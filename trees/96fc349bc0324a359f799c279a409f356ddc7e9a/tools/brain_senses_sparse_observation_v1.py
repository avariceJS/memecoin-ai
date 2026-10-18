#!/usr/bin/env python3
"""Compile offline sparse-observation references from sealed role bundles.

The tool reads one qualified role bundle per invocation. It never opens source
capture response bodies, never combines role payloads, and publishes immutable
role artifacts through the descriptor-bound research-storage writer.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import stat
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from memecoin_research_storage import StorageError, writer_guard
from tools.brain_senses_external_writer_v1 import DescriptorWriter
from tools.brain_senses_sparse_observation_schema_v1 import (
    BAR_SECONDS,
    CAMPAIGN_SCHEMA,
    MAX_HISTORY,
    NS_PER_SECOND,
    ROLE_SCHEMA,
    ROLES,
    SCHEMA_VERSION,
    SparseReferenceError,
    canonical,
    digest,
    require_sha256,
)

REPO = Path(__file__).resolve().parents[1]
CAMPAIGN_RECEIPT = "docs/evidence/brain-independent-population-learning-2026-10-08.json"
CAMPAIGN_RECEIPT_SHA256 = "b976178f5fdfeacbf9f46f5e6ed4e88e9054f918a9ba0315a433539758551086"
CAMPAIGN_COVERAGE = "work/brain-diverse-campaign-20261008/learning/coverage.json"
ROLE_SOURCE_PINS = {
    "train": "3d3ff36feb8a73b3130611d9a9c22d3121fdc4ed2bfc7e6df2d2503be49fd181",
    "research_evaluation": "9b970e259da1edbf27082ccfb9732835316ca92bd7d9a23dc9784ff421a62edb",
}
ROLE_EXPECTED = {
    "train": {
        "entities": 170,
        "bars": 38_705,
        "support_slots": 587_520,
        "consecutive32": 8_792,
        "consecutive32_entities": 49,
    },
    "research_evaluation": {
        "entities": 137,
        "bars": 14_375,
        "support_slots": 473_472,
        "consecutive32": 6_465,
        "consecutive32_entities": 30,
    },
}
MIN_FREE_BYTES = 50 * 1024**3
METADATA_MARGIN_BYTES = 2 * 1024**2
DEFAULT_OUTPUT_PREFIX = "brain/sparse-observation-reference-v1/independent-population-2026-10-08"
CODE_FILES = {
    "sparse_engine": "tools/brain_senses_sparse_observation_v1.py",
    "sparse_schema": "tools/brain_senses_sparse_observation_schema_v1.py",
    "descriptor_writer": "tools/brain_senses_external_writer_v1.py",
    "storage_guard": "src/memecoin_research_storage/__init__.py",
}


def _read_plain(path: Path, limit: int) -> bytes:
    if not path.is_absolute():
        path = REPO / path
    if (
        ".." in path.parts
        or path.is_symlink()
        or any(parent.is_symlink() for parent in path.parents if parent != parent.parent)
    ):
        raise SparseReferenceError("source_path_unsafe")
    try:
        st = path.lstat()
    except OSError as exc:
        raise SparseReferenceError("source_file_missing") from exc
    if not path.is_file() or st.st_size > limit:
        raise SparseReferenceError("source_file_shape")
    raw = path.read_bytes()
    if len(raw) != st.st_size:
        raise SparseReferenceError("source_file_changed")
    return raw


def _json_bytes(raw: bytes, code: str) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise SparseReferenceError(code) from exc


def _read_lines(raw: bytes, code: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line:
            raise SparseReferenceError(code)
        value = _json_bytes(line, code)
        if not isinstance(value, dict):
            raise SparseReferenceError(code)
        rows.append(value)
    return rows


def _finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _request_class(record: dict[str, Any]) -> str:
    acquired = record.get("acquisition_status")
    qualification = record.get("qualification")
    if qualification == "unsupported_namespace":
        return "unsupported"
    if acquired not in {"received", "completed"} or qualification in {
        "http_failure",
        "capture_failure",
        "malformed_payload",
        "mint_mismatch",
        "bar_schema_or_alignment",
        "invalid_OHLCV",
    }:
        return "failed"
    if qualification in {"qualified", "duplicate_bar_in_response"}:
        return "unknown"
    raise SparseReferenceError("unrecognized_request_outcome")


def _stable_id(value: dict[str, Any]) -> str:
    return digest(canonical(value))


def _open_data_root_descriptor(guard: Any) -> int:
    """Open the configured private data root using descriptor-relative traversal."""
    current = os.dup(guard.mount_fd)
    device = os.fstat(current).st_dev
    try:
        for part in guard.config.data_root.split("/"):
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            entry = os.fstat(child)
            if (
                not stat.S_ISDIR(entry.st_mode)
                or entry.st_dev != device
                or entry.st_uid != os.getuid()
                or stat.S_IMODE(entry.st_mode) != 0o700
            ):
                os.close(child)
                raise StorageError("sparse_storage_root_identity")
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def current_code_hashes() -> dict[str, str]:
    return {
        name: digest(_read_plain(REPO / path, 8 * 1024 * 1024))
        for name, path in sorted(CODE_FILES.items())
    }


def _interval_contains(intervals: list[list[int]], open_s: int) -> bool:
    return any(start <= open_s < end for start, end in intervals)


def _parse_row(row: dict[str, Any], entity_ids: set[str]) -> tuple[str, int, int | None, str]:
    required = {"available_ns", "capture_sha", "lane", "ohlcv", "open_s", "pool"}
    if set(row) != required:
        raise SparseReferenceError("bar_row_fields")
    entity = row["pool"]
    open_s = row["open_s"]
    availability = row["available_ns"]
    capture_sha = require_sha256(row["capture_sha"], "bar_capture_hash")
    if (
        not isinstance(entity, str)
        or entity not in entity_ids
        or type(open_s) is not int
        or open_s < 0
        or open_s % BAR_SECONDS
        or (availability is not None and (type(availability) is not int or availability <= 0))
        or not isinstance(row["lane"], str)
        or not isinstance(row["ohlcv"], list)
        or len(row["ohlcv"]) != 5
        or any(not _finite_number(x) for x in row["ohlcv"])
    ):
        raise SparseReferenceError("bar_row_value")
    return entity, open_s, availability, capture_sha


def compile_role(
    role: str,
    source: dict[str, Any],
    row_lines: Iterable[bytes],
    capture_lines: Iterable[bytes],
    request_records: list[dict[str, Any]],
    *,
    source_pins: dict[str, str],
) -> dict[str, Any]:
    """Compile one role only; other-role rows are not an input to this function."""
    if role not in ROLES or not isinstance(source, dict):
        raise SparseReferenceError("role_source")
    if source.get("version") != "continuous-qualified-source-v1":
        raise SparseReferenceError("source_version")
    if source.get("namespace") != "geckoterminal/usd/base/5min/first-qualified-receipt":
        raise SparseReferenceError("source_namespace")
    if source.get("selection_policy") != "first_qualified_receipt_then_capture_sha":
        raise SparseReferenceError("source_dedup_policy")
    if source.get("historical_online_availability_known") is not False:
        raise SparseReferenceError("source_availability_claim")
    source_pins = dict(source_pins)
    code_hashes = source_pins.pop("code_sha256", None)
    if not isinstance(code_hashes, dict) or set(code_hashes) != set(CODE_FILES):
        raise SparseReferenceError("implementation_pins")
    for value in code_hashes.values():
        require_sha256(value, "implementation_pin")
    require_sha256(source_pins.get("source_pin"), "source_pin")
    plan = source.get("plan")
    if not isinstance(plan, dict) or plan.get("role") != role:
        raise SparseReferenceError("role_plan_mismatch")
    pools = plan.get("pools")
    intervals = plan.get("intervals")
    if not isinstance(pools, list) or not isinstance(intervals, dict):
        raise SparseReferenceError("source_enrollment_shape")
    pool_rows: dict[str, dict[str, str]] = {}
    for pool in pools:
        if (
            not isinstance(pool, dict)
            or set(pool) != {"pool", "base_mint", "quote_mint"}
            or any(not isinstance(pool.get(k), str) or not pool[k] for k in pool)
        ):
            raise SparseReferenceError("source_enrollment_row")
        if pool["pool"] in pool_rows:
            raise SparseReferenceError("duplicate_enrollment")
        pool_rows[pool["pool"]] = pool
    entity_ids = set(pool_rows)
    if set(intervals) != entity_ids:
        raise SparseReferenceError("support_entity_mismatch")

    normalized_intervals: dict[str, list[list[int]]] = {}
    support_rows: list[dict[str, Any]] = []
    declared_slots = 0
    for entity in sorted(entity_ids):
        spans = intervals[entity]
        if not isinstance(spans, list) or not spans:
            raise SparseReferenceError("support_interval_missing")
        prior_end = -1
        normalized: list[list[int]] = []
        for span in spans:
            if (
                not isinstance(span, list)
                or len(span) != 2
                or any(type(x) is not int for x in span)
                or not 0 <= span[0] < span[1]
                or span[0] % BAR_SECONDS
                or span[1] % BAR_SECONDS
                or span[0] < prior_end
            ):
                raise SparseReferenceError("support_interval_invalid")
            start_s, end_s = span
            prior_end = end_s
            slots = (end_s - start_s) // BAR_SECONDS
            declared_slots += slots
            normalized.append([start_s, end_s])
            support_rows.append(
                {
                    "entity_id": entity,
                    "start_ns": start_s * NS_PER_SECOND,
                    "end_ns": end_s * NS_PER_SECOND,
                    "grid_slots": slots,
                    "status": "unknown",
                    "status_basis": "declared_source_interval_without_complete_support_assertion",
                }
            )
        normalized_intervals[entity] = normalized

    captures: dict[tuple[str, str], str] = {}
    for raw in capture_lines:
        record = _json_bytes(raw, "capture_index_json")
        if set(record) != {"pin", "pool", "reason"}:
            raise SparseReferenceError("capture_index_fields")
        entity = record["pool"]
        pin = require_sha256(record["pin"], "capture_index_hash")
        reason = record["reason"]
        if entity not in entity_ids or not isinstance(reason, str):
            raise SparseReferenceError("capture_index_entity")
        captures[(entity, pin)] = reason

    observations_by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_bar_keys: set[tuple[str, int]] = set()
    for raw in row_lines:
        row = _json_bytes(raw, "bar_row_json")
        entity, open_s, received_ns, capture_sha = _parse_row(row, entity_ids)
        key = (entity, open_s)
        if key in seen_bar_keys:
            raise SparseReferenceError("duplicate_bar_open")
        seen_bar_keys.add(key)
        if not _interval_contains(normalized_intervals[entity], open_s):
            raise SparseReferenceError("bar_outside_declared_support")
        if captures.get((entity, capture_sha)) != "qualified":
            raise SparseReferenceError("bar_capture_not_qualified")
        source_row_sha = digest(raw + b"\n")
        start_ns = open_s * NS_PER_SECOND
        end_ns = start_ns + BAR_SECONDS * NS_PER_SECOND
        full_bar_available_ns = (
            received_ns if received_ns is not None and received_ns >= end_ns else None
        )
        availability_status = (
            "receipt_after_bar_end"
            if full_bar_available_ns is not None
            else "full_bar_availability_unproved"
        )
        observation_id = _stable_id(
            {
                "role": role,
                "source_pin": source_pins["source_pin"],
                "entity_id": entity,
                "open_s": open_s,
                "capture_sha256": capture_sha,
            }
        )
        observation = {
            "observation_id": observation_id,
            "entity_id": entity,
            "role": role,
            "event_time_ns": start_ns,
            "bar_open_ns": start_ns,
            "bar_end_ns": end_ns,
            "full_bar_receipt_available_ns": full_bar_available_ns,
            "availability_status": availability_status,
            "availability_basis": (
                "captured_row_available_ns_checked_against_bar_end; "
                "historical_online_availability_unknown"
            ),
            "source_pin": source_pins["source_pin"],
            "source_row_sha256": source_row_sha,
            "capture_sha256": capture_sha,
            "modality": "ohlcv_bar",
            "units": {
                "source_namespace": "geckoterminal/usd/base/5min/first-qualified-receipt",
                "volume_unit": None,
                "volume_unit_status": "provider_reported_unverified",
                "interval_seconds": BAR_SECONDS,
            },
            "first_qualified_receipt": True,
        }
        observations_by_entity[entity].append(observation)

    request_windows: list[dict[str, Any]] = []
    request_status_counts: Counter[str] = Counter()
    request_order: set[int] = set()
    for record in request_records:
        if not isinstance(record, dict) or set(record) != {
            "acquisition_status",
            "bars",
            "capture",
            "job",
            "ordinal",
            "qualification",
        }:
            raise SparseReferenceError("request_record_fields")
        job = record["job"]
        if (
            not isinstance(job, dict)
            or set(job) != {"end_s", "pool", "start_s"}
            or job.get("pool") not in entity_ids
            or type(job.get("start_s")) is not int
            or type(job.get("end_s")) is not int
            or job["start_s"] >= job["end_s"]
            or type(record.get("ordinal")) is not int
            or record["ordinal"] in request_order
            or type(record.get("bars")) is not int
            or record["bars"] < 0
            or not isinstance(record.get("acquisition_status"), str)
            or not isinstance(record.get("qualification"), str)
        ):
            raise SparseReferenceError("request_record_value")
        request_order.add(record["ordinal"])
        status = _request_class(record)
        request_status_counts[status] += 1
        capture = record["capture"]
        if capture is not None:
            require_sha256(capture, "request_capture_hash")
        request_windows.append(
            {
                "ordinal": record["ordinal"],
                "entity_id": job["pool"],
                "start_ns": job["start_s"] * NS_PER_SECOND,
                "end_ns": job["end_s"] * NS_PER_SECOND,
                "request_status": status,
                "acquisition_status": record["acquisition_status"],
                "qualification": record["qualification"],
                "reported_bars": record["bars"],
                "capture_sha256": capture,
            }
        )
    request_windows.sort(key=lambda r: r["ordinal"])

    enrollment_rows: list[dict[str, Any]] = []
    obs_counts = Counter({entity: len(rows) for entity, rows in observations_by_entity.items()})
    req_by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in request_windows:
        req_by_entity[item["entity_id"]].append(item)
    for entity in sorted(entity_ids):
        windows = req_by_entity.get(entity, [])
        states = {row["request_status"] for row in windows}
        if obs_counts[entity]:
            status = "has_qualified_observations"
        elif "unknown" in states:
            status = "no_qualified_observation_in_scope"
        elif "failed" in states and "unsupported" in states:
            status = "failed_and_unsupported_no_observation"
        elif "failed" in states:
            status = "failed_no_observation"
        elif "unsupported" in states:
            status = "unsupported_no_observation"
        else:
            status = "enrolled_without_observation_evidence"
        enrollment_rows.append(
            {
                "entity_id": entity,
                "role": role,
                "status": status,
                "qualified_observation_count": obs_counts[entity],
                "request_count": len(windows),
                "request_status_counts": dict(
                    sorted(Counter(r["request_status"] for r in windows).items())
                ),
            }
        )

    for _entity, rows in observations_by_entity.items():
        rows.sort(key=lambda r: (r["event_time_ns"], r["observation_id"]))
    observations: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    history_histogram: Counter[int] = Counter()
    consecutive32_rows = 0
    consecutive32_entities: set[str] = set()
    sparse_origins: set[str] = set()
    gap_origins = 0
    gap_count = 0
    for entity in sorted(entity_ids):
        rows = observations_by_entity.get(entity, [])
        entity_base_index = len(observations)
        previous_open_ns: int | None = None
        for idx, row in enumerate(rows):
            spacing_steps = (
                None
                if previous_open_ns is None
                else (row["bar_open_ns"] - previous_open_ns) // (BAR_SECONDS * NS_PER_SECOND)
            )
            row["observed_spacing_grid_steps"] = spacing_steps
            previous_open_ns = row["bar_open_ns"]
            observations.append(row)
            history = rows[max(0, idx - MAX_HISTORY) : idx]
            if any(prior["event_time_ns"] >= row["event_time_ns"] for prior in history):
                raise SparseReferenceError("history_future_bar")
            path = [*history, row]
            steps = [
                (path[i + 1]["event_time_ns"] - path[i]["event_time_ns"])
                // (BAR_SECONDS * NS_PER_SECOND)
                for i in range(len(path) - 1)
            ]
            if any(
                step < 1
                or (step * BAR_SECONDS * NS_PER_SECOND)
                != (path[i + 1]["event_time_ns"] - path[i]["event_time_ns"])
                for i, step in enumerate(steps)
            ):
                raise SparseReferenceError("history_grid_alignment")
            unknown_mask = [step > 1 for step in steps]
            is_sparse = len(history) < MAX_HISTORY or any(unknown_mask)
            reasons = []
            if len(history) < MAX_HISTORY:
                reasons.append("fewer_than_32_observed_prior_bars")
            if any(unknown_mask):
                reasons.append("unknown_elapsed_gap_between_observed_bars")
                gap_origins += 1
                gap_count += sum(unknown_mask)
            if is_sparse:
                sparse_origins.add(entity)
            if (
                idx >= 31
                and rows[idx]["event_time_ns"] - rows[idx - 31]["event_time_ns"]
                == 31 * BAR_SECONDS * NS_PER_SECOND
            ):
                consecutive32_rows += 1
                consecutive32_entities.add(entity)
            history_histogram[len(history)] += 1
            origin_index = len(observations) - 1
            contexts.append(
                {
                    "context_id": _stable_id(
                        {"role": role, "entity_id": entity, "origin_id": row["observation_id"]}
                    ),
                    "entity_id": entity,
                    "role": role,
                    "origin_index": origin_index,
                    "origin_event_time_ns": row["event_time_ns"],
                    "history_indices": list(
                        range(entity_base_index + max(0, idx - MAX_HISTORY), origin_index)
                    ),
                    "elapsed_grid_steps": steps,
                    "unknown_gap_mask": unknown_mask,
                    "history_length": len(history),
                    "censor_reasons": reasons,
                    "point_in_time_eligible": False,
                    "decision_ns": None,
                }
            )

    bars = len(observations)
    coverage = source.get("coverage")
    if not isinstance(coverage, list) or len(coverage) != len(entity_ids):
        raise SparseReferenceError("source_coverage_shape")
    coverage_by_entity = {r.get("pool"): r for r in coverage if isinstance(r, dict)}
    if set(coverage_by_entity) != entity_ids:
        raise SparseReferenceError("source_coverage_entities")
    for entity in entity_ids:
        expected = sum((end - start) // BAR_SECONDS for start, end in normalized_intervals[entity])
        declared = coverage_by_entity[entity]
        if (
            set(declared) != {"bars", "expected_slots", "missing_slots", "pool"}
            or declared["pool"] != entity
            or declared["bars"] != obs_counts[entity]
            or declared["expected_slots"] != expected
            or declared["missing_slots"] != expected - declared["bars"]
        ):
            raise SparseReferenceError("source_coverage_mismatch")
    if source.get("bars") != bars or declared_slots != sum(x["expected_slots"] for x in coverage):
        raise SparseReferenceError("source_bar_count_mismatch")

    report_counts = source.get("counts")
    if not isinstance(report_counts, dict):
        raise SparseReferenceError("source_dedup_counts")
    summary = {
        "schema": ROLE_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "role": role,
        "source_pins": source_pins,
        "code_sha256": code_hashes,
        "enrollment_entities": len(entity_ids),
        "qualified_bars": bars,
        "origins": bars,
        "entities_with_qualified_bars": sum(bool(obs_counts[e]) for e in entity_ids),
        "entities_without_qualified_bars": sum(not obs_counts[e] for e in entity_ids),
        "declared_support_interval_count": len(support_rows),
        "declared_support_slots": declared_slots,
        "qualified_observed_bar_slots": bars,
        "unobserved_declared_slots": declared_slots - bars,
        "support_status": "unknown_for_all_declared_intervals",
        "complete_support_claims": 0,
        "failed_request_interval_count": request_status_counts["failed"],
        "unsupported_request_interval_count": request_status_counts["unsupported"],
        "request_status_counts": dict(sorted(request_status_counts.items())),
        "first_qualified_dedup_policy": source.get("selection_policy"),
        "source_revision_count": report_counts.get("revised_observations"),
        "duplicate_observation_count": report_counts.get("duplicate_observations"),
        "context_history_length_histogram": {
            str(n): history_histogram[n] for n in range(MAX_HISTORY + 1)
        },
        "sparse_origins": sum(
            1
            for row in contexts
            if row["history_length"] < MAX_HISTORY or any(row["unknown_gap_mask"])
        ),
        "entities_with_sparse_origins": len(sparse_origins),
        "origins_with_unknown_gaps": gap_origins,
        "unknown_gap_edges": gap_count,
        "consecutive_32_including_origin": consecutive32_rows,
        "entities_with_consecutive_32": len(consecutive32_entities),
        "availability_basis": (
            "captured available_ns is retained only when at_or_after bar_end; otherwise null; "
            "historical_online_availability_unknown"
        ),
        "event_time_basis": "provider open_s timestamp; bar interval is [open,end)",
        "history_basis": (
            "strictly earlier event-time bars; no support-at-origin claim; no value imputation"
        ),
        "point_in_time_eligible": False,
        "decision_ns": None,
        "claim_scope": "historical_replay_structure_only_not_activity_census_or_training_admission",
    }
    return {
        "summary": summary,
        "observations": observations,
        "contexts": contexts,
        "support_intervals": support_rows,
        "request_windows": request_windows,
        "enrollment": enrollment_rows,
    }


def _jsonl(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(canonical(row) for row in rows)


def serialize_role(compiled: dict[str, Any]) -> dict[str, bytes]:
    summary = compiled["summary"]
    if not isinstance(summary, dict) or summary.get("schema") != ROLE_SCHEMA:
        raise SparseReferenceError("role_artifact_shape")
    return {
        "observations.jsonl": _jsonl(compiled["observations"]),
        "contexts.jsonl": _jsonl(compiled["contexts"]),
        "support-intervals.jsonl": _jsonl(compiled["support_intervals"]),
        "request-windows.jsonl": _jsonl(compiled["request_windows"]),
        "enrollment.jsonl": _jsonl(compiled["enrollment"]),
        "summary.json": canonical(summary),
    }


def verify_role_artifact(files: dict[str, bytes], *, expected_role: str) -> dict[str, Any]:
    expected_names = {
        "observations.jsonl",
        "contexts.jsonl",
        "support-intervals.jsonl",
        "request-windows.jsonl",
        "enrollment.jsonl",
        "summary.json",
    }
    if expected_role not in ROLES or set(files) != expected_names:
        raise SparseReferenceError("role_artifact_files")
    summary = _json_bytes(files["summary.json"], "role_summary_json")
    if (
        not isinstance(summary, dict)
        or summary.get("schema") != ROLE_SCHEMA
        or summary.get("schema_version") != SCHEMA_VERSION
        or summary.get("role") != expected_role
        or summary.get("point_in_time_eligible") is not False
        or summary.get("decision_ns") is not None
    ):
        raise SparseReferenceError("role_summary_identity")
    expected_summary_fields = {
        "schema",
        "schema_version",
        "role",
        "source_pins",
        "code_sha256",
        "enrollment_entities",
        "qualified_bars",
        "origins",
        "entities_with_qualified_bars",
        "entities_without_qualified_bars",
        "declared_support_interval_count",
        "declared_support_slots",
        "qualified_observed_bar_slots",
        "unobserved_declared_slots",
        "support_status",
        "complete_support_claims",
        "failed_request_interval_count",
        "unsupported_request_interval_count",
        "request_status_counts",
        "first_qualified_dedup_policy",
        "source_revision_count",
        "duplicate_observation_count",
        "context_history_length_histogram",
        "sparse_origins",
        "entities_with_sparse_origins",
        "origins_with_unknown_gaps",
        "unknown_gap_edges",
        "consecutive_32_including_origin",
        "entities_with_consecutive_32",
        "availability_basis",
        "event_time_basis",
        "history_basis",
        "point_in_time_eligible",
        "decision_ns",
        "claim_scope",
    }
    if set(summary) != expected_summary_fields:
        raise SparseReferenceError("role_summary_fields")
    obs = _read_lines(files["observations.jsonl"], "observation_jsonl")
    contexts = _read_lines(files["contexts.jsonl"], "context_jsonl")
    support = _read_lines(files["support-intervals.jsonl"], "support_jsonl")
    requests = _read_lines(files["request-windows.jsonl"], "requests_jsonl")
    enrollment = _read_lines(files["enrollment.jsonl"], "enrollment_jsonl")
    ids = {row.get("observation_id") for row in obs}
    if (
        len(ids) != len(obs)
        or len(contexts) != summary.get("origins")
        or len(obs) != summary.get("qualified_bars")
        or len(enrollment) != summary.get("enrollment_entities")
        or len(support) != summary.get("declared_support_interval_count")
        or len(requests) != sum(summary.get("request_status_counts", {}).values())
    ):
        raise SparseReferenceError("role_artifact_counts")
    source_pins = summary.get("source_pins")
    if not isinstance(source_pins, dict) or set(source_pins) != {
        "campaign_receipt_sha256",
        "source_pin",
        "campaign_source_pin",
        "meta_manifest_sha256",
        "rows_sha256",
        "captures_index_sha256",
        "accounting_manifest_sha256",
    }:
        raise SparseReferenceError("role_source_pins")
    for key in set(source_pins) - {"code_sha256"}:
        require_sha256(source_pins[key], "role_source_pin")
    if (
        source_pins["campaign_receipt_sha256"] != CAMPAIGN_RECEIPT_SHA256
        or source_pins["source_pin"] != ROLE_SOURCE_PINS[expected_role]
    ):
        raise SparseReferenceError("role_source_pin_mismatch")
    code_hashes = summary.get("code_sha256")
    if (
        not isinstance(code_hashes, dict)
        or set(code_hashes) != set(CODE_FILES)
        or any(not isinstance(v, str) or len(v) != 64 for v in code_hashes.values())
    ):
        raise SparseReferenceError("role_code_pins")
    for code_hash in code_hashes.values():
        require_sha256(code_hash, "role_code_pin")
    if code_hashes != current_code_hashes():
        raise SparseReferenceError("role_code_pin_mismatch")
    if obs != sorted(obs, key=lambda row: (row.get("entity_id"), row.get("event_time_ns"))):
        raise SparseReferenceError("observations_not_sorted")
    previous_by_entity: dict[str, dict[str, Any]] = {}
    seen_entity_open: set[tuple[str, int]] = set()
    for row in obs:
        if set(row) != {
            "observation_id",
            "entity_id",
            "role",
            "event_time_ns",
            "bar_open_ns",
            "bar_end_ns",
            "full_bar_receipt_available_ns",
            "availability_status",
            "availability_basis",
            "source_pin",
            "source_row_sha256",
            "capture_sha256",
            "modality",
            "units",
            "first_qualified_receipt",
            "observed_spacing_grid_steps",
        }:
            raise SparseReferenceError("observation_fields")
        entity = row["entity_id"]
        start = row["event_time_ns"]
        end = row["bar_end_ns"]
        available = row["full_bar_receipt_available_ns"]
        capture_sha = require_sha256(row["capture_sha256"], "observation_capture_hash")
        require_sha256(row["source_row_sha256"], "observation_row_hash")
        if (
            row["role"] != expected_role
            or not isinstance(entity, str)
            or type(start) is not int
            or start < 0
            or start % (BAR_SECONDS * NS_PER_SECOND)
            or row["bar_open_ns"] != start
            or end != start + BAR_SECONDS * NS_PER_SECOND
            or (available is not None and (type(available) is not int or available < end))
            or row["availability_status"]
            != (
                "receipt_after_bar_end"
                if available is not None
                else "full_bar_availability_unproved"
            )
            or row["source_pin"] != source_pins["source_pin"]
            or row["modality"] != "ohlcv_bar"
            or row["units"]
            != {
                "source_namespace": "geckoterminal/usd/base/5min/first-qualified-receipt",
                "volume_unit": None,
                "volume_unit_status": "provider_reported_unverified",
                "interval_seconds": BAR_SECONDS,
            }
            or row["first_qualified_receipt"] is not True
        ):
            raise SparseReferenceError("observation_time_or_provenance")
        if (entity, start) in seen_entity_open:
            raise SparseReferenceError("duplicate_observation_open")
        seen_entity_open.add((entity, start))
        expected_id = _stable_id(
            {
                "role": expected_role,
                "source_pin": source_pins["source_pin"],
                "entity_id": entity,
                "open_s": start // NS_PER_SECOND,
                "capture_sha256": capture_sha,
            }
        )
        if row["observation_id"] != expected_id:
            raise SparseReferenceError("observation_id_binding")
        previous = previous_by_entity.get(entity)
        expected_spacing = (
            None
            if previous is None
            else (start - previous["event_time_ns"]) // (BAR_SECONDS * NS_PER_SECOND)
        )
        if row["observed_spacing_grid_steps"] != expected_spacing:
            raise SparseReferenceError("observation_spacing")
        previous_by_entity[entity] = row
    observed_by_entity = Counter(row["entity_id"] for row in obs)
    enrollment_entities = {row.get("entity_id") for row in enrollment}
    if (
        len(enrollment_entities) != len(enrollment)
        or any(not isinstance(entity, str) for entity in enrollment_entities)
        or set(observed_by_entity) - enrollment_entities
        or any(
            row.get("role") != expected_role
            or type(row.get("qualified_observation_count")) is not int
            or row.get("qualified_observation_count") != observed_by_entity[row.get("entity_id")]
            for row in enrollment
        )
    ):
        raise SparseReferenceError("enrollment_observation_membership")
    support_slots = 0
    support_by_entity: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in support:
        if set(row) != {"entity_id", "start_ns", "end_ns", "grid_slots", "status", "status_basis"}:
            raise SparseReferenceError("support_row_fields")
        start, end = row.get("start_ns"), row.get("end_ns")
        if (
            row.get("entity_id") not in enrollment_entities
            or type(start) is not int
            or type(end) is not int
            or start % (BAR_SECONDS * NS_PER_SECOND)
            or end % (BAR_SECONDS * NS_PER_SECOND)
            or start >= end
            or row.get("grid_slots") != (end - start) // (BAR_SECONDS * NS_PER_SECOND)
            or row.get("status") != "unknown"
            or row.get("status_basis")
            != "declared_source_interval_without_complete_support_assertion"
        ):
            raise SparseReferenceError("support_interval_shape")
        if any(
            start < other_end and other_start < end
            for other_start, other_end in support_by_entity[row["entity_id"]]
        ):
            raise SparseReferenceError("support_interval_overlap")
        support_by_entity[row["entity_id"]].append((start, end))
        support_slots += row["grid_slots"]
    if (
        support_slots != summary.get("declared_support_slots")
        or set(support_by_entity) != enrollment_entities
        or len(support) != summary.get("declared_support_interval_count")
        or summary.get("complete_support_claims") != 0
        or summary.get("support_status") != "unknown_for_all_declared_intervals"
    ):
        raise SparseReferenceError("support_summary_mismatch")
    if any(
        not _interval_contains(
            [
                [start // NS_PER_SECOND, end // NS_PER_SECOND]
                for start, end in support_by_entity[row["entity_id"]]
            ],
            row["bar_open_ns"] // NS_PER_SECOND,
        )
        for row in obs
    ):
        raise SparseReferenceError("observation_outside_support_interval")
    request_counts: Counter[str] = Counter()
    request_ordinals: set[int] = set()
    for row in requests:
        if set(row) != {
            "ordinal",
            "entity_id",
            "start_ns",
            "end_ns",
            "request_status",
            "acquisition_status",
            "qualification",
            "reported_bars",
            "capture_sha256",
        }:
            raise SparseReferenceError("request_window_fields")
        ordinal, start, end = row["ordinal"], row["start_ns"], row["end_ns"]
        if (
            type(ordinal) is not int
            or ordinal in request_ordinals
            or type(start) is not int
            or type(end) is not int
            or start >= end
            or row.get("entity_id") not in enrollment_entities
            or type(row.get("reported_bars")) is not int
            or row["reported_bars"] < 0
            or (
                row.get("capture_sha256") is not None
                and not isinstance(row.get("capture_sha256"), str)
            )
        ):
            raise SparseReferenceError("request_window_value")
        if row["capture_sha256"] is not None:
            require_sha256(row["capture_sha256"], "request_capture_hash")
        request_ordinals.add(ordinal)
        if (
            _request_class(
                {
                    "acquisition_status": row["acquisition_status"],
                    "qualification": row["qualification"],
                }
            )
            != row["request_status"]
        ):
            raise SparseReferenceError("request_status_binding")
        request_counts[row["request_status"]] += 1
    if (
        any(status not in {"unknown", "failed", "unsupported"} for status in request_counts)
        or requests != sorted(requests, key=lambda row: row["ordinal"])
        or dict(sorted(request_counts.items())) != summary.get("request_status_counts")
    ):
        raise SparseReferenceError("request_summary_mismatch")
    if (
        summary.get("failed_request_interval_count") != request_counts["failed"]
        or summary.get("unsupported_request_interval_count") != request_counts["unsupported"]
    ):
        raise SparseReferenceError("request_failure_denominator_mismatch")
    request_counts_by_entity: dict[str, Counter[str]] = defaultdict(Counter)
    for request in requests:
        request_counts_by_entity[request["entity_id"]][request["request_status"]] += 1
    for enrollment_row in enrollment:
        if set(enrollment_row) != {
            "entity_id",
            "role",
            "status",
            "qualified_observation_count",
            "request_count",
            "request_status_counts",
        }:
            raise SparseReferenceError("enrollment_fields")
        entity = enrollment_row["entity_id"]
        counts = request_counts_by_entity[entity]
        if enrollment_row["request_count"] != sum(counts.values()) or enrollment_row[
            "request_status_counts"
        ] != dict(sorted(counts.items())):
            raise SparseReferenceError("enrollment_request_counts")
        states = set(counts)
        if enrollment_row["qualified_observation_count"]:
            expected_status = "has_qualified_observations"
        elif "unknown" in states:
            expected_status = "no_qualified_observation_in_scope"
        elif {"failed", "unsupported"} <= states:
            expected_status = "failed_and_unsupported_no_observation"
        elif "failed" in states:
            expected_status = "failed_no_observation"
        elif "unsupported" in states:
            expected_status = "unsupported_no_observation"
        else:
            expected_status = "enrolled_without_observation_evidence"
        if enrollment_row["status"] != expected_status:
            raise SparseReferenceError("enrollment_status_mismatch")
    indices_by_entity: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(obs):
        indices_by_entity[row["entity_id"]].append(index)
    seen_origins: set[int] = set()
    seen_contexts: set[str] = set()
    for context in contexts:
        if set(context) != {
            "context_id",
            "entity_id",
            "role",
            "origin_index",
            "origin_event_time_ns",
            "history_indices",
            "elapsed_grid_steps",
            "unknown_gap_mask",
            "history_length",
            "censor_reasons",
            "point_in_time_eligible",
            "decision_ns",
        }:
            raise SparseReferenceError("context_fields")
        if (
            context.get("role") != expected_role
            or context.get("point_in_time_eligible") is not False
            or context.get("decision_ns") is not None
        ):
            raise SparseReferenceError("context_origin_binding")
        origin_index = context.get("origin_index")
        if (
            type(origin_index) is not int
            or not 0 <= origin_index < len(obs)
            or origin_index in seen_origins
        ):
            raise SparseReferenceError("context_origin_index")
        seen_origins.add(origin_index)
        origin = obs[origin_index]
        if context.get("origin_event_time_ns") != origin.get("event_time_ns"):
            raise SparseReferenceError("context_origin_time")
        history_indices = context.get("history_indices")
        steps = context.get("elapsed_grid_steps")
        mask = context.get("unknown_gap_mask")
        if (
            not isinstance(history_indices, list)
            or len(history_indices) > MAX_HISTORY
            or context.get("history_length") != len(history_indices)
            or not isinstance(steps, list)
            or len(steps) != len(history_indices)
            or not isinstance(mask, list)
            or len(mask) != len(history_indices)
            or any(type(ref) is not int or not 0 <= ref < len(obs) for ref in history_indices)
            or history_indices != sorted(history_indices)
            or any(ref >= origin_index for ref in history_indices)
        ):
            raise SparseReferenceError("context_history_shape")
        expected_history_indices = [
            index for index in indices_by_entity[origin["entity_id"]] if index < origin_index
        ][-MAX_HISTORY:]
        if history_indices != expected_history_indices:
            raise SparseReferenceError("context_not_maximal_prior_history")
        history_rows = [obs[ref] for ref in history_indices]
        if any(
            r["entity_id"] != origin["entity_id"] or r["role"] != expected_role
            for r in history_rows
        ):
            raise SparseReferenceError("context_cross_entity_or_role")
        if any(r["event_time_ns"] >= origin["event_time_ns"] for r in history_rows):
            raise SparseReferenceError("context_future_bar")
        path = [*history_rows, origin]
        differences = [
            path[i + 1]["event_time_ns"] - path[i]["event_time_ns"] for i in range(len(path) - 1)
        ]
        if any(delta <= 0 or delta % (BAR_SECONDS * NS_PER_SECOND) for delta in differences):
            raise SparseReferenceError("context_grid_alignment")
        expected_steps = [delta // (BAR_SECONDS * NS_PER_SECOND) for delta in differences]
        if expected_steps != steps or [x > 1 for x in expected_steps] != mask:
            raise SparseReferenceError("context_elapsed_or_gap_mask")
        expected_reasons = []
        if len(history_indices) < MAX_HISTORY:
            expected_reasons.append("fewer_than_32_observed_prior_bars")
        if any(mask):
            expected_reasons.append("unknown_elapsed_gap_between_observed_bars")
        if context.get("censor_reasons") != expected_reasons:
            raise SparseReferenceError("context_censor_reasons")
        if context.get("entity_id") != origin["entity_id"]:
            raise SparseReferenceError("context_entity_binding")
        expected_context_id = _stable_id(
            {
                "role": expected_role,
                "entity_id": origin["entity_id"],
                "origin_id": origin["observation_id"],
            }
        )
        cid = context.get("context_id")
        if cid != expected_context_id:
            raise SparseReferenceError("context_id_binding")
        if cid in seen_contexts:
            raise SparseReferenceError("duplicate_context")
        seen_contexts.add(cid)
    history_histogram = Counter(context["history_length"] for context in contexts)
    if {str(i): history_histogram[i] for i in range(MAX_HISTORY + 1)} != summary.get(
        "context_history_length_histogram"
    ):
        raise SparseReferenceError("context_histogram_mismatch")
    contexts_by_origin = {context["origin_index"]: context for context in contexts}
    if len(contexts_by_origin) != len(obs):
        raise SparseReferenceError("context_origin_coverage")
    sparse_origins = 0
    gap_origins = 0
    gap_edges = 0
    for context in contexts:
        has_gap = any(context["unknown_gap_mask"])
        sparse_origins += int(context["history_length"] < MAX_HISTORY or has_gap)
        gap_origins += int(has_gap)
        gap_edges += sum(context["unknown_gap_mask"])
    baseline = 0
    baseline_entities: set[str] = set()
    by_entity: dict[str, list[int]] = defaultdict(list)
    for row in obs:
        by_entity[row["entity_id"]].append(row["event_time_ns"])
    for entity, times in by_entity.items():
        for index in range(31, len(times)):
            if times[index] - times[index - 31] == 31 * BAR_SECONDS * NS_PER_SECOND:
                baseline += 1
                baseline_entities.add(entity)
    if (
        sparse_origins != summary.get("sparse_origins")
        or sum(1 for row in enrollment if row["qualified_observation_count"] > 0)
        != summary.get("entities_with_qualified_bars")
        or sum(1 for row in enrollment if row["qualified_observation_count"] == 0)
        != summary.get("entities_without_qualified_bars")
        or gap_origins != summary.get("origins_with_unknown_gaps")
        or gap_edges != summary.get("unknown_gap_edges")
        or baseline != summary.get("consecutive_32_including_origin")
        or len(baseline_entities) != summary.get("entities_with_consecutive_32")
        or summary.get("qualified_observed_bar_slots") != len(obs)
        or summary.get("unobserved_declared_slots") != support_slots - len(obs)
        or summary.get("first_qualified_dedup_policy") != "first_qualified_receipt_then_capture_sha"
        or summary.get("code_sha256") != code_hashes
        or type(summary.get("source_revision_count")) is not int
        or summary["source_revision_count"] < 0
        or type(summary.get("duplicate_observation_count")) is not int
        or summary["duplicate_observation_count"] < 0
        or summary.get("entities_with_sparse_origins")
        != len(
            {
                context["entity_id"]
                for context in contexts
                if context["history_length"] < MAX_HISTORY or any(context["unknown_gap_mask"])
            }
        )
        or summary.get("point_in_time_eligible") is not False
        or summary.get("decision_ns") is not None
    ):
        raise SparseReferenceError("role_summary_recomputation")
    # Validation is role-local; campaign reconciliation only consumes these summaries.
    return cast(dict[str, Any], summary)


def _validate_campaign_role_summary(role: str, summary: dict[str, Any]) -> None:
    expected = ROLE_EXPECTED[role]
    expected_values = {
        "enrollment_entities": expected["entities"],
        "qualified_bars": expected["bars"],
        "origins": expected["bars"],
        "declared_support_slots": expected["support_slots"],
        "consecutive_32_including_origin": expected["consecutive32"],
        "entities_with_consecutive_32": expected["consecutive32_entities"],
    }
    source_pins = summary.get("source_pins")
    code_hashes = summary.get("code_sha256")
    histogram = summary.get("context_history_length_histogram")
    if (
        set(summary)
        != {
            "schema",
            "schema_version",
            "role",
            "source_pins",
            "code_sha256",
            "enrollment_entities",
            "qualified_bars",
            "origins",
            "entities_with_qualified_bars",
            "entities_without_qualified_bars",
            "declared_support_interval_count",
            "declared_support_slots",
            "qualified_observed_bar_slots",
            "unobserved_declared_slots",
            "support_status",
            "complete_support_claims",
            "failed_request_interval_count",
            "unsupported_request_interval_count",
            "request_status_counts",
            "first_qualified_dedup_policy",
            "source_revision_count",
            "duplicate_observation_count",
            "context_history_length_histogram",
            "sparse_origins",
            "entities_with_sparse_origins",
            "origins_with_unknown_gaps",
            "unknown_gap_edges",
            "consecutive_32_including_origin",
            "entities_with_consecutive_32",
            "availability_basis",
            "event_time_basis",
            "history_basis",
            "point_in_time_eligible",
            "decision_ns",
            "claim_scope",
        }
        or summary.get("schema") != ROLE_SCHEMA
        or summary.get("schema_version") != SCHEMA_VERSION
        or summary.get("role") != role
        or not isinstance(source_pins, dict)
        or set(source_pins)
        != {
            "campaign_receipt_sha256",
            "source_pin",
            "campaign_source_pin",
            "meta_manifest_sha256",
            "rows_sha256",
            "captures_index_sha256",
            "accounting_manifest_sha256",
        }
        or source_pins.get("campaign_receipt_sha256") != CAMPAIGN_RECEIPT_SHA256
        or source_pins.get("source_pin") != ROLE_SOURCE_PINS[role]
        or not isinstance(code_hashes, dict)
        or set(code_hashes) != set(CODE_FILES)
        or code_hashes != current_code_hashes()
        or not isinstance(histogram, dict)
        or set(histogram) != {str(i) for i in range(MAX_HISTORY + 1)}
        or any(type(value) is not int or value < 0 for value in histogram.values())
        or sum(histogram.values()) != expected["bars"]
        or any(summary.get(key) != value for key, value in expected_values.items())
        or summary.get("qualified_observed_bar_slots") != expected["bars"]
        or summary.get("support_status") != "unknown_for_all_declared_intervals"
        or summary.get("complete_support_claims") != 0
        or summary.get("point_in_time_eligible") is not False
        or summary.get("decision_ns") is not None
        or summary.get("first_qualified_dedup_policy") != "first_qualified_receipt_then_capture_sha"
        or summary.get("declared_support_interval_count", 0) < expected["entities"]
        or summary.get("unobserved_declared_slots") != expected["support_slots"] - expected["bars"]
        or summary.get("entities_with_qualified_bars", 0)
        + summary.get("entities_without_qualified_bars", 0)
        != expected["entities"]
    ):
        raise SparseReferenceError("campaign_role_summary_invalid")
    for name in (
        "campaign_source_pin",
        "meta_manifest_sha256",
        "rows_sha256",
        "captures_index_sha256",
        "accounting_manifest_sha256",
    ):
        require_sha256(source_pins.get(name), "campaign_role_source_pin")
    for value in code_hashes.values():
        require_sha256(value, "campaign_role_code_pin")


def compile_campaign(train_summary: dict[str, Any], eval_summary: dict[str, Any]) -> dict[str, Any]:
    if (
        train_summary.get("schema") != ROLE_SCHEMA
        or train_summary.get("role") != "train"
        or eval_summary.get("schema") != ROLE_SCHEMA
        or eval_summary.get("role") != "research_evaluation"
    ):
        raise SparseReferenceError("campaign_role_identity")
    _validate_campaign_role_summary("train", train_summary)
    _validate_campaign_role_summary("research_evaluation", eval_summary)
    train_pin = require_sha256(train_summary["source_pins"]["source_pin"])
    eval_pin = require_sha256(eval_summary["source_pins"]["source_pin"])
    train_code = train_summary.get("code_sha256")
    eval_code = eval_summary.get("code_sha256")
    if (
        train_pin != ROLE_SOURCE_PINS["train"]
        or eval_pin != ROLE_SOURCE_PINS["research_evaluation"]
        or train_pin == eval_pin
        or train_code != eval_code
        or not isinstance(train_code, dict)
        or set(train_code) != set(CODE_FILES)
    ):
        raise SparseReferenceError("campaign_role_source_collision")
    totals = {
        key: train_summary[key] + eval_summary[key]
        for key in (
            "enrollment_entities",
            "qualified_bars",
            "declared_support_interval_count",
            "declared_support_slots",
            "qualified_observed_bar_slots",
            "unobserved_declared_slots",
            "consecutive_32_including_origin",
        )
    }
    histogram = {
        str(i): train_summary["context_history_length_histogram"][str(i)]
        + eval_summary["context_history_length_histogram"][str(i)]
        for i in range(MAX_HISTORY + 1)
    }
    expected_total = {
        "enrollment_entities": 307,
        "qualified_bars": 53_080,
        "consecutive_32_including_origin": 15_257,
    }
    if any(totals[k] != v for k, v in expected_total.items()):
        raise SparseReferenceError("campaign_known_baseline_mismatch")
    if (
        train_summary["consecutive_32_including_origin"] != 8_792
        or eval_summary["consecutive_32_including_origin"] != 6_465
    ):
        raise SparseReferenceError("campaign_role_baseline_mismatch")
    return {
        "schema": CAMPAIGN_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "campaign_receipt_path": CAMPAIGN_RECEIPT,
        "campaign_receipt_sha256": CAMPAIGN_RECEIPT_SHA256,
        "roles": {
            "train": {
                "source_pin": train_pin,
                "enrollment_entities": train_summary["enrollment_entities"],
                "qualified_bars": train_summary["qualified_bars"],
                "consecutive_32_including_origin": train_summary["consecutive_32_including_origin"],
            },
            "research_evaluation": {
                "source_pin": eval_pin,
                "enrollment_entities": eval_summary["enrollment_entities"],
                "qualified_bars": eval_summary["qualified_bars"],
                "consecutive_32_including_origin": eval_summary["consecutive_32_including_origin"],
            },
        },
        "totals": totals,
        "code_sha256": train_code,
        "context_history_length_histogram": histogram,
        "point_in_time_eligible": False,
        "decision_ns": None,
        "claim_scope": (
            "historical_replay_structure_only_not_market_census_activity_zero_lifecycle_"
            "settlement_labels_edge_or_training_admission"
        ),
        "role_payload_reconciliation": (
            "each role compiled and verified in an isolated invocation; "
            "this manifest reads role summaries only"
        ),
    }


def write_create_only(writer: DescriptorWriter, path: str, raw: bytes) -> None:
    if writer.exists(path):
        if writer.read_bytes(path, max_bytes=max(len(raw), 256 * 1024 * 1024)) != raw:
            raise SparseReferenceError("immutable_output_collision")
        return
    writer.atomic_write(path, raw, create_only=True)


def publish_role(writer: DescriptorWriter, prefix: str, compiled: dict[str, Any]) -> dict[str, Any]:
    role = compiled["summary"]["role"]
    if role not in ROLES:
        raise SparseReferenceError("publish_role_identity")
    files = serialize_role(compiled)
    verified = verify_role_artifact(files, expected_role=role)
    role_prefix = f"{prefix}/{role}"
    writer.mkdir(role_prefix)
    for name, raw in files.items():
        write_create_only(writer, f"{role_prefix}/{name}", raw)
    manifest = {
        "schema": ROLE_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "role": role,
        "files": {name: digest(raw) for name, raw in sorted(files.items())},
        "source_pins": verified["source_pins"],
        "code_sha256": verified["code_sha256"],
    }
    manifest_bytes = canonical(manifest)
    write_create_only(writer, f"{role_prefix}/manifest.json", manifest_bytes)
    persisted = verify_role_from_writer(writer, role_prefix, role)
    if persisted != verified:
        raise SparseReferenceError("published_role_readback_mismatch")
    return {"manifest": manifest, "manifest_sha256": digest(manifest_bytes), "summary": persisted}


def verify_role_from_writer(
    writer: DescriptorWriter, role_prefix: str, role: str
) -> dict[str, Any]:
    manifest_raw = writer.read_bytes(f"{role_prefix}/manifest.json", max_bytes=2 * 1024 * 1024)
    manifest = _json_bytes(manifest_raw, "role_manifest_json")
    names = {
        "observations.jsonl",
        "contexts.jsonl",
        "support-intervals.jsonl",
        "request-windows.jsonl",
        "enrollment.jsonl",
        "summary.json",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest)
        != {"schema", "schema_version", "role", "files", "source_pins", "code_sha256"}
        or manifest.get("schema") != ROLE_SCHEMA
        or manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("role") != role
        or set(manifest.get("files", {})) != names
    ):
        raise SparseReferenceError("role_manifest_shape")
    files = {
        name: writer.read_bytes(f"{role_prefix}/{name}", max_bytes=256 * 1024 * 1024)
        for name in sorted(names)
    }
    if {name: digest(raw) for name, raw in files.items()} != manifest["files"]:
        raise SparseReferenceError("role_manifest_hash_mismatch")
    summary = verify_role_artifact(files, expected_role=role)
    if (
        manifest["source_pins"] != summary["source_pins"]
        or manifest["code_sha256"] != summary["code_sha256"]
    ):
        raise SparseReferenceError("role_manifest_source_mismatch")
    return summary


def _campaign_role_summary(
    writer: DescriptorWriter, prefix: str, role: str
) -> tuple[dict[str, Any], dict[str, str]]:
    role_prefix = f"{prefix}/{role}"
    raw_manifest = writer.read_bytes(f"{role_prefix}/manifest.json", max_bytes=2 * 1024 * 1024)
    manifest = _json_bytes(raw_manifest, "role_manifest_json")
    summary_raw = writer.read_bytes(f"{role_prefix}/summary.json", max_bytes=2 * 1024 * 1024)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != ROLE_SCHEMA
        or manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("role") != role
        or not isinstance(manifest.get("files"), dict)
        or not isinstance(manifest.get("code_sha256"), dict)
        or manifest["files"].get("summary.json") != digest(summary_raw)
    ):
        raise SparseReferenceError("campaign_role_summary_binding")
    summary = _json_bytes(summary_raw, "role_summary_json")
    if (
        not isinstance(summary, dict)
        or summary.get("schema") != ROLE_SCHEMA
        or summary.get("schema_version") != SCHEMA_VERSION
        or summary.get("role") != role
        or summary.get("source_pins") != manifest.get("source_pins")
        or summary.get("code_sha256") != manifest.get("code_sha256")
    ):
        raise SparseReferenceError("campaign_role_summary_identity")
    return summary, {"manifest_sha256": digest(raw_manifest), "summary_sha256": digest(summary_raw)}


def publish_campaign_summary(
    writer: DescriptorWriter,
    prefix: str,
    train_summary: dict[str, Any],
    eval_summary: dict[str, Any],
    *,
    root_id: str,
    volume_uuid: str,
    role_bindings: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    campaign = compile_campaign(train_summary, eval_summary)
    campaign["storage"] = {"root_id": root_id, "volume_uuid": volume_uuid}
    campaign["role_artifact_pins"] = role_bindings or {}
    raw = canonical(campaign)
    write_create_only(writer, f"{prefix}/campaign-summary.json", raw)
    files = {"campaign-summary.json": digest(raw)}
    manifest = {"schema": CAMPAIGN_SCHEMA, "schema_version": SCHEMA_VERSION, "files": files}
    manifest_raw = canonical(manifest)
    write_create_only(writer, f"{prefix}/manifest.json", manifest_raw)
    return {"campaign": campaign, "manifest": manifest, "manifest_sha256": digest(manifest_raw)}


def seal_campaign_from_writer(
    writer: DescriptorWriter,
    prefix: str,
    *,
    root_id: str,
    volume_uuid: str,
) -> dict[str, Any]:
    # Only role summaries and their manifest pins are opened here; row/context payloads
    # remain role-local and are independently verified through verify_role_from_writer.
    train, train_binding = _campaign_role_summary(writer, prefix, "train")
    evaluation, evaluation_binding = _campaign_role_summary(writer, prefix, "research_evaluation")
    result = publish_campaign_summary(
        writer,
        prefix,
        train,
        evaluation,
        root_id=root_id,
        volume_uuid=volume_uuid,
        role_bindings={"train": train_binding, "research_evaluation": evaluation_binding},
    )
    verified = verify_campaign_from_writer(writer, prefix, root_id=root_id, volume_uuid=volume_uuid)
    if verified != result["campaign"]:
        raise SparseReferenceError("campaign_readback_mismatch")
    return result


def verify_campaign_from_writer(
    writer: DescriptorWriter,
    prefix: str,
    *,
    root_id: str,
    volume_uuid: str,
) -> dict[str, Any]:
    manifest_raw = writer.read_bytes(f"{prefix}/manifest.json", max_bytes=2 * 1024 * 1024)
    manifest = _json_bytes(manifest_raw, "campaign_manifest_json")
    summary_raw = writer.read_bytes(f"{prefix}/campaign-summary.json", max_bytes=2 * 1024 * 1024)
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schema", "schema_version", "files"}
        or manifest.get("schema") != CAMPAIGN_SCHEMA
        or manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("files") != {"campaign-summary.json": digest(summary_raw)}
    ):
        raise SparseReferenceError("campaign_manifest_binding")
    stored = _json_bytes(summary_raw, "campaign_summary_json")
    train, train_binding = _campaign_role_summary(writer, prefix, "train")
    evaluation, eval_binding = _campaign_role_summary(writer, prefix, "research_evaluation")
    expected = compile_campaign(train, evaluation)
    expected["storage"] = {"root_id": root_id, "volume_uuid": volume_uuid}
    expected["role_artifact_pins"] = {"train": train_binding, "research_evaluation": eval_binding}
    if stored != expected:
        raise SparseReferenceError("campaign_summary_recomputation")
    return cast(dict[str, Any], stored)


def _expected_source_for_role(role: str) -> tuple[Path, dict[str, Any], dict[str, str]]:
    if role not in ROLES:
        raise SparseReferenceError("invalid_role")
    receipt_raw = _read_plain(REPO / CAMPAIGN_RECEIPT, 4_000_000)
    if digest(receipt_raw) != CAMPAIGN_RECEIPT_SHA256:
        raise SparseReferenceError("campaign_receipt_pin")
    receipt = _json_bytes(receipt_raw, "campaign_receipt_json")
    coverage_raw = _read_plain(REPO / CAMPAIGN_COVERAGE, 4_000_000)
    if digest(coverage_raw) != receipt.get("payloads", {}).get("learning/coverage.json"):
        raise SparseReferenceError("campaign_coverage_pin")
    coverage = _json_bytes(coverage_raw, "campaign_coverage_json")
    expected_pin = ROLE_SOURCE_PINS[role]
    if coverage.get("roles", {}).get(role, {}).get("source") != expected_pin:
        raise SparseReferenceError("role_source_pin")
    # Role bundle roots are fixed, preventing accidental access to the other role tree.
    suffix = "train-source" if role == "train" else "research_evaluation-source"
    root = REPO / "work/brain-diverse-campaign-20261008/learning" / suffix
    pins = {
        "campaign_receipt_sha256": CAMPAIGN_RECEIPT_SHA256,
        "source_pin": expected_pin,
        "campaign_source_pin": receipt["source"][role]["accounting"]["source_pin"],
        "code_sha256": current_code_hashes(),
    }
    return root, receipt, pins


def load_role_source(
    role: str,
) -> tuple[dict[str, Any], list[bytes], list[bytes], list[dict[str, Any]], dict[str, str]]:
    root, receipt, pins = _expected_source_for_role(role)
    if root.is_symlink() or not root.is_dir():
        raise SparseReferenceError("role_bundle_missing_or_symlink")
    meta = root / "source/meta"
    accounting = root / "accounting"
    meta_manifest_raw = _read_plain(meta / "manifest.json", 1024 * 1024)
    if digest(meta_manifest_raw) != pins["source_pin"]:
        raise SparseReferenceError("role_bundle_manifest_pin")
    meta_manifest = _json_bytes(meta_manifest_raw, "meta_manifest_json")
    if set(meta_manifest) != {"captures.jsonl", "rows.jsonl", "source.json"}:
        raise SparseReferenceError("meta_manifest_fields")
    source_raw = _read_plain(meta / "source.json", 8 * 1024 * 1024)
    captures_raw = _read_plain(meta / "captures.jsonl", 64 * 1024 * 1024)
    rows_raw = _read_plain(meta / "rows.jsonl", 256 * 1024 * 1024)
    if {
        "source.json": digest(source_raw),
        "captures.jsonl": digest(captures_raw),
        "rows.jsonl": digest(rows_raw),
    } != meta_manifest:
        raise SparseReferenceError("meta_manifest_file_hash")
    source = _json_bytes(source_raw, "source_json")
    if source.get("plan", {}).get("role") != role:
        raise SparseReferenceError("role_source_payload_mismatch")
    account_manifest_raw = _read_plain(accounting / "manifest.json", 1024 * 1024)
    account_manifest = _json_bytes(account_manifest_raw, "accounting_manifest_json")
    if set(account_manifest) != {"plan.json", "report.json", "requests.jsonl"}:
        raise SparseReferenceError("accounting_manifest_fields")
    account_files = {
        name: _read_plain(accounting / name, 16 * 1024 * 1024) for name in account_manifest
    }
    if {name: digest(raw) for name, raw in account_files.items()} != account_manifest:
        raise SparseReferenceError("accounting_manifest_file_hash")
    report = _json_bytes(account_files["report.json"], "accounting_report_json")
    if (
        report.get("role") != role
        or report.get("bars") != source.get("bars")
        or report.get("source_pin") != pins["source_pin"]
        or report.get("historical_online_availability_known") is not False
    ):
        raise SparseReferenceError("accounting_source_binding")
    pools = source.get("plan", {}).get("pools")
    if not isinstance(pools, list) or len(pools) != receipt["enrollment"]["roles"][role]:
        raise SparseReferenceError("role_enrollment_count")
    expected = ROLE_EXPECTED[role]
    if len(pools) != expected["entities"] or source.get("bars") != expected["bars"]:
        raise SparseReferenceError("role_known_baseline")
    request_records = _read_lines(account_files["requests.jsonl"], "request_rows")
    pins.update(
        {
            "meta_manifest_sha256": digest(meta_manifest_raw),
            "rows_sha256": digest(rows_raw),
            "captures_index_sha256": digest(captures_raw),
            "accounting_manifest_sha256": digest(account_manifest_raw),
        }
    )
    return source, rows_raw.splitlines(), captures_raw.splitlines(), request_records, pins


def compile_role_from_pinned_bundle(role: str) -> dict[str, Any]:
    source, rows, captures, requests, pins = load_role_source(role)
    compiled = compile_role(role, source, rows, captures, requests, source_pins=pins)
    expected = ROLE_EXPECTED[role]
    if (
        compiled["summary"]["declared_support_slots"] != expected["support_slots"]
        or compiled["summary"]["consecutive_32_including_origin"] != expected["consecutive32"]
        or compiled["summary"]["entities_with_consecutive_32"] != expected["consecutive32_entities"]
    ):
        raise SparseReferenceError("role_coverage_baseline")
    return compiled


def publish_role_to_storage(
    compiled: dict[str, Any],
    *,
    config_path: Path,
    prefix: str = DEFAULT_OUTPUT_PREFIX,
) -> dict[str, Any]:
    role_files = serialize_role(compiled)
    budget = sum(map(len, role_files.values())) + 1_000_000 + METADATA_MARGIN_BYTES
    required_free = MIN_FREE_BYTES + budget
    with writer_guard(config_path, min_free_bytes=required_free) as guard:

        def revalidate() -> None:
            guard.revalidate(min_free_bytes=required_free)

        root_fd = _open_data_root_descriptor(guard)
        try:
            with DescriptorWriter(root_fd, before_write=revalidate) as writer:
                with writer.lock(blocking=True):
                    return publish_role(writer, prefix, compiled)
        finally:
            os.close(root_fd)


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    compile_parser = sub.add_parser("compile-role")
    compile_parser.add_argument("--role", choices=ROLES, required=True)
    compile_parser.add_argument(
        "--storage-config",
        type=Path,
        default=Path.home() / ".config/brain-research/storage-v1.json",
    )
    compile_parser.add_argument("--prefix", default=DEFAULT_OUTPUT_PREFIX)
    for name in ("verify-role", "seal-campaign", "verify-campaign"):
        child = sub.add_parser(name)
        child.add_argument(
            "--storage-config",
            type=Path,
            default=Path.home() / ".config/brain-research/storage-v1.json",
        )
        child.add_argument("--prefix", default=DEFAULT_OUTPUT_PREFIX)
        if name == "verify-role":
            child.add_argument("--role", choices=ROLES, required=True)
    args = parser.parse_args()
    try:
        if args.command == "compile-role":
            compiled = compile_role_from_pinned_bundle(args.role)
            result = publish_role_to_storage(
                compiled, config_path=args.storage_config, prefix=args.prefix
            )
            status = {
                "status": "published_role",
                "role": args.role,
                "summary": result["summary"],
                "manifest_sha256": result["manifest_sha256"],
            }
        else:
            required = (
                MIN_FREE_BYTES
                + METADATA_MARGIN_BYTES
                + (1_000_000 if args.command == "seal-campaign" else 0)
            )
            with writer_guard(args.storage_config, min_free_bytes=required) as guard:

                def revalidate() -> None:
                    guard.revalidate(min_free_bytes=required)

                root_fd = _open_data_root_descriptor(guard)
                try:
                    with DescriptorWriter(root_fd, before_write=revalidate) as writer:
                        with writer.lock(blocking=True):
                            prefix = args.prefix
                            if args.command == "verify-role":
                                verified = verify_role_from_writer(
                                    writer, f"{prefix}/{args.role}", args.role
                                )
                                status = {
                                    "status": "verified_role",
                                    "role": args.role,
                                    "summary": verified,
                                }
                            elif args.command == "seal-campaign":
                                result = seal_campaign_from_writer(
                                    writer,
                                    prefix,
                                    root_id=guard.root_id,
                                    volume_uuid=guard.volume_uuid,
                                )
                                status = {
                                    "status": "published_campaign",
                                    "campaign": result["campaign"],
                                    "manifest_sha256": result["manifest_sha256"],
                                }
                            else:
                                verified_campaign = verify_campaign_from_writer(
                                    writer,
                                    prefix,
                                    root_id=guard.root_id,
                                    volume_uuid=guard.volume_uuid,
                                )
                                status = {
                                    "status": "verified_campaign",
                                    "campaign": verified_campaign,
                                }
                finally:
                    os.close(root_fd)
        print(json.dumps(status, sort_keys=True))
        return 0
    except (OSError, StorageError, SparseReferenceError, KeyError, TypeError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "failed", "error_class": type(exc).__name__, "reason": str(exc)},
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(_main())
