#!/usr/bin/env python3
"""Offline parent-envelope bridge for existing Senses v3 child journals.

This module has no provider client, scheduler, credential loader, or activation path.
The child runner is injected by its caller; tests use only a fake RPC implementation.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA = "brain-senses-prospective-operation-envelope-v2"
ZERO = "0" * 64
EVENTS = {"initialized", "gap", "reservation", "started", "bundle", "seal", "stop"}
UTC_DAY_NS = 86_400_000_000_000
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class EnvelopeError(ValueError):
    """Fail-closed parent-envelope or child-bundle error."""


class _ScheduledClient:
    def __init__(self, client: Any, *, root: Path, start_ns: int, end_ns: int, floor: int):
        self._client = client
        self._root = root
        self._start_ns = start_ns
        self._end_ns = end_ns
        self._floor = floor

    def fetch(self, method: str, params: list[Any]) -> Any:
        now = time.time_ns()
        free = shutil.disk_usage(self._root).free
        if not self._start_ns <= now < self._end_ns:
            raise EnvelopeError("child_request_outside_prospective_window")
        if free < self._floor:
            raise EnvelopeError("child_request_physical_disk_floor_failed")
        return self._client.fetch(method, params)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise EnvelopeError("json_object_required")
    return value


def _create(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _writer(root: Path):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = root.lstat()
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o700
    ):
        raise EnvelopeError("parent_root_owner_or_mode_invalid")
    fd = os.open(
        root / ".envelope-v2.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    lock = os.fdopen(fd, "r+")
    st = os.fstat(lock.fileno())
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o600
    ):
        lock.close()
        raise EnvelopeError("parent_lock_owner_or_mode_invalid")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise EnvelopeError("parent_writer_already_active") from exc
    return lock


def _events(root: Path) -> list[dict[str, Any]]:
    path = root / "events-v2.jsonl"
    if not path.exists():
        return []
    data = path.read_bytes()
    if not data.endswith(b"\n"):
        raise EnvelopeError("parent_event_tail_incomplete")
    out: list[dict[str, Any]] = []
    prior = ZERO
    for seq, line in enumerate(data.splitlines()):
        row = json.loads(line)
        supplied = row.pop("event_sha256", None)
        if (
            row.get("seq") != seq
            or row.get("previous_sha256") != prior
            or supplied != _sha(_canonical(row))
        ):
            raise EnvelopeError("parent_event_chain_invalid")
        row["event_sha256"] = supplied
        if row.get("kind") not in EVENTS:
            raise EnvelopeError("parent_event_kind_invalid")
        out.append(row)
        prior = supplied
    return out


def _append_locked(root: Path, event: dict[str, Any]) -> dict[str, Any]:
    rows = _events(root)
    if rows and rows[-1]["kind"] == "stop":
        raise EnvelopeError("parent_stopped")
    if event["kind"] == "initialized":
        if rows or event.get("plan_sha256") != _sha((root / "plan-v2.json").read_bytes()):
            raise EnvelopeError("parent_initialization_binding_invalid")
    elif not rows:
        raise EnvelopeError("parent_plan_missing")
    else:
        rid = event.get("operation_id")
        reservations = {r["operation_id"]: r for r in rows if r["kind"] == "reservation"}
        started = {r["operation_id"] for r in rows if r["kind"] == "started"}
        bundled = {r["operation_id"] for r in rows if r["kind"] == "bundle"}
        sealed = {r["operation_id"] for r in rows if r["kind"] == "seal"}
        if event["kind"] == "reservation":
            plan = _json(root / "plan-v2.json")
            bucket = next(
                (b for b in plan["buckets"] if b["bucket_id"] == event.get("bucket_id")),
                None,
            )
            if (
                bucket is None
                or bucket["kind"] != event.get("bucket_kind")
                or type(event.get("at_ns")) is not int
                or not bucket["start_ns"]
                <= event["at_ns"]
                < min(bucket["end_ns"], plan["expires_ns"])
                or event.get("utc_day_index") != event["at_ns"] // UTC_DAY_NS
                or event.get("physical_free_bytes", 0) < plan["caps"]["physical_bytes"]
                or not isinstance(event.get("child_path"), str)
                or any(
                    r.get("child_path") == event.get("child_path") for r in reservations.values()
                )
            ):
                raise EnvelopeError("reservation_schedule_or_disk_binding_invalid")
            if any(r["bucket_id"] == event.get("bucket_id") for r in reservations.values()):
                raise EnvelopeError("bucket_reserved_once")
            if any(rid not in sealed for rid in reservations):
                raise EnvelopeError("parent_operation_outstanding")
        elif event["kind"] == "started":
            reservation = reservations.get(rid)
            if (
                not reservation
                or rid in started
                or event.get("child_plan_sha256") != reservation["child_plan_sha256"]
            ):
                raise EnvelopeError("start_not_bound_to_unique_reservation")
        elif event["kind"] == "bundle":
            if (
                rid not in started
                or rid in bundled
                or event.get("child_plan_sha256") != reservations[rid]["child_plan_sha256"]
            ):
                raise EnvelopeError("bundle_without_unique_start")
        elif event["kind"] == "seal":
            if (
                rid not in bundled
                or rid in sealed
                or event.get("bundle_sha256")
                != next(
                    r["bundle_sha256"]
                    for r in rows
                    if r["kind"] == "bundle" and r["operation_id"] == rid
                )
            ):
                raise EnvelopeError("seal_without_verified_bundle")
            if event.get("status") != "captured_bundle":
                raise EnvelopeError("seal_status_invalid")
        elif event["kind"] == "gap":
            if any(operation_id not in sealed for operation_id in reservations):
                raise EnvelopeError("cannot_gap_while_operation_outstanding")
            plan = _json(root / "plan-v2.json")
            bucket = next(
                (b for b in plan["buckets"] if b["bucket_id"] == event.get("bucket_id")),
                None,
            )
            if (
                bucket is None
                or type(event.get("at_ns")) is not int
                or event["at_ns"] < bucket["end_ns"]
                or not isinstance(event.get("reason"), str)
                or not event["reason"]
            ):
                raise EnvelopeError("gap_schedule_or_reason_invalid")
            if any(r["bucket_id"] == event.get("bucket_id") for r in reservations.values()) or any(
                r.get("bucket_id") == event.get("bucket_id") for r in rows if r["kind"] == "gap"
            ):
                raise EnvelopeError("bucket_already_reserved_or_gapped")
        elif event["kind"] == "stop":
            pass
    body = {
        **event,
        "seq": len(rows),
        "previous_sha256": rows[-1]["event_sha256"] if rows else ZERO,
    }
    body["event_sha256"] = _sha(_canonical(body))
    path = root / "events-v2.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "ab") as stream:
        stream.write(_canonical(body) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return body


def initialize(root: Path, plan: dict[str, Any]) -> None:
    """Create a new versioned envelope plan. This does not authorize acquisition."""
    caps = plan.get("caps")
    buckets = plan.get("buckets")
    if (
        plan.get("schema") != SCHEMA
        or not isinstance(plan.get("lease_id"), str)
        or not plan["lease_id"]
        or type(plan.get("created_ns")) is not int
        or type(plan.get("expires_ns")) is not int
        or plan["expires_ns"] <= plan["created_ns"]
        or not isinstance(caps, dict)
        or not isinstance(buckets, list)
        or not buckets
    ):
        raise EnvelopeError("plan_shape_invalid")
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
    if any(type(caps.get(key)) is not int or caps[key] <= 0 for key in cap_names):
        raise EnvelopeError("plan_cap_invalid")
    if caps["physical_bytes"] < 50 * 1024**3:
        raise EnvelopeError("physical_floor_below_minimum")
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
        raise EnvelopeError("source_hash_binding_invalid")
    seen: set[str] = set()
    previous_end = 0
    for bucket in buckets:
        if (
            not isinstance(bucket, dict)
            or not isinstance(bucket.get("bucket_id"), str)
            or not bucket["bucket_id"]
            or bucket["bucket_id"] in seen
            or bucket.get("kind") not in {"anchor", "frame"}
            or type(bucket.get("start_ns")) is not int
            or type(bucket.get("end_ns")) is not int
            or bucket["start_ns"] < plan["created_ns"]
            or bucket["start_ns"] < previous_end
            or bucket["end_ns"] <= bucket["start_ns"]
            or bucket["end_ns"] > plan["expires_ns"]
        ):
            raise EnvelopeError("bucket_schedule_invalid")
        seen.add(bucket["bucket_id"])
        previous_end = bucket["end_ns"]
    if buckets[0]["kind"] != "anchor":
        raise EnvelopeError("first_bucket_must_be_anchor")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _writer(root):
        payload = _canonical(plan) + b"\n"
        _create(root / "plan-v2.json", payload)
        _append_locked(
            root, {"kind": "initialized", "plan_sha256": _sha(payload), "at_ns": time.time_ns()}
        )


def _frontier_before(
    root: Path, plan: dict[str, Any], rows: list[dict[str, Any]]
) -> tuple[int, bool]:
    frontier = plan["predecessor"]["frontier_slot"]
    reset_required = bool(plan["predecessor"].get("gaps"))
    reservations = {row["operation_id"]: row for row in rows if row["kind"] == "reservation"}
    bundles = {row["operation_id"]: row for row in rows if row["kind"] == "bundle"}
    sealed = {row["operation_id"] for row in rows if row["kind"] == "seal"}
    for row in rows:
        if row["kind"] == "gap":
            reset_required = True
        elif row["kind"] == "seal" and row.get("status") == "captured_bundle":
            oid = row["operation_id"]
            reservation = reservations[oid]
            bundle_row = bundles[oid]
            bundle = _json(root / bundle_row["bundle_path"])
            if reservation["bucket_kind"] == "anchor":
                frontier = bundle["frontier_after"]
                reset_required = False
            else:
                if reset_required or bundle.get("frontier_before") != frontier:
                    raise EnvelopeError("sealed_frame_frontier_lineage_invalid")
                frontier = bundle["frontier_after"]
    if any(row["operation_id"] not in sealed for row in rows if row["kind"] == "reservation"):
        raise EnvelopeError("parent_operation_outstanding")
    return frontier, reset_required


def reserve_and_run(
    root: Path,
    *,
    bucket_id: str,
    operation_id: str,
    child_root: Path,
    child_plan: dict[str, Any],
    runner: Callable[..., dict[str, Any]],
    clients: dict[str, Any],
    crash_at: str | None = None,
) -> dict[str, Any]:
    """Durably bind and start one child; never invokes a live provider itself."""
    root = root.resolve()
    child_root = child_root.resolve()
    if not isinstance(operation_id, str) or not _SAFE_ID.fullmatch(operation_id):
        raise EnvelopeError("operation_id_invalid")
    from tools.brain_senses_block_epoch_v3 import PROTOCOL as CHILD_PROTOCOL
    from tools.brain_senses_block_epoch_v3 import SCHEMA_VERSION as CHILD_SCHEMA_VERSION
    from tools.brain_senses_block_epoch_v3 import run_epoch

    if runner is not run_epoch:
        raise EnvelopeError("child_runner_not_pinned_to_v3")
    if (
        child_plan.get("protocol") != CHILD_PROTOCOL
        or child_plan.get("schema_version") != CHILD_SCHEMA_VERSION
        or child_plan.get("provider") != "helius-free"
    ):
        raise EnvelopeError("child_plan_not_v3")
    try:
        child_root.relative_to(root)
    except ValueError as exc:
        raise EnvelopeError("child_root_outside_parent") from exc
    plan = _json(root / "plan-v2.json")
    bucket = next((b for b in plan["buckets"] if b["bucket_id"] == bucket_id), None)
    rows = _events(root)
    prior = next(
        (r for r in rows if r["kind"] == "reservation" and r["operation_id"] == operation_id),
        None,
    )
    prior_sealed = any(r["kind"] == "seal" and r.get("operation_id") == operation_id for r in rows)
    if rows and rows[-1]["kind"] == "stop":
        raise EnvelopeError("parent_stopped")
    if prior_sealed:
        raise EnvelopeError("operation_identity_already_used_no_replay")
    at_ns = time.time_ns()
    physical_free_bytes = shutil.disk_usage(root).free
    if bucket is None or not bucket["start_ns"] <= at_ns < bucket["end_ns"]:
        if prior is not None:
            _stop(root, operation_id, bucket_id, "resume_outside_bucket_no_replay")
        raise EnvelopeError("outside_bucket_window")
    if physical_free_bytes < plan["caps"]["physical_bytes"]:
        if prior is not None:
            _stop(root, operation_id, bucket_id, "resume_disk_floor_failed_no_replay")
        raise EnvelopeError("physical_disk_floor_failed")
    n = 1 if bucket["kind"] == "anchor" else 34
    if child_plan.get("bootstrap") != (bucket["kind"] == "anchor"):
        raise EnvelopeError("child_plan_kind_mismatch")
    if bucket["kind"] == "frame" and child_plan.get("selection_count") != 16:
        raise EnvelopeError("frame_contract_requires_16_selected_slots")
    if child_plan.get("request_budget", 0) < n:
        raise EnvelopeError("child_request_budget_below_contract")
    if child_plan.get("minimum_free_disk_bytes", 0) < plan["caps"]["physical_bytes"]:
        raise EnvelopeError("child_per_request_disk_floor_below_parent_floor")
    if child_plan.get("raw_byte_budget", 0) <= 0 or child_plan.get("stored_byte_budget", 0) <= 0:
        raise EnvelopeError("child_byte_envelope_missing")
    if not bucket["start_ns"] <= at_ns < min(bucket["end_ns"], plan["expires_ns"]):
        raise EnvelopeError("outside_lease_or_bucket_window")
    if at_ns < plan["created_ns"]:
        raise EnvelopeError("operation_before_lease_creation")
    width = child_plan.get("frame_width_cap", 0)
    if type(width) is not int or width > plan["caps"]["width"]:
        raise EnvelopeError("child_width_cap_exceeds_lease")
    child_root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    parent_stat = child_root.parent.lstat()
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.getuid()
        or stat.S_IMODE(parent_stat.st_mode) != 0o700
    ):
        raise EnvelopeError("child_parent_owner_or_mode_invalid")
    child_root.mkdir(exist_ok=True, mode=0o700)
    child_stat = child_root.lstat()
    if (
        not stat.S_ISDIR(child_stat.st_mode)
        or child_stat.st_uid != os.getuid()
        or stat.S_IMODE(child_stat.st_mode) != 0o700
    ):
        raise EnvelopeError("child_root_owner_or_mode_invalid")
    plan_path = child_root / "epoch-plan.json"
    plan_bytes = _canonical(child_plan) + b"\n"
    if plan_path.exists():
        if plan_path.read_bytes() != plan_bytes:
            raise EnvelopeError("child_plan_already_bound_differently")
    else:
        _create(plan_path, plan_bytes)
    plan_hash = _sha(plan_bytes)
    request_hash = _sha(
        _canonical({"bucket_id": bucket_id, "child_plan_sha256": plan_hash, "request_count": n})
    )
    with _writer(root):
        rows = _events(root)
        if any(r["kind"] == "stop" for r in rows):
            raise EnvelopeError("parent_stopped")
        reservations = {row["operation_id"]: row for row in rows if row["kind"] == "reservation"}
        existing = reservations.get(operation_id)
        started_ids = {row["operation_id"] for row in rows if row["kind"] == "started"}
        sealed_ids = {row["operation_id"] for row in rows if row["kind"] == "seal"}
        if existing is not None:
            if (
                existing.get("bucket_id") != bucket_id
                or existing.get("request_sha256") != request_hash
                or existing.get("child_plan_sha256") != plan_hash
                or operation_id in sealed_ids
            ):
                raise EnvelopeError("operation_identity_already_used_no_replay")
        else:
            child_rel = str(child_root.relative_to(root))
            if any(r.get("child_path") == child_rel for r in reservations.values()):
                raise EnvelopeError("child_root_already_bound_to_operation")
            frontier, reset_required = _frontier_before(root, plan, rows)
            if bucket["kind"] == "frame":
                if reset_required:
                    raise EnvelopeError("gap_requires_future_anchor_only")
                if child_plan.get("next_start") != frontier:
                    raise EnvelopeError("frame_does_not_start_at_next_unobserved_slot")
            day = at_ns // UTC_DAY_NS
            seals = {r["operation_id"]: r for r in rows if r["kind"] == "seal"}
            used = {"requests": 0, "raw_bytes": 0, "stored_bytes": 0}
            daily = {"requests": 0, "raw_bytes": 0, "stored_bytes": 0}
            for prior in (r for r in rows if r["kind"] == "reservation"):
                seal = seals.get(prior["operation_id"])
                amounts = {
                    key: seal.get("actual_" + key, prior[key])
                    if seal and seal.get("status") == "captured_bundle"
                    else prior[key]
                    for key in used
                }
                for key, amount in amounts.items():
                    used[key] += amount
                    if prior["utc_day_index"] == day:
                        daily[key] += amount
            reserved = {
                "requests": n,
                "raw_bytes": child_plan["raw_byte_budget"],
                "stored_bytes": child_plan["stored_byte_budget"],
            }
            if any(used[key] + reserved[key] > plan["caps"][key] for key in used):
                raise EnvelopeError("lease_budget_exhausted")
            daily_caps = {
                "requests": plan["caps"]["daily_requests"],
                "raw_bytes": plan["caps"]["daily_raw_bytes"],
                "stored_bytes": plan["caps"]["daily_stored_bytes"],
            }
            if any(daily[key] + reserved[key] > daily_caps[key] for key in daily):
                raise EnvelopeError("utc_day_budget_exhausted")
            known = {row.get("bucket_id") for row in rows if row["kind"] in {"reservation", "gap"}}
            if any(
                item["end_ns"] <= at_ns and item["bucket_id"] not in known and item != bucket
                for item in plan["buckets"]
            ):
                raise EnvelopeError("missed_bucket_requires_explicit_gap")
            latest_gap = max((r["seq"] for r in rows if r["kind"] == "gap"), default=-1)
            reservation_by_id = {r["operation_id"]: r for r in rows if r["kind"] == "reservation"}
            latest_anchor = max(
                (
                    r["seq"]
                    for r in rows
                    if r["kind"] == "seal"
                    and reservation_by_id[r["operation_id"]].get("bucket_kind") == "anchor"
                    and r.get("status") == "captured_bundle"
                ),
                default=-1,
            )
            if latest_gap > latest_anchor and bucket["kind"] != "anchor":
                raise EnvelopeError("gap_requires_future_anchor_only")
            if bucket["kind"] == "frame" and latest_anchor < 0:
                raise EnvelopeError("frame_requires_sealed_anchor")
            _append_locked(
                root,
                {
                    "kind": "reservation",
                    "operation_id": operation_id,
                    "bucket_id": bucket_id,
                    "bucket_kind": bucket["kind"],
                    "at_ns": at_ns,
                    "utc_day_index": day,
                    "method": "v3-anchor" if n == 1 else "v3-frame",
                    "request_sha256": request_hash,
                    "requests": n,
                    "raw_bytes": child_plan["raw_byte_budget"],
                    "stored_bytes": child_plan["stored_byte_budget"],
                    "physical_free_bytes": physical_free_bytes,
                    "child_path": child_rel,
                    "child_plan_sha256": plan_hash,
                    "frontier_before": frontier,
                },
            )
        if crash_at == "after_reservation" and existing is None:
            return {"status": "crash_injected_before_dispatch", "child_request_reservations": 0}
        if operation_id not in started_ids:
            start_ns = time.time_ns()
            start_free = shutil.disk_usage(child_root).free
            if (
                start_ns < bucket["start_ns"]
                or start_ns >= min(bucket["end_ns"], plan["expires_ns"])
                or start_free < plan["caps"]["physical_bytes"]
            ):
                _append_locked(
                    root,
                    {
                        "kind": "stop",
                        "operation_id": operation_id,
                        "bucket_id": bucket_id,
                        "at_ns": start_ns,
                        "reason": "start_clock_or_disk_recheck_failed",
                        "unknown_interval": True,
                        "retry_started_child": False,
                    },
                )
                raise EnvelopeError("start_clock_or_disk_recheck_failed")
            _append_locked(
                root,
                {
                    "kind": "started",
                    "operation_id": operation_id,
                    "at_ns": start_ns,
                    "physical_free_bytes": start_free,
                    "child_plan_sha256": plan_hash,
                },
            )
    if crash_at == "after_start_before_dispatch":
        return {
            "status": "crash_injected_after_start_before_child_dispatch",
            "child_request_reservations": 0,
        }
    try:
        gated_clients = {
            name: _ScheduledClient(
                client,
                root=child_root,
                start_ns=bucket["start_ns"],
                end_ns=min(bucket["end_ns"], plan["expires_ns"]),
                floor=plan["caps"]["physical_bytes"],
            )
            for name, client in clients.items()
        }
        summary = runner(child_root, child_plan, clients=gated_clients)
        if crash_at == "after_capture_fsync":
            _stop(root, operation_id, bucket_id, "child_captured_parent_bundle_not_sealed")
            return {
                "status": "stopped_unknown",
                "child_request_reservations": len(list((child_root / "requests").glob("*.json"))),
            }
        bundle = _make_bundle(root, operation_id, bucket_id, child_root, child_plan, summary)
        with _writer(root):
            payload = bundle_payload(bundle)
            if not any(
                row["kind"] == "bundle" and row.get("operation_id") == operation_id
                for row in _events(root)
            ):
                _append_locked(
                    root,
                    {
                        "kind": "bundle",
                        "operation_id": operation_id,
                        "child_plan_sha256": plan_hash,
                        "bundle_path": str(bundle.relative_to(root)),
                        "bundle_sha256": _sha(bundle.read_bytes()),
                        "actual_requests": payload["actual_requests"],
                        "actual_raw_bytes": payload["actual_raw_bytes"],
                        "actual_stored_bytes": payload["actual_stored_bytes"],
                        "frontier_before": payload["frontier_before"],
                        "frontier_after": payload["frontier_after"],
                    },
                )
            if crash_at == "before_parent_seal":
                return {
                    "status": "crash_injected_before_seal",
                    "child_request_reservations": len(
                        list((child_root / "requests").glob("*.json"))
                    ),
                }
            # The independent parser traverses every child request, response, receipt and byte
            # before the parent may publish its captured seal.
            from tools.brain_senses_operation_bundle_verifier_v2 import verify

            checked = verify(root)
            if (
                not checked.get("valid")
                or checked.get("recovery") != "verified_bundle_ready_to_seal_no_replay"
            ):
                raise EnvelopeError("independent_child_bundle_verification_failed")
            _append_locked(
                root,
                {
                    "kind": "seal",
                    "operation_id": operation_id,
                    "at_ns": time.time_ns(),
                    "status": "captured_bundle",
                    "bundle_sha256": _sha(bundle.read_bytes()),
                    "actual_requests": payload["actual_requests"],
                    "actual_raw_bytes": payload["actual_raw_bytes"],
                    "actual_stored_bytes": payload["actual_stored_bytes"],
                },
            )
        return {
            "status": "captured_bundle",
            "summary": summary,
            "bundle": str(bundle),
            "child_request_reservations": len(list((child_root / "requests").glob("*.json"))),
        }
    except Exception as exc:
        _stop(root, operation_id, bucket_id, f"child_failed:{type(exc).__name__}")
        raise


def bundle_payload(path: Path) -> dict[str, Any]:
    return _json(path)


def _stop(root: Path, operation_id: str, bucket_id: str, reason: str) -> None:
    with _writer(root):
        if not any(r["kind"] == "stop" for r in _events(root)):
            _append_locked(
                root,
                {
                    "kind": "stop",
                    "operation_id": operation_id,
                    "bucket_id": bucket_id,
                    "at_ns": time.time_ns(),
                    "reason": reason,
                    "unknown_interval": True,
                    "retry_started_child": False,
                },
            )


def record_gap(root: Path, *, bucket_id: str, at_ns: int, reason: str) -> None:
    plan = _json(root / "plan-v2.json")
    bucket = next((b for b in plan["buckets"] if b["bucket_id"] == bucket_id), None)
    if bucket is None or at_ns < bucket["end_ns"]:
        raise EnvelopeError("gap_must_follow_scheduled_bucket")
    with _writer(root):
        _append_locked(
            root,
            {
                "kind": "gap",
                "bucket_id": bucket_id,
                "at_ns": at_ns,
                "reason": reason,
                "status": "gap",
            },
        )


def _make_bundle(
    root: Path,
    operation_id: str,
    bucket_id: str,
    child: Path,
    plan: dict[str, Any],
    summary: dict[str, Any],
) -> Path:
    requests = child / "requests"
    results = child / "request-results"
    request_rows = sorted(requests.glob("*.json"))
    result_rows = sorted(results.glob("*.json"))
    if len(request_rows) != len(result_rows):
        raise EnvelopeError("child_request_result_count_mismatch")
    children: list[dict[str, Any]] = []
    raw = stored = 0
    for rp, sp in zip(request_rows, result_rows, strict=True):
        req, res = _json(rp), _json(sp)
        if (
            req.get("sequence") != res.get("sequence")
            or req.get("request_sha256") is None
            or res.get("status") != "captured"
        ):
            raise EnvelopeError("child_request_result_not_captured")
        capture_rel = res.get("capture_path")
        if (
            not isinstance(capture_rel, str)
            or Path(capture_rel).is_absolute()
            or ".." in Path(capture_rel).parts
        ):
            raise EnvelopeError("child_capture_path_invalid")
        capture = child / capture_rel
        receipt = capture / "receipt.json"
        receipt_data = receipt.read_bytes()
        receipt_obj = json.loads(receipt_data)
        response_rel = receipt_obj.get("storage", {}).get("path", "response.json")
        response = capture / response_rel
        response_data = response.read_bytes()
        raw_bytes = res.get("response_bytes")
        stored_bytes = res.get("stored_response_bytes")
        raw += raw_bytes
        stored += stored_bytes
        children.append(
            {
                "sequence": req["sequence"],
                "method": req["method"],
                "params_sha256": _sha(_canonical(req["params"])),
                "request_sha256": req["request_sha256"],
                "request_path": str(rp.relative_to(root)),
                "request_file_sha256": _sha(rp.read_bytes()),
                "result_path": str(sp.relative_to(root)),
                "result_file_sha256": _sha(sp.read_bytes()),
                "receipt_path": str(receipt.relative_to(root)),
                "receipt_sha256": _sha(receipt_data),
                "response_path": str(response.relative_to(root)),
                "response_stored_sha256": _sha(response_data),
                "response_raw_sha256": res["response_sha256"],
                "raw_bytes": raw_bytes,
                "stored_bytes": stored_bytes,
                "request_started_ns": res["request_started_ns"],
                "receipt_ns": res["receipt_ns"],
                "availability_ns": res["availability_ns"],
                "event_slot": res.get("event_slot"),
            }
        )
    if summary.get("status") not in {"frame_complete", "bootstrap_complete"}:
        raise EnvelopeError("child_summary_not_complete")
    if len(children) != (1 if plan["bootstrap"] else 34):
        raise EnvelopeError("child_operation_count_not_contract")
    reservation = next(
        row
        for row in _events(root)
        if row["kind"] == "reservation" and row["operation_id"] == operation_id
    )
    frontier_after = (
        summary.get("bootstrap_slot", -1) + 1 if plan["bootstrap"] else summary.get("advance_to")
    )
    if type(frontier_after) is not int or frontier_after <= 0:
        raise EnvelopeError("child_frontier_missing")
    selection_path = child / "selection-plan.json"
    frame_path = child / "frame.json"
    manifest = {
        "schema": f"{SCHEMA}-child-bundle",
        "claim_scope": (
            "captured_request_response_bundle_only_child_structure_not_independently_qualified"
        ),
        "operation_id": operation_id,
        "bucket_id": bucket_id,
        "child_plan_path": str((child / "epoch-plan.json").relative_to(root)),
        "child_plan_sha256": _sha((child / "epoch-plan.json").read_bytes()),
        "summary_path": str((child / "epoch-summary.json").relative_to(root)),
        "summary_sha256": _sha((child / "epoch-summary.json").read_bytes()),
        "summary": summary,
        "frontier_before": reservation.get("frontier_before"),
        "frontier_after": frontier_after,
        "frame_path": str(frame_path.relative_to(root)) if frame_path.exists() else None,
        "frame_sha256": _sha(frame_path.read_bytes()) if frame_path.exists() else None,
        "selection_path": str(selection_path.relative_to(root))
        if selection_path.exists()
        else None,
        "selection_sha256": _sha(selection_path.read_bytes()) if selection_path.exists() else None,
        "children": children,
        "actual_requests": len(children),
        "actual_raw_bytes": raw,
        "actual_stored_bytes": stored,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }
    path = root / "bundles" / f"{operation_id}.json"
    encoded = _canonical(manifest) + b"\n"
    if path.exists():
        if path.read_bytes() != encoded:
            raise EnvelopeError("immutable_bundle_changed")
    else:
        _create(path, encoded)
    return path
