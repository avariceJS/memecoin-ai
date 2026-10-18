from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from memecoin_ai.sources import Capture
from tools import brain_senses_external_orchestrator_v4 as runtime
from tools import brain_senses_external_rolling_policy_v1 as policy
from tools import brain_senses_external_schema_v4 as schema
from tools import brain_senses_external_verify_v4 as verify
from tools.brain_senses_external_orchestrator_v4 import RollingRuntimeError
from tools.brain_senses_external_writer_v1 import DescriptorWriter


class TickClock:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        self.value += 1_000_000
        return self.value


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[object]]] = []

    def fetch(self, method: str, params: list[object]) -> Capture:
        self.calls.append((method, params))
        if method == "getSlot":
            result: object = 100 if len(self.calls) == 1 else 102
        elif method == "getBlocks":
            result = [101, 102]
        elif method == "getBlock":
            slot = int(params[0])
            identity = {
                "blockhash": f"hash-{slot}",
                "parentSlot": slot - 1,
                "previousBlockhash": f"previous-{slot}",
            }
            if params[1]["transactionDetails"] == "signatures":  # type: ignore[index]
                result = {**identity, "signatures": []}
            else:
                result = {**identity, "transactions": []}
        else:
            raise AssertionError(method)
        response = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": result},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        start = runtime.time.time_ns()
        # Use the controlled wall clock supplied by the orchestrator for ordering.
        start = getattr(self, "clock", None)() if getattr(self, "clock", None) else start
        receive = getattr(self, "clock", None)() if getattr(self, "clock", None) else start + 1
        request_json = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            sort_keys=True,
            separators=(",", ":"),
        )
        return Capture("helius-free", method, request_json, start, receive, response)


def _writer(tmp_path: Path) -> DescriptorWriter:
    root = tmp_path / "research-root"
    root.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    result = DescriptorWriter(fd)
    os.close(fd)
    return result


def _seed_epoch(writer: DescriptorWriter, now_ns: int) -> tuple[str, dict[str, object]]:
    anchor_ns = policy.next_fresh_anchor_ns(now_ns)
    epoch_id = schema.epoch_id(anchor_ns, "0123456789ab")
    relative = runtime._epoch_rel(epoch_id)
    writer.mkdir(relative)
    writer.mkdir(f"{relative}/captures")
    plan = schema.build_plan(
        epoch=epoch_id,
        created_ns=now_ns,
        anchor_ns=anchor_ns,
        root_id="a" * 32,
        volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        source_bindings=runtime.source_bindings(),
    )
    plan_bytes = schema.canonical(plan)
    writer.create(f"{relative}/{schema.PLAN_FILE}", plan_bytes)
    runtime._append_event(
        writer,
        runtime._epoch_event_path(epoch_id),
        event_schema=schema.EVENT_SCHEMA,
        kind="epoch_started",
        at_ns=now_ns,
        fields={
            "epoch_id": epoch_id,
            "plan_sha256": schema.sha(plan_bytes),
            "anchor_ns": anchor_ns,
        },
    )
    return epoch_id, plan


@pytest.mark.parametrize("crash_at", [13, 14])
def test_anchor_then_inventory_frame_replays_and_restart_is_read_only(
    tmp_path: Path, crash_at: int
) -> None:
    writer = _writer(tmp_path)
    try:
        now = 1_800_000_000_000_000_000
        epoch_id, plan = _seed_epoch(writer, now)
        clock = TickClock(plan["windows"][0]["start_ns"] + 1)
        client = FakeClient()
        client.clock = clock
        runtime._current_frame(
            writer,
            epoch_id=epoch_id,
            plan=plan,
            window_index=0,
            mode="anchor",
            now_ns=clock,
            client=client,
            guard_check=lambda: None,
            sleep=lambda _: None,
        )
        projection = verify.verify_epoch_events(
            plan, writer.read_bytes(runtime._epoch_event_path(epoch_id))
        )
        assert projection.next_window_index == 1
        assert runtime._latest_verified_anchor(
            writer, epoch_id, writer.read_bytes(runtime._epoch_event_path(epoch_id))
        ) == 100

        clock.value = plan["windows"][1]["start_ns"] + 1
        append_count = 0

        def crash_before_frame_seal(operation: str) -> None:
            nonlocal append_count
            if operation == "append_write":
                append_count += 1
                if append_count == crash_at:
                    raise OSError("simulated crash before durable frame boundary")

        writer._fault_hook = crash_before_frame_seal
        try:
            runtime._current_frame(
                writer,
                epoch_id=epoch_id,
                plan=plan,
                window_index=1,
                mode="inventory",
                now_ns=clock,
                client=client,
                guard_check=lambda: None,
                sleep=lambda _: None,
            )
        except OSError as exc:
            assert "simulated crash" in str(exc)
        else:
            raise AssertionError("expected injected crash before seal")
        assert len(client.calls) == 1 + 2 + 2 * 2
        writer.close()
        writer = _writer(tmp_path)
        runtime._load_epochs(
            writer,
            root_id="a" * 32,
            volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        )
        clock.value += 1_000_000
        runtime._current_frame(
            writer,
            epoch_id=epoch_id,
            plan=plan,
            window_index=1,
            mode="inventory",
            now_ns=clock,
            client=client,
            guard_check=lambda: None,
            sleep=lambda _: None,
        )
        event_bytes = writer.read_bytes(runtime._epoch_event_path(epoch_id))
        projection = verify.verify_epoch_events(plan, event_bytes)
        assert projection.next_window_index == 2
        assert len(client.calls) == 1 + 2 + 2 * 2
        runtime.verify_epoch_captures(
            writer, epoch_relative=runtime._epoch_rel(epoch_id), event_bytes=event_bytes
        )
        runtime._verify_sealed_frames(writer, epoch_id=epoch_id, plan=plan, event_bytes=event_bytes)
        before = len(client.calls)
        runtime._verify_sealed_frames(writer, epoch_id=epoch_id, plan=plan, event_bytes=event_bytes)
        assert len(client.calls) == before
    finally:
        writer.close()


def test_pending_intent_is_never_dispatched_again_after_restart(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    try:
        now = 1_800_000_000_000_000_000
        epoch_id, plan = _seed_epoch(writer, now)
        window = plan["windows"][0]
        bucket = window["bucket"]
        params = [{"commitment": "finalized"}]
        runtime._append_event(
            writer,
            runtime._epoch_event_path(epoch_id),
            event_schema=schema.EVENT_SCHEMA,
            kind="window_reserved",
            at_ns=window["start_ns"] + 1,
            fields={
                "index": 0,
                "bucket": bucket,
                "kind": "anchor",
                "mode": "anchor",
                "request_cap": 1,
            },
        )
        runtime._append_event(
            writer,
            runtime._epoch_event_path(epoch_id),
            event_schema=schema.EVENT_SCHEMA,
            kind="request_intent",
            at_ns=window["start_ns"] + 2,
            fields={
                "request_id": f"{bucket}-00",
                "bucket": bucket,
                "ordinal": 0,
                "method": "getSlot",
                "params": params,
                "wire_request_sha256": runtime.wire_request_sha256("getSlot", params),
                "decision_ns": window["start_ns"] + 2,
            },
        )
        client = FakeClient()
        with pytest.raises(RollingRuntimeError, match="ambiguous_request_possibly_spent_no_replay"):
            runtime._dispatch(
                writer,
                epoch_id,
                bucket=bucket,
                ordinal=0,
                method="getSlot",
                params=params,
                now_ns=TickClock(window["start_ns"] + 10),
                client=client,
                guard_check=lambda: None,
                sleep=lambda _: None,
            )
        assert client.calls == []
    finally:
        writer.close()


def test_detach_at_durable_intent_prevents_provider_dispatch(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    now = 1_800_000_000_000_000_000
    epoch_id, plan = _seed_epoch(writer, now)
    window = plan["windows"][0]
    bucket = window["bucket"]
    runtime._append_event(
        writer,
        runtime._epoch_event_path(epoch_id),
        event_schema=schema.EVENT_SCHEMA,
        kind="window_reserved",
        at_ns=window["start_ns"] + 1,
        fields={
            "index": 0,
            "bucket": bucket,
            "kind": "anchor",
            "mode": "anchor",
            "request_cap": 1,
        },
    )
    detached = False

    def detach_on_append(operation: str) -> None:
        nonlocal detached
        if operation == "append_write":
            detached = True
            raise OSError("simulated descriptor detach")

    writer._fault_hook = detach_on_append
    client = FakeClient()
    try:
        try:
            runtime._dispatch(
                writer,
                epoch_id,
                bucket=bucket,
                ordinal=0,
                method="getSlot",
                params=[{"commitment": "finalized"}],
                now_ns=TickClock(window["start_ns"] + 10),
                client=client,
                guard_check=lambda: None,
                sleep=lambda _: None,
            )
        except OSError as exc:
            assert "detach" in str(exc)
        else:
            raise AssertionError("expected detach during durable intent write")
        assert detached
        assert client.calls == []
    finally:
        writer.close()


def test_headroom_receipt_is_recomputed_before_budget_resume(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    try:
        writer.mkdir(runtime.SERVICE_RELATIVE)
        empty = policy.RollingUsage(0, 0, 0, 0)
        digest = runtime._write_budget_evidence(
            writer,
            reason="rolling_request_cap",
            disposition="headroom",
            at_ns=10**18,
            usage=empty,
        )
        assert runtime._budget_evidence_map(writer, {}) == {
            digest: "headroom:rolling_request_cap"
        }
    finally:
        writer.close()


def test_activation_cap_is_immutable_and_lifetime_pause_never_auto_resumes(
    tmp_path: Path,
) -> None:
    writer = _writer(tmp_path)
    try:
        writer.mkdir(runtime.SERVICE_RELATIVE)
        writer.mkdir(f"{runtime.SERVICE_RELATIVE}/epochs")

        class Guard:
            root_id = "a" * 32
            volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"

            def revalidate(self, *, min_free_bytes: int) -> object:
                assert min_free_bytes > policy.DISK_FLOOR_BYTES
                return type("Resolved", (), {"available_bytes": 219_384_127_488})()

        cap = runtime._allocation_cap(writer, Guard(), now_ns=10**18)
        assert cap["service_allocation_cap_bytes"] == 150_000_000_000
        assert runtime._allocation_cap(writer, Guard(), now_ns=10**18 + 1) == cap
        runtime._persist_lifetime_pause(
            writer,
            cap=cap,
            observed_bytes=150_000_000_000,
            at_ns=10**18 + 2,
        )
        assert runtime._verify_lifetime_pause(writer, cap)
    finally:
        writer.close()


def test_service_allocation_census_rejects_unexpected_files(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    try:
        writer.mkdir(runtime.SERVICE_RELATIVE)
        writer.create(f"{runtime.SERVICE_RELATIVE}/unexpected.log", b"x")
        with pytest.raises(RollingRuntimeError, match="service_inventory_unexpected_entry"):
            runtime._service_allocated_upper_bytes(writer)
    finally:
        writer.close()


def test_terminal_receipt_binds_epoch_allocation_manifest(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    try:
        now = 1_800_000_000_000_000_000
        epoch_id, plan = _seed_epoch(writer, now)
        plan_bytes = writer.read_bytes(f"{runtime._epoch_rel(epoch_id)}/{schema.PLAN_FILE}")
        runtime._finish_epoch(
            writer,
            epoch_id=epoch_id,
            plan_bytes=plan_bytes,
            status="abandoned",
            reason="test_terminal",
            pause_reason=None,
            now_ns=now + 1,
        )
        runtime._load_epochs(
            writer,
            root_id="a" * 32,
            volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        )
        writer.create(f"{runtime._epoch_rel(epoch_id)}/unexpected.bin", b"extra")
        with pytest.raises(RollingRuntimeError, match="terminal_receipt_invalid"):
            runtime._load_epochs(
                writer,
                root_id="a" * 32,
                volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
            )
    finally:
        writer.close()


def test_monitor_receipt_replays_after_restart() -> None:
    record: dict[str, object] = {
        "schema": runtime.MONITOR_SCHEMA,
        "schema_version": 4,
        "seq": 0,
        "at_ns": 10**18,
        "status": "window_processed",
        "reason": None,
        "epoch_id": None,
        "next_window_index": 0,
        "request_count": 0,
        "service_allocation_cap_sha256": "a" * 64,
        "service_allocated_upper_bytes": 100,
        "service_allocation_cap_bytes": 150_000_000_000,
        "volume_available_bytes": 200_000_000_000,
        "clock_record": {"schema": "clock-v1"},
        "clock_sha256": "b" * 64,
        "previous_sha256": None,
    }
    record["receipt_sha256"] = schema.sha(schema.canonical(record))
    raw = schema.canonical(record)
    runtime._verify_monitor_receipt(schema.decode(raw), raw)
    record["seq"] = 1
    with pytest.raises(RollingRuntimeError, match="poll_monitor_receipt_invalid"):
        runtime._verify_monitor_receipt(record, raw)


def test_source_change_before_dispatch_retires_only_the_unstarted_future_plan(
    tmp_path: Path,
) -> None:
    writer = _writer(tmp_path)
    try:
        now = 1_800_000_000_000_000_000
        epoch_id, old_plan = _seed_epoch(writer, now)
        changed_sources = dict(old_plan["source_bindings"])
        changed_sources["tools/brain_senses_external_orchestrator_v4.py"] = "f" * 64
        stale_plan = schema.build_plan(
            epoch=epoch_id,
            created_ns=old_plan["created_ns"],
            anchor_ns=old_plan["anchor_ns"],
            root_id="a" * 32,
            volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
            source_bindings=changed_sources,
        )
        stale_bytes = schema.canonical(stale_plan)
        epoch_relative = runtime._epoch_rel(epoch_id)
        writer.atomic_write(f"{epoch_relative}/{schema.PLAN_FILE}", stale_bytes)
        start = schema.event_record(
            schema=schema.EVENT_SCHEMA,
            seq=0,
            kind="epoch_started",
            at_ns=now,
            previous_sha256=None,
            fields={
                "epoch_id": epoch_id,
                "plan_sha256": schema.sha(stale_bytes),
                "anchor_ns": old_plan["anchor_ns"],
            },
        )
        writer.atomic_write(
            f"{epoch_relative}/{schema.EVENT_FILE}", schema.canonical(start)
        )
        plans, terminals, projections = runtime._load_epochs(
            writer,
            root_id="a" * 32,
            volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        )
        assert plans[epoch_id] == stale_bytes
        assert projections[epoch_id].terminal_status == "abandoned"
        assert terminals[epoch_id].status == "abandoned"
        terminal = schema.decode(
            writer.read_bytes(f"{epoch_relative}/{schema.TERMINAL_FILE}")
        )
        assert terminal["reason"] == "source_binding_changed_before_dispatch"
    finally:
        writer.close()
