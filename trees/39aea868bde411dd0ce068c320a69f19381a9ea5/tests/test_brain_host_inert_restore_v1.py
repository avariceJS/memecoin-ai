from __future__ import annotations

from pathlib import Path

import pytest

from tests import test_brain_host_recovery_set as fixtures
from tools.brain_host_inert_restore_v1 import InertRestoreError, restore_inert_v1
from tools.brain_host_recovery_set import REQUIRED_ROLES, WITNESS_BASE, create_set, verify_set
from tools.brain_host_tree_custody_v3 import TreeCustodyV3Error


def _prepared(
    tmp_path: Path,
    *,
    sqlite_disposition: str = "substitute_online_backup",
    extra_owner_files: dict[str, bytes] | None = None,
    owner_symlinks: dict[str, str] | None = None,
    protected_symlink: bool = False,
):
    writer, spec, _database, _sqlite_base = fixtures._setup_set(
        tmp_path,
        sqlite_disposition=sqlite_disposition,
        extra_owner_files=extra_owner_files,
        owner_symlinks=owner_symlinks,
        protected_symlink=protected_symlink,
    )
    result = create_set(
        writer,
        spec,
        expected_volume_uuid=fixtures.UUID,
        expected_root_id=fixtures.ROOT_ID,
        revalidate_identity=fixtures._identity,
    )
    target_parent = tmp_path / "incoming"
    target_parent.mkdir(mode=0o700)
    repo = tmp_path / "repo"
    repo.mkdir(mode=0o700)
    return writer, spec, result["index_sha256"], target_parent, repo


def _restore(writer, spec, index_sha256, target_parent, repo, **kwargs):
    return restore_inert_v1(
        writer,
        set_id=spec["set_id"],
        expected_index_sha256=index_sha256,
        expected_volume_uuid=fixtures.UUID,
        expected_root_id=fixtures.ROOT_ID,
        revalidate_identity=fixtures._identity,
        target_parent=target_parent,
        repository_root=repo,
        storage_root=repo.parent / "archive",
        forbidden_roots=(repo.parent / "owner-work",),
        require_separate_device=False,
        **kwargs,
    )


def test_conductor_restores_six_roles_and_emits_blocked_assessor_handoff(
    tmp_path: Path,
) -> None:
    writer, spec, index_sha256, target_parent, repo = _prepared(tmp_path)
    try:
        result = _restore(writer, spec, index_sha256, target_parent, repo, witness_id="9" * 32)
        payload = result.payload
        assert payload["positive_claim"] == "inert_artifacts_restored_and_byte_verified"
        assert payload["member_count"] == 6
        assert set(payload["role_targets"]) == REQUIRED_ROLES
        assert payload["target_identity"]["mode"] == 0o700
        assert payload["restore_witness_bound"] is True
        assert payload["coverage_resolved"] is True
        assert payload["application_recovery_qualified"] is False
        assert payload["T_qualified"] is False
        assert payload["K_frontier_qualified"] is False
        assert payload["host_closure"] is False
        assert payload["safe_to_erase"] is False
        assert payload["services_changed"] is False
        assert payload["provider_requests_sent"] == 0
        assert payload["assessor_handoff"]["status"] == "blocked"
        assert payload["assessor_handoff"]["expected_index_sha256"] == index_sha256
        assert "calendar-execution-01" not in str(payload)
        verified = verify_set(
            writer,
            spec["set_id"],
            expected_volume_uuid=fixtures.UUID,
            expected_root_id=fixtures.ROOT_ID,
            revalidate_identity=fixtures._identity,
            witness_id="9" * 32,
            witness_targets={role: Path(path) for role, path in payload["role_targets"].items()},
        )
        assert verified["restore_witness_bound"] is True
    finally:
        writer.close()


def test_wrong_independent_pin_fails_before_creating_target(tmp_path: Path) -> None:
    writer, spec, _index_sha256, target_parent, repo = _prepared(tmp_path)
    try:
        with pytest.raises(InertRestoreError, match="independent_index_pin_mismatch"):
            _restore(writer, spec, "0" * 64, target_parent, repo)
        assert list(target_parent.iterdir()) == []
    finally:
        writer.close()


def test_owner_source_root_is_rejected_before_quarantine_creation(tmp_path: Path) -> None:
    writer, spec, index_sha256, _target_parent, repo = _prepared(tmp_path)
    owner_root = tmp_path / "owner-work"
    nested_target = owner_root / "incoming"
    nested_target.mkdir(mode=0o700)
    try:
        with pytest.raises(InertRestoreError, match="target_parent_inside_forbidden_root"):
            restore_inert_v1(
                writer,
                set_id=spec["set_id"],
                expected_index_sha256=index_sha256,
                expected_volume_uuid=fixtures.UUID,
                expected_root_id=fixtures.ROOT_ID,
                revalidate_identity=fixtures._identity,
                target_parent=nested_target,
                repository_root=repo,
                storage_root=repo.parent / "archive",
                forbidden_roots=(owner_root,),
                require_separate_device=False,
            )
        assert list(nested_target.iterdir()) == []
    finally:
        writer.close()


def test_protected_component_file_exclusion_blocks_restore_before_target_creation(
    tmp_path: Path,
) -> None:
    writer, spec, index_sha256, target_parent, repo = _prepared(
        tmp_path,
        extra_owner_files={"F01": b"synthetic protected marker bytes"},
    )
    try:
        with pytest.raises(InertRestoreError, match="owner_work_protected_scope_exclusion_unbound"):
            _restore(writer, spec, index_sha256, target_parent, repo)
        assert list(target_parent.iterdir()) == []
    finally:
        writer.close()


def test_absolute_intra_root_symlink_is_rejected_before_restore_target_creation(
    tmp_path: Path,
) -> None:
    source_root = (tmp_path / "owner-work").resolve()
    writer, spec, index_sha256, target_parent, repo = _prepared(
        tmp_path,
        extra_owner_files={"absolute-target": b"synthetic target"},
        owner_symlinks={"absolute-link": str(source_root / "absolute-target")},
    )
    try:
        with pytest.raises(InertRestoreError, match="owner_work_absolute_symlink_unrebased"):
            _restore(writer, spec, index_sha256, target_parent, repo)
        assert list(target_parent.iterdir()) == []
    finally:
        writer.close()


def test_protected_member_symlink_blocks_restore_before_target_creation(
    tmp_path: Path,
) -> None:
    writer, spec, index_sha256, target_parent, repo = _prepared(tmp_path, protected_symlink=True)
    try:
        with pytest.raises(InertRestoreError, match="protected_d43_symlinks_unrebased"):
            _restore(writer, spec, index_sha256, target_parent, repo)
        assert list(target_parent.iterdir()) == []
    finally:
        writer.close()


def test_unresolved_set_is_refused_before_creating_target(tmp_path: Path) -> None:
    writer, spec, _index_sha256, target_parent, repo = _prepared(
        tmp_path, sqlite_disposition="disposable_not_required"
    )
    try:
        # Recreate with a fresh set ID because the fixture set was already published.
        spec["set_id"] = "0" * 32
        result = create_set(
            writer,
            spec,
            expected_volume_uuid=fixtures.UUID,
            expected_root_id=fixtures.ROOT_ID,
            revalidate_identity=fixtures._identity,
        )
        with pytest.raises(InertRestoreError, match="recovery_set_obligations_unresolved"):
            _restore(writer, spec, result["index_sha256"], target_parent, repo)
        assert list(target_parent.iterdir()) == []
    finally:
        writer.close()


def test_target_replacement_is_rejected_and_partial_quarantine_retained(
    tmp_path: Path,
) -> None:
    writer, spec, index_sha256, target_parent, repo = _prepared(tmp_path)
    try:

        def replace_after_owner(stage: str) -> None:
            if stage != "after_owner_work":
                return
            root = next(target_parent.iterdir())
            moved = target_parent / "detached-quarantine"
            root.rename(moved)
            root.symlink_to(moved, target_is_directory=True)

        with pytest.raises(InertRestoreError, match="quarantine_root_replaced"):
            _restore(
                writer,
                spec,
                index_sha256,
                target_parent,
                repo,
                fault_hook=replace_after_owner,
            )
        assert (target_parent / "detached-quarantine").is_dir()
        assert (target_parent / next(target_parent.glob("brain-restore-*")).name).is_symlink()
    finally:
        writer.close()


def test_partial_failure_is_left_in_quarantine_without_resume(tmp_path: Path) -> None:
    writer, spec, index_sha256, target_parent, repo = _prepared(tmp_path)
    try:

        def stop_after_owner(stage: str) -> None:
            if stage == "after_owner_work":
                raise RuntimeError("injected extraction interruption")

        with pytest.raises(InertRestoreError, match="quarantine_retained="):
            _restore(
                writer,
                spec,
                index_sha256,
                target_parent,
                repo,
                fault_hook=stop_after_owner,
            )
        root = next(target_parent.glob("brain-restore-*"))
        assert (root / "owner_work").is_dir()
        assert not (root / "research_work").exists()
    finally:
        writer.close()


def test_witness_failure_leaves_restored_quarantine_inert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer, spec, index_sha256, target_parent, repo = _prepared(tmp_path)
    try:
        import tools.brain_host_recovery_set as recovery_set

        original_verify_witness = recovery_set._verify_restore_witness

        def fail_during_witness(*args, **kwargs):
            if kwargs.get("require_complete") is False:
                raise RuntimeError("injected witness verification failure")
            return original_verify_witness(*args, **kwargs)

        monkeypatch.setattr(recovery_set, "_verify_restore_witness", fail_during_witness)

        with pytest.raises(InertRestoreError, match="quarantine_retained="):
            _restore(
                writer,
                spec,
                index_sha256,
                target_parent,
                repo,
                witness_id="8" * 32,
            )
        assert writer.exists(f"{WITNESS_BASE}/{spec['set_id']}/{'8' * 32}/WITNESS.json")
        assert not writer.exists(f"{WITNESS_BASE}/{spec['set_id']}/{'8' * 32}/COMPLETE.json")
        assert len(list(target_parent.glob("brain-restore-*"))) == 1
    finally:
        writer.close()


def test_missing_artifact_fails_preflight_without_target_creation(tmp_path: Path) -> None:
    writer, spec, index_sha256, target_parent, repo = _prepared(tmp_path)
    try:
        member = next(item for item in spec["members"] if item["role"] == "research_work")
        writer.unlink(f"host-recovery/tree-custody-v3/{member['artifact_id']}/COMPLETE.json")
        with pytest.raises(TreeCustodyV3Error, match="complete_missing"):
            _restore(writer, spec, index_sha256, target_parent, repo)
        assert list(target_parent.iterdir()) == []
    finally:
        writer.close()
