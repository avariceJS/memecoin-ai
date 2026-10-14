from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tools import brain_host_tree_restore_v4_synthetic as restore
from tools.brain_host_apfs_snapshot_attestor_v1 import (
    MNT_RDONLY,
    ApprovedHostPlan,
    HeldSnapshotT,
    approved_host_plan_sha256,
)
from tools.brain_host_tree_capture_v4 import capture_synthetic_descriptor_tree


class _FakeSnapshotOS:
    def __init__(self, mountpoint: Path) -> None:
        self.mountpoint = mountpoint

    def disk_info(self, target: str) -> dict[str, object]:
        if target == "/dev/disk3s5":
            return {
                "FilesystemType": "apfs",
                "VolumeUUID": "11111111-2222-3333-4444-555555555555",
                "DeviceIdentifier": "disk3s5",
                "APFSContainerReference": "disk3",
                "APFSVolumeGroupID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "APFSSnapshot": False,
            }
        return {
            "FilesystemType": "apfs",
            "APFSSnapshot": True,
            "APFSSnapshotUUID": "99999999-8888-7777-6666-555555555555",
            "VolumeUUID": "99999999-8888-7777-6666-555555555555",
            "APFSVolumeGroupID": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "MountPoint": str(self.mountpoint),
        }

    def snapshots(self, _target: str) -> list[dict[str, object]]:
        return [
            {
                "SnapshotUUID": "99999999-8888-7777-6666-555555555555",
                "SnapshotXID": 420,
                "SnapshotName": "fixture-snapshot",
            }
        ]

    def filesystem(self, _fd: int) -> dict[str, object]:
        return {
            "fs_type": "apfs",
            "flags": MNT_RDONLY,
            "fsid": (71, 72),
            "mountpoint": str(self.mountpoint),
            "source": "fixture-snapshot@/dev/disk3s5",
        }


def _held(tmp_path: Path) -> tuple[HeldSnapshotT, dict[str, Path]]:
    mountpoint = tmp_path / "snapshot-mount"
    mountpoint.mkdir()
    roots = {role: mountpoint / role for role in ("owner", "research", "private")}
    for root in roots.values():
        root.mkdir()
    plan = ApprovedHostPlan(
        approval_id="synthetic-restore-test",
        data_volume_uuid="11111111-2222-3333-4444-555555555555",
        data_device_identifier="disk3s5",
        data_container_reference="disk3",
        data_volume_group_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        snapshot_uuid="99999999-8888-7777-6666-555555555555",
        snapshot_xid=420,
        root_locators={role: role for role in roots},
    )
    held = HeldSnapshotT(
        plan,
        mountpoint,
        os_api=_FakeSnapshotOS(mountpoint),
        synthetic=True,
        trusted_plan_sha256=approved_host_plan_sha256(plan),
    )
    return held, roots


def _captured(tmp_path: Path, *, nonce: str = "a" * 32) -> Path:
    held, roots = _held(tmp_path)
    try:
        owner = roots["owner"]
        research = roots["research"]
        private = roots["private"]
        (owner / "subdir").mkdir()
        (owner / "subdir" / "primary.bin").write_bytes(b"cross-root hardlink payload")
        os.link(owner / "subdir" / "primary.bin", research / "alias.bin")
        (private / "external.bin").write_bytes(b"unresolved external alias")
        os.link(private / "external.bin", tmp_path / "outside-alias.bin")
        (owner / ".env.local").write_bytes(b"must remain excluded")
        os.link(owner / ".env.local", research / "credential-alias")
        (owner / "absolute-link").symlink_to(tmp_path / "outside-alias.bin")
        (research / "dangling-link").symlink_to("../../missing-target")
        capture_synthetic_descriptor_tree(held, tmp_path, nonce=nonce)
    finally:
        held.close()
    return tmp_path / f"brain-capture-{nonce}"


def test_reconstructs_bytes_hardlinks_and_inert_evidence(tmp_path: Path) -> None:
    capture_dir = _captured(tmp_path)
    result = restore.reconstruct_synthetic_v4(capture_dir, tmp_path, nonce="b" * 32)
    quarantine = tmp_path / result["quarantine"]
    assert result["byte_topology_reconstruction_verified"] is True
    assert result["metadata_applied"] is False
    assert result["full_restore_verified"] is False
    assert result["real_T"] is False
    assert result["erase_ready"] is False
    assert result["inert_symlink_count"] == 2
    assert result["unresolved_external_hardlink_group_count"] == 1
    assert result["unresolved_external_hardlink_groups"][0]["external_location"] == "unresolved"
    manifest = json.loads((capture_dir / "capture.json").read_bytes())
    root_keys = {row["source_locator"]: row["root_key"] for row in manifest["plan"]["root_roster"]}
    primary = quarantine / root_keys["owner"] / "subdir" / "primary.bin"
    alias = quarantine / root_keys["research"] / "alias.bin"
    assert primary.read_bytes() == b"cross-root hardlink payload"
    assert os.stat(primary).st_ino == os.stat(alias).st_ino
    assert not (quarantine / root_keys["owner"] / ".env.local").exists()
    assert not (quarantine / root_keys["research"] / "credential-alias").exists()
    assert not (quarantine / root_keys["owner"] / "absolute-link").exists()
    assert not (quarantine / root_keys["research"] / "dangling-link").exists()
    metadata_bytes = restore._canonical(manifest["metadata_values"])
    evidence = quarantine / "metadata-evidence.json"
    assert evidence.read_bytes() == metadata_bytes
    assert hashlib.sha256(evidence.read_bytes()).hexdigest() == result["metadata_evidence_sha256"]
    assert sorted(path.name for path in quarantine.iterdir()) == sorted(
        [
            "metadata-evidence.json",
            *root_keys.values(),
            "synthetic-witness.json",
        ]
    )


def test_corrupt_capture_object_fails_before_quarantine(tmp_path: Path) -> None:
    capture_dir = _captured(tmp_path)
    object_path = next(capture_dir.glob("object-*.bin"))
    object_path.write_bytes(b"corrupt")
    with pytest.raises(restore.SyntheticRestoreError):
        restore.reconstruct_synthetic_v4(capture_dir, tmp_path, nonce="c" * 32)
    assert not (tmp_path / ("brain-restore-" + "c" * 32)).exists()


def test_extra_capture_member_fails_before_quarantine(tmp_path: Path) -> None:
    capture_dir = _captured(tmp_path)
    (capture_dir / "unexpected.bin").write_bytes(b"not in manifest")
    with pytest.raises(restore.SyntheticRestoreError, match="member_roster"):
        restore.reconstruct_synthetic_v4(capture_dir, tmp_path, nonce="9" * 32)
    assert not (tmp_path / ("brain-restore-" + "9" * 32)).exists()


def test_missing_capture_object_fails_before_quarantine(tmp_path: Path) -> None:
    capture_dir = _captured(tmp_path)
    next(capture_dir.glob("object-*.bin")).unlink()
    with pytest.raises((restore.SyntheticRestoreError, FileNotFoundError)):
        restore.reconstruct_synthetic_v4(capture_dir, tmp_path, nonce="4" * 32)
    assert not (tmp_path / ("brain-restore-" + "4" * 32)).exists()


def test_fifo_object_is_rejected_without_blocking(tmp_path: Path) -> None:
    capture_dir = _captured(tmp_path)
    object_path = next(capture_dir.glob("object-*.bin"))
    object_path.unlink()
    os.mkfifo(object_path)
    with pytest.raises(restore.SyntheticRestoreError, match="identity"):
        restore.reconstruct_synthetic_v4(capture_dir, tmp_path, nonce="3" * 32)
    assert not (tmp_path / ("brain-restore-" + "3" * 32)).exists()


def test_capture_descriptor_closes_when_receipt_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_dir = _captured(tmp_path)
    real_open = os.open
    capture_fds: list[int] = []

    def fail_receipt(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if path == "capture.json":
            fd = real_open(path, flags, *args, **kwargs)
            capture_fds.append(fd)
            return fd
        if path == "receipt.json":
            raise OSError("synthetic receipt open failure")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(restore.os, "open", fail_receipt)
    with pytest.raises(OSError, match="receipt open failure"):
        restore.reconstruct_synthetic_v4(capture_dir, tmp_path, nonce="2" * 32)
    assert len(capture_fds) == 1
    with pytest.raises(OSError):
        os.fstat(capture_fds[0])


def test_quarantine_open_failure_removes_new_nonce_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_dir = _captured(tmp_path)
    real_open = os.open
    failed = False

    def fail_quarantine_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal failed
        if (
            not failed
            and isinstance(path, str)
            and path == "brain-restore-" + "1" * 32
            and flags & os.O_DIRECTORY
        ):
            failed = True
            raise OSError("synthetic quarantine open failure")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(restore.os, "open", fail_quarantine_open)
    with pytest.raises(OSError, match="quarantine open failure"):
        restore.reconstruct_synthetic_v4(capture_dir, tmp_path, nonce="1" * 32)
    assert failed
    assert not (tmp_path / ("brain-restore-" + "1" * 32)).exists()


def test_wrong_object_group_mapping_fails_before_quarantine(tmp_path: Path) -> None:
    capture_dir = _captured(tmp_path)
    manifest_path = capture_dir / "capture.json"
    receipt_path = capture_dir / "receipt.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["objects"][0]["path"] = "object-" + "0" * 64 + ".bin"
    manifest_bytes = restore._canonical(manifest)
    receipt = json.loads(receipt_path.read_bytes())
    receipt["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_path.write_bytes(manifest_bytes)
    receipt_path.write_bytes(restore._canonical(receipt))
    with pytest.raises(restore.SyntheticRestoreError, match="group_mapping"):
        restore.reconstruct_synthetic_v4(capture_dir, tmp_path, nonce="8" * 32)
    assert not (tmp_path / ("brain-restore-" + "8" * 32)).exists()


def test_preexisting_quarantine_is_never_modified(tmp_path: Path) -> None:
    capture_dir = _captured(tmp_path)
    output = tmp_path / ("brain-restore-" + "d" * 32)
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("caller-owned")
    with pytest.raises(FileExistsError):
        restore.reconstruct_synthetic_v4(capture_dir, tmp_path, nonce="d" * 32)
    assert marker.read_text() == "caller-owned"


def test_cross_device_link_failure_removes_only_owned_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_dir = _captured(tmp_path)
    real_link = os.link

    def fail_link(*args: object, **kwargs: object) -> None:
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(restore.os, "link", fail_link)
    with pytest.raises(OSError, match="cross-device"):
        restore.reconstruct_synthetic_v4(capture_dir, tmp_path, nonce="e" * 32)
    assert not (tmp_path / ("brain-restore-" + "e" * 32)).exists()
    monkeypatch.setattr(restore.os, "link", real_link)


def test_interruption_removes_run_owned_partial_tree(tmp_path: Path) -> None:
    capture_dir = _captured(tmp_path)

    def interrupt(_index: int, _output: Path) -> None:
        raise RuntimeError("synthetic interruption")

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        restore.reconstruct_synthetic_v4(
            capture_dir, tmp_path, nonce="f" * 32, interruption_hook=interrupt
        )
    assert not (tmp_path / ("brain-restore-" + "f" * 32)).exists()


def test_post_write_mutation_fails_and_cleans_owned_quarantine(tmp_path: Path) -> None:
    capture_dir = _captured(tmp_path)

    def mutate(_index: int, output: Path) -> None:
        target = next(path for path in output.glob("root-*/**/*") if path.is_file())
        target.write_bytes(b"post-write mutation")

    with pytest.raises(restore.SyntheticRestoreError):
        restore.reconstruct_synthetic_v4(
            capture_dir, tmp_path, nonce="7" * 32, interruption_hook=mutate
        )
    assert not (tmp_path / ("brain-restore-" + "7" * 32)).exists()


def test_replacement_of_capture_bundle_during_restore_fails_closed(tmp_path: Path) -> None:
    capture_dir = _captured(tmp_path)
    changed = False

    def replace_receipt(_index: int, _output: Path) -> None:
        nonlocal changed
        if not changed:
            (capture_dir / "receipt.json").write_bytes(b"{}")
            changed = True

    with pytest.raises(restore.SyntheticRestoreError):
        restore.reconstruct_synthetic_v4(
            capture_dir, tmp_path, nonce="6" * 32, interruption_hook=replace_receipt
        )
    assert not (tmp_path / ("brain-restore-" + "6" * 32)).exists()


def test_external_alias_added_to_restored_inode_fails_closed(tmp_path: Path) -> None:
    capture_dir = _captured(tmp_path)
    external_alias = tmp_path / "restore-external-alias.bin"
    created = False

    def add_external_alias(_index: int, output: Path) -> None:
        nonlocal created
        if created:
            return
        target = next(path for path in output.glob("root-*/**/*") if path.is_file())
        os.link(target, external_alias)
        created = True

    try:
        with pytest.raises(restore.SyntheticRestoreError, match="external_alias"):
            restore.reconstruct_synthetic_v4(
                capture_dir, tmp_path, nonce="5" * 32, interruption_hook=add_external_alias
            )
        assert not (tmp_path / ("brain-restore-" + "5" * 32)).exists()
    finally:
        external_alias.unlink(missing_ok=True)
