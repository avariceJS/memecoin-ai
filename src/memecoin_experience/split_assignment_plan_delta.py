"""Compare two caller-declared row split assignment plans."""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any


_ERROR = "split_assignment_plan_delta_invalid_input"
_SPLITS = ("train", "validation", "test")
_STATES = (*_SPLITS, "unassigned")
_MAX_ROWS = 5000
_MAX_ASSIGNMENTS = 10000
_MAX_GROUPS = 5000
_MAX_GROUP_MEMBERSHIPS = 10000
_MAX_INPUT_BYTES = 2_000_000


def _invalid() -> ValueError:
    return ValueError(_ERROR)


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 64 and value.isascii() and value.isprintable()


def analyze(payload: dict) -> dict:
    """Return deterministic plan deltas without loading or inferring population data."""
    if type(payload) is not dict or set(payload) not in (
        {"row_ids", "before", "after"}, {"row_ids", "before", "after", "groups"}
    ):
        raise _invalid()
    row_ids, before_records, after_records = payload["row_ids"], payload["before"], payload["after"]
    if type(row_ids) is not list or type(before_records) is not list or type(after_records) is not list:
        raise _invalid()
    if len(row_ids) > _MAX_ROWS or len(before_records) > _MAX_ASSIGNMENTS or len(after_records) > _MAX_ASSIGNMENTS:
        raise _invalid()
    if any(not _identifier(row_id) for row_id in row_ids) or len(set(row_ids)) != len(row_ids):
        raise _invalid()
    universe = set(row_ids)

    def parse_plan(records: list) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        for record in records:
            if type(record) is not dict or set(record) != {"row_id", "split"}:
                raise _invalid()
            row_id, split = record["row_id"], record["split"]
            if not _identifier(row_id) or row_id not in universe or row_id in result:
                raise _invalid()
            if split is not None and (type(split) is not str or split not in _SPLITS):
                raise _invalid()
            result[row_id] = split
        return result

    before, after = parse_plan(before_records), parse_plan(after_records)
    group_declarations = None
    if "groups" in payload:
        raw_groups = payload["groups"]
        if type(raw_groups) is not list or len(raw_groups) > _MAX_GROUPS:
            raise _invalid()
        group_declarations = {}
        membership_count = 0
        for declaration in raw_groups:
            if type(declaration) is not dict or set(declaration) != {"group_id", "row_ids"}:
                raise _invalid()
            group_id, members = declaration["group_id"], declaration["row_ids"]
            if not _identifier(group_id) or group_id in group_declarations or type(members) is not list:
                raise _invalid()
            if any(not _identifier(row_id) or row_id not in universe for row_id in members):
                raise _invalid()
            if len(set(members)) != len(members):
                raise _invalid()
            membership_count += len(members)
            if membership_count > _MAX_GROUP_MEMBERSHIPS:
                raise _invalid()
            group_declarations[group_id] = sorted(members)
    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise _invalid() from exc
    if len(canonical) > _MAX_INPUT_BYTES:
        raise _invalid()

    before_missing = sorted(universe - before.keys())
    after_missing = sorted(universe - after.keys())
    categories = {"same": [], "moved": [], "added": [], "removed": [], "missing_both": []}
    move_rows: dict[tuple[str, str], list[str]] = {}
    for row_id in sorted(universe):
        in_before, in_after = row_id in before, row_id in after
        if not in_before and not in_after:
            categories["missing_both"].append(row_id)
        elif not in_before:
            categories["added"].append(row_id)
        elif not in_after:
            categories["removed"].append(row_id)
        elif before[row_id] == after[row_id]:
            categories["same"].append(row_id)
        else:
            categories["moved"].append(row_id)
            old = before[row_id] if before[row_id] is not None else "unassigned"
            new = after[row_id] if after[row_id] is not None else "unassigned"
            move_rows.setdefault((old, new), []).append(row_id)

    move_matrix = [
        {"from_split": old, "to_split": new, "row_count": len(ids), "row_ids": ids}
        for old in _STATES for new in _STATES
        if old != new and (ids := move_rows.get((old, new)))
    ]
    split_counts = {
        split: {
            "before_count": sum(value == split for value in before.values()),
            "after_count": sum(value == split for value in after.values()),
        }
        for split in _SPLITS
    }
    for counts in split_counts.values():
        counts["delta"] = counts["after_count"] - counts["before_count"]

    result = {
        "schema": "brain-research-diagnostic.v1",
        "kind": "split_assignment_plan_delta",
        "input_sha256": hashlib.sha256(canonical).hexdigest(),
        "summary": {
            "declared_row_count": len(universe),
            "before_assignment_count": len(before),
            "after_assignment_count": len(after),
            "same_plan_denominator": len(categories["same"]),
            "moved_assignment_denominator": len(categories["moved"]),
            "added_assignment_denominator": len(categories["added"]),
            "removed_assignment_denominator": len(categories["removed"]),
            "missing_before_denominator": len(before_missing),
            "missing_after_denominator": len(after_missing),
            "missing_both_denominator": len(categories["missing_both"]),
            "explicitly_unassigned_before_count": sum(value is None for value in before.values()),
            "explicitly_unassigned_after_count": sum(value is None for value in after.values()),
        },
        "details": {
            "same_plan_row_ids": categories["same"],
            "moved_row_ids": categories["moved"],
            "added_row_ids": categories["added"],
            "removed_row_ids": categories["removed"],
            "missing_before_row_ids": before_missing,
            "missing_after_row_ids": after_missing,
            "missing_both_row_ids": categories["missing_both"],
            "directional_move_matrix": move_matrix,
            "split_counts": split_counts,
        },
        "limitations": [
            "The declared row universe and both assignment plans are caller claims and are not independently verified.",
            "An omitted assignment record is missing; a record with a null split is explicitly unassigned. No missing rows or assignments are inferred.",
            "Counts and row moves describe only the supplied declarations and do not establish population completeness, split validity, or scientific qualification.",
        ],
    }
    if group_declarations is not None:
        grouped_rows = {row_id for members in group_declarations.values() for row_id in members}
        undeclared_rows = sorted(universe - grouped_rows)

        def group_plan(group_id: str, members: list[str], plan: dict[str, str | None]) -> dict:
            present = [row_id for row_id in members if row_id in plan]
            splits = sorted({plan[row_id] for row_id in present if plan[row_id] is not None}, key=_SPLITS.index)
            missing_count = len(members) - len(present)
            unassigned_count = sum(plan[row_id] is None for row_id in present)
            if not members:
                status = "no_support"
            elif len(splits) > 1:
                status = "fragmented"
            elif len(splits) == 1:
                status = "contained"
            elif missing_count:
                status = "incomplete"
            else:
                status = "unassigned"
            return {
                "group_id": group_id,
                "declared_row_count": len(members),
                "assigned_row_count": len(present),
                "missing_assignment_count": missing_count,
                "explicitly_unassigned_count": unassigned_count,
                "observed_splits": splits,
                "status": status,
            }

        before_groups = {group_id: group_plan(group_id, members, before) for group_id, members in group_declarations.items()}
        after_groups = {group_id: group_plan(group_id, members, after) for group_id, members in group_declarations.items()}
        changes = []
        for group_id in sorted(group_declarations):
            old, new = before_groups[group_id], after_groups[group_id]
            if old["status"] == new["status"] and old["observed_splits"] == new["observed_splits"]:
                change = "unchanged"
            elif old["status"] == "contained" and new["status"] == "fragmented":
                change = "contained_to_fragmented"
            elif old["status"] == "fragmented" and new["status"] == "contained":
                change = "fragmented_to_contained"
            elif old["status"] == "contained" and new["status"] == "contained":
                change = "contained_split_changed"
            else:
                change = "status_or_splits_changed"
            changes.append({"group_id": group_id, "change": change, "before": old, "after": new})

        def group_counts(groups: dict[str, dict]) -> dict[str, int]:
            return {
                "declared_group_count": len(groups),
                "supported_group_count": sum(item["declared_row_count"] > 0 for item in groups.values()),
                "zero_support_group_count": sum(item["declared_row_count"] == 0 for item in groups.values()),
                "contained_group_count": sum(item["status"] == "contained" for item in groups.values()),
                "fragmented_group_count": sum(item["status"] == "fragmented" for item in groups.values()),
                "incomplete_group_count": sum(item["status"] == "incomplete" for item in groups.values()),
                "unassigned_group_count": sum(item["status"] == "unassigned" for item in groups.values()),
                "missing_assignment_denominator": sum(item["missing_assignment_count"] for item in groups.values()),
            }

        result["group_comparison"] = {
            "summary": {
                "declared_group_count": len(group_declarations),
                "declared_membership_count": membership_count,
                "undeclared_row_count": len(undeclared_rows),
                "before": group_counts(before_groups),
                "after": group_counts(after_groups),
                "contained_to_fragmented_count": sum(item["change"] == "contained_to_fragmented" for item in changes),
                "fragmented_to_contained_count": sum(item["change"] == "fragmented_to_contained" for item in changes),
                "changed_group_count": sum(item["change"] != "unchanged" for item in changes),
            },
            "details": {
                "undeclared_row_ids": undeclared_rows,
                "zero_support_group_ids": sorted(group_id for group_id, members in group_declarations.items() if not members),
                "before_groups": [before_groups[group_id] for group_id in sorted(before_groups)],
                "after_groups": [after_groups[group_id] for group_id in sorted(after_groups)],
                "changes": changes,
            },
            "limitations": [
                "Group membership and assignments are caller declarations; group membership is not inferred.",
                "Only supplied group identifiers are compared; groups absent from the declaration cannot be enumerated.",
                "Containment and fragmentation describe only observed non-null assignments; missing and explicitly unassigned rows are reported separately.",
                "Group comparisons do not establish leakage, population completeness, split validity, or scientific qualification.",
            ],
        }
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise _invalid()


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise _invalid()
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            raise _invalid()
        payload = json.loads(raw.decode("utf-8", errors="strict"), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
        output = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        sys.stderr.write('{"error":"split_assignment_plan_delta_invalid_input"}\n')
        return 2
    sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
