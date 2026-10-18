from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from tools.brain_senses_sell23_process_qualification_v1 import (
    QualificationError,
    build_receipt,
    classify_field_outcome,
    verify_source_manifest,
)


def test_exact_requires_every_destination_token_native_fee_and_rent_field() -> None:
    fields = {
        "source_identity_valid": True,
        "transaction_success": True,
        "destination_owner_program_known": True,
        "closed_account_token_endpoints_known": True,
        "destination_close_effect_attributable": True,
        "fee_lamports_known": True,
        "rent_reserve_known": True,
    }
    assert classify_field_outcome(**fields) == "exact"
    for field in (
        "destination_owner_program_known",
        "closed_account_token_endpoints_known",
        "destination_close_effect_attributable",
        "rent_reserve_known",
    ):
        incomplete = {**fields, field: False}
        assert classify_field_outcome(**incomplete) == "ambiguous"


@pytest.mark.parametrize(
    "field",
    ("source_identity_valid", "transaction_success", "fee_lamports_known"),
)
def test_invalid_source_transaction_or_fee_is_unsupported(field: str) -> None:
    fields = {
        "source_identity_valid": True,
        "transaction_success": True,
        "destination_owner_program_known": True,
        "closed_account_token_endpoints_known": True,
        "destination_close_effect_attributable": True,
        "fee_lamports_known": True,
        "rent_reserve_known": True,
    }
    assert classify_field_outcome(**{**fields, field: False}) == "unsupported"


def test_source_manifest_is_c3_only_and_reconciles_all_pinned_hashes() -> None:
    manifest = verify_source_manifest()
    assert manifest["scope"]["epoch_ids"] == [f"20261010T{hour:02d}0000Z" for hour in range(5, 11)]
    assert manifest["planned_denominator"]["selected_blocks"] == 96
    assert manifest["scope"]["failed_1100_epoch"] == "excluded"
    assert all("c4" not in epoch["epoch_id"] for epoch in manifest["epochs"])
    assert len(manifest["epochs"]) == 6
    assert sum(len(epoch["full_blocks"]) for epoch in manifest["epochs"]) == 96


def test_verifier_rejects_unexpected_source_root(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"schema_version":1,"content_sha256":"bad"}', encoding="utf-8")
    with pytest.raises((QualificationError, KeyError, TypeError)):
        verify_source_manifest(path)


def test_verifier_rejects_a_hash_valid_manifest_with_an_extra_source_root(tmp_path) -> None:
    manifest = verify_source_manifest()
    manifest.pop("manifest_sha256")
    manifest["source_root"] = "work/other-campaign"
    content = {
        key: value for key, value in manifest.items() if key not in ("content_sha256", "created_ns")
    }
    manifest["content_sha256"] = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(QualificationError, match="frozen pre-replay manifest"):
        verify_source_manifest(path)


def test_full_c3_replay_reconciles_censored_rows_and_unknown_fields() -> None:
    manifest = verify_source_manifest()
    report = build_receipt(
        manifest=manifest,
        decision_time_ns=time.time_ns(),
        tool_path=Path("tools/brain_senses_sell23_process_qualification_v1.py"),
    )
    denominators = report["denominators"]
    assert denominators["successful_exact_class_candidates"] == 553
    assert denominators["exact_ledger_rows_excluded_from_censored_scope"] == 68
    assert denominators["selected_censored_native_close_rows"] == 485
    assert denominators["failed_structural_ref_attempts"] == 2
    assert denominators["missing_full_blocks"] == denominators["unsupported_full_blocks"] == 0
    assert (
        denominators["zero_claim_scope"] == "only the verified 96 selected c3 full-block captures"
    )
    assert report["field_counts"]["source_identity_valid"] == 485
    assert report["field_counts"]["fee_lamports_exact"] == 485
    assert report["field_counts"]["destination_owner_program_unknown"] == 485
    assert report["field_counts"]["rent_reserve_unknown"] == 485
    assert report["denominators"]["row_outcomes"] == {
        "exact": 0,
        "ambiguous": 485,
        "unsupported": 0,
    }
