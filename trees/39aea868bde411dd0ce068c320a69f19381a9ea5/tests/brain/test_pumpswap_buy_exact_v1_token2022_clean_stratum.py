from __future__ import annotations

import pytest
from test_pumpswap_normalize import compiled_transaction, public_key

from memecoin_pumpswap.decoder import TOKEN2022, encode58
from memecoin_pumpswap.normalize import normalize_compiled_transaction
from tools.brain_senses_buy_exact_v1_token2022_clean_stratum import (
    _raw_privileges,
    _trace_candidate,
)


def _header_tx():
    tx, keys = compiled_transaction()
    tx["transaction"]["message"]["header"] = {
        "numRequiredSignatures": 1,
        "numReadonlySignedAccounts": 0,
        "numReadonlyUnsignedAccounts": 2,
    }
    return tx, keys


def test_static_privilege_partition_and_conflict_detection():
    tx, keys = _header_tx()
    flags, conflicts = _raw_privileges(tx)
    assert flags[keys[0]] is True
    assert flags[keys[1]] is False
    assert flags[keys[2]] is False
    assert conflicts == set()

    tx["transaction"]["message"]["addressTableLookups"] = [
        {"accountKey": public_key(20), "writableIndexes": [1], "readonlyIndexes": []}
    ]
    tx["meta"]["loadedAddresses"] = {"writable": [keys[2]], "readonly": []}
    flags, conflicts = _raw_privileges(tx)
    assert keys[2] in conflicts
    assert keys[2] not in flags


@pytest.mark.parametrize("version", [0, 1])
def test_loaded_addresses_keep_writable_then_readonly_classes(version):
    tx, _ = _header_tx()
    writable, readonly = public_key(21), public_key(22)
    tx["version"] = version
    tx["transaction"]["message"]["addressTableLookups"] = [
        {"accountKey": public_key(20), "writableIndexes": [3], "readonlyIndexes": [7]}
    ]
    tx["meta"]["loadedAddresses"] = {"writable": [writable], "readonly": [readonly]}

    flags, conflicts = _raw_privileges(tx)

    assert conflicts == set()
    assert flags[writable] is True
    assert flags[readonly] is False


def test_candidate_trace_preserves_exact_wire_path_and_no_economic_semantics():
    tx, keys = _header_tx()
    row = {
        "signature": "signature",
        "slot": 44,
        "transaction_index": 0,
        "instruction_path": [0],
        "target_accounts": {"quote_mint": keys[2]},
    }

    result = _trace_candidate(tx, row)

    trace = result["target_instruction_trace"]
    assert trace[0]["path"] == [0]
    assert trace[0]["stack_height"] == 1
    assert (
        trace[0]["wire_hex"]
        == normalize_compiled_transaction(tx)["outer_instructions"][0]["data_hex"]
    )
    assert result["historical_extension_state"] == "unavailable"
    assert result["economic_effects"] == "not_assessed"


def test_unrecognized_token2022_wire_shape_is_explicitly_quarantined():
    tx, keys = _header_tx()
    tx["transaction"]["message"]["accountKeys"][1] = TOKEN2022
    tx["transaction"]["message"]["instructions"][0].update(
        programIdIndex=1, accounts=[2], data=encode58(b"\x09")
    )
    row = {
        "signature": "signature",
        "slot": 44,
        "transaction_index": 0,
        "instruction_path": [0],
        "target_accounts": {"quote_mint": keys[2]},
    }

    result = _trace_candidate(tx, row)

    assert result["token2022_wire_state"] == "unclassified_wire_shape_present"
    assert result["token2022_wire_shape_counts"] == [{"first_byte": 9, "length": 1, "count": 1}]


def test_missing_inner_instruction_stack_evidence_fails_closed():
    tx, keys = _header_tx()
    tx["meta"]["innerInstructions"][0]["instructions"][0].pop("stackHeight")
    with pytest.raises(ValueError, match="inner_stack_height_unavailable"):
        _trace_candidate(
            tx,
            {
                "signature": "signature",
                "slot": 44,
                "transaction_index": 0,
                "instruction_path": [0],
                "target_accounts": {"quote_mint": keys[2]},
            },
        )
