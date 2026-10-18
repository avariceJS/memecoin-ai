"""Frozen operational gate from actual wall-clock samples and immutable request receipts."""

from collections import defaultdict

from memecoin_ml.data import read

from .hot import NS
from .store import connect


def assess(root, samples, started):
    c = read(root / "config.json")
    end = samples[-1]["at_ns"]
    eligible = [s for s in samples if s["at_ns"] >= started + 180 * NS]
    # Missing wall time is not a good sample. Coverage is independently bounded.
    sample_gaps = [
        (b["at_ns"] - a["at_ns"]) / NS for a, b in zip(eligible, eligible[1:], strict=False)
    ]
    one = sum(len(s["fresh"]) >= 1 for s in eligible) / max(1, len(eligible))
    four = sum(len(s["fresh"]) >= 4 for s in eligible) / max(1, len(eligible))
    starts = defaultdict(list)
    lanes = defaultdict(int)
    failures = []
    for cap in sorted((root / "requests").iterdir()):
        req = read(cap / "request.json")
        pool = req["url"].split("/pools/")[1].split("/")[0]
        success = (cap / "receipt.json").exists()
        lanes[req["lane"] + ("_success" if success else "_failure")] += 1
        if not success:
            failures.append(
                read(cap / "failure.json")
                if (cap / "failure.json").exists()
                else dict(category="reserved_without_receipt")
            )
        if pool in c["hot_pools"] and req["lane"] == "prospective":
            starts[pool].append(req["started_ns"])
    gaps = {}
    for p in c["hot_pools"]:
        ts = [started, *sorted(starts[p]), end]
        gaps[p] = max((b - a) / NS for a, b in zip(ts, ts[1:], strict=False))
    with connect(root / "index.sqlite") as db:
        new = {
            p: db.execute(
                "select count(distinct open_s) from versions where pool=? "
                "and available>=? and available<=? and (open_s+300)*1000000000>?",
                (p, started, end, started),
            ).fetchone()[0]
            for p in c["hot_pools"]
        }
    health = read(root / "health.json")
    success = sum(n for k, n in lanes.items() if k.endswith("_success"))
    attempts = sum(lanes.values())
    gates = dict(
        samples=len(eligible) >= 90,
        span=bool(eligible) and eligible[-1]["at_ns"] - eligible[0]["at_ns"] >= 900 * NS,
        sample_continuity=bool(sample_gaps) and max(sample_gaps) <= 30,
        one_fresh_fraction=one >= 0.80,
        four_fresh_fraction=four >= 0.60,
        new_closed_candles=all(n >= 3 for n in new.values()),
        hot_request_gaps=all(g <= 180 for g in gaps.values()),
        success_rate=success / max(1, attempts) >= 0.95,
        hot_active=all(health["pools"][p]["status"] == "active" for p in c["hot_pools"]),
        background_observed=lanes["historical_success"] > 0,
        no_stop=not (root / "STOP").exists(),
        bounded=attempts <= 120,
    )
    return dict(
        approved=all(gates.values()),
        gates=gates,
        start_ns=started,
        cutoff_ns=end,
        samples=len(eligible),
        one_fresh_fraction=one,
        four_fresh_fraction=four,
        sample_max_gap_s=max(sample_gaps, default=0),
        new_candles=new,
        max_hot_request_gap_s=gaps,
        requests=attempts,
        lanes=dict(lanes),
        failures=failures,
    )
