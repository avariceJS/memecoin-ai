#!/usr/bin/env python3
"""Offline captured-class structural census for PumpSwap v1 buy-exact attempts."""

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

from memecoin_pumpswap.buy_exact_v1 import (  # noqa: E402
    CONTRACT,
    FEE_RECIPIENTS_DOC_COMMIT,
    FEE_RECIPIENTS_DOC_SHA256,
    IDL_SHA256,
    inspect_buy_exact_quote_in_v1,
)  # noqa: E402
from memecoin_pumpswap.decoder import PROGRAM, decode58  # noqa: E402

SCHEMA_VERSION = "pumpswap-buy-exact-v1-captured-class-census-v2"
DISCRIMINATOR = bytes.fromhex("c62e1552b4d9e870")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text())


def _is_candidate_transaction(tx: dict[str, Any]) -> bool:
    """Cheaply detect a PumpSwap discriminator without normalizing unrelated transactions."""
    message = tx.get("transaction", {}).get("message", {})
    meta = tx.get("meta", {})
    raw_keys = message.get("accountKeys")
    if not isinstance(raw_keys, list):
        return False
    keys = [value.get("pubkey") if isinstance(value, dict) else value for value in raw_keys]
    loaded = meta.get("loadedAddresses") or {}
    if isinstance(loaded, dict):
        keys.extend(loaded.get("writable", []))
        keys.extend(loaded.get("readonly", []))
    instructions = list(message.get("instructions", []))
    for group in meta.get("innerInstructions") or []:
        instructions.extend(group.get("instructions", []))
    for instruction in instructions:
        if not isinstance(instruction, dict):
            continue
        index = instruction.get("programIdIndex")
        if type(index) is not int or not 0 <= index < len(keys) or keys[index] != PROGRAM:
            continue
        data = instruction.get("data")
        if not isinstance(data, str):
            continue
        try:
            if decode58(data).startswith(DISCRIMINATOR):
                return True
        except ValueError:
            continue
    return False


def _capture_rows(root: Path) -> list[tuple[Path, dict[str, Any], dict[str, Any]]]:
    rows = []
    for receipt_path in sorted((root / "captures/helius-free").glob("*/receipt.json")):
        receipt = _json(receipt_path)
        request = json.loads(receipt["request_json"])
        params = request.get("params")
        if (
            receipt.get("source_alias") != "helius-free"
            or request.get("method") != "getBlock"
            or not isinstance(params, list)
            or len(params) != 2
            or not isinstance(params[1], dict)
            or params[1].get("transactionDetails") != "full"
        ):
            continue
        rows.append((receipt_path.parent / receipt["storage"]["path"], receipt, request))
    return rows


def _read_block(zstd: str, capture_path: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    compressed = capture_path.read_bytes()
    storage = receipt.get("storage")
    if not isinstance(storage, dict):
        raise ValueError("capture_storage_receipt_missing")
    if len(compressed) != storage.get("bytes") or _sha256(compressed) != storage.get("sha256"):
        raise ValueError("compressed_capture_hash_or_length_mismatch")
    completed = subprocess.run([zstd, "-dc", str(capture_path)], check=True, capture_output=True)
    raw = completed.stdout
    if len(raw) != receipt.get("response_bytes") or _sha256(raw) != receipt.get("response_sha256"):
        raise ValueError("decompressed_capture_hash_or_length_mismatch")
    wrapper = json.loads(raw)
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("result"), dict):
        raise ValueError("block_response_shape")
    if not isinstance(wrapper["result"].get("transactions"), list):
        raise ValueError("full_block_transactions_missing")
    return wrapper["result"]


def census(
    root: Path,
    output: Path,
    expected_slots: int = 512,
    source_binding_receipt: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    output = output.resolve()
    if output.exists():
        raise ValueError("output_exists_refusing_overwrite")
    zstd = shutil.which("zstd")
    if zstd is None:
        raise ValueError("zstd_cli_unavailable")
    plan_path = root / "plan.json"
    if not plan_path.is_file():
        raise ValueError("capture_plan_missing")
    plan_bytes = plan_path.read_bytes()
    plan_pin = _sha256(plan_bytes)
    audit_receipt_path = source_binding_receipt or (
        REPO_ROOT / "docs/evidence/helius-alchemy-pair64-offline-audit-2026-10-09.json"
    )
    audit_receipt = _json(audit_receipt_path)
    if audit_receipt.get("bound_plan_sha256") == plan_pin:
        binding_kind = "pair64_plan_bound_provider_audit"
    else:
        expected_capture_root = (root / "captures/helius-free").resolve()
        receipt_capture_root = Path(audit_receipt.get("capture_root", "")).resolve()
        if (
            receipt_capture_root != expected_capture_root
            or audit_receipt.get("selected_full_blocks_verified") != expected_slots
            or audit_receipt.get("offline_replay") is not True
        ):
            raise ValueError("source_binding_receipt_does_not_bind_captured_blocks")
        binding_kind = "captured_class_full_block_replay_receipt"

    captures = _capture_rows(root)
    if len(captures) != expected_slots:
        raise ValueError("unexpected_full_block_capture_count")

    slot_rows: set[int] = set()
    seen_signatures: set[str] = set()
    reference_slots: set[int] = set()
    rows: list[dict[str, Any]] = []
    matrix: Counter[tuple[str, str, str, str, str]] = Counter()
    counters: Counter[str] = Counter()
    captures_receipts = []
    for capture_path, receipt, request in captures:
        slot = request["params"][0]
        if type(slot) is not int or slot in slot_rows:
            raise ValueError("duplicate_or_invalid_full_block_slot")
        slot_rows.add(slot)
        block = _read_block(zstd, capture_path, receipt)
        counters["full_block_transactions"] += len(block["transactions"])
        counters["raw_response_bytes"] += receipt["response_bytes"]
        counters["compressed_response_bytes"] += receipt["storage"]["bytes"]
        counters["block_requests"] += 1
        captures_receipts.append(
            {
                "slot": slot,
                "capture_receipt": str(capture_path.parent.relative_to(root) / "receipt.json"),
                "compressed_sha256": receipt["storage"]["sha256"],
                "response_sha256": receipt["response_sha256"],
                "receipt_ns": receipt["received_ns"],
                "response_bytes": receipt["response_bytes"],
                "compressed_bytes": receipt["storage"]["bytes"],
            }
        )
        for transaction_index, tx in enumerate(block["transactions"]):
            if not _is_candidate_transaction(tx):
                continue
            transaction_status = (
                "failed" if tx.get("meta", {}).get("err") is not None else "success"
            )
            counters["candidate_transactions"] += 1
            counters[f"candidate_transactions_{transaction_status}"] += 1
            signatures = tx.get("transaction", {}).get("signatures")
            if (
                not isinstance(signatures, list)
                or not signatures
                or not isinstance(signatures[0], str)
            ):
                counters["candidate_signature_shape_errors"] += 1
                continue
            signature = signatures[0]
            if signature in seen_signatures:
                counters["duplicate_candidate_signatures"] += 1
            seen_signatures.add(signature)
            inspected = inspect_buy_exact_quote_in_v1(tx)
            if inspected["status"] == "malformed_or_missing_trace":
                counters["normalization_errors"] += 1
                rows.append(
                    {
                        "slot": slot,
                        "transaction_index": transaction_index,
                        "signature": signature,
                        "transaction_status": "normalization_failed_or_trace_unknown",
                        "normalization_error": inspected["normalization_error"],
                        "reference_state": "unknown_due_trace_failure",
                        "availability_ns": receipt["received_ns"],
                        "native_event_time_unix_seconds": block.get("blockTime"),
                    }
                )
                continue
            for reference in inspected["references"]:
                counters["instruction_references"] += 1
                reference_slots.add(slot)
                key = (
                    transaction_status,
                    str(reference["instruction_data_length"]),
                    str(reference["remaining_account_count"]),
                    reference["suffix_state"],
                    reference["argument_state"],
                )
                matrix[key] += 1
                rows.append(
                    {
                        "slot": slot,
                        "transaction_index": transaction_index,
                        "signature": signature,
                        "native_event_time_unix_seconds": block.get("blockTime"),
                        "availability_ns": receipt["received_ns"],
                        "transaction_status": transaction_status,
                        "path": reference["path"],
                        "stack_height": reference["stack_height"],
                        "instruction_data_hex": reference["instruction_data_hex"],
                        "instruction_data_length": reference["instruction_data_length"],
                        "argument_state": reference["argument_state"],
                        "arguments": reference["arguments"],
                        "base_account_prefix_state": reference["base_account_prefix_state"],
                        "remaining_account_count": reference["remaining_account_count"],
                        "suffix_state": reference["suffix_state"],
                        "suffix_checks": reference["suffix_checks"],
                        "identified_roles": reference.get("identified_roles", {}),
                        "observed_fee_recipient": reference.get("observed_fee_recipient"),
                        "observed_fee_ata": reference.get("observed_fee_ata"),
                        "unresolved_predecessor_account": reference.get(
                            "unresolved_predecessor_account"
                        ),
                        "settlement_state": reference["settlement_state"],
                        "economic_state": reference.get("economic_state", "not_assessed"),
                    }
                )

    if len(slot_rows) != expected_slots:
        raise ValueError("unique_slot_count_mismatch")
    counters["candidate_unique_signatures"] = len(seen_signatures)
    counters["reference_slots"] = len(reference_slots)
    counters.setdefault("normalization_errors", 0)
    report = {
        "schema_version": SCHEMA_VERSION,
        "contract": CONTRACT,
        "scope": (
            "all recognized v1 buy_exact_quote_in references in the supplied captured "
            "Helius full blocks only"
        ),
        "source": {
            "root": str(root),
            "plan_sha256": plan_pin,
            "provider_audit_receipt": str(audit_receipt_path),
            "provider_audit_receipt_sha256": _sha256(audit_receipt_path.read_bytes()),
            "source_binding_kind": binding_kind,
            "provider_audit_code_commit": audit_receipt["audit_code_commit"],
            "provider_audit_analysis_sha256": audit_receipt["analysis_sha256"],
            "expected_full_block_slots": expected_slots,
            "observed_full_block_slots": len(slot_rows),
            "captures": captures_receipts,
        },
        "pins": {
            "pump_amm_idl_sha256": IDL_SHA256,
            "fee_recipient_notice_commit": FEE_RECIPIENTS_DOC_COMMIT,
            "fee_recipient_notice_sha256": FEE_RECIPIENTS_DOC_SHA256,
            "discriminator_hex": DISCRIMINATOR.hex(),
            "tool_sha256": _sha256(Path(__file__).read_bytes()),
            "implementation_sha256": _sha256(
                (REPO_ROOT / "src/memecoin_pumpswap/buy_exact_v1.py").read_bytes()
            ),
        },
        "time_semantics": {
            "native_event_time": "blockTime reported in the captured finalized block response",
            "availability": (
                "recorded local capture receipt time; not historical provider availability"
            ),
            "decision_time": "not applicable to this offline structural census",
        },
        "counts": dict(sorted(counters.items())),
        "matrix": [
            {
                "transaction_status": key[0],
                "instruction_data_length": key[1],
                "remaining_account_count": key[2],
                "suffix_state": key[3],
                "argument_state": key[4],
                "references": count,
            }
            for key, count in sorted(matrix.items())
        ],
        "rows": rows,
        "limitations": [
            (
                "The bounded captured-block class is not global transaction discovery or "
                "process completeness."
            ),
            "A structural fee-pair candidate is not proof of an executed or settled fill.",
            "Three-account suffix predecessors and four/seven-account suffixes remain unqualified.",
            "24-byte and 26-byte instruction payloads are retained as unverified layouts.",
            "No zero-activity, wallet-net price, training, execution, or edge claim is made.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return {
        "output": str(output),
        "output_sha256": _sha256(payload),
        "counts": dict(sorted(counters.items())),
        "matrix": report["matrix"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-slots", type=int, default=512)
    parser.add_argument("--source-binding-receipt", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            census(
                args.capture_root,
                args.output,
                args.expected_slots,
                args.source_binding_receipt,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
