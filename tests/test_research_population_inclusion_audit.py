"""Focused synthetic tests for population inclusion auditing."""

from copy import deepcopy

import pytest

from memecoin_experience.population_inclusion_audit import analyze


PIN = "a" * 64


def make_inputs():
    snapshot = {"snapshot_id": "population-1", "snapshot_sha256": PIN,
                "row_ids": ["r3", "r1", "r2", "r4"]}
    decisions = [
        {"row_id": "r1", "group_id": "g1", "disposition": "included", "reason": None},
        {"row_id": "r2", "group_id": "g1", "disposition": "excluded", "reason": "bad row"},
        {"row_id": "r3", "group_id": "g2", "disposition": "included", "reason": None},
        {"row_id": "r4", "group_id": "g3", "disposition": "excluded", "reason": "out of scope"},
    ]
    return snapshot, decisions


def test_reports_rows_groups_and_denominator_changes_including_partial_group():
    snapshot, decisions = make_inputs()
    result = analyze(snapshot, decisions)

    assert [row["row_id"] for row in result["rows"]] == ["r1", "r2", "r3", "r4"]
    assert [(group["group_id"], group["status"]) for group in result["groups"]] == [
        ("g1", "partial"), ("g2", "included"), ("g3", "excluded")]
    assert result["groups"][0] == {
        "group_id": "g1", "row_count": 2, "included_count": 1,
        "excluded_count": 1, "disposition": "excluded", "status": "partial"}
    assert result["denominators"] == {
        "rows": {"before": 4, "after": 2, "included": 2, "excluded": 2},
        "groups": {"before": 3, "after": 1, "included": 1, "excluded": 2, "partial": 1}}
    assert result["snapshot"] == {"snapshot_id": "population-1", "snapshot_sha256": PIN}


def test_digest_is_order_independent_and_inputs_are_unchanged():
    snapshot, decisions = make_inputs()
    original_snapshot, original_decisions = deepcopy(snapshot), deepcopy(decisions)
    first = analyze(snapshot, decisions)
    second = analyze({**snapshot, "row_ids": list(reversed(snapshot["row_ids"]))},
                     list(reversed(decisions)))

    assert first["canonical_input_sha256"] == second["canonical_input_sha256"]
    assert snapshot == original_snapshot
    assert decisions == original_decisions


@pytest.mark.parametrize("mutation,code", [
    ("missing", "missing_decision_row"),
    ("duplicate", "duplicate_decision_row"),
    ("extra", "extra_decision_row"),
])
def test_rejects_incomplete_duplicate_and_extra_decisions(mutation, code):
    snapshot, decisions = make_inputs()
    if mutation == "missing":
        decisions.pop()
    elif mutation == "duplicate":
        decisions.append(dict(decisions[0]))
    else:
        decisions.append({"row_id": "unknown", "group_id": "g", "disposition": "included", "reason": None})

    with pytest.raises(ValueError, match=f"population_inclusion_audit_{code}"):
        analyze(snapshot, decisions)


def test_all_included_and_all_excluded_groups_have_expected_counts():
    snapshot = {"snapshot_id": "empty-pin", "snapshot_sha256": PIN, "row_ids": ["a", "b"]}
    decisions = [
        {"row_id": "a", "group_id": "yes", "disposition": "included", "reason": None},
        {"row_id": "b", "group_id": "no", "disposition": "excluded", "reason": "reason"},
    ]
    result = analyze(snapshot, decisions)
    assert result["denominators"]["groups"] == {
        "before": 2, "after": 1, "included": 1, "excluded": 1, "partial": 0}
