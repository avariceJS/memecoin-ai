from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from tools.brain_host_absence_gap_chain_v2 import (
    CAPACITY_SCHEMA,
    CAPTURE_SCHEMA,
    INDEX_SCHEMA,
    INTENT_SCHEMA,
    POLICY_SCHEMA,
    AbsenceGapChainError,
    policy_roster_v2_sha256,
    validate_absence_gap_chain_v2,
    validate_policy_roster_v2,
)
from tools.brain_host_held_data_absence_witness_v2 import (
    EXPECTED_COMMIT,
    HISTORICAL_STATE,
    PLAN_SCHEMA,
    AbsenceWitnessError,
    load_absence_gap_plan_v2,
)
from tools.brain_host_held_data_t_v1 import REQUIRED_ROOT_ROLES, ROOT_KINDS
from tools.brain_host_tree_capture_v5 import TreeCaptureV5Error, policy_roster_sha256_v5
from tools.brain_host_tree_capture_v5_production import (
    ProductionCaptureV5CLIError,
    _capacity_intent_fields,
    _capacity_receipt_fields,
    _capture_spec_fields,
)
from tools.brain_senses_external_writer_v1 import canonical_json

UUID = "430740ca-42e7-4e6c-a07a-e4e7370152cc"
GROUP = "b72c52b0-591e-4c21-b4d2-87e6d7679c3a"


def _seal(value: dict[str, Any], field: str) -> dict[str, Any]:
    value[field] = hashlib.sha256(canonical_json(value)).hexdigest()
    return value


def _plan(tmp_path: Path):
    root_roster: dict[str, dict[str, Any]] = {}
    for role in sorted(REQUIRED_ROOT_ROLES):
        if role == "verifier_worktree":
            root_roster[role] = {
                "present": False,
                "relative_locator": None,
                "kind": None,
                "unresolved_reason": "verifier_worktree_absent_unresolved",
            }
        else:
            root_roster[role] = {
                "present": True,
                "relative_locator": f"roots/{role}",
                "kind": ROOT_KINDS[role],
                "unresolved_reason": None,
            }
    document = {
        "schema": PLAN_SCHEMA,
        "approval_id": "synthetic-capacity-worksheet",
        "data_volume_uuid": UUID,
        "data_device_identifier": "disk3s5",
        "data_container_reference": "disk3",
        "data_volume_group_id": GROUP,
        "root_roster": root_roster,
        "verifier_worktree_locator": "roots/verifier_worktree",
        "expected_fetchable_commit": EXPECTED_COMMIT,
        "remote_fetchability_evidence_sha256": "a" * 64,
        "historical_non_git_state": HISTORICAL_STATE,
    }
    path = tmp_path / "pinned-plan-v2.json"
    path.write_bytes(canonical_json(document))
    path.chmod(0o600)
    plan_sha = hashlib.sha256(canonical_json(document)).hexdigest()
    return load_absence_gap_plan_v2(path, expected_plan_sha256=plan_sha)


def _witness(plan) -> dict[str, Any]:
    witness: dict[str, Any] = {
        "schema": "brain-host-held-data-absence-witness-v2",
        "role": "verifier_worktree",
        "relative_locator": plan.verifier_worktree_locator,
        "absence_plan_v2_sha256": plan.plan_sha256,
        "held_data_plan_sha256": plan.held_data_plan_sha256,
        "data_volume_uuid": plan.document["data_volume_uuid"],
        "data_device_identifier": plan.document["data_device_identifier"],
        "data_volume_group_id": plan.document["data_volume_group_id"],
        "parent_identity_chain": [{"device": 1, "inode": 2}],
        "leaf_observation": "source_absent_on_held_readonly_view",
        "expected_fetchable_commit": EXPECTED_COMMIT,
        "remote_fetchability_evidence_sha256": plan.document["remote_fetchability_evidence_sha256"],
        "historical_non_git_state": HISTORICAL_STATE,
        "synthetic_backend": False,
        "root_closure": False,
        "full_restore_qualified": False,
        "activation_authorized": False,
        "erase_ready": False,
        "T_qualified": False,
        "K_qualified": False,
    }
    return _seal(witness, "witness_sha256")


def _chain(tmp_path: Path, *, transition_confirmed: bool = True):
    plan = _plan(tmp_path)
    witness = _witness(plan)
    roles = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        if role == "verifier_worktree":
            roles.append(
                {
                    "role": role,
                    "present": False,
                    "disposition": "nonqualifying_absence_gap",
                    "witness_sha256": witness["witness_sha256"],
                    "relative_locator": witness["relative_locator"],
                    "expected_fetchable_commit": EXPECTED_COMMIT,
                    "historical_non_git_state": HISTORICAL_STATE,
                    "scope": None,
                    "policy_sha256": None,
                }
            )
        else:
            roles.append(
                {
                    "role": role,
                    "present": True,
                    "scope": "non_protected",
                    "policy_sha256": "b" * 64,
                }
            )
    policy_sha = policy_roster_v2_sha256(
        plan_sha256=plan.plan_sha256,
        witness_sha256=witness["witness_sha256"],
        roles=roles,
    )
    policy = {
        "schema": POLICY_SCHEMA,
        "plan_sha256": plan.plan_sha256,
        "witness_sha256": witness["witness_sha256"],
        "roles": roles,
        "root_closure": False,
        "T_qualified": False,
        "K_qualified": False,
        "full_restore_qualified": False,
        "activation_authorized": False,
        "erase_ready": False,
        "policy_roster_sha256": policy_sha,
    }
    intent = {
        "schema": INTENT_SCHEMA,
        "plan_sha256": plan.plan_sha256,
        "witness_sha256": witness["witness_sha256"],
        "policy_roster_sha256": policy_sha,
        "R_safety_floor_bytes": 50 * 1024**3,
        "G_writer_floor_bytes": 8 * 1024**3,
        "A_apfs_allocation_reserve_bytes": 10 * 1024**3,
        "A_formula_version": "operational-reserve-v1",
        "verifier_mode": "shared_target_pool",
        "S_V_verifier_scratch_reserve_bytes": 32 * 1024**3,
        "W_V_verifier_headroom_bytes": 256 * 1024**2,
        "X_shared_pool_margin_bytes": 0,
        "metadata_reserve_bytes": 0,
        "target_spool_reserve_bytes": 1024,
        "personal_backup_transition_confirmed": transition_confirmed,
        "personal_backup_transition_receipt_sha256": "c" * 64 if transition_confirmed else None,
        "personal_backup_transition_completed_at_utc": (
            "2026-10-01T12:00:00Z" if transition_confirmed else None
        ),
        "target_volume_uuid": UUID,
        "target_root_id": "f" * 32,
        "root_closure": False,
        "T_qualified": False,
        "K_qualified": False,
        "full_restore_qualified": False,
        "activation_authorized": False,
        "erase_ready": False,
    }
    intent = _seal(intent, "intent_sha256")
    inventory_sha = "d" * 64
    components = {
        "B_payload_logical_bytes": 1024,
        "M_metadata_value_bytes": 0,
        "J_candidate_json_bytes": 1024,
        "S_K_spool_reserved_bytes": 1024,
        "N_new_candidate_entries": 1,
        "R_safety_floor_bytes": intent["R_safety_floor_bytes"],
        "G_writer_floor_bytes": intent["G_writer_floor_bytes"],
        "A_apfs_allocation_reserve_bytes": intent["A_apfs_allocation_reserve_bytes"],
        "S_V_verifier_scratch_reserve_bytes": intent["S_V_verifier_scratch_reserve_bytes"],
        "W_V_verifier_headroom_bytes": intent["W_V_verifier_headroom_bytes"],
        "X_shared_pool_margin_bytes": intent["X_shared_pool_margin_bytes"],
    }
    components["A_min_operational_reserve_bytes"] = (
        8 * 1024**3
        + 32 * 1024
        + (
            components["B_payload_logical_bytes"]
            + components["M_metadata_value_bytes"]
            + components["J_candidate_json_bytes"]
            + components["S_K_spool_reserved_bytes"]
            + 9
        )
        // 10
    )
    required = sum(
        components[field]
        for field in (
            "B_payload_logical_bytes",
            "M_metadata_value_bytes",
            "J_candidate_json_bytes",
            "S_K_spool_reserved_bytes",
            "R_safety_floor_bytes",
            "G_writer_floor_bytes",
            "A_apfs_allocation_reserve_bytes",
            "S_V_verifier_scratch_reserve_bytes",
            "W_V_verifier_headroom_bytes",
            "X_shared_pool_margin_bytes",
        )
    )
    capacity = {
        "schema": CAPACITY_SCHEMA,
        "plan_sha256": plan.plan_sha256,
        "witness_sha256": witness["witness_sha256"],
        "policy_roster_sha256": policy_sha,
        "intent_sha256": intent["intent_sha256"],
        "inventory_sha256": inventory_sha,
        "F_live_available_bytes": required + 1024 if transition_confirmed else 0,
        **components,
        "required_new_free_bytes": required,
        "capacity_gate_satisfied": transition_confirmed,
        "personal_backup_transition_confirmed": transition_confirmed,
        "personal_backup_transition_receipt_sha256": "c" * 64 if transition_confirmed else None,
        "personal_backup_transition_completed_at_utc": (
            "2026-10-01T12:00:00Z" if transition_confirmed else None
        ),
        "kingston_measurement_sha256": "e" * 64 if transition_confirmed else None,
        "kingston_measurement_observed_at_utc": (
            "2026-10-01T12:05:00Z" if transition_confirmed else None
        ),
        "kingston_volume_uuid": UUID if transition_confirmed else None,
        "kingston_root_id": "f" * 32 if transition_confirmed else None,
        "root_closure": False,
        "T_qualified": False,
        "K_qualified": False,
        "full_restore_qualified": False,
        "activation_authorized": False,
        "erase_ready": False,
    }
    capacity = _seal(capacity, "capacity_receipt_sha256")
    capture = {
        "schema": CAPTURE_SCHEMA,
        "plan_sha256": plan.plan_sha256,
        "witness_sha256": witness["witness_sha256"],
        "policy_roster_sha256": policy_sha,
        "intent_sha256": intent["intent_sha256"],
        "capacity_receipt_sha256": capacity["capacity_receipt_sha256"],
        "inventory_sha256": inventory_sha,
        "operation_id": "synthetic-operation",
        "candidate_status": "nonqualifying_absence_gap",
        "personal_backup_transition_confirmed": transition_confirmed,
        "personal_backup_transition_receipt_sha256": capacity[
            "personal_backup_transition_receipt_sha256"
        ],
        "personal_backup_transition_completed_at_utc": capacity[
            "personal_backup_transition_completed_at_utc"
        ],
        "kingston_measurement_sha256": capacity["kingston_measurement_sha256"],
        "kingston_measurement_observed_at_utc": capacity["kingston_measurement_observed_at_utc"],
        "kingston_volume_uuid": capacity["kingston_volume_uuid"],
        "kingston_root_id": capacity["kingston_root_id"],
        "capture_preflight_observed_at_utc": "2026-10-01T12:06:00Z",
        "kingston_measurement_max_age_seconds": 300,
        "root_closure": False,
        "T_qualified": False,
        "K_qualified": False,
        "full_restore_qualified": False,
        "activation_authorized": False,
        "erase_ready": False,
    }
    capture = _seal(capture, "capture_spec_sha256")
    role_dispositions = [
        {
            "role": role,
            "present": True,
            "disposition": "captured_opaque",
        }
        for role in sorted(REQUIRED_ROOT_ROLES - {"verifier_worktree"})
    ]
    role_dispositions.append(
        {
            "role": "verifier_worktree",
            "present": False,
            "disposition": "nonqualifying_absence_gap",
            "witness_sha256": witness["witness_sha256"],
            "relative_locator": witness["relative_locator"],
            "expected_fetchable_commit": EXPECTED_COMMIT,
            "historical_non_git_state": HISTORICAL_STATE,
        }
    )
    index = {
        "schema": INDEX_SCHEMA,
        "plan_sha256": plan.plan_sha256,
        "witness_sha256": witness["witness_sha256"],
        "policy_roster_sha256": policy_sha,
        "intent_sha256": intent["intent_sha256"],
        "capacity_receipt_sha256": capacity["capacity_receipt_sha256"],
        "inventory_sha256": inventory_sha,
        "capture_spec_sha256": capture["capture_spec_sha256"],
        "candidate_payload_manifest_sha256": "9" * 64,
        "candidate_status": "nonqualifying_absence_gap",
        "role_dispositions": role_dispositions,
        "personal_backup_transition_confirmed": transition_confirmed,
        "kingston_measurement_sha256": capacity["kingston_measurement_sha256"],
        "kingston_measurement_observed_at_utc": capacity["kingston_measurement_observed_at_utc"],
        "kingston_volume_uuid": capacity["kingston_volume_uuid"],
        "kingston_root_id": capacity["kingston_root_id"],
        "root_closure": False,
        "T_qualified": False,
        "K_qualified": False,
        "full_restore_qualified": False,
        "activation_authorized": False,
        "erase_ready": False,
    }
    index = _seal(index, "candidate_index_sha256")
    return {
        "plan": plan,
        "witness": witness,
        "policy": policy,
        "intent": intent,
        "capacity_receipt": capacity,
        "capture_spec": capture,
        "candidate_index": index,
    }


def test_full_v2_chain_joins_every_stage_and_keeps_candidate_nonqualifying(tmp_path: Path) -> None:
    chain = _chain(tmp_path)
    result = validate_absence_gap_chain_v2(chain)
    assert list(result) == [
        "plan_sha256",
        "witness_sha256",
        "policy_roster_sha256",
        "intent_sha256",
        "capacity_receipt_sha256",
        "capture_spec_sha256",
        "candidate_index_sha256",
    ]
    assert chain["candidate_index"]["candidate_status"] == "nonqualifying_absence_gap"
    assert chain["candidate_index"]["erase_ready"] is False


def test_capacity_receipt_must_join_intent_budgets(tmp_path: Path) -> None:
    chain = _chain(tmp_path)
    capacity = chain["capacity_receipt"]
    capacity["G_writer_floor_bytes"] += 1
    capacity.pop("capacity_receipt_sha256")
    _seal(capacity, "capacity_receipt_sha256")
    with pytest.raises(AbsenceGapChainError, match="intent_budget_mismatch"):
        validate_absence_gap_chain_v2(chain)


@pytest.mark.parametrize(
    ("field", "replacement", "error"),
    [
        ("required_new_free_bytes", float(1), "capacity_gate_or_policy_invalid"),
        ("A_min_operational_reserve_bytes", float(1), "capacity_allocation_reserve_invalid"),
    ],
)
def test_capacity_byte_totals_reject_float_even_when_integral(
    tmp_path: Path, field: str, replacement: float, error: str
) -> None:
    chain = _chain(tmp_path)
    capacity = chain["capacity_receipt"]
    capacity[field] = replacement
    capacity.pop("capacity_receipt_sha256")
    _seal(capacity, "capacity_receipt_sha256")
    with pytest.raises(AbsenceGapChainError, match=error):
        validate_absence_gap_chain_v2(chain)


@pytest.mark.parametrize("bad_role", [["verifier_worktree"], {"bad": "role"}])
def test_policy_rejects_non_string_role_with_named_error(tmp_path: Path, bad_role: Any) -> None:
    chain = _chain(tmp_path)
    chain["policy"]["roles"][0]["role"] = bad_role
    with pytest.raises(AbsenceGapChainError, match="policy_role_invalid"):
        validate_absence_gap_chain_v2(chain)


@pytest.mark.parametrize("bad_role", [["verifier_worktree"], {"bad": "role"}])
def test_candidate_index_rejects_non_string_role_with_named_error(
    tmp_path: Path, bad_role: Any
) -> None:
    chain = _chain(tmp_path)
    chain["candidate_index"]["role_dispositions"][0]["role"] = bad_role
    chain["candidate_index"].pop("candidate_index_sha256")
    _seal(chain["candidate_index"], "candidate_index_sha256")
    with pytest.raises(AbsenceGapChainError, match="candidate_index_roles_invalid"):
        validate_absence_gap_chain_v2(chain)


def test_v2_plan_rejects_missing_nonverifier_role_even_if_later_stages_claim_capture(
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path)
    document = dict(chain["plan"].document)
    roster = {role: dict(row) for role, row in document["root_roster"].items()}
    missing_role = next(iter(REQUIRED_ROOT_ROLES - {"verifier_worktree"}))
    roster[missing_role] = {
        "present": False,
        "relative_locator": None,
        "kind": None,
        "unresolved_reason": "synthetic_missing",
    }
    document["root_roster"] = roster
    path = tmp_path / "plan-with-missing-nonverifier.json"
    path.write_bytes(canonical_json(document))
    path.chmod(0o600)
    plan_sha = hashlib.sha256(canonical_json(document)).hexdigest()
    with pytest.raises(AbsenceWitnessError, match="absence_plan_nonverifier_role_absent"):
        load_absence_gap_plan_v2(path, expected_plan_sha256=plan_sha)


def test_chain_rejects_synthetic_held_view_witness(tmp_path: Path) -> None:
    chain = _chain(tmp_path)
    witness = chain["witness"]
    witness["synthetic_backend"] = True
    witness.pop("witness_sha256")
    _seal(witness, "witness_sha256")
    with pytest.raises(AbsenceGapChainError, match="synthetic_witness_not_operational"):
        validate_absence_gap_chain_v2(chain)


def test_unconfirmed_personal_backup_transition_cannot_reach_capture_or_candidate(
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path, transition_confirmed=False)
    with pytest.raises(AbsenceGapChainError, match="capture_spec_policy_invalid"):
        validate_absence_gap_chain_v2(chain)


def test_stale_post_transition_kingston_measurement_cannot_reach_candidate(
    tmp_path: Path,
) -> None:
    chain = _chain(tmp_path)
    capture = chain["capture_spec"]
    capture["capture_preflight_observed_at_utc"] = "2026-10-01T12:10:01Z"
    capture.pop("capture_spec_sha256")
    _seal(capture, "capture_spec_sha256")
    with pytest.raises(AbsenceGapChainError, match="capture_spec_measurement_stale"):
        validate_absence_gap_chain_v2(chain)


def test_v2_policy_reader_rejects_v1_wire_schema() -> None:
    with pytest.raises(AbsenceGapChainError, match="policy_schema_fields_invalid"):
        validate_policy_roster_v2(
            {
                "schema": "brain-host-tree-capture-v5-policy-roster",
                "version": 1,
            }
        )


def test_v1_capture_readers_reject_v2_stage_schemas(tmp_path: Path) -> None:
    chain = _chain(tmp_path)
    with pytest.raises(TreeCaptureV5Error):
        policy_roster_sha256_v5(
            chain["plan"].held_data_plan_sha256,
            chain["policy"]["roles"],
        )
    with pytest.raises(ProductionCaptureV5CLIError, match="capacity_intent_schema_fields_invalid"):
        _capacity_intent_fields(chain["intent"])
    with pytest.raises(ProductionCaptureV5CLIError, match="capacity_receipt_schema_fields_invalid"):
        _capacity_receipt_fields(chain["capacity_receipt"])
    with pytest.raises(
        ProductionCaptureV5CLIError, match="capture_bound_spec_schema_fields_invalid"
    ):
        _capture_spec_fields(chain["capture_spec"])
