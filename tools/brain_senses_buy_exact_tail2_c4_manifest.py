#!/usr/bin/env python3
"""Freeze a metadata-only source manifest for the preregistered tail2 c4 audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import brain_senses_block_epoch_v2 as epoch_v2  # noqa: E402
from tools import brain_senses_campaign4_recovery as campaign4  # noqa: E402

FREEZE_COMMIT_NS = 1_791_670_297_000_000_000  # 2026-10-10T22:11:37Z
PREREG_PATH = REPO_ROOT / "docs/evidence/pumpswap-buy-exact-tail2-c4-audit-prereg-2026-10-10.json"
EXPECTED_PREREG_SHA256 = "d7b8fc352ef2039a722febbf54465834dd8a954f850905bc3c9fdda5da903350"
BUCKET_RE = re.compile(r"^\d{8}T\d{6}Z$")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path) -> tuple[dict[str, Any], bytes]:
    metadata = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise ValueError("manifest_metadata_not_regular_file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(fd, "rb") as stream:
            raw = stream.read()
            after = os.fstat(stream.fileno())
    finally:
        current = path.lstat()
    if (
        metadata.st_dev != after.st_dev
        or metadata.st_ino != after.st_ino
        or metadata.st_size != after.st_size
        or metadata.st_mtime_ns != after.st_mtime_ns
        or current.st_dev != after.st_dev
        or current.st_ino != after.st_ino
        or current.st_size != after.st_size
        or current.st_mtime_ns != after.st_mtime_ns
        or current.st_ctime_ns != after.st_ctime_ns
        or len(raw) != after.st_size
    ):
        raise ValueError("manifest_metadata_changed_while_reading")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("manifest_json_object_required")
    return value, raw


def _hash_file(path: Path) -> dict[str, Any]:
    before = path.lstat()
    if not path.is_file() or path.is_symlink():
        raise ValueError("manifest_artifact_not_regular_file")
    h = hashlib.sha256()
    size = 0
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        with os.fdopen(fd, "rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                h.update(chunk)
            after = os.fstat(stream.fileno())
    finally:
        final = path.lstat()

    def identity(row: os.stat_result) -> tuple[int, int, int, int, int]:
        return row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns

    if (
        identity(before) != identity(after)
        or identity(after) != identity(final)
        or size != after.st_size
    ):
        raise ValueError("manifest_artifact_changed_while_hashing")
    return {"bytes": size, "mtime_ns": after.st_mtime_ns, "sha256": h.hexdigest()}


def _bucket_start(bucket: str) -> datetime:
    if not BUCKET_RE.fullmatch(bucket):
        raise ValueError("manifest_bucket_invalid")
    return datetime.strptime(bucket, "%Y%m%dT%H0000Z").replace(tzinfo=UTC)


def _candidate_status(epoch: Path, bucket: str) -> tuple[str, dict[str, Any] | None]:
    if not epoch.is_dir():
        return "missing", None
    if epoch.is_symlink() or any(
        (epoch / name).is_symlink()
        for name in (
            "requests",
            "request-results",
            "captures",
        )
        if (epoch / name).exists()
    ):
        return "ambiguous_or_partial", None
    try:
        plan, _ = _json(epoch / "epoch-plan.json")
        frame, _ = _json(epoch / "frame.json")
        summary, summary_bytes = _json(epoch / "epoch-summary.json")
        stop, _ = _json(epoch / "STOP")
        reservation, _ = _json(epoch / "frame-reservation.json")
        release, _ = _json(epoch / "frame-reservation-release.json")
        selection, _ = _json(epoch / "selection-plan.json")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return "ambiguous_or_partial", None
    complete = (
        plan.get("epoch_id") == bucket
        and plan.get("bucket") == bucket
        and plan.get("schedule_id") == campaign4.CAMPAIGN_ID
        and plan.get("provider") == "helius-free"
        and plan.get("bootstrap") is False
        and plan.get("code_sha256") == epoch_v2._sha(Path(epoch_v2.__file__))
        and frame.get("status") == "captured"
        and frame.get("width") == frame.get("end_exclusive", 0) - frame.get("start", 0)
        and summary.get("status") == "frame_complete"
        and summary.get("selected_slots") == selection.get("selected_slots")
        and summary.get("request_reservations") == campaign4.REQUESTS_PER_FRAME
        and isinstance(summary.get("structural_checks"), list)
        and len(summary["structural_checks"]) == campaign4.SELECTION_COUNT
        and all(
            isinstance(row, dict)
            and row.get("status") == "qualified_structural_reference_count"
            and row.get("block_identity_equal") is True
            and row.get("signature_order_equal") is True
            for row in summary["structural_checks"]
        )
        and stop.get("status") == "frame_complete"
        and reservation.get("bucket") == bucket
        and reservation.get("request_count") == campaign4.REQUESTS_PER_FRAME
        and release.get("bucket") == bucket
        and release.get("actual_requests") == campaign4.REQUESTS_PER_FRAME
        and release.get("summary_sha256") == _sha(summary_bytes)
    )
    return ("successful" if complete else "failed_or_partial"), summary if complete else None


def _scheduled_buckets(last_bucket: str) -> list[str]:
    first = (FREEZE_COMMIT_NS // 3_600_000_000_000 + 1) * 3_600_000_000_000
    end = int(_bucket_start(last_bucket).timestamp() * 1_000_000_000)
    if end < first:
        return []
    values = []
    cursor = first
    while cursor <= end:
        values.append(
            datetime.fromtimestamp(cursor // 1_000_000_000, UTC).strftime("%Y%m%dT%H0000Z")
        )
        cursor += 3_600_000_000_000
    return values


def _frame_binding(root: Path, bucket: str, summary: dict[str, Any]) -> dict[str, Any]:
    epoch = root / "epochs" / bucket
    artifact_hashes: dict[str, dict[str, Any]] = {}
    plan, plan_bytes = _json(epoch / "epoch-plan.json")
    frame, frame_bytes = _json(epoch / "frame.json")
    selection, selection_bytes = _json(epoch / "selection-plan.json")
    summary_value, summary_bytes = _json(epoch / "epoch-summary.json")
    stop, stop_bytes = _json(epoch / "STOP")
    reservation, reservation_bytes = _json(epoch / "frame-reservation.json")
    release, release_bytes = _json(epoch / "frame-reservation-release.json")
    artifact_hashes.update(
        {
            name: {"bytes": len(raw), "sha256": _sha(raw)}
            for name, raw in (
                ("epoch-plan.json", plan_bytes),
                ("frame.json", frame_bytes),
                ("selection-plan.json", selection_bytes),
                ("epoch-summary.json", summary_bytes),
                ("STOP", stop_bytes),
                ("frame-reservation.json", reservation_bytes),
                ("frame-reservation-release.json", release_bytes),
            )
        }
    )
    context_path = epoch / "attempt-context.json"
    context, context_bytes = _json(context_path)
    if context.get("bucket") != bucket or context.get("missed_slot_backfill") is not False:
        raise ValueError("manifest_attempt_context_mismatch")
    artifact_hashes["attempt-context.json"] = {
        "bytes": len(context_bytes),
        "sha256": _sha(context_bytes),
    }
    if summary_value != summary:
        raise ValueError("manifest_summary_changed_during_selection")
    if (
        plan.get("epoch_id") != bucket
        or plan.get("bucket") != bucket
        or plan.get("schedule_id") != campaign4.CAMPAIGN_ID
        or plan.get("provider") != "helius-free"
        or plan.get("bootstrap") is not False
        or plan.get("code_sha256") != epoch_v2._sha(Path(epoch_v2.__file__))
        or frame.get("status") != "captured"
        or frame.get("width") != frame.get("end_exclusive", 0) - frame.get("start", 0)
        or summary_value.get("status") != "frame_complete"
        or summary_value.get("selected_slots") != selection.get("selected_slots")
        or not isinstance(summary_value.get("structural_checks"), list)
        or len(summary_value["structural_checks"]) != campaign4.SELECTION_COUNT
        or any(
            not isinstance(row, dict)
            or row.get("status") != "qualified_structural_reference_count"
            or row.get("block_identity_equal") is not True
            or row.get("signature_order_equal") is not True
            for row in summary_value["structural_checks"]
        )
        or stop.get("status") != "frame_complete"
        or reservation.get("bucket") != bucket
        or reservation.get("request_count") != campaign4.REQUESTS_PER_FRAME
        or release.get("bucket") != bucket
        or release.get("actual_requests") != campaign4.REQUESTS_PER_FRAME
        or release.get("summary_sha256") != _sha(summary_bytes)
    ):
        raise ValueError("manifest_frame_terminal_binding_mismatch")
    operations = epoch_v2._operations_for(epoch, plan, selection)
    requests = sorted((epoch / "requests").glob("*.json"))
    results = sorted((epoch / "request-results").glob("*.json"))
    if len(requests) != len(operations) or len(results) != len(operations):
        raise ValueError("manifest_request_journal_incomplete")
    request_rows = []
    capture_rows = []
    result_rows = []
    raw_bytes = stored_bytes = 0
    for sequence, (request_path, result_path, operation) in enumerate(
        zip(requests, results, operations, strict=True), start=1
    ):
        request, request_bytes = _json(request_path)
        result, result_bytes = _json(result_path)
        expected_name = f"{sequence:012d}.json"
        request_body = epoch_v2._request_body(operation["method"], operation["params"])
        if (
            request_path.name != expected_name
            or result_path.name != expected_name
            or request.get("sequence") != sequence
            or request.get("phase") != operation["phase"]
            or request.get("provider") != plan["provider"]
            or request.get("method") != operation["method"]
            or request.get("params") != operation["params"]
            or request.get("request_sha256") != _sha(request_body)
            or result.get("sequence") != sequence
            or result.get("phase") != operation["phase"]
            or result.get("provider") != plan["provider"]
            or result.get("method") != operation["method"]
            or not epoch_v2._result_identity(result, operation, sequence)
            or result.get("status") != "captured"
            or result.get("result_status") != "parsed"
        ):
            raise ValueError("manifest_request_identity_mismatch")
        capture_rel = result.get("capture_path")
        capture_path = epoch / str(capture_rel)
        expected_capture = epoch / "captures" / f"{sequence:012d}"
        if (
            capture_path != expected_capture
            or capture_path.is_symlink()
            or not capture_path.is_dir()
            or not (capture_path / "receipt.json").is_file()
            or (capture_path / "receipt.json").is_symlink()
        ):
            raise ValueError("manifest_capture_path_invalid")
        capture_meta, capture_meta_bytes = _json(capture_path / "receipt.json")
        stored_path = capture_path / "response.json.zst"
        stored = _hash_file(stored_path)
        storage = capture_meta.get("storage")
        if (
            capture_meta.get("source_alias") != plan["provider"]
            or capture_meta.get("method") != operation["method"]
            or capture_meta.get("request_json") != request_body.decode()
            or capture_meta.get("request_started_ns") != result.get("request_started_ns")
            or capture_meta.get("received_ns") != result.get("receipt_ns")
            or capture_meta.get("response_sha256") != result.get("response_sha256")
            or capture_meta.get("response_bytes") != result.get("response_bytes")
            or not isinstance(storage, dict)
            or storage.get("codec") != "zstd"
            or storage.get("path") != "response.json.zst"
            or storage.get("sha256") != stored["sha256"]
            or storage.get("bytes") != stored["bytes"]
            or result.get("stored_response_bytes") != stored["bytes"]
        ):
            raise ValueError("manifest_capture_receipt_mismatch")
        raw_bytes += int(result["response_bytes"])
        stored_bytes += stored["bytes"]
        artifact_hashes[request_path.relative_to(epoch).as_posix()] = {
            "bytes": len(request_bytes),
            "sha256": _sha(request_bytes),
        }
        artifact_hashes[result_path.relative_to(epoch).as_posix()] = {
            "bytes": len(result_bytes),
            "sha256": _sha(result_bytes),
        }
        artifact_hashes[(capture_path / "receipt.json").relative_to(epoch).as_posix()] = {
            "bytes": len(capture_meta_bytes),
            "sha256": _sha(capture_meta_bytes),
        }
        artifact_hashes[stored_path.relative_to(epoch).as_posix()] = stored
        request_rows.append(
            {
                "sequence": sequence,
                "sha256": _sha(request_bytes),
                "result_sha256": _sha(result_bytes),
            }
        )
        result_rows.append({"sequence": sequence, "sha256": _sha(result_bytes)})
        capture_rows.append(
            {
                "sequence": sequence,
                "phase": operation["phase"],
                "slot": operation.get("slot"),
                "request_sha256": _sha(request_body),
                "metadata_sha256": _sha(capture_meta_bytes),
                "compressed_response": stored,
                "response_sha256": result["response_sha256"],
                "response_bytes": result["response_bytes"],
            }
        )
    inventory_rows = []
    for path in results:
        result, _result_bytes = _json(path)
        if result.get("phase") == "inventory":
            inventory_rows.append(result)
    if len(inventory_rows) != 1:
        raise ValueError("manifest_inventory_result_missing")
    inventory_result = inventory_rows[0]
    expected_selection = epoch_v2._build_selection(
        plan=plan,
        inventory_slots=inventory_result.get("inventory_slots"),
        inventory_sha256=inventory_result.get("response_sha256"),
    )
    if selection != expected_selection:
        raise ValueError("manifest_selection_recomputation_mismatch")
    if (
        raw_bytes != summary_value.get("captured_response_bytes")
        or stored_bytes != summary_value.get("stored_response_bytes")
        or release.get("actual_raw_bytes") != raw_bytes
        or release.get("actual_stored_bytes") != stored_bytes
        or release.get("reservation_sha256") != _sha(reservation_bytes)
    ):
        raise ValueError("manifest_frame_byte_accounting_mismatch")
    expected_dirs = {"captures", "requests", "request-results", "events"}
    expected_dirs.update(f"captures/{sequence:012d}" for sequence in range(1, len(operations) + 1))
    actual_files: set[str] = set()
    actual_dirs: set[str] = set()
    for current, dirs, files in os.walk(epoch, followlinks=False):
        current_path = Path(current)
        for name in dirs:
            path = current_path / name
            if path.is_symlink():
                raise ValueError("manifest_frame_artifact_symlink")
            actual_dirs.add(path.relative_to(epoch).as_posix())
        for name in files:
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("manifest_frame_artifact_not_regular")
            actual_files.add(path.relative_to(epoch).as_posix())
    if actual_dirs != expected_dirs or actual_files != set(artifact_hashes):
        raise ValueError("manifest_frame_artifact_inventory_mismatch")
    return {
        "bucket": bucket,
        "plan_sha256": _sha(plan_bytes),
        "frame_sha256": _sha(frame_bytes),
        "selection_plan_sha256": _sha(selection_bytes),
        "summary_sha256": _sha(summary_bytes),
        "stop_sha256": _sha(stop_bytes),
        "reservation_sha256": _sha(reservation_bytes),
        "release_sha256": _sha(release_bytes),
        "status": summary["status"],
        "selected_slots": selection["selected_slots"],
        "request_count": len(request_rows),
        "request_journal": request_rows,
        "result_journal": result_rows,
        "captures": capture_rows,
        "artifact_index": [
            {"path": path, **artifact_hashes[path]} for path in sorted(artifact_hashes)
        ],
        "reconciled_raw_bytes": raw_bytes,
        "reconciled_stored_bytes": stored_bytes,
    }


def _pinned_source_hashes(prereg: dict[str, Any]) -> dict[str, Any]:
    pins = prereg.get("source_pins")
    if not isinstance(pins, dict) or not pins:
        raise ValueError("manifest_preregistered_source_pins_missing")
    actual: dict[str, Any] = {}
    for name, pin in sorted(pins.items()):
        if not isinstance(pin, dict) or not isinstance(pin.get("path"), str):
            raise ValueError("manifest_preregistered_source_pin_invalid")
        path = REPO_ROOT / pin["path"]
        digest = _hash_file(path)
        if digest["sha256"] != pin.get("sha256"):
            raise ValueError("manifest_preregistered_source_hash_mismatch")
        actual[name] = {"path": pin["path"], "sha256": digest["sha256"]}
    return actual


def build_manifest(root: Path, output: Path) -> dict[str, Any]:
    if root.is_symlink():
        raise ValueError("manifest_campaign_root_symlink")
    root = root.resolve(strict=True)
    output = output.resolve()
    if root != campaign4.ROOT.resolve() or output.is_relative_to(root):
        raise ValueError("manifest_campaign_or_output_path_invalid")
    if output.exists():
        raise ValueError("manifest_output_exists_refusing_overwrite")
    prereg, prereg_bytes = _json(PREREG_PATH)
    if prereg.get("status") != "metadata_only_selection_rule_frozen_payloads_unopened":
        raise ValueError("manifest_preregistration_invalid")
    current_prereg_hash = _sha(prereg_bytes)
    if current_prereg_hash != EXPECTED_PREREG_SHA256:
        raise ValueError("manifest_preregistration_hash_mismatch")
    frontier, frontier_bytes = _json(root / "frontier-v3.json")
    frontier_sha256 = _sha(frontier_bytes)
    last_bucket = frontier.get("last_attempt_bucket")
    if not isinstance(last_bucket, str):
        raise ValueError("manifest_campaign_frontier_missing")
    scheduled = _scheduled_buckets(last_bucket)
    metadata_statuses: list[dict[str, str]] = []
    metadata_successes: list[tuple[str, dict[str, Any]]] = []
    for bucket in scheduled:
        try:
            state, summary = _candidate_status(root / "epochs" / bucket, bucket)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            state, summary = "ambiguous_or_partial", None
        metadata_statuses.append({"bucket": bucket, "state": state})
        if state == "successful" and summary is not None:
            metadata_successes.append((bucket, summary))
    if len(metadata_successes) < 4:
        raise ValueError("manifest_four_metadata_complete_frames_not_yet_available")

    # Validate every external/source pin before hashing any selected compressed response.
    lease = _hash_file(root / "lease-v1.json")
    schedule = _hash_file(root / "schedule-v3.json")
    runtime_pins = prereg.get("runtime_metadata_hashes", {})
    if lease["sha256"] != runtime_pins.get("c4_lease_sha256") or schedule[
        "sha256"
    ] != runtime_pins.get("c4_schedule_sha256"):
        raise ValueError("manifest_runtime_metadata_hash_mismatch")
    lease_value = campaign4._policy(root)
    capture_runtime_sources = campaign4._code_hashes()
    if capture_runtime_sources != lease_value.get("code_sha256"):
        raise ValueError("manifest_capture_runtime_source_hash_mismatch")
    if _hash_file(root / "frontier-v3.json")["sha256"] != frontier_sha256:
        raise ValueError("manifest_frontier_changed_during_selection")
    pinned_sources = _pinned_source_hashes(prereg)

    successful = []
    for bucket, summary in metadata_successes:
        try:
            binding = _frame_binding(root, bucket, summary)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            state = "ambiguous_or_partial"
        else:
            state = "successful"
            successful.append((bucket, binding))
        for row in metadata_statuses:
            if row["bucket"] == bucket:
                row["state"] = state
                break
        if len(successful) == 4:
            break
    if len(successful) != 4:
        raise ValueError("manifest_four_successfully_bound_frames_not_yet_available")
    fourth_bucket = successful[-1][0]
    statuses = []
    for row in metadata_statuses:
        statuses.append(row)
        if row["bucket"] == fourth_bucket:
            break
    scheduled = [row["bucket"] for row in statuses]
    frames = [binding for _bucket, binding in successful]
    payload = {
        "schema_version": 1,
        "contract": "pumpswap_buy_exact_tail2_c4_metadata_source_manifest_v1",
        "status": "four_frames_source_bound_payloads_not_decoded",
        "campaign_id": campaign4.CAMPAIGN_ID,
        "campaign_root": str(root),
        "preregistration_path": str(PREREG_PATH),
        "preregistration_sha256": current_prereg_hash,
        "generator_sha256": _sha(Path(__file__).read_bytes()),
        "runtime_metadata_sha256": {
            "lease": lease["sha256"],
            "schedule": schedule["sha256"],
            "frontier": frontier_sha256,
        },
        "capture_runtime_sources": capture_runtime_sources,
        "sources": pinned_sources,
        "scheduled_bucket_denominator": statuses,
        "selected_frames": frames,
        "counts": {
            "scheduled_buckets_in_denominator": len(statuses),
            "selected_successful_frames": len(frames),
            "frame_requests": sum(frame["request_count"] for frame in frames),
            "full_block_pairs": sum(len(frame["selected_slots"]) for frame in frames),
            "payloads_decoded_by_manifest_builder": 0,
            "rpc_requests": 0,
        },
        "scope": (
            "metadata-only selection and content-hash binding; no block payload decompression "
            "or candidate analysis"
        ),
        "limitations": [
            (
                "This manifest binds source bytes and the c4 frame's existing structural "
                "self-checks; the audit runner must independently verify full/signature payload "
                "agreement during "
                "its one pass."
            ),
            "Four selected frames do not establish global discovery or population completeness.",
        ],
        "created_ns": time.time_ns(),
    }
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
        directory_fd = os.open(output.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    return {"status": payload["status"], "path": str(output), "sha256": _sha(encoded)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=campaign4.ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build_manifest(args.root, args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(
            json.dumps({"status": "failed_closed", "error": str(exc)}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
