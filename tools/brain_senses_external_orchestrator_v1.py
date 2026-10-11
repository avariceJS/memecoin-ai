#!/usr/bin/env python3
"""Finite bootstrap plus one inventory-first frame on external storage v1.

This version never calls mutating v1 campaign/service APIs. It shares only the
frozen in-memory selection and structural-validation helpers.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from memecoin_ai.sources import HTTPSourceError, ReadOnlyRPC, TransportSourceError
from memecoin_research_storage import StorageError
from tools import brain_senses_block_epoch_v2 as epoch_v2
from tools import brain_senses_external_clock_v1 as external_clock
from tools import brain_senses_external_schema_v1 as schema
from tools.brain_senses_block_provider_pilot import (
    MAX_RESPONSE_BYTES,
    _signature_list,
    _structural_response_validation,
)
from tools.brain_senses_external_verify_v1 import (
    DescriptorReader,
    verify_bundle,
    verify_plan,
)
from tools.brain_senses_external_writer_v1 import (
    DescriptorWriter,
    canonical_json,
    semantic_request_sha256,
    sha256,
    wire_request_sha256,
    write_capture,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAFE_ORIGINS = {"https://mainnet.helius-rpc.com", "https://rpc.helius.xyz"}
DISK_FLOOR_BYTES = 50 * 1024**3
MAX_FRAME_WIDTH = 20_000
MAX_REQUESTS_TOTAL = 35
MAX_BOOTSTRAP_RESPONSE_BYTES = MAX_RESPONSE_BYTES
MAX_FRAME_BYTES = 256_000_000
MAX_TOTAL_BYTES = MAX_BOOTSTRAP_RESPONSE_BYTES + MAX_FRAME_BYTES
PEAK_WRITE_MARGIN_BYTES = 290_000_000
SELECTION_RULE = "uniform_without_replacement_from_finalized_getblocks_members"


def _sha_file(path: str) -> str:
    with open(os.path.join(REPO_ROOT, path), "rb") as stream:
        return sha256(stream.read())


def source_bindings() -> dict[str, str]:
    paths = (
        "tools/brain_senses_external_writer_v1.py",
        "tools/brain_senses_external_orchestrator_v1.py",
        "tools/brain_senses_external_schema_v1.py",
        "tools/brain_senses_external_verify_v1.py",
        "tools/brain_senses_external_clock_v1.py",
        "tools/brain_senses_external_launch_v1.py",
        "tools/brain_senses_block_epoch_v2.py",
        "tools/brain_senses_block_provider_pilot.py",
        "tools/brain_senses_program_watch.py",
        "tools/brain_senses_service_v1_runner.py",
        "tools/brain_senses_block_epoch_v3_schedule.py",
        "tools/brain_senses_service_v1.py",
        "src/memecoin_ai/sources.py",
        "src/memecoin_ai/domain.py",
        "src/memecoin_ai/storage.py",
        "src/memecoin_collector/private_config.py",
        "src/memecoin_research_storage/__init__.py",
        "src/memecoin_pumpswap/normalize.py",
        "src/memecoin_pumpswap/schema.py",
    )
    return {path: _sha_file(path) for path in paths}


def build_plan(
    *,
    service_id: str,
    root_id: str,
    volume_uuid: str,
    relative_root: str,
    predecessor: dict[str, Any],
    clock_preflight_ns: int,
    bootstrap_start_ns: int,
    frame_start_ns: int,
    expires_ns: int,
    endpoint_origin: str,
) -> dict[str, Any]:
    if endpoint_origin not in SAFE_ORIGINS:
        raise StorageError("external_writer_endpoint_origin_not_allowlisted")
    if (
        type(clock_preflight_ns) is not int
        or type(bootstrap_start_ns) is not int
        or type(frame_start_ns) is not int
        or type(expires_ns) is not int
        or bootstrap_start_ns < clock_preflight_ns + 60_000_000_000
        or not 300_000_000_000 <= frame_start_ns - bootstrap_start_ns <= 600_000_000_000
    ):
        raise StorageError("external_writer_schedule_invalid")
    bootstrap_end = bootstrap_start_ns + 60_000_000_000
    frame_end = frame_start_ns + 180_000_000_000
    if expires_ns != frame_end:
        raise StorageError("external_writer_schedule_expiry_must_match_frame_end")
    plan = {
        "schema": schema.PLAN_SCHEMA,
        "schema_version": 1,
        "service_id": service_id,
        "provider": "helius-free",
        "endpoint_origin": endpoint_origin,
        "storage": {
            "root_id": root_id,
            "volume_uuid": volume_uuid,
            "relative_root": relative_root,
        },
        "code_sha256": source_bindings(),
        "predecessor": predecessor,
        "schedule": {
            "clock_preflight_ns": clock_preflight_ns,
            "bootstrap": {
                "bucket": _bucket_name(bootstrap_start_ns),
                "start_ns": bootstrap_start_ns,
                "end_ns": bootstrap_end,
            },
            "frame": {
                "bucket": _bucket_name(frame_start_ns),
                "start_ns": frame_start_ns,
                "end_ns": frame_end,
            },
            "expires_ns": expires_ns,
        },
        "caps": {
            "max_attempts": 2,
            "max_requests_total": MAX_REQUESTS_TOTAL,
            "max_requests_per_utc_date": MAX_REQUESTS_TOTAL,
            "bootstrap_requests": 1,
            "frame_requests": 34,
            "max_frame_width": MAX_FRAME_WIDTH,
            "selection_count": 16,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "bootstrap_raw_bytes": MAX_BOOTSTRAP_RESPONSE_BYTES,
            "bootstrap_stored_bytes": MAX_BOOTSTRAP_RESPONSE_BYTES,
            "frame_raw_bytes": MAX_FRAME_BYTES,
            "frame_stored_bytes": MAX_FRAME_BYTES,
            "raw_bytes_total": MAX_TOTAL_BYTES,
            "stored_bytes_total": MAX_TOTAL_BYTES,
            "raw_bytes_per_utc_date": MAX_TOTAL_BYTES,
            "stored_bytes_per_utc_date": MAX_TOTAL_BYTES,
            "disk_floor_bytes": DISK_FLOOR_BYTES,
            "peak_write_margin_bytes": PEAK_WRITE_MARGIN_BYTES,
            "storage_codec": "raw",
        },
    }
    verify_plan(plan, expected_sources=source_bindings())
    return plan


def _bucket_name(start_ns: int) -> str:
    return datetime.fromtimestamp(start_ns / 1_000_000_000, UTC).strftime("%Y%m%dT%H%M00Z")


def plan_digest(plan: Mapping[str, Any]) -> str:
    return sha256(canonical_json(dict(plan)))


def _read_events(writer: DescriptorWriter) -> list[dict[str, Any]]:
    if not writer.exists(schema.EVENTS_FILE):
        return []
    payload = writer.read_bytes(schema.EVENTS_FILE)
    if not payload.endswith(b"\n"):
        raise StorageError("external_writer_event_tail_incomplete")
    events: list[dict[str, Any]] = []
    previous = "0" * 64
    last_at = 0
    for line in payload.splitlines():
        value = json.loads(line)
        if not isinstance(value, dict) or value.get("schema") != schema.EVENT_SCHEMA:
            raise StorageError("external_writer_event_invalid")
        digest = value.get("event_sha256")
        body = {key: item for key, item in value.items() if key != "event_sha256"}
        if (
            value.get("seq") != len(events)
            or value.get("previous_sha256") != previous
            or type(value.get("at_ns")) is not int
            or value["at_ns"] < last_at
            or digest != sha256(canonical_json(body))
        ):
            raise StorageError("external_writer_event_chain_invalid")
        if events and events[-1]["kind"] == "stop":
            raise StorageError("external_writer_event_after_stop")
        events.append(value)
        previous = digest
        last_at = value["at_ns"]
    return events


def append_event(
    writer: DescriptorWriter,
    *,
    kind: str,
    bucket: str | None,
    at_ns: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    events = _read_events(writer)
    if events and events[-1]["kind"] == "stop":
        raise StorageError("external_writer_terminal_stop")
    body = {
        "schema": schema.EVENT_SCHEMA,
        "schema_version": 1,
        "seq": len(events),
        "kind": kind,
        "bucket": bucket,
        "at_ns": at_ns,
        "payload": payload,
        "previous_sha256": events[-1]["event_sha256"] if events else "0" * 64,
    }
    event = {**body, "event_sha256": sha256(canonical_json(body))}
    writer.append(schema.EVENTS_FILE, canonical_json(event))
    return event


def initialize(writer: DescriptorWriter, plan: dict[str, Any]) -> dict[str, Any]:
    with writer.lock():
        return _initialize_locked(writer, plan)


def _initialize_locked(writer: DescriptorWriter, plan: dict[str, Any]) -> dict[str, Any]:
    verify_plan(plan, expected_sources=source_bindings())
    if writer.exists(schema.PLAN_FILE) or writer.exists(schema.EVENTS_FILE):
        raise StorageError("external_writer_already_initialized")
    plan_bytes = canonical_json(plan)
    writer.atomic_write(schema.PLAN_FILE, plan_bytes, create_only=True)
    at_ns = time.time_ns()
    event = append_event(
        writer,
        kind="initialized",
        bucket=None,
        at_ns=at_ns,
        payload={"plan_sha256": sha256(plan_bytes)},
    )
    return {"status": "initialized_offline", "plan_sha256": sha256(plan_bytes), "event": event}


def _load_plan(writer: DescriptorWriter) -> tuple[dict[str, Any], str]:
    raw = writer.read_bytes(schema.PLAN_FILE)
    plan = json.loads(raw)
    if canonical_json(plan) != raw:
        raise StorageError("external_writer_plan_not_canonical")
    verify_plan(plan, expected_sources=source_bindings())
    return plan, sha256(raw)


def _stop_record(
    reason: str, bucket: str | None, bundle_sha256: str | None, at_ns: int
) -> dict[str, Any]:
    return {
        "schema": schema.STOP_SCHEMA,
        "schema_version": 1,
        "reason": reason,
        "stopped_ns": at_ns,
        "last_bucket": bucket,
        "bundle_sha256": bundle_sha256,
        "post_stop_requests": 0,
    }


def _publish_stop(
    writer: DescriptorWriter,
    *,
    reason: str,
    bucket: str | None,
    bundle_sha256: str | None = None,
    at_ns: int | None = None,
) -> dict[str, Any]:
    existing_stop: dict[str, Any] | None = None
    if writer.exists(schema.STOP_FILE):
        existing_stop = json.loads(writer.read_bytes(schema.STOP_FILE))
    events = _read_events(writer)
    stop_event = next((event for event in reversed(events) if event["kind"] == "stop"), None)
    if existing_stop is not None:
        stop = existing_stop
        if stop_event is None:
            append_event(
                writer,
                kind="stop",
                bucket=None,
                at_ns=int(stop["stopped_ns"]),
                payload={"stop": stop},
            )
        elif stop_event.get("payload", {}).get("stop") != stop:
            raise StorageError("external_writer_stop_marker_mismatch")
        return stop
    if stop_event is not None:
        stop = stop_event.get("payload", {}).get("stop")
        if not isinstance(stop, dict):
            raise StorageError("external_writer_stop_event_invalid")
    else:
        stopped_ns = at_ns if at_ns is not None else time.time_ns()
        stop = _stop_record(reason, bucket, bundle_sha256, stopped_ns)
        append_event(
            writer,
            kind="stop",
            bucket=None,
            at_ns=stopped_ns,
            payload={"stop": stop},
        )
    writer.atomic_write(schema.STOP_FILE, canonical_json(stop), create_only=True)
    return stop


def _reconcile_terminal_prefix(writer: DescriptorWriter) -> dict[str, Any] | None:
    if writer.exists(schema.STOP_FILE):
        return _publish_stop(
            writer,
            reason="existing_stop_marker",
            bucket=None,
        )
    events = _read_events(writer)
    if events and events[-1]["kind"] == "stop":
        return _publish_stop(writer, reason="existing_stop_event", bucket=events[-1].get("bucket"))
    return None


def _schedule(plan: dict[str, Any], bucket: str) -> tuple[str, dict[str, int]]:
    for phase in ("bootstrap", "frame"):
        window = plan["schedule"][phase]
        if window["bucket"] == bucket:
            return phase, window
    raise StorageError("external_writer_bucket_not_in_plan")


def _check_dispatch_time(plan: dict[str, Any], window: dict[str, int], now_ns: int) -> str:
    if now_ns < window["start_ns"] + 5_000_000_000:
        return "too_early"
    if now_ns > window["end_ns"] - 5_000_000_000:
        return "window_closing" if now_ns < window["end_ns"] else "missed_bucket"
    if now_ns > plan["schedule"]["expires_ns"]:
        return "missed_bucket"
    return "eligible"


def _raw_response_bytes(captures: list[dict[str, Any]]) -> int:
    return sum(int(item["response_bytes"]) for item in captures)


def _next_request(
    writer: DescriptorWriter,
    *,
    plan: dict[str, Any],
    plan_hash: str,
    bucket: str,
    phase: str,
    reservation_id: str,
    ordinal: int,
    method: str,
    params: list[Any],
    client: ReadOnlyRPC,
    decision_ns: int,
    existing_bytes: int,
    reservation: dict[str, Any],
    guard: Any | None,
    window: dict[str, int],
    clock_record: dict[str, Any],
    clock_path: str,
    clock_hash: str,
) -> tuple[dict[str, Any], Any | None, dict[str, Any] | None]:
    wall_ns = time.time_ns()
    mono_ns = time.monotonic_ns()
    if not window["start_ns"] + 5_000_000_000 <= wall_ns <= window["end_ns"] - 5_000_000_000:
        raise StorageError("external_writer_bucket_expired")
    try:
        external_clock.validate_progress(clock_record, wall_ns=wall_ns, monotonic_ns=mono_ns)
    except external_clock.ClockError as exc:
        raise StorageError("external_writer_clock_progress_invalid") from exc
    if existing_bytes + plan["caps"]["max_response_bytes"] > reservation["raw_byte_cap"]:
        raise StorageError("external_writer_response_cap_exhausted")
    if guard is not None:
        guard.revalidate(
            min_free_bytes=(
                plan["caps"]["disk_floor_bytes"]
                + plan["caps"]["stored_bytes_total"]
                + plan["caps"]["peak_write_margin_bytes"]
            )
        )
    request_id = f"{bucket}-{ordinal:02d}"
    started_ns = wall_ns
    semantic_hash = semantic_request_sha256(method, params)
    wire_hash = wire_request_sha256(method, params)
    request = {
        "schema": schema.REQUEST_SCHEMA,
        "schema_version": 1,
        "provider": plan["provider"],
        "reservation_id": reservation_id,
        "request_id": request_id,
        "ordinal": ordinal,
        "method": method,
        "params": params,
        "request_sha256": semantic_hash,
        "wire_request_sha256": wire_hash,
        "request_started_ns": started_ns,
        "decision_ns": decision_ns,
        "clock_check_path": clock_path,
        "clock_check_sha256": clock_hash,
        "monotonic_ns": mono_ns,
    }
    request_path = f"buckets/{bucket}/requests/{ordinal:02d}.json"
    writer.atomic_write(request_path, canonical_json(request), create_only=True)
    # The intent is now spent. Recheck timing and descriptor-bound storage
    # immediately before dispatch; failure is terminal and never replayed.
    wall_ns = time.time_ns()
    mono_ns = time.monotonic_ns()
    if not window["start_ns"] + 5_000_000_000 <= wall_ns <= window["end_ns"] - 5_000_000_000:
        raise StorageError("external_writer_bucket_expired_after_intent")
    try:
        external_clock.validate_progress(clock_record, wall_ns=wall_ns, monotonic_ns=mono_ns)
    except external_clock.ClockError as exc:
        raise StorageError("external_writer_clock_progress_invalid_after_intent") from exc
    if guard is not None:
        guard.revalidate()
    try:
        capture = client.fetch(method, params)
    except Exception as exc:
        http_status = exc.status if isinstance(exc, HTTPSourceError) else None
        transport_category = exc.reason if isinstance(exc, TransportSourceError) else None
        result = {
            "schema": schema.RESULT_SCHEMA,
            "schema_version": 1,
            "provider": plan["provider"],
            "request_id": request_id,
            "request_sha256": semantic_hash,
            "wire_request_sha256": wire_hash,
            "receipt_sha256": None,
            "response_sha256": None,
            "status": "ambiguous",
            "result_status": "unknown",
            "method": method,
            "event_slot": params[0] if method == "getBlock" else None,
            "produced_slots": None,
            "signatures": None,
            "block_identity": None,
            "response_bytes": None,
            "stored_response_bytes": None,
            "event_time_ns": None,
            "availability_ns": None,
            "decision_ns": decision_ns,
            "http_status": http_status,
            "transport_category": transport_category,
        }
        writer.atomic_write(
            f"buckets/{bucket}/results/{ordinal:02d}.json",
            canonical_json(result),
            create_only=True,
        )
        return request, None, result
    if (
        not isinstance(capture, object)
        or getattr(capture, "source_alias", None) != plan["provider"]
    ):
        raise StorageError("external_writer_capture_provider_mismatch")
    capture_directory = f"buckets/{bucket}/captures/{ordinal:02d}"
    receipt = write_capture(
        writer,
        capture=capture,
        provider=plan["provider"],
        request_id=request_id,
        method=method,
        params=params,
        request_sha256=semantic_hash,
        wire_request_sha256=wire_hash,
        decision_ns=decision_ns,
        relative_directory=capture_directory,
        storage_codec=plan["caps"]["storage_codec"],
    )
    available_ns = time.time_ns()
    value: Any = None
    result_status = "parsed"
    try:
        value = capture.result()
    except Exception:
        result_status = "provider_error"
    response_hash = receipt["response_sha256"]
    event_slot: int | None = None
    produced_slots: list[int] | None = None
    signatures: list[str] | None = None
    block_identity: dict[str, Any] | None = None
    event_time_ns: int | None = None
    if result_status == "parsed":
        # Capture.result() validates the JSON-RPC envelope, id, and error
        # before exposing a parsed value. It is called below before publication.
        if method == "getSlot":
            if type(value) is not int or value < 1:
                result_status = "invalid_response"
            else:
                event_slot = value
                if method == "getSlot" and phase == "bootstrap":
                    if value + 1 < plan["predecessor"]["committed_frontier"]:
                        result_status = "invalid_response"
        elif method == "getBlocks":
            start, end = params[0], params[1] + 1
            operation_rows = {
                "status": "captured",
                "result_status": "parsed",
                "inventory_slots": value,
            }
            parsed = epoch_v2._parse_inventory(operation_rows, start, end)
            if parsed is None:
                result_status = "invalid_response"
            else:
                produced_slots = parsed
        else:
            slot = params[0]
            detail = params[1]["transactionDetails"]
            if not isinstance(value, dict):
                result_status = "invalid_response"
            else:
                signatures = _signature_list(value, detail)
                if signatures is None:
                    result_status = "invalid_response"
                else:
                    event_slot = slot
                    block_identity = {
                        key: value.get(key)
                        for key in (
                            "blockhash",
                            "previousBlockhash",
                            "parentSlot",
                            "blockTime",
                            "blockHeight",
                        )
                    }
                    event_time = value.get("blockTime")
                    event_time_ns = event_time * 1_000_000_000 if type(event_time) is int else None
                    if detail == "full" and _structural_response_validation(value, slot):
                        result_status = "invalid_response"
    result = {
        "schema": schema.RESULT_SCHEMA,
        "schema_version": 1,
        "provider": plan["provider"],
        "request_id": request_id,
        "request_sha256": semantic_hash,
        "wire_request_sha256": wire_hash,
        "receipt_sha256": sha256(canonical_json(receipt)),
        "response_sha256": response_hash,
        "status": "captured",
        "result_status": result_status,
        "method": method,
        "event_slot": event_slot,
        "produced_slots": produced_slots,
        "signatures": signatures,
        "block_identity": block_identity,
        "response_bytes": receipt["response_bytes"],
        "stored_response_bytes": receipt["storage"]["bytes"],
        "event_time_ns": event_time_ns,
        "availability_ns": available_ns,
        "decision_ns": decision_ns,
        "http_status": None,
        "transport_category": None,
    }
    writer.atomic_write(
        f"buckets/{bucket}/results/{ordinal:02d}.json",
        canonical_json(result),
        create_only=True,
    )
    return request, value, result


def _build_bundle(
    writer: DescriptorWriter,
    *,
    plan: dict[str, Any],
    plan_hash: str,
    phase: str,
    bucket: str,
    reservation: dict[str, Any],
    request_rows: list[dict[str, Any]],
    values: list[Any],
    result_rows: list[dict[str, Any]],
    response_raws: list[bytes],
    guard: Any | None,
) -> dict[str, Any]:
    bootstrap_slot: int | None = None
    frame_bootstrap_slot: int | None = None
    frame: dict[str, Any] | None = None
    selected_blocks: list[dict[str, Any]] = []
    if phase == "bootstrap":
        bootstrap_slot = values[0]
    else:
        bootstrap_bucket = plan["schedule"]["bootstrap"]["bucket"]
        with DescriptorReader(writer.root_fd) as reader:
            prior = verify_bundle(reader, bootstrap_bucket, plan)
        if (
            prior.get("status") != "complete"
            or prior.get("terminal_status") != "bootstrap_complete"
        ):
            raise StorageError("external_writer_bootstrap_bundle_unverified")
        frame_bootstrap_slot = prior.get("bootstrap_slot")
        anchor = values[0]
        if type(frame_bootstrap_slot) is not int:
            raise StorageError("external_writer_bootstrap_slot_invalid")
        start, end = frame_bootstrap_slot + 1, anchor + 1
        inventory = values[1]
        selected = epoch_v2._build_selection(
            plan={
                "seed": plan["service_id"],
                "epoch_id": bucket,
                "selection_count": plan["caps"]["selection_count"],
                "selection_rule": SELECTION_RULE,
            },
            inventory_slots=inventory,
            inventory_sha256=sha256(response_raws[1]),
        )
        frame = {
            "start": start,
            "end_exclusive": end,
            "anchor_slot": anchor,
            "width": end - start,
            "inventory_sha256": selected["inventory_sha256"],
            "denominator": selected["inventory_denominator"],
            "selection_count": selected["selection_count"],
            "selected_slots": selected["selected_slots"],
            "inclusion_probability": selected["inclusion_probability"],
            "seed": selected["seed"],
            "selection_rule": selected["selection_rule"],
        }
        for index, slot in enumerate(frame["selected_slots"]):
            sig_value, full_value = values[2 + index * 2], values[3 + index * 2]
            sigs = _signature_list(sig_value, "signatures")
            full_sigs = _signature_list(full_value, "full")
            if sigs is None or full_sigs != sigs:
                raise StorageError("external_writer_signature_pair_mismatch")
            if any(
                sig_value.get(key) != full_value.get(key)
                for key in (
                    "blockhash",
                    "previousBlockhash",
                    "parentSlot",
                    "blockTime",
                    "blockHeight",
                )
            ):
                raise StorageError("external_writer_block_identity_mismatch")
            selected_blocks.append(
                {
                    "slot": slot,
                    "signatures": full_sigs,
                    "blockhash": full_value.get("blockhash"),
                    "previous_blockhash": full_value.get("previousBlockhash"),
                    "parent_slot": full_value.get("parentSlot"),
                }
            )
    request_entries: list[dict[str, str]] = []
    raw_total = stored_total = 0
    for ordinal, request in enumerate(request_rows):
        receipt_path = f"buckets/{bucket}/captures/{ordinal:02d}/receipt.json"
        result_path = f"buckets/{bucket}/results/{ordinal:02d}.json"
        receipt_raw = writer.read_bytes(receipt_path)
        result_raw = writer.read_bytes(result_path)
        receipt = json.loads(receipt_raw)
        result = json.loads(result_raw)
        request_entries.append(
            {
                "request_id": request["request_id"],
                "request_sha256": request["request_sha256"],
                "receipt_sha256": sha256(receipt_raw),
                "result_sha256": sha256(result_raw),
                "response_sha256": result["response_sha256"],
            }
        )
        raw_total += int(receipt["response_bytes"])
        stored_total += int(receipt["storage"]["bytes"])
    bundle = {
        "schema": schema.BUNDLE_SCHEMA,
        "schema_version": 1,
        "provider": plan["provider"],
        "bucket": bucket,
        "phase": phase,
        "reservation_id": reservation["reservation_id"],
        "plan_sha256": plan_hash,
        "status": "complete",
        "request_count": len(request_rows),
        "requests": request_entries,
        "raw_bytes": raw_total,
        "stored_bytes": stored_total,
        "bootstrap_slot": bootstrap_slot,
        "frame": frame,
        "selected_blocks": selected_blocks,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
        "terminal_status": phase + "_complete",
    }
    return bundle


def _seal_bundle(
    writer: DescriptorWriter,
    *,
    plan: dict[str, Any],
    bucket: str,
    bundle: dict[str, Any],
    now_ns: int,
) -> dict[str, Any]:
    bundle_bytes = canonical_json(bundle)
    bundle_hash = sha256(bundle_bytes)
    bundle_path = f"buckets/{bucket}/bundle.json"
    if writer.exists(bundle_path):
        if writer.read_bytes(bundle_path) != bundle_bytes:
            raise StorageError("external_writer_bundle_recovery_mismatch")
    else:
        writer.atomic_write(bundle_path, bundle_bytes, create_only=True)
    events = _read_events(writer)
    bundle_events = [e for e in events if e["kind"] == "bundle" and e.get("bucket") == bucket]
    if bundle_events:
        if bundle_events[-1]["payload"] != {"bundle_sha256": bundle_hash}:
            raise StorageError("external_writer_bundle_event_mismatch")
    else:
        append_event(
            writer,
            kind="bundle",
            bucket=bucket,
            at_ns=now_ns,
            payload={"bundle_sha256": bundle_hash},
        )
    with DescriptorReader(writer.root_fd) as reader:
        verified = verify_bundle(reader, bucket, plan)
    if verified.get("bundle_sha256") != bundle_hash or verified.get("status") != "complete":
        raise StorageError("external_writer_independent_bundle_verification_failed")
    seal = {
        "schema": schema.SEAL_SCHEMA,
        "schema_version": 1,
        "bucket": bucket,
        "reservation_id": bundle["reservation_id"],
        "plan_sha256": bundle["plan_sha256"],
        "bundle_sha256": bundle_hash,
        "sealed_ns": time.time_ns(),
        "status": "complete",
    }
    seal_hash = sha256(canonical_json(seal))
    seal_path = f"buckets/{bucket}/seal.json"
    if writer.exists(seal_path):
        existing_seal = writer.read_bytes(seal_path)
        seal = json.loads(existing_seal)
        seal_hash = sha256(existing_seal)
        if (
            seal.get("bucket") != bucket
            or seal.get("reservation_id") != bundle["reservation_id"]
            or seal.get("plan_sha256") != bundle["plan_sha256"]
            or seal.get("bundle_sha256") != bundle_hash
            or seal.get("status") != "complete"
        ):
            raise StorageError("external_writer_seal_file_mismatch")
    else:
        events = _read_events(writer)
        seal_events = [e for e in events if e["kind"] == "seal" and e.get("bucket") == bucket]
        if seal_events:
            raise StorageError("external_writer_seal_event_without_file")
        writer.atomic_write(seal_path, canonical_json(seal), create_only=True)
    events = _read_events(writer)
    seal_events = [e for e in events if e["kind"] == "seal" and e.get("bucket") == bucket]
    if not seal_events:
        append_event(
            writer,
            kind="seal",
            bucket=bucket,
            at_ns=seal["sealed_ns"],
            payload={"seal_sha256": seal_hash},
        )
    elif seal_events[-1]["payload"] != {"seal_sha256": seal_hash}:
        raise StorageError("external_writer_seal_event_mismatch")
    return {"status": "complete", "bundle": bundle, "seal": seal}


def _run_bucket_locked(
    writer: DescriptorWriter,
    *,
    bucket: str,
    client_loader: Callable[[], ReadOnlyRPC],
    guard: Any | None = None,
    now_ns: int | None = None,
    clock_probe: Callable[[], dict[str, Any]] = external_clock.probe_pair,
) -> dict[str, Any]:
    """Run exactly one scheduled bucket; any durable request intent forbids replay."""
    terminal = _reconcile_terminal_prefix(writer)
    if terminal is not None:
        return {"status": "stopped", "stop": terminal, "provider_io": False}
    plan, plan_hash = _load_plan(writer)
    phase, window = _schedule(plan, bucket)
    current = time.time_ns() if now_ns is None else now_ns
    base = f"buckets/{bucket}"
    # Any bucket artifact or event means this identity was already spent or its
    # publication boundary crashed. Reconcile local data only; never dispatch.
    if any(path.startswith(base + "/") for path in writer.list_files()):
        try:
            with DescriptorReader(writer.root_fd) as reader:
                verify_bundle(reader, bucket, plan)
            stored_bundle = json.loads(writer.read_bytes(base + "/bundle.json"))
            result = _seal_bundle(
                writer,
                plan=plan,
                bucket=bucket,
                bundle=stored_bundle,
                now_ns=time.time_ns(),
            )
            return {**result, "provider_io": False}
        except Exception:
            stop = _publish_stop(
                writer, reason="interrupted_no_replay", bucket=bucket, at_ns=current
            )
            return {"status": "stopped", "stop": stop, "provider_io": False}
    events = _read_events(writer)
    if any(event.get("bucket") == bucket for event in events):
        stop = _publish_stop(writer, reason="interrupted_no_replay", bucket=bucket, at_ns=current)
        return {"status": "stopped", "stop": stop, "provider_io": False}
    clock_path = f"clock/{bucket}-00.json"
    if writer.exists(clock_path):
        stop = _publish_stop(writer, reason="interrupted_no_replay", bucket=bucket, at_ns=current)
        return {"status": "stopped", "stop": stop, "provider_io": False}
    dispatch = _check_dispatch_time(plan, window, current)
    if dispatch == "too_early":
        return {"status": "not_due", "provider_io": False}
    if dispatch == "window_closing":
        return {"status": "not_due", "provider_io": False}
    if dispatch != "eligible":
        append_event(
            writer,
            kind="gap",
            bucket=bucket,
            at_ns=max(current, window["end_ns"]),
            payload={"reason": dispatch, "window": window},
        )
        stop = _publish_stop(
            writer, reason=dispatch, bucket=bucket, at_ns=max(current, window["end_ns"])
        )
        return {"status": "stopped", "stop": stop, "provider_io": False}
    if phase == "frame":
        boot = f"buckets/{plan['schedule']['bootstrap']['bucket']}/seal.json"
        if not writer.exists(boot):
            stop = _publish_stop(
                writer, reason="bootstrap_not_sealed", bucket=bucket, at_ns=current
            )
            return {"status": "stopped", "stop": stop, "provider_io": False}
    try:
        clock_record = clock_probe()
        external_clock.validate_clock(clock_record, now_ns=time.time_ns())
    except (external_clock.ClockError, OSError):
        stop = _publish_stop(
            writer, reason="clock_check_failed", bucket=bucket, at_ns=time.time_ns()
        )
        return {"status": "stopped", "stop": stop, "provider_io": False}
    clock_bytes = canonical_json(clock_record)
    clock_hash = sha256(clock_bytes)
    try:
        writer.atomic_write(clock_path, clock_bytes, create_only=True)
    except Exception:
        stop = _publish_stop(
            writer, reason="clock_persistence_failed", bucket=bucket, at_ns=time.time_ns()
        )
        return {"status": "stopped", "stop": stop, "provider_io": False}
    current = time.time_ns()
    if _check_dispatch_time(plan, window, current) != "eligible":
        stop = _publish_stop(
            writer, reason="clock_check_outside_bucket", bucket=bucket, at_ns=current
        )
        return {"status": "stopped", "stop": stop, "provider_io": False}
    caps = plan["caps"]
    request_count = 1 if phase == "bootstrap" else caps["frame_requests"]
    byte_cap = caps["bootstrap_raw_bytes"] if phase == "bootstrap" else caps["frame_raw_bytes"]
    stored_cap = (
        caps["bootstrap_stored_bytes"] if phase == "bootstrap" else caps["frame_stored_bytes"]
    )
    reservation_id = sha256(f"{plan_hash}:{bucket}:{phase}".encode())[:32]
    reservation = {
        "schema": schema.BUCKET_SCHEMA,
        "schema_version": 1,
        "service_id": plan["service_id"],
        "bucket": bucket,
        "phase": phase,
        "reservation_id": reservation_id,
        "plan_sha256": plan_hash,
        "request_count": request_count,
        "raw_byte_cap": byte_cap,
        "stored_byte_cap": stored_cap,
        "created_ns": current,
        "not_before_ns": window["start_ns"],
        "expires_ns": window["end_ns"],
        "predecessor_event_sha256": events[-1]["event_sha256"] if events else "0" * 64,
        "clock_check_path": clock_path,
        "clock_check_sha256": clock_hash,
    }
    reservation_hash = sha256(canonical_json(reservation))
    append_event(
        writer,
        kind="reservation",
        bucket=bucket,
        at_ns=current,
        payload={"reservation_sha256": reservation_hash},
    )
    writer.mkdir(base)
    writer.atomic_write(base + "/reservation.json", canonical_json(reservation), create_only=True)
    try:
        if guard is not None:
            guard.revalidate(
                min_free_bytes=(
                    caps["disk_floor_bytes"]
                    + caps["stored_bytes_total"]
                    + caps["peak_write_margin_bytes"]
                )
            )
        client = client_loader()
    except Exception:
        stop = _publish_stop(
            writer, reason="provider_client_load_failed", bucket=bucket, at_ns=time.time_ns()
        )
        return {"status": "stopped", "stop": stop, "provider_io": False}
    started_ns = time.time_ns()
    if _check_dispatch_time(plan, window, started_ns) != "eligible":
        stop = _publish_stop(
            writer, reason="bucket_expired_before_start", bucket=bucket, at_ns=started_ns
        )
        return {"status": "stopped", "stop": stop, "provider_io": False}
    if (
        not isinstance(client, ReadOnlyRPC)
        or client.source_alias != plan["provider"]
        or client.endpoint_origin not in SAFE_ORIGINS
        or client.endpoint_origin != plan["endpoint_origin"]
        or client.max_bytes > caps["max_response_bytes"]
    ):
        stop = _publish_stop(
            writer, reason="provider_client_binding_invalid", bucket=bucket, at_ns=started_ns
        )
        return {"status": "stopped", "stop": stop, "provider_io": False}
    started = {
        "schema": schema.BUCKET_SCHEMA,
        "schema_version": 1,
        "reservation_id": reservation_id,
        "plan_sha256": plan_hash,
        "started_ns": started_ns,
        "pid": os.getpid(),
    }
    started_hash = sha256(canonical_json(started))
    append_event(
        writer,
        kind="started",
        bucket=bucket,
        at_ns=started_ns,
        payload={"started_sha256": started_hash},
    )
    writer.atomic_write(base + "/started.json", canonical_json(started), create_only=True)
    if phase == "bootstrap":
        operations = [("getSlot", [{"commitment": "finalized"}])]
    else:
        boot_bucket = plan["schedule"]["bootstrap"]["bucket"]
        with DescriptorReader(writer.root_fd) as reader:
            bootstrap = verify_bundle(reader, boot_bucket, plan)
        bootstrap_slot = bootstrap.get("bootstrap_slot")
        operations = [("getSlot", [{"commitment": "finalized"}])]
        # getBlocks and selected block requests are appended after the new anchor.
        operations.append(("__frame_dependent__", [bootstrap_slot]))
    request_rows: list[dict[str, Any]] = []
    result_rows: list[dict[str, Any]] = []
    values: list[Any] = []
    raws: list[bytes] = []
    failure_reason: str | None = None
    try:
        ordinal = 0
        while ordinal < len(operations):
            method, params = operations[ordinal]
            if method == "__frame_dependent__":
                if not values or type(values[0]) is not int:
                    raise StorageError("external_writer_invalid_frame_anchor")
                start = params[0] + 1
                anchor = values[0]
                if anchor < start:
                    raise StorageError("external_writer_anchor_regression")
                if anchor + 1 - start > caps["max_frame_width"]:
                    raise StorageError("external_writer_oversized_frame")
                operations[ordinal] = (
                    "getBlocks",
                    [start, anchor, {"commitment": "finalized"}],
                )
                method, params = operations[ordinal]
            decision_ns = time.time_ns()
            request, value, result = _next_request(
                writer,
                plan=plan,
                plan_hash=plan_hash,
                bucket=bucket,
                phase=phase,
                reservation_id=reservation_id,
                ordinal=ordinal,
                method=method,
                params=params,
                client=client,
                decision_ns=decision_ns,
                existing_bytes=_raw_response_bytes(
                    [row for row in result_rows if row.get("status") == "captured"]
                ),
                reservation=reservation,
                guard=guard,
                window=window,
                clock_record=clock_record,
                clock_path=clock_path,
                clock_hash=clock_hash,
            )
            request_rows.append(request)
            result_rows.append(result)
            if result.get("status") != "captured" or result.get("result_status") != "parsed":
                if result.get("http_status") == 429:
                    failure_reason = "provider_http_429"
                elif result.get("http_status") in {402, 403}:
                    failure_reason = "provider_quota_or_billing_error"
                elif result.get("transport_category") is not None:
                    failure_reason = "provider_transport_failure"
                else:
                    failure_reason = "provider_or_parse_failure"
                raise StorageError("external_writer_provider_or_parse_failure")
            capture_raw = writer.read_bytes(
                f"{base}/captures/{ordinal:02d}/response.json", max_bytes=caps["max_response_bytes"]
            )
            raws.append(capture_raw)
            values.append(value)
            if method == "getBlocks":
                inventory_sha = sha256(capture_raw)
                selected = epoch_v2._build_selection(
                    plan={
                        "seed": plan["service_id"],
                        "epoch_id": bucket,
                        "selection_count": caps["selection_count"],
                        "selection_rule": SELECTION_RULE,
                    },
                    inventory_slots=value,
                    inventory_sha256=inventory_sha,
                )
                for slot in selected["selected_slots"]:
                    operations.extend(
                        [
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
                            ),
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
                            ),
                        ]
                    )
            ordinal += 1
    except Exception as exc:
        reason = "provider_or_persistence_failure"
        if failure_reason is not None:
            reason = failure_reason
        elif isinstance(exc, StorageError):
            message = str(exc)
            if message in {
                "external_writer_oversized_frame",
                "external_writer_anchor_regression",
            }:
                reason = message
            elif "cap_exhausted" in message:
                reason = "byte_cap_exhausted"
            elif "bucket_expired" in message:
                reason = "bucket_expired"
            else:
                reason = "local_uncertainty"
        stop = _publish_stop(
            writer,
            reason=reason,
            bucket=bucket,
            at_ns=time.time_ns(),
        )
        return {"status": "stopped", "stop": stop, "provider_io": bool(request_rows)}
    if phase == "bootstrap" and (type(values[0]) is not int or values[0] < 1):
        stop = _publish_stop(writer, reason="invalid_bootstrap_anchor", bucket=bucket)
        return {"status": "stopped", "stop": stop, "provider_io": True}
    if phase == "frame":
        with DescriptorReader(writer.root_fd) as reader:
            bootstrap = verify_bundle(reader, plan["schedule"]["bootstrap"]["bucket"], plan)
        if values[0] < bootstrap["bootstrap_slot"] + 1:
            stop = _publish_stop(writer, reason="anchor_regression", bucket=bucket)
            return {"status": "stopped", "stop": stop, "provider_io": True}
    bundle = _build_bundle(
        writer,
        plan=plan,
        plan_hash=plan_hash,
        phase=phase,
        bucket=bucket,
        reservation=reservation,
        request_rows=request_rows,
        values=values,
        result_rows=result_rows,
        response_raws=raws,
        guard=guard,
    )
    result = _seal_bundle(writer, plan=plan, bucket=bucket, bundle=bundle, now_ns=time.time_ns())
    return {**result, "provider_io": True}


def run_bucket(
    writer: DescriptorWriter,
    *,
    bucket: str,
    client_loader: Callable[[], ReadOnlyRPC],
    guard: Any | None = None,
    now_ns: int | None = None,
    clock_probe: Callable[[], dict[str, Any]] = external_clock.probe_pair,
) -> dict[str, Any]:
    """Serialize one bucket for this service root and reconcile without replay."""
    with writer.lock():
        return _run_bucket_locked(
            writer,
            bucket=bucket,
            client_loader=client_loader,
            guard=guard,
            now_ns=now_ns,
            clock_probe=clock_probe,
        )


def reader_closed_file_exists(writer: DescriptorWriter, relative: str) -> bool:
    try:
        writer.stat(relative)
        return True
    except FileNotFoundError:
        return False
