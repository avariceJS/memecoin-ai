"""Diagnostic-only PumpSwap v1 buy-exact transfer and settlement facts."""

from __future__ import annotations

from typing import Any

from .buy_exact_v1 import inspect_buy_exact_quote_in_v1
from .compiled_economics import (
    EconomicDiagnosticError,
    _balance_map,
    _flatten,
    _transaction_tree,
)
from .decoder import EVENT_TAG, PROGRAM, TOKEN, TOKEN2022, event_payload
from .legacy_token_ledger import LEDGER_CONTRACT, reconcile_legacy_token_accounts
from .normalize import decode_token_transfer_checked, normalize_compiled_transaction
from .schema import SCHEMA

CONTRACT = "pumpswap_v1_buy_exact_quote_in_full_transaction_diagnostic_v1"
_EVENT_ROLE_FIELDS = (
    "pool",
    "user",
    "user_base_token_account",
    "user_quote_token_account",
    "protocol_fee_recipient",
    "protocol_fee_recipient_token_account",
)


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "status": "quarantined",
        "reason": reason,
        "references": [],
        "settlement_state": "unknown_partial",
        "training_eligible": False,
        "execution_supported": False,
        "admission": "none",
    }


def _sum_delta(transfers: list[dict[str, Any]], mint: str, account: str) -> int:
    """Net token atomic units credited to `account` within this invocation subtree."""
    return sum(
        transfer["amount_base_units"]
        * ((transfer["destination"] == account) - (transfer["source"] == account))
        for transfer in transfers
        if transfer["mint"] == mint
    )


def _inspect_reference(
    transaction: Any,
    normalized: dict[str, Any],
    tree: list[dict[str, Any]],
    reference: dict[str, Any],
) -> dict[str, Any]:
    fact: dict[str, Any] = {
        "path": reference["path"],
        "transaction_status": reference["transaction_status"],
        "argument_state": reference["argument_state"],
        "suffix_state": reference["suffix_state"],
        "trade_fact_state": "not_assessed",
        "settlement_state": "not_assessed",
        "training_eligible": False,
        "execution_supported": False,
    }
    if normalized["meta_error"] is not None:
        fact.update(
            trade_fact_state="none_failed_transaction",
            settlement_state="none_failed_transaction",
            reason="failed_transaction_attempt_only",
        )
        return fact
    if reference["argument_state"] != "pinned_idl_v1_shape":
        fact.update(trade_fact_state="quarantined", reason="instruction_payload_unverified")
        return fact
    if reference["suffix_state"] != "documented_fee_pair_suffix_candidate":
        fact.update(trade_fact_state="quarantined", reason="suffix_not_qualified")
        return fact

    nodes = [
        node
        for root in tree
        for node in _flatten(root)
        if node["instruction"]["path"] == reference["path"]
    ]
    if len(nodes) != 1:
        fact.update(trade_fact_state="quarantined", reason="target_path_ambiguous")
        return fact
    node = nodes[0]
    instruction = node["instruction"]
    names = SCHEMA["instructions"]["buy_exact_quote_in"]["accounts"]
    if len(instruction["accounts"]) != len(names) + 2:
        fact.update(trade_fact_state="quarantined", reason="target_account_layout_changed")
        return fact
    roles = dict(zip(names, instruction["accounts"][: len(names)], strict=True))
    fee_recipient, fee_ata = instruction["accounts"][-2:]
    if roles["program"] != PROGRAM or instruction["program_id"] != PROGRAM:
        fact.update(trade_fact_state="quarantined", reason="program_role_mismatch")
        return fact
    if roles["base_mint"] == roles["quote_mint"]:
        fact.update(trade_fact_state="quarantined", reason="identical_base_quote_mints")
        return fact
    if roles["pool"] == roles["user"]:
        fact.update(trade_fact_state="quarantined", reason="pool_user_role_alias")
        return fact
    if roles["base_token_program"] != TOKEN or roles["quote_token_program"] != TOKEN:
        fact.update(trade_fact_state="quarantined", reason="token_2022_effects_unsupported")
        return fact
    market_accounts = (
        roles["user_base_token_account"],
        roles["pool_base_token_account"],
        roles["user_quote_token_account"],
        roles["pool_quote_token_account"],
    )
    if len(set(market_accounts)) != len(market_accounts):
        fact.update(trade_fact_state="quarantined", reason="market_account_role_alias")
        return fact
    quote_destination_roles = (
        roles["user_quote_token_account"],
        roles["pool_quote_token_account"],
        roles["protocol_fee_recipient_token_account"],
        roles["coin_creator_vault_ata"],
        instruction["accounts"][-1],
    )
    if len(set(quote_destination_roles)) != len(quote_destination_roles):
        fact.update(trade_fact_state="quarantined", reason="quote_destination_role_alias")
        return fact

    data = bytes.fromhex(instruction["data_hex"])
    spendable_quote_in = int.from_bytes(data[8:16], "little")
    min_base_amount_out = int.from_bytes(data[16:24], "little")
    track_volume = bool(data[24])
    descendants = [child for child in _flatten(node) if child is not node]
    events: list[dict[str, Any]] = []
    transfers: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    for descendant in descendants:
        ix = descendant["instruction"]
        wire = bytes.fromhex(ix["data_hex"])
        if ix["program_id"] == PROGRAM and wire.startswith(EVENT_TAG):
            try:
                events.append(event_payload(wire))
            except (ValueError, KeyError, TypeError) as exc:
                unsupported.append({"path": ix["path"], "reason": str(exc)})
        if ix["program_id"] in (TOKEN, TOKEN2022):
            try:
                transfer = decode_token_transfer_checked(ix)
            except (ValueError, KeyError, TypeError) as exc:
                unsupported.append({"path": ix["path"], "reason": str(exc)})
                continue
            if transfer["program_id"] != TOKEN or transfer["additional_signers"]:
                unsupported.append({"path": ix["path"], "reason": "token2022_or_multisig"})
            transfers.append(transfer)
    if unsupported:
        fact.update(
            trade_fact_state="quarantined",
            reason="unsupported_target_subtree_effect",
            unsupported_effects=unsupported,
        )
        return fact
    if len(events) != 1 or not events[0]["exact"] or events[0]["name"] != "BuyEvent":
        fact.update(
            trade_fact_state="quarantined",
            reason="buy_event_count_or_shape",
            observed_event_count=len(events),
        )
        return fact
    event = events[0]
    fields = event["fields"]
    if any(fields.get(name) != roles[name] for name in _EVENT_ROLE_FIELDS):
        fact.update(trade_fact_state="quarantined", reason="event_role_mismatch")
        return fact
    if fields.get("ix_name") != "buy_exact_quote_in":
        fact.update(trade_fact_state="quarantined", reason="event_instruction_name_mismatch")
        return fact
    if fields.get("track_volume") != track_volume:
        fact.update(trade_fact_state="quarantined", reason="event_volume_flag_mismatch")
        return fact
    if fields.get("max_quote_amount_in") != spendable_quote_in:
        fact.update(trade_fact_state="quarantined", reason="event_spendable_argument_mismatch")
        return fact
    if fields.get("min_base_amount_out") != min_base_amount_out:
        fact.update(trade_fact_state="quarantined", reason="event_minimum_argument_mismatch")
        return fact
    if fields.get("cashback", 0) != 0 or fields.get("creator_fee_unclaimed", 0) != 0:
        fact.update(trade_fact_state="quarantined", reason="cashback_or_unclaimed_creator_fee")
        return fact

    if not transfers or any(
        t["mint"] not in (roles["base_mint"], roles["quote_mint"]) for t in transfers
    ):
        fact.update(trade_fact_state="quarantined", reason="missing_or_foreign_token_transfer")
        return fact
    if any(t["amount_base_units"] <= 0 or t["source"] == t["destination"] for t in transfers):
        fact.update(trade_fact_state="quarantined", reason="zero_or_self_token_transfer")
        return fact
    base_transfers = [t for t in transfers if t["mint"] == roles["base_mint"]]
    if (
        len(base_transfers) != 1
        or base_transfers[0]["source"] != roles["pool_base_token_account"]
        or base_transfers[0]["destination"] != roles["user_base_token_account"]
        or base_transfers[0]["authority"] != roles["pool"]
    ):
        fact.update(trade_fact_state="quarantined", reason="base_transfer_role_or_count")
        return fact
    quote_transfers = [t for t in transfers if t["mint"] == roles["quote_mint"]]
    if not quote_transfers or any(
        t["source"] != roles["user_quote_token_account"] or t["authority"] != roles["user"]
        for t in quote_transfers
    ):
        fact.update(trade_fact_state="quarantined", reason="quote_source_or_authority")
        return fact
    recognized_quote_destinations = {
        roles["pool_quote_token_account"],
        roles["protocol_fee_recipient_token_account"],
        roles["coin_creator_vault_ata"],
        instruction["accounts"][-1],
    }
    if any(t["destination"] not in recognized_quote_destinations for t in quote_transfers):
        fact.update(
            trade_fact_state="quarantined",
            reason="unrecognized_quote_transfer_destination",
        )
        return fact

    try:
        pre_balances, post_balances = _balance_map(normalized)
    except (EconomicDiagnosticError, ValueError, KeyError, TypeError, IndexError) as exc:
        fact.update(
            trade_fact_state="quarantined",
            reason=str(exc) or "token_balance_trace_unavailable",
        )
        return fact
    for transfer in transfers:
        for account in (transfer["source"], transfer["destination"]):
            for observed in (pre_balances.get(account), post_balances.get(account)):
                if observed is not None and (
                    observed["mint"] != transfer["mint"]
                    or observed["program"] != TOKEN
                    or observed["decimals"] != transfer["decimals"]
                ):
                    fact.update(
                        trade_fact_state="quarantined",
                        reason="transfer_checked_mint_program_or_decimal_mismatch",
                        account=account,
                    )
                    return fact
    decimal_evidence = {
        mint: sorted(
            {
                balance["decimals"]
                for account in (pre_balances, post_balances)
                for balance in account.values()
                if balance["mint"] == mint and balance["program"] == TOKEN
            }
        )
        for mint in (roles["base_mint"], roles["quote_mint"])
    }
    if any(len(values) > 1 for values in decimal_evidence.values()):
        fact.update(
            trade_fact_state="quarantined",
            reason="mint_decimal_metadata_disagrees_within_transaction",
            decimals_by_mint=decimal_evidence,
        )
        return fact

    gross_quote_debit = (
        _sum_delta(transfers, roles["quote_mint"], roles["user_quote_token_account"]) * -1
    )
    pool_quote_credit = _sum_delta(
        transfers, roles["quote_mint"], roles["pool_quote_token_account"]
    )
    base_amount_credited = _sum_delta(
        transfers, roles["base_mint"], roles["user_base_token_account"]
    )
    quote_credits_by_destination: dict[str, int] = {}
    for transfer in quote_transfers:
        quote_credits_by_destination[transfer["destination"]] = (
            quote_credits_by_destination.get(transfer["destination"], 0)
            + transfer["amount_base_units"]
        )
    exact_facts = {
        "base_user_credit_equals_event_base_amount_out": base_amount_credited
        == fields["base_amount_out"],
        "gross_user_quote_debit_equals_event_quote_amount_in": gross_quote_debit
        == fields["quote_amount_in"],
        "pool_quote_credit_equals_event_quote_amount_in_with_lp_fee": pool_quote_credit
        == fields["quote_amount_in_with_lp_fee"],
        "gross_debit_within_spendable_quote_in": gross_quote_debit <= spendable_quote_in,
        "base_credit_meets_minimum": base_amount_credited >= min_base_amount_out,
        "gross_user_quote_debit_is_positive": gross_quote_debit > 0,
        "base_user_credit_is_positive": base_amount_credited > 0,
    }
    if not all(exact_facts.values()):
        fact.update(
            trade_fact_state="quarantined",
            reason="instruction_event_transfer_mismatch",
            exact_transfer_event_checks=exact_facts,
        )
        return fact

    target_mints: dict[str, str] = {}
    for transfer in transfers:
        for account in (transfer["source"], transfer["destination"]):
            prior = target_mints.get(account)
            if prior is not None and prior != transfer["mint"]:
                fact.update(trade_fact_state="quarantined", reason="target_account_mint_alias")
                return fact
            target_mints[account] = transfer["mint"]
    ledger = reconcile_legacy_token_accounts(transaction, target_mints)
    protocol_paid = quote_credits_by_destination.get(
        roles["protocol_fee_recipient_token_account"], 0
    )
    creator_paid = quote_credits_by_destination.get(roles["coin_creator_vault_ata"], 0)
    buyback_paid = quote_credits_by_destination.get(fee_ata, 0)
    fee_flow_hypotheses = {
        "protocol_recipient_transfer_plus_buyback_equals_event_protocol_fee": (
            protocol_paid + buyback_paid == fields.get("protocol_fee")
        ),
        "protocol_recipient_transfer_equals_protocol_fee_less_buyback": protocol_paid
        == fields.get("protocol_fee", 0) - fields.get("buyback_fee", 0),
        "creator_vault_transfer_equals_event_creator_fee": creator_paid
        == fields.get("coin_creator_fee"),
        "buyback_ata_transfer_equals_event_buyback_fee": buyback_paid == fields.get("buyback_fee"),
        "only_supported_quote_destination_roles_observed": set(quote_credits_by_destination)
        <= recognized_quote_destinations,
    }
    fact.update(
        trade_fact_state="exact_instruction_event_transfer_facts_diagnostic_only",
        settlement_state=ledger["status"],
        settlement_contract=LEDGER_CONTRACT,
        account_ledger=ledger,
        amounts_atomic_units={
            "spendable_quote_in": spendable_quote_in,
            "minimum_base_out": min_base_amount_out,
            "gross_user_quote_debit": gross_quote_debit,
            "pool_quote_credit": pool_quote_credit,
            "base_user_credit": base_amount_credited,
            "event_net_user_quote_amount_in": fields.get("user_quote_amount_in"),
            "event_lp_fee": fields.get("lp_fee"),
            "event_protocol_fee": fields.get("protocol_fee"),
            "event_creator_fee": fields.get("coin_creator_fee"),
            "event_buyback_fee": fields.get("buyback_fee"),
        },
        unit_evidence={
            "base_mint": roles["base_mint"],
            "base_atomic_decimals_observed": decimal_evidence[roles["base_mint"]],
            "quote_mint": roles["quote_mint"],
            "quote_atomic_decimals_observed": decimal_evidence[roles["quote_mint"]],
            "ui_conversion_performed": False,
        },
        exact_transfer_event_checks=exact_facts,
        fee_flow_hypotheses={
            "pool_credit_less_lp_fee_equals_event_net_user_quote_amount": pool_quote_credit
            - fields.get("lp_fee", 0)
            == fields.get("user_quote_amount_in"),
            **fee_flow_hypotheses,
        },
        quote_credits_by_destination_atomic_units=quote_credits_by_destination,
        fee_recipient=fee_recipient,
        fee_quote_ata=fee_ata,
        event=event,
        transfers=transfers,
        target_accounts=target_mints,
        admission="none",
    )
    return fact


def diagnose_pumpswap_buy_exact_v1(transaction: Any) -> dict[str, Any]:
    """Emit one path-scoped fact record per v1 reference; never grants learning admission."""
    inspected = inspect_buy_exact_quote_in_v1(transaction)
    if inspected["status"] == "malformed_or_missing_trace":
        return _unavailable(inspected["normalization_error"])
    if not inspected["references"]:
        return {
            "contract": CONTRACT,
            "status": "no_v1_reference",
            "references": [],
            "settlement_state": "not_applicable",
            "training_eligible": False,
            "execution_supported": False,
            "admission": "none",
        }
    try:
        normalized = normalize_compiled_transaction(transaction)
        tree = _transaction_tree(normalized)
    except (EconomicDiagnosticError, ValueError, KeyError, TypeError, IndexError) as exc:
        return _unavailable(str(exc) or "transaction_trace_unavailable")
    facts = [
        _inspect_reference(transaction, normalized, tree, reference)
        for reference in inspected["references"]
    ]
    failed = normalized["meta_error"] is not None
    return {
        "contract": CONTRACT,
        "status": "failed_tx_attempt_only"
        if failed
        else "diagnostic_facts_only"
        if facts
        and all(
            item["trade_fact_state"] == "exact_instruction_event_transfer_facts_diagnostic_only"
            for item in facts
        )
        else "quarantined",
        "transaction_signatures": inspected.get("transaction_signatures"),
        "references": facts,
        "settlement_state": "none_failed_transaction"
        if failed
        else "per_reference_full_transaction_ledger",
        "training_eligible": False,
        "execution_supported": False,
        "admission": "none",
    }
