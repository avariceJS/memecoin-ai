from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from memecoin_experience.expected_observation_timeliness import analyze


def test_expected_slot_deadline_counts_and_deterministic_hash() -> None:
    payload = {
        "intervals": [{"id": "i", "stream": "s", "start_ns": 0, "end_ns": 20,
                       "slot_width_ns": 2, "deadline_ns": 10}],
        "observations": [
            {"id": "timely", "stream": "s", "event_ns": 0, "available_ns": 10, "receipt_ns": 10},
            {"id": "late_availability", "stream": "s", "event_ns": 4, "available_ns": 11, "receipt_ns": 12},
            {"id": "late_receipt", "stream": "s", "event_ns": 6, "available_ns": 9, "receipt_ns": 11},
            {"id": "both_late", "stream": "s", "event_ns": 8, "available_ns": 11, "receipt_ns": 12},
            {"id": "missing", "stream": "s", "event_ns": 10, "available_ns": None, "receipt_ns": 8},
            {"id": "dup1", "stream": "s", "event_ns": 12, "available_ns": 8, "receipt_ns": 9},
            {"id": "dup2", "stream": "s", "event_ns": 13, "available_ns": 8, "receipt_ns": 9},
            {"id": "timely2", "stream": "s", "event_ns": 18, "available_ns": 7, "receipt_ns": 8},
            {"id": "outside", "stream": "s", "event_ns": 20, "available_ns": 8, "receipt_ns": 8},
            {"id": "no_event", "stream": "s", "event_ns": None, "available_ns": 8, "receipt_ns": 8},
        ],
    }
    result = analyze(payload)
    summary = result["summary"]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    assert result["schema"] == "brain-research-diagnostic.v1"
    assert result["kind"] == "expected_observation_timeliness"
    assert result["input_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert result == analyze(payload)
    assert summary["expected_slot_count"] == 10
    assert summary["usable_slot_count"] == 2
    assert summary["usable_fraction"] == 0.2
    assert summary["absent_slot_count"] == 3
    assert summary["duplicate_slot_count"] == 1
    assert summary["duplicate_observation_count"] == 1
    assert summary["unclassifiable_observation_count"] == 2
    assert summary["missing_timestamp_observation_count"] == 2
    assert summary["missing_available_ns_count"] == 1
    assert summary["late_availability_count"] == 2
    assert summary["late_receipt_count"] == 3
    assert [item["reason"] for item in result["details"]["unclassifiable_observations"]] == [
        "outside_declared_intervals", "missing_event_ns"
    ]


def test_zero_expected_support_has_null_fraction() -> None:
    result = analyze({"intervals": [], "observations": []})
    assert result["summary"]["expected_slot_count"] == 0
    assert result["summary"]["usable_fraction"] is None


@pytest.mark.parametrize("payload", [
    {"intervals": [{"id": "i", "stream": "s", "start_ns": True, "end_ns": 2,
                    "slot_width_ns": 1, "deadline_ns": 2}], "observations": []},
    {"intervals": [{"id": "i", "stream": "s", "start_ns": 0, "end_ns": 2,
                    "slot_width_ns": 1, "deadline_ns": 2}],
     "observations": [{"id": "o", "stream": "s", "event_ns": 0,
                       "available_ns": float("nan"), "receipt_ns": 1}]},
    {"intervals": [], "observations": [
        {"id": "o", "stream": "s", "event_ns": 0, "available_ns": None, "receipt_ns": None},
        {"id": "o", "stream": "s", "event_ns": 0, "available_ns": None, "receipt_ns": None},
    ]},
])
def test_rejects_malformed_nonfinite_boolean_and_duplicate_identities(payload: dict) -> None:
    with pytest.raises(ValueError):
        analyze(payload)


def test_cli_json_success_and_duplicate_key_failure() -> None:
    payload = {"intervals": [], "observations": []}
    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.expected_observation_timeliness"],
        input=json.dumps(payload), text=True, capture_output=True, check=True,
    )
    assert json.loads(completed.stdout) == analyze(payload)
    invalid = subprocess.run(
        [sys.executable, "-m", "memecoin_experience.expected_observation_timeliness"],
        input='{"intervals":[],"intervals":[],"observations":[]}', text=True, capture_output=True,
    )
    assert invalid.returncode == 2
