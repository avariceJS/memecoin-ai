#!/usr/bin/env python3
"""Canonical schema primitives for the offline Alchemy corroboration lane."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from tools import brain_senses_alchemy_corroboration_policy_v1 as policy

FRAME_SCHEMA = "brain-senses-helius-v4-frame-projection-v2"
PIN_SCHEMA = "brain-senses-alchemy-corroboration-frame-pin-v2"
EVENT_SCHEMA = "brain-senses-alchemy-corroboration-event-v1"
MAX_PROJECTION_BYTES = 1_000_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BUCKET = re.compile(r"[0-9]{8}T[0-9]{2}0000Z\Z")
_EPOCH_ID = re.compile(r"([0-9]{8}T[0-9]{6}Z)-([0-9a-f]{12})\Z")
_PROJECTION_FIELDS = {
    "schema",
    "schema_version",
    "helius_service_id",
    "epoch_id",
    "bucket",
    "frame_index",
    "seal_event_sha256",
    "interval_start_slot",
    "interval_end_slot",
    "inventory_slots",
    "inventory_response_sha256",
    "selection_artifact_sha256",
    "selected_slots",
    "request_captures",
    "helius_signature_captures",
}
_EVENT_KINDS = frozenset(
    {
        "frame_admitted",
        "frame_unobserved",
        "request_intent",
        "request_captured",
        "request_failed",
        "frame_completed",
        "frame_stopped",
    }
)


class SchemaError(ValueError):
    """Invalid canonical input or event chain."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode()


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise SchemaError("json_duplicate_key")
        output[key] = value
    return output


def _reject_constant(value: str) -> None:
    raise SchemaError("json_nonfinite")


def decode(payload: bytes) -> Any:
    try:
        return json.loads(payload, object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError("json_invalid") from exc


def bucket_start_ns(bucket: Any) -> int:
    if not isinstance(bucket, str) or not _BUCKET.fullmatch(bucket):
        raise SchemaError("helius_bucket_invalid")
    try:
        parsed = datetime.strptime(bucket, "%Y%m%dT%H0000Z").replace(tzinfo=UTC)
        return int(parsed.timestamp()) * policy.SECOND_NS
    except (OverflowError, OSError, ValueError) as exc:
        raise SchemaError("helius_bucket_invalid") from exc


def validate_projection(value: Any) -> dict[str, Any]:
    """Validate the exact hash-bound v4 projection shape and internal bindings."""
    if not isinstance(value, dict) or set(value) != _PROJECTION_FIELDS:
        raise SchemaError("helius_projection_shape_invalid")
    bucket_start = bucket_start_ns(value.get("bucket"))
    epoch_value = value.get("epoch_id")
    epoch_match = _EPOCH_ID.fullmatch(epoch_value) if isinstance(epoch_value, str) else None
    interval_start, interval_end = value.get("interval_start_slot"), value.get("interval_end_slot")
    inventory, selected, captures = (
        value.get("inventory_slots"),
        value.get("selected_slots"),
        value.get("request_captures"),
    )
    digest_fields = (
        "seal_event_sha256",
        "inventory_response_sha256",
        "selection_artifact_sha256",
    )
    signature_captures = value.get("helius_signature_captures")
    if (
        value.get("schema") != FRAME_SCHEMA
        or value.get("schema_version") != 2
        or value.get("helius_service_id") != "external-rolling-v4"
        or not isinstance(value.get("epoch_id"), str)
        or epoch_match is None
        or type(value.get("frame_index")) is not int
        or not 0 <= value["frame_index"] < 24
        or any(
            not isinstance(value.get(key), str) or not _SHA256.fullmatch(value[key])
            for key in digest_fields
        )
        or type(interval_start) is not int
        or type(interval_end) is not int
        or interval_start < 0
        or interval_end < interval_start
        or not isinstance(inventory, list)
        or any(
            type(slot) is not int or not interval_start <= slot <= interval_end
            for slot in inventory
        )
        or inventory != sorted(set(inventory))
        or not isinstance(selected, list)
        or any(type(slot) is not int or slot not in inventory for slot in selected)
        or selected != sorted(set(selected))
        or len(selected) > policy.MAX_HELIUS_SELECTED_SLOTS
        or not isinstance(captures, list)
        or len(captures) != 2 + 2 * len(selected)
        or not isinstance(signature_captures, list)
        or len(signature_captures) != len(selected)
    ):
        raise SchemaError("helius_projection_invalid")
    try:
        anchor = datetime.strptime(epoch_match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        expected_bucket_start = (
            int(anchor.timestamp()) * policy.SECOND_NS + (value["frame_index"] + 1) * policy.HOUR_NS
        )
    except (OverflowError, OSError, ValueError) as exc:
        raise SchemaError("helius_projection_epoch_invalid") from exc
    if bucket_start != expected_bucket_start:
        raise SchemaError("helius_projection_epoch_bucket_mismatch")
    for key in digest_fields:
        if not _SHA256.fullmatch(value[key]):
            raise SchemaError("helius_projection_digest_invalid")
    request_ids: list[str] = []
    for index, capture in enumerate(captures):
        if not isinstance(capture, dict) or set(capture) != {
            "request_id",
            "method",
            "response_sha256",
            "receipt_sha256",
        }:
            raise SchemaError("helius_projection_capture_invalid")
        method = "getSlot" if index == 0 else "getBlocks" if index == 1 else "getBlock"
        if (
            not isinstance(capture["request_id"], str)
            or not capture["request_id"]
            or capture["method"] != method
            or any(
                not isinstance(capture[key], str) or not _SHA256.fullmatch(capture[key])
                for key in ("response_sha256", "receipt_sha256")
            )
        ):
            raise SchemaError("helius_projection_capture_invalid")
        request_ids.append(capture["request_id"])
    if len(request_ids) != len(set(request_ids)):
        raise SchemaError("helius_projection_request_ids_duplicate")
    if captures[1]["response_sha256"] != value["inventory_response_sha256"]:
        raise SchemaError("helius_projection_inventory_capture_mismatch")
    for index, source in enumerate(signature_captures):
        if not isinstance(source, dict) or set(source) != {
            "slot",
            "request_id",
            "signatures_sha256",
            "signature_count",
        }:
            raise SchemaError("helius_projection_signature_capture_invalid")
        request_capture = captures[2 + index * 2]
        if (
            type(source["slot"]) is not int
            or source["slot"] != selected[index]
            or source["request_id"] != request_capture["request_id"]
            or not isinstance(source["signatures_sha256"], str)
            or not _SHA256.fullmatch(source["signatures_sha256"])
            or type(source["signature_count"]) is not int
            or source["signature_count"] < 0
        ):
            raise SchemaError("helius_projection_signature_capture_invalid")
    return value


def build_pin(
    *,
    projection_bytes: bytes,
    helius_available_ns: int,
    verified_ns: int,
) -> dict[str, Any]:
    """Bind bounded canonical projection bytes plus separate chronology metadata."""
    if len(projection_bytes) > MAX_PROJECTION_BYTES:
        raise SchemaError("helius_projection_oversized_unobserved")
    projection = decode(projection_bytes)
    try:
        validate_projection(projection)
    except SchemaError:
        raise
    if canonical(projection) != projection_bytes:
        raise SchemaError("helius_projection_not_canonical")
    if type(helius_available_ns) is not int or type(verified_ns) is not int:
        raise SchemaError("helius_projection_signature_or_time_invalid")
    bucket_ns = bucket_start_ns(projection["bucket"])
    window = policy.frame_window(bucket_ns)
    if (
        not helius_available_ns > 0
        or not helius_available_ns <= verified_ns <= window.seal_deadline_ns
    ):
        raise SchemaError("helius_projection_verification_late")
    projection_sha = sha(projection_bytes)
    frame_id = f"{projection['epoch_id']}:{projection['bucket']}:{projection['frame_index']}"
    pin_body = {
        "projection": projection,
        "projection_sha256": projection_sha,
        "helius_available_ns": helius_available_ns,
        "verified_ns": verified_ns,
        "bucket_start_ns": bucket_ns,
        "frame_id": frame_id,
    }
    return {
        "schema": PIN_SCHEMA,
        "schema_version": 1,
        **pin_body,
        "pin_sha256": sha(canonical(pin_body)),
    }


def event_record(
    *, seq: int, kind: str, at_ns: int, previous_sha256: str | None, fields: Mapping[str, Any]
) -> dict[str, Any]:
    if (
        type(seq) is not int
        or seq < 0
        or type(at_ns) is not int
        or at_ns <= 0
        or kind not in _EVENT_KINDS
        or (previous_sha256 is not None and not _SHA256.fullmatch(previous_sha256))
        or not isinstance(fields, Mapping)
    ):
        raise SchemaError("event_fields_invalid")
    unsigned = {
        "schema": EVENT_SCHEMA,
        "schema_version": 1,
        "seq": seq,
        "kind": kind,
        "at_ns": at_ns,
        "previous_sha256": previous_sha256,
        "fields": dict(fields),
    }
    return {**unsigned, "event_sha256": sha(canonical(unsigned))}


def verify_event_chain(payload: bytes) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    previous: str | None = None
    previous_at_ns = 0
    for seq, line in enumerate(payload.splitlines()):
        record = decode(line)
        if (
            not isinstance(record, dict)
            or set(record)
            != {
                "schema",
                "schema_version",
                "seq",
                "kind",
                "at_ns",
                "previous_sha256",
                "fields",
                "event_sha256",
            }
            or record["schema"] != EVENT_SCHEMA
            or record["schema_version"] != 1
            or record["seq"] != seq
            or record["kind"] not in _EVENT_KINDS
            or type(record["at_ns"]) is not int
            or record["at_ns"] <= 0
            or record["at_ns"] < previous_at_ns
            or record["previous_sha256"] != previous
            or not isinstance(record["fields"], dict)
            or not isinstance(record["event_sha256"], str)
            or not _SHA256.fullmatch(record["event_sha256"])
        ):
            raise SchemaError("event_chain_record_invalid")
        unsigned = dict(record)
        digest = unsigned.pop("event_sha256")
        if sha(canonical(unsigned)) != digest:
            raise SchemaError("event_chain_hash_invalid")
        previous = digest
        previous_at_ns = record["at_ns"]
        records.append(record)
    if payload and not payload.endswith(b"\n"):
        raise SchemaError("event_chain_torn_tail")
    return tuple(records)
