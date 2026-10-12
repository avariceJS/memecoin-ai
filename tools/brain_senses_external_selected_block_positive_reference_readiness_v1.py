#!/usr/bin/env python3
"""Publish a sample-local readiness diagnostic for sealed selected external-v2 blocks.

The output preserves acquisition-frame time separately from native Solana slots. It
does not establish population coverage, UTC event-time mapping, economic support,
or training/admission eligibility.
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
from tools import (  # noqa: E402
    brain_senses_external_sample_unknown_review_queue_v1 as unknown_review,
)
from tools import brain_senses_external_structural_family_triage_v1 as family_triage  # noqa: E402
from tools import (  # noqa: E402
    brain_senses_external_structural_input_schema_v1 as structural_schema,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402

SERVICE_ID = family_triage.SERVICE_ID
STORAGE_CONFIG = family_triage.STORAGE_CONFIG
STORAGE_ROOT_ID = archive_projection.STORAGE_ROOT_ID
STORAGE_VOLUME_UUID = archive_projection.STORAGE_VOLUME_UUID
EXPECTED_PLAN_SHA256 = family_triage.EXPECTED_PLAN_SHA256
EXPECTED_QUEUE_SHA256 = family_triage.EXPECTED_QUEUE_SHA256
EXPECTED_ARCHIVE_SHA256 = family_triage.EXPECTED_ARCHIVE_SHA256
EXPECTED_FAMILY_SHA256 = "c5a83e4e65eb5227e7c2da16de4efb0a8365d21f6b008674f81ba04674b60ee0"
EXPECTED_UNKNOWN_SHA256 = "aea8f395f932fdf3e989f60439b3bf8e0e2d1c2bc63b5503eb4a5a8c36cf6bcf"
EXPECTED_UNKNOWN_RECEIPT_SHA256 = "ce7828e50ba4ee6f29754e3b224b713e4c9c8f01cfe5d1e786035b293efd8885"
UNKNOWN_RELATIVE = unknown_review.OUTPUT_RELATIVE
UNKNOWN_RECEIPT_RELATIVE = unknown_review.OUTPUT_RECEIPT_RELATIVE
OUTPUT_RELATIVE = (
    "derived/external-v2-selected-block-positive-reference-readiness-v1/"
    "external-v2-20261012T0600Z/readiness.json"
)
OUTPUT_RECEIPT_RELATIVE = (
    "derived/external-v2-selected-block-positive-reference-readiness-v1/"
    "external-v2-20261012T0600Z/publication-receipt.json"
)
SCHEMA = "external-v2-selected-block-positive-reference-readiness-v1"
RECEIPT_SCHEMA = "external-v2-selected-block-positive-reference-readiness-publication-v1"
MAX_BYTES = 4_000_000
EXPECTED_SLOTS = 4_164
EXPECTED_BLOCKS = 48
EXPECTED_REFS = 4_865


class ReadinessError(ValueError):
    """Frozen evidence or joins violate the sample-local readiness contract."""


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ReadinessError(code)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _canonical_sha(value: Any) -> str:
    return _sha(_canonical(value))


def _strict_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _load_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReadinessError(f"{label}_json") from exc
    _require(isinstance(value, dict), f"{label}_object")
    return cast(dict[str, Any], value)


def validate_and_build(
    *,
    queue: Mapping[str, Any],
    archive: Mapping[str, Any],
    family: Mapping[str, Any],
    unknown: Mapping[str, Any],
    source_replay: Mapping[str, Any],
    projection_known_ns: int,
) -> dict[str, Any]:
    """Build aggregate, 12 frame dispositions, and 48 selected-block rows."""
    _require(_strict_int(projection_known_ns), "projection_time")
    _require(queue.get("scope", {}).get("plan_sha256") == EXPECTED_PLAN_SHA256, "plan_binding")
    _require(queue.get("denominators", {}).get("scheduled_frames") == 12, "frame_denominator")
    _require(source_replay.get("status") == "verified_not_published", "source_replay_status")
    _require(source_replay.get("family_count") == 68, "source_replay_family_count")
    _require(
        source_replay.get("denominators", {}).get("selected_blocks_in_sealed_frames")
        == EXPECTED_BLOCKS,
        "replay_blocks",
    )
    replay_binding = source_replay.get("source_binding")
    _require(isinstance(replay_binding, dict), "replay_source_binding")
    replay_binding = cast(dict[str, Any], replay_binding)
    _require(
        replay_binding.get("idl_history_receipt_sha256") == family_triage.IDL_HISTORY_RECEIPT_SHA256
        and replay_binding.get("idl_manifest_sha256") == family_triage.IDL_MANIFEST_SHA256
        and isinstance(replay_binding.get("idl_blobs"), list)
        and len(replay_binding["idl_blobs"]) == 18,
        "official_idl_program_id_source_binding",
    )
    _require(
        structural_schema.PUMP_PROGRAM_ID == "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
        "official_program_id",
    )

    schedule = archive.get("schedule")
    _require(isinstance(schedule, list) and len(schedule) == 12, "schedule_rows")
    schedule_rows = cast(list[dict[str, Any]], schedule)
    _require(sum(row.get("state") == "sealed" for row in schedule_rows) == 3, "sealed_frames")
    _require(
        sum(row.get("state") == "expired_unobserved" for row in schedule_rows) == 8,
        "expired_frames",
    )
    _require(
        sum(row.get("state") == "callback_failed_unobserved" for row in schedule_rows) == 1,
        "callback_frame",
    )
    _require(
        sum(
            cast(dict[str, Any], row.get("counts"))["produced_slots"]
            for row in schedule_rows
            if row.get("state") == "sealed"
        )
        == EXPECTED_SLOTS,
        "produced_slot_denominator",
    )

    observed = queue.get("observed")
    _require(isinstance(observed, dict), "queue_observed")
    observed = cast(dict[str, Any], observed)
    captures = observed.get("captures")
    references = observed.get("references")
    _require(isinstance(captures, list) and len(captures) == EXPECTED_BLOCKS, "capture_count")
    _require(isinstance(references, list) and len(references) == EXPECTED_REFS, "reference_count")
    capture_rows = cast(list[dict[str, Any]], captures)
    reference_rows = cast(list[dict[str, Any]], references)
    capture_ids: set[str] = set()
    block_keys: set[tuple[int, str]] = set()
    capture_by_id: dict[str, dict[str, Any]] = {}
    per_capture: dict[str, Counter[str]] = defaultdict(Counter)
    for capture in capture_rows:
        capture_id, bucket = capture.get("capture_id"), capture.get("bucket")
        slot, blockhash = capture.get("slot"), capture.get("blockhash")
        _require(isinstance(capture_id, str) and capture_id not in capture_ids, "capture_identity")
        _require(isinstance(bucket, str) and isinstance(blockhash, str), "capture_coordinates")
        _require(_strict_int(slot), "native_slot")
        _require((cast(int, slot), blockhash) not in block_keys, "duplicate_slot_block")
        _require(capture.get("frame_index") is not None, "capture_frame_index")
        _require(
            all(
                isinstance(capture.get(key), str) and len(capture[key]) == 64
                for key in (
                    "capture_receipt_sha256",
                    "request_sha256",
                    "response_sha256",
                    "result_sha256",
                )
            ),
            "capture_provenance_hashes",
        )
        capture_id = cast(str, capture_id)
        blockhash = cast(str, blockhash)
        slot = cast(int, slot)
        capture_ids.add(capture_id)
        block_keys.add((slot, blockhash))
        capture_by_id[capture_id] = capture

    ref_ids: set[str] = set()
    for ref in reference_rows:
        _require(ref.get("program_id") == structural_schema.PUMP_PROGRAM_ID, "reference_program_id")
        capture_id = ref.get("capture_id")
        _require(capture_id in capture_by_id, "reference_capture_join")
        capture = capture_by_id[cast(str, capture_id)]
        _require(ref.get("bucket") == capture.get("bucket"), "reference_bucket_join")
        _require(ref.get("slot") == capture.get("slot"), "reference_slot_join")
        _require(ref.get("blockhash") == capture.get("blockhash"), "reference_blockhash_join")
        status = ref.get("transaction_status")
        _require(status in {"success", "failed", "unknown"}, "reference_status")
        reference_id = ref.get("reference_id")
        _require(
            isinstance(reference_id, str) and reference_id not in ref_ids, "reference_id_unique"
        )
        reference_id = cast(str, reference_id)
        ref_ids.add(reference_id)
        per_capture[cast(str, capture_id)][cast(str, status)] += 1
    _require(
        len(capture_ids) == EXPECTED_BLOCKS and len(ref_ids) == EXPECTED_REFS, "joined_denominators"
    )
    _require(sum(per_capture[cid]["success"] for cid in capture_ids) == 4_176, "success_refs")
    _require(sum(per_capture[cid]["failed"] for cid in capture_ids) == 689, "failed_refs")
    _require(sum(per_capture[cid]["unknown"] for cid in capture_ids) == 0, "unknown_refs")

    archived_refs = archive.get("references")
    family_refs = family.get("references")
    diagnostic_entries = unknown.get("entries")
    _require(
        isinstance(archived_refs, list) and len(archived_refs) == EXPECTED_REFS,
        "archive_reference_count",
    )
    _require(
        isinstance(family_refs, list) and len(family_refs) == EXPECTED_REFS,
        "family_reference_count",
    )
    _require(
        isinstance(diagnostic_entries, list) and len(diagnostic_entries) == EXPECTED_REFS,
        "unknown_entry_count",
    )
    _require(
        unknown.get("readiness", {}).get("anomaly_source_qualified") is False,
        "unknown_claim_boundary",
    )
    _require(unknown.get("readiness", {}).get("decision_ns") is None, "unknown_decision_time")

    def identity(row: Mapping[str, Any]) -> tuple[Any, ...]:
        path = row.get("instruction_path")
        _require(isinstance(path, dict), "reference_path")
        path = cast(dict[str, Any], path)
        return (
            row.get("capture_id"),
            row.get("signature_sha256"),
            path.get("outer_index"),
            path.get("inner_index"),
        )

    queue_identity = {identity(row) for row in reference_rows}
    _require(len(queue_identity) == EXPECTED_REFS, "queue_identity_unique")
    for rows, label in (
        (cast(list[dict[str, Any]], archived_refs), "archive"),
        (cast(list[dict[str, Any]], family_refs), "family"),
        (cast(list[dict[str, Any]], diagnostic_entries), "unknown"),
    ):
        _require({identity(row) for row in rows} == queue_identity, f"{label}_identity_set")

    rows_by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for capture in capture_rows:
        rows_by_bucket[cast(str, capture["bucket"])].append(capture)
    frames: list[dict[str, Any]] = []
    sealed_buckets: set[str] = set()
    for frame in schedule_rows:
        bucket = frame.get("bucket")
        _require(isinstance(bucket, str), "frame_bucket")
        bucket = cast(str, bucket)
        state = frame.get("state")
        if state != "sealed":
            raw_counts = frame.get("counts")
            _require(isinstance(raw_counts, dict), "unobserved_counts")
            unobserved_counts = cast(dict[str, Any], raw_counts)
            _require(
                all(value is None for value in unobserved_counts.values()),
                "unobserved_metrics_not_null",
            )
            frames.append(
                {
                    "bucket": bucket,
                    "acquisition_window_start_ns": frame.get("start_ns"),
                    "acquisition_window_end_ns": frame.get("end_ns"),
                    "disposition": state,
                    "captured_selected_blocks": None,
                    "positive_reference_occurrences": None,
                    "success_reference_occurrences": None,
                    "failed_reference_occurrences": None,
                    "unknown_reference_occurrences": None,
                    "expected_event_count": None,
                }
            )
            continue
        sealed_buckets.add(bucket)
        local = rows_by_bucket.get(bucket, [])
        frame_counts: Counter[str] = Counter()
        for capture in local:
            frame_counts.update(per_capture[cast(str, capture["capture_id"])])
        source_counts = cast(dict[str, Any], frame.get("counts"))
        _require(len(local) == source_counts.get("selected_blocks"), "sealed_capture_count")
        _require(
            sum(frame_counts.values()) == source_counts.get("reference_occurrences"),
            "sealed_ref_count",
        )
        frames.append(
            {
                "bucket": bucket,
                "acquisition_window_start_ns": frame.get("start_ns"),
                "acquisition_window_end_ns": frame.get("end_ns"),
                "disposition": "sealed_selected_sample",
                "captured_selected_blocks": len(local),
                "positive_reference_occurrences": sum(frame_counts.values()),
                "success_reference_occurrences": frame_counts["success"],
                "failed_reference_occurrences": frame_counts["failed"],
                "unknown_reference_occurrences": frame_counts["unknown"],
                "expected_event_count": None,
            }
        )
    _require(
        len(sealed_buckets) == 3
        and sum(len(v) for v in rows_by_bucket.values()) == EXPECTED_BLOCKS,
        "frame_capture_set",
    )

    blocks: list[dict[str, Any]] = []
    for capture in sorted(capture_rows, key=lambda r: (r["bucket"], r["slot"], r["blockhash"])):
        cid = cast(str, capture["capture_id"])
        counts = per_capture[cid]
        blocks.append(
            {
                "service_id": SERVICE_ID,
                "acquisition_bucket": capture["bucket"],
                "native_slot": capture["slot"],
                "blockhash": capture["blockhash"],
                "capture_id": cid,
                "capture_receipt_sha256": capture["capture_receipt_sha256"],
                "request_sha256": capture["request_sha256"],
                "response_sha256": capture["response_sha256"],
                "result_sha256": capture["result_sha256"],
                "positive_reference_occurrences": sum(counts.values()),
                "success_reference_occurrences": counts["success"],
                "failed_reference_occurrences": counts["failed"],
                "unknown_reference_occurrences": counts["unknown"],
                "event_time_utc_ns": None,
                "decision_ns": None,
            }
        )

    max_source_available = max(cast(int, c["available_ns"]) for c in capture_rows)
    _require(projection_known_ns > max_source_available, "projection_time_order")
    unknown_known = cast(int, unknown["scope"]["projection_known_ns"])
    _require(projection_known_ns > unknown_known, "unknown_projection_time_order")
    totals: Counter[str] = Counter()
    for counts in per_capture.values():
        totals.update(counts)
    _require(sum(len(v) for v in rows_by_bucket.values()) == 48, "selected_block_exact_count")
    return {
        "schema": SCHEMA,
        "schema_version": 1,
        "status": "selected_block_positive_reference_readiness_diagnostic",
        "scope": {
            "network": "solana-mainnet-beta",
            "service_id": SERVICE_ID,
            "plan_sha256": EXPECTED_PLAN_SHA256,
            "sample_only": True,
            "time_axes": {
                "acquisition": "UTC-nanosecond schedule frames",
                "event_coordinate": "native Solana slot; no UTC mapping",
                "projection_known_ns": projection_known_ns,
            },
        },
        "source_binding": {},
        "frames": frames,
        "selected_blocks": blocks,
        "denominators": {
            "scheduled_frames": 12,
            "sealed_frames": 3,
            "unobserved_frames": 9,
            "produced_slots_in_sealed_frames": EXPECTED_SLOTS,
            "selected_blocks_in_sealed_frames": EXPECTED_BLOCKS,
            "positive_reference_occurrences": sum(totals.values()),
            "success_reference_occurrences": totals["success"],
            "failed_reference_occurrences": totals["failed"],
            "unknown_reference_occurrences": totals["unknown"],
            "expected_event_count": None,
        },
        "readiness": {
            "selected_block_positive_reference_readiness": True,
            "population_coverage_qualified": False,
            "complete_support": False,
            "decision_ns": None,
        },
        "nonclaims": {
            "unselected_slot_coverage": False,
            "slot_to_utc_event_mapping": False,
            "zero_or_absence": False,
            "population_or_market_observation_support": False,
            "formal_support_assertion": False,
            "economic_or_lifecycle_qualification": False,
            "training_or_admission": False,
        },
    }


def _open_data_root(guard: Any) -> int:
    current = os.dup(guard.mount_fd)
    device = os.fstat(current).st_dev
    try:
        for component in guard.config.data_root.split("/"):
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            st = os.fstat(child)
            if (
                not stat.S_ISDIR(st.st_mode)
                or st.st_dev != device
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o700
            ):
                os.close(child)
                raise ReadinessError("storage_root_identity_or_mode")
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _read_sources(config_path: Path) -> tuple[bytes, ...]:
    with writer_guard(config_path) as guard:
        _require(
            guard.root_id == STORAGE_ROOT_ID and guard.volume_uuid.lower() == STORAGE_VOLUME_UUID,
            "storage_identity",
        )
        root_fd = _open_data_root(guard)
        try:
            with DescriptorWriter(root_fd) as reader:
                return (
                    reader.read_bytes(
                        family_triage.QUEUE_RELATIVE,
                        max_bytes=family_triage.EXPECTED_QUEUE_BYTES + 1,
                    ),
                    reader.read_bytes(family_triage.QUEUE_RECEIPT_RELATIVE, max_bytes=64_000),
                    reader.read_bytes(
                        family_triage.ARCHIVE_RELATIVE, max_bytes=family_triage.OUTPUT_MAX_BYTES
                    ),
                    reader.read_bytes(family_triage.ARCHIVE_RECEIPT_RELATIVE, max_bytes=64_000),
                    reader.read_bytes(
                        family_triage.OUTPUT_RELATIVE, max_bytes=family_triage.OUTPUT_MAX_BYTES
                    ),
                    reader.read_bytes(family_triage.OUTPUT_RECEIPT_RELATIVE, max_bytes=64_000),
                    reader.read_bytes(UNKNOWN_RELATIVE, max_bytes=unknown_review.OUTPUT_MAX_BYTES),
                    reader.read_bytes(UNKNOWN_RECEIPT_RELATIVE, max_bytes=64_000),
                )
        finally:
            os.close(root_fd)


def _verify_existing(artifact_bytes: bytes, receipt_bytes: bytes) -> dict[str, Any]:
    artifact, receipt = (
        _load_json(artifact_bytes, "existing_artifact"),
        _load_json(receipt_bytes, "existing_receipt"),
    )
    _require(
        _canonical(artifact) == artifact_bytes and _canonical(receipt) == receipt_bytes,
        "existing_canonical",
    )
    _require(
        artifact.get("schema") == SCHEMA
        and artifact.get("status") == "selected_block_positive_reference_readiness_diagnostic",
        "existing_schema",
    )
    _require(
        receipt.get("schema") == RECEIPT_SCHEMA and receipt.get("artifact_path") == OUTPUT_RELATIVE,
        "existing_receipt_schema",
    )
    _require(receipt.get("artifact_sha256") == _sha(artifact_bytes), "existing_artifact_hash")
    _require(
        receipt.get("source_binding_sha256") == _canonical_sha(artifact.get("source_binding")),
        "existing_source_binding",
    )
    _require(
        receipt.get("content_sha256")
        == _canonical_sha({k: v for k, v in receipt.items() if k != "content_sha256"}),
        "existing_receipt_hash",
    )
    _require(
        receipt.get("provider_io") is False and receipt.get("point_in_time_eligible") is False,
        "existing_claims",
    )
    _require(
        len(artifact.get("frames", [])) == 12 and len(artifact.get("selected_blocks", [])) == 48,
        "existing_denominators",
    )
    _require(
        all(
            row.get("decision_ns") is None and row.get("event_time_utc_ns") is None
            for row in artifact["selected_blocks"]
        ),
        "existing_time_claim",
    )
    _require(
        all(artifact["frames"][i].get("expected_event_count") is None for i in range(12)),
        "existing_event_counts",
    )
    return {**receipt, "publication_receipt_sha256": _sha(receipt_bytes)}


def _receipt(artifact_bytes: bytes, artifact: Mapping[str, Any]) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "schema_version": 1,
        "status": "created_or_verified",
        "artifact_path": OUTPUT_RELATIVE,
        "artifact_sha256": _sha(artifact_bytes),
        "source_binding_sha256": _canonical_sha(artifact["source_binding"]),
        "projection_known_ns": artifact["scope"]["time_axes"]["projection_known_ns"],
        "provider_io": False,
        "point_in_time_eligible": False,
    }
    receipt["content_sha256"] = _canonical_sha(receipt)
    return receipt


def _publish(artifact: Mapping[str, Any], config_path: Path) -> dict[str, Any]:
    artifact_bytes = _canonical(artifact)
    _require(len(artifact_bytes) <= MAX_BYTES, "output_size_cap")
    receipt_bytes = _canonical(_receipt(artifact_bytes, artifact))
    with writer_guard(config_path) as guard:
        _require(
            guard.root_id == STORAGE_ROOT_ID and guard.volume_uuid.lower() == STORAGE_VOLUME_UUID,
            "output_storage_identity",
        )
        guard.create_directory(OUTPUT_RELATIVE.rsplit("/", 1)[0])
        root_fd = _open_data_root(guard)
        try:
            with DescriptorWriter(root_fd, before_write=guard.revalidate) as writer:
                exists_a, exists_r = (
                    writer.exists(OUTPUT_RELATIVE),
                    writer.exists(OUTPUT_RECEIPT_RELATIVE),
                )
                _require(exists_a == exists_r, "incomplete_publication_pair")
                if exists_a:
                    existing = writer.read_bytes(OUTPUT_RELATIVE, max_bytes=MAX_BYTES)
                    prior = _verify_existing(
                        existing, writer.read_bytes(OUTPUT_RECEIPT_RELATIVE, max_bytes=64_000)
                    )
                    _require(existing == artifact_bytes, "existing_candidate_mismatch")
                    guard.revalidate()
                    return prior
                writer.create(OUTPUT_RELATIVE, artifact_bytes)
                writer.create(OUTPUT_RECEIPT_RELATIVE, receipt_bytes)
        finally:
            os.close(root_fd)
        guard.revalidate()
    return {
        **_receipt(artifact_bytes, artifact),
        "publication_receipt_sha256": _sha(receipt_bytes),
        "bytes": len(artifact_bytes),
    }


def build_candidate(
    *, config_path: Path = STORAGE_CONFIG, projection_known_ns: int | None = None
) -> dict[str, Any]:
    sources = _read_sources(config_path)
    queue, archive, family = unknown_review._verify_source_artifacts(*sources[:6])
    _require(
        _sha(sources[0]) == EXPECTED_QUEUE_SHA256 and _sha(sources[2]) == EXPECTED_ARCHIVE_SHA256,
        "upstream_hashes",
    )
    _require(_sha(sources[4]) == EXPECTED_FAMILY_SHA256, "family_hash")
    _require(
        _sha(sources[6]) == EXPECTED_UNKNOWN_SHA256
        and _sha(sources[7]) == EXPECTED_UNKNOWN_RECEIPT_SHA256,
        "unknown_hashes",
    )
    unknown = _load_json(sources[6], "unknown_review")
    unknown_review._verify_existing_publication(sources[6], sources[7])
    # This independently rebuilds the terminal partial queue, reopens all 48
    # response captures, and reconciles the raw references before readiness rows
    # are derived from the pinned artifacts.
    replay = family_triage.run(publish=False, config_path=config_path)
    known_ns = time.time_ns() if projection_known_ns is None else projection_known_ns
    artifact = validate_and_build(
        queue=queue,
        archive=archive,
        family=family,
        unknown=unknown,
        source_replay=replay,
        projection_known_ns=known_ns,
    )
    artifact["source_binding"] = {
        "queue_sha256": _sha(sources[0]),
        "queue_receipt_sha256": _sha(sources[1]),
        "archive_sha256": _sha(sources[2]),
        "archive_receipt_sha256": _sha(sources[3]),
        "family_triage_sha256": _sha(sources[4]),
        "family_receipt_sha256": _sha(sources[5]),
        "unknown_review_sha256": _sha(sources[6]),
        "unknown_review_receipt_sha256": _sha(sources[7]),
        "plan_sha256": EXPECTED_PLAN_SHA256,
        "program_id": structural_schema.PUMP_PROGRAM_ID,
        "official_idl_history_receipt_sha256": replay["source_binding"][
            "idl_history_receipt_sha256"
        ],
        "official_idl_manifest_sha256": replay["source_binding"]["idl_manifest_sha256"],
        "raw_capture_replay_summary_sha256": _canonical_sha(
            {
                "status": replay["status"],
                "denominators": replay["denominators"],
                "family_count": replay["family_count"],
                "source_binding": replay["source_binding"],
            }
        ),
        "replay_source_binding": replay["source_binding"],
    }
    _require(len(_canonical(artifact)) <= MAX_BYTES, "output_size_cap")
    return artifact


def _read_existing_pair(config_path: Path) -> bytes | None:
    with writer_guard(config_path) as guard:
        _require(
            guard.root_id == STORAGE_ROOT_ID and guard.volume_uuid.lower() == STORAGE_VOLUME_UUID,
            "storage_identity",
        )
        root_fd = _open_data_root(guard)
        try:
            with DescriptorWriter(root_fd) as reader:
                exists_a, exists_r = (
                    reader.exists(OUTPUT_RELATIVE),
                    reader.exists(OUTPUT_RECEIPT_RELATIVE),
                )
                _require(exists_a == exists_r, "incomplete_publication_pair")
                if not exists_a:
                    return None
                artifact_bytes = reader.read_bytes(OUTPUT_RELATIVE, max_bytes=MAX_BYTES)
                receipt_bytes = reader.read_bytes(OUTPUT_RECEIPT_RELATIVE, max_bytes=64_000)
                _verify_existing(artifact_bytes, receipt_bytes)
                return cast(bytes, artifact_bytes)
        finally:
            os.close(root_fd)


def run(*, publish: bool, config_path: Path = STORAGE_CONFIG) -> dict[str, Any]:
    existing = _read_existing_pair(config_path)
    if existing is None:
        artifact = build_candidate(config_path=config_path)
    else:
        prior = _load_json(existing, "existing_artifact")
        projection_known_ns = prior["scope"]["time_axes"]["projection_known_ns"]
        artifact = build_candidate(config_path=config_path, projection_known_ns=projection_known_ns)
        _require(_canonical(artifact) == existing, "recomputed_artifact_mismatch")
    if publish:
        return _publish(artifact, config_path)
    artifact_bytes = _canonical(artifact)
    return {
        "status": "verified_not_published",
        "artifact_sha256": _sha(artifact_bytes),
        "artifact_bytes": len(artifact_bytes),
        "projection_known_ns": artifact["scope"]["time_axes"]["projection_known_ns"],
        "denominators": artifact["denominators"],
        "source_binding_sha256": _canonical_sha(artifact["source_binding"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--verify-only", action="store_true")
    modes.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    try:
        result = run(publish=args.publish)
    except (ReadinessError, OSError, ValueError, RuntimeError) as exc:
        print(f"selected-block readiness failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
