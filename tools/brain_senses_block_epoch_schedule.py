#!/usr/bin/env python3
"""Run one seeded finalized-block epoch per UTC scheduling bucket."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.brain_senses_block_epoch import PROVIDERS, start_epoch  # noqa: E402
from tools.brain_senses_program_watch import _create, _read  # noqa: E402

BUCKET_RE = re.compile(r"^\d{8}T\d{2}0000Z$")
CAMPAIGN_SEED = "brain-senses-helius-hourly-sample-20261009"
HELIUS_CREDIT_CAP = 20_000
MAX_CAMPAIGN_RAW_BYTES = 32 * 1024**3
MAX_EPOCH_RAW_BYTES = 1_000_000_000


def utc_bucket(now: datetime, interval_hours: int = 6) -> str:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone_aware_datetime_required")
    if interval_hours not in (1, 2, 3, 4, 6, 8, 12, 24):
        raise ValueError("unsupported_interval_hours")
    utc = now.astimezone(UTC)
    hour = utc.hour - utc.hour % interval_hours
    return utc.replace(hour=hour, minute=0, second=0, microsecond=0).strftime("%Y%m%dT%H0000Z")


def select_parent(previous_epochs: list[Path], initial_parent_root: Path | None) -> Path | None:
    return sorted(previous_epochs)[-1] if previous_epochs else initial_parent_root


def campaign_usage(root_base: Path) -> dict[str, int]:
    helius_credits = raw_bytes = helius_slots = public_pair_slots = public_windows = 0
    for epoch in root_base.iterdir():
        if not epoch.is_dir() or not BUCKET_RE.fullmatch(epoch.name):
            continue
        plan_path = epoch / "epoch-plan.json"
        window_path = epoch / "window-plan.json"
        if plan_path.exists() and window_path.exists():
            plan = _read(plan_path)
            window = _read(window_path)
            start, end = window["selected_slot_range_exclusive"]
            helius_slots += end - start
            if "solana-public" in plan.get("providers", []):
                public_pair_slots += end - start
                public_windows += 1
        for result_path in epoch.glob("**/request-results/*.json"):
            result = _read(result_path)
            raw_bytes += int(result.get("response_bytes") or 0)
        for request_path in epoch.glob("**/requests/*.json"):
            request = _read(request_path)
            if request.get("provider") == "helius-free":
                helius_credits += int(
                    request.get("credit_reservation")
                    or request.get("expected_credit_reservation")
                    or 0
                )
    return {
        "helius_credits": helius_credits,
        "raw_bytes": raw_bytes,
        "helius_slots": helius_slots,
        "public_pair_slots": public_pair_slots,
        "public_windows": public_windows,
    }


def run_bucket(
    root_base: Path,
    *,
    window_slots: int = 64,
    interval_hours: int = 1,
    initial_parent_root: Path | None = None,
    slots_per_day_estimate: int = 321_661,
    campaign_hours: int = 72,
) -> dict:
    now = datetime.now(UTC)
    bucket = utc_bucket(now, interval_hours)
    root_base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not 1 <= window_slots <= 64:
        raise ValueError("window_slots_outside_bound")
    if not 20 <= campaign_hours <= 168:
        raise ValueError("campaign_hours_outside_bound")
    if slots_per_day_estimate < 24 * window_slots:
        raise ValueError("slots_per_day_estimate_too_small")
    if interval_hours not in (1, 2, 3, 4, 6, 8, 12, 24):
        raise ValueError("unsupported_interval_hours")
    campaign_path = root_base / "campaign.json"
    if campaign_path.exists():
        campaign = _read(campaign_path)
        if any(
            campaign.get(key) != expected
            for key, expected in (
                ("window_slots", window_slots),
                ("schedule_interval_hours", interval_hours),
                ("campaign_hours", campaign_hours),
                ("slots_per_day_estimate", slots_per_day_estimate),
            )
        ):
            raise ValueError("campaign_binding_changed")
    else:
        started_ns = time.time_ns()
        campaign = {
            "schema_version": 1,
            "campaign_id": root_base.name,
            "started_ns": started_ns,
            "stops_accepting_new_epochs_ns": started_ns + campaign_hours * 3_600_000_000_000,
            "campaign_hours": campaign_hours,
            "window_slots": window_slots,
            "slots_per_day_estimate": slots_per_day_estimate,
            "schedule_interval_hours": interval_hours,
            "seed_base": CAMPAIGN_SEED,
            "seed_policy": "sha256(campaign_seed || UTC hour bucket)",
            "providers": ["helius-free"],
            "public_crosscheck_utc_hours": [0, 6, 12, 18],
            "signature_check": "all selected slots per requested provider",
            "helius_expected_credits_per_window": 2 * window_slots + 2,
            "helius_expected_credits_total": (
                (2 * window_slots + 2) * (campaign_hours // interval_hours)
            ),
            "max_helius_credit_reservations": HELIUS_CREDIT_CAP,
            "max_helius_slots": window_slots * campaign_hours // interval_hours,
            "max_public_pair_slots": window_slots * (campaign_hours // 6),
            "max_new_raw_bytes": MAX_CAMPAIGN_RAW_BYTES,
            "max_single_epoch_raw_bytes": MAX_EPOCH_RAW_BYTES,
            "expected_public_crosscheck_hours_utc": [0, 6, 12, 18],
            "helius_request_pace_seconds": 0.35,
            "public_shared_request_rate_limit_per_second": 1,
            "completeness": "sampled finalized slots only; all unselected slots unknown",
        }
        _create(campaign_path, campaign)
    campaign_stop_path = root_base / "CAMPAIGN_STOP"
    if campaign_stop_path.exists():
        return _read(campaign_stop_path)
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
        if time.time_ns() >= campaign["stops_accepting_new_epochs_ns"]:
            event = {"status": "campaign_window_expired", "bucket": bucket}
            _create(campaign_stop_path, event)
            return event
        usage = campaign_usage(root_base)
        providers_for_bucket = (
            PROVIDERS if int(bucket[9:11]) in (0, 6, 12, 18) else ("helius-free",)
        )
        next_public_slots = window_slots if "solana-public" in providers_for_bucket else 0
        expected_next_helius_credits = 2 * window_slots + 2
        cap_hit = (
            usage["helius_credits"] + expected_next_helius_credits
            > min(HELIUS_CREDIT_CAP, int(campaign["max_helius_credit_reservations"]))
            or usage["helius_slots"] + window_slots > int(campaign["max_helius_slots"])
            or usage["public_pair_slots"] + next_public_slots
            > int(campaign["max_public_pair_slots"])
            or usage["raw_bytes"] + MAX_EPOCH_RAW_BYTES > int(campaign["max_new_raw_bytes"])
        )
        if cap_hit:
            event = {"status": "campaign_cap_reached", "usage": usage}
            _create(campaign_stop_path, event)
            return event
        previous = sorted(
            path
            for path in root_base.iterdir()
            if path.is_dir() and BUCKET_RE.fullmatch(path.name) and path != root
        )
        parent = select_parent(previous, initial_parent_root)
        hour = int(bucket[9:11])
        providers = PROVIDERS if hour in (0, 6, 12, 18) else ("helius-free",)
        seed = hashlib.sha256(f"{CAMPAIGN_SEED}:{bucket}".encode()).hexdigest()
        summary = start_epoch(
            root,
            window_slots=window_slots,
            old_epoch_root=parent,
            providers=providers,
            slots_per_hour_estimate=(slots_per_day_estimate + 23) // 24,
            sampling_seed=seed,
            signature_check_all_slots=True,
        )
        event = {
            "status": "epoch_finished",
            "bucket": bucket,
            "root": str(root),
            "window_slots": window_slots,
            "providers": providers,
            "sampling_seed": seed,
            "summary": summary,
        }
        _create(root_base / f"{bucket}.schedule-result.json", event)
        return event


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root-base", type=Path, required=True)
    parser.add_argument("--window-slots", type=int, default=64)
    parser.add_argument("--interval-hours", type=int, default=1)
    parser.add_argument("--initial-parent-root", type=Path)
    parser.add_argument("--slots-per-day-estimate", type=int, default=321_661)
    parser.add_argument("--campaign-hours", type=int, default=72)
    args = parser.parse_args()
    if not 1 <= args.window_slots <= 64:
        parser.error("window_slots_outside_bound")
    try:
        result = run_bucket(
            args.root_base,
            window_slots=args.window_slots,
            interval_hours=args.interval_hours,
            initial_parent_root=args.initial_parent_root,
            slots_per_day_estimate=args.slots_per_day_estimate,
            campaign_hours=args.campaign_hours,
        )
        from memecoin_ai.domain import canonical

        print(canonical(result), flush=True)
        return 0
    except Exception as exc:
        print(f"block epoch scheduler failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
