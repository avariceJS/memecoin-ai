from __future__ import annotations

import copy
import json
import os
import subprocess
import sys

import pytest

from memecoin_experience.observation_gap_intervals import analyze


LIMIT = 1_000_000_000_000_000_000


def row(identifier: str, start: int, end: int, stream: str = "s") -> dict:
    return {"id": identifier, "stream": stream, "start_ns": start, "end_ns": end}


def interval_pairs(intervals: list[dict]) -> list[tuple[int, int]]:
    return [(item["start_ns"], item["end_ns"]) for item in intervals]


def stream_result(result: dict, stream: str = "s") -> dict:
    return next(item for item in result["streams"] if item["stream"] == stream)


def test_hand_oracle_overlap_clipping_and_gap_subtraction():
    payload = {
        "windows": [row("w1", 0, 10), row("w2", 8, 15), row("w3", 20, 30)],
        "observations": [row("o1", 0, 2), row("o2", 5, 12), row("o3", 14, 22), row("o4", 28, 35)],
    }
    detail = stream_result(analyze(payload))
    assert interval_pairs(detail["expected_windows"]) == [(0, 15), (20, 30)]
    assert interval_pairs(detail["covered_intervals"]) == [(0, 2), (5, 12), (14, 15), (20, 22), (28, 30)]
    assert interval_pairs(detail["uncovered_intervals"]) == [(2, 5), (12, 14), (22, 28)]
    assert (detail["expected_duration_ns"], detail["covered_duration_ns"], detail["uncovered_duration_ns"]) == (25, 14, 11)
    assert detail["covered_fraction"] == 14 / 25


def test_touching_nested_endpoint_only_and_outside_observations():
    result = analyze({
        "windows": [row("w1", 0, 10), row("w2", 10, 20), row("w3", 3, 7)],
        "observations": [row("o1", 0, 5), row("o2", 5, 20), row("o3", 8, 9)],
    })
    detail = stream_result(result)
    assert interval_pairs(detail["expected_windows"]) == [(0, 20)]
    assert interval_pairs(detail["covered_intervals"]) == [(0, 20)]
    assert detail["uncovered_intervals"] == []
    endpoint_only = analyze({"windows": [row("w", 10, 20)], "observations": [row("a", 0, 10), row("b", 20, 30)]})
    assert interval_pairs(stream_result(endpoint_only)["uncovered_intervals"]) == [(10, 20)]
    outside = analyze({"windows": [row("w", 2, 4)], "observations": [row("a", 0, 2), row("b", 4, 9)]})
    assert interval_pairs(stream_result(outside)["covered_intervals"]) == []


def test_empty_observations_disjoint_windows_and_empty_input():
    result = analyze({"windows": [row("a", 1, 3), row("b", 8, 10)], "observations": []})
    assert interval_pairs(stream_result(result)["uncovered_intervals"]) == [(1, 3), (8, 10)]
    assert analyze({"windows": [], "observations": []}) == {
        "schema": "brain-observation-gap-intervals.v1",
        "summary": {
            "stream_count": 0, "window_count": 0, "observation_count": 0,
            "expected_duration_ns": 0, "covered_duration_ns": 0, "uncovered_duration_ns": 0,
            "denominator_status": "zero_expected_duration", "covered_fraction": None,
        },
        "streams": [],
        "limitations": [
            "Coverage is geometric overlap of caller-declared intervals only.",
            "No schedule, interpolation, feed health, availability or missingness cause is inferred.",
            "Out-of-window observations do not establish coverage of undeclared time.",
        ],
    }


def test_stream_denominators_observation_only_stream_and_raw_counts():
    result = analyze({
        "windows": [row("w1", 0, 2, "z"), row("w2", 0, 8, "a")],
        "observations": [row("o1", 0, 2, "z"), row("extra", 5, 6, "only")],
    })
    assert [item["stream"] for item in result["streams"]] == ["a", "only", "z"]
    assert result["summary"] == {
        "stream_count": 3, "window_count": 2, "observation_count": 2,
        "expected_duration_ns": 10, "covered_duration_ns": 2, "uncovered_duration_ns": 8,
        "denominator_status": "positive_expected_duration", "covered_fraction": 0.2,
    }
    only = stream_result(result, "only")
    assert only["expected_windows"] == only["covered_intervals"] == only["uncovered_intervals"] == []
    assert only["denominator_status"] == "zero_expected_duration" and only["covered_fraction"] is None


def test_boundary_identical_geometry_and_derived_durations_are_exact():
    result = analyze({
        "windows": [row("w1", LIMIT - 1, LIMIT, "a"), row("w2", 0, LIMIT, "b"), row("w3", 0, LIMIT, "c")],
        "observations": [row("o1", LIMIT - 1, LIMIT, "a"), row("o2", 0, LIMIT, "b"), row("o3", 0, LIMIT, "c")],
    })
    assert result["summary"]["expected_duration_ns"] == 2 * LIMIT + 1
    assert result["summary"]["covered_duration_ns"] == 2 * LIMIT + 1
    assert result["summary"]["covered_fraction"] == 1.0
    duplicate_geometry = analyze({"windows": [row("a", 0, 10), row("b", 0, 10)], "observations": [row("x", 0, 10), row("y", 0, 10)]})
    assert stream_result(duplicate_geometry)["expected_duration_ns"] == 10
    assert stream_result(duplicate_geometry)["covered_duration_ns"] == 10


def _runs(cells: set[int]) -> list[tuple[int, int]]:
    result = []
    ordered = sorted(cells)
    for cell in ordered:
        if not result or cell != result[-1][1]:
            result.append((cell, cell + 1))
        else:
            result[-1] = (result[-1][0], cell + 1)
    return result


@pytest.mark.parametrize("window_spans,observation_spans", [
    ([(0, 8), (5, 12)], [(1, 4), (3, 7), (10, 14)]),
    ([(0, 4), (4, 9), (2, 3)], [(0, 2), (2, 8)]),
    ([(2, 6), (10, 15)], [(0, 3), (5, 11), (13, 20)]),
    ([(0, 24)], [(3, 5), (5, 6), (8, 17), (21, 24)]),
    ([(1, 2), (4, 8), (7, 10)], [(1, 10)]),
])
def test_independent_integer_cell_oracle(window_spans, observation_spans):
    payload = {
        "windows": [row(f"w{i}", start, end) for i, (start, end) in enumerate(window_spans)],
        "observations": [row(f"o{i}", start, end) for i, (start, end) in enumerate(observation_spans)],
    }
    expected_cells = {cell for start, end in window_spans for cell in range(start, end)}
    observed_cells = {cell for start, end in observation_spans for cell in range(start, end)}
    covered_cells = expected_cells & observed_cells
    uncovered_cells = expected_cells - observed_cells
    detail = stream_result(analyze(payload))
    assert interval_pairs(detail["expected_windows"]) == _runs(expected_cells)
    assert interval_pairs(detail["covered_intervals"]) == _runs(covered_cells)
    assert interval_pairs(detail["uncovered_intervals"]) == _runs(uncovered_cells)
    assert detail["expected_duration_ns"] == len(expected_cells)
    assert detail["covered_duration_ns"] == len(covered_cells)
    assert detail["uncovered_duration_ns"] == len(uncovered_cells)


def test_permutation_translation_idempotence_and_fresh_results():
    payload = {"windows": [row("w1", 0, 10), row("w2", 8, 12)], "observations": [row("o1", 2, 5), row("o2", 4, 9)]}
    before = copy.deepcopy(payload)
    result = analyze(payload)
    assert payload == before
    assert analyze({"windows": list(reversed(payload["windows"])), "observations": list(reversed(payload["observations"]))}) == result
    repeated = analyze({"windows": payload["windows"] + [row("w3", 0, 10)], "observations": payload["observations"] + [row("o3", 2, 5)]})
    assert repeated["streams"] == result["streams"]
    assert repeated["summary"]["expected_duration_ns"] == result["summary"]["expected_duration_ns"]
    assert repeated["summary"]["covered_duration_ns"] == result["summary"]["covered_duration_ns"]
    assert repeated["summary"]["window_count"] == result["summary"]["window_count"] + 1
    shift = 100
    moved = {key: [{**item, "start_ns": item["start_ns"] + shift, "end_ns": item["end_ns"] + shift} for item in payload[key]] for key in payload}
    translated = analyze(moved)
    for key in ("expected_windows", "covered_intervals", "uncovered_intervals"):
        assert interval_pairs(stream_result(translated)[key]) == [(a + shift, b + shift) for a, b in interval_pairs(stream_result(result)[key])]
    assert translated["summary"]["expected_duration_ns"] == result["summary"]["expected_duration_ns"]
    result["streams"][0]["uncovered_intervals"].append({"start_ns": 50, "end_ns": 51})
    assert analyze(payload)["streams"][0]["uncovered_intervals"] != result["streams"][0]["uncovered_intervals"]


@pytest.mark.parametrize("payload", [
    None,
    {"windows": [], "observations": [], "extra": 1},
    {"windows": []},
    {"windows": {}, "observations": []},
    {"windows": [{"id": "i", "stream": "s", "start_ns": 0, "end_ns": 1, "extra": 1}], "observations": []},
    {"windows": [row("x", 0, 1), row("x", 1, 2, "other")], "observations": []},
    {"windows": [], "observations": [row("x", 0, 1), row("x", 1, 2, "other")]},
    {"windows": [row("", 0, 1)], "observations": []},
    {"windows": [row("é", 0, 1)], "observations": []},
    {"windows": [row("x" * 129, 0, 1)], "observations": []},
    {"windows": [row("i", True, 1)], "observations": []},
    {"windows": [row("i", 0.0, 1)], "observations": []},
    {"windows": [row("i", 0, None)], "observations": []},
    {"windows": [row("i", -1, 1)], "observations": []},
    {"windows": [row("i", 1, 1)], "observations": []},
    {"windows": [row("i", 2, 1)], "observations": []},
    {"windows": [row("i", 0, LIMIT + 1)], "observations": []},
])
def test_api_rejects_malformed_input_with_prefix(payload):
    with pytest.raises(ValueError, match=r"^observation_gap_intervals_"):
        analyze(payload)


def test_api_row_stream_and_canonical_byte_limits():
    too_many_rows = {"windows": [row(f"w{i}", i, i + 1) for i in range(2049)], "observations": [row(f"o{i}", i, i + 1) for i in range(2048)]}
    with pytest.raises(ValueError, match="row_limit"):
        analyze(too_many_rows)
    too_many_streams = {"windows": [row(f"w{i}", 0, 1, f"s{i}") for i in range(257)], "observations": []}
    with pytest.raises(ValueError, match="stream_limit"):
        analyze(too_many_streams)
    large = {"windows": [row(f"{i:04x}" + "i" * 124, 0, 1, "s" * 128) for i in range(4096)], "observations": []}
    with pytest.raises(ValueError, match="canonical_size"):
        analyze(large)


def _cli(data: bytes, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, ["src", env.get("PYTHONPATH", "")]))
    return subprocess.run([sys.executable, "-m", "memecoin_experience.observation_gap_intervals", *args], input=data, capture_output=True, env=env)


def test_cli_success_bytes_parity_and_raw_byte_limit():
    payload = {"windows": [row("w", 0, 3)], "observations": [row("o", 1, 2)]}
    data = json.dumps(payload).encode()
    result = _cli(data)
    assert result.returncode == 0 and result.stderr == b""
    assert result.stdout.endswith(b"\n") and result.stdout.count(b"\n") == 1
    assert json.loads(result.stdout) == analyze(payload)
    expected_bytes = json.dumps(analyze(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode() + b"\n"
    assert result.stdout == expected_bytes
    at_limit = data + b" " * (1_048_576 - len(data))
    assert len(at_limit) == 1_048_576 and _cli(at_limit).returncode == 0
    too_large = _cli(at_limit + b" ")
    assert too_large.returncode == 2 and too_large.stdout == b""
    assert too_large.stderr.startswith(b"observation_gap_intervals_") and not too_large.stderr.startswith(b"Traceback")


@pytest.mark.parametrize("data,args", [
    (b"{", ()),
    (b"{} {}", ()),
    (b'{"windows":[],"windows":[],"observations":[]}', ()),
    (b'{"windows":[{"id":"i","id":"j","stream":"s","start_ns":0,"end_ns":1}],"observations":[]}', ()),
    (b'{"windows":[],"observations":[],"x":NaN}', ()),
    (b"\xff", ()),
    (b'{"windows":[],"observations":[]}', ("--bad",)),
])
def test_cli_invalid_input_is_a_single_prefixed_diagnostic(data, args):
    result = _cli(data, *args)
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr.startswith(b"observation_gap_intervals_")
    assert result.stderr.count(b"\n") == 1
    assert b"Traceback" not in result.stderr
