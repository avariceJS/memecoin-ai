from __future__ import annotations

import itertools
import json
import subprocess
import sys

import pytest

from memecoin_experience.group_split_feasibility import analyze


def payload(groups, target, budget=50_000):
    strata = sorted(target)
    return {"groups": [{"id": ident, "counts": dict(zip(strata, vector))} for ident, vector in groups],
            "holdout_counts": target, "max_states": budget}


def oracle(data):
    strata = sorted(data["holdout_counts"])
    target = tuple(data["holdout_counts"][s] for s in strata)
    valid = []
    for mask in range(1 << len(data["groups"])):
        chosen = tuple(sorted(data["groups"][i]["id"] for i in range(len(data["groups"])) if mask >> i & 1))
        sums = tuple(sum(data["groups"][i]["counts"][s] for i in range(len(data["groups"])) if mask >> i & 1) for s in strata)
        if sums == target:
            valid.append(chosen)
    return min(valid, key=lambda x: (len(x), x)) if valid else None


def test_saved_case_and_exact_canonical_witness():
    data = payload([("g1", (2, 0)), ("g2", (0, 2)), ("g4", (1, 1)), ("g3", (1, 1))],
                   {"a": 1, "b": 1})
    result = analyze(data)
    assert result["summary"]["status"] == "feasible"
    assert result["details"]["holdout_group_ids"] == ["g3"]
    assert analyze(payload([("g1", (2, 0)), ("g2", (0, 2)), ("g3", (1, 1)), ("g4", (1, 1))],
                          {"a": 1, "b": 0}))["summary"]["status"] == "infeasible"


@pytest.mark.parametrize("groups,target", [
    ([], {"a": 0}), ([ ("z", (0,)), ("a", (0,)) ], {"a": 0}),
    ([ ("a", (2,)), ("b", (3,)) ], {"a": 5}), ([ ("a", (1, 0)), ("b", (0, 2)), ("c", (1, 1)) ], {"a": 1, "b": 2}),
    ([ ("a", (1,)), ("b", (1,)) ], {"a": 2}),
])
def test_matches_independent_exhaustive_oracle(groups, target):
    data = payload(groups, target)
    got = analyze(data)
    expected = oracle(data)
    assert got["summary"]["status"] == ("feasible" if expected is not None else "infeasible")
    if expected is not None:
        assert got["details"]["holdout_group_ids"] == list(expected)


def test_budget_boundary_permutations_and_shortcut_not_used_over_budget():
    data = payload([("a", (1,)), ("b", (2,))], {"x": 1}, 2)
    assert analyze(data)["summary"]["status"] == "feasible"
    over = payload([("a", (1,)), ("b", (2,))], {"x": 1}, 1)
    assert analyze(over)["summary"]["status"] == "limit_exceeded"
    permuted = payload([("b", (2,)), ("a", (1,))], {"x": 1}, 2)
    assert analyze(data)["details"] == analyze(permuted)["details"]
    assert analyze(payload([(f"g{i}", (1 if i == 0 else 0,)) for i in range(64)], {"x": 1}, 2))["summary"]["status"] == "feasible"


def test_eight_strata_and_supported_count_ceiling():
    strata = {f"s{i}": 1 for i in range(8)}
    vector = tuple(1 for _ in range(8))
    result = analyze(payload([("all", vector)], strata, 256))
    assert result["summary"]["stratum_count"] == 8
    assert result["summary"]["state_space_upper_bound"] == 256
    assert result["details"]["holdout_group_ids"] == ["all"]
    max_count = analyze(payload([("large", (1_000_000,))], {"x": 1_000_000}, 50_000))
    assert max_count["summary"]["status"] == "limit_exceeded"
    assert max_count["summary"]["state_space_upper_bound"] == 1_000_001


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(extra=1), lambda d: d.update(max_states=True),
    lambda d: d.update(holdout_counts={"x": True}),
    lambda d: d.update(groups=[{"id": "bad id", "counts": {"x": 1}}]),
    lambda d: d.update(groups=[{"id": "a", "counts": {"x": 1, "extra": 0}}]),
    lambda d: d.update(groups=[{"id": "a", "counts": {"x": 1}}, {"id": "a", "counts": {"x": 0}}]),
])
def test_rejects_invalid_contract(mutate):
    data = payload([], {"x": 0})
    mutate(data)
    with pytest.raises(ValueError, match="^group_split_feasibility_"):
        analyze(data)


def run_cli(text):
    return subprocess.run([sys.executable, "-m", "memecoin_experience.group_split_feasibility"],
                          input=text, text=True, capture_output=True, check=False)


def test_cli_valid_invalid_duplicate_nonfinite_and_oversize():
    data = payload([("a", (1,))], {"x": 1})
    success = run_cli(json.dumps(data))
    assert success.returncode == 0 and success.stderr == ""
    assert json.loads(success.stdout) == analyze(data)
    for text in ('{"groups":[],"groups":[],"holdout_counts":{"x":0},"max_states":2}',
                 '{"groups":[],"holdout_counts":{"x":NaN},"max_states":2}', '{',
                 json.dumps(data) + " {}", " " * (2 * 1024 * 1024 + 1)):
        failed = run_cli(text)
        assert failed.returncode == 2 and failed.stdout == ""
        assert failed.stderr.startswith("group_split_feasibility_") and "Traceback" not in failed.stderr
