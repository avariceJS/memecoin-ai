import base64
import json
from dataclasses import replace
from pathlib import Path

import pytest

from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.history import EpochSchedule, ParameterHistory
from memecoin_ai.protocol import checkpoint, replay_transaction
from memecoin_ai.sources import Capture
from memecoin_ai.witness import verify_liquidity_witness

FIXTURES = Path(__file__).parent / "fixtures" / "parameter_history"


def test_real_liquidity_witnesses_and_external_withheld():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    for path, sha in manifest["files"].items():
        assert digest((FIXTURES / path).read_bytes()) == sha
    kinds = []
    for path in sorted(FIXTURES.glob("lp-*")):
        report = verify_liquidity_witness(Capture.load(path))
        assert report["arithmetic_and_transfers_match"]
        assert not report["independent_prestate_verified"]
        assert not report["market_dataset_qualified"]
        kinds.append(report["kind"])
    assert sorted(kinds) == ["deposit", "deposit", "withdraw", "withdraw"]
    anchor = Capture.load(FIXTURES.parent / "token2022" / "checkpoint-2")
    pool = json.loads(anchor.request_json)["params"][0][0]
    ctx = checkpoint(anchor, pool)
    history = ParameterHistory(
        anchor, EpochSchedule.from_capture(Capture.load(FIXTURES / "epoch-schedule"))
    )
    state, detail = replay_transaction(
        ctx, ctx.state, Capture.load(FIXTURES / "external-withheld").result(), parameters=history
    )
    assert state == ctx.state and not detail["operations"]
    # Real recipient is external: this establishes no claim about reconstructing mint-wide withheld.


@pytest.mark.parametrize(
    "corruption", ["event", "transfer", "failed", "transfer_fee", "missing_log"]
)
def test_liquidity_witness_rejects_inconsistent_evidence(corruption):
    cap = Capture.load(FIXTURES / "lp-1-tx-1")
    payload = json.loads(cap.response)
    tx = payload["result"]
    if corruption in ("event", "transfer_fee"):
        i = next(
            i for i, v in enumerate(tx["meta"]["logMessages"]) if v.startswith("Program data: ")
        )
        raw = bytearray(base64.b64decode(tx["meta"]["logMessages"][i][14:]))
        raw[64 if corruption == "event" else 80] ^= 1
        tx["meta"]["logMessages"][i] = "Program data: " + base64.b64encode(raw).decode()
    elif corruption == "transfer":
        ix = next(
            n
            for g in tx["meta"]["innerInstructions"]
            for n in g["instructions"]
            if n.get("parsed", {}).get("type") == "transferChecked"
        )
        ix["parsed"]["info"]["tokenAmount"]["amount"] = "1"
    elif corruption == "failed":
        tx["meta"]["err"] = {"fixture": "failed"}
    else:
        tx["meta"]["logMessages"].pop()
    with pytest.raises(ValidationError):
        verify_liquidity_witness(replace(cap, response=canonical(payload).encode()))


def test_real_fund_collections_and_transfer_deductions():
    from memecoin_ai.witness import verify_collection_witness

    root = FIXTURES.parent / "fund_collection"
    manifest = json.loads((root / "manifest.json").read_text())
    for name, sha in manifest["files"].items():
        assert digest((root / name).read_bytes()) == sha
    expected = [[104918084, 0], [7492515, 0], [16656671, 1520]]
    for path, deductions in zip(sorted(root.glob("fund-tx-*")), expected, strict=True):
        report = verify_collection_witness(Capture.load(path))
        assert report["kind"] == "collect_fund_fee"
        assert report["transfers_and_limits_match"]
        assert report["observed_transfer_deductions"] == deductions
        assert not report["fee_counters_reconstructed"] and not report["historical_tariff_verified"]


@pytest.mark.parametrize("corruption", ["limit", "transfer", "balance", "logs", "failed"])
def test_collection_witness_rejects_inconsistent_evidence(corruption):
    import struct

    from memecoin_ai.raydium import PROGRAM, b58decode, b58encode
    from memecoin_ai.witness import verify_collection_witness

    cap = Capture.load(FIXTURES.parent / "fund_collection" / "fund-tx-2")
    payload = json.loads(cap.response)
    tx = payload["result"]
    if corruption == "limit":
        ix = next(
            ix for ix in tx["transaction"]["message"]["instructions"] if ix["programId"] == PROGRAM
        )
        raw = bytearray(b58decode(ix["data"]))
        struct.pack_into("<Q", raw, 8, 1)
        ix["data"] = b58encode(raw)
    elif corruption == "transfer":
        ix = next(
            n
            for g in tx["meta"]["innerInstructions"]
            for n in g["instructions"]
            if n.get("parsed", {}).get("type") == "transferChecked"
        )
        ix["parsed"]["info"]["tokenAmount"]["amount"] = "1"
    elif corruption == "balance":
        b = tx["meta"]["postTokenBalances"][0]
        b["uiTokenAmount"]["amount"] = str(int(b["uiTokenAmount"]["amount"]) + 1)
    elif corruption == "logs":
        tx["meta"]["logMessages"].pop()
    else:
        tx["meta"]["err"] = {"fixture": "failure"}
    with pytest.raises(ValidationError):
        verify_collection_witness(replace(cap, response=canonical(payload).encode()))
