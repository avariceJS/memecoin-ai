from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from brain_senses_sell23_c4_native_flow_audit import (  # noqa: E402
    _validate_receipt_paths,
    completed_frame_ids,
    select_pass_examples,
)


def test_first_four_completed_scheduled_frames_skip_gaps_and_keep_order(tmp_path: Path) -> None:
    epochs = tmp_path / "epochs"
    epochs.mkdir()
    states = {
        "20261010T130000Z": "frame_complete",
        "20261010T140000Z": "transport_failed",
        "20261010T150000Z": "frame_complete",
        "20261010T160000Z": "frame_complete",
        "20261010T170000Z": "frame_complete",
        "20261010T180000Z": "frame_complete",
    }
    for bucket, status in states.items():
        epoch = epochs / bucket
        epoch.mkdir()
        bucket_time = datetime.strptime(bucket, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        trigger_ns = int((bucket_time + timedelta(minutes=5)).timestamp()) * 1_000_000_000
        (epoch / "epoch-plan.json").write_text(
            json.dumps(
                {
                    "epoch_id": bucket,
                    "bucket": bucket,
                    "created_ns": trigger_ns + 10,
                }
            )
        )
        (epoch / "requests").mkdir()
        (epoch / "request-results").mkdir()
        (epoch / "requests/000000000001.json").write_text(
            json.dumps(
                {
                    "sequence": 1,
                    "phase": "anchor",
                    "method": "getSlot",
                    "reserved_ns": trigger_ns + 20,
                }
            )
        )
        (epoch / "request-results/000000000001.json").write_text(
            json.dumps(
                {
                    "sequence": 1,
                    "status": "captured",
                    "request_started_ns": trigger_ns + 25,
                }
            )
        )
        (epoch / "epoch-summary.json").write_text(
            json.dumps({"status": status, "sealed_ns": trigger_ns + 30})
        )

    schedule = {
        "service_policy": {
            "start_calendar_interval": {"minute": 5},
            "schedule_timezone": "host-local calendar trigger; epoch buckets are UTC",
        }
    }
    freeze_bucket = datetime.strptime("20261010T140000Z", "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    freeze_ns = int((freeze_bucket + timedelta(minutes=5)).timestamp()) * 1_000_000_000
    selected, skipped = completed_frame_ids(tmp_path, freeze_ns=freeze_ns, schedule=schedule)

    assert selected == [
        "20261010T150000Z",
        "20261010T160000Z",
        "20261010T170000Z",
        "20261010T180000Z",
    ]
    assert [row["bucket"] for row in skipped] == ["20261010T130000Z", "20261010T140000Z"]
    assert skipped[0]["status"] == "completed_before_or_during_freeze"
    assert skipped[1]["status"] == "transport_failed"


def test_pass_examples_are_stable_and_limited_per_pool() -> None:
    rows = [
        {"signature": f"sig-{index}", "pool": pool, "flow_status": status, "row": index}
        for pool in ("pool-b", "pool-a")
        for index, status in enumerate(
            [
                "source_account_flow_exact",
                "source_account_flow_exact",
                "source_account_flow_exact",
                "unsupported_touch",
            ]
        )
    ]

    selected = select_pass_examples(rows)
    reversed_selected = select_pass_examples(list(reversed(rows)))

    assert selected == reversed_selected
    assert len(selected) == 4
    assert all(row["flow_status"] == "source_account_flow_exact" for row in selected)
    assert {row["pool"] for row in selected} == {"pool-a", "pool-b"}
    assert all(
        sum(row["pool"] == pool for row in selected) == 2 for pool in {"pool-a", "pool-b"}
    )


def test_frame_with_missing_acquisition_time_is_ineligible(tmp_path: Path) -> None:
    epoch = tmp_path / "epochs" / "20261010T150000Z"
    (epoch / "requests").mkdir(parents=True)
    (epoch / "request-results").mkdir()
    (epoch / "epoch-plan.json").write_text(
        json.dumps(
            {
                "epoch_id": epoch.name,
                "bucket": epoch.name,
                "created_ns": 1_800_000_000_000_000_000,
            }
        )
    )
    (epoch / "epoch-summary.json").write_text(
        json.dumps({"status": "frame_complete", "sealed_ns": 1_800_000_000_000_000_100})
    )
    schedule = {
        "service_policy": {
            "start_calendar_interval": {"minute": 5},
            "schedule_timezone": "host-local calendar trigger; epoch buckets are UTC",
        }
    }

    selected, skipped = completed_frame_ids(
        tmp_path, freeze_ns=1_700_000_000_000_000_000, schedule=schedule
    )

    assert selected == []
    assert skipped[0]["status"] == "timing_evidence_missing_or_ambiguous"
    assert skipped[0]["eligible"] is False


def test_frame_with_request_before_reservation_order_is_ineligible(tmp_path: Path) -> None:
    epoch = tmp_path / "epochs" / "20261010T150000Z"
    (epoch / "requests").mkdir(parents=True)
    (epoch / "request-results").mkdir()
    bucket_time = datetime.strptime(epoch.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    trigger_ns = int((bucket_time + timedelta(minutes=5)).timestamp()) * 1_000_000_000
    (epoch / "epoch-plan.json").write_text(
        json.dumps(
            {
                "epoch_id": epoch.name,
                "bucket": epoch.name,
                "created_ns": trigger_ns + 10,
            }
        )
    )
    (epoch / "requests/000000000001.json").write_text(
        json.dumps(
            {
                "sequence": 1,
                "phase": "anchor",
                "method": "getSlot",
                "reserved_ns": trigger_ns - 1,
            }
        )
    )
    (epoch / "request-results/000000000001.json").write_text(
        json.dumps(
            {
                "sequence": 1,
                "status": "captured",
                "request_started_ns": trigger_ns + 20,
            }
        )
    )
    (epoch / "epoch-summary.json").write_text(
        json.dumps({"status": "frame_complete", "sealed_ns": trigger_ns + 30})
    )
    schedule = {
        "service_policy": {
            "start_calendar_interval": {"minute": 5},
            "schedule_timezone": "host-local calendar trigger; epoch buckets are UTC",
        }
    }

    selected, skipped = completed_frame_ids(
        tmp_path, freeze_ns=1_700_000_000_000_000_000, schedule=schedule
    )

    assert selected == []
    assert skipped[0]["status"] == "timing_evidence_missing_or_ambiguous"
    assert skipped[0]["reason"] == "frame_request_time_order_invalid"


def test_capture_receipt_paths_cannot_escape_capture_directory(tmp_path: Path) -> None:
    receipt_dir = tmp_path / "capture"
    receipt_dir.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    receipt = receipt_dir / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "method": "getBlock",
                "storage": {"path": "../outside.json"},
                "response_bytes": 2,
            }
        )
    )

    try:
        _validate_receipt_paths([receipt])
    except ValueError as exc:
        assert str(exc) == "capture_storage_path_escapes_receipt"
    else:
        raise AssertionError("receipt path traversal was accepted")
