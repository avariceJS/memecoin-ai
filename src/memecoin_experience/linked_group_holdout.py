"""Partition rows into entity split candidates closed over shared groups.

The input is exactly ``{"rows": [...], "holdout_groups": [...]}``. Each row
is exactly ``{"id": nonempty_string, "groups": [nonempty_string, ...]}``;
row ids and per-row group labels are unique. Holdout groups are unique and must
occur in at least one row. Rows connected by a chain of shared groups belong
to the same component. A component is holdout when any of its groups is
nominated, and otherwise is train. Output is exactly a schema label,
lexicographically sorted ``train_ids`` and ``holdout_ids``, and components
sorted by their smallest row id; each component has sorted ``row_ids``, sorted
``groups``, and role ``train`` or ``holdout``.

This labels entity split candidates only; it does not establish chronological
ordering, maturity, or qualification as training data. The schema has no
numeric inputs or outputs, so the applicable finite-number limit is vacuous;
the general numeric boundary is absolute value <= 1e12. Numeric values,
including booleans, are invalid wherever strings or lists are required. This
tool makes no claim about real-data quality, trading competence, or runtime
admission. It performs no filesystem, network, model, or runtime operations.
"""

from __future__ import annotations

import json
import sys
from typing import Any


_PAYLOAD_KEYS = {"rows", "holdout_groups"}


def _string_list(value: Any, label: str, *, nonempty: bool) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"linked_group_holdout_{label}")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"linked_group_holdout_{label}")
    if len(set(value)) != len(value):
        raise ValueError(f"linked_group_holdout_{label}_unique")
    return value


def analyze(payload: dict) -> dict:
    """Validate payload and return deterministic entity split candidates."""
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise ValueError("linked_group_holdout_payload")

    rows = payload["rows"]
    if not isinstance(rows, list):
        raise ValueError("linked_group_holdout_rows")
    nominations = _string_list(payload["holdout_groups"], "holdout_groups", nonempty=False)

    row_groups: dict[str, set[str]] = {}
    group_rows: dict[str, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "groups"}:
            raise ValueError("linked_group_holdout_row")
        identity = row["id"]
        if not isinstance(identity, str) or not identity:
            raise ValueError("linked_group_holdout_id")
        if identity in row_groups:
            raise ValueError("linked_group_holdout_duplicate_id")
        groups = _string_list(row["groups"], "groups", nonempty=True)
        row_groups[identity] = set(groups)
        for group in groups:
            group_rows.setdefault(group, set()).add(identity)

    if any(group not in group_rows for group in nominations):
        raise ValueError("linked_group_holdout_unknown_group")

    unseen = set(row_groups)
    components: list[dict[str, Any]] = []
    while unseen:
        seed = min(unseen)
        component_rows = {seed}
        component_groups: set[str] = set()
        frontier = [seed]
        unseen.remove(seed)
        while frontier:
            identity = frontier.pop()
            for group in row_groups[identity]:
                if group in component_groups:
                    continue
                component_groups.add(group)
                linked = group_rows[group] & unseen
                component_rows.update(linked)
                unseen.difference_update(linked)
                frontier.extend(linked)
        role = "holdout" if component_groups.intersection(nominations) else "train"
        components.append({
            "row_ids": sorted(component_rows),
            "groups": sorted(component_groups),
            "role": role,
        })

    components.sort(key=lambda component: component["row_ids"][0])
    holdout_ids = sorted(
        identity for component in components if component["role"] == "holdout"
        for identity in component["row_ids"]
    )
    train_ids = sorted(
        identity for component in components if component["role"] == "train"
        for identity in component["row_ids"]
    )
    return {
        "schema": "brain-linked-group-holdout.v1",
        "train_ids": train_ids,
        "holdout_ids": holdout_ids,
        "components": components,
    }


def _reject_constant(_value: str) -> None:
    raise ValueError("linked_group_holdout_nonfinite_json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("linked_group_holdout_duplicate_key")
        result[key] = value
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        print(str(exc) or "linked_group_holdout_invalid_input", file=sys.stderr)
        return 2
    # ASCII escaping keeps every accepted Python string serializable, including
    # lone surrogate code units supplied through JSON's \uXXXX escapes.
    json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
