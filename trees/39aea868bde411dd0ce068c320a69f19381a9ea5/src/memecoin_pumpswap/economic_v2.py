"""Full-transaction settlement wrapper for the pinned PumpSwap sell diagnostic."""

from __future__ import annotations

from typing import Any

from .compiled_economics import (
    EconomicDiagnosticError,
    _flatten,
    _transaction_tree,
    diagnose_pumpswap_sell,
)
from .decoder import PROGRAM, TOKEN, TOKEN2022
from .legacy_token_ledger import LEDGER_CONTRACT, reconcile_legacy_token_accounts
from .normalize import decode_token_transfer_checked, normalize_compiled_transaction
from .schema import SCHEMA

ECONOMIC_CONTRACT = "pumpswap_sell23_full_transaction_settlement_v2"


def diagnose_pumpswap_sell_v2(transaction: Any) -> dict[str, Any]:
    """Keep v1 trade facts and replace subtree-only settlement with a full-tx ledger."""
    base = diagnose_pumpswap_sell(transaction)
    result = dict(base)
    result["contract"] = ECONOMIC_CONTRACT
    result["settlement_contract"] = LEDGER_CONTRACT
    if base.get("transfer_fact_state") != "exact_checked_inner_transfer_and_event_equations":
        result["account_settlement_state"] = "unknown_partial"
        result["reason"] = base.get("reason") or "target_transfer_facts_unqualified"
        result["account_ledger"] = {
            "contract": LEDGER_CONTRACT,
            "status": "unknown_partial",
            "target_account_count": 0,
            "exact_account_count": 0,
            "censored_account_count": 0,
            "unknown_account_count": 0,
            "unknown_reasons": {result["reason"]: 1},
            "censored_reasons": {},
            "instruction_trace": "unqualified",
            "account_facts": {},
            "unsupported_instruction_effects": [],
        }
        return result

    try:
        normalized = normalize_compiled_transaction(transaction)
        instruction_profile = SCHEMA["instructions"]["sell"]
        discriminator = bytes(instruction_profile["discriminator"])
        instructions = [*normalized["outer_instructions"], *normalized["inner_instructions"]]
        targets = [
            instruction
            for instruction in instructions
            if instruction["program_id"] == PROGRAM
            and bytes.fromhex(instruction["data_hex"])[:8] == discriminator
        ]
        if len(targets) != 1:
            raise EconomicDiagnosticError("v2_target_instruction_identity")
        target = targets[0]
        tree = _transaction_tree(normalized)
        target_node = next(
            node
            for root in tree
            for node in _flatten(root)
            if node["instruction"]["path"] == target["path"]
        )
        descendants = [node["instruction"] for node in _flatten(target_node)][1:]
        target_mints: dict[str, str] = {}
        target_transfer_count = 0
        for instruction in descendants:
            if instruction["program_id"] not in (TOKEN, TOKEN2022):
                continue
            transfer = decode_token_transfer_checked(instruction)
            if transfer["program_id"] != TOKEN or transfer["additional_signers"]:
                raise EconomicDiagnosticError("v2_target_token_program_unsupported")
            for account in (transfer["source"], transfer["destination"]):
                prior = target_mints.get(account)
                if prior is not None and prior != transfer["mint"]:
                    raise EconomicDiagnosticError("v2_target_account_mint_alias")
                target_mints[account] = transfer["mint"]
            target_transfer_count += 1
        if not target_transfer_count or not target_mints:
            raise EconomicDiagnosticError("v2_target_transfers_missing")
        ledger = reconcile_legacy_token_accounts(transaction, target_mints)
    except (
        EconomicDiagnosticError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        StopIteration,
    ) as exc:
        reason = str(exc) or "v2_ledger_schema_invalid"
        result["account_ledger"] = {
            "contract": LEDGER_CONTRACT,
            "status": "unknown_partial",
            "target_account_count": 0,
            "exact_account_count": 0,
            "censored_account_count": 0,
            "unknown_account_count": 0,
            "unknown_reasons": {reason: 1},
            "censored_reasons": {},
            "instruction_trace": "unqualified",
            "account_facts": {},
            "unsupported_instruction_effects": [],
        }
        ledger = result["account_ledger"]
    else:
        result["account_ledger"] = ledger

    exact = ledger["status"] == "exact"
    censored = ledger["status"] == "censored_partial"
    result["account_settlement_state"] = ledger["status"]
    result["status"] = (
        "reconciled_diagnostic_only"
        if exact
        else "reconciled_trade_with_censored_account_settlement"
        if censored
        else "quarantined"
    )
    result["reason"] = (
        None
        if exact
        else next(iter(ledger["censored_reasons"]), None)
        if censored
        else "full_transaction_account_settlement_unknown"
    )
    result["account_ledger_scope"] = (
        "target-touched legacy SPL Token accounts; full transaction instruction trace as returned"
    )
    result["wallet_net_price"] = None
    result["wallet_net_price_state"] = "unknown_not_reconstructed"
    result["admission"] = "none"
    result["training_eligible"] = False
    result["execution_supported"] = False
    return result
