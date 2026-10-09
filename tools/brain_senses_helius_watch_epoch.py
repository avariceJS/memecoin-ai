#!/usr/bin/env python3
"""Run a fresh, bounded Helius address-index epoch within one UTC six-hour bucket."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memecoin_ai.domain import canonical  # noqa: E402
from tools.brain_senses_program_watch import _create, _read  # noqa: E402

BUCKET_RE = re.compile(r"^\d{8}T(?:00|06|12|18)0000Z$")
MAX_EPOCH_REQUESTS = 1_100
MAX_EPOCH_SECONDS = 21_600


def utc_bucket(now: datetime) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone_aware_datetime_required")
    utc = now.astimezone(UTC)
    hour = utc.hour - utc.hour % 6
    return utc.replace(hour=hour, minute=0, second=0, microsecond=0).strftime("%Y%m%dT%H0000Z")


def seconds_until_next_bucket(now: datetime) -> int:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone_aware_datetime_required")
    utc = now.astimezone(UTC)
    next_hour = (utc.hour // 6 + 1) * 6
    boundary = utc.replace(minute=0, second=0, microsecond=0)
    if next_hour == 24:
        boundary = boundary.replace(hour=0) + timedelta(days=1)
    else:
        boundary = boundary.replace(hour=next_hour)
    return max(1, min(MAX_EPOCH_SECONDS, int((boundary - utc).total_seconds())))


def select_parent(previous_epochs: list[Path], initial_parent: Path | None) -> Path | None:
    return sorted(previous_epochs)[-1] if previous_epochs else initial_parent


def run_epoch(root_base: Path, *, initial_parent: Path | None = None) -> dict:
    now = datetime.now(UTC)
    bucket = utc_bucket(now)
    root_base.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root_base, 0o700)
    root = root_base / bucket
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = root_base / ".schedule.lock"
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "epoch_scheduler_already_running", "bucket": bucket}
        if (root / "epoch-schedule.json").exists() or (root / "source-epoch.json").exists():
            return {"status": "epoch_already_attempted_no_replay", "bucket": bucket}
        previous = [
            path
            for path in root_base.iterdir()
            if path.is_dir() and BUCKET_RE.fullmatch(path.name) and path != root
        ]
        parent = select_parent(previous, initial_parent)
        remaining_seconds = seconds_until_next_bucket(now)
        schedule = {
            "schema_version": 1,
            "bucket": bucket,
            "created_ns": time.time_ns(),
            "provider": "helius-free",
            "root": str(root),
            "parent_epoch_root": str(parent) if parent else None,
            "cursor_inheritance": "none",
            "retry_policy": "one attempt per reserved request; failure ends this epoch",
            "max_request_reservations": MAX_EPOCH_REQUESTS,
            "max_runtime_seconds": remaining_seconds,
            "page_limit": 1_000,
            "poll_seconds": 15,
            "pace_seconds": 0.5,
            "coverage_claim": (
                "sparse Helius address-index stream; gaps and unqueried scope unknown"
            ),
        }
        _create(root / "epoch-schedule.json", schedule)
        command = [
            sys.executable,
            str(REPO_ROOT / "tools/brain_senses_program_watch.py"),
            "--provider",
            "helius-free",
            "--root",
            str(root),
            "--poll-seconds",
            "15",
            "--pace-seconds",
            "0.5",
            "--daily-request-cap",
            str(MAX_EPOCH_REQUESTS),
            "--max-pages",
            "4",
            "--page-limit",
            "1000",
            "--stop-after-seconds",
            str(remaining_seconds),
        ]
        if parent is not None:
            command.extend(("--parent-epoch-root", str(parent)))
        _create(
            root / "epoch-command.json",
            {"command_sha256": hashlib.sha256(canonical(command).encode()).hexdigest()},
        )
        try:
            with (
                (root / "collector.out.log").open("ab") as stdout,
                (root / "collector.err.log").open("ab") as stderr,
            ):
                completed = subprocess.run(
                    command,
                    cwd=REPO_ROOT,
                    env=None,
                    stdout=stdout,
                    stderr=stderr,
                    check=False,
                )
            if completed.returncode != 0 and not (root / "STOP").exists():
                _create(
                    root / "STOP",
                    {"reason": "collector_exit_failure", "returncode": completed.returncode},
                )
            result = {
                "status": "epoch_finished",
                "bucket": bucket,
                "root": str(root),
                "returncode": completed.returncode,
                "stop": _read(root / "STOP") if (root / "STOP").exists() else None,
            }
        except Exception as exc:
            if not (root / "STOP").exists():
                _create(
                    root / "STOP",
                    {"reason": "scheduler_exception", "error_class": type(exc).__name__},
                )
            result = {
                "status": "scheduler_failed",
                "bucket": bucket,
                "error_class": type(exc).__name__,
            }
        _create(root / "epoch-result.json", result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root-base", type=Path, required=True)
    parser.add_argument("--initial-parent-root", type=Path)
    args = parser.parse_args()
    result = run_epoch(args.root_base, initial_parent=args.initial_parent_root)
    print(canonical(result), flush=True)
    return 0 if result["status"] in ("epoch_finished", "epoch_already_attempted_no_replay") else 1


if __name__ == "__main__":
    raise SystemExit(main())
