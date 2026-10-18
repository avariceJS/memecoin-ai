"""Source-bound structural observations for PumpSwap v1 buy-exact instructions.

This module distinguishes an instruction reference from an executed trade. It does not infer
settled amounts, market activity, or completeness from an instruction or transaction status.
"""

from __future__ import annotations

from typing import Any

from .compiled_economics import (
    FEE_RECIPIENTS_UPGRADE_2026_04_28,
    EconomicDiagnosticError,
    _is_writable,
    associated_token_address,
)
from .decoder import PROGRAM
from .normalize import NormalizationError, normalize_compiled_transaction
from .schema import SCHEMA

CONTRACT = "pumpswap_v1_buy_exact_quote_in_attempt_and_suffix_candidate_v1"
IDL_SHA256 = SCHEMA["provenance"]["sha256"]
FEE_RECIPIENTS_DOC_COMMIT = "8cda1fa30ea658b20909d8aedf002047119388d2"
FEE_RECIPIENTS_DOC_SHA256 = "8b05e0906eaf1746508b0b2f909ac7c344ce7be33a6564d595335c9fda399868"
DISCRIMINATOR = bytes(SCHEMA["instructions"]["buy_exact_quote_in"]["discriminator"])
ACCOUNT_ROLES = tuple(SCHEMA["instructions"]["buy_exact_quote_in"]["accounts"])
BASE_ACCOUNT_COUNT = len(ACCOUNT_ROLES)


def _reference_status(transaction: Any, normalized: dict[str, Any]) -> str:
    if normalized["meta_error"] is not None:
        return "failed_tx_attempt_only"
    return "successful_tx_instruction_reference"


def _decode_candidate(
    transaction: Any, normalized: dict[str, Any], instruction: dict[str, Any]
) -> dict[str, Any]:
    wire = bytes.fromhex(instruction["data_hex"])
    accounts = instruction["accounts"]
    status = _reference_status(transaction, normalized)
    result: dict[str, Any] = {
        "contract": CONTRACT,
        "path": instruction["path"],
        "outer_index": instruction["outer_index"],
        "stack_height": instruction["stack_height"],
        "transaction_status": status,
        "instruction_data_hex": wire.hex(),
        "instruction_data_length": len(wire),
        "argument_state": "unsupported_payload_shape",
        "arguments": None,
        "base_account_prefix_state": "unresolved",
        "base_account_count": BASE_ACCOUNT_COUNT,
        "instruction_account_count": len(accounts),
        "remaining_account_count": max(0, len(accounts) - BASE_ACCOUNT_COUNT),
        "suffix_state": "unsupported_suffix_layout",
        "suffix_checks": {},
        "settlement_state": "none_failed_transaction"
        if status == "failed_tx_attempt_only"
        else "not_assessed",
        "training_eligible": False,
        "execution_supported": False,
    }

    if wire[:8] != DISCRIMINATOR:
        result["reference_state"] = "not_target_discriminator"
        return result
    result["reference_state"] = "recognized_instruction_attempt"

    if len(wire) == 25 and wire[24] in (0, 1):
        result["argument_state"] = "pinned_idl_v1_shape"
        result["arguments"] = {
            "spendable_quote_in": int.from_bytes(wire[8:16], "little"),
            "min_base_amount_out": int.from_bytes(wire[16:24], "little"),
            "track_volume": bool(wire[24]),
            "raw_integer_units": True,
        }
    elif len(wire) == 25:
        result["argument_state"] = "pinned_idl_bool_value_invalid"

    if len(accounts) < BASE_ACCOUNT_COUNT:
        result["base_account_prefix_state"] = "shorter_than_pinned_idl"
        return result

    roles = dict(zip(ACCOUNT_ROLES, accounts[:BASE_ACCOUNT_COUNT], strict=True))
    prefix_errors = []
    if roles.get("program") != PROGRAM or instruction["program_id"] != PROGRAM:
        prefix_errors.append("program_role_mismatch")
    account_roles_by_key: dict[str, list[str]] = {}
    for name, value in roles.items():
        account_roles_by_key.setdefault(value, []).append(name)
    role_aliases = {value: names for value, names in account_roles_by_key.items() if len(names) > 1}
    result["base_account_prefix_state"] = (
        "pinned_idl_prefix_candidate" if not prefix_errors else "invalid_pinned_idl_prefix"
    )
    result["base_account_prefix_errors"] = prefix_errors
    result["base_account_role_aliases"] = role_aliases
    if prefix_errors:
        return result
    result["identified_roles"] = {
        name: roles[name]
        for name in (
            "pool",
            "user",
            "base_mint",
            "quote_mint",
            "user_base_token_account",
            "user_quote_token_account",
            "pool_base_token_account",
            "pool_quote_token_account",
            "base_token_program",
            "quote_token_program",
        )
    }

    remaining = accounts[BASE_ACCOUNT_COUNT:]
    if len(remaining) not in (2, 3):
        return result

    fee_recipient, fee_ata = remaining[-2:]
    suffix_checks: dict[str, Any] = {
        "fee_recipient_in_pinned_eight_address_set": (
            fee_recipient in FEE_RECIPIENTS_UPGRADE_2026_04_28
        ),
        "fee_ata_derives_from_recipient_quote_mint_and_quote_program": False,
        "fee_recipient_not_transaction_writable": False,
        "fee_ata_transaction_writable": False,
    }
    if suffix_checks["fee_recipient_in_pinned_eight_address_set"]:
        try:
            suffix_checks["fee_ata_derives_from_recipient_quote_mint_and_quote_program"] = (
                associated_token_address(
                    fee_recipient,
                    roles["quote_mint"],
                    roles["quote_token_program"],
                )
                == fee_ata
            )
            suffix_checks["fee_recipient_not_transaction_writable"] = not _is_writable(
                transaction, normalized, fee_recipient
            )
            suffix_checks["fee_ata_transaction_writable"] = _is_writable(
                transaction, normalized, fee_ata
            )
        except EconomicDiagnosticError as exc:
            suffix_checks["privilege_or_derivation_error"] = str(exc)
    result["suffix_checks"] = suffix_checks
    result["observed_fee_recipient"] = fee_recipient
    result["observed_fee_ata"] = fee_ata
    pair_valid = all(
        suffix_checks.get(key) is True
        for key in (
            "fee_recipient_in_pinned_eight_address_set",
            "fee_ata_derives_from_recipient_quote_mint_and_quote_program",
            "fee_recipient_not_transaction_writable",
            "fee_ata_transaction_writable",
        )
    )
    if not pair_valid:
        result["suffix_state"] = "fee_pair_unqualified"
    elif len(remaining) == 2:
        result["suffix_state"] = "documented_fee_pair_suffix_candidate"
    else:
        result["suffix_state"] = "documented_fee_pair_with_unresolved_predecessor"
        result["unresolved_predecessor_account"] = remaining[0]

    if result["argument_state"] != "pinned_idl_v1_shape":
        result["economic_state"] = "quarantined_unverified_instruction_payload"
    elif status == "failed_tx_attempt_only":
        result["economic_state"] = "failed_tx_no_settlement"
    elif result["suffix_state"] == "documented_fee_pair_suffix_candidate":
        result["economic_state"] = "not_assessed"
    else:
        result["economic_state"] = "quarantined_unresolved_suffix_or_layout"
    return result


def inspect_buy_exact_quote_in_v1(transaction: Any) -> dict[str, Any]:
    """Return one structural attempt record per recognized v1 reference in a transaction.

    A failed transaction remains an attempt with no settlement. Successful transactions also
    remain structural references; callers must use a separately frozen settlement ledger before
    recording any executed economic fact.
    """
    try:
        normalized = normalize_compiled_transaction(transaction)
    except NormalizationError as exc:
        return {
            "contract": CONTRACT,
            "status": "malformed_or_missing_trace",
            "normalization_error": exc.category,
            "references": [],
            "admission": "none",
        }

    references = []
    for instruction in [*normalized["outer_instructions"], *normalized["inner_instructions"]]:
        if instruction["program_id"] != PROGRAM:
            continue
        wire_hex = instruction["data_hex"]
        try:
            wire = bytes.fromhex(wire_hex)
        except ValueError:
            continue
        if wire[:8] != DISCRIMINATOR:
            continue
        references.append(_decode_candidate(transaction, normalized, instruction))

    status = (
        "failed_tx_attempt_only"
        if normalized["meta_error"] is not None and references
        else "structural_references_only"
        if references
        else "no_v1_reference"
    )
    return {
        "contract": CONTRACT,
        "status": status,
        "transaction_signatures": normalized["transaction_signatures"],
        "references": references,
        "settlement_state": "none_failed_transaction"
        if normalized["meta_error"] is not None and references
        else "not_assessed",
        "admission": "none",
        "training_eligible": False,
        "execution_supported": False,
    }
