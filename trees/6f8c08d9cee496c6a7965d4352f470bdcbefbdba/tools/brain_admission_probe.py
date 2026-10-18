"""Historical cohort -> existing USD bar contract. No live source mutation."""

import argparse
import datetime
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from memecoin_brain.data import build
from memecoin_brain.source import EXCLUDED, SourceFailure, bars, ohlcv
from memecoin_ml.data import read, sha
from memecoin_operational.supervisor import child
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import publish

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / "work/brain-b13/source-repaired01"


def worker(root):
    from memecoin_operational.worker import lease_watchdog

    lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
    binding = read(root / "binding.json")
    if sha(Path(__file__)) != binding["code_sha"]:
        raise ValueError("worker_changed")
    results = []
    try:
        for case in binding["cases"]:
            if sha(SOURCE / "config.json") != binding["control_source_config_sha"]:
                raise ValueError("shared_provider_control_changed")
            events = sorted((SOURCE / "journal").glob("event-*.json"))
            state = read(events[-1])["payload"]["state"]
            if state["cooldown_ns"] > time.time_ns():
                raise ValueError("shared_provider_cooldown")
            try:
                capture = ohlcv(
                    root,
                    case["pool"],
                    lane="historical",
                    before=case["event_s"] + 3 * 86400,
                    limit=1000,
                )
            except SourceFailure:
                attempt = sorted((root / "requests").iterdir())[-1]
                if read(attempt / "failure.json").get("http_status") == 404:
                    results.append(dict(pool=case["pool"], status="not_found", eligible=0))
                    continue
                raise
            data = read(capture / "response.bin")
            metadata = data.get("meta", {})
            actual = {side: metadata.get(side, {}).get("address") for side in ("base", "quote")}
            if actual["base"] != case["base_mint"] or actual["quote"] != case["quote_mint"]:
                raise ValueError("birth_bar_mint_mismatch")
            rows = bars(capture, case["pool"])
            if any(r["open_s"] < case["event_s"] // 300 * 300 for r in rows):
                raise ValueError("bar_before_pool_birth")
            bundle = build(rows, dict(as_of_ns=time.time_ns()))
            eligible = sum(
                all(t["log_return"] is not None for t in s["targets"]) for s in bundle["samples"]
            )
            results.append(
                dict(
                    pool=case["pool"],
                    month=case["month"],
                    status="valid",
                    bars=len(rows),
                    eligible=eligible,
                    first_s=rows[0]["open_s"] if rows else None,
                    last_s=rows[-1]["open_s"] if rows else None,
                    capture=str(capture.resolve()),
                    capture_sha=sha(capture / "manifest.json"),
                    metadata=actual,
                )
            )
        publish(
            root / "result.json",
            dict(
                cases=results,
                eligible=sum(r["eligible"] for r in results),
                eligible_pools=sum(r["eligible"] > 0 for r in results),
            ),
        )
    except Exception as exc:
        publish(
            root / "STOP",
            dict(
                category=str(exc) if isinstance(exc, ValueError) else type(exc).__name__,
                results=results,
                automatic_retry=False,
            ),
        )
    seal(root)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--worker", action="store_true")
    p.add_argument("--recent-only", action="store_true")
    a = p.parse_args()
    if a.worker:
        worker(a.root)
        return
    catalogue = BASE / "work/brain-review-20261006/monthly-births01-catalogue"
    verify(catalogue)
    cases = []
    months = {m: 0 for m in ("2025-04", "2025-10", "2026-04", "2026-07", "2026-08", "2026-09")}
    if a.recent_only:
        months = {m: 0 for m in ("2026-05", "2026-06", "2026-07", "2026-08", "2026-09")}
    with sqlite3.connect(
        f"file:{catalogue / 'catalogue.sqlite'}?mode=ro&immutable=1", uri=True
    ) as db:
        for (raw,) in db.execute("select payload from births order by event_s,pool"):
            b = json.loads(raw)
            month = datetime.datetime.fromtimestamp(b["event_s"], datetime.UTC).strftime("%Y-%m")
            if month in months and months[month] < 2 and b["pool"] not in EXCLUDED:
                cases.append(dict(b, month=month))
                months[month] += 1
    if len(cases) != (10 if a.recent_only else 12):
        raise ValueError("cohort_support")
    a.root.mkdir(parents=True, exist_ok=False)
    (a.root / "requests").mkdir()
    (a.root / "code").mkdir()
    shutil.copyfile(Path(__file__), a.root / "code/worker.py")
    now = time.time_ns()
    publish(
        a.root / "config.json",
        dict(
            created_ns=now,
            expires_ns=now + 1800 * 10**9,
            max_requests=len(cases),
            min_request_interval_seconds=16,
            timeout_seconds=15,
            max_response_bytes=2 * 1024**2,
            free_floor_bytes=4 * 1024**3,
            storage_limit_bytes=64 * 1024**2,
            paid_budget_usd=0,
        ),
    )
    publish(
        a.root / "binding.json",
        dict(
            cases=cases,
            code_sha=sha(Path(__file__)),
            catalogue_sha=sha(catalogue / "manifest.json"),
            control_source_config_sha=sha(SOURCE / "config.json"),
            plan_sha=sha(BASE / "docs/brain-training-expansion.md"),
        ),
    )
    result = child(
        [sys.executable, "-B", str(Path(__file__).resolve()), str(a.root.resolve()), "--worker"],
        a.root.parent / (a.root.name + "-worker.log"),
        300,
        lambda: None,
    )
    publish(a.root.parent / (a.root.name + "-process.json"), result)
    if not (a.root / "manifest.json").exists():
        if not (a.root / "STOP").exists():
            publish(a.root / "STOP", dict(category="worker_interrupted", result=result))
        seal(a.root)
    print(read(a.root / ("STOP" if (a.root / "STOP").exists() else "result.json")))


if __name__ == "__main__":
    main()
