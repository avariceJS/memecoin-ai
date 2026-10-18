from __future__ import annotations

import hashlib
import multiprocessing
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json
from tools.brain_senses_gecko_capture_owner_runtime_v3 import (
    OwnerBinding,
    OwnerRuntimeError,
    execute_one_shot,
    owner_preflight_only,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _activated_binding() -> OwnerBinding:
    return OwnerBinding(
        release_id="a" * 32,
        service_root="services/gecko-market-v1/capture-only-v2/" + "a" * 32,
        release_sha256="b" * 64,
        source_bindings={},
        storage={},
        mount_storage={},
        storage_config={},
        policy_sha256="c" * 64,
        certificate_sha256="d" * 64,
        plan_sha256="e" * 64,
        source_manifest_sha256="f" * 64,
        binding_sha256="1" * 64,
        activation_record_sha256="2" * 64,
    )


class _Backend:
    def __init__(self, *, fail_before_network: bool = False) -> None:
        self.events: list[str] = []
        self.terminals: list[dict[str, Any]] = []
        self.fail_before_network = fail_before_network
        self.unresolved_count = 0
        self.recovery_enabled = False

    def acquire_ordered_locks(self, binding: Any) -> str:
        self.events.append("locks")
        return "lease"

    def build_intent(self, binding: Any, slot: Any, request_id: str) -> dict[str, Any]:
        self.events.append("build_intent")
        return {
            "request_id": request_id,
            "request": {
                "url": "https://example.invalid",
                "bucket_close_ns": time.time_ns() - 60_000_000_000,
            },
        }

    def revalidate_storage(self) -> None:
        self.events.append("storage")

    def persist_intent(self, intent: Any, lease: Any) -> str:
        self.events.append("intent")
        return "reservation"

    def persist_dispatch(self, intent: Any, reservation: Any, lease: Any) -> str:
        self.events.append("dispatch")
        return {"dispatch_ns": time.time_ns(), "dispatch_monotonic_ns": time.monotonic_ns()}

    @staticmethod
    def transport_once(request: Any) -> dict[str, Any]:
        return {"status": 200, "body": b"ok"}

    def build_terminal(self, **kwargs: Any) -> dict[str, Any]:
        self.events.append("build_terminal")
        self.terminals.append(kwargs)
        return {"status": "received" if kwargs["transport_status"] == "ok" else "ambiguous"}

    def persist_terminal(
        self, reservation: Any, terminal: Any, lease: Any, *, ambiguous: bool
    ) -> str:
        self.events.append("terminal")
        return "coordinator_terminal"

    def persist_confirmation(
        self, intent: Any, terminal: Any, coordinator_terminal: Any, lease: Any
    ) -> dict[str, Any]:
        self.events.append("confirmation")
        return {"confirmed": True}

    def verify_attempt(self, binding: Any, request_id: str) -> dict[str, Any]:
        self.events.append("replay")
        return {
            "status": "verified_prefix",
            "unresolved_intents": 0,
            "confirmation_count": 1,
        }

    def verify_prefix(self, binding: Any) -> dict[str, Any]:
        self.events.append("verify_prefix")
        return {"status": "verified_prefix", "unresolved_count": self.unresolved_count}

    def recover_confirmations(self, binding: Any) -> dict[str, Any]:
        self.events.append("recover_confirmations")
        if self.recovery_enabled:
            self.unresolved_count = 0
        return {"provider_io": False, "status": "confirmation_reconciled"}

    def release_ordered_locks(self, lease: Any) -> None:
        self.events.append("unlock")


def _sleep_transport(_request: Any) -> dict[str, Any]:
    time.sleep(2)
    return {"status": 200}


class OwnerRuntimeTests(unittest.TestCase):
    def test_one_shot_requires_verified_activation_record_before_locks(self) -> None:
        backend = _Backend()
        inactive = _activated_binding()
        inactive = OwnerBinding(
            **{
                **inactive.__dict__,
                "activation_record_sha256": None,
            }
        )
        with self.assertRaisesRegex(OwnerRuntimeError, "activation_record_required"):
            execute_one_shot(
                slot={"request_id": "3" * 64},
                binding=inactive,
                backend=backend,
                recheck=lambda _stage: None,
            )
        self.assertEqual(backend.events, [])

    def test_inert_preflight_binds_source_and_requires_no_loaded_agent(self) -> None:
        raw = b"source"
        result = owner_preflight_only(
            source_bytes={"tools/runtime.py": raw},
            expected_sources={"tools/runtime.py": _sha(raw)},
            launch_snapshot={"loaded": False, "active_gecko_labels": []},
            expected_label="ai.memecoin.brain.senses.gecko-capture-only-v5",
            expected_arguments=["python", "runtime.py", "--tick"],
        )
        self.assertEqual(result["status"], "verified_inert_preflight")
        self.assertFalse(result["dispatch_authorized"])

    def test_inert_preflight_rejects_changed_source_and_competitor(self) -> None:
        with self.assertRaisesRegex(OwnerRuntimeError, "source_hash"):
            owner_preflight_only(
                source_bytes={"tools/runtime.py": b"changed"},
                expected_sources={"tools/runtime.py": _sha(b"source")},
                launch_snapshot={"loaded": False, "active_gecko_labels": []},
                expected_label="label",
                expected_arguments=["python", "runtime.py"],
            )
        with self.assertRaisesRegex(OwnerRuntimeError, "launch_preflight"):
            owner_preflight_only(
                source_bytes={"tools/runtime.py": b"source"},
                expected_sources={"tools/runtime.py": _sha(b"source")},
                launch_snapshot={"loaded": False, "active_gecko_labels": ["other"]},
                expected_label="label",
                expected_arguments=["python", "runtime.py"],
            )

    def test_release_material_reuses_preflight_v2_without_capture_bundle_files(self) -> None:
        import plistlib

        from tools import brain_senses_gecko_capture_owner_preflight_v2 as preflight_v2
        from tools.brain_senses_gecko_capture_owner_runtime_v3 import (
            _open_relative_directory,
            verify_release_material,
        )

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            mount = base / "mount"
            data_root = mount / "BrainData"
            data_root.mkdir(parents=True, mode=0o700)
            os.chmod(mount, 0o700)
            os.chmod(data_root, 0o700)
            mount_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
            data_fd = os.open(data_root, os.O_RDONLY | os.O_DIRECTORY)
            writer = DescriptorWriter(data_fd)
            try:
                mount_stat = os.fstat(mount_fd)
                root_id = "a" * 32
                volume_uuid = "12345678-1234-1234-1234-123456789abc"
                capture_storage = {
                    "root_id": root_id,
                    "volume_uuid": volume_uuid,
                    "device": os.fstat(data_fd).st_dev,
                    "inode": os.fstat(data_fd).st_ino,
                }
                storage = {
                    "root_id": root_id,
                    "volume_uuid": volume_uuid,
                    "device": mount_stat.st_dev,
                    "inode": mount_stat.st_ino,
                    "data_root": "BrainData",
                }
                service_root = "services/gecko-market-v1/capture-only-v2/" + "b" * 32
                for path in (
                    "services",
                    "services/gecko-market-v1",
                    "services/gecko-market-v1/capture-only-v2",
                    service_root,
                    f"{service_root}/chain",
                ):
                    writer.mkdir(path)
                service_fd = _open_relative_directory(data_fd, service_root)

                repo_root = Path(__file__).resolve().parents[1]
                source_commit = "c" * 40
                manifest = preflight_v2.compile_source_manifest(
                    repo_root=repo_root, source_commit=source_commit
                )
                source_map = {path: row["sha256"] for path, row in manifest["sources"].items()}
                manifest_raw = canonical_json(manifest)
                policy = {"policy_sha256": "d" * 64, "population_sha256": "e" * 64}
                certificate = {"certificate_sha256": "f" * 64}
                plan = {
                    "schema": "brain-senses-gecko-scout-chain-v2-plan",
                    "sequence": 1,
                    "record_sha256": "1" * 64,
                }
                chain_records = [plan]
                binding_core = {
                    "schema": "brain-senses-gecko-scout-chain-v2-binding",
                    "schema_version": 2,
                    "root": service_root,
                    "storage": capture_storage,
                    "policy_sha256": policy["policy_sha256"],
                    "population_sha256": policy["population_sha256"],
                    "cutover_certificate_sha256": certificate["certificate_sha256"],
                    "provider_io": False,
                    "dispatch_authorized": False,
                }
                capture_binding = {
                    **binding_core,
                    "binding_sha256": _sha(canonical_json(binding_core)),
                }
                label = "ai.memecoin.brain.senses.gecko-capture-only-v3"
                watchdog_label = "ai.memecoin.brain.senses.gecko-capture-watchdog-v3"
                program_arguments = ["python", "-B", "runtime.py"]
                watchdog_arguments = ["python", "-B", "watchdog.py"]
                plist_raw = plistlib.dumps(
                    {
                        "Label": label,
                        "ProgramArguments": program_arguments,
                        "StartInterval": 12,
                        "RunAtLoad": True,
                        "KeepAlive": False,
                    },
                    sort_keys=True,
                )
                watchdog_raw = plistlib.dumps(
                    {
                        "Label": watchdog_label,
                        "ProgramArguments": watchdog_arguments,
                        "StartInterval": 15,
                        "RunAtLoad": True,
                        "KeepAlive": {"SuccessfulExit": False},
                    },
                    sort_keys=True,
                )
                clock_record = {"sample": "synthetic"}
                release = preflight_v2.compile_owner_release(
                    manifest=manifest,
                    storage=storage,
                    plan_record=plan,
                    certificate=certificate,
                    binding=capture_binding,
                    policy=policy,
                    chain_tail_sha256=plan["record_sha256"],
                    plist_raw=plist_raw,
                    watchdog_raw=watchdog_raw,
                    clock_record=clock_record,
                    label=label,
                    program_arguments=program_arguments,
                    watchdog_label=watchdog_label,
                    watchdog_program_arguments=watchdog_arguments,
                )
                for name, payload in {
                    "binding.json": canonical_json(capture_binding),
                    "policy.json": canonical_json(policy),
                    "predecessor-cutover.json": canonical_json(certificate),
                    "chain/00000000.json": canonical_json(plan),
                }.items():
                    writer.atomic_write(f"{service_root}/{name}", payload, create_only=True)

                guard = SimpleNamespace(
                    mount_fd=mount_fd,
                    config=SimpleNamespace(
                        root_id=storage["root_id"],
                        volume_uuid=storage["volume_uuid"],
                        data_root="BrainData",
                    ),
                    revalidate=lambda **_kwargs: SimpleNamespace(available_bytes=1 << 40),
                )
                storage_config = {
                    "root_id": storage["root_id"],
                    "volume_uuid": storage["volume_uuid"],
                    "data_root": "BrainData",
                }
                preflight_context = {
                    "repo_root": repo_root,
                    "source_manifest_raw": manifest_raw,
                    "expected_source_manifest_sha256": manifest["manifest_sha256"],
                    "source_commit": source_commit,
                    "release": release,
                    "expected_storage": storage,
                    "mount_fd": mount_fd,
                    "data_root_fd": data_fd,
                    "service_fd": service_fd,
                    "source_reader_fd": service_fd,
                    "writer_root_fd": service_fd,
                    "storage_guard": guard,
                    "plist_raw": plist_raw,
                    "watchdog_raw": watchdog_raw,
                    "launch_snapshot": {"active_gecko_labels": []},
                    "policy": policy,
                    "certificate": certificate,
                    "certificate_inputs": {},
                    "chain_records": chain_records,
                    "plan_record": plan,
                    "capture_inputs": {"binding": capture_binding, "chain_records": chain_records},
                    "inventory_inputs": {
                        "writer": writer,
                        "required_successor_root": service_root,
                        "expected_handoffs": {},
                        "expected_v2_chains": {},
                    },
                    "clock_record": clock_record,
                    "now_ns": time.time_ns(),
                    "monotonic_ns": time.monotonic_ns(),
                    "expected_label": label,
                    "expected_program_arguments": program_arguments,
                }
                from unittest.mock import patch

                from tools import brain_senses_gecko_capture_owner_runtime_v3 as runtime_v3

                def git_result(argv: list[str], **_kwargs: Any) -> SimpleNamespace:
                    return SimpleNamespace(
                        stdout=source_commit + "\n" if argv[1] == "rev-parse" else ""
                    )

                fake_preflight_result = {
                    "status": "verified_inert_preflight",
                    "provider_io": False,
                    "dispatch_authorized": False,
                    "activation_authorized": False,
                    "source_manifest_sha256": manifest["manifest_sha256"],
                    "plan_sha256": _sha(canonical_json(plan)),
                    "certificate_sha256": certificate["certificate_sha256"],
                }
                with (
                    patch.object(
                        preflight_v2,
                        "preflight_owner_host",
                        return_value=fake_preflight_result,
                    ) as run_preflight,
                    patch.object(runtime_v3.subprocess, "run", side_effect=git_result),
                ):
                    verified = verify_release_material(
                        release_raw=canonical_json(release),
                        source_manifest_raw=manifest_raw,
                        repo_root=repo_root,
                        expected_source_manifest_sha256=manifest["manifest_sha256"],
                        expected_source_commit=source_commit,
                        plist_raw=plist_raw,
                        watchdog_raw=watchdog_raw,
                        clock_record=clock_record,
                        capture_binding=capture_binding,
                        policy=policy,
                        certificate=certificate,
                        chain_records=chain_records,
                        expected_sources=source_map,
                        storage=storage,
                        storage_config=storage_config,
                        mount_fd=mount_fd,
                        data_root_fd=data_fd,
                        service_fd=service_fd,
                        storage_guard=guard,
                        revalidate_storage=lambda: None,
                        preflight_context=preflight_context,
                    )
                    self.assertEqual(run_preflight.call_count, 1)
                self.assertEqual(verified.service_root, service_root)
                self.assertEqual(verified.release_sha256, release["release_sha256"])
                self.assertEqual(
                    set(writer.list_names(service_root)),
                    {"binding.json", "policy.json", "predecessor-cutover.json", "chain"},
                )
                with (
                    patch.object(runtime_v3.subprocess, "run", side_effect=git_result),
                    self.assertRaisesRegex(OwnerRuntimeError, "source_manifest_replay"),
                ):
                    verify_release_material(
                        release_raw=canonical_json(release),
                        source_manifest_raw=manifest_raw,
                        repo_root=repo_root,
                        expected_source_manifest_sha256="0" * 64,
                        expected_source_commit=source_commit,
                        plist_raw=plist_raw,
                        watchdog_raw=watchdog_raw,
                        clock_record=clock_record,
                        capture_binding=capture_binding,
                        policy=policy,
                        certificate=certificate,
                        chain_records=chain_records,
                        expected_sources=source_map,
                        storage=storage,
                        storage_config=storage_config,
                        mount_fd=mount_fd,
                        data_root_fd=data_fd,
                        service_fd=service_fd,
                        storage_guard=guard,
                        revalidate_storage=lambda: None,
                        preflight_context=preflight_context,
                    )
            finally:
                writer.close()
                os.close(service_fd)
                os.close(data_fd)
                os.close(mount_fd)

    def test_release_adapter_runs_real_capture_and_inventory_preflight(self) -> None:
        import plistlib
        import sys

        repo = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(repo / "tests"))
        import test_brain_senses_gecko_capture_only_v2_verify as clock_fixture
        import test_brain_senses_gecko_scout_chain_v2 as chain_fixture

        from tools import brain_senses_capacity_coordinator_v2 as capacity
        from tools import brain_senses_gecko_capture_owner_preflight_v2 as preflight_v2
        from tools.brain_senses_external_writer_v1 import DescriptorWriter
        from tools.brain_senses_gecko_capture_owner_runtime_v3 import verify_release_material
        from tools.brain_senses_gecko_rotating_scout_v1 import DISPATCH_EARLIEST_NS
        from tools.brain_senses_gecko_scout_chain_v2 import DescriptorScoutJournalV2

        with tempfile.TemporaryDirectory() as temp:
            mount = Path(temp) / "mount"
            data_root = mount / "BrainData"
            data_root.mkdir(parents=True, mode=0o700)
            os.chmod(mount, 0o700)
            os.chmod(data_root, 0o700)
            mount_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
            data_fd = os.open(data_root, os.O_RDONLY | os.O_DIRECTORY)
            writer = DescriptorWriter(data_fd)
            service_fd = -1
            try:
                inputs = chain_fixture._certificate_inputs(writer)
                policy = chain_fixture._policy()
                certificate = chain_fixture.chain_v2.build_cutover_certificate(**inputs)
                journal = DescriptorScoutJournalV2(
                    writer,
                    root=chain_fixture.ROOT,
                    policy=policy,
                    certificate=certificate,
                    certificate_inputs=inputs,
                )
                journal.initialize()
                close_ns = journal.records()[-1]["next_bucket_close_ns"] + 900_000_000_000
                now_ns = close_ns + DISPATCH_EARLIEST_NS + 1_000_000_000
                journal.ensure_plan_for_current_bucket(now_ns=now_ns, bucket_close_ns=close_ns)
                records = journal.records()
                plan = records[-1]
                binding = journal.binding
                capture_storage = dict(binding["storage"])
                mount_stat = os.fstat(mount_fd)
                release_storage = {
                    "root_id": capture_storage["root_id"],
                    "volume_uuid": capture_storage["volume_uuid"],
                    "device": mount_stat.st_dev,
                    "inode": mount_stat.st_ino,
                    "data_root": "BrainData",
                }
                service_fd = os.open(
                    chain_fixture.ROOT,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=data_fd,
                )

                # The real v4 inventory reads and replays this descriptor-bound
                # coordinator genesis and the actual v3 capture prefix below.
                for directory in ("services/senses-capacity-v2",):
                    writer.mkdir(directory)
                coordinator_policy = capacity.policy_document(
                    capture_storage,
                    service_lock_paths={"coordinator": capacity.LOCK_PATH},
                    allocation_roots={"coordinator": ()},
                )
                policy_raw = capacity.canonical(coordinator_policy)
                writer.create(capacity.POLICY_PATH, policy_raw)
                writer.create(capacity.LOCK_PATH, b"lock\n")
                genesis = capacity._event(
                    seq=0,
                    kind="genesis",
                    at_ns=now_ns,
                    previous=None,
                    fields={"policy_sha256": capacity.sha256(policy_raw)},
                )
                writer.create(capacity.LEDGER_PATH, capacity.canonical(genesis))
                coordinator_events = [genesis]

                source_commit = "c" * 40
                manifest = preflight_v2.compile_source_manifest(repo, source_commit=source_commit)
                manifest_raw = canonical_json(manifest)
                source_map = {path: row["sha256"] for path, row in manifest["sources"].items()}
                label = "ai.memecoin.brain.senses.gecko-capture-only-v5"
                watchdog_label = "ai.memecoin.brain.senses.gecko-capture-watchdog-v3"
                program_arguments = ["/python", "-B", "/repo/gecko.py", "--scheduled-tick"]
                watchdog_arguments = ["/python", "-B", "/repo/watchdog.py", "--check"]
                plist_raw = plistlib.dumps(
                    {
                        "Label": label,
                        "ProgramArguments": program_arguments,
                        "StartInterval": 12,
                        "RunAtLoad": True,
                        "KeepAlive": False,
                        "ProcessType": "Background",
                        "StandardOutPath": "/dev/null",
                        "StandardErrorPath": "/dev/null",
                    },
                    sort_keys=True,
                )
                watchdog_raw = plistlib.dumps(
                    {
                        "Label": watchdog_label,
                        "ProgramArguments": watchdog_arguments,
                        "StartInterval": 15,
                        "RunAtLoad": True,
                        "KeepAlive": {"SuccessfulExit": False},
                    },
                    sort_keys=True,
                )
                clock_record, _ = clock_fixture._clock(now_ns, 500_000_000_000)
                release = preflight_v2.compile_owner_release(
                    manifest=manifest,
                    storage=release_storage,
                    plan_record=plan,
                    certificate=certificate,
                    binding=binding,
                    policy=policy,
                    chain_tail_sha256=plan["record_sha256"],
                    plist_raw=plist_raw,
                    watchdog_raw=watchdog_raw,
                    clock_record=clock_record,
                    label=label,
                    program_arguments=program_arguments,
                    watchdog_label=watchdog_label,
                    watchdog_program_arguments=watchdog_arguments,
                )
                guard = SimpleNamespace(
                    mount_fd=mount_fd,
                    config=SimpleNamespace(
                        root_id=capture_storage["root_id"],
                        volume_uuid=capture_storage["volume_uuid"],
                        data_root="BrainData",
                    ),
                    revalidate=lambda **_kwargs: SimpleNamespace(
                        available_bytes=60 * 1024**3,
                        mount_device=os.fstat(mount_fd).st_dev,
                        mount_inode=os.fstat(mount_fd).st_ino,
                    ),
                )
                certificate_inputs = dict(inputs)
                capture_inputs = {
                    "root": chain_fixture.ROOT,
                    "binding": binding,
                    "policy": policy,
                    "chain_records": records,
                    "intents": [],
                    "dispatch_records": [],
                    "terminals": [],
                    "raw_responses": {},
                    "confirmations": [],
                    "coordinator_events": coordinator_events,
                    "skips": [],
                    "certificate": certificate,
                    "certificate_inputs": certificate_inputs,
                }
                preflight_context = {
                    "repo_root": repo,
                    "source_manifest_raw": manifest_raw,
                    "expected_source_manifest_sha256": manifest["manifest_sha256"],
                    "source_commit": source_commit,
                    "release": release,
                    "expected_storage": release_storage,
                    "mount_fd": mount_fd,
                    "data_root_fd": data_fd,
                    "service_fd": service_fd,
                    "source_reader_fd": service_fd,
                    "writer_root_fd": service_fd,
                    "storage_guard": guard,
                    "plist_raw": plist_raw,
                    "watchdog_raw": watchdog_raw,
                    "launch_snapshot": {"active_gecko_labels": []},
                    "policy": policy,
                    "certificate": certificate,
                    "certificate_inputs": certificate_inputs,
                    "chain_records": records,
                    "plan_record": plan,
                    "capture_inputs": capture_inputs,
                    "inventory_inputs": {
                        "writer": writer,
                        "required_successor_root": chain_fixture.ROOT,
                        "expected_handoffs": {},
                        "expected_v2_chains": {
                            chain_fixture.ROOT: {
                                "certificate_inputs": certificate_inputs,
                                "observed_now_ns": now_ns,
                            }
                        },
                    },
                    "clock_record": clock_record,
                    "now_ns": now_ns,
                    "monotonic_ns": 500_000_000_000,
                    "expected_label": label,
                    "expected_program_arguments": program_arguments,
                }
                from unittest.mock import patch

                from tools import brain_senses_gecko_capture_owner_runtime_v3 as runtime_v3

                def git_result(argv: list[str], **_kwargs: Any) -> SimpleNamespace:
                    return SimpleNamespace(
                        stdout=source_commit + "\n" if argv[1] == "rev-parse" else ""
                    )

                with patch.object(runtime_v3.subprocess, "run", side_effect=git_result):
                    result = verify_release_material(
                        release_raw=canonical_json(release),
                        source_manifest_raw=manifest_raw,
                        repo_root=repo,
                        expected_source_manifest_sha256=manifest["manifest_sha256"],
                        expected_source_commit=source_commit,
                        plist_raw=plist_raw,
                        watchdog_raw=watchdog_raw,
                        clock_record=clock_record,
                        capture_binding=binding,
                        policy=policy,
                        certificate=certificate,
                        chain_records=records,
                        expected_sources=source_map,
                        storage=release_storage,
                        storage_config={
                            "root_id": capture_storage["root_id"],
                            "volume_uuid": capture_storage["volume_uuid"],
                            "data_root": "BrainData",
                        },
                        mount_fd=mount_fd,
                        data_root_fd=data_fd,
                        service_fd=service_fd,
                        storage_guard=guard,
                        revalidate_storage=lambda: None,
                        preflight_context=preflight_context,
                    )
                self.assertEqual(result.service_root, chain_fixture.ROOT)
                self.assertFalse(release["dispatch_authorized"])
            finally:
                writer.close()
                if service_fd >= 0:
                    os.close(service_fd)
                os.close(data_fd)
                os.close(mount_fd)

    def test_one_shot_writes_before_network_and_has_no_retry(self) -> None:
        backend = _Backend()
        checks: list[str] = []
        result = execute_one_shot(
            slot={"request_id": "c" * 64},
            binding=_activated_binding(),
            backend=backend,
            recheck=checks.append,
        )
        self.assertEqual(
            checks, ["before_reservation", "after_lock_before_reservation", "before_network"]
        )
        self.assertLess(backend.events.index("intent"), backend.events.index("dispatch"))
        self.assertLess(backend.events.index("dispatch"), backend.events.index("terminal"))
        self.assertEqual(backend.events.count("intent"), 1)
        self.assertTrue(result["provider_io_possible"])

    def test_pre_network_gate_failure_is_spent_and_never_replays(self) -> None:
        backend = _Backend()
        calls = 0

        def reject(stage: str) -> None:
            nonlocal calls
            if stage == "before_network":
                calls += 1
                raise RuntimeError("host secret should not be serialized")

        with self.assertRaisesRegex(OwnerRuntimeError, "post_dispatch_gate_failed"):
            execute_one_shot(
                slot={"request_id": "d" * 64},
                binding=_activated_binding(),
                backend=backend,
                recheck=reject,
            )
        self.assertEqual(calls, 1)
        self.assertEqual(backend.events.count("intent"), 1)
        self.assertEqual(backend.events.count("dispatch"), 1)
        self.assertNotIn("fetch", backend.events)
        self.assertNotIn("terminal", backend.events)
        self.assertNotIn("confirmation", backend.events)

    def test_child_drains_large_result_before_join(self) -> None:
        from tools.brain_senses_gecko_capture_owner_runtime_v3 import run_bounded_transport

        status, value = run_bounded_transport(lambda: b"x" * 256_000, timeout_seconds=3)
        self.assertEqual(status, "ok")
        self.assertEqual(len(value), 256_000)

    def test_timed_child_returns_request_and_receipt_wall_monotonic_pair(self) -> None:
        from tools.brain_senses_gecko_capture_owner_runtime_v3 import run_bounded_transport

        status, value = run_bounded_transport(
            lambda: {"status": 200, "body": b"ok"}, timeout_seconds=3, timed=True
        )
        self.assertEqual(status, "ok")
        self.assertEqual(value["payload"]["status"], 200)
        self.assertGreaterEqual(value["receipt_ns"], value["request_started_ns"])
        wall_delta = value["receipt_ns"] - value["request_started_ns"]
        mono_delta = value["receipt_monotonic_ns"] - value["request_started_monotonic_ns"]
        self.assertLessEqual(abs(wall_delta - mono_delta), 2_000_000_000)
        self.assertLessEqual(mono_delta, 3_000_000_000)

    def test_restart_prefix_unresolved_blocks_before_any_new_intent_or_transport(self) -> None:
        backend = _Backend()
        backend.unresolved_count = 1  # models an intent-only or dispatch-only prior attempt
        with self.assertRaisesRegex(OwnerRuntimeError, "prefix_unresolved"):
            execute_one_shot(
                slot={"request_id": "7" * 64},
                binding=_activated_binding(),
                backend=backend,
                recheck=lambda _stage: None,
            )
        self.assertEqual(backend.events.count("recover_confirmations"), 1)
        self.assertEqual(backend.events.count("intent"), 0)
        self.assertEqual(backend.events.count("dispatch"), 0)
        self.assertEqual(backend.events.count("terminal"), 0)

    def test_confirmation_recovery_is_read_only_and_can_close_unresolved_prefix(self) -> None:
        from tools.brain_senses_gecko_capture_owner_runtime_v3 import (
            reconcile_prefix_before_dispatch,
        )

        backend = _Backend()
        backend.unresolved_count = (
            1  # terminal and coordinator settle exist; confirmation is absent
        )
        backend.recovery_enabled = True
        result = reconcile_prefix_before_dispatch(binding=object(), backend=backend)
        self.assertEqual(result["unresolved_count"], 0)
        self.assertEqual(backend.events.count("recover_confirmations"), 1)
        self.assertNotIn("intent", backend.events)
        self.assertNotIn("dispatch", backend.events)
        self.assertNotIn("build_terminal", backend.events)

    def test_child_timeout_is_spent_and_fork_attempt_is_rejected(self) -> None:
        from tools.brain_senses_gecko_capture_owner_runtime_v3 import run_bounded_transport

        children_before = {child.pid for child in multiprocessing.active_children()}
        status, value = run_bounded_transport(time.sleep, 2, timeout_seconds=1)
        self.assertEqual((status, value), ("transport_uncertain_possibly_spent", None))
        self.assertEqual(
            {child.pid for child in multiprocessing.active_children()}, children_before
        )
        status, value = run_bounded_transport(os.setsid, timeout_seconds=3)
        self.assertEqual((status, value), ("error", "PermissionError"))

    def test_one_shot_timeout_is_spent_once_without_retry(self) -> None:
        backend = _Backend()
        backend.transport_once = _sleep_transport
        result = execute_one_shot(
            slot={"request_id": "e" * 64},
            binding=_activated_binding(),
            backend=backend,
            recheck=lambda _stage: None,
            timeout_seconds=1,
        )
        self.assertEqual(backend.events.count("intent"), 1)
        self.assertEqual(backend.events.count("dispatch"), 1)
        self.assertEqual(backend.events.count("terminal"), 1)
        self.assertTrue(result["provider_io_possible"])

    def test_storage_detach_after_dispatch_blocks_network_and_terminal_write(self) -> None:
        class DetachBackend(_Backend):
            def __init__(self) -> None:
                super().__init__()
                self.storage_checks = 0

            def revalidate_storage(self) -> None:
                self.storage_checks += 1
                self.events.append("storage")
                if self.storage_checks >= 3:
                    raise RuntimeError("detached")

        backend = DetachBackend()
        with self.assertRaisesRegex(OwnerRuntimeError, "post_dispatch_gate_failed"):
            execute_one_shot(
                slot={"request_id": "f" * 64},
                binding=_activated_binding(),
                backend=backend,
                recheck=lambda _stage: None,
            )
        self.assertEqual(backend.events.count("intent"), 1)
        self.assertEqual(backend.events.count("dispatch"), 1)
        self.assertNotIn("terminal", backend.events)
        self.assertNotIn("confirmation", backend.events)

    def test_post_lock_detach_blocks_reservation_and_releases_lane(self) -> None:
        class DetachBeforeReserveBackend(_Backend):
            def revalidate_storage(self) -> None:
                self.events.append("storage")
                raise RuntimeError("detached after lock")

        backend = DetachBeforeReserveBackend()
        with self.assertRaisesRegex(RuntimeError, "detached"):
            execute_one_shot(
                slot={"request_id": "9" * 64},
                binding=_activated_binding(),
                backend=backend,
                recheck=lambda _stage: None,
            )
        self.assertIn("locks", backend.events)
        self.assertEqual(backend.events.count("intent"), 0)
        self.assertEqual(backend.events.count("dispatch"), 0)
        self.assertNotIn("terminal", backend.events)
        self.assertNotIn("confirmation", backend.events)
        self.assertEqual(backend.events[-1], "unlock")

    def test_bounded_transport_ambiguous_result_is_confirmed_once(self) -> None:
        backend = _Backend()
        backend.transport_once = _sleep_transport
        result = execute_one_shot(
            slot={"request_id": "a" * 64},
            binding=_activated_binding(),
            backend=backend,
            recheck=lambda _stage: None,
            timeout_seconds=1,
        )
        self.assertEqual(result["status"], "ambiguous")
        self.assertTrue(result["provider_io_possible"])
        self.assertEqual(backend.events.count("intent"), 1)
        self.assertEqual(backend.events.count("dispatch"), 1)
        self.assertEqual(backend.events.count("terminal"), 1)
        self.assertEqual(backend.events.count("confirmation"), 1)
        self.assertEqual(backend.events.count("replay"), 1)

    def test_over_limit_response_is_spent_ambiguous_without_retaining_body(self) -> None:
        backend = _Backend()
        backend.transport_once = lambda _request: {"status": 200, "body": b"x" * 16_385}
        result = execute_one_shot(
            slot={"request_id": "8" * 64},
            binding=_activated_binding(),
            backend=backend,
            recheck=lambda _stage: None,
        )
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(backend.events.count("intent"), 1)
        self.assertEqual(backend.events.count("dispatch"), 1)
        self.assertEqual(backend.events.count("terminal"), 1)
        self.assertEqual(
            backend.terminals[0]["transport_status"], "transport_uncertain_possibly_spent"
        )
        self.assertIsNone(backend.terminals[0]["response"])


if __name__ == "__main__":
    unittest.main()
