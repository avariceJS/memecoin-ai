from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT / "tests", ROOT / "tests/brain"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import test_brain_senses_raw_account_fixture_v1 as raw_helpers  # noqa: E402
import test_senses_agave_commit_to_root_join_v1 as join_helpers  # noqa: E402
import test_senses_candidate_position_witness_check_v1 as witness_helpers  # noqa: E402

from tools import brain_senses_agave_commit_to_root_join_v1 as commit_checker  # noqa: E402
from tools import brain_senses_candidate_position_witness_check_v1 as witness_checker  # noqa: E402
from tools import brain_senses_candidate_position_witness_envelope_v1 as envelope  # noqa: E402


def _make_envelope() -> dict[str, Any]:
    commit = join_helpers._fixture()
    witness = witness_helpers._witness()
    raw_bundle = raw_helpers._bundle()
    receipts = {row["type"]: row["payload"] for row in witness["receipts"]}
    raw_blockhash = raw_bundle["vectors"][0]["frame"]["blockhash"]
    receipts["block_join"]["ledger_blockhash"] = raw_blockhash
    receipts["root_status"]["ledger_blockhash"] = raw_blockhash
    witness_helpers._rechain_receipts(witness)
    attempt = commit["attempts"][0]
    attempt.update(
        {
            "bank_instance_id": receipts["bank_commit"]["bank_instance_id"],
            "signature": witness["signature"],
            "message_hash": witness["message_sha256"],
        }
    )
    for row in commit["attempts"]:
        row["bank_instance_id"] = receipts["bank_commit"]["bank_instance_id"]
    commit["frozen_banks"][0].update(
        {"bank_instance_id": receipts["bank_freeze"]["bank_instance_id"], "slot": 900}
    )
    commit["root_selection"][0].update({"root_slot": 900})
    for entry in commit["block_entries"]:
        entry.update(
            {
                "bank_instance_id": receipts["bank_commit"]["bank_instance_id"],
                "slot": 900,
                "block_hash": receipts["block_join"]["ledger_blockhash"],
            }
        )
    first = commit["block_entries"][0]
    first.update(
        {
            "absolute_block_transaction_index": receipts["block_join"]["block_transaction_index"],
            "block_hash": receipts["block_join"]["ledger_blockhash"],
            "signature": witness["signature"],
            "message_hash": witness["message_sha256"],
        }
    )
    for row in commit["commit_batches"]:
        row.update(
            {
                "commit_batch_id": receipts["bank_commit"]["commit_batch_id"],
            }
        )
    commit["commit_batches"][0].update(
        {
            "signature": witness["signature"],
            "message_hash": witness["message_sha256"],
        }
    )
    time_rows = {row["stage"]: row for row in commit["time_receipts"]}
    time_rows["commit"]["subject_id"] = receipts["bank_commit"]["commit_batch_id"]
    time_rows["freeze"]["subject_id"] = receipts["bank_freeze"]["bank_instance_id"]
    time_rows["block"]["subject_id"] = receipts["block_join"]["ledger_blockhash"]
    bank_commit = receipts["bank_commit"]
    block_join = receipts["block_join"]
    raw_frame = raw_bundle["vectors"][0]["frame"]
    raw_frame["slot"] = bank_commit["slot"]
    raw_frame["blockhash"] = block_join["ledger_blockhash"]
    raw_frame["capture_id"] = raw_helpers.fixture.sha256(
        raw_helpers.fixture.canonical(
            {"slot": raw_frame["slot"], "blockhash": raw_frame["blockhash"]}
        )
    )
    attempt = commit["attempts"][0]
    return {
        "schema": envelope.SCHEMA,
        "schema_version": envelope.SCHEMA_VERSION,
        "source_sha256": envelope.source_bindings(),
        "commit_to_root": {
            "payload": commit,
            "sha256": envelope.sha256(commit_checker.canonical_json_bytes(commit)),
        },
        "raw_account_fixture": {
            "bundle": raw_bundle,
            "sha256": raw_helpers.fixture.sha256(raw_helpers.fixture.canonical(raw_bundle)),
        },
        "candidate_witnesses": [
            {
                "witness_id": "synthetic-witness-0",
                "witness": witness,
                "sha256": envelope.sha256(witness_checker.canonical_json_bytes(witness)),
            }
        ],
        "synthetic_crosswalk": [
            {
                "attempt_id": attempt["attempt_id"],
                "witness_id": "synthetic-witness-0",
                "raw_case_id": raw_bundle["vectors"][0]["case_id"],
                "raw_capture_id": raw_frame["capture_id"],
                "slot": raw_frame["slot"],
                "blockhash": raw_frame["blockhash"],
                "claimed_signature_sha256": envelope.sha256(witness["signature"].encode("utf-8")),
                "claimed_message_sha256": witness["message_sha256"],
            }
        ],
        "claims": dict(envelope.FALSE_CLAIMS),
    }


def _rehash_components(value: dict[str, Any]) -> None:
    value["commit_to_root"]["sha256"] = envelope.sha256(
        commit_checker.canonical_json_bytes(value["commit_to_root"]["payload"])
    )
    value["raw_account_fixture"]["sha256"] = raw_helpers.fixture.sha256(
        raw_helpers.fixture.canonical(value["raw_account_fixture"]["bundle"])
    )
    for row in value["candidate_witnesses"]:
        row["sha256"] = envelope.sha256(witness_checker.canonical_json_bytes(row["witness"]))


def test_valid_synthetic_envelope_returns_non_authorizing_receipt() -> None:
    value = _make_envelope()
    receipt = envelope.verify_envelope(value)
    assert receipt["schema"] == envelope.RECEIPT_SCHEMA
    assert receipt["status"] == "synthetic_components_consistent"
    assert receipt["attempt_count"] == 3
    assert receipt["unique_rooted_join_count"] == 3
    assert receipt["candidate_witness_count"] == 1
    assert receipt["raw_account_vector_count"] == 6
    assert receipt["raw_execution_linkage"] == "unknown_raw_contract_has_no_transaction_identity"
    assert receipt["candidate_witnesses"][0]["decision_ns"] is None
    assert receipt["candidate_witnesses"][0]["candidate_position_status"] == (
        "unknown_or_exact_frame_state_only"
    )
    assert receipt["attempt_dispositions"][-1]["candidate_position_status"] == (
        "unavailable_fees_only"
    )
    assert receipt["claims"] == envelope.FALSE_CLAIMS
    assert all(item is False for item in receipt["claims"].values())
    assert receipt["cross_component_linkage"] == "synthetic_unverified_hypothesis_only"
    assert receipt["raw_execution_association"] == "unknown_synthetic_unverified_crosswalk"
    assert receipt["synthetic_crosswalk_count"] == 1
    assert receipt["synthetic_crosswalk"][0]["association_status"] == (
        "synthetic_unverified_hypothesis_only"
    )


def test_envelope_bytes_are_canonical_and_permutation_invariant() -> None:
    value = _make_envelope()
    raw = envelope.canonical_json_bytes(value)
    receipt = envelope.verify_bytes(raw)
    permuted = copy.deepcopy(value)
    permuted["commit_to_root"]["payload"]["attempts"].reverse()
    permuted["commit_to_root"]["payload"]["commit_batches"].reverse()
    permuted["commit_to_root"]["payload"]["block_entries"].reverse()
    permuted["raw_account_fixture"]["bundle"]["vectors"].reverse()
    _rehash_components(permuted)
    reordered = envelope.verify_envelope(permuted)
    assert {k: v for k, v in receipt.items() if k != "envelope_sha256"} == {
        k: v for k, v in reordered.items() if k != "envelope_sha256"
    }
    with pytest.raises(envelope.EnvelopeRejected, match="envelope_not_canonical"):
        envelope.verify_bytes(raw + b" ")


def test_source_pin_or_claim_mutations_fail_closed() -> None:
    value = _make_envelope()
    value["source_sha256"]["tools/brain_senses_raw_account_fixture_v1.py"] = "0" * 64
    with pytest.raises(envelope.EnvelopeRejected, match="source_binding_mismatch"):
        envelope.verify_envelope(value)
    value = _make_envelope()
    value["claims"]["economic_admission"] = True
    with pytest.raises(envelope.EnvelopeRejected, match="qualification_claim_present"):
        envelope.verify_envelope(value)


def test_commit_fork_and_absolute_index_splice_rejected() -> None:
    value = _make_envelope()
    value["commit_to_root"]["payload"]["root_selection"][0]["root_bank_hash"] = "sibling-bank"
    _rehash_components(value)
    with pytest.raises(envelope.EnvelopeRejected, match="commit_component_rejected"):
        envelope.verify_envelope(value)
    value = _make_envelope()
    value["commit_to_root"]["payload"]["block_entries"][0]["absolute_block_transaction_index"] += 1
    _rehash_components(value)
    receipt = envelope.verify_envelope(value)
    assert receipt["cross_component_linkage"] == "synthetic_unverified_hypothesis_only"


def test_duplicate_and_spliced_witness_rows_rejected() -> None:
    value = _make_envelope()
    value["candidate_witnesses"].append(copy.deepcopy(value["candidate_witnesses"][0]))
    with pytest.raises(envelope.EnvelopeRejected, match="witness_attempt_duplicate_or_invalid"):
        envelope.verify_envelope(value)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("raw_capture_id", "0" * 64, "synthetic_crosswalk_conflict"),
        ("slot", 901, "synthetic_crosswalk_conflict"),
        ("blockhash", "spliced-block", "synthetic_crosswalk_conflict"),
        ("claimed_signature_sha256", "0" * 64, "synthetic_crosswalk_conflict"),
        ("claimed_message_sha256", "0" * 64, "synthetic_crosswalk_conflict"),
    ],
)
def test_synthetic_crosswalk_rejects_spliced_identifiers(
    field: str, value: Any, reason: str
) -> None:
    payload = _make_envelope()
    payload["synthetic_crosswalk"][0][field] = value
    _rehash_components(payload)
    with pytest.raises(envelope.EnvelopeRejected, match=reason):
        envelope.verify_envelope(payload)


def test_synthetic_crosswalk_rejects_duplicate_component_identity() -> None:
    payload = _make_envelope()
    payload["synthetic_crosswalk"].append(copy.deepcopy(payload["synthetic_crosswalk"][0]))
    with pytest.raises(envelope.EnvelopeRejected, match="synthetic_crosswalk_duplicate_or_spliced"):
        envelope.verify_envelope(payload)
    value = _make_envelope()
    value["candidate_witnesses"].append(
        {**copy.deepcopy(value["candidate_witnesses"][0]), "witness_id": "synthetic-witness-1"}
    )
    with pytest.raises(envelope.EnvelopeRejected, match="witness_signature_duplicate_or_invalid"):
        envelope.verify_envelope(value)


@pytest.mark.parametrize("mutation", ["missing_role", "missing_clock", "missing_rent"])
def test_raw_account_role_epoch_and_rent_mutations_are_rejected(mutation: str) -> None:
    value = _make_envelope()
    vectors = value["raw_account_fixture"]["bundle"]["vectors"]
    if mutation == "missing_role":
        vector = vectors[0]
        vector["accounts"] = [row for row in vector["accounts"] if row["account_id"] != "fee-mint"]
    elif mutation == "missing_clock":
        vector = next(row for row in vectors if row["domain"] == "wsol_native_lifecycle")
        vector["clock_account_id"] = None
    else:
        vector = next(row for row in vectors if row["domain"] == "wsol_native_lifecycle")
        vector["rent_account_id"] = None
    _rehash_components(value)
    with pytest.raises(envelope.EnvelopeRejected, match="raw_component_rejected"):
        envelope.verify_envelope(value)


def test_hook_and_rollback_remain_unknown_or_attempted_only() -> None:
    value = _make_envelope()
    receipt = envelope.verify_envelope(value)
    assert receipt["candidate_witnesses"][0]["hook_effect_status"] == "unknown_or_unsupported"
    failed = next(
        row for row in receipt["attempt_dispositions"] if row["disposition"] == "executed_failed"
    )
    assert failed["candidate_position_status"] == "unknown_candidate_witness_unlinked"
    assert receipt["failed_attempts_are_attempted_only"] is True


def test_witness_clock_and_sibling_path_mutations_rejected() -> None:
    for mutation in ("clock", "sibling_path"):
        value = _make_envelope()
        witness = value["candidate_witnesses"][0]["witness"]
        if mutation == "clock":
            witness["receipts"][1]["observed_utc_ms"] = 10_000
            witness_helpers._rechain_receipts(witness)
        else:
            witness["frames"][1]["path"] = [0, 1]
            witness["post_execution_trace"][1]["path"] = [0, 1]
            witness["expected_candidate_frames"][1]["path"] = [0, 1]
            witness_helpers._reseal(witness)
        _rehash_components(value)
        with pytest.raises(envelope.EnvelopeRejected, match="candidate_witness_rejected"):
            envelope.verify_envelope(value)
