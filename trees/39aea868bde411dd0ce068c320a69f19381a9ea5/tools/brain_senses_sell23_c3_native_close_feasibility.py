#!/usr/bin/env python3
"""Offline, row-level feasibility census for censored c3 sell23 native closes.

This is an evidence inventory only. It never fetches account state or derives a
wallet-net execution price. Lamport quantities are integer lamports; token
amounts are token atomic units. No conversion between them is made here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

repo = Path.cwd()
sys.path.insert(0, str(repo / "src"))
sys.path.insert(0, str(repo / "tools"))

from brain_senses_census_pumpswap_sell_ledger import _capture  # noqa: E402

from memecoin_pumpswap.decoder import PROGRAM, TOKEN, encode58  # noqa: E402
from memecoin_pumpswap.economic_v2 import diagnose_pumpswap_sell_v2  # noqa: E402
from memecoin_pumpswap.normalize import normalize_compiled_transaction  # noqa: E402
from memecoin_pumpswap.schema import SCHEMA  # noqa: E402

FRAME_IDS = [f"20261010T{hour:02d}0000Z" for hour in range(5, 11)]
ROOT = repo / "work/brain-senses-live-helius-inventory-v3-20261010-3/epochs"
BASELINE_RECEIPT = repo / "docs/evidence/pumpswap-sell23-c3-six-frame-ledger-replay-2026-10-10.json"
CONTRACT = "pumpswap_sell23_c3_native_close_feasibility_v1"
RENT_SYSVAR = "SysvarRent111111111111111111111111111111111"
SYSTEM_PROGRAM = "11111111111111111111111111111111"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _system_effect(instruction: dict[str, Any]) -> dict[str, Any] | None:
    """Decode only System instructions with explicit lamport effects."""
    wire = bytes.fromhex(instruction["data_hex"])
    accounts = instruction["accounts"]
    if instruction["program_id"] != SYSTEM_PROGRAM or len(wire) < 4:
        return None
    tag = int.from_bytes(wire[:4], "little")
    path = instruction["path"]
    if tag == 0 and len(wire) == 52 and len(accounts) >= 2:
        return {
            "kind": "create_account",
            "path": path,
            "payer": accounts[0],
            "account": accounts[1],
            "lamports": int.from_bytes(wire[4:12], "little"),
            "space_bytes": int.from_bytes(wire[12:20], "little"),
            "owner_program": encode58(wire[20:52]),
        }
    if tag == 2 and len(wire) == 12 and len(accounts) >= 2:
        return {
            "kind": "transfer",
            "path": path,
            "source": accounts[0],
            "destination": accounts[1],
            "lamports": int.from_bytes(wire[4:12], "little"),
        }
    return {"kind": "unsupported_system", "path": path, "tag": tag}


def _token_effect(instruction: dict[str, Any]) -> dict[str, Any] | None:
    if instruction["program_id"] != TOKEN:
        return None
    wire = bytes.fromhex(instruction["data_hex"])
    accounts = instruction["accounts"]
    if not wire:
        return {"kind": "unsupported_legacy_token", "path": instruction["path"], "tag": None}
    tag = wire[0]
    path = instruction["path"]
    if tag == 17 and wire == b"\x11" and accounts:
        return {"kind": "sync_native", "path": path, "account": accounts[0]}
    if tag == 9 and wire == b"\x09" and len(accounts) >= 3:
        return {
            "kind": "close_account",
            "path": path,
            "account": accounts[0],
            "destination": accounts[1],
            "authority": accounts[2],
        }
    if tag in (1, 16, 18) and len(accounts) >= 2:
        expected = {1: 1, 16: 33, 18: 33}[tag]
        if len(wire) == expected:
            return {"kind": "initialize_account", "path": path, "account": accounts[0]}
    if tag == 12 and len(wire) == 10 and len(accounts) >= 4:
        return {
            "kind": "transfer_checked",
            "path": path,
            "source": accounts[0],
            "destination": accounts[2],
            "mint": accounts[1],
            "amount_atomic": int.from_bytes(wire[1:9], "little"),
            "decimals": wire[9],
        }
    if tag == 3 and len(wire) == 9 and len(accounts) >= 3:
        return {
            "kind": "transfer",
            "path": path,
            "source": accounts[0],
            "destination": accounts[1],
            "amount_atomic": int.from_bytes(wire[1:9], "little"),
        }
    return {"kind": "unsupported_legacy_token", "path": path, "tag": tag}


def _path_inside(path: list[int], root_path: list[int]) -> bool:
    return len(path) >= len(root_path) and path[: len(root_path)] == root_path


def _token_endpoint(tx: dict[str, Any], account_index: int, field: str) -> dict[str, Any] | None:
    rows = (tx.get("meta") or {}).get(field)
    if not isinstance(rows, list):
        return None
    matches = [
        row for row in rows if isinstance(row, dict) and row.get("accountIndex") == account_index
    ]
    if len(matches) > 1:
        raise ValueError("duplicate_native_token_endpoint")
    if not matches:
        return None
    row = matches[0]
    amount = (row.get("uiTokenAmount") or {}).get("amount")
    if not isinstance(amount, str) or not amount.isascii() or not amount.isdecimal():
        raise ValueError("invalid_native_token_endpoint")
    return {
        "amount_atomic": int(amount),
        "mint": row.get("mint"),
        "owner": row.get("owner"),
        "program": row.get("programId"),
    }


def _lamport_endpoint(
    tx: dict[str, Any], account_index: int, field: str, account_count: int
) -> int | None:
    values = (tx.get("meta") or {}).get(field)
    if not isinstance(values, list) or len(values) != account_count:
        return None
    value = values[account_index]
    if type(value) is not int or value < 0:
        raise ValueError("invalid_lamport_endpoint")
    return value


def classify_native_close(
    transaction: dict[str, Any], diagnosis: dict[str, Any], target_path: list[int]
) -> dict[str, Any]:
    """Return observable facts and explicit blockers for native target accounts."""
    normalized = normalize_compiled_transaction(transaction)
    meta = transaction.get("meta") or {}
    facts = diagnosis.get("account_ledger", {}).get("account_facts", {})
    native_accounts = [
        account
        for account, fact in facts.items()
        if fact.get("mint") == "So11111111111111111111111111111111111111112"
        and fact.get("status") == "censored"
    ]
    if not native_accounts:
        raise ValueError("censored_row_without_native_account")
    all_instructions = [*normalized["outer_instructions"], *normalized["inner_instructions"]]
    ordered = sorted(all_instructions, key=lambda item: item["path"])
    parsed: list[dict[str, Any]] = []
    for instruction in ordered:
        effect = _system_effect(instruction)
        if effect is None and instruction["program_id"] == SYSTEM_PROGRAM:
            effect = {"kind": "unsupported_system", "path": instruction["path"], "tag": None}
        if effect is None:
            effect = _token_effect(instruction)
        if effect is not None:
            parsed.append(effect)
    account_rows = []
    all_blockers: Counter[str] = Counter()
    for account in sorted(native_accounts):
        account_index = next(
            i for i, row in enumerate(normalized["accounts"]) if row["pubkey"] == account
        )
        pre_lamports = _lamport_endpoint(
            transaction, account_index, "preBalances", len(normalized["accounts"])
        )
        post_lamports = _lamport_endpoint(
            transaction, account_index, "postBalances", len(normalized["accounts"])
        )
        pre_token = _token_endpoint(transaction, account_index, "preTokenBalances")
        post_token = _token_endpoint(transaction, account_index, "postTokenBalances")
        touches = [item for item in all_instructions if account in item["accounts"]]
        effects = [
            item
            for item in parsed
            if account in item.get("accounts", [])
            or account
            in (item.get("account"), item.get("source"), item.get("destination"), item.get("payer"))
        ]
        closes = [
            item
            for item in effects
            if item["kind"] == "close_account" and item["account"] == account
        ]
        syncs = [
            item for item in effects if item["kind"] == "sync_native" and item["account"] == account
        ]
        inits = [
            item
            for item in effects
            if item["kind"] == "initialize_account" and item["account"] == account
        ]
        creates = [
            item
            for item in effects
            if item["kind"] == "create_account" and item["account"] == account
        ]
        sys_transfers = [
            item
            for item in effects
            if item["kind"] == "transfer"
            and (item.get("source") == account or item.get("destination") == account)
        ]
        target_paths = [item["path"] for item in touches if _path_inside(item["path"], target_path)]
        sibling_paths = [
            item["path"] for item in touches if not _path_inside(item["path"], target_path)
        ]
        effect_by_path = {tuple(item["path"]): item for item in parsed}
        touching_trace = [
            {
                "path": item["path"],
                "program_id": item["program_id"],
                "accounts": item["accounts"],
                "data_hex": item["data_hex"],
                "decoded_effect": effect_by_path.get(
                    tuple(item["path"]), {"kind": "not_decoded_by_this_census"}
                ),
                "inside_sell_target_subtree": _path_inside(item["path"], target_path),
            }
            for item in touches
        ]
        system_lamport_net = 0
        system_flow_complete = True
        for effect in effects:
            if effect["kind"] == "create_account" and effect.get("account") == account:
                system_lamport_net += effect["lamports"]
            elif effect["kind"] == "transfer":
                if effect.get("destination") == account:
                    system_lamport_net += effect["lamports"]
                if effect.get("source") == account:
                    system_lamport_net -= effect["lamports"]
            elif effect["kind"] == "unsupported_system":
                system_flow_complete = False
        if pre_lamports is None:
            system_flow_complete = False
        explicit_close_lamports_candidate = (
            pre_lamports + system_lamport_net
            if system_flow_complete and len(closes) == 1 and len(syncs) == 1
            else None
        )
        blockers: list[str] = []
        if pre_lamports is None or post_lamports is None:
            blockers.append("lamport_endpoint_missing_or_misaligned")
        if pre_token is None and not inits:
            blockers.append("opening_token_state_missing_without_initialize")
        if post_token is None and not closes:
            blockers.append("closing_token_state_missing_without_close")
        if len(syncs) != 1:
            blockers.append("sync_native_count_not_one")
        if len(closes) != 1:
            blockers.append("close_account_count_not_one")
        if len(inits) > 1 or len(creates) > 1:
            blockers.append("multiple_account_opening_instructions")
        if any(e.get("kind") == "unsupported_system" for e in effects):
            blockers.append("unsupported_system_instruction_touches_native_account")
        if any(e.get("kind") == "unsupported_legacy_token" for e in effects):
            blockers.append("unsupported_legacy_token_instruction_touches_native_account")
        if not isinstance(meta.get("innerInstructions"), list):
            blockers.append("inner_instruction_trace_unavailable")
        if meta.get("err") is not None:
            blockers.append("transaction_failed")
        if closes and syncs and not (syncs[0]["path"] < closes[0]["path"]):
            blockers.append("sync_not_before_close")
        if closes and closes[0]["destination"] not in {r["pubkey"] for r in normalized["accounts"]}:
            blockers.append("close_destination_not_in_message")
        if any(
            path != target_path and not _path_inside(path, target_path) for path in sibling_paths
        ):
            all_blockers["sibling_account_touch_observed"] += 1
        for blocker in blockers:
            all_blockers[blocker] += 1

        close_destination = closes[0]["destination"] if len(closes) == 1 else None
        destination_index = next(
            (
                i
                for i, row in enumerate(normalized["accounts"])
                if row["pubkey"] == close_destination
            ),
            None,
        )
        destination_pre = (
            _lamport_endpoint(
                transaction, destination_index, "preBalances", len(normalized["accounts"])
            )
            if destination_index is not None
            else None
        )
        destination_post = (
            _lamport_endpoint(
                transaction, destination_index, "postBalances", len(normalized["accounts"])
            )
            if destination_index is not None
            else None
        )
        destination_touches = [
            item
            for item in all_instructions
            if close_destination and close_destination in item["accounts"]
        ]
        destination_trace = [
            {
                "path": item["path"],
                "program_id": item["program_id"],
                "accounts": item["accounts"],
                "data_hex": item["data_hex"],
                "decoded_effect": effect_by_path.get(
                    tuple(item["path"]), {"kind": "not_decoded_by_this_census"}
                ),
                "inside_sell_target_subtree": _path_inside(item["path"], target_path),
            }
            for item in destination_touches
        ]
        rent_sysvar_present = RENT_SYSVAR in {row["pubkey"] for row in normalized["accounts"]}
        account_rows.append(
            {
                "account": account,
                "message_account_index": account_index,
                "pre_lamports": pre_lamports,
                "post_lamports": post_lamports,
                "transaction_boundary_lamport_delta": (post_lamports - pre_lamports)
                if pre_lamports is not None and post_lamports is not None
                else None,
                "pre_token_endpoint": pre_token,
                "post_token_endpoint": post_token,
                "legacy_token_account_fact": facts[account],
                "system_create_effects": creates,
                "system_transfers_touching_account": sys_transfers,
                "explicit_system_lamport_net_lamports": system_lamport_net,
                "close_lamports_from_explicit_system_flow_candidate": (
                    explicit_close_lamports_candidate
                ),
                "explicit_lamport_flow_complete_under_observed_instructions": system_flow_complete,
                "sync_native_effects": syncs,
                "initialize_effects": inits,
                "close_effects": closes,
                "target_instruction_paths": target_paths,
                "sibling_instruction_paths": sibling_paths,
                "account_touching_instruction_trace": touching_trace,
                "close_destination": close_destination,
                "close_destination_pre_lamports": destination_pre,
                "close_destination_post_lamports": destination_post,
                "close_destination_boundary_delta_lamports": (destination_post - destination_pre)
                if destination_pre is not None and destination_post is not None
                else None,
                "close_destination_instruction_paths": [
                    item["path"] for item in destination_touches
                ],
                "close_destination_touching_instruction_trace": destination_trace,
                "rent_sysvar_in_message": rent_sysvar_present,
                "rent_reserve_lamports_observed": None,
                "rent_reserve_state": "not_directly_observed_in_transaction_capture",
                "blockers": sorted(set(blockers)),
            }
        )
    return {"native_accounts": account_rows, "blocker_counts": dict(sorted(all_blockers.items()))}


def is_sell23(
    transaction: dict[str, Any], sell_disc: bytes, disc: dict[bytes, str]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    normalized = normalize_compiled_transaction(transaction)
    calls = []
    for instruction in [*normalized["outer_instructions"], *normalized["inner_instructions"]]:
        if instruction["program_id"] != PROGRAM:
            continue
        raw = bytes.fromhex(instruction["data_hex"])
        kind = disc.get(raw[:8])
        if kind is not None:
            calls.append((instruction, kind, raw))
    if len(calls) != 1:
        return None
    instruction, kind, data = calls[0]
    if (
        kind != "sell"
        or data[:8] != sell_disc
        or len(data) != 24
        or len(instruction["accounts"]) != 23
        or instruction["accounts"][11] != TOKEN
        or instruction["accounts"][12] != TOKEN
    ):
        return None
    return normalized, instruction


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epoch-root", type=Path, default=ROOT)
    parser.add_argument("--baseline-receipt", type=Path, default=BASELINE_RECEIPT)
    parser.add_argument(
        "--output",
        type=Path,
        default=repo / "work/brain-senses-sell23-c3-native-close-feasibility.json",
    )
    args = parser.parse_args()
    baseline = json.loads(args.baseline_receipt.read_text())
    if baseline.get("eligible_successful_sell23_candidates") != 553 or baseline.get(
        "global_ledger_outcomes"
    ) != {
        "reconciled_diagnostic_only": 68,
        "reconciled_trade_with_censored_account_settlement": 485,
    }:
        raise RuntimeError("frozen_baseline_counts_mismatch")
    frozen_code_paths = [
        "src/memecoin_pumpswap/compiled_economics.py",
        "src/memecoin_pumpswap/economic_v2.py",
        "src/memecoin_pumpswap/legacy_token_ledger.py",
        "src/memecoin_pumpswap/normalize.py",
        "src/memecoin_pumpswap/decoder.py",
        "src/memecoin_pumpswap/schema.py",
        "tools/brain_senses_select_pumpswap_sell_sample.py",
        "tools/brain_senses_census_pumpswap_sell_ledger.py",
    ]
    for path in frozen_code_paths:
        if baseline.get("code_sha256", {}).get(path) != sha(repo / path):
            raise RuntimeError(f"frozen_qualifier_code_hash_mismatch:{path}")
    disc = {bytes(value["discriminator"]): name for name, value in SCHEMA["instructions"].items()}
    sell_disc = bytes(SCHEMA["instructions"]["sell"]["discriminator"])
    rows: list[dict[str, Any]] = []
    frames: dict[str, Any] = {}
    totals: Counter[str] = Counter()
    for frame_id in FRAME_IDS:
        d = args.epoch_root / frame_id
        plan = json.loads((d / "epoch-plan.json").read_text())
        summary = json.loads((d / "epoch-summary.json").read_text())
        if (
            plan.get("epoch_id") != frame_id
            or summary.get("epoch_id") != frame_id
            or summary.get("status") != "frame_complete"
        ):
            raise RuntimeError(f"frame_not_complete:{frame_id}")
        receipt_paths = sorted((d / "captures").glob("*/receipt.json"))
        frozen_frame = baseline.get("frames", {}).get(frame_id)
        if not isinstance(frozen_frame, dict):
            raise RuntimeError(f"baseline_frame_missing:{frame_id}")
        source_binding = {
            "plan_sha256": sha(d / "epoch-plan.json"),
            "summary_sha256": sha(d / "epoch-summary.json"),
            "frame_sha256": sha(d / "frame.json"),
            "stop_sha256": sha(d / "STOP"),
            "receipt_manifest_sha256": hashlib.sha256(
                "\n".join(f"{p.relative_to(repo)} {sha(p)}" for p in receipt_paths).encode()
            ).hexdigest(),
        }
        for key, value in source_binding.items():
            if frozen_frame.get(key) != value:
                raise RuntimeError(f"frozen_frame_hash_mismatch:{frame_id}:{key}")
        frame_counts: Counter[str] = Counter()
        matched_success: set[str] = set()
        failed_refs = 0
        for receipt_path in receipt_paths:
            receipt = json.loads(receipt_path.read_text())
            if receipt.get("method") != "getBlock":
                continue
            request = json.loads(receipt["request_json"])
            params = request.get("params")
            options = params[1] if isinstance(params, list) and len(params) > 1 else None
            if not isinstance(options, dict) or options.get("transactionDetails") != "full":
                continue
            slot = params[0]
            block = json.loads(_capture(receipt_path, receipt)).get("result")
            if not isinstance(block, dict) or not isinstance(block.get("transactions"), list):
                raise RuntimeError("full_block_invalid")
            frame_counts["full_blocks"] += 1
            for transaction_index, transaction in enumerate(block["transactions"]):
                candidate = is_sell23(transaction, sell_disc, disc)
                if candidate is None:
                    continue
                normalized, target = candidate
                signatures = normalized.get("transaction_signatures")
                if (
                    not isinstance(signatures, list)
                    or not signatures
                    or not isinstance(signatures[0], str)
                ):
                    raise RuntimeError("candidate_signature_missing")
                signature = signatures[0]
                if (transaction.get("meta") or {}).get("err") is not None:
                    failed_refs += 1
                    frame_counts["failed_exact_layout_refs"] += 1
                    continue
                if signature in matched_success:
                    raise RuntimeError("duplicate_successful_candidate")
                matched_success.add(signature)
                frame_counts["successful_exact_layout_refs"] += 1
                diagnosis = diagnose_pumpswap_sell_v2(transaction)
                status = diagnosis.get("account_settlement_state")
                frame_counts[f"ledger:{status}"] += 1
                if status != "censored_partial":
                    continue
                trace = classify_native_close(transaction, diagnosis, target["path"])
                for native in trace["native_accounts"]:
                    for blocker in native["blockers"]:
                        frame_counts[f"blocker:{blocker}"] += 1
                rows.append(
                    {
                        "frame_id": frame_id,
                        "slot": slot,
                        "transaction_index": transaction_index,
                        "signature": signature,
                        "signature_sha256": hashlib.sha256(signature.encode()).hexdigest(),
                        "target_instruction_path": target["path"],
                        "capture_receipt_path": str(receipt_path.relative_to(repo)),
                        "capture_receipt_sha256": sha(receipt_path),
                        "raw_response_sha256": receipt.get("response_sha256"),
                        "capture_receipt_ns": receipt.get("receipt_ns"),
                        "ledger_status": status,
                        "ledger_censored_reasons": diagnosis["account_ledger"].get(
                            "censored_reasons", {}
                        ),
                        "native_trace": trace,
                    }
                )
        frames[frame_id] = {
            "plan_sha256": sha(d / "epoch-plan.json"),
            "summary_sha256": sha(d / "epoch-summary.json"),
            "frame_sha256": sha(d / "frame.json"),
            "stop_sha256": sha(d / "STOP"),
            "receipt_manifest_sha256": hashlib.sha256(
                "\n".join(f"{p.relative_to(repo)} {sha(p)}" for p in receipt_paths).encode()
            ).hexdigest(),
            "receipt_count": len(receipt_paths),
            "counts": dict(sorted(frame_counts.items())),
        }
        totals.update(frame_counts)
        if (
            frame_counts["ledger:censored_partial"] + frame_counts["ledger:exact"]
            != frame_counts["successful_exact_layout_refs"]
        ):
            raise RuntimeError(f"unaccounted_success_rows:{frame_id}")
    if (
        len(rows) != 485
        or totals["ledger:exact"] != 68
        or totals["failed_exact_layout_refs"] != 2
        or totals["successful_exact_layout_refs"] != 553
        or totals["full_blocks"] != 96
    ):
        raise RuntimeError("frozen_candidate_denominators_mismatch")
    native_accounts = [
        account for row in rows for account in row["native_trace"]["native_accounts"]
    ]
    flow_candidates = [
        account["close_lamports_from_explicit_system_flow_candidate"]
        for account in native_accounts
        if account["close_lamports_from_explicit_system_flow_candidate"] is not None
    ]
    create_lamports = [
        effect["lamports"]
        for account in native_accounts
        for effect in account["system_create_effects"]
    ]
    input_transfer_lamports = [
        effect["lamports"]
        for account in native_accounts
        for effect in account["system_transfers_touching_account"]
        if effect.get("destination") == account["account"]
    ]
    if len(native_accounts) != 485 or len(flow_candidates) != 485:
        raise RuntimeError("native_account_flow_candidate_denominator_mismatch")
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "status": "complete_offline_feasibility_census",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip(),
        "offline_only": True,
        "provider_requests_made": 0,
        "paid_requests_made": 0,
        "c4_modified": False,
        "frames": frames,
        "counts": {
            "receipt_hash_verified_full_blocks": totals["full_blocks"],
            "successful_exact_layout_refs": totals["successful_exact_layout_refs"],
            "exact_ledger_rows_preserved": totals["ledger:exact"],
            "censored_rows": totals["ledger:censored_partial"],
            "failed_refs_outside_success_target": totals["failed_exact_layout_refs"],
            "native_target_account_rows": sum(
                len(row["native_trace"]["native_accounts"]) for row in rows
            ),
        },
        "observability_summary": {
            "native_target_accounts_with_pre_and_post_lamport_endpoints": sum(
                account["pre_lamports"] is not None and account["post_lamports"] is not None
                for account in native_accounts
            ),
            "native_target_accounts_with_zero_transaction_boundary_lamport_delta": sum(
                account["transaction_boundary_lamport_delta"] == 0 for account in native_accounts
            ),
            "accounts_with_one_legacy_token_system_create_sync_and_close": sum(
                len(account["system_create_effects"]) == 1
                and len(account["initialize_effects"]) == 1
                and len(account["sync_native_effects"]) == 1
                and len(account["close_effects"]) == 1
                for account in native_accounts
            ),
            "accounts_with_explicit_system_lamport_flow_candidates": len(flow_candidates),
            "native_accounts_with_pre_token_balance_endpoint": sum(
                account["pre_token_endpoint"] is not None for account in native_accounts
            ),
            "native_accounts_with_post_token_balance_endpoint": sum(
                account["post_token_endpoint"] is not None for account in native_accounts
            ),
            "close_destinations_with_pre_and_post_lamport_endpoints": sum(
                account["close_destination_pre_lamports"] is not None
                and account["close_destination_post_lamports"] is not None
                for account in native_accounts
            ),
            "rent_sysvar_present_in_transaction_message": sum(
                account["rent_sysvar_in_message"] for account in native_accounts
            ),
            "other_close_instructions_to_same_destination": sum(
                sum(
                    effect.get("kind") == "close_account"
                    and effect.get("destination") == account["close_destination"]
                    and effect.get("account") != account["account"]
                    for effect in account["close_destination_touching_instruction_trace"]
                )
                > 0
                for account in native_accounts
            ),
            "explicit_funding_ranges_lamports": {
                "system_create_min": min(create_lamports),
                "system_create_max": max(create_lamports),
                "system_transfer_into_native_min": min(input_transfer_lamports),
                "system_transfer_into_native_max": max(input_transfer_lamports),
                "close_lamports_candidate_min": min(flow_candidates),
                "close_lamports_candidate_max": max(flow_candidates),
            },
        },
        "baseline_replay_receipt": {
            "path": str(args.baseline_receipt.relative_to(repo)),
            "sha256": sha(args.baseline_receipt),
        },
        "frozen_qualifier_code_sha256": {path: sha(repo / path) for path in frozen_code_paths},
        "new_feasibility_tool_sha256": sha(Path(__file__)),
        "rent_semantics": {
            "units": "lamports",
            "reserve_value": None,
            "policy": (
                "No rent-exempt reserve is inferred from SyncNative residuals or present-day "
                "rent configuration. Each account reports whether its transaction capture "
                "exposes the rent sysvar and create-account funding."
            ),
        },
        "limitations": [
            "All results are feasibility observations over six completed selected-block frames.",
            "The two failed exact-layout references are counted separately and are not executions.",
            "No transaction is reclassified as economically exact; no native amount, wallet-net "
            "price, fee, or rent reserve is derived.",
            "Target/account-reference paths prove observation, not an account write.",
            "The captured provider trace and transaction-boundary balances do not prove global "
            "completeness or historical account state.",
        ],
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    args.output.chmod(0o600)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "rows": len(rows),
                "counts": report["counts"],
                "bytes": args.output.stat().st_size,
                "sha256": sha(args.output),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
