#!/usr/bin/env python3
"""One-use, receipt-bound handoff from the abnormal c2 STOP to finite c3."""

from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memecoin_ai.domain import canonical  # noqa: E402
from memecoin_ai.sources import Capture  # noqa: E402
from tools.brain_senses_block_epoch_v2 import (  # noqa: E402
    _validate_journal,  # noqa: E402
    build_epoch_plan,  # noqa: E402
)
from tools.brain_senses_block_epoch_v2_schedule import _read, _usage  # noqa: E402
from tools.brain_senses_block_epoch_v3_schedule import (  # noqa: E402
    ATTEMPTS_MAX,
    CAMPAIGN_RAW_BYTES,
    CAMPAIGN_STORED_BYTES,
    DISK_FLOOR_BYTES,
    FRAME_RAW_BYTES,
    FRAME_STORED_BYTES,
    FRAME_WIDTH_CAP,
    PEAK_WRITE_MARGIN_BYTES,
    REQUESTS_PER_FRAME,
    SELECTION_COUNT,
    _attempts,
    _reconcile_epoch,
    initialize,
)
from tools.brain_senses_campaign_controller import (  # noqa: E402
    CONTROLLER_LABEL,
    _campaign_plist,
)

HANDOFF_ID = "helius-inventory-v3-c3-anchor-adoption-20261010-v1"
RECEIPT = REPO_ROOT / "docs/evidence/helius-inventory-v3-campaign2-missed-gap-2026-10-10.json"
RECEIPT_SHA256 = "76d2733335c1a22e870c8dd40a30c3dfa1bf5965558a54624bb9e0efdc07f7b6"
CAMPAIGN1_ID = "helius-inventory-v3-20261010-1"
CAMPAIGN2_ID = "helius-inventory-v3-20261010-2"
CAMPAIGN3_ID = "helius-inventory-v3-20261010-3"
CONTROLLER_ID = "helius-inventory-controller-20261010-72h-recovery-v1"
CONTROLLER_STOP_SHA256 = "8a298c7426343f4b4dca138d0bc39669ed7382f229f7644d4a82539ba99cbfe0"
CAMPAIGN2_STOP_SHA256 = "da160ec4cf3ad3ca4e0804175d33d53ad605253d6ce57be5e5d9665306b0005a"
ANCHOR_SLOT = 455116880
ANCHOR_NS = 1791605106134330000
FRONTIER = 455116881
GAPS = [
    {
        "bucket": "20261010T000000Z",
        "interval_end_exclusive": 455061516,
        "interval_start": 455037817,
        "reason": "oversized_frame",
        "receipt": "helius-inventory-v2-oversized-gap-2026-10-10.json",
    },
    {"end_exclusive": 455116881, "reason": "missed_schedule_no_backfill", "start": 455082524},
]
LEASE_REQUESTS = 2451
LEASE_RAW_BYTES = 12_000_000_000
LEASE_STORED_BYTES = 3_000_000_000
CONTROLLER_ROOT = REPO_ROOT / "work/brain-senses-helius-controller-recovery-20261010"
CAMPAIGN1_ROOT = REPO_ROOT / "work/brain-senses-live-helius-inventory-v3-20261010-1"
CAMPAIGN2_ROOT = REPO_ROOT / "work/brain-senses-live-helius-inventory-v3-20261010-2"
CAMPAIGN3_ROOT = REPO_ROOT / "work/brain-senses-live-helius-inventory-v3-20261010-3"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _loaded(label: str, runner=subprocess.run) -> bool:
    return (
        runner(
            ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )


def _campaign2_evidence(receipt: dict[str, Any]) -> dict[str, Any]:
    root = CAMPAIGN2_ROOT
    epoch = root / "epochs/20261010T040000Z"
    stop_path = root / "STOP"
    stop = _read(stop_path)
    summary = _read(epoch / "epoch-summary.json")
    plan = _read(epoch / "epoch-plan.json")
    reservation = _read(epoch / "frame-reservation.json")
    active_reservation = _read(root / "active-frame-reservation.json")
    release_path = epoch / "frame-reservation-release.json"
    request_path = epoch / "requests/000000000001.json"
    result_path = epoch / "request-results/000000000001.json"
    result = _read(result_path)
    capture = epoch / result["capture_path"]
    rows = _validate_journal(epoch, plan)
    _reconcile_epoch(epoch, plan, reservation)
    expected_campaign = receipt["campaign"]
    expected_epoch = receipt["epoch"]
    if (
        len(rows) != 1
        or _sha(stop_path) != CAMPAIGN2_STOP_SHA256
        or _sha(stop_path) != expected_campaign["stop_sha256"]
        or _sha(epoch / "epoch-summary.json") != expected_campaign["epoch_summary_sha256"]
        or _sha(epoch / "epoch-plan.json") != expected_epoch["plan_sha256"]
        or _sha(epoch / "STOP") != expected_epoch["epoch_stop_sha256"]
        or _sha(request_path) != expected_epoch["request_file_sha256"]
        or _sha(result_path) != expected_epoch["request_result_sha256"]
        or _sha(release_path) != expected_epoch["release_sha256"]
        or result.get("method") != "getSlot"
        or result.get("slot_value") != ANCHOR_SLOT
        or result.get("receipt_ns") != ANCHOR_NS
        or result.get("availability_ns") != ANCHOR_NS
        or hashlib.sha256(Capture.load(capture).response).hexdigest()
        != expected_epoch["response_sha256"]
        or _sha(capture / "response.json.zst") != expected_epoch["stored_response_sha256"]
        or summary.get("request_reservations") != 1
        or summary.get("status") != "bootstrap_complete"
        or stop.get("reason") != "missed_schedule_gap_requires_review"
        or plan.get("gap_reset_anchor_only") is not True
        or plan.get("missed_schedule_buckets") != ["20261010T030000Z"]
        or rows[0].get("phase") != "anchor"
        or summary.get("captured_response_bytes") != 43
        or summary.get("stored_response_bytes") != 56
        or expected_epoch.get("inventory_requests") != 0
        or expected_epoch.get("block_body_requests") != 0
        or receipt.get("frontier", {}).get("gap_intervals") != GAPS
        or active_reservation.get("status") != "reconciled"
        or active_reservation.get("actual_requests") != 1
        or active_reservation.get("actual_raw_bytes") != 43
        or active_reservation.get("actual_stored_bytes") != 56
    ):
        raise ValueError("c2_receipt_or_raw_reconciliation_failed")
    usage = _usage(root / "epochs")
    if usage != {
        "requests": 1,
        "raw_bytes": 43,
        "stored_bytes": 56,
        "reserved_raw_bytes": 0,
        "reserved_stored_bytes": 0,
    }:
        raise ValueError("c2_usage_not_exact")
    return {
        "root": str(root),
        "stop_sha256": _sha(stop_path),
        "summary_sha256": _sha(epoch / "epoch-summary.json"),
        "plan_sha256": _sha(epoch / "epoch-plan.json"),
        "epoch_stop_sha256": _sha(epoch / "STOP"),
        "request_sha256": _sha(request_path),
        "request_result_sha256": _sha(result_path),
        "release_sha256": _sha(release_path),
        "raw_response_sha256": hashlib.sha256(Capture.load(capture).response).hexdigest(),
        "compressed_capture_sha256": _sha(capture / "response.json.zst"),
        "usage": {"requests": 1, "raw_bytes": 43, "stored_bytes": 56},
    }


def _validate_authority() -> tuple[dict[str, Any], dict[str, Any]]:
    if not RECEIPT.is_file() or _sha(RECEIPT) != RECEIPT_SHA256:
        raise ValueError("c2_final_receipt_missing_or_changed")
    receipt = _read(RECEIPT)
    evidence = _campaign2_evidence(receipt)
    controller_state = _read(CONTROLLER_ROOT / "controller-state.json")
    controller_stop_path = CONTROLLER_ROOT / "STOP"
    controller_stop = _read(controller_stop_path)
    policy = _read(CONTROLLER_ROOT / "controller-policy.json")
    if (
        _sha(controller_stop_path) != CONTROLLER_STOP_SHA256
        or _sha(CONTROLLER_ROOT / "controller-state.json")
        != receipt["controller"]["controller_state_sha256"]
        or controller_stop.get("reason") != "abnormal_predecessor_stop:ValueError"
        or controller_state.get("controller_id") != CONTROLLER_ID
        or controller_state.get("status") != "stopped"
        or controller_state.get("campaign_count") != 2
        or controller_state.get("current_campaign_id") != CAMPAIGN2_ID
        or controller_state.get("aggregate_requests") != 0
        or controller_state.get("aggregate_raw_bytes") != 0
        or controller_state.get("aggregate_stored_bytes") != 0
        or controller_state.get("lease_expires_ns") != receipt["controller"]["lease_expires_ns"]
        or policy.get("aggregate_request_cap") != LEASE_REQUESTS
        or policy.get("aggregate_raw_byte_cap") != LEASE_RAW_BYTES
        or policy.get("aggregate_stored_byte_cap") != LEASE_STORED_BYTES
    ):
        raise ValueError("stopped_controller_binding_changed")
    c1_stop = _read(CAMPAIGN1_ROOT / "STOP")
    c1_usage = _usage(CAMPAIGN1_ROOT / "epochs")
    if (
        c1_stop.get("campaign_id") != CAMPAIGN1_ID
        or c1_usage["requests"] != 0
        or c1_usage["raw_bytes"] != 0
        or c1_usage["stored_bytes"] != 0
        or c1_usage["reserved_raw_bytes"] != 0
        or c1_usage["reserved_stored_bytes"] != 0
    ):
        raise ValueError("c1_zero_io_charge_not_proven")
    return receipt, {
        "controller_state_sha256": _sha(CONTROLLER_ROOT / "controller-state.json"),
        "controller_stop_sha256": _sha(controller_stop_path),
        "controller_policy_sha256": _sha(CONTROLLER_ROOT / "controller-policy.json"),
        "c1_stop_sha256": _sha(CAMPAIGN1_ROOT / "STOP"),
        "c1_usage": c1_usage,
        "c2": evidence,
        "lease_start_ns": controller_state["lease_start_ns"],
        "lease_expires_ns": controller_state["lease_expires_ns"],
    }


def _record(root: Path) -> dict[str, Any]:
    record_path = root / "handoff-v1.json"
    record = _read(record_path)
    if record.get("handoff_id") != HANDOFF_ID or _sha(RECEIPT) != record.get("c2_receipt_sha256"):
        raise ValueError("c3_handoff_record_binding_changed")
    _, facts = _validate_authority()
    for key, value in facts.items():
        if record.get("authority", {}).get(key) != value:
            raise ValueError("c3_authority_binding_changed")
    if record.get("campaign_id") != CAMPAIGN3_ID:
        raise ValueError("c3_campaign_id_invalid")
    return record


def prepare(*, now_ns: int | None = None) -> dict[str, Any]:
    now_ns = now_ns or time.time_ns()
    if CAMPAIGN3_ROOT.exists():
        raise ValueError("c3_root_already_exists_single_use")
    receipt, authority = _validate_authority()
    if now_ns >= authority["lease_expires_ns"]:
        raise ValueError("original_lease_expired")
    if (
        authority["c1_usage"]["requests"]
        + authority["c2"]["usage"]["requests"]
        + ATTEMPTS_MAX * REQUESTS_PER_FRAME
        > LEASE_REQUESTS
        or authority["c1_usage"]["raw_bytes"]
        + authority["c2"]["usage"]["raw_bytes"]
        + CAMPAIGN_RAW_BYTES
        > LEASE_RAW_BYTES
        or authority["c1_usage"]["stored_bytes"]
        + authority["c2"]["usage"]["stored_bytes"]
        + CAMPAIGN_STORED_BYTES
        > LEASE_STORED_BYTES
    ):
        raise ValueError("original_lease_aggregate_cap")
    required_disk = (
        DISK_FLOOR_BYTES + FRAME_RAW_BYTES + FRAME_STORED_BYTES + PEAK_WRITE_MARGIN_BYTES
    )
    free_disk = shutil.disk_usage(REPO_ROOT).free
    if free_disk < required_disk:
        raise ValueError("disk_floor_plus_frame_envelope")
    label = f"ai.memecoin.brain.senses.{CAMPAIGN3_ID}"
    lineage = {
        "schema_version": 1,
        "provider": "helius-free",
        "campaign_id": CAMPAIGN3_ID,
        "controller_id": CONTROLLER_ID,
        "predecessor_campaign_id": CAMPAIGN2_ID,
        "predecessor_stop_sha256": authority["c2"]["stop_sha256"],
        "predecessor_manifest_sha256": receipt["campaign"]["manifest_sha256"],
        "predecessor_receipt_sha256": RECEIPT_SHA256,
        "predecessor_controller_stop_sha256": CONTROLLER_STOP_SHA256,
        "last_attempt_bucket": "20261010T040000Z",
        "last_observed_anchor_slot": ANCHOR_SLOT,
        "last_observed_anchor_receipt_ns": ANCHOR_NS,
        "last_inventoried_interval": receipt["frontier"]["inventoried_interval"],
        "gap_intervals": GAPS,
        "predecessor_status": (
            "missed_schedule_gap_requires_review; anchor adopted as observed frontier only"
        ),
        "predecessor_campaign_usage": authority["c2"]["usage"],
        "controller_lease_expires_ns": authority["lease_expires_ns"],
    }
    record = {
        "schema_version": 1,
        "handoff_id": HANDOFF_ID,
        "created_ns": now_ns,
        "campaign_id": CAMPAIGN3_ID,
        "campaign_label": label,
        "campaign_root": str(CAMPAIGN3_ROOT),
        "single_use": True,
        "c2_receipt_sha256": RECEIPT_SHA256,
        "authority": authority,
        "lineage_sha256": hashlib.sha256(canonical(lineage).encode()).hexdigest(),
        "seeded_frontier": FRONTIER,
        "seeded_anchor": {"slot": ANCHOR_SLOT, "availability_ns": ANCHOR_NS},
        "gap_intervals": GAPS,
        "observed_frontier_is_inventory": False,
        "campaign_limits": {
            "attempts": ATTEMPTS_MAX,
            "frame_directories": ATTEMPTS_MAX,
            "requests": ATTEMPTS_MAX * REQUESTS_PER_FRAME,
            "raw_bytes": CAMPAIGN_RAW_BYTES,
            "stored_bytes": CAMPAIGN_STORED_BYTES,
            "all_campaign_ids_counted": True,
        },
        "first_frame": {
            "expected_bucket": "next future hourly bucket",
            "start_inclusive": FRONTIER,
            "upper_anchor": "new finalized getSlot required before inventory",
            "backfill": False,
            "bootstrap_request": False,
        },
        "disk_free_at_prepare": free_disk,
        "required_disk_floor_and_frame_envelope": required_disk,
    }
    CAMPAIGN3_ROOT.mkdir(parents=True, mode=0o700)
    lineage_path = CAMPAIGN3_ROOT / "predecessor-lineage.json"
    fd = os.open(lineage_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(canonical(lineage) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_dir(CAMPAIGN3_ROOT)
    initialize(CAMPAIGN3_ROOT, lineage_path)
    manifest = _read(CAMPAIGN3_ROOT / "schedule-v3.json")
    frontier = _read(CAMPAIGN3_ROOT / "frontier-v3.json")
    if (
        manifest.get("schedule_id") != CAMPAIGN3_ID
        or frontier.get("observed_frontier") != FRONTIER
        or frontier.get("observed_anchor_slot") != ANCHOR_SLOT
        or frontier.get("observed_anchor_receipt_ns") != ANCHOR_NS
        or frontier.get("gap_intervals") != GAPS
        or frontier.get("last_inventoried_interval") != lineage["last_inventoried_interval"]
    ):
        raise ValueError("c3_offline_seed_verification_failed")
    now = time.time_ns() // 1_000_000_000
    next_hour = (now // 3600 + 1) * 3600
    future_bucket = time.strftime("%Y%m%dT%H0000Z", time.gmtime(next_hour))
    plan = build_epoch_plan(
        epoch_id=future_bucket,
        bucket=future_bucket,
        schedule_id=CAMPAIGN3_ID,
        schedule_sha256=hashlib.sha256(canonical(manifest).encode()).hexdigest(),
        seed=hashlib.sha256(f"{CAMPAIGN3_ID}\0{future_bucket}".encode()).hexdigest(),
        bootstrap=False,
        next_start=FRONTIER,
        frame_width_cap=FRAME_WIDTH_CAP,
        selection_count=SELECTION_COUNT,
        request_budget=REQUESTS_PER_FRAME,
        raw_byte_budget=FRAME_RAW_BYTES,
        stored_byte_budget=FRAME_STORED_BYTES,
        minimum_free_disk_bytes=DISK_FLOOR_BYTES,
        capture_storage_codec="zstd",
    )
    if plan.get("bootstrap") is not False or plan.get("next_start") != FRONTIER:
        raise ValueError("c3_forward_only_offline_plan_failed")
    record["first_frame"]["expected_bucket"] = future_bucket
    record["first_frame"]["plan_sha256"] = hashlib.sha256(canonical(plan).encode()).hexdigest()
    record_path = CAMPAIGN3_ROOT / "handoff-v1.json"
    record["offline_plan_sha256"] = hashlib.sha256(canonical(plan).encode()).hexdigest()
    fd = os.open(record_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(canonical(record) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_dir(CAMPAIGN3_ROOT)
    return {
        "status": "prepared_inactive",
        "campaign_id": CAMPAIGN3_ID,
        "campaign_root": str(CAMPAIGN3_ROOT),
        "handoff_sha256": _sha(record_path),
        "manifest_sha256": _sha(CAMPAIGN3_ROOT / "schedule-v3.json"),
        "frontier_sha256": _sha(CAMPAIGN3_ROOT / "frontier-v3.json"),
        "first_frame_start_inclusive": frontier["observed_frontier"],
        "first_frame_bucket": future_bucket,
        "offline_plan_sha256": hashlib.sha256(canonical(plan).encode()).hexdigest(),
        "gap_intervals": frontier["gap_intervals"],
        "requests_max_effective": ATTEMPTS_MAX * REQUESTS_PER_FRAME,
        "network_requests": 0,
    }


def activate(*, launch_agents: Path | None = None, runner=subprocess.run) -> dict[str, Any]:
    record = _record(CAMPAIGN3_ROOT)
    if (
        (CAMPAIGN3_ROOT / "STOP").exists()
        or _attempts(CAMPAIGN3_ROOT / "epochs")
        or (CAMPAIGN3_ROOT / "active-frame-reservation.json").exists()
    ):
        raise ValueError("c3_not_pristine_at_activation")
    launch_agents = launch_agents or Path.home() / "Library/LaunchAgents"
    label = f"ai.memecoin.brain.senses.{CAMPAIGN3_ID}"
    stale_labels = [
        CONTROLLER_LABEL,
        f"ai.memecoin.brain.senses.{CAMPAIGN1_ID}",
        f"ai.memecoin.brain.senses.{CAMPAIGN2_ID}",
        label,
    ]
    if any(_loaded(item, runner=runner) for item in stale_labels):
        raise ValueError("duplicate_or_predecessor_service_loaded")
    now_ns = time.time_ns()
    if now_ns >= record["authority"]["lease_expires_ns"]:
        raise ValueError("original_lease_expired")
    first_bucket = datetime.strptime(
        record["first_frame"]["expected_bucket"], "%Y%m%dT%H0000Z"
    ).replace(tzinfo=UTC)
    first_bucket_ns = int(first_bucket.timestamp() * 1_000_000_000)
    if now_ns >= first_bucket_ns + 5 * 60 * 1_000_000_000:
        raise ValueError("c3_first_frame_trigger_missed")
    required_disk = (
        DISK_FLOOR_BYTES + FRAME_RAW_BYTES + FRAME_STORED_BYTES + PEAK_WRITE_MARGIN_BYTES
    )
    if shutil.disk_usage(REPO_ROOT).free < required_disk:
        raise ValueError("disk_floor_plus_frame_envelope")
    lineage = CAMPAIGN3_ROOT / "predecessor-lineage.json"
    plist = _campaign_plist(CAMPAIGN3_ROOT, lineage, label)
    plist_path = launch_agents / f"{label}.plist"
    payload = plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True)
    if plist_path.exists():
        raise ValueError("c3_plist_already_exists_single_use")
    fd = os.open(plist_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_dir(launch_agents)
    result = runner(
        ["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0 and not _loaded(label, runner=runner):
        raise ValueError("c3_bootstrap_failed")
    return {
        "status": "activated",
        "campaign_id": CAMPAIGN3_ID,
        "label": label,
        "plist": str(plist_path),
        "plist_sha256": _sha(plist_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "activate"))
    args = parser.parse_args(argv)
    result = prepare() if args.action == "prepare" else activate()
    print(canonical(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
