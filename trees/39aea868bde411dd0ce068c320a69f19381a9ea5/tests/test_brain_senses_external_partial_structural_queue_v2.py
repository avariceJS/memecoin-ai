from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools.brain_senses_external_partial_structural_queue_v2 import (
    FAILURE_RECEIPT,
    FINAL_STATUS_RECEIPT,
    PartialQueueError,
    _check_receipts,
    _class_rows,
    _full_schedule_rows,
)


def _receipts(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    failure = tmp_path / FAILURE_RECEIPT.name
    status = tmp_path / FINAL_STATUS_RECEIPT.name
    failure.write_bytes(FAILURE_RECEIPT.read_bytes())
    status.write_bytes(FINAL_STATUS_RECEIPT.read_bytes())
    activation = {
        "plan_sha256": "2bb19afee5475173e7e5a73a59d1cd33a27c6af1ef8e309fc39bfc025fa5a688",
        "plist_sha256": "080d8a1be8f6e133c620d09e16e8c2fc19dd8c2dfa687581859dd81b36a45d65",
    }
    return failure, status, activation


def test_receipts_bind_final_expiry_and_disabled_lease(tmp_path: Path) -> None:
    failure, status, activation = _receipts(tmp_path)
    result = _check_receipts(
        failure_path=failure,
        status_path=status,
        activation=activation,
        plan={
            "storage": {
                "root_id": "9428695af6d4f46c08e1a444cdc66de8",
                "volume_uuid": "8f8f1081-9be4-4914-9111-75f1b90255bf",
            }
        },
    )
    assert result["failure_sha256"] == result["disable_sha256"]
    assert result["final"]["status_projection"]["sealed_windows"] == 4
    assert len(result["final"]["status_projection"]["expired_unobserved"]) == 9


def test_status_receipt_mutation_fails_closed(tmp_path: Path) -> None:
    failure, status, activation = _receipts(tmp_path)
    value = json.loads(status.read_text())
    value["status_projection"]["successor_eligible"] = True
    status.write_text(json.dumps(value))
    with pytest.raises(PartialQueueError, match="final_status_projection_hash"):
        _check_receipts(
            failure_path=failure,
            status_path=status,
            activation=activation,
            plan={
                "storage": {
                    "root_id": "9428695af6d4f46c08e1a444cdc66de8",
                    "volume_uuid": "8f8f1081-9be4-4914-9111-75f1b90255bf",
                }
            },
        )


def test_full_schedule_retains_unknown_tail_as_null_counts() -> None:
    planned = [
        {"bucket": f"20261012T{hour:02d}{minute:02d}00Z", "start_ns": index, "end_ns": index + 1}
        for index, (hour, minute) in enumerate(
            [(6, minute) for minute in range(5, 60, 5)] + [(7, 0)]
        )
    ]
    sealed = [row["bucket"] for row in planned[:3]]
    observed = [{"bucket": bucket, "state": "sealed", "selected_blocks": 16} for bucket in sealed]
    rows = _full_schedule_rows(planned, sealed, observed, {row["bucket"] for row in planned[3:]})
    assert len(rows) == 12
    assert [row["state"] for row in rows[:4]] == [
        "sealed",
        "sealed",
        "sealed",
        "callback_failed_unobserved",
    ]
    assert all(row["selected_blocks"] is None for row in rows[3:])
    assert all(row["inclusion_probability"] is None for row in rows[3:])
    with pytest.raises(PartialQueueError, match="schedule_projection_forbidden"):
        _full_schedule_rows(planned[:3], sealed, observed, set())


def test_class_denominators_keep_occurrences_and_transaction_status_separate() -> None:
    refs = [
        {
            "class_fingerprint_sha256": "a" * 64,
            "program_id": "pump-program",
            "instruction_data_sha256": "b" * 64,
            "account_count": 5,
            "transaction_status": "success",
        },
        {
            "class_fingerprint_sha256": "a" * 64,
            "program_id": "pump-program",
            "instruction_data_sha256": "b" * 64,
            "account_count": 5,
            "transaction_status": "failed",
        },
    ]
    rows = _class_rows(refs)
    assert len(rows) == 1
    assert rows[0]["classification"] == "unresolved_unsupported_no_decoder_claim"
    assert rows[0]["reference_occurrences"] == 2
    assert rows[0]["transaction_status_counts"] == {"success": 1, "failed": 1, "unknown": 0}
