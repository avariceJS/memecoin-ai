"""Compose real stratum coverage and target-deviation audits for split review."""

from __future__ import annotations

from typing import Any

from .split_stratum_coverage import analyze as analyze_coverage
from .split_target_deviation import analyze as analyze_deviation


_DECLARED_SPLITS = ("train", "validation", "test")
_LIMITATIONS = [
    "Coverage and target counts describe only rows assignable by the supplied population review.",
    "Snapshot and split-plan hashes are caller-supplied declarations; this helper does not verify unsupplied source data.",
    "Coverage does not establish population completeness, representativeness, independence, temporal validity or model qualification.",
    "Target deviation compares declared split targets with counts derived from eligible row labels; it does not change assignments or optimize a split.",
]


def _invalid(code: str) -> ValueError:
    return ValueError(f"split_review_coverage_{code}")


def _records(value: Any, keys: set[str], code: str) -> list[dict]:
    if type(value) is not list or any(type(item) is not dict or set(item) != keys for item in value):
        raise _invalid(code)
    return value


def _validate_population(population: Any) -> tuple[list[dict], list[dict], dict, dict]:
    if type(population) is not dict:
        raise _invalid("population")
    rows = _records(population.get("rows"), {"id", "group_id"}, "population_rows")
    row_splits = _records(population.get("row_splits"), {"row_id", "split"}, "population_splits")
    inclusion = population.get("inclusion")
    lineage = population.get("lineage")
    if type(inclusion) is not dict or type(lineage) is not dict:
        raise _invalid("population_provenance")
    if type(inclusion.get("rows")) is not list:
        raise _invalid("population_provenance")
    included_ids: set[str] = set()
    for row in rows:
        if type(row["id"]) is not str or not row["id"] or type(row["group_id"]) is not str or not row["group_id"] or row["id"] in included_ids:
            raise _invalid("population_rows")
        included_ids.add(row["id"])
    split_by_row: dict[str, str] = {}
    declared = lineage.get("declared_splits")
    if type(declared) is not list or not declared or any(type(s) is not str or s not in _DECLARED_SPLITS for s in declared) or len(set(declared)) != len(declared) or not {"train", "test"} <= set(declared):
        raise _invalid("declared_splits")
    for entry in row_splits:
        row_id, split = entry["row_id"], entry["split"]
        if type(row_id) is not str or not row_id or row_id in split_by_row or row_id not in included_ids or type(split) is not str or split not in declared:
            raise _invalid("population_splits")
        split_by_row[row_id] = split
    if set(split_by_row) != included_ids:
        raise _invalid("population_splits")

    snapshot_ids: set[str] = set()
    for row in inclusion["rows"]:
        if type(row) is not dict or not {"row_id", "group_id", "disposition", "reason"} <= set(row):
            raise _invalid("population_provenance")
        row_id = row["row_id"]
        if type(row_id) is not str or not row_id or row_id in snapshot_ids:
            raise _invalid("population_provenance")
        snapshot_ids.add(row_id)
    if not included_ids <= snapshot_ids:
        raise _invalid("population_provenance")
    return rows, row_splits, inclusion, lineage


def analyze(population: dict, strata: list, row_strata: list, *, target_counts: dict | None = None) -> dict:
    """Audit assignable-row declarations and, when possible, target deviations.

    The population argument is the result of ``split_review_population.analyze``.
    Excluded snapshot-row declarations are omitted from the coverage producer's
    input and counted separately; declarations for unknown rows are retained.
    """
    rows, row_splits, inclusion, lineage = _validate_population(population)
    strata = _records(strata, {"id", "supported_splits"}, "strata")
    row_strata = _records(row_strata, {"row_id", "stratum_id"}, "row_strata")
    if len(strata) > 1000 or len(row_strata) > 20000:
        raise _invalid("size_limit")

    snapshot_ids = {row["row_id"] for row in inclusion["rows"]}
    assignable_ids = {row["id"] for row in rows}
    excluded_ids = snapshot_ids - assignable_ids
    retained_row_strata: list[dict] = []
    filtered_excluded_count = 0
    for item in row_strata:
        row_id = item["row_id"]
        # The producer validates identifiers. Validate here before set membership
        # so malformed values receive this helper's stable error code.
        if type(row_id) is not str or not row_id or type(item["stratum_id"]) is not str or not item["stratum_id"]:
            raise _invalid("row_strata")
        if row_id in excluded_ids:
            filtered_excluded_count += 1
        else:
            retained_row_strata.append(item)

    coverage = analyze_coverage({
        "rows": [{"id": row["id"]} for row in rows],
        "strata": strata,
        "row_strata": retained_row_strata,
        "row_splits": row_splits,
    })

    deviation: dict
    if target_counts is None:
        deviation = {"status": "not_requested", "reason": "missing_target_counts"}
    else:
        deviation = _target_deviation(rows, row_splits, strata, retained_row_strata,
                                     lineage["declared_splits"], target_counts, coverage)

    coverage_summary = coverage["summary"]
    denominators = {
        "assignable_rows": len(rows),
        "strata": len(strata),
        "row_stratum_declarations_received": len(row_strata),
        "row_stratum_declarations_filtered_for_known_excluded_rows": filtered_excluded_count,
        "row_stratum_declarations_audited": len(retained_row_strata),
        "unknown_row_stratum_declarations": coverage_summary["unknown_stratum_declaration_count"],
        "coverage_availability": "empty_population" if not rows else ("empty_strata" if not strata else "available"),
    }
    return {
        "coverage": coverage,
        "target_deviation": deviation,
        "denominators": denominators,
        "limitations": list(_LIMITATIONS),
    }


def _target_deviation(rows: list[dict], row_splits: list[dict], strata: list[dict],
                      row_strata: list[dict], declared_splits: list[str],
                      target_counts: Any, coverage: dict) -> dict:
    if type(target_counts) is not dict or set(target_counts) != set(declared_splits):
        raise _invalid("target_splits")
    stratum_ids = {item["id"] for item in strata if type(item.get("id")) is str}
    if len(stratum_ids) != len(strata):
        raise _invalid("strata")
    if any(type(target_counts.get(split)) is not dict or set(target_counts[split]) != stratum_ids for split in declared_splits):
        raise _invalid("target_strata")
    if not stratum_ids:
        return {"status": "unknown", "reason": "empty_strata"}

    summary = coverage["summary"]
    if summary["missing_stratum_declaration_count"] or summary["duplicate_stratum_row_count"]:
        return {"status": "unknown", "reason": "incomplete_or_ambiguous_row_strata"}
    if summary["unknown_stratum_declaration_count"]:
        # These records cannot be counted as eligible rows and their identity is
        # unknown, so a complete observed table cannot be asserted.
        return {"status": "unknown", "reason": "unknown_row_stratum_declarations"}

    stratum_for_row: dict[str, str] = {}
    for item in row_strata:
        stratum_for_row[item["row_id"]] = item["stratum_id"]
    row_split_map = {item["row_id"]: item["split"] for item in row_splits}
    supported = {item["id"]: set(item["supported_splits"]) for item in strata}
    observed = {split: {stratum: 0 for stratum in stratum_ids} for split in declared_splits}
    for row in rows:
        row_id = row["id"]
        stratum = stratum_for_row.get(row_id)
        split = row_split_map.get(row_id)
        if stratum not in stratum_ids or split not in observed or split not in supported.get(stratum, set()):
            return {"status": "unknown", "reason": "unsupported_or_unattributable_eligible_row"}
        observed[split][stratum] += 1
    audit = analyze_deviation({"observed_counts": observed, "target_counts": target_counts})
    return {"status": "available", "audit": audit}
