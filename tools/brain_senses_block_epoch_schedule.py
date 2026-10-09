#!/usr/bin/env python3
"""Run one seeded finalized-block epoch per UTC scheduling bucket."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import re
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.brain_senses_block_epoch import PROVIDERS, start_epoch  # noqa: E402
from tools.brain_senses_program_watch import _create, _read  # noqa: E402

BUCKET_RE = re.compile(r"^\d{8}T\d{2}0000Z$")
CAMPAIGN_SEED = "brain-senses-helius-hourly-sample-20261009"
HELIUS_CREDIT_CAP = 45_000
MAX_CAMPAIGN_RAW_BYTES = 100 * 1024**3
MAX_CAMPAIGN_STORED_BYTES = 16 * 1024**3
MAX_EPOCH_RAW_BYTES = 2_000_000_000
EXPECTED_EPOCH_STORED_BYTES = 200 * 1024**2


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
    helius_credits = raw_bytes = stored_bytes = helius_slots = public_pair_slots = (
        alchemy_pair_slots
    ) = public_windows = 0
    for epoch in root_base.iterdir():
        if not epoch.is_dir() or not BUCKET_RE.fullmatch(epoch.name):
            continue
        plan_path = epoch / "epoch-plan.json"
        window_path = epoch / "window-plan.json"
        if plan_path.exists() and window_path.exists():
            plan = _read(plan_path)
            window = _read(window_path)
            start, end = window["selected_slot_range_exclusive"]
            sampled_slots = window.get("full_sample_slots")
            sample_count = len(sampled_slots) if isinstance(sampled_slots, list) else end - start
            helius_slots += sample_count
            if "solana-public" in plan.get("providers", []):
                public_pair_slots += sample_count
                public_windows += 1
            alchemy_pair_slots += len(
                window.get("provider_full_sample_slots", {}).get("alchemy-free", [])
            )
        for result_path in epoch.glob("**/request-results/*.json"):
            result = _read(result_path)
            raw_bytes += int(result.get("response_bytes") or 0)
        for receipt_path in epoch.glob("**/captures/**/receipt.json"):
            receipt = _read(receipt_path)
            storage = receipt.get("storage")
            stored_bytes += int(
                storage.get("bytes")
                if isinstance(storage, dict)
                else receipt.get("response_bytes") or 0
            )
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
        "stored_bytes": stored_bytes,
        "helius_slots": helius_slots,
        "public_pair_slots": public_pair_slots,
        "public_windows": public_windows,
        "alchemy_pair_slots": alchemy_pair_slots,
    }


def shared_helius_reservations_for_utc_day(
    roots: tuple[Path, ...], utc_day: str
) -> dict[str, Any]:
    day_start = int(datetime.strptime(utc_day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp())
    day_end = day_start + 86_400
    by_root: dict[str, int] = {}
    for root in roots:
        if not root.exists():
            continue
        count = 0
        watch_root = "helius-watch" in root.name
        for request_path in root.glob("**/requests/*.json"):
            try:
                stat = request_path.stat()
                if not day_start <= int(stat.st_mtime) < day_end:
                    continue
                request = _read(request_path)
            except (OSError, ValueError, TypeError):
                continue
            if request.get("provider") == "helius-free" or (
                watch_root and request.get("method")
            ):
                count += int(
                    request.get("credit_reservation")
                    or request.get("expected_credit_reservation")
                    or 1
                )
        by_root[str(root)] = count
    return {"utc_day": utc_day, "by_root": by_root, "total": sum(by_root.values())}


def run_bucket(
    root_base: Path,
    *,
    window_slots: int = 64,
    interval_hours: int = 1,
    initial_parent_root: Path | None = None,
    slots_per_day_estimate: int = 321_661,
    campaign_hours: int = 72,
    capture_storage_codec: str = "raw",
    stratum_count: int = 0,
    paired_provider: str | None = None,
    paired_slots_per_stratum: int = 0,
    public_crosscheck_utc_hours: tuple[int, ...] = (0, 6, 12, 18),
    helius_credit_cap: int = HELIUS_CREDIT_CAP,
    max_new_raw_bytes: int = MAX_CAMPAIGN_RAW_BYTES,
    max_new_stored_bytes: int = MAX_CAMPAIGN_STORED_BYTES,
    max_single_epoch_raw_bytes: int = MAX_EPOCH_RAW_BYTES,
    max_single_epoch_stored_bytes: int = 1_000_000_000,
    expected_epoch_stored_bytes: int = EXPECTED_EPOCH_STORED_BYTES,
    minimum_free_disk_bytes: int = 0,
    shared_helius_daily_credit_cap: int = 0,
    shared_helius_reservation_roots: tuple[Path, ...] = (),
) -> dict:
    now = datetime.now(UTC)
    bucket = utc_bucket(now, interval_hours)
    root_base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not 1 <= window_slots <= 512:
        raise ValueError("window_slots_outside_bound")
    if not 20 <= campaign_hours <= 168:
        raise ValueError("campaign_hours_outside_bound")
    if slots_per_day_estimate < 24 * window_slots:
        raise ValueError("slots_per_day_estimate_too_small")
    if interval_hours not in (1, 2, 3, 4, 6, 8, 12, 24):
        raise ValueError("unsupported_interval_hours")
    if not 0 <= stratum_count <= window_slots or (stratum_count and window_slots % stratum_count):
        raise ValueError("invalid_stratum_count")
    if paired_provider is not None and paired_provider not in ("alchemy-free", "alchemy-payg"):
        raise ValueError("unsupported_paired_provider")
    if paired_slots_per_stratum < 0 or (paired_provider is None and paired_slots_per_stratum):
        raise ValueError("invalid_paired_slots_per_stratum")
    if paired_provider is not None and (not stratum_count or paired_slots_per_stratum < 1):
        raise ValueError("paired_provider_requires_stratified_selection")
    if (
        not 1 <= helius_credit_cap <= 1_000_000
        or minimum_free_disk_bytes < 0
        or not 0 <= shared_helius_daily_credit_cap <= 1_000_000
    ):
        raise ValueError("campaign_resource_bound_outside_range")
    if any(hour not in range(24) for hour in public_crosscheck_utc_hours):
        raise ValueError("invalid_public_crosscheck_hour")
    campaign_path = root_base / "campaign.json"
    if campaign_path.exists():
        campaign = _read(campaign_path)
        bindings = [
            campaign.get(key) != expected
            for key, expected in (
                ("window_slots", window_slots),
                ("schedule_interval_hours", interval_hours),
                ("campaign_hours", campaign_hours),
                ("slots_per_day_estimate", slots_per_day_estimate),
                ("capture_storage_codec", capture_storage_codec),
            )
        ]
        if "stratum_count" in campaign:
            bindings.append(campaign.get("stratum_count") != stratum_count)
        elif stratum_count:
            bindings.append(True)
        if "public_crosscheck_utc_hours" in campaign:
            bindings.append(
                campaign.get("public_crosscheck_utc_hours") != list(public_crosscheck_utc_hours)
            )
        elif tuple(public_crosscheck_utc_hours) != (0, 6, 12, 18):
            bindings.append(True)
        if campaign.get("campaign_config_version") == 2:
            bindings.extend(
                campaign.get(key) != expected
                for key, expected in (
                    ("paired_provider", paired_provider),
                    ("paired_slots_per_stratum", paired_slots_per_stratum),
                    ("helius_credit_cap", helius_credit_cap),
                    ("max_new_raw_bytes", max_new_raw_bytes),
                    ("max_new_stored_bytes", max_new_stored_bytes),
                    ("max_single_epoch_raw_bytes", max_single_epoch_raw_bytes),
                    ("max_single_epoch_stored_bytes", max_single_epoch_stored_bytes),
                    ("expected_epoch_stored_bytes", expected_epoch_stored_bytes),
                    ("minimum_free_disk_bytes", minimum_free_disk_bytes),
                    ("shared_helius_daily_credit_cap", shared_helius_daily_credit_cap),
                    (
                        "shared_helius_reservation_roots",
                        [str(root) for root in shared_helius_reservation_roots],
                    ),
                )
            )
        elif any(
            (
                paired_provider is not None,
                paired_slots_per_stratum,
                helius_credit_cap != HELIUS_CREDIT_CAP,
                max_new_raw_bytes != MAX_CAMPAIGN_RAW_BYTES,
                max_new_stored_bytes != MAX_CAMPAIGN_STORED_BYTES,
                max_single_epoch_raw_bytes != MAX_EPOCH_RAW_BYTES,
                max_single_epoch_stored_bytes != 1_000_000_000,
                expected_epoch_stored_bytes != EXPECTED_EPOCH_STORED_BYTES,
                minimum_free_disk_bytes,
                shared_helius_daily_credit_cap,
                shared_helius_reservation_roots,
            )
        ):
            bindings.append(True)
        if any(bindings):
            raise ValueError("campaign_binding_changed")
    else:
        started_ns = time.time_ns()
        campaign = {
            "schema_version": 1,
            "campaign_config_version": 2,
            "campaign_id": root_base.name,
            "started_ns": started_ns,
            "stops_accepting_new_epochs_ns": started_ns + campaign_hours * 3_600_000_000_000,
            "campaign_hours": campaign_hours,
            "window_slots": window_slots,
            "slots_per_day_estimate": slots_per_day_estimate,
            "schedule_interval_hours": interval_hours,
            "seed_base": (
                CAMPAIGN_SEED if stratum_count == 0 else f"{CAMPAIGN_SEED}:strata-{stratum_count}"
            ),
            "seed_policy": "sha256(campaign_seed || UTC hour bucket)",
            "providers": ["helius-free", paired_provider]
            if paired_provider is not None
            else ["helius-free"],
            "paired_provider": paired_provider,
            "paired_slots_per_stratum": paired_slots_per_stratum,
            "public_crosscheck_utc_hours": list(public_crosscheck_utc_hours),
            "stratum_count": stratum_count,
            "signature_check": "all selected slots per requested provider",
            "capture_storage_codec": capture_storage_codec,
            "helius_expected_credits_per_window": 2 * window_slots + 2,
            "helius_expected_credits_total": (
                (2 * window_slots + 2) * (campaign_hours // interval_hours)
            ),
            "max_helius_credit_reservations": helius_credit_cap,
            "max_helius_slots": window_slots * campaign_hours // interval_hours,
            "max_alchemy_pair_slots": (
                paired_slots_per_stratum * stratum_count * campaign_hours // interval_hours
            ),
            "max_public_pair_slots": window_slots * (campaign_hours // 6),
            "max_new_raw_bytes": max_new_raw_bytes,
            "max_new_stored_bytes": max_new_stored_bytes,
            "max_single_epoch_raw_bytes": max_single_epoch_raw_bytes,
            "max_single_epoch_stored_bytes": max_single_epoch_stored_bytes,
            "max_single_epoch_expected_stored_bytes": expected_epoch_stored_bytes,
            "minimum_free_disk_bytes": minimum_free_disk_bytes,
            "shared_helius_daily_credit_cap": shared_helius_daily_credit_cap,
            "shared_helius_reservation_roots": [
                str(root) for root in shared_helius_reservation_roots
            ],
            "expected_public_crosscheck_hours_utc": list(public_crosscheck_utc_hours),
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
            ("helius-free", paired_provider)
            if paired_provider is not None
            else PROVIDERS
            if int(bucket[9:11]) in public_crosscheck_utc_hours
            else ("helius-free",)
        )
        next_public_slots = window_slots if "solana-public" in providers_for_bucket else 0
        expected_next_helius_credits = 2 * window_slots + 2
        paired_per_window = paired_slots_per_stratum * stratum_count
        disk_free = shutil.disk_usage(root_base).free
        utc_day = now.strftime("%Y-%m-%d")
        shared_roots = tuple(dict.fromkeys((root_base, *shared_helius_reservation_roots)))
        shared_usage = shared_helius_reservations_for_utc_day(shared_roots, utc_day)
        cap_hit = (
            usage["helius_credits"] + expected_next_helius_credits
            > int(campaign["max_helius_credit_reservations"])
            or usage["helius_slots"] + window_slots > int(campaign["max_helius_slots"])
            or usage["alchemy_pair_slots"] + paired_per_window
            > int(campaign.get("max_alchemy_pair_slots", 0))
            or usage["public_pair_slots"] + next_public_slots
            > int(campaign["max_public_pair_slots"])
            or usage["raw_bytes"]
            + int(campaign.get("max_single_epoch_raw_bytes", MAX_EPOCH_RAW_BYTES))
            > int(campaign["max_new_raw_bytes"])
            or usage["stored_bytes"]
            + int(
                campaign.get(
                    "max_single_epoch_expected_stored_bytes", EXPECTED_EPOCH_STORED_BYTES
                )
            )
            > int(campaign.get("max_new_stored_bytes", campaign["max_new_raw_bytes"]))
            or (
                int(campaign.get("minimum_free_disk_bytes", 0)) > 0
                and disk_free
                < int(campaign["minimum_free_disk_bytes"])
                + int(campaign["max_single_epoch_stored_bytes"])
                + 32_000_000
            )
            or (
                int(campaign.get("shared_helius_daily_credit_cap", 0)) > 0
                and shared_usage["total"] + expected_next_helius_credits
                > int(campaign["shared_helius_daily_credit_cap"])
            )
        )
        if cap_hit:
            event = {
                "status": "campaign_cap_reached",
                "usage": usage,
                "shared_helius_usage": shared_usage,
                "disk_free_bytes": disk_free,
                "minimum_free_disk_bytes": campaign.get("minimum_free_disk_bytes", 0),
            }
            _create(campaign_stop_path, event)
            return event
        previous = sorted(
            path
            for path in root_base.iterdir()
            if path.is_dir() and BUCKET_RE.fullmatch(path.name) and path != root
        )
        parent = select_parent(previous, initial_parent_root)
        hour = int(bucket[9:11])
        providers = (
            ("helius-free", paired_provider)
            if paired_provider is not None
            else PROVIDERS
            if hour in public_crosscheck_utc_hours
            else ("helius-free",)
        )
        seed = hashlib.sha256(f"{campaign['seed_base']}:{bucket}".encode()).hexdigest()
        summary = start_epoch(
            root,
            window_slots=window_slots,
            old_epoch_root=parent,
            providers=providers,
            slots_per_hour_estimate=(slots_per_day_estimate + 23) // 24,
            sampling_seed=seed,
            signature_check_all_slots=True,
            stratum_count=stratum_count,
            paired_provider=paired_provider,
            paired_slots_per_stratum=paired_slots_per_stratum,
            max_capture_response_bytes=int(
                campaign.get("max_single_epoch_raw_bytes", MAX_EPOCH_RAW_BYTES)
            ),
            max_capture_stored_bytes=int(
                campaign.get("max_single_epoch_stored_bytes", 1_000_000_000)
            ),
            capture_storage_codec=capture_storage_codec,
        )
        event = {
            "status": "epoch_finished",
            "bucket": bucket,
            "root": str(root),
            "window_slots": window_slots,
            "providers": providers,
            "sampling_seed": seed,
            "shared_helius_usage_before_epoch": shared_usage,
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
    parser.add_argument("--capture-storage-codec", choices=("raw", "zstd"), default="raw")
    parser.add_argument("--stratum-count", type=int, default=0)
    parser.add_argument("--paired-provider", choices=("alchemy-free", "alchemy-payg"))
    parser.add_argument("--paired-slots-per-stratum", type=int, default=0)
    parser.add_argument("--helius-credit-cap", type=int, default=HELIUS_CREDIT_CAP)
    parser.add_argument("--max-new-raw-bytes", type=int, default=MAX_CAMPAIGN_RAW_BYTES)
    parser.add_argument("--max-new-stored-bytes", type=int, default=MAX_CAMPAIGN_STORED_BYTES)
    parser.add_argument("--max-single-epoch-raw-bytes", type=int, default=MAX_EPOCH_RAW_BYTES)
    parser.add_argument("--max-single-epoch-stored-bytes", type=int, default=1_000_000_000)
    parser.add_argument(
        "--expected-epoch-stored-bytes", type=int, default=EXPECTED_EPOCH_STORED_BYTES
    )
    parser.add_argument("--minimum-free-disk-bytes", type=int, default=0)
    parser.add_argument("--shared-helius-daily-credit-cap", type=int, default=0)
    parser.add_argument("--shared-helius-reservation-root", type=Path, action="append", default=[])
    parser.add_argument(
        "--public-crosscheck-hours",
        type=int,
        nargs="*",
        default=[0, 6, 12, 18],
    )
    args = parser.parse_args()
    if not 1 <= args.window_slots <= 512:
        parser.error("window_slots_outside_bound")
    try:
        result = run_bucket(
            args.root_base,
            window_slots=args.window_slots,
            interval_hours=args.interval_hours,
            initial_parent_root=args.initial_parent_root,
            slots_per_day_estimate=args.slots_per_day_estimate,
            campaign_hours=args.campaign_hours,
            capture_storage_codec=args.capture_storage_codec,
            stratum_count=args.stratum_count,
            paired_provider=args.paired_provider,
            paired_slots_per_stratum=args.paired_slots_per_stratum,
            public_crosscheck_utc_hours=tuple(args.public_crosscheck_hours),
            helius_credit_cap=args.helius_credit_cap,
            max_new_raw_bytes=args.max_new_raw_bytes,
            max_new_stored_bytes=args.max_new_stored_bytes,
            max_single_epoch_raw_bytes=args.max_single_epoch_raw_bytes,
            max_single_epoch_stored_bytes=args.max_single_epoch_stored_bytes,
            expected_epoch_stored_bytes=args.expected_epoch_stored_bytes,
            minimum_free_disk_bytes=args.minimum_free_disk_bytes,
            shared_helius_daily_credit_cap=args.shared_helius_daily_credit_cap,
            shared_helius_reservation_roots=tuple(args.shared_helius_reservation_root),
        )
        from memecoin_ai.domain import canonical

        print(canonical(result), flush=True)
        return 0
    except Exception as exc:
        print(f"block epoch scheduler failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
