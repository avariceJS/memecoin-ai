#!/usr/bin/env python3
"""Run one bounded paired finalized-block epoch per UTC scheduling bucket."""

from __future__ import annotations

import argparse
import fcntl
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.brain_senses_block_epoch import start_epoch  # noqa: E402
from tools.brain_senses_program_watch import _create  # noqa: E402

BUCKET_RE = re.compile(r"^\d{8}T\d{2}0000Z$")


def utc_bucket(now: datetime, interval_hours: int = 6) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone_aware_datetime_required")
    if interval_hours not in (1, 2, 3, 4, 6, 8, 12, 24):
        raise ValueError("unsupported_interval_hours")
    utc = now.astimezone(UTC)
    hour = utc.hour - utc.hour % interval_hours
    return utc.replace(hour=hour, minute=0, second=0, microsecond=0).strftime("%Y%m%dT%H0000Z")


def run_bucket(root_base: Path, *, window_slots: int = 20, interval_hours: int = 6) -> dict:
    now = datetime.now(UTC)
    bucket = utc_bucket(now, interval_hours)
    root_base.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = root_base / bucket
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = root_base / ".schedule.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        attempts = list((root / "requests").glob("*.json"))
        if attempts or (root / "epoch-plan.json").exists():
            event = {
                "status": "bucket_already_attempted_no_replay",
                "bucket": bucket,
                "root": str(root),
                "attempt_count": len(attempts),
            }
            _create(root / f"schedule-skip-{time.time_ns()}.json", event)
            return event
        previous = sorted(
            path
            for path in root_base.iterdir()
            if path.is_dir() and BUCKET_RE.fullmatch(path.name) and path != root
        )
        summary = start_epoch(
            root,
            window_slots=window_slots,
            old_epoch_root=previous[-1] if previous else None,
        )
        event = {
            "status": "epoch_finished",
            "bucket": bucket,
            "root": str(root),
            "window_slots": window_slots,
            "summary": summary,
        }
        _create(root_base / f"{bucket}.schedule-result.json", event)
        return event


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root-base", type=Path, required=True)
    parser.add_argument("--window-slots", type=int, default=20)
    parser.add_argument("--interval-hours", type=int, default=6)
    args = parser.parse_args()
    if not 1 <= args.window_slots <= 64:
        parser.error("window_slots_outside_bound")
    try:
        result = run_bucket(
            args.root_base,
            window_slots=args.window_slots,
            interval_hours=args.interval_hours,
        )
        from memecoin_ai.domain import canonical

        print(canonical(result), flush=True)
        return 0
    except Exception as exc:
        print(f"block epoch scheduler failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
