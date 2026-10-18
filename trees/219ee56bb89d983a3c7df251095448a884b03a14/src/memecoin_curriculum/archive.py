"""Measured archive-to-training funnel and causal, optional bar-path supervision."""

import json
import math
from collections import Counter, defaultdict

from memecoin_brain.data import BAR, CONTEXT, build, fingerprint, split
from memecoin_registry.contracts import digest


def summary(rows):
    counts = Counter(r["pool"] for r in rows)
    n = len(rows)
    return dict(
        bars=n,
        pools=len(counts),
        per_pool=dict(counts),
        first_open_s=min((r["open_s"] for r in rows), default=None),
        last_open_s=max((r["open_s"] for r in rows), default=None),
        event_days=len({r["open_s"] // 86400 for r in rows}),
        receipt_days=len({r["available_ns"] // (86400 * 10**9) for r in rows}),
        largest_pool_fraction=max(counts.values(), default=0) / n if n else None,
        effective_pool_count=1 / sum((v / n) ** 2 for v in counts.values()) if n else 0,
    )


def rolling(rows, cutoff):
    # Match deployed source_rows(training=True) and service.training without changing it.
    selected = [r for r in rows if r["open_s"] >= cutoff // 10**9 - 7 * 86400]
    if not selected:
        return selected
    latest = max(r["open_s"] for r in selected)
    selected = [r for r in selected if r["open_s"] >= latest - 7 * 86400]
    if len(selected) > 28000:
        floor = sorted(r["open_s"] for r in selected)[-28000]
        selected = [r for r in selected if r["open_s"] >= floor]
    return selected


def funnel(rows, provenance, bundle=None):
    bundle = build(rows, provenance) if bundle is None else bundle
    eligible = [
        s for s in bundle["samples"] if all(t["log_return"] is not None for t in s["targets"])
    ]
    parts, bounds = split(bundle)
    support = {}
    for name, part in parts.items():
        c = Counter(s["pool"] for s in part)
        support[name] = dict(
            samples=len(part),
            per_pool=dict(c),
            pools=len(c),
            event_days=len({s["event_close_s"] // 86400 for s in part}),
        )
    return bundle, dict(
        raw=summary(rows),
        contiguous_context_decisions=len(bundle["samples"]),
        complete_label_decisions=len(eligible),
        excluded_context=len(rows) - len(bundle["samples"]),
        excluded_incomplete_targets=len(bundle["samples"]) - len(eligible),
        global_split=bounds,
        partitions=support,
        dataset_fingerprint=fingerprint(bundle),
    )


def path_labels(rows, as_of_ns, horizons=(1, 3)):
    """No absent bar is zero-filled, and no close excursion pretends to be intrabar execution."""
    by_pool = defaultdict(dict)
    for r in rows:
        if r["available_ns"] > as_of_ns or (r["open_s"] + BAR) * 10**9 > r["available_ns"]:
            raise ValueError("archive_availability")
        if r["open_s"] in by_pool[r["pool"]]:
            raise ValueError("duplicate_bar_identity")
        by_pool[r["pool"]][r["open_s"]] = r
    for pool, bars in sorted(by_pool.items()):
        for t, origin in sorted(bars.items()):
            context = [bars.get(t - k * BAR) for k in range(CONTEXT - 1, -1, -1)]
            if not all(context):
                continue
            for h in horizons:
                future = [bars.get(t + k * BAR) for k in range(1, h + 1)]
                valid = all(future)
                values = None
                if valid:
                    closes = [origin["ohlcv"][3], *[b["ohlcv"][3] for b in future]]
                    changes = [
                        math.log(b / a) for a, b in zip(closes[:-1], closes[1:], strict=True)
                    ]
                    values = dict(
                        log_return=math.log(closes[-1] / closes[0]),
                        realized_variance=sum(v * v for v in changes),
                        downside_from_origin_close=min(
                            0.0, *[math.log(v / closes[0]) for v in closes[1:]]
                        ),
                        log_future_dollar_volume=math.log1p(sum(b["ohlcv"][4] for b in future)),
                    )
                yield dict(
                    pool=pool,
                    event_close_s=t + BAR,
                    horizon=h,
                    input_available_ns=max(b["available_ns"] for b in context),
                    target_available_ns=max(b["available_ns"] for b in future) if valid else None,
                    values=values,
                    status="matured" if valid else "missing_future_bars",
                    input_captures=[b["capture_sha"] for b in context],
                    target_captures=[b["capture_sha"] for b in future] if valid else [],
                    execution_target=False,
                    historical_online_availability_known=False,
                )


def catalogue(db):
    """Coverage inventory; missing intervals do not establish dead/no-trade markets."""
    grouped = defaultdict(list)
    for pool, t in db.execute("select pool,open_s from firsts order by pool,open_s"):
        grouped[pool].append(t)
    result = []
    for pool, times in grouped.items():
        spans = []
        start = previous = times[0]
        for t in times[1:]:
            if t != previous + BAR:
                spans.append([start, previous + BAR])
                start = t
            previous = t
        spans.append([start, previous + BAR])
        result.append(
            dict(
                pool=pool,
                observed_intervals_s=spans,
                bars=len(times),
                history_complete=False,
                namespace="geckoterminal/usd/base/5min/first-observed",
            )
        )
    return result


def revision_audit(db):
    altered, closes, pools = set(), set(), Counter()
    for pool, t, original, version in db.execute(
        "select f.pool,f.open_s,f.payload,v.payload from firsts f join versions v "
        "on f.pool=v.pool and f.open_s=v.open_s where f.pin!=v.pin"
    ):
        a, b = json.loads(original)["ohlcv"], json.loads(version)["ohlcv"]
        if a != b:
            altered.add((pool, t))
            pools[pool] += 1
        if a[3] != b[3]:
            closes.add((pool, t))
    return dict(
        changed_keys=len(altered),
        changed_close_keys=len(closes),
        changed_observations_per_pool=dict(pools),
        fingerprint=digest(sorted(altered)),
    )


def shards(rows, provenance, chunk=4096):
    """Overlap covers both feature history of every context frame and future labels."""
    if not 1 <= chunk <= 4096:
        raise ValueError("archive_chunk_bound")
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["pool"]].append(row)
    for _pool, history in sorted(grouped.items()):
        history.sort(key=lambda r: r["open_s"])
        if len({r["open_s"] for r in history}) != len(history):
            raise ValueError("duplicate_bar_identity")
        for start in range(0, len(history), chunk):
            origins = {r["open_s"] + BAR for r in history[start : start + chunk]}
            part = build(history[max(0, start - 2 * (CONTEXT - 1)) : start + chunk + 3], provenance)
            part["samples"] = [s for s in part["samples"] if s["event_close_s"] in origins]
            keys = {k for s in part["samples"] for k in s["history"]}
            part["frames"] = {k: v for k, v in part["frames"].items() if k in keys}
            yield part


def assemble(parts, max_decisions=100000):
    """Bounded compatibility adapter; large archives remain sharded, never silently truncated."""
    result, seen = None, set()
    for part in parts:
        if result is None:
            result = dict(part, frames={}, samples=[])
        if (
            part["provenance"] != result["provenance"]
            or part["feature_names"] != result["feature_names"]
        ):
            raise ValueError("archive_contract_mismatch")
        for sample in part["samples"]:
            if sample["id"] in seen:
                raise ValueError("archive_duplicate_sample")
            seen.add(sample["id"])
        if len(seen) > max_decisions:
            raise ValueError("archive_materialization_capacity")
        for k, v in part["frames"].items():
            if k in result["frames"] and result["frames"][k] != v:
                raise ValueError("archive_context_mismatch")
            result["frames"][k] = v
        result["samples"].extend(part["samples"])
    if result is None:
        raise ValueError("empty_archive")
    return result
