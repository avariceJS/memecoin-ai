"""Audit preassigned train intervals against test intervals and post-test embargoes."""

from __future__ import annotations

import json
import re
import sys
from typing import Any


_PREFIX = "purged_temporal_split_"
_MAX_VALUE = 1_000_000_000_000
_MAX_ROWS = 256
_MAX_INPUT_BYTES = 1_048_576
_PAYLOAD_KEYS = {"rows", "embargo"}
_ROW_KEYS = {"id", "role", "start", "end"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)


def _fail(label: str) -> None:
    raise ValueError(_PREFIX + label)


def _integer(value: Any, label: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_VALUE:
        _fail(label)
    return value


def analyze(payload: dict) -> dict:
    """Return retained and purged train IDs with every conflicting test ID."""
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        _fail("payload_keys")
    rows, embargo_raw = payload["rows"], payload["embargo"]
    embargo = _integer(embargo_raw, "embargo")
    if not isinstance(rows, list):
        _fail("rows_type")
    if len(rows) > _MAX_ROWS:
        _fail("rows_length")

    seen: set[str] = set()
    trains: list[tuple[str, int, int]] = []
    tests: list[tuple[str, int, int]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            _fail("row_keys")
        identifier, role = row["id"], row["role"]
        if not isinstance(identifier, str) or _ID.fullmatch(identifier) is None:
            _fail("id")
        if identifier in seen:
            _fail("duplicate_id")
        seen.add(identifier)
        if role not in ("train", "test") or not isinstance(role, str):
            _fail("role")
        start, end = _integer(row["start"], "start"), _integer(row["end"], "end")
        if start >= end:
            _fail("interval")
        (trains if role == "train" else tests).append((identifier, start, end))

    reasons: list[dict[str, Any]] = []
    purged: list[str] = []
    retained: list[str] = []
    for identifier, start, end in trains:
        matches: list[dict[str, str]] = []
        for test_id, test_start, test_end in tests:
            if start < test_end and test_start < end:
                reason = "overlap"
            elif embargo > 0 and start < test_end + embargo and test_end < end:
                reason = "embargo"
            else:
                continue
            matches.append({"test_id": test_id, "reason": reason})
        matches.sort(key=lambda match: match["test_id"])
        reasons.append({"id": identifier, "matches": matches})
        (purged if matches else retained).append(identifier)

    return {
        "schema": "brain-purged-temporal-split.v1",
        "embargo": embargo,
        "retained_train_ids": sorted(retained),
        "purged_train_ids": sorted(purged),
        "test_ids": sorted(test_id for test_id, _, _ in tests),
        "reasons": sorted(reasons, key=lambda item: item["id"]),
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_PREFIX + "duplicate_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError(_PREFIX + "nonfinite")


def main() -> int:
    """Read one bounded JSON document from stdin and emit canonical JSON."""
    try:
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            raise ValueError(_PREFIX + "input_size")
        document = raw.decode("utf-8")
        payload = json.loads(document, object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant)
        result = analyze(payload)
        output = json.dumps(result, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=True, allow_nan=False)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        message = str(exc)
        if not message.startswith(_PREFIX):
            message = _PREFIX + "invalid_input"
        sys.stderr.write(message.splitlines()[0] + "\n")
        return 2
    sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
