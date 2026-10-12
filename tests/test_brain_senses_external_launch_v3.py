from __future__ import annotations

import contextlib
import hashlib
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tools import brain_senses_external_launch_v3 as launch_v3
from tools.brain_senses_failure_receipt_v1 import _proof_state, build_record

PLAN_BYTES = b"synthetic immutable launch-v3 plan\n"
PLAN_SHA = hashlib.sha256(PLAN_BYTES).hexdigest()
SERVICE = "synthetic-v3"
BUCKET = "20200101T0000Z"
WINDOW_START_NS = 1_577_836_800_000_000_000
WINDOW_END_NS = WINDOW_START_NS + 60_000_000_000
SOURCE_SHA = "a" * 64
PRED_SHA = "b" * 64
TAIL_SHA = "c" * 64


@pytest.fixture(autouse=True)
def _fixed_callback_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        launch_v3,
        "callback_start",
        lambda: launch_v3.CallbackTimes(WINDOW_START_NS + 1_000_000_000, 123_000),
    )


def _binding(_raw: bytes, _started: launch_v3.CallbackTimes) -> launch_v3.PlanBinding:
    return launch_v3.PlanBinding(
        SERVICE,
        PLAN_SHA,
        BUCKET,
        WINDOW_START_NS,
        WINDOW_END_NS,
        SOURCE_SHA,
        PRED_SHA,
    )


def _snapshot(
    binding: launch_v3.PlanBinding,
    *,
    reservations: tuple[str, ...] = (),
    intents: tuple[str, ...] = (),
    tail: str = TAIL_SHA,
    source_sha: str = SOURCE_SHA,
    predecessor_sha: str = PRED_SHA,
) -> launch_v3.JournalSnapshot:
    return launch_v3.JournalSnapshot(
        verified=True,
        service_id=binding.service_id,
        plan_sha256=binding.plan_sha256,
        source_sha256=source_sha,
        predecessor_certificate_sha256=predecessor_sha,
        bucket=binding.scheduled_bucket,
        event_tail_sha256=tail,
        reservation_ids=reservations,
        intent_ids=intents,
    )


def _locks(events: list[str], *, fail_at: str | None = None) -> launch_v3.OrderedLocks:
    def factory(name: str) -> Callable[[], Any]:
        @contextlib.contextmanager
        def acquire() -> Iterator[None]:
            events.append(name + ":enter")
            if fail_at == name:
                raise BlockingIOError("secret lock detail")
            try:
                yield
            finally:
                events.append(name + ":exit")

        return acquire

    return launch_v3.OrderedLocks(
        host=factory("host"),
        storage_guard=factory("guard"),
        predecessor_shared=factory("predecessor"),
        source_service=factory("service"),
    )


def _run(
    dispatch: Callable[[launch_v3.PlanBinding], Any],
    *,
    dispatch_locks: launch_v3.OrderedLocks | None = None,
    proof_locks: launch_v3.OrderedLocks | None = None,
    snapshot_reader: Callable[[launch_v3.PlanBinding], launch_v3.JournalSnapshot] | None = None,
    failure_sink: Callable[..., Any] | None = None,
) -> launch_v3.CallbackResult:
    return launch_v3.dispatch_once(
        PLAN_BYTES,
        plan_validator=_binding,
        locks=dispatch_locks or _locks([]),
        dispatch=dispatch,
        proof_locks=proof_locks or _locks([]),
        snapshot_reader=snapshot_reader or _snapshot,
        failure_sink=failure_sink or (lambda **_kwargs: None),
    )


def test_success_dispatches_exactly_once_and_never_writes_failure_receipt() -> None:
    calls = 0
    sink_calls = 0
    journal = {"files": ("external-plan-v3.json", "events-v3.jsonl"), "requests": 0}

    def dispatch(binding: launch_v3.PlanBinding) -> str:
        nonlocal calls
        calls += 1
        assert binding.scheduled_bucket == BUCKET
        return "synthetic-success"

    def sink(**_kwargs: Any) -> None:
        nonlocal sink_calls
        sink_calls += 1

    result = _run(dispatch, failure_sink=sink)
    assert result.value == "synthetic-success"
    assert result.dispatched is True
    assert calls == 1
    assert sink_calls == 0
    assert journal == {"files": ("external-plan-v3.json", "events-v3.jsonl"), "requests": 0}


def test_pre_intent_failure_rereads_under_ordered_locks_then_sinks_after_unwind() -> None:
    events: list[str] = []
    dispatch_calls = 0
    snapshot_calls = 0
    sink_calls: list[dict[str, Any]] = []

    def dispatch(_binding: launch_v3.PlanBinding) -> None:
        nonlocal dispatch_calls
        dispatch_calls += 1
        events.append("dispatch")
        raise launch_v3.V3DispatchFailure(
            launch_v3.TypedFailure("pre-dispatch", "pre_dispatch_failed")
        )

    def snapshot(binding: launch_v3.PlanBinding) -> launch_v3.JournalSnapshot:
        nonlocal snapshot_calls
        snapshot_calls += 1
        return _snapshot(binding)

    def sink(**kwargs: Any) -> None:
        events.append("receipt_sink")
        sink_calls.append(kwargs)

    with pytest.raises(launch_v3.V3DispatchFailure) as caught:
        _run(
            dispatch,
            dispatch_locks=_locks(events),
            proof_locks=_locks(events),
            snapshot_reader=snapshot,
            failure_sink=sink,
        )

    assert caught.value.failure == launch_v3.TypedFailure("pre-dispatch", "pre_dispatch_failed")
    assert dispatch_calls == 1
    assert snapshot_calls == 2
    assert _proof_state(BUCKET, sink_calls[0]["journal_proof"])[0] == "verified_none"
    assert sink_calls[0]["phase"] == "pre-dispatch"
    assert sink_calls[0]["reason_code"] == "pre_dispatch_failed"
    assert events.index("service:exit") < events.index("receipt_sink")
    assert events[-1] == "receipt_sink"
    assert events.count("host:enter") == 2
    assert events.count("guard:enter") == 2
    assert events.count("predecessor:enter") == 2
    assert events.count("service:enter") == 2


def test_descriptor_sink_reacquires_host_then_guard_after_dispatch_locks_unwind(
    tmp_path: Path,
) -> None:
    mount = tmp_path / "mount"
    data = mount / "BrainData"
    service = data / "services" / SERVICE
    service.mkdir(mode=0o700, parents=True)
    for directory in (mount, data, data / "services", service):
        os.chmod(directory, 0o700)
    (service / "external-plan-v3.json").write_bytes(PLAN_BYTES)
    os.chmod(service / "external-plan-v3.json", 0o600)
    events: list[str] = []

    @contextlib.contextmanager
    def host_lock() -> Iterator[None]:
        events.append("receipt_host:enter")
        try:
            yield
        finally:
            events.append("receipt_host:exit")

    class Guard:
        config = type("Config", (), {"data_root": "BrainData"})()

        def __init__(self) -> None:
            self.mount_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

        def revalidate(self) -> None:
            return None

        def close(self) -> None:
            os.close(self.mount_fd)

    @contextlib.contextmanager
    def guard_factory(_path: Path) -> Iterator[Guard]:
        events.append("receipt_guard:enter")
        guard = Guard()
        try:
            yield guard
        finally:
            guard.close()
            events.append("receipt_guard:exit")

    sink = launch_v3.descriptor_failure_sink(
        config_path=tmp_path / "storage.json",
        host_lock=host_lock,
        guard_factory=guard_factory,
    )

    def dispatch(_binding_arg: launch_v3.PlanBinding) -> None:
        events.append("dispatch")
        raise launch_v3.V3DispatchFailure(
            launch_v3.TypedFailure("pre-dispatch", "pre_dispatch_failed")
        )

    with pytest.raises(launch_v3.V3DispatchFailure):
        _run(
            dispatch,
            dispatch_locks=_locks(events),
            proof_locks=_locks(events),
            failure_sink=sink,
        )

    receipt = data / "diagnostics" / "external-launch-v1" / SERVICE / f"failure-{BUCKET}.json"
    assert receipt.exists()
    assert events.index("service:exit") < events.index("receipt_host:enter")
    assert events.index("receipt_host:enter") < events.index("receipt_guard:enter")
    assert events[-2:] == ["receipt_guard:exit", "receipt_host:exit"]
    assert (service / "external-plan-v3.json").read_bytes() == PLAN_BYTES
    assert not list((data / "services").rglob("requests/*"))


def test_post_intent_failure_is_possibly_spent_and_never_retried() -> None:
    dispatch_calls = 0
    received: list[dict[str, Any]] = []

    def dispatch(_binding: launch_v3.PlanBinding) -> None:
        nonlocal dispatch_calls
        dispatch_calls += 1
        raise launch_v3.V3DispatchFailure(
            launch_v3.TypedFailure("post-intent", "post_intent_failed")
        )

    def snapshot(binding: launch_v3.PlanBinding) -> launch_v3.JournalSnapshot:
        return _snapshot(binding, reservations=("r0",), intents=("i0",))

    with pytest.raises(launch_v3.V3DispatchFailure):
        _run(dispatch, snapshot_reader=snapshot, failure_sink=lambda **data: received.append(data))

    assert dispatch_calls == 1
    proof = received[0]["journal_proof"]
    assert _proof_state(BUCKET, proof)[0] == "possibly_spent"
    assert received[0]["phase"] == "post-intent"


def test_changed_or_unverified_snapshots_are_unknown_not_verified_none() -> None:
    outputs = [
        _snapshot(_binding(PLAN_BYTES, launch_v3.CallbackTimes(WINDOW_START_NS, 123_000))),
        _snapshot(
            _binding(PLAN_BYTES, launch_v3.CallbackTimes(WINDOW_START_NS, 123_000)),
            tail="d" * 64,
        ),
    ]

    def changed(_binding_arg: launch_v3.PlanBinding) -> launch_v3.JournalSnapshot:
        return outputs.pop(0)

    calls: list[dict[str, Any]] = []
    error = RuntimeError("secret key and endpoint must stay only in caller")
    with pytest.raises(RuntimeError) as caught:
        _run(
            lambda _binding_arg: (_ for _ in ()).throw(error),
            snapshot_reader=changed,
            failure_sink=lambda **data: calls.append(data),
        )
    assert caught.value is error
    assert calls[0]["journal_proof"] is None
    record = build_record(
        service_id=SERVICE,
        plan_sha256=PLAN_SHA,
        scheduled_bucket=BUCKET,
        callback_start_utc_ns=1_000_000_000,
        callback_start_monotonic_ns=1_000,
        phase=calls[0]["phase"],
        exception=error,
        reason_code=calls[0]["reason_code"],
        journal_proof=calls[0]["journal_proof"],
        receipt_utc_ns=2_000_000_000,
        receipt_monotonic_ns=2_000,
    )
    from tools.brain_senses_failure_receipt_v1 import canonical

    encoded = canonical(record)
    assert record["phase"] == "unknown"
    assert record["reason_code"] == "unclassified"
    assert b"secret key" not in encoded
    assert b"endpoint" not in encoded


def test_duplicate_journal_ids_reject_the_verified_none_proof() -> None:
    calls: list[dict[str, Any]] = []

    def snapshot(binding: launch_v3.PlanBinding) -> launch_v3.JournalSnapshot:
        return _snapshot(binding, reservations=("same-id", "same-id"))

    with pytest.raises(RuntimeError):
        _run(
            lambda _binding_arg: (_ for _ in ()).throw(RuntimeError("pre-intent")),
            snapshot_reader=snapshot,
            failure_sink=lambda **data: calls.append(data),
        )
    assert calls[0]["journal_proof"] is None


def test_lock_contention_never_dispatches_and_sink_loss_preserves_failure() -> None:
    events: list[str] = []
    dispatch_calls = 0
    sink_calls: list[dict[str, Any]] = []
    original = BlockingIOError("secret lock detail")

    def dispatch(_binding_arg: launch_v3.PlanBinding) -> None:
        nonlocal dispatch_calls
        dispatch_calls += 1

    def broken_sink(**_kwargs: Any) -> None:
        sink_calls.append(_kwargs)
        raise OSError("diagnostic sink secret path")

    with pytest.raises(BlockingIOError) as caught:
        _run(
            dispatch,
            dispatch_locks=_locks(events, fail_at="service"),
            proof_locks=_locks([]),
            failure_sink=broken_sink,
        )
    assert caught.value.args == original.args
    assert dispatch_calls == 0
    assert len(sink_calls) == 1
    assert sink_calls[0]["journal_proof"] is not None


def test_plan_binding_must_match_exact_validated_bytes_before_dispatch() -> None:
    called = 0
    sink_calls: list[dict[str, Any]] = []

    def bad_validator(_raw: bytes, _started: launch_v3.CallbackTimes) -> launch_v3.PlanBinding:
        return launch_v3.PlanBinding(
            SERVICE,
            "0" * 64,
            BUCKET,
            WINDOW_START_NS,
            WINDOW_END_NS,
            SOURCE_SHA,
            PRED_SHA,
        )

    with pytest.raises(launch_v3.LaunchV3Error, match="plan_bytes_changed"):
        launch_v3.dispatch_once(
            PLAN_BYTES,
            plan_validator=bad_validator,
            locks=_locks([]),
            dispatch=lambda _binding_arg: called,
            proof_locks=_locks([]),
            snapshot_reader=_snapshot,
            failure_sink=lambda **data: sink_calls.append(data),
        )
    assert called == 0
    assert sink_calls == []


def test_callback_outside_validated_plan_window_has_no_dispatch_or_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"dispatch": 0, "receipt": 0}
    monkeypatch.setattr(
        launch_v3,
        "callback_start",
        lambda: launch_v3.CallbackTimes(WINDOW_END_NS + 1, 123_001),
    )

    def dispatch(_binding_arg: launch_v3.PlanBinding) -> None:
        calls["dispatch"] += 1

    def sink(**_kwargs: Any) -> None:
        calls["receipt"] += 1

    with pytest.raises(launch_v3.LaunchV3Error, match="callback_outside_plan_window"):
        _run(dispatch, failure_sink=sink)
    assert calls == {"dispatch": 0, "receipt": 0}


@pytest.mark.parametrize("bad_pin", ["source", "predecessor"])
def test_snapshot_must_match_pinned_source_and_predecessor(
    bad_pin: str,
) -> None:
    received: list[dict[str, Any]] = []

    def snapshot(binding: launch_v3.PlanBinding) -> launch_v3.JournalSnapshot:
        if bad_pin == "source":
            return _snapshot(binding, source_sha="d" * 64)
        return _snapshot(binding, predecessor_sha="e" * 64)

    with pytest.raises(RuntimeError):
        _run(
            lambda _binding_arg: (_ for _ in ()).throw(RuntimeError("pre-intent")),
            snapshot_reader=snapshot,
            failure_sink=lambda **data: received.append(data),
        )
    assert received[0]["journal_proof"] is None
