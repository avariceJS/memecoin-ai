from __future__ import annotations

from tools.brain_senses_buy_exact_v1_census import _source_receipt_metadata


def test_source_receipt_metadata_accepts_legacy_capture_census_receipt():
    metadata = _source_receipt_metadata(
        {
            "git_commit": "d2f39b519db33fb2a5b5edca84acb03f1d663464",
            "offline_replay": True,
        }
    )

    assert metadata == {
        "provider_audit_code_commit": "d2f39b519db33fb2a5b5edca84acb03f1d663464",
        "provider_audit_analysis_sha256": None,
    }


def test_source_receipt_metadata_preserves_explicit_audit_fields():
    metadata = _source_receipt_metadata(
        {
            "audit_code_commit": "audit-commit",
            "git_commit": "fallback-commit",
            "analysis_sha256": "analysis-hash",
        }
    )

    assert metadata == {
        "provider_audit_code_commit": "audit-commit",
        "provider_audit_analysis_sha256": "analysis-hash",
    }
