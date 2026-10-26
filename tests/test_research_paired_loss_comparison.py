import hashlib
import json
import subprocess
import sys

import pytest

from memecoin_runner.paired_loss_comparison import analyze


def example_payload():
    return {
        "model_a": "baseline",
        "model_b": "candidate",
        "metric": "log_loss",
        "rows": [
            {"id": "l1", "cohort": "large", "loss_a": 5.0, "loss_b": 4.0},
            {"id": "l2", "cohort": "large", "loss_a": 5.0, "loss_b": 4.0},
            {"id": "l3", "cohort": "large", "loss_a": 5.0, "loss_b": 4.0},
            {"id": "s1", "cohort": "small", "loss_a": 0.0, "loss_b": 10.0},
            {"id": "a-only", "cohort": "large", "loss_a": 2.0, "loss_b": None},
            {"id": "b-only", "cohort": "small", "loss_a": None, "loss_b": 3.0},
            {"id": "missing", "cohort": "unsupported", "loss_a": None, "loss_b": None},
        ],
    }


def test_paired_support_and_micro_macro_means():
    result = analyze(example_payload())
    summary = result["summary"]
    assert result["schema"] == "brain-research-diagnostic.v1"
    assert result["kind"] == "paired_loss_comparison"
    assert summary["paired"] == 4
    assert summary["a_only"] == summary["b_only"] == summary["both_missing"] == 1
    assert summary["supported_cohorts"] == 2
    assert summary["micro_mean_delta"] == pytest.approx(1.75)
    assert summary["macro_mean_delta"] == pytest.approx(4.5)
    by_cohort = {item["cohort"]: item for item in result["details"]}
    assert by_cohort["large"]["mean_delta"] == -1
    assert (by_cohort["large"]["a_wins"], by_cohort["large"]["b_wins"], by_cohort["large"]["ties"]) == (0, 3, 0)
    assert by_cohort["small"]["mean_delta"] == 10
    assert by_cohort["unsupported"]["mean_delta"] is None
    assert by_cohort["unsupported"]["paired"] == 0


def test_input_hash_uses_frozen_canonical_encoding():
    payload = example_payload()
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()
    assert analyze(payload)["input_sha256"] == expected


@pytest.mark.parametrize(
    "payload, message",
    [
        (None, "payload must be an object"),
        ({"model_a": "a", "model_b": "a", "metric": "m", "rows": []}, "model_a and model_b must be distinct"),
        ({"model_a": "a", "model_b": "b", "metric": " ", "rows": []}, "metric must be a nonempty string"),
        ({"model_a": "a", "model_b": "b", "metric": "m", "rows": {}}, "rows must be a list"),
        (
            {"model_a": "a", "model_b": "b", "metric": "m", "rows": [
                {"id": "x", "cohort": "c", "loss_a": True, "loss_b": 1}
            ]},
            "rows[0].loss_a must be a nonnegative finite number or null",
        ),
        (
            {"model_a": "a", "model_b": "b", "metric": "m", "rows": [
                {"id": "x", "cohort": "c", "loss_a": 0, "loss_b": 1},
                {"id": "x", "cohort": "d", "loss_a": 0, "loss_b": 1},
            ]},
            "row ids must be unique",
        ),
        (
            {"model_a": "a", "model_b": "b", "metric": "m", "rows": [
                {"id": "x", "cohort": "c", "loss_a": 0, "loss_b": float("inf")}
            ]},
            "payload must contain finite JSON values",
        ),
    ],
)
def test_invalid_inputs_raise_stable_value_error(payload, message):
    with pytest.raises(ValueError, match=message.replace("[", r"\[")):
        analyze(payload)


def test_empty_input_has_explicit_zero_support():
    result = analyze({"model_a": "a", "model_b": "b", "metric": "m", "rows": []})
    assert result["summary"]["micro_mean_delta"] is None
    assert result["summary"]["macro_mean_delta"] is None
    assert result["summary"]["supported_cohorts"] == 0


def test_module_cli_reads_stdin_and_emits_one_json_result():
    encoded = json.dumps(example_payload(), ensure_ascii=False)
    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_comparison"],
        input=encoded,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    result = json.loads(completed.stdout)
    assert result["summary"]["micro_mean_delta"] == pytest.approx(1.75)


def test_module_cli_rejects_nonfinite_json_with_nonzero_exit():
    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_comparison"],
        input='{"model_a":"a","model_b":"b","metric":"m","rows":[],"extra":NaN}',
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "invalid JSON constant: NaN" in completed.stderr
