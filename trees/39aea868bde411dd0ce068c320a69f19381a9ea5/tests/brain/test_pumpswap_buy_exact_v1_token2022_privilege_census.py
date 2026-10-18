from __future__ import annotations

import copy

import pytest
from test_pumpswap_normalize import compiled_transaction, public_key

from memecoin_pumpswap.normalize import NormalizationError, normalize_compiled_transaction
from tools.brain_senses_buy_exact_v1_token2022_privilege_census import (
    account_writable_flags,
    classify_transaction,
    raw_key_privilege_in_transaction,
)


def _tx_with_header():
    tx, keys = compiled_transaction()
    tx["transaction"]["message"]["header"] = {
        "numRequiredSignatures": 1,
        "numReadonlySignedAccounts": 0,
        "numReadonlyUnsignedAccounts": 2,
    }
    return tx, keys


@pytest.mark.parametrize("readonly_unsigned", [0, 1, 2])
def test_static_privileges_follow_message_header_partition(readonly_unsigned):
    tx, keys = _tx_with_header()
    tx["transaction"]["message"]["header"]["numReadonlyUnsignedAccounts"] = readonly_unsigned
    normalized = normalize_compiled_transaction(tx)

    flags = account_writable_flags(tx, normalized)

    assert flags[keys[0]] is True  # writable signed prefix
    assert flags[keys[1]] is (readonly_unsigned < 2)
    assert flags[keys[2]] is (readonly_unsigned < 1)


@pytest.mark.parametrize("version", [0, 1])
def test_loaded_privileges_preserve_writable_then_readonly_order(version):
    tx, keys = _tx_with_header()
    tx["version"] = version
    writable, readonly = public_key(21), public_key(22)
    tx["transaction"]["message"]["addressTableLookups"] = [
        {"accountKey": public_key(20), "writableIndexes": [3], "readonlyIndexes": [7]}
    ]
    tx["meta"]["loadedAddresses"] = {"writable": [writable], "readonly": [readonly]}
    tx["transaction"]["message"]["instructions"][0]["accounts"] = [0, 3, 4]
    normalized = normalize_compiled_transaction(tx)

    flags = account_writable_flags(tx, normalized)

    assert flags[writable] is True
    assert flags[readonly] is False
    assert [account["pubkey"] for account in normalized["accounts"][-2:]] == [writable, readonly]


def test_full_slot_scan_detects_static_and_loaded_potential_writes():
    tx, keys = _tx_with_header()
    assert raw_key_privilege_in_transaction(tx, keys[2]) == "readonly"

    tx["transaction"]["message"]["header"]["numReadonlyUnsignedAccounts"] = 0
    assert raw_key_privilege_in_transaction(tx, keys[2]) == "writable"

    loaded = public_key(23)
    tx["transaction"]["message"]["addressTableLookups"] = [
        {"accountKey": public_key(20), "writableIndexes": [5], "readonlyIndexes": []}
    ]
    tx["meta"]["loadedAddresses"] = {"writable": [loaded], "readonly": []}
    assert raw_key_privilege_in_transaction(tx, loaded) == "writable"
    assert raw_key_privilege_in_transaction(tx, public_key(24)) == "absent"

    tx["meta"]["loadedAddresses"]["writable"] = []
    with pytest.raises(ValueError, match="loaded_address_count_mismatch"):
        raw_key_privilege_in_transaction(tx, loaded)


def test_full_slot_scan_marks_duplicate_target_key_as_conflict():
    tx, keys = _tx_with_header()
    tx["transaction"]["message"]["addressTableLookups"] = [
        {"accountKey": public_key(20), "writableIndexes": [5], "readonlyIndexes": []}
    ]
    tx["meta"]["loadedAddresses"] = {"writable": [keys[2]], "readonly": []}

    assert raw_key_privilege_in_transaction(tx, keys[2]) == "account_key_conflict"


def test_quote_mint_write_privilege_is_potential_and_extension_state_stays_unknown():
    tx, keys = _tx_with_header()
    row = {
        "signature": "signature",
        "slot": 1,
        "transaction_index": 0,
        "instruction_path": [0],
        "target_accounts": {"user": keys[0], "quote_mint": keys[2]},
    }

    result = classify_transaction(tx, row)

    assert result["quote_mint_transaction_privilege"] == "mint_readonly_full_transaction"
    assert result["historical_extension_state"] == "unavailable"
    assert result["economic_effects"] == "not_assessed"

    tx_writable = copy.deepcopy(tx)
    tx_writable["transaction"]["message"]["header"]["numReadonlyUnsignedAccounts"] = 0
    writable_result = classify_transaction(tx_writable, row)
    assert writable_result["quote_mint_transaction_privilege"] == "mint_writable_potential"
    assert writable_result["quote_mint_writable_is_only_potential_write"] is True


def test_unknown_program_touch_is_retained_as_explicit_unresolved_evidence():
    tx, keys = _tx_with_header()
    tx["transaction"]["message"]["instructions"][0]["programIdIndex"] = 1
    row = {
        "signature": "signature",
        "slot": 1,
        "transaction_index": 0,
        "instruction_path": [0],
        "target_accounts": {"quote_mint": keys[2]},
    }

    result = classify_transaction(tx, row)

    assert result["unknown_target_affecting_programs"] == [keys[1]]
    assert result["target_affecting_instruction_trace"][0]["stack_height"] == 1


def test_missing_inner_stack_trace_fails_closed_before_classification():
    tx, keys = _tx_with_header()
    del tx["meta"]["innerInstructions"]
    with pytest.raises(NormalizationError, match="inner_instruction_trace_unavailable"):
        classify_transaction(
            tx,
            {
                "signature": "signature",
                "slot": 1,
                "transaction_index": 0,
                "instruction_path": [0],
                "target_accounts": {"quote_mint": keys[2]},
            },
        )
