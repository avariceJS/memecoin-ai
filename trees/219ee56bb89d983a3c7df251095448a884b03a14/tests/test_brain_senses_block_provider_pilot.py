from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest

from memecoin_ai.domain import canonical
from memecoin_ai.sources import Capture, HTTPSourceError
from tools import brain_senses_block_epoch as block_epoch
from tools import brain_senses_block_provider_pilot as block_pilot
from tools.brain_senses_block_epoch import start_epoch
from tools.brain_senses_block_provider_audit import _helius_log_truncation_summary, audit
from tools.brain_senses_block_provider_pilot import (
    MAX_RESPONSE_BYTES,
    _build_operations,
    _structural_response_validation,
    _transaction_comparison,
    run_once,
)


class FakeBlockRPC:
    def __init__(self, *, fail_first: bool = False, log_messages: list[str] | None = None) -> None:
        self.calls = 0
        self.fail_first = fail_first
        self.log_messages = log_messages

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
                                **(
                                    {"logMessages": self.log_messages}
                                    if self.log_messages is not None
                                    else {}
                                ),
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


def test_block_pilot_restarts_from_journal_without_duplicate_reservations(
    tmp_path: Path, monkeypatch
) -> None:
    original_result_for = block_pilot._result_for
    monkeypatch.setattr(block_pilot.time, "sleep", lambda _seconds: None)
    # Exhaust every crash boundary after a durable result in this six-request fixture.
    for crash_after in range(1, 6):
        output = tmp_path / f"restartable-pilot-{crash_after}"
        helius = FakeBlockRPC()
        public = FakeBlockRPC()
        dispatched = 0

        def interrupt_at_boundary(
            *args: Any, _crash_boundary: int = crash_after, **kwargs: Any
        ) -> dict[str, Any]:
            nonlocal dispatched
            row = original_result_for(*args, **kwargs)
            dispatched += 1
            if dispatched == _crash_boundary:
                raise KeyboardInterrupt
            return row

        monkeypatch.setattr(block_pilot, "_result_for", interrupt_at_boundary)
        with pytest.raises(KeyboardInterrupt):
            run_once(
                output,
                slot_ranges=[[100, 101]],
                request_cap=3,
                clients={"helius-free": helius, "solana-public": public},
            )
        assert len(list((output / "requests").glob("*.json"))) == crash_after

        monkeypatch.setattr(block_pilot, "_result_for", original_result_for)
        resumed = run_once(
            output,
            slot_ranges=[[100, 101]],
            request_cap=3,
            clients={"helius-free": helius, "solana-public": public},
        )

        requests = sorted((output / "requests").glob("*.json"))
        results = sorted((output / "request-results").glob("*.json"))
        assert resumed["completed"] == resumed["attempts"] == 6
        assert len(requests) == len(results) == 6
        assert [json.loads(path.read_text())["sequence"] for path in requests] == list(
            range(1, 7)
        )
        assert [json.loads(path.read_text())["sequence"] for path in results] == list(
            range(1, 7)
        )
        identities = {
            (
                row["provider"],
                row["method"],
                row.get("slot"),
                row["detail"],
                row["request_sha256"],
            )
            for row in (json.loads(path.read_text()) for path in requests)
        }
        assert len(identities) == 6
        assert helius.calls == public.calls == 3


def test_block_pilot_reconstructs_stop_for_unresolved_reservation(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "ambiguous-pilot"
    original_result_for = block_pilot._result_for

    def interrupt_after_reservation(
        root: Path, sequence: int, operation: dict[str, Any], client: Any, **kwargs: Any
    ) -> dict[str, Any]:
        request = dict(
            jsonrpc="2.0", id=1, method=operation["method"], params=operation["params"]
        )
        body = canonical(request).encode()
        block_pilot._create(
            root / "requests" / f"{sequence:012d}.json",
            dict(
                sequence=sequence,
                provider=operation["provider"],
                method=operation["method"],
                slot=operation.get("slot"),
                slot_range=operation.get("slot_range"),
                detail=operation["detail"],
                request_sha256=hashlib.sha256(body).hexdigest(),
                params_sha256=hashlib.sha256(canonical(operation["params"]).encode()).hexdigest(),
                reserved_ns=time.time_ns(),
                request_reservation=1,
                credit_reservation=1 if operation["provider"] == "helius-free" else 0,
            ),
        )
        raise KeyboardInterrupt

    monkeypatch.setattr(block_pilot, "_result_for", interrupt_after_reservation)
    with pytest.raises(KeyboardInterrupt):
        run_once(
            output,
            slot_ranges=[[100, 101]],
            request_cap=3,
            clients={"helius-free": FakeBlockRPC(), "solana-public": FakeBlockRPC()},
        )
    monkeypatch.setattr(block_pilot, "_result_for", original_result_for)
    helius = FakeBlockRPC()
    with pytest.raises(ValueError, match="ambiguous_block_request_requires_manual_reconciliation"):
        run_once(
            output,
            slot_ranges=[[100, 101]],
            request_cap=3,
            clients={"helius-free": helius, "solana-public": FakeBlockRPC()},
        )
    assert helius.calls == 0
    assert json.loads((output / "STOP").read_text())["reason"] == (
        "ambiguous_block_request_requires_manual_reconciliation"
    )


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


def test_helius_truncation_audit_requires_explicit_marker_for_log_only_difference() -> None:
    helius = {
        "transactions": [
            {
                "transaction": {"signatures": ["sig"]},
                "meta": {"logMessages": ["Program log", "Log truncated"]},
            }
        ]
    }
    alchemy = {
        "transactions": [
            {"transaction": {"signatures": ["sig"]}, "meta": {"logMessages": ["Program log"]}}
        ]
    }

    result = _helius_log_truncation_summary(helius, alchemy)

    assert result == {
        "status": "compared",
        "paired_transactions": 1,
        "exact_transactions": 0,
        "different_transactions": 1,
        "log_messages_only_differences": 1,
        "explicit_helius_truncation_markers": 1,
        "log_messages_differences_without_marker": 0,
        "other_differences": 0,
        "unpaired_helius_transactions": 0,
        "unpaired_other_transactions": 0,
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
    transaction_comparison = result["block_comparison"]["transaction_comparison"]
    assert {
        key: transaction_comparison[key]
        for key in (
            "paired_transactions",
            "exact_tree_matches",
            "different_trees",
            "difference_paths",
        )
    } == {
        "paired_transactions": 4,
        "exact_tree_matches": 4,
        "different_trees": 0,
        "difference_paths": {},
    }
    assert (
        transaction_comparison["helius_log_truncation_audit"]["audited_differing_slot_pairs"] == 0
    )


def test_offline_provider_audit_replays_only_differences_and_counts_truncation_markers(
    tmp_path: Path,
) -> None:
    root = tmp_path / "truncated-log-pilot"
    run_once(
        root,
        slot_ranges=[[100, 102]],
        request_cap=10,
        clients={
            "helius-free": FakeBlockRPC(log_messages=["Program log", "Log truncated"]),
            "solana-public": FakeBlockRPC(log_messages=["Program log"]),
        },
    )

    result = audit(root)

    truncation = result["block_comparison"]["transaction_comparison"]["helius_log_truncation_audit"]
    assert truncation["audited_differing_slot_pairs"] == 2
    assert truncation["totals_over_replayed_differences"] == {
        "different_transactions": 4,
        "exact_transactions": 0,
        "explicit_helius_truncation_markers": 4,
        "log_messages_differences_without_marker": 0,
        "log_messages_only_differences": 4,
        "other_differences": 0,
        "paired_transactions": 4,
        "unpaired_helius_transactions": 0,
        "unpaired_other_transactions": 0,
        "unknown_transaction_maps": 0,
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


def test_block_epoch_restart_reuses_anchor_and_pinned_selection_lineage(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "restartable-epoch"
    old_epoch = tmp_path / "previous-epoch"
    (old_epoch / "request-results").mkdir(parents=True)
    (old_epoch / "STOP").write_text('{"reason":"prior_stop"}\n')
    (old_epoch / "request-results" / "000000000001.json").write_text(
        '{"receipt_ns":12345}\n'
    )
    helius = FakeEpochRPC()
    original_result_for = block_pilot._result_for
    dispatched = 0

    def interrupt_after_first_result(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal dispatched
        row = original_result_for(*args, **kwargs)
        dispatched += 1
        if dispatched == 1:
            raise KeyboardInterrupt
        return row

    monkeypatch.setattr(block_pilot, "_result_for", interrupt_after_first_result)
    with pytest.raises(KeyboardInterrupt):
        start_epoch(
            output,
            window_slots=2,
            old_epoch_root=old_epoch,
            providers=("helius-free",),
            slots_per_hour_estimate=24,
            sampling_seed="restart-lineage-seed",
            clients={"helius-free": helius},
        )
    first_window = json.loads((output / "window-plan.json").read_text())
    first_reserved = sorted((output / "paired-block-capture" / "requests").glob("*.json"))
    assert len(first_reserved) == 1
    assert helius.calls == 2  # one anchor and the first, journaled inventory operation

    monkeypatch.setattr(block_pilot, "_result_for", original_result_for)
    result = start_epoch(
        output,
        window_slots=2,
        old_epoch_root=old_epoch,
        providers=("helius-free",),
        slots_per_hour_estimate=24,
        sampling_seed="restart-lineage-seed",
        clients={"helius-free": helius},
    )

    requests = sorted((output / "paired-block-capture" / "requests").glob("*.json"))
    assert result["status"] == "provider_window_attempted"
    assert json.loads((output / "window-plan.json").read_text()) == first_window
    results = list((output / "paired-block-capture" / "request-results").glob("*.json"))
    assert len(requests) == len(results) == 4
    assert [json.loads(path.read_text())["sequence"] for path in requests] == [1, 2, 3, 4]
    assert helius.calls == 5  # no repeated anchor or inventory request
    assert result["gap"]["last_receipt_ns_in_old_epoch"] == 12345
    assert result["gap"]["claim"].startswith("unobserved interval")


def test_block_epoch_reconstructs_stop_for_ambiguous_anchor_without_replay(tmp_path: Path) -> None:
    output = tmp_path / "ambiguous-anchor-epoch"

    class InterruptedAnchor:
        calls = 0

        def fetch(self, method: str, params: list[object]) -> Capture:
            self.calls += 1
            raise KeyboardInterrupt

        def close(self) -> None:
            return

    interrupted = InterruptedAnchor()
    with pytest.raises(KeyboardInterrupt):
        start_epoch(
            output,
            window_slots=2,
            providers=("helius-free",),
            clients={"helius-free": interrupted},
        )
    retry_client = FakeEpochRPC()
    with pytest.raises(
        ValueError, match="ambiguous_epoch_anchor_requires_manual_reconciliation"
    ):
        start_epoch(
            output,
            window_slots=2,
            providers=("helius-free",),
            clients={"helius-free": retry_client},
        )

    assert retry_client.calls == 0
    assert len(list((output / "requests").glob("*.json"))) == 1
    assert json.loads((output / "STOP").read_text())["reason"] == (
        "ambiguous_epoch_anchor_requires_manual_reconciliation"
    )


def test_block_epoch_resumes_after_durable_anchor_before_window_plan(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "anchor-before-window-epoch"
    helius = FakeEpochRPC()
    original_anchor_once = block_epoch._anchor_once

    def interrupt_after_anchor_result(*args: Any, **kwargs: Any) -> dict[str, Any]:
        original_anchor_once(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(block_epoch, "_anchor_once", interrupt_after_anchor_result)
    with pytest.raises(KeyboardInterrupt):
        start_epoch(
            output,
            window_slots=2,
            providers=("helius-free",),
            sampling_seed="anchor-before-window-seed",
            slots_per_hour_estimate=24,
            signature_check_all_slots=True,
            clients={"helius-free": helius},
        )

    assert len(list((output / "requests").glob("*.json"))) == 1
    assert not (output / "window-plan.json").exists()
    assert helius.calls == 1

    monkeypatch.setattr(block_epoch, "_anchor_once", original_anchor_once)
    result = start_epoch(
        output,
        window_slots=2,
        providers=("helius-free",),
        sampling_seed="anchor-before-window-seed",
        slots_per_hour_estimate=24,
        signature_check_all_slots=True,
        clients={"helius-free": helius},
    )

    requests = sorted((output / "paired-block-capture" / "requests").glob("*.json"))
    assert result["status"] == "provider_window_attempted"
    window = json.loads((output / "window-plan.json").read_text())
    assert window["selection_frame"]["sampling_seed"] == "anchor-before-window-seed"
    assert len(requests) == 5
    assert helius.calls == 6  # the durable anchor was reused, not repeated


def test_block_epoch_rejects_modified_selection_before_resuming_requests(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "tampered-selection-epoch"
    helius = FakeEpochRPC()
    original_result_for = block_pilot._result_for

    def interrupt_after_first_result(*args: Any, **kwargs: Any) -> dict[str, Any]:
        original_result_for(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(block_pilot, "_result_for", interrupt_after_first_result)
    with pytest.raises(KeyboardInterrupt):
        start_epoch(
            output,
            window_slots=2,
            providers=("helius-free",),
            slots_per_hour_estimate=24,
            sampling_seed="tamper-detection-seed",
            clients={"helius-free": helius},
        )
    monkeypatch.setattr(block_pilot, "_result_for", original_result_for)

    window_path = output / "window-plan.json"
    window = json.loads(window_path.read_text())
    window["full_sample_slots"][0] += 1
    window_path.write_text(json.dumps(window, sort_keys=True) + "\n")
    calls_before_restart = helius.calls
    with pytest.raises(ValueError, match="epoch_window_selection_recomputation_mismatch"):
        start_epoch(
            output,
            window_slots=2,
            providers=("helius-free",),
            slots_per_hour_estimate=24,
            sampling_seed="tamper-detection-seed",
            clients={"helius-free": helius},
        )

    assert helius.calls == calls_before_restart


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
        (tmp_path / "paired-stratified-epoch" / "paired-block-capture" / "summary.json").read_text()
    )
    rows = paired_summary["comparison"]["windows"][0]["slots_compared"]
    assert len(rows) == 16
    assert (
        sum(
            row["providers"]["alchemy-free"]["observation_status"]
            == "qualified_structural_reference_count"
            for row in rows
        )
        == 8
    )
    assert all(
        row["providers"]["helius-free"]["observation_status"]
        == "qualified_structural_reference_count"
        for row in rows
    )
