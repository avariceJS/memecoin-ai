"""Diagnostic-only economic reconciliation for a narrow PumpSwap sell layout.

This decoder deliberately admits only one pinned legacy Token sell invocation, its exact
documented fee-recipient suffix, an exact SellEvent, and a complete checked-transfer subtree.
It never grants training or execution eligibility.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

from .decoder import EVENT_TAG, PROGRAM, TOKEN, TOKEN2022, decode58, event_payload
from .normalize import (
    NormalizationError,
    decode_token_transfer_checked,
    normalize_compiled_transaction,
)
from .schema import SCHEMA

ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
# The suffix roles and eight-recipient set are pinned to the official Pump docs below;
# the historical AMM IDL supplies the unchanged 21-account sell prefix.
PDA_MARKER = b"ProgramDerivedAddress"
FEE_RECIPIENTS_UPGRADE_2026_04_28 = frozenset(
    {
        "5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD",
        "9M4giFFMxmFGXtc3feFzRai56WbBqehoSeRE5GK7gf7",
        "GXPFM2caqTtQYC2cJ5yJRi9VDkpsYZXzYdwYpGnLmtDL",
        "3BpXnfJaUTiwXnJNe7Ej1rcbzqTTQUvLShZaWazebsVR",
        "5cjcW9wExnJJiqgLjq7DEG75Pm6JBgE1hNv4B2vHXUW6",
        "EHAAiTxcdDwQ3U4bU6YcMsQGaekdzLS3B5SmYo46kJtL",
        "5eHhjP8JaYkz83CWwvGU2uMUXefd3AazWGx4gpcuEEYD",
        "A7hAgCzFw14fejgCp387JUJRMNyz4j89JKnhtKU8piqW",
    }
)
FEE_RECIPIENT_SOURCE = (
    "https://github.com/pump-fun/pump-public-docs/blob/"
    "7de0b959fa2bdab379a2f75f5433d3de1e35d229/docs/BREAKING_FEE_RECIPIENT.md"
)


class EconomicDiagnosticError(ValueError):
    """A concrete evidence or support boundary for the compiled diagnostic."""


def _decode_pubkey(value: str) -> bytes:
    raw = cast(bytes, decode58(value))
    if len(raw) != 32:
        raise EconomicDiagnosticError("public_key_length")
    return raw


def _ed25519_compressed_point_is_on_curve(encoded: bytes) -> bool:
    if len(encoded) != 32:
        return False
    prime = 2**255 - 19
    y = int.from_bytes(encoded, "little") & ((1 << 255) - 1)
    sign = encoded[31] >> 7
    if y >= prime:
        return False
    d = (-121665 * pow(121666, prime - 2, prime)) % prime
    y2 = y * y % prime
    denominator = (d * y2 + 1) % prime
    if denominator == 0:
        return False
    x2 = (y2 - 1) * pow(denominator, prime - 2, prime) % prime
    x = pow(x2, (prime + 3) // 8, prime)
    if (x * x - x2) % prime:
        sqrt_m1 = pow(2, (prime - 1) // 4, prime)
        x = x * sqrt_m1 % prime
    if (x * x - x2) % prime:
        return False
    return not (x == 0 and sign == 1)


def _create_program_address(seeds: list[bytes], program_id: str) -> bytes:
    if len(seeds) > 16 or any(len(seed) > 32 for seed in seeds):
        raise EconomicDiagnosticError("pda_seed_bounds")
    import hashlib

    address = hashlib.sha256(b"".join(seeds) + _decode_pubkey(program_id) + PDA_MARKER).digest()
    if _ed25519_compressed_point_is_on_curve(address):
        raise EconomicDiagnosticError("pda_on_curve")
    return address


def _find_program_address(seeds: list[bytes], program_id: str) -> bytes:
    for bump in range(255, -1, -1):
        try:
            return _create_program_address([*seeds, bytes([bump])], program_id)
        except EconomicDiagnosticError as exc:
            if str(exc) != "pda_on_curve":
                raise
    raise EconomicDiagnosticError("pda_bump_not_found")


def associated_token_address(owner: str, mint: str, token_program: str = TOKEN) -> str:
    """Derive the canonical ATA using Solana's PDA and ed25519 curve rules."""
    from .decoder import encode58

    address = _find_program_address(
        [_decode_pubkey(owner), _decode_pubkey(token_program), _decode_pubkey(mint)],
        ASSOCIATED_TOKEN_PROGRAM,
    )
    return cast(str, encode58(address))


def _transaction_tree(normalized: dict[str, Any]) -> list[dict[str, Any]]:
    outer = normalized["outer_instructions"]
    nodes = [dict(instruction=ix, children=[]) for ix in outer]
    groups: set[int] = set()
    for group in normalized["raw_inner_groups"]:
        outer_index = group.get("index")
        if (
            type(outer_index) is not int
            or not 0 <= outer_index < len(nodes)
            or outer_index in groups
        ):
            raise EconomicDiagnosticError("inner_group_index")
        groups.add(outer_index)
        stack: list[tuple[int, dict[str, Any]]] = [(1, nodes[outer_index])]
        for n, _raw in enumerate(group["instructions"]):
            ix = next(
                (
                    item
                    for item in normalized["inner_instructions"]
                    if item["path"] == [outer_index, n]
                ),
                None,
            )
            if ix is None:
                raise EconomicDiagnosticError("inner_instruction_resolution")
            depth = ix["stack_height"]
            while stack and stack[-1][0] >= depth:
                stack.pop()
            if not stack or depth != stack[-1][0] + 1:
                raise EconomicDiagnosticError("inner_stack_height_gap")
            node = dict(instruction=ix, children=[])
            stack[-1][1]["children"].append(node)
            stack.append((depth, node))
    return nodes


def _flatten(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield node
    for child in node["children"]:
        yield from _flatten(child)


def _is_writable(transaction: dict[str, Any], normalized: dict[str, Any], pubkey: str) -> bool:
    matches = [account for account in normalized["accounts"] if account["pubkey"] == pubkey]
    if len(matches) != 1:
        raise EconomicDiagnosticError("account_index_ambiguous")
    account = matches[0]
    if account["source"] == "loaded_writable":
        return True
    if account["source"] == "loaded_readonly":
        return False
    message = transaction["transaction"]["message"]
    header = message.get("header")
    static_count = len(message["accountKeys"])
    if not isinstance(header, dict):
        raise EconomicDiagnosticError("message_header_missing")
    required = header.get("numRequiredSignatures")
    readonly_signed = header.get("numReadonlySignedAccounts")
    readonly_unsigned = header.get("numReadonlyUnsignedAccounts")
    if any(type(value) is not int for value in (required, readonly_signed, readonly_unsigned)):
        raise EconomicDiagnosticError("message_header_invalid")
    required = cast(int, required)
    readonly_signed = cast(int, readonly_signed)
    readonly_unsigned = cast(int, readonly_unsigned)
    index = cast(int, account["index"])
    if index < required:
        return index < required - readonly_signed
    return index < static_count - readonly_unsigned


def _balance_map(
    normalized: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    accounts = normalized["accounts"]
    maps = []
    for field_name in ("pre_token_balances", "post_token_balances"):
        rows = normalized[field_name]
        if not isinstance(rows, list):
            raise EconomicDiagnosticError("token_balance_trace_unavailable")
        result = {}
        for row in rows:
            if not isinstance(row, dict):
                raise EconomicDiagnosticError("token_balance_row_invalid")
            index = row.get("accountIndex")
            if type(index) is not int or not 0 <= index < len(accounts):
                raise EconomicDiagnosticError("token_balance_index")
            key = accounts[index]["pubkey"]
            amount = row.get("uiTokenAmount", {}).get("amount")
            decimals = row.get("uiTokenAmount", {}).get("decimals")
            if (
                key in result
                or not isinstance(amount, str)
                or not amount.isascii()
                or not amount.isdecimal()
                or type(decimals) is not int
                or not 0 <= decimals <= 255
                or not isinstance(row.get("mint"), str)
                or not isinstance(row.get("owner"), str)
                or not isinstance(row.get("programId"), str)
            ):
                raise EconomicDiagnosticError("token_balance_row_invalid")
            result[key] = {
                "mint": row["mint"],
                "owner": row["owner"],
                "program": row["programId"],
                "amount": int(amount),
                "decimals": decimals,
            }
        maps.append(result)
    return maps[0], maps[1]


def _balance_deltas(
    transfers: list[dict[str, Any]], pre: dict[str, Any], post: dict[str, Any]
) -> dict[str, dict[str, int | str | None]]:
    expected: dict[str, int] = {}
    for transfer in transfers:
        for account, sign in ((transfer["source"], -1), (transfer["destination"], 1)):
            expected[account] = expected.get(account, 0) + sign * transfer["amount_base_units"]
    result: dict[str, dict[str, int | str | None]] = {}
    for account, delta in expected.items():
        before, after = pre.get(account), post.get(account)
        if before is None or after is None:
            result[account] = {
                "status": "unknown_balance_side_missing",
                "expected_transfer_delta": delta,
                "observed_balance_delta": None,
            }
            continue
        if (
            before["mint"] != after["mint"]
            or before["owner"] != after["owner"]
            or before["program"] != after["program"]
            or before["decimals"] != after["decimals"]
        ):
            result[account] = {
                "status": "unknown_token_account_identity_changed",
                "expected_transfer_delta": delta,
                "observed_balance_delta": after["amount"] - before["amount"],
            }
            continue
        observed = after["amount"] - before["amount"]
        result[account] = {
            "status": "exact" if observed == delta else "unknown_other_transaction_effects",
            "expected_transfer_delta": delta,
            "observed_balance_delta": observed,
        }
    return result


def _diagnose(transaction: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_compiled_transaction(transaction)
    if normalized["meta_error"] is not None:
        raise EconomicDiagnosticError("failed_transaction")
    schema = cast(dict[str, Any], SCHEMA)
    instruction_profile = schema["instructions"]["sell"]
    discriminator = bytes(instruction_profile["discriminator"])
    instructions = [*normalized["outer_instructions"], *normalized["inner_instructions"]]
    pump_calls = [
        ix
        for ix in instructions
        if ix["program_id"] == PROGRAM and bytes.fromhex(ix["data_hex"])[:8] != EVENT_TAG
    ]
    if len(pump_calls) != 1:
        raise EconomicDiagnosticError("pump_instruction_count")
    instruction = pump_calls[0]
    data = bytes.fromhex(instruction["data_hex"])
    if data[:8] != discriminator:
        raise EconomicDiagnosticError("instruction_variant_unsupported")
    base_names = instruction_profile["accounts"]
    if len(instruction["accounts"]) != len(base_names) + 2 or len(data) != 24:
        raise EconomicDiagnosticError("instruction_layout")
    roles = dict(zip(base_names, instruction["accounts"][: len(base_names)], strict=True))
    fee_recipient, fee_ata = instruction["accounts"][-2:]
    market_token_accounts = (
        roles["user_base_token_account"],
        roles["pool_base_token_account"],
        roles["user_quote_token_account"],
        roles["pool_quote_token_account"],
    )
    if len(set(market_token_accounts)) != len(market_token_accounts):
        raise EconomicDiagnosticError("market_token_account_role_alias")
    if roles["program"] != PROGRAM:
        raise EconomicDiagnosticError("program_role")
    if roles["base_token_program"] != TOKEN or roles["quote_token_program"] != TOKEN:
        raise EconomicDiagnosticError("token_2022_or_unknown_program")
    if fee_recipient not in FEE_RECIPIENTS_UPGRADE_2026_04_28:
        raise EconomicDiagnosticError("fee_recipient_unrecognized")
    if _is_writable(transaction, normalized, fee_recipient):
        raise EconomicDiagnosticError("fee_recipient_writable")
    if not _is_writable(transaction, normalized, fee_ata):
        raise EconomicDiagnosticError("fee_ata_readonly")
    if associated_token_address(fee_recipient, roles["quote_mint"]) != fee_ata:
        raise EconomicDiagnosticError("fee_ata_derivation")

    tree = _transaction_tree(normalized)
    target = next(
        node
        for root in tree
        for node in _flatten(root)
        if node["instruction"]["path"] == instruction["path"]
    )
    descendants = [child for child in _flatten(target) if child is not target]
    events = []
    parse_event: Any = event_payload
    transfers = []
    for node in descendants:
        ix = node["instruction"]
        if ix["program_id"] == PROGRAM:
            wire = bytes.fromhex(ix["data_hex"])
            if wire.startswith(EVENT_TAG):
                events.append(parse_event(wire))
        if ix["program_id"] in (TOKEN, TOKEN2022):
            transfer = decode_token_transfer_checked(ix)
            if transfer["program_id"] != TOKEN or transfer["additional_signers"]:
                raise EconomicDiagnosticError("token_2022_or_multisig_unqualified")
            transfers.append(transfer)
    if len(events) != 1:
        raise EconomicDiagnosticError("event_count_or_unavailable")
    event = events[0]
    if not event["exact"] or event["name"] != "SellEvent":
        raise EconomicDiagnosticError("sell_event_shape")
    fields = event["fields"]
    for name in (
        "pool",
        "user",
        "user_base_token_account",
        "user_quote_token_account",
        "protocol_fee_recipient",
        "protocol_fee_recipient_token_account",
    ):
        if fields.get(name) != roles[name]:
            raise EconomicDiagnosticError("event_role_mismatch")
    if fields.get("cashback", 0) != 0 or fields.get("creator_fee_unclaimed", 0) != 0:
        raise EconomicDiagnosticError("cashback_or_unclaimed_creator_fee_unqualified")
    if any(
        type(fields.get(name)) is not int or not 0 <= fields[name] <= 10_000
        for name in ("lp_fee_basis_points", "protocol_fee_basis_points", "buyback_fee_basis_points")
    ):
        raise EconomicDiagnosticError("fee_basis_points_out_of_range")
    if roles["base_mint"] == roles["quote_mint"]:
        raise EconomicDiagnosticError("identical_mints")
    if not transfers:
        raise EconomicDiagnosticError("transfer_trace_unavailable")
    if any(t["mint"] not in (roles["base_mint"], roles["quote_mint"]) for t in transfers):
        raise EconomicDiagnosticError("foreign_transfer_mint")

    pre, post = _balance_map(normalized)
    for transfer in transfers:
        if transfer["amount_base_units"] <= 0:
            raise EconomicDiagnosticError("zero_or_negative_transfer")
        if transfer["source"] == transfer["destination"]:
            raise EconomicDiagnosticError("self_transfer")
        mint = transfer["mint"]
        if transfer["program_id"] != TOKEN:
            raise EconomicDiagnosticError("token_program_unqualified")
        mint_pool_account = (
            roles["pool_base_token_account"]
            if mint == roles["base_mint"]
            else roles["pool_quote_token_account"]
        )
        if pre[mint_pool_account]["decimals"] != transfer["decimals"]:
            raise EconomicDiagnosticError("transfer_checked_historical_mint_decimals")
        for account in (transfer["source"], transfer["destination"]):
            for balance in (pre.get(account), post.get(account)):
                if balance is not None and (
                    balance["mint"] != mint
                    or balance["program"] != TOKEN
                    or balance["decimals"] != transfer["decimals"]
                ):
                    raise EconomicDiagnosticError("transfer_mint_program_or_decimals_mismatch")
        if transfer["mint"] == roles["base_mint"]:
            if (
                transfer["source"] != roles["user_base_token_account"]
                or transfer["destination"] != roles["pool_base_token_account"]
                or transfer["authority"] != roles["user"]
            ):
                raise EconomicDiagnosticError("base_transfer_role_or_authority")
        elif (
            transfer["source"] != roles["pool_quote_token_account"]
            or transfer["authority"] != roles["pool"]
        ):
            raise EconomicDiagnosticError("quote_transfer_role_or_authority")
    balance_evidence = _balance_deltas(transfers, pre, post)

    def matching(*, mint: str, source: str, destination: str) -> list[dict[str, Any]]:
        return [
            t
            for t in transfers
            if t["mint"] == mint and t["source"] == source and t["destination"] == destination
        ]

    base_in = matching(
        mint=roles["base_mint"],
        source=roles["user_base_token_account"],
        destination=roles["pool_base_token_account"],
    )
    if len(base_in) != 1 or base_in[0]["amount_base_units"] != fields["base_amount_in"]:
        raise EconomicDiagnosticError("base_transfer_reconciliation")
    if len([t for t in transfers if t["mint"] == roles["base_mint"]]) != 1:
        raise EconomicDiagnosticError("extra_base_transfer")
    gross = fields["quote_amount_out"]
    lp_fee = fields["lp_fee"]
    protocol_fee = fields["protocol_fee"]
    creator_fee = fields["coin_creator_fee"]
    buyback_fee = fields["buyback_fee"]
    user_out = fields["user_quote_amount_out"]
    no_lp = fields["quote_amount_out_without_lp_fee"]
    if lp_fee > gross or buyback_fee > protocol_fee:
        raise EconomicDiagnosticError("fee_amount_out_of_range")
    quote_mint = roles["quote_mint"]
    quote_transfers = [transfer for transfer in transfers if transfer["mint"] == quote_mint]
    pool_quote_out = [
        t for t in quote_transfers if t["source"] == roles["pool_quote_token_account"]
    ]
    quote_destinations = (
        roles["user_quote_token_account"],
        roles["protocol_fee_recipient_token_account"],
        roles["coin_creator_vault_ata"],
        fee_ata,
    )
    if len(set(quote_destinations)) != len(quote_destinations):
        raise EconomicDiagnosticError("quote_fee_destination_alias")
    if roles["pool_quote_token_account"] in quote_destinations:
        raise EconomicDiagnosticError("pool_quote_destination_alias")
    expected_destinations = {
        roles["user_quote_token_account"]: user_out,
        roles["protocol_fee_recipient_token_account"]: protocol_fee - buyback_fee,
        roles["coin_creator_vault_ata"]: creator_fee,
        fee_ata: buyback_fee,
    }
    actual_by_destination: dict[str, int] = {}
    for transfer in pool_quote_out:
        actual_by_destination[transfer["destination"]] = (
            actual_by_destination.get(transfer["destination"], 0) + transfer["amount_base_units"]
        )
    expected_positive = {key: value for key, value in expected_destinations.items() if value > 0}
    if actual_by_destination != expected_positive:
        raise EconomicDiagnosticError("quote_recipient_transfer_reconciliation")
    if any(t["source"] != roles["pool_quote_token_account"] for t in quote_transfers):
        raise EconomicDiagnosticError("unexpected_quote_transfer_source")
    pool_debit = sum(t["amount_base_units"] for t in pool_quote_out)
    pool_state = {}
    for account in (roles["pool_base_token_account"], roles["pool_quote_token_account"]):
        evidence = balance_evidence.get(account)
        if evidence is None or evidence["status"] != "exact":
            raise EconomicDiagnosticError("pool_token_balance_reconciliation_unknown")
        pool_state[account] = evidence["observed_balance_delta"]
    if pool_state[roles["pool_base_token_account"]] != fields["base_amount_in"]:
        raise EconomicDiagnosticError("pool_base_signed_delta_mismatch")
    if pool_state[roles["pool_quote_token_account"]] != -pool_debit:
        raise EconomicDiagnosticError("pool_quote_signed_delta_mismatch")

    bps_equations = {
        "hypothesis_lp_ceil_fee_from_gross_basis_points": lp_fee
        == (gross * fields["lp_fee_basis_points"] + 9_999) // 10_000,
        "hypothesis_protocol_ceil_fee_from_gross_basis_points": (
            protocol_fee == (gross * fields["protocol_fee_basis_points"] + 9_999) // 10_000
        ),
        "hypothesis_buyback_floor_split_from_protocol_basis_points": (
            buyback_fee == protocol_fee * fields["buyback_fee_basis_points"] // 10_000
        ),
    }
    equations = {
        "gross_less_lp_equals_quote_without_lp": gross - lp_fee == no_lp,
        "quote_without_lp_less_fees_equals_user_output": (
            no_lp - protocol_fee - creator_fee == user_out
        ),
        "pool_quote_debit_equals_quote_without_lp": pool_debit == no_lp,
        **bps_equations,
    }
    if not all(equations.values()):
        raise EconomicDiagnosticError("sell_event_fee_equation_mismatch")
    if fields["min_quote_amount_out"] != int.from_bytes(data[16:24], "little"):
        raise EconomicDiagnosticError("instruction_minimum_event_mismatch")
    if fields["base_amount_in"] != int.from_bytes(data[8:16], "little"):
        raise EconomicDiagnosticError("instruction_base_event_mismatch")
    fee_ata_pre, fee_ata_post = pre.get(fee_ata), post.get(fee_ata)
    if buyback_fee and (fee_ata_pre is None or fee_ata_post is None):
        raise EconomicDiagnosticError("fee_ata_balance_unavailable")
    if fee_ata_pre is not None and (
        fee_ata_pre["mint"] != quote_mint or fee_ata_pre["owner"] != fee_recipient
    ):
        raise EconomicDiagnosticError("fee_ata_historical_identity")
    if fee_ata_post is not None and (
        fee_ata_post["mint"] != quote_mint or fee_ata_post["owner"] != fee_recipient
    ):
        raise EconomicDiagnosticError("fee_ata_post_identity")
    if (
        fee_ata_pre is not None
        and fee_ata_post is not None
        and (fee_ata_pre["program"] != TOKEN or fee_ata_post["program"] != TOKEN)
    ):
        raise EconomicDiagnosticError("fee_ata_program")
    fee_ata_balance = balance_evidence.get(fee_ata)
    if buyback_fee and (fee_ata_balance is None or fee_ata_balance["status"] != "exact"):
        raise EconomicDiagnosticError("fee_ata_balance_delta")

    all_account_balances_exact = all(
        evidence["status"] == "exact" for evidence in balance_evidence.values()
    )
    balance_unknown_count = sum(
        evidence["status"] != "exact" for evidence in balance_evidence.values()
    )
    result = {
        "contract": "pumpswap_v1_sell23_fee_recipient_suffix_economic_diagnostic_v1",
        "status": "reconciled_diagnostic_only" if all_account_balances_exact else "quarantined",
        "reason": None if all_account_balances_exact else "economic_account_balances_unknown",
        "admission": "none",
        "training_eligible": False,
        "execution_supported": False,
        "transfer_fact_state": "exact_checked_inner_transfer_and_event_equations",
        "account_settlement_state": "exact" if all_account_balances_exact else "unknown_partial",
        "fee_equation_status": "empirical_diagnostic_hypotheses_not_protocol_proof",
        "economic_process": (
            "PumpSwap sell, legacy SPL Token, exact pinned account prefix plus "
            "fee-recipient/quote-ATA suffix"
        ),
        "event_availability": "exact_transaction_inner_instruction",
        "coverage_scope": (
            "single_successful_transaction; no market or slot range completeness claim"
        ),
        "price_scope": (
            "pool trade transfer only; excludes network fees, rent, and sibling route legs"
        ),
        "quantities": {
            "base_amount_in": fields["base_amount_in"],
            "gross_quote_amount_out": gross,
            "lp_fee": lp_fee,
            "protocol_fee": protocol_fee,
            "creator_fee": creator_fee,
            "buyback_fee": buyback_fee,
            "user_quote_amount_out": user_out,
            "pool_quote_debit": pool_debit,
            "trade_transfer_price_numerator_quote_base_units": user_out,
            "trade_transfer_price_denominator_base_base_units": fields["base_amount_in"],
            "quote_decimals": pre[roles["pool_quote_token_account"]]["decimals"],
            "base_decimals": pre[roles["pool_base_token_account"]]["decimals"],
            "wallet_net_price": None,
            "wallet_net_price_state": "unknown_not_reconstructed",
        },
        "equations": equations,
        "fee_recipient_set": "pump_fee_recipient_upgrade_2026_04_28",
        "fee_recipient": fee_recipient,
        "fee_ata": fee_ata,
        "transfer_count": len(transfers),
        "balance_evidence": {
            "pool_token_accounts": "exact_pre_post_transfer_reconciled",
            "buyback_fee_ata": "exact_pre_post_transfer_reconciled"
            if buyback_fee
            else "no_buyback_transfer_exact_event_zero",
            "other_touched_token_accounts": {
                "exact": sum(row["status"] == "exact" for row in balance_evidence.values()),
                "unknown": balance_unknown_count,
            },
        },
    }
    return result


def diagnose_pumpswap_sell(transaction: Any) -> dict[str, Any]:
    """Return exact diagnostic facts or a stable quarantine category; never admits rows."""
    try:
        return _diagnose(transaction)
    except NormalizationError as exc:
        reason = exc.category
    except (EconomicDiagnosticError, ValueError) as exc:
        reason = str(exc) or "economic_diagnostic_invalid"
    except (KeyError, IndexError, TypeError, StopIteration):
        reason = "economic_diagnostic_schema"
    return {
        "contract": "pumpswap_v1_sell23_fee_recipient_suffix_economic_diagnostic_v1",
        "status": "quarantined",
        "admission": "none",
        "training_eligible": False,
        "execution_supported": False,
        "reason": reason,
    }
