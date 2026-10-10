#!/usr/bin/env python3
"""Read-only terminal qualification for the finite Helius c4 campaign.

The only campaign-root write this tool can make is the create-only derived
``terminal-receipt.json``, after natural STOP and the post-STOP observation window.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import stat
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
from tools import brain_senses_block_epoch_v2 as epoch_v2  # noqa: E402
from tools import brain_senses_block_epoch_v3_schedule as schedule_v3  # noqa: E402
from tools import brain_senses_campaign4_recovery as campaign4  # noqa: E402

QUALIFIER_ID = "helius_c4_terminal_qualifier_v1"
CAMPAIGN_ID = campaign4.CAMPAIGN_ID
LABEL = campaign4.LABEL
RECEIPT_NAME = "terminal-receipt.json"
OBSERVATION_WINDOW_NS = 70 * 60 * 1_000_000_000
FIRST_OBSERVATION_LIMIT_NS = 7 * 60 * 1_000_000_000
CLOCK_TOLERANCE_NS = 120 * 1_000_000_000
PROVIDER_CHARGE_UNITS = "unknown_unverified"
EXCLUDED_RUNTIME_FILES = {
    ".c4.lock",
    ".schedule-v3.lock",
    "supervisor/stdout.log",
    "supervisor/stderr.log",
}
POLICY = {
    "schema_version": 1,
    "qualifier_id": QUALIFIER_ID,
    "campaign_id": CAMPAIGN_ID,
    "normal_stop_reason": "finite_attempt_cap_reached",
    "successful_frame_count": 24,
    "requests_per_frame": 34,
    "bootstrap_requests": 1,
    "post_stop_observation_window_ns": OBSERVATION_WINDOW_NS,
    "first_observation_limit_ns": FIRST_OBSERVATION_LIMIT_NS,
    "clock_tolerance_ns": CLOCK_TOLERANCE_NS,
    "never_backfill_or_retry": True,
    "post_stop_claim_scope": "c4-attributable requests through pinned local collector paths",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("terminal_evidence_not_regular_file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("terminal_evidence_not_object")
    return value


def _stable_hash(path: Path) -> tuple[int, int, str, int]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise ValueError("terminal_artifact_not_regular_file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        h = hashlib.sha256()
        total = 0
        while chunk := os.read(fd, 1024 * 1024):
            total += len(chunk)
            h.update(chunk)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    final = path.lstat()

    def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    if (
        identity(before) != identity(opened)
        or identity(opened) != identity(after)
        or identity(after) != identity(final)
    ):
        raise ValueError("terminal_artifact_changed_while_reading")
    if total != after.st_size:
        raise ValueError("terminal_artifact_size_changed")
    return total, after.st_mtime_ns, h.hexdigest(), int(getattr(after, "st_blocks", 0)) * 512


def _inventory(root: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    totals = {"logical_bytes": 0, "allocated_bytes": 0, "max_mtime_ns": 0}
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in list(dirs):
            path = current_path / name
            if path.is_symlink():
                raise ValueError("terminal_artifact_symlink_directory")
        for name in files:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if relative == RECEIPT_NAME or relative in EXCLUDED_RUNTIME_FILES:
                continue
            size, mtime_ns, digest, allocated = _stable_hash(path)
            rows.append(
                {
                    "path": relative,
                    "bytes": size,
                    "mtime_ns": mtime_ns,
                    "sha256": digest,
                }
            )
            totals["logical_bytes"] += size
            totals["allocated_bytes"] += allocated
            totals["max_mtime_ns"] = max(totals["max_mtime_ns"], mtime_ns)
    rows.sort(key=lambda row: row["path"])
    return rows, totals


def _nearest_rank(values: list[int | float], percentile: float) -> int | float:
    if not values or not 0 < percentile <= 1:
        raise ValueError("terminal_quantile_input_invalid")
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return ordered[index]


def _distribution(values: list[int | float], unit: str) -> dict[str, Any]:
    if not values:
        raise ValueError("terminal_distribution_empty")
    ordered = sorted(values)
    n = len(ordered)
    median: int | float
    if n % 2:
        median = ordered[n // 2]
    else:
        median = (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    return {
        "samples": n,
        "unit": unit,
        "min": ordered[0],
        "median": median,
        "p95_nearest_rank": _nearest_rank(ordered, 0.95),
        "max": ordered[-1],
    }


def _assert_zero_request_suffix(root: Path, stop_ns: int) -> None:
    request_roots = [root / "bootstrap", root / "epochs"]
    for base in request_roots:
        if not base.exists():
            continue
        for result_path in base.rglob("request-results/*.json"):
            result = _read(result_path)
            started = result.get("request_started_ns")
            received = result.get("receipt_ns")
            available = result.get("availability_ns")
            if type(started) is not int or type(received) is not int or type(available) is not int:
                raise ValueError("terminal_request_time_missing")
            if not started <= received <= available <= stop_ns:
                raise ValueError("terminal_post_stop_request_found")
        for request_path in base.rglob("requests/*.json"):
            request = _read(request_path)
            reserved = request.get("reserved_ns")
            if type(reserved) is not int or reserved > stop_ns:
                raise ValueError("terminal_post_stop_reservation_found")


def _validate_journal_read_only(
    epoch: Path, plan: dict[str, Any]
) -> tuple[list[dict[str, Any]], int, int]:
    """Pure v2 identity/capture check; unlike recovery helpers, never seals or repairs."""
    request_files = sorted((epoch / "requests").glob("*.json"))
    result_files = sorted((epoch / "request-results").glob("*.json"))
    if [p.name for p in request_files] != [p.name for p in result_files]:
        raise ValueError("terminal_request_result_set_mismatch")
    selection = _read(epoch / "selection-plan.json") if not plan["bootstrap"] else None
    operations = epoch_v2._operations_for(epoch, plan, selection)
    if len(request_files) != len(operations):
        raise ValueError("terminal_operation_count_mismatch")
    rows: list[dict[str, Any]] = []
    raw = stored = 0
    for sequence, (request_path, result_path, operation) in enumerate(
        zip(request_files, result_files, operations, strict=True), start=1
    ):
        expected_name = f"{sequence:012d}.json"
        if request_path.name != expected_name or result_path.name != expected_name:
            raise ValueError("terminal_journal_sequence_gap")
        reservation = _read(request_path)
        result = _read(result_path)
        request_body = epoch_v2._request_body(operation["method"], operation["params"])
        if (
            reservation.get("sequence") != sequence
            or reservation.get("phase") != operation["phase"]
            or reservation.get("provider") != plan["provider"]
            or reservation.get("method") != operation["method"]
            or reservation.get("params") != operation["params"]
            or reservation.get("request_sha256") != _sha(request_body)
            or reservation.get("advance_to") != operation.get("advance_to")
            or not epoch_v2._result_identity(result, operation, sequence)
            or result.get("status") != "captured"
            or result.get("result_status") != "parsed"
        ):
            raise ValueError("terminal_journal_identity_mismatch")
        capture_path = epoch / str(result.get("capture_path", ""))
        if capture_path != epoch / "captures" / f"{sequence:012d}":
            raise ValueError("terminal_capture_path_invalid")
        capture = Capture.load(capture_path)
        if (
            capture.method != operation["method"]
            or capture.request_json.encode() != request_body
            or capture.source_alias != plan["provider"]
            or capture.request_started_ns != result.get("request_started_ns")
            or capture.received_ns != result.get("receipt_ns")
            or result.get("response_sha256") != _sha(capture.response)
            or result.get("response_bytes") != len(capture.response)
        ):
            raise ValueError("terminal_capture_reconciliation_failed")
        stored_path = capture_path / "response.json.zst"
        if stored_path.is_file():
            stored_size = stored_path.stat().st_size
        else:
            stored_path = capture_path / "response.json"
            stored_size = stored_path.stat().st_size
        if result.get("stored_response_bytes") != stored_size:
            raise ValueError("terminal_stored_capture_size_mismatch")
        raw += len(capture.response)
        stored += stored_size
        rows.append(result)
    return rows, raw, stored


def _validate_epoch(
    root: Path, bucket: str, expected_start: int
) -> tuple[dict[str, Any], list[dict[str, Any]], int, int]:
    epoch = root / "epochs" / bucket
    plan = _read(epoch / "epoch-plan.json")
    frame = _read(epoch / "frame.json")
    selection = _read(epoch / "selection-plan.json")
    summary = _read(epoch / "epoch-summary.json")
    epoch_stop = _read(epoch / "STOP")
    reservation = _read(epoch / "frame-reservation.json")
    release = _read(epoch / "frame-reservation-release.json")
    if (
        plan.get("epoch_id") != bucket
        or plan.get("bucket") != bucket
        or plan.get("schedule_id") != CAMPAIGN_ID
        or plan.get("provider") != "helius-free"
        or plan.get("bootstrap") is not False
        or plan.get("start") != expected_start
        or plan.get("request_budget") != campaign4.REQUESTS_PER_FRAME
        or plan.get("raw_byte_budget") != schedule_v3.FRAME_RAW_BYTES
        or plan.get("stored_byte_budget") != schedule_v3.FRAME_STORED_BYTES
        or plan.get("capture_storage_codec") != "zstd"
        or plan.get("code_sha256") != epoch_v2._sha(Path(epoch_v2.__file__))
        or frame.get("status") != "captured"
        or frame.get("start") != expected_start
        or frame.get("end_exclusive", 0) <= expected_start
        or frame.get("width") != frame.get("end_exclusive") - expected_start
        or frame.get("width", 0) > schedule_v3.FRAME_WIDTH_CAP
        or selection.get("epoch_id") != bucket
        or selection.get("selected_slots") != summary.get("selected_slots")
        or len(selection.get("selected_slots", [])) != schedule_v3.SELECTION_COUNT
        or summary.get("status") != "frame_complete"
        or summary.get("request_reservations") != campaign4.REQUESTS_PER_FRAME
        or summary.get("start") != expected_start
        or summary.get("end_exclusive") != frame.get("end_exclusive")
        or summary.get("stop_sha256") != _sha((epoch / "STOP").read_bytes())
        or epoch_stop.get("status") != "frame_complete"
        or reservation.get("bucket") != bucket
        or reservation.get("request_count") != campaign4.REQUESTS_PER_FRAME
        or reservation.get("plan_sha256") != _sha((epoch / "epoch-plan.json").read_bytes())
        or release.get("bucket") != bucket
        or release.get("reservation_sha256")
        != _sha((epoch / "frame-reservation.json").read_bytes())
        or release.get("summary_sha256") != _sha((epoch / "epoch-summary.json").read_bytes())
    ):
        raise ValueError("terminal_frame_contract_mismatch")
    rows, raw, stored = _validate_journal_read_only(epoch, plan)
    inventory_rows = [row for row in rows if row["phase"] == "inventory"]
    if len(inventory_rows) != 1:
        raise ValueError("terminal_inventory_request_missing")
    inventory_row = inventory_rows[0]
    inventory = epoch_v2._parse_inventory(
        inventory_row,
        frame["start"],
        frame["end_exclusive"],
    )
    if inventory is None:
        raise ValueError("terminal_inventory_payload_invalid")
    expected_selection = epoch_v2._build_selection(
        plan=plan,
        inventory_slots=inventory,
        inventory_sha256=inventory_row["response_sha256"],
    )
    if selection != expected_selection:
        raise ValueError("terminal_selection_recomputation_mismatch")
    structural_checks = summary.get("structural_checks")
    if (
        summary.get("captured_response_bytes") != raw
        or summary.get("stored_response_bytes") != stored
        or not isinstance(structural_checks, list)
        or len(structural_checks) != campaign4.SELECTION_COUNT
        or any(
            row.get("status") != "qualified_structural_reference_count" for row in structural_checks
        )
        or any(row.get("block_identity_equal") is not True for row in structural_checks)
        or any(row.get("signature_order_equal") is not True for row in structural_checks)
        or [row.get("slot") for row in structural_checks] != selection.get("selected_slots")
        or release.get("actual_requests") != len(rows)
        or release.get("actual_raw_bytes") != raw
        or release.get("actual_stored_bytes") != stored
        or type(release.get("released_ns")) is not int
        or release["released_ns"] < summary.get("sealed_ns", 0)
        or any(type(row.get("request_started_ns")) is not int for row in rows)
    ):
        raise ValueError("terminal_frame_accounting_mismatch")
    by_slot_phase = {
        (row.get("slot"), row.get("phase")): row
        for row in rows
        if row.get("phase") in {"full_block", "signature_check"}
    }
    for check in structural_checks:
        slot = check["slot"]
        full_row = by_slot_phase.get((slot, "full_block"))
        signature_row = by_slot_phase.get((slot, "signature_check"))
        if full_row is None or signature_row is None:
            raise ValueError("terminal_structural_pair_missing")
        full_capture = Capture.load(epoch / full_row["capture_path"])
        signature_capture = Capture.load(epoch / signature_row["capture_path"])
        full_value = full_capture.result()
        signature_value = signature_capture.result()
        if (
            epoch_v2._structural_response_validation(full_value, slot)
            or epoch_v2._signature_list(full_value, "full")
            != epoch_v2._signature_list(signature_value, "signatures")
            or full_value.get("blockhash") != signature_value.get("blockhash")
            or full_value.get("parentSlot") != signature_value.get("parentSlot")
            or full_value.get("previousBlockhash") != signature_value.get("previousBlockhash")
        ):
            raise ValueError("terminal_structural_capture_reconciliation_failed")
        recomputed_structural = epoch_v2._pump_invocations(full_value)
        if (
            recomputed_structural != check.get("structural_references")
            or recomputed_structural.get("unresolved_instruction_shapes") != 0
            or recomputed_structural.get("compiled_transaction_normalization_failures")
        ):
            raise ValueError("terminal_structural_summary_recomputation_mismatch")
    return {"bucket": bucket, "frame": frame, "summary": summary}, rows, raw, stored


def _validate_bootstrap(root: Path) -> tuple[dict[str, Any], int, int]:
    lease = _read(root / "lease-v1.json")
    bootstrap = lease["bootstrap"]
    bucket = bootstrap["bucket"]
    epoch = root / "bootstrap" / bucket
    plan = _read(epoch / "epoch-plan.json")
    summary = _read(epoch / "epoch-summary.json")
    epoch_stop = _read(epoch / "STOP")
    reservation = _read(root / "bootstrap-reservation.json")
    receipt = _read(root / "bootstrap-receipt.json")
    lineage = _read(root / "predecessor-lineage.json")
    if (
        plan.get("bootstrap") is not True
        or plan.get("bucket") != bucket
        or plan.get("schedule_id") != CAMPAIGN_ID
        or plan.get("provider") != "helius-free"
        or reservation.get("status") != "reserved_before_io"
        or reservation.get("request_limit") != 1
        or receipt.get("campaign_id") != CAMPAIGN_ID
        or receipt.get("bucket") != bucket
        or receipt.get("method") != "getSlot(finalized)"
        or summary.get("status") != "bootstrap_complete"
        or epoch_stop.get("status") != "bootstrap_complete"
        or receipt.get("summary_sha256") != _sha((epoch / "epoch-summary.json").read_bytes())
        or receipt.get("plan_sha256") != _sha((epoch / "epoch-plan.json").read_bytes())
        or lease["bootstrap"].get("receipt_sha256")
        != _sha((root / "bootstrap-receipt.json").read_bytes())
        or lineage.get("bootstrap_receipt_sha256") != lease["bootstrap"].get("receipt_sha256")
        or summary.get("request_reservations") != 1
        or summary.get("bootstrap_slot") != receipt.get("observed_anchor_slot")
        or lineage.get("last_observed_anchor_slot") != receipt.get("observed_anchor_slot")
    ):
        raise ValueError("terminal_bootstrap_contract_mismatch")
    rows, raw, stored = _validate_journal_read_only(epoch, plan)
    if (
        len(rows) != 1
        or rows[0].get("slot_value") != receipt.get("observed_anchor_slot")
        or raw != summary.get("captured_response_bytes")
        or stored != summary.get("stored_response_bytes")
    ):
        raise ValueError("terminal_bootstrap_accounting_mismatch")
    return {"bucket": bucket, "receipt": receipt, "lineage": lineage}, raw, stored


def _load_observations(
    paths: list[Path], root: Path, stop: dict[str, Any], stop_hash: str, inventory_hash: str
) -> list[dict[str, Any]]:
    if len(paths) < 2:
        raise ValueError("terminal_observation_pair_required")
    values = []
    for path in paths:
        if path.is_symlink():
            raise ValueError("terminal_observation_symlink")
        resolved = path.resolve(strict=True)
        if resolved.is_relative_to(root.resolve()):
            raise ValueError("terminal_observations_must_be_external")
        size, _mtime_ns, digest, _allocated = _stable_hash(resolved)
        payload = resolved.read_bytes()
        if len(payload) != size or _sha(payload) != digest:
            raise ValueError("terminal_observation_changed_while_reading")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("terminal_observation_not_object")
        if (
            value.get("schema_version") != 1
            or value.get("status") != "operational_snapshot"
            or value.get("campaign_id") != CAMPAIGN_ID
            or value.get("stop_sha256") != stop_hash
            or value.get("qualifier_sha256") != _sha(Path(__file__).read_bytes())
            or value.get("artifact_inventory_sha256") != inventory_hash
            or type(value.get("observed_at_ns")) is not int
            or value["observed_at_ns"] < stop["time_ns"]
            or value["observed_at_ns"] > time.time_ns() + CLOCK_TOLERANCE_NS
            or value.get("launchagent_loaded") is not False
            or value.get("c4_process_pids") != []
            or value.get("senses_writer_pids") != []
            or value.get("unified_log_query_status") != "complete"
            or value.get("sensitive_value_detected") is not False
        ):
            raise ValueError("terminal_operational_observation_invalid")
        values.append({"path": str(resolved), "sha256": digest, **value})
    values.sort(key=lambda row: row["observed_at_ns"])
    first, last = values[0], values[-1]
    stop_ns = stop["time_ns"]
    if (
        first["observed_at_ns"] > stop_ns + FIRST_OBSERVATION_LIMIT_NS + CLOCK_TOLERANCE_NS
        or last["observed_at_ns"] < stop_ns + OBSERVATION_WINDOW_NS
        or last["observed_at_ns"] - first["observed_at_ns"]
        < OBSERVATION_WINDOW_NS - FIRST_OBSERVATION_LIMIT_NS
        or any(value.get("campaign_log_events") != 0 for value in values)
        or any(
            value.get("unified_log_start_ns", stop_ns) > stop_ns + CLOCK_TOLERANCE_NS
            for value in values
        )
        or any(
            value.get("unified_log_end_ns", 0) < value["observed_at_ns"] - CLOCK_TOLERANCE_NS
            for value in values
        )
    ):
        raise ValueError("terminal_observation_window_incomplete")
    return values


def _campaign_facts(root: Path) -> dict[str, Any]:
    policy = campaign4._policy(root)
    stop = _read(root / "STOP")
    if (
        stop.get("campaign_id") != CAMPAIGN_ID
        or stop.get("reason") != "finite_attempt_cap_reached"
        or stop.get("attempt_count") != campaign4.MAX_ATTEMPTS
        or stop.get("active_reservation_status") != "reconciled"
        or stop.get("accounting_error_class") is not None
        or type(stop.get("time_ns")) is not int
    ):
        raise ValueError("terminal_stop_not_normal_finite_cap")
    status = campaign4.read_status(root)
    if (
        status.get("attempts") != campaign4.MAX_ATTEMPTS
        or status.get("successful_epochs") != campaign4.MAX_ATTEMPTS
        or status.get("failed_epochs") != 0
        or status.get("gapped_epochs") != 0
        or status.get("active_frame_status") != "reconciled"
        or status.get("stop_reason") != "finite_attempt_cap_reached"
        or status.get("requests") != campaign4.MAX_ATTEMPTS * campaign4.REQUESTS_PER_FRAME
    ):
        raise ValueError("terminal_campaign_status_incomplete")
    bootstrap, bootstrap_raw, bootstrap_stored = _validate_bootstrap(root)
    lineage = bootstrap["lineage"]
    expected_gaps = lineage.get("gap_intervals")
    frontier = _read(root / "frontier-v3.json")
    if frontier.get("gap_intervals") != expected_gaps:
        raise ValueError("terminal_unexpected_gap_lineage")
    epochs = sorted(
        path.name
        for path in (root / "epochs").iterdir()
        if path.is_dir() and len(path.name) == 16 and path.name.endswith("Z")
    )
    if len(epochs) != campaign4.MAX_ATTEMPTS:
        raise ValueError("terminal_frame_bucket_count_mismatch")
    first_dt = datetime.strptime(epochs[0], "%Y%m%dT%H0000Z").replace(tzinfo=UTC)
    for i, bucket in enumerate(epochs):
        expected_dt = first_dt.timestamp() + i * 3600
        if (
            datetime.strptime(bucket, "%Y%m%dT%H0000Z").replace(tzinfo=UTC).timestamp()
            != expected_dt
        ):
            raise ValueError("terminal_frame_buckets_not_hourly_consecutive")
    anchor = int(lineage["last_observed_anchor_slot"])
    start = anchor + 1
    frame_rows: list[dict[str, Any]] = []
    latencies_ms: list[int] = []
    anchor_to_seal_ms: list[int] = []
    widths: list[int] = []
    raw_total = stored_total = 0
    for bucket in epochs:
        summary, rows, raw, stored = _validate_epoch(root, bucket, start)
        start = summary["frame"]["end_exclusive"]
        frame_rows.append(summary)
        widths.append(summary["frame"]["width"])
        raw_total += raw
        stored_total += stored
        anchor_result = next(row for row in rows if row["phase"] == "anchor")
        anchor_to_seal_ms.append(
            (summary["summary"]["sealed_ns"] - anchor_result["receipt_ns"]) / 1_000_000
        )
        for row in rows:
            if (
                type(row.get("request_started_ns")) is not int
                or type(row.get("receipt_ns")) is not int
            ):
                raise ValueError("terminal_request_latency_missing")
            latencies_ms.append((row["receipt_ns"] - row["request_started_ns"]) / 1_000_000)
            if row["request_started_ns"] > stop["time_ns"] or row["receipt_ns"] > stop["time_ns"]:
                raise ValueError("terminal_post_stop_request_found")
    if (
        epochs[-1] != stop.get("active_bucket")
        or _sha((root / "epochs" / epochs[-1] / "epoch-summary.json").read_bytes())
        != stop.get("epoch_summary_sha256")
        or _sha((root / "epochs" / epochs[-1] / "frame-reservation.json").read_bytes())
        != stop.get("frame_reservation_sha256")
        or stop.get("observed_anchor_slot") != frontier.get("observed_anchor_slot")
        or stop.get("observed_frontier") != frontier.get("observed_frontier")
        or stop.get("observed_anchor_receipt_ns") != frontier.get("observed_anchor_receipt_ns")
        or status.get("all_in_requests")
        != bootstrap["receipt"].get("usage", {}).get("requests", 1) + status["requests"]
    ):
        raise ValueError("terminal_stop_frontier_binding_mismatch")
    all_in_requests = status["requests"] + 1
    all_in_raw = raw_total + bootstrap_raw
    all_in_stored = stored_total + bootstrap_stored
    if (
        all_in_requests > policy["bounds"]["total_requests"]
        or all_in_raw > policy["bounds"]["raw_bytes"]
        or all_in_stored > policy["bounds"]["stored_bytes"]
        or stop.get("requests") != status["requests"]
        or stop.get("raw_bytes") != raw_total
        or stop.get("stored_bytes") != stored_total
    ):
        raise ValueError("terminal_all_in_accounting_mismatch")
    _assert_zero_request_suffix(root, stop["time_ns"])
    return {
        "stop": stop,
        "bootstrap": bootstrap,
        "frame_buckets": epochs,
        "frame_summaries": frame_rows,
        "frontier": frontier,
        "requests": {
            "frames": status["requests"],
            "bootstrap": 1,
            "all_in": all_in_requests,
            "provider_charge_units": PROVIDER_CHARGE_UNITS,
        },
        "bytes": {
            "frame_raw": raw_total,
            "bootstrap_raw": bootstrap_raw,
            "all_in_raw": all_in_raw,
            "frame_stored": stored_total,
            "bootstrap_stored": bootstrap_stored,
            "all_in_stored": all_in_stored,
            "unit": "decimal bytes",
        },
        "measurements": {
            "request_round_trip": _distribution(latencies_ms, "ms"),
            "anchor_receipt_to_frame_seal": _distribution(anchor_to_seal_ms, "ms"),
            "frame_width": _distribution(widths, "slots"),
            "frames": len(epochs),
            "requests_per_frame": campaign4.REQUESTS_PER_FRAME,
        },
    }


def _inventory_digest(rows: list[dict[str, Any]]) -> str:
    return _sha(canonical(rows).encode())


def _iso_utc(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1_000_000_000, UTC).isoformat(timespec="seconds")


def _collect_snapshot(root: Path, output_dir: Path) -> Path:
    root = root.resolve(strict=True)
    stop = _read(root / "STOP")
    stop_ns = stop.get("time_ns")
    if type(stop_ns) is not int:
        raise ValueError("terminal_stop_time_missing")
    now_ns = time.time_ns()
    if now_ns < stop_ns:
        raise ValueError("terminal_clock_precedes_stop")
    rows, totals = _inventory(root)
    if totals["max_mtime_ns"] > stop_ns + CLOCK_TOLERANCE_NS:
        raise ValueError("terminal_campaign_artifact_written_after_stop")

    launchd = subprocess.run(
        ["/bin/launchctl", "list"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        timeout=20,
    )
    if launchd.returncode != 0:
        raise ValueError("terminal_launchctl_probe_failed")
    label_loaded = any(
        line.split() and line.split()[-1] == LABEL
        for line in launchd.stdout.decode(errors="replace").splitlines()
    )

    ps = subprocess.run(
        ["/bin/ps", "-axo", "pid=,command="],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        timeout=20,
    )
    writer_scripts = [
        str(path.resolve()) for path in (REPO_ROOT / "tools").glob("brain_senses_*.py")
    ]
    c4_script = str((REPO_ROOT / "tools/brain_senses_campaign4_recovery.py").resolve())
    c4_pids: list[int] = []
    writer_pids: list[int] = []
    for line in ps.stdout.decode(errors="replace").splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2:
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        command = fields[1]
        if c4_script in command:
            c4_pids.append(pid)
        if any(path in command for path in writer_scripts):
            writer_pids.append(pid)

    predicate = (
        'processImagePath CONTAINS[c] "brain_senses_campaign4_recovery.py" '
        f'or eventMessage CONTAINS[c] "{LABEL}"'
    )
    log_result = subprocess.run(
        [
            "/usr/bin/log",
            "show",
            "--style",
            "json",
            "--start",
            _iso_utc(stop_ns),
            "--end",
            _iso_utc(now_ns),
            "--predicate",
            predicate,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        timeout=90,
    )
    if log_result.returncode != 0:
        raise ValueError("terminal_unified_log_probe_failed")
    log_bytes = log_result.stdout
    sensitive = False
    env_path = REPO_ROOT / ".env.local"
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            name, separator, value = line.partition("=")
            secret = value.strip().strip("\"'") if separator else ""
            if name.strip() == "MEMECOIN_HELIUS_RPC_URL" and secret:
                sensitive = secret.encode() in log_bytes or secret.encode() in ps.stdout
                break
    try:
        decoded_logs = json.loads(log_bytes or b"[]")
        if isinstance(decoded_logs, dict):
            decoded_logs = decoded_logs.get("events", [])
        if not isinstance(decoded_logs, list):
            raise ValueError
        log_event_count = len(decoded_logs)
    except (json.JSONDecodeError, ValueError):
        raise ValueError("terminal_unified_log_output_invalid") from None

    inventory_hash = _inventory_digest(rows)
    observation = {
        "schema_version": 1,
        "status": "operational_snapshot",
        "campaign_id": CAMPAIGN_ID,
        "stop_sha256": _sha((root / "STOP").read_bytes()),
        "qualifier_sha256": _sha(Path(__file__).read_bytes()),
        "observed_at_ns": now_ns,
        "artifact_inventory_sha256": inventory_hash,
        "artifact_max_mtime_ns": totals["max_mtime_ns"],
        "launchagent_loaded": label_loaded,
        "launchctl_output_sha256": _sha(launchd.stdout),
        "c4_process_pids": sorted(c4_pids),
        "senses_writer_pids": sorted(writer_pids),
        "process_census_sha256": _sha(ps.stdout),
        "unified_log_query_status": "complete",
        "unified_log_start_ns": stop_ns,
        "unified_log_end_ns": now_ns,
        "unified_log_output_bytes": len(log_bytes),
        "unified_log_output_sha256": _sha(log_bytes),
        "campaign_log_events": log_event_count,
        "sensitive_value_detected": sensitive,
        "observation_scope": (
            "local campaign process, exact LaunchAgent, artifact mtimes, "
            "and filtered Unified Log query"
        ),
        "limitations": [
            "provider-side and unrelated host network activity are not observable here",
            "an empty log query is not evidence of network silence by itself",
        ],
    }
    output_dir = output_dir.resolve()
    if output_dir.is_relative_to(root):
        raise ValueError("terminal_observation_directory_must_be_external")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = output_dir / f"observation-{now_ns}.json"
    _write_create_only(path, observation)
    return path


def _write_create_only(path: Path, value: dict[str, Any]) -> None:
    payload = (canonical(value) + "\n").encode()
    temp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        temp.unlink(missing_ok=True)


def verify_receipt(root: Path, receipt_path: Path | None = None) -> dict[str, Any]:
    """Revalidate the frozen receipt against current c4 artifacts without writes or RPC."""
    root = root.resolve(strict=True)
    target = receipt_path or root / RECEIPT_NAME
    if target.resolve(strict=True) != root / RECEIPT_NAME:
        raise ValueError("terminal_receipt_path_mismatch")
    receipt = _read(target)
    if (
        receipt.get("schema_version") != 1
        or receipt.get("qualifier_id") != QUALIFIER_ID
        or receipt.get("campaign_id") != CAMPAIGN_ID
        or receipt.get("root") != str(root)
        or receipt.get("status") != "normal_finite_terminal"
        or receipt.get("post_stop_rpc_requests") != 0
        or type(receipt.get("post_stop_rpc_requests")) is not int
        or receipt.get("policy_sha256") != _sha(canonical(POLICY).encode())
        or receipt.get("qualifier_sha256") != _sha(Path(__file__).read_bytes())
    ):
        raise ValueError("terminal_receipt_contract_invalid")
    facts = _campaign_facts(root)
    rows, totals = _inventory(root)
    storage = receipt.get("physical_storage")
    if (
        receipt.get("stop_sha256") != _sha((root / "STOP").read_bytes())
        or receipt.get("artifact_index") != rows
        or receipt.get("artifact_inventory_sha256") != _inventory_digest(rows)
        or receipt.get("campaign_facts") != facts
        or not isinstance(storage, dict)
        or storage.get("logical_bytes") != totals["logical_bytes"]
        or storage.get("allocated_bytes") != totals["allocated_bytes"]
        or type(storage.get("free_bytes")) is not int
        or storage.get("measurement")
        != "st_size and st_blocks*512 over indexed acquisition artifacts; shutil.disk_usage"
        or type(storage.get("observed_at_ns")) is not int
        or storage["observed_at_ns"] < facts["stop"]["time_ns"]
        or storage["observed_at_ns"] > time.time_ns() + CLOCK_TOLERANCE_NS
    ):
        raise ValueError("terminal_receipt_evidence_changed")
    stop = facts["stop"]
    observation_paths = [
        Path(row["path"]) for row in receipt.get("observation_window", {}).get("snapshots", [])
    ]
    observations = _load_observations(
        observation_paths,
        root,
        stop,
        receipt["stop_sha256"],
        receipt["artifact_inventory_sha256"],
    )
    expected_refs = [{"path": row["path"], "sha256": row["sha256"]} for row in observations]
    if receipt["observation_window"].get("snapshots") != expected_refs:
        raise ValueError("terminal_observation_binding_changed")
    return receipt


def qualify(root: Path, observation_paths: list[Path]) -> dict[str, Any]:
    if root.is_symlink():
        raise ValueError("terminal_campaign_root_invalid")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("terminal_campaign_root_invalid")
    target = root / RECEIPT_NAME
    if target.exists():
        return verify_receipt(root, target)
    facts = _campaign_facts(root)
    if (root / "invocation-in-progress.json").exists():
        raise ValueError("terminal_campaign_invocation_still_active")
    rows, totals = _inventory(root)
    stop = facts["stop"]
    tolerance = CLOCK_TOLERANCE_NS
    if totals["max_mtime_ns"] > stop["time_ns"] + tolerance:
        raise ValueError("terminal_campaign_artifact_written_after_stop")
    inventory_hash = _inventory_digest(rows)
    observations = _load_observations(
        observation_paths, root, stop, _sha((root / "STOP").read_bytes()), inventory_hash
    )
    if totals["max_mtime_ns"] > stop["time_ns"] + tolerance:
        raise ValueError("terminal_campaign_artifact_changed_after_stop")
    now_ns = time.time_ns()
    disk = shutil.disk_usage(root)
    receipt = {
        "schema_version": 1,
        "qualifier_id": QUALIFIER_ID,
        "campaign_id": CAMPAIGN_ID,
        "root": str(root),
        "status": "normal_finite_terminal",
        "attempt_count": campaign4.MAX_ATTEMPTS,
        "active_reservation_status": "reconciled",
        "accounting_error_count": 0,
        "stop_sha256": _sha((root / "STOP").read_bytes()),
        "post_stop_rpc_requests": 0,
        "post_stop_scope": POLICY["post_stop_claim_scope"],
        "observation_window": {
            "from_ns": stop["time_ns"],
            "through_ns": observations[-1]["observed_at_ns"],
            "minimum_duration_ns": OBSERVATION_WINDOW_NS,
            "snapshots": [{"path": row["path"], "sha256": row["sha256"]} for row in observations],
            "external_evidence": True,
            "limitation": "does not establish provider-side or host-wide network silence",
        },
        "policy_sha256": _sha(canonical(POLICY).encode()),
        "qualifier_sha256": _sha(Path(__file__).read_bytes()),
        "excluded_runtime_files": sorted(EXCLUDED_RUNTIME_FILES),
        "artifact_inventory_sha256": inventory_hash,
        "artifact_index": rows,
        "physical_storage": {
            "observed_at_ns": now_ns,
            "logical_bytes": totals["logical_bytes"],
            "allocated_bytes": totals["allocated_bytes"],
            "free_bytes": disk.free,
            "measurement": (
                "st_size and st_blocks*512 over indexed acquisition artifacts; shutil.disk_usage"
            ),
        },
        "campaign_facts": facts,
    }
    _write_create_only(target, receipt)
    return verify_receipt(root, target)


def collect_observation(root: Path, output_dir: Path) -> Path:
    """Record a read-only local post-STOP snapshot outside the frozen campaign root."""
    return _collect_snapshot(root, output_dir)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=campaign4.ROOT)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--observation", type=Path, action="append", default=[])
    parser.add_argument("--collect-observation", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if args.collect_observation:
        if args.output_dir is None:
            parser.error("--output-dir is required with --collect-observation")
        observation = collect_observation(args.root, args.output_dir)
        print(canonical({"status": "observation_captured", "path": str(observation)}))
        return 0
    result = verify_receipt(args.root) if args.verify else qualify(args.root, args.observation)
    print(
        canonical(
            {
                "status": result["status"],
                "receipt_sha256": _sha((canonical(result) + "\n").encode()),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
