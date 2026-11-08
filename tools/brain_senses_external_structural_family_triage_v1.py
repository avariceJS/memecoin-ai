#!/usr/bin/env python3
"""Offline raw-byte structural-family triage for a frozen external-v2 sample.

The tool reads only the three sealed frames' 48 selected full-block responses
through the existing external-v2 descriptor verifier. It records structural
identity and documented IDL shape matches only; it does not decode effects or
infer execution, settlement, economics, or market lifecycle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from memecoin_pumpswap.normalize import (  # noqa: E402
    NormalizationError,
    normalize_compiled_transaction,
)
from memecoin_research_storage import writer_guard  # noqa: E402
from tools import brain_senses_external_archival_projection_v1 as archive_projection  # noqa: E402
from tools import brain_senses_external_launch_v2 as launch  # noqa: E402
from tools import brain_senses_external_orchestrator_v2 as engine  # noqa: E402
from tools import brain_senses_external_partial_structural_queue_v2 as partial_queue  # noqa: E402
from tools import brain_senses_external_schema_v2 as lease_schema  # noqa: E402
from tools import (  # noqa: E402
    brain_senses_external_structural_input_schema_v1 as structural_schema,  # noqa: E402
)
from tools import brain_senses_external_structural_input_v1 as structural_input  # noqa: E402
from tools import brain_senses_external_structural_publish_v1 as structural_publish  # noqa: E402
from tools import brain_senses_external_verify_v1 as predecessor_verify  # noqa: E402
from tools import brain_senses_external_verify_v2 as source_verify  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402

SERVICE_ID = "external-v2-20261012T0600Z"
EXPECTED_PLAN_SHA256 = "2bb19afee5475173e7e5a73a59d1cd33a27c6af1ef8e309fc39bfc025fa5a688"
STORAGE_CONFIG = Path.home() / "Developer/memecoin-runtime/brain/config/storage-v1.json"
ACTIVATION = (
    Path.home()
    / "Developer/memecoin-runtime/brain/config/external-v2-20261012T0600Z-final/activation.json"
)
FAILURE_RECEIPT = partial_queue.FAILURE_RECEIPT
FINAL_STATUS_RECEIPT = partial_queue.FINAL_STATUS_RECEIPT
QUEUE_RELATIVE = "derived/partial-structural-queue-v2/external-v2-20261012T0600Z/partial-queue.json"
QUEUE_RECEIPT_RELATIVE = (
    "derived/partial-structural-queue-v2/external-v2-20261012T0600Z/publication-receipt.json"
)
ARCHIVE_RELATIVE = archive_projection.OUTPUT_RELATIVE
ARCHIVE_RECEIPT_RELATIVE = archive_projection.OUTPUT_RECEIPT_RELATIVE
OUTPUT_RELATIVE = (
    "derived/external-structural-family-triage-v1/"
    "external-v2-20261012T0600Z/structural-family-triage.json"
)
OUTPUT_RECEIPT_RELATIVE = (
    "derived/external-structural-family-triage-v1/"
    "external-v2-20261012T0600Z/publication-receipt.json"
)
OUTPUT_MAX_BYTES = 32_000_000
OUTPUT_PREFIX_BYTES = 8
REPRESENTATIVES_PER_FAMILY = 3
NETWORK = "solana-mainnet-beta"
PROGRAM_ID = structural_schema.PUMP_PROGRAM_ID
FAMILY_SCHEMA = "pump-amm-raw-structural-family-v1"
TRIAGE_SCHEMA = "pump-amm-raw-structural-family-triage-v1"
IDL_CAPTURE_DIR = Path("work/brain-senses-pumpswap-idl-history-2026-10-11")
IDL_MANIFEST_RELATIVE = f"{IDL_CAPTURE_DIR.as_posix()}/capture_manifest.json"
IDL_MANIFEST_SHA256 = "57a671e4a2b7bfaf84e30dc1804ce05ff4babb1344481f1dc78a1fa4a7eadf03"
IDL_HISTORY_RECEIPT = (
    REPO / "docs/evidence/pumpswap-c4-idl-history-qualification-v3-2026-10-11.json"
)
IDL_HISTORY_RECEIPT_SHA256 = "3003c80f0ad6291b45a2a8c6c477fe729d8339446eb591a67c3c5c47029bdf46"
IDL_HISTORY_MANIFEST_SHA256 = "57a671e4a2b7bfaf84e30dc1804ce05ff4babb1344481f1dc78a1fa4a7eadf03"
EXPECTED_QUEUE_SHA256 = archive_projection.INPUT_SHA256
EXPECTED_QUEUE_BYTES = archive_projection.INPUT_BYTES
EXPECTED_QUEUE_RECEIPT_SHA256 = archive_projection.INPUT_RECEIPT_SHA256
EXPECTED_ARCHIVE_SHA256 = "b348ef424d26a3baa3b296ade34c136d7cac8ed428c803d3b3c2a3edc5c01a72"
EXPECTED_ARCHIVE_RECEIPT_SHA256 = "ca8eacdd774efa298b3c98edd3cb67722c3e3eaa11862c7113060ca4c25be670"
EXPECTED_STATUS_COUNTS = {"success": 4_176, "failed": 689, "unknown": 0}
EXPECTED_REFERENCE_COUNT = 4_865
EXPECTED_CLASS_COUNT = 4_595
EXPECTED_CAPTURE_COUNT = 48
EXPECTED_BUCKETS = ("20261012T060500Z", "20261012T061000Z", "20261012T061500Z")
MAX_RESPONSE_BYTES = lease_schema.MAX_RESPONSE_BYTES
FRAME_RAW_BYTE_CAP = lease_schema.CAPS["frame_raw_bytes"]


class TriageError(ValueError):
    """Frozen source or reference reconciliation violates the triage contract."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise TriageError(reason)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _canonical_sha(value: Any) -> str:
    return _sha(_canonical(value))


def _strict_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _ref_identity(ref: Mapping[str, Any]) -> dict[str, Any]:
    path = ref.get("instruction_path")
    _require(isinstance(path, dict), "reference_path_object")
    path = cast(dict[str, Any], path)
    return {
        "capture_id": ref.get("capture_id"),
        "signature_sha256": ref.get("signature_sha256"),
        "instruction_path": {
            "outer_index": path.get("outer_index"),
            "inner_index": path.get("inner_index"),
        },
    }


def _identity_key(ref: Mapping[str, Any]) -> tuple[str, str, int, int | None]:
    path = ref.get("instruction_path")
    _require(isinstance(path, dict), "reference_path_object")
    path = cast(dict[str, Any], path)
    return (
        cast(str, ref.get("capture_id")),
        cast(str, ref.get("signature_sha256")),
        cast(int, path.get("outer_index")),
        cast(int | None, path.get("inner_index")),
    )


def _family_key(
    data: bytes,
    *,
    account_count: int,
    context: str,
    transaction_status: str,
) -> dict[str, Any]:
    width = min(OUTPUT_PREFIX_BYTES, len(data))
    return {
        "schema": FAMILY_SCHEMA,
        "raw_prefix_hex": data[:OUTPUT_PREFIX_BYTES].hex(),
        "raw_prefix_width": width,
        "instruction_data_length_bytes": len(data),
        "account_count": account_count,
        "context": context,
        "transaction_status": transaction_status,
    }


def _load_idl_versions(
    *,
    read_bytes: Callable[[str], bytes],
    expected_manifest_sha256: str = IDL_MANIFEST_SHA256,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_raw = read_bytes(IDL_MANIFEST_RELATIVE)
    _require(_sha(manifest_raw) == expected_manifest_sha256, "idl_manifest_hash")
    try:
        manifest = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TriageError("idl_manifest_json") from exc
    _require(isinstance(manifest, dict), "idl_manifest_object")
    rows = manifest.get("versions")
    _require(isinstance(rows, list) and len(rows) == 18, "idl_versions_count")
    versions: list[dict[str, Any]] = []
    for capture in rows:
        _require(isinstance(capture, dict), "idl_capture_row")
        path = capture.get("path")
        _require(
            isinstance(path, str) and path.startswith(IDL_CAPTURE_DIR.as_posix() + "/"),
            "idl_path_scope",
        )
        raw = read_bytes(path)
        _require(
            len(raw) == capture.get("bytes") and _sha(raw) == capture.get("sha256"),
            "idl_blob_binding",
        )
        try:
            idl = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TriageError("idl_blob_json") from exc
        _require(isinstance(idl, dict) and idl.get("address") == PROGRAM_ID, "idl_program_id")
        versions.append({"capture": dict(capture), "idl": idl})
    _require(manifest.get("history_entry_count") == len(versions), "idl_manifest_count")
    _require(
        sum(cast(int, row["capture"]["bytes"]) for row in versions) == 1_974_484, "idl_total_bytes"
    )
    return versions, manifest


def _idl_index(versions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for version in versions:
        capture = version["capture"]
        idl = version["idl"]
        for instruction in idl.get("instructions", []):
            if not isinstance(instruction, dict):
                continue
            discriminator = instruction.get("discriminator")
            if not isinstance(discriminator, list) or not all(
                type(byte) is int and 0 <= byte <= 255 for byte in discriminator
            ):
                continue
            raw = bytes(discriminator)
            if len(raw) != OUTPUT_PREFIX_BYTES or not isinstance(instruction.get("name"), str):
                continue
            accounts = instruction.get("accounts", [])
            args = instruction.get("args", [])
            account_count = len(accounts) if isinstance(accounts, list) else None
            arg_count = len(args) if isinstance(args, list) else None
            index[raw.hex()].append(
                {
                    "name": instruction["name"],
                    "commit": capture.get("commit"),
                    "commit_time_utc": capture.get("commit_time_utc"),
                    "idl_sha256": capture.get("sha256"),
                    "documented_fixed_account_count": account_count,
                    "documented_argument_count": arg_count,
                }
            )
    return {
        key: sorted(values, key=lambda row: (row["commit"], row["name"]))
        for key, values in index.items()
    }


def _idl_classification(
    prefix_hex: str, width: int, idl_index: Mapping[str, list[dict[str, Any]]]
) -> tuple[str, str | None, list[dict[str, Any]]]:
    if width != OUTPUT_PREFIX_BYTES:
        return "unresolved_short_raw_prefix", None, []
    matches = list(idl_index.get(prefix_hex, []))
    names = sorted({row["name"] for row in matches})
    if not matches:
        return "unresolved_no_pinned_idl_discriminator_match", None, []
    if len(names) != 1:
        return "unresolved_ambiguous_pinned_idl_name", None, matches
    return "documented_idl_name_match_sample_only", names[0], matches


def _source_ref_index(
    references: list[dict[str, Any]],
) -> dict[tuple[str, str, int, int | None], dict[str, Any]]:
    result: dict[tuple[str, str, int, int | None], dict[str, Any]] = {}
    for ref in references:
        key = _identity_key(ref)
        _require(key not in result, "duplicate_source_reference_identity")
        _require(
            ref.get("program_id") == PROGRAM_ID
            and ref.get("transaction_status") in {"success", "failed", "unknown"}
            and structural_schema.require_digest(ref.get("signature_sha256"), "signature_sha")
            == ref.get("signature_sha256"),
            "source_reference_identity_fields",
        )
        path = ref.get("instruction_path")
        _require(isinstance(path, dict), "source_reference_path")
        path = cast(dict[str, Any], path)
        _require(_strict_int(path.get("outer_index")), "source_reference_outer_index")
        _require(
            path.get("inner_index") is None or _strict_int(path.get("inner_index")),
            "source_reference_inner_index",
        )
        _require(
            ref.get("reference_id")
            == structural_schema.digest(structural_schema.canonical(_ref_identity(ref))),
            "source_reference_id_binding",
        )
        result[key] = ref
    return result


def _extract_raw_references(
    *,
    response: Mapping[str, Any],
    capture: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rpc_result = response.get("result")
    _require(isinstance(rpc_result, dict), "raw_response_result")
    rpc_result = cast(dict[str, Any], rpc_result)
    transactions = rpc_result.get("transactions")
    _require(isinstance(transactions, list), "raw_response_transactions")
    extracted: list[dict[str, Any]] = []
    for entry in cast(list[Any], transactions):
        _require(isinstance(entry, dict), "raw_transaction_object")
        transaction = entry
        tx = entry.get("transaction")
        signatures = tx.get("signatures") if isinstance(tx, dict) else None
        _require(
            isinstance(signatures, list) and bool(signatures) and isinstance(signatures[0], str),
            "raw_transaction_signature",
        )
        signatures = cast(list[Any], signatures)
        signature_sha = _sha(cast(str, signatures[0]).encode("utf-8"))
        status = structural_input._resolved_status(entry.get("meta"))
        try:
            normalized = normalize_compiled_transaction(transaction)
        except NormalizationError as exc:
            raise TriageError(f"raw_transaction_normalization:{exc.category}") from exc
        for context, instructions in (
            ("outer", normalized["outer_instructions"]),
            ("inner", normalized["inner_instructions"]),
        ):
            for instruction in instructions:
                if instruction.get("program_id") != PROGRAM_ID:
                    continue
                try:
                    data = bytes.fromhex(instruction["data_hex"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise TriageError("raw_instruction_data_invalid") from exc
                if context == "outer":
                    path = {"outer_index": instruction.get("outer_index"), "inner_index": None}
                else:
                    raw_path = instruction.get("path")
                    _require(isinstance(raw_path, list) and len(raw_path) == 2, "raw_inner_path")
                    path = {"outer_index": raw_path[0], "inner_index": raw_path[1]}
                accounts = instruction.get("account_indexes")
                _require(isinstance(accounts, list), "raw_instruction_accounts")
                extracted.append(
                    {
                        "capture_id": capture["capture_id"],
                        "signature_sha256": signature_sha,
                        "instruction_path": path,
                        "program_id": instruction.get("program_id"),
                        "transaction_status": status,
                        "instruction_data": data,
                        "instruction_data_sha256": _sha(data),
                        "instruction_data_length_bytes": len(data),
                        "account_count": len(accounts),
                        "context": context,
                        "stack_height": instruction.get("stack_height"),
                    }
                )
    return extracted


def reconcile_and_group(
    source_references: list[dict[str, Any]],
    raw_references: list[dict[str, Any]],
    *,
    idl_index: Mapping[str, list[dict[str, Any]]],
    expected_status_counts: Mapping[str, int] = EXPECTED_STATUS_COUNTS,
    expected_reference_count: int = EXPECTED_REFERENCE_COUNT,
) -> dict[str, Any]:
    """Join source refs to raw instructions by capture/signature/typed path."""
    source_index = _source_ref_index(source_references)
    raw_index: dict[tuple[str, str, int, int | None], dict[str, Any]] = {}
    for raw_ref in raw_references:
        raw_path = raw_ref.get("instruction_path")
        _require(isinstance(raw_path, dict), "raw_reference_path")
        raw_path = cast(dict[str, Any], raw_path)
        _require(_strict_int(raw_path.get("outer_index")), "raw_reference_outer_index")
        _require(
            raw_path.get("inner_index") is None or _strict_int(raw_path.get("inner_index")),
            "raw_reference_inner_index",
        )
        _require(
            structural_schema.require_digest(raw_ref.get("signature_sha256"), "raw_signature_sha")
            == raw_ref.get("signature_sha256"),
            "raw_signature_binding",
        )
        _require(
            raw_ref.get("transaction_status") in {"success", "failed", "unknown"},
            "raw_transaction_status",
        )
        key = _identity_key(raw_ref)
        _require(key not in raw_index, "duplicate_raw_reference_identity")
        raw_index[key] = raw_ref
    _require(set(source_index) == set(raw_index), "raw_source_reference_set_mismatch")

    status_counts: Counter[str] = Counter()
    grouped: dict[str, dict[str, Any]] = {}
    projected: list[dict[str, Any]] = []
    for key in sorted(
        source_index,
        key=lambda identity: (
            identity[0],
            identity[1],
            identity[2],
            -1 if identity[3] is None else identity[3],
        ),
    ):
        source = source_index[key]
        raw_ref = raw_index[key]
        for field in (
            "program_id",
            "transaction_status",
            "instruction_data_sha256",
            "account_count",
        ):
            _require(source.get(field) == raw_ref.get(field), f"raw_source_{field}_mismatch")
        path = raw_ref["instruction_path"]
        context = "outer" if path["inner_index"] is None else "inner"
        _require(context == raw_ref["context"], "raw_path_context_mismatch")
        data = raw_ref["instruction_data"]
        _require(isinstance(data, bytes), "raw_data_bytes")
        _require(
            raw_ref.get("instruction_data_length_bytes") == len(data),
            "raw_data_length_mismatch",
        )
        _require(_sha(data) == source.get("instruction_data_sha256"), "raw_data_hash_mismatch")
        _require(
            structural_input._fingerprint(PROGRAM_ID, data.hex(), raw_ref["account_count"])
            == source.get("class_fingerprint_sha256"),
            "source_class_fingerprint_mismatch",
        )
        prefix_width = min(OUTPUT_PREFIX_BYTES, len(data))
        family_key = _family_key(
            data,
            account_count=raw_ref["account_count"],
            context=context,
            transaction_status=raw_ref["transaction_status"],
        )
        family_sha = structural_schema.digest(structural_schema.canonical(family_key))
        idl_status, idl_name, idl_matches = _idl_classification(
            family_key["raw_prefix_hex"], prefix_width, idl_index
        )
        status = cast(str, source["transaction_status"])
        status_counts[status] += 1
        family = grouped.setdefault(
            family_sha,
            {
                "family_id": family_sha,
                "family_key": family_key,
                "reference_occurrences": 0,
                "transaction_status_counts": {
                    state: 0 for state in ("success", "failed", "unknown")
                },
                "idl_match_classification": idl_status,
                "documented_idl_name": idl_name,
                "pinned_idl_matches": idl_matches,
                "representative_candidates": [],
            },
        )
        _require(family["family_key"] == family_key, "family_hash_collision")
        _require(
            family["idl_match_classification"] == idl_status
            and family["documented_idl_name"] == idl_name
            and family["pinned_idl_matches"] == idl_matches,
            "family_idl_classification_collision",
        )
        family["reference_occurrences"] += 1
        family["transaction_status_counts"][status] += 1
        identity_sha = structural_schema.digest(structural_schema.canonical(_ref_identity(source)))
        family["representative_candidates"].append(identity_sha)
        projected.append(
            {
                "reference_id": source["reference_id"],
                "capture_id": source["capture_id"],
                "signature_sha256": source["signature_sha256"],
                "instruction_path": dict(source["instruction_path"]),
                "program_id": source["program_id"],
                "transaction_status": status,
                "instruction_data_sha256": raw_ref["instruction_data_sha256"],
                "instruction_data_length_bytes": raw_ref["instruction_data_length_bytes"],
                "raw_prefix_hex": family_key["raw_prefix_hex"],
                "raw_prefix_width": prefix_width,
                "account_count": raw_ref["account_count"],
                "context": context,
                "stack_height": raw_ref["stack_height"],
                "class_fingerprint_schema": source["class_fingerprint_schema"],
                "class_fingerprint_sha256": source["class_fingerprint_sha256"],
                "capture_receipt_sha256": source["capture_receipt_sha256"],
                "response_sha256": source["response_sha256"],
                "request_sha256": source["request_sha256"],
                "result_sha256": source["result_sha256"],
                "bucket": source["bucket"],
                "slot": source["slot"],
                "blockhash": source["blockhash"],
                "request_path": source["request_path"],
                "result_path": source["result_path"],
                "capture_receipt_path": source["capture_receipt_path"],
                "response_path": source["response_path"],
                "reference_identity_sha256": identity_sha,
                "source_event_time_ns": source["source_event_time_ns"],
                "received_ns": source["received_ns"],
                "capture_available_ns": source["available_ns"],
                "source_reconciliation": "exact_capture_signature_typed_path_match",
                "family_id": family_sha,
                "idl_match_classification": idl_status,
                "documented_idl_name": idl_name,
                "pinned_idl_matches": idl_matches,
                "executed_instruction": None,
                "instruction_effect": None,
                "settlement": None,
            }
        )

    _require(len(projected) == expected_reference_count, "reference_count")
    _require(
        {state: status_counts[state] for state in ("success", "failed", "unknown")}
        == dict(expected_status_counts),
        "reference_status_denominators",
    )
    projected.sort(key=lambda row: row["reference_id"])
    family_rows: list[dict[str, Any]] = []
    rep_lookup: dict[str, list[str]] = {}
    for family_sha, family in grouped.items():
        reps = sorted(family.pop("representative_candidates"))[:REPRESENTATIVES_PER_FAMILY]
        rep_lookup[family_sha] = reps
        family["representative_reference_identity_hashes"] = reps
        family_rows.append(family)
    family_rows.sort(key=lambda row: row["family_id"])
    _require(
        sum(row["reference_occurrences"] for row in family_rows) == len(projected), "family_ref_sum"
    )
    return {
        "references": projected,
        "families": family_rows,
        "denominators": {
            "reference_occurrences": len(projected),
            "transaction_status_counts": {
                state: status_counts[state] for state in ("success", "failed", "unknown")
            },
            "family_count": len(family_rows),
            "representatives_per_family_max": REPRESENTATIVES_PER_FAMILY,
        },
    }


def _validate_input_pair(
    queue_bytes: bytes,
    queue_receipt_bytes: bytes,
    archive_bytes: bytes,
    archive_receipt_bytes: bytes,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    _require(len(queue_bytes) == EXPECTED_QUEUE_BYTES, "queue_size_pin")
    _require(_sha(queue_bytes) == EXPECTED_QUEUE_SHA256, "queue_hash_pin")
    _require(_sha(queue_receipt_bytes) == EXPECTED_QUEUE_RECEIPT_SHA256, "queue_receipt_hash_pin")
    _require(_sha(archive_bytes) == EXPECTED_ARCHIVE_SHA256, "archive_hash_pin")
    _require(
        _sha(archive_receipt_bytes) == EXPECTED_ARCHIVE_RECEIPT_SHA256, "archive_receipt_hash_pin"
    )
    try:
        queue = json.loads(queue_bytes)
        queue_receipt = json.loads(queue_receipt_bytes)
        archive = json.loads(archive_bytes)
        archive_receipt = json.loads(archive_receipt_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TriageError("source_json") from exc
    for value, code in (
        (queue, "queue_object"),
        (queue_receipt, "queue_receipt_object"),
        (archive, "archive_object"),
        (archive_receipt, "archive_receipt_object"),
    ):
        _require(isinstance(value, dict), code)
    queue_dict = cast(dict[str, Any], queue)
    archive_dict = cast(dict[str, Any], archive)
    _require(structural_schema.canonical(queue) == queue_bytes, "queue_canonical")
    _require(_canonical(archive) == archive_bytes, "archive_canonical")
    q_receipt = cast(dict[str, Any], queue_receipt)
    _require(
        q_receipt.get("artifact_sha256") == EXPECTED_QUEUE_SHA256
        and q_receipt.get("artifact_path") == QUEUE_RELATIVE
        and q_receipt.get("source_root_id") == archive_projection.STORAGE_ROOT_ID
        and str(q_receipt.get("volume_uuid", "")).lower() == archive_projection.STORAGE_VOLUME_UUID,
        "queue_receipt_binding",
    )
    _require(
        q_receipt.get("schema") == "brain-senses-external-partial-structural-queue-publication-v2"
        and q_receipt.get("status") == "created_or_verified"
        and q_receipt.get("provider_io") is False
        and q_receipt.get("availability_ns") == archive_projection.INPUT_RECEIPT_NS,
        "queue_receipt_contract",
    )
    a_receipt = cast(dict[str, Any], archive_receipt)
    archive_projection._verify_existing_publication(
        archive_bytes,
        archive_receipt_bytes,
        input_sha256=EXPECTED_QUEUE_SHA256,
        input_receipt_sha256=EXPECTED_QUEUE_RECEIPT_SHA256,
    )
    _require(
        archive_dict.get("source_binding", {}).get("source_artifact_sha256")
        == EXPECTED_QUEUE_SHA256,
        "archive_source_queue_binding",
    )
    _require(
        queue_dict.get("denominators") == archive_projection.EXPECTED_DENOMINATORS,
        "queue_denominators",
    )
    _require(
        archive_dict.get("denominators", {}).get("reference_status_counts")
        == EXPECTED_STATUS_COUNTS,
        "archive_status_denominators",
    )
    observed = queue_dict.get("observed")
    _require(isinstance(observed, dict), "queue_observed")
    queue_refs = cast(dict[str, Any], observed).get("references")
    archive_classes = archive_dict.get("classes")
    _require(isinstance(queue_refs, list), "queue_references")
    _require(isinstance(archive_classes, list), "archive_classes")
    _validate_class_reconciliation(
        cast(list[dict[str, Any]], queue_refs),
        cast(list[dict[str, Any]], archive_classes),
    )
    _validate_archive_schedule(archive_dict)
    return cast(dict[str, Any], queue), q_receipt, cast(dict[str, Any], archive), a_receipt


def _validate_class_reconciliation(
    references: list[dict[str, Any]], classes: list[dict[str, Any]]
) -> None:
    grouped: dict[tuple[Any, ...], Counter[str]] = defaultdict(Counter)
    for reference in references:
        key = (
            reference.get("program_id"),
            reference.get("instruction_data_sha256"),
            reference.get("account_count"),
            reference.get("class_fingerprint_schema"),
            reference.get("class_fingerprint_sha256"),
        )
        status = reference.get("transaction_status")
        _require(status in EXPECTED_STATUS_COUNTS, "class_reference_status")
        grouped[key][cast(str, status)] += 1
    expected: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in classes:
        key = (
            row.get("program_id"),
            row.get("instruction_data_sha256"),
            row.get("account_count"),
            row.get("class_fingerprint_schema"),
            row.get("class_fingerprint_sha256"),
        )
        _require(key not in expected, "duplicate_class_identity")
        expected[key] = row
    _require(set(grouped) == set(expected), "class_reference_identity_set")
    for key, counts in grouped.items():
        row = expected[key]
        _require(row.get("reference_occurrences") == sum(counts.values()), "class_reference_count")
        _require(
            row.get("transaction_status_counts")
            == {state: counts[state] for state in ("success", "failed", "unknown")},
            "class_status_counts",
        )


def _validate_archive_schedule(archive: Mapping[str, Any]) -> None:
    schedule = archive.get("schedule")
    _require(isinstance(schedule, list) and len(schedule) == 12, "archive_schedule_rows")
    rows = cast(list[dict[str, Any]], schedule)
    _require(sum(row.get("state") == "sealed" for row in rows) == 3, "archive_sealed_count")
    _require(
        sum(row.get("state") == "callback_failed_unobserved" for row in rows) == 1,
        "archive_callback_failure_count",
    )
    _require(
        sum(row.get("state") == "expired_unobserved" for row in rows) == 8,
        "archive_expired_count",
    )
    metrics = (
        "produced_slots",
        "selected_blocks",
        "scanned_transactions",
        "success_transactions",
        "failed_transactions",
        "unknown_transactions",
        "reference_occurrences",
        "unknown_scan_transactions",
    )
    for row in rows:
        if row.get("state") == "sealed":
            continue
        counts = row.get("counts")
        _require(isinstance(counts, dict), "archive_unobserved_counts")
        counts = cast(dict[str, Any], counts)
        _require(
            all(counts.get(metric) is None for metric in metrics),
            "archive_unobserved_not_null",
        )


def _build_artifact(
    *,
    queue: Mapping[str, Any],
    queue_receipt_bytes: bytes,
    archive: Mapping[str, Any],
    archive_receipt_bytes: bytes,
    output: Mapping[str, Any],
    idl_versions: list[dict[str, Any]],
    source_hashes: Mapping[str, str],
    projection_known_ns: int,
) -> dict[str, Any]:
    observed = queue.get("observed")
    _require(isinstance(observed, dict), "queue_observed")
    observed = cast(dict[str, Any], observed)
    source_captures = observed.get("captures")
    _require(
        isinstance(source_captures, list) and len(source_captures) == EXPECTED_CAPTURE_COUNT,
        "capture_count",
    )
    source_captures = cast(list[dict[str, Any]], source_captures)
    capture_counts = Counter(row.get("bucket") for row in source_captures)
    _require(
        capture_counts == Counter({bucket: 16 for bucket in EXPECTED_BUCKETS}),
        "capture_bucket_denominators",
    )
    captured_hashes = [
        {
            "capture_id": row["capture_id"],
            "bucket": row["bucket"],
            "slot": row["slot"],
            "blockhash": row["blockhash"],
            "capture_receipt_sha256": row["capture_receipt_sha256"],
            "response_sha256": row["response_sha256"],
        }
        for row in source_captures
    ]
    return {
        "schema": TRIAGE_SCHEMA,
        "schema_version": 1,
        "status": "sample_only_raw_structural_family_triage",
        "source_binding": {
            "queue_artifact_sha256": EXPECTED_QUEUE_SHA256,
            "queue_publication_receipt_sha256": _sha(queue_receipt_bytes),
            "archive_projection_sha256": EXPECTED_ARCHIVE_SHA256,
            "archive_projection_receipt_sha256": _sha(archive_receipt_bytes),
            "source_code_sha256": dict(sorted(source_hashes.items())),
            "idl_history_receipt_sha256": IDL_HISTORY_RECEIPT_SHA256,
            "idl_manifest_sha256": IDL_MANIFEST_SHA256,
            "idl_blobs": [dict(row["capture"]) for row in idl_versions],
            "selected_capture_hashes": captured_hashes,
            "maximum_source_availability_ns": max(
                cast(int, row["available_ns"]) for row in source_captures
            ),
        },
        "scope": {
            "network": NETWORK,
            "process_version": "external-v2-partial-structural-queue-v2",
            "service_id": SERVICE_ID,
            "program_id": PROGRAM_ID,
            "sample_only": True,
            "projection_known_ns": projection_known_ns,
            "family_schema": FAMILY_SCHEMA,
            "raw_prefix_width_max_bytes": OUTPUT_PREFIX_BYTES,
            "representative_rule": "three lowest canonical reference-identity hashes per family",
            "idl_match_rule": (
                "exact raw 8-byte discriminator match in one or more of 18 pinned IDL blobs; "
                "documented shape only"
            ),
        },
        "schedule": archive["schedule"],
        "denominators": {
            **archive["denominators"],
            "raw_references_reconciled": output["denominators"]["reference_occurrences"],
            "family_count": output["denominators"]["family_count"],
            "reference_status_counts": output["denominators"]["transaction_status_counts"],
            "unmatched_references": 0,
            "ambiguous_or_unresolved_idl_families": sum(
                family["idl_match_classification"].startswith("unresolved_")
                for family in output["families"]
            ),
        },
        "references": output["references"],
        "families": output["families"],
        "nonclaims": {
            "extrapolation": False,
            "zero_or_absence_claim": False,
            "no_new_class_claim": False,
            "execution_or_effect_claim": False,
            "settlement_claim": False,
            "economic_or_wallet_claim": False,
            "layout_or_lifecycle_join": False,
            "price_or_candidate_position_join": False,
            "market_event_deduplication": False,
            "process_support": False,
            "point_in_time_support": False,
            "training_population_evaluation_admission": False,
            "decision_ns": None,
            "unobserved_windows_remain_null": True,
        },
    }


def _open_data_root(guard: Any) -> int:
    current = os.dup(guard.mount_fd)
    device = os.fstat(current).st_dev
    try:
        for component in guard.config.data_root.split("/"):
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            info = os.fstat(child)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_dev != device
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                os.close(child)
                raise TriageError("storage_root_identity_or_mode")
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _publication_action(artifact_exists: bool, receipt_exists: bool) -> str:
    if artifact_exists and receipt_exists:
        return "verify_existing"
    if artifact_exists or receipt_exists:
        raise TriageError("incomplete_existing_publication")
    return "create"


def _build_receipt(artifact_bytes: bytes, *, projection_known_ns: int) -> dict[str, Any]:
    receipt = {
        "schema": "pump-amm-raw-structural-family-triage-publication-v1",
        "schema_version": 1,
        "status": "created_or_verified",
        "artifact_path": OUTPUT_RELATIVE,
        "artifact_sha256": _sha(artifact_bytes),
        "queue_artifact_sha256": EXPECTED_QUEUE_SHA256,
        "queue_publication_receipt_sha256": EXPECTED_QUEUE_RECEIPT_SHA256,
        "archive_projection_sha256": EXPECTED_ARCHIVE_SHA256,
        "archive_projection_receipt_sha256": EXPECTED_ARCHIVE_RECEIPT_SHA256,
        "projection_known_ns": projection_known_ns,
        "point_in_time_eligible": False,
        "provider_io": False,
    }
    receipt["content_sha256"] = _canonical_sha(receipt)
    return receipt


def _verify_existing_publication(
    artifact_bytes: bytes,
    receipt_bytes: bytes,
    *,
    expected_source_hashes: Mapping[str, str],
    expected_idl_blobs: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        artifact, receipt = json.loads(artifact_bytes), json.loads(receipt_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TriageError("existing_publication_json") from exc
    _require(
        isinstance(artifact, dict) and isinstance(receipt, dict), "existing_publication_objects"
    )
    _require(
        _canonical(artifact) == artifact_bytes and _canonical(receipt) == receipt_bytes,
        "existing_publication_canonical",
    )
    _require(
        receipt.get("schema") == "pump-amm-raw-structural-family-triage-publication-v1"
        and receipt.get("status") == "created_or_verified",
        "existing_receipt_schema",
    )
    _require(
        receipt.get("artifact_path") == OUTPUT_RELATIVE
        and receipt.get("artifact_sha256") == _sha(artifact_bytes),
        "existing_artifact_binding",
    )
    for field, expected in (
        ("queue_artifact_sha256", EXPECTED_QUEUE_SHA256),
        ("queue_publication_receipt_sha256", EXPECTED_QUEUE_RECEIPT_SHA256),
        ("archive_projection_sha256", EXPECTED_ARCHIVE_SHA256),
        ("archive_projection_receipt_sha256", EXPECTED_ARCHIVE_RECEIPT_SHA256),
    ):
        _require(receipt.get(field) == expected, f"existing_{field}")
    content = dict(receipt)
    content_hash = content.pop("content_sha256", None)
    _require(content_hash == _canonical_sha(content), "existing_receipt_content_hash")
    _require(
        artifact.get("schema") == TRIAGE_SCHEMA
        and artifact.get("status") == "sample_only_raw_structural_family_triage",
        "existing_artifact_schema",
    )
    binding = artifact.get("source_binding")
    _require(isinstance(binding, dict), "existing_source_binding")
    binding = cast(dict[str, Any], binding)
    _require(
        binding.get("queue_artifact_sha256") == EXPECTED_QUEUE_SHA256
        and binding.get("archive_projection_sha256") == EXPECTED_ARCHIVE_SHA256,
        "existing_artifact_source_binding",
    )
    source_code = binding.get("source_code_sha256")
    _require(isinstance(source_code, dict), "existing_source_code_binding")
    source_code = cast(dict[str, Any], source_code)
    _require(
        source_code.get("tools/brain_senses_external_structural_family_triage_v1.py")
        == _sha(Path(__file__).read_bytes()),
        "existing_tool_hash",
    )
    _require(source_code == dict(expected_source_hashes), "existing_source_code_hashes")
    _require(binding.get("idl_blobs") == expected_idl_blobs, "existing_idl_blob_bindings")
    _require(
        binding.get("idl_history_receipt_sha256") == IDL_HISTORY_RECEIPT_SHA256
        and binding.get("idl_manifest_sha256") == IDL_MANIFEST_SHA256,
        "existing_idl_binding",
    )
    _require(
        artifact.get("scope", {}).get("projection_known_ns") == receipt.get("projection_known_ns"),
        "existing_time_binding",
    )
    projection_known_ns = receipt.get("projection_known_ns")
    maximum_source_availability_ns = binding.get("maximum_source_availability_ns")
    _require(
        _strict_int(projection_known_ns)
        and _strict_int(maximum_source_availability_ns)
        and projection_known_ns > maximum_source_availability_ns
        and projection_known_ns > archive_projection.INPUT_RECEIPT_NS,
        "existing_time_after_source",
    )
    _require(
        receipt.get("provider_io") is False and receipt.get("point_in_time_eligible") is False,
        "existing_claims",
    )
    return {
        **receipt,
        "publication_receipt_sha256": _sha(receipt_bytes),
        "bytes": len(artifact_bytes),
    }


def publish_create_only(
    artifact: Mapping[str, Any],
    *,
    projection_known_ns: int,
    expected_source_hashes: Mapping[str, str],
    expected_idl_blobs: list[dict[str, Any]],
    config_path: Path = STORAGE_CONFIG,
) -> dict[str, Any]:
    artifact_bytes = _canonical(artifact)
    _require(len(artifact_bytes) <= OUTPUT_MAX_BYTES, "output_size_cap")
    receipt = _build_receipt(artifact_bytes, projection_known_ns=projection_known_ns)
    receipt_bytes = _canonical(receipt)
    relative_dir = OUTPUT_RELATIVE.rsplit("/", 1)[0]
    with writer_guard(config_path) as guard:
        _require(
            guard.root_id == archive_projection.STORAGE_ROOT_ID
            and guard.volume_uuid.lower() == archive_projection.STORAGE_VOLUME_UUID,
            "storage_root_binding",
        )
        guard.create_directory(relative_dir)
        root_fd = _open_data_root(guard)
        try:

            def before_write() -> None:
                guard.revalidate()

            with DescriptorWriter(root_fd, before_write=before_write) as writer:
                action = _publication_action(
                    writer.exists(OUTPUT_RELATIVE), writer.exists(OUTPUT_RECEIPT_RELATIVE)
                )
                if action == "verify_existing":
                    prior = _verify_existing_publication(
                        writer.read_bytes(OUTPUT_RELATIVE, max_bytes=OUTPUT_MAX_BYTES),
                        writer.read_bytes(OUTPUT_RECEIPT_RELATIVE, max_bytes=64_000),
                        expected_source_hashes=expected_source_hashes,
                        expected_idl_blobs=expected_idl_blobs,
                    )
                    guard.revalidate()
                    return prior
                writer.create(OUTPUT_RELATIVE, artifact_bytes)
                writer.create(OUTPUT_RECEIPT_RELATIVE, receipt_bytes)
        finally:
            os.close(root_fd)
        guard.revalidate()
    return {
        **receipt,
        "publication_receipt_sha256": _sha(receipt_bytes),
        "bytes": len(artifact_bytes),
    }


def run(*, publish: bool, config_path: Path = STORAGE_CONFIG) -> dict[str, Any]:
    """Verify pinned inputs, reopen selected captures, optionally publish."""
    with writer_guard(config_path) as guard:
        _require(
            guard.root_id == archive_projection.STORAGE_ROOT_ID
            and guard.volume_uuid.lower() == archive_projection.STORAGE_VOLUME_UUID,
            "input_storage_root_binding",
        )
        root_fd = _open_data_root(guard)
        try:
            with DescriptorWriter(root_fd) as reader:
                queue_bytes = reader.read_bytes(QUEUE_RELATIVE, max_bytes=EXPECTED_QUEUE_BYTES + 1)
                queue_receipt_bytes = reader.read_bytes(QUEUE_RECEIPT_RELATIVE, max_bytes=64_000)
                archive_bytes = reader.read_bytes(ARCHIVE_RELATIVE, max_bytes=OUTPUT_MAX_BYTES)
                archive_receipt_bytes = reader.read_bytes(
                    ARCHIVE_RECEIPT_RELATIVE, max_bytes=64_000
                )
        finally:
            os.close(root_fd)
    queue, queue_receipt, archive, _ = _validate_input_pair(
        queue_bytes, queue_receipt_bytes, archive_bytes, archive_receipt_bytes
    )
    activation, plan = launch.activation_gate(ACTIVATION, config_path, running=False)
    _require(activation.get("plan_sha256") == EXPECTED_PLAN_SHA256, "activation_plan_pin")
    sources = engine.source_bindings()
    _require(plan.get("code_sha256") == sources, "source_binding")
    with (
        launch.host_lock(),
        writer_guard(config_path, min_free_bytes=partial_queue.MIN_FREE_BYTES) as guard,
    ):
        guard.revalidate(min_free_bytes=partial_queue.MIN_FREE_BYTES)
        _require(
            guard.root_id == plan["storage"]["root_id"]
            and guard.volume_uuid.lower() == plan["storage"]["volume_uuid"].lower(),
            "source_storage_identity",
        )
        source_fd = structural_publish._open_relative(
            guard, f"services/{SERVICE_ID}", create=False, minimum=partial_queue.MIN_FREE_BYTES
        )
        predecessor_fd = structural_publish._open_relative(
            guard,
            plan["predecessor"]["relative_root"],
            create=False,
            minimum=partial_queue.MIN_FREE_BYTES,
        )
        try:
            with launch.predecessor_read_lock(predecessor_fd):
                with structural_publish._existing_shared_lock(
                    source_fd, lease_schema.LOCK_FILE, device=os.fstat(source_fd).st_dev
                ):
                    with source_verify.DescriptorReaderV2(source_fd) as source_reader:
                        with predecessor_verify.DescriptorReader(
                            predecessor_fd
                        ) as predecessor_reader:
                            rebuilt = partial_queue.compile_partial_prefix(
                                source_reader,
                                predecessor_reader,
                                activation=activation,
                                expected_sources=sources,
                                failure_path=FAILURE_RECEIPT,
                                status_path=FINAL_STATUS_RECEIPT,
                            )
                            _require(
                                structural_schema.canonical(rebuilt) == queue_bytes,
                                "recompiled_queue_mismatch",
                            )
                            observed = rebuilt.get("observed")
                            _require(isinstance(observed, dict), "recompiled_queue_observed")
                            observed = cast(dict[str, Any], observed)
                            source_refs = observed.get("references")
                            captures = observed.get("captures")
                            _require(isinstance(source_refs, list), "recompiled_source_references")
                            _require(
                                isinstance(captures, list)
                                and len(captures) == EXPECTED_CAPTURE_COUNT,
                                "recompiled_capture_count",
                            )
                            captures = cast(list[dict[str, Any]], captures)
                            raw_refs: list[dict[str, Any]] = []
                            for capture in captures:
                                _require(isinstance(capture, dict), "capture_row")
                                response_path = capture.get("response_path")
                                _require(isinstance(response_path, str), "capture_response_path")
                                response_raw = source_reader.read_bytes(
                                    cast(str, response_path), limit=MAX_RESPONSE_BYTES
                                )
                                _require(
                                    _sha(response_raw) == capture.get("response_sha256"),
                                    "capture_response_hash",
                                )
                                response = structural_input._decode(response_raw)
                                _require(isinstance(response, dict), "capture_response_object")
                                raw_refs.extend(
                                    _extract_raw_references(response=response, capture=capture)
                                )
        finally:
            os.close(source_fd)
            os.close(predecessor_fd)

    archive_refs = archive.get("references")
    _require(isinstance(archive_refs, list), "archive_references")
    # Require the archive projection to preserve precisely the frozen queue's rows.
    observed_queue = queue.get("observed")
    _require(isinstance(observed_queue, dict), "queue_observed")
    queue_refs = cast(dict[str, Any], observed_queue).get("references")
    _require(isinstance(queue_refs, list), "queue_references")
    queue_refs = cast(list[dict[str, Any]], queue_refs)
    archive_refs = cast(list[dict[str, Any]], archive_refs)
    queue_identity = {_identity_key(row) for row in queue_refs}
    archive_identity = {_identity_key(row) for row in archive_refs}
    _require(
        queue_identity == archive_identity and len(queue_identity) == EXPECTED_REFERENCE_COUNT,
        "archive_queue_reference_join",
    )
    idl_raw_receipt = IDL_HISTORY_RECEIPT.read_bytes()
    _require(_sha(idl_raw_receipt) == IDL_HISTORY_RECEIPT_SHA256, "idl_history_receipt_hash")
    idl_versions, _manifest = _load_idl_versions(
        read_bytes=lambda relative: (REPO / relative).read_bytes()
    )
    output = reconcile_and_group(
        queue_refs,
        raw_refs,
        idl_index=_idl_index(idl_versions),
    )
    _require(
        output["denominators"]["family_count"] <= EXPECTED_REFERENCE_COUNT, "family_count_bound"
    )
    source_hashes = {
        "tools/brain_senses_external_structural_family_triage_v1.py": _sha(
            Path(__file__).read_bytes()
        ),
        **sources,
    }
    max_available = max(cast(int, row["available_ns"]) for row in captures)
    projection_known_ns = time.time_ns()
    _require(projection_known_ns > max_available, "projection_time_before_source_availability")
    _require(
        projection_known_ns > cast(int, queue_receipt["availability_ns"]),
        "projection_time_before_queue_receipt",
    )
    artifact = _build_artifact(
        queue=queue,
        queue_receipt_bytes=queue_receipt_bytes,
        archive=archive,
        archive_receipt_bytes=archive_receipt_bytes,
        output=output,
        idl_versions=idl_versions,
        source_hashes=source_hashes,
        projection_known_ns=projection_known_ns,
    )
    artifact_bytes = _canonical(artifact)
    _require(len(artifact_bytes) <= OUTPUT_MAX_BYTES, "output_size_cap")
    if not publish:
        return {
            "status": "verified_not_published",
            "artifact_sha256": _sha(artifact_bytes),
            "artifact_bytes": len(artifact_bytes),
            "denominators": artifact["denominators"],
            "family_count": output["denominators"]["family_count"],
            "source_binding": artifact["source_binding"],
            "projection_known_ns": projection_known_ns,
        }
    return publish_create_only(
        artifact,
        projection_known_ns=projection_known_ns,
        expected_source_hashes=source_hashes,
        expected_idl_blobs=[dict(row["capture"]) for row in idl_versions],
        config_path=config_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--verify-only", action="store_true", help="verify and summarize without writing"
    )
    mode.add_argument("--publish", action="store_true", help="verify and create-only publish")
    args = parser.parse_args()
    try:
        result = run(publish=args.publish)
    except (TriageError, OSError, ValueError, RuntimeError) as exc:
        print(f"structural-family triage failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
