"""Paired curriculum diagnostics; few calendar days do not manufacture confidence."""

import random
from collections import Counter, defaultdict

from memecoin_runner.scoring import quantile


def paired(records, control, challenger, *, draws=500):
    if not records:
        return dict(variants=0, days=0, relative_gain=None, ci=None)

    def metric(name, weights):
        pools = defaultdict(lambda: [0.0, 0.0])
        for r in records:
            w = weights[r["day"]]
            pools[r["pool"]][0] += w * r["losses"][name]
            pools[r["pool"]][1] += w
        values = [a / n for a, n in pools.values() if n]
        return sum(values) / len(values)

    days = sorted({r["day"] for r in records})
    weights = dict.fromkeys(days, 1)
    a, b = metric(control, weights), metric(challenger, weights)
    out = dict(
        variants=len(records),
        pools=len({r["pool"] for r in records}),
        days=len(days),
        control=a,
        challenger=b,
        relative_gain=1 - b / a if a > 0 else None,
        ci=None,
        inference="descriptive_only_fewer_than5UTCdays",
        overlapping_horizons=True,
    )
    if len(days) >= 5 and a > 0:
        rng = random.Random(7)
        gains = []
        for _ in range(draws):
            weight = Counter(rng.choices(days, k=len(days)))
            base = metric(control, weight)
            if base > 0:
                gains.append(1 - metric(challenger, weight) / base)
        out.update(
            ci=[quantile(gains, 0.025), quantile(gains, 0.975)],
            inference="exploratory_paired_UTC_day_bootstrap",
            draws=draws,
        )
    return out
