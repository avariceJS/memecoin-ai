"""Descriptor-bound journal writes for the separately versioned Gecko capture-only v2 lane.

This module provides no provider transport, LaunchAgent, activation, source qualification, training,
or economic-admission operation. Callers must hold the Gecko coordinator lane lease before taking
the v2 root lock and must retain both through terminal settlement and confirmation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

from memecoin_research_storage import _open_relative_directory
from tools import brain_senses_capacity_coordinator_v2 as capacity
from tools import brain_senses_gecko_capture_only_v2_scheduler as scheduler
from tools import brain_senses_gecko_capture_only_v2_verify as verifier
from tools import brain_senses_gecko_scout_chain_v1 as chain
from tools.brain_senses_external_clock_v1 import ClockError, validate_progress
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json

MAX_OUTCOME_ENVELOPE_BYTES = 64 * 1024
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_PREFIX_FILE_BYTES = 32 * 1024 * 1024


class CaptureWriterError(ValueError):
    """A v2 writer precondition or descriptor-bound journal operation failed."""


@dataclass
class RunnerLease:
    owner: object
    thread_id: int
    active: bool = True


class DescriptorCaptureWriter:
    """Create-only v2 attempt writer rooted at one verified storage descriptor."""

    def __init__(
        self,
        writer: DescriptorWriter,
        *,
        root: str,
        binding: Mapping[str, Any],
    ) -> None:
        if type(root) is not str or type(binding) is not dict:
            raise CaptureWriterError("v2_writer_binding_shape")
        if binding.get("root") != root or binding.get("schema") != f"{chain.SCHEMA}-binding":
            raise CaptureWriterError("v2_writer_root_binding")
        storage = binding.get("storage")
        root_stat = os.fstat(writer.root_fd)
        if (
            type(storage) is not dict
            or set(storage) != {"root_id", "volume_uuid", "device", "inode"}
            or storage.get("device") != root_stat.st_dev
            or storage.get("inode") != root_stat.st_ino
            or type(storage.get("root_id")) is not str
            or re.fullmatch(r"[0-9a-f]{32}", storage["root_id"]) is None
            or type(storage.get("volume_uuid")) is not str
            or re.fullmatch(r"[0-9a-f-]{36}", storage["volume_uuid"]) is None
        ):
            raise CaptureWriterError("v2_writer_storage_binding")
        self.writer = writer
        self.root = root
        self.binding = dict(binding)
        self._owner = object()

    def _path(self, relative: str) -> str:
        return f"{self.root}/{relative}"

    @contextmanager
    def runner_lock(
        self,
        *,
        coordinator: capacity.CapacityCoordinator,
        lane_lease: capacity.LaneLockLease,
        expected_prefix_sha256: str,
    ) -> Iterator[RunnerLease]:
        """Acquire v2 EX lock after lane lease and pin the exact census prefix bytes."""
        coordinator._validate_lane_lease("gecko", lane_lease)
        if _HASH_RE.fullmatch(expected_prefix_sha256) is None:
            raise CaptureWriterError("v2_writer_prefix_hash_shape")
        root_fd = _open_relative_directory(self.writer.root_fd, self.root)
        try:
            root_stat = os.fstat(root_fd)
            lock_fd = os.open(
                ".runner.lock",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=root_fd,
            )
        except BaseException:
            os.close(root_fd)
            raise
        os.close(root_fd)
        try:
            lock_stat = os.fstat(lock_fd)
            if (
                not stat.S_ISREG(lock_stat.st_mode)
                or lock_stat.st_dev != self.writer.device
                or lock_stat.st_dev != root_stat.st_dev
                or lock_stat.st_uid != os.getuid()
                or lock_stat.st_nlink != 1
                or stat.S_IMODE(lock_stat.st_mode) != 0o600
            ):
                raise CaptureWriterError("v2_writer_lock_unsafe")
            import fcntl

            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CaptureWriterError("v2_writer_root_busy") from exc
            lease = RunnerLease(self._owner, threading.get_ident())
            try:
                self._verify_descriptor_binding()
                actual_prefix = self.prefix_sha256()
                if actual_prefix != expected_prefix_sha256:
                    raise CaptureWriterError("v2_writer_prefix_changed_after_census")
                yield lease
            finally:
                lease.active = False
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    def _verify_descriptor_binding(self) -> None:
        storage = cast(dict[str, Any], self.binding["storage"])
        root_stat = os.fstat(self.writer.root_fd)
        if (root_stat.st_dev, root_stat.st_ino) != (storage["device"], storage["inode"]):
            raise CaptureWriterError("v2_writer_descriptor_detached")

    def _require_lease(self, lease: RunnerLease) -> None:
        if (
            not isinstance(lease, RunnerLease)
            or lease.owner is not self._owner
            or not lease.active
            or lease.thread_id != threading.get_ident()
        ):
            raise CaptureWriterError("v2_writer_lease_invalid")
        self._verify_descriptor_binding()

    def prefix_sha256(self) -> str:
        """Hash all current root files exactly as the shared-usage inventory does."""
        files: list[dict[str, Any]] = []

        def visit(relative: str) -> None:
            for name in self.writer.list_names(relative):
                path = f"{relative}/{name}"
                entry = self.writer.stat(path)
                if stat.S_ISDIR(entry.st_mode):
                    visit(path)
                else:
                    payload = self.writer.read_bytes(path, max_bytes=_MAX_PREFIX_FILE_BYTES)
                    files.append(
                        {
                            "path": path[len(self.root) + 1 :],
                            "size": len(payload),
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    )

        visit(self.root)
        return hashlib.sha256(canonical_json(files)).hexdigest()

    def initialize_attempt_directories(self, lease: RunnerLease) -> None:
        self._require_lease(lease)
        if "clocks" in self.writer.list_names(self.root):
            raise CaptureWriterError("v2_standalone_clock_directory_forbidden")
        for directory in ("intents", "results", "confirmations", "skips"):
            self.writer.mkdir(self._path(directory))

    def reserve_intent(
        self,
        lease: RunnerLease,
        *,
        coordinator: capacity.CapacityCoordinator,
        lane_lease: capacity.LaneLockLease,
        intent: Mapping[str, Any],
        outcome_path: str | None = None,
    ) -> capacity.Reservation:
        self._require_lease(lease)
        coordinator._validate_lane_lease("gecko", lane_lease)
        request_id = intent.get("request_id")
        _require_request_id(request_id)
        request_id = cast(str, request_id)
        payload = canonical_json(dict(intent))
        intent_core = dict(intent)
        claimed_intent_sha256 = intent_core.pop("intent_sha256", None)
        if (
            claimed_intent_sha256 != hashlib.sha256(canonical_json(intent_core)).hexdigest()
            or intent.get("schema") != verifier.INTENT_SCHEMA
        ):
            raise CaptureWriterError("v2_intent_hash_invalid")
        intent_path = self._path(f"intents/{request_id}.json")
        result_path = outcome_path or self._path(f"results/{request_id}.json")
        if result_path != self._path(f"results/{request_id}.json"):
            raise CaptureWriterError("v2_writer_outcome_path_mismatch")
        return coordinator.reserve_and_persist_intent(
            service="gecko",
            reservation_id=request_id,
            request_id=request_id,
            intent_path=intent_path,
            outcome_path=result_path,
            intent_bytes=payload,
            intent_sha256=hashlib.sha256(payload).hexdigest(),
            reserve_bytes=MAX_OUTCOME_ENVELOPE_BYTES,
            lane_lease=lane_lease,
            persist_intent=lambda target_writer, path, body: cast(
                DescriptorWriter, target_writer
            ).atomic_write(path, body, create_only=True),
        )

    def append_dispatch(
        self,
        lease: RunnerLease,
        *,
        request_id: str,
        intent_sha256: str,
        dispatch_ns: int,
    ) -> dict[str, Any]:
        self._require_lease(lease)
        _require_request_id(request_id)
        if _HASH_RE.fullmatch(intent_sha256) is None or type(dispatch_ns) is not int:
            raise CaptureWriterError("v2_dispatch_input_invalid")
        path = self._path("dispatch.jsonl")
        raw = (
            self.writer.read_bytes(path, max_bytes=16 * 1024 * 1024)
            if self.writer.exists(path)
            else b""
        )
        if raw and not raw.endswith(b"\n"):
            raise CaptureWriterError("v2_dispatch_journal_torn")
        previous: str | None = None
        sequence = 0
        seen_request_ids: set[str] = set()
        for line in raw.splitlines():
            row = json.loads(line)
            if type(row) is not dict or canonical_json(row).rstrip(b"\n") != line:
                raise CaptureWriterError("v2_dispatch_journal_invalid")
            if row.get("sequence") != sequence or row.get("previous_sha256") != previous:
                raise CaptureWriterError("v2_dispatch_chain_invalid")
            core = dict(row)
            claimed = core.pop("record_sha256", None)
            if claimed != hashlib.sha256(canonical_json(core)).hexdigest():
                raise CaptureWriterError("v2_dispatch_chain_invalid")
            if row.get("request_id") in seen_request_ids:
                raise CaptureWriterError("v2_dispatch_request_duplicate")
            seen_request_ids.add(cast(str, row.get("request_id")))
            previous = cast(str, claimed)
            sequence += 1
        intent_path = self._path(f"intents/{request_id}.json")
        intent_raw = self.writer.read_bytes(intent_path, max_bytes=8 * 1024 * 1024)
        try:
            intent_record = json.loads(intent_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CaptureWriterError("v2_dispatch_intent_mismatch") from exc
        if (
            type(intent_record) is not dict
            or canonical_json(intent_record) != intent_raw
            or intent_record.get("request_id") != request_id
            or intent_record.get("intent_sha256") != intent_sha256
            or request_id in seen_request_ids
        ):
            raise CaptureWriterError("v2_dispatch_intent_mismatch")
        core = {
            "schema": verifier.DISPATCH_SCHEMA,
            "sequence": sequence,
            "request_id": request_id,
            "intent_sha256": intent_sha256,
            "dispatch_ns": dispatch_ns,
            "previous_sha256": previous,
        }
        record = {**core, "record_sha256": hashlib.sha256(canonical_json(core)).hexdigest()}
        self.writer.append(path, canonical_json(record))
        return record

    def settle_outcome(
        self,
        lease: RunnerLease,
        *,
        coordinator: capacity.CapacityCoordinator,
        lane_lease: capacity.LaneLockLease,
        reservation: capacity.Reservation,
        outcome: Mapping[str, Any],
        ambiguous: bool,
    ) -> dict[str, Any]:
        self._require_lease(lease)
        coordinator._validate_lane_lease("gecko", lane_lease)
        payload = canonical_json(dict(outcome))
        if len(payload) > MAX_OUTCOME_ENVELOPE_BYTES:
            raise CaptureWriterError("v2_outcome_envelope_over_budget")
        if reservation.outcome_path != self._path(f"results/{reservation.request_id}.json"):
            raise CaptureWriterError("v2_writer_outcome_path_mismatch")
        kind = coordinator.settle(
            reservation,
            outcome=str(outcome.get("status", "unknown")),
            outcome_bytes=payload,
            outcome_path=reservation.outcome_path,
            ambiguous=ambiguous,
            persist_outcome=lambda target_writer, path, body: cast(
                DescriptorWriter, target_writer
            ).atomic_write(path, body, create_only=True),
            lane_lease=lane_lease,
        )
        with coordinator._shared_lock("gecko", lane_lease):
            _, events = coordinator._load_ledger()
        terminals = [
            event
            for event in events
            if event["kind"] in {"settled", "ambiguous_terminal"}
            and event["fields"].get("reservation_id") == reservation.reservation_id
        ]
        if not terminals or terminals[-1]["kind"] != kind:
            raise CaptureWriterError("v2_coordinator_terminal_missing")
        return terminals[-1]

    def write_confirmation(
        self,
        lease: RunnerLease,
        *,
        request_id: str,
        outcome_sha256: str,
        availability_ns: int,
        availability_monotonic_ns: int,
        availability_clock: Mapping[str, Any],
        coordinator_terminal: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._require_lease(lease)
        _require_request_id(request_id)
        if (
            _HASH_RE.fullmatch(outcome_sha256) is None
            or type(coordinator_terminal) is not dict
            or type(availability_ns) is not int
            or type(availability_monotonic_ns) is not int
            or type(availability_clock) is not dict
        ):
            raise CaptureWriterError("v2_confirmation_input_invalid")
        result_path = self._path(f"results/{request_id}.json")
        result_bytes = self.writer.read_bytes(result_path, max_bytes=MAX_OUTCOME_ENVELOPE_BYTES)
        if hashlib.sha256(result_bytes).hexdigest() != outcome_sha256:
            raise CaptureWriterError("v2_confirmation_outcome_mismatch")
        try:
            result = json.loads(result_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CaptureWriterError("v2_confirmation_outcome_invalid") from exc
        fields = coordinator_terminal.get("fields")
        if (
            canonical_json(result) != result_bytes
            or type(fields) is not dict
            or coordinator_terminal.get("kind") not in {"settled", "ambiguous_terminal"}
            or fields.get("outcome_sha256") != outcome_sha256
            or type(coordinator_terminal.get("at_ns")) is not int
            or coordinator_terminal["at_ns"] > availability_ns
            or type(coordinator_terminal.get("event_sha256")) is not str
            or _HASH_RE.fullmatch(coordinator_terminal["event_sha256"]) is None
            or result.get("request_id") != request_id
            or type(result.get("receipt_ns")) is not int
            or availability_ns < result["receipt_ns"]
        ):
            raise CaptureWriterError("v2_confirmation_coordinator_binding")
        try:
            validate_progress(
                dict(availability_clock),
                wall_ns=availability_ns,
                monotonic_ns=availability_monotonic_ns,
            )
        except ClockError as exc:
            raise CaptureWriterError("v2_confirmation_clock_invalid") from exc
        confirmation: dict[str, Any] = {
            "schema": verifier.CONFIRMATION_SCHEMA,
            "schema_version": 1,
            "request_id": request_id,
            "outcome_sha256": outcome_sha256,
            "availability_ns": availability_ns,
            "availability_monotonic_ns": availability_monotonic_ns,
            "availability_clock": dict(availability_clock),
            "availability_clock_sha256": availability_clock_sha256(availability_clock),
            "coordinator_event_sha256": coordinator_terminal["event_sha256"],
        }
        confirmation["confirmation_sha256"] = hashlib.sha256(
            canonical_json(confirmation)
        ).hexdigest()
        self.writer.atomic_write(
            self._path(f"confirmations/{request_id}.json"),
            canonical_json(confirmation),
            create_only=True,
        )
        return confirmation

    def append_missed_skip(
        self,
        lease: RunnerLease,
        *,
        plan: Mapping[str, Any],
        slot_index: int,
        at_ns: int,
    ) -> dict[str, Any]:
        """Record an expired fixed slot as a durable miss; never replace or retry it."""
        self._require_lease(lease)
        selection = plan.get("selection")
        pools = selection.get("selected_pool_ids") if isinstance(selection, Mapping) else None
        sequence = plan.get("sequence")
        close_ns = plan.get("bucket_close_ns")
        if (
            type(pools) is not list
            or type(sequence) is not int
            or type(close_ns) is not int
            or type(slot_index) is not int
            or not 0 <= slot_index < len(pools)
            or type(at_ns) is not int
            or at_ns < close_ns + verifier.REQUEST_START_END_NS
        ):
            raise CaptureWriterError("v2_skip_input_invalid")
        record = {
            "schema": scheduler.SKIP_SCHEMA,
            "plan_sequence": sequence,
            "slot_index": slot_index,
            "pool_id": pools[slot_index],
            "bucket_close_ns": close_ns,
            "at_ns": at_ns,
            "reason": "missed_dispatch",
        }
        name = f"{sequence}-{slot_index:02d}.json"
        self.writer.atomic_write(
            self._path(f"skips/{name}"), canonical_json(record), create_only=True
        )
        return record


def _require_request_id(value: Any) -> None:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        raise CaptureWriterError("v2_request_id_invalid")


def availability_clock_sha256(clock: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(clock))).hexdigest()


__all__ = ["CaptureWriterError", "DescriptorCaptureWriter", "RunnerLease"]
