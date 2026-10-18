#!/usr/bin/env python3
"""Offline, one-decision supervisor for an already initialized finite Senses lease.

This module has no client factory, credential handling, scheduler, or activation path. Any
operation runner and client are explicit caller injections; the command line is read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Any

from tools import brain_senses_block_epoch_v3 as child_v3
from tools import brain_senses_finite_calendar_v3 as calendar
from tools import brain_senses_finite_calendar_verifier_v3 as calendar_verify
from tools import brain_senses_operation_bundle_v2 as bridge
from tools import brain_senses_operation_bundle_verifier_v2 as bridge_verify

SCHEMA = "brain-senses-calendar-supervisor-v1"
BINDING = "supervisor-v1-binding.json"


class SupervisorError(ValueError):
    """Offline supervisor binding, verification, or decision error."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _latest_durable_time(rows: list[dict[str, Any]]) -> int:
    return max(
        (
            value
            for row in rows
            for field in ("at_ns", "receipt_ns", "availability_ns")
            if type(value := row.get(field)) is int
        ),
        default=0,
    )


def source_hashes() -> dict[str, str]:
    """Hashes of the exact supervisor, v3 calendar, v2 envelope, and child-v3 code."""
    paths = {
        "supervisor": Path(__file__).resolve(),
        "calendar_driver": Path(calendar.__file__).resolve(),
        "calendar_verifier": Path(calendar_verify.__file__).resolve(),
        "operation_envelope": Path(bridge.__file__).resolve(),
        "operation_verifier": Path(bridge_verify.__file__).resolve(),
        "child_v3": Path(child_v3.__file__).resolve(),
    }
    return {name: _sha(path.read_bytes()) for name, path in paths.items()}


def _root(path: Path) -> tuple[Path, dict[str, int]]:
    original = Path(path)
    try:
        entry = original.lstat()
    except OSError as exc:
        raise SupervisorError("lease_root_unavailable") from exc
    if (
        stat.S_ISLNK(entry.st_mode)
        or not stat.S_ISDIR(entry.st_mode)
        or entry.st_uid != os.getuid()
        or stat.S_IMODE(entry.st_mode) != 0o700
    ):
        raise SupervisorError("lease_root_identity_invalid")
    resolved = original.resolve(strict=True)
    return resolved, {"device": entry.st_dev, "inode": entry.st_ino, "uid": entry.st_uid}


def _private_file(path: Path) -> bytes:
    try:
        st = path.lstat()
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600
        ):
            raise SupervisorError("supervisor_binding_identity_invalid")
        return path.read_bytes()
    except OSError as exc:
        raise SupervisorError("supervisor_binding_unavailable") from exc


def bind(
    lease_root: Path,
    *,
    expected_plan_sha256: str,
    expected_source_sha256: dict[str, str],
) -> dict[str, Any]:
    """Write an immutable owner-only binding for a caller-pinned existing lease.

    This records local hash expectations. It does not authenticate the plan author or reviewer.
    """
    root, identity = _root(lease_root)
    plan_path = root / "plan-v2.json"
    try:
        plan_bytes = _private_file(plan_path)
    except SupervisorError as exc:
        raise SupervisorError("lease_plan_unavailable") from exc
    verified = bridge_verify.verify(root)
    if not verified.get("valid"):
        raise SupervisorError("independent_parent_verification_failed")
    actual_plan_sha = _sha(plan_bytes)
    actual_sources = source_hashes()
    if (
        expected_plan_sha256 != actual_plan_sha
        or expected_source_sha256 != actual_sources
        or json.loads(plan_bytes).get("calendar_contract") != calendar.contract_binding()
    ):
        raise SupervisorError("caller_pin_or_contract_mismatch")
    payload = {
        "schema": SCHEMA,
        "lease_root": str(root),
        "root_identity": identity,
        "plan_sha256": actual_plan_sha,
        "source_sha256": actual_sources,
        "activation_authorized": False,
    }
    path = root / BINDING
    data = _canonical(payload) + b"\n"
    if path.exists() or path.is_symlink():
        if _private_file(path) != data:
            raise SupervisorError("supervisor_binding_already_pinned")
        return payload
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    dfd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return payload


def _validate_binding(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved, identity = _root(root)
    binding_path = resolved / BINDING
    raw = _private_file(binding_path)
    binding = json.loads(raw)
    if _canonical(binding) + b"\n" != raw:
        raise SupervisorError("supervisor_binding_noncanonical")
    try:
        plan_bytes = _private_file(resolved / "plan-v2.json")
        plan = json.loads(plan_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise SupervisorError("lease_plan_invalid") from exc
    if (
        binding.get("schema") != SCHEMA
        or binding.get("lease_root") != str(resolved)
        or binding.get("root_identity") != identity
        or binding.get("plan_sha256") != _sha(plan_bytes)
        or binding.get("source_sha256") != source_hashes()
        or plan.get("calendar_contract") != calendar.contract_binding()
        or binding.get("activation_authorized") is not False
    ):
        raise SupervisorError("supervisor_pin_or_source_changed")
    return binding, plan


def _verified_prefix(
    root: Path, plan: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    checked = bridge_verify.verify(root)
    if not checked.get("valid"):
        raise SupervisorError("independent_parent_verification_failed")
    rows = calendar._rows(root)
    if rows[0].get("kind") != "initialized":
        raise SupervisorError("parent_initialization_missing")
    handoff_path = root / "terminal-handoff-v3.json"
    if handoff_path.exists():
        result = calendar_verify.verify_handoff(root)
        if not result.get("valid"):
            raise SupervisorError("independent_handoff_verification_failed")
    if plan.get("calendar_contract") != calendar.contract_binding():
        raise SupervisorError("calendar_contract_binding_invalid")
    return checked, rows


def _report(root: Path, *, now_ns: int, decision_ns: int | None = None) -> dict[str, Any]:
    binding, plan = _validate_binding(root)
    checked, rows = _verified_prefix(root, plan)
    frontier, gaps, unknown = calendar._frontier_gaps_unknown(root, rows)
    reservations = {r["operation_id"]: r for r in rows if r["kind"] == "reservation"}
    seals = {r["operation_id"] for r in rows if r["kind"] == "seal"}
    known_buckets = {r["bucket_id"] for r in rows if r["kind"] in {"reservation", "gap"}}
    next_bucket = next(
        (dict(b) for b in plan["buckets"] if b["bucket_id"] not in known_buckets), None
    )
    terminal = None
    handoff_path = root / "terminal-handoff-v3.json"
    if handoff_path.exists():
        terminal = calendar_verify.verify_handoff(root)["terminal_reason"]
    stops = [r for r in rows if r["kind"] == "stop"]
    latest_event_ns = _latest_durable_time(rows)
    decision_ns = now_ns if decision_ns is None else decision_ns
    post_operation_regressed = now_ns < decision_ns
    if now_ns < latest_event_ns or post_operation_regressed:
        status_value = "clock_fault"
        terminal = terminal or "utc_clock_regressed"
    elif terminal is not None:
        status_value = "terminal"
    elif stops:
        status_value = "stopped"
        terminal = terminal or stops[-1].get("reason")
    elif any(operation_id not in seals for operation_id in reservations):
        status_value = "ambiguous_no_replay"
    elif now_ns >= plan["expires_ns"]:
        status_value = "expiry_due"
    elif next_bucket is None:
        status_value = "schedule_complete_pending_handoff"
    elif now_ns < next_bucket["start_ns"]:
        status_value = "waiting"
    elif now_ns >= next_bucket["end_ns"]:
        status_value = "elapsed_gap_pending"
    elif next_bucket["kind"] == "frame" and bridge._frontier_before(root, plan, rows)[1]:
        status_value = "waiting_for_anchor"
    else:
        status_value = "eligible"
    try:
        physical_free_bytes = shutil.disk_usage(root).free
    except OSError:
        physical_free_bytes = None
    if physical_free_bytes is None:
        status_value = "disk_evidence_fault"
    elif physical_free_bytes < plan["caps"]["physical_bytes"] and status_value not in {
        "terminal",
        "stopped",
        "clock_fault",
    }:
        status_value = "disk_floor_failed"
    report = {
        "schema": SCHEMA,
        "status": status_value,
        "lease_root": str(root.resolve(strict=True)),
        "plan_sha256": binding["plan_sha256"],
        "events_sha256": _sha(_private_file(root / "events-v2.jsonl")),
        "calendar_contract": plan["calendar_contract"],
        "source_sha256": binding["source_sha256"],
        "evaluated_at_ns": now_ns,
        "decision_at_ns": decision_ns,
        "post_operation_clock_regressed": post_operation_regressed,
        "frontier_slot": frontier,
        "gaps": gaps,
        "unknown_intervals": unknown,
        "caps": dict(plan["caps"]),
        "physical_free_bytes": physical_free_bytes,
        "next_bucket": next_bucket,
        "terminal_reason": terminal,
        "sealed_operation_count": len(seals),
        "unsealed_operation_count": len(set(reservations) - seals),
        "bridge_verifier_status": checked.get("recovery"),
        "activation_authorized": False,
    }
    report["status_sha256"] = _sha(_canonical(report))
    return report


def status(lease_root: Path, *, now_ns: int | None = None) -> dict[str, Any]:
    """Return a read-only, independently checked snapshot of the current lease."""
    root, _ = _root(lease_root)
    return _report(root, now_ns=time.time_ns() if now_ns is None else now_ns)


def verify_status(
    report: dict[str, Any], lease_root: Path, *, now_ns: int | None = None
) -> dict[str, Any]:
    """Check that a status snapshot still binds the current durable prefix and plan."""
    current_ns = time.time_ns() if now_ns is None else now_ns
    decision_ns = report.get("decision_at_ns", current_ns)
    if type(current_ns) is not int or type(decision_ns) is not int:
        return {"valid": False, "stale": True, "reason": "status_time_missing"}
    try:
        current = _report(Path(lease_root), now_ns=current_ns, decision_ns=decision_ns)
    except (
        SupervisorError,
        calendar.CalendarError,
        bridge.EnvelopeError,
        calendar_verify.VerificationError,
        OSError,
    ) as exc:
        return {"valid": False, "stale": True, "reason": str(exc)}
    valid_hash = report.get("status_sha256") == _sha(
        _canonical({key: value for key, value in report.items() if key != "status_sha256"})
    )
    same_snapshot = valid_hash and report.get("status_sha256") == current["status_sha256"]
    return {"valid": same_snapshot, "stale": not same_snapshot, "reason": None}


def advance_once(
    lease_root: Path,
    *,
    now_ns: int | None = None,
    runner: Any = None,
    clients: dict[str, Any] | None = None,
    child_root: Any = None,
    child_plan: Any = None,
) -> dict[str, Any]:
    """Make at most one due-bucket decision under the calendar's exclusive owner lock.

    Operation execution is possible only with explicit injected runner/client/callbacks. No
    provider client is constructed or loaded by this supervisor.
    """
    root, _ = _root(lease_root)
    with calendar._driver_lock(root):
        post_lock_clock_ns = time.time_ns()
        if now_ns is not None and now_ns != post_lock_clock_ns:
            raise SupervisorError("injected_clock_must_match_post_lock_wall_time")
        decision_ns = post_lock_clock_ns
        _validate_binding(root)
        plan = calendar._plan(root)
        _verified_prefix(root, plan)
        decision = calendar._drive_once(
            root,
            now_ns=decision_ns,
            runner=runner,
            clients=clients,
            child_root=child_root,
            child_plan=child_plan,
        )
        post_operation_clock_ns = time.time_ns()
        current = _report(
            root,
            now_ns=post_operation_clock_ns,
            decision_ns=decision_ns,
        )
        return {"decision": decision, "status": current, "activation_authorized": False}


def main(argv: list[str] | None = None) -> int:
    """Read-only CLI status; it accepts no plan, client, credential, or runner arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lease_root", type=Path)
    parser.add_argument("--now-ns", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        report = status(args.lease_root, now_ns=args.now_ns)
    except (SupervisorError, calendar.CalendarError, bridge.EnvelopeError) as exc:
        print(
            _canonical({"valid": False, "error": str(exc), "activation_authorized": False}).decode()
        )
        return 2
    print(_canonical(report).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
