"""Measure paired loss differences under a caller-declared cohort mix.

For each supplied complete pair, delta is ``loss_b - loss_a``. The pooled
mean weights rows equally; the target mean uses the declared cohort weights
without renormalizing over unsupported cohorts. This is descriptive and does
not validate the caller's cohort distribution or comparability assumptions.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from fractions import Fraction
from typing import Any


_TOP_LEVEL_KEYS = {"model_a", "model_b", "metric", "target_cohorts", "rows"}
_TARGET_KEYS = {"cohort", "weight"}
_ROW_KEYS = {"id", "cohort", "loss_a", "loss_b"}
_MAX_ITEMS = 10_000
_LIMITATIONS = [
    "Only supplied complete pairs are analyzed; no omitted identities are inferred, filtered, or imputed.",
    "Target weights and cohort comparability are caller assumptions and are not empirically validated.",
    "A target mean is reported only when every positive-weight cohort has support; weights are never renormalized over supported cohorts.",
    "This descriptive diagnostic provides no p-value, transportability guarantee, winner, or deployment gate.",
    "Statistics use exact integer and binary-float rational arithmetic until final representation; final float rounding or underflow may occur, and nonintegral values beyond float range are rounded to the nearest integer (ties to even).",
]


def _validate_json_value(value: Any, path: str = "payload") -> None:
    """Reject Python-only values before canonical hashing or calculation."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain finite JSON values")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{path} object keys must be strings")
            _validate_json_value(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} must contain only JSON values")


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("payload must contain finite JSON values") from exc


def _integer_json(value: int) -> str:
    """Format arbitrary integers without changing Python's parser digit limit."""
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    remaining = abs(value)
    chunks = []
    while remaining:
        remaining, chunk = divmod(remaining, 1_000_000_000)
        chunks.append(chunk)
    return sign + str(chunks[-1]) + "".join(f"{chunk:09d}" for chunk in reversed(chunks[:-1]))


def _canonical_output_json(value: Any) -> str:
    """Serialize JSON deterministically, including integers beyond str's digit cap."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        return _integer_json(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("cannot serialize nonfinite JSON number")
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if type(value) is list:
        return "[" + ",".join(_canonical_output_json(item) for item in value) + "]"
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise ValueError("JSON object keys must be strings")
        return "{" + ",".join(
            f"{json.dumps(key, ensure_ascii=False)}:{_canonical_output_json(value[key])}"
            for key in sorted(value)
        ) + "}"
    raise ValueError("cannot serialize non-JSON value")


def _nonempty_string(value: Any, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _finite_number(value: Any, field: str) -> Fraction:
    if type(value) not in (int, float):
        raise ValueError(f"{field} must be a finite number")
    if type(value) is float and not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return Fraction(value)


def _output_number(value: Fraction) -> int | float:
    """Represent an exact result once as an integer or finite binary float."""
    if value.denominator == 1:
        return value.numerator
    try:
        converted = float(value)
    except OverflowError:
        converted = math.inf
    if math.isfinite(converted):
        return converted
    return round(value)


def analyze(payload: dict) -> dict:
    """Validate a declared cohort mix and return its exact descriptive result."""
    if type(payload) is not dict:
        raise ValueError("payload must be an object")
    try:
        _validate_json_value(payload)
    except RecursionError as exc:
        raise ValueError("payload must contain only finite JSON values") from exc
    if set(payload) != _TOP_LEVEL_KEYS:
        raise ValueError("payload must contain exactly model_a, model_b, metric, target_cohorts, rows")
    model_a = _nonempty_string(payload["model_a"], "model_a")
    model_b = _nonempty_string(payload["model_b"], "model_b")
    metric = _nonempty_string(payload["metric"], "metric")

    target_cohorts = payload["target_cohorts"]
    if type(target_cohorts) is not list:
        raise ValueError("target_cohorts must be a list")
    if not target_cohorts or len(target_cohorts) > _MAX_ITEMS:
        raise ValueError("target_cohorts must contain between 1 and 10000 items")
    weights: dict[str, Fraction] = {}
    original_weights: dict[str, int | float] = {}
    for index, item in enumerate(target_cohorts):
        if type(item) is not dict or set(item) != _TARGET_KEYS:
            raise ValueError(f"target_cohorts[{index}] must contain exactly cohort, weight")
        cohort = _nonempty_string(item["cohort"], f"target_cohorts[{index}].cohort")
        if cohort in weights:
            raise ValueError("target cohort names must be unique")
        weight = _finite_number(item["weight"], f"target_cohorts[{index}].weight")
        if weight < 0:
            raise ValueError(f"target_cohorts[{index}].weight must be nonnegative")
        weights[cohort] = weight
        original_weights[cohort] = item["weight"]
    weight_total = sum(weights.values(), Fraction())
    if weight_total <= 0:
        raise ValueError("target cohort weights must have a positive total")

    rows = payload["rows"]
    if type(rows) is not list:
        raise ValueError("rows must be a list")
    if len(rows) > _MAX_ITEMS:
        raise ValueError("rows must contain at most 10000 items")

    canonical = _canonical_json(payload)
    seen_ids: set[str] = set()
    cohort_deltas: dict[str, list[Fraction]] = {cohort: [] for cohort in weights}
    all_deltas: list[Fraction] = []
    for index, row in enumerate(rows):
        if type(row) is not dict or set(row) != _ROW_KEYS:
            raise ValueError(f"rows[{index}] must contain exactly id, cohort, loss_a, loss_b")
        identity = _nonempty_string(row["id"], f"rows[{index}].id")
        cohort = _nonempty_string(row["cohort"], f"rows[{index}].cohort")
        if identity in seen_ids:
            raise ValueError("row ids must be unique")
        seen_ids.add(identity)
        if cohort not in weights:
            raise ValueError(f"rows[{index}].cohort must reference a declared target cohort")
        loss_a = _finite_number(row["loss_a"], f"rows[{index}].loss_a")
        loss_b = _finite_number(row["loss_b"], f"rows[{index}].loss_b")
        if loss_a < 0 or loss_b < 0:
            raise ValueError(f"rows[{index}] losses must be nonnegative")
        delta = loss_b - loss_a
        cohort_deltas[cohort].append(delta)
        all_deltas.append(delta)

    total_rows = len(all_deltas)
    pooled = sum(all_deltas, Fraction()) / total_rows if total_rows else None
    counts = {cohort: len(cohort_deltas[cohort]) for cohort in weights}
    supported_weight = sum(
        (weights[cohort] for cohort in weights if counts[cohort] > 0), Fraction()
    )
    unsupported_positive = sum(
        1 for cohort in weights if counts[cohort] == 0 and weights[cohort] > 0
    )
    complete_target_support = unsupported_positive == 0

    details = []
    target_weighted_sum = Fraction()
    for cohort in sorted(weights):
        deltas = cohort_deltas[cohort]
        count = len(deltas)
        mean = sum(deltas, Fraction()) / count if count else None
        share = weights[cohort] / weight_total
        if count:
            contribution = share * mean
            target_weighted_sum += contribution
        else:
            contribution = Fraction() if weights[cohort] == 0 else None
        details.append({
            "cohort": cohort,
            "row_count": count,
            "sum_delta": _output_number(sum(deltas, Fraction())),
            "mean_delta": _output_number(mean) if mean is not None else None,
            "target_weight": original_weights[cohort],
            "target_share": _output_number(share),
            "supported": count > 0,
            "weighted_contribution": _output_number(contribution) if contribution is not None else None,
        })

    target_mean = target_weighted_sum if complete_target_support else None
    difference = target_mean - pooled if target_mean is not None and pooled is not None else None
    summary = {
        "model_a": model_a,
        "model_b": model_b,
        "metric": metric,
        "row_count": total_rows,
        "declared_cohort_count": len(weights),
        "supported_cohort_count": sum(1 for count in counts.values() if count > 0),
        "unsupported_positive_weight_cohort_count": unsupported_positive,
        "target_weight_total": _output_number(weight_total),
        "supported_target_weight": _output_number(supported_weight),
        "supported_target_share": _output_number(supported_weight / weight_total),
        "pooled_mean_delta": _output_number(pooled) if pooled is not None else None,
        "target_mean_delta": _output_number(target_mean) if target_mean is not None else None,
        "target_minus_pooled": _output_number(difference) if difference is not None else None,
    }
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "paired_loss_target_mix",
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
        payload = json.load(
            sys.stdin,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
        result = analyze(payload)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        sys.stderr.write(_canonical_output_json({"error": f"paired_loss_target_mix: {exc}"}) + "\n")
        return 2
    sys.stdout.write(_canonical_output_json(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
