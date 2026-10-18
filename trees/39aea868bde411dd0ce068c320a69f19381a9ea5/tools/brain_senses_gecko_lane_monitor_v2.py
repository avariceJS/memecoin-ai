"""Descriptor-bound latest health snapshot and sparse alarm ledger for Gecko v4.

This monitor is diagnostic only. It does not authorize dispatch, infer provider
health, or replace the independently verified request/coordinator journals.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SCHEMA = "brain-senses-gecko-lane-monitor-v2"
VERSION = 2
SNAPSHOT_PATH = "monitor-v2/health.json"
EVENT_DIR = "monitor-v2/events"
MAX_EVENTS = 256
MAX_EVENT_BYTES = 2048
MAX_EVENT_TOTAL_BYTES = MAX_EVENTS * MAX_EVENT_BYTES
MAX_SNAPSHOT_BYTES = 8192
_SNAPSHOT_KEYS = {
    "schema",
    "schema_version",
    "generation",
    "observed_at_ns",
    "monotonic_sample_ns",
    "status",
    "reason",
    "root_id",
    "volume_uuid",
    "request_count",
    "result_count",
    "request_tail_sha256",
    "result_tail_sha256",
    "coordinator_sequence",
    "coordinator_tail_sha256",
    "coordinator_policy_sha256",
    "active_bucket_close_ns",
    "cooldown_until_ns",
    "stop_latched",
    "free_bytes",
    "source_sha256",
    "plan_sha256",
    "alarm_ledger_count",
    "alarm_ledger_tail_sha256",
    "active_alarm_reason",
    "diagnostic_only",
    "snapshot_sha256",
}
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_STATUS = frozenset({"healthy", "degraded", "stopped"})
_REASONS = frozenset(
    {
        "none",
        "cooldown",
        "provider_error",
        "transport_uncertain",
        "transient_threshold",
        "rate_limited",
        "manual_rearm_required",
        "clock_error",
        "storage_error",
        "integrity_error",
        "capacity_error",
        "unknown",
        "journal_reconciled",
    }
)


class MonitorError(ValueError):
    """Malformed, stale, mismatched, or over-cap diagnostic monitor state."""


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MonitorError("monitor_duplicate_json_key")
        result[key] = value
    return result


def _decode_canonical(raw: bytes, *, max_bytes: int) -> dict[str, Any]:
    if len(raw) > max_bytes:
        raise MonitorError("monitor_object_too_large")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MonitorError("monitor_invalid_json") from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise MonitorError("monitor_noncanonical_json")
    return value


def _nonnegative(value: object, code: str) -> int:
    if type(value) is not int or value < 0:
        raise MonitorError(code)
    return value


def _validate_alarm_event(
    record: dict[str, Any], expected_sequence: int, previous: str | None
) -> None:
    keys = {
        "schema",
        "sequence",
        "observed_at_ns",
        "kind",
        "reason",
        "request_tail_sha256",
        "result_tail_sha256",
        "coordinator_sequence",
        "coordinator_tail_sha256",
        "coordinator_policy_sha256",
        "source_sha256",
        "plan_sha256",
        "reconciliation",
        "previous_sha256",
        "event_sha256",
    }
    if set(record) != keys:
        raise MonitorError("monitor_event_shape")
    event_sha = record["event_sha256"]
    core = dict(record)
    core.pop("event_sha256")
    if (
        record["schema"] != "brain-senses-gecko-monitor-transition-v2"
        or record["sequence"] != expected_sequence
        or type(record["observed_at_ns"]) is not int
        or record["observed_at_ns"] <= 0
        or record["kind"] not in {"alarm", "recovery", "journal_reconciled"}
        or record["reason"] not in _REASONS - {"none"}
        or any(
            not _is_sha(record[name])
            for name in (
                "request_tail_sha256",
                "result_tail_sha256",
                "coordinator_tail_sha256",
                "coordinator_policy_sha256",
                "source_sha256",
                "plan_sha256",
            )
        )
        or type(record["coordinator_sequence"]) is not int
        or record["coordinator_sequence"] < 0
        or record["previous_sha256"] != previous
        or not _is_sha(event_sha)
        or event_sha != sha256(canonical_json(core))
    ):
        raise MonitorError("monitor_event_invalid")
    reconciliation = record["reconciliation"]
    if record["kind"] == "journal_reconciled":
        expected_reconciliation = {
            "prior_snapshot_sha256",
            "prior_request_tail_sha256",
            "prior_result_tail_sha256",
            "prior_coordinator_sequence",
            "prior_coordinator_tail_sha256",
            "new_request_tail_sha256",
            "new_result_tail_sha256",
            "new_coordinator_sequence",
            "new_coordinator_tail_sha256",
            "recovery_proof_sha256",
        }
        if (
            not isinstance(reconciliation, dict)
            or set(reconciliation) != expected_reconciliation
            or any(
                not _is_sha(reconciliation[name])
                for name in (
                    "prior_snapshot_sha256",
                    "prior_request_tail_sha256",
                    "prior_result_tail_sha256",
                    "prior_coordinator_tail_sha256",
                    "new_request_tail_sha256",
                    "new_result_tail_sha256",
                    "new_coordinator_tail_sha256",
                    "recovery_proof_sha256",
                )
            )
            or type(reconciliation["prior_coordinator_sequence"]) is not int
            or type(reconciliation["new_coordinator_sequence"]) is not int
            or reconciliation["prior_coordinator_sequence"] < 0
            or reconciliation["new_coordinator_sequence"] < 0
            or reconciliation["new_request_tail_sha256"] != record["request_tail_sha256"]
            or reconciliation["new_result_tail_sha256"] != record["result_tail_sha256"]
            or reconciliation["new_coordinator_sequence"] != record["coordinator_sequence"]
            or reconciliation["new_coordinator_tail_sha256"] != record["coordinator_tail_sha256"]
        ):
            raise MonitorError("monitor_reconciliation_event_invalid")
    elif reconciliation is not None:
        raise MonitorError("monitor_unexpected_reconciliation_payload")


def read_transition_receipts(writer: DescriptorWriter) -> list[dict[str, Any]]:
    """Read and replay the bounded immutable alarm/recovery receipt ledger."""
    try:
        names = writer.list_names(EVENT_DIR)
    except FileNotFoundError:
        return []
    if len(names) > MAX_EVENTS:
        raise MonitorError("monitor_event_cap")
    expected_names = [f"{index:06d}.json" for index in range(len(names))]
    if list(names) != expected_names:
        raise MonitorError("monitor_event_names")
    records: list[dict[str, Any]] = []
    total = 0
    previous: str | None = None
    active: str | None = None
    for index, name in enumerate(names):
        raw = writer.read_bytes(f"{EVENT_DIR}/{name}", max_bytes=MAX_EVENT_BYTES)
        total += len(raw)
        if total > MAX_EVENT_TOTAL_BYTES:
            raise MonitorError("monitor_event_bytes_cap")
        record = _decode_canonical(raw, max_bytes=MAX_EVENT_BYTES)
        _validate_alarm_event(record, index, previous)
        if record["kind"] == "alarm":
            if active is not None:
                raise MonitorError("monitor_overlapping_alarm_transition")
            active = record["reason"]
        elif record["kind"] == "recovery":
            if active != record["reason"]:
                raise MonitorError("monitor_unmatched_recovery_event")
            active = None
        records.append(record)
        previous = record["event_sha256"]
    return records


def _active_alarm(events: list[dict[str, Any]]) -> str | None:
    active: str | None = None
    for event in events:
        if event["kind"] == "alarm":
            active = event["reason"]
        elif event["kind"] == "recovery":
            active = None
    return active


def _snapshot_core(
    *,
    generation: int,
    observed_at_ns: int,
    monotonic_sample_ns: int,
    status: str,
    reason: str,
    root_id: str,
    volume_uuid: str,
    request_count: int,
    result_count: int,
    request_tail_sha256: str,
    result_tail_sha256: str,
    coordinator_sequence: int,
    coordinator_tail_sha256: str,
    coordinator_policy_sha256: str,
    active_bucket_close_ns: int | None,
    cooldown_until_ns: int | None,
    stop_latched: bool,
    free_bytes: int,
    source_sha256: str,
    plan_sha256: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    if status not in _STATUS or reason not in _REASONS:
        raise MonitorError("monitor_status_reason")
    for hash_name, hash_value in (
        ("request_tail_sha256", request_tail_sha256),
        ("result_tail_sha256", result_tail_sha256),
        ("coordinator_tail_sha256", coordinator_tail_sha256),
        ("coordinator_policy_sha256", coordinator_policy_sha256),
        ("source_sha256", source_sha256),
        ("plan_sha256", plan_sha256),
    ):
        if not _is_sha(hash_value):
            raise MonitorError(f"monitor_{hash_name}")
    if not isinstance(root_id, str) or not root_id or len(root_id) > 128:
        raise MonitorError("monitor_root_id")
    if not isinstance(volume_uuid, str) or not volume_uuid or len(volume_uuid) > 64:
        raise MonitorError("monitor_volume_uuid")
    for field_name, counter_value in (
        ("generation", generation),
        ("observed_at_ns", observed_at_ns),
        ("monotonic_sample_ns", monotonic_sample_ns),
        ("request_count", request_count),
        ("result_count", result_count),
        ("coordinator_sequence", coordinator_sequence),
        ("free_bytes", free_bytes),
    ):
        _nonnegative(counter_value, f"monitor_{field_name}")
    if observed_at_ns == 0 or monotonic_sample_ns == 0:
        raise MonitorError("monitor_timestamp")
    if active_bucket_close_ns is not None:
        _nonnegative(active_bucket_close_ns, "monitor_active_bucket")
    if cooldown_until_ns is not None:
        _nonnegative(cooldown_until_ns, "monitor_cooldown")
    if type(stop_latched) is not bool:
        raise MonitorError("monitor_stop_latched")
    active = _active_alarm(events)
    return {
        "schema": SCHEMA,
        "schema_version": VERSION,
        "generation": generation,
        "observed_at_ns": observed_at_ns,
        "monotonic_sample_ns": monotonic_sample_ns,
        "status": status,
        "reason": reason,
        "root_id": root_id,
        "volume_uuid": volume_uuid.lower(),
        "request_count": request_count,
        "result_count": result_count,
        "request_tail_sha256": request_tail_sha256,
        "result_tail_sha256": result_tail_sha256,
        "coordinator_sequence": coordinator_sequence,
        "coordinator_tail_sha256": coordinator_tail_sha256,
        "coordinator_policy_sha256": coordinator_policy_sha256,
        "active_bucket_close_ns": active_bucket_close_ns,
        "cooldown_until_ns": cooldown_until_ns,
        "stop_latched": stop_latched,
        "free_bytes": free_bytes,
        "source_sha256": source_sha256,
        "plan_sha256": plan_sha256,
        "alarm_ledger_count": len(events),
        "alarm_ledger_tail_sha256": events[-1]["event_sha256"] if events else None,
        "active_alarm_reason": active,
        "diagnostic_only": True,
    }


def _read_snapshot(writer: DescriptorWriter) -> dict[str, Any] | None:
    try:
        raw = writer.read_bytes(SNAPSHOT_PATH, max_bytes=MAX_SNAPSHOT_BYTES)
    except FileNotFoundError:
        return None
    snapshot = _decode_canonical(raw, max_bytes=MAX_SNAPSHOT_BYTES)
    if set(snapshot) != _SNAPSHOT_KEYS:
        raise MonitorError("monitor_snapshot_shape")
    claimed = snapshot.pop("snapshot_sha256", None)
    if (
        snapshot.get("schema") != SCHEMA
        or snapshot.get("schema_version") != VERSION
        or not _is_sha(claimed)
        or claimed != sha256(canonical_json(snapshot))
    ):
        raise MonitorError("monitor_snapshot_hash_or_schema")
    for name in (
        "generation",
        "observed_at_ns",
        "monotonic_sample_ns",
        "request_count",
        "result_count",
        "coordinator_sequence",
        "free_bytes",
        "alarm_ledger_count",
    ):
        _nonnegative(snapshot[name], f"monitor_snapshot_{name}")
    if (
        snapshot["observed_at_ns"] == 0
        or snapshot["monotonic_sample_ns"] == 0
        or snapshot["status"] not in _STATUS
        or snapshot["reason"] not in _REASONS
        or not isinstance(snapshot["root_id"], str)
        or not snapshot["root_id"]
        or not isinstance(snapshot["volume_uuid"], str)
        or not snapshot["volume_uuid"]
        or snapshot["volume_uuid"] != snapshot["volume_uuid"].lower()
        or type(snapshot["stop_latched"]) is not bool
        or snapshot["diagnostic_only"] is not True
        or any(
            not _is_sha(snapshot[name])
            for name in (
                "request_tail_sha256",
                "result_tail_sha256",
                "coordinator_tail_sha256",
                "coordinator_policy_sha256",
                "source_sha256",
                "plan_sha256",
            )
        )
        or snapshot["alarm_ledger_count"] > MAX_EVENTS
        or (
            snapshot["alarm_ledger_tail_sha256"] is not None
            and not _is_sha(snapshot["alarm_ledger_tail_sha256"])
        )
        or (
            snapshot["active_bucket_close_ns"] is not None
            and (
                type(snapshot["active_bucket_close_ns"]) is not int
                or snapshot["active_bucket_close_ns"] < 0
            )
        )
        or (
            snapshot["cooldown_until_ns"] is not None
            and (
                type(snapshot["cooldown_until_ns"]) is not int or snapshot["cooldown_until_ns"] < 0
            )
        )
        or (
            snapshot["active_alarm_reason"] is not None
            and snapshot["active_alarm_reason"] not in _REASONS - {"none"}
        )
    ):
        raise MonitorError("monitor_snapshot_fields")
    snapshot["snapshot_sha256"] = claimed
    return snapshot


def validate_snapshot(
    writer: DescriptorWriter,
    *,
    expected_request_tail_sha256: str,
    expected_result_tail_sha256: str,
    verified_coordinator_ancestors: list[Mapping[str, Any]],
    expected_coordinator_policy_sha256: str,
    expected_root_id: str | None = None,
    expected_volume_uuid: str | None = None,
    expected_source_sha256: str | None = None,
    expected_plan_sha256: str | None = None,
    now_ns: int | None = None,
    max_age_ns: int | None = None,
) -> dict[str, Any]:
    """Validate latest snapshot against independently verified live journal tails.

    `monotonic_sample_ns` is retained for diagnostics only and is never compared
    across process restarts. Liveness freshness is computed by the caller.
    """
    snapshot = _read_snapshot(writer)
    if snapshot is None:
        raise MonitorError("monitor_snapshot_missing")
    events = read_transition_receipts(writer)
    count = snapshot["alarm_ledger_count"]
    if type(count) is not int or count < 0 or count > len(events):
        raise MonitorError("monitor_ledger_snapshot_mismatch")
    tip = events[count - 1]["event_sha256"] if count else None
    if snapshot["alarm_ledger_tail_sha256"] != tip:
        raise MonitorError("monitor_ledger_snapshot_mismatch")
    if snapshot["active_alarm_reason"] != _active_alarm(events[:count]):
        raise MonitorError("monitor_alarm_snapshot_mismatch")
    if (
        snapshot["request_tail_sha256"] != expected_request_tail_sha256
        or snapshot["result_tail_sha256"] != expected_result_tail_sha256
    ):
        raise MonitorError("monitor_journal_tail_mismatch")
    if not _is_sha(expected_coordinator_policy_sha256):
        raise MonitorError("monitor_coordinator_policy_pin")
    ancestors: dict[int, Mapping[str, Any]] = {}
    for ancestor in verified_coordinator_ancestors:
        if not isinstance(ancestor, Mapping) or set(ancestor) != {
            "sequence",
            "tail_sha256",
            "policy_sha256",
            "root_id",
            "volume_uuid",
        }:
            raise MonitorError("monitor_coordinator_ancestor_shape")
        sequence = ancestor["sequence"]
        if (
            type(sequence) is not int
            or sequence < 0
            or sequence in ancestors
            or not _is_sha(ancestor["tail_sha256"])
            or not _is_sha(ancestor["policy_sha256"])
        ):
            raise MonitorError("monitor_coordinator_ancestor_invalid")
        ancestors[sequence] = ancestor
    snapshot_ancestor = ancestors.get(snapshot["coordinator_sequence"])
    if (
        snapshot_ancestor is None
        or snapshot_ancestor["tail_sha256"] != snapshot["coordinator_tail_sha256"]
        or snapshot_ancestor["policy_sha256"] != snapshot["coordinator_policy_sha256"]
        or snapshot["coordinator_policy_sha256"] != expected_coordinator_policy_sha256
        or snapshot_ancestor["root_id"] != snapshot["root_id"]
        or str(snapshot_ancestor["volume_uuid"]).lower() != snapshot["volume_uuid"]
    ):
        raise MonitorError("monitor_coordinator_ancestor_mismatch")
    for field, expected in (
        ("root_id", expected_root_id),
        ("volume_uuid", expected_volume_uuid.lower() if expected_volume_uuid else None),
        ("source_sha256", expected_source_sha256),
        ("plan_sha256", expected_plan_sha256),
    ):
        if expected is not None and snapshot[field] != expected:
            raise MonitorError(f"monitor_{field}_mismatch")
    if now_ns is not None or max_age_ns is not None:
        if type(now_ns) is not int or type(max_age_ns) is not int or max_age_ns < 0:
            raise MonitorError("monitor_freshness_args")
        age = now_ns - snapshot["observed_at_ns"]
        if age < 0 or age > max_age_ns:
            raise MonitorError("monitor_stale")
    return snapshot


def publish_snapshot(
    writer: DescriptorWriter,
    *,
    observed_at_ns: int,
    monotonic_sample_ns: int,
    status: str,
    reason: str,
    root_id: str,
    volume_uuid: str,
    request_count: int,
    result_count: int,
    request_tail_sha256: str,
    result_tail_sha256: str,
    coordinator_sequence: int,
    coordinator_tail_sha256: str,
    coordinator_policy_sha256: str,
    active_bucket_close_ns: int | None,
    cooldown_until_ns: int | None,
    stop_latched: bool,
    free_bytes: int,
    source_sha256: str,
    plan_sha256: str,
    reconciliation: Mapping[str, Any] | None = None,
    completed_operation: bool = False,
) -> dict[str, Any]:
    """Write sparse immutable transition receipt(s), then atomically replace latest."""
    events = read_transition_receipts(writer)
    prior_snapshot = _read_snapshot(writer)
    if prior_snapshot is not None:
        if prior_snapshot["alarm_ledger_count"] > len(events):
            raise MonitorError("monitor_snapshot_ahead_of_ledger")
        prior_tip = (
            events[prior_snapshot["alarm_ledger_count"] - 1]["event_sha256"]
            if prior_snapshot["alarm_ledger_count"]
            else None
        )
        if prior_snapshot["alarm_ledger_tail_sha256"] != prior_tip:
            raise MonitorError("monitor_snapshot_ledger_mismatch")
        local_tails_changed = (
            request_tail_sha256 != prior_snapshot["request_tail_sha256"]
            or result_tail_sha256 != prior_snapshot["result_tail_sha256"]
        )
        if type(completed_operation) is not bool:
            raise MonitorError("monitor_completed_operation_type")
        if local_tails_changed and reconciliation is None and not completed_operation:
            raise MonitorError("monitor_unreceipted_journal_advance")
    elif type(completed_operation) is not bool:
        raise MonitorError("monitor_completed_operation_type")
    active = _active_alarm(events)
    alarm_now = status != "healthy" or stop_latched
    desired_reason = reason if reason != "none" else "unknown"
    event_specs: list[tuple[str, str, Mapping[str, Any] | None]] = []
    if reconciliation is not None:
        required = {
            "prior_snapshot_sha256",
            "prior_request_tail_sha256",
            "prior_result_tail_sha256",
            "prior_coordinator_sequence",
            "prior_coordinator_tail_sha256",
            "new_request_tail_sha256",
            "new_result_tail_sha256",
            "new_coordinator_sequence",
            "new_coordinator_tail_sha256",
            "recovery_proof_sha256",
        }
        if (
            prior_snapshot is None
            or set(reconciliation) != required
            or reconciliation["prior_snapshot_sha256"] != prior_snapshot["snapshot_sha256"]
            or reconciliation["prior_request_tail_sha256"] != prior_snapshot["request_tail_sha256"]
            or reconciliation["prior_result_tail_sha256"] != prior_snapshot["result_tail_sha256"]
            or reconciliation["prior_coordinator_sequence"]
            != prior_snapshot["coordinator_sequence"]
            or reconciliation["prior_coordinator_tail_sha256"]
            != prior_snapshot["coordinator_tail_sha256"]
            or reconciliation["new_request_tail_sha256"] != request_tail_sha256
            or reconciliation["new_result_tail_sha256"] != result_tail_sha256
            or reconciliation["new_coordinator_sequence"] != coordinator_sequence
            or reconciliation["new_coordinator_tail_sha256"] != coordinator_tail_sha256
            or not _is_sha(reconciliation["recovery_proof_sha256"])
            or (
                request_tail_sha256 == prior_snapshot["request_tail_sha256"]
                and result_tail_sha256 == prior_snapshot["result_tail_sha256"]
                and coordinator_sequence == prior_snapshot["coordinator_sequence"]
                and coordinator_tail_sha256 == prior_snapshot["coordinator_tail_sha256"]
            )
        ):
            raise MonitorError("monitor_reconciliation_binding")
        event_specs.append(("journal_reconciled", "journal_reconciled", reconciliation))
    if alarm_now:
        if active is not None and active != desired_reason:
            event_specs.append(("recovery", active, None))
            active = None
        if active != desired_reason:
            event_specs.append(("alarm", desired_reason, None))
    elif active is not None:
        event_specs.append(("recovery", active, None))
    if reconciliation is not None:
        matching_reconciliations = [
            event
            for event in events
            if event["kind"] == "journal_reconciled"
            and event["reconciliation"]["prior_snapshot_sha256"]
            == reconciliation["prior_snapshot_sha256"]
        ]
        if matching_reconciliations:
            previous_receipt = matching_reconciliations[-1]
            if (
                previous_receipt["reconciliation"] != dict(reconciliation)
                or previous_receipt["request_tail_sha256"] != request_tail_sha256
                or previous_receipt["result_tail_sha256"] != result_tail_sha256
                or previous_receipt["coordinator_sequence"] != coordinator_sequence
                or previous_receipt["coordinator_tail_sha256"] != coordinator_tail_sha256
                or previous_receipt["coordinator_policy_sha256"] != coordinator_policy_sha256
            ):
                raise MonitorError("monitor_reconciliation_replay_conflict")
            event_specs = [spec for spec in event_specs if spec[0] != "journal_reconciled"]
    if len(events) + len(event_specs) > MAX_EVENTS:
        raise MonitorError("monitor_event_cap")
    for event_kind, event_reason, event_reconciliation in event_specs:
        event: dict[str, Any] = {
            "schema": "brain-senses-gecko-monitor-transition-v2",
            "sequence": len(events),
            "observed_at_ns": observed_at_ns,
            "kind": event_kind,
            "reason": event_reason,
            "request_tail_sha256": request_tail_sha256,
            "result_tail_sha256": result_tail_sha256,
            "coordinator_sequence": coordinator_sequence,
            "coordinator_tail_sha256": coordinator_tail_sha256,
            "coordinator_policy_sha256": coordinator_policy_sha256,
            "source_sha256": source_sha256,
            "plan_sha256": plan_sha256,
            "reconciliation": (
                dict(event_reconciliation) if event_reconciliation is not None else None
            ),
            "previous_sha256": events[-1]["event_sha256"] if events else None,
        }
        event["event_sha256"] = sha256(canonical_json(event))
        event_raw = canonical_json(event)
        if len(event_raw) > MAX_EVENT_BYTES:
            raise MonitorError("monitor_event_too_large")
        writer.create(f"{EVENT_DIR}/{len(events):06d}.json", event_raw)
        events.append(event)
    if event_specs:
        events = read_transition_receipts(writer)
    generation = (prior_snapshot["generation"] + 1) if prior_snapshot else 0
    snapshot = _snapshot_core(
        generation=generation,
        observed_at_ns=observed_at_ns,
        monotonic_sample_ns=monotonic_sample_ns,
        status=status,
        reason=reason,
        root_id=root_id,
        volume_uuid=volume_uuid,
        request_count=request_count,
        result_count=result_count,
        request_tail_sha256=request_tail_sha256,
        result_tail_sha256=result_tail_sha256,
        coordinator_sequence=coordinator_sequence,
        coordinator_tail_sha256=coordinator_tail_sha256,
        coordinator_policy_sha256=coordinator_policy_sha256,
        active_bucket_close_ns=active_bucket_close_ns,
        cooldown_until_ns=cooldown_until_ns,
        stop_latched=stop_latched,
        free_bytes=free_bytes,
        source_sha256=source_sha256,
        plan_sha256=plan_sha256,
        events=events,
    )
    snapshot["snapshot_sha256"] = sha256(canonical_json(snapshot))
    raw = canonical_json(snapshot)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise MonitorError("monitor_snapshot_too_large")
    writer.atomic_write(SNAPSHOT_PATH, raw)
    return snapshot


def snapshot_is_stale(snapshot: Mapping[str, Any], *, now_ns: int, max_age_ns: int) -> bool:
    """Return external wall-clock liveness; no liveness claim is persisted."""
    if type(now_ns) is not int or type(max_age_ns) is not int or max_age_ns < 0:
        raise MonitorError("monitor_freshness_args")
    observed = snapshot.get("observed_at_ns")
    if type(observed) is not int or observed <= 0:
        raise MonitorError("monitor_observed_time")
    return now_ns < observed or now_ns - observed > max_age_ns


__all__ = [
    "MAX_EVENT_BYTES",
    "MAX_EVENT_TOTAL_BYTES",
    "MAX_EVENTS",
    "MAX_SNAPSHOT_BYTES",
    "MonitorError",
    "publish_snapshot",
    "read_transition_receipts",
    "snapshot_is_stale",
    "validate_snapshot",
]
