"""Prepare fresh standalone payloads from one explicit evidence and loss roster."""

from __future__ import annotations

from typing import Any

from . import abstention_frontier, research_evidence_gate


_PAYLOAD_KEYS = {"policy", "thresholds", "rows"}
_POLICY_KEYS = {"max_input_age_ns", "max_uncertainty"}
_GATE_ROW_KEYS = {
    "id",
    "decision_ns",
    "input_available_ns",
    "source_status",
    "forecast_status",
    "uncertainty",
}
_ROSTER_ROW_KEYS = _GATE_ROW_KEYS | {"loss"}


def _exact_keys(value: dict, expected: set[str], label: str) -> None:
    if value.keys() != expected:
        raise ValueError(f"{label}_keys_must_match_exactly")


def prepare(payload: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a unified roster and return independent gate/frontier inputs.

    The gate payload keeps the frozen policy and all eligibility fields. The
    frontier payload includes every roster row, initially supported, so the
    caller can apply the gate result without losing rejected rows or order.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload_must_be_object")
    _exact_keys(payload, _PAYLOAD_KEYS, "payload")

    policy = payload["policy"]
    if not isinstance(policy, dict):
        raise ValueError("policy_must_be_object")
    _exact_keys(policy, _POLICY_KEYS, "policy")

    thresholds = payload["thresholds"]
    rows = payload["rows"]
    if not isinstance(rows, list):
        raise ValueError("rows_must_be_list")

    gate_rows: list[dict[str, Any]] = []
    frontier_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("row_must_be_object")
        _exact_keys(row, _ROSTER_ROW_KEYS, "row")
        gate_rows.append({key: row[key] for key in _GATE_ROW_KEYS})
        frontier_rows.append({
            "id": row["id"],
            "supported": True,
            "uncertainty": row["uncertainty"],
            "loss": row["loss"],
        })

    gate_payload = {
        "policy": {
            "max_input_age_ns": policy.get("max_input_age_ns"),
            "max_uncertainty": policy.get("max_uncertainty"),
        },
        "rows": gate_rows,
    }
    frontier_payload = {"thresholds": list(thresholds) if isinstance(thresholds, list) else thresholds,
                        "rows": frontier_rows}

    # These public analyzers own the existing range, type, uniqueness, and
    # finite-number rules. Run both so every roster value is validated even
    # when its row would be ineligible under the policy.
    try:
        research_evidence_gate.analyze(gate_payload)
        abstention_frontier.analyze(frontier_payload)
    except OverflowError:
        # float(very_large_int) in the frontier analyzer can overflow before
        # it reaches its normal ValueError validation path.
        raise ValueError("numeric_value_out_of_range") from None

    return gate_payload, frontier_payload
