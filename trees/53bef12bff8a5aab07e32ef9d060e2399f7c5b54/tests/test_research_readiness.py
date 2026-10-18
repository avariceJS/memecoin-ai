from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest

from memecoin_ml.data import read
from memecoin_research.framework import (
    DAY,
    PHASES,
    freeze,
    load_protocol,
    materialize,
    readiness_verdict,
    result_verdict,
    role_checks,
    role_statistics,
    select_validation,
    trial_plan,
    validate_spec,
)

SPEC = Path(__file__).parents[1] / "configs/research-f01-v1.json"


def validation_rows(spec, phase="fold0"):
    return [
        dict(trial_id=t["id"], partition="validation", status="completed", score=1.0, best_epoch=5)
        for t in trial_plan(spec)["trials"]
        if t["phase"] == phase
    ]


def test_frozen_protocol_and_no_rule_changes(tmp_path: Path) -> None:
    freeze(SPEC, tmp_path / "protocol")
    spec = load_protocol(tmp_path / "protocol")
    changed = copy.deepcopy(spec)
    changed["readiness"]["min_train_days"] = 1
    with pytest.raises(ValueError, match="new_version_required"):
        validate_spec(changed)
    with pytest.raises(FileExistsError):
        freeze(SPEC, tmp_path / "protocol")
    with (tmp_path / "protocol/spec.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="inventory_mismatch"):
        load_protocol(tmp_path / "protocol")


def test_future_calendar_never_reuses_reserved_holdout() -> None:
    spec = read(SPEC)
    anchor = (spec["inherited_holdout"]["end_ns"] // DAY + 2) * DAY
    binding = materialize(spec, anchor_ns=anchor, created_ns=anchor - DAY)
    assert binding["folds"][0]["train_end_ns"] == anchor + 28 * DAY
    assert binding["folds"][2]["test_end_ns"] == binding["holdout"]["start_ns"]
    assert binding["holdout"]["end_ns"] == anchor + 84 * DAY
    assert binding["automatic_collection"] is False
    for bad, now in (
        (anchor, anchor),
        (anchor - 3 * DAY, anchor - 4 * DAY),
        (anchor + 1, anchor - DAY),
    ):
        with pytest.raises(ValueError):
            materialize(spec, anchor_ns=bad, created_ns=now)


def test_budget_and_all_seeds_are_reserved_in_advance() -> None:
    spec = read(SPEC)
    plan = trial_plan(spec)
    assert len(plan["trials"]) == 108 and len(plan["final_refit_slots"]) == 6
    assert plan["trainable_fit_budget"] == 114
    assert len({t["id"] for t in plan["trials"]}) == 108
    assert all(sum(t["phase"] == phase for t in plan["trials"]) == 27 for phase in PHASES)
    assert plan["training_started"] is False


def test_selector_uses_mean_all_seeds_and_prefers_simple_within_tolerance() -> None:
    spec = read(SPEC)
    rows = validation_rows(spec)
    for row in rows:
        if ":gru16:" in row["trial_id"]:
            row["score"] = 0.995  # Less than 1 percent advantage: use simpler linear.
    assert select_validation(spec, "fold0", rows)["selected"]["id"] == "linear16"
    for row in rows:
        if ":gru16:" in row["trial_id"]:
            row["score"] = 0.95
    selected = select_validation(spec, "fold0", rows)
    assert selected["selected"]["id"] == "gru16"
    assert selected["ensemble_seeds"] == [7, 19, 41]
    assert selected["research_launch_authorized"] is False
    # One lucky seed cannot beat a consistently better model via seed cherry picking.
    for row in rows:
        if ":mlp16:" in row["trial_id"]:
            row["score"] = 0.1 if row["trial_id"].endswith(":7") else 1.9
    assert select_validation(spec, "fold0", rows)["selected"]["id"] == "gru16"


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "test_field", "test_role", "failed", "nan", "seed"]
)
def test_invalid_selection_evidence_is_rejected(fault: str) -> None:
    spec = read(SPEC)
    rows = validation_rows(spec)
    if fault == "missing":
        rows.pop()
    elif fault == "duplicate":
        rows.append(rows[0])
    elif fault == "test_field":
        rows[0]["test_score"] = 0.0
    elif fault == "test_role":
        rows[0]["partition"] = "test"
    elif fault == "failed":
        rows[0]["status"] = "failed"
    elif fault == "nan":
        rows[0]["score"] = math.nan
    else:
        rows[0]["trial_id"] = "fold0:linear16:99"
    with pytest.raises(ValueError):
        select_validation(spec, "fold0", rows)


def test_even_positive_design_and_reviews_do_not_authorize_training() -> None:
    spec = read(SPEC)
    verdict = readiness_verdict(
        {"synthetic_all_checks": True}, dict.fromkeys(spec["required_reviews"], "reviewed"), spec
    )
    assert verdict["forecast_review_candidate"] is True
    assert verdict["research_launch_authorized"] is False
    assert verdict["holdout_release_authorized"] is False
    assert verdict["economic_ready"] is False
    with pytest.raises(ValueError):
        readiness_verdict(
            {"truthy_is_not_evidence": 1}, dict.fromkeys(spec["required_reviews"], "missing"), spec
        )


def test_scenarios_do_not_inflate_decision_or_day_support() -> None:
    spec = read(SPEC)
    actions = [[0, 0.001, 0, 20], [1, 0.001, 0, 20]]
    sample = dict(
        id="synthetic",
        pool_id=spec["cohort"][0],
        decision_ns=DAY - 1,
        maturity_ns=DAY + 20 * 10**9,
        targets=[dict(action=a, status="conditional_roundtrip") for a in actions],
    )
    stats = role_statistics([sample], actions, spec["cohort"])
    assert stats["decisions"] == 1 and stats["variants"] == 2
    assert stats["utc_days"] == 1
    assert stats["day_fraction_each_stratum"] == [0.0, 0.0, 0.0, 1.0]
    assert not any(
        role_checks(stats, "train", spec)[k]
        for k in ("days", "decisions", "nonoverlap", "cohort", "strata", "per_action_classes")
    )


def test_confirmatory_verdict_requires_both_comparators_precision_and_calibration() -> None:
    spec = read(SPEC)
    evidence = dict(
        observed_days=28,
        comparisons={
            k: dict(block2=[0.03, 0.05], block4=[0.025, 0.055]) for k in ("empirical", "linear")
        },
        max_coverage_error=0.02,
        brier_increase=0.001,
        log_loss_increase=0.001,
    )
    assert result_verdict(spec, evidence) == "conditional_forecast_improvement_only_not_edge"
    for alteration in ("wide", "linear_fails", "calibration", "few_days"):
        e = copy.deepcopy(evidence)
        if alteration == "wide":
            e["comparisons"]["empirical"]["block2"] = [0.03, 0.2]
        elif alteration == "linear_fails":
            e["comparisons"]["linear"]["block4"] = [-0.01, 0.03]
        elif alteration == "calibration":
            e["max_coverage_error"] = 0.2
        else:
            e["observed_days"] = 5
        assert "improvement_only" not in result_verdict(spec, e)


def test_result_guard_rejects_nan_even_if_another_condition_already_failed() -> None:
    spec = read(SPEC)
    evidence = dict(
        observed_days=1,
        comparisons={
            "empirical": dict(block2=[-0.1, 0.1], block4=[-0.1, 0.1]),
            "linear": dict(block2=[math.nan, 0.1], block4=[-0.1, 0.1]),
        },
        max_coverage_error=0.1,
        brier_increase=0.0,
        log_loss_increase=0.0,
    )
    with pytest.raises(ValueError, match="confidence_interval"):
        result_verdict(spec, evidence)
