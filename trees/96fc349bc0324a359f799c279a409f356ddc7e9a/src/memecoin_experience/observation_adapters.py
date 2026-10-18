"""Qualified legacy bar lane -> generic observations; no invented completeness or deaths."""

import itertools
import json
import math
from pathlib import Path

from . import continuous, observations
from .numerics import log_ratio
from .storage import file_hash, verify_files


def from_continuous(reference, store, *, role="train", lifecycle=(), chunk=4096):
    path = Path(reference["path"])
    meta = json.loads((path / "meta/source.json").read_text())
    plan = continuous.source_plan(meta["plan"])
    if plan["role"] != role:
        raise ValueError("observation_reserved_role")
    verify_files(path / "meta", reference["pin"])
    cutoff = plan["cutoff_ns"]
    stream = "qualified_price_bar"
    schema = dict(
        version="market-observations-v1",
        role=role,
        availability="historical_replay",
        streams={
            stream: dict(
                process="qualified_provider_bar_closes; missing bars do not certify inactivity",
                features=[
                    dict(name=n, unit=u, modality="ohlcv")
                    for n, u in (
                        ("close_usd", "USD"),
                        ("log_high_low", "log_ratio"),
                        ("log_close_open", "log_ratio"),
                        ("log_volume_usd", "log1p_USD"),
                    )
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
                    yield dict(
                        kind="event",
                        entity=entity,
                        stream=stream,
                        id=str(bar["open_s"]),
                        event_ns=at,
                        available_ns=bar["available_ns"],
                        evidence=[bar["capture_sha"]],
                        values=[c, log_ratio(h, low), log_ratio(c, o), math.log1p(v)],
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

    return observations.publish(
        store,
        rows(),
        schema=schema,
        cutoff=cutoff,
        sources=[
            reference,
            dict(
                producer="continuous-observation-adapter-v1",
                implementation=file_hash(Path(__file__)),
                numerics=file_hash(Path(__file__).with_name("numerics.py")),
            ),
        ],
        chunk=chunk,
    )
