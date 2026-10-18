from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from itertools import product
from pathlib import Path

import pytest

from memecoin_pumpswap.decoder import TOKEN, TOKEN2022
from tools import brain_senses_buy_exact_tail2_c4_audit as audit
from tools.brain_senses_buy_exact_tail2_c4_audit import _replication_claim, _tail2_predicate


def _eligible_reference():
    return {
        "transaction_status": "successful_tx_instruction_reference",
        "argument_state": "pinned_idl_v1_shape",
        "instruction_data_length": 25,
        "remaining_account_count": 2,
        "suffix_state": "documented_fee_pair_suffix_candidate",
        "identified_roles": {
            "base_token_program": TOKEN,
            "quote_token_program": TOKEN,
        },
    }


def test_tail2_predicate_is_invariant_to_unrelated_metadata_product():
    values = (None, 0, "diagnostic", False, ("unknown",), {"x": 1})
    for extra_a, extra_b, extra_c in product(values, repeat=3):
        row = _eligible_reference()
        row.update({"source_note": extra_a, "amount": extra_b, "event": extra_c})
        assert _tail2_predicate(row)


def test_each_frozen_predicate_boundary_rejects_a_single_field_change():
    mutations = (
        ("transaction_status", "failed_tx_attempt_only"),
        ("argument_state", "unsupported_payload_shape"),
        ("instruction_data_length", 24),
        ("remaining_account_count", 3),
        ("suffix_state", "unsupported_suffix_layout"),
    )
    for field, value in mutations:
        row = deepcopy(_eligible_reference())
        row[field] = value
        assert not _tail2_predicate(row)
    for field, value in (("base_token_program", TOKEN2022), ("quote_token_program", TOKEN2022)):
        row = deepcopy(_eligible_reference())
        row["identified_roles"][field] = value
        assert not _tail2_predicate(row)


def test_tail2_predicate_fails_closed_for_missing_or_wrongly_typed_roles():
    row = _eligible_reference()
    row["identified_roles"] = None
    assert not _tail2_predicate(row)
    row = _eligible_reference()
    row["instruction_data_length"] = True
    assert not _tail2_predicate(row)


def test_replication_claim_preserves_zero_unknown_and_mismatch_states():
    assert _replication_claim(0, 0, 0, 0) == "no_tail2_replication_claim_zero_eligible_references"
    assert _replication_claim(None, 1, 1, 0) == (
        "no_replication_claim_unknown_frozen_population_cases"
    )
    assert _replication_claim(0, 0, 0, 0, 1) == (
        "no_replication_claim_unknown_frozen_population_cases"
    )
    assert (
        _replication_claim(3, 3, 2, 0)
        == "no_replication_claim_incomplete_candidate_flow_or_event_checks"
    )
    assert _replication_claim(3, 3, 2, 1) == "blocked_by_semantic_disagreement"
    assert _replication_claim(3, 3, 3, 0) == (
        "candidate_checked_transfer_and_BuyEvent_agreement_on_selected_captured_block_class_only"
    )
    assert _replication_claim(3, 3, 3, 0, 1) == (
        "no_replication_claim_unknown_frozen_population_cases"
    )


def test_population_scope_keeps_failed_attempt_uncertainty_out_of_success_denominator():
    counters = Counter(
        captured_transactions=12,
        **{
            "transaction_outcome:successful": 10,
            "transaction_outcome:failed": 2,
            "frozen_tail2_successful_dual_legacy_denominator": 4,
            "failed_tx_unresolved_instruction_transactions": 1,
        },
    )
    scope = audit._population_scope(counters)
    assert scope["observed_candidate_lower_bound"] == 4
    assert scope["exact_candidate_denominator"] == 4
    assert scope["complete_successful_class"] is True
    assert scope["failed_attempt_reference_census_complete"] is False


@pytest.mark.parametrize(
    "unknown_counter",
    (
        "transaction_outcome:unknown",
        "successful_tx_unresolved_instruction_transactions",
        "successful_tx_normalization_unknown_transactions",
        "successful_tx_candidate_detection_unknown_transactions",
        "unknown_frozen_population_reference_rows",
    ),
)
def test_population_scope_withholds_exact_denominator_for_each_unknown_class(unknown_counter):
    outcome = (
        {"transaction_outcome:unknown": 1}
        if unknown_counter == "transaction_outcome:unknown"
        else {"transaction_outcome:successful": 1}
    )
    counters = Counter(captured_transactions=1, **outcome)
    counters["frozen_tail2_successful_dual_legacy_denominator"] = 1
    counters[unknown_counter] = 1
    scope = audit._population_scope(counters)
    assert scope["observed_candidate_lower_bound"] == 1
    assert scope["exact_candidate_denominator"] is None
    assert scope["complete_successful_class"] is False


def test_transaction_outcome_requires_explicit_metadata_error_field():
    assert audit._transaction_outcome({"meta": {"err": None}}) == "successful"
    assert audit._transaction_outcome({"meta": {"err": {"InstructionError": [0, "x"]}}}) == (
        "failed"
    )
    assert audit._transaction_outcome({"meta": {"err": "AccountInUse"}}) == "failed"
    assert audit._transaction_outcome({"meta": {"err": False}}) == "unknown"
    assert audit._transaction_outcome({"meta": {}}) == "unknown"
    assert audit._transaction_outcome({}) == "unknown"


def test_jsonrpc_boolean_id_is_not_accepted_as_integer_one():
    valid = b'{"jsonrpc":"2.0","id":1,"result":{}}'
    invalid = b'{"jsonrpc":"2.0","id":true,"result":{}}'
    assert audit._response_result(valid) == {}
    with pytest.raises(ValueError, match="audit_rpc_response_envelope_invalid"):
        audit._response_result(invalid)


def test_audit_refuses_fewer_than_four_frames_before_campaign_metadata_reads(monkeypatch):
    def forbidden_read(_path: Path):
        raise AssertionError("campaign metadata must not be read below the four-frame gate")

    monkeypatch.setattr(audit, "_read_json", forbidden_read)
    with pytest.raises(ValueError, match="audit_four_frame_selection_required"):
        audit._preflight_frames({"campaign_root": "/unread", "selected_frames": []})


def test_malformed_pump_instruction_bytes_are_unknown_not_exact_zero():
    tx = {
        "transaction": {
            "message": {
                "accountKeys": [audit.PROGRAM],
                "instructions": [{"programIdIndex": 0, "data": "0O-not-base58"}],
            }
        },
        "meta": {"loadedAddresses": {}, "innerInstructions": []},
    }
    assert audit._raw_program_resolution_unknowns(tx) == (0, 1)


def test_unresolvable_program_index_is_recorded_as_unknown():
    tx = {
        "transaction": {
            "message": {
                "accountKeys": [audit.PROGRAM],
                "instructions": [{"programIdIndex": 7, "data": "1"}],
            }
        },
        "meta": {"loadedAddresses": {}, "innerInstructions": []},
    }
    assert audit._raw_program_resolution_unknowns(tx) == (1, 0)


def test_malformed_candidate_scan_is_unknown_not_non_candidate():
    assert audit._candidate_detection_state({"transaction": None, "meta": None}) == "unknown"


def test_post_start_failure_seals_incomplete_and_blocks_a_second_pass(tmp_path, monkeypatch):
    plan_path = tmp_path / "plan.json"
    freeze_path = tmp_path / "freeze.json"
    plan_path.write_text("{}")
    freeze_path.write_text("{}")
    output = tmp_path / "audit"
    monkeypatch.setattr(
        audit,
        "_preflight",
        lambda _plan, _freeze: (
            {"campaign_root": str(tmp_path / "campaign"), "zstd": {"path": "/unused"}},
            {"plan_sha256": "p", "freeze_receipt_sha256": "f"},
        ),
    )
    monkeypatch.setattr(audit, "_preflight_frames", lambda _plan: [{"bucket": "synthetic"}])

    def fail_after_start(*_args):
        raise ValueError("synthetic_semantic_failure")

    monkeypatch.setattr(audit, "_audit_frame", fail_after_start)
    with pytest.raises(ValueError, match="synthetic_semantic_failure"):
        audit.run_audit(plan_path, freeze_path, output)
    incomplete = json.loads((output / "audit-incomplete-receipt.json").read_text())
    assert incomplete["status"] == "incomplete_semantic_audit_no_replay_permitted"
    assert (output / "attempt-started.json").is_file()
    assert not (output / "audit-receipt.json").exists()
    with pytest.raises(ValueError, match="audit_output_exists_refusing_replay"):
        audit.run_audit(plan_path, freeze_path, output)
