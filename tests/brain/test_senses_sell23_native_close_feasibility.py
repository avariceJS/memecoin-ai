from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from brain_senses_sell23_c3_native_close_feasibility import (  # noqa: E402
    SYSTEM_PROGRAM,
    TOKEN,
    _system_effect,
    _token_effect,
)


def instruction(program: str, data: bytes, accounts: list[str]) -> dict:
    return {"program_id": program, "data_hex": data.hex(), "accounts": accounts, "path": [0]}


def test_system_transfer_retains_lamport_dimension_and_integer_value() -> None:
    lamports = 2**53 + 19
    data = (2).to_bytes(4, "little") + lamports.to_bytes(8, "little")
    effect = _system_effect(instruction(SYSTEM_PROGRAM, data, ["source", "destination"]))
    assert effect == {
        "kind": "transfer",
        "path": [0],
        "source": "source",
        "destination": "destination",
        "lamports": lamports,
    }
    assert "amount_atomic" not in effect


def test_legacy_transfer_checked_retains_token_atomic_dimension() -> None:
    amount = 2**53 + 23
    data = b"\x0c" + amount.to_bytes(8, "little") + b"\x09"
    effect = _token_effect(instruction(TOKEN, data, ["source", "mint", "destination", "authority"]))
    assert effect == {
        "kind": "transfer_checked",
        "path": [0],
        "source": "source",
        "destination": "destination",
        "mint": "mint",
        "amount_atomic": amount,
        "decimals": 9,
    }
    assert "lamports" not in effect


def test_system_transfer_parser_accepts_only_exact_wire_length() -> None:
    for length in range(0, 20):
        data = (2).to_bytes(4, "little") + bytes(length)
        effect = _system_effect(instruction(SYSTEM_PROGRAM, data, ["source", "destination"]))
        assert (effect is not None and effect["kind"] == "transfer") is (length == 8)
    transfer_with_seed = (11).to_bytes(4, "little") + bytes(80)
    effect = _system_effect(
        instruction(SYSTEM_PROGRAM, transfer_with_seed, ["source", "destination", "base"])
    )
    assert effect == {"kind": "unsupported_system", "path": [0], "tag": 11}


def test_sync_native_and_close_account_require_exact_legacy_wire_shapes() -> None:
    accounts = ["native_account", "destination", "authority"]
    assert _token_effect(instruction(TOKEN, b"\x11", accounts))["kind"] == "sync_native"
    assert _token_effect(instruction(TOKEN, b"\x09", accounts))["kind"] == "close_account"
    assert (
        _token_effect(instruction(TOKEN, b"\x11\x00", accounts))["kind"]
        == "unsupported_legacy_token"
    )
    assert (
        _token_effect(instruction(TOKEN, b"\x09\x00", accounts))["kind"]
        == "unsupported_legacy_token"
    )
