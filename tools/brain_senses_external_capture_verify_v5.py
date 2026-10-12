#!/usr/bin/env python3
"""Independent raw response and receipt verifier for Senses acquisition v5."""

from __future__ import annotations

from typing import Any

from memecoin_research_storage import StorageError
from tools import brain_senses_external_schema_v5 as schema
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256


class CaptureVerifyError(ValueError):
    """A persisted response cannot be bound to its unique durable intent."""


def verify_capture(
    writer: DescriptorWriter,
    *,
    relative_directory: str,
    expected: dict[str, Any],
) -> dict[str, Any]:
    """Reopen response and receipt by descriptor; prove request and byte identity."""
    try:
        receipt_bytes = writer.read_bytes(f"{relative_directory}/receipt.json", max_bytes=64_000)
        response_bytes = writer.read_bytes(
            f"{relative_directory}/response.json", max_bytes=32_000_000
        )
    except (OSError, StorageError) as exc:
        raise CaptureVerifyError("capture_artifact_unavailable") from exc
    try:
        import json

        receipt = json.loads(receipt_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureVerifyError("capture_receipt_invalid") from exc
    fields = {
        "schema",
        "schema_version",
        "provider",
        "request_id",
        "request_sha256",
        "wire_request_sha256",
        "request_started_ns",
        "received_ns",
        "availability_ns",
        "decision_ns",
        "response_sha256",
        "response_bytes",
        "storage",
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != fields
        or canonical_json(receipt) != receipt_bytes
        or receipt.get("schema") != "brain-senses-external-rolling-v5-request-receipt"
        or receipt.get("schema_version") != 1
        or receipt.get("provider") != "helius-free"
        or receipt.get("request_id") != expected.get("request_id")
        or receipt.get("request_sha256") != expected.get("request_sha256")
        or receipt.get("wire_request_sha256") != expected.get("wire_request_sha256")
        or receipt.get("decision_ns") != expected.get("decision_ns")
        or receipt.get("response_sha256") != sha256(response_bytes)
        or receipt.get("response_bytes") != len(response_bytes)
        or type(receipt.get("request_started_ns")) is not int
        or type(receipt.get("received_ns")) is not int
        or receipt["request_started_ns"] < receipt["decision_ns"]
        or receipt["received_ns"] < receipt["request_started_ns"]
        or receipt.get("availability_ns") != receipt.get("received_ns")
    ):
        raise CaptureVerifyError("capture_receipt_binding_invalid")
    storage = receipt.get("storage")
    if (
        not isinstance(storage, dict)
        or storage.get("codec") != "raw"
        or storage.get("path") != "response.json"
        or storage.get("sha256") != sha256(response_bytes)
        or storage.get("bytes") != len(response_bytes)
        or storage.get("codec_version") is not None
    ):
        raise CaptureVerifyError("capture_storage_binding_invalid")
    try:
        envelope = schema.decode(response_bytes)
    except schema.SchemaError as exc:
        raise CaptureVerifyError("capture_response_invalid_json") from exc
    if (
        not isinstance(envelope, dict)
        or envelope.get("jsonrpc") != "2.0"
        or envelope.get("id") != 1
    ):
        raise CaptureVerifyError("capture_response_envelope_invalid")
    has_result = "result" in envelope
    has_error = "error" in envelope
    if has_result == has_error:
        raise CaptureVerifyError("capture_response_outcome_ambiguous")
    if has_error and not isinstance(envelope["error"], dict):
        raise CaptureVerifyError("capture_provider_error_invalid")
    return {
        "receipt": receipt,
        "response": envelope,
        "response_bytes": len(response_bytes),
        "response_sha256": sha256(response_bytes),
        "allocated_bytes": (writer.stat(f"{relative_directory}/response.json").st_blocks * 512)
        + (writer.stat(f"{relative_directory}/receipt.json").st_blocks * 512),
    }


def verify_epoch_captures(
    writer: DescriptorWriter, *, epoch_relative: str, event_bytes: bytes
) -> tuple[dict[str, Any], ...]:
    """Reconcile every captured event with its request intent and raw receipt."""
    from tools.brain_senses_external_writer_v1 import semantic_request_sha256, wire_request_sha256

    try:
        events = schema.verify_event_chain(event_bytes, schema=schema.EVENT_SCHEMA)
        captures_dir = f"{epoch_relative}/captures"
        capture_names = writer.list_names(captures_dir)
    except (StorageError, schema.SchemaError) as exc:
        raise CaptureVerifyError("epoch_capture_inventory_invalid") from exc
    intents = {
        event["fields"]["request_id"]: event["fields"]
        for event in events
        if event["kind"] == "request_intent"
    }
    captured_events = {
        event["fields"]["request_id"]: event["fields"]
        for event in events
        if event["kind"] == "request_captured"
    }
    failed_events = {
        event["fields"]["request_id"] for event in events if event["kind"] == "request_failed"
    }
    if (
        not set(capture_names) <= set(captured_events) | failed_events | set(intents)
        or not set(captured_events) <= set(intents)
        or not failed_events <= set(intents)
        or not set(captured_events) <= set(capture_names)
    ):
        raise CaptureVerifyError("epoch_capture_event_set_mismatch")
    verified: list[dict[str, Any]] = []
    for request_id in sorted(captured_events):
        intent = intents[request_id]
        event = captured_events[request_id]
        method, params = intent.get("method"), intent.get("params")
        if not isinstance(method, str) or not isinstance(params, list):
            raise CaptureVerifyError("epoch_capture_intent_invalid")
        expected = {
            "request_id": request_id,
            "request_sha256": semantic_request_sha256(method, params),
            "wire_request_sha256": wire_request_sha256(method, params),
            "decision_ns": intent.get("decision_ns"),
        }
        if (
            intent.get("wire_request_sha256") != expected["wire_request_sha256"]
            or event.get("response_sha256") is None
            or type(event.get("response_bytes")) is not int
        ):
            raise CaptureVerifyError("epoch_capture_intent_binding_invalid")
        result = verify_capture(
            writer,
            relative_directory=f"{captures_dir}/{request_id}",
            expected=expected,
        )
        receipt = result["receipt"]
        if (
            "result" not in result["response"]
            or event.get("response_sha256") != result["response_sha256"]
            or event.get("response_bytes") != result["response_bytes"]
            or event.get("availability_ns") != receipt.get("availability_ns")
        ):
            raise CaptureVerifyError("epoch_capture_event_receipt_mismatch")
        verified.append(result)
    return tuple(verified)
