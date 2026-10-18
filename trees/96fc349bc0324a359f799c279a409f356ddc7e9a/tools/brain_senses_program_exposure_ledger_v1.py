#!/usr/bin/env python3
"""Build a bounded, offline venue-neutral program exposure ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from memecoin_pumpswap.normalize import (  # noqa: E402
    NormalizationError,
    normalize_compiled_transaction,
)
from memecoin_pumpswap.schema import SCHEMA as PUMP_AMM_SCHEMA  # noqa: E402

SCHEMA = "senses_program_exposure_ledger_v1"
PAIR64 = Path("work/brain-senses-live-helius-alchemy-512-pair64-20261009/20261009T150000Z")
C3 = Path("work/brain-senses-live-helius-inventory-v3-20261010-3")
FRAME_IDS = tuple(f"20261010T{hour:02d}0000Z" for hour in range(5, 11))
OUTPUT_CAP_BYTES = 6 * 1024 * 1024
EXAMPLE_HASHES_PER_CLASS = 3
PUMP_AMM_PROGRAM_ID = PUMP_AMM_SCHEMA["address"]
PROGRAM_STATUS_ENUM = ("success", "failed", "unknown")
INSTRUCTION_PATH_ENUM = ("outer", "inner")
PROGRAM_ID_SOURCES = ("static", "loaded_writable", "loaded_readonly")
SLOT_LEDGER_FIELDS = (
    "slot",
    "blockhash",
    "event_time_unix_seconds",
    "full_request_json_sha256",
    "signature_request_json_sha256",
    "full_receipt_ns",
    "signature_receipt_ns",
    "availability_ns",
    "full_response_sha256",
    "full_stored_sha256",
    "signature_response_sha256",
    "signature_stored_sha256",
    "provider_signature_count",
    "provider_full_transaction_count",
    "transaction_count",
    "normalized_transactions",
    "failed_transactions",
    "unsupported_normalization",
    "outer_instruction_references",
    "inner_instruction_references",
    "program_count",
    "program_exposures",
)


class LedgerFailure(ValueError):
    """Fail-closed error for missing or inconsistent selected evidence."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _request_identity_sha256(request: dict[str, Any]) -> str:
    return _sha(_canonical(request))


def _alt_format_issue(transaction: Any) -> str | None:
    if not isinstance(transaction, dict):
        return None
    version = transaction.get("version")
    raw = transaction.get("transaction")
    message = raw.get("message") if isinstance(raw, dict) else None
    meta = transaction.get("meta")
    if not isinstance(message, dict) or not isinstance(meta, dict):
        return None
    if version not in ("legacy", 0, 1) or type(version) is bool:
        return None
    lookups = message.get("addressTableLookups", [])
    loaded = meta.get("loadedAddresses", {})
    if not isinstance(lookups, list) or not isinstance(loaded, dict):
        return "address_table_state_shape_invalid"
    writable = loaded.get("writable", [])
    readonly = loaded.get("readonly", [])
    if not isinstance(writable, list) or not isinstance(readonly, list):
        return "loaded_address_state_shape_invalid"
    if version in ("legacy", 1) and (lookups or writable or readonly):
        return "versioned_address_lookup_state_forbidden"
    return None


def _v0_outer_program_index_issue(transaction: Any) -> tuple[str, int] | None:
    if not isinstance(transaction, dict) or type(transaction.get("version")) is not int:
        return None
    if transaction.get("version") != 0:
        return None
    raw = transaction.get("transaction")
    message = raw.get("message") if isinstance(raw, dict) else None
    if not isinstance(message, dict):
        return None
    static_keys = message.get("accountKeys")
    outer = message.get("instructions")
    if not isinstance(static_keys, list) or not isinstance(outer, list):
        return None
    for index, instruction in enumerate(outer):
        if not isinstance(instruction, dict):
            continue
        program_index = instruction.get("programIdIndex")
        if type(program_index) is not int or not 0 <= program_index < len(static_keys):
            return "v0_outer_program_id_not_static", index
        if program_index == 0:
            return "v0_outer_program_id_is_fee_payer", index
    return None


def _raw_version_status_path_counts(transaction: Any) -> tuple[str, str, dict[str, int]]:
    version = transaction.get("version") if isinstance(transaction, dict) else None
    version_label = (
        f"v{version}" if type(version) is int else "legacy" if version == "legacy" else "unknown"
    )
    meta = transaction.get("meta") if isinstance(transaction, dict) else None
    if not isinstance(meta, dict) or "err" not in meta:
        status = "unknown"
    else:
        status = "success" if meta["err"] is None else "failed"
    raw = transaction.get("transaction") if isinstance(transaction, dict) else None
    message = raw.get("message") if isinstance(raw, dict) else None
    outer = message.get("instructions") if isinstance(message, dict) else None
    inner_groups = meta.get("innerInstructions") if isinstance(meta, dict) else None
    inner_count = 0
    if isinstance(inner_groups, list):
        for group in inner_groups:
            if isinstance(group, dict) and isinstance(group.get("instructions"), list):
                inner_count += len(group["instructions"])
    return (
        version_label,
        status,
        {
            "outer": len(outer) if isinstance(outer, list) else 0,
            "inner": inner_count,
        },
    )


class _Reader:
    def __init__(self) -> None:
        self.bytes_read = 0
        self.files: dict[Path, bytes] = {}

    def read(self, path: Path, *, cache: bool = True) -> bytes:
        path = path.resolve()
        if cache and path in self.files:
            return self.files[path]
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise LedgerFailure("input_missing_or_unreadable", str(path)) from exc
        self.bytes_read += len(raw)
        if cache:
            self.files[path] = raw
        return raw

    def json(self, path: Path) -> Any:
        try:
            return json.loads(self.read(path))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LedgerFailure("input_json_invalid", str(path)) from exc


def _required(path: Path) -> Path:
    if not path.is_file():
        raise LedgerFailure("required_input_missing", str(path))
    return path


def _pin_source(reader: _Reader, commit: str, relative: str, expected_sha: str) -> dict[str, str]:
    if not isinstance(commit, str) or len(commit) != 40:
        raise LedgerFailure("source_commit_invalid", relative)
    result = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if result.returncode or _sha(result.stdout) != expected_sha:
        raise LedgerFailure("source_code_binding_mismatch", f"{commit}:{relative}")
    return {"commit": commit, "path": relative, "sha256": expected_sha}


def _receipt_paths(reader: _Reader, root: Path, *, pair64: bool) -> list[Path]:
    provider_root = (
        root / "paired-block-capture/captures/helius-free" if pair64 else root / "captures"
    )
    if not provider_root.is_dir():
        raise LedgerFailure("capture_root_missing", str(provider_root))
    receipts = []
    for path in sorted(provider_root.glob("*/receipt.json")):
        receipt = reader.json(path)
        try:
            request = json.loads(receipt["request_json"])
            method = request["method"]
            details = (
                request["params"][1].get("transactionDetails")
                if method == "getBlock"
                and len(request.get("params", [])) > 1
                and isinstance(request["params"][1], dict)
                else None
            )
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LedgerFailure("receipt_request_invalid", str(path)) from exc
        if receipt.get("source_alias") != "helius-free":
            continue
        if method == "getBlocks" or (method == "getBlock" and details in {"full", "signatures"}):
            receipts.append(path)
    return receipts


def _capture(
    reader: _Reader, receipt_path: Path, zstd: str
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    receipt = reader.json(receipt_path)
    try:
        request = json.loads(receipt["request_json"])
        storage = receipt["storage"]
        compressed_path = receipt_path.parent / storage["path"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise LedgerFailure("capture_receipt_shape", str(receipt_path)) from exc
    compressed = reader.read(compressed_path, cache=False)
    if (
        storage.get("codec") != "zstd"
        or storage.get("bytes") != len(compressed)
        or storage.get("sha256") != _sha(compressed)
    ):
        raise LedgerFailure("stored_capture_hash_mismatch", str(receipt_path))
    decoded = subprocess.run([zstd, "-dc"], input=compressed, check=False, capture_output=True)
    if decoded.returncode:
        raise LedgerFailure("capture_decompression_failed", str(receipt_path))
    raw = decoded.stdout
    if receipt.get("response_bytes") != len(raw) or receipt.get("response_sha256") != _sha(raw):
        raise LedgerFailure("raw_capture_hash_mismatch", str(receipt_path))
    try:
        wrapper = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LedgerFailure("raw_capture_json_invalid", str(receipt_path)) from exc
    if not isinstance(wrapper, dict) or not isinstance(wrapper.get("result"), (dict, list)):
        raise LedgerFailure("capture_result_shape_invalid", str(receipt_path))
    if type(receipt.get("received_ns")) is not int or receipt["received_ns"] <= 0:
        raise LedgerFailure("capture_receipt_time_invalid", str(receipt_path))
    return request, receipt, wrapper["result"]


def _inventory(
    reader: _Reader,
    receipt_paths: list[Path],
    *,
    expected_start: int,
    expected_end_exclusive: int,
    expected_summary_hash: str | None,
    zstd: str,
) -> dict[str, Any]:
    inventory_path = None
    parsed = None
    receipt = None
    request = None
    for path in receipt_paths:
        current = reader.json(path)
        try:
            candidate = json.loads(current["request_json"])
        except (KeyError, json.JSONDecodeError, TypeError) as exc:
            raise LedgerFailure("inventory_request_invalid", str(path)) from exc
        if candidate.get("method") == "getBlocks":
            if inventory_path is not None:
                raise LedgerFailure("duplicate_inventory_capture", str(path))
            inventory_path = path
            request, receipt = candidate, current
    if inventory_path is None:
        raise LedgerFailure("inventory_capture_missing", str(expected_start))
    request, receipt, parsed = _capture(reader, inventory_path, zstd)
    params = request.get("params")
    if (
        not isinstance(params, list)
        or len(params) < 2
        or params[:2] != [expected_start, expected_end_exclusive - 1]
    ):
        raise LedgerFailure("inventory_range_mismatch", str(inventory_path))
    if not isinstance(parsed, list) or any(type(slot) is not int for slot in parsed):
        raise LedgerFailure("inventory_members_invalid", str(inventory_path))
    if parsed != sorted(set(parsed)) or any(
        not expected_start <= slot < expected_end_exclusive for slot in parsed
    ):
        raise LedgerFailure("inventory_members_not_ordered_unique_in_range", str(inventory_path))
    if (
        expected_summary_hash is not None
        and receipt.get("response_sha256") != expected_summary_hash
    ):
        raise LedgerFailure("inventory_summary_hash_mismatch", str(inventory_path))
    return {
        "capture_path": str(inventory_path.relative_to(ROOT)),
        "request_json_sha256": _sha(_canonical(request)),
        "response_sha256": receipt["response_sha256"],
        "stored_response_sha256": receipt["storage"]["sha256"],
        "request_started_ns": receipt.get("request_started_ns"),
        "receipt_ns": receipt["received_ns"],
        "availability_ns": receipt.get("availability_ns"),
        "range_inclusive": params[:2],
        "provider_returned_member_count": len(parsed),
        "returned_slots_sha256": _sha(_canonical(parsed)),
        "returned_slots": parsed,
    }


def _load_specs(reader: _Reader, zstd: str) -> list[dict[str, Any]]:
    specs = []

    # Pair64: the sealed Helius full/signature sample and its captured inventory.
    pair_root = ROOT / PAIR64
    pair_capture_root = pair_root / "paired-block-capture"
    pair_plan_path = _required(pair_capture_root / "plan.json")
    pair_summary_path = _required(pair_capture_root / "summary.json")
    pair_epoch_plan_path = _required(pair_root / "epoch-plan.json")
    pair_plan = reader.json(pair_plan_path)
    pair_summary = reader.json(pair_summary_path)
    pair_epoch_plan = reader.json(pair_epoch_plan_path)
    plan_bytes = reader.read(pair_plan_path)
    selected = pair_plan.get("full_sample_slots")
    if not isinstance(selected, list) or len(selected) != 512 or len(set(selected)) != 512:
        raise LedgerFailure("pair64_selected_slot_set_invalid", "expected 512 unique slots")
    plan_hash = _sha(plan_bytes)
    if pair_summary.get("plan_sha256") != plan_hash:
        raise LedgerFailure("pair64_summary_plan_hash_mismatch", "Pair64")
    source_pins = [
        _pin_source(
            reader,
            pair_plan["code_commit"],
            "tools/brain_senses_block_provider_pilot.py",
            pair_plan["code_sha256"],
        ),
        _pin_source(
            reader,
            pair_epoch_plan["code_commit"],
            "tools/brain_senses_block_epoch.py",
            pair_epoch_plan["code_sha256"],
        ),
    ]
    pair_receipts = _receipt_paths(reader, pair_root, pair64=True)
    pair_epoch_summary_path = _required(pair_root / "epoch-summary.json")
    pair_epoch_summary = reader.json(pair_epoch_summary_path)
    slot_range = pair_epoch_summary.get("slot_range")
    if (
        not isinstance(slot_range, list)
        or len(slot_range) != 2
        or any(type(s) is not int for s in slot_range)
    ):
        raise LedgerFailure("pair64_window_range_invalid", "Pair64")
    inventory = _inventory(
        reader,
        pair_receipts,
        expected_start=slot_range[0],
        expected_end_exclusive=slot_range[1],
        expected_summary_hash=None,
        zstd=zstd,
    )
    if not set(selected).issubset(inventory["returned_slots"]):
        raise LedgerFailure("pair64_selected_slot_outside_inventory", "Pair64")
    specs.append(
        {
            "dataset_id": "pair64",
            "campaign_id": "brain-senses-live-helius-alchemy-512-pair64-20261009",
            "bucket_id": pair_epoch_plan.get("epoch_id"),
            "root": pair_root,
            "selected_slots": selected,
            "receipt_paths": pair_receipts,
            "inventory": inventory,
            "lineage": {
                "plan_path": str(pair_plan_path.relative_to(ROOT)),
                "plan_sha256": plan_hash,
                "summary_sha256": _sha(reader.read(pair_summary_path)),
                "epoch_plan_sha256": _sha(reader.read(pair_epoch_plan_path)),
                "epoch_summary_sha256": _sha(reader.read(pair_epoch_summary_path)),
                "epoch_source_pins": source_pins,
            },
            "zstd": zstd,
        }
    )

    # c3: only the six named completed frames, all in this stopped campaign root.
    campaign_root = ROOT / C3
    campaign_manifest_path = _required(campaign_root / "schedule-v3.json")
    campaign_manifest_bytes = reader.read(campaign_manifest_path)
    campaign_manifest = reader.json(campaign_manifest_path)
    campaign_manifest_sha = _sha(campaign_manifest_bytes)
    campaign_manifest_canonical_sha = _sha(_canonical(campaign_manifest))
    for epoch_id in FRAME_IDS:
        epoch_root = campaign_root / "epochs" / epoch_id
        plan_path = _required(epoch_root / "epoch-plan.json")
        summary_path = _required(epoch_root / "epoch-summary.json")
        frame_path = _required(epoch_root / "frame.json")
        stop_path = _required(epoch_root / "STOP")
        plan_raw = reader.read(plan_path)
        plan = reader.json(plan_path)
        summary = reader.json(summary_path)
        frame = reader.json(frame_path)
        stop = reader.json(stop_path)
        selected_slots = summary.get("selected_slots")
        if (
            summary.get("status") != "frame_complete"
            or summary.get("epoch_id") != epoch_id
            or plan.get("epoch_id") != epoch_id
            or stop.get("status") != "frame_complete"
            or summary.get("selected_slots") != stop.get("selected_slots")
            or summary.get("stop_sha256") != _sha(reader.read(stop_path))
            or frame.get("epoch_id") != epoch_id
            or frame.get("status") != "captured"
            or plan.get("schedule_sha256") != campaign_manifest_canonical_sha
            or not isinstance(selected_slots, list)
            or len(selected_slots) != 16
            or len(set(selected_slots)) != 16
            or plan.get("protocol") != "senses-finalized-inventory-first-v2"
        ):
            raise LedgerFailure("c3_frame_seal_mismatch", epoch_id)
        source_pin = _pin_source(
            reader,
            plan["code_commit"],
            "tools/brain_senses_block_epoch_v2.py",
            plan["code_sha256"],
        )
        receipt_paths = _receipt_paths(reader, epoch_root, pair64=False)
        inventory = _inventory(
            reader,
            receipt_paths,
            expected_start=summary["start"],
            expected_end_exclusive=summary["end_exclusive"],
            expected_summary_hash=summary.get("inventory_sha256"),
            zstd=zstd,
        )
        if len(inventory["returned_slots"]) != summary.get("inventory_denominator"):
            raise LedgerFailure("c3_inventory_denominator_mismatch", epoch_id)
        if not set(selected_slots).issubset(inventory["returned_slots"]):
            raise LedgerFailure("c3_selected_slot_outside_inventory", epoch_id)
        outcomes = summary.get("per_slot_outcomes")
        if not isinstance(outcomes, list) or {row.get("slot") for row in outcomes} != set(
            selected_slots
        ):
            raise LedgerFailure("c3_slot_outcome_set_mismatch", epoch_id)
        if any(
            row.get("full_status") != "captured"
            or row.get("signature_status") != "captured"
            or row.get("full_result_status") != "parsed"
            or row.get("signature_result_status") != "parsed"
            for row in outcomes
        ):
            raise LedgerFailure("c3_slot_outcome_not_complete", epoch_id)
        specs.append(
            {
                "dataset_id": f"c3:{epoch_id}",
                "campaign_id": plan.get("schedule_id"),
                "bucket_id": epoch_id,
                "root": epoch_root,
                "selected_slots": selected_slots,
                "receipt_paths": receipt_paths,
                "inventory": inventory,
                "lineage": {
                    "plan_path": str(plan_path.relative_to(ROOT)),
                    "plan_sha256": _sha(plan_raw),
                    "summary_sha256": _sha(reader.read(summary_path)),
                    "frame_sha256": _sha(reader.read(frame_path)),
                    "stop_sha256": _sha(reader.read(stop_path)),
                    "campaign_manifest_path": str(campaign_manifest_path.relative_to(ROOT)),
                    "campaign_manifest_sha256": campaign_manifest_sha,
                    "campaign_manifest_canonical_sha256": campaign_manifest_canonical_sha,
                    "epoch_source_pins": [source_pin],
                    "inventory_sha256": summary["inventory_sha256"],
                    "inventory_denominator": summary["inventory_denominator"],
                    "interval": [summary["start"], summary["end_exclusive"]],
                },
                "zstd": zstd,
            }
        )
    return specs


def _paired_blocks(reader: _Reader, spec: dict[str, Any]):
    selected_set = set(spec["selected_slots"])
    full_by_slot: dict[int, Path] = {}
    signatures_by_slot: dict[int, Path] = {}
    for receipt_path in spec["receipt_paths"]:
        receipt = reader.json(receipt_path)
        try:
            request = json.loads(receipt["request_json"])
            params = request["params"]
            if request.get("method") != "getBlock" or not isinstance(params, list):
                continue
            slot = params[0]
            options = params[1]
            details = options.get("transactionDetails") if isinstance(options, dict) else None
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LedgerFailure("block_request_invalid", str(receipt_path)) from exc
        if type(slot) is not int or slot not in selected_set:
            continue
        if params[1].get("commitment") != "finalized" or params[1].get("encoding") != "json":
            raise LedgerFailure("block_request_semantics_invalid", str(receipt_path))
        target = (
            full_by_slot
            if details == "full"
            else signatures_by_slot
            if details == "signatures"
            else None
        )
        if target is None or slot in target:
            raise LedgerFailure(
                "unexpected_or_duplicate_block_capture", f"{spec['dataset_id']}:{slot}"
            )
        target[slot] = receipt_path
    if set(full_by_slot) != selected_set or set(signatures_by_slot) != selected_set:
        raise LedgerFailure(
            "full_signature_slot_set_mismatch",
            (
                f"{spec['dataset_id']}:full={len(full_by_slot)} "
                f"signatures={len(signatures_by_slot)} expected={len(selected_set)}"
            ),
        )
    for slot in sorted(selected_set):
        full_path = full_by_slot[slot]
        sig_path = signatures_by_slot[slot]
        full_request, full_receipt, full = _capture(reader, full_path, spec["zstd"])
        sig_request, sig_receipt, sig_block = _capture(reader, sig_path, spec["zstd"])
        if (
            full_request["params"][1].get("transactionDetails") != "full"
            or sig_request["params"][1].get("transactionDetails") != "signatures"
        ):
            raise LedgerFailure(
                "full_signature_request_mode_mismatch", f"{spec['dataset_id']}:{slot}"
            )
        transactions = full.get("transactions")
        signatures = sig_block.get("signatures")
        if (
            not isinstance(transactions, list)
            or not isinstance(signatures, list)
            or not isinstance(full.get("blockhash"), str)
            or full.get("blockhash") != sig_block.get("blockhash")
            or full.get("blockTime") != sig_block.get("blockTime")
            or full.get("parentSlot") != sig_block.get("parentSlot")
        ):
            raise LedgerFailure(
                "full_signature_block_identity_mismatch", f"{spec['dataset_id']}:{slot}"
            )
        tx_signatures = []
        for tx in transactions:
            try:
                values = tx["transaction"]["signatures"]
            except (KeyError, TypeError) as exc:
                raise LedgerFailure(
                    "full_transaction_signature_missing", f"{spec['dataset_id']}:{slot}"
                ) from exc
            if not isinstance(values, list) or not values or not isinstance(values[0], str):
                raise LedgerFailure(
                    "full_transaction_signature_invalid", f"{spec['dataset_id']}:{slot}"
                )
            tx_signatures.append(values[0])
        if tx_signatures != signatures:
            raise LedgerFailure("full_signature_order_mismatch", f"{spec['dataset_id']}:{slot}")
        yield {
            "slot": slot,
            "full_request": full_request,
            "full_path": full_path,
            "full_receipt": full_receipt,
            "full": full,
            "signature_path": sig_path,
            "signature_request": sig_request,
            "signature_receipt": sig_receipt,
            "signatures": signatures,
        }


def _blank_counts() -> dict[str, Any]:
    return {
        "instruction_references": 0,
        "unique_transactions": 0,
        "success_references": 0,
        "failed_references": 0,
        "unknown_status_references": 0,
        "outer_references": 0,
        "inner_references": 0,
        "unique_transactions_by_status_and_path": Counter(),
        "references_by_program_id_source": Counter(),
        "references_by_program_id_source_and_path": Counter(),
        "signature_hashes": defaultdict(set),
    }


def _add_signature_example(counts: dict[str, Any], key: str, signature_hash: bytes) -> None:
    examples = counts["signature_hashes"][key]
    examples.add(signature_hash)
    if len(examples) > EXAMPLE_HASHES_PER_CLASS:
        examples.remove(max(examples))


def _freeze_counts(counts: dict[str, Any]) -> dict[str, Any]:
    tx_classes = {
        f"{status}_{path}": counts["unique_transactions_by_status_and_path"][f"{status}_{path}"]
        for status in ("success", "failed", "unknown")
        for path in ("outer", "inner")
    }
    return {
        "instruction_references": counts["instruction_references"],
        "unique_transactions": counts["unique_transactions"],
        "unique_transactions_by_status_and_path": tx_classes,
        "references_by_status": {
            "success": counts["success_references"],
            "failed": counts["failed_references"],
            "unknown": counts["unknown_status_references"],
        },
        "references_by_path": {
            "outer": counts["outer_references"],
            "inner": counts["inner_references"],
        },
        "references_by_program_id_source": {
            source: counts["references_by_program_id_source"][source]
            for source in PROGRAM_ID_SOURCES
            if counts["references_by_program_id_source"][source] > 0
        },
        "references_by_program_id_source_and_path": {
            f"{source}_{path}": counts["references_by_program_id_source_and_path"][
                f"{source}_{path}"
            ]
            for source in PROGRAM_ID_SOURCES
            for path in INSTRUCTION_PATH_ENUM
            if counts["references_by_program_id_source_and_path"][f"{source}_{path}"] > 0
        },
        "signature_hash_examples_by_status_and_path": {
            key: [value.hex() for value in sorted(values)[:EXAMPLE_HASHES_PER_CLASS]]
            for key, values in sorted(counts["signature_hashes"].items())
        },
    }


def _validate_program_reference_counts(
    programs: dict[str, dict[str, Any]], outer_count: int, inner_count: int
) -> None:
    expected_total = outer_count + inner_count
    total = 0
    for program_id, counts in programs.items():
        references = counts["instruction_references"]
        if references != sum(
            counts[key]
            for key in ("success_references", "failed_references", "unknown_status_references")
        ):
            raise LedgerFailure("program_status_reference_count_mismatch", program_id)
        if references != sum(counts[key] for key in ("outer_references", "inner_references")):
            raise LedgerFailure("program_path_reference_count_mismatch", program_id)
        if references != sum(counts["references_by_program_id_source"].values()):
            raise LedgerFailure("program_source_reference_count_mismatch", program_id)
        if references != sum(counts["references_by_program_id_source_and_path"].values()):
            raise LedgerFailure("program_source_path_reference_count_mismatch", program_id)
        total += references
    if total != expected_total:
        raise LedgerFailure("program_reference_total_mismatch", f"{total}!={expected_total}")


def _compact_slot_rows(slot_rows: list[dict[str, Any]]) -> list[list[Any]]:
    compact = []
    integer_fields = {
        "slot",
        "provider_signature_count",
        "provider_full_transaction_count",
        "transaction_count",
        "normalized_transactions",
        "failed_transactions",
        "unsupported_normalization",
        "outer_instruction_references",
        "inner_instruction_references",
        "program_count",
    }
    nullable_integer_fields = {
        "event_time_unix_seconds",
        "full_receipt_ns",
        "signature_receipt_ns",
        "availability_ns",
    }
    hash_fields = {
        "blockhash",
        "full_request_json_sha256",
        "signature_request_json_sha256",
        "full_response_sha256",
        "full_stored_sha256",
        "signature_response_sha256",
        "signature_stored_sha256",
    }
    for row in slot_rows:
        if set(row) != set(SLOT_LEDGER_FIELDS):
            raise LedgerFailure("slot_ledger_field_schema_mismatch", str(row.get("slot")))
        if any(type(row[key]) is not int for key in integer_fields):
            raise LedgerFailure("slot_ledger_integer_type_invalid", str(row.get("slot")))
        if any(
            value is not None and type(value) is not int
            for key in nullable_integer_fields
            if (value := row[key]) is not None
        ):
            raise LedgerFailure("slot_ledger_nullable_integer_type_invalid", str(row.get("slot")))
        if any(not isinstance(row[key], str) for key in hash_fields):
            raise LedgerFailure("slot_ledger_hash_type_invalid", str(row.get("slot")))
        exposures = row["program_exposures"]
        if (
            not isinstance(exposures, list)
            or any(
                not isinstance(exposure, list)
                or len(exposure) != 2
                or type(exposure[0]) is not int
                or type(exposure[1]) is not int
                for exposure in exposures
            )
            or row["program_count"] != len(exposures)
        ):
            raise LedgerFailure("slot_ledger_program_exposure_shape_invalid", str(row.get("slot")))
        values = [row[field] for field in SLOT_LEDGER_FIELDS]
        if len(values) != len(SLOT_LEDGER_FIELDS):
            raise LedgerFailure("slot_ledger_arity_invalid", str(row.get("slot")))
        compact.append(values)
    return compact


def _transaction_instruction_paths(
    instructions: list[dict[str, Any]], *, dataset_id: str, slot: int, tx_index: int
) -> tuple[list[tuple[str, str, str]], dict[str, set[str]]]:
    seen_paths: set[tuple[int, ...]] = set()
    references = []
    paths_by_program: dict[str, set[str]] = defaultdict(set)
    for instruction in instructions:
        program_id = instruction.get("program_id")
        path = instruction.get("path")
        source = instruction.get("program_id_source")
        if not isinstance(program_id, str) or not program_id:
            raise LedgerFailure("normalized_program_id_invalid", f"{dataset_id}:{slot}:{tx_index}")
        if source not in PROGRAM_ID_SOURCES:
            raise LedgerFailure(
                "normalized_program_id_source_invalid", f"{dataset_id}:{slot}:{tx_index}"
            )
        if not isinstance(path, list) or not path or any(type(i) is not int or i < 0 for i in path):
            raise LedgerFailure(
                "normalized_instruction_path_invalid", f"{dataset_id}:{slot}:{tx_index}"
            )
        identity = tuple(path)
        if identity in seen_paths:
            raise LedgerFailure("duplicate_instruction_identity", f"{dataset_id}:{slot}:{tx_index}")
        seen_paths.add(identity)
        path_kind = "outer" if len(path) == 1 else "inner"
        references.append((program_id, path_kind, source))
        paths_by_program[program_id].add(path_kind)
    return references, paths_by_program


def _validate_decision_time(decision_time_ns: int, evidence_times: list[int | None]) -> None:
    known_times = [value for value in evidence_times if type(value) is int]
    if known_times and decision_time_ns < max(known_times):
        raise LedgerFailure(
            "decision_time_precedes_evidence",
            f"decision={decision_time_ns};latest_evidence={max(known_times)}",
        )


def _reproducible_content_sha256(receipt: dict[str, Any]) -> str:
    payload = dict(receipt)
    for volatile in (
        "decision_time_ns",
        "decision_time_provenance",
        "replay_measurements",
        "reproducible_content_sha256",
    ):
        payload.pop(volatile, None)
    return _sha(_canonical(payload))


def _root_aggregate_counts(total_counts: Counter) -> dict[str, int]:
    non_additive_names = {
        "unique_program_ids_with_references",
        "unique_referencing_transactions",
    }
    return {
        **{
            key: value
            for key, value in sorted(total_counts.items())
            if key not in non_additive_names
        },
        "sum_dataset_program_id_table_entries": total_counts["unique_program_ids_with_references"],
        "sum_dataset_slot_unique_referencing_transaction_observations": total_counts[
            "unique_referencing_transactions"
        ],
    }


def _scan_dataset(reader: _Reader, spec: dict[str, Any]) -> dict[str, Any]:
    blocks = _paired_blocks(reader, spec)
    programs: dict[str, dict[str, Any]] = defaultdict(_blank_counts)
    slot_rows = []
    dataset_signature_hashes: set[bytes] = set()
    referencing_transaction_count = 0
    normalization_errors = Counter()
    fatal_dataset_errors = Counter()
    status_counts = Counter()
    raw_transaction_version_status = Counter()
    raw_instruction_incidence = Counter()
    transaction_count = 0
    normalized_count = 0
    outer_count = 0
    inner_count = 0
    failed_tx_count = 0
    unknown_status_tx_count = 0
    for block_row in blocks:
        slot = block_row["slot"]
        block = block_row["full"]
        slot_programs: dict[str, dict[str, Any]] = defaultdict(_blank_counts)
        slot_transactions = 0
        slot_normalized = 0
        slot_outer = 0
        slot_inner = 0
        slot_failed = 0
        slot_unsupported = 0
        slot_signature_hashes: set[bytes] = set()
        slot_referencing_hashes: set[bytes] = set()
        txs = block["transactions"]
        transaction_count += len(txs)
        for tx_index, transaction in enumerate(txs):
            slot_transactions += 1
            meta = transaction.get("meta") if isinstance(transaction, dict) else None
            raw_err_present = isinstance(meta, dict) and "err" in meta
            raw_status = (
                "unknown" if not raw_err_present else "success" if meta["err"] is None else "failed"
            )
            status_counts[raw_status] += 1
            if raw_status == "failed":
                failed_tx_count += 1
                slot_failed += 1
            elif raw_status == "unknown":
                unknown_status_tx_count += 1
            version_label, version_status, raw_paths = _raw_version_status_path_counts(transaction)
            raw_transaction_version_status[f"{version_label}_{version_status}"] += 1
            for path_kind, count in raw_paths.items():
                raw_instruction_incidence[f"{version_label}_{version_status}_{path_kind}"] += count
            format_issue = _alt_format_issue(transaction)
            if format_issue is not None:
                normalization_errors[format_issue] += 1
                fatal_dataset_errors[format_issue] += 1
                slot_unsupported += 1
                continue
            outer_issue = _v0_outer_program_index_issue(transaction)
            if outer_issue is not None:
                error_code, outer_index = outer_issue
                normalization_errors[error_code] += 1
                fatal_dataset_errors[error_code] += 1
                slot_unsupported += 1
                continue
            try:
                normalized = normalize_compiled_transaction(transaction)
            except NormalizationError as exc:
                normalization_errors[exc.category] += 1
                slot_unsupported += 1
                continue
            normalized_count += 1
            slot_normalized += 1
            if normalized.get("meta_error") is not None:
                status = "failed"
            else:
                status = raw_status
            signatures = normalized.get("transaction_signatures")
            if (
                not isinstance(signatures, list)
                or not signatures
                or not isinstance(signatures[0], str)
            ):
                normalization_errors["transaction_signature_invalid"] += 1
                slot_unsupported += 1
                normalized_count -= 1
                slot_normalized -= 1
                continue
            signature = signatures[0]
            sig_hash = bytes.fromhex(_sha(signature.encode()))
            if sig_hash in slot_signature_hashes:
                raise LedgerFailure(
                    "duplicate_transaction_signature_in_slot",
                    f"{spec['dataset_id']}:{slot}:{sig_hash.hex()}",
                )
            slot_signature_hashes.add(sig_hash)
            dataset_signature_hashes.add(sig_hash)
            instructions = [*normalized["outer_instructions"], *normalized["inner_instructions"]]
            if instructions:
                slot_referencing_hashes.add(sig_hash)
            resolved_instructions = []
            for instruction in instructions:
                program_index = instruction["program_id_index"]
                resolved_instructions.append(
                    {
                        **instruction,
                        "program_id_source": normalized["accounts"][program_index]["source"],
                    }
                )
            transaction_references, program_paths_for_transaction = _transaction_instruction_paths(
                resolved_instructions,
                dataset_id=spec["dataset_id"],
                slot=slot,
                tx_index=tx_index,
            )
            for program_id, path_kind, program_source in transaction_references:
                program_counts = programs[program_id]
                slot_counts = slot_programs[program_id]
                for counts in (program_counts, slot_counts):
                    counts["instruction_references"] += 1
                    counts[f"{status}_references"] += 1
                    counts[f"{path_kind}_references"] += 1
                    counts["references_by_program_id_source"][program_source] += 1
                    counts["references_by_program_id_source_and_path"][
                        f"{program_source}_{path_kind}"
                    ] += 1
                    _add_signature_example(counts, f"{status}_{path_kind}", sig_hash)
                if path_kind == "outer":
                    outer_count += 1
                    slot_outer += 1
                else:
                    inner_count += 1
                    slot_inner += 1
            for program_id, path_kinds in program_paths_for_transaction.items():
                for counts in (programs[program_id], slot_programs[program_id]):
                    counts["unique_transactions"] += 1
                    for path_kind in path_kinds:
                        counts["unique_transactions_by_status_and_path"][
                            f"{status}_{path_kind}"
                        ] += 1
        referencing_transaction_count += len(slot_referencing_hashes)
        block = block_row["full"]
        slot_rows.append(
            {
                "slot": slot,
                "blockhash": block["blockhash"],
                "event_time_unix_seconds": block.get("blockTime"),
                "full_request_json_sha256": _request_identity_sha256(block_row["full_request"]),
                "signature_request_json_sha256": _request_identity_sha256(
                    block_row["signature_request"]
                ),
                "full_receipt_ns": block_row["full_receipt"].get("received_ns"),
                "signature_receipt_ns": block_row["signature_receipt"].get("received_ns"),
                "availability_ns": None,
                "full_response_sha256": block_row["full_receipt"].get("response_sha256"),
                "full_stored_sha256": block_row["full_receipt"]["storage"].get("sha256"),
                "signature_response_sha256": block_row["signature_receipt"].get("response_sha256"),
                "signature_stored_sha256": block_row["signature_receipt"]["storage"].get("sha256"),
                "provider_signature_count": len(block_row["signatures"]),
                "provider_full_transaction_count": len(txs),
                "transaction_count": slot_transactions,
                "normalized_transactions": slot_normalized,
                "failed_transactions": slot_failed,
                "unsupported_normalization": slot_unsupported,
                "outer_instruction_references": slot_outer,
                "inner_instruction_references": slot_inner,
                "program_count": len(slot_programs),
                "unique_transactions_by_program": {
                    program_id: slot_programs[program_id]["unique_transactions"]
                    for program_id in sorted(slot_programs)
                },
            }
        )
    unique_transactions_by_program = {
        program_id: counts["unique_transactions"] for program_id, counts in programs.items()
    }
    raw_instruction_count = sum(raw_instruction_incidence.values())
    _validate_program_reference_counts(programs, outer_count, inner_count)
    if normalization_errors.total() == 0 and raw_instruction_count != outer_count + inner_count:
        fatal_dataset_errors["raw_normalized_instruction_count_mismatch"] += 1
    normalization_failed = sum(normalization_errors.values())
    slot_rows.sort(key=lambda item: item["slot"])
    program_rows = []
    program_id_table = sorted(programs)
    program_id_index = {program_id: index for index, program_id in enumerate(program_id_table)}
    for program_id in program_id_table:
        counts = programs[program_id]
        program_rows.append(
            {
                "program_id": program_id,
                "venue_label": "pump_amm"
                if program_id == PUMP_AMM_PROGRAM_ID
                else "unknown_program",
                **_freeze_counts(counts),
            }
        )
    for row in slot_rows:
        by_program = row.pop("unique_transactions_by_program")
        row["program_exposures"] = [
            [program_id_index[program_id], unique_transactions]
            for program_id, unique_transactions in sorted(
                by_program.items(), key=lambda item: program_id_index[item[0]]
            )
        ]
    zero_reference_slots = [row["slot"] for row in slot_rows if not row["program_count"]]
    compact_slot_rows = _compact_slot_rows(slot_rows)
    return {
        "dataset_id": spec["dataset_id"],
        "campaign_id": spec["campaign_id"],
        "bucket_id": spec["bucket_id"],
        "status": "failed"
        if fatal_dataset_errors
        else "complete"
        if normalization_failed == 0 and unknown_status_tx_count == 0
        else "partial",
        "failure_categories": dict(sorted(fatal_dataset_errors.items())),
        "inventory": {
            key: value for key, value in spec["inventory"].items() if key != "returned_slots"
        },
        "selected_slots": len(slot_rows),
        "counts": {
            "provider_returned_getBlocks_members": spec["inventory"][
                "provider_returned_member_count"
            ],
            "selected_slots": len(slot_rows),
            "full_signature_pairs_checked": len(slot_rows),
            "full_block_transactions": transaction_count,
            "normalized_transactions": normalized_count,
            "unsupported_normalization": normalization_failed,
            "normalization_failure_categories": dict(sorted(normalization_errors.items())),
            "success_transactions": status_counts["success"],
            "failed_transactions": failed_tx_count,
            "unknown_status_transactions": unknown_status_tx_count,
            "outer_instruction_references": outer_count,
            "inner_instruction_references": inner_count,
            "instruction_references_total": outer_count + inner_count,
            "unique_program_ids_with_references": len(programs),
            "unique_referencing_transactions": referencing_transaction_count,
            "raw_transactions_by_version_and_status": {
                f"{version}_{status}": raw_transaction_version_status[f"{version}_{status}"]
                for version in ("legacy", "v0", "v1", "unknown")
                for status in ("success", "failed", "unknown")
            },
            "raw_instruction_incidence_by_version_status_and_path": {
                f"{version}_{status}_{path}": raw_instruction_incidence[
                    f"{version}_{status}_{path}"
                ]
                for version in ("legacy", "v0", "v1", "unknown")
                for status in ("success", "failed", "unknown")
                for path in INSTRUCTION_PATH_ENUM
            },
            "raw_instruction_references_total": raw_instruction_count,
        },
        "program_concentration": [
            {"program_id": key, "unique_transactions": value}
            for key, value in sorted(
                unique_transactions_by_program.items(), key=lambda item: (-item[1], item[0])
            )[:25]
        ],
        "program_id_table": program_id_table,
        "program_id_table_order": "ascending exact program_id string; index is zero-based",
        "selected_slot_ledger_fields": list(SLOT_LEDGER_FIELDS),
        "selected_slot_ledger_validation": "full_signature_order_check matched for every row",
        "program_exposure_encoding": (
            "selected_slot_ledger.program_exposures entries are [program_id_table_index, "
            "unique_transaction_count]"
        ),
        "program_id_source_semantics": {
            "enum": list(PROGRAM_ID_SOURCES),
            "zero_count_fields": "omitted from sparse per-program source maps",
            "inner_instruction_caveat": (
                "loaded source labels are diagnostic account-index resolution and do not "
                "claim that loaded-address CPI execution is confirmed by these captures"
            ),
        },
        "selected_slots_with_zero_program_references": zero_reference_slots,
        "status_enum": list(PROGRAM_STATUS_ENUM),
        "instruction_path_enum": list(INSTRUCTION_PATH_ENUM),
        "selected_slot_ledger": compact_slot_rows,
        "programs": program_rows,
        "lineage": spec["lineage"],
        "_signature_hashes": dataset_signature_hashes,
    }


def build_receipt(
    decision_time_ns: int, *, decision_time_source: str
) -> tuple[dict[str, Any], int]:
    if type(decision_time_ns) is not int or decision_time_ns <= 0:
        raise LedgerFailure("decision_time_invalid", str(decision_time_ns))
    if not decision_time_source:
        raise LedgerFailure("decision_time_source_invalid", "empty")
    zstd = shutil.which("zstd")
    if not zstd:
        raise LedgerFailure("zstd_unavailable", "zstd CLI is required")
    reader = _Reader()
    specs = _load_specs(reader, zstd)
    datasets = [_scan_dataset(reader, spec) for spec in specs]
    signature_overlaps = []
    for left_index, left in enumerate(datasets):
        for right in datasets[left_index + 1 :]:
            overlap = left["_signature_hashes"] & right["_signature_hashes"]
            signature_overlaps.append(
                {
                    "left_dataset_id": left["dataset_id"],
                    "right_dataset_id": right["dataset_id"],
                    "overlapping_signature_hash_count": len(overlap),
                    "overlapping_signature_hashes": [value.hex() for value in sorted(overlap)],
                    "observations_remain_separate": True,
                }
            )
        left.pop("_signature_hashes")
    # The per-dataset slot rows keep the full selected-slot denominator. Global rows are
    # summarized from those deterministic per-dataset program aggregates below.
    global_rows = []
    for program_id in sorted({row["program_id"] for ds in datasets for row in ds["programs"]}):
        appearances = []
        reference_total = 0
        unique_tx_total = 0
        classes: dict[str, dict[str, Any]] = {}
        program_source_totals = Counter()
        program_source_path_totals = Counter()
        first_program_seen = None
        for dataset in datasets:
            match = next(
                (row for row in dataset["programs"] if row["program_id"] == program_id), None
            )
            if match is None:
                continue
            first_program_seen = first_program_seen or dataset["dataset_id"]
            reference_total += match["instruction_references"]
            unique_tx_total += match["unique_transactions"]
            program_source_totals.update(match["references_by_program_id_source"])
            program_source_path_totals.update(match["references_by_program_id_source_and_path"])
            appearances.append(
                {
                    "dataset_id": dataset["dataset_id"],
                    "instruction_references": match["instruction_references"],
                    "unique_transactions": match["unique_transactions"],
                    "references_by_status": match["references_by_status"],
                    "references_by_path": match["references_by_path"],
                    "references_by_program_id_source": match["references_by_program_id_source"],
                    "references_by_program_id_source_and_path": match[
                        "references_by_program_id_source_and_path"
                    ],
                }
            )
            for class_name, hashes in match["signature_hash_examples_by_status_and_path"].items():
                item = classes.setdefault(class_name, {"signature_hash_examples": set()})
                item["signature_hash_examples"].update(hashes)
        global_rows.append(
            {
                "program_id": program_id,
                "venue_label": "pump_amm"
                if program_id == PUMP_AMM_PROGRAM_ID
                else "unknown_program",
                "first_seen_in_sampled_corpus": first_program_seen,
                "total_instruction_references": reference_total,
                "sum_of_per_dataset_unique_transactions": unique_tx_total,
                "sum_dataset_references_by_program_id_source": dict(
                    sorted(program_source_totals.items())
                ),
                "sum_dataset_references_by_program_id_source_and_path": dict(
                    sorted(program_source_path_totals.items())
                ),
                "per_dataset": appearances,
                "signature_hash_examples_by_status_and_path": {
                    name: sorted(values["signature_hash_examples"])[:EXAMPLE_HASHES_PER_CLASS]
                    for name, values in sorted(classes.items())
                },
            }
        )
    total_counts = Counter()
    evidence_times: list[int | None] = []
    for dataset in datasets:
        evidence_times.append(dataset["inventory"].get("receipt_ns"))
        for row in dataset["selected_slot_ledger"]:
            evidence_times.extend(
                row[SLOT_LEDGER_FIELDS.index(field)]
                for field in ("full_receipt_ns", "signature_receipt_ns", "availability_ns")
            )
        for key, value in dataset["counts"].items():
            if type(value) is int:
                total_counts[key] += value
    _validate_decision_time(decision_time_ns, evidence_times)
    receipt = {
        "schema": SCHEMA,
        "status": "complete_structural_exposure_only"
        if all(dataset["status"] == "complete" for dataset in datasets)
        else "partial_structural_exposure",
        "decision_time_ns": decision_time_ns,
        "decision_time_provenance": {
            "source": decision_time_source,
            "units": "UTC Unix epoch nanoseconds",
            "lower_bound": "not earlier than any included capture receipt or availability time",
        },
        "scope": {
            "datasets": ["Pair64 open sealed Helius capture", "c3 six completed sealed frames"],
            "selected_blocks_only": True,
            "program_id_venue_resolution": {
                PUMP_AMM_PROGRAM_ID: {
                    "venue_label": "pump_amm",
                    "source": "pinned Pump AMM IDL primary program ID",
                    "commit": PUMP_AMM_SCHEMA["provenance"]["commit"],
                    "source_sha256": PUMP_AMM_SCHEMA["provenance"]["sha256"],
                },
                "all_other_program_ids": "unknown_program",
            },
            "identity": "(campaign_id,bucket_id,slot,signature,path)",
            "availability_time_rule": (
                "null unless independently present in the sealed receipt; never infer "
                "from event or receipt time"
            ),
            "first_seen_semantics": (
                "first-seen in this ordered sampled corpus only; not chain birth"
            ),
        },
        "source_pins": {
            "repository_head": subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
            ).stdout.strip(),
            "tool_sha256": _sha(Path(__file__).read_bytes()),
            "normalizer_path": "src/memecoin_pumpswap/normalize.py",
            "normalizer_sha256": _sha((ROOT / "src/memecoin_pumpswap/normalize.py").read_bytes()),
            "decoder_path": "src/memecoin_pumpswap/decoder.py",
            "decoder_sha256": _sha((ROOT / "src/memecoin_pumpswap/decoder.py").read_bytes()),
            "pump_idl_schema_path": "src/memecoin_pumpswap/schema.py",
            "pump_idl_schema_file_sha256": _sha(
                (ROOT / "src/memecoin_pumpswap/schema.py").read_bytes()
            ),
            "pump_idl_commit": PUMP_AMM_SCHEMA["provenance"]["commit"],
            "pump_idl_sha256": PUMP_AMM_SCHEMA["provenance"]["sha256"],
            "pump_amm_program_id": PUMP_AMM_PROGRAM_ID,
        },
        "counts": _root_aggregate_counts(total_counts),
        "aggregation_semantics": (
            "Every additive root count sums dataset observations. Cross-dataset signature "
            "overlaps are reported separately and are not deduplicated; names prefixed "
            "sum_dataset_ are explicitly sums, not global distinct counts."
        ),
        "program_concentration": [
            {
                "program_id": row["program_id"],
                "venue_label": row["venue_label"],
                "unique_transactions": row["sum_of_per_dataset_unique_transactions"],
                "instruction_references": row["total_instruction_references"],
            }
            for row in sorted(
                global_rows,
                key=lambda item: (
                    -item["sum_of_per_dataset_unique_transactions"],
                    item["program_id"],
                ),
            )[:25]
        ],
        "programs": global_rows,
        "dataset_signature_overlaps": signature_overlaps,
        "datasets": datasets,
        "limitations": [
            (
                "Program references are structural exposures inside verified selected blocks only; "
                "a zero means no reference in those blocks, not global absence."
            ),
            (
                "Program IDs are not market identities, pools, trades, births, or evidence of "
                "cross-slot population continuity."
            ),
            (
                "Only the program ID independently pinned by the official Pump AMM IDL receives "
                "a venue label; all other IDs remain unknown_program."
            ),
            (
                "Transaction status comes from captured meta.err and does not establish execution "
                "settlement or economic effect."
            ),
            (
                "Capture receipt time is not availability time; blockTime is nullable event time; "
                "decision time is recorded separately."
            ),
        ],
    }
    receipt["reproducible_content_sha256"] = _reproducible_content_sha256(receipt)
    return receipt, reader.bytes_read


def run(output: Path, decision_time_ns: int | None = None) -> dict[str, Any]:
    if output.exists():
        raise LedgerFailure("output_exists_refusing_overwrite", str(output))
    if decision_time_ns is None:
        decision_time_ns = time.time_ns()
        decision_time_source = "time.time_ns() sampled at analyzer process start"
    else:
        decision_time_source = "explicit --decision-time-ns forensic/replay input"
    cpu_before = time.process_time_ns()
    wall_before = time.perf_counter_ns()
    usage_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    receipt, bytes_read = build_receipt(decision_time_ns, decision_time_source=decision_time_source)
    usage_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    receipt["replay_measurements"] = {
        "local_input_bytes_read": bytes_read,
        "python_cpu_time_ns": time.process_time_ns() - cpu_before,
        "wall_time_ns": time.perf_counter_ns() - wall_before,
        "python_peak_rss_bytes": max(usage_before, usage_after)
        * (1 if sys.platform == "darwin" else 1024),
        "measurement_scope": "analyzer Python process; zstd child CPU/RSS not included",
        "digest_bytes": 0,
        "fail_closed_digest_cap_bytes": OUTPUT_CAP_BYTES,
    }
    receipt["replay_measurements"]["digest_bytes"] = len(_canonical(receipt)) + 1
    encoded = _canonical(receipt) + b"\n"
    if receipt["replay_measurements"]["digest_bytes"] != len(encoded):
        receipt["replay_measurements"]["digest_bytes"] = len(encoded)
        encoded = _canonical(receipt) + b"\n"
    if len(encoded) > OUTPUT_CAP_BYTES:
        raise LedgerFailure("digest_output_cap_exceeded", f"{len(encoded)}>{OUTPUT_CAP_BYTES}")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
    except FileExistsError as exc:
        raise LedgerFailure("output_exists_refusing_overwrite", str(output)) from exc
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--decision-time-ns",
        type=int,
        help="explicit UTC Unix epoch nanoseconds for deterministic forensic replay",
    )
    args = parser.parse_args(argv)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    result = run(output, args.decision_time_ns)
    print(
        _canonical(
            {
                "status": result["status"],
                "output": str(output),
                "counts": result["counts"],
                "program_count": len(result["programs"]),
                "digest_bytes": result["replay_measurements"]["digest_bytes"],
                "fail_closed_digest_cap_bytes": OUTPUT_CAP_BYTES,
            }
        ).decode(),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
