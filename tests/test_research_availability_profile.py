from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from memecoin_experience.availability_profile import analyze


def test_profiles_late_zero_delay_and_missing_availability() -> None:
    payload = {
        "decision_ns": 10,
        "rows": [
            {"id": "late", "stream": "ticks", "event_ns": 5, "available_ns": 12, "receipt_ns": 15},
            {"id": "zero", "stream": "ticks", "event_ns": 10, "available_ns": 10, "receipt_ns": 10},
            {"id": "missing", "stream": "book", "event_ns": 3, "available_ns": None, "receipt_ns": 4},
        ],
    }
    result = analyze(payload)
    assert result["schema"] == "brain-research-diagnostic.v1"
    assert result["kind"] == "availability_profile"
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    assert result["input_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert result["summary"]["total"] == 3
    assert result["summary"]["future_event_count"] == 0
    assert result["summary"]["not_yet_available_count"] == 1
    assert result["summary"]["missing_timestamp_counts"]["available_ns"] == 1
    assert result["summary"]["event_to_availability"] == {
        "valid_count": 2,
        "invalid_order_count": 0,
        "p50_nearest_rank": 0,
        "p95_nearest_rank": 7,
    }
    assert result["details"]["book"]["event_to_availability"]["p50_nearest_rank"] is None


def test_counts_invalid_ordering_and_empty_denominators() -> None:
    result = analyze({
        "decision_ns": 1,
        "rows": [{"id": "bad", "stream": "s", "event_ns": 3, "available_ns": 2, "receipt_ns": 1}],
    })
    summary = result["summary"]
    assert summary["future_event_count"] == 1
    assert summary["not_yet_available_count"] == 1
    assert summary["event_to_availability"] == {
        "valid_count": 0,
        "invalid_order_count": 1,
        "p50_nearest_rank": None,
        "p95_nearest_rank": None,
    }
    assert summary["availability_to_receipt"]["invalid_order_count"] == 1


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"decision_ns": True, "rows": []}, "availability_profile_decision_ns"),
        ({"decision_ns": -1, "rows": []}, "availability_profile_decision_ns"),
        ({"decision_ns": 0, "rows": ()}, "availability_profile_rows"),
        ({"decision_ns": 0, "rows": [{"id": "x", "stream": "s", "event_ns": False, "available_ns": None, "receipt_ns": None}]}, "availability_profile_event_ns"),
        ({"decision_ns": 0, "rows": [
            {"id": "x", "stream": "s", "event_ns": None, "available_ns": None, "receipt_ns": None},
            {"id": "x", "stream": "t", "event_ns": None, "available_ns": None, "receipt_ns": None},
        ]}, "availability_profile_duplicate_id"),
    ],
)
def test_rejects_invalid_input_with_stable_errors(payload: dict, error: str) -> None:
    with pytest.raises(ValueError, match=f"^{error}$"):
        analyze(payload)


def test_cli_reads_one_json_payload_and_writes_envelope() -> None:
    payload = {"decision_ns": 4, "rows": [{"id": "x", "stream": "s", "event_ns": 1, "available_ns": 2, "receipt_ns": 3}]}
    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.availability_profile"],
        input=json.dumps(payload), text=True, capture_output=True, check=True,
    )
    assert json.loads(completed.stdout) == analyze(payload)
    assert completed.stderr == ""
