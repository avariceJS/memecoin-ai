from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from tests import test_brain_host_recovery_set as set_fixtures
from tests import test_brain_host_source_view_proof_v1 as proof_fixtures
from tools.brain_host_source_view_adapter_v1 import (
    HeldSyntheticView,
    SourceViewAdapterError,
    SyntheticViewPlan,
    reconcile_synthetic_capture,
)


def _plan(tmp_path: Path) -> SyntheticViewPlan:
    return SyntheticViewPlan(
        fixture_id="source-view-adapter-test-fixture-v1",
        view_id="synthetic-held-view-0001",
        view_root=tmp_path,
        source_volume_uuid="synthetic-source-volume",
        source_root_id="8" * 32,
        root_locators={
            "owner_work": "owner-work",
            "research_work": "research-work",
            "private_config": "private-config",
            "launchagent_definitions": "launch-defs",
            "protected_d43": "owner-work/calendar-execution-01",
            "sqlite_backup": "owner-work",
        },
    )


def _assessment(
    tmp_path: Path, proof: dict, writer, *, plan: SyntheticViewPlan | None = None
) -> dict:
    return reconcile_synthetic_capture(
        writer,
        plan or _plan(tmp_path),
        proof,
        expected_volume_uuid=set_fixtures.UUID,
        expected_root_id=set_fixtures.ROOT_ID,
        expected_source_volume_uuid="synthetic-source-volume",
        expected_source_root_id="8" * 32,
        revalidate_identity=set_fixtures._identity,
    )


def test_synthetic_adapter_reconciles_declared_roots_but_never_qualifies_t(
    tmp_path: Path,
) -> None:
    writer, proof, _source_roots, _members = proof_fixtures._proof(tmp_path)
    try:
        result = _assessment(tmp_path, proof, writer)
        assert result["schema"] == "brain-host-source-view-adapter-v1"
        assert result["synthetic_tree_root_reconciliation_verified"] is True
        assert result["capture_was_descriptor_sourced"] is False
        assert result["apfs_snapshot_attested"] is False
        assert result["T_qualified"] is False
        assert result["host_closure"] is False
        assert result["erase_ready"] is False
        assert result["member_count"] == 6
        assert result["declared_unknown_count"] == 1
        assert result["credential_closure"] == "unknown_no_exact_credential_scope_declared"
        assert result["root_bindings"]["owner_work"]["locator_matches_capture"] is True
        assert result["root_bindings"]["protected_d43"]["locator_matches_capture"] is None
        assert (
            result["root_bindings"]["protected_d43"]["capture_relation"]
            == "artifact_verified_source_unbound"
        )
        assert (
            result["root_bindings"]["sqlite_backup"]["capture_relation"]
            == "artifact_verified_source_unbound"
        )
        assert all(
            row["capture_relation"] == "post_capture_path_reconciliation"
            for role, row in result["root_bindings"].items()
            if role in {"owner_work", "research_work", "private_config", "launchagent_definitions"}
        )
        assert len(result["evidence_sha256"]) == 64
    finally:
        writer.close()


def test_adapter_reconciles_exact_reissue_only_credential_exclusion(tmp_path: Path) -> None:
    writer, proof, _source_roots, _members = proof_fixtures._proof(
        tmp_path, extra_owner_files={".env.local": b"synthetic-only opaque credential bytes"}
    )
    plan = replace(_plan(tmp_path), credential_reissue_only=(("owner_work", ".env.local"),))
    try:
        result = _assessment(tmp_path, proof, writer, plan=plan)
        assert result["credential_closure"] == "declared_exact_credential_exclusions_verified"
        assert result["credential_scope_complete"] is False
        assert result["credential_dispositions"][0]["state"] == "excluded_exact_file_reissue_only"
        assert "relative_path" not in result["credential_dispositions"][0]
    finally:
        writer.close()


def test_adapter_rejects_credential_locator_included_in_capture(tmp_path: Path) -> None:
    writer, proof, _source_roots, _members = proof_fixtures._proof(
        tmp_path, extra_owner_files={"private-token.dat": b"synthetic dummy bytes"}
    )
    plan = replace(_plan(tmp_path), credential_reissue_only=(("owner_work", "private-token.dat"),))
    try:
        with pytest.raises(SourceViewAdapterError, match="credential_payload_included"):
            _assessment(tmp_path, proof, writer, plan=plan)
    finally:
        writer.close()


def test_adapter_rejects_symlink_view_ancestor(tmp_path: Path) -> None:
    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    writer, proof, _source_roots, _members = proof_fixtures._proof(fixture_root)
    try:
        alias_parent = tmp_path / "view-parent-alias"
        alias_parent.symlink_to(tmp_path, target_is_directory=True)
        plan = _plan(fixture_root)
        bad = replace(plan, view_root=alias_parent / "fixture")
        with pytest.raises(SourceViewAdapterError, match="view_path_open_failed"):
            reconcile_synthetic_capture(
                writer,
                bad,
                proof,
                expected_volume_uuid=set_fixtures.UUID,
                expected_root_id=set_fixtures.ROOT_ID,
                expected_source_volume_uuid="synthetic-source-volume",
                expected_source_root_id="8" * 32,
                revalidate_identity=set_fixtures._identity,
            )
    finally:
        writer.close()


def test_adapter_rejects_declared_root_replacement_and_early_use_after_release(
    tmp_path: Path,
) -> None:
    writer, _proof, _source_roots, _members = proof_fixtures._proof(tmp_path)
    try:
        view = HeldSyntheticView(_plan(tmp_path))
        research = tmp_path / "research-work"
        detached = tmp_path / "research-work-detached"
        research.rename(detached)
        research.mkdir()
        with pytest.raises(SourceViewAdapterError, match="declared_root_replaced"):
            view.revalidate()
        view.close()
        with pytest.raises(SourceViewAdapterError, match="source_view_released"):
            view.revalidate()
    finally:
        writer.close()


def test_adapter_rejects_cross_device_and_malformed_locator(tmp_path: Path) -> None:
    writer, _proof, _source_roots, _members = proof_fixtures._proof(tmp_path)
    try:
        view = HeldSyntheticView(_plan(tmp_path))
        view._device += 1
        with pytest.raises(SourceViewAdapterError, match="declared_root_crosses_device"):
            view.revalidate()
    finally:
        if "view" in locals():
            view.close()
        writer.close()

    invalid = copy.copy(_plan(tmp_path))
    object.__setattr__(
        invalid, "root_locators", {**invalid.root_locators, "owner_work": "../owner-work"}
    )
    with pytest.raises(SourceViewAdapterError, match="root_locator_invalid"):
        HeldSyntheticView(invalid)


@pytest.mark.parametrize("forged", ["scenario", "qualification"])
def test_adapter_rejects_apfs_attestation_or_caller_qualification(
    tmp_path: Path, forged: str
) -> None:
    writer, proof, _source_roots, _members = proof_fixtures._proof(tmp_path)
    try:
        bad = copy.deepcopy(proof)
        if forged == "scenario":
            bad["scenario"] = "apfs_snapshot"
            expected_message = "apfs_snapshot_attestation_unavailable"
        else:
            bad["T_qualified"] = True
            expected_message = "caller_qualification_fields_forbidden"
        with pytest.raises(SourceViewAdapterError, match=expected_message):
            _assessment(tmp_path, bad, writer)
    finally:
        writer.close()
