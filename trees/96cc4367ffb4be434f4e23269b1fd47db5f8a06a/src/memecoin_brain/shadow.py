"""Durable label-free predictions first; independent delayed outcomes, never paper trades."""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, verify
from memecoin_runner.ledger import publish

from .data import HORIZONS, build
from .source import BAR, NS

Row = dict[str, Any]


def eligible(bundle: Row, *, model_finished_ns: int, now_ns: int) -> list[Row]:
    if model_finished_ns > now_ns:
        raise ValueError("future_model")
    latest = {}
    for sample in bundle["samples"]:
        if sample["actual_available_ns"] > now_ns:
            raise ValueError("future_shadow_feature")
        if 0 <= now_ns / NS - sample["event_close_s"] < BAR:
            if (
                sample["pool"] not in latest
                or sample["event_close_s"] > latest[sample["pool"]]["event_close_s"]
            ):
                latest[sample["pool"]] = sample
    # Remove all outcomes before inference. Model cannot receive future labels by accident.
    return [
        {**s, "targets": [dict(horizon=h, log_return=None, available_ns=None) for h in HORIZONS]}
        for s in latest.values()
    ]


def evaluate_prediction(prediction: Row, rows: list[Row], *, now_ns: int) -> Row | None:
    by_time = {r["open_s"]: r for r in rows if r["pool"] == prediction["pool"]}
    needed = [
        by_time.get(prediction["event_close_s"] + (k - 1) * BAR)
        for k in range(1, prediction["horizon"] + 1)
    ]
    target_close = prediction["event_close_s"] + prediction["horizon"] * BAR
    if not all(needed):
        if now_ns < (target_close + 3600) * NS:
            return None
        return dict(status="censored", reason="missing_future_bar_after_1h", observed_ns=now_ns)
    last = needed[-1]
    available = max(r["available_ns"] for r in needed)
    if available > now_ns:
        return None
    if available <= prediction["predicted_ns"] or target_close * NS <= prediction["predicted_ns"]:
        return dict(status="invalid", reason="target_precedes_prediction", observed_ns=now_ns)
    y = math.log(last["ohlcv"][3] / prediction["origin_close"])
    q = prediction["q"]
    loss = (
        sum(
            max(level * (y - p), (level - 1) * (y - p))
            for level, p in zip((0.1, 0.5, 0.9), q, strict=True)
        )
        / 3
    )
    candidates = {}
    for name, values in prediction.get("q_by_candidate", dict(selected=q)).items():
        if (
            len(values) != 3
            or values != sorted(values)
            or any(not math.isfinite(v) for v in values)
        ):
            raise ValueError("invalid_shadow_quantiles")
        candidates[name] = dict(
            pinball=sum(
                max(level * (y - p), (level - 1) * (y - p))
                for level, p in zip((0.1, 0.5, 0.9), values, strict=True)
            )
            / 3,
            coverage=[y <= p for p in values],
        )
    return dict(
        status="scored",
        log_return=y,
        pinball=loss,
        coverage=[y <= p for p in q],
        target_available_ns=available,
        scored_ns=now_ns,
        source_captures=[r["capture_sha"] for r in needed],
        target_is_price_not_execution=True,
        candidate_metrics=candidates,
    )


def tick(
    root: Path,
    rows: list[Row],
    provenance: Row,
    model: Path | None,
    *,
    prediction_pools: set[str] | None = None,
) -> Row:
    predictions = root / "shadow/predictions"
    outcomes = root / "shadow/outcomes"
    predictions.mkdir(exist_ok=True)
    outcomes.mkdir(exist_ok=True)
    completed = 0
    for path in sorted(predictions.glob("*.json")):
        if (outcomes / path.name).exists():
            continue
        value = evaluate_prediction(read(path), rows, now_ns=time.time_ns())
        if value is not None:
            publish(outcomes / path.name, dict(prediction_sha=sha(path), **value))
            completed += 1
    issued = 0
    if model is not None:
        from .model import ensemble_predict, predict

        verify(model)
        card = read(model / "model-card.json")
        bundle = build(rows, provenance)
        now = time.time_ns()
        samples = eligible(bundle, model_finished_ns=card["finished_ns"], now_ns=now)
        samples = [
            s
            for s in samples
            if (prediction_pools is None or s["pool"] in prediction_pools)
            and not all((predictions / (digest([s["id"], h]) + ".json")).exists() for h in HORIZONS)
        ]
        if samples:
            qs = predict(model, bundle, samples)
            paired = {
                "empirical": [
                    card["baseline"][str(t["horizon"])] for s in samples for t in s["targets"]
                ]
            }
            for kind in ("linear", "gru"):
                paired[kind] = ensemble_predict(model, kind, bundle, samples)
            by_key = {(r["pool"], r["open_s"]): r for r in rows}
            for i, s in enumerate(samples):
                for j, h in enumerate(HORIZONS):
                    key = digest([s["id"], h])
                    path = predictions / (key + ".json")
                    if path.exists():
                        continue
                    actual = time.time_ns()
                    # Inference may cross a bar boundary; never backdate the resulting prediction.
                    if actual >= (s["event_close_s"] + h * BAR) * NS:
                        continue
                    origin = by_key[(s["pool"], s["event_close_s"] - BAR)]
                    publish(
                        path,
                        dict(
                            id=s["id"],
                            pool=s["pool"],
                            event_close_s=s["event_close_s"],
                            horizon=h,
                            predicted_ns=actual,
                            model_sha=sha(model / "manifest.json"),
                            model_path=str(model.resolve()),
                            q=qs[i * len(HORIZONS) + j],
                            q_by_candidate={
                                name: values[i * len(HORIZONS) + j]
                                for name, values in paired.items()
                            },
                            origin_close=origin["ohlcv"][3],
                            input_available_ns=s["actual_available_ns"],
                            history=s["history"],
                            input_frames={k: bundle["frames"][k] for k in s["history"]},
                            input_schema="Brain bar features; not CPMM",
                            training_cutoff_ns=card["training_as_of_ns"],
                            F01=False,
                        ),
                    )
                    issued += 1
    return dict(
        new_predictions=issued,
        new_outcomes=completed,
        total_predictions=len(list(predictions.glob("*.json"))),
        total_outcomes=len(list(outcomes.glob("*.json"))),
        trade_orders=0,
    )
