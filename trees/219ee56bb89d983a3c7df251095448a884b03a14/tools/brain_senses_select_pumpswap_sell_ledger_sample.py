#!/usr/bin/env python3
"""Preselect a fresh sell23 ledger sample excluding every prior v1 sample signature."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

from brain_senses_select_pumpswap_sell_sample import (  # noqa: E402
    _choose,
    _manifest,
    eligible_sell23,
)


def _read(path: Path) -> Any:
    return json.loads(path.read_text())


def select(
    capture_root: Path,
    paired_root: Path,
    excluded_manifests: list[Path],
    output_dir: Path,
    count: int,
) -> dict[str, Any]:
    population = eligible_sell23(capture_root)
    excluded_hashes: set[str] = set()
    exclusion_hashes: dict[str, str] = {}
    for path in excluded_manifests:
        manifest = _read(path)
        items = manifest.get("items")
        if not isinstance(items, list):
            raise ValueError("excluded_manifest_items_missing")
        excluded_hashes.update(item["signature_sha256"] for item in items)
        exclusion_hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    excluded_signatures = {
        row["signature"]
        for row in population
        if hashlib.sha256(row["signature"].encode()).hexdigest() in excluded_hashes
    }
    remaining = [row for row in population if row["signature"] not in excluded_signatures]
    if len(excluded_signatures) != len(excluded_hashes):
        raise ValueError("excluded_manifest_signatures_not_in_capture_population")
    if len(remaining) < count * 2:
        raise ValueError("remaining_eligible_population_too_small")

    paired_slots: set[int] = set()
    for receipt_path in paired_root.glob("*/receipt.json"):
        receipt = _read(receipt_path)
        if receipt.get("method") != "getBlock":
            continue
        params = json.loads(receipt["request_json"]).get("params")
        options = params[1] if isinstance(params, list) and len(params) > 1 else None
        if (
            isinstance(params, list)
            and params
            and type(params[0]) is int
            and isinstance(options, dict)
            and options.get("transactionDetails") == "full"
        ):
            paired_slots.add(params[0])

    seed_prefix = "pumpswap-sell23-full-transaction-ledger-v2-20261009"
    audit = _choose(remaining, f"{seed_prefix}-audit", set(), count)
    development = _choose(
        remaining,
        f"{seed_prefix}-development",
        {row["signature"] for row in audit},
        count,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    for name, rows, seed in (
        ("development", development, f"{seed_prefix}-development"),
        ("audit_sealed", audit, f"{seed_prefix}-audit"),
    ):
        target = output_dir / f"{name}.json"
        target.write_text(
            json.dumps(
                _manifest(name, rows, seed, len(remaining), paired_slots),
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        target.chmod(0o600)
    summary = {
        "schema_version": 1,
        "experiment": "pumpswap_sell23_full_transaction_ledger_v2_sample_selection",
        "status": "preselected_before_decode",
        "eligible_population_original": len(population),
        "prior_signature_exclusions": len(excluded_signatures),
        "eligible_population_remaining": len(remaining),
        "excluded_manifest_sha256": exclusion_hashes,
        "development_manifest_sha256": hashlib.sha256(
            (output_dir / "development.json").read_bytes()
        ).hexdigest(),
        "audit_manifest_sha256": hashlib.sha256(
            (output_dir / "audit_sealed.json").read_bytes()
        ).hexdigest(),
        "development_unique_pools": len({row["pool"] for row in development}),
        "audit_unique_pools": len({row["pool"] for row in audit}),
        "development_slot_bins_250": dict(Counter(row["slot"] // 250 for row in development)),
        "audit_slot_bins_250": dict(Counter(row["slot"] // 250 for row in audit)),
    }
    (output_dir / "selection.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    (output_dir / "selection.json").chmod(0o600)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--paired-root", type=Path, required=True)
    parser.add_argument("--exclude-manifest", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=50)
    args = parser.parse_args()
    try:
        summary = select(
            args.capture_root,
            args.paired_root,
            args.exclude_manifest,
            args.output_dir,
            args.count,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"selection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
