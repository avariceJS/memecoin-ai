#!/usr/bin/env python3
"""Inventory-first, restartable finalized-block Senses epochs (schema v3)."""

from __future__ import annotations

import hashlib
import os
import platform
import random
import shutil
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from memecoin_ai.domain import canonical, digest  # noqa: E402
from memecoin_ai.sources import (  # noqa: E402
    Capture,
    TransportSourceError,
    _zstd_executable,
    verify_capture_storage_codec,
)
from tools.brain_senses_block_provider_pilot import (  # noqa: E402
    MAX_RESPONSE_BYTES,
    _pump_invocations,
    _signature_list,
    _structural_response_validation,
)
from tools.brain_senses_program_watch import (  # noqa: E402
    _create,
    _read,
)

SCHEMA_VERSION = 3
PROTOCOL = "senses-finalized-inventory-first-v3"
PROVIDER = "helius-free"
MAX_GET_BLOCKS_WIDTH = 500_000


def _durable_capture(capture: Capture, path: Path, *, storage_codec: str) -> None:
    """Persist one capture under its private child root without changing shared Capture.save."""
    capture.save(path, storage_codec=storage_codec)
    entry = path.lstat()
    if not os.path.isdir(path) or os.path.islink(path) or entry.st_uid != os.getuid():
        raise ValueError("capture_directory_identity_invalid")
    os.chmod(path, 0o700, follow_symlinks=False)
    response_name = "response.json.zst" if storage_codec == "zstd" else "response.json"
    for name in (response_name, "receipt.json"):
        fd = os.open(path / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    for directory in (path, path.parent):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_epoch_plan(
    *,
    epoch_id: str,
    bucket: str,
    schedule_id: str,
    schedule_sha256: str,
    seed: str,
    bootstrap: bool,
    next_start: int | None,
    frame_width_cap: int,
    selection_count: int,
    request_budget: int,
    raw_byte_budget: int,
    stored_byte_budget: int,
    minimum_free_disk_bytes: int = 0,
    capture_storage_codec: str = "raw",
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    if not epoch_id or not bucket or not schedule_id or not seed:
        raise ValueError("epoch_identity_required")
    if not schedule_sha256 or len(schedule_sha256) != 64:
        raise ValueError("schedule_hash_invalid")
    if not 1 <= frame_width_cap <= MAX_GET_BLOCKS_WIDTH:
        raise ValueError("frame_width_cap_outside_getblocks_bound")
    if selection_count < 0 or selection_count > frame_width_cap:
        raise ValueError("selection_count_outside_bound")
    if request_budget < 1 or raw_byte_budget < 1 or stored_byte_budget < 1:
        raise ValueError("epoch_budget_must_be_positive")
    if minimum_free_disk_bytes < 0:
        raise ValueError("minimum_free_disk_bytes_outside_bound")
    if not 1 <= max_response_bytes <= MAX_RESPONSE_BYTES:
        raise ValueError("max_response_bytes_outside_bound")
    if bootstrap and next_start is not None:
        raise ValueError("bootstrap_must_not_have_frontier")
    if not bootstrap and (type(next_start) is not int or next_start < 1):
        raise ValueError("frame_epoch_requires_frontier")
    verify_capture_storage_codec(capture_storage_codec)
    codec_tool = None
    if capture_storage_codec == "zstd":
        executable, version = _zstd_executable()
        executable_path = Path(executable).resolve(strict=True)
        if not executable_path.is_absolute() or not executable_path.is_file():
            raise ValueError("zstd_executable_path_invalid")
        codec_tool = {
            "path": str(executable_path),
            "version": version,
            "sha256": _sha(executable_path),
        }
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "epoch_id": epoch_id,
        "bucket": bucket,
        "schedule_id": schedule_id,
        "schedule_sha256": schedule_sha256,
        "provider": PROVIDER,
        "seed": seed,
        "selection_rule": "uniform_without_replacement_from_finalized_getblocks_members",
        "bootstrap": bootstrap,
        "start": next_start,
        "next_start": next_start,
        "frame_width_cap": frame_width_cap,
        "selection_count": selection_count,
        "request_budget": request_budget,
        "raw_byte_budget": raw_byte_budget,
        "stored_byte_budget": stored_byte_budget,
        "minimum_free_disk_bytes": minimum_free_disk_bytes,
        "capture_storage_codec": capture_storage_codec,
        "capture_codec_tool": codec_tool,
        "max_response_bytes": max_response_bytes,
        "retry_policy": "one durable reservation per operation; never replay ambiguous attempts",
        "scope": (
            "provider-reported finalized produced-slot inventory; selected structural body checks; "
            "no independent inventory truth or economic-zero claim"
        ),
        "code_commit": commit,
        "code_sha256": _sha(Path(__file__)),
        "python_version": platform.python_version(),
        "created_ns": time.time_ns(),
    }


def _request_body(method: str, params: list[Any]) -> bytes:
    return canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()


def _operations_for(
    root: Path, plan: dict[str, Any], selection: dict[str, Any] | None
) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = [
        {"phase": "anchor", "method": "getSlot", "params": [{"commitment": "finalized"}]}
    ]
    frame_path = root / "frame.json"
    frame = _read(frame_path) if not plan["bootstrap"] and frame_path.exists() else None
    if plan["bootstrap"] or frame is None or frame.get("status") != "captured":
        return operations
    start, end = frame["start"], frame["end_exclusive"]
    if end == start or frame.get("status") in {"oversized_frame", "anchor_regression"}:
        return operations
    operations.append(
        {
            "phase": "inventory",
            "method": "getBlocks",
            "params": [start, end - 1, {"commitment": "finalized"}],
            "advance_to": end,
        }
    )
    if selection is not None:
        for slot in selection["selected_slots"]:
            operations.extend(
                [
                    {
                        "phase": "full_block",
                        "method": "getBlock",
                        "slot": slot,
                        "detail": "full",
                        "advance_to": end,
                        "params": [
                            slot,
                            {
                                "commitment": "finalized",
                                "encoding": "json",
                                "transactionDetails": "full",
                                "rewards": False,
                                "maxSupportedTransactionVersion": 1,
                            },
                        ],
                    },
                    {
                        "phase": "signature_check",
                        "method": "getBlock",
                        "slot": slot,
                        "detail": "signatures",
                        "advance_to": end,
                        "params": [
                            slot,
                            {
                                "commitment": "finalized",
                                "encoding": "json",
                                "transactionDetails": "signatures",
                                "rewards": False,
                            },
                        ],
                    },
                ]
            )
    return operations


def _result_identity(row: dict[str, Any], op: dict[str, Any], sequence: int) -> bool:
    return all(
        row.get(key) == value
        for key, value in {
            "sequence": sequence,
            "phase": op["phase"],
            "method": op["method"],
            "slot": op.get("slot"),
            "detail": op.get("detail"),
        }.items()
    )


def _counts(root: Path) -> tuple[int, int, int]:
    raw = stored = 0
    for path in (root / "request-results").glob("*.json"):
        row = _read(path)
        raw += int(row.get("response_bytes") or 0)
        stored += int(row.get("stored_response_bytes") or 0)
    requests = len(list((root / "requests").glob("*.json")))
    return requests, raw, stored


def _capture_result(root: Path, row: dict[str, Any]) -> Any | None:
    if row.get("status") != "captured" or row.get("result_status") != "parsed":
        return None
    capture = Capture.load(root / row["capture_path"])
    if digest(capture.response) != row.get("response_sha256"):
        raise ValueError("capture_result_hash_mismatch")
    return capture.result()


def _validate_journal(root: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    requests = sorted((root / "requests").glob("*.json"))
    results = sorted((root / "request-results").glob("*.json"))
    request_names = {p.name for p in requests}
    result_names = {p.name for p in results}
    if result_names - request_names:
        raise ValueError("result_without_reservation")
    selection_path = root / "selection-plan.json"
    selection = _read(selection_path) if selection_path.exists() else None
    operations = _operations_for(root, plan, selection)
    if len(requests) > len(operations):
        raise ValueError("journal_exceeds_pinned_operation_plan")
    if request_names - result_names:
        seq = min(int(path[:-5]) for path in request_names - result_names)
        reservation = _read(root / "requests" / f"{seq:012d}.json")
        operation = operations[seq - 1]
        expected_hash = hashlib.sha256(
            _request_body(operation["method"], operation["params"])
        ).hexdigest()
        if (
            reservation.get("sequence") != seq
            or reservation.get("phase") != operation["phase"]
            or reservation.get("provider") != plan["provider"]
            or reservation.get("method") != operation["method"]
            or reservation.get("params") != operation["params"]
            or reservation.get("request_sha256") != expected_hash
            or reservation.get("advance_to") != operation.get("advance_to")
        ):
            raise ValueError("ambiguous_reservation_identity_mismatch")
        _seal(
            root,
            status=(
                "anchor_ambiguous"
                if reservation.get("phase") == "anchor"
                else "inventory_unknown"
                if reservation.get("phase") == "inventory"
                else "body_ambiguous"
            ),
            reason="ambiguous_reservation_no_replay",
            advance_to=reservation.get("advance_to"),
            sequence=seq,
        )
        raise _Terminal("ambiguous_reservation_no_replay")
    if len(results) != len(requests):
        raise ValueError("journal_count_mismatch")
    for seq, path in enumerate(results, start=1):
        if path.name != f"{seq:012d}.json":
            raise ValueError("journal_sequence_gap")
        operation = operations[seq - 1]
        reservation = _read(root / "requests" / path.name)
        result = _read(path)
        expected_hash = hashlib.sha256(
            _request_body(operation["method"], operation["params"])
        ).hexdigest()
        if (
            reservation.get("sequence") != seq
            or reservation.get("phase") != operation["phase"]
            or reservation.get("provider") != plan["provider"]
            or reservation.get("method") != operation["method"]
            or reservation.get("params") != operation["params"]
            or reservation.get("request_sha256") != expected_hash
            or result.get("provider") != plan["provider"]
            or reservation.get("advance_to") != operation.get("advance_to")
            or not _result_identity(result, operation, seq)
        ):
            raise ValueError("journal_operation_identity_mismatch")
    return [_read(path) for path in results]


class _Terminal(Exception):
    pass


def _seal(
    root: Path,
    *,
    status: str,
    reason: str | None = None,
    advance_to: int | None,
    **extra: Any,
) -> dict[str, Any]:
    now = time.time_ns()
    requests, raw, stored = _counts(root)
    stop_path = root / "STOP"
    if not stop_path.exists():
        _create(
            stop_path,
            {
                "schema_version": SCHEMA_VERSION,
                "status": status,
                "reason": reason or status,
                "advance_to": advance_to,
                "time_ns": now,
                **extra,
            },
        )
    stop = _read(stop_path)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "epoch_id": root.name,
        "status": stop["status"],
        "reason": stop.get("reason"),
        "advance_to": stop.get("advance_to"),
        "request_reservations": requests,
        "captured_response_bytes": raw,
        "stored_response_bytes": stored,
        "sealed_ns": stop.get("time_ns", now),
        "stop_sha256": _sha(stop_path),
        **{k: v for k, v in stop.items() if k not in {"status", "reason", "advance_to", "time_ns"}},
        "claim": (
            "provider-bounded structural observation; no global inventory or economic-zero claim"
        ),
    }
    summary_path = root / "epoch-summary.json"
    if summary_path.exists():
        return _read(summary_path)
    _create(summary_path, summary)
    return summary


def _load_summary(root: Path) -> dict[str, Any]:
    summary_path = root / "epoch-summary.json"
    if summary_path.exists():
        return _read(summary_path)
    if (root / "STOP").exists():
        stop = _read(root / "STOP")
        return _seal(
            root,
            status=stop.get("status", "stopped"),
            reason=stop.get("reason"),
            advance_to=stop.get("advance_to"),
            **{
                k: v
                for k, v in stop.items()
                if k not in {"status", "reason", "advance_to", "time_ns"}
            },
        )
    raise ValueError("summary_unavailable")


def _dispatch(
    root: Path,
    plan: dict[str, Any],
    sequence: int,
    operation: dict[str, Any],
    client: Any,
) -> dict[str, Any]:
    request_path = root / "requests" / f"{sequence:012d}.json"
    result_path = root / "request-results" / f"{sequence:012d}.json"
    body = _request_body(operation["method"], operation["params"])
    request_hash = hashlib.sha256(body).hexdigest()
    if result_path.exists():
        reservation = _read(request_path)
        result = _read(result_path)
        if (
            reservation.get("request_sha256") != request_hash
            or reservation.get("provider") != plan["provider"]
            or result.get("provider") != plan["provider"]
            or not _result_identity(result, operation, sequence)
            or reservation.get("phase") != operation["phase"]
        ):
            raise ValueError("journal_operation_identity_mismatch")
        return result
    if request_path.exists():
        _validate_journal(root, plan)
        raise ValueError("ambiguous_reservation_not_sealed")

    requests, raw, stored = _counts(root)
    if requests >= plan["request_budget"]:
        raise _BudgetStop("request_budget_exhausted")
    reserve_bytes = plan["max_response_bytes"]
    if raw + reserve_bytes > plan["raw_byte_budget"]:
        raise _BudgetStop("raw_byte_budget_exhausted")
    if stored + reserve_bytes > plan["stored_byte_budget"]:
        raise _BudgetStop("stored_byte_budget_exhausted")
    if shutil.disk_usage(root).free < plan["minimum_free_disk_bytes"] + reserve_bytes:
        raise _BudgetStop("minimum_free_disk_floor_before_request")
    reservation = {
        "schema_version": SCHEMA_VERSION,
        "sequence": sequence,
        "phase": operation["phase"],
        "provider": plan["provider"],
        "method": operation["method"],
        "params": operation["params"],
        "slot": operation.get("slot"),
        "detail": operation.get("detail"),
        "request_sha256": request_hash,
        "reserved_ns": time.time_ns(),
        "request_reservation": 1,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
        "reserved_response_bytes": reserve_bytes,
        "reserved_stored_response_bytes": reserve_bytes,
        "advance_to": operation.get("advance_to"),
        "policy": "reservation is durable before I/O; no retries",
    }
    _create(request_path, reservation)
    capture: Capture | None = None
    try:
        capture = client.fetch(operation["method"], operation["params"])
        rel = f"captures/{sequence:012d}"
        capture_path = root / rel
        _durable_capture(capture, capture_path, storage_codec=plan["capture_storage_codec"])
        availability_ns = time.time_ns()
        response_name = (
            "response.json.zst" if plan["capture_storage_codec"] == "zstd" else "response.json"
        )
        row: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "sequence": sequence,
            "phase": operation["phase"],
            "provider": plan["provider"],
            "method": operation["method"],
            "slot": operation.get("slot"),
            "detail": operation.get("detail"),
            "status": "captured",
            "capture_path": rel,
            "response_sha256": digest(capture.response),
            "response_bytes": len(capture.response),
            "stored_response_bytes": (capture_path / response_name).stat().st_size,
            "request_started_ns": capture.request_started_ns,
            "receipt_ns": capture.received_ns,
            "availability_ns": availability_ns,
            "event_slot": operation.get("slot"),
        }
        if (
            type(capture.request_started_ns) is not int
            or type(capture.received_ns) is not int
            or capture.request_started_ns > capture.received_ns
            or availability_ns < capture.received_ns
        ):
            row.update(
                {
                    "status": "captured_availability_unknown",
                    "availability_ns": None,
                    "failure_observed_ns": availability_ns,
                    "result_status": "capture_clock_order_invalid",
                }
            )
        else:
            try:
                value = capture.result()
                row["result_status"] = "provider_null" if value is None else "parsed"
                if operation["method"] == "getSlot":
                    row["slot_value"] = value if type(value) is int else None
                elif operation["method"] == "getBlocks":
                    row["inventory_slots"] = value if isinstance(value, list) else None
                    row["inventory_count"] = len(value) if isinstance(value, list) else None
                elif isinstance(value, dict):
                    row["blockhash"] = value.get("blockhash")
                    row["parent_slot"] = value.get("parentSlot")
                    row["block_time_seconds"] = value.get("blockTime")
                    row["transaction_count"] = (
                        len(value["transactions"])
                        if isinstance(value.get("transactions"), list)
                        else None
                    )
                    signatures = value.get("signatures")
                    if isinstance(signatures, list):
                        row["signatures"] = signatures
                else:
                    row["result_status"] = "result_shape_unknown"
            except Exception as exc:
                row["result_status"] = "response_error"
                row["result_error_class"] = type(exc).__name__
    except Exception as exc:
        row = {
            "schema_version": SCHEMA_VERSION,
            "sequence": sequence,
            "phase": operation["phase"],
            "provider": plan["provider"],
            "method": operation["method"],
            "slot": operation.get("slot"),
            "detail": operation.get("detail"),
            "status": "request_failed",
            "error_class": type(exc).__name__,
            "receipt_ns": None,
            "availability_ns": None,
            "failure_observed_ns": time.time_ns(),
        }
        if capture is not None:
            row.update(
                {
                    "status": "received_persistence_failed",
                    "capture_persistence_status": "failed",
                    "response_sha256": digest(capture.response),
                    "response_bytes": len(capture.response),
                    "stored_response_bytes": None,
                    "request_started_ns": capture.request_started_ns,
                    "receipt_ns": capture.received_ns,
                    "availability_ns": None,
                    "failure_observed_ns": time.time_ns(),
                }
            )
        if isinstance(exc, TransportSourceError):
            row["transport_category"] = exc.reason
        if hasattr(exc, "status"):
            row["http_status"] = exc.status
    _create(result_path, row)
    return row


class _BudgetStop(Exception):
    pass


def _parse_inventory(row: dict[str, Any], start: int, end: int) -> list[int] | None:
    slots = row.get("inventory_slots")
    if row.get("status") != "captured" or row.get("result_status") != "parsed":
        return None
    if not isinstance(slots, list) or any(type(slot) is not int for slot in slots):
        return None
    if (
        len(slots) != len(set(slots))
        or any(slot < start or slot >= end for slot in slots)
        or slots != sorted(slots)
    ):
        return None
    return slots


def _build_selection(
    *, plan: dict[str, Any], inventory_slots: list[int], inventory_sha256: str
) -> dict[str, Any]:
    denominator = len(inventory_slots)
    count = min(plan["selection_count"], denominator)
    seed = f"{plan['seed']}:{plan['epoch_id']}:{inventory_sha256}"
    selected = sorted(random.Random(seed).sample(inventory_slots, count)) if count else []
    probability = Fraction(count, denominator) if denominator else None
    return {
        "schema_version": SCHEMA_VERSION,
        "epoch_id": plan["epoch_id"],
        "inventory_sha256": inventory_sha256,
        "inventory_denominator": denominator,
        "selection_count": count,
        "selected_slots": selected,
        "inclusion_probability": (
            f"{probability.numerator}/{probability.denominator}" if probability else None
        ),
        "seed": plan["seed"],
        "selection_rule": plan["selection_rule"],
    }


def run_epoch(
    root: Path,
    plan: dict[str, Any],
    *,
    clients: dict[str, Any],
) -> dict[str, Any]:
    """Run one offline-injected epoch. Live provider construction belongs to another adapter."""
    if not isinstance(clients, dict) or plan.get("provider") not in clients:
        raise ValueError("explicit_offline_client_required")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("requests", "request-results", "captures", "events"):
        (root / name).mkdir(exist_ok=True, mode=0o700)
    plan_path = root / "epoch-plan.json"
    if plan.get("start") != plan.get("next_start"):
        raise ValueError("epoch_start_frontier_mismatch")
    candidate = dict(plan)
    if plan_path.exists():
        existing = _read(plan_path)
        candidate["created_ns"] = existing.get("created_ns")
        if existing != candidate:
            raise ValueError("epoch_plan_changed")
        plan = existing
    else:
        if plan.get("epoch_id") != root.name:
            raise ValueError("epoch_root_identity_mismatch")
        _create(plan_path, plan)
    if (root / "epoch-summary.json").exists() or (root / "STOP").exists():
        return _load_summary(root)

    client = clients[plan["provider"]]
    try:
        _validate_journal(root, plan)
    except _Terminal:
        return _load_summary(root)
    anchor_op = _operations_for(root, plan, None)[0]
    try:
        anchor = _dispatch(root, plan, 1, anchor_op, client)
    except _BudgetStop as exc:
        return _seal(
            root,
            status="anchor_budget_exhausted",
            reason=str(exc),
            advance_to=None,
        )
    if anchor["status"] != "captured" or anchor.get("result_status") != "parsed":
        return _seal(
            root,
            status="anchor_failed",
            reason="finalized_anchor_unknown",
            advance_to=None,
        )
    anchor_slot = anchor.get("slot_value")
    if type(anchor_slot) is not int or anchor_slot < 1:
        return _seal(
            root,
            status="anchor_failed",
            reason="finalized_anchor_shape_unknown",
            advance_to=None,
        )
    if plan["bootstrap"]:
        return _seal(
            root,
            status="bootstrap_complete",
            advance_to=anchor_slot + 1,
            bootstrap_slot=anchor_slot,
            bootstrap_receipt_ns=anchor["receipt_ns"],
        )

    start = plan["next_start"]
    end = anchor_slot + 1
    if end < start:
        return _seal(
            root,
            status="anchor_regression",
            reason="finalized_anchor_below_committed_frontier",
            advance_to=None,
            start=start,
            anchor_slot=anchor_slot,
            end=end,
        )
    width = end - start
    if width > plan["frame_width_cap"]:
        frame = {
            "schema_version": SCHEMA_VERSION,
            "epoch_id": plan["epoch_id"],
            "status": "oversized_frame",
            "start": start,
            "end_exclusive": end,
            "width": width,
            "width_cap": plan["frame_width_cap"],
            "anchor_slot": anchor_slot,
            "anchor_receipt_ns": anchor["receipt_ns"],
        }
        if not (root / "frame.json").exists():
            _create(root / "frame.json", frame)
        elif _read(root / "frame.json") != frame:
            raise ValueError("pinned_frame_changed")
        return _seal(
            root,
            status="oversized_frame",
            reason="frame_exceeds_pinned_width_cap",
            advance_to=end,
            start=start,
            end_exclusive=end,
            width=width,
            width_cap=plan["frame_width_cap"],
        )
    frame_path = root / "frame.json"
    frame = {
        "schema_version": SCHEMA_VERSION,
        "epoch_id": plan["epoch_id"],
        "status": "empty_frame" if width == 0 else "captured",
        "start": start,
        "end_exclusive": end,
        "width": width,
        "width_cap": plan["frame_width_cap"],
        "anchor_slot": anchor_slot,
        "anchor_receipt_ns": anchor["receipt_ns"],
        "anchor_event_slot": anchor_slot,
        "claim": (
            "numeric frame from persisted frontier and finalized anchor; not an inventory claim"
        ),
    }
    if frame_path.exists():
        if _read(frame_path) != frame:
            raise ValueError("pinned_frame_changed")
    else:
        _create(frame_path, frame)
    if width == 0:
        return _seal(
            root,
            status="empty_frame",
            reason="frame_end_equals_frontier",
            advance_to=end,
            start=start,
            end_exclusive=end,
            width=0,
        )

    inventory_op = {
        "phase": "inventory",
        "method": "getBlocks",
        "params": [start, end - 1, {"commitment": "finalized"}],
        "advance_to": end,
    }
    try:
        inventory = _dispatch(root, plan, 2, inventory_op, client)
    except _BudgetStop as exc:
        return _seal(
            root,
            status="inventory_unknown",
            reason=str(exc),
            advance_to=end,
            start=start,
            end_exclusive=end,
        )
    inventory = _read(root / "request-results" / "000000000002.json")
    if inventory["status"] != "captured" or inventory.get("result_status") != "parsed":
        return _seal(
            root,
            status="inventory_unknown",
            reason="getblocks_request_failed_or_unparsed",
            advance_to=end,
            start=start,
            end_exclusive=end,
        )
    inventory_slots = _parse_inventory(inventory, start, end)
    if inventory_slots is None:
        return _seal(
            root,
            status="inventory_unknown",
            reason="getblocks_inventory_shape_or_range_invalid",
            advance_to=end,
            start=start,
            end_exclusive=end,
        )
    inventory_hash = inventory["response_sha256"]
    expected_selection = _build_selection(
        plan=plan, inventory_slots=inventory_slots, inventory_sha256=inventory_hash
    )
    selection_path = root / "selection-plan.json"
    if selection_path.exists():
        selection = _read(selection_path)
        if selection != expected_selection:
            raise ValueError("selection_plan_recomputation_mismatch")
    else:
        selection = expected_selection
        _create(selection_path, selection)

    full_ops = _operations_for(root, plan, selection)[2:]
    outcomes: list[dict[str, Any]] = []
    for offset, op in enumerate(full_ops, start=3):
        op["advance_to"] = end
        try:
            row = _dispatch(root, plan, offset, op, client)
        except _BudgetStop as exc:
            attempted_slots = {row.get("slot") for row in outcomes}
            failed_rows = [
                row
                for row in outcomes
                if row.get("status") != "captured" or row.get("result_status") != "parsed"
            ]
            failed_slots = {row.get("slot") for row in failed_rows}
            selected_slots = selection["selected_slots"]
            return _seal(
                root,
                status="body_budget_exhausted",
                reason=str(exc),
                advance_to=end,
                start=start,
                end_exclusive=end,
                selected_denominator=selection["inventory_denominator"],
                selected_count=selection["selection_count"],
                selected_slots=selected_slots,
                planned_body_request_count=len(full_ops),
                attempted_body_request_count=len(outcomes),
                failed_body_request_count=len(failed_rows),
                not_attempted_body_request_count=len(full_ops) - len(outcomes),
                selected_slot_attempted_count=len(attempted_slots),
                selected_slot_failed_count=len(failed_slots),
                selected_slot_not_attempted_count=len(set(selected_slots) - attempted_slots),
            )
        outcomes.append(row)
    # Structural self-checks remain explicit request outcomes, never population truth.
    by_slot: dict[int, dict[str, dict[str, Any]]] = {}
    for row in outcomes:
        by_slot.setdefault(row["slot"], {})[row["detail"]] = row
    checks: list[dict[str, Any]] = []
    for slot in selection["selected_slots"]:
        full = by_slot.get(slot, {}).get("full", {})
        signatures = by_slot.get(slot, {}).get("signatures", {})
        full_value = _capture_result(root, full) if full else None
        signature_value = _capture_result(root, signatures) if signatures else None
        full_sigs = _signature_list(full_value, "full")
        sig_sigs = _signature_list(signature_value, "signatures")
        validation_failures = _structural_response_validation(full_value, slot)
        structural = (
            _pump_invocations(full_value)
            if isinstance(full_value, dict) and not validation_failures
            else None
        )
        block_identity_equal = (
            isinstance(full_value, dict)
            and isinstance(signature_value, dict)
            and full_value.get("blockhash") == signature_value.get("blockhash")
            and full_value.get("parentSlot") == signature_value.get("parentSlot")
            and full_value.get("previousBlockhash") == signature_value.get("previousBlockhash")
        )
        check_passed = (
            structural is not None
            and structural.get("unresolved_instruction_shapes") == 0
            and not structural.get("compiled_transaction_normalization_failures")
            and full.get("status") == "captured"
            and full.get("result_status") == "parsed"
            and signatures.get("status") == "captured"
            and signatures.get("result_status") == "parsed"
            and full_sigs is not None
            and sig_sigs is not None
            and full_sigs == sig_sigs
            and block_identity_equal
        )
        checks.append(
            {
                "slot": slot,
                "status": (
                    "qualified_structural_reference_count"
                    if check_passed
                    else "self_check_failed_or_unknown"
                ),
                "full_response_validation_failures": validation_failures,
                "structural_references": structural,
                "block_identity_equal": block_identity_equal,
                "signature_order_equal": (
                    full_sigs == sig_sigs
                    if full_sigs is not None and sig_sigs is not None
                    else None
                ),
                "full_receipt_ns": full.get("receipt_ns"),
                "signature_receipt_ns": signatures.get("receipt_ns"),
                "event_slot": slot,
                "block_time_seconds": full.get("block_time_seconds"),
            }
        )
    has_gaps = any(row["status"] != "qualified_structural_reference_count" for row in checks)
    result = _seal(
        root,
        status="sampled_gaps" if has_gaps else "frame_complete",
        advance_to=end,
        start=start,
        end_exclusive=end,
        inventory_sha256=inventory_hash,
        inventory_denominator=len(inventory_slots),
        selected_slots=selection["selected_slots"],
        inclusion_probability=selection["inclusion_probability"],
        structural_checks=checks,
        per_slot_outcomes=[
            {
                "slot": slot,
                "full_status": by_slot.get(slot, {}).get("full", {}).get("status", "not_attempted"),
                "full_result_status": by_slot.get(slot, {}).get("full", {}).get("result_status"),
                "signature_status": by_slot.get(slot, {})
                .get("signatures", {})
                .get("status", "not_attempted"),
                "signature_result_status": by_slot.get(slot, {})
                .get("signatures", {})
                .get("result_status"),
            }
            for slot in selection["selected_slots"]
        ],
    )
    return result
