#!/usr/bin/env python3
"""Replay a frozen PumpSwap v1 buy-exact development population offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from memecoin_pumpswap.decoder import TOKEN  # noqa: E402
from memecoin_pumpswap.economic_buy_exact_v1 import (  # noqa: E402
    CONTRACT,
    diagnose_pumpswap_buy_exact_v1,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _row_key(row: dict[str, Any]) -> tuple[Any, ...]:
    path = row.get("path")
    if not isinstance(path, list):
        raise ValueError("instruction_path_missing")
    return (row["slot"], row["transaction_index"], tuple(path), row["signature"])


def _selected_rows(census: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for row in census["rows"]:
        roles = row.get("identified_roles", {})
        if (
            row.get("transaction_status") == "success"
            and row.get("argument_state") == "pinned_idl_v1_shape"
            and row.get("instruction_data_length") == 25
            and row.get("remaining_account_count") == 2
            and row.get("suffix_state") == "documented_fee_pair_suffix_candidate"
            and roles.get("base_token_program") == TOKEN
            and roles.get("quote_token_program") == TOKEN
        ):
            rows.append(row)
    return sorted(
        rows, key=lambda r: (r["slot"], r["transaction_index"], r["path"], r["signature"])
    )


def _load_full_block(
    root: Path,
    source_row: dict[str, Any],
    zstd: str,
    cache: dict[int, dict[str, Any]],
    captures_by_slot: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    slot = source_row["slot"]
    if slot in cache:
        return cache[slot]
    capture = captures_by_slot.get(slot)
    if capture is None:
        raise ValueError("selected_slot_missing_full_block_capture")
    receipt_path = root / capture["capture_receipt"]
    receipt = _read_json(receipt_path)
    request = json.loads(receipt["request_json"])
    params = request.get("params")
    if (
        request.get("method") != "getBlock"
        or not isinstance(params, list)
        or len(params) != 2
        or params[0] != slot
        or not isinstance(params[1], dict)
        or params[1].get("transactionDetails") != "full"
    ):
        raise ValueError("selected_capture_is_not_pinned_full_block")
    storage = receipt.get("storage")
    if not isinstance(storage, dict):
        raise ValueError("selected_capture_storage_receipt_missing")
    if (
        storage.get("sha256") != capture["compressed_sha256"]
        or receipt.get("response_sha256") != capture["response_sha256"]
        or receipt.get("received_ns") != capture["receipt_ns"]
    ):
        raise ValueError("selected_capture_receipt_disagrees_with_census")
    capture_path = receipt_path.parent / storage["path"]
    compressed = capture_path.read_bytes()
    if len(compressed) != storage.get("bytes") or _sha(compressed) != storage.get("sha256"):
        raise ValueError("compressed_capture_hash_or_length_mismatch")
    raw = subprocess.run([zstd, "-dc", str(capture_path)], check=True, capture_output=True).stdout
    if len(raw) != receipt.get("response_bytes") or _sha(raw) != receipt.get("response_sha256"):
        raise ValueError("decompressed_capture_hash_or_length_mismatch")
    response = json.loads(raw)
    block = response.get("result") if isinstance(response, dict) else None
    if not isinstance(block, dict) or not isinstance(block.get("transactions"), list):
        raise ValueError("full_block_response_shape")
    if block.get("parentSlot") is not None and type(block.get("parentSlot")) is not int:
        raise ValueError("full_block_parent_slot_invalid")
    cache[slot] = block
    return block


def replay(census_path: Path, plan_path: Path, output: Path) -> dict[str, Any]:
    census_path = census_path.resolve()
    plan_path = plan_path.resolve()
    output = output.resolve()
    if output.exists():
        raise ValueError("output_exists_refusing_overwrite")
    census_bytes = census_path.read_bytes()
    census = json.loads(census_bytes)
    plan = _read_json(plan_path)
    if _sha(census_bytes) != plan.get("source_report_sha256"):
        raise ValueError("development_plan_source_report_hash_mismatch")
    if census.get("source", {}).get("plan_sha256") != plan.get("source_pair64_plan_sha256"):
        raise ValueError("development_plan_pair64_plan_mismatch")
    if (
        plan.get("purpose")
        != "buyExact v1 development replay selection; not audit or population completeness"
    ):
        raise ValueError("unexpected_development_plan_purpose")
    criteria = plan.get("criteria", {})
    if criteria.get("base_token_program") != TOKEN or criteria.get("quote_token_program") != TOKEN:
        raise ValueError("development_plan_token_program_scope_mismatch")
    expected_rows = _selected_rows(census)
    supplied_rows = plan.get("selection_rows")
    if not isinstance(supplied_rows, list) or len(expected_rows) != plan.get("row_count"):
        raise ValueError("development_plan_denominator_mismatch")
    expected_keys = [_row_key(r) for r in expected_rows]
    supplied_keys = [_row_key(r) for r in supplied_rows]
    if expected_keys != supplied_keys or len(set(supplied_keys)) != len(supplied_keys):
        raise ValueError("development_plan_selection_not_census_predicate")
    if plan.get("unique_transactions") != len({r["signature"] for r in supplied_rows}):
        raise ValueError("development_plan_unique_transaction_count_mismatch")
    zstd = shutil.which("zstd")
    if zstd is None:
        raise ValueError("zstd_cli_unavailable")

    source_root = Path(census["source"]["root"]).resolve()
    source_plan_path = source_root / "plan.json"
    if _sha(source_plan_path.read_bytes()) != census["source"].get("plan_sha256"):
        raise ValueError("captured_pair64_plan_hash_mismatch")
    audit_receipt_path = (
        REPO_ROOT / "docs/evidence/helius-alchemy-pair64-offline-audit-2026-10-09.json"
    )
    audit_receipt = _read_json(audit_receipt_path)
    if audit_receipt.get("bound_plan_sha256") != census["source"].get("plan_sha256"):
        raise ValueError("captured_pair64_audit_binding_mismatch")
    captures_by_slot = {row["slot"]: row for row in census["source"]["captures"]}
    if len(captures_by_slot) != census["source"].get("expected_full_block_slots"):
        raise ValueError("census_full_block_manifest_invalid")
    census_by_key = {
        _row_key(r): r
        for r in census["rows"]
        if isinstance(r.get("path"), list) and isinstance(r.get("signature"), str)
    }

    cache: dict[int, dict[str, Any]] = {}
    facts = []
    counts: Counter[str] = Counter()
    for selected in supplied_rows:
        key = _row_key(selected)
        census_row = census_by_key.get(key)
        if census_row is None:
            raise ValueError("selected_reference_missing_from_census")
        block = _load_full_block(
            source_root, captures_by_slot[selected["slot"]], zstd, cache, captures_by_slot
        )
        tx_index = selected["transaction_index"]
        if not 0 <= tx_index < len(block["transactions"]):
            raise ValueError("selected_transaction_index_out_of_range")
        transaction = block["transactions"][tx_index]
        signatures = transaction.get("transaction", {}).get("signatures")
        if (
            not isinstance(signatures, list)
            or not signatures
            or signatures[0] != selected["signature"]
        ):
            raise ValueError("selected_signature_index_mismatch")
        diagnostic = diagnose_pumpswap_buy_exact_v1(transaction)
        matching = [
            ref for ref in diagnostic.get("references", []) if ref.get("path") == selected["path"]
        ]
        if len(matching) != 1:
            raise ValueError("selected_instruction_path_not_unique_in_replay")
        fact = matching[0]
        counts[f"trade_fact_state:{fact['trade_fact_state']}"] += 1
        counts[f"settlement_state:{fact['settlement_state']}"] += 1
        facts.append(
            {
                "slot": selected["slot"],
                "transaction_index": tx_index,
                "signature": selected["signature"],
                "path": selected["path"],
                "stack_height": selected["stack_height"],
                "availability_ns": census_row["availability_ns"],
                "native_event_time_unix_seconds": census_row["native_event_time_unix_seconds"],
                "other_v1_reference_count_in_transaction": len(diagnostic.get("references", []))
                - 1,
                "fact": fact,
            }
        )
    if len(facts) != plan["row_count"]:
        raise ValueError("development_replay_incomplete_denominator")
    report = {
        "schema_version": 1,
        "contract": CONTRACT,
        "status": "development_replay_only",
        "scope": (
            "all selected 15:00Z captured-class refs from the frozen plan; no audit, "
            "completeness, or admission claim"
        ),
        "source": {
            "census_report_path": str(census_path),
            "census_report_sha256": _sha(census_bytes),
            "development_plan_path": str(plan_path),
            "development_plan_sha256": _sha(plan_path.read_bytes()),
            "source_pair64_plan_sha256": census["source"]["plan_sha256"],
            "captured_slots_replayed": sorted(cache),
            "full_block_receipts_replayed": len(cache),
        },
        "pins": {
            "tool_sha256": _sha(Path(__file__).read_bytes()),
            "attempt_inspector_sha256": _sha(
                (REPO_ROOT / "src/memecoin_pumpswap/buy_exact_v1.py").read_bytes()
            ),
            "economic_diagnostic_sha256": _sha(
                (REPO_ROOT / "src/memecoin_pumpswap/economic_buy_exact_v1.py").read_bytes()
            ),
        },
        "counts": {
            "selected_references": len(facts),
            "unique_transactions": len({r["signature"] for r in facts}),
            **dict(sorted(counts.items())),
        },
        "time_semantics": {
            "native_event_time": "blockTime from captured finalized full-block response",
            "availability": "receipt time of captured Helius full-block response",
            "decision_time": "not applicable to offline development replay",
        },
        "facts": facts,
        "admission": {"training": "none", "execution": "none", "wallet_net_price": "none"},
        "limitations": [
            (
                "Development-only evidence; the 13:00Z archive is a disjoint one-time audit "
                "and is not used here."
            ),
            "Captured-block class only; no global discovery or process completeness claim.",
            "Fee-flow and price formulas are diagnostic hypotheses, not qualified pricing.",
            (
                "Unknown and censored account settlement remains explicit; no residual "
                "balancing or inference is allowed."
            ),
        ],
    }
    payload = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return {"output": str(output), "output_sha256": _sha(payload), "counts": report["counts"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--census", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(replay(args.census, args.plan, args.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        subprocess.CalledProcessError,
    ) as exc:
        print(
            json.dumps({"status": "failed_closed", "error": str(exc)}, sort_keys=True),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
