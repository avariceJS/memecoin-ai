"""Tail2 dual-legacy PumpSwap buy-exact atomic-flow diagnostic, v1.

This is a transfer-effect diagnostic only. It keeps candidate invocation flows separate from
the full transaction endpoint ledger and treats event fields and fee equations as cross-checks.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .buy_exact_v1 import inspect_buy_exact_quote_in_v1
from .compiled_economics import (
    ASSOCIATED_TOKEN_PROGRAM,
    EconomicDiagnosticError,
    _balance_map,
    _flatten,
    _is_writable,
    _transaction_tree,
    associated_token_address,
)
from .decoder import EVENT_TAG, PROGRAM, TOKEN, TOKEN2022, event_payload
from .legacy_token_ledger import (
    LEDGER_CONTRACT,
    SYSTEM_PROGRAM,
    _legacy_token_effect,
    _system_create_account,
    reconcile_legacy_token_accounts,
)
from .normalize import (
    NormalizationError,
    decode_token_transfer_checked,
    normalize_compiled_transaction,
)
from .schema import SCHEMA

CONTRACT = "pumpswap_v1_buy_exact_quote_in_tail2_dual_legacy_atomic_flow_diagnostic_v1"
# Address in the pinned Pump AMM IDL at SCHEMA provenance commit:
# 8cda1fa30ea658b20909d8aedf002047119388d2.
FEE_PROGRAM = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"
# ATA interface source at this commit defines Create/CreateIdempotent as tags 0/1 and six ordered
# accounts; its SHA-256 is recorded in the replay receipt.
ATA_INTERFACE_SOURCE_COMMIT = "06b93de62562a86eb0a5da8164a40fd06fbd4f74"
ATA_INTERFACE_SOURCE_SHA256 = "033191cb1316fe2370fb9f694b0a0f9f140f647ca44661f07cb661ed4aa8c56d"
EVENT_ROLE_FIELDS = (
    "pool",
    "user",
    "user_base_token_account",
    "user_quote_token_account",
    "protocol_fee_recipient",
    "protocol_fee_recipient_token_account",
)
_TARGET_ROLES = (
    "user_base_token_account",
    "pool_base_token_account",
    "user_quote_token_account",
    "pool_quote_token_account",
    "protocol_fee_recipient_token_account",
    "coin_creator_vault_ata",
)


def _state(reason: str, *, attempt: bool = False, **details: Any) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "status": "failed_tx_attempt_only" if attempt else "quarantined",
        "reason": reason,
        "candidate_token_flow_state": "none_failed_transaction" if attempt else "unknown",
        "transaction_target_account_ledger_state": "none_failed_transaction"
        if attempt
        else "unknown",
        "training_eligible": False,
        "execution_supported": False,
        "admission": "none",
        **details,
    }


def _signer(transaction: Any, normalized: dict[str, Any], pubkey: str) -> bool:
    matches = [row for row in normalized["accounts"] if row["pubkey"] == pubkey]
    if len(matches) != 1 or matches[0]["source"] != "static":
        return False
    header = transaction["transaction"]["message"].get("header")
    required = header.get("numRequiredSignatures") if isinstance(header, dict) else None
    return type(required) is int and matches[0]["index"] < required


def _event_crosscheck(
    events: list[dict[str, Any]],
    event_errors: list[dict[str, str]],
    roles: dict[str, str],
    args: dict[str, Any],
    flows: dict[str, int],
) -> dict[str, Any]:
    if (
        event_errors
        or len(events) != 1
        or not events[0].get("exact")
        or events[0].get("name") != "BuyEvent"
    ):
        return {
            "state": "unknown_or_ambiguous",
            "event_count": len(events),
            "event_decode_errors": event_errors,
        }
    fields = events[0]["fields"]
    checks = {
        "role_fields_match": all(fields.get(key) == roles[key] for key in EVENT_ROLE_FIELDS),
        "instruction_name_matches": fields.get("ix_name") == "buy_exact_quote_in",
        "track_volume_matches": fields.get("track_volume") == args["track_volume"],
        "spendable_argument_matches": fields.get("max_quote_amount_in")
        == args["spendable_quote_in"],
        "minimum_argument_matches": fields.get("min_base_amount_out") == args["minimum_base_out"],
        "gross_quote_debit_matches": fields.get("quote_amount_in")
        == flows["gross_user_quote_debit"],
        "pool_quote_credit_matches": fields.get("quote_amount_in_with_lp_fee")
        == flows["pool_quote_credit"],
        "base_credit_matches": fields.get("base_amount_out") == flows["base_user_credit"],
    }
    return {
        "state": "match" if all(checks.values()) else "mismatch",
        "checks": checks,
        "fields": fields,
    }


def _fee_hypotheses(
    event_check: dict[str, Any], roles: dict[str, str], fee_ata: str, credits: dict[str, int]
) -> dict[str, Any]:
    if event_check.get("state") != "match":
        return {
            "fee_flow_hypotheses_state": "not_evaluated",
            "pool_credit_less_lp_fee_equals_event_net_user_quote_amount": None,
            "protocol_recipient_transfer_plus_buyback_equals_event_protocol_fee": None,
            "protocol_recipient_transfer_equals_protocol_fee_less_buyback": None,
            "creator_vault_transfer_equals_event_creator_fee": None,
            "buyback_ata_transfer_equals_event_buyback_fee": None,
        }
    event = event_check["fields"]
    protocol = credits.get(roles["protocol_fee_recipient_token_account"], 0)
    buyback = credits.get(fee_ata, 0)
    creator = credits.get(roles["coin_creator_vault_ata"], 0)
    pool = credits.get(roles["pool_quote_token_account"], 0)
    return {
        "fee_flow_hypotheses_state": "evaluated",
        "pool_credit_less_lp_fee_equals_event_net_user_quote_amount": pool - event.get("lp_fee", 0)
        == event.get("user_quote_amount_in"),
        "protocol_recipient_transfer_plus_buyback_equals_event_protocol_fee": protocol + buyback
        == event.get("protocol_fee"),
        "protocol_recipient_transfer_equals_protocol_fee_less_buyback": protocol
        == event.get("protocol_fee", 0) - event.get("buyback_fee", 0),
        "creator_vault_transfer_equals_event_creator_fee": creator == event.get("coin_creator_fee"),
        "buyback_ata_transfer_equals_event_buyback_fee": buyback == event.get("buyback_fee"),
    }


def _signed_transfer_deltas(transfers: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    """Signed token atomic-unit deltas keyed by (account, mint); lamports are out of scope."""
    deltas: dict[tuple[str, str], int] = defaultdict(int)
    for transfer in transfers:
        amount = transfer.get("amount_base_units")
        if type(amount) is not int or not 0 <= amount < 2**64:
            raise ValueError("transfer_amount_outside_u64")
        mint = transfer.get("mint")
        source = transfer.get("source")
        destination = transfer.get("destination")
        if not all(isinstance(value, str) and value for value in (mint, source, destination)):
            raise ValueError("transfer_account_or_mint_missing")
        deltas[(source, mint)] -= amount
        deltas[(destination, mint)] += amount
    return dict(deltas)


def _unclassified_target_touches(
    transaction: Any,
    normalized: dict[str, Any],
    target_mints: dict[str, str],
    candidate_path: list[int],
) -> list[dict[str, Any]]:
    """Find target-account references outside the pinned candidate or classified token effects."""
    target_accounts = set(target_mints)
    unclassified: list[dict[str, Any]] = []
    instructions = [*normalized["outer_instructions"], *normalized["inner_instructions"]]
    for instruction in instructions:
        touched = sorted(set(instruction["accounts"]) & target_accounts)
        if not touched:
            continue
        if instruction["path"] == candidate_path and instruction["program_id"] == PROGRAM:
            continue
        if instruction["program_id"] == TOKEN:
            try:
                effect = _legacy_token_effect(instruction)
            except (ValueError, KeyError, TypeError):
                effect = None
            if effect is not None:
                continue
        elif instruction["program_id"] == ASSOCIATED_TOKEN_PROGRAM:
            accounts = instruction["accounts"]
            wire = bytes.fromhex(instruction["data_hex"])
            try:
                associated_is_exact = (
                    len(accounts) == 6
                    and wire in (b"\x00", b"\x01")
                    and accounts[1] in target_accounts
                    and target_mints[accounts[1]] == accounts[3]
                    and accounts[4] == SYSTEM_PROGRAM
                    and accounts[5] == TOKEN
                    and associated_token_address(accounts[2], accounts[3], accounts[5])
                    == accounts[1]
                    and _is_writable(transaction, normalized, accounts[0])
                    and _signer(transaction, normalized, accounts[0])
                    and _is_writable(transaction, normalized, accounts[1])
                    and touched == [accounts[1]]
                )
            except (EconomicDiagnosticError, ValueError, IndexError):
                associated_is_exact = False
            if associated_is_exact:
                continue
        elif instruction["program_id"] == SYSTEM_PROGRAM:
            try:
                effect = _system_create_account(instruction)
            except (ValueError, KeyError, TypeError):
                effect = None
            if (
                effect is not None
                and effect.get("owner_program") == TOKEN
                and effect.get("account") in target_accounts
                and effect.get("funded_by") not in target_accounts
                and len(instruction["accounts"]) == 2
            ):
                continue
        unclassified.append(
            {
                "path": instruction["path"],
                "program_id": instruction["program_id"],
                "accounts": touched,
            }
        )
    return unclassified


def diagnose_pumpswap_buy_exact_tail2_dual_legacy_v1(
    transaction: Any, path: list[int]
) -> dict[str, Any]:
    """Return independently scoped candidate flows and transaction endpoint ledger for one path."""
    try:
        structural = inspect_buy_exact_quote_in_v1(transaction)
        normalized = normalize_compiled_transaction(transaction)
        if normalized["meta_error"] is not None:
            return _state("failed_transaction_attempt_only", attempt=True)
        matches = [row for row in structural.get("references", []) if row.get("path") == path]
        if len(matches) != 1:
            return _state("target_path_missing_or_ambiguous")
        reference = matches[0]
        if (
            reference.get("argument_state") != "pinned_idl_v1_shape"
            or reference.get("suffix_state") != "documented_fee_pair_suffix_candidate"
            or reference.get("remaining_account_count") != 2
        ):
            return _state("tail2_structural_predicate_not_satisfied")
        if any(
            reference.get("suffix_checks", {}).get(key) is not True
            for key in (
                "fee_recipient_in_pinned_eight_address_set",
                "fee_ata_derives_from_recipient_quote_mint_and_quote_program",
                "fee_recipient_not_transaction_writable",
                "fee_ata_transaction_writable",
            )
        ):
            return _state("fee_pair_privileges_or_derivation_unverified")

        tree = _transaction_tree(normalized)
        nodes = [
            node for root in tree for node in _flatten(root) if node["instruction"]["path"] == path
        ]
        if len(nodes) != 1:
            return _state("target_path_ambiguous")
        node = nodes[0]
        ix = node["instruction"]
        names = SCHEMA["instructions"]["buy_exact_quote_in"]["accounts"]
        if len(ix["accounts"]) != len(names) + 2:
            return _state("target_account_layout_changed")
        roles = dict(zip(names, ix["accounts"][: len(names)], strict=True))
        _fee_recipient, fee_ata = ix["accounts"][-2:]
        if (
            ix["program_id"] != PROGRAM
            or roles["program"] != PROGRAM
            or roles["base_token_program"] != TOKEN
            or roles["quote_token_program"] != TOKEN
            or roles["system_program"] != SYSTEM_PROGRAM
            or roles["associated_token_program"] != ASSOCIATED_TOKEN_PROGRAM
            or roles["fee_program"] != FEE_PROGRAM
            or roles["base_mint"] == roles["quote_mint"]
            or roles["pool"] == roles["user"]
            or len({roles[key] for key in _TARGET_ROLES}) != len(_TARGET_ROLES)
        ):
            return _state("legacy_role_or_mint_contract_unverified")
        quote_destination_roles = {
            roles["user_quote_token_account"],
            roles["pool_quote_token_account"],
            roles["protocol_fee_recipient_token_account"],
            roles["coin_creator_vault_ata"],
            fee_ata,
        }
        if len(quote_destination_roles) != 5:
            return _state("quote_destination_role_alias")
        if not _signer(transaction, normalized, roles["user"]):
            return _state("user_not_verified_signer")

        data = bytes.fromhex(ix["data_hex"])
        args = {
            "spendable_quote_in": int.from_bytes(data[8:16], "little"),
            "minimum_base_out": int.from_bytes(data[16:24], "little"),
            "track_volume": bool(data[24]),
        }
        events: list[dict[str, Any]] = []
        event_errors: list[dict[str, str]] = []
        transfers: list[dict[str, Any]] = []
        ancillary_lamport_effects: list[dict[str, Any]] = []
        unsupported: list[dict[str, str]] = []
        for descendant in list(_flatten(node))[1:]:
            child = descendant["instruction"]
            wire = bytes.fromhex(child["data_hex"])
            if child["program_id"] == PROGRAM and wire.startswith(EVENT_TAG):
                try:
                    events.append(event_payload(wire))
                except (ValueError, KeyError, TypeError):
                    event_errors.append(
                        {"path": str(child["path"]), "reason": "event_decode_failed"}
                    )
            elif child["program_id"] == roles["fee_program"]:
                # The pinned buyExact accounts include the fee program; token amount effects
                # remain captured as nested legacy Token instructions in this subtree.
                continue
            elif child["program_id"] == SYSTEM_PROGRAM:
                effect = _system_create_account(child)
                if (
                    effect is None
                    or effect.get("owner_program") not in (TOKEN, PROGRAM)
                    or len(child["accounts"]) != 2
                    or not _is_writable(transaction, normalized, effect["funded_by"])
                    or not _is_writable(transaction, normalized, effect["account"])
                ):
                    unsupported.append(
                        {"path": str(child["path"]), "reason": "unsupported_system_effect"}
                    )
                else:
                    ancillary_lamport_effects.append(
                        {
                            "kind": "system_create_account",
                            "path": child["path"],
                            "account": effect["account"],
                            "funded_by": effect["funded_by"],
                            "lamports": effect["lamports"],
                            "space_bytes": effect["space"],
                            "owner_program": effect["owner_program"],
                        }
                    )
            elif child["program_id"] == PROGRAM:
                unsupported.append(
                    {"path": str(child["path"]), "reason": "unsupported_program_effect"}
                )
            elif child["program_id"] not in (TOKEN, TOKEN2022):
                unsupported.append(
                    {"path": str(child["path"]), "reason": "unsupported_program_effect"}
                )
            if child["program_id"] in (TOKEN, TOKEN2022):
                try:
                    transfer = decode_token_transfer_checked(child)
                except (NormalizationError, ValueError, KeyError, TypeError) as exc:
                    unsupported.append(
                        {
                            "path": str(child["path"]),
                            "reason": str(exc) or "unsupported_token_effect",
                        }
                    )
                    continue
                if transfer["program_id"] != TOKEN or transfer["additional_signers"]:
                    unsupported.append(
                        {"path": str(child["path"]), "reason": "token2022_or_multisig"}
                    )
                transfers.append(transfer)
        if unsupported:
            return _state("unsupported_candidate_subtree_effect", unsupported_effects=unsupported)
        if not transfers or any(
            transfer["mint"] not in (roles["base_mint"], roles["quote_mint"])
            or transfer["amount_base_units"] <= 0
            or transfer["source"] == transfer["destination"]
            for transfer in transfers
        ):
            return _state("invalid_or_foreign_transfer_fact")
        base = [row for row in transfers if row["mint"] == roles["base_mint"]]
        quote = [row for row in transfers if row["mint"] == roles["quote_mint"]]
        if (
            len(base) != 1
            or base[0]["source"] != roles["pool_base_token_account"]
            or base[0]["destination"] != roles["user_base_token_account"]
            or base[0]["authority"] != roles["pool"]
            or not quote
            or any(
                row["source"] != roles["user_quote_token_account"]
                or row["authority"] != roles["user"]
                for row in quote
            )
        ):
            return _state("transfer_roles_or_counts_unverified")
        quote_destinations = {
            roles["pool_quote_token_account"],
            roles["protocol_fee_recipient_token_account"],
            roles["coin_creator_vault_ata"],
            fee_ata,
        }
        if any(row["destination"] not in quote_destinations for row in quote):
            return _state("quote_destination_outside_scoped_roles")
        if any(
            not _is_writable(transaction, normalized, account)
            for row in transfers
            for account in (row["source"], row["destination"])
        ):
            return _state("transfer_account_not_writable")
        decimals: dict[str, set[int]] = defaultdict(set)
        signed_by_account_mint: dict[tuple[str, str], int] = defaultdict(int)
        totals_by_mint: dict[str, int] = defaultdict(int)
        for row in transfers:
            decimals[row["mint"]].add(row["decimals"])
            totals_by_mint[row["mint"]] += row["amount_base_units"]
        signed_by_account_mint.update(_signed_transfer_deltas(transfers))
        if any(len(values) != 1 for values in decimals.values()):
            return _state("mint_decimal_metadata_disagreement")
        pre, post = _balance_map(normalized)
        for row in transfers:
            for account in (row["source"], row["destination"]):
                for observed in (pre.get(account), post.get(account)):
                    if observed is not None and (
                        observed["mint"] != row["mint"]
                        or observed["program"] != TOKEN
                        or observed["decimals"] != row["decimals"]
                    ):
                        return _state("endpoint_mint_program_or_decimals_mismatch")

        # Each legacy SPL token transfer conserves atomic units within its mint.
        conservation = {
            mint: sum(
                delta
                for (account, unit_mint), delta in signed_by_account_mint.items()
                if unit_mint == mint
            )
            for mint in totals_by_mint
        }
        if any(value != 0 for value in conservation.values()):
            return _state("signed_account_mint_conservation_failed")
        gross = sum(row["amount_base_units"] for row in quote)
        pool_credit = sum(
            row["amount_base_units"]
            for row in quote
            if row["destination"] == roles["pool_quote_token_account"]
        )
        base_credit = sum(
            row["amount_base_units"]
            for row in base
            if row["destination"] == roles["user_base_token_account"]
        )
        credits: dict[str, int] = defaultdict(int)
        for row in quote:
            credits[row["destination"]] += row["amount_base_units"]
        if (
            gross <= 0
            or base_credit <= 0
            or gross > args["spendable_quote_in"]
            or base_credit < args["minimum_base_out"]
        ):
            return _state("flow_bounds_not_satisfied")

        target_mints: dict[str, str] = {}
        for row in transfers:
            for account in (row["source"], row["destination"]):
                prior = target_mints.setdefault(account, row["mint"])
                if prior != row["mint"]:
                    return _state("target_account_mint_alias")
        if any(
            effect["account"] in target_mints or effect["funded_by"] in target_mints
            for effect in ancillary_lamport_effects
        ):
            return _state("system_create_account_aliases_token_target")
        unclassified_touches = _unclassified_target_touches(
            transaction, normalized, target_mints, path
        )
        ledger = reconcile_legacy_token_accounts(transaction, target_mints)
        ledger_exact = (
            ledger.get("status") == "exact"
            and ledger.get("exact_account_count") == len(target_mints)
            and ledger.get("unknown_account_count") == 0
            and ledger.get("censored_account_count") == 0
        )
        event_check = _event_crosscheck(
            events,
            event_errors,
            roles,
            args,
            {
                "gross_user_quote_debit": gross,
                "pool_quote_credit": pool_credit,
                "base_user_credit": base_credit,
            },
        )
        decimals_evidence = {mint: next(iter(values)) for mint, values in sorted(decimals.items())}
        return {
            "contract": CONTRACT,
            "status": "diagnostic_facts_only",
            "candidate_token_flow_state": "exact",
            "transaction_target_account_ledger_state": (
                "exact"
                if ledger_exact and not unclassified_touches
                else "unknown_partial"
                if unclassified_touches or ledger.get("status") == "exact"
                else ledger.get("status", "unknown_partial")
            ),
            "transaction_target_account_ledger_contract": LEDGER_CONTRACT,
            "candidate_flows_atomic_units": {
                "gross_user_quote_debit": gross,
                "pool_quote_credit": pool_credit,
                "user_base_credit": base_credit,
                "quote_credits_by_destination": dict(sorted(credits.items())),
            },
            "units": {
                "base_mint": roles["base_mint"],
                "quote_mint": roles["quote_mint"],
                "atomic_decimals_metadata": decimals_evidence,
                "amounts_are_integer_atomic_units": True,
                "ui_conversion_performed": False,
                "lamports_mixed_into_token_amounts": False,
                "system_create_account_lamports_are_separately_recorded": True,
            },
            "instruction_arguments_atomic_units": args,
            "transfer_facts": transfers,
            "ancillary_lamport_effects": ancillary_lamport_effects,
            "signed_account_mint_deltas_atomic_units": [
                {"account": account, "mint": mint, "delta": delta}
                for (account, mint), delta in sorted(signed_by_account_mint.items())
            ],
            "signed_conservation_by_mint_atomic_units": dict(sorted(conservation.items())),
            "flow_bounds": {
                "gross_debit_positive": gross > 0,
                "base_credit_positive": base_credit > 0,
                "gross_debit_at_most_spendable": gross <= args["spendable_quote_in"],
                "base_credit_at_least_minimum": base_credit >= args["minimum_base_out"],
            },
            "event_crosscheck": event_check,
            "event_decode_errors": event_errors,
            "fee_flow_hypotheses": _fee_hypotheses(event_check, roles, fee_ata, credits),
            "only_supported_quote_destination_roles_observed": set(credits) <= quote_destinations,
            "only_supported_quote_destination_roles_is_fee_law": False,
            "ordered_target_account_touches": "classified"
            if not unclassified_touches
            else "incomplete",
            "unclassified_target_account_touches": unclassified_touches,
            "target_account_ledger": ledger,
            "admission": {"training": "none", "execution": "none", "wallet_net_price": "none"},
        }
    except (
        NormalizationError,
        EconomicDiagnosticError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
    ) as exc:
        return _state(getattr(exc, "category", None) or str(exc) or "diagnostic_unavailable")
