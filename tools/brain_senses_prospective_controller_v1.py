#!/usr/bin/env python3
"""Offline durable event ledger for a future finite Senses prospective lease.

This module owns no provider adapter, credential loader, LaunchAgent, scheduler,
or activation decision. It specifies durable ordering and recovery semantics for
future reviewed implementations. Creating a lease here does not authorize RPC.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

SCHEMA = "brain-senses-prospective-controller-v1"
ZERO_HASH = "0" * 64
HEX64 = re.compile(r"^[0-9a-f]{64}$")
EVENTS = {"initialized", "gap", "reservation", "started", "capture", "result", "seal", "stop"}
# All *_ns values are Unix epoch nanoseconds. Byte caps and observations are integer bytes.
UTC_DAY_NS = 86_400_000_000_000


class ControllerError(ValueError):
    """A fail-closed lease, ledger, or transition violation."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_create(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _locked(root: Path):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root_stat = root.lstat()
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_uid != os.getuid()
        or stat.S_IMODE(root_stat.st_mode) != 0o700
    ):
        raise ControllerError("controller_root_owner_or_mode_invalid")
    lock_fd = os.open(
        root / ".prospective-v1.lock",
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    handle = os.fdopen(lock_fd, "r+")
    lock_stat = os.fstat(handle.fileno())
    if (
        not stat.S_ISREG(lock_stat.st_mode)
        or lock_stat.st_uid != os.getuid()
        or stat.S_IMODE(lock_stat.st_mode) != 0o600
    ):
        handle.close()
        raise ControllerError("controller_lock_owner_or_mode_invalid")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise ControllerError("controller_writer_already_active") from exc
    return handle


def validate_plan(plan: dict[str, Any], *, now_ns: int) -> None:
    """Validate a finite source-bound plan; this does not validate approval evidence."""
    caps = plan.get("caps")
    buckets = plan.get("buckets")
    bindings = plan.get("source_sha256")
    predecessor = plan.get("predecessor")
    if (
        plan.get("schema") != SCHEMA
        or not isinstance(plan.get("lease_id"), str)
        or not plan["lease_id"]
        or type(plan.get("created_ns")) is not int
        or type(plan.get("expires_ns")) is not int
        or plan["created_ns"] > now_ns
        or plan["expires_ns"] <= now_ns
        or not isinstance(caps, dict)
        or not isinstance(buckets, list)
        or not buckets
        or not isinstance(bindings, dict)
        or not bindings
        or not isinstance(predecessor, dict)
    ):
        raise ControllerError("plan_identity_or_shape_invalid")
    for key in (
        "requests",
        "raw_bytes",
        "stored_bytes",
        "physical_bytes",
        "width",
        "daily_requests",
        "daily_raw_bytes",
        "daily_stored_bytes",
    ):
        if type(caps.get(key)) is not int or caps[key] <= 0:
            raise ControllerError("plan_cap_invalid")
    if caps["physical_bytes"] < 50 * 1024**3:
        raise ControllerError("physical_disk_floor_below_minimum")
    for digest in bindings.values():
        if not isinstance(digest, str) or not HEX64.fullmatch(digest):
            raise ControllerError("source_binding_invalid")
    for key in ("terminal_receipt_sha256", "stop_sha256"):
        if not isinstance(predecessor.get(key), str) or not HEX64.fullmatch(predecessor[key]):
            raise ControllerError("predecessor_binding_invalid")
    if type(predecessor.get("frontier_slot")) is not int or predecessor["frontier_slot"] < 0:
        raise ControllerError("predecessor_frontier_invalid")
    if not isinstance(predecessor.get("gaps"), list):
        raise ControllerError("predecessor_gaps_invalid")
    seen: set[str] = set()
    previous_start = 0
    previous_end = 0
    previous_end = 0
    for bucket in buckets:
        if (
            not isinstance(bucket, dict)
            or not isinstance(bucket.get("bucket_id"), str)
            or bucket["bucket_id"] in seen
            or bucket.get("kind") not in {"anchor", "frame"}
            or type(bucket.get("start_ns")) is not int
            or type(bucket.get("end_ns")) is not int
            or bucket["start_ns"] <= now_ns
            or bucket["start_ns"] < previous_start
            or bucket["start_ns"] < previous_end
            or bucket["start_ns"] < previous_end
            or bucket["end_ns"] <= bucket["start_ns"]
            or bucket["end_ns"] > plan["expires_ns"]
        ):
            raise ControllerError("scheduled_bucket_invalid")
        seen.add(bucket["bucket_id"])
        previous_start = bucket["start_ns"]
        previous_end = bucket["end_ns"]
        previous_end = bucket["end_ns"]
    if buckets[0]["kind"] != "anchor":
        raise ControllerError("first_bucket_must_be_anchor")
    if any(a["start_ns"] >= b["start_ns"] for a, b in zip(buckets[:-1], buckets[1:], strict=True)):
        raise ControllerError("bucket_schedule_not_strictly_ordered")


def _events(root: Path) -> list[dict[str, Any]]:
    path = root / "events-v1.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    previous = ZERO_HASH
    reservations: dict[str, dict[str, Any]] = {}
    started: set[str] = set()
    captures: set[str] = set()
    results: set[str] = set()
    seals: set[str] = set()
    stopped = False
    for seq, line in enumerate(path.read_bytes().splitlines()):
        event = json.loads(line)
        digest = event.pop("event_sha256", None)
        if (
            event.get("seq") != seq
            or event.get("previous_sha256") != previous
            or digest != _sha(_canonical(event))
            or event.get("kind") not in EVENTS
        ):
            raise ControllerError("ledger_sequence_or_hash_invalid")
        kind = event["kind"]
        rid = event.get("request_id")
        if stopped:
            raise ControllerError("event_after_stop")
        if kind == "initialized":
            if seq != 0:
                raise ControllerError("duplicate_initialization")
        elif seq == 0:
            raise ControllerError("ledger_missing_initialization")
        elif kind == "reservation":
            if not isinstance(rid, str) or rid in reservations:
                raise ControllerError("duplicate_or_invalid_reservation")
            reservations[rid] = event
        elif kind == "started":
            if (
                rid not in reservations
                or rid in started
                or event.get("method") != reservations[rid].get("method")
                or event.get("request_sha256") != reservations[rid].get("request_sha256")
            ):
                raise ControllerError("start_without_unique_reservation")
            started.add(rid)
        elif kind == "capture":
            if (
                rid not in started
                or rid in captures
                or not HEX64.fullmatch(event.get("artifact_sha256", ""))
            ):
                raise ControllerError("capture_without_start_or_hash")
            artifact = Path(event.get("artifact_path", ""))
            if (
                rid not in reservations
                or event.get("raw_bytes") > reservations[rid]["raw_bytes"]
                or event.get("availability_ns") < event.get("receipt_ns")
                or event.get("decision_ns") is not None
                or not artifact.is_file()
                or _sha(artifact.read_bytes()) != event["artifact_sha256"]
            ):
                raise ControllerError("capture_artifact_hash_mismatch")
            captures.add(rid)
        elif kind == "result":
            if rid not in reservations or rid in results:
                raise ControllerError("result_without_unique_reservation")
            if event.get("status") not in {"captured", "failed", "unknown"}:
                raise ControllerError("result_status_invalid")
            if event["status"] == "captured" and rid not in captures:
                raise ControllerError("captured_result_without_artifact")
            if event["status"] == "captured":
                capture = next(
                    e for e in events if e["kind"] == "capture" and e["request_id"] == rid
                )
                start = next(e for e in events if e["kind"] == "started" and e["request_id"] == rid)
                if (
                    event.get("actual_requests", 0) < 1
                    or event.get("actual_raw_bytes") != capture.get("raw_bytes")
                    or event.get("capture_sha256") != capture.get("artifact_sha256")
                    or event.get("receipt_ns", -1) < start.get("at_ns", 0)
                    or event.get("receipt_ns") != capture.get("receipt_ns")
                    or event.get("availability_ns") != capture.get("availability_ns")
                ):
                    raise ControllerError("captured_result_artifact_binding_mismatch")
            results.add(rid)
        elif kind == "seal":
            if rid not in results or rid in seals:
                raise ControllerError("seal_without_unique_result")
            result = next(e for e in events if e["kind"] == "result" and e["request_id"] == rid)
            if event.get("status") != result.get("status") or any(
                event.get(actual) != result.get(actual)
                for actual in ("actual_requests", "actual_raw_bytes", "actual_stored_bytes")
            ):
                raise ControllerError("seal_result_binding_mismatch")
            seals.add(rid)
        elif kind == "stop":
            stopped = True
        previous = digest
        event["event_sha256"] = digest
        events.append(event)
    return events


def initialize(root: Path, plan: dict[str, Any], *, now_ns: int) -> dict[str, Any]:
    """Create an offline/test lease ledger; operational activation is a separate gate."""
    validate_plan(plan, now_ns=now_ns)
    root = root.resolve()
    with _locked(root):
        plan_bytes = _canonical(plan) + b"\n"
        _write_create(root / "plan-v1.json", plan_bytes)
        _append_locked(
            root, {"kind": "initialized", "at_ns": now_ns, "plan_sha256": _sha(plan_bytes)}
        )
    return status(root, now_ns=now_ns)


def _append_locked(root: Path, event: dict[str, Any]) -> dict[str, Any]:
    events = _events(root)
    if (events and events[-1]["kind"] == "stop") or (
        (root / "STOP").exists() and event["kind"] != "stop"
    ):
        raise ControllerError("lease_stopped")
    _validate_next(root, events, event)
    body = {
        **event,
        "seq": len(events),
        "previous_sha256": events[-1]["event_sha256"] if events else ZERO_HASH,
    }
    body["event_sha256"] = _sha(_canonical(body))
    path = root / "events-v1.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "ab") as stream:
        stream.write(_canonical(body) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return body


def _validate_next(root: Path, events: list[dict[str, Any]], event: dict[str, Any]) -> None:
    kind = event["kind"]
    if kind == "initialized":
        if events or event.get("plan_sha256") != _sha((root / "plan-v1.json").read_bytes()):
            raise ControllerError("initialization_plan_binding_invalid")
        return
    if not events:
        raise ControllerError("ledger_missing_initialization")
    reservations = {e["request_id"]: e for e in events if e["kind"] == "reservation"}
    starts = {e["request_id"] for e in events if e["kind"] == "started"}
    captures = {e["request_id"] for e in events if e["kind"] == "capture"}
    results = {e["request_id"]: e for e in events if e["kind"] == "result"}
    seals = {e["request_id"] for e in events if e["kind"] == "seal"}
    rid = event.get("request_id")
    if kind == "reservation":
        plan = _plan(root)
        if not isinstance(rid, str) or rid in reservations:
            raise ControllerError("duplicate_or_invalid_reservation")
        bucket = next(
            (b for b in plan["buckets"] if b["bucket_id"] == event.get("bucket_id")), None
        )
        if (
            bucket is None
            or bucket["kind"] != event.get("bucket_kind")
            or not bucket["start_ns"] <= event["at_ns"] < bucket["end_ns"]
        ):
            raise ControllerError("reservation_bucket_binding_invalid")
        if any(
            existing.get("bucket_id") == event["bucket_id"] for existing in reservations.values()
        ):
            raise ControllerError("bucket_already_reserved_once")
        outstanding = set(reservations) - {
            existing["request_id"] for existing in events if existing["kind"] == "seal"
        }
        if outstanding:
            raise ControllerError("one_outstanding_request_at_a_time")
        caps = plan["caps"]
        if (
            type(event.get("at_ns")) is not int
            or type(event.get("utc_day_index")) is not int
            or event["utc_day_index"] != event["at_ns"] // UTC_DAY_NS
            or type(event.get("requests")) is not int
            or not 1 <= event["requests"] <= caps["requests"]
            or type(event.get("raw_bytes")) is not int
            or not 0 <= event["raw_bytes"] <= caps["raw_bytes"]
            or type(event.get("stored_bytes")) is not int
            or not 0 <= event["stored_bytes"] <= caps["stored_bytes"]
            or type(event.get("width")) is not int
            or not 0 <= event["width"] <= caps["width"]
            or type(event.get("physical_free_bytes")) is not int
            or not isinstance(event.get("method"), str)
            or not event["method"]
            or not HEX64.fullmatch(event.get("request_sha256", ""))
        ):
            raise ControllerError("reservation_quantity_or_unit_invalid")
        known_bucket_ids = {
            existing.get("bucket_id")
            for existing in events
            if existing["kind"] in {"gap", "reservation"}
        }
        if any(
            item["end_ns"] <= event["at_ns"]
            and item["bucket_id"] not in known_bucket_ids
            and item["bucket_id"] != event["bucket_id"]
            for item in plan["buckets"]
        ):
            raise ControllerError("missed_bucket_requires_explicit_gap")
        latest_gap = max((e["seq"] for e in events if e["kind"] == "gap"), default=-1)
        latest_anchor = max(
            (
                e["seq"]
                for e in events
                if e["kind"] == "seal"
                and e.get("bucket_kind") == "anchor"
                and e.get("status") == "captured"
            ),
            default=-1,
        )
        if latest_gap > latest_anchor and event["bucket_kind"] != "anchor":
            raise ControllerError("gap_requires_future_anchor_only")
        if event["bucket_kind"] == "frame" and latest_anchor < 0:
            raise ControllerError("frame_requires_sealed_anchor")
        caps = plan["caps"]
        usage = _usage(events)
        for actual, cap, used in (
            (event["requests"], caps["requests"], usage["requests"]),
            (event["raw_bytes"], caps["raw_bytes"], usage["raw_bytes"]),
            (event["stored_bytes"], caps["stored_bytes"], usage["stored_bytes"]),
        ):
            if used + actual > cap:
                raise ControllerError("lease_budget_exhausted")
        if event.get("physical_free_bytes", 0) < caps["physical_bytes"]:
            raise ControllerError("physical_disk_floor_failed")
        day_usage = _usage_for_utc_day(events, event["utc_day_index"])
        for actual, cap, used in (
            (event["requests"], caps["daily_requests"], day_usage["requests"]),
            (event["raw_bytes"], caps["daily_raw_bytes"], day_usage["raw_bytes"]),
            (event["stored_bytes"], caps["daily_stored_bytes"], day_usage["stored_bytes"]),
        ):
            if used + actual > cap:
                raise ControllerError("utc_day_budget_exhausted")
    elif kind == "started":
        plan = _plan(root)
        reservation = reservations.get(rid)
        bucket = next(
            (
                b
                for b in plan["buckets"]
                if reservation and b["bucket_id"] == reservation["bucket_id"]
            ),
            None,
        )
        if (
            reservation is None
            or rid in starts
            or event["at_ns"] < reservation["at_ns"]
            or event.get("method") != reservation.get("method")
            or event.get("request_sha256") != reservation.get("request_sha256")
            or bucket is None
            or event["at_ns"] >= min(bucket["end_ns"], plan["expires_ns"])
            or event.get("physical_free_bytes", 0) < plan["caps"]["physical_bytes"]
        ):
            raise ControllerError("start_without_valid_reservation_clock_or_disk")
    elif kind == "capture":
        if rid not in starts or rid in captures:
            raise ControllerError("capture_without_unique_start")
        path = Path(event["artifact_path"])
        if not path.is_file() or _sha(path.read_bytes()) != event["artifact_sha256"]:
            raise ControllerError("capture_artifact_hash_mismatch")
    elif kind == "result":
        if rid not in reservations or rid in results:
            raise ControllerError("result_without_unique_reservation")
        reservation = reservations[rid]
        if any(
            event[actual] > reservation[reserved]
            for actual, reserved in (
                ("actual_requests", "requests"),
                ("actual_raw_bytes", "raw_bytes"),
                ("actual_stored_bytes", "stored_bytes"),
            )
        ):
            raise ControllerError("result_exceeds_reservation")
        if event["status"] == "captured" and rid not in captures:
            raise ControllerError("captured_result_without_artifact")
        if event["status"] == "captured" and event.get("actual_requests", 0) < 1:
            raise ControllerError("captured_result_requires_request")
    elif kind == "seal":
        if rid not in results or rid in seals:
            raise ControllerError("seal_without_unique_result")
    elif kind == "gap":
        plan = _plan(root)
        bucket = next(
            (b for b in plan["buckets"] if b["bucket_id"] == event.get("bucket_id")), None
        )
        if (
            bucket is None
            or type(event.get("at_ns")) is not int
            or event["at_ns"] < bucket["end_ns"]
            or not isinstance(event.get("reason"), str)
            or not event["reason"]
        ):
            raise ControllerError("gap_record_invalid")
        if any(
            e.get("bucket_id") == event.get("bucket_id")
            for e in events
            if e["kind"] in {"gap", "reservation"}
        ):
            raise ControllerError("bucket_already_observed_or_reserved")


def _append(root: Path, event: dict[str, Any]) -> dict[str, Any]:
    with _locked(root):
        return _append_locked(root, event)


def _plan(root: Path) -> dict[str, Any]:
    value = json.loads((root / "plan-v1.json").read_bytes())
    if not isinstance(value, dict):
        raise ControllerError("plan_not_object")
    return value


def _verify_plan_binding(root: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    plan = _plan(root)
    if type(plan.get("created_ns")) is not int:
        raise ControllerError("plan_created_time_invalid")
    validate_plan(plan, now_ns=plan["created_ns"])
    plan_bytes = (root / "plan-v1.json").read_bytes()
    if (
        not events
        or events[0].get("kind") != "initialized"
        or events[0].get("plan_sha256") != _sha(plan_bytes)
    ):
        raise ControllerError("initial_plan_binding_invalid")
    return plan


def _usage(events: list[dict[str, Any]]) -> dict[str, int]:
    reservations = {e["request_id"]: e for e in events if e["kind"] == "reservation"}
    seals = {e["request_id"]: e for e in events if e["kind"] == "seal"}
    usage = {"requests": 0, "raw_bytes": 0, "stored_bytes": 0}
    for rid, reservation in reservations.items():
        seal = seals.get(rid)
        if seal is None or seal["status"] != "captured":
            usage["requests"] += reservation["requests"]
            usage["raw_bytes"] += reservation["raw_bytes"]
            usage["stored_bytes"] += reservation["stored_bytes"]
        else:
            usage["requests"] += seal["actual_requests"]
            usage["raw_bytes"] += seal["actual_raw_bytes"]
            usage["stored_bytes"] += seal["actual_stored_bytes"]
    return usage


def _usage_for_utc_day(events: list[dict[str, Any]], day_index: int) -> dict[str, int]:
    ids = {
        event["request_id"]
        for event in events
        if event["kind"] == "reservation" and event["utc_day_index"] == day_index
    }
    day_events = [
        event
        for event in events
        if event["kind"] == "reservation"
        and event["request_id"] in ids
        or event["kind"] == "seal"
        and event["request_id"] in ids
    ]
    return _usage(day_events)


def status(root: Path, *, now_ns: int) -> dict[str, Any]:
    root = root.resolve()
    plan = _plan(root)
    events = _events(root)
    _verify_plan_binding(root, events)
    outstanding = {e["request_id"] for e in events if e["kind"] == "reservation"} - {
        e["request_id"] for e in events if e["kind"] == "seal"
    }
    return {
        "schema": SCHEMA,
        "lease_id": plan["lease_id"],
        "stopped": (root / "STOP").exists() or any(e["kind"] == "stop" for e in events),
        "expired": now_ns >= plan["expires_ns"],
        "event_count": len(events),
        "head_sha256": events[-1]["event_sha256"] if events else ZERO_HASH,
        "usage": _usage(events),
        "outstanding_request_ids": sorted(outstanding),
        "recovery_required": bool(outstanding),
    }


def record_gap(root: Path, *, bucket_id: str, at_ns: int, reason: str) -> dict[str, Any]:
    plan = _plan(root)
    bucket = next((b for b in plan["buckets"] if b["bucket_id"] == bucket_id), None)
    if bucket is None or not bucket["start_ns"] <= at_ns or at_ns < bucket["end_ns"]:
        raise ControllerError("gap_must_be_recorded_after_missed_bucket")
    return _append(root, {"kind": "gap", "bucket_id": bucket_id, "at_ns": at_ns, "reason": reason})


def reserve(
    root: Path,
    *,
    request_id: str,
    bucket_id: str,
    method: str,
    request_sha256: str,
    requests: int,
    raw_bytes: int,
    stored_bytes: int,
    width: int,
    at_ns: int,
    physical_free_bytes: int,
) -> dict[str, Any]:
    root = root.resolve()
    plan = _plan(root)
    events = _events(root)
    bucket = next((b for b in plan["buckets"] if b["bucket_id"] == bucket_id), None)
    caps = plan["caps"]
    if bucket is None or not bucket["start_ns"] <= at_ns < bucket["end_ns"]:
        raise ControllerError("outside_pinned_bucket_window")
    if not method or not HEX64.fullmatch(request_sha256):
        raise ControllerError("request_identity_invalid")
    if at_ns >= plan["expires_ns"]:
        raise ControllerError("lease_expired")
    if any(e["kind"] == "gap" for e in events if e.get("bucket_id") == bucket_id):
        raise ControllerError("missed_bucket_is_gap")
    if any(e["kind"] == "stop" for e in events):
        raise ControllerError("lease_stopped")
    known_bucket_ids = {e.get("bucket_id") for e in events if e["kind"] in {"gap", "reservation"}}
    if bucket_id in known_bucket_ids:
        raise ControllerError("bucket_already_observed_or_reserved")
    missed_unrecorded = [
        candidate["bucket_id"]
        for candidate in plan["buckets"]
        if candidate["end_ns"] <= at_ns
        and candidate["bucket_id"] not in known_bucket_ids
        and candidate["bucket_id"] != bucket_id
    ]
    if missed_unrecorded:
        raise ControllerError("missed_bucket_requires_explicit_gap")
    latest_gap = max((e["seq"] for e in events if e["kind"] == "gap"), default=-1)
    latest_anchor = max(
        (
            e["seq"]
            for e in events
            if e["kind"] == "seal"
            and e.get("bucket_kind") == "anchor"
            and e.get("status") == "captured"
        ),
        default=-1,
    )
    if latest_gap > latest_anchor and bucket["kind"] != "anchor":
        raise ControllerError("gap_requires_future_anchor_only")
    if type(requests) is not int or not 1 <= requests <= caps["requests"]:
        raise ControllerError("request_reservation_outside_bound")
    if type(raw_bytes) is not int or not 0 <= raw_bytes <= caps["raw_bytes"]:
        raise ControllerError("raw_reservation_outside_bound")
    if type(stored_bytes) is not int or not 0 <= stored_bytes <= caps["stored_bytes"]:
        raise ControllerError("stored_reservation_outside_bound")
    if type(width) is not int or not 0 <= width <= caps["width"]:
        raise ControllerError("slot_width_outside_bound")
    if physical_free_bytes < caps["physical_bytes"]:
        raise ControllerError("physical_disk_floor_failed")
    usage = _usage(events)
    if any(
        usage[k] + v > caps[k]
        for k, v in (
            ("requests", requests),
            ("raw_bytes", raw_bytes),
            ("stored_bytes", stored_bytes),
        )
    ):
        raise ControllerError("lease_budget_exhausted")
    day_usage = _usage_for_utc_day(events, at_ns // UTC_DAY_NS)
    if any(
        day_usage[metric] + amount > caps[cap]
        for metric, cap, amount in (
            ("requests", "daily_requests", requests),
            ("raw_bytes", "daily_raw_bytes", raw_bytes),
            ("stored_bytes", "daily_stored_bytes", stored_bytes),
        )
    ):
        raise ControllerError("utc_day_budget_exhausted")
    if bucket["kind"] == "frame" and latest_anchor < 0:
        raise ControllerError("frame_requires_sealed_anchor")
    return _append(
        root,
        {
            "kind": "reservation",
            "request_id": request_id,
            "bucket_id": bucket_id,
            "bucket_kind": bucket["kind"],
            "method": method,
            "request_sha256": request_sha256,
            "requests": requests,
            "raw_bytes": raw_bytes,
            "stored_bytes": stored_bytes,
            "width": width,
            "at_ns": at_ns,
            "utc_day_index": at_ns // UTC_DAY_NS,
            "physical_free_bytes": physical_free_bytes,
        },
    )


def mark_started(
    root: Path, *, request_id: str, at_ns: int, physical_free_bytes: int
) -> dict[str, Any]:
    events = _events(root)
    reservation = next(
        (e for e in events if e["kind"] == "reservation" and e["request_id"] == request_id), None
    )
    if reservation is None or not reservation["at_ns"] <= at_ns:
        raise ControllerError("start_without_reservation_or_bad_time")
    plan = _plan(root)
    bucket = next(b for b in plan["buckets"] if b["bucket_id"] == reservation["bucket_id"])
    if at_ns >= min(bucket["end_ns"], plan["expires_ns"]):
        raise ControllerError("request_start_outside_window")
    if physical_free_bytes < plan["caps"]["physical_bytes"]:
        raise ControllerError("physical_disk_floor_failed_before_dispatch")
    return _append(
        root,
        {
            "kind": "started",
            "request_id": request_id,
            "at_ns": at_ns,
            "physical_free_bytes": physical_free_bytes,
            "method": reservation["method"],
            "request_sha256": reservation["request_sha256"],
        },
    )


def record_capture(
    root: Path,
    *,
    request_id: str,
    artifact_path: Path,
    event_time: dict[str, Any],
    receipt_ns: int,
    availability_ns: int,
) -> dict[str, Any]:
    """Bind an already fsynced immutable raw artifact and distinct time coordinates."""
    path = artifact_path.resolve(strict=True)
    data = path.read_bytes()
    if path.stat().st_size != len(data) or availability_ns < receipt_ns:
        raise ControllerError("capture_artifact_or_time_invalid")
    reservation = next(
        (e for e in _events(root) if e["kind"] == "reservation" and e["request_id"] == request_id),
        None,
    )
    if reservation is None or len(data) > reservation["raw_bytes"]:
        raise ControllerError("capture_exceeds_reservation")
    return _append(
        root,
        {
            "kind": "capture",
            "request_id": request_id,
            "artifact_path": str(path),
            "artifact_sha256": _sha(data),
            "raw_bytes": len(data),
            "event_time": event_time,
            "receipt_ns": receipt_ns,
            "availability_ns": availability_ns,
            "decision_ns": None,
        },
    )


def record_result(
    root: Path,
    *,
    request_id: str,
    status_value: str,
    actual_requests: int,
    actual_raw_bytes: int,
    actual_stored_bytes: int,
    receipt_ns: int,
    availability_ns: int | None,
) -> dict[str, Any]:
    if status_value not in {"captured", "failed", "unknown"}:
        raise ControllerError("result_status_invalid")
    if any(
        type(v) is not int or v < 0
        for v in (actual_requests, actual_raw_bytes, actual_stored_bytes)
    ):
        raise ControllerError("actual_accounting_invalid")
    reservation = next(
        (e for e in _events(root) if e["kind"] == "reservation" and e["request_id"] == request_id),
        None,
    )
    if (
        reservation is None
        or actual_requests > reservation["requests"]
        or actual_raw_bytes > reservation["raw_bytes"]
        or actual_stored_bytes > reservation["stored_bytes"]
    ):
        raise ControllerError("actual_accounting_exceeds_reservation")
    if status_value == "captured" and availability_ns is None:
        raise ControllerError("capture_availability_time_required")
    capture = next(
        (e for e in _events(root) if e["kind"] == "capture" and e["request_id"] == request_id),
        None,
    )
    if status_value == "captured" and (capture is None or actual_raw_bytes != capture["raw_bytes"]):
        raise ControllerError("captured_raw_byte_denominator_mismatch")
    if availability_ns is not None and availability_ns < receipt_ns:
        raise ControllerError("availability_precedes_receipt")
    row = _append(
        root,
        {
            "kind": "result",
            "request_id": request_id,
            "status": status_value,
            "actual_requests": actual_requests,
            "actual_raw_bytes": actual_raw_bytes,
            "actual_stored_bytes": actual_stored_bytes,
            "receipt_ns": receipt_ns,
            "availability_ns": availability_ns,
            "capture_sha256": capture["artifact_sha256"] if capture else None,
        },
    )
    if status_value != "captured":
        seal(root, request_id=request_id, at_ns=receipt_ns)
        stop(
            root,
            reason="ambiguous_started_request_no_replay"
            if status_value == "unknown"
            else "provider_request_failed",
            at_ns=receipt_ns,
        )
    return row


def seal(root: Path, *, request_id: str, at_ns: int) -> dict[str, Any]:
    events = _events(root)
    result = next(
        (e for e in events if e["kind"] == "result" and e["request_id"] == request_id), None
    )
    if result is None or at_ns < result["receipt_ns"]:
        raise ControllerError("seal_without_result_or_bad_time")
    reservation = next(
        e for e in events if e["kind"] == "reservation" and e["request_id"] == request_id
    )
    return _append(
        root,
        {
            "kind": "seal",
            "request_id": request_id,
            "bucket_id": reservation["bucket_id"],
            "bucket_kind": reservation["bucket_kind"],
            "status": result["status"],
            "actual_requests": result["actual_requests"],
            "actual_raw_bytes": result["actual_raw_bytes"],
            "actual_stored_bytes": result["actual_stored_bytes"],
            "at_ns": at_ns,
        },
    )


def stop(root: Path, *, reason: str, at_ns: int) -> dict[str, Any]:
    root = root.resolve()
    with _locked(root):
        return _stop_locked(root, reason=reason, at_ns=at_ns)


def _stop_locked(root: Path, *, reason: str, at_ns: int) -> dict[str, Any]:
    prior = root / "STOP"
    if prior.exists():
        row = json.loads(prior.read_bytes())
    else:
        row = {"schema": SCHEMA, "status": "stopped", "reason": reason, "stopped_ns": at_ns}
        _write_create(prior, _canonical(row) + b"\n")
    events = _events(root)
    if events and not any(event["kind"] == "stop" for event in events):
        _append_locked(root, {"kind": "stop", "reason": row["reason"], "at_ns": row["stopped_ns"]})
    return row


def recover(root: Path, *, at_ns: int) -> dict[str, Any]:
    """Validate only local state. Never calls an adapter or replays an ambiguous start."""
    root = root.resolve()
    try:
        plan = _plan(root)
        events = _events(root)
        plan = _verify_plan_binding(root, events)
        stop_path = root / "STOP"
        stop_event = next((event for event in events if event["kind"] == "stop"), None)
        if stop_path.exists() or stop_event is not None:
            marker = json.loads(stop_path.read_bytes()) if stop_path.exists() else stop_event
            if stop_event is not None and (
                marker.get("reason") != stop_event.get("reason")
                or marker.get("stopped_ns") != stop_event.get("at_ns")
            ):
                raise ControllerError("stop_marker_ledger_mismatch")
            stop(root, reason=marker["reason"], at_ns=marker["stopped_ns"])
            return status(root, now_ns=at_ns)
        starts = {e["request_id"] for e in events if e["kind"] == "started"}
        results = {e["request_id"] for e in events if e["kind"] == "result"}
        ambiguous = starts - results
        outstanding = {e["request_id"] for e in events if e["kind"] == "reservation"} - {
            e["request_id"] for e in events if e["kind"] == "seal"
        }
        if ambiguous or outstanding:
            stop(
                root,
                reason="ambiguous_started_request_no_replay"
                if ambiguous
                else "outstanding_reservation_requires_review",
                at_ns=at_ns,
            )
            raise ControllerError(
                "ambiguous_started_request_no_replay"
                if ambiguous
                else "outstanding_reservation_requires_review"
            )
        if at_ns >= plan["expires_ns"] and not any(e["kind"] == "stop" for e in events):
            stop(root, reason="lease_expired", at_ns=at_ns)
        return status(root, now_ns=at_ns)
    except Exception as exc:
        if not (root / "STOP").exists():
            stop(root, reason="local_evidence_uncertain", at_ns=at_ns)
        if isinstance(exc, ControllerError):
            raise
        raise ControllerError("local_evidence_uncertain") from exc


def _transition_lock(parent: Path):
    """Exclusive local-filesystem lock spanning predecessor closure and successor creation."""
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = parent / ".brain-senses-prospective-v1.transition.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    handle = os.fdopen(fd, "r+")
    metadata = os.fstat(fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        handle.close()
        raise ControllerError("transition_lock_owner_or_mode_invalid")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise ControllerError("lease_transition_already_active") from exc
    return handle


def _plan_sha(root: Path) -> str:
    return _sha((root / "plan-v1.json").read_bytes())


def _verify_terminal(root: Path, terminal: dict[str, Any]) -> dict[str, Any]:
    body = dict(terminal)
    supplied = body.pop("terminal_sha256", None)
    events = _events(root)
    stop_bytes = (root / "STOP").read_bytes()
    stop_marker = json.loads(stop_bytes)
    if (
        supplied != _sha(_canonical(body))
        or terminal.get("status") != "lease_closed"
        or terminal.get("plan_sha256") != _plan_sha(root)
        or terminal.get("stop_sha256") != _sha(stop_bytes)
        or not events
        or terminal.get("ledger_head_sha256") != events[-1].get("event_sha256")
        or stop_marker.get("reason") != "finite_lease_complete"
        or terminal.get("closed_ns") != stop_marker.get("stopped_ns")
    ):
        raise ControllerError("terminal_receipt_or_stop_binding_invalid")
    return terminal


def close_lease(root: Path, *, frontier_slot: int, at_ns: int) -> dict[str, Any]:
    """Seal one finite generation and its prospective frontier/gaps, without renewal."""
    root = root.resolve()
    with _locked(root):
        events = _events(root)
        plan = _verify_plan_binding(root, events)
        outstanding = {e["request_id"] for e in events if e["kind"] == "reservation"} - {
            e["request_id"] for e in events if e["kind"] == "seal"
        }
        if outstanding:
            raise ControllerError("cannot_close_with_outstanding_request")
        if (root / "STOP").exists():
            prior_stop = json.loads((root / "STOP").read_bytes())
            if prior_stop.get("reason") != "finite_lease_complete":
                raise ControllerError("abnormal_stop_cannot_auto_close_or_renew")
        prior_stop_event = next((e for e in events if e["kind"] == "stop"), None)
        if (
            prior_stop_event is not None
            and prior_stop_event.get("reason") != "finite_lease_complete"
        ):
            raise ControllerError("abnormal_stop_cannot_auto_close_or_renew")
        prior_frontier = plan["predecessor"]["frontier_slot"]
        if type(frontier_slot) is not int or frontier_slot < prior_frontier:
            raise ControllerError("terminal_frontier_regression")
        captured_anchor_slots = []
        for capture in (e for e in events if e["kind"] == "capture"):
            reservation = next(
                e
                for e in events
                if e["kind"] == "reservation" and e["request_id"] == capture["request_id"]
            )
            result = next(
                e
                for e in events
                if e["kind"] == "result" and e["request_id"] == capture["request_id"]
            )
            if reservation["bucket_kind"] == "anchor" and result["status"] == "captured":
                slot = capture.get("event_time", {}).get("slot")
                if type(slot) is not int:
                    raise ControllerError("captured_anchor_slot_missing")
                captured_anchor_slots.append(slot)
        observed_frontier = max(captured_anchor_slots, default=prior_frontier - 1) + 1
        if frontier_slot != max(prior_frontier, observed_frontier):
            raise ControllerError("terminal_frontier_not_bound_to_anchor_or_predecessor")
        if (root / "TERMINAL.json").exists():
            terminal = json.loads((root / "TERMINAL.json").read_bytes())
            return _verify_terminal(root, terminal)
        _stop_locked(root, reason="finite_lease_complete", at_ns=at_ns)
        events = _events(root)
        stop_bytes = (root / "STOP").read_bytes()
        gaps = [
            *plan["predecessor"].get("gaps", []),
            *[
                {"bucket_id": e["bucket_id"], "at_ns": e["at_ns"], "reason": e["reason"]}
                for e in events
                if e["kind"] == "gap"
            ],
        ]
        body = {
            "schema": f"{SCHEMA}-terminal-v1",
            "status": "lease_closed",
            "lease_id": plan["lease_id"],
            "closed_ns": at_ns,
            "plan_sha256": _plan_sha(root),
            "stop_sha256": _sha(stop_bytes),
            "ledger_head_sha256": events[-1]["event_sha256"],
            "frontier_slot": frontier_slot,
            "gaps": gaps,
            "usage": _usage(events),
        }
        body["terminal_sha256"] = _sha(_canonical(body))
        _write_create(root / "TERMINAL.json", _canonical(body) + b"\n")
        return body


def initialize_successor(
    previous_root: Path,
    successor_root: Path,
    successor_plan: dict[str, Any],
    *,
    frontier_slot: int,
    at_ns: int,
) -> dict[str, Any]:
    """Close predecessor and create a reviewed successor under one local writer lock.

    A crash between these operations leaves a sealed predecessor and no successor;
    it never reopens the old lease or renews automatically.
    """
    previous_root = previous_root.resolve()
    successor_root = successor_root.resolve()
    if previous_root.parent != successor_root.parent or previous_root == successor_root:
        raise ControllerError("successor_must_share_local_transition_store")
    with _transition_lock(previous_root.parent):
        prior = close_lease(previous_root, frontier_slot=frontier_slot, at_ns=at_ns)
        if prior.get("status") != "lease_closed" or prior.get("frontier_slot") != frontier_slot:
            raise ControllerError("predecessor_terminal_receipt_invalid")
        _verify_terminal(previous_root, prior)
        plan = json.loads(_canonical(successor_plan))
        review = plan.get("review")
        if (
            not isinstance(review, dict)
            or not isinstance(review.get("review_id"), str)
            or not review["review_id"]
            or not HEX64.fullmatch(review.get("decision_sha256", ""))
        ):
            raise ControllerError("successor_requires_new_review_decision_binding")
        predecessor = dict(plan.get("predecessor", {}))
        predecessor.update(
            {
                "lease_id": prior["lease_id"],
                "terminal_path": str(previous_root / "TERMINAL.json"),
                "terminal_receipt_sha256": _sha((previous_root / "TERMINAL.json").read_bytes()),
                "stop_path": str(previous_root / "STOP"),
                "stop_sha256": _sha((previous_root / "STOP").read_bytes()),
                "frontier_slot": frontier_slot,
                "gaps": prior["gaps"],
            }
        )
        plan["predecessor"] = predecessor
        return initialize(successor_root, plan, now_ns=at_ns)
