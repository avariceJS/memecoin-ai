import hashlib
import json
import math
import subprocess
import sys
from fractions import Fraction

import pytest

from memecoin_runner.paired_loss_target_mix import analyze


def payload():
    return {
        "model_a": "same",
        "model_b": "same",
        "metric": "loss",
        "target_cohorts": [
            {"cohort": "x", "weight": 1},
            {"cohort": "y", "weight": 3},
        ],
        "rows": [
            {"id": "x1", "cohort": "x", "loss_a": 1, "loss_b": 0},
            {"id": "x2", "cohort": "x", "loss_a": 1, "loss_b": 0},
            {"id": "x3", "cohort": "x", "loss_a": 1, "loss_b": 0},
            {"id": "y1", "cohort": "y", "loss_a": 0, "loss_b": 1},
        ],
    }


def test_example_target_mix_changes_direction_and_schema_is_exact():
    result = analyze(payload())
    summary = result["summary"]
    assert result["schema"] == "brain-research-diagnostic.v1"
    assert result["kind"] == "paired_loss_target_mix"
    assert set(result) == {"schema", "kind", "input_sha256", "summary", "details", "limitations"}
    assert summary == {
        "model_a": "same",
        "model_b": "same",
        "metric": "loss",
        "row_count": 4,
        "declared_cohort_count": 2,
        "supported_cohort_count": 2,
        "unsupported_positive_weight_cohort_count": 0,
        "target_weight_total": 4,
        "supported_target_weight": 4,
        "supported_target_share": 1,
        "pooled_mean_delta": -0.5,
        "target_mean_delta": 0.5,
        "target_minus_pooled": 1,
    }
    assert [item["cohort"] for item in result["details"]] == ["x", "y"]
    assert set(result["details"][0]) == {
        "cohort", "row_count", "sum_delta", "mean_delta", "target_weight",
        "target_share", "supported", "weighted_contribution",
    }
    assert result["details"][0]["weighted_contribution"] == -0.25
    assert result["details"][1]["weighted_contribution"] == 0.75


def test_unsupported_positive_and_zero_weight_cohorts_are_not_renormalized():
    value = payload()
    value["target_cohorts"].extend([
        {"cohort": "missing", "weight": 2},
        {"cohort": "zero", "weight": 0},
    ])
    result = analyze(value)
    summary = result["summary"]
    assert summary["pooled_mean_delta"] == -0.5
    assert summary["target_mean_delta"] is None
    assert summary["target_minus_pooled"] is None
    assert summary["supported_target_weight"] == 4
    assert summary["supported_target_share"] == pytest.approx(2 / 3)
    assert summary["unsupported_positive_weight_cohort_count"] == 1
    by_cohort = {item["cohort"]: item for item in result["details"]}
    assert by_cohort["missing"]["weighted_contribution"] is None
    assert by_cohort["zero"]["weighted_contribution"] == 0
    assert by_cohort["zero"]["supported"] is False


def test_zero_weight_empty_cohort_does_not_break_target_mean():
    value = payload()
    value["target_cohorts"].append({"cohort": "empty", "weight": 0})
    assert analyze(value)["summary"]["target_mean_delta"] == 0.5


def test_empty_rows_and_empty_weighted_support_have_explicit_nulls():
    value = payload()
    value["rows"] = []
    result = analyze(value)
    summary = result["summary"]
    assert summary["supported_target_share"] == 0
    assert summary["pooled_mean_delta"] is None
    assert summary["target_mean_delta"] is None
    assert summary["target_minus_pooled"] is None
    assert all(item["mean_delta"] is None for item in result["details"])


def test_hash_is_canonical_without_mutating_payload_and_permutations_preserve_math():
    value = payload()
    original = json.loads(json.dumps(value))
    expected = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    result = analyze(value)
    assert result["input_sha256"] == expected
    assert value == original
    reordered_objects = {
        "rows": [dict(reversed(list(row.items()))) for row in value["rows"]],
        "target_cohorts": [
            dict(reversed(list(cohort.items()))) for cohort in value["target_cohorts"]
        ],
        "metric": value["metric"],
        "model_b": value["model_b"],
        "model_a": value["model_a"],
    }
    assert analyze(reordered_objects)["input_sha256"] == expected
    permuted = payload()
    permuted["rows"].reverse()
    permuted["target_cohorts"].reverse()
    assert analyze(permuted)["summary"] == result["summary"]


def test_exact_arithmetic_handles_large_integer_cancellation_and_float_extremes():
    value = payload()
    value["target_cohorts"] = [{"cohort": "x", "weight": 1}]
    value["rows"] = [
        {"id": "large1", "cohort": "x", "loss_a": 2**53 + 1, "loss_b": 2**53},
        {"id": "large2", "cohort": "x", "loss_a": 0, "loss_b": 1},
    ]
    result = analyze(value)
    assert result["details"][0]["sum_delta"] == 0
    assert result["summary"]["pooled_mean_delta"] == 0

    value["rows"] = [
        {"id": "huge", "cohort": "x", "loss_a": 0, "loss_b": 10**400},
    ]
    assert analyze(value)["summary"]["target_mean_delta"] == 10**400

    value["rows"] = [
        {"id": "subnormal", "cohort": "x", "loss_a": 0.0, "loss_b": math.ulp(0.0)},
    ]
    assert analyze(value)["summary"]["target_mean_delta"] == math.ulp(0.0)

    value["rows"] = [
        {"id": "max", "cohort": "x", "loss_a": 0.0, "loss_b": sys.float_info.max},
    ]
    assert analyze(value)["summary"]["target_mean_delta"] == sys.float_info.max


def test_target_mean_is_within_cohort_duplicate_invariant_and_weight_scale_invariant():
    original = payload()
    baseline = analyze(original)["summary"]["target_mean_delta"]
    duplicated = payload()
    duplicated["rows"].append({"id": "x4", "cohort": "x", "loss_a": 1, "loss_b": 0})
    assert analyze(duplicated)["summary"]["target_mean_delta"] == baseline
    scaled = payload()
    for cohort in scaled["target_cohorts"]:
        cohort["weight"] *= 100
    assert analyze(scaled)["summary"]["target_mean_delta"] == baseline


def test_target_mean_matches_independently_expanded_integer_weight_population():
    value = payload()
    value["target_cohorts"] = [
        {"cohort": "x", "weight": 2},
        {"cohort": "y", "weight": 3},
        {"cohort": "z", "weight": 4},
    ]
    value["rows"] = [
        {"id": "x1", "cohort": "x", "loss_a": 3, "loss_b": 1},
        {"id": "x2", "cohort": "x", "loss_a": 0, "loss_b": 2},
        {"id": "y1", "cohort": "y", "loss_a": 1, "loss_b": 5},
        {"id": "y2", "cohort": "y", "loss_a": 3, "loss_b": 0},
        {"id": "y3", "cohort": "y", "loss_a": 0, "loss_b": 2},
        {"id": "z1", "cohort": "z", "loss_a": 9, "loss_b": 8},
        {"id": "z2", "cohort": "z", "loss_a": 1, "loss_b": 2},
    ]
    # For integer cohort weights, replicate every row in c by
    # weight[c] * lcm(cohort counts) / count[c].
    counts = {"x": 2, "y": 3, "z": 2}
    weights = {item["cohort"]: item["weight"] for item in value["target_cohorts"]}
    common_multiple = math.lcm(*counts.values())
    expanded_deltas = []
    for row in value["rows"]:
        multiplier = (
            weights[row["cohort"]] * common_multiple // counts[row["cohort"]]
        )
        expanded_deltas.extend([row["loss_b"] - row["loss_a"]] * multiplier)
    independent_mean = Fraction(sum(expanded_deltas), len(expanded_deltas))
    assert analyze(value)["summary"]["target_mean_delta"] == float(independent_mean)


def test_noninteger_and_huge_statistics_match_fraction_oracle():
    value = payload()
    value["target_cohorts"] = [
        {"cohort": "x", "weight": 1.0},
        {"cohort": "y", "weight": 2.0},
    ]
    value["rows"] = [
        {"id": "x", "cohort": "x", "loss_a": 0.1, "loss_b": 0.3},
        {"id": "y", "cohort": "y", "loss_a": 0.2, "loss_b": 0.7},
    ]
    weight_x, weight_y = Fraction(1.0), Fraction(2.0)
    delta_x = Fraction(0.3) - Fraction(0.1)
    delta_y = Fraction(0.7) - Fraction(0.2)
    oracle = (weight_x * delta_x + weight_y * delta_y) / (weight_x + weight_y)
    assert analyze(value)["summary"]["target_mean_delta"] == float(oracle)

    value["target_cohorts"] = [{"cohort": "x", "weight": 1}]
    value["rows"] = [{"id": "large", "cohort": "x", "loss_a": 0, "loss_b": 10**400}]
    value["rows"].append(
        {"id": "zero", "cohort": "x", "loss_a": 0, "loss_b": 0}
    )
    exact = Fraction(10**400, 2)
    assert analyze(value)["summary"]["target_mean_delta"] == round(exact)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(extra=1),
        lambda p: p["target_cohorts"].append({"cohort": "x", "weight": 1}),
        lambda p: p["target_cohorts"].__setitem__(0, {"cohort": "x", "weight": True}),
        lambda p: p["target_cohorts"].__setitem__(0, {"cohort": "x", "weight": float("nan")}),
        lambda p: p["target_cohorts"].__setitem__(0, {"cohort": "x", "weight": -1}),
        lambda p: p["target_cohorts"].__setitem__(slice(None), [
            {"cohort": "x", "weight": 0}, {"cohort": "y", "weight": 0}
        ]),
        lambda p: p["rows"].__setitem__(0, {"id": "x1", "cohort": "x", "loss_a": None, "loss_b": 0}),
        lambda p: p["rows"].__setitem__(0, {"id": "x1", "cohort": "x", "loss_a": True, "loss_b": 0}),
        lambda p: p["rows"].__setitem__(0, {"id": "x1", "cohort": "x", "loss_a": -1, "loss_b": 0}),
        lambda p: p["rows"].__setitem__(0, {"id": "x1", "cohort": "undeclared", "loss_a": 0, "loss_b": 0}),
        lambda p: p["rows"].append({"id": "x1", "cohort": "y", "loss_a": 0, "loss_b": 0}),
    ],
)
def test_invalid_inputs_raise_value_error(mutate):
    value = payload()
    mutate(value)
    with pytest.raises(ValueError):
        analyze(value)


def test_non_json_api_types_and_rows_cap_are_rejected():
    value = payload()
    value["rows"] = tuple(value["rows"])
    with pytest.raises(ValueError):
        analyze(value)

    value = payload()
    value["target_cohorts"] = [
        {"cohort": f"c{i}", "weight": 1} for i in range(10_001)
    ]
    with pytest.raises(ValueError):
        analyze(value)

    value = payload()
    value["rows"] = [
        {"id": str(i), "cohort": "x", "loss_a": 0, "loss_b": 0}
        for i in range(10_001)
    ]
    with pytest.raises(ValueError):
        analyze(value)


def test_cli_success_is_canonical_and_cli_rejects_duplicate_keys_and_bad_values():
    encoded = json.dumps(payload(), ensure_ascii=False)
    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_target_mix"],
        input=encoded,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout.endswith("\n")
    assert completed.stdout == json.dumps(
        json.loads(completed.stdout), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ) + "\n"

    for bad in [
        "{}",
        '{"model_a":"a","model_a":"b"}',
        '{"model_a":"a","model_b":"b","metric":"m","target_cohorts":[{"cohort":"x","weight":1}],"rows":[],"extra":NaN}',
    ]:
        failed = subprocess.run(
            [sys.executable, "-m", "memecoin_runner.paired_loss_target_mix"],
            input=bad,
            text=True,
            capture_output=True,
            check=False,
        )
        assert failed.returncode == 2
        assert failed.stdout == ""
        error = json.loads(failed.stderr)
        assert set(error) == {"error"}
        assert isinstance(error["error"], str)


def test_cli_serializes_valid_4300_digit_weights_without_raising_parser_limit():
    weight = 9 * 10**4299
    value = {
        "model_a": "a",
        "model_b": "b",
        "metric": "m",
        "target_cohorts": [
            {"cohort": "x", "weight": weight},
            {"cohort": "y", "weight": weight},
        ],
        "rows": [],
    }
    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_target_mix"],
        input=json.dumps(value),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout.endswith("\n")
    output = json.loads(completed.stdout, parse_int=lambda token: token)
    assert output["summary"]["target_weight_total"] == "18" + "0" * 4299


def test_cli_over_limit_integer_is_rejected_as_json_without_relaxing_parser_limit():
    prefix = (
        '{"model_a":"a","model_b":"b","metric":"m",'
        '"target_cohorts":[{"cohort":"x","weight":'
    )
    suffix = '}],"rows":[]}'
    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_target_mix"],
        input=prefix + "9" * 4301 + suffix,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    error = json.loads(completed.stderr)
    assert "limit" in error["error"]
