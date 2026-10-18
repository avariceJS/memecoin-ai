"""Synthetic observations cannot create inclusion latency or discard failed fee costs."""

import json
import runpy
from pathlib import Path

import pytest

from memecoin_ai.domain import ValidationError
from memecoin_ai.sources import READ_METHODS, Capture


def module(monkeypatch):
    tools = Path(__file__).parents[1] / "tools"
    monkeypatch.syspath_prepend(str(tools))
    return runpy.run_path(str(tools / "observed_execution.py"))


def cap(method, result, params=None, received=10_000_000_000):
    return Capture(
        "synthetic",
        method,
        json.dumps({"jsonrpc": "2.0", "id": 1, "params": params or []}),
        received - 100,
        received,
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode(),
    )


def candidate():
    return dict(signature="s", slot=10, first_seen_ns=1_000_000_000, listed_error=None)


def test_status_rpc_is_readonly_without_trade_methods():
    assert "getSignatureStatuses" in READ_METHODS
    assert "sendTransaction" not in READ_METHODS and "simulateTransaction" not in READ_METHODS


def test_missing_tx_is_not_free_execution(monkeypatch):
    r = module(monkeypatch)["transaction_evidence"](cap("getTransaction", None), candidate())
    assert r["status"] == "unavailable_at_finalized" and "fee_lamports" not in r


def test_failed_transaction_fee_is_retained_and_not_strategy_cost(monkeypatch):
    c = candidate()
    c["listed_error"] = {"InstructionError": [0, "failed"]}
    tx = dict(
        slot=10,
        transaction=dict(signatures=["s"]),
        blockTime=2,
        meta=dict(err=c["listed_error"], fee=5000, computeUnitsConsumed=12),
    )
    r = module(monkeypatch)["transaction_evidence"](cap("getTransaction", tx), c)
    assert r["failed"] and r["fee_lamports"] == 5000
    assert r["total_strategy_cost"] is None and r["submission_to_inclusion_seconds"] is None
    tx["transaction"]["signatures"] = ["other"]
    with pytest.raises(ValidationError, match="linkage"):
        module(monkeypatch)["transaction_evidence"](cap("getTransaction", tx), c)


@pytest.mark.parametrize(
    "statuses,censor",
    [
        (["finalized"], "left_censored"),
        (["confirmed", None], "right_censored"),
        (["confirmed", "finalized"], "observed_between_polls"),
    ],
)
def test_polling_preserves_censoring_and_unknown_inclusion(monkeypatch, statuses, censor):
    polls = [
        cap(
            "getSignatureStatuses",
            dict(value=[None if s is None else dict(slot=10, confirmationStatus=s)]),
            [["s"]],
            (i + 2) * 10**9,
        )
        for i, s in enumerate(statuses)
    ]
    r = module(monkeypatch)["timing"](candidate(), polls)
    assert r["censoring"] == censor and r["inclusion_latency_seconds"] is None


def test_selection_keeps_failed_signatures_and_is_deterministic(monkeypatch):
    rows = [
        dict(signature="z", slot=10, err={"x": 1}, blockTime=None),
        dict(signature="a", slot=10, err=None, blockTime=None),
    ]
    selected = module(monkeypatch)["select"]([cap("getSignaturesForAddress", rows)], 2)
    assert [r["signature"] for r in selected] == ["a", "z"]
    assert selected[1]["listed_error"] is not None


def test_receipts_cannot_move_before_discovery(monkeypatch):
    m = module(monkeypatch)
    with pytest.raises(ValidationError, match="precedes"):
        m["transaction_evidence"](cap("getTransaction", None, received=1), candidate())
    with pytest.raises(ValidationError, match="chronology"):
        m["timing"](candidate(), [cap("getSignatureStatuses", {}, [["s"]], received=1)])
