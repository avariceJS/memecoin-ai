from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from memecoin_experience.provider_roster_coverage import analyze


def row(provider, identity, observation_id):
    return {"provider": provider, "id": identity, "observation_id": observation_id}


def test_complete_and_deterministic_digest():
    payload = {"expected_ids": ["a", "b"], "providers": ["x", "y"],
               "observations": [row("x", "a", "1"), row("x", "b", "2"),
                               row("y", "a", "3"), row("y", "b", "4")]}
    result = analyze(payload)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    assert result["input_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert result == analyze(payload)
    assert result["summary"]["absent_provider_identity_count"] == 0
    assert result["summary"]["shared_absent_count"] == result["summary"]["provider_specific_absent_count"] == 0


def test_partitions_shared_and_provider_specific_absence_and_counts_duplicates():
    payload = {"expected_ids": ["shared", "only_x", "present"], "providers": ["x", "y"],
               "observations": [row("x", "only_x", "1"), row("x", "present", "2"),
                               row("x", "present", "3"), row("y", "present", "4")]}
    result = analyze(payload)
    summary = result["summary"]
    assert summary["shared_absent_count"] == 1
    assert summary["provider_specific_absent_count"] == 1
    assert summary["absent_provider_identity_count"] == 3
    assert summary["provider_identity_denominator"] == 6
    assert result["details"]["shared_absent_ids"] == ["shared"]
    assert result["details"]["provider_specific_absent_ids"] == ["only_x"]
    assert result["details"]["providers"][0]["duplicate_ids"] == ["present"]


def test_empty_denominators_are_null_rates():
    result = analyze({"expected_ids": [], "providers": [], "observations": []})
    assert result["summary"]["shared_absent_rate"] is None
    assert result["summary"]["provider_specific_absent_rate"] is None


@pytest.mark.parametrize("payload", [
    {"expected_ids": ["a", "a"], "providers": [], "observations": []},
    {"expected_ids": [""], "providers": [], "observations": []},
    {"expected_ids": [], "providers": ["x", "x"], "observations": []},
    {"expected_ids": [], "providers": [], "observations": [row("x", "a", "1")]},
    {"expected_ids": ["a"], "providers": ["x"], "observations": [row("x", "b", "1")]},
    {"expected_ids": ["a"], "providers": ["x"], "observations": [row("x", "a", "1"), row("x", "a", "1")]},
    {"expected_ids": ["a"], "providers": ["x"], "observations": [{"provider": "x", "id": "a"}]},
])
def test_rejects_malformed_unknown_and_duplicate_observations(payload):
    with pytest.raises(ValueError):
        analyze(payload)


def test_cli_success_and_failure():
    payload = {"expected_ids": ["a"], "providers": ["x"], "observations": [row("x", "a", "o")]}
    result = subprocess.run([sys.executable, "-m", "memecoin_experience.provider_roster_coverage"],
                            input=json.dumps(payload), text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == analyze(payload)
    assert result.stderr == ""
    invalid = subprocess.run([sys.executable, "-m", "memecoin_experience.provider_roster_coverage"],
                             input='{"expected_ids": [], "providers": [], "observations": [], "extra": 1}',
                             text=True, capture_output=True)
    assert invalid.returncode == 2
    assert invalid.stdout == ""
