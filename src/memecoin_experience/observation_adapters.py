"""Qualified legacy bar lane -> generic observations; no invented completeness or deaths."""

import itertools
import json
import math
from pathlib import Path

from . import continuous, observations
from .numerics import log_ratio
from .storage import file_hash, verify_files


_LEGACY_FEATURES = (
    ("close_usd", "USD"),
    ("log_high_low", "log_ratio"),
    ("log_close_open", "log_ratio"),
    ("log_volume_usd", "log1p_USD"),
)
_MORPHOLOGY_FEATURES = (
    ("upper_wick_fraction", "fraction_of_range"),
    ("lower_wick_fraction", "fraction_of_range"),
    ("signed_body_fraction", "signed_fraction_of_range"),
    ("close_position", "fraction_of_range"),
    ("zero_range", "indicator"),
)
_MORPHOLOGY_RULES = (
    "For R=high-low>0: upper_wick_fraction=(high-max(open,close))/R; "
    "lower_wick_fraction=(min(open,close)-low)/R; "
    "signed_body_fraction=(close-open)/R; close_position=(close-low)/R; "
    "zero_range=0. For R=0: upper_wick_fraction=0, lower_wick_fraction=0, "
    "signed_body_fraction=0, close_position=None, zero_range=1."
)


def from_continuous(
    reference, store, *, role="train", lifecycle=(), chunk=4096, profile="legacy-v1"
):
    """Publish qualified bars with the compatible legacy or opt-in morphology profile.

    The default and explicit ``legacy-v1`` preserve the original four-feature stream.
    ``candle-morphology-v1`` appends normalized candle structure while representing a
    zero-range candle's undefined close placement as missing, never as an observed zero.
    """
    path = Path(reference["path"])
    meta = json.loads((path / "meta/source.json").read_text())
    plan = continuous.source_plan(meta["plan"])
    if plan["role"] != role:
        raise ValueError("observation_reserved_role")
    if not isinstance(profile, str) or profile not in ("legacy-v1", "candle-morphology-v1"):
        raise ValueError("observation_profile")
    morphology = profile == "candle-morphology-v1"
    verify_files(path / "meta", reference["pin"])
    cutoff = plan["cutoff_ns"]
    stream = "qualified_price_bar"
    feature_definitions = _LEGACY_FEATURES + (_MORPHOLOGY_FEATURES if morphology else ())
    process = "qualified_provider_bar_closes; missing bars do not certify inactivity"
    if morphology:
        process += "; candle-morphology-v1: " + _MORPHOLOGY_RULES
    schema = dict(
        version="market-observations-v1",
        role=role,
        availability="historical_replay",
        streams={
            stream: dict(
                process=process,
                features=[
                    dict(name=n, unit=u, modality="ohlcv")
                    for n, u in feature_definitions
                ],
            )
        },
    )
    facts = iter(lifecycle)
    life = next(facts, None)
    groups = iter(
        itertools.groupby(continuous.source(reference, cutoff, role=role), key=lambda r: r["pool"])
    )
    current = next(groups, None)

    def rows():
        nonlocal current, life
        for pool in sorted(p["pool"] for p in plan["pools"]):
            entity = "solana/pool/" + pool
            spans = plan.get("intervals", {}).get(pool, [[plan["start_s"], plan["end_s"]]])
            if not spans:
                continue
            starts = [a * 10**9 for a, _ in spans]
            ends = [b * 10**9 + 1 for _, b in spans]
            # Lifecycle is an explicitly supplied qualified fact, never inferred from silence.
            member_start = min(starts)
            if life and life["entity"] < entity:
                raise ValueError("observation_unenrolled_lifecycle")
            lifecycle_rows = []
            while life and life["entity"] == entity:
                lifecycle_rows.append(life)
                member_start = min(member_start, life["event_ns"])
                life = next(facts, None)
            yield dict(
                kind="enrollment",
                entity=entity,
                start_ns=member_start,
                end_ns=max(ends),
                available_ns=cutoff,
                evidence=[reference["pin"]],
            )
            if current and current[0] < pool:
                raise ValueError("observation_unenrolled_event")
            if current and current[0] == pool:
                for bar in current[1]:
                    o, h, low, c, v = bar["ohlcv"]
                    at = (bar["open_s"] + 300) * 10**9
                    values = [c, log_ratio(h, low), log_ratio(c, o), math.log1p(v)]
                    if morphology:
                        candle_range = h - low
                        if candle_range == 0:
                            values.extend((0.0, 0.0, 0.0, None, 1.0))
                        else:
                            values.extend(
                                (
                                    (h - max(o, c)) / candle_range,
                                    (min(o, c) - low) / candle_range,
                                    (c - o) / candle_range,
                                    (c - low) / candle_range,
                                    0.0,
                                )
                            )
                    yield dict(
                        kind="event",
                        entity=entity,
                        stream=stream,
                        id=str(bar["open_s"]),
                        event_ns=at,
                        available_ns=bar["available_ns"],
                        evidence=[bar["capture_sha"]],
                        values=values,
                    )
                current = next(groups, None)
            yield from lifecycle_rows
            # Disjoint original windows can share their close-boundary nanosecond; union them.
            merged = []
            for a, b in zip(starts, ends, strict=True):
                if merged and a <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], b)
                else:
                    merged.append([a, b])
            for a, b in merged:
                yield dict(
                    kind="support",
                    entity=entity,
                    stream=stream,
                    start_ns=a,
                    end_ns=b,
                    available_ns=cutoff,
                    status="unknown",
                    evidence=[reference["pin"]],
                )
        if current or life:
            raise ValueError("observation_unenrolled_record")

    producer = dict(
        producer="continuous-observation-adapter-v1",
        implementation=file_hash(Path(__file__)),
        numerics=file_hash(Path(__file__).with_name("numerics.py")),
    )
    if morphology:
        producer = dict(
            producer="continuous-observation-adapter-v2",
            implementation=producer["implementation"],
            numerics=producer["numerics"],
            profile=dict(
                name="candle-morphology-v1",
                version=1,
                features=[
                    dict(name=n, unit=u, modality="ohlcv") for n, u in _MORPHOLOGY_FEATURES
                ],
                rules=_MORPHOLOGY_RULES,
            ),
        )
    return observations.publish(
        store,
        rows(),
        schema=schema,
        cutoff=cutoff,
        sources=[
            reference,
            producer,
        ],
        chunk=chunk,
    )
