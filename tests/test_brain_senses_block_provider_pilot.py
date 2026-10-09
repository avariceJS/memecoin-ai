from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from memecoin_ai.domain import canonical
from memecoin_ai.sources import Capture, HTTPSourceError
from tools.brain_senses_block_epoch import start_epoch
from tools.brain_senses_block_provider_audit import audit
from tools.brain_senses_block_provider_pilot import (
    MAX_RESPONSE_BYTES,
    _build_operations,
    _structural_response_validation,
    _transaction_comparison,
    run_once,
)


class FakeBlockRPC:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls = 0
        self.fail_first = fail_first

    def fetch(self, method: str, params: list[object]) -> Capture:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise TimeoutError("fixture timeout")
        if method == "getBlocks":
            value: Any = list(range(int(params[0]), int(params[1]) + 1))
        else:
            slot = int(params[0])
            detail = params[1]["transactionDetails"]
            signatures = [f"sig-{slot}-0", f"sig-{slot}-1"]
            if detail == "signatures":
                value = dict(
                    blockhash=f"hash-{slot}",
                    parentSlot=slot - 1,
                    previousBlockhash=f"hash-{slot - 1}",
                    signatures=signatures,
                )
            else:
                value = dict(
                    blockhash=f"hash-{slot}",
                    parentSlot=slot - 1,
                    previousBlockhash=f"hash-{slot - 1}",
                    transactions=[
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
                )
        now = time.time_ns()
        request = canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        response = canonical({"jsonrpc": "2.0", "id": 1, "result": value}).encode()
        return Capture("fixture", method, request, now, now, response)

    def close(self) -> None:
        return


class FakeEpochRPC(FakeBlockRPC):
    def fetch(self, method: str, params: list[object]) -> Capture:
        if method != "getSlot":
            return super().fetch(method, params)
        self.calls += 1
        now = time.time_ns()
        request = canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        response = canonical({"jsonrpc": "2.0", "id": 1, "result": 100}).encode()
        return Capture("fixture", method, request, now, now, response)


def test_64_slot_hourly_window_fits_full_and_signature_request_budget() -> None:
    operations = _build_operations(
        [[1_000, 1_064]],
        signature_sample_slots=set(range(1_000, 1_064)),
        providers=("helius-free",),
    )

    assert len(operations) == 129
    assert sum(operation["method"] == "getBlock" for operation in operations) == 128
    assert sum(operation["method"] == "getBlocks" for operation in operations) == 1


def test_full_sample_subset_keeps_single_hour_inventory_and_unknown_unsampled_bodies(
    tmp_path: Path,
):
    slots = [100, 102]
    operations = _build_operations(
        [[100, 103]],
        signature_sample_slots=set(slots),
        providers=("helius-free",),
        full_sample_slots=set(slots),
    )
    assert len(operations) == 5
    assert sum(operation["method"] == "getBlocks" for operation in operations) == 1

    result = run_once(
        tmp_path / "sampled",
        slot_ranges=[[100, 103]],
        request_cap=5,
        providers=("helius-free",),
        signature_sample_slots=slots,
        full_sample_slots=slots,
        clients={"helius-free": FakeBlockRPC()},
    )
    window = result["comparison"]["windows"][0]
    rows = {row["slot"]: row for row in window["slots_compared"]}
    assert rows[100]["providers"]["helius-free"]["observation_status"] == (
        "qualified_structural_reference_count"
    )
    assert rows[100]["providers"]["helius-free"]["qualified_structural_reference_count"] == 0
    assert set(window["unselected_produced_slots"]["helius-free"]) == {101}
    assert window["unselected_slot_semantics"].endswith("structural_reference_count_unknown")


def test_same_slot_block_pilot_compares_slot_sets_and_full_transactions(tmp_path: Path) -> None:
    helius = FakeBlockRPC()
    public = FakeBlockRPC()
    result = run_once(
        tmp_path / "pilot",
        slot_ranges=[[100, 102]],
        request_cap=5,
        clients={"helius-free": helius, "solana-public": public},
    )
    assert result["completed"] == 10
    assert result["helius_credit_reservations"] == 5
    assert helius.calls == public.calls == 5
    window = result["comparison"]["windows"][0]
    assert window["enumeration_equal"] is True
    assert all(row["cross_provider_signature_order_equal"] for row in window["slots_compared"])
    assert all(
        provider["full_matches_signatures_only"]
        for row in window["slots_compared"]
        for provider in row["providers"].values()
    )


def test_block_pilot_can_pair_helius_and_alchemy_provider_aliases(tmp_path: Path):
    helius = FakeBlockRPC()
    alchemy = FakeBlockRPC()
    result = run_once(
        tmp_path / "helius-alchemy",
        slot_ranges=[[100, 102]],
        request_cap=5,
        providers=("helius-free", "alchemy-payg"),
        clients={"helius-free": helius, "alchemy-payg": alchemy},
    )

    assert result["completed"] == 10
    assert result["helius_credit_reservations"] == 5
    assert helius.calls == alchemy.calls == 5
    window = result["comparison"]["windows"][0]
    assert window["enumeration_equal"] is True
    assert all(row["cross_provider_signature_order_equal"] for row in window["slots_compared"])


def test_block_pilot_failure_is_accounted_once_and_never_retried(tmp_path: Path) -> None:
    helius = FakeBlockRPC(fail_first=True)
    public = FakeBlockRPC()
    output = tmp_path / "pilot"
    first = run_once(
        output,
        slot_ranges=[[100, 101]],
        request_cap=3,
        clients={"helius-free": helius, "solana-public": public},
    )
    second = run_once(
        output,
        slot_ranges=[[100, 101]],
        request_cap=3,
        clients={"helius-free": helius, "solana-public": public},
    )
    assert first["completed"] == 6
    assert first["outcomes"]["request_failed"] == 1
    assert second["completed"] == 6
    assert helius.calls == public.calls == 3
    assert len(list((output / "requests").glob("*.json"))) == 6


def test_block_pilot_preserves_sanitized_http_status_for_failure(tmp_path: Path) -> None:
    class HTTPFailRPC(FakeBlockRPC):
        def fetch(self, method: str, params: list[object]) -> Capture:
            self.calls += 1
            raise HTTPSourceError(429)

    helius = HTTPFailRPC()
    result = run_once(
        tmp_path / "pilot",
        slot_ranges=[[100, 101]],
        request_cap=3,
        clients={"helius-free": helius, "solana-public": FakeBlockRPC()},
    )

    failure = json.loads((tmp_path / "pilot" / "request-results" / "000000000001.json").read_text())
    assert result["outcomes"]["request_failed"] == 3
    assert failure["http_status"] == 429


def test_helius_daily_credit_cap_stops_before_next_reserved_request(tmp_path: Path) -> None:
    helius = FakeBlockRPC()
    public = FakeBlockRPC()
    output = tmp_path / "pilot"
    result = run_once(
        output,
        slot_ranges=[[100, 102]],
        request_cap=10,
        helius_total_credit_cap=1,
        helius_daily_credit_cap=1,
        clients={"helius-free": helius, "solana-public": public},
    )

    assert result["attempts"] == 1
    assert result["helius_credit_reservations"] == 1
    assert helius.calls == 1
    assert public.calls == 0
    assert json.loads((output / "STOP").read_text())["reason"] == "helius_wave_credit_cap"


def test_captured_byte_cap_stops_before_next_request_reservation(tmp_path: Path) -> None:
    helius = FakeBlockRPC()
    public = FakeBlockRPC()
    output = tmp_path / "pilot"
    result = run_once(
        output,
        slot_ranges=[[100, 102]],
        request_cap=10,
        max_captured_bytes=MAX_RESPONSE_BYTES,
        clients={"helius-free": helius, "solana-public": public},
    )

    assert result["attempts"] == 1
    assert result["captured_response_bytes"] < MAX_RESPONSE_BYTES
    assert helius.calls == 1
    assert public.calls == 0
    assert json.loads((output / "STOP").read_text())["reason"] == (
        "captured_response_byte_cap_before_request"
    )


def test_signature_only_cross_check_is_limited_to_pinned_slots(tmp_path: Path) -> None:
    helius = FakeBlockRPC()
    public = FakeBlockRPC()
    result = run_once(
        tmp_path / "pilot",
        slot_ranges=[[100, 103]],
        request_cap=10,
        signature_sample_slots=[101],
        clients={"helius-free": helius, "solana-public": public},
    )

    assert result["completed"] == 10
    assert helius.calls == public.calls == 5
    rows = result["comparison"]["windows"][0]["slots_compared"]
    assert rows[0]["providers"]["helius-free"]["full_matches_signatures_only"] is None
    assert rows[1]["providers"]["helius-free"]["full_matches_signatures_only"] is True
    assert rows[2]["providers"]["helius-free"]["full_matches_signatures_only"] is None


def test_transaction_comparison_reports_nested_difference_paths() -> None:
    left = {
        "transactions": [{"transaction": {"signatures": ["sig"]}, "meta": {"logMessages": ["one"]}}]
    }
    right = {
        "transactions": [
            {
                "transaction": {"signatures": ["sig"]},
                "meta": {"logMessages": ["one", "Log truncated"]},
            }
        ]
    }

    result = _transaction_comparison(left, right)

    assert result == {
        "status": "paired_transaction_maps",
        "paired": 1,
        "exact": 0,
        "differing": 1,
        "helius_only": 0,
        "public_only": 0,
        "difference_paths": {"meta.logMessages": 1},
    }


def test_structural_response_count_fails_closed_without_inner_instruction_evidence() -> None:
    block = {
        "blockhash": "hash-100",
        "previousBlockhash": "hash-99",
        "parentSlot": 99,
        "transactions": [
            {
                "transaction": {
                    "signatures": ["sig"],
                    "message": {"accountKeys": ["payer"], "instructions": []},
                },
                "version": "legacy",
                "meta": {
                    "err": None,
                    "innerInstructions": None,
                    "loadedAddresses": {"writable": [], "readonly": []},
                },
            }
        ],
    }

    assert "transaction_0_instruction_coverage_missing" in _structural_response_validation(
        block, 100
    )


def test_offline_provider_audit_summarizes_paired_receipts(tmp_path: Path) -> None:
    root = tmp_path / "pilot"
    run_once(
        root,
        slot_ranges=[[100, 102]],
        request_cap=10,
        clients={"helius-free": FakeBlockRPC(), "solana-public": FakeBlockRPC()},
    )

    result = audit(root)

    assert result["audit_mode"] == "offline_only_no_provider_requests"
    assert result["completed_requests"] == 10
    assert result["helius_expected_credit_reservations"] == 5
    assert result["block_comparison"]["paired_block_identities"] == 2
    assert result["block_comparison"]["transaction_comparison"] == {
        "paired_transactions": 4,
        "exact_tree_matches": 4,
        "different_trees": 0,
        "difference_paths": {},
    }


def test_block_epoch_anchors_both_providers_before_new_paired_window(tmp_path: Path) -> None:
    helius = FakeEpochRPC()
    public = FakeEpochRPC()
    old_epoch = tmp_path / "old-epoch"
    (old_epoch / "request-results").mkdir(parents=True)
    (old_epoch / "STOP").write_text('{"reason":"old_epoch_stopped"}\n')
    (old_epoch / "request-results" / "000000000001.json").write_text('{"received_ns":123456789}\n')

    result = start_epoch(
        tmp_path / "epoch",
        window_slots=2,
        old_epoch_root=old_epoch,
        clients={"helius-free": helius, "solana-public": public},
    )

    assert result["status"] == "provider_window_attempted"
    assert result["slot_range"] == [99, 101]
    assert result["block_capture"]["helius_credit_reservations"] == 4
    assert helius.calls == public.calls == 5  # getSlot, getBlocks, two full, one signatures
    assert (
        json.loads((tmp_path / "epoch" / "window-plan.json").read_text())["anchors"]["helius-free"][
            "slot"
        ]
        == 100
    )
    assert result["gap"]["last_receipt_ns_in_old_epoch"] == 123456789


def test_helius_only_window_reproducibly_checks_every_selected_slot(tmp_path: Path) -> None:
    helius = FakeEpochRPC()

    result = start_epoch(
        tmp_path / "helius-only-epoch",
        window_slots=2,
        providers=("helius-free",),
        slots_per_hour_estimate=24,
        sampling_seed="fixed-hour-stratum",
        signature_check_all_slots=True,
        clients={"helius-free": helius},
    )

    assert result["status"] == "provider_window_attempted"
    assert result["slot_range"][1] - result["slot_range"][0] == 2
    assert result["block_capture"]["helius_credit_reservations"] == 5
    assert result["block_capture"]["attempts"] == 5
    assert helius.calls == 6  # finalized anchor, then range + two full + two signatures
    summary = json.loads(
        (tmp_path / "helius-only-epoch" / "paired-block-capture" / "summary.json").read_text()
    )
    assert summary["comparison"]["windows"][0]["enumerated_slot_count"] == 2
    assert all(
        row["providers"]["helius-free"]["full_matches_signatures_only"]
        for row in summary["comparison"]["windows"][0]["slots_compared"]
    )
    single_audit = audit(tmp_path / "helius-only-epoch" / "paired-block-capture")
    assert single_audit["providers"]["helius-free"]["requests"] == 5
    assert (
        single_audit["block_comparison"]["blocks_by_provider"]["helius-free"]["parsed_full_blocks"]
        == 2
    )
    assert single_audit["block_comparison"]["signature_only_cross_checks_equal"] == 2
    assert (
        single_audit["block_comparison"]["inventory_qualification"]["helius-free"][
            "provider_inventory_self_consistent"
        ]
        == 2
    )
    assert all(
        row["providers"]["helius-free"]["structural_reference_qualification"]
        == "exact_over_stored_full_response"
        and row["providers"]["helius-free"]["provider_inventory_self_consistent"]
        for row in summary["comparison"]["windows"][0]["slots_compared"]
    )
    replica = start_epoch(
        tmp_path / "helius-only-epoch-replica",
        window_slots=2,
        providers=("helius-free",),
        slots_per_hour_estimate=24,
        sampling_seed="fixed-hour-stratum",
        signature_check_all_slots=True,
        clients={"helius-free": FakeEpochRPC()},
    )
    assert replica["slot_range"] == result["slot_range"]


def test_helius_stratified_epoch_inventories_frame_but_fetches_only_sampled_windows(
    tmp_path: Path,
):
    helius = FakeEpochRPC()

    result = start_epoch(
        tmp_path / "stratified-epoch",
        window_slots=16,
        providers=("helius-free",),
        slots_per_hour_estimate=3_200,
        sampling_seed="fixed-stratified-frame",
        signature_check_all_slots=True,
        stratum_count=8,
        clients={"helius-free": helius},
    )

    window_plan = json.loads((tmp_path / "stratified-epoch" / "window-plan.json").read_text())
    paired_summary = json.loads(
        (tmp_path / "stratified-epoch" / "paired-block-capture" / "summary.json").read_text()
    )
    window = paired_summary["comparison"]["windows"][0]
    assert len(window_plan["selection_frame"]["selected_windows"]) == 8
    assert len(window_plan["full_sample_slots"]) == 16
    assert len(window_plan["selection_frame"]["slot_inclusion_probabilities"]) > 16
    assert result["block_capture"]["qualified_sample_slot_count"] == 16
    assert len(window["slots_compared"]) == 16
    assert window["enumerated_slot_count"] == 100
    assert len(window["unselected_produced_slots"]["helius-free"]) == 84


def test_stratified_epoch_pairs_alchemy_on_a_recorded_provider_subset(tmp_path: Path):
    helius = FakeEpochRPC()
    alchemy = FakeEpochRPC()

    result = start_epoch(
        tmp_path / "paired-stratified-epoch",
        window_slots=16,
        providers=("helius-free", "alchemy-free"),
        slots_per_hour_estimate=3_200,
        sampling_seed="fixed-paired-stratified-frame",
        signature_check_all_slots=True,
        stratum_count=8,
        paired_provider="alchemy-free",
        paired_slots_per_stratum=1,
        clients={"helius-free": helius, "alchemy-free": alchemy},
    )

    window_plan = json.loads(
        (tmp_path / "paired-stratified-epoch" / "window-plan.json").read_text()
    )
    provider_samples = window_plan["provider_full_sample_slots"]
    assert len(provider_samples["helius-free"]) == 16
    assert len(provider_samples["alchemy-free"]) == 8
    assert len(window_plan["paired_selection_probabilities"]) == 8
    assert result["block_capture"]["helius_credit_reservations"] == 33
    assert helius.calls == 34  # finalized anchor, inventory, and full/signature pairs
    assert alchemy.calls == 18  # anchor, inventory, and eight full/signature pairs
    paired_summary = json.loads(
        (
            tmp_path
            / "paired-stratified-epoch"
            / "paired-block-capture"
            / "summary.json"
        ).read_text()
    )
    rows = paired_summary["comparison"]["windows"][0]["slots_compared"]
    assert len(rows) == 16
    assert sum(
        row["providers"]["alchemy-free"]["observation_status"]
        == "qualified_structural_reference_count"
        for row in rows
    ) == 8
    assert all(
        row["providers"]["helius-free"]["observation_status"]
        == "qualified_structural_reference_count"
        for row in rows
    )
