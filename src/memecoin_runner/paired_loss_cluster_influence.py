"""Exact leave-one-cluster-out influence for supplied paired losses.

Each row contributes ``loss_b - loss_a``. Clusters are caller-declared and
statistics describe only supplied rows; removals are descriptive sensitivity
checks, not confidence intervals or recommendations.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from fractions import Fraction
from typing import Any


_TOP_LEVEL_KEYS = {"model_a", "model_b", "metric", "rows"}
_ROW_KEYS = {"id", "cluster", "loss_a", "loss_b"}
_MAX_ROWS = 10_000
_LIMITATIONS = [
    "Only supplied complete pairs and caller-declared clusters are described; omitted identities, correlation, and representativeness are unknown.",
    "Leave-one-cluster-out ranges are descriptive sensitivity results, not confidence intervals; no standard errors or independence assumptions are estimated.",
    "No rows are filtered, imputed, acquired, or used to calculate losses, and no recommendation to remove clusters is made.",
    "Exact integer and binary-float rational arithmetic is used; nonintegral final values round once to float, with ties-to-even integer output beyond float range, and extreme float conversion may underflow.",
]


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("payload must contain finite JSON values") from exc


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _finite_number(value: Any, field: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return Fraction(value)


def _loss(value: Any, field: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite nonnegative number")
    number = _finite_number(value, field)
    if number < 0:
        raise ValueError(f"{field} must be a finite nonnegative number")
    return number


def _output_number(value: Fraction) -> int | float:
    if value.denominator == 1:
        return value.numerator
    try:
        converted = float(value)
    except OverflowError:
        converted = math.inf
    if math.isfinite(converted):
        return converted
    return round(value)


def _json_integer(value: int) -> str:
    """Format arbitrary output integers without altering Python's input limit."""
    if value == 0:
        return "0"
    negative = value < 0
    remaining = -value if negative else value
    base = 10**1000
    chunks: list[int] = []
    while remaining:
        remaining, chunk = divmod(remaining, base)
        chunks.append(chunk)
    rendered = str(chunks.pop())
    while chunks:
        rendered += f"{chunks.pop():01000d}"
    return "-" + rendered if negative else rendered


def _json_dumps(value: Any) -> str:
    """Canonical JSON writer supporting exact output integers beyond parser limits."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return _json_integer(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("nonfinite output number")
        return json.dumps(value, allow_nan=False)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(_json_dumps(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + _json_dumps(value[key])
            for key in sorted(value)
        ) + "}"
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _mean(total: Fraction, count: int) -> int | float | None:
    return _output_number(total / count) if count else None


def _reversal(full_mean: Fraction | None, leave_out_mean: Fraction | None) -> bool | None:
    if full_mean is None or leave_out_mean is None:
        return None
    return (full_mean < 0 < leave_out_mean) or (leave_out_mean < 0 < full_mean)


def analyze(payload: dict) -> dict:
    """Validate the supplied pairs and calculate exact cluster deletion effects."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if set(payload) != _TOP_LEVEL_KEYS:
        raise ValueError("payload must contain exactly model_a, model_b, metric, rows")
    model_a = _nonempty_string(payload["model_a"], "model_a")
    model_b = _nonempty_string(payload["model_b"], "model_b")
    metric = _nonempty_string(payload["metric"], "metric")
    rows = payload["rows"]
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    if len(rows) > _MAX_ROWS:
        raise ValueError("rows must contain at most 10000 items")

    canonical = _canonical_json(payload)
    seen_ids: set[str] = set()
    cluster_sums: dict[str, Fraction] = {}
    cluster_counts: dict[str, int] = {}
    total_delta = Fraction()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            raise ValueError(f"rows[{index}] must contain exactly id, cluster, loss_a, loss_b")
        identity = _nonempty_string(row["id"], f"rows[{index}].id")
        cluster = _nonempty_string(row["cluster"], f"rows[{index}].cluster")
        if identity in seen_ids:
            raise ValueError("row ids must be unique")
        seen_ids.add(identity)
        loss_a = _loss(row["loss_a"], f"rows[{index}].loss_a")
        loss_b = _loss(row["loss_b"], f"rows[{index}].loss_b")
        delta = loss_b - loss_a
        total_delta += delta
        cluster_sums[cluster] = cluster_sums.get(cluster, Fraction()) + delta
        cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1

    count = len(rows)
    full_mean = total_delta / count if count else None
    details: list[dict[str, Any]] = []
    leave_out: list[Fraction] = []
    reversal_count = 0
    for cluster in sorted(cluster_counts):
        cluster_count = cluster_counts[cluster]
        remaining_count = count - cluster_count
        remaining_sum = total_delta - cluster_sums[cluster]
        leave_mean = remaining_sum / remaining_count if remaining_count else None
        shift = leave_mean - full_mean if leave_mean is not None and full_mean is not None else None
        reversal = _reversal(full_mean, leave_mean)
        if leave_mean is not None:
            leave_out.append(leave_mean)
        if reversal:
            reversal_count += 1
        details.append({
            "cluster": cluster,
            "row_count": cluster_count,
            "sum_delta": _output_number(cluster_sums[cluster]),
            "mean_delta": _output_number(cluster_sums[cluster] / cluster_count),
            "remaining_row_count": remaining_count,
            "leave_out_mean_delta": _output_number(leave_mean) if leave_mean is not None else None,
            "shift": _output_number(shift) if shift is not None else None,
            "sign_reversal": reversal,
        })

    shifts = [abs((mean - full_mean)) for mean in leave_out] if full_mean is not None else []
    summary = {
        "model_a": model_a,
        "model_b": model_b,
        "metric": metric,
        "row_count": count,
        "cluster_count": len(cluster_counts),
        "full_mean_delta": _output_number(full_mean) if full_mean is not None else None,
        "defined_removal_count": len(leave_out),
        "sign_reversal_count": reversal_count,
        "minimum_leave_out_mean_delta": _output_number(min(leave_out)) if leave_out else None,
        "maximum_leave_out_mean_delta": _output_number(max(leave_out)) if leave_out else None,
        "max_abs_shift": _output_number(max(shifts)) if shifts else None,
    }
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "paired_loss_cluster_influence",
        "input_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "summary": summary,
        "details": details,
        "limitations": list(_LIMITATIONS),
    }


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        sys.stderr.write(_json_dumps({"error": f"paired_loss_cluster_influence: {exc}"}) + "\n")
        return 2
    sys.stdout.write(_json_dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
