from __future__ import annotations

from copy import deepcopy

import pytest

from memecoin_experience.group_split_allocation_audit import analyze as allocation_audit
from memecoin_experience.population_inclusion_audit import analyze as inclusion_audit
from memecoin_experience.split_input_lineage import analyze as lineage_audit
from memecoin_experience.split_review_allocation import analyze


def population_from_producers():
    """Build the frozen A shape from the real inclusion and lineage producers."""
    snapshot = {"snapshot_id": "synthetic-population", "snapshot_sha256": "a" * 64,
                "row_ids": ["r1", "r2", "r3", "r4", "excluded"]}
    decisions = [
        {"row_id": "r1", "group_id": "g1", "disposition": "included", "reason": None},
        {"row_id": "r2", "group_id": "g1", "disposition": "included", "reason": None},
        {"row_id": "r3", "group_id": "g2", "disposition": "included", "reason": None},
        {"row_id": "r4", "group_id": "g3", "disposition": "included", "reason": None},
        {"row_id": "excluded", "group_id": "g4", "disposition": "excluded", "reason": "synthetic exclusion"},
    ]
    inclusion = inclusion_audit(snapshot, decisions)
    assignments = [{"group_id": "g1", "split": "train"},
                   {"group_id": "g2", "split": "test"},
                   {"group_id": "g3", "split": "test"}]
    plan = {"plan_id": "synthetic-plan", "plan_sha256": "b" * 64,
            "declared_splits": ["train", "test"]}
    lineage = lineage_audit(inclusion["snapshot_pin"], inclusion, assignments, plan)
    group_by_row = {item["row_id"]: item["group_id"] for item in inclusion["rows"]}
    rows = [{"id": row_id, "group_id": group_by_row[row_id]}
            for row_id in sorted(row_id for split in ("train", "test")
                                 for row_id in lineage["included_row_ids_by_split"][split])]
    row_splits = [{"row_id": row_id, "split": split}
                  for split in ("train", "test")
                  for row_id in lineage["included_row_ids_by_split"][split]]
    return {"inclusion": inclusion, "lineage": lineage, "rows": rows,
            "row_splits": sorted(row_splits, key=lambda item: item["row_id"]),
            "denominators": {"snapshot_rows": 5, "individually_included_rows": 4,
                             "assignable_rows": 4, "whole_group_excluded_rows": 1,
                             "snapshot_groups": 4, "assignable_groups": 3, "excluded_groups": 1}}


STRATA = [{"id": "alpha", "supported_splits": ["train", "test"]},
          {"id": "beta", "supported_splits": ["train", "test"]}]
ROW_STRATA = [{"row_id": "r1", "stratum_id": "alpha"},
              {"row_id": "r2", "stratum_id": "beta"},
              {"row_id": "r3", "stratum_id": "alpha"},
              {"row_id": "r4", "stratum_id": "beta"}]
TARGETS = {"train": {"alpha": 0, "beta": 0}, "test": {"alpha": 1, "beta": 1}}


def test_uses_actual_assignment_audit_and_producer_population_shape():
    population = population_from_producers()
    result = analyze(population, STRATA, ROW_STRATA, target_counts=TARGETS)
    assert result["allocation"] == allocation_audit({"rows": population["rows"],
                                                     "assignments": population["row_splits"]})
    assert result["allocation"]["summary"]["status"] == "clean"
    assert result["feasibility"]["status"] == "feasible"
    # The quota diagnostic is hypothetical; its minimum witness may move the
    # already assigned train group into the candidate test allocation.
    assert result["feasibility"]["diagnostic"]["details"]["holdout_group_ids"] == ["g1"]
    assert "does not alter assignments" in result["limitations"][1]


@pytest.mark.parametrize("row_strata,reason", [
    (ROW_STRATA[:-1], "missing_row_strata"),
    (ROW_STRATA + [ROW_STRATA[0]], "ambiguous_row_strata"),
    (ROW_STRATA[:-1] + [{"row_id": "r4", "stratum_id": "ghost"}], "unknown_row_strata"),
])
def test_missing_ambiguous_and_unknown_labels_make_feasibility_unknown(row_strata, reason):
    result = analyze(population_from_producers(), STRATA, row_strata, target_counts=TARGETS)
    assert result["feasibility"]["status"] == "unknown"
    assert result["feasibility"]["reason"] == reason


def test_no_targets_misaligned_targets_empty_population_and_state_limit():
    population = population_from_producers()
    assert analyze(population, STRATA, ROW_STRATA)["feasibility"] == {
        "status": "not_requested", "reason": "missing_target_counts"}
    assert analyze(population, STRATA, ROW_STRATA,
                   target_counts={"train": {}, "test": {}})["feasibility"]["reason"] == "misaligned_target_counts"
    limited = analyze(population, STRATA, ROW_STRATA, target_counts=TARGETS, max_states=1)
    assert limited["feasibility"]["status"] == "unknown"
    assert limited["feasibility"]["reason"] == "state_limit_exceeded"
    assert limited["feasibility"]["diagnostic"]["summary"]["status"] == "limit_exceeded"

    empty = deepcopy(population)
    empty["rows"] = []
    empty["row_splits"] = []
    no_groups = analyze(empty, STRATA, [], target_counts={"train": {"alpha": 0, "beta": 0},
                                                          "test": {"alpha": 0, "beta": 0}})
    assert no_groups["allocation"]["summary"]["status"] == "clean"
    assert no_groups["feasibility"]["status"] == "feasible"
    assert no_groups["feasibility"]["diagnostic"]["summary"]["group_count"] == 0


def test_duplicate_strata_and_test_unsupported_targets_are_unknown():
    population = population_from_producers()
    duplicate = STRATA + [STRATA[0]]
    assert analyze(population, duplicate, ROW_STRATA, target_counts=TARGETS)["feasibility"]["reason"] == "duplicate_strata"
    unsupported = [{"id": "alpha", "supported_splits": ["train"]}, STRATA[1]]
    assert analyze(population, unsupported, ROW_STRATA,
                   target_counts=TARGETS)["feasibility"]["reason"] == "unsupported_test_targets"


def test_group_partition_is_not_modified_or_reassigned():
    population = population_from_producers()
    before = deepcopy(population["row_splits"])
    result = analyze(population, STRATA, ROW_STRATA, target_counts=TARGETS)
    assert population["row_splits"] == before
    assert result["feasibility"]["diagnostic"]["details"]["holdout_group_ids"] == ["g1"]
    assert result["allocation"]["summary"]["status"] == "clean"


def test_unsupported_eligible_row_and_invalid_or_misaligned_all_split_targets():
    population = population_from_producers()
    unsupported = [{"id": s["id"], "supported_splits": ["test"]} for s in STRATA]
    zeros = {split: {s["id"]: 0 for s in STRATA} for split in ("train", "test")}
    assert analyze(population, unsupported, ROW_STRATA, target_counts=zeros)["feasibility"]["reason"] == "unsupported_eligible_row_strata"
    targets = deepcopy(TARGETS)
    targets["train"]["alpha"] = -1
    assert analyze(population, STRATA, ROW_STRATA, target_counts=targets)["feasibility"]["reason"] == "invalid_target_counts"
    population["lineage"]["declared_splits"].append("validation")
    targets = {**TARGETS, "validation": {}}
    assert analyze(population, STRATA, ROW_STRATA, target_counts=targets)["feasibility"]["reason"] == "misaligned_target_counts"
