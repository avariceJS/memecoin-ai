"""Create-only qualification for c4's specific abnormal disk STOP.

This is deliberately separate from the normal 24-attempt terminal verifier. It is an offline
evidence writer and grants no successor or acquisition authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from tools import brain_senses_c4_terminal_qualifier as normal  # noqa: E402

CONTRACT = "brain_senses_c4_abnormal_disk_terminal_v1"
STATUS = "abnormal_disk_terminal_proven"
STOP_REASON = "minimum_free_disk_floor_plus_frame_envelope"
EXPECTED_ATTEMPTS = 11
EXPECTED_ROOT = normal.REPO_ROOT / "work/brain-senses-live-helius-inventory-v3-20261010-4"
EXPECTED_STOP_SHA256 = "a7ac1fdd02615491fbea494d35f5bbcba8b03d5bf2a4d11f2516616b39f00098"
EARLY_TERMINAL_MARGIN_NS = 71 * 60 * 1_000_000_000
EXPECTED_BUCKETS = tuple(f"20261010T{hour:02}0000Z" for hour in range(13, 24))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _private_rpc_secret() -> bytes:
    config = normal.REPO_ROOT / ".env.local"
    if config.is_symlink() or not config.is_file() or config.stat().st_mode & 0o777 != 0o600:
        raise ValueError("abnormal_private_config_not_private")
    for line in config.read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == "MEMECOIN_HELIUS_RPC_URL":
            secret = value.strip().strip("\"'")
            if secret:
                return secret.encode()
    raise ValueError("abnormal_private_rpc_credential_missing")


def _read(path: Path) -> dict[str, Any]:
    size, _mtime_ns, digest, _allocated = normal._stable_hash(path)
    payload = path.read_bytes()
    if len(payload) != size or _sha(payload) != digest:
        raise ValueError("abnormal_metadata_changed_while_reading")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("abnormal_metadata_not_object")
    return value


def _hourly_buckets_are_consecutive(buckets: list[str]) -> bool:
    if not buckets:
        return False
    try:
        instants = [datetime.strptime(row, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) for row in buckets]
    except (TypeError, ValueError):
        return False
    return all(
        later - earlier == timedelta(hours=1)
        for earlier, later in zip(instants, instants[1:], strict=False)
    )


def _uncovered_continuation(frontier: int, reason: str) -> dict[str, Any]:
    if type(frontier) is not int or not 0 <= frontier < 2**64 or reason != STOP_REASON:
        raise ValueError("abnormal_continuation_input_invalid")
    return {
        "start_frontier_slot": frontier,
        "end_exclusive": None,
        "end_known": False,
        "cause": "abnormal_disk_stop_without_successor_anchor",
        "cause_detail": reason,
        "closure_rule": "only a future finalized anchor-only bootstrap may establish end_exclusive",
    }


def _all_in_totals(
    frame_raw_bytes: list[int],
    frame_stored_bytes: list[int],
    frame_requests: int,
    bootstrap_raw_bytes: int,
    bootstrap_stored_bytes: int,
    bootstrap_requests: int,
) -> dict[str, int]:
    values = [
        *frame_raw_bytes,
        *frame_stored_bytes,
        frame_requests,
        bootstrap_raw_bytes,
        bootstrap_stored_bytes,
        bootstrap_requests,
    ]
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("abnormal_accounting_dimension_invalid")
    if len(frame_raw_bytes) != len(frame_stored_bytes):
        raise ValueError("abnormal_frame_byte_vector_length_mismatch")
    raw_frame = sum(frame_raw_bytes)
    stored_frame = sum(frame_stored_bytes)
    return {
        "frame_requests": frame_requests,
        "bootstrap_requests": bootstrap_requests,
        "all_in_requests": frame_requests + bootstrap_requests,
        "frame_raw_bytes": raw_frame,
        "bootstrap_raw_bytes": bootstrap_raw_bytes,
        "all_in_raw_bytes": raw_frame + bootstrap_raw_bytes,
        "frame_stored_bytes": stored_frame,
        "bootstrap_stored_bytes": bootstrap_stored_bytes,
        "all_in_stored_bytes": stored_frame + bootstrap_stored_bytes,
    }


def _metadata_journal(
    epoch: Path, plan: dict[str, Any], selection: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Validate durable request metadata without opening any response capture."""
    request_files = sorted((epoch / "requests").glob("*.json"))
    result_files = sorted((epoch / "request-results").glob("*.json"))
    if [path.name for path in request_files] != [path.name for path in result_files]:
        raise ValueError("abnormal_request_result_set_mismatch")
    operations = normal.epoch_v2._operations_for(epoch, plan, selection)
    if len(request_files) != len(operations):
        raise ValueError("abnormal_operation_count_mismatch")
    rows = []
    for sequence, (request_path, result_path, operation) in enumerate(
        zip(request_files, result_files, operations, strict=True), start=1
    ):
        reservation = _read(request_path)
        result = _read(result_path)
        expected_body = normal.epoch_v2._request_body(operation["method"], operation["params"])
        capture_path = result.get("capture_path")
        response_digest = result.get("response_sha256")
        if (
            request_path.name != f"{sequence:012d}.json"
            or result_path.name != f"{sequence:012d}.json"
            or reservation.get("sequence") != sequence
            or reservation.get("phase") != operation["phase"]
            or reservation.get("provider") != plan["provider"]
            or reservation.get("method") != operation["method"]
            or reservation.get("params") != operation["params"]
            or reservation.get("request_sha256") != _sha(expected_body)
            or reservation.get("advance_to") != operation.get("advance_to")
            or not normal.epoch_v2._result_identity(result, operation, sequence)
            or result.get("status") != "captured"
            or result.get("result_status") != "parsed"
            or capture_path != f"captures/{sequence:012d}"
            or type(result.get("response_bytes")) is not int
            or result["response_bytes"] < 0
            or type(result.get("stored_response_bytes")) is not int
            or result["stored_response_bytes"] < 0
            or not isinstance(response_digest, str)
            or len(response_digest) != 64
            or any(char not in "0123456789abcdef" for char in response_digest)
        ):
            raise ValueError("abnormal_journal_metadata_mismatch")
        times = [result.get(key) for key in ("request_started_ns", "receipt_ns", "availability_ns")]
        if any(type(value) is not int for value in times) or not times[0] <= times[1] <= times[2]:
            raise ValueError("abnormal_request_time_metadata_invalid")
        capture_dir = epoch / capture_path
        if capture_dir.is_symlink() or not capture_dir.is_dir():
            raise ValueError("abnormal_capture_directory_invalid")
        capture_receipt_path = capture_dir / "receipt.json"
        capture_receipt = _read(capture_receipt_path)
        stored_name = capture_receipt.get("storage", {}).get("path")
        if stored_name != "response.json.zst":
            raise ValueError("abnormal_capture_storage_receipt_invalid")
        stored_path = capture_dir / stored_name
        stored_stat = stored_path.lstat()
        if (
            stored_path.is_symlink()
            or not stored_path.is_file()
            or capture_receipt.get("method") != operation["method"]
            or capture_receipt.get("request_json") != expected_body.decode()
            or capture_receipt.get("source_alias") != plan["provider"]
            or capture_receipt.get("request_started_ns") != result.get("request_started_ns")
            or capture_receipt.get("received_ns") != result.get("receipt_ns")
            or capture_receipt.get("response_sha256") != result.get("response_sha256")
            or capture_receipt.get("response_bytes") != result.get("response_bytes")
            or capture_receipt.get("storage", {}).get("bytes")
            != result.get("stored_response_bytes")
            or stored_stat.st_size != result.get("stored_response_bytes")
        ):
            raise ValueError("abnormal_capture_receipt_reconciliation_failed")
        rows.append(
            {
                **result,
                "_request_record_sha256": _sha(request_path.read_bytes()),
                "_result_record_sha256": _sha(result_path.read_bytes()),
                "_capture_receipt_sha256": _sha(capture_receipt_path.read_bytes()),
            }
        )
    return rows


def _validate_metadata_epoch(
    root: Path, bucket: str, expected_start: int
) -> tuple[dict[str, Any], list[dict[str, Any]], int, int]:
    """Reconcile immutable plans, summaries, journals, and recorded capture digests only."""
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
        or plan.get("schedule_id") != normal.CAMPAIGN_ID
        or plan.get("provider") != "helius-free"
        or plan.get("bootstrap") is not False
        or plan.get("start") != expected_start
        or plan.get("request_budget") != normal.campaign4.REQUESTS_PER_FRAME
        or plan.get("raw_byte_budget") != normal.schedule_v3.FRAME_RAW_BYTES
        or plan.get("stored_byte_budget") != normal.schedule_v3.FRAME_STORED_BYTES
        or plan.get("frame_width_cap") != normal.schedule_v3.FRAME_WIDTH_CAP
        or plan.get("selection_count") != normal.schedule_v3.SELECTION_COUNT
        or plan.get("capture_storage_codec") != "zstd"
        or plan.get("code_sha256") != _sha(Path(normal.epoch_v2.__file__).read_bytes())
        or frame.get("status") != "captured"
        or frame.get("start") != expected_start
        or type(frame.get("end_exclusive")) is not int
        or frame["end_exclusive"] <= expected_start
        or frame.get("width") != frame["end_exclusive"] - expected_start
        or frame.get("width", 0) > normal.schedule_v3.FRAME_WIDTH_CAP
        or selection.get("epoch_id") != bucket
        or len(selection.get("selected_slots", [])) != normal.schedule_v3.SELECTION_COUNT
        or selection.get("selected_slots") != summary.get("selected_slots")
        or any(type(slot) is not int for slot in selection.get("selected_slots", []))
        or len(set(selection.get("selected_slots", []))) != len(selection.get("selected_slots", []))
        or summary.get("status") != "frame_complete"
        or summary.get("selected_slots") != selection.get("selected_slots")
        or summary.get("request_reservations") != normal.campaign4.REQUESTS_PER_FRAME
        or summary.get("start") != expected_start
        or summary.get("end_exclusive") != frame.get("end_exclusive")
        or summary.get("stop_sha256") != _sha((epoch / "STOP").read_bytes())
        or epoch_stop.get("status") != "frame_complete"
        or reservation.get("bucket") != bucket
        or reservation.get("request_count") != normal.campaign4.REQUESTS_PER_FRAME
        or reservation.get("plan_sha256") != _sha((epoch / "epoch-plan.json").read_bytes())
        or release.get("bucket") != bucket
        or release.get("reservation_sha256")
        != _sha((epoch / "frame-reservation.json").read_bytes())
        or release.get("summary_sha256") != _sha((epoch / "epoch-summary.json").read_bytes())
    ):
        raise ValueError("abnormal_frame_metadata_contract_mismatch")
    rows = _metadata_journal(epoch, plan, selection)
    raw_bytes = sum(row["response_bytes"] for row in rows)
    stored_bytes = sum(row["stored_response_bytes"] for row in rows)
    structural = summary.get("structural_checks")
    if (
        len(rows) != normal.campaign4.REQUESTS_PER_FRAME
        or summary.get("captured_response_bytes") != raw_bytes
        or summary.get("stored_response_bytes") != stored_bytes
        or release.get("actual_requests") != len(rows)
        or release.get("actual_raw_bytes") != raw_bytes
        or release.get("actual_stored_bytes") != stored_bytes
        or type(release.get("released_ns")) is not int
        or release["released_ns"] < summary.get("sealed_ns", 0)
        or not isinstance(structural, list)
        or len(structural) != normal.schedule_v3.SELECTION_COUNT
        or any(item.get("status") != "qualified_structural_reference_count" for item in structural)
        or [item.get("slot") for item in structural] != selection.get("selected_slots")
    ):
        raise ValueError("abnormal_frame_metadata_accounting_mismatch")
    return {"bucket": bucket, "frame": frame, "summary": summary}, rows, raw_bytes, stored_bytes


def _validate_metadata_bootstrap(root: Path) -> tuple[dict[str, Any], int, int]:
    lease = _read(root / "lease-v1.json")
    receipt = _read(root / "bootstrap-receipt.json")
    bucket = lease["bootstrap"]["bucket"]
    epoch = root / "bootstrap" / bucket
    plan = _read(epoch / "epoch-plan.json")
    summary = _read(epoch / "epoch-summary.json")
    epoch_stop = _read(epoch / "STOP")
    reservation = _read(root / "bootstrap-reservation.json")
    if (
        plan.get("bootstrap") is not True
        or plan.get("bucket") != bucket
        or plan.get("schedule_id") != normal.CAMPAIGN_ID
        or plan.get("provider") != "helius-free"
        or reservation.get("status") != "reserved_before_io"
        or reservation.get("request_limit") != 1
        or receipt.get("campaign_id") != normal.CAMPAIGN_ID
        or receipt.get("bucket") != bucket
        or receipt.get("method") != "getSlot(finalized)"
        or summary.get("status") != "bootstrap_complete"
        or epoch_stop.get("status") != "bootstrap_complete"
        or receipt.get("summary_sha256") != _sha((epoch / "epoch-summary.json").read_bytes())
        or receipt.get("plan_sha256") != _sha((epoch / "epoch-plan.json").read_bytes())
        or lease["bootstrap"].get("receipt_sha256")
        != _sha((root / "bootstrap-receipt.json").read_bytes())
    ):
        raise ValueError("abnormal_bootstrap_metadata_contract_mismatch")
    rows = _metadata_journal(epoch, plan, None)
    if len(rows) != 1 or rows[0].get("slot_value") != receipt.get("observed_anchor_slot"):
        raise ValueError("abnormal_bootstrap_metadata_accounting_mismatch")
    raw_bytes = rows[0]["response_bytes"]
    stored_bytes = rows[0]["stored_response_bytes"]
    if raw_bytes != summary.get("captured_response_bytes") or stored_bytes != summary.get(
        "stored_response_bytes"
    ):
        raise ValueError("abnormal_bootstrap_metadata_byte_mismatch")
    lineage = _read(root / "predecessor-lineage.json")
    if (
        lineage.get("bootstrap_receipt_sha256") != lease["bootstrap"].get("receipt_sha256")
        or summary.get("request_reservations") != 1
        or summary.get("bootstrap_slot") != receipt.get("observed_anchor_slot")
        or lineage.get("last_observed_anchor_slot") != receipt.get("observed_anchor_slot")
    ):
        raise ValueError("abnormal_bootstrap_lineage_mismatch")
    journal_binding = {
        "reservation_sha256": _sha((epoch / "requests/000000000001.json").read_bytes()),
        "result_sha256": _sha((epoch / "request-results/000000000001.json").read_bytes()),
        "capture_receipt_sha256": rows[0]["_capture_receipt_sha256"],
        "response_sha256": rows[0]["response_sha256"],
    }
    return (
        {
            "receipt": receipt,
            "lineage": lineage,
            "journal_binding": journal_binding,
        },
        raw_bytes,
        stored_bytes,
    )


def _metadata_inventory(root: Path) -> dict[str, Any]:
    """Bind current campaign paths and metadata without reading opaque response bodies."""
    rows = []
    total_bytes = 0
    max_mtime_ns = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in list(dirs):
            if (current_path / name).is_symlink():
                raise ValueError("abnormal_campaign_symlink_directory")
        for name in files:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if relative in normal.EXCLUDED_RUNTIME_FILES:
                continue
            stat_result = path.lstat()
            if not path.is_file() or path.is_symlink():
                raise ValueError("abnormal_campaign_nonregular_file")
            row = {
                "path": relative,
                "bytes": stat_result.st_size,
                "mtime_ns": stat_result.st_mtime_ns,
            }
            if path.name in {"response.json.zst", "response.json"} and "captures" in path.parts:
                row["opaque_capture_body"] = True
            else:
                size, mtime_ns, digest, _allocated = normal._stable_hash(path)
                if size != stat_result.st_size or mtime_ns != stat_result.st_mtime_ns:
                    raise ValueError("abnormal_metadata_inventory_file_changed")
                row["sha256"] = digest
            rows.append(row)
            total_bytes += stat_result.st_size
            max_mtime_ns = max(max_mtime_ns, stat_result.st_mtime_ns)
    rows.sort(key=lambda row: row["path"])
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return {
        "sha256": _sha(encoded),
        "file_count": len(rows),
        "logical_bytes": total_bytes,
        "max_mtime_ns": max_mtime_ns,
    }


def _derive(root: Path, artifact_inventory_sha256: str) -> dict[str, Any]:
    if root.is_symlink():
        raise ValueError("abnormal_campaign_root_invalid")
    root = root.resolve(strict=True)
    if not root.is_dir() or root != EXPECTED_ROOT.resolve(strict=True):
        raise ValueError("abnormal_campaign_root_invalid")
    current_metadata_inventory = _metadata_inventory(root)

    stop_path = root / "STOP"
    stop_bytes = stop_path.read_bytes()
    stop = json.loads(stop_bytes)
    if (
        stop.get("campaign_id") != normal.CAMPAIGN_ID
        or _sha(stop_bytes) != EXPECTED_STOP_SHA256
        or stop.get("reason") != STOP_REASON
        or stop.get("attempt_count") != EXPECTED_ATTEMPTS
        or stop.get("active_reservation_status") != "reconciled"
        or stop.get("accounting_error_class") is not None
        or type(stop.get("time_ns")) is not int
        or current_metadata_inventory["max_mtime_ns"] > stop["time_ns"] + normal.CLOCK_TOLERANCE_NS
    ):
        raise ValueError("abnormal_stop_contract_mismatch")

    status = normal.campaign4.read_status(root)
    active = _read(root / "active-frame-reservation.json")
    if (
        status.get("attempts") != EXPECTED_ATTEMPTS
        or status.get("successful_epochs") != EXPECTED_ATTEMPTS
        or status.get("failed_epochs") != 0
        or status.get("gapped_epochs") != 0
        or status.get("active_frame_status") != "reconciled"
        or status.get("stop_reason") != STOP_REASON
        or status.get("requests") != EXPECTED_ATTEMPTS * normal.campaign4.REQUESTS_PER_FRAME
        or status.get("all_in_requests") != status.get("requests") + 1
        or active.get("status") != "reconciled"
        or active.get("bucket") != stop.get("last_attempt_bucket")
        or active.get("actual_requests") != normal.campaign4.REQUESTS_PER_FRAME
    ):
        raise ValueError("abnormal_campaign_status_incomplete")

    bootstrap, bootstrap_raw, bootstrap_stored = _validate_metadata_bootstrap(root)
    lineage = bootstrap["lineage"]
    frontier = _read(root / "frontier-v3.json")
    buckets = sorted(
        path.name
        for path in (root / "epochs").iterdir()
        if path.is_dir() and len(path.name) == 16 and path.name.endswith("Z")
    )
    if (
        tuple(buckets) != EXPECTED_BUCKETS
        or not _hourly_buckets_are_consecutive(buckets)
        or buckets[-1] != "20261010T230000Z"
        or buckets[-1] != stop.get("active_bucket")
        or stop.get("last_attempt_bucket") != buckets[-1]
        or frontier.get("gap_intervals") != lineage.get("gap_intervals")
    ):
        raise ValueError("abnormal_frame_bucket_or_gap_lineage_mismatch")

    committed_start = int(lineage["last_observed_anchor_slot"]) + 1
    frame_rows: list[dict[str, Any]] = []
    frame_request_rows: list[dict[str, Any]] = []
    last_frame_anchor: dict[str, Any] | None = None
    frame_raw = frame_stored = 0
    for bucket in buckets:
        summary, request_rows, raw_bytes, stored_bytes = _validate_metadata_epoch(
            root, bucket, committed_start
        )
        committed_start = summary["frame"]["end_exclusive"]
        frame_raw += raw_bytes
        frame_stored += stored_bytes
        frame_request_rows.extend(request_rows)
        last_frame_anchor = next(row for row in request_rows if row.get("phase") == "anchor")
        frame_path = root / "epochs" / bucket
        frame_rows.append(
            {
                "bucket": bucket,
                "plan_sha256": _sha((frame_path / "epoch-plan.json").read_bytes()),
                "summary_sha256": _sha((frame_path / "epoch-summary.json").read_bytes()),
                "frame_stop_sha256": _sha((frame_path / "STOP").read_bytes()),
                "reservation_sha256": _sha((frame_path / "frame-reservation.json").read_bytes()),
                "release_sha256": _sha(
                    (frame_path / "frame-reservation-release.json").read_bytes()
                ),
                "requests": len(request_rows),
                "raw_bytes": raw_bytes,
                "stored_bytes": stored_bytes,
                "end_exclusive": committed_start,
                "request_result_binding_sha256": _sha(
                    json.dumps(
                        [
                            {
                                "sequence": row["sequence"],
                                "request_record_sha256": row["_request_record_sha256"],
                                "result_record_sha256": row["_result_record_sha256"],
                                "capture_receipt_sha256": row["_capture_receipt_sha256"],
                                "response_sha256": row["response_sha256"],
                                "response_bytes": row["response_bytes"],
                                "stored_response_bytes": row["stored_response_bytes"],
                            }
                            for row in request_rows
                        ],
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ),
            }
        )

    if committed_start != frontier.get("observed_frontier"):
        raise ValueError("abnormal_last_committed_frontier_mismatch")

    if (
        sum(row["requests"] for row in frame_rows) != status["requests"]
        or stop.get("requests") != status["requests"]
        or stop.get("raw_bytes") != frame_raw
        or stop.get("stored_bytes") != frame_stored
        or stop.get("observed_anchor_slot") != frontier.get("observed_anchor_slot")
        or stop.get("observed_anchor_receipt_ns") != frontier.get("observed_anchor_receipt_ns")
        or stop.get("observed_frontier") != frontier.get("observed_frontier")
        or stop.get("last_inventoried_interval") != frontier.get("last_inventoried_interval")
    ):
        raise ValueError("abnormal_stop_accounting_or_frontier_mismatch")

    if last_frame_anchor is None:
        raise ValueError("abnormal_last_frame_anchor_missing")
    frame_anchor = last_frame_anchor
    anchor_times = {
        "request_started_ns": frame_anchor.get("request_started_ns"),
        "receipt_ns": frame_anchor.get("receipt_ns"),
        "availability_ns": frame_anchor.get("availability_ns"),
    }
    if (
        any(type(value) is not int for value in anchor_times.values())
        or not anchor_times["request_started_ns"]
        <= anchor_times["receipt_ns"]
        <= anchor_times["availability_ns"]
        <= stop["time_ns"]
        or frame_anchor.get("slot_value") != frontier.get("observed_anchor_slot")
        or frontier.get("observed_anchor_receipt_ns") != anchor_times["receipt_ns"]
    ):
        raise ValueError("abnormal_last_anchor_time_binding_mismatch")

    bootstrap_requests = bootstrap["receipt"].get("usage", {}).get("requests")
    bootstrap_plan = root / "bootstrap" / bootstrap["receipt"]["bucket"] / "epoch-plan.json"
    bootstrap_summary = root / "bootstrap" / bootstrap["receipt"]["bucket"] / "epoch-summary.json"
    totals = _all_in_totals(
        [row["raw_bytes"] for row in frame_rows],
        [row["stored_bytes"] for row in frame_rows],
        status["requests"],
        bootstrap_raw,
        bootstrap_stored,
        bootstrap_requests,
    )
    all_requests = totals["all_in_requests"]
    all_raw = totals["all_in_raw_bytes"]
    all_stored = totals["all_in_stored_bytes"]
    policy = normal.campaign4._policy(root)
    if (
        bootstrap_requests != 1
        or all_requests != 375
        or all_raw != 769_657_413
        or all_stored != 177_576_506
        or all_requests > policy["bounds"]["total_requests"]
        or all_raw > policy["bounds"]["raw_bytes"]
        or all_stored > policy["bounds"]["stored_bytes"]
    ):
        raise ValueError("abnormal_all_in_accounting_mismatch")

    normal._assert_zero_request_suffix(root, stop["time_ns"])
    return {
        "campaign_id": normal.CAMPAIGN_ID,
        "campaign_root": str(root),
        "launchagent_label": normal.LABEL,
        "stop": {
            "sha256": _sha(stop_bytes),
            "time_ns": stop["time_ns"],
            "reason": stop["reason"],
            "attempt_count": stop["attempt_count"],
            "active_reservation_status": stop["active_reservation_status"],
            "last_attempt_bucket": stop["last_attempt_bucket"],
        },
        "campaign_manifest": {
            "schedule_sha256": _sha((root / "schedule-v3.json").read_bytes()),
            "lease_sha256": _sha((root / "lease-v1.json").read_bytes()),
            "predecessor_lineage_sha256": _sha((root / "predecessor-lineage.json").read_bytes()),
            "source_code_sha256": _read(root / "schedule-v3.json").get("code_sha256"),
        },
        "bootstrap": {
            "bucket": bootstrap["receipt"]["bucket"],
            "plan_sha256": _sha(bootstrap_plan.read_bytes()),
            "summary_sha256": _sha(bootstrap_summary.read_bytes()),
            "receipt_sha256": _sha((root / "bootstrap-receipt.json").read_bytes()),
            "journal_binding": bootstrap["journal_binding"],
            "request_count": bootstrap_requests,
            "raw_bytes": bootstrap_raw,
            "stored_bytes": bootstrap_stored,
            "anchor_slot": bootstrap["receipt"]["observed_anchor_slot"],
            "receipt_ns": bootstrap["receipt"]["receipt_ns"],
            "availability_ns": bootstrap["receipt"]["availability_ns"],
        },
        "frames": frame_rows,
        "requests": {
            "frame_requests": status["requests"],
            "bootstrap_requests": bootstrap_requests,
            "all_in_requests": all_requests,
            "provider_charge_units": normal.PROVIDER_CHARGE_UNITS,
        },
        "bytes_decimal": {
            "frame_raw_bytes": totals["frame_raw_bytes"],
            "bootstrap_raw_bytes": totals["bootstrap_raw_bytes"],
            "all_in_raw_bytes": totals["all_in_raw_bytes"],
            "frame_stored_bytes": totals["frame_stored_bytes"],
            "bootstrap_stored_bytes": totals["bootstrap_stored_bytes"],
            "all_in_stored_bytes": totals["all_in_stored_bytes"],
        },
        "last_committed": {
            "frontier_slot": frontier["observed_frontier"],
            "anchor_slot": frontier["observed_anchor_slot"],
            "anchor_request_times": anchor_times,
            "last_inventoried_interval": frontier["last_inventoried_interval"],
            "preexisting_gaps": frontier["gap_intervals"],
        },
        "uncovered_continuation": _uncovered_continuation(
            frontier["observed_frontier"], stop["reason"]
        ),
        "zero_post_stop_io": {
            "request_journal_check": "passed",
            "open_or_ambiguous_reservations": 0,
            "frame_at_00Z_exists": False,
            "post_stop_rpc_requests": 0,
            "scope": "c4 request journals and captured local process/launchd/Unified Log evidence",
        },
        "artifact_inventory": {
            "sha256": artifact_inventory_sha256,
            "basis": "preexisting external snapshot; body bytes are not re-read",
            "current_metadata_only": current_metadata_inventory,
        },
        "metadata_only_qualification": {
            "status": "terminal control/accounting/provenance verified; raw response bodies "
            "and structural census not independently replayed in this qualification",
            "response_body_validation": "not_replayed; existing immutable response_sha256 "
            "and byte counts are bound from request-result metadata",
            "structural_validation": "existing sealed structural summary hashes are bound; "
            "not independently replayed",
        },
    }


def _observation_binding(
    paths: list[Path], root: Path, derived: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = _load_abnormal_observations(paths, root, derived)
    if rows[-1]["observed_at_ns"] < derived["stop"]["time_ns"] + EARLY_TERMINAL_MARGIN_NS:
        raise ValueError("abnormal_terminal_log_window_incomplete")
    return [
        {
            "path": row["path"],
            "sha256": row["sha256"],
            "observed_at_ns": row["observed_at_ns"],
            "launchagent_loaded": row["launchagent_loaded"],
            "senses_launchagent_labels": row.get("senses_launchagent_labels", []),
            "c4_process_pids": row["c4_process_pids"],
            "senses_writer_pids": row["senses_writer_pids"],
            "sensitive_value_detected": row["sensitive_value_detected"],
            "process_census_sha256": row["process_census_sha256"],
            "launchctl_output_sha256": row["launchctl_output_sha256"],
            "snapshot_contract": row["snapshot_contract"],
        }
        for row in rows
    ]


def _query_campaign_logs(start_ns: int, end_ns: int) -> tuple[bytes, list[str], int]:
    predicate = (
        'processImagePath CONTAINS[c] "brain_senses_campaign4_recovery.py" '
        f'or eventMessage CONTAINS[c] "{normal.LABEL}"'
    )
    result = subprocess.run(
        [
            "/usr/bin/log",
            "show",
            "--style",
            "json",
            "--start",
            _local_log_bound(start_ns)[0],
            "--end",
            _local_log_bound(end_ns)[0],
            "--predicate",
            predicate,
        ],
        cwd=normal.REPO_ROOT,
        check=False,
        capture_output=True,
        timeout=90,
    )
    if result.returncode != 0:
        raise ValueError("abnormal_terminal_unified_log_probe_failed")
    try:
        events = json.loads(result.stdout or b"[]")
    except json.JSONDecodeError as exc:
        raise ValueError("abnormal_terminal_unified_log_output_invalid") from exc
    if isinstance(events, dict):
        events = events.get("events", [])
    if not isinstance(events, list):
        raise ValueError("abnormal_terminal_unified_log_output_invalid")
    noncollector = []
    for event in events:
        process = event.get("processImagePath")
        subsystem = event.get("subsystem", "")
        message = event.get("eventMessage", "")
        if (
            not isinstance(process, str)
            or not isinstance(subsystem, str)
            or not isinstance(message, str)
        ):
            raise ValueError("abnormal_terminal_unified_log_event_invalid")
        if "brain_senses_campaign4_recovery.py" in process:
            raise ValueError("abnormal_terminal_campaign_log_activity_found")
        is_os_event = (
            (process == "/usr/libexec/UserEventAgent" and subsystem == "com.apple.xpc.activity")
            or (process == "/sbin/launchd" and subsystem.startswith("gui/"))
            or (
                process.endswith("/backgroundtaskmanagementd")
                and subsystem == "com.apple.backgroundtaskmanagement"
            )
        )
        if not is_os_event or (normal.LABEL in message and not is_os_event):
            raise ValueError("abnormal_terminal_unclassified_log_process")
        noncollector.append(process)
    return result.stdout, noncollector, len(events)


def _local_log_bound(ns: int) -> tuple[str, int, str, int]:
    instant = datetime.fromtimestamp(ns / 1_000_000_000, UTC).astimezone()
    offset = instant.utcoffset()
    if instant.tzname() != "MSK" or offset != timedelta(hours=3):
        raise ValueError("abnormal_terminal_host_timezone_unexpected")
    rounded = instant.replace(microsecond=0)
    return (
        rounded.strftime("%Y-%m-%d %H:%M:%S"),
        int(rounded.timestamp()) * 1_000_000_000,
        "Europe/Moscow",
        10_800,
    )


def _summarize_log_events(log_bytes: bytes) -> list[dict[str, Any]]:
    events = json.loads(log_bytes or b"[]")
    if isinstance(events, dict):
        events = events.get("events", [])
    if not isinstance(events, list):
        raise ValueError("abnormal_terminal_unified_log_output_invalid")
    rows = []
    for event in events:
        timestamp = event.get("timestamp")
        if not isinstance(timestamp, str):
            raise ValueError("abnormal_terminal_log_event_time_missing")
        instant = datetime.fromisoformat(timestamp)
        if instant.tzinfo is None:
            raise ValueError("abnormal_terminal_log_event_timezone_missing")
        event_ns = int(instant.timestamp()) * 1_000_000_000 + instant.microsecond * 1_000
        rows.append(
            {
                "timestamp_utc_ns": event_ns,
                "process": event.get("processImagePath"),
                "category": event.get("category"),
            }
        )
    if rows != sorted(rows, key=lambda row: row["timestamp_utc_ns"]):
        raise ValueError("abnormal_terminal_log_events_not_chronological")
    return rows


def _load_abnormal_observations(
    paths: list[Path], root: Path, derived: dict[str, Any]
) -> list[dict[str, Any]]:
    if len(paths) != 2:
        raise ValueError("abnormal_terminal_observation_pair_required")
    stop_ns = derived["stop"]["time_ns"]
    rows = []
    for path in paths:
        if path.is_symlink():
            raise ValueError("abnormal_terminal_observation_symlink")
        resolved = path.resolve(strict=True)
        if resolved.is_relative_to(root.resolve()):
            raise ValueError("abnormal_terminal_observations_must_be_external")
        _size, _mtime, digest, _allocated = normal._stable_hash(resolved)
        value = _read(resolved)
        snapshot_contract = (
            "normal_snapshot_wrong_window"
            if value.get("status") == "operational_snapshot"
            else value.get("contract")
        )
        if (
            value.get("schema_version") != 1
            or value.get("status") not in {"operational_snapshot", "abnormal_operational_snapshot"}
            or value.get("campaign_id") != normal.CAMPAIGN_ID
            or value.get("stop_sha256") != derived["stop"]["sha256"]
            or (
                value.get("qualifier_sha256") != _sha(Path(normal.__file__).read_bytes())
                if snapshot_contract == "normal_snapshot_wrong_window"
                else value.get("qualifier_sha256") != _sha(Path(__file__).read_bytes())
            )
            or type(value.get("observed_at_ns")) is not int
            or value["observed_at_ns"] < stop_ns
            or value["observed_at_ns"] > time.time_ns() + normal.CLOCK_TOLERANCE_NS
            or value.get("launchagent_loaded") is not False
            or (snapshot_contract == CONTRACT and value.get("senses_launchagent_labels") != [])
            or value.get("c4_process_pids") != []
            or value.get("senses_writer_pids") != []
            or value.get("sensitive_value_detected") is not False
            or value.get("artifact_inventory_sha256") != derived["artifact_inventory"]["sha256"]
        ):
            raise ValueError("abnormal_terminal_operational_observation_invalid")
        if snapshot_contract not in {"normal_snapshot_wrong_window", CONTRACT}:
            raise ValueError("abnormal_terminal_snapshot_contract_invalid")
        if snapshot_contract == CONTRACT and (
            value.get("capture_bodies_read") is not False
            or value.get("metadata_inventory_sha256") != _metadata_inventory(root)["sha256"]
        ):
            raise ValueError("abnormal_terminal_metadata_snapshot_mismatch")
        if snapshot_contract == "normal_snapshot_wrong_window" and (
            value.get("artifact_max_mtime_ns", 2**63) > stop_ns + normal.CLOCK_TOLERANCE_NS
        ):
            raise ValueError("abnormal_initial_snapshot_artifact_mtime_invalid")
        rows.append(
            {
                "path": str(resolved),
                "sha256": digest,
                **value,
                "snapshot_contract": snapshot_contract,
            }
        )
    rows.sort(key=lambda row: row["observed_at_ns"])
    first, last = rows[0], rows[-1]
    if (
        first["snapshot_contract"] != "normal_snapshot_wrong_window"
        or last["snapshot_contract"] != CONTRACT
        or first["observed_at_ns"]
        > stop_ns + normal.FIRST_OBSERVATION_LIMIT_NS + normal.CLOCK_TOLERANCE_NS
        or last["observed_at_ns"] < stop_ns + normal.OBSERVATION_WINDOW_NS
    ):
        raise ValueError("abnormal_terminal_observation_window_incomplete")
    return rows


def collect_observation(root: Path, output_dir: Path, artifact_inventory_sha256: str) -> Path:
    """Create a metadata-only operational snapshot; never reads or hashes response bodies."""
    root = root.resolve(strict=True)
    snapshot_started_ns = time.time_ns()
    metadata_inventory = _metadata_inventory(root)
    stop_ns = _read(root / "STOP")["time_ns"]
    if metadata_inventory["max_mtime_ns"] > stop_ns + normal.CLOCK_TOLERANCE_NS:
        raise ValueError("abnormal_campaign_artifact_written_after_stop")
    launchd = subprocess.run(
        ["/bin/launchctl", "list"], capture_output=True, check=False, timeout=20
    )
    if launchd.returncode != 0:
        raise ValueError("abnormal_terminal_launchctl_probe_failed")
    launch_lines = launchd.stdout.decode(errors="replace").splitlines()
    if not launch_lines or launch_lines[0].split() != ["PID", "Status", "Label"]:
        raise ValueError("abnormal_terminal_launchctl_inventory_invalid")
    launch_rows = [line.split() for line in launch_lines[1:]]
    if any(len(fields) != 3 for fields in launch_rows):
        raise ValueError("abnormal_terminal_launchctl_inventory_invalid")
    senses_labels = sorted(
        fields[2] for fields in launch_rows if fields[2].startswith("ai.memecoin.brain.senses.")
    )
    loaded = normal.LABEL in senses_labels
    ps = subprocess.run(
        ["/bin/ps", "-axo", "pid=,command="], capture_output=True, check=True, timeout=20
    )
    secret = _private_rpc_secret()
    if secret in ps.stdout or secret in launchd.stdout:
        raise ValueError("abnormal_credential_in_process_or_launchd_output")
    writer_scripts = [
        str(path.resolve()) for path in (normal.REPO_ROOT / "tools").glob("brain_senses_*.py")
    ]
    c4_script = str((normal.REPO_ROOT / "tools/brain_senses_campaign4_recovery.py").resolve())
    c4_pids, writer_pids = [], []
    for line in ps.stdout.decode(errors="replace").splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2 or not fields[0].isdigit() or int(fields[0]) == os.getpid():
            continue
        if c4_script in fields[1]:
            c4_pids.append(int(fields[0]))
        if any(script in fields[1] for script in writer_scripts):
            writer_pids.append(int(fields[0]))
    output_dir = output_dir.resolve()
    if output_dir.is_relative_to(root):
        raise ValueError("abnormal_terminal_observation_directory_must_be_external")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = output_dir / f"observation-{time.time_ns()}.json"
    if path.exists() or path.is_symlink():
        raise ValueError("abnormal_terminal_observation_exists")
    observed_at_ns = time.time_ns()
    payload = {
        "schema_version": 1,
        "status": "abnormal_operational_snapshot",
        "contract": CONTRACT,
        "campaign_id": normal.CAMPAIGN_ID,
        "stop_sha256": _sha((root / "STOP").read_bytes()),
        "qualifier_sha256": _sha(Path(__file__).read_bytes()),
        "snapshot_started_ns": snapshot_started_ns,
        "observed_at_ns": observed_at_ns,
        "artifact_inventory_sha256": artifact_inventory_sha256,
        "metadata_inventory_sha256": metadata_inventory["sha256"],
        "metadata_file_count": metadata_inventory["file_count"],
        "metadata_logical_bytes": metadata_inventory["logical_bytes"],
        "metadata_max_mtime_ns": metadata_inventory["max_mtime_ns"],
        "launchagent_loaded": loaded,
        "senses_launchagent_labels": senses_labels,
        "launchctl_output_sha256": _sha(launchd.stdout),
        "c4_process_pids": sorted(c4_pids),
        "senses_writer_pids": sorted(writer_pids),
        "process_census_sha256": _sha(ps.stdout),
        "capture_bodies_read": False,
        "sensitive_value_detected": False,
    }
    _write_create_only(path, (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode())
    return path


def collect_log_receipt(
    root: Path, first_observation_path: Path, output_path: Path
) -> dict[str, Any]:
    """Capture corrected historical OS-log coverage without touching campaign payloads."""
    root = root.resolve(strict=True)
    first_observation_path = first_observation_path.resolve(strict=True)
    if first_observation_path.is_relative_to(root):
        raise ValueError("abnormal_terminal_observations_must_be_external")
    first = _read(first_observation_path)
    stop = _read(root / "STOP")
    stop_ns = stop["time_ns"]
    if (
        first.get("observed_at_ns", 0) > stop_ns + normal.FIRST_OBSERVATION_LIMIT_NS
        or first.get("campaign_id") != normal.CAMPAIGN_ID
        or first.get("stop_sha256") != _sha((root / "STOP").read_bytes())
    ):
        raise ValueError("abnormal_initial_observation_invalid")
    query_started_ns = time.time_ns()
    start_arg, query_start_ns, timezone_name, offset_seconds = _local_log_bound(stop_ns)
    end_arg, query_end_ns, _timezone_name, _offset_seconds = _local_log_bound(query_started_ns)
    if query_end_ns < stop_ns + EARLY_TERMINAL_MARGIN_NS:
        raise ValueError("abnormal_terminal_log_window_not_mature")
    log_bytes, noncollector, event_count = _query_campaign_logs(stop_ns, query_started_ns)
    if _private_rpc_secret() in log_bytes:
        raise ValueError("abnormal_credential_in_unified_log")
    receipt_ns = time.time_ns()
    event_rows = _summarize_log_events(log_bytes)
    output_path = output_path.absolute()
    if output_path.resolve().is_relative_to(root):
        raise ValueError("abnormal_terminal_log_receipt_must_be_external")
    payload = {
        "schema_version": 1,
        "contract": f"{CONTRACT}_unified_log_v1",
        "status": "historical_log_interval_captured",
        "campaign_id": normal.CAMPAIGN_ID,
        "stop_sha256": _sha((root / "STOP").read_bytes()),
        "qualifier_sha256": _sha(Path(__file__).read_bytes()),
        "query_started_ns": query_started_ns,
        "captured_at_ns": receipt_ns,
        "first_observation": {
            "path": str(first_observation_path),
            "sha256": _sha(first_observation_path.read_bytes()),
            "observed_at_ns": first["observed_at_ns"],
        },
        "query": {
            "command": "/usr/bin/log show --style json",
            "timezone": timezone_name,
            "utc_offset_seconds": offset_seconds,
            "start_local": start_arg,
            "end_local": end_arg,
            "covered_utc_ns": [query_start_ns, query_end_ns],
            "receipt_ns": receipt_ns,
            "exit_status": 0,
            "valid_json": True,
            "output_bytes": len(log_bytes),
            "output_sha256": _sha(log_bytes),
            "event_count": event_count,
            "noncollector_processes": sorted(noncollector),
            "events": event_rows,
        },
        "limitations": [
            "This later-received OS log query does not backdate availability to the first "
            "snapshot.",
            "It does not prove provider-side or host-wide network silence.",
            "Local Unified Log retention and filtering remain the boundary of this evidence.",
        ],
    }
    _write_create_only(output_path, (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode())
    return payload


def _validate_log_receipt(
    path: Path, root: Path, derived: dict[str, Any], observations: list[dict[str, Any]]
) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("abnormal_terminal_log_receipt_symlink")
    path = path.resolve(strict=True)
    if path.is_relative_to(root):
        raise ValueError("abnormal_terminal_log_receipt_must_be_external")
    receipt = _read(path)
    query = receipt.get("query", {})
    stop_ns = derived["stop"]["time_ns"]
    covered = query.get("covered_utc_ns")
    if not isinstance(covered, list) or len(covered) != 2:
        raise ValueError("abnormal_terminal_log_coverage_invalid")
    if (
        receipt.get("schema_version") != 1
        or receipt.get("contract") != f"{CONTRACT}_unified_log_v1"
        or receipt.get("status") != "historical_log_interval_captured"
        or receipt.get("campaign_id") != normal.CAMPAIGN_ID
        or receipt.get("stop_sha256") != derived["stop"]["sha256"]
        or receipt.get("qualifier_sha256") != _sha(Path(__file__).read_bytes())
        or receipt.get("first_observation", {}).get("sha256") != observations[0]["sha256"]
        or receipt.get("first_observation", {}).get("path") != observations[0]["path"]
        or receipt.get("first_observation", {}).get("observed_at_ns")
        != observations[0]["observed_at_ns"]
        or query.get("timezone") != "Europe/Moscow"
        or query.get("utc_offset_seconds") != 10_800
        or query.get("exit_status") != 0
        or query.get("valid_json") is not True
        or covered[0] > stop_ns
        or covered[1] < stop_ns + normal.OBSERVATION_WINDOW_NS
        or type(receipt.get("query_started_ns")) is not int
        or type(receipt.get("captured_at_ns")) is not int
        or receipt.get("query_started_ns") > receipt.get("captured_at_ns")
        or receipt.get("captured_at_ns") != query.get("receipt_ns")
        or receipt.get("query_started_ns") < stop_ns + EARLY_TERMINAL_MARGIN_NS
        or receipt.get("captured_at_ns") > time.time_ns() + normal.CLOCK_TOLERANCE_NS
        or covered
        != [
            _local_log_bound(stop_ns)[1],
            _local_log_bound(receipt.get("query_started_ns", 0))[1],
        ]
        or query.get("start_local") != _local_log_bound(stop_ns)[0]
        or query.get("end_local") != _local_log_bound(receipt.get("query_started_ns", 0))[0]
    ):
        raise ValueError("abnormal_terminal_log_receipt_invalid")
    log_bytes, noncollector, event_count = _query_campaign_logs(covered[0], covered[1])
    if (
        _sha(log_bytes) != query.get("output_sha256")
        or len(log_bytes) != query.get("output_bytes")
        or event_count != query.get("event_count")
        or sorted(noncollector) != query.get("noncollector_processes")
        or _summarize_log_events(log_bytes) != query.get("events")
    ):
        raise ValueError("abnormal_terminal_log_receipt_replay_mismatch")
    return {"path": str(path), "sha256": _sha(path.read_bytes()), "receipt": receipt}


def _write_create_only(path: Path, payload: bytes) -> None:
    if path.is_symlink() or path.exists():
        raise ValueError("abnormal_terminal_output_already_exists")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.resolve(strict=True)
    target = parent / path.name
    if target.is_symlink() or target.exists():
        raise ValueError("abnormal_terminal_output_already_exists")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def qualify(
    root: Path, observation_paths: list[Path], log_receipt_path: Path, output_path: Path
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    output_path = output_path.absolute()
    if output_path.resolve().is_relative_to(root):
        raise ValueError("abnormal_terminal_output_must_be_external")
    if not observation_paths:
        raise ValueError("abnormal_terminal_observation_pair_required")
    first_observation = _read(observation_paths[0])
    artifact_inventory_sha256 = first_observation.get("artifact_inventory_sha256")
    if not isinstance(artifact_inventory_sha256, str) or len(artifact_inventory_sha256) != 64:
        raise ValueError("abnormal_terminal_inventory_binding_missing")
    derived = _derive(root, artifact_inventory_sha256)
    observations = _observation_binding(observation_paths, root, derived)
    log_receipt = _validate_log_receipt(log_receipt_path, root, derived, observations)
    log_summary = {
        "path": log_receipt["path"],
        "sha256": log_receipt["sha256"],
        "covered_utc_ns": log_receipt["receipt"]["query"]["covered_utc_ns"],
        "captured_at_ns": log_receipt["receipt"]["captured_at_ns"],
        "event_count": log_receipt["receipt"]["query"]["event_count"],
        "noncollector_processes": log_receipt["receipt"]["query"]["noncollector_processes"],
    }
    payload = {
        "schema_version": 1,
        "contract": CONTRACT,
        "status": STATUS,
        "qualifier_sha256": _sha(Path(__file__).read_bytes()),
        "normal_terminal_verifier_sha256": _sha(Path(normal.__file__).read_bytes()),
        "evidence": derived,
        "observations": observations,
        "corrected_unified_log_receipt": log_summary,
        "limitations": [
            "This proves only c4's abnormal terminal state. It does not authorize a successor.",
            "Post-STOP zero-I/O scope is local c4 journal, process, launchd, and filtered "
            "Unified Log evidence.",
            "Provider-side and host-wide network silence are not established.",
            "The uncovered continuation has an unknown end; a future finalized anchor-only "
            "bootstrap may close it.",
            "Provider charge units and account headroom remain unknown.",
        ],
        "created_ns": time.time_ns(),
    }
    if output_path.resolve().is_relative_to(root):
        raise ValueError("abnormal_terminal_output_must_be_external")
    _write_create_only(output_path, (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode())
    return payload


def verify(receipt_path: Path, root: Path) -> dict[str, Any]:
    if receipt_path.is_symlink():
        raise ValueError("abnormal_terminal_receipt_symlink")
    receipt_path = receipt_path.resolve(strict=True)
    root = root.resolve(strict=True)
    if receipt_path.is_relative_to(root):
        raise ValueError("abnormal_terminal_receipt_must_be_external")
    receipt = _read(receipt_path)
    if (
        receipt.get("schema_version") != 1
        or receipt.get("contract") != CONTRACT
        or receipt.get("status") != STATUS
        or receipt.get("qualifier_sha256") != _sha(Path(__file__).read_bytes())
        or receipt.get("normal_terminal_verifier_sha256")
        != _sha(Path(normal.__file__).read_bytes())
        or receipt.get("evidence")
        != _derive(root, receipt.get("evidence", {}).get("artifact_inventory", {}).get("sha256"))
    ):
        raise ValueError("abnormal_terminal_receipt_evidence_mismatch")
    observations = _observation_binding(
        [Path(row["path"]) for row in receipt.get("observations", [])], root, receipt["evidence"]
    )
    if receipt.get("observations") != observations:
        raise ValueError("abnormal_terminal_observation_binding_mismatch")
    log_receipt = _validate_log_receipt(
        Path(receipt.get("corrected_unified_log_receipt", {}).get("path", "")),
        root,
        receipt["evidence"],
        observations,
    )
    log_summary = {
        "path": log_receipt["path"],
        "sha256": log_receipt["sha256"],
        "covered_utc_ns": log_receipt["receipt"]["query"]["covered_utc_ns"],
        "captured_at_ns": log_receipt["receipt"]["captured_at_ns"],
        "event_count": log_receipt["receipt"]["query"]["event_count"],
        "noncollector_processes": log_receipt["receipt"]["query"]["noncollector_processes"],
    }
    if receipt.get("corrected_unified_log_receipt") != log_summary:
        raise ValueError("abnormal_terminal_log_receipt_binding_mismatch")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--observation", type=Path, action="append", default=[])
    parser.add_argument("--log-receipt", type=Path)
    parser.add_argument("--collect-log-receipt", action="store_true")
    parser.add_argument("--first-observation", type=Path)
    parser.add_argument("--collect-observation-dir", type=Path)
    parser.add_argument("--artifact-inventory-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.collect_observation_dir:
        if not args.artifact_inventory_sha256:
            parser.error("--collect-observation-dir requires --artifact-inventory-sha256")
        path = collect_observation(
            args.root, args.collect_observation_dir, args.artifact_inventory_sha256
        )
        print(json.dumps({"observation": str(path)}, sort_keys=True))
        return 0
    if args.collect_log_receipt:
        if not args.first_observation or not args.output:
            parser.error("--collect-log-receipt requires --first-observation and --output")
        value = collect_log_receipt(args.root, args.first_observation, args.output)
        print(
            json.dumps({"status": value["status"], "contract": value["contract"]}, sort_keys=True)
        )
        return 0
    if args.verify:
        value = verify(args.verify, args.root)
    else:
        if not args.output or not args.log_receipt or len(args.observation) < 2:
            parser.error(
                "--output, --log-receipt, and at least two --observation values are required"
            )
        value = qualify(args.root, args.observation, args.log_receipt, args.output)
    print(json.dumps({"status": value["status"], "contract": value["contract"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
