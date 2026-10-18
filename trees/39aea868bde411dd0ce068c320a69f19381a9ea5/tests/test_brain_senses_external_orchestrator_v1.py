from __future__ import annotations

import json
import os
from email.utils import formatdate
from pathlib import Path
from typing import Any

import pytest

from tools import brain_senses_external_orchestrator_v1 as engine
from tools.brain_senses_external_verify_v1 import DescriptorReader, verify
from tools.brain_senses_external_writer_v1 import DescriptorWriter

N = 1_800_000_000_000_000_000


def _root(tmp_path: Path) -> tuple[Path, int]:
    root = tmp_path / "data" / "services" / "finite"
    root.mkdir(parents=True, mode=0o700)
    for directory in (tmp_path / "data", tmp_path / "data" / "services", root):
        directory.chmod(0o700)
    return root, os.open(root, os.O_RDONLY | os.O_DIRECTORY)


def _plan(monkeypatch: Any) -> dict[str, Any]:
    monkeypatch.setattr(engine, "source_bindings", lambda: {"source.py": "a" * 64})
    return engine.build_plan(
        service_id="finite",
        root_id="root-id",
        volume_uuid="volume-id",
        relative_root="services/finite",
        predecessor={
            "campaign_id": "c4",
            "stop_sha256": "b" * 64,
            "terminal_receipt_sha256": "c" * 64,
            "committed_frontier": 80,
            "gap_intervals": [],
            "last_inventoried_interval": None,
        },
        clock_preflight_ns=N - 120 * 10**9,
        bootstrap_start_ns=N,
        frame_start_ns=N + 300 * 10**9,
        expires_ns=N + 480 * 10**9,
        endpoint_origin="https://mainnet.helius-rpc.com",
    )


def test_plan_pins_conservative_phase_and_peak_caps(monkeypatch: Any) -> None:
    plan = _plan(monkeypatch)
    caps = plan["caps"]
    assert plan["schedule"]["bootstrap"]["end_ns"] - N == 60 * 10**9
    assert plan["schedule"]["frame"]["end_ns"] - (N + 300 * 10**9) == 180 * 10**9
    assert caps["raw_bytes_total"] == 288_000_000
    assert caps["peak_write_margin_bytes"] == 290_000_000
    assert caps["frame_requests"] == 34


def test_spent_bucket_stops_without_loading_client_or_replaying(
    monkeypatch: Any, tmp_path: Path
) -> None:
    plan = _plan(monkeypatch)
    _, root_fd = _root(tmp_path)
    loader_calls = 0

    def fail_if_called() -> Any:
        nonlocal loader_calls
        loader_calls += 1
        raise AssertionError("spent bucket must not load credentials or dispatch")

    try:
        with DescriptorWriter(root_fd) as writer:
            engine.initialize(writer, plan)
            bucket = plan["schedule"]["bootstrap"]["bucket"]
            engine.append_event(
                writer,
                kind="reservation",
                bucket=bucket,
                at_ns=N + 10 * 10**9,
                payload={"reservation_sha256": "d" * 64},
            )
            result = engine.run_bucket(
                writer,
                bucket=bucket,
                client_loader=fail_if_called,
                now_ns=N + 10 * 10**9,
                clock_probe=fail_if_called,
            )
            assert result["status"] == "stopped"
            assert result["stop"]["reason"] == "interrupted_no_replay"
            assert result["provider_io"] is False
            assert loader_calls == 0
            with DescriptorReader(writer.root_fd) as reader:
                state = verify(reader, expected_sources={"source.py": "a" * 64})
            assert state["status"] in {"stopped", "interrupted_no_replay"}
    finally:
        os.close(root_fd)


@pytest.mark.parametrize("frame_failure", [None, "regressed", "oversized"])
def test_bootstrap_and_inventory_first_frame_verify_end_to_end(
    monkeypatch: Any, tmp_path: Path, frame_failure: str | None
) -> None:
    from memecoin_ai.sources import ReadOnlyRPC
    from tools import brain_senses_external_clock_v1 as clock

    plan = _plan(monkeypatch)
    _, root_fd = _root(tmp_path)
    current = [N - 120 * 10**9]
    monotonic = [50_000_000_000]
    monkeypatch.setattr(engine.time, "time_ns", lambda: current[0])
    monkeypatch.setattr(engine.time, "monotonic_ns", lambda: monotonic[0])

    def clock_pair() -> dict[str, Any]:
        checked = current[0] - 1_000_000_000
        checked_mono = monotonic[0] - 1_000_000_000
        samples = []
        for index, host in enumerate(clock.HOSTS):
            sent = checked - 100_000_000 + index * 10_000_000
            received = sent + 10_000_000
            samples.append(
                {
                    "host": host,
                    "status": 200,
                    "date": formatdate(sent // 1_000_000_000, usegmt=True),
                    "age": None,
                    "sent_ns": sent,
                    "received_ns": received,
                    "monotonic_sent_ns": checked_mono - 100_000_000 + index * 10_000_000,
                    "monotonic_received_ns": checked_mono - 90_000_000 + index * 10_000_000,
                }
            )
        return {
            "schema": clock.CLOCK_SCHEMA,
            "schema_version": 1,
            "samples": samples,
            "checked_ns": samples[-1]["received_ns"],
            "monotonic_ns": samples[-1]["monotonic_received_ns"],
        }

    stage = ["bootstrap"]
    fetched: list[str] = []

    def transport(_endpoint: str, request: bytes, _timeout: float, _limit: int) -> bytes:
        body = json.loads(request)
        method = body["method"]
        params = body["params"]
        fetched.append(method)
        if method == "getSlot":
            value: Any = 100 if stage[0] == "bootstrap" else 102
            if stage[0] == "frame" and frame_failure == "regressed":
                value = 100
            elif stage[0] == "frame" and frame_failure == "oversized":
                value = 20_101
        elif method == "getBlocks":
            value = [101, 102]
        else:
            slot, options = params
            common = {
                "blockhash": f"block-{slot}",
                "previousBlockhash": f"block-{slot - 1}",
                "parentSlot": slot - 1,
                "blockTime": 1234,
                "blockHeight": slot - 5,
            }
            value = (
                {**common, "signatures": []}
                if options["transactionDetails"] == "signatures"
                else {**common, "transactions": []}
            )
        return json.dumps({"jsonrpc": "2.0", "id": 1, "result": value}).encode()

    def client_loader() -> ReadOnlyRPC:
        return ReadOnlyRPC(
            "https://mainnet.helius-rpc.com/private-token-path",
            "helius-free",
            max_bytes=32_000_000,
            transport=transport,
            clock=lambda: current[0],
        )

    try:
        with DescriptorWriter(root_fd) as writer:
            engine.initialize(writer, plan)
            current[0] = N + 10 * 10**9
            boot_bucket = plan["schedule"]["bootstrap"]["bucket"]
            boot_result = engine.run_bucket(
                writer,
                bucket=boot_bucket,
                client_loader=client_loader,
                now_ns=N + 10 * 10**9,
                clock_probe=clock_pair,
            )
            assert boot_result["status"] == "complete"
            assert boot_result["bundle"]["bootstrap_slot"] == 100
            assert boot_result["provider_io"] is True

            stage[0] = "frame"
            current[0] = N + 310 * 10**9
            monotonic[0] += 300 * 10**9
            frame_bucket = plan["schedule"]["frame"]["bucket"]
            frame_result = engine.run_bucket(
                writer,
                bucket=frame_bucket,
                client_loader=client_loader,
                now_ns=N + 310 * 10**9,
                clock_probe=clock_pair,
            )
            assert fetched.count("getSlot") == 2
            if frame_failure is None:
                assert frame_result["status"] == "complete"
                assert frame_result["bundle"]["bootstrap_slot"] is None
                assert frame_result["bundle"]["frame"]["selected_slots"] == [101, 102]
                assert frame_result["provider_io"] is True
                assert fetched.count("getBlocks") == 1
                assert fetched.count("getBlock") == 4
            else:
                expected_reason = (
                    "external_writer_anchor_regression"
                    if frame_failure == "regressed"
                    else "external_writer_oversized_frame"
                )
                assert frame_result["status"] == "stopped"
                assert frame_result["stop"]["reason"] == expected_reason
                assert frame_result["provider_io"] is True
                assert fetched.count("getBlocks") == 0
                assert fetched.count("getBlock") == 0
                assert not writer.exists(f"buckets/{frame_bucket}/bundle.json")
                assert not writer.exists(f"buckets/{frame_bucket}/seal.json")

            with DescriptorReader(writer.root_fd) as reader:
                state = verify(reader, expected_sources={"source.py": "a" * 64})
            assert state["frontier"] == (103 if frame_failure is None else 101)
            assert state["status"] == ("verified_prefix" if frame_failure is None else "stopped")
            assert state["activation_authorized"] is False
    finally:
        os.close(root_fd)


@pytest.mark.parametrize("failure_mode", ["http_429", "rpc_error"])
def test_provider_failure_is_captured_and_never_retried(
    monkeypatch: Any, tmp_path: Path, failure_mode: str
) -> None:
    from memecoin_ai.sources import HTTPSourceError, ReadOnlyRPC
    from tools import brain_senses_external_clock_v1 as clock

    plan = _plan(monkeypatch)
    _, root_fd = _root(tmp_path)
    current = [N - 120 * 10**9]
    monotonic = [50_000_000_000]
    monkeypatch.setattr(engine.time, "time_ns", lambda: current[0])
    monkeypatch.setattr(engine.time, "monotonic_ns", lambda: monotonic[0])

    def clock_pair() -> dict[str, Any]:
        checked = current[0] - 1_000_000_000
        checked_mono = monotonic[0] - 1_000_000_000
        samples = []
        for index, host in enumerate(clock.HOSTS):
            sent = checked - 100_000_000 + index * 10_000_000
            received = sent + 10_000_000
            samples.append(
                {
                    "host": host,
                    "status": 200,
                    "date": formatdate(sent // 1_000_000_000, usegmt=True),
                    "age": None,
                    "sent_ns": sent,
                    "received_ns": received,
                    "monotonic_sent_ns": checked_mono - 100_000_000 + index * 10_000_000,
                    "monotonic_received_ns": checked_mono - 90_000_000 + index * 10_000_000,
                }
            )
        return {
            "schema": clock.CLOCK_SCHEMA,
            "schema_version": 1,
            "samples": samples,
            "checked_ns": samples[-1]["received_ns"],
            "monotonic_ns": samples[-1]["monotonic_received_ns"],
        }

    calls = 0

    def transport(_endpoint: str, _request: bytes, _timeout: float, _limit: int) -> bytes:
        nonlocal calls
        calls += 1
        if failure_mode == "http_429":
            raise HTTPSourceError(429)
        return b'{"jsonrpc":"2.0","id":1,"error":{"code":-32005,"message":"quota"}}'

    def client_loader() -> ReadOnlyRPC:
        return ReadOnlyRPC(
            "https://mainnet.helius-rpc.com/private-token-path",
            "helius-free",
            max_bytes=32_000_000,
            transport=transport,
            clock=lambda: current[0],
        )

    try:
        with DescriptorWriter(root_fd) as writer:
            engine.initialize(writer, plan)
            current[0] = N + 10 * 10**9
            bucket = plan["schedule"]["bootstrap"]["bucket"]
            result = engine.run_bucket(
                writer,
                bucket=bucket,
                client_loader=client_loader,
                now_ns=N + 10 * 10**9,
                clock_probe=clock_pair,
            )
            assert result["status"] == "stopped"
            assert result["stop"]["reason"] == (
                "provider_http_429" if failure_mode == "http_429" else "provider_or_parse_failure"
            )
            assert calls == 1
            result_row = json.loads(writer.read_bytes(f"buckets/{bucket}/results/00.json"))
            assert result_row["http_status"] == (429 if failure_mode == "http_429" else None)
            if failure_mode == "rpc_error":
                assert result_row["status"] == "captured"
                assert result_row["result_status"] == "provider_error"
            restarted = engine.run_bucket(
                writer,
                bucket=bucket,
                client_loader=client_loader,
                now_ns=N + 10 * 10**9,
                clock_probe=clock_pair,
            )
            assert restarted["status"] == "stopped"
            assert restarted["provider_io"] is False
            assert calls == 1
            with DescriptorReader(writer.root_fd) as reader:
                state = verify(reader, expected_sources={"source.py": "a" * 64})
            assert state["status"] == "stopped"
            assert state["requests"] == 1
    finally:
        os.close(root_fd)


def test_request_intent_before_dispatch_stops_and_restart_never_loads_client(
    monkeypatch: Any, tmp_path: Path
) -> None:
    from memecoin_ai.sources import ReadOnlyRPC
    from tools import brain_senses_external_clock_v1 as clock

    plan = _plan(monkeypatch)
    _, root_fd = _root(tmp_path)
    current = [N - 120 * 10**9]
    monotonic = [50_000_000_000]
    monkeypatch.setattr(engine.time, "time_ns", lambda: current[0])
    monkeypatch.setattr(engine.time, "monotonic_ns", lambda: monotonic[0])

    def clock_pair() -> dict[str, Any]:
        checked = current[0] - 1_000_000_000
        checked_mono = monotonic[0] - 1_000_000_000
        samples = []
        for index, host in enumerate(clock.HOSTS):
            sent = checked - 100_000_000 + index * 10_000_000
            received = sent + 10_000_000
            samples.append(
                {
                    "host": host,
                    "status": 200,
                    "date": formatdate(sent // 1_000_000_000, usegmt=True),
                    "age": None,
                    "sent_ns": sent,
                    "received_ns": received,
                    "monotonic_sent_ns": checked_mono - 100_000_000 + index * 10_000_000,
                    "monotonic_received_ns": checked_mono - 90_000_000 + index * 10_000_000,
                }
            )
        return {
            "schema": clock.CLOCK_SCHEMA,
            "schema_version": 1,
            "samples": samples,
            "checked_ns": samples[-1]["received_ns"],
            "monotonic_ns": samples[-1]["monotonic_received_ns"],
        }

    guard_calls = 0

    class Guard:
        def revalidate(self, **_kwargs: Any) -> None:
            nonlocal guard_calls
            guard_calls += 1
            if guard_calls == 3:
                raise RuntimeError("detached after request intent")

    transport_calls = 0
    loader_calls = 0

    def transport(_endpoint: str, _request: bytes, _timeout: float, _limit: int) -> bytes:
        nonlocal transport_calls
        transport_calls += 1
        return b'{"jsonrpc":"2.0","id":1,"result":100}'

    def client_loader() -> ReadOnlyRPC:
        nonlocal loader_calls
        loader_calls += 1
        return ReadOnlyRPC(
            "https://mainnet.helius-rpc.com/private-token-path",
            "helius-free",
            max_bytes=32_000_000,
            transport=transport,
            clock=lambda: current[0],
        )

    try:
        with DescriptorWriter(root_fd) as writer:
            engine.initialize(writer, plan)
            current[0] = N + 10 * 10**9
            bucket = plan["schedule"]["bootstrap"]["bucket"]
            result = engine.run_bucket(
                writer,
                bucket=bucket,
                client_loader=client_loader,
                guard=Guard(),
                now_ns=N + 10 * 10**9,
                clock_probe=clock_pair,
            )
            assert result["status"] == "stopped"
            assert writer.exists(f"buckets/{bucket}/requests/00.json")
            assert not writer.exists(f"buckets/{bucket}/results/00.json")
            assert transport_calls == 0
            assert loader_calls == 1

            restarted = engine.run_bucket(
                writer,
                bucket=bucket,
                client_loader=client_loader,
                now_ns=N + 10 * 10**9,
                clock_probe=clock_pair,
            )
            assert restarted["status"] == "stopped"
            assert restarted["provider_io"] is False
            assert transport_calls == 0
            assert loader_calls == 1
            with DescriptorReader(writer.root_fd) as reader:
                state = verify(reader, expected_sources={"source.py": "a" * 64})
            assert state["status"] == "stopped"
            assert state["requests"] == 1
    finally:
        os.close(root_fd)
