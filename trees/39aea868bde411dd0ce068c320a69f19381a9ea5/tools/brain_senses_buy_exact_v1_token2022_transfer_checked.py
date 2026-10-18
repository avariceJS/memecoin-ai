#!/usr/bin/env python3
"""Offline replay of captured Token-2022 TransferChecked instructions in clean PumpSwap rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from memecoin_ai.domain import canonical  # noqa: E402
from memecoin_pumpswap.decoder import TOKEN2022, decode58  # noqa: E402
from tools.brain_senses_buy_exact_v1_census import _read_block  # noqa: E402
from tools.brain_senses_buy_exact_v1_token2022_close_classifier import (  # noqa: E402
    _json,
    _resolve_instruction,
)

CONTRACT = "pumpswap_buy_exact_v1_token2022_transfer_checked_v1"
CLEAN_CONTRACT = "pumpswap_v1_buy_exact_token2022_clean_stratum_trace_v1"
IDL_RECEIPT_PATH = (
    REPO_ROOT / "docs/evidence/pumpswap-buy-exact-sdk-builder-source-survey-2026-10-10.json"
)
IDL_SOURCE_PATH = (
    REPO_ROOT / "work/brain-senses-source-qual-20261009/official-pump-amm-idl/pump_amm-main.json"
)
IDL_COMMIT = "8cda1fa30ea658b20909d8aedf002047119388d2"
IDL_SHA256 = "b7d8c57a4d9c4dd0109a9ab893052352333252d4eedced10ac091d4d66cab89b"
IDL_INSTRUCTION = "buy_exact_quote_in"
PUMP_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
IDL_ACCOUNT_COUNT = 23
IDL_ACCOUNT_NAMES = (
    "pool",
    "user",
    "global_config",
    "base_mint",
    "quote_mint",
    "user_base_token_account",
    "user_quote_token_account",
    "pool_base_token_account",
    "pool_quote_token_account",
    "protocol_fee_recipient",
    "protocol_fee_recipient_token_account",
    "base_token_program",
    "quote_token_program",
    "system_program",
    "associated_token_program",
    "event_authority",
    "program",
    "coin_creator_vault_ata",
    "coin_creator_vault_authority",
    "global_volume_accumulator",
    "user_volume_accumulator",
    "fee_config",
    "fee_program",
)
TOKEN2022_COMMIT = "b4724e6e7fecc8fc36295f861a769401bf92b270"
TOKEN2022_FILES = {
    "instruction.rs": "672b3890cb58e5700a67f202491069ed44dffcbfa0e7d0b83a0cfbbb05b3a287",
    "processor.rs": "57e451a47aba32e0eac5ab079696b5ff6ac96532b19de5cec4d6bc610fe4ef04",
}
TOKEN2022_URLS = {
    "instruction.rs": (
        "https://raw.githubusercontent.com/solana-program/token-2022/"
        f"{TOKEN2022_COMMIT}/interface/src/instruction.rs"
    ),
    "processor.rs": (
        "https://raw.githubusercontent.com/solana-program/token-2022/"
        f"{TOKEN2022_COMMIT}/program/src/processor.rs"
    ),
}
TRANSFER_CHECKED_TAG = 12
TRANSFER_CHECKED_WIRE_LENGTH = 10
EXPECTED_INSTRUCTIONS = 3552


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_code_commit(code_commit: str | None) -> tuple[str, str]:
    if code_commit == "draft-uncommitted":
        return code_commit, "draft_uncommitted_tool"
    if not code_commit:
        raise ValueError("code_commit_required")
    resolved = subprocess.run(
        ["git", "rev-parse", "--verify", f"{code_commit}^{{commit}}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if resolved != code_commit:
        raise ValueError("code_commit_must_be_full_resolved_commit_hash")
    committed_tool = subprocess.run(
        [
            "git",
            "show",
            f"{resolved}:tools/brain_senses_buy_exact_v1_token2022_transfer_checked.py",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    if _sha256(committed_tool) != _sha256(Path(__file__).read_bytes()):
        raise ValueError("code_commit_tool_hash_mismatch")
    return resolved, "committed_tool"


def parse_transfer_checked_wire(data: bytes) -> dict[str, int]:
    """Decode tag 12: u64 amount in token atomic units plus u8 decimal-place argument.

    The decimal argument is metadata supplied for the mint check. This parser deliberately
    performs no atomic-to-UI scaling; these two fields have distinct dimensions.
    """
    if len(data) != TRANSFER_CHECKED_WIRE_LENGTH or data[0] != TRANSFER_CHECKED_TAG:
        raise ValueError("wire_not_exact_transfer_checked")
    return {
        "declared_amount_atomic": int.from_bytes(data[1:9], "little", signed=False),
        "decimals_argument": data[9],
    }


def pack_transfer_checked_wire(declared_amount_atomic: int, decimals_argument: int) -> bytes:
    """Pack wire fields without converting token atomic units to a display amount."""
    if type(declared_amount_atomic) is not int or not 0 <= declared_amount_atomic < 2**64:
        raise ValueError("declared_amount_atomic_out_of_range")
    if type(decimals_argument) is not int or not 0 <= decimals_argument <= 255:
        raise ValueError("decimals_argument_out_of_range")
    return (
        bytes([TRANSFER_CHECKED_TAG])
        + declared_amount_atomic.to_bytes(8, "little")
        + bytes([decimals_argument])
    )


def verify_token2022_sources(source_root: Path) -> dict[str, Any]:
    files = {}
    reasons = []
    for filename, expected_sha in TOKEN2022_FILES.items():
        path = source_root / filename
        actual_sha = _sha256(path.read_bytes()) if path.is_file() else None
        files[filename] = {
            "url": TOKEN2022_URLS[filename],
            "expected_sha256": expected_sha,
            "actual_sha256": actual_sha,
        }
        if actual_sha != expected_sha:
            reasons.append(f"token2022_source_hash_mismatch:{filename}")
    if source_root.name != TOKEN2022_COMMIT:
        reasons.append("token2022_source_commit_directory_mismatch")
    return {
        "commit": TOKEN2022_COMMIT,
        "source_root": str(source_root.resolve()),
        "files": files,
        "valid": not reasons,
        "failure_reasons": reasons,
    }


def verify_pump_idl(idl_receipt_path: Path, idl_source_path: Path) -> dict[str, Any]:
    receipt_bytes = idl_receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    pinned = receipt.get("pinned_official_idl", {})
    actual_sha = _sha256(idl_source_path.read_bytes()) if idl_source_path.is_file() else None
    idl = json.loads(idl_source_path.read_bytes()) if actual_sha else {}
    instruction = next(
        (
            item
            for item in idl.get("instructions", [])
            if isinstance(item, dict) and item.get("name") == IDL_INSTRUCTION
        ),
        None,
    )
    actual_names = (
        [account.get("name") for account in instruction.get("accounts", [])]
        if isinstance(instruction, dict)
        else []
    )
    receipt_valid = (
        pinned.get("repository") == "pump-fun/pump-public-docs"
        and pinned.get("commit") == IDL_COMMIT
        and pinned.get("path") == "idl/pump_amm.json"
        and pinned.get("sha256") == IDL_SHA256
        and idl.get("address") == PUMP_PROGRAM_ID
    )
    valid = bool(
        receipt_valid
        and actual_sha == IDL_SHA256
        and actual_names[:IDL_ACCOUNT_COUNT] == list(IDL_ACCOUNT_NAMES)
        and len(actual_names) == IDL_ACCOUNT_COUNT
        and instruction.get("discriminator")
        and isinstance(instruction.get("discriminator"), list)
    )
    return {
        "receipt_path": str(idl_receipt_path.resolve()),
        "receipt_sha256": _sha256(receipt_bytes),
        "commit": IDL_COMMIT,
        "source_path": str(idl_source_path.resolve()),
        "source_sha256": actual_sha,
        "expected_sha256": IDL_SHA256,
        "instruction": IDL_INSTRUCTION,
        "program_id": idl.get("address"),
        "account_names": actual_names,
        "discriminator": instruction.get("discriminator") if instruction else None,
        "discriminator_bytes": instruction.get("discriminator") if instruction else None,
        "valid": valid,
    }


def _candidate_instructions(clean: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if clean.get("contract") != CLEAN_CONTRACT or clean.get("schema_version") != "1":
        raise ValueError("clean_stratum_receipt_contract_mismatch")
    if len(clean.get("rows", [])) != 1146:
        raise ValueError("clean_stratum_reference_denominator_mismatch")
    candidates = []
    for row in clean["rows"]:
        traces = row.get("target_instruction_trace")
        if not isinstance(traces, list):
            raise ValueError("clean_stratum_trace_missing")
        for trace in traces:
            if not isinstance(trace, dict):
                continue
            wire_hex = trace.get("wire_hex")
            if (
                trace.get("program_id") == TOKEN2022
                and isinstance(wire_hex, str)
                and wire_hex.startswith("0c")
                and len(wire_hex) == 20
            ):
                candidates.append((row, trace))
    if len(candidates) != EXPECTED_INSTRUCTIONS:
        raise ValueError(f"transfer_checked_trace_denominator_mismatch:{len(candidates)}")
    return candidates


def _request_capture_binding(
    capture_root: Path, result: dict[str, Any], slot: int
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    sequence = result.get("sequence")
    if type(sequence) is not int or sequence < 0:
        raise ValueError(f"capture_sequence_invalid:{slot}")
    capture_dir = (capture_root / result.get("capture_path", "")).resolve()
    if not capture_dir.is_relative_to(capture_root.resolve()):
        raise ValueError(f"capture_path_outside_root:{slot}")
    receipt_path = capture_dir / "receipt.json"
    receipt_bytes = receipt_path.read_bytes()
    capture_receipt = json.loads(receipt_bytes)
    request = json.loads(capture_receipt.get("request_json", "{}"))
    params = request.get("params")
    reservation_path = capture_root / "requests" / f"{sequence:012d}.json"
    reservation = _json(reservation_path)
    request_hash = _sha256(canonical(request).encode())
    params_hash = _sha256(canonical(params).encode()) if isinstance(params, list) else None
    if (
        result.get("provider") != "helius-free"
        or result.get("method") != "getBlock"
        or result.get("detail") != "full"
        or result.get("status") != "captured"
        or result.get("result_status") != "parsed"
        or capture_receipt.get("source_alias") != "helius-free"
        or request.get("method") != "getBlock"
        or not isinstance(params, list)
        or len(params) != 2
        or params[0] != slot
        or not isinstance(params[1], dict)
        or params[1].get("commitment") != "finalized"
        or params[1].get("transactionDetails") != "full"
        or reservation.get("sequence") != sequence
        or reservation.get("provider") != "helius-free"
        or reservation.get("method") != "getBlock"
        or reservation.get("slot") != slot
        or reservation.get("detail") != "full"
        or reservation.get("request_reservation") != 1
        or reservation.get("request_sha256") != request_hash
        or reservation.get("params_sha256") != params_hash
        or result.get("response_sha256") != capture_receipt.get("response_sha256")
        or result.get("response_bytes") != capture_receipt.get("response_bytes")
        or result.get("stored_response_bytes") != capture_receipt.get("storage", {}).get("bytes")
        or result.get("receipt_ns") != capture_receipt.get("received_ns")
    ):
        raise ValueError(f"capture_request_receipt_binding_invalid:{slot}")
    storage_path = (capture_dir / capture_receipt["storage"]["path"]).resolve()
    if not storage_path.is_relative_to(capture_root.resolve()):
        raise ValueError(f"capture_storage_path_outside_root:{slot}")
    return capture_receipt, reservation, storage_path


def _empty_instruction_result(
    row: dict[str, Any], trace: dict[str, Any], reason: str
) -> dict[str, Any]:
    return {
        "signature": row.get("signature"),
        "slot": row.get("slot"),
        "transaction_index": row.get("transaction_index"),
        "instruction_path": trace.get("path"),
        "stack_height": trace.get("stack_height"),
        "classification": "unknown",
        "checks": {"raw_capture_and_transaction_binding": False},
        "failure_reasons": [reason],
        "declared_amount_atomic": None,
        "decimals_argument": None,
        "amount_dimension": "token_atomic_units",
        "decimals_dimension": "decimal_places_expected",
        "amount_semantics": "declared_instruction_argument_not_realized_effect",
        "role_mapping_status": "unknown",
        "transfer_accounts": [],
        "capture_binding": None,
    }


def _idl_roles_for_transaction(
    row: dict[str, Any],
    transaction: dict[str, Any],
    instruction_index: int,
    idl: dict[str, Any],
) -> tuple[dict[str, str], bool, list[str]]:
    reasons = []
    tx_inner = transaction.get("transaction")
    message = tx_inner.get("message") if isinstance(tx_inner, dict) else None
    instructions = message.get("instructions") if isinstance(message, dict) else None
    if not isinstance(instructions, list) or not 0 <= instruction_index < len(instructions):
        return {}, False, ["pump_idl_outer_instruction_path_invalid"]
    outer_ix = instructions[instruction_index]
    try:
        program_id, ordered = _resolve_instruction(transaction, outer_ix)
        data = decode58(outer_ix.get("data"))
    except (TypeError, ValueError) as exc:
        return {}, False, [f"pump_idl_outer_instruction_unresolved:{exc}"]
    discriminator = bytes(idl.get("discriminator_bytes", []))
    if program_id != PUMP_PROGRAM_ID:
        reasons.append("pump_idl_outer_program_mismatch")
    if not discriminator or data[: len(discriminator)] != discriminator:
        reasons.append("pump_idl_buy_exact_discriminator_mismatch")
    if len(ordered) < IDL_ACCOUNT_COUNT:
        reasons.append("pump_idl_fixed_account_list_short")
    candidate_path = row.get("instruction_path")
    if not (
        isinstance(candidate_path, list)
        and len(candidate_path) == 1
        and type(candidate_path[0]) is int
        and candidate_path[0] == instruction_index
    ):
        reasons.append("trace_parent_not_clean_row_candidate")
    target_accounts = row.get("target_accounts", {})
    if not isinstance(target_accounts, dict):
        reasons.append("clean_row_target_accounts_missing")
    role_values = {}
    if not reasons:
        for name, account in zip(IDL_ACCOUNT_NAMES, ordered[:IDL_ACCOUNT_COUNT], strict=True):
            expected = target_accounts.get(name)
            if expected is not None:
                if account["pubkey"] != expected:
                    reasons.append(f"pump_idl_target_role_account_mismatch:{name}")
                else:
                    role_values[name] = account["pubkey"]
    if reasons:
        role_values = {}
    return role_values, not reasons, sorted(set(reasons))


def _classify_instruction(
    row: dict[str, Any],
    trace: dict[str, Any],
    transaction: dict[str, Any],
    *,
    idl_instruction: dict[str, Any],
    capture_binding: dict[str, Any],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    reasons = []
    path = trace.get("path")
    outer_index = (
        path[0] if isinstance(path, list) and len(path) == 2 and type(path[0]) is int else None
    )
    raw_ix = None
    if outer_index is not None and isinstance(path, list) and len(path) == 2:
        inner_groups = transaction.get("meta", {}).get("innerInstructions")
        if isinstance(inner_groups, list):
            groups = [
                group
                for group in inner_groups
                if isinstance(group, dict) and group.get("index") == outer_index
            ]
            if len(groups) == 1 and isinstance(groups[0].get("instructions"), list):
                inner_index = path[1]
                if type(inner_index) is int and 0 <= inner_index < len(groups[0]["instructions"]):
                    raw_ix = groups[0]["instructions"][inner_index]
    checks["instruction_path_resolves"] = isinstance(raw_ix, dict)
    if not checks["instruction_path_resolves"]:
        reasons.append("instruction_path_unresolved")

    signatures = transaction.get("transaction", {}).get("signatures")
    checks["transaction_signature_matches"] = bool(
        isinstance(signatures, list) and signatures and signatures[0] == row.get("signature")
    )
    if not checks["transaction_signature_matches"]:
        reasons.append("transaction_signature_mismatch")
    meta = transaction.get("meta")
    checks["transaction_succeeded"] = (
        isinstance(meta, dict) and "err" in meta and meta["err"] is None
    )
    if not checks["transaction_succeeded"]:
        reasons.append("transaction_status_not_success")
    checks["trace_path_and_stack_match"] = bool(
        isinstance(path, list)
        and len(path) == 2
        and outer_index is not None
        and trace.get("stack_height") == 2
        and raw_ix is not None
        and raw_ix.get("stackHeight") == 2
    )
    if not checks["trace_path_and_stack_match"]:
        reasons.append("trace_path_or_stack_mismatch")

    raw_data = None
    program_id = None
    accounts = []
    if isinstance(raw_ix, dict):
        try:
            program_id, accounts = _resolve_instruction(transaction, raw_ix)
            raw_data = decode58(raw_ix.get("data"))
        except (TypeError, ValueError) as exc:
            reasons.append(f"raw_instruction_unresolved:{exc}")
    checks["exact_token2022_program"] = (
        program_id == TOKEN2022 and trace.get("program_id") == TOKEN2022
    )
    if not checks["exact_token2022_program"]:
        reasons.append("token2022_program_id_mismatch")
    trace_wire = None
    try:
        trace_wire = bytes.fromhex(trace.get("wire_hex", ""))
    except (TypeError, ValueError):
        pass
    checks["exact_trace_and_raw_transfer_checked_wire"] = bool(
        raw_data is not None
        and len(raw_data) == TRANSFER_CHECKED_WIRE_LENGTH
        and raw_data[0] == TRANSFER_CHECKED_TAG
        and raw_data == trace_wire
        and trace.get("wire_sha256") == _sha256(raw_data)
    )
    if not checks["exact_trace_and_raw_transfer_checked_wire"]:
        reasons.append("transfer_checked_wire_mismatch")
    checks["minimum_transfer_account_count"] = len(accounts) >= 4
    if not checks["minimum_transfer_account_count"]:
        reasons.append("transfer_checked_account_count_below_four")

    amount = None
    decimals = None
    if (
        raw_data is not None
        and len(raw_data) == TRANSFER_CHECKED_WIRE_LENGTH
        and raw_data[0] == TRANSFER_CHECKED_TAG
    ):
        parsed = parse_transfer_checked_wire(raw_data)
        amount = parsed["declared_amount_atomic"]
        decimals = parsed["decimals_argument"]

    role_values, idl_supported, idl_reasons = _idl_roles_for_transaction(
        row, transaction, outer_index if outer_index is not None else -1, idl_instruction
    )
    candidate_path = row.get("instruction_path")
    trace_parent_matches_candidate = bool(
        isinstance(candidate_path, list)
        and len(candidate_path) == 1
        and outer_index is not None
        and candidate_path[0] == outer_index
    )
    transfer_accounts = []
    labels = ("source", "mint", "destination", "authority")
    for index, account in enumerate(accounts):
        matching_roles = [
            name for name, pubkey in role_values.items() if pubkey == account["pubkey"]
        ]
        transfer_accounts.append(
            {
                "operand": labels[index] if index < len(labels) else "additional_account_meta",
                "pubkey": account["pubkey"],
                "writable_potential": account["writable_potential"],
                "signer_potential": account["signer_potential"],
                "target_roles": matching_roles if idl_supported else [],
                "role_mapping_source": "pinned_pump_idl_fixed_account_alignment"
                if idl_supported and matching_roles
                else "none",
            }
        )
    core_pass = all(checks.values())
    classification = "transfer_checked_structural_pass" if core_pass else "unknown"
    return {
        "signature": row.get("signature"),
        "slot": row.get("slot"),
        "transaction_index": row.get("transaction_index"),
        "instruction_path": path,
        "stack_height": trace.get("stack_height"),
        "classification": classification,
        "checks": checks,
        "failure_reasons": sorted(set(reasons)),
        "declared_amount_atomic": amount,
        "decimals_argument": decimals,
        "amount_dimension": "token_atomic_units",
        "decimals_dimension": "decimal_places_expected",
        "amount_semantics": "declared_instruction_argument_not_realized_effect",
        "role_mapping_status": "pinned_pump_idl_supported" if idl_supported else "unknown",
        "trace_parent_matches_clean_row_candidate": trace_parent_matches_candidate,
        "role_mapping_failure_reasons": idl_reasons,
        "transfer_accounts": transfer_accounts,
        "capture_binding": capture_binding,
    }


def derive_receipt(
    clean_receipt_path: Path,
    capture_root: Path,
    token2022_source_root: Path,
    idl_receipt_path: Path,
    idl_source_path: Path,
    output_path: Path,
    *,
    code_commit: str | None,
) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("output_exists_refusing_overwrite")
    clean_bytes = clean_receipt_path.read_bytes()
    clean = json.loads(clean_bytes)
    if not isinstance(clean, dict):
        raise ValueError("clean_stratum_receipt_not_object")
    source = clean.get("source")
    if not isinstance(source, dict) or source.get("network_requests") != 0:
        raise ValueError("clean_stratum_source_binding_invalid")
    capture_root = capture_root.resolve()
    if capture_root != Path(source.get("capture_root", "")).resolve():
        raise ValueError("capture_root_binding_mismatch")
    plan_bytes = (capture_root / "plan.json").read_bytes()
    plan_hash = _sha256(plan_bytes)
    if plan_hash != source.get("capture_plan_sha256"):
        raise ValueError("capture_plan_hash_mismatch")
    token_source = verify_token2022_sources(token2022_source_root.resolve())
    idl_pin = verify_pump_idl(idl_receipt_path, idl_source_path)
    code_commit, provenance_state = _validate_code_commit(code_commit)
    candidates = _candidate_instructions(clean)
    if len(candidates) != EXPECTED_INSTRUCTIONS:
        raise ValueError("candidate_count_invariant_failed")

    by_slot: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    for row, trace in candidates:
        by_slot[row["slot"]].append((row, trace))
    results_by_slot: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for result_path in (capture_root / "request-results").glob("*.json"):
        result = _json(result_path)
        slot = result.get("slot")
        if (
            type(slot) is int
            and slot in by_slot
            and result.get("provider") == "helius-free"
            and result.get("method") == "getBlock"
            and result.get("detail") == "full"
        ):
            results_by_slot[slot].append(result)

    output_rows = []
    for slot in sorted(by_slot):
        selected = by_slot[slot]
        slot_results = results_by_slot.get(slot, [])
        if len(slot_results) != 1:
            reason = "full_block_result_missing_or_ambiguous"
            output_rows.extend(
                _empty_instruction_result(row, trace, reason) for row, trace in selected
            )
            continue
        result = slot_results[0]
        try:
            capture_receipt, reservation, capture_path = _request_capture_binding(
                capture_root, result, slot
            )
            block = _read_block("zstd", capture_path, capture_receipt)
            transactions = block.get("transactions")
            if (
                block.get("blockhash") != result.get("blockhash")
                or block.get("parentSlot") != result.get("parent_slot")
                or block.get("blockTime") != result.get("block_time_seconds")
                or not isinstance(transactions, list)
                or len(transactions) != result.get("transaction_count")
            ):
                raise ValueError("full_block_identity_or_transaction_count_mismatch")
            binding_base = {
                "sequence": result.get("sequence"),
                "request_sha256": reservation["request_sha256"],
                "params_sha256": reservation["params_sha256"],
                "request_reserved_ns": reservation.get("reserved_ns"),
                "request_started_ns": capture_receipt.get("request_started_ns"),
                "receipt_ns": capture_receipt.get("received_ns"),
                "response_sha256": result.get("response_sha256"),
                "response_bytes": result.get("response_bytes"),
                "capture_receipt_sha256": _sha256(
                    (capture_root / result["capture_path"] / "receipt.json").read_bytes()
                ),
                "stored_response_sha256": capture_receipt["storage"]["sha256"],
                "stored_response_bytes": capture_receipt["storage"]["bytes"],
                "blockhash": block.get("blockhash"),
                "parent_slot": block.get("parentSlot"),
                "block_time_seconds": block.get("blockTime"),
                "transaction_count": len(transactions),
            }
            for row, trace in selected:
                tx_index = row.get("transaction_index")
                if type(tx_index) is not int or not 0 <= tx_index < len(transactions):
                    output_rows.append(
                        _empty_instruction_result(row, trace, "transaction_index_out_of_range")
                    )
                    continue
                transaction = transactions[tx_index]
                tx_sig = transaction.get("transaction", {}).get("signatures", [None])[0]
                row_binding = {
                    **binding_base,
                    "transaction_signature_matches": tx_sig == row.get("signature"),
                }
                result_row = _classify_instruction(
                    row,
                    trace,
                    transaction,
                    idl_instruction=idl_pin,
                    capture_binding=row_binding,
                )
                if tx_sig != row.get("signature"):
                    result_row["classification"] = "unknown"
                    result_row["failure_reasons"] = sorted(
                        set(
                            result_row["failure_reasons"]
                            + ["full_block_signature_location_mismatch"]
                        )
                    )
                if not token_source["valid"]:
                    result_row["classification"] = "unknown"
                    result_row["failure_reasons"] = sorted(
                        set(result_row["failure_reasons"] + ["pinned_token2022_source_invalid"])
                    )
                if not idl_pin["valid"]:
                    result_row["role_mapping_status"] = "unknown"
                    result_row["role_mapping_failure_reasons"] = ["pinned_pump_idl_invalid"]
                    for operand in result_row["transfer_accounts"]:
                        operand["target_roles"] = []
                        operand["role_mapping_source"] = "none"
                output_rows.append(result_row)
        except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            output_rows.extend(
                _empty_instruction_result(row, trace, f"capture_or_block_invalid:{exc}")
                for row, trace in selected
            )

    if len(output_rows) != EXPECTED_INSTRUCTIONS:
        raise ValueError(f"output_instruction_denominator_mismatch:{len(output_rows)}")
    counts = Counter(item["classification"] for item in output_rows)
    reasons = Counter(reason for item in output_rows for reason in item.get("failure_reasons", []))
    idl_role_counts = Counter(
        role
        for item in output_rows
        for operand in item.get("transfer_accounts", [])
        for role in operand.get("target_roles", [])
    )
    result = {
        "schema_version": "1",
        "contract": CONTRACT,
        "claim": (
            "offline source-bound decoding of declared Token-2022 TransferChecked wire arguments "
            "in 3,552 captured traces; no UI conversion, successful-transfer amount, or account "
            "balance delta, fee settlement, PumpSwap economic, or market completeness claim"
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
        "code_commit": code_commit,
        "provenance_state": provenance_state,
        "tool_sha256": _sha256(Path(__file__).read_bytes()),
        "source": {
            "clean_stratum_receipt": str(clean_receipt_path.resolve()),
            "clean_stratum_receipt_sha256": _sha256(clean_bytes),
            "capture_root": str(capture_root),
            "capture_plan_sha256": plan_hash,
            "token2022_official_source": token_source,
            "pump_idl_pin": idl_pin,
            "network_requests": 0,
        },
        "scope": {
            "clean_reference_denominator": 1146,
            "target_trace_denominator": EXPECTED_INSTRUCTIONS,
            "selection_predicate": {
                "program_id": TOKEN2022,
                "instruction_tag": TRANSFER_CHECKED_TAG,
                "wire_length": TRANSFER_CHECKED_WIRE_LENGTH,
            },
            "selected_slots": len(by_slot),
        },
        "wire_dimensions": {
            "declared_amount_atomic": (
                "u64 token atomic/base units exactly as encoded; no UI scaling"
            ),
            "decimals_argument": (
                "u8 decimal-place expectation exactly as encoded; not a multiplier"
            ),
            "conversion_applied": False,
            "realized_effect_inferred": False,
        },
        "summary": {
            "instructions": len(output_rows),
            "classifications": dict(sorted(counts.items())),
            "failure_reasons": dict(sorted(reasons.items())),
            "idl_supported_target_role_operand_counts": dict(sorted(idl_role_counts.items())),
            "declared_amount_missing": sum(
                item["declared_amount_atomic"] is None for item in output_rows
            ),
            "transaction_failed": sum(
                not item["checks"].get("transaction_succeeded", False) for item in output_rows
            ),
            "role_mapping_unknown": sum(
                item["role_mapping_status"] != "pinned_pump_idl_supported" for item in output_rows
            ),
            "trace_parent_differs_from_clean_row_candidate": sum(
                not item.get("trace_parent_matches_clean_row_candidate", False)
                for item in output_rows
            ),
        },
        "rows": output_rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clean-receipt",
        type=Path,
        default=REPO_ROOT
        / "docs/evidence/pumpswap-buy-exact-v1-token2022-clean-stratum-2026-10-10.json",
    )
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument(
        "--token2022-source-root",
        type=Path,
        default=REPO_ROOT / "work/official-sources/solana-token-2022" / TOKEN2022_COMMIT,
    )
    parser.add_argument("--idl-receipt", type=Path, default=IDL_RECEIPT_PATH)
    parser.add_argument("--idl-source", type=Path, default=IDL_SOURCE_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--code-commit", default=os.environ.get("BRAIN_SENSES_CODE_COMMIT"))
    args = parser.parse_args(argv)
    receipt = derive_receipt(
        args.clean_receipt,
        args.capture_root,
        args.token2022_source_root,
        args.idl_receipt,
        args.idl_source,
        args.output,
        code_commit=args.code_commit,
    )
    print(
        json.dumps(
            {"contract": CONTRACT, "output": str(args.output), **receipt["summary"]}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
