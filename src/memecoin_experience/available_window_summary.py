"""Summarize explicitly available observations in caller-declared windows.

The API is a deterministic descriptive scan over supplied scalar observations.
It does not infer timestamps, fill missing values, or discover expected support.
"""

from __future__ import annotations

import json
import math
import re
import sys
from typing import Any


_MAX_NS = 2**63 - 1
_MAX_VALUE = 10**12
_MAX_INPUT_BYTES = 1_048_576
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)
_ROOT_KEYS = {"decision_ns", "observations", "windows"}
_OBSERVATION_KEYS = {"id", "event_ns", "available_ns", "value"}
_WINDOW_KEYS = {"id", "older_ns", "newer_ns"}


def _plain_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0 or value > _MAX_NS:
        raise ValueError(f"available_window_summary_{label}")
    return value


def _identity(value: Any, label: str) -> str:
    if type(value) is not str or _ID_RE.fullmatch(value) is None:
        raise ValueError(f"available_window_summary_{label}")
    return value


def _value(value: Any) -> float | None:
    if value is None:
        return None
    if type(value) is int:
        if abs(value) > _MAX_VALUE:
            raise ValueError("available_window_summary_value")
        return float(value)
    if type(value) is not float or not math.isfinite(value) or abs(value) > _MAX_VALUE:
        raise ValueError("available_window_summary_value")
    return value


def analyze(payload: dict) -> dict:
    """Validate and summarize the observations without modifying ``payload``."""
    if type(payload) is not dict or set(payload) != _ROOT_KEYS:
        raise ValueError("available_window_summary_payload")

    decision = _plain_int(payload["decision_ns"], "decision_ns")
    observations = payload["observations"]
    windows = payload["windows"]
    if type(observations) is not list or len(observations) > 10_000:
        raise ValueError("available_window_summary_observations")
    if type(windows) is not list or not 1 <= len(windows) <= 64:
        raise ValueError("available_window_summary_windows")

    checked_observations: list[tuple[str, int, int, float | None]] = []
    observation_ids: set[str] = set()
    for row in observations:
        if type(row) is not dict or set(row) != _OBSERVATION_KEYS:
            raise ValueError("available_window_summary_observation")
        identity = _identity(row["id"], "observation_id")
        if identity in observation_ids:
            raise ValueError("available_window_summary_duplicate_observation_id")
        observation_ids.add(identity)
        event_ns = _plain_int(row["event_ns"], "event_ns")
        available_ns = _plain_int(row["available_ns"], "available_ns")
        if event_ns > available_ns:
            raise ValueError("available_window_summary_timestamp_order")
        checked_observations.append((identity, event_ns, available_ns, _value(row["value"])))

    checked_windows: list[tuple[str, int, int]] = []
    window_ids: set[str] = set()
    for row in windows:
        if type(row) is not dict or set(row) != _WINDOW_KEYS:
            raise ValueError("available_window_summary_window")
        identity = _identity(row["id"], "window_id")
        if identity in window_ids:
            raise ValueError("available_window_summary_duplicate_window_id")
        window_ids.add(identity)
        older_ns = _plain_int(row["older_ns"], "older_ns")
        newer_ns = _plain_int(row["newer_ns"], "newer_ns")
        if newer_ns >= older_ns:
            raise ValueError("available_window_summary_window_bounds")
        checked_windows.append((identity, older_ns, newer_ns))

    result_windows: list[dict[str, Any]] = []
    for identity, older_ns, newer_ns in sorted(checked_windows, key=lambda item: item[0]):
        start_ns = decision - older_ns
        end_ns = decision - newer_ns
        values: list[tuple[str, float]] = []
        missing_count = 0
        for observation_id, event_ns, available_ns, value in checked_observations:
            if start_ns <= event_ns < end_ns and available_ns <= decision:
                if value is None:
                    missing_count += 1
                else:
                    values.append((observation_id, value))

        values.sort(key=lambda item: item[0])
        count = len(values)
        if count:
            numbers = [number for _, number in values]
            mean = math.fsum(numbers) / count
            minimum = min(numbers)
            maximum = max(numbers)
            is_missing = False
        else:
            mean = minimum = maximum = None
            is_missing = True
        result_windows.append({
            "id": identity,
            "start_ns": start_ns,
            "end_ns": end_ns,
            "count": count,
            "missing_count": missing_count,
            "mean": mean,
            "min": minimum,
            "max": maximum,
            "missing": is_missing,
        })

    return {
        "schema": "brain-available-window-summary.v1",
        "decision_ns": decision,
        "observation_count": len(checked_observations),
        "windows": result_windows,
    }


def _reject_constant(_constant: str) -> None:
    raise ValueError("available_window_summary_nonfinite_json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("available_window_summary_duplicate_json_key")
        result[key] = value
    return result


def main() -> int:
    """Read one bounded JSON object from stdin and write one summary."""
    try:
        if len(sys.argv) != 1:
            raise ValueError("available_window_summary_file_arguments_not_supported")
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            raise ValueError("available_window_summary_input_too_large")
        source = raw.decode("utf-8")
        payload = json.loads(
            source,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
        result = analyze(payload)
        encoded = json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, UnicodeError, RecursionError) as exc:
        detail = str(exc).splitlines()[0] if str(exc) else "invalid_input"
        print(f"available_window_summary_invalid_input: {detail}", file=sys.stderr)
        return 2

    sys.stdout.write(encoded + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
