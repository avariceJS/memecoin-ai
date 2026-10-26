"""Descriptive availability and receipt latency for explicit observation rows.

Input is exactly ``{"decision_ns": int, "rows": [...]}``; each row is exactly
``{id, stream, event_ns, available_ns, receipt_ns}``. Times are nonnegative
integers or null, and booleans are not integers for this contract. A timestamp
is missing when null; no timestamp is imputed from another field. ``future_event``
counts event times strictly after the decision, while ``not_yet_available``
counts non-null availability times strictly after it.

Event-to-availability delays use rows with both values present and
``event_ns <= available_ns``. Availability-to-receipt uses both values present
and ``available_ns <= receipt_ns``. Each pair that violates its ordering is
counted as invalid and excluded. Delay summaries report valid support and
nearest-rank p50/p95: sort ascending and select one-based rank ``ceil(p*n)``.
An empty valid support has null quantiles. Global summaries pool rows across
streams; stream summaries are computed independently. This is a descriptive
diagnostic of caller-supplied data, not a causal guarantee or a completeness
claim about any external stream.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import defaultdict
from typing import Any


_PAYLOAD_KEYS = {"decision_ns", "rows"}
_ROW_KEYS = {"id", "stream", "event_ns", "available_ns", "receipt_ns"}
_TIMES = ("event_ns", "available_ns", "receipt_ns")


def _integer(value: Any, label: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"availability_profile_{label}")
    return value


def _nearest_rank(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def _delay_summary(values: list[int], invalid: int) -> dict[str, int | None]:
    return {
        "valid_count": len(values),
        "invalid_order_count": invalid,
        "p50_nearest_rank": _nearest_rank(values, 0.50),
        "p95_nearest_rank": _nearest_rank(values, 0.95),
    }


def _new_counts() -> dict[str, Any]:
    return {
        "total": 0,
        "missing_timestamp_counts": {key: 0 for key in _TIMES},
        "future_event_count": 0,
        "not_yet_available_count": 0,
        "event_to_availability": {"values": [], "invalid": 0},
        "availability_to_receipt": {"values": [], "invalid": 0},
    }


def _finish(counts: dict[str, Any]) -> dict[str, Any]:
    return {
        "total": counts["total"],
        "missing_timestamp_counts": counts["missing_timestamp_counts"],
        "future_event_count": counts["future_event_count"],
        "not_yet_available_count": counts["not_yet_available_count"],
        "event_to_availability": _delay_summary(
            counts["event_to_availability"]["values"],
            counts["event_to_availability"]["invalid"],
        ),
        "availability_to_receipt": _delay_summary(
            counts["availability_to_receipt"]["values"],
            counts["availability_to_receipt"]["invalid"],
        ),
    }


def analyze(payload: dict) -> dict:
    """Return the frozen common diagnostic envelope for explicit input data."""
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise ValueError("availability_profile_payload")
    decision = _integer(payload["decision_ns"], "decision_ns")
    rows = payload["rows"]
    if not isinstance(rows, list):
        raise ValueError("availability_profile_rows")

    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    overall = _new_counts()
    streams: dict[str, dict[str, Any]] = defaultdict(_new_counts)
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            raise ValueError("availability_profile_row")
        identity, stream = row["id"], row["stream"]
        if not isinstance(identity, str) or not identity:
            raise ValueError("availability_profile_id")
        if identity in seen:
            raise ValueError("availability_profile_duplicate_id")
        seen.add(identity)
        if not isinstance(stream, str) or not stream:
            raise ValueError("availability_profile_stream")
        times = {key: _integer(row[key], key, nullable=True) for key in _TIMES}
        for group in (overall, streams[stream]):
            group["total"] += 1
            for key, value in times.items():
                if value is None:
                    group["missing_timestamp_counts"][key] += 1
            if times["event_ns"] is not None and times["event_ns"] > decision:
                group["future_event_count"] += 1
            if times["available_ns"] is not None and times["available_ns"] > decision:
                group["not_yet_available_count"] += 1
            event, available, receipt = (times[key] for key in _TIMES)
            if event is not None and available is not None:
                pair = group["event_to_availability"]
                if event <= available:
                    pair["values"].append(available - event)
                else:
                    pair["invalid"] += 1
            if available is not None and receipt is not None:
                pair = group["availability_to_receipt"]
                if available <= receipt:
                    pair["values"].append(receipt - available)
                else:
                    pair["invalid"] += 1

    detail = {name: _finish(streams[name]) for name in sorted(streams)}
    summary = _finish(overall)
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "availability_profile",
        "input_sha256": hashlib.sha256(canonical).hexdigest(),
        "summary": summary,
        "details": detail,
        "limitations": [
            "Describes only the explicitly supplied rows; it does not discover or validate stream completeness.",
            "Null timestamps remain missing, and invalidly ordered pairs are excluded from delay quantiles.",
            "Counts and delays are descriptive and do not establish why an observation was late.",
        ],
    }


def _reject_constant(_value: str) -> None:
    raise ValueError("availability_profile_nonfinite_json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("availability_profile_duplicate_key")
        result[key] = value
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        message = str(exc) or "availability_profile_invalid_input"
        print(message, file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
