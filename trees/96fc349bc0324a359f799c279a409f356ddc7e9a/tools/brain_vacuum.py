"""Bounded free-quota cohort acquisition, separate from the live B13 runtime."""

import argparse
import datetime
import os
import sys
import time
from pathlib import Path

from memecoin_collector.qualification import endpoint, identity
from memecoin_ml.data import read, sha
from memecoin_operational.supervisor import child
from memecoin_pumpswap.indexed import IndexedRPC
from memecoin_pumpswap.vacuum import catalogue, run
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--worker", action="store_true")
    p.add_argument("--pool-cohort", type=Path)
    p.add_argument("--fee-stream", action="store_true")
    a = p.parse_args()
    if a.worker:
        from memecoin_operational.worker import lease_watchdog

        lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
        binding = read(a.root / "binding.json")
        url = endpoint(BASE / ".env.local")
        if identity(url) != binding["credential_sha"] or any(
            sha(BASE / k) != v for k, v in binding["code"].items()
        ):
            raise ValueError("vacuum_binding")
        client = IndexedRPC(
            url, "historical-birth-vacuum", timeout=15, max_bytes=10 * 1024**2, keep_alive=True
        )
        run(a.root, client)
        seal(a.root)
        return
    owner = BASE / "docs/evidence/f01-d51-owner-attestation-2026-10-04.json"
    credential = identity(endpoint(BASE / ".env.local"))
    if credential != read(owner)["credential_sha256"]:
        raise ValueError("owner_mapping_changed")
    a.root.mkdir(parents=True, exist_ok=False)
    dates = [
        datetime.datetime(y, m, 1, tzinfo=datetime.UTC)
        for y in (2025, 2026)
        for m in range(1, 13)
        if (2025, 4) <= (y, m) <= (2026, 9)
    ]
    cases = [
        dict(
            address="39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg",
            start=int(d.timestamp()),
            end=int(d.timestamp()) + 86400,
        )
        for d in dates
    ]
    cohort_pin = None
    if a.pool_cohort:
        import sqlite3

        from memecoin_registry.contracts import verify

        verify(a.pool_cohort)
        cohort_pin = sha(a.pool_cohort / "manifest.json")
        cases, months = [], set()
        with sqlite3.connect(
            f"file:{a.pool_cohort / 'catalogue.sqlite'}?mode=ro&immutable=1", uri=True
        ) as db:
            for pool, t in db.execute("select pool,event_s from births order by event_s,pool"):
                month = datetime.datetime.fromtimestamp(t, datetime.UTC).strftime("%Y-%m")
                if month not in months:
                    months.add(month)
                    cases.append(dict(address=pool, start=t, end=t + 86400))
        if len(cases) != 17:
            raise ValueError("cohort_support")
    if a.fee_stream:
        cases = [
            dict(
                address=address,
                start=int(datetime.datetime.fromisoformat(day + "T00:00:00+00:00").timestamp()),
                end=int(datetime.datetime.fromisoformat(day + "T00:00:00+00:00").timestamp())
                + 86400,
            )
            for day in ("2025-05-01", "2025-07-01", "2025-10-01")
            for address in (
                "94qWNrtmfn42h3ZjUZwWvK1MEo9uVmmrBPd2hpNjYDjb",
                "7GFUN3bWzJMKMRZ34JLsvcqdssDbXnp589SiE33KVwcC",
            )
        ]
    code = [
        "tools/brain_vacuum.py",
        *[str(p.relative_to(BASE)) for p in (BASE / "src/memecoin_pumpswap").glob("*.py")],
    ]
    import shutil

    for name in code:
        target = a.root / "code" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BASE / name, target)
    seal(a.root / "code")
    publish(
        a.root / "binding.json",
        dict(
            schema="historical-cohort-v1",
            cases=cases,
            cohort_sha=cohort_pin,
            pages_per_case=3,
            max_rpc=len(cases) * 3,
            max_cu=len(cases) * 300,
            max_bytes=540 * 1024**2,
            paid_usd=0,
            expires_ns=time.time_ns() + 600 * 10**9,
            credential_sha=credential,
            owner_sha=sha(owner),
            code={k: sha(BASE / k) for k in code},
            prereg_sha=sha(BASE / "docs/brain-market-vacuum.md"),
        ),
    )
    result = child(
        [sys.executable, "-B", str(Path(__file__).resolve()), str(a.root.resolve()), "--worker"],
        a.root.parent / (a.root.name + "-worker.log"),
        600,
        lambda: None,
    )
    publish(a.root.parent / (a.root.name + "-process.json"), result)
    if not (a.root / "manifest.json").exists():
        if not (a.root / "STOP").exists():
            publish(a.root / "STOP", dict(category="worker_failed", process=result))
        seal(a.root)
    if not (a.root / "STOP").exists():
        print(
            dict(
                pools=catalogue(a.root, a.root.parent / (a.root.name + "-catalogue")),
                result=read(a.root / "result.json"),
            )
        )
    else:
        print(read(a.root / "STOP"))


if __name__ == "__main__":
    main()
