"""Concrete OHLCV producer for the extensible dense representation boundary."""

import hashlib
from pathlib import Path

from memecoin_brain import data
from memecoin_brain.source import BAR, NS

from . import numerics, records
from .schema import validate


def ohlcv_schema(context=8, horizons=(1, 3), *, stable=False):
    if type(context) is not int or not 1 <= context <= 256:
        raise ValueError("experience_context")
    if (
        not horizons
        or any(type(h) is not int or not 1 <= h <= 4096 for h in horizons)
        or len(set(horizons)) != len(horizons)
    ):
        raise ValueError("experience_horizons")
    implementation = hashlib.sha256()
    for module in (data, records, numerics) if stable else (data, records):
        implementation.update(Path(module.__file__).read_bytes())
    implementation.update(Path(__file__).read_bytes())
    return validate(
        dict(
            schema="brain-experience-v2",
            layout="dense-sequence-f64-v1",
            context=context,
            features=[
                dict(
                    name=name,
                    unit={
                        "log_volume_usd": "log1p_USD",
                        "past_positive_fraction": "fraction",
                        "zero_volume": "indicator",
                    }.get(name, "log_ratio"),
                    modality="ohlcv",
                )
                for name in data.FEATURES
            ],
            targets=[
                dict(
                    name=f"return_{h * BAR}s",
                    unit="log_ratio",
                    kind=f"origin_close_log_return@{h * BAR}s",
                )
                for h in horizons
            ],
            representation=dict(
                name="ohlcv-sequence",
                version="3" if stable else "2",
                code_sha=implementation.hexdigest(),
            ),
            availability="historical_replay",
        )
    )


def ohlcv_samples(rows, cutoff, schema):
    horizons = tuple(int(t["kind"].split("@")[1][:-1]) // BAR for t in schema["targets"])
    stable = schema["representation"]["version"] == "3"
    if schema != ohlcv_schema(schema["context"], horizons, stable=stable):
        raise ValueError("experience_representation_implementation")
    for row in records.experiences(
        rows, cutoff, schema["context"], horizons=horizons, stable=stable
    ):
        yield dict(
            id=row["id"],
            pool=row["pool"],
            decision_ns=row["origin_s"] * NS,
            input_start_ns=row["input_start_s"] * NS,
            input_available_ns=row["input_available_ns"],
            input_dependencies=[
                dict(artifact_sha=pin, event_ns=t * NS, available_ns=at)
                for t, at, pin in row["input_dependencies"]
            ],
            values=row["values"],
            targets=row["targets"],
            target_status=row["target_status"],
            target_available_ns=row["target_available_ns"],
            target_captures=row["target_captures"],
            target_maturity_ns=[(row["origin_s"] + h * BAR) * NS for h in horizons],
        )
