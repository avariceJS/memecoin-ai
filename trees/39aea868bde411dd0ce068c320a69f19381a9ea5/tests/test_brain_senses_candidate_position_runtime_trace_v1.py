from __future__ import annotations

import copy
import hashlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools.brain_senses_candidate_position_runtime_trace_v1 import (
    CLAIMS,
    SCHEMA,
    TraceRejected,
    canonical_json_bytes,
    verify_trace,
)


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fixture(*, disposition: str = "transaction_success") -> dict:
    data = b"raw-token-account"
    clock = b"raw-clock-sysvar"
    rent = b"raw-rent-sysvar"
    fee = b"raw-fee-config"
    hook = b"raw-hook-config"
    phases = ["pre", "post"] if disposition == "transaction_success" else ["pre", "attempted"]
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "source_pins": {
            "agave_commit": "a" * 40,
            "patch_sha256": "b" * 64,
            "cargo_lock_sha256": "c" * 64,
            "runtime_source_sha256": "d" * 64,
            "feature_set_sha256": "e" * 64,
        },
        "attempt": {
            "attempt_id": "attempt-1",
            "execution_id": "execution-1",
            "signature_sha256": "f" * 64,
            "message_sha256": "1" * 64,
            "disposition": disposition,
        },
        "availability": {
            "status": "receipt_bounded",
            "provider_receipt_ns": 100,
            "durable_available_ns": 110,
            "clock_uncertainty_ns": 1,
            "clock_evidence_sha256": "2" * 64,
        },
        "clock": {
            "slot": 7,
            "epoch": 3,
            "raw_bytes_hex": clock.hex(),
            "raw_sha256": _hash(clock),
            "source_status": "raw_runtime_sysvar_unqualified",
        },
        "rent": {
            "slot": 7,
            "epoch": 3,
            "lamports_per_byte_year": 3480,
            "exemption_threshold_numerator": 2,
            "exemption_threshold_denominator": 1,
            "raw_bytes_hex": rent.hex(),
            "raw_sha256": _hash(rent),
            "source_status": "raw_runtime_sysvar_unqualified",
        },
        "programs": [
            {
                "program_id": "candidate-program",
                "identity_kind": "deployed_bytecode",
                "owner": "loader",
                "executable_sha256": "3" * 64,
                "source_status": "pinned_local_source_unqualified",
            }
        ],
        "account_roles": [
            {
                "role": "candidate_token_account",
                "pubkey": "account-key",
                "owner": "token-program",
                "mint": "mint-key",
                "token_program": "token-program",
            }
        ],
        "frames": [
            {
                "attempt_id": "attempt-1",
                "execution_id": "execution-1",
                "ordinal": 0,
                "path": [0],
                "parent_path": [],
                "stack_height": 1,
                "program_id": "candidate-program",
                "instruction_data_sha256": "4" * 64,
                "entry_status": "entered",
                "return_status": "success" if disposition == "transaction_success" else "failed",
                "candidate_path": True,
            }
        ],
        "account_snapshots": [
            {
                "attempt_id": "attempt-1",
                "execution_id": "execution-1",
                "frame_path": [0],
                "role": "candidate_token_account",
                "phase": phase,
                "pubkey": "account-key",
                "owner": "token-program",
                "mint": "mint-key",
                "token_program": "token-program",
                "lamports": 2,
                "rent_epoch": 0,
                "data_bytes_hex": data.hex(),
                "data_sha256": _hash(data),
            }
            for phase in phases
        ],
        "fee_config": {
            "pubkey": "fee-config",
            "owner": "token-program",
            "raw_bytes_hex": fee.hex(),
            "raw_sha256": _hash(fee),
            "slot": 7,
            "epoch": 3,
        },
        "hook_config": {
            "pubkey": "hook-config",
            "owner": "hook-program",
            "raw_bytes_hex": hook.hex(),
            "raw_sha256": _hash(hook),
            "slot": 7,
            "epoch": 3,
        },
        "claims": dict(CLAIMS),
    }


def test_valid_trace_is_only_local_consistency_and_keeps_claims_false() -> None:
    result = verify_trace(_fixture())
    assert result["verification_status"] == "local_trace_consistent_unqualified"
    assert result["attempted_only"] is False
    assert result["claims"] == CLAIMS
    assert result["hook_effect_status"] == "unsupported_or_unknown"


def test_failed_execution_remains_attempted_only() -> None:
    result = verify_trace(_fixture(disposition="transaction_failed"))
    assert result["attempted_only"] is True
    assert result["transaction_disposition"] == "transaction_failed"


def test_raw_byte_mutation_without_hash_update_is_rejected() -> None:
    trace = _fixture()
    trace["account_snapshots"][0]["data_bytes_hex"] = b"mutated".hex()
    with pytest.raises(TraceRejected, match="trace_snapshot_bytes"):
        verify_trace(trace)


@given(raw=st.binary(max_size=512))
@settings(max_examples=60, deadline=None)
def test_arbitrary_account_bytes_are_accepted_only_with_matching_hash(raw: bytes) -> None:
    trace = _fixture()
    for row in trace["account_snapshots"]:
        row["data_bytes_hex"] = raw.hex()
        row["data_sha256"] = _hash(raw)
    result = verify_trace(trace)
    assert result["account_snapshot_count"] == 2


@given(identity=st.text(min_size=1, max_size=40).filter(lambda value: value.strip() == value))
@settings(max_examples=50, deadline=None)
def test_cross_attempt_frame_identity_is_always_rejected(identity: str) -> None:
    trace = _fixture()
    trace["frames"][0]["attempt_id"] = identity
    if identity == "attempt-1":
        trace["frames"][0]["execution_id"] = "other-execution"
    with pytest.raises(TraceRejected, match="trace_frame_cross_attempt"):
        verify_trace(trace)


@given(
    field=st.sampled_from(
        [
            "provider_receipt_ns",
            "durable_available_ns",
            "clock_uncertainty_ns",
            "clock_evidence_sha256",
        ]
    )
)
def test_unknown_availability_cannot_smuggle_timestamps_or_provenance(field: str) -> None:
    trace = _fixture()
    trace["availability"] = {
        "status": "unknown",
        "provider_receipt_ns": None,
        "durable_available_ns": None,
        "clock_uncertainty_ns": None,
        "clock_evidence_sha256": None,
    }
    trace["availability"][field] = 0 if field != "clock_evidence_sha256" else "2" * 64
    with pytest.raises(TraceRejected, match="trace_unknown_availability_has_values"):
        verify_trace(trace)


@given(lamports=st.integers(min_value=0, max_value=(1 << 64) - 1))
@settings(max_examples=60, deadline=None)
def test_unsigned_lamport_domain_includes_zero_and_u64_boundary(lamports: int) -> None:
    trace = _fixture()
    for row in trace["account_snapshots"]:
        row["lamports"] = lamports
    assert verify_trace(trace)["verification_status"] == "local_trace_consistent_unqualified"


def test_canonical_json_is_deterministic_and_rejects_nonfinite_values() -> None:
    trace = _fixture()
    assert canonical_json_bytes(trace) == canonical_json_bytes(copy.deepcopy(trace))
    trace["rent"]["lamports_per_byte_year"] = float("nan")
    with pytest.raises(TraceRejected, match="trace_not_canonicalizable"):
        canonical_json_bytes(trace)


def test_source_commit_and_hashes_require_exact_lowercase_hex() -> None:
    trace = _fixture()
    trace["source_pins"]["agave_commit"] = "a" * 39 + "G"
    with pytest.raises(TraceRejected, match="trace_source_commit"):
        verify_trace(trace)


def test_qualification_claims_require_false_boolean_not_numeric_zero() -> None:
    trace = _fixture()
    trace["claims"]["economic_admission"] = 0
    with pytest.raises(TraceRejected, match="trace_qualification_claim"):
        verify_trace(trace)


def test_schema_version_requires_integer_type() -> None:
    trace = _fixture()
    trace["schema_version"] = True
    with pytest.raises(TraceRejected, match="trace_schema"):
        verify_trace(trace)
