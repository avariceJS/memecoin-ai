import itertools
import json
import math
import subprocess
import sys

import pytest

from memecoin_runner.paired_loss_missing_bounds import analyze


def payload(rows=None, upper=1.0):
    return {
        "model_a": "baseline",
        "model_b": "candidate",
        "metric": "bounded_loss",
        "loss_upper_bound": upper,
        "rows": rows if rows is not None else [
            {"id": "paired", "cohort": "x", "loss_a": 0.2, "loss_b": 0.1},
            {"id": "a-only", "cohort": "x", "loss_a": None, "loss_b": 0.4},
            {"id": "b-only", "cohort": "y", "loss_a": 0.6, "loss_b": None},
            {"id": "missing", "cohort": "y", "loss_a": None, "loss_b": None},
        ],
    }


def test_observable_mixed_missingness_example_and_cohort_support():
    result = analyze(payload())
    summary = result["summary"]
    assert result["schema"] == "brain-research-diagnostic.v1"
    assert result["kind"] == "paired_loss_missing_bounds"
    assert (summary["paired_count"], summary["a_only_count"], summary["b_only_count"], summary["both_missing_count"]) == (1, 1, 1, 1)
    assert summary["total_count"] == summary["total_denominator"] == 4
    assert summary["complete_pair_mean_delta"] == pytest.approx(-0.1)
    assert summary["complete_pair_mean_denominator"] == 1
    assert summary["all_roster_lower_mean_delta"] == pytest.approx(-0.575)
    assert summary["all_roster_upper_mean_delta"] == pytest.approx(0.425)
    assert summary["all_roster_mean_denominator"] == 4
    assert [item["cohort"] for item in result["details"]] == ["x", "y"]
    cohorts = {item["cohort"]: item for item in result["details"]}
    assert cohorts["x"]["paired_count"] == 1
    assert cohorts["y"]["both_missing_count"] == 1
    assert cohorts["y"]["all_roster_lower_mean_delta"] == pytest.approx(-0.8)
    assert "identities omitted from the roster remain unknown" in " ".join(result["limitations"])


def test_endpoint_enumeration_oracle_and_adversarial_support():
    cases = [
        payload(),
        payload([{"id": "a", "cohort": "c", "loss_a": 0.0, "loss_b": 0.0},
                 {"id": "b", "cohort": "c", "loss_a": 1.0, "loss_b": 1.0}]),
        payload([{"id": "m", "cohort": "c", "loss_a": None, "loss_b": None}], upper=0.7),
        payload([{"id": "m1", "cohort": "c", "loss_a": None, "loss_b": 0.0},
                 {"id": "m2", "cohort": "c", "loss_a": 1.0, "loss_b": None}], upper=1.0),
    ]
    for case in cases:
        result = analyze(case)["summary"]
        u = case["loss_upper_bound"]
        assignments = []
        for row in case["rows"]:
            choices_a = [row["loss_a"]] if row["loss_a"] is not None else [0.0, u]
            choices_b = [row["loss_b"]] if row["loss_b"] is not None else [0.0, u]
            assignments.append([b - a for a, b in itertools.product(choices_a, choices_b)])
        attainable = [sum(values) / len(values) for values in itertools.product(*assignments)] if assignments else []
        if attainable:
            assert result["all_roster_lower_mean_delta"] == pytest.approx(min(attainable))
            assert result["all_roster_upper_mean_delta"] == pytest.approx(max(attainable))
        else:
            assert result["all_roster_lower_mean_delta"] is None
            assert result["all_roster_upper_mean_delta"] is None


def test_complete_all_missing_and_empty_populations():
    complete = analyze(payload([{"id": "z", "cohort": "c", "loss_a": 0, "loss_b": 0}]))["summary"]
    assert complete["complete_pair_mean_delta"] == 0
    assert complete["all_roster_lower_mean_delta"] == complete["all_roster_upper_mean_delta"] == 0
    missing = analyze(payload([{"id": "z", "cohort": "c", "loss_a": None, "loss_b": None}], upper=3))["summary"]
    assert missing["all_roster_lower_mean_delta"] == -3
    assert missing["all_roster_upper_mean_delta"] == 3
    empty = analyze(payload([]))["summary"]
    assert empty["total_count"] == empty["paired_count"] == 0
    assert empty["complete_pair_mean_delta"] is None
    assert empty["all_roster_lower_mean_delta"] is empty["all_roster_upper_mean_delta"] is None


@pytest.mark.parametrize("change", [
    lambda p: p.update(extra=1),
    lambda p: p.update(model_a=" "),
    lambda p: p.update(rows=[{"id": "x", "cohort": "c", "loss_a": 9_007_199_254_740_993, "loss_b": 0}], loss_upper_bound=9_007_199_254_740_992),
    lambda p: p.update(loss_upper_bound=True),
    lambda p: p.update(loss_upper_bound=0),
    lambda p: p.update(loss_upper_bound=float("inf")),
    lambda p: p.update(rows=[{"id": "", "cohort": "c", "loss_a": 0, "loss_b": 0}]),
    lambda p: p.update(rows=[{"id": "x", "cohort": " ", "loss_a": 0, "loss_b": 0}]),
    lambda p: p.update(rows=[{"id": "x", "cohort": "c", "loss_a": True, "loss_b": 0}]),
    lambda p: p.update(rows=[{"id": "x", "cohort": "c", "loss_a": -0.1, "loss_b": 0}]),
    lambda p: p.update(rows=[{"id": "x", "cohort": "c", "loss_a": 0, "loss_b": 1.1}]),
    lambda p: p.update(rows=[{"id": "x", "cohort": "c", "loss_a": 0, "loss_b": 0, "other": 1}]),
    lambda p: p.update(rows=[{"id": "x", "cohort": "c", "loss_a": 0, "loss_b": 0}, {"id": "x", "cohort": "d", "loss_a": 0, "loss_b": 0}]),
])
def test_invalid_inputs_rejected(change):
    candidate = payload()
    change(candidate)
    with pytest.raises(ValueError):
        analyze(candidate)


def test_duplicate_json_keys_and_nonfinite_cli_input_rejected():
    duplicate = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_missing_bounds"],
        input='{"model_a":"a","model_a":"b"}', text=True, capture_output=True, check=False,
    )
    assert duplicate.returncode == 2 and duplicate.stdout == ""
    assert "duplicate JSON object key" in duplicate.stderr
    nonfinite = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_missing_bounds"],
        input='{"model_a":"a","model_b":"b","metric":"m","loss_upper_bound":1,"rows":[],"x":NaN}',
        text=True, capture_output=True, check=False,
    )
    assert nonfinite.returncode == 2 and "invalid JSON constant" in nonfinite.stderr


def test_equal_nonempty_model_names_are_valid():
    candidate = payload([{"id": "same-label", "cohort": "c", "loss_a": 0, "loss_b": 0}])
    candidate["model_b"] = candidate["model_a"]
    result = analyze(candidate)
    assert result["summary"]["model_a"] == result["summary"]["model_b"] == "baseline"
    assert result["summary"]["complete_pair_mean_delta"] == 0

    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_missing_bounds"],
        input=json.dumps(candidate), text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0 and completed.stderr == ""
    assert json.loads(completed.stdout)["summary"]["model_a"] == "baseline"


def test_out_of_range_integer_is_rejected_before_float_rounding_by_api_and_cli():
    candidate = payload([{"id": "0", "cohort": "z", "loss_a": 9_007_199_254_740_993, "loss_b": 0}],
                        upper=9_007_199_254_740_992)
    with pytest.raises(ValueError, match="within \\[0, loss_upper_bound\\]"):
        analyze(candidate)

    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_missing_bounds"],
        input=json.dumps(candidate), text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "within [0, loss_upper_bound]" in completed.stderr


def test_maximum_roster_bound_and_overflowing_size_rejected():
    rows = [{"id": str(index), "cohort": "c", "loss_a": 0, "loss_b": 1} for index in range(10_000)]
    result = analyze(payload(rows))["summary"]
    assert result["total_count"] == result["complete_pair_mean_denominator"] == 10_000
    rows.append({"id": "extra", "cohort": "c", "loss_a": 0, "loss_b": 1})
    with pytest.raises(ValueError, match="at most 10000"):
        analyze(payload(rows))


def test_canonical_hash_stability_and_key_order_independence():
    first = payload()
    second = {key: first[key] for key in reversed(list(first))}
    second["rows"] = [{key: row[key] for key in reversed(list(row))} for row in first["rows"]]
    assert analyze(first)["input_sha256"] == analyze(second)["input_sha256"]
    assert analyze(first) == analyze(first)


def test_extreme_valid_bound_has_finite_arithmetic():
    upper = 1.7e308
    result = analyze(payload([{"id": "a", "cohort": "c", "loss_a": None, "loss_b": None}], upper=upper))["summary"]
    assert result["all_roster_lower_mean_delta"] == -upper
    assert result["all_roster_upper_mean_delta"] == upper
    assert all(math.isfinite(result[key]) for key in ("all_roster_lower_mean_delta", "all_roster_upper_mean_delta"))


def test_large_integer_deltas_are_exact_in_summary_cohorts_and_cli():
    upper = 9_007_199_254_740_994
    candidate = payload([
        {"id": "down", "cohort": "down", "loss_a": 9_007_199_254_740_993, "loss_b": 9_007_199_254_740_992},
        {"id": "up", "cohort": "up", "loss_a": 9_007_199_254_740_992, "loss_b": 9_007_199_254_740_993},
    ], upper=upper)
    result = analyze(candidate)
    assert result["summary"]["complete_pair_mean_delta"] == 0
    assert result["summary"]["all_roster_lower_mean_delta"] == 0
    cohorts = {item["cohort"]: item for item in result["details"]}
    for name, delta in (("down", -1), ("up", 1)):
        cohort = cohorts[name]
        assert cohort["complete_pair_mean_delta"] == delta
        assert cohort["all_roster_lower_mean_delta"] == delta
        assert cohort["all_roster_upper_mean_delta"] == delta
    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_missing_bounds"],
        input=json.dumps(candidate), text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0 and completed.stderr == ""
    replay = json.loads(completed.stdout)
    assert replay == result
    assert replay["details"][0]["complete_pair_mean_delta"] == -1


def test_exact_mixed_arithmetic_fractional_means_and_cancellation():
    candidate = payload([
        {"id": "i", "cohort": "mixed", "loss_a": 9_007_199_254_740_993, "loss_b": 9_007_199_254_740_992.0},
        {"id": "f", "cohort": "mixed", "loss_a": 0.0, "loss_b": 0.5},
        {"id": "cancel-a", "cohort": "cancel", "loss_a": 0, "loss_b": 10**400},
        {"id": "cancel-b", "cohort": "cancel", "loss_a": 10**400, "loss_b": 0},
    ], upper=10**400)
    result = analyze(candidate)
    summary = result["summary"]
    assert summary["complete_pair_mean_delta"] == -0.125
    cohorts = {item["cohort"]: item for item in result["details"]}
    assert cohorts["mixed"]["complete_pair_mean_delta"] == -0.25
    assert cohorts["cancel"]["complete_pair_mean_delta"] == 0
    assert cohorts["cancel"]["all_roster_lower_mean_delta"] == 0
    assert summary["all_roster_lower_mean_delta"] == -0.125


@pytest.mark.parametrize("upper", [5e-324, 1.7976931348623157e308])
def test_subnormal_and_near_maximum_finite_bounds(upper):
    result = analyze(payload([{"id": "m", "cohort": "c", "loss_a": None, "loss_b": None}], upper=upper))["summary"]
    assert result["all_roster_lower_mean_delta"] == -upper
    assert result["all_roster_upper_mean_delta"] == upper
    assert math.isfinite(result["all_roster_lower_mean_delta"])
    assert math.isfinite(result["all_roster_upper_mean_delta"])


def test_exact_integer_upper_bound_beyond_binary64_is_preserved():
    upper = 10**400
    result = analyze(payload([{"id": "m", "cohort": "c", "loss_a": upper - 1, "loss_b": upper}], upper=upper))
    assert result["summary"]["loss_upper_bound"] == upper
    assert result["summary"]["complete_pair_mean_delta"] == 1
    assert result["summary"]["all_roster_lower_mean_delta"] == 1


def test_cli_success_emits_one_deterministic_json_envelope():
    encoded = json.dumps(payload(), separators=(",", ":"))
    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_missing_bounds"],
        input=encoded, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0 and completed.stderr == ""
    result = json.loads(completed.stdout)
    assert result["summary"]["all_roster_lower_mean_delta"] == pytest.approx(-0.575)
    assert completed.stdout.endswith("\n")
