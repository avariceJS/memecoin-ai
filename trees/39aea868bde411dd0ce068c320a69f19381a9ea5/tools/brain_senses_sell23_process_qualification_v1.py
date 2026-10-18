"""Offline process-level field qualification for the frozen c3 sell23 censored rows.

This tool reads only the pre-pinned six successful c3 frames and their existing c3
qualification receipts. It performs no RPC, credential access, or writes to source roots.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MANIFEST_PATH = Path("work/brain-senses-sell23-process-qualification-v1/source-manifest-v1.json")
MANIFEST_CONTENT_SHA256 = "797c8c191dade1a26d4c0670f3e325b2986b12f2c8de995f1be042d942eddf55"
MANIFEST_SHA256 = "2e7a160eb3085fe2b06c55b1bebe99d0c3841b30d18e555b29baf3a99bb19189"
C3_ROOT = Path("work/brain-senses-live-helius-inventory-v3-20261010-3")
SOURCE_RECEIPTS = {
    "six_frame_replay": Path(
        "docs/evidence/pumpswap-sell23-c3-six-frame-ledger-replay-2026-10-10.json"
    ),
    "native_close_feasibility": Path(
        "docs/evidence/pumpswap-sell23-c3-native-close-feasibility-2026-10-10.json"
    ),
    "native_account_flow": Path(
        "docs/evidence/pumpswap-sell23-c3-native-account-flow-2026-10-10.json"
    ),
}
RENT_SYSVAR = "SysvarRent111111111111111111111111111111111"


class QualificationError(ValueError):
    """Raised when the frozen source set cannot be reconciled safely."""


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _canonical_hash(value: Any) -> str:
    return _sha_bytes(_canonical(value))


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise QualificationError(f"cannot read pinned JSON: {path}") from exc
    if not isinstance(value, dict):
        raise QualificationError(f"expected JSON object: {path}")
    return value


def verify_source_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    manifest_bytes = path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise QualificationError("unsupported source manifest schema")
    expected_content_sha = manifest.get("content_sha256")
    content = {
        key: value for key, value in manifest.items() if key not in ("content_sha256", "created_ns")
    }
    if expected_content_sha != _canonical_hash(content):
        raise QualificationError("source manifest content hash mismatch")
    if (
        expected_content_sha != MANIFEST_CONTENT_SHA256
        or _sha_bytes(manifest_bytes) != MANIFEST_SHA256
    ):
        raise QualificationError("source manifest differs from the frozen pre-replay manifest")
    if manifest.get("manifest_kind") != "source_freeze_before_new_semantic_replay":
        raise QualificationError("source manifest is not a pre-replay source freeze")
    if manifest.get("source_root") != C3_ROOT.as_posix():
        raise QualificationError("source root is outside the pinned c3 cohort")
    scope = manifest.get("scope", {})
    if scope.get("campaign") != "c3" or scope.get("c4_and_frozen_tail2") != "excluded":
        raise QualificationError("c3-only source scope changed")
    if scope.get("epoch_ids") != [f"20261010T{hour:02d}0000Z" for hour in range(5, 11)]:
        raise QualificationError("c3 epoch selection changed")
    epochs = manifest.get("epochs", [])
    if not isinstance(epochs, list) or len(epochs) != 6:
        raise QualificationError("source manifest does not contain six c3 epochs")
    for epoch in epochs:
        epoch_id = epoch.get("epoch_id")
        if epoch_id not in scope["epoch_ids"] or epoch.get("status") != "frame_complete":
            raise QualificationError("unexpected or incomplete c3 epoch")
        if "c4" in epoch_id.lower() or "pair64" in epoch_id.lower():
            raise QualificationError("forbidden epoch identifier")
        epoch_root = C3_ROOT / "epochs" / epoch_id
        if not isinstance(epoch.get("full_blocks"), list) or len(epoch["full_blocks"]) != 16:
            raise QualificationError("each selected c3 frame must pin 16 full blocks")
        for item in epoch.get("files", []):
            rel = Path(item["path"])
            if (
                rel.is_absolute()
                or ".." in rel.parts
                or any("c4" in part.lower() or "pair64" in part.lower() for part in rel.parts)
            ):
                raise QualificationError("invalid or forbidden source path")
            source = epoch_root / rel
            if (
                not source.is_file()
                or source.stat().st_size != item["bytes"]
                or _sha_file(source) != item["sha256"]
            ):
                raise QualificationError(f"c3 source drift: {epoch_id}/{rel.as_posix()}")
    for source in manifest.get("source_receipts", []):
        path_item = Path(source["path"])
        if path_item not in SOURCE_RECEIPTS.values() or _sha_file(path_item) != source["sha256"]:
            raise QualificationError("pinned c3 receipt drift or unexpected receipt")
    pins = manifest.get("decoder_pins")
    if not isinstance(pins, dict) or not pins:
        raise QualificationError("decoder/source pins are missing")
    for rel_path, expected_sha in pins.items():
        source = Path(rel_path)
        if source.is_absolute() or ".." in source.parts or not source.is_file():
            raise QualificationError("invalid decoder/source pin path")
        if _sha_file(source) != expected_sha:
            raise QualificationError(f"decoder/source pin drift: {rel_path}")
    return {**manifest, "manifest_sha256": _sha_bytes(manifest_bytes)}


def _pubkey(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("pubkey"), str):
        return value["pubkey"]
    return None


def _resolved_keys(transaction: dict[str, Any]) -> list[str] | None:
    message = transaction.get("transaction", {}).get("message", {})
    meta = transaction.get("meta", {})
    static_raw = message.get("accountKeys")
    if not isinstance(static_raw, list):
        return None
    static = [_pubkey(item) for item in static_raw]
    if any(item is None for item in static):
        return None
    loaded = meta.get("loadedAddresses") or {}
    writable = loaded.get("writable", []) if isinstance(loaded, dict) else []
    readonly = loaded.get("readonly", []) if isinstance(loaded, dict) else []
    if not isinstance(writable, list) or not isinstance(readonly, list):
        return None
    if any(not isinstance(item, str) for item in writable + readonly):
        return None
    return [*static, *writable, *readonly]


def _token_entries(meta: dict[str, Any], field: str, account_index: int) -> list[dict[str, Any]]:
    entries = meta.get(field, [])
    if not isinstance(entries, list):
        return []
    return [
        entry
        for entry in entries
        if isinstance(entry, dict) and entry.get("accountIndex") == account_index
    ]


def classify_field_outcome(
    *,
    source_identity_valid: bool,
    transaction_success: bool,
    destination_owner_program_known: bool,
    closed_account_token_endpoints_known: bool,
    destination_close_effect_attributable: bool,
    fee_lamports_known: bool,
    rent_reserve_known: bool,
) -> str:
    """Classify the requested all-fields reconciliation without mixing units."""
    if not source_identity_valid or not transaction_success or not fee_lamports_known:
        return "unsupported"
    if not all(
        (
            destination_owner_program_known,
            closed_account_token_endpoints_known,
            destination_close_effect_attributable,
            rent_reserve_known,
        )
    ):
        return "ambiguous"
    return "exact"


def _decode_capture(path: Path, *, zstd: str) -> dict[str, Any]:
    result = subprocess.run([zstd, "-q", "-dc", str(path)], check=True, capture_output=True)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise QualificationError(f"malformed captured block response: {path}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
        raise QualificationError(f"missing block response object: {path}")
    return value["result"]


def build_receipt(
    *,
    manifest: dict[str, Any],
    decision_time_ns: int,
    tool_path: Path,
    zstd_path: str | None = None,
) -> dict[str, Any]:
    zstd = zstd_path or shutil.which("zstd")
    if not zstd:
        raise QualificationError("zstd executable unavailable; no capture was decoded")
    feasibility_path = SOURCE_RECEIPTS["native_close_feasibility"]
    flow_path = SOURCE_RECEIPTS["native_account_flow"]
    feasibility = _load(feasibility_path)
    flow_receipt = _load(flow_path)
    replay_receipt = _load(SOURCE_RECEIPTS["six_frame_replay"])
    if (
        replay_receipt.get("eligible_successful_sell23_candidates") != 553
        or replay_receipt.get("selected_full_blocks_verified") != 96
        or feasibility.get("counts")
        != {
            "censored_rows": 485,
            "exact_ledger_rows_preserved": 68,
            "failed_refs_outside_success_target": 2,
            "native_target_account_rows": 485,
            "receipt_hash_verified_full_blocks": 96,
            "successful_exact_layout_refs": 553,
        }
        or flow_receipt.get("counts", {}).get("source_account_flow_exact") != 485
        or flow_receipt.get("counts", {}).get("destination_ambiguous") != 485
    ):
        raise QualificationError("frozen c3 receipt denominators changed")
    feasibility_rows = feasibility.get("rows")
    flow_rows = flow_receipt.get("rows")
    if not isinstance(feasibility_rows, list) or not isinstance(flow_rows, list):
        raise QualificationError("c3 source receipts have no row arrays")

    manifest_epochs = {epoch["epoch_id"]: epoch for epoch in manifest["epochs"]}
    blocks: dict[tuple[str, int], tuple[dict[str, Any], dict[str, Any]]] = {}
    for epoch_id, epoch in manifest_epochs.items():
        ep_root = C3_ROOT / "epochs" / epoch_id
        for block_record in epoch["full_blocks"]:
            seq = block_record["sequence"]
            capture = ep_root / "captures" / f"{seq:012d}" / "response.json.zst"
            if _sha_file(capture) != block_record["compressed_capture_sha256"]:
                raise QualificationError("selected full-block capture changed after source freeze")
            block = _decode_capture(capture, zstd=zstd)
            if block.get("blockhash") != block_record["blockhash"]:
                raise QualificationError("selected full-block identity changed")
            txs = block.get("transactions")
            if not isinstance(txs, list):
                raise QualificationError("selected full block has no transaction list")
            blocks[(epoch_id, block_record["slot"])] = (
                block_record,
                {"transactions": txs, "epoch": epoch},
            )

    flow_index: dict[tuple[str, int, int, str], dict[str, Any]] = {}
    for row in flow_rows:
        key = (row["frame_id"], row["slot"], row["transaction_index"], row["signature_sha256"])
        if key in flow_index:
            raise QualificationError("duplicate c3 flow row identity")
        flow_index[key] = row

    outcomes: list[dict[str, Any]] = []
    for row in feasibility_rows:
        key = (row["frame_id"], row["slot"], row["transaction_index"], row["signature_sha256"])
        flow_row = flow_index.get(key)
        source_valid = flow_row is not None
        block_tuple = blocks.get((row["frame_id"], row["slot"]))
        tx: dict[str, Any] | None = None
        block_record: dict[str, Any] | None = None
        if block_tuple is not None:
            block_record = block_tuple[0]
            txs = block_tuple[1]["transactions"]
            tx_index = row["transaction_index"]
            if (
                type(tx_index) is int
                and 0 <= tx_index < len(txs)
                and isinstance(txs[tx_index], dict)
            ):
                tx = txs[tx_index]
        meta = tx.get("meta", {}) if tx else {}
        sigs = tx.get("transaction", {}).get("signatures", []) if tx else []
        signature = row.get("signature")
        signature_hash = (
            _sha_bytes(signature.encode("ascii")) if isinstance(signature, str) else None
        )
        source_valid = bool(
            source_valid
            and block_record is not None
            and tx is not None
            and signature_hash == row.get("signature_sha256")
            and sigs
            and sigs[0] == signature
            and block_record["response_sha256"] == row.get("raw_response_sha256")
            and row.get("capture_receipt_sha256")
            in {
                item["capture_receipt_sha256"]
                for item in manifest_epochs[row["frame_id"]]["full_blocks"]
                if item["slot"] == row["slot"]
            }
        )

        flow = flow_row.get("flow", {}) if flow_row else {}
        native_account = row.get("native_trace", {}).get("native_accounts", [{}])[0]
        keys = _resolved_keys(tx) if tx else None
        pre = meta.get("preBalances") if isinstance(meta, dict) else None
        post = meta.get("postBalances") if isinstance(meta, dict) else None
        balances_valid = (
            keys is not None
            and isinstance(pre, list)
            and isinstance(post, list)
            and len(keys) == len(pre) == len(post)
            and all(type(v) is int for v in pre + post)
        )
        dest_key = flow.get("close_destination")
        dest_indices = [i for i, pubkey in enumerate(keys or []) if pubkey == dest_key]
        dest_index = dest_indices[0] if len(dest_indices) == 1 else None
        fee = meta.get("fee") if isinstance(meta, dict) else None
        fee_payer = keys[0] if keys else None
        fee_valid = type(fee) is int and fee >= 0 and fee_payer is not None
        destination_delta = (
            post[dest_index] - pre[dest_index]
            if balances_valid and dest_index is not None
            else None
        )
        destination_token_before = (
            _token_entries(meta, "preTokenBalances", dest_index) if dest_index is not None else []
        )
        destination_token_after = (
            _token_entries(meta, "postTokenBalances", dest_index) if dest_index is not None else []
        )
        temp_index = flow.get("message_account_index")
        temp_before = (
            _token_entries(meta, "preTokenBalances", temp_index) if type(temp_index) is int else []
        )
        temp_after = (
            _token_entries(meta, "postTokenBalances", temp_index) if type(temp_index) is int else []
        )
        rent_key_present = bool(keys and RENT_SYSVAR in keys)
        close_path = next(
            (
                effect.get("path")
                for effect in native_account.get("close_effects", [])
                if effect.get("kind") == "close_account"
            ),
            None,
        )
        dest_trace = native_account.get("close_destination_touching_instruction_trace", [])
        sibling_paths = sorted(
            {
                tuple(item.get("path", []))
                for item in dest_trace
                if isinstance(item, dict) and item.get("path") != close_path
            }
        )
        success = bool(
            tx is not None and isinstance(meta, dict) and "err" in meta and meta.get("err") is None
        )
        source_valid = source_valid and balances_valid and dest_index is not None and fee_valid
        outcome = classify_field_outcome(
            source_identity_valid=source_valid,
            transaction_success=success,
            destination_owner_program_known=False,
            closed_account_token_endpoints_known=bool(temp_before and temp_after),
            destination_close_effect_attributable=not sibling_paths,
            fee_lamports_known=fee_valid,
            rent_reserve_known=False,
        )
        transfer_amount = flow.get("token_transfer_amount_atomic_units")
        transfer_decimals = flow.get("token_transfer_decimals")
        if transfer_amount is not None and type(transfer_amount) is not int:
            outcome = "unsupported"
        if transfer_decimals is not None and type(transfer_decimals) is not int:
            outcome = "unsupported"
        record = {
            "frame_id": row["frame_id"],
            "slot": row["slot"],
            "transaction_index": row["transaction_index"],
            "signature_sha256": row["signature_sha256"],
            "event_block_time": block_record["event_block_time"] if block_record else None,
            "receipt_ns": block_record["receipt_ns"] if block_record else None,
            "availability_ns": block_record["availability_ns"] if block_record else None,
            "blockhash": block_record["blockhash"] if block_record else None,
            "raw_response_sha256": block_record["response_sha256"] if block_record else None,
            "request_json_sha256": block_record["request_json_sha256"] if block_record else None,
            "capture_receipt_sha256": block_record["capture_receipt_sha256"]
            if block_record
            else None,
            "transaction_success": success,
            "source_identity_valid": source_valid,
            "destination_owner_program": {
                "status": "unknown_not_in_transaction_payload",
                "value": None,
            },
            "destination_token_endpoint": {
                "status": "present_in_tx_meta"
                if destination_token_before or destination_token_after
                else "absent",
                "before_entry_count": len(destination_token_before),
                "after_entry_count": len(destination_token_after),
                "unit": "token_atomic_units",
            },
            "closed_native_token_account_endpoints": {
                "status": "present_in_tx_meta" if temp_before and temp_after else "missing",
                "before_entry_count": len(temp_before),
                "after_entry_count": len(temp_after),
                "unit": "token_atomic_units",
            },
            "native_transfer_checked": {
                "amount_atomic_units": transfer_amount,
                "decimals_argument": transfer_decimals,
                "unit": "token_atomic_units",
            },
            "destination_transaction_boundary_delta": {
                "pre_lamports": pre[dest_index]
                if balances_valid and dest_index is not None
                else None,
                "post_lamports": post[dest_index]
                if balances_valid and dest_index is not None
                else None,
                "lamports": destination_delta,
                "destination_fee_payer": bool(dest_index == 0),
                "status": "exact_transaction_boundary"
                if destination_delta is not None
                else "unsupported",
            },
            "transaction_fee": {
                "lamports": fee if fee_valid else None,
                "fee_payer_sha256": _sha_bytes(fee_payer.encode()) if fee_valid else None,
                "status": "exact" if fee_valid else "unsupported",
            },
            "close_destination_effect": {
                "close_sweep_candidate_lamports": flow.get(
                    "close_sweep_lamports_by_account_conservation"
                ),
                "distinct_sibling_instruction_paths": len(sibling_paths),
                "status": "ambiguous_sibling_effects"
                if sibling_paths
                else "not_independently_attributed",
            },
            "rent_reserve": {
                "lamports": None,
                "status": "unknown_not_observed",
                "rent_sysvar_in_message": rent_key_present,
            },
            "temporary_native_account_flow": {
                "status": flow.get("status"),
                "pre_lamports": flow.get("pre_lamports"),
                "created_lamports": flow.get("create_lamports"),
                "system_credited_lamports": flow.get("system_credited_lamports"),
                "system_debited_lamports": flow.get("system_debited_lamports"),
                "post_lamports": flow.get("post_lamports"),
                "close_sweep_candidate_lamports": flow.get(
                    "close_sweep_lamports_by_account_conservation"
                ),
                "owner_program": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
                if flow.get("create_lamports") is not None
                else None,
                "owner_program_status": "creation_instruction_owner_only",
                "unit": "lamports",
            },
            "outcome": outcome,
        }
        outcomes.append(record)

    outcomes.sort(
        key=lambda item: (
            item["frame_id"],
            item["slot"],
            item["transaction_index"],
            item["signature_sha256"],
        )
    )
    counts = {
        name: sum(row["outcome"] == name for row in outcomes)
        for name in ("exact", "ambiguous", "unsupported")
    }
    if (
        len(outcomes) != 485
        or len(outcomes) != len(feasibility_rows)
        or len(flow_index) != 485
        or len(flow_index) != len(flow_rows)
        or len(blocks) != 96
    ):
        raise QualificationError("source row denominator changed")
    if counts["exact"] + counts["ambiguous"] + counts["unsupported"] != len(outcomes):
        raise QualificationError("row outcomes do not reconcile")
    decision_time_ns = int(decision_time_ns)
    if decision_time_ns <= 0:
        raise QualificationError("decision time must be positive nanoseconds")
    if decision_time_ns < max(row["availability_ns"] for row in outcomes):
        raise QualificationError("decision time predates source availability")
    report = {
        "schema_version": SCHEMA_VERSION,
        "receipt_kind": "pumpswap_sell23_process_qualification_v1",
        "status": "partial_fields_reconciled_full_destination_settlement_unqualified",
        "decision_time_ns": decision_time_ns,
        "source_manifest_path": MANIFEST_PATH.as_posix(),
        "source_manifest_sha256": manifest["manifest_sha256"],
        "source_manifest_content_sha256": manifest["content_sha256"],
        "decoder_pins": manifest["decoder_pins"],
        "tool_sha256": _sha_file(tool_path),
        "code_commit": subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip(),
        "scope": {
            "campaign": "c3",
            "frame_ids": sorted(manifest_epochs),
            "selected_full_blocks_expected": 96,
            "selected_full_blocks_verified": len(blocks),
            "failed_1100_epoch_excluded": True,
            "pair64_used": False,
            "c4_and_frozen_tail2_excluded": True,
            "provider_requests_made": 0,
        },
        "source_receipts": [
            {
                "kind": name,
                "path": source["path"],
                "sha256": source["sha256"],
                "status": source["status"],
                "git_commit": source["git_commit"],
            }
            for name, source in zip(
                ("six_frame_replay", "native_close_feasibility", "native_account_flow"),
                manifest["source_receipts"],
                strict=True,
            )
        ],
        "frame_bindings": [
            {
                "frame_id": epoch["epoch_id"],
                "plan_sha256": epoch["plan_sha256"],
                "frame_sha256": epoch["frame_sha256"],
                "summary_sha256": epoch["summary_sha256"],
                "selection_plan_sha256": epoch["selection_plan_sha256"],
                "selected_full_block_count": epoch["selected_full_block_count"],
                "start": epoch["start"],
                "end_exclusive": epoch["end_exclusive"],
            }
            for epoch in manifest["epochs"]
        ],
        "denominators": {
            "selected_frames": 6,
            "selected_full_blocks": 96,
            "missing_full_blocks": 0,
            "unsupported_full_blocks": 0,
            "successful_exact_class_candidates": 553,
            "exact_ledger_rows_excluded_from_censored_scope": 68,
            "selected_censored_native_close_rows": 485,
            "failed_structural_refs_outside_success_rows": 2,
            "failed_structural_ref_attempts": 2,
            "censored_native_close_rows_expected": 485,
            "censored_native_close_rows_joined": len(outcomes),
            "missing_rows": 485 - len(outcomes),
            "unsupported_rows": counts["unsupported"],
            "zero_claim_scope": "only the verified 96 selected c3 full-block captures",
            "row_outcomes": counts,
        },
        "field_counts": {
            "source_identity_valid": sum(r["source_identity_valid"] for r in outcomes),
            "successful_transactions": sum(r["transaction_success"] for r in outcomes),
            "destination_boundary_delta_lamports_exact": sum(
                r["destination_transaction_boundary_delta"]["status"]
                == "exact_transaction_boundary"
                for r in outcomes
            ),
            "fee_lamports_exact": sum(r["transaction_fee"]["status"] == "exact" for r in outcomes),
            "destination_owner_program_unknown": sum(
                r["destination_owner_program"]["value"] is None for r in outcomes
            ),
            "closed_native_token_account_endpoints_missing": sum(
                r["closed_native_token_account_endpoints"]["status"] == "missing" for r in outcomes
            ),
            "rent_reserve_unknown": sum(
                r["rent_reserve"]["status"] == "unknown_not_observed" for r in outcomes
            ),
            "ambiguous_destination_close_effects": sum(
                r["close_destination_effect"]["status"] == "ambiguous_sibling_effects"
                for r in outcomes
            ),
        },
        "unit_contract": {
            "native_balances_fee_create_close_sweep": "lamports",
            "token_transfer_amount": "token_atomic_units",
            "decimals": "integer instruction argument; no UI conversion performed",
            "rent_reserve": "lamports unknown; not inferred from creation funding",
        },
        "limitations": [
            (
                "Transaction message data does not contain the account owner program "
                "for the close destination."
            ),
            (
                "All censored rows lack pre/post token-balance endpoints for the "
                "closed native token account."
            ),
            (
                "Close destinations have sibling transaction touches, so the "
                "transaction-boundary lamport delta is not attributed to CloseAccount."
            ),
            (
                "The Rent sysvar and historical reserve are not observed; 1,488,440 "
                "lamports is create funding, not asserted rent."
            ),
            (
                "No wallet-net, realized token amount, price, USD PnL, edge, or "
                "training eligibility is derived."
            ),
            (
                "Pair64 was not added: c3 transaction payloads lack the account-state "
                "fields needed for owner/rent qualification. No further acquisition "
                "is authorized by this horizon."
            ),
        ],
        "rows": outcomes,
    }
    report["content_sha256"] = _canonical_hash(
        {k: v for k, v in report.items() if k not in ("content_sha256", "decision_time_ns")}
    )
    return report


def write_receipt(path: Path, report: dict[str, Any]) -> None:
    payload = _canonical(report) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--decision-time-ns", type=int, required=True)
    args = parser.parse_args(argv)
    manifest = verify_source_manifest(args.manifest)
    report = build_receipt(
        manifest=manifest,
        decision_time_ns=args.decision_time_ns,
        tool_path=Path(__file__),
    )
    write_receipt(args.output, report)
    print(
        json.dumps(
            {
                "output": args.output.as_posix(),
                "status": report["status"],
                "content_sha256": report["content_sha256"],
                "rows": report["denominators"]["row_outcomes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
