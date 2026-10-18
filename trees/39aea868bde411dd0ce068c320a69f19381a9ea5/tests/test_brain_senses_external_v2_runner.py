from __future__ import annotations

import os
from email.utils import formatdate
from pathlib import Path
from typing import Any

from memecoin_ai.sources import ReadOnlyRPC
from tests.test_brain_senses_external_v2_plan import _plan
from tools import brain_senses_external_orchestrator_v2 as engine
from tools import brain_senses_external_schema_v2 as schema
from tools import brain_senses_external_verify_v1 as v1
from tools import brain_senses_external_verify_v2 as v2
from tools.brain_senses_external_writer_v1 import DescriptorWriter


def _clock_record(wall_ns: int, mono_ns: int) -> dict[str, Any]:
    samples = []
    for index, host in enumerate(("www.cloudflare.com", "www.google.com")):
        sent = wall_ns - 200_000_000 + index * 10_000_000
        received = sent + 10_000_000
        mono_sent = mono_ns - 200_000_000 + index * 10_000_000
        mono_received = mono_sent + 10_000_000
        samples.append(
            {
                "host": host,
                "status": 200,
                "date": formatdate(sent // 1_000_000_000, usegmt=True),
                "age": None,
                "sent_ns": sent,
                "received_ns": received,
                "monotonic_sent_ns": mono_sent,
                "monotonic_received_ns": mono_received,
            }
        )
    return {
        "schema": "brain-senses-external-v1-clock",
        "schema_version": 1,
        "samples": samples,
        "checked_ns": samples[-1]["received_ns"],
        "monotonic_ns": samples[-1]["monotonic_received_ns"],
    }


class _Guard:
    available_bytes = 100 * 2**30

    def revalidate(self, *, min_free_bytes: int | None = None) -> None:
        assert min_free_bytes is None or min_free_bytes < self.available_bytes


def _initialized_writer(tmp_path: Path, monkeypatch: Any) -> tuple[DescriptorWriter, dict, int]:
    root = tmp_path / "service"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd)
    plan = _plan()
    monkeypatch.setattr(engine, "source_bindings", lambda: plan["code_sha256"])
    plan_raw = engine.canonical(plan)
    writer.atomic_write(schema.PLAN_FILE, plan_raw, create_only=True)
    engine._append_event(
        writer,
        kind="initialized",
        bucket=None,
        at_ns=plan["schedule"]["clock_preflight_ns"],
        payload={
            "plan_sha256": engine.sha(plan_raw),
            "predecessor_day_usage": [],
            "predecessor_day_usage_sha256": engine.sha(engine.canonical([])),
        },
    )
    return writer, plan, root_fd


def test_bootstrap_writer_persists_and_independently_seals_synthetic_capture(
    monkeypatch: Any, tmp_path: Path
) -> None:
    writer, plan, root_fd = _initialized_writer(tmp_path, monkeypatch)
    bucket = plan["schedule"]["bootstrap"]["bucket"]
    now = plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000
    mono = 800_000_000_000
    monkeypatch.setattr(engine.time, "time_ns", lambda: now)
    monkeypatch.setattr(engine.time, "monotonic_ns", lambda: mono)
    monkeypatch.setattr(
        v2,
        "verify_live_predecessor",
        lambda *_: {
            "day_usage": [],
            "plan": {"provider": plan["provider"], "endpoint_origin": plan["endpoint_origin"]},
        },
    )

    def transport(_endpoint: str, _body: bytes, _timeout: float, _limit: int) -> bytes:
        return b'{"jsonrpc":"2.0","id":1,"result":455802050}'

    client = ReadOnlyRPC(
        "https://mainnet.helius-rpc.com/api/test",
        "helius-free",
        max_bytes=schema.MAX_RESPONSE_BYTES,
        transport=transport,
        clock=lambda: now,
    )
    try:
        result = engine.run_bootstrap(
            writer,
            client_loader=lambda: client,
            guard=_Guard(),
            predecessor_reader=object(),
            clock_probe=lambda: _clock_record(now - 1_000_000, mono - 1_000_000),
        )
        assert result["status"] == "complete", result
        assert result["bundle"]["bootstrap_slot"] == 455802050
        with v1.DescriptorReader(writer.root_fd) as reader:
            state = v2.verify(
                reader, expected_sources=plan["code_sha256"], predecessor_reader=object()
            )
        assert state["status"] == "verified_prefix"
        assert state["frontier"] == 455802050
        assert state["buckets"][bucket]["seal"]["status"] == "complete"
    finally:
        client.close()
        writer.close()
        os.close(root_fd)


def test_bootstrap_spent_request_intent_never_loads_client(
    monkeypatch: Any, tmp_path: Path
) -> None:
    writer, plan, root_fd = _initialized_writer(tmp_path, monkeypatch)
    bucket = plan["schedule"]["bootstrap"]["bucket"]
    base_time = plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000
    clock_path = f"clock/{bucket}-00.json"
    clock_raw = engine.canonical(_clock_record(base_time - 500_000_000, 900_000_000_000))
    clock_hash = engine.sha(clock_raw)
    reservation_raw = engine.canonical(
        {
            "synthetic": "reservation",
            "clock_check_path": clock_path,
            "clock_check_sha256": clock_hash,
        }
    )
    started_raw = engine.canonical({"synthetic": "started"})
    writer.mkdir(f"buckets/{bucket}")
    writer.atomic_write(f"buckets/{bucket}/reservation.json", reservation_raw, create_only=True)
    writer.atomic_write(f"buckets/{bucket}/started.json", started_raw, create_only=True)
    writer.atomic_write(clock_path, clock_raw, create_only=True)
    engine._append_event(
        writer,
        kind="reservation",
        bucket=bucket,
        at_ns=base_time + 1,
        payload={"reservation_sha256": engine.sha(reservation_raw), "frame_index": None},
    )
    engine._append_event(
        writer,
        kind="started",
        bucket=bucket,
        at_ns=base_time + 2,
        payload={"started_sha256": engine.sha(started_raw)},
    )
    engine._append_event(
        writer,
        kind="request_intent",
        bucket=bucket,
        at_ns=base_time + 250_000_000,
        payload={
            "ordinal": 0,
            "request_sha256": "f" * 64,
            "request_started_ns": base_time + 250_000_000,
        },
    )
    monkeypatch.setattr(
        v2,
        "verify_live_predecessor",
        lambda *_: {
            "day_usage": [],
            "plan": {"provider": plan["provider"], "endpoint_origin": plan["endpoint_origin"]},
        },
    )
    calls = 0

    def fail_loader() -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError("spent bucket must not load provider credentials")

    try:
        result = engine.run_bootstrap(
            writer,
            client_loader=fail_loader,
            guard=_Guard(),
            predecessor_reader=object(),
            now_ns=plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000,
            clock_probe=lambda: (_ for _ in ()).throw(
                AssertionError("spent bucket must not probe")
            ),
        )
        assert result["status"] == "interrupted_no_replay"
        assert result["provider_io"] is False
        assert result["replay_authorized"] is False
        assert calls == 0
    finally:
        writer.close()
        os.close(root_fd)
