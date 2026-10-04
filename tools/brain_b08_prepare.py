"""Bounded four-page discovery, then seed a new source without disturbing B04/B07."""

import time
from pathlib import Path

from memecoin_brain.source import select_universe
from memecoin_brain_quality.policy import states
from memecoin_continuous.source import fetch, seed_state, tick
from memecoin_continuous.store import audit, connect, ingest, stats
from memecoin_ml.data import read, sha
from memecoin_runner.ledger import Ledger, publish

BASE = Path(__file__).resolve().parents[1]
OLD = BASE / "work/brain-b04/prospective01"
ROOT = BASE / "work/brain-b08/source01"


def main():
    base = ROOT.parent
    base.mkdir(exist_ok=False)
    discovery = base / "discovery01"
    discovery.mkdir()
    (discovery / "requests").mkdir()
    now = time.time_ns()
    c = dict(
        expires_ns=now + 3600 * 10**9, max_requests=4, history_floor_s=now // 10**9 - 90 * 86400
    )
    publish(discovery / "config.json", c)
    publish(discovery / "universe.json", dict(pools=[]))
    caps = []
    s = seed_state(discovery)
    with Ledger(
        discovery / "journal", dict(config_sha=sha(discovery / "config.json"), scope="B08_source")
    ) as ledger:
        for page in range(1, 5):
            s, p = fetch(discovery, dict(lane="discovery", page=page), s, ledger)
            caps.append(p)
            if (p / "failure.json").exists():
                raise ValueError("discovery_failed_no_retry")
    chosen = select_universe(caps, now_ns=time.time_ns(), maximum=64)
    old = read(OLD / "universe.json")
    quality, _ = states(OLD)
    pools = []
    for p in old["pools"]:
        pools.append(
            dict(p, initial_quality=quality[p["pool"]]["status"], admitted_ns=old["admitted_ns"])
        )
    bases = {p["base_mint"] for p in pools}
    for p in chosen["pools"]:
        if p["base_mint"] not in bases and len(pools) < 24:
            pools.append(dict(p, initial_quality="active", admitted_ns=chosen["admitted_ns"]))
            bases.add(p["base_mint"])
    ROOT.mkdir()
    (ROOT / "requests").mkdir()
    publish(
        ROOT / "config.json",
        dict(
            created_ns=now,
            expires_ns=now + 7 * 86400 * 10**9,
            max_requests=74996,
            discovery_attempts=4,
            history_floor_s=c["history_floor_s"],
            paid_budget_usd=0,
            source="public_GeckoTerminal",
            plan_sha=sha(BASE / "docs/brain-continuity.md"),
        ),
    )
    publish(
        ROOT / "universe.json",
        dict(
            pools=pools,
            admitted_ns=chosen["admitted_ns"],
            survivorship_bias=True,
            historical_universe_known=False,
            parent_universe_sha=sha(OLD / "universe.json"),
        ),
    )
    captures = []
    for path in (OLD / "requests").iterdir():
        if (path / "manifest.json").exists() and (path / "receipt.json").exists():
            request = read(path / "request.json")
            if request["lane"] != "discovery":
                captures.append(path)
    captures.sort(key=lambda p: (read(p / "receipt.json")["available_ns"], str(p)))
    start = time.monotonic()
    with connect(ROOT / "index.sqlite") as db:
        for cap in captures:
            ingest(db, cap, read(cap / "request.json")["url"].split("/pools/")[1].split("/")[0])
    publish(
        ROOT / "seed.json",
        dict(
            parent=str(OLD),
            captures=len(captures),
            seconds=time.monotonic() - start,
            quality=quality,
            model="B07 unchanged",
            **stats(ROOT),
        ),
    )
    print(dict(universe=len(pools), seed=stats(ROOT)), flush=True)
    for i in range(3):
        tick(ROOT)
        print(dict(qualification_cycle=i, health=stats(ROOT)), flush=True)
        if (ROOT / "STOP").exists():
            break
    print(audit(ROOT, time.time_ns()), flush=True)


if __name__ == "__main__":
    main()
