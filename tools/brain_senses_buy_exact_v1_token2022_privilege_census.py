#!/usr/bin/env python3
"""Offline Token-2022 target-account privilege census; no economics are inferred."""

from __future__ import annotations

import argparse
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

from memecoin_pumpswap.compiled_economics import _flatten, _transaction_tree  # noqa: E402
from memecoin_pumpswap.decoder import PROGRAM, TOKEN, TOKEN2022  # noqa: E402
from memecoin_pumpswap.legacy_token_ledger import SYSTEM_PROGRAM  # noqa: E402
from memecoin_pumpswap.normalize import normalize_compiled_transaction  # noqa: E402
from tools.brain_senses_buy_exact_v1_census import _capture_rows, _read_block, _sha256  # noqa: E402

CONTRACT = "pumpswap_v1_buy_exact_token2022_privilege_census_v1"
EVIDENCE_CONTRACT = "pumpswap_v1_buy_exact_token2022_evidence_census_v1"
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
MEMO_PROGRAM = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
KNOWN_TARGET_PROGRAMS = {
    TOKEN,
    TOKEN2022,
    SYSTEM_PROGRAM,
    PROGRAM,
    ASSOCIATED_TOKEN_PROGRAM,
    COMPUTE_BUDGET_PROGRAM,
    MEMO_PROGRAM,
}


def account_writable_flags(tx: dict[str, Any], normalized: dict[str, Any]) -> dict[str, bool]:
    """Resolve transaction-wide privileges using message header and loaded-address order."""
    message = tx.get("transaction", {}).get("message", {})
    raw_keys = message.get("accountKeys")
    header = message.get("header")
    if not isinstance(raw_keys, list) or not isinstance(header, dict):
        raise ValueError("message_account_keys_or_header_missing")
    required = header.get("numRequiredSignatures")
    readonly_signed = header.get("numReadonlySignedAccounts")
    readonly_unsigned = header.get("numReadonlyUnsignedAccounts")
    if any(
        type(value) is not int or value < 0
        for value in (required, readonly_signed, readonly_unsigned)
    ):
        raise ValueError("message_header_privilege_counts_invalid")
    accounts = normalized.get("accounts")
    if not isinstance(accounts, list) or len(accounts) < len(raw_keys):
        raise ValueError("normalized_account_vector_invalid")
    static_count = len(raw_keys)
    if (
        required > static_count
        or readonly_signed > required
        or readonly_unsigned > static_count - required
    ):
        raise ValueError("message_header_privilege_counts_out_of_range")
    flags: dict[int, bool] = {}
    for index in range(static_count):
        key = raw_keys[index]
        key = key.get("pubkey") if isinstance(key, dict) else key
        if (
            not isinstance(key, str)
            or accounts[index].get("pubkey") != key
            or accounts[index].get("index") != index
        ):
            raise ValueError("static_account_index_resolution_mismatch")
        writable = (
            index < required - readonly_signed
            or required <= index < static_count - readonly_unsigned
        )
        flags[index] = writable
    loaded_writable = [
        row for row in accounts[static_count:] if row.get("source") == "loaded_writable"
    ]
    loaded_readonly = [
        row for row in accounts[static_count:] if row.get("source") == "loaded_readonly"
    ]
    if len(loaded_writable) + len(loaded_readonly) != len(accounts) - static_count:
        raise ValueError("loaded_account_privilege_provenance_unknown")
    expected_order = loaded_writable + loaded_readonly
    if expected_order != accounts[static_count:]:
        raise ValueError("loaded_account_privilege_order_invalid")
    for account in loaded_writable:
        flags[account["index"]] = True
    for account in loaded_readonly:
        flags[account["index"]] = False
    if len(flags) != len(accounts):
        raise ValueError("account_privilege_denominator_mismatch")
    return {account["pubkey"]: flags[account["index"]] for account in accounts}


def classify_transaction(tx: dict[str, Any], evidence_row: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_compiled_transaction(tx)
    if normalized["meta_error"] is not None:
        raise ValueError("token2022_candidate_not_successful")
    privileges = account_writable_flags(tx, normalized)
    all_keys = [account["pubkey"] for account in normalized["accounts"]]
    all_key_counts = Counter(all_keys)
    duplicate_keys = sorted(key for key, count in all_key_counts.items() if count > 1)
    role_accounts = evidence_row.get("target_accounts")
    if not isinstance(role_accounts, dict) or not role_accounts:
        raise ValueError("candidate_target_account_roles_missing")
    if any(not isinstance(key, str) or key not in privileges for key in role_accounts.values()):
        raise ValueError("candidate_target_account_key_unresolved")
    if duplicate_keys and set(duplicate_keys).intersection(role_accounts.values()):
        raise ValueError("candidate_target_account_key_conflict")
    quote_mint = role_accounts.get("quote_mint")
    if quote_mint is None:
        raise ValueError("candidate_quote_mint_missing")
    mint_state = (
        "mint_writable_potential" if privileges[quote_mint] else "mint_readonly_full_transaction"
    )

    tree = _transaction_tree(normalized)
    nodes = [node for root in tree for node in _flatten(root)]
    target_set = set(role_accounts.values())
    target_instruction_rows = []
    unknown_programs = set()
    for node in nodes:
        ix = node["instruction"]
        touched = sorted(target_set.intersection(ix["accounts"]))
        if not touched:
            continue
        wire = bytes.fromhex(ix["data_hex"])
        if ix["program_id"] not in KNOWN_TARGET_PROGRAMS:
            unknown_programs.add(ix["program_id"])
        target_instruction_rows.append(
            {
                "path": ix["path"],
                "stack_height": ix["stack_height"],
                "program_id": ix["program_id"],
                "touched_target_roles": sorted(
                    role for role, key in role_accounts.items() if key in touched
                ),
                "data_length": len(wire),
                "wire_first_byte": wire[0] if wire else None,
                "transaction_writable_touches": sorted(key for key in touched if privileges[key]),
            }
        )
    privilege_classes = Counter(
        "writable" if privileges[key] else "readonly" for key in set(role_accounts.values())
    )
    return {
        "signature": evidence_row["signature"],
        "slot": evidence_row["slot"],
        "transaction_index": evidence_row["transaction_index"],
        "instruction_path": evidence_row["instruction_path"],
        "target_roles": role_accounts,
        "quote_mint_transaction_privilege": mint_state,
        "quote_mint_writable_is_only_potential_write": privileges[quote_mint],
        "target_role_privilege_counts": dict(sorted(privilege_classes.items())),
        "writable_target_roles": sorted(
            role for role, key in role_accounts.items() if privileges[key]
        ),
        "readonly_target_roles": sorted(
            role for role, key in role_accounts.items() if not privileges[key]
        ),
        "target_affecting_instruction_trace": target_instruction_rows,
        "unknown_target_affecting_programs": sorted(unknown_programs),
        "historical_extension_state": "unavailable",
        "economic_effects": "not_assessed",
    }


def raw_key_privilege_in_transaction(tx: dict[str, Any], target_key: str) -> str:
    """Return a key's message privilege without needing unrelated inner trace parsing."""
    message = tx.get("transaction", {}).get("message", {})
    meta = tx.get("meta", {})
    raw_static = message.get("accountKeys")
    if not isinstance(raw_static, list):
        raise ValueError("slot_scan_static_account_keys_missing")
    static = [item.get("pubkey") if isinstance(item, dict) else item for item in raw_static]
    if any(not isinstance(key, str) for key in static):
        raise ValueError("slot_scan_static_account_key_invalid")
    lookups = message.get("addressTableLookups", [])
    if not isinstance(lookups, list):
        raise ValueError("slot_scan_address_table_lookups_shape")
    loaded = meta.get("loadedAddresses")
    if loaded is None:
        if lookups:
            raise ValueError("slot_scan_loaded_addresses_missing")
        loaded = {"writable": [], "readonly": []}
    if (
        not isinstance(loaded, dict)
        or not isinstance(loaded.get("writable"), list)
        or not isinstance(loaded.get("readonly"), list)
    ):
        raise ValueError("slot_scan_loaded_addresses_shape")
    writable_loaded, readonly_loaded = loaded["writable"], loaded["readonly"]
    if any(not isinstance(key, str) for key in writable_loaded + readonly_loaded):
        raise ValueError("slot_scan_loaded_account_key_invalid")
    expected_writable = 0
    expected_readonly = 0
    for lookup in lookups:
        if not isinstance(lookup, dict):
            raise ValueError("slot_scan_address_table_lookup_shape")
        for field in ("writableIndexes", "readonlyIndexes"):
            indices = lookup.get(field)
            if not isinstance(indices, list) or any(
                type(index) is not int or not 0 <= index <= 255 for index in indices
            ):
                raise ValueError("slot_scan_address_table_lookup_indices_invalid")
            if field == "writableIndexes":
                expected_writable += len(indices)
            else:
                expected_readonly += len(indices)
    if len(writable_loaded) != expected_writable or len(readonly_loaded) != expected_readonly:
        raise ValueError("slot_scan_loaded_address_count_mismatch")
    positions = [index for index, key in enumerate(static) if key == target_key]
    writable_positions = [index for index, key in enumerate(writable_loaded) if key == target_key]
    readonly_positions = [index for index, key in enumerate(readonly_loaded) if key == target_key]
    occurrence_count = len(positions) + len(writable_positions) + len(readonly_positions)
    if occurrence_count > 1:
        return "account_key_conflict"
    if occurrence_count == 0:
        return "absent"
    if writable_positions:
        return "writable"
    if readonly_positions:
        return "readonly"
    header = message.get("header")
    if not isinstance(header, dict):
        raise ValueError("slot_scan_message_header_missing")
    required = header.get("numRequiredSignatures")
    readonly_signed = header.get("numReadonlySignedAccounts")
    readonly_unsigned = header.get("numReadonlyUnsignedAccounts")
    if any(
        type(value) is not int or value < 0
        for value in (required, readonly_signed, readonly_unsigned)
    ):
        raise ValueError("slot_scan_message_header_invalid")
    if (
        required > len(static)
        or readonly_signed > required
        or readonly_unsigned > len(static) - required
    ):
        raise ValueError("slot_scan_message_header_out_of_range")
    index = positions[0]
    writable = (
        index < required - readonly_signed or required <= index < len(static) - readonly_unsigned
    )
    if isinstance(raw_static[index], dict) and "writable" in raw_static[index]:
        if raw_static[index]["writable"] is not writable:
            raise ValueError("slot_scan_static_privilege_disagrees_with_header")
    return "writable" if writable else "readonly"


def census(
    capture_root: Path, evidence_path: Path, structural_path: Path, output: Path
) -> dict[str, Any]:
    capture_root, evidence_path, structural_path, output = (
        capture_root.resolve(),
        evidence_path.resolve(),
        structural_path.resolve(),
        output.resolve(),
    )
    if output.exists():
        raise ValueError("output_exists_refusing_overwrite")
    evidence_bytes = evidence_path.read_bytes()
    decompressed = subprocess.run(
        ["zstd", "-dc", str(evidence_path)], check=True, capture_output=True
    ).stdout
    evidence = json.loads(decompressed)
    structural_bytes = structural_path.read_bytes()
    structural = json.loads(structural_bytes)
    if (
        evidence.get("contract") != EVIDENCE_CONTRACT
        or structural.get("contract")
        != "pumpswap_v1_buy_exact_quote_in_attempt_and_suffix_candidate_v1"
    ):
        raise ValueError("unexpected_source_contract")
    if evidence.get("source", {}).get("capture_root") != str(capture_root):
        raise ValueError("evidence_capture_root_mismatch")
    if evidence.get("source", {}).get("structural_census_sha256") != _sha256(structural_bytes):
        raise ValueError("structural_census_hash_mismatch")
    if structural.get("source", {}).get("plan_sha256") != evidence.get("source", {}).get(
        "capture_plan_sha256"
    ):
        raise ValueError("capture_plan_hash_mismatch")
    binding_path = Path(evidence.get("source", {}).get("binding_receipt", "")).resolve()
    binding_bytes = binding_path.read_bytes()
    if _sha256(binding_bytes) != evidence.get("source", {}).get("binding_receipt_sha256"):
        raise ValueError("provider_binding_receipt_hash_mismatch")
    provider_binding = json.loads(binding_bytes)
    if provider_binding.get("bound_plan_sha256") != structural.get("source", {}).get("plan_sha256"):
        raise ValueError("provider_binding_receipt_plan_mismatch")
    expected_manifest = {item["slot"]: item for item in structural["source"]["captures"]}
    rows = evidence.get("rows")
    expected_denominator = evidence.get("scope", {}).get("structural_source_candidate_denominator")
    if (
        not isinstance(rows, list)
        or len(rows) != expected_denominator
        or expected_denominator != 1234
    ):
        raise ValueError("evidence_candidate_denominator_invalid")
    wanted: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
    mints_by_slot: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        key = (row["signature"], tuple(row["instruction_path"]))
        if key in wanted:
            raise ValueError("duplicate_evidence_signature_path")
        wanted[key] = row
        quote_mint = row.get("target_accounts", {}).get("quote_mint")
        if not isinstance(quote_mint, str):
            raise ValueError("candidate_quote_mint_missing")
        mints_by_slot[row["slot"]].add(quote_mint)
    by_signature: dict[str, list[tuple[str, tuple[int, ...]]]] = {}
    for key in wanted:
        by_signature.setdefault(key[0], []).append(key)
    captures = _capture_rows(capture_root)
    selected_slots = {row["slot"] for row in rows}
    captures = [
        capture
        for capture in captures
        if json.loads(capture[1]["request_json"])["params"][0] in selected_slots
    ]
    found: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
    slot_mint_privileges: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    slot_transaction_counts: Counter[int] = Counter()
    zstd = subprocess.run(
        ["which", "zstd"], check=True, capture_output=True, text=True
    ).stdout.strip()
    for path, receipt, _request in captures:
        slot = json.loads(receipt["request_json"])["params"][0]
        binding = expected_manifest.get(slot)
        if (
            not binding
            or binding.get("response_sha256") != receipt["response_sha256"]
            or binding.get("compressed_sha256") != receipt["storage"]["sha256"]
        ):
            raise ValueError("selected_capture_not_bound_by_structural_census")
        block = _read_block(zstd, path, receipt)
        for tx_index, tx in enumerate(block["transactions"]):
            slot_transaction_counts[slot] += 1
            for mint in mints_by_slot[slot]:
                state = raw_key_privilege_in_transaction(tx, mint)
                slot_mint_privileges[(slot, mint)][state] += 1
            signatures = tx.get("transaction", {}).get("signatures", [])
            if not signatures:
                continue
            for key in by_signature.get(signatures[0], []):
                row = wanted[key]
                if row["slot"] != slot or row["transaction_index"] != tx_index:
                    raise ValueError("candidate_location_mismatch")
                if key in found:
                    raise ValueError("candidate_replayed_more_than_once")
                found[key] = classify_transaction(tx, row)
    if set(found) != set(wanted):
        raise ValueError(f"candidate_replay_denominator_mismatch:{len(found)}/{len(wanted)}")
    mint_counts = Counter(row["quote_mint_transaction_privilege"] for row in found.values())
    slot_states = {}
    writable_role_counts: Counter[str] = Counter()
    readonly_role_counts: Counter[str] = Counter()
    for row in found.values():
        writable_role_counts.update(row["writable_target_roles"])
        readonly_role_counts.update(row["readonly_target_roles"])
        slot, mint = row["slot"], row["target_roles"]["quote_mint"]
        counts = slot_mint_privileges[(slot, mint)]
        if counts["account_key_conflict"]:
            state = "same_slot_mint_key_conflict"
        elif counts["writable"]:
            state = "same_slot_mint_writable_potential"
        elif counts["unknown"]:
            state = "same_slot_mint_privilege_unknown"
        else:
            state = "same_slot_mint_readonly_or_absent"
        row["quote_mint_slot_privilege"] = state
        row["same_slot_mint_key_occurrences"] = counts["readonly"] + counts["writable"]
        row["same_slot_mint_writable_transaction_count"] = counts["writable"]
        slot_states[(slot, mint)] = state
    unknown_counts = Counter(
        ",".join(row["unknown_target_affecting_programs"]) or "none" for row in found.values()
    )
    result = {
        "schema_version": "1",
        "contract": CONTRACT,
        "claim": (
            "full-message transaction privilege and target-instruction census for this captured "
            "development class; no economic decode, account-state history, discovery completeness, "
            "or exact-zero claim"
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
        "code_commit": os.environ.get("BRAIN_SENSES_CODE_COMMIT")
        or subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "tool_sha256": _sha256(Path(__file__).read_bytes()),
        "source": {
            "provider": "helius-free",
            "capture_root": str(capture_root),
            "capture_plan_sha256": evidence["source"]["capture_plan_sha256"],
            "evidence_census_sha256": _sha256(evidence_bytes),
            "structural_census_sha256": _sha256(structural_bytes),
            "binding_receipt": evidence["source"]["binding_receipt"],
            "binding_receipt_sha256": evidence["source"]["binding_receipt_sha256"],
            "network_requests": 0,
        },
        "scope": {
            "predicate": evidence["scope"]["predicate"],
            "denominator": expected_denominator,
            "selected_slots": len(selected_slots),
        },
        "summary": {
            "candidate_references": len(found),
            "quote_mint_privilege_counts": dict(sorted(mint_counts.items())),
            "quote_mint_slot_privilege_counts_by_reference": dict(
                sorted(Counter(row["quote_mint_slot_privilege"] for row in found.values()).items())
            ),
            "quote_mint_slot_privilege_counts_by_distinct_slot_mint": dict(
                sorted(Counter(slot_states.values()).items())
            ),
            "selected_full_blocks": len(slot_transaction_counts),
            "selected_full_block_transactions": sum(slot_transaction_counts.values()),
            "references_by_unknown_target_programs": dict(sorted(unknown_counts.items())),
            "references_with_unknown_target_program": sum(
                bool(row["unknown_target_affecting_programs"]) for row in found.values()
            ),
            "writable_references_by_target_role": dict(sorted(writable_role_counts.items())),
            "readonly_references_by_target_role": dict(sorted(readonly_role_counts.items())),
            "historical_extension_state_unavailable": len(found),
            "economic_effects_not_assessed": len(found),
        },
        "rows": [found[key] for key in sorted(found)],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--evidence-census", type=Path, required=True)
    parser.add_argument("--structural-census", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = census(args.capture_root, args.evidence_census, args.structural_census, args.output)
    print(
        json.dumps(
            {"contract": result["contract"], "output": str(args.output), **result["summary"]},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
