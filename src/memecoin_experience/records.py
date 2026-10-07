"""Bounded-history OHLCV experience with transitive receipt and capture dependencies."""

import itertools
import math
import re
from collections import deque

from memecoin_brain.data import FEATURES, features
from memecoin_brain.source import BAR, EXCLUDED, NS

SCHEMA = "brain-experience-v1"
HORIZONS = (1, 3)


def checked_rows(rows, cutoff):
    if type(cutoff) is not int or cutoff < 0:
        raise ValueError("experience_cutoff")
    previous = None
    for row in rows:
        key = (row["pool"], row["open_s"])
        if previous is not None and key <= previous:
            raise ValueError("experience_order_or_duplicate")
        if row["pool"] in EXCLUDED:
            raise ValueError("experience_protected_pool")
        t, available = row["open_s"], row["available_ns"]
        values = row["ohlcv"]
        if (
            type(t) is not int
            or t < 0
            or t % BAR
            or type(available) is not int
            or not (t + BAR) * NS <= available <= cutoff
            or len(values) != 5
            or any(not math.isfinite(v) for v in values)
            or min(values[:4]) <= 0
            or values[4] < 0
            or values[1] < max(values[0], values[3])
            or values[2] > min(values[0], values[3])
            or not isinstance(row["capture_sha"], str)
            or re.fullmatch(r"[0-9a-f]{64}", row["capture_sha"]) is None
        ):
            raise ValueError("experience_bar_contract")
        previous = key
        yield row


def experiences(rows, cutoff, context=8, *, horizons=HORIZONS):
    """Emit all context-ready origins, including incomplete labels. Memory O(context)."""
    if type(context) is not int or not 1 <= context <= 256:
        raise ValueError("experience_context")
    if (
        not horizons
        or any(type(h) is not int or not 1 <= h <= 4096 for h in horizons)
        or len(set(horizons)) != len(horizons)
    ):
        raise ValueError("experience_horizons")
    for pool, stream in itertools.groupby(checked_rows(rows, cutoff), key=lambda r: r["pool"]):
        future = deque(itertools.islice(stream, max(horizons) + 1))
        past, frames = deque(maxlen=8), deque(maxlen=context)
        previous = None
        while future:
            row = future.popleft()
            if previous is not None and row["open_s"] != previous + BAR:
                past.clear()
                frames.clear()
            previous = row["open_s"]
            past.append(row)
            dependencies = [(r["open_s"], r["available_ns"], r["capture_sha"]) for r in past]
            values = features(list(past))
            if any(v is not None and not math.isfinite(v) for v in values):
                raise ValueError("experience_nonfinite_feature")
            frames.append((values, dependencies))
            if len(frames) == context:
                inputs = sorted({tuple(d) for _, deps in frames for d in deps})
                targets, status, label_times, target_pins = [], [], [], []
                lookup = {r["open_s"]: r for r in future}
                for horizon in horizons:
                    needed = [lookup.get(row["open_s"] + BAR * i) for i in range(1, horizon + 1)]
                    valid = all(r is not None for r in needed)
                    ratio = needed[-1]["ohlcv"][3] / row["ohlcv"][3] if valid else None
                    if ratio is not None and (ratio <= 0 or not math.isfinite(ratio)):
                        raise ValueError("experience_nonfinite_target")
                    targets.append(math.log(ratio) if valid else None)
                    if targets[-1] is not None and not math.isfinite(targets[-1]):
                        raise ValueError("experience_nonfinite_target")
                    maturity = (row["open_s"] + (horizon + 1) * BAR) * NS
                    status.append(
                        "observed"
                        if valid
                        else "pending"
                        if maturity > cutoff
                        else "missing_future"
                    )
                    label_times.append(
                        max(r["available_ns"] for r in [row, *needed]) if valid else None
                    )
                    target_pins.append([r["capture_sha"] for r in [row, *needed]] if valid else [])
                yield dict(
                    id=f"{pool}:{row['open_s']}",
                    pool=pool,
                    origin_s=row["open_s"] + BAR,
                    input_start_s=inputs[0][0],
                    input_available_ns=max(d[1] for d in inputs),
                    input_dependencies=[list(d) for d in inputs],
                    values=[f[0] for f in frames],
                    targets=targets,
                    target_status=status,
                    target_available_ns=label_times,
                    target_captures=target_pins,
                )
            next_row = next(stream, None)  # noqa: B031 -- deliberately consume one shared iterator
            if next_row is not None:
                future.append(next_row)


def split_plan(plan):
    required = {"train_start_s", "train_end_s", "validation_end_s", "test_end_s"}
    if set(plan) != required or any(type(plan[k]) is not int for k in required):
        raise ValueError("experience_split_schema")
    if (
        not 0
        <= plan["train_start_s"]
        < plan["train_end_s"]
        < plan["validation_end_s"]
        < plan["test_end_s"]
    ):
        raise ValueError("experience_split_order")
    return plan


def membership(row, plan):
    """Purge full input dependencies; retain independently valid horizon labels."""
    for role, start, end in (
        ("train", plan["train_start_s"], plan["train_end_s"]),
        ("validation", plan["train_end_s"], plan["validation_end_s"]),
        ("test", plan["validation_end_s"], plan["test_end_s"]),
    ):
        if start <= row["origin_s"] < end:
            if row["input_start_s"] < start:
                return "purged", [False] * len(HORIZONS)
            mask = [
                status == "observed" and row["origin_s"] + h * BAR < end
                for h, status in zip(HORIZONS, row["target_status"], strict=True)
            ]
            return role, mask
    return "outside", [False] * len(HORIZONS)


def contract(context):
    return dict(
        schema=SCHEMA,
        feature_names=list(FEATURES),
        context=context,
        feature_history=8,
        horizons=list(HORIZONS),
        bar_seconds=BAR,
        historical_online_availability_known=False,
        target="origin_close_log_return_no_execution",
        partial_labels=True,
    )
