from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from memecoin_ai.domain import canonical
from memecoin_ai.sources import Capture
from tools import brain_senses_block_epoch_v2 as epoch_v2
from tools import brain_senses_block_epoch_v2_schedule as schedule_v2
from tools import brain_senses_block_epoch_v3_launch as launch_v3
from tools import brain_senses_block_epoch_v3_schedule as schedule_v3


class FakeRPC:
    def __init__(
        self,
        *,
        anchors: list[int] | None = None,
        inventory: list[int] | None = None,
        fail_method: str | None = None,
        fail_slot: int | None = None,
        plan_root: Path | None = None,
        frame_root: Path | None = None,
    ) -> None:
        self.anchors = list(anchors or [100])
        self.inventory = inventory
        self.fail_method = fail_method
        self.fail_slot = fail_slot
        self.plan_root = plan_root
        self.frame_root = frame_root
        self.calls: list[tuple[str, list[Any]]] = []

    def fetch(self, method: str, params: list[Any]) -> Capture:
        if self.plan_root is not None:
            assert (self.plan_root / "epoch-plan.json").exists()
        if method == "getBlocks" and self.frame_root is not None:
            assert (self.frame_root / "frame.json").exists()
        self.calls.append((method, params))
        if method == self.fail_method:
            if self.fail_slot is None or params[0] == self.fail_slot:
                raise TimeoutError("fixture_timeout")
        if method == "getSlot":
            value: Any = self.anchors.pop(0) if len(self.anchors) > 1 else self.anchors[0]
        elif method == "getBlocks":
            value = (
                self.inventory
                if self.inventory is not None
                else list(range(int(params[0]), int(params[1]) + 1))
            )
        else:
            slot = int(params[0])
            detail = params[1]["transactionDetails"]
            signatures = [f"sig-{slot}-0", f"sig-{slot}-1"]
            if detail == "signatures":
                value = {
                    "blockhash": f"hash-{slot}",
                    "parentSlot": slot - 1,
                    "previousBlockhash": f"hash-{slot - 1}",
                    "signatures": signatures,
                }
            else:
                value = {
                    "blockhash": f"hash-{slot}",
                    "parentSlot": slot - 1,
                    "previousBlockhash": f"hash-{slot - 1}",
                    "blockTime": 1_800_000_000 + slot,
                    "transactions": [
                        {
                            "transaction": {
                                "signatures": [signature],
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
                        for signature in signatures
                    ],
                }
        now = time.time_ns()
        request = canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        response = canonical({"jsonrpc": "2.0", "id": 1, "result": value}).encode()
        return Capture("fixture", method, request, now, now + 10, response)

    def close(self) -> None:
        return


def make_plan(
    root: Path,
    *,
    bootstrap: bool = False,
    next_start: int | None = None,
    frame_width_cap: int = 20,
    selection_count: int = 2,
    request_budget: int = 20,
    minimum_free_disk_bytes: int = 0,
) -> dict[str, Any]:
    return epoch_v2.build_epoch_plan(
        epoch_id=root.name,
        bucket=root.name,
        schedule_id="fixture-schedule",
        schedule_sha256="a" * 64,
        seed="fixed-fixture-seed",
        bootstrap=bootstrap,
        next_start=next_start,
        frame_width_cap=frame_width_cap,
        selection_count=selection_count,
        request_budget=request_budget,
        raw_byte_budget=128_000_000,
        stored_byte_budget=128_000_000,
        minimum_free_disk_bytes=minimum_free_disk_bytes,
    )


def run(root: Path, plan: dict[str, Any], client: FakeRPC) -> dict[str, Any]:
    return epoch_v2.run_epoch(root, plan, clients={"helius-free": client})


def test_bootstrap_is_anchor_only_and_persists_receipt_frontier(tmp_path: Path) -> None:
    root = tmp_path / "bootstrap"
    client = FakeRPC(anchors=[500])

    result = run(root, make_plan(root, bootstrap=True), client)

    assert result["status"] == "bootstrap_complete"
    assert result["advance_to"] == 501
    assert len(client.calls) == 1
    assert client.calls[0][0] == "getSlot"
    anchor = json.loads((root / "request-results" / "000000000001.json").read_text())
    reservation = json.loads((root / "requests" / "000000000001.json").read_text())
    assert anchor["receipt_ns"] == anchor["availability_ns"]
    assert anchor["receipt_ns"] > anchor["request_started_ns"]
    assert result["bootstrap_receipt_ns"] == anchor["receipt_ns"]
    assert reservation["request_reservation"] == 1
    assert reservation["provider_charge_units"] is None
    assert reservation["provider_charge_status"] == "unknown_unverified"
    assert "credit_reservation" not in reservation
    assert not (root / "frame.json").exists()


def test_inventory_frame_samples_only_produced_members_and_is_reproducible(
    tmp_path: Path,
) -> None:
    root = tmp_path / "inventory-frame"
    client = FakeRPC(anchors=[105], inventory=[100, 102, 104], plan_root=root, frame_root=root)
    plan = make_plan(root, next_start=100, selection_count=2)

    result = run(root, plan, client)

    frame = json.loads((root / "frame.json").read_text())
    selection = json.loads((root / "selection-plan.json").read_text())
    assert [frame["start"], frame["end_exclusive"]] == [100, 106]
    assert client.calls[1] == ("getBlocks", [100, 105, {"commitment": "finalized"}])
    assert selection["inventory_denominator"] == 3
    assert len(selection["selected_slots"]) == 2
    assert set(selection["selected_slots"]) <= {100, 102, 104}
    assert selection["inclusion_probability"] == "2/3"
    body_slots = {params[0] for method, params in client.calls if method == "getBlock"}
    assert body_slots == set(selection["selected_slots"])
    assert result["status"] == "frame_complete"
    assert (
        result["inventory_sha256"]
        == json.loads((root / "request-results" / "000000000002.json").read_text())[
            "response_sha256"
        ]
    )
    assert all(
        row["status"] == "qualified_structural_reference_count"
        for row in result["structural_checks"]
    )

    repeat_client = FakeRPC(anchors=[105], inventory=[100, 102, 104])
    repeated = run(root, plan, repeat_client)
    assert repeated == result
    assert repeat_client.calls == []


def test_anchor_restart_reuses_success_and_ambiguous_anchor_stops_without_retry(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "anchor-resume"
    client = FakeRPC(anchors=[105], inventory=[100, 102, 104], plan_root=root)
    plan = make_plan(root, next_start=100)
    original = epoch_v2._dispatch

    def interrupt_after_anchor(*args: Any, **kwargs: Any) -> dict[str, Any]:
        row = original(*args, **kwargs)
        if args[2] == 1:
            raise KeyboardInterrupt
        return row

    monkeypatch.setattr(epoch_v2, "_dispatch", interrupt_after_anchor)
    with pytest.raises(KeyboardInterrupt):
        run(root, plan, client)
    assert len(client.calls) == 1

    monkeypatch.setattr(epoch_v2, "_dispatch", original)
    result = run(root, plan, client)
    assert result["status"] == "frame_complete"
    assert client.calls.count(("getSlot", [{"commitment": "finalized"}])) == 1

    ambiguous_root = tmp_path / "anchor-ambiguous"
    ambiguous_client = FakeRPC()

    def reserve_then_interrupt(_method: str, _params: list[Any]) -> Capture:
        ambiguous_client.calls.append((_method, _params))
        raise KeyboardInterrupt

    ambiguous_client.fetch = reserve_then_interrupt  # type: ignore[method-assign]
    ambiguous_plan = make_plan(ambiguous_root, next_start=100)
    with pytest.raises(KeyboardInterrupt):
        run(ambiguous_root, ambiguous_plan, ambiguous_client)
    retry = FakeRPC(anchors=[105])
    stopped = run(ambiguous_root, ambiguous_plan, retry)
    assert stopped["status"] == "anchor_ambiguous"
    assert stopped["advance_to"] is None
    assert retry.calls == []


def test_inventory_restart_and_ambiguous_or_failed_inventory_stop(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "inventory-resume"
    client = FakeRPC(anchors=[105], inventory=[100, 102, 104], plan_root=root)
    plan = make_plan(root, next_start=100)
    original = epoch_v2._dispatch

    def interrupt_after_inventory(*args: Any, **kwargs: Any) -> dict[str, Any]:
        row = original(*args, **kwargs)
        if args[3]["phase"] == "inventory":
            raise KeyboardInterrupt
        return row

    monkeypatch.setattr(epoch_v2, "_dispatch", interrupt_after_inventory)
    with pytest.raises(KeyboardInterrupt):
        run(root, plan, client)
    assert len(client.calls) == 2

    monkeypatch.setattr(epoch_v2, "_dispatch", original)
    result = run(root, plan, client)
    assert result["status"] == "frame_complete"
    assert len(client.calls) == 2 + 2 * len(result["selected_slots"])

    failed_root = tmp_path / "inventory-failure"
    failed_client = FakeRPC(anchors=[105], fail_method="getBlocks")
    failed = run(failed_root, make_plan(failed_root, next_start=100), failed_client)
    assert failed["status"] == "inventory_unknown"
    assert failed["advance_to"] == 106
    assert len(failed_client.calls) == 2
    assert json.loads((failed_root / "STOP").read_text())["reason"] == (
        "getblocks_request_failed_or_unparsed"
    )

    ambiguous_root = tmp_path / "inventory-ambiguous"
    ambiguous_client = FakeRPC(anchors=[105])

    def interrupt_inventory(method: str, params: list[Any]) -> Capture:
        if method == "getBlocks":
            ambiguous_client.calls.append((method, params))
            raise KeyboardInterrupt
        return FakeRPC.fetch(ambiguous_client, method, params)

    ambiguous_client.fetch = interrupt_inventory  # type: ignore[method-assign]
    ambiguous_plan = make_plan(ambiguous_root, next_start=100)
    with pytest.raises(KeyboardInterrupt):
        run(ambiguous_root, ambiguous_plan, ambiguous_client)
    retry = FakeRPC(anchors=[105], inventory=[100, 102, 104])
    stopped = run(ambiguous_root, ambiguous_plan, retry)
    assert stopped["status"] == "inventory_unknown"
    assert stopped["advance_to"] == 106
    assert retry.calls == []


def test_anchor_regression_empty_and_oversized_frames_do_not_query_inventory(
    tmp_path: Path,
) -> None:
    regression_root = tmp_path / "regression"
    regression = run(
        regression_root,
        make_plan(regression_root, next_start=100),
        FakeRPC(anchors=[98]),
    )
    assert regression["status"] == "anchor_regression"
    assert regression["advance_to"] is None

    empty_root = tmp_path / "empty"
    empty_client = FakeRPC(anchors=[99])
    empty = run(empty_root, make_plan(empty_root, next_start=100), empty_client)
    assert empty["status"] == "empty_frame"
    assert empty["advance_to"] == 100
    assert [method for method, _ in empty_client.calls] == ["getSlot"]

    large_root = tmp_path / "oversized"
    large_client = FakeRPC(anchors=[111])
    oversized = run(
        large_root,
        make_plan(large_root, next_start=100, frame_width_cap=10),
        large_client,
    )
    assert oversized["status"] == "oversized_frame"
    assert oversized["advance_to"] == 112
    assert [method for method, _ in large_client.calls] == ["getSlot"]


def test_failed_selected_body_is_a_gap_and_never_replaced(tmp_path: Path) -> None:
    root = tmp_path / "body-failure"
    client = FakeRPC(
        anchors=[105], inventory=[100, 102, 104], fail_method="getBlock", fail_slot=102
    )
    result = run(root, make_plan(root, next_start=100, selection_count=3), client)
    selection = json.loads((root / "selection-plan.json").read_text())
    failed = [
        json.loads(path.read_text())
        for path in sorted((root / "request-results").glob("*.json"))
        if json.loads(path.read_text())["status"] == "request_failed"
    ]
    assert result["selected_slots"] == selection["selected_slots"]
    assert result["status"] == "sampled_gaps"
    assert len(failed) == 2
    assert {row["slot"] for row in failed} == {102}
    assert {params[0] for method, params in client.calls if method == "getBlock"} == set(
        selection["selected_slots"]
    )
    assert result["advance_to"] == 106
    assert {
        row["slot"]
        for row in result["structural_checks"]
        if row["status"] == "self_check_failed_or_unknown"
    } == {102}


def test_disk_floor_stops_before_next_body_and_records_selected_denominators(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "disk-floor"
    client = FakeRPC(anchors=[100], inventory=[100])
    free_bytes = iter([100_000_000, 100_000_000, 32_000_000])
    monkeypatch.setattr(
        epoch_v2.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=next(free_bytes)),
    )

    result = run(
        root,
        make_plan(
            root,
            next_start=100,
            selection_count=1,
            minimum_free_disk_bytes=1,
        ),
        client,
    )

    selection = json.loads((root / "selection-plan.json").read_text())
    assert [method for method, _ in client.calls] == ["getSlot", "getBlocks"]
    assert result["status"] == "body_budget_exhausted"
    assert result["reason"] == "minimum_free_disk_floor_before_request"
    assert result["advance_to"] == 101
    assert result["selected_slots"] == selection["selected_slots"] == [100]
    assert result["selected_denominator"] == 1
    assert result["planned_body_request_count"] == 2
    assert result["attempted_body_request_count"] == 0
    assert result["failed_body_request_count"] == 0
    assert result["not_attempted_body_request_count"] == 2
    assert result["selected_slot_attempted_count"] == 0
    assert result["selected_slot_failed_count"] == 0
    assert result["selected_slot_not_attempted_count"] == 1


def test_unresolved_or_normalization_failure_is_a_sampled_gap(tmp_path: Path, monkeypatch) -> None:
    original = epoch_v2._pump_invocations
    for index, injected in enumerate(
        [
            {"unresolved_instruction_shapes": 1},
            {"compiled_transaction_normalization_failures": {"fixture": 1}},
        ]
    ):
        root = tmp_path / f"structural-gap-{index}"

        def inject_failure(
            block: dict[str, Any], fields: dict[str, Any] = injected
        ) -> dict[str, Any]:
            result = original(block)
            result.update(fields)
            return result

        monkeypatch.setattr(epoch_v2, "_pump_invocations", inject_failure)
        result = run(
            root,
            make_plan(root, next_start=100, selection_count=1),
            FakeRPC(anchors=[100], inventory=[100]),
        )

        assert result["status"] == "sampled_gaps"
        check = result["structural_checks"][0]
        assert check["structural_references"] is not None
        assert check["status"] == "self_check_failed_or_unknown"
    monkeypatch.setattr(epoch_v2, "_pump_invocations", original)


def test_scheduler_bootstrap_restart_frontier_and_campaign_caps(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "schedule"
    first_bucket = datetime(2026, 10, 10, 12, 10, tzinfo=UTC)
    client = FakeRPC(anchors=[100])
    args = dict(
        schedule_id="v2-test",
        interval_hours=1,
        frame_width_cap=10,
        selection_count=2,
        seed_base="fixed-schedule-seed",
        request_budget_per_epoch=8,
        raw_byte_budget_per_epoch=128_000_000,
        stored_byte_budget_per_epoch=128_000_000,
        max_total_requests=20,
        max_total_raw_bytes=1_000_000_000,
        max_total_stored_bytes=1_000_000_000,
        now=first_bucket,
        clients={"helius-free": client},
    )
    original_replace = schedule_v2._replace

    def crash_frontier(path: Path, row: dict[str, Any]) -> None:
        if path.name == "frontier-v2.json":
            raise OSError("fixture_stop_after_epoch_summary")
        original_replace(path, row)

    monkeypatch.setattr(schedule_v2, "_replace", crash_frontier)
    with pytest.raises(OSError, match="fixture_stop_after_epoch_summary"):
        schedule_v2.run_bucket(root, **args)
    assert len(client.calls) == 1

    monkeypatch.setattr(schedule_v2, "_replace", original_replace)
    resumed = schedule_v2.run_bucket(root, **args)
    assert resumed["epoch"]["status"] == "bootstrap_complete"
    assert resumed["frontier"]["next_start"] == 101
    assert len(client.calls) == 1
    schedule_manifest = json.loads((root / "schedule-v2.json").read_text())
    epoch_plan = json.loads((root / "epochs" / "20261010T120000Z" / "epoch-plan.json").read_text())
    assert schedule_manifest["scheduler_source_version"].endswith("@2")
    assert len(schedule_manifest["scheduler_source_sha256"]) == 64
    assert (
        epoch_plan["schedule_sha256"]
        == hashlib.sha256(canonical(schedule_manifest).encode()).hexdigest()
    )

    next_args = dict(
        args,
        now=first_bucket + timedelta(hours=1),
        clients={"helius-free": FakeRPC(anchors=[102], inventory=[101, 102])},
    )
    next_result = schedule_v2.run_bucket(root, **next_args)
    assert next_result["epoch"]["status"] == "frame_complete"
    assert next_result["frontier"]["next_start"] == 103

    capped_root = tmp_path / "capped-schedule"
    capped_client = FakeRPC(anchors=[100])
    capped_args = dict(
        args,
        clients={"helius-free": capped_client},
        max_total_requests=1,
        request_budget_per_epoch=1,
    )
    schedule_v2.run_bucket(capped_root, **capped_args)
    after_cap = FakeRPC(anchors=[101])
    stopped = schedule_v2.run_bucket(
        capped_root,
        **dict(
            capped_args, now=first_bucket + timedelta(hours=1), clients={"helius-free": after_cap}
        ),
    )
    assert stopped["status"] == "campaign_stopped"
    assert stopped["reason"] == "campaign_budget_exhausted"
    assert after_cap.calls == []


def test_scheduler_head_is_provenance_but_code_and_config_bindings_stay_strict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "strict-schedule-binding"
    start = datetime(2026, 10, 10, 12, 10, tzinfo=UTC)
    args = dict(
        schedule_id="strict-binding-test",
        interval_hours=1,
        frame_width_cap=10,
        selection_count=2,
        seed_base="strict-binding-seed",
        request_budget_per_epoch=8,
        raw_byte_budget_per_epoch=128_000_000,
        stored_byte_budget_per_epoch=128_000_000,
        max_total_requests=20,
        max_total_raw_bytes=1_000_000_000,
        max_total_stored_bytes=1_000_000_000,
    )
    first_client = FakeRPC(anchors=[100])
    schedule_v2.run_bucket(root, **args, now=start, clients={"helius-free": first_client})
    assert len(first_client.calls) == 1

    real_run = schedule_v2.subprocess.run

    def moved_head(command: list[str], **kwargs: Any) -> Any:
        if command == ["git", "rev-parse", "HEAD"]:
            return SimpleNamespace(stdout="docs-only-later-head\n")
        return real_run(command, **kwargs)

    monkeypatch.setattr(schedule_v2.subprocess, "run", moved_head)
    no_rpc = FakeRPC()
    resumed = schedule_v2.run_bucket(root, **args, now=start, clients={"helius-free": no_rpc})
    assert resumed["epoch"]["status"] == "bootstrap_complete"
    assert no_rpc.calls == []

    changed_config_client = FakeRPC()
    with pytest.raises(ValueError, match="schedule_binding_changed"):
        schedule_v2.run_bucket(
            root,
            **dict(args, selection_count=3),
            now=start + timedelta(hours=1),
            clients={"helius-free": changed_config_client},
        )
    assert changed_config_client.calls == []

    original_bindings = schedule_v2._source_bindings

    def changed_code_bindings() -> dict[str, str]:
        bindings = original_bindings()
        bindings["tools/brain_senses_block_epoch_v2.py"] = "0" * 64
        return bindings

    monkeypatch.setattr(schedule_v2, "_source_bindings", changed_code_bindings)
    changed_source_client = FakeRPC()
    with pytest.raises(ValueError, match="schedule_binding_changed"):
        schedule_v2.run_bucket(
            root,
            **args,
            now=start + timedelta(hours=1),
            clients={"helius-free": changed_source_client},
        )
    assert changed_source_client.calls == []


def test_completed_epoch_recovery_returns_saved_summary_without_rpc(tmp_path: Path) -> None:
    root = tmp_path / "completed-epoch-recovery"
    start = datetime(2026, 10, 10, 12, 10, tzinfo=UTC)
    args = dict(
        schedule_id="completed-recovery-test",
        interval_hours=1,
        frame_width_cap=10,
        selection_count=1,
        seed_base="completed-recovery-seed",
        request_budget_per_epoch=8,
        raw_byte_budget_per_epoch=128_000_000,
        stored_byte_budget_per_epoch=128_000_000,
        max_total_requests=20,
        max_total_raw_bytes=1_000_000_000,
        max_total_stored_bytes=1_000_000_000,
    )
    schedule_v2.run_bucket(
        root,
        **args,
        now=start,
        clients={"helius-free": FakeRPC(anchors=[100])},
    )
    frame_client = FakeRPC(anchors=[105], inventory=[101, 102, 103, 104, 105])
    first = schedule_v2.run_bucket(
        root,
        **args,
        now=start + timedelta(hours=1),
        clients={"helius-free": frame_client},
    )
    assert first["epoch"]["status"] == "frame_complete"
    assert len(frame_client.calls) == 4

    class NoRPC:
        calls = 0

        def fetch(self, *_: Any) -> Capture:
            self.calls += 1
            raise AssertionError("completed epoch attempted another RPC")

    no_rpc = NoRPC()
    recovered = schedule_v2.run_bucket(
        root,
        **args,
        now=start + timedelta(hours=1),
        clients={"helius-free": no_rpc},
    )
    assert recovered["epoch"] == first["epoch"]
    assert no_rpc.calls == 0


def test_campaign_counts_outstanding_response_reservations_and_schedule_lock(
    tmp_path: Path,
) -> None:
    root = tmp_path / "reserved-byte-cap"
    start = datetime(2026, 10, 12, 7, 15, tzinfo=UTC)
    common = dict(
        schedule_id="reservation-cap-test",
        interval_hours=1,
        frame_width_cap=10,
        selection_count=1,
        seed_base="reservation-cap-seed",
        request_budget_per_epoch=4,
        raw_byte_budget_per_epoch=epoch_v2.MAX_RESPONSE_BYTES + 1,
        stored_byte_budget_per_epoch=epoch_v2.MAX_RESPONSE_BYTES + 1,
        max_total_requests=20,
        max_total_raw_bytes=epoch_v2.MAX_RESPONSE_BYTES + 1,
        max_total_stored_bytes=epoch_v2.MAX_RESPONSE_BYTES + 1,
    )
    first = FakeRPC()

    def ambiguous_anchor(method: str, params: list[Any]) -> Capture:
        first.calls.append((method, params))
        raise KeyboardInterrupt

    first.fetch = ambiguous_anchor  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        schedule_v2.run_bucket(root, **common, now=start, clients={"helius-free": first})
    reservation = json.loads(
        (root / "epochs" / "20261012T070000Z" / "requests" / "000000000001.json").read_text()
    )
    assert reservation["reserved_response_bytes"] == epoch_v2.MAX_RESPONSE_BYTES

    next_client = FakeRPC(anchors=[101])
    result = schedule_v2.run_bucket(
        root,
        **common,
        now=start + timedelta(hours=1),
        clients={"helius-free": next_client},
    )
    assert result["status"] == "campaign_stopped"
    assert result["reason"] == "campaign_cannot_reserve_one_response"
    assert next_client.calls == []

    locked_root = tmp_path / "locked-schedule"
    locked_root.mkdir()
    lock_path = locked_root / ".schedule-v2.lock"
    with lock_path.open("a") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked_client = FakeRPC()
        with pytest.raises(ValueError, match="inventory_first_schedule_already_active"):
            schedule_v2.run_bucket(
                locked_root,
                **common,
                now=start,
                clients={"helius-free": locked_client},
            )
        assert locked_client.calls == []
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def test_received_but_undurable_response_is_accounted_and_never_replayed(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "undurable-response"
    bucket_time = datetime(2026, 10, 13, 9, 15, tzinfo=UTC)
    args = dict(
        schedule_id="undurable-response-test",
        interval_hours=1,
        frame_width_cap=10,
        selection_count=1,
        seed_base="undurable-response-seed",
        request_budget_per_epoch=4,
        raw_byte_budget_per_epoch=128_000_000,
        stored_byte_budget_per_epoch=128_000_000,
        max_total_requests=10,
        max_total_raw_bytes=1_000_000_000,
        max_total_stored_bytes=1_000_000_000,
        now=bucket_time,
    )
    first_client = FakeRPC(anchors=[100])

    def fail_persistence(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("fixture_storage_failure")

    monkeypatch.setattr(epoch_v2, "_durable_capture", fail_persistence)
    first_result = schedule_v2.run_bucket(root, **args, clients={"helius-free": first_client})

    epoch_root = root / "epochs" / "20261013T090000Z"
    received = json.loads((epoch_root / "request-results" / "000000000001.json").read_text())
    usage = schedule_v2._usage(root / "epochs")
    assert first_result["epoch"]["status"] == "anchor_failed"
    assert received["status"] == "received_persistence_failed"
    assert received["capture_persistence_status"] == "failed"
    assert received["response_bytes"] > 0
    assert received["stored_response_bytes"] is None
    assert received["request_started_ns"] < received["receipt_ns"]
    assert received["receipt_ns"] == received["availability_ns"]
    assert usage["raw_bytes"] == received["response_bytes"]
    assert usage["stored_bytes"] == 0
    assert usage["reserved_stored_bytes"] == epoch_v2.MAX_RESPONSE_BYTES

    retry_client = FakeRPC(anchors=[101])
    resumed = schedule_v2.run_bucket(root, **args, clients={"helius-free": retry_client})
    assert resumed["epoch"]["status"] == "anchor_failed"
    assert retry_client.calls == []


def test_failed_anchor_keeps_frontier_and_next_epoch_exposes_accumulated_frame(
    tmp_path: Path,
) -> None:
    root = tmp_path / "failed-anchor-schedule"
    start = datetime(2026, 10, 11, 8, 15, tzinfo=UTC)
    common = dict(
        schedule_id="anchor-gap-test",
        interval_hours=1,
        frame_width_cap=10,
        selection_count=2,
        seed_base="anchor-gap-seed",
        request_budget_per_epoch=8,
        raw_byte_budget_per_epoch=128_000_000,
        stored_byte_budget_per_epoch=128_000_000,
        max_total_requests=100,
        max_total_raw_bytes=2_000_000_000,
        max_total_stored_bytes=2_000_000_000,
    )
    bootstrap = schedule_v2.run_bucket(
        root,
        **common,
        now=start,
        clients={"helius-free": FakeRPC(anchors=[100])},
    )
    assert bootstrap["frontier"]["next_start"] == 101
    assert (
        bootstrap["frontier"]["bootstrap_receipt_ns"] == bootstrap["epoch"]["bootstrap_receipt_ns"]
    )

    failed = schedule_v2.run_bucket(
        root,
        **common,
        now=start + timedelta(hours=1),
        clients={"helius-free": FakeRPC(fail_method="getSlot")},
    )
    assert failed["epoch"]["status"] == "anchor_failed"
    assert failed["frontier"]["next_start"] == 101

    accumulated_client = FakeRPC(anchors=[105], inventory=[101, 103, 104])
    accumulated = schedule_v2.run_bucket(
        root,
        **common,
        now=start + timedelta(hours=2),
        clients={"helius-free": accumulated_client},
    )
    assert accumulated_client.calls[1] == (
        "getBlocks",
        [101, 105, {"commitment": "finalized"}],
    )
    assert accumulated["frontier"]["next_start"] == 106


def _v3_lineage(path: Path, *, bucket: str = "20261010T010000Z", anchor: int = 100) -> Path:
    lineage = {
        "schema_version": 1,
        "provider": "helius-free",
        "campaign_id": "helius-inventory-v3-hourly-20261010",
        "controller_lease_expires_ns": 2_000_000_000_000_000_000,
        "predecessor_campaign_id": "frozen-v2-fixture",
        "last_attempt_bucket": bucket,
        "last_observed_anchor_slot": anchor,
        "last_observed_anchor_receipt_ns": 1_800_000_000_000_000_000,
        "last_inventoried_interval": {
            "start": 91,
            "end_exclusive": 101,
            "provider_returned_produced_slots": 9,
        },
        "gap_intervals": [
            {"start": 81, "end_exclusive": 91, "reason": "predecessor_oversized_frame"}
        ],
    }
    path.write_text(json.dumps(lineage, sort_keys=True))
    return path


def test_v3_preflight_exact_boundaries_and_one_unit_short() -> None:
    exact = dict(
        attempts=23,
        epoch_dirs=24,
        requests=schedule_v3.REQUESTS_MAX - schedule_v3.REQUESTS_PER_FRAME,
        raw=schedule_v3.CAMPAIGN_RAW_BYTES - schedule_v3.FRAME_RAW_BYTES,
        stored=schedule_v3.CAMPAIGN_STORED_BYTES - schedule_v3.FRAME_STORED_BYTES,
        reserved_raw=0,
        reserved_stored=0,
        free_disk=(
            schedule_v3.DISK_FLOOR_BYTES
            + schedule_v3.FRAME_RAW_BYTES
            + schedule_v3.FRAME_STORED_BYTES
            + schedule_v3.PEAK_WRITE_MARGIN_BYTES
        ),
    )
    assert schedule_v3._preflight(**exact) is None

    boundaries = [
        ("requests", "campaign_cannot_reserve_full_frame_requests"),
        ("raw", "campaign_cannot_reserve_full_frame_raw_bytes"),
        ("stored", "campaign_cannot_reserve_full_frame_stored_bytes"),
        ("free_disk", "minimum_free_disk_floor_plus_frame_envelope"),
    ]
    for dimension, expected_reason in boundaries:
        short = dict(exact)
        short[dimension] = (
            short[dimension] - 1 if dimension == "free_disk" else short[dimension] + 1
        )
        assert schedule_v3._preflight(**short) == expected_reason

    assert schedule_v3._preflight(**{**exact, "attempts": 24}) == "attempt_or_epoch_cap"
    assert schedule_v3._preflight(**{**exact, "epoch_dirs": 25}) == "attempt_or_epoch_cap"

    observed_raw = 71_939_096
    observed_stored = 15_335_264
    for attempt in range(schedule_v3.ATTEMPTS_MAX):
        replay = dict(
            attempts=attempt,
            epoch_dirs=attempt + 1,
            requests=attempt * schedule_v3.REQUESTS_PER_FRAME,
            raw=attempt * observed_raw,
            stored=attempt * observed_stored,
            reserved_raw=0,
            reserved_stored=0,
            free_disk=exact["free_disk"],
        )
        assert schedule_v3._preflight(**replay) is None
    assert schedule_v3.ATTEMPTS_MAX * observed_raw == 1_726_538_304
    assert schedule_v3.ATTEMPTS_MAX * observed_stored == 368_046_336


def test_v3_missed_hour_is_anchor_only_and_never_backfilled(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "v3"
    lineage_path = _v3_lineage(tmp_path / "lineage.json")
    monkeypatch.setattr(
        schedule_v3.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(
            free=(
                schedule_v3.DISK_FLOOR_BYTES
                + schedule_v3.FRAME_RAW_BYTES
                + schedule_v3.FRAME_STORED_BYTES
                + schedule_v3.PEAK_WRITE_MARGIN_BYTES
            )
        ),
    )
    current = datetime(2026, 10, 10, 3, 5, tzinfo=UTC)
    client = FakeRPC(anchors=[103])
    result = schedule_v3.run_bucket(
        root, lineage_path=lineage_path, now=current, clients={"helius-free": client}
    )
    service_policy = json.loads((root / "schedule-v3.json").read_text())["service_policy"]
    assert service_policy["run_at_load"] is False
    assert service_policy["keep_alive"] is False
    assert (
        service_policy["renewal"]
        == "finite controller may create a new unique campaign under its lease"
    )
    assert result["status"] == "campaign_stopped"
    assert result["reason"] == "missed_schedule_gap_requires_review"
    assert result["epoch"]["status"] == "bootstrap_complete"
    assert [method for method, _ in client.calls] == ["getSlot"]
    context = json.loads(
        (root / "epochs" / "20261010T030000Z" / "attempt-context.json").read_text()
    )
    plan = json.loads((root / "epochs" / "20261010T030000Z" / "epoch-plan.json").read_text())
    assert context["missed_schedule_buckets"] == ["20261010T020000Z"]
    assert context["missed_slot_backfill"] is False
    assert plan["bootstrap"] is True
    assert (root / "epochs" / "20261010T030000Z" / "frame-reservation.json").exists()
    assert (root / "epochs" / "20261010T030000Z" / "frame-reservation-release.json").exists()
    assert result["frontier"]["gap_intervals"][-1] == {
        "start": 101,
        "end_exclusive": 104,
        "reason": "missed_schedule_no_backfill",
    }
    status = schedule_v3.read_status(root)
    assert status["attempts"] == 1
    assert status["successful_epochs"] == 1
    assert status["gapped_epochs"] == 1
    assert status["requests"] == 1
    assert status["provider_charge_units"] == "unknown_unverified"

    # Repeated launchd delivery for the same bucket returns the sealed result without RPC.
    repeated = schedule_v3.run_bucket(
        root, lineage_path=lineage_path, now=current, clients={"helius-free": client}
    )
    assert repeated["status"] == "campaign_stopped"
    assert len(client.calls) == 1

    next_client = FakeRPC(anchors=[105])
    next_result = schedule_v3.run_bucket(
        root,
        lineage_path=lineage_path,
        now=datetime(2026, 10, 10, 4, 2, tzinfo=UTC),
        clients={"helius-free": next_client},
    )
    assert next_result["status"] == "campaign_stopped"
    assert next_result["reason"] == "missed_schedule_gap_requires_review"
    assert next_client.calls == []


def test_v3_24th_attempt_seals_terminal_stop_and_never_calls_provider_again(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "v3-finite"
    lineage_path = _v3_lineage(tmp_path / "lineage-finite.json")
    monkeypatch.setattr(schedule_v3, "ATTEMPTS_MAX", 1)
    monkeypatch.setattr(schedule_v3, "EPOCH_DIRS_MAX", 2)
    monkeypatch.setattr(
        schedule_v3.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(
            free=(
                schedule_v3.DISK_FLOOR_BYTES
                + schedule_v3.FRAME_RAW_BYTES
                + schedule_v3.FRAME_STORED_BYTES
                + schedule_v3.PEAK_WRITE_MARGIN_BYTES
            )
        ),
    )
    client = FakeRPC(anchors=[101])
    result = schedule_v3.run_bucket(
        root,
        lineage_path=lineage_path,
        now=datetime(2026, 10, 10, 2, 5, tzinfo=UTC),
        clients={"helius-free": client},
    )
    assert result["status"] == "campaign_stopped"
    stop = json.loads((root / "STOP").read_text())
    assert stop["reason"] == "finite_attempt_cap_reached"
    assert stop["campaign_id"] == "helius-inventory-v3-hourly-20261010"
    assert len(stop["manifest_sha256"]) == 64
    assert len(stop["epoch_summary_sha256"]) == 64
    assert len(stop["frame_reservation_sha256"]) == 64
    assert stop["attempt_count"] == 1
    assert 1 <= stop["requests"] <= schedule_v3.REQUESTS_PER_FRAME
    assert stop["observed_anchor_slot"] == 101
    assert stop["request_started"] is True
    calls_at_stop = len(client.calls)
    again = schedule_v3.run_bucket(
        root,
        lineage_path=lineage_path,
        now=datetime(2026, 10, 10, 3, 5, tzinfo=UTC),
        clients={"helius-free": client},
    )
    assert again["status"] == "campaign_stopped"
    assert len(client.calls) == calls_at_stop


def test_v3_crash_after_frontier_commit_reconciles_offline_then_stops(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "v3-crash-before-stop"
    lineage_path = _v3_lineage(tmp_path / "lineage-crash-before-stop.json")
    monkeypatch.setattr(schedule_v3, "ATTEMPTS_MAX", 1)
    monkeypatch.setattr(schedule_v3, "EPOCH_DIRS_MAX", 2)
    monkeypatch.setattr(
        schedule_v3.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(
            free=(
                schedule_v3.DISK_FLOOR_BYTES
                + schedule_v3.FRAME_RAW_BYTES
                + schedule_v3.FRAME_STORED_BYTES
                + schedule_v3.PEAK_WRITE_MARGIN_BYTES
            )
        ),
    )
    seal_stop = schedule_v3._seal_stop

    def crash_before_stop(path: Path, reason: str, **details: Any) -> dict[str, Any]:
        if reason == "finite_attempt_cap_reached":
            raise SystemExit("simulated_crash_before_stop_fsync")
        return seal_stop(path, reason, **details)

    monkeypatch.setattr(schedule_v3, "_seal_stop", crash_before_stop)
    client = FakeRPC(anchors=[101])
    with pytest.raises(SystemExit, match="before_stop_fsync"):
        schedule_v3.run_bucket(
            root,
            lineage_path=lineage_path,
            now=datetime(2026, 10, 10, 2, 5, tzinfo=UTC),
            clients={"helius-free": client},
        )
    calls_at_crash = len(client.calls)
    assert not (root / "STOP").exists()
    assert (root / "invocation-in-progress.json").exists()

    monkeypatch.setattr(schedule_v3, "_seal_stop", seal_stop)
    recovered = schedule_v3.run_bucket(
        root,
        lineage_path=lineage_path,
        now=datetime(2026, 10, 10, 3, 5, tzinfo=UTC),
        clients={"helius-free": client},
    )
    assert recovered["status"] == "campaign_stopped"
    assert recovered["reason"] == "process_interrupted_without_terminal_stop"
    assert recovered["local_reconciliation"] == "sealed_epoch_reconciled"
    assert recovered["attempt_count"] == 1
    assert len(client.calls) == calls_at_crash


def test_v3_completes_exactly_24_normal_attempts_then_seals_stop(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "v3-24-attempts"
    lineage_path = _v3_lineage(tmp_path / "lineage-24-attempts.json")
    monkeypatch.setattr(
        schedule_v3.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(
            free=(
                schedule_v3.DISK_FLOOR_BYTES
                + schedule_v3.FRAME_RAW_BYTES
                + schedule_v3.FRAME_STORED_BYTES
                + schedule_v3.PEAK_WRITE_MARGIN_BYTES
            )
        ),
    )
    calls_total = 0
    start = datetime(2026, 10, 10, 2, 5, tzinfo=UTC)
    for offset in range(schedule_v3.ATTEMPTS_MAX):
        client = FakeRPC(anchors=[101 + offset])
        result = schedule_v3.run_bucket(
            root,
            lineage_path=lineage_path,
            now=start + timedelta(hours=offset),
            clients={"helius-free": client},
        )
        calls_total += len(client.calls)
        if offset < schedule_v3.ATTEMPTS_MAX - 1:
            assert result["status"] == "epoch_finished"
        else:
            assert result["status"] == "campaign_stopped"
            assert result["reason"] == "finite_attempt_cap_reached"
            assert result["attempt_count"] == 24
    assert calls_total > 24
    calls_at_stop = calls_total
    final = schedule_v3.run_bucket(
        root,
        lineage_path=lineage_path,
        now=start + timedelta(hours=24),
        clients={"helius-free": FakeRPC(anchors=[125])},
    )
    assert final["status"] == "campaign_stopped"
    assert len(list((root / "epochs").glob("20*T*Z"))) == 24
    assert calls_total == calls_at_stop


def test_v3_preflight_failure_stops_before_epoch_or_rpc(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "v3-preflight"
    lineage_path = _v3_lineage(tmp_path / "lineage-preflight.json")
    monkeypatch.setattr(
        schedule_v3.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(
            free=(
                schedule_v3.DISK_FLOOR_BYTES
                + schedule_v3.FRAME_RAW_BYTES
                + schedule_v3.FRAME_STORED_BYTES
                + schedule_v3.PEAK_WRITE_MARGIN_BYTES
                - 1
            )
        ),
    )
    client = FakeRPC(anchors=[101])
    result = schedule_v3.run_bucket(
        root,
        lineage_path=lineage_path,
        now=datetime(2026, 10, 10, 2, 5, tzinfo=UTC),
        clients={"helius-free": client},
    )
    assert result["status"] == "preflight_stopped_without_epoch_or_rpc"
    assert result["reason"] == "minimum_free_disk_floor_plus_frame_envelope"
    assert client.calls == []
    assert not (root / "epochs" / "20261010T020000Z").exists()
    assert json.loads((root / "STOP").read_text())["epoch_created"] is False


def test_v3_stop_publication_is_atomic_and_immutable(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "v3-atomic-stop"
    root.mkdir(mode=0o700)
    stop_path = root / "STOP"

    def fail_publish(*args: Any, **kwargs: Any) -> None:
        raise OSError("simulated_stop_link_failure")

    monkeypatch.setattr(schedule_v3.os, "link", fail_publish)
    with pytest.raises(OSError, match="simulated_stop_link_failure"):
        schedule_v3._create_stop_atomically(stop_path, {"reason": "atomic_fixture"})
    assert not stop_path.exists()
    assert list(root.glob(".STOP.*.tmp")) == []

    monkeypatch.undo()
    first = schedule_v3._seal_stop(root, "first_reason")
    second = schedule_v3._seal_stop(root, "second_reason")
    assert first == second
    assert json.loads(stop_path.read_text()) == first
    assert stop_path.stat().st_mode & 0o777 == 0o600


def test_v3_terminal_transport_failure_is_never_replayed(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "v3-failed"
    lineage_path = _v3_lineage(tmp_path / "lineage-failed.json")
    monkeypatch.setattr(
        schedule_v3.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(
            free=(
                schedule_v3.DISK_FLOOR_BYTES
                + schedule_v3.FRAME_RAW_BYTES
                + schedule_v3.FRAME_STORED_BYTES
                + schedule_v3.PEAK_WRITE_MARGIN_BYTES
            )
        ),
    )
    client = FakeRPC(anchors=[101], fail_method="getSlot")
    now = datetime(2026, 10, 10, 2, 5, tzinfo=UTC)
    result = schedule_v3.run_bucket(
        root, lineage_path=lineage_path, now=now, clients={"helius-free": client}
    )
    assert result["status"] == "campaign_stopped"
    assert result["epoch"]["status"] == "anchor_failed"
    assert [method for method, _ in client.calls] == ["getSlot"]
    stopped = schedule_v3.run_bucket(
        root, lineage_path=lineage_path, now=now, clients={"helius-free": client}
    )
    assert stopped["status"] == "campaign_stopped"
    assert len(client.calls) == 1


def test_v3_crash_after_durable_request_never_replays_ambiguous_rpc(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "v3-crash"
    lineage_path = _v3_lineage(tmp_path / "lineage-crash.json")
    monkeypatch.setattr(
        schedule_v3.shutil,
        "disk_usage",
        lambda _: SimpleNamespace(
            free=(
                schedule_v3.DISK_FLOOR_BYTES
                + schedule_v3.FRAME_RAW_BYTES
                + schedule_v3.FRAME_STORED_BYTES
                + schedule_v3.PEAK_WRITE_MARGIN_BYTES
            )
        ),
    )

    class CrashAfterReservation:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def fetch(self, method: str, params: list[Any]) -> Capture:
            self.calls.append(method)
            raise SystemExit("simulated_process_death_after_request_reservation")

        def close(self) -> None:
            return

    now = datetime(2026, 10, 10, 2, 5, tzinfo=UTC)
    crashed = CrashAfterReservation()
    with pytest.raises(SystemExit, match="simulated_process_death"):
        schedule_v3.run_bucket(
            root,
            lineage_path=lineage_path,
            now=now,
            clients={"helius-free": crashed},
        )
    request_path = root / "epochs" / "20261010T020000Z" / "requests" / "000000000001.json"
    assert request_path.exists()
    assert crashed.calls == ["getSlot"]

    replacement = FakeRPC(anchors=[101])
    result = schedule_v3.run_bucket(
        root,
        lineage_path=lineage_path,
        now=now,
        clients={"helius-free": replacement},
    )
    assert result["status"] == "campaign_stopped"
    assert result["reason"] == "process_interrupted_without_terminal_stop"
    assert result["recovered_without_rpc"] is True
    assert result["local_reconciliation"] in {"failed_closed", "journal_valid_but_epoch_unsealed"}
    assert replacement.calls == []
    assert json.loads((root / "active-frame-reservation.json").read_text())["status"] == "active"
    assert not (root / "epochs" / "20261010T020000Z" / "frame-reservation-release.json").exists()


def test_v3_launch_wrapper_boots_out_only_after_stop_and_retries_without_scheduler(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "launch"
    root.mkdir()
    lineage = tmp_path / "lineage.json"
    lineage.write_text("{}")
    calls: list[list[str]] = []

    def success_runner(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    (root / "STOP").write_text(json.dumps({"reason": "fixture_stop"}))
    assert launch_v3.run(root, lineage, "fixture.label", runner=success_runner) == 0
    assert calls == [["/bin/launchctl", "bootout", f"gui/{os.getuid()}/fixture.label"]]

    calls.clear()

    def failed_bootout(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=5)

    assert launch_v3.run(root, lineage, "fixture.label", runner=failed_bootout) == 1
    assert len(calls) == 1
    assert calls[0][1] == "bootout"
    assert "retry teardown without RPC" in capsys.readouterr().err


def test_v3_launch_wrapper_runs_without_secret_args_then_boots_out_on_new_stop(
    tmp_path: Path,
) -> None:
    root = tmp_path / "launch-fresh"
    root.mkdir()
    lineage = tmp_path / "lineage-fresh.json"
    calls: list[list[str]] = []
    scheduler_calls: list[tuple[Path, Path]] = []

    def scheduler_stops_then_bootout(path: Path, *, lineage_path: Path):
        scheduler_calls.append((path, lineage_path))
        (root / "STOP").write_text(json.dumps({"reason": "fixture_stop"}))

    def bootout_only(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    assert (
        launch_v3.run(
            root,
            lineage,
            "fixture.label",
            runner=bootout_only,
            scheduler=scheduler_stops_then_bootout,
        )
        == 0
    )
    assert scheduler_calls == [(root, lineage)]
    assert len(calls) == 1
    assert calls[0][1] == "bootout"


def test_scheduler_cli_help_and_explicit_configuration(monkeypatch, capsys, tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as help_exit:
        schedule_v2.main(["--help"])
    assert help_exit.value.code == 0
    help_output = capsys.readouterr().out
    assert "--root-base" in help_output
    assert "--minimum-free-disk-bytes" in help_output
    assert "--capture-storage-codec" in help_output
    assert "--max-total-stored-bytes" in help_output

    stopped = {"status": "campaign_stopped", "reason": "fixture_budget_stop"}
    observed: dict[str, Any] = {}

    def fake_run_bucket(root_base: Path, **kwargs: Any) -> dict[str, Any]:
        observed["root_base"] = root_base
        observed.update(kwargs)
        return stopped

    monkeypatch.setattr(schedule_v2, "run_bucket", fake_run_bucket)
    argv = [
        "--root-base",
        str(tmp_path / "campaign"),
        "--schedule-id",
        "cli-fixture",
        "--now",
        "2026-10-14T10:30:00+03:00",
        "--interval-hours",
        "2",
        "--frame-width-cap",
        "20",
        "--selection-count",
        "4",
        "--seed-base",
        "cli-seed",
        "--request-budget-per-epoch",
        "12",
        "--raw-byte-budget-per-epoch",
        "64000000",
        "--stored-byte-budget-per-epoch",
        "64000000",
        "--max-total-requests",
        "120",
        "--max-total-raw-bytes",
        "640000000",
        "--max-total-stored-bytes",
        "640000000",
        "--max-epochs",
        "24",
        "--minimum-free-disk-bytes",
        "32000000",
        "--capture-storage-codec",
        "raw",
    ]
    exit_code = schedule_v2.main(argv)
    assert exit_code == 0
    assert observed == {
        "root_base": tmp_path / "campaign",
        "schedule_id": "cli-fixture",
        "now": datetime(2026, 10, 14, 7, 30, tzinfo=UTC),
        "interval_hours": 2,
        "frame_width_cap": 20,
        "selection_count": 4,
        "seed_base": "cli-seed",
        "request_budget_per_epoch": 12,
        "raw_byte_budget_per_epoch": 64_000_000,
        "stored_byte_budget_per_epoch": 64_000_000,
        "max_total_requests": 120,
        "max_total_raw_bytes": 640_000_000,
        "max_total_stored_bytes": 640_000_000,
        "max_epochs": 24,
        "minimum_free_disk_bytes": 32_000_000,
        "capture_storage_codec": "raw",
        "clients": None,
    }
    assert capsys.readouterr().out == canonical(stopped) + "\n"

    observed.clear()
    exit_code = schedule_v2.main(argv[:4] + argv[6:])
    assert exit_code == 0
    assert observed["now"] is None
    assert capsys.readouterr().out == canonical(stopped) + "\n"
