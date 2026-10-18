#!/usr/bin/env python3
"""Independent read-only verifier for finite-calendar-v3 terminal handoffs."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

from tools import brain_senses_operation_bundle_verifier_v2 as bridge_verify

HANDOFF_SCHEMA = "brain-senses-finite-calendar-handoff-v3"
CALENDAR_SCHEMA = "brain-senses-finite-calendar-v3"
HANDOFF_VERIFIER = "brain-senses-finite-calendar-handoff-verifier-v3"


class VerificationError(ValueError):
    """Lifecycle receipt or its operation-envelope evidence is invalid."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        body = stream.read()
        after = os.fstat(stream.fileno())
    current = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.getuid()
        or before.st_nlink != 1
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
        or len(body) != after.st_size
    ):
        raise VerificationError("handoff_file_identity_invalid")
    return body


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
    root: Path, plan: dict[str, Any], rows: list[dict[str, Any]]
) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
    frontier = plan["predecessor"]["frontier_slot"]
    reservations = {r["operation_id"]: r for r in rows if r["kind"] == "reservation"}
    bundles = {r["operation_id"]: r for r in rows if r["kind"] == "bundle"}
    seals = {r["operation_id"] for r in rows if r["kind"] == "seal"}
    stopped = {r.get("operation_id") for r in rows if r["kind"] == "stop"}
    for row in rows:
        oid = row.get("operation_id")
        if row["kind"] == "seal" and oid in seals and oid in bundles:
            reservation = reservations[oid]
            bundle = json.loads(_read(root / bundles[oid]["bundle_path"]))
            if reservation["bucket_kind"] == "anchor" or bundle.get("frontier_before") == frontier:
                frontier = bundle["frontier_after"]
    gaps = [
        {"bucket_id": gap, "reason": "inherited_predecessor_gap"}
        for gap in plan["predecessor"].get("gaps", [])
    ] + [
        {"bucket_id": r["bucket_id"], "at_ns": r["at_ns"], "reason": r["reason"]}
        for r in rows
        if r["kind"] == "gap"
    ]
    unknown = list(plan["predecessor"].get("unknown_intervals", [])) + [
        {
            "bucket_id": reservation["bucket_id"],
            "operation_id": operation_id,
            "reserved_at_ns": reservation["at_ns"],
            "reason": "reserved_operation_without_seal_no_replay",
            "unknown_interval": True,
        }
        for operation_id, reservation in reservations.items()
        if operation_id not in seals and operation_id not in stopped
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


def verify_handoff(root: Path) -> dict[str, Any]:
    original = Path(root)
    if stat.S_ISLNK(original.lstat().st_mode):
        raise VerificationError("root_symlink_forbidden")
    root = original.resolve(strict=True)
    st = root.lstat()
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o700
    ):
        raise VerificationError("root_owner_or_mode_invalid")
    bridge = bridge_verify.verify(root)
    if not bridge.get("valid"):
        raise VerificationError("operation_envelope_invalid")
    plan_bytes = _read(root / "plan-v2.json")
    events_bytes = _read(root / "events-v2.jsonl")
    plan = json.loads(plan_bytes)
    rows = [json.loads(line) for line in events_bytes.splitlines()]
    contract = plan.get("calendar_contract")
    expected_contract = {
        "schema": CALENDAR_SCHEMA,
        "handoff_schema": HANDOFF_SCHEMA,
        "verifier_protocol": HANDOFF_VERIFIER,
        "driver_sha256": _sha(
            Path(__file__).with_name("brain_senses_finite_calendar_v3.py").read_bytes()
        ),
        "bridge_sha256": _sha(
            Path(__file__).with_name("brain_senses_operation_bundle_v2.py").read_bytes()
        ),
        "bridge_verifier_sha256": _sha(Path(bridge_verify.__file__).resolve().read_bytes()),
        "handoff_verifier_sha256": _sha(Path(__file__).resolve().read_bytes()),
    }
    if contract != expected_contract:
        raise VerificationError("finite_calendar_contract_binding_invalid")
    raw = _read(root / "terminal-handoff-v3.json")
    handoff = json.loads(raw)
    if _canonical(handoff) + b"\n" != raw:
        raise VerificationError("handoff_noncanonical")
    body = dict(handoff)
    supplied = body.pop("handoff_sha256", None)
    if supplied != _sha(_canonical(body)):
        raise VerificationError("handoff_hash_invalid")
    frontier, gaps, unknown_intervals = _frontier_gaps_unknown(root, plan, rows)
    expected = {
        "schema": HANDOFF_SCHEMA,
        "lease_id": plan["lease_id"],
        "calendar_contract": expected_contract,
        "plan_sha256": _sha(plan_bytes),
        "events_sha256": _sha(events_bytes),
        "terminal_event_sha256": rows[-1]["event_sha256"],
        "frontier_slot": frontier,
        "gaps": gaps,
        "unknown_intervals": unknown_intervals,
        "usage": _account(rows),
        "bridge_recovery": bridge["recovery"],
        "activation_authorized": False,
    }
    if any(handoff.get(key) != value for key, value in expected.items()):
        raise VerificationError("handoff_transfer_or_accounting_mismatch")
    if (
        type(handoff.get("terminal_at_ns")) is not int
        or not isinstance(handoff.get("terminal_reason"), str)
        or not handoff["terminal_reason"]
    ):
        raise VerificationError("handoff_terminal_fields_invalid")
    reason = handoff["terminal_reason"]
    stops = [r for r in rows if r["kind"] == "stop"]
    if handoff["terminal_at_ns"] < max((r.get("at_ns", 0) for r in rows), default=0):
        raise VerificationError("handoff_precedes_terminal_event")
    if reason == "lease_expired":
        if handoff["terminal_at_ns"] < plan["expires_ns"] or stops:
            raise VerificationError("handoff_expiry_basis_invalid")
    elif reason in {
        "stopped",
        "disk_floor_failed",
        "utc_clock_regressed",
        "abnormal_unsealed_prefix",
    }:
        if not stops or handoff["terminal_at_ns"] < stops[-1]["at_ns"]:
            raise VerificationError("handoff_stop_basis_invalid")
        stop_reason = stops[-1].get("reason")
        required_reason = {
            "disk_floor_failed": "physical_disk_floor_failed",
            "utc_clock_regressed": "utc_clock_regressed",
            "abnormal_unsealed_prefix": "abnormal_unsealed_prefix_no_replay",
        }.get(reason)
        if required_reason is not None and stop_reason != required_reason:
            raise VerificationError("handoff_stop_reason_mismatch")
    elif reason == "calendar_exhausted":
        buckets = {b["bucket_id"] for b in plan["buckets"]}
        accounted = {r["bucket_id"] for r in rows if r["kind"] in {"reservation", "gap"}}
        seals = {r["operation_id"] for r in rows if r["kind"] == "seal"}
        reserved = {r["operation_id"]: r for r in rows if r["kind"] == "reservation"}
        if buckets != accounted or stops or any(oid not in seals for oid in reserved):
            raise VerificationError("handoff_schedule_not_exhausted")
    else:
        raise VerificationError("handoff_terminal_reason_invalid")
    return {"valid": True, **handoff}
