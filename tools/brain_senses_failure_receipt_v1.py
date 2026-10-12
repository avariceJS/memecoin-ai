#!/usr/bin/env python3
"""Best-effort descriptor-bound, sanitized launch failure receipt v1.

This module is for a future launch-v3 integration. It performs no provider I/O
and does not change or wrap the frozen external-v2 launch path.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from memecoin_research_storage import StorageError, writer_guard
from tools.brain_senses_external_writer_v1 import DescriptorWriter

SCHEMA = "brain-senses-external-launch-failure-receipt-v1"
SCHEMA_VERSION = 1
BASE_DIR = "diagnostics/external-launch-v1"
MAX_RECEIPT_BYTES = 4096
_SERVICE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_BUCKET = re.compile(r"[0-9]{8}T[0-9]{4}Z\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")

PHASES = frozenset(
    {
        "activation",
        "predecessor",
        "service-verify",
        "window",
        "pre-dispatch",
        "post-intent",
        "unknown",
    }
)
REASONS = frozenset(
    {
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
    }
)
EXCEPTION_CLASSES = frozenset(
    {
        "BlockingIOError",
        "ClockError",
        "FileExistsError",
        "FileNotFoundError",
        "LaunchGateError",
        "OSError",
        "PermissionError",
        "RuntimeError",
        "StorageError",
        "TimeoutError",
        "ValueError",
        "VerificationError",
    }
)
_SOURCE_PATHS = (
    "tools/brain_senses_failure_receipt_v1.py",
    "tools/brain_senses_external_writer_v1.py",
    "tools/brain_senses_external_launch_v2.py",
    "src/memecoin_research_storage/__init__.py",
)
_REPO = Path(__file__).resolve().parents[1]


class FailureReceiptError(RuntimeError):
    """A local receipt validation or descriptor-write failure."""


@dataclass(frozen=True)
class JournalProof:
    """Independent before/after journal facts for one scheduled bucket.

    Callers may construct this only after taking the relevant service lock and
    independently rereading/verifying the reservation and request-intent sets.
    The receipt writer additionally checks that both verified sets are unchanged.
    """

    bucket: str
    reservation_ids_before: tuple[str, ...]
    reservation_ids_after: tuple[str, ...]
    intent_ids_before: tuple[str, ...]
    intent_ids_after: tuple[str, ...]
    event_tail_sha256: str | None
    fresh_independent_reads: bool
    required_locks_held: bool


@contextlib.contextmanager
def _no_host_lock() -> Iterator[None]:
    yield


def canonical(value: Any) -> bytes:
    try:
        return (
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FailureReceiptError("receipt_not_canonical_json") from exc


def _source_digest() -> str:
    hashes = {
        relative: hashlib.sha256((_REPO / relative).read_bytes()).hexdigest()
        for relative in _SOURCE_PATHS
    }
    return hashlib.sha256(canonical(hashes)).hexdigest()


def _validate_service_id(service_id: str) -> None:
    if not isinstance(service_id, str) or not _SERVICE_ID.fullmatch(service_id):
        raise FailureReceiptError("invalid_service_id")


def _normalize_bucket(bucket: str | None) -> str | None:
    if bucket is None:
        return None
    if not isinstance(bucket, str) or not _BUCKET.fullmatch(bucket):
        raise FailureReceiptError("invalid_scheduled_bucket")
    return bucket


def _proof_state(
    bucket: str | None, proof: JournalProof | None
) -> tuple[Literal["verified_none", "possibly_spent", "unknown"], str | None]:
    if (
        proof is None
        or bucket is None
        or proof.bucket != bucket
        or proof.fresh_independent_reads is not True
        or proof.required_locks_held is not True
    ):
        return "unknown", None
    if not all(
        isinstance(items, tuple) and all(isinstance(value, str) and value for value in items)
        for items in (
            proof.reservation_ids_before,
            proof.reservation_ids_after,
            proof.intent_ids_before,
            proof.intent_ids_after,
        )
    ):
        return "unknown", None
    same_sets = (
        proof.reservation_ids_before == proof.reservation_ids_after
        and proof.intent_ids_before == proof.intent_ids_after
    )
    tail = proof.event_tail_sha256
    if tail is not None and (not isinstance(tail, str) or not _SHA256.fullmatch(tail)):
        return "unknown", None
    if not same_sets:
        return "possibly_spent", tail
    if not proof.reservation_ids_after and not proof.intent_ids_after:
        return "verified_none", tail
    return "possibly_spent", tail


def build_record(
    *,
    service_id: str,
    plan_sha256: str,
    scheduled_bucket: str | None,
    callback_start_utc_ns: int,
    callback_start_monotonic_ns: int,
    phase: str,
    exception: BaseException,
    reason_code: str,
    journal_proof: JournalProof | None = None,
    receipt_utc_ns: int | None = None,
    receipt_monotonic_ns: int | None = None,
) -> dict[str, Any]:
    """Build a fixed-schema record without serializing exception text."""
    _validate_service_id(service_id)
    if not isinstance(plan_sha256, str) or not _SHA256.fullmatch(plan_sha256):
        raise FailureReceiptError("invalid_plan_sha256")
    bucket = _normalize_bucket(scheduled_bucket)
    if type(callback_start_utc_ns) is not int or callback_start_utc_ns <= 0:
        raise FailureReceiptError("invalid_callback_start_utc")
    if type(callback_start_monotonic_ns) is not int or callback_start_monotonic_ns <= 0:
        raise FailureReceiptError("invalid_callback_start_monotonic")
    phase_value = phase if isinstance(phase, str) and phase in PHASES else "unknown"
    reason_value = (
        reason_code if isinstance(reason_code, str) and reason_code in REASONS else "unclassified"
    )
    exception_name = type(exception).__name__
    exception_value = exception_name if exception_name in EXCEPTION_CLASSES else "Other"
    receipt_utc = time.time_ns() if receipt_utc_ns is None else receipt_utc_ns
    receipt_monotonic = (
        time.monotonic_ns() if receipt_monotonic_ns is None else receipt_monotonic_ns
    )
    if (
        type(receipt_utc) is not int
        or receipt_utc < callback_start_utc_ns
        or type(receipt_monotonic) is not int
        or receipt_monotonic < callback_start_monotonic_ns
    ):
        raise FailureReceiptError("invalid_receipt_time_order")
    intent_state, event_tail = _proof_state(bucket, journal_proof)
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "service_id": service_id,
        "plan_sha256": plan_sha256,
        "scheduled_bucket": bucket,
        "callback_start_utc_ns": callback_start_utc_ns,
        "callback_start_monotonic_ns": callback_start_monotonic_ns,
        "receipt_utc_ns": receipt_utc,
        "receipt_monotonic_ns": receipt_monotonic,
        "phase": phase_value,
        "exception_class": exception_value,
        "reason_code": reason_value,
        "intent_state": intent_state,
        "verified_event_tail_sha256": event_tail,
        "diagnostic_source_sha256": _source_digest(),
    }
    validate_record(record)
    if len(canonical(record)) > MAX_RECEIPT_BYTES:
        raise FailureReceiptError("receipt_too_large")
    return record


def validate_record(record: Any) -> None:
    expected = {
        "schema",
        "schema_version",
        "service_id",
        "plan_sha256",
        "scheduled_bucket",
        "callback_start_utc_ns",
        "callback_start_monotonic_ns",
        "receipt_utc_ns",
        "receipt_monotonic_ns",
        "phase",
        "exception_class",
        "reason_code",
        "intent_state",
        "verified_event_tail_sha256",
        "diagnostic_source_sha256",
    }
    if not isinstance(record, dict) or set(record) != expected:
        raise FailureReceiptError("receipt_fields")
    if (
        record["schema"] != SCHEMA
        or record["schema_version"] != SCHEMA_VERSION
        or not isinstance(record["service_id"], str)
        or not _SERVICE_ID.fullmatch(record["service_id"])
        or not isinstance(record["plan_sha256"], str)
        or not _SHA256.fullmatch(record["plan_sha256"])
        or record["scheduled_bucket"] is not None
        and (
            not isinstance(record["scheduled_bucket"], str)
            or not _BUCKET.fullmatch(record["scheduled_bucket"])
        )
        or any(
            type(record[name]) is not int or record[name] <= 0
            for name in (
                "callback_start_utc_ns",
                "callback_start_monotonic_ns",
                "receipt_utc_ns",
                "receipt_monotonic_ns",
            )
        )
        or record["receipt_utc_ns"] < record["callback_start_utc_ns"]
        or record["receipt_monotonic_ns"] < record["callback_start_monotonic_ns"]
        or not isinstance(record["phase"], str)
        or record["phase"] not in PHASES
        or not isinstance(record["exception_class"], str)
        or record["exception_class"] not in EXCEPTION_CLASSES | {"Other"}
        or not isinstance(record["reason_code"], str)
        or record["reason_code"] not in REASONS
        or not isinstance(record["intent_state"], str)
        or record["intent_state"] not in {"verified_none", "possibly_spent", "unknown"}
        or record["verified_event_tail_sha256"] is not None
        and (
            not isinstance(record["verified_event_tail_sha256"], str)
            or not _SHA256.fullmatch(record["verified_event_tail_sha256"])
        )
        or not isinstance(record["diagnostic_source_sha256"], str)
        or not _SHA256.fullmatch(record["diagnostic_source_sha256"])
    ):
        raise FailureReceiptError("receipt_values")


def _data_root_fd(guard: Any) -> int:
    relative = guard.config.data_root
    if not isinstance(relative, str) or "\x00" in relative or "\\" in relative:
        raise StorageError("failure_receipt_data_root_invalid")
    components = relative.split("/")
    if not components or any(part in {"", ".", ".."} for part in components):
        raise StorageError("failure_receipt_data_root_invalid")
    current = os.dup(guard.mount_fd)
    device = os.fstat(current).st_dev
    try:
        for component in components:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            try:
                st = os.fstat(child)
                if (
                    not stat.S_ISDIR(st.st_mode)
                    or st.st_uid != os.getuid()
                    or stat.S_IMODE(st.st_mode) != 0o700
                    or st.st_dev != device
                ):
                    raise StorageError("failure_receipt_data_root_identity")
            except BaseException:
                os.close(child)
                raise
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def failure_relative_path(service_id: str, bucket: str | None) -> str:
    _validate_service_id(service_id)
    safe_bucket = _normalize_bucket(bucket) or "unknown"
    return f"{BASE_DIR}/{service_id}/failure-{safe_bucket}.json"


def persist_record_with_guard(
    guard: Any,
    record: Mapping[str, Any],
    *,
    writer_factory: Callable[..., Any] = DescriptorWriter,
) -> bool:
    """Create one canonical receipt beneath the held storage root.

    Returns true for a new or byte-identical existing receipt. It never replaces
    a conflicting file and never redirects to an internal path.
    """
    validate_record(dict(record))
    payload = canonical(dict(record))
    if len(payload) > MAX_RECEIPT_BYTES:
        raise FailureReceiptError("receipt_too_large")
    path = failure_relative_path(record["service_id"], record["scheduled_bucket"])
    directory = path.rsplit("/", 1)[0]
    guard.revalidate()
    root_fd = _data_root_fd(guard)
    try:
        root_stat = os.fstat(root_fd)
        mount_stat = os.fstat(guard.mount_fd)
        if root_stat.st_dev != mount_stat.st_dev:
            raise StorageError("failure_receipt_root_device_mismatch")
        with writer_factory(root_fd, before_write=guard.revalidate) as writer:
            writer.mkdir(directory)
            guard.revalidate()
            try:
                existing = writer.read_bytes(path, max_bytes=MAX_RECEIPT_BYTES)
            except FileNotFoundError:
                existing = None
            if existing is not None:
                guard.revalidate()
                return bool(existing == payload)
            try:
                writer.create(path, payload, mode=0o600)
            except StorageError:
                try:
                    return bool(writer.read_bytes(path, max_bytes=MAX_RECEIPT_BYTES) == payload)
                except Exception:
                    return False
            guard.revalidate()
            return bool(writer.read_bytes(path, max_bytes=MAX_RECEIPT_BYTES) == payload)
    finally:
        os.close(root_fd)


def best_effort_record_failure(
    *,
    config_path: Path,
    service_id: str,
    plan_sha256: str,
    scheduled_bucket: str | None,
    callback_start_utc_ns: int,
    callback_start_monotonic_ns: int,
    phase: str,
    exception: BaseException,
    reason_code: str,
    journal_proof: JournalProof | None = None,
    guard_factory: Callable[..., Any] = writer_guard,
    host_lock: Callable[[], Any] | None = None,
    writer_factory: Callable[..., Any] = DescriptorWriter,
) -> bool:
    """Attempt one write; all sink faults are suppressed to preserve the caller failure."""
    try:
        if host_lock is None:
            # Keep v3-only callers inert at import time. The frozen v2 lock is
            # retained solely as the compatibility default for existing callers.
            from tools.brain_senses_external_launch_v2 import host_lock as default_host_lock

            host_lock = default_host_lock
        record = build_record(
            service_id=service_id,
            plan_sha256=plan_sha256,
            scheduled_bucket=scheduled_bucket,
            callback_start_utc_ns=callback_start_utc_ns,
            callback_start_monotonic_ns=callback_start_monotonic_ns,
            phase=phase,
            exception=exception,
            reason_code=reason_code,
            journal_proof=journal_proof,
        )
        with host_lock(), guard_factory(config_path) as guard:
            guard.revalidate()
            return persist_record_with_guard(guard, record, writer_factory=writer_factory)
    except Exception:
        return False
