"""Select the latest revised observation knowable at each decision time.

Input is exactly ``{"observations": [...], "queries": [...]}``. Each
observation is exactly ``{key, event_ns, available_ns, revision, value,
withdrawn}``: ``key`` is a nonempty string, the three integer fields are
nonnegative plain integers, ``available_ns >= event_ns``, ``value`` is null
or a finite number with absolute value at most 1e12, and ``withdrawn`` is a
bool. The ``(key, event_ns, revision)`` identity is unique and availability
times cannot decrease across revisions of one event. Each query is exactly
``{id, key, decision_ns, max_age_ns}``, with nonempty string id/key and
nonnegative plain integer times; query ids are unique.

For each query, eligible rows have ``available_ns <= decision_ns`` and
``event_ns <= decision_ns``. The chosen row has greatest event time, then
greatest revision. Status precedence is absent, withdrawn, stale (age greater
than ``max_age_ns``), missing_value, available. A chosen withdrawal or null
value does not fall back to an earlier row; equality at the age bound is
eligible. Output is ``{"schema": "brain-revision-asof.v1", "results": [...],
"counts": {...}}`` in query order, with each result containing ``id``,
``status`` and the complete selected observation or null. Counts include all
five statuses and sum to the number of queries.

This is a deterministic synthetic-input diagnostic only. It does not establish
real-data quality or trading competence and has no runtime admission effects.
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any


_PAYLOAD_KEYS = {"observations", "queries"}
_OBSERVATION_KEYS = {"key", "event_ns", "available_ns", "revision", "value", "withdrawn"}
_QUERY_KEYS = {"id", "key", "decision_ns", "max_age_ns"}
_STATUSES = ("absent", "withdrawn", "stale", "missing_value", "available")
_MAX_ABS_VALUE = 1e12


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"revision_asof_{label}")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"revision_asof_{label}")
    return value


def _value(value: Any) -> int | float | None:
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ValueError("revision_asof_value")
    if type(value) is float and not math.isfinite(value):
        raise ValueError("revision_asof_value")
    if abs(value) > _MAX_ABS_VALUE:
        raise ValueError("revision_asof_value")
    return value


def analyze(payload: dict) -> dict:
    """Validate input and return each query's latest knowable observation."""
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise ValueError("revision_asof_payload")
    observations = payload["observations"]
    queries = payload["queries"]
    if not isinstance(observations, list):
        raise ValueError("revision_asof_observations")
    if not isinstance(queries, list):
        raise ValueError("revision_asof_queries")

    checked_observations: list[dict[str, Any]] = []
    identities: set[tuple[str, int, int]] = set()
    availability_by_event: dict[tuple[str, int], dict[int, int]] = {}
    for row in observations:
        if not isinstance(row, dict) or set(row) != _OBSERVATION_KEYS:
            raise ValueError("revision_asof_observation")
        key = _string(row["key"], "observation_key")
        event_ns = _nonnegative_int(row["event_ns"], "event_ns")
        available_ns = _nonnegative_int(row["available_ns"], "available_ns")
        revision = _nonnegative_int(row["revision"], "revision")
        if available_ns < event_ns:
            raise ValueError("revision_asof_availability_before_event")
        value = _value(row["value"])
        withdrawn = row["withdrawn"]
        if type(withdrawn) is not bool:
            raise ValueError("revision_asof_withdrawn")
        identity = (key, event_ns, revision)
        if identity in identities:
            raise ValueError("revision_asof_duplicate_observation")
        identities.add(identity)
        event_identity = (key, event_ns)
        availability_by_event.setdefault(event_identity, {})[revision] = available_ns
        checked_observations.append({
            "key": key, "event_ns": event_ns, "available_ns": available_ns,
            "revision": revision, "value": value, "withdrawn": withdrawn,
        })

    for revision_availability in availability_by_event.values():
        ordered = sorted(revision_availability.items())
        if any(right[1] < left[1] for left, right in zip(ordered, ordered[1:])):
            raise ValueError("revision_asof_decreasing_availability")

    checked_queries: list[dict[str, Any]] = []
    query_ids: set[str] = set()
    for query in queries:
        if not isinstance(query, dict) or set(query) != _QUERY_KEYS:
            raise ValueError("revision_asof_query")
        identity = _string(query["id"], "query_id")
        key = _string(query["key"], "query_key")
        decision_ns = _nonnegative_int(query["decision_ns"], "decision_ns")
        max_age_ns = _nonnegative_int(query["max_age_ns"], "max_age_ns")
        if identity in query_ids:
            raise ValueError("revision_asof_duplicate_query_id")
        query_ids.add(identity)
        checked_queries.append({"id": identity, "key": key, "decision_ns": decision_ns,
                               "max_age_ns": max_age_ns})

    counts = {status: 0 for status in _STATUSES}
    results = []
    for query in checked_queries:
        decision_ns = query["decision_ns"]
        eligible = [row for row in checked_observations
                    if row["key"] == query["key"]
                    and row["available_ns"] <= decision_ns
                    and row["event_ns"] <= decision_ns]
        selected = max(eligible, key=lambda row: (row["event_ns"], row["revision"]), default=None)
        if selected is None:
            status = "absent"
        elif selected["withdrawn"]:
            status = "withdrawn"
        elif decision_ns - selected["event_ns"] > query["max_age_ns"]:
            status = "stale"
        elif selected["value"] is None:
            status = "missing_value"
        else:
            status = "available"
        counts[status] += 1
        results.append({"id": query["id"], "status": status,
                        "selected": dict(selected) if selected is not None else None})
    return {"schema": "brain-revision-asof.v1", "results": results, "counts": counts}


def _reject_constant(_value: str) -> None:
    raise ValueError("revision_asof_nonfinite_json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("revision_asof_duplicate_json_key")
        result[key] = value
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
    except (ValueError, json.JSONDecodeError, RecursionError) as exc:
        print(str(exc) or "revision_asof_invalid_input", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"), allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
