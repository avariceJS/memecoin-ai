#!/usr/bin/env python3
"""Strict role-policy and nonqualifying candidate-index v3 schemas.

The v2 wire contracts remain frozen in ``brain_host_absence_gap_chain_v2``.
This module adds the richer policy/index semantics needed by the typed v3
capture adapter without importing its v5 publisher or verifier.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from tools.brain_host_absence_gap_chain_v2 import (
    _FALSE_AUTHORITY_FIELDS,
    AbsenceGapChainError,
    _exact,
    _sha,
    _utc,
    validate_capacity_intent_v2,
    validate_capacity_receipt_v2,
    validate_capture_spec_v2,
)
from tools.brain_host_held_data_absence_witness_v2 import (
    EXPECTED_COMMIT,
    HISTORICAL_STATE,
    AbsenceGapPlanV2,
    is_validated_absence_gap_plan_v2,
    validate_absence_witness_v2,
)
from tools.brain_host_held_data_t_v1 import REQUIRED_ROOT_ROLES, ROOT_KINDS
from tools.brain_senses_external_writer_v1 import canonical_json

POLICY_V3_SCHEMA = "brain-host-tree-capture-v5-policy-roster-v3"
INDEX_V3_SCHEMA = "brain-host-tree-capture-v5-candidate-absence-gap-v3"
CHAIN_V3_SCHEMA = "brain-host-tree-capture-v5-absence-gap-chain-v3"
CAPTURE_STATUS = "nonqualifying_absence_gap"
PROTECTED_CUSTODY_MODE = "include_protected_opaque_custody"
PROTECTED_CLASSES = frozenset({"F01", "D43", "reserved_holdout"})
ROLE_MODES = {
    "owner_checkout": "owner_tree_opaque",
    "research_checkout": "research_git_plus_opaque_state",
    "owner_codex_worktree": "codex_tree_opaque",
    "private_brain_config": "private_config_quarantine_reissue",
    "launch_agents": "launch_definition_quarantine_no_load",
    "helius_mcp_wrapper": "wrapper_quarantine_reissue",
    "codex_mcp_config": "mcp_config_quarantine_reauth",
    "cursor_mcp_config": "mcp_config_quarantine_reauth",
    "verifier_worktree": "absent_unknown_unrecoverable",
}
PRESENT_ROLES = frozenset(REQUIRED_ROOT_ROLES - {"verifier_worktree"})
MEMBER_MODES = frozenset(
    {
        "include_opaque",
        "exclude_backup_required",
        "exclude_reissue",
        "exclude_protected_unresolved",
        "git_reconstructible_proven",
    }
)
INDEX_FLAGS = {
    "root_closure": False,
    "T_qualified": False,
    "K_qualified": False,
    "full_restore_qualified": False,
    "activation_authorized": False,
    "erase_ready": False,
}
INDEX_FIELDS_V3 = frozenset(
    {
        "schema",
        "candidate_status",
        "plan_sha256",
        "witness_sha256",
        "policy_roster_sha256",
        "intent_sha256",
        "capacity_receipt_sha256",
        "capture_spec_sha256",
        "inventory_sha256",
        "protected_overlay_sha256",
        "candidate_payload_manifest_sha256",
        "approved_operation_sha256",
        "approved_operation_artifact_sha256",
        "role_dispositions",
        "personal_backup_transition_confirmed",
        "kingston_measurement_sha256",
        "kingston_measurement_observed_at_utc",
        "kingston_volume_uuid",
        "kingston_root_id",
        "kingston_m1_measurement_sha256",
        "kingston_m1_observed_at_utc",
        "kingston_m1_volume_uuid",
        "kingston_m1_root_id",
        "kingston_m1_container_reference",
        "kingston_m1_device_identifier",
        "kingston_m1_available_bytes",
        "kingston_m1_monotonic_start_ns",
        "kingston_m1_monotonic_end_ns",
        *_FALSE_AUTHORITY_FIELDS,
        "candidate_index_sha256",
    }
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _digest(value: Mapping[str, Any], field: str, error: str) -> str:
    digest = _sha(value.get(field), error)
    body = {key: item for key, item in value.items() if key != field}
    if hashlib.sha256(canonical_json(body)).hexdigest() != digest:
        raise AbsenceGapChainError(error)
    return digest


def effective_policy_sha256(
    *,
    role: str,
    locator: str,
    kind: str,
    scope: str,
    mode: str,
    role_policy_sha256: str,
    protected_overlay_sha256: str,
) -> str:
    """Domain-separated role projection; overlay digest is always included."""
    if role not in PRESENT_ROLES or mode != ROLE_MODES[role]:
        raise AbsenceGapChainError("policy_v3_role_mode_invalid")
    _sha(role_policy_sha256, "policy_v3_role_policy_sha_invalid")
    _sha(protected_overlay_sha256, "policy_v3_overlay_sha_invalid")
    if (
        not isinstance(locator, str)
        or not locator
        or not isinstance(kind, str)
        or kind != ROOT_KINDS[role]
    ):
        raise AbsenceGapChainError("policy_v3_role_projection_invalid")
    if not isinstance(scope, str) or scope not in {"non_protected", "protected_opaque"}:
        raise AbsenceGapChainError("policy_v3_role_projection_invalid")
    return hashlib.sha256(
        canonical_json(
            {
                "domain": "brain-host-effective-role-policy-v3",
                "role": role,
                "locator": locator,
                "kind": kind,
                "scope": scope,
                "role_mode": mode,
                "role_policy_sha256": role_policy_sha256,
                "protected_overlay_sha256": protected_overlay_sha256,
            }
        )
    ).hexdigest()


def approved_operation_v3_sha256(operation_id: str, capture_spec_sha256: str) -> str:
    if not isinstance(operation_id, str) or not operation_id:
        raise AbsenceGapChainError("approved_operation_id_invalid")
    _sha(capture_spec_sha256, "approved_operation_capture_spec_sha_invalid")
    return hashlib.sha256(
        canonical_json(
            {
                "domain": "brain-host-approved-absence-gap-operation-v3",
                "operation_id": operation_id,
                "capture_spec_sha256": capture_spec_sha256,
            }
        )
    ).hexdigest()


def validate_approved_operation_artifact_v3(
    value: Mapping[str, Any],
    *,
    expected_sha256: str,
    capacity_receipt: Mapping[str, Any],
    capacity_receipt_sha256: str,
    capture_spec: Mapping[str, Any],
    capture_spec_sha256: str,
    source_data_plan: Any,
) -> str:
    fields = {
        "schema",
        "version",
        "operation_id",
        "operation_sha256",
        "capacity_receipt_sha256",
        "m0_measurement",
        "m0_measurement_sha256",
        "approval_sha256",
    }
    _exact(
        value,
        fields,
        "brain-host-approved-operation-v3",
        "approved_operation_schema_invalid",
    )
    if (
        value["version"] != 3
        or not isinstance(value["operation_id"], str)
        or not value["operation_id"]
        or value["capacity_receipt_sha256"] != capacity_receipt_sha256
    ):
        raise AbsenceGapChainError("approved_operation_binding_invalid")
    _sha(expected_sha256, "approved_operation_pin_invalid")
    if _digest(value, "approval_sha256", "approved_operation_digest_invalid") != expected_sha256:
        raise AbsenceGapChainError("approved_operation_pin_mismatch")
    m0 = value["m0_measurement"]
    m0_sha = target_measurement_v3_sha256(m0)
    if (
        m0_sha != value["m0_measurement_sha256"]
        or m0_sha != capacity_receipt["kingston_measurement_sha256"]
        or m0["observed_at_utc"] != capacity_receipt["kingston_measurement_observed_at_utc"]
        or m0["target_volume_uuid"].lower() != capacity_receipt["kingston_volume_uuid"].lower()
        or m0["target_root_id"] != capacity_receipt["kingston_root_id"]
        or m0["available_bytes"] != capacity_receipt["F_live_available_bytes"]
        or m0["target_volume_uuid"].lower() != capture_spec["target_volume_uuid"].lower()
        or m0["target_root_id"] != capture_spec["target_root_id"]
        or m0["target_volume_uuid"].lower() == source_data_plan.data_volume_uuid.lower()
        or m0["target_container_reference"] == source_data_plan.data_container_reference
        or m0["target_device_identifier"] == source_data_plan.data_device_identifier
        or value["operation_sha256"]
        != approved_operation_v3_sha256(value["operation_id"], capture_spec_sha256)
        or _utc(m0["observed_at_utc"], "approved_operation_m0_time_invalid")
        <= _utc(
            capture_spec["personal_backup_transition_completed_at_utc"],
            "approved_operation_transition_time_invalid",
        )
    ):
        raise AbsenceGapChainError("approved_operation_m0_join_invalid")
    return expected_sha256


def target_measurement_v3_sha256(value: Mapping[str, Any]) -> str:
    fields = {
        "target_volume_uuid",
        "target_root_id",
        "target_container_reference",
        "target_device_identifier",
        "available_bytes",
        "observed_at_utc",
        "monotonic_start_ns",
        "monotonic_end_ns",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise AbsenceGapChainError("target_measurement_v3_fields_invalid")
    if (
        not isinstance(value["target_volume_uuid"], str)
        or not isinstance(value["target_root_id"], str)
        or not isinstance(value["target_container_reference"], str)
        or not isinstance(value["target_device_identifier"], str)
        or type(value["available_bytes"]) is not int
        or value["available_bytes"] < 0
        or type(value["monotonic_start_ns"]) is not int
        or type(value["monotonic_end_ns"]) is not int
        or value["monotonic_start_ns"] < 0
        or value["monotonic_end_ns"] < value["monotonic_start_ns"]
        or not re.fullmatch(
            r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
            value["target_volume_uuid"],
        )
        or not re.fullmatch(r"[0-9a-f]{32}", value["target_root_id"])
        or not re.fullmatch(r"disk[0-9]+", value["target_container_reference"])
        or not re.fullmatch(r"disk[0-9]+s[0-9]+", value["target_device_identifier"])
    ):
        raise AbsenceGapChainError("target_measurement_v3_fields_invalid")
    _utc(value["observed_at_utc"], "target_measurement_v3_time_invalid")
    body = {"domain": "brain-host-target-measurement-v3", **dict(value)}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def validate_protected_overlay_v3(value: Mapping[str, Any], expected_sha256: str) -> str:
    """Validate path-only protected custody constraints under an external pin."""
    fields = {"schema", "version", "constraints", "overlay_sha256"}
    _exact(
        value,
        fields,
        "brain-host-protected-overlay-v3",
        "protected_overlay_schema_invalid",
    )
    if value["version"] != 3 or not isinstance(value["constraints"], list):
        raise AbsenceGapChainError("protected_overlay_schema_invalid")
    _sha(expected_sha256, "protected_overlay_pin_invalid")
    if _digest(value, "overlay_sha256", "protected_overlay_digest_invalid") != expected_sha256:
        raise AbsenceGapChainError("protected_overlay_pin_mismatch")
    seen: set[tuple[str, str]] = set()
    for row in value["constraints"]:
        if not isinstance(row, Mapping):
            raise AbsenceGapChainError("protected_overlay_constraint_invalid")
        disposition = row.get("disposition")
        expected_fields = (
            {
                "role",
                "relative_prefix",
                "disposition",
                "authorization_receipt_sha256",
                "protection_class",
            }
            if disposition == PROTECTED_CUSTODY_MODE
            else {"role", "relative_prefix", "disposition"}
        )
        if (
            set(row) != expected_fields
            or not isinstance(row.get("role"), str)
            or row["role"] not in PRESENT_ROLES
            or not isinstance(row.get("relative_prefix"), str)
            or not row["relative_prefix"]
            or disposition not in {"exclude_protected_unresolved", PROTECTED_CUSTODY_MODE}
        ):
            raise AbsenceGapChainError("protected_overlay_constraint_invalid")
        if row["relative_prefix"].startswith("/") or any(
            part in {"", ".", ".."} for part in row["relative_prefix"].split("/")
        ):
            raise AbsenceGapChainError("protected_overlay_constraint_invalid")
        if disposition == PROTECTED_CUSTODY_MODE:
            _sha(row["authorization_receipt_sha256"], "protected_overlay_authorization_invalid")
            if row["protection_class"] not in PROTECTED_CLASSES or row["role"] in {
                "helius_mcp_wrapper",
                "codex_mcp_config",
                "cursor_mcp_config",
            }:
                raise AbsenceGapChainError("protected_overlay_class_invalid")
        elif row["role"] in {
            "helius_mcp_wrapper",
            "codex_mcp_config",
            "cursor_mcp_config",
        }:
            raise AbsenceGapChainError("protected_overlay_cannot_override_reissue_policy")
        key = (row["role"], row["relative_prefix"])
        if key in seen:
            raise AbsenceGapChainError("protected_overlay_constraint_duplicate")
        seen.add(key)
    return expected_sha256


def policy_roster_v3_sha256(value: Mapping[str, Any]) -> str:
    fields = {
        "schema",
        "plan_sha256",
        "witness_sha256",
        "protected_overlay_sha256",
        "roles",
        *_FALSE_AUTHORITY_FIELDS,
        "policy_roster_sha256",
    }
    _exact(value, fields, POLICY_V3_SCHEMA, "policy_v3_schema_invalid")
    plan_sha = _sha(value["plan_sha256"], "policy_v3_plan_sha_invalid")
    witness_sha = _sha(value["witness_sha256"], "policy_v3_witness_sha_invalid")
    overlay_sha = _sha(value["protected_overlay_sha256"], "policy_v3_overlay_sha_invalid")
    if any(value.get(field) is not False for field in INDEX_FLAGS):
        raise AbsenceGapChainError("policy_v3_authority_flag_invalid")
    if not isinstance(value["roles"], list) or len(value["roles"]) != len(REQUIRED_ROOT_ROLES):
        raise AbsenceGapChainError("policy_v3_roles_invalid")
    roles: dict[str, dict[str, Any]] = {}
    for row in value["roles"]:
        if not isinstance(row, Mapping) or not isinstance(row.get("role"), str):
            raise AbsenceGapChainError("policy_v3_role_invalid")
        role = row["role"]
        if role not in REQUIRED_ROOT_ROLES or role in roles:
            raise AbsenceGapChainError("policy_v3_role_invalid")
        if role == "verifier_worktree":
            expected = {
                "role",
                "present",
                "mode",
                "disposition",
                "witness_sha256",
                "relative_locator",
                "expected_fetchable_commit",
                "historical_non_git_state",
            }
            if (
                set(row) != expected
                or row["present"] is not False
                or row["mode"] != ROLE_MODES[role]
                or row["disposition"] != CAPTURE_STATUS
                or row["witness_sha256"] != witness_sha
                or not isinstance(row["relative_locator"], str)
                or row["expected_fetchable_commit"] != EXPECTED_COMMIT
                or row["historical_non_git_state"] != HISTORICAL_STATE
            ):
                raise AbsenceGapChainError("policy_v3_absence_role_invalid")
            roles[role] = {key: row[key] for key in sorted(expected - {"role"})}
            continue
        expected = {
            "role",
            "present",
            "mode",
            "relative_locator",
            "kind",
            "scope",
            "role_policy_sha256",
            "effective_policy_sha256",
        }
        if set(row) != expected or row["present"] is not True or row["mode"] != ROLE_MODES[role]:
            raise AbsenceGapChainError("policy_v3_present_role_invalid")
        calculated = effective_policy_sha256(
            role=role,
            locator=row["relative_locator"],
            kind=row["kind"],
            scope=row["scope"],
            mode=row["mode"],
            role_policy_sha256=row["role_policy_sha256"],
            protected_overlay_sha256=overlay_sha,
        )
        if row["effective_policy_sha256"] != calculated:
            raise AbsenceGapChainError("policy_v3_effective_policy_mismatch")
        roles[role] = {key: row[key] for key in sorted(expected - {"role"})}
    if set(roles) != set(REQUIRED_ROOT_ROLES):
        raise AbsenceGapChainError("policy_v3_roles_invalid")
    body = {
        "schema": POLICY_V3_SCHEMA,
        "plan_sha256": plan_sha,
        "witness_sha256": witness_sha,
        "protected_overlay_sha256": overlay_sha,
        "roles": {key: roles[key] for key in sorted(roles)},
        **INDEX_FLAGS,
    }
    return hashlib.sha256(canonical_json(body)).hexdigest()


def validate_policy_roster_v3(value: Mapping[str, Any]) -> str:
    expected = policy_roster_v3_sha256(value)
    if value["policy_roster_sha256"] != expected:
        raise AbsenceGapChainError("policy_v3_digest_mismatch")
    return expected


def _validate_member_counts(row: Mapping[str, Any]) -> None:
    fields = {
        "observed_entry_count",
        "included_entry_count",
        "excluded_entry_count",
        "unresolved_entry_count",
        "included_logical_bytes",
        "excluded_logical_bytes",
        "unresolved_logical_bytes",
    }
    exact_row_fields = {
        "role",
        "present",
        "role_mode",
        "relative_locator",
        "kind",
        "scope",
        "effective_policy_sha256",
        "member_manifest_sha256",
        "observed_entry_count",
        "included_entry_count",
        "excluded_entry_count",
        "unresolved_entry_count",
        "included_logical_bytes",
        "excluded_logical_bytes",
        "unresolved_logical_bytes",
        "member_modes",
        "member_mode_counts",
        "member_mode_logical_bytes",
        "member_ranges",
    }
    if set(row) != exact_row_fields or not fields.issubset(row):
        raise AbsenceGapChainError("index_v3_count_fields_invalid")
    for name in fields:
        value = row[name]
        if value is not None and (type(value) is not int or value < 0):
            raise AbsenceGapChainError("index_v3_count_fields_invalid")
    if row["observed_entry_count"] is None:
        raise AbsenceGapChainError("index_v3_count_fields_invalid")
    if row["included_entry_count"] is None or row["excluded_entry_count"] is None:
        raise AbsenceGapChainError("index_v3_count_fields_invalid")
    if row["unresolved_entry_count"] is None:
        if (
            row["unresolved_logical_bytes"] is not None
            or row["included_entry_count"] + row["excluded_entry_count"]
            >= row["observed_entry_count"]
        ):
            raise AbsenceGapChainError("index_v3_count_fields_invalid")
    elif (
        row["included_entry_count"] + row["excluded_entry_count"] + row["unresolved_entry_count"]
        != row["observed_entry_count"]
    ):
        raise AbsenceGapChainError("index_v3_count_partition_invalid")
    modes = row["member_mode_counts"]
    bytes_by_mode = row["member_mode_logical_bytes"]
    if not isinstance(modes, Mapping) or set(modes) != MEMBER_MODES:
        raise AbsenceGapChainError("index_v3_count_fields_invalid")
    if not isinstance(bytes_by_mode, Mapping) or set(bytes_by_mode) != MEMBER_MODES:
        raise AbsenceGapChainError("index_v3_count_fields_invalid")
    for mode in MEMBER_MODES:
        count = modes[mode]
        amount = bytes_by_mode[mode]
        if count is not None and (type(count) is not int or count < 0):
            raise AbsenceGapChainError("index_v3_count_fields_invalid")
        if amount is not None and (type(amount) is not int or amount < 0):
            raise AbsenceGapChainError("index_v3_count_fields_invalid")
        if mode != "exclude_protected_unresolved" and (count is None or amount is None):
            raise AbsenceGapChainError("index_v3_count_fields_invalid")
        if (count is None) != (amount is None):
            raise AbsenceGapChainError("index_v3_count_fields_invalid")
    if (
        modes["include_opaque"] != row["included_entry_count"]
        or sum(
            modes[key] or 0
            for key in ("exclude_backup_required", "exclude_reissue", "git_reconstructible_proven")
        )
        != row["excluded_entry_count"]
        or modes["exclude_protected_unresolved"] != row["unresolved_entry_count"]
        or bytes_by_mode["include_opaque"] != row["included_logical_bytes"]
        or sum(
            bytes_by_mode[key] or 0
            for key in ("exclude_backup_required", "exclude_reissue", "git_reconstructible_proven")
        )
        != row["excluded_logical_bytes"]
        or bytes_by_mode["exclude_protected_unresolved"] != row["unresolved_logical_bytes"]
    ):
        raise AbsenceGapChainError("index_v3_count_join_invalid")
    expected_modes = sorted(mode for mode, count in modes.items() if count is None or count != 0)
    if row["member_modes"] != expected_modes:
        raise AbsenceGapChainError("index_v3_count_join_invalid")


def validate_candidate_index_v3(
    value: Mapping[str, Any],
    *,
    plan: AbsenceGapPlanV2,
    witness: Mapping[str, Any],
    policy: Mapping[str, Any],
    intent_sha256: str,
    capacity_sha256: str,
    capture_spec_sha256: str,
    inventory_sha256: str,
    capacity_receipt: Mapping[str, Any],
    approved_operation_sha256: str,
    approved_operation_artifact_sha256: str,
) -> str:
    _exact(value, INDEX_FIELDS_V3, INDEX_V3_SCHEMA, "candidate_index_v3_schema_invalid")
    policy_sha = validate_policy_roster_v3(policy)
    joins = {
        "plan_sha256": plan.plan_sha256,
        "witness_sha256": witness["witness_sha256"],
        "policy_roster_sha256": policy_sha,
        "intent_sha256": intent_sha256,
        "capacity_receipt_sha256": capacity_sha256,
        "capture_spec_sha256": capture_spec_sha256,
        "inventory_sha256": inventory_sha256,
        "protected_overlay_sha256": policy["protected_overlay_sha256"],
        "candidate_payload_manifest_sha256": value["candidate_payload_manifest_sha256"],
        "approved_operation_sha256": approved_operation_sha256,
        "approved_operation_artifact_sha256": approved_operation_artifact_sha256,
        "kingston_measurement_sha256": capacity_receipt["kingston_measurement_sha256"],
        "kingston_measurement_observed_at_utc": capacity_receipt[
            "kingston_measurement_observed_at_utc"
        ],
        "kingston_volume_uuid": capacity_receipt["kingston_volume_uuid"],
        "kingston_root_id": capacity_receipt["kingston_root_id"],
    }
    for field, expected in joins.items():
        actual = (
            _sha(value[field], "candidate_index_v3_join_invalid")
            if field.endswith("sha256")
            else value[field]
        )
        if actual != expected:
            raise AbsenceGapChainError("candidate_index_v3_join_mismatch")
    _sha(
        value["approved_operation_artifact_sha256"],
        "candidate_index_v3_approved_operation_artifact_invalid",
    )
    if value["approved_operation_artifact_sha256"] != approved_operation_artifact_sha256:
        raise AbsenceGapChainError("candidate_index_v3_approved_operation_artifact_mismatch")
    if (
        value["candidate_status"] != CAPTURE_STATUS
        or value["personal_backup_transition_confirmed"] is not True
        or capacity_receipt["capacity_gate_satisfied"] is not True
        or any(value[field] is not False for field in INDEX_FLAGS)
    ):
        raise AbsenceGapChainError("candidate_index_v3_policy_invalid")
    m1_body = {
        "target_volume_uuid": value["kingston_m1_volume_uuid"],
        "target_root_id": value["kingston_m1_root_id"],
        "target_container_reference": value["kingston_m1_container_reference"],
        "target_device_identifier": value["kingston_m1_device_identifier"],
        "available_bytes": value["kingston_m1_available_bytes"],
        "observed_at_utc": value["kingston_m1_observed_at_utc"],
        "monotonic_start_ns": value["kingston_m1_monotonic_start_ns"],
        "monotonic_end_ns": value["kingston_m1_monotonic_end_ns"],
    }
    if (
        _sha(value["kingston_m1_measurement_sha256"], "candidate_index_v3_m1_sha_invalid")
        != target_measurement_v3_sha256(m1_body)
        or value["kingston_m1_volume_uuid"].lower()
        != capacity_receipt["kingston_volume_uuid"].lower()
        or value["kingston_m1_root_id"] != capacity_receipt["kingston_root_id"]
        or value["kingston_m1_available_bytes"] < capacity_receipt["required_new_free_bytes"]
        or not isinstance(value["kingston_m1_container_reference"], str)
        or not re.fullmatch(r"disk[0-9]+", value["kingston_m1_container_reference"])
        or not isinstance(value["kingston_m1_device_identifier"], str)
        or not re.fullmatch(r"disk[0-9]+s[0-9]+", value["kingston_m1_device_identifier"])
    ):
        raise AbsenceGapChainError("candidate_index_v3_m1_join_invalid")
    dispositions = value["role_dispositions"]
    if not isinstance(dispositions, list) or len(dispositions) != len(REQUIRED_ROOT_ROLES):
        raise AbsenceGapChainError("candidate_index_v3_roles_invalid")
    by_role: dict[str, Mapping[str, Any]] = {}
    policy_roles = {row["role"]: row for row in policy["roles"]}
    for row in dispositions:
        if not isinstance(row, Mapping) or not isinstance(row.get("role"), str):
            raise AbsenceGapChainError("candidate_index_v3_role_invalid")
        role = row["role"]
        if role not in REQUIRED_ROOT_ROLES or role in by_role:
            raise AbsenceGapChainError("candidate_index_v3_role_invalid")
        by_role[role] = row
    if set(by_role) != set(REQUIRED_ROOT_ROLES):
        raise AbsenceGapChainError("candidate_index_v3_roles_invalid")
    for role, row in by_role.items():
        if role == "verifier_worktree":
            expected_absent = {
                "role",
                "present",
                "role_mode",
                "disposition",
                "witness_sha256",
                "relative_locator",
                "expected_fetchable_commit",
                "historical_non_git_state",
            }
            if (
                set(row) != expected_absent
                or row.get("present") is not False
                or row.get("role_mode") != ROLE_MODES[role]
                or row.get("disposition") != CAPTURE_STATUS
                or row.get("witness_sha256") != witness["witness_sha256"]
                or row.get("relative_locator") != witness["relative_locator"]
                or row.get("expected_fetchable_commit") != EXPECTED_COMMIT
                or row.get("historical_non_git_state") != HISTORICAL_STATE
            ):
                raise AbsenceGapChainError("candidate_index_v3_absence_role_invalid")
            continue
        projection = policy_roles[role]
        if (
            row.get("present") is not True
            or row.get("role_mode") != ROLE_MODES[role]
            or row.get("relative_locator") != projection["relative_locator"]
            or row.get("kind") != projection["kind"]
            or row.get("scope") != projection["scope"]
            or row.get("effective_policy_sha256") != projection["effective_policy_sha256"]
            or not isinstance(row.get("member_manifest_sha256"), str)
            or not _SHA256.fullmatch(row["member_manifest_sha256"])
            or not isinstance(row.get("member_ranges"), list)
            or not isinstance(row.get("member_modes"), list)
            or any(
                not isinstance(mode, str) or mode not in MEMBER_MODES
                for mode in row["member_modes"]
            )
        ):
            raise AbsenceGapChainError("candidate_index_v3_role_projection_mismatch")
        _validate_member_counts(row)
        previous_last: str | None = None
        range_total = 0
        for member in row["member_ranges"]:
            if (
                not isinstance(member, Mapping)
                or set(member) != {"first_path", "last_path", "count", "sha256"}
                or not isinstance(member["first_path"], str)
                or not isinstance(member["last_path"], str)
                or member["first_path"] > member["last_path"]
                or type(member["count"]) is not int
                or member["count"] <= 0
                or member["count"] > 512
                or (previous_last is not None and member["first_path"] <= previous_last)
            ):
                raise AbsenceGapChainError("candidate_index_v3_member_range_invalid")
            _sha(member["sha256"], "candidate_index_v3_member_range_invalid")
            previous_last = member["last_path"]
            range_total += member["count"]
        if range_total != row["observed_entry_count"]:
            raise AbsenceGapChainError("candidate_index_v3_member_range_invalid")
        if role in {"helius_mcp_wrapper", "codex_mcp_config", "cursor_mcp_config"}:
            allowed = {"exclude_reissue", "exclude_backup_required"}
            if row.get("included_entry_count") != 0 or row.get("included_logical_bytes") != 0:
                raise AbsenceGapChainError("candidate_index_v3_config_payload_forbidden")
            if not set(row["member_modes"]).issubset(allowed):
                raise AbsenceGapChainError("candidate_index_v3_config_disposition_invalid")
    return _digest(value, "candidate_index_sha256", "candidate_index_v3_digest_invalid")


def validate_absence_gap_chain_v3(chain: Mapping[str, Any]) -> dict[str, str]:
    """Validate v2 stable budget stages with v3 policy and candidate schemas."""
    expected_stages = {
        "schema",
        "plan",
        "witness",
        "protected_overlay",
        "policy",
        "intent",
        "capacity_receipt",
        "capture_spec",
        "approved_operation",
        "candidate_index",
    }
    if not isinstance(chain, Mapping) or set(chain) != expected_stages:
        raise AbsenceGapChainError("chain_v3_stage_set_invalid")
    if chain["schema"] != CHAIN_V3_SCHEMA:
        raise AbsenceGapChainError("chain_v3_schema_invalid")
    plan = chain["plan"]
    if not isinstance(plan, AbsenceGapPlanV2) or not is_validated_absence_gap_plan_v2(plan):
        raise AbsenceGapChainError("chain_v3_plan_invalid")
    validate_absence_witness_v2(chain["witness"])
    if chain["witness"].get("synthetic_backend"):
        raise AbsenceGapChainError("chain_v3_synthetic_witness_rejected")
    overlay_sha = validate_protected_overlay_v3(
        chain["protected_overlay"], chain["policy"].get("protected_overlay_sha256")
    )
    policy = chain["policy"]
    policy_sha = validate_policy_roster_v3(policy)
    if (
        policy["plan_sha256"] != plan.plan_sha256
        or policy["witness_sha256"] != chain["witness"]["witness_sha256"]
        or policy["protected_overlay_sha256"] != overlay_sha
    ):
        raise AbsenceGapChainError("chain_v3_policy_join_mismatch")
    plan_roster = plan.document["root_roster"]
    role_map = {row["role"]: row for row in policy["roles"]}
    for role in PRESENT_ROLES:
        declaration = plan_roster[role]
        projection = role_map[role]
        if (
            declaration["present"] is not True
            or projection["relative_locator"] != declaration["relative_locator"]
            or projection["kind"] != declaration["kind"]
        ):
            raise AbsenceGapChainError("chain_v3_plan_projection_mismatch")
    intent = chain["intent"]
    if (
        intent["plan_sha256"] != plan.plan_sha256
        or intent["witness_sha256"] != chain["witness"]["witness_sha256"]
    ):
        raise AbsenceGapChainError("chain_v3_intent_join_mismatch")
    intent_sha = validate_capacity_intent_v2(intent, policy_sha)
    capacity = chain["capacity_receipt"]
    capacity_sha = validate_capacity_receipt_v2(
        capacity, intent_sha256=intent_sha, intent=intent, policy_sha256=policy_sha
    )
    capture = chain["capture_spec"]
    capture_sha = validate_capture_spec_v2(
        capture,
        plan_sha256=plan.plan_sha256,
        witness_sha256=chain["witness"]["witness_sha256"],
        policy_sha256=policy_sha,
        intent_sha256=intent_sha,
        capacity_sha256=capacity_sha,
        inventory_sha256=capacity["inventory_sha256"],
        capacity_receipt=capacity,
    )
    approved_artifact_sha = validate_approved_operation_artifact_v3(
        chain["approved_operation"],
        expected_sha256=chain["approved_operation"].get("approval_sha256"),
        capacity_receipt=capacity,
        capacity_receipt_sha256=capacity_sha,
        capture_spec=capture,
        capture_spec_sha256=capture_sha,
        source_data_plan=plan.held_data_plan,
    )
    index_sha = validate_candidate_index_v3(
        chain["candidate_index"],
        plan=plan,
        witness=chain["witness"],
        policy=policy,
        intent_sha256=intent_sha,
        capacity_sha256=capacity_sha,
        capture_spec_sha256=capture_sha,
        inventory_sha256=capacity["inventory_sha256"],
        capacity_receipt=capacity,
        approved_operation_sha256=approved_operation_v3_sha256(
            capture["operation_id"], capture_sha
        ),
        approved_operation_artifact_sha256=approved_artifact_sha,
    )
    return {
        "plan_sha256": plan.plan_sha256,
        "witness_sha256": chain["witness"]["witness_sha256"],
        "protected_overlay_sha256": overlay_sha,
        "policy_roster_sha256": policy_sha,
        "intent_sha256": intent_sha,
        "capacity_receipt_sha256": capacity_sha,
        "capture_spec_sha256": capture_sha,
        "candidate_index_sha256": index_sha,
    }
