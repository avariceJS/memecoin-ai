import base64
import json
import struct
from dataclasses import replace

import pytest

from memecoin_ai.domain import ValidationError, canonical
from memecoin_ai.provider_audit import (
    compare_captures,
    crosscheck,
    measure_latency,
    verify_provider_bundle,
)
from memecoin_ai.sources import Capture, HTTPSourceError, ReadOnlyRPC
from memecoin_ai.tokens import CLOCK, SYSVAR_OWNER


def capture(method, params, result, alias="a"):
    return Capture(
        alias,
        method,
        canonical(dict(jsonrpc="2.0", id=1, method=method, params=params)),
        1,
        2,
        canonical(dict(jsonrpc="2.0", id=1, result=result)).encode(),
    )


def test_different_banks_are_not_disagreement_or_agreement():
    a = capture(
        "getMultipleAccounts",
        [["key"], dict(commitment="finalized", minContextSlot=10)],
        dict(context=dict(slot=10), value=[{"data": "x"}]),
    )
    b = replace(a, source_alias="b", response=a.response.replace(b'"slot":10', b'"slot":11'))
    assert compare_captures(a, b)["status"] == "incomparable"
    b = replace(a, source_alias="b")
    assert compare_captures(a, b)["status"] == "match"
    assert not compare_captures(a, b)["independent_infrastructure_verified"]
    with pytest.raises(ValidationError):
        compare_captures(a, a)


def test_block_order_and_null_are_never_silently_ignored():
    params = [101, dict(commitment="finalized", transactionDetails="signatures")]
    block = dict(
        blockhash="h", previousBlockhash="p", parentSlot=100, blockTime=1, signatures=["x", "y"]
    )
    a = capture("getBlock", params, block)
    b = capture("getBlock", params, dict(block, signatures=["y", "x"]), alias="b")
    assert compare_captures(a, b)["status"] == "mismatch"
    assert (
        compare_captures(a, capture("getBlock", params, None, alias="b"))["status"] == "unavailable"
    )
    b = capture("getBlock", [102, params[1]], block, alias="b")
    with pytest.raises(ValidationError, match="requests differ"):
        compare_captures(a, b)


@pytest.mark.parametrize("values", [[], [102], [101, 102]])
def test_block_enumeration_comparison(values):
    params = [101, 102, dict(commitment="finalized")]
    a = capture("getBlocks", params, [101, 102])
    b = capture("getBlocks", params, values, alias="b")
    status = compare_captures(a, b)["status"]
    assert status == (
        "unavailable" if not values else "match" if values == [101, 102] else "mismatch"
    )


def test_denied_provider_retains_plan_and_stops_without_claiming_match(tmp_path):
    calls = []

    def transport(endpoint, body, timeout, limit):
        calls.append(body)
        raise HTTPSourceError(403)

    client = ReadOnlyRPC("https://example.invalid", "secondary", transport=transport)
    captures = [capture("getBlocks", [1, 2, dict(commitment="finalized")], [1, 2])] * 2
    report = crosscheck(captures, client, tmp_path / "audit")
    assert report["attempted"] == 1 and report["planned"] == 2 and len(calls) == 1
    assert not report["all_requested_observations_match"]
    assert (tmp_path / "audit/001-reference/response.json").exists()
    assert verify_provider_bundle(tmp_path / "audit") == report
    with pytest.raises(FileExistsError):
        crosscheck(captures, client, tmp_path / "audit")


def test_match_reproduces_and_corrupted_receipt_is_rejected(tmp_path):
    a = capture("getBlocks", [1, 2, dict(commitment="finalized")], [1, 2])
    client = ReadOnlyRPC("https://example.invalid", "secondary", transport=lambda *args: a.response)
    report = crosscheck([a], client, tmp_path / "audit")
    assert report["all_requested_observations_match"]
    assert verify_provider_bundle(tmp_path / "audit") == report
    with (tmp_path / "audit/000-secondary/receipt.json").open("a") as f:
        f.write(" ")
    with pytest.raises(ValidationError):
        verify_provider_bundle(tmp_path / "audit")


def test_monotonic_measurements_and_visibility_censoring(tmp_path, monkeypatch):
    import memecoin_ai.provider_audit as module

    monkeypatch.setattr(module.time, "sleep", lambda _: None)

    def transport(endpoint, body, timeout, limit):
        req = json.loads(body)
        assert req["params"][0] == CLOCK
        slot = 100 if req["params"][1]["commitment"] == "confirmed" else 90
        raw = struct.pack("<QqQQq", slot, 0, 0, 0, 1)
        result = dict(
            context=dict(slot=slot),
            value=dict(
                owner=SYSVAR_OWNER,
                executable=False,
                data=[base64.b64encode(raw).decode(), "base64"],
            ),
        )
        return canonical(dict(jsonrpc="2.0", id=1, result=result)).encode()

    client = ReadOnlyRPC("https://example.invalid", "primary", transport=transport)
    report = measure_latency(client, tmp_path / "latency", rounds=2, interval=1)
    assert report["attempted"] == 4 and report["errors"] == 0
    assert not report["finalized_seen"] and report["observed_visibility_delay_seconds"] is None
    assert report["groups"]["confirmed"]["response_ms"]["n"] == 2
    assert not report["execution_latency_calibrated"]
    assert verify_provider_bundle(tmp_path / "latency") == report


def test_latency_rate_limit_is_recorded_and_stops(tmp_path):
    def transport(*args):
        raise HTTPSourceError(429)

    client = ReadOnlyRPC("https://example.invalid", "primary", transport=transport)
    report = measure_latency(client, tmp_path / "latency", rounds=12)
    assert report["attempted"] == 2 and report["planned"] == 24 and report["errors"] == 2
    assert report["groups"]["finalized"]["response_ms"]["n"] == 0
    assert verify_provider_bundle(tmp_path / "latency") == report


def test_real_transaction_metadata_disagreement_is_not_ignored():
    from pathlib import Path

    a = Capture.load(Path(__file__).parent / "fixtures/fund_collection/fund-tx-2")
    b = replace(a, source_alias="independent-candidate")
    assert compare_captures(a, b)["status"] == "match"
    payload = json.loads(b.response)
    payload["result"]["meta"]["postTokenBalances"][0]["uiTokenAmount"]["amount"] = "0"
    b = replace(b, response=canonical(payload).encode())
    assert compare_captures(a, b)["status"] == "mismatch"


def test_confirmed_visibility_already_observed_is_left_censored(tmp_path):
    from memecoin_ai.provider_audit import latency_report

    (tmp_path / "spec.json").write_text(canonical(dict(source_alias="a", rounds=1)))
    attempts = []
    for index, commitment in enumerate(("confirmed", "finalized")):
        raw = struct.pack("<QqQQq", 100, 0, 0, 0, 1)
        result = dict(
            context=dict(slot=100),
            value=dict(
                owner=SYSVAR_OWNER,
                executable=False,
                data=[base64.b64encode(raw).decode(), "base64"],
            ),
        )
        cap = capture(
            "getAccountInfo", [CLOCK, dict(encoding="base64", commitment=commitment)], result
        )
        cap = replace(cap, received_ns=3 - index)
        cap.save(tmp_path / commitment)
        attempts.append(dict(round=0, commitment=commitment, elapsed_ns=1, capture=commitment))
    (tmp_path / "attempts.json").write_text(canonical(attempts))
    report = latency_report(tmp_path)
    assert report["finalized_seen"]
    assert report["observed_visibility_delay_seconds"] is None
