"""Finite birth-cohort admission campaign, one bounded child per request, no retries."""

import argparse
import datetime
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from memecoin_brain.source import EXCLUDED, SourceFailure, ohlcv
from memecoin_curriculum.admission import qualify
from memecoin_ml.data import read, sha
from memecoin_operational.supervisor import child
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import publish

BASE = Path.cwd()


def control(binding):
    source = Path(binding["source"])
    if sha(source / "config.json") != binding["source_config_sha"] or (source / "STOP").exists():
        raise ValueError("control_source_changed_or_stopped")
    latest = sorted((source / "journal").glob("event-*.json"))[-1]
    state = read(latest)["payload"]["state"]
    if state["cooldown_ns"] > time.time_ns():
        raise ValueError("control_source_cooldown")
    if time.time_ns() - state["last_ns"] > 600 * 10**9:
        raise ValueError("control_source_stale")
    return state


def worker(root, index):
    from memecoin_operational.worker import lease_watchdog

    lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
    binding = read(root / "binding.json")
    control(binding)
    case = binding["cases"][index]
    try:
        capture = ohlcv(
            root, case["pool"], lane="historical", before=case["event_s"] + 3 * 86400, limit=1000
        )
    except SourceFailure:
        attempts = sorted((root / "requests").iterdir())
        failure = (
            read(attempts[-1] / "failure.json")
            if attempts and (attempts[-1] / "failure.json").exists()
            else {}
        )
        if failure.get("http_status") is None and failure.get("error_type") in {
            "URLError",
            "TimeoutError",
            "ConnectionResetError",
        }:
            result = dict(
                pool=case["pool"],
                eligible=0,
                status="transport_gap",
                birth=case,
                failure=failure,
                capture=str(attempts[-1]),
            )
        elif failure.get("http_status") == 404:
            result = dict(pool=case["pool"], eligible=0, status="not_found", birth=case)
        else:
            raise
    else:
        try:
            _, result = qualify(capture, case)
        except ValueError as exc:
            result = dict(
                pool=case["pool"],
                eligible=0,
                status="quarantined",
                reason=str(exc),
                birth=case,
                capture=str(capture),
                capture_sha=sha(capture / "manifest.json"),
            )
    publish(root / "results" / f"{index:06d}.json", result)


def initialize(root, count, spacing, not_before, previous=None):
    catalogue = BASE / "work/brain-review-20261006/monthly-births01-catalogue"
    source = BASE / "work/brain-b13/source-repaired01"
    verify(catalogue)
    blocked = set(EXCLUDED)
    for name in ("admission-probe01", "admission-probe02"):
        probe = root.parent / name
        for p in (probe / "requests").glob("*/request.json"):
            blocked.add(read(p)["url"].split("/pools/")[1].split("/")[0])
    months = {f"2026-{m:02d}": [] for m in range(5, 10)}
    with sqlite3.connect(
        f"file:{catalogue / 'catalogue.sqlite'}?mode=ro&immutable=1", uri=True
    ) as db:
        for (raw,) in db.execute("select payload from births"):
            b = json.loads(raw)
            month = datetime.datetime.fromtimestamp(b["event_s"], datetime.UTC).strftime("%Y-%m")
            if month in months and b["pool"] not in blocked:
                months[month].append(dict(b, month=month))
    for m in months:
        months[m].sort(key=lambda b: digest(["birth-admission-v1", b["pool"]]))
    cases = []
    while len(cases) < count:
        for values in months.values():
            if values and len(cases) < count:
                cases.append(values.pop(0))
        if not any(months.values()) and len(cases) < count:
            raise ValueError("not_enough_births")
    continuation = None
    if previous:
        verify(previous)
        old = read(previous / "binding.json")
        attempts = sorted((previous / "requests").iterdir())
        failure = read(attempts[-1] / "failure.json")
        if (
            not (previous / "STOP").exists()
            or failure.get("http_status") is not None
            or failure.get("error_type") != "URLError"
        ):
            raise ValueError("continuation_requires_transport_stop")
        cases = old["cases"][len(attempts) :]
        count = len(cases)
        continuation = dict(
            path=str(previous),
            sha=sha(previous / "manifest.json"),
            charged_requests=len(attempts),
            transport_failures=1,
            original_case_count=len(old["cases"]),
            failed_pool=old["cases"][len(attempts) - 1]["pool"],
        )
        not_before = max(not_before, failure["received_ns"] + 120 * 10**9)
    root.mkdir(parents=True, exist_ok=False)
    for name in ("requests", "results", "processes", "code"):
        (root / name).mkdir()
    shutil.copyfile(__file__, root / "code/worker.py")
    shutil.copyfile(BASE / "docs/brain-training-expansion.md", root / "code/protocol.md")
    for path in (BASE / "src").glob("memecoin_*/*.py"):
        dest = root / "code/runtime" / path.relative_to(BASE / "src")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
    seal(root / "code")
    now = time.time_ns()
    expires = max(now, not_before) + (count * (spacing + 20) + 600) * 10**9
    if previous:
        expires = min(expires, read(previous / "config.json")["expires_ns"])
    publish(
        root / "config.json",
        dict(
            created_ns=now,
            expires_ns=expires,
            max_requests=count,
            min_request_interval_seconds=spacing,
            timeout_seconds=15,
            max_response_bytes=2 * 1024**2,
            free_floor_bytes=4 * 1024**3,
            storage_limit_bytes=320 * 1024**2,
            paid_budget_usd=0,
        ),
    )
    publish(
        root / "binding.json",
        dict(
            version="birth-admission-v1",
            cases=cases,
            source=str(source),
            source_config_sha=sha(source / "config.json"),
            catalogue_sha=sha(catalogue / "manifest.json"),
            code_sha=sha(root / "code/manifest.json"),
            plan_sha=sha(BASE / "docs/brain-training-expansion.md"),
            not_before_ns=not_before,
            spacing_s=spacing,
            automatic_retry=False,
            continuation=continuation,
            transport_gap_cooldown_s=120,
            max_transport_failures=5,
        ),
    )


def run(root):
    b = read(root / "binding.json")
    c = read(root / "config.json")
    try:
        if sha(root / "code/manifest.json") != b["code_sha"]:
            raise ValueError("worker_pin")
        verify(root / "code")
        last, gap_due, consecutive = 0, 0, 0
        failures = (b.get("continuation") or {}).get("transport_failures", 0)
        for index, case in enumerate(b["cases"]):
            due = max(b["not_before_ns"], last + b["spacing_s"] * 10**9, gap_due)
            while True:
                now = time.time_ns()
                if now >= c["expires_ns"] or (root / "STOP").exists():
                    raise ValueError("campaign_expiry_or_stop")
                state = control(b)
                # Background lane yields at least 12s after each observed control reservation.
                if now >= max(due, state["last_ns"] + 12 * 10**9):
                    break
                time.sleep(1)
            script = (
                "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));"
                "runpy.run_path(sys.argv.pop(1),run_name='__main__')"
            )
            last = time.time_ns()
            publish(
                root / "processes" / f"{index:06d}-reservation.json",
                dict(index=index, pool=case["pool"], started_ns=last),
            )
            result = child(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    script,
                    str(root / "code/runtime"),
                    str(root / "code/worker.py"),
                    str(root),
                    "--worker",
                    str(index),
                ],
                root / "processes" / f"{index:06d}.log",
                45,
                lambda: control(b),
            )
            publish(root / "processes" / f"{index:06d}-outcome.json", result)
            if result["exit_code"] or not (root / "results" / f"{index:06d}.json").exists():
                raise ValueError("campaign_child_failed")
            row = read(root / "results" / f"{index:06d}.json")
            if row["status"] == "transport_gap":
                failures += 1
                consecutive += 1
                gap_due = time.time_ns() + b["transport_gap_cooldown_s"] * 10**9
                if failures >= b["max_transport_failures"] or consecutive >= 3:
                    raise ValueError("transport_failure_bound")
            else:
                consecutive = 0
            print(
                json.dumps(
                    dict(
                        index=index,
                        pool=case["pool"],
                        status=row["status"],
                        eligible=row["eligible"],
                    )
                ),
                flush=True,
            )
        publish(root / "completed.json", dict(at_ns=time.time_ns(), requests=len(b["cases"])))
    except Exception as exc:
        if not (root / "STOP").exists():
            publish(
                root / "STOP",
                dict(
                    at_ns=time.time_ns(),
                    category=str(exc) if isinstance(exc, ValueError) else type(exc).__name__,
                    automatic_retry=False,
                ),
            )
        raise
    finally:
        seal(root)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    p.add_argument("--worker", type=int)
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--spacing", type=int, default=30)
    p.add_argument("--not-before", type=int, default=1791261712341818000)
    p.add_argument("--continue-from", type=Path)
    args = p.parse_args()
    root = args.root.resolve()
    if args.worker is not None:
        worker(root, args.worker)
    else:
        if not 1 <= args.count <= 120 or args.spacing < 30:
            raise ValueError("campaign_bound")
        initialize(
            root,
            args.count,
            args.spacing,
            args.not_before,
            args.continue_from.resolve() if args.continue_from else None,
        )
        run(root)


if __name__ == "__main__":
    main()
