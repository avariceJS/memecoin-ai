#!/usr/bin/env python3
"""Finite, reservation-backed successor scheduler for prospective Helius Senses frames."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import shutil
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memecoin_ai.domain import canonical  # noqa: E402
from memecoin_ai.sources import Capture  # noqa: E402
from tools.brain_senses_block_epoch_v2 import (  # noqa: E402
    MAX_RESPONSE_BYTES,
    _validate_journal,
    build_epoch_plan,
    run_epoch,
)
from tools.brain_senses_block_epoch_v2_schedule import _read, _usage  # noqa: E402
from tools.brain_senses_program_watch import _create, _replace  # noqa: E402

SCHEMA_VERSION = 3
PROVIDER = "helius-free"
ATTEMPTS_MAX = 24
EPOCH_DIRS_MAX = 25  # no-RPC predecessor lineage record plus 24 scheduled attempts
REQUESTS_MAX = 817  # conservative all-in ceiling; 34 requests reserved per attempt
REQUESTS_PER_FRAME = 34
# Payload/campaign caps are decimal bytes; the free-disk floor is 50 binary GiB, stored as bytes.
FRAME_RAW_BYTES = 256_000_000
FRAME_STORED_BYTES = 128_000_000
CAMPAIGN_RAW_BYTES = 4_000_000_000
CAMPAIGN_STORED_BYTES = 1_000_000_000
DISK_FLOOR_BYTES = 53_687_091_200
# Worst one-frame metadata mirror, one maximum write temp, and a bounded journal margin.
PEAK_WRITE_MARGIN_BYTES = FRAME_RAW_BYTES + MAX_RESPONSE_BYTES + 2_000_000
FRAME_WIDTH_CAP = 20_000
SELECTION_COUNT = 16
INTERVAL_HOURS = 1
CODE_BINDINGS = (
    "tools/brain_senses_block_epoch_v3_schedule.py",
    "tools/brain_senses_block_epoch_v3_launch.py",
    "tools/brain_senses_block_epoch_v2.py",
    "tools/brain_senses_block_provider_pilot.py",
    "tools/brain_senses_program_watch.py",
    "src/memecoin_ai/sources.py",
    "src/memecoin_ai/domain.py",
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bindings() -> dict[str, str]:
    return {p: _hash(REPO_ROOT / p) for p in CODE_BINDINGS}


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _replace(path, value)


def _attempts(epochs_root: Path) -> list[Path]:
    return (
        sorted(
            p
            for p in epochs_root.iterdir()
            if p.is_dir() and len(p.name) == 16 and p.name.endswith("Z")
        )
        if epochs_root.exists()
        else []
    )


def _usage_v3(epochs_root: Path) -> dict[str, int]:
    totals = {
        "requests": 0,
        "raw_bytes": 0,
        "stored_bytes": 0,
        "reserved_raw_bytes": 0,
        "reserved_stored_bytes": 0,
    }
    base = _usage(epochs_root)
    for key in totals:
        totals[key] = int(base.get(key, 0))
    return totals


def _preflight(
    *,
    attempts: int,
    epoch_dirs: int,
    requests: int,
    raw: int,
    stored: int,
    reserved_raw: int,
    reserved_stored: int,
    free_disk: int,
) -> str | None:
    """Pure full-frame preflight; integer byte/request dimensions only."""
    if attempts >= ATTEMPTS_MAX or epoch_dirs >= EPOCH_DIRS_MAX:
        return "attempt_or_epoch_cap"
    if REQUESTS_MAX - requests < REQUESTS_PER_FRAME:
        return "campaign_cannot_reserve_full_frame_requests"
    if raw + reserved_raw + FRAME_RAW_BYTES > CAMPAIGN_RAW_BYTES:
        return "campaign_cannot_reserve_full_frame_raw_bytes"
    if stored + reserved_stored + FRAME_STORED_BYTES > CAMPAIGN_STORED_BYTES:
        return "campaign_cannot_reserve_full_frame_stored_bytes"
    required_disk = (
        DISK_FLOOR_BYTES + FRAME_RAW_BYTES + FRAME_STORED_BYTES + PEAK_WRITE_MARGIN_BYTES
    )
    if free_disk < required_disk:
        return "minimum_free_disk_floor_plus_frame_envelope"
    return None


def _read_manifest(root: Path, lineage: dict[str, Any]) -> dict[str, Any]:
    config = {
        "schema_version": SCHEMA_VERSION,
        "schedule_id": "helius-inventory-v3-hourly-20261010",
        "protocol": "senses-finalized-inventory-first-v3-scheduler-v2-frames",
        "provider": PROVIDER,
        "interval_hours": INTERVAL_HOURS,
        "frame_width_cap": FRAME_WIDTH_CAP,
        "selection_count": SELECTION_COUNT,
        "request_budget_per_epoch": REQUESTS_PER_FRAME,
        "raw_byte_budget_per_epoch": FRAME_RAW_BYTES,
        "stored_byte_budget_per_epoch": FRAME_STORED_BYTES,
        "max_attempts": ATTEMPTS_MAX,
        "max_epoch_dirs": EPOCH_DIRS_MAX,
        "max_total_requests": REQUESTS_MAX,
        "max_total_raw_bytes": CAMPAIGN_RAW_BYTES,
        "max_total_stored_bytes": CAMPAIGN_STORED_BYTES,
        "minimum_free_disk_bytes": DISK_FLOOR_BYTES,
        "peak_write_margin_bytes": PEAK_WRITE_MARGIN_BYTES,
        "service_policy": {
            "supervisor": "macOS LaunchAgent",
            "start_calendar_interval": {"minute": 5},
            "run_at_load": False,
            "keep_alive": False,
            "schedule_timezone": "host-local calendar trigger; epoch buckets are UTC",
            "terminal_action": "durable STOP then launchctl bootout exact service label",
            "bootout_failure": (
                "next calendar invocation checks STOP and retries teardown without RPC"
            ),
            "crash_recovery": (
                "offline journal/artifact reconciliation then durable STOP; no request replay"
            ),
            "renewal": "none; new campaign ID and separate service launch required",
        },
        "capture_storage_codec": "zstd",
        "retry_policy": (
            "never replay a durable reservation; ambiguous reservation seals and stops campaign"
        ),
        "missed_bucket_policy": "anchor-only observation; record gap; never fetch missed slots",
        "frontier_semantics": "observed anchor and inventoried interval are distinct",
        "predecessor_lineage_sha256": hashlib.sha256(canonical(lineage).encode()).hexdigest(),
        "code_sha256": _bindings(),
        "created_ns": time.time_ns(),
    }
    path = root / "schedule-v3.json"
    if path.exists():
        existing = _read(path)
        for key, value in config.items():
            if key != "created_ns" and existing.get(key) != value:
                raise ValueError("successor_schedule_binding_changed")
        config["created_ns"] = existing["created_ns"]
        return existing
    _create(path, config)
    return config


def _frontier(root: Path, lineage: dict[str, Any]) -> dict[str, Any]:
    path = root / "frontier-v3.json"
    if path.exists():
        value = _read(path)
        if (
            value.get("predecessor_lineage_sha256")
            != hashlib.sha256(canonical(lineage).encode()).hexdigest()
        ):
            raise ValueError("successor_frontier_lineage_mismatch")
        return value
    frontier = {
        "schema_version": SCHEMA_VERSION,
        "predecessor_lineage_sha256": hashlib.sha256(canonical(lineage).encode()).hexdigest(),
        "observed_anchor_slot": lineage["last_observed_anchor_slot"],
        "observed_anchor_receipt_ns": lineage["last_observed_anchor_receipt_ns"],
        "observed_frontier": lineage["last_observed_anchor_slot"] + 1,
        "last_attempt_bucket": lineage["last_attempt_bucket"],
        "last_inventoried_interval": lineage.get("last_inventoried_interval"),
        "gap_intervals": list(lineage.get("gap_intervals", [])),
        "attempt_count": 0,
        "updated_ns": time.time_ns(),
    }
    _create(path, frontier)
    return frontier


def initialize(root: Path, lineage_path: Path) -> dict[str, Any]:
    """Create the no-RPC predecessor epoch and bind the new finite campaign to it."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lineage = _read(lineage_path)
    if lineage.get("schema_version") != 1 or lineage.get("provider") != PROVIDER:
        raise ValueError("predecessor_lineage_invalid")
    manifest = _read_manifest(root, lineage)
    epochs = root / "epochs"
    epochs.mkdir(mode=0o700, exist_ok=True)
    bootstrap = epochs / "predecessor-lineage"
    bootstrap.mkdir(mode=0o700, exist_ok=True)
    lineage_target = bootstrap / "lineage.json"
    if lineage_target.exists():
        if _read(lineage_target) != lineage:
            raise ValueError("predecessor_lineage_changed")
    else:
        _create(lineage_target, lineage)
    _frontier(root, lineage)
    return manifest


def _terminal_health(summary: dict[str, Any]) -> bool:
    return summary.get("status") in {"frame_complete", "bootstrap_complete"}


def _reconcile_epoch(
    epoch: Path, plan: dict[str, Any], reservation: dict[str, Any]
) -> dict[str, Any]:
    """Verify sealed journal and actual artifacts before releasing an active envelope."""
    summary_path = epoch / "epoch-summary.json"
    if not summary_path.is_file():
        raise ValueError("active_epoch_not_terminal")
    if _hash(epoch / "epoch-plan.json") != reservation.get("plan_sha256"):
        raise ValueError("active_epoch_plan_hash_mismatch")
    if _read(epoch / "frame-reservation.json") != {
        key: value
        for key, value in reservation.items()
        if key not in {"actual_requests", "actual_raw_bytes", "actual_stored_bytes", "released_ns"}
    }:
        raise ValueError("active_epoch_reservation_mismatch")
    rows = _validate_journal(epoch, plan)
    raw = stored = 0
    for row in rows:
        if row.get("status") == "captured":
            capture = Capture.load(epoch / row["capture_path"])
            raw += len(capture.response)
            response = epoch / row["capture_path"] / "response.json.zst"
            stored += response.stat().st_size
    summary = _read(summary_path)
    if (
        raw != summary.get("captured_response_bytes")
        or stored != summary.get("stored_response_bytes")
        or raw > reservation["raw_bytes"]
        or stored > reservation["stored_bytes"]
        or len(rows) > reservation["request_count"]
    ):
        raise ValueError("active_epoch_reconciliation_mismatch")
    return summary


def _record_release(epoch: Path, reservation: dict[str, Any], summary: dict[str, Any]) -> None:
    release = {
        "schema_version": SCHEMA_VERSION,
        "bucket": reservation["bucket"],
        "reservation_sha256": _hash(epoch / "frame-reservation.json"),
        "summary_sha256": _hash(epoch / "epoch-summary.json"),
        "actual_requests": summary["request_reservations"],
        "actual_raw_bytes": summary["captured_response_bytes"],
        "actual_stored_bytes": summary["stored_response_bytes"],
        "released_ns": time.time_ns(),
        "policy": (
            "unused active envelope released only after journal, captures and summary reconcile"
        ),
    }
    path = epoch / "frame-reservation-release.json"
    if path.exists():
        if _read(path) != release:
            # A prior release timestamp is immutable; compare all evidence fields.
            existing = _read(path)
            if any(
                existing.get(key) != value for key, value in release.items() if key != "released_ns"
            ):
                raise ValueError("frame_reservation_release_mismatch")
        return
    _create(path, release)


def _seal_stop(root: Path, reason: str, **details: Any) -> dict[str, Any]:
    """Durably record terminal state and a bounded snapshot before service teardown."""
    stop_path = root / "STOP"
    if stop_path.exists():
        return _read(stop_path)
    frontier_path = root / "frontier-v3.json"
    try:
        frontier = _read(frontier_path) if frontier_path.is_file() else {}
        usage = _usage_v3(root / "epochs")
        accounting_error = None
    except Exception as exc:
        frontier = {}
        usage = {"requests": 0, "raw_bytes": 0, "stored_bytes": 0}
        accounting_error = type(exc).__name__
    active_path = root / "active-frame-reservation.json"
    try:
        active = _read(active_path) if active_path.is_file() else None
    except Exception as exc:
        active = None
        accounting_error = accounting_error or type(exc).__name__
    bucket = active.get("bucket") if active else frontier.get("last_attempt_bucket")
    if not isinstance(bucket, str):
        bucket = None
    epoch = root / "epochs" / bucket if bucket else None
    manifest_path = root / "schedule-v3.json"
    summary_path = epoch / "epoch-summary.json" if epoch else None
    reservation_path = epoch / "frame-reservation.json" if epoch else None
    request_started = bool(epoch and list((epoch / "requests").glob("*.json")))
    record = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": "helius-inventory-v3-hourly-20261010",
        "manifest_sha256": _hash(manifest_path) if manifest_path.is_file() else None,
        "predecessor_lineage_sha256": (
            frontier.get("predecessor_lineage_sha256") if frontier else None
        ),
        "reason": reason,
        "time_ns": time.time_ns(),
        "attempt_count": frontier.get("attempt_count", 0),
        "requests": usage["requests"],
        "raw_bytes": usage["raw_bytes"],
        "stored_bytes": usage["stored_bytes"],
        "observed_anchor_slot": frontier.get("observed_anchor_slot"),
        "observed_frontier": frontier.get("observed_frontier"),
        "last_inventoried_interval": frontier.get("last_inventoried_interval"),
        "active_bucket": active.get("bucket") if active else None,
        "active_reservation_status": active.get("status") if active else None,
        "request_started": request_started,
        "epoch_created": bool(epoch and epoch.exists()),
        "epoch_summary_sha256": (
            _hash(summary_path) if summary_path and summary_path.is_file() else None
        ),
        "frame_reservation_sha256": (
            _hash(reservation_path) if reservation_path and reservation_path.is_file() else None
        ),
        "accounting_error_class": accounting_error,
        **details,
    }
    _create(stop_path, record)
    return record


def _offline_reconcile_interrupted(root: Path) -> dict[str, Any]:
    """Reconcile only already sealed local artifacts after a process death; never call RPC."""
    frontier_path = root / "frontier-v3.json"
    active_path = root / "active-frame-reservation.json"
    if not active_path.is_file() or not frontier_path.is_file():
        return {"local_reconciliation": "no_active_frame"}
    active = _read(active_path)
    if active.get("status") not in {"active", "reconciled"}:
        return {"local_reconciliation": "unknown_reservation_status"}
    epoch = root / "epochs" / str(active.get("bucket"))
    plan_path = epoch / "epoch-plan.json"
    if not plan_path.is_file():
        return {"local_reconciliation": "missing_epoch_plan"}
    plan = _read(plan_path)
    try:
        rows = _validate_journal(epoch, plan)
        summary_path = epoch / "epoch-summary.json"
        if not summary_path.is_file():
            return {
                "local_reconciliation": "journal_valid_but_epoch_unsealed",
                "journal_rows": len(rows),
            }
        reservation = _read(epoch / "frame-reservation.json")
        summary = _reconcile_epoch(epoch, plan, reservation)
        if active.get("status") == "active":
            _record_release(epoch, reservation, summary)
            active.update(
                {
                    "status": "reconciled",
                    "actual_requests": summary["request_reservations"],
                    "actual_raw_bytes": summary["captured_response_bytes"],
                    "actual_stored_bytes": summary["stored_response_bytes"],
                    "released_ns": time.time_ns(),
                }
            )
            _replace(active_path, active)
        frontier = _read(frontier_path)
        if frontier.get("last_attempt_bucket") != active.get("bucket"):
            frontier = _advance(frontier, str(active["bucket"]), epoch, summary)
            _replace(frontier_path, frontier)
        return {
            "local_reconciliation": "sealed_epoch_reconciled",
            "epoch_status": summary.get("status"),
            "journal_rows": len(rows),
        }
    except Exception as exc:
        return {
            "local_reconciliation": "failed_closed",
            "error_class": type(exc).__name__,
        }


def _run_bucket_locked(
    root: Path,
    *,
    lineage_path: Path,
    now: datetime | None = None,
    clients: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stop_path = root / "STOP"
    if stop_path.exists():
        return {"status": "campaign_stopped", **_read(stop_path)}
    lineage = _read(lineage_path)
    if lineage.get("schema_version") != 1 or lineage.get("provider") != PROVIDER:
        raise ValueError("predecessor_lineage_invalid")
    manifest = initialize(root, lineage_path)
    frontier_path = root / "frontier-v3.json"
    frontier = _frontier(root, lineage)
    if frontier.get("last_terminal_status") not in (None, "frame_complete", "bootstrap_complete"):
        _seal_stop(
            root,
            "prior_epoch_requires_review_before_new_anchor",
            epoch_status=frontier.get("last_terminal_status"),
        )
        return {
            "status": "campaign_stopped",
            "reason": "prior_epoch_requires_review_before_new_anchor",
        }

    bucket_time = (
        (now or datetime.now(UTC)).astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    )
    bucket = bucket_time.strftime("%Y%m%dT%H0000Z")
    epochs = root / "epochs"
    epoch = epochs / bucket
    active_path = root / "active-frame-reservation.json"

    if active_path.exists() and _read(active_path).get("status") not in {
        "active",
        "reconciled",
    }:
        _seal_stop(root, "active_frame_reservation_status_unknown")
        return {"status": "campaign_stopped", "reason": "active_frame_reservation_status_unknown"}

    if active_path.exists() and _read(active_path).get("status") == "active":
        active = _read(active_path)
        active_epoch = epochs / str(active.get("bucket"))
        plan_path = active_epoch / "epoch-plan.json"
        try:
            plan = _read(plan_path)
            summary_path = active_epoch / "epoch-summary.json"
            if not summary_path.is_file():
                # The process died with an outstanding envelope. Do not replay, even if
                # local files suggest that the request may not have reached the provider.
                record = _seal_stop(
                    root,
                    "process_interrupted_with_active_reservation",
                    recovered_without_rpc=True,
                )
                return {"status": "campaign_stopped", **record}
            summary = _reconcile_epoch(active_epoch, plan, active)
            _record_release(active_epoch, active, summary)
            active.update(
                {
                    "status": "reconciled",
                    "actual_requests": summary["request_reservations"],
                    "actual_raw_bytes": summary["captured_response_bytes"],
                    "actual_stored_bytes": summary["stored_response_bytes"],
                    "released_ns": time.time_ns(),
                }
            )
            _replace(active_path, active)
            frontier = _advance(frontier, str(active["bucket"]), active_epoch, summary)
            _replace(frontier_path, frontier)
            record = _seal_stop(
                root,
                "process_interrupted_after_epoch_sealed",
                recovered_without_rpc=True,
                epoch_status=summary.get("status"),
            )
            return {"status": "campaign_stopped", **record}
        except Exception as exc:
            record = _seal_stop(
                root,
                "active_frame_recovery_failed_closed",
                error_class=type(exc).__name__,
                recovered_without_rpc=True,
            )
            return {"status": "campaign_stopped", **record}

    # A crash after durable release but before the frontier update is reconciled without RPC.
    if active_path.exists():
        released = _read(active_path)
        if released.get("status") == "reconciled" and frontier.get(
            "last_attempt_bucket"
        ) != released.get("bucket"):
            released_bucket = released["bucket"]
            released_epoch = epochs / released_bucket
            released_plan = _read(released_epoch / "epoch-plan.json")
            released_summary = _reconcile_epoch(released_epoch, released_plan, released)
            _record_release(released_epoch, released, released_summary)
            frontier = _advance(frontier, released_bucket, released_epoch, released_summary)
            _replace(frontier_path, frontier)
            if not _terminal_health(released_summary):
                _seal_stop(
                    root,
                    "recovered_epoch_requires_review",
                    epoch_status=released_summary.get("status"),
                )
                return {
                    "status": "campaign_stopped",
                    "reason": "recovered_epoch_requires_review",
                    "epoch": released_summary,
                }

    if len(_attempts(epochs)) != int(frontier.get("attempt_count", -1)):
        _seal_stop(
            root,
            "attempt_directory_frontier_mismatch",
            attempt_directories=len(_attempts(epochs)),
            frontier_attempt_count=frontier.get("attempt_count"),
        )
        return {"status": "campaign_stopped", "reason": "attempt_directory_frontier_mismatch"}

    if epoch.exists():
        # A sealed bucket can be observed repeatedly by launchd without creating another attempt.
        summary_path = epoch / "epoch-summary.json"
        if summary_path.exists():
            summary = _read(summary_path)
            if not _terminal_health(summary):
                _seal_stop(
                    root,
                    "epoch_requires_review_before_next_launch",
                    epoch_status=summary.get("status"),
                )
                return {
                    "status": "campaign_stopped",
                    "reason": "epoch_requires_review_before_next_launch",
                    "epoch": summary,
                }
            return {
                "status": "bucket_already_terminal",
                "bucket": bucket,
                "epoch": summary,
                "frontier": frontier,
            }
        _seal_stop(root, "epoch_exists_without_active_reservation")
        return {"status": "campaign_stopped", "reason": "epoch_exists_without_active_reservation"}

    last_bucket = frontier.get("last_attempt_bucket")
    if last_bucket is not None and bucket <= last_bucket:
        _seal_stop(root, "non_monotonic_schedule_bucket", prior_bucket=last_bucket)
        return {"status": "campaign_stopped", "reason": "non_monotonic_schedule_bucket"}
    missed = _missed_buckets(last_bucket, bucket)
    attempts = int(frontier["attempt_count"])
    epoch_dirs = len(_attempts(epochs)) + 1  # plus predecessor lineage dir
    usage = _usage_v3(epochs)
    reason = _preflight(
        attempts=attempts,
        epoch_dirs=epoch_dirs,
        requests=usage["requests"],
        raw=usage["raw_bytes"],
        stored=usage["stored_bytes"],
        reserved_raw=usage["reserved_raw_bytes"],
        reserved_stored=usage["reserved_stored_bytes"],
        free_disk=shutil.disk_usage(root).free,
    )
    if reason:
        _seal_stop(root, reason, usage=usage)
        return {
            "status": "preflight_stopped_without_epoch_or_rpc",
            "reason": reason,
            "bucket": bucket,
            "usage": usage,
        }

    # Persist the exact request plan before reserving it; no RPC can happen in setup.
    epochs.mkdir(mode=0o700, exist_ok=True)
    epoch.mkdir(mode=0o700)
    gap_reset = bool(missed)
    plan = build_epoch_plan(
        epoch_id=bucket,
        bucket=bucket,
        schedule_id=manifest["schedule_id"],
        schedule_sha256=hashlib.sha256(canonical(manifest).encode()).hexdigest(),
        seed=hashlib.sha256(f"{manifest['schedule_id']}\0{bucket}".encode()).hexdigest(),
        bootstrap=gap_reset,
        next_start=None if gap_reset else frontier["observed_frontier"],
        frame_width_cap=FRAME_WIDTH_CAP,
        selection_count=SELECTION_COUNT,
        request_budget=REQUESTS_PER_FRAME,
        raw_byte_budget=FRAME_RAW_BYTES,
        stored_byte_budget=FRAME_STORED_BYTES,
        minimum_free_disk_bytes=DISK_FLOOR_BYTES,
        capture_storage_codec="zstd",
        max_response_bytes=MAX_RESPONSE_BYTES,
    )
    plan["schedule_sha256"] = hashlib.sha256(canonical(manifest).encode()).hexdigest()
    plan.update(
        {
            "scheduler_version": SCHEMA_VERSION,
            "predecessor_lineage_sha256": manifest["predecessor_lineage_sha256"],
            "missed_schedule_buckets": missed,
            "gap_reset_anchor_only": gap_reset,
        }
    )
    _create(epoch / "epoch-plan.json", plan)
    # Pin the whole-frame reservation before any anchor I/O. A crash keeps it charged.
    reservation = {
        "schema_version": SCHEMA_VERSION,
        "bucket": bucket,
        "plan_sha256": _hash(epoch / "epoch-plan.json"),
        "request_count": REQUESTS_PER_FRAME,
        "raw_bytes": FRAME_RAW_BYTES,
        "stored_bytes": FRAME_STORED_BYTES,
        "peak_disk_bytes": FRAME_RAW_BYTES + FRAME_STORED_BYTES + PEAK_WRITE_MARGIN_BYTES,
        "reserved_ns": time.time_ns(),
        "status": "active",
        "policy": (
            "durable before anchor; release unused envelope only after journal "
            "and file reconciliation"
        ),
    }
    _create(epoch / "frame-reservation.json", reservation)
    if active_path.exists():
        _replace(active_path, reservation)
    else:
        _create(active_path, reservation)
    _create(
        epoch / "attempt-context.json",
        {
            "bucket": bucket,
            "previous_observed_anchor_slot": frontier["observed_anchor_slot"],
            "previous_observed_frontier": frontier["observed_frontier"],
            "last_inventoried_interval": frontier.get("last_inventoried_interval"),
            "missed_schedule_buckets": missed,
            "missed_slot_backfill": False,
            "gap_reset_anchor_only": gap_reset,
            "active_reservation_sha256": _hash(active_path),
        },
    )
    try:
        summary = run_epoch(epoch, plan, clients=clients)
        summary = _reconcile_epoch(epoch, plan, reservation)
    except Exception as exc:
        # The reservation remains active; later process invocations cannot spend it again.
        if not stop_path.exists():
            _seal_stop(
                root,
                "epoch_reconciliation_failed_closed",
                error_class=type(exc).__name__,
            )
        raise

    _record_release(epoch, reservation, summary)
    reservation.update(
        {
            "status": "reconciled",
            "actual_requests": summary["request_reservations"],
            "actual_raw_bytes": summary["captured_response_bytes"],
            "actual_stored_bytes": summary["stored_response_bytes"],
            "released_ns": time.time_ns(),
        }
    )
    _replace(active_path, reservation)
    frontier = _advance(frontier, bucket, epoch, summary)
    _replace(frontier_path, frontier)
    if not _terminal_health(summary):
        _seal_stop(
            root,
            "epoch_requires_review_before_next_launch",
            epoch_status=summary.get("status"),
        )
        return {
            "status": "campaign_stopped",
            "reason": "epoch_requires_review_before_next_launch",
            "epoch": summary,
            "frontier": frontier,
        }
    if frontier["attempt_count"] >= ATTEMPTS_MAX:
        record = _seal_stop(root, "finite_attempt_cap_reached")
        return {"status": "campaign_stopped", **record, "epoch": summary}
    return {"status": "epoch_finished", "bucket": bucket, "epoch": summary, "frontier": frontier}


def _missed_buckets(last_bucket: str | None, bucket: str) -> list[str]:
    if last_bucket is None:
        return []
    previous = datetime.strptime(last_bucket, "%Y%m%dT%H0000Z").replace(tzinfo=UTC)
    current = datetime.strptime(bucket, "%Y%m%dT%H0000Z").replace(tzinfo=UTC)
    missed: list[str] = []
    cursor = previous + timedelta(hours=INTERVAL_HOURS)
    while cursor < current:
        missed.append(cursor.strftime("%Y%m%dT%H0000Z"))
        cursor += timedelta(hours=INTERVAL_HOURS)
    return missed


def _advance(
    frontier: dict[str, Any], bucket: str, epoch: Path, summary: dict[str, Any]
) -> dict[str, Any]:
    value = dict(frontier)
    plan = _read(epoch / "epoch-plan.json")
    rows = _validate_journal(epoch, plan)
    anchors = [
        row for row in rows if row.get("phase") == "anchor" and row.get("status") == "captured"
    ]
    if anchors:
        anchor = anchors[0]
        slot = anchor.get("slot_value")
        if type(slot) is int:
            old_frontier = value["observed_frontier"]
            value["observed_anchor_slot"] = slot
            value["observed_anchor_receipt_ns"] = anchor["receipt_ns"]  # Unix epoch nanoseconds
            value["observed_frontier"] = slot + 1
            if slot + 1 > old_frontier and (
                plan.get("gap_reset_anchor_only") or summary.get("status") == "oversized_frame"
            ):
                value["gap_intervals"].append(
                    {
                        "start": old_frontier,
                        "end_exclusive": slot + 1,
                        "reason": "missed_schedule_no_backfill"
                        if plan.get("gap_reset_anchor_only")
                        else "oversized_frame",
                    }
                )
    if summary.get("inventory_sha256") is not None:
        value["last_inventoried_interval"] = {
            "start": summary.get("start"),
            "end_exclusive": summary.get("end_exclusive"),
            "inventory_sha256": summary["inventory_sha256"],
            "provider_returned_produced_slots": summary.get("inventory_denominator"),
            "anchor_receipt_ns": next((row.get("receipt_ns") for row in anchors), None),
            "structural_checks_passed": sum(
                c.get("status") == "qualified_structural_reference_count"
                for c in summary.get("structural_checks", [])
            ),
            "structural_checks_total": len(summary.get("structural_checks", [])),
        }
    value["last_attempt_bucket"] = bucket
    value["attempt_count"] += 1
    value["last_terminal_status"] = summary.get("status")
    value["updated_ns"] = time.time_ns()
    return value


def run_bucket(
    root: Path,
    *,
    lineage_path: Path,
    now: datetime | None = None,
    clients: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".schedule-v3.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("inventory_first_v3_schedule_already_active") from exc
        stop_path = root / "STOP"
        if stop_path.exists():
            return {"status": "campaign_stopped", **_read(stop_path)}
        in_progress = root / "invocation-in-progress.json"
        if in_progress.exists():
            reconciliation = _offline_reconcile_interrupted(root)
            record = _seal_stop(
                root,
                "process_interrupted_without_terminal_stop",
                recovered_without_rpc=True,
                **reconciliation,
            )
            return {"status": "campaign_stopped", **record}
        _create(
            in_progress,
            {"pid": os.getpid(), "started_ns": time.time_ns(), "policy": "one invocation"},
        )
        try:
            result = _run_bucket_locked(root, lineage_path=lineage_path, now=now, clients=clients)
        except BaseException:
            # Leave the marker after an abrupt/exceptional exit unless STOP is already durable.
            if stop_path.exists():
                in_progress.unlink(missing_ok=True)
                _sync_dir(root)
            raise
        if not in_progress.exists():
            return result
        in_progress.unlink()
        _sync_dir(root)
        return result


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_status(root: Path) -> dict[str, Any]:
    """Read-only local campaign status; no provider call and no mutation."""
    manifest_path = root / "schedule-v3.json"
    frontier_path = root / "frontier-v3.json"
    if not manifest_path.is_file() or not frontier_path.is_file():
        raise ValueError("successor_campaign_not_initialized")
    manifest = _read(manifest_path)
    frontier = _read(frontier_path)
    epochs = root / "epochs"
    attempts = _attempts(epochs)
    summaries = [
        _read(path / "epoch-summary.json")
        for path in attempts
        if (path / "epoch-summary.json").is_file()
    ]
    usage = _usage_v3(epochs)
    active_path = root / "active-frame-reservation.json"
    active = _read(active_path) if active_path.is_file() else None
    active_attempt = next(
        (path for path in attempts if active and path.name == active.get("bucket")), None
    )
    active_requests = (
        len(list((active_attempt / "requests").glob("*.json"))) if active_attempt else 0
    )
    active_raw = active_stored = 0
    if active_attempt:
        for result_path in (active_attempt / "request-results").glob("*.json"):
            row = _read(result_path)
            active_raw += int(row.get("response_bytes") or 0)
            active_stored += int(row.get("stored_response_bytes") or 0)
    held_requests = (
        max(0, int(active.get("request_count", 0)) - active_requests)
        if active and active.get("status") == "active"
        else 0
    )
    held_raw = (
        max(0, int(active.get("raw_bytes", 0)) - active_raw)
        if active and active.get("status") == "active"
        else usage["reserved_raw_bytes"]
    )
    held_stored = (
        max(0, int(active.get("stored_bytes", 0)) - active_stored)
        if active and active.get("status") == "active"
        else usage["reserved_stored_bytes"]
    )
    gap_statuses = {"oversized_frame", "sampled_gaps", "empty_frame"}
    gaps = 0
    for path, summary in zip(
        [p for p in attempts if (p / "epoch-summary.json").is_file()], summaries, strict=True
    ):
        plan = _read(path / "epoch-plan.json")
        gaps += summary.get("status") in gap_statuses or bool(plan.get("missed_schedule_buckets"))
    failed = sum(not _terminal_health(summary) for summary in summaries)
    stop = _read(root / "STOP") if (root / "STOP").is_file() else None
    return {
        "schema_version": SCHEMA_VERSION,
        "schedule_id": manifest["schedule_id"],
        "predecessor_campaign_id": _read(epochs / "predecessor-lineage" / "lineage.json").get(
            "predecessor_campaign_id"
        ),
        "attempts": len(attempts),
        "attempts_remaining": max(0, ATTEMPTS_MAX - len(attempts)),
        "successful_epochs": sum(_terminal_health(summary) for summary in summaries),
        "gapped_epochs": int(gaps),
        "failed_epochs": int(failed),
        "requests": usage["requests"],
        "requests_remaining_after_active_reservation": max(
            0, REQUESTS_MAX - usage["requests"] - held_requests
        ),
        "raw_response_bytes": usage["raw_bytes"],
        "raw_bytes_remaining_after_active_reservation": max(
            0, CAMPAIGN_RAW_BYTES - usage["raw_bytes"] - held_raw
        ),
        "stored_capture_bytes": usage["stored_bytes"],
        "stored_bytes_remaining_after_active_reservation": max(
            0, CAMPAIGN_STORED_BYTES - usage["stored_bytes"] - held_stored
        ),
        "free_disk_bytes": shutil.disk_usage(root).free,
        "minimum_free_disk_bytes": DISK_FLOOR_BYTES,
        "last_attempt_bucket": frontier.get("last_attempt_bucket"),
        "last_terminal_status": frontier.get("last_terminal_status"),
        "observed_anchor_slot": frontier.get("observed_anchor_slot"),
        "observed_frontier": frontier.get("observed_frontier"),
        "last_inventoried_interval": frontier.get("last_inventoried_interval"),
        "gap_intervals": frontier.get("gap_intervals", []),
        "active_frame_status": active.get("status") if active else None,
        "stop_reason": stop.get("reason") if stop else None,
        "provider_charge_units": "unknown_unverified",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-base", required=True, type=Path)
    parser.add_argument("--lineage", type=Path)
    parser.add_argument("--status-only", action="store_true")
    args = parser.parse_args(argv)
    if args.status_only:
        result = read_status(args.root_base)
    elif args.lineage:
        result = run_bucket(args.root_base, lineage_path=args.lineage)
    else:
        parser.error("--lineage is required unless --status-only is set")
    print(canonical(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
