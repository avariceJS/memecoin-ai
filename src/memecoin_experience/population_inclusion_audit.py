"""Audit explicit row inclusion decisions against a pinned population snapshot.

This module is a pure, offline summary. It does not infer rows or load data,
models, or runtime facilities.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_SNAPSHOT_KEYS = {"snapshot_id", "snapshot_sha256", "row_ids"}
_DECISION_KEYS = {"row_id", "group_id", "disposition", "reason"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def _nonempty_string(value: Any, code: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"population_inclusion_audit_{code}")
    return value


def analyze(snapshot: dict, decisions: list) -> dict:
    """Validate and summarize decisions for every row in ``snapshot``."""
    if type(snapshot) is not dict or set(snapshot) != _SNAPSHOT_KEYS:
        raise ValueError("population_inclusion_audit_snapshot")
    snapshot_id = _nonempty_string(snapshot["snapshot_id"], "snapshot_id")
    snapshot_sha256 = snapshot["snapshot_sha256"]
    if type(snapshot_sha256) is not str or _SHA256.fullmatch(snapshot_sha256) is None:
        raise ValueError("population_inclusion_audit_snapshot_sha256")
    row_ids = snapshot["row_ids"]
    if type(row_ids) is not list:
        raise ValueError("population_inclusion_audit_row_ids")
    checked_rows = [_nonempty_string(row_id, "row_id") for row_id in row_ids]
    if len(set(checked_rows)) != len(checked_rows):
        raise ValueError("population_inclusion_audit_duplicate_snapshot_row")
    known_rows = set(checked_rows)

    if type(decisions) is not list:
        raise ValueError("population_inclusion_audit_decisions")
    checked: list[dict[str, str | None]] = []
    decision_rows: set[str] = set()
    for decision in decisions:
        if type(decision) is not dict or set(decision) != _DECISION_KEYS:
            raise ValueError("population_inclusion_audit_decision")
        row_id = _nonempty_string(decision["row_id"], "decision_row_id")
        group_id = _nonempty_string(decision["group_id"], "group_id")
        disposition = decision["disposition"]
        if disposition not in ("included", "excluded") or type(disposition) is not str:
            raise ValueError("population_inclusion_audit_disposition")
        reason = decision["reason"]
        if disposition == "excluded":
            _nonempty_string(reason, "exclusion_reason")
        elif reason is not None and type(reason) is not str:
            raise ValueError("population_inclusion_audit_reason")
        elif type(reason) is str and reason == "":
            raise ValueError("population_inclusion_audit_reason")
        if row_id in decision_rows:
            raise ValueError("population_inclusion_audit_duplicate_decision_row")
        decision_rows.add(row_id)
        checked.append({"row_id": row_id, "group_id": group_id,
                        "disposition": disposition, "reason": reason})

    if decision_rows - known_rows:
        raise ValueError("population_inclusion_audit_extra_decision_row")
    if known_rows - decision_rows:
        raise ValueError("population_inclusion_audit_missing_decision_row")

    rows = sorted(checked, key=lambda item: item["row_id"])
    canonical_input = {
        "snapshot": {"snapshot_id": snapshot_id, "snapshot_sha256": snapshot_sha256,
                     "row_ids": sorted(checked_rows)},
        "decisions": rows,
    }
    try:
        encoded = json.dumps(canonical_input, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("population_inclusion_audit_json_value") from exc

    grouped: dict[str, list[dict[str, str | None]]] = {}
    for row in rows:
        grouped.setdefault(row["group_id"], []).append(row)
    group_summaries = []
    for group_id in sorted(grouped):
        group_rows = grouped[group_id]
        included_count = sum(row["disposition"] == "included" for row in group_rows)
        excluded_count = len(group_rows) - included_count
        partial = included_count > 0 and excluded_count > 0
        group_summaries.append({
            "group_id": group_id,
            "row_count": len(group_rows),
            "included_count": included_count,
            "excluded_count": excluded_count,
            "included_row_count": included_count,
            "excluded_row_count": excluded_count,
            "disposition": "included" if excluded_count == 0 else "excluded",
            "status": "partial" if partial else ("included" if excluded_count == 0 else "excluded"),
        })

    included_rows = sum(row["disposition"] == "included" for row in rows)
    excluded_rows = len(rows) - included_rows
    included_groups = sum(group["status"] == "included" for group in group_summaries)
    partial_groups = sum(group["status"] == "partial" for group in group_summaries)
    excluded_groups = len(group_summaries) - included_groups
    row_denominators = {"before": len(checked_rows), "after": included_rows}
    group_denominators = {"before": len(group_summaries), "after": included_groups}
    return {
        "schema": "brain-population-inclusion-audit.v1",
        "snapshot": {"snapshot_id": snapshot_id, "snapshot_sha256": snapshot_sha256},
        "snapshot_pin": {"snapshot_id": snapshot_id, "snapshot_sha256": snapshot_sha256},
        "canonical_input_sha256": hashlib.sha256(encoded).hexdigest(),
        "input_sha256": hashlib.sha256(encoded).hexdigest(),
        "rows": rows,
        "groups": group_summaries,
        "row_denominators": row_denominators,
        "group_denominators": group_denominators,
        "denominators": {
            "rows": {"before": len(checked_rows), "after": included_rows,
                     "included": included_rows, "excluded": excluded_rows},
            "groups": {"before": len(group_summaries), "after": included_groups,
                       "included": included_groups, "excluded": excluded_groups,
                       "partial": partial_groups},
        },
    }
