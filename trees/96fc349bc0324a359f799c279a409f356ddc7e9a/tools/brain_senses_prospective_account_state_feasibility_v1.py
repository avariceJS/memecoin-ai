"""Validate a prospective, two-endpoint account-state feasibility record.

This is an inert provider-agnostic contract checker. It performs no network, storage,
transaction, balance-decoding, settlement, or admission operation.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any, cast

SCHEMA = "brain-senses-prospective-account-state-feasibility-v1"
SCHEMA_VERSION = 1
READ_METHOD = "getAccountInfo"
READ_PHASES = ("after_finalized_acquisition", "after_observed_disposition")
ACQUISITION_STATES = {"finalized", "failed", "unobserved"}
DISPOSITION_STATES = {"observed_within_horizon", "none_within_horizon", "failed_or_unobserved"}
CLAIMS = {
    "candidate_position_admitted": False,
    "economic_admission": False,
    "exact_pnl": False,
    "buyExact25_semantics_qualified": False,
    "historical_rent_or_proceeds_established": False,
    "decision_time_availability_inferred": False,
}

_HEX_32 = re.compile(r"[0-9a-f]{64}\Z")
_ENDPOINT_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_SAFE_ERROR_CODE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_TOP_FIELDS = {
    "schema",
    "schema_version",
    "preselection",
    "horizon",
    "candidate_position",
    "claims",
}
_PRESELECTION_FIELDS = {"position_id", "selected_at_ns", "selected_before_acquisition"}
_HORIZON_FIELDS = {"starts_at_ns", "ends_at_ns", "duration_ns"}
_POSITION_FIELDS = {
    "position_id",
    "account_kind",
    "account_pubkey",
    "mint_pubkey",
    "token_account_owner_pubkey",
    "account_program_owner_pubkey",
    "token_program_id",
    "acquisition",
    "disposition",
    "account_reads",
    "intervening_write_evidence",
}
_EVENT_FIELDS = {
    "status",
    "signature",
    "slot",
    "event_ns",
    "received_ns",
    "available_ns",
    "source_evidence_sha256",
}
_READ_FIELDS = {
    "phase",
    "source_signature",
    "source_slot",
    "account_pubkey",
    "mint_pubkey",
    "token_account_owner_pubkey",
    "account_program_owner_pubkey",
    "token_program_id",
    "endpoint_id",
    "method",
    "params",
    "request_started_ns",
    "received_ns",
    "available_ns",
    "response_status",
    "raw_account_data_hex",
    "raw_account_data_sha256",
    "error_code",
}
_WRITE_EVIDENCE_FIELDS = {"status", "rows"}
_WRITE_FIELDS = {
    "account_pubkey",
    "signature",
    "slot",
    "event_ns",
    "received_ns",
    "available_ns",
    "evidence_sha256",
}
_NONFINAL_ACQUISITION_FIELDS = {"status", "failure_code", "source_evidence_sha256"}
_NONOBSERVED_DISPOSITION_FIELDS = {
    "status",
    "observed_through_ns",
    "source_evidence_sha256",
    "failure_code",
}


class ContractError(ValueError):
    """A prospective account-state feasibility record violates its frozen shape."""


def _reject(condition: bool, code: str) -> None:
    if not condition:
        raise ContractError(code)


def _exact(value: Any, fields: set[str], code: str) -> Mapping[str, Any]:
    _reject(isinstance(value, Mapping) and set(value) == fields, code)
    return cast(Mapping[str, Any], value)


def _text(value: Any, code: str) -> str:
    _reject(isinstance(value, str) and bool(value.strip()), code)
    return cast(str, value)


def _safe_code(value: Any, code: str) -> str:
    _reject(isinstance(value, str) and _SAFE_ERROR_CODE.fullmatch(value) is not None, code)
    return cast(str, value)


def _ns(value: Any, code: str) -> int:
    _reject(type(value) is int and value >= 0, code)
    return cast(int, value)


def _digest(value: Any, code: str) -> str:
    _reject(isinstance(value, str) and _HEX_32.fullmatch(value) is not None, code)
    return cast(str, value)


def _event(value: Any, *, expected_status: str, code: str) -> Mapping[str, Any]:
    event = _exact(value, _EVENT_FIELDS, f"{code}_shape")
    _reject(event.get("status") == expected_status, f"{code}_status")
    _text(event.get("signature"), f"{code}_signature")
    _ns(event.get("slot"), f"{code}_slot")
    event_ns = event.get("event_ns")
    if event_ns is not None:
        event_ns = _ns(event_ns, f"{code}_event_time")
    received = _ns(event.get("received_ns"), f"{code}_received_time")
    available = _ns(event.get("available_ns"), f"{code}_available_time")
    _reject(received <= available, f"{code}_receipt_availability_order")
    if event_ns is not None:
        _reject(event_ns <= received, f"{code}_event_receipt_order")
    _digest(event.get("source_evidence_sha256"), f"{code}_source_evidence")
    return event


def _read(
    value: Any,
    *,
    phase: str,
    position: Mapping[str, Any],
    source_event: Mapping[str, Any],
    not_before_ns: int,
    horizon_end_ns: int,
) -> Mapping[str, Any]:
    read = _exact(value, _READ_FIELDS, f"{phase}_shape")
    _reject(read.get("phase") == phase, f"{phase}_phase")
    _reject(read.get("source_signature") == source_event["signature"], f"{phase}_source_signature")
    _reject(read.get("source_slot") == source_event["slot"], f"{phase}_source_slot")
    for field in (
        "account_pubkey",
        "mint_pubkey",
        "token_account_owner_pubkey",
        "account_program_owner_pubkey",
        "token_program_id",
    ):
        _reject(read.get(field) == position.get(field), f"{phase}_{field}_binding")
    endpoint_id = read.get("endpoint_id")
    _reject(
        isinstance(endpoint_id, str) and _ENDPOINT_ID.fullmatch(endpoint_id) is not None,
        f"{phase}_endpoint_id",
    )
    _reject(read.get("method") == READ_METHOD, f"{phase}_method")
    params = read.get("params")
    _reject(isinstance(params, Mapping), f"{phase}_params")
    params = cast(Mapping[str, Any], params)
    _reject(
        set(params)
        in (
            {"account_pubkey", "commitment"},
            {"account_pubkey", "commitment", "encoding"},
        ),
        f"{phase}_params_fields",
    )
    _reject(params.get("account_pubkey") == position["account_pubkey"], f"{phase}_params_account")
    _reject(params.get("commitment") == "finalized", f"{phase}_params_commitment")
    if "encoding" in params:
        _reject(
            params.get("encoding") in {"base58", "base64", "base64+zstd", "jsonParsed"},
            f"{phase}_params_encoding",
        )
    started = _ns(read.get("request_started_ns"), f"{phase}_request_started")
    received = _ns(read.get("received_ns"), f"{phase}_received")
    available = _ns(read.get("available_ns"), f"{phase}_available")
    _reject(not_before_ns <= started <= received <= available, f"{phase}_time_order")
    _reject(started <= horizon_end_ns, f"{phase}_outside_horizon")

    status = read.get("response_status")
    raw_hex = read.get("raw_account_data_hex")
    raw_hash = read.get("raw_account_data_sha256")
    error_code = read.get("error_code")
    if status == "account_data_observed":
        _reject(isinstance(raw_hex, str), f"{phase}_raw_data_missing")
        try:
            raw = bytes.fromhex(cast(str, raw_hex))
        except ValueError as exc:
            raise ContractError(f"{phase}_raw_data_hex") from exc
        _reject(raw.hex() == raw_hex, f"{phase}_raw_data_hex_noncanonical")
        _reject(
            _digest(raw_hash, f"{phase}_raw_data_hash") == hashlib.sha256(raw).hexdigest(),
            f"{phase}_raw_data_hash_mismatch",
        )
        _reject(error_code is None, f"{phase}_success_has_error")
    elif status in {"account_absent", "request_failed"}:
        _reject(raw_hex is None and raw_hash is None, f"{phase}_null_raw_data_pair")
        if status == "account_absent":
            _reject(error_code is None, f"{phase}_absent_has_error")
        else:
            _reject(
                isinstance(error_code, str) and _SAFE_ERROR_CODE.fullmatch(error_code) is not None,
                f"{phase}_failure_error",
            )
    else:
        raise ContractError(f"{phase}_response_status")
    return read


def validate_record(value: Any) -> dict[str, Any]:
    """Validate one preselected prospective position and return non-admission summary."""
    record = _exact(value, _TOP_FIELDS, "record_shape")
    _reject(record.get("schema") == SCHEMA, "schema")
    _reject(record.get("schema_version") == SCHEMA_VERSION, "schema_version")

    preselection = _exact(record.get("preselection"), _PRESELECTION_FIELDS, "preselection_shape")
    position_id = _text(preselection.get("position_id"), "position_id")
    selected_at = _ns(preselection.get("selected_at_ns"), "selection_time")
    _reject(preselection.get("selected_before_acquisition") is True, "not_preselected")

    horizon = _exact(record.get("horizon"), _HORIZON_FIELDS, "horizon_shape")
    horizon_start = _ns(horizon.get("starts_at_ns"), "horizon_start")
    horizon_end = _ns(horizon.get("ends_at_ns"), "horizon_end")
    duration = _ns(horizon.get("duration_ns"), "horizon_duration")
    _reject(horizon_start >= selected_at, "horizon_before_selection")
    _reject(
        horizon_end > horizon_start and horizon_end - horizon_start == duration,
        "horizon_duration_mismatch",
    )

    position = _exact(record.get("candidate_position"), _POSITION_FIELDS, "position_shape")
    _reject(position.get("position_id") == position_id, "position_identity")
    _reject(position.get("account_kind") == "persistent_token_account", "account_kind")
    for field in (
        "account_pubkey",
        "mint_pubkey",
        "token_account_owner_pubkey",
        "account_program_owner_pubkey",
        "token_program_id",
    ):
        _text(position.get(field), f"position_{field}")

    acquisition = position.get("acquisition")
    disposition = position.get("disposition")
    reads = position.get("account_reads")
    writes = _exact(
        position.get("intervening_write_evidence"), _WRITE_EVIDENCE_FIELDS, "write_evidence_shape"
    )
    write_status = writes.get("status")
    _text(write_status, "write_evidence_status")
    _reject(
        write_status in {"evidence_rows_present", "unknown_or_incomplete"},
        "write_evidence_status",
    )
    write_rows = writes.get("rows")
    _reject(isinstance(write_rows, list), "write_rows_shape")
    write_rows = cast(list[Any], write_rows)
    for row in write_rows:
        item = _exact(row, _WRITE_FIELDS, "write_row_shape")
        _reject(
            item.get("account_pubkey") == position.get("account_pubkey"),
            "write_account_binding",
        )
        _text(item.get("signature"), "write_signature")
        _ns(item.get("slot"), "write_slot")
        event_ns = item.get("event_ns")
        if event_ns is not None:
            _ns(event_ns, "write_event_time")
        received = _ns(item.get("received_ns"), "write_received_time")
        available = _ns(item.get("available_ns"), "write_available_time")
        _reject(received <= available, "write_receipt_availability_order")
        if event_ns is not None:
            _reject(event_ns <= received, "write_event_receipt_order")
        _digest(item.get("evidence_sha256"), "write_evidence_hash")
    if write_status == "evidence_rows_present":
        _reject(len(write_rows) > 0, "observed_write_rows_empty")
    elif write_status == "unknown_or_incomplete":
        pass

    _reject(isinstance(reads, list) and len(reads) <= 2, "account_read_limit")
    reads = cast(list[Any], reads)
    allowed_read_phases = set(READ_PHASES)
    read_phases = [row.get("phase") if isinstance(row, Mapping) else None for row in reads]
    _reject(all(phase in allowed_read_phases for phase in read_phases), "unknown_read_phase")
    _reject(len(set(read_phases)) == len(read_phases), "duplicate_read_phase")
    _reject(read_phases == sorted(read_phases, key=READ_PHASES.index), "account_read_order")
    if "after_observed_disposition" in read_phases:
        _reject("after_finalized_acquisition" in read_phases, "second_read_without_first")

    acquisition_state = acquisition.get("status") if isinstance(acquisition, Mapping) else None
    if acquisition_state == "finalized":
        acquisition = _event(acquisition, expected_status="finalized", code="acquisition")
        acquisition_available = _ns(acquisition.get("available_ns"), "acquisition_available_time")
        _reject(
            horizon_start <= acquisition_available <= horizon_end,
            "acquisition_availability_outside_horizon",
        )
        acquisition_event_time = acquisition.get("event_ns")
        if acquisition_event_time is not None:
            _reject(
                horizon_start <= acquisition_event_time <= horizon_end,
                "acquisition_event_outside_horizon",
            )
    elif acquisition_state in {"failed", "unobserved"}:
        acquisition = _exact(
            acquisition, _NONFINAL_ACQUISITION_FIELDS, "acquisition_nonfinal_shape"
        )
        _safe_code(acquisition.get("failure_code"), "acquisition_failure_code")
        evidence_hash = acquisition.get("source_evidence_sha256")
        if evidence_hash is not None:
            _digest(evidence_hash, "acquisition_source_evidence")
        _reject(not reads, "read_without_finalized_acquisition")
        _reject(
            isinstance(disposition, Mapping)
            and disposition.get("status") == "failed_or_unobserved",
            "disposition_without_acquisition",
        )
    else:
        raise ContractError("acquisition_status")

    disposition_state = disposition.get("status") if isinstance(disposition, Mapping) else None
    if disposition_state == "observed_within_horizon":
        disposition = _event(disposition, expected_status=disposition_state, code="disposition")
        _reject(acquisition_state == "finalized", "disposition_without_finalized_acquisition")
        disposition_available = _ns(disposition.get("available_ns"), "disposition_available_time")
        _reject(
            horizon_start <= disposition_available <= horizon_end,
            "disposition_availability_outside_horizon",
        )
        disposition_event_time = disposition.get("event_ns")
        if disposition_event_time is not None:
            _reject(
                horizon_start <= disposition_event_time <= horizon_end,
                "disposition_event_outside_horizon",
            )
    elif disposition_state in {"none_within_horizon", "failed_or_unobserved"}:
        disposition = _exact(
            disposition,
            _NONOBSERVED_DISPOSITION_FIELDS,
            "disposition_nonobserved_shape",
        )
        if disposition_state == "none_within_horizon":
            observed_through = _ns(
                disposition.get("observed_through_ns"), "disposition_observed_through"
            )
            _reject(observed_through >= horizon_end, "disposition_horizon_not_complete")
            _digest(
                disposition.get("source_evidence_sha256"),
                "disposition_absence_evidence",
            )
            _reject(disposition.get("failure_code") is None, "absence_has_failure_code")
        else:
            _reject(disposition.get("observed_through_ns") is None, "failure_has_observed_through")
            _safe_code(disposition.get("failure_code"), "disposition_failure_code")
            evidence_hash = disposition.get("source_evidence_sha256")
            if evidence_hash is not None:
                _digest(evidence_hash, "disposition_source_evidence")
        _reject(
            not any(phase == "after_observed_disposition" for phase in read_phases),
            "read_without_observed_disposition",
        )
    else:
        raise ContractError("disposition_status")

    source_events = {
        "after_finalized_acquisition": acquisition if acquisition_state == "finalized" else None,
        "after_observed_disposition": disposition
        if disposition_state == "observed_within_horizon"
        else None,
    }
    minimum_times = {
        "after_finalized_acquisition": (
            acquisition["available_ns"] if acquisition_state == "finalized" else None
        ),
        "after_observed_disposition": (
            disposition["available_ns"] if disposition_state == "observed_within_horizon" else None
        ),
    }
    for read in reads:
        phase = read["phase"]
        source_event = source_events[phase]
        not_before = minimum_times[phase]
        _reject(source_event is not None and not_before is not None, "read_phase_event_missing")
        source_event = cast(Mapping[str, Any], source_event)
        not_before = cast(int, not_before)
        _read(
            read,
            phase=phase,
            position=position,
            source_event=source_event,
            not_before_ns=not_before,
            horizon_end_ns=horizon_end,
        )

    if len(reads) == 2:
        first_available = cast(int, reads[0]["available_ns"])
        second_started = cast(int, reads[1]["request_started_ns"])
        for row in write_rows:
            event_ns = row["event_ns"]
            if event_ns is not None:
                _reject(
                    first_available <= event_ns <= second_started,
                    "write_outside_account_read_interval",
                )

    if acquisition_state == "finalized":
        lower_write_time = cast(int, acquisition["available_ns"])
        upper_write_time = (
            cast(int, disposition["available_ns"])
            if disposition_state == "observed_within_horizon"
            else cast(int, disposition["observed_through_ns"])
            if disposition_state == "none_within_horizon"
            else horizon_end
        )
        write_signatures: set[str] = set()
        for row in write_rows:
            signature = cast(str, row["signature"])
            _reject(signature not in write_signatures, "duplicate_write_signature")
            write_signatures.add(signature)
            _reject(
                row["event_ns"] is None or lower_write_time <= row["event_ns"] <= upper_write_time,
                "write_time_outside_observation_interval",
            )

    claims = _exact(record.get("claims"), set(CLAIMS), "claims_shape")
    _reject(dict(claims) == CLAIMS, "claims_must_remain_false")
    _reject(selected_at <= horizon_start, "selection_horizon_order")

    return {
        "schema": SCHEMA,
        "candidate_position_denominator": 1,
        "position_id": position_id,
        "acquisition_status": acquisition_state,
        "disposition_status": disposition_state,
        "account_read_count": len(reads),
        "account_read_limit": 2,
        "outcome_denominators": {
            "acquisition": {
                "finalized": int(acquisition_state == "finalized"),
                "failed": int(acquisition_state == "failed"),
                "unobserved": int(acquisition_state == "unobserved"),
            },
            "disposition": {
                "observed_within_horizon": int(disposition_state == "observed_within_horizon"),
                "none_within_horizon": int(disposition_state == "none_within_horizon"),
                "failed_or_unobserved": int(disposition_state == "failed_or_unobserved"),
            },
        },
        "intervening_write_evidence_status": write_status,
        "intervening_write_coverage": "unknown",
        "admission": "none",
        "decision_ns": None,
        "claims": dict(CLAIMS),
    }
