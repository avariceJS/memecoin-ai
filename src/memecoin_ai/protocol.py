"""Replay economic state from a checkpoint; observations are checks, never state patches."""

from __future__ import annotations

import base64
import re
import struct
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from memecoin_ai.collection import PilotStore
from memecoin_ai.cpmm import U64, EconomicState, Fees, collect_fees, liquidity, u64
from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.raydium import (
    PROGRAM,
    SOURCE_REVISION,
    TOKEN,
    TOKEN_2022,
    PoolState,
    account_bytes,
    b58decode,
    b58encode,
    decode_pool,
    discriminator,
)
from memecoin_ai.reconciliation import _audit_pilot, vault_transition
from memecoin_ai.sources import Capture
from memecoin_ai.tokens import TransferFee, decode_supported_checkpoint, transfer_swap

COLLECTIONS: dict[str, Literal["protocol", "fund", "creator"]] = {
    "collect_protocol_fee": "protocol",
    "collect_fund_fee": "fund",
    "collect_creator_fee": "creator",
}

KINDS = {
    discriminator("global:" + name): name
    for name in (
        "swap_base_input",
        "swap_base_output",
        "deposit",
        "withdraw",
        "collect_protocol_fee",
        "collect_fund_fee",
        "collect_creator_fee",
    )
}


@dataclass(frozen=True)
class Checkpoint:
    pool_id: str
    pool: PoolState
    fees: Fees
    state: EconomicState
    lp_mint: str
    structural_hash: str
    config_hash: str
    token_fees: tuple[TransferFee, TransferFee] = (TransferFee(), TransferFee())
    epoch: int | None = None
    withheld: tuple[int, int] = (0, 0)
    mint_hashes: tuple[str, ...] = ()
    fee_mints: tuple[bool, ...] = ()


def checkpoint(cap: Capture, pool_id: str) -> Checkpoint:
    checked = decode_supported_checkpoint(cap, pool_id)
    result = cap.result()
    if not isinstance(result, dict):
        raise ValidationError("invalid checkpoint result")
    accounts = result["value"]
    pool = decode_pool(accounts[0])
    raw = account_bytes(accounts[0], PROGRAM, 637)
    lp, p0, p1, f0, f1, _, _ = struct.unpack_from("<7Q", raw, 333)
    c0, c1 = struct.unpack_from("<2Q", raw, 397)
    state = EconomicState(tuple(checked["vault_balances"]), (p0, p1), (f0, f1), (c0, c1), lp)
    # Keep the old checkpoint schema immutable. Epoch/oracle state is outside this adapter.
    structural = raw[:333] + raw[373:381] + raw[389:397] + raw[413:]
    return Checkpoint(
        pool_id,
        pool,
        Fees(**checked["fees"]),
        state,
        b58encode(raw[136:168]),
        digest(structural),
        digest(account_bytes(accounts[1], PROGRAM, 236)),
        (
            TransferFee(**checked["mint_terms"][0]["transfer_fee"]),
            TransferFee(**checked["mint_terms"][1]["transfer_fee"]),
        )
        if checked["schema_version"] == 2
        else (TransferFee(), TransferFee()),
        checked.get("clock_epoch"),
        tuple(checked.get("vault_withheld", [0, 0])),
        tuple(m["economic_hash"] for m in checked.get("mint_terms", [])),
        tuple(1 in m["extensions"] for m in checked.get("mint_terms", [])),
    )


@dataclass
class Invocation:
    instruction: dict[str, Any]
    depth: int
    children: list[Invocation] = field(default_factory=list)
    events: list[bytes] = field(default_factory=list)


def flatten(nodes: list[Invocation]) -> list[Invocation]:
    result = []
    for node in nodes:
        result.append(node)
        result.extend(flatten(node.children))
    return result


def invocations(tx: dict[str, Any]) -> list[Invocation]:
    outer = tx["transaction"]["message"]["instructions"]
    groups = tx["meta"]["innerInstructions"]
    if not isinstance(groups, list):
        raise ValidationError("missing inner instruction recording")
    by_index = {}
    for group in groups:
        index = group["index"]
        if type(index) is not int or not 0 <= index < len(outer) or index in by_index:
            raise ValidationError("ambiguous inner instruction group")
        by_index[index] = group["instructions"]
    roots = []
    for index, ix in enumerate(outer):
        root = Invocation(ix, 1)
        roots.append(root)
        stack = [root]
        for inner in by_index.get(index, []):
            depth = inner.get("stackHeight")
            if type(depth) is not int or not 2 <= depth <= 64:
                raise ValidationError("missing/invalid CPI depth")
            while stack and stack[-1].depth >= depth:
                stack.pop()
            if not stack or stack[-1].depth != depth - 1:
                raise ValidationError("CPI depth discontinuity")
            node = Invocation(inner, depth)
            stack[-1].children.append(node)
            stack.append(node)
    tracked = [
        n for n in flatten(roots) if n.instruction["programId"] in (PROGRAM, TOKEN, TOKEN_2022)
    ]
    # Runtime log prefixes (not `Program log:` strings) attribute Anchor events to invocations.
    log_stack: list[tuple[str, Invocation | None]] = []
    cursor = 0
    logs = tx["meta"].get("logMessages")
    if not isinstance(logs, list):
        raise ValidationError("missing invocation logs")
    for line in logs:
        match = re.fullmatch(r"Program (\w+) invoke \[(\d+)\]", line)
        if match:
            program, depth_text = match.groups()
            depth = int(depth_text)
            if depth != len(log_stack) + 1:
                raise ValidationError("log depth discontinuity")
            current = None
            if program in (PROGRAM, TOKEN, TOKEN_2022):
                if (
                    cursor >= len(tracked)
                    or tracked[cursor].depth != depth
                    or tracked[cursor].instruction["programId"] != program
                ):
                    raise ValidationError("logs/instructions invocation mismatch")
                current = tracked[cursor] if program == PROGRAM else None
                cursor += 1
            log_stack.append((program, current))
        elif re.fullmatch(r"Program \w+ success", line):
            if not log_stack or log_stack[-1][0] != line.split()[1]:
                raise ValidationError("unbalanced invocation logs")
            log_stack.pop()
        elif re.match(r"Program \w+ failed:", line):
            # A caught failed CPI can roll back an inner subtree. Do not apply its events.
            raise ValidationError("caught CPI failure unsupported")
        elif line.startswith("Program data: ") and log_stack and log_stack[-1][1] is not None:
            current = log_stack[-1][1]
            assert current is not None
            current.events.append(base64.b64decode(line[14:], validate=True))
        elif "Log truncated" in line:
            raise ValidationError("truncated invocation logs")
    if log_stack or cursor != len(tracked):
        raise ValidationError("incomplete invocation logs")
    return roots


def transfer(node: Invocation) -> tuple[str, str, str, int]:
    ix = node.instruction
    parsed = ix.get("parsed", {})
    if (
        ix["programId"] not in (TOKEN, TOKEN_2022)
        or parsed.get("type") != "transferChecked"
        or node.children
    ):
        raise ValidationError("unsupported vault transfer instruction")
    info = parsed["info"]
    text = info["tokenAmount"]["amount"]
    if not isinstance(text, str) or not text.isascii() or not text.isdecimal():
        raise ValidationError("invalid transfer amount")
    amount = int(text)
    if not 0 <= amount <= U64:
        raise ValidationError("transfer amount overflow")
    return info["source"], info["destination"], info["mint"], amount


def swap_event(node: Invocation, expected: dict[str, Any]) -> None:
    if len(node.events) != 1:
        raise ValidationError("missing/ambiguous swap event")
    raw = node.events[0]
    if len(raw) != 170 or raw[:8] != discriminator("event:SwapEvent"):
        raise ValidationError("unsupported swap event layout")
    if raw[88] not in (0, 1) or raw[169] not in (0, 1):
        raise ValidationError("invalid event boolean")
    values = struct.unpack_from("<6Q", raw, 40)
    trade, creator = struct.unpack_from("<2Q", raw, 153)
    actual = dict(
        pool=b58encode(raw[8:40]),
        reserves=list(values[:2]),
        amounts=list(values[2:4]),
        transfer_fees=list(values[4:]),
        exact_input=bool(raw[88]),
        mints=[b58encode(raw[89:121]), b58encode(raw[121:153])],
        trade_fee=trade,
        creator_fee=creator,
        creator_on_input=bool(raw[169]),
    )
    if actual != expected:
        raise ValidationError("swap event disagrees with independently computed quote")


def lp_event(
    node: Invocation,
    ctx: Checkpoint,
    state: EconomicState,
    amounts: tuple[int, int],
    deposit: bool,
    transfer_fees: tuple[int, int] = (0, 0),
) -> None:
    if len(node.events) != 1:
        raise ValidationError("missing/ambiguous liquidity event")
    raw = node.events[0]
    if (
        len(raw) != 97
        or raw[:8] != discriminator("event:LpChangeEvent")
        or b58encode(raw[8:40]) != ctx.pool_id
        or struct.unpack_from("<7Q", raw, 40)
        != (state.lp_supply, *state.reserves, *amounts, *transfer_fees)
        or raw[96] != (0 if deposit else 1)
    ):
        raise ValidationError("liquidity event disagrees with transition")


def transition(
    ctx: Checkpoint,
    state: EconomicState,
    node: Invocation,
) -> tuple[EconomicState, dict[str, Any], list[Invocation]]:
    ix = node.instruction
    raw = b58decode(ix["data"])
    kind = KINDS.get(raw[:8])
    if kind is None:
        raise ValidationError("unsupported pool instruction")
    accounts = ix["accounts"]
    expected_transfers: list[tuple[str, str, str, int]] = []
    summary: dict[str, Any] = dict(kind=kind, quote_checked=False)
    withheld_delta = [0, 0]
    if kind.startswith("swap_"):
        if len(raw) != 24 or len(accounts) < 13 or accounts[3] != ctx.pool_id:
            raise ValidationError("unsupported swap layout")
        direction = ctx.pool.vaults.index(accounts[6])
        other = 1 - direction
        if (
            accounts[2] != ctx.pool.config
            or accounts[7] != ctx.pool.vaults[other]
            or accounts[8:10]
            != [ctx.pool.token_programs[direction], ctx.pool.token_programs[other]]
            or accounts[10:12] != [ctx.pool.mints[direction], ctx.pool.mints[other]]
            or ctx.pool.status & 4
        ):
            raise ValidationError("swap account/status mismatch")
        first, second = struct.unpack_from("<2Q", raw, 8)
        exact_input = kind == "swap_base_input"
        quote, gross_in, net_out, input_fee, output_fee = transfer_swap(
            state,
            ctx.fees,
            direction,
            first if exact_input else second,
            second if exact_input else first,
            exact_input=exact_input,
            creator_fee_on=ctx.pool.creator_fee_on,
            creator_enabled=ctx.pool.enable_creator_fee,
            token_fees=ctx.token_fees,
        )
        swap_event(
            node,
            dict(
                pool=ctx.pool_id,
                reserves=[state.reserves[direction], state.reserves[other]],
                amounts=[quote.input_amount, quote.output_amount],
                transfer_fees=[input_fee, output_fee],
                exact_input=exact_input,
                mints=accounts[10:12],
                trade_fee=quote.trade_fee,
                creator_fee=quote.creator_fee,
                creator_on_input=quote.creator_on_input,
            ),
        )
        expected_transfers = [
            (accounts[4], accounts[6], accounts[10], gross_in),
            (accounts[7], accounts[5], accounts[11], quote.output_amount),
        ]
        withheld_delta[direction] = input_fee
        after = quote.after
        summary.update(
            quote_checked=True,
            input_index=direction,
            quote={k: v for k, v in asdict(quote).items() if k != "after"},
        )
        if ctx.epoch is not None:
            summary["token_transfer"] = dict(
                gross_input=gross_in, net_output=net_out, input_fee=input_fee, output_fee=output_fee
            )
    elif kind in ("deposit", "withdraw"):
        deposit = kind == "deposit"
        if (
            len(raw) != 32
            or len(accounts) != (13 if deposit else 14)
            or accounts[2] != ctx.pool_id
            or accounts[6:8] != list(ctx.pool.vaults)
            or accounts[10:12] != list(ctx.pool.mints)
            or accounts[12] != ctx.lp_mint
            or ctx.pool.status & (1 if deposit else 2)
        ):
            raise ValidationError("liquidity account/status mismatch")
        lp, limit0, limit1 = struct.unpack_from("<3Q", raw, 8)
        after, amounts = liquidity(state, lp, (U64, U64) if deposit else (0, 0), deposit=deposit)
        gross = tuple(
            ctx.token_fees[i].gross(amounts[i]) if deposit else amounts[i] for i in range(2)
        )
        transfer_fees = tuple(ctx.token_fees[i].fee(gross[i]) for i in range(2))
        net = tuple(gross[i] - transfer_fees[i] for i in range(2))
        if any(
            (gross[i] > limit if deposit else net[i] < limit)
            for i, limit in enumerate((limit0, limit1))
        ):
            raise ValidationError("liquidity net/gross slippage limit")
        lp_event(
            node,
            ctx,
            state,
            amounts if deposit else (net[0], net[1]),
            deposit,
            (transfer_fees[0], transfer_fees[1]),
        )
        if deposit:
            withheld_delta = list(transfer_fees)
        for i in range(2):
            source, dest = (
                (accounts[4 + i], accounts[6 + i])
                if deposit
                else (accounts[6 + i], accounts[4 + i])
            )
            expected_transfers.append((source, dest, ctx.pool.mints[i], gross[i]))
        lp_calls = [
            c
            for c in node.children
            if c.instruction.get("parsed", {}).get("type") in ("mintTo", "burn")
        ]
        if len(lp_calls) != 1:
            raise ValidationError("missing/ambiguous LP mint/burn")
        lp_ix = lp_calls[0].instruction
        parsed = lp_ix["parsed"]
        if (
            lp_ix["programId"] != TOKEN
            or parsed["type"] != ("mintTo" if deposit else "burn")
            or parsed["info"]["mint"] != ctx.lp_mint
            or parsed["info"]["amount"] != str(lp)
        ):
            raise ValidationError("LP token instruction mismatch")
        summary.update(lp_amount=lp, token_amounts=amounts)
    else:
        counter = COLLECTIONS[kind]
        creator = counter == "creator"
        if (
            len(raw) != (8 if creator else 24)
            or len(accounts) != (14 if creator else 12)
            or accounts[2] != ctx.pool_id
            or accounts[3] != ctx.pool.config
            or accounts[4:6] != list(ctx.pool.vaults)
            or accounts[6:8] != list(ctx.pool.mints)
            or node.events
        ):
            raise ValidationError("fee collection account/layout mismatch")
        requested = (U64, U64) if creator else struct.unpack_from("<2Q", raw, 8)
        after, amounts = collect_fees(state, counter, requested)
        expected_transfers = [
            (accounts[4 + i], accounts[8 + i], ctx.pool.mints[i], amounts[i])
            for i in range(2)
            if amounts[i]
        ]
        summary.update(token_amounts=amounts)
    children = flatten(node.children)
    vault_calls = [
        c for c in children if any(v in canonical(c.instruction) for v in ctx.pool.vaults)
    ]
    actual = [transfer(c) for c in vault_calls]
    for child, item in zip(vault_calls, actual, strict=True):
        if child.instruction["programId"] != ctx.pool.token_programs[ctx.pool.mints.index(item[2])]:
            raise ValidationError("vault transfer token program mismatch")
    if ctx.epoch is not None:
        summary["vault_withheld_delta"] = withheld_delta
    if actual != expected_transfers:
        raise ValidationError("CPI transfers disagree with computed transition")
    return after, summary, vault_calls


def replay_transaction(
    ctx: Checkpoint,
    state: EconomicState,
    tx: dict[str, Any],
    *,
    withheld: tuple[int, int] | None = None,
) -> tuple[EconomicState, dict[str, Any]]:
    observations = [
        vault_transition(tx, v, m, p)
        for v, m, p in zip(ctx.pool.vaults, ctx.pool.mints, ctx.pool.token_programs, strict=True)
    ]
    for i, observation in enumerate(observations):
        if observation is not None and observation[0] != state.vaults[i]:
            raise ValidationError("transaction pre-balance disagrees with reconstruction")
    if tx["meta"]["err"] is not None:
        return state, dict(failed=True, operations=[])
    tracked_withheld = list(ctx.withheld if withheld is None else withheld)
    for value in tracked_withheld:
        u64(value)
    roots = invocations(tx)
    nodes = flatten(roots)
    consumed: set[int] = set()
    operations = []
    for node in nodes:
        ix = node.instruction
        if ix["programId"] == PROGRAM and ctx.pool_id in ix.get("accounts", []):
            if not set(ix["accounts"]) <= {
                k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]
            }:
                raise ValidationError("instruction account absent from transaction keys")
            # blockTime is an RPC estimate, not a captured Clock sysvar: only a diagnostic gate.
            is_swap = KINDS.get(b58decode(ix["data"])[:8], "").startswith("swap_")
            if is_swap and (
                type(tx.get("blockTime")) is not int or tx["blockTime"] < ctx.pool.open_time
            ):
                raise ValidationError("missing/early block timestamp")
            state, result, transfers = transition(ctx, state, node)
            consumed.update(id(n) for n in transfers)
            consumed.add(id(node))
            operations.append(result)
            for i, delta in enumerate(result.get("vault_withheld_delta", [0, 0])):
                tracked_withheld[i] = u64(tracked_withheld[i] + delta)
        elif (
            ix["programId"] == TOKEN_2022
            and ix.get("parsed", {}).get("type") == "harvestWithheldTokensToMint"
        ):
            info = ix["parsed"]["info"]
            sources = info["sourceAccounts"]
            touched = [i for i, vault in enumerate(ctx.pool.vaults) if vault in sources]
            if not touched:
                continue
            if (
                ctx.epoch is None
                or node.children
                or not isinstance(sources, list)
                or not all(isinstance(v, str) for v in sources)
                or not {info["mint"], *sources}
                <= {k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]}
            ):
                raise ValidationError("invalid/unattributable withheld harvest")
            delta = [0, 0]
            for i in touched:
                if (
                    info["mint"] != ctx.pool.mints[i]
                    or not ctx.fee_mints[i]
                    or ctx.pool.token_programs[i] != TOKEN_2022
                ):
                    raise ValidationError("harvest mint/extension mismatch")
                delta[i] = -tracked_withheld[i]
                tracked_withheld[i] = 0
            consumed.add(id(node))
            operations.append(
                dict(
                    kind="harvest_withheld_to_mint",
                    quote_checked=False,
                    vault_withheld_delta=delta,
                    scope="tracked vaults only; other sources and mint total unobserved",
                )
            )
    for node in nodes:
        if id(node) in consumed:
            continue
        ix = node.instruction
        # Foreign routers cannot mutate classic SPL data without a Token CPI.
        # Read-only/no-op routers can legitimately mention vaults without any CPI.
        if ix["programId"] in (TOKEN, TOKEN_2022) and any(
            v in canonical(ix) for v in ctx.pool.vaults
        ):
            raise ValidationError("unattributed vault operation")
    for i, observation in enumerate(observations):
        if observation is not None and observation[1] != state.vaults[i]:
            raise ValidationError("transaction post-balance disagrees with reconstruction")
    return state, dict(failed=False, operations=operations)


def audit_protocol(store: PilotStore) -> dict[str, Any]:
    if store.db.in_transaction:
        raise ValidationError("protocol audit requires its own read transaction")
    store.db.execute("BEGIN")
    try:
        base = _audit_pilot(store)
        checkpoints = {
            r["id"]: checkpoint(store.capture(r["capture_id"]), r["pool"])
            for r in store.db.execute("SELECT * FROM checkpoints ORDER BY id")
        }
        reports = []
        for interval in base["intervals"]:
            left = checkpoints[interval["start_checkpoint"]]
            right = checkpoints[interval["end_checkpoint"]]
            state = left.state
            withheld = list(left.withheld)
            blockers = list(interval["blockers"])
            steps = []
            if (
                left.structural_hash != right.structural_hash
                or left.config_hash != right.config_hash
            ):
                blockers.append("pool/config structural change unsupported")
            if left.epoch != right.epoch or left.mint_hashes != right.mint_hashes:
                blockers.append("epoch boundary or mint economic terms changed; segment required")
            if not blockers:
                for step in interval["steps"]:
                    row = store.db.execute(
                        "SELECT capture_id FROM transactions WHERE signature=?",
                        (step["signature"],),
                    ).fetchone()
                    tx = store.capture(row[0]).result()
                    if not isinstance(tx, dict):
                        raise ValidationError("invalid transaction result")
                    try:
                        state, details = replay_transaction(
                            left, state, tx, withheld=(withheld[0], withheld[1])
                        )
                        for op in details["operations"]:
                            for i, delta in enumerate(op.get("vault_withheld_delta", [0, 0])):
                                withheld[i] += delta
                        steps.append(
                            dict(
                                signature=step["signature"],
                                slot=step["slot"],
                                transaction_index=step["transaction_index"],
                                **details,
                            )
                        )
                    except (ValidationError, ValueError, KeyError, TypeError, IndexError) as exc:
                        blockers.append(f"{step['signature']}:{type(exc).__name__}:{exc}")
                        break  # No endpoint-based repair after an unexplained transition.
            if tuple(withheld) != right.withheld:
                blockers.append("terminal withheld balance mismatch")
            if state != right.state:
                blockers.append("terminal economic state mismatch")
            reports.append(
                dict(
                    pool_id=left.pool_id,
                    start_slot=interval["start_slot"],
                    end_slot=interval["end_slot"],
                    reconstructed=asdict(state),
                    observed=asdict(right.state),
                    steps=steps,
                    consistency_passed=not blockers,
                    blockers=blockers,
                )
            )
            if left.epoch is not None:
                reports[-1].update(
                    reconstructed_withheld=withheld,
                    observed_withheld=list(right.withheld),
                    epoch=left.epoch,
                )
        paths = (
            "protocol.py",
            "cpmm.py",
            "tokens.py",
            "raydium.py",
            "reconciliation.py",
            "collection.py",
            "sources.py",
            "domain.py",
        )
        steps = [s for r in reports for s in r["steps"]]
        operations = [o for s in steps for o in s["operations"]]
        return dict(
            schema_version=1,
            counts=dict(
                transactions=len(steps),
                failed=sum(s["failed"] for s in steps),
                successful_no_pool_operation=sum(
                    not s["failed"] and not s["operations"] for s in steps
                ),
                operations=dict(Counter(o["kind"] for o in operations)),
                creator_fee_swaps=sum(
                    o.get("quote", {}).get("creator_fee", 0) > 0 for o in operations
                ),
            ),
            source_revision=SOURCE_REVISION,
            decoder_hashes={p: digest(Path(__file__).with_name(p).read_bytes()) for p in paths},
            input_audit_sha256=digest(canonical(base).encode()),
            all_intervals_consistent=base["all_intervals_consistent"]
            and all(r["consistency_passed"] for r in reports),
            intervals=reports,
            economic_scope="vaults, protocol/fund/creator counters, internal LP supply",
            full_pool_state_reconstructed=False,
            market_dataset_qualified=False,
            transaction_execution_qualified=False,
            independent_provider_verified=False,
            assumptions=[
                "provider address index complete",
                "config and mint economic terms unchanged between checkpoints",
            ],
            exclusions=[
                "non-allowlisted Token-2022 extensions",
                "oracle and recent_epoch",
                "admin/config transitions",
                "mint-wide withheld total and direct withheld withdrawals",
                "initialization",
                "caught CPI failures",
                "unattributed vault changes",
                "Clock sysvar/admission/account permissions",
                "future inclusion/latency/MEV",
                "network/priority fees/rent",
                "deployed binary equivalence",
            ],
        )
    finally:
        store.db.rollback()


def write_protocol_audit(store: PilotStore, output: Path) -> dict[str, Any]:
    report = audit_protocol(store)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(canonical(report) + "\n")
    return report
