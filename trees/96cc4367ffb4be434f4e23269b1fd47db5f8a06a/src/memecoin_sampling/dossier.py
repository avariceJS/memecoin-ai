"""Exact frozen folds plus coverage denominators. Produces assessment, never admission."""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any

from memecoin_ml.data import load_dataset, read, save_dataset, sha, write
from memecoin_registry.contracts import digest, seal, verify
from memecoin_research.framework import role_checks, role_statistics
from memecoin_runner.contracts import partition, plan, validate_plan
from memecoin_sampling.contract import paths_disjoint
from memecoin_sampling.registry import combine, verify_registry

Row = dict[str, Any]


def qualify(
    binding: Row, spec: Row, records: list[Row], bundle: Row, phases: Row, *, as_of_ns: int
) -> tuple[Row, Row]:
    by_id = {s["id"]: s for s in bundle["samples"]}
    parents = {r["index"]: r for r in bundle["source"]["parents"]}
    checks: dict[str, bool] = {}
    roles: Row = {}
    folds: Row = {}
    for phase, bounds in sorted(phases.items()):
        folds[phase] = partition(bundle, bounds)
        roles[phase] = {}
        for role, start, end in (
            ("train", bounds["start"], bounds["train_end"]),
            ("validation", bounds["train_end"] + bounds["embargo"], bounds["validation_end"]),
            ("test", bounds["validation_end"] + bounds["embargo"], bounds["test_end"]),
        ):
            if phase == "final_selection" and role == "test":
                continue
            samples = [by_id[k] for k in folds[phase][role]]
            stats = role_statistics(samples, bundle["source"]["scenario_grid"], spec["cohort"])
            indices = [s["index"] for s in binding["windows"][:224] if start <= s["due_ns"] < end]
            states = Counter(records[i]["status"] for i in indices)
            starts = sum(
                parents[i]["feature_quality"]["feature_starts"] for i in indices if i in parents
            )
            usable = sum(parents[i]["feature_quality"]["usable"] for i in indices if i in parents)
            # Failed/incomplete sources have unknown actual starts; use their full ceiling.
            # Missed declaration alone is not a proof of zero attempts, so include it too.
            unknown_start_ceiling = 180 * (len(indices) - states["completed"])
            lower = (
                usable / (starts + unknown_start_ceiling) if starts + unknown_start_ceiling else 0.0
            )
            coverage = dict(
                planned_windows=len(indices),
                states=dict(states),
                completed_fraction=states["completed"] / len(indices) if indices else 0.0,
                completed_source_starts=starts,
                completed_source_usable=usable,
                other_source_start_upper_bound=unknown_start_ceiling,
                actual_usable_fraction=None
                if unknown_start_ceiling
                else (usable / starts if starts else None),
                usable_fraction_lower_bound=lower,
                interpretation="conservative sufficient test, not estimated actual usability",
            )
            prefix = f"{phase}.{role}"
            checks.update({f"{prefix}.{k}": v for k, v in role_checks(stats, role, spec).items()})
            checks[f"{prefix}.calendar_closed"] = as_of_ns >= end
            checks[f"{prefix}.scheduled_coverage"] = (
                coverage["completed_fraction"]
                >= spec["readiness"]["min_completed_planned_windows_fraction"]
            )
            checks[f"{prefix}.usable_lower_bound"] = (
                lower >= spec["readiness"]["min_usable_fraction"]
            )
            roles[phase][role] = dict(support=stats, coverage=coverage)
    completed = [r for r in records if r["status"] == "completed"]
    checks["prospective_only"] = bool(completed) and all(
        r["data_kind"] == "prospective" for r in completed
    )
    failed = [r["index"] for r in records if r["status"] == "failed"]
    stop = sorted(failed)[7] if len(failed) >= 8 else None
    checks["no_collection_after_failure_stop"] = stop is None or not any(
        r["status"] == "completed" and r["index"] > stop for r in records
    )
    checks["failure_ceiling"] = len(failed) <= 8
    report = dict(
        schema=1,
        numeric_checks=checks,
        numeric_design_passed=all(checks.values()),
        blockers=[k for k, v in checks.items() if not v],
        roles=roles,
        missing_reviews=list(spec["required_reviews"]),
        collection_activation="not_issued_by_D48",
        research_launch_authorized=False,
        holdout_release_authorized=False,
        economic_ready=False,
        economic_blockers=spec["economic_blockers"],
        as_of_ns=as_of_ns,
        sampled_scope="fixed cohort and planned UTC windows only; not continuous market coverage",
        holdout=dict(planned_windows=112, source_opened=False, labels_assessed=False),
        failure_denominator_policy="missing/failed/pending never removed from planned denominator",
    )
    return report, folds


def export_dossier(root: Path, output: Path, *, now_ns: int | None = None) -> Row:
    paths_disjoint(root, output)
    current = verify_registry(root)
    now = time.time_ns() if now_ns is None else now_ns
    if now < current["as_of_ns"]:
        raise ValueError("dossier_before_registry_availability")
    binding, spec = current["binding"], current["spec"]
    bundle = combine(root, binding, current["records"])
    output.mkdir(parents=True, exist_ok=False)
    save_dataset(output / "development", bundle)
    runtime_plan = plan(
        spec,
        output / "development/dataset.json",
        mode="research",
        anchor_ns=binding["anchor_ns"],
        sampling=binding,
    )
    validate_plan(spec, runtime_plan, output / "development/dataset.json")
    report, folds = qualify(
        binding, spec, current["records"], bundle, runtime_plan["phases"], as_of_ns=now
    )
    write(output / "runner-plan.json", runtime_plan)
    write(output / "folds.json", folds)
    write(output / "report.json", report)
    write(
        output / "provenance.json",
        dict(
            registry_revision=current["sequence"],
            registry_head=current["head"],
            sampling_sha256=digest(binding),
            dataset_sha256=sha(output / "development/dataset.json"),
            as_of_ns=now,
            records=current["records"],
            actual_research_admission=False,
        ),
    )
    seal(output)
    return report


def reproduce_dossier(root: Path, output: Path) -> Row:
    verify(output)
    current = verify_registry(root)
    provenance = read(output / "provenance.json")
    revision = provenance["registry_revision"]
    if revision == -1:
        from memecoin_sampling.registry import _records

        records = _records(current["binding"])
        head = None
    else:
        path = root / "revisions" / f"{revision:06d}"
        records = read(path / "revision.json")["records"]
        head = sha(path / "manifest.json")
    if records != provenance["records"] or head != provenance["registry_head"]:
        raise ValueError("dossier_registry_provenance_changed")
    binding, spec = current["binding"], current["spec"]
    if provenance["sampling_sha256"] != digest(binding):
        raise ValueError("dossier_sampling_changed")
    bundle = combine(root, binding, records)
    if (
        bundle != load_dataset(output / "development")
        or sha(output / "development/dataset.json") != provenance["dataset_sha256"]
    ):
        raise ValueError("dossier_dataset_changed")
    runtime_plan = read(output / "runner-plan.json")
    validate_plan(spec, runtime_plan, output / "development/dataset.json")
    report, folds = qualify(
        binding, spec, records, bundle, runtime_plan["phases"], as_of_ns=provenance["as_of_ns"]
    )
    if report != read(output / "report.json") or folds != read(output / "folds.json"):
        raise ValueError("dossier_reproduction_changed")
    return dict(
        exact=True,
        research_launch_authorized=False,
        registry_revision=revision,
        dataset_sha256=provenance["dataset_sha256"],
        dossier_sha256=sha(output / "manifest.json"),
    )
