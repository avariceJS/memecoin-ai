"""Paired conditional scoring and calendar-block inference, without torch."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import mean
from typing import Any

from memecoin_ml.data import CLASSES
from memecoin_research.framework import DAY, result_verdict, validate_spec

Row = dict[str, Any]
LEVELS = (0.1, 0.5, 0.9)


def quantile(values: list[float], level: float) -> float:
    if not values or any(not math.isfinite(v) for v in values):
        raise ValueError("empty_or_nonfinite_quantile")
    ordered = sorted(values)
    p = (len(ordered) - 1) * level
    lo, hi = math.floor(p), math.ceil(p)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (p - lo)


def return_scale(samples: list[Row]) -> float:
    values = [t["return"] for s in samples for t in s["targets"] if t["return"] is not None]
    return max(1e-4, quantile(values, 0.75) - quantile(values, 0.25))


def empirical_fit(samples: list[Row]) -> Row:
    targets = [t for s in samples for t in s["targets"]]
    actions = sorted({tuple(t["action"]) for t in targets})
    fitted = []
    for action in actions:
        matching = [t for t in targets if tuple(t["action"]) == action]
        observed = [t["return"] for t in matching if t["return"] is not None]
        labeled = [t["status"] for t in matching if t["status"] in CLASSES]
        fallback = dict(returns=not observed, classes=not labeled)
        if not observed:
            observed = [t["return"] for t in targets if t["return"] is not None]
        if not labeled:
            labeled = [t["status"] for t in targets if t["status"] in CLASSES]
        if not labeled:
            raise ValueError("empirical_no_observed_classes")
        fitted.append(
            dict(
                action=list(action),
                q=[quantile(observed, q) for q in LEVELS],
                p=[(labeled.count(c) + 1) / (len(labeled) + 3) for c in CLASSES],
                fallback=fallback,
                returns=len(observed),
                classes=len(labeled),
            )
        )
    return dict(actions=fitted)


def empirical_predict(fit: Row, samples: list[Row]) -> list[Row]:
    lookup = {tuple(r["action"]): r for r in fit["actions"]}
    return [
        dict(
            id=s["id"],
            action=t["action"],
            q=lookup[tuple(t["action"])]["q"],
            p=lookup[tuple(t["action"])]["p"],
        )
        for s in samples
        for t in s["targets"]
    ]


def _index(predictions: list[Row]) -> dict[tuple[str, tuple[float, ...]], Row]:
    result = {}
    for r in predictions:
        key = (r["id"], tuple(r["action"]))
        q, p = r["q"], r["p"]
        if (
            key in result
            or len(q) != 3
            or len(p) != 3
            or any(not math.isfinite(v) for v in (*q, *p))
            or q != sorted(q)
            or any(v < 0 or v > 1 for v in p)
            or abs(sum(p) - 1) > 1e-5
        ):
            raise ValueError("invalid_or_duplicate_prediction")
        result[key] = r
    return result


def ensemble(predictions: list[list[Row]]) -> list[Row]:
    if len(predictions) != 3:
        raise ValueError("all_three_seeds_required")
    indices = [_index(p) for p in predictions]
    if any(set(i) != set(indices[0]) for i in indices):
        raise ValueError("ensemble_support_mismatch")
    return [
        dict(
            id=k[0],
            action=list(k[1]),
            q=[mean(i[k]["q"][j] for i in indices) for j in range(3)],
            p=[mean(i[k]["p"][j] for i in indices) for j in range(3)],
        )
        for k in sorted(indices[0])
    ]


def score(
    samples: list[Row],
    predictions: dict[str, list[Row]],
    *,
    pools: list[str],
    scale: float,
    fold: str,
) -> Row:
    """Each head averages independently: scenarios, decisions, pools, days."""
    if (
        not samples
        or not pools
        or len(set(pools)) != len(pools)
        or not math.isfinite(scale)
        or scale < 1e-4
    ):
        raise ValueError("invalid_score_support_or_scale")
    if len({s["id"] for s in samples}) != len(samples):
        raise ValueError("duplicate_decision")
    expected = {(s["id"], tuple(t["action"])) for s in samples for t in s["targets"]}
    indices = {name: _index(p) for name, p in predictions.items()}
    if not indices or any(set(i) != expected for i in indices.values()):
        raise ValueError("paired_support_mismatch")
    metrics = ("pinball", "log_loss", "brier", "coverage0", "coverage1", "coverage2")
    # Values are already decision means before pooling.
    cells: Any = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    support: Any = defaultdict(lambda: dict(decisions=0, returns=0, classes=0, censored=0))
    for sample in sorted(samples, key=lambda s: (s["decision_ns"], s["id"])):
        pool, day = sample["pool_id"], sample["decision_ns"] // DAY
        if pool not in pools:
            raise ValueError("out_of_cohort")
        cell = (day, pool)
        support[cell]["decisions"] += 1
        for name, index in indices.items():
            per: Any = defaultdict(list)
            for t in sample["targets"]:
                pred = index[sample["id"], tuple(t["action"])]
                status, y = t["status"], t["return"]
                if status not in (*CLASSES, "censored") or (y is not None) != (
                    status == CLASSES[2]
                ):
                    raise ValueError("invalid_target_mask")
                if name == next(iter(indices)):
                    support[cell]["returns"] += int(y is not None)
                    support[cell]["classes"] += int(status in CLASSES)
                    support[cell]["censored"] += int(status == "censored")
                if y is not None:
                    if not math.isfinite(y):
                        raise ValueError("nonfinite_target")
                    errors = [y - q for q in pred["q"]]
                    per["pinball"].append(
                        mean(max(a * e, (a - 1) * e) for a, e in zip(LEVELS, errors, strict=True))
                    )
                    for j in range(3):
                        per[f"coverage{j}"].append(float(y <= pred["q"][j]))
                if status in CLASSES:
                    cl = CLASSES.index(status)
                    probabilities = [max(1e-6, p) for p in pred["p"]]
                    total = sum(probabilities)
                    probabilities = [p / total for p in probabilities]
                    per["log_loss"].append(-math.log(probabilities[cl]))
                    per["brier"].append(
                        sum((p - int(j == cl)) ** 2 for j, p in enumerate(pred["p"]))
                    )
            for metric, values in per.items():
                cells[cell][name][metric].append(mean(values))
    daily, missing = [], []
    for day in sorted({s["decision_ns"] // DAY for s in samples}):
        absent = [
            dict(day=day, pool=p, metric=m)
            for p in pools
            for m in metrics
            if not cells[day, p][next(iter(indices))][m]
        ]
        missing.extend(absent)
        values = {}
        for name in indices:
            values[name] = {
                m: mean(mean(cells[day, p][name][m]) for p in pools)
                if all(cells[day, p][name][m] for p in pools)
                else None
                for m in metrics
            }
            v = values[name]
            v["joint"] = (
                v["pinball"] / scale + v["log_loss"]
                if v["pinball"] is not None and v["log_loss"] is not None
                else None
            )
        daily.append(dict(day=day, fold=fold, models=values))
    aggregate = {
        name: {
            m: mean(d["models"][name][m] for d in daily)
            if all(d["models"][name][m] is not None for d in daily)
            else None
            for m in (*metrics, "joint")
        }
        for name in indices
    }
    return dict(
        daily=daily,
        aggregate=aggregate,
        missing=missing,
        scale=scale,
        support=[dict(day=k[0], pool=k[1], **v) for k, v in sorted(support.items())],
    )


def bootstrap(spec: Row, report: Row) -> Row:
    validate_spec(spec)
    daily = sorted(report["daily"], key=lambda r: (r["fold"], r["day"]))
    if report["missing"] or not daily:
        return dict(status="inconclusive", reason="missing_head_or_cohort_support")
    if len({(r["fold"], r["day"]) for r in daily}) != len(daily):
        raise ValueError("duplicate_day")
    runs: list[list[int]] = []
    for i, d in enumerate(daily):
        if not runs or d["fold"] != daily[i - 1]["fold"] or d["day"] != daily[i - 1]["day"] + 1:
            runs.append([])
        runs[-1].append(i)
    if any(len(run) < 4 for run in runs):
        return dict(status="inconclusive", reason="contiguous_run_shorter_than_four_days")
    arrays = {
        m: [r["models"][m]["joint"] for r in daily] for m in ("selected", "empirical", "linear")
    }
    if any(not math.isfinite(v) or v < 0 for a in arrays.values() for v in a):
        raise ValueError("invalid_daily_loss")
    comparisons: Row = {m: {} for m in ("empirical", "linear")}
    for length in (2, 4):
        rng = random.Random(spec["evaluation"]["bootstrap_seed"])
        draws: Row = {m: [] for m in comparisons}
        for _ in range(spec["evaluation"]["bootstrap_replicates"]):
            indices = []
            for run in runs:
                chosen: list[int] = []
                while len(chosen) < len(run):
                    start = rng.randrange(len(run) - length + 1)
                    chosen.extend(run[start : start + length])
                indices.extend(chosen[: len(run)])
            selected = mean(arrays["selected"][i] for i in indices)
            for m in comparisons:
                baseline = mean(arrays[m][i] for i in indices)
                if baseline <= 1e-12:
                    return dict(status="inconclusive", reason="undefined_relative_improvement")
                draws[m].append(1 - selected / baseline)
        tail = (1 - spec["evaluation"]["ci_level"]) / 2
        for m in comparisons:
            comparisons[m][f"block{length}"] = [
                quantile(draws[m], tail),
                quantile(draws[m], 1 - tail),
            ]
    a = report["aggregate"]
    evidence = dict(
        observed_days=len({d["day"] for d in daily}),
        comparisons=comparisons,
        max_coverage_error=max(
            abs(a["selected"][f"coverage{j}"] - q) for j, q in enumerate(LEVELS)
        ),
        brier_increase=max(a["selected"]["brier"] - a[m]["brier"] for m in comparisons),
        log_loss_increase=max(a["selected"]["log_loss"] - a[m]["log_loss"] for m in comparisons),
    )
    return dict(
        status="computed",
        evidence=evidence,
        verdict=result_verdict(spec, evidence),
        role="descriptive_until_released_holdout_support_is_verified",
        relative_improvement={
            m: 1 - mean(arrays["selected"]) / mean(arrays[m]) for m in comparisons
        },
        runs=[len(r) for r in runs],
    )
