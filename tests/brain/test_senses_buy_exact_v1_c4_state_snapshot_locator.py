from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.brain_senses_buy_exact_v1_c4_state_snapshot_locator as locator  # noqa: E402

MINT = "3SwWxAKdbeYH2H8ENPhFAXbQ9zCCjaMyc2MnFkaXpump"
PAYER = "7eaaz9yZSJwJeTjPidq6LMocEaCU6jEmnMEp7xcCbvq4"
OTHER = "ComputeBudget111111111111111111111111111111"
ALT_TABLE = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"


def _tx(
    *,
    mint_writable: bool = True,
    status: str = "success",
    fee_payer: str = PAYER,
    loaded_mint: bool = False,
    unresolved_payer: bool = False,
    unknown_status: bool = False,
    signature: str = "tx-signature",
) -> dict:
    if unresolved_payer:
        static = []
        loaded_writable = [MINT]
        lookups = [{"accountKey": ALT_TABLE, "writableIndexes": [3], "readonlyIndexes": []}]
    elif loaded_mint:
        static = [fee_payer, OTHER]
        loaded_writable = [MINT]
        lookups = [{"accountKey": ALT_TABLE, "writableIndexes": [3], "readonlyIndexes": []}]
    elif fee_payer == MINT:
        static = [MINT]
        loaded_writable = []
        lookups = []
    else:
        static = [fee_payer, MINT]
        loaded_writable = []
        lookups = []
    num_readonly_unsigned = 0 if mint_writable else 1
    if loaded_mint:
        num_readonly_unsigned = 1
    message = {
        "accountKeys": static,
        "header": {
            "numRequiredSignatures": 1 if static else 0,
            "numReadonlySignedAccounts": 0,
            "numReadonlyUnsignedAccounts": num_readonly_unsigned,
        },
        "addressTableLookups": lookups,
        "instructions": [],
    }
    meta = {
        "loadedAddresses": {"writable": loaded_writable, "readonly": []},
        "innerInstructions": [],
    }
    if not unknown_status:
        meta["err"] = None if status == "success" else {"InstructionError": [0, "Custom"]}
    return {
        "transaction": {"signatures": [signature], "message": message},
        "meta": meta,
    }


def test_loaded_alt_mint_writable_occurrence_is_retained_with_provenance() -> None:
    indexed, statuses = locator._writable_occurrences([_tx(loaded_mint=True)], {MINT})

    assert statuses == {"success": 1}
    assert len(indexed[MINT]) == 1
    occurrence = indexed[MINT][0]
    assert occurrence["transaction_index"] == 0
    assert occurrence["mint_account_provenance"]["source"] == "loaded_writable"
    assert occurrence["mint_account_provenance"]["lookup_index"] == 3
    assert occurrence["fee_payer"] == PAYER


def test_earlier_and_later_successful_writable_tx_block_opposite_snapshots() -> None:
    before = locator._occurrence_side_and_effect(
        locator._writable_occurrences([_tx()], {MINT})[0][MINT][0],
        target_index=1,
        mint=MINT,
    )
    after = locator._occurrence_side_and_effect(
        locator._writable_occurrences([_tx()], {MINT})[0][MINT][0],
        target_index=0,
        mint=MINT,
    )

    assert (before["side"], before["whole_account_blocker"]) == ("earlier", True)
    assert (after["side"], after["whole_account_blocker"]) == ("later", True)


def test_failed_writable_tx_with_distinct_resolved_fee_payer_is_nonblocking() -> None:
    occurrence = locator._writable_occurrences([_tx(status="failed", fee_payer=PAYER)], {MINT})[0][
        MINT
    ][0]

    classified = locator._occurrence_side_and_effect(occurrence, target_index=1, mint=MINT)
    assert classified["transaction_status"] == "failed"
    assert classified["fee_payer"] == PAYER
    assert classified["whole_account_blocker"] is False
    assert classified["side_effect_class"] == "failed_nonpayer_nonblocking_for_mint_state"


def test_fee_payer_status_and_side_truth_table() -> None:
    # Exhaust the small policy domain: only proven failure with a resolved, distinct
    # payer is nonblocking; direction labels must not change that decision.
    statuses = ("success", "failed", "unknown")
    payers = (MINT, PAYER, None)
    sides = ((0, 1, "earlier"), (2, 1, "later"))
    for status, payer, (tx_index, target_index, expected_side) in product(statuses, payers, sides):
        item = {
            "transaction_index": tx_index,
            "signature": "synthetic",
            "transaction_status": status,
            "fee_payer": payer,
            "fee_payer_resolution": "unresolved"
            if payer is None
            else "resolved_static_account_zero",
            "mint_account_provenance": {"source": "loaded_writable"},
            "scan_state": "message_writable_potential",
            "possible_write": True,
        }
        classified = locator._occurrence_side_and_effect(item, target_index=target_index, mint=MINT)
        assert classified["side"] == expected_side
        assert classified["whole_account_blocker"] is not (
            status == "failed" and payer is not None and payer != MINT
        )


def test_failed_writable_tx_blocks_when_mint_is_fee_payer() -> None:
    occurrence = locator._writable_occurrences([_tx(status="failed", fee_payer=MINT)], {MINT})[0][
        MINT
    ][0]

    classified = locator._occurrence_side_and_effect(occurrence, target_index=1, mint=MINT)
    assert classified["fee_payer"] == MINT
    assert classified["whole_account_blocker"] is True


def test_unresolved_fee_payer_blocks_failed_writable_tx() -> None:
    occurrence = locator._writable_occurrences(
        [_tx(status="failed", unresolved_payer=True)], {MINT}
    )[0][MINT][0]

    classified = locator._occurrence_side_and_effect(occurrence, target_index=1, mint=MINT)
    assert classified["transaction_status"] == "failed"
    assert classified["fee_payer"] is None
    assert classified["fee_payer_resolution"] == "unresolved"
    assert classified["whole_account_blocker"] is True


def test_unknown_status_with_writable_mint_blocks_even_with_distinct_payer() -> None:
    occurrence = locator._writable_occurrences([_tx(unknown_status=True, fee_payer=PAYER)], {MINT})[
        0
    ][MINT][0]

    classified = locator._occurrence_side_and_effect(occurrence, target_index=1, mint=MINT)
    assert classified["transaction_status"] == "unknown"
    assert classified["fee_payer"] == PAYER
    assert classified["whole_account_blocker"] is True


def test_malformed_transaction_is_an_explicit_unknown_blocker() -> None:
    occurrences, statuses = locator._writable_occurrences([{"meta": None}], {MINT})

    assert statuses == {"unknown": 1}
    assert len(occurrences[MINT]) == 1
    occurrence = locator._occurrence_side_and_effect(
        occurrences[MINT][0], target_index=1, mint=MINT
    )
    assert occurrence["scan_state"] == "transaction_account_resolution_unknown"
    assert occurrence["whole_account_blocker"] is True


def test_target_requires_success_readonly_mint_and_distinct_fee_payer() -> None:
    tx = _tx(mint_writable=False, signature="target-signature")
    row = {
        "identity": {
            "slot": 1,
            "transaction_index": 0,
            "instruction_path": [3, 2],
            "signature": "target-signature",
        },
        "required_state_accounts": {"mint_roles": {"base_mint": MINT}},
    }
    assert locator._target_message_gate(row, {"transactions": [tx]})["eligible"] is True

    message = tx["transaction"]["message"]
    message["accountKeys"] = [MINT, OTHER]
    message["header"] = {
        "numRequiredSignatures": 1,
        "numReadonlySignedAccounts": 0,
        "numReadonlyUnsignedAccounts": 1,
    }
    gate = locator._target_message_gate(row, {"transactions": [tx]})
    assert gate["eligible"] is False
    assert "target_fee_payer_is_t22_mint" in gate["reasons"]


def test_frozen_receipt_hash_and_scope_are_pinned() -> None:
    raw = locator.INPUT_RECEIPT.read_bytes()
    value = json.loads(raw)
    assert locator._sha256(raw) == locator.INPUT_RECEIPT_SHA256
    assert value["counts"]["rows"] == 140
    assert len(value["source"]["captures"]) == 16
    assert value["source"]["capture_root"] == str(locator.CAPTURE_ROOT)


def test_build_rows_fails_closed_on_wrong_frozen_denominator() -> None:
    with pytest.raises(ValueError, match="frozen_140_row_denominator_mismatch"):
        locator.build_rows({"rows": []}, {})
