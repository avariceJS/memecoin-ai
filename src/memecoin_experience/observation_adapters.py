"""Qualified legacy bar lane -> generic observations; no invented completeness or deaths."""

import itertools
import json
import math
from collections import deque
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
_CONTEXT_FEATURES = (
    ("prior_close_log_return", "log_ratio"),
    ("recent_close_position", "fraction_of_observed_range"),
    ("context_count", "observed_bar_count"),
    ("context_span_s", "seconds"),
    ("context_max_gap_s", "seconds"),
    ("context_contiguous", "indicator"),
    ("context_ready", "indicator"),
    ("context_zero_range", "indicator"),
)
_VOLUME_FEATURES = (
    ("prior_volume_count", "observed_bar_count"),
    ("volume_log1p_delta", "log1p_USD_difference"),
)
_CONTEXT_PARAMETERS = dict(
    bar_seconds=300,
    max_count=12,
    max_span_s=3600,
    min_count=2,
    ready_count=12,
)
_VOLUME_PARAMETERS = dict(_CONTEXT_PARAMETERS, min_prior_volume_count=2)
_CONTEXT_RULES = (
    "At each current open, retain at most 12 observed bars in the same pool and same "
    "original selected half-open enrollment interval with open time in [current-3600, "
    "current]; reset at every original interval boundary, including touching intervals; "
    "evict by time then count; retain gaps without filling. prior_close_log_return is "
    "stable log(current.close / immediately preceding retained close) only when the "
    "preceding open is exactly 300 seconds earlier, otherwise missing. For at least two "
    "bars, range bounds are min(low) and max(high), including current; position is "
    "(current.close-min(low))/(max(high)-min(low)) when range is positive, otherwise "
    "missing with zero_range=1. With fewer than two bars position and zero_range are "
    "missing. count is retained observed count; span is latest minus earliest open, "
    "zero for a singleton; max gap is missing for a singleton and otherwise the largest "
    "consecutive open-time gap. contiguous is missing for a singleton and otherwise "
    "one iff every gap is 300 seconds. ready is one iff count is 12 and contiguous. "
    "Event availability is the maximum receipt availability of every retained "
    "contributor; evidence is the deterministic unique capture hashes of all contributors."
)
_VOLUME_RULES = (
    "prior_volume_count is retained observed contributor count excluding current. "
    "volume_log1p_delta is log1p(current.volume_usd) minus the arithmetic mean of "
    "log1p(volume_usd) for all strictly preceding retained contributors, defined only "
    "with at least two priors and every consecutive gap through current exactly 300 "
    "seconds; otherwise missing. Zero volume is valid; this log1p USD difference is "
    "not an exact raw volume ratio."
)


def _bounded_position(close, low, high):
    """Compute a bounded range fraction without overflowing on finite extremes."""
    width = high - low
    if math.isfinite(width) and width > 0:
        position = (close - low) / width
    else:
        scale = max(abs(close), abs(low), abs(high))
        position = (close / scale - low / scale) / (high / scale - low / scale)
    return min(1.0, max(0.0, position))


def from_continuous(
    reference, store, *, role="train", lifecycle=(), chunk=4096, profile="legacy-v1"
):
    """Publish qualified bars with the compatible legacy or opt-in morphology profile.

    The default and explicit ``legacy-v1`` preserve the original four-feature stream.
    ``candle-morphology-v1`` appends normalized candle structure, and
    ``candle-context-v1`` appends bounded, receipt-qualified context to that layout.
    """
    path = Path(reference["path"])
    meta = json.loads((path / "meta/source.json").read_text())
    plan = continuous.source_plan(meta["plan"])
    if plan["role"] != role:
        raise ValueError("observation_reserved_role")
    if not isinstance(profile, str) or profile not in (
        "legacy-v1", "candle-morphology-v1", "candle-context-v1",
        "candle-volume-context-v1",
    ):
        raise ValueError("observation_profile")
    morphology = profile in (
        "candle-morphology-v1", "candle-context-v1", "candle-volume-context-v1"
    )
    context = profile in ("candle-context-v1", "candle-volume-context-v1")
    volume_context = profile == "candle-volume-context-v1"
    verify_files(path / "meta", reference["pin"])
    cutoff = plan["cutoff_ns"]
    stream = "qualified_price_bar"
    feature_definitions = _LEGACY_FEATURES + (_MORPHOLOGY_FEATURES if morphology else ())
    if context:
        feature_definitions += _CONTEXT_FEATURES
    if volume_context:
        feature_definitions += _VOLUME_FEATURES
    process = "qualified_provider_bar_closes; missing bars do not certify inactivity"
    if morphology:
        process += "; candle-morphology-v1: " + _MORPHOLOGY_RULES
    if context:
        process += "; candle-context-v1: " + _CONTEXT_RULES
    if volume_context:
        process += "; candle-volume-context-v1: " + _VOLUME_RULES
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
                history = deque()
                active_interval = None
                for bar in current[1]:
                    interval_index = next(
                        (
                            i
                            for i, (start, end) in enumerate(spans)
                            if start <= bar["open_s"] < end
                        ),
                        None,
                    )
                    if interval_index is None:
                        raise ValueError("observation_unselected_event")
                    if interval_index != active_interval:
                        history.clear()
                        active_interval = interval_index
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
                    if context:
                        history.append(bar)
                        while bar["open_s"] - history[0]["open_s"] > _CONTEXT_PARAMETERS[
                            "max_span_s"
                        ]:
                            history.popleft()
                        while len(history) > _CONTEXT_PARAMETERS["max_count"]:
                            history.popleft()
                        contributors = list(history)
                        count = len(contributors)
                        opens = [item["open_s"] for item in contributors]
                        gaps = [b - a for a, b in zip(opens[:-1], opens[1:], strict=True)]
                        prior_return = (
                            log_ratio(c, contributors[-2]["ohlcv"][3])
                            if len(contributors) >= 2 and gaps[-1] == 300
                            else None
                        )
                        if count >= _CONTEXT_PARAMETERS["min_count"]:
                            range_low = min(item["ohlcv"][2] for item in contributors)
                            range_high = max(item["ohlcv"][1] for item in contributors)
                            if range_high == range_low:
                                position, zero_range = None, 1.0
                            else:
                                position = _bounded_position(c, range_low, range_high)
                                zero_range = 0.0
                            contiguous = float(
                                all(gap == _CONTEXT_PARAMETERS["bar_seconds"] for gap in gaps)
                            )
                        else:
                            position, zero_range, contiguous = None, None, None
                        ready = float(
                            count == _CONTEXT_PARAMETERS["ready_count"] and contiguous == 1.0
                        )
                        values.extend(
                            (
                                prior_return,
                                position,
                                count,
                                opens[-1] - opens[0],
                                max(gaps) if gaps else None,
                                contiguous,
                                ready,
                                zero_range,
                            )
                        )
                        if volume_context:
                            priors = contributors[:-1]
                            prior_count = len(priors)
                            volume_delta = None
                            if (
                                prior_count >= _VOLUME_PARAMETERS["min_prior_volume_count"]
                                and all(
                                    gap == _CONTEXT_PARAMETERS["bar_seconds"] for gap in gaps
                                )
                            ):
                                prior_log_mean = math.fsum(
                                    math.log1p(item["ohlcv"][4]) for item in priors
                                ) / prior_count
                                volume_delta = math.log1p(v) - prior_log_mean
                            values.extend((prior_count, volume_delta))
                    yield dict(
                        kind="event",
                        entity=entity,
                        stream=stream,
                        id=str(bar["open_s"]),
                        event_ns=at,
                        available_ns=(
                            max(item["available_ns"] for item in history)
                            if context
                            else bar["available_ns"]
                        ),
                        evidence=(
                            sorted({item["capture_sha"] for item in history})
                            if context
                            else [bar["capture_sha"]]
                        ),
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
    if context and not volume_context:
        producer = dict(
            producer="continuous-observation-adapter-v3",
            implementation=producer["implementation"],
            numerics=producer["numerics"],
            profile=dict(
                name="candle-context-v1",
                version=1,
                features=[
                    dict(name=n, unit=u, modality="ohlcv") for n, u in feature_definitions
                ],
                parameters=_CONTEXT_PARAMETERS,
                revision_selection=continuous.NAMESPACE,
                formulas=dict(
                    prior_close_log_return=(
                        "log(current.close / immediately_preceding_retained.close) only "
                        "when immediately_preceding_retained.open == current.open-300s"
                    ),
                    recent_close_position=(
                        "(current.close-min(contributor.low))/(max(contributor.high)-"
                        "min(contributor.low)) when range positive"
                    ),
                    context_count="retained observed contributor count including current",
                    context_span_s="latest contributor open minus earliest contributor open",
                    context_max_gap_s="maximum consecutive contributor open-time difference; missing for singleton",
                    context_contiguous="one iff count>=2 and all consecutive gaps equal 300s; missing for singleton",
                    context_ready="one iff count=12 and context_contiguous=1, otherwise zero",
                    context_zero_range="one iff count>=2 and max(high)==min(low); missing below count two",
                ),
                morphology_rules=_MORPHOLOGY_RULES,
                rules=_CONTEXT_RULES,
                identity=dict(
                    source_pin=reference["pin"],
                    cutoff_ns=cutoff,
                    implementation_sha256=file_hash(Path(__file__)),
                    numerics_sha256=file_hash(Path(__file__).with_name("numerics.py")),
                ),
            ),
        )
    if volume_context:
        producer = dict(
            producer="continuous-observation-adapter-v4",
            implementation=producer["implementation"],
            numerics=producer["numerics"],
            profile=dict(
                name="candle-volume-context-v1",
                version=1,
                features=[
                    dict(name=n, unit=u, modality="ohlcv") for n, u in feature_definitions
                ],
                parameters=_VOLUME_PARAMETERS,
                revision_selection=continuous.NAMESPACE,
                formulas=dict(
                    prior_close_log_return=(
                        "log(current.close / immediately_preceding_retained.close) only "
                        "when immediately_preceding_retained.open == current.open-300s"
                    ),
                    recent_close_position=(
                        "(current.close-min(contributor.low))/(max(contributor.high)-"
                        "min(contributor.low)) when range positive"
                    ),
                    context_count="retained observed contributor count including current",
                    context_span_s="latest contributor open minus earliest contributor open",
                    context_max_gap_s="maximum consecutive contributor open-time difference; missing for singleton",
                    context_contiguous="one iff count>=2 and all consecutive gaps equal 300s; missing for singleton",
                    context_ready="one iff count=12 and context_contiguous=1, otherwise zero",
                    context_zero_range="one iff count>=2 and max(high)==min(low); missing below count two",
                    prior_volume_count="retained observed contributor count excluding current",
                    volume_log1p_delta=(
                        "log1p(current.volume_usd) - mean(log1p(prior.volume_usd)); "
                        "missing unless prior_volume_count>=2 and all consecutive gaps "
                        "through current equal 300s"
                    ),
                ),
                morphology_rules=_MORPHOLOGY_RULES,
                rules=_CONTEXT_RULES + " " + _VOLUME_RULES,
                identity=dict(
                    source_pin=reference["pin"],
                    cutoff_ns=cutoff,
                    implementation_sha256=file_hash(Path(__file__)),
                    numerics_sha256=file_hash(Path(__file__).with_name("numerics.py")),
                ),
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
