#!/usr/bin/env python3
"""Offline validators for the nonqualifying absence-gap v2 hash chain.

This module deliberately contains no writer, CLI, activation, or publication
entrypoint. It validates the exact joins required before a separately reviewed
capture adapter may become callable.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from tools.brain_host_held_data_absence_witness_v2 import (
    EXPECTED_COMMIT,
    HISTORICAL_STATE,
    AbsenceGapPlanV2,
    AbsenceWitnessError,
    is_validated_absence_gap_plan_v2,
    validate_absence_witness_v2,
)
from tools.brain_host_held_data_t_v1 import REQUIRED_ROOT_ROLES
from tools.brain_senses_external_writer_v1 import canonical_json

POLICY_SCHEMA = "brain-host-tree-capture-v5-policy-roster-v2"
INTENT_SCHEMA = "brain-host-tree-capture-v5-capacity-intent-v3"
CAPACITY_SCHEMA = "brain-host-tree-capture-v5-capacity-receipt-v3"
CAPTURE_SCHEMA = "brain-host-tree-capture-v5-capture-spec-v3"
INDEX_SCHEMA = "brain-host-tree-capture-v5-candidate-absence-gap-v2"
FORMULA_VERSION = "operational-reserve-v1"
CAPTURE_STATUS = "nonqualifying_absence_gap"
MAX_SOURCE_BYTES = 2 * 1024**4
MAX_SCRATCH_BYTES = 32 * 1024**3
MAX_METADATA_BYTES = 8 * 1024**3
MAX_CANDIDATE_JSON_BYTES = 256 * 1024**3
MAX_APFS_ALLOCATION_RESERVE_BYTES = 1024**4
MAX_PLANNED_NEW_ENTRIES = 16_000_000

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BASE_ROLES = frozenset(REQUIRED_ROOT_ROLES - {"verifier_worktree"})
_FALSE_AUTHORITY_FIELDS = {
    "root_closure": False,
    "T_qualified": False,
    "K_qualified": False,
    "full_restore_qualified": False,
    "activation_authorized": False,
    "erase_ready": False,
}


class AbsenceGapChainError(ValueError):
    """A strict v2 stage is malformed or does not join its predecessor."""


def _digest(value: Mapping[str, Any], field: str, error: str) -> str:
    if not isinstance(value, Mapping) or not isinstance(value.get(field), str):
        raise AbsenceGapChainError(error)
    digest = value[field]
    body = {key: item for key, item in value.items() if key != field}
    if not _SHA256.fullmatch(digest) or hashlib.sha256(canonical_json(body)).hexdigest() != digest:
        raise AbsenceGapChainError(error)
    return digest


def _exact(value: Mapping[str, Any], keys: set[str], schema: str, error: str) -> None:
    if not isinstance(value, Mapping) or set(value) != keys or value.get("schema") != schema:
        raise AbsenceGapChainError(error)


def _sha(value: object, error: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise AbsenceGapChainError(error)
    return value


def _nonnegative(value: object, error: str) -> int:
    if type(value) is not int or value < 0:
        raise AbsenceGapChainError(error)
    return value


def _utc(value: object, error: str) -> datetime:
    if not isinstance(value, str):
        raise AbsenceGapChainError(error)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AbsenceGapChainError(error) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise AbsenceGapChainError(error)
    return parsed


def policy_roster_v2_sha256(
    *, plan_sha256: str, witness_sha256: str, roles: list[Mapping[str, Any]]
) -> str:
    """Hash exact nine-role policies with the witness bound to the absent role."""
    _sha(plan_sha256, "policy_plan_sha_invalid")
    _sha(witness_sha256, "policy_witness_sha_invalid")
    if not isinstance(roles, list) or len(roles) != len(REQUIRED_ROOT_ROLES):
        raise AbsenceGapChainError("policy_roles_invalid")
    canonical_roles: dict[str, dict[str, Any]] = {}
    for row in roles:
        if not isinstance(row, Mapping):
            raise AbsenceGapChainError("policy_role_invalid")
        role = row.get("role")
        if not isinstance(role, str) or role not in REQUIRED_ROOT_ROLES or role in canonical_roles:
            raise AbsenceGapChainError("policy_role_invalid")
        if role == "verifier_worktree":
            expected = {
                "role",
                "present",
                "disposition",
                "witness_sha256",
                "relative_locator",
                "expected_fetchable_commit",
                "historical_non_git_state",
                "scope",
                "policy_sha256",
            }
            if (
                set(row) != expected
                or row.get("present") is not False
                or row.get("disposition") != CAPTURE_STATUS
                or row.get("witness_sha256") != witness_sha256
                or not isinstance(row.get("relative_locator"), str)
                or row.get("expected_fetchable_commit") != EXPECTED_COMMIT
                or row.get("historical_non_git_state") != HISTORICAL_STATE
                or row.get("scope") is not None
                or row.get("policy_sha256") is not None
            ):
                raise AbsenceGapChainError("policy_absence_role_invalid")
            canonical_roles[role] = {key: row[key] for key in sorted(expected - {"role"})}
        else:
            expected = {"role", "present", "scope", "policy_sha256"}
            if (
                set(row) != expected
                or row.get("present") is not True
                or row.get("scope") not in {"non_protected", "protected_opaque"}
                or not isinstance(row.get("policy_sha256"), str)
                or not _SHA256.fullmatch(row["policy_sha256"])
            ):
                raise AbsenceGapChainError("policy_present_role_invalid")
            canonical_roles[role] = {
                "present": True,
                "scope": row["scope"],
                "policy_sha256": row["policy_sha256"],
            }
    if set(canonical_roles) != set(REQUIRED_ROOT_ROLES):
        raise AbsenceGapChainError("policy_roles_invalid")
    return hashlib.sha256(
        canonical_json(
            {
                "schema": POLICY_SCHEMA,
                "plan_sha256": plan_sha256,
                "witness_sha256": witness_sha256,
                "roles": {role: canonical_roles[role] for role in sorted(canonical_roles)},
                **_FALSE_AUTHORITY_FIELDS,
            }
        )
    ).hexdigest()


def validate_policy_roster_v2(value: Mapping[str, Any]) -> str:
    fields = {
        "schema",
        "plan_sha256",
        "witness_sha256",
        "roles",
        "root_closure",
        "T_qualified",
        "K_qualified",
        "full_restore_qualified",
        "activation_authorized",
        "erase_ready",
        "policy_roster_sha256",
    }
    _exact(value, fields, POLICY_SCHEMA, "policy_schema_fields_invalid")
    if any(value.get(key) is not False for key in _FALSE_AUTHORITY_FIELDS):
        raise AbsenceGapChainError("policy_authority_flag_invalid")
    expected = policy_roster_v2_sha256(
        plan_sha256=_sha(value["plan_sha256"], "policy_plan_sha_invalid"),
        witness_sha256=_sha(value["witness_sha256"], "policy_witness_sha_invalid"),
        roles=value["roles"],
    )
    if value["policy_roster_sha256"] != expected:
        raise AbsenceGapChainError("policy_roster_sha_mismatch")
    return expected


_INTENT_FIELDS = {
    "schema",
    "plan_sha256",
    "witness_sha256",
    "policy_roster_sha256",
    "R_safety_floor_bytes",
    "G_writer_floor_bytes",
    "A_apfs_allocation_reserve_bytes",
    "A_formula_version",
    "verifier_mode",
    "S_V_verifier_scratch_reserve_bytes",
    "W_V_verifier_headroom_bytes",
    "X_shared_pool_margin_bytes",
    "metadata_reserve_bytes",
    "target_spool_reserve_bytes",
    "personal_backup_transition_confirmed",
    "personal_backup_transition_receipt_sha256",
    "personal_backup_transition_completed_at_utc",
    "target_volume_uuid",
    "target_root_id",
    "root_closure",
    "T_qualified",
    "K_qualified",
    "full_restore_qualified",
    "activation_authorized",
    "erase_ready",
    "intent_sha256",
}


def validate_capacity_intent_v2(value: Mapping[str, Any], policy_sha256: str) -> str:
    _exact(value, _INTENT_FIELDS, INTENT_SCHEMA, "intent_schema_fields_invalid")
    _sha(policy_sha256, "intent_policy_sha_invalid")
    for field, error in (
        ("plan_sha256", "intent_plan_sha_invalid"),
        ("witness_sha256", "intent_witness_sha_invalid"),
        ("policy_roster_sha256", "intent_policy_sha_invalid"),
    ):
        _sha(value[field], error)
    if value["policy_roster_sha256"] != policy_sha256:
        raise AbsenceGapChainError("intent_policy_join_mismatch")
    for field in (
        "R_safety_floor_bytes",
        "G_writer_floor_bytes",
        "A_apfs_allocation_reserve_bytes",
        "S_V_verifier_scratch_reserve_bytes",
        "W_V_verifier_headroom_bytes",
        "X_shared_pool_margin_bytes",
        "metadata_reserve_bytes",
        "target_spool_reserve_bytes",
    ):
        if _nonnegative(value[field], "intent_budget_invalid") == 0 and field not in {
            "X_shared_pool_margin_bytes",
            "metadata_reserve_bytes",
        }:
            raise AbsenceGapChainError("intent_budget_invalid")
    if (
        value["R_safety_floor_bytes"] != 50 * 1024**3
        or value["G_writer_floor_bytes"] != 8 * 1024**3
        or value["A_formula_version"] != FORMULA_VERSION
        or value["verifier_mode"] != "shared_target_pool"
        or value["S_V_verifier_scratch_reserve_bytes"] != 32 * 1024**3
        or value["W_V_verifier_headroom_bytes"] != 256 * 1024**2
        or value["A_apfs_allocation_reserve_bytes"] > MAX_APFS_ALLOCATION_RESERVE_BYTES
        or value["metadata_reserve_bytes"] > MAX_METADATA_BYTES
        or value["target_spool_reserve_bytes"] > MAX_SCRATCH_BYTES
        or type(value["personal_backup_transition_confirmed"]) is not bool
        or any(value[field] is not False for field in _FALSE_AUTHORITY_FIELDS)
    ):
        raise AbsenceGapChainError("intent_policy_invalid")
    if value["personal_backup_transition_confirmed"]:
        _sha(
            value["personal_backup_transition_receipt_sha256"], "intent_transition_receipt_invalid"
        )
        _utc(value["personal_backup_transition_completed_at_utc"], "intent_transition_time_invalid")
    elif (
        value["personal_backup_transition_receipt_sha256"] is not None
        or value["personal_backup_transition_completed_at_utc"] is not None
    ):
        raise AbsenceGapChainError("intent_transition_unconfirmed_fields_invalid")
    if (
        not isinstance(value["target_volume_uuid"], str)
        or not re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
            value["target_volume_uuid"],
        )
        or not isinstance(value["target_root_id"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", value["target_root_id"])
    ):
        raise AbsenceGapChainError("intent_target_identity_invalid")
    return _digest(value, "intent_sha256", "intent_sha_mismatch")


_CAPACITY_FIELDS = {
    "schema",
    "plan_sha256",
    "witness_sha256",
    "policy_roster_sha256",
    "intent_sha256",
    "inventory_sha256",
    "F_live_available_bytes",
    "B_payload_logical_bytes",
    "M_metadata_value_bytes",
    "J_candidate_json_bytes",
    "S_K_spool_reserved_bytes",
    "N_new_candidate_entries",
    "A_min_operational_reserve_bytes",
    "R_safety_floor_bytes",
    "G_writer_floor_bytes",
    "A_apfs_allocation_reserve_bytes",
    "S_V_verifier_scratch_reserve_bytes",
    "W_V_verifier_headroom_bytes",
    "X_shared_pool_margin_bytes",
    "required_new_free_bytes",
    "capacity_gate_satisfied",
    "personal_backup_transition_confirmed",
    "personal_backup_transition_receipt_sha256",
    "personal_backup_transition_completed_at_utc",
    "kingston_measurement_sha256",
    "kingston_measurement_observed_at_utc",
    "kingston_volume_uuid",
    "kingston_root_id",
    "root_closure",
    "T_qualified",
    "K_qualified",
    "full_restore_qualified",
    "activation_authorized",
    "erase_ready",
    "capacity_receipt_sha256",
}
_CAPACITY_COMPONENTS = (
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


def validate_capacity_receipt_v2(
    value: Mapping[str, Any], *, intent_sha256: str, intent: Mapping[str, Any], policy_sha256: str
) -> str:
    _exact(value, _CAPACITY_FIELDS, CAPACITY_SCHEMA, "capacity_schema_fields_invalid")
    for field, expected in (
        ("plan_sha256", intent["plan_sha256"]),
        ("witness_sha256", intent["witness_sha256"]),
        ("policy_roster_sha256", policy_sha256),
        ("intent_sha256", intent_sha256),
    ):
        if _sha(value[field], "capacity_join_sha_invalid") != expected:
            raise AbsenceGapChainError("capacity_join_mismatch")
    _sha(value["inventory_sha256"], "capacity_inventory_sha_invalid")
    required = 0
    for field in _CAPACITY_COMPONENTS:
        amount = _nonnegative(value[field], "capacity_budget_invalid")
        if field in intent and amount != intent[field]:
            raise AbsenceGapChainError("capacity_intent_budget_mismatch")
        required += amount
    if (
        value["B_payload_logical_bytes"] > MAX_SOURCE_BYTES
        or value["M_metadata_value_bytes"] > MAX_METADATA_BYTES
        or value["J_candidate_json_bytes"] > MAX_CANDIDATE_JSON_BYTES
        or value["S_K_spool_reserved_bytes"] > MAX_SCRATCH_BYTES
        or value["A_apfs_allocation_reserve_bytes"] > MAX_APFS_ALLOCATION_RESERVE_BYTES
    ):
        raise AbsenceGapChainError("capacity_component_limit_exceeded")
    if (
        _nonnegative(value["F_live_available_bytes"], "capacity_free_bytes_invalid") < 0
        or type(value["capacity_gate_satisfied"]) is not bool
        or type(value["personal_backup_transition_confirmed"]) is not bool
        or value["personal_backup_transition_confirmed"]
        != intent["personal_backup_transition_confirmed"]
        or value["personal_backup_transition_receipt_sha256"]
        != intent["personal_backup_transition_receipt_sha256"]
        or value["personal_backup_transition_completed_at_utc"]
        != intent["personal_backup_transition_completed_at_utc"]
        or type(value["required_new_free_bytes"]) is not int
        or value["required_new_free_bytes"] != required
        or value["capacity_gate_satisfied"]
        != (
            value["F_live_available_bytes"] >= required
            and value["personal_backup_transition_confirmed"]
        )
        or any(value[field] is not False for field in _FALSE_AUTHORITY_FIELDS)
    ):
        raise AbsenceGapChainError("capacity_gate_or_policy_invalid")
    if value["personal_backup_transition_confirmed"]:
        _sha(value["kingston_measurement_sha256"], "capacity_measurement_sha_invalid")
        measurement_time = _utc(
            value["kingston_measurement_observed_at_utc"], "capacity_measurement_time_invalid"
        )
        transition_time = _utc(
            value["personal_backup_transition_completed_at_utc"],
            "capacity_transition_time_invalid",
        )
        if (
            measurement_time <= transition_time
            or not isinstance(value["kingston_volume_uuid"], str)
            or value["kingston_volume_uuid"].lower() != intent["target_volume_uuid"].lower()
            or not isinstance(value["kingston_root_id"], str)
            or value["kingston_root_id"] != intent["target_root_id"]
        ):
            raise AbsenceGapChainError("capacity_post_transition_measurement_invalid")
    elif any(
        value[field] is not None
        for field in (
            "kingston_measurement_sha256",
            "kingston_measurement_observed_at_utc",
            "kingston_volume_uuid",
            "kingston_root_id",
        )
    ):
        raise AbsenceGapChainError("capacity_unconfirmed_measurement_fields_invalid")
    if value["M_metadata_value_bytes"] > intent["metadata_reserve_bytes"]:
        raise AbsenceGapChainError("capacity_metadata_reserve_exceeded")
    if value["S_K_spool_reserved_bytes"] > intent["target_spool_reserve_bytes"]:
        raise AbsenceGapChainError("capacity_spool_reserve_exceeded")
    entries = _nonnegative(value["N_new_candidate_entries"], "capacity_entry_count_invalid")
    if entries > MAX_PLANNED_NEW_ENTRIES:
        raise AbsenceGapChainError("capacity_entry_count_invalid")
    expected_allocation_floor = (
        8 * 1024**3
        + 32 * 1024 * entries
        + (
            value["B_payload_logical_bytes"]
            + value["M_metadata_value_bytes"]
            + value["J_candidate_json_bytes"]
            + value["S_K_spool_reserved_bytes"]
            + 9
        )
        // 10
    )
    if (
        type(value["A_min_operational_reserve_bytes"]) is not int
        or value["A_min_operational_reserve_bytes"] != expected_allocation_floor
        or value["A_apfs_allocation_reserve_bytes"] < expected_allocation_floor
    ):
        raise AbsenceGapChainError("capacity_allocation_reserve_invalid")
    return _digest(value, "capacity_receipt_sha256", "capacity_receipt_sha_mismatch")


_CAPTURE_FIELDS = {
    "schema",
    "plan_sha256",
    "witness_sha256",
    "policy_roster_sha256",
    "intent_sha256",
    "capacity_receipt_sha256",
    "inventory_sha256",
    "operation_id",
    "candidate_status",
    "personal_backup_transition_confirmed",
    "personal_backup_transition_receipt_sha256",
    "personal_backup_transition_completed_at_utc",
    "kingston_measurement_sha256",
    "kingston_measurement_observed_at_utc",
    "kingston_volume_uuid",
    "kingston_root_id",
    "capture_preflight_observed_at_utc",
    "kingston_measurement_max_age_seconds",
    "root_closure",
    "T_qualified",
    "K_qualified",
    "full_restore_qualified",
    "activation_authorized",
    "erase_ready",
    "capture_spec_sha256",
}


def validate_capture_spec_v2(
    value: Mapping[str, Any],
    *,
    plan_sha256: str,
    witness_sha256: str,
    policy_sha256: str,
    intent_sha256: str,
    capacity_sha256: str,
    inventory_sha256: str,
    capacity_receipt: Mapping[str, Any],
) -> str:
    _exact(value, _CAPTURE_FIELDS, CAPTURE_SCHEMA, "capture_spec_schema_fields_invalid")
    expected = {
        "plan_sha256": plan_sha256,
        "witness_sha256": witness_sha256,
        "policy_roster_sha256": policy_sha256,
        "intent_sha256": intent_sha256,
        "capacity_receipt_sha256": capacity_sha256,
        "inventory_sha256": inventory_sha256,
    }
    for field, digest in expected.items():
        if _sha(value[field], "capture_spec_join_sha_invalid") != digest:
            raise AbsenceGapChainError("capture_spec_join_mismatch")
    if (
        not isinstance(value["operation_id"], str)
        or not value["operation_id"]
        or value["candidate_status"] != CAPTURE_STATUS
        or value["personal_backup_transition_confirmed"] is not True
        or capacity_receipt["personal_backup_transition_confirmed"] is not True
        or capacity_receipt["capacity_gate_satisfied"] is not True
        or any(value[field] is not False for field in _FALSE_AUTHORITY_FIELDS)
    ):
        raise AbsenceGapChainError("capture_spec_policy_invalid")
    for field in (
        "personal_backup_transition_receipt_sha256",
        "personal_backup_transition_completed_at_utc",
        "kingston_measurement_sha256",
        "kingston_measurement_observed_at_utc",
        "kingston_volume_uuid",
        "kingston_root_id",
    ):
        if value[field] != capacity_receipt[field]:
            raise AbsenceGapChainError("capture_spec_capacity_measurement_join_mismatch")
    observed = _utc(
        value["capture_preflight_observed_at_utc"], "capture_spec_preflight_time_invalid"
    )
    measured = _utc(
        value["kingston_measurement_observed_at_utc"], "capture_spec_measurement_time_invalid"
    )
    max_age = _nonnegative(
        value["kingston_measurement_max_age_seconds"], "capture_spec_measurement_age_invalid"
    )
    if (
        max_age != 300
        or (observed - measured).total_seconds() < 0
        or (observed - measured).total_seconds() > max_age
    ):
        raise AbsenceGapChainError("capture_spec_measurement_stale")
    return _digest(value, "capture_spec_sha256", "capture_spec_sha_mismatch")


_INDEX_FIELDS = {
    "schema",
    "plan_sha256",
    "witness_sha256",
    "policy_roster_sha256",
    "intent_sha256",
    "capacity_receipt_sha256",
    "inventory_sha256",
    "capture_spec_sha256",
    "candidate_payload_manifest_sha256",
    "candidate_status",
    "role_dispositions",
    "personal_backup_transition_confirmed",
    "kingston_measurement_sha256",
    "kingston_measurement_observed_at_utc",
    "kingston_volume_uuid",
    "kingston_root_id",
    "root_closure",
    "T_qualified",
    "K_qualified",
    "full_restore_qualified",
    "activation_authorized",
    "erase_ready",
    "candidate_index_sha256",
}


def validate_candidate_index_v2(
    value: Mapping[str, Any],
    *,
    plan_sha256: str,
    capture_spec_sha256: str,
    witness_sha256: str,
    policy_sha256: str,
    intent_sha256: str,
    capacity_sha256: str,
    inventory_sha256: str,
    measurement_sha256: str,
    measurement_time: str,
    volume_uuid: str,
    root_id: str,
    witness: Mapping[str, Any],
    plan_roster: Mapping[str, Any],
) -> str:
    _exact(value, _INDEX_FIELDS, INDEX_SCHEMA, "candidate_index_schema_fields_invalid")
    for field in (
        "plan_sha256",
        "witness_sha256",
        "policy_roster_sha256",
        "intent_sha256",
        "capacity_receipt_sha256",
        "inventory_sha256",
        "capture_spec_sha256",
        "candidate_payload_manifest_sha256",
    ):
        _sha(value[field], "candidate_index_digest_invalid")
    if (
        value["plan_sha256"] != plan_sha256
        or value["capture_spec_sha256"] != capture_spec_sha256
        or value["witness_sha256"] != witness_sha256
        or value["policy_roster_sha256"] != policy_sha256
        or value["intent_sha256"] != intent_sha256
        or value["capacity_receipt_sha256"] != capacity_sha256
        or value["inventory_sha256"] != inventory_sha256
        or value["candidate_status"] != CAPTURE_STATUS
        or value["personal_backup_transition_confirmed"] is not True
        or value["kingston_measurement_sha256"] != measurement_sha256
        or value["kingston_measurement_observed_at_utc"] != measurement_time
        or value["kingston_volume_uuid"] != volume_uuid
        or value["kingston_root_id"] != root_id
        or any(value[field] is not False for field in _FALSE_AUTHORITY_FIELDS)
    ):
        raise AbsenceGapChainError("candidate_index_join_or_policy_invalid")
    if not isinstance(value["role_dispositions"], list):
        raise AbsenceGapChainError("candidate_index_roles_invalid")
    if len(value["role_dispositions"]) != len(REQUIRED_ROOT_ROLES) or any(
        not isinstance(row, Mapping) for row in value["role_dispositions"]
    ):
        raise AbsenceGapChainError("candidate_index_roles_invalid")
    if any(not isinstance(row.get("role"), str) for row in value["role_dispositions"]):
        raise AbsenceGapChainError("candidate_index_roles_invalid")
    absent = [
        row
        for row in value["role_dispositions"]
        if isinstance(row, Mapping) and row.get("role") == "verifier_worktree"
    ]
    if len(absent) != 1 or absent[0].get("disposition") != CAPTURE_STATUS:
        raise AbsenceGapChainError("candidate_index_absence_role_invalid")
    if {row.get("role") for row in value["role_dispositions"] if isinstance(row, Mapping)} != set(
        REQUIRED_ROOT_ROLES
    ):
        raise AbsenceGapChainError("candidate_index_roles_invalid")
    for row in value["role_dispositions"]:
        role = row["role"]
        declaration = plan_roster.get(role)
        if not isinstance(declaration, Mapping) or declaration.get("present") is not (
            role != "verifier_worktree"
        ):
            raise AbsenceGapChainError("candidate_index_plan_role_mismatch")
        if role == "verifier_worktree":
            if (
                set(row)
                != {
                    "role",
                    "present",
                    "disposition",
                    "witness_sha256",
                    "relative_locator",
                    "expected_fetchable_commit",
                    "historical_non_git_state",
                }
                or row.get("present") is not False
                or row.get("disposition") != CAPTURE_STATUS
                or row.get("witness_sha256") != witness_sha256
                or row.get("relative_locator") != witness["relative_locator"]
                or row.get("expected_fetchable_commit") != EXPECTED_COMMIT
                or row.get("historical_non_git_state") != HISTORICAL_STATE
            ):
                raise AbsenceGapChainError("candidate_index_absence_role_invalid")
        elif (
            set(row) != {"role", "present", "disposition"}
            or row.get("present") is not True
            or row.get("disposition") != "captured_opaque"
        ):
            raise AbsenceGapChainError("candidate_index_present_role_invalid")
    return _digest(value, "candidate_index_sha256", "candidate_index_sha_mismatch")


def validate_absence_gap_chain_v2(chain: Mapping[str, Any]) -> dict[str, str]:
    """Validate exact order and SHA joins across all inert v2 chain stages."""
    expected_stages = {
        "plan",
        "witness",
        "policy",
        "intent",
        "capacity_receipt",
        "capture_spec",
        "candidate_index",
    }
    if not isinstance(chain, Mapping) or set(chain) != expected_stages:
        raise AbsenceGapChainError("chain_stage_set_invalid")
    plan = chain["plan"]
    if not isinstance(plan, AbsenceGapPlanV2) or not is_validated_absence_gap_plan_v2(plan):
        raise AbsenceGapChainError("chain_plan_invalid")
    plan_sha = plan.plan_sha256
    if hashlib.sha256(canonical_json(plan.document)).hexdigest() != plan_sha:
        raise AbsenceGapChainError("chain_plan_sha_mismatch")
    witness = chain["witness"]
    try:
        validate_absence_witness_v2(witness)
    except AbsenceWitnessError as exc:
        raise AbsenceGapChainError("chain_witness_invalid") from exc
    if witness["synthetic_backend"]:
        raise AbsenceGapChainError("chain_synthetic_witness_not_operational")
    if _sha(witness.get("absence_plan_v2_sha256"), "chain_plan_sha_invalid") != plan_sha:
        raise AbsenceGapChainError("chain_witness_plan_mismatch")
    witness_sha = witness["witness_sha256"]
    for witness_field, plan_field in (
        ("relative_locator", "verifier_worktree_locator"),
        ("expected_fetchable_commit", "expected_fetchable_commit"),
        ("remote_fetchability_evidence_sha256", "remote_fetchability_evidence_sha256"),
        ("historical_non_git_state", "historical_non_git_state"),
        ("data_volume_uuid", "data_volume_uuid"),
        ("data_device_identifier", "data_device_identifier"),
        ("data_volume_group_id", "data_volume_group_id"),
    ):
        expected = plan.document[plan_field]
        if str(witness[witness_field]).lower() != str(expected).lower():
            raise AbsenceGapChainError("chain_witness_plan_binding_mismatch")

    policy = chain["policy"]
    if policy.get("plan_sha256") != plan_sha or policy.get("witness_sha256") != witness_sha:
        raise AbsenceGapChainError("chain_policy_join_mismatch")
    policy_sha = validate_policy_roster_v2(policy)
    policy_roles = {row["role"]: row for row in policy["roles"]}
    if set(policy_roles) != set(plan.document["root_roster"]):
        raise AbsenceGapChainError("chain_policy_plan_roles_mismatch")
    for role in _BASE_ROLES:
        if (
            plan.document["root_roster"][role]["present"] is not True
            or policy_roles[role]["present"] is not True
        ):
            raise AbsenceGapChainError("chain_policy_plan_roles_mismatch")
    absent_policy = [
        row
        for row in policy["roles"]
        if isinstance(row, Mapping) and row.get("role") == "verifier_worktree"
    ]
    if (
        len(absent_policy) != 1
        or absent_policy[0].get("relative_locator") != witness["relative_locator"]
        or absent_policy[0].get("expected_fetchable_commit") != witness["expected_fetchable_commit"]
        or absent_policy[0].get("historical_non_git_state") != witness["historical_non_git_state"]
    ):
        raise AbsenceGapChainError("chain_policy_witness_binding_mismatch")

    intent = chain["intent"]
    if intent.get("plan_sha256") != plan_sha or intent.get("witness_sha256") != witness_sha:
        raise AbsenceGapChainError("chain_intent_join_mismatch")
    intent_sha = validate_capacity_intent_v2(intent, policy_sha)

    capacity = chain["capacity_receipt"]
    capacity_sha = validate_capacity_receipt_v2(
        capacity, intent_sha256=intent_sha, intent=intent, policy_sha256=policy_sha
    )
    capture = chain["capture_spec"]
    capture_sha = validate_capture_spec_v2(
        capture,
        plan_sha256=plan_sha,
        witness_sha256=witness_sha,
        policy_sha256=policy_sha,
        intent_sha256=intent_sha,
        capacity_sha256=capacity_sha,
        inventory_sha256=capacity["inventory_sha256"],
        capacity_receipt=capacity,
    )
    index_sha = validate_candidate_index_v2(
        chain["candidate_index"],
        plan_sha256=plan_sha,
        capture_spec_sha256=capture_sha,
        witness_sha256=witness_sha,
        policy_sha256=policy_sha,
        intent_sha256=intent_sha,
        capacity_sha256=capacity_sha,
        inventory_sha256=capacity["inventory_sha256"],
        measurement_sha256=capture["kingston_measurement_sha256"],
        measurement_time=capture["kingston_measurement_observed_at_utc"],
        volume_uuid=capture["kingston_volume_uuid"],
        root_id=capture["kingston_root_id"],
        witness=witness,
        plan_roster=plan.document["root_roster"],
    )
    return {
        "plan_sha256": plan_sha,
        "witness_sha256": witness_sha,
        "policy_roster_sha256": policy_sha,
        "intent_sha256": intent_sha,
        "capacity_receipt_sha256": capacity_sha,
        "capture_spec_sha256": capture_sha,
        "candidate_index_sha256": index_sha,
    }
