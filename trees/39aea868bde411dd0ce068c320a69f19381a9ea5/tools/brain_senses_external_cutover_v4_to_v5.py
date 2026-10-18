#!/usr/bin/env python3
"""No-network, no-replay reconciliation of an unstarted rolling-v4 epoch.

This helper is deliberately separate from the pinned v4 collector. Its public
entry point requires an injected LaunchAgent controller and a descriptor-bound
writer, which keeps tests synthetic and makes accidental provider dispatch
impossible here.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, Protocol, cast

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from memecoin_research_storage import (  # noqa: E402
    StorageConfig,
    StorageError,
    _open_relative_directory,
    writer_guard,
)
from tools import brain_senses_external_frame_verify_v4 as frame_verify  # noqa: E402
from tools import brain_senses_external_launch_v2 as launch  # noqa: E402
from tools import brain_senses_external_orchestrator_v4 as runtime  # noqa: E402
from tools import brain_senses_external_rolling_policy_v1 as policy  # noqa: E402
from tools import brain_senses_external_sampling_v4 as sampling  # noqa: E402
from tools import brain_senses_external_schema_v4 as schema  # noqa: E402
from tools import brain_senses_external_verify_v4 as verify  # noqa: E402
from tools.brain_senses_external_capture_verify_v4 import (  # noqa: E402
    verify_epoch_captures,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402

STOP_FILE = f"{schema.SERVICE_RELATIVE_ROOT}/STOP"
STOP_SCHEMA = "brain-senses-external-v4-operator-stop"
EXCEPTION_PATH = "services/external-rolling-v5/cutover-exception-v1.json"
EXCEPTION_SCHEMA = "brain-senses-external-v4-cutover-forensic-exception"
STOP_FIELDS = {
    "schema",
    "schema_version",
    "service_id",
    "epoch_id",
    "plan_sha256",
    "reason",
    "created_ns",
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_LABEL = "ai.memecoin.brain.senses.external-rolling-v4"
_V4_SCRIPT = str(REPO / "tools/brain_senses_external_orchestrator_v4.py")
# Exact v4 orchestrator from the immediately preceding verified implementation
# commit; this is the sole known historical source drift in the closed prefix.
_PREVIOUS_V4_RUNTIME_SHA256 = "719c8e6f3c476c6251d9da6c824eeb45fd047ff9657c3b6d231d7c18fed7ed1f"
DEFAULT_STORAGE_CONFIG = Path.home() / ".config/brain-research/storage-v1.json"
_DISPATCH_FLOOR = (
    policy.DISK_FLOOR_BYTES
    + policy.SERVICE_OPERATING_RESERVE_BYTES
    + policy.SERVICE_PEAK_METADATA_RESERVE_BYTES
)


class CutoverError(RuntimeError):
    """Sanitized fail-closed cutover state."""


class AgentControl(Protocol):
    def disable_bootout_and_verify(self) -> None: ...


class LaunchctlAgentControl:
    """Small explicit launchctl adapter; never starts or loads a service."""

    def __init__(self, *, uid: int | None = None) -> None:
        self.uid = os.getuid() if uid is None else uid
        if type(self.uid) is not int or self.uid <= 0:
            raise CutoverError("launchctl_uid_invalid")
        self.domain = f"gui/{self.uid}"

    @staticmethod
    def _run(argv: list[str]) -> str:
        try:
            result = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                env={"PATH": "/bin:/usr/bin:/sbin:/usr/sbin"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CutoverError("launchctl_unavailable") from exc
        if result.returncode != 0:
            raise CutoverError("launchctl_command_failed")
        return result.stdout

    def disable_bootout_and_verify(self) -> None:
        target = f"{self.domain}/{_LABEL}"
        self._run(["/bin/launchctl", "disable", target])
        # bootout can report failure if already absent; in that case the
        # subsequent state checks still decide whether the gate is satisfied.
        try:
            self._run(["/bin/launchctl", "bootout", target])
        except CutoverError:
            pass
        loaded = self._run(["/bin/launchctl", "list"])
        if any(line.split() and line.split()[-1] == _LABEL for line in loaded.splitlines()):
            raise CutoverError("v4_agent_still_loaded")
        processes = self._run(["/bin/ps", "-axo", "pid=,command="])
        if any(
            _V4_SCRIPT in line and "--scheduled-tick" in line for line in processes.splitlines()
        ):
            raise CutoverError("v4_process_still_running")
        disabled = self._run(["/bin/launchctl", "print-disabled", self.domain])
        disabled_lines = {line.strip() for line in disabled.splitlines() if line.strip()}
        if not {
            f'"{_LABEL}" => true',
            f'"{_LABEL}" => disabled',
        }.intersection(disabled_lines):
            raise CutoverError("v4_agent_not_disabled")


def _epoch_relative(epoch_id: str) -> str:
    schema.validate_epoch_id(epoch_id)
    return f"{schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch_id}"


def _load_plan(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    expected_plan_sha256: str,
    root_id: str,
    volume_uuid: str,
) -> tuple[bytes, dict[str, Any]]:
    if not _SHA256.fullmatch(expected_plan_sha256):
        raise CutoverError("plan_hash_invalid")
    relative = _epoch_relative(epoch_id)
    plan_bytes = writer.read_bytes(f"{relative}/{schema.PLAN_FILE}", max_bytes=1_000_000)
    if schema.sha(plan_bytes) != expected_plan_sha256:
        raise CutoverError("plan_hash_mismatch")
    try:
        plan = verify.verify_plan(
            plan_bytes,
            expected_sources=runtime.source_bindings(),
            expected_root_id=root_id,
            expected_volume_uuid=volume_uuid,
        )
    except Exception as exc:
        raise CutoverError("plan_binding_invalid") from exc
    if plan["epoch_id"] != epoch_id:
        raise CutoverError("plan_epoch_mismatch")
    return plan_bytes, plan


def _validate_stop(
    writer: DescriptorWriter, *, epoch_id: str, plan_sha256: str
) -> dict[str, Any] | None:
    if not writer.exists(STOP_FILE):
        return None
    raw = writer.read_bytes(STOP_FILE, max_bytes=16_000)
    try:
        value = schema.decode(raw)
    except Exception as exc:
        raise CutoverError("stop_marker_invalid") from exc
    if (
        not isinstance(value, dict)
        or set(value) != STOP_FIELDS
        or schema.canonical(value) != raw
        or value.get("schema") != STOP_SCHEMA
        or value.get("schema_version") != 1
        or value.get("service_id") != schema.SERVICE_ID
        or value.get("epoch_id") != epoch_id
        or value.get("plan_sha256") != plan_sha256
        or value.get("reason") != "operator_cutover_before_dispatch"
        or type(value.get("created_ns")) is not int
        or value["created_ns"] <= 0
    ):
        raise CutoverError("stop_marker_binding_invalid")
    return value


def _event_rows(writer: DescriptorWriter, epoch_id: str) -> list[dict[str, Any]]:
    raw = writer.read_bytes(f"{_epoch_relative(epoch_id)}/{schema.EVENT_FILE}", max_bytes=8_000_000)
    if raw and not raw.endswith(b"\n"):
        # A torn epoch journal can conceal a durable request intent. Never
        # repair it into an apparently unspent epoch.
        raise CutoverError("epoch_journal_torn_possibly_spent")
    try:
        return list(schema.verify_event_chain(raw, schema=schema.EVENT_SCHEMA))
    except Exception as exc:
        raise CutoverError("epoch_journal_invalid") from exc


def _assert_no_dispatch_artifacts(writer: DescriptorWriter, epoch_id: str) -> None:
    relative = _epoch_relative(epoch_id)
    names = set(writer.list_names(relative))
    allowed = {schema.PLAN_FILE, schema.EVENT_FILE, "captures"}
    if schema.TERMINAL_FILE in names:
        allowed.add(schema.TERMINAL_FILE)
    if names != allowed:
        raise CutoverError("epoch_artifact_set_unexpected")
    if writer.list_names(f"{relative}/captures"):
        raise CutoverError("epoch_capture_artifact_present")


def _target_control_events(writer: DescriptorWriter, epoch_id: str) -> list[dict[str, Any]]:
    path = f"{schema.SERVICE_RELATIVE_ROOT}/{schema.CONTROL_FILE}"
    raw = runtime._repair_torn_chain(writer, path)
    try:
        rows = schema.verify_event_chain(raw, schema=schema.CONTROL_SCHEMA)
    except Exception as exc:
        raise CutoverError("control_journal_invalid") from exc
    return [row for row in rows if row["fields"].get("epoch_id") == epoch_id]


def _ensure_target_open(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    plan: Mapping[str, Any],
    plan_bytes: bytes,
    now_ns: int,
) -> None:
    path = f"{schema.SERVICE_RELATIVE_ROOT}/{schema.CONTROL_FILE}"
    runtime._repair_torn_chain(writer, path)
    raw = writer.read_bytes(path, max_bytes=8_000_000) if writer.exists(path) else b""
    if raw and not raw.endswith(b"\n"):
        raise CutoverError("control_repair_did_not_complete")
    try:
        rows = schema.verify_event_chain(raw, schema=schema.CONTROL_SCHEMA)
    except Exception as exc:
        raise CutoverError("control_journal_invalid") from exc
    target = [row for row in rows if row["fields"].get("epoch_id") == epoch_id]
    opens = [row for row in target if row["kind"] == "epoch_open"]
    closes = [row for row in target if row["kind"] in {"epoch_abandon", "epoch_close"}]
    if len(opens) > 1 or len(closes) > 1:
        raise CutoverError("control_target_duplicate")
    if opens:
        event = opens[0]
        if event["fields"] != {
            "epoch_id": epoch_id,
            "plan_sha256": schema.sha(plan_bytes),
            "anchor_ns": plan["anchor_ns"],
        }:
            raise CutoverError("control_open_binding_invalid")
        if closes:
            return
        if rows[-1]["event_sha256"] != event["event_sha256"]:
            # There must be no later active epoch or unrelated control change.
            later = rows[rows.index(event) + 1 :]
            if any(row["kind"] in {"epoch_open", "budget_pause", "budget_resume"} for row in later):
                raise CutoverError("control_active_state_conflict")
        return
    if target:
        raise CutoverError("control_target_without_open")
    # An epoch_started plan may be left by a crash just before epoch_open.
    # Reconstruct the missing event only if its anchor is still prospective
    # relative to the last control event and callback time.
    last_at = rows[-1]["at_ns"] if rows else 0
    open_at = max(plan["created_ns"], last_at, now_ns)
    if plan["anchor_ns"] <= open_at:
        raise CutoverError("unopened_epoch_no_longer_future")
    runtime._control_event(
        writer,
        kind="epoch_open",
        at_ns=open_at,
        fields={
            "epoch_id": epoch_id,
            "plan_sha256": schema.sha(plan_bytes),
            "anchor_ns": plan["anchor_ns"],
        },
    )


def _terminal_file(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    plan_bytes: bytes,
    expected_event_tail: str,
) -> tuple[dict[str, Any], bytes]:
    relative = _epoch_relative(epoch_id)
    path = f"{relative}/{schema.TERMINAL_FILE}"
    raw = writer.read_bytes(path, max_bytes=64_000)
    value = schema.decode(raw)
    manifest_sha, allocation_bytes, object_count = runtime._epoch_allocation_manifest(
        writer, epoch_id
    )
    required = {
        "schema",
        "schema_version",
        "epoch_id",
        "plan_sha256",
        "event_tail_sha256",
        "status",
        "reason",
        "sealed_ns",
        "pause_reason",
        "allocation_manifest_sha256",
        "allocation_upper_bytes",
        "allocation_object_count",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or schema.canonical(value) != raw
        or value.get("schema") != schema.TERMINAL_SCHEMA
        or value.get("schema_version") != 4
        or value.get("epoch_id") != epoch_id
        or value.get("plan_sha256") != schema.sha(plan_bytes)
        or value.get("event_tail_sha256") != expected_event_tail
        or value.get("status") != "abandoned"
        or value.get("reason") != "operator_cutover_before_dispatch"
        or type(value.get("sealed_ns")) is not int
        or value["sealed_ns"] <= 0
        or value.get("pause_reason") is not None
        or value.get("allocation_manifest_sha256") != manifest_sha
        or value.get("allocation_upper_bytes") != allocation_bytes
        or value.get("allocation_object_count") != object_count
    ):
        raise CutoverError("terminal_receipt_invalid")
    return value, raw


def _ensure_abandon_control(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    terminal: Mapping[str, Any],
    terminal_bytes: bytes,
    event_tail: str,
    now_ns: int,
) -> None:
    path = f"{schema.SERVICE_RELATIVE_ROOT}/{schema.CONTROL_FILE}"
    raw = writer.read_bytes(path)
    rows = schema.verify_event_chain(raw, schema=schema.CONTROL_SCHEMA)
    matching = [
        row
        for row in rows
        if row["kind"] == "epoch_abandon" and row["fields"].get("epoch_id") == epoch_id
    ]
    if matching:
        event = matching[0]
        expected = {
            "epoch_id": epoch_id,
            "terminal_sha256": schema.sha(terminal_bytes),
            "event_tail_sha256": event_tail,
        }
        fields = event["fields"]
        if (
            len(matching) != 1
            or set(fields) != set(expected) | {"cooldown_until_ns"}
            or any(fields.get(key) != value for key, value in expected.items())
            or type(fields.get("cooldown_until_ns")) is not int
            or fields["cooldown_until_ns"] < event["at_ns"] + policy.HOUR_NS
        ):
            raise CutoverError("control_abandon_binding_invalid")
        return
    target_open = [
        row
        for row in rows
        if row["kind"] == "epoch_open" and row["fields"].get("epoch_id") == epoch_id
    ]
    if len(target_open) != 1:
        raise CutoverError("control_open_missing")
    at_ns = max(now_ns, terminal["sealed_ns"], rows[-1]["at_ns"] if rows else 0)
    runtime._control_event(
        writer,
        kind="epoch_abandon",
        at_ns=at_ns,
        fields={
            "epoch_id": epoch_id,
            "terminal_sha256": schema.sha(terminal_bytes),
            "event_tail_sha256": event_tail,
            "cooldown_until_ns": at_ns + policy.HOUR_NS,
        },
    )


def _verify_all_epochs_terminal(
    writer: DescriptorWriter,
    *,
    root_id: str,
    volume_uuid: str,
    expected_epoch_id: str,
    allow_expected_active: bool = False,
) -> tuple[verify.ControlProjection, dict[str, Any] | None]:
    """Read-only full replay: no other epoch may remain active or unclosed."""
    epochs_root = f"{schema.SERVICE_RELATIVE_ROOT}/epochs"
    expected_sources = runtime.source_bindings()
    plans: dict[str, bytes] = {}
    terminals: dict[str, verify.VerifiedTerminal] = {}
    projections: dict[str, verify.EpochProjection] = {}
    for epoch_id in writer.list_names(epochs_root):
        try:
            schema.validate_epoch_id(epoch_id)
            relative = _epoch_relative(epoch_id)
            names = set(writer.list_names(relative))
            if not {schema.PLAN_FILE, schema.EVENT_FILE, "captures"}.issubset(names):
                raise CutoverError("epoch_artifact_set_incomplete")
            allowed = {schema.PLAN_FILE, schema.EVENT_FILE, "captures"}
            # v4 persists deterministic sample selections for inventory frames.
            # The canonical frame replay below verifies each expected selection
            # byte-for-byte; also reject unreferenced selection files here.
            event_bytes = writer.read_bytes(f"{relative}/{schema.EVENT_FILE}", max_bytes=8_000_000)
            if event_bytes and not event_bytes.endswith(b"\n"):
                raise CutoverError("epoch_journal_torn_possibly_spent")
            event_rows = schema.verify_event_chain(event_bytes, schema=schema.EVENT_SCHEMA)
            reserved_modes = {
                row["fields"].get("index"): row["fields"]
                for row in event_rows
                if row["kind"] == "window_reserved"
            }
            inventory_buckets = {
                reserved.get("bucket")
                for reserved in reserved_modes.values()
                if reserved.get("mode") == "inventory"
            }
            if "selections" in names:
                allowed.add("selections")
                selection_names = set(writer.list_names(f"{relative}/selections"))
                if any(
                    not isinstance(bucket, str) or f"{bucket}.json" not in selection_names
                    for bucket in inventory_buckets
                    if any(
                        row["kind"] == "request_captured"
                        and row["fields"].get("bucket") == bucket
                        and any(
                            intent["fields"].get("bucket") == bucket
                            and intent["fields"].get("method") == "getBlocks"
                            for intent in event_rows
                            if intent["kind"] == "request_intent"
                            and intent["fields"].get("request_id")
                            == row["fields"].get("request_id")
                        )
                        for row in event_rows
                    )
                ) or not selection_names.issubset(
                    {f"{bucket}.json" for bucket in inventory_buckets if isinstance(bucket, str)}
                ):
                    raise CutoverError("selection_artifact_set_invalid")
                captured = runtime._captured_results(writer, epoch_id, event_bytes)
                intents = {
                    row["fields"]["request_id"]: row["fields"]
                    for row in event_rows
                    if row["kind"] == "request_intent"
                }
                for selection_name in selection_names:
                    bucket = selection_name.removesuffix(".json")
                    inventory_intent = next(
                        (
                            fields
                            for fields in intents.values()
                            if fields.get("bucket") == bucket
                            and fields.get("method") == "getBlocks"
                            and fields.get("request_id") in captured
                        ),
                        None,
                    )
                    if inventory_intent is None:
                        raise CutoverError("selection_without_inventory_capture")
                    verified_capture = captured[inventory_intent["request_id"]]
                    params = inventory_intent.get("params")
                    if not isinstance(params, list) or len(params) != 3:
                        raise CutoverError("selection_inventory_request_invalid")
                    inventory = frame_verify.inventory_slots(
                        verified_capture["response"].get("result"),
                        start=params[0],
                        end_inclusive=params[1],
                    )
                    expected_selection = sampling.build_selection(
                        epoch_id=epoch_id,
                        bucket=bucket,
                        inventory_slots=inventory,
                        inventory_sha256=verified_capture["response_sha256"],
                    )
                    selection_raw = writer.read_bytes(
                        f"{relative}/selections/{selection_name}", max_bytes=1_000_000
                    )
                    if selection_raw != schema.canonical(expected_selection):
                        raise CutoverError("selection_artifact_replay_mismatch")
            elif inventory_buckets:
                events_have_captured_inventory = any(
                    row["kind"] == "request_captured"
                    and row["fields"].get("request_id")
                    in {
                        intent["fields"].get("request_id")
                        for intent in event_rows
                        if intent["kind"] == "request_intent"
                        and intent["fields"].get("method") == "getBlocks"
                    }
                    for row in event_rows
                )
                if events_have_captured_inventory:
                    raise CutoverError("selection_artifact_missing")
            if schema.TERMINAL_FILE in names:
                allowed.add(schema.TERMINAL_FILE)
            if names != allowed:
                raise CutoverError("epoch_artifact_set_unexpected")
            plan_bytes = writer.read_bytes(f"{relative}/{schema.PLAN_FILE}", max_bytes=1_000_000)
            plan_value = schema.decode(plan_bytes)
            if not isinstance(plan_value, dict) or not isinstance(
                plan_value.get("source_bindings"), dict
            ):
                raise CutoverError("epoch_plan_invalid")
            planned_sources = plan_value["source_bindings"]
            sources_match = planned_sources == expected_sources
            runtime_only_change = (
                set(planned_sources) == set(expected_sources)
                and {
                    path
                    for path in expected_sources
                    if planned_sources.get(path) != expected_sources[path]
                }
                == {"tools/brain_senses_external_orchestrator_v4.py"}
                and (
                    planned_sources.get("tools/brain_senses_external_orchestrator_v4.py")
                    == _PREVIOUS_V4_RUNTIME_SHA256
                )
            )
            if not sources_match and not runtime_only_change:
                raise CutoverError("epoch_source_binding_invalid")
            plan = verify.verify_plan(
                plan_bytes,
                expected_sources=expected_sources if sources_match else planned_sources,
                expected_root_id=root_id,
                expected_volume_uuid=volume_uuid,
            )
            if plan.get("epoch_id") != epoch_id:
                raise CutoverError("epoch_plan_path_mismatch")
            projection = verify.verify_epoch_events(plan, event_bytes)
            verify_epoch_captures(writer, epoch_relative=relative, event_bytes=event_bytes)
            runtime._verify_sealed_frames(
                writer,
                epoch_id=epoch_id,
                plan=plan,
                event_bytes=event_bytes,
            )
            terminal_path = f"{relative}/{schema.TERMINAL_FILE}"
            terminal_present = writer.exists(terminal_path)
            if projection.terminal_status is None or not terminal_present:
                if not allow_expected_active or epoch_id != expected_epoch_id or terminal_present:
                    raise CutoverError("other_epoch_active_or_unterminalized")
                plans[epoch_id] = plan_bytes
                projections[epoch_id] = projection
                if projection.terminal_status is not None:
                    terminals[epoch_id] = verify.VerifiedTerminal(
                        terminal_sha256="",
                        event_tail_sha256=projection.event_tail_sha256 or "",
                        status=cast(verify.TerminalStatus, projection.terminal_status),
                    )
                continue
            terminal_bytes = writer.read_bytes(terminal_path, max_bytes=64_000)
            terminal = schema.decode(terminal_bytes)
            manifest_sha, allocation_bytes, object_count = runtime._epoch_allocation_manifest(
                writer, epoch_id
            )
            terminal_fields = {
                "schema",
                "schema_version",
                "epoch_id",
                "plan_sha256",
                "event_tail_sha256",
                "status",
                "reason",
                "sealed_ns",
                "pause_reason",
                "allocation_manifest_sha256",
                "allocation_upper_bytes",
                "allocation_object_count",
            }
            if (
                not isinstance(terminal, dict)
                or set(terminal) != terminal_fields
                or schema.canonical(terminal) != terminal_bytes
                or terminal.get("schema") != schema.TERMINAL_SCHEMA
                or terminal.get("schema_version") != 4
                or terminal.get("epoch_id") != epoch_id
                or terminal.get("plan_sha256") != schema.sha(plan_bytes)
                or terminal.get("event_tail_sha256") != projection.event_tail_sha256
                or terminal.get("status") != projection.terminal_status
                or type(terminal.get("sealed_ns")) is not int
                or terminal.get("allocation_manifest_sha256") != manifest_sha
                or terminal.get("allocation_upper_bytes") != allocation_bytes
                or terminal.get("allocation_object_count") != object_count
                or (
                    not sources_match
                    and terminal.get("reason") != "source_binding_changed_before_dispatch"
                )
            ):
                raise CutoverError("epoch_terminal_receipt_invalid")
            plans[epoch_id] = plan_bytes
            projections[epoch_id] = projection
            terminals[epoch_id] = verify.VerifiedTerminal(
                terminal_sha256=schema.sha(terminal_bytes),
                event_tail_sha256=projection.event_tail_sha256 or "",
                status=cast(verify.TerminalStatus, terminal["status"]),
                pause_reason=terminal["pause_reason"],
            )
        except (schema.SchemaError, verify.VerifyError, OSError) as exc:
            raise CutoverError("service_epoch_replay_failed") from exc

    if expected_epoch_id not in plans:
        raise CutoverError("cutover_epoch_missing_from_service")
    control_path = f"{schema.SERVICE_RELATIVE_ROOT}/{schema.CONTROL_FILE}"
    control_bytes = writer.read_bytes(control_path, max_bytes=8_000_000)
    if control_bytes and not control_bytes.endswith(b"\n"):
        raise CutoverError("control_journal_torn")

    def validate_control_plan(raw: bytes, epoch_id: str) -> dict[str, Any]:
        pinned = plans.get(epoch_id)
        if pinned is None or pinned != raw:
            raise CutoverError("control_plan_binding_invalid")
        document = schema.decode(raw)
        if not isinstance(document, dict) or not isinstance(document.get("source_bindings"), dict):
            raise CutoverError("control_plan_sources_invalid")
        return verify.verify_plan(
            raw,
            expected_sources=document["source_bindings"],
            expected_root_id=root_id,
            expected_volume_uuid=volume_uuid,
        )

    try:
        state = verify.verify_control_chain(
            control_bytes,
            plans=plans,
            terminals=terminals,
            budget_evidence=runtime._budget_evidence_map(writer, plans),
            plan_validator=validate_control_plan,
        )
    except Exception as exc:
        if allow_expected_active:
            exception = _verify_cutover_only_control_prefix(
                writer,
                control_bytes=control_bytes,
                control_rows=list(
                    schema.verify_event_chain(control_bytes, schema=schema.CONTROL_SCHEMA)
                ),
                plans=plans,
                terminals=terminals,
                projections=projections,
                expected_epoch_id=expected_epoch_id,
                root_id=root_id,
                volume_uuid=volume_uuid,
            )
            return (
                verify.ControlProjection(
                    active_epoch_id=expected_epoch_id
                    if expected_epoch_id not in terminals
                    else None,
                    budget_paused=False,
                    pause_reason=None,
                    cooldown_until_ns=0,
                    epoch_ids=tuple(plans),
                    event_tail_sha256=exception["control_tail_sha256"],
                    pending_terminal_epoch_id=None,
                ),
                exception,
            )
        raise CutoverError("service_control_replay_failed") from exc
    active_invalid = (
        state.active_epoch_id not in {None, expected_epoch_id}
        if allow_expected_active
        else (state.active_epoch_id is not None)
    )
    pending_invalid = (
        state.pending_terminal_epoch_id not in {None, expected_epoch_id}
        if allow_expected_active
        else state.pending_terminal_epoch_id is not None
    )
    if active_invalid or pending_invalid:
        raise CutoverError("service_control_still_active")
    if not allow_expected_active and projections[expected_epoch_id].terminal_status != "abandoned":
        raise CutoverError("cutover_epoch_not_abandoned")
    return state, None


def _verify_cutover_only_control_prefix(
    writer: DescriptorWriter,
    *,
    control_bytes: bytes,
    control_rows: list[dict[str, Any]],
    plans: Mapping[str, bytes],
    terminals: Mapping[str, verify.VerifiedTerminal],
    projections: Mapping[str, verify.EpochProjection],
    expected_epoch_id: str,
    root_id: str,
    volume_uuid: str,
) -> dict[str, Any]:
    """Attest one known invalid open suffix for cutover only; never authorize v4."""
    target_indices = [
        index
        for index, row in enumerate(control_rows)
        if row["fields"].get("epoch_id") == expected_epoch_id
    ]
    target_rows = [control_rows[index] for index in target_indices]
    expected_kinds = (
        ["epoch_open", "epoch_abandon"] if expected_epoch_id in terminals else ["epoch_open"]
    )
    if (
        not target_indices
        or target_indices != list(range(target_indices[0], len(control_rows)))
        or [row["kind"] for row in target_rows] != expected_kinds
        or len(target_rows) not in {1, 2}
    ):
        raise CutoverError("forensic_suffix_not_single_target")

    open_row = target_rows[0]
    plan_bytes = plans.get(expected_epoch_id)
    projection = projections.get(expected_epoch_id)
    if plan_bytes is None or projection is None:
        raise CutoverError("forensic_target_missing")
    plan = schema.decode(plan_bytes)
    if (
        not isinstance(plan, dict)
        or open_row["fields"]
        != {
            "epoch_id": expected_epoch_id,
            "plan_sha256": schema.sha(plan_bytes),
            "anchor_ns": plan.get("anchor_ns"),
        }
        or plan.get("anchor_ns") <= open_row["at_ns"]
        or (projection.terminal_status is None) != (expected_epoch_id not in terminals)
        or projection.request_states
        or projection.active_bucket is not None
    ):
        raise CutoverError("forensic_target_binding_invalid")

    prefix_index = target_indices[0]
    prefix_rows = control_rows[:prefix_index]
    prefix_bytes = b"".join(control_bytes.splitlines(keepends=True)[:prefix_index])
    prior_abandons = [row for row in prefix_rows if row["kind"] == "epoch_abandon"]
    recent_abandons = [
        row
        for row in prior_abandons
        if open_row["at_ns"] - policy.DAY_NS < row["at_ns"] <= open_row["at_ns"]
    ]
    if len(recent_abandons) != 2:
        raise CutoverError("forensic_abandon_count_mismatch")
    prefix_epoch_ids = {
        row["fields"].get("epoch_id") for row in prefix_rows if row["kind"] == "epoch_open"
    }
    prefix_plans = {epoch: plans[epoch] for epoch in prefix_epoch_ids if epoch in plans}
    prefix_terminals = {epoch: terminals[epoch] for epoch in prefix_epoch_ids if epoch in terminals}
    if set(prefix_plans) != prefix_epoch_ids or set(prefix_terminals) != prefix_epoch_ids:
        raise CutoverError("forensic_prefix_epoch_closure_invalid")

    def validate_prefix_plan(raw: bytes, epoch_id: str) -> dict[str, Any]:
        if prefix_plans.get(epoch_id) != raw:
            raise CutoverError("forensic_prefix_plan_binding_invalid")
        document = schema.decode(raw)
        if not isinstance(document, dict) or not isinstance(document.get("source_bindings"), dict):
            raise CutoverError("forensic_prefix_sources_invalid")
        return verify.verify_plan(
            raw,
            expected_sources=document["source_bindings"],
            expected_root_id=root_id,
            expected_volume_uuid=volume_uuid,
        )

    try:
        prefix_state = verify.verify_control_chain(
            prefix_bytes,
            plans=prefix_plans,
            terminals=prefix_terminals,
            budget_evidence=runtime._budget_evidence_map(writer, prefix_plans),
            plan_validator=validate_prefix_plan,
        )
    except Exception as exc:
        raise CutoverError("forensic_valid_prefix_replay_failed") from exc
    if (
        prefix_state.active_epoch_id is not None
        or prefix_state.pending_terminal_epoch_id is not None
    ):
        raise CutoverError("forensic_prefix_not_closed")
    if plan["anchor_ns"] <= max(open_row["at_ns"], prefix_state.cooldown_until_ns):
        raise CutoverError("forensic_anchor_not_future")

    if expected_epoch_id in terminals:
        close = target_rows[1]
        terminal = terminals[expected_epoch_id]
        expected_fields = {
            "epoch_id": expected_epoch_id,
            "terminal_sha256": terminal.terminal_sha256,
            "event_tail_sha256": projection.event_tail_sha256,
        }
        fields = close["fields"]
        if (
            close["kind"] != "epoch_abandon"
            or set(fields) != set(expected_fields) | {"cooldown_until_ns"}
            or any(fields.get(key) != value for key, value in expected_fields.items())
            or type(fields.get("cooldown_until_ns")) is not int
            or fields["cooldown_until_ns"] < close["at_ns"] + policy.HOUR_NS
            or terminal.status != "abandoned"
        ):
            raise CutoverError("forensic_target_close_invalid")

    epoch_usage: list[dict[str, Any]] = []
    for epoch_id in sorted(plans):
        event_bytes = writer.read_bytes(
            f"{_epoch_relative(epoch_id)}/{schema.EVENT_FILE}", max_bytes=8_000_000
        )
        event_rows = schema.verify_event_chain(event_bytes, schema=schema.EVENT_SCHEMA)
        manifest_sha, allocation_bytes, object_count = runtime._epoch_allocation_manifest(
            writer, epoch_id
        )
        epoch_usage.append(
            {
                "epoch_id": epoch_id,
                "plan_sha256": schema.sha(plans[epoch_id]),
                "source_bindings_sha256": schema.sha(
                    schema.canonical(schema.decode(plans[epoch_id])["source_bindings"])
                ),
                "event_tail_sha256": projections[epoch_id].event_tail_sha256,
                "terminal_sha256": terminals[epoch_id].terminal_sha256
                if epoch_id in terminals
                else None,
                "request_intents": sum(row["kind"] == "request_intent" for row in event_rows),
                "allocation_manifest_sha256": manifest_sha,
                "allocation_upper_bytes": allocation_bytes,
                "allocation_object_count": object_count,
            }
        )
    stop_bytes = writer.read_bytes(STOP_FILE, max_bytes=16_000)
    return {
        "schema": EXCEPTION_SCHEMA,
        "schema_version": 1,
        "classification": "invalid_open_after_two_abandons",
        "v4_policy_violation": True,
        "cutover_only": True,
        "activation_authority": "none",
        "target_epoch_id": expected_epoch_id,
        "target_plan_sha256": schema.sha(plan_bytes),
        "target_open_event_sha256": open_row["event_sha256"],
        "target_open_event_index": target_indices[0],
        "target_open_at_ns": open_row["at_ns"],
        "valid_prefix_tail_sha256": prefix_state.event_tail_sha256,
        "recent_abandon_event_sha256": [row["event_sha256"] for row in recent_abandons],
        "target_event_tail_sha256": projection.event_tail_sha256,
        "target_terminal_sha256": terminals[expected_epoch_id].terminal_sha256
        if expected_epoch_id in terminals
        else None,
        "target_request_intents": 0,
        "target_captures": 0,
        "target_outcomes": 0,
        "stop_sha256": schema.sha(stop_bytes),
        "root_id": root_id,
        "volume_uuid": volume_uuid.lower(),
        "epochs": epoch_usage,
        "control_tail_sha256": control_rows[-1]["event_sha256"],
    }


def _reconcile_locked(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    expected_plan_sha256: str,
    root_id: str,
    volume_uuid: str,
    now_ns: int,
) -> dict[str, Any]:
    stop = _validate_stop(writer, epoch_id=epoch_id, plan_sha256=expected_plan_sha256)
    if stop is None:
        raise CutoverError("operator_stop_missing")
    plan_bytes, plan = _load_plan(
        writer,
        epoch_id=epoch_id,
        expected_plan_sha256=expected_plan_sha256,
        root_id=root_id,
        volume_uuid=volume_uuid,
    )
    _assert_no_dispatch_artifacts(writer, epoch_id)
    events = _event_rows(writer, epoch_id)
    kinds = [row["kind"] for row in events]
    if kinds not in (["epoch_started"], ["epoch_started", "epoch_terminal"]):
        raise CutoverError("epoch_not_unstarted")
    started = events[0]
    if (
        started["fields"].get("epoch_id") != epoch_id
        or started["fields"].get("plan_sha256") != schema.sha(plan_bytes)
        or started["fields"].get("anchor_ns") != plan["anchor_ns"]
    ):
        raise CutoverError("epoch_start_binding_invalid")
    try:
        projection = verify.verify_epoch_events(
            plan, writer.read_bytes(f"{_epoch_relative(epoch_id)}/{schema.EVENT_FILE}")
        )
    except Exception as exc:
        raise CutoverError("epoch_projection_invalid") from exc
    if projection.request_states or projection.active_bucket is not None:
        raise CutoverError("epoch_dispatch_state_present")
    _ensure_target_open(
        writer,
        epoch_id=epoch_id,
        plan=plan,
        plan_bytes=plan_bytes,
        now_ns=now_ns,
    )
    _, initial_exception = _verify_all_epochs_terminal(
        writer,
        root_id=root_id,
        volume_uuid=volume_uuid,
        expected_epoch_id=epoch_id,
        allow_expected_active=True,
    )
    events = _event_rows(writer, epoch_id)
    projection = verify.verify_epoch_events(
        plan, writer.read_bytes(f"{_epoch_relative(epoch_id)}/{schema.EVENT_FILE}")
    )
    terminal_path = f"{_epoch_relative(epoch_id)}/{schema.TERMINAL_FILE}"
    terminal_exists = writer.exists(terminal_path)
    if projection.terminal_status is None and terminal_exists:
        raise CutoverError("terminal_without_terminal_event")
    if not terminal_exists:
        runtime._finish_epoch(
            writer,
            epoch_id=epoch_id,
            plan_bytes=plan_bytes,
            status="abandoned",
            reason="operator_cutover_before_dispatch",
            pause_reason=None,
            now_ns=max(now_ns, events[-1]["at_ns"]),
        )
        events = _event_rows(writer, epoch_id)
        projection = verify.verify_epoch_events(
            plan, writer.read_bytes(f"{_epoch_relative(epoch_id)}/{schema.EVENT_FILE}")
        )
    if projection.terminal_status != "abandoned" or not projection.event_tail_sha256:
        raise CutoverError("epoch_terminal_state_invalid")
    terminal, terminal_bytes = _terminal_file(
        writer,
        epoch_id=epoch_id,
        plan_bytes=plan_bytes,
        expected_event_tail=projection.event_tail_sha256,
    )
    _ensure_abandon_control(
        writer,
        epoch_id=epoch_id,
        terminal=terminal,
        terminal_bytes=terminal_bytes,
        event_tail=projection.event_tail_sha256,
        now_ns=now_ns,
    )
    final_events = _event_rows(writer, epoch_id)
    final_projection = verify.verify_epoch_events(
        plan, writer.read_bytes(f"{_epoch_relative(epoch_id)}/{schema.EVENT_FILE}")
    )
    if (
        [row["kind"] for row in final_events] != ["epoch_started", "epoch_terminal"]
        or final_projection.request_states
        or final_projection.active_bucket is not None
        or final_projection.terminal_status != "abandoned"
    ):
        raise CutoverError("epoch_dispatch_count_changed")
    target_rows = _target_control_events(writer, epoch_id)
    if [row["kind"] for row in target_rows] != ["epoch_open", "epoch_abandon"]:
        raise CutoverError("control_cutover_not_terminal")
    _, final_exception = _verify_all_epochs_terminal(
        writer,
        root_id=root_id,
        volume_uuid=volume_uuid,
        expected_epoch_id=epoch_id,
        allow_expected_active=True,
    )
    if (initial_exception is None) != (final_exception is None):
        raise CutoverError("forensic_exception_state_changed")
    exception_sha256: str | None = None
    if final_exception is not None:
        exception_bytes = schema.canonical(final_exception)
        if writer.exists(EXCEPTION_PATH):
            if writer.read_bytes(EXCEPTION_PATH, max_bytes=1_000_000) != exception_bytes:
                raise CutoverError("forensic_exception_receipt_mismatch")
        else:
            writer.mkdir("services/external-rolling-v5")
            writer.create(EXCEPTION_PATH, exception_bytes)
        exception_sha256 = schema.sha(exception_bytes)
    return {
        "status": (
            "abandoned_before_dispatch_v4_policy_violation"
            if final_exception is not None
            else "abandoned_before_dispatch"
        ),
        "epoch_id": epoch_id,
        "plan_sha256": schema.sha(plan_bytes),
        "event_tail_sha256": final_projection.event_tail_sha256,
        "terminal_sha256": schema.sha(terminal_bytes),
        "request_intents": 0,
        "captures": 0,
        "outcomes": 0,
        "v4_policy_violation": final_exception is not None,
        "cutover_exception_sha256": exception_sha256,
    }


def cutover_pending_epoch(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    expected_plan_sha256: str,
    root_id: str,
    volume_uuid: str,
    agent: AgentControl,
    now_ns: Callable[[], int] = time.time_ns,
) -> dict[str, Any]:
    """Publish STOP, disable/unload v4, and terminalize one no-intent epoch.

    The writer root must be the configured data root, created from a held
    StorageGuard descriptor. Every service mutation is protected by the
    pre-existing root-level v4 lock and the writer's live guard callback.
    """
    with writer.lock(schema.LOCK_FILE):
        plan_bytes, plan = _load_plan(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=expected_plan_sha256,
            root_id=root_id,
            volume_uuid=volume_uuid,
        )
        existing_stop = _validate_stop(
            writer, epoch_id=epoch_id, plan_sha256=schema.sha(plan_bytes)
        )
        if existing_stop is None:
            created_ns = now_ns()
            marker = {
                "schema": STOP_SCHEMA,
                "schema_version": 1,
                "service_id": schema.SERVICE_ID,
                "epoch_id": epoch_id,
                "plan_sha256": schema.sha(plan_bytes),
                "reason": "operator_cutover_before_dispatch",
                "created_ns": created_ns,
            }
            writer.create(STOP_FILE, schema.canonical(marker))
    # STOP is durable before any launchctl action. A racing poll acquires the
    # same writer lock and exits before constructing the provider client.
    agent.disable_bootout_and_verify()
    with writer.lock(schema.LOCK_FILE):
        return _reconcile_locked(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=expected_plan_sha256,
            root_id=root_id,
            volume_uuid=volume_uuid,
            now_ns=now_ns(),
        )


@contextlib.contextmanager
def bound_writer(config_path: Path) -> Iterator[tuple[Any, DescriptorWriter]]:
    """Open the existing v4 data root without creating or renaming anything."""
    with launch.host_lock():
        config = StorageConfig.load(config_path)
        with writer_guard(config_path, min_free_bytes=_DISPATCH_FLOOR) as guard:
            if (
                guard.root_id != config.root_id
                or guard.volume_uuid.lower() != config.volume_uuid.lower()
            ):
                raise CutoverError("storage_binding_changed")
            root_fd = _open_relative_directory(guard.mount_fd, config.data_root)
            try:

                def revalidate() -> None:
                    guard.revalidate(min_free_bytes=_DISPATCH_FLOOR)

                with DescriptorWriter(root_fd, before_write=revalidate) as writer:
                    yield guard, writer
            finally:
                os.close(root_fd)


def run_bound_cutover(
    config_path: Path,
    *,
    epoch_id: str,
    expected_plan_sha256: str,
    agent: AgentControl | None = None,
) -> dict[str, Any]:
    """Production-shaped entry point; callers must explicitly invoke it."""
    with bound_writer(config_path) as (guard, writer):
        return cutover_pending_epoch(
            writer,
            epoch_id=epoch_id,
            expected_plan_sha256=expected_plan_sha256,
            root_id=guard.root_id,
            volume_uuid=guard.volume_uuid,
            agent=agent or LaunchctlAgentControl(),
        )


def main(argv: list[str] | None = None) -> int:
    """Explicit path-only CLI pinned to the owner storage binding."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--plan-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_bound_cutover(
            DEFAULT_STORAGE_CONFIG,
            epoch_id=args.epoch_id,
            expected_plan_sha256=args.plan_sha256,
        )
    except (CutoverError, StorageError, OSError) as exc:
        # Errors are finite/sanitized by these boundaries; never print an
        # endpoint, exception text, request body, or environment.
        print(f"cutover failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(
        "cutover complete: "
        f"epoch={result['epoch_id']} requests={result['request_intents']} "
        f"terminal_sha256={result['terminal_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
