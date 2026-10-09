#!/usr/bin/env python3
"""Precommit a deterministic, broad finalized-slot sample for Helius/public comparison."""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

from memecoin_ai.domain import canonical, digest

UTC_DAY_SECONDS = 86_400
STRATA_PER_DAY = 24
WINDOWS_PER_STRATUM = 2
SLOTS_PER_WINDOW = 20
MAX_DAYS = 3
MAX_WINDOWS = MAX_DAYS * STRATA_PER_DAY * WINDOWS_PER_STRATUM


def build_sample(
    *,
    end_slot: int,
    end_block_time: int,
    slots_per_day: int,
    seed: str,
    days: int = MAX_DAYS,
) -> dict[str, Any]:
    if end_slot < 1 or end_block_time < 1 or slots_per_day < 24 * 2 * SLOTS_PER_WINDOW:
        raise ValueError("invalid_finalized_slot_frame")
    if not 1 <= days <= MAX_DAYS:
        raise ValueError("day_count_outside_bound")
    if not seed or len(seed) > 256:
        raise ValueError("seed_outside_bound")
    first_slot = end_slot - days * slots_per_day
    if first_slot < 1:
        raise ValueError("slot_frame_before_genesis")

    rng = random.Random(seed)
    windows: list[dict[str, Any]] = []
    for day_index in range(days):
        day_start = first_slot + day_index * slots_per_day
        for stratum in range(STRATA_PER_DAY):
            lower = day_start + stratum * slots_per_day // STRATA_PER_DAY
            upper = day_start + (stratum + 1) * slots_per_day // STRATA_PER_DAY
            starts: list[int] = []
            attempts = 0
            while len(starts) < WINDOWS_PER_STRATUM:
                attempts += 1
                if attempts > 10_000:
                    raise ValueError("unable_to_draw_nonoverlapping_windows")
                candidate = rng.randint(lower, upper - SLOTS_PER_WINDOW)
                if all(
                    candidate + SLOTS_PER_WINDOW <= start or start + SLOTS_PER_WINDOW <= candidate
                    for start in starts
                ):
                    starts.append(candidate)
            for local_window, start in enumerate(sorted(starts)):
                windows.append(
                    {
                        "day_index": day_index,
                        "slot_stratum": stratum,
                        "window_in_stratum": local_window,
                        "slot_range": [start, start + SLOTS_PER_WINDOW],
                        "selection": "uniform_seeded_start_within_equal_slot_stratum",
                    }
                )

    windows.sort(key=lambda row: row["slot_range"])
    if len(windows) > MAX_WINDOWS:
        raise ValueError("sample_window_count_outside_bound")
    ranges = [row["slot_range"] for row in windows]
    for left, right in zip(ranges, ranges[1:], strict=False):
        if left[1] > right[0]:
            raise ValueError("sample_windows_overlap")
    return {
        "schema_version": 1,
        "created_ns": time.time_ns(),
        "seed": seed,
        "target_days": days,
        "frame": {
            "commitment": "finalized",
            "end_slot": end_slot,
            "end_block_time_utc_seconds": end_block_time,
            "end_slot_receipt_semantics": (
                "selected from previously captured finalized public block"
            ),
            "slots_per_day_estimate": slots_per_day,
            "estimated_start_slot": first_slot,
            "estimated_start_block_time_utc_seconds": end_block_time - days * UTC_DAY_SECONDS,
            "utc_strata_are_approximate": True,
            "stratum_definition": (
                "24 equal slot-number strata per estimated UTC day; observed blockTime will be "
                "audited after capture"
            ),
            "sampling_frame": (
                "integer finalized-slot range; no provider signature page is used as the "
                "sampling frame"
            ),
        },
        "design": {
            "strata_per_day": STRATA_PER_DAY,
            "random_windows_per_stratum": WINDOWS_PER_STRATUM,
            "slots_per_window": SLOTS_PER_WINDOW,
            "selected_slots": days * STRATA_PER_DAY * WINDOWS_PER_STRATUM * SLOTS_PER_WINDOW,
            "selected_windows": len(windows),
            "diagnostic_windows": 0,
            "providers": ["helius-free", "solana-public"],
            "methods_per_selected_slot": [
                "getBlock transactionDetails=full",
                "getBlock transactionDetails=signatures",
            ],
            "enumeration": "getBlocks over each sampled window at both providers",
            "estimator_scope": (
                "random windows only; source agreement over the selected finalized-slot range, "
                "never unsampled slot completeness"
            ),
        },
        "windows": windows,
        "slot_ranges": ranges,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end-slot", type=int, required=True)
    parser.add_argument("--end-block-time", type=int, required=True)
    parser.add_argument("--slots-per-day", type=int, required=True)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--days", type=int, default=MAX_DAYS)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    sample = build_sample(
        end_slot=args.end_slot,
        end_block_time=args.end_block_time,
        slots_per_day=args.slots_per_day,
        seed=args.seed,
        days=args.days,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    body = canonical(sample).encode() + b"\n"
    args.output.write_bytes(body)
    print(
        json.dumps(
            {
                "sampling_plan_sha256": digest(body),
                "selected_windows": sample["design"]["selected_windows"],
                "selected_slots": sample["design"]["selected_slots"],
                "slot_ranges": len(sample["slot_ranges"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
