from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from memecoin_experience.expected_slot_coverage import analyze


def interval(identifier="i", stream="s", start=0, end=10, width=2):
    return {"id": identifier, "stream": stream, "start_ns": start, "end_ns": end, "slot_width_ns": width}


def observation(identifier, event, stream="s"):
    return {"id": identifier, "stream": stream, "event_ns": event}


def test_complete_slots_and_deterministic_digest_output():
    payload = {"intervals": [interval()], "observations": [observation(str(i), i * 2) for i in range(5)]}
    result = analyze(payload)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    assert result["input_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert result == analyze(payload)
    assert result["summary"]["expected_slot_count"] == result["summary"]["observed_slot_count"] == 5
    assert result["details"]["intervals"][0]["missing_slot_runs"] == []


def test_absent_and_duplicate_slots_report_maximal_gap_endpoints():
    result = analyze({"intervals": [interval(end=12)], "observations": [observation("a", 0), observation("b", 1), observation("c", 10)]})
    detail = result["details"]["intervals"][0]
    assert detail["expected_count"] == 6
    assert detail["observed_count"] == 2
    assert detail["duplicate_observation_count"] == 1
    assert detail["duplicate_observations"] == [{"id": "b", "slot_index": 0, "event_ns": 1}]
    assert detail["missing_slot_runs"] == [{"first_slot_index": 1, "last_slot_index": 4, "start_ns": 2, "end_ns": 8, "slot_count": 4}]


def test_out_of_range_missing_time_empty_support_and_zero_slots_are_distinct():
    result = analyze({"intervals": [interval(start=3, end=4, width=9)], "observations": [observation("outside", 4), observation("unknown", None)]})
    assert result["summary"]["expected_slot_count"] == 1
    assert result["summary"]["unmatched_observation_count"] == 1
    assert result["summary"]["missing_event_time_observation_count"] == 1
    assert analyze({"intervals": [], "observations": []})["summary"]["has_declared_expected_intervals"] is False
    assert analyze({"intervals": [], "observations": []})["summary"]["has_zero_expected_slots"] is True


@pytest.mark.parametrize("intervals", [
    [interval(), interval("j", start=8, end=12)],
    [interval(start=2, end=2)],
    [interval(width=0)],
    [interval(start=True)],
])
def test_rejects_overlapping_malformed_and_invalid_intervals(intervals):
    with pytest.raises(ValueError):
        analyze({"intervals": intervals, "observations": []})


def test_rejects_duplicate_identities_and_invalid_event_times():
    with pytest.raises(ValueError, match="duplicate_interval_id"):
        analyze({"intervals": [interval(), interval()], "observations": []})
    with pytest.raises(ValueError, match="duplicate_observation_id"):
        analyze({"intervals": [], "observations": [observation("x", None), observation("x", 1)]})
    with pytest.raises(ValueError, match="event_ns"):
        analyze({"intervals": [interval()], "observations": [observation("x", True)]})


def test_cli_json_round_trip_and_invalid_input_exit_code():
    payload = {"intervals": [interval()], "observations": [observation("x", 0)]}
    result = subprocess.run([sys.executable, "-m", "memecoin_experience.expected_slot_coverage"], input=json.dumps(payload), text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == analyze(payload)
    assert result.stderr == ""
    invalid = subprocess.run([sys.executable, "-m", "memecoin_experience.expected_slot_coverage"], input='{"intervals":[],"observations":[],"extra":0}', text=True, capture_output=True)
    assert invalid.returncode == 2
