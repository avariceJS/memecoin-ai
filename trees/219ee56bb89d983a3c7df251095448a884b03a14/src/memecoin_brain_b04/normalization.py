"""Bound train-only winsorization artifact; raw frames and labels are never edited."""

import math

from memecoin_brain.data import FEATURES
from memecoin_registry.contracts import digest
from memecoin_runner.scoring import quantile

BINARY = {"past_positive_fraction", "zero_volume"}


def fit(bundle: dict, samples: list[dict]) -> dict:
    keys = sorted({k for s in samples for k in s["history"]})
    if not keys:
        raise ValueError("empty_normalization_train")
    limits = []
    for j, name in enumerate(FEATURES):
        values = [
            bundle["frames"][k]["values"][j]
            for k in keys
            if bundle["frames"][k]["values"][j] is not None
        ]
        if any(not math.isfinite(v) for v in values):
            raise ValueError("nonfinite_training_feature")
        lo, hi = (quantile(values, 0.01), quantile(values, 0.99)) if values else (0.0, 0.0)
        enabled = name not in BINARY and hi > lo
        limits.append(
            dict(
                name=name,
                low=lo,
                high=hi,
                enabled=enabled,
                reason="winsorize" if enabled else "binary_or_degenerate",
            )
        )
    return dict(
        schema=1,
        kind="train_q01_q99_then_original_z",
        features=list(FEATURES),
        train_frames_sha=digest(keys),
        unique_train_frames=len(keys),
        limits=limits,
    )


def apply(bundle: dict, fitted: dict) -> dict:
    if (
        fitted["schema"] != 1
        or fitted["kind"] != "train_q01_q99_then_original_z"
        or fitted["features"] != list(FEATURES)
        or [x["name"] for x in fitted["limits"]] != list(FEATURES)
    ):
        raise ValueError("normalization_contract")
    for x in fitted["limits"]:
        if not math.isfinite(x["low"]) or not math.isfinite(x["high"]) or x["low"] > x["high"]:
            raise ValueError("normalization_bounds")
    frames = {}
    for k, frame in bundle["frames"].items():
        values = []
        for v, limit in zip(frame["values"], fitted["limits"], strict=True):
            if v is not None and not math.isfinite(v):
                raise ValueError("nonfinite_feature")
            values.append(
                min(limit["high"], max(limit["low"], v))
                if v is not None and limit["enabled"]
                else v
            )
        frames[k] = dict(frame, values=values)
    return dict(bundle, frames=frames)
