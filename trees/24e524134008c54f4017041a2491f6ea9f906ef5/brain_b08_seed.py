"""Continue from three successful discovery captures; no retry or new network IO."""

import time
from pathlib import Path

from memecoin_brain.source import select_universe
from memecoin_brain_quality.policy import states
from memecoin_continuous.store import audit, connect, ingest, stats
from memecoin_ml.data import read, sha
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
OLD = BASE / "work/brain-b04/prospective01"
ROOT = BASE / "work/brain-b08/source01"


def main():
    caps = [
        p for p in (ROOT.parent / "discovery01/requests").iterdir() if (p / "receipt.json").exists()
    ]
    chosen = select_universe(caps, now_ns=time.time_ns(), maximum=64)
    old = read(OLD / "universe.json")
    quality, _ = states(OLD)
    pools = [
        dict(p, initial_quality=quality[p["pool"]]["status"], admitted_ns=old["admitted_ns"])
        for p in old["pools"]
    ]
    bases = {p["base_mint"] for p in pools}
    for p in chosen["pools"]:
        if p["base_mint"] not in bases and len(pools) < 24:
            pools.append(dict(p, initial_quality="active", admitted_ns=chosen["admitted_ns"]))
            bases.add(p["base_mint"])
    now = time.time_ns()
    ROOT.mkdir()
    (ROOT / "requests").mkdir()
    publish(
        ROOT / "config.json",
        dict(
            created_ns=now,
            expires_ns=now + 7 * 86400 * 10**9,
            max_requests=74996,
            discovery_attempts=4,
            request_spacing_s=12,
            history_floor_s=now // 10**9 - 90 * 86400,
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
    failures = [
        read(ROOT.parent / "discovery01/requests/000003/failure.json"),
        read(OLD / "requests/000441/failure.json"),
    ]
    times = [r["received_ns"] for r in failures]
    publish(
        ROOT / "provider-seed.json",
        dict(cooldown_ns=max(times) + 900 * 10**9, rate_events=times, failures=2),
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
            seed_capture_count=len(captures),
            seconds=time.monotonic() - start,
            quality=quality,
            **stats(ROOT),
        ),
    )
    print(
        dict(universe=len(pools), stats=stats(ROOT), audit=audit(ROOT, time.time_ns())), flush=True
    )


if __name__ == "__main__":
    main()
