"""Bound paired loss differences over an explicit, capped evaluation roster.

For each supplied row, delta is ``loss_b - loss_a``. A known loss is fixed;
each missing loss may be anywhere in the caller-declared interval [0, U]. The
reported lower and upper means average row-wise difference endpoints over the
entire explicit roster, including rows with both losses missing. Cohort results
use the same calculation within each cohort. This is a descriptive bound, not
imputation or inference.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from fractions import Fraction
from typing import Any


_TOP_LEVEL_KEYS = {"model_a", "model_b", "metric", "loss_upper_bound", "rows"}
_ROW_KEYS = {"id", "cohort", "loss_a", "loss_b"}
_MAX_ROWS = 10_000
_LIMITATIONS = [
    "Bounds assume loss_upper_bound is valid for the chosen metric; an unbounded metric cannot obtain a valid cap from observed maxima.",
    "The roster and bounds describe only supplied identities; identities omitted from the roster remain unknown.",
    "Missing losses are bounded without imputation; no sampling interval, p-value, causal conclusion, winner or acceptance flag, or trading decision is provided.",
    "Complete-pair means use only rows with both losses observed; all-roster bounds include every supplied row.",
    "Statistics are computed exactly from supplied numeric values, then represented as JSON integers or finite floats; nonintegral results outside float range are rounded once to the nearest integer (ties to even).",
]


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


def _finite_number(value: Any, field: str) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return Fraction(value)


def _loss(value: Any, field: str, upper_bound: Fraction) -> Fraction | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    number = _finite_number(value, field)
    if number < 0 or number > upper_bound:
        raise ValueError(f"{field} must be within [0, loss_upper_bound]")
    return number


def _output_number(value: Fraction) -> int | float:
    """Convert an exact result once, avoiding nonfinite JSON numbers."""
    if value.denominator == 1:
        return value.numerator
    try:
        converted = float(value)
    except OverflowError:
        converted = math.inf
    if math.isfinite(converted):
        return converted
    # A nonintegral rational outside binary64 range is represented by its
    # nearest integer (Python's round uses ties-to-even).
    return round(value)


def _mean(values: list[Fraction]) -> int | float | None:
    if not values:
        return None
    return _output_number(sum(values, Fraction()) / len(values))


def _population(rows: list[dict[str, Any]], upper_bound: Fraction) -> dict[str, Any]:
    total = len(rows)
    paired = [row for row in rows if row["loss_a"] is not None and row["loss_b"] is not None]
    deltas = [row["loss_b"] - row["loss_a"] for row in paired]
    lower: list[float] = []
    upper: list[float] = []
    counts = {"paired": 0, "a_only": 0, "b_only": 0, "both_missing": 0}
    for row in rows:
        loss_a, loss_b = row["loss_a"], row["loss_b"]
        a_low, a_high = (Fraction(), upper_bound) if loss_a is None else (loss_a, loss_a)
        b_low, b_high = (Fraction(), upper_bound) if loss_b is None else (loss_b, loss_b)
        lower.append(b_low - a_high)
        upper.append(b_high - a_low)
        if loss_a is not None and loss_b is not None:
            counts["paired"] += 1
        elif loss_a is not None:
            counts["a_only"] += 1
        elif loss_b is not None:
            counts["b_only"] += 1
        else:
            counts["both_missing"] += 1

    # Every count is explicitly interpretable against the population size.
    denominators = {f"{name}_count_denominator": total for name in counts}
    return {
        "total_count": total,
        "total_denominator": total,
        "paired_count": counts["paired"],
        "paired_count_denominator": total,
        "a_only_count": counts["a_only"],
        "a_only_count_denominator": total,
        "b_only_count": counts["b_only"],
        "b_only_count_denominator": total,
        "both_missing_count": counts["both_missing"],
        "both_missing_count_denominator": total,
        "complete_pair_mean_delta": _mean(deltas),
        "complete_pair_mean_denominator": len(paired),
        "all_roster_lower_mean_delta": _mean(lower),
        "all_roster_upper_mean_delta": _mean(upper),
        "all_roster_mean_denominator": total,
        **denominators,
    }


def analyze(payload: dict) -> dict:
    """Validate strict roster input and return deterministic bounded results."""
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    if set(payload) != _TOP_LEVEL_KEYS:
        raise ValueError("payload must contain exactly model_a, model_b, metric, loss_upper_bound, rows")
    model_a = _nonempty_string(payload["model_a"], "model_a")
    model_b = _nonempty_string(payload["model_b"], "model_b")
    metric = _nonempty_string(payload["metric"], "metric")
    original_upper_bound = payload["loss_upper_bound"]
    upper_bound = _finite_number(original_upper_bound, "loss_upper_bound")
    if upper_bound <= 0:
        raise ValueError("loss_upper_bound must be positive")
    rows = payload["rows"]
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    if len(rows) > _MAX_ROWS:
        raise ValueError("rows must contain at most 10000 items")

    canonical = _canonical_json(payload)
    seen_ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    cohorts: dict[str, list[dict[str, Any]]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            raise ValueError(f"rows[{index}] must contain exactly id, cohort, loss_a, loss_b")
        identity = _nonempty_string(row["id"], f"rows[{index}].id")
        cohort = _nonempty_string(row["cohort"], f"rows[{index}].cohort")
        if identity in seen_ids:
            raise ValueError("row ids must be unique")
        seen_ids.add(identity)
        parsed = {
            "id": identity,
            "cohort": cohort,
            "loss_a": _loss(row["loss_a"], f"rows[{index}].loss_a", upper_bound),
            "loss_b": _loss(row["loss_b"], f"rows[{index}].loss_b", upper_bound),
        }
        normalized.append(parsed)
        cohorts.setdefault(cohort, []).append(parsed)

    details = [
        {"cohort": cohort, **_population(cohorts[cohort], upper_bound)}
        for cohort in sorted(cohorts)
    ]
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "paired_loss_missing_bounds",
        "input_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "summary": {
            "model_a": model_a,
            "model_b": model_b,
            "metric": metric,
            "loss_upper_bound": original_upper_bound,
            **_population(normalized, upper_bound),
        },
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
        print(f"paired_loss_missing_bounds: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
