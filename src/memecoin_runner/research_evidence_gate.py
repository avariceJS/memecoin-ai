"""Offline per-row abstention gate for explicit research evidence.

The caller supplies a frozen policy and rows. This module validates those
assertions and reports whether each row may enter offline research review; it
does not create forecasts or affect runtime behavior.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from typing import Any


_TOP_LEVEL_KEYS = {"policy", "rows"}
_POLICY_KEYS = {"max_input_age_ns", "max_uncertainty"}
_ROW_KEYS = {
    "id",
    "decision_ns",
    "input_available_ns",
    "source_status",
    "forecast_status",
    "uncertainty",
}
_REASONS = (
    "source_unknown",
    "source_unsupported",
    "forecast_missing",
    "forecast_unsupported",
    "availability_unknown",
    "not_available_by_decision",
    "input_stale",
    "uncertainty_unknown",
    "uncertainty_over_limit",
)
_LIMITATIONS = [
    "Statuses, times, and thresholds are caller assertions.",
    "This is not a live, runtime, or trading gate and does not prove data quality or predictive competence.",
]
_MAX_NS = 10**18


def _exact_keys(value: dict, expected: set[str], label: str) -> None:
    if value.keys() != expected:
        raise ValueError(f"{label}_keys_must_match_exactly")


def _plain_int(value: Any, label: str, *, maximum: int) -> int:
    if type(value) is not int:
        raise ValueError(f"{label}_must_be_plain_integer")
    if value < 0 or value > maximum:
        raise ValueError(f"{label}_out_of_range")
    return value


def _unit_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}_must_be_finite_number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{label}_out_of_range") from None
    if not math.isfinite(result) or result < 0 or result > 1:
        raise ValueError(f"{label}_out_of_range")
    return result


def _parse(payload: Any) -> tuple[int, float, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise ValueError("payload_must_be_object")
    _exact_keys(payload, _TOP_LEVEL_KEYS, "payload")

    policy = payload["policy"]
    if not isinstance(policy, dict):
        raise ValueError("policy_must_be_object")
    _exact_keys(policy, _POLICY_KEYS, "policy")
    max_age = _plain_int(policy["max_input_age_ns"], "max_input_age_ns", maximum=_MAX_NS)
    max_uncertainty = _unit_number(policy["max_uncertainty"], "max_uncertainty")

    rows = payload["rows"]
    if not isinstance(rows, list):
        raise ValueError("rows_must_be_list")
    if len(rows) > 10_000:
        raise ValueError("rows_exceed_limit")

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("row_must_be_object")
        _exact_keys(row, _ROW_KEYS, "row")

        identity = row["id"]
        if not isinstance(identity, str) or not identity:
            raise ValueError("row_id_must_be_nonempty_string")
        if identity in seen:
            raise ValueError("row_ids_must_be_unique")
        seen.add(identity)

        decision = _plain_int(row["decision_ns"], "decision_ns", maximum=_MAX_NS)
        available_value = row["input_available_ns"]
        available = None if available_value is None else _plain_int(
            available_value, "input_available_ns", maximum=_MAX_NS
        )
        source = row["source_status"]
        if source not in ("qualified", "unknown", "unsupported"):
            raise ValueError("source_status_invalid")
        forecast = row["forecast_status"]
        if forecast not in ("available", "missing", "unsupported"):
            raise ValueError("forecast_status_invalid")
        uncertainty_value = row["uncertainty"]
        uncertainty = None if uncertainty_value is None else _unit_number(
            uncertainty_value, "uncertainty"
        )
        normalized.append({
            "id": identity,
            "decision_ns": decision,
            "input_available_ns": available,
            "source_status": source,
            "forecast_status": forecast,
            "uncertainty": uncertainty,
        })
    return max_age, max_uncertainty, normalized


def analyze(payload: dict) -> dict:
    """Validate and classify caller-supplied evidence without mutating it."""
    max_age, max_uncertainty, rows = _parse(payload)
    counts = {reason: 0 for reason in _REASONS}
    details: list[dict[str, Any]] = []

    for row in rows:
        reasons: list[str] = []
        if row["source_status"] == "unknown":
            reasons.append("source_unknown")
        if row["source_status"] == "unsupported":
            reasons.append("source_unsupported")
        if row["forecast_status"] == "missing":
            reasons.append("forecast_missing")
        if row["forecast_status"] == "unsupported":
            reasons.append("forecast_unsupported")

        available = row["input_available_ns"]
        decision = row["decision_ns"]
        if available is None:
            reasons.append("availability_unknown")
        elif available > decision:
            reasons.append("not_available_by_decision")
        elif decision - available > max_age:
            reasons.append("input_stale")

        uncertainty = row["uncertainty"]
        if uncertainty is None:
            reasons.append("uncertainty_unknown")
        elif uncertainty > max_uncertainty:
            reasons.append("uncertainty_over_limit")

        for reason in reasons:
            counts[reason] += 1
        details.append({
            "id": row["id"],
            "status": "eligible_for_research_review" if not reasons else "abstain",
            "reasons": reasons,
        })

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    summary = {
        "row_count": len(rows),
        "eligible_count": sum(not item["reasons"] for item in details),
        "abstain_count": sum(bool(item["reasons"]) for item in details),
    }
    summary.update({f"{reason}_count": counts[reason] for reason in _REASONS})
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "research_evidence_gate",
        "input_sha256": hashlib.sha256(canonical).hexdigest(),
        "summary": summary,
        "details": details,
        "limitations": list(_LIMITATIONS),
    }


def _reject_constant(value: str) -> None:
    raise ValueError("nonfinite_json_number")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
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
        json.dump(result, sys.stdout, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
