"""Frozen phase orchestration. Research and holdout need separately pinned receipts."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, inventory
from memecoin_research.framework import DAY, role_checks, role_statistics, select_validation
from memecoin_runner.contracts import partition, receipt, validate_plan
from memecoin_runner.ledger import Ledger, publish
from memecoin_runner.scoring import (
    bootstrap,
    empirical_fit,
    empirical_predict,
    ensemble,
    return_scale,
    score,
)

Row = dict[str, Any]


def _completed(ledger: Ledger, trial: str, directory: Path) -> Row | None:
    completed = [e for e in ledger.trial_events(trial) if e["kind"] == "completed"]
    if not completed:
        return None
    if len(completed) != 1 or completed[0]["payload"]["artifacts"] != inventory(directory):
        raise ValueError("completed_trial_artifacts_changed")
    result: Row = read(directory / "result.json")
    return result


def _fit(
    ledger: Ledger,
    spec: Row,
    binding: Row,
    dataset: Path,
    output: Path,
    trial: str,
    config: Row,
    train: list[Row],
    validation: list[Row],
    pools: list[str],
    *,
    epochs: int,
    fixed: bool = False,
) -> Row:
    directory = output / "trials" / trial
    result = _completed(ledger, trial, directory)
    if result is not None:
        return result
    identity = dict(
        binding_sha256=digest(binding),
        trial=trial,
        train=digest(train),
        validation=digest(validation),
        config=config,
        fixed=fixed,
        epochs=epochs,
    )
    start = ledger.start(
        trial,
        identity,
        fit_limit=spec["training"]["max_fit_seconds"],
        total_limit=spec["training"]["max_total_seconds"],
    )
    job = dict(
        dataset=str(dataset.resolve()),
        dataset_sha256=sha(dataset),
        output=str(directory.resolve()),
        train=[s["id"] for s in train],
        validation=[s["id"] for s in validation],
        identity=identity,
        config=config,
        pools=pools,
        epochs=epochs,
        fixed_epochs=fixed,
    )
    job_path = output / f"job-{start['sequence']:06d}.json"
    publish(job_path, job)
    log = output / f"worker-{start['sequence']:06d}.log"
    before = time.monotonic()
    limit = start["payload"]["reserved_seconds"]
    status = "failed"
    try:
        with log.open("xb") as stream:
            process = subprocess.run(
                [sys.executable, "-m", "memecoin_runner.worker", str(job_path)],
                stdout=stream,
                stderr=stream,
                timeout=limit,
                check=False,
            )
        status = "completed" if process.returncode == 0 else "failed"
    except subprocess.TimeoutExpired:
        status = "budget_exhausted"
    finally:
        elapsed = min(limit, time.monotonic() - before)
        ledger.end(
            start,
            elapsed,
            status=status,
            artifacts=inventory(directory) if directory.exists() else {},
        )
    if status != "completed":
        raise ValueError(f"fit_{status}:safe_log={log}")
    result = _completed(ledger, trial, directory)
    assert result is not None
    return result


def _record(ledger: Ledger, path: Path, value: Row) -> None:
    if path.exists():
        events = [
            e
            for e in ledger.events
            if e["kind"] == "artifact" and e["payload"]["name"] == path.name
        ]
        if len(events) != 1 or events[0]["payload"]["sha256"] != sha(path) or read(path) != value:
            raise ValueError("published_phase_artifact_changed")
        return
    publish(path, value)
    ledger.append("artifact", name=path.name, sha256=sha(path))


def run(
    spec: Row,
    binding: Row,
    dataset: Path,
    output: Path,
    *,
    admission: Path | None = None,
    admission_sha256: str | None = None,
) -> Row:
    # Admission checked before parsing research development labels.
    decision = (
        receipt(admission, admission_sha256, kind="forecast_admission", binding=binding, spec=spec)
        if binding["mode"] == "research"
        else None
    )
    bundle = validate_plan(spec, binding, dataset)
    import torch

    from memecoin_runner.trainer import load_predict

    runtime = dict(
        binding=binding,
        torch=str(torch.__version__),
        trial_ids=binding["trial_ids"],
        admission_sha256=admission_sha256,
    )
    pools = (
        spec["cohort"]
        if binding["mode"] == "research"
        else sorted({s["pool_id"] for s in bundle["samples"]})
    )
    phases = {name: partition(bundle, bounds) for name, bounds in binding["phases"].items()}
    by_id = {s["id"]: s for s in bundle["samples"]}
    if decision:
        for phase_name, parts in phases.items():
            for role in ("train", "validation", "test"):
                if role == "test" and phase_name == "final_selection":
                    continue  # final_selection has no test partition.
                rows = [by_id[k] for k in parts[role]]
                actions = [
                    list(a)
                    for a in sorted({tuple(t["action"]) for s in rows for t in s["targets"]})
                ]
                if not all(role_checks(role_statistics(rows, actions, pools), role, spec).values()):
                    raise ValueError("numeric_readiness_closed")
    output.mkdir(parents=True, exist_ok=True)
    with Ledger(output / "ledger", runtime) as ledger:
        if any(e["kind"] == "exposure" for e in ledger.events):
            raise ValueError("post_exposure_training_forbidden")
        if any(e["kind"] == "incident" for e in ledger.events):
            raise ValueError("experiment_stopped_after_incident")
        with ledger.session(spec["training"]["max_total_seconds"]):
            summaries = {}
            for phase, parts in phases.items():
                train, validation, test = (
                    [by_id[k] for k in parts[r]] for r in ("train", "validation", "test")
                )
                results = []
                for candidate in spec["candidates"]:
                    for seed in spec["seeds"]:
                        trial = f"{phase}:{candidate['id']}:{seed}"
                        config = dict(kind=candidate["kind"], width=candidate["width"], seed=seed)
                        result = _fit(
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
                            epochs=binding["max_epochs"],
                        )
                        verified = score(
                            validation,
                            {"candidate": result["validation_predictions"]},
                            pools=pools,
                            scale=return_scale(train),
                            fold=phase,
                        )
                        value = verified["aggregate"]["candidate"]["joint"]
                        if value != result["score"]:
                            raise ValueError("validation_score_provenance_mismatch")
                        results.append(
                            dict(
                                trial_id=trial,
                                partition="validation",
                                status="completed",
                                score=value,
                                best_epoch=result["best_epoch"],
                            )
                        )
                selection = select_validation(spec, phase, results)
                selection["completed_trials_sha256"] = digest(
                    {r["trial_id"]: inventory(output / "trials" / r["trial_id"]) for r in results}
                )
                _record(ledger, output / f"{phase}-selection.json", selection)
                chosen = selection["selected"]["id"]
                if phase == "final_selection":
                    # Refit all development samples with strict nominal maturity purge.
                    cutoff = binding["phases"][phase]["validation_end"]
                    from memecoin_registry.qualification import interval_end

                    refit = [
                        s
                        for s in bundle["samples"]
                        if s["decision_ns"] >= binding["phases"][phase]["start"]
                        and interval_end(s) < cutoff
                    ]
                    paths: Row = {}
                    for name in dict.fromkeys((chosen, "linear16")):
                        c = next(c for c in spec["candidates"] if c["id"] == name)
                        slot = "linear16" if name == "linear16" else "selected_validation_policy"
                        paths[name] = []
                        for seed in spec["seeds"]:
                            trial = f"final_refit:{slot}:{seed}"
                            _fit(
                                ledger,
                                spec,
                                binding,
                                dataset,
                                output,
                                trial,
                                dict(kind=c["kind"], width=c["width"], seed=seed),
                                refit,
                                [],
                                pools,
                                epochs=selection["final_refit_epochs"][name],
                                fixed=True,
                            )
                            paths[name].append(trial)
                    procedure = dict(
                        selected=chosen,
                        models=paths,
                        empirical=empirical_fit(refit),
                        scale=return_scale(refit),
                        binding_sha256=digest(binding),
                        selection_sha256=sha(output / f"{phase}-selection.json"),
                        artifacts={
                            t: inventory(output / "trials" / t) for ts in paths.values() for t in ts
                        },
                    )
                    _record(ledger, output / "procedure.json", procedure)
                elif test:
                    predictions = {
                        name: ensemble(
                            [
                                load_predict(
                                    output / "trials" / f"{phase}:{c}:{seed}", bundle, test
                                )
                                for seed in spec["seeds"]
                            ]
                        )
                        for name, c in (("selected", chosen), ("linear", "linear16"))
                    }
                    baseline = empirical_fit(train)
                    predictions["empirical"] = empirical_predict(baseline, test)
                    report = score(
                        test, predictions, pools=pools, scale=return_scale(train), fold=phase
                    )
                    _record(
                        ledger,
                        output / f"{phase}-outer.json",
                        dict(
                            predictions=predictions,
                            empirical=baseline,
                            scores=report,
                            uncertainty=bootstrap(spec, report),
                            claim="engineering_only"
                            if binding["mode"] == "engineering"
                            else "descriptive_outer_fold",
                        ),
                    )
                summaries[phase] = dict(selected=chosen, trials=len(results))
            result = dict(
                mode=binding["mode"],
                phases=summaries,
                research_run=binding["mode"] == "research",
                holdout_opened=False,
                charged_seconds=ledger.charged(),
                ledger_head=ledger.head,
            )
            # Stable completion artifact; subsequent invocation audits instead of rewriting timing.
            if not (output / "completion.json").exists():
                publish(output / "completion.json", result)
            return result


def evaluate_holdout(
    spec: Row,
    binding: Row,
    output: Path,
    holdout: Path,
    *,
    expected_holdout_sha256: str,
    release: Path,
    release_sha256: str,
    admission_sha256: str,
) -> Row:
    if binding["mode"] != "research":
        raise ValueError("engineering_cannot_release_holdout")
    import torch

    from memecoin_ml.data import _validate
    from memecoin_registry.qualification import interval_end
    from memecoin_runner.contracts import sources
    from memecoin_runner.trainer import load_predict

    if sources() != binding["sources"]:
        raise ValueError("release_sources_changed")
    procedure = read(output / "procedure.json")
    procedure_sha = digest(dict(procedure=procedure, holdout_sha256=expected_holdout_sha256))
    decision = receipt(
        release,
        release_sha256,
        kind="holdout_release",
        binding=binding,
        spec=spec,
        procedure_sha256=procedure_sha,
    )
    runtime = dict(
        binding=binding,
        torch=str(torch.__version__),
        trial_ids=binding["trial_ids"],
        admission_sha256=admission_sha256,
    )
    with Ledger(output / "ledger", runtime) as ledger:
        if any(e["kind"] == "incident" for e in ledger.events):
            raise ValueError("incident_requires_review")
        artifact = [
            e
            for e in ledger.events
            if e["kind"] == "artifact" and e["payload"]["name"] == "procedure.json"
        ]
        if len(artifact) != 1 or artifact[0]["payload"]["sha256"] != sha(output / "procedure.json"):
            raise ValueError("unregistered_procedure")
        for trial, expected in procedure["artifacts"].items():
            if (
                inventory(output / "trials" / trial) != expected
                or _completed(ledger, trial, output / "trials" / trial) is None
            ):
                raise ValueError("released_model_changed")
        ledger.expose(decision, procedure_sha)  # Must precede even hashing protected bytes.
        if sha(holdout) != expected_holdout_sha256:
            raise ValueError("consumed_holdout_hash_mismatch")
        bundle = read(holdout)
        _validate(bundle)
        start = binding["phases"]["final_selection"]["validation_end"]
        embargo = spec["calendar"]["embargo_ns"]
        samples = bundle["samples"]
        if any(
            not start + embargo <= s["decision_ns"] or interval_end(s) >= start + 28 * DAY
            for s in samples
        ):
            raise ValueError("holdout_membership_invalid")
        predictions = {
            name: ensemble(
                [
                    load_predict(output / "trials" / t, bundle, samples)
                    for t in procedure["models"][c]
                ]
            )
            for name, c in (("selected", procedure["selected"]), ("linear", "linear16"))
        }
        predictions["empirical"] = empirical_predict(procedure["empirical"], samples)
        report = score(
            samples,
            predictions,
            pools=spec["cohort"],
            scale=procedure["scale"],
            fold="final_holdout",
        )
        actions = [
            list(a) for a in sorted({tuple(t["action"]) for s in samples for t in s["targets"]})
        ]
        stats = role_statistics(samples, actions, spec["cohort"])
        support = all(role_checks(stats, "test", spec).values()) and stats["utc_days"] >= 24
        result = dict(
            evaluation_samples=samples,
            scores=report,
            predictions=predictions,
            support=stats,
            support_passed=support,
            uncertainty=bootstrap(spec, report)
            if support
            else dict(status="inconclusive", reason="holdout_support"),
        )
        _record(ledger, output / "holdout-result.json", result)
        return result


def reproduce_released(spec: Row, output: Path, runtime_binding: Row, *, expected_head: str) -> Row:
    """Recompute consumed report from its pinned observations; never reopen raw holdout."""
    from memecoin_runner.contracts import sources

    if runtime_binding["binding"]["sources"] != sources():
        raise ValueError("reproduction_sources_changed")
    with Ledger(output / "ledger", runtime_binding) as ledger:
        ledger.audit(expected_head)
        path = output / "holdout-result.json"
        records = [
            e
            for e in ledger.events
            if e["kind"] == "artifact" and e["payload"]["name"] == path.name
        ]
        if len(records) != 1 or records[0]["payload"]["sha256"] != sha(path):
            raise ValueError("released_result_changed_or_missing")
        result = read(path)
        report = score(
            result["evaluation_samples"],
            result["predictions"],
            pools=spec["cohort"],
            scale=result["scores"]["scale"],
            fold="final_holdout",
        )
        uncertainty = (
            bootstrap(spec, report)
            if result["support_passed"]
            else dict(status="inconclusive", reason="holdout_support")
        )
        if report != result["scores"] or uncertainty != result["uncertainty"]:
            raise ValueError("released_result_reproduction_mismatch")
        return dict(exact=True, raw_holdout_reopened=False, result_sha256=sha(path))
