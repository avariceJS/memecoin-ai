from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import memecoin_research_storage as storage
from memecoin_research_storage import StorageConfig, VolumeInfo, create_root
from tools import brain_host_tree_capture_v5 as capture_v5
from tools import brain_host_tree_custody as v2
from tools.brain_host_held_data_t_v1 import (
    MNT_RDONLY,
    REQUIRED_ROOT_ROLES,
    ROOT_KINDS,
    ApprovedDataPlan,
    HeldDataT,
    RootDeclaration,
    approved_data_plan_sha256,
)
from tools.brain_host_tree_capture_v5 import (
    GIT_OPAQUE_WAIVER,
    RoleCapturePolicy,
    TreeCaptureV5Error,
    capture_candidate_v5,
    extract_candidate_inert_v5,
    inventory_held_data_v5,
    model_candidate_capacity_v5,
    policy_roster_sha256_v5,
    verify_candidate_v5,
)
from tools.brain_host_tree_custody_v3 import POLICY_SCHEMA
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json

UUID = "430740ca-42e7-4e6c-a07a-e4e7370152cc"
GROUP = "b72c52b0-591e-4c21-b4d2-87e6d7679c3a"


def test_capacity_model_bounds_owner_scale_without_allocating_payload() -> None:
    estimate = model_candidate_capacity_v5(819_547, 30_000_000_000, average_path_chars=128)
    assert estimate["conservative_database_bytes_per_row"] == 250
    assert estimate["observed_long_path_database_bytes"] == 249_417_728
    assert estimate["observed_long_path_row_count"] == 1_000_000
    assert estimate["minimum_scratch_reserve_bytes"] < 32 * 1024**3
    assert estimate["metadata_sidecar_reserve_bytes"] == 8 * 1024**3
    assert estimate["minimum_total_reserve_bytes"] == (
        estimate["minimum_scratch_reserve_bytes"] + 8 * 1024**3 + 30_000_000_000
    )


class FakeDataOS:
    def __init__(self, mountpoint: Path, plan: ApprovedDataPlan) -> None:
        self.mountpoint = str(mountpoint)
        self.plan = plan

    def disk_info(self, target: str) -> dict[str, object]:
        return {
            "FilesystemType": "apfs",
            "VolumeUUID": self.plan.data_volume_uuid,
            "DeviceIdentifier": self.plan.data_device_identifier,
            "APFSContainerReference": self.plan.data_container_reference,
            "APFSVolumeGroupID": self.plan.data_volume_group_id,
            "APFSSnapshot": False,
            "MountPoint": self.mountpoint if target.startswith("/dev/") else target,
        }

    def apfs_volumes(self, container: str) -> list[dict[str, object]]:
        return [
            {
                "VolumeUUID": self.plan.data_volume_uuid,
                "DeviceIdentifier": self.plan.data_device_identifier,
                "APFSContainerReference": container,
                "APFSVolumeGroupID": self.plan.data_volume_group_id,
            }
        ]

    def mount_entries(self) -> list[dict[str, object]]:
        return [
            {
                "fs_type": "apfs",
                "flags": MNT_RDONLY,
                "fsid": (41, 97),
                "mountpoint": self.mountpoint,
                "source": f"/dev/{self.plan.data_device_identifier}",
            }
        ]

    def filesystem(self, _fd: int) -> dict[str, object]:
        return {
            "fs_type": "apfs",
            "flags": MNT_RDONLY,
            "fsid": (41, 97),
            "mountpoint": self.mountpoint,
            "source": f"/dev/{self.plan.data_device_identifier}",
        }


def _held_fixture(tmp_path: Path) -> tuple[HeldDataT, dict[str, Path]]:
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    roots: dict[str, Path] = {}
    roster = {}
    for role in sorted(REQUIRED_ROOT_ROLES):
        if role == "verifier_worktree":
            roster[role] = RootDeclaration(
                present=False, unresolved_reason="verifier_worktree_absent_not_recreated"
            )
            continue
        locator = f"roots/{role}"
        path = mountpoint / locator
        if ROOT_KINDS[role] == "directory":
            path.mkdir(parents=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic fixture root")
        roots[role] = path
        roster[role] = RootDeclaration(
            present=True, relative_locator=locator, kind=ROOT_KINDS[role]
        )
    plan = ApprovedDataPlan(
        approval_id="synthetic-v5-held-data-plan",
        data_volume_uuid=UUID,
        data_device_identifier="disk3s5",
        data_container_reference="disk3",
        data_volume_group_id=GROUP,
        root_roster=roster,
    )
    view = HeldDataT(
        plan,
        mountpoint,
        trusted_plan_sha256=approved_data_plan_sha256(plan),
        os_api=FakeDataOS(mountpoint, plan),
        synthetic=True,
    )
    return view, roots


def _policies(tmp_path: Path, roots: dict[str, Path]) -> dict[str, RoleCapturePolicy]:
    policies = {}
    for role in roots:
        value = {
            "schema": POLICY_SCHEMA,
            "policy_id": "synthetic-v5-policy",
            "version": 1,
            "rule_set": "owner-work-sensitive-v1",
            "pem_candidate_dispositions": {},
            "exact_path_exclusions": {},
            "symlink_target_dispositions": {},
            "rebuild_inputs": [],
        }
        path = tmp_path / f"{role}.policy.json"
        raw = canonical_json(value)
        path.write_bytes(raw)
        policies[role] = RoleCapturePolicy(path, hashlib.sha256(raw).hexdigest())
    return policies


def _writer(tmp_path: Path) -> DescriptorWriter:
    mount = tmp_path / "storage-volume"
    root = mount / "BrainData"
    root.mkdir(parents=True, mode=0o700)
    mount_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    guard = SimpleNamespace(
        volume_uuid="11111111-1111-4111-8111-111111111111",
        root_id="synthetic-storage-root",
        bootstrap=False,
        mount_fd=mount_fd,
        config=SimpleNamespace(data_root="BrainData"),
        revalidate=lambda: None,
    )
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd, storage_guard=guard)
    os.close(root_fd)
    writer._test_mount_fd = mount_fd
    return writer


def _inventory(tmp_path: Path, held: HeldDataT, roots: dict[str, Path]):
    writer = _writer(tmp_path)
    result = inventory_held_data_v5(
        held,
        writer,
        role_policies=_policies(tmp_path, roots),
        reserve_capacity=lambda _size: None,
        scratch_reserve_bytes=1024 * 1024,
        payload_reserve_bytes=0,
    )
    return result, writer


def _rows(inventory) -> dict[tuple[str, str], tuple[object, ...]]:
    return {
        (role, path): (state, rule, group_id, primary_role, primary_path)
        for role, path, state, rule, group_id, primary_role, primary_path in inventory.db.execute(
            "SELECT role,path,state,rule,group_id,primary_role,primary_path FROM members"
        )
    }


def _set_xattr(path: Path, name: str, value: bytes, *, nofollow: bool = False) -> None:
    import ctypes

    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = libc.setxattr
    function.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    function.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(value)
    result = function(os.fsencode(path), name.encode(), buffer, len(value), 0, 1 if nofollow else 0)
    if result != 0:
        raise OSError(ctypes.get_errno(), "synthetic fixture xattr write failed")


def test_global_inventory_resolves_complete_cross_root_alias_before_any_payload_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.brain_host_tree_capture_v5 as capture

    held, roots = _held_fixture(tmp_path)
    left = roots["owner_checkout"] / "shared.bin"
    left.write_bytes(b"synthetic shared payload")
    right = roots["research_checkout"] / "shared-alias.bin"
    os.link(left, right)
    payload_reads: list[int] = []
    original_read = os.read

    def observe_read(fd, size):
        source = os.fstat(fd)
        if (source.st_dev, source.st_ino) == (left.stat().st_dev, left.stat().st_ino):
            payload_reads.append(fd)
            raise AssertionError("inventory read source payload")
        return original_read(fd, size)

    monkeypatch.setattr(capture.os, "read", observe_read)
    inventory = writer = None
    try:
        inventory, writer = _inventory(tmp_path, held, roots)
        rows = _rows(inventory)
        a = rows[("owner_checkout", "shared.bin")]
        b = rows[("research_checkout", "shared-alias.bin")]
        assert a[0] == b[0] == "included"
        assert a[2] == b[2]
        assert a[3:] == b[3:]
        assert a[3] == "owner_checkout"
        assert a[4] == "shared.bin"
        assert payload_reads == []
        receipt = inventory.public_inventory_receipt()
        assert receipt["payload_read"] is False
        file_root_bytes = sum(
            path.stat().st_size for role, path in roots.items() if ROOT_KINDS[role] == "file"
        )
        assert receipt["included_source_bytes"] == file_root_bytes + left.stat().st_size
    finally:
        if inventory is not None:
            inventory.close()
        if writer is not None:
            writer.close()
            os.close(writer._test_mount_fd)
        held.close()


def test_global_inventory_blocks_group_with_an_excluded_cross_root_alias(
    tmp_path: Path,
) -> None:
    held, roots = _held_fixture(tmp_path)
    left = roots["owner_checkout"] / "ordinary.bin"
    left.write_bytes(b"synthetic excluded alias payload")
    right = roots["research_checkout"] / ".env.local"
    os.link(left, right)
    inventory = writer = None
    try:
        inventory, writer = _inventory(tmp_path, held, roots)
        rows = _rows(inventory)
        assert rows[("owner_checkout", "ordinary.bin")][0] == "excluded_file"
        assert rows[("research_checkout", ".env.local")][0] == "excluded_file"
        assert rows[("owner_checkout", "ordinary.bin")][1] == "hardlink_alias_to_excluded"
        assert rows[("owner_checkout", "ordinary.bin")][2] is not None
        assert (
            rows[("owner_checkout", "ordinary.bin")][2]
            == rows[("research_checkout", ".env.local")][2]
        )
        assert inventory.hardlink_summary["excluded_group_count"] == 1
        assert inventory.public_inventory_receipt()["payload_read"] is False
    finally:
        if inventory is not None:
            inventory.close()
        if writer is not None:
            writer.close()
            os.close(writer._test_mount_fd)
        held.close()


def test_global_inventory_keeps_unseen_external_hardlink_alias_unresolved(tmp_path: Path) -> None:
    held, roots = _held_fixture(tmp_path)
    left = roots["owner_checkout"] / "one-visible-link.bin"
    left.write_bytes(b"synthetic unresolved group")
    external = tmp_path / "outside-roots.bin"
    os.link(left, external)
    inventory = writer = None
    try:
        inventory, writer = _inventory(tmp_path, held, roots)
        row = _rows(inventory)[("owner_checkout", "one-visible-link.bin")]
        assert row[0] == "excluded_file"
        assert row[1] == "hardlink_group_incomplete"
        assert inventory.hardlink_summary["unresolved_group_count"] == 1
    finally:
        if inventory is not None:
            inventory.close()
        if writer is not None:
            writer.close()
            os.close(writer._test_mount_fd)
        held.close()
        external.unlink(missing_ok=True)


def test_git_waiver_is_bound_only_to_research_checkout_and_census(tmp_path: Path) -> None:
    held, roots = _held_fixture(tmp_path)
    research_git = roots["research_checkout"] / ".git"
    research_git.mkdir()
    (research_git / "HEAD").write_text("synthetic ref\n")
    owner_git = roots["owner_checkout"] / ".git"
    owner_git.mkdir()
    (owner_git / "HEAD").write_text("synthetic ref\n")
    inventory = writer = None
    try:
        inventory, writer = _inventory(tmp_path, held, roots)
        research = inventory.db.execute(
            "SELECT disposition FROM members WHERE role='research_checkout' AND path='.git'"
        ).fetchone()
        owner = inventory.db.execute(
            "SELECT disposition FROM members WHERE role='owner_checkout' AND path='.git/HEAD'"
        ).fetchone()
        assert research == ("opaque_unresolved_census_groups",)
        assert owner != ("opaque_unresolved_census_groups",)
        waiver = inventory.public_inventory_receipt()["opaque_waivers"][0]
        assert waiver["role"] == "research_checkout"
        assert waiver["path"] == ".git"
        assert waiver["unresolved_hardlink_group_count"] == 6053
        assert waiver["census_sha256"] == (
            "de9c8cb336658754c0931073a18d5e0bb209fcc5292f313cbf7ce8dd5b1729d9"
        )
    finally:
        if inventory is not None:
            inventory.close()
        if writer is not None:
            writer.close()
            os.close(writer._test_mount_fd)
        held.close()


def test_nested_git_policy_declaration_is_not_hidden_by_top_level_waiver(
    tmp_path: Path,
) -> None:
    held, roots = _held_fixture(tmp_path)
    policies = _policies(tmp_path, roots)
    owner_policy = json.loads(policies["owner_checkout"].policy_path.read_bytes())
    owner_policy["exact_path_exclusions"] = {
        "nested/.git/missing": {
            "entry_type": "file",
            "disposition": "separate_protected_custody",
        }
    }
    raw = canonical_json(owner_policy)
    policy_path = tmp_path / "owner-nested-git-policy.json"
    policy_path.write_bytes(raw)
    policies["owner_checkout"] = RoleCapturePolicy(policy_path, hashlib.sha256(raw).hexdigest())
    writer = _writer(tmp_path)
    try:
        with pytest.raises(TreeCaptureV5Error, match="policy_exact_exclusion_unmatched"):
            inventory_held_data_v5(
                held,
                writer,
                role_policies=policies,
                reserve_capacity=lambda _size: None,
                scratch_reserve_bytes=1024 * 1024,
                payload_reserve_bytes=0,
            )
    finally:
        writer.close()
        os.close(writer._test_mount_fd)
        held.close()


def test_capture_inventory_rejects_non_synthetic_held_view(tmp_path: Path) -> None:
    held, roots = _held_fixture(tmp_path)
    writer = _writer(tmp_path)
    held.synthetic = False
    try:
        with pytest.raises(TreeCaptureV5Error, match="synthetic_fixture_only"):
            inventory_held_data_v5(
                held,
                writer,
                role_policies=_policies(tmp_path, roots),
                reserve_capacity=lambda _size: None,
                scratch_reserve_bytes=1024 * 1024,
                payload_reserve_bytes=0,
            )
    finally:
        writer.close()
        os.close(writer._test_mount_fd)
        held.close()


def test_global_inventory_records_symlink_without_traversing_target(tmp_path: Path) -> None:
    held, roots = _held_fixture(tmp_path)
    secret = tmp_path / "outside-secret"
    secret.write_bytes(b"must not be opened")
    link = roots["owner_checkout"] / "escape"
    link.symlink_to(secret)
    inventory = writer = None
    try:
        inventory, writer = _inventory(tmp_path, held, roots)
        row = inventory.db.execute(
            "SELECT kind,link_text FROM members WHERE role=? AND path=?",
            ("owner_checkout", "escape"),
        ).fetchone()
        assert row == ("symlink", os.fsencode(str(secret)))
        assert (
            inventory.db.execute("SELECT 1 FROM members WHERE path='outside-secret'").fetchone()
            is None
        )
    finally:
        if inventory is not None:
            inventory.close()
        if writer is not None:
            writer.close()
            os.close(writer._test_mount_fd)
        held.close()


def test_global_inventory_does_not_stat_or_open_excluded_directory_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.brain_host_tree_capture_v5 as capture

    held, roots = _held_fixture(tmp_path)
    private = roots["owner_checkout"] / ".ssh"
    private.mkdir()
    (private / "private-key.pem").write_bytes(b"synthetic protected child")
    original_stat = os.stat
    original_open = os.open
    child_calls: list[str] = []

    def observe_stat(path, *args, **kwargs):
        if os.fspath(path) == "private-key.pem":
            child_calls.append("stat")
            raise AssertionError("excluded directory child stat attempted")
        return original_stat(path, *args, **kwargs)

    def observe_open(path, *args, **kwargs):
        if os.fspath(path) == "private-key.pem":
            child_calls.append("open")
            raise AssertionError("excluded directory child open attempted")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(capture.os, "stat", observe_stat)
    monkeypatch.setattr(capture.os, "open", observe_open)
    inventory = writer = None
    try:
        inventory, writer = _inventory(tmp_path, held, roots)
        row = inventory.db.execute(
            "SELECT kind,state FROM members WHERE role=? AND path=?",
            ("owner_checkout", ".ssh"),
        ).fetchone()
        assert row == ("directory", "excluded_subtree")
        assert (
            inventory.db.execute(
                "SELECT 1 FROM members WHERE role=? AND path=?",
                ("owner_checkout", ".ssh/private-key.pem"),
            ).fetchone()
            is None
        )
        assert child_calls == []
    finally:
        if inventory is not None:
            inventory.close()
        if writer is not None:
            writer.close()
            os.close(writer._test_mount_fd)
        held.close()


def test_symlink_metadata_and_parent_replacement_never_use_reconstructed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.brain_host_tree_custody as v2

    held, roots = _held_fixture(tmp_path)
    safe_parent = roots["owner_checkout"] / "safe-parent"
    safe_parent.mkdir()
    (safe_parent / "link-metadata").symlink_to("missing-target")
    outside = tmp_path / "outside-replacement"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"must remain unread and unchanged")
    inventory = writer = None
    path_reads: list[str] = []
    original_list_xattrs = v2._list_xattrs

    def observe_path_xattrs(path, **kwargs):
        path_reads.append(os.fsdecode(path))
        return original_list_xattrs(path, **kwargs)

    monkeypatch.setattr(v2, "_list_xattrs", observe_path_xattrs)
    try:
        inventory, writer = _inventory(tmp_path, held, roots)
        safe_parent.rename(roots["owner_checkout"] / "moved-safe-parent")
        safe_parent.symlink_to(outside, target_is_directory=True)
        with pytest.raises((OSError, TreeCaptureV5Error)):
            capture_candidate_v5(inventory)
        assert sentinel.read_bytes() == b"must remain unread and unchanged"
        assert path_reads == []
    finally:
        if inventory is not None:
            inventory.close()
        if writer is not None:
            writer.close()
            os.close(writer._test_mount_fd)
        held.close()


def test_spool_failure_removes_database_and_unique_directory(tmp_path: Path) -> None:
    held, roots = _held_fixture(tmp_path)
    writer = _writer(tmp_path)

    reserve_calls = 0

    def fail_after_inventory(_size: int) -> None:
        nonlocal reserve_calls
        reserve_calls += 1
        if reserve_calls == 2:
            raise OSError("synthetic full volume")

    with pytest.raises(TreeCaptureV5Error, match="storage_reserve_recheck_failed"):
        inventory_held_data_v5(
            held,
            writer,
            role_policies=_policies(tmp_path, roots),
            reserve_capacity=fail_after_inventory,
            scratch_reserve_bytes=1024 * 1024,
            payload_reserve_bytes=0,
        )
    spool_parent = (
        tmp_path / "storage-volume" / "BrainData" / "host-recovery" / "tree-capture-v5" / "spool"
    )
    assert list(spool_parent.iterdir()) == []
    assert not list((tmp_path / "storage-volume").rglob("inventory.sqlite"))
    writer.close()
    os.close(writer._test_mount_fd)
    held.close()


def test_spool_cleanup_never_unlinks_replacement_database_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.brain_host_tree_capture_v5 as capture

    held, roots = _held_fixture(tmp_path)
    writer = _writer(tmp_path)
    original_scan = capture._scan_role
    observed: dict[str, Path] = {}

    def replace_database_then_fail(*args, **kwargs):
        spool_parent = (
            tmp_path
            / "storage-volume"
            / "BrainData"
            / "host-recovery"
            / "tree-capture-v5"
            / "spool"
        )
        spool_dirs = [item for item in spool_parent.iterdir() if item.is_dir()]
        assert len(spool_dirs) == 1
        spool_dir = spool_dirs[0]
        db_path = spool_dir / "inventory.sqlite"
        original_path = spool_dir / "original-database.sqlite"
        db_path.rename(original_path)
        replacement = spool_dir / "inventory.sqlite"
        replacement.write_bytes(b"synthetic replacement database")
        replacement.chmod(0o600)
        observed["replacement"] = replacement
        observed["original"] = original_path
        return original_scan(*args, **kwargs)

    monkeypatch.setattr(capture, "_scan_role", replace_database_then_fail)
    try:
        with pytest.raises(TreeCaptureV5Error, match="inventory_failure_cleanup_incomplete"):
            inventory_held_data_v5(
                held,
                writer,
                role_policies=_policies(tmp_path, roots),
                reserve_capacity=lambda _size: None,
                scratch_reserve_bytes=1024 * 1024,
                payload_reserve_bytes=0,
            )
        assert observed["replacement"].read_bytes() == b"synthetic replacement database"
        assert observed["original"].exists()
    finally:
        if observed:
            shutil.rmtree(observed["replacement"].parent, ignore_errors=True)
        writer.close()
        os.close(writer._test_mount_fd)
        held.close()


def test_candidate_verify_scratch_setup_failure_removes_original_inode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.brain_host_tree_capture_v5 as capture

    prefix = "brain-capture-v5-verify-"
    before = {path.name for path in Path(tempfile.gettempdir()).glob(f"{prefix}*")}

    def fail_connect(_path: str):
        raise sqlite3.OperationalError("injected SQLite open failure")

    monkeypatch.setattr(capture.sqlite3, "connect", fail_connect)
    with pytest.raises(TreeCaptureV5Error, match="candidate_verify_scratch_unavailable"):
        capture._open_candidate_verify_db()
    after = {path.name for path in Path(tempfile.gettempdir()).glob(f"{prefix}*")}
    assert after == before


@pytest.mark.parametrize("nested_name", ["metadata", "payload"])
def test_candidate_reader_rejects_permissive_intermediate_directory(
    tmp_path: Path, nested_name: str
) -> None:
    from tools.brain_host_tree_capture_v5 import _open_output_directory

    writer = _writer(tmp_path)
    candidate_id = "d" * 32
    relative = Path("host-recovery/tree-capture-v5/candidates") / candidate_id
    candidate_root = tmp_path / "storage-volume" / "BrainData" / relative
    for directory in [
        tmp_path / "storage-volume" / "BrainData" / "host-recovery",
        tmp_path / "storage-volume" / "BrainData" / "host-recovery" / "tree-capture-v5",
        tmp_path
        / "storage-volume"
        / "BrainData"
        / "host-recovery"
        / "tree-capture-v5"
        / "candidates",
        candidate_root,
    ]:
        directory.mkdir(mode=0o700, exist_ok=True)
        directory.chmod(0o700)
    nested = candidate_root / nested_name
    nested.mkdir(mode=0o755)
    nested.chmod(0o755)
    try:
        with pytest.raises(TreeCaptureV5Error, match="candidate_directory_component_invalid"):
            _open_output_directory(writer.root_fd, f"{relative.as_posix()}/{nested_name}")
    finally:
        writer.close()
        os.close(writer._test_mount_fd)


def test_capture_streams_global_primary_groups_metadata_and_fresh_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    held, roots = _held_fixture(tmp_path)
    payload = bytes(index % 251 for index in range(2 * 1024 * 1024 + 17))
    primary = roots["owner_checkout"] / "large.bin"
    primary.write_bytes(payload)
    alias = roots["research_checkout"] / "large-alias.bin"
    os.link(primary, alias)
    _set_xattr(primary, "com.example.synthetic", b"private synthetic xattr")
    link = roots["owner_checkout"] / "external-link"
    target = tmp_path / "external-target"
    target.write_bytes(b"not traversed")
    link.symlink_to(target)
    _set_xattr(link, "com.example.link", b"link metadata", nofollow=True)
    confined_link = roots["owner_checkout"] / "confined-link"
    confined_link.symlink_to("large.bin")
    bridge = roots["owner_checkout"] / "link-bridge"
    bridge.symlink_to(target)
    chain_escape = roots["owner_checkout"] / "chain-escape"
    chain_escape.symlink_to("link-bridge")

    inventory = writer = None
    quarantine_temp = None
    try:
        inventory, writer = _inventory(tmp_path, held, roots)
        original_verify = capture_v5.verify_candidate_v5
        internal_plan_pins: list[str | None] = []

        def track_internal_plan_pin(*args, **kwargs):
            if len(internal_plan_pins) < 2:
                internal_plan_pins.append(kwargs.get("expected_plan_sha256"))
            return original_verify(*args, **kwargs)

        monkeypatch.setattr(capture_v5, "verify_candidate_v5", track_internal_plan_pin)
        file_role_fd = inventory.roots["helius_mcp_wrapper"]
        os.lseek(file_role_fd, 7, os.SEEK_SET)
        receipt = capture_candidate_v5(inventory)
        assert internal_plan_pins == [inventory.plan_sha256, inventory.plan_sha256]
        assert os.lseek(file_role_fd, 0, os.SEEK_CUR) == 7
        assert receipt["readback_verified"] is True
        assert receipt["capture_complete"] is False
        assert receipt["T_qualified"] is False
        assert (
            verify_candidate_v5(writer, receipt["candidate_id"])["member_count"]
            == inventory.entry_count
        )
        quarantine_volume = Path(
            os.environ.get("BRAIN_HOST_CAPTURE_V5_QUARANTINE_VOLUME", "/dev/null")
        )
        separate_device_available = False
        probe_quarantine = None
        if quarantine_volume.is_dir() and quarantine_volume.stat().st_dev != writer.device:
            try:
                probe_quarantine = tempfile.TemporaryDirectory(
                    prefix="capture-v5-quarantine-probe-", dir=quarantine_volume
                )
                separate_device_available = True
            except OSError:
                separate_device_available = False
            finally:
                if probe_quarantine is not None:
                    probe_quarantine.cleanup()
        subprocess_extract = """
import json, os, sys
from tools.brain_senses_external_writer_v1 import DescriptorWriter
from tools.brain_host_tree_capture_v5 import extract_candidate_inert_v5
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
writer = DescriptorWriter(fd)
os.close(fd)
quarantine_fd = os.open(sys.argv[3], os.O_RDONLY | os.O_DIRECTORY)
try:
    result = extract_candidate_inert_v5(
        writer, sys.argv[2], quarantine_parent_fd=quarantine_fd,
        allow_unbound_synthetic=True,
    )
    print(json.dumps(result))
finally:
    os.close(quarantine_fd)
    writer.close()
"""
        if separate_device_available:
            with tempfile.TemporaryDirectory(
                prefix="capture-v5-inert-subprocess-", dir=quarantine_volume
            ) as subprocess_quarantine:
                os.chmod(subprocess_quarantine, 0o700)
                second_process = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        subprocess_extract,
                        str(tmp_path / "storage-volume" / "BrainData"),
                        receipt["candidate_id"],
                        subprocess_quarantine,
                    ],
                    cwd=Path(__file__).resolve().parents[1],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                assert second_process.returncode == 0, second_process.stderr
                subprocess_receipt = json.loads(second_process.stdout)
                assert subprocess_receipt["inert_data_extraction"] is True
                assert subprocess_receipt["xattr_acl_flags_applied"] is False
                assert (
                    subprocess_receipt["candidate_source_device"]
                    != subprocess_receipt["quarantine_device"]
                )
                second_root = Path(subprocess_quarantine) / subprocess_receipt["extraction_id"]
                assert (
                    os.stat(second_root / "owner_checkout" / "large.bin").st_ino
                    == os.stat(second_root / "research_checkout" / "large-alias.bin").st_ino
                )
            quarantine_temp = tempfile.TemporaryDirectory(
                prefix="capture-v5-inert-test-", dir=quarantine_volume
            )
            quarantine = quarantine_temp.name
            os.chmod(quarantine, 0o700)
            quarantine_fd = os.open(quarantine, os.O_RDONLY | os.O_DIRECTORY)
            try:
                assert os.fstat(quarantine_fd).st_dev != os.fstat(writer.root_fd).st_dev
                import tools.brain_host_tree_capture_v5 as capture

                real_read = capture._candidate_read
                real_open_destination_parent = capture._open_destination_parent
                extraction_started = False
                extraction_swapped = False
                moved_extraction = None
                replacement_extraction = None

                def observe_extraction_start(root_fd: int, relative: str) -> tuple[int, str]:
                    nonlocal extraction_started, extraction_swapped
                    nonlocal moved_extraction, replacement_extraction
                    extraction_started = True
                    if not extraction_swapped:
                        names = os.listdir(quarantine_fd)
                        assert len(names) == 1
                        original_name = names[0]
                        moved_name = f"{original_name}.moved"
                        os.rename(
                            original_name,
                            moved_name,
                            src_dir_fd=quarantine_fd,
                            dst_dir_fd=quarantine_fd,
                        )
                        moved_extraction = Path(quarantine) / moved_name
                        replacement_extraction = Path(quarantine) / original_name
                        replacement_extraction.mkdir(mode=0o700)
                        (moved_extraction / "injected-moved.txt").write_text(
                            "keep moved extraction", encoding="utf-8"
                        )
                        (replacement_extraction / "injected-replacement.txt").write_text(
                            "keep replacement extraction", encoding="utf-8"
                        )
                        extraction_swapped = True
                    return real_open_destination_parent(root_fd, relative)

                def fail_during_payload_read(
                    root_fd: int, base: str, relative: str, limit: int
                ) -> bytes:
                    if extraction_started and "/payload/" in relative:
                        raise OSError("synthetic extraction read failure")
                    return real_read(root_fd, base, relative, limit)

                with monkeypatch.context() as patch:
                    patch.setattr(capture, "_candidate_read", fail_during_payload_read)
                    patch.setattr(capture, "_open_destination_parent", observe_extraction_start)
                    with pytest.raises(
                        TreeCaptureV5Error, match="inert_extraction_cleanup_incomplete"
                    ):
                        extract_candidate_inert_v5(
                            writer,
                            receipt["candidate_id"],
                            quarantine_parent_fd=quarantine_fd,
                        )
                assert extraction_started
                assert extraction_swapped
                assert moved_extraction is not None and replacement_extraction is not None
                assert (moved_extraction / "injected-moved.txt").read_text() == (
                    "keep moved extraction"
                )
                assert (replacement_extraction / "injected-replacement.txt").read_text() == (
                    "keep replacement extraction"
                )
                extraction = extract_candidate_inert_v5(
                    writer,
                    receipt["candidate_id"],
                    quarantine_parent_fd=quarantine_fd,
                )
            finally:
                os.close(quarantine_fd)
            assert extraction["quarantine_only"] is True
            assert extraction["xattr_acl_flags_applied"] is False
            assert extraction["restore_verified"] is False
            assert extraction["unconfined_links_omitted"] >= 1
            extracted_root = Path(quarantine) / extraction["extraction_id"]
            extracted_large = extracted_root / "owner_checkout" / "large.bin"
            assert (
                hashlib.sha256(extracted_large.read_bytes()).digest()
                == hashlib.sha256(payload).digest()
            )
            extracted_fd = os.open(extracted_large, os.O_RDONLY)
            try:
                assert b"com.example.synthetic" not in v2._list_xattrs_fd(extracted_fd)
            finally:
                os.close(extracted_fd)
            extracted_link = extracted_root / "owner_checkout" / "external-link"
            assert not extracted_link.exists()
            assert not extracted_link.is_symlink()
            extracted_confined = extracted_root / "owner_checkout" / "confined-link"
            assert not extracted_confined.exists()
            assert not extracted_confined.is_symlink()
            extracted_chain = extracted_root / "owner_checkout" / "chain-escape"
            assert not extracted_chain.exists()
            assert not extracted_chain.is_symlink()
            extracted_alias = extracted_root / "research_checkout" / "large-alias.bin"
            assert os.stat(extracted_large).st_ino == os.stat(extracted_alias).st_ino
        else:
            same_device_temp = tempfile.TemporaryDirectory(prefix="capture-v5-same-device-")
            try:
                same_device_fd = os.open(same_device_temp.name, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    assert os.fstat(same_device_fd).st_dev == os.fstat(writer.root_fd).st_dev
                    with pytest.raises(TreeCaptureV5Error, match="inert_quarantine_parent_invalid"):
                        extract_candidate_inert_v5(
                            writer,
                            receipt["candidate_id"],
                            quarantine_parent_fd=same_device_fd,
                        )
                finally:
                    os.close(same_device_fd)
            finally:
                same_device_temp.cleanup()

        base = "host-recovery/tree-capture-v5/candidates/" + receipt["candidate_id"]
        index = json.loads(writer.read_bytes(f"{base}/INDEX.json"))
        members = []
        for page in index["member_pages"]:
            members.extend(
                json.loads(line) for line in writer.read_bytes(page["path"]).splitlines()
            )
        file_rows = [item for item in members if item["path"] in {"large.bin", "large-alias.bin"}]
        assert len(file_rows) == 2
        assert file_rows[0]["payload_id"] == file_rows[1]["payload_id"]
        assert file_rows[0]["payload_sha256"] == hashlib.sha256(payload).hexdigest()
        assert file_rows[1]["source_nlink"] == 2
        xattr = next(
            ref
            for ref in file_rows[0]["metadata"]["xattrs"]
            if ref["name"] == "com.example.synthetic"
        )
        assert writer.read_bytes(xattr["path"]) == b"private synthetic xattr"
        link_row = next(item for item in members if item["path"] == "external-link")
        assert link_row["metadata"]["state"] == "unobserved_symlink_nofollow"
        assert link_row["metadata"]["xattrs"] == []
        assert link_row["metadata"]["acl_state"] == "unobserved_symlink_nofollow"
        assert not any(item["path"] == "external-target" for item in members)
        unexpected = (
            tmp_path
            / "storage-volume"
            / "BrainData"
            / "host-recovery"
            / "tree-capture-v5"
            / "candidates"
            / receipt["candidate_id"]
            / "payload"
            / "unexpected"
        )
        unexpected.mkdir(mode=0o700)
        (unexpected / "extra.bin").write_bytes(b"unexpected")
        (unexpected / "extra.bin").chmod(0o600)
        with pytest.raises(TreeCaptureV5Error, match="candidate_unexpected_member_set"):
            verify_candidate_v5(writer, receipt["candidate_id"])
        (unexpected / "extra.bin").unlink()
        unexpected.rmdir()
        readback = (
            tmp_path
            / "storage-volume"
            / "BrainData"
            / "host-recovery"
            / "tree-capture-v5"
            / "candidates"
            / receipt["candidate_id"]
            / "READBACK.json"
        )
        readback.write_bytes(b"{}")
        with pytest.raises(TreeCaptureV5Error, match="candidate_final_witness_invalid"):
            verify_candidate_v5(writer, receipt["candidate_id"])
    finally:
        if quarantine_temp is not None:
            quarantine_temp.cleanup()
        if inventory is not None:
            inventory.close()
        if writer is not None:
            writer.close()
            os.close(writer._test_mount_fd)
        held.close()


def test_capture_rejects_source_path_replacement_and_leaves_incomplete_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.brain_host_tree_capture_v5 as capture

    held, roots = _held_fixture(tmp_path)
    source = roots["owner_checkout"] / "replace-me.bin"
    source.write_bytes(b"synthetic payload before replacement")
    inventory, writer = _inventory(tmp_path, held, roots)
    original_read = os.pread
    replaced = False

    def replacing_read(fd: int, size: int, offset: int) -> bytes:
        nonlocal replaced
        if not replaced:
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) == (source.stat().st_dev, source.stat().st_ino):
                replacement = source.with_suffix(".replacement")
                replacement.write_bytes(b"replacement payload")
                os.replace(replacement, source)
                replaced = True
        return original_read(fd, size, offset)

    monkeypatch.setattr(capture.os, "pread", replacing_read)
    try:
        with pytest.raises(TreeCaptureV5Error, match="candidate_cleanup_incomplete"):
            capture_candidate_v5(inventory)
        assert replaced
        candidates = (
            tmp_path
            / "storage-volume"
            / "BrainData"
            / "host-recovery"
            / "tree-capture-v5"
            / "candidates"
        )
        remaining = list(candidates.iterdir())
        assert len(remaining) == 1
        assert list(remaining[0].rglob("*.bin"))
    finally:
        inventory.close()
        writer.close()
        os.close(writer._test_mount_fd)
        held.close()


def test_candidate_cleanup_keeps_replacement_directory_untouched(tmp_path: Path) -> None:
    held, roots = _held_fixture(tmp_path)
    source = roots["owner_checkout"] / "candidate-race.bin"
    source.write_bytes(b"synthetic candidate replacement race")
    inventory, writer = _inventory(tmp_path, held, roots)
    original_create = writer.create
    candidates_parent = (
        tmp_path
        / "storage-volume"
        / "BrainData"
        / "host-recovery"
        / "tree-capture-v5"
        / "candidates"
    )
    moved_path = None
    replacement_path = None
    swapped = False

    def replace_candidate_before_first_write(relative: str, payload: bytes, *, mode: int = 0o600):
        nonlocal moved_path, replacement_path, swapped
        if not swapped and relative.startswith("host-recovery/tree-capture-v5/candidates/"):
            candidate_id = relative.split("/")[3]
            current = candidates_parent / candidate_id
            moved_path = candidates_parent / f"{candidate_id}.moved"
            replacement_path = current
            current.rename(moved_path)
            (moved_path / "injected-moved.txt").write_text("keep original", encoding="utf-8")
            current.mkdir(mode=0o700)
            (current / "injected-replacement.txt").write_text("keep replacement", encoding="utf-8")
            swapped = True
        return original_create(relative, payload, mode=mode)

    writer.create = replace_candidate_before_first_write
    try:
        with pytest.raises(TreeCaptureV5Error, match="candidate_cleanup_incomplete"):
            capture_candidate_v5(inventory)
        assert swapped
        assert replacement_path is not None and replacement_path.is_dir()
        assert moved_path is not None and moved_path.is_dir()
        assert (replacement_path / "injected-replacement.txt").read_text() == "keep replacement"
        assert (moved_path / "injected-moved.txt").read_text() == "keep original"
    finally:
        writer.create = original_create
        if replacement_path is not None:
            shutil.rmtree(replacement_path, ignore_errors=True)
        if moved_path is not None:
            shutil.rmtree(moved_path, ignore_errors=True)
        inventory.close()
        writer.close()
        os.close(writer._test_mount_fd)
        held.close()


def test_candidate_cleanup_leaves_unexpected_injected_member(
    tmp_path: Path,
) -> None:
    held, roots = _held_fixture(tmp_path)
    (roots["owner_checkout"] / "payload.bin").write_bytes(b"synthetic payload")
    inventory, writer = _inventory(tmp_path, held, roots)
    original_create = writer.create
    candidates_parent = (
        tmp_path
        / "storage-volume"
        / "BrainData"
        / "host-recovery"
        / "tree-capture-v5"
        / "candidates"
    )
    injected_path = None
    injected = False

    def create_then_inject(relative: str, payload: bytes, *, mode: int = 0o600):
        nonlocal injected, injected_path
        result = original_create(relative, payload, mode=mode)
        if not injected:
            candidate_id = relative.split("/")[3]
            candidate = candidates_parent / candidate_id
            injected_path = candidate / "unexpected-injected-member"
            injected_path.write_text("preserve", encoding="utf-8")
            injected = True
            raise OSError("synthetic failure after injected candidate member")
        return result

    writer.create = create_then_inject
    try:
        with pytest.raises(TreeCaptureV5Error, match="candidate_cleanup_incomplete"):
            capture_candidate_v5(inventory)
        assert injected and injected_path is not None
        assert injected_path.read_text() == "preserve"
    finally:
        writer.create = original_create
        inventory.close()
        writer.close()
        os.close(writer._test_mount_fd)
        held.close()


def test_inert_cleanup_leaves_unexpected_injected_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    volume = Path(os.environ.get("BRAIN_HOST_CAPTURE_V5_QUARANTINE_VOLUME", "/dev/null"))
    if not volume.is_dir() or volume.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("separate-device synthetic quarantine target is unavailable")

    held, roots = _held_fixture(tmp_path)
    (roots["owner_checkout"] / "payload.bin").write_bytes(b"synthetic payload")
    inventory, writer = _inventory(tmp_path, held, roots)
    receipt = capture_candidate_v5(inventory)
    quarantine_temp = tempfile.TemporaryDirectory(prefix="capture-v5-injected-", dir=volume)
    quarantine_fd = os.open(quarantine_temp.name, os.O_RDONLY | os.O_DIRECTORY)
    injected = False
    import tools.brain_host_tree_capture_v5 as capture

    original_open_parent = capture._open_destination_parent

    def inject_and_fail(root_fd: int, relative: str) -> tuple[int, str]:
        nonlocal injected
        if not injected:
            marker_fd = os.open(
                "unexpected-injected-member",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=root_fd,
            )
            try:
                os.write(marker_fd, b"preserve")
            finally:
                os.close(marker_fd)
            injected = True
            raise OSError("synthetic extraction failure after injected member")
        return original_open_parent(root_fd, relative)

    monkeypatch.setattr(capture, "_open_destination_parent", inject_and_fail)
    try:
        with pytest.raises(TreeCaptureV5Error, match="inert_extraction_cleanup_incomplete"):
            extract_candidate_inert_v5(
                writer, receipt["candidate_id"], quarantine_parent_fd=quarantine_fd
            )
        assert injected
        extractions = list(Path(quarantine_temp.name).iterdir())
        assert len(extractions) == 1
        assert (extractions[0] / "unexpected-injected-member").read_bytes() == b"preserve"
    finally:
        os.close(quarantine_fd)
        quarantine_temp.cleanup()
        inventory.close()
        writer.close()
        os.close(writer._test_mount_fd)
        held.close()


def test_fresh_process_readonly_verify_cli_rejects_writable_target_without_mutation(
    tmp_path: Path,
) -> None:
    mount = tmp_path / "synthetic-storage"
    mount.mkdir(mode=0o700)
    config_path = tmp_path / "private" / "storage.json"
    config_path.parent.mkdir(mode=0o700)
    config = StorageConfig(
        "b" * 32,
        "11111111-2222-3333-4444-555555555555",
        mount,
        "BrainData",
        1,
    )
    config_path.write_bytes(canonical_json(config.to_json()))
    config_path.chmod(0o600)

    class Probe:
        def inspect(self, mount_path: Path) -> VolumeInfo:
            return VolumeInfo(
                mount_path=mount_path,
                volume_uuid=config.volume_uuid,
                filesystem="Case-sensitive APFS",
                filesystem_type="apfs",
                owners_enabled=True,
                read_only=False,
                available_bytes=10**9,
                is_mountpoint=True,
                mount_options=("apfs", "local", "journaled"),
            )

    create_root(config_path, probe=Probe())
    root = mount / config.data_root
    (root / "verify-scratch").mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd)
    os.close(root_fd)
    candidate_id = "c" * 32
    role_dispositions = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        if role == "owner_checkout":
            role_dispositions.append(
                {
                    "role": role,
                    "present": True,
                    "kind": ROOT_KINDS[role],
                    "disposition": "included_root",
                    "scope": "non_protected",
                    "locator_sha256": hashlib.sha256(role.encode()).hexdigest(),
                    "policy_sha256": hashlib.sha256(b"synthetic policy").hexdigest(),
                    "root_signature": [1] * 11,
                    "root_stat": {
                        "dev": 1,
                        "ino": 2,
                        "nlink": 2,
                        "mode": stat.S_IFDIR | 0o700,
                        "uid": os.getuid(),
                        "gid": os.getgid(),
                        "size": 0,
                        "mtime_ns": 1,
                        "ctime_ns": 1,
                        "atime_ns": 1,
                        "birthtime_ns": 1,
                        "flags": 0,
                    },
                    "unresolved_reason_sha256": None,
                }
            )
        else:
            role_dispositions.append(
                {
                    "role": role,
                    "present": False,
                    "kind": None,
                    "scope": None,
                    "disposition": "absent_unresolved",
                    "locator_sha256": None,
                    "policy_sha256": None,
                    "root_signature": None,
                    "root_stat": None,
                    "unresolved_reason_sha256": hashlib.sha256(
                        b"synthetic absent role"
                    ).hexdigest(),
                }
            )
    member = {
        "role": "owner_checkout",
        "path": "",
        "kind": "directory",
        "state": "included",
        "rule": None,
        "disposition": None,
        "mode": 0o700,
        "source_device": 1,
        "source_inode": 2,
        "source_nlink": 2,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "size_bytes": 0,
        "mtime_ns": 1,
        "ctime_ns": 1,
        "atime_ns": 1,
        "birthtime_ns": 1,
        "flags": 0,
        "link_target_b64": None,
        "group_id": None,
        "primary_role": None,
        "primary_path": None,
        "payload_id": None,
        "payload_sha256": None,
        "payload_bytes": None,
        "metadata": {
            "state": "observed",
            "xattrs": [],
            "acl": None,
            "acl_state": "absent",
            "flags_state": "absent",
            "flags_value": None,
        },
    }
    member_raw = canonical_json(member).rstrip(b"\n") + b"\n"
    index = {
        "schema": "brain-host-tree-capture-v5-candidate",
        "candidate_id": candidate_id,
        "synthetic_fixture_only": True,
        "plan_sha256": hashlib.sha256(b"synthetic plan").hexdigest(),
        "policy_roster_sha256": policy_roster_sha256_v5(
            hashlib.sha256(b"synthetic plan").hexdigest(), role_dispositions
        ),
        "role_dispositions": role_dispositions,
        "opaque_waivers": [GIT_OPAQUE_WAIVER],
        "member_pages": [
            {
                "path": (
                    f"host-recovery/tree-capture-v5/candidates/{candidate_id}/"
                    "members/00000000.jsonl"
                ),
                "sha256": hashlib.sha256(member_raw).hexdigest(),
                "count": 1,
                "size_bytes": len(member_raw),
            }
        ],
        "payload_pages": [],
        "sidecar_pages": [],
        "payload_bytes": 0,
        "entry_count": 1,
        "resource_model": {"minimum_total_reserve_bytes": 0},
        "xattr_acl_flags_restore_policy": "evidence_only_never_applied",
    }
    index_raw = canonical_json(index)
    index_sha = hashlib.sha256(index_raw).hexdigest()
    base = f"host-recovery/tree-capture-v5/candidates/{candidate_id}"
    writer.create(f"{base}/members/00000000.jsonl", member_raw)
    writer.create(f"{base}/INDEX.json", index_raw)
    writer.create(
        f"{base}/READBACK.json",
        canonical_json(
            {
                "schema": "brain-host-tree-capture-v5-final-readback-witness",
                "candidate_id": candidate_id,
                "index_sha256": index_sha,
                "member_count": 1,
                "payload_bytes": 0,
            }
        ),
    )
    writer.close()
    (mount / storage.LOCK_NAME).unlink(missing_ok=True)
    before = sorted(item.relative_to(mount).as_posix() for item in mount.rglob("*"))
    repo = Path(__file__).resolve().parents[1]
    cli_script = """
import memecoin_research_storage as storage
from memecoin_research_storage import VolumeInfo
class Probe:
    def inspect(self, mount_path):
        return VolumeInfo(mount_path, '11111111-2222-3333-4444-555555555555',
            'Case-sensitive APFS', True, False, 10**9, True,
            filesystem_type='apfs', mount_options=('apfs','local','journaled'))
storage.MacOSVolumeProbe = Probe
from tools.brain_host_tree_capture_v5 import main
raise SystemExit(main())
"""

    def invoke(index_digest: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                cli_script,
                "verify",
                candidate_id,
                "--storage-config",
                str(config_path),
                "--target-volume-uuid",
                config.volume_uuid,
                "--target-root-id",
                config.root_id,
                "--plan-sha256",
                hashlib.sha256(b"synthetic plan").hexdigest(),
                "--scratch-parent",
                str(root / "verify-scratch"),
                "--index-sha256",
                index_digest,
            ],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )

    verified = invoke(index_sha)
    assert verified.returncode == 2
    assert "production_verify_candidate_target_not_read_only" in verified.stderr
    after = sorted(item.relative_to(mount).as_posix() for item in mount.rglob("*"))
    assert after == before
