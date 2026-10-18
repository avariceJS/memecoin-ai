from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.brain_senses_alchemy_archive_mint_manifest_v1 as manifest  # noqa: E402


def _synthetic_inputs() -> tuple[dict, dict]:
    identity_a = {
        "slot": 50,
        "transaction_index": 2,
        "instruction_path": [1],
        "signature": "sig-a",
    }
    identity_b = {
        "slot": 50,
        "transaction_index": 4,
        "instruction_path": [2],
        "signature": "sig-b",
    }
    mint = "T22MintSynthetic"
    capture = {
        "slot": 50,
        "blockhash": "block-50",
        "response_sha256": "response-hash",
        "compressed_sha256": "compressed-hash",
        "receipt_time_ns": 1_000,
        "transaction_count": 2,
    }
    locator_rows = [
        {
            "identity": identity,
            "target_t22_mint": mint,
            "slot_blockhash": "block-50",
            "target_gate": {"eligible": True},
            "snapshot_locator": {"whole_account_two_sided_candidate": True},
        }
        for identity in (identity_a, identity_b)
    ]
    feasibility_rows = [
        {
            "identity": identity,
            "required_state_accounts": {"mint_roles": {"base_mint": mint}},
        }
        for identity in (identity_a, identity_b)
    ]
    locator = {
        "contract": "pumpswap_buy_exact_v1_c4_state_snapshot_locator_v1",
        "rows": locator_rows,
        "rows_canonical_sha256": manifest._sha256(manifest.canonical(locator_rows).encode()),
        "source": {
            "capture_root": manifest.CAPTURE_ROOT,
            "feasibility_receipt_sha256": manifest.FEASIBILITY_RECEIPT_SHA256,
            "tool_sha256": manifest._sha256(manifest.LOCATOR_TOOL.read_bytes()),
            "selected_slot_count": 1,
            "selected_transaction_count": 2,
            "captures": [capture],
            "repository_head": "frozen-code-commit",
        },
        "counts": {"rows": 2},
    }
    feasibility = {
        "contract": "pumpswap_buy_exact_v1_c4_bank_state_bracketing_feasibility_v1",
        "counts": {"rows": 2},
        "rows": feasibility_rows,
        "source": {
            "captures": [
                {
                    "slot": 50,
                    "response_sha256": "response-hash",
                    "compressed_sha256": "compressed-hash",
                    "receipt_ns": 1_000,
                }
            ]
        },
    }
    return locator, feasibility


@pytest.fixture
def synthetic_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manifest, "EXPECTED_ROWS", 2)
    monkeypatch.setattr(manifest, "EXPECTED_SLOTS", 1)
    monkeypatch.setattr(manifest, "EXPECTED_TRANSACTIONS", 2)
    monkeypatch.setattr(manifest, "EXPECTED_PAIRS_PER_BOUNDARY", 1)


def test_frozen_locator_joins_and_deduplicates_150_pairs_without_rpc() -> None:
    result = manifest.load_manifest(repository_head="test-head")

    assert result["status"] == "no_rpc_proposed_manifest_access_gate_pending"
    assert result["counts"] == {
        "frozen_identities": 140,
        "unique_pre_state_pairs": 75,
        "unique_post_state_pairs": 75,
        "unique_total_pairs_and_proposed_max_requests": 150,
        "actual_requests": 0,
        "actual_raw_response_bytes": 0,
        "actual_cu": None,
    }
    assert result["source"]["selected_transaction_count"] == 17_607
    assert result["source"]["manifest_tool_path"] == (
        "tools/brain_senses_alchemy_archive_mint_manifest_v1.py"
    )
    assert (
        result["source"]["manifest_tool_sha256"]
        == hashlib.sha256(Path(manifest.__file__).read_bytes()).hexdigest()
    )
    assert result["access_gate"]["rpc_requests_made_by_this_manifest"] == 0
    assert all(
        pair["planned_result"]["reservation_id"] is None
        and pair["planned_result"]["request_journal_entry_id"] is None
        and pair["planned_result"]["raw_response_sha256"] is None
        and pair["planned_result"]["receipt_ns"] is None
        and pair["planned_result"]["availability_ns"] is None
        and pair["planned_result"]["provider_cu_delta"] is None
        for pair in result["pairs"]
    )


def test_duplicate_identity_pairs_deduplicate_by_exact_mint_and_slot(
    synthetic_counts: None,
) -> None:
    locator, feasibility = _synthetic_inputs()
    result = manifest.build_manifest(
        locator,
        feasibility,
        locator_receipt_sha256="locator-hash",
        repository_head="test-head",
    )

    assert len(result["pairs"]) == 2
    by_boundary = {pair["boundary"]: pair for pair in result["pairs"]}
    assert by_boundary["pre_state_S_minus_1"]["requested_finalized_slot"] == 49
    assert by_boundary["post_state_S"]["requested_finalized_slot"] == 50
    assert len(by_boundary["pre_state_S_minus_1"]["candidate_identities"]) == 2
    assert len(by_boundary["post_state_S"]["candidate_identities"]) == 2
    request_ids = {pair["planned_request"]["request_identity_sha256"] for pair in result["pairs"]}
    assert len(request_ids) == 2


def test_mint_join_mismatch_fails_closed(synthetic_counts: None) -> None:
    locator, feasibility = _synthetic_inputs()
    locator["rows"][0]["target_t22_mint"] = "different-mint"
    locator["rows_canonical_sha256"] = manifest._sha256(
        manifest.canonical(locator["rows"]).encode()
    )

    with pytest.raises(ValueError, match="locator_base_mint_role_join_mismatch"):
        manifest.build_manifest(
            locator,
            feasibility,
            locator_receipt_sha256="locator-hash",
            repository_head="test-head",
        )


def test_blockhash_join_mismatch_fails_closed(synthetic_counts: None) -> None:
    locator, feasibility = _synthetic_inputs()
    locator["rows"][0]["slot_blockhash"] = "different-block"
    locator["rows_canonical_sha256"] = manifest._sha256(
        manifest.canonical(locator["rows"]).encode()
    )

    with pytest.raises(ValueError, match="locator_row_blockhash_join_mismatch"):
        manifest.build_manifest(
            locator,
            feasibility,
            locator_receipt_sha256="locator-hash",
            repository_head="test-head",
        )


def test_locator_hash_or_identity_drift_fails_closed(synthetic_counts: None) -> None:
    locator, feasibility = _synthetic_inputs()
    locator["rows_canonical_sha256"] = "wrong-hash"
    with pytest.raises(ValueError, match="locator_rows_hash_mismatch"):
        manifest.build_manifest(
            locator,
            feasibility,
            locator_receipt_sha256="locator-hash",
            repository_head="test-head",
        )

    locator, feasibility = _synthetic_inputs()
    feasibility = copy.deepcopy(feasibility)
    feasibility["rows"][0]["identity"]["signature"] = "different-sig"
    with pytest.raises(ValueError, match="locator_feasibility_identity_set_mismatch"):
        manifest.build_manifest(
            locator,
            feasibility,
            locator_receipt_sha256="locator-hash",
            repository_head="test-head",
        )


def test_outcome_taxonomy_and_proposed_caps_are_not_authorization() -> None:
    outcomes = manifest.load_manifest(repository_head=None)["outcome_states"]
    assert "account_nonexistent_value_null" in outcomes
    assert "out_of_coverage" in outcomes
    assert "transport_failure" in outcomes
    assert "partial_or_malformed" in outcomes
    assert "cross_source_conflict" in outcomes

    # The proposed budget is only a planning ceiling; it cannot mark access open.
    result = manifest.load_manifest(repository_head=None)
    assert result["proposed_caps_not_authorization"]["max_request_attempts"] == 150
    assert result["proposed_caps_not_authorization"]["proposed_max_standard_cu"] == 1_500
    assert result["proposed_caps_not_authorization"]["authorization"] == (
        "none; any acquisition requires separate access/budget review and approval"
    )
    assert result["access_gate"]["entitlement_verified"] is False
