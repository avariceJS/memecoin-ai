"""Audit explicit row assignments for duplicate, missing, unknown, and group leakage findings."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Any


_ERROR = "group_split_allocation_audit_invalid_input"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", re.ASCII)
_LIMITATIONS = [
    "This audits only the explicitly supplied row/group declarations and allocation records; it cannot establish that they describe a real or complete population.",
    "A clean allocation does not establish quota feasibility, temporal ordering, maturity, representativeness, independence, data-access or training qualification.",
    "Duplicate, unknown, missing and leakage counts use the explicit declared rows and assignment records; omitted source rows cannot be inferred.",
]


def _invalid() -> ValueError:
    return ValueError(_ERROR)


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None


def analyze(payload: dict) -> dict:
    """Validate and audit caller-supplied declarations without changing them."""
    if type(payload) is not dict or set(payload) != {"rows", "assignments"}:
        raise _invalid()
    rows = payload["rows"]
    assignments = payload["assignments"]
    if type(rows) is not list or type(assignments) is not list:
        raise _invalid()
    if len(rows) > 10000 or len(assignments) > 20000:
        raise _invalid()

    row_group: dict[str, str] = {}
    group_rows: dict[str, list[str]] = {}
    for row in rows:
        if type(row) is not dict or set(row) != {"id", "group_id"}:
            raise _invalid()
        row_id, group_id = row["id"], row["group_id"]
        if not _valid_id(row_id) or not _valid_id(group_id) or row_id in row_group:
            raise _invalid()
        row_group[row_id] = group_id
        group_rows.setdefault(group_id, []).append(row_id)

    normalized_assignments: list[tuple[int, str, str]] = []
    for index, assignment in enumerate(assignments):
        if type(assignment) is not dict or set(assignment) != {"row_id", "split"}:
            raise _invalid()
        row_id, split = assignment["row_id"], assignment["split"]
        if not _valid_id(row_id) or not _valid_id(split):
            raise _invalid()
        normalized_assignments.append((index, row_id, split))

    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    input_sha256 = hashlib.sha256(canonical).hexdigest()

    records: dict[str, list[str]] = {}
    unknown_assignments: list[dict[str, Any]] = []
    for index, row_id, split in normalized_assignments:
        if row_id not in row_group:
            unknown_assignments.append({"assignment_index": index, "row_id": row_id, "split": split})
        else:
            records.setdefault(row_id, []).append(split)

    missing_row_ids = sorted(set(row_group) - set(records))
    duplicate_assignments = [
        {
            "row_id": row_id,
            "assignment_count": len(splits),
            "extra_assignment_count": len(splits) - 1,
        }
        for row_id, splits in sorted(records.items())
        if len(splits) > 1
    ]
    split_by_group: dict[str, set[str]] = {}
    for row_id, splits in records.items():
        split_by_group.setdefault(row_group[row_id], set()).update(splits)
    group_leakage = [
        {
            "group_id": group_id,
            "split_labels": sorted(labels),
            "row_ids": sorted(group_rows[group_id]),
        }
        for group_id, labels in sorted(split_by_group.items())
        if len(labels) > 1
    ]

    expected_count = len(row_group)
    assigned_unique_count = len(records)
    missing_count = len(missing_row_ids)
    duplicate_row_count = len(duplicate_assignments)
    duplicate_extra_count = sum(item["extra_assignment_count"] for item in duplicate_assignments)
    unknown_count = len(unknown_assignments)
    group_count = len(group_rows)
    leaked_group_count = len(group_leakage)
    affected_row_count = sum(len(item["row_ids"]) for item in group_leakage)
    row_summary = {
        "expected_row_count": expected_count,
        "assigned_unique_row_count": assigned_unique_count,
        "missing_row_count": missing_count,
        "duplicate_row_id_count": duplicate_row_count,
        "duplicate_extra_assignment_count": duplicate_extra_count,
        "unknown_assignment_count": unknown_count,
        "group_count": group_count,
    }
    leakage_summary = {
        "checked_group_count": group_count,
        "leaked_group_count": leaked_group_count,
        "affected_row_count": affected_row_count,
    }
    findings = any((missing_count, duplicate_row_count, unknown_count, leaked_group_count))
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "group_split_allocation_audit",
        "input_sha256": input_sha256,
        "summary": {
            "status": "findings" if findings else "clean",
            "expected_row_count": expected_count,
            "assigned_unique_row_count": assigned_unique_count,
            "missing_row_count": missing_count,
            "duplicate_row_id_count": duplicate_row_count,
            "duplicate_extra_assignment_count": duplicate_extra_count,
            "unknown_assignment_count": unknown_count,
            "group_count": group_count,
            "leaked_group_count": leaked_group_count,
        },
        "details": {
            "missing_row_ids": missing_row_ids,
            "duplicate_assignments": duplicate_assignments,
            "unknown_assignments": sorted(unknown_assignments, key=lambda item: item["assignment_index"]),
            "group_leakage": group_leakage,
            "row_assignment_summary": row_summary,
            "group_leakage_summary": leakage_summary,
        },
        "limitations": list(_LIMITATIONS),
    }


def _reject_constant(_value: str) -> None:
    raise _invalid()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise _invalid()
        raw = sys.stdin.buffer.read()
        payload = json.loads(
            raw.decode("utf-8", errors="strict"),
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
        result = analyze(payload)
        output = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        sys.stderr.write('{"error":"group_split_allocation_audit_invalid_input"}\n')
        return 2
    sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
