"""Instruction-scoped PumpSwap buyExact three-tail diagnostic ledger.

This contract is restricted to the separately qualified pool-v2 three-tail prefix
class. It does not reuse the v1 two-tail suffix classifier or qualify fee semantics.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .compiled_economics import _balance_map, _flatten, _transaction_tree
from .decoder import EVENT_TAG, PROGRAM, TOKEN, TOKEN2022, event_payload
from .normalize import decode_token_transfer_checked, normalize_compiled_transaction
from .schema import SCHEMA

CONTRACT = "pumpswap_buy_exact_quote_in_pool_v2_three_tail_instruction_token_ledger_v1"
EVENT_ROLE_FIELDS = (
    "pool",
    "user",
    "user_base_token_account",
    "user_quote_token_account",
    "protocol_fee_recipient",
    "protocol_fee_recipient_token_account",
)
TOKEN_ACCOUNT_ROLES = (
    "user_base_token_account",
    "user_quote_token_account",
    "pool_base_token_account",
    "pool_quote_token_account",
    "protocol_fee_recipient_token_account",
    "coin_creator_vault_ata",
)
U64_MAX = (1 << 64) - 1


def _quarantined(reason: str, **details: Any) -> dict[str, Any]:
    return {"contract": CONTRACT, "state": "quarantined", "reason": reason, **details}


def _censored(reason: str, **details: Any) -> dict[str, Any]:
    return {"contract": CONTRACT, "state": "censored", "reason": reason, **details}


def _signed_transfer_deltas(transfers: list[dict[str, Any]]) -> dict[str, int]:
    """Account deltas in atomic units, conserving each transfer across endpoints."""
    deltas: dict[str, int] = defaultdict(int)
    for transfer in transfers:
        amount = transfer.get("amount_base_units")
        source, destination = transfer.get("source"), transfer.get("destination")
        if (
            type(amount) is not int
            or not 0 < amount <= U64_MAX
            or not isinstance(source, str)
            or not isinstance(destination, str)
            or source == destination
        ):
            raise ValueError("transfer_amount_or_endpoint_invalid")
        deltas[source] -= amount
        deltas[destination] += amount
    return dict(deltas)


def inspect_pool_v2_buy_exact(transaction: Any, path: list[int]) -> dict[str, Any]:
    """Check exact candidate-subtree legacy token deltas without fee or wallet-net claims."""
    try:
        normalized = normalize_compiled_transaction(transaction)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return _quarantined("transaction_normalization_failed", detail=str(exc))
    if normalized.get("meta_error") is not None:
        return {
            "contract": CONTRACT,
            "state": "failed_attempt_only",
            "reason": "failed_transaction",
        }
    try:
        roots = _transaction_tree(normalized)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return _quarantined("transaction_invocation_tree_unavailable", detail=str(exc))
    nodes = [node for root in roots for node in _flatten(root)]
    candidates = [node for node in nodes if node["instruction"]["path"] == path]
    if len(candidates) != 1:
        return _quarantined("candidate_path_not_unique")
    candidate_node = candidates[0]
    candidate = candidate_node["instruction"]
    names = SCHEMA["instructions"]["buy_exact_quote_in"]["accounts"]
    if candidate["program_id"] != PROGRAM or len(candidate["accounts"]) != len(names) + 3:
        return _quarantined("candidate_three_tail_layout_mismatch")
    roles = dict(zip(names, candidate["accounts"][: len(names)], strict=True))
    pool_v2, fee_wallet, fee_quote_ata = candidate["accounts"][-3:]
    if roles["base_token_program"] != TOKEN or roles["quote_token_program"] != TOKEN:
        return _quarantined("token_2022_effects_unsupported")
    if roles["base_mint"] == roles["quote_mint"]:
        return _quarantined("identical_base_and_quote_mints")

    data = bytes.fromhex(candidate["data_hex"])
    if len(data) != 25 or data[:8] != bytes(
        SCHEMA["instructions"]["buy_exact_quote_in"]["discriminator"]
    ):
        return _quarantined("instruction_wire_shape_mismatch")
    spendable_quote_in = int.from_bytes(data[8:16], "little")
    min_base_out = int.from_bytes(data[16:24], "little")
    track_volume = data[24]
    if track_volume not in (0, 1):
        return _quarantined("track_volume_boolean_invalid")

    subtree_nodes = list(_flatten(candidate_node))
    descendants = [node["instruction"] for node in subtree_nodes]
    events = []
    transfers = []
    unsupported = []
    for instruction in descendants:
        wire = bytes.fromhex(instruction["data_hex"])
        if instruction["program_id"] == PROGRAM and wire.startswith(EVENT_TAG):
            try:
                events.append((instruction, event_payload(wire)))
            except (ValueError, KeyError, TypeError) as exc:
                unsupported.append({"path": instruction["path"], "reason": f"event:{exc}"})
        if instruction["program_id"] in (TOKEN, TOKEN2022):
            try:
                transfer = decode_token_transfer_checked(instruction)
            except (ValueError, KeyError, TypeError) as exc:
                unsupported.append({"path": instruction["path"], "reason": f"token:{exc}"})
                continue
            if transfer.get("program_id") != TOKEN or transfer.get("additional_signers"):
                unsupported.append(
                    {"path": instruction["path"], "reason": "non_legacy_or_multisig"}
                )
            transfers.append(transfer)
    if unsupported:
        return _quarantined("unsupported_candidate_subtree_effect", effects=unsupported)
    if len(events) != 1:
        return _quarantined("candidate_subtree_event_not_unique", event_count=len(events))
    event_instruction, event = events[0]
    if not event.get("exact") or event.get("name") != "BuyEvent":
        return _quarantined("candidate_subtree_buy_event_unqualified")
    fields = event["fields"]
    if any(fields.get(key) != roles.get(key) for key in EVENT_ROLE_FIELDS):
        return _quarantined("buy_event_role_mismatch")
    if (
        fields.get("ix_name") != "buy_exact_quote_in"
        or fields.get("track_volume") != bool(track_volume)
        or fields.get("max_quote_amount_in") != spendable_quote_in
        or fields.get("min_base_amount_out") != min_base_out
    ):
        return _quarantined("buy_event_instruction_arguments_mismatch")
    if not transfers:
        return _quarantined("candidate_subtree_token_transfers_missing")
    if any(t.get("mint") not in (roles["base_mint"], roles["quote_mint"]) for t in transfers):
        return _quarantined("foreign_mint_transfer_in_candidate_subtree")
    if any(t["amount_base_units"] <= 0 or t["source"] == t["destination"] for t in transfers):
        return _quarantined("zero_or_self_transfer")

    base = [t for t in transfers if t["mint"] == roles["base_mint"]]
    quote = [t for t in transfers if t["mint"] == roles["quote_mint"]]
    if (
        len(base) != 1
        or base[0]["source"] != roles["pool_base_token_account"]
        or base[0]["destination"] != roles["user_base_token_account"]
        or base[0]["authority"] != roles["pool"]
    ):
        return _quarantined("base_transfer_route_mismatch")
    allowed_quote_destinations = {
        roles["pool_quote_token_account"],
        roles["protocol_fee_recipient_token_account"],
        roles["coin_creator_vault_ata"],
        fee_quote_ata,
    }
    if not quote or any(
        t["source"] != roles["user_quote_token_account"]
        or t["authority"] != roles["user"]
        or t["destination"] not in allowed_quote_destinations
        for t in quote
    ):
        return _quarantined("quote_transfer_route_mismatch")

    try:
        deltas = _signed_transfer_deltas(transfers)
        before, after = _balance_map(normalized)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return _censored("token_endpoint_trace_unavailable", detail=str(exc))

    expected_owners = {
        roles["user_base_token_account"]: roles["user"],
        roles["user_quote_token_account"]: roles["user"],
        roles["pool_base_token_account"]: roles["pool"],
        roles["pool_quote_token_account"]: roles["pool"],
        roles["protocol_fee_recipient_token_account"]: roles["protocol_fee_recipient"],
        roles["coin_creator_vault_ata"]: roles["coin_creator_vault_authority"],
        fee_quote_ata: fee_wallet,
    }
    endpoint_rows = []
    for account, delta in sorted(deltas.items()):
        pre, post = before.get(account), after.get(account)
        if pre is None or post is None:
            return _censored("token_account_endpoint_missing", account=account)
        if (
            pre["mint"] != post["mint"]
            or pre["mint"] not in (roles["base_mint"], roles["quote_mint"])
            or pre["program"] != TOKEN
            or post["program"] != TOKEN
            or pre["decimals"] != post["decimals"]
            or type(pre["amount"]) is not int
            or type(post["amount"]) is not int
        ):
            return _quarantined("token_account_endpoint_identity_mismatch", account=account)
        expected_owner = expected_owners.get(account)
        if (
            expected_owner is None
            or pre["owner"] != expected_owner
            or post["owner"] != expected_owner
        ):
            return _quarantined(
                "legacy_token_account_owner_unverified",
                account=account,
                expected_owner=expected_owner,
                pre_owner=pre["owner"],
                post_owner=post["owner"],
            )
        observed_delta = post["amount"] - pre["amount"]
        if observed_delta != delta:
            return _censored(
                "transaction_boundary_delta_not_fully_attributed",
                account=account,
                observed_delta_atomic=observed_delta,
                candidate_transfer_delta_atomic=delta,
            )
        endpoint_rows.append(
            {
                "account": account,
                "mint": pre["mint"],
                "owner": pre["owner"],
                "decimals_metadata": pre["decimals"],
                "pre_amount_atomic": pre["amount"],
                "post_amount_atomic": post["amount"],
                "candidate_subtree_transfer_delta_atomic": delta,
            }
        )

    quote_debit = sum(t["amount_base_units"] for t in quote)
    base_credit = base[0]["amount_base_units"]
    checks = {
        "base_credit_equals_buy_event_base_amount_out": base_credit
        == fields.get("base_amount_out"),
        "gross_quote_debit_equals_buy_event_quote_amount_in": quote_debit
        == fields.get("quote_amount_in"),
        "gross_quote_debit_within_instruction_spendable_quote": quote_debit <= spendable_quote_in,
        "base_credit_meets_instruction_minimum": base_credit >= min_base_out,
    }
    if not all(checks.values()):
        return _quarantined("instruction_event_amount_equation_failed", checks=checks)
    return {
        "contract": CONTRACT,
        "state": "exact_candidate_subtree_token_settlement",
        "candidate_path": path,
        "event_path": event_instruction["path"],
        "pool_v2_pda": pool_v2,
        "fee_wallet": fee_wallet,
        "fee_quote_ata": fee_quote_ata,
        "realized_amounts_atomic_units": {
            "base_user_credit": base_credit,
            "quote_user_gross_debit": quote_debit,
            "base_unit": "atomic_units_of_base_mint",
            "quote_unit": "atomic_units_of_quote_mint",
            "ui_decimal_scaling_applied": False,
        },
        "endpoint_reconciliation": "all_candidate_touched_legacy_token_accounts_exact_and_owned",
        "account_endpoints": endpoint_rows,
        "event_amount_checks": checks,
        "fee_semantics": "hypotheses_only_not_qualified",
        "wallet_net_price": "unknown_not_computed",
        "admission": "none",
    }
