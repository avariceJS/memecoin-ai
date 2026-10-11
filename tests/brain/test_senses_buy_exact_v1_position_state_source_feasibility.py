from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tools.brain_senses_buy_exact_v1_position_state_source_feasibility_v1 import (
    FeasibilityError,
    _classify,
    _synthetic_boundary_alias,
)


def _row(*, status: str, category: str, token_pair: str = "contains_token2022") -> dict:
    return {
        "identity": {"slot": 10, "transaction_index": 2, "path": [0, 1], "signature": "sig"},
        "population_category": category,
        "transaction_status": status,
        "economic_admission": False,
        "state_and_position_limits": ["transaction boundaries only"] * 4,
        "candidate_roles_token_programs": {
            "base_token_program": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
            if token_pair == "contains_token2022"
            else "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            "quote_token_program": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        },
        "candidate_token_program_pair": token_pair,
        "provider_endpoint_rows": [],
        "raw_transaction_evidence": {"wsol_role_names": []},
    }


@given(
    amount_atomic=st.integers(min_value=1, max_value=2**63 - 1),
    slack_atomic=st.integers(min_value=0, max_value=2**63 - 1),
)
def test_equal_transaction_endpoints_do_not_identify_candidate_position_state(
    amount_atomic: int, slack_atomic: int
):
    start_atomic = amount_atomic + slack_atomic
    result = _synthetic_boundary_alias(amount_atomic, start_atomic)
    assert result["unit"] == "atomic_units_of_mint"
    assert result["end_a_atomic"] == result["end_b_atomic"] == start_atomic
    assert result["candidate_position_a_atomic"] != result["candidate_position_b_atomic"]
    assert result["candidate_position_diff_atomic"] == 2 * amount_atomic


def test_failed_attempt_is_exact_disposition_without_zero_label():
    result = _classify(_row(status="failed_attempt_only", category="failed_structural_mismatch"))
    assert result["transaction_disposition"] == "failed_attempt_exact"
    assert result["candidate_position_state"] == "not_applicable_failed_attempt"
    assert "economic zero" in result["candidate_position_state_reason"]
    assert result["required_state_or_semantics"] == []


def test_successful_structural_pass_requires_missing_position_and_token2022_state():
    result = _classify(_row(status="successful_transaction", category="successful_structural_pass"))
    assert result["candidate_position_state"] == "unavailable_from_documented_sources"
    assert any("TransferFeeConfig" in item for item in result["required_state_or_semantics"])
    assert any(
        "TransferHook" in item or "transfer-hook" in item
        for item in result["required_state_or_semantics"]
    )
    assert result["economic_admission"] is False


def test_successful_structural_mismatch_remains_unknown():
    result = _classify(
        _row(status="successful_transaction", category="successful_structural_mismatch")
    )
    assert result["candidate_position_state"] == "unknown"
    assert "instruction-class qualification" in result["required_state_or_semantics"]


def test_dual_legacy_success_does_not_require_token2022_extensions():
    result = _classify(
        _row(
            status="successful_transaction",
            category="successful_structural_pass",
            token_pair="dual_legacy",
        )
    )
    requirements = result["required_state_or_semantics"]
    assert result["candidate_position_state"] == "unavailable_from_documented_sources"
    assert not any("TransferFeeConfig" in item for item in requirements)
    assert not any("TransferHook" in item or "transfer-hook" in item for item in requirements)


def test_wsol_requirement_uses_recorded_role_names():
    row = _row(status="successful_transaction", category="successful_structural_pass")
    row["raw_transaction_evidence"]["wsol_role_names"] = ["quote_mint"]
    result = _classify(row)
    assert any("WSOL token amount" in item for item in result["required_state_or_semantics"])


def test_inconsistent_status_and_category_fails_closed():
    row = _row(status="failed_attempt_only", category="successful_structural_pass")
    with pytest.raises(FeasibilityError, match="population_status_category_inconsistent"):
        _classify(row)


def test_missing_wsol_role_evidence_fails_closed():
    row = _row(status="successful_transaction", category="successful_structural_pass")
    del row["raw_transaction_evidence"]
    with pytest.raises(FeasibilityError, match="raw_transaction_evidence_missing"):
        _classify(row)
