import hashlib
import itertools
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


def test_explicit_directional_alternatives_use_correct_signed_tails():
    # Differences are [-4, -1, 2], with observed sum -3. Of the eight
    # sign assignments, three sums are <= -3 and six are >= -3.
    pairs = [
        {"id": "a", "left_loss": 4, "right_loss": 0},
        {"id": "b", "left_loss": 1, "right_loss": 0},
        {"id": "c", "left_loss": 0, "right_loss": 2},
    ]
    less = analyze({"pairs": pairs, "alternative": "less"})
    greater = analyze({"pairs": pairs, "alternative": "greater"})
    assert less["summary"]["observed_mean_signed_difference"] == -1
    assert less["summary"]["alternative"] == "less"
    assert less["summary"]["exact_less_tail_count"] == 3
    assert less["summary"]["exact_less_p_value"] == 3 / 8
    assert "exact_two_sided_p_value" not in less["summary"]
    assert greater["summary"]["exact_greater_tail_count"] == 6
    assert greater["summary"]["exact_greater_p_value"] == 6 / 8
    assert "preselection" in " ".join(less["limitations"]).lower() or "before inspecting" in " ".join(less["limitations"]).lower()


@pytest.mark.parametrize("differences", [
    [1], [-2, 1], [0, 0, 0], [3, -1, 2, -5], [1e-12, -1e-12, 2e-12],
])
def test_directional_counts_match_independent_enumeration(differences):
    pairs = [
        {"id": str(index), "left_loss": max(0, -difference), "right_loss": max(0, difference)}
        for index, difference in enumerate(differences)
    ]
    observed = sum(differences)
    assignments = [sum(sign * value for sign, value in zip(signs, differences))
                   for signs in itertools.product((-1, 1), repeat=len(differences))]
    for alternative, expected in (
        ("less", sum(value <= observed for value in assignments)),
        ("greater", sum(value >= observed for value in assignments)),
    ):
        summary = analyze({"pairs": pairs, "alternative": alternative})["summary"]
        assert summary[f"exact_{alternative}_tail_count"] == expected
        assert summary[f"exact_{alternative}_p_value"] == expected / (1 << len(differences))


@pytest.mark.parametrize("differences", list(itertools.product(range(-3, 4), repeat=3)))
def test_directional_zero_sum_and_nonzero_ties_are_exact_for_small_integers(differences):
    pairs = [
        {"id": str(index), "left_loss": max(0, -difference), "right_loss": max(0, difference)}
        for index, difference in enumerate(differences)
    ]
    observed = sum(differences)
    assignments = [sum(sign * value for sign, value in zip(signs, differences))
                   for signs in itertools.product((-1, 1), repeat=3)]
    for alternative, predicate in (
        ("less", lambda value: value <= observed),
        ("greater", lambda value: value >= observed),
    ):
        expected = sum(predicate(value) for value in assignments)
        actual = analyze({"pairs": pairs, "alternative": alternative})["summary"]
        assert actual[f"exact_{alternative}_tail_count"] == expected


def test_directional_exact_ties_with_tiny_and_huge_binary_differences():
    for magnitude in (2.0 ** -1000, 2.0 ** 1000):
        pairs = [
            {"id": "a", "left_loss": 0.0, "right_loss": 3 * magnitude},
            {"id": "b", "left_loss": magnitude, "right_loss": 0.0},
            {"id": "c", "left_loss": 2 * magnitude, "right_loss": 0.0},
        ]
        # The represented differences are [3m, -m, -2m], whose observed
        # sum is zero. Both assignments yielding zero are ties in both tails.
        for alternative in ("less", "greater"):
            result = analyze({"pairs": pairs, "alternative": alternative})["summary"]
            assert result[f"exact_{alternative}_tail_count"] == 5


def test_default_contract_and_directional_swap_symmetry():
    payload = {"pairs": [
        {"id": "a", "left_loss": 0, "right_loss": 3},
        {"id": "b", "left_loss": 4, "right_loss": 1},
    ]}
    omitted = analyze(payload)
    explicit_two_sided = analyze({"pairs": payload["pairs"], "alternative": "two-sided"})
    assert "alternative" not in omitted["summary"]
    assert "exact_two_sided_p_value" in omitted["summary"]
    assert explicit_two_sided["summary"]["exact_two_sided_tail_count"] == omitted["summary"]["exact_two_sided_tail_count"]
    assert explicit_two_sided["summary"]["alternative"] == "two-sided"
    swapped = {"pairs": [
        {"id": pair["id"], "left_loss": pair["right_loss"], "right_loss": pair["left_loss"]}
        for pair in payload["pairs"]
    ]}
    less = analyze({**payload, "alternative": "less"})["summary"]
    swap_greater = analyze({**swapped, "alternative": "greater"})["summary"]
    assert less["exact_less_tail_count"] == swap_greater["exact_greater_tail_count"]


def test_alternative_validation_and_explicit_empty_input():
    with pytest.raises(ValueError, match="alternative must be one of"):
        analyze({"pairs": [], "alternative": "two_sided"})
    with pytest.raises(ValueError, match="alternative must be one of"):
        analyze({"pairs": [], "alternative": None})
    summary = analyze({"pairs": [], "alternative": "less"})["summary"]
    assert summary["observed_mean_signed_difference"] is None
    assert summary["exact_less_tail_count"] == 0
    assert summary["exact_less_p_value"] is None


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

    directed = subprocess.run(
        [sys.executable, "-m", "memecoin_runner.paired_loss_signflip"],
        input=json.dumps({"pairs": [{"id": "x", "left_loss": 2, "right_loss": 0}], "alternative": "less"}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert directed.returncode == 0
    # The two assignments yield -2 and +2; only -2 is <= the observed -2.
    assert json.loads(directed.stdout)["summary"]["exact_less_tail_count"] == 1

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
