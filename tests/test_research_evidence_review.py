from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys

import pytest

from memecoin_runner import abstention_frontier, evidence_review, research_evidence_gate


def row(identity: str, **updates: object) -> dict:
    result = {
        "id": identity,
        "decision_ns": 100,
        "input_available_ns": 95,
        "source_status": "qualified",
        "forecast_status": "available",
        "uncertainty": 0.2,
        "loss": 1.0,
    }
    result.update(updates)
    return result


def payload(rows: list[dict] | None = None, *, max_uncertainty: float = 0.8) -> dict:
    return {
        "policy": {"max_input_age_ns": 5, "max_uncertainty": max_uncertainty},
        "thresholds": [0.5, 0, 0.2, 1],
        "rows": rows or [],
    }


def test_composes_real_analyzers_keeps_roster_denominators_and_original_hash() -> None:
    source = row("unsupported-source", source_status="unsupported", uncertainty=0.9)
    future = row("future", input_available_ns=101)
    stale = row("stale", input_available_ns=94)
    unknown_u = row("unknown-u", uncertainty=None)
    no_forecast = row("no-forecast", forecast_status="missing")
    multi = row("multi", source_status="unknown", forecast_status="missing",
                input_available_ns=94, uncertainty=0.9)
    known = row("known", uncertainty=0.2, loss=2)
    unknown_loss = row("unknown-loss", uncertainty=0.5, loss=None)
    original = payload([source, future, stale, unknown_u, no_forecast, multi, known, unknown_loss])
    snapshot = copy.deepcopy(original)

    result = evidence_review.analyze(original)

    expected_hash = hashlib.sha256(json.dumps(
        original, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()).hexdigest()
    assert result["input_sha256"] == expected_hash
    assert result["kind"] == "research_evidence_review"
    assert result["summary"] == {
        "row_count": 8, "eligible_count": 2, "ineligible_count": 6, "threshold_count": 4,
        "roster_known_loss_count": 7, "roster_missing_loss_count": 1,
        "eligible_known_loss_count": 1, "eligible_missing_loss_count": 1,
    }
    assert result["gate"] == research_evidence_gate.analyze({
        "policy": original["policy"],
        "rows": [{key: value for key, value in item.items() if key != "loss"}
                 for item in original["rows"]],
    })
    assert result["frontier"]["kind"] == "abstention_frontier"
    assert result["frontier"]["details"][0]["rejected"]["unsupported_count"] == 6
    assert [point["threshold"] for point in result["details"]] == [0, 0.2, 0.5, 1]
    assert [point["accepted_count"] for point in result["details"]] == [0, 1, 2, 2]
    assert [point["accepted_ids"] for point in result["details"]] == [
        [], ["known"], ["known", "unknown-loss"], ["known", "unknown-loss"]
    ]
    assert [point["coverage_of_eligible"] for point in result["details"]] == [0, 0.5, 1, 1]
    assert [point["mean_loss"] for point in result["details"]] == [None, 2, 2, 2]
    assert result["details"][-1]["coverage"] == 0.25
    assert result["gate"]["summary"]["abstain_count"] == 6
    assert original == snapshot
    assert [point["accepted_known_loss_count"] for point in result["details"]] == [0, 1, 1, 1]
    assert sum(len(item["reasons"]) for item in result["gate"]["details"]) > 6
    assert result["frontier"] == abstention_frontier.analyze({
        "thresholds": original["thresholds"],
        "rows": [{"id": item["id"], "uncertainty": item["uncertainty"],
                  "loss": item["loss"], "supported": item["id"] in {"known", "unknown-loss"}}
                 for item in original["rows"]],
    })
    for point, frontier_point in zip(result["details"], result["frontier"]["details"]):
        assert {key: point[key] for key in frontier_point} == frontier_point
    assert_all_entrypoints(original, result)


def assert_all_entrypoints(original: dict, expected: dict) -> None:
    for module in (research_evidence_gate, abstention_frontier):
        assert module.analyze_review(original) == expected
        process = subprocess.run(
            [sys.executable, "-m", module.__name__, "--review"],
            input=json.dumps(original), text=True, capture_output=True, check=False,
        )
        assert process.returncode == 0, process.stderr
        assert process.stderr == ""
        assert json.loads(process.stdout) == expected
        assert process.stdout == json.dumps(
            expected, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ) + "\n"


@pytest.mark.parametrize("rows", [
    [],
    [row("one", source_status="unsupported", uncertainty=0.1, loss=None)],
    [row("zero", uncertainty=0, loss=0)],
])
def test_empty_all_ineligible_and_known_zero_cases(rows: list[dict]) -> None:
    result = evidence_review.analyze(payload(rows))
    assert_all_entrypoints(payload(rows), result)
    assert result["summary"]["row_count"] == len(rows)
    if not rows:
        assert result["details"] and all(point["coverage"] is None for point in result["details"])
        assert all(point["coverage_of_eligible"] is None for point in result["details"])
    elif rows[0]["source_status"] == "unsupported":
        assert result["summary"]["eligible_count"] == 0
        assert all(point["accepted_count"] == 0 for point in result["details"])
        assert all(point["coverage_of_eligible"] is None for point in result["details"])
    else:
        assert result["details"][0]["accepted_count"] == 1
        assert result["details"][0]["mean_loss"] == 0


def test_threshold_above_frozen_gate_ceiling_does_not_restore_row() -> None:
    result = evidence_review.analyze(payload(
        [row("over-ceiling", uncertainty=0.9)], max_uncertainty=0.8
    ))
    assert result["summary"]["ineligible_count"] == 1
    assert result["details"][-1]["threshold"] == 1
    assert result["details"][-1]["accepted_count"] == 0
    assert result["frontier"]["details"][-1]["rejected"]["unsupported_count"] == 1
    assert_all_entrypoints(payload([row("over-ceiling", uncertainty=0.9)]), result)


def test_large_finite_loss_and_frontier_mean_are_preserved() -> None:
    result = evidence_review.analyze(payload([
        row("large-a", uncertainty=1, loss=1e308),
        row("large-b", uncertainty=1, loss=1e308),
    ], max_uncertainty=1))
    assert result["details"][-1]["mean_loss"] == 1e308
    assert result["details"][-1]["accepted_ids"] == ["large-a", "large-b"]
    assert result["frontier"] == abstention_frontier.analyze({
        "thresholds": [0.5, 0, 0.2, 1],
        "rows": [{"id": name, "supported": True, "uncertainty": 1, "loss": 1e308}
                 for name in ("large-a", "large-b")],
    })
    assert_all_entrypoints(payload([
        row("large-a", uncertainty=1, loss=1e308),
        row("large-b", uncertainty=1, loss=1e308),
    ], max_uncertainty=1), result)


def test_missing_loss_is_not_imputed_and_changes_original_hash() -> None:
    original = payload([row("unknown-loss", uncertainty=0, loss=None)])
    result = evidence_review.analyze(original)
    assert result["summary"]["eligible_missing_loss_count"] == 1
    assert all(point["accepted_known_loss_count"] == 0 for point in result["details"])
    assert all(point["mean_loss"] is None for point in result["details"])
    zero = payload([row("unknown-loss", uncertainty=0, loss=0)])
    assert evidence_review.analyze(zero)["input_sha256"] != result["input_sha256"]
    assert_all_entrypoints(original, result)


@pytest.mark.parametrize("field,value", [
    ("loss", 10**1000), ("loss", True), ("loss", float("inf")),
    ("uncertainty", 10**1000), ("decision_ns", True),
])
def test_invalid_values_rejected_even_on_ineligible_rows(field: str, value: object) -> None:
    original = payload([row("bad", source_status="unsupported", **{field: value})])
    for analyze in (evidence_review.analyze, research_evidence_gate.analyze_review,
                    abstention_frontier.analyze_review):
        with pytest.raises(ValueError):
            analyze(original)
    for module in (research_evidence_gate, abstention_frontier):
        process = subprocess.run(
            [sys.executable, "-m", module.__name__, "--review"],
            input=json.dumps(original), text=True, capture_output=True, check=False,
        )
        assert process.returncode == 2
        assert process.stdout == ""
        assert process.stderr.startswith("error: ")
        assert "Traceback" not in process.stderr
