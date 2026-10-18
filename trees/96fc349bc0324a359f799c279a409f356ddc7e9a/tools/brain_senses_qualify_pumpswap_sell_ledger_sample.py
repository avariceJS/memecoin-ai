#!/usr/bin/env python3
"""Offline qualification of a fresh PumpSwap sell sample under ledger v2."""

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

from memecoin_pumpswap.economic_v2 import diagnose_pumpswap_sell_v2  # noqa: E402


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _capture_bytes(receipt_path: Path, receipt: dict[str, Any]) -> bytes:
    storage = receipt.get("storage")
    if not isinstance(storage, dict) or not isinstance(storage.get("path"), str):
        raise ValueError("capture_storage_path_missing")
    payload_path = receipt_path.parent / storage["path"]
    encoded = payload_path.read_bytes()
    if hashlib.sha256(encoded).hexdigest() != storage.get("sha256"):
        raise ValueError("stored_capture_hash_mismatch")
    if payload_path.suffix == ".zst":
        raw = subprocess.run(
            ["zstd", "-q", "-d", "-c", str(payload_path)],
            check=True,
            capture_output=True,
        ).stdout
    else:
        raw = encoded
    if hashlib.sha256(raw).hexdigest() != receipt.get("response_sha256"):
        raise ValueError("raw_capture_hash_mismatch")
    if len(raw) != receipt.get("response_bytes"):
        raise ValueError("raw_capture_size_mismatch")
    return raw


def qualify(capture_root: Path, sample_path: Path, audit_manifest_path: Path) -> dict[str, Any]:
    sample = _read_json(sample_path)
    if sample.get("status") != "selected_not_decoded":
        raise ValueError("sample_not_in_selected_not_decoded_state")
    items = sample.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("sample_items_missing")
    wanted = {item["signature_sha256"]: item for item in items}
    if len(wanted) != len(items):
        raise ValueError("duplicate_sample_signature_hash")
    audit_manifest = audit_manifest_path
    if not audit_manifest.is_file():
        raise ValueError("precommitted_audit_manifest_missing")
    if audit_manifest.stat().st_mode & 0o077:
        raise ValueError("audit_manifest_permissions_not_private")
    code_paths = (
        "src/memecoin_pumpswap/compiled_economics.py",
        "src/memecoin_pumpswap/economic_v2.py",
        "src/memecoin_pumpswap/legacy_token_ledger.py",
        "src/memecoin_pumpswap/normalize.py",
        "src/memecoin_pumpswap/decoder.py",
        "src/memecoin_pumpswap/schema.py",
        "tools/brain_senses_select_pumpswap_sell_sample.py",
        "tools/brain_senses_select_pumpswap_sell_ledger_sample.py",
        "tools/brain_senses_qualify_pumpswap_sell_ledger_sample.py",
    )
    code_hashes = {
        path: hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest() for path in code_paths
    }
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    outcomes: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    transfer_fact_states: Counter[str] = Counter()
    settlement_states: Counter[str] = Counter()
    equation_counts: Counter[str] = Counter()
    balance_states: Counter[str] = Counter()
    ledger_states: Counter[str] = Counter()
    ledger_unknown_reasons: Counter[str] = Counter()
    ledger_censored_reasons: Counter[str] = Counter()
    ledger_exact_accounts = 0
    ledger_censored_accounts = 0
    ledger_unknown_accounts = 0
    ledger_target_accounts = 0
    matched_hashes: set[str] = set()
    receipt_ns: list[int] = []
    inspected_blocks = 0

    for receipt_path in sorted(capture_root.glob("*/receipt.json")):
        receipt = _read_json(receipt_path)
        if receipt.get("method") != "getBlock":
            continue
        request = json.loads(receipt["request_json"])
        params = request.get("params")
        if not isinstance(params, list) or not params or type(params[0]) is not int:
            raise ValueError("get_block_request_identity_invalid")
        options = params[1] if len(params) > 1 else None
        if not isinstance(options, dict) or options.get("transactionDetails") != "full":
            continue
        slot = params[0]
        relevant = {h for h, item in wanted.items() if item.get("slot") == slot}
        if not relevant:
            continue
        block = json.loads(_capture_bytes(receipt_path, receipt)).get("result")
        if not isinstance(block, dict):
            raise ValueError("selected_block_result_missing")
        inspected_blocks += 1
        signatures = block.get("transactions")
        if not isinstance(signatures, list):
            raise ValueError("selected_block_transactions_missing")
        for transaction in signatures:
            tx_signatures = transaction.get("transaction", {}).get("signatures")
            signature = (
                tx_signatures[0] if isinstance(tx_signatures, list) and tx_signatures else None
            )
            if not isinstance(signature, str):
                continue
            signature_hash = hashlib.sha256(signature.encode()).hexdigest()
            if signature_hash not in relevant:
                continue
            if signature_hash in matched_hashes:
                raise ValueError("sample_signature_duplicate_in_capture")
            matched_hashes.add(signature_hash)
            receipt_ns.append(int(receipt["received_ns"]))
            result = diagnose_pumpswap_sell_v2(transaction)
            outcomes[result["status"]] += 1
            if result.get("reason"):
                reason_counts[result.get("reason", "reason_missing")] += 1
            ledger = result.get("account_ledger")
            if isinstance(ledger, dict):
                ledger_states[ledger.get("status", "status_missing")] += 1
                ledger_exact_accounts += int(ledger.get("exact_account_count", 0))
                ledger_censored_accounts += int(ledger.get("censored_account_count", 0))
                ledger_unknown_accounts += int(ledger.get("unknown_account_count", 0))
                ledger_target_accounts += int(ledger.get("target_account_count", 0))
                ledger_unknown_reasons.update(ledger.get("unknown_reasons", {}))
                ledger_censored_reasons.update(ledger.get("censored_reasons", {}))
            if (
                result.get("transfer_fact_state")
                != "exact_checked_inner_transfer_and_event_equations"
            ):
                continue
            transfer_fact_states[result["transfer_fact_state"]] += 1
            settlement_states[result["account_settlement_state"]] += 1
            for name, passed in result["equations"].items():
                equation_counts[f"{name}:{'pass' if passed else 'fail'}"] += 1
            evidence = result["balance_evidence"]
            balance_states[evidence["pool_token_accounts"]] += 1
            balance_states[evidence["buyback_fee_ata"]] += 1
            unknown_count = evidence["other_touched_token_accounts"]["unknown"]
            balance_states[f"other_accounts_unknown:{unknown_count}"] += 1

    missing = len(items) - len(matched_hashes)
    if missing:
        outcomes["not_found_in_capture"] += missing
    report = {
        "schema_version": 1,
        "experiment": "pumpswap_sell23_full_transaction_ledger_v2_sample_qualification",
        "sample": sample.get("sample"),
        "status": "qualified_diagnostic_only"
        if outcomes.get("reconciled_diagnostic_only") == len(items)
        else "qualified_with_quarantines",
        "admission": "none",
        "training_eligible": False,
        "execution_supported": False,
        "sample_manifest_sha256": hashlib.sha256(sample_path.read_bytes()).hexdigest(),
        "audit_manifest_sha256": hashlib.sha256(audit_manifest.read_bytes()).hexdigest(),
        "git_commit": commit,
        "code_sha256": code_hashes,
        "qualification_build_sha256": hashlib.sha256(
            json.dumps(
                {
                    "git_commit": commit,
                    "code_sha256": code_hashes,
                    "sample_manifest_sha256": hashlib.sha256(sample_path.read_bytes()).hexdigest(),
                    "audit_manifest_sha256": hashlib.sha256(
                        audit_manifest.read_bytes()
                    ).hexdigest(),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "capture_root": str(capture_root),
        "offline_replay": True,
        "helius_requests_made": 0,
        "sample_selected": len(items),
        "sample_matched": len(matched_hashes),
        "selected_blocks_verified": inspected_blocks,
        "transaction_receipt_time_ns": {
            "min": min(receipt_ns) if receipt_ns else None,
            "max": max(receipt_ns) if receipt_ns else None,
        },
        "outcomes": dict(sorted(outcomes.items())),
        "quarantine_reasons": dict(sorted(reason_counts.items())),
        "ledger_statuses": dict(sorted(ledger_states.items())),
        "ledger_unknown_reasons": dict(sorted(ledger_unknown_reasons.items())),
        "ledger_censored_reasons": dict(sorted(ledger_censored_reasons.items())),
        "ledger_exact_accounts": ledger_exact_accounts,
        "ledger_censored_accounts": ledger_censored_accounts,
        "ledger_unknown_accounts": ledger_unknown_accounts,
        "ledger_target_accounts": ledger_target_accounts,
        "transfer_fact_states": dict(sorted(transfer_fact_states.items())),
        "account_settlement_states": dict(sorted(settlement_states.items())),
        "equations": dict(sorted(equation_counts.items())),
        "balance_evidence": dict(sorted(balance_states.items())),
        "scope": (
            "single-transaction economic qualification; not range completeness or market-wide truth"
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--audit-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = qualify(args.capture_root, args.sample, args.audit_manifest)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
        args.output.chmod(0o600)
        brief_keys = (
            "status",
            "sample",
            "sample_selected",
            "sample_matched",
            "outcomes",
            "helius_requests_made",
        )
        print(json.dumps({key: report[key] for key in brief_keys}))
        return 0
    except Exception as exc:
        print(f"qualification failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
