"""Expected-slot usability by a caller-declared decision deadline.

Intervals support half-open [start_ns, end_ns) ranges with slots beginning at
start_ns + k*slot_width_ns; the last slot may be shorter. An observation maps
by stream and event_ns using the same rules as expected_slot_coverage. A slot
is usable only when exactly one observation maps to it and both available_ns
and receipt_ns are present and no later than the interval deadline. Absent and
duplicate slots are distinct. Late availability and receipt flags are counted
independently and may overlap. All results describe only explicit input data.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from typing import Any


_PAYLOAD_KEYS = {"intervals", "observations"}
_INTERVAL_KEYS = {"id", "stream", "start_ns", "end_ns", "slot_width_ns", "deadline_ns"}
_OBSERVATION_KEYS = {"id", "stream", "event_ns", "available_ns", "receipt_ns"}
_TIMESTAMPS = ("event_ns", "available_ns", "receipt_ns")


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"expected_observation_timeliness_{label}")
    return value


def _time(value: Any, label: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"expected_observation_timeliness_{label}")
    return value


def _slot_count(interval: dict[str, Any]) -> int:
    span = interval["end_ns"] - interval["start_ns"]
    return (span + interval["slot_width_ns"] - 1) // interval["slot_width_ns"]


def _empty_counts() -> dict[str, int]:
    return {
        "expected_slot_count": 0,
        "usable_slot_count": 0,
        "absent_slot_count": 0,
        "duplicate_slot_count": 0,
        "duplicate_observation_count": 0,
        "unclassifiable_observation_count": 0,
        "missing_timestamp_observation_count": 0,
        "missing_event_ns_count": 0,
        "missing_available_ns_count": 0,
        "missing_receipt_ns_count": 0,
        "late_availability_count": 0,
        "late_receipt_count": 0,
    }


def _result_counts(counts: dict[str, int]) -> dict[str, Any]:
    expected, usable = counts["expected_slot_count"], counts["usable_slot_count"]
    return {**counts, "usable_fraction": usable / expected if expected else None}


def analyze(payload: dict) -> dict:
    """Analyze explicit expected intervals and observations by deadline."""
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise ValueError("expected_observation_timeliness_payload")
    intervals, observations = payload["intervals"], payload["observations"]
    if not isinstance(intervals, list):
        raise ValueError("expected_observation_timeliness_intervals")
    if not isinstance(observations, list):
        raise ValueError("expected_observation_timeliness_observations")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")

    parsed: list[dict[str, Any]] = []
    interval_ids: set[str] = set()
    for raw in intervals:
        if not isinstance(raw, dict) or set(raw) != _INTERVAL_KEYS:
            raise ValueError("expected_observation_timeliness_interval")
        identifier = _identity(raw["id"], "interval_id")
        stream = _identity(raw["stream"], "stream")
        if identifier in interval_ids:
            raise ValueError("expected_observation_timeliness_duplicate_interval_id")
        interval_ids.add(identifier)
        start = _time(raw["start_ns"], "start_ns")
        end = _time(raw["end_ns"], "end_ns")
        width = _time(raw["slot_width_ns"], "slot_width_ns")
        deadline = _time(raw["deadline_ns"], "deadline_ns")
        if start >= end or width == 0:
            raise ValueError("expected_observation_timeliness_interval_range")
        parsed.append({"id": identifier, "stream": stream, "start_ns": start, "end_ns": end,
                       "slot_width_ns": width, "deadline_ns": deadline})

    by_stream: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for interval in parsed:
        by_stream[interval["stream"]].append(interval)
    for group in by_stream.values():
        ordered = sorted(group, key=lambda item: (item["start_ns"], item["end_ns"], item["id"]))
        for previous, current in zip(ordered, ordered[1:]):
            if current["start_ns"] < previous["end_ns"]:
                raise ValueError("expected_observation_timeliness_overlapping_intervals")

    mapped: dict[str, dict[int, list[dict[str, Any]]]] = {item["id"]: defaultdict(list) for item in parsed}
    counts = {item["id"]: _empty_counts() for item in parsed}
    global_counts = _empty_counts()
    seen: set[str] = set()
    unclassifiable: list[dict[str, Any]] = []
    for raw in observations:
        if not isinstance(raw, dict) or set(raw) != _OBSERVATION_KEYS:
            raise ValueError("expected_observation_timeliness_observation")
        identifier = _identity(raw["id"], "observation_id")
        stream = _identity(raw["stream"], "stream")
        if identifier in seen:
            raise ValueError("expected_observation_timeliness_duplicate_observation_id")
        seen.add(identifier)
        times = {key: _time(raw[key], key, nullable=True) for key in _TIMESTAMPS}
        observation = {"id": identifier, "stream": stream, **times}
        event = times["event_ns"]
        candidates = [] if event is None else [item for item in by_stream.get(stream, []) if item["start_ns"] <= event < item["end_ns"]]
        if len(candidates) != 1:
            reason = "missing_event_ns" if event is None else "outside_declared_intervals" if not candidates else "ambiguous_interval"
            unclassifiable.append({"id": identifier, "stream": stream, "reason": reason})
            for group in (global_counts,):
                group["unclassifiable_observation_count"] += 1
                group["missing_timestamp_observation_count"] += event is None or times["available_ns"] is None or times["receipt_ns"] is None
                group["missing_event_ns_count"] += event is None
                group["missing_available_ns_count"] += times["available_ns"] is None
                group["missing_receipt_ns_count"] += times["receipt_ns"] is None
            continue
        interval = candidates[0]
        index = (event - interval["start_ns"]) // interval["slot_width_ns"]
        mapped[interval["id"]][index].append(observation)
        for group in (counts[interval["id"]], global_counts):
            group["missing_timestamp_observation_count"] += any(times[key] is None for key in _TIMESTAMPS)
            group["missing_event_ns_count"] += times["event_ns"] is None
            group["missing_available_ns_count"] += times["available_ns"] is None
            group["missing_receipt_ns_count"] += times["receipt_ns"] is None
            group["late_availability_count"] += times["available_ns"] is not None and times["available_ns"] > interval["deadline_ns"]
            group["late_receipt_count"] += times["receipt_ns"] is not None and times["receipt_ns"] > interval["deadline_ns"]

    details = []
    for interval in sorted(parsed, key=lambda item: (item["stream"], item["start_ns"], item["id"])):
        total = _slot_count(interval)
        per = counts[interval["id"]]
        per["expected_slot_count"] = total
        for index in range(total):
            rows = mapped[interval["id"]].get(index, [])
            if not rows:
                per["absent_slot_count"] += 1
            elif len(rows) > 1:
                per["duplicate_slot_count"] += 1
                per["duplicate_observation_count"] += len(rows) - 1
            elif rows[0]["available_ns"] is not None and rows[0]["receipt_ns"] is not None and rows[0]["available_ns"] <= interval["deadline_ns"] and rows[0]["receipt_ns"] <= interval["deadline_ns"]:
                per["usable_slot_count"] += 1
        global_counts["expected_slot_count"] += total
        global_counts["absent_slot_count"] += per["absent_slot_count"]
        global_counts["duplicate_slot_count"] += per["duplicate_slot_count"]
        global_counts["duplicate_observation_count"] += per["duplicate_observation_count"]
        global_counts["usable_slot_count"] += per["usable_slot_count"]
        details.append({"id": interval["id"], "stream": interval["stream"], "start_ns": interval["start_ns"],
                        "end_ns": interval["end_ns"], "slot_width_ns": interval["slot_width_ns"],
                        "deadline_ns": interval["deadline_ns"], **_result_counts(per)})

    return {
        "schema": "brain-research-diagnostic.v1", "kind": "expected_observation_timeliness",
        "input_sha256": hashlib.sha256(canonical).hexdigest(),
        "summary": {"interval_count": len(parsed), **_result_counts(global_counts)},
        "details": {"intervals": details, "unclassifiable_observations": unclassifiable},
        "limitations": [
            "Results cover only caller-declared intervals and slots; no schedule or missing observation is inferred.",
            "A slot is usable only with one mapped observation and known availability and receipt timestamps by its deadline.",
            "Late availability and late receipt counts are independent and may overlap; diagnostic counts do not explain causes.",
        ],
    }


def _reject_constant(_value: str) -> None:
    raise ValueError("expected_observation_timeliness_nonfinite_json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("expected_observation_timeliness_duplicate_key")
        result[key] = value
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        print(str(exc) or "expected_observation_timeliness_invalid_input", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
