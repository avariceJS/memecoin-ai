#!/usr/bin/env python3
"""Independent read-only verifier for operation-envelope-v2 child bundles.

Does not import the writer, controller-v1, epoch scheduler, or verifier-v1. It
recomputes the 34-operation child contract from immutable captured evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "brain-senses-prospective-operation-envelope-v2"
ZERO = "0" * 64
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
MAX_RESPONSE_BYTES = 32_000_000


class VerificationError(ValueError):
    """Parent or child evidence failed independent verification."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(data: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for k, v in pairs:
            if k in result:
                raise VerificationError("duplicate_json_key")
            result[k] = v
        return result

    try:
        return json.loads(data, object_pairs_hook=unique)
    except VerificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError("json_invalid") from exc


def _read(path: Path, *, owner: int) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_uid != owner or before.st_nlink != 1:
                raise VerificationError("artifact_type_owner_or_link_invalid")
            body = stream.read()
            after = os.fstat(stream.fileno())
        current = path.lstat()
    except VerificationError:
        raise
    except OSError as exc:
        raise VerificationError("artifact_unavailable") from exc
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
        or len(body) != after.st_size
    ):
        raise VerificationError("artifact_changed_during_read")
    return body


def _inside(root: Path, rel: Any) -> Path:
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise VerificationError("relative_artifact_path_invalid")
    p = root
    for part in Path(rel).parts:
        p = p / part
        try:
            entry = p.lstat()
        except OSError as exc:
            raise VerificationError("artifact_unavailable") from exc
        if stat.S_ISLNK(entry.st_mode):
            raise VerificationError("artifact_symlink_forbidden")
        if stat.S_ISDIR(entry.st_mode) and (
            entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o700
        ):
            raise VerificationError("artifact_directory_owner_or_mode_invalid")
    p = p.resolve(strict=True)
    try:
        p.relative_to(root)
    except ValueError as exc:
        raise VerificationError("artifact_outside_parent_root") from exc
    return p


def _parent_events(root: Path, owner: int) -> list[dict[str, Any]]:
    raw = _read(root / "events-v2.jsonl", owner=owner)
    if not raw.endswith(b"\n") or b"\r" in raw:
        raise VerificationError("parent_ledger_incomplete")
    rows = []
    prior = ZERO
    for seq, line in enumerate(raw.splitlines()):
        row = _json(line)
        if _canonical(row) + b"\n" != line + b"\n":
            raise VerificationError("parent_event_noncanonical")
        body = dict(row)
        supplied = body.pop("event_sha256", None)
        if (
            row.get("seq") != seq
            or row.get("previous_sha256") != prior
            or supplied != _sha(_canonical(body))
        ):
            raise VerificationError("parent_event_chain_invalid")
        row["event_sha256"] = supplied
        prior = supplied
        rows.append(row)
    return rows


def _decode_response(
    receipt: dict[str, Any],
    stored: bytes,
    *,
    expected_codec: str,
    codec_tool: Any,
    max_response_bytes: int,
) -> bytes:
    meta = receipt.get("storage")
    response_bytes = receipt.get("response_bytes")
    if (
        type(max_response_bytes) is not int
        or not 1 <= max_response_bytes <= MAX_RESPONSE_BYTES
        or type(response_bytes) is not int
        or not 0 <= response_bytes <= max_response_bytes
        or len(stored) > max_response_bytes + 1_048_576
    ):
        raise VerificationError("capture_response_size_invalid")
    if expected_codec == "raw" and meta is None:
        if len(stored) != response_bytes:
            raise VerificationError("raw_capture_size_mismatch")
        return stored
    if (
        expected_codec != "zstd"
        or not isinstance(codec_tool, dict)
        or not isinstance(meta, dict)
        or meta.get("codec") != "zstd"
        or meta.get("path") != "response.json.zst"
        or meta.get("bytes") != len(stored)
        or meta.get("sha256") != _sha(stored)
        or meta.get("codec_version") != codec_tool.get("version")
    ):
        raise VerificationError("compressed_capture_receipt_mismatch")
    executable = Path(str(codec_tool.get("path", "")))
    try:
        executable_stat = executable.lstat()
    except OSError as exc:
        raise VerificationError("zstd_verifier_executable_unavailable") from exc
    if (
        not executable.is_absolute()
        or stat.S_ISLNK(executable_stat.st_mode)
        or not stat.S_ISREG(executable_stat.st_mode)
        or not executable_stat.st_mode & 0o111
        or codec_tool.get("sha256") != _sha(_read(executable, owner=executable_stat.st_uid))
    ):
        raise VerificationError("zstd_verifier_executable_binding_invalid")
    version = subprocess.run(
        [str(executable), "--version"], capture_output=True, text=True, check=False
    )
    if version.returncode or version.stdout.strip() != codec_tool.get("version"):
        raise VerificationError("zstd_verifier_version_mismatch")
    with tempfile.TemporaryFile() as compressed:
        compressed.write(stored)
        compressed.seek(0)
        process = subprocess.Popen(
            [str(executable), "--quiet", "--decompress", "--stdout"],
            stdin=compressed,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        decoded = bytearray()
        while len(decoded) <= max_response_bytes:
            chunk = process.stdout.read(min(64 * 1024, max_response_bytes + 1 - len(decoded)))
            if not chunk:
                break
            decoded.extend(chunk)
        if len(decoded) > max_response_bytes:
            process.kill()
            process.wait()
            raise VerificationError("compressed_capture_exceeds_response_bound")
        if process.wait() != 0:
            raise VerificationError("compressed_capture_invalid")
    if len(decoded) != response_bytes:
        raise VerificationError("compressed_capture_raw_size_mismatch")
    if not decoded:
        raise VerificationError("compressed_capture_invalid")
    return bytes(decoded)


def _child(
    root: Path,
    operation: dict[str, Any],
    reservation: dict[str, Any],
    bundle: dict[str, Any],
    owner: int,
) -> dict[str, int]:
    child = _inside(root, reservation["child_path"])
    child_stat = child.lstat()
    if (
        not stat.S_ISDIR(child_stat.st_mode)
        or child_stat.st_uid != owner
        or stat.S_IMODE(child_stat.st_mode) != 0o700
    ):
        raise VerificationError("child_root_owner_or_mode_invalid")
    child_rel = reservation["child_path"]
    plan_path = child / "epoch-plan.json"
    plan_bytes = _read(plan_path, owner=owner)
    plan = _json(plan_bytes)
    if (
        _sha(plan_bytes) != reservation["child_plan_sha256"]
        or plan.get("epoch_id") != child.name
        or plan.get("schema_version") != 3
        or plan.get("protocol") != "senses-finalized-inventory-first-v3"
        or plan.get("provider") != "helius-free"
        or plan.get("capture_storage_codec") not in {"raw", "zstd"}
        or (
            plan.get("capture_storage_codec") == "raw"
            and plan.get("capture_codec_tool") is not None
        )
        or (
            plan.get("capture_storage_codec") == "zstd"
            and not isinstance(plan.get("capture_codec_tool"), dict)
        )
        or type(plan.get("max_response_bytes")) is not int
        or not 1 <= plan["max_response_bytes"] <= MAX_RESPONSE_BYTES
    ):
        raise VerificationError("child_plan_binding_invalid")
    summary_path = child / "epoch-summary.json"
    summary_bytes = _read(summary_path, owner=owner)
    summary = _json(summary_bytes)
    if (
        _sha(summary_bytes) != bundle.get("summary_sha256")
        or summary != bundle.get("summary")
        or summary.get("status") not in {"frame_complete", "bootstrap_complete"}
    ):
        raise VerificationError("child_summary_binding_or_status_invalid")
    if (
        bundle.get("operation_id") != operation["operation_id"]
        or bundle.get("bucket_id") != reservation["bucket_id"]
        or bundle.get("child_plan_sha256") != reservation["child_plan_sha256"]
        or bundle.get("claim_scope")
        != "captured_request_response_bundle_only_child_structure_not_independently_qualified"
        or bundle.get("child_plan_path") != f"{child_rel}/epoch-plan.json"
        or bundle.get("summary_path") != f"{child_rel}/epoch-summary.json"
        or bundle.get("frontier_before") != reservation.get("frontier_before")
    ):
        raise VerificationError("bundle_parent_identity_mismatch")
    boot = bool(plan.get("bootstrap"))
    if boot:
        if bundle.get("frame_path") is not None or bundle.get("selection_path") is not None:
            raise VerificationError("anchor_bundle_has_frame_artifacts")
    else:
        for path_key, hash_key, relname in (
            ("frame_path", "frame_sha256", "frame.json"),
            ("selection_path", "selection_sha256", "selection-plan.json"),
        ):
            artifact_path = _inside(root, bundle.get(path_key))
            if bundle.get(path_key) != f"{child_rel}/{relname}":
                raise VerificationError("bundle_frame_or_selection_path_invalid")
            if _sha(_read(artifact_path, owner=owner)) != bundle.get(hash_key):
                raise VerificationError("bundle_frame_or_selection_digest_invalid")
    expected_n = 1 if boot else 34
    if reservation.get("requests") != expected_n or bundle.get("actual_requests") != expected_n:
        raise VerificationError("parent_child_request_count_invalid")
    requests = []
    results = []
    for seq in range(1, expected_n + 1):
        rq = _inside(child, f"requests/{seq:012d}.json")
        rs = _inside(child, f"request-results/{seq:012d}.json")
        requests.append((_read(rq, owner=owner), rq))
        results.append((_read(rs, owner=owner), rs))
    if (
        len(list((child / "requests").glob("*.json"))) != expected_n
        or len(list((child / "request-results").glob("*.json"))) != expected_n
    ):
        raise VerificationError("unaccounted_child_journal_record")
    parsed: list[tuple[dict[str, Any], dict[str, Any], bytes, bytes, dict[str, Any]]] = []
    raw_total = stored_total = 0
    listed = bundle.get("children")
    if not isinstance(listed, list) or len(listed) != expected_n:
        raise VerificationError("bundle_child_list_count_invalid")
    for idx, ((rq_bytes, _rq_path), (rs_bytes, _rs_path), item) in enumerate(
        zip(requests, results, listed, strict=True), start=1
    ):
        rq, rs = _json(rq_bytes), _json(rs_bytes)
        if (
            rq.get("schema_version") != 3
            or rs.get("schema_version") != 3
            or rq.get("sequence") != idx
            or rs.get("sequence") != idx
            or rq.get("provider") != plan.get("provider")
            or rs.get("provider") != plan.get("provider")
            or rs.get("status") != "captured"
            or rs.get("result_status") != "parsed"
            or rq.get("method") != rs.get("method")
        ):
            raise VerificationError("child_journal_identity_or_status_invalid")
        params = rq.get("params")
        request_body = _canonical(
            {"jsonrpc": "2.0", "id": 1, "method": rq["method"], "params": params}
        )
        if (
            _sha(request_body) != rq.get("request_sha256")
            or _sha(_canonical(params)) != item.get("params_sha256")
            or item.get("request_sha256") != rq.get("request_sha256")
        ):
            raise VerificationError("child_request_digest_invalid")
        if item.get("request_file_sha256") != _sha(rq_bytes) or item.get(
            "result_file_sha256"
        ) != _sha(rs_bytes):
            raise VerificationError("bundle_journal_file_digest_invalid")
        cap_rel = rs.get("capture_path")
        if (
            not isinstance(cap_rel, str)
            or Path(cap_rel).is_absolute()
            or ".." in Path(cap_rel).parts
        ):
            raise VerificationError("child_capture_relpath_invalid")
        receipt_path = _inside(child, f"{cap_rel}/receipt.json")
        receipt_bytes = _read(receipt_path, owner=owner)
        receipt = _json(receipt_bytes)
        stored_rel = receipt.get("storage", {}).get("path", "response.json")
        if Path(stored_rel).is_absolute() or ".." in Path(stored_rel).parts:
            raise VerificationError("response_relpath_invalid")
        stored_path = _inside(child, f"{cap_rel}/{stored_rel}")
        stored_bytes = _read(stored_path, owner=owner)
        body = _decode_response(
            receipt,
            stored_bytes,
            expected_codec=plan.get("capture_storage_codec"),
            codec_tool=plan.get("capture_codec_tool"),
            max_response_bytes=plan.get("max_response_bytes", -1),
        )
        request_json = _json(receipt.get("request_json", "").encode())
        if (
            request_json.get("method") != rq.get("method")
            or request_json.get("params") != params
            or request_json.get("id") != 1
            or receipt.get("request_started_ns") != rs.get("request_started_ns")
            or receipt.get("received_ns") != rs.get("receipt_ns")
            or receipt.get("source_alias") != plan.get("provider")
            or type(rs.get("availability_ns")) is not int
            or rs.get("availability_ns", -1) < rs.get("receipt_ns", 0)
            or receipt.get("response_sha256") != _sha(body)
            or receipt.get("response_bytes") != len(body)
        ):
            raise VerificationError("capture_receipt_payload_mismatch")
        if (
            rs.get("response_sha256") != _sha(body)
            or rs.get("response_bytes") != len(body)
            or rs.get("stored_response_bytes") != len(stored_bytes)
        ):
            raise VerificationError("child_result_capture_mismatch")
        for time_key in ("request_started_ns", "receipt_ns", "availability_ns"):
            if type(rs.get(time_key)) is not int:
                raise VerificationError("child_time_coordinate_missing")
        if (
            not rs["request_started_ns"] <= rs["receipt_ns"]
            or rs["availability_ns"] < rs["receipt_ns"]
            or type(operation.get("started_at_ns")) is not int
            or rs["request_started_ns"] < operation["started_at_ns"]
            or rs["request_started_ns"] >= operation.get("bucket_end_ns", -1)
            or rs["request_started_ns"] >= operation.get("lease_expires_ns", -1)
        ):
            raise VerificationError("child_time_order_invalid")
        if (
            item.get("receipt_sha256") != _sha(receipt_bytes)
            or item.get("response_stored_sha256") != _sha(stored_bytes)
            or item.get("response_raw_sha256") != _sha(body)
            or item.get("raw_bytes") != len(body)
            or item.get("stored_bytes") != len(stored_bytes)
            or item.get("sequence") != idx
            or item.get("method") != rq.get("method")
            or item.get("request_path") != f"{child_rel}/requests/{idx:012d}.json"
            or item.get("result_path") != f"{child_rel}/request-results/{idx:012d}.json"
            or item.get("receipt_path") != f"{child_rel}/{cap_rel}/receipt.json"
            or item.get("response_path") != f"{child_rel}/{cap_rel}/{stored_rel}"
            or item.get("request_started_ns") != rs.get("request_started_ns")
            or item.get("receipt_ns") != rs.get("receipt_ns")
            or item.get("availability_ns") != rs.get("availability_ns")
        ):
            raise VerificationError("bundle_capture_receipt_or_bytes_mismatch")
        raw_total += len(body)
        stored_total += len(stored_bytes)
        parsed.append((rq, rs, body, stored_bytes, receipt))
    _check_operations(plan, child, parsed, boot, summary, owner)
    if (
        summary.get("request_reservations") != expected_n
        or summary.get("captured_response_bytes") != raw_total
        or summary.get("stored_response_bytes") != stored_total
    ):
        raise VerificationError("child_summary_recomputed_totals_mismatch")
    if (
        bundle.get("actual_raw_bytes") != raw_total
        or bundle.get("actual_stored_bytes") != stored_total
    ):
        raise VerificationError("bundle_recomputed_totals_mismatch")
    if raw_total > reservation.get("raw_bytes", -1) or stored_total > reservation.get(
        "stored_bytes", -1
    ):
        raise VerificationError("parent_envelope_byte_cap_exceeded")
    frontier_after = summary.get("bootstrap_slot", -1) + 1 if boot else summary.get("advance_to")
    if (
        type(frontier_after) is not int
        or bundle.get("frontier_after") != frontier_after
        or (not boot and plan.get("next_start") != reservation.get("frontier_before"))
    ):
        raise VerificationError("parent_child_frontier_binding_invalid")
    return {"requests": expected_n, "raw_bytes": raw_total, "stored_bytes": stored_total}


def _validate_v3_prefix(child: Path, reservation: dict[str, Any], owner: int) -> None:
    child_stat = child.lstat()
    if (
        not stat.S_ISDIR(child_stat.st_mode)
        or child_stat.st_uid != owner
        or stat.S_IMODE(child_stat.st_mode) != 0o700
    ):
        raise VerificationError("child_root_owner_or_mode_invalid")
    plan_bytes = _read(child / "epoch-plan.json", owner=owner)
    plan = _json(plan_bytes)
    if (
        _sha(plan_bytes) != reservation.get("child_plan_sha256")
        or plan.get("schema_version") != 3
        or plan.get("protocol") != "senses-finalized-inventory-first-v3"
        or plan.get("provider") != "helius-free"
        or plan.get("epoch_id") != child.name
    ):
        raise VerificationError("child_plan_v3_required")
    request_dir, result_dir = child / "requests", child / "request-results"
    for directory in (request_dir, result_dir):
        if directory.exists():
            _inside(child, directory.name)
    request_paths = sorted(request_dir.glob("*.json")) if request_dir.exists() else []
    result_paths = sorted(result_dir.glob("*.json")) if result_dir.exists() else []
    request_names = {path.name for path in request_paths}
    result_names = {path.name for path in result_paths}
    if result_names - request_names:
        raise VerificationError("child_result_without_reservation")
    expected_count = reservation["requests"]
    if len(request_paths) > expected_count:
        raise VerificationError("child_prefix_exceeds_reserved_request_count")
    if any(path.name != f"{index:012d}.json" for index, path in enumerate(request_paths, 1)):
        raise VerificationError("child_request_prefix_not_contiguous")
    for index, request_path in enumerate(request_paths, 1):
        request = _json(_read(request_path, owner=owner))
        if (
            request.get("schema_version") != 3
            or request.get("sequence") != index
            or request.get("provider") != plan.get("provider")
        ):
            raise VerificationError("child_request_v3_identity_invalid")
        result_path = result_dir / request_path.name
        if not result_path.exists():
            continue
        result = _json(_read(result_path, owner=owner))
        if (
            result.get("schema_version") != 3
            or result.get("sequence") != index
            or result.get("provider") != plan.get("provider")
        ):
            raise VerificationError("child_result_v3_identity_invalid")
        if result.get("status") == "captured":
            receipt_ns = result.get("receipt_ns")
            availability_ns = result.get("availability_ns")
            if (
                result.get("result_status") not in {"parsed", "response_error"}
                or type(result.get("request_started_ns")) is not int
                or type(receipt_ns) is not int
                or type(availability_ns) is not int
                or not result["request_started_ns"] <= receipt_ns <= availability_ns
            ):
                raise VerificationError("captured_child_time_coordinates_invalid")
        elif result.get("status") == "received_persistence_failed":
            if (
                type(result.get("request_started_ns")) is not int
                or type(result.get("receipt_ns")) is not int
                or result.get("availability_ns") is not None
                or type(result.get("failure_observed_ns")) is not int
            ):
                raise VerificationError("failed_persistence_claims_payload_availability")
        elif result.get("status") == "captured_availability_unknown":
            if (
                type(result.get("request_started_ns")) is not int
                or type(result.get("receipt_ns")) is not int
                or result.get("availability_ns") is not None
                or type(result.get("failure_observed_ns")) is not int
                or result.get("result_status") != "capture_clock_order_invalid"
            ):
                raise VerificationError("clock_regression_claims_payload_availability")
        elif result.get("status") == "request_failed":
            if (
                result.get("receipt_ns") is not None
                or result.get("availability_ns") is not None
                or type(result.get("failure_observed_ns")) is not int
            ):
                raise VerificationError("request_failure_claims_payload_availability")
        else:
            raise VerificationError("child_result_status_unknown")


def _check_operations(
    plan: dict[str, Any],
    child: Path,
    parsed: list[tuple[dict[str, Any], dict[str, Any], bytes, bytes, dict[str, Any]]],
    boot: bool,
    summary: dict[str, Any],
    owner: int,
) -> None:
    anchor_result = _json(parsed[0][2])
    anchor_slot = anchor_result.get("result") if isinstance(anchor_result, dict) else None
    anchor_row = parsed[0][1]
    if (
        type(anchor_slot) is not int
        or anchor_slot < 1
        or anchor_row.get("slot_value") != anchor_slot
    ):
        raise VerificationError("anchor_slot_value_invalid")
    if boot:
        if (
            parsed[0][4].get("received_ns") != anchor_row.get("receipt_ns")
            or anchor_row.get("availability_ns") < anchor_row.get("receipt_ns")
            or summary.get("bootstrap_slot") != anchor_slot
            or summary.get("bootstrap_receipt_ns") != anchor_row.get("receipt_ns")
        ):
            raise VerificationError("anchor_availability_binding_invalid")
    elif anchor_row.get("availability_ns") < anchor_row.get("receipt_ns") or anchor_row.get(
        "receipt_ns"
    ) != parsed[0][4].get("received_ns"):
        raise VerificationError("anchor_availability_binding_invalid")
    expected: list[tuple[str, list[Any], int | None, str | None]] = [
        ("getSlot", [{"commitment": "finalized"}], None, "anchor")
    ]
    if not boot:
        frame = _json(_read(_inside(child, "frame.json"), owner=owner))
        start, end = frame.get("start"), frame.get("end_exclusive")
        if (
            frame.get("status") != "captured"
            or type(start) is not int
            or type(end) is not int
            or end <= start
            or start != plan.get("next_start")
            or end != anchor_slot + 1
            or frame.get("anchor_slot") != anchor_slot
            or frame.get("anchor_receipt_ns") != anchor_row.get("receipt_ns")
        ):
            raise VerificationError("frame_definition_invalid")
        expected.append(
            ("getBlocks", [start, end - 1, {"commitment": "finalized"}], None, "inventory")
        )
        inventory = _json(parsed[1][2])
        slots = inventory.get("result") if isinstance(inventory, dict) else None
        if (
            not isinstance(slots, list)
            or any(type(s) is not int for s in slots)
            or slots != sorted(set(slots))
            or any(s < start or s >= end for s in slots)
        ):
            raise VerificationError("inventory_payload_invalid")
        selection = _json(_read(_inside(child, "selection-plan.json"), owner=owner))
        count = min(plan.get("selection_count", -1), len(slots))
        seed = f"{plan['seed']}:{plan['epoch_id']}:{parsed[1][1]['response_sha256']}"
        selected = sorted(random.Random(seed).sample(slots, count)) if count else []
        if (
            selection.get("inventory_sha256") != parsed[1][1].get("response_sha256")
            or selection.get("inventory_denominator") != len(slots)
            or selection.get("selection_count") != count
            or selection.get("selected_slots") != selected
            or selection.get("seed") != plan.get("seed")
            or selection.get("selection_rule")
            != "uniform_without_replacement_from_finalized_getblocks_members"
        ):
            raise VerificationError("selection_recomputation_mismatch")
        if len(selected) != 16:
            raise VerificationError("frame_selection_not_16")
        if (
            summary.get("start") != start
            or summary.get("end_exclusive") != end
            or summary.get("advance_to") != end
            or summary.get("selected_slots") != selected
            or summary.get("inventory_sha256") != parsed[1][1].get("response_sha256")
        ):
            raise VerificationError("summary_frontier_or_selection_binding_invalid")
        for slot in selected:
            expected.extend(
                [
                    (
                        "getBlock",
                        [
                            slot,
                            {
                                "commitment": "finalized",
                                "encoding": "json",
                                "transactionDetails": "full",
                                "rewards": False,
                                "maxSupportedTransactionVersion": 1,
                            },
                        ],
                        slot,
                        "full_block",
                    ),
                    (
                        "getBlock",
                        [
                            slot,
                            {
                                "commitment": "finalized",
                                "encoding": "json",
                                "transactionDetails": "signatures",
                                "rewards": False,
                            },
                        ],
                        slot,
                        "signature_check",
                    ),
                ]
            )
    if len(expected) != len(parsed):
        raise VerificationError("expected_child_operation_count_mismatch")
    for (method, params, slot, phase), (rq, rs, _, _, _) in zip(expected, parsed, strict=True):
        if (
            rq.get("method") != method
            or rq.get("params") != params
            or rq.get("phase") != phase
            or rs.get("phase") != phase
            or rq.get("slot") != slot
            or rs.get("slot") != slot
        ):
            raise VerificationError("child_operation_order_or_parameters_invalid")


def verify(root: Path) -> dict[str, Any]:
    """Verify one local envelope tree and return a read-only recovery recommendation."""
    original_root = root
    try:
        if stat.S_ISLNK(original_root.lstat().st_mode):
            raise VerificationError("parent_root_symlink_forbidden")
    except OSError as exc:
        raise VerificationError("parent_root_unavailable") from exc
    root = root.resolve(strict=True)
    st = root.lstat()
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o700
    ):
        raise VerificationError("parent_root_owner_or_mode_invalid")
    owner = os.getuid()
    plan_bytes = _read(root / "plan-v2.json", owner=owner)
    plan = _json(plan_bytes)
    _validate_plan(plan, plan_bytes)
    rows = _parent_events(root, owner)
    if (
        not rows
        or rows[0].get("kind") != "initialized"
        or rows[0].get("plan_sha256") != _sha(plan_bytes)
    ):
        raise VerificationError("parent_plan_not_bound")
    regressed_frontier_operations = _verify_transitions(plan, rows)
    reservations = {r["operation_id"]: r for r in rows if r["kind"] == "reservation"}
    starts = {r["operation_id"]: r for r in rows if r["kind"] == "started"}
    bundles = {r["operation_id"]: r for r in rows if r["kind"] == "bundle"}
    seals = {r["operation_id"]: r for r in rows if r["kind"] == "seal"}
    counts = {"requests": 0, "raw_bytes": 0, "stored_bytes": 0}
    for oid, reservation in reservations.items():
        if oid not in starts:
            child = _inside(root, reservation["child_path"])
            _validate_v3_prefix(child, reservation, owner)
            plan_body = _read(child / "epoch-plan.json", owner=owner)
            if _sha(plan_body) != reservation.get("child_plan_sha256"):
                raise VerificationError("unstarted_child_plan_binding_invalid")
            request_dir = child / "requests"
            result_dir = child / "request-results"
            if (request_dir.exists() and list(request_dir.iterdir())) or (
                result_dir.exists() and list(result_dir.iterdir())
            ):
                raise VerificationError("child_io_without_parent_started_barrier")
            counts["requests"] += reservation["requests"]
            continue
        if oid not in bundles:
            child = _inside(root, reservation["child_path"])
            _validate_v3_prefix(child, reservation, owner)
            return {
                "schema": f"{SCHEMA}-verifier-v2",
                "valid": True,
                "recovery": "stop_unknown_started_operation_no_replay",
                "activation_authorized": False,
            }
        bundle_event = bundles[oid]
        bundle_path = _inside(root, bundle_event.get("bundle_path"))
        bundle_bytes = _read(bundle_path, owner=owner)
        if _sha(bundle_bytes) != bundle_event.get("bundle_sha256"):
            raise VerificationError("parent_bundle_hash_invalid")
        bundle = _json(bundle_bytes)
        bucket = next(b for b in plan["buckets"] if b["bucket_id"] == reservation["bucket_id"])
        totals = _child(
            root,
            {
                "operation_id": oid,
                "started_at_ns": starts[oid]["at_ns"],
                "bucket_end_ns": bucket["end_ns"],
                "lease_expires_ns": plan["expires_ns"],
            },
            reservation,
            bundle,
            owner,
        )
        if any(bundle_event.get("actual_" + key) != value for key, value in totals.items()):
            raise VerificationError("parent_child_totals_mismatch")
        if oid not in seals:
            return {
                "schema": f"{SCHEMA}-verifier-v2",
                "valid": True,
                "recovery": "stopped_no_replay"
                if any(row["kind"] == "stop" for row in rows)
                else "frontier_regression_stop_required_no_replay"
                if oid in regressed_frontier_operations
                else "verified_bundle_ready_to_seal_no_replay",
                "verified_child_totals": totals,
                "activation_authorized": False,
            }
        if _sha(bundle_bytes) != seals[oid].get("bundle_sha256") or any(
            seals[oid].get("actual_" + key) != value for key, value in totals.items()
        ):
            raise VerificationError("parent_seal_binding_or_totals_invalid")
        for key, value in totals.items():
            counts[key] += value
    return {
        "schema": f"{SCHEMA}-verifier-v2",
        "valid": True,
        "recovery": "stopped_no_replay"
        if any(row["kind"] == "stop" for row in rows)
        else "sealed"
        if len(seals) == len(reservations)
        else "unstarted_reservation_may_be_repaired_or_gapped",
        "usage": counts,
        "operation_count": len(reservations),
        "activation_authorized": False,
    }


def _validate_plan(plan: Any, encoded: bytes) -> None:
    if not isinstance(plan, dict) or plan.get("schema") != SCHEMA:
        raise VerificationError("parent_plan_schema_invalid")
    if _canonical(plan) + b"\n" != encoded:
        raise VerificationError("parent_plan_noncanonical")
    if (
        not isinstance(plan.get("lease_id"), str)
        or not plan["lease_id"]
        or type(plan.get("created_ns")) is not int
        or type(plan.get("expires_ns")) is not int
        or plan["expires_ns"] <= plan["created_ns"]
    ):
        raise VerificationError("parent_plan_identity_or_clock_invalid")
    caps = plan.get("caps")
    cap_names = (
        "requests",
        "raw_bytes",
        "stored_bytes",
        "physical_bytes",
        "width",
        "daily_requests",
        "daily_raw_bytes",
        "daily_stored_bytes",
    )
    if not isinstance(caps, dict) or any(
        type(caps.get(key)) is not int or caps[key] <= 0 for key in cap_names
    ):
        raise VerificationError("parent_plan_caps_invalid")
    if caps["physical_bytes"] < 50 * 1024**3:
        raise VerificationError("parent_plan_disk_floor_below_minimum")
    source = plan.get("source_sha256")
    if (
        not isinstance(source, dict)
        or not source
        or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or len(value) != 64
            or any(ch not in "0123456789abcdef" for ch in value)
            for key, value in source.items()
        )
    ):
        raise VerificationError("parent_plan_source_binding_invalid")
    buckets = plan.get("buckets")
    if not isinstance(buckets, list) or not buckets:
        raise VerificationError("parent_plan_buckets_invalid")
    seen: set[str] = set()
    previous_end = plan["created_ns"]
    for bucket in buckets:
        if (
            not isinstance(bucket, dict)
            or not isinstance(bucket.get("bucket_id"), str)
            or not bucket["bucket_id"]
            or bucket["bucket_id"] in seen
            or bucket.get("kind") not in {"anchor", "frame"}
            or type(bucket.get("start_ns")) is not int
            or type(bucket.get("end_ns")) is not int
            or bucket["start_ns"] < previous_end
            or bucket["end_ns"] <= bucket["start_ns"]
            or bucket["end_ns"] > plan["expires_ns"]
        ):
            raise VerificationError("parent_plan_bucket_schedule_invalid")
        seen.add(bucket["bucket_id"])
        previous_end = bucket["end_ns"]
    if buckets[0]["kind"] != "anchor":
        raise VerificationError("parent_plan_first_bucket_not_anchor")
    predecessor = plan.get("predecessor")
    if (
        not isinstance(predecessor, dict)
        or not isinstance(predecessor.get("terminal_sha256"), str)
        or len(predecessor["terminal_sha256"]) != 64
        or any(ch not in "0123456789abcdef" for ch in predecessor["terminal_sha256"])
        or type(predecessor.get("frontier_slot")) is not int
        or predecessor["frontier_slot"] < 0
        or not isinstance(predecessor.get("gaps"), list)
        or any(not isinstance(gap, str) or not gap for gap in predecessor["gaps"])
    ):
        raise VerificationError("parent_plan_predecessor_invalid")


def _verify_transitions(plan: dict[str, Any], rows: list[dict[str, Any]]) -> set[str]:
    if not rows or rows[0].get("kind") != "initialized":
        raise VerificationError("parent_initialization_missing")
    if any(row.get("kind") == "initialized" for row in rows[1:]):
        raise VerificationError("parent_duplicate_initialization")
    buckets = {row["bucket_id"]: row for row in plan["buckets"]}
    reservations: dict[str, dict[str, Any]] = {}
    starts: set[str] = set()
    bundles: dict[str, dict[str, Any]] = {}
    seals: dict[str, dict[str, Any]] = {}
    regressed_frontier_operations: set[str] = set()
    gapped: set[str] = set()
    child_paths: set[str] = set()
    frontier = plan["predecessor"]["frontier_slot"]
    reset_required = bool(plan["predecessor"]["gaps"])
    stopped = False
    for row in rows[1:]:
        kind = row.get("kind")
        oid = row.get("operation_id")
        if stopped:
            raise VerificationError("parent_event_after_stop")
        if kind == "reservation":
            bucket = buckets.get(row.get("bucket_id"))
            count = 1 if bucket and bucket["kind"] == "anchor" else 34
            if (
                not isinstance(oid, str)
                or not _SAFE_ID.fullmatch(oid)
                or oid in reservations
                or bucket is None
                or bucket["kind"] != row.get("bucket_kind")
                or row["bucket_id"] in gapped
                or any(r["bucket_id"] == row["bucket_id"] for r in reservations.values())
                or row.get("requests") != count
                or row.get("method") != ("v3-anchor" if count == 1 else "v3-frame")
                or type(row.get("at_ns")) is not int
                or not bucket["start_ns"]
                <= row["at_ns"]
                < min(bucket["end_ns"], plan["expires_ns"])
                or row.get("utc_day_index") != row["at_ns"] // 86_400_000_000_000
                or row.get("physical_free_bytes", 0) < plan["caps"]["physical_bytes"]
                or not isinstance(row.get("child_path"), str)
                or row.get("child_path") in child_paths
                or not isinstance(row.get("child_plan_sha256"), str)
                or len(row["child_plan_sha256"]) != 64
                or any(ch not in "0123456789abcdef" for ch in row["child_plan_sha256"])
                or row.get("request_sha256")
                != _sha(
                    _canonical(
                        {
                            "bucket_id": row["bucket_id"],
                            "child_plan_sha256": row["child_plan_sha256"],
                            "request_count": count,
                        }
                    )
                )
            ):
                raise VerificationError("parent_reservation_invalid")
            outstanding = set(reservations) - set(seals)
            if outstanding:
                raise VerificationError("parent_multiple_outstanding_operations")
            known = set(gapped) | {r["bucket_id"] for r in reservations.values()}
            if any(
                b["end_ns"] <= row["at_ns"]
                and b["bucket_id"] not in known
                and b["bucket_id"] != row["bucket_id"]
                for b in plan["buckets"]
            ):
                raise VerificationError("parent_missed_bucket_not_gapped")
            latest_gap = max(
                (r["seq"] for r in rows[: row["seq"]] if r["kind"] == "gap"), default=-1
            )
            reservation_map = {r["operation_id"]: r for r in reservations.values()}
            latest_anchor = max(
                (
                    r["seq"]
                    for r in rows[: row["seq"]]
                    if r["kind"] == "seal"
                    and reservation_map[r["operation_id"]]["bucket_kind"] == "anchor"
                    and r.get("status") == "captured_bundle"
                ),
                default=-1,
            )
            if latest_gap > latest_anchor and bucket["kind"] != "anchor":
                raise VerificationError("parent_gap_requires_anchor")
            if bucket["kind"] == "frame" and latest_anchor < 0:
                raise VerificationError("parent_frame_without_anchor")
            if row.get("frontier_before") != frontier:
                raise VerificationError("parent_reservation_frontier_mismatch")
            if bucket["kind"] == "frame" and reset_required:
                raise VerificationError("parent_gap_requires_anchor")
            child_paths.add(row["child_path"])
            reservations[oid] = row
        elif kind == "started":
            reservation = reservations.get(oid)
            if (
                not reservation
                or oid in starts
                or row.get("child_plan_sha256") != reservation.get("child_plan_sha256")
                or type(row.get("at_ns")) is not int
                or row["at_ns"] < reservation["at_ns"]
                or row["at_ns"]
                >= min(buckets[reservation["bucket_id"]]["end_ns"], plan["expires_ns"])
                or row.get("physical_free_bytes", 0) < plan["caps"]["physical_bytes"]
            ):
                raise VerificationError("parent_start_invalid")
            starts.add(oid)
        elif kind == "bundle":
            reservation = reservations.get(oid)
            if (
                reservation is None
                or oid not in starts
                or oid in bundles
                or row.get("child_plan_sha256") != reservation.get("child_plan_sha256")
                or row.get("actual_requests", 0) > reservation["requests"]
                or row.get("actual_raw_bytes", 0) > reservation["raw_bytes"]
                or row.get("actual_stored_bytes", 0) > reservation["stored_bytes"]
                or not isinstance(row.get("bundle_sha256"), str)
                or len(row["bundle_sha256"]) != 64
                or row.get("frontier_before") != reservation.get("frontier_before")
                or type(row.get("frontier_after")) is not int
                or row["frontier_after"] <= 0
                or row.get("bundle_path") != f"bundles/{oid}.json"
            ):
                raise VerificationError("parent_bundle_transition_invalid")
            if row["frontier_after"] < reservation["frontier_before"]:
                regressed_frontier_operations.add(oid)
            bundles[oid] = row
        elif kind == "seal":
            reservation = reservations.get(oid)
            bundle = bundles.get(oid)
            if (
                not reservation
                or not bundle
                or oid in seals
                or row.get("status") != "captured_bundle"
                or row.get("bundle_sha256") != bundle.get("bundle_sha256")
                or row.get("actual_requests", 0) > reservation["requests"]
                or row.get("actual_raw_bytes", 0) > reservation["raw_bytes"]
                or row.get("actual_stored_bytes", 0) > reservation["stored_bytes"]
            ):
                raise VerificationError("parent_seal_invalid")
            if oid in regressed_frontier_operations:
                raise VerificationError("parent_bundle_frontier_regression_seal_forbidden")
            if reservation["bucket_kind"] == "anchor":
                if bundle["frontier_after"] < frontier:
                    raise VerificationError("parent_anchor_frontier_regression")
                frontier = bundle["frontier_after"]
                reset_required = False
            else:
                if reset_required or bundle.get("frontier_before") != frontier:
                    raise VerificationError("parent_frame_frontier_discontinuity")
                if bundle["frontier_after"] < frontier:
                    raise VerificationError("parent_frame_frontier_regression")
                frontier = bundle["frontier_after"]
            seals[oid] = row
        elif kind == "gap":
            bucket = buckets.get(row.get("bucket_id"))
            if (
                bucket is None
                or row["bucket_id"] in gapped
                or any(r["bucket_id"] == row["bucket_id"] for r in reservations.values())
                or type(row.get("at_ns")) is not int
                or row["at_ns"] < bucket["end_ns"]
                or not isinstance(row.get("reason"), str)
                or not row["reason"]
                or any(operation_id not in seals for operation_id in reservations)
            ):
                raise VerificationError("parent_gap_invalid")
            gapped.add(row["bucket_id"])
            reset_required = True
        elif kind == "stop":
            if not isinstance(row.get("reason"), str) or not row["reason"]:
                raise VerificationError("parent_stop_reason_invalid")
            if any(
                operation_id not in seals and operation_id in starts
                for operation_id in reservations
            ):
                if (
                    row.get("unknown_interval") is not True
                    or row.get("retry_started_child") is not False
                ):
                    raise VerificationError("parent_stop_unknown_interval_contract_invalid")
            stopped = True
        else:
            raise VerificationError("parent_transition_kind_invalid")
    usage = {"requests": 0, "raw_bytes": 0, "stored_bytes": 0}
    daily: dict[int, dict[str, int]] = {}
    for oid, reservation in reservations.items():
        seal = seals.get(oid)
        actual = {
            key: seal.get("actual_" + key, reservation[key])
            if seal and seal.get("status") == "captured_bundle"
            else reservation[key]
            for key in usage
        }
        for key, value in actual.items():
            usage[key] += value
        day = reservation["utc_day_index"]
        day_total = daily.setdefault(day, {key: 0 for key in usage})
        for key, value in actual.items():
            day_total[key] += value
    if any(usage[key] > plan["caps"][key] for key in usage):
        raise VerificationError("parent_lease_budget_exceeded")
    daily_caps = {
        "requests": plan["caps"]["daily_requests"],
        "raw_bytes": plan["caps"]["daily_raw_bytes"],
        "stored_bytes": plan["caps"]["daily_stored_bytes"],
    }
    if any(amount[key] > daily_caps[key] for amount in daily.values() for key in usage):
        raise VerificationError("parent_utc_day_budget_exceeded")
    return regressed_frontier_operations
