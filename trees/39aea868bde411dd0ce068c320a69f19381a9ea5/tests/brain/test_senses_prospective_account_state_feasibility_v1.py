from __future__ import annotations

import copy
import hashlib

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from tools.brain_senses_prospective_account_state_feasibility_v1 import (
    ContractError,
    validate_record,
)


def _event(status: str, signature: str, slot: int, event_ns: int) -> dict:
    return {
        "status": status,
        "signature": signature,
        "slot": slot,
        "event_ns": event_ns,
        "received_ns": event_ns + 5,
        "available_ns": event_ns + 10,
        "source_evidence_sha256": hashlib.sha256(signature.encode()).hexdigest(),
    }


def _read(phase: str, event: dict, started: int) -> dict:
    raw = b"opaque provider-returned account bytes"
    return {
        "phase": phase,
        "source_signature": event["signature"],
        "source_slot": event["slot"],
        "account_pubkey": "token-account-1",
        "mint_pubkey": "mint-1",
        "token_account_owner_pubkey": "authority-1",
        "account_program_owner_pubkey": "token-program-1",
        "token_program_id": "token-program-1",
        "endpoint_id": "provider_mainnet_primary",
        "method": "getAccountInfo",
        "params": {"account_pubkey": "token-account-1", "commitment": "finalized"},
        "request_started_ns": started,
        "received_ns": started + 5,
        "available_ns": started + 10,
        "response_status": "account_data_observed",
        "raw_account_data_hex": raw.hex(),
        "raw_account_data_sha256": hashlib.sha256(raw).hexdigest(),
        "error_code": None,
    }


def _record() -> dict:
    acquisition = _event("finalized", "acquisition-signature", 10, 150)
    disposition = _event("observed_within_horizon", "disposition-signature", 20, 500)
    return {
        "schema": "brain-senses-prospective-account-state-feasibility-v1",
        "schema_version": 1,
        "preselection": {
            "position_id": "preselected-1",
            "selected_at_ns": 50,
            "selected_before_acquisition": True,
        },
        "horizon": {"starts_at_ns": 100, "ends_at_ns": 1000, "duration_ns": 900},
        "candidate_position": {
            "position_id": "preselected-1",
            "account_kind": "persistent_token_account",
            "account_pubkey": "token-account-1",
            "mint_pubkey": "mint-1",
            "token_account_owner_pubkey": "authority-1",
            "account_program_owner_pubkey": "token-program-1",
            "token_program_id": "token-program-1",
            "acquisition": acquisition,
            "disposition": disposition,
            "account_reads": [
                _read("after_finalized_acquisition", acquisition, 200),
                _read("after_observed_disposition", disposition, 510),
            ],
            "intervening_write_evidence": {
                "status": "evidence_rows_present",
                "rows": [
                    {
                        "account_pubkey": "token-account-1",
                        "signature": "intervening-write",
                        "slot": 15,
                        "event_ns": 300,
                        "received_ns": 305,
                        "available_ns": 310,
                        "evidence_sha256": hashlib.sha256(b"write evidence").hexdigest(),
                    }
                ],
            },
        },
        "claims": {
            "candidate_position_admitted": False,
            "economic_admission": False,
            "exact_pnl": False,
            "buyExact25_semantics_qualified": False,
            "historical_rent_or_proceeds_established": False,
            "decision_time_availability_inferred": False,
        },
    }


def _failed_record() -> dict:
    record = _record()
    position = record["candidate_position"]
    position["acquisition"] = {
        "status": "failed",
        "failure_code": "acquisition_unavailable",
        "source_evidence_sha256": None,
    }
    position["disposition"] = {
        "status": "failed_or_unobserved",
        "observed_through_ns": None,
        "source_evidence_sha256": None,
        "failure_code": "disposition_unavailable",
    }
    position["account_reads"] = []
    position["intervening_write_evidence"] = {
        "status": "unknown_or_incomplete",
        "rows": [],
    }
    return record


def test_valid_two_read_record_preserves_limited_feasibility_scope() -> None:
    result = validate_record(_record())
    assert result["candidate_position_denominator"] == 1
    assert result["account_read_count"] == 2
    assert result["admission"] == "none"
    assert result["decision_ns"] is None
    assert result["outcome_denominators"]["acquisition"]["finalized"] == 1
    assert result["outcome_denominators"]["disposition"]["observed_within_horizon"] == 1
    assert result["intervening_write_coverage"] == "unknown"


@given(raw=st.binary(max_size=512))
def test_raw_account_hex_roundtrips_for_arbitrary_bytes(raw: bytes) -> None:
    record = _record()
    for read in record["candidate_position"]["account_reads"]:
        read["raw_account_data_hex"] = raw.hex()
        read["raw_account_data_sha256"] = hashlib.sha256(raw).hexdigest()

    result = validate_record(record)

    assert result["account_read_count"] == 2
    assert result["admission"] == "none"


@given(tail=st.binary(max_size=128))
def test_raw_account_hex_rejects_noncanonical_text(tail: bytes) -> None:
    record = _record()
    raw = b"\xab" + tail
    read = record["candidate_position"]["account_reads"][0]
    read["raw_account_data_hex"] = raw.hex().upper()
    read["raw_account_data_sha256"] = hashlib.sha256(raw).hexdigest()

    with pytest.raises(
        ContractError, match="after_finalized_acquisition_raw_data_hex_noncanonical"
    ):
        validate_record(record)


@given(code=st.from_regex(r"[a-z][a-z0-9_.-]{0,63}", fullmatch=True))
@example("a")
@example("provider_timeout")
@example("rpc-429.retry")
def test_safe_failure_codes_preserve_failed_attempt_denominators(code: str) -> None:
    record = _failed_record()
    position = record["candidate_position"]
    position["acquisition"]["failure_code"] = code
    position["disposition"]["failure_code"] = code

    result = validate_record(record)

    assert result["outcome_denominators"]["acquisition"]["failed"] == 1
    assert result["outcome_denominators"]["disposition"]["failed_or_unobserved"] == 1
    assert result["admission"] == "none"


@given(payload=st.text(max_size=128))
def test_failure_codes_reject_freeform_provider_payloads(payload: str) -> None:
    record = _failed_record()
    record["candidate_position"]["acquisition"]["failure_code"] = f"provider_response={payload}"
    with pytest.raises(ContractError, match="acquisition_failure_code"):
        validate_record(record)

    record = _failed_record()
    record["candidate_position"]["disposition"]["failure_code"] = f"provider_response={payload}"
    with pytest.raises(ContractError, match="disposition_failure_code"):
        validate_record(record)


@pytest.mark.parametrize("status", ["failed", "unobserved"])
def test_failed_or_unobserved_acquisition_remains_in_denominator(status: str) -> None:
    record = _record()
    position = record["candidate_position"]
    position["acquisition"] = {
        "status": status,
        "failure_code": "acquisition_not_finalized",
        "source_evidence_sha256": None,
    }
    position["disposition"] = {
        "status": "failed_or_unobserved",
        "observed_through_ns": None,
        "source_evidence_sha256": None,
        "failure_code": "no_finalized_acquisition",
    }
    position["account_reads"] = []
    position["intervening_write_evidence"] = {"status": "unknown_or_incomplete", "rows": []}

    result = validate_record(record)
    assert result["candidate_position_denominator"] == 1
    assert result["outcome_denominators"]["acquisition"][status] == 1
    assert result["outcome_denominators"]["disposition"]["failed_or_unobserved"] == 1
    assert result["account_read_count"] == 0


def test_no_disposition_within_horizon_keeps_first_read_and_no_second_read() -> None:
    record = _record()
    position = record["candidate_position"]
    position["disposition"] = {
        "status": "none_within_horizon",
        "observed_through_ns": 1000,
        "source_evidence_sha256": hashlib.sha256(b"horizon observation").hexdigest(),
        "failure_code": None,
    }
    position["account_reads"] = position["account_reads"][:1]
    position["intervening_write_evidence"] = {"status": "unknown_or_incomplete", "rows": []}

    result = validate_record(record)
    assert result["disposition_status"] == "none_within_horizon"
    assert result["account_read_count"] == 1
    assert result["outcome_denominators"]["disposition"]["none_within_horizon"] == 1


def test_no_disposition_requires_observation_through_fixed_horizon() -> None:
    record = _record()
    position = record["candidate_position"]
    position["disposition"] = {
        "status": "none_within_horizon",
        "observed_through_ns": 999,
        "source_evidence_sha256": hashlib.sha256(b"horizon observation").hexdigest(),
        "failure_code": None,
    }
    position["account_reads"] = position["account_reads"][:1]
    position["intervening_write_evidence"] = {"status": "unknown_or_incomplete", "rows": []}
    with pytest.raises(ContractError, match="disposition_horizon_not_complete"):
        validate_record(record)


def test_failed_account_read_retains_request_identity_and_explicit_null_bytes() -> None:
    record = _record()
    read = record["candidate_position"]["account_reads"][0]
    read["response_status"] = "request_failed"
    read["raw_account_data_hex"] = None
    read["raw_account_data_sha256"] = None
    read["error_code"] = "transport_failure"
    result = validate_record(record)
    assert result["account_read_count"] == 2


def test_nullable_event_time_does_not_replace_receipt_or_availability() -> None:
    record = _record()
    record["candidate_position"]["acquisition"]["event_ns"] = None
    result = validate_record(record)
    assert result["account_read_count"] == 2


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda value: value["candidate_position"]["account_reads"].append(
                copy.deepcopy(value["candidate_position"]["account_reads"][0])
            ),
            "account_read_limit",
        ),
        (
            lambda value: value["candidate_position"]["account_reads"].reverse(),
            "account_read_order",
        ),
        (
            lambda value: value["candidate_position"]["account_reads"][0].update(
                {"source_signature": "other-signature"}
            ),
            "after_finalized_acquisition_source_signature",
        ),
        (
            lambda value: value["candidate_position"]["account_reads"][0].update(
                {"token_account_owner_pubkey": "other-owner"}
            ),
            "after_finalized_acquisition_token_account_owner_pubkey_binding",
        ),
        (
            lambda value: value["candidate_position"]["account_reads"][0].update(
                {"raw_account_data_sha256": "0" * 64}
            ),
            "after_finalized_acquisition_raw_data_hash_mismatch",
        ),
        (
            lambda value: value["candidate_position"]["account_reads"][1].update(
                {"request_started_ns": 490, "received_ns": 495, "available_ns": 499}
            ),
            "after_observed_disposition_time_order",
        ),
        (
            lambda value: value["candidate_position"]["account_reads"][1].update(
                {"method": "getBalance"}
            ),
            "after_observed_disposition_method",
        ),
        (
            lambda value: value["candidate_position"]["account_reads"][0].update(
                {"endpoint_id": "https://provider.example/rpc"}
            ),
            "after_finalized_acquisition_endpoint_id",
        ),
        (
            lambda value: value["candidate_position"]["account_reads"][0]["params"].update(
                {"api_key": "do-not-store"}
            ),
            "after_finalized_acquisition_params_fields",
        ),
        (
            lambda value: value["candidate_position"]["account_reads"][0].update(
                {"request_started_ns": 159, "received_ns": 165, "available_ns": 170}
            ),
            "after_finalized_acquisition_time_order",
        ),
        (
            lambda value: value["candidate_position"]["account_reads"][0].update(
                {
                    "response_status": "request_failed",
                    "raw_account_data_hex": None,
                    "raw_account_data_sha256": None,
                    "error_code": "api_key=secret",
                }
            ),
            "after_finalized_acquisition_failure_error",
        ),
        (
            lambda value: value["candidate_position"]["account_reads"][0].update(
                {"response_status": "account_absent"}
            ),
            "after_finalized_acquisition_null_raw_data_pair",
        ),
        (
            lambda value: value["claims"].update({"economic_admission": True}),
            "claims_must_remain_false",
        ),
    ],
)
def test_mutations_fail_closed(mutate, code: str) -> None:
    record = _record()
    mutate(record)
    with pytest.raises(ContractError, match=code):
        validate_record(record)


def test_disposition_read_requires_an_observed_disposition() -> None:
    record = _record()
    record["candidate_position"]["disposition"] = {
        "status": "none_within_horizon",
        "observed_through_ns": 1000,
        "source_evidence_sha256": hashlib.sha256(b"horizon observation").hexdigest(),
        "failure_code": None,
    }
    with pytest.raises(ContractError, match="read_without_observed_disposition"):
        validate_record(record)


def test_incomplete_write_evidence_is_retained_without_implying_no_writes() -> None:
    record = _record()
    record["candidate_position"]["intervening_write_evidence"] = {
        "status": "unknown_or_incomplete",
        "rows": [],
    }
    result = validate_record(record)
    assert result["intervening_write_evidence_status"] == "unknown_or_incomplete"


def test_intervening_write_evidence_must_fall_between_account_reads() -> None:
    record = _record()
    row = record["candidate_position"]["intervening_write_evidence"]["rows"][0]
    row.update({"event_ns": 600, "received_ns": 605, "available_ns": 610})
    with pytest.raises(ContractError, match="write_outside_account_read_interval"):
        validate_record(record)
