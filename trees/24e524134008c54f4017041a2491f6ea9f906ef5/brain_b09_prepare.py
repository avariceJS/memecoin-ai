"""Seed B09 without network; reuse immutable B04/B08 captures and provider failure history."""

import copy
import time
from pathlib import Path

from memecoin_continuous.runtime import stage_source
from memecoin_continuous.source import state
from memecoin_continuous.store import audit, connect, ingest, stats
from memecoin_ml.data import read, sha
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
OLD = BASE / "work/brain-b08/source01"
ROOT = BASE / "work/brain-b09/source01"


def main():
    s = state(OLD)
    universe = read(OLD / "universe.json")
    hot = [
        p["pool"]
        for p in sorted(
            universe["pools"], key=lambda p: (-p["liquidity_at_discovery_usd"], p["pool"])
        )
        if s["pools"][p["pool"]]["status"] == "active"
    ][:6]
    now = time.time_ns()
    ROOT.mkdir(parents=True)
    (ROOT / "requests").mkdir()
    publish(ROOT / "universe.json", universe)
    publish(
        ROOT / "config.json",
        dict(
            created_ns=now,
            expires_ns=now + 3 * 86400 * 10**9,
            max_requests=25000,
            qualification_expires_ns=now + 1500 * 10**9,
            qualification_max_requests=120,
            hot_pools=hot,
            hot_interval_s=120,
            cold_interval_s=1800,
            max_background_requests=4000,
            request_spacing_s=12,
            http_timeout_s=20,
            history_floor_s=now // 10**9 - 90 * 86400,
            paid_budget_usd=0,
            source="public_GeckoTerminal",
            service_prefix="ai.memecoin.brain.b09.",
            plan_sha=sha(BASE / "docs/brain-hot-lane.md"),
            training_max_runs=4,
            predecessor_total_get=36,
        ),
    )
    scheduler = copy.deepcopy(s["pools"])
    quality = {}
    for p, q in scheduler.items():
        if q["status"] == "active":
            q["next_ns"] = 0
        quality[p] = dict(
            streak=q["streak"], probes=q["probes"], next_probe_ns=q["next_ns"], status=q["status"]
        )
    publish(
        ROOT / "provider-seed.json",
        {k: s[k] for k in ("cooldown_ns", "rate_events", "failures", "last_ns")},
    )
    with connect(OLD / "index.sqlite") as db:
        caps = [
            (Path(path), read(Path(path) / "request.json")["url"].split("/pools/")[1].split("/")[0])
            for (path,) in db.execute("select path from captures order by available,path")
        ]
    with connect(ROOT / "index.sqlite") as db:
        for cap, p in caps:
            ingest(db, cap, p)
    publish(
        ROOT / "seed.json",
        dict(
            parent=str(OLD),
            quality=quality,
            scheduler_state=scheduler,
            seed_capture_count=len(caps),
            **stats(ROOT),
        ),
    )
    pin = stage_source(ROOT, ROOT.parent / "source-runtime01")
    publish(ROOT / "seed-audit.json", audit(ROOT, time.time_ns()))
    print(dict(pin=pin, hot=hot, stats=stats(ROOT)), flush=True)


if __name__ == "__main__":
    main()
