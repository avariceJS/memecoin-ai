from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from brain_senses_sell23_c3_native_account_flow import (  # noqa: E402
    ASSOCIATED_TOKEN,
    SYSTEM,
    TOKEN,
    _close_sweep_candidate,
    _token_effect,
    _touch_effect,
)


def ix(program_id: str, data: bytes, accounts: list[str], path: list[int]) -> dict:
    return {
        "program_id": program_id,
        "data_hex": data.hex(),
        "accounts": accounts,
        "path": path,
    }


def test_lamport_conservation_identity_across_large_integer_domain() -> None:
    rng = random.Random(20261010)
    for _ in range(2_000):
        pre = rng.randrange(0, 2**64)
        create = rng.randrange(0, 2**64)
        credited = rng.randrange(0, 2**64)
        debited = rng.randrange(0, pre + create + credited + 1)
        post = rng.randrange(0, pre + create + credited - debited + 1)
        close = _close_sweep_candidate(pre, create, credited, debited, post)
        assert close is not None
        assert pre + create + credited - debited == post + close


def test_conservation_is_exact_above_float_integer_precision() -> None:
    large = 2**60 + 17
    assert _close_sweep_candidate(0, large, 23, 11, 7) == large + 5


def test_missing_endpoints_are_not_zero() -> None:
    assert _close_sweep_candidate(None, 100, 20, 0, 0) is None
    assert _close_sweep_candidate(0, 100, 20, 0, None) is None


@pytest.mark.parametrize("values", [(-1, 0, 0, 0, 0), (0, 0, 0, -1, 0)])
def test_negative_flow_quantities_fail_closed(values: tuple[int, int, int, int, int]) -> None:
    with pytest.raises(ValueError, match="invalid_lamport_flow_quantity"):
        _close_sweep_candidate(*values)


def test_initialize_immutable_owner_is_an_explicit_legacy_token_touch() -> None:
    effect = _token_effect(ix(TOKEN, b"\x16", ["temporary"], [2, 2]))
    assert effect == {
        "kind": "initialize_immutable_owner",
        "path": [2, 2],
        "account": "temporary",
    }


def test_unknown_token_touch_is_not_dropped_from_touch_accounting() -> None:
    effect = _touch_effect(ix(TOKEN, b"\x7f", ["temporary"], [2, 2]), "temporary", [7])
    assert effect["kind"] == "unsupported_touch"


def test_associated_token_create_requires_expected_account_position_and_shape() -> None:
    good = _touch_effect(
        ix(
            ASSOCIATED_TOKEN,
            b"\x01",
            ["payer", "temporary", "owner", "mint", "system", "token"],
            [2],
        ),
        "temporary",
        [7],
    )
    assert good["kind"] == "associated_token_create_idempotent"
    wrong_target = _touch_effect(
        ix(
            ASSOCIATED_TOKEN,
            b"\x01",
            ["payer", "other", "temporary", "mint", "system", "token"],
            [2],
        ),
        "temporary",
        [7],
    )
    assert wrong_target["kind"] == "unsupported_touch"


def test_system_transfer_and_token_transfer_have_distinct_units() -> None:
    lamports = _touch_effect(
        ix(
            SYSTEM,
            (2).to_bytes(4, "little") + (12_345).to_bytes(8, "little"),
            ["payer", "temporary"],
            [5],
        ),
        "temporary",
        [7],
    )
    token = _token_effect(
        ix(
            TOKEN,
            b"\x0c" + (12_345).to_bytes(8, "little") + b"\x09",
            ["temporary", "mint", "vault", "authority"],
            [7, 1],
        )
    )
    assert lamports["kind"] == "system_transfer"
    assert lamports["lamports"] == 12_345
    assert token["kind"] == "transfer_checked"
    assert token["amount_atomic"] == 12_345
    assert "lamports" not in token
