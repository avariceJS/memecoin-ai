"""Strict ABI inspection plus independent integer transfer reconciliation (jsonParsed only)."""

from .schema import SCHEMA

PROGRAM = SCHEMA["address"]
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
EVENT_TAG = bytes.fromhex("e445a52e51cb9a1d")
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def decode58(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 12000:
        raise ValueError("base58_bound")
    n = 0
    for c in value:
        if c not in ALPHABET:
            raise ValueError("base58_character")
        n = n * 58 + ALPHABET.index(c)
    return bytes(len(value) - len(value.lstrip("1"))) + n.to_bytes((n.bit_length() + 7) // 8, "big")


def encode58(value):
    n, out = int.from_bytes(value, "big"), ""
    while n:
        n, k = divmod(n, 58)
        out = ALPHABET[k] + out
    return "1" * (len(value) - len(value.lstrip(b"\0"))) + out


def event_payload(data):
    """An unknown suffix is retained and rejected, never silently discarded."""
    if data[:8] != EVENT_TAG:
        raise ValueError("event_tag")
    name = next((k for k, v in SCHEMA["events"].items() if data[8:16] == bytes(v)), None)
    if name is None:
        raise ValueError("event_discriminator")
    offset, values = 16, {}
    for key, typ in SCHEMA["fields"][name]:
        size = {"u64": 8, "i64": 8, "i128": 16, "bool": 1, "pubkey": 32}.get(typ)
        if typ == "string":
            if offset + 4 > len(data):
                raise ValueError("event_truncated")
            size = 4 + int.from_bytes(data[offset : offset + 4], "little")
            if size > 68:
                raise ValueError("event_string_bound")
        if size is None or offset + size > len(data):
            raise ValueError("event_truncated")
        part = data[offset : offset + size]
        if typ == "pubkey":
            value = encode58(part)
        elif typ == "string":
            value = part[4:].decode("utf8")
        else:
            value = int.from_bytes(part, "little", signed=typ.startswith("i"))
            if typ == "bool" and value not in (0, 1):
                raise ValueError("event_bool")
        values[key] = value
        offset += size
    return dict(
        name=name,
        fields=values,
        exact=offset == len(data),
        unknown_suffix_hex=data[offset:].hex(),
        consumed_bytes=offset,
        total_bytes=len(data),
    )


def tree(tx):
    """Use runtime stackHeight, not log text or nearest preceding swap."""
    outer = tx["transaction"]["message"]["instructions"]
    nodes = [dict(ix=i, path=[n], depth=1, children=[]) for n, i in enumerate(outer)]
    seen = set()
    for group in tx["meta"]["innerInstructions"]:
        index = group["index"]
        if type(index) is not int or not 0 <= index < len(nodes) or index in seen:
            raise ValueError("inner_group_index")
        seen.add(index)
        stack = [nodes[index]]
        for n, ix in enumerate(group["instructions"]):
            depth = ix.get("stackHeight")
            if type(depth) is not int or depth < 2:
                raise ValueError("missing_stack_height")
            while stack and stack[-1]["depth"] >= depth:
                stack.pop()
            if not stack or depth != stack[-1]["depth"] + 1:
                raise ValueError("stack_height_gap")
            node = dict(ix=ix, path=[index, n], depth=depth, children=[])
            stack[-1]["children"].append(node)
            stack.append(node)
    return nodes


def flatten(nodes):
    for n in nodes:
        yield n
        yield from flatten(n["children"])


def balances(tx):
    keys = tx["transaction"]["message"]["accountKeys"]
    keys = [k["pubkey"] if isinstance(k, dict) else k for k in keys]
    result = []
    for field in ["preTokenBalances", "postTokenBalances"]:
        out = {}
        for b in tx["meta"][field]:
            idx = b["accountIndex"]
            if type(idx) is not int or not 0 <= idx < len(keys) or keys[idx] in out:
                raise ValueError("balance_index")
            a = b["uiTokenAmount"]
            out[keys[idx]] = dict(
                mint=b["mint"], amount=integer_amount(a["amount"]), decimals=a["decimals"]
            )
        result.append(out)
    return result


def integer_amount(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("invalid_integer_amount")
    n = int(value)
    if n > 2**64 - 1:
        raise ValueError("amount_overflow")
    return n


def transfers(node):
    out = []
    for child in node["children"]:
        ix = child["ix"]
        if ix.get("programId") not in (TOKEN, TOKEN2022):
            continue
        parsed = ix.get("parsed", {})
        if parsed.get("type") != "transferChecked":
            raise ValueError("unsupported_token_instruction")
        info = parsed["info"]
        out.append(
            dict(
                source=info["source"],
                destination=info["destination"],
                mint=info["mint"],
                authority=info["authority"],
                amount=integer_amount(info["tokenAmount"]["amount"]),
                decimals=info["tokenAmount"]["decimals"],
                program=ix["programId"],
            )
        )
    return out


def movement(node, roles, kind, pre, post):
    ts = transfers(node)
    base, quote = roles["base_mint"], roles["quote_mint"]
    if base == quote:
        raise ValueError("identical_mints")
    ub, uq = roles["user_base_token_account"], roles["user_quote_token_account"]
    pb, pq = roles["pool_base_token_account"], roles["pool_quote_token_account"]
    if len({ub, uq, pb, pq}) != 4:
        raise ValueError("aliased_token_roles")
    src, dst = (ub, pb) if kind == "sell" else (pb, ub)
    bs = [t for t in ts if t["source"] == src and t["destination"] == dst and t["mint"] == base]
    if len(bs) != 1 or bs[0]["amount"] <= 0:
        raise ValueError("base_transfer_count")
    for t in ts:
        if t["mint"] not in (base, quote):
            raise ValueError("foreign_transfer_mint")
        vault, mint, program = (
            (pb, base, roles["base_token_program"])
            if t["mint"] == base
            else (pq, quote, roles["quote_token_program"])
        )
        if t["program"] != program or t["decimals"] != pre[vault]["decimals"]:
            raise ValueError("token_program_or_decimals")
        if t["mint"] == base and t != bs[0]:
            raise ValueError("extra_base_transfer")
        authority = roles["user"] if t["source"] in (ub, uq) else roles["pool"]
        if t["authority"] != authority:
            raise ValueError("transfer_authority")
        if t["mint"] == quote and t["source"] != (pq if kind == "sell" else uq):
            raise ValueError("quote_source")
        if pre[vault]["mint"] != mint or post[vault]["mint"] != mint:
            raise ValueError("vault_mint")
        if pre[vault]["decimals"] != post[vault]["decimals"]:
            raise ValueError("vault_decimals_changed")

    def delta(account):
        return sum(
            t["amount"] * ((t["destination"] == account) - (t["source"] == account)) for t in ts
        )

    q_user, q_pool = delta(uq), delta(pq)
    if (kind == "sell" and not q_user > 0 > q_pool) or (kind != "sell" and not q_pool > 0 > q_user):
        raise ValueError("quote_direction")
    return dict(
        base_amount=bs[0]["amount"],
        quote_user_amount=abs(q_user),
        quote_vault_amount=abs(q_pool),
        base_decimals=bs[0]["decimals"],
        quote_decimals=pre[pq]["decimals"],
        external_quote_fees=abs(q_user + q_pool),
        vault_deltas={pb: delta(pb), pq: delta(pq)},
        transfers=ts,
    )


def decode(tx):
    result = dict(
        swaps=[],
        unknown_instructions=[],
        errors=[],
        training_eligible=False,
        execution_supported=False,
        protocol_qualified=False,
    )
    if tx is None:
        return dict(result, status="unavailable")
    if tx.get("version") not in ("legacy", 0) or not isinstance(tx.get("meta"), dict):
        return dict(result, status="unsupported_transaction")
    if tx["meta"].get("err") is not None:
        return dict(result, status="failed_transaction")
    try:
        nodes = list(flatten(tree(tx)))
        pre, post = balances(tx)
    except (ValueError, KeyError, TypeError) as exc:
        return dict(
            result,
            status="unsupported_trace",
            errors=[str(exc) if isinstance(exc, ValueError) else "trace_schema"],
        )
    bydisc = {bytes(v["discriminator"]): k for k, v in SCHEMA["instructions"].items()}
    for node in nodes:
        ix = node["ix"]
        if ix.get("programId") != PROGRAM:
            continue
        try:
            data = decode58(ix.get("data"))
            if data[:8] == EVENT_TAG:
                continue
            kind = bydisc.get(data[:8])
            if kind is None:
                result["unknown_instructions"].append(node["path"])
                continue
            swap = dict(path=node["path"], kind=kind, blockers=[], movement=None, event=None)
            result["swaps"].append(swap)
            names = SCHEMA["instructions"][kind]["accounts"]
            accounts = ix["accounts"]
            if len(accounts) < len(names) or len(data) not in (
                (24,) if kind == "sell" else (24, 25)
            ):
                raise ValueError("instruction_layout")
            if len(data) == 25 and data[24] not in (0, 1):
                raise ValueError("instruction_option_bool")
            roles = dict(zip(names, accounts, strict=False))
            swap["roles"] = roles
            if len(accounts) != len(names):
                swap["blockers"].append("unreviewed_remaining_accounts")
            if roles["base_token_program"] != TOKEN or roles["quote_token_program"] != TOKEN:
                swap["blockers"].append("token2022_extensions_unqualified")
            if roles["program"] != PROGRAM:
                raise ValueError("program_role")
            evnodes = [c for c in node["children"] if c["ix"].get("programId") == PROGRAM]
            if len(evnodes) != 1:
                raise ValueError("event_count")
            evix = evnodes[0]["ix"]
            if evix["accounts"] != [roles["event_authority"]]:
                raise ValueError("event_authority")
            ev = event_payload(decode58(evix["data"]))
            swap["event"] = ev
            if not ev["exact"]:
                swap["blockers"].append("unknown_event_suffix")
            fields = ev["fields"]
            if ev["name"] != ("SellEvent" if kind == "sell" else "BuyEvent"):
                raise ValueError("event_side")
            for role in [
                "pool",
                "user",
                "user_base_token_account",
                "user_quote_token_account",
                "protocol_fee_recipient",
                "protocol_fee_recipient_token_account",
            ]:
                if fields[role] != roles[role]:
                    raise ValueError("event_role_mismatch")
            m = movement(node, roles, kind, pre, post)
            swap["movement"] = m
            amount, limit = (
                int.from_bytes(data[8:16], "little"),
                int.from_bytes(data[16:24], "little"),
            )
            if kind == "buy_exact_quote_in":
                args_ok = m["quote_user_amount"] <= amount and m["base_amount"] >= limit
            elif kind == "buy":
                args_ok = m["base_amount"] == amount and m["quote_user_amount"] <= limit
            else:
                args_ok = m["base_amount"] == amount and m["quote_user_amount"] >= limit
            if not args_ok:
                raise ValueError("instruction_transfer_mismatch")
            bfield, qfield = (
                ("base_amount_in", "user_quote_amount_out")
                if kind == "sell"
                else ("base_amount_out", "user_quote_amount_in")
            )
            if fields[bfield] != m["base_amount"] or fields[qfield] != m["quote_user_amount"]:
                swap["blockers"].append("event_transfer_mismatch")
        except (ValueError, KeyError, TypeError) as exc:
            category = str(exc) if isinstance(exc, ValueError) else "instruction_schema"
            result["errors"].append(dict(path=node["path"], category=category))
    deltas = {}
    for s in result["swaps"]:
        if s["movement"]:
            for k, v in s["movement"]["vault_deltas"].items():
                deltas[k] = deltas.get(k, 0) + v
    mismatches = [k for k, v in deltas.items() if post[k]["amount"] - pre[k]["amount"] != v]
    result["vault_balance_reconciled"] = bool(deltas) and not mismatches and not result["errors"]
    if mismatches:
        result["errors"].append(dict(category="vault_delta_mismatch", accounts=mismatches))
    result["status"] = (
        "reconciled_candidate"
        if result["vault_balance_reconciled"]
        and not result["unknown_instructions"]
        and all(not s["blockers"] for s in result["swaps"])
        else "quarantined"
        if result["swaps"] or result["errors"]
        else "no_supported_swap"
    )
    return result
