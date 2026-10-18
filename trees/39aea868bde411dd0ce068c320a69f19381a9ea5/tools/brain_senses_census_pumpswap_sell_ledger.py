#!/usr/bin/env python3
"""Offline census of the frozen sell23 ledger diagnostic over eligible captures."""

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

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))

from brain_senses_select_pumpswap_sell_sample import eligible_sell23  # noqa: E402

from memecoin_pumpswap.economic_v2 import diagnose_pumpswap_sell_v2  # noqa: E402


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _capture(receipt_path: Path, receipt: dict[str, Any]) -> bytes:
    storage = receipt.get("storage")
    if not isinstance(storage, dict) or not isinstance(storage.get("path"), str):
        raise ValueError("capture_storage_path_missing")
    payload = receipt_path.parent / storage["path"]
    encoded = payload.read_bytes()
    if hashlib.sha256(encoded).hexdigest() != storage.get("sha256"):
        raise ValueError("stored_capture_hash_mismatch")
    raw = (
        subprocess.run(
            ["zstd", "-q", "-d", "-c", str(payload)], check=True, capture_output=True
        ).stdout
        if payload.suffix == ".zst"
        else encoded
    )
    if hashlib.sha256(raw).hexdigest() != receipt.get("response_sha256"):
        raise ValueError("raw_capture_hash_mismatch")
    if len(raw) != receipt.get("response_bytes"):
        raise ValueError("raw_capture_size_mismatch")
    return raw


def census(capture_root: Path) -> dict[str, Any]:
    candidates = eligible_sell23(capture_root)
    wanted = {hashlib.sha256(row["signature"].encode()).hexdigest() for row in candidates}
    if len(wanted) != len(candidates):
        raise ValueError("eligible_signature_duplicate")
    outcomes: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    ledger_statuses: Counter[str] = Counter()
    ledger_unknown_reasons: Counter[str] = Counter()
    ledger_censored_reasons: Counter[str] = Counter()
    equations: Counter[str] = Counter()
    matched: set[str] = set()
    verified_blocks = 0
    for receipt_path in sorted(capture_root.glob("*/receipt.json")):
        receipt = _read_json(receipt_path)
        if receipt.get("method") != "getBlock":
            continue
        request = json.loads(receipt["request_json"])
        params = request.get("params")
        options = params[1] if isinstance(params, list) and len(params) > 1 else None
        if not isinstance(options, dict) or options.get("transactionDetails") != "full":
            continue
        response = json.loads(_capture(receipt_path, receipt))
        block = response.get("result")
        transactions = block.get("transactions") if isinstance(block, dict) else None
        if not isinstance(transactions, list):
            raise ValueError("full_block_transactions_missing")
        verified_blocks += 1
        for transaction in transactions:
            signatures = transaction.get("transaction", {}).get("signatures")
            signature = signatures[0] if isinstance(signatures, list) and signatures else None
            if not isinstance(signature, str):
                continue
            signature_hash = hashlib.sha256(signature.encode()).hexdigest()
            if signature_hash not in wanted:
                continue
            if signature_hash in matched:
                raise ValueError("eligible_signature_duplicate_in_captures")
            matched.add(signature_hash)
            result = diagnose_pumpswap_sell_v2(transaction)
            outcomes[result["status"]] += 1
            if result.get("reason"):
                reasons[result["reason"]] += 1
            ledger = result["account_ledger"]
            ledger_statuses[ledger.get("status", "status_missing")] += 1
            ledger_unknown_reasons.update(ledger.get("unknown_reasons", {}))
            ledger_censored_reasons.update(ledger.get("censored_reasons", {}))
            if (
                result.get("transfer_fact_state")
                == "exact_checked_inner_transfer_and_event_equations"
            ):
                for name, passed in result["equations"].items():
                    equations[f"{name}:{'pass' if passed else 'fail'}"] += 1

    missing = len(wanted) - len(matched)
    if missing:
        outcomes["not_found_in_capture"] += missing
    code_paths = (
        "src/memecoin_pumpswap/compiled_economics.py",
        "src/memecoin_pumpswap/economic_v2.py",
        "src/memecoin_pumpswap/legacy_token_ledger.py",
        "src/memecoin_pumpswap/normalize.py",
        "src/memecoin_pumpswap/decoder.py",
        "src/memecoin_pumpswap/schema.py",
        "tools/brain_senses_select_pumpswap_sell_sample.py",
        "tools/brain_senses_census_pumpswap_sell_ledger.py",
    )
    hashes = {
        path: hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest() for path in code_paths
    }
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    return {
        "schema_version": 1,
        "experiment": "pumpswap_sell23_full_transaction_ledger_v2_capture_census",
        "status": "complete_over_eligible_captured_candidates" if missing == 0 else "partial",
        "git_commit": commit,
        "code_sha256": hashes,
        "capture_root": str(capture_root),
        "offline_replay": True,
        "helius_requests_made": 0,
        "eligible_candidates": len(wanted),
        "matched_candidates": len(matched),
        "selected_full_blocks_verified": verified_blocks,
        "outcomes": dict(sorted(outcomes.items())),
        "reasons": dict(sorted(reasons.items())),
        "ledger_statuses": dict(sorted(ledger_statuses.items())),
        "ledger_unknown_reasons": dict(sorted(ledger_unknown_reasons.items())),
        "ledger_censored_reasons": dict(sorted(ledger_censored_reasons.items())),
        "equations": dict(sorted(equations.items())),
        "scope": (
            "census of the declared sell23 structural candidate class in these captured full "
            "blocks; "
            "not global PumpSwap completeness, economic market coverage, or training eligibility"
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = census(args.capture_root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
        args.output.chmod(0o600)
    except Exception as exc:
        print(f"census failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "status",
                    "eligible_candidates",
                    "matched_candidates",
                    "outcomes",
                    "helius_requests_made",
                )
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
