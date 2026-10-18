from __future__ import annotations

from itertools import product

from memecoin_pumpswap.decoder import TOKEN, TOKEN2022
from tools.brain_senses_buy_exact_v1_token2022_evidence_census import (
    _balance_evidence,
    _candidate,
)


def test_candidate_predicate_accepts_only_the_frozen_successful_quote_side_class():
    for status, arguments, suffix, base_program in product(
        ("success", "failed"),
        ("pinned_idl_v1_shape", "unsupported_payload_shape"),
        (
            "documented_fee_pair_suffix_candidate",
            "documented_fee_pair_with_unresolved_predecessor",
        ),
        (TOKEN, TOKEN2022),
    ):
        row = {
            "transaction_status": status,
            "argument_state": arguments,
            "suffix_state": suffix,
            "identified_roles": {
                "base_token_program": base_program,
                "quote_token_program": TOKEN2022,
            },
        }
        assert _candidate(row) is (
            status == "success"
            and arguments == "pinned_idl_v1_shape"
            and suffix == "documented_fee_pair_suffix_candidate"
            and base_program == TOKEN
        )


def test_missing_transaction_boundary_balance_trace_stays_unknown_not_zero():
    result = _balance_evidence(
        {"accounts": [], "pre_token_balances": None, "post_token_balances": None},
        {"target-account"},
    )

    assert result == {
        "pre_token_balances": {"state": "unknown_balance_trace_unavailable"},
        "post_token_balances": {"state": "unknown_balance_trace_unavailable"},
    }


def test_present_empty_balance_rows_remain_empty_evidence_not_inferred_zero():
    result = _balance_evidence(
        {"accounts": [], "pre_token_balances": [], "post_token_balances": []},
        {"target-account"},
    )

    assert result == {"pre_token_balances": {}, "post_token_balances": {}}
