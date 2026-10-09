#!/usr/bin/env python3
"""Offline Token-2022 path/state-evidence census for captured PumpSwap v1 buy-exact refs.

This records wire and transaction structure. It does not decode Token-2022 economics or
infer historical mint/account extensions from transaction metadata.
"""

from __future__ import annotations

import argparse
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

from memecoin_pumpswap.buy_exact_v1 import inspect_buy_exact_quote_in_v1  # noqa: E402
from memecoin_pumpswap.compiled_economics import _flatten, _transaction_tree  # noqa: E402
from memecoin_pumpswap.decoder import TOKEN, TOKEN2022  # noqa: E402
from memecoin_pumpswap.legacy_token_ledger import SYSTEM_PROGRAM  # noqa: E402
from memecoin_pumpswap.normalize import normalize_compiled_transaction  # noqa: E402
from memecoin_pumpswap.schema import SCHEMA  # noqa: E402
from tools.brain_senses_buy_exact_v1_census import (  # noqa: E402
    _capture_rows,
    _read_block,
    _sha256,
)

CONTRACT = "pumpswap_v1_buy_exact_token2022_evidence_census_v1"
SCHEMA_VERSION = "1"


def _candidate(row: dict[str, Any]) -> bool:
    roles = row.get("identified_roles")
    return (
        row.get("transaction_status") == "success"
        and row.get("argument_state") == "pinned_idl_v1_shape"
        and row.get("suffix_state") == "documented_fee_pair_suffix_candidate"
        and isinstance(roles, dict)
        and roles.get("base_token_program") == TOKEN
        and roles.get("quote_token_program") == TOKEN2022
    )


def _instruction_record(
    node: dict[str, Any], location: str, target_accounts: set[str]
) -> dict[str, Any]:
    ix = node["instruction"]
    wire = bytes.fromhex(ix["data_hex"])
    result = {
        "path": ix["path"],
        "location": location,
        "program_id": ix["program_id"],
        "account_count": len(ix["accounts"]),
        "touched_target_accounts": sorted(target_accounts.intersection(ix["accounts"])),
        "wire_hex": wire.hex(),
        "wire_length": len(wire),
        "wire_first_byte": wire[0] if wire else None,
        "stack_height": ix["stack_height"],
    }
    if ix["program_id"] in (TOKEN, TOKEN2022, SYSTEM_PROGRAM):
        result["ordered_accounts"] = ix["accounts"]
    return result


def _balance_evidence(normalized: dict[str, Any], targets: set[str]) -> dict[str, Any]:
    accounts = normalized["accounts"]
    result: dict[str, Any] = {}
    for side in ("pre_token_balances", "post_token_balances"):
        rows = normalized[side]
        if not isinstance(rows, list):
            result[side] = {"state": "unknown_balance_trace_unavailable"}
            continue
        side_result: dict[str, Any] = {}
        for row in rows:
            account_index = row.get("accountIndex")
            if type(account_index) is not int or not 0 <= account_index < len(accounts):
                raise ValueError("token_balance_account_index_invalid")
            account = accounts[account_index]["pubkey"]
            if account not in targets:
                continue
            ui_amount = row.get("uiTokenAmount")
            if not isinstance(ui_amount, dict):
                raise ValueError("token_balance_amount_shape_invalid")
            amount = ui_amount.get("amount")
            decimals = ui_amount.get("decimals")
            if not isinstance(amount, str) or not amount.isdecimal() or type(decimals) is not int:
                raise ValueError("token_balance_amount_invalid")
            side_result[account] = {
                "mint": row["mint"],
                "program_id": row["programId"],
                "amount_atomic": int(amount),
                "decimals": decimals,
            }
        result[side] = side_result
    return result


def _analyze_candidate(
    tx: dict[str, Any], row: dict[str, Any], receipt: dict[str, Any], block_time: Any
) -> dict[str, Any]:
    normalized = normalize_compiled_transaction(tx)
    if normalized["meta_error"] is not None:
        raise ValueError("candidate_census_row_is_not_successful")
    inspected = inspect_buy_exact_quote_in_v1(tx)
    matches = [
        item
        for item in inspected["references"]
        if item["path"] == row["path"]
        and item["argument_state"] == row["argument_state"]
        and item["suffix_state"] == row["suffix_state"]
    ]
    if len(matches) != 1:
        raise ValueError("candidate_reference_does_not_replay_uniquely")

    reference = matches[0]
    tree = _transaction_tree(normalized)
    target = next(
        (
            node
            for root in tree
            for node in _flatten(root)
            if node["instruction"]["path"] == row["path"]
        ),
        None,
    )
    if target is None:
        raise ValueError("candidate_instruction_node_missing")
    instruction = target["instruction"]
    account_names = SCHEMA["instructions"]["buy_exact_quote_in"]["accounts"]
    if len(instruction["accounts"]) != len(account_names) + 2:
        raise ValueError("candidate_account_layout_changed")
    roles = dict(zip(account_names, instruction["accounts"][: len(account_names)], strict=True))
    roles["observed_fee_recipient"] = instruction["accounts"][-2]
    roles["observed_fee_ata"] = instruction["accounts"][-1]
    role_names = (
        "pool",
        "user",
        "base_mint",
        "quote_mint",
        "user_base_token_account",
        "pool_base_token_account",
        "user_quote_token_account",
        "pool_quote_token_account",
        "protocol_fee_recipient_token_account",
        "coin_creator_vault_ata",
        "observed_fee_recipient",
        "observed_fee_ata",
    )
    roles = {name: roles[name] for name in role_names if isinstance(roles.get(name), str)}
    for key, field in (
        ("pool", "pool"),
        ("user", "user"),
        ("base_mint", "base_mint"),
        ("quote_mint", "quote_mint"),
        ("user_base_token_account", "user_base_token_account"),
        ("pool_base_token_account", "pool_base_token_account"),
        ("user_quote_token_account", "user_quote_token_account"),
        ("pool_quote_token_account", "pool_quote_token_account"),
    ):
        if roles.get(key) != row["identified_roles"].get(field):
            raise ValueError("replayed_candidate_roles_differ_from_census")
    identified_roles = reference.get("identified_roles", {})
    if (
        identified_roles.get("base_token_program") != TOKEN
        or identified_roles.get("quote_token_program") != TOKEN2022
    ):
        raise ValueError("replayed_candidate_token_programs_differ_from_census")

    subtree = list(_flatten(target))
    subtree_ids = {id(node) for node in subtree}
    all_nodes = [node for root in tree for node in _flatten(root)]
    target_token_account_roles = (
        "user_base_token_account",
        "pool_base_token_account",
        "user_quote_token_account",
        "pool_quote_token_account",
        "protocol_fee_recipient_token_account",
        "coin_creator_vault_ata",
        "observed_fee_ata",
    )
    target_accounts = {roles[name] for name in target_token_account_roles if name in roles}
    outside = [
        node
        for node in all_nodes
        if id(node) not in subtree_ids
        and target_accounts.intersection(node["instruction"]["accounts"])
    ]
    subtree_instructions = [
        _instruction_record(node, "target_subtree", target_accounts) for node in subtree
    ]
    lifecycle_touch_records = [
        _instruction_record(node, "transaction", target_accounts)
        for node in all_nodes
        if node["instruction"]["program_id"] in (TOKEN, TOKEN2022, SYSTEM_PROGRAM)
        and target_accounts.intersection(node["instruction"]["accounts"])
    ]
    sibling_touches = [
        _instruction_record(node, "outside_target_subtree", target_accounts) for node in outside
    ]
    signatures = tx.get("transaction", {}).get("signatures", [])
    if not signatures or signatures[0] != row["signature"]:
        raise ValueError("candidate_signature_does_not_match_census")
    return {
        "signature": row["signature"],
        "slot": row["slot"],
        "transaction_index": row["transaction_index"],
        "instruction_path": row["path"],
        "availability_ns": receipt["received_ns"],
        "native_event_time_unix_seconds": block_time,
        "wire_instruction": {
            "data_hex": row["instruction_data_hex"],
            "length": row["instruction_data_length"],
            "spendable_quote_in_budget_atomic": row["arguments"]["spendable_quote_in"],
            "minimum_base_out_budget_atomic": row["arguments"]["min_base_amount_out"],
            "track_volume": row["arguments"]["track_volume"],
        },
        "token_programs": {
            "base": identified_roles["base_token_program"],
            "quote": identified_roles["quote_token_program"],
        },
        "target_accounts": roles,
        "target_token_accounts": sorted(target_accounts),
        "historical_extension_state": {
            "mint": "historical_extension_state_unavailable",
            "token_accounts": "historical_extension_state_unavailable",
            "fee_configuration_at_bank": "historical_extension_state_unavailable",
            "reason": (
                "transaction metadata does not bind mint/account extension bytes "
                "at the transaction bank"
            ),
        },
        "subtree_instruction_trace": subtree_instructions,
        "target_account_token_or_system_instruction_wire_evidence": lifecycle_touch_records,
        "outside_subtree_target_account_touches": sibling_touches,
        "transaction_boundary_spendable_balances": _balance_evidence(normalized, target_accounts),
        "settlement_state": "not_assessed",
        "economic_amount_interpretation": "not_assessed",
    }


def census(
    capture_root: Path, census_path: Path, binding_receipt_path: Path, output_path: Path
) -> dict[str, Any]:
    capture_root = capture_root.resolve()
    census_path = census_path.resolve()
    binding_receipt_path = binding_receipt_path.resolve()
    output_path = output_path.resolve()
    if output_path.exists():
        raise ValueError("output_exists_refusing_overwrite")
    census_bytes = census_path.read_bytes()
    census_data = json.loads(census_bytes)
    if (
        census_data.get("contract")
        != "pumpswap_v1_buy_exact_quote_in_attempt_and_suffix_candidate_v1"
    ):
        raise ValueError("unexpected_structural_census_contract")
    if census_data.get("source", {}).get("root") != str(capture_root):
        raise ValueError("structural_census_capture_root_mismatch")
    binding_bytes = binding_receipt_path.read_bytes()
    binding = json.loads(binding_bytes)
    direct_capture_binding = (
        Path(binding.get("capture_root", "")).resolve() == capture_root / "captures/helius-free"
        and binding.get("selected_full_blocks_verified") == 512
        and binding.get("offline_replay") is True
    )
    pair64_plan_binding = (
        Path(binding.get("source_root", "")).resolve() == capture_root.parent
        and binding.get("bound_plan_sha256") == census_data.get("source", {}).get("plan_sha256")
        and binding.get("offline_replay") is True
    )
    if not (direct_capture_binding or pair64_plan_binding) or _sha256(
        binding_bytes
    ) != census_data.get("source", {}).get("provider_audit_receipt_sha256"):
        raise ValueError("provider_binding_receipt_does_not_bind_512_helius_blocks")

    candidate_rows = [row for row in census_data["rows"] if _candidate(row)]
    keyed_rows: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
    for row in candidate_rows:
        key = (row["signature"], tuple(row["path"]))
        if key in keyed_rows:
            raise ValueError("duplicate_structural_candidate_signature_path")
        keyed_rows[key] = row

    replayed: list[dict[str, Any]] = []
    found: set[tuple[str, tuple[int, ...]]] = set()
    captures = _capture_rows(capture_root)
    if len(captures) != 512:
        raise ValueError("expected_512_full_block_captures")
    expected_captures = {
        row["slot"]: row for row in census_data.get("source", {}).get("captures", [])
    }
    if len(expected_captures) != 512:
        raise ValueError("structural_census_capture_manifest_invalid")
    by_signature: dict[str, list[tuple[str, tuple[int, ...]]]] = {}
    for key in keyed_rows:
        by_signature.setdefault(key[0], []).append(key)
    for capture_path, receipt, _request in captures:
        block = _read_block("zstd", capture_path, receipt)
        request = json.loads(receipt["request_json"])
        capture_slot = request["params"][0]
        expected_capture = expected_captures.get(capture_slot)
        if (
            expected_capture is None
            or expected_capture["response_sha256"] != receipt["response_sha256"]
            or expected_capture["compressed_sha256"] != receipt["storage"]["sha256"]
            or expected_capture["compressed_bytes"] != receipt["storage"]["bytes"]
        ):
            raise ValueError("capture_not_bound_by_structural_census")
        for transaction_index, tx in enumerate(block["transactions"]):
            signatures = tx.get("transaction", {}).get("signatures", [])
            if not signatures:
                continue
            signature = signatures[0]
            matching = by_signature.get(signature, [])
            if not matching:
                continue
            for key in matching:
                row = keyed_rows[key]
                if row["slot"] != capture_slot:
                    raise ValueError("candidate_slot_mismatch")
                if row["transaction_index"] != transaction_index:
                    raise ValueError("candidate_transaction_index_mismatch")
                replayed.append(_analyze_candidate(tx, row, receipt, block.get("blockTime")))
                found.add(key)
    if found != set(keyed_rows):
        raise ValueError(f"candidate_replay_denominator_mismatch:{len(found)}/{len(keyed_rows)}")

    shape_counts: Counter[tuple[Any, ...]] = Counter()
    for row in replayed:
        token_shape_counts = Counter(
            (item["program_id"], item["wire_first_byte"], item["wire_length"])
            for item in row["subtree_instruction_trace"]
            if item["program_id"] in (TOKEN, TOKEN2022)
        )
        token_shapes = tuple(sorted((*shape, count) for shape, count in token_shape_counts.items()))
        shape_counts[
            (
                tuple(row["instruction_path"]),
                token_shapes,
                bool(row["outside_subtree_target_account_touches"]),
            )
        ] += 1
    summary = {
        "candidate_references": len(replayed),
        "unique_signatures": len({row["signature"] for row in replayed}),
        "slots": len({row["slot"] for row in replayed}),
        "historical_extension_state_unavailable": len(replayed),
        "with_outside_subtree_target_account_touch": sum(
            bool(row["outside_subtree_target_account_touches"]) for row in replayed
        ),
        "by_subtree_shape": [
            {
                "instruction_path": path,
                "token_program_wire_shapes_first_byte_length_count": shapes,
                "has_outside_subtree_target_account_touch": sibling,
                "references": count,
            }
            for (path, shapes, sibling), count in sorted(
                shape_counts.items(), key=lambda item: repr(item[0])
            )
        ],
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "contract": CONTRACT,
        "claim": (
            "captured-class structural and wire census only; no Token-2022 economic decode, "
            "historical extension state, completeness, or exact-zero claim"
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
            "capture_root": str(capture_root),
            "capture_plan_sha256": census_data["source"]["plan_sha256"],
            "structural_census_sha256": _sha256(census_bytes),
            "binding_receipt": str(binding_receipt_path),
            "binding_receipt_sha256": _sha256(binding_bytes),
            "provider_audit_receipt": census_data["source"]["provider_audit_receipt"],
            "provider_audit_receipt_sha256": census_data["source"]["provider_audit_receipt_sha256"],
            "provider": "helius-free",
        },
        "scope": {
            "predicate": {
                "transaction_status": "success",
                "argument_state": "pinned_idl_v1_shape",
                "suffix_state": "documented_fee_pair_suffix_candidate",
                "base_token_program": TOKEN,
                "quote_token_program": TOKEN2022,
            },
            "structural_source_candidate_denominator": len(candidate_rows),
        },
        "summary": summary,
        "rows": replayed,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--structural-census", type=Path, required=True)
    parser.add_argument("--binding-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = census(args.capture_root, args.structural_census, args.binding_receipt, args.output)
    print(
        json.dumps(
            {"contract": result["contract"], "output": str(args.output), **result["summary"]},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
