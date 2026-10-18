"""Frozen planning and readiness diagnostics; never trains or releases a holdout."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from memecoin_ml.data import load_dataset, read, sha, write
from memecoin_registry.contracts import digest, seal, verify
from memecoin_registry.qualification import support
from memecoin_registry.registry import verify_registry

Row = dict[str, Any]
DAY = 86400 * 10**9
FROZEN_SPEC = "8e3213272ac164603965d5de832d2625a01740ffad3dba268b7dd171f0766aa7"
PHASES = ("fold0", "fold1", "fold2", "final_selection")


def sources() -> Row:
    return {p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))}


def validate_spec(spec: Row) -> None:
    if spec.get("schema") != 1 or spec.get("id") != "F01-v1":
        raise ValueError("unsupported_research_specification")
    if (
        spec.get("research_launch_authorized") is not False
        or spec.get("holdout_release_authorized") is not False
    ):
        raise ValueError("research_and_release_gates_closed")
    if digest(spec) != FROZEN_SPEC:
        raise ValueError("frozen_spec_changed_new_version_required")
    if spec["seeds"] != [7, 19, 41] or len(spec["candidates"]) != 9:
        raise ValueError("invalid_trial_grid")
    candidates = spec["candidates"]
    if len({c["id"] for c in candidates}) != len(candidates) or [
        c["priority"] for c in candidates
    ] != list(range(9)):
        raise ValueError("duplicate_or_unordered_candidates")
    if spec["training"]["max_trainable_fits"] != len(PHASES) * 9 * 3 + 6:
        raise ValueError("trial_budget_mismatch")
    if spec["calendar"]["fold_days"] != [[0, 28, 35, 42], [0, 35, 42, 49], [0, 42, 49, 56]]:
        raise ValueError("unsupported_calendar_design")
    if spec["calendar"]["holdout_days"] != [56, 84] or spec["calendar"]["final_selection_days"] != [
        0,
        49,
        56,
    ]:
        raise ValueError("unsupported_final_split")
    if any(
        type(v) not in (int, float, bool) or not math.isfinite(v) or v <= 0
        for v in spec["readiness"].values()
    ):
        raise ValueError("invalid_readiness_thresholds")
    if spec["calendar"]["automatic_collection"] is not False:
        raise ValueError("collection_not_authorized_by_protocol")


def freeze(spec_path: Path, output: Path) -> Row:
    spec: Row = read(spec_path)
    validate_spec(spec)
    output.mkdir(parents=True, exist_ok=False)
    write(output / "spec.json", spec)
    meta = dict(
        created_ns=time.time_ns(),
        source=sources(),
        spec_sha256=sha(output / "spec.json"),
        research_launch_authorized=False,
        holdout_release_authorized=False,
    )
    write(output / "binding.json", meta)
    seal(output)
    return meta


def load_protocol(root: Path) -> Row:
    verify(root)
    binding = read(root / "binding.json")
    if binding["source"] != sources() or binding["spec_sha256"] != sha(root / "spec.json"):
        raise ValueError("protocol_source_or_spec_changed")
    spec: Row = read(root / "spec.json")
    validate_spec(spec)
    return spec


def materialize(spec: Row, *, anchor_ns: int, created_ns: int) -> Row:
    validate_spec(spec)
    if (
        type(anchor_ns) is not int
        or type(created_ns) is not int
        or created_ns < 0
        or anchor_ns % DAY
    ):
        raise ValueError("future_UTC_midnight_required")
    if anchor_ns <= max(created_ns, spec["inherited_holdout"]["end_ns"]):
        raise ValueError("anchor_overlaps_past_or_reserved_holdout")
    folds = []
    for i, (a, b, c, d) in enumerate(spec["calendar"]["fold_days"]):
        folds.append(
            dict(
                id=f"fold{i}",
                train_start_ns=anchor_ns + a * DAY,
                train_end_ns=anchor_ns + b * DAY,
                validation_end_ns=anchor_ns + c * DAY,
                test_end_ns=anchor_ns + d * DAY,
                embargo_ns=spec["calendar"]["embargo_ns"],
            )
        )
    return dict(
        spec_sha256=digest(spec),
        created_ns=created_ns,
        anchor_ns=anchor_ns,
        folds=folds,
        final_selection=dict(
            train_start_ns=anchor_ns,
            train_end_ns=anchor_ns + 49 * DAY,
            validation_end_ns=anchor_ns + 56 * DAY,
        ),
        holdout=dict(start_ns=anchor_ns + 56 * DAY, end_ns=anchor_ns + 84 * DAY),
        binding_status="design_only_pending_sampling_plan_and_admission",
        research_launch_authorized=False,
        automatic_collection=False,
    )


def trial_plan(spec: Row) -> Row:
    validate_spec(spec)
    trials = [
        dict(
            id=f"{phase}:{c['id']}:{seed}",
            phase=phase,
            candidate=c,
            seed=seed,
            role="validation_selection",
            max_seconds=spec["training"]["max_fit_seconds"],
        )
        for phase in PHASES
        for c in spec["candidates"]
        for seed in spec["seeds"]
    ]
    refits = [
        dict(id=f"final_refit:{model}:{seed}", candidate_from=model, seed=seed)
        for model in ("selected_validation_policy", "linear16")
        for seed in spec["seeds"]
    ]
    return dict(
        spec_sha256=digest(spec),
        trials=trials,
        final_refit_slots=refits,
        trainable_fit_budget=len(trials) + len(refits),
        deterministic_empirical_fits=4,
        training_started=False,
        requirements_before_reservation=[
            "immutable data manifest",
            "code/device binding",
            "readiness admission",
            "append-only trial/exposure ledger",
            "sampling binding",
        ],
    )


def select_validation(spec: Row, phase: str, results: list[Row]) -> Row:
    """Strict validation-only selector; no failed/missing seeds or unseen test fields."""
    validate_spec(spec)
    if phase not in PHASES:
        raise ValueError("unknown_selection_phase")
    expected = {t["id"] for t in trial_plan(spec)["trials"] if t["phase"] == phase}
    seen = set()
    scores: dict[str, list[float]] = {}
    epochs: dict[str, list[int]] = {}
    for row in results:
        if (
            set(row) != {"trial_id", "partition", "status", "score", "best_epoch"}
            or row["partition"] != "validation"
        ):
            raise ValueError("validation_only_evidence_required")
        key = row["trial_id"]
        if key not in expected or key in seen:
            raise ValueError("unknown_or_duplicate_trial")
        seen.add(key)
        if row["status"] != "completed":
            raise ValueError("incomplete_selection_no_replacement_trials")
        score, epoch = row["score"], row["best_epoch"]
        if type(score) not in (float, int) or not math.isfinite(score) or score < 0:
            raise ValueError("invalid_validation_score")
        if type(epoch) is not int or not 1 <= epoch <= spec["training"]["max_epochs"]:
            raise ValueError("invalid_best_epoch")
        candidate = key.split(":")[1]
        scores.setdefault(candidate, []).append(float(score))
        epochs.setdefault(candidate, []).append(epoch)
    if seen != expected:
        raise ValueError("incomplete_selection_no_replacement_trials")
    means = {key: sum(values) / len(values) for key, values in scores.items()}
    limit = min(means.values()) * (1 + spec["selection"]["tie_relative"])
    chosen = next(c for c in spec["candidates"] if means[c["id"]] <= limit)
    return dict(
        spec_sha256=digest(spec),
        phase=phase,
        selected=chosen,
        mean_validation_scores=means,
        ensemble_seeds=spec["seeds"],
        final_refit_epochs={key: sorted(epochs[key])[1] for key in (chosen["id"], "linear16")},
        evidence_sha256=digest(results),
        research_launch_authorized=False,
    )


def role_statistics(samples: list[Row], actions: list[list[float]], pools: list[str]) -> Row:
    base = support(samples, actions)
    days = {s["decision_ns"] // DAY for s in samples}
    strata = {
        i: {s["decision_ns"] // DAY for s in samples if (s["decision_ns"] % DAY) // (DAY // 4) == i}
        for i in range(4)
    }
    base.update(
        utc_days=len(days),
        day_fraction_each_stratum=[len(strata[i]) / len(days) if days else 0.0 for i in range(4)],
        min_pool_fraction=min(
            (base["decisions_per_pool"].get(p, 0) / len(samples) if samples else 0.0) for p in pools
        ),
    )
    return base


def role_checks(stats: Row, role: str, spec: Row) -> dict[str, bool]:
    floors = spec["readiness"]
    return dict(
        days=stats["utc_days"] >= floors[f"min_{role}_days"],
        decisions=stats["decisions"] >= floors[f"min_{role}_decisions"],
        nonoverlap=stats["greedy_nonoverlap_decisions"] >= floors[f"min_{role}_nonoverlap"],
        cohort=stats["min_pool_fraction"] >= floors["min_pool_decisions_fraction"],
        strata=all(
            x >= floors["min_day_fraction_each_stratum"] for x in stats["day_fraction_each_stratum"]
        ),
        per_action_classes=bool(stats["per_action"])
        and all(
            a["statuses"][c] >= floors[f"min_{role}_labels_per_action_class"]
            for a in stats["per_action"]
            for c in ("conditional_roundtrip", "entry_unfilled", "exit_unfilled")
        ),
        censoring=bool(stats["per_action"])
        and all(
            sum(a["statuses"].values()) > 0
            and a["statuses"]["censored"] / sum(a["statuses"].values())
            <= floors["max_censored_fraction_per_action"]
            for a in stats["per_action"]
        ),
    )


def readiness_verdict(checks: dict[str, bool], reviews: dict[str, str], spec: Row) -> Row:
    """Assessment is never an executable permission token, even for positive fixtures."""
    validate_spec(spec)
    if not checks or any(type(value) is not bool for value in checks.values()):
        raise ValueError("explicit_boolean_checks_required")
    if set(reviews) != set(spec["required_reviews"]) or any(
        v not in ("missing", "reviewed") for v in reviews.values()
    ):
        raise ValueError("invalid_review_inventory")
    blockers = [k for k, v in checks.items() if not v]
    blockers += [k for k, v in reviews.items() if v != "reviewed"]
    return dict(
        design_checks_passed=all(checks.values()),
        forecast_review_candidate=not blockers,
        blockers=blockers,
        research_launch_authorized=False,
        holdout_release_authorized=False,
        economic_ready=False,
        economic_blockers=spec["economic_blockers"],
        notice="review artifacts required; this function cannot authorize execution",
    )


def assess_registry(protocol_root: Path, registry: Path, revision: int, output: Path) -> Row:
    spec = load_protocol(protocol_root)
    if output.resolve().is_relative_to(registry.resolve()) or output.resolve().is_relative_to(
        protocol_root.resolve()
    ):
        raise ValueError("assessment_output_must_be_separate")
    latest = verify_registry(registry)  # Offline only: never reads D43 raw or reserved labels.
    if latest is None:
        raise ValueError("empty_registry")
    rev = registry / "revisions" / f"{revision:06d}"
    bundle = load_dataset(rev / "development")
    folds = read(rev / "folds.json")
    quality = read(rev / "qualification.json")
    binding = read(registry / "contract/binding.json")
    checks = dict(
        integrity=True,
        source_semantics=binding["semantics"] == spec["source_semantics"],
        # D45 finite six-window protocol is deliberately not a F01 collection binding.
        f01_prospective_sampling_binding=False,
        usable_fraction=quality["feature_starts"] > 0
        and (quality["feature_starts"] - quality["unusable_starts"]) / quality["feature_starts"]
        >= spec["readiness"]["min_usable_fraction"],
    )
    by_id = {s["id"]: s for s in bundle["samples"]}
    statistics = {}
    for name in PHASES:
        for role in (
            ("train", "validation")
            if name == "final_selection"
            else ("train", "validation", "test")
        ):
            ids = folds.get(name, {}).get("partitions", {}).get(role, [])
            stats = role_statistics(
                [by_id[key] for key in ids], bundle["source"]["scenario_grid"], spec["cohort"]
            )
            statistics[f"{name}.{role}"] = stats
            checks.update(
                {f"{name}.{role}.{k}": v for k, v in role_checks(stats, role, spec).items()}
            )
    # Future F01 completed/planned denominator is absent, never inferred from observed successes.
    checks["completed_fraction_against_frozen_F01_schedule"] = False
    reviews = dict.fromkeys(spec["required_reviews"], "missing")
    result = dict(
        spec_sha256=sha(protocol_root / "spec.json"),
        source=sources(),
        registry_revision=revision,
        registry_manifest_sha256=sha(rev / "manifest.json"),
        qualification="readiness_assessment_not_training",
        seed=None,
        registry_as_of_ns=quality["as_of_ns"],
        checks=checks,
        role_statistics=statistics,
        reviews=reviews,
        verdict=readiness_verdict(checks, reviews, spec),
        holdout_accessed=False,
        D43_accessed=False,
        current_calendar_can_satisfy_F01=False,
        missing_implementation=[
            "F01 sampling/registry adapter",
            "fold-aware admitted research runner",
            "paired block inference and exposure/trial ledger",
        ],
    )
    output.mkdir(parents=True, exist_ok=False)
    write(output / "report.json", result)
    seal(output)
    return result


def result_verdict(spec: Row, evidence: Row) -> str:
    """Future result contract on already verified summary; never a market-edge verdict."""
    validate_spec(spec)
    required = {
        "observed_days",
        "comparisons",
        "max_coverage_error",
        "brier_increase",
        "log_loss_increase",
    }
    if set(evidence) != required:
        raise ValueError("invalid_result_summary")
    rule = spec["evaluation"]
    if type(evidence["observed_days"]) is not int or evidence["observed_days"] < 0:
        raise ValueError("invalid_observed_days")
    passed = evidence["observed_days"] >= rule["min_daily_blocks"]
    comparisons = evidence["comparisons"]
    if set(comparisons) != {"empirical", "linear"}:
        raise ValueError("both_preregistered_comparators_required")
    for summary in comparisons.values():
        if set(summary) != {"block2", "block4"}:
            raise ValueError("both_dependence_scales_required")
        for lower, upper in summary.values():
            if (
                any(type(v) not in (float, int) or not math.isfinite(v) for v in (lower, upper))
                or lower > upper
                or upper > 1
            ):
                raise ValueError("invalid_confidence_interval")
            if (
                lower <= rule["practical_relative_improvement"]
                or (upper - lower) / 2 > rule["max_relative_ci_half_width"]
            ):
                passed = False
    for key, maximum in (
        ("max_coverage_error", rule["quantile_coverage_max_abs_error"]),
        ("brier_increase", rule["class_brier_noninferiority"]),
        ("log_loss_increase", rule["class_log_loss_noninferiority"]),
    ):
        value = evidence[key]
        if type(value) not in (float, int) or not math.isfinite(value):
            raise ValueError("invalid_calibration_summary")
        if key == "max_coverage_error" and not 0 <= value <= 1:
            raise ValueError("invalid_coverage_error")
        if value > maximum:
            passed = False
    return (
        "conditional_forecast_improvement_only_not_edge"
        if passed
        else "inconclusive_or_no_improvement"
    )
