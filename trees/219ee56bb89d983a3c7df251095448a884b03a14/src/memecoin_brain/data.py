"""Separate bar features/targets: historical event-time replay is never online availability."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import digest, seal, verify

from .source import BAR, bars

Row = dict[str, Any]
FEATURES = (
    "log_return_1",
    "log_close_open",
    "log_high_low",
    "log_volume_usd",
    "upper_wick",
    "lower_wick",
    "volume_vs_past",
    "log_return_3",
    "log_return_6",
    "past_return_rms",
    "past_positive_fraction",
    "zero_volume",
)
HORIZONS = (1, 3)
CONTEXT = 8


def corpus(root: Path, as_of_ns: int) -> tuple[list[Row], Row]:
    universe = read(root / "universe.json")
    pools = {p["pool"] for p in universe["pools"]}
    if universe["admitted_ns"] > as_of_ns:
        raise ValueError("universe_not_available")
    first, revisions, captures = {}, [], []
    for path in sorted((root / "requests").iterdir()):
        if not (path / "receipt.json").exists():
            continue
        receipt = read(path / "receipt.json")
        if receipt["available_ns"] > as_of_ns:
            continue
        request = read(path / "request.json")
        if not request["started_ns"] <= receipt["received_ns"] <= receipt["available_ns"]:
            raise ValueError("capture_availability_order")
        if request["lane"] == "discovery":
            continue
        pool = request["url"].split("/pools/")[1].split("/")[0]
        if pool not in pools:
            raise ValueError("unadmitted_pool")
        captures.append(dict(path=path.name, manifest_sha=sha(path / "manifest.json")))
        for row in bars(path, pool):
            key = (pool, row["open_s"])
            if key not in first:
                first[key] = row
            elif first[key]["ohlcv"] != row["ohlcv"]:
                revisions.append(
                    dict(
                        pool=pool,
                        open_s=row["open_s"],
                        observed_ns=row["available_ns"],
                        original_sha=first[key]["capture_sha"],
                        revised_sha=row["capture_sha"],
                    )
                )
    result = sorted(first.values(), key=lambda r: (r["pool"], r["open_s"]))
    return result, dict(
        as_of_ns=as_of_ns,
        universe_sha=sha(root / "universe.json"),
        captures=captures,
        revisions=revisions,
        counts=dict(Counter(r["pool"] for r in result)),
        first_observed_versions=True,
        historical_online_availability_known=False,
    )


def features(history: list[Row]) -> list[float | None]:
    o, high, low, c, v = history[-1]["ohlcv"]
    closes = [r["ohlcv"][3] for r in history]
    changes = [math.log(b / a) for a, b in zip(closes[:-1], closes[1:], strict=True)]
    previous_volumes = [r["ohlcv"][4] for r in history[:-1]]
    return [
        changes[-1] if changes else None,
        math.log(c / o),
        math.log(high / low),
        math.log1p(v),
        math.log(high / max(c, o)),
        math.log(min(c, o) / low),
        math.log((v + 1) / (sum(previous_volumes) / len(previous_volumes) + 1))
        if previous_volumes
        else None,
        math.log(c / closes[-4]) if len(closes) >= 4 else None,
        math.log(c / closes[-7]) if len(closes) >= 7 else None,
        math.sqrt(sum(r * r for r in changes) / len(changes)) if changes else None,
        sum(r > 0 for r in changes) / len(changes) if changes else None,
        float(v == 0),
    ]


def build(rows: list[Row], provenance: Row) -> Row:
    grouped = defaultdict(list)
    for row in rows:
        if row["available_ns"] > provenance["as_of_ns"]:
            raise ValueError("future_training_input")
        grouped[row["pool"]].append(row)
    frames, samples = {}, []
    for pool, pool_rows in sorted(grouped.items()):
        pool_rows.sort(key=lambda r: r["open_s"])
        lookup = {r["open_s"]: r for r in pool_rows}
        if len(lookup) != len(pool_rows):
            raise ValueError("duplicate_bar_identity")
        history = []
        for row in pool_rows:
            if history and row["open_s"] != history[-1]["open_s"] + BAR:
                history = []
            history.append(row)
            history = history[-CONTEXT:]
            key = f"{pool}:{row['open_s']}"
            frames[key] = dict(
                values=features(history),
                event_close_s=row["open_s"] + BAR,
                available_ns=row["available_ns"],
                pool=pool,
                capture_sha=row["capture_sha"],
            )
            if len(history) < CONTEXT:
                continue
            targets = []
            for horizon in HORIZONS:
                needed = [lookup.get(row["open_s"] + k * BAR) for k in range(1, horizon + 1)]
                valid = all(needed)
                target = needed[-1] if valid else None
                targets.append(
                    dict(
                        horizon=horizon,
                        log_return=math.log(target["ohlcv"][3] / row["ohlcv"][3])
                        if target
                        else None,
                        available_ns=max(x["available_ns"] for x in needed) if valid else None,
                    )
                )
            samples.append(
                dict(
                    id=key,
                    pool=pool,
                    event_close_s=row["open_s"] + BAR,
                    actual_available_ns=max(r["available_ns"] for r in history),
                    history=[f"{pool}:{r['open_s']}" for r in history],
                    maturity_s=row["open_s"] + BAR * (1 + max(HORIZONS)),
                    targets=targets,
                )
            )
    if len(samples) > 40000:
        raise ValueError("B01_dataset_capacity")
    return dict(
        schema=1,
        track="brain_development",
        target="log_price_return_no_execution",
        feature_names=list(FEATURES),
        provenance=provenance,
        frames=frames,
        samples=samples,
        F01=False,
    )


def split(bundle: Row) -> tuple[Row, Row]:
    times = sorted({s["event_close_s"] for s in bundle["samples"]})
    if len(times) < 30:
        raise ValueError("insufficient_development_times")
    a, b, end = times[len(times) * 6 // 10], times[len(times) * 8 // 10], times[-1] + BAR
    parts: Row = {k: [] for k in ("train", "validation", "test", "purged")}
    for sample in bundle["samples"]:
        t, maturity = sample["event_close_s"], sample["maturity_s"]
        name = "purged"
        for role, start, stop in (("train", times[0], a), ("validation", a, b), ("test", b, end)):
            if (
                start <= t < stop
                and maturity < stop
                and all(t["log_return"] is not None for t in sample["targets"])
            ):
                name = role
        parts[name].append(sample)
    if any(len(parts[k]) < 16 for k in ("train", "validation", "test")):
        raise ValueError("insufficient_purged_development_support")
    return parts, dict(
        train_start_s=times[0],
        train_end_s=a,
        validation_end_s=b,
        test_end_s=end,
        group_maturity_purge=True,
        retrospective_not_online=True,
        counts={k: len(v) for k, v in parts.items()},
    )


def scaler(bundle: Row, samples: list[Row]) -> Row:
    keys = sorted({k for s in samples for k in s["history"]})
    means, scales = [], []
    for j in range(len(FEATURES)):
        values = [
            bundle["frames"][k]["values"][j]
            for k in keys
            if bundle["frames"][k]["values"][j] is not None
        ]
        mean = sum(values) / len(values) if values else 0.0
        scale = math.sqrt(sum((x - mean) ** 2 for x in values) / len(values)) if values else 1.0
        means.append(mean)
        scales.append(max(scale, 1e-6))
    return dict(
        mean=means, scale=scales, unique_train_frames=len(keys), feature_names=list(FEATURES)
    )


def encode(bundle: Row, sample: Row, fitted: Row) -> list[list[float]]:
    if fitted["feature_names"] != list(FEATURES):
        raise ValueError("feature_contract")
    out = []
    last = None
    for key in sample["history"]:
        frame = bundle["frames"][key]
        if (
            frame["pool"] != sample["pool"]
            or frame["event_close_s"] > sample["event_close_s"]
            or (last is not None and frame["event_close_s"] != last + BAR)
        ):
            raise ValueError("history_order_or_gap")
        if frame["available_ns"] > sample["actual_available_ns"]:
            raise ValueError("future_history_receipt")
        values = frame["values"]
        out.append(
            [
                (v - m) / s if v is not None else 0.0
                for v, m, s in zip(values, fitted["mean"], fitted["scale"], strict=True)
            ]
            + [float(v is None) for v in values]
            + [math.log1p(sample["event_close_s"] - frame["event_close_s"])]
        )
        last = frame["event_close_s"]
    if len(out) != CONTEXT or last != sample["event_close_s"]:
        raise ValueError("missing_decision_context")
    return out


def save_dataset(output: Path, bundle: Row) -> None:
    output.mkdir(exist_ok=False)
    write(output / "dataset.json", bundle)
    seal(output)


def load_dataset(path: Path) -> Row:
    verify(path)
    data = read(path / "dataset.json")
    if (
        data["feature_names"] != list(FEATURES)
        or data["track"] != "brain_development"
        or data["F01"] is not False
    ):
        raise ValueError("not_Brain_dataset")
    return data


def fingerprint(bundle: Row) -> str:
    return digest(dict(frames=bundle["frames"], samples=bundle["samples"]))
