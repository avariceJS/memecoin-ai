from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from memecoin_ai.domain import canonical
from memecoin_ai.sources import Capture
from tools.brain_senses_block_provider_pilot import MAX_RESPONSE_BYTES, run_once


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
                                "message": {"accountKeys": [], "instructions": []},
                            },
                            "meta": {"err": None, "innerInstructions": []},
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
