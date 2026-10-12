#!/usr/bin/env python3
"""Metadata-only conservative Helius usage carry-forward for v5.

This verifier consumes one immutable operator-prepared receipt. It never scans a
v4 capture/body archive and never treats modeled credits as provider charges.
"""

from __future__ import annotations

import stat
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from tools import brain_senses_external_cutover_v4_to_v5 as cutover_forensics
from tools import brain_senses_external_orchestrator_v4 as v4_runtime
from tools import brain_senses_external_rolling_policy_v1 as v4_policy
from tools import brain_senses_external_rolling_policy_v5 as policy
from tools import brain_senses_external_schema_v4 as v4_schema
from tools import brain_senses_external_schema_v5 as schema
from tools import brain_senses_external_verify_v4 as v4_verify
from tools.brain_senses_external_writer_v1 import (
    DescriptorWriter,
    semantic_request_sha256,
    wire_request_sha256,
)

RECEIPT_PATH = "services/external-rolling-v5/cutover-usage-v1.json"
RECEIPT_SCHEMA = "brain-senses-external-rolling-v5-cutover-usage"
RECEIPT_VERSION = 2
LEGACY_COUNTS = {"c1": 0, "c2": 1, "c3": 205}
LEGACY_SOURCE_BINDINGS = {
    "c1": {
        "evidence_path": "docs/evidence/helius-inventory-v3-preanchor-recovery-2026-10-10.json",
        "evidence_sha256": "d5da27cdf1909065bdbfc5a5cd676141170f742dd6e11cfefbbe120f30d23c2f",
    },
    "c2": {
        "evidence_path": "docs/evidence/helius-inventory-v3-campaign2-missed-gap-2026-10-10.json",
        "evidence_sha256": "76d2733335c1a22e870c8dd40a30c3dfa1bf5965558a54624bb9e0efdc07f7b6",
    },
    "c3": {
        "evidence_path": "docs/evidence/helius-inventory-v3-c3-abnormal-stop-2026-10-10.json",
        "evidence_sha256": "6954c9680181e33ed65cf4d0bfe6fd2929fe3a375a138e627d06a67bca000d32",
    },
}
LEGACY_CAMPAIGN_START_NS = 1_791_590_400_000_000_000  # 2026-10-10T00:00:00Z
LEGACY_CAMPAIGN_END_NS = 1_791_676_800_000_000_000  # 2026-10-11T00:00:00Z
HISTORICAL_C4 = {
    "request_count": 375,
    "dispatch_start_ns": 1_791_633_600_000_000_000,  # 2026-10-10T12:00:00Z
    # Latest terminal STOP: 2026-10-11T00:05:00.418875Z.
    "dispatch_end_ns": 1_791_677_100_418_875_000,
    "response_bytes_upper": 4_000_000_000,
    "physical_bytes_upper": 4_000_000_000,
    "evidence_sha256": "8b97dd4936ddcd6e8494d35e06cb2a86d01929589e4c4c4c5ff7ba91825cbe65",
    "evidence_path": "docs/evidence/helius-inventory-v3-c4-abnormal-terminal-2026-10-11.json",
    "attribution": "possible_same_account",
}


class CutoverUsageError(ValueError):
    """Invalid, incomplete, or unpinned cutover accounting evidence."""


def build_from_v4_metadata(
    reader: DescriptorWriter,
    *,
    root_id: str,
    volume_uuid: str,
    generated_ns: int,
    prior_campaigns: Mapping[str, Mapping[str, Any]],
) -> bytes:
    """Build a cutover receipt from terminal v4 metadata without opening response bodies.

    The caller must hold the configured storage guard and existing v4 service lock,
    and pass a reader rooted at that verified data root. This function is read-only.
    ``prior_campaigns`` supplies already reviewed c1-c3 metadata. Historical
    inventory-v3 c4 is a separately pinned metadata-only terminal; current rolling-v4
    usage is derived into its own source and intent rows. The result is bytes only and
    is never published.
    """
    if type(generated_ns) is not int or generated_ns <= 0:
        raise CutoverUsageError("generated_ns_invalid")
    expected_sources = v4_runtime.source_bindings()
    service = v4_schema.SERVICE_RELATIVE_ROOT
    try:
        inventory = v4_runtime._descriptor_inventory(reader, service)
        v4_runtime._verify_service_layout(inventory)
        cap_path = f"{service}/{v4_runtime.ALLOCATION_CAP_FILE.rsplit('/', 1)[-1]}"
        allocation_cap_bytes = reader.read_bytes(cap_path, max_bytes=64_000)
        allocation_cap = v4_schema.decode(allocation_cap_bytes)
        if (
            not isinstance(allocation_cap, dict)
            or set(allocation_cap)
            != {
                "schema",
                "schema_version",
                "created_ns",
                "root_id",
                "volume_uuid",
                "available_at_activation_bytes",
                "service_allocation_cap_bytes",
            }
            or v4_schema.canonical(allocation_cap) != allocation_cap_bytes
            or allocation_cap.get("schema") != "brain-senses-external-rolling-v4-allocation-cap"
            or allocation_cap.get("schema_version") != 4
            or allocation_cap.get("root_id") != root_id
            or allocation_cap.get("volume_uuid") != volume_uuid.lower()
            or type(allocation_cap.get("created_ns")) is not int
            or allocation_cap["created_ns"] <= 0
            or allocation_cap["created_ns"] > generated_ns
            or type(allocation_cap.get("available_at_activation_bytes")) is not int
            or allocation_cap["available_at_activation_bytes"] <= 0
            or type(allocation_cap.get("service_allocation_cap_bytes")) is not int
            or allocation_cap["service_allocation_cap_bytes"]
            != v4_policy.activation_allocation_cap(allocation_cap["available_at_activation_bytes"])
        ):
            raise CutoverUsageError("v4_allocation_cap_binding_invalid")
        allocation_rows: list[dict[str, Any]] = [
            {"service_allocation_cap_sha256": v4_schema.sha(allocation_cap_bytes)}
        ]
        epochs_root = f"{service}/epochs"
        epoch_ids = sorted(reader.list_names(epochs_root))
        if not epoch_ids:
            raise CutoverUsageError("terminal_v4_epoch_missing")
        plans: dict[str, bytes] = {}
        terminals: dict[str, v4_verify.VerifiedTerminal] = {}
        projections: dict[str, v4_verify.EpochProjection] = {}
        epoch_rows: dict[str, tuple[bytes, bytes, dict[str, Any], dict[str, Any]]] = {}
        all_intents: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any] | None]] = []
        for epoch_id in epoch_ids:
            v4_schema.validate_epoch_id(epoch_id)
            relative = f"{epochs_root}/{epoch_id}"
            plan_bytes = reader.read_bytes(f"{relative}/{v4_schema.PLAN_FILE}", max_bytes=2_000_000)
            plan_value = v4_schema.decode(plan_bytes)
            planned_sources = (
                plan_value.get("source_bindings") if isinstance(plan_value, dict) else None
            )
            sources_match = planned_sources == expected_sources
            runtime_only_change = (
                isinstance(planned_sources, dict)
                and set(planned_sources) == set(expected_sources)
                and {
                    path
                    for path in expected_sources
                    if planned_sources.get(path) != expected_sources[path]
                }
                == {"tools/brain_senses_external_orchestrator_v4.py"}
            )
            if not sources_match and not runtime_only_change:
                raise CutoverUsageError("v4_source_binding_changed")
            planned_source_bindings = cast(Mapping[str, str], planned_sources)
            plan = v4_verify.verify_plan(
                plan_bytes,
                expected_sources=expected_sources if sources_match else planned_source_bindings,
                expected_root_id=root_id,
                expected_volume_uuid=volume_uuid,
            )
            event_bytes = reader.read_bytes(
                f"{relative}/{v4_schema.EVENT_FILE}", max_bytes=64_000_000
            )
            event_rows = v4_schema.verify_event_chain(event_bytes, schema=v4_schema.EVENT_SCHEMA)
            projection = v4_verify.verify_epoch_events(plan, event_bytes)
            if projection.terminal_status is None or projection.event_tail_sha256 is None:
                raise CutoverUsageError("v4_epoch_not_terminal")
            terminal_bytes = reader.read_bytes(
                f"{relative}/{v4_schema.TERMINAL_FILE}", max_bytes=64_000
            )
            terminal = v4_schema.decode(terminal_bytes)
            manifest_sha, allocated_bytes, object_count = v4_runtime._epoch_allocation_manifest(
                reader, epoch_id
            )
            required_terminal = {
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
                or set(terminal) != required_terminal
                or v4_schema.canonical(terminal) != terminal_bytes
                or terminal.get("schema") != v4_schema.TERMINAL_SCHEMA
                or terminal.get("schema_version") != 4
                or terminal.get("epoch_id") != epoch_id
                or terminal.get("plan_sha256") != v4_schema.sha(plan_bytes)
                or terminal.get("event_tail_sha256") != projection.event_tail_sha256
                or terminal.get("status") != projection.terminal_status
                or terminal.get("allocation_manifest_sha256") != manifest_sha
                or terminal.get("allocation_upper_bytes") != allocated_bytes
                or terminal.get("allocation_object_count") != object_count
                or type(terminal.get("sealed_ns")) is not int
                or terminal["sealed_ns"] > generated_ns
                or (
                    not sources_match
                    and terminal.get("reason") != "source_binding_changed_before_dispatch"
                )
            ):
                raise CutoverUsageError("v4_terminal_binding_invalid")
            terminals[epoch_id] = v4_verify.VerifiedTerminal(
                terminal_sha256=v4_schema.sha(terminal_bytes),
                event_tail_sha256=projection.event_tail_sha256,
                status=terminal["status"],
                pause_reason=terminal.get("pause_reason"),
            )
            plans[epoch_id] = plan_bytes
            projections[epoch_id] = projection
            epoch_rows[epoch_id] = (plan_bytes, event_bytes, plan, terminal)
            allocation_rows.append(
                {
                    "epoch_id": epoch_id,
                    "manifest_sha256": manifest_sha,
                    "allocation_upper_bytes": allocated_bytes,
                    "allocation_object_count": object_count,
                }
            )
            outcomes_by_request = {
                event["fields"].get("request_id"): event
                for event in event_rows
                if event["kind"] in {"request_captured", "request_failed"}
            }
            for event in event_rows:
                if event["kind"] != "request_intent":
                    continue
                fields = event["fields"]
                request_id = fields.get("request_id")
                if not isinstance(request_id, str):
                    raise CutoverUsageError("v4_request_identity_invalid")
                outcome_event = outcomes_by_request.get(request_id)
                all_intents.append((epoch_id, event, outcome_event or {}, terminal))

        control_bytes = reader.read_bytes(
            f"{service}/{v4_schema.CONTROL_FILE}", max_bytes=64_000_000
        )
        control_events = v4_schema.verify_event_chain(
            control_bytes, schema=v4_schema.CONTROL_SCHEMA
        )
        if any(event["kind"] in {"budget_pause", "budget_resume"} for event in control_events):
            raise CutoverUsageError("v4_budget_control_requires_separate_review")
        control_rows = list(control_events)

        def validate_control_plan(raw: bytes, plan_epoch_id: str) -> dict[str, Any]:
            if plans.get(plan_epoch_id) != raw:
                raise CutoverUsageError("control_plan_binding_invalid")
            document = v4_schema.decode(raw)
            if not isinstance(document, dict) or not isinstance(
                document.get("source_bindings"), dict
            ):
                raise CutoverUsageError("control_plan_source_invalid")
            planned = document["source_bindings"]
            if planned != expected_sources and not (
                set(planned) == set(expected_sources)
                and {
                    path for path in expected_sources if planned.get(path) != expected_sources[path]
                }
                == {"tools/brain_senses_external_orchestrator_v4.py"}
            ):
                raise CutoverUsageError("control_plan_source_changed")
            return v4_verify.verify_plan(
                raw,
                expected_sources=planned,
                expected_root_id=root_id,
                expected_volume_uuid=volume_uuid,
            )

        cutover_exception: dict[str, Any] | None = None
        try:
            control_state = v4_verify.verify_control_chain(
                control_bytes,
                plans=plans,
                terminals=terminals,
                budget_evidence={},
                plan_validator=validate_control_plan,
            )
        except v4_verify.VerifyError:
            cutover_exception = cutover_forensics._verify_cutover_only_control_prefix(
                reader,
                control_bytes=control_bytes,
                control_rows=control_rows,
                plans=plans,
                terminals=terminals,
                projections=projections,
                expected_epoch_id=epoch_ids[-1],
                root_id=root_id,
                volume_uuid=volume_uuid,
            )
            control_state = None
        if control_state is not None and (
            control_state.active_epoch_id is not None
            or control_state.pending_terminal_epoch_id is not None
        ):
            raise CutoverUsageError("v4_service_not_closed")
        if cutover_exception is not None and (
            cutover_exception.get("v4_policy_violation") is not True
            or cutover_exception.get("cutover_only") is not True
            or cutover_exception.get("activation_authority") != "none"
        ):
            raise CutoverUsageError("v4_cutover_exception_invalid")
        exception_path = f"{schema.SERVICE_RELATIVE_ROOT}/{schema.CUTOVER_EXCEPTION_FILE}"
        if cutover_exception is None:
            try:
                reader.read_bytes(exception_path, max_bytes=1_000_000)
            except FileNotFoundError:
                pass
            else:
                raise CutoverUsageError("unexpected_v4_cutover_exception_receipt")
            cutover_exception_sha256 = None
        else:
            exception_raw = reader.read_bytes(exception_path, max_bytes=1_000_000)
            if exception_raw != v4_schema.canonical(cutover_exception):
                raise CutoverUsageError("v4_cutover_exception_receipt_mismatch")
            cutover_exception_sha256 = v4_schema.sha(exception_raw)
        control_tail = control_events[-1]["event_sha256"] if control_events else None
        if control_tail is None:
            raise CutoverUsageError("v4_control_tail_missing")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        if isinstance(exc, CutoverUsageError):
            raise
        raise CutoverUsageError("v4_metadata_verification_failed") from exc

    prior = {name: dict(record) for name, record in prior_campaigns.items()}
    if set(prior) != {"c1", "c2", "c3"}:
        raise CutoverUsageError("prior_campaign_metadata_invalid")
    legacy_campaigns: dict[str, dict[str, Any]] = {}
    for name in LEGACY_COUNTS:
        supplied = prior[name]
        expected = LEGACY_SOURCE_BINDINGS[name]
        count = LEGACY_COUNTS[name]
        if (
            supplied.get("request_count") != count
            or supplied.get("evidence_path") != expected["evidence_path"]
            or supplied.get("evidence_sha256") != expected["evidence_sha256"]
            or supplied.get("dispatch_start_ns") != LEGACY_CAMPAIGN_START_NS
            or supplied.get("dispatch_end_ns") != LEGACY_CAMPAIGN_END_NS
            or supplied.get("response_bytes_upper") != (0 if count == 0 else 4_000_000_000)
            or supplied.get("physical_bytes_upper") != (0 if count == 0 else 4_000_000_000)
            or supplied.get("attribution") != "possible_same_account"
        ):
            raise CutoverUsageError("prior_campaign_source_pin_or_envelope_mismatch")
        legacy_campaigns[name] = dict(supplied)
    legacy_campaigns["c4"] = dict(HISTORICAL_C4)
    request_rows: list[dict[str, Any]] = []
    min_dispatch: int | None = None
    max_dispatch: int | None = None
    response_debit = physical_debit = 0
    for epoch_id, intent_event, outcome_event, _terminal in all_intents:
        intent = intent_event["fields"]
        outcome = outcome_event.get("kind")
        request_id = intent["request_id"]
        decision_ns = intent.get("decision_ns")
        if type(decision_ns) is not int or decision_ns <= 0 or decision_ns > generated_ns:
            raise CutoverUsageError("v4_decision_time_invalid")
        dispatch_ns: int | None
        latest_possible_dispatch_ns: int
        if outcome == "request_captured":
            capture = f"{service}/epochs/{epoch_id}/captures/{request_id}"
            try:
                receipt_bytes = reader.read_bytes(f"{capture}/receipt.json", max_bytes=64_000)
                receipt = v4_schema.decode(receipt_bytes)
                response_stat = reader.stat(f"{capture}/response.json")
                receipt_stat = reader.stat(f"{capture}/receipt.json")
                event_fields = outcome_event["fields"]
                expected_fields = {
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
                    or set(receipt) != expected_fields
                    or v4_schema.canonical(receipt) != receipt_bytes
                    or receipt.get("schema") != v4_schema.RECEIPT_SCHEMA
                    or receipt.get("schema_version") != 1
                    or receipt.get("provider") != "helius-free"
                    or receipt.get("request_id") != request_id
                    or receipt.get("request_sha256")
                    != semantic_request_sha256(intent["method"], intent["params"])
                    or receipt.get("wire_request_sha256")
                    != wire_request_sha256(intent["method"], intent["params"])
                    or receipt.get("decision_ns") != decision_ns
                    or receipt.get("response_sha256") != event_fields.get("response_sha256")
                    or receipt.get("response_bytes") != event_fields.get("response_bytes")
                    or receipt.get("availability_ns") != event_fields.get("availability_ns")
                    or not stat.S_ISREG(response_stat.st_mode)
                    or not stat.S_ISREG(receipt_stat.st_mode)
                    or receipt_stat.st_size != len(receipt_bytes)
                    or response_stat.st_size != receipt.get("response_bytes")
                    or type(receipt.get("request_started_ns")) is not int
                    or type(receipt.get("received_ns")) is not int
                    or receipt["request_started_ns"] < decision_ns
                    or receipt["received_ns"] < receipt["request_started_ns"]
                    or not isinstance(receipt.get("storage"), dict)
                    or set(receipt["storage"])
                    != {"codec", "path", "sha256", "bytes", "codec_version"}
                    or receipt["storage"].get("codec") != "raw"
                    or receipt["storage"].get("path") != "response.json"
                    or receipt["storage"].get("sha256") != receipt.get("response_sha256")
                    or receipt["storage"].get("bytes") != receipt.get("response_bytes")
                    or receipt["storage"].get("codec_version") is not None
                ):
                    raise CutoverUsageError("v4_capture_metadata_binding_invalid")
                response_bytes = receipt["response_bytes"]
                dispatch_ns = receipt["request_started_ns"]
                latest_possible_dispatch_ns = dispatch_ns
                prefix = f"epochs/{epoch_id}/captures/{request_id}"
                physical = sum(
                    row["allocated_upper_bytes"]
                    for row in inventory
                    if row["path"] == prefix or row["path"].startswith(prefix + "/")
                )
                status, response_charge, physical_charge = "captured", response_bytes, physical
            except (OSError, ValueError, KeyError, TypeError) as exc:
                if isinstance(exc, CutoverUsageError):
                    raise
                raise CutoverUsageError("v4_capture_metadata_unavailable") from exc
        elif outcome == "request_failed":
            status = "possibly_spent"
            dispatch_ns = None
            failed_at = outcome_event.get("at_ns")
            if type(failed_at) is int:
                latest_possible_dispatch_ns = failed_at
            elif _terminal is not None and type(_terminal.get("sealed_ns")) is int:
                latest_possible_dispatch_ns = _terminal["sealed_ns"]
            else:
                raise CutoverUsageError("v4_uncertain_dispatch_time_missing")
            if not decision_ns <= latest_possible_dispatch_ns <= generated_ns:
                raise CutoverUsageError("v4_uncertain_dispatch_time_invalid")
            response_charge = policy.MAX_RESPONSE_BYTES
            physical_charge = policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES
        else:
            raise CutoverUsageError("v4_terminal_request_outcome_missing")
        request_rows.append(
            {
                "campaign_id": "rolling_v4",
                "request_id": request_id,
                "intent_sha256": v4_schema.sha(v4_schema.canonical(intent)),
                "outcome_sha256": outcome_event["event_sha256"],
                "decision_ns": decision_ns,
                "dispatch_ns": dispatch_ns,
                "latest_possible_dispatch_ns": latest_possible_dispatch_ns,
                "operation": intent["method"],
                "outcome": status,
                "request_debit": 1,
                "modeled_credit_debit": policy.CONSERVATIVE_CREDITS_PER_REQUEST,
                "response_bytes_debit": response_charge,
                "physical_bytes_debit": physical_charge,
                "source_event_sha256": intent_event["event_sha256"],
            }
        )
        lower = dispatch_ns if dispatch_ns is not None else latest_possible_dispatch_ns
        min_dispatch = lower if min_dispatch is None else min(min_dispatch, lower)
        max_dispatch = max(max_dispatch or lower, latest_possible_dispatch_ns)
        response_debit += response_charge
        physical_debit += physical_charge

    rolling_v4_exact = len(request_rows) <= v4_policy.MAX_EPOCH_REQUESTS
    if not rolling_v4_exact:
        raise CutoverUsageError("v4_request_count_exceeds_epoch_cap")
    latest_terminal = epoch_rows[sorted(epoch_rows)[-1]][3]
    interval_start = min(plan["created_ns"] for _pb, _eb, plan, _term in epoch_rows.values())
    interval_end = max(term["sealed_ns"] for _pb, _eb, _plan, term in epoch_rows.values())
    source_plan_rows = [
        {"epoch_id": epoch_id, "plan_sha256": v4_schema.sha(plan_bytes)}
        for epoch_id, (plan_bytes, _event_bytes, _plan, _terminal) in sorted(epoch_rows.items())
    ]
    source_terminal_rows = [
        {
            "epoch_id": epoch_id,
            "terminal_sha256": terminals[epoch_id].terminal_sha256,
            "event_tail_sha256": terminals[epoch_id].event_tail_sha256,
        }
        for epoch_id in sorted(epoch_rows)
    ]
    source_allocation_sha = v4_schema.sha(v4_schema.canonical(allocation_rows))
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "schema_version": RECEIPT_VERSION,
        "generated_ns": generated_ns,
        "service": "helius",
        "source_v4": {
            "service_root": service,
            "plan_sha256": v4_schema.sha(v4_schema.canonical(source_plan_rows)),
            "terminal_sha256": v4_schema.sha(v4_schema.canonical(source_terminal_rows)),
            "event_tail_sha256": control_tail,
            "allocation_manifest_sha256": source_allocation_sha,
            "source_bindings": dict(sorted(expected_sources.items())),
            "source_bindings_sha256": v4_schema.sha(
                v4_schema.canonical(dict(sorted(expected_sources.items())))
            ),
            "terminal_status": latest_terminal["status"],
            "verified_interval_start_ns": interval_start,
            "verified_interval_end_ns": interval_end,
        },
        "cutover_exception": {
            "path": exception_path if cutover_exception_sha256 is not None else None,
            "sha256": cutover_exception_sha256,
            "classification": cutover_exception.get("classification")
            if cutover_exception is not None
            else None,
            "v4_policy_violation": cutover_exception is not None,
        },
        "legacy_campaigns": legacy_campaigns,
        "rolling_v4_request_rows": request_rows,
        "rolling_v4_accounting": {
            "exact": rolling_v4_exact,
            "request_count": len(request_rows),
            "dispatch_start_ns": min_dispatch or 0,
            "dispatch_end_ns": max_dispatch or 0,
            "response_bytes_upper": response_debit,
            "physical_bytes_upper": physical_debit,
        },
        "accounting": {
            "historical_c4_exact_count": True,
            "historical_c4_debit_requests": HISTORICAL_C4["request_count"],
            "rolling_v4_exact": rolling_v4_exact,
            "rolling_v4_debit_requests": len(request_rows),
            "legacy_possible_same_account": True,
            "provider_charge_units": None,
            "provider_charge_status": "unknown_unverified",
        },
    }
    raw = schema.canonical(receipt)
    verify_receipt(raw, expected_sha256=schema.sha(raw), expected_v4_sources=expected_sources)
    return raw


def publish_create_only(writer: DescriptorWriter, receipt_bytes: bytes) -> str:
    """Publish already verified receipt bytes beneath the caller's held root fd.

    The caller owns lock ordering and guard lifetime. This helper never opens a
    host path and never replaces an existing receipt.
    """
    expected_sources = v4_runtime.source_bindings()
    try:
        exception_path = f"{schema.SERVICE_RELATIVE_ROOT}/{schema.CUTOVER_EXCEPTION_FILE}"
        exception_sha256 = (
            schema.sha(writer.read_bytes(exception_path, max_bytes=1_000_000))
            if writer.exists(exception_path)
            else None
        )
        verify_receipt(
            receipt_bytes,
            expected_sha256=schema.sha(receipt_bytes),
            expected_v4_sources=expected_sources,
            expected_cutover_exception_sha256=exception_sha256,
        )
        if writer.exists(RECEIPT_PATH):
            existing = writer.read_bytes(RECEIPT_PATH, max_bytes=4_000_000)
            if existing != receipt_bytes:
                raise CutoverUsageError("cutover_receipt_create_only_conflict")
            return "existing_identical"
        writer.create(RECEIPT_PATH, receipt_bytes)
        if writer.read_bytes(RECEIPT_PATH, max_bytes=4_000_000) != receipt_bytes:
            raise CutoverUsageError("cutover_receipt_postwrite_mismatch")
        return "created"
    except CutoverUsageError:
        raise
    except Exception as exc:
        raise CutoverUsageError("cutover_receipt_publish_failed") from exc


@dataclass(frozen=True)
class VerifiedCutoverUsage:
    receipt_sha256: str
    generated_ns: int
    source_plan_sha256: str
    source_terminal_sha256: str
    source_event_tail_sha256: str
    source_allocation_manifest_sha256: str
    source_bindings_sha256: str
    legacy_campaigns: tuple[dict[str, Any], ...]
    request_rows: tuple[dict[str, Any], ...]
    legacy_possible_same_account: bool
    rolling_v4_exact: bool
    rolling_v4_debit_requests: int
    cutover_exception_sha256: str | None
    v4_policy_violation: bool

    def usage(self, *, now_ns: int) -> policy.RollingUsage:
        """Return conservative trailing-30d usage, retaining ambiguous reservations."""
        cutoff = now_ns - policy.ROLLING_WINDOW_NS
        requests = credits = response = physical = 0
        for campaign in self.legacy_campaigns:
            if campaign["dispatch_start_ns"] <= now_ns and campaign["dispatch_end_ns"] >= cutoff:
                request_count = campaign["request_debit"]
                requests += request_count
                credits += request_count * policy.CONSERVATIVE_CREDITS_PER_REQUEST
                response += campaign["response_bytes_upper"]
                physical += campaign["physical_bytes_upper"]
        for row in self.request_rows:
            dispatch_ns = row["dispatch_ns"]
            if dispatch_ns is None:
                # Unknown historical dispatch time is retained until the latest
                # possible time in the receipt ages out of the rolling window.
                latest_possible_ns = row["latest_possible_dispatch_ns"]
                if latest_possible_ns >= cutoff:
                    requests += row["request_debit"]
                    credits += row["modeled_credit_debit"]
                    response += row["response_bytes_debit"]
                    physical += row["physical_bytes_debit"]
                continue
            if cutoff <= dispatch_ns <= now_ns:
                requests += row["request_debit"]
                credits += row["modeled_credit_debit"]
                response += row["response_bytes_debit"]
                physical += row["physical_bytes_debit"]
        if requests > policy.MAX_ROLLING_REQUESTS:
            raise CutoverUsageError("cutover_requests_over_ceiling")
        return policy.RollingUsage(requests, credits, response, physical)


def _sha(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise CutoverUsageError(f"{field}_invalid")
    return value


def _nonnegative(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise CutoverUsageError(f"{field}_invalid")
    return value


def verify_receipt(
    raw: bytes,
    *,
    expected_sha256: str,
    expected_v4_sources: Mapping[str, str],
    expected_cutover_exception_sha256: str | None = None,
) -> VerifiedCutoverUsage:
    """Verify exact metadata receipt and its v4 source-code binding."""
    if schema.sha(raw) != _sha(expected_sha256, "expected_receipt_sha256"):
        raise CutoverUsageError("receipt_digest_mismatch")
    try:
        value = schema.decode(raw)
    except schema.SchemaError as exc:
        raise CutoverUsageError("receipt_json_invalid") from exc
    required_v1 = {
        "schema",
        "schema_version",
        "generated_ns",
        "service",
        "source_v4",
        "legacy_campaigns",
        "rolling_v4_request_rows",
        "rolling_v4_accounting",
        "accounting",
    }
    required = (
        required_v1 | {"cutover_exception"}
        if isinstance(value, dict) and value.get("schema_version") == 2
        else required_v1
    )
    if (
        not isinstance(value, dict)
        or set(value) != required
        or schema.canonical(value) != raw
        or value.get("schema") != RECEIPT_SCHEMA
        or value.get("schema_version") not in {1, RECEIPT_VERSION}
        or value.get("service") != "helius"
    ):
        raise CutoverUsageError("receipt_shape_invalid")
    generated_ns = _nonnegative(value.get("generated_ns"), "generated_ns")
    exception = value.get(
        "cutover_exception",
        {
            "path": None,
            "sha256": None,
            "classification": None,
            "v4_policy_violation": False,
        },
    )
    if not isinstance(exception, dict) or set(exception) != {
        "path",
        "sha256",
        "classification",
        "v4_policy_violation",
    }:
        raise CutoverUsageError("cutover_exception_shape_invalid")
    if exception.get("v4_policy_violation") is True:
        exception_sha = _sha(exception.get("sha256"), "cutover_exception_sha256")
        if (
            exception.get("path")
            != f"{schema.SERVICE_RELATIVE_ROOT}/{schema.CUTOVER_EXCEPTION_FILE}"
            or exception.get("classification") != "invalid_open_after_two_abandons"
            or (
                expected_cutover_exception_sha256 is not None
                and exception_sha != expected_cutover_exception_sha256
            )
        ):
            raise CutoverUsageError("cutover_exception_binding_invalid")
    elif (
        exception
        != {
            "path": None,
            "sha256": None,
            "classification": None,
            "v4_policy_violation": False,
        }
        or expected_cutover_exception_sha256 is not None
    ):
        raise CutoverUsageError("unexpected_cutover_exception_fields")
    source = value["source_v4"]
    if not isinstance(source, dict) or set(source) != {
        "service_root",
        "plan_sha256",
        "terminal_sha256",
        "event_tail_sha256",
        "allocation_manifest_sha256",
        "source_bindings",
        "source_bindings_sha256",
        "terminal_status",
        "verified_interval_start_ns",
        "verified_interval_end_ns",
    }:
        raise CutoverUsageError("source_binding_shape_invalid")
    source_bindings = source["source_bindings"]
    if (
        source.get("service_root") != "services/external-rolling-v4"
        or source.get("terminal_status") not in {"complete", "abandoned", "cap_paused"}
        or not isinstance(source_bindings, dict)
        or source_bindings != dict(sorted(expected_v4_sources.items()))
        or source.get("source_bindings_sha256") != schema.sha(schema.canonical(source_bindings))
    ):
        raise CutoverUsageError("source_binding_mismatch")
    start_ns = _nonnegative(source.get("verified_interval_start_ns"), "source_interval_start")
    end_ns = _nonnegative(source.get("verified_interval_end_ns"), "source_interval_end")
    if not start_ns or end_ns < start_ns or end_ns > generated_ns:
        raise CutoverUsageError("source_interval_invalid")
    campaigns = value["legacy_campaigns"]
    if not isinstance(campaigns, dict) or set(campaigns) != {"c1", "c2", "c3", "c4"}:
        raise CutoverUsageError("legacy_campaigns_invalid")
    verified_campaigns: list[dict[str, Any]] = []
    for name, expected_count in {**LEGACY_COUNTS, "c4": HISTORICAL_C4["request_count"]}.items():
        record = campaigns[name]
        if (
            not isinstance(record, dict)
            or set(record)
            != {
                "request_count",
                "dispatch_start_ns",
                "dispatch_end_ns",
                "response_bytes_upper",
                "physical_bytes_upper",
                "evidence_sha256",
                "evidence_path",
                "attribution",
            }
            or record.get("request_count") != expected_count
            or record.get("attribution") != "possible_same_account"
        ):
            raise CutoverUsageError("legacy_campaign_count_or_shape_invalid")
        _sha(record.get("evidence_sha256"), f"{name}_evidence_sha256")
        if name in LEGACY_SOURCE_BINDINGS:
            source_pin = LEGACY_SOURCE_BINDINGS[name]
            if (
                record.get("evidence_path") != source_pin["evidence_path"]
                or record.get("evidence_sha256") != source_pin["evidence_sha256"]
                or record.get("dispatch_start_ns") != LEGACY_CAMPAIGN_START_NS
                or record.get("dispatch_end_ns") != LEGACY_CAMPAIGN_END_NS
                or record.get("response_bytes_upper")
                != (0 if expected_count == 0 else 4_000_000_000)
                or record.get("physical_bytes_upper")
                != (0 if expected_count == 0 else 4_000_000_000)
            ):
                raise CutoverUsageError("legacy_campaign_source_pin_or_envelope_mismatch")
        _nonnegative(record.get("response_bytes_upper"), f"{name}_response_upper")
        _nonnegative(record.get("physical_bytes_upper"), f"{name}_physical_upper")
        count = record["request_count"]
        c_start = _nonnegative(record.get("dispatch_start_ns"), f"{name}_dispatch_start")
        c_end = _nonnegative(record.get("dispatch_end_ns"), f"{name}_dispatch_end")
        if c_start > c_end or c_end > generated_ns:
            raise CutoverUsageError("legacy_campaign_interval_invalid")
        if count > 0 and (c_end == 0 or not c_start):
            raise CutoverUsageError("legacy_campaign_dispatch_window_missing")
        if count == 0 and (record["response_bytes_upper"] or record["physical_bytes_upper"]):
            raise CutoverUsageError("empty_campaign_has_usage")
        if name == "c4" and (
            count != HISTORICAL_C4["request_count"]
            or c_start != HISTORICAL_C4["dispatch_start_ns"]
            or c_end != HISTORICAL_C4["dispatch_end_ns"]
            or record["response_bytes_upper"] < HISTORICAL_C4["response_bytes_upper"]
            or record["physical_bytes_upper"] < HISTORICAL_C4["physical_bytes_upper"]
            or record["evidence_sha256"] != HISTORICAL_C4["evidence_sha256"]
            or record["evidence_path"] != HISTORICAL_C4["evidence_path"]
        ):
            raise CutoverUsageError("historical_c4_binding_invalid")
        verified_campaigns.append(
            {
                "campaign_id": name,
                "dispatch_start_ns": c_start,
                "dispatch_end_ns": c_end,
                "request_debit": count,
                "response_bytes_upper": record["response_bytes_upper"],
                "physical_bytes_upper": record["physical_bytes_upper"],
            }
        )
    accounting = value["accounting"]
    if (
        not isinstance(accounting, dict)
        or set(accounting)
        != {
            "historical_c4_exact_count",
            "historical_c4_debit_requests",
            "rolling_v4_exact",
            "rolling_v4_debit_requests",
            "legacy_possible_same_account",
            "provider_charge_units",
            "provider_charge_status",
        }
        or accounting.get("historical_c4_exact_count") is not True
        or accounting.get("historical_c4_debit_requests") != HISTORICAL_C4["request_count"]
        or type(accounting.get("rolling_v4_exact")) is not bool
        or accounting.get("legacy_possible_same_account") is not True
        or accounting.get("provider_charge_units") is not None
        or accounting.get("provider_charge_status") != "unknown_unverified"
    ):
        raise CutoverUsageError("legacy_accounting_invalid")
    rows = value["rolling_v4_request_rows"]
    if not isinstance(rows, list):
        raise CutoverUsageError("rolling_v4_request_rows_invalid")
    verified_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        required_row = {
            "campaign_id",
            "request_id",
            "intent_sha256",
            "outcome_sha256",
            "decision_ns",
            "dispatch_ns",
            "latest_possible_dispatch_ns",
            "operation",
            "outcome",
            "request_debit",
            "modeled_credit_debit",
            "response_bytes_debit",
            "physical_bytes_debit",
            "source_event_sha256",
        }
        if not isinstance(row, dict) or set(row) != required_row:
            raise CutoverUsageError(f"request_row_{index}_shape_invalid")
        request_id = row["request_id"]
        if not isinstance(request_id, str) or not request_id or request_id in seen:
            raise CutoverUsageError("request_id_duplicate_or_invalid")
        if row["campaign_id"] != "rolling_v4":
            raise CutoverUsageError("request_campaign_invalid")
        seen.add(request_id)
        _sha(row["intent_sha256"], "intent_sha256")
        _sha(row["outcome_sha256"], "outcome_sha256")
        _sha(row["source_event_sha256"], "source_event_sha256")
        dispatch = row["dispatch_ns"]
        decision = _nonnegative(row["decision_ns"], "decision_ns")
        latest = _nonnegative(row["latest_possible_dispatch_ns"], "latest_possible_dispatch_ns")
        if not start_ns <= decision <= latest <= end_ns:
            raise CutoverUsageError("request_temporal_bounds_invalid")
        if dispatch is not None:
            _nonnegative(dispatch, "dispatch_ns")
            if not decision <= dispatch <= latest:
                raise CutoverUsageError("dispatch_outside_verified_interval")
        if row["operation"] not in {"getSlot", "getBlocks", "getBlock"}:
            raise CutoverUsageError("request_operation_invalid")
        if row["outcome"] not in {"captured", "possibly_spent", "pre_intent_released"}:
            raise CutoverUsageError("request_outcome_invalid")
        expected_debits = {
            "request_debit": 0 if row["outcome"] == "pre_intent_released" else 1,
            "modeled_credit_debit": 0
            if row["outcome"] == "pre_intent_released"
            else policy.CONSERVATIVE_CREDITS_PER_REQUEST,
        }
        for field, expected in expected_debits.items():
            if _nonnegative(row[field], field) != expected:
                raise CutoverUsageError("request_debit_mismatch")
        response_debit = _nonnegative(row["response_bytes_debit"], "response_bytes_debit")
        physical_debit = _nonnegative(row["physical_bytes_debit"], "physical_bytes_debit")
        if response_debit > policy.MAX_RESPONSE_BYTES or physical_debit > (
            policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES
        ):
            raise CutoverUsageError("request_upper_bound_invalid")
        if row["outcome"] == "pre_intent_released" and (
            response_debit != 0 or dispatch is not None
        ):
            raise CutoverUsageError("pre_intent_dispatch_or_response_invalid")
        if (row["outcome"] == "captured") != (dispatch is not None):
            raise CutoverUsageError("dispatch_outcome_binding_invalid")
        if row["outcome"] == "possibly_spent" and (
            row["response_bytes_debit"] < policy.MAX_RESPONSE_BYTES
            or row["physical_bytes_debit"] < policy.MAX_RESPONSE_BYTES
        ):
            raise CutoverUsageError("ambiguous_request_reserve_understated")
        verified_rows.append(dict(row))
    rolling = value["rolling_v4_accounting"]
    if not isinstance(rolling, dict) or set(rolling) != {
        "exact",
        "request_count",
        "dispatch_start_ns",
        "dispatch_end_ns",
        "response_bytes_upper",
        "physical_bytes_upper",
    }:
        raise CutoverUsageError("rolling_v4_accounting_shape_invalid")
    count = _nonnegative(rolling.get("request_count"), "rolling_v4_request_count")
    if count > v4_policy.MAX_EPOCH_REQUESTS or accounting["rolling_v4_debit_requests"] != count:
        raise CutoverUsageError("rolling_v4_request_count_invalid")
    if rolling.get("exact") is not accounting["rolling_v4_exact"]:
        raise CutoverUsageError("rolling_v4_exact_mismatch")
    dispatched = [row for row in verified_rows if row["request_debit"] == 1]
    if len(dispatched) != count:
        if rolling.get("exact") is True or dispatched:
            raise CutoverUsageError("rolling_v4_exact_count_mismatch")
    start = _nonnegative(rolling.get("dispatch_start_ns"), "rolling_v4_start")
    end = _nonnegative(rolling.get("dispatch_end_ns"), "rolling_v4_end")
    if rolling["exact"]:
        if start != min(
            (row["dispatch_ns"] or row["latest_possible_dispatch_ns"] for row in dispatched),
            default=0,
        ):
            raise CutoverUsageError("rolling_v4_dispatch_start_mismatch")
        if end != max((row["latest_possible_dispatch_ns"] for row in dispatched), default=0):
            raise CutoverUsageError("rolling_v4_dispatch_end_mismatch")
    response_upper = _nonnegative(rolling.get("response_bytes_upper"), "rolling_v4_response_upper")
    physical_upper = _nonnegative(rolling.get("physical_bytes_upper"), "rolling_v4_physical_upper")
    if response_upper < sum(row["response_bytes_debit"] for row in dispatched):
        raise CutoverUsageError("rolling_v4_response_upper_understated")
    if physical_upper < sum(row["physical_bytes_debit"] for row in dispatched):
        raise CutoverUsageError("rolling_v4_physical_upper_understated")
    if not rolling["exact"] and (
        count != v4_policy.MAX_EPOCH_REQUESTS
        or response_upper < v4_policy.MAX_EPOCH_RESPONSE_BYTES
        or physical_upper < v4_policy.MAX_EPOCH_PHYSICAL_BYTES
        or not start
        or end < start
    ):
        raise CutoverUsageError("rolling_v4_conservative_bound_missing")
    if not rolling["exact"]:
        verified_campaigns.append(
            {
                "campaign_id": "rolling_v4_unknown",
                "dispatch_start_ns": start,
                "dispatch_end_ns": end,
                "request_debit": count,
                "response_bytes_upper": response_upper,
                "physical_bytes_upper": physical_upper,
            }
        )
    return VerifiedCutoverUsage(
        receipt_sha256=schema.sha(raw),
        generated_ns=generated_ns,
        source_plan_sha256=_sha(source["plan_sha256"], "source_plan_sha256"),
        source_terminal_sha256=_sha(source["terminal_sha256"], "source_terminal_sha256"),
        source_event_tail_sha256=_sha(source["event_tail_sha256"], "source_event_tail_sha256"),
        source_allocation_manifest_sha256=_sha(
            source["allocation_manifest_sha256"], "source_allocation_manifest_sha256"
        ),
        source_bindings_sha256=_sha(source["source_bindings_sha256"], "source_bindings_sha256"),
        legacy_campaigns=tuple(verified_campaigns),
        request_rows=tuple(verified_rows),
        legacy_possible_same_account=accounting["legacy_possible_same_account"],
        rolling_v4_exact=bool(accounting["rolling_v4_exact"]),
        rolling_v4_debit_requests=accounting["rolling_v4_debit_requests"],
        cutover_exception_sha256=exception.get("sha256"),
        v4_policy_violation=exception["v4_policy_violation"],
    )
