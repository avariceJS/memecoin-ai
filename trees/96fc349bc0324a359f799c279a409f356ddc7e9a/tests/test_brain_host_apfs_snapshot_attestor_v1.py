from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tools.brain_host_apfs_snapshot_attestor_v1 import (
    MNT_RDONLY,
    ApprovedHostPlan,
    HeldSnapshotT,
    SnapshotAttestationError,
    approved_host_plan_sha256,
    attest_mounted_snapshot,
)

DATA_UUID = "11111111-2222-3333-4444-555555555555"
GROUP_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SNAPSHOT_UUID = "99999999-8888-7777-6666-555555555555"


class FakeSnapshotOS:
    def __init__(self, mountpoint: Path) -> None:
        self.mountpoint = mountpoint
        self.base_info: dict[str, Any] = {
            "FilesystemType": "apfs",
            "VolumeUUID": DATA_UUID,
            "DeviceIdentifier": "disk3s5",
            "APFSContainerReference": "disk3",
            "APFSVolumeGroupID": GROUP_ID,
            "APFSSnapshot": False,
        }
        self.mounted_info: dict[str, Any] = {
            "FilesystemType": "apfs",
            "APFSSnapshot": True,
            "APFSSnapshotUUID": SNAPSHOT_UUID,
            "VolumeUUID": SNAPSHOT_UUID,
            "APFSVolumeGroupID": GROUP_ID,
            "MountPoint": str(mountpoint),
        }
        self.snapshot_rows: list[dict[str, Any]] = [
            {
                "SnapshotUUID": SNAPSHOT_UUID,
                "SnapshotXID": 420,
                "SnapshotName": "fixture-snapshot",
            }
        ]
        self.fs_type = "apfs"
        self.flags = MNT_RDONLY
        self.source = "fixture-snapshot@/dev/disk3s5"
        self.fsid = (71, 72)
        self.nested_inode: int | None = None

    def disk_info(self, target: str) -> dict[str, Any]:
        if target == "/dev/disk3s5":
            return dict(self.base_info)
        return dict(self.mounted_info)

    def snapshots(self, target: str) -> list[dict[str, Any]]:
        assert target == "/dev/disk3s5"
        return [dict(row) for row in self.snapshot_rows]

    def filesystem(self, fd: int) -> dict[str, Any]:
        mountpoint = str(self.mountpoint)
        if self.nested_inode == __import__("os").fstat(fd).st_ino:
            return {
                "fs_type": "apfs",
                "flags": MNT_RDONLY,
                "fsid": (4, 5),
                "mountpoint": "/nested-mount",
                "source": self.source,
            }
        return {
            "fs_type": self.fs_type,
            "flags": self.flags,
            "fsid": self.fsid,
            "mountpoint": mountpoint,
            "source": self.source,
        }


def _fixture(tmp_path: Path) -> tuple[ApprovedHostPlan, Path, FakeSnapshotOS]:
    mountpoint = tmp_path / "snapshot-mount"
    mountpoint.mkdir()
    for name in ("owner", "research", "private"):
        (mountpoint / name).mkdir()
    plan = ApprovedHostPlan(
        approval_id="host-plan-review-2026-10-fixture",
        data_volume_uuid=DATA_UUID,
        data_device_identifier="disk3s5",
        data_container_reference="disk3",
        data_volume_group_id=GROUP_ID,
        snapshot_uuid=SNAPSHOT_UUID,
        snapshot_xid=420,
        root_locators={
            "owner": "owner",
            "research": "research",
            "private": "private",
        },
    )
    return plan, mountpoint, FakeSnapshotOS(mountpoint)


def _held(plan: ApprovedHostPlan, mountpoint: Path, api: FakeSnapshotOS) -> HeldSnapshotT:
    return HeldSnapshotT(
        plan,
        mountpoint,
        os_api=api,
        synthetic=True,
        trusted_plan_sha256=approved_host_plan_sha256(plan),
    )


def test_attests_only_bounded_synthetic_snapshot_contract(tmp_path: Path) -> None:
    plan, mountpoint, api = _fixture(tmp_path)
    result = attest_mounted_snapshot(
        plan,
        mountpoint,
        os_api=api,
        trusted_plan_sha256=approved_host_plan_sha256(plan),
    )
    assert result["schema"] == "brain-host-apfs-snapshot-attestation-v1"
    assert result["classification"] == "synthetic_snapshot_contract_only"
    assert result["synthetic"] is True
    assert result["snapshot"]["snapshot_uuid"] == SNAPSHOT_UUID
    assert result["snapshot"]["snapshot_xid"] == 420
    assert result["held_root_count"] == 3
    assert result["root_completeness"] == "unproven"
    assert result["capture_complete"] is False
    assert result["erase_ready"] is False


def test_normalizes_optional_plist_creation_time_for_json(tmp_path: Path) -> None:
    plan, mountpoint, api = _fixture(tmp_path)
    api.mounted_info["APFSSnapshotCreateTime"] = datetime(2026, 10, 14, 4, 30, 0)
    result = attest_mounted_snapshot(
        plan,
        mountpoint,
        os_api=api,
        trusted_plan_sha256=approved_host_plan_sha256(plan),
    )
    assert result["snapshot"]["snapshot_created_at"] == "2026-10-14T04:30:00Z"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("wrong_data_uuid", "pinned_data_volume_mismatch"),
        ("system_group", "mounted_snapshot_identity_mismatch"),
        ("not_snapshot", "mounted_snapshot_identity_mismatch"),
        ("not_apfs", "mounted_filesystem_not_apfs"),
        ("writable", "mounted_filesystem_not_readonly"),
        ("wrong_source", "snapshot_mount_source_unbound"),
        ("xid_missing", "snapshot_xid_missing"),
        ("xid_boolean", "snapshot_xid_missing"),
        ("snapshot_absent", "snapshot_not_unique_on_pinned_data_volume"),
        ("snapshot_duplicate", "snapshot_not_unique_on_pinned_data_volume"),
    ],
)
def test_rejects_os_identity_mismatches(tmp_path: Path, mutation: str, message: str) -> None:
    plan, mountpoint, api = _fixture(tmp_path)
    if mutation == "wrong_data_uuid":
        api.base_info["VolumeUUID"] = "00000000-0000-0000-0000-000000000000"
    elif mutation == "system_group":
        api.mounted_info["APFSVolumeGroupID"] = "00000000-0000-0000-0000-000000000000"
    elif mutation == "not_snapshot":
        api.mounted_info["APFSSnapshot"] = False
    elif mutation == "not_apfs":
        api.fs_type = "hfs"
    elif mutation == "writable":
        api.flags = 0
    elif mutation == "wrong_source":
        api.source = "disk-other@/dev/disk8s2"
    elif mutation == "xid_missing":
        api.snapshot_rows[0]["SnapshotXID"] = None
    elif mutation == "xid_boolean":
        api.snapshot_rows[0]["SnapshotXID"] = True
    elif mutation == "snapshot_absent":
        api.snapshot_rows.clear()
    elif mutation == "snapshot_duplicate":
        api.snapshot_rows.append(dict(api.snapshot_rows[0]))
    with pytest.raises(SnapshotAttestationError, match=message):
        _held(plan, mountpoint, api)


def test_plan_requires_exact_three_nonoverlapping_roots(tmp_path: Path) -> None:
    plan, mountpoint, api = _fixture(tmp_path)
    plan = ApprovedHostPlan(
        **{
            **plan.__dict__,
            "root_locators": {"a": "owner", "b": "owner/child", "c": "private"},
        }
    )
    with pytest.raises(SnapshotAttestationError, match="host_plan_roots_overlap"):
        _held(plan, mountpoint, api)


def test_rejects_nested_mount_and_replaced_root(tmp_path: Path) -> None:
    plan, mountpoint, api = _fixture(tmp_path)
    api.nested_inode = (mountpoint / "research").stat().st_ino
    with pytest.raises(SnapshotAttestationError, match="root_crosses_mount_boundary"):
        _held(plan, mountpoint, api)

    api.nested_inode = None
    held = _held(plan, mountpoint, api)
    try:
        detached = mountpoint / "owner-detached"
        (mountpoint / "owner").rename(detached)
        (mountpoint / "owner").mkdir()
        with pytest.raises(SnapshotAttestationError, match="root_path_replaced"):
            held.revalidate()
    finally:
        held.close()


def test_revalidation_rechecks_snapshot_and_data_frontier(tmp_path: Path) -> None:
    plan, mountpoint, api = _fixture(tmp_path)
    held = _held(plan, mountpoint, api)
    try:
        api.snapshot_rows[0]["SnapshotXID"] = 421
        with pytest.raises(SnapshotAttestationError, match="snapshot_identity_changed"):
            held.revalidate()
        api.snapshot_rows[0]["SnapshotXID"] = 420
        api.base_info["VolumeUUID"] = "00000000-0000-0000-0000-000000000000"
        with pytest.raises(SnapshotAttestationError, match="snapshot_identity_changed"):
            held.revalidate()
    finally:
        held.close()


def test_rejects_untrusted_plan_pin_and_borrows_root_fd_safely(tmp_path: Path) -> None:
    plan, mountpoint, api = _fixture(tmp_path)
    with pytest.raises(SnapshotAttestationError, match="approved_host_plan_pin_mismatch"):
        HeldSnapshotT(
            plan,
            mountpoint,
            os_api=api,
            synthetic=True,
            trusted_plan_sha256="0" * 64,
        )

    held = _held(plan, mountpoint, api)
    with held.borrow_root("owner") as borrowed:
        assert os.fstat(borrowed).st_ino == (mountpoint / "owner").stat().st_ino
    held.close()
    with pytest.raises(SnapshotAttestationError, match="snapshot_handle_released"):
        with held.borrow_root("owner"):
            pass


def test_rejects_mount_parent_replacement(tmp_path: Path) -> None:
    plan, mountpoint, api = _fixture(tmp_path)
    held = _held(plan, mountpoint, api)
    original_parent = mountpoint.parent
    detached_parent = original_parent.with_name(f"{original_parent.name}-detached")
    try:
        original_parent.rename(detached_parent)
        original_parent.mkdir()
        (original_parent / mountpoint.name).mkdir()
        with pytest.raises(SnapshotAttestationError, match="mount_parent_replaced"):
            held.revalidate()
    finally:
        held.close()
        if detached_parent.exists():
            if original_parent.exists():
                shutil.rmtree(original_parent)
            detached_parent.rename(original_parent)


def test_synthetic_backend_cannot_be_mislabeled_as_production(tmp_path: Path) -> None:
    plan, mountpoint, api = _fixture(tmp_path)
    with pytest.raises(SnapshotAttestationError, match="synthetic_backend_requires_synthetic_mode"):
        HeldSnapshotT(
            plan,
            mountpoint,
            os_api=api,
            trusted_plan_sha256=approved_host_plan_sha256(plan),
        )
