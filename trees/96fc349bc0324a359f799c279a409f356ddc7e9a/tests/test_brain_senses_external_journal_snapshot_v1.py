from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import brain_senses_external_journal_snapshot_v1 as journal  # noqa: E402
from tools import brain_senses_external_launch_v3 as launch_v3  # noqa: E402
from tools import brain_senses_external_orchestrator_v2 as engine_v2  # noqa: E402
from tools import brain_senses_external_schema_v2 as schema_v2  # noqa: E402
from tools import brain_senses_external_verify_v2 as verify_v2  # noqa: E402
from tools.brain_senses_failure_receipt_v1 import _proof_state  # noqa: E402

SERVICE = "synthetic-journal-v3"
BUCKET = "20261012T0600Z"
START_NS = 1_791_787_200_000_000_000
END_NS = START_NS + 60_000_000_000
PLAN_SHA = "1" * 64
PRED_SHA = "2" * 64
SOURCE_SHA = hashlib.sha256(engine_v2.canonical(engine_v2.source_bindings())).hexdigest()
TAIL_0 = "3" * 64
TAIL_1 = "4" * 64
RESERVATION_SHA = "5" * 64
REQUEST_SHA = "6" * 64


def _binding() -> launch_v3.PlanBinding:
    return launch_v3.PlanBinding(SERVICE, PLAN_SHA, BUCKET, START_NS, END_NS, SOURCE_SHA, PRED_SHA)


def _event(kind: str, bucket: str | None, payload: dict[str, Any], tail: str) -> dict[str, Any]:
    return {"kind": kind, "bucket": bucket, "payload": payload, "event_sha256": tail}


def _state(*, spent: bool = False, tail: str = TAIL_0) -> dict[str, Any]:
    sources = engine_v2.source_bindings()
    cert = {"certificate_sha256": PRED_SHA}
    plan = {
        "service_id": SERVICE,
        "storage": {"relative_root": f"services/{SERVICE}"},
        "code_sha256": sources,
        "predecessor": {"certificate": cert, "certificate_sha256": PRED_SHA},
        "schedule": {
            "bootstrap": {"bucket": BUCKET, "start_ns": START_NS, "end_ns": END_NS},
            "frames": [],
        },
    }
    events = [_event("initialized", None, {"plan_sha256": PLAN_SHA}, tail)]
    if spent:
        events.extend(
            [
                _event("reservation", BUCKET, {"reservation_sha256": RESERVATION_SHA}, tail),
                _event("started", BUCKET, {}, tail),
                _event(
                    "request_intent",
                    BUCKET,
                    {"ordinal": 0, "request_sha256": REQUEST_SHA},
                    tail,
                ),
            ]
        )
    return {
        "status": "interrupted_no_replay" if spent else "verified_prefix",
        "plan": plan,
        "plan_sha256": PLAN_SHA,
        "events": events,
        "event_tail_sha256": tail,
        "reservations": {BUCKET: {}} if spent else {},
        "intents": {BUCKET: [0]} if spent else {BUCKET: []},
        "sealed": {},
        "missed": set(),
        "unresolved_buckets": [BUCKET] if spent else [],
        "stop": False,
    }


class _Reader:
    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = files or {}

    def read_bytes(self, relative: str, *, limit: int = 64_000_000) -> bytes:
        value = self.files[relative]
        if len(value) > limit:
            raise OSError("synthetic file exceeds limit")
        return value

    def list_files(self) -> list[str]:
        return sorted(self.files)


def _real_plan_and_reader() -> tuple[dict[str, Any], _Reader]:
    now = 1_791_787_200_000_000_000
    start = now + 5 * 60_000_000_000
    cert = {
        "kind": "external-v1-sealed",
        "service_id": "synthetic-predecessor",
        "plan_sha256": "7" * 64,
        "source_sha256": {"synthetic.py": "8" * 64},
        "storage": {
            "root_id": "synthetic-root",
            "volume_uuid": "synthetic-volume",
            "relative_root": "services/synthetic-predecessor",
        },
        "frontier": 100,
        "gap_intervals": [],
        "expires_ns": start - 120_000_000_000,
        "terminal_sealed_ns": start - 120_000_000_000,
    }
    plan = {
        "schema": schema_v2.PLAN_SCHEMA,
        "schema_version": 2,
        "service_id": SERVICE,
        "provider": "helius-free",
        "endpoint_origin": "https://mainnet.helius-rpc.com",
        "storage": {
            "root_id": "synthetic-root",
            "volume_uuid": "synthetic-volume",
            "relative_root": f"services/{SERVICE}",
        },
        "code_sha256": engine_v2.source_bindings(),
        "predecessor": {
            "certificate": cert,
            "certificate_sha256": engine_v2.sha(engine_v2.canonical(cert)),
            "relative_root": "services/synthetic-predecessor",
            "candidate_sha256": "9" * 64,
            "validation_sha256": "a" * 64,
            "completion_sha256": "b" * 64,
        },
        "schedule": engine_v2.build_schedule(start, now),
        "caps": dict(schema_v2.CAPS),
    }
    plan_bytes = engine_v2.canonical(plan)
    plan_hash = engine_v2.sha(plan_bytes)
    day_usage: list[dict[str, Any]] = []
    body = {
        "schema": schema_v2.EVENT_SCHEMA,
        "schema_version": 2,
        "seq": 0,
        "kind": "initialized",
        "bucket": None,
        "at_ns": now,
        "payload": {
            "plan_sha256": plan_hash,
            "predecessor_day_usage": day_usage,
            "predecessor_day_usage_sha256": engine_v2.sha(engine_v2.canonical(day_usage)),
        },
        "previous_sha256": "0" * 64,
    }
    event = {**body, "event_sha256": engine_v2.sha(engine_v2.canonical(body))}
    return plan, _Reader(
        {
            schema_v2.PLAN_FILE: plan_bytes,
            schema_v2.EVENTS_FILE: engine_v2.canonical(event),
            schema_v2.LOCK_FILE: b"synthetic-lock\n",
        }
    )


def _install_state(monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []

    def fake_verify(reader: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((reader, kwargs))
        return state

    monkeypatch.setattr(verify_v2, "verify", fake_verify)
    return calls


def test_clean_pre_dispatch_prefix_proves_verified_none_from_v2_verifier(monkeypatch):
    state = _state()
    calls = _install_state(monkeypatch, state)
    snapshot = journal.read_snapshot(_Reader(), _Reader(), _binding())
    assert snapshot.verified is True
    assert snapshot.reservation_ids == ()
    assert snapshot.intent_ids == ()
    assert snapshot.event_tail_sha256 == TAIL_0
    assert calls[0][1]["expected_plan_sha256"] == PLAN_SHA
    assert calls[0][1]["expected_sources"] == engine_v2.source_bindings()
    assert calls[0][1]["predecessor_reader"] is not None


def test_exact_descriptor_contents_pass_the_full_v2_plan_event_and_file_verifier(monkeypatch):
    plan, reader = _real_plan_and_reader()
    monkeypatch.setattr(
        verify_v2,
        "verify_live_predecessor",
        lambda _reader, _predecessor: {
            "plan": {"provider": plan["provider"], "endpoint_origin": plan["endpoint_origin"]},
            "day_usage": [],
        },
    )
    bootstrap = plan["schedule"]["bootstrap"]
    binding = launch_v3.PlanBinding(
        SERVICE,
        engine_v2.sha(reader.files[schema_v2.PLAN_FILE]),
        bootstrap["bucket"],
        bootstrap["start_ns"],
        bootstrap["end_ns"],
        SOURCE_SHA,
        plan["predecessor"]["certificate_sha256"],
    )
    snapshot = journal.read_snapshot(reader, _Reader(), binding)
    assert snapshot.verified is True
    assert snapshot.reservation_ids == ()
    assert snapshot.intent_ids == ()
    assert (
        snapshot.event_tail_sha256
        == json.loads(reader.files[schema_v2.EVENTS_FILE])["event_sha256"]
    )


def test_torn_or_duplicate_event_chain_fails_closed_under_full_v2_verifier(monkeypatch):
    plan, reader = _real_plan_and_reader()
    monkeypatch.setattr(
        verify_v2,
        "verify_live_predecessor",
        lambda _reader, _predecessor: {
            "plan": {"provider": plan["provider"], "endpoint_origin": plan["endpoint_origin"]},
            "day_usage": [],
        },
    )
    reader.files[schema_v2.EVENTS_FILE] = reader.files[schema_v2.EVENTS_FILE].rstrip(b"\n")
    bootstrap = plan["schedule"]["bootstrap"]
    binding = launch_v3.PlanBinding(
        SERVICE,
        engine_v2.sha(reader.files[schema_v2.PLAN_FILE]),
        bootstrap["bucket"],
        bootstrap["start_ns"],
        bootstrap["end_ns"],
        SOURCE_SHA,
        plan["predecessor"]["certificate_sha256"],
    )
    with pytest.raises(journal.JournalSnapshotError, match="service_journal_unverified"):
        journal.read_snapshot(reader, _Reader(), binding)


def test_post_intent_crash_is_possibly_spent_and_never_verified_none(monkeypatch):
    state = _state(spent=True, tail=TAIL_1)
    _install_state(monkeypatch, state)
    snapshot = journal.read_snapshot(_Reader(), _Reader(), _binding())
    assert snapshot.reservation_ids == (RESERVATION_SHA,)
    assert snapshot.intent_ids == (f"0:{REQUEST_SHA}",)

    reads = iter((snapshot, snapshot))
    proof = launch_v3.reread_journal_proof(
        _binding(),
        locks=launch_v3.OrderedLocks(*(_null_lock for _ in range(4))),
        snapshot_reader=lambda _binding: next(reads),
    )
    assert _proof_state(BUCKET, proof)[0] == "possibly_spent"


def test_mutation_between_two_fresh_verifier_reads_rejects_proof(monkeypatch):
    current = [_state(), _state(tail=TAIL_1)]
    calls = 0

    def fake_verify(_reader: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        state = current[calls]
        calls += 1
        return state

    monkeypatch.setattr(verify_v2, "verify", fake_verify)
    with pytest.raises(launch_v3.LaunchV3Error, match="journal_snapshot_changed"):
        launch_v3.reread_journal_proof(
            _binding(),
            locks=launch_v3.OrderedLocks(*(_null_lock for _ in range(4))),
            snapshot_reader=lambda binding: journal.read_snapshot(_Reader(), _Reader(), binding),
        )


def test_failed_full_journal_verification_never_yields_a_snapshot(monkeypatch):
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise verify_v2.VerificationError("sensitive detail suppressed")

    monkeypatch.setattr(verify_v2, "verify", fail)
    with pytest.raises(journal.JournalSnapshotError, match="service_journal_unverified"):
        journal.read_snapshot(_Reader(), _Reader(), _binding())


def test_predecessor_source_and_bucket_bindings_are_exact(monkeypatch):
    state = _state()
    _install_state(monkeypatch, state)
    wrong_source = launch_v3.PlanBinding(
        SERVICE, PLAN_SHA, BUCKET, START_NS, END_NS, "f" * 64, PRED_SHA
    )
    with pytest.raises(journal.JournalSnapshotError, match="source_pin_mismatch"):
        journal.read_snapshot(_Reader(), _Reader(), wrong_source)
    wrong_window = launch_v3.PlanBinding(
        SERVICE, PLAN_SHA, BUCKET, START_NS, END_NS + 1, SOURCE_SHA, PRED_SHA
    )
    with pytest.raises(journal.JournalSnapshotError, match="scheduled_window_mismatch"):
        journal.read_snapshot(_Reader(), _Reader(), wrong_window)


def _null_lock():
    import contextlib

    return contextlib.nullcontext()


def test_descriptor_reader_factories_are_reopened_and_fds_are_closed(tmp_path, monkeypatch):
    service_root = tmp_path / "service"
    predecessor_root = tmp_path / "predecessor"
    service_root.mkdir(mode=0o700)
    predecessor_root.mkdir(mode=0o700)
    os.chmod(service_root, 0o700)
    os.chmod(predecessor_root, 0o700)
    supplied: list[int] = []
    calls = 0

    def fake_read(service_reader: Any, predecessor_reader: Any, binding: Any) -> str:
        nonlocal calls
        calls += 1
        assert service_reader.fd != predecessor_reader.fd
        assert binding == _binding()
        return f"read-{calls}"

    monkeypatch.setattr(journal, "read_snapshot", fake_read)

    def open_fd(path: Path) -> int:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        supplied.append(fd)
        return fd

    reader = journal.DescriptorSnapshotReader(
        lambda: open_fd(service_root), lambda: open_fd(predecessor_root)
    )
    assert reader(_binding()) == "read-1"
    assert reader(_binding()) == "read-2"
    assert len(supplied) == 4
    for fd in supplied:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_descriptor_reader_rejects_symlinked_service_file(tmp_path):
    from tools.brain_senses_external_verify_v2 import DescriptorReaderV2

    root = tmp_path / "root"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    target = tmp_path / "outside"
    target.write_text("synthetic", encoding="utf-8")
    (root / "external-plan-v2.json").symlink_to(target)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with DescriptorReaderV2(root_fd) as reader:
            with pytest.raises(ValueError, match="unsafe_file"):
                reader.list_files()
    finally:
        os.close(root_fd)
