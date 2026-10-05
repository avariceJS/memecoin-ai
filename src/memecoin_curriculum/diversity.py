"""Offline support audit. Concentration and overlap are not statistical independence."""

import math
from collections import Counter, defaultdict

from memecoin_registry.contracts import digest


def bucket(value, boundaries):
    if value is None or not math.isfinite(value):
        return "unknown"
    return str(sum(value >= edge for edge in boundaries))


def compact(bundle, samples, births=None):
    births = births or {}
    result = []
    for sample in samples:
        frame = bundle["frames"][sample["history"][-1]]
        birth = births.get(sample["pool"])
        age = None if birth is None else sample["event_close_s"] - birth / 10**9
        result.append(
            dict(
                id=sample["id"],
                pool=sample["pool"],
                close=sample["event_close_s"],
                available=sample["actual_available_ns"],
                maturity=sample["maturity_s"],
                age=bucket(age, [0, 86400, 7 * 86400, 30 * 86400, 90 * 86400]),
                volatility=bucket(frame["values"][9], [0.005, 0.02, 0.1]),
                volume=bucket(
                    frame["values"][3], [math.log1p(x) for x in [100, 1000, 10000, 100000]]
                ),
                feature_sha=digest([bundle["frames"][k]["values"] for k in sample["history"]]),
                returns=[t["log_return"] for t in sample["targets"]],
            )
        )
    return result


def correlation(pairs):
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs, strict=True)
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    xx = sum((x - mx) ** 2 for x in xs)
    yy = sum((y - my) ** 2 for y in ys)
    return sum((x - mx) * (y - my) for x, y in pairs) / math.sqrt(xx * yy) if xx * yy else None


def support(rows):
    counts = Counter(r["pool"] for r in rows)
    n = len(rows)
    by_pool = defaultdict(list)
    for r in rows:
        by_pool[r["pool"]].append(r)
    adjacent = 0
    nonoverlap = 0
    correlations = {}
    for pool, part in sorted(by_pool.items()):
        part.sort(key=lambda r: r["close"])
        previous_end = -math.inf
        for r in part:
            if r["close"] - 8 * 300 >= previous_end:
                nonoverlap += 1
                previous_end = r["maturity"]
        pairs = [
            (a, b)
            for a, b in zip(part[:-1], part[1:], strict=True)
            if b["close"] - a["close"] == 300
        ]
        adjacent += len(pairs)
        correlations[pool] = {
            str(h): dict(
                pairs=len(pairs),
                pearson=correlation([(a["returns"][j], b["returns"][j]) for a, b in pairs]),
            )
            for j, h in enumerate([5, 15])
        }
    return dict(
        decisions=n,
        pools=len(counts),
        per_pool=dict(sorted(counts.items())),
        largest_pool_share=max(counts.values(), default=0) / n if n else None,
        effective_pool_count=n * n / sum(v * v for v in counts.values()) if n else 0,
        utc_hours=len({r["close"] // 3600 for r in rows}),
        utc_days=len({r["close"] // 86400 for r in rows}),
        pool_days=len({(r["pool"], r["close"] // 86400) for r in rows}),
        age_buckets=dict(Counter(r["age"] for r in rows)),
        volatility_buckets=dict(Counter(r["volatility"] for r in rows)),
        volume_buckets=dict(Counter(r["volume"] for r in rows)),
        adjacent_decisions=adjacent,
        adjacent_fraction=adjacent / n if n else None,
        disjoint_context_and_target_windows=nonoverlap,
        lag1_return_correlation=correlations,
        independent_sample_size=None,
    )


def delta(old, new):
    old_ids = {r["id"] for r in old}
    new_ids = {r["id"] for r in new}
    added = [r for r in new if r["id"] not in old_ids]
    pools = {r["pool"] for r in old}
    days = {(r["pool"], r["close"] // 86400) for r in old}
    cells = {(r["pool"], r["age"], r["volatility"], r["volume"]) for r in old}
    features = {r["feature_sha"] for r in old}
    return dict(
        added=support(added),
        removed=len(old_ids - new_ids),
        retained=len(old_ids & new_ids),
        new_pools=sorted({r["pool"] for r in added} - pools),
        new_pool_days=len({(r["pool"], r["close"] // 86400) for r in added} - days),
        familiar_pool_rows=sum(r["pool"] in pools for r in added),
        familiar_pool_day_rows=sum((r["pool"], r["close"] // 86400) in days for r in added),
        familiar_regime_cell_rows=sum(
            (r["pool"], r["age"], r["volatility"], r["volume"]) in cells for r in added
        ),
        exact_feature_duplicate_rows=sum(r["feature_sha"] in features for r in added),
        membership_sha=digest(sorted(r["id"] for r in added)),
        near_duplicate_fraction=None,
        note="Familiar regimes are support proxies, not proof of redundant information.",
    )
