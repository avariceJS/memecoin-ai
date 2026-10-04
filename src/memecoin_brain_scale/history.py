"""One bounded historical acquisition; shared public-source pacing with frozen B01."""

from __future__ import annotations

import fcntl
import json
import math
import time
from pathlib import Path

from memecoin_brain.data import corpus
from memecoin_brain.service import cooldown
from memecoin_brain.source import BAR, NS, SourceFailure, bars, host_guard, ohlcv
from memecoin_ml.data import read, sha, write
from memecoin_registry.contracts import seal, verify


def budget_bound(primary: Path, now_ns: int, extra: int = 24) -> dict:
    config = read(primary / "config.json")
    used = len(list((primary / "requests").iterdir()))
    used += read(primary / "external-request-debit.json")["requests"]
    # Include current due bucket even if already collected. Bound future requests conservatively.
    future = (max(0, math.ceil((config["expires_ns"] - now_ns) / NS / BAR)) + 1) * 12
    if used + future + extra > config["max_requests"]:
        raise SourceFailure("shared_lifetime_budget")
    return dict(used=used, future_max=future, B02_reserved=extra, ceiling=config["max_requests"])


def idle_slot(primary: Path, now_ns: int) -> bool:
    due = now_ns // (BAR * NS) * BAR
    result = primary / "cycles" / str(due) / "result.json"
    if BAR - now_ns / NS % BAR < 35:
        return False
    return result.exists() and read(result)["status"] == "completed"


def prepare(primary: Path, output: Path, parent: Path | None = None) -> dict:
    host_guard(primary)
    now = time.time_ns()
    budget = budget_bound(primary, now)
    rows, _ = corpus(primary, now)
    universe = read(primary / "universe.json")
    anchor = now // (BAR * NS) * BAR
    debit = 0
    parent_ref = None
    if parent is not None:
        verify(parent)
        prior = read(parent / "plan.json")
        failure = read(parent / "failure.json")
        if failure["category"] != "pagination_not_strictly_before" or prior.get("parent"):
            raise SourceFailure("continuation_not_qualified")
        # One reviewed protocol correction; no retry of an unsuccessful provider request.
        requests = sorted((parent / "requests").iterdir())
        if len(requests) != 1 or not (requests[0] / "receipt.json").exists():
            raise SourceFailure("continuation_requires_single_successful_page")
        task = next(t for t in prior["tasks"] if t["needed"])
        got = bars(requests[0], task["pool"])
        if (
            not got
            or max(r["open_s"] for r in got) != task["cursor"]
            or min(r["open_s"] for r in got) > task["floor"]
        ):
            raise SourceFailure("continuation_boundary_diagnosis")
        rows += got
        debit, anchor = 1, prior["anchor_s"]
        parent_ref = dict(path=str(parent.resolve()), manifest_sha=sha(parent / "manifest.json"))
    tasks = []
    for u in universe["pools"]:
        pool_rows = [r for r in rows if r["pool"] == u["pool"]]
        cursor = min((r["open_s"] for r in pool_rows), default=anchor)
        floor = max(anchor - 10 * 86400, u["pool_created_ns"] // (BAR * NS) * BAR)
        tasks.append(dict(pool=u["pool"], cursor=cursor, floor=floor, needed=cursor > floor))
    output.mkdir(parents=True, exist_ok=False)
    (output / "requests").mkdir()
    c = read(primary / "config.json")
    c.update(
        created_ns=now,
        expires_ns=min(now + 600 * NS, c["expires_ns"]),
        max_requests=24 - debit,
        min_request_interval_seconds=12,
        storage_limit_bytes=256 * 1024**2,
    )
    write(output / "config.json", c)
    write(output / "universe.json", universe)
    plan = dict(
        primary=str(primary.resolve()),
        primary_config_sha=sha(primary / "config.json"),
        primary_universe_sha=sha(primary / "universe.json"),
        anchor_s=anchor,
        tasks=tasks,
        budget=budget,
        max_requests=24 - debit,
        parent=parent_ref,
        prior_attempts=debit,
        no_retry=True,
        F01=False,
    )
    write(output / "plan.json", plan)
    return plan


def acquire(primary: Path, output: Path, parent: Path | None = None) -> dict:
    plan = prepare(primary, output, parent)
    write(output / "claim.json", dict(started_ns=time.time_ns(), attempts=24))
    pending = [dict(t) for t in plan["tasks"] if t["needed"]]
    completed, captures = [], []
    deadline = time.monotonic() + 565
    status = "completed"
    active = None
    try:
        while (
            pending and len(captures) < plan.get("max_requests", 24) and time.monotonic() < deadline
        ):
            host_guard(primary)
            host_guard(output)
            if time.time_ns() < cooldown(primary):
                raise SourceFailure("primary_provider_cooldown")
            if not idle_slot(primary, time.time_ns()):
                time.sleep(2)
                continue
            with (primary / ".lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    time.sleep(2)
                    continue
                if not idle_slot(primary, time.time_ns()):
                    continue
                budget_bound(primary, time.time_ns())
                previous = sorted((primary / "requests").iterdir())
                if (
                    previous
                    and time.time_ns() - read(previous[-1] / "request.json")["started_ns"] < 12 * NS
                ):
                    continue
                task = pending.pop(0)
                active = task
                try:
                    cap = ohlcv(output, task["pool"], lane="historical", before=task["cursor"] - 1)
                    rs = bars(cap, task["pool"])
                    if any(r["open_s"] >= task["cursor"] for r in rs):
                        raise SourceFailure("pagination_not_strictly_before")
                    captures.append(
                        dict(path=str(cap), sha=sha(cap / "manifest.json"), rows=len(rs))
                    )
                    if not rs:
                        completed.append(
                            dict(**task, reason="provider_empty_not_proof_of_no_trades")
                        )
                    else:
                        task["cursor"] = min(r["open_s"] for r in rs)
                        if task["cursor"] <= task["floor"]:
                            completed.append(dict(**task, reason="target_floor_reached"))
                        else:
                            pending.append(task)
                    print(json.dumps(dict(status="page_saved", **captures[-1])), flush=True)
                    active = None
                finally:
                    # Frozen B01 knows only its own receipts; hold the shared lock through spacing.
                    time.sleep(12)
        if pending:
            status = "bounded_partial"
    except Exception as exc:
        if active is not None:
            pending.insert(0, active)
        status = "failed_no_retry"
        failure = dict(
            category=str(exc) if isinstance(exc, SourceFailure) else "history_integrity",
            error_type=type(exc).__name__,
            failed_ns=time.time_ns(),
        )
        write(output / "failure.json", failure)
        print(json.dumps(failure), flush=True)
    result = dict(
        status=status,
        captures=captures,
        completed=completed,
        pending=pending,
        actual_attempts=len(list((output / "requests").iterdir())),
        finished_ns=time.time_ns(),
    )
    write(output / "result.json", result)
    seal(output)
    return result


if __name__ == "__main__":
    import argparse
    import os
    import sys

    from memecoin_operational.supervisor import Watch, child
    from memecoin_operational.worker import lease_watchdog

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("primary", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--parent", type=Path)
    args = parser.parse_args()
    if args.worker:
        lease_watchdog(int(os.environ["MEMECOIN_LEASE_FD"]))
        print(json.dumps(acquire(args.primary, args.output, args.parent)))
    else:
        watch = Watch(600)

        def guard():
            if watch.remaining() <= 0:
                raise SourceFailure("history_wall_budget")

        command = [
            sys.executable,
            "-B",
            "-m",
            "memecoin_brain_scale.history",
            str(args.primary),
            str(args.output),
            "--worker",
        ]
        if args.parent:
            command.extend(["--parent", str(args.parent)])
        result = child(
            command,
            args.output.with_suffix(".log"),
            600,
            guard,
        )
        write(args.output.with_suffix(".process.json"), result)
        print(json.dumps(result))
