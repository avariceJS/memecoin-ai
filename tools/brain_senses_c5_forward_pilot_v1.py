"""Offline policy and transition checks for the bounded c5 forward-only pilot.

This module never creates a service root, loads credentials, or performs provider I/O.
It describes and validates a future two-bucket run so synthetic tests can exercise its
hard-stop boundaries before any separate operational authorization exists.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1
MAX_REQUESTS = 35
BOOTSTRAP_REQUESTS = 1
FRAME_REQUESTS = 34
BOOTSTRAP_RAW_BYTES = 1_000_000
BOOTSTRAP_STORED_BYTES = 1_000_000
FRAME_RAW_BYTES = 256_000_000
FRAME_STORED_BYTES = 128_000_000
FRAME_WIDTH_MAX = 20_000
SCHEDULED_TICK_MINUTE_MIN = 5
SCHEDULED_TICK_MINUTE_MAX = 15
BUCKET_RE = re.compile(r"^(\d{8})T(\d{2})0000Z$")

# Metadata-only inputs from the pinned c4 abnormal terminal receipt. No c4 capture
# or frozen tail payload is needed by this policy module.
C4_LINEAGE: dict[str, Any] = {
    "terminal_receipt_sha256": "8b97dd4936ddcd6e8494d35e06cb2a86d01929589e4c4c4c5ff7ba91825cbe65",
    "campaign_id": "helius-inventory-v3-20261010-4",
    "stop_sha256": "a7ac1fdd02615491fbea494d35f5bbcba8b03d5bf2a4d11f2516616b39f00098",
    "stop_reason": "minimum_free_disk_floor_plus_frame_envelope",
    "stop_time_ns": 1791677100418875000,
    "last_attempt_bucket": "20261010T230000Z",
    "last_committed": {
        "frontier_slot": 455430160,
        "anchor_slot": 455430159,
        "anchor_request_started_ns": 1791673505187305000,
        "anchor_receipt_ns": 1791673505517633000,
        "availability_ns": 1791673505517633000,
        "last_inventoried_interval": {
            "start": 455413633,
            "end_exclusive": 455430160,
            "inventory_sha256": "ea1e6fc38f86f536fdfa8094236e6a97fa1168919afd79709d804ecef27c5fdf",
            "provider_returned_produced_slots": 16527,
        },
    },
    "predecessor_gaps": [
        {
            "start": 455037817,
            "end_exclusive": 455061516,
            "reason": "oversized_frame",
            "bucket": "20261010T000000Z",
        },
        {
            "start": 455082524,
            "end_exclusive": 455116881,
            "reason": "missed_schedule_no_backfill",
        },
        {
            "start": 455216025,
            "end_exclusive": 455249113,
            "reason": "uncovered_since_c3_trusted_frontier_no_backfill",
            "bootstrap_bucket": "20261010T120000Z",
            "from_c3_stop_sha256": (
                "e8666a111d5e9aadd9c12044490e2d0ad4d42b0a204d57fbae0295ba036a9f00"
            ),
        },
    ],
    "unknown_ended_gap": {
        "start": 455430160,
        "end_exclusive": None,
        "cause": "abnormal_disk_stop_without_successor_anchor",
        "cause_detail": "minimum_free_disk_floor_plus_frame_envelope",
        "closure_rule": "future finalized c5 bootstrap anchor only",
    },
}


class PilotError(ValueError):
    """Raised when a proposed plan or transition violates the frozen c5 contract."""


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_scheduled_tick(bucket: str, tick_at_ns: Any) -> bool:
    if type(tick_at_ns) is not int or tick_at_ns <= 0:
        return False
    bucket_at = _utc_bucket(bucket)
    tick_at = datetime.fromtimestamp(tick_at_ns / 1_000_000_000, UTC)
    return (
        tick_at.date() == bucket_at.date()
        and tick_at.hour == bucket_at.hour
        and SCHEDULED_TICK_MINUTE_MIN <= tick_at.minute <= SCHEDULED_TICK_MINUTE_MAX
    )


def _utc_bucket(value: str) -> datetime:
    match = BUCKET_RE.fullmatch(value)
    if match is None:
        raise PilotError(f"invalid UTC hour bucket: {value!r}")
    try:
        return datetime.strptime(value, "%Y%m%dT%H0000Z").replace(tzinfo=UTC)
    except ValueError as exc:
        raise PilotError(f"invalid UTC hour bucket: {value!r}") from exc


def build_plan(
    *,
    pilot_id: str,
    service_id: str,
    bootstrap_bucket: str,
    frame_bucket: str,
    authorized_at_ns: int,
    source_bindings: dict[str, str],
) -> dict[str, Any]:
    """Build a credential-free plan object; does not persist or initialize a root.

    The two bucket IDs are explicit inputs so a missed pre-pinned tick cannot be
    silently replaced with a later one. The caller must separately verify current
    account and disk gates before any operational activation.
    """
    if not pilot_id or not service_id or pilot_id == service_id:
        raise PilotError("pilot and service IDs must be non-empty and distinct")
    if type(authorized_at_ns) is not int or authorized_at_ns <= 0:
        raise PilotError("authorization time must be a positive integer nanosecond timestamp")
    bootstrap_at = _utc_bucket(bootstrap_bucket)
    frame_at = _utc_bucket(frame_bucket)
    authorized_at = datetime.fromtimestamp(authorized_at_ns / 1_000_000_000, UTC)
    if bootstrap_at <= authorized_at:
        raise PilotError("bootstrap bucket must be strictly future to plan authorization")
    if (frame_at - bootstrap_at).total_seconds() != 3600:
        raise PilotError("frame must use the immediately following distinct UTC hour bucket")
    if not source_bindings or any(
        not isinstance(name, str)
        or not name
        or not isinstance(digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        for name, digest in source_bindings.items()
    ):
        raise PilotError("source bindings must be named SHA-256 values")

    body = {
        "schema_version": SCHEMA_VERSION,
        "pilot_id": pilot_id,
        "service_id": service_id,
        "authorized_at_ns": authorized_at_ns,
        "schedule": {
            "bootstrap_bucket": bootstrap_bucket,
            "frame_bucket": frame_bucket,
            "scheduled_tick_utc_minute_window": [
                SCHEDULED_TICK_MINUTE_MIN,
                SCHEDULED_TICK_MINUTE_MAX,
            ],
            "no_catch_up_or_replacement": True,
            "miss_or_intervening_bucket_action": "stop_for_review",
        },
        "limits": {
            "all_in_requests": MAX_REQUESTS,
            "bootstrap_requests": BOOTSTRAP_REQUESTS,
            "frame_requests": FRAME_REQUESTS,
            "bootstrap_raw_bytes": BOOTSTRAP_RAW_BYTES,
            "bootstrap_stored_bytes": BOOTSTRAP_STORED_BYTES,
            "frame_raw_bytes": FRAME_RAW_BYTES,
            "frame_stored_bytes": FRAME_STORED_BYTES,
            "frame_width_slots": FRAME_WIDTH_MAX,
        },
        "predecessor": C4_LINEAGE,
        "source_bindings": dict(sorted(source_bindings.items())),
        "activation_gates": {
            "account_credit_and_overage_verified": False,
            "disk_floor_and_peak_margin_verified": False,
            "activation_authorized": False,
        },
        "execution": "offline_policy_only_no_root_initialized",
    }
    return {**body, "plan_sha256": _canonical_hash(body)}


def verify_plan(plan: dict[str, Any], *, current_bindings: dict[str, str]) -> None:
    """Fail closed on plan mutation or source drift before any future action."""
    if not isinstance(plan, dict) or plan.get("schema_version") != SCHEMA_VERSION:
        raise PilotError("unsupported c5 plan schema")
    body = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if plan.get("plan_sha256") != _canonical_hash(body):
        raise PilotError("c5 plan hash mismatch")
    if plan.get("source_bindings") != dict(sorted(current_bindings.items())):
        raise PilotError("c5 source binding drift")
    limits = {
        "all_in_requests": MAX_REQUESTS,
        "bootstrap_requests": BOOTSTRAP_REQUESTS,
        "frame_requests": FRAME_REQUESTS,
        "bootstrap_raw_bytes": BOOTSTRAP_RAW_BYTES,
        "bootstrap_stored_bytes": BOOTSTRAP_STORED_BYTES,
        "frame_raw_bytes": FRAME_RAW_BYTES,
        "frame_stored_bytes": FRAME_STORED_BYTES,
        "frame_width_slots": FRAME_WIDTH_MAX,
    }
    if plan.get("limits") != limits:
        raise PilotError("c5 limits differ from the fixed policy")
    schedule = plan.get("schedule")
    if not isinstance(schedule, dict):
        raise PilotError("c5 schedule is malformed")
    if (
        schedule.get("no_catch_up_or_replacement") is not True
        or schedule.get("miss_or_intervening_bucket_action") != "stop_for_review"
    ):
        raise PilotError("c5 schedule permits replacement or catch-up")
    if schedule.get("scheduled_tick_utc_minute_window") != [
        SCHEDULED_TICK_MINUTE_MIN,
        SCHEDULED_TICK_MINUTE_MAX,
    ]:
        raise PilotError("c5 scheduled tick window changed")
    bootstrap_at = _utc_bucket(schedule.get("bootstrap_bucket", ""))
    frame_at = _utc_bucket(schedule.get("frame_bucket", ""))
    authorized_ns = plan.get("authorized_at_ns")
    if type(authorized_ns) is not int or authorized_ns <= 0:
        raise PilotError("c5 authorization timestamp is malformed")
    authorized_at = datetime.fromtimestamp(authorized_ns / 1_000_000_000, UTC)
    if bootstrap_at <= authorized_at or (frame_at - bootstrap_at).total_seconds() != 3600:
        raise PilotError("c5 schedule is not two future adjacent buckets")
    if plan.get("predecessor") != C4_LINEAGE:
        raise PilotError("c4 predecessor lineage changed")
    if plan.get("execution") != "offline_policy_only_no_root_initialized":
        raise PilotError("plan is not an offline-only policy object")
    if plan.get("activation_gates", {}).get("activation_authorized") is not False:
        raise PilotError("this c5 policy artifact does not authorize activation")


def initial_state(plan: dict[str, Any]) -> dict[str, Any]:
    """Return an in-memory synthetic state for offline transition checks."""
    return {
        "plan_sha256": plan.get("plan_sha256"),
        "phase": "awaiting_bootstrap",
        "request_count": 0,
        "frontier_slot": C4_LINEAGE["last_committed"]["frontier_slot"],
        "bootstrap": None,
        "frame": None,
        "stop_reason": None,
    }


def _stop(state: dict[str, Any], reason: str) -> dict[str, Any]:
    stopped = dict(state)
    stopped["phase"] = "stopped_for_review"
    stopped["stop_reason"] = reason
    return stopped


def transition(
    plan: dict[str, Any], state: dict[str, Any], event: dict[str, Any]
) -> dict[str, Any]:
    """Apply one synthetic event; invalid/ambiguous progress becomes terminal STOP.

    This is a pure policy model, not a request runner or durable service controller.
    It intentionally cannot invent a new bucket, retry a request, or resume a STOP.
    """
    verify_plan(plan, current_bindings=plan.get("source_bindings", {}))
    if state.get("plan_sha256") != plan["plan_sha256"]:
        return _stop(state, "state_plan_mismatch")
    if state.get("phase") in ("stopped_for_review", "complete"):
        return _stop(state, "terminal_state_is_immutable")
    kind = event.get("kind")
    schedule = plan["schedule"]

    if kind in {
        "missed_tick",
        "intervening_gap",
        "interruption",
        "transport_failure",
        "ambiguous_result",
        "journal_uncertain",
    }:
        return _stop(state, kind)

    if state["phase"] == "awaiting_bootstrap":
        bucket = schedule["bootstrap_bucket"]
        if (
            kind != "bootstrap_started"
            or event.get("bucket") != bucket
            or not _is_scheduled_tick(bucket, event.get("tick_at_ns"))
        ):
            return _stop(state, "unexpected_or_missed_bootstrap_bucket")
        if state["request_count"] != 0:
            return _stop(state, "duplicate_bootstrap_reservation")
        return {**state, "phase": "bootstrap_in_flight", "request_count": 1}

    if state["phase"] == "bootstrap_in_flight":
        if kind != "bootstrap_sealed":
            return _stop(state, "bootstrap_not_sealed_exactly_once")
        if event.get("bucket") != schedule["bootstrap_bucket"] or event.get("outcome") != "success":
            return _stop(state, "bootstrap_failure_or_bucket_mismatch")
        anchor = event.get("anchor_slot")
        raw = event.get("raw_bytes")
        stored = event.get("stored_bytes")
        receipt_ns = event.get("receipt_ns")
        availability_ns = event.get("availability_ns")
        request_sha256 = event.get("request_sha256")
        response_sha256 = event.get("response_sha256")
        capture_receipt_sha256 = event.get("capture_receipt_sha256")
        if event.get("commitment") != "finalized":
            return _stop(state, "bootstrap_not_finalized")
        if any(type(v) is not int for v in (anchor, raw, stored, receipt_ns, availability_ns)):
            return _stop(state, "bootstrap_evidence_incomplete")
        if not all(
            _is_sha256(value) for value in (request_sha256, response_sha256, capture_receipt_sha256)
        ):
            return _stop(state, "bootstrap_hash_binding_incomplete")
        if raw < 0 or raw > BOOTSTRAP_RAW_BYTES or stored < 0 or stored > BOOTSTRAP_STORED_BYTES:
            return _stop(state, "bootstrap_byte_cap")
        if receipt_ns <= 0 or availability_ns < receipt_ns:
            return _stop(state, "bootstrap_time_order_invalid")
        frontier = C4_LINEAGE["last_committed"]["frontier_slot"]
        if anchor + 1 < frontier:
            return _stop(state, "bootstrap_anchor_regression")
        bootstrap = {
            "anchor_slot": anchor,
            "receipt_ns": receipt_ns,
            "availability_ns": availability_ns,
            "raw_bytes": raw,
            "stored_bytes": stored,
            "request_sha256": request_sha256,
            "response_sha256": response_sha256,
            "capture_receipt_sha256": capture_receipt_sha256,
            "gap_closed": {"start": frontier, "end_exclusive": anchor + 1},
        }
        return {
            **state,
            "phase": "awaiting_frame",
            "frontier_slot": anchor + 1,
            "bootstrap": bootstrap,
        }

    if state["phase"] == "awaiting_frame":
        bucket = schedule["frame_bucket"]
        if (
            kind != "frame_started"
            or event.get("bucket") != bucket
            or not _is_scheduled_tick(bucket, event.get("tick_at_ns"))
        ):
            return _stop(state, "unexpected_or_missed_frame_bucket")
        if state["request_count"] != BOOTSTRAP_REQUESTS:
            return _stop(state, "request_count_before_frame_invalid")
        return {**state, "phase": "frame_in_flight", "request_count": MAX_REQUESTS}

    if state["phase"] == "frame_in_flight":
        if kind != "frame_sealed":
            return _stop(state, "frame_not_sealed_exactly_once")
        if event.get("bucket") != schedule["frame_bucket"] or event.get("outcome") != "complete":
            return _stop(state, "frame_failure_or_bucket_mismatch")
        if event.get("request_count") != FRAME_REQUESTS:
            return _stop(state, "frame_request_count_mismatch")
        raw = event.get("raw_bytes")
        stored = event.get("stored_bytes")
        anchor = event.get("anchor_slot")
        request_journal_sha256 = event.get("request_journal_sha256")
        capture_manifest_sha256 = event.get("capture_manifest_sha256")
        summary_sha256 = event.get("summary_sha256")
        if event.get("commitment") != "finalized":
            return _stop(state, "frame_not_finalized")
        if any(type(v) is not int for v in (raw, stored, anchor)):
            return _stop(state, "frame_evidence_incomplete")
        if not all(
            _is_sha256(value)
            for value in (request_journal_sha256, capture_manifest_sha256, summary_sha256)
        ):
            return _stop(state, "frame_hash_binding_incomplete")
        if raw < 0 or raw > FRAME_RAW_BYTES or stored < 0 or stored > FRAME_STORED_BYTES:
            return _stop(state, "frame_byte_cap")
        start = state["frontier_slot"]
        end_exclusive = anchor + 1
        width = end_exclusive - start
        if width < 1 or width > FRAME_WIDTH_MAX:
            return _stop(state, "frame_width_or_frontier_invalid")
        return {
            **state,
            "phase": "complete",
            "frontier_slot": end_exclusive,
            "frame": {
                "start": start,
                "end_exclusive": end_exclusive,
                "anchor_slot": anchor,
                "request_count": FRAME_REQUESTS,
                "raw_bytes": raw,
                "stored_bytes": stored,
                "request_journal_sha256": request_journal_sha256,
                "capture_manifest_sha256": capture_manifest_sha256,
                "summary_sha256": summary_sha256,
            },
        }

    return _stop(state, "unknown_phase")
