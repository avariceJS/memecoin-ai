import hashlib
import json
import math
import subprocess
import sys
from fractions import Fraction

import pytest

from memecoin_runner.paired_loss_cluster_influence import analyze


def payload(rows=None):
    return {
        "model_a": "baseline",
        "model_b": "candidate",
        "metric": "loss",
        "rows": rows if rows is not None else [
            {"id": "x", "cluster": "x", "loss_a": 4, "loss_b": 0},
            {"id": "y", "cluster": "y", "loss_a": 0, "loss_b": 1},
            {"id": "z", "cluster": "z", "loss_a": 0, "loss_b": 1},
        ],
    }


def test_hand_computable_reversal_example_and_exact_envelope():
    candidate = payload()
    result = analyze(candidate)
    assert result["schema"] == "brain-research-diagnostic.v1"
    assert result["kind"] == "paired_loss_cluster_influence"
    assert result["input_sha256"] == hashlib.sha256(
        json.dumps(candidate, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    assert result["summary"] == {
        "model_a": "baseline", "model_b": "candidate", "metric": "loss",
        "row_count": 3, "cluster_count": 3, "full_mean_delta": -2 / 3,
        "defined_removal_count": 3, "sign_reversal_count": 1,
        "minimum_leave_out_mean_delta": -1.5, "maximum_leave_out_mean_delta": 1,
        "max_abs_shift": 5 / 3,
    }
    assert result["details"] == [
        {"cluster": "x", "row_count": 1, "sum_delta": -4, "mean_delta": -4,
         "remaining_row_count": 2, "leave_out_mean_delta": 1, "shift": 5 / 3, "sign_reversal": True},
        {"cluster": "y", "row_count": 1, "sum_delta": 1, "mean_delta": 1,
         "remaining_row_count": 2, "leave_out_mean_delta": -1.5, "shift": -5 / 6, "sign_reversal": False},
        {"cluster": "z", "row_count": 1, "sum_delta": 1, "mean_delta": 1,
         "remaining_row_count": 2, "leave_out_mean_delta": -1.5, "shift": -5 / 6, "sign_reversal": False},
    ]
    assert "not confidence intervals" in " ".join(result["limitations"])


def test_unequal_clusters_use_remaining_row_counts_and_bruteforce_oracle():
    candidate = payload([
        {"id": "a1", "cluster": "a", "loss_a": 10, "loss_b": 0},
        {"id": "a2", "cluster": "a", "loss_a": 0, "loss_b": 1},
        {"id": "b1", "cluster": "b", "loss_a": 0, "loss_b": 2},
        {"id": "c1", "cluster": "c", "loss_a": 3, "loss_b": 0},
        {"id": "c2", "cluster": "c", "loss_a": 0, "loss_b": 4},
    ])
    result = analyze(candidate)
    deltas = [Fraction(row["loss_b"] - row["loss_a"]) for row in candidate["rows"]]
    full = sum(deltas, Fraction()) / len(deltas)
    assert result["summary"]["full_mean_delta"] == float(full)
    for detail in result["details"]:
        kept = [Fraction(row["loss_b"] - row["loss_a"])
                for row in candidate["rows"] if row["cluster"] != detail["cluster"]]
        oracle = sum(kept, Fraction()) / len(kept)
        assert detail["remaining_row_count"] == len(kept)
        assert detail["leave_out_mean_delta"] == float(oracle)
        assert detail["sign_reversal"] == ((full < 0 < oracle) or (oracle < 0 < full))


def test_empty_single_cluster_ties_zero_and_sorted_labels():
    empty = analyze(payload([]))
    assert empty["summary"]["row_count"] == empty["summary"]["cluster_count"] == 0
    assert empty["summary"]["full_mean_delta"] is None
    assert empty["summary"]["minimum_leave_out_mean_delta"] is None
    assert empty["details"] == []

    one = analyze(payload([{"id": "a", "cluster": "only", "loss_a": 0, "loss_b": 1}]))
    assert one["details"][0] == {
        "cluster": "only", "row_count": 1, "sum_delta": 1, "mean_delta": 1,
        "remaining_row_count": 0, "leave_out_mean_delta": None, "shift": None, "sign_reversal": None,
    }
    assert one["summary"]["defined_removal_count"] == 0
    assert one["summary"]["max_abs_shift"] is None

    tie = analyze(payload([
        {"id": "a", "cluster": "z", "loss_a": 1, "loss_b": 0},
        {"id": "b", "cluster": "a", "loss_a": 0, "loss_b": 1},
    ]))
    assert [d["cluster"] for d in tie["details"]] == ["a", "z"]
    assert tie["summary"]["full_mean_delta"] == 0
    assert all(d["sign_reversal"] is False for d in tie["details"])


@pytest.mark.parametrize("candidate", [
    None,
    {"model_a": "a", "model_b": "b", "metric": "m", "rows": [], "extra": 0},
    {"model_a": " ", "model_b": "b", "metric": "m", "rows": []},
    {"model_a": "a", "model_b": "b", "metric": "m", "rows": {}},
    payload([{"id": "x", "cluster": "c", "loss_a": 0, "loss_b": None}]),
    payload([{"id": "x", "cluster": "c", "loss_a": True, "loss_b": 0}]),
    payload([{"id": "x", "cluster": "c", "loss_a": -1, "loss_b": 0}]),
    payload([{"id": "x", "cluster": "c", "loss_a": float("nan"), "loss_b": 0}]),
    payload([{"id": "x", "cluster": "c", "loss_a": 0, "loss_b": 1, "extra": 0}]),
    payload([{"id": "x", "cluster": "c", "loss_a": 0, "loss_b": 1},
             {"id": "x", "cluster": "d", "loss_a": 0, "loss_b": 1}]),
])
def test_invalid_api_inputs_raise_value_error(candidate):
    with pytest.raises(ValueError):
        analyze(candidate)


def test_permutations_preserve_math_and_keys_preserve_hash():
    first = payload()
    second = {key: first[key] for key in reversed(list(first))}
    second["rows"] = [
        {key: row[key] for key in reversed(list(row))}
        for row in reversed(first["rows"])
    ]
    a, b = analyze(first), analyze(second)
    assert a["input_sha256"] != b["input_sha256"]
    assert a["summary"] == b["summary"]
    assert a["details"] == b["details"]
    same = {key: first[key] for key in reversed(list(first))}
    same["rows"] = [{key: row[key] for key in reversed(list(row))} for row in first["rows"]]
    assert analyze(first)["input_sha256"] == analyze(same)["input_sha256"]


def test_exact_huge_integer_deltas_and_integral_cli_output_growth():
    huge = 10**400
    candidate = payload([
        {"id": "down", "cluster": "down", "loss_a": huge, "loss_b": 0},
        {"id": "up", "cluster": "up", "loss_a": 0, "loss_b": huge},
    ])
    result = analyze(candidate)
    assert result["summary"]["full_mean_delta"] == 0
    assert result["details"][0]["sum_delta"] == -huge

    # Each input is below Python's normal integer parser limit; their aggregate
    # exceeds it and must still be emitted as an exact JSON integer.
    large = 10**4299
    many = payload([{"id": str(i), "cluster": str(i), "loss_a": 0, "loss_b": large}
                    for i in range(10_000)])
    output = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_cluster_influence"],
        input=json.dumps(many), text=True, capture_output=True, check=False,
    )
    assert output.returncode == 0 and output.stderr == ""
    assert '"full_mean_delta":100000' in output.stdout
    assert output.stdout.endswith("\n")
    assert len(output.stdout) > 4300


def test_duplicate_keys_nonfinite_cli_errors_and_success_parity():
    module = "memecoin_runner.paired_loss_cluster_influence"
    failed = subprocess.run([sys.executable, "-m", module], input='{"rows":[],"rows":[]}',
                            text=True, capture_output=True, check=False)
    assert failed.returncode == 2 and failed.stdout == ""
    assert "duplicate JSON object key" in json.loads(failed.stderr)["error"]
    nonfinite = subprocess.run([sys.executable, "-m", module], input='{"x":NaN}',
                               text=True, capture_output=True, check=False)
    assert nonfinite.returncode == 2 and nonfinite.stdout == ""
    assert "invalid JSON constant" in nonfinite.stderr

    candidate = payload()
    completed = subprocess.run([sys.executable, "-m", module], input=json.dumps(candidate),
                               text=True, capture_output=True, check=False)
    assert completed.returncode == 0 and completed.stderr == ""
    assert completed.stdout == json.dumps(analyze(candidate), sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=False, allow_nan=False) + "\n"
    assert math.isfinite(json.loads(completed.stdout)["summary"]["full_mean_delta"])
