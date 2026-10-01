import base64
import copy
import json
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from memecoin_ai.cpmm import U64, collect_fees, liquidity
from memecoin_ai.domain import ValidationError, digest
from memecoin_ai.protocol import (
    Invocation,
    audit_protocol,
    checkpoint,
    flatten,
    invocations,
    replay_transaction,
    transition,
)
from memecoin_ai.raydium import PROGRAM, TOKEN, b58decode, b58encode, discriminator
from memecoin_ai.sources import Capture

FIXTURE = Path(__file__).parent / "fixtures" / "cpmm_protocol"


def cases():
    return json.loads((FIXTURE / "manifest.json").read_text())["cases"]


def load_case(index=0):
    case = cases()[index]
    ctx = checkpoint(Capture.load(FIXTURE / str(case["start"])), case["pool"])
    transactions = [Capture.load(FIXTURE / str(cid)).result() for cid in case["transactions"]]
    return case, ctx, transactions


def test_real_captures_and_full_economic_endpoint():
    manifest = json.loads((FIXTURE / "manifest.json").read_text())
    for path, sha in manifest["files"].items():
        assert digest((FIXTURE / path).read_bytes()) == sha
    kinds = set()
    failed = 0
    for index in range(2):
        case, ctx, txs = load_case(index)
        state = ctx.state
        for tx in txs:
            before = state
            state, detail = replay_transaction(ctx, state, tx)
            kinds.update(o["kind"] for o in detail["operations"])
            if detail["failed"]:
                failed += 1
                assert state == before
        if case["end"]:
            end = checkpoint(Capture.load(FIXTURE / str(case["end"])), case["pool"])
            assert state == end.state
            assert state.protocol != ctx.state.protocol
            assert state.fund != ctx.state.fund
    assert kinds == {"swap_base_input", "swap_base_output"}
    assert failed == 2


def target_node(ctx, tx):
    return next(
        n
        for n in flatten(invocations(tx))
        if n.instruction["programId"] == PROGRAM and ctx.pool_id in n.instruction["accounts"]
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "amount",
        "event",
        "transfer",
        "depth",
        "logs",
        "pre",
        "post",
        "failed",
        "program",
        "mint",
        "counter",
    ],
)
def test_evidence_corruption_cannot_be_patched_from_endpoint(corruption):
    _, ctx, txs = load_case()
    tx = copy.deepcopy(txs[0])
    node = target_node(ctx, tx)
    state = ctx.state
    if corruption == "amount":
        raw = bytearray(b58decode(node.instruction["data"]))
        raw[8] ^= 1
        node.instruction["data"] = b58encode(raw)
    elif corruption == "event":
        logs = tx["meta"]["logMessages"]
        i = next(i for i, s in enumerate(logs) if s.startswith("Program data:"))
        raw = bytearray(base64.b64decode(logs[i][14:]))
        raw[40] ^= 1
        logs[i] = "Program data: " + base64.b64encode(raw).decode()
    elif corruption == "transfer":
        node.children[0].instruction["parsed"]["info"]["tokenAmount"]["amount"] = "1"
    elif corruption == "depth":
        node.instruction["stackHeight"] = None
    elif corruption == "logs":
        tx["meta"]["logMessages"] = tx["meta"]["logMessages"][:-1]
    elif corruption in ("pre", "post"):
        field = "preTokenBalances" if corruption == "pre" else "postTokenBalances"
        keys = [k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
        balance = next(
            b for b in tx["meta"][field] if keys[b["accountIndex"]] == ctx.pool.vaults[0]
        )
        balance["uiTokenAmount"]["amount"] = str(int(balance["uiTokenAmount"]["amount"]) + 1)
    elif corruption == "failed":
        tx["meta"]["err"] = {"InstructionError": [0, "Custom"]}
    elif corruption == "program":
        node.children[0].instruction["programId"] = "unknown"
    elif corruption == "mint":
        node.instruction["accounts"][10] = ctx.pool.mints[1]
    elif corruption == "counter":
        state = replace(state, protocol=(state.protocol[0] + 1, state.protocol[1]))
    with pytest.raises((ValidationError, ValueError)):
        replay_transaction(ctx, state, tx)


def test_no_rewind_wrong_order():
    _, ctx, txs = load_case()
    with pytest.raises(ValidationError, match="pre-balance"):
        replay_transaction(ctx, ctx.state, txs[1])


def test_remaining_account_is_not_a_new_protocol_layout():
    _, ctx, txs = load_case()
    expected = replay_transaction(ctx, ctx.state, txs[0])
    tx = copy.deepcopy(txs[0])
    target_node(ctx, tx).instruction["accounts"].append(PROGRAM)
    assert replay_transaction(ctx, ctx.state, tx) == expected


def test_missing_inner_token_cpi_is_rejected_even_when_endpoint_matches():
    _, ctx, txs = load_case()
    tx = copy.deepcopy(txs[0])
    for group in tx["meta"]["innerInstructions"]:
        group["instructions"] = [i for i in group["instructions"] if i["programId"] != TOKEN]
    with pytest.raises(ValidationError, match="invocation mismatch"):
        replay_transaction(ctx, ctx.state, tx)


def test_caught_cpi_failure_is_explicitly_unsupported():
    _, ctx, txs = load_case()
    tx = copy.deepcopy(txs[0])
    tx["meta"]["logMessages"][-1] = tx["meta"]["logMessages"][-1].replace(
        "success", "failed: custom"
    )
    with pytest.raises(ValidationError, match="caught CPI failure"):
        replay_transaction(ctx, ctx.state, tx)


def transfer_node(source, dest, mint, amount):
    return Invocation(
        dict(
            programId=TOKEN,
            parsed=dict(
                type="transferChecked",
                info=dict(
                    source=source, destination=dest, mint=mint, tokenAmount=dict(amount=str(amount))
                ),
            ),
        ),
        2,
    )


@pytest.mark.parametrize("deposit", [True, False])
def test_liquidity_instruction_and_event_binding(deposit):
    _, ctx, _ = load_case()
    kind = "deposit" if deposit else "withdraw"
    lp = 1000
    limits = (U64, U64) if deposit else (0, 0)
    state, amounts = liquidity(ctx.state, lp, limits, deposit=deposit)
    accounts = [
        "owner",
        "authority",
        ctx.pool_id,
        "lp-account",
        "user0",
        "user1",
        *ctx.pool.vaults,
        TOKEN,
        "token2022",
        *ctx.pool.mints,
        ctx.lp_mint,
    ]
    if not deposit:
        accounts.append("memo")
    raw = discriminator("global:" + kind) + struct.pack("<3Q", lp, *limits)
    event = (
        discriminator("event:LpChangeEvent")
        + b58decode(ctx.pool_id)
        + struct.pack(
            "<7QB", ctx.state.lp_supply, *ctx.state.reserves, *amounts, 0, 0, 0 if deposit else 1
        )
    )
    node = Invocation(
        dict(programId=PROGRAM, accounts=accounts, data=b58encode(raw)), 1, events=[event]
    )
    for i in range(2):
        src, dst = (
            (accounts[4 + i], accounts[6 + i]) if deposit else (accounts[6 + i], accounts[4 + i])
        )
        node.children.append(transfer_node(src, dst, ctx.pool.mints[i], amounts[i]))
    node.children.append(
        Invocation(
            dict(
                programId=TOKEN,
                parsed=dict(
                    type="mintTo" if deposit else "burn",
                    info=dict(mint=ctx.lp_mint, amount=str(lp)),
                ),
            ),
            2,
        )
    )
    result, _, _ = transition(ctx, ctx.state, node)
    assert result == state
    node.children[-1].instruction["parsed"]["info"]["amount"] = "1"
    with pytest.raises(ValidationError, match="LP token instruction"):
        transition(ctx, ctx.state, node)


@pytest.mark.parametrize("counter", ["protocol", "fund", "creator"])
def test_collection_instruction_binding(counter):
    _, ctx, _ = load_case()
    initial = replace(ctx.state, creator=(1, 2)) if counter == "creator" else ctx.state
    expected, amounts = collect_fees(initial, counter, (U64, U64))
    accounts = [
        "owner",
        "authority",
        ctx.pool_id,
        ctx.pool.config,
        *ctx.pool.vaults,
        *ctx.pool.mints,
        "user0",
        "user1",
        TOKEN,
        "token2022",
    ]
    if counter == "creator":
        accounts += ["associated", "system"]
    raw = discriminator("global:collect_" + counter + "_fee")
    if counter != "creator":
        raw += struct.pack("<2Q", U64, U64)
    node = Invocation(dict(programId=PROGRAM, accounts=accounts, data=b58encode(raw)), 1)
    for i in range(2):
        if amounts[i]:
            node.children.append(
                transfer_node(accounts[4 + i], accounts[8 + i], ctx.pool.mints[i], amounts[i])
            )
    actual, _, _ = transition(ctx, initial, node)
    assert actual == expected
    assert actual.reserves == initial.reserves


def test_incomplete_journal_cannot_pass_protocol_audit(tmp_path):
    from test_collection import setup_store

    store, _ = setup_store(tmp_path / "pilot.sqlite", ending=(10000, 10000))
    try:
        report = audit_protocol(store)
        assert not report["all_intervals_consistent"]
        assert not report["transaction_execution_qualified"]
        assert not store.db.in_transaction
    finally:
        store.close()


def test_foreign_noop_router_is_not_counted_as_a_swap():
    _, ctx, txs = load_case()
    tx = copy.deepcopy(txs[0])
    router = "RouterNoop"
    tx["transaction"]["message"]["instructions"] = [
        dict(programId=router, accounts=[ctx.pool_id, *ctx.pool.vaults], data="1")
    ]
    tx["meta"]["innerInstructions"] = []
    tx["meta"]["postTokenBalances"] = copy.deepcopy(tx["meta"]["preTokenBalances"])
    tx["meta"]["logMessages"] = [f"Program {router} invoke [1]", f"Program {router} success"]
    state, detail = replay_transaction(ctx, ctx.state, tx)
    assert state == ctx.state
    assert detail == dict(failed=False, operations=[])


def test_unknown_pool_instruction_cannot_be_treated_as_noop():
    _, ctx, txs = load_case()
    tx = copy.deepcopy(txs[0])
    node = target_node(ctx, tx)
    node.instruction["data"] = b58encode(discriminator("global:unknown_admin"))
    with pytest.raises(ValidationError, match="unsupported pool instruction"):
        replay_transaction(ctx, ctx.state, tx)


def test_unattributed_vault_transfer_cannot_be_treated_as_noop():
    _, ctx, txs = load_case()
    tx = copy.deepcopy(txs[0])
    tx["meta"]["postTokenBalances"] = copy.deepcopy(tx["meta"]["preTokenBalances"])
    ix = transfer_node(ctx.pool.vaults[0], ctx.pool.vaults[0], ctx.pool.mints[0], 1).instruction
    tx["transaction"]["message"]["instructions"] = [ix]
    tx["meta"]["innerInstructions"] = []
    tx["meta"]["logMessages"] = [f"Program {TOKEN} invoke [1]", f"Program {TOKEN} success"]
    with pytest.raises(ValidationError, match="unattributed"):
        replay_transaction(ctx, ctx.state, tx)
