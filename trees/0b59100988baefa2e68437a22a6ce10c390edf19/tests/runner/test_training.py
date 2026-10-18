from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from memecoin_ml.data import read, synthetic  # noqa: E402
from memecoin_runner.contracts import partition  # noqa: E402
from memecoin_runner.trainer import fit, load_predict  # noqa: E402


def test_exact_resume_and_validation_only(tmp_path: Path) -> None:
    synthetic(tmp_path / "data")
    bundle = read(tmp_path / "data/dataset.json")
    for s in bundle["samples"]:
        s["pool_id"] = "synthetic"
    parts = partition(
        bundle,
        dict(
            start=0,
            train_end=60 * 10**9,
            validation_end=90 * 10**9,
            test_end=121 * 10**9,
            embargo=2 * 10**9,
        ),
    )
    by_id = {s["id"]: s for s in bundle["samples"]}
    train, val, test = ([by_id[k] for k in parts[r]] for r in ("train", "validation", "test"))
    config = dict(kind="gru", width=16, seed=7)
    kwargs = dict(identity={"fixture": True}, pools=["synthetic"], max_epochs=3)
    continuous = fit(bundle, train, val, config, tmp_path / "continuous", **kwargs)
    first = fit(bundle, train, val, config, tmp_path / "resume", stop_after_epoch=1, **kwargs)
    assert first["status"] == "suspended"
    resumed = fit(bundle, train, val, config, tmp_path / "resume", **kwargs)
    assert resumed == continuous
    changed = deepcopy(bundle)
    for s in changed["samples"]:
        if s["id"] in parts["test"]:
            for t in s["targets"]:
                if t["return"] is not None:
                    t["return"] *= 1e6
    untouched = fit(changed, train, val, config, tmp_path / "test_changed", **kwargs)
    assert untouched == continuous
    assert load_predict(tmp_path / "continuous", bundle, test) == load_predict(
        tmp_path / "resume", bundle, test
    )
    with pytest.raises(ValueError, match="identity_changed"):
        fit(bundle, train, val, {**config, "seed": 19}, tmp_path / "resume", **kwargs)


def test_fully_censored_no_optimizer_step(tmp_path: Path) -> None:
    synthetic(tmp_path / "data")
    bundle = read(tmp_path / "data/dataset.json")
    for s in bundle["samples"]:
        s["pool_id"] = "synthetic"
    # Include one observed decision to define scaler/IQR, compare frozen initial optimizer
    # on a fully censored batch via trainer losses is covered by D44; here empty support stops.
    train = deepcopy(bundle["samples"][:20])
    for s in train:
        for t in s["targets"]:
            t["status"], t["return"] = "censored", None
    with pytest.raises(ValueError, match="empty_or_nonfinite_quantile"):
        fit(
            bundle,
            train,
            bundle["samples"][30:40],
            dict(kind="linear", width=16, seed=7),
            tmp_path / "fit",
            identity={},
            pools=["synthetic"],
            max_epochs=1,
        )


def test_parent_timeout_stops_experiment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from memecoin_runner.ledger import Ledger
    from memecoin_runner.runner import _fit

    spec = read(Path("configs/research-f01-v1.json"))
    dataset = tmp_path / "data.json"
    dataset.write_text("{}")
    from memecoin_ml.data import sha
    from memecoin_runner.contracts import sources

    binding = {"sources": sources(), "dataset_sha256": sha(dataset)}

    def timeout(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired("fixture", 0.01)

    monkeypatch.setattr(subprocess, "run", timeout)
    with Ledger(tmp_path / "ledger", dict(trial_ids=["fixture"])) as ledger:
        with pytest.raises(ValueError, match="budget_exhausted"):
            _fit(ledger, spec, binding, dataset, tmp_path, "fixture", {}, [], [], [], epochs=1)
        assert any(e["kind"] == "incident" for e in ledger.events)
        with pytest.raises(ValueError, match="stopped"):
            ledger.start("fixture", {}, fit_limit=600, total_limit=86400)


def test_holdout_exposure_precedes_hash_and_survives_failure(tmp_path: Path) -> None:
    from memecoin_ml.data import sha
    from memecoin_registry.contracts import digest
    from memecoin_runner.contracts import sources
    from memecoin_runner.ledger import Ledger, publish
    from memecoin_runner.runner import evaluate_holdout

    spec = read(Path("configs/research-f01-v1.json"))
    # Clearly synthetic decision receipts; no project holdout or actual admission.
    binding = dict(mode="research", sources=sources(), trial_ids=[])
    runtime = dict(
        binding=binding, torch=str(torch.__version__), trial_ids=[], admission_sha256="fixture"
    )
    procedure = dict(artifacts={})
    publish(tmp_path / "procedure.json", procedure)
    with Ledger(tmp_path / "ledger", runtime) as ledger:
        ledger.append("artifact", name="procedure.json", sha256=sha(tmp_path / "procedure.json"))
    protected = tmp_path / "fixture-holdout.json"
    protected.write_text('{"fixture_only":true}')
    review = tmp_path / "review.json"
    publish(review, {"fixture_only": True})
    release = tmp_path / "release.json"
    r = dict(
        schema=1,
        kind="holdout_release",
        decision="approved",
        scope="conditional_forecast_only",
        binding_sha256=digest(binding),
        spec_sha256=digest(spec),
        reviewer="fixture",
        decided_ns=1,
        limitations=["test only"],
        procedure_sha256=digest(dict(procedure=procedure, holdout_sha256="wrong_fixture_hash")),
        evidence={
            k: dict(path=str(review), sha256=sha(review))
            for k in (
                "selected_refit_artifacts",
                "loss_ci_sources",
                "exposure_review",
                "evidence_completeness",
            )
        },
    )
    publish(release, r)
    kwargs = dict(
        expected_holdout_sha256="wrong_fixture_hash",
        release=release,
        release_sha256=sha(release),
        admission_sha256="fixture",
    )
    import sys

    def audit(event: str, args: tuple) -> None:
        if event == "open" and args[0] == str(protected):
            events = [read(p) for p in sorted((tmp_path / "ledger").glob("event-*.json"))]
            assert events[-1]["kind"] == "exposure"

    sys.addaudithook(audit)
    with pytest.raises(ValueError, match="consumed_holdout_hash_mismatch"):
        evaluate_holdout(spec, binding, tmp_path, protected, **kwargs)
    with pytest.raises(ValueError, match="consumed_exact_reproduction"):
        evaluate_holdout(spec, binding, tmp_path, protected, **kwargs)


def test_fixed_refit_uses_last_epoch_and_no_validation(tmp_path: Path) -> None:
    synthetic(tmp_path / "data")
    bundle = read(tmp_path / "data/dataset.json")
    for s in bundle["samples"]:
        s["pool_id"] = "synthetic"
    result = fit(
        bundle,
        bundle["samples"][:60],
        [],
        dict(kind="linear", width=16, seed=19),
        tmp_path / "refit",
        identity={"fixture_refit": True},
        pools=["synthetic"],
        max_epochs=3,
        fixed_epochs=True,
    )
    assert result["best_epoch"] == 3
    assert result["validation_predictions"] == []


def test_all_phases_and_linear_deduplicated_refit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Orchestration fixture only: mock admission/data, never a research grant."""
    import memecoin_runner.runner as runner
    import memecoin_runner.trainer as trainer
    from memecoin_registry.contracts import inventory
    from memecoin_research.framework import trial_plan
    from memecoin_runner.ledger import publish
    from memecoin_runner.scoring import empirical_fit, empirical_predict, return_scale, score

    synthetic(tmp_path / "data")
    bundle = read(tmp_path / "data/dataset.json")
    spec = read(Path("configs/research-f01-v1.json"))
    samples = []
    for s in bundle["samples"]:
        for pool in spec["cohort"]:
            samples.append({**deepcopy(s), "id": s["id"] + pool, "pool_id": pool})
    bundle["samples"] = samples
    bounds = dict(
        start=0, train_end=60 * 10**9, validation_end=90 * 10**9, test_end=121 * 10**9, embargo=0
    )
    binding = dict(
        mode="research",
        phases={p: bounds for p in ("fold0", "fold1", "fold2", "final_selection")},
        max_epochs=2,
        trial_ids=[r["id"] for r in trial_plan(spec)["trials"]]
        + [r["id"] for r in trial_plan(spec)["final_refit_slots"]],
    )
    monkeypatch.setattr(runner, "receipt", lambda *a, **k: {})
    monkeypatch.setattr(runner, "validate_plan", lambda *a: bundle)
    fitted = []
    baselines = {}

    def fake_fit(
        ledger,
        spec,
        binding,
        dataset,
        output,
        trial,
        config,
        train,
        validation,
        pools,
        *,
        epochs,
        fixed=False,
    ):
        fitted.append(trial)
        directory = output / "trials" / trial
        directory.mkdir(parents=True)
        baseline = empirical_fit(train)
        baselines[str(directory)] = baseline
        predictions = empirical_predict(baseline, validation) if validation else []
        loss = (
            score(
                validation,
                {"candidate": predictions},
                pools=pools,
                scale=return_scale(train),
                fold="f",
            )["aggregate"]["candidate"]["joint"]
            if validation
            else 0.0
        )
        result = dict(validation_predictions=predictions, score=loss, best_epoch=2)
        publish(directory / "result.json", result)
        start = ledger.start(trial, {"fixture": True}, fit_limit=600, total_limit=86400)
        ledger.end(start, 0.001, status="completed", artifacts=inventory(directory))
        return result

    monkeypatch.setattr(runner, "_fit", fake_fit)
    monkeypatch.setattr(
        trainer,
        "load_predict",
        lambda path, bundle, samples: empirical_predict(baselines[str(path)], samples),
    )
    result = runner.run(spec, binding, tmp_path / "data/dataset.json", tmp_path / "run")
    assert len(fitted) == 111  # 108 selection + 3 refits; no redundant linear duplicate.
    assert all(p["selected"] == "linear16" for p in result["phases"].values())
    procedure = read(tmp_path / "run/procedure.json")
    assert set(procedure["models"]) == {"linear16"}
    assert result["holdout_opened"] is False


def test_worker_boundary_rejects_dataset_mutation(tmp_path: Path) -> None:
    from memecoin_ml.data import sha
    from memecoin_runner.contracts import sources
    from memecoin_runner.ledger import Ledger
    from memecoin_runner.runner import _fit

    path = tmp_path / "data.json"
    path.write_text("{}")
    binding = dict(sources=sources(), dataset_sha256=sha(path))
    path.write_text('{"changed": true}')
    with Ledger(tmp_path / "ledger", dict(trial_ids=["fixture"])) as ledger:
        with pytest.raises(ValueError, match="boundary_source_or_data_changed"):
            _fit(
                ledger,
                read(Path("configs/research-f01-v1.json")),
                binding,
                path,
                tmp_path,
                "fixture",
                {},
                [],
                [],
                [],
                epochs=1,
            )
        assert ledger.events == []
