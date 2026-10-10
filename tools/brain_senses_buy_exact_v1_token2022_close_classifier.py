#!/usr/bin/env python3
"""Offline source-bound structural classification of four Token-2022 close instructions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
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

CONTRACT = "pumpswap_v1_token2022_close_structural_v1"
CLEAN_CONTRACT = "pumpswap_v1_buy_exact_token2022_clean_stratum_trace_v1"
SOURCE_COMMIT = "b4724e6e7fecc8fc36295f861a769401bf92b270"
SOURCE_FILES = {
    "instruction.rs": "672b3890cb58e5700a67f202491069ed44dffcbfa0e7d0b83a0cfbbb05b3a287",
    "processor.rs": "57e451a47aba32e0eac5ab079696b5ff6ac96532b19de5cec4d6bc610fe4ef04",
}
SOURCE_URLS = {
    "instruction.rs": (
        "https://raw.githubusercontent.com/solana-program/token-2022/"
        f"{SOURCE_COMMIT}/interface/src/instruction.rs"
    ),
    "processor.rs": (
        "https://raw.githubusercontent.com/solana-program/token-2022/"
        f"{SOURCE_COMMIT}/program/src/processor.rs"
    ),
}
EXPECTED_ROWS = 4


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected_json_object:{path}")
    return value


def verify_official_source(source_root: Path) -> dict[str, Any]:
    actual = {}
    reasons = []
    for filename, expected in SOURCE_FILES.items():
        path = source_root / filename
        actual_hash = _sha256(path.read_bytes()) if path.is_file() else None
        actual[filename] = {
            "url": SOURCE_URLS[filename],
            "expected_sha256": expected,
            "actual_sha256": actual_hash,
        }
        if actual_hash != expected:
            reasons.append(f"official_source_hash_mismatch:{filename}")
    if source_root.name != SOURCE_COMMIT:
        reasons.append("official_source_commit_directory_mismatch")
    return {
        "commit": SOURCE_COMMIT,
        "source_root": str(source_root.resolve()),
        "files": actual,
        "valid": not reasons,
        "failure_reasons": reasons,
    }


def _resolve_instruction(
    transaction: dict[str, Any], instruction: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]]:
    tx = transaction.get("transaction")
    message = tx.get("message") if isinstance(tx, dict) else None
    meta = transaction.get("meta")
    if not isinstance(message, dict) or not isinstance(meta, dict):
        raise ValueError("transaction_message_or_meta_missing")
    raw_keys = message.get("accountKeys")
    header = message.get("header")
    if not isinstance(raw_keys, list) or not raw_keys or not isinstance(header, dict):
        raise ValueError("message_keys_or_header_missing")
    static_keys = [item.get("pubkey") if isinstance(item, dict) else item for item in raw_keys]
    if any(not isinstance(key, str) or not key for key in static_keys):
        raise ValueError("static_account_key_invalid")
    required = header.get("numRequiredSignatures")
    readonly_signed = header.get("numReadonlySignedAccounts")
    readonly_unsigned = header.get("numReadonlyUnsignedAccounts")
    if any(
        type(value) is not int or value < 0
        for value in (required, readonly_signed, readonly_unsigned)
    ):
        raise ValueError("message_header_privileges_invalid")
    if (
        required > len(static_keys)
        or readonly_signed > required
        or readonly_unsigned > len(static_keys) - required
    ):
        raise ValueError("message_header_privileges_out_of_range")

    lookups = message.get("addressTableLookups", [])
    loaded = meta.get("loadedAddresses")
    if loaded is None:
        if lookups:
            raise ValueError("loaded_addresses_missing")
        loaded = {"writable": [], "readonly": []}
    if (
        not isinstance(loaded, dict)
        or not isinstance(loaded.get("writable"), list)
        or not isinstance(loaded.get("readonly"), list)
    ):
        raise ValueError("loaded_addresses_shape_invalid")
    loaded_writable = loaded["writable"]
    loaded_readonly = loaded["readonly"]
    if any(not isinstance(key, str) or not key for key in loaded_writable + loaded_readonly):
        raise ValueError("loaded_account_key_invalid")
    expected_writable = expected_readonly = 0
    if not isinstance(lookups, list):
        raise ValueError("address_table_lookups_invalid")
    for lookup in lookups:
        if not isinstance(lookup, dict):
            raise ValueError("address_table_lookup_invalid")
        for field in ("writableIndexes", "readonlyIndexes"):
            indexes = lookup.get(field)
            if not isinstance(indexes, list) or any(
                type(index) is not int or not 0 <= index <= 255 for index in indexes
            ):
                raise ValueError("address_table_indexes_invalid")
            if field == "writableIndexes":
                expected_writable += len(indexes)
            else:
                expected_readonly += len(indexes)
    if len(loaded_writable) != expected_writable or len(loaded_readonly) != expected_readonly:
        raise ValueError("loaded_address_count_mismatch")

    keys = static_keys + loaded_writable + loaded_readonly
    if len(keys) != len(set(keys)):
        raise ValueError("resolved_account_key_conflict")
    writable = [
        index < required - readonly_signed
        or required <= index < len(static_keys) - readonly_unsigned
        for index in range(len(static_keys))
    ]
    writable.extend([True] * len(loaded_writable))
    writable.extend([False] * len(loaded_readonly))
    signer = [index < required for index in range(len(static_keys))]
    signer.extend([False] * (len(loaded_writable) + len(loaded_readonly)))

    for index, raw_key in enumerate(raw_keys):
        if isinstance(raw_key, dict):
            if "writable" in raw_key and raw_key["writable"] is not writable[index]:
                raise ValueError("account_key_writable_flag_disagreement")
            if "signer" in raw_key and raw_key["signer"] is not signer[index]:
                raise ValueError("account_key_signer_flag_disagreement")

    program_index = instruction.get("programIdIndex")
    account_indexes = instruction.get("accounts")
    if type(program_index) is not int or not 0 <= program_index < len(keys):
        raise ValueError("instruction_program_index_invalid")
    if not isinstance(account_indexes, list) or any(
        type(index) is not int or not 0 <= index < len(keys) for index in account_indexes
    ):
        raise ValueError("instruction_account_indexes_invalid")
    ordered_accounts = [
        {
            "pubkey": keys[index],
            "writable_potential": writable[index],
            "signer_potential": signer[index],
            "account_index": index,
        }
        for index in account_indexes
    ]
    return keys[program_index], ordered_accounts


def classify_reference(
    row: dict[str, Any],
    transaction: dict[str, Any],
    *,
    source_valid: bool,
    capture_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trace = row.get("target_instruction_trace")
    target_ixs = (
        [
            item
            for item in trace
            if isinstance(item, dict)
            and item.get("program_id") == TOKEN2022
            and item.get("wire_hex") == "09"
        ]
        if isinstance(trace, list)
        else []
    )
    checks: dict[str, bool] = {"pinned_official_source_hashes": source_valid}
    reasons: list[str] = []
    target = target_ixs[0] if len(target_ixs) == 1 else {}
    if len(target_ixs) != 1:
        checks["unique_target_wire_trace"] = False
        reasons.append("target_wire_trace_missing_or_ambiguous")
    else:
        checks["unique_target_wire_trace"] = True

    path = target.get("path")
    stack_height = target.get("stack_height")
    instructions = (
        transaction.get("transaction", {}).get("message", {}).get("instructions")
        if isinstance(transaction.get("transaction"), dict)
        else None
    )
    raw_ix = None
    if (
        isinstance(path, list)
        and len(path) == 1
        and type(path[0]) is int
        and isinstance(instructions, list)
        and 0 <= path[0] < len(instructions)
    ):
        raw_ix = instructions[path[0]]
    checks["outer_instruction_path_and_stack"] = bool(
        raw_ix is not None
        and isinstance(raw_ix, dict)
        and raw_ix.get("stackHeight") == stack_height == 1
    )
    if not checks["outer_instruction_path_and_stack"]:
        reasons.append("instruction_path_or_stack_mismatch")

    tx_inner = transaction.get("transaction")
    tx_signatures = tx_inner.get("signatures") if isinstance(tx_inner, dict) else None
    checks["transaction_signature_matches"] = bool(
        isinstance(tx_signatures, list)
        and tx_signatures
        and tx_signatures[0] == row.get("signature")
    )
    if not checks["transaction_signature_matches"]:
        reasons.append("transaction_signature_mismatch")
    meta = transaction.get("meta")
    checks["transaction_succeeded"] = bool(
        isinstance(meta, dict) and "err" in meta and meta["err"] is None
    )
    if not checks["transaction_succeeded"]:
        reasons.append("transaction_status_not_success")

    decoded_data = None
    program_id = None
    ordered_accounts: list[dict[str, Any]] = []
    if raw_ix is not None:
        try:
            program_id, ordered_accounts = _resolve_instruction(transaction, raw_ix)
        except (TypeError, ValueError) as exc:
            reasons.append(str(exc))
    checks["exact_token2022_program"] = (
        program_id == TOKEN2022 and target.get("program_id") == TOKEN2022
    )
    if not checks["exact_token2022_program"]:
        reasons.append("target_program_id_mismatch")
    if raw_ix is not None and isinstance(raw_ix, dict):
        try:
            decoded_data = decode58(raw_ix.get("data"))
        except (TypeError, ValueError):
            decoded_data = None
    checks["exact_close_account_wire"] = bool(
        decoded_data == b"\x09"
        and target.get("wire_hex") == "09"
        and target.get("wire_sha256") == hashlib.sha256(b"\x09").hexdigest()
    )
    if not checks["exact_close_account_wire"]:
        reasons.append("wire_payload_not_exact_close_account_tag")

    roles = row.get("target_accounts")
    role_specs = (
        [
            ("close_target_token_account", roles.get("user_quote_token_account")),
            ("destination_account", roles.get("user")),
            ("single_owner_signer", roles.get("user")),
        ]
        if isinstance(roles, dict)
        else []
    )
    expected_order = [pubkey for _role, pubkey in role_specs]
    actual_order = [account["pubkey"] for account in ordered_accounts]
    ordered_role_match = (
        len(ordered_accounts) == 3 and len(expected_order) == 3 and actual_order == expected_order
    )
    account_privileges_match = bool(
        ordered_role_match
        and ordered_accounts[0]["writable_potential"]
        and not ordered_accounts[0]["signer_potential"]
        and ordered_accounts[1]["writable_potential"]
        and ordered_accounts[2]["signer_potential"]
    )
    checks["resolved_close_account_order_and_privileges"] = account_privileges_match
    if not account_privileges_match:
        reasons.append("resolved_close_account_roles_or_privileges_mismatch")
    reported_accounts = [
        {**account, "expected_role": role_specs[index][0]}
        for index, account in enumerate(ordered_accounts)
        if index < len(role_specs)
    ]

    classification = "known_structural_unsupported_close" if all(checks.values()) else "unknown"
    return {
        "signature": row.get("signature"),
        "slot": row.get("slot"),
        "transaction_index": row.get("transaction_index"),
        "classification": classification,
        "instruction_name": "CloseAccount" if classification.startswith("known_") else None,
        "program_id": program_id,
        "wire_hex": decoded_data.hex() if decoded_data is not None else None,
        "wire_sha256": hashlib.sha256(decoded_data).hexdigest()
        if decoded_data is not None
        else None,
        "instruction_path": path,
        "stack_height": stack_height,
        "transaction_succeeded": checks["transaction_succeeded"],
        "resolved_ordered_accounts": reported_accounts,
        "checks": checks,
        "failure_reasons": sorted(set(reasons)),
        "capture_binding": capture_binding,
        "historical_extension_state": "unavailable",
        "economic_effects": "not_assessed",
        "rent_or_lamport_settlement": "not_assessed",
    }


def _candidate_rows(clean: dict[str, Any]) -> list[dict[str, Any]]:
    if clean.get("contract") != CLEAN_CONTRACT or clean.get("schema_version") != "1":
        raise ValueError("clean_stratum_receipt_contract_mismatch")
    rows = clean.get("rows")
    if not isinstance(rows, list) or len(rows) != 1146:
        raise ValueError("clean_stratum_denominator_mismatch")
    selected = []
    for row in rows:
        traces = row.get("target_instruction_trace") if isinstance(row, dict) else None
        if not isinstance(traces, list):
            raise ValueError("clean_stratum_trace_missing")
        for trace in traces:
            if (
                isinstance(trace, dict)
                and trace.get("program_id") == TOKEN2022
                and trace.get("wire_hex") == "09"
            ):
                selected.append(row)
                break
    if len(selected) != EXPECTED_ROWS:
        raise ValueError(f"unexpected_close_shape_denominator:{len(selected)}")
    return selected


def _full_capture_by_slot(
    capture_root: Path, wanted_slots: set[int]
) -> dict[int, tuple[dict[str, Any], dict[str, Any]]]:
    results = {}
    for result_path in (capture_root / "request-results").glob("*.json"):
        result = _json(result_path)
        if (
            result.get("provider") != "helius-free"
            or result.get("method") != "getBlock"
            or result.get("detail") != "full"
        ):
            continue
        slot = result.get("slot")
        if type(slot) is not int or slot not in wanted_slots:
            continue
        if slot in results:
            raise ValueError(f"duplicate_full_capture_slot:{slot}")
        capture_dir = (capture_root / result.get("capture_path", "")).resolve()
        if not capture_dir.is_relative_to(capture_root.resolve()):
            raise ValueError("capture_path_outside_capture_root")
        receipt_path = capture_dir / "receipt.json"
        if result.get("status") != "captured" or result.get("result_status") != "parsed":
            continue
        capture_receipt = _json(receipt_path)
        request = json.loads(capture_receipt.get("request_json", "{}"))
        params = request.get("params")
        sequence = result.get("sequence")
        if type(sequence) is not int or sequence < 0:
            raise ValueError(f"full_capture_request_sequence_invalid:{slot}")
        reservation_path = capture_root / "requests" / f"{sequence:012d}.json"
        reservation = _json(reservation_path) if reservation_path.is_file() else {}
        request_body = canonical(request).encode()
        params_hash = _sha256(canonical(params).encode()) if isinstance(params, list) else None
        if (
            capture_receipt.get("source_alias") != "helius-free"
            or request.get("method") != "getBlock"
            or not isinstance(params, list)
            or len(params) != 2
            or params[0] != slot
            or not isinstance(params[1], dict)
            or params[1].get("commitment") != "finalized"
            or params[1].get("transactionDetails") != "full"
            or reservation.get("sequence") != result.get("sequence")
            or reservation.get("provider") != "helius-free"
            or reservation.get("method") != "getBlock"
            or reservation.get("slot") != slot
            or reservation.get("detail") != "full"
            or reservation.get("request_reservation") != 1
            or reservation.get("params_sha256") != params_hash
            or reservation.get("request_sha256") != _sha256(request_body)
        ):
            raise ValueError(f"full_capture_request_binding_invalid:{slot}")
        if (
            result.get("response_sha256") != capture_receipt.get("response_sha256")
            or result.get("response_bytes") != capture_receipt.get("response_bytes")
            or result.get("stored_response_bytes")
            != capture_receipt.get("storage", {}).get("bytes")
            or result.get("receipt_ns") != capture_receipt.get("received_ns")
        ):
            raise ValueError(f"full_capture_response_hash_binding_invalid:{slot}")
        capture_file = (capture_dir / capture_receipt["storage"]["path"]).resolve()
        if not capture_file.is_relative_to(capture_root.resolve()):
            raise ValueError("capture_storage_path_outside_capture_root")
        block = _read_block("zstd", capture_file, capture_receipt)
        transactions = block.get("transactions")
        if (
            block.get("blockhash") != result.get("blockhash")
            or block.get("parentSlot") != result.get("parent_slot")
            or block.get("blockTime") != result.get("block_time_seconds")
            or not isinstance(transactions, list)
            or len(transactions) != result.get("transaction_count")
        ):
            raise ValueError(f"full_capture_block_identity_mismatch:{slot}")
        results[slot] = (
            block,
            {
                "sequence": result.get("sequence"),
                "request_sha256": reservation["request_sha256"],
                "params_sha256": reservation["params_sha256"],
                "request_reserved_ns": reservation.get("reserved_ns"),
                "request_started_ns": capture_receipt.get("request_started_ns"),
                "receipt_ns": capture_receipt.get("received_ns"),
                "response_sha256": result["response_sha256"],
                "response_bytes": result.get("response_bytes"),
                "capture_receipt_sha256": _sha256(receipt_path.read_bytes()),
                "capture_storage_sha256": capture_receipt["storage"]["sha256"],
                "blockhash": result.get("blockhash"),
                "parent_slot": result.get("parent_slot"),
                "block_time_seconds": result.get("block_time_seconds"),
                "transaction_count": result.get("transaction_count"),
            },
        )
    return results


def derive_receipt(
    clean_receipt_path: Path,
    capture_root: Path,
    source_root: Path,
    output_path: Path,
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
    if str(capture_root) != str(Path(source.get("capture_root", "")).resolve()):
        raise ValueError("capture_root_binding_mismatch")
    plan_bytes = (capture_root / "plan.json").read_bytes()
    plan_hash = _sha256(plan_bytes)
    if plan_hash != source.get("capture_plan_sha256"):
        raise ValueError("capture_plan_hash_mismatch")
    source_manifest = verify_official_source(source_root.resolve())
    selected_rows = _candidate_rows(clean)
    captures = _full_capture_by_slot(capture_root, {row["slot"] for row in selected_rows})
    results = []
    for row in selected_rows:
        raw = captures.get(row.get("slot"))
        if raw is None:
            result = classify_reference(row, {}, source_valid=source_manifest["valid"])
            result["failure_reasons"] = sorted(
                set(result["failure_reasons"] + ["bound_full_capture_missing"])
            )
            result["classification"] = "unknown"
            results.append(result)
            continue
        block, binding = raw
        transactions = block.get("transactions")
        index = row.get("transaction_index")
        if (
            not isinstance(transactions, list)
            or type(index) is not int
            or not 0 <= index < len(transactions)
        ):
            tx = {}
            binding["failure_reason"] = "transaction_index_out_of_range"
        else:
            tx = transactions[index]
            tx_sigs = tx.get("transaction", {}).get("signatures", [])
            if not tx_sigs or tx_sigs[0] != row.get("signature"):
                binding["failure_reason"] = "full_capture_signature_location_mismatch"
        result = classify_reference(
            row, tx, source_valid=source_manifest["valid"], capture_binding=binding
        )
        if binding.get("failure_reason"):
            result["classification"] = "unknown"
            result["failure_reasons"] = sorted(
                set(result["failure_reasons"] + [binding["failure_reason"]])
            )
        results.append(result)

    counts = {}
    for row in results:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    result_receipt = {
        "schema_version": "1",
        "contract": CONTRACT,
        "claim": (
            "offline source-bound structural identification of four captured Token-2022 close "
            "instructions; no lifecycle, account-state, amount, rent/lamport settlement, PumpSwap "
            "economic, market-completeness, or admission claim"
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
        "code_commit": os.environ.get("BRAIN_SENSES_CODE_COMMIT")
        or subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip(),
        "tool_sha256": _sha256(Path(__file__).read_bytes()),
        "source": {
            "clean_stratum_receipt": str(clean_receipt_path.resolve()),
            "clean_stratum_receipt_sha256": _sha256(clean_bytes),
            "capture_root": str(capture_root),
            "capture_plan_sha256": plan_hash,
            "official_token2022": source_manifest,
            "program_id": TOKEN2022,
            "network_requests": 0,
        },
        "scope": {
            "input_clean_reference_denominator": 1146,
            "selected_close_wire_shape_denominator": EXPECTED_ROWS,
            "selection_predicate": {
                "program_id": TOKEN2022,
                "wire_hex": "09",
                "wire_length": 1,
            },
        },
        "pinned_interpretation": {
            "instruction_name": "CloseAccount",
            "instruction_byte": 9,
            "account_order": [
                "writable close target",
                "writable destination",
                "single-owner signer",
            ],
            "source_commit": SOURCE_COMMIT,
            "source_hashes": SOURCE_FILES,
        },
        "summary": {
            "references": len(results),
            "classifications": dict(sorted(counts.items())),
            "historical_extension_state_unavailable": len(results),
            "economic_effects_not_assessed": len(results),
            "rent_or_lamport_settlement_not_assessed": len(results),
        },
        "rows": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result_receipt, sort_keys=True, separators=(",", ":")) + "\n")
    return result_receipt


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
        "--official-source-root",
        type=Path,
        default=REPO_ROOT / "work/official-sources/solana-token-2022" / SOURCE_COMMIT,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT
        / "docs/evidence/pumpswap-buy-exact-v1-token2022-close-structural-2026-10-10.json",
    )
    args = parser.parse_args(argv)
    result = derive_receipt(
        args.clean_receipt, args.capture_root, args.official_source_root, args.output
    )
    print(
        json.dumps(
            {"contract": CONTRACT, "output": str(args.output), **result["summary"]}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
