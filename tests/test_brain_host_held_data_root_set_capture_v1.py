from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tools.brain_host_held_data_root_set_capture_v1 import (
    CAPTURE_CONSISTENCY_LITERAL,
    INDEX_SCHEMA,
    POLICY_SCHEMA,
    SET_BASE,
    HeldDataRootSetError,
    capture_held_data_root_set_v1,
    verify_held_data_root_set_v1,
)
from tools.brain_host_held_data_t_v1 import (
    REQUIRED_ROOT_ROLES,
    ROOT_KINDS,
    ApprovedDataPlan,
    HeldDataT,
    RootDeclaration,
    approved_data_plan_sha256,
)
from tools.brain_host_tree_custody_v3 import POLICY_SCHEMA as TREE_POLICY_SCHEMA
from tools.brain_host_tree_custody_v3 import _validate_policy
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

DATA_UUID = "6e652e4f-9c38-4a48-887a-530158ba2767"
DATA_GROUP = "6e652e4f-9c38-4a48-887a-530158ba2767"


class _FakeGuard:
    volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"
    root_id = "a" * 32


def _plan(
    mountpoint: Path,
    *,
    present: tuple[str, ...] = ("owner_checkout", "research_checkout"),
    present_file: str | None = None,
) -> ApprovedDataPlan:
    roster: dict[str, RootDeclaration] = {}
    for role in sorted(REQUIRED_ROOT_ROLES):
        if role in present:
            locator = f"roots/{role}"
            roster[role] = RootDeclaration(True, locator, ROOT_KINDS[role])
            if ROOT_KINDS[role] == "directory":
                (mountpoint / locator).mkdir(parents=True, exist_ok=True)
            else:
                (mountpoint / locator).parent.mkdir(parents=True, exist_ok=True)
                (mountpoint / locator).write_bytes(b"opaque file fixture")
        elif role == present_file:
            locator = f"roots/{role}"
            roster[role] = RootDeclaration(True, locator, ROOT_KINDS[role])
            (mountpoint / locator).parent.mkdir(parents=True, exist_ok=True)
            (mountpoint / locator).write_bytes(b"opaque file fixture")
        else:
            roster[role] = RootDeclaration(
                False,
                unresolved_reason=f"fixture_{role}_not_selected",
            )
    return ApprovedDataPlan(
        "synthetic-root-set-fixture",
        DATA_UUID,
        "disk5s1",
        "disk5",
        DATA_GROUP,
        roster,
    )


def _writer(path: Path, guard: object) -> DescriptorWriter:
    path.mkdir(mode=0o700)
    root_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        writer = DescriptorWriter(root_fd, storage_guard=guard)
    finally:
        os.close(root_fd)
    writer.assert_storage_binding = lambda supplied: None  # type: ignore[method-assign]
    return writer


def _tree_policy_file(path: Path) -> tuple[Path, str]:
    policy = _validate_policy(
        {
            "schema": TREE_POLICY_SCHEMA,
            "policy_id": "root-set-fixture",
            "version": 1,
            "rule_set": "owner-work-sensitive-v1",
            "pem_candidate_dispositions": {},
            "exact_path_exclusions": {},
            "symlink_target_dispositions": {},
            "rebuild_inputs": [],
        }
    )
    raw = canonical_json(policy)
    path.write_bytes(raw)
    os.chmod(path, 0o600)
    return path, sha256(raw)


def _role_policy(plan: ApprovedDataPlan, plan_hash: str, tree_policy_hash: str) -> dict[str, Any]:
    roles: dict[str, dict[str, Any]] = {}
    for role, declaration in plan.root_roster.items():
        if not declaration.present:
            roles[role] = {
                "state": "unresolved_absence",
                "relative_locator": None,
                "kind": None,
                "unresolved_reason": declaration.unresolved_reason,
                "scope": None,
                "policy_sha256": None,
                "application_consistency": None,
            }
        elif declaration.kind == "directory":
            roles[role] = {
                "state": "capture_directory",
                "relative_locator": declaration.relative_locator,
                "kind": "directory",
                "unresolved_reason": None,
                "scope": "non_protected",
                "policy_sha256": tree_policy_hash,
                "application_consistency": CAPTURE_CONSISTENCY_LITERAL,
            }
        else:
            roles[role] = {
                "state": "unsupported_present_file",
                "relative_locator": declaration.relative_locator,
                "kind": declaration.kind,
                "unresolved_reason": None,
                "scope": None,
                "policy_sha256": None,
                "application_consistency": None,
            }
    return {
        "schema": POLICY_SCHEMA,
        "version": 1,
        "source_plan_sha256": plan_hash,
        "roles": roles,
    }


def _held_view(plan: ApprovedDataPlan, mountpoint: Path, *, synthetic: bool = False) -> HeldDataT:
    view = object.__new__(HeldDataT)
    view.plan = plan
    view.mountpoint = mountpoint
    view.synthetic = synthetic
    view._closed = False
    view.root_fds = {
        role: os.open(
            mountpoint / declaration.relative_locator,
            os.O_RDONLY | (os.O_DIRECTORY if declaration.kind == "directory" else 0),
        )
        for role, declaration in plan.root_roster.items()
        if declaration.present
    }
    view.revalidate = lambda: None
    view.revalidate_mount_fast = lambda: None
    identity = {
        "data_volume_uuid": plan.data_volume_uuid,
        "data_device_identifier": plan.data_device_identifier,
        "data_container_reference": plan.data_container_reference,
        "data_volume_group_id": plan.data_volume_group_id,
        "mounted_fs_type": "apfs",
        "mounted_fsid": [41, 97],
        "mountpoint": str(mountpoint),
        "readonly": True,
        "source": f"/dev/{plan.data_device_identifier}",
    }
    view.result = lambda: {"data": identity}  # type: ignore[method-assign]
    return view


def _close_view(view: HeldDataT) -> None:
    for fd in view.root_fds.values():
        os.close(fd)


def _policy_inputs(
    tmp_path: Path,
    plan: ApprovedDataPlan,
) -> tuple[Path, str, Path, str, dict[str, Path], dict[str, str]]:
    plan_hash = approved_data_plan_sha256(plan)
    tree_path, tree_hash = _tree_policy_file(tmp_path / "tree-policy.json")
    role_policy = _role_policy(plan, plan_hash, tree_hash)
    role_raw = canonical_json(role_policy)
    role_path = tmp_path / "role-policy.json"
    role_path.write_bytes(role_raw)
    os.chmod(role_path, 0o600)
    present_dirs = {
        role
        for role, declaration in plan.root_roster.items()
        if declaration.present and declaration.kind == "directory"
    }
    return (
        role_path,
        sha256(role_raw),
        tree_path,
        tree_hash,
        {role: tree_path for role in present_dirs},
        {role: tree_hash for role in present_dirs},
    )


def _install_fake_capture(monkeypatch: pytest.MonkeyPatch, source_identity: dict[str, Any]) -> None:
    import tools.brain_host_held_data_root_set_capture_v1 as root_set

    def fake_capture(
        _view: HeldDataT,
        writer: DescriptorWriter,
        *,
        role: str,
        expected_policy_sha256: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        capture_id = ("a" if role == "owner_checkout" else "b") * 32
        manifest_hash = ("c" if role == "owner_checkout" else "d") * 64
        relation = {
            "capture_id": capture_id,
            "policy_sha256": expected_policy_sha256,
            "source_data_identity": source_identity,
            "source_relative_locator": f"roots/{role}",
            "source_role": role,
            "unresolved_exclusion_count": 0,
            "external_custody_required_count": 0,
        }
        raw = canonical_json(relation)
        writer.create(f"host-recovery/held-data-tree-custody-v3/{capture_id}.json", raw)
        return {
            "capture_id": capture_id,
            "relation_sha256": sha256(raw),
            "manifest_sha256": manifest_hash,
            "included_entry_count": 2,
            "unresolved_exclusion_count": 0,
            "external_custody_required_count": 0,
        }

    monkeypatch.setattr(root_set, "capture_held_data_root_v3", fake_capture)
    monkeypatch.setattr(
        root_set,
        "verify_held_data_root_relation_v3",
        lambda *_args, **_kwargs: {"verified": True},
    )
    monkeypatch.setattr(
        root_set,
        "verify_tree",
        lambda _writer, capture_id, **_kwargs: {
            "complete": True,
            "manifest_sha256": "c" * 64 if capture_id.startswith("a") else "d" * 64,
            "included_entry_count": 2,
            "unresolved_exclusion_count": 0,
            "external_custody_required_count": 0,
        },
    )


def _capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    present_file: str | None = None,
) -> tuple[dict[str, Any], DescriptorWriter, HeldDataT, dict[str, Any]]:
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint, present_file=present_file)
    (
        role_path,
        role_hash,
        tree_path,
        tree_hash,
        policy_paths,
        policy_hashes,
    ) = _policy_inputs(tmp_path, plan)
    view = _held_view(plan, mountpoint)
    source_identity = view.result()["data"]
    guard = _FakeGuard()
    writer = _writer(tmp_path / "archive", guard)
    _install_fake_capture(monkeypatch, source_identity)
    plan_hash = approved_data_plan_sha256(plan)
    result = capture_held_data_root_set_v1(
        view,
        writer,
        role_policy_path=role_path,
        expected_plan_sha256=plan_hash,
        expected_role_policy_sha256=role_hash,
        role_policy_paths=policy_paths,
        expected_policy_sha256_by_role=policy_hashes,
        storage_guard=guard,
        scratch_root_relative="host-recovery/scratch",
        scratch_budget_bytes_per_role=1024,
        set_id="e" * 32,
    )
    return (
        {
            "plan": plan,
            "plan_hash": plan_hash,
            "role_path": role_path,
            "role_hash": role_hash,
            "tree_hashes": policy_hashes,
            "tree_path": tree_path,
            "tree_hash": tree_hash,
        },
        writer,
        view,
        result,
    )


def test_two_directory_set_publishes_candidate_index_and_verifies_independently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, writer, view, result = _capture(tmp_path, monkeypatch)
    try:
        assert result["schema"] == INDEX_SCHEMA
        assert result["captured_directory_count"] == 2
        assert result["unresolved_role_count"] == len(REQUIRED_ROOT_ROLES) - 2
        assert result["required_root_closure_complete"] is False
        assert result["T_qualified"] is False
        verified = verify_held_data_root_set_v1(
            writer,
            result["set_id"],
            context["plan"],
            storage_guard=writer.storage_guard,
            expected_plan_sha256=context["plan_hash"],
            role_policy_path=context["role_path"],
            expected_role_policy_sha256=context["role_hash"],
            expected_policy_sha256_by_role=context["tree_hashes"],
            expected_set_index_sha256=result["set_index_sha256"],
        )
        assert verified["verified"] is True
        assert verified["captured_directory_count"] == 2
        assert verified["safe_to_erase"] is False
    finally:
        _close_view(view)
        writer.close()


def test_missing_roster_role_fails_before_any_capture_or_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint)
    role_path, _role_hash, _tree_path, _tree_hash, _paths, _hashes = _policy_inputs(tmp_path, plan)
    policy = __import__("json").loads(role_path.read_bytes())
    del policy["roles"]["verifier_worktree"]
    raw = canonical_json(policy)
    role_path.write_bytes(raw)
    guard = _FakeGuard()
    writer = _writer(tmp_path / "archive", guard)
    view = _held_view(plan, mountpoint)
    try:
        with pytest.raises(HeldDataRootSetError, match="role_policy_roster_incomplete"):
            capture_held_data_root_set_v1(
                view,
                writer,
                role_policy_path=role_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_role_policy_sha256=sha256(raw),
                role_policy_paths={},
                expected_policy_sha256_by_role={},
                storage_guard=guard,
                scratch_root_relative="host-recovery/scratch",
                scratch_budget_bytes_per_role=1,
            )
        assert writer.list_names() == ()
    finally:
        _close_view(view)
        writer.close()


def test_all_absent_role_roster_cannot_publish_vacuous_verified_set(
    tmp_path: Path,
) -> None:
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint, present=())
    role_path, role_hash, _tree_path, _tree_hash, paths, hashes = _policy_inputs(tmp_path, plan)
    guard = _FakeGuard()
    writer = _writer(tmp_path / "archive", guard)
    view = _held_view(plan, mountpoint)
    try:
        with pytest.raises(HeldDataRootSetError, match="no_supported_present_directory_roles"):
            capture_held_data_root_set_v1(
                view,
                writer,
                role_policy_path=role_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_role_policy_sha256=role_hash,
                role_policy_paths=paths,
                expected_policy_sha256_by_role=hashes,
                storage_guard=guard,
                scratch_root_relative="host-recovery/scratch",
                scratch_budget_bytes_per_role=1,
            )
        assert writer.list_names() == ()
    finally:
        _close_view(view)
        writer.close()


def test_present_file_role_rejects_whole_set_before_any_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint, present_file="helius_mcp_wrapper")
    role_path, role_hash, _tree_path, _tree_hash, paths, hashes = _policy_inputs(tmp_path, plan)
    guard = _FakeGuard()
    writer = _writer(tmp_path / "archive", guard)
    view = _held_view(plan, mountpoint)
    try:
        with pytest.raises(HeldDataRootSetError, match="unsupported_present_file_role"):
            capture_held_data_root_set_v1(
                view,
                writer,
                role_policy_path=role_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_role_policy_sha256=role_hash,
                role_policy_paths=paths,
                expected_policy_sha256_by_role=hashes,
                storage_guard=guard,
                scratch_root_relative="host-recovery/scratch",
                scratch_budget_bytes_per_role=1,
            )
        assert writer.list_names() == ()
    finally:
        _close_view(view)
        writer.close()


def test_protected_scope_or_application_claim_rejects_before_any_capture(
    tmp_path: Path,
) -> None:
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint)
    role_path, _role_hash, _tree_path, _tree_hash, paths, hashes = _policy_inputs(tmp_path, plan)
    policy = __import__("json").loads(role_path.read_bytes())
    policy["roles"]["owner_checkout"]["scope"] = "protected_opaque"
    raw = canonical_json(policy)
    role_path.write_bytes(raw)
    view = _held_view(plan, mountpoint)
    guard = _FakeGuard()
    writer = _writer(tmp_path / "archive", guard)
    try:
        with pytest.raises(HeldDataRootSetError, match="protected_role_not_supported"):
            capture_held_data_root_set_v1(
                view,
                writer,
                role_policy_path=role_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_role_policy_sha256=sha256(raw),
                role_policy_paths=paths,
                expected_policy_sha256_by_role=hashes,
                storage_guard=guard,
                scratch_root_relative="host-recovery/scratch",
                scratch_budget_bytes_per_role=1,
            )
        assert writer.list_names() == ()
    finally:
        _close_view(view)
        writer.close()

    consistency_dir = tmp_path / "consistency"
    consistency_dir.mkdir()
    role_path, _role_hash, _tree_path, _tree_hash, paths, hashes = _policy_inputs(
        consistency_dir, plan
    )
    policy = __import__("json").loads(role_path.read_bytes())
    policy["roles"]["owner_checkout"]["application_consistency"] = "consistent_sqlite"
    raw = canonical_json(policy)
    role_path.write_bytes(raw)
    second_view = _held_view(plan, mountpoint)
    second_writer = _writer(tmp_path / "archive-consistency", guard)
    try:
        with pytest.raises(
            HeldDataRootSetError,
            match="application_consistency_must_remain_unqualified",
        ):
            capture_held_data_root_set_v1(
                second_view,
                second_writer,
                role_policy_path=role_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_role_policy_sha256=sha256(raw),
                role_policy_paths=paths,
                expected_policy_sha256_by_role=hashes,
                storage_guard=guard,
                scratch_root_relative="host-recovery/scratch",
                scratch_budget_bytes_per_role=1,
            )
        assert second_writer.list_names() == ()
    finally:
        _close_view(second_view)
        second_writer.close()


def test_cross_root_hardlink_rejects_set_before_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint)
    left = mountpoint / plan.root_roster["owner_checkout"].relative_locator / "payload.bin"
    left.write_bytes(b"same inode")
    right = mountpoint / plan.root_roster["research_checkout"].relative_locator / "payload.bin"
    os.link(left, right)
    _role_path, _role_hash, _tree_path, _tree_hash, _paths, _hashes = _policy_inputs(tmp_path, plan)
    view = _held_view(plan, mountpoint)
    guard = _FakeGuard()
    writer = _writer(tmp_path / "archive", guard)
    try:
        with pytest.raises(HeldDataRootSetError, match="cross_root_hardlink_unsupported"):
            # The scanner runs before the capture adapter is invoked.
            capture_held_data_root_set_v1(
                view,
                writer,
                role_policy_path=_role_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_role_policy_sha256=_role_hash,
                role_policy_paths=_paths,
                expected_policy_sha256_by_role=_hashes,
                storage_guard=guard,
                scratch_root_relative="host-recovery/scratch",
                scratch_budget_bytes_per_role=1,
            )
        assert writer.list_names() == ()
    finally:
        _close_view(view)
        writer.close()


def test_cross_root_directory_alias_rejects_set_before_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint)
    _role_path, _role_hash, _tree_path, _tree_hash, _paths, _hashes = _policy_inputs(tmp_path, plan)
    view = _held_view(plan, mountpoint)
    os.close(view.root_fds["research_checkout"])
    view.root_fds["research_checkout"] = os.open(
        mountpoint / plan.root_roster["owner_checkout"].relative_locator,
        os.O_RDONLY | os.O_DIRECTORY,
    )
    guard = _FakeGuard()
    writer = _writer(tmp_path / "archive", guard)
    try:
        with pytest.raises(
            HeldDataRootSetError,
            match="cross_root_directory_alias_unsupported",
        ):
            capture_held_data_root_set_v1(
                view,
                writer,
                role_policy_path=_role_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_role_policy_sha256=_role_hash,
                role_policy_paths=_paths,
                expected_policy_sha256_by_role=_hashes,
                storage_guard=guard,
                scratch_root_relative="host-recovery/scratch",
                scratch_budget_bytes_per_role=1,
            )
        assert writer.list_names() == ()
    finally:
        _close_view(view)
        writer.close()


def test_external_hardlink_alias_rejects_set_before_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint, present=("owner_checkout",))
    source = mountpoint / plan.root_roster["owner_checkout"].relative_locator / "payload.bin"
    source.write_bytes(b"outside alias")
    os.link(source, tmp_path / "outside-alias.bin")
    _role_path, _role_hash, _tree_path, _tree_hash, paths, hashes = _policy_inputs(tmp_path, plan)
    view = _held_view(plan, mountpoint)
    guard = _FakeGuard()
    writer = _writer(tmp_path / "archive", guard)
    try:
        with pytest.raises(HeldDataRootSetError, match="hardlink_alias_outside_selected_roots"):
            capture_held_data_root_set_v1(
                view,
                writer,
                role_policy_path=_role_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_role_policy_sha256=_role_hash,
                role_policy_paths=paths,
                expected_policy_sha256_by_role=hashes,
                storage_guard=guard,
                scratch_root_relative="host-recovery/scratch",
                scratch_budget_bytes_per_role=1,
            )
        assert writer.list_names() == ()
    finally:
        _close_view(view)
        writer.close()


def test_hardlink_preflight_does_not_descend_into_excluded_private_subtree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.brain_host_held_data_root_set_capture_v1 as root_set

    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint, present=("owner_checkout",))
    private = mountpoint / "roots/owner_checkout/wallets"
    (private / "deep").mkdir(parents=True)
    (private / "deep/opaque.bin").write_bytes(b"private fixture")
    view = _held_view(plan, mountpoint)
    _role_path, _role_hash, tree_path, tree_hash, _paths, _hashes = _policy_inputs(tmp_path, plan)
    policy, _ = root_set._read_policy_pin(tree_path, tree_hash)
    seen: list[str] = []
    original = root_set._classify_path

    def record(path: str, kind: str, pinned: dict[str, Any], **kwargs: Any) -> Any:
        seen.append(path)
        return original(path, kind, pinned, **kwargs)

    monkeypatch.setattr(root_set, "_classify_path", record)
    try:
        root_set._scan_hardlinks(view, ["owner_checkout"], {"owner_checkout": policy})
        assert "wallets" in seen
        assert all(not path.startswith("wallets/") for path in seen)
    finally:
        _close_view(view)


def test_included_file_hardlinked_into_excluded_subtree_fails_preflight(
    tmp_path: Path,
) -> None:
    import tools.brain_host_held_data_root_set_capture_v1 as root_set

    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint, present=("owner_checkout",))
    owner = mountpoint / "roots/owner_checkout"
    included = owner / "payload.bin"
    included.write_bytes(b"shared fixture")
    wallets = owner / "wallets"
    wallets.mkdir()
    os.link(included, wallets / "alias.bin")
    _role_path, _role_hash, tree_path, tree_hash, _paths, _hashes = _policy_inputs(tmp_path, plan)
    policy, _ = root_set._read_policy_pin(tree_path, tree_hash)
    view = _held_view(plan, mountpoint)
    try:
        with pytest.raises(HeldDataRootSetError, match="hardlink_alias_outside_selected_roots"):
            root_set._scan_hardlinks(view, ["owner_checkout"], {"owner_checkout": policy})
    finally:
        _close_view(view)


def test_special_file_fails_all_role_preflight_before_capture(
    tmp_path: Path,
) -> None:
    import tools.brain_host_held_data_root_set_capture_v1 as root_set

    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint, present=("owner_checkout",))
    os.mkfifo(mountpoint / "roots/owner_checkout/unclassified.pipe")
    _role_path, _role_hash, tree_path, tree_hash, _paths, _hashes = _policy_inputs(tmp_path, plan)
    policy, _ = root_set._read_policy_pin(tree_path, tree_hash)
    view = _held_view(plan, mountpoint)
    try:
        with pytest.raises(
            HeldDataRootSetError,
            match="hardlink_preflight_unsupported_entry_type",
        ):
            root_set._scan_hardlinks(view, ["owner_checkout"], {"owner_checkout": policy})
    finally:
        _close_view(view)


def test_wide_directory_preflight_uses_bounded_open_descriptors(tmp_path: Path) -> None:
    import tools.brain_host_held_data_root_set_capture_v1 as root_set

    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint, present=("owner_checkout",))
    root = mountpoint / "roots/owner_checkout"
    for index in range(1200):
        (root / f"directory-{index:04d}").mkdir()
    _role_path, _role_hash, tree_path, tree_hash, _paths, _hashes = _policy_inputs(tmp_path, plan)
    policy, _ = root_set._read_policy_pin(tree_path, tree_hash)
    view = _held_view(plan, mountpoint)
    try:
        root_set._scan_hardlinks(view, ["owner_checkout"], {"owner_checkout": policy})
    finally:
        _close_view(view)


def test_unresolved_exclusion_prevents_set_index_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.brain_host_held_data_root_set_capture_v1 as root_set

    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint, present=("owner_checkout",))
    role_path, role_hash, _tree_path, _tree_hash, paths, hashes = _policy_inputs(tmp_path, plan)
    view = _held_view(plan, mountpoint)
    guard = _FakeGuard()
    writer = _writer(tmp_path / "archive", guard)
    source_identity = view.result()["data"]
    _install_fake_capture(monkeypatch, source_identity)
    original = root_set.capture_held_data_root_v3

    def unresolved(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original(*args, **kwargs)
        result["unresolved_exclusion_count"] = 1
        return result

    monkeypatch.setattr(root_set, "capture_held_data_root_v3", unresolved)
    try:
        with pytest.raises(HeldDataRootSetError, match="capture_set_role_failed:owner_checkout"):
            capture_held_data_root_set_v1(
                view,
                writer,
                role_policy_path=role_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_role_policy_sha256=role_hash,
                role_policy_paths=paths,
                expected_policy_sha256_by_role=hashes,
                storage_guard=guard,
                scratch_root_relative="host-recovery/scratch",
                scratch_budget_bytes_per_role=1,
            )
        assert not any(name.startswith(f"{SET_BASE}/") for name in writer.list_names())
    finally:
        _close_view(view)
        writer.close()


@pytest.mark.parametrize(
    ("count_field", "error"),
    [
        ("unresolved_exclusion_count", "set_index_archive_relation_mismatch"),
        ("external_custody_required_count", "set_index_archive_relation_mismatch"),
    ],
)
def test_fresh_verifier_rejects_archive_count_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    count_field: str,
    error: str,
) -> None:
    import tools.brain_host_held_data_root_set_capture_v1 as root_set

    context, writer, view, result = _capture(tmp_path, monkeypatch)
    original = root_set.verify_tree

    def drifted(*args: Any, **kwargs: Any) -> dict[str, Any]:
        archive = original(*args, **kwargs)
        archive[count_field] = 1
        return archive

    monkeypatch.setattr(root_set, "verify_tree", drifted)
    try:
        with pytest.raises(HeldDataRootSetError, match=error):
            verify_held_data_root_set_v1(
                writer,
                result["set_id"],
                context["plan"],
                storage_guard=writer.storage_guard,
                expected_plan_sha256=context["plan_hash"],
                role_policy_path=context["role_path"],
                expected_role_policy_sha256=context["role_hash"],
                expected_policy_sha256_by_role=context["tree_hashes"],
                expected_set_index_sha256=result["set_index_sha256"],
            )
    finally:
        _close_view(view)
        writer.close()


def test_partial_capture_never_publishes_a_set_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.brain_host_held_data_root_set_capture_v1 as root_set

    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint)
    role_path, role_hash, _tree_path, tree_hash, paths, hashes = _policy_inputs(tmp_path, plan)
    view = _held_view(plan, mountpoint)
    guard = _FakeGuard()
    writer = _writer(tmp_path / "archive", guard)
    source_identity = view.result()["data"]
    calls = 0

    def fail_second(*_args: object, **_kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("fixture_second_role_failure")
        capture_id = "a" * 32
        manifest_hash = "c" * 64
        relation = {
            "capture_id": capture_id,
            "policy_sha256": tree_hash,
            "source_data_identity": source_identity,
            "source_relative_locator": "roots/owner_checkout",
            "source_role": "owner_checkout",
            "unresolved_exclusion_count": 0,
            "external_custody_required_count": 0,
        }
        raw = canonical_json(relation)
        writer.create(f"host-recovery/held-data-tree-custody-v3/{capture_id}.json", raw)
        return {
            "capture_id": capture_id,
            "relation_sha256": sha256(raw),
            "manifest_sha256": manifest_hash,
            "included_entry_count": 1,
            "unresolved_exclusion_count": 0,
            "external_custody_required_count": 0,
        }

    monkeypatch.setattr(root_set, "capture_held_data_root_v3", fail_second)
    monkeypatch.setattr(
        root_set,
        "verify_held_data_root_relation_v3",
        lambda *_args, **_kwargs: {"verified": True},
    )
    monkeypatch.setattr(
        root_set,
        "verify_tree",
        lambda *_args, **_kwargs: {
            "complete": True,
            "manifest_sha256": "c" * 64,
            "included_entry_count": 1,
        },
    )
    try:
        with pytest.raises(HeldDataRootSetError, match="no_set_index_published"):
            capture_held_data_root_set_v1(
                view,
                writer,
                role_policy_path=role_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_role_policy_sha256=role_hash,
                role_policy_paths=paths,
                expected_policy_sha256_by_role=hashes,
                storage_guard=guard,
                scratch_root_relative="host-recovery/scratch",
                scratch_budget_bytes_per_role=1,
                set_id="f" * 32,
            )
        with pytest.raises(OSError):
            writer.read_bytes(f"{SET_BASE}/{'f' * 32}.json")
        assert writer.read_bytes(f"host-recovery/held-data-tree-custody-v3/{'a' * 32}.json")
    finally:
        _close_view(view)
        writer.close()


@pytest.mark.parametrize("failure", ["source_identity", "storage_guard"])
def test_mid_set_identity_or_guard_change_never_publishes_set_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    mountpoint = tmp_path / "held-data"
    mountpoint.mkdir()
    plan = _plan(mountpoint)
    role_path, role_hash, _tree_path, tree_hash, paths, hashes = _policy_inputs(tmp_path, plan)
    view = _held_view(plan, mountpoint)
    guard = _FakeGuard()
    writer = _writer(tmp_path / "archive", guard)
    source_identity = view.result()["data"]
    _install_fake_capture(monkeypatch, source_identity)
    if failure == "source_identity":
        revalidations = 0

        def fail_revalidation() -> None:
            nonlocal revalidations
            revalidations += 1
            if revalidations == 8:
                raise RuntimeError("fixture_source_identity_changed")

        view.revalidate = fail_revalidation
    else:
        binding_checks = 0

        def fail_binding(supplied: object) -> None:
            nonlocal binding_checks
            assert supplied is guard
            binding_checks += 1
            if binding_checks == 5:
                raise RuntimeError("fixture_storage_guard_changed")

        writer.assert_storage_binding = fail_binding  # type: ignore[method-assign]
    try:
        with pytest.raises(HeldDataRootSetError, match="no_set_index_published"):
            capture_held_data_root_set_v1(
                view,
                writer,
                role_policy_path=role_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_role_policy_sha256=role_hash,
                role_policy_paths=paths,
                expected_policy_sha256_by_role=hashes,
                storage_guard=guard,
                scratch_root_relative="host-recovery/scratch",
                scratch_budget_bytes_per_role=1,
                set_id="9" * 32,
            )
        with pytest.raises(OSError):
            writer.read_bytes(f"{SET_BASE}/{'9' * 32}.json")
        assert writer.read_bytes(f"host-recovery/held-data-tree-custody-v3/{'a' * 32}.json")
    finally:
        _close_view(view)
        writer.close()
