from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tools import brain_host_tree_capture_v4 as capture_module
from tools.brain_host_apfs_snapshot_attestor_v1 import (
    MNT_RDONLY,
    ApprovedHostPlan,
    HeldSnapshotT,
    approved_host_plan_sha256,
)
from tools.brain_host_tree_capture_v4 import TreeCaptureError, capture_synthetic_descriptor_tree

DATA_UUID = "11111111-2222-3333-4444-555555555555"
GROUP_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SNAPSHOT_UUID = "99999999-8888-7777-6666-555555555555"


class FakeSnapshotOS:
    def __init__(self, mountpoint: Path) -> None:
        self.mountpoint = mountpoint

    def disk_info(self, target: str) -> dict[str, Any]:
        if target == "/dev/disk3s5":
            return {
                "FilesystemType": "apfs",
                "VolumeUUID": DATA_UUID,
                "DeviceIdentifier": "disk3s5",
                "APFSContainerReference": "disk3",
                "APFSVolumeGroupID": GROUP_ID,
                "APFSSnapshot": False,
            }
        return {
            "FilesystemType": "apfs",
            "APFSSnapshot": True,
            "APFSSnapshotUUID": SNAPSHOT_UUID,
            "VolumeUUID": SNAPSHOT_UUID,
            "APFSVolumeGroupID": GROUP_ID,
            "MountPoint": str(self.mountpoint),
        }

    def snapshots(self, target: str) -> list[dict[str, Any]]:
        return [
            {"SnapshotUUID": SNAPSHOT_UUID, "SnapshotXID": 420, "SnapshotName": "fixture-snapshot"}
        ]

    def filesystem(self, fd: int) -> dict[str, Any]:
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
        approval_id="synthetic-capture-test",
        data_volume_uuid=DATA_UUID,
        data_device_identifier="disk3s5",
        data_container_reference="disk3",
        data_volume_group_id=GROUP_ID,
        snapshot_uuid=SNAPSHOT_UUID,
        snapshot_xid=420,
        root_locators={role: role for role in roots},
    )
    held = HeldSnapshotT(
        plan,
        mountpoint,
        os_api=FakeSnapshotOS(mountpoint),
        synthetic=True,
        trusted_plan_sha256=approved_host_plan_sha256(plan),
    )
    return held, roots


def test_descriptor_capture_reconciles_aliases_and_reads_only_included_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held, roots = _held(tmp_path)
    try:
        (roots["owner"] / "notes.txt").write_bytes(b"owner research state")
        (roots["owner"] / ".env.local").write_bytes(b"credential-must-not-be-read")
        os.link(roots["owner"] / ".env.local", roots["research"] / "credential-alias")
        symlink_target = tmp_path / "external-secret"
        symlink_target.write_bytes(b"target-must-not-be-read")
        outside = roots["private"] / "unresolved-link-count"
        outside.write_bytes(b"has unseen alias")
        os.link(outside, tmp_path / "outside-hardlink")
        (roots["owner"] / "dangling-link").symlink_to("../../external-secret")
        if hasattr(os, "setxattr"):
            os.setxattr(roots["owner"] / "notes.txt", b"user.capture", b"preserved-xattr")

        env_inode = (roots["owner"] / ".env.local").stat().st_ino
        symlink_target_inode = symlink_target.stat().st_ino
        real_read = os.read
        real_list_xattrs = capture_module._list_xattrs

        def guarded_read(fd: int, count: int) -> bytes:
            if os.fstat(fd).st_ino in {env_inode, symlink_target_inode}:
                pytest.fail("excluded credential or symlink target bytes were read")
            return real_read(fd, count)

        def guarded_xattrs(fd: int) -> list[bytes]:
            if os.fstat(fd).st_ino == env_inode:
                pytest.fail("excluded credential metadata was probed")
            return real_list_xattrs(fd)

        monkeypatch.setattr("tools.brain_host_tree_capture_v4.os.read", guarded_read)
        monkeypatch.setattr("tools.brain_host_tree_capture_v4._list_xattrs", guarded_xattrs)
        receipt = capture_synthetic_descriptor_tree(held, tmp_path, nonce="a" * 32)
        out = tmp_path / f"brain-capture-{'a' * 32}"
        manifest = json.loads((out / "capture.json").read_text())
        assert receipt["synthetic"] is True
        assert receipt["real_T"] is False
        assert receipt["kingston_custody"] is False
        assert manifest["complete"] is False
        assert not (out / "COMPLETE").exists()
        assert len(manifest["objects"]) == 2
        assert all("credential" not in item["path"] for item in manifest["objects"])
        assert manifest["plan"]["unaccounted_hardlink_group_count"] >= 1
        assert manifest["plan"]["reissue_excluded_entry_count"] == 2
        symlink = next(row for row in manifest["plan"]["entries"] if row["path"] == "dangling-link")
        assert symlink["symlink_materialize"] is False
        values = next(
            value
            for key, value in manifest["metadata_values"].items()
            if key.endswith(":notes.txt")
        )
        assert values["xattrs"]
        object_row = next(
            item
            for item in manifest["objects"]
            if item["size_bytes"] == len(b"owner research state")
        )
        fresh = (out / object_row["path"]).read_bytes()
        assert hashlib.sha256(fresh).hexdigest() == object_row["sha256"]
        assert fresh == b"owner research state"
    finally:
        held.close()


def test_source_mutation_and_interruption_leave_no_capture_tree(tmp_path: Path) -> None:
    held, roots = _held(tmp_path)
    try:
        source = roots["owner"] / "item.bin"
        source.write_bytes(b"before")
        with pytest.raises(RuntimeError, match="interrupt"):
            capture_synthetic_descriptor_tree(
                held,
                tmp_path,
                nonce="b" * 32,
                interruption_hook=lambda _index, _out: (_ for _ in ()).throw(
                    RuntimeError("interrupt")
                ),
            )
        assert not (tmp_path / f"brain-capture-{'b' * 32}").exists()

        def mutate(_index: int, _out: Path) -> None:
            source.write_bytes(b"after mutation")

        with pytest.raises(TreeCaptureError):
            capture_synthetic_descriptor_tree(
                held, tmp_path, nonce="c" * 32, interruption_hook=mutate
            )
        assert not (tmp_path / f"brain-capture-{'c' * 32}").exists()
    finally:
        held.close()


def test_capture_rejects_non_synthetic_handle(tmp_path: Path) -> None:
    held, _roots = _held(tmp_path)
    try:
        held.synthetic = False
        with pytest.raises(TreeCaptureError, match="synthetic_handle_required"):
            capture_synthetic_descriptor_tree(held, tmp_path, nonce="d" * 32)
    finally:
        held.close()


def test_env_local_symlink_target_is_not_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held, roots = _held(tmp_path)
    try:
        target = tmp_path / "private-target"
        target.write_bytes(b"private")
        (roots["owner"] / ".env.local").symlink_to("../../private-target")
        real_readlink = os.readlink

        def guarded_readlink(path: Any, *args: Any, **kwargs: Any) -> Any:
            if os.fspath(path) == ".env.local":
                pytest.fail("excluded .env.local symlink text was read")
            return real_readlink(path, *args, **kwargs)

        monkeypatch.setattr("tools.brain_host_tree_capture_v4.os.readlink", guarded_readlink)
        capture_synthetic_descriptor_tree(held, tmp_path, nonce="e" * 32)
        manifest = json.loads((tmp_path / f"brain-capture-{'e' * 32}" / "capture.json").read_text())
        row = next(item for item in manifest["plan"]["entries"] if item["path"] == ".env.local")
        assert row["link_text_b64"] is None
        assert row["disposition"] == "reissue_excluded"
    finally:
        held.close()


def test_unsupported_xattr_inventory_fails_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held, roots = _held(tmp_path)
    try:
        (roots["owner"] / "item").write_bytes(b"payload")

        def unsupported(_fd: int) -> list[bytes]:
            raise TreeCaptureError("xattr_inventory_unsupported")

        monkeypatch.setattr("tools.brain_host_tree_capture_v4._list_xattrs", unsupported)
        with pytest.raises(TreeCaptureError, match="xattr_inventory_unsupported"):
            capture_synthetic_descriptor_tree(held, tmp_path, nonce="f" * 32)
        assert not (tmp_path / f"brain-capture-{'f' * 32}").exists()
    finally:
        held.close()


def test_post_borrow_revalidation_failure_removes_nonce_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held, roots = _held(tmp_path)
    try:
        (roots["owner"] / "item").write_bytes(b"payload")
        real_revalidate = held.revalidate
        calls = 0

        def fail_last_borrow_exit() -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 6:
                raise RuntimeError("post-borrow revalidation failed")
            return real_revalidate()

        monkeypatch.setattr(held, "revalidate", fail_last_borrow_exit)
        with pytest.raises(RuntimeError, match="post-borrow"):
            capture_synthetic_descriptor_tree(held, tmp_path, nonce="1" * 32)
        assert not (tmp_path / f"brain-capture-{'1' * 32}").exists()
    finally:
        held.close()


def test_metadata_identity_change_fails_before_xattr_value_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held, roots = _held(tmp_path)
    try:
        (roots["owner"] / ".env.local").write_bytes(b"credential")
        (roots["owner"] / "public").write_bytes(b"public")
        credential_inode = (roots["owner"] / ".env.local").stat().st_ino
        real_open_child = capture_module._open_child
        real_get_xattr = capture_module._get_xattr
        read_attempts = 0

        def swap_public_for_credential(
            parent_fd: int, name: str, *, directory: bool = False
        ) -> int:
            if name == "public" and not directory:
                os.unlink(roots["owner"] / "public")
                os.link(roots["owner"] / ".env.local", roots["owner"] / "public")
            return real_open_child(parent_fd, name, directory=directory)

        def count_value_reads(fd: int, name: bytes) -> bytes:
            nonlocal read_attempts
            if os.fstat(fd).st_ino == credential_inode:
                read_attempts += 1
            return real_get_xattr(fd, name)

        monkeypatch.setattr(
            "tools.brain_host_tree_capture_v4._open_child", swap_public_for_credential
        )
        monkeypatch.setattr("tools.brain_host_tree_capture_v4._get_xattr", count_value_reads)
        with pytest.raises(TreeCaptureError, match="source_identity_changed_before_metadata"):
            capture_synthetic_descriptor_tree(held, tmp_path, nonce="2" * 32)
        assert read_attempts == 0
        assert not (tmp_path / f"brain-capture-{'2' * 32}").exists()
    finally:
        held.close()


def test_final_output_roster_mismatch_removes_nonce_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held, roots = _held(tmp_path)
    try:
        (roots["owner"] / "item").write_bytes(b"payload")
        real_write = capture_module._write_private_file

        def add_unlisted_output(directory: Path, name: str, payload: bytes) -> None:
            real_write(directory, name, payload)
            if name == "receipt.json":
                (directory / "rogue").write_bytes(b"unexpected")

        monkeypatch.setattr(capture_module, "_write_private_file", add_unlisted_output)
        with pytest.raises(TreeCaptureError, match="artifact_member_roster_mismatch"):
            capture_synthetic_descriptor_tree(held, tmp_path, nonce="3" * 32)
        assert not (tmp_path / f"brain-capture-{'3' * 32}").exists()
    finally:
        held.close()
