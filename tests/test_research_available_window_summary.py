from __future__ import annotations

import copy
import json
import math
import random
import subprocess
import sys
from fractions import Fraction

import pytest

from memecoin_experience.available_window_summary import analyze


def hand_payload() -> dict:
    return {
        "decision_ns": 100,
        "observations": [
            {"id": "a", "event_ns": 80, "available_ns": 80, "value": 8},
            {"id": "b", "event_ns": 85, "available_ns": 85, "value": None},
            {"id": "c", "event_ns": 89, "available_ns": 89, "value": -2},
            {"id": "d", "event_ns": 90, "available_ns": 100, "value": 2},
            {"id": "e", "event_ns": 94, "available_ns": 95, "value": 6},
            {"id": "f", "event_ns": 95, "available_ns": 101, "value": 10},
            {"id": "g", "event_ns": 99, "available_ns": 99, "value": 4},
            {"id": "h", "event_ns": 100, "available_ns": 100, "value": 7},
            {"id": "i", "event_ns": 101, "available_ns": 101, "value": 9},
        ],
        "windows": [
            {"id": "recent", "older_ns": 10, "newer_ns": 0},
            {"id": "middle", "older_ns": 20, "newer_ns": 10},
            {"id": "overlap", "older_ns": 15, "newer_ns": 5},
            {"id": "empty", "older_ns": 40, "newer_ns": 30},
        ],
    }


def expected_hand_output() -> dict:
    return {
        "schema": "brain-available-window-summary.v1",
        "decision_ns": 100,
        "observation_count": 9,
        "windows": [
            {"id": "empty", "start_ns": 60, "end_ns": 70, "count": 0, "missing_count": 0,
             "mean": None, "min": None, "max": None, "missing": True},
            {"id": "middle", "start_ns": 80, "end_ns": 90, "count": 2, "missing_count": 1,
             "mean": 3.0, "min": -2.0, "max": 8.0, "missing": False},
            {"id": "overlap", "start_ns": 85, "end_ns": 95, "count": 3, "missing_count": 1,
             "mean": 2.0, "min": -2.0, "max": 6.0, "missing": False},
            {"id": "recent", "start_ns": 90, "end_ns": 100, "count": 3, "missing_count": 0,
             "mean": 4.0, "min": 2.0, "max": 6.0, "missing": False},
        ],
    }


def test_hand_computed_contract_fixture_and_no_mutation() -> None:
    payload = hand_payload()
    original = copy.deepcopy(payload)
    assert analyze(payload) == expected_hand_output()
    assert payload == original


def test_decision_zero_keeps_event_zero_outside_negative_start_windows() -> None:
    payload = {
        "decision_ns": 0,
        "observations": [
            {"id": "null", "event_ns": 0, "available_ns": 0, "value": None},
            {"id": "zero", "event_ns": 0, "available_ns": 0, "value": 0},
            {"id": "negative", "event_ns": 0, "available_ns": 0, "value": -3},
        ],
        "windows": [
            {"id": "older", "older_ns": 2, "newer_ns": 1},
            {"id": "right-edge-zero", "older_ns": 1, "newer_ns": 0},
        ],
    }
    assert analyze(payload)["windows"] == [
        {"id": "older", "start_ns": -2, "end_ns": -1, "count": 0, "missing_count": 0,
         "mean": None, "min": None, "max": None, "missing": True},
        {"id": "right-edge-zero", "start_ns": -1, "end_ns": 0, "count": 0, "missing_count": 0,
         "mean": None, "min": None, "max": None, "missing": True},
    ]


def test_d1_includes_zero_negative_values_and_distinguishes_null_from_empty() -> None:
    payload = {
        "decision_ns": 1,
        "observations": [
            {"id": "null", "event_ns": 0, "available_ns": 0, "value": None},
            {"id": "zero", "event_ns": 0, "available_ns": 0, "value": 0},
            {"id": "negative", "event_ns": 0, "available_ns": 0, "value": -3},
        ],
        "windows": [{"id": "supported", "older_ns": 1, "newer_ns": 0}],
    }
    result = analyze(payload)["windows"][0]
    assert result == {"id": "supported", "start_ns": 0, "end_ns": 1, "count": 2,
                      "missing_count": 1, "mean": -1.5, "min": -3.0, "max": 0.0,
                      "missing": False}

    null_only = copy.deepcopy(payload)
    null_only["observations"] = [payload["observations"][0]]
    null_result = analyze(null_only)["windows"][0]
    assert null_result["count"] == 0 and null_result["missing_count"] == 1
    assert null_result["missing"] is True

    empty = copy.deepcopy(payload)
    empty["observations"] = []
    empty_result = analyze(empty)["windows"][0]
    assert empty_result["count"] == 0 and empty_result["missing_count"] == 0
    assert empty_result["missing"] is True


def test_availability_boundary_delayed_rows_and_half_open_event_boundary() -> None:
    base = {
        "decision_ns": 10,
        "observations": [
            {"id": "left", "event_ns": 5, "available_ns": 10, "value": 5},
            {"id": "right", "event_ns": 10, "available_ns": 10, "value": 10},
            {"id": "late", "event_ns": 6, "available_ns": 11, "value": 6},
            {"id": "late-event", "event_ns": 11, "available_ns": 11, "value": 11},
        ],
        "windows": [{"id": "w", "older_ns": 5, "newer_ns": 0}],
    }
    at_ten = analyze(base)["windows"][0]
    assert (at_ten["count"], at_ten["mean"], at_ten["start_ns"], at_ten["end_ns"]) == (1, 5.0, 5, 10)
    advanced = copy.deepcopy(base)
    advanced["decision_ns"] = 11
    at_eleven = analyze(advanced)["windows"][0]
    assert (at_eleven["count"], at_eleven["mean"], at_eleven["start_ns"], at_eleven["end_ns"]) == (2, 8.0, 6, 11)


def test_overlapping_identical_windows_ties_and_permutation_invariance() -> None:
    payload = {
        "decision_ns": 5,
        "observations": [
            {"id": "z", "event_ns": 4, "available_ns": 4, "value": 1},
            {"id": "a", "event_ns": 4, "available_ns": 4, "value": 2},
            {"id": "m", "event_ns": 3, "available_ns": 5, "value": 3},
        ],
        "windows": [
            {"id": "same-b", "older_ns": 3, "newer_ns": 1},
            {"id": "same-a", "older_ns": 3, "newer_ns": 1},
            {"id": "overlap", "older_ns": 4, "newer_ns": 1},
            {"id": "ties", "older_ns": 3, "newer_ns": 0},
        ],
    }
    expected = analyze(payload)
    assert [row["id"] for row in expected["windows"]] == ["overlap", "same-a", "same-b", "ties"]
    assert all(row["count"] == 1 for row in expected["windows"][:3])
    assert all(row["mean"] == 3.0 for row in expected["windows"][:3])
    assert expected["windows"][3]["count"] == 3
    assert expected["windows"][3]["mean"] == 2.0
    reversed_payload = {**payload, "observations": list(reversed(payload["observations"])),
                        "windows": list(reversed(payload["windows"]))}
    assert analyze(reversed_payload) == expected
    randomizer = random.Random(338)
    for _ in range(12):
        shuffled = copy.deepcopy(payload)
        randomizer.shuffle(shuffled["observations"])
        randomizer.shuffle(shuffled["windows"])
        assert analyze(shuffled) == expected


def test_valid_future_unavailable_and_outside_window_rows_do_not_change_windows() -> None:
    payload = {"decision_ns": 10,
               "observations": [{"id": "inside", "event_ns": 7, "available_ns": 8, "value": 4}],
               "windows": [{"id": "w", "older_ns": 5, "newer_ns": 0}]}
    expected = analyze(payload)["windows"]
    extended = copy.deepcopy(payload)
    extended["observations"].extend([
        {"id": "future", "event_ns": 11, "available_ns": 11, "value": 99},
        {"id": "unavailable", "event_ns": 8, "available_ns": 11, "value": 99},
        {"id": "before", "event_ns": 4, "available_ns": 4, "value": 99},
    ])
    result = analyze(extended)
    assert result["windows"] == expected
    assert result["observation_count"] == 4


def scan_oracle(payload: dict) -> dict:
    """Deliberately simple independent scan; exact arithmetic for integer fixtures."""
    decision = payload["decision_ns"]
    outputs = []
    for window in sorted(payload["windows"], key=lambda row: row["id"]):
        start = decision - window["older_ns"]
        end = decision - window["newer_ns"]
        support = [row for row in payload["observations"]
                   if start <= row["event_ns"] < end and row["available_ns"] <= decision]
        known = [row for row in support if row["value"] is not None]
        nums = [float(row["value"]) for row in known]
        exact_mean = Fraction(sum(row["value"] for row in known), len(known)) if known else None
        outputs.append({
            "id": window["id"], "start_ns": start, "end_ns": end,
            "count": len(known), "missing_count": len(support) - len(known),
            "mean": float(exact_mean) if exact_mean is not None else None,
            "min": min(nums) if nums else None, "max": max(nums) if nums else None,
            "missing": not known,
        })
    return {"schema": "brain-available-window-summary.v1", "decision_ns": decision,
            "observation_count": len(payload["observations"]), "windows": outputs}


def test_bounded_generated_fixtures_match_fraction_scan_oracle() -> None:
    randomizer = random.Random(20261030)
    for fixture in range(100):
        decision = randomizer.randrange(0, 16)
        observations = []
        for index in range(randomizer.randrange(0, 18)):
            event = randomizer.randrange(0, 21)
            available = randomizer.randrange(event, 22)
            observations.append({"id": f"r{index:02}", "event_ns": event,
                                 "available_ns": available,
                                 "value": None if randomizer.randrange(5) == 0 else randomizer.randrange(-20, 21)})
        windows = []
        for index in range(randomizer.randrange(1, 6)):
            newer = randomizer.randrange(0, 10)
            older = randomizer.randrange(newer + 1, 14)
            windows.append({"id": f"w{index:02}", "older_ns": older, "newer_ns": newer})
        payload = {"decision_ns": decision, "observations": observations, "windows": windows}
        got = analyze(payload)
        want = scan_oracle(payload)
        assert got["schema"] == want["schema"] and got["decision_ns"] == want["decision_ns"]
        assert got["observation_count"] == want["observation_count"]
        assert len(got["windows"]) == len(want["windows"]), fixture
        for actual, expected in zip(got["windows"], want["windows"]):
            for key in ("id", "start_ns", "end_ns", "count", "missing_count", "min", "max", "missing"):
                assert actual[key] == expected[key], (fixture, key)
            if expected["mean"] is None:
                assert actual["mean"] is None
            else:
                assert math.isclose(actual["mean"], expected["mean"], rel_tol=1e-12, abs_tol=1e-12)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(extra=True),
        lambda p: p.pop("windows"),
        lambda p: p.update(decision_ns=True),
        lambda p: p.update(decision_ns=-1),
        lambda p: p.update(decision_ns=2**63),
        lambda p: p.update(observations=()),
        lambda p: p.update(windows=[]),
        lambda p: p.update(windows=[{"id": "w", "older_ns": 1, "newer_ns": 0},
                                    {"id": "w", "older_ns": 2, "newer_ns": 0}]),
        lambda p: p["observations"].append({"id": "bad", "event_ns": 1,
                                           "available_ns": 1, "value": 0, "extra": 0}),
        lambda p: p["observations"].append({"id": "bad!", "event_ns": 1,
                                           "available_ns": 1, "value": 0}),
        lambda p: p["observations"].append({"id": "bad", "event_ns": True,
                                           "available_ns": 1, "value": 0}),
        lambda p: p["observations"].append({"id": "bad", "event_ns": 2,
                                           "available_ns": 1, "value": 0}),
        lambda p: p["observations"].append({"id": "bad", "event_ns": 1,
                                           "available_ns": 1, "value": 10**400}),
        lambda p: p["observations"].append({"id": "bad", "event_ns": 1,
                                           "available_ns": 1, "value": True}),
        lambda p: p["observations"].append({"id": "bad", "event_ns": 1,
                                           "available_ns": 1, "value": float("nan")}),
        lambda p: p["observations"].append({"id": "bad", "event_ns": 1,
                                           "available_ns": 1, "value": float("inf")}),
        lambda p: p.update(windows=[{"id": "w", "older_ns": True, "newer_ns": 0}]),
        lambda p: p.update(windows=[{"id": "w", "older_ns": 0, "newer_ns": 0}]),
        lambda p: p.update(windows=[{"id": "w", "older_ns": 2**63, "newer_ns": 0}]),
        lambda p: p.update(windows=[{"id": "w", "older_ns": 1, "newer_ns": 1}]),
    ],
)
def test_rejects_invalid_rows_and_shapes_even_when_ineligible(mutate) -> None:
    payload = {"decision_ns": 0, "observations": [],
               "windows": [{"id": "w", "older_ns": 1, "newer_ns": 0}]}
    mutate(payload)
    with pytest.raises(ValueError):
        analyze(payload)


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(decision_ns="0"),
    lambda p: p.update(observations=None),
    lambda p: p.update(windows=None),
    lambda p: p.update(observations=[None]),
    lambda p: p.update(observations=[{"id": "x", "event_ns": 0, "available_ns": 0}]),
    lambda p: p.update(observations=[{"id": "é", "event_ns": 0, "available_ns": 0, "value": 1}]),
    lambda p: p.update(observations=[{"id": "x" * 65, "event_ns": 0, "available_ns": 0, "value": 1}]),
    lambda p: p.update(observations=[{"id": "x", "event_ns": -1, "available_ns": 0, "value": 1}]),
    lambda p: p.update(observations=[{"id": "x", "event_ns": 0, "available_ns": "0", "value": 1}]),
    lambda p: p.update(observations=[{"id": "x", "event_ns": 0, "available_ns": 0, "value": "1"}]),
    lambda p: p.update(observations=[{"id": "x", "event_ns": 0, "available_ns": 0, "value": 10**12 + 1}]),
    lambda p: p.update(observations=[{"id": "x", "event_ns": 0, "available_ns": 0, "value": 1e12 + 1}]),
    lambda p: p.update(windows=[None]),
    lambda p: p.update(windows=[{"id": "w", "older_ns": 1}]),
    lambda p: p.update(windows=[{"id": "é", "older_ns": 1, "newer_ns": 0}]),
    lambda p: p.update(windows=[{"id": "w", "older_ns": 1, "newer_ns": 0, "extra": 0}]),
    lambda p: p.update(windows=[{"id": "w", "older_ns": 1.0, "newer_ns": 0}]),
    lambda p: p.update(windows=[{"id": "w", "older_ns": 1, "newer_ns": False}]),
])
def test_rejects_remaining_validation_categories(mutate) -> None:
    payload = {"decision_ns": 0, "observations": [],
               "windows": [{"id": "w", "older_ns": 1, "newer_ns": 0}]}
    mutate(payload)
    with pytest.raises(ValueError):
        analyze(payload)


def test_observation_and_window_id_namespaces_are_independent() -> None:
    payload = {"decision_ns": 1,
               "observations": [{"id": "same", "event_ns": 0, "available_ns": 0, "value": 1}],
               "windows": [{"id": "same", "older_ns": 2, "newer_ns": 0}]}
    assert analyze(payload)["windows"][0]["count"] == 1


def test_timestamp_integer_endpoints_and_duplicate_observation_ids() -> None:
    top = 2**63 - 1
    payload = {"decision_ns": top, "observations": [
        {"id": "x", "event_ns": top, "available_ns": top, "value": 10**12}],
        "windows": [{"id": "w", "older_ns": top, "newer_ns": top - 1}]}
    assert analyze(payload)["observation_count"] == 1
    duplicate = copy.deepcopy(payload)
    duplicate["observations"].append(copy.deepcopy(duplicate["observations"][0]))
    with pytest.raises(ValueError, match="duplicate_observation_id"):
        analyze(duplicate)


def test_declared_collection_limits_and_one_over() -> None:
    observation = {"id": "r", "event_ns": 0, "available_ns": 0, "value": 1}
    window = {"id": "w", "older_ns": 1, "newer_ns": 0}
    payload = {"decision_ns": 0, "observations": [dict(observation, id=f"r{i}") for i in range(10_000)],
               "windows": [dict(window, id=f"w{i}", newer_ns=0) for i in range(64)]}
    assert analyze(payload)["observation_count"] == 10_000
    too_many_observations = copy.deepcopy(payload)
    too_many_observations["observations"].append(dict(observation, id="extra"))
    with pytest.raises(ValueError):
        analyze(too_many_observations)
    too_many_windows = copy.deepcopy(payload)
    too_many_windows["windows"].append(dict(window, id="extra"))
    with pytest.raises(ValueError):
        analyze(too_many_windows)


def run_cli(data: bytes, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "memecoin_experience.available_window_summary", *args],
        input=data, capture_output=True,
    )


def test_real_cli_success_is_compact_and_matches_api() -> None:
    payload = hand_payload()
    completed = run_cli(json.dumps(payload).encode("utf-8"))
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == analyze(payload)
    assert completed.stdout.endswith(b"\n") and completed.stdout.count(b"\n") == 1
    assert completed.stderr == b""


@pytest.mark.parametrize("raw", [
    b'{"decision_ns":0,"decision_ns":0,"observations":[],"windows":[{"id":"w","older_ns":1,"newer_ns":0}]}',
    b'{"decision_ns":0,"observations":[{"id":"x","id":"x","event_ns":0,"available_ns":0,"value":1}],"windows":[{"id":"w","older_ns":1,"newer_ns":0}]}',
    b'{"decision_ns":0,"observations":[],"windows":[{"id":"w","older_ns":1,"newer_ns":0,"nested":{"x":1,"x":2}}]}',
    b'{"decision_ns":0,"observations":[],"windows":[{"id":"w","older_ns":1,"newer_ns":0}]} trailing',
    b'{"decision_ns":0,',
    b'{"decision_ns":0,"observations":[],"windows":[{"id":"w","older_ns":1,"newer_ns":0}],"extra":NaN}',
    b'{"decision_ns":0,"observations":[],"windows":[{"id":"w","older_ns":1,"newer_ns":0}],"extra":Infinity}',
    b'\xff',
    b'{"decision_ns":true,"observations":[],"windows":[{"id":"w","older_ns":1,"newer_ns":0}]}',
])
def test_real_cli_invalid_json_has_empty_stdout_and_one_diagnostic(raw: bytes) -> None:
    completed = run_cli(raw)
    assert completed.returncode == 2
    assert completed.stdout == b""
    assert completed.stderr.startswith(b"available_window_summary_invalid_input:")
    assert len(completed.stderr.splitlines()) == 1


def test_real_cli_rejects_file_arguments_and_byte_overflow() -> None:
    valid = json.dumps(hand_payload()).encode("utf-8")
    with_args = run_cli(valid, "input.json")
    assert with_args.returncode == 2 and with_args.stdout == b""
    assert with_args.stderr.startswith(b"available_window_summary_invalid_input:")
    too_large = run_cli(b" " * 1_048_577)
    assert too_large.returncode == 2 and too_large.stdout == b""
    assert too_large.stderr.startswith(b"available_window_summary_invalid_input:")


def test_real_cli_accepts_exact_byte_limit_before_json_parsing() -> None:
    payload = {"decision_ns": 0, "observations": [],
               "windows": [{"id": "w", "older_ns": 1, "newer_ns": 0}]}
    encoded = json.dumps(payload, separators=(",", ":")).encode("ascii")
    exactly_full = encoded + b" " * (1_048_576 - len(encoded))
    completed = run_cli(exactly_full)
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == analyze(payload)
    assert completed.stderr == b""
