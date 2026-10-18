from __future__ import annotations

import random

from memecoin_pumpswap.decoder import TOKEN, TOKEN2022
from tools.brain_senses_buy_exact_v1_token2022_endpoint_delta_census import (
    _account_deltas,
    _subtree_attribution,
)


def _balance(amount: int, *, program: str = TOKEN2022, decimals: int = 6) -> dict:
    return {
        "mint": "mint-a",
        "program_id": program,
        "amount_atomic": amount,
        "decimals": decimals,
    }


def test_endpoint_delta_uses_only_same_mint_atomic_integer_endpoints() -> None:
    row = {
        "target_token_accounts": ["quote-account"],
        "transaction_boundary_spendable_balances": {
            "pre_token_balances": {"quote-account": _balance(400)},
            "post_token_balances": {"quote-account": _balance(325)},
        },
    }

    deltas, unknown = _account_deltas(row)

    assert unknown == []
    assert deltas == [
        {
            "account": "quote-account",
            "mint": "mint-a",
            "token_program_id": TOKEN2022,
            "decimals": 6,
            "pre_amount_atomic": 400,
            "post_amount_atomic": 325,
            "transaction_endpoint_delta_atomic": -75,
            "unit": "atomic_units_of_mint",
            "provider_scope": "reported_transaction_endpoint_token_amount",
        }
    ]


def test_missing_endpoint_remains_unknown_and_never_becomes_zero() -> None:
    row = {
        "target_token_accounts": ["quote-account"],
        "transaction_boundary_spendable_balances": {
            "pre_token_balances": {"quote-account": _balance(0)},
            "post_token_balances": {},
        },
    }

    deltas, unknown = _account_deltas(row)

    assert deltas == []
    assert unknown == [
        {
            "account": "quote-account",
            "state": "unknown",
            "reasons": ["post_endpoint_missing"],
        }
    ]


def test_explicit_equal_endpoint_rows_produce_zero_delta_without_activity_claim() -> None:
    row = {
        "target_token_accounts": ["quote-account"],
        "transaction_boundary_spendable_balances": {
            "pre_token_balances": {"quote-account": _balance(0)},
            "post_token_balances": {"quote-account": _balance(0)},
        },
    }

    deltas, unknown = _account_deltas(row)

    assert unknown == []
    assert deltas[0]["transaction_endpoint_delta_atomic"] == 0
    assert deltas[0]["unit"] == "atomic_units_of_mint"
    assert deltas[0]["provider_scope"] == "reported_transaction_endpoint_token_amount"


def test_endpoint_decimal_or_program_mismatch_is_unknown() -> None:
    for post in (_balance(20, decimals=9), _balance(20, program=TOKEN)):
        row = {
            "target_token_accounts": ["quote-account"],
            "transaction_boundary_spendable_balances": {
                "pre_token_balances": {"quote-account": _balance(10)},
                "post_token_balances": {"quote-account": post},
            },
        }

        deltas, unknown = _account_deltas(row)

        assert deltas == []
        assert len(unknown) == 1
        assert unknown[0]["state"] == "unknown"


def test_endpoint_amount_respects_u64_atomic_boundary() -> None:
    max_u64 = (1 << 64) - 1
    valid = {
        "target_token_accounts": ["quote-account"],
        "transaction_boundary_spendable_balances": {
            "pre_token_balances": {"quote-account": _balance(max_u64)},
            "post_token_balances": {"quote-account": _balance(max_u64)},
        },
    }
    invalid = {
        "target_token_accounts": ["quote-account"],
        "transaction_boundary_spendable_balances": {
            "pre_token_balances": {"quote-account": _balance(max_u64)},
            "post_token_balances": {"quote-account": _balance(max_u64 + 1)},
        },
    }

    valid_deltas, valid_unknown = _account_deltas(valid)
    invalid_deltas, invalid_unknown = _account_deltas(invalid)

    assert valid_unknown == []
    assert valid_deltas[0]["transaction_endpoint_delta_atomic"] == 0
    assert invalid_deltas == []
    assert invalid_unknown[0]["reasons"] == ["endpoint_atomic_amount_outside_u64"]


def test_generated_u64_endpoint_pairs_preserve_signed_atomic_delta_identity() -> None:
    generator = random.Random(0xB12A2026)
    for _ in range(256):
        pre_amount = generator.getrandbits(64)
        post_amount = generator.getrandbits(64)
        row = {
            "target_token_accounts": ["quote-account"],
            "transaction_boundary_spendable_balances": {
                "pre_token_balances": {"quote-account": _balance(pre_amount)},
                "post_token_balances": {"quote-account": _balance(post_amount)},
            },
        }

        deltas, unknown = _account_deltas(row)

        assert unknown == []
        assert deltas[0]["transaction_endpoint_delta_atomic"] == post_amount - pre_amount
        assert deltas[0]["unit"] == "atomic_units_of_mint"


def test_sibling_touch_censors_subtree_attribution_even_with_exact_deltas() -> None:
    accounts = [f"token-account-{index}" for index in range(7)]
    roles = dict(
        zip(
            (
                "user_base_token_account",
                "pool_base_token_account",
                "user_quote_token_account",
                "pool_quote_token_account",
                "protocol_fee_recipient_token_account",
                "coin_creator_vault_ata",
                "observed_fee_ata",
            ),
            accounts,
            strict=True,
        )
    )
    complete = [
        {
            "account": account,
            "mint": "mint-a",
            "token_program_id": TOKEN2022,
            "decimals": 6,
            "pre_amount_atomic": 10,
            "post_amount_atomic": 11,
            "transaction_endpoint_delta_atomic": 1,
            "unit": "atomic_units_of_mint",
            "provider_scope": "reported_transaction_endpoint_token_amount",
        }
        for account in accounts
    ]
    row = {
        "outside_subtree_target_account_touches": [{"path": [4]}],
        "target_accounts": roles,
        "target_token_accounts": accounts,
        "subtree_instruction_trace": [
            {
                "program_id": TOKEN2022,
                "wire_first_byte": 12,
                "wire_length": 10,
                "touched_target_accounts": accounts,
            }
        ],
    }

    assert _subtree_attribution(row, complete, []) == (
        "censored_by_outside_subtree_target_account_touch"
    )
