#!/usr/bin/env python3
"""Independent rolling Senses acquisition schema v4."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from tools import brain_senses_external_rolling_policy_v1 as policy

SERVICE_ID = "external-rolling-v4"
SERVICE_RELATIVE_ROOT = f"services/{SERVICE_ID}"
POLICY_FILE = "policy.json"
CONTROL_FILE = "control.jsonl"
LOCK_FILE = ".external-rolling-v4.lock"
PLAN_FILE = "plan.json"
EVENT_FILE = "events.jsonl"
TERMINAL_FILE = "terminal.json"

POLICY_SCHEMA = "brain-senses-external-rolling-v4-policy"
PLAN_SCHEMA = "brain-senses-external-rolling-v4-plan"
EVENT_SCHEMA = "brain-senses-external-rolling-v4-event"
CONTROL_SCHEMA = "brain-senses-external-rolling-v4-control-event"
RECEIPT_SCHEMA = "brain-senses-external-rolling-v4-request-receipt"
TERMINAL_SCHEMA = "brain-senses-external-rolling-v4-terminal"

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ROOT_ID = re.compile(r"[0-9a-f]{32}\Z")
_VOLUME_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_EPOCH_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\Z")
_EVENT_KINDS = frozenset(
    {
        "epoch_started",
        "window_missed",
        "window_reserved",
        "request_intent",
        "request_captured",
        "request_failed",
        "frame_sealed",
        "epoch_terminal",
        "epoch_abandoned",
    }
)
_CONTROL_KINDS = frozenset(
    {"epoch_open", "epoch_close", "epoch_abandon", "budget_pause", "budget_resume"}
)


class SchemaError(ValueError):
    """Invalid v4 schema value or hash-chain event."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SchemaError("json_duplicate_key")
        result[key] = value
    return result


def decode(payload: bytes) -> Any:
    try:
        return json.loads(payload, object_pairs_hook=_object_pairs, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaError("json_invalid") from exc


def _reject_constant(value: str) -> None:
    raise SchemaError("json_nonfinite")


def bucket_for(start_ns: int) -> str:
    if type(start_ns) is not int or start_ns <= 0 or start_ns % policy.HOUR_NS:
        raise SchemaError("bucket_start_invalid")
    try:
        return datetime.fromtimestamp(start_ns // policy.SECOND_NS, UTC).strftime("%Y%m%dT%H0000Z")
    except (OverflowError, OSError, ValueError) as exc:
        raise SchemaError("bucket_start_out_of_range") from exc


def epoch_id(anchor_ns: int, nonce: str) -> str:
    bucket = bucket_for(anchor_ns)
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{12}", nonce):
        raise SchemaError("epoch_nonce_invalid")
    return f"{bucket}-{nonce}"


def build_plan(
    *,
    epoch: str,
    created_ns: int,
    anchor_ns: int,
    root_id: str,
    volume_uuid: str,
    source_bindings: Mapping[str, str],
) -> dict[str, Any]:
    """Build an immutable independent epoch plan with no legacy predecessor."""
    validate_epoch_id(epoch)
    if (
        type(created_ns) is not int
        or created_ns <= 0
        or type(anchor_ns) is not int
        or anchor_ns < created_ns + 2 * policy.MINUTE_NS
        or not isinstance(root_id, str)
        or not _ROOT_ID.fullmatch(root_id)
        or not isinstance(volume_uuid, str)
        or not _VOLUME_UUID.fullmatch(volume_uuid.lower())
    ):
        raise SchemaError("plan_host_or_clock_binding_invalid")
    windows = policy.build_epoch(anchor_ns)
    if epoch != epoch_id(anchor_ns, epoch.rsplit("-", 1)[-1]):
        raise SchemaError("plan_epoch_anchor_mismatch")
    if not isinstance(source_bindings, Mapping) or not source_bindings:
        raise SchemaError("plan_sources_invalid")
    sources = dict(sorted(source_bindings.items()))
    for path, source_hash in sources.items():
        if (
            not isinstance(path, str)
            or path.startswith("/")
            or ".." in path.split("/")
            or not isinstance(source_hash, str)
            or not _SHA256.fullmatch(source_hash)
        ):
            raise SchemaError("plan_source_binding_invalid")
    policy_bytes = canonical(policy.policy_document())
    return {
        "schema": PLAN_SCHEMA,
        "schema_version": 4,
        "service_id": SERVICE_ID,
        "epoch_id": epoch,
        "policy_id": policy.POLICY_ID,
        "policy_sha256": sha(policy_bytes),
        "source_bindings": sources,
        "source_bindings_sha256": sha(canonical(sources)),
        "provider": policy.policy_document()["provider_alias"],
        "endpoint_origin": policy.policy_document()["endpoint_origin"],
        "storage": {
            "root_id": root_id,
            "volume_uuid": volume_uuid.lower(),
            "relative_root": f"{SERVICE_RELATIVE_ROOT}/epochs/{epoch}",
        },
        "created_ns": created_ns,
        "anchor_ns": anchor_ns,
        "prior_interval": "unobserved_no_continuity_claim",
        "windows": [
            {
                "kind": row.kind,
                "index": row.index,
                "bucket": row.bucket,
                "start_ns": row.start_ns,
                "end_ns": row.end_ns,
                "request_cap": row.request_cap,
            }
            for row in windows
        ],
        "caps": policy.policy_document(),
    }


def event_record(
    *,
    schema: str,
    seq: int,
    kind: str,
    at_ns: int,
    previous_sha256: str | None,
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    allowed = _EVENT_KINDS if schema == EVENT_SCHEMA else _CONTROL_KINDS
    if schema not in {EVENT_SCHEMA, CONTROL_SCHEMA} or kind not in allowed:
        raise SchemaError("event_kind_invalid")
    if type(seq) is not int or seq < 0 or type(at_ns) is not int or at_ns <= 0:
        raise SchemaError("event_position_invalid")
    if previous_sha256 is not None and not _SHA256.fullmatch(previous_sha256):
        raise SchemaError("event_previous_digest_invalid")
    record: dict[str, Any] = {
        "schema": schema,
        "schema_version": 4,
        "seq": seq,
        "kind": kind,
        "at_ns": at_ns,
        "previous_sha256": previous_sha256,
        "fields": dict(fields),
    }
    record["event_sha256"] = sha(canonical(record))
    return record


def verify_event_chain(payload: bytes, *, schema: str) -> tuple[dict[str, Any], ...]:
    if schema not in {EVENT_SCHEMA, CONTROL_SCHEMA}:
        raise SchemaError("event_chain_schema_invalid")
    lines = payload.splitlines()
    records: list[dict[str, Any]] = []
    previous: str | None = None
    previous_at_ns = 0
    allowed = _EVENT_KINDS if schema == EVENT_SCHEMA else _CONTROL_KINDS
    for seq, line in enumerate(lines):
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
            or record.get("schema") != schema
            or record.get("schema_version") != 4
            or record.get("seq") != seq
            or record.get("kind") not in allowed
            or type(record.get("at_ns")) is not int
            or record["at_ns"] <= 0
            or record["at_ns"] < previous_at_ns
            or record.get("previous_sha256") != previous
            or not isinstance(record.get("fields"), dict)
            or not isinstance(record.get("event_sha256"), str)
            or not _SHA256.fullmatch(record["event_sha256"])
        ):
            raise SchemaError("event_chain_record_invalid")
        unsigned = dict(record)
        expected = unsigned.pop("event_sha256")
        if sha(canonical(unsigned)) != expected:
            raise SchemaError("event_chain_hash_invalid")
        previous = expected
        previous_at_ns = record["at_ns"]
        records.append(record)
    if payload and not payload.endswith(b"\n"):
        raise SchemaError("event_chain_torn_tail")
    return tuple(records)


def validate_epoch_id(value: Any) -> str:
    if not isinstance(value, str) or not _EPOCH_ID.fullmatch(value):
        raise SchemaError("epoch_id_invalid")
    return value
