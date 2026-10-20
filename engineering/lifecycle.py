"""Pure fail-closed lifecycle planning; this module performs no process or service actions."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Literal


class LifecycleError(ValueError):
    """Malformed durable lifecycle observation; fail closed without scheduling work."""


Action = Literal["hold", "wait", "observe", "recovery_required", "launch_request"]


@dataclass(frozen=True)
class LifecyclePlan:
    action: Action
    reason: str
    retry_at: float | None
    attempt_id: int | None
    request_owner: str | None
    request_epoch: int | None
    decision_sha256: str


def _finite(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LifecycleError(code)
    try:
        finite = math.isfinite(value)
    except OverflowError:
        raise LifecycleError(code) from None
    if not finite:
        raise LifecycleError(code)
    return float(value)


def _decision_hash(body: dict) -> str:
    try:
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise LifecycleError("invalid_lifecycle_input") from None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def plan_lifecycle(
    *,
    desired_state: str,
    stop_reasons: Any,
    runtime_ready: Any,
    accounting_ready: Any,
    quota_ready: Any,
    process_state: str,
    process_owner: str | None,
    lease_owner: str | None,
    active_attempt_id: Any,
    recovery_open: Any,
    backoff_until: Any,
    now: Any,
    launch_authorized: Any,
    request_owner: Any = None,
    request_epoch: Any = None,
) -> LifecyclePlan:
    """Return a deterministic plan without changing state, starting a process, or waking a service.

    `launch_request` is an inert proposal for a separately qualified lifecycle owner to consume.
    It is never process execution authority by itself.
    """
    if type(desired_state) is not str or desired_state not in {"enabled", "stopped"}:
        raise LifecycleError("invalid_desired_state")
    if type(stop_reasons) not in (list, tuple) or any(
        type(reason) is not str or not reason.strip() for reason in stop_reasons
    ):
        raise LifecycleError("invalid_stop_reasons")
    for name, value in (
        ("runtime_ready", runtime_ready),
        ("accounting_ready", accounting_ready),
        ("quota_ready", quota_ready),
        ("recovery_open", recovery_open),
        ("launch_authorized", launch_authorized),
    ):
        if type(value) is not bool:
            raise LifecycleError(f"invalid_{name}")
    if type(process_state) is not str or process_state not in {"alive", "stopped", "unknown"}:
        raise LifecycleError("invalid_process_state")
    for name, value in (("process_owner", process_owner), ("lease_owner", lease_owner)):
        if value is not None and (type(value) is not str or not value.strip()):
            raise LifecycleError(f"invalid_{name}")
    if request_owner is not None and (type(request_owner) is not str or not request_owner.strip()):
        raise LifecycleError("invalid_request_owner")
    if request_epoch is not None and (
        isinstance(request_epoch, bool) or type(request_epoch) is not int or request_epoch <= 0
    ):
        raise LifecycleError("invalid_request_epoch")
    if active_attempt_id is not None and (
        isinstance(active_attempt_id, bool)
        or type(active_attempt_id) is not int
        or active_attempt_id <= 0
    ):
        raise LifecycleError("invalid_active_attempt_id")
    now_value = _finite(now, "invalid_lifecycle_time")
    retry_at = None if backoff_until is None else _finite(backoff_until, "invalid_backoff_until")

    body = {
        "desired_state": desired_state,
        "stop_reasons": list(stop_reasons),
        "runtime_ready": runtime_ready,
        "accounting_ready": accounting_ready,
        "quota_ready": quota_ready,
        "process_state": process_state,
        "process_owner": process_owner,
        "lease_owner": lease_owner,
        "active_attempt_id": active_attempt_id,
        "recovery_open": recovery_open,
        "backoff_until": retry_at,
        "now": now_value,
        "launch_authorized": launch_authorized,
        "request_owner": request_owner,
        "request_epoch": request_epoch,
    }

    def plan(action: Action, reason: str, at: float | None = None) -> LifecyclePlan:
        result = {"action": action, "reason": reason, "retry_at": at}
        return LifecyclePlan(
            action,
            reason,
            at,
            active_attempt_id,
            request_owner if action == "launch_request" else None,
            request_epoch if action == "launch_request" else None,
            _decision_hash({"input": body, "plan": result}),
        )

    if desired_state == "stopped":
        return plan("hold", "desired_stopped")
    if stop_reasons:
        return plan("hold", "stop_reason_active")
    if not runtime_ready:
        return plan("hold", "runtime_not_qualified")
    if not accounting_ready:
        return plan("hold", "usage_settlement_unqualified")
    if not quota_ready:
        return plan("hold", "quota_not_ready")
    if process_state == "unknown":
        return plan("hold", "process_observation_unknown")
    if process_state == "alive":
        if active_attempt_id is None or lease_owner is None:
            return plan("hold", "live_process_without_lease")
        if process_owner != lease_owner:
            return plan("hold", "process_lease_owner_mismatch")
        return plan("observe", "active_lease_owner_confirmed")
    if active_attempt_id is not None:
        return plan("hold", "recovery_obligation_open" if recovery_open else "recovery_required")
    # Owner strings are correlation hints only; this planner has no durable fencing proof.
    if process_owner is not None or lease_owner is not None:
        return plan("hold", "owner_without_active_attempt")
    if recovery_open:
        return plan("hold", "recovery_obligation_open")
    if retry_at is not None and now_value < retry_at:
        return plan("wait", "backoff", retry_at)
    if not launch_authorized:
        return plan("hold", "launch_not_authorized")
    if request_owner is None or request_epoch is None:
        return plan("hold", "launch_request_identity_missing")
    return plan("launch_request", "all_gates_clear")
