import copy
import hashlib
import json

import pytest

from memecoin_experience.split_input_lineage import analyze
from memecoin_experience.population_inclusion_audit import analyze as analyze_population


PIN = {"snapshot_id": "population-1", "snapshot_sha256": "a" * 64}
PLAN = {"plan_id": "plan-1", "plan_sha256": "b" * 64, "declared_splits": ["train", "validation", "test"]}


def inputs():
    rows = [
        {"row_id": "r3", "group_id": "g2", "disposition": "included", "reason": "eligible"},
        {"row_id": "r1", "group_id": "g1", "disposition": "included", "reason": "eligible"},
        {"row_id": "r2", "group_id": "g1", "disposition": "included", "reason": "eligible"},
        {"row_id": "r4", "group_id": "g3", "disposition": "excluded", "reason": "excluded"},
    ]
    groups = [
        {"group_id": "g1", "row_count": 2, "included_row_count": 2, "excluded_row_count": 0, "disposition": "included"},
        {"group_id": "g2", "row_count": 1, "included_row_count": 1, "excluded_row_count": 0, "disposition": "included"},
        {"group_id": "g3", "row_count": 1, "included_row_count": 0, "excluded_row_count": 1, "disposition": "excluded"},
    ]
    inclusion = {
        "snapshot_pin": dict(PIN), "input_sha256": "c" * 64,
        "rows": rows, "groups": groups,
        "row_denominators": {"before": 4, "after": 3},
        "group_denominators": {"before": 3, "after": 2},
    }
    assignments = [{"group_id": "g2", "split": "test"}, {"group_id": "g1", "split": "train"}]
    return inclusion, assignments


def test_valid_manifest_counts_hash_and_ordering():
    inclusion, assignments = inputs()
    result = analyze(PIN, inclusion, assignments, PLAN)
    assert result["included_row_ids_by_split"] == {"train": ["r1", "r2"], "validation": [], "test": ["r3"]}
    assert result["excluded_row_count"] == 1
    assert result["excluded_group_count"] == 1
    assert result["row_denominators"] == {"before": 4, "after": 3, "change": -1}
    assert result["group_denominators"] == {"before": 3, "after": 2, "change": -1}
    body = {k: v for k, v in result.items() if k != "manifest_sha256"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert result["manifest_sha256"] == hashlib.sha256(canonical).hexdigest()
    inclusion2, assignments2 = inputs()
    inclusion2["rows"].reverse()
    assignments2.reverse()
    assert analyze(PIN, inclusion2, assignments2, PLAN) == result


@pytest.mark.parametrize("alias_mode", ["legacy", "published", "mixed", "agreeing_dual"])
def test_group_count_alias_compatibility(alias_mode):
    inclusion, assignments = inputs()
    for index, group in enumerate(inclusion["groups"]):
        included = group.pop("included_row_count")
        excluded = group.pop("excluded_row_count")
        if alias_mode == "legacy" or (alias_mode == "mixed" and index % 2 == 0):
            group.update(included_count=included, excluded_count=excluded)
        elif alias_mode == "agreeing_dual":
            group.update(
                included_row_count=included,
                excluded_row_count=excluded,
                included_count=included,
                excluded_count=excluded,
            )
        else:
            group.update(included_row_count=included, excluded_row_count=excluded)

    assert analyze(PIN, inclusion, assignments, PLAN)["included_group_counts_by_split"] == {
        "train": 1, "validation": 0, "test": 1,
    }


def test_legacy_mixed_included_group_remains_assignable():
    inclusion, assignments = inputs()
    inclusion["rows"][2].update(disposition="excluded", reason="out of scope")
    inclusion["groups"][0] = {
        "group_id": "g1", "row_count": 2, "included_count": 1,
        "excluded_count": 1, "disposition": "included",
    }
    inclusion["row_denominators"]["after"] = 2

    result = analyze(PIN, inclusion, assignments, PLAN)

    assert result["included_row_ids_by_split"] == {
        "train": ["r1"], "validation": [], "test": ["r3"],
    }
    assert result["excluded_row_count"] == 2
    assert result["excluded_group_count"] == 1
    assert result["row_denominators"] == {"before": 4, "after": 2, "change": -2}
    assert result["group_denominators"] == {"before": 3, "after": 2, "change": -1}


@pytest.mark.parametrize("alias_key,conflict", [
    ("included_count", 1),
    ("excluded_count", 1),
])
def test_conflicting_group_count_aliases_fail(alias_key, conflict):
    inclusion, assignments = inputs()
    inclusion["groups"][0][alias_key] = conflict
    with pytest.raises(ValueError, match="split_lineage_inclusion"):
        analyze(PIN, inclusion, assignments, PLAN)


@pytest.mark.parametrize("alias_key", [
    "included_row_count", "included_count", "excluded_row_count", "excluded_count",
])
@pytest.mark.parametrize("invalid", [True, -1, 2.0, "2"])
def test_every_supplied_group_count_alias_must_be_a_nonnegative_strict_int(alias_key, invalid):
    inclusion, assignments = inputs()
    inclusion["groups"][0][alias_key] = invalid
    with pytest.raises(ValueError, match="split_lineage_inclusion"):
        analyze(PIN, inclusion, assignments, PLAN)


@pytest.mark.parametrize("mutator,code", [
    (lambda i, a: a.pop(), "split_lineage_missing_assignment"),
    (lambda i, a: a.append(dict(a[0])), "split_lineage_duplicate_assignment"),
    (lambda i, a: a.append({"group_id": "unknown", "split": "test"}), "split_lineage_unknown_assignment"),
    (lambda i, a: a.append({"group_id": "g3", "split": "test"}), "split_lineage_excluded_assignment"),
    (lambda i, a: a.__setitem__(0, {"group_id": "g2", "split": "validation"}), "split_lineage_undeclared_split"),
])
def test_assignment_errors(mutator, code):
    inclusion, assignments = inputs()
    plan = dict(PLAN, declared_splits=["train", "test"])
    mutator(inclusion, assignments)
    with pytest.raises(ValueError, match=code):
        analyze(PIN, inclusion, assignments, plan)


def test_pin_mismatch_and_immutability():
    inclusion, assignments = inputs()
    before = copy.deepcopy((PIN, inclusion, assignments, PLAN))
    mismatched = dict(PIN, snapshot_sha256="d" * 64)
    with pytest.raises(ValueError, match="split_lineage_pin_mismatch"):
        analyze(mismatched, inclusion, assignments, PLAN)
    analyze(PIN, inclusion, assignments, PLAN)
    assert (PIN, inclusion, assignments, PLAN) == before


def test_population_audit_output_flows_directly_into_split_lineage():
    snapshot = {"snapshot_id": PIN["snapshot_id"],
                "snapshot_sha256": PIN["snapshot_sha256"],
                "row_ids": ["r1", "r2", "r3"]}
    decisions = [
        {"row_id": "r1", "group_id": "g1", "disposition": "included", "reason": None},
        {"row_id": "r2", "group_id": "g2", "disposition": "included", "reason": None},
        {"row_id": "r3", "group_id": "g3", "disposition": "excluded", "reason": "out of scope"},
    ]
    inclusion = analyze_population(snapshot, decisions)
    result = analyze(PIN, inclusion,
                     [{"group_id": "g1", "split": "train"}, {"group_id": "g2", "split": "test"}],
                     PLAN)
    assert result["included_row_ids_by_split"] == {
        "train": ["r1"], "validation": [], "test": ["r2"]}
    assert result["excluded_row_count"] == 1
    assert result["excluded_group_count"] == 1


def test_population_audit_partial_group_is_excluded_from_split_lineage():
    snapshot = {"snapshot_id": PIN["snapshot_id"],
                "snapshot_sha256": PIN["snapshot_sha256"],
                "row_ids": ["r1", "r2", "r3", "r4", "r5", "r6"]}
    decisions = [
        {"row_id": "r1", "group_id": "g1", "disposition": "included", "reason": None},
        {"row_id": "r2", "group_id": "g1", "disposition": "included", "reason": None},
        {"row_id": "r3", "group_id": "mixed", "disposition": "included", "reason": None},
        {"row_id": "r4", "group_id": "mixed", "disposition": "excluded", "reason": "out of scope"},
        {"row_id": "r5", "group_id": "excluded", "disposition": "excluded", "reason": "out of scope"},
        {"row_id": "r6", "group_id": "g2", "disposition": "included", "reason": None},
    ]
    inclusion = analyze_population(snapshot, decisions)
    mixed_group = next(group for group in inclusion["groups"] if group["group_id"] == "mixed")
    assert mixed_group["status"] == "partial"
    assert mixed_group["disposition"] == "excluded"

    assignments = [{"group_id": "g1", "split": "train"}, {"group_id": "g2", "split": "test"}]
    result = analyze(PIN, inclusion, assignments, PLAN)
    assert result["included_row_ids_by_split"] == {
        "train": ["r1", "r2"], "validation": [], "test": ["r6"]}
    assert result["excluded_row_count"] == 2
    assert result["excluded_group_count"] == 2
    assert result["row_denominators"] == {"before": 6, "after": 4, "change": -2}
    assert result["group_denominators"] == {"before": 4, "after": 2, "change": -2}

    with pytest.raises(ValueError, match="split_lineage_excluded_assignment"):
        analyze(PIN, inclusion, assignments + [{"group_id": "mixed", "split": "test"}], PLAN)
