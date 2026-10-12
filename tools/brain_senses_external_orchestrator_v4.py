#!/usr/bin/env python3
"""Forward-only, descriptor-bound continuous Senses collector v4."""

# mypy: disable-error-code=import-untyped

from __future__ import annotations

import argparse
import contextlib
import os
import plistlib
import re
import stat
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qsl, urlsplit

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from memecoin_ai.sources import (  # noqa: E402
    HTTPSourceError,
    ReadOnlyRPC,
    SourceError,
    TransportSourceError,
)
from memecoin_collector.private_config import (  # noqa: E402  # type: ignore[import-untyped]
    read_private_config,
)
from memecoin_research_storage import (  # noqa: E402
    StorageConfig,
    StorageError,
    _open_relative_directory,
    writer_guard,
)
from tools import brain_senses_external_clock_v1 as clock_check  # noqa: E402
from tools import brain_senses_external_frame_verify_v4 as frame_verify  # noqa: E402
from tools import brain_senses_external_rolling_policy_v1 as policy  # noqa: E402
from tools import brain_senses_external_sampling_v4 as sampling  # noqa: E402
from tools import brain_senses_external_schema_v4 as schema  # noqa: E402
from tools import brain_senses_external_verify_v4 as verify  # noqa: E402
from tools.brain_senses_external_capture_verify_v4 import (  # noqa: E402
    CaptureVerifyError,
    verify_capture,
    verify_epoch_captures,
)
from tools.brain_senses_external_writer_v1 import (  # noqa: E402
    DescriptorWriter,
    canonical_json,
    semantic_request_sha256,
    sha256,
    wire_request_sha256,
)

CONFIG_PATH = Path.home() / ".config/brain-research/storage-v1.json"
PRIVATE_ENDPOINT_FILE = REPO / ".env.local"
SERVICE_RELATIVE = schema.SERVICE_RELATIVE_ROOT
MONITOR_FILE = f"{SERVICE_RELATIVE}/monitor/latest.json"
MONITOR_SCHEMA = "brain-senses-external-rolling-v4-poll"
ALLOCATION_CAP_FILE = f"{SERVICE_RELATIVE}/service-allocation-cap.json"
LIFETIME_PAUSE_FILE = f"{SERVICE_RELATIVE}/service-lifetime-pause.json"
_SOURCE_PATHS = (
    "tools/brain_senses_external_orchestrator_v4.py",
    "tools/brain_senses_external_clock_v1.py",
    "ops/launchd/ai.memecoin.brain.senses.external-rolling-v4.plist",
    "tools/brain_senses_external_capture_verify_v4.py",
    "tools/brain_senses_external_frame_verify_v4.py",
    "tools/brain_senses_external_sampling_v4.py",
    "tools/brain_senses_external_schema_v4.py",
    "tools/brain_senses_external_rolling_policy_v1.py",
    "tools/brain_senses_external_verify_v4.py",
    "tools/brain_senses_external_writer_v1.py",
    "src/memecoin_ai/sources.py",
    "src/memecoin_collector/private_config.py",
    "src/memecoin_research_storage/__init__.py",
)


class RollingRuntimeError(RuntimeError):
    """Sanitized service stop reason; endpoint and response text stay private."""


class BudgetStop(RollingRuntimeError):
    """A verified hard cap prevents the next request from being reserved."""

    def __init__(self, reason: str, checked_ns: int) -> None:
        self.reason = reason
        self.checked_ns = checked_ns
        super().__init__("budget_cap:" + reason)


class LifetimeCapStop(RollingRuntimeError):
    """The immutable service allocation cap has no remaining safe headroom."""


def source_bindings() -> dict[str, str]:
    return {path: sha256((REPO / path).read_bytes()) for path in _SOURCE_PATHS}


def _private_endpoint() -> str:
    try:
        raw = read_private_config(PRIVATE_ENDPOINT_FILE).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RollingRuntimeError("private_helius_config_unavailable") from exc
    values: list[str] = [
        line.partition("=")[2].strip()
        for line in raw.splitlines()
        if line.partition("=")[0].strip() == "MEMECOIN_HELIUS_RPC_URL"
        and line.partition("=")[1]
    ]
    if len(values) != 1:
        raise RollingRuntimeError("private_helius_config_ambiguous")
    value = values[0]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    try:
        parsed = urlsplit(value)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        keys = [item for key, item in query if key == "api-key"]
        if (
            parsed.scheme != "https"
            or parsed.hostname != "mainnet.helius-rpc.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or parsed.fragment
            or parsed.netloc.count("@")
            or len(keys) != 1
            or not keys[0]
            or any(ord(char) < 33 or ord(char) > 126 for char in value)
        ):
            raise ValueError
    except ValueError as exc:
        raise RollingRuntimeError("private_helius_endpoint_invalid") from exc
    return value


def _read_optional(writer: DescriptorWriter, relative: str, *, limit: int = 32_000_000) -> bytes:
    try:
        return writer.read_bytes(relative, max_bytes=limit)
    except FileNotFoundError:
        return b""


def _append_event(
    writer: DescriptorWriter, relative: str, *, event_schema: str, kind: str, at_ns: int,
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    raw = _repair_torn_chain(writer, relative)
    records = schema.verify_event_chain(raw, schema=event_schema)
    previous = records[-1]["event_sha256"] if records else None
    event = schema.event_record(
        schema=event_schema,
        seq=len(records),
        kind=kind,
        at_ns=at_ns,
        previous_sha256=previous,
        fields=fields,
    )
    writer.append(relative, schema.canonical(event))
    return event


def _repair_torn_chain(writer: DescriptorWriter, relative: str) -> bytes:
    """Drop only a non-newline final suffix; any complete invalid record still stops."""
    raw = _read_optional(writer, relative)
    if raw and not raw.endswith(b"\n"):
        last_newline = raw.rfind(b"\n")
        complete_prefix = raw[: last_newline + 1] if last_newline >= 0 else b""
        writer.atomic_write(relative, complete_prefix)
        return complete_prefix
    return raw


def _load_document(writer: DescriptorWriter, relative: str, *, max_bytes: int = 32_000_000) -> Any:
    return schema.decode(writer.read_bytes(relative, max_bytes=max_bytes))


def _safe_reason(exc: BaseException) -> str:
    if isinstance(exc, HTTPSourceError):
        return "provider_quota_or_billing" if exc.status == 429 else "provider_http"
    if isinstance(exc, TransportSourceError):
        return "provider_transport"
    if isinstance(exc, StorageError):
        return "clock_or_storage_denied_after_intent"
    if isinstance(exc, RollingRuntimeError) and "too_large" in str(exc):
        return "response_too_large"
    if isinstance(exc, SourceError):
        return "response_invalid"
    return "response_invalid"


@contextlib.contextmanager
def _open_storage_writer(config_path: Path) -> Iterator[tuple[Any, DescriptorWriter]]:
    """Bind every data write to the configured Kingston descriptor and floor."""
    config = StorageConfig.load(config_path)
    dispatch_floor = (
        policy.DISK_FLOOR_BYTES
        + policy.SERVICE_OPERATING_RESERVE_BYTES
        + policy.SERVICE_PEAK_METADATA_RESERVE_BYTES
    )
    with writer_guard(config_path, min_free_bytes=dispatch_floor) as guard:
        guard.create_directory("services")
        guard.create_directory(SERVICE_RELATIVE)
        root_fd = _open_relative_directory(guard.mount_fd, config.data_root)
        try:
            def revalidate_before_write() -> None:
                guard.revalidate(min_free_bytes=dispatch_floor)

            with DescriptorWriter(root_fd, before_write=revalidate_before_write) as writer:
                yield guard, writer
        finally:
            os.close(root_fd)


def _source_valid(plan: Mapping[str, Any]) -> bool:
    return plan.get("source_bindings") == source_bindings()


def _service_allocated_upper_bytes(writer: DescriptorWriter) -> int:
    rows = _descriptor_inventory(writer, SERVICE_RELATIVE)
    _verify_service_layout(rows)
    return sum(row["allocated_upper_bytes"] for row in rows)


def _descriptor_inventory(writer: DescriptorWriter, relative: str) -> list[dict[str, Any]]:
    """Inventory files and directories only through the held DescriptorWriter root."""
    rows: list[dict[str, Any]] = []

    def add(path: str, entry: os.stat_result, kind: str) -> None:
        size = entry.st_size if kind == "file" else 0
        rows.append(
            {
                "path": path,
                "kind": kind,
                "size_bytes": size,
                "allocated_upper_bytes": max(size, entry.st_blocks * 512) + 16 * 1024,
                "mode": stat.S_IMODE(entry.st_mode),
                "owner": entry.st_uid,
                "links": entry.st_nlink,
            }
        )

    add(".", writer.stat(relative), "directory")

    def visit(current: str, prefix: str) -> None:
        for name in writer.list_names(current):
            child = f"{current}/{name}"
            rel_name = f"{prefix}/{name}" if prefix else name
            entry = writer.stat(child)
            kind = "directory" if stat.S_ISDIR(entry.st_mode) else "file"
            add(rel_name, entry, kind)
            if kind == "directory":
                visit(child, rel_name)

    visit(relative, "")
    return sorted(rows, key=lambda row: row["path"])


def _epoch_allocation_manifest(
    writer: DescriptorWriter, epoch_id: str
) -> tuple[str, int, int]:
    rows = [
        {
            key: row[key]
            for key in (
                "path",
                "kind",
                "size_bytes",
                "allocated_upper_bytes",
                "mode",
                "owner",
                "links",
            )
        }
        for row in _descriptor_inventory(writer, _epoch_rel(epoch_id))
        if row["path"] not in {".", schema.TERMINAL_FILE}
    ]
    payload = schema.canonical(rows)
    return (
        schema.sha(payload),
        sum(row["allocated_upper_bytes"] for row in rows),
        len(rows),
    )


def _verify_service_layout(rows: list[dict[str, Any]]) -> None:
    """Reject unexplained files, links, temporary writes, and unknown service roots."""
    allowed_root_files = {
        "policy.json",
        schema.CONTROL_FILE,
        Path(ALLOCATION_CAP_FILE).name,
        Path(LIFETIME_PAUSE_FILE).name,
        "STOP",
    }
    for row in rows:
        path = row["path"]
        kind = row["kind"]
        if path == ".":
            if kind != "directory":
                raise RollingRuntimeError("service_inventory_root_invalid")
            continue
        parts = path.split("/")
        allowed = False
        if len(parts) == 1 and parts[0] in allowed_root_files:
            allowed = kind == "file"
        elif path in {"epochs", "budget-evidence", "monitor"}:
            allowed = kind == "directory"
        elif parts[0] == "epochs" and len(parts) == 2:
            try:
                schema.validate_epoch_id(parts[1])
                allowed = kind == "directory"
            except schema.SchemaError:
                allowed = False
        elif parts[0] == "epochs" and len(parts) >= 3:
            try:
                schema.validate_epoch_id(parts[1])
            except schema.SchemaError:
                allowed = False
            else:
                tail = parts[2:]
                if tail in ([schema.PLAN_FILE], [schema.EVENT_FILE], [schema.TERMINAL_FILE]):
                    allowed = kind == "file"
                elif tail == ["captures"]:
                    allowed = kind == "directory"
                elif len(tail) == 2 and tail[0] == "captures":
                    allowed = (
                        bool(re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9]{2}", tail[1]))
                        and kind == "directory"
                    )
                elif len(tail) == 3 and tail[0] == "captures":
                    allowed = (
                        bool(re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9]{2}", tail[1]))
                        and tail[2] in {"receipt.json", "response.json"}
                        and kind == "file"
                    )
                elif tail == ["selections"]:
                    allowed = kind == "directory"
                elif len(tail) == 2 and tail[0] == "selections":
                    allowed = (
                        bool(re.fullmatch(r"[0-9]{8}T[0-9]{4}00Z\.json", tail[1]))
                        and kind == "file"
                    )
        elif parts[0] == "budget-evidence" and len(parts) == 1:
            allowed = kind == "directory"
        elif parts[0] == "budget-evidence" and len(parts) == 2:
            allowed = bool(re.fullmatch(r"[0-9a-f]{64}\.json", parts[1])) and kind == "file"
        elif parts == ["monitor"]:
            allowed = kind == "directory"
        elif parts == ["monitor", "latest.json"]:
            allowed = kind == "file"
        if not allowed:
            raise RollingRuntimeError("service_inventory_unexpected_entry")


def _allocation_cap(writer: DescriptorWriter, guard: Any, *, now_ns: int) -> dict[str, Any]:
    if writer.exists(ALLOCATION_CAP_FILE):
        raw = writer.read_bytes(ALLOCATION_CAP_FILE, max_bytes=64_000)
        value = schema.decode(raw)
        required = {
            "schema", "schema_version", "created_ns", "root_id", "volume_uuid",
            "available_at_activation_bytes", "service_allocation_cap_bytes",
        }
        if (
            not isinstance(value, dict)
            or set(value) != required
            or schema.canonical(value) != raw
            or value.get("schema") != "brain-senses-external-rolling-v4-allocation-cap"
            or value.get("schema_version") != 4
            or value.get("root_id") != guard.root_id
            or value.get("volume_uuid") != guard.volume_uuid.lower()
            or type(value.get("created_ns")) is not int
            or type(value.get("available_at_activation_bytes")) is not int
            or type(value.get("service_allocation_cap_bytes")) is not int
            or not 0 < value["service_allocation_cap_bytes"]
            <= policy.MAX_SERVICE_LIFETIME_ALLOCATION_BYTES
            or value["service_allocation_cap_bytes"]
            != policy.activation_allocation_cap(value["available_at_activation_bytes"])
        ):
            raise RollingRuntimeError("service_allocation_cap_invalid")
        return value
    try:
        if writer.list_names(f"{SERVICE_RELATIVE}/epochs"):
            raise RollingRuntimeError("allocation_cap_missing_after_epoch_creation")
    except FileNotFoundError:
        pass
    observed = guard.revalidate(
        min_free_bytes=(
            policy.DISK_FLOOR_BYTES
            + policy.SERVICE_OPERATING_RESERVE_BYTES
            + policy.NEXT_EPOCH_ALLOCATION_RESERVE_BYTES
            + policy.SERVICE_PEAK_METADATA_RESERVE_BYTES
        )
    )
    cap_bytes = policy.activation_allocation_cap(observed.available_bytes)
    value = {
        "schema": "brain-senses-external-rolling-v4-allocation-cap",
        "schema_version": 4,
        "created_ns": now_ns,
        "root_id": guard.root_id,
        "volume_uuid": guard.volume_uuid.lower(),
        "available_at_activation_bytes": observed.available_bytes,
        "service_allocation_cap_bytes": cap_bytes,
    }
    writer.create(ALLOCATION_CAP_FILE, schema.canonical(value))
    return value


def _verify_lifetime_pause(writer: DescriptorWriter, cap: Mapping[str, Any]) -> bool:
    if not writer.exists(LIFETIME_PAUSE_FILE):
        return False
    raw = writer.read_bytes(LIFETIME_PAUSE_FILE, max_bytes=64_000)
    value = schema.decode(raw)
    required = {
        "schema", "schema_version", "at_ns", "allocation_cap_sha256",
        "service_allocation_cap_bytes", "observed_allocated_upper_bytes", "reason",
        "automatic_resume",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or schema.canonical(value) != raw
        or value.get("schema") != "brain-senses-external-rolling-v4-lifetime-pause"
        or value.get("schema_version") != 4
        or value.get("allocation_cap_sha256") != schema.sha(
            writer.read_bytes(ALLOCATION_CAP_FILE)
        )
        or value.get("reason") != "service_lifetime_allocation_cap"
        or value.get("automatic_resume") is not False
        or type(value.get("at_ns")) is not int
        or value.get("service_allocation_cap_bytes")
        != cap.get("service_allocation_cap_bytes")
        or type(value.get("observed_allocated_upper_bytes")) is not int
    ):
        raise RollingRuntimeError("service_lifetime_pause_invalid")
    return True


def _persist_lifetime_pause(
    writer: DescriptorWriter,
    *,
    cap: Mapping[str, Any],
    observed_bytes: int,
    at_ns: int,
) -> None:
    payload = schema.canonical(
        {
            "schema": "brain-senses-external-rolling-v4-lifetime-pause",
            "schema_version": 4,
            "at_ns": at_ns,
            "allocation_cap_sha256": schema.sha(writer.read_bytes(ALLOCATION_CAP_FILE)),
            "service_allocation_cap_bytes": cap["service_allocation_cap_bytes"],
            "observed_allocated_upper_bytes": observed_bytes,
            "reason": "service_lifetime_allocation_cap",
            "automatic_resume": False,
        }
    )
    if writer.exists(LIFETIME_PAUSE_FILE):
        if writer.read_bytes(LIFETIME_PAUSE_FILE) != payload:
            raise RollingRuntimeError("service_lifetime_pause_conflict")
        return
    writer.create(LIFETIME_PAUSE_FILE, payload)


def _epoch_rel(epoch_id: str) -> str:
    schema.validate_epoch_id(epoch_id)
    return f"{SERVICE_RELATIVE}/epochs/{epoch_id}"


def _epoch_event_path(epoch_id: str) -> str:
    return f"{_epoch_rel(epoch_id)}/{schema.EVENT_FILE}"


def _load_epochs(
    writer: DescriptorWriter, *, root_id: str, volume_uuid: str
) -> tuple[dict[str, bytes], dict[str, verify.VerifiedTerminal], dict[str, Any]]:
    plans: dict[str, bytes] = {}
    terminals: dict[str, verify.VerifiedTerminal] = {}
    projections: dict[str, Any] = {}
    epochs_root = f"{SERVICE_RELATIVE}/epochs"
    try:
        epoch_names = writer.list_names(epochs_root)
    except FileNotFoundError:
        return plans, terminals, projections
    expected_sources = source_bindings()
    for epoch_id in epoch_names:
        try:
            schema.validate_epoch_id(epoch_id)
        except schema.SchemaError as exc:
            raise RollingRuntimeError("unexpected_epoch_directory") from exc
        relative = _epoch_rel(epoch_id)
        plan_path = f"{relative}/{schema.PLAN_FILE}"
        raw_plan = writer.read_bytes(plan_path, max_bytes=2_000_000)
        plan = verify.verify_plan(
            raw_plan,
            expected_sources=expected_sources,
            expected_root_id=root_id,
            expected_volume_uuid=volume_uuid,
        )
        if not _source_valid(plan):
            raise RollingRuntimeError("source_binding_changed")
        event_path = f"{relative}/{schema.EVENT_FILE}"
        raw_events = _repair_torn_chain(writer, event_path)
        event_rows = schema.verify_event_chain(raw_events, schema=schema.EVENT_SCHEMA)
        intents = {
            event["fields"]["request_id"]: event["fields"]
            for event in event_rows
            if event["kind"] == "request_intent"
        }
        outcomes = {
            event["fields"]["request_id"]
            for event in event_rows
            if event["kind"] in {"request_captured", "request_failed"}
        }
        for request_id in sorted(set(intents) - outcomes):
            capture_dir = f"{relative}/captures/{request_id}"
            if not (
                writer.exists(f"{capture_dir}/receipt.json")
                and writer.exists(f"{capture_dir}/response.json")
            ):
                continue
            intent = intents[request_id]
            recovered = verify_capture(
                writer,
                relative_directory=capture_dir,
                expected={
                    "request_id": request_id,
                    "request_sha256": semantic_request_sha256(
                        intent["method"], intent["params"]
                    ),
                    "wire_request_sha256": intent["wire_request_sha256"],
                    "decision_ns": intent["decision_ns"],
                },
            )
            if "result" not in recovered["response"]:
                continue
            _append_event(
                writer,
                event_path,
                event_schema=schema.EVENT_SCHEMA,
                kind="request_captured",
                at_ns=max(
                    time.time_ns(),
                    event_rows[-1]["at_ns"],
                    recovered["receipt"]["availability_ns"],
                ),
                fields={
                    "request_id": request_id,
                    "response_sha256": recovered["response_sha256"],
                    "response_bytes": recovered["response_bytes"],
                    "availability_ns": recovered["receipt"]["availability_ns"],
                },
            )
            raw_events = writer.read_bytes(event_path)
            event_rows = schema.verify_event_chain(raw_events, schema=schema.EVENT_SCHEMA)
        projection = verify.verify_epoch_events(plan, raw_events)
        verify_epoch_captures(writer, epoch_relative=relative, event_bytes=raw_events)
        _verify_sealed_frames(writer, epoch_id=epoch_id, plan=plan, event_bytes=raw_events)
        plans[epoch_id] = raw_plan
        projections[epoch_id] = projection
        terminal_path = f"{relative}/{schema.TERMINAL_FILE}"
        if writer.exists(terminal_path):
            terminal_bytes = writer.read_bytes(terminal_path, max_bytes=64_000)
            terminal = schema.decode(terminal_bytes)
            required = {
                "schema", "schema_version", "epoch_id", "plan_sha256", "event_tail_sha256",
                "status", "reason", "sealed_ns", "pause_reason",
                "allocation_manifest_sha256", "allocation_upper_bytes", "allocation_object_count",
            }
            manifest_sha, allocation_bytes, object_count = _epoch_allocation_manifest(
                writer, epoch_id
            )
            if (
                not isinstance(terminal, dict)
                or set(terminal) != required
                or schema.canonical(terminal) != terminal_bytes
                or terminal.get("schema") != schema.TERMINAL_SCHEMA
                or terminal.get("schema_version") != 4
                or terminal.get("epoch_id") != epoch_id
                or terminal.get("plan_sha256") != schema.sha(raw_plan)
                or terminal.get("event_tail_sha256") != projection.event_tail_sha256
                or terminal.get("status") != projection.terminal_status
                or terminal.get("allocation_manifest_sha256") != manifest_sha
                or terminal.get("allocation_upper_bytes") != allocation_bytes
                or terminal.get("allocation_object_count") != object_count
            ):
                raise RollingRuntimeError("terminal_receipt_invalid")
            terminals[epoch_id] = verify.VerifiedTerminal(
                terminal_sha256=schema.sha(terminal_bytes),
                event_tail_sha256=projection.event_tail_sha256 or "",
                status=terminal["status"],
                pause_reason=terminal["pause_reason"],
            )
    return plans, terminals, projections


def _verify_sealed_frames(
    writer: DescriptorWriter, *, epoch_id: str, plan: Mapping[str, Any], event_bytes: bytes
) -> None:
    """Replay all sealed anchor/inventory/sample modalities from immutable captures."""
    events = schema.verify_event_chain(event_bytes, schema=schema.EVENT_SCHEMA)
    intents = {
        event["fields"]["request_id"]: event["fields"]
        for event in events
        if event["kind"] == "request_intent"
    }
    windows = {
        event["fields"]["index"]: event["fields"]["mode"]
        for event in events
        if event["kind"] == "window_reserved"
    }
    responses = _captured_results(writer, epoch_id, event_bytes)
    prior_anchor: int | None = None
    for event in events:
        if event["kind"] != "frame_sealed":
            continue
        fields = event["fields"]
        index, bucket = fields["index"], fields["bucket"]
        mode = windows.get(index)
        ids = fields["request_ids"]
        if (
            not isinstance(mode, str)
            or not isinstance(ids, list)
            or len(ids) > policy.MAX_REQUESTS_PER_FRAME
        ):
            raise RollingRuntimeError("sealed_frame_event_invalid")
        calls = [
            (intents[item], responses[item]["response"]["result"], responses[item])
            for item in ids
        ]
        if not calls:
            raise RollingRuntimeError("sealed_frame_without_capture")
        if mode in {"anchor", "anchor_reset"}:
            intent, result, _capture = calls[0]
            if (
                len(calls) != 1
                or intent.get("method") != "getSlot"
                or intent.get("params") != [{"commitment": "finalized"}]
                or fields.get("empty_interval") is not False
            ):
                raise RollingRuntimeError("anchor_frame_modality_invalid")
            prior_anchor = frame_verify.finalized_slot(result)
            continue
        if mode != "inventory":
            raise RollingRuntimeError("sealed_frame_mode_invalid")
        first_intent, current_raw, _ = calls[0]
        current_slot = frame_verify.finalized_slot(current_raw)
        if (
            first_intent.get("method") != "getSlot"
            or first_intent.get("params") != [{"commitment": "finalized"}]
            or prior_anchor is None
            or current_slot < prior_anchor
        ):
            raise RollingRuntimeError("inventory_anchor_binding_invalid")
        if current_slot == prior_anchor:
            if len(calls) != 1 or fields.get("empty_interval") is not True:
                raise RollingRuntimeError("empty_interval_claim_invalid")
            continue
        if fields.get("empty_interval") is not False or len(calls) < 2:
            raise RollingRuntimeError("inventory_frame_missing_inventory")
        inventory_intent, inventory_raw, inventory_capture = calls[1]
        expected_inventory_params = [
            prior_anchor + 1,
            current_slot,
            {"commitment": "finalized"},
        ]
        if (
            inventory_intent.get("method") != "getBlocks"
            or inventory_intent.get("params") != expected_inventory_params
        ):
            raise RollingRuntimeError("inventory_request_binding_invalid")
        inventory = frame_verify.inventory_slots(
            inventory_raw,
            start=prior_anchor + 1,
            end_inclusive=current_slot,
        )
        selection = sampling.build_selection(
            epoch_id=epoch_id,
            bucket=bucket,
            inventory_slots=inventory,
            inventory_sha256=inventory_capture["response_sha256"],
        )
        selection_path = f"{_epoch_rel(epoch_id)}/selections/{bucket}.json"
        if writer.read_bytes(selection_path, max_bytes=1_000_000) != schema.canonical(selection):
            raise RollingRuntimeError("selection_artifact_replay_mismatch")
        expected_count = 2 + 2 * len(selection["selected_slots"])
        if len(calls) != expected_count:
            raise RollingRuntimeError("sample_request_count_mismatch")
        pairs: list[dict[str, Any]] = []
        for pair_index, slot in enumerate(selection["selected_slots"]):
            signature_intent, signature_result, _ = calls[2 + pair_index * 2]
            full_intent, full_result, _ = calls[3 + pair_index * 2]
            expected_sig_params = [
                slot,
                {
                    "commitment": "finalized",
                    "encoding": "json",
                    "transactionDetails": "signatures",
                    "rewards": False,
                },
            ]
            expected_full_params = [
                slot,
                {
                    "commitment": "finalized",
                    "encoding": "json",
                    "transactionDetails": "full",
                    "rewards": False,
                    "maxSupportedTransactionVersion": 1,
                },
            ]
            if (
                signature_intent.get("method") != "getBlock"
                or signature_intent.get("params") != expected_sig_params
                or full_intent.get("method") != "getBlock"
                or full_intent.get("params") != expected_full_params
            ):
                raise RollingRuntimeError("selected_block_request_binding_invalid")
            pairs.append({"slot": slot, "signatures": signature_result, "full": full_result})
        frame_verify.verify_sample_pairs(
            epoch_id=epoch_id,
            bucket=bucket,
            inventory=inventory,
            inventory_sha256=inventory_capture["response_sha256"],
            pairs=pairs,
        )
        prior_anchor = current_slot


def _control_projection(
    writer: DescriptorWriter,
    *,
    root_id: str,
    volume_uuid: str,
) -> tuple[verify.ControlProjection, dict[str, bytes], dict[str, Any]]:
    control_path = f"{SERVICE_RELATIVE}/{schema.CONTROL_FILE}"
    control_bytes = _repair_torn_chain(writer, control_path)
    plans, terminals, projections = _load_epochs(
        writer, root_id=root_id, volume_uuid=volume_uuid
    )
    opened = {
        event["fields"].get("epoch_id")
        for event in schema.verify_event_chain(control_bytes, schema=schema.CONTROL_SCHEMA)
        if event["kind"] == "epoch_open"
    }
    orphans = sorted(set(plans) - opened)
    if orphans:
        if len(orphans) != 1:
            raise RollingRuntimeError("multiple_unopened_epoch_plans")
        epoch_id = orphans[0]
        raw_plan = plans[epoch_id]
        plan = verify.verify_plan(
            raw_plan,
            expected_sources=source_bindings(),
            expected_root_id=root_id,
            expected_volume_uuid=volume_uuid,
        )
        raw_epoch_events = _repair_torn_chain(writer, _epoch_event_path(epoch_id))
        epoch_rows = schema.verify_event_chain(
            raw_epoch_events, schema=schema.EVENT_SCHEMA
        )
        if (
            len(epoch_rows) != 1
            or epoch_rows[0]["kind"] != "epoch_started"
            or projections[epoch_id].request_states
            or projections[epoch_id].active_bucket is not None
        ):
            raise RollingRuntimeError("unopened_epoch_has_dispatch_state")
        control_rows = schema.verify_event_chain(control_bytes, schema=schema.CONTROL_SCHEMA)
        last_at_ns = control_rows[-1]["at_ns"] if control_rows else 0
        open_at_ns = max(plan["created_ns"], last_at_ns)
        if plan["anchor_ns"] <= open_at_ns:
            raise RollingRuntimeError("unopened_epoch_anchor_no_longer_prospective")
        _control_event(
            writer,
            kind="epoch_open",
            at_ns=open_at_ns,
            fields={
                "epoch_id": epoch_id,
                "plan_sha256": schema.sha(raw_plan),
                "anchor_ns": plan["anchor_ns"],
            },
        )
        control_bytes = writer.read_bytes(control_path)
    state = verify.verify_control_chain(
        control_bytes,
        plans=plans,
        terminals=terminals,
        budget_evidence=_budget_evidence_map(writer, plans),
        plan_validator=lambda raw, _epoch: verify.verify_plan(
            raw,
            expected_sources=source_bindings(),
            expected_root_id=root_id,
            expected_volume_uuid=volume_uuid,
        ),
    )
    return state, plans, projections


def _service_usage_rows(
    writer: DescriptorWriter,
    epoch_ids: Any,
    *,
    now_ns: int,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    del now_ns
    rows: list[dict[str, Any]] = []
    by_epoch: dict[str, list[dict[str, Any]]] = {}
    for epoch_id in epoch_ids:
        event_bytes = _read_optional(writer, _epoch_event_path(epoch_id))
        events = schema.verify_event_chain(event_bytes, schema=schema.EVENT_SCHEMA)
        captures = {
            capture["receipt"]["request_id"]: capture
            for capture in verify_epoch_captures(
                writer, epoch_relative=_epoch_rel(epoch_id), event_bytes=event_bytes
            )
        }
        current: list[dict[str, Any]] = []
        for event in events:
            if event["kind"] != "request_intent":
                continue
            intent = event["fields"]
            capture = captures.get(intent["request_id"])
            row = {
                "decision_ns": intent["decision_ns"],
                "response_bytes": capture["response_bytes"] if capture else None,
                "allocated_bytes": capture["allocated_bytes"] if capture else None,
            }
            rows.append(row)
            current.append(row)
        by_epoch[epoch_id] = current
    return rows, by_epoch


def _rolling_usage(
    writer: DescriptorWriter, epoch_ids: Any, *, at_ns: int
) -> tuple[policy.RollingUsage, dict[str, list[dict[str, Any]]]]:
    rows, by_epoch = _service_usage_rows(writer, epoch_ids, now_ns=at_ns)
    return policy.conservative_rolling_usage(rows, now_ns=at_ns), by_epoch


def _budget_is_exceeded(
    reason: str,
    usage: policy.RollingUsage,
    epoch_usage: policy.RollingUsage | None = None,
) -> bool:
    if reason == "epoch_request_cap":
        return epoch_usage is not None and epoch_usage.requests >= policy.MAX_EPOCH_REQUESTS
    if reason == "epoch_response_cap":
        return (
            epoch_usage is not None
            and epoch_usage.response_bytes + policy.MAX_RESPONSE_BYTES
            > policy.MAX_EPOCH_RESPONSE_BYTES
        )
    if reason == "epoch_physical_cap":
        return (
            epoch_usage is not None
            and epoch_usage.allocated_bytes
            + policy.MAX_RESPONSE_BYTES
            + policy.PER_REQUEST_METADATA_RESERVE_BYTES
            > policy.MAX_EPOCH_PHYSICAL_BYTES
        )
    if reason == "rolling_request_cap":
        return usage.requests + 1 > policy.MAX_ROLLING_REQUESTS
    if reason == "rolling_credit_cap":
        return (
            usage.modeled_credits + policy.CONSERVATIVE_CREDITS_PER_REQUEST
            > policy.MAX_ROLLING_CREDITS
        )
    if reason == "rolling_response_cap":
        return usage.response_bytes + policy.MAX_RESPONSE_BYTES > policy.MAX_ROLLING_RESPONSE_BYTES
    if reason == "rolling_physical_cap":
        return (
            usage.allocated_bytes
            + policy.MAX_RESPONSE_BYTES
            + policy.PER_REQUEST_METADATA_RESERVE_BYTES
            > policy.MAX_ROLLING_PHYSICAL_BYTES
        )
    return False


def _write_budget_evidence(
    writer: DescriptorWriter,
    *,
    reason: str,
    disposition: str,
    at_ns: int,
    usage: policy.RollingUsage,
    epoch_usage: policy.RollingUsage | None = None,
    epoch_id: str | None = None,
) -> str:
    row = {
        "schema": "brain-senses-external-rolling-v4-budget-evidence",
        "schema_version": 4,
        "reason": reason,
        "disposition": disposition,
        "checked_ns": at_ns,
        "epoch_id": epoch_id,
        "rolling_usage": {
            "requests": usage.requests,
            "modeled_credits": usage.modeled_credits,
            "response_bytes": usage.response_bytes,
            "allocated_bytes": usage.allocated_bytes,
        },
        "epoch_usage": (
            {
                "requests": epoch_usage.requests,
                "modeled_credits": epoch_usage.modeled_credits,
                "response_bytes": epoch_usage.response_bytes,
                "allocated_bytes": epoch_usage.allocated_bytes,
            }
            if epoch_usage is not None
            else None
        ),
    }
    payload = schema.canonical(row)
    digest = schema.sha(payload)
    relative = f"{SERVICE_RELATIVE}/budget-evidence/{digest}.json"
    writer.mkdir(f"{SERVICE_RELATIVE}/budget-evidence")
    if writer.exists(relative):
        if writer.read_bytes(relative) != payload:
            raise RollingRuntimeError("budget_evidence_collision")
    else:
        writer.create(relative, payload)
    return digest


def _budget_evidence_map(
    writer: DescriptorWriter, plans: Mapping[str, bytes]
) -> dict[str, str]:
    directory = f"{SERVICE_RELATIVE}/budget-evidence"
    try:
        names = writer.list_names(directory)
    except FileNotFoundError:
        return {}
    epochs = tuple(plans)
    evidence: dict[str, str] = {}
    for name in names:
        if not name.endswith(".json"):
            raise RollingRuntimeError("budget_evidence_filename_invalid")
        payload = writer.read_bytes(f"{directory}/{name}", max_bytes=64_000)
        digest = schema.sha(payload)
        row = schema.decode(payload)
        if (
            name != f"{digest}.json"
            or not isinstance(row, dict)
            or schema.canonical(row) != payload
            or row.get("schema") != "brain-senses-external-rolling-v4-budget-evidence"
            or row.get("schema_version") != 4
            or row.get("reason") not in {
                "epoch_request_cap", "epoch_response_cap", "epoch_physical_cap",
                "rolling_request_cap", "rolling_credit_cap", "rolling_response_cap",
                "rolling_physical_cap",
            }
            or row.get("disposition") not in {"exceeded", "headroom"}
            or type(row.get("checked_ns")) is not int
        ):
            raise RollingRuntimeError("budget_evidence_invalid")
        checked_ns = row["checked_ns"]
        usage, by_epoch = _rolling_usage(writer, epochs, at_ns=checked_ns)
        current_epoch_usage = None
        def usage_doc(value: policy.RollingUsage) -> dict[str, int]:
            return {
                "requests": value.requests,
                "modeled_credits": value.modeled_credits,
                "response_bytes": value.response_bytes,
                "allocated_bytes": value.allocated_bytes,
            }
        if row.get("rolling_usage") != usage_doc(usage):
            raise RollingRuntimeError("budget_evidence_usage_mismatch")
        reason = row["reason"]
        if reason.startswith("epoch_") and row["disposition"] == "exceeded":
            evidence_epoch_id = row.get("epoch_id")
            epoch_rows = row.get("epoch_usage")
            if (
                not isinstance(evidence_epoch_id, str)
                or evidence_epoch_id not in by_epoch
                or not isinstance(epoch_rows, dict)
            ):
                raise RollingRuntimeError("budget_evidence_epoch_usage_missing")
            current_epoch_usage = policy.conservative_rolling_usage(
                by_epoch[evidence_epoch_id], now_ns=checked_ns
            )
            if epoch_rows != usage_doc(current_epoch_usage):
                raise RollingRuntimeError("budget_evidence_epoch_usage_mismatch")
        if row["disposition"] == "headroom" and reason.startswith("epoch_"):
            current_epoch_usage = None
            if row.get("epoch_usage") is not None or row.get("epoch_id") is not None:
                raise RollingRuntimeError("budget_resume_epoch_evidence_invalid")
        if row["disposition"] == "exceeded":
            valid = _budget_is_exceeded(reason, usage, current_epoch_usage)
        else:
            valid = (
                policy.blocked_reason(
                    usage,
                    epoch_requests=0,
                    epoch_response_bytes=0,
                    epoch_allocated_bytes=0,
                )
                is None
            )
        if not valid:
            raise RollingRuntimeError("budget_evidence_disposition_invalid")
        evidence[digest] = f"{row['disposition']}:{reason}"
    return evidence


def _budget_snapshot(
    writer: DescriptorWriter,
    *,
    plans: Mapping[str, bytes],
    epoch_id: str,
    now_ns: int,
) -> tuple[str | None, policy.RollingUsage, policy.RollingUsage]:
    usage, by_epoch = _rolling_usage(writer, plans, at_ns=now_ns)
    epoch_usage = policy.conservative_rolling_usage(by_epoch.get(epoch_id, []), now_ns=now_ns)
    reason = policy.blocked_reason(
        usage,
        epoch_requests=epoch_usage.requests,
        epoch_response_bytes=epoch_usage.response_bytes,
        epoch_allocated_bytes=epoch_usage.allocated_bytes,
    )
    return reason, usage, epoch_usage


def _control_event(
    writer: DescriptorWriter, *, kind: str, at_ns: int, fields: Mapping[str, Any]
) -> None:
    _append_event(
        writer,
        f"{SERVICE_RELATIVE}/{schema.CONTROL_FILE}",
        event_schema=schema.CONTROL_SCHEMA,
        kind=kind,
        at_ns=at_ns,
        fields=fields,
    )


def _open_epoch(writer: DescriptorWriter, *, root_id: str, volume_uuid: str, now_ns: int) -> str:
    anchor_ns = policy.next_fresh_anchor_ns(now_ns)
    epoch_id = schema.epoch_id(anchor_ns, os.urandom(6).hex())
    relative = _epoch_rel(epoch_id)
    writer.mkdir(relative)
    writer.mkdir(f"{relative}/captures")
    plan = schema.build_plan(
        epoch=epoch_id,
        created_ns=now_ns,
        anchor_ns=anchor_ns,
        root_id=root_id,
        volume_uuid=volume_uuid,
        source_bindings=source_bindings(),
    )
    plan_bytes = schema.canonical(plan)
    writer.create(f"{relative}/{schema.PLAN_FILE}", plan_bytes)
    _append_event(
        writer,
        f"{relative}/{schema.EVENT_FILE}",
        event_schema=schema.EVENT_SCHEMA,
        kind="epoch_started",
        at_ns=now_ns,
        fields={
            "epoch_id": epoch_id,
            "plan_sha256": schema.sha(plan_bytes),
            "anchor_ns": anchor_ns,
        },
    )
    _control_event(
        writer,
        kind="epoch_open",
        at_ns=now_ns,
        fields={
            "epoch_id": epoch_id,
            "plan_sha256": schema.sha(plan_bytes),
            "anchor_ns": anchor_ns,
        },
    )
    return epoch_id


def _window_event(
    writer: DescriptorWriter,
    epoch_id: str,
    *,
    kind: str,
    at_ns: int,
    fields: Mapping[str, Any],
) -> None:
    _append_event(
        writer,
        _epoch_event_path(epoch_id),
        event_schema=schema.EVENT_SCHEMA,
        kind=kind,
        at_ns=at_ns,
        fields=fields,
    )


def _intent_events(event_bytes: bytes) -> tuple[dict[str, Any], ...]:
    return tuple(
        event["fields"]
        for event in schema.verify_event_chain(event_bytes, schema=schema.EVENT_SCHEMA)
        if event["kind"] == "request_intent"
    )


def _captured_results(
    writer: DescriptorWriter, epoch_id: str, event_bytes: bytes
) -> dict[str, Any]:
    verify_epoch_captures(writer, epoch_relative=_epoch_rel(epoch_id), event_bytes=event_bytes)
    events = schema.verify_event_chain(event_bytes, schema=schema.EVENT_SCHEMA)
    result: dict[str, Any] = {}
    intents = {
        event["fields"]["request_id"]: event["fields"]
        for event in events
        if event["kind"] == "request_intent"
    }
    for event in events:
        if event["kind"] != "request_captured":
            continue
        request_id = event["fields"]["request_id"]
        path = f"{_epoch_rel(epoch_id)}/captures/{request_id}"
        verified = verify_capture(
            writer,
            relative_directory=path,
            expected={
                "request_id": request_id,
                "request_sha256": semantic_request_sha256(
                    intents[request_id]["method"], intents[request_id]["params"]
                ),
                "wire_request_sha256": intents[request_id]["wire_request_sha256"],
                "decision_ns": intents[request_id]["decision_ns"],
            },
        )
        result[request_id] = verified
    return result


def _latest_verified_anchor(
    writer: DescriptorWriter, epoch_id: str, event_bytes: bytes
) -> int | None:
    events = schema.verify_event_chain(event_bytes, schema=schema.EVENT_SCHEMA)
    results = _captured_results(writer, epoch_id, event_bytes)
    intents = {
        event["fields"]["request_id"]: event["fields"]
        for event in events
        if event["kind"] == "request_intent"
    }
    seals = [event for event in events if event["kind"] == "frame_sealed"]
    slot = None
    for seal in seals:
        for request_id in seal["fields"]["request_ids"]:
            intent = intents[request_id]
            if intent["method"] == "getSlot":
                result = results[request_id]["response"].get("result")
                slot = frame_verify.finalized_slot(result)
    return slot


def _request_id(bucket: str, ordinal: int) -> str:
    return f"{bucket}-{ordinal:02d}"


def _dispatch(
    writer: DescriptorWriter,
    epoch_id: str,
    *,
    bucket: str,
    ordinal: int,
    method: str,
    params: list[Any],
    now_ns: Callable[[], int],
    client: Any,
    clock_record: Mapping[str, Any] | None = None,
    guard_check: Callable[[], Any],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    relative = _epoch_rel(epoch_id)
    events_path = f"{relative}/{schema.EVENT_FILE}"
    event_bytes = writer.read_bytes(events_path)
    existing_intents = _intent_events(event_bytes)
    bucket_intents = [intent for intent in existing_intents if intent.get("bucket") == bucket]
    request_id = _request_id(bucket, ordinal)
    wire_sha = wire_request_sha256(method, params)
    if ordinal < len(bucket_intents):
        intent = bucket_intents[ordinal]
        if (
            intent.get("request_id") != request_id
            or intent.get("method") != method
            or intent.get("params") != params
            or intent.get("wire_request_sha256") != wire_sha
        ):
            raise RollingRuntimeError("existing_request_intent_mismatch")
        outcomes = {
            event["fields"]["request_id"]: event
            for event in schema.verify_event_chain(event_bytes, schema=schema.EVENT_SCHEMA)
            if event["kind"] in {"request_captured", "request_failed"}
        }
        if request_id in outcomes:
            if outcomes[request_id]["kind"] == "request_failed":
                raise RollingRuntimeError("previous_request_failed_possibly_spent")
            return cast(
                dict[str, Any], _captured_results(writer, epoch_id, event_bytes)[request_id]
            )
        capture_dir = f"{relative}/captures/{request_id}"
        if writer.exists(f"{capture_dir}/receipt.json") and writer.exists(
            f"{capture_dir}/response.json"
        ):
            verified = verify_capture(
                writer,
                relative_directory=capture_dir,
                expected={
                    "request_id": request_id,
                    "request_sha256": semantic_request_sha256(method, params),
                    "wire_request_sha256": wire_sha,
                    "decision_ns": intent["decision_ns"],
                },
            )
            if "result" not in verified["response"]:
                raise RollingRuntimeError("recovered_provider_error_possibly_spent")
            _window_event(
                writer,
                epoch_id,
                kind="request_captured",
                at_ns=now_ns(),
                fields={
                    "request_id": request_id,
                    "response_sha256": verified["response_sha256"],
                    "response_bytes": verified["response_bytes"],
                    "availability_ns": verified["receipt"]["availability_ns"],
                },
            )
            return verified
        raise RollingRuntimeError("ambiguous_request_possibly_spent_no_replay")
    if ordinal != len(bucket_intents):
        raise RollingRuntimeError("request_ordinal_gap")
    decision_ns = now_ns()
    if bucket_intents:
        previous_decision = bucket_intents[-1]["decision_ns"]
        elapsed = decision_ns - previous_decision
        if elapsed < 250_000_000:
            sleep((250_000_000 - elapsed) / 1_000_000_000)
    guard_check()
    _window_event(
        writer,
        epoch_id,
        kind="request_intent",
        at_ns=decision_ns,
        fields={
            "request_id": request_id,
            "bucket": bucket,
            "ordinal": ordinal,
            "method": method,
            "params": params,
            "wire_request_sha256": wire_sha,
            "decision_ns": decision_ns,
        },
    )
    try:
        capture = client.fetch(method, params)
        if clock_record is not None:
            clock_check.validate_progress(
                dict(clock_record),
                wall_ns=int(capture.received_ns),
                monotonic_ns=time.monotonic_ns(),
            )
        response = bytes(capture.response)
        if len(response) > policy.MAX_RESPONSE_BYTES:
            raise RollingRuntimeError("provider_response_too_large")
        response_decoded = schema.decode(response)
        if (
            not isinstance(response_decoded, dict)
            or response_decoded.get("jsonrpc") != "2.0"
            or type(response_decoded.get("id")) is not int
            or response_decoded.get("id") != 1
            or "error" in response_decoded
            or "result" not in response_decoded
        ):
            raise SourceError("invalid JSON-RPC response")
        capture_dir = f"{relative}/captures/{request_id}"
        writer.mkdir(capture_dir)
        response_sha = sha256(response)
        writer.create(f"{capture_dir}/response.json", response)
        receipt = {
            "schema": schema.RECEIPT_SCHEMA,
            "schema_version": 1,
            "provider": "helius-free",
            "request_id": request_id,
            "request_sha256": semantic_request_sha256(method, params),
            "wire_request_sha256": wire_sha,
            "request_started_ns": int(capture.request_started_ns),
            "received_ns": int(capture.received_ns),
            "availability_ns": int(capture.received_ns),
            "decision_ns": decision_ns,
            "response_sha256": response_sha,
            "response_bytes": len(response),
            "storage": {
                "codec": "raw",
                "path": "response.json",
                "sha256": response_sha,
                "bytes": len(response),
                "codec_version": None,
            },
        }
        writer.create(f"{capture_dir}/receipt.json", canonical_json(receipt))
        verified = verify_capture(
            writer,
            relative_directory=capture_dir,
            expected={
                "request_id": request_id,
                "request_sha256": semantic_request_sha256(method, params),
                "wire_request_sha256": wire_sha,
                "decision_ns": decision_ns,
            },
        )
        _window_event(
            writer,
            epoch_id,
            kind="request_captured",
            at_ns=now_ns(),
            fields={
                "request_id": request_id,
                "response_sha256": response_sha,
                "response_bytes": len(response),
                "availability_ns": int(capture.received_ns),
            },
        )
        return verified
    except Exception as exc:
        # Once both immutable capture files exist, a failure is local persistence or
        # verification. Preserve the durable intent and let startup replay the capture;
        # never mislabel it as a provider failure or dispatch it again.
        capture_dir = f"{relative}/captures/{request_id}"
        if writer.exists(f"{capture_dir}/receipt.json") and writer.exists(
            f"{capture_dir}/response.json"
        ):
            raise
        failure = _safe_reason(exc)
        _window_event(
            writer,
            epoch_id,
            kind="request_failed",
            at_ns=now_ns(),
            fields={
                "request_id": request_id,
                "failure_class": failure,
                "certainty": "possibly_spent",
            },
        )
        raise RollingRuntimeError(failure) from None


def _finish_epoch(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    plan_bytes: bytes,
    status: str,
    reason: str,
    pause_reason: str | None,
    budget_evidence_sha256: str | None = None,
    now_ns: int,
) -> None:
    relative = _epoch_rel(epoch_id)
    event_bytes = writer.read_bytes(f"{relative}/{schema.EVENT_FILE}")
    event_rows = schema.verify_event_chain(event_bytes, schema=schema.EVENT_SCHEMA)
    intents = {
        row["fields"]["request_id"]
        for row in event_rows
        if row["kind"] == "request_intent"
    }
    outcomes = {
        row["fields"]["request_id"]
        for row in event_rows
        if row["kind"] in {"request_captured", "request_failed"}
    }
    for request_id in sorted(intents - outcomes):
        _window_event(
            writer,
            epoch_id,
            kind="request_failed",
            at_ns=now_ns,
            fields={
                "request_id": request_id,
                "failure_class": "response_invalid",
                "certainty": "possibly_spent",
            },
        )
    events = writer.read_bytes(f"{relative}/{schema.EVENT_FILE}")
    projection = verify.verify_epoch_events(schema.decode(plan_bytes), events)
    if projection.terminal_status is None:
        _window_event(
            writer,
            epoch_id,
            kind="epoch_terminal",
            at_ns=now_ns,
            fields={
                "epoch_id": epoch_id,
                "status": status,
                "plan_sha256": schema.sha(plan_bytes),
            },
        )
    final_events = writer.read_bytes(f"{relative}/{schema.EVENT_FILE}")
    final_projection = verify.verify_epoch_events(schema.decode(plan_bytes), final_events)
    terminal = {
        "schema": schema.TERMINAL_SCHEMA,
        "schema_version": 4,
        "epoch_id": epoch_id,
        "plan_sha256": schema.sha(plan_bytes),
        "event_tail_sha256": final_projection.event_tail_sha256,
        "status": status,
        "reason": reason,
        "sealed_ns": now_ns,
        "pause_reason": pause_reason,
    }
    manifest_sha, allocation_bytes, object_count = _epoch_allocation_manifest(
        writer, epoch_id
    )
    terminal.update(
        {
            "allocation_manifest_sha256": manifest_sha,
            "allocation_upper_bytes": allocation_bytes,
            "allocation_object_count": object_count,
        }
    )
    terminal_bytes = schema.canonical(terminal)
    path = f"{relative}/{schema.TERMINAL_FILE}"
    if writer.exists(path):
        if writer.read_bytes(path) != terminal_bytes:
            raise RollingRuntimeError("terminal_receipt_conflict")
    else:
        writer.create(path, terminal_bytes)
    fields: dict[str, Any] = {
        "epoch_id": epoch_id,
        "terminal_sha256": schema.sha(terminal_bytes),
        "event_tail_sha256": final_projection.event_tail_sha256,
    }
    if status == "abandoned":
        fields["cooldown_until_ns"] = now_ns + policy.HOUR_NS
        _control_event(writer, kind="epoch_abandon", at_ns=now_ns, fields=fields)
    else:
        _control_event(writer, kind="epoch_close", at_ns=now_ns, fields=fields)
    if status == "cap_paused":
        if pause_reason is None or budget_evidence_sha256 is None:
            raise RollingRuntimeError("budget_pause_evidence_missing")
        _control_event(
            writer,
            kind="budget_pause",
            at_ns=now_ns,
            fields={"reason": pause_reason, "evidence_sha256": budget_evidence_sha256},
        )


def _current_frame(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    plan: Mapping[str, Any],
    window_index: int,
    mode: str,
    now_ns: Callable[[], int],
    client: Any,
    clock_record: Mapping[str, Any] | None = None,
    guard_check: Callable[[], Any],
    sleep: Callable[[float], None],
) -> None:
    window = plan["windows"][window_index]
    bucket = window["bucket"]
    events_path = _epoch_event_path(epoch_id)
    events_bytes = writer.read_bytes(events_path)
    projection = verify.verify_epoch_events(plan, events_bytes)
    if projection.active_bucket is None:
        current_ns = now_ns()
        disposition = policy.disposition(
            policy.Window(
                window["kind"], window["index"], window["bucket"],
                window["start_ns"], window["end_ns"], window["request_cap"],
            ),
            current_ns,
        )
        if disposition == "not_due":
            return
        if disposition == "expired_unobserved":
            _window_event(
                writer,
                epoch_id,
                kind="window_missed",
                at_ns=current_ns,
                fields={
                    "index": window_index,
                    "bucket": bucket,
                    "kind": window["kind"],
                    "start_ns": window["start_ns"],
                    "end_ns": window["end_ns"],
                },
            )
            return
        _window_event(
            writer,
            epoch_id,
            kind="window_reserved",
            at_ns=current_ns,
            fields={
                "index": window_index,
                "bucket": bucket,
                "kind": window["kind"],
                "mode": mode,
                "request_cap": window["request_cap"],
            },
        )
    elif projection.active_bucket != bucket or projection.active_mode != mode:
        raise RollingRuntimeError("active_window_plan_mismatch")

    captured = _dispatch(
        writer,
        epoch_id,
        bucket=bucket,
        ordinal=0,
        method="getSlot",
        params=[{"commitment": "finalized"}],
        now_ns=now_ns,
        client=client,
        clock_record=clock_record,
        guard_check=guard_check,
        sleep=sleep,
    )
    current_slot = frame_verify.finalized_slot(captured["response"].get("result"))
    empty_interval = False
    if mode == "inventory":
        event_bytes = writer.read_bytes(events_path)
        previous_slot = _latest_verified_anchor(writer, epoch_id, event_bytes)
        if previous_slot is None:
            raise RollingRuntimeError("previous_verified_anchor_missing")
        if current_slot < previous_slot:
            raise RollingRuntimeError("provider_finalized_slot_regression")
        if current_slot == previous_slot:
            empty_interval = True
        else:
            width = current_slot - previous_slot
            if width > policy.MAX_INVENTORY_SLOTS_PER_FRAME:
                _window_event(
                    writer,
                    epoch_id,
                    kind="window_interrupted",
                    at_ns=now_ns(),
                    fields={
                        "index": window_index,
                        "bucket": bucket,
                        "reason": "oversized_interval",
                    },
                )
                return
            inventory_call = _dispatch(
                writer,
                epoch_id,
                bucket=bucket,
                ordinal=1,
                method="getBlocks",
                params=[previous_slot + 1, current_slot, {"commitment": "finalized"}],
                now_ns=now_ns,
                client=client,
                clock_record=clock_record,
                guard_check=guard_check,
                sleep=sleep,
            )
            inventory_response = inventory_call["response"]
            inventory = frame_verify.inventory_slots(
                inventory_response.get("result"),
                start=previous_slot + 1,
                end_inclusive=current_slot,
            )
            selection = sampling.build_selection(
                epoch_id=epoch_id,
                bucket=bucket,
                inventory_slots=inventory,
                inventory_sha256=inventory_call["response_sha256"],
            )
            selection_path = f"{_epoch_rel(epoch_id)}/selections/{bucket}.json"
            writer.mkdir(f"{_epoch_rel(epoch_id)}/selections")
            selection_bytes = schema.canonical(selection)
            if writer.exists(selection_path):
                if writer.read_bytes(selection_path) != selection_bytes:
                    raise RollingRuntimeError("selection_replay_mismatch")
            else:
                writer.create(selection_path, selection_bytes)
            pairs: list[dict[str, Any]] = []
            for sample_index, selected_slot in enumerate(selection["selected_slots"]):
                ordinal = 2 + sample_index * 2
                signatures = _dispatch(
                    writer,
                    epoch_id,
                    bucket=bucket,
                    ordinal=ordinal,
                    method="getBlock",
                    params=[
                        selected_slot,
                        {
                            "commitment": "finalized",
                            "encoding": "json",
                            "transactionDetails": "signatures",
                            "rewards": False,
                        },
                    ],
                    now_ns=now_ns,
                    client=client,
                    clock_record=clock_record,
                    guard_check=guard_check,
                    sleep=sleep,
                )
                ordinal = 3 + sample_index * 2
                full = _dispatch(
                    writer,
                    epoch_id,
                    bucket=bucket,
                    ordinal=ordinal,
                    method="getBlock",
                    params=[
                        selected_slot,
                        {
                            "commitment": "finalized",
                            "encoding": "json",
                            "transactionDetails": "full",
                            "rewards": False,
                            "maxSupportedTransactionVersion": 1,
                        },
                    ],
                    now_ns=now_ns,
                    client=client,
                    clock_record=clock_record,
                    guard_check=guard_check,
                    sleep=sleep,
                )
                pairs.append(
                    {
                        "slot": selected_slot,
                        "signatures": signatures["response"].get("result"),
                        "full": full["response"].get("result"),
                    }
                )
            frame_verify.verify_sample_pairs(
                epoch_id=epoch_id,
                bucket=bucket,
                inventory=inventory,
                inventory_sha256=inventory_call["response_sha256"],
                pairs=pairs,
            )
    event_bytes = writer.read_bytes(events_path)
    projection = verify.verify_epoch_events(plan, event_bytes)
    request_ids = [
        row["request_id"] for row in _intent_events(event_bytes) if row["bucket"] == bucket
    ]
    _window_event(
        writer,
        epoch_id,
        kind="frame_sealed",
        at_ns=now_ns(),
        fields={
            "index": window_index,
            "bucket": bucket,
            "request_ids": request_ids,
            "request_count": len(request_ids),
            "empty_interval": empty_interval,
        },
    )


def _run_poll_once(
    *,
    config_path: Path = CONFIG_PATH,
    client: Any | None = None,
    clock_record: Mapping[str, Any],
    now_ns: Callable[[], int] = time.time_ns,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Verify/recover current state and dispatch at most one prospective window."""
    with _open_storage_writer(config_path) as (guard, writer):
        writer.mkdir(f"{SERVICE_RELATIVE}/epochs")
        with writer.lock(schema.LOCK_FILE):
            policy_path = f"{SERVICE_RELATIVE}/{schema.POLICY_FILE}"
            policy_bytes = schema.canonical(policy.policy_document())
            if writer.exists(policy_path):
                if writer.read_bytes(policy_path) != policy_bytes:
                    raise RollingRuntimeError("immutable_policy_changed")
            else:
                writer.create(policy_path, policy_bytes)
            if writer.exists(f"{SERVICE_RELATIVE}/STOP"):
                return {"status": "stopped", "reason": "operator_stop_marker"}
            now_for_cap = now_ns()
            allocation_cap = _allocation_cap(writer, guard, now_ns=now_for_cap)
            if _verify_lifetime_pause(writer, allocation_cap):
                return {"status": "lifetime_paused", "reason": "service_lifetime_allocation_cap"}
            active_client = client
            own_client = active_client is None
            if own_client:
                active_client = ReadOnlyRPC(
                    _private_endpoint(),
                    "helius-free",
                    timeout=30,
                    max_bytes=policy.MAX_RESPONSE_BYTES,
                    keep_alive=True,
                )
            try:
                clock_check.validate_progress(
                    dict(clock_record),
                    wall_ns=now_ns(),
                    monotonic_ns=time.monotonic_ns(),
                )
                state, plans, projections = _control_projection(
                    writer, root_id=guard.root_id, volume_uuid=guard.volume_uuid
                )
                current_ns = now_ns()
                if state.budget_paused:
                    rolling_usage, _ = _rolling_usage(writer, plans, at_ns=current_ns)
                    if policy.blocked_reason(
                        rolling_usage,
                        epoch_requests=0,
                        epoch_response_bytes=0,
                        epoch_allocated_bytes=0,
                    ) is not None:
                        return {"status": "budget_paused", "reason": state.pause_reason}
                    assert state.pause_reason is not None
                    evidence_hash = _write_budget_evidence(
                        writer,
                        reason=state.pause_reason,
                        disposition="headroom",
                        at_ns=current_ns,
                        usage=rolling_usage,
                    )
                    _control_event(
                        writer,
                        kind="budget_resume",
                        at_ns=current_ns,
                        fields={
                            "reason": state.pause_reason,
                            "evidence_sha256": evidence_hash,
                        },
                    )
                    state, plans, projections = _control_projection(
                        writer, root_id=guard.root_id, volume_uuid=guard.volume_uuid
                    )
                if state.active_epoch_id is None:
                    if current_ns < state.cooldown_until_ns:
                        return {"status": "cooldown", "until_ns": state.cooldown_until_ns}
                    service_bytes = _service_allocated_upper_bytes(writer)
                    cap_bytes = allocation_cap["service_allocation_cap_bytes"]
                    full_epoch_free = guard.revalidate(
                        min_free_bytes=(
                            policy.DISK_FLOOR_BYTES
                            + policy.SERVICE_OPERATING_RESERVE_BYTES
                            + policy.SERVICE_PEAK_METADATA_RESERVE_BYTES
                        )
                    )
                    if service_bytes + policy.NEXT_EPOCH_ALLOCATION_RESERVE_BYTES > cap_bytes:
                        _persist_lifetime_pause(
                            writer,
                            cap=allocation_cap,
                            observed_bytes=service_bytes,
                            at_ns=current_ns,
                        )
                        return {
                            "status": "lifetime_paused",
                            "reason": "service_lifetime_allocation_cap",
                        }
                    if (
                        full_epoch_free.available_bytes
                        < policy.DISK_FLOOR_BYTES
                        + policy.SERVICE_OPERATING_RESERVE_BYTES
                        + policy.NEXT_EPOCH_ALLOCATION_RESERVE_BYTES
                        + policy.SERVICE_PEAK_METADATA_RESERVE_BYTES
                    ):
                        return {"status": "capacity_paused", "reason": "next_epoch_headroom"}
                    epoch_id = _open_epoch(
                        writer,
                        root_id=guard.root_id,
                        volume_uuid=guard.volume_uuid,
                        now_ns=current_ns,
                    )
                    plan_bytes = writer.read_bytes(f"{_epoch_rel(epoch_id)}/{schema.PLAN_FILE}")
                    plan = schema.decode(plan_bytes)
                    plans = {**plans, epoch_id: plan_bytes}
                    window_index = 0
                    mode = "anchor"
                else:
                    epoch_id = state.active_epoch_id
                    plan_bytes = plans[epoch_id]
                    plan = verify.verify_plan(
                        plan_bytes,
                        expected_sources=source_bindings(),
                        expected_root_id=guard.root_id,
                        expected_volume_uuid=guard.volume_uuid,
                    )
                    projection = projections[epoch_id]
                    if projection.terminal_status is not None:
                        terminal_payload = writer.read_bytes(
                            f"{_epoch_rel(epoch_id)}/{schema.TERMINAL_FILE}"
                        )
                        terminal_value = schema.decode(terminal_payload)
                        terminal = verify.VerifiedTerminal(
                            schema.sha(terminal_payload),
                            projection.event_tail_sha256 or "",
                            cast(verify.TerminalStatus, projection.terminal_status),
                            terminal_value["pause_reason"],
                        )
                        control_fields: dict[str, Any] = {
                            "epoch_id": epoch_id,
                            "terminal_sha256": terminal.terminal_sha256,
                            "event_tail_sha256": terminal.event_tail_sha256,
                        }
                        if projection.terminal_status == "abandoned":
                            control_fields["cooldown_until_ns"] = current_ns + policy.HOUR_NS
                            _control_event(
                                writer,
                                kind="epoch_abandon",
                                at_ns=current_ns,
                                fields=control_fields,
                            )
                        else:
                            _control_event(
                                writer,
                                kind="epoch_close",
                                at_ns=current_ns,
                                fields=control_fields,
                            )
                            if projection.terminal_status == "cap_paused":
                                matching = [
                                    digest
                                    for digest, disposition in _budget_evidence_map(
                                        writer, plans
                                    ).items()
                                    if disposition == f"exceeded:{terminal.pause_reason}"
                                ]
                                if not matching:
                                    raise RollingRuntimeError(
                                        "pending_budget_terminal_evidence_missing"
                                    )
                                _control_event(
                                    writer,
                                    kind="budget_pause",
                                    at_ns=current_ns,
                                    fields={
                                        "reason": terminal.pause_reason,
                                        "evidence_sha256": matching[-1],
                                    },
                                )
                        return {"status": "terminal_reconciled", "epoch_id": epoch_id}
                    window_index = projection.next_window_index
                    if window_index >= len(plan["windows"]):
                        _finish_epoch(
                            writer,
                            epoch_id=epoch_id,
                            plan_bytes=plan_bytes,
                            status="complete",
                            reason="schedule_complete",
                            pause_reason=None,
                            now_ns=current_ns,
                        )
                        return {"status": "epoch_complete", "epoch_id": epoch_id}
                    if projection.active_mode is not None:
                        mode = projection.active_mode
                    elif window_index == 0:
                        mode = "anchor"
                    elif projection.reset_anchor_required:
                        mode = "anchor_reset"
                    else:
                        mode = "inventory"
                guard.revalidate(min_free_bytes=policy.DISK_FLOOR_BYTES)

                def admission_check() -> None:
                    clock_check.validate_progress(
                        dict(clock_record),
                        wall_ns=now_ns(),
                        monotonic_ns=time.monotonic_ns(),
                    )
                    guard.revalidate(min_free_bytes=policy.DISK_FLOOR_BYTES)
                    check_ns = now_ns()
                    allocated = _service_allocated_upper_bytes(writer)
                    if (
                        allocated
                        + policy.MAX_RESPONSE_BYTES
                        + policy.PER_REQUEST_METADATA_RESERVE_BYTES
                        + policy.SERVICE_PEAK_METADATA_RESERVE_BYTES
                        > allocation_cap["service_allocation_cap_bytes"]
                    ):
                        raise LifetimeCapStop("service_lifetime_allocation_cap")
                    guard.revalidate(
                        min_free_bytes=(
                            policy.DISK_FLOOR_BYTES
                            + policy.SERVICE_OPERATING_RESERVE_BYTES
                            + policy.MAX_RESPONSE_BYTES
                            + policy.PER_REQUEST_METADATA_RESERVE_BYTES
                            + policy.SERVICE_PEAK_METADATA_RESERVE_BYTES
                        )
                    )
                    reason, _usage, _epoch_usage = _budget_snapshot(
                        writer,
                        plans=plans,
                        epoch_id=epoch_id,
                        now_ns=check_ns,
                    )
                    if reason is not None:
                        raise BudgetStop(reason, check_ns)

                _current_frame(
                    writer,
                    epoch_id=epoch_id,
                    plan=plan,
                    window_index=window_index,
                    mode=mode,
                    now_ns=now_ns,
                    client=active_client,
                    clock_record=clock_record,
                    guard_check=admission_check,
                    sleep=sleep,
                )
                event_bytes = writer.read_bytes(_epoch_event_path(epoch_id))
                next_projection = verify.verify_epoch_events(plan, event_bytes)
                return {
                    "status": "window_processed",
                    "epoch_id": epoch_id,
                    "next_window_index": next_projection.next_window_index,
                    "active_bucket": next_projection.active_bucket,
                    "request_count": len(next_projection.request_states),
                }
            except (
                BudgetStop,
                LifetimeCapStop,
                RollingRuntimeError,
                CaptureVerifyError,
                frame_verify.FrameVerifyError,
                verify.VerifyError,
                schema.SchemaError,
                StorageError,
            ) as exc:
                if isinstance(exc, LifetimeCapStop):
                    try:
                        _persist_lifetime_pause(
                            writer,
                            cap=allocation_cap,
                            observed_bytes=_service_allocated_upper_bytes(writer),
                            at_ns=now_ns(),
                        )
                        return {
                            "status": "lifetime_paused",
                            "reason": "service_lifetime_allocation_cap",
                        }
                    except Exception:
                        return {"status": "integrity_stop", "reason": "lifetime_pause_write_failed"}
                active_epoch = locals().get("epoch_id")
                plan_payload = locals().get("plan_bytes")
                if isinstance(active_epoch, str) and isinstance(plan_payload, bytes):
                    try:
                        if isinstance(exc, BudgetStop):
                            existing_plans = locals().get("plans", {})
                            if not isinstance(existing_plans, dict):
                                existing_plans = {}
                            existing_plans = {**existing_plans, active_epoch: plan_payload}
                            _reason, usage, epoch_usage = _budget_snapshot(
                                writer,
                                plans=existing_plans,
                                epoch_id=active_epoch,
                                now_ns=exc.checked_ns,
                            )
                            evidence_hash = _write_budget_evidence(
                                writer,
                                reason=exc.reason,
                                disposition="exceeded",
                                at_ns=exc.checked_ns,
                                usage=usage,
                                epoch_usage=(
                                    epoch_usage if exc.reason.startswith("epoch_") else None
                                ),
                                epoch_id=(
                                    active_epoch if exc.reason.startswith("epoch_") else None
                                ),
                            )
                            _finish_epoch(
                                writer,
                                epoch_id=active_epoch,
                                plan_bytes=plan_payload,
                                status="cap_paused",
                                reason="finite_budget_cap_reached",
                                pause_reason=exc.reason,
                                budget_evidence_sha256=evidence_hash,
                                now_ns=now_ns(),
                            )
                            return {"status": "budget_paused", "reason": exc.reason}
                        _finish_epoch(
                            writer,
                            epoch_id=active_epoch,
                            plan_bytes=plan_payload,
                            status="abandoned",
                            reason=_safe_reason(exc),
                            pause_reason=None,
                            now_ns=now_ns(),
                        )
                        return {"status": "epoch_abandoned", "reason": _safe_reason(exc)}
                    except Exception:
                        pass
                return {"status": "integrity_stop", "reason": _safe_reason(exc)}
            finally:
                if own_client and active_client is not None:
                    active_client.close()


def _record_poll(result: Mapping[str, Any], *, config_path: Path) -> None:
    with _open_storage_writer(config_path) as (guard, writer):
        writer.mkdir(f"{SERVICE_RELATIVE}/epochs")
        writer.mkdir(f"{SERVICE_RELATIVE}/monitor")
        with writer.lock(schema.LOCK_FILE):
            cap = _allocation_cap(writer, guard, now_ns=time.time_ns())
            allocation_cap_sha256 = schema.sha(writer.read_bytes(ALLOCATION_CAP_FILE))
            service_allocated = _service_allocated_upper_bytes(writer)
            live = guard.revalidate(
                min_free_bytes=(
                    policy.DISK_FLOOR_BYTES
                    + policy.SERVICE_OPERATING_RESERVE_BYTES
                    + policy.SERVICE_PEAK_METADATA_RESERVE_BYTES
                )
            )
            old_raw = _read_optional(writer, MONITOR_FILE, limit=64_000)
            old = schema.decode(old_raw) if old_raw else None
            old_without_receipt = (
                {key: value for key, value in old.items() if key != "receipt_sha256"}
                if isinstance(old, dict)
                else None
            )
            if old is not None and (
                not isinstance(old, dict)
                or old.get("schema") != MONITOR_SCHEMA
                or old.get("schema_version") != 4
                or schema.canonical(cast(dict[str, Any], old_without_receipt)) != old_raw
                or schema.sha(schema.canonical(cast(dict[str, Any], old_without_receipt)))
                != old.get("receipt_sha256")
            ):
                raise RollingRuntimeError("poll_monitor_receipt_invalid")
            old_seq = old.get("seq", -1) if isinstance(old, dict) else -1
            previous_sha = schema.sha(old_raw) if old_raw else None
            record = {
                "schema": MONITOR_SCHEMA,
                "schema_version": 4,
                "seq": old_seq + 1,
                "at_ns": time.time_ns(),
                "status": str(result.get("status", "unknown")),
                "reason": result.get("reason") if isinstance(result.get("reason"), str) else None,
                "epoch_id": (
                    result.get("epoch_id") if isinstance(result.get("epoch_id"), str) else None
                ),
                "next_window_index": (
                    result.get("next_window_index")
                    if type(result.get("next_window_index")) is int
                    else None
                ),
                "request_count": (
                    result.get("request_count")
                    if type(result.get("request_count")) is int
                    else None
                ),
                "service_allocation_cap_sha256": allocation_cap_sha256,
                "service_allocated_upper_bytes": service_allocated,
                "service_allocation_cap_bytes": cap["service_allocation_cap_bytes"],
                "volume_available_bytes": live.available_bytes,
                "clock_record": result["clock_record"],
                "clock_sha256": schema.sha(schema.canonical(result["clock_record"])),
                "previous_sha256": previous_sha,
            }
            record["receipt_sha256"] = schema.sha(schema.canonical(record))
            writer.atomic_write(MONITOR_FILE, schema.canonical(record))


def run_poll(
    *,
    config_path: Path = CONFIG_PATH,
    client: Any | None = None,
    now_ns: Callable[[], int] = time.time_ns,
    sleep: Callable[[float], None] = time.sleep,
    clock_probe: Callable[[], dict[str, Any]] = clock_check.probe_pair,
) -> dict[str, Any]:
    """Run one tick and persist its sanitized outcome on the configured volume."""
    try:
        clock_record = clock_probe()
        clock_check.validate_clock(clock_record, now_ns=now_ns())
        result = _run_poll_once(
            config_path=config_path,
            client=client,
            clock_record=clock_record,
            now_ns=now_ns,
            sleep=sleep,
        )
    except Exception as exc:
        clock_record = {
            "schema": "brain-senses-clock-probe-unavailable",
            "schema_version": 0,
            "samples": [],
            "checked_ns": now_ns(),
            "monotonic_ns": time.monotonic_ns(),
        }
        result = {
            "status": "integrity_stop",
            "reason": (
                "clock_probe_failed"
                if isinstance(exc, clock_check.ClockError)
                else "uncaught_sanitized_failure"
            ),
        }
    result["clock_record"] = clock_record
    try:
        _record_poll(result, config_path=config_path)
    except Exception:
        return {"status": "integrity_stop", "reason": "monitor_receipt_write_failed"}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scheduled-tick", action="store_true")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--plist", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.scheduled_tick:
        parser.error("--scheduled-tick is required")
    expected_plist = REPO / "ops/launchd/ai.memecoin.brain.senses.external-rolling-v4.plist"
    if args.plist.resolve() != expected_plist.resolve():
        return 2
    try:
        plist = plistlib.loads(expected_plist.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return 2
    expected_arguments = [
        str(REPO / ".venv/bin/python"),
        "-B",
        str(REPO / "tools/brain_senses_external_orchestrator_v4.py"),
        "--scheduled-tick",
        "--plist",
        str(expected_plist),
    ]
    if (
        not isinstance(plist, dict)
        or plist.get("Label") != "ai.memecoin.brain.senses.external-rolling-v4"
        or plist.get("ProgramArguments") != expected_arguments
        or plist.get("StartInterval") != 60
        or plist.get("RunAtLoad") is not True
        or plist.get("KeepAlive") is not False
        or plist.get("StandardOutPath") != "/dev/null"
        or plist.get("StandardErrorPath") != "/dev/null"
        or sys.executable != expected_arguments[0]
    ):
        return 2
    try:
        result = run_poll(config_path=args.config)
    except (StorageError, OSError, RollingRuntimeError, verify.VerifyError, schema.SchemaError):
        return 1
    # All operational receipts stay on the explicitly bound Kingston root.
    return (
        0
        if result.get("status")
        in {
            "window_processed",
            "cooldown",
            "budget_paused",
            "stopped",
            "terminal_reconciled",
            "epoch_complete",
            "epoch_abandoned",
            "lifetime_paused",
            "capacity_paused",
        }
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
