"""Continuous geometric coverage within caller-declared observation windows.

All coordinates are integer nanoseconds and intervals are half-open. This
module reports only the overlap between declared windows and observed spans;
it does not infer schedules, availability, or causes of missing coverage.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from typing import Any


_PREFIX = "observation_gap_intervals_"
_MAX_ENDPOINT = 1_000_000_000_000_000_000
_MAX_ROWS = 4096
_MAX_STREAMS = 256
_MAX_BYTES = 1_048_576
_PAYLOAD_KEYS = {"windows", "observations"}
_ROW_KEYS = {"id", "stream", "start_ns", "end_ns"}
_IDENTITY = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z", re.ASCII)
_LIMITATIONS = [
    "Coverage is geometric overlap of caller-declared intervals only.",
    "No schedule, interpolation, feed health, availability or missingness cause is inferred.",
    "Out-of-window observations do not establish coverage of undeclared time.",
]


def _fail(label: str) -> None:
    raise ValueError(f"{_PREFIX}{label}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    _fail("nonfinite_json")


def _canonical_size(payload: dict[str, Any]) -> None:
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, RecursionError, OverflowError):
        _fail("json_shape")
    if len(encoded) > _MAX_BYTES:
        _fail("canonical_size")


def _parse_rows(value: Any, label: str, streams: set[str]) -> tuple[list[tuple[str, int, int]], int]:
    if type(value) is not list:
        _fail(label + "_array")
    parsed: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for row in value:
        if type(row) is not dict or set(row) != _ROW_KEYS:
            _fail(label + "_row")
        identity, stream = row["id"], row["stream"]
        if type(identity) is not str or _IDENTITY.fullmatch(identity) is None:
            _fail(label + "_id")
        if identity in seen:
            _fail("duplicate_" + label + "_id")
        seen.add(identity)
        if type(stream) is not str or _IDENTITY.fullmatch(stream) is None:
            _fail("stream")
        start, end = row["start_ns"], row["end_ns"]
        if type(start) is not int or type(end) is not int:
            _fail("timestamp_type")
        if not (0 <= start < end <= _MAX_ENDPOINT):
            _fail("timestamp_range")
        streams.add(stream)
        parsed.append((stream, start, end))
    return parsed, len(value)


def _union(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            previous_start, previous_end = result[-1]
            if end > previous_end:
                result[-1] = (previous_start, end)
        else:
            result.append((start, end))
    return result


def _intersect(expected: list[tuple[int, int]], observed: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    i = j = 0
    while i < len(expected) and j < len(observed):
        start = max(expected[i][0], observed[j][0])
        end = min(expected[i][1], observed[j][1])
        if start < end:
            result.append((start, end))
        if expected[i][1] <= observed[j][1]:
            i += 1
        else:
            j += 1
    return _union(result)


def _subtract(expected: list[tuple[int, int]], covered: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    j = 0
    for start, end in expected:
        cursor = start
        while j < len(covered) and covered[j][1] <= cursor:
            j += 1
        k = j
        while k < len(covered) and covered[k][0] < end:
            cover_start, cover_end = covered[k]
            if cover_start > cursor:
                result.append((cursor, min(cover_start, end)))
            if cover_end > cursor:
                cursor = cover_end
            if cursor >= end:
                break
            k += 1
        if cursor < end:
            result.append((cursor, end))
        j = k
    return result


def _intervals_json(intervals: list[tuple[int, int]]) -> list[dict[str, int]]:
    return [{"start_ns": start, "end_ns": end} for start, end in intervals]


def _fraction(covered: int, expected: int) -> tuple[str, float | None]:
    if expected == 0:
        return "zero_expected_duration", None
    return "positive_expected_duration", covered / expected


def analyze(payload: dict) -> dict:
    """Return normalized expected, covered, and uncovered spans per stream."""
    if type(payload) is not dict or set(payload) != _PAYLOAD_KEYS:
        _fail("payload")
    windows, window_count = _parse_rows(payload["windows"], "window", set())
    observations, observation_count = _parse_rows(payload["observations"], "observation", set())
    if window_count + observation_count > _MAX_ROWS:
        _fail("row_limit")
    streams = {stream for stream, _, _ in windows}
    streams.update(stream for stream, _, _ in observations)
    if len(streams) > _MAX_STREAMS:
        _fail("stream_limit")
    _canonical_size(payload)

    by_window: dict[str, list[tuple[int, int]]] = defaultdict(list)
    by_observation: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for stream, start, end in windows:
        by_window[stream].append((start, end))
    for stream, start, end in observations:
        by_observation[stream].append((start, end))

    stream_results: list[dict[str, Any]] = []
    total_expected = total_covered = total_uncovered = 0
    for stream in sorted(streams):
        expected = _union(by_window[stream])
        observed = _union(by_observation[stream])
        covered = _intersect(expected, observed)
        uncovered = _subtract(expected, covered)
        expected_duration = sum(end - start for start, end in expected)
        covered_duration = sum(end - start for start, end in covered)
        uncovered_duration = sum(end - start for start, end in uncovered)
        if expected_duration != covered_duration + uncovered_duration or not 0 <= covered_duration <= expected_duration:
            raise AssertionError("observation_gap_intervals_invariant")
        status, fraction = _fraction(covered_duration, expected_duration)
        stream_results.append({
            "stream": stream,
            "expected_windows": _intervals_json(expected),
            "covered_intervals": _intervals_json(covered),
            "uncovered_intervals": _intervals_json(uncovered),
            "expected_duration_ns": expected_duration,
            "covered_duration_ns": covered_duration,
            "uncovered_duration_ns": uncovered_duration,
            "denominator_status": status,
            "covered_fraction": fraction,
        })
        total_expected += expected_duration
        total_covered += covered_duration
        total_uncovered += uncovered_duration

    status, fraction = _fraction(total_covered, total_expected)
    summary = {
        "stream_count": len(stream_results),
        "window_count": window_count,
        "observation_count": observation_count,
        "expected_duration_ns": total_expected,
        "covered_duration_ns": total_covered,
        "uncovered_duration_ns": total_uncovered,
        "denominator_status": status,
        "covered_fraction": fraction,
    }
    return {
        "schema": "brain-observation-gap-intervals.v1",
        "summary": summary,
        "streams": stream_results,
        "limitations": list(_LIMITATIONS),
    }


def main() -> int:
    try:
        if len(sys.argv) != 1:
            _fail("arguments")
        raw = sys.stdin.buffer.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            _fail("raw_size")
        text = raw.decode("utf-8", errors="strict")
        payload = json.loads(text, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
        output = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (ValueError, UnicodeError, json.JSONDecodeError, RecursionError, OverflowError) as exc:
        message = str(exc)
        if not message.startswith(_PREFIX):
            message = _PREFIX + "invalid_json"
        sys.stderr.write(message + "\n")
        return 2
    sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
