from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from memecoin_ai.sources import Capture
from tools import brain_senses_capacity_coordinator_v2 as capacity
from tools import brain_senses_external_cutover_usage_v5 as cutover_usage
from tools import brain_senses_external_orchestrator_v4 as v4_runtime
from tools import brain_senses_external_orchestrator_v5 as runtime
from tools import brain_senses_external_rolling_policy_v5 as policy
from tools import brain_senses_external_schema_v5 as schema
from tools import brain_senses_external_verify_v5 as verify
from tools.brain_senses_external_orchestrator_v5 import RollingRuntimeError
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


def _seed_cutover_receipt(writer: DescriptorWriter, now_ns: int) -> str:
    writer.mkdir(runtime.SERVICE_RELATIVE)
    source_start = now_ns - 2 * policy.DAY_NS
    source_end = now_ns - policy.DAY_NS
    campaigns: dict[str, dict[str, object]] = {}
    for name, count in cutover_usage.LEGACY_COUNTS.items():
        source = cutover_usage.LEGACY_SOURCE_BINDINGS[name]
        cap = 0 if count == 0 else 4_000_000_000
        campaigns[name] = {
            "request_count": count,
            "dispatch_start_ns": cutover_usage.LEGACY_CAMPAIGN_START_NS,
            "dispatch_end_ns": cutover_usage.LEGACY_CAMPAIGN_END_NS,
            "response_bytes_upper": cap,
            "physical_bytes_upper": cap,
            **source,
            "attribution": "possible_same_account",
        }
    campaigns["c4"] = dict(cutover_usage.HISTORICAL_C4)
    v4_sources = v4_runtime.source_bindings()
    receipt = {
        "schema": cutover_usage.RECEIPT_SCHEMA,
        "schema_version": cutover_usage.RECEIPT_VERSION,
        "generated_ns": now_ns,
        "service": "helius",
        "source_v4": {
            "service_root": "services/external-rolling-v4",
            "plan_sha256": "1" * 64,
            "terminal_sha256": "2" * 64,
            "event_tail_sha256": "3" * 64,
            "allocation_manifest_sha256": "4" * 64,
            "source_bindings": v4_sources,
            "source_bindings_sha256": schema.sha(schema.canonical(v4_sources)),
            "terminal_status": "complete",
            "verified_interval_start_ns": source_start,
            "verified_interval_end_ns": source_end,
        },
        "cutover_exception": {
            "path": None,
            "sha256": None,
            "classification": None,
            "v4_policy_violation": False,
        },
        "legacy_campaigns": campaigns,
        "rolling_v4_request_rows": [],
        "rolling_v4_accounting": {
            "exact": True,
            "request_count": 0,
            "dispatch_start_ns": 0,
            "dispatch_end_ns": 0,
            "response_bytes_upper": 0,
            "physical_bytes_upper": 0,
        },
        "accounting": {
            "historical_c4_exact_count": True,
            "historical_c4_debit_requests": 375,
            "rolling_v4_exact": True,
            "rolling_v4_debit_requests": 0,
            "legacy_possible_same_account": True,
            "provider_charge_units": None,
            "provider_charge_status": "unknown_unverified",
        },
    }
    raw = schema.canonical(receipt)
    path = f"{runtime.SERVICE_RELATIVE}/{schema.CUTOVER_USAGE_FILE}"
    writer.create(path, raw)
    return schema.sha(raw)


def _seed_epoch(writer: DescriptorWriter, now_ns: int) -> tuple[str, dict[str, object]]:
    cutover_sha = _seed_cutover_receipt(writer, now_ns)
    anchor_ns = policy.next_fresh_anchor_ns(now_ns)
    epoch_id = schema.epoch_id(anchor_ns, "0123456789ab")
    relative = runtime._epoch_rel(epoch_id)
    writer.mkdir(relative)
    writer.mkdir(f"{relative}/captures")
    writer.mkdir(f"{relative}/intents")
    writer.mkdir(f"{relative}/outcomes")
    plan = schema.build_plan(
        epoch=epoch_id,
        created_ns=now_ns,
        anchor_ns=anchor_ns,
        root_id="a" * 32,
        volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        cutover_usage_sha256=cutover_sha,
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


class FakeCapacityCoordinator:
    def __init__(self, writer: DescriptorWriter) -> None:
        self.writer = writer
        self.reservations: list[capacity.Reservation] = []
        self.settlements: list[tuple[str, str, bool]] = []

    def reserve_and_persist_intent(self, **kwargs: object) -> capacity.Reservation:
        intent_path = str(kwargs["intent_path"])
        intent_bytes = bytes(kwargs["intent_bytes"])
        self.writer.create(intent_path, intent_bytes)
        reservation = capacity.Reservation(
            reservation_id=str(kwargs["reservation_id"]),
            service="helius",
            request_id=str(kwargs["request_id"]),
            intent_path=intent_path,
            outcome_path=str(kwargs["outcome_path"]),
            intent_sha256=str(kwargs["intent_sha256"]),
            reserved_bytes=int(kwargs["reserve_bytes"]),
            intent_bytes=intent_bytes,
        )
        self.reservations.append(reservation)
        return reservation

    def settle(self, reservation: capacity.Reservation, **kwargs: object) -> str:
        self.writer.create(str(kwargs["outcome_path"]), bytes(kwargs["outcome_bytes"]))
        self.settlements.append(
            (reservation.request_id, str(kwargs["outcome"]), bool(kwargs["ambiguous"]))
        )
        return "settled"


def _fake_capacity(
    writer: DescriptorWriter,
) -> tuple[FakeCapacityCoordinator, capacity.LaneLockLease]:
    # The synthetic frame calls only exercise the protocol boundary; no real lock,
    # capacity source, storage root, coordinator ledger, or provider is involved.
    coordinator = FakeCapacityCoordinator(writer)
    lease = capacity.LaneLockLease("helius", 0, object())
    return coordinator, lease


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
        coordinator, lane_lease = _fake_capacity(writer)
        runtime._current_frame(
            writer,
            epoch_id=epoch_id,
            plan=plan,
            window_index=0,
            mode="anchor",
            now_ns=clock,
            client=client,
            coordinator=coordinator,
            lane_lease=lane_lease,
            guard_check=lambda: None,
            sleep=lambda _: None,
        )
        projection = verify.verify_epoch_events(
            plan, writer.read_bytes(runtime._epoch_event_path(epoch_id))
        )
        assert projection.next_window_index == 1
        assert (
            runtime._latest_verified_anchor(
                writer, epoch_id, writer.read_bytes(runtime._epoch_event_path(epoch_id))
            )
            == 100
        )

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
                coordinator=coordinator,
                lane_lease=lane_lease,
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
        coordinator, lane_lease = _fake_capacity(writer)
        clock.value += 1_000_000
        runtime._current_frame(
            writer,
            epoch_id=epoch_id,
            plan=plan,
            window_index=1,
            mode="inventory",
            now_ns=clock,
            client=client,
            coordinator=coordinator,
            lane_lease=lane_lease,
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
            coordinator, lane_lease = _fake_capacity(writer)
            runtime._dispatch(
                writer,
                epoch_id,
                bucket=bucket,
                ordinal=0,
                method="getSlot",
                params=params,
                now_ns=TickClock(window["start_ns"] + 10),
                client=client,
                coordinator=coordinator,
                lane_lease=lane_lease,
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
    coordinator, lane_lease = _fake_capacity(writer)
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
                coordinator=coordinator,
                lane_lease=lane_lease,
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
        _seed_cutover_receipt(writer, 1_800_000_000_000_000_000)
        empty = policy.RollingUsage(0, 0, 0, 0)
        digest = runtime._write_budget_evidence(
            writer,
            reason="rolling_request_cap",
            disposition="headroom",
            at_ns=10**18,
            usage=empty,
        )
        assert runtime._budget_evidence_map(writer, {}) == {digest: "headroom:rolling_request_cap"}
    finally:
        writer.close()


def test_v5_removes_private_lifetime_cap_and_uses_shared_coordinator() -> None:
    assert not hasattr(runtime, "_allocation_cap")
    assert runtime.COORDINATOR_SERVICE_ROOTS["helius"] == "services/external-rolling-v5"
    assert runtime.COORDINATOR_ALLOCATION_ROOTS["helius"] == (
        "services/external-rolling-v4",
        "services/external-rolling-v5",
    )
    assert runtime.COORDINATOR_LOCK_PATHS["helius"] == ".external-rolling-v4.lock"


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
        "schema_version": 5,
        "seq": 0,
        "at_ns": 10**18,
        "status": "window_processed",
        "reason": None,
        "epoch_id": None,
        "next_window_index": 0,
        "request_count": 0,
        "rolling_requests_used": 0,
        "rolling_requests_remaining": policy.MAX_ROLLING_REQUESTS,
        "rolling_modeled_credits_used": 0,
        "rolling_modeled_credits_remaining": policy.MAX_ROLLING_CREDITS,
        "rolling_response_bytes_used": 0,
        "rolling_response_bytes_remaining": policy.MAX_ROLLING_RESPONSE_BYTES,
        "rolling_physical_bytes_used": 0,
        "rolling_physical_bytes_remaining": policy.MAX_ROLLING_PHYSICAL_BYTES,
        "helius_resident_allocated_bytes": 100,
        "helius_resident_allocation_cap_bytes": policy.HELIUS_RESIDENT_ALLOCATION_CAP_BYTES,
        "helius_resident_allocation_headroom_bytes": policy.HELIUS_RESIDENT_ALLOCATION_CAP_BYTES
        - 100,
        "helius_4gb_epoch_allowances_remaining": (policy.HELIUS_RESIDENT_ALLOCATION_CAP_BYTES - 100)
        // policy.MAX_EPOCH_PHYSICAL_BYTES,
        "helius_resident_early_warning": False,
        "forecast_days_observed_rolling_rate": None,
        "forecast_days_worst_case_4gb_per_epoch": 37.5,
        "prior_usage_lineage": "verified-v4-cutover-plus-v5-ledger:" + "a" * 64,
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
        changed_sources["tools/brain_senses_external_orchestrator_v5.py"] = "f" * 64
        stale_plan = schema.build_plan(
            epoch=epoch_id,
            created_ns=old_plan["created_ns"],
            anchor_ns=old_plan["anchor_ns"],
            root_id="a" * 32,
            volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
            cutover_usage_sha256=old_plan["cutover_usage_sha256"],
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
        writer.atomic_write(f"{epoch_relative}/{schema.EVENT_FILE}", schema.canonical(start))
        plans, terminals, projections = runtime._load_epochs(
            writer,
            root_id="a" * 32,
            volume_uuid="8f8f1081-9be4-4914-9111-75f1b90255bf",
        )
        assert plans[epoch_id] == stale_bytes
        assert projections[epoch_id].terminal_status == "abandoned"
        assert terminals[epoch_id].status == "abandoned"
        terminal = schema.decode(writer.read_bytes(f"{epoch_relative}/{schema.TERMINAL_FILE}"))
        assert terminal["reason"] == "source_binding_changed_before_dispatch"
    finally:
        writer.close()
