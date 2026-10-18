#!/usr/bin/env python3
"""Read-only, descriptor-bound service journal verifier for launch-v3 diagnostics.

The verifier delegates the journal's event, reservation, intent, file-closure,
and predecessor semantics to the independent finite-v2 verifier. It derives a
v3 snapshot only after checking exact plan lineage and the requested bucket.
It has no provider, retry, repair, or activation path.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Protocol

from tools import brain_senses_external_launch_v3 as launch_v3
from tools import brain_senses_external_orchestrator_v2 as engine_v2
from tools import brain_senses_external_verify_v2 as verify_v2


class Reader(Protocol):
    def read_bytes(self, relative: str, *, limit: int = 64_000_000) -> bytes: ...

    def list_files(self) -> list[str]: ...


class JournalSnapshotError(ValueError):
    """The exact source service journal cannot prove this callback snapshot."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise JournalSnapshotError(code)


def read_snapshot(
    service_reader: Reader,
    predecessor_reader: Reader,
    binding: launch_v3.PlanBinding,
) -> launch_v3.JournalSnapshot:
    """Verify one fresh service/predecessor read and derive its bucket state.

    The caller must hold the agreed host, storage, predecessor-shared, and
    service locks. Each invocation rereads all files through descriptor-bound
    readers; launch-v3 performs two invocations and compares their full outputs.
    """
    try:
        state = verify_v2.verify(
            service_reader,
            expected_plan_sha256=binding.plan_sha256,
            expected_sources=engine_v2.source_bindings(),
            predecessor_reader=predecessor_reader,
        )
    except verify_v2.VerificationError as exc:
        raise JournalSnapshotError("service_journal_unverified") from exc
    plan = state.get("plan")
    if not isinstance(plan, dict):
        raise JournalSnapshotError("verified_plan_missing")
    _require(plan.get("service_id") == binding.service_id, "service_identity_mismatch")
    storage = plan.get("storage")
    if not isinstance(storage, dict):
        raise JournalSnapshotError("service_storage_missing")
    _require(
        storage.get("relative_root") == f"services/{binding.service_id}",
        "service_storage_path_mismatch",
    )
    _require(
        verify_v2.sha(verify_v2.canonical(plan.get("code_sha256"))) == binding.source_sha256,
        "source_pin_mismatch",
    )
    predecessor = plan.get("predecessor")
    if not isinstance(predecessor, dict):
        raise JournalSnapshotError("predecessor_missing")
    _require(
        predecessor.get("certificate_sha256") == binding.predecessor_certificate_sha256,
        "predecessor_certificate_mismatch",
    )
    schedule = plan.get("schedule")
    if not isinstance(schedule, dict):
        raise JournalSnapshotError("schedule_missing")
    windows = [schedule["bootstrap"], *schedule["frames"]]
    matches = [row for row in windows if row.get("bucket") == binding.scheduled_bucket]
    _require(len(matches) == 1, "scheduled_bucket_missing_or_ambiguous")
    window = matches[0]
    _require(
        (window.get("start_ns"), window.get("end_ns"))
        == (binding.window_start_ns, binding.window_end_ns),
        "scheduled_window_mismatch",
    )

    target = binding.scheduled_bucket
    reservation_ids = tuple(
        event["payload"]["reservation_sha256"]
        for event in state["events"]
        if event["kind"] == "reservation" and event["bucket"] == target
    )
    intent_ids = tuple(
        f"{event['payload']['ordinal']}:{event['payload']['request_sha256']}"
        for event in state["events"]
        if event["kind"] == "request_intent" and event["bucket"] == target
    )
    _require(len(set(reservation_ids)) == len(reservation_ids), "duplicate_reservation_identity")
    _require(len(set(intent_ids)) == len(intent_ids), "duplicate_intent_identity")

    stopped = state.get("stop") is True or state.get("status") == "stopped"
    _require(not stopped, "service_stopped")
    schedule_index = windows.index(window)
    prior = windows[:schedule_index]
    _require(
        all(
            state.get("sealed", {}).get(row["bucket"]) is True
            or row["bucket"] in state.get("missed", set())
            for row in prior
        ),
        "prior_window_prefix_incomplete",
    )
    _require(target not in state.get("missed", set()), "target_bucket_already_missed")
    unresolved = state.get("unresolved_buckets", [])
    if reservation_ids:
        _require(
            state.get("status") == "interrupted_no_replay" and unresolved == [target],
            "reserved_prefix_not_isolated",
        )
    else:
        _require(
            state.get("status") == "verified_prefix" and not unresolved,
            "service_prefix_not_clean",
        )
    return launch_v3.JournalSnapshot(
        verified=True,
        service_id=binding.service_id,
        plan_sha256=binding.plan_sha256,
        source_sha256=binding.source_sha256,
        predecessor_certificate_sha256=binding.predecessor_certificate_sha256,
        bucket=target,
        event_tail_sha256=state["event_tail_sha256"],
        reservation_ids=reservation_ids,
        intent_ids=intent_ids,
    )


class DescriptorSnapshotReader:
    """Callable v3 reader that reopens two caller-owned descriptor roots each read.

    Factories must return fresh root file descriptors from storage's held mount
    descriptor. Paths are deliberately absent from this interface.
    """

    def __init__(
        self,
        service_root_fd: Callable[[], int],
        predecessor_root_fd: Callable[[], int],
    ) -> None:
        self._service_root_fd = service_root_fd
        self._predecessor_root_fd = predecessor_root_fd

    def __call__(self, binding: launch_v3.PlanBinding) -> launch_v3.JournalSnapshot:
        from tools.brain_senses_external_verify_v2 import DescriptorReaderV2

        service_fd: int | None = None
        predecessor_fd: int | None = None
        try:
            service_fd = self._service_root_fd()
            predecessor_fd = self._predecessor_root_fd()
            if (
                type(service_fd) is not int
                or type(predecessor_fd) is not int
                or service_fd == predecessor_fd
            ):
                raise JournalSnapshotError("descriptor_factory_contract")
            with DescriptorReaderV2(service_fd) as service_reader:
                with DescriptorReaderV2(predecessor_fd) as predecessor_reader:
                    return read_snapshot(service_reader, predecessor_reader, binding)
        finally:
            if type(service_fd) is int:
                os.close(service_fd)
            if type(predecessor_fd) is int and predecessor_fd != service_fd:
                os.close(predecessor_fd)
