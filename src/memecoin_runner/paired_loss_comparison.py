"""Descriptive comparison of caller-supplied paired model losses.

The input has exactly ``model_a``, ``model_b``, ``metric``, and ``rows``.
Each row has a unique nonempty ``id``, nonempty ``cohort``, and nonnegative
finite ``loss_a``/``loss_b`` values (either loss may be null). Only rows with
both losses present contribute to comparison statistics. A row with only
``loss_a`` is counted as ``a_only``; a row with only ``loss_b`` is counted as
``b_only``; two nulls are ``both_missing``.

For paired rows, delta is ``loss_b - loss_a``: positive values favor A because
A has lower loss. Per-cohort mean delta is the arithmetic mean of its paired
deltas. A win is a strictly positive delta, a B win is strictly negative, and
zero is a tie. The micro mean pools all paired rows. The macro mean gives equal
weight to each cohort with at least one paired row. Unsupported cohorts remain
listed with null means, and overall means are null when their support is zero.
All counts expose the relevant support. This is a descriptive diagnostic only;
it does not estimate significance, uncertainty, bootstrap intervals, or
training/model quality. Losses must be computed by the caller under a suitable
common protocol.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from typing import Any


_TOP_LEVEL_KEYS = {"model_a", "model_b", "metric", "rows"}
_ROW_KEYS = {"id", "cohort", "loss_a", "loss_b"}


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("payload must contain finite JSON values") from exc


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _loss(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a nonnegative finite number or null")
    try:
        number = float(value)
    except (OverflowError, ValueError):
        number = math.inf
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be a nonnegative finite number or null")
    return number


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    # Scaling before summation avoids overflow for valid, very large losses.
    scale = max(abs(value) for value in values)
    if scale == 0:
        return 0.0
    return math.fsum((value / scale) / len(values) for value in values) * scale


def analyze(payload: dict) -> dict:
    """Validate explicit paired-loss input and return a frozen diagnostic envelope."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if set(payload) != _TOP_LEVEL_KEYS:
        raise ValueError("payload must contain exactly model_a, model_b, metric, rows")
    model_a = _nonempty_string(payload["model_a"], "model_a")
    model_b = _nonempty_string(payload["model_b"], "model_b")
    if model_a == model_b:
        raise ValueError("model_a and model_b must be distinct")
    metric = _nonempty_string(payload["metric"], "metric")
    rows = payload["rows"]
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")

    canonical = _canonical_json(payload)
    seen_ids: set[str] = set()
    by_cohort: dict[str, dict[str, Any]] = {}
    paired_deltas: list[float] = []
    totals = {"paired": 0, "a_only": 0, "b_only": 0, "both_missing": 0}

    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            raise ValueError(f"rows[{index}] must contain exactly id, cohort, loss_a, loss_b")
        identity = _nonempty_string(row["id"], f"rows[{index}].id")
        cohort = _nonempty_string(row["cohort"], f"rows[{index}].cohort")
        if identity in seen_ids:
            raise ValueError("row ids must be unique")
        seen_ids.add(identity)
        loss_a = _loss(row["loss_a"], f"rows[{index}].loss_a")
        loss_b = _loss(row["loss_b"], f"rows[{index}].loss_b")
        stats = by_cohort.setdefault(
            cohort,
            {"rows": 0, "paired": 0, "a_only": 0, "b_only": 0, "both_missing": 0,
             "deltas": [], "a_wins": 0, "b_wins": 0, "ties": 0},
        )
        stats["rows"] += 1
        if loss_a is not None and loss_b is not None:
            delta = loss_b - loss_a
            stats["paired"] += 1
            stats["deltas"].append(delta)
            paired_deltas.append(delta)
            totals["paired"] += 1
            if delta > 0:
                stats["a_wins"] += 1
            elif delta < 0:
                stats["b_wins"] += 1
            else:
                stats["ties"] += 1
        elif loss_a is not None:
            stats["a_only"] += 1
            totals["a_only"] += 1
        elif loss_b is not None:
            stats["b_only"] += 1
            totals["b_only"] += 1
        else:
            stats["both_missing"] += 1
            totals["both_missing"] += 1

    details = []
    supported_means = []
    for cohort in sorted(by_cohort):
        stats = by_cohort[cohort]
        mean_delta = _mean(stats["deltas"])
        if mean_delta is not None:
            supported_means.append(mean_delta)
        details.append({
            "cohort": cohort,
            "rows": stats["rows"],
            "paired": stats["paired"],
            "a_only": stats["a_only"],
            "b_only": stats["b_only"],
            "both_missing": stats["both_missing"],
            "mean_delta": mean_delta,
            "a_wins": stats["a_wins"],
            "b_wins": stats["b_wins"],
            "ties": stats["ties"],
        })

    summary = {
        "model_a": model_a,
        "model_b": model_b,
        "metric": metric,
        "rows": len(rows),
        **totals,
        "supported_cohorts": len(supported_means),
        "micro_mean_delta": _mean(paired_deltas),
        "macro_mean_delta": _mean(supported_means),
    }
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "paired_loss_comparison",
        "input_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "summary": summary,
        "details": details,
        "limitations": [
            "Descriptive paired-loss comparison only; no significance, uncertainty, or bootstrap inference is provided.",
            "Only rows with both losses contribute to means and win/tie counts; missing-loss support is reported separately.",
            "Interpretation assumes caller-computed losses use a common metric and comparable evaluation protocol.",
        ],
    }


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def main() -> int:
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant)
        result = analyze(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"paired_loss_comparison: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
