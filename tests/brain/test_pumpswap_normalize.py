import copy

import pytest

from memecoin_pumpswap.decoder import TOKEN, TOKEN2022, decode58, encode58
from memecoin_pumpswap.normalize import (
    NormalizationError,
    decode_token_transfer_checked,
    normalize_compiled_transaction,
)


def public_key(byte: int) -> str:
    return encode58(bytes([byte]) * 32)


def test_base58_represents_empty_instruction_data_exactly():
    assert decode58("") == b""
    assert encode58(b"") == ""


def compiled_transaction(version="legacy"):
    static = [public_key(n) for n in range(1, 4)]
    tx = {
        "version": version,
        "transaction": {
            "signatures": ["signature"],
            "message": {
                "accountKeys": static,
                "instructions": [
                    {"programIdIndex": 1, "accounts": [0, 2], "data": encode58(b"outer")}
                ],
            },
        },
        "meta": {
            "err": None,
            "innerInstructions": [
                {
                    "index": 0,
                    "instructions": [
                        {
                            "programIdIndex": 2,
                            "accounts": [0],
                            "data": encode58(b"inner"),
                            "stackHeight": 2,
                        }
                    ],
                }
            ],
            "preTokenBalances": [],
            "postTokenBalances": [],
        },
    }
    return tx, static


def test_resolves_static_accounts_and_preserves_raw_instruction_fields():
    tx, static = compiled_transaction()

    result = normalize_compiled_transaction(tx)

    assert [account["pubkey"] for account in result["accounts"]] == static
    outer = result["outer_instructions"][0]
    assert outer["program_id"] == static[1]
    assert outer["accounts"] == [static[0], static[2]]
    assert outer["account_provenance"] == [
        {"index": 0, "pubkey": static[0], "source": "static"},
        {"index": 2, "pubkey": static[2], "source": "static"},
    ]
    assert outer["raw_instruction"] == tx["transaction"]["message"]["instructions"][0]
    assert result["inner_instructions"][0]["path"] == [0, 0]
    assert result["inner_instructions"][0]["data_hex"] == b"inner".hex()


@pytest.mark.parametrize("version", [0, 1])
def test_resolves_loaded_writable_then_readonly_accounts(version):
    tx, static = compiled_transaction(version)
    table = public_key(20)
    writable, readonly = public_key(21), public_key(22)
    tx["transaction"]["message"]["addressTableLookups"] = [
        {"accountKey": table, "writableIndexes": [3], "readonlyIndexes": [7]}
    ]
    tx["meta"]["loadedAddresses"] = {"writable": [writable], "readonly": [readonly]}
    tx["transaction"]["message"]["instructions"][0]["accounts"] = [3, 4]

    result = normalize_compiled_transaction(tx)

    assert [account["pubkey"] for account in result["accounts"]] == [*static, writable, readonly]
    assert [account["source"] for account in result["accounts"][-2:]] == [
        "loaded_writable",
        "loaded_readonly",
    ]
    assert result["outer_instructions"][0]["accounts"] == [writable, readonly]
    assert result["accounts"][-1]["lookup_table"] == table


@pytest.mark.parametrize(
    ("mutate", "category"),
    [
        (lambda tx: tx["meta"].pop("innerInstructions"), "inner_instruction_trace_unavailable"),
        (
            lambda tx: tx["meta"].update(innerInstructions=None),
            "inner_instruction_trace_unavailable",
        ),
        (
            lambda tx: tx["transaction"]["message"]["instructions"][0].update(programIdIndex=9),
            "program_id_index_out_of_range",
        ),
        (
            lambda tx: tx["transaction"]["message"]["instructions"][0].update(accounts=[3]),
            "account_index_out_of_range",
        ),
        (lambda tx: tx.update(version=2), "transaction_version_unsupported_or_missing"),
        (lambda tx: tx.update(version=True), "transaction_version_unsupported_or_missing"),
        (
            lambda tx: tx["meta"]["innerInstructions"][0]["instructions"][0].pop("stackHeight"),
            "inner_stack_height_unavailable",
        ),
    ],
)
def test_invalid_shapes_fail_closed(mutate, category):
    tx, _ = compiled_transaction()
    mutate(tx)

    with pytest.raises(NormalizationError, match=category):
        normalize_compiled_transaction(tx)


def test_lookup_accounts_require_exact_loaded_address_cardinality():
    tx, _ = compiled_transaction(0)
    tx["transaction"]["message"]["addressTableLookups"] = [
        {"accountKey": public_key(20), "writableIndexes": [0], "readonlyIndexes": []}
    ]
    tx["meta"]["loadedAddresses"] = {"writable": [], "readonly": []}

    with pytest.raises(NormalizationError, match="loaded_address_count_mismatch"):
        normalize_compiled_transaction(tx)

    tx["meta"].pop("loadedAddresses")
    with pytest.raises(NormalizationError, match="loaded_addresses_missing"):
        normalize_compiled_transaction(tx)


def test_rejects_duplicate_or_out_of_range_inner_group_indexes():
    tx, _ = compiled_transaction()
    tx["meta"]["innerInstructions"].append(copy.deepcopy(tx["meta"]["innerInstructions"][0]))

    with pytest.raises(NormalizationError, match="inner_group_index_invalid"):
        normalize_compiled_transaction(tx)


@pytest.mark.parametrize(
    ("program", "expected_status"),
    [
        (TOKEN, "exact_instruction_amount"),
        (TOKEN2022, "token_2022_fee_and_hook_effects_unresolved"),
    ],
)
def test_decodes_transfer_checked_wire_bytes_without_promoting_token_2022_effects(
    program, expected_status
):
    amount = 123456789
    accounts = [public_key(n) for n in range(40, 45)]
    normalized_instruction = {
        "program_id": program,
        "accounts": accounts,
        # SPL Token's official TransferChecked pack vector: tag 12, u64 LE, decimals.
        "data_hex": (bytes([12]) + amount.to_bytes(8, "little") + bytes([6])).hex(),
    }

    transfer = decode_token_transfer_checked(normalized_instruction)

    assert transfer == {
        "instruction": "transfer_checked",
        "program_id": program,
        "source": accounts[0],
        "mint": accounts[1],
        "destination": accounts[2],
        "authority": accounts[3],
        "additional_signers": accounts[4:],
        "amount_base_units": amount,
        "decimals": 6,
        "economic_status": expected_status,
    }


@pytest.mark.parametrize(
    ("instruction", "category"),
    [
        (
            {"program_id": TOKEN, "accounts": [], "data_hex": "0c010000000000000001"},
            "transfer_checked_account_shape",
        ),
        (
            {"program_id": TOKEN, "accounts": [public_key(1)] * 4, "data_hex": "0c0100"},
            "transfer_checked_wire_length",
        ),
        (
            {
                "program_id": TOKEN,
                "accounts": [public_key(1)] * 4,
                "data_hex": "030100000000000000",
            },
            "token_instruction_unsupported",
        ),
        (
            {
                "program_id": public_key(2),
                "accounts": [public_key(1)] * 4,
                "data_hex": "0c010000000000000001",
            },
            "not_a_supported_token_program",
        ),
    ],
)
def test_token_wire_decoder_quarantines_unknown_or_malformed_shapes(instruction, category):
    with pytest.raises(NormalizationError, match=category):
        decode_token_transfer_checked(instruction)
