"""Allocation audit and bounded test-quota feasibility for split review."""

from __future__ import annotations

from typing import Any

from .group_split_allocation_audit import analyze as audit_allocation
from .group_split_feasibility import analyze as assess_feasibility


_LIMITATIONS = [
    "Allocation findings describe only the supplied assignable rows and row-split declarations.",
    "Quota feasibility is an exact bounded group-count diagnostic for the declared test quota; it does not alter assignments or establish temporal, maturity, representativeness, independence, data-access or training qualification.",
    "Feasibility is unknown when eligible-row strata are incomplete, ambiguous, unsupported or not aligned with the declared test targets; a bounded state limit is also unknown, never evidence of infeasibility.",
]


def _unknown(reason: str, diagnostic: dict | None = None) -> dict:
    result: dict[str, Any] = {"status": "unknown", "reason": reason}
    if diagnostic is not None:
        result["diagnostic"] = diagnostic
    return result


def _derive_feasibility(
    population: dict,
    strata: list,
    row_strata: list,
    target_counts: Any,
    max_states: Any,
) -> dict:
    if target_counts is None:
        return {"status": "not_requested", "reason": "missing_target_counts"}

    if type(strata) is not list or type(row_strata) is not list:
        return _unknown("invalid_strata_declarations")
    lineage = population.get("lineage")
    declared_splits = lineage.get("declared_splits") if type(lineage) is dict else None
    if (type(declared_splits) is not list or "train" not in declared_splits or "test" not in declared_splits
            or type(target_counts) is not dict or set(target_counts) != set(declared_splits)):
        return _unknown("misaligned_target_counts")

    stratum_ids: list[str] = []
    stratum_supported: dict[str, set[str]] = {}
    for declaration in strata:
        if (type(declaration) is not dict or set(declaration) != {"id", "supported_splits"}
                or type(declaration["id"]) is not str or not declaration["id"]
                or type(declaration["supported_splits"]) is not list
                or any(type(split) is not str or split not in declared_splits
                       for split in declaration["supported_splits"])
                or len(set(declaration["supported_splits"])) != len(declaration["supported_splits"])):
            return _unknown("invalid_strata_declarations")
        stratum_ids.append(declaration["id"])
        stratum_supported.setdefault(declaration["id"], set()).update(declaration["supported_splits"])
    if len(set(stratum_ids)) != len(stratum_ids):
        return _unknown("duplicate_strata")

    # The feasibility producer requires one count for every supplied target
    # stratum. Requiring an exact, test-supported match prevents silently
    # dropping labels or inventing zero counts for unsupported strata.
    test_targets = target_counts["test"]
    if any(type(table) is not dict or set(table) != set(stratum_ids)
           for table in target_counts.values()):
        return _unknown("misaligned_target_counts")
    if any(type(count) is not int or count < 0
           for table in target_counts.values() for count in table.values()):
        return _unknown("invalid_target_counts")
    if any("test" not in stratum_supported[s] and test_targets[s] != 0 for s in stratum_ids):
        return _unknown("unsupported_test_targets")

    rows = population["rows"]
    row_ids = {row["id"] for row in rows}
    labels: dict[str, list[str]] = {}
    for declaration in row_strata:
        if (type(declaration) is not dict or set(declaration) != {"row_id", "stratum_id"}
                or type(declaration["row_id"]) is not str
                or type(declaration["stratum_id"]) is not str):
            return _unknown("invalid_row_strata_declarations")
        row_id, stratum_id = declaration["row_id"], declaration["stratum_id"]
        if row_id in row_ids:
            labels.setdefault(row_id, []).append(stratum_id)

    if any(len(labels.get(row_id, [])) > 1 for row_id in row_ids):
        return _unknown("ambiguous_row_strata")
    if any(row_id not in labels for row_id in row_ids):
        return _unknown("missing_row_strata")
    if any(labels[row_id][0] not in stratum_supported for row_id in row_ids):
        return _unknown("unknown_row_strata")
    split_by_row = {entry["row_id"]: entry["split"] for entry in population["row_splits"]}
    if any(split_by_row.get(row_id) not in stratum_supported[labels[row_id][0]]
           for row_id in row_ids):
        return _unknown("unsupported_eligible_row_strata")

    groups: dict[str, dict[str, int]] = {}
    for row in rows:
        group_id = row["group_id"]
        counts = groups.setdefault(group_id, {s: 0 for s in stratum_ids})
        counts[labels[row["id"]][0]] += 1

    try:
        diagnostic = assess_feasibility({
            "groups": [{"id": group_id, "counts": groups[group_id]} for group_id in sorted(groups)],
            "holdout_counts": test_targets,
            "max_states": max_states,
        })
    except (TypeError, ValueError):
        return _unknown("invalid_or_unsupported_test_targets")

    producer_status = diagnostic["summary"]["status"]
    if producer_status == "limit_exceeded":
        return _unknown("state_limit_exceeded", diagnostic)
    return {"status": producer_status, "diagnostic": diagnostic}


def analyze(
    population: dict,
    strata: list,
    row_strata: list,
    *,
    target_counts: dict | None = None,
    max_states: int = 50_000,
) -> dict:
    """Audit population assignments and assess only an explicitly declared test quota."""
    if type(population) is not dict or not {"rows", "row_splits"} <= set(population):
        raise ValueError("split_review_allocation_invalid_population")
    allocation = audit_allocation({"rows": population["rows"], "assignments": population["row_splits"]})
    feasibility = _derive_feasibility(population, strata, row_strata, target_counts, max_states)
    return {
        "allocation": allocation,
        "feasibility": feasibility,
        "limitations": list(_LIMITATIONS),
    }
