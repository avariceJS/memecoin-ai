from __future__ import annotations

import copy
import hashlib
import json
import math
import subprocess
import sys

import pytest

from memecoin_runner.quantile_cohort_coverage import analyze


def forecast(*values: float) -> list[dict]:
    return [{"level": level, "value": value} for level, value in zip((0.25, 0.5), values)]


def example() -> dict:
    return {
        "nominal_levels": [0.25, 0.5],
        "rows": [
            {"id": "b-1", "target": "price", "cohort": "young", "status": "scored", "actual": 10, "quantiles": forecast(10, 11)},
            {"id": "b-2", "target": "price", "cohort": "young", "status": "scored", "actual": 12, "quantiles": forecast(11, 12)},
            {"id": "a-1", "target": "volume", "cohort": "deep", "status": "scored", "actual": 3, "quantiles": forecast(2, 4)},
            {"id": "b-ma", "target": "price", "cohort": "young", "status": "missing_actual", "actual": None, "quantiles": forecast(10, 12)},
            {"id": "b-mp", "target": "price", "cohort": "young", "status": "missing_prediction", "actual": 12, "quantiles": None},
            {"id": "b-u", "target": "price", "cohort": "young", "status": "unsupported", "actual": None, "quantiles": None},
        ],
    }


def test_grouped_coverage_counts_status_denominators_and_boundary_equality() -> None:
    result = analyze(example())
    assert set(result) == {"schema", "kind", "input_sha256", "summary", "details", "limitations"}
    assert result["schema"] == "brain-research-diagnostic.v1"
    assert result["kind"] == "quantile_cohort_coverage"
    assert result["summary"] == {
        "row_count": 6, "group_count": 2, "scored_count": 3,
        "missing_actual_count": 1, "missing_prediction_count": 1, "unsupported_count": 1,
    }
    young, deep = result["details"]
    assert [(item["target"], item["cohort"]) for item in result["details"]] == [
        ("price", "young"), ("volume", "deep")
    ]
    assert [set(row) for row in result["details"]] == [
        {"target", "cohort", "row_count", "scored_count", "missing_actual_count", "missing_prediction_count", "unsupported_count", "levels"}
    ] * 2
    assert deep["levels"] == [
        {"nominal_level": 0.25, "scored_count": 1, "covered_count": 0, "empirical_coverage": 0.0, "signed_gap": -0.25},
        {"nominal_level": 0.5, "scored_count": 1, "covered_count": 1, "empirical_coverage": 1.0, "signed_gap": 0.5},
    ]
    assert young["row_count"] == 5
    assert (young["scored_count"], young["missing_actual_count"], young["missing_prediction_count"], young["unsupported_count"]) == (2, 1, 1, 1)
    assert young["levels"][0]["covered_count"] == 1  # actual == forecast is covered
    assert young["levels"][1]["covered_count"] == 2
    assert all(math.isfinite(level["signed_gap"]) for group in result["details"] for level in group["levels"])


def test_zero_score_group_and_empty_input() -> None:
    payload = {"nominal_levels": [0.25], "rows": [
        {"id": "none", "target": "x", "cohort": "empty-score", "status": "unsupported", "actual": None, "quantiles": None}
    ]}
    group = analyze(payload)["details"][0]
    assert group["levels"] == [{
        "nominal_level": 0.25, "scored_count": 0, "covered_count": 0,
        "empirical_coverage": None, "signed_gap": None,
    }]
    empty = analyze({"nominal_levels": [0.25], "rows": []})
    assert empty["summary"] == {
        "row_count": 0, "group_count": 0, "scored_count": 0,
        "missing_actual_count": 0, "missing_prediction_count": 0, "unsupported_count": 0,
    }
    assert empty["details"] == []


def test_ordering_invariance_and_order_sensitive_canonical_hash() -> None:
    payload = example()
    reversed_payload = {"rows": list(reversed(payload["rows"])), "nominal_levels": [0.25, 0.5]}
    result, reversed_result = analyze(payload), analyze(reversed_payload)
    assert result["summary"] == reversed_result["summary"]
    assert result["details"] == reversed_result["details"]
    assert result["limitations"] == reversed_result["limitations"]
    assert result["input_sha256"] != reversed_result["input_sha256"]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    assert result["input_sha256"] == hashlib.sha256(canonical).hexdigest()


def test_input_is_not_mutated() -> None:
    payload = example()
    before = copy.deepcopy(payload)
    analyze(payload)
    assert payload == before


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(extra=True),
    lambda p: p.pop("rows"),
    lambda p: p.update(nominal_levels=[]),
    lambda p: p.update(nominal_levels=[0, 0.5]),
    lambda p: p.update(nominal_levels=[0.5, 0.5]),
    lambda p: p.update(nominal_levels=[0.5, 0.25]),
    lambda p: p.update(nominal_levels=[True]),
    lambda p: p.update(nominal_levels=[0.1] * 10),
    lambda p: p.update(rows=[{}] * 10_001),
    lambda p: p.update(rows=[{"id": "x"}]),
    lambda p: p["rows"][0].update(extra=1),
    lambda p: p["rows"][0].update(id=""),
    lambda p: p["rows"][0].update(target=""),
    lambda p: p["rows"][0].update(cohort=""),
    lambda p: p["rows"][0].update(status="unknown"),
    lambda p: p["rows"][0].update(actual=True),
    lambda p: p["rows"][0].update(actual=float("inf")),
    lambda p: p["rows"][0].update(actual=1e12 + 1),
    lambda p: p["rows"][0]["quantiles"][0].update(level=0.5),
    lambda p: p["rows"][0]["quantiles"][0].update(value=float("nan")),
    lambda p: p["rows"][0]["quantiles"][0].update(value=False),
    lambda p: p["rows"][0]["quantiles"][0].update(extra=1),
    lambda p: p["rows"][0].update(quantiles=[]),
    lambda p: p["rows"].append(copy.deepcopy(p["rows"][0])),
    lambda p: p["rows"][1].update(status="missing_actual", actual=None, quantiles=None),
    lambda p: p["rows"][1].update(status="missing_prediction", actual=None, quantiles=None),
    lambda p: p["rows"][1].update(status="unsupported", actual=12, quantiles=None),
])
def test_invalid_inputs_raise_value_error(mutate) -> None:
    payload = example()
    mutate(payload)
    with pytest.raises(ValueError):
        analyze(payload)


def _cli(input_text: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "memecoin_runner.quantile_cohort_coverage"],
        input=input_text, text=True, capture_output=True,
    )


def test_cli_valid_input_is_deterministic_and_has_clean_streams() -> None:
    input_text = json.dumps(example())
    first, second = _cli(input_text), _cli(input_text)
    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout
    assert json.loads(first.stdout) == analyze(example())


@pytest.mark.parametrize("input_text,expected", [
    ('{"nominal_levels":[0.25],"rows":[],"rows":[]}', "duplicate_json_key"),
    ('{"nominal_levels":[NaN],"rows":[]}', "nonfinite_json_number"),
    ('{"nominal_levels":[0.25],"rows":', "error:"),
    ('{"nominal_levels":[0.25],"rows":[{}]}', "row_keys_mismatch"),
])
def test_cli_rejects_duplicate_nonfinite_malformed_and_contract_invalid_input(input_text: str, expected: str) -> None:
    process = _cli(input_text)
    assert process.returncode == 2
    assert process.stdout == ""
    assert expected in process.stderr
