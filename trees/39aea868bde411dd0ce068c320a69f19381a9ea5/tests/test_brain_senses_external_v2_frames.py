from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from memecoin_ai.sources import HTTPSourceError, ReadOnlyRPC
from tests.test_brain_senses_external_v2_plan import _plan
from tests.test_brain_senses_external_v2_runner import _clock_record, _Guard
from tools import brain_senses_external_orchestrator_v2 as engine
from tools import brain_senses_external_schema_v2 as schema
from tools import brain_senses_external_verify_v1 as v1
from tools import brain_senses_external_verify_v2 as v2
from tools.brain_senses_external_writer_v1 import DescriptorWriter


def _root(tmp_path: Path, monkeypatch: Any) -> tuple[DescriptorWriter, dict[str, Any], int]:
    root = tmp_path / "service"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd)
    plan = _plan()
    now = time.time_ns()
    minute = 60_000_000_000
    start = ((now + 2 * minute + minute - 1) // minute) * minute
    plan["schedule"] = engine.build_schedule(start, now)
    plan["predecessor"]["certificate"]["expires_ns"] = start - 180_000_000_000
    plan["predecessor"]["certificate"]["terminal_sealed_ns"] = start - 180_000_000_000
    plan["predecessor"]["certificate_sha256"] = engine.sha(
        engine.canonical(plan["predecessor"]["certificate"])
    )
    plan["predecessor"]["certificate_sha256"] = engine.sha(
        engine.canonical(plan["predecessor"]["certificate"])
    )
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
    monkeypatch.setattr(
        v2,
        "verify_live_predecessor",
        lambda *_: {
            "day_usage": [],
            "plan": {"provider": plan["provider"], "endpoint_origin": plan["endpoint_origin"]},
        },
    )
    return writer, plan, root_fd


@pytest.mark.parametrize("inventory_count", [0, 16])
def test_bootstrap_then_inventory_signature_full_frame_verifies(
    monkeypatch: Any, tmp_path: Path, inventory_count: int
) -> None:
    writer, plan, root_fd = _root(tmp_path, monkeypatch)
    real_wall = time.time_ns
    offset = [plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000 - real_wall()]
    monkeypatch.setattr(engine.time, "time_ns", lambda: real_wall() + offset[0])
    slot = 455_802_050
    slots = list(range(slot, slot + inventory_count))
    calls: list[str] = []
    params_seen: list[tuple[str, Any]] = []

    def client() -> ReadOnlyRPC:
        def transport(_endpoint: str, body: bytes, _timeout: float, _limit: int) -> bytes:
            request = json.loads(body)
            method = request["method"]
            params = request["params"]
            calls.append(method)
            params_seen.append((method, params))
            if method == "getSlot":
                result: Any = slot if not calls[:-1] or not slots else slots[-1]
            elif method == "getBlocks":
                result = slots
            elif params[1]["transactionDetails"] == "signatures":
                block_slot = params[0]
                result = {
                    "blockhash": f"block-{block_slot}",
                    "previousBlockhash": f"block-{block_slot - 1}",
                    "parentSlot": block_slot - 1,
                    "blockTime": 1_800_000_000,
                    "blockHeight": block_slot,
                    "signatures": [f"sig-{block_slot}"],
                }
            else:
                block_slot = params[0]
                result = {
                    "blockhash": f"block-{block_slot}",
                    "previousBlockhash": f"block-{block_slot - 1}",
                    "parentSlot": block_slot - 1,
                    "blockTime": 1_800_000_000,
                    "blockHeight": block_slot,
                    "transactions": [
                        {
                            "transaction": {
                                "signatures": [f"sig-{block_slot}"],
                                "message": {
                                    "accountKeys": ["11111111111111111111111111111111"],
                                    "instructions": [],
                                },
                            },
                            "version": "legacy",
                            "meta": {
                                "err": None,
                                "innerInstructions": [],
                                "loadedAddresses": {"writable": [], "readonly": []},
                            },
                        }
                    ],
                }
            return json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": result}, separators=(",", ":")
            ).encode()

        return ReadOnlyRPC(
            "https://mainnet.helius-rpc.com/api/test",
            "helius-free",
            max_bytes=schema.MAX_RESPONSE_BYTES,
            transport=transport,
            clock=engine.time.time_ns,
        )

    def clock_probe() -> dict[str, Any]:
        wall = engine.time.time_ns()
        mono = time.monotonic_ns()
        return _clock_record(wall - 500_000_000, mono - 500_000_000)

    try:
        first = client()
        result = engine.run_bootstrap(
            writer,
            client_loader=lambda: first,
            guard=_Guard(),
            predecessor_reader=object(),
            clock_probe=clock_probe,
            now_ns=plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000,
        )
        first.close()
        assert result["status"] == "complete", result

        frame = plan["schedule"]["frames"][0]
        offset[0] += frame["start_ns"] - plan["schedule"]["bootstrap"]["start_ns"]
        second = client()
        result = engine.run_frame(
            writer,
            0,
            client_loader=lambda: second,
            guard=_Guard(),
            predecessor_reader=object(),
            clock_probe=clock_probe,
            now_ns=frame["start_ns"] + 10_000_000_000,
        )
        second.close()
        assert result["status"] == "complete", result
        assert calls.count("getBlock") == 2 * inventory_count
        assert len(calls) == 3 + 2 * inventory_count
        for method, params in params_seen:
            if method == "getBlock":
                detail = params[1]["transactionDetails"]
                expected_options = {
                    "commitment": "finalized",
                    "encoding": "json",
                    "transactionDetails": detail,
                    "rewards": False,
                }
                if detail == "full":
                    expected_options["maxSupportedTransactionVersion"] = 1
                assert params[1] == expected_options
        with v1.DescriptorReader(writer.root_fd) as reader:
            state = v2.verify(
                reader, expected_sources=plan["code_sha256"], predecessor_reader=object()
            )
            file_set = set(reader.list_files())
            durable_raw_bytes = sum(
                len(
                    reader.read_bytes(
                        f"buckets/{request_bucket}/captures/{ordinal:02d}/response.json"
                    )
                )
                for request_bucket, ordinals in state["intents"].items()
                for ordinal in ordinals
                if f"buckets/{request_bucket}/captures/{ordinal:02d}/response.json" in file_set
            )
        assert state["frontier"] == slot + max(inventory_count, 1)
        assert state["buckets"][frame["bucket"]]["seal"]["status"] == "complete"
        assert state["requests"] == 1 + 2 + 2 * inventory_count
        assert state["v2_raw_bytes"] == durable_raw_bytes
        assert state["v2_stored_bytes"] == durable_raw_bytes
        assert sum(row["requests"] for row in state["v2_day_usage"].values()) == state["requests"]
        assert (
            state["buckets"][frame["bucket"]]["bundle"]["frame"]["denominator"] == inventory_count
        )
        assert (
            state["buckets"][frame["bucket"]]["bundle"]["request_count"] == 2 + 2 * inventory_count
        )
    finally:
        writer.close()
        os.close(root_fd)


def test_frame_missed_window_is_logged_without_provider_load(
    monkeypatch: Any, tmp_path: Path
) -> None:
    writer, plan, root_fd = _root(tmp_path, monkeypatch)
    monkeypatch.setattr(
        v2,
        "verify_live_predecessor",
        lambda *_: {
            "day_usage": [],
            "plan": {"provider": plan["provider"], "endpoint_origin": plan["endpoint_origin"]},
        },
    )
    try:
        # Bootstrap is required first; create an expired bootstrap STOP prefix.
        now = plan["schedule"]["bootstrap"]["end_ns"] + 1_000_000_000
        stopped = engine.run_frame(
            writer,
            0,
            client_loader=lambda: (_ for _ in ()).throw(AssertionError("no provider")),
            guard=_Guard(),
            predecessor_reader=object(),
            now_ns=now,
            clock_probe=lambda: (_ for _ in ()).throw(AssertionError("no clock")),
        )
        assert stopped["status"] == "stopped"
        assert stopped["stop"]["reason"] == "missed_bootstrap"
        with v1.DescriptorReader(writer.root_fd) as reader:
            state = v2.verify(
                reader, expected_sources=plan["code_sha256"], predecessor_reader=object()
            )
        assert state["stop"] is True
        assert state["requests"] == 0
    finally:
        writer.close()
        os.close(root_fd)


def test_frame_429_stops_after_spent_intent_without_retry(monkeypatch: Any, tmp_path: Path) -> None:
    writer, plan, root_fd = _root(tmp_path, monkeypatch)
    real_wall = time.time_ns
    offset = [plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000 - real_wall()]
    monkeypatch.setattr(engine.time, "time_ns", lambda: real_wall() + offset[0])
    slot = 455_802_050
    requests: list[str] = []

    def clock_probe() -> dict[str, Any]:
        wall, mono = engine.time.time_ns(), time.monotonic_ns()
        return _clock_record(wall - 500_000_000, mono - 500_000_000)

    def make_client(*, fail_inventory: bool = False) -> ReadOnlyRPC:
        def transport(_endpoint: str, body: bytes, _timeout: float, _limit: int) -> bytes:
            request = json.loads(body)
            method = request["method"]
            requests.append(method)
            if fail_inventory and method == "getBlocks":
                raise HTTPSourceError(429)
            result: Any = slot if method == "getSlot" else []
            return json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": result}, separators=(",", ":")
            ).encode()

        return ReadOnlyRPC(
            "https://mainnet.helius-rpc.com/api/test",
            "helius-free",
            max_bytes=schema.MAX_RESPONSE_BYTES,
            transport=transport,
            clock=engine.time.time_ns,
        )

    try:
        bootstrap_client = make_client()
        assert (
            engine.run_bootstrap(
                writer,
                client_loader=lambda: bootstrap_client,
                guard=_Guard(),
                predecessor_reader=object(),
                clock_probe=clock_probe,
                now_ns=plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000,
            )["status"]
            == "complete"
        )
        bootstrap_client.close()
        frame = plan["schedule"]["frames"][0]
        offset[0] += frame["start_ns"] - plan["schedule"]["bootstrap"]["start_ns"]
        frame_client = make_client(fail_inventory=True)
        result = engine.run_frame(
            writer,
            0,
            client_loader=lambda: frame_client,
            guard=_Guard(),
            predecessor_reader=object(),
            clock_probe=clock_probe,
            now_ns=frame["start_ns"] + 10_000_000_000,
        )
        frame_client.close()
        assert result["status"] == "stopped"
        assert result["stop"]["reason"] == "provider_http_429"
        assert requests == ["getSlot", "getSlot", "getBlocks"]
        with v1.DescriptorReader(writer.root_fd) as reader:
            state = v2.verify(
                reader,
                expected_sources=plan["code_sha256"],
                predecessor_reader=object(),
            )
        assert state["status"] == "stopped"
        assert state["requests"] == 3
        assert schema.MAX_RESPONSE_BYTES < state["v2_raw_bytes"] < 3 * schema.MAX_RESPONSE_BYTES
    finally:
        writer.close()
        os.close(root_fd)


def test_malformed_full_block_with_matching_signatures_stops_before_bundle_seal_or_next_rpc(
    monkeypatch: Any, tmp_path: Path
) -> None:
    writer, plan, root_fd = _root(tmp_path, monkeypatch)
    real_wall = time.time_ns
    offset = [plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000 - real_wall()]
    monkeypatch.setattr(engine.time, "time_ns", lambda: real_wall() + offset[0])
    slot = plan["predecessor"]["certificate"]["frontier"] + 1
    methods: list[str] = []

    def client() -> ReadOnlyRPC:
        def transport(_endpoint: str, body: bytes, _timeout: float, _limit: int) -> bytes:
            request = json.loads(body)
            method = request["method"]
            params = request["params"]
            methods.append(method)
            if method == "getSlot":
                result: Any = slot
            elif method == "getBlocks":
                result = [slot]
            elif params[1]["transactionDetails"] == "signatures":
                result = {
                    "blockhash": f"block-{slot}",
                    "previousBlockhash": f"block-{slot - 1}",
                    "parentSlot": slot - 1,
                    "signatures": [f"sig-{slot}"],
                }
            else:
                # Signatures match the first response, but the transaction is
                # structurally incomplete and must stop the bucket immediately.
                result = {
                    "blockhash": f"block-{slot}",
                    "previousBlockhash": f"block-{slot - 1}",
                    "parentSlot": slot - 1,
                    "transactions": [{"transaction": {"signatures": [f"sig-{slot}"]}}],
                }
            return json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": result}, separators=(",", ":")
            ).encode()

        return ReadOnlyRPC(
            "https://mainnet.helius-rpc.com/api/test",
            "helius-free",
            max_bytes=schema.MAX_RESPONSE_BYTES,
            transport=transport,
            clock=engine.time.time_ns,
        )

    def clock_probe() -> dict[str, Any]:
        wall, mono = engine.time.time_ns(), time.monotonic_ns()
        return _clock_record(wall - 500_000_000, mono - 500_000_000)

    try:
        bootstrap_client = client()
        assert (
            engine.run_bootstrap(
                writer,
                client_loader=lambda: bootstrap_client,
                guard=_Guard(),
                predecessor_reader=object(),
                clock_probe=clock_probe,
                now_ns=plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000,
            )["status"]
            == "complete"
        )
        bootstrap_client.close()
        frame = plan["schedule"]["frames"][0]
        offset[0] += frame["start_ns"] - plan["schedule"]["bootstrap"]["start_ns"]
        frame_client = client()
        result = engine.run_frame(
            writer,
            0,
            client_loader=lambda: frame_client,
            guard=_Guard(),
            predecessor_reader=object(),
            clock_probe=clock_probe,
            now_ns=frame["start_ns"] + 10_000_000_000,
        )
        frame_client.close()
        assert result["status"] == "stopped"
        assert result["stop"]["reason"] == "invalid_frame_response"
        assert methods == ["getSlot", "getSlot", "getBlocks", "getBlock", "getBlock"]
        with v1.DescriptorReader(writer.root_fd) as reader:
            state = v2.verify(
                reader,
                expected_sources=plan["code_sha256"],
                predecessor_reader=object(),
            )
        assert state["status"] == "stopped"
        assert state["requests"] == 5
        assert frame["bucket"] not in state["sealed"]
        assert not any(
            e["kind"] == "bundle" and e["bucket"] == frame["bucket"] for e in state["events"]
        )
    finally:
        writer.close()
        os.close(root_fd)


def test_all_twelve_frames_advance_without_catchup_or_replay(
    monkeypatch: Any, tmp_path: Path
) -> None:
    writer, plan, root_fd = _root(tmp_path, monkeypatch)
    real_wall = time.time_ns
    offset = [plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000 - real_wall()]
    monkeypatch.setattr(engine.time, "time_ns", lambda: real_wall() + offset[0])
    slot = 455_802_050
    slot_queries = 0

    def make_client() -> ReadOnlyRPC:
        def transport(_endpoint: str, body: bytes, _timeout: float, _limit: int) -> bytes:
            nonlocal slot_queries
            request = json.loads(body)
            if request["method"] == "getSlot":
                current = slot + max(0, slot_queries - 1)
                slot_queries += 1
                result: Any = current
            else:
                result = []
            return json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": result}, separators=(",", ":")
            ).encode()

        return ReadOnlyRPC(
            "https://mainnet.helius-rpc.com/api/test",
            "helius-free",
            max_bytes=schema.MAX_RESPONSE_BYTES,
            transport=transport,
            clock=engine.time.time_ns,
        )

    def clock_probe() -> dict[str, Any]:
        wall, mono = engine.time.time_ns(), time.monotonic_ns()
        return _clock_record(wall - 500_000_000, mono - 500_000_000)

    try:
        bootstrap_client = make_client()
        assert (
            engine.run_bootstrap(
                writer,
                client_loader=lambda: bootstrap_client,
                guard=_Guard(),
                predecessor_reader=object(),
                clock_probe=clock_probe,
                now_ns=plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000,
            )["status"]
            == "complete"
        )
        bootstrap_client.close()
        prior_start = plan["schedule"]["bootstrap"]["start_ns"]
        for frame in plan["schedule"]["frames"]:
            offset[0] += frame["start_ns"] - prior_start
            prior_start = frame["start_ns"]
            client = make_client()
            result = engine.run_frame(
                writer,
                frame["index"],
                client_loader=lambda client=client: client,
                guard=_Guard(),
                predecessor_reader=object(),
                clock_probe=clock_probe,
                now_ns=frame["start_ns"] + 10_000_000_000,
            )
            client.close()
            assert result["status"] == "complete", result
        with v1.DescriptorReader(writer.root_fd) as reader:
            state = v2.verify(
                reader,
                expected_sources=plan["code_sha256"],
                predecessor_reader=object(),
            )
        assert state["status"] == "verified_prefix"
        assert state["requests"] == 25
        assert state["frontier"] == slot + 12
        assert all(state["sealed"].get(row["bucket"]) for row in plan["schedule"]["frames"])
    finally:
        writer.close()
        os.close(root_fd)


@pytest.mark.parametrize(
    ("anchor", "reason"),
    [
        (455_802_049, "external_writer_anchor_regression"),
        (455_822_050, "external_writer_oversized_frame"),
    ],
)
def test_invalid_frame_anchor_stops_before_inventory_or_replay(
    monkeypatch: Any, tmp_path: Path, anchor: int, reason: str
) -> None:
    writer, plan, root_fd = _root(tmp_path, monkeypatch)
    real_wall = time.time_ns
    offset = [plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000 - real_wall()]
    monkeypatch.setattr(engine.time, "time_ns", lambda: real_wall() + offset[0])
    slot = 455_802_050
    methods: list[str] = []

    def client(anchor_slot: int) -> ReadOnlyRPC:
        def transport(_endpoint: str, body: bytes, _timeout: float, _limit: int) -> bytes:
            method = json.loads(body)["method"]
            methods.append(method)
            result: Any = slot if len(methods) == 1 else anchor_slot
            return json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": result}, separators=(",", ":")
            ).encode()

        return ReadOnlyRPC(
            "https://mainnet.helius-rpc.com/api/test",
            "helius-free",
            max_bytes=schema.MAX_RESPONSE_BYTES,
            transport=transport,
            clock=engine.time.time_ns,
        )

    def clock_probe() -> dict[str, Any]:
        wall, mono = engine.time.time_ns(), time.monotonic_ns()
        return _clock_record(wall - 500_000_000, mono - 500_000_000)

    try:
        bootstrap_client = client(slot)
        assert (
            engine.run_bootstrap(
                writer,
                client_loader=lambda: bootstrap_client,
                guard=_Guard(),
                predecessor_reader=object(),
                clock_probe=clock_probe,
                now_ns=plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000,
            )["status"]
            == "complete"
        )
        bootstrap_client.close()
        frame = plan["schedule"]["frames"][0]
        offset[0] += frame["start_ns"] - plan["schedule"]["bootstrap"]["start_ns"]
        frame_client = client(anchor)
        result = engine.run_frame(
            writer,
            0,
            client_loader=lambda: frame_client,
            guard=_Guard(),
            predecessor_reader=object(),
            clock_probe=clock_probe,
            now_ns=frame["start_ns"] + 10_000_000_000,
        )
        frame_client.close()
        assert result["status"] == "stopped"
        assert result["stop"]["reason"] == reason
        assert methods == ["getSlot", "getSlot"]
        with v1.DescriptorReader(writer.root_fd) as reader:
            state = v2.verify(
                reader,
                expected_sources=plan["code_sha256"],
                predecessor_reader=object(),
            )
        assert state["status"] == "stopped"
        assert state["frontier"] == slot
        assert state["requests"] == 2
    finally:
        writer.close()
        os.close(root_fd)


def test_expired_empty_frame_is_marked_missed_then_later_frame_can_run(
    monkeypatch: Any, tmp_path: Path
) -> None:
    writer, plan, root_fd = _root(tmp_path, monkeypatch)
    real_wall = time.time_ns
    offset = [plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000 - real_wall()]
    monkeypatch.setattr(engine.time, "time_ns", lambda: real_wall() + offset[0])
    slot = 455_802_050

    def client() -> ReadOnlyRPC:
        def transport(_endpoint: str, body: bytes, _timeout: float, _limit: int) -> bytes:
            method = json.loads(body)["method"]
            result: Any = slot if method == "getSlot" else []
            return json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": result}, separators=(",", ":")
            ).encode()

        return ReadOnlyRPC(
            "https://mainnet.helius-rpc.com/api/test",
            "helius-free",
            max_bytes=schema.MAX_RESPONSE_BYTES,
            transport=transport,
            clock=engine.time.time_ns,
        )

    def clock_probe() -> dict[str, Any]:
        wall, mono = engine.time.time_ns(), time.monotonic_ns()
        return _clock_record(wall - 500_000_000, mono - 500_000_000)

    try:
        bootstrap_client = client()
        assert (
            engine.run_bootstrap(
                writer,
                client_loader=lambda: bootstrap_client,
                guard=_Guard(),
                predecessor_reader=object(),
                clock_probe=clock_probe,
                now_ns=plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000,
            )["status"]
            == "complete"
        )
        bootstrap_client.close()
        frame = plan["schedule"]["frames"][1]
        offset[0] += frame["start_ns"] - plan["schedule"]["bootstrap"]["start_ns"]
        frame_client = client()
        result = engine.run_frame(
            writer,
            1,
            client_loader=lambda: frame_client,
            guard=_Guard(),
            predecessor_reader=object(),
            clock_probe=clock_probe,
            now_ns=frame["start_ns"] + 10_000_000_000,
        )
        frame_client.close()
        assert result["status"] == "complete", result
        with v1.DescriptorReader(writer.root_fd) as reader:
            state = v2.verify(
                reader,
                expected_sources=plan["code_sha256"],
                predecessor_reader=object(),
            )
        assert state["status"] == "verified_prefix"
        assert plan["schedule"]["frames"][0]["bucket"] in state["missed"]
        assert state["sealed"].get(frame["bucket"]) is True
    finally:
        writer.close()
        os.close(root_fd)


def test_frame_at_end_minus_four_seconds_is_missed_with_actual_timestamp(
    monkeypatch: Any, tmp_path: Path
) -> None:
    writer, plan, root_fd = _root(tmp_path, monkeypatch)
    real_wall = time.time_ns
    offset = [plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000 - real_wall()]
    monkeypatch.setattr(engine.time, "time_ns", lambda: real_wall() + offset[0])
    slot = plan["predecessor"]["certificate"]["frontier"]
    requests: list[str] = []

    def client() -> ReadOnlyRPC:
        def transport(_endpoint: str, body: bytes, _timeout: float, _limit: int) -> bytes:
            method = json.loads(body)["method"]
            requests.append(method)
            result: Any = slot if method == "getSlot" else []
            return json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": result}, separators=(",", ":")
            ).encode()

        return ReadOnlyRPC(
            "https://mainnet.helius-rpc.com/api/test",
            "helius-free",
            max_bytes=schema.MAX_RESPONSE_BYTES,
            transport=transport,
            clock=engine.time.time_ns,
        )

    def clock_probe() -> dict[str, Any]:
        wall, mono = engine.time.time_ns(), time.monotonic_ns()
        return _clock_record(wall - 500_000_000, mono - 500_000_000)

    try:
        bootstrap_client = client()
        result = engine.run_bootstrap(
            writer,
            client_loader=lambda: bootstrap_client,
            guard=_Guard(),
            predecessor_reader=object(),
            clock_probe=clock_probe,
            now_ns=plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000,
        )
        bootstrap_client.close()
        assert result["status"] == "complete", result
        frame = plan["schedule"]["frames"][0]
        missed_ns = frame["end_ns"] - 4_000_000_000
        offset[0] = missed_ns - real_wall()
        missed = engine.run_frame(
            writer,
            0,
            client_loader=lambda: (_ for _ in ()).throw(AssertionError("missed frame has no RPC")),
            guard=_Guard(),
            predecessor_reader=object(),
            now_ns=missed_ns,
        )
        assert missed["status"] == "missed"
        assert requests == ["getSlot"]
        with v1.DescriptorReader(writer.root_fd) as reader:
            state = v2.verify(
                reader,
                expected_sources=plan["code_sha256"],
                predecessor_reader=object(),
            )
        event = next(e for e in state["events"] if e["kind"] == "missed")
        assert event["at_ns"] == missed_ns
    finally:
        writer.close()
        os.close(root_fd)
