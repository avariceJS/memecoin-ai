#!/usr/bin/env python3
"""Bounded offline structural novelty digest over frozen Pair64 and c3 captures."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from memecoin_pumpswap.decoder import PROGRAM  # noqa: E402
from memecoin_pumpswap.normalize import (  # noqa: E402
    NormalizationError,
    normalize_compiled_transaction,
)
from memecoin_pumpswap.schema import SCHEMA  # noqa: E402

SCHEMA_VERSION = "pumpswap-structural-novelty-v1"
PAIR64_ROOT = Path("work/brain-senses-live-helius-alchemy-512-pair64-20261009/20261009T150000Z")
C3_ROOT = Path("work/brain-senses-live-helius-inventory-v3-20261010-3")
PAIR64_CENSUS = PAIR64_ROOT / "paired-block-capture/buy_exact_v1_census.json"
PAIR64_AUDIT = Path("docs/evidence/helius-alchemy-pair64-offline-audit-2026-10-09.json")
C3_SELL_CENSUS = Path("docs/evidence/pumpswap-sell23-c3-six-frame-ledger-replay-2026-10-10.json")
C3_BUY_CENSUSES = {
    "20261010T050000Z": Path("docs/evidence/pumpswap-buy-exact-v1-c3-frame-census-2026-10-10.json"),
    "20261010T060000Z": Path(
        "docs/evidence/pumpswap-buy-exact-v1-c3-frame2-census-2026-10-10.json"
    ),
}
FRAME_IDS = tuple(f"20261010T{hour:02d}0000Z" for hour in range(5, 11))
IDL_INSTRUCTIONS = SCHEMA["instructions"]
DISCRIMINATORS = {bytes(value["discriminator"]): name for name, value in IDL_INSTRUCTIONS.items()}
PUMP_PROGRAM = SCHEMA["address"]


class MonitorFailure(ValueError):
    """An input inconsistency that prevents a qualified replay."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitorFailure("input_json_invalid", str(path)) from exc


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _required(path: Path) -> Path:
    if not path.exists():
        raise MonitorFailure("required_input_missing", str(path))
    return path


def _signature_hash(signature: str) -> str:
    return _sha(signature.encode("utf-8"))


def _source_code_pin(commit: str, relative_path: str, expected_sha256: str) -> dict[str, str]:
    if len(commit) != 40:
        raise MonitorFailure("source_commit_invalid", commit)
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative_path}"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0 or _sha(completed.stdout) != expected_sha256:
        raise MonitorFailure("campaign_code_pin_mismatch", f"{commit}:{relative_path}")
    return {"commit": commit, "path": relative_path, "sha256": expected_sha256}


def _capture_receipts(capture_root: Path, *, pair64: bool) -> list[Path]:
    if pair64:
        provider_root = capture_root / "captures/helius-free"
    else:
        provider_root = capture_root / "captures"
    _required(provider_root)
    result = []
    for receipt_path in sorted(provider_root.glob("*/receipt.json")):
        receipt = _json(receipt_path)
        if receipt.get("source_alias") != "helius-free":
            continue
        try:
            request = json.loads(receipt["request_json"])
        except (KeyError, json.JSONDecodeError, TypeError) as exc:
            raise MonitorFailure("capture_request_invalid", str(receipt_path)) from exc
        params = request.get("params")
        if (
            receipt.get("method") == "getBlock"
            and request.get("method") == "getBlock"
            and isinstance(params, list)
            and len(params) == 2
            and isinstance(params[1], dict)
            and params[1].get("transactionDetails") == "full"
            and params[1].get("commitment") == "finalized"
            and params[1].get("encoding") == "json"
        ):
            result.append(receipt_path)
    return result


def _read_capture(receipt_path: Path, zstd: str) -> tuple[int, dict[str, Any], dict[str, Any]]:
    receipt = _json(receipt_path)
    try:
        request = json.loads(receipt["request_json"])
        params = request["params"]
        slot = params[0]
        storage = receipt["storage"]
        capture = receipt_path.parent / storage["path"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise MonitorFailure("capture_receipt_shape", str(receipt_path)) from exc
    if type(slot) is not int or not capture.is_file():
        raise MonitorFailure("capture_slot_or_body_missing", str(receipt_path))
    compressed = capture.read_bytes()
    if (
        storage.get("codec") != "zstd"
        or len(compressed) != storage.get("bytes")
        or _sha(compressed) != storage.get("sha256")
    ):
        raise MonitorFailure("stored_capture_hash_mismatch", str(receipt_path))
    completed = subprocess.run([zstd, "-dc", str(capture)], check=False, capture_output=True)
    if completed.returncode != 0:
        raise MonitorFailure("capture_decompression_failed", str(receipt_path))
    raw = completed.stdout
    if len(raw) != receipt.get("response_bytes") or _sha(raw) != receipt.get("response_sha256"):
        raise MonitorFailure("raw_capture_hash_mismatch", str(receipt_path))
    try:
        wrapper = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MonitorFailure("raw_capture_json_invalid", str(receipt_path)) from exc
    block = wrapper.get("result") if isinstance(wrapper, dict) else None
    if not isinstance(block, dict) or not isinstance(block.get("transactions"), list):
        raise MonitorFailure("full_block_shape_invalid", str(receipt_path))
    if not isinstance(block.get("blockhash"), str) or not block["blockhash"]:
        raise MonitorFailure("block_identity_missing", str(slot))
    if type(receipt.get("received_ns")) is not int or receipt["received_ns"] <= 0:
        raise MonitorFailure("capture_receipt_time_invalid", str(receipt_path))
    if block.get("parentSlot") is not None and type(block["parentSlot"]) is not int:
        raise MonitorFailure("block_parent_slot_invalid", str(slot))
    if block.get("blockHeight") is not None and type(block["blockHeight"]) is not int:
        raise MonitorFailure("block_height_invalid", str(slot))
    if block.get("blockTime") is not None and type(block["blockTime"]) is not int:
        raise MonitorFailure("block_time_invalid", str(slot))
    return slot, block, receipt


def _privileges(transaction: dict[str, Any], indexes: list[int]) -> list[dict[str, bool]]:
    message = transaction["transaction"]["message"]
    static = message["accountKeys"]
    if not isinstance(static, list) or not static:
        raise MonitorFailure("static_account_keys_missing", "instruction privilege resolution")
    header = message.get("header")
    meta = transaction.get("meta")
    if not isinstance(header, dict) or not isinstance(meta, dict):
        raise MonitorFailure(
            "account_privilege_context_missing", "instruction privilege resolution"
        )
    try:
        required = header["numRequiredSignatures"]
        readonly_signed = header["numReadonlySignedAccounts"]
        readonly_unsigned = header["numReadonlyUnsignedAccounts"]
        loaded = meta.get("loadedAddresses") or {"writable": [], "readonly": []}
        loaded_writable = loaded["writable"]
        loaded_readonly = loaded["readonly"]
    except (KeyError, TypeError) as exc:
        raise MonitorFailure(
            "account_privilege_context_invalid", "instruction privilege resolution"
        ) from exc
    if any(type(value) is not int for value in (required, readonly_signed, readonly_unsigned)):
        raise MonitorFailure("account_header_invalid", "instruction privilege resolution")
    total = len(static) + len(loaded_writable) + len(loaded_readonly)
    if any(type(index) is not int or not 0 <= index < total for index in indexes):
        raise MonitorFailure(
            "instruction_account_index_invalid", "instruction privilege resolution"
        )
    result = []
    for index in indexes:
        if index >= len(static):
            loaded_index = index - len(static)
            result.append({"signer": False, "writable": loaded_index < len(loaded_writable)})
        else:
            signer = index < required
            writable = (
                index < required - readonly_signed
                if signer
                else index < len(static) - readonly_unsigned
            )
            result.append({"signer": signer, "writable": writable})
    return result


def _instruction_class(
    normalized: dict[str, Any], instruction: dict[str, Any], tx_status: str
) -> tuple[dict[str, Any], str, str | None]:
    data = bytes.fromhex(instruction["data_hex"])
    kind = DISCRIMINATORS.get(data[:8])
    idl = IDL_INSTRUCTIONS.get(kind) if kind else None
    fixed_count = len(idl["accounts"]) if idl is not None else None
    accounts = instruction["accounts"]
    indexes = instruction["account_indexes"]
    if len(accounts) != len(indexes):
        raise MonitorFailure("normalized_account_count_mismatch", str(instruction["path"]))
    suffix_start = fixed_count if fixed_count is not None else 0
    trailing = data[8:].hex()
    token_pair = None
    pool_key = None
    if idl is not None:
        account_names = idl["accounts"]
        if len(accounts) >= len(account_names):
            if "base_token_program" in account_names and "quote_token_program" in account_names:
                token_pair = [
                    accounts[account_names.index("base_token_program")],
                    accounts[account_names.index("quote_token_program")],
                ]
            if account_names and account_names[0] == "pool":
                pool_key = accounts[0]
    signature_list = normalized.get("transaction_signatures")
    signature = signature_list[0] if isinstance(signature_list, list) and signature_list else None
    if not isinstance(signature, str):
        raise MonitorFailure("referencing_transaction_signature_missing", str(instruction["path"]))
    cls = {
        "instruction_kind": kind or "unknown_discriminator",
        "discriminator_hex": data[:8].hex(),
        "wire_data_length": len(data),
        "trailing_wire_hex": trailing,
        "fixed_account_count": fixed_count,
        "observed_account_count": len(accounts),
        "suffix": [
            {
                "position": position,
                "privilege": instruction["_account_privileges"][position],
            }
            for position in range(suffix_start, len(accounts))
        ],
        "token_program_pair": token_pair,
        "path": instruction["path"],
        "stack_height": instruction["stack_height"],
        "transaction_status": tx_status,
    }
    family = dict(cls)
    family.pop("trailing_wire_hex")
    family_id = _sha(_canonical(family))
    cls["wire_family_sha256"] = family_id
    return cls, signature, pool_key


def _scan_block(block: dict[str, Any], slot: int, receipt: dict[str, Any]) -> dict[str, Any]:
    refs = []
    normalized_count = 0
    candidate_tx_count = 0
    full_transactions = block["transactions"]
    for transaction_index, tx in enumerate(full_transactions):
        try:
            normalized = normalize_compiled_transaction(tx)
        except NormalizationError as exc:
            raise MonitorFailure(
                "transaction_normalization_failed",
                f"slot={slot},tx={transaction_index},{exc.category}",
            ) from exc
        normalized_count += 1
        instructions = [*normalized["outer_instructions"], *normalized["inner_instructions"]]
        pump_instructions = [ix for ix in instructions if ix["program_id"] == PROGRAM]
        if not pump_instructions:
            continue
        candidate_tx_count += 1
        tx_status = "failed" if normalized["meta_error"] is not None else "success"
        recognized_instruction_count = sum(
            1 for ix in pump_instructions if bytes.fromhex(ix["data_hex"])[:8] in DISCRIMINATORS
        )
        signature_list = normalized.get("transaction_signatures")
        if (
            not isinstance(signature_list, list)
            or not signature_list
            or not isinstance(signature_list[0], str)
        ):
            raise MonitorFailure(
                "pump_transaction_signature_invalid", f"slot={slot},tx={transaction_index}"
            )
        for instruction in pump_instructions:
            privileges = _privileges(tx, instruction["account_indexes"])
            instruction["_account_privileges"] = privileges
            if len(instruction["accounts"]) != len(privileges):
                raise MonitorFailure(
                    "privilege_account_count_mismatch", f"slot={slot},tx={transaction_index}"
                )
            class_row, signature, pool_key = _instruction_class(normalized, instruction, tx_status)
            instruction_identity = {
                "signature_sha256": _signature_hash(signature),
                "path": instruction["path"],
                "program_id": instruction["program_id"],
            }
            refs.append(
                {
                    "slot": slot,
                    "transaction_index": transaction_index,
                    "signature": signature,
                    "identity": instruction_identity,
                    "class": class_row,
                    "pool_key": pool_key,
                    "recognized_pumpswap_instruction_count": recognized_instruction_count,
                }
            )
    return {
        "normalized_transactions": normalized_count,
        "transactions_with_pumpswap_program": candidate_tx_count,
        "references": refs,
        "transaction_count": len(full_transactions),
    }


def _load_dataset_specs() -> list[dict[str, Any]]:
    zstd = shutil.which("zstd")
    if not zstd:
        raise MonitorFailure("zstd_cli_unavailable", "zstd is required for sealed captures")
    if PROGRAM != SCHEMA["address"]:
        raise MonitorFailure("pump_program_pin_mismatch", "decoder and IDL program pins differ")
    specs: list[dict[str, Any]] = []
    pair_root = REPO_ROOT / PAIR64_ROOT / "paired-block-capture"
    pair_plan_path = _required(pair_root / "plan.json")
    pair_plan_bytes = pair_plan_path.read_bytes()
    pair_plan = _json(pair_plan_path)
    pair_summary = _json(_required(pair_root / "summary.json"))
    pair_epoch_plan = _json(_required(REPO_ROOT / PAIR64_ROOT / "epoch-plan.json"))
    pair_audit_path = _required(REPO_ROOT / PAIR64_AUDIT)
    pair_audit = _json(pair_audit_path)
    pair_census_path = _required(REPO_ROOT / PAIR64_CENSUS)
    pair_census = _json(pair_census_path)
    selected = pair_plan.get("full_sample_slots")
    if (
        not isinstance(selected, list)
        or len(selected) != 512
        or len(set(selected)) != 512
        or pair_audit.get("bound_plan_sha256") != _sha(pair_plan_bytes)
        or pair_audit.get("provider_comparison", {})
        .get("helius_inventory", {})
        .get("selected_blocks_qualified")
        != 512
        or pair_summary.get("plan_sha256") != _sha(pair_plan_bytes)
    ):
        raise MonitorFailure("pair64_plan_or_audit_binding_mismatch", "Pair64")
    _source_code_pin(
        pair_plan["code_commit"],
        "tools/brain_senses_block_provider_pilot.py",
        pair_plan["code_sha256"],
    )
    _source_code_pin(
        pair_epoch_plan["code_commit"],
        "tools/brain_senses_block_epoch.py",
        pair_epoch_plan["code_sha256"],
    )
    specs.append(
        {
            "dataset_id": "pair64",
            "campaign_id": "brain-senses-live-helius-alchemy-512-pair64-20261009",
            "epoch_id": pair_epoch_plan["epoch_id"],
            "capture_root": pair_root,
            "expected_slots": selected,
            "plan_path": pair_plan_path,
            "plan_sha256": _sha(pair_plan_bytes),
            "campaign_plan_sha256": _file_sha(REPO_ROOT / PAIR64_ROOT / "epoch-plan.json"),
            "campaign_code": {
                "commit": pair_epoch_plan["code_commit"],
                "sha256": pair_epoch_plan["code_sha256"],
            },
            "census": pair_census,
            "census_path": pair_census_path,
            "census_sha256": _file_sha(pair_census_path),
            "binding_receipt": {
                "path": str(PAIR64_AUDIT),
                "sha256": _file_sha(pair_audit_path),
            },
            "pair_summary_sha256": _file_sha(pair_root / "summary.json"),
            "pair_epoch_plan_sha256": _file_sha(REPO_ROOT / PAIR64_ROOT / "epoch-plan.json"),
            "zstd": zstd,
            "pair64": True,
        }
    )

    sell_census_path = _required(REPO_ROOT / C3_SELL_CENSUS)
    sell_census = _json(sell_census_path)
    for epoch_id in FRAME_IDS:
        epoch_root = REPO_ROOT / C3_ROOT / "epochs" / epoch_id
        plan_path = _required(epoch_root / "epoch-plan.json")
        summary_path = _required(epoch_root / "epoch-summary.json")
        frame_path = _required(epoch_root / "frame.json")
        stop_path = _required(epoch_root / "STOP")
        plan_bytes = plan_path.read_bytes()
        plan = _json(plan_path)
        summary = _json(summary_path)
        frame = _json(frame_path)
        stop = _json(stop_path)
        campaign_manifest_path = REPO_ROOT / C3_ROOT / "schedule-v3.json"
        campaign_manifest = _json(campaign_manifest_path)
        campaign_manifest_sha256 = _file_sha(campaign_manifest_path)
        campaign_manifest_canonical_sha256 = _sha(_canonical(campaign_manifest))
        if (
            summary.get("status") != "frame_complete"
            or summary.get("epoch_id") != epoch_id
            or plan.get("epoch_id") != epoch_id
            or stop.get("status") != "frame_complete"
            or summary.get("selected_slots") != stop.get("selected_slots")
            or summary.get("stop_sha256") != _file_sha(stop_path)
            or frame.get("epoch_id") != epoch_id
            or frame.get("status") != "captured"
            or plan.get("schedule_sha256") != campaign_manifest_canonical_sha256
        ):
            raise MonitorFailure("c3_frame_seal_mismatch", epoch_id)
        selected_slots = summary.get("selected_slots")
        if (
            not isinstance(selected_slots, list)
            or len(selected_slots) != 16
            or len(set(selected_slots)) != 16
        ):
            raise MonitorFailure("c3_selected_slot_set_invalid", epoch_id)
        if plan.get("protocol") != "senses-finalized-inventory-first-v2":
            raise MonitorFailure("c3_epoch_protocol_mismatch", epoch_id)
        _source_code_pin(
            plan["code_commit"], "tools/brain_senses_block_epoch_v2.py", plan["code_sha256"]
        )
        census_path = C3_BUY_CENSUSES.get(epoch_id)
        census = _json(_required(REPO_ROOT / census_path)) if census_path else None
        specs.append(
            {
                "dataset_id": f"c3:{epoch_id}",
                "campaign_id": plan.get("schedule_id"),
                "epoch_id": epoch_id,
                "capture_root": epoch_root,
                "expected_slots": selected_slots,
                "plan_path": plan_path,
                "plan_sha256": _sha(plan_bytes),
                "campaign_plan_sha256": campaign_manifest_sha256,
                "campaign_manifest_canonical_sha256": campaign_manifest_canonical_sha256,
                "campaign_code": {"commit": plan["code_commit"], "sha256": plan["code_sha256"]},
                "summary_sha256": _file_sha(summary_path),
                "frame_sha256": _file_sha(frame_path),
                "stop_sha256": _file_sha(stop_path),
                "inventory_sha256": summary.get("inventory_sha256"),
                "inventory_denominator": summary.get("inventory_denominator"),
                "range": [summary.get("start"), summary.get("end_exclusive")],
                "census": census,
                "census_path": str(census_path) if census_path else None,
                "census_sha256": _file_sha(REPO_ROOT / census_path) if census_path else None,
                "sell_census": sell_census,
                "sell_census_path": str(C3_SELL_CENSUS),
                "sell_census_sha256": _file_sha(sell_census_path),
                "zstd": zstd,
                "pair64": False,
            }
        )
    return specs


def _replay_dataset(spec: dict[str, Any]) -> dict[str, Any]:
    capture_receipts = _capture_receipts(spec["capture_root"], pair64=spec["pair64"])
    expected_slots = spec["expected_slots"]
    if len(capture_receipts) != len(expected_slots):
        raise MonitorFailure(
            "full_capture_count_mismatch",
            f"{spec['dataset_id']} expected={len(expected_slots)} observed={len(capture_receipts)}",
        )
    expected_slot_set = set(expected_slots)
    observed_slots: set[int] = set()
    block_rows = []
    for receipt_path in capture_receipts:
        slot, block, receipt = _read_capture(receipt_path, spec["zstd"])
        if slot not in expected_slot_set or slot in observed_slots:
            raise MonitorFailure(
                "selected_slot_set_mismatch_or_duplicate", f"{spec['dataset_id']}:{slot}"
            )
        if block.get("parentSlot") is not None and block.get("parentSlot") >= slot:
            raise MonitorFailure(
                "block_parent_slot_not_before_slot", f"{spec['dataset_id']}:{slot}"
            )
        observed_slots.add(slot)
        block_rows.append((slot, block, receipt, receipt_path))
    if observed_slots != expected_slot_set:
        raise MonitorFailure("selected_slot_set_mismatch", spec["dataset_id"])
    block_rows.sort(key=lambda row: row[0])
    counters: Counter[str] = Counter()
    class_counts: Counter[str] = Counter()
    class_payloads: dict[str, dict[str, Any]] = {}
    class_frames: dict[str, list[str]] = defaultdict(list)
    signature_examples: dict[str, set[str]] = defaultdict(set)
    pool_counts: Counter[str] = Counter()
    pool_totals = 0
    no_ref_slots = []
    block_bindings = []
    identities = set()
    transaction_locations: dict[str, tuple[int, int]] = {}
    refs_by_status = Counter()
    known_kind_counts = Counter()
    unsupported_counts = Counter()
    buy_exact_ref_count = 0
    buy_exact_tx_signatures: set[str] = set()
    buy_exact_tx_status: dict[str, str] = {}
    sell23_success = 0
    sell23_failed = 0
    for slot, block, receipt, receipt_path in block_rows:
        block_summary = _scan_block(block, slot, receipt)
        counters["selected_blocks"] += 1
        counters["normalized_transactions"] += block_summary["normalized_transactions"]
        counters["full_block_transactions"] += block_summary["transaction_count"]
        counters["raw_full_block_response_bytes"] += receipt["response_bytes"]
        counters["stored_full_block_response_bytes"] += receipt["storage"]["bytes"]
        counters["transactions_with_pumpswap_program"] += block_summary[
            "transactions_with_pumpswap_program"
        ]
        refs = block_summary["references"]
        if not refs:
            no_ref_slots.append(slot)
        for ref in refs:
            signature_hash = ref["identity"]["signature_sha256"]
            location = (slot, ref["transaction_index"])
            previous_location = transaction_locations.setdefault(signature_hash, location)
            if previous_location != location:
                raise MonitorFailure(
                    "duplicate_referencing_transaction_signature",
                    f"{spec['dataset_id']}:{signature_hash}",
                )
            identity = (ref["identity"]["signature_sha256"], tuple(ref["identity"]["path"]))
            if identity in identities:
                raise MonitorFailure(
                    "duplicate_instruction_identity", f"{spec['dataset_id']}:{identity[0]}"
                )
            identities.add(identity)
            counters["instruction_references"] += 1
            refs_by_status[ref["class"]["transaction_status"]] += 1
            kind = ref["class"]["instruction_kind"]
            known_kind_counts[kind] += 1
            if kind == "unknown_discriminator":
                unsupported_counts["unknown_discriminator"] += 1
            if ref["class"]["wire_data_length"] < 8:
                unsupported_counts["wire_shorter_than_discriminator"] += 1
            if (
                ref["class"]["fixed_account_count"] is not None
                and ref["class"]["observed_account_count"] < ref["class"]["fixed_account_count"]
            ):
                unsupported_counts["fixed_account_prefix_short"] += 1
            if kind == "buy_exact_quote_in":
                buy_exact_ref_count += 1
                buy_exact_tx_signatures.add(ref["identity"]["signature_sha256"])
                old_status = buy_exact_tx_status.setdefault(
                    ref["identity"]["signature_sha256"], ref["class"]["transaction_status"]
                )
                if old_status != ref["class"]["transaction_status"]:
                    raise MonitorFailure("buyexact_transaction_status_conflict", spec["dataset_id"])
            if (
                kind == "sell"
                and ref["recognized_pumpswap_instruction_count"] == 1
                and ref["class"]["wire_data_length"] == 24
                and ref["class"]["observed_account_count"] == 23
                and ref["class"]["token_program_pair"]
                == [
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                ]
            ):
                if ref["class"]["transaction_status"] == "success":
                    sell23_success += 1
                else:
                    sell23_failed += 1
            class_bytes = _canonical(ref["class"])
            class_id = _sha(class_bytes)
            class_counts[class_id] += 1
            class_payloads[class_id] = ref["class"]
            if spec["dataset_id"] not in class_frames[class_id]:
                class_frames[class_id].append(spec["dataset_id"])
            signature_examples[class_id].add(ref["identity"]["signature_sha256"])
            if ref["pool_key"] is not None:
                pool_counts[ref["pool_key"]] += 1
                pool_totals += 1
        block_bindings.append(
            {
                "slot": slot,
                "blockhash": block.get("blockhash"),
                "parent_slot": block.get("parentSlot"),
                "event_time_unix_seconds": block.get("blockTime"),
                "receipt_time_ns": receipt.get("received_ns"),
                "raw_response_sha256": receipt.get("response_sha256"),
                "stored_response_sha256": receipt.get("storage", {}).get("sha256"),
                "capture_receipt": str(receipt_path.relative_to(spec["capture_root"])),
                "transaction_count": len(block["transactions"]),
                "raw_response_bytes": receipt["response_bytes"],
                "stored_response_bytes": receipt["storage"]["bytes"],
                "pumpswap_instruction_references": len(refs),
            }
        )
    unique_transaction_signatures = {identity[0] for identity in identities}
    counters["unique_referencing_transactions"] = len(unique_transaction_signatures)
    counters["parser_failures"] = 0
    counters["successful_instruction_references"] = refs_by_status["success"]
    counters["failed_instruction_references"] = refs_by_status["failed"]
    reconciliation = {}
    if spec["pair64"]:
        census_counts = spec["census"].get("counts", {})
        expected = {
            "selected_blocks": census_counts.get("block_requests"),
            "full_block_transactions": census_counts.get("full_block_transactions"),
            "raw_full_block_response_bytes": census_counts.get("raw_response_bytes"),
            "stored_full_block_response_bytes": census_counts.get("compressed_response_bytes"),
            "buy_exact_instruction_references": census_counts.get("instruction_references"),
            "buy_exact_unique_referencing_transactions": census_counts.get(
                "candidate_transactions"
            ),
            "buy_exact_successful_transactions": census_counts.get(
                "candidate_transactions_success"
            ),
            "buy_exact_failed_transactions": census_counts.get("candidate_transactions_failed"),
        }
        observed = {
            "selected_blocks": counters["selected_blocks"],
            "full_block_transactions": counters["full_block_transactions"],
            "raw_full_block_response_bytes": counters["raw_full_block_response_bytes"],
            "stored_full_block_response_bytes": counters["stored_full_block_response_bytes"],
            "buy_exact_instruction_references": buy_exact_ref_count,
            "buy_exact_unique_referencing_transactions": len(buy_exact_tx_signatures),
            "buy_exact_successful_transactions": census_counts.get(
                "candidate_transactions_success"
            ),
            "buy_exact_failed_transactions": census_counts.get("candidate_transactions_failed"),
        }
        observed["buy_exact_successful_transactions"] = sum(
            status == "success" for status in buy_exact_tx_status.values()
        )
        observed["buy_exact_failed_transactions"] = sum(
            status == "failed" for status in buy_exact_tx_status.values()
        )
        if expected != observed:
            raise MonitorFailure(
                "pair64_census_reconciliation_failed",
                json.dumps({"expected": expected, "observed": observed}, sort_keys=True),
            )
        reconciliation = {"expected": expected, "observed": observed, "status": "matched"}
    else:
        buy_census = spec["census"]
        sell_counts = spec["sell_census"]["frames"][spec["epoch_id"]]["counts"]
        if buy_census is not None:
            census_counts = buy_census.get("counts", {})
            buy_expected = {
                "selected_blocks": census_counts.get("block_requests"),
                "full_block_transactions": census_counts.get("full_block_transactions"),
                "raw_full_block_response_bytes": census_counts.get(
                    "raw_response_bytes", census_counts.get("raw_full_block_response_bytes")
                ),
                "stored_full_block_response_bytes": census_counts.get(
                    "compressed_response_bytes", census_counts.get("zstd_full_block_response_bytes")
                ),
                "buy_exact_instruction_references": census_counts.get("instruction_references"),
                "buy_exact_unique_referencing_transactions": census_counts.get(
                    "candidate_transactions", census_counts.get("unique_candidate_transactions")
                ),
            }
            buy_observed = {
                "selected_blocks": counters["selected_blocks"],
                "full_block_transactions": counters["full_block_transactions"],
                "raw_full_block_response_bytes": counters["raw_full_block_response_bytes"],
                "stored_full_block_response_bytes": counters["stored_full_block_response_bytes"],
                "buy_exact_instruction_references": buy_exact_ref_count,
                "buy_exact_unique_referencing_transactions": len(buy_exact_tx_signatures),
            }
            if buy_expected != buy_observed:
                raise MonitorFailure(
                    "c3_buyexact_census_reconciliation_failed",
                    json.dumps(
                        {
                            "frame": spec["epoch_id"],
                            "expected": buy_expected,
                            "observed": buy_observed,
                        },
                        sort_keys=True,
                    ),
                )
        sell_expected = {
            "eligible_successful_sell23_candidates": sell_counts.get(
                "eligible_successful_sell23_candidates", 0
            ),
            "failed_exact_sell23_structural_refs": sell_counts.get(
                "failed_exact_sell23_structural_refs", 0
            ),
            "selected_full_blocks_verified": spec["sell_census"]["frames"][spec["epoch_id"]].get(
                "selected_full_blocks_verified"
            ),
        }
        sell_observed = {
            "eligible_successful_sell23_candidates": sell23_success,
            "failed_exact_sell23_structural_refs": sell23_failed,
            "selected_full_blocks_verified": counters["selected_blocks"],
        }
        if sell_expected != sell_observed:
            raise MonitorFailure(
                "c3_sell23_census_reconciliation_failed",
                json.dumps(
                    {
                        "frame": spec["epoch_id"],
                        "expected": sell_expected,
                        "observed": sell_observed,
                    },
                    sort_keys=True,
                ),
            )
        reconciliation = {
            "buy_exact": "matched"
            if buy_census is not None
            else "no_frozen_buyexact_census_for_frame",
            "sell23": {"expected": sell_expected, "observed": sell_observed, "status": "matched"},
        }
    return {
        "dataset_id": spec["dataset_id"],
        "campaign_id": spec["campaign_id"],
        "epoch_id": spec["epoch_id"],
        "plan_sha256": spec["plan_sha256"],
        "campaign_plan_sha256": spec["campaign_plan_sha256"],
        "campaign_manifest_canonical_sha256": spec.get("campaign_manifest_canonical_sha256"),
        "campaign_code": spec["campaign_code"],
        "summary_sha256": spec.get("summary_sha256"),
        "frame_sha256": spec.get("frame_sha256"),
        "stop_sha256": spec.get("stop_sha256"),
        "inventory_sha256": spec.get("inventory_sha256"),
        "inventory_denominator": spec.get("inventory_denominator"),
        "interval": spec.get("range"),
        "selected_slots_sha256": _sha(_canonical(sorted(expected_slots))),
        "selected_slots": len(expected_slots),
        "counts": dict(sorted(counters.items())),
        "reference_status_counts": dict(sorted(refs_by_status.items())),
        "instruction_kind_counts": dict(sorted(known_kind_counts.items())),
        "unsupported_structure_counts": dict(sorted(unsupported_counts.items())),
        "selected_blocks_without_pumpswap_refs": no_ref_slots,
        "first_seen_class_count": len(class_counts),
        "class_count": len(class_counts),
        "classes_truncated": len(class_counts) > 256,
        "class_index": {
            class_id: class_payloads[class_id]["wire_family_sha256"]
            for class_id in sorted(class_counts)
        },
        "classes": [
            {
                "class_sha256": class_id,
                "count": class_counts[class_id],
                "first_seen_in": class_frames[class_id][0],
                "also_seen_in": class_frames[class_id][1:],
                "fingerprint": class_payloads[class_id],
                "signature_hash_examples": sorted(signature_examples[class_id])[:3],
            }
            for class_id in sorted(class_counts)[:256]
        ],
        "pool_concentration": {
            "basis": "IDL pool account position 0 for recognized instruction kinds only",
            "recognized_pool_reference_count": pool_totals,
            "unique_pool_accounts": len(pool_counts),
            "top": [
                {"pool_account": pool, "references": count}
                for pool, count in pool_counts.most_common(25)
            ],
        },
        "block_bindings": block_bindings,
        "census_reconciliation": reconciliation,
        "census_source": {
            "path": str(spec["census_path"]) if spec.get("census_path") else None,
            "sha256": spec.get("census_sha256"),
            "sell_census_path": spec.get("sell_census_path"),
            "sell_census_sha256": spec.get("sell_census_sha256"),
        },
        "binding_receipt": spec.get("binding_receipt"),
        "_signature_hashes": sorted(transaction_locations),
    }


def build_receipt() -> dict[str, Any]:
    specs = _load_dataset_specs()
    datasets = [_replay_dataset(spec) for spec in specs]
    seen_classes: set[str] = set()
    seen_families: set[str] = set()
    seen_transaction_signatures: set[str] = set()
    first_seen_by_class: dict[str, str] = {}
    ordered_novelty = []
    for dataset in datasets:
        duplicate_signatures = seen_transaction_signatures.intersection(
            dataset["_signature_hashes"]
        )
        if duplicate_signatures:
            raise MonitorFailure(
                "duplicate_referencing_transaction_across_datasets",
                sorted(duplicate_signatures)[0],
            )
        seen_transaction_signatures.update(dataset.pop("_signature_hashes"))
        class_index = dataset.pop("class_index")
        current = set(class_index)
        new_classes = current - seen_classes
        new_families = {class_index[class_id] for class_id in new_classes} - seen_families
        changed_classes = {
            class_id for class_id in new_classes if class_index[class_id] in seen_families
        }
        for class_id in current:
            first_seen_by_class.setdefault(class_id, dataset["dataset_id"])
        for row in dataset["classes"]:
            row["first_seen_in"] = first_seen_by_class[row["class_sha256"]]
        ordered_novelty.append(
            {
                "dataset_id": dataset["dataset_id"],
                "classes_seen_before": len(seen_classes),
                "new_class_count": len(new_classes),
                "first_wire_family_count": len(new_families),
                "changed_wire_variant_count": len(changed_classes),
                "new_class_sha256": sorted(new_classes)[:256],
                "new_class_list_truncated": len(new_classes) > 256,
                "no_longer_observed_class_count": len(seen_classes - current),
            }
        )
        seen_classes.update(current)
        seen_families.update(class_index.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "replay_complete_structural_only",
        "offline_replay": True,
        "scope": (
            "PumpSwap instruction wire/path/privilege class novelty in frozen Pair64 "
            "and c3 selected blocks only"
        ),
        "source_pins": {
            "repository_head": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "tool_sha256": _file_sha(Path(__file__)),
            "normalizer_sha256": _file_sha(REPO_ROOT / "src/memecoin_pumpswap/normalize.py"),
            "schema_module_sha256": _file_sha(REPO_ROOT / "src/memecoin_pumpswap/schema.py"),
            "pump_amm_idl_commit": SCHEMA["provenance"]["commit"],
            "pump_amm_idl_sha256": SCHEMA["provenance"]["sha256"],
            "pump_amm_program_id": PUMP_PROGRAM,
        },
        "novelty_sequence": ordered_novelty,
        "datasets": datasets,
        "limitations": [
            (
                "This digest is a bounded structural scan of selected captured blocks, not global "
                "transaction discovery or market-process completeness."
            ),
            (
                "Account positions remain positional except the fixed IDL prefix and recognized "
                "IDL pool position; suffix accounts receive no invented roles."
            ),
            (
                "Transaction success is the captured meta.err indicator and does not prove "
                "settlement or economic effect."
            ),
            "No admission, prospective impact, economic-zero, or completeness claim is made.",
        ],
    }


def _write_receipt(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical(payload) + b"\n"
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
    except FileExistsError as exc:
        raise MonitorFailure("output_exists_refusing_overwrite", str(path)) from exc


def _safe_failure_source_pins() -> dict[str, str | None]:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = None
    try:
        tool_sha = _file_sha(Path(__file__))
    except OSError:
        tool_sha = None
    return {"repository_head": head, "tool_sha256": tool_sha}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new receipt path; existing files are never overwritten",
    )
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    try:
        receipt = build_receipt()
    except Exception as exc:
        failure_code = exc.code if isinstance(exc, MonitorFailure) else "unexpected_replay_error"
        failure_detail = (
            exc.detail[:160]
            if isinstance(exc, MonitorFailure)
            else f"unexpected:{type(exc).__name__}"
        )
        failure = {
            "schema_version": SCHEMA_VERSION,
            "status": "monitor_failure",
            "offline_replay": True,
            "failure": {
                "code": failure_code,
                "detail": failure_detail,
            },
            "source_pins": _safe_failure_source_pins(),
        }
        _write_receipt(output, failure)
        print(json.dumps(failure, sort_keys=True, separators=(",", ":")))
        return 2
    _write_receipt(output, receipt)
    print(json.dumps({"status": receipt["status"], "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
