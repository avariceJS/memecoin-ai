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


def test_token2022_program_binding_and_net_transfer_evidence():
    # Adapt a real classic transaction to an explicitly synthetic metadata-only Token-2022 mint.
    # Zero transfer fees must preserve its economics while requiring the new owner/program binding.
    _, ctx, txs = load_case()
    from memecoin_ai.raydium import TOKEN_2022

    tx = copy.deepcopy(txs[0])
    ctx = replace(ctx, pool=replace(ctx.pool, token_programs=(TOKEN, TOKEN_2022)), epoch=1)
    mint = ctx.pool.mints[1]
    node = target_node(ctx, tx)
    direction = ctx.pool.vaults.index(node.instruction["accounts"][6])
    node.instruction["accounts"][8 + (1 - direction)] = TOKEN_2022
    keys = tx["transaction"]["message"]["accountKeys"]
    if not any(k["pubkey"] == TOKEN_2022 for k in keys):
        keys.append(dict(pubkey=TOKEN_2022))
    child = next(c for c in node.children if c.instruction["parsed"]["info"]["mint"] == mint)
    child.instruction["programId"] = TOKEN_2022
    # Replace exactly this token CPI runtime invocation, preserving other route instructions.
    logs = tx["meta"]["logMessages"]
    cpmm_start = next(
        i for i, s in enumerate(logs) if s == f"Program {PROGRAM} invoke [{node.depth}]"
    )
    token_starts = [
        i
        for i in range(cpmm_start + 1, len(logs))
        if logs[i] == f"Program {TOKEN} invoke [{node.depth + 1}]"
    ]
    selected = token_starts[1 - direction]
    end = next(i for i in range(selected, len(logs)) if logs[i] == f"Program {TOKEN} success")
    for i in range(selected, end + 1):
        logs[i] = logs[i].replace(TOKEN, TOKEN_2022)
    for side in ("preTokenBalances", "postTokenBalances"):
        for balance in tx["meta"][side]:
            if balance["mint"] == mint:
                balance["programId"] = TOKEN_2022
    state, detail = replay_transaction(ctx, ctx.state, tx)
    assert detail["operations"][0]["vault_withheld_delta"] == [0, 0]
    assert state != ctx.state
    child.instruction["programId"] = TOKEN
    with pytest.raises(ValidationError):
        replay_transaction(ctx, ctx.state, tx)


@pytest.mark.parametrize("fault", ["epoch", "mint-base", "withheld"])
def test_token2022_interval_blocks_unexplained_non_reserve_changes(tmp_path, fault):
    from memecoin_ai.collection import PilotStore
    from memecoin_ai.domain import canonical
    from memecoin_ai.reconciliation import audit_pilot
    from memecoin_ai.sources import ReadOnlyRPC
    from memecoin_ai.tokens import decode_supported_checkpoint

    root = Path(__file__).parent / "fixtures" / "token2022"
    start = Capture.load(root / "checkpoint-1")
    pool = json.loads(start.request_json)["params"][0][0]
    raw = json.loads(start.response)
    slot = raw["result"]["context"]["slot"] + 1
    raw["result"]["context"]["slot"] = slot
    values = raw["result"]["value"]
    clock = bytearray(base64.b64decode(values[6]["data"][0]))
    struct.pack_into("<Q", clock, 0, slot)
    if fault == "epoch":
        struct.pack_into("<Q", clock, 16, struct.unpack_from("<Q", clock, 16)[0] + 1)
    values[6]["data"][0] = base64.b64encode(clock).decode()
    if fault != "epoch":
        index, offset = (5, 36) if fault == "mint-base" else (3, 170)
        data = bytearray(base64.b64decode(values[index]["data"][0]))
        struct.pack_into("<Q", data, offset, struct.unpack_from("<Q", data, offset)[0] + 1)
        values[index]["data"][0] = base64.b64encode(data).decode()
    end = replace(start, response=canonical(raw).encode(), received_ns=start.received_ns + 1)
    store = PilotStore(tmp_path / "pilot.sqlite")
    try:
        store.configure([pool], start.source_alias, 100, token2022=True)
        store.checkpoint(store.save_capture(start), pool)
        store.checkpoint(store.save_capture(end), pool)
        clock_times = iter(range(start.received_ns + 2, start.received_ns + 100))

        def transport(endpoint, body, timeout, limit):
            return canonical(
                dict(
                    jsonrpc="2.0",
                    id=1,
                    result=[
                        dict(signature="boundary", slot=slot - 1, confirmationStatus="finalized")
                    ],
                )
            ).encode()

        client = ReadOnlyRPC(
            "https://example.invalid",
            start.source_alias,
            transport=transport,
            clock=lambda: next(clock_times),
        )
        for scan in store.db.execute("select * from scans").fetchall():
            cap = client.fetch(
                "getSignaturesForAddress",
                [scan["address"], dict(commitment="finalized", minContextSlot=slot, limit=100)],
            )
            store.apply_page(scan["id"], store.save_capture(cap))
        assert audit_pilot(store)["all_intervals_consistent"]
        report = audit_protocol(store)
        assert not report["all_intervals_consistent"]
        reason = "withheld" if fault == "withheld" else "epoch boundary or mint"
        assert any(reason in b for b in report["intervals"][0]["blockers"])
        if fault == "withheld":
            decoded = decode_supported_checkpoint(end, pool)
            assert (
                decoded["vault_balances"]
                == decode_supported_checkpoint(start, pool)["vault_balances"]
            )
    finally:
        store.close()


def test_harvest_uses_current_withheld_once_without_changing_reserves():
    from memecoin_ai.raydium import TOKEN_2022

    cap = Capture.load(Path(__file__).parent / "fixtures" / "token2022" / "checkpoint-2")
    pool = json.loads(cap.request_json)["params"][0][0]
    ctx = checkpoint(cap, pool)
    keys = [*ctx.pool.vaults, *ctx.pool.mints, TOKEN_2022]
    balances = [
        dict(
            accountIndex=i,
            mint=ctx.pool.mints[i],
            programId=ctx.pool.token_programs[i],
            uiTokenAmount=dict(amount=str(ctx.state.vaults[i])),
        )
        for i in range(2)
    ]
    ix = dict(
        programId=TOKEN_2022,
        parsed=dict(
            type="harvestWithheldTokensToMint",
            info=dict(mint=ctx.pool.mints[1], sourceAccounts=[ctx.pool.vaults[1]]),
        ),
    )
    tx = dict(
        transaction=dict(
            message=dict(
                accountKeys=[dict(pubkey=k) for k in keys], instructions=[ix, copy.deepcopy(ix)]
            )
        ),
        meta=dict(
            err=None,
            preTokenBalances=balances,
            postTokenBalances=copy.deepcopy(balances),
            innerInstructions=[],
            logMessages=[f"Program {TOKEN_2022} invoke [1]", f"Program {TOKEN_2022} success"] * 2,
        ),
    )
    state, detail = replay_transaction(ctx, ctx.state, tx, withheld=(0, 123))
    assert state == ctx.state
    assert [o["vault_withheld_delta"] for o in detail["operations"]] == [[0, -123], [0, 0]]
    assert all(not o["quote_checked"] for o in detail["operations"])
    tx["meta"]["err"] = {"InstructionError": [0, "fixture"]}
    assert replay_transaction(ctx, ctx.state, tx, withheld=(0, 123))[1]["operations"] == []
    tx["meta"]["err"] = None
    ix["parsed"]["info"]["mint"] = ctx.pool.mints[0]
    with pytest.raises(ValidationError, match="harvest mint"):
        replay_transaction(ctx, ctx.state, tx, withheld=(0, 123))
