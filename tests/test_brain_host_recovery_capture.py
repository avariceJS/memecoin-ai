from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools import brain_host_recovery_capture as recovery
from tools.brain_senses_external_writer_v1 import DescriptorWriter


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(recovery, "CHUNK_BYTES", 7)
    storage = tmp_path / "storage"
    storage.mkdir(mode=0o700)
    storage_fd = os.open(storage, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(storage_fd)
    os.close(storage_fd)
    source = tmp_path / "source"
    source.mkdir(mode=0o700)
    (source / "nested").mkdir()
    (source / "nested" / "payload.bin").write_bytes(b"recovery-payload-2026")
    root = recovery.StaticRoot("settings", "host-runtime", "recovery-2026-10-14", source)
    yield tmp_path, writer, source, root
    writer.close()


def _capture(writer: DescriptorWriter, root: recovery.StaticRoot, capture_id="1" * 32):
    return recovery.capture_static_roots(writer, [root], capture_id=capture_id, now_ns=10)


def test_capture_verify_and_extract_roundtrip(harness):
    tmp_path, writer, source, root = harness
    result = _capture(writer, root)
    assert result["complete"] is True
    assert result["verified_bytes"] is True
    manifest_path = "host-recovery/captures/" + "1" * 32 + "/manifest.json"
    manifest = json.loads(writer.read_bytes(manifest_path))
    assert manifest["consistency_claim"] == "caller_asserted_recovery_points_no_global_atomicity"
    assert manifest["roots"][0]["recovery_point_id"] == "recovery-2026-10-14"
    assert recovery.verify_capture(writer, "1" * 32)["member_count"] == 3

    destination = tmp_path / "restored"
    restored = recovery.extract_capture(writer, "1" * 32, destination)
    assert restored["restore_complete"] is True
    assert (destination / "settings" / "nested" / "payload.bin").read_bytes() == (
        source / "nested" / "payload.bin"
    ).read_bytes()
    assert (destination / "RESTORE_COMPLETE.json").exists()


def test_interruption_leaves_no_complete_marker(harness):
    tmp_path, base_writer, _source, root = harness
    base_writer.close()
    calls = 0

    def interrupt(_operation: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 7:
            raise OSError("injected interruption")

    storage_fd = os.open(tmp_path / "storage", os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(storage_fd, fault_hook=interrupt)
    os.close(storage_fd)
    try:
        with pytest.raises(recovery.RecoveryCaptureError):
            _capture(writer, root)
    finally:
        writer.close()
    plain_fd = os.open(tmp_path / "storage", os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(plain_fd)
    os.close(plain_fd)
    try:
        with pytest.raises(recovery.RecoveryCaptureError, match="capture_artifact_missing"):
            recovery.verify_capture(writer, "1" * 32)
    finally:
        writer.close()


def test_capture_id_collision_is_create_only(harness):
    _tmp_path, writer, _source, root = harness
    _capture(writer, root)
    with pytest.raises(recovery.RecoveryCaptureError, match="capture_id_collision"):
        _capture(writer, root)


def test_verify_rejects_corrupt_chunk_and_extra_member(harness):
    _tmp_path, writer, _source, root = harness
    _capture(writer, root)
    capture_id = "1" * 32
    manifest = json.loads(writer.read_bytes(f"host-recovery/captures/{capture_id}/manifest.json"))
    chunk_path = manifest["roots"][0]["entries"][-1]["chunks"][0]["path"]
    disk_path = _tmp_path / "storage" / chunk_path
    disk_path.write_bytes(b"corrupt")
    with pytest.raises(recovery.RecoveryCaptureError, match="capture_chunk_hash_mismatch"):
        recovery.verify_capture(writer, capture_id)

    # Restore the chunk bytes, then create an unlisted member: exact membership fails.
    source_payload = (_source / "nested" / "payload.bin").read_bytes()
    first_chunk_size = manifest["roots"][0]["entries"][-1]["chunks"][0]["size_bytes"]
    disk_path.write_bytes(source_payload[:first_chunk_size])
    writer.create(f"host-recovery/captures/{capture_id}/unlisted.txt", b"extra")
    with pytest.raises(recovery.RecoveryCaptureError, match="capture_membership_drift"):
        recovery.verify_capture(writer, capture_id)


def test_verifier_rejects_manifest_chunk_reorder(harness):
    _tmp_path, writer, _source, root = harness
    _capture(writer, root)
    manifest = json.loads(writer.read_bytes(f"host-recovery/captures/{'1' * 32}/manifest.json"))
    chunks = manifest["roots"][0]["entries"][-1]["chunks"]
    chunks[0], chunks[1] = chunks[1], chunks[0]
    with pytest.raises(recovery.RecoveryCaptureError, match="manifest_chunk_order_invalid"):
        recovery._validate_manifest(manifest, "1" * 32)


def test_source_membership_drift_aborts_before_complete(harness):
    tmp_path, base_writer, source, root = harness
    base_writer.close()
    changed = False

    def drift(_operation: str) -> None:
        nonlocal changed
        if not changed:
            changed = True
            (source / "late-added.txt").write_text("changed", encoding="utf-8")

    storage_fd = os.open(tmp_path / "storage", os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(storage_fd, fault_hook=drift)
    os.close(storage_fd)
    try:
        with pytest.raises(
            recovery.RecoveryCaptureError, match="source_membership_or_metadata_changed"
        ):
            _capture(writer, root)
        assert "COMPLETE" not in writer.list_names(f"host-recovery/captures/{'1' * 32}")
    finally:
        writer.close()


def test_detach_fault_and_missing_marker_fail_closed(harness):
    tmp_path, base_writer, _source, root = harness
    base_writer.close()

    def detach(operation: str) -> None:
        if operation == "create":
            raise OSError("injected detach")

    storage_fd = os.open(tmp_path / "storage", os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(storage_fd, fault_hook=detach)
    os.close(storage_fd)
    try:
        with pytest.raises(recovery.RecoveryCaptureError):
            _capture(writer, root)
    finally:
        writer.close()

    storage_fd = os.open(tmp_path / "storage", os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(storage_fd)
    os.close(storage_fd)
    try:
        with pytest.raises(recovery.RecoveryCaptureError):
            recovery.verify_capture(writer, "1" * 32)
    finally:
        writer.close()


def test_rejects_source_symlink_and_unsafe_manifest_archive_path(harness):
    _tmp_path, writer, _source, root = harness
    linked = root.path.parent / "linked-root"
    linked.symlink_to(root.path, target_is_directory=True)
    unsafe_root = recovery.StaticRoot("settings", "host-runtime", "point", linked)
    with pytest.raises(recovery.RecoveryCaptureError, match="source_root_open_failed"):
        _capture(writer, unsafe_root)

    malicious = {
        "schema": recovery.SCHEMA,
        "capture_id": "1" * 32,
        "chunk_bytes": recovery.CHUNK_BYTES,
        "created_ns": 10,
        "consistency_claim": "caller_asserted_recovery_points_no_global_atomicity",
        "membership_sha256": "0" * 64,
        "member_count": 0,
        "total_source_bytes": 0,
        "metadata_policy": {
            "recorded": ["mode", "uid", "gid", "mtime_ns"],
            "restore": ["mode", "mtime_ns"],
        },
        "roots": [
            {
                "name": "settings",
                "scope": "non_protected_static",
                "authority_id": "host-runtime",
                "recovery_point_id": "point",
                "recovery_point_source": "caller_asserted",
                "source_device": 1,
                "source_inode": 1,
                "entries": [
                    {
                        "path": "../escape",
                        "kind": "directory",
                        "mode": 0o700,
                        "uid": 1,
                        "gid": 1,
                        "size_bytes": 0,
                        "mtime_ns": 1,
                        "source_device": 1,
                        "source_inode": 2,
                        "source_ctime_ns": 1,
                    }
                ],
            }
        ],
    }
    with pytest.raises(recovery.RecoveryCaptureError, match="unsafe_relative_path"):
        recovery._validate_manifest(malicious, "1" * 32)


def test_verify_detects_member_reordering_via_manifest_canonical_contract(harness):
    _tmp_path, writer, _source, root = harness
    _capture(writer, root)
    manifest = json.loads(writer.read_bytes(f"host-recovery/captures/{'1' * 32}/manifest.json"))
    entries = manifest["roots"][0]["entries"]
    entries.reverse()
    with pytest.raises(recovery.RecoveryCaptureError, match="manifest_entry_order_invalid"):
        recovery._validate_manifest(manifest, "1" * 32)


def test_empty_restore_destination_is_required(harness):
    tmp_path, writer, _source, root = harness
    _capture(writer, root)
    destination = tmp_path / "existing"
    destination.mkdir()
    with pytest.raises(recovery.RecoveryCaptureError, match="restore_failed"):
        recovery.extract_capture(writer, "1" * 32, destination)
