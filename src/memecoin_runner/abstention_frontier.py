"""Offline uncertainty-threshold sensitivity for explicit diagnostic rows.

``thresholds`` is a nonempty sequence of distinct finite numbers in [0, 1].
Each row has a unique nonempty string ``id``, strict boolean ``supported``,
``uncertainty`` in [0, 1] or null, and nonnegative finite ``loss`` or null.
At each sorted threshold, a row is accepted iff it is supported and its known
uncertainty is <= the threshold. Rejections are disjoint: unsupported takes
precedence, then missing uncertainty, then above threshold. Coverage is
accepted rows / all input rows (null when there are no rows). Accepted known
and missing loss counts are reported separately; mean loss averages only the
known accepted losses and is null when none are known. These descriptive
metrics do not establish quality and are not an execution or policy gate.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from typing import Any


_LIMITATIONS = [
    "Offline descriptive sensitivity only; this diagnostic is not an execution gate or quality guarantee.",
    "Coverage uses all supplied rows as its denominator; results describe only the explicit input population.",
    "Mean loss uses accepted rows with known loss only; missing outcomes may bias that mean.",
]


def _number(value: Any, label: str, *, minimum: float, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}_must_be_finite_number")
    result = float(value)
    if not math.isfinite(result) or result < minimum or (maximum is not None and result > maximum):
        raise ValueError(f"{label}_out_of_range")
    return result


def _finite_mean(values: list[float]) -> float | None:
    """Average nonnegative finite values after scaling to avoid sum overflow."""
    if not values:
        return None
    scale = max(values)
    if scale == 0:
        return 0.0
    return (sum(value / scale for value in values) / len(values)) * scale


def analyze(payload: dict) -> dict:
    """Validate and analyze explicit thresholds and rows; input is not mutated."""
    if not isinstance(payload, dict):
        raise ValueError("payload_must_be_object")
    thresholds = payload.get("thresholds")
    rows = payload.get("rows")
    if not isinstance(thresholds, list) or not thresholds:
        raise ValueError("thresholds_must_be_nonempty_list")
    if not isinstance(rows, list):
        raise ValueError("rows_must_be_list")
    parsed_thresholds = [_number(t, "threshold", minimum=0, maximum=1) for t in thresholds]
    if len(set(parsed_thresholds)) != len(parsed_thresholds):
        raise ValueError("thresholds_must_be_distinct")

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("row_must_be_object")
        identity = row.get("id")
        if not isinstance(identity, str) or not identity:
            raise ValueError("row_id_must_be_nonempty_string")
        if identity in seen:
            raise ValueError("row_ids_must_be_unique")
        seen.add(identity)
        supported = row.get("supported")
        if not isinstance(supported, bool):
            raise ValueError("supported_must_be_boolean")
        uncertainty_value = row.get("uncertainty")
        uncertainty = None if uncertainty_value is None else _number(
            uncertainty_value, "uncertainty", minimum=0, maximum=1
        )
        loss_value = row.get("loss")
        loss = None if loss_value is None else _number(loss_value, "loss", minimum=0)
        normalized.append({"supported": supported, "uncertainty": uncertainty, "loss": loss})

    curve = []
    total = len(normalized)
    for threshold in sorted(parsed_thresholds):
        accepted = [
            row for row in normalized
            if row["supported"] and row["uncertainty"] is not None
            and row["uncertainty"] <= threshold
        ]
        unsupported = sum(not row["supported"] for row in normalized)
        missing_uncertainty = sum(
            row["supported"] and row["uncertainty"] is None for row in normalized
        )
        above = sum(
            row["supported"] and row["uncertainty"] is not None
            and row["uncertainty"] > threshold for row in normalized
        )
        known_losses = [row["loss"] for row in accepted if row["loss"] is not None]
        missing_losses = len(accepted) - len(known_losses)
        curve.append({
            "threshold": threshold,
            "accepted_count": len(accepted),
            "coverage": len(accepted) / total if total else None,
            "rejected": {
                "unsupported_count": unsupported,
                "missing_uncertainty_count": missing_uncertainty,
                "above_threshold_count": above,
            },
            "accepted_known_loss_count": len(known_losses),
            "accepted_missing_loss_count": missing_losses,
            "mean_loss": _finite_mean(known_losses),
        })

    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "abstention_frontier",
        "input_sha256": hashlib.sha256(canonical).hexdigest(),
        "summary": {"row_count": total, "threshold_count": len(curve)},
        "details": curve,
        "limitations": list(_LIMITATIONS),
    }


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite_json_number")


def main() -> int:
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant)
        result = analyze(payload)
        json.dump(result, sys.stdout, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
