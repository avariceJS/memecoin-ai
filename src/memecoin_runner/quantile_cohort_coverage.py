"""Descriptive quantile coverage grouped by caller-declared cohorts."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from typing import Any


_STATUSES = ("scored", "missing_actual", "missing_prediction", "unsupported")
_ROW_KEYS = {"id", "target", "cohort", "status", "actual", "quantiles"}
_LEVEL_KEYS = {"level", "value"}
_LIMITATIONS = [
    "Target and cohort labels and row membership are caller claims; labels are not inferred or verified.",
    "Overlapping or time-correlated rows do not support iid uncertainty estimates.",
    "These descriptive values do not establish general calibration or model quality.",
]


def _number(value: Any, label: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}_must_be_finite_number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{label}_out_of_range") from None
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValueError(f"{label}_out_of_range")
    return result


def _exact_keys(value: dict, expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label}_keys_mismatch")


def analyze(payload: dict) -> dict:
    """Validate explicit forecast rows and compute coverage by target/cohort."""
    if not isinstance(payload, dict):
        raise ValueError("payload_must_be_object")
    _exact_keys(payload, {"nominal_levels", "rows"}, "payload")
    nominal = payload["nominal_levels"]
    rows = payload["rows"]
    if not isinstance(nominal, list) or not nominal:
        raise ValueError("nominal_levels_must_be_nonempty_list")
    if len(nominal) > 9:
        raise ValueError("nominal_levels_limit_exceeded")
    levels = [_number(level, "nominal_level", minimum=0, maximum=1) for level in nominal]
    if any(level <= 0 or level >= 1 for level in levels):
        raise ValueError("nominal_level_out_of_range")
    if any(left >= right for left, right in zip(levels, levels[1:])):
        raise ValueError("nominal_levels_must_be_strictly_increasing")
    if not isinstance(rows, list):
        raise ValueError("rows_must_be_list")
    if len(rows) > 10_000:
        raise ValueError("rows_limit_exceeded")

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    totals = {status: 0 for status in _STATUSES}
    seen_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("row_must_be_object")
        _exact_keys(row, _ROW_KEYS, "row")
        for field in ("id", "target", "cohort"):
            if not isinstance(row[field], str) or not row[field]:
                raise ValueError(f"{field}_must_be_nonempty_string")
        if row["id"] in seen_ids:
            raise ValueError("row_ids_must_be_unique")
        seen_ids.add(row["id"])
        status = row["status"]
        if status not in _STATUSES:
            raise ValueError("invalid_status")
        actual_raw, forecasts = row["actual"], row["quantiles"]
        actual: float | None
        parsed_forecasts: list[float] | None = None
        if status in ("scored", "missing_prediction"):
            actual = _number(actual_raw, "actual", minimum=-1e12, maximum=1e12)
        elif actual_raw is not None:
            raise ValueError("actual_must_be_null")
        else:
            actual = None
        if status in ("scored", "missing_actual"):
            if not isinstance(forecasts, list) or len(forecasts) != len(levels):
                raise ValueError("quantiles_must_match_nominal_levels")
            parsed_forecasts = []
            for index, forecast in enumerate(forecasts):
                if not isinstance(forecast, dict):
                    raise ValueError("quantile_must_be_object")
                _exact_keys(forecast, _LEVEL_KEYS, "quantile")
                level = _number(forecast["level"], "quantile_level", minimum=0, maximum=1)
                if level != levels[index]:
                    raise ValueError("quantile_levels_must_match_nominal_levels")
                parsed_forecasts.append(_number(forecast["value"], "forecast", minimum=-1e12, maximum=1e12))
        elif forecasts is not None:
            raise ValueError("quantiles_must_be_null")

        key = (row["target"], row["cohort"])
        if key not in groups:
            groups[key] = {
                "target": key[0], "cohort": key[1], "row_count": 0,
                **{f"{name}_count": 0 for name in _STATUSES},
                "scored": [0] * len(levels), "covered": [0] * len(levels),
            }
        group = groups[key]
        group["row_count"] += 1
        group[f"{status}_count"] += 1
        totals[status] += 1
        if status == "scored":
            for index, forecast in enumerate(parsed_forecasts or []):
                group["scored"][index] += 1
                if actual <= forecast:
                    group["covered"][index] += 1

    details = []
    for key in sorted(groups):
        group = groups[key]
        level_results = []
        for index, level in enumerate(levels):
            scored, covered = group["scored"][index], group["covered"][index]
            empirical = covered / scored if scored else None
            level_results.append({
                "nominal_level": level, "scored_count": scored, "covered_count": covered,
                "empirical_coverage": empirical,
                "signed_gap": empirical - level if empirical is not None else None,
            })
        details.append({
            "target": group["target"], "cohort": group["cohort"], "row_count": group["row_count"],
            **{f"{status}_count": group[f"{status}_count"] for status in _STATUSES},
            "levels": level_results,
        })

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "quantile_cohort_coverage",
        "input_sha256": hashlib.sha256(canonical).hexdigest(),
        "summary": {
            "row_count": len(rows), "group_count": len(groups),
            **{f"{status}_count": totals[status] for status in _STATUSES},
        },
        "details": details,
        "limitations": list(_LIMITATIONS),
    }


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite_json_number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
        json.dump(result, sys.stdout, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except (ValueError, TypeError, json.JSONDecodeError, RecursionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
