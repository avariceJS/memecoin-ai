#!/usr/bin/env python3
"""Verify pinned buyExact role positions against sealed full-block captures (offline)."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from memecoin_pumpswap.buy_exact_v1 import ACCOUNT_ROLES, IDL_SHA256  # noqa: E402
from memecoin_pumpswap.decoder import PROGRAM, decode58  # noqa: E402
from tools.brain_senses_buy_exact_v1_census import _capture_rows, _read_block  # noqa: E402

TARGET_ROLES = ("pool", "base_mint", "quote_mint", "base_token_program", "quote_token_program")
SELECTOR = {
    "instruction_data_length": 25,
    "remaining_account_count": 2,
    "argument_state": "pinned_idl_v1_shape",
    "suffix_state": "documented_fee_pair_suffix_candidate",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _key(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("pubkey"), str):
        return value["pubkey"]
    raise ValueError("invalid_message_account_key")


def _compiled_instruction(tx: dict[str, Any], path: list[int]) -> dict[str, Any]:
    message = tx.get("transaction", {}).get("message", {})
    if len(path) == 1:
        instructions = message.get("instructions", [])
        outer_index = path[0]
        if not isinstance(instructions, list) or not 0 <= outer_index < len(instructions):
            raise ValueError("outer_instruction_path_missing")
        return instructions[outer_index]
    if len(path) == 2:
        outer_index, inner_index = path
        groups = tx.get("meta", {}).get("innerInstructions") or []
        for group in groups:
            if group.get("index") == outer_index:
                instructions = group.get("instructions", [])
                if isinstance(instructions, list) and 0 <= inner_index < len(instructions):
                    return instructions[inner_index]
        raise ValueError("inner_instruction_path_missing")
    raise ValueError("unsupported_instruction_path_depth")


def _roles_from_raw(tx: dict[str, Any], path: list[int]) -> tuple[dict[str, str], str]:
    message = tx.get("transaction", {}).get("message", {})
    raw_keys = message.get("accountKeys")
    if not isinstance(raw_keys, list):
        raise ValueError("message_account_keys_missing")
    keys = [_key(value) for value in raw_keys]
    loaded = tx.get("meta", {}).get("loadedAddresses") or {}
    keys.extend(_key(value) for value in loaded.get("writable", []))
    keys.extend(_key(value) for value in loaded.get("readonly", []))

    instruction = _compiled_instruction(tx, path)
    program_index = instruction.get("programIdIndex")
    if type(program_index) is not int or not 0 <= program_index < len(keys):
        raise ValueError("instruction_program_index_invalid")
    if keys[program_index] != PROGRAM:
        raise ValueError("instruction_program_mismatch")
    account_indexes = instruction.get("accounts")
    if not isinstance(account_indexes, list) or len(account_indexes) < len(ACCOUNT_ROLES):
        raise ValueError("instruction_account_prefix_short")
    if any(type(index) is not int or not 0 <= index < len(keys) for index in account_indexes):
        raise ValueError("instruction_account_index_invalid")
    wire = decode58(instruction["data"])
    role_keys = (keys[index] for index in account_indexes[: len(ACCOUNT_ROLES)])
    result = dict(zip(ACCOUNT_ROLES, role_keys, strict=True))
    return {role: result[role] for role in TARGET_ROLES}, wire.hex()


def _validate_frame_binding(
    report: dict[str, Any], epoch_root: Path, frame_receipt_path: Path
) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes, bytes]:
    source = report.get("source")
    if (
        not isinstance(source, dict)
        or Path(source.get("root", "")).resolve() != epoch_root.resolve()
    ):
        raise ValueError("census_report_epoch_root_mismatch")
    frame_bytes = frame_receipt_path.read_bytes()
    plan_bytes = (epoch_root / "epoch-plan.json").read_bytes()
    summary_bytes = (epoch_root / "epoch-summary.json").read_bytes()
    frame_receipt = json.loads(frame_bytes)
    frame = frame_receipt.get("frame")
    summary = json.loads(summary_bytes)
    if not isinstance(frame, dict):
        raise ValueError("frame_receipt_frame_missing")
    if (
        source.get("source_binding_kind") != "helius_v3_frame_receipt_bound_offline_replay"
        or source.get("plan_sha256") != _sha(plan_bytes)
        or source.get("provider_audit_receipt_sha256") != _sha(frame_bytes)
        or frame.get("status") != "frame_complete"
        or frame.get("reservation_status") != "reconciled"
        or frame.get("request_statuses") != {"captured": frame.get("actual_requests")}
        or frame.get("sampled_structural_checks", {}).get("passed") != 16
        or frame.get("sampled_structural_checks", {}).get("total") != 16
        or frame.get("plan_sha256") != _sha(plan_bytes)
        or frame.get("summary_sha256") != _sha(summary_bytes)
        or frame.get("forward_only", {}).get("missed_slot_backfill") is not False
    ):
        raise ValueError("sealed_frame_receipt_binding_mismatch")
    if (
        summary.get("epoch_id") != epoch_root.name
        or summary.get("status") != "frame_complete"
        or not isinstance(summary.get("selected_slots"), list)
        or len(summary["selected_slots"]) != 16
        or len(summary.get("structural_checks", [])) != 16
        or any(
            check.get("status") != "qualified_structural_reference_count"
            for check in summary["structural_checks"]
        )
    ):
        raise ValueError("sealed_epoch_summary_mismatch")
    return frame_receipt, summary, frame_bytes, plan_bytes, summary_bytes


def qualify(report_path: Path, epoch_root: Path, frame_receipt_path: Path) -> dict[str, Any]:
    report_bytes = report_path.read_bytes()
    report = json.loads(report_bytes)
    frame_receipt, summary, frame_bytes, plan_bytes, summary_bytes = _validate_frame_binding(
        report, epoch_root, frame_receipt_path
    )
    candidates = [
        row
        for row in report["rows"]
        if all(row.get(key) == value for key, value in SELECTOR.items())
    ]
    if len(candidates) != 59:
        raise ValueError(f"expected_59_candidates_got_{len(candidates)}")

    zstd = shutil.which("zstd")
    if zstd is None:
        raise ValueError("zstd_cli_unavailable")
    capture_entries = _capture_rows(epoch_root, v3_layout=True)
    by_slot: dict[int, tuple[dict[str, Any], dict[str, Any], Path]] = {}
    captures = []
    for capture_path, receipt, request in capture_entries:
        params = request.get("params")
        if not isinstance(params, list) or not params or type(params[0]) is not int:
            continue
        slot = params[0]
        by_slot[slot] = (receipt, request, capture_path)
        storage = receipt["storage"]
        captures.append({
            "slot": slot,
            "capture_sha256": storage["sha256"],
            "capture_bytes": storage["bytes"],
            "response_sha256": receipt["response_sha256"],
            "response_bytes": receipt["response_bytes"],
        })

    selected_slots = set(summary["selected_slots"])
    if set(by_slot) != selected_slots:
        raise ValueError("full_block_captures_do_not_match_selected_slots")
    report_captures = report.get("source", {}).get("captures", [])
    report_capture_hashes = {
        capture["slot"]: (capture["compressed_sha256"], capture["response_sha256"])
        for capture in report_captures
    }
    actual_capture_hashes = {
        slot: (receipt["storage"]["sha256"], receipt["response_sha256"])
        for slot, (receipt, _, _) in by_slot.items()
    }
    if report_capture_hashes != actual_capture_hashes:
        raise ValueError("census_report_capture_hash_set_mismatch")

    loaded_blocks: dict[int, dict[str, Any]] = {}
    pools: dict[str, dict[str, Any]] = {}
    status_counts = Counter()
    for row in candidates:
        slot = row["slot"]
        if slot not in by_slot:
            raise ValueError("candidate_slot_missing_full_block_capture")
        if slot not in loaded_blocks:
            receipt, _, capture_path = by_slot[slot]
            block = _read_block(zstd, capture_path, receipt)
            if block.get("parentSlot") is None:
                raise ValueError("captured_block_parent_slot_missing")
            loaded_blocks[slot] = block
        transactions = loaded_blocks[slot]["transactions"]
        tx_index = row["transaction_index"]
        if type(tx_index) is not int or not 0 <= tx_index < len(transactions):
            raise ValueError("candidate_transaction_index_missing")
        tx = transactions[tx_index]
        signatures = tx.get("transaction", {}).get("signatures", [])
        if not signatures or signatures[0] != row["signature"]:
            raise ValueError("candidate_signature_mismatch")
        raw_roles, wire_hex = _roles_from_raw(tx, row["path"])
        if wire_hex != row["instruction_data_hex"]:
            raise ValueError("candidate_instruction_wire_mismatch")
        expected_status = "success" if tx.get("meta", {}).get("err") is None else "failed"
        if row["transaction_status"] != expected_status:
            raise ValueError("candidate_transaction_status_mismatch")
        identified = row.get("identified_roles") or {}
        if any(raw_roles[role] != identified.get(role) for role in TARGET_ROLES):
            raise ValueError("identified_idl_role_mismatch")
        if raw_roles["base_mint"] == raw_roles["quote_mint"]:
            raise ValueError("base_quote_mint_alias")
        pool = raw_roles["pool"]
        tuple_value = tuple(raw_roles[role] for role in TARGET_ROLES[1:])
        entry = pools.setdefault(pool, {
            "pool": pool,
            "base_mint": raw_roles["base_mint"],
            "quote_mint": raw_roles["quote_mint"],
            "base_token_program": raw_roles["base_token_program"],
            "quote_token_program": raw_roles["quote_token_program"],
            "successful_references": 0,
            "failed_transaction_references": 0,
        })
        existing_tuple = tuple(entry[role] for role in TARGET_ROLES[1:])
        if existing_tuple != tuple_value:
            raise ValueError("conflicting_role_tuple_for_pool")
        count_key = (
            "successful_references"
            if expected_status == "success"
            else "failed_transaction_references"
        )
        entry[count_key] += 1
        status_counts[expected_status] += 1

    if len(pools) != 27 or status_counts != Counter({"success": 53, "failed": 6}):
        raise ValueError("pool_or_status_denominator_mismatch")
    script_bytes = Path(__file__).read_bytes()
    return {
        "schema_version": 1,
        "evidence_id": "pumpswap-buy-exact-v1-c3-role-tuple-qualification-2026-10-10",
        "scope": (
            "offline raw-capture qualification of 59 preselected 25-byte/two-account "
            "structural suffix candidates in one sealed c3 frame"
        ),
        "result": {
            "references_verified": sum(status_counts.values()),
            "successful_transaction_references": status_counts["success"],
            "failed_transaction_references": status_counts["failed"],
            "distinct_pools": len(pools),
            "mints_nonaliased": True,
            "raw_roles_match_census": True,
            "no_conflicting_tuple_per_pool": True,
            "pools": sorted(pools.values(), key=lambda value: value["pool"]),
        },
        "source": {
            "census_report_sha256": _sha(report_bytes),
            "frame_receipt": frame_receipt_path.as_posix(),
            "frame_receipt_sha256": _sha(frame_bytes),
            "epoch_plan_sha256": _sha(plan_bytes),
            "epoch_summary_sha256": _sha(summary_bytes),
            "full_block_captures": sorted(captures, key=lambda value: value["slot"]),
            "pinned_idl_sha256": IDL_SHA256,
            "qualifier_sha256": _sha(script_bytes),
            "source_binding": (
                "raw_full_block_account_indices_and_message_keys; "
                "loaded writable then readonly"
            ),
        },
        "limitations": [
            "This validates structural IDL-position attribution only, not economic role "
            "orientation, pool state, settlement, amounts, pricing, fees, or completeness.",
            "The 59 references are dependent observations from one sampled c3 frame and 27 pools.",
            "Successful transaction status does not establish that the candidate instruction "
            "settled an economic fill.",
            "Failed transaction references remain attempts without settlement.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--epoch-root", type=Path, required=True)
    parser.add_argument("--frame-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite output")
    result = qualify(args.report, args.epoch_root, args.frame_receipt)
    data = json.dumps(result, indent=2, sort_keys=True).encode() + b"\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        stream.write(data)
    print(
        json.dumps(
            {"output": str(args.output), "sha256": _sha(data), "result": result["result"]},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
