"""Describe concordance between two caller-supplied row partitions.

The declared row universe and both partition assignments are explicit inputs.
Missing assignment records and records with a null group label are counted
separately. Group names are treated as opaque labels; no scientific meaning is
inferred from them.
"""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any


_ERROR = "group_partition_concordance_invalid_input"
_MAX_ROWS = 5000
_MAX_ASSIGNMENTS = 10000
_MAX_INPUT_BYTES = 2_000_000


def _invalid() -> ValueError:
    return ValueError(_ERROR)


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 64 and value.isascii() and value.isprintable()


def analyze(payload: dict) -> dict:
    """Return a deterministic descriptive comparison of two explicit partitions."""
    if type(payload) is not dict or set(payload) != {"row_ids", "left", "right"}:
        raise _invalid()
    row_ids, left_rows, right_rows = payload["row_ids"], payload["left"], payload["right"]
    if type(row_ids) is not list or type(left_rows) is not list or type(right_rows) is not list:
        raise _invalid()
    if len(row_ids) > _MAX_ROWS or len(left_rows) > _MAX_ASSIGNMENTS or len(right_rows) > _MAX_ASSIGNMENTS:
        raise _invalid()
    if any(not _identifier(row_id) for row_id in row_ids) or len(set(row_ids)) != len(row_ids):
        raise _invalid()
    universe = set(row_ids)

    def parse_partition(items: list) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        for item in items:
            if type(item) is not dict or set(item) != {"row_id", "group"}:
                raise _invalid()
            row_id, group = item["row_id"], item["group"]
            if not _identifier(row_id) or row_id not in universe or row_id in result:
                raise _invalid()
            if group is not None and not _identifier(group):
                raise _invalid()
            result[row_id] = group
        return result

    left, right = parse_partition(left_rows), parse_partition(right_rows)
    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise _invalid() from exc

    left_missing = sorted(universe - left.keys())
    right_missing = sorted(universe - right.keys())
    left_unassigned = sorted(row_id for row_id, group in left.items() if group is None)
    right_unassigned = sorted(row_id for row_id, group in right.items() if group is None)
    jointly_assigned = sorted(row_id for row_id in universe if left.get(row_id) is not None and right.get(row_id) is not None)

    cells: dict[tuple[str, str], int] = {}
    left_sizes: dict[str, int] = {}
    right_sizes: dict[str, int] = {}
    for row_id in jointly_assigned:
        lgroup, rgroup = left[row_id], right[row_id]
        assert lgroup is not None and rgroup is not None
        cells[(lgroup, rgroup)] = cells.get((lgroup, rgroup), 0) + 1
        left_sizes[lgroup] = left_sizes.get(lgroup, 0) + 1
        right_sizes[rgroup] = right_sizes.get(rgroup, 0) + 1

    left_labels, right_labels = sorted(left_sizes), sorted(right_sizes)
    contingency = [
        {"left_group": lgroup, "right_group": rgroup, "row_count": cells[(lgroup, rgroup)]}
        for lgroup in left_labels for rgroup in right_labels if cells.get((lgroup, rgroup), 0)
    ]
    alignment = []
    aligned_count = 0
    for lgroup in left_labels:
        options = [(cells.get((lgroup, rgroup), 0), rgroup) for rgroup in right_labels]
        count, matched = min(((-n, label) for n, label in options)) if options else (0, "")
        count = -count
        aligned_count += count
        alignment.append({"left_group": lgroup, "best_matching_right_group": matched if count else None, "matched_row_count": count})

    n = len(jointly_assigned)
    pair_denominator = n * (n - 1) // 2
    choose_two = lambda size: size * (size - 1) // 2
    same_same = sum(choose_two(count) for count in cells.values())
    same_left = sum(choose_two(count) for count in left_sizes.values())
    same_right = sum(choose_two(count) for count in right_sizes.values())
    same_different = same_left - same_same
    different_same = same_right - same_same
    different_different = pair_denominator - same_same - same_different - different_same

    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "group_partition_concordance",
        "input_sha256": hashlib.sha256(canonical).hexdigest(),
        "summary": {
            "declared_row_count": len(universe),
            "left_assignment_record_count": len(left), "right_assignment_record_count": len(right),
            "left_missing_row_count": len(left_missing), "right_missing_row_count": len(right_missing),
            "left_unassigned_row_count": len(left_unassigned), "right_unassigned_row_count": len(right_unassigned),
            "jointly_assigned_row_count": n,
            "left_group_count": len(left_sizes), "right_group_count": len(right_sizes),
            "pairwise_denominator": pair_denominator,
            "pairwise_agreement_count": same_same + different_different,
            "pairwise_agreement_rate": (same_same + different_different) / pair_denominator if pair_denominator else None,
            "alignment_denominator": n,
            "alignment_count": aligned_count,
            "alignment_rate": aligned_count / n if n else None,
        },
        "details": {
            "left_missing_row_ids": left_missing, "right_missing_row_ids": right_missing,
            "left_unassigned_row_ids": left_unassigned, "right_unassigned_row_ids": right_unassigned,
            "jointly_assigned_row_ids": jointly_assigned,
            "contingency_cells": contingency, "left_to_best_right_alignment": alignment,
            "pairwise_cells": {
                "same_in_both": same_same, "same_left_different_right": same_different,
                "different_left_same_right": different_same, "different_in_both": different_different,
            },
        },
        "limitations": [
            "Row identities, group labels, partitions and declared universe are caller claims and are not independently verified.",
            "Pairwise agreement uses only unordered pairs among rows assigned to a non-null group on both sides; its denominator is n*(n-1)/2.",
            "Alignment maps each left group independently to its most frequent right group, so multiple left groups may match one right group; this descriptive score is label-name invariant but is not a one-to-one cluster matching.",
            "Counts and rates describe only the supplied row universe and do not establish population completeness or scientific validity.",
        ],
    }


def _reject_constant(_value: str) -> None:
    raise _invalid()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


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
        sys.stderr.write('{"error":"group_partition_concordance_invalid_input"}\n')
        return 2
    sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
