#!/usr/bin/env python3
"""Compile an immutable, offline Senses structural unknown/anomaly queue.

This reader consumes only a versioned structural input manifest and its pinned
summary/detail artifacts. It has no RPC client, scheduler, decoder promotion,
fact-admission, or transaction-body interface. Queue publication is create-only
through the configured descriptor-bound research storage root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memecoin_research_storage import StorageError, writer_guard
from tools.brain_senses_external_writer_v1 import DescriptorWriter
from tools.brain_senses_structural_unknown_queue_schema_v1 import (
    INPUT_SCHEMA,
    QUEUE_SCHEMA,
    RECOGNIZED_KINDS,
    SCHEMA_VERSION,
    SOURCE_FAILURE_CODES,
    QueueInputError,
    canonical,
    digest,
    require_bucket,
    require_digest,
    require_signature_hash,
    safe_source_path,
)

REPO = Path(__file__).resolve().parents[1]
STORAGE_CONFIG = Path.home() / "Developer/memecoin-runtime/brain/config/storage-v1.json"
QUEUE_PREFIX = "ResearchData/brain/senses-structural-unknown-queue-v1"
MIN_FREE_BYTES = 50 * 1024**3
METADATA_MARGIN_BYTES = 2 * 1024**2

C4_RECEIPT_PATH = "docs/evidence/pumpswap-c4-structural-novelty-v1-2026-10-11.json"
C4_RECEIPT_SHA256 = "56f74e7328cf127cf29161572ee22387cfb845e02daeef3fa01d3eb39abf762a"
C4_DETAIL_PATH = "work/brain-senses-c4-structural-novelty-v1-2026-10-11/detail.json.zst"
C4_DETAIL_SHA256 = "e032f3668dab6f8b5638837148b8a3d4494a810c5e53e4fe2786a9cb1089960b"
C4_DETAIL_RAW_SHA256 = "6fc70827e5482887efb9a20414b0298b10107762023376ce6b2bbd9f0c06d424"
C4_BUCKETS = tuple(f"20261010T{hour:02d}0000Z" for hour in range(13, 23))
C4_TAIL2_BUCKET = "20261010T230000Z"


class QueueFailure(RuntimeError):
    """Sanitized source, schema, or persistence failure."""


class CreateOnlyWriter(Protocol):
    def exists(self, relative: str) -> bool: ...

    def read_bytes(self, relative: str, *, max_bytes: int = 64 * 1024 * 1024) -> bytes: ...

    def create(self, relative: str, payload: bytes, *, mode: int = 0o600) -> None: ...


def _exact_keys(value: Any, keys: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise QueueInputError(code)
    return value


def _int(value: Any, code: str, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise QueueInputError(code)
    return value


def _source_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: manifest[key]
        for key in (
            "schedule",
            "captures",
            "blocks",
            "classes",
            "references",
            "candidate_failures",
            "exclusions",
        )
    }


def _validate_source(source: Any) -> dict[str, Any]:
    source = _exact_keys(
        source,
        {
            "receipt_path",
            "receipt_sha256",
            "detail_path",
            "detail_sha256",
            "detail_uncompressed_sha256",
            "detail_uncompressed_bytes",
            "structural_payload_sha256",
        },
        "source_manifest_fields",
    )
    receipt_path = safe_source_path(source["receipt_path"])
    detail_path = safe_source_path(source["detail_path"])
    if not receipt_path.startswith("docs/evidence/") or not receipt_path.endswith(".json"):
        raise QueueInputError("source_receipt_path_scope")
    if not detail_path.startswith("work/brain-senses-") or not detail_path.endswith(
        "/detail.json.zst"
    ):
        raise QueueInputError("source_detail_path_scope")
    for key in (
        "receipt_sha256",
        "detail_sha256",
        "detail_uncompressed_sha256",
        "structural_payload_sha256",
    ):
        require_digest(source[key], f"source_{key}")
    _int(source["detail_uncompressed_bytes"], "source_detail_length", positive=True)
    return cast(dict[str, Any], source)


def _c4_fingerprint_digest(value: Any) -> str:
    """Reproduce the pinned v1 structural detail's compact hash preimage."""
    legacy = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(legacy).hexdigest()


def _verify_input_source_content(
    manifest: dict[str, Any], source: dict[str, Any], receipt_bytes: bytes, detail_bytes: bytes
) -> None:
    if digest(receipt_bytes) != source["receipt_sha256"]:
        raise QueueFailure("source_receipt_hash_mismatch")
    if digest(detail_bytes) != source["detail_uncompressed_sha256"]:
        raise QueueFailure("source_detail_uncompressed_hash_mismatch")
    try:
        receipt = json.loads(receipt_bytes)
        detail = json.loads(detail_bytes)
    except json.JSONDecodeError as exc:
        raise QueueFailure("source_metadata_parse") from exc
    receipt = _exact_keys(
        receipt,
        {"schema", "schema_version", "status", "structural_payload_sha256", "detail_artifact"},
        "source_receipt_contract",
    )
    if (
        receipt["schema"] != "brain-senses-structural-source-receipt-v1"
        or receipt["schema_version"] != 1
        or receipt["status"] != "sealed"
        or receipt["structural_payload_sha256"] != source["structural_payload_sha256"]
    ):
        raise QueueFailure("source_receipt_lineage_mismatch")
    artifact = _exact_keys(
        receipt["detail_artifact"],
        {"path", "compressed_sha256", "uncompressed_sha256", "uncompressed_bytes"},
        "source_receipt_detail_contract",
    )
    if (
        artifact["path"] != source["detail_path"]
        or artifact["compressed_sha256"] != source["detail_sha256"]
        or artifact["uncompressed_sha256"] != source["detail_uncompressed_sha256"]
        or artifact["uncompressed_bytes"] != len(detail_bytes)
    ):
        raise QueueFailure("source_detail_receipt_pin_mismatch")
    detail = _exact_keys(
        detail,
        {
            "schema",
            "schema_version",
            "structural_payload_sha256",
            "payload",
            "raw_response_bodies_included",
        },
        "source_detail_contract",
    )
    payload = _source_payload(manifest)
    if (
        detail["schema"] != "brain-senses-structural-detail-v1"
        or detail["schema_version"] != 1
        or detail["raw_response_bodies_included"] is not False
        or detail["structural_payload_sha256"] != source["structural_payload_sha256"]
        or digest(canonical(detail["payload"])) != source["structural_payload_sha256"]
        or detail["payload"] != payload
    ):
        raise QueueFailure("source_detail_payload_mismatch")


def _validate_schedule(schedule: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not isinstance(schedule, list) or not schedule or len(schedule) > 256:
        raise QueueInputError("schedule_shape")
    validated: list[dict[str, Any]] = []
    by_bucket: dict[str, int] = {}
    previous_end = -1
    for index, row in enumerate(schedule):
        row = _exact_keys(
            row,
            {"bucket", "state", "start_ns", "end_ns", "selected_slots", "selection_sha256"},
            "schedule_row_fields",
        )
        bucket = require_bucket(row["bucket"])
        if bucket in by_bucket:
            raise QueueInputError("duplicate_scheduled_bucket")
        start = _int(row["start_ns"], "schedule_start")
        end = _int(row["end_ns"], "schedule_end", positive=True)
        if start >= end or start < previous_end:
            raise QueueInputError("schedule_time_order")
        previous_end = end
        state = row["state"]
        if state == "sealed":
            slots = row["selected_slots"]
            if (
                not isinstance(slots, list)
                or any(type(slot) is not int or slot < 0 for slot in slots)
                or slots != sorted(set(slots))
            ):
                raise QueueInputError("selected_slot_manifest")
            expected_selection_sha = digest(canonical({"bucket": bucket, "selected_slots": slots}))
            if row["selection_sha256"] != expected_selection_sha:
                raise QueueInputError("selection_hash_mismatch")
        elif state == "missed":
            if row["selected_slots"] is not None or row["selection_sha256"] is not None:
                raise QueueInputError("missed_bucket_has_selection")
            slots = None
        else:
            raise QueueInputError("bucket_state_invalid")
        by_bucket[bucket] = index
        validated.append({**row, "bucket": bucket, "selected_slots": slots})
    return validated, by_bucket


def _fingerprint_status(fingerprint: dict[str, Any]) -> str:
    kind = fingerprint.get("instruction_kind")
    if kind == "unknown_discriminator":
        return "unsupported_instruction"
    if kind in RECOGNIZED_KINDS:
        return "recognized_but_semantics_unresolved"
    return "candidate_parse_failure"


def compile_queue(manifest: Any) -> dict[str, Any]:
    """Validate and compile a manifest without filesystem or network access."""
    manifest = _exact_keys(
        manifest,
        {
            "schema",
            "schema_version",
            "scope_id",
            "process",
            "source",
            "schedule",
            "captures",
            "blocks",
            "classes",
            "references",
            "candidate_failures",
            "exclusions",
        },
        "input_manifest_fields",
    )
    if manifest["schema"] != INPUT_SCHEMA or manifest["schema_version"] != SCHEMA_VERSION:
        raise QueueInputError("input_schema")
    scope_id = manifest["scope_id"]
    if not isinstance(scope_id, str) or not scope_id or len(scope_id) > 200:
        raise QueueInputError("scope_id_invalid")
    process = _exact_keys(
        manifest["process"],
        {"source_commit", "source_tool_sha256", "source_schema_sha256"},
        "process_pin_fields",
    )
    if not isinstance(process["source_commit"], str) or not process["source_commit"]:
        raise QueueInputError("process_commit_invalid")
    for key in ("source_tool_sha256", "source_schema_sha256"):
        require_digest(process[key], f"process_{key}")
    source = _validate_source(manifest["source"])
    schedule, bucket_index = _validate_schedule(manifest["schedule"])
    if not isinstance(manifest["captures"], list) or len(manifest["captures"]) > 1_000_000:
        raise QueueInputError("capture_table_shape")
    captures: dict[str, dict[str, Any]] = {}
    capture_paths: set[str] = set()
    for raw in manifest["captures"]:
        capture = _exact_keys(
            raw,
            {
                "capture_id",
                "bucket",
                "slot",
                "blockhash",
                "full_block_path",
                "full_block_sha256",
                "capture_receipt_path",
                "capture_receipt_sha256",
            },
            "capture_row_fields",
        )
        capture_id = capture["capture_id"]
        if not isinstance(capture_id, str) or not capture_id or len(capture_id) > 200:
            raise QueueInputError("capture_id_invalid")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", capture_id):
            raise QueueInputError("capture_id_invalid")
        if capture_id in captures:
            raise QueueInputError("duplicate_capture_id")
        capture["bucket"] = require_bucket(capture["bucket"])
        capture["slot"] = _int(capture["slot"], "capture_slot")
        if not isinstance(capture["blockhash"], str) or not capture["blockhash"]:
            raise QueueInputError("capture_blockhash_invalid")
        for path_key, hash_key in (
            ("full_block_path", "full_block_sha256"),
            ("capture_receipt_path", "capture_receipt_sha256"),
        ):
            source_path = safe_source_path(capture[path_key])
            if not source_path.startswith("work/brain-senses-") or not source_path.endswith(
                ".json"
            ):
                raise QueueInputError("capture_source_path_scope")
            if source_path in capture_paths:
                raise QueueInputError("duplicate_capture_source_path")
            capture_paths.add(source_path)
            require_digest(capture[hash_key], f"capture_{hash_key}")
        captures[capture_id] = capture
    if not isinstance(manifest["blocks"], list) or not isinstance(manifest["classes"], list):
        raise QueueInputError("block_or_class_list")
    if not isinstance(manifest["references"], list) or not isinstance(
        manifest["candidate_failures"], list
    ):
        raise QueueInputError("reference_or_failure_list")
    exclusions = manifest["exclusions"]
    if not isinstance(exclusions, list):
        raise QueueInputError("exclusions_shape")
    for exclusion in exclusions:
        exclusion = _exact_keys(exclusion, {"kind", "identity", "reason"}, "exclusion_fields")
        if (
            exclusion["kind"] != "bucket"
            or exclusion["identity"] != "20261010T230000Z"
            or exclusion["reason"] != "tail2_explicitly_excluded"
        ):
            raise QueueInputError("exclusion_scope_invalid")

    payload_sha = digest(canonical(_source_payload(manifest)))
    if payload_sha != source["structural_payload_sha256"]:
        raise QueueInputError("structural_payload_hash_mismatch")

    bucket_rows: dict[str, dict[int, dict[str, Any]]] = {row["bucket"]: {} for row in schedule}
    tx_status: dict[tuple[str, int, str], str] = {}
    tx_seen_global: set[str] = set()
    scanned_transactions = 0
    produced_blocks = 0
    verified_blocks = 0
    block_keys: set[tuple[str, int]] = set()
    used_capture_ids: set[str] = set()
    known_source_availability: list[int] = []
    for raw in manifest["blocks"]:
        row = _exact_keys(
            raw,
            {
                "bucket",
                "slot",
                "capture_id",
                "status",
                "produced",
                "blockhash",
                "event_time_ns",
                "receipt_ns",
                "availability_ns",
                "partition_status",
                "transactions",
                "failure_code",
            },
            "block_row_fields",
        )
        bucket = require_bucket(row["bucket"])
        if bucket not in bucket_index or schedule[bucket_index[bucket]]["state"] != "sealed":
            raise QueueInputError("block_outside_sealed_schedule")
        slot = _int(row["slot"], "block_slot")
        block_identity = (bucket, slot)
        if block_identity in block_keys:
            raise QueueInputError("duplicate_selected_block")
        block_keys.add(block_identity)
        capture_id = row["capture_id"]
        joined_capture = captures.get(capture_id) if isinstance(capture_id, str) else None
        if row["status"] == "verified":
            if joined_capture is None or any(
                joined_capture[field] != row[field] for field in ("bucket", "slot", "blockhash")
            ):
                raise QueueInputError("block_capture_join_mismatch")
            used_capture_ids.add(capture_id)
        elif capture_id is not None or joined_capture is not None:
            raise QueueInputError("failed_block_has_full_capture")
        if row["produced"] is not True:
            raise QueueInputError("selected_slot_not_produced")
        produced_blocks += 1
        status = row["status"]
        if status == "verified":
            if row["partition_status"] != "verified" or row["failure_code"] is not None:
                raise QueueInputError("verified_block_partition_state")
            if not isinstance(row["blockhash"], str) or not row["blockhash"]:
                raise QueueInputError("block_identity_missing")
            event_ns = _int(row["event_time_ns"], "block_event_time")
            receipt_ns = _int(row["receipt_ns"], "block_receipt_time")
            availability_ns = _int(row["availability_ns"], "block_availability")
            if event_ns > receipt_ns or receipt_ns > availability_ns:
                raise QueueInputError("block_time_order")
            known_source_availability.append(availability_ns)
            partitions = _exact_keys(
                row["transactions"], {"success", "failed", "unknown"}, "transaction_partitions"
            )
            seen: set[str] = set()
            for name, values in partitions.items():
                if not isinstance(values, list):
                    raise QueueInputError("transaction_partition_list")
                for signature in values:
                    signature = require_signature_hash(signature)
                    if signature in seen or signature in tx_seen_global:
                        raise QueueInputError("transaction_partition_overlap_or_duplicate")
                    seen.add(signature)
                    tx_seen_global.add(signature)
                    tx_status[(bucket, slot, signature)] = name
            scanned_transactions += len(seen)
            verified_blocks += 1
        elif status in {"failed", "unresolved"}:
            code = row["failure_code"]
            if (
                row["partition_status"] != "unobserved"
                or code not in SOURCE_FAILURE_CODES
                or row["transactions"] != {"success": [], "failed": [], "unknown": []}
            ):
                raise QueueInputError("failed_block_classification")
            if row["blockhash"] is not None or row["event_time_ns"] is not None:
                raise QueueInputError("failed_block_claims_identity")
            if row["receipt_ns"] is not None or row["availability_ns"] is not None:
                receipt_ns = row["receipt_ns"]
                availability_ns = row["availability_ns"]
                if type(receipt_ns) is not int or type(availability_ns) is not int:
                    raise QueueInputError("failed_block_time_shape")
                if receipt_ns < 0 or availability_ns < receipt_ns:
                    raise QueueInputError("failed_block_time_order")
                known_source_availability.append(availability_ns)
        else:
            raise QueueInputError("block_status_invalid")
        bucket_rows[bucket][slot] = row

    if set(captures) != used_capture_ids:
        raise QueueInputError("unjoined_capture_source")

    selected_count = 0
    missed_count = 0
    for row in schedule:
        bucket = row["bucket"]
        if row["state"] == "missed":
            missed_count += 1
            if bucket_rows[bucket]:
                raise QueueInputError("missed_bucket_has_block_rows")
            continue
        selected_slots = row["selected_slots"]
        if set(bucket_rows[bucket]) != set(selected_slots):
            raise QueueInputError("selected_block_membership_mismatch")
        selected_count += len(selected_slots)

    class_rows: dict[str, dict[str, Any]] = {}
    class_refs: dict[str, Counter[str]] = defaultdict(Counter)
    class_times: dict[str, list[tuple[int, int, int, str, int, str, str, dict[str, Any]]]] = (
        defaultdict(list)
    )
    references_seen: set[str] = set()
    reference_count = 0
    for raw in manifest["references"]:
        row = _exact_keys(
            raw,
            {
                "reference_id",
                "capture_id",
                "bucket",
                "slot",
                "signature_sha256",
                "instruction_path",
                "class_sha256",
            },
            "reference_row_fields",
        )
        bucket = require_bucket(row["bucket"])
        slot = _int(row["slot"], "reference_slot")
        capture_id = row["capture_id"]
        joined_capture = captures.get(capture_id) if isinstance(capture_id, str) else None
        if joined_capture is None or (joined_capture["bucket"], joined_capture["slot"]) != (
            bucket,
            slot,
        ):
            raise QueueInputError("reference_capture_join_mismatch")
        block = bucket_rows.get(bucket, {}).get(slot)
        if block is None or block["status"] != "verified":
            raise QueueInputError("reference_outside_verified_block")
        if block["capture_id"] != capture_id:
            raise QueueInputError("reference_block_capture_mismatch")
        signature = require_signature_hash(row["signature_sha256"])
        instruction_path = _exact_keys(
            row["instruction_path"], {"outer_index", "inner_index"}, "instruction_path_fields"
        )
        _int(instruction_path["outer_index"], "instruction_outer_index")
        inner_index = instruction_path["inner_index"]
        if inner_index is not None:
            _int(inner_index, "instruction_inner_index")
        expected_reference_id = digest(
            canonical(
                {
                    "capture_id": capture_id,
                    "signature_sha256": signature,
                    "instruction_path": instruction_path,
                }
            )
        )
        reference_id = row["reference_id"]
        if reference_id != expected_reference_id or reference_id in references_seen:
            raise QueueInputError("reference_identity_mismatch_or_duplicate")
        references_seen.add(reference_id)
        tx_class = tx_status.get((bucket, slot, signature))
        if tx_class is None:
            raise QueueInputError("reference_transaction_not_partitioned")
        class_sha = require_digest(row["class_sha256"], "reference_class_hash")
        class_refs[class_sha][tx_class] += 1
        class_times[class_sha].append(
            (
                int(block["event_time_ns"]),
                int(block["receipt_ns"]),
                int(block["availability_ns"]),
                bucket,
                slot,
                reference_id,
                capture_id,
                instruction_path,
            )
        )
        reference_count += 1

    if len(manifest["classes"]) > 1_000_000:
        raise QueueInputError("class_limit")
    for raw in manifest["classes"]:
        row = _exact_keys(raw, {"fingerprint", "class_sha256", "count"}, "class_row_fields")
        fingerprint = row["fingerprint"]
        if not isinstance(fingerprint, dict):
            raise QueueInputError("candidate_fingerprint_shape")
        class_sha = require_digest(row["class_sha256"], "class_hash_shape")
        if digest(canonical(fingerprint)) != class_sha:
            raise QueueInputError("class_fingerprint_hash_mismatch")
        if class_sha in class_rows:
            raise QueueInputError("duplicate_class_fingerprint")
        count = _int(row["count"], "class_count", positive=True)
        if sum(class_refs[class_sha].values()) != count:
            raise QueueInputError("class_reference_count_mismatch")
        transaction_status = fingerprint.get("transaction_status")
        observed_statuses = {name for name, amount in class_refs[class_sha].items() if amount}
        if transaction_status not in {"success", "failed", "unknown"} or observed_statuses != {
            transaction_status
        }:
            raise QueueInputError("class_transaction_status_mismatch")
        class_rows[class_sha] = {**row, "status": _fingerprint_status(fingerprint)}
    if set(class_rows) != set(class_refs):
        raise QueueInputError("class_reference_membership_mismatch")

    status_transaction_matrix = {
        status: {
            tx_status_name: sum(
                amount
                for class_sha, counts in class_refs.items()
                if class_rows[class_sha]["status"] == status
                for name, amount in counts.items()
                if name == tx_status_name
            )
            for tx_status_name in ("success", "failed", "unknown")
        }
        for status in (
            "unsupported_instruction",
            "recognized_but_semantics_unresolved",
            "candidate_parse_failure",
        )
    }

    process_pin = {
        "scope_id": scope_id,
        "source_commit": process["source_commit"],
        "source_tool_sha256": process["source_tool_sha256"],
        "source_schema_sha256": process["source_schema_sha256"],
        "source_receipt_sha256": source["receipt_sha256"],
        "source_detail_sha256": source["detail_sha256"],
        "structural_payload_sha256": source["structural_payload_sha256"],
    }
    process_pin_sha = digest(canonical(process_pin))
    queue_entries: dict[tuple[str, str], dict[str, Any]] = {}
    rejected_candidates = 0
    unresolved_candidates = 0
    bucket_order = {row["bucket"]: idx for idx, row in enumerate(schedule)}
    for class_sha, row in class_rows.items():
        fingerprint = row["fingerprint"]
        status = row["status"]
        if status == "candidate_parse_failure":
            rejected_candidates += int(row["count"])
        else:
            unresolved_candidates += int(row["count"])
        occurrences = class_times[class_sha]
        first = min(
            occurrences, key=lambda item: (bucket_order[item[3]], item[0], item[4], item[5])
        )
        representatives = sorted(
            occurrences,
            key=lambda item: (
                bucket_order[item[3]],
                item[4],
                item[7]["outer_index"],
                -1 if item[7]["inner_index"] is None else item[7]["inner_index"],
                item[5],
            ),
        )[:3]
        entry = {
            "process_pin_sha256": process_pin_sha,
            "fingerprint_sha256": class_sha,
            "status": status,
            "instruction_kind": fingerprint.get("instruction_kind"),
            "transaction_status": fingerprint.get("transaction_status"),
            "count": row["count"],
            "success_references": class_refs[class_sha]["success"],
            "failed_references": class_refs[class_sha]["failed"],
            "unknown_references": class_refs[class_sha]["unknown"],
            "first_bucket": first[3],
            "event_time_ns": first[0],
            "source_receipt_ns": first[1],
            "source_available_ns": first[2],
            "representative_references": [
                {
                    "reference_id": item[5],
                    "capture_id": item[6],
                    "bucket": item[3],
                    "slot": item[4],
                    "instruction_path": item[7],
                }
                for item in representatives
            ],
            "decision_ns": None,
        }
        queue_entries[(process_pin_sha, class_sha)] = entry

    candidate_failure_ids: set[str] = set()
    for raw in manifest["candidate_failures"]:
        row = _exact_keys(
            raw,
            {"failure_id", "bucket", "failure_code", "count", "source_row_sha256"},
            "candidate_failure_fields",
        )
        if not isinstance(row["failure_id"], str) or not row["failure_id"]:
            raise QueueInputError("candidate_failure_id")
        if row["failure_id"] in candidate_failure_ids:
            raise QueueInputError("duplicate_candidate_failure_id")
        candidate_failure_ids.add(row["failure_id"])
        bucket = require_bucket(row["bucket"])
        if bucket not in bucket_index or schedule[bucket_index[bucket]]["state"] != "sealed":
            raise QueueInputError("candidate_failure_outside_sealed_scope")
        if row["failure_code"] != "candidate_parse_failed":
            raise QueueInputError("candidate_failure_code")
        count = _int(row["count"], "candidate_failure_count", positive=True)
        source_row_sha = require_digest(row["source_row_sha256"], "candidate_failure_source_hash")
        failure_key = digest(
            canonical({"failure_id": row["failure_id"], "source_row_sha256": source_row_sha})
        )
        queue_entries[(process_pin_sha, failure_key)] = {
            "process_pin_sha256": process_pin_sha,
            "fingerprint_sha256": failure_key,
            "status": "candidate_parse_failure",
            "instruction_kind": None,
            "transaction_status": None,
            "count": count,
            "success_references": 0,
            "failed_references": 0,
            "unknown_references": 0,
            "first_bucket": bucket,
            "event_time_ns": None,
            "source_receipt_ns": None,
            "source_available_ns": None,
            "decision_ns": None,
            "failure_code": row["failure_code"],
        }
        rejected_candidates += count
        unresolved_candidates += count

    sorted_entries = [queue_entries[key] for key in sorted(queue_entries)]
    all_sealed = missed_count == 0 and verified_blocks == selected_count
    new_by_bucket: Counter[str] = Counter()
    for occurrences in class_times.values():
        first_bucket = min(occurrences, key=lambda item: bucket_order[item[3]])[3]
        new_by_bucket[first_bucket] += 1
    final_bucket = schedule[-1]["bucket"]
    no_new_class_value: bool | None = (
        new_by_bucket[final_bucket] == 0 if all_sealed and not rejected_candidates else None
    )
    queue = {
        "schema": QUEUE_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "scope_id": scope_id,
        "process_pin": process_pin,
        "process_pin_sha256": process_pin_sha,
        "input_structural_payload_sha256": payload_sha,
        "capture_sources": [captures[capture_id] for capture_id in sorted(captures)],
        "max_source_available_ns": max(known_source_availability, default=None),
        "decision_ns": None,
        "entries": sorted_entries,
        "denominators": {
            "scheduled_buckets": len(schedule),
            "sealed_buckets": sum(row["state"] == "sealed" for row in schedule),
            "missed_buckets": missed_count,
            "selected_blocks": selected_count,
            "produced_blocks": produced_blocks,
            "verified_blocks": verified_blocks,
            "scanned_transactions": scanned_transactions,
            "transaction_references": reference_count,
            "class_count": len(class_rows),
            "instruction_references_by_status_and_transaction_status": status_transaction_matrix,
            "instruction_references_by_transaction_status": {
                status: sum(
                    amount
                    for counts in class_refs.values()
                    for tx_status_name, amount in counts.items()
                    if tx_status_name == status
                )
                for status in ("success", "failed", "unknown")
            },
            "unattributed_candidate_failures": rejected_candidates
            - sum(
                int(row["count"])
                for row in class_rows.values()
                if row["status"] == "candidate_parse_failure"
            ),
            "rejected_candidates": rejected_candidates,
            "unresolved_candidates": unresolved_candidates,
        },
        "scope_findings": {
            "named_scope": scope_id,
            "complete": (
                True
                if all_sealed and not rejected_candidates
                else None
                if rejected_candidates or missed_count or verified_blocks != selected_count
                else False
            ),
            "completeness_status": (
                "complete"
                if all_sealed and not rejected_candidates
                else "unknown"
                if rejected_candidates or missed_count or verified_blocks != selected_count
                else "incomplete"
            ),
            "new_classes_by_bucket": dict(new_by_bucket),
            "no_new_class_in_final_bucket": no_new_class_value,
            "global_or_economic_zero_claim": False,
        },
        "exclusions": exclusions,
        "source": source,
    }
    return queue


def verify_queue(manifest: Any, queue_bytes: bytes, *, expected_manifest_sha256: str) -> None:
    require_digest(expected_manifest_sha256, "expected_manifest_hash")
    expected = compile_queue(manifest)
    if digest(canonical(manifest)) != expected_manifest_sha256:
        raise QueueInputError("input_manifest_hash_mismatch")
    if queue_bytes != canonical(expected):
        raise QueueInputError("queue_recomputation_mismatch")


def _fsync_existing(writer: CreateOnlyWriter, relative: str) -> None:
    """Fsync an existing regular file and parent through the held writer root fd."""
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or str(path) != relative:
        raise QueueFailure("queue_output_path_invalid")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise QueueFailure("queue_output_path_invalid")
    root_fd = getattr(writer, "root_fd", None)
    device = getattr(writer, "device", None)
    prewrite = getattr(writer, "_prewrite", None)
    if type(root_fd) is not int or type(device) is not int or not callable(prewrite):
        raise QueueFailure("descriptor_writer_capability_missing")
    current_fd = os.dup(root_fd)
    try:
        for component in path.parts[:-1]:
            child_fd = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current_fd
            )
            directory_stat = os.fstat(child_fd)
            if (
                not stat.S_ISDIR(directory_stat.st_mode)
                or directory_stat.st_uid != os.getuid()
                or stat.S_IMODE(directory_stat.st_mode) != 0o700
                or directory_stat.st_dev != device
            ):
                os.close(child_fd)
                raise QueueFailure("unsafe_queue_parent_directory")
            os.close(current_fd)
            current_fd = child_fd
        prewrite("fsync")
        file_fd = os.open(
            path.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current_fd
        )
        try:
            file_stat = os.fstat(file_fd)
            if (
                not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_uid != os.getuid()
                or stat.S_IMODE(file_stat.st_mode) != 0o600
                or file_stat.st_nlink != 1
                or file_stat.st_dev != device
            ):
                raise QueueFailure("unsafe_queue_file")
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        os.fsync(current_fd)
    except OSError as exc:
        raise QueueFailure("queue_durability_sync_failed") from exc
    finally:
        os.close(current_fd)


def publish_create_only(writer: CreateOnlyWriter, relative: str, payload: bytes) -> str:
    """Create an immutable descriptor-bound queue; exact replay is idempotent."""
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise QueueFailure("queue_output_path_invalid")
    expected_sha = digest(payload)
    if writer.exists(relative):
        existing = writer.read_bytes(relative, max_bytes=len(payload) + 1)
        if existing != payload:
            raise QueueFailure("immutable_queue_collision")
        _fsync_existing(writer, relative)
        return expected_sha
    try:
        writer.create(relative, payload)
    except StorageError as exc:
        if "create_only_collision" not in str(exc):
            raise QueueFailure("queue_create_failed") from exc
        existing = writer.read_bytes(relative, max_bytes=len(payload) + 1)
        if existing != payload:
            raise QueueFailure("immutable_queue_collision") from exc
    _fsync_existing(writer, relative)
    return expected_sha


def publish_queue_with_receipt(
    writer: CreateOnlyWriter,
    manifest: Any,
    queue: dict[str, Any],
    *,
    manifest_sha256: str,
    root_id: str,
    volume_uuid: str,
    prefix: str = QUEUE_PREFIX,
    now_ns: Any | None = None,
) -> dict[str, Any]:
    """Publish deterministic queue bytes, then record their local durable confirmation."""
    require_digest(manifest_sha256, "publication_manifest_hash")
    if now_ns is None:
        now_ns = time_now_ns
    verify_queue(
        manifest,
        canonical(queue),
        expected_manifest_sha256=manifest_sha256,
    )
    if not isinstance(root_id, str) or not re.fullmatch(r"[0-9a-f]{32}", root_id):
        raise QueueFailure("publication_root_id_invalid")
    if not isinstance(volume_uuid, str):
        raise QueueFailure("publication_volume_uuid_invalid")
    try:
        if str(uuid.UUID(volume_uuid)) != volume_uuid:
            raise QueueFailure("publication_volume_uuid_invalid")
    except ValueError as exc:
        raise QueueFailure("publication_volume_uuid_invalid") from exc
    safe_source_path(prefix)
    queue_bytes = canonical(queue)
    queue_sha = digest(queue_bytes)
    queue_path = f"{prefix}/queue-{queue_sha}.json"
    receipt_path = f"{prefix}/queue-{queue_sha}.publication.json"
    max_available = queue.get("max_source_available_ns")
    if max_available is not None:
        _int(max_available, "publication_source_availability")

    def load_receipt() -> dict[str, Any]:
        receipt_bytes = writer.read_bytes(receipt_path, max_bytes=1_000_000)
        try:
            stored = json.loads(receipt_bytes)
        except json.JSONDecodeError as exc:
            raise QueueFailure("publication_receipt_parse") from exc
        stored = _exact_keys(
            stored,
            {
                "schema",
                "schema_version",
                "publication_receipt_path",
                "queue_path",
                "queue_sha256",
                "manifest_sha256",
                "root_id",
                "volume_uuid",
                "max_source_available_ns",
                "confirmation_ns",
                "confirmation_kind",
                "decision_ns",
            },
            "publication_receipt_fields",
        )
        expected_pins = {
            "schema": "brain-senses-structural-unknown-publication-v1",
            "schema_version": 1,
            "publication_receipt_path": receipt_path,
            "queue_path": queue_path,
            "queue_sha256": queue_sha,
            "manifest_sha256": manifest_sha256,
            "root_id": root_id,
            "volume_uuid": volume_uuid,
            "max_source_available_ns": max_available,
            "decision_ns": None,
        }
        if any(stored[key] != value for key, value in expected_pins.items()):
            raise QueueFailure("publication_receipt_pin_mismatch")
        _int(stored["confirmation_ns"], "publication_confirmation_time", positive=True)
        if max_available is not None and stored["confirmation_ns"] < max_available:
            raise QueueFailure("publication_confirmation_precedes_source")
        if stored["confirmation_kind"] not in {
            "initial_durable_publish",
            "recovered_queue_without_receipt",
        }:
            raise QueueFailure("publication_confirmation_kind")
        if receipt_bytes != canonical(stored):
            raise QueueFailure("publication_receipt_noncanonical")
        return stored

    if writer.exists(receipt_path):
        stored_queue = writer.read_bytes(queue_path, max_bytes=len(queue_bytes) + 1)
        if stored_queue != queue_bytes:
            raise QueueFailure("immutable_queue_collision")
        _fsync_existing(writer, queue_path)
        stored_receipt = load_receipt()
        _fsync_existing(writer, receipt_path)
        return stored_receipt

    was_present = writer.exists(queue_path)
    if was_present:
        stored_queue = writer.read_bytes(queue_path, max_bytes=len(queue_bytes) + 1)
        if stored_queue != queue_bytes:
            raise QueueFailure("immutable_queue_collision")
        _fsync_existing(writer, queue_path)
    else:
        publish_create_only(writer, queue_path, queue_bytes)
    confirmation_ns = now_ns()
    if type(confirmation_ns) is not int or confirmation_ns <= 0:
        raise QueueFailure("publication_confirmation_clock_invalid")
    if max_available is not None and confirmation_ns < max_available:
        raise QueueFailure("publication_confirmation_precedes_source")
    receipt = {
        "schema": "brain-senses-structural-unknown-publication-v1",
        "schema_version": 1,
        "publication_receipt_path": receipt_path,
        "queue_path": queue_path,
        "queue_sha256": queue_sha,
        "manifest_sha256": manifest_sha256,
        "root_id": root_id,
        "volume_uuid": volume_uuid,
        "max_source_available_ns": max_available,
        "confirmation_ns": confirmation_ns,
        "confirmation_kind": (
            "recovered_queue_without_receipt" if was_present else "initial_durable_publish"
        ),
        "decision_ns": None,
    }
    receipt_bytes = canonical(receipt)
    try:
        publish_create_only(writer, receipt_path, receipt_bytes)
        return receipt
    except QueueFailure:
        if not writer.exists(receipt_path):
            raise
        stored = load_receipt()
        _fsync_existing(writer, receipt_path)
        return stored


def _read_repo_file(relative: str, *, max_bytes: int) -> bytes:
    relative = safe_source_path(relative)
    fd = os.open(REPO, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    finally:
        os.close(fd)
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise QueueFailure("source_file_identity")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(file_fd, min(1_048_576, remaining))
            if not chunk:
                raise QueueFailure("source_file_short_read")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(file_fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise QueueFailure("source_file_changed")
        return b"".join(chunks)
    finally:
        os.close(file_fd)


def inspect_c4_readiness() -> dict[str, Any]:
    """Validate only the named receipt/detail pair; never opens capture bodies."""
    try:
        receipt_bytes = _read_repo_file(C4_RECEIPT_PATH, max_bytes=4_000_000)
        if digest(receipt_bytes) != C4_RECEIPT_SHA256:
            raise QueueFailure("c4_receipt_hash_mismatch")
        receipt = json.loads(receipt_bytes)
        detail_pin = receipt.get("detail_artifact", {})
        if (
            receipt.get("schema_version") != "pumpswap-c4-structural-novelty-v1"
            or receipt.get("status") != "replay_complete_structural_only"
            or receipt.get("offline_replay") is not True
            or receipt.get("frozen_manifest", {}).get("body_read_during_freeze") is not False
            or tuple(receipt.get("frozen_manifest", {}).get("included_buckets", ())) != C4_BUCKETS
            or receipt.get("frozen_manifest", {}).get("excluded_buckets")
            != [
                {
                    "bucket": C4_TAIL2_BUCKET,
                    "reason": "frozen_tail2_control_excluded; response body not opened",
                }
            ]
            or detail_pin.get("path") != C4_DETAIL_PATH
            or detail_pin.get("compressed_sha256") != C4_DETAIL_SHA256
            or detail_pin.get("uncompressed_sha256") != C4_DETAIL_RAW_SHA256
        ):
            raise QueueFailure("c4_receipt_binding_mismatch")
        compressed = _read_repo_file(C4_DETAIL_PATH, max_bytes=10_000_000)
        if (
            len(compressed) != detail_pin.get("compressed_bytes")
            or digest(compressed) != C4_DETAIL_SHA256
        ):
            raise QueueFailure("c4_detail_compressed_binding_mismatch")
        zstd = shutil.which("zstd")
        if not zstd:
            return {
                "schema": "brain-senses-structural-unknown-readiness-v1",
                "status": "synthetic_only_readiness",
                "reason": "zstd_unavailable",
                "provider_io": False,
            }
        result = subprocess.run(
            [zstd, "-dc"], input=compressed, capture_output=True, check=True, timeout=60
        )
        raw_detail = result.stdout
        if (
            len(raw_detail) != detail_pin.get("uncompressed_bytes")
            or digest(raw_detail) != C4_DETAIL_RAW_SHA256
        ):
            raise QueueFailure("c4_detail_uncompressed_binding_mismatch")
        detail = json.loads(raw_detail)
        detail_frames = detail.get("frames")
        receipt_frames = receipt.get("frames")
        frames_match = (
            isinstance(detail_frames, list)
            and isinstance(receipt_frames, list)
            and len(detail_frames) == len(receipt_frames)
            and all(
                isinstance(left, dict)
                and isinstance(right, dict)
                and {key: left.get(key) for key in right} == right
                for left, right in zip(detail_frames, receipt_frames, strict=True)
            )
        )
        if (
            detail.get("schema_version") != receipt.get("schema_version")
            or detail.get("status") != receipt.get("status")
            or detail.get("aggregate") != receipt.get("aggregate")
            or not frames_match
            or detail.get("frozen_manifest") != receipt.get("frozen_manifest")
            or detail.get("source_pins") != receipt.get("source_pins")
        ):
            raise QueueFailure("c4_detail_receipt_reconciliation")
        frames = {row["bucket"]: row for row in detail["frames"]}
        bindings = detail["block_bindings"]
        if len(frames) != 10 or len(bindings) != 160:
            raise QueueFailure("c4_detail_denominator_mismatch")
        for bucket in C4_BUCKETS:
            selected = set(frames[bucket]["slot_structural_reconciliation"])
            bound = {str(row["slot"]) for row in bindings if row["bucket"] == bucket}
            if selected != bound:
                raise QueueFailure("c4_selected_block_membership_mismatch")
        class_hashes: set[str] = set()
        class_ref_total = 0
        for row in detail["classes"]:
            if _c4_fingerprint_digest(row["fingerprint"]) != row["class_sha256"]:
                raise QueueFailure("c4_class_fingerprint_hash_mismatch")
            if (
                row["class_sha256"] in class_hashes
                or type(row["count"]) is not int
                or row["count"] < 1
            ):
                raise QueueFailure("c4_class_denominator_mismatch")
            class_hashes.add(row["class_sha256"])
            class_ref_total += row["count"]
        if (
            len(class_hashes) != receipt["aggregate"]["unique_structural_classes"]
            or class_ref_total != receipt["aggregate"]["pumpswap_instruction_references"]
        ):
            raise QueueFailure("c4_class_count_reconciliation")
        replay_ref = receipt.get("c4_replay_receipt")
        if not isinstance(replay_ref, dict) or set(replay_ref) != {"path", "sha256"}:
            raise QueueFailure("c4_replay_receipt_reference")
        replay_path = safe_source_path(replay_ref["path"])
        if not replay_path.startswith("docs/evidence/") or not replay_path.endswith(".json"):
            raise QueueFailure("c4_replay_receipt_path")
        replay_bytes = _read_repo_file(replay_path, max_bytes=4_000_000)
        if digest(replay_bytes) != replay_ref["sha256"]:
            raise QueueFailure("c4_replay_receipt_hash_mismatch")
        missing = [
            "per_transaction_success_failed_unknown_partition",
            "per_reference_transaction_membership",
            "post_fsync_durable_availability_ns",
        ]
        return {
            "schema": "brain-senses-structural-unknown-readiness-v1",
            "status": "synthetic_only_readiness",
            "source_receipt_sha256": C4_RECEIPT_SHA256,
            "source_detail_sha256": C4_DETAIL_SHA256,
            "included_buckets": list(C4_BUCKETS),
            "excluded_buckets": [C4_TAIL2_BUCKET],
            "detail_blocks": len(bindings),
            "detail_classes": len(detail["classes"]),
            "detail_instruction_references": detail["aggregate"]["pumpswap_instruction_references"],
            "required_manifest_fields_missing": missing,
            "raw_response_bodies_opened": False,
            "queue_published": False,
            "provider_io": False,
        }
    except QueueFailure:
        raise
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise QueueFailure("c4_source_integrity_stop") from exc


def _read_manifest(path: Path, expected_sha256: str) -> dict[str, Any]:
    require_digest(expected_sha256, "input_manifest_expected_hash")
    if not path.is_absolute():
        raise QueueFailure("input_manifest_path_must_be_absolute")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size > 64 * 1024 * 1024
        ):
            raise QueueFailure("input_manifest_file_identity")
        raw = b"".join(iter(lambda: os.read(fd, 1_048_576), b""))
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise QueueFailure("input_manifest_changed")
    finally:
        os.close(fd)
    if digest(raw) != expected_sha256:
        raise QueueFailure("input_manifest_hash_mismatch")
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise QueueFailure("input_manifest_parse") from exc
    if raw != canonical(manifest):
        raise QueueFailure("input_manifest_not_canonical")
    source = _validate_source(manifest.get("source"))
    for source_path_key, hash_key, limit in (
        ("receipt_path", "receipt_sha256", 4_000_000),
        ("detail_path", "detail_sha256", 64 * 1024 * 1024),
    ):
        data = _read_repo_file(source[source_path_key], max_bytes=limit)
        if digest(data) != source[hash_key]:
            raise QueueFailure("source_attestation_hash_mismatch")
        if source_path_key == "receipt_path":
            receipt_bytes = data
        else:
            compressed_bytes = data
    zstd = shutil.which("zstd")
    if not zstd:
        raise QueueFailure("source_detail_decompressor_unavailable")
    try:
        decompressed = subprocess.run(
            [zstd, "-dc"], input=compressed_bytes, capture_output=True, check=True, timeout=60
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise QueueFailure("source_detail_decompression_failed") from exc
    if (
        len(decompressed) != source["detail_uncompressed_bytes"]
        or digest(decompressed) != source["detail_uncompressed_sha256"]
    ):
        raise QueueFailure("source_detail_uncompressed_binding_mismatch")
    _verify_input_source_content(manifest, source, receipt_bytes, decompressed)
    payload = compile_queue(manifest)
    return {"manifest": manifest, "manifest_sha256": expected_sha256, "queue": payload}


def time_now_ns() -> int:
    import time

    return time.time_ns()


def publish_to_configured_storage(
    manifest: Any,
    queue: dict[str, Any],
    *,
    manifest_sha256: str,
    config_path: Path = STORAGE_CONFIG,
) -> dict[str, Any]:
    encoded = canonical(queue)
    required_free = MIN_FREE_BYTES + len(encoded) + 1_000_000 + METADATA_MARGIN_BYTES
    with writer_guard(config_path, min_free_bytes=required_free) as guard:

        def revalidate_write() -> None:
            guard.revalidate(min_free_bytes=required_free)

        with DescriptorWriter(
            guard.mount_fd,
            before_write=revalidate_write,
        ) as writer:
            root_prefix = f"{guard.config.data_root}/{QUEUE_PREFIX}"
            return publish_queue_with_receipt(
                writer,
                manifest,
                queue,
                manifest_sha256=manifest_sha256,
                root_id=guard.config.root_id,
                volume_uuid=guard.config.volume_uuid,
                prefix=root_prefix,
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser(
        "inspect-c4", help="validate the pinned summary/detail without reading captures"
    )
    inspect.add_argument("--json", action="store_true")
    compile_parser = sub.add_parser("compile", help="compile a pinned structural input manifest")
    compile_parser.add_argument("--input-manifest", type=Path, required=True)
    compile_parser.add_argument("--expected-sha256", required=True)
    compile_parser.add_argument("--storage-config", type=Path, default=STORAGE_CONFIG)
    args = parser.parse_args()
    try:
        if args.command == "inspect-c4":
            result = inspect_c4_readiness()
        else:
            loaded = _read_manifest(args.input_manifest, args.expected_sha256)
            receipt = publish_to_configured_storage(
                loaded["manifest"],
                loaded["queue"],
                manifest_sha256=loaded["manifest_sha256"],
                config_path=args.storage_config,
            )
            result = {
                "status": "created_or_verified",
                "publication_receipt_path": receipt["publication_receipt_path"],
                "publication_receipt": receipt,
                "provider_io": False,
            }
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"status": "stopped", "error_class": type(exc).__name__, "provider_io": False}
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
