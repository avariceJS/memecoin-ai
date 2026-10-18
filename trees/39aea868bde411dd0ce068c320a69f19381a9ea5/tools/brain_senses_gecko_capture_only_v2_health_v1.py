"""Bounded descriptor-bound health snapshot for the Gecko v2 host scheduler.

Health is a diagnostic projection, never request authority. The request root remains
independently replayed and has no heartbeat artifacts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SCHEMA = "brain-senses-gecko-capture-only-v2-health-v1"
ROOT_PREFIX = "services/gecko-market-v1/host-health-v1"
SNAPSHOT = "snapshot.json"
EVENTS = "events"
LOCK = ".runner.lock"
MAX_SNAPSHOT_BYTES = 16 * 1024
MAX_EVENT_BYTES = 2048
MAX_EVENTS = 256
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ROOT_ID = re.compile(r"[0-9a-f]{32}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_STATUSES = {"healthy", "paused", "stopped"}
_REASONS = {
    "none",
    "resumed",
    "outside_window",
    "cooldown",
    "busy",
    "request_cap",
    "byte_cap",
    "physical_cap",
    "storage_floor",
    "clock_error",
    "provider_error",
    "integrity_error",
    "storage_error",
    "launchd_error",
    "manual_reconciliation_required",
}


class HealthError(ValueError):
    """Invalid health binding, snapshot, or transition chain."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise HealthError(reason)


def _hash(value: object) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _health_root(release_id: str) -> str:
    _require(_ROOT_ID.fullmatch(release_id) is not None, "health_release_id_invalid")
    return f"{ROOT_PREFIX}/{release_id}"


def _decode(raw: bytes, *, limit: int, code: str) -> dict[str, Any]:
    _require(len(raw) <= limit, f"{code}_oversize")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HealthError(f"{code}_json_invalid") from exc
    _require(type(value) is dict and canonical_json(value) == raw, f"{code}_noncanonical")
    return cast(dict[str, Any], value)


def _event_core(
    *, sequence: int, observed_ns: int, previous: str | None, old: str, new: str, reason: str
) -> dict[str, Any]:
    return {
        "schema": f"{SCHEMA}-transition",
        "sequence": sequence,
        "observed_ns": observed_ns,
        "from_status": old,
        "to_status": new,
        "reason": reason,
        "previous_sha256": previous,
    }


def read_events(writer: DescriptorWriter, *, release_id: str) -> list[dict[str, Any]]:
    root = _health_root(release_id)
    try:
        names = writer.list_names(f"{root}/{EVENTS}")
    except FileNotFoundError:
        raise HealthError("health_events_missing") from None
    _require(len(names) <= MAX_EVENTS, "health_event_cap")
    expected = tuple(f"{index:06d}.json" for index in range(len(names)))
    _require(names == expected, "health_event_names")
    rows: list[dict[str, Any]] = []
    previous: str | None = None
    previous_status: str | None = None
    for index, name in enumerate(names):
        row = _decode(
            writer.read_bytes(f"{root}/{EVENTS}/{name}", max_bytes=MAX_EVENT_BYTES),
            limit=MAX_EVENT_BYTES,
            code="health_event",
        )
        expected_keys = {
            "schema",
            "sequence",
            "observed_ns",
            "from_status",
            "to_status",
            "reason",
            "previous_sha256",
            "event_sha256",
        }
        _require(set(row) == expected_keys, "health_event_shape")
        core = dict(row)
        claimed = core.pop("event_sha256")
        _require(
            row["schema"] == f"{SCHEMA}-transition"
            and row["sequence"] == index
            and type(row["observed_ns"]) is int
            and row["observed_ns"] > 0
            and row["from_status"] in _STATUSES
            and row["to_status"] in _STATUSES
            and row["from_status"] != row["to_status"]
            and row["reason"] in _REASONS - {"none"}
            and row["previous_sha256"] == previous
            and _hash(claimed)
            and claimed == sha256(canonical_json(core))
            and (previous_status is None or row["from_status"] == previous_status),
            "health_event_invalid",
        )
        previous = cast(str, claimed)
        previous_status = cast(str, row["to_status"])
        rows.append(row)
    return rows


def validate_snapshot(
    snapshot: Mapping[str, Any],
    *,
    release_id: str,
    storage: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    row = dict(snapshot)
    fields = {
        "schema",
        "schema_version",
        "release_id",
        "release_sha256",
        "source_sha256",
        "binding_sha256",
        "root_id",
        "volume_uuid",
        "device",
        "inode",
        "generation",
        "observed_ns",
        "monotonic_ns",
        "status",
        "reason",
        "stop_latched",
        "request_prefix_sha256",
        "coordinator_policy_sha256",
        "coordinator_sequence",
        "coordinator_tail_sha256",
        "cutover_prefix_sha256",
        "cutover_last_dispatch_ns",
        "last_dispatch_ns",
        "bucket_close_ns",
        "plan_sequence",
        "plan_record_sha256",
        "cursor",
        "selected_count",
        "intent_count",
        "received_count",
        "durable_count",
        "eligible_new_open_count",
        "missing_count",
        "daily_request_count",
        "daily_response_bytes",
        "rolling_request_count",
        "rolling_response_bytes",
        "allocated_bytes",
        "free_bytes",
        "cooldown_until_ns",
        "last_progress_ns",
        "last_error_code",
        "transition_count",
        "transition_tail_sha256",
        "snapshot_sha256",
    }
    _require(set(row) == fields, "health_snapshot_shape")
    core = dict(row)
    claimed = core.pop("snapshot_sha256")
    _require(
        row["schema"] == SCHEMA
        and row["schema_version"] == 1
        and row["release_id"] == release_id
        and _hash(row["release_sha256"])
        and _hash(row["source_sha256"])
        and _hash(row["binding_sha256"])
        and row["root_id"] == storage.get("root_id")
        and row["volume_uuid"] == str(storage.get("volume_uuid", "")).lower()
        and row["device"] == storage.get("device")
        and row["inode"] == storage.get("inode")
        and _ROOT_ID.fullmatch(str(row["root_id"])) is not None
        and _UUID.fullmatch(str(row["volume_uuid"])) is not None
        and type(row["generation"]) is int
        and row["generation"] >= 0
        and type(row["observed_ns"]) is int
        and row["observed_ns"] > 0
        and type(row["monotonic_ns"]) is int
        and row["monotonic_ns"] > 0
        and row["status"] in _STATUSES
        and row["reason"] in _REASONS
        and type(row["stop_latched"]) is bool
        and (row["status"] != "stopped" or row["stop_latched"] is True)
        and all(
            _hash(row[name])
            for name in (
                "request_prefix_sha256",
                "coordinator_policy_sha256",
                "coordinator_tail_sha256",
                "cutover_prefix_sha256",
                "plan_record_sha256",
            )
        )
        and (row["transition_count"] == len(events))
        and row["transition_tail_sha256"] == (events[-1]["event_sha256"] if events else None)
        and _hash(claimed)
        and claimed == sha256(canonical_json(core)),
        "health_snapshot_invalid",
    )
    for name in (
        "coordinator_sequence",
        "selected_count",
        "intent_count",
        "received_count",
        "durable_count",
        "eligible_new_open_count",
        "missing_count",
        "daily_request_count",
        "daily_response_bytes",
        "rolling_request_count",
        "rolling_response_bytes",
        "allocated_bytes",
        "free_bytes",
        "last_progress_ns",
        "transition_count",
    ):
        _require(type(row[name]) is int and row[name] >= 0, f"health_{name}_invalid")
    for name in (
        "cutover_last_dispatch_ns",
        "last_dispatch_ns",
        "bucket_close_ns",
        "plan_sequence",
        "cursor",
        "cooldown_until_ns",
    ):
        _require(
            row[name] is None or (type(row[name]) is int and row[name] >= 0),
            f"health_{name}_invalid",
        )
    _require(
        row["last_error_code"] is None
        or (type(row["last_error_code"]) is str and len(row["last_error_code"]) <= 96),
        "health_error_code_invalid",
    )
    _require(
        row["received_count"] <= row["intent_count"]
        and row["durable_count"] <= row["intent_count"],
        "health_denominator_invalid",
    )
    _require(
        row["eligible_new_open_count"] <= row["durable_count"],
        "health_eligible_denominator_invalid",
    )
    _require(
        row["missing_count"] == max(0, row["selected_count"] - row["intent_count"]),
        "health_missing_denominator_invalid",
    )
    if events:
        _require(row["status"] == events[-1]["to_status"], "health_transition_status_mismatch")
    return row


def read_snapshot(
    writer: DescriptorWriter, *, release_id: str, storage: Mapping[str, Any]
) -> dict[str, Any]:
    root = _health_root(release_id)
    _require(set(writer.list_names(root)) == {LOCK, EVENTS, SNAPSHOT}, "health_root_entries")
    events = read_events(writer, release_id=release_id)
    snapshot = _decode(
        writer.read_bytes(f"{root}/{SNAPSHOT}", max_bytes=MAX_SNAPSHOT_BYTES),
        limit=MAX_SNAPSHOT_BYTES,
        code="health_snapshot",
    )
    return validate_snapshot(snapshot, release_id=release_id, storage=storage, events=events)


def publish_snapshot(
    writer: DescriptorWriter,
    *,
    release_id: str,
    storage: Mapping[str, Any],
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate prior state, append a transition if needed, then atomically replace the snapshot."""
    root = _health_root(release_id)
    with writer.lock(f"{root}/{LOCK}", blocking=False):
        old = read_snapshot(writer, release_id=release_id, storage=storage)
        events = read_events(writer, release_id=release_id)
        return _publish_locked(
            writer,
            root=root,
            release_id=release_id,
            storage=storage,
            fields=fields,
            old=old,
            events=events,
        )


def _publish_locked(
    writer: DescriptorWriter,
    *,
    root: str,
    release_id: str,
    storage: Mapping[str, Any],
    fields: Mapping[str, Any],
    old: Mapping[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    new_status = fields.get("status")
    reason = fields.get("reason")
    _require(new_status in _STATUSES and reason in _REASONS, "health_update_status_invalid")
    if new_status != old["status"]:
        _require(reason != "none", "health_transition_reason_missing")
        _require(len(events) < MAX_EVENTS, "health_event_cap")
        previous = cast(str | None, events[-1]["event_sha256"] if events else None)
        core = _event_core(
            sequence=len(events),
            observed_ns=cast(int, fields["observed_ns"]),
            previous=previous,
            old=cast(str, old["status"]),
            new=cast(str, new_status),
            reason=cast(str, reason),
        )
        event = {**core, "event_sha256": sha256(canonical_json(core))}
        writer.atomic_write(
            f"{root}/{EVENTS}/{len(events):06d}.json",
            canonical_json(event),
            create_only=True,
        )
        events = [*events, event]
    snapshot = {
        "schema": SCHEMA,
        "schema_version": 1,
        "release_id": release_id,
        **dict(fields),
        "transition_count": len(events),
        "transition_tail_sha256": events[-1]["event_sha256"] if events else None,
    }
    snapshot["generation"] = old["generation"] + 1
    snapshot["snapshot_sha256"] = sha256(canonical_json(snapshot))
    validated = validate_snapshot(snapshot, release_id=release_id, storage=storage, events=events)
    writer.atomic_write(f"{root}/{SNAPSHOT}", canonical_json(validated), create_only=False)
    return validated


def initialize_snapshot(
    writer: DescriptorWriter,
    *,
    release_id: str,
    storage: Mapping[str, Any],
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    """Create an inert initial root that starts paused until a verified callback runs."""
    root = _health_root(release_id)
    _require(not writer.exists(root), "health_root_already_exists")
    writer.mkdir(root)
    with writer.lock(f"{root}/{LOCK}", blocking=False):
        writer.mkdir(f"{root}/{EVENTS}")
        initial = dict(fields)
        initial.update(
            status="paused",
            reason="outside_window",
            stop_latched=False,
            generation=0,
            transition_count=0,
            transition_tail_sha256=None,
        )
        snapshot = {
            "schema": SCHEMA,
            "schema_version": 1,
            "release_id": release_id,
            **initial,
        }
        snapshot["snapshot_sha256"] = sha256(canonical_json(snapshot))
        validated = validate_snapshot(snapshot, release_id=release_id, storage=storage, events=[])
        writer.atomic_write(f"{root}/{SNAPSHOT}", canonical_json(validated), create_only=True)
    return validated


__all__ = [
    "EVENTS",
    "LOCK",
    "MAX_EVENTS",
    "ROOT_PREFIX",
    "SCHEMA",
    "SNAPSHOT",
    "HealthError",
    "initialize_snapshot",
    "publish_snapshot",
    "read_events",
    "read_snapshot",
]
