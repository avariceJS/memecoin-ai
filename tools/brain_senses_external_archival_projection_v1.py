#!/usr/bin/env python3
"""Project the frozen partial queue as sample-only structural-reference evidence.

This tool deliberately does not decode transaction effects, infer execution for
failed transactions, join economic/lifecycle facts, or fill the nine unobserved
scheduled windows. Its publisher is descriptor-bound and create-only; this
first implementation pass must not be run on the Kingston cohort until review.
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
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from memecoin_research_storage import writer_guard  # noqa: E402
from tools import brain_senses_external_structural_input_schema_v1 as source_schema  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402

INPUT_ROOT = Path(
    "/Volumes/Kingston/BrainData/derived/partial-structural-queue-v2/external-v2-20261012T0600Z"
)
INPUT_PATH = INPUT_ROOT / "partial-queue.json"
INPUT_RECEIPT_PATH = INPUT_ROOT / "publication-receipt.json"
INPUT_RELATIVE = "derived/partial-structural-queue-v2/external-v2-20261012T0600Z/partial-queue.json"
INPUT_RECEIPT_RELATIVE = (
    "derived/partial-structural-queue-v2/external-v2-20261012T0600Z/publication-receipt.json"
)
INPUT_SHA256 = "fd8a2ffb750d53eb877e1326c02419358c24870aeb7338ea444a121712f83b81"
INPUT_BYTES = 9_378_849
INPUT_RECEIPT_SHA256 = "23ede32e11040280024afd7da06b657a353f445850a55aecf99b4d11fcc294f4"
INPUT_RECEIPT_NS = 1_791_789_947_251_897_000
OUTPUT_RELATIVE = (
    "derived/pump-amm-structural-archive-projection-v1/"
    "external-v2-20261012T0600Z/archival-projection.json"
)
OUTPUT_RECEIPT_RELATIVE = (
    "derived/pump-amm-structural-archive-projection-v1/"
    "external-v2-20261012T0600Z/publication-receipt.json"
)
STORAGE_CONFIG = Path.home() / "Developer/memecoin-runtime/brain/config/storage-v1.json"
STORAGE_ROOT_ID = "9428695af6d4f46c08e1a444cdc66de8"
STORAGE_VOLUME_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"
PUMP_AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
NETWORK = "solana-mainnet-beta"
PROCESS_VERSION = "external-v2-partial-structural-queue-v2"
CLASSIFICATION = "unresolved_unsupported_no_decoder_claim"
SCHEDULED_FRAMES = 12
SEALED_FRAMES = 3
UNOBSERVED_FRAMES = 9

EXPECTED_DENOMINATORS: dict[str, int] = {
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
EXPECTED_REFERENCE_STATUS_COUNTS = {"success": 4_176, "failed": 689, "unknown": 0}


class ProjectionError(ValueError):
    """Input evidence violates the fixed archival projection contract."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ProjectionError(code)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _canonical_sha256(value: Any) -> str:
    return _sha256(_canonical(value))


def _strict_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _class_key(reference: Mapping[str, Any]) -> tuple[str, str, int, str, str]:
    return (
        cast(str, reference["program_id"]),
        cast(str, reference["instruction_data_sha256"]),
        cast(int, reference["account_count"]),
        cast(str, reference["class_fingerprint_schema"]),
        cast(str, reference["class_fingerprint_sha256"]),
    )


def _check_input_receipt(
    receipt: Mapping[str, Any],
    *,
    input_sha256: str,
    input_bytes: int,
    receipt_sha256: str,
    expected_input_sha256: str | None,
    expected_input_bytes: int | None,
    expected_receipt_sha256: str | None,
    enforce_frozen_input: bool,
) -> int:
    _require(
        receipt.get("schema") == "brain-senses-external-partial-structural-queue-publication-v2",
        "receipt_schema",
    )
    _require(receipt.get("schema_version") == 1, "receipt_version")
    _require(receipt.get("status") == "created_or_verified", "receipt_status")
    _require(receipt.get("artifact_path") == INPUT_PATH_RELATIVE, "receipt_artifact_path")
    _require(receipt.get("artifact_sha256") == input_sha256, "receipt_artifact_hash")
    _require(_strict_int(receipt.get("availability_ns")), "receipt_availability_time")
    _require(receipt.get("provider_io") is False, "receipt_provider_io")
    _require(receipt.get("point_in_time_eligible") is False, "receipt_pit_claim")
    _require(receipt.get("source_root_id") == STORAGE_ROOT_ID, "receipt_root_id")
    _require(
        str(receipt.get("volume_uuid", "")).lower() == STORAGE_VOLUME_UUID, "receipt_volume_uuid"
    )
    if enforce_frozen_input:
        _require(input_bytes == expected_input_bytes, "input_byte_count")
        _require(input_sha256 == expected_input_sha256, "input_hash_pin")
        _require(receipt_sha256 == expected_receipt_sha256, "input_receipt_hash_pin")
    availability = cast(int, receipt["availability_ns"])
    _require(availability == INPUT_RECEIPT_NS, "input_receipt_availability_changed")
    _require(input_bytes > 0, "empty_input")
    return availability


INPUT_PATH_RELATIVE = (
    "derived/partial-structural-queue-v2/external-v2-20261012T0600Z/partial-queue.json"
)


def _validate_schedule(
    artifact: Mapping[str, Any], expected: Mapping[str, int]
) -> list[dict[str, Any]]:
    schedule = artifact.get("schedule")
    _require(isinstance(schedule, dict), "schedule_object")
    frames = schedule.get("frames")
    _require(isinstance(frames, list) and len(frames) == SCHEDULED_FRAMES, "schedule_denominator")
    rows = cast(list[dict[str, Any]], frames)
    buckets = [row.get("bucket") for row in rows]
    _require(all(isinstance(bucket, str) for bucket in buckets), "schedule_bucket_type")
    _require(len(set(buckets)) == len(buckets), "duplicate_schedule_bucket")
    sealed = [row for row in rows if row.get("state") == "sealed"]
    unobserved = [row for row in rows if row.get("state") != "sealed"]
    _require(
        len(sealed) == SEALED_FRAMES and len(unobserved) == UNOBSERVED_FRAMES,
        "schedule_state_counts",
    )
    _require(
        sum(row.get("state") == "callback_failed_unobserved" for row in unobserved) == 1,
        "callback_failure_denominator",
    )
    _require(
        sum(row.get("state") == "expired_unobserved" for row in unobserved) == 8,
        "expired_denominator",
    )
    nullable_metric_names = (
        "produced_slots",
        "selected_blocks",
        "scanned_transactions",
        "success_transactions",
        "failed_transactions",
        "unknown_transactions",
        "reference_occurrences",
        "unknown_scan_transactions",
    )
    for row in unobserved:
        for metric in nullable_metric_names:
            _require(row.get(metric) is None, "unobserved_metric_not_null")
    _require(len(sealed) == expected["sealed_frames"], "sealed_expected_denominator")
    return rows


def _class_summary(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int, str, str], Counter[str]] = defaultdict(Counter)
    for reference in references:
        grouped[_class_key(reference)][reference["transaction_status"]] += 1
    result: list[dict[str, Any]] = []
    for key, counts in grouped.items():
        program_id, data_sha, account_count, fingerprint_schema, fingerprint_sha = key
        result.append(
            {
                "program_id": program_id,
                "instruction_data_sha256": data_sha,
                "account_count": account_count,
                "class_fingerprint_schema": fingerprint_schema,
                "class_fingerprint_sha256": fingerprint_sha,
                "classification": CLASSIFICATION,
                "reference_occurrences": sum(counts.values()),
                "transaction_status_counts": {
                    status: counts[status] for status in ("success", "failed", "unknown")
                },
            }
        )
    return sorted(result, key=lambda row: _canonical(row))


def validate_and_project(
    source_bytes: bytes,
    receipt_bytes: bytes,
    *,
    projection_known_ns: int,
    expected_denominators: Mapping[str, int] | None = None,
    expected_reference_status_counts: Mapping[str, int] | None = EXPECTED_REFERENCE_STATUS_COUNTS,
    expected_input_sha256: str | None = INPUT_SHA256,
    expected_input_bytes: int | None = INPUT_BYTES,
    expected_receipt_sha256: str | None = INPUT_RECEIPT_SHA256,
    enforce_frozen_input: bool = True,
) -> dict[str, Any]:
    """Verify immutable input and emit structural-reference rows only.

    Unit tests supply compact fixture bytes and their own exact denominators.
    The publish CLI retains the frozen 9,378,849-byte input/hash pins.
    """
    input_sha256 = _sha256(source_bytes)
    receipt_sha256 = _sha256(receipt_bytes)
    try:
        source = json.loads(source_bytes)
        receipt = json.loads(receipt_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectionError("json_decode") from exc
    _require(isinstance(source, dict) and isinstance(receipt, dict), "input_objects")
    queue_receipt_ns = _check_input_receipt(
        receipt,
        input_sha256=input_sha256,
        input_bytes=len(source_bytes),
        receipt_sha256=receipt_sha256,
        expected_input_sha256=expected_input_sha256,
        expected_input_bytes=expected_input_bytes,
        expected_receipt_sha256=expected_receipt_sha256,
        enforce_frozen_input=enforce_frozen_input,
    )
    expected = expected_denominators or EXPECTED_DENOMINATORS
    _require(
        source.get("schema") == "brain-senses-external-partial-structural-queue-v2"
        and source.get("schema_version") == 1
        and source.get("status") == "partial_observed_prefix",
        "source_schema",
    )
    scope = source.get("scope")
    _require(isinstance(scope, dict), "source_scope")
    _require(scope.get("service_id") == "external-v2-20261012T0600Z", "service_id")
    denominators = source.get("denominators")
    _require(isinstance(denominators, dict), "source_denominators")
    for name, count in expected.items():
        _require(denominators.get(name) == count, f"denominator_{name}")
    _require(denominators.get("scheduled_frames") == SCHEDULED_FRAMES, "scheduled_frames")
    _require(denominators.get("sealed_frames") == SEALED_FRAMES, "sealed_frames")
    _require(denominators.get("unobserved_frames") == UNOBSERVED_FRAMES, "unobserved_frames")
    nonclaims = source.get("nonclaims")
    _require(isinstance(nonclaims, dict), "source_nonclaims")
    for claim in (
        "full_schedule_complete",
        "no_new_class_claim",
        "market_completeness",
        "point_in_time_eligible",
        "economic_admission",
        "successor_eligible",
    ):
        _require(nonclaims.get(claim) is False, "source_nonclaim_violated")
    receipt_hash_binding = {
        "source_artifact_sha256": input_sha256,
        "source_publication_receipt_sha256": receipt_sha256,
        "source_publication_receipt_ns": queue_receipt_ns,
        "service_id": scope["service_id"],
        "plan_sha256": scope.get("plan_sha256"),
        "process_version": PROCESS_VERSION,
    }
    _require(_strict_int(projection_known_ns), "projection_known_time")
    max_available = source.get("maximum_source_availability_ns")
    _require(_strict_int(max_available), "source_max_availability")
    _require(
        projection_known_ns > max(queue_receipt_ns, cast(int, max_available)),
        "projection_time_not_after_source_availability",
    )
    schedule_rows = _validate_schedule(source, expected)

    observed = source.get("observed")
    _require(isinstance(observed, dict), "observed_object")
    captures = observed.get("captures")
    references = observed.get("references")
    classes = observed.get("classes")
    frames = observed.get("frames")
    _require(isinstance(captures, list), "captures_list")
    _require(isinstance(references, list), "references_list")
    _require(isinstance(classes, list), "classes_list")
    _require(isinstance(frames, list), "observed_frames_list")
    captures = cast(list[dict[str, Any]], captures)
    references = cast(list[dict[str, Any]], references)
    classes = cast(list[dict[str, Any]], classes)
    frames = cast(list[dict[str, Any]], frames)
    _require(len(captures) == expected["selected_blocks_in_sealed_frames"], "capture_count")
    _require(len(frames) == expected["sealed_frames"], "observed_frame_count")
    _require(len(references) == expected["reference_occurrences"], "reference_count")
    _require(len(classes) == expected["class_count"], "class_count")

    schedule_sealed = {row["bucket"]: row for row in schedule_rows if row.get("state") == "sealed"}
    observed_by_bucket = {row["bucket"]: row for row in frames}
    _require(len(observed_by_bucket) == len(frames), "duplicate_observed_frame")
    _require(set(observed_by_bucket) == set(schedule_sealed), "observed_schedule_splice")
    capture_by_id: dict[str, dict[str, Any]] = {}
    capture_by_coordinate: dict[tuple[str, int, str], dict[str, Any]] = {}
    for capture in captures:
        capture_id = capture.get("capture_id")
        _require(
            isinstance(capture_id, str) and capture_id not in capture_by_id, "duplicate_capture_id"
        )
        _require(_is_sha256(capture.get("capture_receipt_sha256")), "capture_receipt_sha")
        _require(_is_sha256(capture.get("response_sha256")), "capture_response_sha")
        coordinate = (capture.get("bucket"), capture.get("slot"), capture.get("blockhash"))
        _require(
            isinstance(coordinate[0], str)
            and _strict_int(coordinate[1])
            and isinstance(coordinate[2], str),
            "capture_coordinate",
        )
        _require(coordinate not in capture_by_coordinate, "duplicate_capture_coordinate")
        capture_by_id[capture_id] = capture
        capture_by_coordinate[cast(tuple[str, int, str], coordinate)] = capture

    observed_block_keys: set[tuple[str, int, str]] = set()
    captured_transaction_totals: Counter[str] = Counter()
    selected_block_total = 0
    produced_slot_total = 0
    unknown_scan_total = 0
    for bucket, observed_frame in observed_by_bucket.items():
        source_frame = schedule_sealed[bucket]
        frame_counts = observed_frame.get("counts")
        source_counts = source_frame.get("counts")
        blocks = observed_frame.get("blocks")
        _require(isinstance(frame_counts, dict) and isinstance(source_counts, dict), "frame_counts")
        _require(isinstance(blocks, list), "frame_blocks")
        _require(frame_counts == source_counts, "schedule_frame_count_mismatch")
        local = cast(list[dict[str, Any]], blocks)
        selected_block_total += len(local)
        produced_slot_total += cast(int, frame_counts["produced_slots"])
        unknown_scan_total += cast(int, frame_counts["unknown_scan_transactions"])
        block_transactions: Counter[str] = Counter()
        for block in local:
            coordinate = (bucket, block.get("slot"), block.get("blockhash"))
            _require(coordinate not in observed_block_keys, "duplicate_observed_block")
            observed_block_keys.add(cast(tuple[str, int, str], coordinate))
            _require(coordinate in capture_by_coordinate, "observed_block_capture_missing")
            capture = capture_by_coordinate[cast(tuple[str, int, str], coordinate)]
            for block_key, capture_key in (
                ("capture_id", "capture_id"),
                ("capture_receipt_sha256", "capture_receipt_sha256"),
                ("response_sha256", "response_sha256"),
                ("result_sha256", "result_sha256"),
                ("received_ns", "received_ns"),
                ("available_ns", "available_ns"),
            ):
                _require(
                    block.get(block_key) == capture.get(capture_key), f"block_capture_{block_key}"
                )
            status_counts = block.get("transaction_status_counts")
            _require(isinstance(status_counts, dict), "block_transaction_status_counts")
            _require(
                sum(status_counts.get(status, 0) for status in ("success", "failed", "unknown"))
                == block.get("transaction_count"),
                "block_transaction_status_sum",
            )
            for status in ("success", "failed", "unknown"):
                count = status_counts.get(status)
                _require(_strict_int(count), "block_transaction_status_count")
                block_transactions[status] += cast(int, count)
            block_unknown_scan = block.get("unknown_structural_scan_transactions")
            _require(_strict_int(block_unknown_scan), "block_unknown_scan_count")
            _require(block_unknown_scan == 0, "unknown_scan_transaction")
        for status in ("success", "failed", "unknown"):
            _require(
                block_transactions[status] == frame_counts[f"{status}_transactions"],
                "frame_block_transaction_status_sum",
            )
            captured_transaction_totals[status] += block_transactions[status]
        _require(
            sum(block_transactions.values()) == frame_counts["scanned_transactions"],
            "frame_scanned_transaction_sum",
        )
        _require(
            sum(block.get("unknown_structural_scan_transactions", 0) for block in local)
            == frame_counts["unknown_scan_transactions"],
            "frame_unknown_scan_sum",
        )
    _require(len(observed_block_keys) == len(captures), "capture_block_set_count")
    _require(
        selected_block_total == expected["selected_blocks_in_sealed_frames"], "selected_block_total"
    )
    _require(
        produced_slot_total == expected["produced_slots_in_sealed_frames"], "produced_slot_total"
    )
    _require(unknown_scan_total == expected["unknown_scan_transactions"], "unknown_scan_total")
    for status, denominator_name in (
        ("success", "success_transactions"),
        ("failed", "failed_transactions"),
        ("unknown", "unknown_transactions"),
    ):
        _require(
            captured_transaction_totals[status] == expected[denominator_name],
            "transaction_status_total",
        )

    reference_ids: set[str] = set()
    reference_status_counts: Counter[str] = Counter()
    per_frame_ref_counts: dict[str, Counter[str]] = defaultdict(Counter)
    projected_rows: list[dict[str, Any]] = []
    for reference in references:
        reference_id = reference.get("reference_id")
        _require(
            isinstance(reference_id, str) and reference_id not in reference_ids,
            "duplicate_reference_id",
        )
        reference_ids.add(reference_id)
        _require(reference.get("program_id") == PUMP_AMM, "program_id")
        _require(_is_sha256(reference.get("signature_sha256")), "signature_sha")
        _require(_is_sha256(reference.get("instruction_data_sha256")), "instruction_data_sha")
        _require(_is_sha256(reference.get("class_fingerprint_sha256")), "class_fingerprint_sha")
        _require(
            reference.get("class_fingerprint_schema") == "pump_program_structural_fingerprint_v1",
            "class_fingerprint_schema",
        )
        _require(_strict_int(reference.get("account_count")), "account_count")
        status = reference.get("transaction_status")
        _require(status in ("success", "failed", "unknown"), "transaction_status")
        path = reference.get("instruction_path")
        _require(
            isinstance(path, dict) and set(path) == {"outer_index", "inner_index"},
            "instruction_path_shape",
        )
        _require(_strict_int(path.get("outer_index")), "instruction_path_outer")
        _require(
            path.get("inner_index") is None or _strict_int(path.get("inner_index")),
            "instruction_path_inner",
        )
        expected_reference_id = source_schema.digest(
            source_schema.canonical(
                {
                    "capture_id": reference.get("capture_id"),
                    "signature_sha256": reference.get("signature_sha256"),
                    "instruction_path": path,
                }
            )
        )
        _require(reference_id == expected_reference_id, "reference_id_binding")
        _require(reference.get("decision_ns") is None, "decision_time_must_remain_null")
        capture_id = reference.get("capture_id")
        _require(
            isinstance(capture_id, str) and capture_id in capture_by_id, "reference_capture_missing"
        )
        capture = capture_by_id[capture_id]
        for key in (
            "bucket",
            "slot",
            "blockhash",
            "capture_id",
            "capture_receipt_sha256",
            "response_sha256",
            "request_sha256",
            "result_sha256",
            "request_path",
            "response_path",
            "result_path",
            "capture_receipt_path",
        ):
            capture_key = "capture_receipt_sha256" if key == "capture_receipt_sha256" else key
            _require(reference.get(key) == capture.get(capture_key), f"reference_capture_{key}")
        for time_key, capture_key in (
            ("source_event_time_ns", "source_event_time_ns"),
            ("received_ns", "received_ns"),
            ("available_ns", "available_ns"),
        ):
            _require(
                reference.get(time_key) == capture.get(capture_key), f"reference_capture_{time_key}"
            )
        event_ns = reference.get("source_event_time_ns")
        received_ns = reference.get("received_ns")
        available_ns = reference.get("available_ns")
        _require(
            all(_strict_int(value) for value in (event_ns, received_ns, available_ns)),
            "reference_time_type",
        )
        _require(
            event_ns <= received_ns <= available_ns <= projection_known_ns, "reference_time_order"
        )
        _require(projection_known_ns >= available_ns, "capture_time_after_projection")
        observed_frame = observed_by_bucket.get(reference["bucket"])
        _require(observed_frame is not None, "reference_unobserved_bucket")
        _require(
            (reference["bucket"], reference["slot"], reference["blockhash"])
            in capture_by_coordinate,
            "reference_capture_coordinate_splice",
        )
        reference_status_counts[status] += 1
        per_frame_ref_counts[reference["bucket"]][status] += 1
        projected_rows.append(
            {
                "record_type": (
                    "pump_amm_structural_instruction_reference_present_in_captured_selected_block"
                ),
                "claim_scope": "sample_only_structural_reference_presence",
                "network": NETWORK,
                "process_version": PROCESS_VERSION,
                "service_id": scope["service_id"],
                "bucket": reference["bucket"],
                "slot": reference["slot"],
                "blockhash": reference["blockhash"],
                "reference_id": reference_id,
                "capture_id": capture_id,
                "signature_sha256": reference["signature_sha256"],
                "instruction_path": dict(path),
                "capture_receipt_sha256": reference["capture_receipt_sha256"],
                "response_sha256": reference["response_sha256"],
                "request_sha256": reference["request_sha256"],
                "result_sha256": reference["result_sha256"],
                "program_id": reference["program_id"],
                "instruction_data_sha256": reference["instruction_data_sha256"],
                "account_count": reference["account_count"],
                "class_fingerprint_schema": reference["class_fingerprint_schema"],
                "class_fingerprint_sha256": reference["class_fingerprint_sha256"],
                "transaction_status": status,
                "source_event_time_ns": event_ns,
                "received_ns": received_ns,
                "capture_available_ns": available_ns,
                "projection_known_ns": projection_known_ns,
                "decision_ns": None,
                "executed_instruction": None,
                "instruction_effect": None,
                "settlement": None,
            }
        )

    if expected_reference_status_counts is not None:
        for status in ("success", "failed", "unknown"):
            _require(
                reference_status_counts[status] == expected_reference_status_counts[status],
                f"reference_{status}_denominator",
            )
    _require(
        sum(reference_status_counts.values()) == expected["reference_occurrences"],
        "reference_status_sum",
    )
    projected_rows.sort(key=lambda row: row["reference_id"])

    projected_classes = _class_summary(projected_rows)
    _require(len(projected_classes) == expected["class_count"], "projected_class_count")
    input_classes_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for class_row in classes:
        _require(
            class_row.get("classification") == CLASSIFICATION, "unresolved_classification_changed"
        )
        key = (
            class_row.get("program_id"),
            class_row.get("instruction_data_sha256"),
            class_row.get("account_count"),
            "pump_program_structural_fingerprint_v1",
            class_row.get("class_fingerprint_sha256"),
        )
        _require(key not in input_classes_by_key, "duplicate_class_row")
        input_classes_by_key[key] = class_row
    projected_by_key = {_class_key(row): row for row in projected_classes}
    _require(set(input_classes_by_key) == set(projected_by_key), "class_reference_set_mismatch")
    for key, source_class in input_classes_by_key.items():
        projected_class = projected_by_key[key]
        _require(
            source_class.get("reference_occurrences") == projected_class["reference_occurrences"],
            "class_occurrence_count",
        )
        _require(
            source_class.get("transaction_status_counts")
            == projected_class["transaction_status_counts"],
            "class_status_count",
        )

    for bucket, observed_frame in observed_by_bucket.items():
        counts = observed_frame.get("counts")
        _require(isinstance(counts, dict), "observed_frame_counts")
        local_refs = per_frame_ref_counts[bucket]
        _require(
            counts.get("reference_occurrences") == sum(local_refs.values()), "frame_reference_count"
        )
    observed_total = Counter()
    for count in per_frame_ref_counts.values():
        observed_total.update(count)
    _require(observed_total == reference_status_counts, "reference_status_frame_reconciliation")

    class_summary = projected_classes
    _require(
        sum(row["reference_occurrences"] for row in class_summary)
        == expected["reference_occurrences"],
        "class_occurrence_sum",
    )
    output_denominators = {
        **{key: expected[key] for key in EXPECTED_DENOMINATORS},
        "reference_status_counts": {
            status: reference_status_counts[status] for status in ("success", "failed", "unknown")
        },
        "selected_share_of_produced_slots": {
            "selected_blocks": expected["selected_blocks_in_sealed_frames"],
            "produced_slots": expected["produced_slots_in_sealed_frames"],
        },
        "unobserved_state_counts": {
            "callback_failed_unobserved": 1,
            "expired_unobserved": 8,
        },
    }
    return {
        "schema": "pump-amm-structural-archive-projection-v1",
        "schema_version": 1,
        "status": "sample_only_structural_reference_archive",
        "source_binding": receipt_hash_binding,
        "scope": {
            "network": NETWORK,
            "process_version": PROCESS_VERSION,
            "service_id": scope["service_id"],
            "program_id": PUMP_AMM,
            "projection_known_ns": projection_known_ns,
            "sample_only": True,
        },
        "schedule": [
            {
                "bucket": row["bucket"],
                "state": row["state"],
                "start_ns": row["start_ns"],
                "end_ns": row["end_ns"],
                "counts": row.get("counts")
                if row.get("state") == "sealed"
                else {
                    "produced_slots": row.get("produced_slots"),
                    "selected_blocks": row.get("selected_blocks"),
                    "scanned_transactions": row.get("scanned_transactions"),
                    "success_transactions": row.get("success_transactions"),
                    "failed_transactions": row.get("failed_transactions"),
                    "unknown_transactions": row.get("unknown_transactions"),
                    "reference_occurrences": row.get("reference_occurrences"),
                    "unknown_scan_transactions": row.get("unknown_scan_transactions"),
                },
            }
            for row in schedule_rows
        ],
        "denominators": output_denominators,
        "references": projected_rows,
        "classes": class_summary,
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
                raise ProjectionError("storage_root_identity_or_mode")
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
        raise ProjectionError("incomplete_existing_publication")
    return "create"


def _build_publication_receipt(
    artifact_bytes: bytes,
    *,
    projection_known_ns: int,
    input_sha256: str,
    input_receipt_sha256: str,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema": "pump-amm-structural-archive-projection-publication-v1",
        "schema_version": 1,
        "status": "created_or_verified",
        "artifact_path": OUTPUT_RELATIVE,
        "artifact_sha256": _sha256(artifact_bytes),
        "source_artifact_sha256": input_sha256,
        "source_publication_receipt_sha256": input_receipt_sha256,
        "projection_known_ns": projection_known_ns,
        "point_in_time_eligible": False,
        "provider_io": False,
    }
    receipt["content_sha256"] = _canonical_sha256(receipt)
    return receipt


def _verify_existing_publication(
    artifact_bytes: bytes,
    receipt_bytes: bytes,
    *,
    input_sha256: str,
    input_receipt_sha256: str,
) -> dict[str, Any]:
    try:
        artifact = json.loads(artifact_bytes)
        receipt = json.loads(receipt_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectionError("existing_publication_json") from exc
    _require(
        isinstance(artifact, dict) and isinstance(receipt, dict), "existing_publication_objects"
    )
    _require(_canonical(artifact) == artifact_bytes, "existing_artifact_not_canonical")
    _require(_canonical(receipt) == receipt_bytes, "existing_receipt_not_canonical")
    _require(
        receipt.get("schema") == "pump-amm-structural-archive-projection-publication-v1"
        and receipt.get("schema_version") == 1
        and receipt.get("status") == "created_or_verified",
        "existing_receipt_schema",
    )
    _require(receipt.get("artifact_path") == OUTPUT_RELATIVE, "existing_receipt_path")
    _require(receipt.get("artifact_sha256") == _sha256(artifact_bytes), "existing_artifact_hash")
    _require(receipt.get("source_artifact_sha256") == input_sha256, "existing_source_hash")
    _require(
        receipt.get("source_publication_receipt_sha256") == input_receipt_sha256,
        "existing_source_receipt_hash",
    )
    _require(receipt.get("point_in_time_eligible") is False, "existing_pit_claim")
    _require(receipt.get("provider_io") is False, "existing_provider_io")
    content_hash = receipt.get("content_sha256")
    receipt_without_hash = dict(receipt)
    receipt_without_hash.pop("content_sha256", None)
    _require(
        content_hash == _canonical_sha256(receipt_without_hash),
        "existing_receipt_content_hash",
    )
    _require(
        artifact.get("schema") == "pump-amm-structural-archive-projection-v1"
        and artifact.get("schema_version") == 1
        and artifact.get("status") == "sample_only_structural_reference_archive",
        "existing_artifact_schema",
    )
    binding = artifact.get("source_binding")
    scope = artifact.get("scope")
    _require(isinstance(binding, dict) and isinstance(scope, dict), "existing_artifact_binding")
    _require(binding.get("source_artifact_sha256") == input_sha256, "existing_artifact_source_hash")
    _require(
        binding.get("source_publication_receipt_sha256") == input_receipt_sha256,
        "existing_artifact_source_receipt_hash",
    )
    projection_known_ns = receipt.get("projection_known_ns")
    _require(_strict_int(projection_known_ns), "existing_projection_time")
    _require(projection_known_ns > INPUT_RECEIPT_NS, "existing_projection_before_input_receipt")
    _require(
        scope.get("projection_known_ns") == projection_known_ns, "existing_projection_time_binding"
    )
    _require(
        binding.get("source_publication_receipt_ns") == INPUT_RECEIPT_NS,
        "existing_source_receipt_time_binding",
    )
    return {
        **receipt,
        "publication_receipt_path": OUTPUT_RECEIPT_RELATIVE,
        "publication_receipt_sha256": _sha256(receipt_bytes),
        "bytes": len(artifact_bytes),
    }


def publish_create_only(
    artifact: Mapping[str, Any],
    *,
    projection_known_ns: int,
    input_sha256: str,
    input_receipt_sha256: str,
    config_path: Path = STORAGE_CONFIG,
) -> dict[str, Any]:
    """Descriptor-bound, idempotent create-only publisher; not invoked by tests."""
    artifact_bytes = _canonical(artifact)
    machine_receipt = _build_publication_receipt(
        artifact_bytes,
        projection_known_ns=projection_known_ns,
        input_sha256=input_sha256,
        input_receipt_sha256=input_receipt_sha256,
    )
    receipt_bytes = _canonical(machine_receipt)
    relative_dir = OUTPUT_RELATIVE.rsplit("/", 1)[0]
    receipt_relative = OUTPUT_RECEIPT_RELATIVE
    with writer_guard(config_path) as guard:
        _require(
            guard.root_id == STORAGE_ROOT_ID and guard.volume_uuid.lower() == STORAGE_VOLUME_UUID,
            "storage_root_binding_mismatch",
        )
        guard.create_directory(relative_dir)
        root_fd = _open_data_root(guard)
        try:

            def revalidate_before_write() -> None:
                guard.revalidate()

            with DescriptorWriter(root_fd, before_write=revalidate_before_write) as writer:
                action = _publication_action(
                    writer.exists(OUTPUT_RELATIVE), writer.exists(receipt_relative)
                )
                if action == "verify_existing":
                    prior_artifact = writer.read_bytes(OUTPUT_RELATIVE, max_bytes=64 * 1024 * 1024)
                    prior_receipt = writer.read_bytes(receipt_relative, max_bytes=64_000)
                    result = _verify_existing_publication(
                        prior_artifact,
                        prior_receipt,
                        input_sha256=input_sha256,
                        input_receipt_sha256=input_receipt_sha256,
                    )
                    guard.revalidate()
                    return result
                writer.create(OUTPUT_RELATIVE, artifact_bytes)
                writer.create(receipt_relative, receipt_bytes)
        finally:
            os.close(root_fd)
        guard.revalidate()
        return {
            **machine_receipt,
            "publication_receipt_path": OUTPUT_RECEIPT_RELATIVE,
            "publication_receipt_sha256": _sha256(receipt_bytes),
            "bytes": len(artifact_bytes),
        }


def run(
    *,
    config_path: Path = STORAGE_CONFIG,
) -> dict[str, Any]:
    """Read pinned input, timestamp the projection locally, validate, and publish."""
    with writer_guard(config_path) as guard:
        _require(
            guard.root_id == STORAGE_ROOT_ID and guard.volume_uuid.lower() == STORAGE_VOLUME_UUID,
            "input_storage_root_binding_mismatch",
        )
        root_fd = _open_data_root(guard)
        try:
            with DescriptorWriter(root_fd) as reader:
                source_bytes = reader.read_bytes(INPUT_RELATIVE, max_bytes=INPUT_BYTES + 1)
                receipt_bytes = reader.read_bytes(INPUT_RECEIPT_RELATIVE, max_bytes=64_000)
        finally:
            os.close(root_fd)
    projection_known_ns = time.time_ns()
    artifact = validate_and_project(
        source_bytes, receipt_bytes, projection_known_ns=projection_known_ns
    )
    return publish_create_only(
        artifact,
        projection_known_ns=projection_known_ns,
        input_sha256=_sha256(source_bytes),
        input_receipt_sha256=_sha256(receipt_bytes),
        config_path=config_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="publish only after separate review")
    parser.add_argument("--storage-config", type=Path, default=STORAGE_CONFIG)
    args = parser.parse_args()
    if not args.publish:
        parser.error("--publish is required; review the frozen projection contract first")
    print(
        json.dumps(
            run(config_path=args.storage_config),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
