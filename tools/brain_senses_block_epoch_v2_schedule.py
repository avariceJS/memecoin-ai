#!/usr/bin/env python3
"""Finite scheduler/frontier journal for the inventory-first Senses epoch v2."""

from __future__ import annotations

import argparse
import difflib
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

from memecoin_ai.domain import canonical, digest  # noqa: E402
from memecoin_ai.sources import Capture, verify_capture_storage_codec  # noqa: E402
from tools.brain_senses_block_epoch_schedule import BUCKET_RE, utc_bucket  # noqa: E402
from tools.brain_senses_block_epoch_v2 import (  # noqa: E402
    MAX_GET_BLOCKS_WIDTH,
    MAX_RESPONSE_BYTES,
    PROVIDER,
    build_epoch_plan,
    run_epoch,
)
from tools.brain_senses_program_watch import _create, _read, _replace  # noqa: E402

SCHEDULE_SOURCE = Path(__file__).resolve()
BEHAVIOR_SOURCE_PATHS = (
    "tools/brain_senses_block_epoch_v2.py",
    "tools/brain_senses_block_provider_pilot.py",
    "tools/brain_senses_program_watch.py",
    "src/memecoin_ai/sources.py",
    "src/memecoin_ai/domain.py",
)
FIRST_APPROVED_SCHEDULER_COMMIT = "9eeca1e535fe364ae6f88844286d347b8e876146"


def _hash_object(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _source_bindings() -> dict[str, str]:
    paths = (SCHEDULE_SOURCE, *(REPO_ROOT / path for path in BEHAVIOR_SOURCE_PATHS))
    return {
        str(path.relative_to(REPO_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def _git_blob(commit: str, relative_path: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{relative_path}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _historical_behavior_bindings(commit: str) -> dict[str, str]:
    return {
        path: hashlib.sha256(_git_blob(commit, path)).hexdigest() for path in BEHAVIOR_SOURCE_PATHS
    }


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
        "behavior_source_sha256": _source_bindings(),
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


def _legacy_schedule_config_matches(existing: dict[str, Any], expected: dict[str, Any]) -> bool:
    ignored = {
        "created_ns",
        "scheduler_source_commit",
        "scheduler_source_sha256",
        "behavior_source_sha256",
    }
    return {key: value for key, value in existing.items() if key not in ignored} == {
        key: value for key, value in expected.items() if key not in ignored
    }


def _verify_legacy_bootstrap(
    root_base: Path, existing: dict[str, Any], *, require_bootstrap_only: bool = True
) -> dict[str, Any]:
    """Verify the exact one-request bootstrap before a source compatibility migration."""
    epochs_root = root_base / "epochs"
    epoch_root = epochs_root / "20261009T220000Z"
    plan_path = epoch_root / "epoch-plan.json"
    summary_path = epoch_root / "epoch-summary.json"
    frontier_path = root_base / "frontier-v2.json"
    reservation_path = epoch_root / "requests" / "000000000001.json"
    result_path = epoch_root / "request-results" / "000000000001.json"
    if not all(
        path.is_file()
        for path in (plan_path, summary_path, frontier_path, reservation_path, result_path)
    ):
        raise ValueError("compatibility_migration_bootstrap_evidence_missing")
    all_reservations = sorted(epochs_root.glob("*/requests/*.json"))
    if reservation_path not in all_reservations or (
        require_bootstrap_only and all_reservations != [reservation_path]
    ):
        raise ValueError("compatibility_migration_unexpected_request_history")
    plan = _read(plan_path)
    summary = _read(summary_path)
    frontier = _read(frontier_path)
    reservation = _read(reservation_path)
    result = _read(result_path)
    expected_request_hash = hashlib.sha256(
        canonical(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSlot",
                "params": [{"commitment": "finalized"}],
            }
        ).encode()
    ).hexdigest()
    current_sources = _source_bindings()
    if (
        plan.get("schedule_id") != existing.get("schedule_id")
        or plan.get("schedule_sha256") != _hash_object(existing)
        or plan.get("code_commit") != existing.get("scheduler_source_commit")
        or plan.get("code_sha256") != current_sources["tools/brain_senses_block_epoch_v2.py"]
        or plan.get("bootstrap") is not True
        or summary.get("status") != "bootstrap_complete"
        or summary.get("request_reservations") != 1
        or summary.get("advance_to") != 455028552
        or frontier.get("next_start") != 455028552
        or frontier.get("last_applied_bucket") != "20261009T220000Z"
        or reservation.get("method") != "getSlot"
        or reservation.get("provider") != PROVIDER
        or reservation.get("sequence") != 1
        or reservation.get("request_sha256") != expected_request_hash
        or result.get("status") != "captured"
        or result.get("result_status") != "parsed"
        or result.get("slot_value") != 455028551
    ):
        raise ValueError("compatibility_migration_bootstrap_binding_mismatch")
    capture_dir = epoch_root / result["capture_path"]
    capture = Capture.load(capture_dir)
    if digest(capture.response) != result.get("response_sha256"):
        raise ValueError("compatibility_migration_capture_hash_mismatch")
    receipt_path = capture_dir / "receipt.json"
    if not receipt_path.is_file():
        raise ValueError("compatibility_migration_capture_receipt_missing")
    return {
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        "frontier_sha256": hashlib.sha256(frontier_path.read_bytes()).hexdigest(),
        "reservation_sha256": hashlib.sha256(reservation_path.read_bytes()).hexdigest(),
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        "capture_response_sha256": result["response_sha256"],
        "capture_receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        "request_count": 1,
        "frontier_next_start": 455028552,
    }


def _scheduler_diff(old_source: bytes, new_source: bytes, old_label: str) -> str:
    relative_path = str(SCHEDULE_SOURCE.relative_to(REPO_ROOT))
    return "".join(
        difflib.unified_diff(
            old_source.decode("utf-8").splitlines(keepends=True),
            new_source.decode("utf-8").splitlines(keepends=True),
            fromfile=f"{old_label}:{relative_path}",
            tofile=f"approved-current:{relative_path}",
        )
    )


def _validate_compatibility_migration(
    root_base: Path, existing: dict[str, Any], expected: dict[str, Any]
) -> None:
    migration_path = root_base / "schedule-migrations" / "0001.json"
    if not migration_path.is_file():
        raise ValueError("schedule_binding_changed")
    migration_names = sorted(path.name for path in migration_path.parent.glob("*.json"))
    if migration_names not in (["0001.json"], ["0001.json", "0002.json"]):
        raise ValueError("schedule_binding_changed")
    migration = _read(migration_path)
    original_scheduler = _git_blob(
        existing["scheduler_source_commit"], "tools/brain_senses_block_epoch_v2_schedule.py"
    )
    second_path = migration_path.parent / "0002.json"
    second = _read(second_path) if second_path.is_file() else None
    first_approved_source = (
        _git_blob(
            second["previous_approved_source_commit"],
            "tools/brain_senses_block_epoch_v2_schedule.py",
        )
        if second
        else SCHEDULE_SOURCE.read_bytes()
    )
    first_approved_hash = hashlib.sha256(first_approved_source).hexdigest()
    expected_first_diff = _scheduler_diff(
        original_scheduler,
        first_approved_source,
        existing["scheduler_source_commit"],
    )
    expected_second_diff = (
        _scheduler_diff(
            first_approved_source,
            SCHEDULE_SOURCE.read_bytes(),
            second["previous_approved_source_commit"],
        )
        if second
        else None
    )
    current_bindings = _source_bindings()
    first_bindings = migration.get("approved_source_bindings", {})
    source_path = str(SCHEDULE_SOURCE.relative_to(REPO_ROOT))
    current_helpers = {key: value for key, value in current_bindings.items() if key != source_path}
    first_helpers = {key: value for key, value in first_bindings.items() if key != source_path}
    if (
        not _legacy_schedule_config_matches(existing, expected)
        or migration.get("original_scheduler_source_commit")
        != existing.get("scheduler_source_commit")
        or migration.get("original_manifest_sha256") != _hash_object(existing)
        or migration.get("original_scheduler_source_sha256")
        != existing.get("scheduler_source_sha256")
        or migration.get("approved_scheduler_source_sha256") != first_approved_hash
        or first_approved_hash != first_bindings.get(str(SCHEDULE_SOURCE.relative_to(REPO_ROOT)))
        or migration.get("approved_source_bindings") is None
        or first_helpers
        != _historical_behavior_bindings(
            second["previous_approved_source_commit"]
            if second
            else existing["scheduler_source_commit"]
        )
        or (second is not None and current_helpers != first_helpers)
        or migration.get("bootstrap_behavior_source_sha256")
        != _historical_behavior_bindings(existing["scheduler_source_commit"])
        or migration.get("scheduler_compatibility_diff") != expected_first_diff
        or migration.get("bootstrap_evidence")
        != _verify_legacy_bootstrap(root_base, existing, require_bootstrap_only=False)
        or (second is None and current_bindings != first_bindings)
        or (
            second is not None
            and (
                second.get("previous_migration_sha256")
                != hashlib.sha256(migration_path.read_bytes()).hexdigest()
                or second.get("previous_approved_scheduler_source_sha256") != first_approved_hash
                or second.get("approved_scheduler_source_sha256")
                != expected.get("scheduler_source_sha256")
                or second.get("approved_source_bindings") != current_bindings
                or second.get("scheduler_compatibility_diff") != expected_second_diff
            )
        )
    ):
        raise ValueError("schedule_binding_changed")


def migrate_legacy_schedule_binding(root_base: Path, expected: dict[str, Any]) -> dict[str, Any]:
    """Append one reviewed, no-RPC source migration for the 2026-10-09 pilot."""
    root_base.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = root_base / ".schedule-v2.lock"
    with lock_path.open("a") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("inventory_first_schedule_already_active") from exc
        schedule_path = root_base / "schedule-v2.json"
        if not schedule_path.is_file():
            raise ValueError("compatibility_migration_schedule_missing")
        existing = _read(schedule_path)
        migration_dir = root_base / "schedule-migrations"
        migration_path = migration_dir / "0001.json"
        if migration_path.exists():
            _validate_compatibility_migration(root_base, existing, expected)
            return _read(migration_path)
        if not _legacy_schedule_config_matches(existing, expected):
            raise ValueError("schedule_binding_changed")
        bootstrap = _verify_legacy_bootstrap(root_base, existing)
        old_scheduler = _git_blob(
            existing["scheduler_source_commit"], "tools/brain_senses_block_epoch_v2_schedule.py"
        )
        old_hash = hashlib.sha256(old_scheduler).hexdigest()
        if old_hash != existing.get("scheduler_source_sha256"):
            raise ValueError("compatibility_migration_original_source_hash_mismatch")
        historical_behavior = _historical_behavior_bindings(existing["scheduler_source_commit"])
        current_behavior = {path: _source_bindings()[path] for path in BEHAVIOR_SOURCE_PATHS}
        if historical_behavior != current_behavior:
            raise ValueError("compatibility_migration_behavior_source_changed")
        current_scheduler = SCHEDULE_SOURCE.read_bytes()
        diff = "".join(
            difflib.unified_diff(
                old_scheduler.decode("utf-8").splitlines(keepends=True),
                current_scheduler.decode("utf-8").splitlines(keepends=True),
                fromfile=(
                    f"{existing['scheduler_source_commit']}:{SCHEDULE_SOURCE.relative_to(REPO_ROOT)}"
                ),
                tofile=f"approved-current:{SCHEDULE_SOURCE.relative_to(REPO_ROOT)}",
            )
        )
        if not diff:
            raise ValueError("compatibility_migration_diff_missing")
        migration = {
            "schema_version": 1,
            "migration_id": "scheduler-head-provenance-only-20261010",
            "original_manifest_sha256": _hash_object(existing),
            "original_scheduler_source_commit": existing["scheduler_source_commit"],
            "original_scheduler_source_sha256": old_hash,
            "approved_scheduler_source_sha256": expected["scheduler_source_sha256"],
            "bootstrap_behavior_source_sha256": historical_behavior,
            "approved_source_bindings": _source_bindings(),
            "bootstrap_evidence": bootstrap,
            "compatibility_only_changes": [
                "scheduler_source_commit is provenance, not a moving-HEAD compatibility check",
                "only the recorded scheduler hash transition is accepted; "
                "config/source pins stay strict",
                "interrupted epochs resume their saved plan under the current "
                "approved source bindings",
            ],
            "scheduler_compatibility_diff": diff,
            "no_rpc_validation": {
                "validated": True,
                "checks": [
                    "docs-only HEAD movement preserves schedule compatibility",
                    "schedule config and behavior source mismatches fail closed",
                    "interrupted epoch resumes from saved plan without repeating reservations",
                    "bootstrap and absence of later reservations verified read-only",
                ],
                "test_nodes": [
                    "tests/test_brain_senses_block_epoch_v2.py::test_scheduler_head_is_provenance_but_code_and_config_bindings_stay_strict",
                    "tests/test_brain_senses_block_epoch_v2.py::test_scheduler_bootstrap_restart_frontier_and_campaign_caps",
                ],
            },
            "created_ns": time.time_ns(),
        }
        migration_dir.mkdir(mode=0o700, exist_ok=True)
        _create(migration_path, migration)
        _validate_compatibility_migration(root_base, existing, expected)
        return migration


def advance_legacy_scheduler_binding(root_base: Path, expected: dict[str, Any]) -> dict[str, Any]:
    """Append the reviewed recovery-validator correction after migration 0001."""
    root_base.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = root_base / ".schedule-v2.lock"
    with lock_path.open("a") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("inventory_first_schedule_already_active") from exc
        schedule_path = root_base / "schedule-v2.json"
        first_path = root_base / "schedule-migrations" / "0001.json"
        second_path = root_base / "schedule-migrations" / "0002.json"
        if not schedule_path.is_file() or not first_path.is_file():
            raise ValueError("compatibility_migration_predecessor_missing")
        existing, first = _read(schedule_path), _read(first_path)
        if second_path.exists():
            _validate_compatibility_migration(root_base, existing, expected)
            return _read(second_path)
        previous_source = _git_blob(
            FIRST_APPROVED_SCHEDULER_COMMIT,
            "tools/brain_senses_block_epoch_v2_schedule.py",
        )
        previous_hash = hashlib.sha256(previous_source).hexdigest()
        previous_bindings = first.get("approved_source_bindings", {})
        previous_path = str(SCHEDULE_SOURCE.relative_to(REPO_ROOT))
        if (
            not _legacy_schedule_config_matches(existing, expected)
            or first.get("original_scheduler_source_commit")
            != existing.get("scheduler_source_commit")
            or first.get("original_manifest_sha256") != _hash_object(existing)
            or first.get("original_scheduler_source_sha256")
            != existing.get("scheduler_source_sha256")
            or previous_hash != first.get("approved_scheduler_source_sha256")
            or previous_bindings.get(previous_path) != previous_hash
            or {key: value for key, value in previous_bindings.items() if key != previous_path}
            != _historical_behavior_bindings(FIRST_APPROVED_SCHEDULER_COMMIT)
            or _verify_legacy_bootstrap(root_base, existing, require_bootstrap_only=False)
            != first.get("bootstrap_evidence")
        ):
            raise ValueError("compatibility_migration_predecessor_mismatch")
        current_bindings = _source_bindings()
        current_helpers = {
            key: value for key, value in current_bindings.items() if key != previous_path
        }
        previous_helpers = {
            key: value for key, value in previous_bindings.items() if key != previous_path
        }
        if current_helpers != previous_helpers:
            raise ValueError("compatibility_migration_behavior_source_changed")
        diff = _scheduler_diff(
            previous_source, SCHEDULE_SOURCE.read_bytes(), FIRST_APPROVED_SCHEDULER_COMMIT
        )
        if not diff:
            raise ValueError("compatibility_migration_diff_missing")
        second = {
            "schema_version": 1,
            "migration_id": "scheduler-recovery-validator-20261010",
            "previous_migration_sha256": hashlib.sha256(first_path.read_bytes()).hexdigest(),
            "previous_approved_source_commit": FIRST_APPROVED_SCHEDULER_COMMIT,
            "previous_approved_scheduler_source_sha256": previous_hash,
            "approved_scheduler_source_sha256": expected["scheduler_source_sha256"],
            "approved_source_bindings": current_bindings,
            "bootstrap_evidence": first["bootstrap_evidence"],
            "compatibility_only_changes": [
                "validate the original bootstrap evidence independently of "
                "later epoch reservations",
                "keep the original bootstrap hashes and schedule identity immutable",
                "retain the same source/config/request behavior and recovery rules",
            ],
            "scheduler_compatibility_diff": diff,
            "no_rpc_validation": {
                "validated": True,
                "checks": [
                    "completed frame journal replays from saved summary without an RPC",
                    "bootstrap receipt remains hash-bound after a later epoch is added",
                    "source/config mismatches remain fail-closed",
                ],
                "test_nodes": [
                    "tests/test_brain_senses_block_epoch_v2.py::test_scheduler_bootstrap_restart_frontier_and_campaign_caps",
                    "tests/test_brain_senses_block_epoch_v2.py::test_scheduler_head_is_provenance_but_code_and_config_bindings_stay_strict",
                ],
            },
            "created_ns": time.time_ns(),
        }
        _create(second_path, second)
        _validate_compatibility_migration(root_base, existing, expected)
        return second


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
    schedule_hash: str,
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
        plan = _read(plan_path)
        if (
            plan.get("epoch_id") != epoch.name
            or plan.get("bucket") != epoch.name
            or plan.get("schedule_sha256") != schedule_hash
            or plan.get("code_sha256") != _source_bindings()["tools/brain_senses_block_epoch_v2.py"]
        ):
            raise ValueError("interrupted_epoch_source_binding_changed")
        summary = run_epoch(epoch, plan, clients=clients)
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
        current_compatible = dict(existing)
        current_compatible.pop("scheduler_source_commit", None)
        expected_compatible = dict(expected)
        expected_compatible.pop("scheduler_source_commit", None)
        if current_compatible != expected_compatible:
            _validate_compatibility_migration(root_base, existing, expected)
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
        _reconcile_prior_epochs(
            epochs_root, bucket, frontier, frontier_path, schedule_hash, clients
        )
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
    if plan.get("schedule_sha256") != _hash_object(manifest):
        raise ValueError("epoch_schedule_identity_mismatch")
    if plan.get("code_sha256") != _source_bindings()["tools/brain_senses_block_epoch_v2.py"]:
        raise ValueError("epoch_source_binding_changed")
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
    parser.add_argument(
        "--migrate-legacy-compatibility-binding",
        action="store_true",
        help="append the approved no-RPC source migration for the pinned 2026-10-09 pilot",
    )
    parser.add_argument(
        "--advance-legacy-source-binding",
        action="store_true",
        help="append the reviewed recovery-validator correction to migration 0001",
    )
    args = parser.parse_args(argv)
    if args.migrate_legacy_compatibility_binding or args.advance_legacy_source_binding:
        expected = _schedule_manifest(
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
        )
        result = (
            advance_legacy_scheduler_binding(args.root_base, expected)
            if args.advance_legacy_source_binding
            else migrate_legacy_schedule_binding(args.root_base, expected)
        )
        print(canonical(result), flush=True)
        return 0
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
