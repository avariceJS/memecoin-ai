from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from memecoin_ml.data import read, synthetic
from memecoin_registry.contracts import digest
from memecoin_runner.contracts import partition, plan, receipt, validate_plan
from memecoin_runner.ledger import Ledger, publish
from memecoin_runner.scoring import bootstrap, empirical_fit, empirical_predict, ensemble, score

SPEC = Path("configs/research-f01-v1.json")


def test_ledger_crash_budget_binding_tamper(tmp_path: Path) -> None:
    binding = dict(trial_ids=["one", "two"])
    with Ledger(tmp_path, binding) as ledger:
        start = ledger.start("one", {"data": "x"}, fit_limit=600, total_limit=86400)
        assert ledger.charged() == 600
        ledger.end(start, 10, status="suspended", artifacts={})
        assert ledger.charged() == 10
        with pytest.raises(ValueError, match="already_closed"):
            ledger.end(start, 10, status="completed", artifacts={})
        ledger.start("one", {"data": "x"}, fit_limit=600, total_limit=86400)
        head = ledger.head
        assert ledger.charged() == 600
        with pytest.raises(BlockingIOError):
            Ledger(tmp_path, binding)
    with Ledger(tmp_path, binding) as ledger:
        assert ledger.audit(head)
        with pytest.raises(ValueError, match="budget"):
            ledger.start("one", {"data": "x"}, fit_limit=600, total_limit=86400)
        with pytest.raises(ValueError, match="identity"):
            ledger.start("one", {"data": "other"}, fit_limit=600, total_limit=86400)
        with pytest.raises(ValueError, match="unregistered"):
            ledger.start("extra", {}, fit_limit=600, total_limit=86400)
    path = tmp_path / "event-000000.json"
    row = json.loads(path.read_text())
    row["payload"]["identity"] = {}
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="chain"):
        Ledger(tmp_path, binding)


def test_ledger_completion_failure_exposure(tmp_path: Path) -> None:
    with Ledger(tmp_path, dict(trial_ids=["one", "two"])) as ledger:
        start = ledger.start("one", {}, fit_limit=600, total_limit=86400)
        ledger.end(start, 1, status="completed", artifacts={})
        with pytest.raises(ValueError, match="already_completed"):
            ledger.start("one", {}, fit_limit=600, total_limit=86400)
        start = ledger.start("two", {}, fit_limit=600, total_limit=86400)
        ledger.end(start, 2, status="failed", artifacts={})
        with pytest.raises(ValueError, match="stopped"):
            ledger.start("two", {}, fit_limit=600, total_limit=86400)
        ledger.expose({"fixture": True}, "pinned")
        with pytest.raises(ValueError, match="consumed"):
            ledger.expose({"fixture": True}, "pinned")
    with pytest.raises(FileExistsError):
        publish(tmp_path / "binding.json", {})


def test_plan_partition_and_closed_admission(tmp_path: Path) -> None:
    synthetic(tmp_path / "dataset")
    path = tmp_path / "dataset/dataset.json"
    bundle = read(path)
    bounds = dict(
        start=0,
        train_end=60 * 10**9,
        validation_end=90 * 10**9,
        test_end=121 * 10**9,
        embargo=2 * 10**9,
    )
    parts = partition(bundle, bounds)
    assert "57" in parts["purged"]  # decision58 + nominal2 == cutoff60
    assert "59" in parts["purged"]  # embargo
    assert "61" in parts["validation"]
    spec = read(SPEC)
    binding = plan(spec, path, mode="engineering", engineering_bounds=bounds)
    assert validate_plan(spec, binding, path) == bundle
    altered = deepcopy(binding)
    altered["max_epochs"] = 100
    with pytest.raises(ValueError):
        validate_plan(spec, altered, path)
    with pytest.raises(ValueError, match="sampling"):
        plan(spec, path, mode="research")
    with pytest.raises(ValueError, match="pinned"):
        receipt(None, None, kind="forecast_admission", binding=binding, spec=spec)
    fake = tmp_path / "fake.json"
    publish(fake, dict(reviewed=True))
    from memecoin_ml.data import sha

    with pytest.raises(ValueError, match="invalid"):
        receipt(fake, sha(fake), kind="forecast_admission", binding=binding, spec=spec)


def scoring_fixture(days: int = 8) -> tuple[list[dict], dict]:
    from memecoin_research.framework import DAY

    samples, predictions = [], []
    for day in range(days):
        for pool in ("a", "b"):
            for n in range(1 if pool == "a" else 3):
                key = f"{day}:{pool}:{n}"
                samples.append(
                    dict(
                        id=key,
                        decision_ns=day * DAY,
                        pool_id=pool,
                        targets=[
                            dict(
                                action=[0, 0.001, 0, 20],
                                status="conditional_roundtrip",
                                **{"return": 1.0 if pool == "a" else 3.0},
                            ),
                            dict(action=[1, 0.001, 0, 20], status="censored", **{"return": None}),
                        ],
                    )
                )
                for action in (0, 1):
                    predictions.append(
                        dict(
                            id=key,
                            action=[action, 0.001, 0, 20],
                            q=[0.0, 0.0, 0.0],
                            p=[0.0, 0.0, 1.0],
                        )
                    )
    return samples, dict(
        selected=predictions, empirical=deepcopy(predictions), linear=deepcopy(predictions)
    )


def test_hierarchical_masks_floor_and_support() -> None:
    samples, predictions = scoring_fixture()
    report = score(samples, predictions, pools=["a", "b"], scale=2.0, fold="f")
    assert report["aggregate"]["selected"]["pinball"] == 1.0  # (.5 + 1.5)/2 not scenario-weighted
    assert report["aggregate"]["selected"]["brier"] == 0
    assert report["aggregate"]["selected"]["log_loss"] > 0  # floor + renormalization
    assert sum(r["censored"] for r in report["support"]) == len(samples)
    absent = score(samples, predictions, pools=["a", "b", "c"], scale=2.0, fold="f")
    assert absent["aggregate"]["selected"]["joint"] is None
    assert bootstrap(read(SPEC), absent)["status"] == "inconclusive"
    predictions["linear"].pop()
    with pytest.raises(ValueError, match="paired_support"):
        score(samples, predictions, pools=["a", "b"], scale=2.0, fold="f")


def test_bootstrap_pairing_determinism_and_short_runs() -> None:
    samples, predictions = scoring_fixture()
    report = score(samples, predictions, pools=["a", "b"], scale=2.0, fold="f")
    result = bootstrap(read(SPEC), report)
    assert result == bootstrap(read(SPEC), report)
    assert result["evidence"]["comparisons"] == {
        m: {"block2": [0.0, 0.0], "block4": [0.0, 0.0]} for m in ("empirical", "linear")
    }
    assert result["verdict"] == "inconclusive_or_no_improvement"
    report["daily"][2]["day"] += 100  # breaks run, must not silently drop it
    assert bootstrap(read(SPEC), report)["status"] == "inconclusive"


def test_empirical_and_ensemble() -> None:
    samples, predictions = scoring_fixture(1)
    baseline = empirical_fit(samples)
    assert baseline["actions"][1]["fallback"] == {"returns": True, "classes": True}
    assert len(empirical_predict(baseline, samples)) == 8
    combined = ensemble([predictions["selected"], predictions["empirical"], predictions["linear"]])
    assert len(combined) == 8
    with pytest.raises(ValueError, match="three_seeds"):
        ensemble([combined])
    samples[0]["targets"][0]["return"] = None
    with pytest.raises(ValueError, match="target_mask"):
        score(samples, predictions, pools=["a", "b"], scale=2.0, fold="f")


def test_external_head_detects_suffix_removal(tmp_path: Path) -> None:
    with Ledger(tmp_path, dict(trial_ids=[])) as ledger:
        ledger.append("fixture")
        head = ledger.head
    (tmp_path / "event-000000.json").unlink()
    with Ledger(tmp_path, dict(trial_ids=[])) as ledger:
        with pytest.raises(ValueError, match="head_changed"):
            ledger.audit(head)
    assert digest({"x": 1}) != head


def test_total_budget_and_session_crash(tmp_path: Path) -> None:
    binding = dict(trial_ids=["one", "two"])
    with Ledger(tmp_path, binding) as ledger:
        start = ledger.start("one", {}, fit_limit=600, total_limit=10)
        assert start["payload"]["reserved_seconds"] == 10
        ledger.end(start, 10, status="completed", artifacts={})
        with pytest.raises(ValueError, match="budget"):
            ledger.start("two", {}, fit_limit=600, total_limit=10)
    root = tmp_path / "crash"
    with Ledger(root, binding) as ledger:
        ledger.append("session_start", reserved_seconds=86400)
    with Ledger(root, binding) as ledger:
        with pytest.raises(ValueError, match="session_budget"):
            with ledger.session(86400):
                pytest.fail("crashed session cannot reset budget")


def test_nan_unordered_quantiles_duplicate_predictions() -> None:
    for mutation in ("nan", "order", "duplicate"):
        samples, preds = scoring_fixture(1)
        if mutation == "nan":
            preds["selected"][0]["p"][0] = float("nan")
        elif mutation == "order":
            preds["selected"][0]["q"] = [1.0, 0.0, 2.0]
        else:
            preds["selected"].append(preds["selected"][0])
        with pytest.raises(ValueError, match="invalid_or_duplicate"):
            score(samples, preds, pools=["a", "b"], scale=1.0, fold="f")


def test_bootstrap_ratio_of_means_not_mean_of_ratios() -> None:
    samples, preds = scoring_fixture(8)
    report = score(samples, preds, pools=["a", "b"], scale=1.0, fold="f")
    for i, row in enumerate(report["daily"]):
        row["models"]["selected"]["joint"] = 1.0
        for name in ("empirical", "linear"):
            row["models"][name]["joint"] = float(i + 1)
    result = bootstrap(read(SPEC), report)
    assert result["relative_improvement"]["linear"] == pytest.approx(1 - 1 / 4.5)
    # A constant multiple stays exactly paired in every resample, not independent draws.
    for row in report["daily"]:
        row["models"]["selected"]["joint"] = row["models"]["linear"]["joint"] / 2
    result = bootstrap(read(SPEC), report)
    assert result["evidence"]["comparisons"]["linear"]["block4"] == [0.5, 0.5]


def test_receipt_link_integrity_and_wrong_scope(tmp_path: Path) -> None:
    from memecoin_ml.data import sha

    spec, binding = read(SPEC), {"fixture": True}
    evidence = tmp_path / "review.json"
    publish(evidence, dict(fixture_only=True, decision="fixture review, never actual admission"))
    r = dict(
        schema=1,
        kind="forecast_admission",
        decision="approved",
        scope="conditional_forecast_only",
        binding_sha256=digest(binding),
        spec_sha256=digest(spec),
        reviewer="fixture reviewer",
        decided_ns=1,
        evidence={
            k: dict(path=str(evidence), sha256=sha(evidence)) for k in spec["required_reviews"]
        },
        limitations=["synthetic unit test only"],
        procedure_sha256=None,
    )
    path = tmp_path / "receipt.json"
    publish(path, r)
    assert receipt(path, sha(path), kind="forecast_admission", binding=binding, spec=spec) == r
    with pytest.raises(ValueError, match="invalid_or_stale"):
        receipt(path, sha(path), kind="holdout_release", binding=binding, spec=spec)
    evidence.write_text("{}")
    with pytest.raises(ValueError, match="evidence_changed"):
        receipt(path, sha(path), kind="forecast_admission", binding=binding, spec=spec)
