#!/usr/bin/env python3
"""Build a sample-local diagnostic review queue for one sealed external-v2 prefix.

This adapter binds only the immutable external-v2 queue, archive projection, and
structural-family triage. It does not qualify an anomaly source, execution,
settlement, economics, point-in-time support, training, or activation.
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
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

REPO = Path(__file__).resolve().parents[1]
for _path in (REPO, REPO / "src", REPO / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from memecoin_research_storage import writer_guard  # noqa: E402
from tools import brain_senses_external_archival_projection_v1 as archive_projection  # noqa: E402
from tools import brain_senses_external_launch_v2 as launch  # noqa: E402
from tools import brain_senses_external_orchestrator_v2 as engine  # noqa: E402
from tools import brain_senses_external_partial_structural_queue_v2 as partial_queue  # noqa: E402
from tools import brain_senses_external_schema_v2 as lease_schema  # noqa: E402
from tools import brain_senses_external_structural_family_triage_v1 as family_triage  # noqa: E402
from tools import (  # noqa: E402
    brain_senses_external_structural_input_schema_v1 as structural_schema,  # noqa: E402
)
from tools import brain_senses_external_structural_publish_v1 as structural_publish  # noqa: E402
from tools import brain_senses_external_verify_v1 as predecessor_verify  # noqa: E402
from tools import brain_senses_external_verify_v2 as source_verify  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402

SERVICE_ID = family_triage.SERVICE_ID
STORAGE_CONFIG = family_triage.STORAGE_CONFIG
ACTIVATION = family_triage.ACTIVATION
FAILURE_RECEIPT = family_triage.FAILURE_RECEIPT
FINAL_STATUS_RECEIPT = family_triage.FINAL_STATUS_RECEIPT
QUEUE_RELATIVE = family_triage.QUEUE_RELATIVE
QUEUE_RECEIPT_RELATIVE = family_triage.QUEUE_RECEIPT_RELATIVE
ARCHIVE_RELATIVE = family_triage.ARCHIVE_RELATIVE
ARCHIVE_RECEIPT_RELATIVE = family_triage.ARCHIVE_RECEIPT_RELATIVE
FAMILY_RELATIVE = family_triage.OUTPUT_RELATIVE
FAMILY_RECEIPT_RELATIVE = family_triage.OUTPUT_RECEIPT_RELATIVE
OUTPUT_RELATIVE = (
    "derived/external-v2-sample-unknown-review-queue-v1/"
    "external-v2-20261012T0600Z/diagnostic-queue.json"
)
OUTPUT_RECEIPT_RELATIVE = (
    "derived/external-v2-sample-unknown-review-queue-v1/"
    "external-v2-20261012T0600Z/publication-receipt.json"
)
OUTPUT_MAX_BYTES = 32_000_000
FAMILY_MAX_BYTES = 32_000_000
SCHEMA = "external-v2-sample-local-unknown-review-queue-v1"
RECEIPT_SCHEMA = "external-v2-sample-local-unknown-review-queue-publication-v1"
EXPECTED_FAMILY_SHA256 = "c5a83e4e65eb5227e7c2da16de4efb0a8365d21f6b008674f81ba04674b60ee0"
EXPECTED_FAMILY_RECEIPT_SHA256 = "ae6beb74e07035d00a289bd2b2eaa0c8eab5499da5527c7330ce5c9aebc51f02"
EXPECTED_REFERENCE_COUNTS = {"success": 4_176, "failed": 689, "unknown": 0}
EXPECTED_QUEUE_DENOMINATORS = {
    "scheduled_frames": 12,
    "sealed_frames": 3,
    "unobserved_frames": 9,
    "produced_slots_in_sealed_frames": 4_164,
    "selected_blocks_in_sealed_frames": 48,
    "scanned_transactions": 46_222,
    "success_transactions": 43_636,
    "failed_transactions": 2_586,
    "unknown_transactions": 0,
    "reference_occurrences": 4_865,
    "class_count": 4_595,
    "unknown_scan_transactions": 0,
}
DIAGNOSTIC_CLASSES = frozenset(
    {
        "observed_positive_reference",
        "documented_IDL_shape_semantics_unverified",
        "unmatched_or_ambiguous_shape",
        "explicit_parse_source_failure",
    }
)


class ReviewQueueError(ValueError):
    """Frozen source evidence or joins violate the sample-local queue contract."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ReviewQueueError(code)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _canonical_sha(value: Any) -> str:
    return _sha(_canonical(value))


def _strict_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _identity(ref: Mapping[str, Any]) -> tuple[str, str, int, int | None]:
    path = ref.get("instruction_path")
    _require(isinstance(path, dict), "instruction_path_object")
    path = cast(dict[str, Any], path)
    outer, inner = path.get("outer_index"), path.get("inner_index")
    _require(_strict_int(outer), "instruction_path_outer")
    _require(inner is None or _strict_int(inner), "instruction_path_inner")
    capture_id, signature = ref.get("capture_id"), ref.get("signature_sha256")
    _require(isinstance(capture_id, str) and bool(capture_id), "capture_id")
    _require(
        structural_schema.require_digest(signature, "signature_sha") == signature, "signature_sha"
    )
    return cast(str, capture_id), cast(str, signature), cast(int, outer), cast(int | None, inner)


def _reference_hash(ref: Mapping[str, Any]) -> str:
    path = ref.get("instruction_path")
    _require(isinstance(path, dict), "instruction_path_object")
    path = cast(dict[str, Any], path)
    identity = {
        "capture_id": ref.get("capture_id"),
        "signature_sha256": ref.get("signature_sha256"),
        "instruction_path": {
            "outer_index": path.get("outer_index"),
            "inner_index": path.get("inner_index"),
        },
    }
    return cast(str, structural_schema.digest(structural_schema.canonical(identity)))


def _identity_hash(ref: Mapping[str, Any]) -> str:
    return cast(
        str,
        structural_schema.digest(structural_schema.canonical(_reference_identity(ref))),
    )


def validate_and_build(
    *,
    queue: Mapping[str, Any],
    archive: Mapping[str, Any],
    family_artifact: Mapping[str, Any],
    projection_known_ns: int,
    expected_reference_counts: Mapping[str, int] = EXPECTED_REFERENCE_COUNTS,
    expected_queue_denominators: Mapping[str, int] = EXPECTED_QUEUE_DENOMINATORS,
    expected_reference_count: int = 4_865,
    expected_family_count: int = 68,
) -> dict[str, Any]:
    """Reconcile family rows to the frozen queue and emit bounded review rows."""
    _require(_strict_int(projection_known_ns), "projection_known_time")
    queue_scope = queue.get("scope")
    _require(isinstance(queue_scope, dict), "queue_scope")
    queue_scope = cast(dict[str, Any], queue_scope)
    _require(queue_scope.get("plan_sha256") == family_triage.EXPECTED_PLAN_SHA256, "queue_plan")
    _require(queue.get("denominators") == dict(expected_queue_denominators), "queue_denominators")

    schedule = archive.get("schedule")
    family_schedule = family_artifact.get("schedule")
    _require(isinstance(schedule, list) and len(schedule) == 12, "archive_schedule")
    _require(family_schedule == schedule, "family_schedule_binding")
    _validate_schedule(cast(list[dict[str, Any]], schedule))

    observed = queue.get("observed")
    _require(isinstance(observed, dict), "queue_observed")
    observed = cast(dict[str, Any], observed)
    source_refs = observed.get("references")
    _require(isinstance(source_refs, list), "queue_references")
    source_index: dict[tuple[str, str, int, int | None], dict[str, Any]] = {}
    for item in cast(list[dict[str, Any]], source_refs):
        key = _identity(item)
        _require(key not in source_index, "duplicate_queue_reference")
        _require(item.get("reference_id") == _reference_hash(item), "queue_reference_id")
        source_index[key] = item

    archive_refs = archive.get("references")
    family_refs = family_artifact.get("references")
    family_rows = family_artifact.get("families")
    _require(isinstance(archive_refs, list), "archive_references")
    _require(isinstance(family_refs, list), "family_references")
    _require(isinstance(family_rows, list), "family_rows")
    _require(len(source_index) == expected_reference_count, "queue_reference_count")
    _require(len(cast(list[Any], archive_refs)) == len(source_index), "archive_reference_count")
    _require(len(cast(list[Any], family_refs)) == len(source_index), "family_reference_count")

    archive_index: dict[tuple[str, str, int, int | None], dict[str, Any]] = {}
    for item in cast(list[dict[str, Any]], archive_refs):
        key = _identity(item)
        _require(key not in archive_index, "duplicate_archive_reference")
        archive_index[key] = item
    family_index: dict[tuple[str, str, int, int | None], dict[str, Any]] = {}
    for item in cast(list[dict[str, Any]], family_refs):
        key = _identity(item)
        _require(key not in family_index, "duplicate_family_reference")
        family_index[key] = item
    _require(set(source_index) == set(archive_index) == set(family_index), "reference_identity_set")

    families_by_id: dict[str, dict[str, Any]] = {}
    family_counts: dict[str, Counter[str]] = defaultdict(Counter)
    family_first: dict[str, tuple[int, str]] = {}
    rows_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    refs_status: Counter[str] = Counter()
    for key in sorted(
        source_index, key=lambda key: (key[0], key[1], key[2], -1 if key[3] is None else key[3])
    ):
        source = source_index[key]
        archive_ref = archive_index[key]
        family_ref = family_index[key]
        for field in (
            "reference_id",
            "capture_id",
            "signature_sha256",
            "instruction_path",
            "program_id",
            "transaction_status",
            "bucket",
            "slot",
            "blockhash",
            "capture_receipt_sha256",
            "response_sha256",
            "request_sha256",
            "result_sha256",
            "instruction_data_sha256",
            "account_count",
            "class_fingerprint_schema",
            "class_fingerprint_sha256",
            "source_event_time_ns",
            "received_ns",
            "available_ns",
        ):
            archive_field = "capture_available_ns" if field == "available_ns" else field
            _require(
                source.get(field) == archive_ref.get(archive_field),
                f"archive_{archive_field}_mismatch",
            )
            projected_field = "capture_available_ns" if field == "available_ns" else field
            if projected_field in family_ref:
                _require(
                    family_ref.get(projected_field) == source.get(field),
                    f"family_{projected_field}_mismatch",
                )
        _require(
            family_ref.get("source_reconciliation") == "exact_capture_signature_typed_path_match",
            "family_reconciliation",
        )
        _require(
            family_ref.get("reference_identity_sha256") == _identity_hash(source),
            "family_reference_identity_hash",
        )
        _require(family_ref.get("decision_ns") is None, "family_decision_time")
        status = source.get("transaction_status")
        _require(status in expected_reference_counts, "reference_status")
        refs_status[cast(str, status)] += 1
        event_ns = source.get("source_event_time_ns")
        received_ns = source.get("received_ns")
        available_ns = source.get("available_ns")
        _require(_strict_int(received_ns) and _strict_int(available_ns), "source_time_fields")
        _require(cast(int, received_ns) <= cast(int, available_ns), "received_after_available")
        _require(
            event_ns is None
            or (_strict_int(event_ns) and cast(int, event_ns) <= cast(int, received_ns)),
            "event_after_receipt",
        )
        family_id = family_ref.get("family_id")
        _require(isinstance(family_id, str), "family_id")
        family_id = cast(str, family_id)
        family_counts[family_id][cast(str, status)] += 1
        capture_available = source.get("available_ns")
        _require(_strict_int(capture_available), "capture_available_time")
        sample_order = (
            cast(int, capture_available),
            cast(str, family_ref["reference_identity_sha256"]),
        )
        if family_id not in family_first or sample_order < family_first[family_id]:
            family_first[family_id] = sample_order
        rows_by_family[family_id].append({"source": source, "family": family_ref})
    _require(
        {state: refs_status[state] for state in ("success", "failed", "unknown")}
        == dict(expected_reference_counts),
        "reference_status_denominators",
    )

    for row in cast(list[dict[str, Any]], family_rows):
        family_id = row.get("family_id")
        _require(
            isinstance(family_id, str) and family_id not in families_by_id, "duplicate_family_id"
        )
        families_by_id[cast(str, family_id)] = row
    _require(set(rows_by_family) == set(families_by_id), "family_id_set")
    _require(len(families_by_id) == expected_family_count, "family_count")
    for family_id, refs in rows_by_family.items():
        family_row = families_by_id[family_id]
        _require(family_row.get("reference_occurrences") == len(refs), "family_occurrence_count")
        _require(
            family_row.get("transaction_status_counts")
            == {
                state: family_counts[family_id][state] for state in ("success", "failed", "unknown")
            },
            "family_status_counts",
        )
        for pair in refs:
            family_ref = pair["family"]
            _require(
                family_ref.get("family_id") == family_row.get("family_id"),
                "family_reference_assignment",
            )
            _require(
                family_ref.get("family_id")
                == structural_schema.digest(
                    structural_schema.canonical(family_row.get("family_key"))
                ),
                "family_key_hash",
            )
            _require(
                family_ref.get("idl_match_classification")
                == family_row.get("idl_match_classification")
                and family_ref.get("documented_idl_name") == family_row.get("documented_idl_name"),
                "family_idl_assignment",
            )

    max_known = max(
        cast(int, ref["source"]["available_ns"])
        for family in rows_by_family.values()
        for ref in family
    )
    family_known = family_artifact.get("scope", {}).get("projection_known_ns")
    _require(_strict_int(family_known), "family_projection_time")
    _require(
        cast(int, family_known) > max_known and projection_known_ns > cast(int, family_known),
        "projection_time_order",
    )

    first_ids = {family_id: first_order[1] for family_id, first_order in family_first.items()}
    review_rows: list[dict[str, Any]] = []
    for key in sorted(
        source_index, key=lambda key: (key[0], key[1], key[2], -1 if key[3] is None else key[3])
    ):
        source = source_index[key]
        family_ref = family_index[key]
        family_id = cast(str, family_ref["family_id"])
        family = families_by_id[family_id]
        idl_class = family.get("idl_match_classification")
        if idl_class == "documented_idl_name_match_sample_only":
            shape_class = "documented_IDL_shape_semantics_unverified"
        elif isinstance(idl_class, str) and idl_class.startswith("unresolved_"):
            shape_class = "unmatched_or_ambiguous_shape"
        else:
            raise ReviewQueueError("family_idl_classification")
        available = cast(int, source["available_ns"])
        identity_sha = _identity_hash(source)
        review_rows.append(
            {
                "record_type": "external_v2_sample_local_structural_reference_review",
                "diagnostic_classes": ["observed_positive_reference", shape_class],
                "claim_scope": "sample_local_structural_reference_diagnostic",
                "network": "solana-mainnet-beta",
                "process_version": "external-v2-partial-structural-queue-v2",
                "service_id": SERVICE_ID,
                "bucket": source["bucket"],
                "slot": source["slot"],
                "blockhash": source["blockhash"],
                "reference_id": source["reference_id"],
                "reference_identity_sha256": identity_sha,
                "capture_id": source["capture_id"],
                "signature_sha256": source["signature_sha256"],
                "instruction_path": dict(source["instruction_path"]),
                "program_id": source["program_id"],
                "instruction_data_sha256": source["instruction_data_sha256"],
                "raw_prefix_hex": family_ref["raw_prefix_hex"],
                "raw_prefix_width": family_ref["raw_prefix_width"],
                "instruction_data_length_bytes": family_ref["instruction_data_length_bytes"],
                "account_count": source["account_count"],
                "class_fingerprint_schema": source["class_fingerprint_schema"],
                "class_fingerprint_sha256": source["class_fingerprint_sha256"],
                "family_id": family_id,
                "transaction_status": source["transaction_status"],
                "capture_receipt_sha256": source["capture_receipt_sha256"],
                "response_sha256": source["response_sha256"],
                "request_sha256": source["request_sha256"],
                "result_sha256": source["result_sha256"],
                "documented_idl_name": family["documented_idl_name"],
                "idl_match_classification": idl_class,
                "source_event_time_ns": source["source_event_time_ns"],
                "received_ns": source["received_ns"],
                "capture_available_ns": available,
                "family_projection_known_ns": family_known,
                "projection_known_ns": projection_known_ns,
                "first_observed_in_this_sample": identity_sha == first_ids[family_id],
                "decision_ns": None,
                "executed_instruction": None,
                "instruction_effect": None,
                "settlement": None,
            }
        )
    _require(len(review_rows) == expected_reference_count, "review_row_count")
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "status": "sample_local_diagnostic_review_queue",
        "scope": {
            "network": "solana-mainnet-beta",
            "process_version": "external-v2-partial-structural-queue-v2",
            "service_id": SERVICE_ID,
            "sample_only": True,
            "sample_local_first_observation_rule": (
                "earliest capture availability, then canonical reference identity "
                "hash within family"
            ),
            "projection_known_ns": projection_known_ns,
        },
        "source_binding": {},
        "schedule": schedule,
        "denominators": {
            **dict(expected_queue_denominators),
            "reference_status_counts": dict(expected_reference_counts),
            "family_count": len(families_by_id),
            "unobserved_state_counts": {
                "callback_failed_unobserved": 1,
                "expired_unobserved": 8,
            },
            "diagnostic_class_counts": {
                "observed_positive_reference": len(review_rows),
                "documented_IDL_shape_semantics_unverified": sum(
                    "documented_IDL_shape_semantics_unverified" in row["diagnostic_classes"]
                    for row in review_rows
                ),
                "unmatched_or_ambiguous_shape": sum(
                    "unmatched_or_ambiguous_shape" in row["diagnostic_classes"]
                    for row in review_rows
                ),
                "explicit_parse_source_failure": 0,
            },
        },
        "entries": review_rows,
        "parse_source_failure_entries": [],
        "readiness": {
            "sample_local_diagnostic_queue_ready": True,
            "anomaly_source_qualified": False,
            "decision_ns": None,
        },
        "nonclaims": {
            "zero_or_absence": False,
            "no_new_class": False,
            "anomaly_rate": False,
            "economic_or_wallet_claim": False,
            "execution_or_effect_claim": False,
            "settlement_claim": False,
            "point_in_time_support": False,
            "training_population_evaluation_admission": False,
            "activation_or_live_monitor": False,
        },
    }


def _reference_identity(ref: Mapping[str, Any]) -> dict[str, Any]:
    path = cast(dict[str, Any], ref["instruction_path"])
    return {
        "capture_id": ref["capture_id"],
        "signature_sha256": ref["signature_sha256"],
        "instruction_path": {
            "outer_index": path["outer_index"],
            "inner_index": path["inner_index"],
        },
    }


def _validate_schedule(rows: list[dict[str, Any]]) -> None:
    _require(len(rows) == 12, "schedule_count")
    _require(sum(row.get("state") == "sealed" for row in rows) == 3, "sealed_frames")
    _require(
        sum(row.get("state") == "callback_failed_unobserved" for row in rows) == 1,
        "callback_failure",
    )
    _require(sum(row.get("state") == "expired_unobserved" for row in rows) == 8, "expired_frames")
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
        if row.get("state") in {"sealed"}:
            continue
        counts = row.get("counts")
        _require(isinstance(counts, dict), "unobserved_counts")
        counts = cast(dict[str, Any], counts)
        _require(all(counts.get(name) is None for name in metrics), "unobserved_metrics_not_null")


def _load_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewQueueError(f"{label}_json") from exc
    _require(isinstance(value, dict), f"{label}_object")
    return cast(dict[str, Any], value)


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
                raise ReviewQueueError("storage_root_identity_or_mode")
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
        raise ReviewQueueError("incomplete_publication_pair")
    return "create"


def _build_publication_receipt(artifact_bytes: bytes) -> dict[str, Any]:
    artifact = _load_json(artifact_bytes, "output_artifact")
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "schema_version": 1,
        "status": "created_or_verified",
        "artifact_path": OUTPUT_RELATIVE,
        "artifact_sha256": _sha(artifact_bytes),
        "source_binding_sha256": _canonical_sha(artifact["source_binding"]),
        "projection_known_ns": artifact["scope"]["projection_known_ns"],
        "provider_io": False,
        "point_in_time_eligible": False,
    }
    receipt["content_sha256"] = _canonical_sha(receipt)
    return receipt


def _verify_existing_publication(
    artifact_bytes: bytes,
    receipt_bytes: bytes,
    *,
    expected_entry_count: int = 4_865,
) -> dict[str, Any]:
    artifact = _load_json(artifact_bytes, "existing_artifact")
    receipt = _load_json(receipt_bytes, "existing_receipt")
    _require(_canonical(artifact) == artifact_bytes, "existing_artifact_canonical")
    _require(_canonical(receipt) == receipt_bytes, "existing_receipt_canonical")
    _require(
        artifact.get("schema") == SCHEMA
        and artifact.get("status") == "sample_local_diagnostic_review_queue",
        "existing_artifact_schema",
    )
    _require(
        receipt.get("schema") == RECEIPT_SCHEMA
        and receipt.get("schema_version") == 1
        and receipt.get("status") == "created_or_verified",
        "existing_receipt_schema",
    )
    _require(receipt.get("artifact_path") == OUTPUT_RELATIVE, "existing_receipt_path")
    _require(receipt.get("artifact_sha256") == _sha(artifact_bytes), "existing_artifact_hash")
    _require(
        receipt.get("source_binding_sha256") == _canonical_sha(artifact.get("source_binding")),
        "existing_source_binding_hash",
    )
    _require(
        receipt.get("projection_known_ns") == artifact.get("scope", {}).get("projection_known_ns"),
        "existing_projection_time_binding",
    )
    entries = artifact.get("entries")
    _require(
        isinstance(entries, list) and len(entries) == expected_entry_count,
        "existing_entry_count",
    )
    entry_rows = cast(list[dict[str, Any]], entries)
    identities = [_identity(row) for row in entry_rows]
    _require(len(set(identities)) == len(identities), "existing_duplicate_entry")
    _require(
        all(row.get("decision_ns") is None for row in entry_rows),
        "existing_entry_decision_time",
    )
    max_available = max(cast(int, row["capture_available_ns"]) for row in entry_rows)
    max_family_known = max(cast(int, row["family_projection_known_ns"]) for row in entry_rows)
    projection_known = receipt.get("projection_known_ns")
    _require(
        _strict_int(projection_known)
        and cast(int, projection_known) > max_available
        and cast(int, projection_known) > max_family_known,
        "existing_projection_time_order",
    )
    readiness = artifact.get("readiness")
    _require(
        isinstance(readiness, dict)
        and readiness.get("sample_local_diagnostic_queue_ready") is True
        and readiness.get("anomaly_source_qualified") is False
        and readiness.get("decision_ns") is None,
        "existing_readiness_boundary",
    )
    _require(
        receipt.get("provider_io") is False and receipt.get("point_in_time_eligible") is False,
        "existing_receipt_claims",
    )
    receipt_body = dict(receipt)
    content_hash = receipt_body.pop("content_sha256", None)
    _require(content_hash == _canonical_sha(receipt_body), "existing_receipt_content_hash")
    return {**receipt, "publication_receipt_sha256": _sha(receipt_bytes)}


def publish_create_only(
    artifact: Mapping[str, Any], *, config_path: Path = STORAGE_CONFIG
) -> dict[str, Any]:
    """Descriptor-bound create-only publisher with immutable replay verification."""
    artifact_bytes = _canonical(artifact)
    _require(len(artifact_bytes) <= OUTPUT_MAX_BYTES, "output_size_cap")
    receipt_bytes = _canonical(_build_publication_receipt(artifact_bytes))
    with writer_guard(config_path) as guard:
        _require(
            guard.root_id == archive_projection.STORAGE_ROOT_ID
            and guard.volume_uuid.lower() == archive_projection.STORAGE_VOLUME_UUID,
            "output_storage_identity",
        )
        guard.create_directory(OUTPUT_RELATIVE.rsplit("/", 1)[0])
        root_fd = _open_data_root(guard)
        try:

            def before_write() -> None:
                guard.revalidate()

            with DescriptorWriter(root_fd, before_write=before_write) as writer:
                action = _publication_action(
                    writer.exists(OUTPUT_RELATIVE), writer.exists(OUTPUT_RECEIPT_RELATIVE)
                )
                if action == "verify_existing":
                    existing_artifact = writer.read_bytes(
                        OUTPUT_RELATIVE, max_bytes=OUTPUT_MAX_BYTES
                    )
                    prior = _verify_existing_publication(
                        existing_artifact,
                        writer.read_bytes(OUTPUT_RECEIPT_RELATIVE, max_bytes=64_000),
                    )
                    _require(existing_artifact == artifact_bytes, "existing_candidate_mismatch")
                    guard.revalidate()
                    return prior
                writer.create(OUTPUT_RELATIVE, artifact_bytes)
                writer.create(OUTPUT_RECEIPT_RELATIVE, receipt_bytes)
        finally:
            os.close(root_fd)
        guard.revalidate()
    return {
        **_load_json(receipt_bytes, "new_receipt"),
        "publication_receipt_sha256": _sha(receipt_bytes),
        "bytes": len(artifact_bytes),
    }


def _read_inputs(config_path: Path) -> tuple[bytes, bytes, bytes, bytes, bytes, bytes]:
    with writer_guard(config_path) as guard:
        _require(
            guard.root_id == archive_projection.STORAGE_ROOT_ID
            and guard.volume_uuid.lower() == archive_projection.STORAGE_VOLUME_UUID,
            "input_storage_identity",
        )
        root_fd = _open_data_root(guard)
        try:
            with DescriptorWriter(root_fd) as reader:
                return (
                    reader.read_bytes(
                        QUEUE_RELATIVE, max_bytes=family_triage.EXPECTED_QUEUE_BYTES + 1
                    ),
                    reader.read_bytes(QUEUE_RECEIPT_RELATIVE, max_bytes=64_000),
                    reader.read_bytes(ARCHIVE_RELATIVE, max_bytes=OUTPUT_MAX_BYTES),
                    reader.read_bytes(ARCHIVE_RECEIPT_RELATIVE, max_bytes=64_000),
                    reader.read_bytes(FAMILY_RELATIVE, max_bytes=FAMILY_MAX_BYTES),
                    reader.read_bytes(FAMILY_RECEIPT_RELATIVE, max_bytes=64_000),
                )
        finally:
            os.close(root_fd)


def _verify_source_artifacts(
    queue_bytes: bytes,
    queue_receipt_bytes: bytes,
    archive_bytes: bytes,
    archive_receipt_bytes: bytes,
    family_bytes: bytes,
    family_receipt_bytes: bytes,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    _require(_sha(queue_bytes) == family_triage.EXPECTED_QUEUE_SHA256, "queue_hash")
    _require(
        _sha(queue_receipt_bytes) == family_triage.EXPECTED_QUEUE_RECEIPT_SHA256,
        "queue_receipt_hash",
    )
    _require(_sha(archive_bytes) == family_triage.EXPECTED_ARCHIVE_SHA256, "archive_hash")
    _require(
        _sha(archive_receipt_bytes) == family_triage.EXPECTED_ARCHIVE_RECEIPT_SHA256,
        "archive_receipt_hash",
    )
    _require(_sha(family_bytes) == EXPECTED_FAMILY_SHA256, "family_artifact_hash")
    _require(_sha(family_receipt_bytes) == EXPECTED_FAMILY_RECEIPT_SHA256, "family_receipt_hash")
    queue = _load_json(queue_bytes, "queue")
    queue_receipt = _load_json(queue_receipt_bytes, "queue_receipt")
    archive = _load_json(archive_bytes, "archive")
    family = _load_json(family_bytes, "family")
    family_receipt = _load_json(family_receipt_bytes, "family_receipt")
    _require(structural_schema.canonical(queue) == queue_bytes, "queue_canonical")
    _require(_canonical(archive) == archive_bytes, "archive_canonical")
    _require(_canonical(family) == family_bytes, "family_canonical")
    _require(
        queue_receipt.get("artifact_sha256") == _sha(queue_bytes), "queue_receipt_artifact_hash"
    )
    _require(queue_receipt.get("artifact_path") == QUEUE_RELATIVE, "queue_receipt_path")
    _require(
        queue_receipt.get("availability_ns") == archive_projection.INPUT_RECEIPT_NS,
        "queue_receipt_time",
    )
    archive_projection._verify_existing_publication(
        archive_bytes,
        archive_receipt_bytes,
        input_sha256=family_triage.EXPECTED_QUEUE_SHA256,
        input_receipt_sha256=family_triage.EXPECTED_QUEUE_RECEIPT_SHA256,
    )
    family_binding = family.get("source_binding")
    _require(isinstance(family_binding, dict), "family_source_binding")
    family_binding = cast(dict[str, Any], family_binding)
    family_triage._verify_existing_publication(
        family_bytes,
        family_receipt_bytes,
        expected_source_hashes=family_binding.get("source_code_sha256", {}),
        expected_idl_blobs=family_binding.get("idl_blobs", []),
    )
    observed = queue.get("observed")
    _require(isinstance(observed, dict), "queue_observed")
    queue_references = cast(dict[str, Any], observed).get("references")
    archive_classes = archive.get("classes")
    _require(isinstance(queue_references, list), "queue_references")
    _require(isinstance(archive_classes, list), "archive_classes")
    family_triage._validate_class_reconciliation(
        cast(list[dict[str, Any]], queue_references),
        cast(list[dict[str, Any]], archive_classes),
    )
    _require(
        family_receipt.get("artifact_sha256") == _sha(family_bytes), "family_receipt_artifact_hash"
    )
    _require(
        family.get("source_binding", {}).get("queue_artifact_sha256") == _sha(queue_bytes),
        "family_queue_binding",
    )
    _require(
        family.get("source_binding", {}).get("archive_projection_sha256") == _sha(archive_bytes),
        "family_archive_binding",
    )
    _require(
        family.get("source_binding", {}).get("queue_publication_receipt_sha256")
        == _sha(queue_receipt_bytes),
        "family_queue_receipt_binding",
    )
    _require(
        family.get("source_binding", {}).get("archive_projection_receipt_sha256")
        == _sha(archive_receipt_bytes),
        "family_archive_receipt_binding",
    )
    return queue, archive, family


def _source_hashes() -> dict[str, str]:
    paths = {
        "tools/brain_senses_external_sample_unknown_review_queue_v1.py",
        "tools/brain_senses_external_structural_family_triage_v1.py",
        *engine.source_bindings().keys(),
    }
    return {path: _sha((REPO / path).read_bytes()) for path in sorted(paths)}


def _build_candidate(
    *, config_path: Path = STORAGE_CONFIG, projection_known_ns: int | None = None
) -> dict[str, Any]:
    """Reverify frozen inputs and construct the candidate without publishing it."""
    source_bytes = _read_inputs(config_path)
    queue, archive, family = _verify_source_artifacts(*source_bytes)
    activation, plan = launch.activation_gate(ACTIVATION, config_path, running=False)
    _require(activation.get("plan_sha256") == family_triage.EXPECTED_PLAN_SHA256, "activation_plan")
    sources = engine.source_bindings()
    _require(plan.get("code_sha256") == sources, "plan_source_hashes")
    with (
        launch.host_lock(),
        writer_guard(config_path, min_free_bytes=partial_queue.MIN_FREE_BYTES) as guard,
    ):
        guard.revalidate(min_free_bytes=partial_queue.MIN_FREE_BYTES)
        _require(guard.root_id == plan["storage"]["root_id"], "source_root_id")
        _require(
            guard.volume_uuid.lower() == plan["storage"]["volume_uuid"].lower(),
            "source_volume_uuid",
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
                                structural_schema.canonical(rebuilt) == source_bytes[0],
                                "recompiled_queue_mismatch",
                            )
                            observed = cast(dict[str, Any], rebuilt["observed"])
                            source_refs = cast(list[dict[str, Any]], observed["references"])
                            _require(
                                source_refs == queue["observed"]["references"],
                                "rebuilt_reference_rows",
                            )
                            max_available = cast(int, rebuilt["maximum_source_availability_ns"])
        finally:
            os.close(source_fd)
            os.close(predecessor_fd)
    if projection_known_ns is None:
        projection_known_ns = time.time_ns()
    family_known = cast(int, family["scope"]["projection_known_ns"])
    _require(
        projection_known_ns > family_known and projection_known_ns > max_available,
        "projection_time_order",
    )
    output = validate_and_build(
        queue=queue,
        archive=archive,
        family_artifact=family,
        projection_known_ns=projection_known_ns,
    )
    output["source_binding"] = {
        "queue_artifact_sha256": _sha(source_bytes[0]),
        "queue_publication_receipt_sha256": _sha(source_bytes[1]),
        "archive_projection_sha256": _sha(source_bytes[2]),
        "archive_projection_receipt_sha256": _sha(source_bytes[3]),
        "family_triage_sha256": _sha(source_bytes[4]),
        "family_triage_receipt_sha256": _sha(source_bytes[5]),
        "source_code_sha256": _source_hashes(),
        "plan_sha256": family_triage.EXPECTED_PLAN_SHA256,
        "terminal_expiry_receipt_sha256": _sha(FINAL_STATUS_RECEIPT.read_bytes()),
        "selected_capture_hashes": [
            {
                "capture_id": row["capture_id"],
                "bucket": row["bucket"],
                "slot": row["slot"],
                "blockhash": row["blockhash"],
                "capture_receipt_sha256": row["capture_receipt_sha256"],
                "response_sha256": row["response_sha256"],
            }
            for row in queue["observed"]["captures"]
        ],
    }
    artifact_bytes = _canonical(output)
    _require(len(artifact_bytes) <= OUTPUT_MAX_BYTES, "output_size_cap")
    return output


def build_candidate(*, config_path: Path = STORAGE_CONFIG) -> dict[str, Any]:
    """Build a source-verified in-memory artifact using the current trusted clock."""
    return _build_candidate(config_path=config_path)


def _preview_summary(candidate: Mapping[str, Any]) -> dict[str, Any]:
    artifact_bytes = _canonical(candidate)
    return {
        "status": "verified_not_published",
        "artifact_sha256": _sha(artifact_bytes),
        "artifact_bytes": len(artifact_bytes),
        "projection_known_ns": candidate["scope"]["projection_known_ns"],
        "denominators": candidate["denominators"],
        "readiness": candidate["readiness"],
        "source_binding": candidate["source_binding"],
    }


def _read_existing_publication_pair(*, config_path: Path) -> tuple[bytes, bytes] | None:
    """Read and validate a complete prior pair; never repairs a partial pair."""
    with writer_guard(config_path) as guard:
        _require(
            guard.root_id == archive_projection.STORAGE_ROOT_ID
            and guard.volume_uuid.lower() == archive_projection.STORAGE_VOLUME_UUID,
            "output_storage_identity",
        )
        root_fd = _open_data_root(guard)
        try:
            with DescriptorWriter(root_fd) as reader:
                artifact_exists = reader.exists(OUTPUT_RELATIVE)
                receipt_exists = reader.exists(OUTPUT_RECEIPT_RELATIVE)
                action = _publication_action(artifact_exists, receipt_exists)
                if action == "create":
                    return None
                artifact_bytes = reader.read_bytes(OUTPUT_RELATIVE, max_bytes=OUTPUT_MAX_BYTES)
                receipt_bytes = reader.read_bytes(OUTPUT_RECEIPT_RELATIVE, max_bytes=64_000)
                _verify_existing_publication(artifact_bytes, receipt_bytes)
                return artifact_bytes, receipt_bytes
        finally:
            os.close(root_fd)


def run_verify_only(*, config_path: Path = STORAGE_CONFIG) -> dict[str, Any]:
    """Run the full source verification and return a non-publishing preview."""
    return _preview_summary(build_candidate(config_path=config_path))


def run_publish(*, config_path: Path = STORAGE_CONFIG) -> dict[str, Any]:
    """Reverify sources, replay an existing timestamp exactly, then publish create-only."""
    existing = _read_existing_publication_pair(config_path=config_path)
    if existing is None:
        candidate = build_candidate(config_path=config_path)
    else:
        existing_artifact, _ = existing
        prior = _load_json(existing_artifact, "existing_artifact")
        projection_known_ns = prior.get("scope", {}).get("projection_known_ns")
        _require(_strict_int(projection_known_ns), "existing_projection_time")
        candidate = _build_candidate(
            config_path=config_path,
            projection_known_ns=cast(int, projection_known_ns),
        )
        _require(
            _canonical(candidate) == existing_artifact, "recomputed_existing_artifact_mismatch"
        )
    return publish_create_only(candidate, config_path=config_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--verify-only", action="store_true")
    modes.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    try:
        result = run_publish() if args.publish else run_verify_only()
    except (ReviewQueueError, OSError, ValueError, RuntimeError) as exc:
        print(f"sample-local review queue failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
