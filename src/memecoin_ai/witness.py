"""Event-conditioned diagnostics, explicitly weaker than checkpoint-anchored replay."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

from memecoin_ai.cpmm import EconomicState, liquidity
from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.protocol import KINDS, flatten, invocations, transfer
from memecoin_ai.raydium import PROGRAM, TOKEN, b58decode, b58encode, discriminator
from memecoin_ai.reconciliation import vault_transition
from memecoin_ai.sources import Capture


def verify_liquidity_witness(capture: Capture) -> dict[str, Any]:
    req = json.loads(capture.request_json)
    tx = capture.result()
    if (
        capture.method != "getTransaction"
        or req["params"][1].get("commitment") != "finalized"
        or req["params"][1].get("encoding") != "jsonParsed"
        or not isinstance(tx, dict)
        or tx["transaction"]["signatures"][0] != req["params"][0]
        or tx["meta"]["err"] is not None
    ):
        raise ValidationError("witness requires a successful finalized parsed transaction")
    nodes = flatten(invocations(tx))
    candidates = [
        n
        for n in nodes
        if n.instruction["programId"] == PROGRAM
        and KINDS.get(b58decode(n.instruction.get("data", ""))[:8]) in ("deposit", "withdraw")
    ]
    if len(candidates) != 1:
        raise ValidationError("witness requires exactly one liquidity operation")
    node = candidates[0]
    ix = node.instruction
    raw = b58decode(ix["data"])
    kind = KINDS[raw[:8]]
    deposit = kind == "deposit"
    accounts = ix["accounts"]
    if len(raw) != 32 or len(accounts) != (13 if deposit else 14) or len(node.events) != 1:
        raise ValidationError("unsupported liquidity witness layout")
    event = node.events[0]
    if (
        len(event) != 97
        or event[:8] != discriminator("event:LpChangeEvent")
        or b58encode(event[8:40]) != accounts[2]
        or event[96] != (0 if deposit else 1)
    ):
        raise ValidationError("liquidity witness event mismatch")
    supply, reserve0, reserve1, amount0, amount1, fee0, fee1 = struct.unpack_from("<7Q", event, 40)
    if fee0 or fee1:
        raise ValidationError("witness does not infer historical Token-2022 fee schedules")
    lp, limit0, limit1 = struct.unpack_from("<3Q", raw, 8)
    # Only mathematical inputs come from the event. Excluded fee counters are NOT inferred.
    state = EconomicState((reserve0, reserve1), (0, 0), (0, 0), (0, 0), supply)
    _, amounts = liquidity(state, lp, (limit0, limit1), deposit=deposit)
    if amounts != (amount0, amount1):
        raise ValidationError("liquidity math disagrees with event")
    vaults, mints = accounts[6:8], accounts[10:12]
    calls = [
        n
        for n in nodes
        if n.instruction["programId"] in (TOKEN,)
        and any(v in canonical(n.instruction) for v in vaults)
    ]
    expected = [
        (
            accounts[4 + i] if deposit else vaults[i],
            vaults[i] if deposit else accounts[4 + i],
            mints[i],
            amounts[i],
        )
        for i in range(2)
    ]
    if (
        len(calls) != 2
        or any(n not in node.children for n in calls)
        or [transfer(n) for n in calls] != expected
    ):
        raise ValidationError("liquidity witness transfer mismatch")
    lp_calls = [
        n.instruction
        for n in node.children
        if n.instruction.get("parsed", {}).get("info", {}).get("mint") == accounts[12]
    ]
    if len(lp_calls) != 1:
        raise ValidationError("ambiguous witness LP mint/burn")
    lp_ix = lp_calls[0]
    if (
        lp_ix["programId"] != TOKEN
        or lp_ix["parsed"]["type"] != ("mintTo" if deposit else "burn")
        or lp_ix["parsed"]["info"]["amount"] != str(lp)
    ):
        raise ValidationError("witness LP amount mismatch")
    for i in range(2):
        balances = vault_transition(tx, vaults[i], mints[i], TOKEN)
        if balances is None or balances[1] - balances[0] != (
            amounts[i] if deposit else -amounts[i]
        ):
            raise ValidationError("liquidity witness vault delta mismatch")
    return dict(
        schema_version=1,
        signature=req["params"][0],
        slot=tx["slot"],
        pool=accounts[2],
        kind=kind,
        lp_amount=lp,
        amounts=list(amounts),
        arithmetic_and_transfers_match=True,
        independent_prestate_verified=False,
        fee_counters_reconstructed=False,
        historical_admission_verified=False,
        market_dataset_qualified=False,
        evidence_sha256=digest(capture.response),
        conditioned_on="LP supply and effective reserves emitted by the same transaction",
    )


def write_liquidity_witness(capture: Capture, output: Path) -> dict[str, Any]:
    report = verify_liquidity_witness(capture)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(canonical(report) + "\n")
    return report
