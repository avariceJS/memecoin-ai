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
