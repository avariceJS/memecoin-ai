"""Due-only qualification engine. No service installation or activation entry point."""

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from memecoin_registry.contracts import digest
from memecoin_sampling.contract import NS, policy

Row = dict[str, Any]


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable(path: Path, row: Row) -> None:
    # Exclusive creation preserves partial writes on crash; restart fails closed.
    with path.open("x") as f:
        json.dump(row, f, sort_keys=True, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    sync_dir(path.parent)


def action(due: int, now: int) -> str:
    if now < due:
        return "pending"
    return "missed" if now > due + 900 * NS else "collect"


def run_due(
    root: Path,
    binding: Row,
    proposal_sha: str,
    *,
    now: Callable[[], int],
    runner: Callable[[Path, Row], Row],
) -> list[Row]:
    """Caller must validate exact activation outside this qualification-only engine.

    A successful callback must itself seal/verify raw data. This layer never examines
    market labels, including for reserved windows, and does not grant corpus admission.
    """
    if binding["policy"] != policy():
        raise ValueError("frozen_policy_changed")
    if root.resolve() != Path(binding["source_root"]).resolve():
        raise ValueError("scheduler_source_root_changed")
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = root / "journal"
        journal.mkdir(exist_ok=True)
        windows = root / "windows"
        windows.mkdir(exist_ok=True)
        header = dict(
            proposal_sha256=proposal_sha,
            source_root=binding["source_root"],
            schedule=binding["windows"],
            policy=binding["policy"],
        )
        if (root / "binding.json").exists():
            if json.loads((root / "binding.json").read_text()) != header:
                raise ValueError("scheduler_binding_changed")
        else:
            durable(root / "binding.json", header)
        old = [json.loads(p.read_text()) for p in sorted(journal.glob("*.json"))]
        if any(
            r["index"] != i
            or r["status"] not in ("completed", "failed", "missed")
            or r["reserved_rpc"] != (0 if r["status"] == "missed" else 180)
            for i, r in enumerate(old)
        ):
            raise ValueError("corrupt_scheduler_journal")
        parent = proposal_sha
        for row in old:
            unsigned = {k: v for k, v in row.items() if k != "sha256"}
            if row["parent"] != parent or row["sha256"] != digest(unsigned):
                raise ValueError("scheduler_journal_chain_changed")
            parent = row["sha256"]
        if old and now() < old[-1]["finished_ns"]:
            raise ValueError("scheduler_clock_regression")
        failures = sum(r["status"] == "failed" for r in old)
        if failures >= 8 or (root / "STOP").exists():
            return old
        for slot in binding["windows"][len(old) :]:
            index, due = slot["index"], slot["due_ns"]
            window = windows / f"{index:03}"
            started = now()
            if window.exists():
                result = dict(status="failed", reason="interrupted_reservation_no_retry")
                reserved = 180
            else:
                choice = action(due, started)
                if choice == "pending":
                    break
                reserved = 0 if choice == "missed" else 180
                if choice == "missed":
                    result = dict(status="missed", reason="lateness_no_catchup")
                else:
                    if sum(r["reserved_rpc"] for r in old) + 180 > 60480:
                        raise ValueError("rpc_reservation_ceiling")
                    window.mkdir()
                    sync_dir(windows)
                    reservation = dict(
                        index=index,
                        due_ns=due,
                        started_ns=started,
                        reserved_rpc=180,
                        plan_sha256=proposal_sha,
                    )
                    durable(window / "reservation.json", reservation)
                    # BaseException intentionally propagates; crash bytes retained,
                    # future call charges full180 and never invokes runner again.
                    try:
                        result = runner(window, reservation)
                    except Exception:
                        result = dict(status="failed", reason="worker_error")
                    if result.get("status") not in ("completed", "failed"):
                        result = dict(status="failed", reason="invalid_worker_outcome")
                    if not started <= now() <= started + 600 * NS:
                        result = dict(status="failed", reason="window_deadline_or_clock")
                    if (root / "STOP").exists():
                        result = dict(status="failed", reason="stop_requested")
            terminal = dict(
                index=index,
                status=result["status"],
                reason=result.get("reason"),
                reserved_rpc=reserved,
                finished_ns=now(),
            )
            terminal["parent"] = old[-1]["sha256"] if old else proposal_sha
            terminal["sha256"] = digest(terminal)
            durable(journal / f"{index:03}.json", terminal)
            old.append(terminal)
            if terminal["status"] == "failed":
                break
        return old
