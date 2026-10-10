from __future__ import annotations

import json
from pathlib import Path

from tools.brain_senses_buy_exact_v1_census import _capture_rows, _source_receipt_metadata


def test_capture_rows_reads_full_blocks_from_v3_epoch_receipts(tmp_path: Path):
    epoch = tmp_path / "20261010T050000Z"
    epoch.mkdir(parents=True)
    (epoch / "attempt-context.json").write_text("{}")
    capture_dir = epoch / "captures"

    def write_capture(sequence: int, method: str, details: str | None):
        folder = capture_dir / f"{sequence:012d}"
        folder.mkdir(parents=True)
        request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": [455116910]}
        if details is not None:
            request["params"].append({"transactionDetails": details})
        receipt = {
            "source_alias": "helius-free",
            "method": method,
            "request_json": json.dumps(request),
            "storage": {"path": "response.json.zst"},
        }
        path = folder / "receipt.json"
        path.write_text(json.dumps(receipt))
        return path

    inventory = write_capture(1, "getBlocks", None)
    signature_only = write_capture(2, "getBlock", "signatures")
    full = write_capture(3, "getBlock", "full")

    rows = _capture_rows(epoch, v3_layout=True)

    full_receipt = json.loads(full.read_text())
    assert rows == [
        (
            full.parent / "response.json.zst",
            full_receipt,
            json.loads(full_receipt["request_json"]),
        )
    ]
    assert inventory not in [row[0] for row in rows]
    assert signature_only not in [row[1] for row in rows]


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
