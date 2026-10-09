#!/usr/bin/env python3
"""Offline audit for a durable paired-provider finalized-block sample."""

from __future__ import annotations

import argparse
import hashlib
import platform
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from memecoin_ai.domain import canonical

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.brain_senses_block_provider_pilot import PROVIDERS, _compare, _read  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(root: Path) -> dict[str, Any]:
    plan = _read(root / "plan.json")
    providers = tuple(plan.get("providers", PROVIDERS))
    results = [_read(path) for path in sorted((root / "request-results").glob("*.json"))]
    comparison = _compare(root, plan, results)
    provider_rows: dict[str, list[dict[str, Any]]] = {
        provider: [row for row in results if row.get("provider") == provider]
        for provider in providers
    }

    provider_stats: dict[str, dict[str, Any]] = {}
    daily_bytes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for provider, rows in provider_rows.items():
        starts = [row["request_started_ns"] for row in rows if row.get("request_started_ns")]
        ends = [row["receipt_ns"] for row in rows if row.get("receipt_ns")]
        latencies = sorted(
            (row["receipt_ns"] - row["request_started_ns"]) / 1_000_000
            for row in rows
            if row.get("receipt_ns") and row.get("request_started_ns")
        )
        byte_count = sum(int(row.get("response_bytes") or 0) for row in rows)
        elapsed = (max(ends) - min(starts)) / 1_000_000_000 if starts and ends else None
        statuses = Counter(row.get("result_status") or row.get("status") for row in rows)
        methods = Counter(row.get("method") for row in rows)
        for row in rows:
            if row.get("receipt_ns"):
                day = (
                    datetime.fromtimestamp(row["receipt_ns"] / 1_000_000_000, UTC)
                    .date()
                    .isoformat()
                )
                daily_bytes[day][provider] += int(row.get("response_bytes") or 0)
        p95 = latencies[max(0, int(0.95 * (len(latencies) - 1)))] if latencies else None
        provider_stats[provider] = {
            "requests": len(rows),
            "methods": dict(sorted(methods.items())),
            "outcomes": dict(sorted(statuses.items())),
            "response_bytes": byte_count,
            "elapsed_seconds": elapsed,
            "effective_requests_per_second": len(rows) / elapsed if elapsed else None,
            "latency_ms": {
                "n": len(latencies),
                "median": latencies[len(latencies) // 2] if latencies else None,
                "p95": p95,
            },
        }

    slot_rows = [row for window in comparison["windows"] for row in window["slots_compared"]]
    paired_transactions = exact_transactions = differing_transactions = 0
    diff_paths: Counter[str] = Counter()
    blocks_by_provider: dict[str, dict[str, int]] = {
        provider: {
            "parsed_full_blocks": 0,
            "provider_null_full_blocks": 0,
            "unknown_full_blocks": 0,
            "pump_structural_references": 0,
            "pump_success_transaction_references": 0,
            "pump_failed_transaction_references": 0,
        }
        for provider in providers
    }
    inventory_qualification: dict[str, dict[str, int]] = {
        provider: {
            "exact_over_stored_full_response": 0,
            "provider_inventory_self_consistent": 0,
            "enumeration_contains_slot": 0,
            "exact_zero_with_self_consistent_inventory": 0,
            "unknown": 0,
        }
        for provider in providers
    }
    actual_hour_counts: Counter[str] = Counter()
    selected_block_times: dict[str, list[int]] = {provider: [] for provider in providers}
    for slot_row in slot_rows:
        transaction = slot_row.get("transaction_comparison", {})
        paired_transactions += int(transaction.get("paired", 0))
        exact_transactions += int(transaction.get("exact", 0))
        differing_transactions += int(transaction.get("differing", 0))
        diff_paths.update(transaction.get("difference_paths", {}))
        for provider in providers:
            item = slot_row["providers"][provider]
            if item.get("structural_reference_qualification") == "exact_over_stored_full_response":
                inventory_qualification[provider]["exact_over_stored_full_response"] += 1
            else:
                inventory_qualification[provider]["unknown"] += 1
            if item.get("provider_inventory_self_consistent") is True:
                inventory_qualification[provider]["provider_inventory_self_consistent"] += 1
                if item.get("program_references", {}).get("structural_reference_count") == 0:
                    inventory_qualification[provider][
                        "exact_zero_with_self_consistent_inventory"
                    ] += 1
            if item.get("enumeration_contains_slot") is True:
                inventory_qualification[provider]["enumeration_contains_slot"] += 1
            if item["full_status"] == "parsed":
                blocks_by_provider[provider]["parsed_full_blocks"] += 1
                refs = item.get("program_references")
                if refs:
                    for field, key in (
                        ("pump_structural_references", "structural_reference_count"),
                        (
                            "pump_success_transaction_references",
                            "references_in_successful_transactions",
                        ),
                        ("pump_failed_transaction_references", "references_in_failed_transactions"),
                    ):
                        blocks_by_provider[provider][field] += int(refs.get(key, 0))
            elif item["full_status"] == "provider_null":
                blocks_by_provider[provider]["provider_null_full_blocks"] += 1
            else:
                blocks_by_provider[provider]["unknown_full_blocks"] += 1
            block_time = item.get("block_time_seconds")
            if isinstance(block_time, int):
                selected_block_times[provider].append(block_time)
                hour = datetime.fromtimestamp(block_time, UTC).strftime("%Y-%m-%dT%H:00Z")
                actual_hour_counts[hour] += 1

    requests = [_read(path) for path in sorted((root / "requests").glob("*.json"))]
    stop_path = root / "STOP"
    stop = _read(stop_path) if stop_path.exists() else None
    try:
        disk_bytes = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    except OSError:
        disk_bytes = None
    code_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    sample_windows = (plan.get("sampling_metadata") or {}).get("windows") or []
    window_times: list[dict[str, Any]] = []
    for index, window in enumerate(comparison["windows"]):
        times = [
            item.get("block_time_seconds")
            for row in window["slots_compared"]
            for item in row["providers"].values()
            if isinstance(item.get("block_time_seconds"), int)
        ]
        sample_meta = sample_windows[index] if index < len(sample_windows) else None
        window_times.append(
            {
                "slot_range": window["slot_range"],
                "sampling_stratum": (
                    {
                        "day_index": sample_meta.get("day_index"),
                        "slot_stratum": sample_meta.get("slot_stratum"),
                    }
                    if isinstance(sample_meta, dict)
                    else None
                ),
                "observed_block_time_min": min(times) if times else None,
                "observed_block_time_max": max(times) if times else None,
            }
        )

    return {
        "schema_version": 1,
        "audit_mode": "offline_only_no_provider_requests",
        "audited_ns": time.time_ns(),
        "audit_code_commit": code_commit,
        "audit_code_sha256": _sha(Path(__file__)),
        "python_version": platform.python_version(),
        "bound_plan_sha256": _sha(root / "plan.json"),
        "bound_capture_code_commit": plan.get("code_commit"),
        "sample_precommit_sha256": hashlib.sha256(
            canonical(plan.get("sampling_metadata")).encode()
        ).hexdigest()
        if plan.get("sampling_metadata")
        else None,
        "planned_slot_ranges": len(plan["slot_ranges"]),
        "planned_integer_slots": sum(end - start for start, end in plan["slot_ranges"]),
        "completed_requests": len(results),
        "request_reservations": len(requests),
        "helius_expected_credit_reservations": sum(
            int(row.get("credit_reservation", 0)) for row in requests
        ),
        "helius_account_meter_available": False,
        "captured_response_bytes": {
            provider: provider_stats[provider]["response_bytes"] for provider in providers
        },
        "root_disk_bytes": disk_bytes,
        "providers": provider_stats,
        "captured_response_bytes_by_receipt_day": {
            day: dict(sorted(values.items())) for day, values in sorted(daily_bytes.items())
        },
        "block_comparison": {
            "windows": len(comparison["windows"]),
            "window_enumeration_equal": sum(
                window["enumeration_equal"] is True for window in comparison["windows"]
            ),
            "selected_integer_slots": len(slot_rows),
            "paired_block_identities": sum(
                row["cross_provider_block_identity_equal"] is True for row in slot_rows
            ),
            "paired_ordered_signature_lists": sum(
                row["cross_provider_signature_order_equal"] is True for row in slot_rows
            ),
            "signature_only_cross_checks_equal": sum(
                item["full_matches_signatures_only"] is True
                for row in slot_rows
                for item in row["providers"].values()
            ),
            "transaction_comparison": {
                "paired_transactions": paired_transactions,
                "exact_tree_matches": exact_transactions,
                "different_trees": differing_transactions,
                "difference_paths": dict(sorted(diff_paths.items())),
            },
            "blocks_by_provider": blocks_by_provider,
            "inventory_qualification": inventory_qualification,
            "observed_utc_hours": dict(sorted(actual_hour_counts.items())),
            "selected_block_time_bounds_utc": {
                provider: {
                    "min": min(values) if values else None,
                    "max": max(values) if values else None,
                }
                for provider, values in selected_block_times.items()
            },
            "window_time_audit": window_times,
            "full_window_detail": comparison["windows"],
        },
        "stop": stop,
        "scope": (
            "provider response agreement for the bound randomized selected-slot sample only; "
            "no assertion about unsampled slots, all provider data, or economic swap semantics"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.root)
    output = args.output or args.root / "analysis.json"
    output.write_text(canonical(result) + "\n")
    compact = {
        key: result[key]
        for key in (
            "bound_plan_sha256",
            "completed_requests",
            "helius_expected_credit_reservations",
            "captured_response_bytes",
            "root_disk_bytes",
            "scope",
        )
    }
    print(canonical(compact), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
