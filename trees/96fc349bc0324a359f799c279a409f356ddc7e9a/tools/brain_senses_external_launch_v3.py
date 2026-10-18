#!/usr/bin/env python3
"""Inactive launch-v3 callback prototype.

This module defines an injected, single-dispatch boundary only. It has no CLI,
provider client, activation gate, or production plan loader. A future v3 caller
must supply its independently verified immutable plan binding, lock factories,
and journal snapshot verifier.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

_SERVICE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_BUCKET = re.compile(r"[0-9]{8}T[0-9]{4}Z\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

Phase = Literal[
    "activation",
    "predecessor",
    "service-verify",
    "window",
    "pre-dispatch",
    "post-intent",
    "unknown",
]
Reason = Literal[
    "activation_failed",
    "predecessor_verification_failed",
    "service_verification_failed",
    "window_missed",
    "pre_dispatch_failed",
    "post_intent_failed",
    "clock_preflight_failed",
    "storage_guard_failed",
    "launch_gate_failed",
    "callback_failed",
    "unclassified",
]


class LaunchV3Error(RuntimeError):
    """Sanitized control error; never serialize its text."""


@dataclass(frozen=True)
class PlanBinding:
    """Immutable plan identity/window returned by a separate v3 verifier."""

    service_id: str
    plan_sha256: str
    scheduled_bucket: str
    window_start_ns: int
    window_end_ns: int
    source_sha256: str
    predecessor_certificate_sha256: str

    def validate(self, exact_plan_bytes: bytes, callback_start_utc_ns: int) -> None:
        if not isinstance(self.service_id, str) or not _SERVICE_ID.fullmatch(self.service_id):
            raise LaunchV3Error("invalid_plan_service")
        if not isinstance(self.plan_sha256, str) or not _SHA256.fullmatch(self.plan_sha256):
            raise LaunchV3Error("invalid_plan_digest")
        if hashlib.sha256(exact_plan_bytes).hexdigest() != self.plan_sha256:
            raise LaunchV3Error("plan_bytes_changed")
        if not isinstance(self.scheduled_bucket, str) or not _BUCKET.fullmatch(
            self.scheduled_bucket
        ):
            raise LaunchV3Error("invalid_plan_bucket")
        if (
            type(self.window_start_ns) is not int
            or type(self.window_end_ns) is not int
            or self.window_start_ns <= 0
            or self.window_end_ns <= self.window_start_ns
            or self.window_start_ns % 60_000_000_000 != 0
        ):
            raise LaunchV3Error("invalid_plan_window")
        expected_bucket = datetime.fromtimestamp(
            self.window_start_ns // 1_000_000_000, UTC
        ).strftime("%Y%m%dT%H%MZ")
        if self.scheduled_bucket != expected_bucket:
            raise LaunchV3Error("plan_bucket_window_mismatch")
        if type(callback_start_utc_ns) is not int or not (
            self.window_start_ns <= callback_start_utc_ns < self.window_end_ns
        ):
            raise LaunchV3Error("callback_outside_plan_window")
        if (
            not isinstance(self.source_sha256, str)
            or not _SHA256.fullmatch(self.source_sha256)
            or not isinstance(self.predecessor_certificate_sha256, str)
            or not _SHA256.fullmatch(self.predecessor_certificate_sha256)
        ):
            raise LaunchV3Error("invalid_plan_lineage_pins")


@dataclass(frozen=True)
class JournalSnapshot:
    """Values returned by one independent, source-pinned v3 verifier read."""

    verified: bool
    service_id: str
    plan_sha256: str
    source_sha256: str
    predecessor_certificate_sha256: str
    bucket: str
    event_tail_sha256: str
    reservation_ids: tuple[str, ...]
    intent_ids: tuple[str, ...]

    def validate(self, binding: PlanBinding) -> None:
        if self.verified is not True:
            raise LaunchV3Error("journal_snapshot_unverified")
        if (
            self.service_id != binding.service_id
            or self.plan_sha256 != binding.plan_sha256
            or self.bucket != binding.scheduled_bucket
            or self.source_sha256 != binding.source_sha256
            or self.predecessor_certificate_sha256 != binding.predecessor_certificate_sha256
            or not _SHA256.fullmatch(self.event_tail_sha256)
            or not isinstance(self.reservation_ids, tuple)
            or not all(isinstance(value, str) and value for value in self.reservation_ids)
            or len(set(self.reservation_ids)) != len(self.reservation_ids)
            or not isinstance(self.intent_ids, tuple)
            or not all(isinstance(value, str) and value for value in self.intent_ids)
            or len(set(self.intent_ids)) != len(self.intent_ids)
        ):
            raise LaunchV3Error("journal_snapshot_binding")


@dataclass(frozen=True)
class V3JournalProof:
    """Duck-typed proof payload consumed by the separate v1 receipt writer."""

    bucket: str
    reservation_ids_before: tuple[str, ...]
    reservation_ids_after: tuple[str, ...]
    intent_ids_before: tuple[str, ...]
    intent_ids_after: tuple[str, ...]
    event_tail_sha256: str
    fresh_independent_reads: bool
    required_locks_held: bool


@dataclass(frozen=True)
class OrderedLocks:
    """Factories acquired in the fixed host→guard→predecessor→service order."""

    host: Callable[[], Any]
    storage_guard: Callable[[], Any]
    predecessor_shared: Callable[[], Any]
    source_service: Callable[[], Any]

    @contextlib.contextmanager
    def acquire(self) -> Iterator[None]:
        with self.host():
            with self.storage_guard():
                with self.predecessor_shared():
                    with self.source_service():
                        yield


def reread_journal_proof(
    binding: PlanBinding,
    *,
    locks: OrderedLocks,
    snapshot_reader: Callable[[PlanBinding], JournalSnapshot],
) -> V3JournalProof:
    """Return proof only when two locked independent reads agree exactly.

    ``snapshot_reader`` must invoke the independent v3 verifier over fresh
    descriptor-bound snapshots. The helper enforces the lock order and equality
    checks; it does not substitute for that verifier.
    """
    with locks.acquire():
        first = snapshot_reader(binding)
        first.validate(binding)
        second = snapshot_reader(binding)
        second.validate(binding)
    if (
        first.service_id != second.service_id
        or first.plan_sha256 != second.plan_sha256
        or first.source_sha256 != second.source_sha256
        or first.predecessor_certificate_sha256 != second.predecessor_certificate_sha256
        or first.bucket != second.bucket
        or first.event_tail_sha256 != second.event_tail_sha256
        or first.reservation_ids != second.reservation_ids
        or first.intent_ids != second.intent_ids
    ):
        raise LaunchV3Error("journal_snapshot_changed")
    return V3JournalProof(
        bucket=binding.scheduled_bucket,
        reservation_ids_before=first.reservation_ids,
        reservation_ids_after=second.reservation_ids,
        intent_ids_before=first.intent_ids,
        intent_ids_after=second.intent_ids,
        event_tail_sha256=first.event_tail_sha256,
        fresh_independent_reads=True,
        required_locks_held=True,
    )


@dataclass(frozen=True)
class TypedFailure:
    phase: Phase
    reason: Reason


@dataclass(frozen=True)
class CallbackTimes:
    utc_ns: int
    monotonic_ns: int


@dataclass(frozen=True)
class CallbackResult:
    value: Any
    callback_start: CallbackTimes
    dispatched: bool


def callback_start() -> CallbackTimes:
    return CallbackTimes(utc_ns=time.time_ns(), monotonic_ns=time.monotonic_ns())


def descriptor_failure_sink(
    *,
    config_path: Any,
    host_lock: Callable[[], Any],
    guard_factory: Callable[..., Any],
) -> Callable[..., Any]:
    """Bind the sanitized sibling writer to explicit v3 host/storage locks.

    The import is local so loading this inactive prototype does not import the
    frozen v2 launcher. This sink is called only after ``dispatch_once`` exits
    its dispatch lock stack; the receipt writer then reacquires host→guard.
    """

    from tools.brain_senses_failure_receipt_v1 import best_effort_record_failure

    def write(**fields: Any) -> bool:
        return best_effort_record_failure(
            config_path=config_path,
            **fields,
            host_lock=host_lock,
            guard_factory=guard_factory,
        )

    return write


def dispatch_once(
    exact_plan_bytes: bytes,
    *,
    plan_validator: Callable[[bytes, CallbackTimes], PlanBinding],
    locks: OrderedLocks,
    dispatch: Callable[[PlanBinding], Any],
    proof_locks: OrderedLocks,
    snapshot_reader: Callable[[PlanBinding], JournalSnapshot],
    failure_sink: Callable[..., Any],
) -> CallbackResult:
    """Run one injected dispatch, then write diagnostics only after lock unwind.

    The callback contains no retry loop. The injected plan validator is the
    only source for the scheduled bucket. Failure proof and sink errors never
    replace the original dispatch exception.
    """
    started = callback_start()
    binding = plan_validator(exact_plan_bytes, started)
    if not isinstance(binding, PlanBinding):
        raise LaunchV3Error("plan_validator_contract")
    binding.validate(exact_plan_bytes, started.utc_ns)
    typed_failure: TypedFailure | None = None
    try:
        with locks.acquire():
            value = dispatch(binding)
        return CallbackResult(value=value, callback_start=started, dispatched=True)
    except Exception as error:
        if isinstance(error, V3DispatchFailure):
            typed_failure = error.failure
        phase: Phase = typed_failure.phase if typed_failure else "unknown"
        reason: Reason = typed_failure.reason if typed_failure else "unclassified"
        try:
            proof = reread_journal_proof(
                binding,
                locks=proof_locks,
                snapshot_reader=snapshot_reader,
            )
        except Exception:
            proof = None
        try:
            failure_sink(
                service_id=binding.service_id,
                plan_sha256=binding.plan_sha256,
                scheduled_bucket=binding.scheduled_bucket,
                callback_start_utc_ns=started.utc_ns,
                callback_start_monotonic_ns=started.monotonic_ns,
                phase=phase,
                exception=error,
                reason_code=reason,
                journal_proof=proof,
            )
        except Exception:
            pass
        raise


class V3DispatchFailure(RuntimeError):
    """Dispatch exception carrying only allowlisted boundary labels."""

    def __init__(self, failure: TypedFailure) -> None:
        self.failure = failure
        super().__init__("launch_v3_dispatch_failed")
