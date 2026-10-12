"""Bounded descriptor-bound health snapshot for the capture-only Gecko lane."""

from __future__ import annotations

import json
from typing import Any

from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SCHEMA = "brain-senses-gecko-capture-only-health-v1"
EVENT_SCHEMA = "brain-senses-gecko-capture-only-transition-v1"
SNAPSHOT = "capture-health.json"
EVENT_DIR = "capture-health-events"
MAX_EVENTS = 128
MAX_EVENT_BYTES = 2048
MAX_HEARTBEAT_AGE_NS = 180 * 1_000_000_000
_REASONS = {
    "none",
    "cooldown",
    "manual_review",
    "capacity",
    "clock",
    "storage",
    "integrity",
    "heartbeat_gap",
    "unknown",
}


def read_events(store: Any) -> list[dict[str, Any]]:
    rows = []
    previous = None
    for index, name in enumerate(sorted(store.files(EVENT_DIR))):
        if name != f"{index:06d}.json":
            raise ValueError("capture_monitor_event_inventory")
        raw = store.writer.read_bytes(store._path(f"{EVENT_DIR}/{name}"), max_bytes=MAX_EVENT_BYTES)
        row = json.loads(raw)
        core = dict(row)
        claimed = core.pop("event_sha256", None)
        if (
            not isinstance(row, dict)
            or set(row)
            != {
                "schema",
                "sequence",
                "at_ns",
                "kind",
                "reason",
                "plan_sha256",
                "previous_sha256",
                "reconciliation",
                "event_sha256",
            }
            or row.get("schema") != EVENT_SCHEMA
            or row.get("sequence") != index
            or row.get("previous_sha256") != previous
            or row.get("kind") not in {"alarm", "recovery", "journal_reconciled"}
            or row.get("reason") not in _REASONS
            or claimed != sha256(canonical_json(core))
            or canonical_json(row) != raw
        ):
            raise ValueError("capture_monitor_event_invalid")
        previous = claimed
        rows.append(row)
    if len(rows) > MAX_EVENTS:
        raise ValueError("capture_monitor_event_cap")
    active = None
    for row in rows:
        if row["kind"] == "journal_reconciled":
            if not isinstance(row["reconciliation"], dict):
                raise ValueError("capture_monitor_recovery_shape")
            continue
        if row["kind"] == "alarm":
            if active is not None:
                raise ValueError("capture_monitor_alarm_overlap")
            active = row["reason"]
        else:
            if active != row["reason"]:
                raise ValueError("capture_monitor_recovery_mismatch")
            active = None
    return rows


def read_snapshot(store: Any) -> dict[str, Any] | None:
    path = store._path(SNAPSHOT)
    if not store.writer.exists(path):
        return None
    raw = store.writer.read_bytes(path, max_bytes=8192)
    value = json.loads(raw)
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise ValueError("capture_monitor_snapshot_canonical")
    core = dict(value)
    claimed = core.pop("snapshot_sha256", None)
    if (
        set(value)
        != {
            "schema",
            "schema_version",
            "generation",
            "observed_at_ns",
            "status",
            "reason",
            "root_id",
            "volume_uuid",
            "request_count",
            "result_count",
            "request_tail_sha256",
            "result_tail_sha256",
            "coordinator_tail_sha256",
            "free_bytes",
            "source_sha256",
            "plan_sha256",
            "stop_latched",
            "event_count",
            "event_tail_sha256",
            "snapshot_sha256",
        }
        or value.get("schema") != SCHEMA
        or value.get("schema_version") != 1
        or type(value.get("generation")) is not int
        or value["generation"] < 0
        or type(value.get("observed_at_ns")) is not int
        or value["observed_at_ns"] <= 0
        or value.get("status") not in {"healthy", "stopped"}
        or value.get("reason") not in _REASONS
        or type(value.get("stop_latched")) is not bool
        or type(value.get("request_count")) is not int
        or type(value.get("result_count")) is not int
        or type(value.get("free_bytes")) is not int
        or type(value.get("event_count")) is not int
        or min(
            value["request_count"],
            value["result_count"],
            value["free_bytes"],
            value["event_count"],
        )
        < 0
        or any(
            not isinstance(value.get(key), str)
            or len(value[key]) != 64
            or any(ch not in "0123456789abcdef" for ch in value[key])
            for key in (
                "request_tail_sha256",
                "result_tail_sha256",
                "coordinator_tail_sha256",
                "source_sha256",
                "plan_sha256",
                "snapshot_sha256",
            )
        )
        or not isinstance(value.get("root_id"), str)
        or not isinstance(value.get("volume_uuid"), str)
        or len(value["root_id"]) != 32
        or any(char not in "0123456789abcdef" for char in value["root_id"])
        or len(value["volume_uuid"]) != 36
        or any(char not in "0123456789abcdef-" for char in value["volume_uuid"])
        or (value["status"] == "healthy" and value["reason"] != "none")
        or (value["status"] == "stopped" and value["reason"] == "none")
        or claimed != sha256(canonical_json(core))
    ):
        raise ValueError("capture_monitor_snapshot_invalid")
    events = read_events(store)
    if value["event_count"] > len(events):
        raise ValueError("capture_monitor_snapshot_event_mismatch")
    snapshot_events = events[: value["event_count"]]
    active = None
    for row in snapshot_events:
        if row["kind"] == "alarm":
            active = row["reason"]
        elif row["kind"] == "recovery":
            active = None
    if (
        value["event_tail_sha256"]
        != (snapshot_events[-1]["event_sha256"] if snapshot_events else None)
        or value["stop_latched"] is not (active is not None)
        or (value["status"] == "stopped" and active != value["reason"])
        or (value["status"] == "healthy" and active is not None)
    ):
        raise ValueError("capture_monitor_snapshot_event_mismatch")
    return value


def publish(
    writer: DescriptorWriter,
    store: Any,
    *,
    now_ns: int,
    root_id: str,
    volume_uuid: str,
    request_count: int,
    result_count: int,
    request_tail_sha256: str,
    result_tail_sha256: str,
    coordinator_tail_sha256: str,
    free_bytes: int,
    source_sha256: str,
    plan_sha256: str,
    stopped_reason: str | None,
    completed_operation: bool = False,
    recovery_proof_sha256: str | None = None,
) -> dict[str, Any]:
    events = read_events(store)
    prior = read_snapshot(store)
    if prior is not None:
        old_tip = events[prior["event_count"] - 1]["event_sha256"] if prior["event_count"] else None
        if prior["event_count"] > len(events) or prior["event_tail_sha256"] != old_tip:
            raise ValueError("capture_monitor_snapshot_event_mismatch")
        changed = (
            prior["request_tail_sha256"] != request_tail_sha256
            or prior["result_tail_sha256"] != result_tail_sha256
            or prior["coordinator_tail_sha256"] != coordinator_tail_sha256
        )
        if changed and not completed_operation and recovery_proof_sha256 is None:
            raise ValueError("capture_monitor_unreceipted_recovery")
        if now_ns < prior["observed_at_ns"]:
            raise ValueError("capture_monitor_time_regression")
        prior_is_stale = now_ns - prior["observed_at_ns"] > MAX_HEARTBEAT_AGE_NS
    else:
        prior_is_stale = False
    reason = stopped_reason or "none"
    if reason not in _REASONS:
        reason = "unknown"
    status = "stopped" if stopped_reason is not None else "healthy"
    active = None
    for row in events:
        if row["kind"] == "alarm":
            active = row["reason"]
        elif row["kind"] == "recovery":
            active = None
    specs: list[tuple[str, str, dict[str, Any] | None]] = []
    # A healthy callback after a long gap must preserve that downtime as immutable
    # evidence instead of replacing the old heartbeat with a current healthy snapshot.
    # If a crash already published both transition records, detect that pair relative
    # to the prior snapshot and avoid duplicating it during local recovery.
    if prior is not None and prior_is_stale:
        post_snapshot_events = events[prior["event_count"] :]
        gap_alarm_seen = (
            prior["status"] == "stopped" and prior["reason"] == "heartbeat_gap"
        ) or any(
            row["kind"] == "alarm" and row["reason"] == "heartbeat_gap"
            for row in post_snapshot_events
        )
        gap_recovery_seen = any(
            row["kind"] == "recovery" and row["reason"] == "heartbeat_gap"
            for row in post_snapshot_events
        )
        if gap_recovery_seen and not gap_alarm_seen:
            raise ValueError("capture_monitor_gap_recovery_without_alarm")
        if gap_alarm_seen and not gap_recovery_seen:
            if active != "heartbeat_gap":
                raise ValueError("capture_monitor_gap_alarm_state_mismatch")
            # A crash left the alarm durable but not its closing recovery. Publish
            # exactly that missing event; never recreate an overlapping alarm.
            specs.append(("recovery", "heartbeat_gap", None))
            active = None
        elif not gap_alarm_seen and active is None:
            specs.extend(
                [
                    ("alarm", "heartbeat_gap", None),
                    ("recovery", "heartbeat_gap", None),
                ]
            )
    if prior is not None and recovery_proof_sha256 is not None:
        specs.append(
            (
                "journal_reconciled",
                "none",
                {
                    "prior_request_tail_sha256": prior["request_tail_sha256"],
                    "prior_result_tail_sha256": prior["result_tail_sha256"],
                    "prior_coordinator_tail_sha256": prior["coordinator_tail_sha256"],
                    "new_request_tail_sha256": request_tail_sha256,
                    "new_result_tail_sha256": result_tail_sha256,
                    "new_coordinator_tail_sha256": coordinator_tail_sha256,
                    "recovery_proof_sha256": recovery_proof_sha256,
                },
            )
        )
    if status == "stopped":
        if active is not None and active != reason:
            specs.append(("recovery", active, None))
            active = None
        if active is None:
            specs.append(("alarm", reason, None))
    elif active is not None:
        specs.append(("recovery", active, None))
    for kind, event_reason, reconciliation in specs:
        event: dict[str, Any] = {
            "schema": EVENT_SCHEMA,
            "sequence": len(events),
            "at_ns": now_ns,
            "kind": kind,
            "reason": event_reason,
            "plan_sha256": plan_sha256,
            "previous_sha256": events[-1]["event_sha256"] if events else None,
            "reconciliation": reconciliation,
        }
        event["event_sha256"] = sha256(canonical_json(event))
        raw_event = canonical_json(event)
        if len(raw_event) > MAX_EVENT_BYTES or len(events) >= MAX_EVENTS:
            raise ValueError("capture_monitor_event_cap")
        writer.create(f"{store.root}/{EVENT_DIR}/{len(events):06d}.json", raw_event)
        events = read_events(store)
    active = None
    for row in events:
        if row["kind"] == "alarm":
            active = row["reason"]
        elif row["kind"] == "recovery":
            active = None
    snapshot: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": 1,
        "generation": prior["generation"] + 1 if prior else 0,
        "observed_at_ns": now_ns,
        "status": status,
        "reason": reason,
        "root_id": root_id,
        "volume_uuid": volume_uuid.lower(),
        "request_count": request_count,
        "result_count": result_count,
        "request_tail_sha256": request_tail_sha256,
        "result_tail_sha256": result_tail_sha256,
        "coordinator_tail_sha256": coordinator_tail_sha256,
        "free_bytes": free_bytes,
        "source_sha256": source_sha256,
        "plan_sha256": plan_sha256,
        "stop_latched": active is not None,
        "event_count": len(events),
        "event_tail_sha256": events[-1]["event_sha256"] if events else None,
    }
    snapshot["snapshot_sha256"] = sha256(canonical_json(snapshot))
    writer.atomic_write(f"{store.root}/{SNAPSHOT}", canonical_json(snapshot))
    return snapshot
