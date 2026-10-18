from __future__ import annotations

import ast
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from memecoin_research_storage import StorageError
from tools import research_kingston_capacity_qualify_v1 as qualify
from tools.brain_senses_external_writer_v1 import DescriptorWriter


def _preflight_objects(**changes: object) -> tuple[object, object, object]:
    config: dict[str, Any] = {
        "root_id": qualify.EXPECTED_ROOT_ID,
        "volume_uuid": qualify.EXPECTED_UUID,
        "min_free_bytes": qualify.CONFIGURED_FLOOR_BYTES,
    }
    resolved: dict[str, Any] = {
        "root_id": qualify.EXPECTED_ROOT_ID,
        "volume_uuid": qualify.EXPECTED_UUID,
        "available_bytes": qualify.INITIAL_FREE_MINIMUM,
    }
    volume: dict[str, Any] = {
        "volume_uuid": qualify.EXPECTED_UUID,
        "filesystem": "Case-sensitive APFS",
        "owners_enabled": True,
        "read_only": False,
        "is_mountpoint": True,
        "available_bytes": qualify.INITIAL_FREE_MINIMUM,
    }
    for key, value in changes.items():
        target, field = key.split("__", 1)
        {"config": config, "resolved": resolved, "volume": volume}[target][field] = value
    return (
        SimpleNamespace(**config),
        SimpleNamespace(**resolved),
        SimpleNamespace(**volume),
    )


def test_plan_respects_exact_capacity_and_file_caps() -> None:
    plan = qualify.plan_shape()
    assert plan["payload_bytes"] == 3_104_000_000
    assert plan["bootstrap_bytes"] == 32_000_000
    assert plan["frames"] == 12
    assert plan["frame_bytes_each"] == 256_000_000
    assert plan["response_files"] == 97 <= plan["maximum_response_files"] == 409
    assert plan["max_bytes_per_file"] == 32_000_000
    assert plan["initial_free_minimum_bytes"] == 57_081_091_200


def test_preflight_rejects_wrong_identity_floor_or_policy() -> None:
    valid = _preflight_objects()
    qualify._validate_preflight_binding(*valid)

    for changes in (
        {"config__root_id": "0" * 32},
        {"volume__volume_uuid": "0cfe6ff9-059c-440d-a847-6f833dab961c"},
        {"volume__available_bytes": qualify.INITIAL_FREE_MINIMUM - 1},
        {"resolved__available_bytes": qualify.INITIAL_FREE_MINIMUM - 1},
        {"volume__filesystem": "APFS"},
        {"volume__owners_enabled": False},
        {"volume__read_only": True},
        {"volume__is_mountpoint": False},
    ):
        with pytest.raises(StorageError, match="kingston_preflight_identity_or_capacity_failed"):
            qualify._validate_preflight_binding(*_preflight_objects(**changes))


def test_partial_prefix_recovery_is_spent_and_never_replayed() -> None:
    assert (
        qualify.classify_recovery(
            expected_bytes=4_000_000,
            observed_prefix_bytes=1_000_000,
            intent_trusted=True,
        )
        == "possibly_spent_no_replay"
    )


def test_interrupted_child_exits_after_fsync_and_parent_recovers_read_only(
    tmp_path: Path,
) -> None:
    root = tmp_path / "private-root"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with DescriptorWriter(root_fd) as writer:
            samples: list[int] = []
            recovery = qualify._write_recovery_fixture(
                writer,
                nonce="test-nonce",
                guard=SimpleNamespace(
                    revalidate=lambda **_: SimpleNamespace(available_bytes=100_000_000)
                ),
                guard_free_samples=samples,
            )
            assert recovery["writer_process_exit_code"] == 73
            assert recovery["observed_prefix_bytes"] == qualify.RECOVERY_BYTES
            assert recovery["recovery"] == "possibly_spent_no_replay"
            assert recovery["unknown_probe"] == "unknown_no_replay"
            assert recovery["recovery_writes"] == 0
            assert recovery["recovery_dispatches"] == 0
            assert len(samples) >= 3
            allocation = qualify._nonce_allocation_inventory(writer, "test-nonce")
            assert len(allocation["directories"]) == 2
            assert sum(item["logical_bytes"] for item in allocation["files"]) > (
                qualify.RECOVERY_BYTES
            )
            assert writer.stat("test-nonce/synthetic-interruption/partial-prefix.bin").st_size == (
                qualify.RECOVERY_BYTES
            )
            writer.unlink("test-nonce/synthetic-interruption/partial-prefix.bin")
            writer.unlink("test-nonce/synthetic-interruption/synthetic-intent.json")
            parent_fd = os.open(
                "test-nonce", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=writer.root_fd
            )
            try:
                os.rmdir("synthetic-interruption", dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
            os.rmdir("test-nonce", dir_fd=writer.root_fd)
    finally:
        os.close(root_fd)


def test_independent_process_is_excluded_by_writer_lock(tmp_path: Path) -> None:
    root = tmp_path / "private-root"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with DescriptorWriter(root_fd) as writer:
            with writer.lock("qualification.lock"):
                assert qualify._independent_lock_attempt(writer.root_fd, "qualification.lock") == 0
            writer.unlink("qualification.lock")
    finally:
        os.close(root_fd)


def test_root_snapshot_accepts_existing_private_subdirectories(tmp_path: Path) -> None:
    root = tmp_path / "private-root"
    root.mkdir(mode=0o700)
    existing = root / "existing-service-directory"
    existing.mkdir(mode=0o700)
    child = existing / "marker.json"
    child.write_bytes(b"{}")
    child.chmod(0o600)
    metadata = root / ".DS_Store"
    metadata.write_bytes(b"finder-metadata")
    metadata.chmod(0o644)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with DescriptorWriter(root_fd) as writer:
            assert qualify._root_listing(writer) == (
                (
                    ".DS_Store",
                    "file",
                    0o644,
                    metadata.stat().st_ino,
                    metadata.stat().st_size,
                    metadata.stat().st_mtime_ns,
                ),
                (
                    "existing-service-directory",
                    "directory",
                    0o700,
                    existing.stat().st_ino,
                    existing.stat().st_size,
                    existing.stat().st_mtime_ns,
                ),
            )
    finally:
        os.close(root_fd)


def test_receipt_path_is_create_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    evidence = repo / "docs/evidence"
    evidence.mkdir(parents=True)
    monkeypatch.setattr(qualify, "REPO", repo)
    target = evidence / "qualification.json"
    qualify._write_receipt_create_only(target, {"status": "passed"})
    assert target.read_text() == '{\n  "status": "passed"\n}\n'
    with pytest.raises(FileExistsError):
        qualify._write_receipt_create_only(target, {"status": "second-write"})
    assert (
        qualify.classify_recovery(
            expected_bytes=4_000_000,
            observed_prefix_bytes=None,
            intent_trusted=False,
        )
        == "unknown_no_replay"
    )
    assert (
        qualify.classify_recovery(
            expected_bytes=4_000_000,
            observed_prefix_bytes=5_000_000,
            intent_trusted=True,
        )
        == "unknown_no_replay"
    )


def test_capacity_tool_import_graph_has_no_provider_or_credential_modules() -> None:
    source = Path(qualify.__file__).read_text()
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    forbidden = {
        "memecoin_ai",
        "memecoin_collector",
        "requests",
        "httpx",
        "aiohttp",
        "keyring",
    }
    assert imported.isdisjoint(forbidden)
    assert "--execute" in source and "--preflight-only" in source
