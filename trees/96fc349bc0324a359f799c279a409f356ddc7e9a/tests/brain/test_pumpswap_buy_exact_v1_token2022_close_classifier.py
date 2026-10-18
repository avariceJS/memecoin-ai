from __future__ import annotations

from copy import deepcopy

import pytest

from memecoin_pumpswap.decoder import TOKEN2022
from tools.brain_senses_buy_exact_v1_token2022_close_classifier import (
    SOURCE_URLS,
    classify_reference,
)

USER = "User111111111111111111111111111111111111111"
QUOTE_ACCOUNT = "Quote1111111111111111111111111111111111111"


def fixture_row_and_transaction():
    row = {
        "signature": "signature-fixture",
        "slot": 100,
        "transaction_index": 3,
        "instruction_path": [5],
        "target_accounts": {
            "user": USER,
            "user_quote_token_account": QUOTE_ACCOUNT,
            "quote_mint": "Mint11111111111111111111111111111111111111",
        },
        "target_instruction_trace": [
            {
                "program_id": TOKEN2022,
                "path": [5],
                "stack_height": 1,
                "wire_hex": "09",
                "wire_sha256": "2b4c342f5433ebe591a1da77e013d1b72475562d48578dca8b84bac6651c3cb9",
            }
        ],
    }
    instructions = [
        {"programIdIndex": 4, "accounts": [], "data": "", "stackHeight": 1} for _ in range(5)
    ]
    instructions.append({"programIdIndex": 4, "accounts": [3, 0, 0], "data": "A", "stackHeight": 1})
    transaction = {
        "transaction": {
            "signatures": [row["signature"]],
            "message": {
                "accountKeys": [
                    USER,
                    "Other1111111111111111111111111111111111111",
                    "Other2222222222222222222222222222222222222",
                    QUOTE_ACCOUNT,
                    TOKEN2022,
                ],
                "header": {
                    "numRequiredSignatures": 1,
                    "numReadonlySignedAccounts": 0,
                    "numReadonlyUnsignedAccounts": 1,
                },
                "addressTableLookups": [],
                "instructions": instructions,
            },
        },
        "meta": {"err": None, "loadedAddresses": {"writable": [], "readonly": []}},
    }
    return row, transaction


def test_recognizes_only_the_source_bound_single_owner_close_structure():
    row, transaction = fixture_row_and_transaction()

    result = classify_reference(row, transaction, source_valid=True)

    assert result["classification"] == "known_structural_unsupported_close"
    assert result["instruction_name"] == "CloseAccount"
    assert result["transaction_succeeded"] is True
    assert [item["pubkey"] for item in result["resolved_ordered_accounts"]] == [
        QUOTE_ACCOUNT,
        USER,
        USER,
    ]
    assert result["resolved_ordered_accounts"][0]["writable_potential"] is True
    assert result["resolved_ordered_accounts"][1]["writable_potential"] is True
    assert result["resolved_ordered_accounts"][2]["signer_potential"] is True
    assert [item["expected_role"] for item in result["resolved_ordered_accounts"]] == [
        "close_target_token_account",
        "destination_account",
        "single_owner_signer",
    ]
    assert result["historical_extension_state"] == "unavailable"
    assert result["economic_effects"] == "not_assessed"
    assert result["rent_or_lamport_settlement"] == "not_assessed"


def test_instruction_source_url_is_the_pinned_interface_path():
    assert SOURCE_URLS["instruction.rs"].endswith("/interface/src/instruction.rs")


@pytest.mark.parametrize(
    ("mutation", "expected_failure"),
    [
        ("source_unpinned", "pinned_official_source_hashes"),
        ("wrong_program", "exact_token2022_program"),
        ("wrong_data", "exact_close_account_wire"),
        ("wrong_order", "resolved_close_account_order_and_privileges"),
        ("owner_not_signer", "resolved_close_account_order_and_privileges"),
        ("target_readonly", "resolved_close_account_order_and_privileges"),
        ("transaction_failed", "transaction_succeeded"),
        ("path_mismatch", "outer_instruction_path_and_stack"),
    ],
)
def test_any_failed_structural_gate_stays_unknown(mutation: str, expected_failure: str):
    row, transaction = fixture_row_and_transaction()
    row = deepcopy(row)
    transaction = deepcopy(transaction)
    source_valid = True
    instruction = transaction["transaction"]["message"]["instructions"][5]
    if mutation == "source_unpinned":
        source_valid = False
    elif mutation == "wrong_program":
        instruction["programIdIndex"] = 2
    elif mutation == "wrong_data":
        instruction["data"] = "B"
    elif mutation == "wrong_order":
        instruction["accounts"] = [0, 3, 0]
    elif mutation == "owner_not_signer":
        transaction["transaction"]["message"]["header"]["numRequiredSignatures"] = 0
    elif mutation == "target_readonly":
        transaction["transaction"]["message"]["header"]["numReadonlyUnsignedAccounts"] = 2
    elif mutation == "transaction_failed":
        transaction["meta"]["err"] = {"InstructionError": [5, "Custom"]}
    elif mutation == "path_mismatch":
        row["target_instruction_trace"][0]["path"] = [99]

    result = classify_reference(row, transaction, source_valid=source_valid)

    assert result["classification"] == "unknown"
    assert expected_failure in result["checks"]
    assert result["checks"][expected_failure] is False
    assert result["instruction_name"] is None
