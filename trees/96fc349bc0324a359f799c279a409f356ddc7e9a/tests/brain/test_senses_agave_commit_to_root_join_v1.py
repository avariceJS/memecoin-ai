from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.brain_senses_agave_commit_to_root_join_v1 import (  # noqa: E402
    COMPOSITE_PATCHES,
    SOURCE_COMMIT,
    SOURCE_DIFF_SHA256,
    SOURCE_MAP_SHA256,
    JoinError,
    verify,
)


def _fixture() -> dict:
    bank = {
        "bank_instance_id": "bank-A",
        "slot": 100,
        "bank_hash": "hash-A",
        "parent_bank_hash": None,
        "frozen": True,
    }
    attempts = []
    commits = []
    entries = []
    for i, status in enumerate(("executed_success", "executed_failed", "fees_only")):
        txid, sig, message = f"tx-{i}", f"sig-{i}", f"message-{i}"
        attempts.append(
            {
                "attempt_id": f"attempt-{i}",
                "execution_id": f"execution-{i}",
                "bank_instance_id": "bank-A",
                "transaction_id": txid,
                "signature": sig,
                "message_hash": message,
                "attempt_status": status,
                "candidate_trace_id": None if status == "fees_only" else f"trace-{i}",
                "program_write_disposition": {
                    "executed_success": "unqualified",
                    "executed_failed": "rolled_back",
                    "fees_only": "no_executed_trace",
                }[status],
                "fee_nonce_disposition": {
                    "executed_success": "separate_commit_result",
                    "executed_failed": "separate_rollback_effect",
                    "fees_only": "rollback_accounts",
                }[status],
            }
        )
        commits.append(
            {
                "commit_batch_id": "batch-7",
                "batch_index": i,
                "attempt_id": f"attempt-{i}",
                "transaction_id": txid,
                "signature": sig,
                "message_hash": message,
                "commit_disposition": status,
            }
        )
        entries.append(
            {
                "bank_instance_id": "bank-A",
                "slot": 100,
                "bank_hash": "hash-A",
                "parent_bank_hash": None,
                "block_hash": "block-A",
                "entry_hash": f"entry-{i}",
                "absolute_block_transaction_index": 40 + i,
                "transaction_id": txid,
                "signature": sig,
                "message_hash": message,
            }
        )
    return {
        "schema": "agave_commit_to_root_synthetic_join_v1",
        "schema_version": 1,
        "source_pin": {
            "source_map_sha256": SOURCE_MAP_SHA256,
            "source_commit": SOURCE_COMMIT,
            "composite_diff_sha256": SOURCE_DIFF_SHA256,
            "composite_patches": list(COMPOSITE_PATCHES),
        },
        "decision_time_ms": 1000,
        "attempts": attempts,
        "commit_batches": commits,
        "block_entries": entries,
        "frozen_banks": [bank],
        "root_selection": [
            {
                "root_receipt_id": "root-receipt",
                "root_bank_hash": "hash-A",
                "root_slot": 100,
                "selected": True,
            }
        ],
        "time_receipts": [
            {
                "receipt_id": f"receipt-attempt-{i}",
                "stage": "attempt",
                "subject_id": f"attempt-{i}",
                "availability_lower_ms": i + 1,
                "availability_upper_ms": i + 2,
            }
            for i in range(3)
        ]
        + [
            {
                "receipt_id": "receipt-commit",
                "stage": "commit",
                "subject_id": "batch-7",
                "availability_lower_ms": 10,
                "availability_upper_ms": 11,
            },
            {
                "receipt_id": "receipt-freeze",
                "stage": "freeze",
                "subject_id": "bank-A",
                "availability_lower_ms": 20,
                "availability_upper_ms": 21,
            },
            {
                "receipt_id": "receipt-block",
                "stage": "block",
                "subject_id": "block-A",
                "availability_lower_ms": 30,
                "availability_upper_ms": 31,
            },
            {
                "receipt_id": "root-receipt",
                "stage": "root",
                "subject_id": "root-receipt",
                "availability_lower_ms": 40,
                "availability_upper_ms": 41,
            },
        ],
    }


def _reject(payload: dict, reason: str) -> None:
    with pytest.raises(JoinError, match=reason):
        verify(payload)


def test_valid_fixture_keeps_batch_and_absolute_positions_distinct_and_all_qualifications_false():
    result = verify(_fixture())
    assert result["status"] == "synthetic_join_consistent"
    assert result["joined_attempt_count"] == 3
    assert result["disposition_counts"] == {
        "executed_failed": 1,
        "executed_success": 1,
        "fees_only": 1,
    }
    assert result["joins"][0]["commit_batch_index"] == 0
    assert result["joins"][0]["absolute_block_transaction_index"] == 40
    assert result["joins"][1]["candidate_trace_status"] == "attempted_only"
    assert result["joins"][2]["candidate_trace_status"] == "unavailable_fees_only"
    assert result["joins"][1]["program_write_disposition"] == "rolled_back"
    assert result["joins"][1]["fee_nonce_disposition"] == "separate_rollback_effect"
    assert result["joins"][2]["fee_nonce_disposition"] == "rollback_accounts"
    assert not any(result["claims"].values())


@given(st.permutations((0, 1, 2)), st.permutations((0, 1, 2)), st.permutations((0, 1, 2)))
@settings(max_examples=30, deadline=None)
def test_verdict_is_invariant_to_input_row_order(attempt_order, commit_order, entry_order):
    payload = _fixture()
    baseline = verify(payload)
    payload["attempts"] = [payload["attempts"][i] for i in attempt_order]
    payload["commit_batches"] = [payload["commit_batches"][i] for i in commit_order]
    payload["block_entries"] = [payload["block_entries"][i] for i in entry_order]
    assert verify(payload) == baseline


@given(st.integers(min_value=100, max_value=10_000))
def test_commit_batch_position_is_independent_of_absolute_block_index(batch_index):
    payload = _fixture()
    payload["commit_batches"][0]["batch_index"] = batch_index
    joined = verify(payload)["joins"][0]
    assert joined["commit_batch_index"] == batch_index
    assert joined["absolute_block_transaction_index"] == 40


def test_same_slot_competing_bank_does_not_join_candidate_to_other_root_fork():
    payload = _fixture()
    fork = {
        "bank_instance_id": "bank-B",
        "slot": 100,
        "bank_hash": "hash-B",
        "parent_bank_hash": None,
        "frozen": True,
    }
    payload["frozen_banks"].append(fork)
    payload["time_receipts"].append(
        {
            "receipt_id": "receipt-freeze-B",
            "stage": "freeze",
            "subject_id": "bank-B",
            "availability_lower_ms": 25,
            "availability_upper_ms": 26,
        }
    )
    wrong = copy.deepcopy(payload["block_entries"][0])
    wrong.update(
        bank_instance_id="bank-B", bank_hash="hash-B", block_hash="block-B", entry_hash="entry-B"
    )
    payload["block_entries"][0] = wrong
    payload["time_receipts"].append(
        {
            "receipt_id": "receipt-block-B",
            "stage": "block",
            "subject_id": "block-B",
            "availability_lower_ms": 35,
            "availability_upper_ms": 36,
        }
    )
    _reject(payload, "attempt_block_entry_missing_or_ambiguous")


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (
            lambda p: p["block_entries"].append(copy.deepcopy(p["block_entries"][0])),
            "block_position_collision",
        ),
        (
            lambda p: p["commit_batches"].append(copy.deepcopy(p["commit_batches"][0])),
            "attempt_commit_ambiguous",
        ),
        (
            lambda p: p["commit_batches"][0].update(commit_disposition="executed_failed"),
            "attempt_commit_disposition_conflict",
        ),
        (
            lambda p: p["attempts"][0].update(attempt_status="executed_failed"),
            "attempt_commit_disposition_conflict",
        ),
        (
            lambda p: p["attempts"][0].update(candidate_trace_id=None),
            "executed_attempt_trace_missing",
        ),
        (
            lambda p: p["attempts"][2].update(candidate_trace_id="trace-fees"),
            "fees_only_candidate_trace_forbidden",
        ),
        (
            lambda p: p["root_selection"][0].update(root_bank_hash="missing"),
            "selected_root_bank_missing_or_mismatched",
        ),
        (
            lambda p: p["time_receipts"][0].update(availability_upper_ms=None),
            "availability_bound_unbounded",
        ),
        (
            lambda p: p["attempts"][1].update(fee_nonce_disposition="zero"),
            "attempt_rollback_effect_type_conflict",
        ),
        (lambda p: p["frozen_banks"][0].update(frozen=False), "unfrozen_bank_present"),
        (
            lambda p: p["block_entries"].pop(),
            "attempt_block_entry_missing_or_ambiguous",
        ),
        (
            lambda p: p["source_pin"].update(source_map_sha256="0" * 64),
            "source_pin_mismatch",
        ),
        (lambda p: p["root_selection"].clear(), "root_selection_missing_or_ambiguous"),
        (
            lambda p: p["commit_batches"][1].update(batch_index=0),
            "commit_batch_position_duplicate",
        ),
    ],
)
def test_ambiguous_or_incomplete_join_fails_closed(mutate, reason):
    payload = _fixture()
    mutate(payload)
    _reject(payload, reason)


def test_rooting_a_different_same_slot_fork_rejects_nonroot_candidate():
    payload = _fixture()
    payload["frozen_banks"] = [
        {
            "bank_instance_id": "bank-A",
            "slot": 100,
            "bank_hash": "hash-A",
            "parent_bank_hash": None,
            "frozen": True,
        },
        {
            "bank_instance_id": "bank-B",
            "slot": 100,
            "bank_hash": "hash-B",
            "parent_bank_hash": None,
            "frozen": True,
        },
    ]
    payload["root_selection"][0].update(root_bank_hash="hash-B")
    payload["time_receipts"].append(
        {
            "receipt_id": "receipt-freeze-B",
            "stage": "freeze",
            "subject_id": "bank-B",
            "availability_lower_ms": 25,
            "availability_upper_ms": 26,
        }
    )
    _reject(payload, "candidate_bank_not_on_selected_root")
