from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
for entry in (REPO / "src", REPO / "tools", REPO / "tests"):
    sys.path.insert(0, str(entry))

import test_brain_senses_gecko_capture_only_v2_verify as clock_fixture  # noqa: E402

from memecoin_research_storage import StorageError  # noqa: E402
from tools import brain_senses_gecko_capture_owner_host_v2 as owner_host  # noqa: E402
from tools import brain_senses_gecko_capture_owner_preflight_v2 as preflight  # noqa: E402
from tools import brain_senses_gecko_capture_watchdog_v3 as watchdog  # noqa: E402
from tools import brain_senses_gecko_scout_chain_v2 as chain  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json  # noqa: E402


def _source_tree_copy(destination: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    for relative in preflight.SOURCE_PATHS:
        source = root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        os.chmod(target, 0o600)
    return destination


def test_source_manifest_is_deterministic_and_detects_drift(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = preflight.compile_source_manifest(root, source_commit="a" * 40)
    assert manifest["source_paths"] == list(preflight.SOURCE_PATHS)
    assert manifest["manifest_sha256"] == preflight.sha256(
        preflight.canonical_json(
            {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        )
    )
    copied = _source_tree_copy(tmp_path / "repo")
    target = copied / preflight.SOURCE_PATHS[0]
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(preflight.PreflightError, match="source_drift"):
        preflight.verify_source_manifest(
            preflight.canonical_json(manifest),
            repo_root=copied,
            expected_manifest_sha256=manifest["manifest_sha256"],
            expected_source_commit="a" * 40,
        )


def test_watchdog_source_and_checked_in_plist_are_pinned_but_host_is_fail_closed() -> None:
    repo = Path(__file__).resolve().parents[1]
    manifest = preflight.compile_source_manifest(repo, source_commit="a" * 40)
    assert manifest["sources"]["tools/brain_senses_gecko_capture_watchdog_v3.py"]
    assert manifest["sources"]["tools/brain_senses_gecko_capture_handoff_supervisor_v1.py"]
    raw = preflight._read_repo_file(repo, owner_host.WATCHDOG_PLIST_TEMPLATE_REL)
    plist = preflight._plist_object(raw, "bad_watchdog_plist")
    assert plist["Label"] == watchdog.WATCHDOG_LABEL
    assert plist["StartInterval"] == 15
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["RunAtLoad"] is True
    assert plist["ProgramArguments"][2].endswith("/tools/brain_senses_gecko_capture_watchdog_v3.py")
    assert watchdog.main(["--owner-adapter-required"]) == 2
    assert not (repo / owner_host.WATCHDOG_PLIST_REL).exists()


def test_source_manifest_rejects_symlink_in_closed_source_tree(tmp_path: Path) -> None:
    copied = _source_tree_copy(tmp_path / "repo")
    relative = preflight.SOURCE_PATHS[0]
    target = copied / relative
    original = target.read_bytes()
    replacement = tmp_path / "replacement.py"
    replacement.write_bytes(original)
    target.unlink()
    target.symlink_to(replacement)
    with pytest.raises(preflight.PreflightError, match="source_read_failed"):
        preflight.compile_source_manifest(copied, source_commit="b" * 40)


def test_import_closure_rejects_new_local_import(tmp_path: Path) -> None:
    copied = _source_tree_copy(tmp_path / "repo")
    new_module = copied / "tools/brain_senses_unpinned_local.py"
    new_module.write_text("VALUE = 1\n")
    imported = copied / preflight.SOURCE_PATHS[0]
    imported.write_text(imported.read_text() + "\nimport brain_senses_unpinned_local\n")
    with pytest.raises(preflight.PreflightError, match="import_closure_incomplete"):
        preflight._assert_import_closure(copied)


def _guard_fixture(tmp_path: Path):
    mount = tmp_path / "mount"
    data_root = mount / "BrainData"
    service = data_root / "services" / "capture-test"
    service.mkdir(parents=True)
    for directory in (data_root, data_root / "services", service):
        os.chmod(directory, 0o700)
    mount_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    data_fd = os.open(data_root, os.O_RDONLY | os.O_DIRECTORY)
    service_fd = os.open(service, os.O_RDONLY | os.O_DIRECTORY)
    root_stat = os.fstat(mount_fd)
    storage = {
        "root_id": "a" * 32,
        "volume_uuid": str(uuid.uuid4()),
        "device": root_stat.st_dev,
        "inode": root_stat.st_ino,
        "data_root": "BrainData",
    }
    resolved = SimpleNamespace(
        available_bytes=60 * 1024**3,
        mount_device=root_stat.st_dev,
        mount_inode=root_stat.st_ino,
    )
    guard = SimpleNamespace(
        config=SimpleNamespace(
            root_id=storage["root_id"],
            volume_uuid=storage["volume_uuid"],
            data_root=storage["data_root"],
        ),
        mount_fd=mount_fd,
        revalidate=lambda *, min_free_bytes: resolved,
    )
    return mount_fd, data_fd, service_fd, guard, storage


def test_storage_preflight_requires_same_bound_service_descriptors(tmp_path: Path) -> None:
    mount_fd, data_fd, service_fd, guard, storage = _guard_fixture(tmp_path)
    try:
        observed = preflight._check_storage_descriptors(
            storage=storage,
            storage_guard=guard,
            mount_fd=mount_fd,
            data_root_fd=data_fd,
            service_fd=service_fd,
            source_reader_fd=service_fd,
            writer_root_fd=service_fd,
        )
        assert observed["root_id"] == storage["root_id"]
        assert observed["available_bytes"] == 60 * 1024**3
        other = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with pytest.raises(preflight.PreflightError, match="data_descriptor_mismatch"):
                preflight._check_storage_descriptors(
                    storage=storage,
                    storage_guard=guard,
                    mount_fd=mount_fd,
                    data_root_fd=data_fd,
                    service_fd=service_fd,
                    source_reader_fd=other,
                    writer_root_fd=service_fd,
                )
        finally:
            os.close(other)
        guard.revalidate = lambda *, min_free_bytes: SimpleNamespace(
            available_bytes=min_free_bytes - 1,
            mount_device=os.fstat(mount_fd).st_dev,
            mount_inode=os.fstat(mount_fd).st_ino,
        )
        with pytest.raises(preflight.PreflightError, match="storage_config_mismatch"):
            preflight._check_storage_descriptors(
                storage=storage,
                storage_guard=guard,
                mount_fd=mount_fd,
                data_root_fd=data_fd,
                service_fd=service_fd,
                source_reader_fd=service_fd,
                writer_root_fd=service_fd,
            )
    finally:
        os.close(service_fd)
        os.close(data_fd)
        os.close(mount_fd)


def test_loaded_launch_identity_is_exact_and_separate_from_preflight() -> None:
    label = "ai.memecoin.brain.senses.gecko-capture-only-v3"
    watchdog = "ai.memecoin.brain.senses.gecko-capture-watchdog-v3"
    args = ["/opt/python", "/repo/runtime.py", "--scheduled-tick"]
    watchdog_args = ["/opt/python", "/repo/watchdog.py", "--check"]
    launch = {
        "loaded": True,
        "label": label,
        "plist_path": "/Users/test/Library/LaunchAgents/capture.plist",
        "pid": 123,
        "uid": os.getuid(),
        "program_arguments": args,
        "competing_gecko_labels": [],
    }
    watchdog_snapshot = {
        "loaded": True,
        "label": watchdog,
        "pid": 124,
        "uid": os.getuid(),
        "program_arguments": watchdog_args,
        "schedule_interval_seconds": 15,
    }
    preflight.verify_loaded_launch_identity(
        launch_snapshot=launch,
        watchdog_snapshot=watchdog_snapshot,
        expected_label=label,
        expected_plist_path=launch["plist_path"],
        expected_program_arguments=args,
        expected_watchdog_label=watchdog,
        expected_watchdog_arguments=watchdog_args,
    )
    bad = dict(launch, program_arguments=[*args, "--unexpected"])
    with pytest.raises(preflight.PreflightError, match="launchd_identity"):
        preflight.verify_loaded_launch_identity(
            launch_snapshot=bad,
            watchdog_snapshot=watchdog_snapshot,
            expected_label=label,
            expected_plist_path=launch["plist_path"],
            expected_program_arguments=args,
            expected_watchdog_label=watchdog,
            expected_watchdog_arguments=watchdog_args,
        )
    bad_watchdog = dict(watchdog_snapshot, schedule_interval_seconds=60)
    with pytest.raises(preflight.PreflightError, match="watchdog_identity"):
        preflight.verify_loaded_launch_identity(
            launch_snapshot=launch,
            watchdog_snapshot=bad_watchdog,
            expected_label=label,
            expected_plist_path=launch["plist_path"],
            expected_program_arguments=args,
            expected_watchdog_label=watchdog,
            expected_watchdog_arguments=watchdog_args,
        )


def test_inert_preflight_replays_pins_and_requires_absent_collector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).resolve().parents[1]
    source_commit = "c" * 40
    manifest = preflight.compile_source_manifest(repo, source_commit=source_commit)
    manifest_raw = canonical_json(manifest)
    wall_ns = 1_800_000_000_000_000_000
    monotonic_ns = 50_000_000_000
    clock_record, _ = clock_fixture._clock(wall_ns, monotonic_ns)
    policy = {"policy_sha256": "1" * 64, "population_sha256": "2" * 64}
    successor_root = "services/gecko-market-v1/capture-only-v2/" + "a" * 32
    certificate = {"certificate_sha256": "3" * 64, "successor_root": successor_root}
    binding = {"binding_sha256": "4" * 64}
    plan = {
        "schema": chain.PLAN_SCHEMA,
        "schema_version": 2,
        "bucket_close_ns": wall_ns - 30_000_000_000,
        "record_sha256": "5" * 64,
    }
    records = [plan]
    capture_inputs = {"binding": binding, "chain_records": records}
    monkeypatch.setattr(
        preflight.chain,
        "verify_chain",
        lambda *args, **kwargs: {"last_record_sha256": plan["record_sha256"]},
    )
    monkeypatch.setattr(
        preflight.capture_verify,
        "verify_capture_prefix_v3",
        lambda **kwargs: {
            "status": "verified_prefix",
            "dispatch_blocked": False,
            "unresolved_count": 0,
            "expired_unaccounted_slots": [],
        },
    )
    monkeypatch.setattr(
        preflight.usage_inventory,
        "build_usage_inventory",
        lambda writer, **kwargs: {
            "root_inventory_sha256": "6" * 64,
            "snapshots": [{"root": successor_root, "status": "verified_none"}],
            "provider_io": False,
            "dispatch_authorized": False,
        },
    )
    mount_fd, data_fd, service_fd, guard, storage = _guard_fixture(tmp_path)
    try:
        label = "ai.memecoin.brain.senses.gecko-capture-only-v3"
        watchdog_label = "ai.memecoin.brain.senses.gecko-capture-watchdog-v3"
        args = ["/opt/python", "/repo/gecko.py", "--scheduled-tick"]
        watchdog_args = ["/opt/python", "/repo/watchdog.py", "--check"]
        plist_raw = plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": args,
                "StartInterval": 12,
                "RunAtLoad": True,
                "KeepAlive": False,
                "ProcessType": "Background",
                "StandardOutPath": "/dev/null",
                "StandardErrorPath": "/dev/null",
            },
            fmt=plistlib.FMT_XML,
            sort_keys=True,
        )
        watchdog_raw = plistlib.dumps(
            {
                "Label": watchdog_label,
                "ProgramArguments": watchdog_args,
                "StartInterval": 15,
                "RunAtLoad": True,
                "KeepAlive": {"SuccessfulExit": False},
            },
            fmt=plistlib.FMT_XML,
            sort_keys=True,
        )
        release = preflight.compile_owner_release(
            manifest=manifest,
            storage=storage,
            plan_record=plan,
            certificate=certificate,
            binding=binding,
            policy=policy,
            chain_tail_sha256=plan["record_sha256"],
            plist_raw=plist_raw,
            watchdog_raw=watchdog_raw,
            clock_record=clock_record,
            label=label,
            program_arguments=args,
            watchdog_label=watchdog_label,
            watchdog_program_arguments=watchdog_args,
        )
        result = preflight.preflight_owner_host(
            repo_root=repo,
            source_manifest_raw=manifest_raw,
            expected_source_manifest_sha256=manifest["manifest_sha256"],
            source_commit=source_commit,
            release=release,
            expected_storage=storage,
            mount_fd=mount_fd,
            data_root_fd=data_fd,
            service_fd=service_fd,
            source_reader_fd=service_fd,
            writer_root_fd=service_fd,
            storage_guard=guard,
            plist_raw=plist_raw,
            watchdog_raw=watchdog_raw,
            launch_snapshot={"active_gecko_labels": []},
            policy=policy,
            certificate=certificate,
            certificate_inputs={},
            chain_records=records,
            plan_record=plan,
            capture_inputs=capture_inputs,
            inventory_inputs={
                "writer": object(),
                "required_successor_root": successor_root,
                "expected_handoffs": {},
                "expected_v2_chains": {},
            },
            clock_record=clock_record,
            now_ns=wall_ns,
            monotonic_ns=monotonic_ns,
            expected_label=label,
            expected_program_arguments=args,
        )
        assert result["status"] == "verified_inert_preflight"
        assert result["launchd_state"] == "inactive_no_competitors"
        assert result["provider_io"] is False
        assert result["dispatch_authorized"] is False
        assert result["activation_authorized"] is False
        with pytest.raises(preflight.PreflightError, match="competing_collector"):
            preflight.preflight_owner_host(
                repo_root=repo,
                source_manifest_raw=manifest_raw,
                expected_source_manifest_sha256=manifest["manifest_sha256"],
                source_commit=source_commit,
                release=release,
                expected_storage=storage,
                mount_fd=mount_fd,
                data_root_fd=data_fd,
                service_fd=service_fd,
                source_reader_fd=service_fd,
                writer_root_fd=service_fd,
                storage_guard=guard,
                plist_raw=plist_raw,
                watchdog_raw=watchdog_raw,
                launch_snapshot={"active_gecko_labels": ["ai.memecoin.brain.senses.gecko-old"]},
                policy=policy,
                certificate=certificate,
                certificate_inputs={},
                chain_records=records,
                plan_record=plan,
                capture_inputs=capture_inputs,
                inventory_inputs={
                    "writer": object(),
                    "required_successor_root": successor_root,
                    "expected_handoffs": {},
                    "expected_v2_chains": {},
                },
                clock_record=clock_record,
                now_ns=wall_ns,
                monotonic_ns=monotonic_ns,
                expected_label=label,
                expected_program_arguments=args,
            )
    finally:
        os.close(service_fd)
        os.close(data_fd)
        os.close(mount_fd)


def test_fixed_host_definition_loader_reads_no_follow_plists(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    collector_path = root / owner_host.COLLECTOR_PLIST_REL
    watchdog_path = root / owner_host.WATCHDOG_PLIST_REL
    collector_path.parent.mkdir(parents=True)
    watchdog_path.parent.mkdir(parents=True, exist_ok=True)
    collector_path.write_bytes(
        plistlib.dumps({"StartInterval": 12, "ProgramArguments": ["python", "run"]})
    )
    watchdog_path.write_bytes(
        plistlib.dumps(
            {
                "Label": "ai.memecoin.brain.senses.gecko-capture-watchdog-v3",
                "StartInterval": 15,
                "RunAtLoad": True,
                "KeepAlive": {"SuccessfulExit": False},
                "ProgramArguments": [
                    "/python",
                    "-B",
                    "/repo/tools/brain_senses_gecko_capture_watchdog_v3.py",
                    "--owner-adapter-required",
                ],
            }
        )
    )
    os.chmod(collector_path, 0o600)
    os.chmod(watchdog_path, 0o600)
    loaded = owner_host.load_fixed_host_definitions(root)
    assert loaded["collector_sha256"] == preflight.sha256(loaded["collector_bytes"])
    assert loaded["watchdog_sha256"] == preflight.sha256(loaded["watchdog_bytes"])
    watchdog_path.unlink()
    external = tmp_path / "external.plist"
    external.write_bytes(plistlib.dumps({"StartInterval": 15}))
    watchdog_path.symlink_to(external)
    with pytest.raises(preflight.PreflightError, match="source_read_failed"):
        owner_host.load_fixed_host_definitions(root)


def test_launchd_snapshot_parses_exact_read_only_identity() -> None:
    label = "ai.memecoin.brain.senses.gecko-capture-only-v3"
    plist_path = "/Users/test/Library/LaunchAgents/capture.plist"
    commands: list[list[str]] = []

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        if argv[:2] == ["launchctl", "list"]:
            return subprocess.CompletedProcess(
                argv, 0, f"PID\tStatus\tLabel\n321\t0\t{label}\n", ""
            )
        if argv[:2] == ["launchctl", "print"]:
            printed = (
                f"{owner_host.LAUNCHD_DOMAIN}/{label} = {{\n"
                f" path = {plist_path}\n"
                " pid = 321\n"
                " arguments = {\n"
                "  0 = /opt/python\n"
                "  1 = /repo/gecko.py\n"
                "  2 = --scheduled-tick\n"
                " }\n}\n"
            )
            return subprocess.CompletedProcess(argv, 0, printed, "")
        if argv[:3] == ["ps", "-o", "uid="]:
            return subprocess.CompletedProcess(argv, 0, f"{os.getuid()}\n", "")
        raise AssertionError(f"unexpected read-only command: {argv}")

    snapshot = owner_host.query_launchd_snapshot(
        label=label, expected_plist_path=plist_path, runner=runner
    )
    assert snapshot["loaded"] is True
    assert snapshot["pid"] == 321
    assert snapshot["uid"] == os.getuid()
    assert snapshot["plist_path"] == plist_path
    assert snapshot["program_arguments"] == ["/opt/python", "/repo/gecko.py", "--scheduled-tick"]
    assert commands == [
        ["launchctl", "list"],
        ["launchctl", "print", f"{owner_host.LAUNCHD_DOMAIN}/{label}"],
        ["ps", "-o", "uid=", "-p", "321"],
    ]

    def absent_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "PID\tStatus\tLabel\n", "")

    assert owner_host.collect_inert_launch_snapshot(
        collector_label=label, runner=absent_runner
    ) == {"active_gecko_labels": []}


def test_release_pair_publisher_is_create_only_and_descriptor_bound(tmp_path: Path) -> None:
    mount_fd, data_fd, service_fd, guard, storage = _guard_fixture(tmp_path)
    writer = DescriptorWriter(data_fd)
    writer.mkdir("release")
    release = {"activation_authorized": False, "dispatch_authorized": False}
    result = {
        "status": "verified_inert_preflight",
        "activation_authorized": False,
        "dispatch_authorized": False,
    }
    checks: list[int] = []

    def revalidate() -> None:
        checks.append(os.fstat(data_fd).st_ino)

    try:
        hashes = owner_host.publish_inert_release_pair(
            writer,
            relative_root="release",
            release=release,
            preflight_result=result,
            expected_storage=storage,
            data_root_fd=data_fd,
            storage_guard=guard,
            revalidate=revalidate,
        )
        assert set(hashes) == {"release/release.json", "release/preflight.json"}
        assert len(checks) == 4
        with pytest.raises(StorageError, match="create_only_collision"):
            owner_host.publish_inert_release_pair(
                writer,
                relative_root="release",
                release=release,
                preflight_result=result,
                expected_storage=storage,
                data_root_fd=data_fd,
                storage_guard=guard,
                revalidate=revalidate,
            )
        assert writer.read_bytes("release/release.json") == canonical_json(release)
        assert writer.read_bytes("release/preflight.json") == canonical_json(result)
        rogue_root = tmp_path / "rogue"
        rogue_root.mkdir(mode=0o700)
        rogue_fd = os.open(rogue_root, os.O_RDONLY | os.O_DIRECTORY)
        rogue_writer = DescriptorWriter(rogue_fd)
        rogue_writer.mkdir("release")
        try:
            with pytest.raises(owner_host.OwnerHostError, match="writer_binding_mismatch"):
                owner_host.publish_inert_release_pair(
                    rogue_writer,
                    relative_root="release",
                    release=release,
                    preflight_result=result,
                    expected_storage=storage,
                    data_root_fd=data_fd,
                    storage_guard=guard,
                    revalidate=revalidate,
                )
            assert rogue_writer.list_names("release") == ()
        finally:
            rogue_writer.close()
            os.close(rogue_fd)
    finally:
        writer.close()
        os.close(service_fd)
        os.close(data_fd)
        os.close(mount_fd)


def test_release_pair_detach_at_write_never_falls_back_to_underlay(tmp_path: Path) -> None:
    mount_fd, data_fd, service_fd, guard, storage = _guard_fixture(tmp_path)
    mount_path = tmp_path / "mount"
    detached_path = tmp_path / "detached-mount"
    writer = DescriptorWriter(
        data_fd,
        before_write=lambda: guard.revalidate(min_free_bytes=preflight.MIN_FREE_BYTES),
    )
    writer.mkdir("release")
    original_mount_inode = os.fstat(mount_fd).st_ino
    release = {"activation_authorized": False, "dispatch_authorized": False}
    result = {
        "status": "verified_inert_preflight",
        "activation_authorized": False,
        "dispatch_authorized": False,
    }
    detached = False

    def detach_between_gate_and_atomic_write() -> None:
        nonlocal detached
        if detached:
            return
        os.rename(mount_path, detached_path)
        mount_path.mkdir(mode=0o700)
        detached = True

    def detect_detach(*, min_free_bytes: int) -> Any:
        visible = os.stat(mount_path, follow_symlinks=False)
        held = os.fstat(mount_fd)
        if (visible.st_dev, visible.st_ino) != (held.st_dev, held.st_ino):
            raise RuntimeError("mount_replaced")
        return SimpleNamespace(
            available_bytes=60 * 1024**3,
            mount_device=held.st_dev,
            mount_inode=held.st_ino,
        )

    guard.revalidate = detect_detach
    try:
        with pytest.raises(owner_host.OwnerHostError, match="storage_revalidation"):
            owner_host.publish_inert_release_pair(
                writer,
                relative_root="release",
                release=release,
                preflight_result=result,
                expected_storage=storage,
                data_root_fd=data_fd,
                storage_guard=guard,
                revalidate=detach_between_gate_and_atomic_write,
            )
        assert detached is True
        assert original_mount_inode == os.stat(detached_path).st_ino
        assert list(mount_path.iterdir()) == []
        assert list((detached_path / "BrainData" / "release").iterdir()) == []
    finally:
        writer.close()
        os.close(service_fd)
        os.close(data_fd)
        os.close(mount_fd)
