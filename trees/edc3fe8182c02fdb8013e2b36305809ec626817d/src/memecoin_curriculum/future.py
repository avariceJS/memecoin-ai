"""Exact prospective joins; frozen weights alone do not make an offline forecast prospective."""

import math
from collections import Counter, defaultdict
from pathlib import Path

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, verify


def read_lane(exam, ref, cutoff, activation):
    parent = exam / "candidate-shadows" / ref["sha"] / "shadow"
    result, counts = {}, Counter()
    for path in sorted((parent / "predictions").glob("*.json")):
        p = read(path)
        if p["predicted_ns"] > cutoff:
            continue
        if p["model_sha"] != ref["sha"] or not activation <= p["predicted_ns"]:
            raise ValueError("future_prediction_binding")
        if p["input_available_ns"] > p["predicted_ns"] or p["event_close_s"] * 10**9 < activation:
            raise ValueError("future_input_boundary")
        counts["predictions"] += 1
        outcome = parent / "outcomes" / path.name
        if not outcome.exists():
            counts["pending"] += 1
            continue
        o = read(outcome)
        if o.get("scored_ns", o.get("observed_ns", cutoff + 1)) > cutoff:
            counts["pending"] += 1
            continue
        if o["prediction_sha"] != sha(path):
            raise ValueError("future_outcome_binding")
        counts[o["status"]] += 1
        if o["status"] != "scored":
            continue
        if not p["predicted_ns"] < o["target_available_ns"] <= o["scored_ns"]:
            raise ValueError("future_target_boundary")
        if (p["event_close_s"] + 300 * p["horizon"]) * 10**9 <= p["predicted_ns"]:
            raise ValueError("future_target_before_prediction")
        key = (p["pool"], p["event_close_s"], p["horizon"])
        if key in result:
            raise ValueError("future_duplicate_origin")
        result[key] = dict(prediction=p, outcome=o)
    return result, dict(counts)


def metric(rows, family):
    pools, days, cells = defaultdict(list), defaultdict(list), defaultdict(list)
    coverage = [0, 0, 0]
    for key, row in rows:
        value = row["outcome"]["candidate_metrics"][family]
        q = row["prediction"]["q_by_candidate"][family]
        y = row["outcome"]["log_return"]
        if len(q) != 3 or q != sorted(q) or not all(math.isfinite(x) for x in [y, *q]):
            raise ValueError("future_invalid_quantiles")
        loss = (
            sum(
                max(t * (y - p), (t - 1) * (y - p)) for t, p in zip((0.1, 0.5, 0.9), q, strict=True)
            )
            / 3
        )
        if abs(loss - value["pinball"]) > 1e-12 or value["coverage"] != [y <= x for x in q]:
            raise ValueError("future_metric_reproduction")
        pools[key[0]].append(value["pinball"])
        days[key[1] // 86400].append(value["pinball"])
        cells[(key[0], key[1] // 86400)].append(value["pinball"])
        coverage = [a + int(b) for a, b in zip(coverage, value["coverage"], strict=True)]
    n = len(rows)
    return dict(
        variants=n,
        pools=len(pools),
        utc_days=len(days),
        pool_days=len(cells),
        pinball=sum(sum(v) for v in pools.values()) / n if n else None,
        pool_macro_pinball=sum(sum(v) / len(v) for v in pools.values()) / len(pools)
        if pools
        else None,
        pool_day_macro_pinball=sum(sum(v) / len(v) for v in cells.values()) / len(cells)
        if cells
        else None,
        coverage=[x / n for x in coverage] if n else None,
        per_pool={p: dict(variants=len(v), pinball=sum(v) / len(v)) for p, v in pools.items()},
    )


def compare_exam(exam, cutoff):
    c = read(exam / "config.json")
    lanes, totals = {}, {}
    for ref in c["frozen_exam_models"]:
        model = Path(ref["path"])
        verify(model)
        card = read(model / "model-card.json")
        if sha(model / "manifest.json") != ref["sha"] or not (
            card["training_as_of_ns"] <= card["finished_ns"] < c["activation_ns"]
        ):
            raise ValueError("future_model_boundary")
        lanes[ref["label"]], totals[ref["label"]] = read_lane(exam, ref, cutoff, c["activation_ns"])
    common = set.intersection(*(set(rows) for rows in lanes.values()))
    for key in common:
        rows = [lane[key] for lane in lanes.values()]
        if (
            len(
                {
                    digest(
                        [
                            r["prediction"]["history"],
                            r["prediction"]["input_frames"],
                            r["prediction"]["origin_close"],
                        ]
                    )
                    for r in rows
                }
            )
            != 1
        ):
            raise ValueError("future_paired_input_mismatch")
        if (
            len(
                {
                    digest([r["outcome"]["log_return"], r["outcome"]["source_captures"]])
                    for r in rows
                }
            )
            != 1
        ):
            raise ValueError("future_paired_target_mismatch")
    metrics = {
        label: {
            str(h * 5): {
                family: metric([(key, lane[key]) for key in sorted(common) if key[2] == h], family)
                for family in ("empirical", "linear", "gru")
            }
            for h in (1, 3)
        }
        for label, lane in lanes.items()
    }
    return dict(
        version="prospective-data-comparison-v1",
        cutoff_ns=cutoff,
        config_sha=sha(exam / "config.json"),
        activation_ns=c["activation_ns"],
        model_refs=c["frozen_exam_models"],
        totals=totals,
        common_variants=len(common),
        common_decisions=len({key[:2] for key in common}),
        common_membership=digest(sorted(common)),
        metrics=metrics,
        all_predictions_published_prospectively=True,
        exact_inputs_and_targets=True,
        independent_sample_size=None,
        confirmatory=False,
        improvement_established=False,
        note="Paired overlapping outcomes; interpret only with horizon, pool and day support.",
    )
