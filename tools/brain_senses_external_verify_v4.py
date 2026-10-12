#!/usr/bin/env python3
"""Independent control-chain and epoch-transition verifier for Senses v4."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from tools import brain_senses_external_rolling_policy_v1 as policy
from tools import brain_senses_external_schema_v4 as schema
from tools.brain_senses_external_rolling_policy_v1 import HOUR_NS

TerminalStatus = Literal["complete", "cap_paused", "abandoned"]


class VerifyError(ValueError):
    """Invalid rolling service evidence or epoch transition."""


@dataclass(frozen=True)
class VerifiedTerminal:
    """Result supplied only by the independent full epoch verifier."""

    terminal_sha256: str
    event_tail_sha256: str
    status: TerminalStatus
    pause_reason: str | None = None


@dataclass(frozen=True)
class ControlProjection:
    """Verified current rolling state reconstructed from the control chain."""

    active_epoch_id: str | None
    budget_paused: bool
    pause_reason: str | None
    cooldown_until_ns: int
    epoch_ids: tuple[str, ...]
    event_tail_sha256: str | None
    pending_terminal_epoch_id: str | None


@dataclass(frozen=True)
class EpochProjection:
    """Verified request/window state; unresolved intents are spent, never retryable."""

    epoch_id: str
    next_window_index: int
    active_bucket: str | None
    active_mode: str | None
    request_states: tuple[tuple[str, str], ...]
    missed_buckets: tuple[str, ...]
    sealed_buckets: tuple[str, ...]
    reset_anchor_required: bool
    terminal_status: str | None
    event_tail_sha256: str | None


PlanValidator = Callable[[bytes, str], Mapping[str, Any]]


_PLAN_FIELDS = {
    "schema",
    "schema_version",
    "service_id",
    "epoch_id",
    "policy_id",
    "policy_sha256",
    "source_bindings",
    "source_bindings_sha256",
    "provider",
    "endpoint_origin",
    "storage",
    "created_ns",
    "anchor_ns",
    "prior_interval",
    "windows",
    "caps",
}


def verify_plan(
    payload: bytes,
    *,
    expected_sources: Mapping[str, str],
    expected_root_id: str,
    expected_volume_uuid: str,
) -> dict[str, Any]:
    """Independently derive the immutable schedule and hard caps from policy."""
    value = schema.decode(payload)
    if (
        not isinstance(value, dict)
        or set(value) != _PLAN_FIELDS
        or schema.canonical(value) != payload
        or value.get("schema") != schema.PLAN_SCHEMA
        or value.get("schema_version") != 4
        or value.get("service_id") != schema.SERVICE_ID
        or value.get("policy_id") != policy.POLICY_ID
        or value.get("policy_sha256") != schema.sha(schema.canonical(policy.policy_document()))
        or value.get("source_bindings") != dict(sorted(expected_sources.items()))
        or value.get("source_bindings_sha256")
        != schema.sha(schema.canonical(dict(sorted(expected_sources.items()))))
        or value.get("provider") != "helius-free"
        or value.get("endpoint_origin") != "https://mainnet.helius-rpc.com"
        or value.get("prior_interval") != "unobserved_no_continuity_claim"
        or value.get("caps") != policy.policy_document()
    ):
        raise VerifyError("plan_identity_invalid")
    storage = value.get("storage")
    epoch = schema.validate_epoch_id(value.get("epoch_id"))
    if (
        not isinstance(storage, dict)
        or set(storage) != {"root_id", "volume_uuid", "relative_root"}
        or storage.get("root_id") != expected_root_id
        or not isinstance(storage.get("volume_uuid"), str)
        or storage["volume_uuid"].lower() != expected_volume_uuid.lower()
        or storage.get("relative_root") != f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch}"
    ):
        raise VerifyError("plan_storage_binding_invalid")
    created_ns, anchor_ns = value.get("created_ns"), value.get("anchor_ns")
    if (
        type(created_ns) is not int
        or created_ns <= 0
        or type(anchor_ns) is not int
        or anchor_ns < created_ns + 2 * policy.MINUTE_NS
        or epoch != schema.epoch_id(anchor_ns, epoch.rsplit("-", 1)[-1])
    ):
        raise VerifyError("plan_clock_binding_invalid")
    expected_windows = [
        {
            "kind": row.kind,
            "index": row.index,
            "bucket": row.bucket,
            "start_ns": row.start_ns,
            "end_ns": row.end_ns,
            "request_cap": row.request_cap,
        }
        for row in policy.build_epoch(anchor_ns)
    ]
    if value.get("windows") != expected_windows:
        raise VerifyError("plan_schedule_invalid")
    return value


def verify_epoch_events(plan: Mapping[str, Any], event_bytes: bytes) -> EpochProjection:
    """Check epoch ordering, gaps, durable request identity, and no-replay state.

    Raw capture files and their independent response receipts are checked by
    the full verifier before this projection is eligible for dispatch.
    """
    epoch = schema.validate_epoch_id(plan.get("epoch_id"))
    windows = plan.get("windows")
    if not isinstance(windows, list) or len(windows) != 25:
        raise VerifyError("epoch_plan_windows_invalid")
    try:
        events = schema.verify_event_chain(event_bytes, schema=schema.EVENT_SCHEMA)
    except schema.SchemaError as exc:
        raise VerifyError("epoch_event_chain_invalid") from exc
    if not events:
        return EpochProjection(
            epoch_id=epoch,
            next_window_index=0,
            active_bucket=None,
            active_mode=None,
            request_states=(),
            missed_buckets=(),
            sealed_buckets=(),
            reset_anchor_required=False,
            terminal_status=None,
            event_tail_sha256=None,
        )

    first = events[0]
    if (
        first["kind"] != "epoch_started"
        or set(first["fields"]) != {"epoch_id", "plan_sha256", "anchor_ns"}
        or first["fields"]["epoch_id"] != epoch
        or type(first["fields"]["anchor_ns"]) is not int
        or first["fields"]["anchor_ns"] != plan.get("anchor_ns")
    ):
        raise VerifyError("epoch_start_binding_invalid")
    plan_sha = schema.sha(schema.canonical(dict(plan)))
    if first["fields"]["plan_sha256"] != plan_sha:
        raise VerifyError("epoch_start_plan_hash_invalid")

    next_index = 0
    active_bucket: str | None = None
    active_mode: str | None = None
    reserved_requests: list[str] = []
    request_states: dict[str, str] = {}
    request_methods: dict[str, str] = {}
    missed: list[str] = []
    sealed: list[str] = []
    reset_required = False
    terminal_status: str | None = None
    faulted = False

    for event in events[1:]:
        kind = event["kind"]
        fields = event["fields"]
        at_ns = event["at_ns"]
        if terminal_status is not None:
            raise VerifyError("epoch_event_after_terminal")
        if faulted and kind != "epoch_terminal":
            raise VerifyError("event_after_provider_or_integrity_fault")
        if kind == "window_missed":
            if (
                set(fields) != {"index", "bucket", "kind", "start_ns", "end_ns"}
                or active_bucket is not None
                or next_index >= len(windows)
            ):
                raise VerifyError("missed_window_fields")
            row = windows[next_index]
            if (
                fields["index"] != next_index
                or fields["bucket"] != row.get("bucket")
                or fields["kind"] != row.get("kind")
                or fields["start_ns"] != row.get("start_ns")
                or fields["end_ns"] != row.get("end_ns")
                or at_ns < row.get("end_ns")
            ):
                raise VerifyError("missed_window_binding")
            missed.append(row["bucket"])
            next_index += 1
            reset_required = True
        elif kind == "window_reserved":
            if (
                set(fields) != {"index", "bucket", "kind", "mode", "request_cap"}
                or active_bucket is not None
                or next_index >= len(windows)
            ):
                raise VerifyError("window_reservation_fields")
            row = windows[next_index]
            expected_mode = (
                "anchor" if next_index == 0 else "anchor_reset" if reset_required else "inventory"
            )
            if (
                fields["index"] != next_index
                or fields["bucket"] != row.get("bucket")
                or fields["kind"] != row.get("kind")
                or fields["mode"] != expected_mode
                or fields["request_cap"] != row.get("request_cap")
                or not row.get("start_ns") <= at_ns < row.get("end_ns")
            ):
                raise VerifyError("window_reservation_binding")
            active_bucket = row["bucket"]
            active_mode = expected_mode
            reserved_requests = []
        elif kind == "request_intent":
            if (
                set(fields)
                != {
                    "request_id",
                    "bucket",
                    "ordinal",
                    "method",
                    "params",
                    "wire_request_sha256",
                    "decision_ns",
                }
                or active_bucket is None
                or fields.get("bucket") != active_bucket
            ):
                raise VerifyError("request_intent_fields")
            request_id = fields["request_id"]
            ordinal = fields["ordinal"]
            method = fields["method"]
            params = fields["params"]
            if (
                not isinstance(request_id, str)
                or request_id in request_states
                or ordinal != len(reserved_requests)
                or not isinstance(method, str)
                or method not in {"getSlot", "getBlocks", "getBlock", "getSignaturesForAddress"}
                or not isinstance(params, list)
                or type(fields["decision_ns"]) is not int
                or fields["decision_ns"] != at_ns
                or not isinstance(fields["wire_request_sha256"], str)
                or len(fields["wire_request_sha256"]) != 64
                or request_id != f"{active_bucket}-{ordinal:02d}"
                or any(state != "captured" for state in request_states.values())
            ):
                raise VerifyError("request_intent_binding")
            wire = schema.canonical(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
            ).rstrip(b"\n")
            if schema.sha(wire) != fields["wire_request_sha256"]:
                raise VerifyError("request_wire_hash_mismatch")
            request_states[request_id] = "possibly_spent"
            request_methods[request_id] = method
            reserved_requests.append(request_id)
        elif kind in {"request_captured", "request_failed"}:
            request_id = fields.get("request_id")
            if (
                not isinstance(request_id, str)
                or request_id not in request_states
                or request_states[request_id] != "possibly_spent"
                or request_id not in reserved_requests
            ):
                raise VerifyError("request_outcome_without_unique_intent")
            if kind == "request_captured":
                if set(fields) != {
                    "request_id",
                    "response_sha256",
                    "response_bytes",
                    "availability_ns",
                }:
                    raise VerifyError("request_capture_fields")
                digest = fields["response_sha256"]
                size = fields["response_bytes"]
                availability_ns = fields["availability_ns"]
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or type(size) is not int
                    or not 0 <= size <= policy.MAX_RESPONSE_BYTES
                    or type(availability_ns) is not int
                    or availability_ns < at_ns
                ):
                    raise VerifyError("request_capture_binding")
                request_states[request_id] = "captured"
            else:
                if set(fields) != {"request_id", "failure_class", "certainty"}:
                    raise VerifyError("request_failure_fields")
                if (
                    fields["failure_class"]
                    not in {
                        "provider_http",
                        "provider_quota_or_billing",
                        "provider_transport",
                        "response_invalid",
                        "response_too_large",
                        "clock_or_storage_denied_after_intent",
                    }
                    or fields["certainty"] != "possibly_spent"
                ):
                    raise VerifyError("request_failure_binding")
                request_states[request_id] = "failed_possibly_spent"
                faulted = True
        elif kind == "frame_sealed":
            if (
                set(fields) != {"index", "bucket", "request_ids", "request_count"}
                or active_bucket is None
                or fields.get("bucket") != active_bucket
                or fields.get("index") != next_index
                or fields.get("request_ids") != reserved_requests
                or fields.get("request_count") != len(reserved_requests)
                or any(request_states[item] != "captured" for item in reserved_requests)
                or not reserved_requests
            ):
                raise VerifyError("frame_seal_invalid")
            methods = [request_methods[item] for item in reserved_requests]
            if active_mode in {"anchor", "anchor_reset"} and methods != ["getSlot"]:
                raise VerifyError("anchor_request_shape")
            if active_mode == "inventory" and (
                len(methods) < 2 or methods[:2] != ["getSlot", "getBlocks"]
            ):
                raise VerifyError("inventory_request_shape")
            sealed.append(active_bucket)
            next_index += 1
            active_bucket = None
            active_mode = None
            reserved_requests = []
            reset_required = False
        elif kind == "epoch_terminal":
            if set(fields) != {"epoch_id", "status", "plan_sha256"}:
                raise VerifyError("epoch_terminal_fields")
            status = fields["status"]
            if (
                fields["epoch_id"] != epoch
                or fields["plan_sha256"] != plan_sha
                or status not in {"complete", "cap_paused", "abandoned"}
                or (
                    status == "complete"
                    and (active_bucket is not None or next_index != len(windows))
                )
            ):
                raise VerifyError("epoch_terminal_binding")
            terminal_status = status
        else:
            raise VerifyError("epoch_event_kind_invalid")

    unresolved = any(
        state in {"possibly_spent", "failed_possibly_spent"} for state in request_states.values()
    )
    if terminal_status == "complete" and unresolved:
        raise VerifyError("complete_epoch_has_unresolved_intent")
    tail = events[-1]["event_sha256"]
    return EpochProjection(
        epoch_id=epoch,
        next_window_index=next_index,
        active_bucket=active_bucket,
        active_mode=active_mode,
        request_states=tuple(sorted(request_states.items())),
        missed_buckets=tuple(missed),
        sealed_buckets=tuple(sealed),
        reset_anchor_required=reset_required,
        terminal_status=terminal_status,
        event_tail_sha256=tail,
    )


def verify_control_chain(
    control_bytes: bytes,
    *,
    plans: Mapping[str, bytes],
    terminals: Mapping[str, VerifiedTerminal],
    budget_evidence: Mapping[str, str],
    plan_validator: PlanValidator,
) -> ControlProjection:
    """Verify one active epoch at most and every independently checked terminal.

    ``terminals`` must be populated by reopening and fully validating the exact
    epoch root. The control chain itself never treats a terminal JSON boolean
    as independent verification.
    """
    try:
        events = schema.verify_event_chain(control_bytes, schema=schema.CONTROL_SCHEMA)
    except schema.SchemaError as exc:
        raise VerifyError("control_chain_invalid") from exc

    active: str | None = None
    paused = False
    pause_reason: str | None = None
    pending_pause = False
    cooldown_until = 0
    opened: list[str] = []
    seen: set[str] = set()
    closed: set[str] = set()

    for event in events:
        kind = event["kind"]
        fields = event["fields"]
        at_ns = event["at_ns"]
        if kind == "epoch_open":
            if set(fields) != {"epoch_id", "plan_sha256", "anchor_ns"}:
                raise VerifyError("epoch_open_fields")
            epoch_id = schema.validate_epoch_id(fields["epoch_id"])
            raw_plan = plans.get(epoch_id)
            plan_digest = fields["plan_sha256"]
            anchor_ns = fields["anchor_ns"]
            if (
                active is not None
                or paused
                or pending_pause
                or epoch_id in seen
                or raw_plan is None
                or not isinstance(plan_digest, str)
                or schema.sha(raw_plan) != plan_digest
                or type(anchor_ns) is not int
                or anchor_ns <= max(at_ns, cooldown_until)
            ):
                raise VerifyError("epoch_open_transition")
            try:
                plan = plan_validator(raw_plan, epoch_id)
            except Exception as exc:
                raise VerifyError("epoch_plan_invalid") from exc
            if plan.get("anchor_ns") != anchor_ns or plan.get("epoch_id") != epoch_id:
                raise VerifyError("epoch_plan_binding")
            active = epoch_id
            seen.add(epoch_id)
            opened.append(epoch_id)
        elif kind in {"epoch_close", "epoch_abandon"}:
            required = {"epoch_id", "terminal_sha256", "event_tail_sha256"}
            if kind == "epoch_abandon":
                required.add("cooldown_until_ns")
            if set(fields) != required or active is None:
                raise VerifyError("epoch_terminal_fields")
            epoch_id = schema.validate_epoch_id(fields["epoch_id"])
            terminal = terminals.get(epoch_id)
            if (
                epoch_id != active
                or terminal is None
                or terminal.terminal_sha256 != fields["terminal_sha256"]
                or terminal.event_tail_sha256 != fields["event_tail_sha256"]
            ):
                raise VerifyError("epoch_terminal_binding")
            if kind == "epoch_close" and terminal.status not in {"complete", "cap_paused"}:
                raise VerifyError("normal_close_status")
            if kind == "epoch_abandon":
                cooldown = fields["cooldown_until_ns"]
                if (
                    terminal.status != "abandoned"
                    or type(cooldown) is not int
                    or cooldown < at_ns + HOUR_NS
                ):
                    raise VerifyError("abandon_cooldown")
                cooldown_until = cooldown
            if terminal.status == "cap_paused":
                if terminal.pause_reason not in {
                    "rolling_request_cap",
                    "rolling_credit_cap",
                    "rolling_response_cap",
                    "rolling_physical_cap",
                    "storage_floor",
                }:
                    raise VerifyError("terminal_pause_reason")
                pending_pause = True
                pause_reason = terminal.pause_reason
            else:
                pending_pause = False
                pause_reason = None
            active = None
            closed.add(epoch_id)
        elif kind == "budget_pause":
            if active is not None or paused or set(fields) != {"reason", "evidence_sha256"}:
                raise VerifyError("budget_pause_transition")
            reason = fields["reason"]
            evidence_hash = fields["evidence_sha256"]
            if (
                reason
                not in {
                    "rolling_request_cap",
                    "rolling_credit_cap",
                    "rolling_response_cap",
                    "rolling_physical_cap",
                    "storage_floor",
                }
                or budget_evidence.get(evidence_hash) != f"exceeded:{reason}"
            ):
                raise VerifyError("budget_pause_reason")
            if pending_pause and reason != pause_reason:
                raise VerifyError("budget_pause_reason_mismatch")
            paused, pause_reason, pending_pause = True, reason, False
        elif kind == "budget_resume":
            if (
                active is not None
                or not paused
                or set(fields) != {"reason", "evidence_sha256"}
                or fields["reason"] != pause_reason
                or not isinstance(fields["evidence_sha256"], str)
                or len(fields["evidence_sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in fields["evidence_sha256"])
                or budget_evidence.get(fields["evidence_sha256"]) != f"headroom:{pause_reason}"
            ):
                raise VerifyError("budget_resume_transition")
            paused, pause_reason = False, None
        else:
            raise VerifyError("control_kind_unknown")

    pending_terminal = active if active is not None and active in terminals else None
    allowed_terminals = closed | ({pending_terminal} if pending_terminal is not None else set())
    if set(plans) != seen or set(terminals) != allowed_terminals:
        raise VerifyError("control_artifact_set_mismatch")

    tail = events[-1]["event_sha256"] if events else None
    return ControlProjection(
        active_epoch_id=active,
        budget_paused=paused,
        pause_reason=pause_reason,
        cooldown_until_ns=cooldown_until,
        epoch_ids=tuple(opened),
        event_tail_sha256=tail,
        pending_terminal_epoch_id=pending_terminal,
    )
