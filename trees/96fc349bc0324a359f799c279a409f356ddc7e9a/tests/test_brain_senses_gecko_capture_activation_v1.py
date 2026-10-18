from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json
from tools.brain_senses_gecko_capture_activation_v1 import (
    ActivationError,
    bind_verified_activation,
    create_activation_record,
    prepare_activation_record,
    read_activation_record,
    validate_activation_record,
    verify_activation_lineage,
)
from tools.brain_senses_gecko_capture_owner_runtime_v3 import OwnerBinding


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class ActivationRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "tests@example.invalid"], cwd=self.repo, check=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Activation Tests"], cwd=self.repo, check=True
        )
        self.source_path = self.repo / "src" / "module.py"
        self.source_path.parent.mkdir()
        self.source_bytes = b"VALUE = 3\n"
        self.source_path.write_bytes(self.source_bytes)
        self.activation_id = "b" * 32
        self.collector_label = "ai.memecoin.brain.senses.gecko-capture-v5-test"
        self.watchdog_label = "ai.memecoin.brain.senses.gecko-capture-watchdog-v5-test"
        self.collector_args = [
            "/usr/bin/python3",
            "collector.py",
            "--activation-id",
            self.activation_id,
        ]
        self.watchdog_args = [
            "/usr/bin/python3",
            "watchdog.py",
            "--activation-id",
            self.activation_id,
        ]
        self.collector_plist = plistlib_dumps(self.collector_label, self.collector_args)
        self.watchdog_plist = plistlib_dumps(self.watchdog_label, self.watchdog_args)
        (self.repo / "ops").mkdir()
        (self.repo / "ops/collector.plist").write_bytes(self.collector_plist)
        (self.repo / "ops/watchdog.plist").write_bytes(self.watchdog_plist)
        subprocess.run(
            ["git", "add", "src/module.py", "ops/collector.plist", "ops/watchdog.plist"],
            cwd=self.repo,
            check=True,
        )
        subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=self.repo, check=True)
        self.commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        self.mount = self.root / "mount"
        self.data_root = self.mount / "BrainData"
        self.data_root.mkdir(parents=True, mode=0o700)
        os.chmod(self.mount, 0o700)
        os.chmod(self.data_root, 0o700)
        control = self.data_root / "control"
        control.mkdir(mode=0o700)
        os.chmod(control, 0o700)
        activation_dir = control / "gecko-capture-activation-v1"
        activation_dir.mkdir(mode=0o700)
        os.chmod(activation_dir, 0o700)
        self.mount_fd = os.open(self.mount, os.O_RDONLY | os.O_DIRECTORY)
        self.data_fd = os.open(self.data_root, os.O_RDONLY | os.O_DIRECTORY)
        self.writer = DescriptorWriter(self.data_fd)
        data_stat = os.fstat(self.data_fd)
        self.storage = {
            "root_id": "a" * 32,
            "volume_uuid": str(uuid.uuid4()),
            "device": data_stat.st_dev,
            "inode": data_stat.st_ino,
            "data_root": "BrainData",
        }
        self.storage_guard = SimpleNamespace(
            mount_fd=self.mount_fd,
            config=SimpleNamespace(
                root_id=self.storage["root_id"],
                volume_uuid=self.storage["volume_uuid"],
                data_root=self.storage["data_root"],
            ),
        )
        self.activation_id = "b" * 32
        self.binding = {
            "schema": "brain-senses-gecko-scout-chain-v2-binding",
            "schema_version": 2,
            "root": "services/gecko-market-v1/capture-only-v2/" + "c" * 32,
            "storage": {
                "root_id": self.storage["root_id"],
                "volume_uuid": self.storage["volume_uuid"],
                "device": data_stat.st_dev,
                "inode": data_stat.st_ino,
            },
            "policy_sha256": "d" * 64,
            "population_sha256": "e" * 64,
            "cutover_certificate_sha256": "f" * 64,
        }
        source_map = {
            "ops/collector.plist": _sha(self.collector_plist),
            "ops/watchdog.plist": _sha(self.watchdog_plist),
            "src/module.py": _sha(self.source_bytes),
        }
        manifest_core = {
            "schema": "brain-senses-gecko-capture-source-manifest-v2",
            "schema_version": 2,
            "source_commit": self.commit,
            "source_paths": sorted(source_map),
            "sources": {
                "ops/collector.plist": {
                    "bytes": len(self.collector_plist),
                    "sha256": _sha(self.collector_plist),
                },
                "ops/watchdog.plist": {
                    "bytes": len(self.watchdog_plist),
                    "sha256": _sha(self.watchdog_plist),
                },
                "src/module.py": {
                    "bytes": len(self.source_bytes),
                    "sha256": _sha(self.source_bytes),
                },
            },
        }
        self.manifest = {
            **manifest_core,
            "manifest_sha256": _sha(canonical_json(manifest_core)),
        }
        self.manifest_raw = canonical_json(self.manifest)
        self.manifest_sha = _sha(self.manifest_raw)
        self.source_map = source_map
        self.revalidate_calls = 0

    def tearDown(self) -> None:
        self.writer.close()
        os.close(self.data_fd)
        os.close(self.mount_fd)
        self.temp.cleanup()

    def _record(self) -> dict[str, Any]:
        self.revalidate_calls = 0

        def revalidate() -> None:
            self.revalidate_calls += 1

        return prepare_activation_record(
            activation_id=self.activation_id,
            phase="continuous",
            source_commit=self.commit,
            source_manifest_raw=self.manifest_raw,
            source_manifest_sha256=self.manifest_sha,
            source_map=self.source_map,
            repo_root=self.repo,
            service_root=self.binding["root"],
            binding=self.binding,
            certificate_sha256="f" * 64,
            policy_sha256="d" * 64,
            population_sha256="e" * 64,
            plan_sha256="1" * 64,
            storage=self.storage,
            data_root_fd=self.data_fd,
            writer=self.writer,
            storage_guard=self.storage_guard,
            collector_plist_raw=self.collector_plist,
            collector_plist_path="ops/collector.plist",
            collector_loaded_path="/tmp/LaunchAgents/collector.plist",
            collector_label=self.collector_label,
            collector_arguments=self.collector_args,
            watchdog_plist_raw=self.watchdog_plist,
            watchdog_plist_path="ops/watchdog.plist",
            watchdog_loaded_path="/tmp/LaunchAgents/watchdog.plist",
            watchdog_label=self.watchdog_label,
            watchdog_arguments=self.watchdog_args,
            predecessor_certificate_sha256="2" * 64,
            supervisor_certificate_sha256="3" * 64,
            revalidate_storage=revalidate,
        )

    def _loaded(self, record: dict[str, Any]) -> dict[str, Any]:
        return {
            "loaded_services": [
                {
                    "label": record["collector"]["label"],
                    "plist_path": record["collector"]["loaded_path"],
                    "program_arguments": record["collector"]["program_arguments"],
                    "uid": os.getuid(),
                },
                {
                    "label": record["watchdog"]["label"],
                    "plist_path": record["watchdog"]["loaded_path"],
                    "program_arguments": record["watchdog"]["program_arguments"],
                    "uid": os.getuid(),
                },
            ]
        }

    def _validate(
        self, raw: bytes, record: dict[str, Any], *, snapshot: dict[str, Any] | None = None
    ):
        return validate_activation_record(
            record_raw=raw,
            expected_activation_id=self.activation_id,
            source_manifest_raw=self.manifest_raw,
            repo_root=self.repo,
            data_root_fd=self.data_fd,
            writer=self.writer,
            storage_guard=self.storage_guard,
            revalidate_storage=lambda: None,
            collector_plist_raw=self.collector_plist,
            watchdog_plist_raw=self.watchdog_plist,
            launch_snapshot=snapshot or self._loaded(record),
        )

    def test_create_only_record_and_restart_gate(self) -> None:
        record = self._record()
        digest = create_activation_record(
            writer=self.writer,
            record=record,
            data_root_fd=self.data_fd,
            revalidate_storage=lambda: None,
        )
        raw = self.writer.read_bytes(record["activation_path"], max_bytes=128 * 1024)
        self.assertEqual(digest, _sha(raw))
        verified = self._validate(raw, record)
        self.assertEqual(verified.activation_id, self.activation_id)
        verify_activation_lineage(
            verified,
            binding=self.binding,
            certificate_sha256="f" * 64,
            policy_sha256="d" * 64,
            population_sha256="e" * 64,
            plan_sha256="1" * 64,
        )
        data_storage = dict(self.binding["storage"])
        owner_binding = OwnerBinding(
            release_id=self.binding["root"].rsplit("/", 1)[-1],
            service_root=self.binding["root"],
            release_sha256="4" * 64,
            source_bindings=self.source_map,
            storage=data_storage,
            mount_storage=self.storage,
            storage_config={"data_root": self.storage["data_root"]},
            policy_sha256="d" * 64,
            certificate_sha256="f" * 64,
            plan_sha256="1" * 64,
            source_manifest_sha256=self.manifest_sha,
            binding_sha256=_sha(canonical_json(self.binding)),
        )
        activated_binding = bind_verified_activation(verified, owner_binding)
        self.assertEqual(activated_binding.activation_record_sha256, digest)
        with self.assertRaisesRegex(ActivationError, "collision"):
            create_activation_record(
                writer=self.writer,
                record=record,
                data_root_fd=self.data_fd,
                revalidate_storage=lambda: None,
            )

    def test_gate_rejects_absence_replacement_plist_and_loaded_identity(self) -> None:
        record = self._record()
        raw = canonical_json(record)
        with self.assertRaisesRegex(ActivationError, "loaded_services"):
            self._validate(raw, record, snapshot={"loaded_services": []})
        changed = dict(record)
        changed["plan_sha256"] = "9" * 64
        changed_activation = self._validate(canonical_json(changed), changed)
        with self.assertRaisesRegex(ActivationError, "fresh_lineage"):
            verify_activation_lineage(
                changed_activation,
                binding=self.binding,
                certificate_sha256="f" * 64,
                policy_sha256="d" * 64,
                population_sha256="e" * 64,
                plan_sha256="1" * 64,
            )
        original = self.collector_plist
        self.collector_plist = b"changed plist"
        with self.assertRaisesRegex(ActivationError, "plist_hash"):
            self._validate(raw, record)
        self.collector_plist = original
        wrong = self._loaded(record)
        wrong["loaded_services"][0]["label"] = "ai.memecoin.brain.senses.other"
        with self.assertRaisesRegex(ActivationError, "loaded_services"):
            self._validate(raw, record, snapshot=wrong)

    def test_missing_record_and_wrong_recorded_commit_fail_before_lineage(self) -> None:
        with self.assertRaisesRegex(ActivationError, "unavailable"):
            read_activation_record(self.writer, self.activation_id)
        record = self._record()
        record["source_commit"] = "0" * 40
        with self.assertRaisesRegex(ActivationError, "source_commit_mismatch"):
            self._validate(canonical_json(record), record)

    def test_source_commit_and_manifest_are_exact_and_clean(self) -> None:
        record = self._record()
        self.source_path.write_bytes(b"VALUE = 4\n")
        with self.assertRaisesRegex(ActivationError, "source_tree_dirty|source_blob"):
            self._validate(canonical_json(record), record)

    def test_wrong_activation_argv_or_phase_rejected_before_record(self) -> None:
        self.collector_args = ["/usr/bin/python3", "collector.py", "--activation-id", "0" * 32]
        self.collector_plist = plistlib_dumps(self.collector_label, self.collector_args)
        with self.assertRaisesRegex(ActivationError, "argv_id|plist_source_pin"):
            self._record()
        with self.assertRaisesRegex(ActivationError, "phase"):
            prepare_activation_record(
                activation_id=self.activation_id,
                phase="pilot",
                source_commit=self.commit,
                source_manifest_raw=self.manifest_raw,
                source_manifest_sha256=self.manifest_sha,
                source_map=self.source_map,
                repo_root=self.repo,
                service_root=self.binding["root"],
                binding=self.binding,
                certificate_sha256="f" * 64,
                policy_sha256="d" * 64,
                population_sha256="e" * 64,
                plan_sha256="1" * 64,
                storage=self.storage,
                data_root_fd=self.data_fd,
                writer=self.writer,
                storage_guard=self.storage_guard,
                collector_plist_raw=self.collector_plist,
                collector_plist_path="ops/collector.plist",
                collector_loaded_path="/tmp/LaunchAgents/collector.plist",
                collector_label=self.collector_label,
                collector_arguments=self.collector_args,
                watchdog_plist_raw=self.watchdog_plist,
                watchdog_plist_path="ops/watchdog.plist",
                watchdog_loaded_path="/tmp/LaunchAgents/watchdog.plist",
                watchdog_label=self.watchdog_label,
                watchdog_arguments=self.watchdog_args,
                predecessor_certificate_sha256=None,
                supervisor_certificate_sha256=None,
                revalidate_storage=lambda: None,
            )

    def _assert_activation_detach(self, target_operation: str) -> None:
        record = self._record()
        original_root = self.root / f"detached-{target_operation}"
        underlay_root = self.data_root
        detached = False
        fired = False

        def revalidate() -> None:
            expected = os.fstat(self.data_fd)
            observed = underlay_root.lstat()
            if (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino):
                raise RuntimeError("descriptor_detached")

        def fault_hook(operation: str) -> None:
            nonlocal detached, fired
            if operation == target_operation and not fired:
                fired = True
                os.rename(underlay_root, original_root)
                underlay_root.mkdir(mode=0o700)
                detached = True

        fault_writer = DescriptorWriter(
            self.data_fd, before_write=revalidate, fault_hook=fault_hook
        )
        try:
            with self.assertRaises((ActivationError, RuntimeError)):
                create_activation_record(
                    writer=fault_writer,
                    record=record,
                    data_root_fd=self.data_fd,
                    revalidate_storage=revalidate,
                )
            self.assertTrue(fired)
            self.assertTrue(detached)
            self.assertEqual(list(underlay_root.iterdir()), [])
            self.assertFalse((underlay_root / record["activation_path"]).exists())
            if target_operation == "atomic_create":
                self.assertFalse((original_root / record["activation_path"]).exists())
            else:
                # Rename can finish on the held old descriptor after detach; the
                # file remains in the detached root and never appears in underlay.
                self.assertTrue((original_root / record["activation_path"]).exists())
        finally:
            fault_writer.close()

    def test_activation_detach_at_atomic_create_never_writes_underlay(self) -> None:
        self._assert_activation_detach("atomic_create")

    def test_activation_detach_at_atomic_rename_never_writes_underlay(self) -> None:
        self._assert_activation_detach("atomic_rename")

    def _assert_activation_crash_prefix(self, target_operation: str) -> None:
        record = self._record()
        fired = False

        def fault_hook(operation: str) -> None:
            nonlocal fired
            if operation == target_operation and not fired:
                fired = True
                raise RuntimeError("injected_crash_boundary")

        fault_writer = DescriptorWriter(self.data_fd, fault_hook=fault_hook)
        try:
            with self.assertRaises(ActivationError):
                create_activation_record(
                    writer=fault_writer,
                    record=record,
                    data_root_fd=self.data_fd,
                    revalidate_storage=lambda: None,
                )
            self.assertTrue(fired)
            names = self.writer.list_names("control/gecko-capture-activation-v1")
            self.assertNotIn(f"{self.activation_id}.json", names)
            self.assertFalse(any(name.endswith(".tmp") for name in names))
        finally:
            fault_writer.close()

    def test_activation_crash_at_atomic_create_leaves_no_file(self) -> None:
        self._assert_activation_crash_prefix("atomic_create")

    def test_activation_crash_at_atomic_rename_leaves_no_file_or_temp(self) -> None:
        self._assert_activation_crash_prefix("atomic_rename")


def plistlib_dumps(label: str, arguments: list[str]) -> bytes:
    import plistlib

    return plistlib.dumps({"Label": label, "ProgramArguments": arguments}, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
