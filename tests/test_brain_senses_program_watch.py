from __future__ import annotations

import json
import time
from pathlib import Path

from memecoin_ai.sources import Capture
from tools.brain_senses_program_watch import poll_once


class FakeRPC:
    source_alias = "fixture-rpc"

    def __init__(self, pages: list[list[dict]], transactions: dict[str, dict | None]):
        self.pages = pages
        self.transactions = transactions
        self.calls: list[tuple[str, list[object]]] = []

    def fetch(self, method: str, params: list[object]) -> Capture:
        self.calls.append((method, params))
        if method == "getSignaturesForAddress":
            value = self.pages.pop(0) if self.pages else []
        else:
            value = self.transactions[str(params[0])]
        now = time.time_ns()
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        response = json.dumps({"jsonrpc": "2.0", "id": 1, "result": value}).encode()
        return Capture(self.source_alias, method, request, now, now, response)


def signature(name: str, slot: int) -> dict:
    return {"signature": name, "slot": slot, "confirmationStatus": "finalized"}


def test_program_watch_seals_raw_transaction_and_deduplicates(tmp_path: Path) -> None:
    rpc = FakeRPC(
        [[signature("new", 30)], []],
        {"new": {"slot": 30, "blockTime": 1, "meta": {"err": None}}},
    )
    root = tmp_path / "watch"
    first = poll_once(root, rpc, page_limit=2, max_pages=1, pace_seconds=0)
    assert first["transactions_captured"] == 1
    assert first["rpc_requests"] == 2
    assert first["response_bytes"] > 0
    state = json.loads((root / "state.json").read_text())
    assert state["cursor"] == "new"

    second = poll_once(root, rpc, page_limit=2, max_pages=1, pace_seconds=0)
    assert second["signatures_observed"] == 0
    assert [method for method, _ in rpc.calls].count("getTransaction") == 1
    assert len(list((root / "captures").iterdir())) == 3


def test_program_watch_pages_newer_history_without_advancing_early(tmp_path: Path) -> None:
    rpc = FakeRPC(
        [
            [signature("base", 10)],
            [signature("new-b", 30), signature("new-a", 20)],
            [signature("older", 15)],
        ],
        {
            "base": {"slot": 10, "meta": {"err": None}},
            "new-a": {"slot": 20, "meta": {"err": None}},
            "new-b": {"slot": 30, "meta": {"err": None}},
            "older": {"slot": 15, "meta": {"err": None}},
        },
    )
    root = tmp_path / "watch"
    poll_once(root, rpc, page_limit=2, max_pages=1, pace_seconds=0)
    poll_once(root, rpc, page_limit=2, max_pages=1, pace_seconds=0)
    pending = json.loads((root / "state.json").read_text())
    assert pending["cursor"] == "base"
    assert pending["pending_before"] == "new-a"
    poll_once(root, rpc, page_limit=2, max_pages=1, pace_seconds=0)
    finished = json.loads((root / "state.json").read_text())
    assert finished["cursor"] == "new-b"
    assert finished["pending_before"] is None
    assert [method for method, _ in rpc.calls].count("getTransaction") == 4


def test_program_watch_stops_on_ambiguous_reserved_request(tmp_path: Path) -> None:
    rpc = FakeRPC([], {})
    root = tmp_path / "watch"
    (root / "requests").mkdir(parents=True)
    (root / "requests" / "000000000001.json").write_text("{}\n")
    try:
        poll_once(root, rpc, pace_seconds=0)
    except ValueError as exc:
        assert str(exc) == "unresolved_request_reservation_requires_reconciliation"
    else:
        raise AssertionError("ambiguous reservation must stop without retry")
    assert rpc.calls == []
