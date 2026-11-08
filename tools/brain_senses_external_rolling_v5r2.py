#!/usr/bin/env python3
"""Helius v5r2 cutover verifier and forward-only release adapter.

This module prepares a distinct v5r2 release beneath the existing v5 service
allocation. It never repairs old roots, mutates the coordinator, or makes
provider calls. Verified cutover bytes can be published create-only through a
caller-held descriptor.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import plistlib
import stat
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for _import_root in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))

from memecoin_research_storage import (  # noqa: E402
    StorageConfig,
    _open_directory,
    _open_relative_directory,
    writer_guard,
)
from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_external_capture_verify_v5 as capture_verify  # noqa: E402
from tools import brain_senses_external_clock_v1 as clock_check  # noqa: E402
from tools import brain_senses_external_cutover_usage_v5 as cutover_usage  # noqa: E402
from tools import brain_senses_external_frame_verify_v5 as frame_verify  # noqa: E402
from tools import brain_senses_external_orchestrator_v5 as v5_runtime  # noqa: E402
from tools import brain_senses_external_rolling_policy_v1 as v4_policy  # noqa: E402
from tools import brain_senses_external_rolling_policy_v5 as policy  # noqa: E402
from tools import brain_senses_external_sampling_v5 as sampling  # noqa: E402
from tools import brain_senses_external_schema_v5 as v5_schema  # noqa: E402
from tools import brain_senses_external_verify_v5 as v5_verify  # noqa: E402
from tools.brain_senses_external_writer_v1 import (  # noqa: E402
    DescriptorWriter,
    semantic_request_sha256,
    wire_request_sha256,
)

RELEASE_ID = "helius-external-rolling-v5r2"
RELEASE_RELATIVE_ROOT = "services/external-rolling-v5/releases/v5r2"
RELEASE_SCHEMA = "brain-senses-external-rolling-v5r2-cutover"
RELEASE_VERSION = 1
RELEASE_MANIFEST_SCHEMA = "brain-senses-external-rolling-v5r2-release-manifest"
RELEASE_MANIFEST_VERSION = 1
RECEIPT_RELATIVE_PATH = f"{RELEASE_RELATIVE_ROOT}/cutover-receipt.json"
MANIFEST_RELATIVE_PATH = f"{RELEASE_RELATIVE_ROOT}/release-manifest.json"
ACTIVATION_CAP_PATH = "services/external-rolling-v4/service-allocation-cap.json"
OLD_COORDINATOR_SHA256 = "b150076874e04b78422efb4ca3818d904a44ec09761b4ead0087882699b632d6"
CURRENT_COORDINATOR_SHA256 = "fd66c42036feccfeeb811c5a310263be7bfc22fb09c9e2f8525cd9f2a1f9fdc8"
EXPECTED_V5_EPOCHS = (
    "20261012T170000Z-5eb36e8fd3f2",
    "20261012T200000Z-5df4c61c68f7",
)
INVALID_OPEN_EPOCH = "20261012T230000Z-612dad349b43"
EXPECTED_ATTEMPTS = 35
EXPECTED_AMBIGUOUS = 2
MIN_R2_ANCHOR_NS = 1_791_918_000_000_000_000  # 2026-10-13T19:00:00Z
MAX_BYTES = 64_000_000

SOURCE_PATHS = (
    "tools/brain_senses_external_rolling_v5r2.py",
    "tools/brain_senses_external_orchestrator_v5.py",
    "tools/brain_senses_external_verify_v5.py",
    "tools/brain_senses_external_schema_v5.py",
    "tools/brain_senses_external_rolling_policy_v5.py",
    "tools/brain_senses_external_clock_v1.py",
    "tools/brain_senses_external_capture_verify_v5.py",
    "tools/brain_senses_external_frame_verify_v5.py",
    "tools/brain_senses_external_sampling_v5.py",
    "tools/brain_senses_external_cutover_usage_v5.py",
    "tools/brain_senses_external_cutover_v4_to_v5.py",
    "tools/brain_senses_external_orchestrator_v4.py",
    "tools/brain_senses_external_schema_v4.py",
    "tools/brain_senses_external_verify_v4.py",
    "tools/brain_senses_external_rolling_policy_v1.py",
    "tools/brain_senses_external_writer_v1.py",
    "tools/brain_senses_capacity_coordinator_v2.py",
    "src/memecoin_research_storage/__init__.py",
    "ops/launchd/ai.memecoin.brain.senses.external-rolling-v5r2.plist",
)


class ReleaseError(ValueError):
    """The historical receipt, cutover boundary, or release gate is invalid."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return v5_schema.canonical(value)


def source_bindings(repo: Path | None = None) -> dict[str, str]:
    """Hash the exact implementation and plist that the v5r2 release will pin."""
    root = repo or Path(__file__).resolve().parents[1]
    try:
        return {name: _sha((root / name).read_bytes()) for name in SOURCE_PATHS}
    except OSError as exc:
        raise ReleaseError("release_source_missing") from exc


def _source_exception(planned: Any, current: Mapping[str, str]) -> None:
    """Accept the one observed coordinator transition and no other old-source drift."""
    if not isinstance(planned, dict) or set(planned) != set(current):
        raise ReleaseError("historical_source_set_invalid")
    changed = {name for name in current if planned.get(name) != current[name]}
    coordinator = "tools/brain_senses_capacity_coordinator_v2.py"
    if current.get(coordinator) != CURRENT_COORDINATOR_SHA256:
        raise ReleaseError("current_coordinator_source_drift")
    if not changed:
        return
    if changed != {coordinator} or planned.get(coordinator) != OLD_COORDINATOR_SHA256:
        raise ReleaseError("historical_source_drift")


def _read_canonical_json(
    reader: DescriptorWriter, path: str, *, limit: int = MAX_BYTES
) -> tuple[bytes, Any]:
    raw = reader.read_bytes(path, max_bytes=limit)
    value = v5_schema.decode(raw)
    if _canonical(value) != raw:
        raise ReleaseError("source_json_not_canonical")
    return raw, value


def _read_root_inventory(reader: DescriptorWriter, relative: str) -> list[dict[str, Any]]:
    """Create the same conservative allocation manifest without writing or repairing."""
    rows: list[dict[str, Any]] = []

    def add(path: str, item: os.stat_result, kind: str) -> None:
        size = item.st_size if kind == "file" else 0
        rows.append(
            {
                "path": path,
                "kind": kind,
                "size_bytes": size,
                "allocated_upper_bytes": max(size, item.st_blocks * 512) + 16 * 1024,
                "mode": stat.S_IMODE(item.st_mode),
                "owner": item.st_uid,
                "links": item.st_nlink,
            }
        )

    add(".", reader.stat(relative), "directory")

    def visit(current: str, prefix: str) -> None:
        for name in reader.list_names(current):
            child = f"{current}/{name}"
            item = reader.stat(child)
            if stat.S_ISDIR(item.st_mode):
                kind = "directory"
            elif stat.S_ISREG(item.st_mode):
                kind = "file"
            else:
                raise ReleaseError("inventory_entry_type_unsupported")
            path = f"{prefix}/{name}" if prefix else name
            add(path, item, kind)
            if kind == "directory":
                visit(child, path)

    visit(relative, "")
    return sorted(rows, key=lambda row: row["path"])


def _allocation_manifest(
    reader: DescriptorWriter,
    epoch_id: str,
    *,
    root_prefix: str | None = None,
) -> tuple[str, int, int, list[dict[str, Any]]]:
    root = f"{root_prefix or v5_schema.SERVICE_RELATIVE_ROOT}/epochs/{epoch_id}"
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
        for row in _read_root_inventory(reader, root)
        if row["path"] not in {".", v5_schema.TERMINAL_FILE}
    ]
    raw = _canonical(rows)
    return _sha(raw), sum(row["allocated_upper_bytes"] for row in rows), len(rows), rows


def _read_intents_outcomes(
    event_rows: Sequence[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    intents: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    for event in event_rows:
        if event["kind"] == "request_intent":
            request_id = event["fields"].get("request_id")
            if not isinstance(request_id, str) or request_id in intents:
                raise ReleaseError("request_intent_duplicate")
            intents[request_id] = event
        elif event["kind"] in {"request_captured", "request_failed"}:
            request_id = event["fields"].get("request_id")
            if not isinstance(request_id, str) or request_id in outcomes:
                raise ReleaseError("request_outcome_duplicate")
            outcomes[request_id] = event
    if set(intents) != set(outcomes):
        raise ReleaseError("request_outcome_denominator_mismatch")
    return intents, outcomes


def _verify_sealed_frames(
    reader: DescriptorWriter,
    epoch_id: str,
    plan: Mapping[str, Any],
    event_bytes: bytes,
    *,
    root_prefix: str | None = None,
) -> None:
    """Replay anchor, inventory, sampled block requests, and raw capture pairs."""
    events = v5_schema.verify_event_chain(event_bytes, schema=v5_schema.EVENT_SCHEMA)
    epoch_prefix = root_prefix or f"services/external-rolling-v5/epochs/{epoch_id}"
    intents = {
        e["fields"]["request_id"]: e["fields"] for e in events if e["kind"] == "request_intent"
    }
    windows = {
        e["fields"]["index"]: e["fields"]["mode"] for e in events if e["kind"] == "window_reserved"
    }
    results: dict[str, dict[str, Any]] = {}
    for e in events:
        if e["kind"] != "request_captured":
            continue
        request_id = e["fields"]["request_id"]
        intent = intents[request_id]
        result = capture_verify.verify_capture(
            reader,
            relative_directory=f"{epoch_prefix}/captures/{request_id}",
            expected={
                "request_id": request_id,
                "request_sha256": semantic_request_sha256(intent["method"], intent["params"]),
                "wire_request_sha256": wire_request_sha256(intent["method"], intent["params"]),
                "decision_ns": intent["decision_ns"],
            },
        )
        if "result" not in result["response"]:
            raise ReleaseError("captured_request_missing_result")
        results[request_id] = result
    prior_anchor: int | None = None
    for event in events:
        if event["kind"] != "frame_sealed":
            continue
        fields = event["fields"]
        bucket, index, ids = fields["bucket"], fields["index"], fields["request_ids"]
        mode = windows.get(index)
        if not isinstance(ids, list) or not ids or any(item not in results for item in ids):
            raise ReleaseError("sealed_frame_capture_missing")
        calls = [
            (intents[item], results[item]["response"]["result"], results[item]) for item in ids
        ]
        if mode in {"anchor", "anchor_reset"}:
            intent, result, _ = calls[0]
            if (
                len(calls) != 1
                or intent["method"] != "getSlot"
                or intent["params"] != [{"commitment": "finalized"}]
                or fields.get("empty_interval") is not False
            ):
                raise ReleaseError("anchor_frame_invalid")
            prior_anchor = frame_verify.finalized_slot(result)
            continue
        if mode != "inventory":
            raise ReleaseError("sealed_frame_mode_invalid")
        first_intent, current_raw, _ = calls[0]
        current_slot = frame_verify.finalized_slot(current_raw)
        if (
            first_intent.get("method") != "getSlot"
            or first_intent.get("params") != [{"commitment": "finalized"}]
            or prior_anchor is None
            or current_slot < prior_anchor
        ):
            raise ReleaseError("inventory_anchor_invalid")
        if current_slot == prior_anchor:
            if len(calls) != 1 or fields.get("empty_interval") is not True:
                raise ReleaseError("empty_inventory_interval_invalid")
            continue
        if fields.get("empty_interval") is not False or len(calls) < 2:
            raise ReleaseError("inventory_capture_missing")
        inv_intent, inventory_raw, inv_capture = calls[1]
        if inv_intent.get("method") != "getBlocks" or inv_intent.get("params") != [
            prior_anchor + 1,
            current_slot,
            {"commitment": "finalized"},
        ]:
            raise ReleaseError("inventory_request_invalid")
        inventory = frame_verify.inventory_slots(
            inventory_raw, start=prior_anchor + 1, end_inclusive=current_slot
        )
        selection = sampling.build_selection(
            epoch_id=epoch_id,
            bucket=bucket,
            inventory_slots=inventory,
            inventory_sha256=inv_capture["response_sha256"],
        )
        path = f"{epoch_prefix}/selections/{bucket}.json"
        if reader.read_bytes(path, max_bytes=1_000_000) != _canonical(selection):
            raise ReleaseError("selection_replay_mismatch")
        if len(calls) != 2 + 2 * len(selection["selected_slots"]):
            raise ReleaseError("sample_request_count_mismatch")
        pairs = []
        for pair_index, slot in enumerate(selection["selected_slots"]):
            signature_intent, signatures, _ = calls[2 + pair_index * 2]
            full_intent, full, _ = calls[3 + pair_index * 2]
            if signature_intent.get("method") != "getBlock" or signature_intent.get("params") != [
                slot,
                {
                    "commitment": "finalized",
                    "encoding": "json",
                    "transactionDetails": "signatures",
                    "rewards": False,
                },
            ]:
                raise ReleaseError("signature_request_invalid")
            if full_intent.get("method") != "getBlock" or full_intent.get("params") != [
                slot,
                {
                    "commitment": "finalized",
                    "encoding": "json",
                    "transactionDetails": "full",
                    "rewards": False,
                    "maxSupportedTransactionVersion": 1,
                },
            ]:
                raise ReleaseError("full_block_request_invalid")
            pairs.append({"slot": slot, "signatures": signatures, "full": full})
        frame_verify.verify_sample_pairs(
            epoch_id=epoch_id,
            bucket=bucket,
            inventory=inventory,
            inventory_sha256=inv_capture["response_sha256"],
            pairs=pairs,
        )
        prior_anchor = current_slot


def _ledger_rows(
    reader: DescriptorWriter, *, root_id: str, volume_uuid: str
) -> tuple[
    bytes, bytes, list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]
]:
    policy_raw = reader.read_bytes(capacity.POLICY_PATH, max_bytes=64_000)
    policy_value = v5_schema.decode(policy_raw)
    if (
        _canonical(policy_value) != policy_raw
        or policy_value.get("schema") != capacity.POLICY_SCHEMA
    ):
        raise ReleaseError("coordinator_policy_invalid")
    expected_roots = {
        "helius": ["services/external-rolling-v4", "services/external-rolling-v5"],
        "alchemy": ["services/alchemy-sentinel-v2"],
        "gecko": ["services/gecko-market-v1"],
        "coordinator": ["services/senses-capacity-v2"],
    }
    binding = policy_value.get("root_binding")
    if (
        policy_value.get("allocation_roots") != expected_roots
        or policy_value.get("service_lock_paths", {}).get("helius") != ".external-rolling-v4.lock"
        or not isinstance(binding, dict)
        or binding.get("root_id") != root_id
        or binding.get("volume_uuid") != volume_uuid.lower()
        or policy_value.get("service_caps_bytes", {}).get("helius")
        != policy.HELIUS_RESIDENT_ALLOCATION_CAP_BYTES
    ):
        raise ReleaseError("coordinator_roots_changed")
    raw = reader.read_bytes(capacity.LEDGER_PATH, max_bytes=MAX_BYTES)
    rows = capacity._verify_ledger(raw)
    if rows[0]["fields"]["policy_sha256"] != _sha(policy_raw):
        raise ReleaseError("coordinator_ledger_policy_binding_invalid")
    reservations, terminal_ids = capacity._reservation_state(rows)
    terminals = {
        e["fields"]["reservation_id"]: e
        for e in rows
        if e["kind"] in {"settled", "ambiguous_terminal", "released"}
    }
    return policy_raw, raw, rows, reservations, terminals


def verify_coordinator_cutover_prefix(
    ledger_bytes: bytes,
    *,
    policy_sha256: str,
    cutover_ledger_sha256: str,
    cutover_event_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify the exact persisted cutover prefix and replay any valid suffix.

    The coordinator ledger continues to serve Gecko/Alchemy after cutover, so
    its current whole-file digest is expected to change. The cutover receipt
    pins an event-count/SHA prefix; the shared coordinator verifier validates
    the full later hash-linked suffix.
    """
    if (
        not isinstance(policy_sha256, str)
        or len(policy_sha256) != 64
        or any(char not in "0123456789abcdef" for char in policy_sha256)
        or not isinstance(cutover_ledger_sha256, str)
        or len(cutover_ledger_sha256) != 64
        or any(char not in "0123456789abcdef" for char in cutover_ledger_sha256)
        or type(cutover_event_count) is not int
        or cutover_event_count < 1
    ):
        raise ReleaseError("coordinator_cutover_prefix_pin_invalid")
    rows = capacity._verify_ledger(ledger_bytes)
    if len(rows) < cutover_event_count:
        raise ReleaseError("coordinator_cutover_prefix_missing")
    prefix = b"".join(capacity.canonical(row) for row in rows[:cutover_event_count])
    if (
        _sha(prefix) != cutover_ledger_sha256
        or rows[0]["fields"].get("policy_sha256") != policy_sha256
    ):
        raise ReleaseError("coordinator_cutover_prefix_changed")
    return rows[:cutover_event_count], rows[cutover_event_count:]


def verify_r2_capacity_suffix(
    reader: DescriptorWriter,
    ledger_bytes: bytes,
    *,
    receipt: Mapping[str, Any],
    verified_request_events: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reconcile every post-cutover Helius reservation with nested r2 files.

    Other shared lanes are accepted as coordinator suffix activity and remain
    charged by the shared coordinator itself. Every Helius suffix reservation
    must be under the nested r2 root and map one-to-one to its durable intent.
    """
    shared = receipt.get("shared_capacity")
    if not isinstance(shared, dict):
        raise ReleaseError("shared_capacity_prefix_binding_invalid")
    policy_sha256 = shared.get("policy_sha256")
    cutover_ledger_sha256 = shared.get("ledger_sha256")
    cutover_event_count = shared.get("ledger_event_count")
    _, suffix = verify_coordinator_cutover_prefix(
        ledger_bytes,
        policy_sha256=policy_sha256 if isinstance(policy_sha256, str) else "",
        cutover_ledger_sha256=(
            cutover_ledger_sha256 if isinstance(cutover_ledger_sha256, str) else ""
        ),
        cutover_event_count=(cutover_event_count if type(cutover_event_count) is int else 0),
    )
    try:
        rows = capacity._verify_ledger(ledger_bytes)
        reservations, terminal_ids = capacity._reservation_state(rows)
    except capacity.CoordinatorError as exc:
        raise ReleaseError("coordinator_ledger_invalid") from exc
    terminal_events = {
        event["fields"]["reservation_id"]: event
        for event in suffix
        if event["kind"] in {"settled", "ambiguous_terminal", "released"}
    }
    suffix_reservations = {
        event["fields"]["reservation_id"]: event["fields"]
        for event in suffix
        if event["kind"] == "reserved" and event["fields"].get("service") == "helius"
    }
    request_epochs: dict[str, set[str]] = {}
    for reservation_id in suffix_reservations:
        _prefix, event_epoch, event_request = reservation_id.split(":", 2)
        request_epochs.setdefault(event_request, set()).add(event_epoch)
    intents: dict[tuple[str | None, str], dict[str, Any]] = {}
    outcomes: dict[tuple[str | None, str], dict[str, Any]] = {}
    for event in verified_request_events:
        kind = event.get("kind")
        if kind not in {"request_intent", "request_captured", "request_failed"}:
            continue
        fields = event["fields"]
        request_id = fields.get("request_id")
        epoch_id = fields.get("epoch_id")
        if epoch_id is None and isinstance(request_id, str):
            matches = request_epochs.get(request_id, set())
            if len(matches) == 1:
                epoch_id = next(iter(matches))
        key = (epoch_id, request_id)
        target = intents if kind == "request_intent" else outcomes
        if key in target:
            reason = "r2_intent_duplicate" if kind == "request_intent" else "r2_outcome_duplicate"
            raise ReleaseError(reason)
        target[key] = fields if kind == "request_intent" else event
    result_rows: list[dict[str, Any]] = []
    linked_ids: set[str] = set()
    prefix = f"{RELEASE_RELATIVE_ROOT}/epochs/"
    for reservation_id, reservation in suffix_reservations.items():
        intent_path = reservation.get("intent_path")
        outcome_path = reservation.get("outcome_path")
        if (
            not isinstance(intent_path, str)
            or not intent_path.startswith(prefix)
            or not isinstance(outcome_path, str)
            or not outcome_path.startswith(prefix)
            or reservation_id.count(":") != 2
            or not reservation_id.startswith("helius-v5r2:")
        ):
            raise ReleaseError("helius_suffix_outside_r2_root")
        _, epoch_id, request_id = reservation_id.split(":", 2)
        request_key = (epoch_id, request_id)
        base = f"{prefix}{epoch_id}"
        expected_intent = f"{base}/intents/{request_id}.json"
        expected_outcome = f"{base}/outcomes/{request_id}.json"
        if intent_path != expected_intent or outcome_path != expected_outcome:
            raise ReleaseError("r2_reservation_path_binding_invalid")
        terminal = terminal_events.get(reservation_id)
        if reservation_id in terminal_ids and terminal is None:
            raise ReleaseError("r2_terminal_suffix_missing")
        if terminal is not None and terminal["kind"] == "released":
            if request_key in intents or request_key in outcomes:
                raise ReleaseError("r2_released_after_durable_intent")
            try:
                reader.stat(intent_path)
            except FileNotFoundError:
                pass
            else:
                raise ReleaseError("r2_released_intent_file_present")
            try:
                reader.stat(outcome_path)
            except FileNotFoundError:
                pass
            else:
                raise ReleaseError("r2_released_outcome_file_present")
            continue
        try:
            intent_raw, intent_doc = _read_canonical_json(reader, intent_path, limit=64_000)
        except FileNotFoundError as exc:
            raise ReleaseError("r2_reserved_intent_file_missing") from exc
        if (
            _sha(intent_raw) != reservation.get("intent_sha256")
            or intent_doc.get("schema") != "brain-senses-external-rolling-v5r2-capacity-intent"
            or intent_doc.get("service") != "helius"
            or intent_doc.get("epoch_id") != epoch_id
            or intent_doc.get("request_id") != request_id
            or intent_doc.get("reservation_id") not in (None, reservation_id)
        ):
            raise ReleaseError("r2_capacity_intent_binding_invalid")
        intent_event = intents.get(request_key)
        if intent_event is not None:
            event_fields = intent_event
            if not isinstance(event_fields, dict):
                raise ReleaseError("r2_capacity_intent_event_malformed")
            for field_key in (
                "bucket",
                "ordinal",
                "method",
                "params",
                "wire_request_sha256",
                "decision_ns",
            ):
                if intent_doc.get(field_key) != event_fields.get(field_key):
                    raise ReleaseError("r2_capacity_intent_event_mismatch")
        elif request_key in outcomes:
            raise ReleaseError("r2_outcome_without_intent")
        status = "possibly_spent"
        response_bytes: int | None = policy.MAX_RESPONSE_BYTES
        allocated_bytes: int | None = (
            policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES
        )
        availability_ns: int | None = None
        outcome_event = outcomes.get(request_key)
        if terminal is not None and terminal["kind"] == "settled":
            if terminal["fields"].get("outcome_path") != outcome_path:
                raise ReleaseError("r2_settlement_path_mismatch")
            outcome_raw, outcome_doc = _read_canonical_json(reader, outcome_path, limit=64_000)
            if terminal["fields"].get("outcome_sha256") != _sha(outcome_raw):
                raise ReleaseError("r2_settlement_hash_mismatch")
            if outcome_doc.get("status") != "captured":
                raise ReleaseError("r2_settled_outcome_not_captured")
            capture = capture_verify.verify_capture(
                reader,
                relative_directory=f"{base}/captures/{request_id}",
                expected={
                    "request_id": request_id,
                    "request_sha256": intent_doc["semantic_request_sha256"],
                    "wire_request_sha256": intent_doc["wire_request_sha256"],
                    "decision_ns": intent_doc["decision_ns"],
                },
            )
            if (
                outcome_doc.get("response_sha256") != capture["response_sha256"]
                or outcome_doc.get("response_bytes") != capture["response_bytes"]
                or outcome_doc.get("availability_ns") != capture["receipt"]["availability_ns"]
            ):
                raise ReleaseError("r2_settled_capture_mismatch")
            status = "captured"
            response_bytes = capture["response_bytes"]
            allocated_bytes = _capture_allocated_bytes(reader, f"{base}/captures/{request_id}")
            availability_ns = capture["receipt"]["availability_ns"]
        elif terminal is not None and terminal["kind"] == "ambiguous_terminal":
            if terminal["fields"].get("outcome_path") == outcome_path:
                outcome_raw = reader.read_bytes(outcome_path, max_bytes=64_000)
                if terminal["fields"].get("outcome_sha256") != _sha(outcome_raw):
                    raise ReleaseError("r2_ambiguous_outcome_hash_mismatch")
            response_bytes = policy.MAX_RESPONSE_BYTES
            allocated_bytes = policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES
        if outcome_event is not None:
            if status == "captured" and outcome_event["kind"] != "request_captured":
                raise ReleaseError("r2_capacity_event_outcome_mismatch")
            if status != "captured" and outcome_event["kind"] != "request_failed":
                raise ReleaseError("r2_capacity_event_outcome_mismatch")
        elif status == "captured":
            # Settlement may be durable immediately before its local event append.
            # The capture remains independently verified and is safe to reconcile.
            pass
        decision_ns = intent_doc.get("decision_ns")
        if type(decision_ns) is not int or decision_ns <= 0:
            raise ReleaseError("r2_capacity_decision_time_invalid")
        linked_ids.add(f"{epoch_id}:{request_id}")
        result_rows.append(
            {
                "request_id": request_id,
                "epoch_id": epoch_id,
                "status": status,
                "decision_ns": decision_ns,
                "response_bytes": response_bytes,
                "allocated_bytes": allocated_bytes,
                "availability_ns": availability_ns,
                "reservation_id": reservation_id,
                "reservation_sha256": reservation["intent_sha256"],
                "terminal_kind": terminal["kind"] if terminal else None,
                "terminal_event_sha256": terminal["event_sha256"] if terminal else None,
            }
        )
    if {f"{epoch}:{request}" for epoch, request in intents} - linked_ids or {
        f"{epoch}:{request}" for epoch, request in outcomes
    } - linked_ids:
        raise ReleaseError("r2_reservation_event_join_incomplete")
    # Every Helius reservation created in the suffix must belong to this root.
    for reservation_id, fields in suffix_reservations.items():
        if fields.get("service") == "helius" and not fields.get("intent_path", "").startswith(
            prefix
        ):
            raise ReleaseError("helius_reservation_outside_r2_release")
        if reservation_id not in reservations:
            raise ReleaseError("r2_reservation_missing_suffix_event")
    return sorted(result_rows, key=lambda row: (row["decision_ns"], row["request_id"]))


def _combined_usage(
    receipt: Mapping[str, Any],
    suffix_rows: Sequence[Mapping[str, Any]],
    *,
    now_ns: int,
    allow_rolling_exceeded: bool = False,
) -> dict[str, int]:
    """Recompute rolling usage from the immutable inherited rows and r2 suffix."""
    if type(now_ns) is not int or now_ns <= 0:
        raise ReleaseError("usage_clock_invalid")
    inherited = receipt.get("inherited_usage_rows")
    if not isinstance(inherited, dict):
        raise ReleaseError("inherited_usage_rows_invalid")
    cutoff = now_ns - policy.ROLLING_WINDOW_NS
    requests = credits = response = physical = 0
    for campaign in inherited.get("legacy_campaigns", []):
        if campaign["dispatch_start_ns"] <= now_ns and campaign["dispatch_end_ns"] >= cutoff:
            count = campaign["request_debit"]
            requests += count
            credits += count * policy.CONSERVATIVE_CREDITS_PER_REQUEST
            response += campaign["response_bytes_upper"]
            physical += campaign["physical_bytes_upper"]
    for row in inherited.get("rolling_v4_request_rows", []):
        dispatch = row["dispatch_ns"]
        included = (
            row["latest_possible_dispatch_ns"] >= cutoff
            if dispatch is None
            else cutoff <= dispatch <= now_ns
        )
        if included:
            requests += row["request_debit"]
            credits += row["modeled_credit_debit"]
            response += row["response_bytes_debit"]
            physical += row["physical_bytes_debit"]
    r2_lifetime_physical = 0
    for row in suffix_rows:
        decision_ns = row.get("decision_ns")
        if type(decision_ns) is not int or decision_ns <= 0:
            raise ReleaseError("r2_usage_decision_time_invalid")
        response_bytes = row.get("response_bytes")
        allocated_bytes = row.get("allocated_bytes")
        if row.get("status") == "possibly_spent":
            if response_bytes != policy.MAX_RESPONSE_BYTES or allocated_bytes != (
                policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES
            ):
                raise ReleaseError("r2_ambiguous_reserve_invalid")
        elif (
            row.get("status") != "captured"
            or type(response_bytes) is not int
            or type(allocated_bytes) is not int
            or not 0 <= response_bytes <= policy.MAX_RESPONSE_BYTES
            or not 0
            <= allocated_bytes
            <= policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES
        ):
            raise ReleaseError("r2_captured_usage_invalid")
        r2_lifetime_physical += allocated_bytes
        if cutoff <= decision_ns <= now_ns:
            requests += 1
            credits += policy.CONSERVATIVE_CREDITS_PER_REQUEST
            response += response_bytes
            physical += allocated_bytes
    if not allow_rolling_exceeded and (
        requests > policy.MAX_ROLLING_REQUESTS
        or credits > policy.MAX_ROLLING_CREDITS
        or response > policy.MAX_ROLLING_RESPONSE_BYTES
        or physical > policy.MAX_ROLLING_PHYSICAL_BYTES
    ):
        raise ReleaseError("r2_rolling_budget_exceeded")
    lifetime = receipt.get("lifetime_usage")
    cap = receipt.get("activation_bound_lifetime_cap")
    if not isinstance(lifetime, dict) or not isinstance(cap, dict):
        raise ReleaseError("r2_lifetime_binding_missing")
    lifetime_physical = lifetime.get("physical_bytes_upper")
    cap_bytes = cap.get("service_allocation_cap_bytes")
    if (
        type(lifetime_physical) is not int
        or type(cap_bytes) is not int
        or lifetime_physical + r2_lifetime_physical > cap_bytes
    ):
        raise ReleaseError("r2_activation_bound_cap_exceeded")
    return {
        "requests": requests,
        "modeled_credits": credits,
        "response_bytes": response,
        "allocated_bytes": physical,
        "lifetime_physical_bytes": lifetime_physical + r2_lifetime_physical,
        "activation_bound_cap_bytes": cap_bytes,
    }


_R2_ROLLING_LIMITS = {
    "rolling_request_cap": ("requests", policy.MAX_ROLLING_REQUESTS),
    "rolling_credit_cap": ("modeled_credits", policy.MAX_ROLLING_CREDITS),
    "rolling_response_cap": ("response_bytes", policy.MAX_ROLLING_RESPONSE_BYTES),
    "rolling_physical_cap": ("allocated_bytes", policy.MAX_ROLLING_PHYSICAL_BYTES),
}


def _r2_budget_reason(usage: Mapping[str, int]) -> str | None:
    for reason, (field, limit) in _R2_ROLLING_LIMITS.items():
        if usage[field] > limit:
            return reason
    return None


def _r2_budget_evidence_path(digest: str) -> str:
    return f"{RELEASE_RELATIVE_ROOT}/budget-evidence/{digest}.json"


def _write_r2_budget_evidence(
    writer: DescriptorWriter,
    *,
    receipt: Mapping[str, Any],
    suffix_rows: Sequence[Mapping[str, Any]],
    epoch_id: str,
    reason: str,
    disposition: str,
    checked_ns: int,
) -> str:
    usage = _combined_usage(
        receipt,
        suffix_rows,
        now_ns=checked_ns,
        allow_rolling_exceeded=True,
    )
    observed_reason = _r2_budget_reason(usage)
    if disposition == "exceeded":
        if reason not in _R2_ROLLING_LIMITS or observed_reason != reason:
            raise ReleaseError("r2_budget_exceeded_evidence_invalid")
    elif disposition == "headroom":
        if reason not in _R2_ROLLING_LIMITS or observed_reason is not None:
            raise ReleaseError("r2_budget_headroom_evidence_invalid")
    else:
        raise ReleaseError("r2_budget_disposition_invalid")
    fields = {
        "schema": "brain-senses-external-rolling-v5r2-budget-evidence",
        "schema_version": 1,
        "release_id": RELEASE_ID,
        "epoch_id": v5_schema.validate_epoch_id(epoch_id),
        "reason": reason,
        "disposition": disposition,
        "checked_ns": checked_ns,
        "usage": {
            key: usage[key]
            for key in ("requests", "modeled_credits", "response_bytes", "allocated_bytes")
        },
    }
    payload = _canonical(fields)
    digest = _sha(payload)
    relative = _r2_budget_evidence_path(digest)
    writer.mkdir(f"{RELEASE_RELATIVE_ROOT}/budget-evidence")
    if _safe_file_exists(writer, relative):
        if writer.read_bytes(relative, max_bytes=64_000) != payload:
            raise ReleaseError("r2_budget_evidence_create_only_conflict")
    else:
        writer.create(relative, payload)
    return digest


def _r2_budget_evidence_map(
    writer: DescriptorWriter,
    *,
    receipt: Mapping[str, Any],
    suffix_rows: Sequence[Mapping[str, Any]],
    now_ns: int,
    allowed_epoch_ids: set[str] | None = None,
) -> dict[str, str]:
    directory = f"{RELEASE_RELATIVE_ROOT}/budget-evidence"
    try:
        names = set(writer.list_names(directory))
    except FileNotFoundError:
        return {}
    evidence: dict[str, str] = {}
    for name in names:
        if not name.endswith(".json"):
            raise ReleaseError("r2_budget_evidence_filename_invalid")
        raw, row = _read_canonical_json(writer, f"{directory}/{name}", limit=64_000)
        if (
            name != f"{_sha(raw)}.json"
            or not isinstance(row, dict)
            or set(row)
            != {
                "schema",
                "schema_version",
                "release_id",
                "epoch_id",
                "reason",
                "disposition",
                "checked_ns",
                "usage",
            }
            or row.get("schema") != "brain-senses-external-rolling-v5r2-budget-evidence"
            or row.get("schema_version") != 1
            or row.get("release_id") != RELEASE_ID
            or not isinstance(row.get("epoch_id"), str)
            or row.get("reason") not in _R2_ROLLING_LIMITS
            or row.get("disposition") not in {"exceeded", "headroom"}
            or type(row.get("checked_ns")) is not int
            or row["checked_ns"] <= 0
            or row["checked_ns"] > now_ns
        ):
            raise ReleaseError("r2_budget_evidence_invalid")
        try:
            v5_schema.validate_epoch_id(row["epoch_id"])
        except v5_schema.SchemaError as exc:
            raise ReleaseError("r2_budget_evidence_invalid") from exc
        if allowed_epoch_ids is not None and row["epoch_id"] not in allowed_epoch_ids:
            raise ReleaseError("r2_budget_evidence_epoch_unknown")
        observed = _combined_usage(
            receipt,
            suffix_rows,
            now_ns=row["checked_ns"],
            allow_rolling_exceeded=True,
        )
        usage_doc = {
            key: observed[key]
            for key in ("requests", "modeled_credits", "response_bytes", "allocated_bytes")
        }
        if row.get("usage") != usage_doc:
            raise ReleaseError("r2_budget_evidence_usage_mismatch")
        reason_now = _r2_budget_reason(observed)
        if row["disposition"] == "exceeded" and reason_now != row["reason"]:
            raise ReleaseError("r2_budget_evidence_disposition_invalid")
        if row["disposition"] == "headroom" and reason_now is not None:
            raise ReleaseError("r2_budget_evidence_disposition_invalid")
        evidence[_sha(raw)] = f"{row['disposition']}:{row['reason']}"
    return evidence


def _r2_budget_evidence_at(
    writer: DescriptorWriter, *, epoch_id: str, checked_ns: int, disposition: str
) -> tuple[str, str] | None:
    directory = f"{RELEASE_RELATIVE_ROOT}/budget-evidence"
    try:
        names = writer.list_names(directory)
    except FileNotFoundError:
        return None
    matches: list[tuple[str, str]] = []
    for name in names:
        raw, row = _read_canonical_json(writer, f"{directory}/{name}", limit=64_000)
        if (
            isinstance(row, dict)
            and name == f"{_sha(raw)}.json"
            and row.get("epoch_id") == epoch_id
            and row.get("checked_ns") == checked_ns
            and row.get("disposition") == disposition
            and row.get("reason") in _R2_ROLLING_LIMITS
        ):
            matches.append((_sha(raw), row["reason"]))
    if len(matches) > 1:
        raise ReleaseError("r2_budget_evidence_duplicate_check")
    return matches[0] if matches else None


def _r2_pending_exceeded_evidence(
    writer: DescriptorWriter, *, epoch_id: str
) -> tuple[str, str, int] | None:
    directory = f"{RELEASE_RELATIVE_ROOT}/budget-evidence"
    try:
        names = writer.list_names(directory)
    except FileNotFoundError:
        return None
    matches: list[tuple[str, str, int]] = []
    for name in names:
        raw, row = _read_canonical_json(writer, f"{directory}/{name}", limit=64_000)
        if (
            isinstance(row, dict)
            and name == f"{_sha(raw)}.json"
            and row.get("epoch_id") == epoch_id
            and row.get("disposition") == "exceeded"
            and row.get("reason") in _R2_ROLLING_LIMITS
            and type(row.get("checked_ns")) is int
        ):
            matches.append((_sha(raw), row["reason"], row["checked_ns"]))
    if len(matches) > 1:
        raise ReleaseError("r2_budget_evidence_duplicate_pending")
    return matches[0] if matches else None


def _capture_allocated_bytes(reader: DescriptorWriter, relative: str) -> int:
    rows = _read_root_inventory(reader, relative)
    return sum(row["allocated_upper_bytes"] for row in rows)


def _validate_capacity_rows(
    reader: DescriptorWriter,
    event_indexes: Mapping[str, tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]],
    reservations: Mapping[str, dict[str, Any]],
    terminals: Mapping[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_request: dict[str, dict[str, Any]] = {}
    wanted_paths: set[str] = set()
    for epoch_id, (intents, outcomes) in event_indexes.items():
        for request_id, intent_event in intents.items():
            reservation_id = f"helius-v5:{epoch_id}:{request_id}"
            reservation = reservations.get(reservation_id)
            terminal = terminals.get(reservation_id)
            if (
                reservation is None
                or terminal is None
                or reservation["service"] != "helius"
                or reservation["request_id"] != request_id
            ):
                raise ReleaseError("capacity_reservation_missing_or_duplicate")
            prefix = f"services/external-rolling-v5/epochs/{epoch_id}"
            expected_intent_path = f"{prefix}/intents/{request_id}.json"
            expected_outcome_path = f"{prefix}/outcomes/{request_id}.json"
            if (
                reservation["intent_path"] != expected_intent_path
                or reservation["outcome_path"] != expected_outcome_path
                or reservation["reserved_bytes"]
                != policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES
            ):
                raise ReleaseError("capacity_reservation_path_or_reserve_invalid")
            intent_raw, intent_doc = _read_canonical_json(
                reader, expected_intent_path, limit=64_000
            )
            outcome_raw, outcome_doc = _read_canonical_json(
                reader, expected_outcome_path, limit=64_000
            )
            event_intent = intent_event["fields"]
            if (
                _sha(intent_raw) != reservation["intent_sha256"]
                or intent_doc.get("schema") != "brain-senses-external-rolling-v5-capacity-intent"
                or intent_doc.get("schema_version") != 5
                or intent_doc.get("epoch_id") != epoch_id
                or intent_doc.get("request_id") != request_id
            ):
                raise ReleaseError("capacity_intent_binding_invalid")
            for key in (
                "request_id",
                "bucket",
                "ordinal",
                "method",
                "params",
                "wire_request_sha256",
                "decision_ns",
            ):
                if intent_doc.get(key) != event_intent.get(key):
                    raise ReleaseError("capacity_intent_event_mismatch")
            status = (
                "captured"
                if outcomes[request_id]["kind"] == "request_captured"
                else "possibly_spent"
            )
            expected_terminal_kind = "settled" if status == "captured" else "ambiguous_terminal"
            if (
                terminal["kind"] != expected_terminal_kind
                or terminal["fields"].get("outcome_path") != expected_outcome_path
                or terminal["fields"].get("outcome_sha256") != _sha(outcome_raw)
                or outcome_doc.get("request_id") != request_id
                or outcome_doc.get("status") != status
            ):
                raise ReleaseError("capacity_outcome_binding_invalid")
            if status == "captured":
                cap = capture_verify.verify_capture(
                    reader,
                    relative_directory=f"{prefix}/captures/{request_id}",
                    expected={
                        "request_id": request_id,
                        "request_sha256": semantic_request_sha256(
                            event_intent["method"], event_intent["params"]
                        ),
                        "wire_request_sha256": wire_request_sha256(
                            event_intent["method"], event_intent["params"]
                        ),
                        "decision_ns": event_intent["decision_ns"],
                    },
                )
                received = cap["receipt"]
                if (
                    outcome_doc.get("response_sha256") != cap["response_sha256"]
                    or outcome_doc.get("response_bytes") != cap["response_bytes"]
                    or outcome_doc.get("availability_ns") != received["availability_ns"]
                ):
                    raise ReleaseError("capacity_capture_binding_invalid")
            elif outcome_doc.get("failure_class") != outcomes[request_id]["fields"].get(
                "failure_class"
            ):
                raise ReleaseError("capacity_ambiguous_binding_invalid")
            wanted_paths.add(expected_intent_path)
            by_request[request_id] = {
                "reservation_id": reservation_id,
                "reservation_sha256": reservation["intent_sha256"],
                "ledger_terminal_kind": terminal["kind"],
                "ledger_terminal_sha256": terminal["event_sha256"],
                "status": status,
            }
    # All Helius v5 reservations must belong to these two epochs. This blocks
    # accidental double-debit and excludes reservations under the invalid open root.
    for _reservation_id, fields in reservations.items():
        if fields["service"] != "helius" or not fields["intent_path"].startswith(
            "services/external-rolling-v5/"
        ):
            continue
        if fields["intent_path"].startswith(
            f"services/external-rolling-v5/epochs/{INVALID_OPEN_EPOCH}/"
        ):
            raise ReleaseError("invalid_open_has_capacity_reservation")
        if fields["intent_path"] not in wanted_paths:
            raise ReleaseError("unaccounted_v5_capacity_reservation")
    if (
        len(by_request) != EXPECTED_ATTEMPTS
        or sum(row["status"] == "possibly_spent" for row in by_request.values())
        != EXPECTED_AMBIGUOUS
    ):
        raise ReleaseError("capacity_attempt_denominator_invalid")
    return by_request


def _invalid_open_proof(
    reader: DescriptorWriter,
    control_events: Sequence[dict[str, Any]],
    ledger_rows: list[dict[str, Any]],
    root_id: str,
    volume_uuid: str,
    expected_sources: Mapping[str, str],
    previous_usage: cutover_usage.VerifiedCutoverUsage,
) -> dict[str, Any]:
    base = f"services/external-rolling-v5/epochs/{INVALID_OPEN_EPOCH}"
    if reader.exists(f"{base}/terminal.json"):
        raise ReleaseError("invalid_open_has_terminal")
    plan_raw, plan = _read_canonical_json(reader, f"{base}/plan.json", limit=2_000_000)
    events_raw = reader.read_bytes(f"{base}/events.jsonl", max_bytes=MAX_BYTES)
    event_rows = v5_schema.verify_event_chain(events_raw, schema=v5_schema.EVENT_SCHEMA)
    if (
        len(event_rows) != 1
        or event_rows[0]["kind"] != "epoch_started"
        or event_rows[0]["fields"].get("epoch_id") != INVALID_OPEN_EPOCH
    ):
        raise ReleaseError("invalid_open_has_dispatch_or_mutation")
    v5_verify.verify_epoch_events(plan, events_raw)
    if plan.get("cutover_usage_sha256") != previous_usage.receipt_sha256:
        raise ReleaseError("invalid_open_usage_binding_invalid")
    planned_sources = plan.get("source_bindings")
    _source_exception(planned_sources, expected_sources)
    v5_verify.verify_plan(
        plan_raw,
        expected_sources=planned_sources,
        expected_root_id=root_id,
        expected_volume_uuid=volume_uuid,
    )
    for leaf in ("intents", "outcomes", "captures"):
        if reader.list_names(f"{base}/{leaf}"):
            raise ReleaseError("invalid_open_has_intents_captures_or_outcomes")
    open_rows = [
        e
        for e in control_events
        if e["kind"] == "epoch_open" and e["fields"].get("epoch_id") == INVALID_OPEN_EPOCH
    ]
    if len(open_rows) != 1 or control_events[-1] != open_rows[0]:
        raise ReleaseError("invalid_open_control_binding_invalid")
    fields = open_rows[0]["fields"]
    if fields.get("plan_sha256") != _sha(plan_raw) or fields.get("anchor_ns") != plan.get(
        "anchor_ns"
    ):
        raise ReleaseError("invalid_open_control_plan_mismatch")
    if any(
        e["kind"] == "reserved"
        and e["fields"].get("service") == "helius"
        and e["fields"].get("intent_path", "").startswith(f"{base}/")
        for e in ledger_rows
    ):
        raise ReleaseError("invalid_open_has_capacity_reservation")
    return {
        "classification": "invalid_open_no_dispatch",
        "epoch_id": INVALID_OPEN_EPOCH,
        "control_event_seq": open_rows[0]["seq"],
        "control_event_sha256": open_rows[0]["event_sha256"],
        "control_tail_sha256": control_events[-1]["event_sha256"],
        "plan_sha256": _sha(plan_raw),
        "event_chain_sha256": _sha(events_raw),
        "intent_count": 0,
        "outcome_count": 0,
        "capture_count": 0,
        "capacity_reservation_count": 0,
        "reason": "invalid_open_after_two_abandons",
    }


def _request_rows(
    reader: DescriptorWriter,
    epoch_ids: tuple[str, ...],
    *,
    root_id: str,
    volume_uuid: str,
    cutover_sha256: str,
) -> tuple[
    list[dict[str, Any]],
    dict[str, tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]],
    list[dict[str, Any]],
]:
    all_rows: list[dict[str, Any]] = []
    event_indexes: dict[str, tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]] = {}
    epoch_summaries: list[dict[str, Any]] = []
    for epoch_id in epoch_ids:
        base = f"services/external-rolling-v5/epochs/{epoch_id}"
        plan_raw, plan = _read_canonical_json(reader, f"{base}/plan.json", limit=2_000_000)
        if plan.get("cutover_usage_sha256") != cutover_sha256:
            raise ReleaseError("terminal_plan_usage_binding_invalid")
        planned_sources = plan.get("source_bindings")
        current_sources = v5_runtime.source_bindings()
        _source_exception(planned_sources, current_sources)
        v5_verify.verify_plan(
            plan_raw,
            expected_sources=planned_sources,
            expected_root_id=root_id,
            expected_volume_uuid=volume_uuid,
        )
        event_raw = reader.read_bytes(f"{base}/events.jsonl", max_bytes=MAX_BYTES)
        event_rows = v5_schema.verify_event_chain(event_raw, schema=v5_schema.EVENT_SCHEMA)
        projection = v5_verify.verify_epoch_events(plan, event_raw)
        if projection.terminal_status != "abandoned":
            raise ReleaseError("historical_epoch_not_abandoned")
        intents, outcomes = _read_intents_outcomes(event_rows)
        event_indexes[epoch_id] = (intents, outcomes)
        verified_captures = capture_verify.verify_epoch_captures(
            reader, epoch_relative=base, event_bytes=event_raw
        )
        _verify_sealed_frames(reader, epoch_id, plan, event_raw)
        capture_by_id = {row["receipt"]["request_id"]: row for row in verified_captures}
        capture_names = set(reader.list_names(f"{base}/captures"))
        captured_ids = {
            rid for rid, event in outcomes.items() if event["kind"] == "request_captured"
        }
        if capture_names != captured_ids:
            raise ReleaseError("capture_directory_denominator_invalid")
        terminal_raw, terminal = _read_canonical_json(reader, f"{base}/terminal.json", limit=64_000)
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
        manifest_sha, allocation_upper, object_count, manifest_rows = _allocation_manifest(
            reader, epoch_id
        )
        if (
            set(terminal) != required
            or terminal.get("schema") != v5_schema.TERMINAL_SCHEMA
            or terminal.get("schema_version") != 5
            or terminal.get("epoch_id") != epoch_id
            or terminal.get("plan_sha256") != _sha(plan_raw)
            or terminal.get("event_tail_sha256") != projection.event_tail_sha256
            or terminal.get("status") != "abandoned"
            or terminal.get("allocation_manifest_sha256") != manifest_sha
            or terminal.get("allocation_upper_bytes") != allocation_upper
            or terminal.get("allocation_object_count") != object_count
        ):
            raise ReleaseError("terminal_manifest_binding_invalid")
        terminal_events = [e for e in event_rows if e["kind"] == "epoch_terminal"]
        if len(terminal_events) != 1 or terminal_events[0]["at_ns"] != terminal.get("sealed_ns"):
            raise ReleaseError("terminal_time_binding_invalid")
        allocation_by_capture: dict[str, int] = Counter()
        for row in manifest_rows:
            parts = row["path"].split("/")
            if len(parts) >= 3 and parts[0] == "captures":
                allocation_by_capture[parts[1]] += row["allocated_upper_bytes"]
        for request_id, intent_event in intents.items():
            intent_fields = intent_event["fields"]
            outcome_event = outcomes[request_id]
            status = "captured" if outcome_event["kind"] == "request_captured" else "possibly_spent"
            if status == "captured":
                receipt = capture_by_id[request_id]["receipt"]
                dispatch_ns = receipt["request_started_ns"]
                availability_ns = receipt["availability_ns"]
                response_bytes = receipt["response_bytes"]
                response_sha256 = receipt["response_sha256"]
                physical_bytes = allocation_by_capture[request_id]
            else:
                dispatch_ns = None
                availability_ns = None
                response_bytes = policy.MAX_RESPONSE_BYTES
                response_sha256 = None
                physical_bytes = (
                    policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES
                )
            decision_ns = intent_fields.get("decision_ns")
            if (
                type(decision_ns) is not int
                or decision_ns > intent_event["at_ns"]
                or (dispatch_ns is not None and dispatch_ns < decision_ns)
                or (availability_ns is not None and availability_ns < dispatch_ns)
            ):
                raise ReleaseError("request_temporal_order_invalid")
            all_rows.append(
                {
                    "epoch_id": epoch_id,
                    "request_id": request_id,
                    "status": status,
                    "request_debit": 1,
                    "modeled_credit_debit": policy.CONSERVATIVE_CREDITS_PER_REQUEST,
                    "response_bytes_debit": response_bytes,
                    "physical_bytes_debit": physical_bytes,
                    "decision_ns": decision_ns,
                    "intent_event_ns": intent_event["at_ns"],
                    "dispatch_ns": dispatch_ns,
                    "availability_ns": availability_ns,
                    "outcome_event_ns": outcome_event["at_ns"],
                    "intent_event_sha256": intent_event["event_sha256"],
                    "outcome_event_sha256": outcome_event["event_sha256"],
                    "response_sha256": response_sha256,
                }
            )
        epoch_summaries.append(
            {
                "epoch_id": epoch_id,
                "plan_sha256": _sha(plan_raw),
                "event_chain_sha256": _sha(event_raw),
                "terminal_sha256": _sha(terminal_raw),
                "event_tail_sha256": projection.event_tail_sha256,
                "allocation_manifest_sha256": manifest_sha,
                "allocation_upper_bytes": allocation_upper,
                "allocation_object_count": object_count,
                "request_count": len(intents),
                "captured_count": len(captured_ids),
                "possibly_spent_count": len(intents) - len(captured_ids),
                "terminal_status": terminal["status"],
                "sealed_ns": terminal["sealed_ns"],
            }
        )
    if (
        len(all_rows) != EXPECTED_ATTEMPTS
        or sum(row["status"] == "possibly_spent" for row in all_rows) != EXPECTED_AMBIGUOUS
    ):
        raise ReleaseError("v5_attempt_denominator_invalid")
    return (
        sorted(all_rows, key=lambda row: (row["decision_ns"], row["epoch_id"], row["request_id"])),
        event_indexes,
        epoch_summaries,
    )


def build_cutover_receipt(
    reader: DescriptorWriter,
    *,
    root_id: str,
    volume_uuid: str,
    generated_ns: int,
    now_ns: int,
    current_sources: Mapping[str, str] | None = None,
) -> bytes:
    """Replay the exact v5 cutover boundary into immutable candidate receipt bytes."""
    if (
        type(generated_ns) is not int
        or generated_ns <= 0
        or type(now_ns) is not int
        or now_ns <= 0
        or generated_ns > now_ns
    ):
        raise ReleaseError("cutover_clock_invalid")
    current_sources = dict(current_sources or source_bindings())
    if set(current_sources) != set(SOURCE_PATHS) or any(
        not isinstance(value, str) or len(value) != 64 for value in current_sources.values()
    ):
        raise ReleaseError("release_source_binding_invalid")
    service = v5_schema.SERVICE_RELATIVE_ROOT
    root_names = reader.list_names(f"{service}/epochs")
    if set(root_names) != set((*EXPECTED_V5_EPOCHS, INVALID_OPEN_EPOCH)):
        raise ReleaseError("v5_epoch_inventory_changed")
    previous_raw = reader.read_bytes(
        f"{service}/{v5_schema.CUTOVER_USAGE_FILE}", max_bytes=4_000_000
    )
    previous = cutover_usage.verify_receipt(
        previous_raw,
        expected_sha256=_sha(previous_raw),
        expected_v4_sources=__import__(
            "tools.brain_senses_external_orchestrator_v4", fromlist=["source_bindings"]
        ).source_bindings(),
        expected_cutover_exception_sha256=_sha(
            reader.read_bytes(f"{service}/{v5_schema.CUTOVER_EXCEPTION_FILE}", max_bytes=1_000_000)
        ),
    )
    previous_raw_policy = reader.read_bytes(f"{service}/{v5_schema.POLICY_FILE}", max_bytes=64_000)
    if previous_raw_policy != _canonical(policy.policy_document()):
        raise ReleaseError("v5_policy_changed")
    activation_cap_path = ACTIVATION_CAP_PATH
    activation_cap_raw, activation_cap = _read_canonical_json(
        reader, activation_cap_path, limit=64_000
    )
    if (
        not isinstance(activation_cap, dict)
        or set(activation_cap)
        != {
            "schema",
            "schema_version",
            "created_ns",
            "root_id",
            "volume_uuid",
            "available_at_activation_bytes",
            "service_allocation_cap_bytes",
        }
        or activation_cap.get("schema") != "brain-senses-external-rolling-v4-allocation-cap"
        or activation_cap.get("schema_version") != 4
        or activation_cap.get("root_id") != root_id
        or activation_cap.get("volume_uuid") != volume_uuid.lower()
        or type(activation_cap.get("created_ns")) is not int
        or not 0 < activation_cap["created_ns"] <= generated_ns
        or type(activation_cap.get("available_at_activation_bytes")) is not int
        or activation_cap["available_at_activation_bytes"] <= 0
        or activation_cap.get("service_allocation_cap_bytes")
        != v4_policy.activation_allocation_cap(activation_cap["available_at_activation_bytes"])
    ):
        raise ReleaseError("activation_bound_lifetime_cap_invalid")
    attempt_rows, event_indexes, epoch_summaries = _request_rows(
        reader,
        EXPECTED_V5_EPOCHS,
        root_id=root_id,
        volume_uuid=volume_uuid,
        cutover_sha256=previous.receipt_sha256,
    )
    policy_raw, ledger_raw, ledger_events, reservations, terminals = _ledger_rows(
        reader, root_id=root_id, volume_uuid=volume_uuid
    )
    capacity_rows = _validate_capacity_rows(reader, event_indexes, reservations, terminals)
    control_raw = reader.read_bytes(f"{service}/{v5_schema.CONTROL_FILE}", max_bytes=1_000_000)
    control_events = v5_schema.verify_event_chain(control_raw, schema=v5_schema.CONTROL_SCHEMA)
    # Verify the terminal prefix using the unmodified v5 verifier. The invalid
    # final open is intentionally isolated below and is never fed to this chain.
    prefix_events = [
        event
        for event in control_events
        if event["kind"] != "epoch_open" or event["fields"].get("epoch_id") != INVALID_OPEN_EPOCH
    ]
    prefix_raw = b"".join(_canonical(event) for event in prefix_events)
    if len(prefix_events) != len(control_events) - 1 or not prefix_events:
        raise ReleaseError("invalid_open_prefix_shape")
    plans: dict[str, bytes] = {}
    terminal_objects: dict[str, v5_verify.VerifiedTerminal] = {}
    for summary in epoch_summaries:
        epoch_id = summary["epoch_id"]
        base = f"{service}/epochs/{epoch_id}"
        plan_raw = reader.read_bytes(f"{base}/plan.json", max_bytes=2_000_000)
        plan = v5_schema.decode(plan_raw)
        terminal_raw = reader.read_bytes(f"{base}/terminal.json", max_bytes=64_000)
        v5_verify.verify_epoch_events(
            plan, reader.read_bytes(f"{base}/events.jsonl", max_bytes=MAX_BYTES)
        )
        plans[epoch_id] = plan_raw
        terminal_objects[epoch_id] = v5_verify.VerifiedTerminal(
            _sha(terminal_raw), summary["event_tail_sha256"], "abandoned"
        )
    current_v5_sources = v5_runtime.source_bindings()

    def validate_plan(raw_plan: bytes, epoch_id: str) -> Mapping[str, Any]:
        doc = v5_schema.decode(raw_plan)
        sources = doc.get("source_bindings")
        _source_exception(sources, current_v5_sources)
        return v5_verify.verify_plan(
            raw_plan,
            expected_sources=sources,
            expected_root_id=root_id,
            expected_volume_uuid=volume_uuid,
        )

    control_projection = v5_verify.verify_control_chain(
        prefix_raw,
        plans=plans,
        terminals=terminal_objects,
        budget_evidence={},
        plan_validator=validate_plan,
    )
    if (
        control_projection.active_epoch_id is not None
        or control_projection.pending_terminal_epoch_id is not None
        or len(control_projection.epoch_ids) != 2
    ):
        raise ReleaseError("terminal_control_prefix_invalid")
    invalid_open = _invalid_open_proof(
        reader, control_events, ledger_events, root_id, volume_uuid, current_v5_sources, previous
    )
    abandon_times = [event["at_ns"] for event in prefix_events if event["kind"] == "epoch_abandon"]
    if len(abandon_times) != 2:
        raise ReleaseError("abandon_denominator_invalid")
    earliest = max(
        abandon_times[0] + policy.DAY_NS,
        max(
            event["fields"]["cooldown_until_ns"]
            for event in prefix_events
            if event["kind"] == "epoch_abandon"
        ),
    )
    anchor_ns = policy.next_fresh_anchor_ns(earliest)
    if anchor_ns != 1_791_918_000_000_000_000:  # 2026-10-13T19:00:00Z
        raise ReleaseError("future_anchor_unexpected")
    usage = previous.usage(now_ns=generated_ns)
    total_requests = usage.requests + len(attempt_rows)
    total_credits = (
        usage.modeled_credits + len(attempt_rows) * policy.CONSERVATIVE_CREDITS_PER_REQUEST
    )
    added_response = sum(row["response_bytes_debit"] for row in attempt_rows)
    added_physical = sum(row["physical_bytes_debit"] for row in attempt_rows)
    # The terminal manifests include epoch metadata and all exact captures;
    # include the no-dispatch open root's small metadata allocation separately.
    epoch_allocation = sum(row["allocation_upper_bytes"] for row in epoch_summaries)
    open_inventory = _read_root_inventory(reader, f"{service}/epochs/{INVALID_OPEN_EPOCH}")
    open_allocation = sum(
        row["allocated_upper_bytes"] for row in open_inventory if row["path"] != "."
    )
    total_response = usage.response_bytes + added_response
    total_physical = usage.allocated_bytes + epoch_allocation + open_allocation
    lifetime_requests = (
        sum(row["request_debit"] for row in previous.legacy_campaigns)
        + sum(row["request_debit"] for row in previous.request_rows)
        + len(attempt_rows)
    )
    lifetime_response = (
        sum(row["response_bytes_upper"] for row in previous.legacy_campaigns)
        + sum(row["response_bytes_debit"] for row in previous.request_rows)
        + added_response
    )
    lifetime_physical = (
        sum(row["physical_bytes_upper"] for row in previous.legacy_campaigns)
        + sum(row["physical_bytes_debit"] for row in previous.request_rows)
        + added_physical
    )
    activation_bound_cap = activation_cap["service_allocation_cap_bytes"]
    root_free = os.fstatvfs(reader.root_fd).f_bavail * os.fstatvfs(reader.root_fd).f_frsize
    next_request_reserve = policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES
    physical_gates = {
        "rolling_physical_with_next_epoch": total_physical
        + policy.NEXT_EPOCH_ALLOCATION_RESERVE_BYTES
        <= policy.MAX_ROLLING_PHYSICAL_BYTES,
        "service_allocation_cap_with_next_epoch": total_physical
        + policy.NEXT_EPOCH_ALLOCATION_RESERVE_BYTES
        <= policy.HELIUS_RESIDENT_ALLOCATION_CAP_BYTES,
        "free_space_reserve": root_free
        >= policy.DISK_FLOOR_BYTES
        + policy.SERVICE_OPERATING_RESERVE_BYTES
        + policy.NEXT_EPOCH_ALLOCATION_RESERVE_BYTES
        + policy.SERVICE_PEAK_METADATA_RESERVE_BYTES
        + policy.PEAK_WRITE_MARGIN_BYTES
        + next_request_reserve,
    }
    activation_check_ns = anchor_ns - 2 * policy.MINUTE_NS
    gates = {
        "rolling_requests": total_requests < policy.MAX_ROLLING_REQUESTS,
        "rolling_credits_after_one_request": total_credits + policy.CONSERVATIVE_CREDITS_PER_REQUEST
        <= policy.MAX_ROLLING_CREDITS,
        "rolling_response_after_one_reservation": total_response + policy.MAX_RESPONSE_BYTES
        <= policy.MAX_ROLLING_RESPONSE_BYTES,
        "lifetime_usage_fully_accounted": previous.rolling_v4_exact
        and previous.legacy_possible_same_account
        and lifetime_requests
        == sum(row["request_debit"] for row in previous.legacy_campaigns)
        + sum(row["request_debit"] for row in previous.request_rows)
        + len(attempt_rows),
        "activation_bound_lifetime_capacity": lifetime_physical <= activation_bound_cap,
        **physical_gates,
        "two_abandon_recovery_after_first_expires": len(
            [
                at
                for at in abandon_times
                if activation_check_ns - policy.DAY_NS < at <= activation_check_ns
            ]
        )
        < 2,
        "no_dispatch_before_anchor": now_ns + 2 * policy.MINUTE_NS <= anchor_ns,
    }
    if not all(gates.values()):
        raise ReleaseError("release_policy_gate_blocked")
    receipt = {
        "schema": RELEASE_SCHEMA,
        "schema_version": RELEASE_VERSION,
        "release_id": RELEASE_ID,
        "release_root": RELEASE_RELATIVE_ROOT,
        "generated_ns": generated_ns,
        "activation": "none",
        "provider_io": "none",
        "source_bindings": dict(sorted(current_sources.items())),
        "source_bindings_sha256": _sha(_canonical(dict(sorted(current_sources.items())))),
        "previous_usage": {
            "path": f"{service}/{v5_schema.CUTOVER_USAGE_FILE}",
            "sha256": _sha(previous_raw),
            "requests": usage.requests,
            "modeled_credits": usage.modeled_credits,
            "response_bytes_upper": usage.response_bytes,
            "physical_bytes_upper": usage.allocated_bytes,
            "possible_same_account": previous.legacy_possible_same_account,
        },
        "inherited_usage_rows": {
            "legacy_campaigns": [dict(row) for row in previous.legacy_campaigns],
            "rolling_v4_request_rows": [dict(row) for row in previous.request_rows],
        },
        "v5_terminal_epochs": epoch_summaries,
        "attempt_count": len(attempt_rows),
        "captured_count": sum(row["status"] == "captured" for row in attempt_rows),
        "possibly_spent_count": sum(row["status"] == "possibly_spent" for row in attempt_rows),
        "attempt_rows": [dict(row, **capacity_rows[row["request_id"]]) for row in attempt_rows],
        "control": {
            "path": f"{service}/{v5_schema.CONTROL_FILE}",
            "sha256": _sha(control_raw),
            "valid_terminal_prefix_sha256": _sha(prefix_raw),
            "abandon_event_ns": abandon_times,
            "abandon_event_sha256": [
                event["event_sha256"] for event in prefix_events if event["kind"] == "epoch_abandon"
            ],
        },
        "invalid_open_exception": invalid_open,
        "shared_capacity": {
            "policy_path": capacity.POLICY_PATH,
            "policy_sha256": _sha(policy_raw),
            "ledger_path": capacity.LEDGER_PATH,
            "ledger_sha256": _sha(ledger_raw),
            "ledger_event_count": len(ledger_events),
            "helius_attempt_reservations": EXPECTED_ATTEMPTS,
            "invalid_open_reservations": 0,
            "nested_r2_accounting": "once_via_existing_external_rolling_v5_allocation_root",
        },
        "activation_bound_lifetime_cap": {
            "path": activation_cap_path,
            "sha256": _sha(activation_cap_raw),
            "available_at_activation_bytes": activation_cap["available_at_activation_bytes"],
            "service_allocation_cap_bytes": activation_bound_cap,
        },
        "seed_usage": {
            "requests": total_requests,
            "modeled_credits": total_credits,
            "response_bytes_upper": total_response,
            "physical_bytes_upper": total_physical,
            "v5_added_response_bytes_upper": added_response,
            "v5_added_physical_bytes_upper": added_physical,
            "v5_epoch_allocation_upper_bytes": epoch_allocation,
            "invalid_open_allocation_upper_bytes": open_allocation,
            "physical_free_bytes": root_free,
        },
        "lifetime_usage": {
            "requests": lifetime_requests,
            "modeled_credits": lifetime_requests * policy.CONSERVATIVE_CREDITS_PER_REQUEST,
            "response_bytes_upper": lifetime_response,
            "physical_bytes_upper": lifetime_physical,
            "source": "verified_v4_cutover_receipt_plus_exact_v5_terminal_attempt_rows_once",
            "provider_charge_units": None,
            "provider_charge_status": "unknown_unverified",
        },
        "gates": gates,
        "earliest_fresh_anchor_ns": anchor_ns,
        "earliest_fresh_anchor_utc": "2026-10-13T19:00:00Z",
    }
    result = _canonical(receipt)
    verify_cutover_receipt(result, expected_sources=current_sources)
    return result


def verify_cutover_receipt(raw: bytes, *, expected_sources: Mapping[str, str]) -> dict[str, Any]:
    """Verify the immutable candidate's shape, source binding, and closed counts."""
    try:
        value = v5_schema.decode(raw)
    except v5_schema.SchemaError as exc:
        raise ReleaseError("receipt_json_invalid") from exc
    if (
        not isinstance(value, dict)
        or _canonical(value) != raw
        or value.get("schema") != RELEASE_SCHEMA
        or value.get("schema_version") != RELEASE_VERSION
        or value.get("release_id") != RELEASE_ID
        or value.get("release_root") != RELEASE_RELATIVE_ROOT
        or value.get("activation") != "none"
        or value.get("provider_io") != "none"
        or type(value.get("generated_ns")) is not int
        or value["generated_ns"] <= 0
    ):
        raise ReleaseError("receipt_identity_invalid")
    sources = value.get("source_bindings")
    if sources != dict(sorted(expected_sources.items())) or value.get(
        "source_bindings_sha256"
    ) != _sha(_canonical(sources)):
        raise ReleaseError("release_source_drift")
    previous = value.get("previous_usage")
    lifetime = value.get("lifetime_usage")
    if (
        not isinstance(previous, dict)
        or set(previous)
        != {
            "path",
            "sha256",
            "requests",
            "modeled_credits",
            "response_bytes_upper",
            "physical_bytes_upper",
            "possible_same_account",
        }
        or previous.get("path") != "services/external-rolling-v5/cutover-usage-v1.json"
        or previous.get("possible_same_account") is not True
    ):
        raise ReleaseError("previous_usage_binding_invalid")
    rows = value.get("attempt_rows")
    if (
        not isinstance(rows, list)
        or len(rows) != EXPECTED_ATTEMPTS
        or value.get("attempt_count") != EXPECTED_ATTEMPTS
        or value.get("captured_count") != EXPECTED_ATTEMPTS - EXPECTED_AMBIGUOUS
        or value.get("possibly_spent_count") != EXPECTED_AMBIGUOUS
    ):
        raise ReleaseError("receipt_attempt_denominator_invalid")
    ids = [row.get("request_id") for row in rows if isinstance(row, dict)]
    if (
        len(ids) != len(rows)
        or len(set(ids)) != EXPECTED_ATTEMPTS
        or sum(row.get("status") == "possibly_spent" for row in rows) != EXPECTED_AMBIGUOUS
    ):
        raise ReleaseError("receipt_attempt_rows_invalid")
    if any(
        row.get("request_debit") != 1
        or row.get("modeled_credit_debit") != 10
        or (
            row.get("status") == "possibly_spent"
            and (
                row.get("response_bytes_debit") != policy.MAX_RESPONSE_BYTES
                or row.get("physical_bytes_debit")
                != policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES
            )
        )
        for row in rows
    ):
        raise ReleaseError("receipt_request_debit_invalid")
    if any(
        row.get("status") not in {"captured", "possibly_spent"}
        or type(row.get("response_bytes_debit")) is not int
        or row["response_bytes_debit"] < 0
        or row["response_bytes_debit"] > policy.MAX_RESPONSE_BYTES
        or type(row.get("physical_bytes_debit")) is not int
        or row["physical_bytes_debit"] <= 0
        for row in rows
    ):
        raise ReleaseError("receipt_attempt_bounds_invalid")
    for field in ("requests", "modeled_credits", "response_bytes_upper", "physical_bytes_upper"):
        if type(previous.get(field)) is not int or previous[field] < 0:
            raise ReleaseError("previous_usage_value_invalid")
    if (
        previous["modeled_credits"]
        != previous["requests"] * policy.CONSERVATIVE_CREDITS_PER_REQUEST
    ):
        raise ReleaseError("previous_usage_credit_binding_invalid")
    inherited = value.get("inherited_usage_rows")
    if (
        not isinstance(inherited, dict)
        or set(inherited) != {"legacy_campaigns", "rolling_v4_request_rows"}
        or not isinstance(inherited.get("legacy_campaigns"), list)
        or not isinstance(inherited.get("rolling_v4_request_rows"), list)
    ):
        raise ReleaseError("inherited_usage_rows_invalid")
    generated_ns = value.get("generated_ns")
    if type(generated_ns) is not int or generated_ns <= 0:
        raise ReleaseError("receipt_generated_time_invalid")
    cutoff = generated_ns - policy.ROLLING_WINDOW_NS
    inherited_requests = inherited_credits = inherited_response = inherited_physical = 0
    for campaign in inherited["legacy_campaigns"]:
        if not isinstance(campaign, dict) or set(campaign) != {
            "campaign_id",
            "dispatch_start_ns",
            "dispatch_end_ns",
            "request_debit",
            "response_bytes_upper",
            "physical_bytes_upper",
        }:
            raise ReleaseError("inherited_campaign_shape_invalid")
        values = (
            campaign["dispatch_start_ns"],
            campaign["dispatch_end_ns"],
            campaign["request_debit"],
            campaign["response_bytes_upper"],
            campaign["physical_bytes_upper"],
        )
        if any(type(number) is not int or number < 0 for number in values):
            raise ReleaseError("inherited_campaign_value_invalid")
        if campaign["dispatch_start_ns"] > campaign["dispatch_end_ns"]:
            raise ReleaseError("inherited_campaign_time_invalid")
        if campaign["dispatch_start_ns"] <= generated_ns and campaign["dispatch_end_ns"] >= cutoff:
            count = campaign["request_debit"]
            inherited_requests += count
            inherited_credits += count * policy.CONSERVATIVE_CREDITS_PER_REQUEST
            inherited_response += campaign["response_bytes_upper"]
            inherited_physical += campaign["physical_bytes_upper"]
    for row in inherited["rolling_v4_request_rows"]:
        if not isinstance(row, dict) or set(row) != {
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
        }:
            raise ReleaseError("inherited_request_row_shape_invalid")
        dispatch = row["dispatch_ns"]
        latest = row["latest_possible_dispatch_ns"]
        if type(latest) is not int or latest < row["decision_ns"]:
            raise ReleaseError("inherited_request_row_time_invalid")
        include = latest >= cutoff if dispatch is None else cutoff <= dispatch <= generated_ns
        if include:
            for field, name in (
                ("request_debit", "requests"),
                ("modeled_credit_debit", "credits"),
                ("response_bytes_debit", "response"),
                ("physical_bytes_debit", "physical"),
            ):
                number = row[field]
                if type(number) is not int or number < 0:
                    raise ReleaseError("inherited_request_row_debit_invalid")
                if name == "requests":
                    inherited_requests += number
                elif name == "credits":
                    inherited_credits += number
                elif name == "response":
                    inherited_response += number
                else:
                    inherited_physical += number
    if (
        inherited_requests != previous["requests"]
        or inherited_credits != previous["modeled_credits"]
        or inherited_response != previous["response_bytes_upper"]
        or inherited_physical != previous["physical_bytes_upper"]
    ):
        raise ReleaseError("inherited_usage_aggregate_mismatch")
    if (
        not isinstance(lifetime, dict)
        or set(lifetime)
        != {
            "requests",
            "modeled_credits",
            "response_bytes_upper",
            "physical_bytes_upper",
            "source",
            "provider_charge_units",
            "provider_charge_status",
        }
        or lifetime.get("source")
        != "verified_v4_cutover_receipt_plus_exact_v5_terminal_attempt_rows_once"
        or lifetime.get("provider_charge_units") is not None
        or lifetime.get("provider_charge_status") != "unknown_unverified"
    ):
        raise ReleaseError("lifetime_usage_shape_invalid")
    expected_lifetime = {
        "requests": previous["requests"] + EXPECTED_ATTEMPTS,
        "modeled_credits": previous["modeled_credits"]
        + EXPECTED_ATTEMPTS * policy.CONSERVATIVE_CREDITS_PER_REQUEST,
        "response_bytes_upper": previous["response_bytes_upper"]
        + sum(row["response_bytes_debit"] for row in rows),
        "physical_bytes_upper": previous["physical_bytes_upper"]
        + sum(row["physical_bytes_debit"] for row in rows),
    }
    if any(lifetime.get(field) != expected for field, expected in expected_lifetime.items()):
        raise ReleaseError("lifetime_usage_double_debit_or_omission")
    cap_binding = value.get("activation_bound_lifetime_cap")
    if (
        not isinstance(cap_binding, dict)
        or set(cap_binding)
        != {
            "path",
            "sha256",
            "available_at_activation_bytes",
            "service_allocation_cap_bytes",
        }
        or cap_binding.get("path") != ACTIVATION_CAP_PATH
        or not isinstance(cap_binding.get("sha256"), str)
        or len(cap_binding["sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in cap_binding["sha256"])
        or type(cap_binding.get("available_at_activation_bytes")) is not int
        or type(cap_binding.get("service_allocation_cap_bytes")) is not int
        or cap_binding["service_allocation_cap_bytes"]
        != v4_policy.activation_allocation_cap(cap_binding["available_at_activation_bytes"])
        or lifetime["physical_bytes_upper"] > cap_binding["service_allocation_cap_bytes"]
    ):
        raise ReleaseError("activation_bound_lifetime_cap_binding_invalid")
    invalid = value.get("invalid_open_exception")
    if (
        not isinstance(invalid, dict)
        or invalid.get("classification") != "invalid_open_no_dispatch"
        or invalid.get("epoch_id") != INVALID_OPEN_EPOCH
        or any(
            invalid.get(name) != 0
            for name in (
                "intent_count",
                "outcome_count",
                "capture_count",
                "capacity_reservation_count",
            )
        )
    ):
        raise ReleaseError("invalid_open_exception_altered")
    shared = value.get("shared_capacity")
    if (
        not isinstance(shared, dict)
        or shared.get("policy_path") != capacity.POLICY_PATH
        or shared.get("ledger_path") != capacity.LEDGER_PATH
        or shared.get("helius_attempt_reservations") != EXPECTED_ATTEMPTS
        or shared.get("invalid_open_reservations") != 0
        or shared.get("nested_r2_accounting")
        != "once_via_existing_external_rolling_v5_allocation_root"
        or type(shared.get("ledger_event_count")) is not int
        or shared["ledger_event_count"] < 1
        or not isinstance(shared.get("ledger_sha256"), str)
        or len(shared["ledger_sha256"]) != 64
        or not isinstance(shared.get("policy_sha256"), str)
        or len(shared["policy_sha256"]) != 64
    ):
        raise ReleaseError("shared_capacity_prefix_binding_invalid")
    expected_gates = {
        "rolling_requests",
        "rolling_credits_after_one_request",
        "rolling_response_after_one_reservation",
        "lifetime_usage_fully_accounted",
        "activation_bound_lifetime_capacity",
        "rolling_physical_with_next_epoch",
        "service_allocation_cap_with_next_epoch",
        "free_space_reserve",
        "two_abandon_recovery_after_first_expires",
        "no_dispatch_before_anchor",
    }
    if (
        value.get("earliest_fresh_anchor_ns") != 1_791_918_000_000_000_000
        or value.get("earliest_fresh_anchor_utc") != "2026-10-13T19:00:00Z"
        or not isinstance(value.get("gates"), dict)
        or set(value["gates"]) != expected_gates
        or not all(type(x) is bool and x for x in value["gates"].values())
    ):
        raise ReleaseError("receipt_policy_gate_invalid")
    if value["gates"]["activation_bound_lifetime_capacity"] is not (
        lifetime["physical_bytes_upper"] <= cap_binding["service_allocation_cap_bytes"]
    ):
        raise ReleaseError("activation_bound_lifetime_gate_mismatch")
    return value


def _verify_activation_cap_file(
    reader: DescriptorWriter,
    receipt: Mapping[str, Any],
    *,
    root_id: str,
    volume_uuid: str,
) -> None:
    """Reopen the immutable v4 activation cap and compare its exact source bytes."""
    binding = receipt.get("activation_bound_lifetime_cap")
    if not isinstance(binding, dict):
        raise ReleaseError("activation_cap_binding_missing")
    raw, value = _read_canonical_json(reader, ACTIVATION_CAP_PATH, limit=64_000)
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "schema_version",
            "created_ns",
            "root_id",
            "volume_uuid",
            "available_at_activation_bytes",
            "service_allocation_cap_bytes",
        }
        or value.get("schema") != "brain-senses-external-rolling-v4-allocation-cap"
        or value.get("schema_version") != 4
        or value.get("root_id") != root_id
        or value.get("volume_uuid") != volume_uuid.lower()
        or type(value.get("created_ns")) is not int
        or value["created_ns"] <= 0
        or value["created_ns"] > receipt.get("generated_ns", 0)
        or type(value.get("available_at_activation_bytes")) is not int
        or type(value.get("service_allocation_cap_bytes")) is not int
        or value["service_allocation_cap_bytes"]
        != v4_policy.activation_allocation_cap(value["available_at_activation_bytes"])
        or binding.get("path") != ACTIVATION_CAP_PATH
        or binding.get("sha256") != _sha(raw)
        or binding.get("available_at_activation_bytes") != value["available_at_activation_bytes"]
        or binding.get("service_allocation_cap_bytes") != value["service_allocation_cap_bytes"]
    ):
        raise ReleaseError("activation_cap_source_changed")


def build_release_manifest(
    *,
    receipt_bytes: bytes,
    root_id: str,
    volume_uuid: str,
    expected_sources: Mapping[str, str],
) -> bytes:
    """Build a source-, storage-, and receipt-bound nested release manifest."""
    if not isinstance(root_id, str) or not root_id or not isinstance(volume_uuid, str):
        raise ReleaseError("release_storage_binding_invalid")
    try:
        import uuid

        normalized_uuid = str(uuid.UUID(volume_uuid)).lower()
    except (ValueError, AttributeError) as exc:
        raise ReleaseError("release_storage_binding_invalid") from exc
    verified = verify_cutover_receipt(receipt_bytes, expected_sources=expected_sources)
    sources = dict(sorted(expected_sources.items()))
    if verified["source_bindings"] != sources:
        raise ReleaseError("release_source_drift")
    manifest = {
        "schema": RELEASE_MANIFEST_SCHEMA,
        "schema_version": RELEASE_MANIFEST_VERSION,
        "release_id": RELEASE_ID,
        "release_root": RELEASE_RELATIVE_ROOT,
        "storage": {"root_id": root_id, "volume_uuid": normalized_uuid},
        "receipt": {
            "path": RECEIPT_RELATIVE_PATH,
            "sha256": _sha(receipt_bytes),
            "schema": RELEASE_SCHEMA,
            "schema_version": RELEASE_VERSION,
            "attempt_count": verified["attempt_count"],
            "possibly_spent_count": verified["possibly_spent_count"],
        },
        "source_bindings": sources,
        "source_bindings_sha256": _sha(_canonical(sources)),
        "plist_path": SOURCE_PATHS[-1],
        "plist_sha256": sources[SOURCE_PATHS[-1]],
        "activation": "none",
        "provider_io": "none",
    }
    return _canonical(manifest)


def verify_release_manifest(
    writer: DescriptorWriter,
    *,
    root_id: str,
    volume_uuid: str,
    expected_sources: Mapping[str, str],
) -> tuple[dict[str, Any], bytes]:
    """Reopen the nested receipt and verify exact source/storage bindings."""
    raw_manifest, manifest = _read_canonical_json(writer, MANIFEST_RELATIVE_PATH, limit=256_000)
    receipt_raw = writer.read_bytes(RECEIPT_RELATIVE_PATH, max_bytes=4_000_000)
    expected_manifest = build_release_manifest(
        receipt_bytes=receipt_raw,
        root_id=root_id,
        volume_uuid=volume_uuid,
        expected_sources=expected_sources,
    )
    if raw_manifest != expected_manifest or manifest != v5_schema.decode(expected_manifest):
        raise ReleaseError("release_manifest_binding_invalid")
    verified_receipt = verify_cutover_receipt(receipt_raw, expected_sources=expected_sources)
    _verify_activation_cap_file(writer, verified_receipt, root_id=root_id, volume_uuid=volume_uuid)
    return manifest, receipt_raw


def publish_cutover_release(
    writer: DescriptorWriter,
    *,
    receipt_bytes: bytes,
    root_id: str,
    volume_uuid: str,
    expected_sources: Mapping[str, str],
) -> str:
    """Create-only publish the verified receipt, then its manifest.

    Replaying identical bytes is idempotent. A conflicting existing artifact or
    a manifest without its receipt fails closed. All writes go through the
    caller's descriptor-bound writer and are fsynced by ``create``.
    """
    verified_receipt = verify_cutover_receipt(receipt_bytes, expected_sources=expected_sources)
    _verify_activation_cap_file(writer, verified_receipt, root_id=root_id, volume_uuid=volume_uuid)
    manifest_bytes = build_release_manifest(
        receipt_bytes=receipt_bytes,
        root_id=root_id,
        volume_uuid=volume_uuid,
        expected_sources=expected_sources,
    )
    writer.mkdir(RELEASE_RELATIVE_ROOT)
    receipt_exists = _safe_file_exists(writer, RECEIPT_RELATIVE_PATH)
    manifest_exists = _safe_file_exists(writer, MANIFEST_RELATIVE_PATH)
    if manifest_exists and not receipt_exists:
        raise ReleaseError("release_manifest_without_receipt")
    if receipt_exists:
        if writer.read_bytes(RECEIPT_RELATIVE_PATH, max_bytes=4_000_000) != receipt_bytes:
            raise ReleaseError("cutover_receipt_create_only_conflict")
    else:
        writer.create(RECEIPT_RELATIVE_PATH, receipt_bytes)
    if writer.read_bytes(RECEIPT_RELATIVE_PATH, max_bytes=4_000_000) != receipt_bytes:
        raise ReleaseError("cutover_receipt_postwrite_mismatch")
    if manifest_exists:
        if writer.read_bytes(MANIFEST_RELATIVE_PATH, max_bytes=256_000) != manifest_bytes:
            raise ReleaseError("release_manifest_create_only_conflict")
    else:
        writer.create(MANIFEST_RELATIVE_PATH, manifest_bytes)
    verify_release_manifest(
        writer,
        root_id=root_id,
        volume_uuid=volume_uuid,
        expected_sources=expected_sources,
    )
    return "existing_identical" if receipt_exists and manifest_exists else "created"


def _safe_file_exists(writer: DescriptorWriter, relative: str) -> bool:
    try:
        info = writer.stat(relative)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode):
        raise ReleaseError("release_artifact_not_regular")
    return True


def build_plan(
    *,
    created_ns: int,
    anchor_ns: int,
    root_id: str,
    volume_uuid: str,
    cutover_sha256: str,
    sources: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Create a v5r2-specific plan under the nested release root."""
    source_map = dict(sources or source_bindings())
    if (
        anchor_ns < created_ns + 2 * policy.MINUTE_NS
        or anchor_ns < MIN_R2_ANCHOR_NS
        or anchor_ns % policy.HOUR_NS
    ):
        raise ReleaseError("future_anchor_invalid")
    epoch = v5_schema.epoch_id(
        anchor_ns, hashlib.sha256(f"{RELEASE_ID}:{created_ns}".encode()).hexdigest()[:12]
    )
    base = v5_schema.build_plan(
        epoch=epoch,
        created_ns=created_ns,
        anchor_ns=anchor_ns,
        root_id=root_id,
        volume_uuid=volume_uuid,
        cutover_usage_sha256=cutover_sha256,
        source_bindings=source_map,
    )
    return {
        **base,
        "schema": "brain-senses-external-rolling-v5r2-plan",
        "schema_version": 1,
        "service_id": RELEASE_ID,
        "release_id": RELEASE_ID,
        "storage": {**base["storage"], "relative_root": f"{RELEASE_RELATIVE_ROOT}/epochs/{epoch}"},
    }


def _release_lineage(
    *,
    receipt_bytes: bytes,
    manifest_bytes: bytes,
    root_id: str,
    volume_uuid: str,
    expected_sources: Mapping[str, str],
) -> dict[str, Any]:
    receipt = verify_cutover_receipt(receipt_bytes, expected_sources=expected_sources)
    expected_manifest = build_release_manifest(
        receipt_bytes=receipt_bytes,
        root_id=root_id,
        volume_uuid=volume_uuid,
        expected_sources=expected_sources,
    )
    if manifest_bytes != expected_manifest:
        raise ReleaseError("release_manifest_binding_invalid")
    return {
        "release_manifest_sha256": _sha(manifest_bytes),
        "cutover_receipt_sha256": _sha(receipt_bytes),
        "activation_bound_lifetime_cap": dict(receipt["activation_bound_lifetime_cap"]),
        "coordinator_prefix": {
            "policy_sha256": receipt["shared_capacity"]["policy_sha256"],
            "ledger_sha256": receipt["shared_capacity"]["ledger_sha256"],
            "ledger_event_count": receipt["shared_capacity"]["ledger_event_count"],
        },
    }


def build_bound_plan(
    *,
    created_ns: int,
    anchor_ns: int,
    root_id: str,
    volume_uuid: str,
    receipt_bytes: bytes,
    manifest_bytes: bytes,
    sources: Mapping[str, str],
) -> dict[str, Any]:
    """Construct a plan inseparably bound to the verified cutover artifacts."""
    lineage = _release_lineage(
        receipt_bytes=receipt_bytes,
        manifest_bytes=manifest_bytes,
        root_id=root_id,
        volume_uuid=volume_uuid,
        expected_sources=sources,
    )
    plan = build_plan(
        created_ns=created_ns,
        anchor_ns=anchor_ns,
        root_id=root_id,
        volume_uuid=volume_uuid,
        cutover_sha256=lineage["cutover_receipt_sha256"],
        sources=sources,
    )
    plan["r2_lineage"] = lineage
    return plan


def verify_bound_plan(
    plan: Mapping[str, Any],
    *,
    receipt_bytes: bytes,
    manifest_bytes: bytes,
    expected_sources: Mapping[str, str],
    root_id: str,
    volume_uuid: str,
) -> dict[str, Any]:
    """Validate the nested plan against the exact cutover and release bytes."""
    base = dict(plan)
    lineage = base.pop("r2_lineage", None)
    expected_lineage = _release_lineage(
        receipt_bytes=receipt_bytes,
        manifest_bytes=manifest_bytes,
        root_id=root_id,
        volume_uuid=volume_uuid,
        expected_sources=expected_sources,
    )
    if lineage != expected_lineage:
        raise ReleaseError("r2_plan_lineage_invalid")
    verify_plan(
        base,
        expected_sources=expected_sources,
        root_id=root_id,
        volume_uuid=volume_uuid,
        cutover_sha256=expected_lineage["cutover_receipt_sha256"],
    )
    return dict(plan)


def initialize_bound_epoch(
    writer: DescriptorWriter,
    *,
    plan: Mapping[str, Any],
    receipt_bytes: bytes,
    manifest_bytes: bytes,
    expected_sources: Mapping[str, str],
    root_id: str,
    volume_uuid: str,
) -> str:
    """Create the first r2 plan/event/control prefix, idempotently and by fd.

    This is an offline initializer only. It does not load credentials, acquire a
    provider client, create a coordinator reservation, or dispatch a request.
    """
    verify_bound_plan(
        plan,
        receipt_bytes=receipt_bytes,
        manifest_bytes=manifest_bytes,
        expected_sources=expected_sources,
        root_id=root_id,
        volume_uuid=volume_uuid,
    )
    stored_manifest, stored_receipt = verify_release_manifest_bytes(
        manifest_bytes=manifest_bytes,
        receipt_bytes=receipt_bytes,
        expected_sources=expected_sources,
        root_id=root_id,
        volume_uuid=volume_uuid,
    )
    del stored_manifest
    writer.mkdir(RELEASE_RELATIVE_ROOT)
    # The release evidence must preexist initialization; this function never
    # synthesizes or replaces its cutover lineage.
    if (
        writer.read_bytes(MANIFEST_RELATIVE_PATH, max_bytes=256_000) != manifest_bytes
        or writer.read_bytes(RECEIPT_RELATIVE_PATH, max_bytes=4_000_000) != stored_receipt
    ):
        raise ReleaseError("release_cutover_artifacts_not_published")
    epoch_id = str(plan["epoch_id"])
    epoch_root = f"{RELEASE_RELATIVE_ROOT}/epochs/{epoch_id}"
    plan_path = f"{epoch_root}/{v5_schema.PLAN_FILE}"
    event_path = f"{epoch_root}/{v5_schema.EVENT_FILE}"
    control_path = f"{RELEASE_RELATIVE_ROOT}/control.jsonl"
    plan_bytes = _canonical(dict(plan))
    start_fields = {
        "epoch_id": epoch_id,
        "plan_sha256": _sha(plan_bytes),
        "anchor_ns": plan["anchor_ns"],
    }
    writer.mkdir(f"{RELEASE_RELATIVE_ROOT}/epochs")
    control_events: tuple[dict[str, Any], ...]
    if _safe_file_exists(writer, control_path):
        control_events = v5_schema.verify_event_chain(
            writer.read_bytes(control_path, max_bytes=1_000_000),
            schema=v5_schema.CONTROL_SCHEMA,
        )
    else:
        control_events = ()
    matching = [
        event
        for event in control_events
        if event["kind"] == "epoch_open" and event["fields"].get("epoch_id") == epoch_id
    ]
    if matching:
        if len(matching) != 1 or matching[0]["fields"] != start_fields:
            raise ReleaseError("r2_control_open_binding_invalid")
        if control_events[-1]["event_sha256"] != matching[0]["event_sha256"]:
            verified = verify_bound_epoch(
                writer,
                epoch_id=epoch_id,
                receipt_bytes=receipt_bytes,
                manifest_bytes=manifest_bytes,
                expected_sources=expected_sources,
                root_id=root_id,
                volume_uuid=volume_uuid,
            )
            if verified["terminal_status"] != "complete":
                raise ReleaseError("r2_control_epoch_not_reopenable")
            return epoch_id
    else:
        if epoch_id in {event["fields"].get("epoch_id") for event in control_events}:
            raise ReleaseError("r2_control_epoch_reused")
        epoch_names = set(writer.list_names(f"{RELEASE_RELATIVE_ROOT}/epochs"))
        opened_ids = [
            event["fields"]["epoch_id"] for event in control_events if event["kind"] == "epoch_open"
        ]
        if control_events:
            if control_events[-1]["kind"] not in {
                "epoch_close",
                "budget_resume",
            } or epoch_names != set(opened_ids):
                raise ReleaseError("r2_prior_epoch_not_terminal")
            if control_events[-1]["kind"] == "epoch_close":
                prior_epoch_id = control_events[-1]["fields"].get("epoch_id")
            else:
                prior_closes = [event for event in control_events if event["kind"] == "epoch_close"]
                prior_epoch_id = (
                    prior_closes[-1]["fields"].get("epoch_id") if prior_closes else None
                )
            if not isinstance(prior_epoch_id, str):
                raise ReleaseError("r2_control_prefix_invalid")
            prior_state = verify_bound_epoch(
                writer,
                epoch_id=prior_epoch_id,
                receipt_bytes=receipt_bytes,
                manifest_bytes=manifest_bytes,
                expected_sources=expected_sources,
                root_id=root_id,
                volume_uuid=volume_uuid,
            )
            if (
                prior_state["active_epoch_id"] is not None
                or prior_state["terminal_status"] not in {"complete", "cap_paused"}
                or (
                    prior_state["terminal_status"] == "cap_paused"
                    and (
                        prior_state["budget_paused"]
                        or control_events[-1]["kind"] != "budget_resume"
                    )
                )
            ):
                raise ReleaseError("r2_prior_epoch_not_terminal")
            prior_root = f"{RELEASE_RELATIVE_ROOT}/epochs/{prior_epoch_id}"
            _, prior_plan = _read_canonical_json(
                writer, f"{prior_root}/{v5_schema.PLAN_FILE}", limit=2_000_000
            )
            prior_terminal = _read_canonical_json(
                writer, f"{prior_root}/{v5_schema.TERMINAL_FILE}", limit=64_000
            )[1]
            next_anchor = policy.next_fresh_anchor_ns(prior_plan["windows"][-1]["end_ns"])
            if plan["created_ns"] < prior_terminal["sealed_ns"] or plan["anchor_ns"] < next_anchor:
                raise ReleaseError("r2_epoch_rollover_overlaps_or_backfills")
        elif epoch_names:
            raise ReleaseError("r2_unreferenced_epoch_root")
        control_event = v5_schema.event_record(
            schema=v5_schema.CONTROL_SCHEMA,
            seq=len(control_events),
            kind="epoch_open",
            at_ns=plan["created_ns"],
            previous_sha256=control_events[-1]["event_sha256"] if control_events else None,
            fields=start_fields,
        )
        if control_events:
            writer.append(control_path, _canonical(control_event))
        else:
            writer.create(control_path, _canonical(control_event))
    # A crash after the control open but before the nested plan files is
    # resumable; no plan means no epoch dispatch authority.
    writer.mkdir(epoch_root)
    for leaf in ("intents", "outcomes", "captures", "selections"):
        writer.mkdir(f"{epoch_root}/{leaf}")
    if _safe_file_exists(writer, plan_path):
        if writer.read_bytes(plan_path, max_bytes=2_000_000) != plan_bytes:
            raise ReleaseError("r2_plan_create_only_conflict")
    else:
        writer.create(plan_path, plan_bytes)
    if _safe_file_exists(writer, event_path):
        epoch_event_bytes = writer.read_bytes(event_path, max_bytes=1_000_000)
        epoch_events = v5_schema.verify_event_chain(
            epoch_event_bytes, schema=v5_schema.EVENT_SCHEMA
        )
        if len(epoch_events) != 1 or epoch_events[0]["kind"] != "epoch_started":
            raise ReleaseError("r2_epoch_start_prefix_invalid")
        if epoch_events[0]["fields"] != start_fields:
            raise ReleaseError("r2_epoch_start_conflict")
    else:
        event = v5_schema.event_record(
            schema=v5_schema.EVENT_SCHEMA,
            seq=0,
            kind="epoch_started",
            at_ns=plan["created_ns"],
            previous_sha256=None,
            fields=start_fields,
        )
        writer.create(event_path, _canonical(event))
    verify_bound_epoch(
        writer,
        epoch_id=epoch_id,
        receipt_bytes=receipt_bytes,
        manifest_bytes=manifest_bytes,
        expected_sources=expected_sources,
        root_id=root_id,
        volume_uuid=volume_uuid,
    )
    return epoch_id


def finalize_bound_epoch(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    at_ns: int,
    receipt_bytes: bytes,
    manifest_bytes: bytes,
    expected_sources: Mapping[str, str],
    root_id: str,
    volume_uuid: str,
) -> dict[str, Any]:
    """Close a fully consumed r2 epoch with a create-only terminal receipt."""
    verify_bound_epoch(
        writer,
        epoch_id=epoch_id,
        receipt_bytes=receipt_bytes,
        manifest_bytes=manifest_bytes,
        expected_sources=expected_sources,
        root_id=root_id,
        volume_uuid=volume_uuid,
        now_ns=at_ns,
    )
    base = f"{RELEASE_RELATIVE_ROOT}/epochs/{epoch_id}"
    plan_raw = writer.read_bytes(f"{base}/{v5_schema.PLAN_FILE}", max_bytes=2_000_000)
    plan = v5_schema.decode(plan_raw)
    event_path = f"{base}/{v5_schema.EVENT_FILE}"
    event_raw = writer.read_bytes(event_path, max_bytes=MAX_BYTES)
    projection = v5_verify.verify_epoch_events(plan, event_raw)
    epoch_events_before_close = v5_schema.verify_event_chain(
        event_raw, schema=v5_schema.EVENT_SCHEMA
    )
    if type(at_ns) is not int or at_ns <= 0 or not epoch_events_before_close:
        raise ReleaseError("r2_epoch_not_complete_for_close")
    terminal_events = [
        event for event in epoch_events_before_close if event["kind"] == "epoch_terminal"
    ]
    if projection.terminal_status is None:
        if (
            projection.next_window_index != len(plan["windows"])
            or at_ns < epoch_events_before_close[-1]["at_ns"]
        ):
            raise ReleaseError("r2_epoch_not_complete_for_close")
        terminal_event = v5_schema.event_record(
            schema=v5_schema.EVENT_SCHEMA,
            seq=len(epoch_events_before_close),
            kind="epoch_terminal",
            at_ns=at_ns,
            previous_sha256=projection.event_tail_sha256,
            fields={
                "epoch_id": epoch_id,
                "status": "complete",
                "plan_sha256": _sha(plan_raw),
            },
        )
        writer.append(event_path, _canonical(terminal_event))
        sealed_ns = at_ns
    elif (
        projection.terminal_status == "complete"
        and len(terminal_events) == 1
        and projection.next_window_index == len(plan["windows"])
    ):
        # Recover an event-before-file crash prefix deterministically.
        sealed_ns = terminal_events[0]["at_ns"]
        if at_ns < sealed_ns:
            raise ReleaseError("r2_terminal_recovery_time_regressed")
    else:
        raise ReleaseError("r2_epoch_not_complete_for_close")
    terminal_events_raw = writer.read_bytes(event_path, max_bytes=MAX_BYTES)
    terminal_projection = v5_verify.verify_epoch_events(plan, terminal_events_raw)
    manifest_sha, allocation_upper, object_count, _ = _allocation_manifest(
        writer, epoch_id, root_prefix=RELEASE_RELATIVE_ROOT
    )
    terminal = {
        "schema": v5_schema.TERMINAL_SCHEMA,
        "schema_version": 5,
        "epoch_id": epoch_id,
        "plan_sha256": _sha(plan_raw),
        "event_tail_sha256": terminal_projection.event_tail_sha256,
        "status": "complete",
        "reason": "scheduled_windows_consumed",
        "sealed_ns": sealed_ns,
        "pause_reason": None,
        "allocation_manifest_sha256": manifest_sha,
        "allocation_upper_bytes": allocation_upper,
        "allocation_object_count": object_count,
    }
    terminal_raw = _canonical(terminal)
    terminal_path = f"{base}/{v5_schema.TERMINAL_FILE}"
    if _safe_file_exists(writer, terminal_path):
        if writer.read_bytes(terminal_path, max_bytes=64_000) != terminal_raw:
            raise ReleaseError("r2_terminal_create_only_conflict")
    else:
        writer.create(terminal_path, terminal_raw)
    control_path = f"{RELEASE_RELATIVE_ROOT}/{v5_schema.CONTROL_FILE}"
    control_raw = writer.read_bytes(control_path, max_bytes=1_000_000)
    control_events = v5_schema.verify_event_chain(control_raw, schema=v5_schema.CONTROL_SCHEMA)
    expected_close_fields = {
        "epoch_id": epoch_id,
        "terminal_sha256": _sha(terminal_raw),
        "event_tail_sha256": terminal_projection.event_tail_sha256,
    }
    current_opens = [
        event
        for event in control_events
        if event["kind"] == "epoch_open" and event["fields"].get("epoch_id") == epoch_id
    ]
    current_closes = [
        event
        for event in control_events
        if event["kind"] == "epoch_close" and event["fields"].get("epoch_id") == epoch_id
    ]
    expected_open_fields = {
        "epoch_id": epoch_id,
        "plan_sha256": _sha(plan_raw),
        "anchor_ns": plan["anchor_ns"],
    }
    if len(current_opens) != 1 or current_opens[0]["fields"] != expected_open_fields:
        raise ReleaseError("r2_control_open_binding_invalid")
    if not current_closes:
        if (
            not control_events
            or control_events[-1]["event_sha256"] != current_opens[0]["event_sha256"]
        ):
            raise ReleaseError("r2_control_epoch_not_active")
        control_event = v5_schema.event_record(
            schema=v5_schema.CONTROL_SCHEMA,
            seq=len(control_events),
            kind="epoch_close",
            at_ns=max(at_ns, control_events[-1]["at_ns"]),
            previous_sha256=control_events[-1]["event_sha256"],
            fields=expected_close_fields,
        )
        writer.append(control_path, _canonical(control_event))
    elif len(current_closes) != 1 or current_closes[0]["fields"] != expected_close_fields:
        raise ReleaseError("r2_control_already_terminal")
    return verify_bound_epoch(
        writer,
        epoch_id=epoch_id,
        receipt_bytes=receipt_bytes,
        manifest_bytes=manifest_bytes,
        expected_sources=expected_sources,
        root_id=root_id,
        volume_uuid=volume_uuid,
        now_ns=at_ns,
    )


def pause_bound_epoch(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    at_ns: int,
    receipt_bytes: bytes,
    manifest_bytes: bytes,
    expected_sources: Mapping[str, str],
    root_id: str,
    volume_uuid: str,
) -> dict[str, Any]:
    """Terminalize an active epoch at a verified rolling-cap boundary.

    The terminal and control transitions are create/append-only and replayable.
    A cap-paused epoch is never reopened; after headroom is proven, a new epoch
    must use a fresh anchor after the old plan's final window.
    """
    state = verify_bound_epoch(
        writer,
        epoch_id=epoch_id,
        receipt_bytes=receipt_bytes,
        manifest_bytes=manifest_bytes,
        expected_sources=expected_sources,
        root_id=root_id,
        volume_uuid=volume_uuid,
        now_ns=at_ns,
    )
    if state["budget_paused"]:
        if state["pause_reason"] is None:
            raise ReleaseError("r2_budget_pause_state_invalid")
        return state
    if state["terminal_status"] not in {None, "cap_paused"} or (
        state["active_epoch_id"] != epoch_id and state["terminal_status"] != "cap_paused"
    ):
        raise ReleaseError("r2_budget_pause_epoch_not_active")
    base = f"{RELEASE_RELATIVE_ROOT}/epochs/{epoch_id}"
    plan_raw, plan = _read_canonical_json(writer, f"{base}/{v5_schema.PLAN_FILE}", limit=2_000_000)
    event_path = f"{base}/{v5_schema.EVENT_FILE}"
    event_raw = writer.read_bytes(event_path, max_bytes=MAX_BYTES)
    epoch_events = v5_schema.verify_event_chain(event_raw, schema=v5_schema.EVENT_SCHEMA)
    terminal_events = [event for event in epoch_events if event["kind"] == "epoch_terminal"]
    if terminal_events:
        if len(terminal_events) != 1 or terminal_events[0]["fields"].get("status") != "cap_paused":
            raise ReleaseError("r2_budget_pause_terminal_conflict")
        checked_ns = terminal_events[0]["at_ns"]
        evidence_at_terminal = _r2_budget_evidence_at(
            writer,
            epoch_id=epoch_id,
            checked_ns=checked_ns,
            disposition="exceeded",
        )
        reason = state.get("terminal_pause_reason") or (
            evidence_at_terminal[1] if evidence_at_terminal else None
        )
    else:
        pending_evidence = _r2_pending_exceeded_evidence(writer, epoch_id=epoch_id)
        if pending_evidence is not None:
            _, reason, checked_ns = pending_evidence
        elif state["active_epoch_id"] == epoch_id:
            reason = _r2_budget_reason(state["combined_usage"])
            checked_ns = at_ns
        else:
            raise ReleaseError("r2_budget_pause_terminal_missing")
    if reason not in _R2_ROLLING_LIMITS:
        raise ReleaseError("r2_budget_pause_not_required")
    if checked_ns > at_ns or checked_ns < epoch_events[-1]["at_ns"]:
        raise ReleaseError("r2_budget_pause_time_invalid")
    receipt = verify_cutover_receipt(receipt_bytes, expected_sources=expected_sources)
    evidence_digest = _write_r2_budget_evidence(
        writer,
        receipt=receipt,
        suffix_rows=state["r2_capacity_rows"],
        epoch_id=epoch_id,
        reason=reason,
        disposition="exceeded",
        checked_ns=checked_ns,
    )
    if not terminal_events:
        _append_r2_event(
            writer,
            epoch_id=epoch_id,
            kind="epoch_terminal",
            at_ns=checked_ns,
            fields={
                "epoch_id": epoch_id,
                "status": "cap_paused",
                "plan_sha256": _sha(plan_raw),
            },
        )
    event_raw = writer.read_bytes(event_path, max_bytes=MAX_BYTES)
    projection = v5_verify.verify_epoch_events(plan, event_raw)
    manifest_sha, allocation_upper, object_count, _ = _allocation_manifest(
        writer, epoch_id, root_prefix=RELEASE_RELATIVE_ROOT
    )
    terminal = {
        "schema": v5_schema.TERMINAL_SCHEMA,
        "schema_version": 5,
        "epoch_id": epoch_id,
        "plan_sha256": _sha(plan_raw),
        "event_tail_sha256": projection.event_tail_sha256,
        "status": "cap_paused",
        "reason": "budget_cap_pause",
        "sealed_ns": checked_ns,
        "pause_reason": reason,
        "allocation_manifest_sha256": manifest_sha,
        "allocation_upper_bytes": allocation_upper,
        "allocation_object_count": object_count,
    }
    terminal_raw = _canonical(terminal)
    terminal_path = f"{base}/{v5_schema.TERMINAL_FILE}"
    if _safe_file_exists(writer, terminal_path):
        if writer.read_bytes(terminal_path, max_bytes=64_000) != terminal_raw:
            raise ReleaseError("r2_budget_pause_terminal_conflict")
    else:
        writer.create(terminal_path, terminal_raw)
    control_path = f"{RELEASE_RELATIVE_ROOT}/{v5_schema.CONTROL_FILE}"
    control_raw = writer.read_bytes(control_path, max_bytes=1_000_000)
    control_events = v5_schema.verify_event_chain(control_raw, schema=v5_schema.CONTROL_SCHEMA)
    current_closes = [
        event
        for event in control_events
        if event["kind"] == "epoch_close" and event["fields"].get("epoch_id") == epoch_id
    ]
    close_fields = {
        "epoch_id": epoch_id,
        "terminal_sha256": _sha(terminal_raw),
        "event_tail_sha256": projection.event_tail_sha256,
    }
    if not current_closes:
        if (
            not control_events
            or control_events[-1]["kind"] != "epoch_open"
            or control_events[-1]["fields"].get("epoch_id") != epoch_id
        ):
            raise ReleaseError("r2_budget_pause_control_not_active")
        close_event = v5_schema.event_record(
            schema=v5_schema.CONTROL_SCHEMA,
            seq=len(control_events),
            kind="epoch_close",
            at_ns=max(at_ns, control_events[-1]["at_ns"]),
            previous_sha256=control_events[-1]["event_sha256"],
            fields=close_fields,
        )
        writer.append(control_path, _canonical(close_event))
        control_events = v5_schema.verify_event_chain(
            writer.read_bytes(control_path, max_bytes=1_000_000),
            schema=v5_schema.CONTROL_SCHEMA,
        )
    elif len(current_closes) != 1 or current_closes[0]["fields"] != close_fields:
        raise ReleaseError("r2_budget_pause_control_conflict")
    pause_events = [
        event
        for event in control_events
        if event["kind"] == "budget_pause"
        and event["fields"].get("evidence_sha256") == evidence_digest
    ]
    if not pause_events:
        if (
            control_events[-1]["kind"] != "epoch_close"
            or control_events[-1]["fields"].get("epoch_id") != epoch_id
        ):
            raise ReleaseError("r2_budget_pause_control_prefix_invalid")
        pause_event = v5_schema.event_record(
            schema=v5_schema.CONTROL_SCHEMA,
            seq=len(control_events),
            kind="budget_pause",
            at_ns=max(at_ns, control_events[-1]["at_ns"]),
            previous_sha256=control_events[-1]["event_sha256"],
            fields={"reason": reason, "evidence_sha256": evidence_digest},
        )
        writer.append(control_path, _canonical(pause_event))
    elif len(pause_events) != 1 or pause_events[0]["fields"] != {
        "reason": reason,
        "evidence_sha256": evidence_digest,
    }:
        raise ReleaseError("r2_budget_pause_control_conflict")
    return verify_bound_epoch(
        writer,
        epoch_id=epoch_id,
        receipt_bytes=receipt_bytes,
        manifest_bytes=manifest_bytes,
        expected_sources=expected_sources,
        root_id=root_id,
        volume_uuid=volume_uuid,
        now_ns=at_ns,
    )


def resume_bound_budget(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    at_ns: int,
    receipt_bytes: bytes,
    manifest_bytes: bytes,
    expected_sources: Mapping[str, str],
    root_id: str,
    volume_uuid: str,
) -> dict[str, Any]:
    """Append a verified headroom transition; never restarts an old epoch."""
    state = verify_bound_epoch(
        writer,
        epoch_id=epoch_id,
        receipt_bytes=receipt_bytes,
        manifest_bytes=manifest_bytes,
        expected_sources=expected_sources,
        root_id=root_id,
        volume_uuid=volume_uuid,
        now_ns=at_ns,
    )
    reason = state.get("pause_reason")
    if not state.get("budget_paused"):
        control_raw = writer.read_bytes(
            f"{RELEASE_RELATIVE_ROOT}/{v5_schema.CONTROL_FILE}", max_bytes=1_000_000
        )
        control_events = v5_schema.verify_event_chain(control_raw, schema=v5_schema.CONTROL_SCHEMA)
        if (
            state.get("terminal_status") == "cap_paused"
            and control_events
            and control_events[-1]["kind"] == "budget_resume"
            and control_events[-1]["fields"].get("reason") == state.get("terminal_pause_reason")
        ):
            return state
        raise ReleaseError("r2_budget_not_paused")
    if not state.get("budget_paused") or reason not in _R2_ROLLING_LIMITS:
        raise ReleaseError("r2_budget_not_paused")
    if _r2_budget_reason(state["combined_usage"]) is not None:
        raise ReleaseError("r2_budget_headroom_not_available")
    receipt = verify_cutover_receipt(receipt_bytes, expected_sources=expected_sources)
    evidence_digest = _write_r2_budget_evidence(
        writer,
        receipt=receipt,
        suffix_rows=state["r2_capacity_rows"],
        epoch_id=epoch_id,
        reason=reason,
        disposition="headroom",
        checked_ns=at_ns,
    )
    control_path = f"{RELEASE_RELATIVE_ROOT}/{v5_schema.CONTROL_FILE}"
    control_raw = writer.read_bytes(control_path, max_bytes=1_000_000)
    control_events = v5_schema.verify_event_chain(control_raw, schema=v5_schema.CONTROL_SCHEMA)
    matching = [
        event
        for event in control_events
        if event["kind"] == "budget_resume"
        and event["fields"].get("evidence_sha256") == evidence_digest
    ]
    if matching:
        if len(matching) != 1 or matching[0]["fields"] != {
            "reason": reason,
            "evidence_sha256": evidence_digest,
        }:
            raise ReleaseError("r2_budget_resume_conflict")
    else:
        if not control_events or control_events[-1]["kind"] != "budget_pause":
            raise ReleaseError("r2_budget_pause_event_missing")
        event = v5_schema.event_record(
            schema=v5_schema.CONTROL_SCHEMA,
            seq=len(control_events),
            kind="budget_resume",
            at_ns=max(at_ns, control_events[-1]["at_ns"]),
            previous_sha256=control_events[-1]["event_sha256"],
            fields={"reason": reason, "evidence_sha256": evidence_digest},
        )
        writer.append(control_path, _canonical(event))
    return verify_bound_epoch(
        writer,
        epoch_id=epoch_id,
        receipt_bytes=receipt_bytes,
        manifest_bytes=manifest_bytes,
        expected_sources=expected_sources,
        root_id=root_id,
        volume_uuid=volume_uuid,
        now_ns=at_ns,
    )


def verify_release_manifest_bytes(
    *,
    manifest_bytes: bytes,
    receipt_bytes: bytes,
    expected_sources: Mapping[str, str],
    root_id: str,
    volume_uuid: str,
) -> tuple[dict[str, Any], bytes]:
    """Pure in-memory form of release manifest and receipt verification."""
    try:
        manifest = v5_schema.decode(manifest_bytes)
    except v5_schema.SchemaError as exc:
        raise ReleaseError("release_manifest_json_invalid") from exc
    expected = build_release_manifest(
        receipt_bytes=receipt_bytes,
        root_id=root_id,
        volume_uuid=volume_uuid,
        expected_sources=expected_sources,
    )
    if manifest_bytes != expected or not isinstance(manifest, dict):
        raise ReleaseError("release_manifest_binding_invalid")
    return manifest, receipt_bytes


def verify_bound_epoch(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    receipt_bytes: bytes,
    manifest_bytes: bytes,
    expected_sources: Mapping[str, str],
    root_id: str,
    volume_uuid: str,
    now_ns: int | None = None,
    _local_only: bool = False,
) -> dict[str, Any]:
    """Independently replay the nested r2 plan, events, and control prefix."""
    v5_schema.validate_epoch_id(epoch_id)
    manifest, stored_receipt = verify_release_manifest(
        writer,
        root_id=root_id,
        volume_uuid=volume_uuid,
        expected_sources=expected_sources,
    )
    del manifest
    if manifest_bytes != writer.read_bytes(MANIFEST_RELATIVE_PATH, max_bytes=256_000):
        raise ReleaseError("r2_manifest_snapshot_changed")
    if receipt_bytes != stored_receipt:
        raise ReleaseError("r2_receipt_snapshot_changed")
    base = f"{RELEASE_RELATIVE_ROOT}/epochs/{epoch_id}"
    plan_raw, plan = _read_canonical_json(writer, f"{base}/{v5_schema.PLAN_FILE}", limit=2_000_000)
    verify_bound_plan(
        plan,
        receipt_bytes=receipt_bytes,
        manifest_bytes=manifest_bytes,
        expected_sources=expected_sources,
        root_id=root_id,
        volume_uuid=volume_uuid,
    )
    event_raw = writer.read_bytes(f"{base}/{v5_schema.EVENT_FILE}", max_bytes=MAX_BYTES)
    projection = v5_verify.verify_epoch_events(plan, event_raw)
    _verify_sealed_frames(
        writer,
        epoch_id,
        plan,
        event_raw,
        root_prefix=f"{RELEASE_RELATIVE_ROOT}/epochs/{epoch_id}",
    )
    control_raw = writer.read_bytes(f"{RELEASE_RELATIVE_ROOT}/control.jsonl", max_bytes=1_000_000)
    control_events = v5_schema.verify_event_chain(control_raw, schema=v5_schema.CONTROL_SCHEMA)
    open_rows = [
        event
        for event in control_events
        if event["kind"] == "epoch_open" and event["fields"].get("epoch_id") == epoch_id
    ]
    if len(open_rows) != 1 or open_rows[0]["fields"] != {
        "epoch_id": epoch_id,
        "plan_sha256": _sha(plan_raw),
        "anchor_ns": plan["anchor_ns"],
    }:
        raise ReleaseError("r2_control_state_invalid")
    terminal_path = f"{base}/{v5_schema.TERMINAL_FILE}"
    terminal_doc: dict[str, Any] | None = None
    terminals: dict[str, v5_verify.VerifiedTerminal] = {}
    terminal_file_exists = _safe_file_exists(writer, terminal_path)
    if terminal_file_exists:
        terminal_raw, decoded_terminal = _read_canonical_json(writer, terminal_path, limit=64_000)
        terminal_rows = v5_schema.verify_event_chain(event_raw, schema=v5_schema.EVENT_SCHEMA)
        required_terminal_fields = {
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
        manifest_sha, allocation_upper, allocation_count, _ = _allocation_manifest(
            writer, epoch_id, root_prefix=RELEASE_RELATIVE_ROOT
        )
        if (
            projection.terminal_status not in {"complete", "cap_paused"}
            or not isinstance(decoded_terminal, dict)
            or set(decoded_terminal) != required_terminal_fields
            or decoded_terminal.get("schema") != v5_schema.TERMINAL_SCHEMA
            or decoded_terminal.get("schema_version") != 5
            or decoded_terminal.get("epoch_id") != epoch_id
            or decoded_terminal.get("plan_sha256") != _sha(plan_raw)
            or decoded_terminal.get("event_tail_sha256") != projection.event_tail_sha256
            or decoded_terminal.get("status") != projection.terminal_status
            or not terminal_rows
            or terminal_rows[-1]["kind"] != "epoch_terminal"
            or decoded_terminal.get("sealed_ns") != terminal_rows[-1]["at_ns"]
            or decoded_terminal.get("allocation_manifest_sha256") != manifest_sha
            or decoded_terminal.get("allocation_upper_bytes") != allocation_upper
            or decoded_terminal.get("allocation_object_count") != allocation_count
            or (
                projection.terminal_status == "cap_paused"
                and (
                    decoded_terminal.get("reason") != "budget_cap_pause"
                    or decoded_terminal.get("pause_reason") not in _R2_ROLLING_LIMITS
                )
            )
            or (
                projection.terminal_status == "complete"
                and decoded_terminal.get("pause_reason") is not None
            )
        ):
            raise ReleaseError("r2_terminal_receipt_invalid")
        terminal_doc = decoded_terminal
        close_rows = [
            event
            for event in control_events
            if event["kind"] == "epoch_close" and event["fields"].get("epoch_id") == epoch_id
        ]
        if close_rows:
            if len(close_rows) != 1:
                raise ReleaseError("r2_terminal_control_binding_invalid")
            terminal_event = close_rows[0]
            if (
                terminal_event["fields"].get("terminal_sha256") != _sha(terminal_raw)
                or terminal_event["fields"].get("event_tail_sha256") != projection.event_tail_sha256
            ):
                raise ReleaseError("r2_terminal_control_binding_invalid")
        terminals[epoch_id] = v5_verify.VerifiedTerminal(
            terminal_sha256=_sha(terminal_raw),
            event_tail_sha256=projection.event_tail_sha256 or "",
            status=decoded_terminal["status"],
            pause_reason=decoded_terminal["pause_reason"],
        )
    expected_files = {
        f"{base}/{v5_schema.PLAN_FILE}",
        f"{base}/{v5_schema.EVENT_FILE}",
    }
    if terminal_doc is not None:
        expected_files.add(terminal_path)
    epoch_events = v5_schema.verify_event_chain(event_raw, schema=v5_schema.EVENT_SCHEMA)
    intent_rows = {
        event["fields"]["request_id"]: event["fields"]
        for event in epoch_events
        if event["kind"] == "request_intent"
    }
    outcome_rows = {
        event["fields"]["request_id"]: event
        for event in epoch_events
        if event["kind"] in {"request_captured", "request_failed"}
    }
    if len(intent_rows) != sum(event["kind"] == "request_intent" for event in epoch_events):
        raise ReleaseError("r2_request_intent_duplicate")
    if len(outcome_rows) != sum(
        event["kind"] in {"request_captured", "request_failed"} for event in epoch_events
    ):
        raise ReleaseError("r2_request_outcome_duplicate")
    for request_id, intent in intent_rows.items():
        expected_intent_path = f"{base}/intents/{request_id}.json"
        intent_raw, intent_doc = _read_canonical_json(writer, expected_intent_path, limit=64_000)
        if (
            intent_doc.get("schema") != "brain-senses-external-rolling-v5r2-capacity-intent"
            or intent_doc.get("epoch_id") != epoch_id
            or intent_doc.get("request_id") != request_id
            or intent_doc.get("method") != intent.get("method")
            or intent_doc.get("params") != intent.get("params")
            or intent_doc.get("bucket") != intent.get("bucket")
            or intent_doc.get("ordinal") != intent.get("ordinal")
            or intent_doc.get("wire_request_sha256") != intent.get("wire_request_sha256")
            or intent_doc.get("decision_ns") != intent.get("decision_ns")
            or intent_doc.get("semantic_request_sha256")
            != semantic_request_sha256(intent["method"], intent["params"])
            or intent_doc.get("wire_request_sha256")
            != wire_request_sha256(intent["method"], intent["params"])
            or not intent_raw
        ):
            raise ReleaseError("r2_intent_file_binding_invalid")
        expected_files.add(expected_intent_path)
        outcome_event = outcome_rows.get(request_id)
        outcome_path = f"{base}/outcomes/{request_id}.json"
        if outcome_event is not None:
            outcome_raw, outcome_doc = _read_canonical_json(writer, outcome_path, limit=64_000)
            if outcome_doc.get("request_id") != request_id:
                raise ReleaseError("r2_outcome_file_binding_invalid")
            if outcome_event["kind"] == "request_captured":
                if (
                    outcome_doc.get("status") != "captured"
                    or outcome_doc.get("response_sha256")
                    != outcome_event["fields"].get("response_sha256")
                    or outcome_doc.get("availability_ns")
                    != outcome_event["fields"].get("availability_ns")
                ):
                    raise ReleaseError("r2_captured_outcome_binding_invalid")
                capture_dir = f"{base}/captures/{request_id}"
                capture = capture_verify.verify_capture(
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
                if capture["response_sha256"] != outcome_event["fields"].get(
                    "response_sha256"
                ) or capture["response_bytes"] != outcome_event["fields"].get("response_bytes"):
                    raise ReleaseError("r2_capture_outcome_hash_mismatch")
                expected_files.update(
                    {f"{capture_dir}/response.json", f"{capture_dir}/receipt.json"}
                )
            else:
                if outcome_doc.get("status") != "possibly_spent":
                    raise ReleaseError("r2_failed_outcome_binding_invalid")
            expected_files.add(outcome_path)
        else:
            response_path = f"{base}/captures/{request_id}/response.json"
            capture_receipt_path = f"{base}/captures/{request_id}/receipt.json"
            if _safe_file_exists(writer, response_path):
                expected_files.add(response_path)
                if _safe_file_exists(writer, capture_receipt_path):
                    expected_files.add(capture_receipt_path)
                    capture_verify.verify_capture(
                        writer,
                        relative_directory=f"{base}/captures/{request_id}",
                        expected={
                            "request_id": request_id,
                            "request_sha256": semantic_request_sha256(
                                intent["method"], intent["params"]
                            ),
                            "wire_request_sha256": intent["wire_request_sha256"],
                            "decision_ns": intent["decision_ns"],
                        },
                    )
    for event in epoch_events:
        if event["kind"] == "frame_sealed" and event["fields"].get("empty_interval") is False:
            index = event["fields"].get("index")
            if (
                type(index) is int
                and 0 <= index < len(plan["windows"])
                and plan["windows"][index].get("kind") == "inventory"
            ):
                bucket = event["fields"].get("bucket")
                expected_files.add(f"{base}/selections/{bucket}.json")
    actual = {path for path in writer.list_files() if path.startswith(f"{base}/")}
    if actual != expected_files:
        raise ReleaseError("r2_epoch_file_closure_invalid")
    expected_names: dict[str, set[str]] = {
        leaf: set() for leaf in ("intents", "outcomes", "captures", "selections")
    }
    for path in expected_files:
        relative = path.removeprefix(f"{base}/").split("/")
        if len(relative) >= 2 and relative[0] in expected_names:
            if relative[0] == "captures":
                expected_names["captures"].add(relative[1])
            else:
                expected_names[relative[0]].add(relative[-1])
    for leaf, names in expected_names.items():
        if set(writer.list_names(f"{base}/{leaf}")) != names:
            raise ReleaseError("r2_epoch_directory_closure_invalid")
    if _local_only:
        return {
            "status": "verified_local_epoch",
            "epoch_id": epoch_id,
            "plan": plan,
            "plan_raw": plan_raw,
            "event_raw": event_raw,
            "projection": projection,
            "terminal": terminal_doc,
            "epoch_events": epoch_events,
            "intent_count": len(intent_rows),
            "capture_count": sum(
                row["kind"] == "request_captured" for row in outcome_rows.values()
            ),
        }
    try:
        policy_raw, ledger_raw, _ledger_events, _reservations, _terminals = _ledger_rows(
            writer, root_id=root_id, volume_uuid=volume_uuid
        )
    except (OSError, capacity.CoordinatorError) as exc:
        raise ReleaseError("r2_coordinator_snapshot_unavailable") from exc
    receipt = verify_cutover_receipt(receipt_bytes, expected_sources=expected_sources)
    _verify_activation_cap_file(writer, receipt, root_id=root_id, volume_uuid=volume_uuid)
    if _sha(policy_raw) != receipt["shared_capacity"]["policy_sha256"]:
        raise ReleaseError("r2_coordinator_policy_changed")
    # Epoch IDs are part of the reservation namespace, so collect every local
    # journal before reconciling the coordinator suffix. A non-canonical or
    # malformed sibling prevents this root from claiming a verified prefix.
    all_request_events: list[dict[str, Any]] = []
    epoch_root = f"{RELEASE_RELATIVE_ROOT}/epochs"
    release_names = set(writer.list_names(RELEASE_RELATIVE_ROOT))
    if release_names != {
        "cutover-receipt.json",
        "release-manifest.json",
        v5_schema.CONTROL_FILE,
        "epochs",
    } and release_names != {
        "cutover-receipt.json",
        "release-manifest.json",
        v5_schema.CONTROL_FILE,
        "epochs",
        "budget-evidence",
    }:
        raise ReleaseError("r2_release_file_closure_invalid")
    epoch_names = set(writer.list_names(epoch_root))
    if epoch_id not in epoch_names or not epoch_names:
        raise ReleaseError("r2_epoch_control_closure_invalid")
    journal_open_ids = {
        event["fields"].get("epoch_id") for event in control_events if event["kind"] == "epoch_open"
    }
    if journal_open_ids != epoch_names:
        raise ReleaseError("r2_epoch_control_closure_invalid")
    local_epochs: dict[str, dict[str, Any]] = {}
    for candidate in sorted(epoch_names):
        try:
            v5_schema.validate_epoch_id(candidate)
        except Exception as exc:
            raise ReleaseError("r2_epoch_directory_name_invalid") from exc
        local_epochs[candidate] = verify_bound_epoch(
            writer,
            epoch_id=candidate,
            receipt_bytes=receipt_bytes,
            manifest_bytes=manifest_bytes,
            expected_sources=expected_sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
            now_ns=now_ns,
            _local_only=True,
        )
    ordered_open_ids = [
        event["fields"]["epoch_id"] for event in control_events if event["kind"] == "epoch_open"
    ]
    if (
        len(ordered_open_ids) != len(epoch_names)
        or len(set(ordered_open_ids)) != len(ordered_open_ids)
        or set(ordered_open_ids) != epoch_names
    ):
        raise ReleaseError("r2_epoch_control_closure_invalid")
    all_buckets: set[str] = set()
    prior_plan: Mapping[str, Any] | None = None
    prior_terminal: Mapping[str, Any] | None = None
    for candidate in ordered_open_ids:
        local = local_epochs[candidate]
        local_plan = local["plan"]
        local_windows = local_plan["windows"]
        buckets = [window["bucket"] for window in local_windows]
        if len(buckets) != len(set(buckets)) or all_buckets.intersection(buckets):
            raise ReleaseError("r2_epoch_bucket_reuse")
        all_buckets.update(buckets)
        if prior_plan is not None:
            if not isinstance(prior_terminal, dict) or prior_terminal.get("status") not in {
                "complete",
                "cap_paused",
            }:
                raise ReleaseError("r2_next_epoch_before_terminal")
            prior_end = prior_plan["windows"][-1]["end_ns"]
            minimum_next_anchor = policy.next_fresh_anchor_ns(prior_end)
            if (
                local_plan["created_ns"] < prior_terminal["sealed_ns"]
                or local_plan["anchor_ns"] < minimum_next_anchor
            ):
                raise ReleaseError("r2_epoch_rollover_overlaps_or_backfills")
        prior_plan = local_plan
        prior_terminal = local["terminal"]
    plans = {name: local["plan_raw"] for name, local in local_epochs.items()}
    terminals = {
        name: v5_verify.VerifiedTerminal(
            terminal_sha256=_sha(_canonical(local["terminal"])),
            event_tail_sha256=local["terminal"]["event_tail_sha256"],
            status=local["terminal"]["status"],
            pause_reason=local["terminal"]["pause_reason"],
        )
        for name, local in local_epochs.items()
        if isinstance(local["terminal"], dict)
    }
    selected = local_epochs[epoch_id]
    projection = selected["projection"]
    for candidate, local in local_epochs.items():
        all_request_events.extend(
            {
                **event,
                "fields": {**event["fields"], "epoch_id": candidate},
            }
            for event in local["epoch_events"]
            if event["kind"] in {"request_intent", "request_captured", "request_failed"}
        )
    suffix_rows = verify_r2_capacity_suffix(
        writer,
        ledger_raw,
        receipt=receipt,
        verified_request_events=all_request_events,
    )
    budget_evidence = _r2_budget_evidence_map(
        writer,
        receipt=receipt,
        suffix_rows=suffix_rows,
        now_ns=time.time_ns() if now_ns is None else now_ns,
        allowed_epoch_ids=epoch_names,
    )
    control = v5_verify.verify_control_chain(
        control_raw,
        plans=plans,
        terminals=terminals,
        budget_evidence=budget_evidence,
        plan_validator=lambda raw, _epoch: verify_bound_plan(
            v5_schema.decode(raw),
            receipt_bytes=receipt_bytes,
            manifest_bytes=manifest_bytes,
            expected_sources=expected_sources,
            root_id=root_id,
            volume_uuid=volume_uuid,
        ),
    )
    if control.active_epoch_id not in {None, ordered_open_ids[-1]}:
        raise ReleaseError("r2_control_epoch_mismatch")
    evidence_dir = f"{RELEASE_RELATIVE_ROOT}/budget-evidence"
    try:
        evidence_names = set(writer.list_names(evidence_dir))
    except FileNotFoundError:
        evidence_names = set()
    evidence_docs: dict[str, dict[str, Any]] = {}
    for name in evidence_names:
        raw, doc = _read_canonical_json(writer, f"{evidence_dir}/{name}", limit=64_000)
        evidence_docs[_sha(raw)] = doc
    referenced_evidence = {
        event["fields"]["evidence_sha256"]
        for event in control_events
        if event["kind"] in {"budget_pause", "budget_resume"}
    }
    dangling = set(evidence_docs) - referenced_evidence
    if len(dangling) > 1:
        raise ReleaseError("r2_budget_evidence_orphan_count")
    if dangling:
        dangling_sha = next(iter(dangling))
        evidence_doc = evidence_docs[dangling_sha]
        evidence_epoch = evidence_doc["epoch_id"]
        checked_ns = evidence_doc["checked_ns"]
        if evidence_doc["disposition"] == "exceeded":
            local_events = local_epochs[evidence_epoch]["epoch_events"]
            if not local_events or local_events[-1]["at_ns"] > checked_ns:
                raise ReleaseError("r2_budget_evidence_orphan_order")
            latest_kind = control_events[-1]["kind"] if control_events else None
            active_pending = control.active_epoch_id == evidence_epoch
            terminal_pending = (
                latest_kind == "epoch_close"
                and control_events[-1]["fields"].get("epoch_id") == evidence_epoch
                and local_epochs[evidence_epoch]["terminal"] is not None
                and local_epochs[evidence_epoch]["terminal"].get("status") == "cap_paused"
            )
            if not active_pending and not terminal_pending:
                raise ReleaseError("r2_budget_evidence_orphan_unbound")
        else:
            if (
                not control.budget_paused
                or control.pause_reason != evidence_doc["reason"]
                or not control_events
                or control_events[-1]["kind"] != "budget_pause"
                or control_events[-1]["at_ns"] > checked_ns
            ):
                raise ReleaseError("r2_budget_headroom_orphan_unbound")
    combined_usage = _combined_usage(
        receipt,
        suffix_rows,
        now_ns=time.time_ns() if now_ns is None else now_ns,
    )
    return {
        "status": "verified_prefix",
        "epoch_id": epoch_id,
        "plan_sha256": _sha(selected["plan_raw"]),
        "event_chain_sha256": _sha(selected["event_raw"]),
        "event_tail_sha256": projection.event_tail_sha256,
        "next_window_index": projection.next_window_index,
        "terminal_status": projection.terminal_status,
        "terminal_pause_reason": (
            selected["terminal"].get("pause_reason")
            if isinstance(selected["terminal"], dict)
            else None
        ),
        "request_states": list(projection.request_states),
        "active_epoch_id": control.active_epoch_id,
        "budget_paused": control.budget_paused,
        "pause_reason": control.pause_reason,
        "epoch_ids": ordered_open_ids,
        "epoch_summaries": [
            {
                "epoch_id": name,
                "plan_sha256": _sha(local["plan_raw"]),
                "event_tail_sha256": local["projection"].event_tail_sha256,
                "terminal_status": local["projection"].terminal_status,
                "intent_count": local["intent_count"],
                "capture_count": local["capture_count"],
            }
            for name, local in local_epochs.items()
        ],
        "cutover_receipt_sha256": _sha(receipt_bytes),
        "release_manifest_sha256": _sha(manifest_bytes),
        "coordinator_policy_sha256": _sha(policy_raw),
        "coordinator_ledger_sha256": _sha(ledger_raw),
        "coordinator_ledger_event_count": len(_ledger_events),
        "r2_capacity_rows": suffix_rows,
        "combined_usage": combined_usage,
    }


def _append_r2_event(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    kind: str,
    at_ns: int,
    fields: Mapping[str, Any],
) -> dict[str, Any]:
    path = f"{RELEASE_RELATIVE_ROOT}/epochs/{epoch_id}/{v5_schema.EVENT_FILE}"
    raw = writer.read_bytes(path, max_bytes=MAX_BYTES)
    events = v5_schema.verify_event_chain(raw, schema=v5_schema.EVENT_SCHEMA)
    event = v5_schema.event_record(
        schema=v5_schema.EVENT_SCHEMA,
        seq=len(events),
        kind=kind,
        at_ns=at_ns,
        previous_sha256=events[-1]["event_sha256"] if events else None,
        fields=fields,
    )
    writer.append(path, _canonical(event))
    return event


def _dispatch_r2(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    bucket: str,
    ordinal: int,
    method: str,
    params: list[Any],
    now_ns: Any,
    client: Any,
    coordinator: Any,
    lane_lease: Any,
    guard_check: Any,
    pre_http_check: Any | None = None,
    clock_record: Mapping[str, Any] | None = None,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Persist one nested r2 request; every durable intent is spent once."""
    base = f"{RELEASE_RELATIVE_ROOT}/epochs/{epoch_id}"
    event_path = f"{base}/{v5_schema.EVENT_FILE}"
    events_raw = writer.read_bytes(event_path, max_bytes=MAX_BYTES)
    events = v5_schema.verify_event_chain(events_raw, schema=v5_schema.EVENT_SCHEMA)
    request_id = f"{bucket}-{ordinal:02d}"
    prior_intents = [
        event["fields"]
        for event in events
        if event["kind"] == "request_intent" and event["fields"].get("bucket") == bucket
    ]
    if ordinal != len(prior_intents):
        raise ReleaseError("r2_request_ordinal_spent_or_gap")
    for event in events:
        if event["kind"] == "request_intent" and event["fields"].get("request_id") == request_id:
            raise ReleaseError("r2_request_id_spent_no_replay")
    if prior_intents:
        elapsed = now_ns() - prior_intents[-1]["decision_ns"]
        if elapsed < 250_000_000:
            sleep((250_000_000 - elapsed) / 1_000_000_000)
    decision_ns = now_ns()
    wire_sha = wire_request_sha256(method, params)
    request_sha = semantic_request_sha256(method, params)
    intent_doc = {
        "schema": "brain-senses-external-rolling-v5r2-capacity-intent",
        "schema_version": 1,
        "service": "helius",
        "release_id": RELEASE_ID,
        "epoch_id": epoch_id,
        "request_id": request_id,
        "bucket": bucket,
        "ordinal": ordinal,
        "method": method,
        "params": params,
        "semantic_request_sha256": request_sha,
        "wire_request_sha256": wire_sha,
        "decision_ns": decision_ns,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }
    intent_bytes = _canonical(intent_doc)
    intent_path = f"{base}/intents/{request_id}.json"
    outcome_path = f"{base}/outcomes/{request_id}.json"
    reservation_id = f"helius-v5r2:{epoch_id}:{request_id}"
    try:
        guard_check()
        reservation = coordinator.reserve_and_persist_intent(
            service="helius",
            reservation_id=reservation_id,
            request_id=request_id,
            intent_path=intent_path,
            outcome_path=outcome_path,
            intent_bytes=intent_bytes,
            intent_sha256=_sha(intent_bytes),
            reserve_bytes=policy.MAX_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES,
            lane_lease=lane_lease,
        )
    except Exception as exc:
        raise ReleaseError("r2_capacity_reservation_failed") from exc
    _append_r2_event(
        writer,
        epoch_id=epoch_id,
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
        if pre_http_check is not None:
            pre_http_check()
        capture = client.fetch(method, params)
        if clock_record is not None:
            clock_check.validate_progress(
                dict(clock_record),
                wall_ns=int(capture.received_ns),
                monotonic_ns=__import__("time").monotonic_ns(),
            )
        response = bytes(capture.response)
        if len(response) > policy.MAX_RESPONSE_BYTES:
            raise ReleaseError("r2_provider_response_too_large")
        decoded = v5_schema.decode(response)
        if (
            not isinstance(decoded, dict)
            or decoded.get("jsonrpc") != "2.0"
            or type(decoded.get("id")) is not int
            or decoded.get("id") != 1
            or "error" in decoded
            or "result" not in decoded
        ):
            raise ReleaseError("r2_invalid_json_rpc_response")
        capture_dir = f"{base}/captures/{request_id}"
        writer.mkdir(capture_dir)
        response_sha = _sha(response)
        writer.create(f"{capture_dir}/response.json", response)
        receipt = {
            "schema": v5_schema.RECEIPT_SCHEMA,
            "schema_version": 1,
            "provider": "helius-free",
            "request_id": request_id,
            "request_sha256": request_sha,
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
        writer.create(f"{capture_dir}/receipt.json", _canonical(receipt))
        verified = capture_verify.verify_capture(
            writer,
            relative_directory=capture_dir,
            expected={
                "request_id": request_id,
                "request_sha256": request_sha,
                "wire_request_sha256": wire_sha,
                "decision_ns": decision_ns,
            },
        )
        outcome_bytes = _canonical(
            {
                "schema": "brain-senses-external-rolling-v5-capacity-outcome",
                "schema_version": 5,
                "request_id": request_id,
                "status": "captured",
                "response_sha256": response_sha,
                "response_bytes": len(response),
                "availability_ns": int(capture.received_ns),
                "provider_charge_units": None,
                "provider_charge_status": "unknown_unverified",
            }
        )
        coordinator.settle(
            reservation,
            outcome="captured",
            outcome_bytes=outcome_bytes,
            outcome_path=outcome_path,
            ambiguous=False,
            lane_lease=lane_lease,
        )
        _append_r2_event(
            writer,
            epoch_id=epoch_id,
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
        capture_dir = f"{base}/captures/{request_id}"
        if _safe_file_exists(writer, f"{capture_dir}/receipt.json") and _safe_file_exists(
            writer, f"{capture_dir}/response.json"
        ):
            raise
        failure = v5_runtime._safe_reason(exc)
        outcome_bytes = _canonical(
            {
                "schema": "brain-senses-external-rolling-v5-capacity-outcome",
                "schema_version": 5,
                "request_id": request_id,
                "status": "possibly_spent",
                "failure_class": failure,
                "provider_charge_units": None,
                "provider_charge_status": "unknown_unverified",
            }
        )
        try:
            coordinator.settle(
                reservation,
                outcome="possibly_spent_failure",
                outcome_bytes=outcome_bytes,
                outcome_path=outcome_path,
                ambiguous=True,
                lane_lease=lane_lease,
            )
        except Exception as settle_exc:
            raise ReleaseError("r2_capacity_settlement_failed") from settle_exc
        _append_r2_event(
            writer,
            epoch_id=epoch_id,
            kind="request_failed",
            at_ns=now_ns(),
            fields={
                "request_id": request_id,
                "failure_class": failure,
                "certainty": "possibly_spent",
            },
        )
        raise ReleaseError(failure) from None


def _latest_r2_anchor(reader: DescriptorWriter, epoch_id: str, event_bytes: bytes) -> int | None:
    events = v5_schema.verify_event_chain(event_bytes, schema=v5_schema.EVENT_SCHEMA)
    intents = {
        event["fields"]["request_id"]: event["fields"]
        for event in events
        if event["kind"] == "request_intent"
    }
    latest: int | None = None
    base = f"{RELEASE_RELATIVE_ROOT}/epochs/{epoch_id}"
    for sealed in (event for event in events if event["kind"] == "frame_sealed"):
        for request_id in sealed["fields"]["request_ids"]:
            intent = intents[request_id]
            if intent["method"] != "getSlot":
                continue
            capture = capture_verify.verify_capture(
                reader,
                relative_directory=f"{base}/captures/{request_id}",
                expected={
                    "request_id": request_id,
                    "request_sha256": semantic_request_sha256(intent["method"], intent["params"]),
                    "wire_request_sha256": intent["wire_request_sha256"],
                    "decision_ns": intent["decision_ns"],
                },
            )
            slot = frame_verify.finalized_slot(capture["response"].get("result"))
            latest = slot
    return latest


def run_r2_frame(
    writer: DescriptorWriter,
    *,
    epoch_id: str,
    window_index: int,
    mode: str,
    now_ns: Any,
    client: Any,
    coordinator: Any,
    lane_lease: Any,
    guard_check: Any,
    admission_check: Any | None = None,
    clock_record: Mapping[str, Any] | None = None,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Run one deterministic v5-compatible frame under the nested r2 root."""
    base = f"{RELEASE_RELATIVE_ROOT}/epochs/{epoch_id}"
    plan_raw, plan = _read_canonical_json(writer, f"{base}/{v5_schema.PLAN_FILE}")
    v5_schema.validate_epoch_id(epoch_id)
    if plan.get("epoch_id") != epoch_id or type(window_index) is not int:
        raise ReleaseError("r2_frame_plan_binding_invalid")
    projection = v5_verify.verify_epoch_events(
        plan, writer.read_bytes(f"{base}/{v5_schema.EVENT_FILE}", max_bytes=MAX_BYTES)
    )
    if projection.next_window_index != window_index or projection.terminal_status is not None:
        raise ReleaseError("r2_frame_not_next_or_terminal")
    if not 0 <= window_index < len(plan["windows"]):
        raise ReleaseError("r2_window_index_invalid")
    window = plan["windows"][window_index]
    expected_mode = (
        "anchor"
        if window_index == 0
        else "anchor_reset"
        if projection.reset_anchor_required
        else "inventory"
    )
    if mode != expected_mode:
        raise ReleaseError("r2_frame_mode_invalid")
    now = now_ns()
    disposition = policy.disposition(
        policy.Window(
            window["kind"],
            window["index"],
            window["bucket"],
            window["start_ns"],
            window["end_ns"],
            window["request_cap"],
        ),
        now,
    )
    if disposition == "not_due":
        return {"status": "not_due", "provider_io": False, "bucket": window["bucket"]}
    if disposition == "due":
        if clock_record is None:
            raise ReleaseError("r2_clock_record_missing")
        try:
            clock_check.validate_progress(
                dict(clock_record), wall_ns=now, monotonic_ns=time.monotonic_ns()
            )
        except Exception as exc:
            raise ReleaseError("r2_clock_preflight_invalid") from exc
    events_path = f"{base}/{v5_schema.EVENT_FILE}"
    if disposition == "expired_unobserved":
        if projection.active_bucket == window["bucket"]:
            _append_r2_event(
                writer,
                epoch_id=epoch_id,
                kind="window_interrupted",
                at_ns=now,
                fields={
                    "index": window_index,
                    "bucket": window["bucket"],
                    "reason": "partial_frame",
                },
            )
            return {
                "status": "interrupted",
                "provider_io": False,
                "bucket": window["bucket"],
            }
        _append_r2_event(
            writer,
            epoch_id=epoch_id,
            kind="window_missed",
            at_ns=now,
            fields={
                "index": window_index,
                "bucket": window["bucket"],
                "kind": window["kind"],
                "start_ns": window["start_ns"],
                "end_ns": window["end_ns"],
            },
        )
        return {"status": "missed", "provider_io": False, "bucket": window["bucket"]}
    if projection.active_bucket is None:
        _append_r2_event(
            writer,
            epoch_id=epoch_id,
            kind="window_reserved",
            at_ns=now,
            fields={
                "index": window_index,
                "bucket": window["bucket"],
                "kind": window["kind"],
                "mode": mode,
                "request_cap": window["request_cap"],
            },
        )
    elif projection.active_bucket != window["bucket"] or projection.active_mode != mode:
        raise ReleaseError("r2_active_window_mismatch")
    if admission_check is None or not callable(admission_check):
        raise ReleaseError("r2_admission_gate_missing")
    attempted: list[str] = []
    dispatched: list[str] = []

    def dispatch(ordinal: int, method: str, params: list[Any]) -> dict[str, Any]:
        def recheck_before_http() -> None:
            dispatch_now = now_ns()
            if (
                policy.disposition(
                    policy.Window(
                        window["kind"],
                        window["index"],
                        window["bucket"],
                        window["start_ns"],
                        window["end_ns"],
                        window["request_cap"],
                    ),
                    dispatch_now,
                )
                != "due"
            ):
                raise ReleaseError("r2_window_expired_before_http")
            try:
                clock_check.validate_progress(
                    dict(clock_record or {}),
                    wall_ns=dispatch_now,
                    monotonic_ns=time.monotonic_ns(),
                )
            except Exception as exc:
                raise ReleaseError("r2_clock_progress_invalid") from exc
            guard_check()
            admission_check(dispatch_now)

        recheck_before_http()
        attempted.append(f"{window['bucket']}-{ordinal:02d}")
        capture = _dispatch_r2(
            writer,
            epoch_id=epoch_id,
            bucket=window["bucket"],
            ordinal=ordinal,
            method=method,
            params=params,
            now_ns=now_ns,
            client=client,
            coordinator=coordinator,
            lane_lease=lane_lease,
            guard_check=guard_check,
            pre_http_check=recheck_before_http,
            clock_record=clock_record,
            sleep=sleep,
        )
        dispatched.append(f"{window['bucket']}-{ordinal:02d}")
        return capture

    anchor_capture = dispatch(0, "getSlot", [{"commitment": "finalized"}])
    current_slot = frame_verify.finalized_slot(anchor_capture["response"].get("result"))
    empty_interval = False
    if mode == "inventory":
        event_bytes = writer.read_bytes(events_path, max_bytes=MAX_BYTES)
        previous_slot = _latest_r2_anchor(writer, epoch_id, event_bytes)
        if previous_slot is None:
            raise ReleaseError("r2_previous_verified_anchor_missing")
        if current_slot < previous_slot:
            raise ReleaseError("r2_finalized_slot_regression")
        if current_slot == previous_slot:
            empty_interval = True
        else:
            if current_slot - previous_slot > policy.MAX_INVENTORY_SLOTS_PER_FRAME:
                _append_r2_event(
                    writer,
                    epoch_id=epoch_id,
                    kind="window_interrupted",
                    at_ns=now_ns(),
                    fields={
                        "index": window_index,
                        "bucket": window["bucket"],
                        "reason": "oversized_interval",
                    },
                )
                return {
                    "status": "interrupted",
                    "provider_io": bool(dispatched),
                    "attempted": attempted,
                    "dispatched": dispatched,
                    "bucket": window["bucket"],
                }
            inventory_call = dispatch(
                1,
                "getBlocks",
                [previous_slot + 1, current_slot, {"commitment": "finalized"}],
            )
            inventory = frame_verify.inventory_slots(
                inventory_call["response"].get("result"),
                start=previous_slot + 1,
                end_inclusive=current_slot,
            )
            selection = sampling.build_selection(
                epoch_id=epoch_id,
                bucket=window["bucket"],
                inventory_slots=inventory,
                inventory_sha256=inventory_call["response_sha256"],
            )
            selection_path = f"{base}/selections/{window['bucket']}.json"
            selection_bytes = _canonical(selection)
            if _safe_file_exists(writer, selection_path):
                if writer.read_bytes(selection_path) != selection_bytes:
                    raise ReleaseError("r2_selection_replay_mismatch")
            else:
                writer.create(selection_path, selection_bytes)
            pairs = []
            for sample_index, slot in enumerate(selection["selected_slots"]):
                signature_call = dispatch(
                    2 + sample_index * 2,
                    "getBlock",
                    [
                        slot,
                        {
                            "commitment": "finalized",
                            "encoding": "json",
                            "transactionDetails": "signatures",
                            "rewards": False,
                        },
                    ],
                )
                full_call = dispatch(
                    3 + sample_index * 2,
                    "getBlock",
                    [
                        slot,
                        {
                            "commitment": "finalized",
                            "encoding": "json",
                            "transactionDetails": "full",
                            "rewards": False,
                            "maxSupportedTransactionVersion": 1,
                        },
                    ],
                )
                pairs.append(
                    {
                        "slot": slot,
                        "signatures": signature_call["response"].get("result"),
                        "full": full_call["response"].get("result"),
                    }
                )
            frame_verify.verify_sample_pairs(
                epoch_id=epoch_id,
                bucket=window["bucket"],
                inventory=inventory,
                inventory_sha256=inventory_call["response_sha256"],
                pairs=pairs,
            )
    event_bytes = writer.read_bytes(events_path, max_bytes=MAX_BYTES)
    request_ids = [
        event["fields"]["request_id"]
        for event in v5_schema.verify_event_chain(event_bytes, schema=v5_schema.EVENT_SCHEMA)
        if event["kind"] == "request_intent" and event["fields"].get("bucket") == window["bucket"]
    ]
    _append_r2_event(
        writer,
        epoch_id=epoch_id,
        kind="frame_sealed",
        at_ns=now_ns(),
        fields={
            "index": window_index,
            "bucket": window["bucket"],
            "request_ids": request_ids,
            "request_count": len(request_ids),
            "empty_interval": empty_interval,
        },
    )
    return {
        "status": "window_processed",
        "provider_io": bool(dispatched),
        "attempted": attempted,
        "dispatched": dispatched,
        "bucket": window["bucket"],
        "empty_interval": empty_interval,
    }


def verify_plan(
    plan: Mapping[str, Any],
    *,
    expected_sources: Mapping[str, str],
    root_id: str,
    volume_uuid: str,
    cutover_sha256: str,
) -> None:
    """Reject r2 source drift and ensure a plan cannot enter the old v5 root."""
    if (
        plan.get("schema") != "brain-senses-external-rolling-v5r2-plan"
        or plan.get("schema_version") != 1
        or plan.get("release_id") != RELEASE_ID
        or plan.get("service_id") != RELEASE_ID
        or plan.get("source_bindings") != dict(sorted(expected_sources.items()))
        or plan.get("cutover_usage_sha256") != cutover_sha256
    ):
        raise ReleaseError("r2_plan_identity_invalid")
    storage = plan.get("storage")
    epoch = v5_schema.validate_epoch_id(plan.get("epoch_id"))
    if not isinstance(storage, dict) or storage != {
        "root_id": root_id,
        "volume_uuid": volume_uuid.lower(),
        "relative_root": f"{RELEASE_RELATIVE_ROOT}/epochs/{epoch}",
    }:
        raise ReleaseError("r2_plan_storage_binding_invalid")
    if type(plan.get("anchor_ns")) is not int or plan["anchor_ns"] < MIN_R2_ANCHOR_NS:
        raise ReleaseError("r2_plan_anchor_precedes_cutover")
    # Reconstruct every field independently, keeping the nested root and r2
    # identity outside the old v5 verifier.
    base = v5_schema.build_plan(
        epoch=epoch,
        created_ns=plan["created_ns"],
        anchor_ns=plan["anchor_ns"],
        root_id=root_id,
        volume_uuid=volume_uuid,
        cutover_usage_sha256=cutover_sha256,
        source_bindings=expected_sources,
    )
    expected = {
        **base,
        "schema": "brain-senses-external-rolling-v5r2-plan",
        "schema_version": 1,
        "service_id": RELEASE_ID,
        "release_id": RELEASE_ID,
        "storage": {**base["storage"], "relative_root": f"{RELEASE_RELATIVE_ROOT}/epochs/{epoch}"},
    }
    if dict(plan) != expected:
        raise ReleaseError("r2_plan_schedule_or_policy_invalid")


def _verify_plist(path: Path) -> bool:
    expected = (
        Path(__file__).resolve().parents[1]
        / "ops/launchd/ai.memecoin.brain.senses.external-rolling-v5r2.plist"
    )
    if path.resolve() != expected.resolve():
        return False
    try:
        value = plistlib.loads(expected.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return False
    args = [
        str(expected.parents[2] / ".venv/bin/python"),
        "-B",
        str(Path(__file__).resolve()),
        "--preflight",
        "--plist",
        str(expected),
    ]
    return (
        isinstance(value, dict)
        and value.get("Label") == "ai.memecoin.brain.senses.external-rolling-v5r2"
        and value.get("ProgramArguments") == args
        and value.get("RunAtLoad") is False
        and value.get("KeepAlive") is False
        and value.get("StandardOutPath") == "/dev/null"
        and value.get("StandardErrorPath") == "/dev/null"
    )


def main(argv: list[str] | None = None) -> int:
    """Run the offline cutover verifier. It never dispatches provider requests."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path.home() / "Developer/memecoin-runtime/brain/config/storage-v1.json",
    )
    parser.add_argument("--plist", type=Path, required=True)
    args = parser.parse_args(argv)
    expected_python = args.plist.resolve().parents[2] / ".venv/bin/python"
    if (
        not args.preflight
        or not _verify_plist(args.plist)
        or Path(sys.executable).resolve() != expected_python.resolve()
    ):
        return 2
    try:
        config = StorageConfig.load(args.config)
        mount_fd = _open_directory(config.mount_path)
        try:
            lock_stat = os.stat(".research-storage.lock", dir_fd=mount_fd, follow_symlinks=False)
            if not stat.S_ISREG(lock_stat.st_mode):
                return 1
        finally:
            os.close(mount_fd)
        with writer_guard(args.config) as guard:
            root_fd = _open_relative_directory(guard.mount_fd, config.data_root)
            try:
                with DescriptorWriter(root_fd) as reader:
                    now_ns = time.time_ns()
                    receipt = build_cutover_receipt(
                        reader,
                        root_id=config.root_id,
                        volume_uuid=config.volume_uuid,
                        generated_ns=now_ns,
                        now_ns=now_ns,
                    )
                    sys.stdout.buffer.write(receipt)
            finally:
                os.close(root_fd)
    except (OSError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
