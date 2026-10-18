#!/usr/bin/env python3
"""Offline exact-wire and same-slot account-privilege inventory for clean Token-2022 refs."""

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

from memecoin_pumpswap.compiled_economics import _flatten, _transaction_tree  # noqa: E402
from memecoin_pumpswap.decoder import TOKEN, TOKEN2022  # noqa: E402
from memecoin_pumpswap.normalize import normalize_compiled_transaction  # noqa: E402
from tools.brain_senses_buy_exact_v1_census import _capture_rows, _read_block, _sha256  # noqa: E402

CONTRACT = "pumpswap_v1_buy_exact_token2022_clean_stratum_trace_v1"
CLEAN_DENOMINATOR = 1146
READONLY_SLOT = "same_slot_mint_readonly_or_absent"


def _raw_privileges(tx: dict[str, Any]) -> tuple[dict[str, bool], set[str]]:
    message = tx.get("transaction", {}).get("message", {})
    meta = tx.get("meta", {})
    static_raw = message.get("accountKeys")
    header = message.get("header")
    if not isinstance(static_raw, list) or not isinstance(header, dict):
        raise ValueError("slot_scan_message_keys_or_header_missing")
    static = [item.get("pubkey") if isinstance(item, dict) else item for item in static_raw]
    if any(not isinstance(key, str) for key in static):
        raise ValueError("slot_scan_static_key_invalid")
    required, ro_signed, ro_unsigned = (
        header.get("numRequiredSignatures"),
        header.get("numReadonlySignedAccounts"),
        header.get("numReadonlyUnsignedAccounts"),
    )
    if any(type(n) is not int or n < 0 for n in (required, ro_signed, ro_unsigned)):
        raise ValueError("slot_scan_header_privileges_invalid")
    if required > len(static) or ro_signed > required or ro_unsigned > len(static) - required:
        raise ValueError("slot_scan_header_privileges_out_of_range")
    lookups = message.get("addressTableLookups", [])
    if not isinstance(lookups, list):
        raise ValueError("slot_scan_lookup_shape_invalid")
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
        raise ValueError("slot_scan_loaded_addresses_shape_invalid")
    loaded_w, loaded_ro = loaded["writable"], loaded["readonly"]
    if any(not isinstance(key, str) for key in loaded_w + loaded_ro):
        raise ValueError("slot_scan_loaded_key_invalid")
    expected_w = expected_ro = 0
    for lookup in lookups:
        if not isinstance(lookup, dict):
            raise ValueError("slot_scan_lookup_entry_invalid")
        for field in ("writableIndexes", "readonlyIndexes"):
            values = lookup.get(field)
            if not isinstance(values, list) or any(
                type(value) is not int or not 0 <= value <= 255 for value in values
            ):
                raise ValueError("slot_scan_lookup_indices_invalid")
            if field == "writableIndexes":
                expected_w += len(values)
            else:
                expected_ro += len(values)
    if len(loaded_w) != expected_w or len(loaded_ro) != expected_ro:
        raise ValueError("slot_scan_loaded_address_count_mismatch")
    keys = static + loaded_w + loaded_ro
    flags = (
        [
            index < required - ro_signed or required <= index < len(static) - ro_unsigned
            for index in range(len(static))
        ]
        + [True] * len(loaded_w)
        + [False] * len(loaded_ro)
    )
    counts = Counter(keys)
    conflicts = {key for key, count in counts.items() if count > 1}
    privileges = {key: flags[index] for index, key in enumerate(keys) if key not in conflicts}
    for index, raw_key in enumerate(static_raw):
        if isinstance(raw_key, dict) and "writable" in raw_key:
            if raw_key["writable"] is not flags[index]:
                raise ValueError("slot_scan_header_flag_disagreement")
    return privileges, conflicts


def _trace_candidate(tx: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_compiled_transaction(tx)
    if normalized["meta_error"] is not None:
        raise ValueError("clean_candidate_not_successful")
    roles = row.get("target_accounts")
    if not isinstance(roles, dict) or not roles:
        raise ValueError("clean_candidate_roles_missing")
    privileges, conflicts = _raw_privileges(tx)
    if set(roles.values()).intersection(conflicts):
        raise ValueError("clean_candidate_target_key_conflict")
    if any(key not in privileges for key in roles.values()):
        raise ValueError("clean_candidate_target_key_unresolved")
    tree = _transaction_tree(normalized)
    target_path = row["instruction_path"]
    target_set = set(roles.values())
    trace = []
    token2022_variants = Counter()
    for root in tree:
        for node in _flatten(root):
            ix = node["instruction"]
            touched = sorted(target_set.intersection(ix["accounts"]))
            if not touched:
                continue
            wire = bytes.fromhex(ix["data_hex"])
            token_program = ix["program_id"] in (TOKEN, TOKEN2022)
            if ix["program_id"] == TOKEN2022:
                token2022_variants[(wire[0] if wire else None, len(wire))] += 1
            path = ix["path"]
            trace.append(
                {
                    "path": path,
                    "stack_height": ix["stack_height"],
                    "relation_to_candidate": (
                        "candidate_or_descendant"
                        if path[: len(target_path)] == target_path
                        else "outside_candidate_subtree"
                    ),
                    "program_id": ix["program_id"],
                    "touched_target_roles": sorted(
                        role for role, key in roles.items() if key in touched
                    ),
                    "touched_accounts": [
                        {"pubkey": key, "writable_potential": privileges[key]} for key in touched
                    ],
                    "wire_hex": wire.hex(),
                    "wire_sha256": hashlib.sha256(wire).hexdigest(),
                    "token_program_instruction": token_program,
                }
            )
    if not any(item["path"] == target_path for item in trace):
        raise ValueError("candidate_path_missing_from_target_trace")
    unsupported_token2022_shape = any(
        item["program_id"] == TOKEN2022
        and (
            len(bytes.fromhex(item["wire_hex"])) != 10
            or not bytes.fromhex(item["wire_hex"])
            or bytes.fromhex(item["wire_hex"])[0] != 12
        )
        for item in trace
    )
    return {
        "signature": row["signature"],
        "slot": row["slot"],
        "transaction_index": row["transaction_index"],
        "instruction_path": target_path,
        "target_accounts": roles,
        "target_instruction_trace": trace,
        "token2022_wire_shape_counts": [
            {"first_byte": first, "length": length, "count": count}
            for (first, length), count in sorted(token2022_variants.items(), key=repr)
        ],
        "token2022_wire_state": (
            "unclassified_wire_shape_present"
            if unsupported_token2022_shape
            else "checked_form_shape_observed_no_economic_decode"
        ),
        "historical_extension_state": "unavailable",
        "economic_effects": "not_assessed",
    }


def census(
    capture_root: Path,
    evidence_path: Path,
    privilege_receipt_path: Path,
    structural_path: Path,
    output: Path,
) -> dict[str, Any]:
    capture_root, evidence_path, privilege_receipt_path, structural_path, output = (
        value.resolve()
        for value in (capture_root, evidence_path, privilege_receipt_path, structural_path, output)
    )
    if output.exists():
        raise ValueError("output_exists_refusing_overwrite")
    evidence_bytes = evidence_path.read_bytes()
    evidence = json.loads(
        subprocess.run(["zstd", "-dc", str(evidence_path)], check=True, capture_output=True).stdout
    )
    privilege_bytes = privilege_receipt_path.read_bytes()
    privilege = json.loads(privilege_bytes)
    structural_bytes = structural_path.read_bytes()
    structural = json.loads(structural_bytes)
    if privilege.get("contract") != "pumpswap_v1_buy_exact_token2022_privilege_census_v1":
        raise ValueError("unexpected_privilege_receipt_contract")
    privilege_tool = REPO_ROOT / "tools/brain_senses_buy_exact_v1_token2022_privilege_census.py"
    if privilege.get("tool_sha256") != _sha256(privilege_tool.read_bytes()):
        raise ValueError("privilege_tool_hash_mismatch")
    if evidence.get("contract") != "pumpswap_v1_buy_exact_token2022_evidence_census_v1":
        raise ValueError("unexpected_evidence_census_contract")
    if evidence.get("source", {}).get("capture_root") != str(capture_root):
        raise ValueError("evidence_capture_root_mismatch")
    if privilege.get("source", {}).get("evidence_census_sha256") != _sha256(evidence_bytes):
        raise ValueError("evidence_census_hash_mismatch")
    if privilege.get("source", {}).get("structural_census_sha256") != _sha256(structural_bytes):
        raise ValueError("structural_census_hash_mismatch")
    if structural.get("source", {}).get("plan_sha256") != evidence.get("source", {}).get(
        "capture_plan_sha256"
    ):
        raise ValueError("capture_plan_hash_mismatch")
    binding_path = Path(evidence["source"]["binding_receipt"]).resolve()
    binding_bytes = binding_path.read_bytes()
    if _sha256(binding_bytes) != evidence["source"]["binding_receipt_sha256"]:
        raise ValueError("provider_binding_receipt_hash_mismatch")
    if json.loads(binding_bytes).get("bound_plan_sha256") != structural["source"]["plan_sha256"]:
        raise ValueError("provider_binding_receipt_plan_mismatch")
    expected_manifest = {item["slot"]: item for item in structural["source"]["captures"]}
    evidence_rows = {
        (row["signature"], tuple(row["instruction_path"])): row for row in evidence["rows"]
    }
    clean_rows = [
        row
        for row in privilege["rows"]
        if row["quote_mint_slot_privilege"] == READONLY_SLOT
        and not row["unknown_target_affecting_programs"]
    ]
    if len(clean_rows) != CLEAN_DENOMINATOR:
        raise ValueError("clean_partition_denominator_invalid")
    partition_counts = Counter(
        (
            row["quote_mint_slot_privilege"],
            bool(row["unknown_target_affecting_programs"]),
        )
        for row in privilege["rows"]
    )
    expected_partitions = Counter(
        {
            (READONLY_SLOT, False): 1146,
            (READONLY_SLOT, True): 67,
            ("same_slot_mint_writable_potential", False): 11,
            ("same_slot_mint_writable_potential", True): 10,
        }
    )
    if partition_counts != expected_partitions:
        raise ValueError("privilege_partition_cross_tab_changed")
    selected: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
    target_accounts_by_slot: dict[int, set[str]] = defaultdict(set)
    by_signature: dict[str, list[tuple[str, tuple[int, ...]]]] = defaultdict(list)
    for row in clean_rows:
        key = (row["signature"], tuple(row["instruction_path"]))
        source_row = evidence_rows.get(key)
        if source_row is None or source_row["slot"] != row["slot"]:
            raise ValueError("clean_reference_missing_from_source_census")
        selected[key] = source_row
        target_accounts_by_slot[row["slot"]].update(source_row["target_accounts"].values())
        by_signature[key[0]].append(key)

    traces: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
    slot_target_privileges: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    slot_tx_counts: Counter[int] = Counter()
    captures = _capture_rows(capture_root)
    wanted_slots = {row["slot"] for row in clean_rows}
    captures = [
        entry
        for entry in captures
        if json.loads(entry[1]["request_json"])["params"][0] in wanted_slots
    ]
    zstd = subprocess.run(
        ["which", "zstd"], check=True, capture_output=True, text=True
    ).stdout.strip()
    for capture_path, receipt, _request in captures:
        slot = json.loads(receipt["request_json"])["params"][0]
        binding = expected_manifest.get(slot)
        if (
            binding is None
            or binding.get("response_sha256") != receipt.get("response_sha256")
            or binding.get("compressed_sha256") != receipt.get("storage", {}).get("sha256")
        ):
            raise ValueError("selected_capture_not_bound_by_structural_census")
        block = _read_block(zstd, capture_path, receipt)
        for tx_index, tx in enumerate(block["transactions"]):
            slot_tx_counts[slot] += 1
            privileges, conflicts = _raw_privileges(tx)
            for account in target_accounts_by_slot[slot]:
                if account in conflicts:
                    slot_target_privileges[(slot, account)]["account_key_conflict"] += 1
                elif account in privileges:
                    state = "writable_potential" if privileges[account] else "readonly"
                    slot_target_privileges[(slot, account)][state] += 1
                else:
                    slot_target_privileges[(slot, account)]["absent"] += 1
            signatures = tx.get("transaction", {}).get("signatures", [])
            if not signatures:
                continue
            for key in by_signature.get(signatures[0], []):
                row = selected[key]
                if row["slot"] != slot or row["transaction_index"] != tx_index:
                    raise ValueError("clean_reference_location_mismatch")
                if key in traces:
                    raise ValueError("clean_reference_replayed_more_than_once")
                traces[key] = _trace_candidate(tx, row)
    if set(traces) != set(selected):
        raise ValueError(f"clean_reference_denominator_mismatch:{len(traces)}/{len(selected)}")

    for row in traces.values():
        per_account = {}
        for role, account in row["target_accounts"].items():
            counts = slot_target_privileges[(row["slot"], account)]
            if counts["account_key_conflict"]:
                state = "same_slot_account_key_conflict"
            elif counts["writable_potential"]:
                state = "same_slot_writable_potential"
            else:
                state = "same_slot_readonly_or_absent"
            per_account[role] = {
                "pubkey": account,
                "slot_privilege_state": state,
                "same_slot_writable_transaction_count": counts["writable_potential"],
                "same_slot_readonly_transaction_count": counts["readonly"],
                "same_slot_absent_transaction_count": counts["absent"],
            }
        row["same_slot_target_account_privileges"] = per_account

    wire_shapes: Counter[tuple[str, int | None, int]] = Counter()
    outside_counts: Counter[str] = Counter()
    role_write_counts: Counter[str] = Counter()
    for row in traces.values():
        for item in row["target_instruction_trace"]:
            if item["relation_to_candidate"] == "outside_candidate_subtree":
                outside_counts[item["program_id"]] += 1
            if item["program_id"] == TOKEN2022:
                data = bytes.fromhex(item["wire_hex"])
                wire_shapes[(item["program_id"], data[0] if data else None, len(data))] += 1
        for role, info in row["same_slot_target_account_privileges"].items():
            if info["slot_privilege_state"] == "same_slot_writable_potential":
                role_write_counts[role] += 1
    result = {
        "schema_version": "1",
        "contract": CONTRACT,
        "claim": (
            "offline exact-wire and full-slot account-privilege inventory for the selected clean "
            "development references; no Token-2022 economic decode, historical extension state, "
            "settlement, global completeness, or exact-zero claim"
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
            "privilege_receipt_sha256": _sha256(privilege_bytes),
            "structural_census_sha256": _sha256(structural_bytes),
            "network_requests": 0,
        },
        "scope": {
            "clean_predicate": {
                "quote_mint_slot_privilege": READONLY_SLOT,
                "unknown_target_affecting_programs": [],
            },
            "denominator": len(traces),
            "selected_full_blocks": len(slot_tx_counts),
            "selected_full_block_transactions": sum(slot_tx_counts.values()),
        },
        "summary": {
            "references": len(traces),
            "token2022_wire_shape_counts": [
                {"program_id": program, "first_byte": first, "length": length, "count": count}
                for (program, first, length), count in sorted(wire_shapes.items(), key=repr)
            ],
            "references_by_token2022_wire_state": dict(
                sorted(Counter(row["token2022_wire_state"] for row in traces.values()).items())
            ),
            "outside_candidate_subtree_instruction_counts_by_program": dict(
                sorted(outside_counts.items())
            ),
            "references_with_same_slot_potential_write_by_target_role": dict(
                sorted(role_write_counts.items())
            ),
            "historical_extension_state_unavailable": len(traces),
            "economic_effects_not_assessed": len(traces),
        },
        "rows": [traces[key] for key in sorted(traces)],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--evidence-census", type=Path, required=True)
    parser.add_argument("--privilege-receipt", type=Path, required=True)
    parser.add_argument("--structural-census", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = census(
        args.capture_root,
        args.evidence_census,
        args.privilege_receipt,
        args.structural_census,
        args.output,
    )
    print(
        json.dumps(
            {"contract": result["contract"], "output": str(args.output), **result["summary"]},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
