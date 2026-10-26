from __future__ import annotations

import math
from copy import deepcopy

import pytest

from memecoin_runner.scoring import score
from memecoin_research.framework import DAY


def paired_fixture() -> tuple[list[dict], dict[str, list[dict]]]:
    samples = []
    predictions: list[dict] = []
    for decision, returned in (("first", 1.0), ("second", 3.0)):
        samples.append(
            dict(
                id=decision,
                decision_ns=0,
                pool_id="pool",
                targets=[
                    dict(action=[0, 0.1], status="conditional_roundtrip", **{"return": returned}),
                    dict(action=[1, 0.1], status="censored", **{"return": None}),
                ],
            )
        )
        for action in (0, 1):
            predictions.append(
                dict(id=decision, action=[action, 0.1], q=[0.0, 0.0, 0.0], p=[0.0, 0.0, 1.0])
            )
    return samples, {"selected": predictions, "other": deepcopy(predictions)}


def test_valid_distinct_action_support_keeps_paired_metrics() -> None:
    samples, predictions = paired_fixture()
    report = score(samples, predictions, pools=["pool"], scale=2.0, fold="f")

    assert report["aggregate"]["selected"]["pinball"] == 1.0
    assert report["aggregate"]["selected"]["pinball"] == report["aggregate"]["other"]["pinball"]
    assert report["support"] == [
        dict(day=0, pool="pool", decisions=2, returns=2, classes=2, censored=2)
    ]


@pytest.mark.parametrize(
    ("target_index", "change"),
    [
        (0, "exact"),
        (0, "return"),
        (0, "status"),
        (1, "exact"),
        (1, "status"),
    ],
)
def test_duplicate_target_action_is_rejected_before_paired_aggregation(
    target_index: int, change: str
) -> None:
    samples, predictions = paired_fixture()
    duplicate = deepcopy(samples[0]["targets"][target_index])
    if change == "return":
        duplicate["return"] = 99.0
    elif change == "status":
        if duplicate["status"] == "censored":
            duplicate.update(status="conditional_roundtrip", **{"return": 99.0})
        else:
            duplicate.update(status="censored", **{"return": None})
    samples[0]["targets"].append(duplicate)

    # Both models retain the original, unchanged prediction support.
    with pytest.raises(ValueError, match="duplicate_target_support"):
        score(samples, predictions, pools=["pool"], scale=2.0, fold="f")


def hierarchical_fixture() -> tuple[list[dict], dict[str, list[dict]]]:
    """Uneven support makes scenario/decision/pool/day weighting observable."""
    # Each row is (day, pool, decision id, returns, extra class-only label).
    # The None return in each decision is a censored scenario; the optional
    # class-only label has a class target but no return target.
    layout = [
        (0, "a", "a0", [2.0], True),
        (0, "a", "a1", [-4.0], False),
        (0, "b", "b0", [8.0], True),
        (0, "b", "b1", [], False),
        (1, "a", "a2", [0.0, 10.0], False),
        (1, "b", "b2", [6.0], False),
        (1, "b", "b3", [2.0], True),
        (1, "b", "b4", [], False),
    ]
    samples, predictions = [], []
    for day, pool, decision, returns, extra_class in layout:
        targets = [
            dict(
                action=[i, 0.1],
                status="conditional_roundtrip",
                **{"return": value},
            )
            for i, value in enumerate(returns)
        ]
        targets.append(
            dict(action=[10, 0.1], status="censored", **{"return": None})
        )
        if extra_class:
            targets.append(
                dict(action=[11, 0.1], status="entry_unfilled", **{"return": None})
            )
        samples.append(
            dict(
                id=decision,
                decision_ns=day * DAY,
                pool_id=pool,
                targets=targets,
            )
        )
        predictions.extend(
            dict(
                id=decision,
                action=target["action"],
                q=[0.0, 0.0, 0.0],
                p=[0.8, 0.1, 0.1],
            )
            for target in targets
        )
    return samples, {"selected": predictions, "other": deepcopy(predictions)}


def test_nonuniform_hierarchical_metrics_and_independent_head_masks() -> None:
    samples, predictions = hierarchical_fixture()
    report = score(samples, predictions, pools=["a", "b"], scale=2.0, fold="f")

    # Pinball is abs(return)/2 for q=[0,0,0]. First average scenarios
    # within each decision, then decisions within pools, pools within days,
    # and finally days: day 0=(1.5+4)/2=2.75, day 1=(2.5+2)/2=2.25.
    assert [row["models"]["selected"]["pinball"] for row in report["daily"]] == [
        2.75,
        2.25,
    ]
    assert report["aggregate"]["selected"]["pinball"] == 2.5
    expected_class_loss = (3 * math.log(10) + math.log(1.25)) / 4
    assert report["aggregate"]["selected"]["log_loss"] == pytest.approx(expected_class_loss)
    assert report["aggregate"]["selected"]["brier"] == pytest.approx(1.11)
    for metric in ("coverage0", "coverage1", "coverage2"):
        assert report["aggregate"]["selected"][metric] == pytest.approx(0.25)
    assert report["aggregate"]["other"] == report["aggregate"]["selected"]

    # Return and class masks differ: censored scenarios have neither head,
    # while entry_unfilled scenarios contribute a class but no return.
    by_cell = {(row["day"], row["pool"]): row for row in report["support"]}
    assert by_cell[0, "a"] == dict(day=0, pool="a", decisions=2, returns=2, classes=3, censored=2)
    assert by_cell[0, "b"] == dict(day=0, pool="b", decisions=2, returns=1, classes=2, censored=2)
    assert by_cell[1, "a"] == dict(day=1, pool="a", decisions=1, returns=2, classes=2, censored=1)
    assert by_cell[1, "b"] == dict(day=1, pool="b", decisions=3, returns=2, classes=3, censored=3)


def test_missing_pool_keeps_null_metrics_and_explicit_missing_rows() -> None:
    samples, predictions = paired_fixture()
    report = score(samples, predictions, pools=["pool", "absent"], scale=2.0, fold="f")

    assert report["aggregate"]["selected"]["pinball"] is None
    assert report["aggregate"]["selected"]["joint"] is None
    assert report["aggregate"]["selected"]["log_loss"] is None
    assert report["missing"] == [
        dict(day=0, pool="absent", metric=metric)
        for metric in (
            "pinball",
            "log_loss",
            "brier",
            "coverage0",
            "coverage1",
            "coverage2",
        )
    ]


def test_one_models_prediction_removal_is_paired_support_mismatch() -> None:
    samples, predictions = hierarchical_fixture()
    predictions["other"].pop()

    with pytest.raises(ValueError, match="paired_support_mismatch"):
        score(samples, predictions, pools=["a", "b"], scale=2.0, fold="f")


def test_duplicate_decision_still_rejects() -> None:
    samples, predictions = paired_fixture()
    samples.append(deepcopy(samples[0]))

    with pytest.raises(ValueError, match="duplicate_decision"):
        score(samples, predictions, pools=["pool"], scale=2.0, fold="f")
