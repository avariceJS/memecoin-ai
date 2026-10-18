from __future__ import annotations

import random

import pytest

from memecoin_pumpswap.decoder import TOKEN2022, encode58
from tools.brain_senses_buy_exact_v1_token2022_transfer_checked import (
    IDL_ACCOUNT_NAMES,
    _classify_instruction,
    pack_transfer_checked_wire,
    parse_transfer_checked_wire,
)

PUMP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
DISCRIMINATOR = bytes.fromhex("c62e1552b4d9e870")


def fixture_row_transaction_and_idl():
    account_keys = [f"account_{index}" for index in range(len(IDL_ACCOUNT_NAMES))]
    account_keys.extend([TOKEN2022, PUMP_PROGRAM])
    row = {
        "signature": "signature-fixture",
        "slot": 123,
        "transaction_index": 4,
        "instruction_path": [2],
        "target_accounts": {
            name: account_keys[index]
            for index, name in enumerate(IDL_ACCOUNT_NAMES)
            if name
            in {
                "pool",
                "user",
                "quote_mint",
                "user_quote_token_account",
                "pool_quote_token_account",
            }
        },
    }
    wire = pack_transfer_checked_wire(19955087022736, 6)
    transaction = {
        "transaction": {
            "signatures": [row["signature"]],
            "message": {
                "accountKeys": account_keys,
                "header": {
                    "numRequiredSignatures": 1,
                    "numReadonlySignedAccounts": 0,
                    "numReadonlyUnsignedAccounts": 0,
                },
                "addressTableLookups": [],
                "instructions": [
                    {
                        "programIdIndex": len(account_keys) - 1,
                        "accounts": list(range(23)),
                        "data": encode58(DISCRIMINATOR),
                        "stackHeight": 1,
                    }
                    if index == 2
                    else {
                        "programIdIndex": len(account_keys) - 1,
                        "accounts": [],
                        "data": "",
                        "stackHeight": 1,
                    }
                    for index in range(3)
                ],
            },
        },
        "meta": {
            "err": None,
            "loadedAddresses": {"writable": [], "readonly": []},
            "innerInstructions": [
                {
                    "index": 2,
                    "instructions": [
                        {
                            "programIdIndex": 23,
                            "accounts": [6, 4, 8, 1],
                            "data": encode58(wire),
                            "stackHeight": 2,
                        }
                    ],
                }
            ],
        },
    }
    trace = {
        "program_id": TOKEN2022,
        "path": [2, 0],
        "stack_height": 2,
        "wire_hex": wire.hex(),
        "wire_sha256": __import__("hashlib").sha256(wire).hexdigest(),
    }
    idl = {"discriminator_bytes": list(DISCRIMINATOR)}
    return row, trace, transaction, idl


def test_transfer_checked_codec_golden_vector_and_distinct_dimensions():
    wire = bytes.fromhex("0c909adf272612000006")

    parsed = parse_transfer_checked_wire(wire)

    assert parsed == {"declared_amount_atomic": 19955087022736, "decimals_argument": 6}
    assert pack_transfer_checked_wire(**parsed) == wire
    assert set(parsed) == {"declared_amount_atomic", "decimals_argument"}


def test_transfer_checked_codec_boundaries_and_deterministic_generated_round_trips():
    vectors = [(0, 0), (0, 255), (2**64 - 1, 0), (2**64 - 1, 255)]
    rng = random.Random(0xC10_12_10)
    vectors.extend((rng.randrange(2**64), rng.randrange(256)) for _ in range(512))

    for amount, decimals in vectors:
        wire = pack_transfer_checked_wire(amount, decimals)
        assert parse_transfer_checked_wire(wire) == {
            "declared_amount_atomic": amount,
            "decimals_argument": decimals,
        }


@pytest.mark.parametrize(
    "wire",
    [b"", b"\x0b" + b"\x00" * 9, b"\x0c" + b"\x00" * 8, b"\x0c" + b"\x00" * 10],
)
def test_transfer_checked_parser_rejects_wrong_tag_or_length(wire: bytes):
    with pytest.raises(ValueError, match="wire_not_exact_transfer_checked"):
        parse_transfer_checked_wire(wire)


def test_transfer_checked_codec_rejects_mixed_or_out_of_range_values():
    for amount, decimals in ((True, 6), (-1, 6), (2**64, 6), (1, True), (1, -1), (1, 256)):
        with pytest.raises(ValueError):
            pack_transfer_checked_wire(amount, decimals)


def test_classifies_raw_transfer_checked_and_maps_only_idl_supported_roles():
    row, trace, transaction, idl = fixture_row_transaction_and_idl()

    result = _classify_instruction(
        row,
        trace,
        transaction,
        idl_instruction=idl,
        capture_binding={"receipt_ns": 123456789},
    )

    assert result["classification"] == "transfer_checked_structural_pass"
    assert result["declared_amount_atomic"] == 19955087022736
    assert result["decimals_argument"] == 6
    assert result["amount_semantics"] == "declared_instruction_argument_not_realized_effect"
    assert result["role_mapping_status"] == "pinned_pump_idl_supported"
    assert [item["target_roles"] for item in result["transfer_accounts"]] == [
        ["user_quote_token_account"],
        ["quote_mint"],
        ["pool_quote_token_account"],
        ["user"],
    ]


def test_failed_transaction_or_raw_trace_disagreement_stays_unknown():
    row, trace, transaction, idl = fixture_row_transaction_and_idl()
    transaction["meta"]["err"] = {"InstructionError": [2, "Custom"]}

    failed = _classify_instruction(row, trace, transaction, idl_instruction=idl, capture_binding={})

    assert failed["classification"] == "unknown"
    assert failed["checks"]["transaction_succeeded"] is False
    assert failed["declared_amount_atomic"] == 19955087022736

    row, trace, transaction, idl = fixture_row_transaction_and_idl()
    trace["path"] = [2, 1]
    mismatch = _classify_instruction(
        row, trace, transaction, idl_instruction=idl, capture_binding={}
    )
    assert mismatch["classification"] == "unknown"
    assert mismatch["checks"]["instruction_path_resolves"] is False


def test_trace_uses_its_parent_path_not_the_row_candidate_path():
    row, trace, transaction, idl = fixture_row_transaction_and_idl()
    row["instruction_path"] = [0]

    result = _classify_instruction(row, trace, transaction, idl_instruction=idl, capture_binding={})

    assert result["classification"] == "transfer_checked_structural_pass"
    assert result["trace_parent_matches_clean_row_candidate"] is False
    assert result["role_mapping_status"] == "unknown"
    assert all(not account["target_roles"] for account in result["transfer_accounts"])


def test_role_mapping_remains_unknown_without_idl_instruction_match():
    row, trace, transaction, idl = fixture_row_transaction_and_idl()
    outer = transaction["transaction"]["message"]["instructions"][2]
    outer["data"] = encode58(b"other-instruction")

    result = _classify_instruction(row, trace, transaction, idl_instruction=idl, capture_binding={})

    assert result["classification"] == "transfer_checked_structural_pass"
    assert result["role_mapping_status"] == "unknown"
    assert all(not account["target_roles"] for account in result["transfer_accounts"])


def test_row_role_address_mismatch_disables_all_role_attribution():
    row, trace, transaction, idl = fixture_row_transaction_and_idl()
    row["target_accounts"]["quote_mint"] = "another-account"

    result = _classify_instruction(row, trace, transaction, idl_instruction=idl, capture_binding={})

    assert result["classification"] == "transfer_checked_structural_pass"
    assert result["role_mapping_status"] == "unknown"
    assert all(not account["target_roles"] for account in result["transfer_accounts"])


def test_accounts_after_first_four_remain_uninterpreted_metas():
    row, trace, transaction, idl = fixture_row_transaction_and_idl()
    message = transaction["transaction"]["message"]
    message["accountKeys"].append("ExtensionMeta111111111111111111111111111111")
    inner = transaction["meta"]["innerInstructions"][0]["instructions"][0]
    inner["accounts"].append(25)

    result = _classify_instruction(row, trace, transaction, idl_instruction=idl, capture_binding={})

    assert result["classification"] == "transfer_checked_structural_pass"
    assert result["transfer_accounts"][-1]["operand"] == "additional_account_meta"
    assert result["transfer_accounts"][-1]["target_roles"] == []


@pytest.mark.parametrize("mutation", ["wrong_program", "wrong_wire", "too_few_accounts"])
def test_raw_transfer_checked_gate_failures_stay_unknown(mutation: str):
    row, trace, transaction, idl = fixture_row_transaction_and_idl()
    raw_ix = transaction["meta"]["innerInstructions"][0]["instructions"][0]
    if mutation == "wrong_program":
        raw_ix["programIdIndex"] = 24
    elif mutation == "wrong_wire":
        raw_ix["data"] = encode58(b"\x0c" + b"\x01" * 8 + b"\x06")
    else:
        raw_ix["accounts"] = [6, 4, 8]

    result = _classify_instruction(row, trace, transaction, idl_instruction=idl, capture_binding={})

    assert result["classification"] == "unknown"
    assert any(not passed for passed in result["checks"].values())
