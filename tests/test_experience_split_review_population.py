"""Focused synthetic tests for the actual inclusion-to-lineage adapter."""

import pytest

from memecoin_experience.split_review_population import analyze


PIN = "a" * 64
PLAN = {"plan_id": "plan-1", "plan_sha256": "b" * 64,
        "declared_splits": ["train", "test"]}


def inputs():
    snapshot = {"snapshot_id": "population-1", "snapshot_sha256": PIN,
                "row_ids": ["r1", "r2", "r3", "r4", "r5", "r6"]}
    decisions = [
        {"row_id": "r1", "group_id": "g1", "disposition": "included", "reason": None},
        {"row_id": "r2", "group_id": "g1", "disposition": "included", "reason": None},
        {"row_id": "r3", "group_id": "mixed", "disposition": "included", "reason": None},
        {"row_id": "r4", "group_id": "mixed", "disposition": "excluded", "reason": "out of scope"},
        {"row_id": "r5", "group_id": "excluded", "disposition": "excluded", "reason": "out of scope"},
        {"row_id": "r6", "group_id": "g2", "disposition": "included", "reason": None},
    ]
    assignments = [
        {"group_id": "g1", "split": "train"},
        {"group_id": "g2", "split": "test"},
    ]
    return snapshot, decisions, assignments


def test_actual_inclusion_envelope_flows_through_lineage_and_excludes_mixed_groups():
    result = analyze(*inputs(), PLAN)

    assert result["inclusion"]["schema"] == "brain-population-inclusion-audit.v1"
    assert result["lineage"]["manifest_sha256"]
    assert result["lineage"]["included_row_ids_by_split"] == {
        "train": ["r1", "r2"], "validation": [], "test": ["r6"],
    }
    assert result["rows"] == [
        {"id": "r1", "group_id": "g1"},
        {"id": "r2", "group_id": "g1"},
        {"id": "r6", "group_id": "g2"},
    ]
    assert result["row_splits"] == [
        {"row_id": "r1", "split": "train"},
        {"row_id": "r2", "split": "train"},
        {"row_id": "r6", "split": "test"},
    ]
    assert result["denominators"] == {
        "snapshot_rows": 6,
        "individually_included_rows": 4,
        "assignable_rows": 3,
        "whole_group_excluded_rows": 3,
        "snapshot_groups": 4,
        "assignable_groups": 2,
        "excluded_groups": 2,
    }


def test_actual_producer_manifest_can_be_pinned_and_mismatch_is_rejected():
    result = analyze(*inputs(), PLAN)
    accepted = analyze(*inputs(), PLAN,
                       expected_manifest_sha256=result["lineage"]["manifest_sha256"])
    assert accepted == result
    with pytest.raises(ValueError, match="split_review_population_expected_manifest_sha256"):
        analyze(*inputs(), PLAN, expected_manifest_sha256="c" * 64)
    with pytest.raises(ValueError, match="split_review_population_expected_manifest_sha256"):
        analyze(*inputs(), PLAN, expected_manifest_sha256="A" * 64)


def test_assignment_validation_is_the_actual_lineage_validation():
    snapshot, decisions, _ = inputs()
    with pytest.raises(ValueError, match="split_lineage_missing_assignment"):
        analyze(snapshot, decisions, [], PLAN)


def test_empty_snapshot_has_zero_denominators():
    result = analyze(
        {"snapshot_id": "empty", "snapshot_sha256": PIN, "row_ids": []},
        [], [], PLAN,
    )
    assert result["rows"] == []
    assert result["row_splits"] == []
    assert result["denominators"] == {
        "snapshot_rows": 0, "individually_included_rows": 0,
        "assignable_rows": 0, "whole_group_excluded_rows": 0,
        "snapshot_groups": 0, "assignable_groups": 0, "excluded_groups": 0,
    }
