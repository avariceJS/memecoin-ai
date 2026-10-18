"""Historical pool-birth metadata. Exact pinned ABI, CPI binding and vault identities.

This lane admits registry metadata only; it never admits a swap or training target.
"""

from .birth_schema import PROFILES
from .decoder import EVENT_TAG, PROGRAM, balances, decode58, encode58, flatten, tree

AUTHORITY = "GS4CU59F31iL7aR2Q8zVS8DRrcRnXX1yjQ66TqNVQnaR"
GLOBAL = "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw"
CREATE = bytes.fromhex("e992d18ecf6840bc")


def fields(data, layout):
    offset, result = 0, {}
    for field in layout:
        name, typ = field["name"], field["type"]
        size = {"u8": 1, "u16": 2, "u64": 8, "i64": 8, "bool": 1, "pubkey": 32}.get(typ)
        if size is None or offset + size > len(data):
            raise ValueError("birth_truncated_or_type")
        part = data[offset : offset + size]
        value = (
            encode58(part)
            if typ == "pubkey"
            else int.from_bytes(part, "little", signed=typ == "i64")
        )
        if typ == "bool" and value not in (0, 1):
            raise ValueError("birth_bool")
        result[name] = value
        offset += size
    if offset != len(data):
        raise ValueError("birth_unknown_suffix")
    return result


def decode_births(tx):
    if tx["meta"]["err"] is not None:
        return dict(status="failed_transaction", births=[], errors=[])
    births, errors = [], []
    try:
        nodes = list(flatten(tree(tx)))
        _pre, post = balances(tx)
        keys = [
            k["pubkey"] if isinstance(k, dict) else k
            for k in tx["transaction"]["message"]["accountKeys"]
        ]
        for node in nodes:
            ix = node["ix"]
            if ix.get("programId") != PROGRAM or decode58(ix["data"])[:8] != CREATE:
                continue
            try:
                data = decode58(ix["data"])
                events = [c["ix"] for c in node["children"] if c["ix"].get("programId") == PROGRAM]
                if len(events) != 1 or events[0]["accounts"] != [AUTHORITY]:
                    raise ValueError("birth_event_binding")
                evdata = decode58(events[0]["data"])
                matched = []
                for profile in PROFILES:
                    if evdata[:16] != EVENT_TAG + bytes(profile["discriminator"]):
                        continue
                    try:
                        args = fields(data[8:], profile["args"])
                        event = fields(evdata[16:], profile["fields"])
                    except ValueError:
                        continue
                    if len(ix["accounts"]) != len(profile["accounts"]):
                        continue
                    matched.append((profile, args, event))
                if len(matched) != 1:
                    raise ValueError("birth_unknown_or_ambiguous_profile")
                profile, args, event = matched[0]
                roles = dict(zip(profile["accounts"], ix["accounts"], strict=True))
                if (
                    roles["program"] != PROGRAM
                    or roles["event_authority"] != AUTHORITY
                    or roles["global_config"] != GLOBAL
                ):
                    raise ValueError("birth_program_roles")
                for key in (
                    "pool",
                    "creator",
                    "base_mint",
                    "quote_mint",
                    "lp_mint",
                    "user_base_token_account",
                    "user_quote_token_account",
                ):
                    if event[key] != roles[key]:
                        raise ValueError("birth_event_roles")
                for key in set(args) & set(event):
                    if args[key] != event[key]:
                        raise ValueError("birth_argument_mismatch")
                pool_index = keys.index(roles["pool"])
                if (
                    tx["meta"]["preBalances"][pool_index] != 0
                    or tx["meta"]["postBalances"][pool_index] <= 0
                ):
                    raise ValueError("birth_existing_pool")
                if abs(event["timestamp"] - tx["blockTime"]) > 2:
                    raise ValueError("birth_timestamp")
                for side in ("base", "quote"):
                    vault = roles[f"pool_{side}_token_account"]
                    if (
                        post[vault]["mint"] != roles[f"{side}_mint"]
                        or post[vault]["decimals"] != event[f"{side}_mint_decimals"]
                    ):
                        raise ValueError("birth_vault_identity")
                births.append(
                    dict(
                        pool=roles["pool"],
                        base_mint=roles["base_mint"],
                        quote_mint=roles["quote_mint"],
                        vaults={s: roles[f"pool_{s}_token_account"] for s in ("base", "quote")},
                        decimals={s: event[f"{s}_mint_decimals"] for s in ("base", "quote")},
                        event_s=tx["blockTime"],
                        slot=tx["slot"],
                        path=node["path"],
                        signature=tx["transaction"]["signatures"][0],
                        profile_sha=profile["profile_sha"],
                        training_eligible=False,
                        execution_supported=False,
                        initial_amounts_reconciled=False,
                    )
                )
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(
                    dict(
                        path=node["path"],
                        category=str(exc) if isinstance(exc, ValueError) else "birth_schema",
                    )
                )
    except (ValueError, KeyError, TypeError):
        errors.append(dict(category="birth_trace_schema"))
    return dict(
        status="quarantined" if errors else "registry_metadata" if births else "no_birth",
        births=births if not errors else [],
        errors=errors,
    )
