#!/usr/bin/env python3
"""Build a read-only structural input manifest from independently sealed v2 frames.

This module reads captures only after the external-v2 verifier has accepted the
plan, complete event chain, selected-block pairs, and seals. It never opens a
provider client and never claims market-wide completeness or economic meaning.
"""

from __future__ import annotations

import json
import os
import stat
import time
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, cast

from memecoin_pumpswap.normalize import NormalizationError, normalize_compiled_transaction
from tools import brain_senses_external_schema_v2 as lease_schema
from tools import brain_senses_external_verify_v2 as lease_verify
from tools.brain_senses_external_structural_input_schema_v1 import (
    BOOTSTRAP_BUCKET,
    FINGERPRINT_SCHEMA,
    FRAME_BUCKETS,
    PUBLICATION_SCHEMA,
    PUMP_PROGRAM_ID,
    SCHEMA,
    VERSION,
    StructuralInputError,
    canonical,
    digest,
    require_digest,
    safe_relative,
)


class Reader(Protocol):
    def read_bytes(self, relative: str, *, limit: int = 64_000_000) -> bytes: ...

    def list_files(self) -> list[str]: ...


class Writer(Protocol):
    def exists(self, relative: str) -> bool: ...

    def read_bytes(self, relative: str, *, max_bytes: int = 64 * 1024 * 1024) -> bytes: ...

    def create(self, relative: str, payload: bytes, *, mode: int = 0o600) -> None: ...


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise StructuralInputError(reason)


def _binding_sources() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    paths = (
        "tools/brain_senses_external_structural_input_schema_v1.py",
        "tools/brain_senses_external_structural_input_v1.py",
        "tools/brain_senses_external_structural_publish_v1.py",
        "tools/brain_senses_external_verify_v2.py",
        "tools/brain_senses_external_schema_v2.py",
        "tools/brain_senses_external_writer_v1.py",
        "tools/brain_senses_block_epoch_v2.py",
        "tools/brain_senses_block_provider_pilot.py",
        "src/memecoin_pumpswap/normalize.py",
        "src/memecoin_pumpswap/schema.py",
    )
    return {relative: digest((root / relative).read_bytes()) for relative in paths}


def _decode(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            _require(key not in out, "duplicate_json_key")
            out[key] = value
        return out

    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: _bad_constant())
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise StructuralInputError("source_json_invalid") from exc


def _bad_constant() -> Any:
    raise StructuralInputError("source_json_nonfinite")


def _json(reader: Reader, path: str) -> tuple[dict[str, Any], bytes]:
    raw = reader.read_bytes(path)
    value = _decode(raw)
    _require(isinstance(value, dict), "source_json_object")
    return cast(dict[str, Any], value), raw


def _expected_schedule(plan: Mapping[str, Any]) -> None:
    schedule = plan.get("schedule")
    _require(isinstance(schedule, dict), "lease_schedule_shape")
    schedule = cast(dict[str, Any], schedule)
    bootstrap = schedule.get("bootstrap")
    frames = schedule.get("frames")
    _require(
        isinstance(bootstrap, dict)
        and bootstrap.get("bucket") == BOOTSTRAP_BUCKET
        and isinstance(frames, list)
        and len(frames) == len(FRAME_BUCKETS)
        and all(isinstance(row, dict) for row in frames)
        and tuple(row.get("bucket") for row in frames if isinstance(row, dict)) == FRAME_BUCKETS,
        "lease_schedule_not_predeclared_0600",
    )


def _resolved_status(meta: Any) -> str:
    if not isinstance(meta, dict) or "err" not in meta:
        return "unknown"
    return "success" if meta["err"] is None else "failed"


def _fingerprint(program_id: str, data_hex: str, account_count: int) -> str:
    try:
        instruction_data = bytes.fromhex(data_hex)
    except ValueError as exc:
        raise StructuralInputError("normalized_instruction_data_invalid") from exc
    return digest(
        canonical(
            {
                "program_id": program_id,
                "instruction_data_sha256": digest(instruction_data),
                "account_count": account_count,
            }
        )
    )


def _block_occurrences(
    *,
    block: dict[str, Any],
    bucket: str,
    slot: int,
    blockhash: str,
    capture_id: str,
    source_refs: Mapping[str, str],
    event_time_ns: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    transactions = block.get("transactions")
    _require(isinstance(transactions, list), "verified_block_transactions_missing")
    transactions = cast(list[Any], transactions)
    references: list[dict[str, Any]] = []
    statuses: Counter[str] = Counter()
    tx_rows: list[dict[str, Any]] = []
    scan_unknown = 0
    for entry in transactions:
        _require(isinstance(entry, dict), "verified_transaction_shape")
        transaction = entry.get("transaction")
        signatures = transaction.get("signatures") if isinstance(transaction, dict) else None
        _require(
            isinstance(signatures, list)
            and bool(signatures)
            and isinstance(signatures[0], str)
            and bool(signatures[0]),
            "verified_transaction_signature",
        )
        signatures = cast(list[Any], signatures)
        signature_sha256 = digest(signatures[0].encode("utf-8"))
        meta = entry.get("meta")
        tx_status = _resolved_status(meta)
        _require(tx_status in {"success", "failed", "unknown"}, "transaction_status")
        statuses[tx_status] += 1
        try:
            normalized = normalize_compiled_transaction(entry)
        except NormalizationError as exc:
            scan_unknown += 1
            tx_rows.append(
                {
                    "signature_sha256": signature_sha256,
                    "status": tx_status,
                    "structural_scan_status": "unknown",
                    "failure_code": exc.category,
                    "pump_reference_count": None,
                }
            )
            continue
        tx_reference_count = 0
        for instruction in normalized["outer_instructions"]:
            if instruction["program_id"] != PUMP_PROGRAM_ID:
                continue
            reference = _reference_row(
                instruction=instruction,
                capture_id=capture_id,
                signature_sha256=signature_sha256,
                instruction_path={"outer_index": instruction["outer_index"], "inner_index": None},
                bucket=bucket,
                slot=slot,
                blockhash=blockhash,
                tx_status=tx_status,
                event_time_ns=event_time_ns,
                source_refs=source_refs,
            )
            references.append(reference)
            tx_reference_count += 1
        for instruction in normalized["inner_instructions"]:
            if instruction["program_id"] != PUMP_PROGRAM_ID:
                continue
            path = instruction["path"]
            _require(isinstance(path, list) and len(path) == 2, "inner_instruction_path")
            reference = _reference_row(
                instruction=instruction,
                capture_id=capture_id,
                signature_sha256=signature_sha256,
                instruction_path={"outer_index": path[0], "inner_index": path[1]},
                bucket=bucket,
                slot=slot,
                blockhash=blockhash,
                tx_status=tx_status,
                event_time_ns=event_time_ns,
                source_refs=source_refs,
            )
            references.append(reference)
            tx_reference_count += 1
        tx_rows.append(
            {
                "signature_sha256": signature_sha256,
                "status": tx_status,
                "structural_scan_status": "scanned",
                "failure_code": None,
                "pump_reference_count": tx_reference_count,
            }
        )
    return references, {
        "transaction_count": len(transactions),
        "transaction_status_counts": {
            key: statuses.get(key, 0) for key in ("success", "failed", "unknown")
        },
        "transactions_with_unknown_structural_scan": scan_unknown,
        "pump_reference_count": len(references),
        "transactions": tx_rows,
    }


def _reference_row(
    *,
    instruction: Mapping[str, Any],
    capture_id: str,
    signature_sha256: str,
    instruction_path: dict[str, int | None],
    bucket: str,
    slot: int,
    blockhash: str,
    tx_status: str,
    event_time_ns: int | None,
    source_refs: Mapping[str, str],
) -> dict[str, Any]:
    account_indexes = instruction.get("account_indexes")
    data_hex = instruction.get("data_hex")
    program_id = instruction.get("program_id")
    _require(
        isinstance(program_id, str)
        and program_id == PUMP_PROGRAM_ID
        and isinstance(account_indexes, list)
        and all(type(index) is int and index >= 0 for index in account_indexes)
        and isinstance(data_hex, str),
        "pump_instruction_shape",
    )
    account_indexes = cast(list[int], account_indexes)
    data_hex = cast(str, data_hex)
    program_id = cast(str, program_id)
    class_sha = _fingerprint(
        program_id,
        data_hex,
        len(account_indexes),
    )
    reference_id = digest(
        canonical(
            {
                "capture_id": capture_id,
                "signature_sha256": signature_sha256,
                "instruction_path": instruction_path,
            }
        )
    )
    return {
        "reference_id": reference_id,
        "capture_id": capture_id,
        "bucket": bucket,
        "slot": slot,
        "blockhash": blockhash,
        "signature_sha256": signature_sha256,
        "transaction_status": tx_status,
        "instruction_path": instruction_path,
        "program_id": program_id,
        "instruction_data_sha256": digest(bytes.fromhex(data_hex)),
        "account_count": len(account_indexes),
        "class_fingerprint_sha256": class_sha,
        "class_fingerprint_schema": FINGERPRINT_SCHEMA,
        "source_event_time_ns": event_time_ns,
        "request_path": source_refs["request_path"],
        "request_sha256": source_refs["request_sha256"],
        "result_path": source_refs["result_path"],
        "result_sha256": source_refs["result_sha256"],
        "capture_receipt_path": source_refs["capture_receipt_path"],
        "capture_receipt_sha256": source_refs["capture_receipt_sha256"],
        "response_path": source_refs["response_path"],
        "response_sha256": source_refs["response_sha256"],
        "received_ns": source_refs["received_ns"],
        "available_ns": source_refs["available_ns"],
        "decision_ns": None,
    }


def _read_selected_block(
    reader: Reader,
    *,
    plan: Mapping[str, Any],
    plan_sha256: str,
    bucket: str,
    frame_index: int,
    selected: Mapping[str, Any],
    ordinal: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    base = f"buckets/{bucket}"
    response_path = f"{base}/captures/{ordinal:02d}/response.json"
    receipt_path = f"{base}/captures/{ordinal:02d}/receipt.json"
    result_path = f"{base}/results/{ordinal:02d}.json"
    request_path = f"{base}/requests/{ordinal:02d}.json"
    response_raw = reader.read_bytes(response_path)
    request, request_raw = _json(reader, request_path)
    receipt, receipt_raw = _json(reader, receipt_path)
    result, result_raw = _json(reader, result_path)
    _require(
        request.get("method") == "getBlock"
        and isinstance(request.get("params"), list)
        and len(request["params"]) == 2
        and request["params"][0] == selected.get("slot")
        and isinstance(request["params"][1], dict)
        and request["params"][1].get("transactionDetails") == "full",
        "full_capture_request_slot",
    )
    response = _decode(response_raw)
    _require(
        isinstance(response, dict)
        and response.get("jsonrpc") == "2.0"
        and type(response.get("id")) is int
        and response.get("id") == 1
        and "error" not in response
        and isinstance(response.get("result"), dict),
        "full_capture_response_envelope",
    )
    block = cast(dict[str, Any], response["result"])
    identity = selected.get("block_identity")
    transaction_rows = block.get("transactions")
    _require(
        isinstance(identity, dict)
        and isinstance(transaction_rows, list)
        and all(isinstance(row, dict) for row in transaction_rows)
        and block.get("blockhash") is not None
        and all(
            block.get(key) == identity.get(key)
            for key in ("blockhash", "previousBlockhash", "parentSlot", "blockTime", "blockHeight")
        )
        and [
            row["transaction"]["signatures"][0]
            for row in transaction_rows
            if isinstance(row.get("transaction"), dict)
            and isinstance(row["transaction"].get("signatures"), list)
            and row["transaction"]["signatures"]
        ]
        == selected.get("signatures"),
        "selected_block_identity",
    )
    slot = cast(int, selected["slot"])
    blockhash = cast(str, block["blockhash"])
    _require(
        receipt.get("response_sha256") == digest(response_raw)
        and receipt.get("storage")
        == {"path": "response.json", "bytes": len(response_raw), "codec": "raw"}
        and result.get("response_sha256") == digest(response_raw)
        and result.get("response_bytes") == len(response_raw)
        and result.get("stored_response_bytes") == len(response_raw)
        and result.get("result_status") == "parsed"
        and result.get("status") == "captured"
        and receipt.get("schema") == lease_schema.RECEIPT_SCHEMA
        and result.get("schema") == lease_schema.RESULT_SCHEMA
        and request.get("schema") == lease_schema.REQUEST_SCHEMA
        and receipt.get("provider") == plan.get("provider")
        and result.get("provider") == plan.get("provider")
        and receipt.get("request_id") == request.get("request_id") == result.get("request_id")
        and receipt.get("request_sha256")
        == request.get("request_sha256")
        == result.get("request_sha256")
        and receipt.get("wire_request_sha256")
        == request.get("wire_request_sha256")
        == result.get("wire_request_sha256")
        and result.get("decision_ns") == request.get("decision_ns")
        and result.get("method") == request.get("method") == "getBlock"
        and result.get("event_slot") == selected.get("slot")
        and result.get("availability_ns") is not None
        and type(receipt.get("received_ns")) is int
        and type(result.get("availability_ns")) is int
        and result["availability_ns"] >= receipt["received_ns"],
        "capture_result_receipt_binding",
    )
    event_time = block.get("blockTime")
    event_ns = event_time * 1_000_000_000 if type(event_time) is int else None
    capture_seed = {
        "plan_sha256": plan_sha256,
        "bucket": bucket,
        "slot": slot,
        "blockhash": blockhash,
        "request_sha256": digest(request_raw),
        "result_sha256": digest(result_raw),
        "receipt_sha256": digest(receipt_raw),
        "response_sha256": digest(response_raw),
    }
    capture_id = "v2-" + digest(canonical(capture_seed))
    refs = {
        "request_path": request_path,
        "request_sha256": digest(request_raw),
        "result_path": result_path,
        "result_sha256": digest(result_raw),
        "capture_receipt_path": receipt_path,
        "capture_receipt_sha256": digest(receipt_raw),
        "response_path": response_path,
        "response_sha256": digest(response_raw),
        "received_ns": receipt["received_ns"],
        "available_ns": result["availability_ns"],
    }
    occurrences, tx_summary = _block_occurrences(
        block=block,
        bucket=bucket,
        slot=slot,
        blockhash=blockhash,
        capture_id=capture_id,
        source_refs=refs,
        event_time_ns=event_ns,
    )
    source_capture = {
        "capture_id": capture_id,
        "bucket": bucket,
        "frame_index": frame_index,
        "slot": slot,
        "blockhash": blockhash,
        "source_event_time_ns": event_ns,
        "received_ns": receipt["received_ns"],
        "available_ns": result["availability_ns"],
        **refs,
    }
    return source_capture, occurrences, tx_summary


def compile_from_sealed_v2(
    reader: Reader,
    predecessor_reader: Reader,
    *,
    expected_plan_sha256: str,
    expected_lease_sources: Mapping[str, str],
) -> dict[str, Any]:
    """Verify v2 lineage first, then build a deterministic sampled structural manifest.

    Incomplete or missed predeclared frames are reported as unknown denominators
    and make the result ineligible for publication. The function never repairs
    source state or performs network operations.
    """
    require_digest(expected_plan_sha256, "expected_plan_digest")
    plan_raw = reader.read_bytes(lease_schema.PLAN_FILE)
    plan = lease_verify.decode(plan_raw)
    _require(isinstance(plan, dict), "lease_plan_shape")
    _expected_schedule(plan)
    verification = lease_verify.verify(
        reader,
        expected_plan_sha256=expected_plan_sha256,
        expected_sources=expected_lease_sources,
        predecessor_reader=predecessor_reader,
    )
    _require(verification.get("plan_sha256") == expected_plan_sha256, "verified_plan_pin")
    frames_plan = plan["schedule"]["frames"]
    published_paths = set(reader.list_files())
    state_rows: list[dict[str, Any]] = []
    for row in frames_plan:
        bucket = row["bucket"]
        if verification.get("sealed", {}).get(bucket) is True:
            state = "sealed"
        elif bucket in verification.get("missed", set()):
            state = "missed"
        else:
            state = "unknown_incomplete"
        intents = verification.get("intents", {}).get(bucket, [])
        result_status_counts: Counter[str] = Counter()
        for ordinal in intents:
            result_path = f"buckets/{bucket}/results/{ordinal:02d}.json"
            if result_path not in published_paths:
                result_status_counts["missing"] += 1
                continue
            result, _ = _json(reader, result_path)
            result_status = result.get("result_status")
            if result.get("status") == "ambiguous" or result_status == "unknown":
                result_status_counts["ambiguous"] += 1
            elif result_status in {"parsed", "provider_error", "invalid_response"}:
                result_status_counts[result_status] += 1
            else:
                result_status_counts["unknown_status"] += 1
        state_rows.append(
            {
                "frame_index": row["index"],
                "bucket": bucket,
                "start_ns": row["start_ns"],
                "end_ns": row["end_ns"],
                "state": state,
                "request_intents": len(intents),
                "unknown_request_outcomes": len(intents) if state != "sealed" else 0,
                "request_result_status_counts": {
                    key: result_status_counts.get(key, 0)
                    for key in (
                        "parsed",
                        "provider_error",
                        "invalid_response",
                        "ambiguous",
                        "missing",
                        "unknown_status",
                    )
                },
            }
        )
    bootstrap_bucket = plan["schedule"]["bootstrap"]["bucket"]
    complete = (
        verification.get("status") == "verified_prefix"
        and verification.get("sealed", {}).get(bootstrap_bucket) is True
        and all(row["state"] == "sealed" for row in state_rows)
        and len(state_rows) == len(FRAME_BUCKETS)
    )
    root_bindings = _binding_sources()
    base: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": VERSION,
        "status": "sealed_sample_ready" if complete else "withheld_incomplete",
        "publishable": complete,
        "point_in_time_eligible": False,
        "decision_ns": None,
        "scope": {
            "bootstrap_bucket": BOOTSTRAP_BUCKET,
            "frame_buckets": list(FRAME_BUCKETS),
            "provider": plan["provider"],
            "endpoint_origin": plan["endpoint_origin"],
            "plan_sha256": expected_plan_sha256,
            "event_tail_sha256": verification.get("event_tail_sha256"),
            "source_code_sha256": dict(sorted(expected_lease_sources.items())),
            "builder_code_sha256": dict(sorted(root_bindings.items())),
            "storage": plan["storage"],
            "selection_rule": "uniform_without_replacement_from_finalized_getblocks_members",
            "fingerprint_schema": FINGERPRINT_SCHEMA,
        },
        "schedule_denominators": {
            "scheduled_frames": len(state_rows),
            "sealed_frames": sum(row["state"] == "sealed" for row in state_rows),
            "missed_frames": sum(row["state"] == "missed" for row in state_rows),
            "unknown_incomplete_frames": sum(
                row["state"] == "unknown_incomplete" for row in state_rows
            ),
            "request_intents_in_incomplete_frames": sum(
                row["request_intents"] for row in state_rows if row["state"] != "sealed"
            ),
        },
        "frames": [],
        "captures": [],
        "references": [],
        "totals": {
            "produced_inventory_slots": 0,
            "selected_blocks": 0,
            "verified_blocks": 0,
            "unselected_produced_slots": 0,
            "scanned_transactions": 0,
            "successful_transactions": 0,
            "failed_transactions": 0,
            "unknown_transactions": 0,
            "pump_program_references": 0,
            "unknown_structural_scan_transactions": 0,
            "economic_trade_count": None,
            "market_completeness_claim": False,
        },
        "frame_states": state_rows,
    }
    if not complete:
        return base

    certificate = plan["predecessor"]["certificate"]
    predecessor_gaps = certificate["gap_intervals"]
    bootstrap_state = verification.get("buckets", {}).get(bootstrap_bucket)
    _require(isinstance(bootstrap_state, dict), "verified_bootstrap_missing")
    bootstrap_bundle = bootstrap_state.get("bundle")
    _require(
        isinstance(bootstrap_bundle, dict) and type(bootstrap_bundle.get("bootstrap_slot")) is int,
        "verified_bootstrap_slot_missing",
    )
    bootstrap_uncovered = {
        "start": certificate["frontier"],
        "end_exclusive": bootstrap_bundle["bootstrap_slot"],
        "reason": "uncovered_since_predecessor_no_backfill",
        "bootstrap_bucket": bootstrap_bucket,
    }
    expected_gaps = [*predecessor_gaps, bootstrap_uncovered]
    verified_gaps = verification.get("gap_intervals")
    _require(
        isinstance(verified_gaps, list) and verified_gaps == expected_gaps,
        "verified_gap_lineage_mismatch",
    )
    base["predecessor_certificate_sha256"] = plan["predecessor"]["certificate_sha256"]
    base["predecessor_gap_intervals"] = predecessor_gaps
    base["bootstrap_uncovered_interval"] = bootstrap_uncovered
    base["verified_gap_intervals"] = verified_gaps

    bundle_sources = verification.get("events")
    _require(isinstance(bundle_sources, list), "verified_event_chain_missing")
    from tools.brain_senses_external_verify_v2 import _verify_bucket

    frontier = plan["predecessor"]["certificate"]["frontier"]
    frame_artifacts: list[dict[str, Any]] = []
    captures: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    totals = dict(base["totals"])
    for frame_plan in frames_plan:
        bucket = frame_plan["bucket"]
        verified_bucket = _verify_bucket(reader, plan, bucket, frontier)
        frontier = verified_bucket["frontier"]
        bundle = verified_bucket["bundle"]
        frame_summary = bundle["frame"]
        selected_slots = frame_summary["selected_slots"]
        selected_blocks = bundle["selected_blocks"]
        denominator = frame_summary["denominator"]
        _require(
            isinstance(selected_slots, list)
            and isinstance(selected_blocks, list)
            and type(denominator) is int
            and denominator >= len(selected_blocks)
            and frame_summary["selection_count"] == len(selected_blocks)
            and selected_slots == [row.get("slot") for row in selected_blocks]
            and len(selected_slots) == len(set(selected_slots)),
            "selected_block_count_mismatch",
        )
        block_rows: list[dict[str, Any]] = []
        frame_refs: list[dict[str, Any]] = []
        frame_tx_counts: Counter[str] = Counter()
        frame_scan_unknown = 0
        full_request_ordinals = {
            entry["slot"]: 3 + 2 * index for index, entry in enumerate(selected_blocks)
        }
        for selected in selected_blocks:
            slot = selected["slot"]
            capture, block_refs, tx_summary = _read_selected_block(
                reader,
                plan=plan,
                plan_sha256=expected_plan_sha256,
                bucket=bucket,
                frame_index=frame_plan["index"],
                selected=selected,
                ordinal=full_request_ordinals[slot],
            )
            captures.append(capture)
            frame_refs.extend(block_refs)
            refs_by_tx = Counter(ref["signature_sha256"] for ref in block_refs)
            tx_summary = {
                **tx_summary,
                "pump_reference_counts_by_signature_sha256": dict(sorted(refs_by_tx.items())),
            }
            block_rows.append(
                {
                    "slot": slot,
                    "blockhash": selected["block_identity"]["blockhash"],
                    "capture_id": capture["capture_id"],
                    "capture_receipt_path": capture["capture_receipt_path"],
                    "capture_receipt_sha256": capture["capture_receipt_sha256"],
                    "response_path": capture["response_path"],
                    "response_sha256": capture["response_sha256"],
                    "received_ns": capture["received_ns"],
                    "available_ns": capture["available_ns"],
                    "transaction_summary": tx_summary,
                }
            )
            for status, count in tx_summary["transaction_status_counts"].items():
                frame_tx_counts[status] += count
            frame_scan_unknown += tx_summary["transactions_with_unknown_structural_scan"]
        frame_artifacts.append(
            {
                "frame_index": frame_plan["index"],
                "bucket": bucket,
                "state": "sealed_sampled",
                "interval": {"start_ns": frame_plan["start_ns"], "end_ns": frame_plan["end_ns"]},
                "anchor_slot": frame_summary["anchor_slot"],
                "inventory_sha256": frame_summary["inventory_sha256"],
                "produced_inventory_slots": denominator,
                "selected_blocks": len(selected_blocks),
                "unselected_produced_slots": denominator - len(selected_blocks),
                "inclusion_probability": frame_summary["inclusion_probability"],
                "selection_rule": frame_summary["selection_rule"],
                "verified_block_count": len(block_rows),
                "scanned_transactions": sum(
                    block["transaction_summary"]["transaction_count"] for block in block_rows
                ),
                "transaction_status_counts": {
                    key: frame_tx_counts.get(key, 0) for key in ("success", "failed", "unknown")
                },
                "pump_program_references": len(frame_refs),
                "unknown_structural_scan_transactions": frame_scan_unknown,
                "economic_trade_count": None,
                "market_completeness_claim": False,
                "blocks": block_rows,
            }
        )
        references.extend(frame_refs)
        totals["produced_inventory_slots"] += denominator
        totals["selected_blocks"] += len(selected_blocks)
        totals["verified_blocks"] += len(block_rows)
        totals["unselected_produced_slots"] += denominator - len(selected_blocks)
        totals["scanned_transactions"] += sum(
            block["transaction_summary"]["transaction_count"] for block in block_rows
        )
        totals["successful_transactions"] += frame_tx_counts.get("success", 0)
        totals["failed_transactions"] += frame_tx_counts.get("failed", 0)
        totals["unknown_transactions"] += frame_tx_counts.get("unknown", 0)
        totals["pump_program_references"] += len(frame_refs)
        totals["unknown_structural_scan_transactions"] += frame_scan_unknown
    references.sort(
        key=lambda row: (
            row["bucket"],
            row["slot"],
            row["signature_sha256"],
            row["instruction_path"]["outer_index"],
            -1
            if row["instruction_path"]["inner_index"] is None
            else row["instruction_path"]["inner_index"],
        )
    )
    capture_ids = [row["capture_id"] for row in captures]
    reference_ids = [row["reference_id"] for row in references]
    _require(len(capture_ids) == len(set(capture_ids)), "duplicate_capture_id")
    _require(len(reference_ids) == len(set(reference_ids)), "duplicate_reference_id")
    base["frames"] = frame_artifacts
    base["captures"] = captures
    base["references"] = references
    base["totals"] = totals
    base["maximum_source_availability_ns"] = max(
        (capture["available_ns"] for capture in captures), default=None
    )
    scan_complete = (
        totals["unknown_transactions"] == 0 and totals["unknown_structural_scan_transactions"] == 0
    )
    base["publishable"] = scan_complete
    base["status"] = "sealed_sample_ready" if scan_complete else "withheld_unknown_transaction_scan"
    return base


def _fsync_existing(writer: Writer, relative: str) -> None:
    """Re-fsync an orphaned create-only artifact through its held writer descriptor."""
    # DescriptorWriter's private traversal is intentionally used here so replay
    # stays anchored to its held root fd and the same no-follow/device checks.
    parent, name = writer._parent_and_name(relative)  # type: ignore[attr-defined]
    parent_fd = writer._directory(parent)  # type: ignore[attr-defined]
    try:
        writer._prewrite("fsync")  # type: ignore[attr-defined]
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            info = os.fstat(fd)
            _require(
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.getuid()
                and info.st_dev == writer.device  # type: ignore[attr-defined]
                and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600,
                "orphan_artifact_identity",
            )
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _publication_receipt(
    *,
    artifact: Mapping[str, Any],
    manifest_path: str,
    service_id: str,
    source_relative_root: str,
    output_relative_root: str,
    root_id: str,
    volume_uuid: str,
    confirmed_ns: int,
) -> dict[str, Any]:
    artifact_raw = canonical(artifact)
    return {
        "schema": PUBLICATION_SCHEMA,
        "schema_version": VERSION,
        "manifest_path": manifest_path,
        "manifest_sha256": digest(artifact_raw),
        "plan_sha256": artifact["scope"]["plan_sha256"],
        "service_id": service_id,
        "source_relative_root": source_relative_root,
        "output_relative_root": output_relative_root,
        "event_tail_sha256": artifact["scope"]["event_tail_sha256"],
        "source_code_sha256": artifact["scope"]["source_code_sha256"],
        "builder_code_sha256": artifact["scope"]["builder_code_sha256"],
        "predecessor_certificate_sha256": artifact["predecessor_certificate_sha256"],
        "predecessor_gap_intervals": artifact["predecessor_gap_intervals"],
        "bootstrap_uncovered_interval": artifact["bootstrap_uncovered_interval"],
        "verified_gap_intervals": artifact["verified_gap_intervals"],
        "root_id": root_id,
        "volume_uuid": volume_uuid,
        "maximum_source_availability_ns": artifact.get("maximum_source_availability_ns"),
        "confirmed_ns": confirmed_ns,
        "decision_ns": None,
        "point_in_time_eligible": False,
    }


def publish_create_only(
    writer: Writer,
    artifact: Mapping[str, Any],
    *,
    source_reader: Reader,
    predecessor_reader: Reader,
    expected_plan_sha256: str,
    expected_lease_sources: Mapping[str, str],
    relative_directory: str,
    service_id: str,
    source_relative_root: str,
    output_relative_root: str,
    identity_check: Any,
    revalidate: Any,
    confirmed_clock: Any = time.time_ns,
) -> dict[str, Any]:
    """Publish only complete verified artifacts; receipt time follows durable write."""
    source_file_set = tuple(sorted(source_reader.list_files()))
    _require(
        artifact.get("schema") == SCHEMA and artifact.get("publishable") is True,
        "artifact_not_publishable",
    )
    replayed = compile_from_sealed_v2(
        source_reader,
        predecessor_reader,
        expected_plan_sha256=expected_plan_sha256,
        expected_lease_sources=expected_lease_sources,
    )
    _require(canonical(replayed) == canonical(artifact), "artifact_source_replay_mismatch")
    relative_directory = "" if relative_directory == "" else safe_relative(relative_directory)
    source_relative_root = safe_relative(source_relative_root)
    output_relative_root = safe_relative(output_relative_root)
    _require(
        relative_directory == ""
        and bool(service_id)
        and len(service_id) <= 80
        and all(
            character in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
            for character in service_id
        ),
        "publication_root_binding",
    )
    local_manifest_path = (
        f"{relative_directory}/manifest.json" if relative_directory else "manifest.json"
    )
    local_receipt_path = (
        f"{relative_directory}/publication.json" if relative_directory else "publication.json"
    )
    manifest_path = f"{output_relative_root}/manifest.json"
    artifact_raw = canonical(artifact)
    _require(callable(identity_check) and callable(revalidate), "publication_guard_callbacks")

    def live_identity() -> tuple[str, str]:
        revalidate()
        identity = identity_check()
        _require(isinstance(identity, Mapping), "publication_guard_identity")
        root_id = identity.get("root_id")
        volume_uuid = identity.get("volume_uuid")
        _require(
            isinstance(root_id, str)
            and bool(root_id)
            and isinstance(volume_uuid, str)
            and bool(volume_uuid),
            "publication_guard_identity",
        )
        plan_storage = artifact["scope"]["storage"]
        _require(
            plan_storage.get("root_id") == root_id
            and str(plan_storage.get("volume_uuid", "")).lower() == volume_uuid.lower(),
            "publication_storage_identity",
        )
        return root_id, volume_uuid

    root_id, volume_uuid = live_identity()
    _require(
        artifact["scope"].get("storage", {}).get("relative_root") == source_relative_root,
        "publication_source_root_binding",
    )
    _require(
        not (writer.exists(local_receipt_path) and not writer.exists(local_manifest_path)),
        "publication_receipt_without_artifact",
    )
    if writer.exists(local_manifest_path):
        existing = writer.read_bytes(local_manifest_path)
        _require(existing == artifact_raw, "create_only_artifact_conflict")
        if writer.exists(local_receipt_path):
            receipt_raw = writer.read_bytes(local_receipt_path)
            receipt = _decode(receipt_raw)
            _require(
                isinstance(receipt, dict)
                and canonical(receipt) == receipt_raw
                and set(receipt)
                == {
                    "schema",
                    "schema_version",
                    "manifest_path",
                    "manifest_sha256",
                    "plan_sha256",
                    "service_id",
                    "source_relative_root",
                    "output_relative_root",
                    "event_tail_sha256",
                    "source_code_sha256",
                    "builder_code_sha256",
                    "predecessor_certificate_sha256",
                    "predecessor_gap_intervals",
                    "bootstrap_uncovered_interval",
                    "verified_gap_intervals",
                    "root_id",
                    "volume_uuid",
                    "maximum_source_availability_ns",
                    "confirmed_ns",
                    "decision_ns",
                    "point_in_time_eligible",
                },
                "publication_receipt_shape",
            )
            confirmed_ns = receipt.get("confirmed_ns")
            maximum_availability = artifact.get("maximum_source_availability_ns")
            _require(
                type(confirmed_ns) is int
                and confirmed_ns > 0
                and (
                    maximum_availability is None
                    or (type(maximum_availability) is int and confirmed_ns >= maximum_availability)
                ),
                "publication_confirmation_time",
            )
            expected_receipt = _publication_receipt(
                artifact=artifact,
                manifest_path=manifest_path,
                service_id=service_id,
                source_relative_root=source_relative_root,
                output_relative_root=output_relative_root,
                root_id=root_id,
                volume_uuid=volume_uuid,
                confirmed_ns=confirmed_ns,
            )
            _require(
                receipt == expected_receipt
                and receipt.get("decision_ns") is None
                and receipt.get("point_in_time_eligible") is False,
                "publication_receipt_mismatch",
            )
            _require(
                tuple(sorted(source_reader.list_files())) == source_file_set,
                "source_file_set_changed_during_publication",
            )
            return cast(dict[str, Any], receipt)
        _fsync_existing(writer, local_manifest_path)
    else:
        writer.create(local_manifest_path, artifact_raw)
    # Revalidate both the mount binding and the closed source before receipt
    # publication. An orphaned manifest may be completed only from exact replay.
    root_id, volume_uuid = live_identity()
    replayed = compile_from_sealed_v2(
        source_reader,
        predecessor_reader,
        expected_plan_sha256=expected_plan_sha256,
        expected_lease_sources=expected_lease_sources,
    )
    _require(canonical(replayed) == canonical(artifact), "artifact_source_replay_mismatch")
    _require(
        tuple(sorted(source_reader.list_files())) == source_file_set,
        "source_file_set_changed_during_publication",
    )
    confirmed_ns = confirmed_clock()
    maximum_availability = artifact.get("maximum_source_availability_ns")
    _require(type(confirmed_ns) is int and confirmed_ns > 0, "publication_clock")
    _require(
        maximum_availability is None
        or (type(maximum_availability) is int and confirmed_ns >= maximum_availability),
        "publication_clock_before_source_availability",
    )
    receipt = _publication_receipt(
        artifact=artifact,
        manifest_path=manifest_path,
        service_id=service_id,
        source_relative_root=source_relative_root,
        output_relative_root=output_relative_root,
        root_id=root_id,
        volume_uuid=volume_uuid,
        confirmed_ns=confirmed_ns,
    )
    writer.create(local_receipt_path, canonical(receipt))
    post_root_id, post_volume_uuid = live_identity()
    _require(
        post_root_id == root_id and post_volume_uuid.lower() == volume_uuid.lower(),
        "publication_identity_changed_after_receipt",
    )
    return receipt
