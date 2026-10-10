#!/usr/bin/env python3
"""Source-pinned offline qualification for PumpSwap buyExact pool-v2 prefixes."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from memecoin_pumpswap.buy_exact_v1 import (  # noqa: E402
    ACCOUNT_ROLES,
    DISCRIMINATOR,
    FEE_RECIPIENTS_DOC_COMMIT,
    FEE_RECIPIENTS_DOC_SHA256,
    IDL_SHA256,
)
from memecoin_pumpswap.compiled_economics import (  # noqa: E402
    FEE_RECIPIENTS_UPGRADE_2026_04_28,
    associated_token_address,
)
from tools.brain_senses_buy_exact_v1_census import _capture_rows, _read_block  # noqa: E402
from tools.brain_senses_buy_exact_v1_role_qualifier import (  # noqa: E402
    _compiled_instruction,
    _roles_from_raw,
)
from memecoin_pumpswap.buy_exact_v1 import inspect_buy_exact_quote_in_v1  # noqa: E402

CONTRACT = "pumpswap_v1_buy_exact_pool_v2_three_tail_prefix_v2_raw_report_reconciled"
REPORT_TOOL = REPO_ROOT / "tools/brain_senses_buy_exact_v1_census.py"
REPORT_IMPLEMENTATION = REPO_ROOT / "src/memecoin_pumpswap/buy_exact_v1.py"
SDK_VERSION = "2.1.0"
SDK_INTEGRITY = (
    "sha512-O0ulb5In/pMzxpcWIkzU17N27HQNZhGGof4fhgXZUkRj/vDbrZsDrViNoouhR52bvtfRf61e/bSvEg/h+35ueA=="
)
SDK_TARBALL_SHA256 = "1ef56832347f06653acf154d8e2072f1bfc9343f0e6c203cf4517bb1948e8865"
SDK_SOURCE_HASHES = {
    "src/sdk/pda.ts": "1f207494497a227cb4bdca62ba1142a7d63cb00e14083fee48476ee36ba4d543",
    "src/sdk/offlinePumpAmm.ts": "ac8312ce476d56f6ce9d47019ba779144fa4c3f3b9db2939693f4b12a727b50b",
    "src/idl/pump_amm.json": "b7d8c57a4d9c4dd0109a9ab893052352333252d4eedced10ac091d4d66cab89b",
}
PINNED_LOCAL_IDL_SHA256 = "b7d8c57a4d9c4dd0109a9ab893052352333252d4eedced10ac091d4d66cab89b"
PINNED_FEE_RECIPIENT_SET_SHA256 = "45b8977270d27ca0105afb3bd235dbc66abaf700b10122ff621c2258a940a341"
SDK_PUBLICATION_TIME = "2026-10-07T19:49:57.700Z"
SDK_REGISTRY_URL = "https://registry.npmjs.org/@pump-fun/pump-swap-sdk/-/pump-swap-sdk-2.1.0.tgz"
SDK_DEPENDENCIES = {"@solana/web3.js": "1.98.2", "@solana/spl-token": "0.4.9", "bn.js": "5.2.2"}
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
REPORTED_ROLE_NAMES = (
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
PREDICATE = {
    "instruction_data_length": 25,
    "track_volume_byte_valid": [0, 1],
    "fixed_account_count": 23,
    "remaining_account_count": 3,
    "prefix_role_state": "sdk_source_bound_pool_v2_pda_candidate",
    "remaining_account_roles": [
        {
            "index_zero_based": 23,
            "role": "sdk_derived_pool_v2_pda_from_idl_base_mint",
            "transaction_writable": False,
            "signer": False,
            "must_not_equal": "official_sdk_cashback_quote_ata",
        },
        {
            "index_zero_based": 24,
            "role": "pinned_fee_wallet",
            "transaction_writable": False,
            "signer": False,
        },
        {
            "index_zero_based": 25,
            "role": "fee_wallet_ata_for_idl_quote_mint_and_quote_token_program",
            "transaction_writable": True,
            "signer": False,
        },
    ],
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("expected_json_object")
    return value


def _verify_sdk(sdk_root: Path, node_dependencies: Path) -> dict[str, Any]:
    package_root = sdk_root.resolve()
    package = _read_object(package_root / "package.json")
    if package.get("version") != SDK_VERSION or package.get("name") != "@pump-fun/pump-swap-sdk":
        raise ValueError("official_sdk_package_identity_mismatch")
    for rel, expected in SDK_SOURCE_HASHES.items():
        if _sha256((package_root / rel).read_bytes()) != expected:
            raise ValueError(f"official_sdk_source_hash_mismatch:{rel}")
    tarball = package_root.parent.parent / "pump-fun-pump-swap-sdk-2.1.0.tgz"
    tarball_bytes = tarball.read_bytes() if tarball.is_file() else b""
    if not tarball_bytes or _sha256(tarball_bytes) != SDK_TARBALL_SHA256:
        raise ValueError("official_sdk_tarball_hash_mismatch")
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(tarball_bytes).digest()).decode("ascii")
    metadata_path = package_root.parent.parent / "pack-metadata.json"
    metadata = json.loads(metadata_path.read_text())[0]
    if metadata.get("integrity") != SDK_INTEGRITY or integrity != SDK_INTEGRITY:
        raise ValueError("official_sdk_npm_integrity_mismatch")
    if IDL_SHA256 != PINNED_LOCAL_IDL_SHA256:
        raise ValueError("local_pinned_idl_hash_mismatch")
    fee_recipient_set_sha = _sha256(
        json.dumps(sorted(FEE_RECIPIENTS_UPGRADE_2026_04_28), separators=(",", ":")).encode()
    )
    if (
        fee_recipient_set_sha != PINNED_FEE_RECIPIENT_SET_SHA256
        or FEE_RECIPIENTS_DOC_COMMIT != "8cda1fa30ea658b20909d8aedf002047119388d2"
        or FEE_RECIPIENTS_DOC_SHA256 != "8b05e0906eaf1746508b0b2f909ac7c344ce7be33a6564d595335c9fda399868"
    ):
        raise ValueError("pinned_fee_recipient_source_mismatch")
    for package_name, expected_version in SDK_DEPENDENCIES.items():
        dependency_package = node_dependencies / package_name / "package.json"
        dependency = _read_object(dependency_package)
        if dependency.get("version") != expected_version:
            raise ValueError(f"sdk_dependency_version_mismatch:{package_name}")
    sdk_idl = _read_object(package_root / "src/idl/pump_amm.json")
    sdk_instruction = next(
        (row for row in sdk_idl.get("instructions", []) if row.get("name") == "buy_exact_quote_in"),
        None,
    )
    if (
        sdk_idl.get("address") != PUMP_AMM_PROGRAM
        or sdk_instruction is None
        or tuple(row.get("name") for row in sdk_instruction.get("accounts", [])) != ACCOUNT_ROLES
        or sdk_instruction.get("discriminator") != list(DISCRIMINATOR)
        or sdk_instruction.get("args")
        != [
            {"name": "spendable_quote_in", "type": "u64"},
            {"name": "min_base_amount_out", "type": "u64"},
            {"name": "track_volume", "type": {"defined": {"name": "OptionBool"}}},
        ]
    ):
        raise ValueError("official_sdk_idl_contract_mismatch")
    return {
        "package": "@pump-fun/pump-swap-sdk",
        "version": SDK_VERSION,
        "publication_time_utc": SDK_PUBLICATION_TIME,
        "registry_url": SDK_REGISTRY_URL,
        "npm_integrity": SDK_INTEGRITY,
        "tarball_sha256": SDK_TARBALL_SHA256,
        "source_sha256": SDK_SOURCE_HASHES,
        "local_pinned_idl_sha256": PINNED_LOCAL_IDL_SHA256,
        "pump_amm_program": PUMP_AMM_PROGRAM,
        "isolated_node_dependencies": SDK_DEPENDENCIES,
        "node_version": subprocess.run(["node", "--version"], check=True, capture_output=True, text=True).stdout.strip(),
    }


def _transaction_privilege(
    transaction: dict[str, Any], account_index: int
) -> tuple[bool, bool]:
    message = transaction.get("transaction", {}).get("message", {})
    meta = transaction.get("meta", {})
    static = message.get("accountKeys")
    header = message.get("header")
    if not isinstance(static, list) or not isinstance(header, dict):
        raise ValueError("message_header_or_account_keys_missing")
    required = header.get("numRequiredSignatures")
    readonly_signed = header.get("numReadonlySignedAccounts")
    readonly_unsigned = header.get("numReadonlyUnsignedAccounts")
    if any(type(value) is not int for value in (required, readonly_signed, readonly_unsigned)):
        raise ValueError("message_header_privilege_counts_invalid")
    if not (0 <= readonly_signed <= required <= len(static)):
        raise ValueError("message_header_privilege_counts_inconsistent")
    if not (0 <= readonly_unsigned <= len(static) - required):
        raise ValueError("message_header_readonly_unsigned_invalid")
    if account_index < 0:
        raise ValueError("account_index_negative")
    if account_index < len(static):
        signer = account_index < required
        writable = (
            account_index < required - readonly_signed
            if signer
            else account_index < len(static) - readonly_unsigned
        )
        return writable, signer
    loaded = meta.get("loadedAddresses")
    if not isinstance(loaded, dict):
        raise ValueError("loaded_addresses_missing")
    loaded_writable = loaded.get("writable")
    loaded_readonly = loaded.get("readonly")
    if not isinstance(loaded_writable, list) or not isinstance(loaded_readonly, list):
        raise ValueError("loaded_address_lists_invalid")
    offset = account_index - len(static)
    if offset < len(loaded_writable):
        return True, False
    if offset < len(loaded_writable) + len(loaded_readonly):
        return False, False
    raise ValueError("account_index_exceeds_resolved_message")


def _frame_binding(
    report: dict[str, Any], epoch_root: Path, frame_receipt_path: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source = report.get("source")
    if not isinstance(source, dict):
        raise ValueError("report_source_binding_missing")
    frame_bytes = frame_receipt_path.read_bytes()
    plan_bytes = (epoch_root / "epoch-plan.json").read_bytes()
    summary_bytes = (epoch_root / "epoch-summary.json").read_bytes()
    frame_receipt = json.loads(frame_bytes)
    frame = frame_receipt.get("frame")
    summary = json.loads(summary_bytes)
    frame_receipt_hash = source.get("frame_receipt_sha256", source.get("provider_audit_receipt_sha256"))
    plan_hash = source.get("epoch_plan_sha256", source.get("plan_sha256"))
    summary_hash = source.get("epoch_summary_sha256", frame.get("summary_sha256") if isinstance(frame, dict) else None)
    if (
        Path(source.get("root", "")).resolve() != epoch_root.resolve()
        or _sha256(frame_bytes) != frame_receipt_hash
        or _sha256(plan_bytes) != plan_hash
        or _sha256(summary_bytes) != summary_hash
        or not isinstance(frame, dict)
        or frame.get("status") != "frame_complete"
        or frame.get("reservation_status") != "reconciled"
        or frame.get("plan_sha256") != _sha256(plan_bytes)
        or frame.get("summary_sha256") != _sha256(summary_bytes)
        or frame.get("forward_only", {}).get("missed_slot_backfill") is not False
        or summary.get("status") != "frame_complete"
        or not isinstance(summary.get("selected_slots"), list)
        or len(summary["selected_slots"]) != 16
    ):
        raise ValueError("sealed_frame_or_summary_binding_mismatch")
    source_captures = source.get("full_block_captures", source.get("captures"))
    if not isinstance(source_captures, list) or len(source_captures) != 16:
        raise ValueError("report_full_block_capture_manifest_invalid")
    expected = {row.get("slot"): row for row in source_captures}
    if set(expected) != set(summary["selected_slots"]):
        raise ValueError("report_full_block_slots_do_not_match_frame")
    return source, frame, summary


def _identity(row: dict[str, Any]) -> tuple[int, str, tuple[int, ...]]:
    slot, signature, path = row.get("slot"), row.get("signature"), row.get("path")
    if (
        type(slot) is not int
        or not isinstance(signature, str)
        or not signature
        or not isinstance(path, list)
        or not path
        or any(type(index) is not int or index < 0 for index in path)
    ):
        raise ValueError("candidate_identity_invalid")
    return slot, signature, tuple(path)


def _reconcile_report_population(
    report: dict[str, Any],
    selected_report_rows: list[dict[str, Any]],
    blocks: dict[int, dict[str, Any]],
    capture_receipts: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    pins = report.get("pins")
    if not isinstance(pins, dict):
        raise ValueError("census_report_source_pins_missing")
    tool_sha = _sha256(REPORT_TOOL.read_bytes())
    implementation_sha = _sha256(REPORT_IMPLEMENTATION.read_bytes())
    if (
        pins.get("tool_sha256") != tool_sha
        or pins.get("implementation_sha256") != implementation_sha
        or pins.get("pump_amm_idl_sha256") != PINNED_LOCAL_IDL_SHA256
        or pins.get("discriminator_hex") != DISCRIMINATOR.hex()
    ):
        raise ValueError("census_report_source_pin_mismatch")

    raw_by_identity: dict[tuple[int, str, tuple[int, ...]], dict[str, Any]] = {}
    raw_signature_counts: Counter[tuple[int, str]] = Counter()
    transactions_normalized = 0
    for slot in sorted(blocks):
        block = blocks[slot]
        receipt = capture_receipts[slot]
        event_time = block.get("blockTime")
        availability_ns = receipt.get("received_ns")
        if type(availability_ns) is not int:
            raise ValueError("capture_receipt_availability_missing")
        for transaction_index, transaction in enumerate(block.get("transactions", [])):
            transactions_normalized += 1
            inspected = inspect_buy_exact_quote_in_v1(transaction)
            if inspected.get("status") == "malformed_or_missing_trace":
                raise ValueError(
                    f"raw_transaction_normalization_error:{slot}:{transaction_index}"
                )
            signatures = transaction.get("transaction", {}).get("signatures", [])
            if not isinstance(signatures, list) or not signatures or not isinstance(signatures[0], str):
                raise ValueError(f"raw_transaction_signature_missing:{slot}:{transaction_index}")
            signature = signatures[0]
            for reference in inspected.get("references", []):
                if not (
                    reference.get("argument_state") == "pinned_idl_v1_shape"
                    and reference.get("instruction_data_length") == 25
                    and reference.get("base_account_count") == 23
                    and reference.get("instruction_account_count") == 26
                    and reference.get("remaining_account_count") == 3
                ):
                    continue
                path = reference.get("path")
                identity = (slot, signature, tuple(path) if isinstance(path, list) else ())
                if identity in raw_by_identity:
                    raise ValueError("duplicate_or_conflicting_raw_candidate_identity")
                _target_roles, wire_hex, accounts = _roles_from_raw(transaction, path)
                instruction = _compiled_instruction(transaction, path)
                if (
                    len(accounts) != 26
                    or len(instruction.get("accounts", [])) != 26
                    or wire_hex != reference.get("instruction_data_hex")
                    or reference.get("stack_height") is None
                ):
                    raise ValueError("raw_candidate_wire_or_account_shape_mismatch")
                tx_status = "failed" if transaction.get("meta", {}).get("err") is not None else "success"
                if reference.get("transaction_status") != (
                    "failed_tx_attempt_only" if tx_status == "failed" else "successful_tx_instruction_reference"
                ):
                    raise ValueError("raw_candidate_status_inconsistent")
                full_idl_roles = dict(zip(ACCOUNT_ROLES, accounts[:23], strict=True))
                raw_report_roles = {
                    name: full_idl_roles[name] for name in REPORTED_ROLE_NAMES
                }
                identity_row = {
                    "slot": slot,
                    "transaction_index": transaction_index,
                    "signature": signature,
                    "path": path,
                    "instruction_data_hex": wire_hex,
                    "instruction_data_length": len(bytes.fromhex(wire_hex)),
                    "remaining_account_count": len(accounts) - 23,
                    "transaction_status": tx_status,
                    "stack_height": reference.get("stack_height"),
                    "identified_roles": raw_report_roles,
                    "remaining_accounts": accounts[23:],
                    "native_event_time_unix_seconds": event_time,
                    "availability_ns": availability_ns,
                }
                raw_by_identity[identity] = identity_row
                raw_signature_counts[(slot, signature)] += 1

    report_by_identity: dict[tuple[int, str, tuple[int, ...]], dict[str, Any]] = {}
    for row in selected_report_rows:
        identity = _identity(row)
        if identity in report_by_identity:
            raise ValueError("duplicate_or_conflicting_report_candidate_identity")
        report_by_identity[identity] = row

    raw_keys, report_keys = set(raw_by_identity), set(report_by_identity)
    if raw_keys != report_keys:
        raise ValueError(
            "raw_report_candidate_set_mismatch:"
            f"raw_only={len(raw_keys - report_keys)}:report_only={len(report_keys - raw_keys)}"
        )
    compared_fields = (
        "slot",
        "transaction_index",
        "signature",
        "path",
        "instruction_data_hex",
        "instruction_data_length",
        "remaining_account_count",
        "transaction_status",
        "stack_height",
        "identified_roles",
        "native_event_time_unix_seconds",
        "availability_ns",
    )
    for identity in sorted(raw_keys):
        raw, row = raw_by_identity[identity], report_by_identity[identity]
        for field in compared_fields:
            if row.get(field) != raw[field]:
                raise ValueError(f"raw_report_candidate_field_mismatch:{field}")
        accounts = raw["remaining_accounts"]
        if (
            (row.get("unresolved_predecessor_account") is not None
             and row.get("unresolved_predecessor_account") != accounts[0])
            or row.get("observed_fee_recipient") != accounts[1]
            or row.get("observed_fee_ata") != accounts[2]
        ):
            raise ValueError("raw_report_remaining_account_mismatch")
    return {
        "method": "independent_full_block_instruction_normalization_and_exact_identity_set_equality",
        "full_blocks_enumerated": len(blocks),
        "transactions_normalized": transactions_normalized,
        "raw_candidate_references": len(raw_by_identity),
        "report_candidate_references": len(report_by_identity),
        "raw_only_references": 0,
        "report_only_references": 0,
        "identity_key": ["slot", "signature", "instruction_path"],
        "candidate_set_equal": True,
        "normalization_errors": 0,
        "duplicate_or_conflicting_identities": 0,
        "census_tool_sha256": tool_sha,
        "census_implementation_sha256": implementation_sha,
        "candidate_references_per_signature_distribution": dict(
            Counter(raw_signature_counts.values())
        ),
    }


def qualify(
    report_path: Path,
    epoch_root: Path,
    frame_receipt_path: Path,
    sdk_root: Path,
    node_dependencies: Path,
    derive_script: Path,
) -> dict[str, Any]:
    sdk_pin = _verify_sdk(sdk_root, node_dependencies)
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    if not isinstance(report, dict):
        raise ValueError("report_json_object_required")
    source, frame, summary = _frame_binding(report, epoch_root, frame_receipt_path)
    rows = report.get("result", {}).get("references", report.get("rows"))
    if not isinstance(rows, list):
        raise ValueError("reference_rows_missing")
    selected = [
        row
        for row in rows
        if row.get("instruction_data_length") == 25
        and row.get("remaining_account_count") == 3
        and row.get("argument_state") == "pinned_idl_v1_shape"
    ]
    if not selected:
        raise ValueError("no_exact_25_byte_three_tail_rows")

    zstd = __import__("shutil").which("zstd")
    if zstd is None:
        raise ValueError("zstd_cli_unavailable")
    captures = _capture_rows(epoch_root, v3_layout=True)
    by_slot: dict[int, tuple[Path, dict[str, Any]]] = {}
    capture_hashes: dict[int, dict[str, Any]] = {}
    for capture_path, capture_receipt, request in captures:
        params = request.get("params")
        if not isinstance(params, list) or not params or type(params[0]) is not int:
            continue
        slot = params[0]
        if slot in by_slot:
            raise ValueError("duplicate_full_block_capture_slot")
        by_slot[slot] = (capture_path, capture_receipt)
        capture_hashes[slot] = {
            "slot": slot,
            "sha256": capture_receipt["storage"]["sha256"],
            "response_sha256": capture_receipt["response_sha256"],
        }
    if set(by_slot) != set(summary["selected_slots"]):
        raise ValueError("captured_full_block_slots_do_not_match_summary")
    source_captures = source.get("full_block_captures", source.get("captures", []))
    for slot, expected in ((row["slot"], row) for row in source_captures):
        receipt = by_slot[slot][1]
        expected_capture_sha = expected.get("capture_sha256", expected.get("compressed_sha256"))
        if (
            receipt["storage"]["sha256"] != expected_capture_sha
            or receipt["response_sha256"] != expected.get("response_sha256")
        ):
            raise ValueError("capture_hash_differs_from_source_report")

    blocks: dict[int, dict[str, Any]] = {
        slot: _read_block(zstd, capture_path, capture_receipt)
        for slot, (capture_path, capture_receipt) in by_slot.items()
    }
    population_binding = _reconcile_report_population(
        report,
        selected,
        blocks,
        {slot: value[1] for slot, value in by_slot.items()},
    )
    raw_rows: list[dict[str, Any]] = []
    derive_inputs: list[dict[str, Any]] = []
    for row in selected:
        slot = row.get("slot")
        tx_index = row.get("transaction_index")
        path = row.get("path")
        if type(slot) is not int or type(tx_index) is not int or not isinstance(path, list):
            raise ValueError("candidate_locator_invalid")
        txs = blocks[slot].get("transactions", [])
        if not 0 <= tx_index < len(txs):
            raise ValueError("candidate_transaction_index_missing")
        transaction = txs[tx_index]
        signatures = transaction.get("transaction", {}).get("signatures", [])
        if not signatures or signatures[0] != row.get("signature"):
            raise ValueError("candidate_signature_mismatch")
        raw_roles, wire_hex, accounts = _roles_from_raw(transaction, path)
        wire = bytes.fromhex(wire_hex)
        if (
            wire[:8] != DISCRIMINATOR
            or len(wire) != 25
            or wire[24] not in (0, 1)
            or len(ACCOUNT_ROLES) != 23
            or len(accounts) != 26
        ):
            raise ValueError("candidate_wire_or_account_count_mismatch")
        instruction = _compiled_instruction(transaction, path)
        indexes = instruction.get("accounts")
        if not isinstance(indexes, list) or len(indexes) != 26:
            raise ValueError("compiled_account_index_count_mismatch")
        transaction_status = "failed" if transaction.get("meta", {}).get("err") is not None else "success"
        if row.get("transaction_status") != transaction_status:
            raise ValueError("candidate_status_mismatch")
        identified_roles = row.get("identified_roles") or {}
        if raw_roles["base_mint"] != row.get("base_mint", identified_roles.get("base_mint")):
            raise ValueError("candidate_base_mint_role_mismatch")
        if raw_roles["quote_mint"] != row.get("quote_mint", identified_roles.get("quote_mint")):
            raise ValueError("candidate_quote_mint_role_mismatch")
        if raw_roles["quote_token_program"] != row.get("quote_token_program", identified_roles.get("quote_token_program")):
            raise ValueError("candidate_quote_program_role_mismatch")
        expected_tail = row.get("ordered_extra_accounts")
        if expected_tail is not None:
            if accounts[23:] != expected_tail:
                raise ValueError("candidate_remaining_accounts_mismatch")
        elif row.get("observed_fee_recipient") is not None or row.get("observed_fee_ata") is not None:
            if accounts[24:] != [row.get("observed_fee_recipient"), row.get("observed_fee_ata")]:
                raise ValueError("candidate_fee_pair_mismatch")
            if row.get("unresolved_predecessor_account") is not None and accounts[23] != row.get(
                "unresolved_predecessor_account"
            ):
                raise ValueError("candidate_unresolved_prefix_mismatch")
        else:
            raise ValueError("candidate_remaining_account_source_fields_missing")
            raise ValueError("candidate_remaining_accounts_mismatch")
        derive_inputs.append(
            {
                "slot": slot,
                "transaction_index": tx_index,
                "path": path,
                "base_mint": raw_roles["base_mint"],
                "user": accounts[1],
                "quote_mint": raw_roles["quote_mint"],
                "quote_token_program": raw_roles["quote_token_program"],
            }
        )
        raw_rows.append(
            {
                "slot": slot,
                "transaction_index": tx_index,
                "path": path,
                "native_event_time_unix_seconds": row.get("native_event_time_unix_seconds"),
                "availability_ns": row.get("availability_ns"),
                "raw_receipt_ns": by_slot[slot][1].get("receipt_ns"),
                "raw_capture_sha256": by_slot[slot][1]["storage"]["sha256"],
                "raw_response_sha256": by_slot[slot][1]["response_sha256"],
                "signature": signatures[0],
                "transaction_status": transaction_status,
                "instruction_data_sha256": _sha256(wire),
                "instruction_data_length": len(wire),
                "track_volume": wire[24],
                "raw_account_indexes": indexes,
                "fixed_idl_base_mint": raw_roles["base_mint"],
                "user": accounts[1],
                "quote_mint": raw_roles["quote_mint"],
                "quote_token_program": raw_roles["quote_token_program"],
                "raw_remaining_accounts": accounts[23:],
            }
        )

    env = dict(os.environ)
    env["NODE_PATH"] = str(node_dependencies.resolve())
    completed = subprocess.run(
        ["node", str(derive_script.resolve()), str(sdk_root.resolve())],
        input=json.dumps(derive_inputs, separators=(",", ":")),
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    derivations = json.loads(completed.stdout)
    if not isinstance(derivations, list) or len(derivations) != len(raw_rows):
        raise ValueError("official_sdk_derivation_output_mismatch")

    output_rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    by_status: dict[str, Counter[str]] = {"success": Counter(), "failed": Counter()}
    for raw, derived in zip(raw_rows, derivations, strict=True):
        if (
            derived.get("slot") != raw["slot"]
            or derived.get("transaction_index") != raw["transaction_index"]
            or derived.get("path") != raw["path"]
        ):
            raise ValueError("official_sdk_derivation_row_identity_mismatch")
        ix = _compiled_instruction(blocks[raw["slot"]]["transactions"][raw["transaction_index"]], raw["path"])
        account_indexes = ix["accounts"]
        transaction = blocks[raw["slot"]]["transactions"][raw["transaction_index"]]
        pool_writable, pool_signer = _transaction_privilege(transaction, account_indexes[23])
        fee_writable, fee_signer = _transaction_privilege(transaction, account_indexes[24])
        ata_writable, ata_signer = _transaction_privilege(transaction, account_indexes[25])
        fee_wallet = raw["raw_remaining_accounts"][1]
        fee_ata = raw["raw_remaining_accounts"][2]
        fee_wallet_known = fee_wallet in FEE_RECIPIENTS_UPGRADE_2026_04_28
        fee_ata_derived = associated_token_address(
            fee_wallet, raw["quote_mint"], raw["quote_token_program"]
        )
        checks = {
            "pool_v2_pda_matches_raw_prefix": raw["raw_remaining_accounts"][0] == derived["pool_v2"],
            "raw_prefix_not_cashback_quote_ata": raw["raw_remaining_accounts"][0] != derived["cashback_quote_ata"],
            "pool_v2_transaction_readonly": pool_writable is False,
            "pool_v2_nonsigner": pool_signer is False,
            "fee_wallet_in_pinned_set": fee_wallet_known,
            "fee_wallet_transaction_readonly": fee_writable is False,
            "fee_wallet_nonsigner": fee_signer is False,
            "fee_quote_ata_derives_from_roles": fee_ata == fee_ata_derived,
            "fee_quote_ata_transaction_writable": ata_writable is True,
            "fee_quote_ata_nonsigner": ata_signer is False,
        }
        failed_checks = [name for name, passed in checks.items() if not passed]
        state = "sdk_source_bound_pool_v2_pda_candidate" if not failed_checks else "quarantined_mismatch"
        counts[state] += 1
        by_status[raw["transaction_status"]][state] += 1
        output_rows.append(
            {
                **raw,
                "derived_pool_v2": derived["pool_v2"],
                "derived_cashback_quote_ata": derived["cashback_quote_ata"],
                "fee_wallet": fee_wallet,
                "fee_quote_ata": fee_ata,
                "transaction_privileges": {
                    "pool_v2": {"writable": pool_writable, "signer": pool_signer},
                    "fee_wallet": {"writable": fee_writable, "signer": fee_signer},
                    "fee_quote_ata": {"writable": ata_writable, "signer": ata_signer},
                },
                "checks": checks,
                "state": state,
                "failed_checks": failed_checks,
                "training_eligible": False,
                "economic_settlement_state": "not_assessed",
            }
        )
    status_counts = {
        status: {
            "references": sum(row["transaction_status"] == status for row in output_rows),
            "structural_passes": by_status[status]["sdk_source_bound_pool_v2_pda_candidate"],
            "quarantined": by_status[status]["quarantined_mismatch"],
        }
        for status in ("success", "failed")
    }
    return {
        "schema_version": 2,
        "contract": CONTRACT,
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "scope": {
            "frame_bucket": epoch_root.name,
            "selected_full_blocks": len(summary["selected_slots"]),
            "predicate": PREDICATE,
            "references": len(output_rows),
        },
        "source": {
            "report_sha256": _sha256(report_bytes),
            "frame_receipt_sha256": _sha256(frame_receipt_path.read_bytes()),
            "epoch_plan_sha256": _sha256((epoch_root / "epoch-plan.json").read_bytes()),
            "epoch_summary_sha256": _sha256((epoch_root / "epoch-summary.json").read_bytes()),
            "full_block_captures": [capture_hashes[slot] for slot in sorted(capture_hashes)],
            "sdk": sdk_pin,
            "fee_recipient_set_sha256": _sha256(
                json.dumps(sorted(FEE_RECIPIENTS_UPGRADE_2026_04_28), separators=(",", ":")).encode()
            ),
            "classifier_sha256": _sha256(Path(__file__).read_bytes()),
            "sdk_derivation_helper_sha256": _sha256(derive_script.read_bytes()),
            "supersedes_receipt_sha256": None,
        },
        "population_binding": population_binding,
        "result": {
            "state_counts": dict(counts),
            "status_counts": status_counts,
            "rows": output_rows,
        },
        "limitations": [
            "A matching pool-v2 PDA prefix is only an SDK-source-bound structural candidate; it does not prove initialized historical pool state or which builder produced the transaction.",
            "Successful transaction references are not independently reconciled economic fills or settled wallet outcomes.",
            "Failed transactions remain attempt-only, even when all structural account checks pass.",
            "Cashback applicability, fees moved, reserves, price, Token-2022 effects, CPI sibling effects, and training/execution admission are not assessed.",
            "This classifier is restricted to exact 25-byte valid-boolean buy_exact_quote_in with exactly three remaining accounts; all other shapes remain quarantined.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--epoch-root", required=True, type=Path)
    parser.add_argument("--frame-receipt", required=True, type=Path)
    parser.add_argument("--sdk-root", required=True, type=Path)
    parser.add_argument("--node-dependencies", required=True, type=Path)
    parser.add_argument("--derive-script", type=Path, default=REPO_ROOT / "tools/brain_senses_buy_exact_v1_pool_v2_pda_derive.cjs")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--supersedes-receipt", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("output_exists_refusing_overwrite")
    result = qualify(
        args.report.resolve(),
        args.epoch_root.resolve(),
        args.frame_receipt.resolve(),
        args.sdk_root.resolve(),
        args.node_dependencies.resolve(),
        args.derive_script.resolve(),
    )
    if args.supersedes_receipt is not None:
        result["source"]["supersedes_receipt_sha256"] = _sha256(
            args.supersedes_receipt.read_bytes()
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({"output": str(args.output), "result": result["result"]["state_counts"], "status_counts": result["result"]["status_counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
