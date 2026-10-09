#!/usr/bin/env python3
"""Finite scheduler/frontier journal for the inventory-first Senses epoch v2."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memecoin_ai.domain import canonical  # noqa: E402
from memecoin_ai.sources import verify_capture_storage_codec  # noqa: E402
from tools.brain_senses_block_epoch_schedule import BUCKET_RE, utc_bucket  # noqa: E402
from tools.brain_senses_block_epoch_v2 import (  # noqa: E402
    MAX_GET_BLOCKS_WIDTH,
    MAX_RESPONSE_BYTES,
    PROVIDER,
    build_epoch_plan,
    run_epoch,
)
from tools.brain_senses_program_watch import _create, _read, _replace  # noqa: E402


def _hash_object(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _bucket_time(bucket: str) -> datetime:
    if not BUCKET_RE.fullmatch(bucket):
        raise ValueError("invalid_epoch_bucket")
    return datetime.strptime(bucket, "%Y%m%dT%H0000Z").replace(tzinfo=UTC)


def _next_bucket(bucket: str, interval_hours: int) -> str:
    return utc_bucket(_bucket_time(bucket) + timedelta(hours=interval_hours), interval_hours)


def _schedule_manifest(
    *,
    schedule_id: str,
    interval_hours: int,
    frame_width_cap: int,
    selection_count: int,
    seed_base: str,
    request_budget_per_epoch: int,
    raw_byte_budget_per_epoch: int,
    stored_byte_budget_per_epoch: int,
    max_total_requests: int,
    max_total_raw_bytes: int,
    max_total_stored_bytes: int,
    max_epochs: int,
    minimum_free_disk_bytes: int,
    capture_storage_codec: str,
) -> dict[str, Any]:
    if not schedule_id or not seed_base:
        raise ValueError("schedule_identity_required")
    if interval_hours not in (1, 2, 3, 4, 6, 8, 12, 24):
        raise ValueError("unsupported_interval_hours")
    if not 1 <= frame_width_cap <= MAX_GET_BLOCKS_WIDTH:
        raise ValueError("frame_width_cap_outside_getblocks_bound")
    if not 0 <= selection_count <= frame_width_cap:
        raise ValueError("selection_count_outside_bound")
    if (
        min(
            request_budget_per_epoch,
            raw_byte_budget_per_epoch,
            stored_byte_budget_per_epoch,
            max_total_requests,
            max_total_raw_bytes,
            max_total_stored_bytes,
            max_epochs,
        )
        < 1
        or minimum_free_disk_bytes < 0
    ):
        raise ValueError("schedule_budget_outside_bound")
    if request_budget_per_epoch > max_total_requests:
        raise ValueError("epoch_request_budget_exceeds_campaign_cap")
    verify_capture_storage_codec(capture_storage_codec)
    return {
        "schema_version": 2,
        "protocol": "senses-finalized-inventory-first-v2",
        "schedule_id": schedule_id,
        "provider": PROVIDER,
        "interval_hours": interval_hours,
        "frame_width_cap": frame_width_cap,
        "selection_count": selection_count,
        "seed_base": seed_base,
        "request_budget_per_epoch": request_budget_per_epoch,
        "raw_byte_budget_per_epoch": raw_byte_budget_per_epoch,
        "stored_byte_budget_per_epoch": stored_byte_budget_per_epoch,
        "max_total_requests": max_total_requests,
        "max_total_raw_bytes": max_total_raw_bytes,
        "max_total_stored_bytes": max_total_stored_bytes,
        "max_epochs": max_epochs,
        "minimum_free_disk_bytes": minimum_free_disk_bytes,
        "capture_storage_codec": capture_storage_codec,
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "scheduler_source_version": "brain_senses_block_epoch_v2_schedule.py@2",
        "scheduler_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scheduler_source_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "frontier_rule": (
            "failed_anchor_no_advance; committed_frame_advances_through_terminal_outcome"
        ),
        "inventory_rule": "exact_finalized_getblocks_frame_then_sample_only_returned_members",
        "created_ns": time.time_ns(),
    }


def _usage(epochs_root: Path) -> dict[str, int]:
    requests = raw = stored = reserved_raw = reserved_stored = 0
    for epoch in epochs_root.iterdir() if epochs_root.exists() else []:
        if not epoch.is_dir() or not BUCKET_RE.fullmatch(epoch.name):
            continue
        for request_path in (epoch / "requests").glob("*.json"):
            requests += 1
            reservation = _read(request_path)
            result_path = epoch / "request-results" / request_path.name
            if not result_path.exists():
                reserved_raw += int(reservation.get("reserved_response_bytes") or 0)
                reserved_stored += int(reservation.get("reserved_stored_response_bytes") or 0)
            else:
                row = _read(result_path)
                if row.get("capture_persistence_status") == "failed":
                    reserved_stored += int(reservation.get("reserved_stored_response_bytes") or 0)
        for result_path in (epoch / "request-results").glob("*.json"):
            row = _read(result_path)
            raw += int(row.get("response_bytes") or 0)
            stored += int(row.get("stored_response_bytes") or 0)
    return {
        "requests": requests,
        "raw_bytes": raw,
        "stored_bytes": stored,
        "reserved_raw_bytes": reserved_raw,
        "reserved_stored_bytes": reserved_stored,
    }


def _frontier(path: Path) -> dict[str, Any]:
    if path.exists():
        value = _read(path)
        if value.get("schema_version") != 2:
            raise ValueError("frontier_schema_mismatch")
        return value
    return {
        "schema_version": 2,
        "next_start": None,
        "last_applied_bucket": None,
        "updated_ns": time.time_ns(),
    }


def _apply_terminal(frontier: dict[str, Any], bucket: str, summary: dict[str, Any]) -> None:
    last = frontier.get("last_applied_bucket")
    if last is not None and bucket <= last:
        return
    advance = summary.get("advance_to")
    if advance is not None:
        if type(advance) is not int or advance < 1:
            raise ValueError("terminal_frontier_invalid")
        current = frontier.get("next_start")
        if current is not None and advance < current:
            raise ValueError("terminal_frontier_regression")
        frontier["next_start"] = advance
    if summary.get("bootstrap_receipt_ns") is not None:
        frontier["bootstrap_slot"] = summary.get("bootstrap_slot")
        frontier["bootstrap_receipt_ns"] = summary["bootstrap_receipt_ns"]
    frontier["last_applied_bucket"] = bucket
    frontier["last_terminal_status"] = summary.get("status")
    frontier["updated_ns"] = time.time_ns()


def _reconcile_prior_epochs(
    epochs_root: Path,
    current_bucket: str,
    frontier: dict[str, Any],
    frontier_path: Path,
    clients: dict[str, Any] | None,
) -> None:
    for epoch in (
        sorted(
            path
            for path in epochs_root.iterdir()
            if path.is_dir() and BUCKET_RE.fullmatch(path.name) and path.name < current_bucket
        )
        if epochs_root.exists()
        else []
    ):
        if frontier.get("last_applied_bucket") and epoch.name <= frontier["last_applied_bucket"]:
            continue
        plan_path = epoch / "epoch-plan.json"
        if not plan_path.exists():
            continue
        summary = run_epoch(epoch, _read(plan_path), clients=clients)
        _apply_terminal(frontier, epoch.name, summary)
        _replace(frontier_path, frontier)


def _run_bucket_locked(
    root_base: Path,
    *,
    schedule_id: str,
    interval_hours: int = 1,
    frame_width_cap: int = 512,
    selection_count: int = 32,
    seed_base: str = "senses-inventory-first-v2",
    request_budget_per_epoch: int = 70,
    raw_byte_budget_per_epoch: int = 1_000_000_000,
    stored_byte_budget_per_epoch: int = 1_000_000_000,
    max_total_requests: int = 10_000,
    max_total_raw_bytes: int = 20_000_000_000,
    max_total_stored_bytes: int = 20_000_000_000,
    max_epochs: int = 168,
    minimum_free_disk_bytes: int = 0,
    capture_storage_codec: str = "raw",
    now: datetime | None = None,
    clients: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one schedule bucket; all acquisition can be replaced with injected fixture clients."""
    root_base.mkdir(parents=True, exist_ok=True, mode=0o700)
    epoch_hours = now or datetime.now(UTC)
    bucket = utc_bucket(epoch_hours, interval_hours)
    manifest = _schedule_manifest(
        schedule_id=schedule_id,
        interval_hours=interval_hours,
        frame_width_cap=frame_width_cap,
        selection_count=selection_count,
        seed_base=seed_base,
        request_budget_per_epoch=request_budget_per_epoch,
        raw_byte_budget_per_epoch=raw_byte_budget_per_epoch,
        stored_byte_budget_per_epoch=stored_byte_budget_per_epoch,
        max_total_requests=max_total_requests,
        max_total_raw_bytes=max_total_raw_bytes,
        max_total_stored_bytes=max_total_stored_bytes,
        max_epochs=max_epochs,
        minimum_free_disk_bytes=minimum_free_disk_bytes,
        capture_storage_codec=capture_storage_codec,
    )
    schedule_path = root_base / "schedule-v2.json"
    if schedule_path.exists():
        existing = _read(schedule_path)
        expected = dict(manifest, created_ns=existing.get("created_ns"))
        if existing != expected:
            raise ValueError("schedule_binding_changed")
        manifest = existing
    else:
        _create(schedule_path, manifest)
    schedule_stop = root_base / "STOP"
    if schedule_stop.exists():
        return {"status": "campaign_stopped", **_read(schedule_stop)}
    schedule_hash = _hash_object(manifest)
    epochs_root = root_base / "epochs"
    epochs_root.mkdir(exist_ok=True, mode=0o700)
    frontier_path = root_base / "frontier-v2.json"
    frontier = _frontier(frontier_path)
    epoch_root = epochs_root / bucket
    existing_plan_path = epoch_root / "epoch-plan.json"
    if existing_plan_path.exists():
        plan = _read(existing_plan_path)
    else:
        _reconcile_prior_epochs(epochs_root, bucket, frontier, frontier_path, clients)
        usage = _usage(epochs_root)
        if (
            len([p for p in epochs_root.iterdir() if p.is_dir() and BUCKET_RE.fullmatch(p.name)])
            >= max_epochs
        ):
            _create(
                root_base / "STOP",
                {"schema_version": 2, "reason": "campaign_epoch_cap", "bucket": bucket},
            )
            return {"status": "campaign_stopped", "reason": "campaign_epoch_cap"}
        if shutil.disk_usage(root_base).free < minimum_free_disk_bytes:
            _create(
                root_base / "STOP",
                {"schema_version": 2, "reason": "minimum_free_disk_floor", "bucket": bucket},
            )
            return {"status": "campaign_stopped", "reason": "minimum_free_disk_floor"}
        remaining_requests = max_total_requests - usage["requests"]
        remaining_raw = max_total_raw_bytes - usage["raw_bytes"] - usage["reserved_raw_bytes"]
        remaining_stored = (
            max_total_stored_bytes - usage["stored_bytes"] - usage["reserved_stored_bytes"]
        )
        if min(remaining_requests, remaining_raw, remaining_stored) <= 0:
            _create(
                root_base / "STOP",
                {"schema_version": 2, "reason": "campaign_budget_exhausted", "bucket": bucket},
            )
            return {"status": "campaign_stopped", "reason": "campaign_budget_exhausted"}
        request_budget = min(request_budget_per_epoch, remaining_requests)
        raw_budget = min(raw_byte_budget_per_epoch, remaining_raw)
        stored_budget = min(stored_byte_budget_per_epoch, remaining_stored)
        if raw_budget < MAX_RESPONSE_BYTES or stored_budget < MAX_RESPONSE_BYTES:
            _create(
                root_base / "STOP",
                {
                    "schema_version": 2,
                    "reason": "campaign_cannot_reserve_one_response",
                    "bucket": bucket,
                },
            )
            return {"status": "campaign_stopped", "reason": "campaign_cannot_reserve_one_response"}
        last_bucket = frontier.get("last_applied_bucket")
        missed: list[str] = []
        if last_bucket is not None:
            cursor = _next_bucket(last_bucket, interval_hours)
            while cursor < bucket:
                missed.append(cursor)
                cursor = _next_bucket(cursor, interval_hours)
        bootstrap = frontier.get("next_start") is None
        seed = hashlib.sha256(f"{seed_base}\0{bucket}".encode()).hexdigest()
        plan = build_epoch_plan(
            epoch_id=bucket,
            bucket=bucket,
            schedule_id=schedule_id,
            schedule_sha256=schedule_hash,
            seed=seed,
            bootstrap=bootstrap,
            next_start=frontier.get("next_start"),
            frame_width_cap=frame_width_cap,
            selection_count=selection_count,
            request_budget=request_budget,
            raw_byte_budget=raw_budget,
            stored_byte_budget=stored_budget,
            minimum_free_disk_bytes=minimum_free_disk_bytes,
            capture_storage_codec=capture_storage_codec,
        )
        plan["previous_applied_bucket"] = last_bucket
        plan["missed_schedule_buckets"] = missed
        plan["frontier_persisted_ns"] = frontier.get("updated_ns")
    result = run_epoch(epoch_root, plan, clients=clients)
    _apply_terminal(frontier, bucket, result)
    _replace(frontier_path, frontier)
    return {"status": "epoch_finished", "bucket": bucket, "epoch": result, "frontier": frontier}


def run_bucket(root_base: Path, **kwargs: Any) -> dict[str, Any]:
    """Serialize scheduler state/frontier changes with an exclusive per-campaign lock."""
    root_base.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = root_base / ".schedule-v2.lock"
    with lock_path.open("a") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("inventory_first_schedule_already_active") from exc
        return _run_bucket_locked(root_base, **kwargs)


def _parse_now(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--now must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--now must include a timezone")
    return parsed.astimezone(UTC)


def main(argv: list[str] | None = None, *, clients: dict[str, Any] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one inventory-first finalized-block Senses schedule bucket."
    )
    parser.add_argument("--root-base", required=True, type=Path)
    parser.add_argument("--schedule-id", required=True)
    parser.add_argument(
        "--now", type=_parse_now, help="ISO 8601 timestamp with timezone (default: current UTC)"
    )
    parser.add_argument("--interval-hours", required=True, type=int)
    parser.add_argument("--frame-width-cap", required=True, type=int)
    parser.add_argument("--selection-count", required=True, type=int)
    parser.add_argument("--seed-base", required=True)
    parser.add_argument("--request-budget-per-epoch", required=True, type=int)
    parser.add_argument("--raw-byte-budget-per-epoch", required=True, type=int)
    parser.add_argument("--stored-byte-budget-per-epoch", required=True, type=int)
    parser.add_argument("--max-total-requests", required=True, type=int)
    parser.add_argument("--max-total-raw-bytes", required=True, type=int)
    parser.add_argument("--max-total-stored-bytes", required=True, type=int)
    parser.add_argument("--max-epochs", required=True, type=int)
    parser.add_argument("--minimum-free-disk-bytes", required=True, type=int)
    parser.add_argument("--capture-storage-codec", required=True, choices=("raw", "zstd"))
    args = parser.parse_args(argv)
    result = run_bucket(
        args.root_base,
        schedule_id=args.schedule_id,
        interval_hours=args.interval_hours,
        frame_width_cap=args.frame_width_cap,
        selection_count=args.selection_count,
        seed_base=args.seed_base,
        request_budget_per_epoch=args.request_budget_per_epoch,
        raw_byte_budget_per_epoch=args.raw_byte_budget_per_epoch,
        stored_byte_budget_per_epoch=args.stored_byte_budget_per_epoch,
        max_total_requests=args.max_total_requests,
        max_total_raw_bytes=args.max_total_raw_bytes,
        max_total_stored_bytes=args.max_total_stored_bytes,
        max_epochs=args.max_epochs,
        minimum_free_disk_bytes=args.minimum_free_disk_bytes,
        capture_storage_codec=args.capture_storage_codec,
        now=args.now,
        clients=clients,
    )
    print(canonical(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
