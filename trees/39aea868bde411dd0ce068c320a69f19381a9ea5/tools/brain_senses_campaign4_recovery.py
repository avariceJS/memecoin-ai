#!/usr/bin/env python3
"""One-use c4 recovery lease: future anchor bootstrap, then bounded v3 frames."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import plistlib
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
from memecoin_ai.sources import Capture  # noqa: E402
from tools.brain_senses_block_epoch_v2 import (  # noqa: E402
    MAX_RESPONSE_BYTES,
    _validate_journal,
    build_epoch_plan,
    run_epoch,
)
from tools.brain_senses_block_epoch_v2_schedule import _read, _usage  # noqa: E402
from tools.brain_senses_block_epoch_v3_launch import _bootout  # noqa: E402
from tools.brain_senses_block_epoch_v3_schedule import (  # noqa: E402
    CAMPAIGN_RAW_BYTES,
    CAMPAIGN_STORED_BYTES,
    DISK_FLOOR_BYTES,
    FRAME_RAW_BYTES,
    FRAME_STORED_BYTES,
    FRAME_WIDTH_CAP,
    PEAK_WRITE_MARGIN_BYTES,
    REQUESTS_PER_FRAME,
    SELECTION_COUNT,
    _create_stop_atomically,
    initialize,
    run_bucket,
)
from tools.brain_senses_block_epoch_v3_schedule import read_status as read_status_v3  # noqa: E402

CAMPAIGN_ID = "helius-inventory-v3-20261010-4"
LABEL = f"ai.memecoin.brain.senses.{CAMPAIGN_ID}"
ROOT = REPO_ROOT / "work/brain-senses-live-helius-inventory-v3-20261010-4"
C3_ROOT = REPO_ROOT / "work/brain-senses-live-helius-inventory-v3-20261010-3"
LEASE_VERSION = "helius-c3-abnormal-stop-recovery-v1"
BOOTSTRAP_RAW_CAP = 32_000_000
BOOTSTRAP_STORED_CAP = 32_000_000
MAX_ATTEMPTS = 24
MAX_REQUESTS = 1 + MAX_ATTEMPTS * REQUESTS_PER_FRAME
CODE_BINDINGS = (
    "tools/brain_senses_campaign4_recovery.py",
    "tools/brain_senses_block_epoch_v3_schedule.py",
    "tools/brain_senses_block_epoch_v3_launch.py",
    "tools/brain_senses_block_epoch_v2.py",
    "tools/brain_senses_block_epoch_v2_schedule.py",
    "tools/brain_senses_program_watch.py",
    "src/memecoin_ai/sources.py",
    "src/memecoin_ai/domain.py",
)

# Hashes bind c4 to the stopped c3 evidence. No key material is part of this evidence.
C3_HASHES = {
    "STOP": "e8666a111d5e9aadd9c12044490e2d0ad4d42b0a204d57fbae0295ba036a9f00",
    "frontier-v3.json": "db52f9a7b4f2b24a4b54d5bde0574a57eaddc9937b5f9402c0ad9d5114d24e65",
    "active-frame-reservation.json": (
        "7292e9fc3cba8d0a92e1f6f8c21095d112523436a79daa06be6f421a6c597713"
    ),
    "epochs/20261010T110000Z/STOP": (
        "61c8c6405ab6b1c15d577b8ec79a2b6e3343e225cecd2508984120822c5e8ff0"
    ),
    "epochs/20261010T110000Z/epoch-summary.json": (
        "854093ce0c79ae5bf067a1e07052b753febe54d835c402927588430132578822"
    ),
    "epochs/20261010T110000Z/epoch-plan.json": (
        "7b3b3ea277af28d402d7caacd5bd10fd70c491d5ae8f705b59be0243890eabb3"
    ),
    "epochs/20261010T110000Z/frame-reservation.json": (
        "8dd92d211dfbb475bae2c49bf26dcc780dc098f6e3a374401b1f1de914a84139"
    ),
    "epochs/20261010T110000Z/frame-reservation-release.json": (
        "ca3dfb5568a8e08cb0b9b3caae6dff83c12dd7f534f432490279bf40b5cdad0b"
    ),
    "epochs/20261010T110000Z/requests/000000000001.json": (
        "3a1529e43ad717ace61c276568f7efd0dcd79b26b3937761aa02f7a11cfabac3"
    ),
    "epochs/20261010T110000Z/request-results/000000000001.json": (
        "a4c37dc7500a98305a6a667f03fba8d3e0f4f78e81068055c5f523e9d893cf6e"
    ),
}


def _write_exclusive(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = (canonical(value) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _c3_evidence(root: Path = C3_ROOT) -> dict[str, Any]:
    for rel, expected in C3_HASHES.items():
        path = root / rel
        if not path.is_file() or _sha(path) != expected:
            raise ValueError("c3_terminal_evidence_hash_mismatch")
    stop = _read(root / "STOP")
    frontier = _read(root / "frontier-v3.json")
    failed = root / "epochs/20261010T110000Z"
    summary = _read(failed / "epoch-summary.json")
    request = _read(failed / "requests/000000000001.json")
    result = _read(failed / "request-results/000000000001.json")
    active = _read(root / "active-frame-reservation.json")
    if (
        stop.get("campaign_id") != "helius-inventory-v3-20261010-3"
        or stop.get("reason") != "epoch_requires_review_before_next_launch"
        or stop.get("requests") != 205
        or stop.get("attempt_count") != 7
        or stop.get("raw_bytes") != 347_578_354
        or stop.get("stored_bytes") != 78_114_752
        or frontier.get("observed_frontier") != 455_216_025
        or frontier.get("observed_anchor_slot") != 455_216_024
        or frontier.get("last_attempt_bucket") != "20261010T110000Z"
        or summary.get("status") != "anchor_failed"
        or summary.get("reason") != "finalized_anchor_unknown"
        or summary.get("request_reservations") != 1
        or request.get("method") != "getSlot"
        or request.get("params") != [{"commitment": "finalized"}]
        or request.get("provider_charge_status") != "unknown_unverified"
        or result.get("status") != "request_failed"
        or result.get("error_class") != "TransportSourceError"
        or result.get("phase") != "anchor"
        or active.get("status") != "reconciled"
        or active.get("actual_requests") != 1
        or active.get("actual_raw_bytes") != 0
        or active.get("actual_stored_bytes") != 0
    ):
        raise ValueError("c3_terminal_evidence_semantics_mismatch")
    return {
        "campaign_id": stop["campaign_id"],
        "status": "permanently_terminal_abnormal_anchor_failure",
        "stop_sha256": C3_HASHES["STOP"],
        "frontier_sha256": C3_HASHES["frontier-v3.json"],
        "failed_bucket": "20261010T110000Z",
        "failed_request_identity": {"bucket": "20261010T110000Z", "phase": "anchor", "sequence": 1},
        "failed_request_sha256": C3_HASHES["epochs/20261010T110000Z/requests/000000000001.json"],
        "failed_result_sha256": C3_HASHES[
            "epochs/20261010T110000Z/request-results/000000000001.json"
        ],
        "failed_summary_sha256": C3_HASHES["epochs/20261010T110000Z/epoch-summary.json"],
        "last_trusted_anchor_slot": 455_216_024,
        "last_trusted_frontier": 455_216_025,
        "attempted_requests_including_ambiguous": 205,
        "raw_response_bytes": 347_578_354,
        "stored_response_bytes": 78_114_752,
        "charge_status_of_failed_request": "unknown_unverified",
    }


def _code_hashes() -> dict[str, str]:
    return {rel: _sha(REPO_ROOT / rel) for rel in CODE_BINDINGS}


def _bucket_time(bucket: str) -> datetime:
    return datetime.strptime(bucket, "%Y%m%dT%H0000Z").replace(tzinfo=UTC)


def _next_bucket(now: datetime) -> str:
    start = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return start.strftime("%Y%m%dT%H0000Z")


def prepare(
    *, now: datetime | None = None, root: Path = ROOT, c3_root: Path = C3_ROOT
) -> dict[str, Any]:
    """Create the one-use c4 policy and bootstrap bucket without provider I/O."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    if root.exists():
        raise ValueError("c4_root_already_exists_single_use")
    c3 = _c3_evidence(c3_root)
    required_disk = (
        DISK_FLOOR_BYTES + FRAME_RAW_BYTES + FRAME_STORED_BYTES + PEAK_WRITE_MARGIN_BYTES
    )
    free_disk = shutil.disk_usage(REPO_ROOT).free
    if free_disk < required_disk:
        raise ValueError("disk_floor_plus_frame_envelope")
    bucket = _next_bucket(now)
    tick_time = _bucket_time(bucket) + timedelta(minutes=5)
    lease_expires = int((tick_time + timedelta(hours=25)).timestamp() * 1_000_000_000)
    policy = {
        "schema_version": 1,
        "lease_id": LEASE_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "provider": "helius-free",
        "root": str(root),
        "predecessor": c3,
        "bootstrap": {
            "bucket": bucket,
            "scheduled_trigger_utc": tick_time.isoformat(),
            "identity": {"phase": "bootstrap_anchor", "sequence": 1},
            "method": "getSlot",
            "params": [{"commitment": "finalized"}],
            "request_limit": 1,
            "raw_byte_cap": BOOTSTRAP_RAW_CAP,
            "stored_byte_cap": BOOTSTRAP_STORED_CAP,
            "same_bucket_replay": False,
        },
        "bounds": {
            "bootstrap_observations": 1,
            "future_hourly_frame_attempts": MAX_ATTEMPTS,
            "total_requests": MAX_REQUESTS,
            "raw_bytes": CAMPAIGN_RAW_BYTES,
            "stored_bytes": CAMPAIGN_STORED_BYTES,
            "requests_per_frame": REQUESTS_PER_FRAME,
            "frame_raw_bytes": FRAME_RAW_BYTES,
            "frame_stored_bytes": FRAME_STORED_BYTES,
            "frame_width_slots": FRAME_WIDTH_CAP,
            "selected_slots_per_frame": SELECTION_COUNT,
            "minimum_free_disk_bytes": DISK_FLOOR_BYTES,
            "peak_write_margin_bytes": PEAK_WRITE_MARGIN_BYTES,
            "lease_expires_ns": lease_expires,
            "automatic_successor": False,
            "automatic_renewal": False,
            "missed_bucket_backfill": False,
            "retry_ambiguous_reservation": False,
        },
        "predecessor_lineage": {
            "c1_attempted_requests": 0,
            "c2_attempted_requests": 1,
            "c3_attempted_requests": 205,
        },
        "accounting_unit_notes": {
            "requests": "integer provider request reservations, not verified credits",
            "raw_bytes": "decimal bytes",
            "stored_bytes": "decimal compressed bytes",
            "slots": "native Solana slot coordinates; no UTC conversion",
            "timestamps": "Unix epoch nanoseconds unless ISO-8601 UTC is explicit",
        },
        "disk_free_at_prepare_bytes": free_disk,
        "required_disk_bytes": required_disk,
        "code_sha256": _code_hashes(),
        "prepared_ns": time.time_ns(),
    }
    root.mkdir(parents=True, mode=0o700)
    _write_exclusive(root / "lease-v1.json", policy)
    _sync_dir(root)
    return {
        "status": "prepared_inactive",
        "campaign_id": CAMPAIGN_ID,
        "bootstrap_bucket": bucket,
        "bootstrap_trigger_utc": tick_time.isoformat(),
        "campaign_root": str(root),
        "lease_sha256": _sha(root / "lease-v1.json"),
        "c3_stop_sha256": c3["stop_sha256"],
        "requests_max": MAX_REQUESTS,
        "network_requests": 0,
        "free_disk_bytes": free_disk,
        "required_disk_bytes": required_disk,
    }


def _policy(root: Path) -> dict[str, Any]:
    path = root / "lease-v1.json"
    if not path.is_file():
        raise ValueError("c4_lease_missing")
    value = _read(path)
    if value.get("lease_id") != LEASE_VERSION or value.get("campaign_id") != CAMPAIGN_ID:
        raise ValueError("c4_lease_identity_mismatch")
    if value.get("code_sha256") != _code_hashes():
        raise ValueError("c4_code_binding_changed")
    expected_c3 = _c3_evidence()
    bounds = value.get("bounds", {})
    bootstrap = value.get("bootstrap", {})
    if (
        value.get("root") != str(root)
        or value.get("predecessor") != expected_c3
        or value.get("predecessor_lineage")
        != {"c1_attempted_requests": 0, "c2_attempted_requests": 1, "c3_attempted_requests": 205}
        or bounds.get("future_hourly_frame_attempts") != MAX_ATTEMPTS
        or bounds.get("total_requests") != MAX_REQUESTS
        or bounds.get("raw_bytes") != CAMPAIGN_RAW_BYTES
        or bounds.get("stored_bytes") != CAMPAIGN_STORED_BYTES
        or bounds.get("requests_per_frame") != REQUESTS_PER_FRAME
        or bootstrap.get("request_limit") != 1
        or bootstrap.get("raw_byte_cap") != BOOTSTRAP_RAW_CAP
        or bootstrap.get("stored_byte_cap") != BOOTSTRAP_STORED_CAP
        or bootstrap.get("method") != "getSlot"
        or bootstrap.get("params") != [{"commitment": "finalized"}]
        or bootstrap.get("same_bucket_replay") is not False
        or bootstrap.get("bucket", "") <= "20261010T110000Z"
    ):
        raise ValueError("c4_lease_contract_mismatch")
    return value


def _stop(root: Path, reason: str, **facts: Any) -> dict[str, Any]:
    path = root / "STOP"
    if path.exists():
        return _read(path)
    record = {
        "schema_version": 1,
        "campaign_id": CAMPAIGN_ID,
        "lease_id": LEASE_VERSION,
        "reason": reason,
        "time_ns": time.time_ns(),
        "bootstrap_receipt_sha256": _sha(root / "bootstrap-receipt.json")
        if (root / "bootstrap-receipt.json").is_file()
        else None,
        **facts,
    }
    _create_stop_atomically(path, record)
    return record


def _bootstrap(
    root: Path, policy: dict[str, Any], *, now: datetime, clients=None
) -> dict[str, Any]:
    receipt_path = root / "bootstrap-receipt.json"
    reservation_path = root / "bootstrap-reservation.json"
    bucket = policy["bootstrap"]["bucket"]
    current_bucket = (
        now.astimezone(UTC).replace(minute=0, second=0, microsecond=0).strftime("%Y%m%dT%H0000Z")
    )
    if receipt_path.is_file():
        receipt = _read(receipt_path)
        if receipt.get("bucket") != bucket:
            return {
                "status": "campaign_stopped",
                **_stop(root, "bootstrap_receipt_bucket_mismatch"),
            }
        return {"status": "bootstrap_already_complete", "receipt_sha256": _sha(receipt_path)}
    if reservation_path.exists():
        return {
            "status": "campaign_stopped",
            **_stop(root, "bootstrap_reservation_ambiguous_no_replay"),
        }
    if current_bucket != bucket or now.astimezone(UTC) >= _bucket_time(bucket) + timedelta(
        minutes=20
    ):
        return {
            "status": "campaign_stopped",
            **_stop(root, "bootstrap_bucket_missed_no_replay", missed_bucket=bucket),
        }
    if now.astimezone(UTC) < _bucket_time(bucket):
        return {"status": "not_due", "bootstrap_bucket": bucket}
    required_disk = (
        DISK_FLOOR_BYTES + FRAME_RAW_BYTES + FRAME_STORED_BYTES + PEAK_WRITE_MARGIN_BYTES
    )
    if shutil.disk_usage(root).free < required_disk:
        return {"status": "campaign_stopped", **_stop(root, "disk_floor_plus_frame_envelope")}

    bootstrap_root = root / "bootstrap" / bucket
    reservation = {
        "schema_version": 1,
        "campaign_id": CAMPAIGN_ID,
        "lease_id": LEASE_VERSION,
        "bucket": bucket,
        "phase": "bootstrap_anchor",
        "sequence": 1,
        "request_limit": 1,
        "raw_byte_cap": BOOTSTRAP_RAW_CAP,
        "stored_byte_cap": BOOTSTRAP_STORED_CAP,
        "status": "reserved_before_io",
        "reserved_ns": time.time_ns(),
        "policy_sha256": _sha(root / "lease-v1.json"),
    }
    _write_exclusive(reservation_path, reservation)
    _sync_dir(root)
    plan = build_epoch_plan(
        epoch_id=bucket,
        bucket=bucket,
        schedule_id=CAMPAIGN_ID,
        schedule_sha256=_sha(root / "lease-v1.json"),
        seed=hashlib.sha256(f"{CAMPAIGN_ID}\0bootstrap\0{bucket}".encode()).hexdigest(),
        bootstrap=True,
        next_start=None,
        frame_width_cap=FRAME_WIDTH_CAP,
        selection_count=0,
        request_budget=1,
        raw_byte_budget=BOOTSTRAP_RAW_CAP,
        stored_byte_budget=BOOTSTRAP_STORED_CAP,
        minimum_free_disk_bytes=DISK_FLOOR_BYTES,
        capture_storage_codec="zstd",
        max_response_bytes=MAX_RESPONSE_BYTES,
    )
    try:
        summary = run_epoch(bootstrap_root, plan, clients=clients)
        rows = _validate_journal(bootstrap_root, plan)
    except Exception as exc:
        _stop(root, "bootstrap_execution_or_reconciliation_failed", error_class=type(exc).__name__)
        return {"status": "campaign_stopped", **_read(root / "STOP")}
    if (
        summary.get("status") != "bootstrap_complete"
        or len(rows) != 1
        or rows[0].get("method") != "getSlot"
        or rows[0].get("phase") != "anchor"
        or rows[0].get("status") != "captured"
        or type(rows[0].get("slot_value")) is not int
        or rows[0]["slot_value"] < 455_216_024
    ):
        _stop(root, "bootstrap_anchor_unknown_or_regressed", epoch_status=summary.get("status"))
        return {"status": "campaign_stopped", **_read(root / "STOP")}
    usage = _usage(bootstrap_root.parent)
    if (
        usage["requests"] != 1
        or usage["raw_bytes"] > BOOTSTRAP_RAW_CAP
        or usage["stored_bytes"] > BOOTSTRAP_STORED_CAP
    ):
        _stop(root, "bootstrap_envelope_reconciliation_failed", bootstrap_usage=usage)
        return {"status": "campaign_stopped", **_read(root / "STOP")}
    anchor = rows[0]
    lower = 455_216_025
    gap = {
        "start": lower,
        "end_exclusive": anchor["slot_value"] + 1,
        "reason": "uncovered_since_c3_trusted_frontier_no_backfill",
        "from_c3_stop_sha256": policy["predecessor"]["stop_sha256"],
        "bootstrap_bucket": bucket,
    }
    prior_gaps = list(_read(C3_ROOT / "frontier-v3.json").get("gap_intervals", []))
    if gap["end_exclusive"] > gap["start"]:
        prior_gaps.append(gap)
    elif gap["end_exclusive"] < gap["start"]:
        _stop(
            root,
            "bootstrap_anchor_below_last_trusted_frontier",
            observed_anchor=anchor["slot_value"],
        )
        return {"status": "campaign_stopped", **_read(root / "STOP")}
    lineage = {
        "schema_version": 1,
        "provider": "helius-free",
        "campaign_id": CAMPAIGN_ID,
        "predecessor_campaign_id": "helius-inventory-v3-20261010-3",
        "predecessor_stop_sha256": policy["predecessor"]["stop_sha256"],
        "last_attempt_bucket": bucket,
        "last_observed_anchor_slot": anchor["slot_value"],
        "last_observed_anchor_receipt_ns": anchor["receipt_ns"],
        "last_inventoried_interval": _read(C3_ROOT / "frontier-v3.json").get(
            "last_inventoried_interval"
        ),
        "gap_intervals": prior_gaps,
        "predecessor_status": "c3_permanently_terminal; c4 bootstrap is new observation",
        "predecessor_campaign_usage": {
            "c1_attempted_requests": 0,
            "c2_attempted_requests": 1,
            "c3_attempted_requests_including_uncertain_charge": 205,
        },
        "controller_lease_expires_ns": policy["bounds"]["lease_expires_ns"],
        "bootstrap_receipt_sha256": None,
    }
    receipt = {
        "schema_version": 1,
        "lease_id": LEASE_VERSION,
        "campaign_id": CAMPAIGN_ID,
        "bucket": bucket,
        "request_identity": {
            "campaign_id": CAMPAIGN_ID,
            "utc_bucket": bucket,
            "phase": "bootstrap_anchor",
            "sequence": 1,
        },
        "method": "getSlot(finalized)",
        "observed_anchor_slot": anchor["slot_value"],
        "receipt_ns": anchor["receipt_ns"],
        "availability_ns": anchor["availability_ns"],
        "uncovered_gap": gap,
        "request_sha256": _sha(bootstrap_root / "requests/000000000001.json"),
        "result_sha256": _sha(bootstrap_root / "request-results/000000000001.json"),
        "summary_sha256": _sha(bootstrap_root / "epoch-summary.json"),
        "plan_sha256": _sha(bootstrap_root / "epoch-plan.json"),
        "raw_response_sha256": hashlib.sha256(
            Capture.load(bootstrap_root / rows[0]["capture_path"]).response
        ).hexdigest(),
        "usage": usage,
        "predecessor_stop_sha256": policy["predecessor"]["stop_sha256"],
    }
    _write_exclusive(receipt_path, receipt)
    lineage["bootstrap_receipt_sha256"] = _sha(receipt_path)
    _write_exclusive(root / "predecessor-lineage.json", lineage)
    initialize(root, root / "predecessor-lineage.json")
    from tools.brain_senses_program_watch import _replace

    policy["bootstrap"]["receipt_sha256"] = _sha(receipt_path)
    _replace(root / "lease-v1.json", policy)
    _sync_dir(root)
    return {
        "status": "bootstrap_complete",
        "bucket": bucket,
        "anchor_slot": anchor["slot_value"],
        "gap": gap,
    }


def tick(
    root: Path = ROOT,
    *,
    now: datetime | None = None,
    clients: dict[str, Any] | None = None,
    runner=subprocess.run,
) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    with (root / ".c4.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("c4_invocation_already_active") from exc
        if (root / "STOP").is_file():
            record = _read(root / "STOP")
            _bootout(LABEL, runner=runner)
            return {"status": "campaign_stopped", **record}
        try:
            policy = _policy(root)
        except Exception as exc:
            record = _stop(root, "lease_validation_failed_closed", error_class=type(exc).__name__)
            _bootout(LABEL, runner=runner)
            return {"status": "campaign_stopped", **record}
        if not (root / "bootstrap-receipt.json").is_file():
            result = _bootstrap(root, policy, now=now, clients=clients)
        else:
            receipt = _read(root / "bootstrap-receipt.json")
            bucket = now.replace(minute=0, second=0, microsecond=0).strftime("%Y%m%dT%H0000Z")
            if bucket == receipt["bucket"]:
                return {"status": "bootstrap_already_complete", "bucket": bucket}
            usage = _usage(root / "epochs")
            bootstrap_usage = _usage(root / "bootstrap")
            if usage["requests"] + 1 + REQUESTS_PER_FRAME > MAX_REQUESTS:
                _stop(root, "total_request_cap_preflight", requests=usage["requests"] + 1)
                result = {"status": "campaign_stopped", **_read(root / "STOP")}
            elif (
                bootstrap_usage["raw_bytes"] + usage["raw_bytes"] + FRAME_RAW_BYTES
                > CAMPAIGN_RAW_BYTES
            ):
                _stop(
                    root,
                    "total_raw_byte_cap_preflight",
                    raw_bytes=bootstrap_usage["raw_bytes"] + usage["raw_bytes"],
                )
                result = {"status": "campaign_stopped", **_read(root / "STOP")}
            elif (
                bootstrap_usage["stored_bytes"] + usage["stored_bytes"] + FRAME_STORED_BYTES
                > CAMPAIGN_STORED_BYTES
            ):
                _stop(
                    root,
                    "total_stored_byte_cap_preflight",
                    stored_bytes=bootstrap_usage["stored_bytes"] + usage["stored_bytes"],
                )
                result = {"status": "campaign_stopped", **_read(root / "STOP")}
            else:
                frontier = _read(root / "frontier-v3.json")
                prior = _bucket_time(frontier["last_attempt_bucket"])
                current = now.replace(minute=0, second=0, microsecond=0)
                if current > prior + timedelta(hours=1):
                    _stop(
                        root,
                        "missed_scheduled_bucket_no_backfill",
                        prior_bucket=frontier["last_attempt_bucket"],
                        current_bucket=current.strftime("%Y%m%dT%H0000Z"),
                    )
                    result = {"status": "campaign_stopped", **_read(root / "STOP")}
                else:
                    result = run_bucket(
                        root,
                        lineage_path=root / "predecessor-lineage.json",
                        now=now,
                        clients=clients,
                    )
        if (root / "STOP").is_file():
            _bootout(LABEL, runner=runner)
        return result


def read_status(root: Path = ROOT) -> dict[str, Any]:
    policy = _policy(root)
    status = (
        read_status_v3(root)
        if (root / "schedule-v3.json").is_file()
        else {"status": "bootstrap_pending"}
    )
    bootstrap_usage = (
        _usage(root / "bootstrap")
        if (root / "bootstrap").is_dir()
        else {
            "requests": 0,
            "raw_bytes": 0,
            "stored_bytes": 0,
            "reserved_raw_bytes": 0,
            "reserved_stored_bytes": 0,
        }
    )
    status["bootstrap_usage"] = bootstrap_usage
    status["all_in_requests"] = bootstrap_usage["requests"] + int(status.get("requests", 0))
    status["all_in_raw_bytes"] = bootstrap_usage["raw_bytes"] + int(
        status.get("raw_response_bytes", 0)
    )
    status["all_in_stored_bytes"] = bootstrap_usage["stored_bytes"] + int(
        status.get("stored_capture_bytes", 0)
    )
    status["lease_id"] = policy["lease_id"]
    return status


def _plist(root: Path) -> dict[str, Any]:
    supervisor = root / "supervisor"
    supervisor.mkdir(parents=True, exist_ok=True, mode=0o700)
    out = supervisor / "stdout.log"
    err = supervisor / "stderr.log"
    for path in (out, err):
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
    zstd = os.environ.get("BRAIN_ZSTD_EXECUTABLE") or shutil.which("zstd")
    if not zstd or not Path(zstd).is_absolute():
        raise ValueError("configured_zstd_executable_unavailable")
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(REPO_ROOT / ".venv/bin/python"),
            str(REPO_ROOT / "tools/brain_senses_campaign4_recovery.py"),
            "tick",
            "--root",
            str(root),
        ],
        "WorkingDirectory": str(REPO_ROOT),
        "EnvironmentVariables": {"BRAIN_ZSTD_EXECUTABLE": zstd},
        "StartCalendarInterval": {"Minute": 5},
        "RunAtLoad": False,
        "KeepAlive": False,
        "StandardOutPath": str(out),
        "StandardErrorPath": str(err),
    }


def activate(
    *, root: Path = ROOT, launch_agents: Path | None = None, runner=subprocess.run
) -> dict[str, Any]:
    policy = _policy(root)
    if (root / "STOP").exists() or (root / "bootstrap-reservation.json").exists():
        raise ValueError("c4_not_pristine_at_activation")
    launch_agents = launch_agents or Path.home() / "Library/LaunchAgents"
    bucket = _bucket_time(policy["bootstrap"]["bucket"])
    trigger = bucket + timedelta(minutes=5)
    now = datetime.now(UTC)
    if now >= trigger or (trigger - now).total_seconds() > 90 * 60:
        raise ValueError("c4_bootstrap_trigger_not_future_or_near")
    required = int(policy["required_disk_bytes"])
    if shutil.disk_usage(REPO_ROOT).free < required:
        raise ValueError("disk_floor_plus_frame_envelope")
    stale = [
        "ai.memecoin.brain.senses.helius-inventory-controller-v1",
        "ai.memecoin.brain.senses.helius-inventory-v3-20261010-1",
        "ai.memecoin.brain.senses.helius-inventory-v3-20261010-2",
        "ai.memecoin.brain.senses.helius-inventory-v3-20261010-3",
        LABEL,
    ]
    for label in stale[:-1]:
        disabled = runner(
            ["/bin/launchctl", "disable", f"gui/{os.getuid()}/{label}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if disabled.returncode != 0:
            raise ValueError("predecessor_service_disable_failed")
    for label in stale:
        check = runner(
            ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        if check.returncode == 0:
            raise ValueError("duplicate_or_predecessor_service_loaded")
    plist_path = launch_agents / f"{LABEL}.plist"
    if plist_path.exists():
        raise ValueError("c4_plist_already_exists_single_use")
    payload = plistlib.dumps(_plist(root), fmt=plistlib.FMT_XML, sort_keys=True)
    fd = os.open(plist_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    _sync_dir(launch_agents)
    result = runner(
        ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        raise ValueError("c4_launchd_bootstrap_failed")
    return {
        "status": "activated",
        "campaign_id": CAMPAIGN_ID,
        "label": LABEL,
        "plist": str(plist_path),
        "plist_sha256": _sha(plist_path),
        "first_bucket": policy["bootstrap"]["bucket"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p_prepare = sub.add_parser("prepare")
    p_prepare.add_argument("--root", type=Path, default=ROOT)
    p_activate = sub.add_parser("activate")
    p_activate.add_argument("--root", type=Path, default=ROOT)
    p_tick = sub.add_parser("tick")
    p_tick.add_argument("--root", type=Path, default=ROOT)
    p_status = sub.add_parser("status")
    p_status.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        result = prepare(root=args.root)
    elif args.action == "activate":
        result = activate(root=args.root)
    elif args.action == "tick":
        result = tick(root=args.root)
    else:
        result = read_status(root=args.root)
    print(canonical(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
