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
