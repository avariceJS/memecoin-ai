from __future__ import annotations

import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from tools.brain_host_held_data_t_v1 import (
    MNT_RDONLY,
    REQUIRED_ROOT_ROLES,
    ROOT_KINDS,
    ApprovedDataPlan,
    HeldDataError,
    HeldDataT,
    RootDeclaration,
    approved_data_plan_sha256,
)

UUID = "430740ca-42e7-4e6c-a07a-e4e7370152cc"
GROUP = "b72c52b0-591e-4c21-b4d2-87e6d7679c3a"
SYSTEM_UUID = "11111111-1111-4111-8111-111111111111"
PREBOOT_UUID = "22222222-2222-4222-8222-222222222222"
SNAPSHOT_UUID = "33333333-3333-4333-8333-333333333333"


class FakeDataOS:
    def __init__(self, mountpoint: Path, plan: ApprovedDataPlan) -> None:
        self.mountpoint = str(mountpoint)
        self.plan = plan
        self.flags = MNT_RDONLY
        self.fsid = (41, 97)
        self.source = f"/dev/{plan.data_device_identifier}"
        self.fs_type = "apfs"
        self.mismatch: str | None = None
        self.target_mismatches: dict[str, str] = {}
        self.bad_fds: set[tuple[int, int]] = set()
        self.fail_fds: set[tuple[int, int]] = set()
        self.extra_mounts: list[dict[str, object]] = []
        self.inventory_override: list[dict[str, object]] | None = None
        self.omit_inventory_group = False
        self.inventory_resolution_started = False
        self.disk_info_overrides: dict[str, dict[str, object]] = {}
        self.disk_info_calls: list[str] = []
        self.disk_info_results: list[tuple[str, dict[str, object]]] = []

    def _info(self, target: str) -> dict[str, object]:
        volume_uuid = self.plan.data_volume_uuid
        device = self.plan.data_device_identifier
        container = self.plan.data_container_reference
        group = self.plan.data_volume_group_id
        if target in self.target_mismatches:
            volume_uuid = self.target_mismatches[target]
        row: dict[str, object] = {
            "FilesystemType": "apfs",
            "VolumeUUID": volume_uuid,
            "DeviceIdentifier": device,
            "APFSContainerReference": container,
            "APFSVolumeGroupID": group,
            "APFSSnapshot": False,
            "MountPoint": self.mountpoint if target.startswith("/dev/") else target,
        }
        if self.mismatch:
            key, value = self.mismatch.split("=", 1)
            row[key] = value
        return row

    def disk_info(self, target: str) -> dict[str, object]:
        self.disk_info_calls.append(target)
        row = self._info(target)
        if not target.startswith("/dev/") or self.inventory_resolution_started:
            row.update(self.disk_info_overrides.get(target, {}))
        self.disk_info_results.append((target, dict(row)))
        return row

    def apfs_volumes(self, container: str) -> list[dict[str, object]]:
        if self.inventory_override is not None:
            if any(not row.get("APFSVolumeGroupID") for row in self.inventory_override):
                self.inventory_resolution_started = True
            return self.inventory_override
        if self.omit_inventory_group:
            self.inventory_resolution_started = True
        row: dict[str, object] = {
            "VolumeUUID": self.plan.data_volume_uuid,
            "DeviceIdentifier": self.plan.data_device_identifier,
            "APFSContainerReference": container,
        }
        if not self.omit_inventory_group:
            row["APFSVolumeGroupID"] = self.plan.data_volume_group_id
        return [row]

    def mount_entries(self) -> list[dict[str, object]]:
        return [
            {
                "fs_type": "apfs",
                "flags": self.flags,
                "fsid": self.fsid,
                "mountpoint": self.mountpoint,
                "source": self.source,
            },
            *self.extra_mounts,
        ]

    def filesystem(self, fd: int) -> dict[str, object]:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) in self.fail_fds:
            raise OSError("synthetic fstatfs/readback failure")
        changed = (st.st_dev, st.st_ino) in self.bad_fds
        return {
            "fs_type": self.fs_type,
            "flags": 0 if changed else self.flags,
            "fsid": (999, 999) if changed else self.fsid,
            "mountpoint": self.mountpoint,
            "source": self.source,
        }


def _plan() -> ApprovedDataPlan:
    roster = {
        role: _present_root(role, f"roots/{role}")
        for role in sorted(REQUIRED_ROOT_ROLES)
        if role != "verifier_worktree"
    }
    roster["verifier_worktree"] = RootDeclaration(
        present=False,
        unresolved_reason="verifier_worktree_absent_not_recreated",
    )
    return ApprovedDataPlan(
        approval_id="synthetic-held-data-plan-v1",
        data_volume_uuid=UUID,
        data_device_identifier="disk3s5",
        data_container_reference="disk3",
        data_volume_group_id=GROUP,
        root_roster=roster,
    )


def _make_roots(mountpoint: Path, plan: ApprovedDataPlan) -> None:
    for declaration in plan.root_roster.values():
        if declaration.present:
            assert declaration.relative_locator is not None
            path = mountpoint / declaration.relative_locator
            if declaration.kind == "directory":
                path.mkdir(parents=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"synthetic fixture only")


def _present_root(role: str, relative_locator: str) -> RootDeclaration:
    return RootDeclaration(
        present=True,
        relative_locator=relative_locator,
        kind=ROOT_KINDS[role],
    )


def _fixture(tmp_path: Path):
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan()
    _make_roots(mountpoint, plan)
    api = FakeDataOS(mountpoint, plan)
    held = HeldDataT(
        plan,
        mountpoint,
        trusted_plan_sha256=approved_data_plan_sha256(plan),
        os_api=api,
        synthetic=True,
    )
    return mountpoint, plan, api, held


def _construct(mountpoint: Path, plan: ApprovedDataPlan, api: FakeDataOS) -> HeldDataT:
    return HeldDataT(
        plan,
        mountpoint,
        trusted_plan_sha256=approved_data_plan_sha256(plan),
        os_api=api,
        synthetic=True,
    )


def test_reports_only_held_data_identity_and_never_qualifies_t(tmp_path: Path) -> None:
    _mount, _plan, _api, held = _fixture(tmp_path)
    try:
        report = held.result()
        assert report["classification"] == "held_readonly_Data_identity_verified"
        assert report["mock_backend"] is True
        assert (
            report["identity_uniqueness_scope"]
            == "pinned_container_and_current_kernel_mount_table"
        )
        assert report["global_apfs_uuid_uniqueness_verified"] is False
        assert report["held_root_count"] == len(REQUIRED_ROOT_ROLES) - 1
        assert report["root_role_roster_schema_validated"] is True
        assert report["required_role_closure"] is False
        assert (
            report["unresolved_required_roles"]["verifier_worktree"]["state"]
            == "absent_unresolved"
        )
        assert report["T_qualified"] is False
        assert report["source_cutoff_verified"] is False
        assert report["erase_ready"] is False
        assert report["clean_shutdown_verified"] is False
        assert report["prior_writable_mount_history_verified"] is False
        assert all(
            row["firmlink_provenance"] == "not_generically_attestable_unknown_if_material"
            for row in report["root_locators"].values()
        )
    finally:
        held.close()


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("VolumeUUID", "00000000-0000-0000-0000-000000000001", "pinned_data_volume_mismatch"),
        (
            "APFSVolumeGroupID",
            "00000000-0000-0000-0000-000000000001",
            "pinned_data_volume_mismatch",
        ),
        ("APFSContainerReference", "disk8", "pinned_data_volume_mismatch"),
        ("DeviceIdentifier", "disk8s1", "pinned_data_volume_mismatch"),
    ],
)
def test_rejects_wrong_data_uuid_group_container_or_device(
    tmp_path: Path, field: str, value: str, error: str
) -> None:
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = ApprovedDataPlan(
        "synthetic-plan", UUID, "disk3s5", "disk3", GROUP,
        {
            role: _present_root(role, f"roots/{role}")
            for role in sorted(REQUIRED_ROOT_ROLES)
            if role != "verifier_worktree"
        }
        | {
            "verifier_worktree": RootDeclaration(
                present=False,
                unresolved_reason="verifier_worktree_absent_not_recreated",
            )
        },
    )
    for declaration in plan.root_roster.values():
        if declaration.present:
            assert declaration.relative_locator is not None
            (mountpoint / declaration.relative_locator).mkdir(parents=True)
    api = FakeDataOS(mountpoint, plan)
    api.mismatch = f"{field}={value}"
    with pytest.raises(HeldDataError, match=error):
        _construct(mountpoint, plan, api)


def test_rejects_writable_or_remounted_data_view(tmp_path: Path) -> None:
    _mount, _active_plan, api, held = _fixture(tmp_path)
    try:
        api.flags = 0
        with pytest.raises(HeldDataError, match="mounted_data_changed_or_writable"):
            held.revalidate()
    finally:
        held.close()

    mountpoint = tmp_path / "another-held-data"
    mountpoint.mkdir()
    plan = _plan()
    _make_roots(mountpoint, plan)
    api = FakeDataOS(mountpoint, plan)
    held = _construct(mountpoint, plan, api)
    try:
        api.fsid = (999, 1000)
        with pytest.raises(HeldDataError, match="mounted_data_changed_or_writable"):
            held.revalidate()
    finally:
        held.close()


def test_rejects_replaced_mount_and_stale_root_descriptor(tmp_path: Path) -> None:
    mountpoint, _plan, api, held = _fixture(tmp_path)
    replacement = tmp_path / "held-data-old"
    try:
        mountpoint.rename(replacement)
        mountpoint.mkdir()
        with pytest.raises(HeldDataError, match="mount_path_replaced"):
            held.revalidate()
    finally:
        held.close()

    (tmp_path / "root-case").mkdir()
    mountpoint, _plan, _api, held = _fixture(tmp_path / "root-case")
    role = "research_checkout"
    try:
        root = mountpoint / held._root_parts[role][0] / held._root_parts[role][1]
        root.rename(root.with_name("research_checkout-old"))
        root.mkdir()
        with pytest.raises(HeldDataError, match="declared_root_replaced"):
            held.revalidate()
    finally:
        held.close()


def test_rejects_incomplete_roster_aliases_symlinks_and_cross_mount(tmp_path: Path) -> None:
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan()
    _make_roots(mountpoint, plan)
    api = FakeDataOS(mountpoint, plan)

    incomplete = replace(
        plan,
        root_roster={k: v for k, v in plan.root_roster.items() if k != "verifier_worktree"},
    )
    with pytest.raises(HeldDataError, match="declared_root_roster_incomplete_or_unexpected"):
        _construct(mountpoint, incomplete, api)

    alias = dict(plan.root_roster)
    alias["research_checkout"] = _present_root(
        "research_checkout", alias["owner_checkout"].relative_locator
    )
    with pytest.raises(HeldDataError, match="declared_root_alias_ambiguous"):
        _construct(mountpoint, replace(plan, root_roster=alias), api)

    ancestry = dict(plan.root_roster)
    ancestry["owner_checkout"] = _present_root("owner_checkout", "roots/Project")
    ancestry["research_checkout"] = _present_root(
        "research_checkout", "roots/project/research"
    )
    with pytest.raises(HeldDataError, match="declared_roots_overlap"):
        _construct(mountpoint, replace(plan, root_roster=ancestry), api)

    symlink = mountpoint / "roots" / "symlink-root"
    symlink.symlink_to(mountpoint / "roots" / "owner_checkout", target_is_directory=True)
    escaped = dict(plan.root_roster)
    escaped["owner_checkout"] = _present_root("owner_checkout", "roots/symlink-root")
    with pytest.raises(HeldDataError, match="declared_root_symlink_rejected"):
        _construct(mountpoint, replace(plan, root_roster=escaped), api)

    escaped["owner_checkout"] = _present_root("owner_checkout", "../outside")
    with pytest.raises(HeldDataError, match="root_locator_invalid"):
        _construct(mountpoint, replace(plan, root_roster=escaped), api)
    escaped["owner_checkout"] = _present_root("owner_checkout", ".")
    with pytest.raises(HeldDataError, match="root_locator_invalid"):
        _construct(mountpoint, replace(plan, root_roster=escaped), api)

    held = _construct(mountpoint, plan, api)
    try:
        target = mountpoint / plan.root_roster["owner_checkout"].relative_locator
        st = target.stat()
        api.bad_fds.add((st.st_dev, st.st_ino))
        with pytest.raises(HeldDataError, match="root_crosses_mount_or_device_boundary"):
            held.revalidate()
    finally:
        held.close()


def test_rejects_shared_component_inode_across_lexically_distinct_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mountpoint, plan, api, held = _fixture(tmp_path)
    held.close()
    roster = dict(plan.root_roster)
    owner_locator = "roots/ProjectA/owner"
    research_locator = "roots/ProjectB/research"
    roster["owner_checkout"] = _present_root("owner_checkout", owner_locator)
    roster["research_checkout"] = _present_root("research_checkout", research_locator)
    plan = replace(plan, root_roster=roster)
    (mountpoint / owner_locator).mkdir(parents=True)
    (mountpoint / research_locator).mkdir(parents=True)

    original_open_locator = HeldDataT._open_locator
    owner_component_identity: tuple[int, int] | None = None

    def open_with_casefold_alias(self, parts: tuple[str, ...], expected_kind: str):
        nonlocal owner_component_identity
        fd, chain = original_open_locator(self, parts, expected_kind)
        if parts == tuple(owner_locator.split("/")):
            owner_component_identity = chain[1]
        elif parts == tuple(research_locator.split("/")):
            assert owner_component_identity is not None
            aliased_chain = list(chain)
            # Synthetic kernel identity: ProjectB resolves to the same held
            # component inode as ProjectA, as on a case/Unicode aliasing FS.
            aliased_chain[1] = owner_component_identity
            chain = tuple(aliased_chain)
        return fd, chain

    monkeypatch.setattr(HeldDataT, "_open_locator", open_with_casefold_alias)
    with pytest.raises(HeldDataError, match="declared_root_alias_ambiguous"):
        _construct(mountpoint, plan, api)


def test_rejects_unreadable_root_and_bad_external_plan_pin(tmp_path: Path) -> None:
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan()
    _make_roots(mountpoint, plan)
    api = FakeDataOS(mountpoint, plan)
    _construct(mountpoint, plan, api).close()
    with pytest.raises(HeldDataError, match="approved_data_plan_pin_mismatch"):
        HeldDataT(plan, mountpoint, trusted_plan_sha256="0" * 64, os_api=api, synthetic=True)


def test_fails_closed_on_root_filesystem_readback_error(tmp_path: Path) -> None:
    mountpoint, _plan, api, held = _fixture(tmp_path)
    try:
        path = mountpoint.joinpath(*held._root_parts["owner_checkout"])
        st = path.stat()
        api.fail_fds.add((st.st_dev, st.st_ino))
        with pytest.raises(HeldDataError, match="declared_root_open_failed"):
            held.revalidate()
    finally:
        held.close()


def test_absent_role_requires_explicit_reason_and_verifier_stays_absent(
    tmp_path: Path,
) -> None:
    mountpoint, plan, _api, held = _fixture(tmp_path)
    try:
        absent = plan.root_roster["verifier_worktree"]
        assert absent.present is False
        assert absent.relative_locator is None
        assert not (mountpoint / "roots" / "verifier_worktree").exists()
        malformed = dict(plan.root_roster)
        malformed["owner_codex_worktree"] = RootDeclaration(present=False)
        bad_plan = replace(plan, root_roster=malformed)
        with pytest.raises(HeldDataError, match="absent_root_requires_unresolved_reason"):
            _construct(mountpoint, bad_plan, _api)
        malformed["owner_codex_worktree"] = plan.root_roster["owner_codex_worktree"]
        malformed["verifier_worktree"] = RootDeclaration(
            present=True,
            relative_locator="roots/verifier_worktree",
            kind="directory",
        )
        with pytest.raises(
            HeldDataError, match="verifier_worktree_must_remain_absent_unresolved"
        ):
            _construct(mountpoint, replace(plan, root_roster=malformed), _api)
    finally:
        held.close()


def test_enumerates_data_mount_aliases_and_fails_on_writable_or_unresolved_alias(
    tmp_path: Path,
) -> None:
    mountpoint, _plan_value, api, held = _fixture(tmp_path)
    alias_path = str(tmp_path / "readonly-data-alias")
    try:
        api.extra_mounts = [
            {
                "fs_type": "apfs",
                "flags": MNT_RDONLY,
                "fsid": (41, 97),
                "mountpoint": alias_path,
                "source": api.source,
            }
        ]
        with pytest.raises(HeldDataError, match="container_mount_alias_set_changed"):
            held.revalidate()
    finally:
        held.close()

    (tmp_path / "readonly-case").mkdir()
    mountpoint, plan, api, held = _fixture(tmp_path / "readonly-case")
    held.close()
    api.extra_mounts = [
        {
            "fs_type": "apfs",
            "flags": MNT_RDONLY,
            "fsid": (41, 97),
            "mountpoint": alias_path,
            "source": api.source,
        }
    ]
    try:
        held = _construct(mountpoint, plan, api)
        report = held.result()
        assert report["data"]["readonly_alias_mountpoints"] == [alias_path]
    finally:
        held.close()

    (tmp_path / "writable-case").mkdir()
    mountpoint, plan, api, held = _fixture(tmp_path / "writable-case")
    held.close()
    api.extra_mounts = [
        {
            "fs_type": "apfs",
            "flags": 0,
            "fsid": (41, 97),
            "mountpoint": alias_path,
            "source": api.source,
        }
    ]
    with pytest.raises(HeldDataError, match="writable_data_alias_detected"):
        _construct(mountpoint, plan, api)

    (tmp_path / "unresolved-case").mkdir()
    mountpoint, plan, api, held = _fixture(tmp_path / "unresolved-case")
    held.close()
    api.extra_mounts = [
        {
            "fs_type": "apfs",
            "flags": MNT_RDONLY,
            "fsid": (42, 98),
            "mountpoint": alias_path,
            "source": "/dev/disk3s7",
        }
    ]
    api.target_mismatches[alias_path] = "00000000-0000-0000-0000-000000000001"
    with pytest.raises(HeldDataError, match="kernel_mount_source_unbound"):
        _construct(mountpoint, plan, api)


def test_rejects_duplicate_or_malformed_apfs_volume_inventory(tmp_path: Path) -> None:
    mountpoint, plan, api, held = _fixture(tmp_path)
    held.close()
    row = api.apfs_volumes(plan.data_container_reference)[0]
    api.inventory_override = [row, {**row, "DeviceIdentifier": "disk3s6"}]
    with pytest.raises(HeldDataError, match="apfs_volume_uuid_alias_ambiguous"):
        _construct(mountpoint, plan, api)

    api.inventory_override = [{**row, "DeviceIdentifier": "unparseable-device"}]
    with pytest.raises(HeldDataError, match="apfs_volume_inventory_unresolved"):
        _construct(mountpoint, plan, api)


def test_mixed_kind_roster_holds_files_and_rejects_file_replacement_or_cross_mount(
    tmp_path: Path,
) -> None:
    mountpoint, plan, api, held = _fixture(tmp_path)
    file_roles = {
        role for role, kind in ROOT_KINDS.items() if kind == "file" and role != "verifier_worktree"
    }
    try:
        assert file_roles == {
            "helius_mcp_wrapper",
            "codex_mcp_config",
            "cursor_mcp_config",
        }
        for role in file_roles:
            fd = held.root_fds[role]
            assert stat.S_ISREG(os.fstat(fd).st_mode)
            assert (mountpoint / plan.root_roster[role].relative_locator).is_file()

        path = mountpoint / plan.root_roster["helius_mcp_wrapper"].relative_locator
        old = path.with_name("helius-wrapper-old")
        path.rename(old)
        path.write_bytes(b"replacement synthetic file")
        with pytest.raises(HeldDataError, match="declared_root_replaced"):
            held.revalidate()
    finally:
        held.close()

    (tmp_path / "cross-file").mkdir()
    mountpoint, plan, api, held = _fixture(tmp_path / "cross-file")
    try:
        path = mountpoint / plan.root_roster["codex_mcp_config"].relative_locator
        st = path.stat()
        api.bad_fds.add((st.st_dev, st.st_ino))
        with pytest.raises(HeldDataError, match="root_crosses_mount_or_device_boundary"):
            held.revalidate()
    finally:
        held.close()


def test_rejects_file_kind_mismatch_and_symlink_leaf(tmp_path: Path) -> None:
    mountpoint, plan, api, held = _fixture(tmp_path)
    held.close()
    file_path = mountpoint / plan.root_roster["helius_mcp_wrapper"].relative_locator
    file_path.unlink()
    file_path.mkdir()
    with pytest.raises(HeldDataError, match="declared_root_kind_mismatch"):
        _construct(mountpoint, plan, api)

    file_path.rmdir()
    external_file = tmp_path / "external-file"
    external_file.write_bytes(b"synthetic target")
    file_path.symlink_to(external_file)
    with pytest.raises(HeldDataError, match="declared_root_symlink_rejected"):
        _construct(mountpoint, plan, api)

    bad_roster = dict(plan.root_roster)
    bad_roster["helius_mcp_wrapper"] = RootDeclaration(
        present=True,
        relative_locator="roots/helius_mcp_wrapper",
        kind="directory",
    )
    with pytest.raises(HeldDataError, match="present_root_declaration_invalid"):
        _construct(mountpoint, replace(plan, root_roster=bad_roster), api)


def test_resolves_missing_inventory_group_from_pinned_diskutil_identity(
    tmp_path: Path,
) -> None:
    mountpoint, plan, api, held = _fixture(tmp_path)
    held.close()
    api.omit_inventory_group = True
    held = _construct(mountpoint, plan, api)
    try:
        assert held.result()["classification"] == "held_readonly_Data_identity_verified"
    finally:
        held.close()

    (tmp_path / "mismatch").mkdir()
    mountpoint, plan, api, held = _fixture(tmp_path / "mismatch")
    held.close()
    api.omit_inventory_group = True
    api.disk_info_overrides[f"/dev/{plan.data_device_identifier}"] = {
        "APFSVolumeGroupID": "00000000-0000-0000-0000-000000000001"
    }
    with pytest.raises(HeldDataError, match="apfs_pinned_volume_identity_mismatch"):
        _construct(mountpoint, plan, api)


def test_resolves_system_snapshot_to_distinct_parent_and_rejects_data_snapshot_alias(
    tmp_path: Path,
) -> None:
    mountpoint, plan, api, held = _fixture(tmp_path)
    held.close()
    api.inventory_override = [
        {
            "VolumeUUID": UUID,
            "DeviceIdentifier": "disk3s5",
            "APFSContainerReference": "disk3",
            "APFSVolumeGroupID": GROUP,
        },
        {
            "VolumeUUID": SYSTEM_UUID,
            "DeviceIdentifier": "disk3s1",
            "APFSContainerReference": "disk3",
        },
        {
            "VolumeUUID": PREBOOT_UUID,
            "DeviceIdentifier": "disk3s4",
            "APFSContainerReference": "disk3",
        },
    ]
    api.disk_info_overrides["/dev/disk3s1"] = {
        "DeviceIdentifier": "disk3s1",
        "VolumeUUID": SYSTEM_UUID,
        "APFSContainerReference": "disk3",
        "APFSVolumeGroupID": GROUP,
    }
    api.disk_info_overrides["/dev/disk3s4"] = {
        "DeviceIdentifier": "disk3s4",
        "VolumeUUID": PREBOOT_UUID,
        "APFSContainerReference": "disk3",
        "APFSVolumeGroupID": None,
    }
    system_snapshot_path = str(tmp_path / "system-snapshot")
    api.extra_mounts = [
        {
            "fs_type": "apfs",
            "flags": MNT_RDONLY,
            "fsid": (88, 99),
            "mountpoint": system_snapshot_path,
            "source": "/dev/disk3s1s1",
        }
    ]
    api.disk_info_overrides[system_snapshot_path] = {
        "DeviceIdentifier": "disk3s1s1",
        "VolumeUUID": SNAPSHOT_UUID,
        "APFSSnapshotUUID": SNAPSHOT_UUID,
        "APFSSnapshot": True,
        "APFSVolumeGroupID": GROUP,
    }
    preboot_path = str(tmp_path / "preboot-volume")
    api.extra_mounts.append(
        {
            "fs_type": "apfs",
            "flags": MNT_RDONLY,
            "fsid": (90, 101),
            "mountpoint": preboot_path,
            "source": "/dev/disk3s4",
        }
    )
    api.disk_info_overrides[preboot_path] = {
        "DeviceIdentifier": "disk3s4",
        "VolumeUUID": PREBOOT_UUID,
        "APFSVolumeGroupID": None,
    }
    held = _construct(mountpoint, plan, api)
    try:
        assert held.result()["data"]["readonly_alias_mountpoints"] == []
        assert "/dev/disk3s1" in api.disk_info_calls
        assert "/dev/disk3s4" in api.disk_info_calls
        resolved_rows = {
            target: row
            for target, row in api.disk_info_results
            if target in {"/dev/disk3s1", "/dev/disk3s4"}
        }
        assert resolved_rows["/dev/disk3s1"]["VolumeUUID"] == SYSTEM_UUID
        assert resolved_rows["/dev/disk3s1"]["APFSVolumeGroupID"] == GROUP
        assert resolved_rows["/dev/disk3s4"]["VolumeUUID"] == PREBOOT_UUID
        assert resolved_rows["/dev/disk3s4"]["APFSVolumeGroupID"] is None
    finally:
        held.close()

    api.disk_info_overrides[system_snapshot_path]["APFSSnapshotUUID"] = SYSTEM_UUID
    with pytest.raises(HeldDataError, match="snapshot_identity_mismatch"):
        _construct(mountpoint, plan, api)

    data_snapshot_path = str(tmp_path / "data-snapshot")
    api.extra_mounts = [
        {
            "fs_type": "apfs",
            "flags": MNT_RDONLY,
            "fsid": (89, 99),
            "mountpoint": data_snapshot_path,
            "source": "/dev/disk3s5s1",
        }
    ]
    api.disk_info_overrides[data_snapshot_path] = {
        "DeviceIdentifier": "disk3s5s1",
        "VolumeUUID": SNAPSHOT_UUID,
        "APFSSnapshotUUID": SNAPSHOT_UUID,
        "APFSSnapshot": True,
        "APFSVolumeGroupID": GROUP,
    }
    with pytest.raises(HeldDataError, match="data_snapshot_alias_detected"):
        _construct(mountpoint, plan, api)


@pytest.mark.parametrize(
    "source,mountpoint,device,container,fsid",
    [
        ("/dev/disk3s5", "foreign-container", "disk9s5", "disk9", (42, 97)),
        ("/dev/disk9s5", "same-fsid-foreign", "disk9s5", "disk9", (41, 97)),
    ],
)
def test_data_source_or_fsid_hint_cannot_map_to_another_volume(
    tmp_path: Path,
    source: str,
    mountpoint: str,
    device: str,
    container: str,
    fsid: tuple[int, int],
) -> None:
    mountpoint_path, plan, api, held = _fixture(tmp_path)
    held.close()
    alias_path = str(tmp_path / mountpoint)
    api.extra_mounts = [
        {
            "fs_type": "apfs",
            "flags": MNT_RDONLY,
            "fsid": fsid,
            "mountpoint": alias_path,
            "source": source,
        }
    ]
    api.disk_info_overrides[alias_path] = {
        "DeviceIdentifier": device,
        "APFSContainerReference": container,
        "VolumeUUID": SYSTEM_UUID,
        "APFSVolumeGroupID": GROUP,
    }
    with pytest.raises(HeldDataError, match="data_alias_identity_mismatch"):
        _construct(mountpoint_path, plan, api)
