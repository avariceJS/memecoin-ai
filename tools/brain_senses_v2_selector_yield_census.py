#!/usr/bin/env python3
"""Offline selector integrity and sampled structural yield for three sealed v2 frames."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import statistics
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

from tools.brain_senses_block_epoch_v2 import (  # noqa: E402
    _build_selection,
    _capture_result,
    _parse_inventory,
    _signature_list,
    _structural_response_validation,
    _validate_journal,
)
from tools.brain_senses_block_provider_pilot import _pump_invocations  # noqa: E402
from tools.brain_senses_select_pumpswap_sell_sample import eligible_sell23  # noqa: E402

CONTRACT = "helius_inventory_v2_three_frame_selector_yield_census_v1"
CAMPAIGN_ROOT = REPO_ROOT / ("work/brain-senses-live-helius-inventory-v2-16-hourly-20261010")
FRAMES = (
    {
        "epoch_id": "20261009T230000Z",
        "receipt": "helius-inventory-v2-first-frame-2026-10-09.json",
    },
    {
        "epoch_id": "20261010T010000Z",
        "receipt": "helius-inventory-v2-frame-2026-10-10T010000Z.json",
    },
    {
        "epoch_id": "20261010T020000Z",
        "receipt": "helius-inventory-v2-final-2026-10-10.json",
    },
)
GAP_RECEIPT = REPO_ROOT / "docs/evidence/helius-inventory-v2-oversized-gap-2026-10-10.json"
CODE_PATHS = (
    "tools/brain_senses_v2_selector_yield_census.py",
    "tools/brain_senses_block_epoch_v2.py",
    "tools/brain_senses_block_provider_pilot.py",
    "tools/brain_senses_select_pumpswap_sell_sample.py",
    "src/memecoin_pumpswap/normalize.py",
    "src/memecoin_pumpswap/schema.py",
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("expected_object")
    return value


def _capture_receipt(epoch_root: Path, sequence: int) -> dict[str, Any]:
    return _read(epoch_root / "captures" / f"{sequence:012d}" / "receipt.json")


def _selected_block_rows(
    epoch_root: Path, plan: dict[str, Any], summary: dict[str, Any]
) -> list[dict[str, Any]]:
    journal = _validate_journal(epoch_root, plan)
    if len(journal) != 34 or len(list((epoch_root / "requests").glob("*.json"))) != 34:
        raise ValueError("frame_request_journal_not_34_of_34")
    checks = {row["slot"]: row for row in summary.get("structural_checks", [])}
    if len(checks) != 16:
        raise ValueError("frame_structural_check_count_invalid")
    rows: list[dict[str, Any]] = []
    for slot in summary["selected_slots"]:
        full = next(
            (r for r in journal if r.get("phase") == "full_block" and r.get("slot") == slot), None
        )
        signatures = next(
            (r for r in journal if r.get("phase") == "signature_check" and r.get("slot") == slot),
            None,
        )
        if full is None or signatures is None:
            raise ValueError("selected_slot_pair_missing")
        block = _capture_result(epoch_root, full)
        signature_block = _capture_result(epoch_root, signatures)
        failures = _structural_response_validation(block, slot)
        structural = _pump_invocations(block) if isinstance(block, dict) and not failures else None
        full_signatures = _signature_list(block, "full")
        signature_order = _signature_list(signature_block, "signatures")
        identity_equal = bool(
            isinstance(block, dict)
            and isinstance(signature_block, dict)
            and block.get("blockhash") == signature_block.get("blockhash")
            and block.get("parentSlot") == signature_block.get("parentSlot")
            and block.get("previousBlockhash") == signature_block.get("previousBlockhash")
        )
        expected_check = checks[slot]
        passed = (
            structural is not None
            and structural.get("unresolved_instruction_shapes") == 0
            and not structural.get("compiled_transaction_normalization_failures")
            and full.get("status") == "captured"
            and full.get("result_status") == "parsed"
            and signatures.get("status") == "captured"
            and signatures.get("result_status") == "parsed"
            and full_signatures is not None
            and full_signatures == signature_order
            and identity_equal
            and expected_check.get("status") == "qualified_structural_reference_count"
            and expected_check.get("structural_references") == structural
            and expected_check.get("block_identity_equal") is True
            and expected_check.get("signature_order_equal") is True
        )
        if not passed:
            raise ValueError("selected_block_replay_differs_from_sealed_summary")
        transactions = block["transactions"]
        failed_txs = 0
        for entry in transactions:
            result = _pump_invocations({"transactions": [entry]})
            if (
                result["transactions_with_reference"]
                and (entry.get("meta") or {}).get("err") is not None
            ):
                failed_txs += 1
        full_receipt = _capture_receipt(epoch_root, int(full["sequence"]))
        signature_receipt = _capture_receipt(epoch_root, int(signatures["sequence"]))
        block_time = block.get("blockTime")
        if type(block_time) is not int:
            raise ValueError("selected_block_native_time_missing")
        rows.append(
            {
                "slot": slot,
                "event_time_unix_seconds": block_time,
                "full_availability_ns": full_receipt["received_ns"],
                "signature_availability_ns": signature_receipt["received_ns"],
                "full_request_started_ns": full_receipt["request_started_ns"],
                "signature_request_started_ns": signature_receipt["request_started_ns"],
                "full_request_to_receipt_latency_ns": (
                    full_receipt["received_ns"] - full_receipt["request_started_ns"]
                ),
                "signature_request_to_receipt_latency_ns": (
                    signature_receipt["received_ns"] - signature_receipt["request_started_ns"]
                ),
                "full_capture_response_sha256": full_receipt["response_sha256"],
                "full_capture_stored_sha256": full_receipt["storage"]["sha256"],
                "signature_capture_response_sha256": signature_receipt["response_sha256"],
                "signature_capture_stored_sha256": signature_receipt["storage"]["sha256"],
                "full_raw_bytes": full_receipt["response_bytes"],
                "full_stored_bytes": full_receipt["storage"]["bytes"],
                "signature_raw_bytes": signature_receipt["response_bytes"],
                "signature_stored_bytes": signature_receipt["storage"]["bytes"],
                "transaction_count": len(transactions),
                "unique_failed_transactions_with_reference": failed_txs,
                "structural_references": structural["structural_reference_count"],
                "transactions_with_reference": structural["transactions_with_reference"],
                "references_in_failed_transactions": structural[
                    "references_in_failed_transactions"
                ],
            }
        )
    return rows


def _candidate_pool_counts(capture_root: Path, selected_slots: set[int]) -> Counter[str]:
    result: Counter[str] = Counter()
    signatures: set[str] = set()
    for row in eligible_sell23(capture_root):
        if row["slot"] not in selected_slots:
            continue
        if row["signature"] in signatures:
            raise ValueError("duplicate_selected_sell23_signature")
        signatures.add(row["signature"])
        result[row["pool"]] += 1
    return result


def _quarter_counts(start: int, end_exclusive: int, slots: set[int]) -> dict[str, int]:
    width = end_exclusive - start
    if width <= 0 or any(
        type(slot) is not int or not start <= slot < end_exclusive for slot in slots
    ):
        raise ValueError("selected_slot_outside_numeric_frame")
    counts = Counter(min(3, 4 * (slot - start) // width) for slot in slots)
    return {str(index + 1): counts[index] for index in range(4)}


def analyze(output_path: Path) -> dict[str, Any]:
    if output_path.exists():
        raise ValueError("output_exists_refusing_overwrite")
    zstd_path = Path("/opt/homebrew/bin/zstd")
    if not zstd_path.is_file() or shutil.which("zstd") != str(zstd_path):
        raise ValueError("zstd_executable_path_mismatch")
    source_frames: list[dict[str, Any]] = []
    all_blocks: list[dict[str, Any]] = []
    all_candidates: Counter[str] = Counter()
    for config in FRAMES:
        epoch_root = CAMPAIGN_ROOT / "epochs" / config["epoch_id"]
        plan_path = epoch_root / "epoch-plan.json"
        selection_path = epoch_root / "selection-plan.json"
        frame_path = epoch_root / "frame.json"
        summary_path = epoch_root / "epoch-summary.json"
        plan, selection, frame, summary = map(
            _read, (plan_path, selection_path, frame_path, summary_path)
        )
        receipt_path = REPO_ROOT / "docs/evidence" / config["receipt"]
        receipt = _read(receipt_path)
        final_epoch = next(
            (
                row
                for row in receipt.get("epoch_evidence", [])
                if row.get("bucket") == config["epoch_id"]
            ),
            None,
        )
        direct_status = receipt.get("status") or receipt.get("acquisition", {}).get("status")
        receipt_frame_status = (
            direct_status == "frame_complete"
            if receipt.get("epoch_id") == config["epoch_id"]
            else final_epoch is not None and final_epoch.get("status") == "frame_complete"
        )
        direct_artifacts = receipt.get("epoch_artifacts") or receipt.get("artifacts") or {}
        direct_inventory_hash = direct_artifacts.get("inventory_sha256") or receipt.get(
            "interval", {}
        ).get("inventory_sha256")
        if (
            not receipt_frame_status
            or summary.get("status") != "frame_complete"
            or frame.get("status") != "captured"
            or summary.get("epoch_id") != config["epoch_id"]
            or plan.get("epoch_id") != config["epoch_id"]
            or plan.get("provider") != "helius-free"
            or frame.get("start") != summary.get("start")
            or frame.get("end_exclusive") != summary.get("end_exclusive")
            or plan.get("start") != summary.get("start")
            or selection.get("epoch_id") != config["epoch_id"]
            or summary.get("selected_slots") != selection.get("selected_slots")
            or summary.get("inclusion_probability") != selection.get("inclusion_probability")
        ):
            raise ValueError("sealed_frame_identity_or_status_invalid")
        epoch_manifest = _read(epoch_root / "selection-plan.json")
        inventory_rows = _validate_journal(epoch_root, plan)
        inventory_result = next((r for r in inventory_rows if r.get("phase") == "inventory"), None)
        if inventory_result is None:
            raise ValueError("inventory_journal_result_missing")
        inventory = _parse_inventory(
            inventory_result,
            summary["start"],
            summary["end_exclusive"],
        )
        raw_inventory = _capture_result(epoch_root, inventory_result)
        if inventory is None or raw_inventory != inventory_result.get("inventory_slots"):
            raise ValueError("inventory_membership_not_replayable")
        inventory_hash = inventory_result.get("response_sha256")
        recomputed = _build_selection(
            plan=plan, inventory_slots=inventory, inventory_sha256=inventory_hash
        )
        if recomputed != epoch_manifest or recomputed != selection:
            raise ValueError("selector_replay_differs_from_frozen_selection")
        if (
            summary.get("inventory_sha256") != inventory_hash
            or summary.get("inventory_denominator") != len(inventory)
            or (
                direct_inventory_hash
                if receipt.get("epoch_id") == config["epoch_id"]
                else receipt.get("last_inventoried_interval", {}).get("inventory_sha256")
            )
            != inventory_hash
            or (
                direct_artifacts.get("plan_sha256")
                if receipt.get("epoch_id") == config["epoch_id"]
                else final_epoch.get("plan_sha256")
            )
            != _sha(plan_path.read_bytes())
            or (
                direct_artifacts.get("summary_sha256")
                if receipt.get("epoch_id") == config["epoch_id"]
                else final_epoch.get("summary_sha256")
            )
            != _sha(summary_path.read_bytes())
        ):
            raise ValueError("frame_receipt_source_binding_mismatch")
        selected_rows = _selected_block_rows(epoch_root, plan, summary)
        receipt_sampling = receipt.get("coverage") or receipt.get("sampling")
        if isinstance(receipt_sampling, dict):
            replay_counts = {
                "structural_instruction_references": sum(
                    row["structural_references"] for row in selected_rows
                ),
                "transactions_with_reference": sum(
                    row["transactions_with_reference"] for row in selected_rows
                ),
                "references_in_failed_transactions": sum(
                    row["references_in_failed_transactions"] for row in selected_rows
                ),
            }
            if any(receipt_sampling.get(key) != value for key, value in replay_counts.items()):
                raise ValueError("selected_structural_yield_differs_from_frame_receipt")
        selected_slots = {row["slot"] for row in selected_rows}
        candidate_pools = _candidate_pool_counts(epoch_root / "captures", selected_slots)
        width = summary["end_exclusive"] - summary["start"]
        slots = sorted(selected_slots)
        event_times = sorted(row["event_time_unix_seconds"] for row in selected_rows)
        availability = [
            ns
            for row in selected_rows
            for ns in (row["full_availability_ns"], row["signature_availability_ns"])
        ]
        bytes_raw = sum(row["full_raw_bytes"] + row["signature_raw_bytes"] for row in selected_rows)
        bytes_stored = sum(
            row["full_stored_bytes"] + row["signature_stored_bytes"] for row in selected_rows
        )
        frame_out = {
            "epoch_id": config["epoch_id"],
            "receipt_path": str(receipt_path.relative_to(REPO_ROOT)),
            "receipt_sha256": _sha(receipt_path.read_bytes()),
            "plan_sha256": _sha(plan_path.read_bytes()),
            "selection_plan_sha256": _sha(selection_path.read_bytes()),
            "frame_sha256": _sha(frame_path.read_bytes()),
            "summary_sha256": _sha(summary_path.read_bytes()),
            "inventory_response_sha256": inventory_hash,
            "inventory_denominator_returned_members": len(inventory),
            "numeric_interval": [summary["start"], summary["end_exclusive"]],
            "numeric_width": width,
            "numeric_positions_absent_from_provider_inventory": width - len(inventory),
            "selection_count": len(selected_rows),
            "selected_slots": slots,
            "selection_quarter_counts": _quarter_counts(
                summary["start"], summary["end_exclusive"], selected_slots
            ),
            "selected_slot_span": slots[-1] - slots[0],
            "selected_block_time_span_seconds": event_times[-1] - event_times[0],
            "selected_event_time_min_unix_seconds": event_times[0],
            "selected_event_time_max_unix_seconds": event_times[-1],
            "selected_availability_first_ns": min(availability),
            "selected_availability_last_ns": max(availability),
            "selected_block_availability_span_ns": max(availability) - min(availability),
            "median_full_request_to_receipt_latency_ns": int(
                statistics.median(
                    row["full_request_to_receipt_latency_ns"] for row in selected_rows
                )
            ),
            "median_signature_request_to_receipt_latency_ns": int(
                statistics.median(
                    row["signature_request_to_receipt_latency_ns"] for row in selected_rows
                )
            ),
            "selected_full_and_signature_raw_bytes": bytes_raw,
            "selected_full_and_signature_stored_bytes": bytes_stored,
            "selected_structural_references": sum(
                row["structural_references"] for row in selected_rows
            ),
            "selected_unique_transactions_with_reference": sum(
                row["transactions_with_reference"] for row in selected_rows
            ),
            "selected_unique_failed_transactions_with_reference": sum(
                row["unique_failed_transactions_with_reference"] for row in selected_rows
            ),
            "selected_reference_occurrences_in_failed_transactions": sum(
                row["references_in_failed_transactions"] for row in selected_rows
            ),
            "selected_sell23_legacy_candidates": sum(candidate_pools.values()),
            "selected_sell23_unique_candidate_pools": len(candidate_pools),
            "selected_sell23_top_two_pools": candidate_pools.most_common(2),
            "selected_sell23_top_two_candidate_fraction": (
                sum(value for _, value in candidate_pools.most_common(2))
                / sum(candidate_pools.values())
                if candidate_pools
                else None
            ),
            "selected_block_yields": selected_rows,
            "selected_yield_matches_frame_receipt": (isinstance(receipt_sampling, dict)),
            "source_receipt_raw_bytes": summary["captured_response_bytes"],
            "source_receipt_stored_bytes": summary["stored_response_bytes"],
            "source_frame_requests": summary["request_reservations"],
        }
        source_frames.append(frame_out)
        all_blocks.extend({**row, "epoch_id": config["epoch_id"]} for row in selected_rows)
        all_candidates.update(candidate_pools)

    gap_receipt = _read(GAP_RECEIPT)
    if (
        gap_receipt.get("status") != "oversized_frame"
        or gap_receipt.get("request_accounting", {}).get("no_inventory_or_block_requests")
        is not True
        or gap_receipt.get("request_accounting", {}).get("no_retries") is not True
    ):
        raise ValueError("oversized_gap_receipt_not_explicit")
    gap = {
        "receipt_path": str(GAP_RECEIPT.relative_to(REPO_ROOT)),
        "receipt_sha256": _sha(GAP_RECEIPT.read_bytes()),
        "interval": [gap_receipt["previous_frontier"], gap_receipt["range_end_exclusive"]],
        "reason": gap_receipt["reason"],
        "inventory_requests": 0,
        "selected_block_requests": 0,
        "replayed": False,
    }
    bootstrap_root = CAMPAIGN_ROOT / "epochs" / "20261009T220000Z"
    gap_root = CAMPAIGN_ROOT / "epochs" / "20261010T000000Z"
    bootstrap_summary = _read(bootstrap_root / "epoch-summary.json")
    gap_summary = _read(gap_root / "epoch-summary.json")
    campaign_closure_path = REPO_ROOT / "docs/evidence/helius-inventory-v2-final-2026-10-10.json"
    campaign_closure = _read(campaign_closure_path)
    campaign_usage = campaign_closure.get("usage", {})
    excluded_raw = (
        bootstrap_summary["captured_response_bytes"] + gap_summary["captured_response_bytes"]
    )
    excluded_stored = (
        bootstrap_summary["stored_response_bytes"] + gap_summary["stored_response_bytes"]
    )
    excluded_requests = (
        bootstrap_summary["request_reservations"] + gap_summary["request_reservations"]
    )
    total_raw = sum(row["source_receipt_raw_bytes"] for row in source_frames)
    total_stored = sum(row["source_receipt_stored_bytes"] for row in source_frames)
    if (
        campaign_usage.get("requests") != 102 + excluded_requests
        or campaign_usage.get("raw_bytes") != total_raw + excluded_raw
        or campaign_usage.get("stored_bytes") != total_stored + excluded_stored
    ):
        raise ValueError("frame_bytes_do_not_reconcile_to_campaign_closure")
    total_members = sum(row["inventory_denominator_returned_members"] for row in source_frames)
    selected_blocks = len(all_blocks)
    event_times = sorted(row["event_time_unix_seconds"] for row in all_blocks)
    availability = [
        ns
        for row in all_blocks
        for ns in (row["full_availability_ns"], row["signature_availability_ns"])
    ]
    code_hashes = {path: _sha((REPO_ROOT / path).read_bytes()) for path in CODE_PATHS}
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    payload = {
        "contract": CONTRACT,
        "schema_version": 1,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "claim": (
            "offline replay of selector integrity and observed yield in three sealed Helius v2 "
            "frames"
        ),
        "git_commit": commit,
        "tool_sha256": code_hashes[CODE_PATHS[0]],
        "code_sha256": code_hashes,
        "compression_tool": {
            "path": str(zstd_path),
            "sha256": _sha(zstd_path.read_bytes()),
            "version": subprocess.run(
                [str(zstd_path), "--version"], check=True, capture_output=True, text=True
            ).stdout.strip(),
        },
        "network_requests": 0,
        "audit_sample_accessed": False,
        "population_scope": {
            "provider": "Helius; provider-returned finalized getBlocks inventory members",
            "frame_count": len(source_frames),
            "inventory_returned_members": total_members,
            "selected_block_count": selected_blocks,
            "slot_sampling_fraction": selected_blocks / total_members,
            "inventory_membership_is_not_global_completeness": True,
            "numeric_positions_absent_from_inventory": sum(
                row["numeric_positions_absent_from_provider_inventory"] for row in source_frames
            ),
        },
        "sealed_oversized_gap": gap,
        "campaign_closure_reconciliation": {
            "receipt_path": str(campaign_closure_path.relative_to(REPO_ROOT)),
            "receipt_sha256": _sha(campaign_closure_path.read_bytes()),
            "three_complete_frame_requests": 102,
            "bootstrap_and_gap_requests": excluded_requests,
            "bootstrap_and_gap_raw_bytes": excluded_raw,
            "bootstrap_and_gap_stored_bytes": excluded_stored,
            "campaign_request_total": campaign_usage["requests"],
            "campaign_raw_byte_total": campaign_usage["raw_bytes"],
            "campaign_stored_byte_total": campaign_usage["stored_bytes"],
            "status": "exactly_reconciled",
        },
        "frames": source_frames,
        "aggregate_selected_yield": {
            "raw_response_bytes_all_three_frames": total_raw,
            "stored_zstd_bytes_all_three_frames": total_stored,
            "selected_full_and_signature_raw_bytes": sum(
                row["selected_full_and_signature_raw_bytes"] for row in source_frames
            ),
            "selected_full_and_signature_stored_bytes": sum(
                row["selected_full_and_signature_stored_bytes"] for row in source_frames
            ),
            "selected_structural_references": sum(
                row["selected_structural_references"] for row in source_frames
            ),
            "selected_unique_transactions_with_reference": sum(
                row["selected_unique_transactions_with_reference"] for row in source_frames
            ),
            "selected_unique_failed_transactions_with_reference": sum(
                row["selected_unique_failed_transactions_with_reference"] for row in source_frames
            ),
            "selected_reference_occurrences_in_failed_transactions": sum(
                row["selected_reference_occurrences_in_failed_transactions"]
                for row in source_frames
            ),
            "selected_sell23_legacy_candidates": sum(all_candidates.values()),
            "selected_sell23_unique_candidate_pools": len(all_candidates),
            "selected_sell23_top_two_pools": all_candidates.most_common(2),
            "selected_sell23_top_two_candidate_fraction": (
                sum(value for _, value in all_candidates.most_common(2))
                / sum(all_candidates.values())
                if all_candidates
                else None
            ),
            "selected_event_time_min_unix_seconds": event_times[0],
            "selected_event_time_max_unix_seconds": event_times[-1],
            "selected_event_time_span_seconds": event_times[-1] - event_times[0],
            "selected_availability_first_ns": min(availability),
            "selected_availability_last_ns": max(availability),
            "selected_receipt_span_ns": max(availability) - min(availability),
            "median_adjacent_selected_block_time_gap_seconds": statistics.median(
                b - a for a, b in zip(event_times, event_times[1:], strict=False)
            ),
        },
        "interpretation": {
            "selector_integrity": "reproduced_exactly_for_all_three_sealed_frames",
            "selected_full_and_signature_pairs": "48/48 independently replayed and summary-matched",
            "sampling_design": "uniform_without_replacement_from_each_provider_returned_inventory",
            "utility": (
                "bounded_per-frame structural sentinel with measured yield and pool concentration"
            ),
            "representativeness": "not_established; 48 blocks are a sparse provider-scoped sample",
            "unselected_block_activity": "unknown; no extrapolation or zero inference",
            "economic_swaps_or_wallet_price": "not_assessed",
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=REPO_ROOT / "work/v2-selector-yield-census.json"
    )
    args = parser.parse_args(argv)
    result = analyze(args.output)
    print(
        json.dumps(
            {
                "contract": result["contract"],
                "population_scope": result["population_scope"],
                "aggregate_selected_yield": result["aggregate_selected_yield"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
