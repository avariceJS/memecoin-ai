"""Descriptor-only persistence primitives for the scout-chain v2 capture root.

This module deliberately has no provider transport or owner-host entry point.  It
provides the create-only transaction edges used by a separately reviewed host.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json
from tools.brain_senses_gecko_capture_only_v3_verify import DISPATCH_SCHEMA


class CaptureWriteError(ValueError):
    """A descriptor-bound capture write or immutable replay failed."""


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _record(raw: bytes, *, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureWriteError(reason) from exc
    if type(value) is not dict or canonical_json(value) != raw:
        raise CaptureWriteError(reason)
    return value


class DescriptorCaptureWriterV2:
    """Writes immutable request artifacts below one already-held service root."""

    def __init__(
        self,
        writer: DescriptorWriter,
        *,
        root: str,
        coordinator: Any,
        expected_storage: Mapping[str, Any] | None = None,
        revalidate_storage: Any = None,
    ) -> None:
        self.writer = writer
        self.root = root
        self.coordinator = coordinator
        coordinator_writer = getattr(coordinator, "writer", None)
        if coordinator_writer is None:
            raise CaptureWriteError("capture_coordinator_writer_missing")
        one, two = os.fstat(writer.root_fd), os.fstat(coordinator_writer.root_fd)
        if (one.st_dev, one.st_ino) != (two.st_dev, two.st_ino):
            raise CaptureWriteError("capture_coordinator_descriptor_mismatch")
        self.expected_storage = dict(expected_storage or {})
        self.revalidate_storage = revalidate_storage
        actual_identity = {"device": one.st_dev, "inode": one.st_ino}
        if self.expected_storage and any(
            self.expected_storage.get(key) != value for key, value in actual_identity.items()
        ):
            raise CaptureWriteError("capture_storage_identity_mismatch")
        parts = root.split("/")
        if (
            len(parts) != 4
            or parts[:3] != ["services", "gecko-market-v1", "capture-only-v2"]
            or len(parts[3]) != 32
            or any(ch not in "0123456789abcdef" for ch in parts[3])
        ):
            raise CaptureWriteError("capture_service_root_invalid")

    def _revalidate(self) -> None:
        st = os.fstat(self.writer.root_fd)
        if self.expected_storage and (
            st.st_dev != self.expected_storage.get("device")
            or st.st_ino != self.expected_storage.get("inode")
        ):
            raise CaptureWriteError("capture_storage_identity_changed")
        if self.revalidate_storage is not None:
            self.revalidate_storage()

    def path(self, suffix: str) -> str:
        if suffix.startswith("/") or ".." in suffix.split("/"):
            raise CaptureWriteError("capture_relative_path_invalid")
        return f"{self.root}/{suffix}"

    @contextmanager
    def locked(self) -> Iterator[Any]:
        """Use the globally agreed lock order: coordinator lane, then service root."""
        with self.coordinator.lane_lock("gecko", blocking=False) as lane_lease:
            with self.writer.lock(self.path(".runner.lock"), blocking=False):
                yield lane_lease

    def read_json(self, suffix: str, *, max_bytes: int = 4 * 1024 * 1024) -> dict[str, Any]:
        return _record(
            self.writer.read_bytes(self.path(suffix), max_bytes=max_bytes),
            reason="capture_record_invalid",
        )

    def read_dispatches(self) -> list[dict[str, Any]]:
        relative = self.path("dispatch.jsonl")
        if not self.writer.exists(relative):
            return []
        raw = self.writer.read_bytes(relative, max_bytes=16 * 1024 * 1024)
        if not raw.endswith(b"\n"):
            raise CaptureWriteError("capture_dispatch_truncated")
        rows: list[dict[str, Any]] = []
        for line in raw.splitlines(keepends=True):
            rows.append(_record(line, reason="capture_dispatch_invalid"))
        return rows

    def create_intent_and_reserve(
        self,
        *,
        intent: Mapping[str, Any],
        reserve_bytes: int,
        lane_lease: Any,
    ) -> Any:
        request_id = intent.get("request_id")
        if type(request_id) is not str or len(request_id) != 64:
            raise CaptureWriteError("capture_request_id_invalid")
        payload = canonical_json(dict(intent))
        digest = sha256(payload)
        self._revalidate()
        reservation = self.coordinator.reserve_and_persist_intent(
            service="gecko",
            reservation_id=request_id,
            request_id=request_id,
            intent_path=self.path(f"intents/{request_id}.json"),
            outcome_path=self.path(f"results/{request_id}.json"),
            intent_bytes=payload,
            intent_sha256=digest,
            reserve_bytes=reserve_bytes,
            lane_lease=lane_lease,
        )
        self._revalidate()
        return reservation

    def create_terminal_and_settle(
        self,
        *,
        reservation: Any,
        terminal: Mapping[str, Any],
        ambiguous: bool,
        lane_lease: Any,
    ) -> tuple[str, dict[str, Any]]:
        return self.settle_terminal(
            reservation=reservation,
            terminal=terminal,
            ambiguous=ambiguous,
            lane_lease=lane_lease,
        )

    def append_dispatch(
        self,
        *,
        intent: Mapping[str, Any],
        dispatch_ns: int,
        dispatch_monotonic_ns: int,
    ) -> dict[str, Any]:
        rows = self.read_dispatches()
        previous = rows[-1]["record_sha256"] if rows else None
        core = {
            "schema": DISPATCH_SCHEMA,
            "sequence": len(rows),
            "request_id": intent["request_id"],
            "intent_sha256": intent["intent_sha256"],
            "dispatch_ns": dispatch_ns,
            "dispatch_monotonic_ns": dispatch_monotonic_ns,
            "previous_sha256": previous,
        }
        row = {**core, "record_sha256": sha256(canonical_json(core))}
        self._revalidate()
        self.writer.append(self.path("dispatch.jsonl"), canonical_json(row))
        self._revalidate()
        return row

    def settle_terminal(
        self,
        *,
        reservation: Any,
        terminal: Mapping[str, Any],
        ambiguous: bool,
        lane_lease: Any,
    ) -> tuple[str, dict[str, Any]]:
        payload = canonical_json(dict(terminal))
        self._revalidate()
        kind = self.coordinator.settle(
            reservation,
            outcome=str(terminal["status"]),
            outcome_bytes=payload,
            outcome_path=reservation.outcome_path,
            ambiguous=ambiguous,
            lane_lease=lane_lease,
        )
        self._revalidate()
        _, events = self.coordinator._load_ledger()
        matches = [
            row
            for row in events
            if row.get("kind") in {"settled", "ambiguous_terminal"}
            and row.get("fields", {}).get("reservation_id") == reservation.reservation_id
        ]
        if len(matches) != 1 or matches[0].get("kind") != kind:
            raise CaptureWriteError("capture_coordinator_terminal_unverified")
        return kind, dict(matches[0])

    def last_coordinator_terminal(self, reservation_id: str) -> dict[str, Any]:
        _, events = self.coordinator._load_ledger()
        matches = [
            row
            for row in events
            if row.get("kind") in {"settled", "ambiguous_terminal"}
            and row.get("fields", {}).get("reservation_id") == reservation_id
        ]
        if len(matches) != 1:
            raise CaptureWriteError("capture_coordinator_terminal_unverified")
        return dict(matches[0])

    def create_confirmation(
        self,
        *,
        request_id: str,
        terminal: Mapping[str, Any],
        availability_ns: int,
        availability_monotonic_ns: int,
        availability_clock: Mapping[str, Any],
        coordinator_event: Mapping[str, Any],
    ) -> dict[str, Any]:
        from tools.brain_senses_gecko_capture_only_v3_verify import CONFIRMATION_SCHEMA

        clock = dict(availability_clock)
        core = {
            "schema": CONFIRMATION_SCHEMA,
            "schema_version": 1,
            "request_id": request_id,
            "outcome_sha256": sha256(canonical_json(dict(terminal))),
            "availability_ns": availability_ns,
            "availability_monotonic_ns": availability_monotonic_ns,
            "availability_clock": clock,
            "availability_clock_sha256": sha256(canonical_json(clock)),
            "coordinator_event_sha256": coordinator_event["event_sha256"],
        }
        row = {**core, "confirmation_sha256": sha256(canonical_json(core))}
        self._revalidate()
        self.writer.create(self.path(f"confirmations/{request_id}.json"), canonical_json(row))
        self._revalidate()
        return row


__all__ = ["CaptureWriteError", "DescriptorCaptureWriterV2", "sha256"]
