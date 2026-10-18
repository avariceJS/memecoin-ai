"""Finite, descriptor-bound external Senses successor lease v2."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tools import brain_senses_external_schema_v2 as schema
from tools.brain_senses_external_writer_v1 import DescriptorWriter

_SERVICE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_MINUTE_NS = 60_000_000_000
_SECOND_NS = 1_000_000_000


class ExternalV2Error(RuntimeError):
    """A bounded, sanitized lease control error."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def bucket_name(start_ns: int) -> str:
    return datetime.fromtimestamp(start_ns // _SECOND_NS, UTC).strftime("%Y%m%dT%H%M00Z")


def source_bindings() -> dict[str, str]:
    paths = (
        "tools/brain_senses_external_schema_v2.py",
        "tools/brain_senses_external_schema_v1.py",
        "tools/brain_senses_external_successor_v2.py",
        "tools/brain_senses_external_orchestrator_v2.py",
        "tools/brain_senses_external_verify_v2.py",
        "tools/brain_senses_block_epoch_v2.py",
        "tools/brain_senses_program_watch.py",
        "tools/brain_senses_external_launch_v2.py",
        "tools/brain_senses_external_writer_v1.py",
        "tools/brain_senses_external_clock_v1.py",
        "tools/brain_senses_external_verify_v1.py",
        "tools/brain_senses_external_orchestrator_v1.py",
        "tools/brain_senses_block_provider_pilot.py",
        "tools/brain_senses_service_v1_runner.py",
        "src/memecoin_ai/domain.py",
        "src/memecoin_ai/sources.py",
        "src/memecoin_ai/storage.py",
        "src/memecoin_collector/private_config.py",
        "src/memecoin_research_storage/__init__.py",
        "src/memecoin_pumpswap/normalize.py",
        "src/memecoin_pumpswap/schema.py",
    )
    root = Path(__file__).resolve().parents[1]
    return {path: sha((root / path).read_bytes()) for path in paths}


def _digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def build_schedule(bootstrap_start_ns: int, clock_preflight_ns: int) -> dict[str, Any]:
    if (
        type(bootstrap_start_ns) is not int
        or type(clock_preflight_ns) is not int
        or bootstrap_start_ns <= 0
        or clock_preflight_ns <= 0
        or bootstrap_start_ns % _MINUTE_NS
        or bootstrap_start_ns < clock_preflight_ns + 2 * _MINUTE_NS
    ):
        raise ExternalV2Error("schedule_bootstrap_invalid")
    bootstrap = {
        "bucket": bucket_name(bootstrap_start_ns),
        "start_ns": bootstrap_start_ns,
        "end_ns": bootstrap_start_ns + 60 * _SECOND_NS,
    }
    frames = []
    for index in range(12):
        start_ns = bootstrap_start_ns + (5 + index * 5) * _MINUTE_NS
        frames.append(
            {
                "index": index,
                "bucket": bucket_name(start_ns),
                "start_ns": start_ns,
                "end_ns": start_ns + 180 * _SECOND_NS,
            }
        )
    return {
        "clock_preflight_ns": clock_preflight_ns,
        "bootstrap": bootstrap,
        "frames": frames,
        "expires_ns": bootstrap_start_ns + 63 * _MINUTE_NS,
    }


def build_plan(
    *,
    candidate: Mapping[str, Any],
    validation_sha256: str,
    completion_sha256: str,
    service_id: str,
    root_id: str,
    volume_uuid: str,
    bootstrap_start_ns: int,
    clock_preflight_ns: int,
    endpoint_origin: str,
) -> dict[str, Any]:
    """Build a reviewed-shape plan; proposal schedule in the v1 candidate is ignored."""
    if not _SERVICE_ID.fullmatch(service_id):
        raise ExternalV2Error("service_id_invalid")
    if endpoint_origin not in {"https://mainnet.helius-rpc.com", "https://rpc.helius.xyz"}:
        raise ExternalV2Error("provider_origin_invalid")
    candidate_value = dict(candidate)
    if (
        candidate_value.get("schema") != "brain-senses-external-successor-v2-candidate"
        or candidate_value.get("schema_version") != 2
        or candidate_value.get("activation_authorized") is not False
        or candidate_value.get("dispatch_authorized") is not False
        or candidate_value.get("replay_authorized") is not False
        or candidate_value.get("provider_io") is not False
        or candidate_value.get("provider_charge_units") is not None
        or candidate_value.get("provider_charge_status") != "unknown_unverified"
    ):
        raise ExternalV2Error("candidate_invalid")
    cert = candidate_value.get("predecessor")
    cert_sha = candidate_value.get("predecessor_sha256")
    if not isinstance(cert, dict) or not _digest(cert_sha):
        raise ExternalV2Error("candidate_certificate_invalid")
    if sha(canonical(cert)) != cert_sha:
        raise ExternalV2Error("candidate_certificate_digest")
    if cert.get("service_id") == service_id:
        raise ExternalV2Error("service_id_reuse")
    if (
        cert.get("storage", {}).get("root_id") != root_id
        or cert.get("storage", {}).get("volume_uuid") != volume_uuid
    ):
        raise ExternalV2Error("candidate_storage_mismatch")
    if not _digest(validation_sha256) or not _digest(completion_sha256):
        raise ExternalV2Error("predecessor_receipt_pin_invalid")

    schedule = build_schedule(bootstrap_start_ns, clock_preflight_ns)
    if (
        bootstrap_start_ns
        < max(cert.get("expires_ns", 0), cert.get("terminal_sealed_ns", 0)) + _MINUTE_NS
    ):
        raise ExternalV2Error("predecessor_terminal_not_reached")
    plan = {
        "schema": schema.PLAN_SCHEMA,
        "schema_version": 2,
        "service_id": service_id,
        "provider": "helius-free",
        "endpoint_origin": endpoint_origin,
        "storage": {
            "root_id": root_id,
            "volume_uuid": volume_uuid,
            "relative_root": f"services/{service_id}",
        },
        "code_sha256": source_bindings(),
        "predecessor": {
            "certificate": cert,
            "certificate_sha256": cert_sha,
            "relative_root": cert["storage"]["relative_root"],
            "candidate_sha256": sha(canonical(candidate_value)),
            "validation_sha256": validation_sha256,
            "completion_sha256": completion_sha256,
        },
        "schedule": schedule,
        "caps": dict(schema.CAPS),
    }
    from tools.brain_senses_external_verify_v2 import verify_plan

    verify_plan(plan, expected_sources=cast(dict[str, str], plan["code_sha256"]))
    return plan


def _reserved_remaining_bytes(state: Mapping[str, Any], plan: Mapping[str, Any]) -> int:
    return max(
        0,
        int(plan["caps"]["stored_bytes_total"]) - int(state.get("v2_stored_bytes", 0)),
    )


def _read_plan(writer: DescriptorWriter) -> tuple[dict[str, Any], str]:
    from tools.brain_senses_external_verify_v2 import verify_plan

    payload = writer.read_bytes(schema.PLAN_FILE)
    plan = json.loads(payload)
    verify_plan(plan, expected_sources=source_bindings())
    if canonical(plan) != payload:
        raise ExternalV2Error("plan_not_canonical")
    return plan, sha(payload)


def required_free_bytes(state: Mapping[str, Any], plan: Mapping[str, Any]) -> int:
    caps = plan["caps"]
    return (
        int(caps["disk_floor_bytes"])
        + _reserved_remaining_bytes(state, plan)
        + int(caps["peak_write_margin_bytes"])
        + int(caps["metadata_margin_bytes"])
    )


def _budget_check(
    state: Mapping[str, Any], plan: Mapping[str, Any], dispatch_ns: int, response_budget: int
) -> bool:
    caps = plan["caps"]
    if int(state.get("v2_stored_bytes", 0)) + response_budget > int(caps["stored_bytes_total"]):
        return False
    day = dispatch_ns // 86_400_000_000_000
    requests = raw_bytes = stored_bytes = 0
    for row in state.get("predecessor_day_usage") or []:
        if row["utc_day"] == day:
            requests += row["requests"]
            raw_bytes += row["raw_bytes"]
            stored_bytes += row["stored_bytes"]
    for key, row in state.get("v2_day_usage", {}).items():
        if int(key) == day:
            requests += row["requests"]
            raw_bytes += row["raw_bytes_reserved"]
            stored_bytes += row["stored_bytes_reserved"]
    return (
        requests + 1 <= int(caps["max_requests_per_utc_date"])
        and raw_bytes + response_budget <= int(caps["raw_bytes_per_utc_date"])
        and stored_bytes + response_budget <= int(caps["stored_bytes_per_utc_date"])
    )


def _frame_bytes_admissible(phase_known: int, lease_known: int) -> bool:
    """Reserve one worst-case response while charging known durable bytes actual."""
    return (
        phase_known + schema.MAX_RESPONSE_BYTES <= cast(int, schema.CAPS["frame_raw_bytes"])
        and lease_known + schema.MAX_RESPONSE_BYTES <= schema.RAW_LEASE_BYTES
    )


def _append_event(
    writer: DescriptorWriter,
    *,
    kind: str,
    bucket: str | None,
    at_ns: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    raw = writer.read_bytes(schema.EVENTS_FILE) if writer.exists(schema.EVENTS_FILE) else b""
    from tools.brain_senses_external_verify_v2 import verify_events

    events = verify_events(raw)
    previous = events[-1]["event_sha256"] if events else "0" * 64
    if events and events[-1].get("kind") == "stop":
        raise ExternalV2Error("terminal_stop")
    body = {
        "schema": schema.EVENT_SCHEMA,
        "schema_version": 2,
        "seq": len(events),
        "kind": kind,
        "bucket": bucket,
        "at_ns": at_ns,
        "payload": payload,
        "previous_sha256": previous,
    }
    event = {**body, "event_sha256": sha(canonical(body))}
    writer.append(schema.EVENTS_FILE, canonical(event))
    return event


def initialize(
    writer: DescriptorWriter,
    plan: dict[str, Any],
    *,
    predecessor_reader: Any,
    candidate_bytes: bytes,
    validation_bytes: bytes,
    completion_bytes: bytes,
) -> dict[str, Any]:
    """Initialize only after caller-held host/storage/service locks and v1 revalidation."""
    from tools.brain_senses_external_verify_v2 import verify_plan, verify_predecessor

    verify_plan(plan, expected_sources=source_bindings())
    predecessor_state = verify_predecessor(
        predecessor_reader,
        plan["predecessor"],
        candidate_bytes=candidate_bytes,
        validation_bytes=validation_bytes,
        completion_bytes=completion_bytes,
    )
    predecessor_plan = predecessor_state["plan"]
    if predecessor_plan.get("endpoint_origin") != plan["endpoint_origin"]:
        raise ExternalV2Error("predecessor_provider_origin_mismatch")
    from tools.brain_senses_external_verify_v2 import predecessor_daily_usage

    day_usage = predecessor_daily_usage(predecessor_reader, plan["predecessor"]["certificate"])
    with writer.lock(schema.LOCK_FILE):
        if writer.exists(schema.PLAN_FILE) or writer.exists(schema.EVENTS_FILE):
            raise ExternalV2Error("already_initialized")
        payload = canonical(plan)
        writer.atomic_write(schema.PLAN_FILE, payload, create_only=True)
        event = _append_event(
            writer,
            kind="initialized",
            bucket=None,
            at_ns=plan["schedule"]["clock_preflight_ns"],
            payload={
                "plan_sha256": sha(payload),
                "predecessor_day_usage": day_usage,
                "predecessor_day_usage_sha256": sha(canonical(day_usage)),
            },
        )
        return {"status": "initialized_offline", "plan_sha256": sha(payload), "event": event}


def _publish_stop(
    writer: DescriptorWriter, *, reason: str, bucket: str | None, at_ns: int
) -> dict[str, Any]:
    stop = {
        "schema": schema.STOP_SCHEMA,
        "schema_version": 2,
        "reason": reason,
        "stopped_ns": at_ns,
        "last_bucket": bucket,
        "post_stop_requests": 0,
    }
    raw = canonical(stop)
    if writer.exists(schema.STOP_FILE):
        existing = writer.read_bytes(schema.STOP_FILE)
        if existing != raw:
            # A prior crash after STOP-file publication remains terminal; never
            # rewrite its immutable reason or timestamp.
            return cast(dict[str, Any], json.loads(existing))
    else:
        writer.atomic_write(schema.STOP_FILE, raw, create_only=True)
    from tools.brain_senses_external_verify_v2 import verify_events

    events = verify_events(writer.read_bytes(schema.EVENTS_FILE))
    if not events or events[-1]["kind"] != "stop":
        _append_event(writer, kind="stop", bucket=None, at_ns=at_ns, payload={"stop": stop})
    return stop


def run_bootstrap(
    writer: DescriptorWriter,
    *,
    client_loader: Any,
    guard: Any,
    predecessor_reader: Any,
    clock_probe: Any = None,
    now_ns: int | None = None,
) -> dict[str, Any]:
    """Execute only the one-request bootstrap phase; all prior intents are spent."""
    from tools import brain_senses_external_clock_v1 as clock
    from tools.brain_senses_external_verify_v2 import DescriptorReaderV2, verify

    with writer.lock(schema.LOCK_FILE):
        plan, plan_hash = _read_plan(writer)
        bucket = plan["schedule"]["bootstrap"]["bucket"]
        window = plan["schedule"]["bootstrap"]
        with DescriptorReaderV2(writer.root_fd) as reader:
            state = verify(
                reader,
                expected_plan_sha256=plan_hash,
                expected_sources=source_bindings(),
                predecessor_reader=predecessor_reader,
            )
        if state["stop"]:
            return {"status": "stopped", "provider_io": False, "replay_authorized": False}
        if not state["predecessor_lineage_verified"]:
            raise ExternalV2Error("predecessor_lineage_unverified")
        if bucket in state["reservations"]:
            result = state["buckets"].get(bucket, {})
            return {
                "status": result.get("status", "interrupted_no_replay"),
                "provider_io": False,
                "replay_authorized": False,
            }
        now = time.time_ns() if now_ns is None else now_ns
        if now < window["start_ns"] + 5 * _SECOND_NS:
            return {"status": "not_due", "provider_io": False}
        if now > window["end_ns"] - 5 * _SECOND_NS:
            stop = _publish_stop(writer, reason="missed_bootstrap", bucket=bucket, at_ns=now)
            return {"status": "stopped", "stop": stop, "provider_io": False}
        try:
            if clock_probe is None:
                clock_record = clock.probe_pair()
            else:
                clock_record = clock_probe()
            clock.validate_clock(clock_record, now_ns=time.time_ns())
        except Exception:
            stop = _publish_stop(
                writer, reason="clock_check_failed", bucket=bucket, at_ns=time.time_ns()
            )
            return {"status": "stopped", "stop": stop, "provider_io": False}
        clock_raw = canonical(clock_record)
        clock_path = f"clock/{bucket}-00.json"
        clock_hash = sha(clock_raw)
        guard.revalidate(min_free_bytes=required_free_bytes(state, plan))
        writer.atomic_write(clock_path, clock_raw, create_only=True)
        reservation_id = sha(f"{plan_hash}:{bucket}:bootstrap".encode())[:32]
        reservation = {
            "schema": schema.BUCKET_SCHEMA,
            "schema_version": 2,
            "bucket": bucket,
            "phase": "bootstrap",
            "frame_index": None,
            "reservation_id": reservation_id,
            "plan_sha256": plan_hash,
            "request_limit": 1,
            "raw_byte_cap": schema.CAPS["bootstrap_raw_bytes"],
            "stored_byte_cap": schema.CAPS["bootstrap_stored_bytes"],
            "window": {"start_ns": window["start_ns"], "end_ns": window["end_ns"]},
            "created_ns": now,
            "predecessor_event_sha256": state["event_tail_sha256"],
            "clock_check_path": clock_path,
            "clock_check_sha256": clock_hash,
        }
        reservation_raw = canonical(reservation)
        _append_event(
            writer,
            kind="reservation",
            bucket=bucket,
            at_ns=now,
            payload={"reservation_sha256": sha(reservation_raw), "frame_index": None},
        )
        writer.atomic_write(f"buckets/{bucket}/reservation.json", reservation_raw, create_only=True)
        writer.mkdir(f"buckets/{bucket}/requests")
        writer.mkdir(f"buckets/{bucket}/results")
        writer.mkdir(f"buckets/{bucket}/captures")
        started = {
            "schema": schema.BUCKET_SCHEMA,
            "schema_version": 2,
            "reservation_id": reservation_id,
            "plan_sha256": plan_hash,
            "started_ns": time.time_ns(),
            "pid": os.getpid(),
        }
        started_raw = canonical(started)
        _append_event(
            writer,
            kind="started",
            bucket=bucket,
            at_ns=cast(int, started["started_ns"]),
            payload={"started_sha256": sha(started_raw)},
        )
        writer.atomic_write(f"buckets/{bucket}/started.json", started_raw, create_only=True)
        guard.revalidate(min_free_bytes=required_free_bytes(state, plan))
        try:
            client = client_loader()
        except Exception:
            stop_record = _publish_stop(
                writer, reason="provider_client_load_failed", bucket=bucket, at_ns=time.time_ns()
            )
            return {"status": "stopped", "stop": stop_record, "provider_io": False}
        if (
            getattr(client, "source_alias", None) != plan["provider"]
            or getattr(client, "endpoint_origin", None) != plan["endpoint_origin"]
            or getattr(client, "max_bytes", schema.MAX_RESPONSE_BYTES + 1)
            > schema.MAX_RESPONSE_BYTES
        ):
            stop_record = _publish_stop(
                writer, reason="provider_binding_invalid", bucket=bucket, at_ns=time.time_ns()
            )
            return {"status": "stopped", "stop": stop_record, "provider_io": False}
        method = "getSlot"
        params: list[Any] = [{"commitment": "finalized"}]
        semantic_hash = sha(canonical({"method": method, "params": params}))
        wire_hash = sha(
            canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).rstrip(b"\n")
        )
        decision_ns, monotonic_ns = time.time_ns(), time.monotonic_ns()
        try:
            if state["requests"] + 1 > schema.MAX_REQUESTS or not _budget_check(
                state, plan, decision_ns, schema.MAX_RESPONSE_BYTES
            ):
                raise ExternalV2Error("request_budget_exhausted")
            clock.validate_progress(clock_record, wall_ns=decision_ns, monotonic_ns=monotonic_ns)
            guard.revalidate(min_free_bytes=required_free_bytes(state, plan))
        except Exception:
            stop = _publish_stop(
                writer, reason="clock_or_storage_gate_failed", bucket=bucket, at_ns=time.time_ns()
            )
            return {"status": "stopped", "stop": stop, "provider_io": False}
        request = {
            "schema": schema.REQUEST_SCHEMA,
            "schema_version": 2,
            "provider": plan["provider"],
            "request_id": f"{bucket}-00",
            "reservation_id": reservation_id,
            "ordinal": 0,
            "phase": "bootstrap",
            "frame_index": None,
            "method": method,
            "params": params,
            "request_sha256": semantic_hash,
            "wire_request_sha256": wire_hash,
            "decision_ns": decision_ns,
            "request_started_ns": decision_ns,
            "monotonic_ns": monotonic_ns,
            "clock_check_path": clock_path,
            "clock_check_sha256": clock_hash,
        }
        request_raw = canonical(request)
        _append_event(
            writer,
            kind="request_intent",
            bucket=bucket,
            at_ns=decision_ns,
            payload={
                "ordinal": 0,
                "request_sha256": sha(request_raw),
                "request_started_ns": decision_ns,
            },
        )
        writer.atomic_write(f"buckets/{bucket}/requests/00.json", request_raw, create_only=True)
        try:
            now2, mono2 = time.time_ns(), time.monotonic_ns()
            clock.validate_progress(clock_record, wall_ns=now2, monotonic_ns=mono2)
            if now2 > window["end_ns"] - 5 * _SECOND_NS:
                raise ExternalV2Error("bucket_expired_after_intent")
            guard.revalidate(min_free_bytes=required_free_bytes(state, plan))
            capture = client.fetch(method, params)
        except Exception as exc:
            from memecoin_ai.sources import HTTPSourceError, TransportSourceError

            result = {
                "schema": schema.RESULT_SCHEMA,
                "schema_version": 2,
                "provider": plan["provider"],
                "request_id": request["request_id"],
                "request_sha256": semantic_hash,
                "wire_request_sha256": wire_hash,
                "status": "ambiguous",
                "result_status": "unknown",
                "response_sha256": None,
                "response_bytes": None,
                "stored_response_bytes": None,
                "availability_ns": None,
                "decision_ns": decision_ns,
                "method": method,
                "event_slot": None,
                "produced_slots": None,
                "signatures": None,
                "block_identity": None,
                "event_time_ns": None,
                "http_status": exc.status if isinstance(exc, HTTPSourceError) else None,
                "transport_category": exc.reason if isinstance(exc, TransportSourceError) else None,
            }
            writer.atomic_write(
                f"buckets/{bucket}/results/00.json", canonical(result), create_only=True
            )
            if isinstance(exc, HTTPSourceError) and exc.status == 429:
                reason = "provider_http_429"
            elif isinstance(exc, HTTPSourceError) and exc.status in {402, 403}:
                reason = "provider_quota_or_billing_error"
            elif isinstance(exc, TransportSourceError):
                reason = "provider_transport_error"
            else:
                reason = "provider_or_transport_error"
            stop = _publish_stop(writer, reason=reason, bucket=bucket, at_ns=time.time_ns())
            return {"status": "stopped", "stop": stop, "provider_io": True}
        expected_wire = (
            canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            .rstrip(b"\n")
            .decode()
        )
        if capture.source_alias != plan["provider"] or capture.request_json != expected_wire:
            stop = _publish_stop(
                writer, reason="capture_identity_mismatch", bucket=bucket, at_ns=time.time_ns()
            )
            return {"status": "stopped", "stop": stop, "provider_io": True}
        response = capture.response
        if len(response) > schema.MAX_RESPONSE_BYTES:
            stop = _publish_stop(
                writer, reason="response_cap_exceeded", bucket=bucket, at_ns=time.time_ns()
            )
            return {"status": "stopped", "stop": stop, "provider_io": True}
        capture_dir = f"buckets/{bucket}/captures/00"
        writer.mkdir(capture_dir)
        writer.create(capture_dir + "/response.json", response)
        receipt = {
            "schema": schema.RECEIPT_SCHEMA,
            "schema_version": 2,
            "provider": plan["provider"],
            "request_id": request["request_id"],
            "request_sha256": semantic_hash,
            "wire_request_sha256": wire_hash,
            "request_started_ns": capture.request_started_ns,
            "received_ns": capture.received_ns,
            "availability_ns": None,
            "response_sha256": sha(response),
            "response_bytes": len(response),
            "storage": {"path": "response.json", "bytes": len(response), "codec": "raw"},
        }
        receipt_raw = canonical(receipt)
        writer.atomic_write(capture_dir + "/receipt.json", receipt_raw, create_only=True)
        availability_ns = time.time_ns()
        result_status = "parsed"
        try:
            slot = capture.result()
        except Exception:
            slot, result_status = None, "provider_error"
        if result_status == "parsed" and (
            not isinstance(slot, int)
            or isinstance(slot, bool)
            or slot < int(plan["predecessor"]["certificate"]["frontier"])
        ):
            result_status = "invalid_response"
        result = {
            "schema": schema.RESULT_SCHEMA,
            "schema_version": 2,
            "provider": plan["provider"],
            "request_id": request["request_id"],
            "request_sha256": semantic_hash,
            "wire_request_sha256": wire_hash,
            "status": "captured",
            "result_status": result_status,
            "response_sha256": sha(response),
            "response_bytes": len(response),
            "stored_response_bytes": len(response),
            "availability_ns": availability_ns,
            "decision_ns": decision_ns,
            "method": "getSlot",
            "event_slot": slot if result_status == "parsed" else None,
            "produced_slots": None,
            "signatures": None,
            "block_identity": None,
            "event_time_ns": None,
            "http_status": None,
            "transport_category": None,
        }
        writer.atomic_write(
            f"buckets/{bucket}/results/00.json", canonical(result), create_only=True
        )
        if result_status != "parsed":
            stop = _publish_stop(
                writer, reason="invalid_bootstrap_response", bucket=bucket, at_ns=time.time_ns()
            )
            return {"status": "stopped", "stop": stop, "provider_io": True}
        bundle = {
            "schema": schema.BUNDLE_SCHEMA,
            "schema_version": 2,
            "provider": plan["provider"],
            "phase": "bootstrap",
            "frame_index": None,
            "bucket": bucket,
            "reservation_id": reservation_id,
            "plan_sha256": plan_hash,
            "status": "complete",
            "request_count": 1,
            "requests": [
                {
                    "ordinal": 0,
                    "request_sha256": sha(request_raw),
                    "receipt_sha256": sha(receipt_raw),
                    "result_sha256": sha(canonical(result)),
                    "response_sha256": sha(response),
                }
            ],
            "raw_bytes": len(response),
            "stored_bytes": len(response),
            "bootstrap_slot": slot,
            "frame": None,
            "selected_blocks": [],
            "provider_charge_units": None,
            "provider_charge_status": "unknown_unverified",
            "terminal_status": "bootstrap_complete",
        }
        bundle_raw = canonical(bundle)
        bundle_hash = sha(bundle_raw)
        writer.atomic_write(f"buckets/{bucket}/bundle.json", bundle_raw, create_only=True)
        _append_event(
            writer,
            kind="bundle",
            bucket=bucket,
            at_ns=time.time_ns(),
            payload={"bundle_sha256": bundle_hash},
        )
        with DescriptorReaderV2(writer.root_fd) as reader:
            from tools.brain_senses_external_verify_v2 import _verify_bucket

            checked = _verify_bucket(
                reader,
                plan,
                bucket,
                plan["predecessor"]["certificate"]["frontier"],
                require_seal=False,
            )
        if checked["bundle_sha256"] != bundle_hash:
            stop = _publish_stop(
                writer,
                reason="independent_bundle_verification_failed",
                bucket=bucket,
                at_ns=time.time_ns(),
            )
            return {"status": "stopped", "stop": stop, "provider_io": True}
        seal = {
            "schema": schema.SEAL_SCHEMA,
            "schema_version": 2,
            "bucket": bucket,
            "phase": "bootstrap",
            "frame_index": None,
            "reservation_id": reservation_id,
            "plan_sha256": plan_hash,
            "bundle_sha256": bundle_hash,
            "sealed_ns": time.time_ns(),
            "status": "complete",
        }
        seal_raw = canonical(seal)
        writer.atomic_write(f"buckets/{bucket}/seal.json", seal_raw, create_only=True)
        _append_event(
            writer,
            kind="seal",
            bucket=bucket,
            at_ns=seal["sealed_ns"],
            payload={"seal_sha256": sha(seal_raw)},
        )
        with DescriptorReaderV2(writer.root_fd) as reader:
            _verify_bucket(
                reader,
                plan,
                bucket,
                plan["predecessor"]["certificate"]["frontier"],
            )
        return {
            "status": "complete",
            "bundle": bundle,
            "seal": seal,
            "provider_io": True,
            "replay_authorized": False,
        }


def run_frame(
    writer: DescriptorWriter,
    frame_index: int,
    *,
    client_loader: Any,
    guard: Any,
    predecessor_reader: Any,
    clock_probe: Any = None,
    now_ns: int | None = None,
) -> dict[str, Any]:
    """Run one inventory-first frame. Any reservation consumes the whole bucket."""
    from tools import brain_senses_external_clock_v1 as clock
    from tools.brain_senses_block_epoch_v2 import _build_selection
    from tools.brain_senses_block_provider_pilot import (
        _signature_list,
        _structural_response_validation,
    )
    from tools.brain_senses_external_verify_v2 import (
        DescriptorReaderV2,
        _verify_bucket,
        decode,
        verify,
        verify_events,
    )

    with writer.lock(schema.LOCK_FILE):
        plan, plan_hash = _read_plan(writer)
        if type(frame_index) is not int or not 0 <= frame_index < len(plan["schedule"]["frames"]):
            raise ExternalV2Error("frame_index_invalid")
        window = plan["schedule"]["frames"][frame_index]
        bucket = window["bucket"]
        with DescriptorReaderV2(writer.root_fd) as reader:
            state = verify(
                reader,
                expected_plan_sha256=plan_hash,
                expected_sources=source_bindings(),
                predecessor_reader=predecessor_reader,
            )
        if not state["predecessor_lineage_verified"]:
            raise ExternalV2Error("predecessor_lineage_unverified")
        if state["stop"]:
            return {"status": "stopped", "provider_io": False, "replay_authorized": False}
        if bucket in state["reservations"] or bucket in state["missed"]:
            result = state["buckets"].get(bucket, {})
            return {
                "status": result.get("status", "interrupted_no_replay"),
                "provider_io": False,
                "replay_authorized": False,
            }
        now = time.time_ns() if now_ns is None else now_ns
        bootstrap_bucket = plan["schedule"]["bootstrap"]["bucket"]
        if not state["sealed"].get(bootstrap_bucket):
            bootstrap_window = plan["schedule"]["bootstrap"]
            if now > bootstrap_window["end_ns"] - 5 * _SECOND_NS:
                stop_value = _publish_stop(
                    writer,
                    reason="missed_bootstrap",
                    bucket=bootstrap_bucket,
                    at_ns=now,
                )
                return {"status": "stopped", "stop": stop_value, "provider_io": False}
            raise ExternalV2Error("frame_without_bootstrap_seal")
        # A later process may have missed earlier future buckets entirely.
        # Record only empty, expired windows; this never replays requests.
        for prior in plan["schedule"]["frames"][:frame_index]:
            prior_bucket = prior["bucket"]
            if (
                prior_bucket not in state["reservations"]
                and prior_bucket not in state["missed"]
                and now > prior["end_ns"] - 5 * _SECOND_NS
            ):
                _append_event(
                    writer,
                    kind="missed",
                    bucket=prior_bucket,
                    at_ns=now,
                    payload={
                        "index": prior["index"],
                        "window": {"start_ns": prior["start_ns"], "end_ns": prior["end_ns"]},
                        "prior_event_sha256": state["event_tail_sha256"],
                    },
                )
                state["missed"].add(prior_bucket)
                state["event_tail_sha256"] = verify_events(writer.read_bytes(schema.EVENTS_FILE))[
                    -1
                ]["event_sha256"]
        for prior in plan["schedule"]["frames"][:frame_index]:
            if not state["sealed"].get(prior["bucket"]) and prior["bucket"] not in state["missed"]:
                raise ExternalV2Error("frame_schedule_gap_unresolved")

        if now < window["start_ns"] + 5 * _SECOND_NS:
            return {"status": "not_due", "provider_io": False}
        if now > window["end_ns"] - 5 * _SECOND_NS:
            _append_event(
                writer,
                kind="missed",
                bucket=bucket,
                at_ns=now,
                payload={
                    "index": frame_index,
                    "window": {"start_ns": window["start_ns"], "end_ns": window["end_ns"]},
                    "prior_event_sha256": state["event_tail_sha256"],
                },
            )
            return {"status": "missed", "provider_io": False, "replay_authorized": False}
        try:
            clock_record = clock.probe_pair() if clock_probe is None else clock_probe()
            clock.validate_clock(clock_record, now_ns=time.time_ns())
        except Exception:
            stop = _publish_stop(
                writer, reason="clock_check_failed", bucket=bucket, at_ns=time.time_ns()
            )
            return {"status": "stopped", "stop": stop, "provider_io": False}
        clock_raw = canonical(clock_record)
        clock_path = f"clock/{bucket}-00.json"
        clock_hash = sha(clock_raw)
        guard.revalidate(min_free_bytes=required_free_bytes(state, plan))
        writer.atomic_write(clock_path, clock_raw, create_only=True)

        reservation_id = sha(f"{plan_hash}:{bucket}:frame:{frame_index}".encode())[:32]
        reservation = {
            "schema": schema.BUCKET_SCHEMA,
            "schema_version": 2,
            "bucket": bucket,
            "phase": "frame",
            "frame_index": frame_index,
            "reservation_id": reservation_id,
            "plan_sha256": plan_hash,
            "request_limit": schema.CAPS["frame_requests"],
            "raw_byte_cap": schema.CAPS["frame_raw_bytes"],
            "stored_byte_cap": schema.CAPS["frame_stored_bytes"],
            "window": {"start_ns": window["start_ns"], "end_ns": window["end_ns"]},
            "created_ns": now,
            "predecessor_event_sha256": state["event_tail_sha256"],
            "clock_check_path": clock_path,
            "clock_check_sha256": clock_hash,
        }
        reservation_raw = canonical(reservation)
        _append_event(
            writer,
            kind="reservation",
            bucket=bucket,
            at_ns=now,
            payload={"reservation_sha256": sha(reservation_raw), "frame_index": frame_index},
        )
        writer.atomic_write(f"buckets/{bucket}/reservation.json", reservation_raw, create_only=True)
        for directory in ("requests", "results", "captures"):
            writer.mkdir(f"buckets/{bucket}/{directory}")
        started_ns = time.time_ns()
        started = {
            "schema": schema.BUCKET_SCHEMA,
            "schema_version": 2,
            "reservation_id": reservation_id,
            "plan_sha256": plan_hash,
            "started_ns": started_ns,
            "pid": os.getpid(),
        }
        started_raw = canonical(started)
        _append_event(
            writer,
            kind="started",
            bucket=bucket,
            at_ns=started_ns,
            payload={"started_sha256": sha(started_raw)},
        )
        writer.atomic_write(f"buckets/{bucket}/started.json", started_raw, create_only=True)
        try:
            guard.revalidate(min_free_bytes=required_free_bytes(state, plan))
            client = client_loader()
        except Exception:
            stop_record = _publish_stop(
                writer, reason="provider_client_load_failed", bucket=bucket, at_ns=time.time_ns()
            )
            return {"status": "stopped", "stop": stop_record, "provider_io": False}
        if (
            getattr(client, "source_alias", None) != plan["provider"]
            or getattr(client, "endpoint_origin", None) != plan["endpoint_origin"]
            or getattr(client, "max_bytes", schema.MAX_RESPONSE_BYTES + 1)
            > schema.MAX_RESPONSE_BYTES
        ):
            stop_record = _publish_stop(
                writer, reason="provider_binding_invalid", bucket=bucket, at_ns=time.time_ns()
            )
            return {"status": "stopped", "stop": stop_record, "provider_io": False}

        requests: list[dict[str, Any]] = []
        receipts: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        responses: list[bytes] = []
        phase_raw = 0
        frame = state["frontier"]
        selection: dict[str, Any] | None = None
        operations: list[tuple[str, list[Any]]] = [
            ("getSlot", [{"commitment": "finalized"}]),
        ]
        anchor: int | None = None
        ordinal = 0
        previous_dispatch_ns = max(
            (event["at_ns"] for event in state["events"] if event["kind"] == "request_intent"),
            default=0,
        )

        def _frame_stop(reason: str, *, provider_io: bool) -> dict[str, Any]:
            value = _publish_stop(writer, reason=reason, bucket=bucket, at_ns=time.time_ns())
            return {"status": "stopped", "stop": value, "provider_io": provider_io}

        while operations:
            method, params = operations.pop(0)
            spacing_ns = previous_dispatch_ns + 250_000_000 - time.time_ns()
            if spacing_ns > 0:
                time.sleep(spacing_ns / _SECOND_NS)
            decision_ns, mono_ns = time.time_ns(), time.monotonic_ns()
            if (
                decision_ns < window["start_ns"] + 5 * _SECOND_NS
                or decision_ns > window["end_ns"] - 5 * _SECOND_NS
            ):
                return _frame_stop("frame_window_expired", provider_io=False)
            if decision_ns >= plan["schedule"]["expires_ns"]:
                return _frame_stop("lease_expired", provider_io=False)
            if ordinal >= cast(int, schema.CAPS["frame_requests"]):
                return _frame_stop("frame_request_cap_exhausted", provider_io=False)
            if not _frame_bytes_admissible(phase_raw, state["v2_raw_bytes"]):
                return _frame_stop("frame_raw_budget_exhausted", provider_io=False)
            if not _budget_check(state, plan, decision_ns, schema.MAX_RESPONSE_BYTES):
                return _frame_stop("request_budget_exhausted", provider_io=False)
            try:
                clock.validate_progress(clock_record, wall_ns=decision_ns, monotonic_ns=mono_ns)
                guard.revalidate(min_free_bytes=required_free_bytes(state, plan))
            except Exception:
                return _frame_stop("clock_or_storage_gate_failed", provider_io=False)
            semantic_hash = sha(canonical({"method": method, "params": params}))
            wire_body = canonical(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            ).rstrip(b"\n")
            wire_hash = sha(wire_body)
            request = {
                "schema": schema.REQUEST_SCHEMA,
                "schema_version": 2,
                "provider": plan["provider"],
                "request_id": f"{bucket}-{ordinal:02d}",
                "reservation_id": reservation_id,
                "ordinal": ordinal,
                "phase": "frame",
                "frame_index": frame_index,
                "method": method,
                "params": params,
                "request_sha256": semantic_hash,
                "wire_request_sha256": wire_hash,
                "decision_ns": decision_ns,
                "request_started_ns": decision_ns,
                "monotonic_ns": mono_ns,
                "clock_check_path": clock_path,
                "clock_check_sha256": clock_hash,
            }
            request_raw = canonical(request)
            _append_event(
                writer,
                kind="request_intent",
                bucket=bucket,
                at_ns=decision_ns,
                payload={
                    "ordinal": ordinal,
                    "request_sha256": sha(request_raw),
                    "request_started_ns": decision_ns,
                },
            )
            writer.atomic_write(
                f"buckets/{bucket}/requests/{ordinal:02d}.json", request_raw, create_only=True
            )
            local_day = decision_ns // 86_400_000_000_000
            day_row = state["v2_day_usage"].setdefault(
                local_day,
                {"requests": 0, "raw_bytes_reserved": 0, "stored_bytes_reserved": 0},
            )
            day_row["requests"] += 1
            day_row["raw_bytes_reserved"] += schema.MAX_RESPONSE_BYTES
            day_row["stored_bytes_reserved"] += schema.MAX_RESPONSE_BYTES
            state["requests"] += 1
            state["v2_raw_bytes"] += schema.MAX_RESPONSE_BYTES
            state["v2_stored_bytes"] += schema.MAX_RESPONSE_BYTES
            previous_dispatch_ns = decision_ns
            requests.append(request)
            try:
                sent, sent_mono = time.time_ns(), time.monotonic_ns()
                clock.validate_progress(clock_record, wall_ns=sent, monotonic_ns=sent_mono)
                if (
                    sent < window["start_ns"] + 5 * _SECOND_NS
                    or sent > window["end_ns"] - 5 * _SECOND_NS
                ):
                    raise ExternalV2Error("frame_window_expired")
                guard.revalidate(min_free_bytes=required_free_bytes(state, plan))
                capture = client.fetch(method, params)
            except Exception as exc:
                from memecoin_ai.sources import HTTPSourceError, TransportSourceError

                result = {
                    "schema": schema.RESULT_SCHEMA,
                    "schema_version": 2,
                    "provider": plan["provider"],
                    "request_id": request["request_id"],
                    "request_sha256": semantic_hash,
                    "wire_request_sha256": wire_hash,
                    "status": "ambiguous",
                    "result_status": "unknown",
                    "response_sha256": None,
                    "response_bytes": None,
                    "stored_response_bytes": None,
                    "availability_ns": None,
                    "decision_ns": decision_ns,
                    "method": method,
                    "event_slot": params[0] if method == "getBlock" else None,
                    "produced_slots": None,
                    "signatures": None,
                    "block_identity": None,
                    "event_time_ns": None,
                    "http_status": exc.status if isinstance(exc, HTTPSourceError) else None,
                    "transport_category": exc.reason
                    if isinstance(exc, TransportSourceError)
                    else None,
                }
                writer.atomic_write(
                    f"buckets/{bucket}/results/{ordinal:02d}.json",
                    canonical(result),
                    create_only=True,
                )
                if isinstance(exc, HTTPSourceError) and exc.status == 429:
                    reason = "provider_http_429"
                elif isinstance(exc, HTTPSourceError) and exc.status in {402, 403}:
                    reason = "provider_quota_or_billing_error"
                elif isinstance(exc, TransportSourceError):
                    reason = "provider_transport_error"
                else:
                    reason = "provider_or_transport_error"
                return _frame_stop(reason, provider_io=True)
            expected_wire = wire_body.decode()
            if capture.source_alias != plan["provider"] or capture.request_json != expected_wire:
                return _frame_stop("capture_identity_mismatch", provider_io=True)
            response = capture.response
            if len(response) > schema.MAX_RESPONSE_BYTES or phase_raw + len(response) > cast(
                int, schema.CAPS["frame_raw_bytes"]
            ):
                return _frame_stop("frame_response_cap_exceeded", provider_io=True)
            cap_dir = f"buckets/{bucket}/captures/{ordinal:02d}"
            writer.mkdir(cap_dir)
            writer.create(cap_dir + "/response.json", response)
            receipt = {
                "schema": schema.RECEIPT_SCHEMA,
                "schema_version": 2,
                "provider": plan["provider"],
                "request_id": request["request_id"],
                "request_sha256": semantic_hash,
                "wire_request_sha256": wire_hash,
                "request_started_ns": capture.request_started_ns,
                "received_ns": capture.received_ns,
                "availability_ns": None,
                "response_sha256": sha(response),
                "response_bytes": len(response),
                "storage": {"path": "response.json", "bytes": len(response), "codec": "raw"},
            }
            receipt_raw = canonical(receipt)
            writer.atomic_write(cap_dir + "/receipt.json", receipt_raw, create_only=True)
            availability = time.time_ns()
            result_status = "parsed"
            try:
                parsed = capture.result()
            except Exception:
                parsed, result_status = None, "provider_error"
            event_slot: int | None = None
            produced_slots: list[int] | None = None
            signature_values: list[str] | None = None
            block_identity: dict[str, Any] | None = None
            event_time_ns: int | None = None
            if result_status == "parsed":
                if method == "getSlot":
                    if not isinstance(parsed, int) or isinstance(parsed, bool):
                        result_status = "invalid_response"
                    else:
                        event_slot = parsed
                elif method == "getBlocks":
                    if (
                        not isinstance(parsed, list)
                        or parsed != sorted(set(parsed))
                        or any(
                            type(slot) is not int or slot < frame or slot > cast(int, anchor)
                            for slot in parsed
                        )
                    ):
                        result_status = "invalid_response"
                    else:
                        produced_slots = parsed
                else:
                    detail = cast(str, params[1]["transactionDetails"])
                    signature_values = _signature_list(parsed, detail)
                    if signature_values is None or (
                        detail == "full"
                        and _structural_response_validation(parsed, cast(int, params[0]))
                    ):
                        result_status = "invalid_response"
                    else:
                        event_slot = cast(int, params[0])
                        block_identity = {
                            key: parsed.get(key)
                            for key in (
                                "blockhash",
                                "previousBlockhash",
                                "parentSlot",
                                "blockTime",
                                "blockHeight",
                            )
                        }
                        block_time = parsed.get("blockTime")
                        event_time_ns = block_time * _SECOND_NS if type(block_time) is int else None
            result = {
                "schema": schema.RESULT_SCHEMA,
                "schema_version": 2,
                "provider": plan["provider"],
                "request_id": request["request_id"],
                "request_sha256": semantic_hash,
                "wire_request_sha256": wire_hash,
                "status": "captured",
                "result_status": result_status,
                "response_sha256": sha(response),
                "response_bytes": len(response),
                "stored_response_bytes": len(response),
                "availability_ns": availability,
                "decision_ns": decision_ns,
                "method": method,
                "event_slot": event_slot,
                "produced_slots": produced_slots,
                "signatures": signature_values,
                "block_identity": block_identity,
                "event_time_ns": event_time_ns,
                "http_status": None,
                "transport_category": None,
            }
            writer.atomic_write(
                f"buckets/{bucket}/results/{ordinal:02d}.json", canonical(result), create_only=True
            )
            # Only a parsed, hash-bound response with a durable local availability
            # timestamp releases the conservative per-intent 32 MB reservation.
            if result_status == "parsed" and availability is not None:
                day_row["raw_bytes_reserved"] -= schema.MAX_RESPONSE_BYTES - len(response)
                day_row["stored_bytes_reserved"] -= schema.MAX_RESPONSE_BYTES - len(response)
                state["v2_raw_bytes"] -= schema.MAX_RESPONSE_BYTES - len(response)
                state["v2_stored_bytes"] -= schema.MAX_RESPONSE_BYTES - len(response)
            receipts.append(receipt)
            results.append(result)
            responses.append(response)
            phase_raw += len(response)
            ordinal += 1
            if result_status != "parsed":
                reason = (
                    "provider_error_response"
                    if result_status == "provider_error"
                    else "invalid_frame_response"
                )
                return _frame_stop(reason, provider_io=True)

            if method == "getSlot":
                if not isinstance(parsed, int) or isinstance(parsed, bool) or parsed < frame:
                    return _frame_stop("external_writer_anchor_regression", provider_io=True)
                anchor = parsed
                width = anchor - frame + 1
                if width > schema.MAX_FRAME_WIDTH:
                    return _frame_stop("external_writer_oversized_frame", provider_io=True)
                operations.append(("getBlocks", [frame, anchor, {"commitment": "finalized"}]))
            elif method == "getBlocks":
                if (
                    not isinstance(parsed, list)
                    or parsed != sorted(set(parsed))
                    or any(
                        type(slot) is not int or slot < frame or slot > cast(int, anchor)
                        for slot in parsed
                    )
                ):
                    return _frame_stop("invalid_inventory_response", provider_io=True)
                selection = _build_selection(
                    plan={
                        "seed": plan["service_id"],
                        "epoch_id": bucket,
                        "selection_count": schema.SELECTION_COUNT,
                        "selection_rule": (
                            "uniform_without_replacement_from_finalized_getblocks_members"
                        ),
                    },
                    inventory_slots=parsed,
                    inventory_sha256=sha(response),
                )
                expected_ops: list[tuple[str, list[Any]]] = []
                for slot in selection["selected_slots"]:
                    for detail in ("signatures", "full"):
                        expected_ops.append(
                            (
                                "getBlock",
                                [
                                    slot,
                                    {
                                        "commitment": "finalized",
                                        "transactionDetails": detail,
                                        "encoding": "json",
                                        "rewards": False,
                                        **(
                                            {"maxSupportedTransactionVersion": 1}
                                            if detail == "full"
                                            else {}
                                        ),
                                    },
                                ],
                            )
                        )
                operations.extend(expected_ops)
            elif method == "getBlock":
                if not isinstance(parsed, dict):
                    return _frame_stop("invalid_block_response", provider_io=True)
                if params[1]["transactionDetails"] == "full":
                    signatures_capture = decode(responses[-2])
                    signatures_result = signatures_capture.get("result")
                    full_signatures = _signature_list(parsed, "full")
                    signature_signatures = _signature_list(signatures_result, "signatures")
                    identity_keys = (
                        "blockhash",
                        "previousBlockhash",
                        "parentSlot",
                        "blockTime",
                        "blockHeight",
                    )
                    if (
                        full_signatures is None
                        or signature_signatures != full_signatures
                        or any(
                            signatures_result.get(key) != parsed.get(key) for key in identity_keys
                        )
                    ):
                        return _frame_stop("signature_pair_mismatch", provider_io=True)
    if selection is None or anchor is None:
        return _frame_stop("incomplete_frame_operations", provider_io=True)
    if len(requests) != 2 + 2 * selection["selection_count"]:
        return _frame_stop("incomplete_frame_operations", provider_io=True)
    selected_blocks = []
    for i, slot in enumerate(selection["selected_slots"]):
        signatures = decode(responses[2 + i * 2])
        full = decode(responses[3 + i * 2])
        sig_list = _signature_list(signatures.get("result"), "signatures")
        full_list = _signature_list(full.get("result"), "full")
        if sig_list is None or sig_list != full_list:
            return _frame_stop("signature_pair_mismatch", provider_io=True)
        sig_result, full_result = signatures["result"], full["result"]
        if any(
            sig_result.get(k) != full_result.get(k)
            for k in ("blockhash", "previousBlockhash", "parentSlot", "blockTime", "blockHeight")
        ):
            return _frame_stop("block_identity_mismatch", provider_io=True)
        selected_blocks.append(
            {
                "slot": slot,
                "signatures": full_list,
                "block_identity": {
                    "blockhash": full_result.get("blockhash"),
                    "previousBlockhash": full_result.get("previousBlockhash"),
                    "parentSlot": full_result.get("parentSlot"),
                    "blockTime": full_result.get("blockTime"),
                    "blockHeight": full_result.get("blockHeight"),
                },
            }
        )
    frame_summary = {
        "start": frame,
        "end_exclusive": anchor + 1,
        "anchor_slot": anchor,
        "width": anchor - frame + 1,
        "inventory_sha256": selection["inventory_sha256"],
        "denominator": selection["inventory_denominator"],
        "selection_count": selection["selection_count"],
        "selected_slots": selection["selected_slots"],
        "inclusion_probability": selection["inclusion_probability"],
        "seed": selection["seed"],
        "selection_rule": selection["selection_rule"],
    }
    entries = []
    for i in range(len(requests)):
        entries.append(
            {
                "ordinal": i,
                "request_sha256": sha(writer.read_bytes(f"buckets/{bucket}/requests/{i:02d}.json")),
                "receipt_sha256": sha(canonical(receipts[i])),
                "result_sha256": sha(canonical(results[i])),
                "response_sha256": sha(responses[i]),
            }
        )
    bundle = {
        "schema": schema.BUNDLE_SCHEMA,
        "schema_version": 2,
        "provider": plan["provider"],
        "phase": "frame",
        "frame_index": frame_index,
        "bucket": bucket,
        "reservation_id": reservation_id,
        "plan_sha256": plan_hash,
        "status": "complete",
        "request_count": len(requests),
        "requests": entries,
        "raw_bytes": phase_raw,
        "stored_bytes": phase_raw,
        "bootstrap_slot": None,
        "frame": frame_summary,
        "selected_blocks": selected_blocks,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
        "terminal_status": "frame_complete",
    }
    bundle_raw = canonical(bundle)
    bundle_hash = sha(bundle_raw)
    writer.atomic_write(f"buckets/{bucket}/bundle.json", bundle_raw, create_only=True)
    _append_event(
        writer,
        kind="bundle",
        bucket=bucket,
        at_ns=time.time_ns(),
        payload={"bundle_sha256": bundle_hash},
    )
    try:
        with DescriptorReaderV2(writer.root_fd) as reader:
            checked = _verify_bucket(reader, plan, bucket, frame, require_seal=False)
    except Exception as exc:
        from tools.brain_senses_external_verify_v2 import VerificationError

        if not isinstance(exc, VerificationError):
            raise
        return _frame_stop("independent_bundle_verification_failed", provider_io=True)
    if checked["bundle_sha256"] != bundle_hash:
        return _frame_stop("independent_bundle_verification_failed", provider_io=True)
    seal = {
        "schema": schema.SEAL_SCHEMA,
        "schema_version": 2,
        "bucket": bucket,
        "phase": "frame",
        "frame_index": frame_index,
        "reservation_id": reservation_id,
        "plan_sha256": plan_hash,
        "bundle_sha256": bundle_hash,
        "sealed_ns": time.time_ns(),
        "status": "complete",
    }
    seal_raw = canonical(seal)
    writer.atomic_write(f"buckets/{bucket}/seal.json", seal_raw, create_only=True)
    _append_event(
        writer,
        kind="seal",
        bucket=bucket,
        at_ns=seal["sealed_ns"],
        payload={"seal_sha256": sha(seal_raw)},
    )
    with DescriptorReaderV2(writer.root_fd) as reader:
        _verify_bucket(reader, plan, bucket, frame)
    return {
        "status": "complete",
        "bundle": bundle,
        "seal": seal,
        "provider_io": True,
        "replay_authorized": False,
    }
