"""Immutable, receipt-causal engineering datasets; no market qualification implied."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from memecoin_ai.causal import FEATURE_NAMES, validate_feature_row
from memecoin_ai.execution_targets import verify_execution_targets

Row = dict[str, Any]
CLASSES = ("entry_unfilled", "exit_unfilled", "conditional_roundtrip")


def read(path: Path) -> Any:
    return json.loads(path.read_text())


def write(path: Path, value: Any) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, sort_keys=True, allow_nan=False, indent=2)
        stream.write("\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> list[Row]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def seal(root: Path) -> None:
    write(root / "manifest.json", {p.name: sha(p) for p in sorted(root.iterdir()) if p.is_file()})


def verify(root: Path) -> None:
    expected = read(root / "manifest.json")
    actual = {p.name: sha(p) for p in root.iterdir() if p.is_file() and p.name != "manifest.json"}
    if actual != expected:
        raise ValueError("artifact checksum/file inventory mismatch")


def _validate(bundle: Row) -> None:
    if bundle["schema"] != 1 or bundle["feature_names"] != list(FEATURE_NAMES):
        raise ValueError("unsupported dataset schema")
    if bundle["qualification"] != "engineering_only":
        raise ValueError("market qualification is not implemented")
    frames = bundle["frames"]
    policy = bundle["history_policy"]
    if not 1 <= policy["context"] <= 256 or policy["max_gap_ns"] <= 0:
        raise ValueError("invalid history policy")
    ids: set[str] = set()
    for sample in bundle["samples"]:
        if sample["id"] in ids:
            raise ValueError("duplicate decision")
        ids.add(sample["id"])
        history = [frames[key] for key in sample["history"]]
        if not history or history[-1]["decision_ns"] != sample["decision_ns"]:
            raise ValueError("history does not end at decision")
        if len(history) > policy["context"]:
            raise ValueError("history exceeds declared context")
        previous = -1
        for frame in history:
            if not previous < frame["decision_ns"] <= sample["decision_ns"]:
                raise ValueError("noncausal or unordered history")
            if not 0 <= frame["available_ns"] <= frame["decision_ns"]:
                raise ValueError("future availability")
            if frame["group"] != sample["group"]:
                raise ValueError("history crosses pool/segment/gap")
            if len(frame["values"]) != len(FEATURE_NAMES) or any(
                v is not None and not math.isfinite(v) for v in frame["values"]
            ):
                raise ValueError("invalid feature vector")
            if previous >= 0 and frame["decision_ns"] - previous > policy["max_gap_ns"]:
                raise ValueError("history crosses time gap")
            previous = frame["decision_ns"]
        seen: set[tuple[float, ...]] = set()
        for target in sample["targets"]:
            action = target["action"]
            if (
                len(action) != 4
                or action[0] not in (0, 1)
                or any(not math.isfinite(v) or v < 0 for v in action)
            ):
                raise ValueError("invalid pre-action scenario")
            if tuple(action) in seen:
                raise ValueError("duplicate scenario")
            seen.add(tuple(action))
            if target["available_ns"] < sample["decision_ns"]:
                raise ValueError("target precedes decision")
            if target["status"] not in (*CLASSES, "censored"):
                raise ValueError("unknown target status")
            value = target["return"]
            if (target["status"] == CLASSES[2]) != (value is not None):
                raise ValueError("return mask disagrees with conditional execution")
            if value is not None and not math.isfinite(value):
                raise ValueError("nonfinite target")
        if not seen or sample["maturity_ns"] != max(t["available_ns"] for t in sample["targets"]):
            raise ValueError("group maturity mismatch")


def save_dataset(root: Path, bundle: Row) -> None:
    _validate(bundle)
    root.mkdir(parents=True, exist_ok=False)
    write(root / "dataset.json", bundle)
    seal(root)


def load_dataset(root: Path) -> Row:
    verify(root)
    bundle: Row = read(root / "dataset.json")
    _validate(bundle)
    return bundle


def export_window(window: Path, output: Path, *, context: int, max_gap_ns: int) -> None:
    """Read only an already sealed D43 window; never change/reseal its artifacts."""
    if not 1 <= context <= 256 or max_gap_ns <= 0:
        raise ValueError("invalid history bounds")
    source_seal = read(window / "seal.json")
    for name, checksum in source_seal["files"].items():
        if sha(window / name) != checksum:
            raise ValueError("D43 window seal mismatch")
    verify_execution_targets(window / "campaign", window / "campaign-targets")
    target_groups: dict[tuple[int, str], list[Row]] = {}
    for target in rows(window / "campaign-targets/targets.jsonl"):
        target_groups.setdefault((target["campaign_segment"], target["sample_id"]), []).append(
            target
        )
    frames: Row = {}
    samples = []
    for path in sorted((window / "campaign/segments").glob("*/corpus/features.jsonl")):
        segment = int(path.parent.parent.name)
        histories: dict[str, list[str]] = {}
        generations: dict[str, int] = {}
        last_segments: dict[str, int] = {}
        for feature in rows(path):
            validate_feature_row(feature)
            pool = feature["pool_id"]
            history = histories.setdefault(pool, [])
            t = feature["decision_ns"]
            reset = (
                not feature["usable"]
                or last_segments.get(pool) != feature["segment"]
                or (history and t - frames[history[-1]]["decision_ns"] > max_gap_ns)
            )
            if reset:
                history.clear()
                generations[pool] = generations.get(pool, 0) + 1
            last_segments[pool] = feature["segment"]
            if not feature["usable"]:
                continue
            group = f"{segment}:{pool}:{generations[pool]}"
            key = f"{segment}:{feature['sample_id']}"
            if key in frames:
                raise ValueError("duplicate feature identity")
            frames[key] = dict(
                group=group,
                decision_ns=t,
                available_ns=feature["feature_available_ns"],
                values=[feature["values"][n] for n in FEATURE_NAMES],
            )
            history.append(key)
            targets = [
                dict(
                    action=[
                        r["input_index"],
                        r["size_ppm"] / 1e6,
                        r["assumed_delay_ns"] / 1e9,
                        r["holding_ns"] / 1e9,
                    ],
                    status=r["status"],
                    reason=r["reason"],
                    available_ns=r["target_available_ns"],
                    return_value=r["return_before_network_costs"],
                )
                for r in target_groups[(segment, feature["sample_id"])]
            ]
            for target in targets:
                target["return"] = target.pop("return_value")
            samples.append(
                dict(
                    id=key,
                    group=group,
                    decision_ns=t,
                    maturity_ns=max(x["available_ns"] for x in targets),
                    history=history[-context:].copy(),
                    targets=targets,
                )
            )
    bundle = dict(
        schema=1,
        qualification="engineering_only",
        feature_names=list(FEATURE_NAMES),
        source=dict(
            kind="sealed_D43_window",
            path=str(window.resolve()),
            seal_sha256=sha(window / "seal.json"),
            target_semantics="conditional reset; before unknown network costs",
        ),
        history_policy=dict(context=context, max_gap_ns=max_gap_ns),
        frames=frames,
        samples=samples,
    )
    save_dataset(output, bundle)


@dataclass(frozen=True)
class Split:
    train_start_ns: int
    train_end_ns: int
    validation_end_ns: int
    test_end_ns: int
    embargo_ns: int = 0

    def __post_init__(self) -> None:
        if (
            not (
                0
                <= self.train_start_ns
                < self.train_end_ns
                < self.validation_end_ns
                < self.test_end_ns
            )
            or self.embargo_ns < 0
        ):
            raise ValueError("invalid chronological split")


def partition(bundle: Row, split: Split) -> dict[str, list[Row]]:
    result: dict[str, list[Row]] = {k: [] for k in ("train", "validation", "test", "purged")}
    for sample in bundle["samples"]:
        t, maturity = sample["decision_ns"], sample["maturity_ns"]
        label = "purged"
        for name, start, end in (
            ("train", split.train_start_ns, split.train_end_ns),
            ("validation", split.train_end_ns + split.embargo_ns, split.validation_end_ns),
            ("test", split.validation_end_ns + split.embargo_ns, split.test_end_ns),
        ):
            if start <= t < end and maturity < end:
                label = name
        result[label].append(sample)
    return result


def fit_scaler(bundle: Row, train: list[Row]) -> Row:
    keys = sorted({key for sample in train for key in sample["history"]})
    if not keys:
        raise ValueError("empty training history")
    columns = [
        [bundle["frames"][key]["values"][j] for key in keys] for j in range(len(FEATURE_NAMES))
    ]
    means, scales, counts = [], [], []
    for column in columns:
        observed = [v for v in column if v is not None]
        mean = sum(observed) / len(observed) if observed else 0.0
        variance = sum((v - mean) ** 2 for v in observed) / len(observed) if observed else 0.0
        means.append(mean)
        scales.append(max(math.sqrt(variance), 1e-8))
        counts.append(len(observed))
    return dict(
        mean=means,
        scale=scales,
        observed=counts,
        unique_frames=len(keys),
        feature_names=list(FEATURE_NAMES),
    )


def encode_history(
    frames: list[Row], decision_ns: int, scaler: Row, context: int
) -> list[list[float]]:
    """Label-free inference transform. No pool IDs or absolute timestamps enter tensors."""
    if not 1 <= context <= 256:
        raise ValueError("invalid inference context")
    if not frames or scaler["feature_names"] != list(FEATURE_NAMES):
        raise ValueError("empty history/schema mismatch")
    result = []
    previous = -1
    for frame in frames[-context:]:
        if not previous < frame["decision_ns"] <= decision_ns or not (
            0 <= frame["available_ns"] <= frame["decision_ns"]
        ):
            raise ValueError("inference history is noncausal")
        if frame["group"] != frames[-1]["group"]:
            raise ValueError("inference history crosses group")
        values = frame["values"]
        if len(values) != len(FEATURE_NAMES) or any(
            v is not None and not math.isfinite(v) for v in values
        ):
            raise ValueError("invalid inference features")
        result.append(
            [
                (v - m) / s if v is not None else 0.0
                for v, m, s in zip(values, scaler["mean"], scaler["scale"], strict=True)
            ]
            + [float(v is None) for v in values]
            + [math.log1p((decision_ns - frame["available_ns"]) / 1e9)]
        )
        previous = frame["decision_ns"]
    if frames[-1]["decision_ns"] != decision_ns:
        raise ValueError("inference decision frame missing")
    return result


def synthetic(output: Path, *, count: int = 120) -> None:
    """Deterministic synthetic integration fixture, explicitly not market evidence."""
    frames: Row = {}
    samples = []
    for i in range(count):
        t = (i + 1) * 10**9
        key = str(i)
        values: list[float | None] = [math.sin(i / 9 + j) for j in range(len(FEATURE_NAMES))]
        if i % 11 == 0:
            values[3] = None
        frames[key] = dict(group="synthetic", decision_ns=t, available_ns=t - 1000, values=values)
        targets = []
        for action in (0, 1):
            status = "censored" if i % 17 == 0 else CLASSES[(i + action) % 3]
            targets.append(
                dict(
                    action=[action, 0.001, 0, 2],
                    status=status,
                    reason="synthetic",
                    available_ns=t + 2 * 10**9,
                    return_value=math.sin((i + 2) / 9) * 0.01 if status == CLASSES[2] else None,
                )
            )
        for target in targets:
            target["return"] = target.pop("return_value")
        samples.append(
            dict(
                id=key,
                group="synthetic",
                decision_ns=t,
                maturity_ns=t + 2 * 10**9,
                history=[str(k) for k in range(max(0, i - 7), i + 1)],
                targets=targets,
            )
        )
    save_dataset(
        output,
        dict(
            schema=1,
            qualification="engineering_only",
            feature_names=list(FEATURE_NAMES),
            source=dict(kind="synthetic"),
            history_policy=dict(context=8, max_gap_ns=2 * 10**9),
            frames=frames,
            samples=samples,
        ),
    )


def split_dict(split: Split) -> Row:
    return asdict(split)
