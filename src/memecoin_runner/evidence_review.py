"""Compose the existing evidence gate and abstention frontier for offline review."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import abstention_frontier, research_evidence_gate


_LIMITATIONS = [
    "Eligibility is frozen by the supplied gate policy; a higher sensitivity threshold cannot recover gate-ineligible rows.",
    "Statuses, times, policy, and thresholds are caller assertions and are not independently verified.",
    "Missing loss outcomes may bias the mean loss, which includes known accepted losses only.",
    "This offline review does not qualify data, establish a live policy, recommend a threshold, or authorize execution or promotion.",
]


def _prepare(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Lazily call the sibling slice's unified roster validator."""
    from . import evidence_review_roster  # type: ignore[import-not-found]

    return evidence_review_roster.prepare(payload)


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """Analyze unified caller-supplied eligibility and loss sensitivity evidence."""
    gate_payload, frontier_payload = _prepare(payload)
    gate = research_evidence_gate.analyze(gate_payload)
    gate_status = {
        item["id"]: item["status"] == "eligible_for_research_review"
        for item in gate["details"]
    }

    # The helper returns fresh dictionaries. Copy the frontier rows again so
    # composing the gate result cannot mutate a producer envelope or caller data.
    frontier_input = {
        **frontier_payload,
        "rows": [
            {**row, "supported": gate_status[row["id"]]}
            for row in frontier_payload["rows"]
        ],
    }
    frontier = abstention_frontier.analyze(frontier_input)

    roster_rows = payload["rows"]
    eligible_ids = {identity for identity, eligible in gate_status.items() if eligible}
    def has_loss(row: dict[str, Any]) -> bool:
        return row["loss"] is not None

    details = []
    for point in frontier["details"]:
        accepted_ids = [
            row["id"] for row in roster_rows
            if row["id"] in eligible_ids
            and row["uncertainty"] is not None
            and row["uncertainty"] <= point["threshold"]
        ]
        eligible_count = len(eligible_ids)
        details.append({
            **point,
            "eligible_count": eligible_count,
            "ineligible_count": len(roster_rows) - eligible_count,
            "coverage_of_eligible": len(accepted_ids) / eligible_count if eligible_count else None,
            "accepted_ids": accepted_ids,
        })

    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    eligible_known_roster = sum(row["id"] in eligible_ids and has_loss(row) for row in roster_rows)
    eligible_missing_roster = sum(
        row["id"] in eligible_ids and not has_loss(row) for row in roster_rows
    )
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "research_evidence_review",
        "input_sha256": hashlib.sha256(canonical).hexdigest(),
        "summary": {
            "row_count": len(roster_rows),
            "eligible_count": len(eligible_ids),
            "ineligible_count": len(roster_rows) - len(eligible_ids),
            "threshold_count": len(details),
            "roster_known_loss_count": sum(has_loss(row) for row in roster_rows),
            "roster_missing_loss_count": sum(not has_loss(row) for row in roster_rows),
            "eligible_known_loss_count": eligible_known_roster,
            "eligible_missing_loss_count": eligible_missing_roster,
        },
        "details": details,
        "gate": gate,
        "frontier": frontier,
        "limitations": list(_LIMITATIONS),
    }
