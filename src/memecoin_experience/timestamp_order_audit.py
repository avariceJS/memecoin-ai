"""Classify selected declared event-order relations from closed time intervals.

The intervals are caller-declared event-time bounds. This module does not
estimate clock accuracy, availability, receipt time, or causality.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any


_ERROR = "timestamp_order_audit_invalid_input"
_MAX_EVENTS = 256
_MAX_PAIRS = 1024
_MAX_NS = 9223372036854775807
_PAYLOAD_KEYS = {"events", "pairs"}
_EVENT_KEYS = {"id", "lo_ns", "hi_ns"}
_PAIR_KEYS = {"before", "after"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", re.ASCII)
_LIMITATIONS = [
    "Only caller-declared intervals and selected pairs are analyzed.",
    "Closed event-time intervals do not establish clock accuracy, availability, receipt time, or causality.",
    "Indeterminate order is unresolved, not a contradiction; no global consistency is inferred.",
]


def _invalid() -> ValueError:
    return ValueError(_ERROR)


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def analyze(payload: dict) -> dict:
    """Return local classifications for the explicitly selected before pairs."""
    if type(payload) is not dict or set(payload) != _PAYLOAD_KEYS:
        raise _invalid()
    events = payload["events"]
    pairs = payload["pairs"]
    if type(events) is not list or type(pairs) is not list:
        raise _invalid()
    if len(events) > _MAX_EVENTS or len(pairs) > _MAX_PAIRS:
        raise _invalid()

    by_id: dict[str, tuple[int, int]] = {}
    for event in events:
        if type(event) is not dict or set(event) != _EVENT_KEYS:
            raise _invalid()
        identity = event["id"]
        lo_ns = event["lo_ns"]
        hi_ns = event["hi_ns"]
        if not _valid_id(identity) or identity in by_id:
            raise _invalid()
        if type(lo_ns) is not int or type(hi_ns) is not int:
            raise _invalid()
        if lo_ns < 0 or hi_ns < lo_ns or hi_ns > _MAX_NS:
            raise _invalid()
        by_id[identity] = (lo_ns, hi_ns)

    selected: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for pair in pairs:
        if type(pair) is not dict or set(pair) != _PAIR_KEYS:
            raise _invalid()
        before = pair["before"]
        after = pair["after"]
        if not _valid_id(before) or not _valid_id(after):
            raise _invalid()
        if before not in by_id or after not in by_id or before == after:
            raise _invalid()
        relation = (before, after)
        if relation in seen_pairs:
            raise _invalid()
        seen_pairs.add(relation)
        selected.append(relation)

    classified = []
    counts = {"definite_before": 0, "definite_after": 0, "indeterminate": 0}
    for before, after in sorted(selected):
        before_lo, before_hi = by_id[before]
        after_lo, after_hi = by_id[after]
        if before_hi < after_lo:
            order, status = "definite_before", "consistent"
        elif after_hi < before_lo:
            order, status = "definite_after", "contradiction"
        else:
            order, status = "indeterminate", "unresolved"
        counts[order] += 1
        classified.append({
            "before": before,
            "after": after,
            "order": order,
            "declared_order_status": status,
        })

    return {
        "schema": "timestamp-order-audit-v1",
        "summary": {
            "event_count": len(events),
            "pair_count": len(pairs),
            "definite_before_count": counts["definite_before"],
            "definite_after_count": counts["definite_after"],
            "indeterminate_count": counts["indeterminate"],
            "contradiction_count": counts["definite_after"],
        },
        "pairs": classified,
        "limitations": list(_LIMITATIONS),
    }


def _reject_constant(_value: str) -> None:
    raise _invalid()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise _invalid()
        raw = sys.stdin.buffer.read(1048577)
        if len(raw) > 1048576:
            raise _invalid()
        payload = json.loads(
            raw.decode("utf-8", errors="strict"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
        result = analyze(payload)
        output = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        sys.stderr.write('{"error":"timestamp_order_audit_invalid_input"}\n')
        return 2
    sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
