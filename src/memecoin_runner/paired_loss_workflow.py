"""Run the three paired-loss diagnostics from one explicit caller roster.

Python API::

    from memecoin_runner.paired_loss_workflow import analyze
    result = analyze({
        "model_a": "baseline", "model_b": "candidate", "metric": "log_loss",
        "loss_upper_bound": 1.0,
        "target_cohorts": [{"cohort": "new", "weight": 2},
                           {"cohort": "returning", "weight": 1}],
        "rows": [{"id": "r1", "cohort": "new", "loss_a": 0.4, "loss_b": 0.3}],
    })

CLI::

    python -m memecoin_runner.paired_loss_workflow < input.json > result.json

Input is strict JSON with exactly the fields shown above. Each row must
explicitly provide nullable ``loss_a`` and ``loss_b``. The caller supplies the
positive loss cap and complete target cohort mixture; neither is inferred from
observed values or roster frequencies. Every roster cohort must be declared,
including cohorts on rows with missing losses. Target weights are passed
unchanged to the target-mix producer, which does not renormalize unsupported
positive-weight cohorts. The three embedded reports retain their producer
envelopes exactly: comparison and bounds use every roster row, while target
mix uses only complete pairs.

The wrapper does not impute missing values or declare a winner, quality level,
or promotion decision. Positive ``loss_b - loss_a`` favors A. Paired, all-roster
bounds, and target-mixture results have different estimands. Input numbers are
subject to the producers' distinct numeric representations: comparison converts
losses to binary floats, while bounds and target-mixture calculations use exact
integer/binary-float rational arithmetic before JSON representation. Inputs
that cannot pass every producer's numeric contract fail as a whole.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from typing import Any

from memecoin_runner import paired_loss_comparison, paired_loss_missing_bounds, paired_loss_target_mix


_TOP_LEVEL_KEYS = {
    "model_a", "model_b", "metric", "loss_upper_bound", "target_cohorts", "rows"
}
_ROW_KEYS = {"id", "cohort", "loss_a", "loss_b"}
_TARGET_KEYS = {"cohort", "weight"}
_MAX_ITEMS = 10_000
_LIMITATIONS = [
    "Metric meaning, evaluation protocol, validity of loss_upper_bound, and target cohort weights are caller assumptions.",
    "Delta is loss_b - loss_a; positive values favor model_a because it has lower loss.",
    "Paired comparison, all-roster missing-loss bounds, and target-mixture reports have different estimands and denominators.",
    "Missing losses are not zeros; comparison and target-mix omit incomplete pairs while bounds retain every roster row.",
    "These diagnostics make no quality, winner, acceptance, or promotion claim.",
    "The comparison producer converts losses to binary floats; bounds and target-mix use exact rational arithmetic before finite JSON representation. Their numeric representation, rounding, and range limitations are inherited.",
]


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("payload must contain finite JSON values") from exc


def _nonempty_string(value: Any, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _number(value: Any, field: str) -> int | float:
    if type(value) not in (int, float):
        raise ValueError(f"{field} must be a finite number")
    if type(value) is float and not math.isfinite(value):
        raise ValueError(f"{field} must be a finite number")
    return value


def _output_json(value: Any) -> str:
    """Serialize finite JSON, including producer integers beyond Python's digit cap."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if type(value) is int:
        if value == 0:
            return "0"
        sign = "-" if value < 0 else ""
        chunks: list[int] = []
        remaining = abs(value)
        while remaining:
            remaining, chunk = divmod(remaining, 1_000_000_000)
            chunks.append(chunk)
        return sign + str(chunks[-1]) + "".join(f"{chunk:09d}" for chunk in reversed(chunks[:-1]))
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("workflow output must contain finite JSON values")
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    if type(value) is list:
        return "[" + ",".join(_output_json(item) for item in value) + "]"
    if type(value) is dict and all(type(key) is str for key in value):
        return "{" + ",".join(
            f"{json.dumps(key, ensure_ascii=False)}:{_output_json(value[key])}"
            for key in sorted(value)
        ) + "}"
    raise ValueError("workflow output must contain only JSON values")


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate one explicit roster and return its three actual producer reports."""
    if type(payload) is not dict or set(payload) != _TOP_LEVEL_KEYS:
        raise ValueError(
            "payload must contain exactly model_a, model_b, metric, loss_upper_bound, target_cohorts, rows"
        )
    model_a = _nonempty_string(payload["model_a"], "model_a")
    model_b = _nonempty_string(payload["model_b"], "model_b")
    if model_a == model_b:
        raise ValueError("model_a and model_b must be distinct")
    metric = _nonempty_string(payload["metric"], "metric")
    cap = _number(payload["loss_upper_bound"], "loss_upper_bound")
    if cap <= 0:
        raise ValueError("loss_upper_bound must be positive")

    targets = payload["target_cohorts"]
    if type(targets) is not list or not targets or len(targets) > _MAX_ITEMS:
        raise ValueError("target_cohorts must contain between 1 and 10000 items")
    target_names: set[str] = set()
    has_positive_weight = False
    for index, item in enumerate(targets):
        if type(item) is not dict or set(item) != _TARGET_KEYS:
            raise ValueError(f"target_cohorts[{index}] must contain exactly cohort, weight")
        name = _nonempty_string(item["cohort"], f"target_cohorts[{index}].cohort")
        if name in target_names:
            raise ValueError("target cohort names must be unique")
        target_names.add(name)
        weight = _number(item["weight"], f"target_cohorts[{index}].weight")
        if weight < 0:
            raise ValueError(f"target_cohorts[{index}].weight must be nonnegative")
        has_positive_weight = has_positive_weight or weight > 0
    if not has_positive_weight:
        raise ValueError("target cohort weights must have a positive total")

    rows = payload["rows"]
    if type(rows) is not list or len(rows) > _MAX_ITEMS:
        raise ValueError("rows must be a list containing at most 10000 items")

    canonical = _canonical_json(payload)
    seen_ids: set[str] = set()
    counts = {"paired": 0, "a_only": 0, "b_only": 0, "both_missing": 0}
    complete_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if type(row) is not dict or set(row) != _ROW_KEYS:
            raise ValueError(f"rows[{index}] must contain exactly id, cohort, loss_a, loss_b")
        identity = _nonempty_string(row["id"], f"rows[{index}].id")
        cohort = _nonempty_string(row["cohort"], f"rows[{index}].cohort")
        if identity in seen_ids:
            raise ValueError("row ids must be unique")
        seen_ids.add(identity)
        if cohort not in target_names:
            raise ValueError(f"rows[{index}].cohort must reference a declared target cohort")
        for field in ("loss_a", "loss_b"):
            loss = row[field]
            if loss is None:
                continue
            loss = _number(loss, f"rows[{index}].{field}")
            if loss < 0 or loss > cap:
                raise ValueError(f"rows[{index}].{field} must be within [0, loss_upper_bound]")
        has_a, has_b = row["loss_a"] is not None, row["loss_b"] is not None
        if has_a and has_b:
            counts["paired"] += 1
            complete_rows.append(row)
        elif has_a:
            counts["a_only"] += 1
        elif has_b:
            counts["b_only"] += 1
        else:
            counts["both_missing"] += 1

    # Producer payloads are projections only; every report comes from the real,
    # unchanged producer API and is retained without envelope edits.
    common = {"model_a": model_a, "model_b": model_b, "metric": metric}
    comparison_payload = {**common, "rows": rows}
    bounds_payload = {**common, "loss_upper_bound": cap, "rows": rows}
    target_payload = {**common, "target_cohorts": targets, "rows": complete_rows}
    try:
        comparison_report = paired_loss_comparison.analyze(comparison_payload)
        bounds_report = paired_loss_missing_bounds.analyze(bounds_payload)
        target_report = paired_loss_target_mix.analyze(target_payload)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError(f"producer numeric contract rejected the common input: {exc}") from exc

    roster_size = len(rows)
    support = {
        "roster_count": roster_size,
        "roster_denominator": roster_size,
        "complete_pair_count": counts["paired"],
        "complete_pair_denominator": roster_size,
        **{
            f"{name}_count": count for name, count in counts.items()
        },
        **{
            f"{name}_denominator": roster_size for name in counts
        },
        "target_projection_count": len(complete_rows),
        "target_projection_denominator": roster_size,
        "target_projection_excluded_count": roster_size - len(complete_rows),
        "target_projection_excluded_denominator": roster_size,
        "target_projection_complete_pair_denominator": len(complete_rows),
    }
    result = {
        "schema": "brain-paired-loss-workflow.v1",
        "input_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "support": support,
        "reports": [comparison_report, bounds_report, target_report],
        "limitations": list(_LIMITATIONS),
    }
    # Detect a nonfinite envelope before returning it, so the CLI never emits a
    # partial response and API callers receive the same failure semantics.
    _output_json(result)
    return result


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
            sys.stdin, parse_constant=_reject_constant, object_pairs_hook=_unique_object
        )
        result = analyze(payload)
        serialized = _output_json(result)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        sys.stderr.write(_output_json({"error": f"paired_loss_workflow: {exc}"}) + "\n")
        return 2
    sys.stdout.write(serialized + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
