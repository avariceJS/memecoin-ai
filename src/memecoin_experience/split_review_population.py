"""Compose actual inclusion and lineage producers for a pinned population."""

from __future__ import annotations

import re

from .population_inclusion_audit import analyze as analyze_inclusion
from .split_input_lineage import analyze as analyze_lineage


_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def analyze(snapshot, decisions, assignments, split_plan, *, expected_manifest_sha256=None):
    """Return inclusion and lineage outputs plus the assignable population.

    Snapshot and plan hashes are caller-supplied identity declarations; this
    adapter has no access to the underlying population or plan data.
    """
    inclusion = analyze_inclusion(snapshot, decisions)
    snapshot_pin = inclusion["snapshot_pin"]
    lineage = analyze_lineage(snapshot_pin, inclusion, assignments, split_plan)
    if expected_manifest_sha256 is not None:
        if (type(expected_manifest_sha256) is not str
                or _SHA256.fullmatch(expected_manifest_sha256) is None
                or expected_manifest_sha256 != lineage["manifest_sha256"]):
            raise ValueError("split_review_population_expected_manifest_sha256")

    assignable_groups = {
        group["group_id"] for group in inclusion["groups"]
        if group["disposition"] == "included"
    }
    rows = sorted(
        ({"id": row["row_id"], "group_id": row["group_id"]}
         for row in inclusion["rows"] if row["group_id"] in assignable_groups),
        key=lambda row: row["id"],
    )
    row_splits = sorted(
        ({"row_id": row_id, "split": split}
         for split, row_ids in lineage["included_row_ids_by_split"].items()
         for row_id in row_ids),
        key=lambda row: row["row_id"],
    )
    denominators = {
        "snapshot_rows": len(snapshot["row_ids"]),
        "individually_included_rows": inclusion["row_denominators"]["after"],
        "assignable_rows": len(rows),
        "whole_group_excluded_rows": len(snapshot["row_ids"]) - len(rows),
        "snapshot_groups": len(inclusion["groups"]),
        "assignable_groups": len(assignable_groups),
        "excluded_groups": len(inclusion["groups"]) - len(assignable_groups),
    }
    return {
        "inclusion": inclusion,
        "lineage": lineage,
        "rows": rows,
        "row_splits": row_splits,
        "denominators": denominators,
    }
