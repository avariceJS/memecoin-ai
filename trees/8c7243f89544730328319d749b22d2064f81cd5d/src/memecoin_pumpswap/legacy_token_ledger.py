"""Versioned legacy SPL Token account ledger for one complete compiled transaction."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .decoder import TOKEN, TOKEN2022, encode58
from .normalize import normalize_compiled_transaction

LEDGER_CONTRACT = "legacy_spl_token_full_transaction_account_ledger_v1"
NATIVE_MINT = "So11111111111111111111111111111111111111112"
SYSTEM_PROGRAM = "11111111111111111111111111111111"


class LedgerEvidenceError(ValueError):
    """A trace or balance boundary that cannot be reconciled without guessing."""


def _legacy_token_effect(instruction: dict[str, Any]) -> dict[str, Any] | None:
    wire = bytes.fromhex(instruction["data_hex"])
    accounts = instruction["accounts"]
    if not wire:
        raise LedgerEvidenceError("token_instruction_data_empty")
    tag = wire[0]
    path = instruction["path"]
    program = instruction["program_id"]
    if program != TOKEN:
        return None
    if tag == 3 and len(wire) == 9 and len(accounts) >= 3:
        amount = int.from_bytes(wire[1:9], "little")
        return {
            "kind": "transfer",
            "path": path,
            "source": accounts[0],
            "destination": accounts[1],
            "amount": amount,
            "mint": None,
        }
    if tag == 12 and len(wire) == 10 and len(accounts) >= 4:
        amount = int.from_bytes(wire[1:9], "little")
        return {
            "kind": "transfer_checked",
            "path": path,
            "source": accounts[0],
            "destination": accounts[2],
            "amount": amount,
            "mint": accounts[1],
            "decimals": wire[9],
        }
    if tag == 7 and len(wire) == 9 and len(accounts) >= 3:
        return {
            "kind": "mint_to",
            "path": path,
            "destination": accounts[1],
            "amount": int.from_bytes(wire[1:9], "little"),
            "mint": accounts[0],
        }
    if tag == 14 and len(wire) == 10 and len(accounts) >= 3:
        return {
            "kind": "mint_to_checked",
            "path": path,
            "destination": accounts[1],
            "amount": int.from_bytes(wire[1:9], "little"),
            "mint": accounts[0],
            "decimals": wire[9],
        }
    if tag == 8 and len(wire) == 9 and len(accounts) >= 3:
        return {
            "kind": "burn",
            "path": path,
            "source": accounts[0],
            "amount": int.from_bytes(wire[1:9], "little"),
            "mint": accounts[1],
        }
    if tag == 15 and len(wire) == 10 and len(accounts) >= 3:
        return {
            "kind": "burn_checked",
            "path": path,
            "source": accounts[0],
            "amount": int.from_bytes(wire[1:9], "little"),
            "mint": accounts[1],
            "decimals": wire[9],
        }
    if tag in (1, 16, 18) and len(accounts) >= 2:
        expected_length = {1: 1, 16: 33, 18: 33}[tag]
        if len(wire) != expected_length:
            raise LedgerEvidenceError("initialize_account_instruction_shape")
        return {
            "kind": "initialize_account",
            "path": path,
            "account": accounts[0],
            "mint": accounts[1],
            "variant": tag,
            "owner": accounts[2] if tag == 1 and len(accounts) >= 3 else encode58(wire[1:33]),
        }
    if tag == 22 and len(wire) == 1 and accounts:
        return {"kind": "initialize_immutable_owner", "path": path, "account": accounts[0]}
    if tag == 9 and len(wire) == 1 and len(accounts) >= 3:
        return {
            "kind": "close_account",
            "path": path,
            "account": accounts[0],
            "destination": accounts[1],
        }
    if tag == 17 and len(wire) == 1 and accounts:
        return {"kind": "sync_native", "path": path, "account": accounts[0]}
    return None


def _system_create_account(instruction: dict[str, Any]) -> dict[str, Any] | None:
    if instruction["program_id"] != SYSTEM_PROGRAM:
        return None
    wire = bytes.fromhex(instruction["data_hex"])
    accounts = instruction["accounts"]
    if len(wire) >= 4 and int.from_bytes(wire[:4], "little") == 0:
        if len(wire) != 52 or len(accounts) < 2:
            raise LedgerEvidenceError("system_create_account_shape")
        return {
            "kind": "system_create_account",
            "path": instruction["path"],
            "funded_by": accounts[0],
            "account": accounts[1],
            "lamports": int.from_bytes(wire[4:12], "little"),
            "space": int.from_bytes(wire[12:20], "little"),
            "owner_program": encode58(wire[20:52]),
        }
    return None


def _ordered_instructions(normalized: dict[str, Any]) -> list[dict[str, Any]]:
    inner_by_outer: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for instruction in normalized["inner_instructions"]:
        inner_by_outer[instruction["outer_index"]].append(instruction)
    ordered: list[dict[str, Any]] = []
    for outer in normalized["outer_instructions"]:
        ordered.append(outer)
        ordered.extend(inner_by_outer.get(outer["path"][0], []))
    if len(ordered) != len(normalized["outer_instructions"]) + len(
        normalized["inner_instructions"]
    ):
        raise LedgerEvidenceError("ordered_instruction_trace_incomplete")
    return ordered


def _balance_maps(normalized: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    accounts = normalized["accounts"]
    maps: list[dict[str, Any]] = []
    for field in ("pre_token_balances", "post_token_balances"):
        rows = normalized[field]
        if not isinstance(rows, list):
            raise LedgerEvidenceError("token_balance_trace_unavailable")
        result: dict[str, Any] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise LedgerEvidenceError("token_balance_row_invalid")
            index = row.get("accountIndex")
            amount = row.get("uiTokenAmount", {}).get("amount")
            decimals = row.get("uiTokenAmount", {}).get("decimals")
            if (
                type(index) is not int
                or not 0 <= index < len(accounts)
                or not isinstance(amount, str)
                or not amount.isascii()
                or not amount.isdecimal()
                or type(decimals) is not int
                or not 0 <= decimals <= 255
                or not isinstance(row.get("mint"), str)
                or not isinstance(row.get("owner"), str)
                or not isinstance(row.get("programId"), str)
            ):
                raise LedgerEvidenceError("token_balance_row_invalid")
            account = accounts[index]["pubkey"]
            if account in result:
                raise LedgerEvidenceError("duplicate_token_balance_account")
            result[account] = {
                "mint": row["mint"],
                "owner": row["owner"],
                "program": row["programId"],
                "amount": int(amount),
                "decimals": decimals,
            }
        maps.append(result)
    return maps[0], maps[1]


def reconcile_legacy_token_accounts(
    transaction: Any, target_account_mints: dict[str, str]
) -> dict[str, Any]:
    """Reconcile target-touched account endpoints against all transaction token instructions.

    `target_account_mints` is derived only from the exact supported venue transfer subtree.
    Missing pre-state becomes zero only after a successful, ordered InitializeAccount. A missing
    post-state is a closed lifecycle state only after a successful CloseAccount. SyncNative never
    acts as a balancing residual: until ordered lamport funding, rent reserve and close effects are
    independently reconciled, a wrapped-SOL account touched by SyncNative has censored settlement.
    """
    try:
        normalized = normalize_compiled_transaction(transaction)
        if normalized["meta_error"] is not None:
            raise LedgerEvidenceError("failed_transaction")
        ordered = _ordered_instructions(normalized)
        before, after = _balance_maps(normalized)
        effects: list[dict[str, Any]] = []
        system_creates: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []
        for index, instruction in enumerate(ordered):
            create = _system_create_account(instruction)
            if create is not None:
                create["order"] = index
                system_creates.append(create)
            if instruction["program_id"] == TOKEN2022:
                touched = set(instruction["accounts"]) & set(target_account_mints)
                if touched:
                    unsupported.append(
                        {
                            "path": instruction["path"],
                            "reason": "token_2022_effect",
                            "accounts": sorted(touched),
                        }
                    )
                continue
            if instruction["program_id"] != TOKEN:
                continue
            effect = _legacy_token_effect(instruction)
            if effect is None:
                touched = set(instruction["accounts"]) & set(target_account_mints)
                if touched:
                    unsupported.append(
                        {
                            "path": instruction["path"],
                            "reason": "unsupported_legacy_token_instruction",
                            "accounts": sorted(touched),
                        }
                    )
                continue
            effect["order"] = index
            effects.append(effect)

        account_facts: dict[str, dict[str, Any]] = {}
        exact_accounts = 0
        censored_accounts = 0
        reason_counts: Counter[str] = Counter()
        censored_reason_counts: Counter[str] = Counter()
        for account, expected_mint in sorted(target_account_mints.items()):
            token_delta: int | None = None
            sync_residual: int | None = None
            observed_delta: int | None = None
            pre = before.get(account)
            post = after.get(account)
            relevant = [
                effect
                for effect in effects
                if account
                in (
                    effect.get("source"),
                    effect.get("destination"),
                    effect.get("account"),
                )
            ]
            bad = [row for row in unsupported if account in row.get("accounts", [])]
            inits = [row for row in relevant if row["kind"] == "initialize_account"]
            closes = [row for row in relevant if row["kind"] == "close_account"]
            syncs = [row for row in relevant if row["kind"] == "sync_native"]
            instruction_mints_match = all(
                effect.get("mint") in (None, expected_mint) for effect in relevant
            )
            if pre is None:
                opening = [
                    row
                    for row in inits
                    if row["order"]
                    < min(
                        (
                            effect["order"]
                            for effect in relevant
                            if effect["kind"]
                            not in ("initialize_account", "initialize_immutable_owner")
                        ),
                        default=10**18,
                    )
                ]
                opening_amount = 0 if len(opening) == 1 else None
                opening_state = (
                    "derived_zero_from_ordered_initialize" if opening_amount == 0 else "unknown"
                )
                if opening:
                    create = next(
                        (
                            item
                            for item in reversed(system_creates)
                            if item["account"] == account
                            and item["order"] < opening[-1]["order"]
                            and item["owner_program"] == TOKEN
                        ),
                        None,
                    )
                    if create is not None:
                        opening_state = "derived_zero_from_system_create_and_initialize"
                mint_proven = bool(opening and opening[-1]["mint"] == expected_mint)
                opening_owner = opening[-1].get("owner") if opening else None
            else:
                opening_amount = pre["amount"]
                opening_state = "exact_pre_balance"
                mint_proven = pre["mint"] == expected_mint and pre["program"] == TOKEN
                opening_owner = pre["owner"]

            last_balance_effect = max(
                (
                    row["order"]
                    for row in relevant
                    if row["kind"]
                    in {
                        "transfer",
                        "transfer_checked",
                        "mint_to",
                        "mint_to_checked",
                        "burn",
                        "burn_checked",
                        "sync_native",
                    }
                ),
                default=-1,
            )
            if post is None:
                closing = next(
                    (row for row in reversed(closes) if row["order"] > last_balance_effect),
                    None,
                )
                closing_state = "closed_by_ordered_token_instruction" if closing else "unknown"
                closing_amount = None
                if closing is not None and opening_amount is not None and not syncs:
                    token_delta = sum(
                        self_delta(effect, account)
                        for effect in relevant
                        if effect["kind"]
                        in {
                            "transfer",
                            "transfer_checked",
                            "mint_to",
                            "mint_to_checked",
                            "burn",
                            "burn_checked",
                        }
                    )
                    closing_amount = opening_amount + token_delta
                    if expected_mint != NATIVE_MINT and closing_amount != 0:
                        closing_state = "close_balance_not_zero"
                elif closing is not None and expected_mint == NATIVE_MINT:
                    closing_state = "native_account_closed_lamport_settlement_unknown"
            else:
                closing_state = "exact_post_balance"
                closing_amount = post["amount"]
                mint_proven = (
                    mint_proven and post["mint"] == expected_mint and post["program"] == TOKEN
                )
                if pre is not None and (
                    pre["owner"] != post["owner"] or pre["decimals"] != post["decimals"]
                ):
                    mint_proven = False
                elif opening_owner is not None and opening_owner != post["owner"]:
                    mint_proven = False

            if syncs and expected_mint != NATIVE_MINT:
                reason = "sync_native_non_native_mint"
            elif len(syncs) > 1:
                reason = "multiple_sync_native_resets"
            elif bad:
                reason = bad[0]["reason"]
            elif not mint_proven:
                reason = "account_mint_or_program_unproven"
            elif not instruction_mints_match:
                reason = "token_instruction_mint_mismatch"
            elif opening_amount is None:
                reason = "opening_boundary_unknown"
            elif post is not None and post["mint"] != expected_mint:
                reason = "post_mint_mismatch"
            elif closing_state == "native_account_closed_lamport_settlement_unknown":
                reason = (
                    "native_close_after_sync_amount_unknown"
                    if syncs
                    else "native_close_lamport_settlement_unknown"
                )
            elif post is None and closing_state != "closed_by_ordered_token_instruction":
                reason = "closing_boundary_unknown"
            elif closing_state == "close_balance_not_zero":
                reason = "non_native_close_balance_not_zero"
            elif syncs:
                reason = "native_sync_lamport_delta_unproven"
            else:
                token_delta = sum(
                    self_delta(effect, account)
                    for effect in relevant
                    if effect["kind"]
                    in {
                        "transfer",
                        "transfer_checked",
                        "mint_to",
                        "mint_to_checked",
                        "burn",
                        "burn_checked",
                    }
                )
                if post is not None:
                    observed_delta = post["amount"] - opening_amount
                    sync_residual = observed_delta - token_delta
                    if sync_residual and not syncs:
                        reason = "unattributed_token_balance_delta"
                    else:
                        reason = None
                elif expected_mint == NATIVE_MINT:
                    if syncs:
                        reason = "native_close_after_sync_amount_unknown"
                    else:
                        observed_delta = None
                        reason = None
                else:
                    observed_delta = -opening_amount
                    sync_residual = 0
                    reason = None

            censored = reason in {
                "native_close_after_sync_amount_unknown",
                "native_close_lamport_settlement_unknown",
                "native_sync_lamport_delta_unproven",
            }
            exact = reason is None
            if exact:
                exact_accounts += 1
            elif censored:
                assert reason is not None
                censored_accounts += 1
                censored_reason_counts[reason] += 1
            else:
                assert reason is not None
                reason_counts[reason] += 1
            account_facts[account] = {
                "status": "exact" if exact else "censored" if censored else "unknown",
                "reason": reason,
                "mint": expected_mint,
                "opening_state": opening_state,
                "opening_amount": opening_amount,
                "system_create_paths": [
                    item["path"] for item in system_creates if item["account"] == account
                ],
                "closing_state": closing_state,
                "closing_amount": closing_amount,
                "transfer_mint_burn_delta": token_delta,
                "sync_native_count": len(syncs),
                "sync_native_residual": sync_residual,
                "observed_endpoint_delta": observed_delta,
                "instruction_paths": [row["path"] for row in relevant],
            }

        unknown_accounts = len(target_account_mints) - exact_accounts - censored_accounts
        ledger_status = (
            "exact"
            if unknown_accounts == 0 and censored_accounts == 0
            else "censored_partial"
            if unknown_accounts == 0
            else "unknown_partial"
        )
        return {
            "contract": LEDGER_CONTRACT,
            "status": ledger_status,
            "target_account_count": len(target_account_mints),
            "exact_account_count": exact_accounts,
            "censored_account_count": censored_accounts,
            "unknown_account_count": unknown_accounts,
            "unknown_reasons": dict(sorted(reason_counts.items())),
            "censored_reasons": dict(sorted(censored_reason_counts.items())),
            "instruction_trace": "complete_as_returned_by_provider",
            "account_facts": account_facts,
            "unsupported_instruction_effects": unsupported,
        }
    except ValueError as exc:
        reason = getattr(exc, "category", None) or str(exc) or "ledger_schema_invalid"
        return {
            "contract": LEDGER_CONTRACT,
            "status": "unknown_partial",
            "target_account_count": len(target_account_mints),
            "exact_account_count": 0,
            "censored_account_count": 0,
            "unknown_account_count": len(target_account_mints),
            "unknown_reasons": {reason: len(target_account_mints)},
            "censored_reasons": {},
            "instruction_trace": "unqualified",
            "account_facts": {},
            "unsupported_instruction_effects": [],
        }
    except (KeyError, IndexError, TypeError):
        return {
            "contract": LEDGER_CONTRACT,
            "status": "unknown_partial",
            "target_account_count": len(target_account_mints),
            "exact_account_count": 0,
            "censored_account_count": 0,
            "unknown_account_count": len(target_account_mints),
            "unknown_reasons": {"ledger_schema_invalid": len(target_account_mints)},
            "censored_reasons": {},
            "instruction_trace": "unqualified",
            "account_facts": {},
            "unsupported_instruction_effects": [],
        }


def self_delta(effect: dict[str, Any], account: str) -> int:
    """Signed balance delta for one account under one supported token instruction."""
    kind = effect["kind"]
    amount = effect.get("amount")
    if type(amount) is not int or amount < 0:
        raise LedgerEvidenceError("token_instruction_amount_invalid")
    if kind in ("transfer", "transfer_checked"):
        if effect["source"] == account:
            return -amount
        if effect["destination"] == account:
            return amount
    if kind in ("mint_to", "mint_to_checked") and effect["destination"] == account:
        return amount
    if kind in ("burn", "burn_checked") and effect["source"] == account:
        return -amount
    return 0
