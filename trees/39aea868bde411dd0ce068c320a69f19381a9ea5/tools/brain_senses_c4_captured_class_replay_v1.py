#!/usr/bin/env python3
"""Replay the frozen c4 13:00–22:00 selected blocks for structural exposure only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memecoin_pumpswap.decoder import PROGRAM, decode58  # noqa: E402
from tools import brain_senses_buy_exact_v1_census as buy_exact  # noqa: E402
from tools.brain_senses_block_epoch_v2 import _validate_journal  # noqa: E402

MANIFEST_DEFAULT = ROOT / "work/brain-senses-c4-captured-class-census-v1/frozen-manifest.json"
OUTPUT_DEFAULT = ROOT / "work/brain-senses-c4-captured-class-census-v1/replay-v1"
CAMPAIGN_ID = "helius-inventory-v3-20261010-4"
EXPECTED_FREEZE_SHA256 = "379afc45a053c3994fe14dbed08a5930a2f018005d6c79a8d12700d337202abc"
EXPECTED_MANIFEST_SHA256 = "4b5ed2c1432c9af2a899ff3e8e1481bb2db4e8026043e0b1d309740408170d6a"
EXPECTED_REPOSITORY_HEAD = "fb51eeb14b1cb38ffe3ce97e7687542061147676"
EXPECTED_CAMPAIGN_ROOT = "work/brain-senses-live-helius-inventory-v3-20261010-4"
INCLUDED = tuple(f"20261010T{hour:02d}0000Z" for hour in range(13, 23))
EXCLUDED = "20261010T230000Z"
SOURCE_PINS = {
    "tools/brain_senses_program_exposure_ledger_v1.py": (
        "74d26ddf406d829e3e2755e1a29ad7e724039ba2fa8fe440518f3af959a3d61e"
    ),
    "tools/brain_senses_pumpswap_structural_novelty_v1.py": (
        "61ba6c2aa5c24b2dd1233a46377daba0f41c5fe7018f18c85714ca198e9502cc"
    ),
    "tools/brain_senses_buy_exact_v1_census.py": (
        "14e6a4eec485b8a8a19267e18621c67131c70ff54c5bd250cf060de023525d46"
    ),
    "src/memecoin_pumpswap/decoder.py": (
        "21610a62f1e8bb6c54cbe831134e6f170938668d85492434704aa66472471a8b"
    ),
    "src/memecoin_pumpswap/normalize.py": (
        "7253588560626c6f45ed65ebfd802200f20e79e2daec88fadb93d34731e84643"
    ),
    "src/memecoin_pumpswap/schema.py": (
        "dafd0708aad54224565863c3b23deee8440ef3fbcd2c1e7b5ce7629e2a6d5ad1"
    ),
}


class CensusError(ValueError):
    """Fail-closed error for a source or frame mismatch."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise CensusError("expected_json_object")
    return value


def _verify_frozen_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    manifest = _read_json(path)
    supplied = manifest.get("freeze_sha256")
    body = dict(manifest)
    body.pop("freeze_sha256", None)
    if _sha(_canonical(body)) != supplied:
        raise CensusError("frozen_manifest_digest_mismatch")
    if (
        supplied != EXPECTED_FREEZE_SHA256
        or _sha(raw) != EXPECTED_MANIFEST_SHA256
        or manifest.get("repository_head") != EXPECTED_REPOSITORY_HEAD
        or manifest.get("campaign_root") != EXPECTED_CAMPAIGN_ROOT
        or manifest.get("schema") != "c4-captured-class-census-freeze-v1"
        or tuple(manifest.get("included_frame_ids", [])) != INCLUDED
        or [row.get("frame_id") for row in manifest.get("excluded_frame_ids", [])] != [EXCLUDED]
        or manifest.get("body_read_during_freeze") is not False
    ):
        raise CensusError("frozen_manifest_scope_mismatch")
    pins = {
        item.get("path"): item.get("git_blob_sha256")
        for item in manifest.get("census_source_code", [])
        if isinstance(item, dict)
    }
    for name, expected in SOURCE_PINS.items():
        path_on_disk = ROOT / name
        if pins.get(name) != expected or _sha(path_on_disk.read_bytes()) != expected:
            raise CensusError("frozen_source_pin_mismatch")
    for item in manifest.get("campaign_metadata", []):
        file_path = ROOT / item["path"]
        data = file_path.read_bytes()
        if len(data) != item.get("bytes") or _sha(data) != item.get("sha256"):
            raise CensusError("frozen_campaign_metadata_mismatch")
    return manifest, _sha(raw)


def _verify_frame(
    manifest: dict[str, Any], frame: dict[str, Any], campaign_root: Path
) -> tuple[Path, dict[str, Any], dict[str, Any], list[dict[str, Any]], str]:
    bucket = frame.get("frame_id")
    if bucket not in INCLUDED:
        raise CensusError("frame_not_in_frozen_population")
    root = campaign_root / "epochs" / bucket
    summary_path = root / "epoch-summary.json"
    summary_raw = summary_path.read_bytes()
    summary = _read_json(summary_path)
    plan_path = root / "epoch-plan.json"
    plan_raw = plan_path.read_bytes()
    plan = _read_json(plan_path)
    stop_path = root / "STOP"
    stop_raw = stop_path.read_bytes()
    stop = json.loads(stop_raw)
    if not isinstance(stop, dict):
        raise CensusError("frame_stop_invalid")
    if (
        _sha(summary_raw) != frame.get("summary_sha256")
        or summary.get("epoch_id") != bucket
        or summary.get("status") != "frame_complete"
        or len(summary.get("selected_slots", [])) != 16
        or summary.get("selected_slots") != frame.get("selected_slot_ids")
        or summary.get("start") != frame.get("start")
        or summary.get("end_exclusive") != frame.get("end_exclusive")
        or summary.get("inventory_denominator") != frame.get("inventory_denominator")
        or summary.get("stop_sha256") != _sha(stop_raw)
        or stop.get("status") != "frame_complete"
        or stop.get("selected_slots") != summary.get("selected_slots")
        or stop.get("advance_to") != summary.get("end_exclusive")
        or plan.get("epoch_id") != bucket
        or plan.get("protocol") != "senses-finalized-inventory-first-v2"
    ):
        raise CensusError("frame_summary_plan_or_stop_mismatch")
    journal = _validate_journal(root, plan)
    if (
        len(journal) != 34
        or any(row.get("status") != "captured" for row in journal)
        or sum(row.get("response_bytes", 0) for row in journal)
        != frame.get("captured_response_bytes")
        or sum(row.get("stored_response_bytes", 0) for row in journal)
        != frame.get("stored_response_bytes")
    ):
        raise CensusError("frame_request_journal_not_complete")
    if (
        len(list((root / "requests").glob("*.json"))) != 34
        or len(list((root / "captures").glob("*/receipt.json"))) != 34
        or frame.get("request_reservations") != 34
        or frame.get("per_slot_outcome_count") != 16
    ):
        raise CensusError("frame_request_or_capture_denominator_mismatch")
    return root, plan, summary, journal, _sha(plan_raw)


def _frame_binding_receipt(
    bucket: str,
    plan_sha: str,
    summary: dict[str, Any],
    summary_sha: str,
    journal: list[dict[str, Any]],
) -> dict[str, Any]:
    """A temporary adapter for the existing census's frame-shape checks, not evidence."""
    return {
        "campaign_id": CAMPAIGN_ID,
        "frame": {
            "bucket": bucket,
            "plan_sha256": plan_sha,
            "status": "frame_complete",
            "reservation_status": "reconciled",
            "capture_artifacts_count": len(journal),
            "actual_requests": len(journal),
            "request_statuses": {"captured": len(journal)},
            "sampled_structural_checks": {"passed": 16, "total": 16},
            "summary_sha256": summary_sha,
        },
    }


def _tx_status(tx: Any) -> str:
    if not isinstance(tx, dict) or not isinstance(tx.get("meta"), dict):
        return "unknown"
    meta = tx["meta"]
    if "err" not in meta:
        return "unknown"
    return "failed" if meta["err"] is not None else "success"


def _instructions(
    tx: dict[str, Any],
) -> tuple[list[tuple[str, dict[str, Any]]], int, int]:
    transaction = tx.get("transaction")
    message = transaction.get("message") if isinstance(transaction, dict) else None
    if not isinstance(message, dict):
        return [], 1, 0
    keys = message.get("accountKeys")
    meta = tx.get("meta")
    if not isinstance(keys, list):
        keys = []
    resolved_keys = [entry.get("pubkey") if isinstance(entry, dict) else entry for entry in keys]
    if isinstance(meta, dict):
        loaded = meta.get("loadedAddresses") or {}
        if isinstance(loaded, dict):
            writable = loaded.get("writable", [])
            readonly = loaded.get("readonly", [])
            if isinstance(writable, list) and isinstance(readonly, list):
                resolved_keys.extend([*writable, *readonly])
    records: list[tuple[str, dict[str, Any]]] = []
    unresolved = 0
    outer = message.get("instructions")
    if not isinstance(outer, list):
        unresolved += 1
        outer = []
    for ix in outer:
        program = _program_id(ix, resolved_keys)
        if program is None:
            unresolved += 1
        elif isinstance(ix, dict):
            records.append(("outer", {**ix, "_program": program}))
    inner_groups = meta.get("innerInstructions") if isinstance(meta, dict) else None
    trace_unavailable = 0
    if not isinstance(inner_groups, list):
        trace_unavailable += 1
        inner_groups = []
    for group in inner_groups:
        if not isinstance(group, dict) or not isinstance(group.get("instructions"), list):
            unresolved += 1
            continue
        for ix in group["instructions"]:
            program = _program_id(ix, resolved_keys)
            if program is None:
                unresolved += 1
            elif isinstance(ix, dict):
                records.append(("inner", {**ix, "_program": program}))
    return records, unresolved, trace_unavailable


def _program_id(ix: Any, keys: list[Any]) -> str | None:
    if not isinstance(ix, dict):
        return None
    direct = ix.get("programId")
    if isinstance(direct, str):
        return direct
    index = ix.get("programIdIndex")
    if type(index) is int and 0 <= index < len(keys) and isinstance(keys[index], str):
        return keys[index]
    return None


def _program_exposure(root: Path) -> dict[str, Any]:
    zstd = shutil.which("zstd")
    if zstd is None:
        raise CensusError("zstd_cli_unavailable")
    program_refs: dict[str, Counter[str]] = defaultdict(Counter)
    program_transactions: dict[str, set[tuple[int, int, str]]] = defaultdict(set)
    layout: Counter[tuple[str, str, str, str]] = Counter()
    tx_counts: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    seen_slots = set()
    for capture_path, receipt, request in buy_exact._capture_rows(root, v3_layout=True):
        slot = request["params"][0]
        if type(slot) is not int or slot in seen_slots:
            raise CensusError("duplicate_or_invalid_full_block_slot")
        seen_slots.add(slot)
        block = buy_exact._read_block(zstd, capture_path, receipt)
        counts["selected_full_block_count"] += 1
        counts["full_block_transactions"] += len(block["transactions"])
        for transaction_index, tx in enumerate(block["transactions"]):
            status = _tx_status(tx)
            tx_counts[status] += 1
            signature_rows = tx.get("transaction", {}).get("signatures", [])
            sig = (
                signature_rows[0]
                if signature_rows and isinstance(signature_rows[0], str)
                else f"index:{transaction_index}"
            )
            records, unresolved, trace_unavailable = _instructions(tx)
            counts["unresolved_instruction_program_ids"] += unresolved
            counts["transactions_with_unavailable_inner_instruction_trace"] += trace_unavailable
            tx_identity = (slot, transaction_index, sig)
            for path, ix in records:
                program = ix["_program"]
                program_refs[program][f"{status}:{path}"] += 1
                program_transactions[program].add(tx_identity)
                counts["resolved_instruction_references"] += 1
                if program == PROGRAM:
                    data = ix.get("data")
                    try:
                        wire = decode58(data) if isinstance(data, str) else b""
                    except ValueError:
                        wire = b""
                    discriminator = wire[:8].hex() if len(wire) >= 8 else "unknown"
                    layout[(status, path, str(len(wire)), discriminator)] += 1
                    counts["pumpswap_instruction_references"] += 1
    if len(seen_slots) != 16:
        raise CensusError("selected_full_block_count_mismatch")
    programs = [
        {
            "program_id": program,
            "unique_transactions": len(program_transactions[program]),
            "instruction_references_by_status_and_path": dict(
                sorted(program_refs[program].items())
            ),
        }
        for program in sorted(program_refs)
    ]
    return {
        "counts": {
            **dict(sorted(counts.items())),
            **{f"transactions_{k}": v for k, v in sorted(tx_counts.items())},
        },
        "programs": programs,
        "pumpswap_wire_layouts": [
            {
                "transaction_status": key[0],
                "path": key[1],
                "instruction_data_bytes": key[2],
                "discriminator_hex_or_unknown": key[3],
                "references": value,
            }
            for key, value in sorted(layout.items())
        ],
        "denominator": (
            "all transactions and resolvable instruction references in 16 Helius full-block "
            "captures selected by the frozen frame summary"
        ),
    }


def replay(manifest_path: Path, output_dir: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    output_dir = output_dir.resolve()
    manifest, manifest_sha = _verify_frozen_manifest(manifest_path)
    campaign_root = ROOT / manifest["campaign_root"]
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(output_dir.iterdir()):
        raise CensusError("output_directory_not_empty")
    frames_by_id = {row["frame_id"]: row for row in manifest["frame_metadata"]}
    frame_receipts = []
    with tempfile.TemporaryDirectory(prefix="c4-census-binding-") as temp:
        temp_root = Path(temp)
        for bucket in INCLUDED:
            frame = frames_by_id[bucket]
            root, plan, summary, journal, plan_sha = _verify_frame(manifest, frame, campaign_root)
            binding_path = temp_root / f"{bucket}.json"
            binding_path.write_bytes(
                _canonical(
                    _frame_binding_receipt(
                        bucket, plan_sha, summary, frame["summary_sha256"], journal
                    )
                )
            )
            temporary_output = temp_root / f"{bucket}.census.json"
            buy_exact.census(root, temporary_output, 16, binding_path)
            census_report = _read_json(temporary_output)
            census_report["source"] = {
                **census_report["source"],
                "provider_audit_receipt": str(manifest_path.relative_to(ROOT)),
                "provider_audit_receipt_sha256": manifest_sha,
                "source_binding_kind": "frozen_c4_metadata_manifest_plus_sealed_frame_replay",
                "synthetic_receipt_used_only_for_internal_frame_shape_checks": True,
                "frame_plan_sha256": plan_sha,
            }
            final_path = output_dir / f"{bucket}-buy-exact-census.json"
            final_bytes = _canonical(census_report) + b"\n"
            fd = os.open(final_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(final_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            frame_receipts.append(
                {
                    "bucket": bucket,
                    "plan_sha256": plan_sha,
                    "summary_sha256": frame["summary_sha256"],
                    "selected_slots": frame["selected_slot_count"],
                    "inventory_denominator": frame["inventory_denominator"],
                    "raw_response_bytes": frame["captured_response_bytes"],
                    "stored_response_bytes": frame["stored_response_bytes"],
                    "buy_exact_counts": census_report["counts"],
                    "buy_exact_matrix": census_report["matrix"],
                    "output_path": str(final_path.relative_to(ROOT)),
                    "output_sha256": _sha(final_bytes),
                }
            )
            temporary_output.unlink()
    exposure = []
    for bucket in INCLUDED:
        root = campaign_root / "epochs" / bucket
        exposure.append({"bucket": bucket, **_program_exposure(root)})
    result = {
        "schema": "brain-senses-c4-captured-class-replay-v1",
        "status": "complete_selected_block_structural_replay",
        "freeze_manifest": str(manifest_path.relative_to(ROOT)),
        "freeze_manifest_sha256": manifest_sha,
        "freeze_manifest_internal_sha256": manifest["freeze_sha256"],
        "repository_head": manifest["repository_head"],
        "campaign_root": manifest["campaign_root"],
        "source_pins": SOURCE_PINS,
        "replay_tool_sha256": _sha(Path(__file__).read_bytes()),
        "buy_exact_census_tool_sha256": _sha(Path(buy_exact.__file__).read_bytes()),
        "included_buckets": list(INCLUDED),
        "excluded_buckets": [
            {
                "bucket": EXCLUDED,
                "reason": "frozen_tail2_control_excluded; response body not opened",
            }
        ],
        "frame_receipts": frame_receipts,
        "program_exposure_by_frame": exposure,
        "aggregate": {
            "selected_full_blocks": sum(
                row["counts"]["selected_full_block_count"] for row in exposure
            ),
            "transactions": sum(row["counts"]["full_block_transactions"] for row in exposure),
            "resolved_instruction_references": sum(
                row["counts"]["resolved_instruction_references"] for row in exposure
            ),
            "unresolved_instruction_program_ids": sum(
                row["counts"]["unresolved_instruction_program_ids"] for row in exposure
            ),
            "transactions_with_unavailable_inner_instruction_trace": sum(
                row["counts"]["transactions_with_unavailable_inner_instruction_trace"]
                for row in exposure
            ),
            "pumpswap_instruction_references": sum(
                row["counts"]["pumpswap_instruction_references"] for row in exposure
            ),
            "buy_exact_instruction_references": sum(
                row["buy_exact_counts"].get("instruction_references", 0) for row in frame_receipts
            ),
            "buy_exact_candidate_transactions": sum(
                row["buy_exact_counts"].get("candidate_transactions", 0) for row in frame_receipts
            ),
        },
        "limitations": [
            (
                "This is the frozen 10-frame, 160-selected-block captured-class replay only; "
                "it is not transaction discovery completeness, market/population completeness, "
                "or exact economic activity."
            ),
            (
                "Provider-reported inventory and selected-block sampling do not establish "
                "omitted-slot economic inactivity."
            ),
            (
                "The buyExact v1 census is structural. A 25-byte wire variant or fee-pair "
                "predicate is not proof of execution settlement, native/wallet-net price, or "
                "Token-2022 state."
            ),
            (
                "Unknown transaction statuses, unresolved instructions, unsupported layouts, "
                "and censored settlement remain explicit; no training or execution admission "
                "follows."
            ),
        ],
    }
    out = output_dir / "replay-receipt.json"
    encoded = _canonical(result) + b"\n"
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    return {"output": str(out), "sha256": _sha(encoded), "aggregate": result["aggregate"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_DEFAULT)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DEFAULT)
    args = parser.parse_args()
    print(json.dumps(replay(args.manifest, args.output_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
