from __future__ import annotations

import random

from memecoin_pumpswap.decoder import TOKEN, TOKEN2022
from tools.brain_senses_buy_exact_v1_c4_success_trace import (
    _amount_operand,
    _transaction_endpoint_token_delta,
)


def test_transfer_checked_operand_preserves_atomic_units_and_decimal_metadata() -> None:
    rng = random.Random(20261010)
    amounts = [0, 1, (1 << 64) - 1, *(rng.randrange(1 << 64) for _ in range(128))]
    for amount in amounts:
        for decimals in (0, 6, 9, 255):
            wire = bytes([12]) + amount.to_bytes(8, "little") + bytes([decimals])
            decoded = _amount_operand(
                TOKEN2022, wire, ["source", "mint", "destination", "authority"]
            )
            assert decoded == {
                "wire_variant": "transfer_checked",
                "declared_amount_atomic": amount,
                "unit": "atomic_units_of_wire_mint",
                "mint_pubkey_from_wire_account": "mint",
                "declared_decimals": decimals,
                "decimal_scale_is_not_applied": True,
                "interpretation": "wire_operand_only_not_realized_transfer_or_settlement",
            }


def test_transfer_operand_is_atomic_but_wire_does_not_identify_mint() -> None:
    for amount in (0, 1, 1 << 63, (1 << 64) - 1):
        wire = bytes([3]) + amount.to_bytes(8, "little")
        decoded = _amount_operand(TOKEN, wire, ["source", "destination", "authority"])
        assert decoded is not None
        assert decoded["declared_amount_atomic"] == amount
        assert decoded["unit"] == "atomic_units_of_source_mint_unidentified_by_wire"
        assert decoded["mint_pubkey_from_wire_account"] is None
        assert decoded["declared_decimals"] is None


def test_unknown_transfer_wire_lengths_and_programs_remain_unclassified() -> None:
    for program in (TOKEN, TOKEN2022):
        assert _amount_operand(program, bytes([3]) + b"\x00" * 7, []) is None
        assert _amount_operand(program, bytes([12]) + b"\x00" * 8, []) is None
        assert _amount_operand(program, bytes([9]), []) is None
    assert _amount_operand("unknown-program", bytes([3]) + b"\x00" * 8, []) is None


def test_endpoint_delta_requires_matching_mint_program_and_decimal_scale() -> None:
    rng = random.Random(455252566)
    identity = {
        "mint": "mint",
        "token_program_id": TOKEN2022,
        "decimals_metadata": 6,
    }
    for _ in range(128):
        before_amount = rng.randrange(1 << 64)
        after_amount = rng.randrange(1 << 64)
        before = {**identity, "amount_atomic": before_amount}
        after = {**identity, "amount_atomic": after_amount}
        delta, state = _transaction_endpoint_token_delta(before, after)
        assert delta == after_amount - before_amount
        assert state == "exact_transaction_boundary_delta_unattributed"
    zero_delta, zero_state = _transaction_endpoint_token_delta(
        {**identity, "amount_atomic": 0}, {**identity, "amount_atomic": 0}
    )
    assert zero_delta == 0
    assert zero_state == "exact_transaction_boundary_delta_unattributed"
    for field, different in (
        ("mint", "other-mint"),
        ("token_program_id", TOKEN),
        ("decimals_metadata", 9),
    ):
        after = {**identity, "amount_atomic": 1, field: different}
        assert _transaction_endpoint_token_delta({**identity, "amount_atomic": 0}, after) == (
            None,
            "censored_endpoint_identity_changed",
        )
    assert _transaction_endpoint_token_delta(None, {**identity, "amount_atomic": 1}) == (
        None,
        "unknown_missing_endpoint",
    )
    invalid_scale = {**identity, "amount_atomic": 1, "decimals_metadata": 256}
    assert _transaction_endpoint_token_delta(invalid_scale, invalid_scale) == (
        None,
        "unknown_endpoint_identity_invalid",
    )
