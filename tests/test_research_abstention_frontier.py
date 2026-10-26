from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from memecoin_runner.abstention_frontier import analyze


def example() -> dict:
    return {
        "thresholds": [0.8, 0.25, 0.5],
        "rows": [
            {"id": "low", "uncertainty": 0.2, "supported": True, "loss": 2},
            {"id": "high", "uncertainty": 0.7, "supported": True, "loss": 6},
            {"id": "unsupported", "uncertainty": 0.1, "supported": False, "loss": 1},
            {"id": "unknown-uncertainty", "uncertainty": None, "supported": True, "loss": 3},
            {"id": "unknown-outcome", "uncertainty": 0.4, "supported": True, "loss": None},
        ],
    }


def test_curve_counts_rejection_reasons_and_known_loss_support() -> None:
    result = analyze(example())
    assert result["schema"] == "brain-research-diagnostic.v1"
    assert result["kind"] == "abstention_frontier"
    assert [point["threshold"] for point in result["details"]] == [0.25, 0.5, 0.8]
    low, mid, high = result["details"]
    assert (low["accepted_count"], low["coverage"], low["mean_loss"]) == (1, 0.2, 2.0)
    assert low["rejected"] == {
        "unsupported_count": 1,
        "missing_uncertainty_count": 1,
        "above_threshold_count": 2,
    }
    assert mid["accepted_count"] == 2
    assert mid["accepted_known_loss_count"] == 1
    assert mid["accepted_missing_loss_count"] == 1
    assert mid["mean_loss"] == 2.0
    assert (high["accepted_count"], high["coverage"], high["mean_loss"]) == (3, 0.6, 4.0)
    assert sum(high["rejected"].values()) + high["accepted_count"] == 5


def test_empty_rows_have_null_coverage_and_loss_metrics() -> None:
    result = analyze({"thresholds": [0], "rows": []})["details"][0]
    assert result["coverage"] is None
    assert result["mean_loss"] is None
    assert result["accepted_known_loss_count"] == result["accepted_missing_loss_count"] == 0


@pytest.mark.parametrize("payload,message", [
    ({"thresholds": [True], "rows": []}, "threshold_must_be_finite_number"),
    ({"thresholds": [float("nan")], "rows": []}, "threshold_out_of_range"),
    ({"thresholds": [0, 0.0], "rows": []}, "thresholds_must_be_distinct"),
    ({"thresholds": [0], "rows": [{"id": "x", "uncertainty": 0, "supported": 1, "loss": 0}]}, "supported_must_be_boolean"),
    ({"thresholds": [0], "rows": [
        {"id": "x", "uncertainty": 0, "supported": True, "loss": 0},
        {"id": "x", "uncertainty": 0, "supported": True, "loss": 0},
    ]}, "row_ids_must_be_unique"),
    ({"thresholds": [0], "rows": [{"id": "x", "uncertainty": True, "supported": True, "loss": 0}]}, "uncertainty_must_be_finite_number"),
    ({"thresholds": [0], "rows": [{"id": "x", "uncertainty": 0, "supported": True, "loss": -1}]}, "loss_out_of_range"),
])
def test_invalid_inputs_raise_stable_value_error(payload: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        analyze(payload)


def test_input_hash_is_canonical_and_cli_emits_one_envelope() -> None:
    payload = example()
    expected = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")).hexdigest()
    result = analyze(payload)
    assert result["input_sha256"] == expected
    process = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.abstention_frontier"],
        input=json.dumps(payload), text=True, capture_output=True, check=True,
    )
    assert json.loads(process.stdout) == result
    assert process.stderr == ""


def test_cli_rejects_json_nonfinite_values() -> None:
    process = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.abstention_frontier"],
        input='{"thresholds":[NaN],"rows":[]}', text=True, capture_output=True,
    )
    assert process.returncode == 2
    assert process.stdout == ""
    assert "nonfinite_json_number" in process.stderr
