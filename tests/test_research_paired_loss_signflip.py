import hashlib
import json
import subprocess
import sys

import pytest

from memecoin_runner.paired_loss_signflip import analyze


def test_hand_computable_exact_tail_and_signed_mean():
    payload = {
        "pairs": [
            {"id": "a", "left_loss": 2, "right_loss": 3},
            {"id": "b", "left_loss": 4, "right_loss": 2},
        ]
    }
    result = analyze(payload)
    assert result["schema"] == "brain-research-diagnostic.v1"
    assert result["kind"] == "paired_loss_signflip"
    assert result["summary"] == {
        "pairs": 2,
        "observed_mean_signed_difference": -0.5,
        "exact_two_sided_tail_count": 4,
        "sign_flip_assignments": 4,
        "exact_two_sided_p_value": 1.0,
    }


def test_ties_and_extreme_observation_are_counted_in_two_sided_tail():
    tied = analyze({"pairs": [
        {"id": "a", "left_loss": 1, "right_loss": 2},
        {"id": "b", "left_loss": 1, "right_loss": 2},
    ]})
    assert tied["summary"]["exact_two_sided_tail_count"] == 2
    assert tied["summary"]["exact_two_sided_p_value"] == 0.5

    extreme = analyze({"pairs": [
        {"id": "a", "left_loss": 0, "right_loss": 5},
        {"id": "b", "left_loss": 0, "right_loss": 5},
        {"id": "c", "left_loss": 0, "right_loss": 5},
    ]})
    assert extreme["summary"]["exact_two_sided_tail_count"] == 2
    assert extreme["summary"]["exact_two_sided_p_value"] == 0.25


def test_empty_input_has_null_statistics_and_zero_support():
    summary = analyze({"pairs": []})["summary"]
    assert summary["pairs"] == 0
    assert summary["observed_mean_signed_difference"] is None
    assert summary["exact_two_sided_tail_count"] == 0
    assert summary["sign_flip_assignments"] == 1
    assert summary["exact_two_sided_p_value"] is None


def test_twenty_pair_limit_and_large_finite_losses():
    payload = {"pairs": [
        {"id": str(index), "left_loss": 0.0, "right_loss": 1e308}
        for index in range(20)
    ]}
    summary = analyze(payload)["summary"]
    assert summary["sign_flip_assignments"] == 1 << 20
    assert summary["observed_mean_signed_difference"] == 1e308
    with pytest.raises(ValueError, match="at most 20"):
        analyze({"pairs": payload["pairs"] + [
            {"id": "extra", "left_loss": 0, "right_loss": 1}
        ]})


@pytest.mark.parametrize(
    "payload, message",
    [
        (None, "payload must be an object"),
        ({"pairs": [], "extra": 1}, "payload must contain exactly pairs"),
        ({"pairs": {}}, "pairs must be a list"),
        ({"pairs": [{"id": "x", "left_loss": 1}]}, r"pairs\[0\] must contain exactly"),
        ({"pairs": [{"id": " ", "left_loss": 1, "right_loss": 1}]}, "nonempty string"),
        ({"pairs": [
            {"id": "x", "left_loss": 1, "right_loss": 2},
            {"id": "x", "left_loss": 2, "right_loss": 3},
        ]}, "pair ids must be unique"),
        ({"pairs": [{"id": "x", "left_loss": True, "right_loss": 1}]}, "nonnegative finite number"),
        ({"pairs": [{"id": "x", "left_loss": 0, "right_loss": float("inf")}]}, "finite JSON values"),
    ],
)
def test_malformed_inputs_raise_value_error(payload, message):
    with pytest.raises(ValueError, match=message):
        analyze(payload)


def test_canonical_input_hash_is_deterministic():
    payload = {"pairs": [{"id": "κ", "left_loss": 1, "right_loss": 2}]}
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()
    assert analyze(payload)["input_sha256"] == expected
    assert analyze(payload) == analyze(payload)


def test_module_cli_success_and_failure():
    encoded = json.dumps({"pairs": [{"id": "x", "left_loss": 0, "right_loss": 2}]})
    completed = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_signflip"],
        input=encoded,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert json.loads(completed.stdout)["summary"]["exact_two_sided_tail_count"] == 2

    failed = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_signflip"],
        input='{"pairs":[],"extra":NaN}',
        text=True,
        capture_output=True,
        check=False,
    )
    assert failed.returncode == 2
    assert failed.stdout == ""
    assert "invalid JSON constant: NaN" in failed.stderr
