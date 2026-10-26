from __future__ import annotations

from copy import deepcopy

import pytest

from memecoin_runner.scoring import score


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
