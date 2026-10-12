"""Validate the structure and provenance of one local candidate-position runtime trace.

This v1 schema is an evidence envelope only. It does not decode token accounts,
prove Agave runtime law, join a trace to rooted chain state, or establish settlement.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

SCHEMA = "brain-senses-candidate-position-runtime-trace-v1"
SCHEMA_VERSION = 1
CLAIMS = {
    "runtime_source_qualified": False,
    "runtime_law_qualified": False,
    "candidate_position_admitted": False,
    "native_settlement_qualified": False,
    "buyExact25_qualified": False,
    "economic_admission": False,
    "point_in_time_eligible": False,
}
_HEX_32 = re.compile(r"[0-9a-f]{64}\Z")
_HEX_20 = re.compile(r"[0-9a-f]{40}\Z")
_U64_MAX = (1 << 64) - 1

_TOP = {
    "schema",
    "schema_version",
    "source_pins",
    "attempt",
    "availability",
    "clock",
    "rent",
    "programs",
    "account_roles",
    "frames",
    "account_snapshots",
    "fee_config",
    "hook_config",
    "claims",
}
_SOURCE_PINS = {
    "agave_commit",
    "patch_sha256",
    "cargo_lock_sha256",
    "runtime_source_sha256",
    "feature_set_sha256",
}
_ATTEMPT = {"attempt_id", "execution_id", "signature_sha256", "message_sha256", "disposition"}
_AVAILABILITY = {
    "status",
    "provider_receipt_ns",
    "durable_available_ns",
    "clock_uncertainty_ns",
    "clock_evidence_sha256",
}
_CLOCK = {"slot", "epoch", "raw_bytes_hex", "raw_sha256", "source_status"}
_RENT = {
    "slot",
    "epoch",
    "lamports_per_byte_year",
    "exemption_threshold_numerator",
    "exemption_threshold_denominator",
    "raw_bytes_hex",
    "raw_sha256",
    "source_status",
}
_PROGRAM = {"program_id", "identity_kind", "owner", "executable_sha256", "source_status"}
_ROLE = {"role", "pubkey", "owner", "mint", "token_program"}
_FRAME = {
    "attempt_id",
    "execution_id",
    "ordinal",
    "path",
    "parent_path",
    "stack_height",
    "program_id",
    "instruction_data_sha256",
    "entry_status",
    "return_status",
    "candidate_path",
}
_SNAPSHOT = {
    "attempt_id",
    "execution_id",
    "frame_path",
    "role",
    "phase",
    "pubkey",
    "owner",
    "mint",
    "token_program",
    "lamports",
    "rent_epoch",
    "data_bytes_hex",
    "data_sha256",
}
_CONFIG_ACCOUNT = {"pubkey", "owner", "raw_bytes_hex", "raw_sha256", "slot", "epoch"}


class TraceRejected(ValueError):
    """Malformed, cross-attempt, or provenance-incomplete runtime trace."""


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TraceRejected("trace_not_canonicalizable") from exc


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exact(value: Any, keys: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise TraceRejected(code)
    return value


def _hash(value: Any, code: str) -> str:
    if not isinstance(value, str) or _HEX_32.fullmatch(value) is None:
        raise TraceRejected(code)
    return value


def _id(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 128:
        raise TraceRejected(code)
    return value


def _u64(value: Any, code: str) -> int:
    if type(value) is not int or not 0 <= value <= _U64_MAX:
        raise TraceRejected(code)
    return value


def _raw_hex(value: Any, digest: Any, code: str) -> bytes:
    if not isinstance(value, str) or len(value) % 2 or value != value.lower():
        raise TraceRejected(code)
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise TraceRejected(code) from exc
    if raw.hex() != value or _hash(digest, code) != sha256(raw):
        raise TraceRejected(code)
    return raw


def _source_pins(value: Any) -> None:
    pins = _exact(value, _SOURCE_PINS, "trace_source_pin_shape")
    commit = pins["agave_commit"]
    if not isinstance(commit, str) or _HEX_20.fullmatch(commit) is None:
        raise TraceRejected("trace_source_commit")
    for name in _SOURCE_PINS - {"agave_commit"}:
        _hash(pins[name], "trace_source_hash")


def _availability(value: Any) -> dict[str, Any]:
    item = _exact(value, _AVAILABILITY, "trace_availability_shape")
    status = item["status"]
    if status == "unknown":
        if any(item[name] is not None for name in _AVAILABILITY - {"status"}):
            raise TraceRejected("trace_unknown_availability_has_values")
    elif status == "receipt_bounded":
        receipt = _u64(item["provider_receipt_ns"], "trace_receipt_time")
        durable = _u64(item["durable_available_ns"], "trace_durable_time")
        uncertainty = _u64(item["clock_uncertainty_ns"], "trace_clock_uncertainty")
        _hash(item["clock_evidence_sha256"], "trace_clock_evidence_hash")
        if durable < receipt or uncertainty == 0:
            raise TraceRejected("trace_availability_order")
    else:
        raise TraceRejected("trace_availability_status")
    return dict(item)


def _account_roles(value: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list) or not value:
        raise TraceRejected("trace_account_roles_empty")
    indexed: dict[str, Mapping[str, Any]] = {}
    for raw in value:
        row = _exact(raw, _ROLE, "trace_account_role_shape")
        role = _id(row["role"], "trace_account_role_id")
        if role in indexed:
            raise TraceRejected("trace_account_role_duplicate")
        for field in ("pubkey", "owner", "mint", "token_program"):
            _id(row[field], "trace_account_role_identity")
        indexed[role] = row
    return indexed


def _programs(value: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list) or not value:
        raise TraceRejected("trace_programs_empty")
    indexed: dict[str, Mapping[str, Any]] = {}
    for raw in value:
        row = _exact(raw, _PROGRAM, "trace_program_shape")
        program_id = _id(row["program_id"], "trace_program_id")
        if program_id in indexed:
            raise TraceRejected("trace_program_duplicate")
        if row["identity_kind"] not in {"deployed_bytecode", "builtin_or_runtime"}:
            raise TraceRejected("trace_program_identity_kind")
        _id(row["owner"], "trace_program_owner")
        _hash(row["executable_sha256"], "trace_program_hash")
        if (
            row["source_status"] != "pinned_local_source_unqualified"
            and row["source_status"] != "unknown"
        ):
            raise TraceRejected("trace_program_source_status")
        indexed[program_id] = row
    return indexed


def _frames(
    value: Any, attempt_id: str, execution_id: str, programs: Mapping[str, Any]
) -> dict[tuple[int, ...], Mapping[str, Any]]:
    if not isinstance(value, list) or not value:
        raise TraceRejected("trace_frames_empty")
    indexed: dict[tuple[int, ...], Mapping[str, Any]] = {}
    candidate_count = 0
    for ordinal, raw in enumerate(value):
        row = _exact(raw, _FRAME, "trace_frame_shape")
        if row["attempt_id"] != attempt_id or row["execution_id"] != execution_id:
            raise TraceRejected("trace_frame_cross_attempt")
        if type(row["ordinal"]) is not int or row["ordinal"] != ordinal:
            raise TraceRejected("trace_frame_order")
        path = row["path"]
        parent = row["parent_path"]
        if (
            not isinstance(path, list)
            or not path
            or any(type(part) is not int or part < 0 for part in path)
            or not isinstance(parent, list)
            or path[:-1] != parent
            or type(row["stack_height"]) is not int
            or row["stack_height"] != len(path)
        ):
            raise TraceRejected("trace_frame_path")
        key = tuple(path)
        if key in indexed or (parent and tuple(parent) not in indexed):
            raise TraceRejected("trace_frame_parent_or_duplicate")
        program_id = _id(row["program_id"], "trace_frame_program")
        if program_id not in programs:
            raise TraceRejected("trace_frame_program_unpinned")
        _hash(row["instruction_data_sha256"], "trace_instruction_hash")
        if row["entry_status"] != "entered" or row["return_status"] not in {
            "success",
            "failed",
            "push_failed",
            "unknown",
        }:
            raise TraceRejected("trace_frame_lifecycle")
        if type(row["candidate_path"]) is not bool:
            raise TraceRejected("trace_candidate_path_type")
        candidate_count += int(row["candidate_path"])
        indexed[key] = row
    if candidate_count != 1:
        raise TraceRejected("trace_candidate_path_count")
    return indexed


def _snapshots(
    value: Any,
    *,
    attempt_id: str,
    execution_id: str,
    roles: Mapping[str, Mapping[str, Any]],
    frames: Mapping[tuple[int, ...], Mapping[str, Any]],
    disposition: str,
) -> dict[tuple[tuple[int, ...], str, str], Mapping[str, Any]]:
    if not isinstance(value, list) or not value:
        raise TraceRejected("trace_snapshots_empty")
    indexed: dict[tuple[tuple[int, ...], str, str], Mapping[str, Any]] = {}
    phases: dict[tuple[tuple[int, ...], str], set[str]] = {}
    for raw in value:
        row = _exact(raw, _SNAPSHOT, "trace_snapshot_shape")
        if row["attempt_id"] != attempt_id or row["execution_id"] != execution_id:
            raise TraceRejected("trace_snapshot_cross_attempt")
        path = row["frame_path"]
        if not isinstance(path, list) or tuple(path) not in frames:
            raise TraceRejected("trace_snapshot_frame_missing")
        role = row["role"]
        expected = roles.get(role) if isinstance(role, str) else None
        if expected is None:
            raise TraceRejected("trace_snapshot_role_unselected")
        phase = row["phase"]
        allowed_phases = (
            {"pre", "post"} if disposition == "transaction_success" else {"pre", "attempted"}
        )
        if phase not in allowed_phases:
            raise TraceRejected("trace_snapshot_phase_for_disposition")
        for field in ("pubkey", "owner", "mint", "token_program"):
            if row[field] != expected[field]:
                raise TraceRejected("trace_snapshot_identity_mismatch")
        _u64(row["lamports"], "trace_snapshot_lamports")
        _u64(row["rent_epoch"], "trace_snapshot_rent_epoch")
        _raw_hex(row["data_bytes_hex"], row["data_sha256"], "trace_snapshot_bytes")
        key = (tuple(path), role, phase)
        if key in indexed:
            raise TraceRejected("trace_snapshot_duplicate")
        indexed[key] = row
        phases.setdefault((tuple(path), role), set()).add(phase)

    candidate_paths = [path for path, frame in frames.items() if frame["candidate_path"]]
    for path in candidate_paths:
        for role in roles:
            actual = phases.get((path, role), set())
            required = (
                {"pre", "post"} if disposition == "transaction_success" else {"pre", "attempted"}
            )
            if actual != required:
                raise TraceRejected("trace_selected_role_coverage")
    return indexed


def _config(value: Any, code: str, clock: Mapping[str, Any]) -> Mapping[str, Any]:
    row = _exact(value, _CONFIG_ACCOUNT, code)
    _id(row["pubkey"], code)
    _id(row["owner"], code)
    _raw_hex(row["raw_bytes_hex"], row["raw_sha256"], code)
    if _u64(row["slot"], code) != clock["slot"] or _u64(row["epoch"], code) != clock["epoch"]:
        raise TraceRejected(f"{code}_clock_binding")
    return row


def verify_trace(value: Any) -> dict[str, Any]:
    """Check exact structure and same-attempt byte/provenance links only."""
    trace = _exact(value, _TOP, "trace_shape")
    if (
        trace["schema"] != SCHEMA
        or type(trace["schema_version"]) is not int
        or trace["schema_version"] != SCHEMA_VERSION
    ):
        raise TraceRejected("trace_schema")
    _source_pins(trace["source_pins"])
    attempt = _exact(trace["attempt"], _ATTEMPT, "trace_attempt_shape")
    attempt_id = _id(attempt["attempt_id"], "trace_attempt_id")
    execution_id = _id(attempt["execution_id"], "trace_execution_id")
    for name in ("signature_sha256", "message_sha256"):
        _hash(attempt[name], "trace_attempt_hash")
    disposition = attempt["disposition"]
    if disposition not in {"transaction_success", "transaction_failed", "fees_only", "unknown"}:
        raise TraceRejected("trace_attempt_disposition")

    availability = _availability(trace["availability"])
    clock = _exact(trace["clock"], _CLOCK, "trace_clock_shape")
    _u64(clock["slot"], "trace_clock_slot")
    _u64(clock["epoch"], "trace_clock_epoch")
    _raw_hex(clock["raw_bytes_hex"], clock["raw_sha256"], "trace_clock_bytes")
    if clock["source_status"] not in {"raw_runtime_sysvar_unqualified", "unknown"}:
        raise TraceRejected("trace_clock_source_status")

    rent = _exact(trace["rent"], _RENT, "trace_rent_shape")
    if (
        _u64(rent["slot"], "trace_rent_slot") != clock["slot"]
        or _u64(rent["epoch"], "trace_rent_epoch") != clock["epoch"]
    ):
        raise TraceRejected("trace_rent_clock_binding")
    _u64(rent["lamports_per_byte_year"], "trace_rent_rate")
    numerator = _u64(rent["exemption_threshold_numerator"], "trace_rent_numerator")
    denominator = _u64(rent["exemption_threshold_denominator"], "trace_rent_denominator")
    if denominator == 0 or numerator == 0:
        raise TraceRejected("trace_rent_threshold")
    _raw_hex(rent["raw_bytes_hex"], rent["raw_sha256"], "trace_rent_bytes")
    if rent["source_status"] not in {"raw_runtime_sysvar_unqualified", "unknown"}:
        raise TraceRejected("trace_rent_source_status")

    programs = _programs(trace["programs"])
    roles = _account_roles(trace["account_roles"])
    frames = _frames(trace["frames"], attempt_id, execution_id, programs)
    _snapshots(
        trace["account_snapshots"],
        attempt_id=attempt_id,
        execution_id=execution_id,
        roles=roles,
        frames=frames,
        disposition=disposition,
    )
    fee = _config(trace["fee_config"], "trace_fee_config", clock)
    hook = _config(trace["hook_config"], "trace_hook_config", clock)
    claims = _exact(trace["claims"], set(CLAIMS), "trace_claim_shape")
    if any(value is not False for value in claims.values()):
        raise TraceRejected("trace_qualification_claim")

    candidate = next(frame for frame in frames.values() if frame["candidate_path"])
    attempted_only = disposition != "transaction_success"
    return {
        "schema": "brain-senses-candidate-position-runtime-trace-v1-verification",
        "verification_status": "local_trace_consistent_unqualified",
        "attempt_id": attempt_id,
        "execution_id": execution_id,
        "candidate_path": list(candidate["path"]),
        "frame_count": len(frames),
        "account_role_count": len(roles),
        "account_snapshot_count": len(trace["account_snapshots"]),
        "transaction_disposition": disposition,
        "attempted_only": attempted_only,
        "hook_effect_status": "unsupported_or_unknown",
        "fee_runtime_law_status": "unqualified_raw_config_only",
        "native_settlement_status": "unqualified",
        "availability_status": availability["status"],
        "fee_config_pubkey": fee["pubkey"],
        "hook_config_pubkey": hook["pubkey"],
        "claims": dict(CLAIMS),
    }


__all__ = [
    "CLAIMS",
    "SCHEMA",
    "SCHEMA_VERSION",
    "TraceRejected",
    "canonical_json_bytes",
    "sha256",
    "verify_trace",
]
