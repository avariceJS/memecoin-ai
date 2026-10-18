"""Support diagnostics and global membership. No return metrics or automatic promotion."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from memecoin_ml.data import CLASSES
from memecoin_registry.contracts import Row, digest, validate_protocol


def interval_end(sample: Row) -> int:
    nominal = max(int((t["action"][2] + t["action"][3]) * 1e9) for t in sample["targets"])
    return max(int(sample["maturity_ns"]), int(sample["decision_ns"]) + nominal)


def memberships(bundle: Row, protocol: Row) -> Row:
    validate_protocol(protocol)
    folds = {}
    for fold in protocol["folds"]:
        assigned: dict[str, list[str]] = {k: [] for k in ("train", "validation", "test")}
        purged = {}
        for sample in bundle["samples"]:
            time, end = sample["decision_ns"], interval_end(sample)
            reason = "outside_fold"
            for label, start, cutoff in (
                ("train", fold["train_start_ns"], fold["train_end_ns"]),
                (
                    "validation",
                    fold["train_end_ns"] + fold["embargo_ns"],
                    fold["validation_end_ns"],
                ),
                ("test", fold["validation_end_ns"] + fold["embargo_ns"], fold["test_end_ns"]),
            ):
                if start <= time < cutoff:
                    if end < cutoff:
                        assigned[label].append(sample["id"])
                        reason = "assigned"
                    else:
                        reason = "group_maturity_or_horizon_crosses_cutoff"
                    break
            if reason == "outside_fold" and any(
                cutoff <= time < cutoff + fold["embargo_ns"]
                for cutoff in (fold["train_end_ns"], fold["validation_end_ns"])
            ):
                reason = "embargo"
            if reason != "assigned":
                purged[sample["id"]] = reason
        folds[fold["id"]] = dict(partitions=assigned, purged=purged, specification=fold)
    return folds


def support(samples: list[Row], actions: list[list[float]]) -> Row:
    statuses = Counter(t["status"] for s in samples for t in s["targets"])
    complete = [s for s in samples if all(t["status"] != "censored" for t in s["targets"])]
    # One timeline across pools/scenarios, conservatively disjoint outcome intervals.
    nonoverlap = []
    previous_end = -1
    for sample in sorted(complete, key=lambda s: (s["decision_ns"], s["id"])):
        if sample["decision_ns"] > previous_end:
            nonoverlap.append(sample["id"])
            previous_end = interval_end(sample)
    by_action = []
    for action in actions:
        counts = Counter(
            t["status"] for s in samples for t in s["targets"] if t["action"] == action
        )
        by_action.append(
            dict(action=action, statuses={c: counts[c] for c in (*CLASSES, "censored")})
        )
    return dict(
        decisions=len(samples),
        variants=sum(statuses.values()),
        statuses=dict(statuses),
        all_scenarios_observed_decisions=len(complete),
        greedy_nonoverlap_decisions=len(nonoverlap),
        nonoverlap_ids=nonoverlap,
        nonoverlap_is_not_independence=True,
        per_action=by_action,
        unique_pools=len({s["pool_id"] for s in samples}),
        decisions_per_pool=dict(Counter(s["pool_id"] for s in samples)),
    )


def report(bundle: Row, protocol: Row, records: list[Row], as_of_ns: int) -> tuple[Row, Row]:
    folds = memberships(bundle, protocol)
    samples = {s["id"]: s for s in bundle["samples"]}
    actions = bundle["source"]["scenario_grid"]
    floors = protocol["engineering_floors"]
    checks: dict[str, bool] = {}
    fold_support = {}
    for name, fold in folds.items():
        partitions = {}
        for part, ids in fold["partitions"].items():
            stats = support([samples[key] for key in ids], actions)
            stats["support_floor_passed"] = (
                stats["decisions"] >= floors["partition_decisions"]
                and stats["greedy_nonoverlap_decisions"] >= floors["partition_nonoverlap"]
                and bool(actions)
                and all(
                    a["statuses"][c] >= floors["labels_per_action_class"]
                    for a in stats["per_action"]
                    for c in CLASSES
                )
            )
            checks[f"{name}.{part}.support"] = stats["support_floor_passed"]
            partitions[part] = stats
        fold_support[name] = dict(partitions=partitions, purged=len(fold["purged"]))
    times = sorted(s["decision_ns"] for s in samples.values())
    dates = [datetime.fromtimestamp(t // 10**9, tz=UTC).isoformat() for t in times]
    days, hours = sorted({t[:10] for t in dates}), sorted({t[:13] for t in dates})
    dev = [r for r in records if r["admission"] == "development"]
    checks.update(
        windows=len(dev) >= floors["windows"],
        utc_days=len(days) >= floors["utc_days"],
        utc_hours=len(hours) >= floors["utc_hours"],
    )
    # Real observed within-group spans, never the elapsed days between windows.
    spans: dict[str, list[int]] = {}
    for sample in samples.values():
        spans.setdefault(sample["group"], []).append(sample["decision_ns"])
    union: list[list[int]] = []
    for start, end in sorted((min(t), max(t)) for t in spans.values()):
        if union and start <= union[-1][1]:
            union[-1][1] = max(end, union[-1][1])
        else:
            union.append([start, end])
    result: dict[str, Any] = dict(
        schema=1,
        qualification="engineering_only",
        as_of_ns=as_of_ns,
        integrity_verified=True,
        development_windows=len(dev),
        inventory_statuses=dict(Counter(r["admission"] for r in records)),
        observed_utc_days=days,
        observed_utc_hours=hours,
        observed_development_span_union_ns=sum(b - a for a, b in union),
        elapsed_calendar_ns=times[-1] - times[0] if times else 0,
        history_groups=len(spans),
        source_segments=len(
            {(s["window_index"], s["group"].split(":")[1]) for s in samples.values()}
        ),
        feature_starts=sum(
            p["feature_quality"]["feature_starts"] for p in bundle["source"]["parents"]
        ),
        unusable_starts=sum(p["feature_quality"]["unusable"] for p in bundle["source"]["parents"]),
        per_window_feature_quality=[
            dict(index=p["index"], **p["feature_quality"]) for p in bundle["source"]["parents"]
        ],
        development=support(list(samples.values()), actions),
        folds=fold_support,
        engineering_checks=checks,
        numeric_floors_passed=all(checks.values()),
        research_training_ready=False,
        execution_qualified=False,
        blockers=[k for k, passed in checks.items() if not passed] + protocol["unresolved"],
        holdout=dict(
            start_ns=protocol["holdout"]["start_ns"],
            end_ns=protocol["holdout"]["end_ns"],
            quarantined_windows=sum(r["admission"] == "holdout_quarantine" for r in records),
            labels_released=False,
            semantic_verification="deferred_until_formal_release",
            os_access_control=False,
        ),
        membership_sha256=digest(folds),
        seed=None,
    )
    return result, folds
