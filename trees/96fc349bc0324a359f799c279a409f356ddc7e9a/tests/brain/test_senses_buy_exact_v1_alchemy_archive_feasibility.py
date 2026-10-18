from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tools.brain_senses_buy_exact_v1_alchemy_archive_feasibility_v1 import (
    FeasibilityError,
    _row_facts,
    _whole_prefix_single_request_capacity,
)


def _rows(*, tx_index: int, quote_mint: str, token_program: str) -> tuple[dict, dict]:
    identity = {"slot": 100, "transaction_index": tx_index, "path": [1, 0], "signature": "sig"}
    ident = {
        "identity": identity,
        "transaction_status": "successful_transaction",
        "population_category": "successful_structural_pass",
        "economic_admission": False,
    }
    roles = {
        "associated_token_program": "assoc-program",
        "base_mint": "base-mint",
        "base_token_program": token_program,
        "coin_creator_vault_ata": "creator-ata",
        "coin_creator_vault_authority": "creator-authority",
        "event_authority": "event-authority",
        "fee_config": "fee-config",
        "fee_program": "fee-program",
        "fee_quote_ata_suffix": "fee-suffix",
        "fee_wallet_suffix": "wallet-suffix",
        "global_config": "global-config",
        "global_volume_accumulator": "global-volume",
        "pool": "pool",
        "pool_base_token_account": "pool-base",
        "pool_quote_token_account": "pool-quote",
        "pool_v2_prefix": "pool-prefix",
        "program": "program",
        "protocol_fee_recipient": "fee-recipient",
        "protocol_fee_recipient_token_account": "fee-recipient-token",
        "quote_mint": quote_mint,
        "quote_token_program": "legacy-program",
        "system_program": "system-program",
        "user": "user",
        "user_base_token_account": "user-base",
        "user_quote_token_account": "user-quote",
        "user_volume_accumulator": "user-volume",
    }
    taxonomy = {
        "identity": dict(identity),
        "economic_admission": False,
        "candidate_roles": roles,
    }
    return ident, taxonomy


@given(tx_index=st.integers(min_value=0, max_value=2**31))
def test_whole_prefix_single_request_screen_never_proves_replay(tx_index: int):
    _, state = _whole_prefix_single_request_capacity(tx_index)
    assert (state == "within_limit_capacity_only") is (tx_index <= 4)


def test_under_five_candidate_still_has_no_instruction_position_state():
    ident, taxonomy = _rows(
        tx_index=2,
        quote_mint="So11111111111111111111111111111111111111112",
        token_program="TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    )
    fact, pairs = _row_facts(ident, taxonomy)
    assert fact["same_slot_preceding_transaction_count"] == 2
    assert fact["whole_prefix_single_request_capacity_screen"] == "within_limit_capacity_only"
    assert fact["candidate_instruction_position_state"] == "not_exposed_by_documented_archive_api"
    assert fact["token_2022_role_present"] is True
    assert fact["wsol_quote_role_present"] is True
    assert len(pairs) == 26
    assert fact["economic_admission"] is False


def test_late_slot_candidate_exceeds_simulation_sequence_cap():
    ident, taxonomy = _rows(
        tx_index=5,
        quote_mint="legacy-mint",
        token_program="legacy-program",
    )
    fact, _ = _row_facts(ident, taxonomy)
    assert fact["candidate_plus_prefix_transaction_count"] == 6
    assert fact["whole_prefix_single_request_capacity_screen"] == "exceeds_limit"
    assert fact["candidate_instruction_position_state"] == "not_exposed_by_documented_archive_api"


def test_identity_mismatch_fails_closed():
    ident, taxonomy = _rows(
        tx_index=0,
        quote_mint="legacy-mint",
        token_program="legacy-program",
    )
    taxonomy["identity"]["transaction_index"] = 1
    with pytest.raises(FeasibilityError, match="taxonomy_identity_mismatch"):
        _row_facts(ident, taxonomy)


def test_economic_admission_must_remain_false():
    ident, taxonomy = _rows(
        tx_index=0,
        quote_mint="legacy-mint",
        token_program="legacy-program",
    )
    ident["economic_admission"] = True
    with pytest.raises(FeasibilityError, match="economic_admission_not_false"):
        _row_facts(ident, taxonomy)


def test_negative_transaction_index_fails_closed():
    with pytest.raises(FeasibilityError, match="transaction_index_invalid"):
        _whole_prefix_single_request_capacity(-1)
