import copy
import hashlib
import json
import subprocess
import sys

import pytest

from memecoin_runner import paired_loss_comparison, paired_loss_missing_bounds, paired_loss_target_mix
from memecoin_runner.paired_loss_workflow import analyze


def payload():
    return {
        "model_a": "baseline",
        "model_b": "candidate",
        "metric": "synthetic_loss",
        "loss_upper_bound": 10,
        "target_cohorts": [
            {"cohort": "small", "weight": 1},
            {"cohort": "large", "weight": 9},
            {"cohort": "unsupported", "weight": 2},
            {"cohort": "zero", "weight": 0},
        ],
        "rows": [
            {"id": "s1", "cohort": "small", "loss_a": 8, "loss_b": 1},
            {"id": "l1", "cohort": "large", "loss_a": 1, "loss_b": 3},
            {"id": "a-only", "cohort": "large", "loss_a": 4, "loss_b": None},
            {"id": "b-only", "cohort": "small", "loss_a": None, "loss_b": 5},
            {"id": "missing", "cohort": "large", "loss_a": None, "loss_b": None},
        ],
    }


def test_wraps_actual_producer_envelopes_and_hashes_full_input_without_mutation():
    value = payload()
    original = copy.deepcopy(value)
    expected_hash = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                   allow_nan=False).encode("utf-8")
    ).hexdigest()
    result = analyze(value)
    pairs = [row for row in value["rows"] if row["loss_a"] is not None and row["loss_b"] is not None]
    common = {key: value[key] for key in ("model_a", "model_b", "metric")}
    expected = [
        paired_loss_comparison.analyze({**common, "rows": value["rows"]}),
        paired_loss_missing_bounds.analyze({**common, "loss_upper_bound": 10,
                                            "rows": value["rows"]}),
        paired_loss_target_mix.analyze({**common, "target_cohorts": value["target_cohorts"],
                                        "rows": pairs}),
    ]
    assert result["schema"] == "brain-paired-loss-workflow.v1"
    assert result["input_sha256"] == expected_hash
    assert result["reports"] == expected
    assert value == original
    assert result["reports"][0]["input_sha256"] != result["input_sha256"]


def test_support_denominators_and_projection_preserve_unsupported_weight():
    result = analyze(payload())
    assert result["support"] == {
        "roster_count": 5, "roster_denominator": 5,
        "complete_pair_count": 2, "complete_pair_denominator": 5,
        "paired_count": 2, "a_only_count": 1, "b_only_count": 1,
        "both_missing_count": 1,
        "paired_denominator": 5, "a_only_denominator": 5,
        "b_only_denominator": 5, "both_missing_denominator": 5,
        "target_projection_count": 2, "target_projection_denominator": 5,
        "target_projection_excluded_count": 3,
        "target_projection_excluded_denominator": 5,
        "target_projection_complete_pair_denominator": 2,
    }
    summary = result["reports"][2]["summary"]
    assert summary["unsupported_positive_weight_cohort_count"] == 1
    assert summary["target_mean_delta"] is None
    cohorts = {detail["cohort"]: detail for detail in result["reports"][2]["details"]}
    assert cohorts["zero"]["supported"] is False
    assert cohorts["zero"]["weighted_contribution"] == 0


def test_paired_and_target_estimands_can_reverse_and_missing_bounds_cross_zero():
    result = analyze(payload())
    comparison = result["reports"][0]["summary"]
    bounds = result["reports"][1]["summary"]
    # The two complete pairs pool to -2.5, while supported target cohorts
    # weighted 1:9 would yield +1.1 if no positive-weight cohort were unsupported.
    assert comparison["micro_mean_delta"] == -2.5
    assert bounds["all_roster_lower_mean_delta"] < 0
    assert bounds["all_roster_upper_mean_delta"] > 0
    value = payload()
    value["target_cohorts"] = [
        {"cohort": "small", "weight": 1}, {"cohort": "large", "weight": 9}
    ]
    assert analyze(value)["reports"][2]["summary"]["target_mean_delta"] == pytest.approx(1.1)


def test_empty_and_all_missing_rosters_have_explicit_support_and_nulls():
    value = payload()
    value["rows"] = []
    empty = analyze(value)
    assert empty["support"]["roster_denominator"] == 0
    assert empty["support"]["target_projection_excluded_denominator"] == 0
    assert empty["reports"][0]["summary"]["micro_mean_delta"] is None
    assert empty["reports"][1]["summary"]["all_roster_lower_mean_delta"] is None
    assert empty["reports"][2]["summary"]["target_mean_delta"] is None
    value["rows"] = [{"id": "m", "cohort": "large", "loss_a": None, "loss_b": None}]
    missing = analyze(value)
    assert missing["support"]["both_missing_count"] == 1
    assert missing["reports"][0]["details"][0]["mean_delta"] is None
    assert missing["reports"][1]["summary"]["all_roster_lower_mean_delta"] == -10
    assert missing["reports"][2]["summary"]["pooled_mean_delta"] is None


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra=True),
    lambda p: p.update(model_b="baseline"),
    lambda p: p.update(loss_upper_bound=True),
    lambda p: p.update(loss_upper_bound=0),
    lambda p: p["target_cohorts"].append({"cohort": "small", "weight": 1}),
    lambda p: p.update(target_cohorts=[{"cohort": "small", "weight": 0}]),
    lambda p: p["rows"].__setitem__(2, {"id": "a-only", "cohort": "undeclared",
                                         "loss_a": 4, "loss_b": None}),
    lambda p: p["rows"].__setitem__(0, {"id": "s1", "cohort": "small",
                                         "loss_a": True, "loss_b": 1}),
    lambda p: p["rows"].__setitem__(0, {"id": "s1", "cohort": "small",
                                         "loss_a": 11, "loss_b": 1}),
    lambda p: p["rows"].__setitem__(0, {"id": "s1", "cohort": "small",
                                         "loss_a": 8, "loss_b": 1, "extra": 0}),
    lambda p: p["rows"].append(dict(p["rows"][0])),
])
def test_invalid_and_hidden_incomplete_roster_inputs_rejected(change):
    value = payload()
    change(value)
    with pytest.raises(ValueError):
        analyze(value)


def test_cli_success_and_error_are_atomic_and_duplicate_keys_rejected():
    valid = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_workflow"],
        input=json.dumps(payload()), text=True, capture_output=True, check=False,
    )
    assert valid.returncode == 0 and valid.stderr == ""
    assert json.loads(valid.stdout) == analyze(payload())

    malformed = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_workflow"],
        input='{"model_a":"a","model_a":"b"}', text=True,
        capture_output=True, check=False,
    )
    assert malformed.returncode == 2 and malformed.stdout == ""
    assert "duplicate JSON object key" in malformed.stderr

    nonfinite = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_workflow"],
        input='{"model_a":"a","model_b":"b","metric":"m","loss_upper_bound":1,'
              '"target_cohorts":[{"cohort":"x","weight":1}],"rows":[],"extra":NaN}',
        text=True, capture_output=True, check=False,
    )
    assert nonfinite.returncode == 2 and nonfinite.stdout == ""
    assert "invalid JSON constant" in nonfinite.stderr


def test_target_item_and_roster_size_caps():
    value = payload()
    value["target_cohorts"] = [
        {"cohort": f"c{i}", "weight": 1} for i in range(10_001)
    ]
    with pytest.raises(ValueError, match="10000"):
        analyze(value)
    value = payload()
    value["rows"] = [
        {"id": str(i), "cohort": "small", "loss_a": 0, "loss_b": 0}
        for i in range(10_001)
    ]
    with pytest.raises(ValueError, match="10000"):
        analyze(value)
