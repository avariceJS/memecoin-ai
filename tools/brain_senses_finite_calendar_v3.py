#!/usr/bin/env python3
"""Offline finite-calendar driver for the Senses operation-envelope-v2 bridge.

Caller-driven only: no provider, credential loader, cadence, timer, or activation path.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools import brain_senses_operation_bundle_v2 as bridge
from tools import brain_senses_operation_bundle_verifier_v2 as bridge_verify

HANDOFF_SCHEMA = "brain-senses-finite-calendar-handoff-v3"
CALENDAR_SCHEMA = "brain-senses-finite-calendar-v3"
HANDOFF_VERIFIER = "brain-senses-finite-calendar-handoff-verifier-v3"


class CalendarError(ValueError):
    """Fail-closed lifecycle or handoff error."""


def contract_binding() -> dict[str, str]:
    """Return the version/source pins callers embed in their finite plan."""
    return {
        "schema": CALENDAR_SCHEMA,
        "handoff_schema": HANDOFF_SCHEMA,
        "verifier_protocol": HANDOFF_VERIFIER,
        "driver_sha256": _sha(Path(__file__).resolve().read_bytes()),
        "bridge_sha256": _sha(Path(bridge.__file__).resolve().read_bytes()),
        "bridge_verifier_sha256": _sha(Path(bridge_verify.__file__).resolve().read_bytes()),
        "handoff_verifier_sha256": _sha(
            Path(__file__).with_name("brain_senses_finite_calendar_verifier_v3.py").read_bytes()
        ),
    }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rows(root: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (root / "events-v2.jsonl").read_text().splitlines()]


def _plan(root: Path) -> dict[str, Any]:
    return json.loads((root / "plan-v2.json").read_bytes())


def _private_root(path: Path, *, create: bool) -> Path:
    """Reject aliases and non-private roots before any writer opens a child path."""
    original = Path(path)
    if create:
        try:
            original.lstat()
        except FileNotFoundError:
            original.parent.mkdir(parents=True, exist_ok=True)
            original.mkdir(mode=0o700)
    try:
        entry = original.lstat()
    except OSError as exc:
        raise CalendarError("calendar_root_unavailable") from exc
    if (
        stat.S_ISLNK(entry.st_mode)
        or not stat.S_ISDIR(entry.st_mode)
        or entry.st_uid != os.getuid()
        or stat.S_IMODE(entry.st_mode) != 0o700
    ):
        raise CalendarError("calendar_root_identity_invalid")
    return original.resolve(strict=True)


def _driver_lock(root: Path):
    fd = os.open(
        root / ".finite-calendar-v3.lock",
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    handle = os.fdopen(fd, "r+")
    st = os.fstat(handle.fileno())
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o600
    ):
        handle.close()
        raise CalendarError("calendar_lock_identity_invalid")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise CalendarError("calendar_writer_already_active") from exc
    return handle


def _record_elapsed(root: Path, now: int) -> None:
    plan = _plan(root)
    known = {r["bucket_id"] for r in _rows(root) if r["kind"] in {"reservation", "gap"}}
    for bucket in plan["buckets"]:
        if bucket["bucket_id"] in known:
            continue
        bridge_verify.verify(root)
        if now >= bucket["end_ns"]:
            bridge.record_gap(
                root, bucket_id=bucket["bucket_id"], at_ns=now, reason="elapsed_without_start"
            )
            known.add(bucket["bucket_id"])


def _account(rows: list[dict[str, Any]]) -> dict[str, int]:
    reservations = {r["operation_id"]: r for r in rows if r["kind"] == "reservation"}
    seals = {r["operation_id"]: r for r in rows if r["kind"] == "seal"}
    totals = {k: 0 for k in ("requests", "raw_bytes", "stored_bytes")}
    for oid, reservation in reservations.items():
        seal = seals.get(oid)
        for key in totals:
            totals[key] += (
                seal.get("actual_" + key, reservation[key])
                if seal and seal.get("status") == "captured_bundle"
                else reservation[key]
            )
    return totals


def _frontier_gaps_unknown(
    root: Path, rows: list[dict[str, Any]]
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    plan = _plan(root)
    frontier = plan["predecessor"]["frontier_slot"]
    reservations = {r["operation_id"]: r for r in rows if r["kind"] == "reservation"}
    bundles = {r["operation_id"]: r for r in rows if r["kind"] == "bundle"}
    for row in rows:
        oid = row.get("operation_id")
        if row["kind"] == "seal" and oid in bundles:
            reservation = reservations[oid]
            bundle = json.loads((root / bundles[oid]["bundle_path"]).read_bytes())
            if reservation["bucket_kind"] == "anchor" or bundle.get("frontier_before") == frontier:
                frontier = bundle["frontier_after"]
    inherited = [
        {"bucket_id": gap, "reason": "inherited_predecessor_gap"}
        for gap in plan["predecessor"].get("gaps", [])
    ]
    gaps = inherited + [
        {"bucket_id": r["bucket_id"], "at_ns": r["at_ns"], "reason": r["reason"]}
        for r in rows
        if r["kind"] == "gap"
    ]
    sealed = {r["operation_id"] for r in rows if r["kind"] == "seal"}
    stopped = {r.get("operation_id") for r in rows if r["kind"] == "stop"}
    unknown = list(plan["predecessor"].get("unknown_intervals", [])) + [
        {
            "bucket_id": reservation["bucket_id"],
            "operation_id": operation_id,
            "reserved_at_ns": reservation["at_ns"],
            "reason": "reserved_operation_without_seal_no_replay",
            "unknown_interval": True,
        }
        for operation_id, reservation in reservations.items()
        if operation_id not in sealed and operation_id not in stopped
    ]
    unknown.extend(
        {
            "bucket_id": row.get("bucket_id"),
            "operation_id": row.get("operation_id"),
            "stopped_at_ns": row["at_ns"],
            "reason": row["reason"],
            "unknown_interval": True,
        }
        for row in rows
        if row["kind"] == "stop" and row.get("unknown_interval") is True
    )
    return frontier, gaps, unknown


def _write_handoff(root: Path, *, now_ns: int, reason: str) -> dict[str, Any]:
    checked = bridge_verify.verify(root)
    if not checked.get("valid"):
        raise CalendarError("bridge_verification_failed_before_terminal")
    rows = _rows(root)
    frontier, gaps, unknown_intervals = _frontier_gaps_unknown(root, rows)
    plan_bytes = (root / "plan-v2.json").read_bytes()
    payload = {
        "schema": HANDOFF_SCHEMA,
        "lease_id": _plan(root)["lease_id"],
        "calendar_contract": _plan(root)["calendar_contract"],
        "terminal_reason": reason,
        "terminal_at_ns": now_ns,
        "plan_sha256": _sha(plan_bytes),
        "events_sha256": _sha((root / "events-v2.jsonl").read_bytes()),
        "terminal_event_sha256": rows[-1]["event_sha256"],
        "frontier_slot": frontier,
        "gaps": gaps,
        "unknown_intervals": unknown_intervals,
        "usage": _account(rows),
        "bridge_recovery": checked["recovery"],
        "activation_authorized": False,
    }
    payload["handoff_sha256"] = _sha(_canonical(payload))
    data = _canonical(payload) + b"\n"
    path = root / "terminal-handoff-v3.json"
    temporary = root / f".terminal-handoff-{uuid.uuid4().hex}.tmp"
    fd = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        temporary.unlink(missing_ok=True)
        raise CalendarError("terminal_handoff_already_exists") from None
    temporary.unlink(missing_ok=True)
    dfd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return payload


def initialize(root: Path, plan: dict[str, Any]) -> None:
    """Initialize from a caller-supplied finite bucket schedule."""
    if plan.get("schema") != bridge.SCHEMA or not isinstance(plan.get("buckets"), list):
        raise CalendarError("caller_finite_plan_required")
    if plan.get("calendar_contract") != contract_binding():
        raise CalendarError("finite_calendar_contract_binding_invalid")
    root = _private_root(root, create=True)
    bridge.initialize(root, plan)


def _drive_once(
    root: Path,
    *,
    now_ns: int | None = None,
    runner: Callable[..., dict[str, Any]] | None = None,
    clients: dict[str, Any] | None = None,
    operation_prefix: str = "calendar",
    child_root: Callable[[Path, str], Path] | None = None,
    child_plan: Callable[[Path, dict[str, Any], str], dict[str, Any]] | None = None,
    physical_free_bytes: int | None = None,
) -> dict[str, Any]:
    """Advance at most one operation. UTC wall time determines all eligibility."""
    root = _private_root(root, create=False)
    now = time.time_ns() if now_ns is None else now_ns
    checked = bridge_verify.verify(root)
    if not checked.get("valid"):
        raise CalendarError("bridge_prefix_not_independently_verified")
    rows = _rows(root)
    plan = _plan(root)
    if (root / "terminal-handoff-v3.json").exists():
        raise CalendarError("calendar_already_terminal")
    if any(r["kind"] == "stop" for r in rows):
        terminal_time = max(now, max(r.get("at_ns", 0) for r in rows))
        return _write_handoff(root, now_ns=terminal_time, reason="stopped")
    if any(
        r["kind"] == "reservation"
        and not any(
            s["kind"] == "seal" and s.get("operation_id") == r["operation_id"] for s in rows
        )
        for r in rows
    ):
        bridge._stop(root, "calendar-recovery", "unknown", "abnormal_unsealed_prefix_no_replay")
        return _write_handoff(root, now_ns=now, reason="abnormal_unsealed_prefix")
    latest = max((r.get("at_ns", 0) for r in rows), default=0)
    if now < latest:
        bridge._stop(root, "calendar-clock", "unknown", "utc_clock_regressed")
        return _write_handoff(root, now_ns=latest, reason="utc_clock_regressed")
    _record_elapsed(root, now)
    rows = _rows(root)
    if now >= plan["expires_ns"]:
        return _write_handoff(root, now_ns=now, reason="lease_expired")
    free = shutil.disk_usage(root).free if physical_free_bytes is None else physical_free_bytes
    if free < plan["caps"]["physical_bytes"]:
        bridge._stop(root, "calendar-disk", "unknown", "physical_disk_floor_failed")
        return _write_handoff(root, now_ns=now, reason="disk_floor_failed")
    known = {r["bucket_id"] for r in rows if r["kind"] in {"reservation", "gap"}}
    due = [b for b in plan["buckets"] if b["bucket_id"] not in known]
    for bucket in due:
        bridge_verify.verify(root)
        if now >= bucket["end_ns"]:
            bridge.record_gap(
                root, bucket_id=bucket["bucket_id"], at_ns=now, reason="elapsed_without_start"
            )
            rows = _rows(root)
            continue
        if now < bucket["start_ns"]:
            return {
                "status": "waiting",
                "bucket_id": bucket["bucket_id"],
                "eligible_at_ns": bucket["start_ns"],
            }
        if bucket["kind"] == "frame":
            _, reset_required = bridge._frontier_before(root, plan, rows)
            if reset_required:
                return {
                    "status": "waiting_for_anchor",
                    "bucket_id": bucket["bucket_id"],
                    "eligible_at_ns": bucket["end_ns"],
                    "reason": "gap_requires_future_anchor",
                }
        if runner is None or clients is None or child_root is None or child_plan is None:
            return {"status": "eligible", "bucket_id": bucket["bucket_id"], "kind": bucket["kind"]}
        oid = f"{operation_prefix}-{bucket['bucket_id']}"
        result = bridge.reserve_and_run(
            root,
            bucket_id=bucket["bucket_id"],
            operation_id=oid,
            child_root=child_root(root, bucket["bucket_id"]),
            child_plan=child_plan(root, bucket, bucket["bucket_id"]),
            runner=runner,
            clients=clients,
        )
        bridge_verify.verify(root)
        return result
    return _write_handoff(root, now_ns=now, reason="calendar_exhausted")


def drive_once(root: Path, **kwargs: Any) -> dict[str, Any]:
    """Serialize each whole lifecycle decision; concurrent invocations fail closed."""
    root = _private_root(root, create=False)
    with _driver_lock(root):
        return _drive_once(root, **kwargs)


def initialize_successor(
    root: Path, successor_root: Path, plan: dict[str, Any], *, review_digest: str
) -> None:
    """Require a distinct lease and a fresh explicit review digest; never renew automatically."""
    handoff = verify_handoff(root)
    if (
        plan.get("lease_id") == handoff["lease_id"]
        or type(plan.get("created_ns")) is not int
        or plan["created_ns"] < handoff["terminal_at_ns"]
        or not isinstance(plan.get("buckets"), list)
        or not plan["buckets"]
        or type(plan["buckets"][0].get("start_ns")) is not int
        or plan["buckets"][0]["start_ns"] < handoff["terminal_at_ns"]
        or not isinstance(review_digest, str)
        or len(review_digest) != 64
        or any(c not in "0123456789abcdef" for c in review_digest)
    ):
        raise CalendarError("successor_requires_distinct_lease_and_review_digest")
    pred = plan.get("predecessor")
    if (
        not isinstance(pred, dict)
        or pred.get("terminal_sha256") != handoff["handoff_sha256"]
        or pred.get("frontier_slot") != handoff["frontier_slot"]
        or pred.get("gaps") != [g["bucket_id"] for g in handoff["gaps"]]
        or pred.get("unknown_intervals") != handoff["unknown_intervals"]
    ):
        raise CalendarError("successor_predecessor_transfer_mismatch")
    bound = dict(plan)
    bound["successor_review_digest"] = review_digest
    initialize(successor_root, bound)


def verify_handoff(root: Path) -> dict[str, Any]:
    from tools.brain_senses_finite_calendar_verifier_v3 import verify_handoff as verify

    return verify(root)
