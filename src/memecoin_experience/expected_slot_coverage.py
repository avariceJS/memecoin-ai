"""Coverage of caller-declared observation slots.

Input is exactly ``{"intervals": [...], "observations": [...]}``. Intervals
are ``{id, stream, start_ns, end_ns, slot_width_ns}`` and observations are
``{id, stream, event_ns}``; event_ns may be null. An interval supports the
half-open range [start_ns, end_ns), with slots at start_ns + k*slot_width_ns.
The final slot may be shorter than slot_width_ns. Each timestamp belongs to
the unique same-stream interval containing it. Duplicate observations of one
slot count once toward coverage and are also reported. Missing runs contain
consecutive slot indices and their inclusive timestamp endpoints.

This diagnostic describes only explicitly declared support. It does not infer
a schedule, explain missingness, or establish completeness outside intervals.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from typing import Any


_PAYLOAD_KEYS = {"intervals", "observations"}
_INTERVAL_KEYS = {"id", "stream", "start_ns", "end_ns", "slot_width_ns"}
_OBSERVATION_KEYS = {"id", "stream", "event_ns"}


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"expected_slot_coverage_{label}")
    return value


def _time(value: Any, label: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"expected_slot_coverage_{label}")
    return value


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _slot_count(interval: dict[str, Any]) -> int:
    span = interval["end_ns"] - interval["start_ns"]
    return (span + interval["slot_width_ns"] - 1) // interval["slot_width_ns"]


def _interval_detail(interval: dict[str, Any], slots: set[int], duplicates: list[dict[str, Any]]) -> dict[str, Any]:
    total = _slot_count(interval)
    runs: list[dict[str, int]] = []
    cursor = 0
    for observed_index in sorted(slots):
        if observed_index > cursor:
            first, last = cursor, observed_index - 1
            runs.append({"first_slot_index": first, "last_slot_index": last,
                         "start_ns": interval["start_ns"] + first * interval["slot_width_ns"],
                         "end_ns": interval["start_ns"] + last * interval["slot_width_ns"],
                         "slot_count": last - first + 1})
        cursor = observed_index + 1
    if cursor < total:
        last = total - 1
        runs.append({"first_slot_index": cursor, "last_slot_index": last,
                     "start_ns": interval["start_ns"] + cursor * interval["slot_width_ns"],
                     "end_ns": interval["start_ns"] + last * interval["slot_width_ns"],
                     "slot_count": last - cursor + 1})
    return {
        "id": interval["id"], "stream": interval["stream"],
        "start_ns": interval["start_ns"], "end_ns": interval["end_ns"],
        "slot_width_ns": interval["slot_width_ns"], "expected_count": total,
        "observed_count": len(slots), "missing_count": total - len(slots),
        "duplicate_observation_count": len(duplicates),
        "duplicate_observations": duplicates, "missing_slot_runs": runs,
    }


def analyze(payload: dict) -> dict:
    """Analyze explicit expected intervals and observed event timestamps."""
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise ValueError("expected_slot_coverage_payload")
    intervals, observations = payload["intervals"], payload["observations"]
    if not isinstance(intervals, list):
        raise ValueError("expected_slot_coverage_intervals")
    if not isinstance(observations, list):
        raise ValueError("expected_slot_coverage_observations")
    canonical = _canonical(payload)

    parsed: list[dict[str, Any]] = []
    interval_ids: set[str] = set()
    for raw in intervals:
        if not isinstance(raw, dict) or set(raw) != _INTERVAL_KEYS:
            raise ValueError("expected_slot_coverage_interval")
        identity = _identity(raw["id"], "interval_id")
        stream = _identity(raw["stream"], "stream")
        if identity in interval_ids:
            raise ValueError("expected_slot_coverage_duplicate_interval_id")
        interval_ids.add(identity)
        start = _time(raw["start_ns"], "start_ns")
        end = _time(raw["end_ns"], "end_ns")
        width = _time(raw["slot_width_ns"], "slot_width_ns")
        if start >= end or width == 0:
            raise ValueError("expected_slot_coverage_interval_range")
        parsed.append({"id": identity, "stream": stream, "start_ns": start, "end_ns": end, "slot_width_ns": width})
    by_stream: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for interval in parsed:
        by_stream[interval["stream"]].append(interval)
    for stream, group in by_stream.items():
        ordered = sorted(group, key=lambda item: (item["start_ns"], item["end_ns"], item["id"]))
        for previous, current in zip(ordered, ordered[1:]):
            if current["start_ns"] < previous["end_ns"]:
                raise ValueError("expected_slot_coverage_overlapping_intervals")

    slots: dict[str, set[int]] = {item["id"]: set() for item in parsed}
    duplicates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    missing_time: list[dict[str, str]] = []
    unmatched: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in observations:
        if not isinstance(raw, dict) or set(raw) != _OBSERVATION_KEYS:
            raise ValueError("expected_slot_coverage_observation")
        identity = _identity(raw["id"], "observation_id")
        stream = _identity(raw["stream"], "stream")
        if identity in seen:
            raise ValueError("expected_slot_coverage_duplicate_observation_id")
        seen.add(identity)
        event = _time(raw["event_ns"], "event_ns", nullable=True)
        if event is None:
            missing_time.append({"id": identity, "stream": stream})
            continue
        candidates = [item for item in by_stream.get(stream, []) if item["start_ns"] <= event < item["end_ns"]]
        if not candidates:
            unmatched.append({"id": identity, "stream": stream, "event_ns": event, "reason": "outside_declared_intervals"})
            continue
        if len(candidates) != 1:
            ambiguous.append({"id": identity, "stream": stream, "event_ns": event, "interval_ids": sorted(item["id"] for item in candidates)})
            continue
        interval = candidates[0]
        index = (event - interval["start_ns"]) // interval["slot_width_ns"]
        if index in slots[interval["id"]]:
            duplicates[interval["id"]].append({"id": identity, "slot_index": index, "event_ns": event})
        slots[interval["id"]].add(index)

    details = [_interval_detail(item, slots[item["id"]], duplicates[item["id"]]) for item in sorted(parsed, key=lambda item: (item["stream"], item["start_ns"], item["id"]))]
    expected = sum(item["expected_count"] for item in details)
    observed = sum(item["observed_count"] for item in details)
    summary = {
        "declared_interval_count": len(parsed),
        "has_declared_expected_intervals": bool(parsed),
        "expected_slot_count": expected,
        "has_zero_expected_slots": expected == 0,
        "observed_slot_count": observed,
        "missing_slot_count": expected - observed,
        "duplicate_observation_count": sum(item["duplicate_observation_count"] for item in details),
        "missing_event_time_observation_count": len(missing_time),
        "unmatched_observation_count": len(unmatched),
        "ambiguous_observation_count": len(ambiguous),
    }
    return {
        "schema": "brain-research-diagnostic.v1", "kind": "expected_slot_coverage",
        "input_sha256": hashlib.sha256(canonical).hexdigest(), "summary": summary,
        "details": {"intervals": details, "missing_event_time_observations": missing_time,
                    "unmatched_observations": unmatched, "ambiguous_observations": ambiguous},
        "limitations": [
            "Coverage is limited to caller-declared intervals and their slot widths; no schedule is inferred.",
            "Observations without event_ns cannot be assigned to an interval or slot.",
            "A reported complete interval does not establish completeness outside its declared support.",
        ],
    }


def _reject_constant(_value: str) -> None:
    raise ValueError("expected_slot_coverage_nonfinite_json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("expected_slot_coverage_duplicate_key")
        result[key] = value
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        print(str(exc) or "expected_slot_coverage_invalid_input", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
