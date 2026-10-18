"""One-case usage replay for the preserved Gecko capture-only v4 clock orphan.

This module cannot repair, relabel, or authorize the legacy root. It only permits the exact
recorded root and orphan clock when the full root prefix, source pins, missed-slot skips, and normal
v4 request replay all match the immutable reconciliation receipt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from tools import brain_senses_capacity_coordinator_v2 as capacity
from tools import brain_senses_external_clock_v1 as clock
from tools import brain_senses_gecko_capture_only_runtime_v1 as capture_runtime
from tools import brain_senses_gecko_interpreter_handoff_v1 as interpreter_handoff
from tools import brain_senses_gecko_lane_live_sources_v2 as live_sources
from tools import brain_senses_gecko_lane_policy_v4 as policy_v4
from tools import brain_senses_gecko_lane_runtime_v4 as runtime_v4
from tools import brain_senses_gecko_lane_verify_v5 as verify_v5
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json

REPO = Path(__file__).resolve().parents[1]
SCHEMA = "brain-senses-gecko-v4-usage-reconciliation-v1"
SOURCE_COMMIT = "8e54478727c21572ceb654b689a31e97e11463c3"
ROOT = "services/gecko-market-v1/capture-only-v1/f4412b7a531d4e3cb3414a2862b8ee76"
ORPHAN_RELATIVE_PATH = "clocks/1791844200000000000-00.json"
ORPHAN_BUCKET_CLOSE_NS = 1_791_844_200_000_000_000
ORPHAN_SHA256 = "e8bd01da09b3b0f999ca745769152cbef4356371b0cc646538a1458e98f6697d"
EXPECTED_CAPTURE_ID = "f4412b7a531d4e3cb3414a2862b8ee76"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
RECEIPT_PATH = REPO / "docs/evidence/gecko-v4-usage-reconciliation-2026-10-12.json"
RECEIPT_FILE_SHA256 = "c509cb43e9162cb860a7b361c9fbd001a0db0e1335011374a361111072251fad"


class ReconciliationError(ValueError):
    """The one pinned legacy anomaly or its usage replay no longer matches."""


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def root_prefix_sha256(writer: DescriptorWriter, root: str) -> str:
    files: list[dict[str, Any]] = []

    def visit(relative: str) -> None:
        for name in writer.list_names(relative):
            path = f"{relative}/{name}"
            entry = writer.stat(path)
            if entry.st_mode & 0o170000 == 0o040000:
                visit(path)
            else:
                raw = writer.read_bytes(path, max_bytes=32 * 1024 * 1024)
                files.append(
                    {
                        "path": path[len(root) + 1 :],
                        "size": len(raw),
                        "sha256": sha256(raw),
                    }
                )

    visit(root)
    return sha256(canonical_json(files))


def _read_journal(writer: DescriptorWriter, *, storage: Mapping[str, Any]) -> dict[str, Any]:
    store = runtime_v4.DescriptorLaneStore(writer, guard=None, root=ROOT)
    plan_raw = writer.read_bytes(store._path("plan.json"), max_bytes=2 * 1024 * 1024)
    plan = store.read_json("plan.json")
    activation_raw = writer.read_bytes(store._path("activation.json"), max_bytes=64 * 1024)
    activation = store.read_json("activation.json")
    _require(
        plan_raw == canonical_json(plan)
        and plan.get("capture_id") == EXPECTED_CAPTURE_ID
        and plan.get("service_root") == ROOT
        and plan.get("storage")
        == {
            "root_id": storage.get("root_id"),
            "volume_uuid": str(storage.get("volume_uuid", "")).lower(),
        }
        and plan.get("authority")
        == {
            "capture_authorized": False,
            "market_source_qualified": False,
            "training_admission": False,
            "economic_admission": False,
            "provider_io": False,
        },
        "legacy_v4_plan_binding",
    )
    plan_core = dict(plan)
    plan_hash = plan_core.pop("plan_sha256", None)
    _require(plan_hash == sha256(canonical_json(plan_core)), "legacy_v4_plan_hash")
    policy_v4.verify_policy_v4(plan["policy"])
    source_map = plan.get("source_sha256")
    _require(type(source_map) is dict and len(source_map) == 24, "legacy_v4_source_map_shape")
    _require(
        activation_raw == canonical_json(activation)
        and activation.get("schema") == capture_runtime.ACTIVATION_SCHEMA
        and activation.get("schema_version") == 1
        and activation.get("capture_id") == EXPECTED_CAPTURE_ID
        and activation.get("plan_sha256") == plan_hash
        and activation.get("source_sha256") == source_map
        and activation.get("capture_authorized") is True
        and activation.get("market_source_qualified") is False
        and activation.get("training_admission") is False
        and activation.get("economic_admission") is False
        and activation.get("provider_io") is True,
        "legacy_v4_activation_binding",
    )

    intents = _json_rows(store, "intents", max_bytes=8 * 1024 * 1024)
    terminals = _json_rows(store, "results", max_bytes=8 * 1024 * 1024)
    skips = _json_rows(store, "skips", max_bytes=64 * 1024)
    raw_responses: dict[str, bytes] = {}
    for terminal in terminals:
        request_id = terminal.get("request_id")
        _require(type(request_id) is str, "legacy_v4_result_id")
        path = store._path(f"captures/{request_id}.bin")
        if writer.exists(path):
            raw_responses[cast(str, request_id)] = writer.read_bytes(
                path, max_bytes=32 * 1024 * 1024
            )
    dispatch = store.read_dispatch_records()
    confirmations = store.read_confirmation_records()
    clock_files = {
        f"{ROOT}/clocks/{name}": writer.read_bytes(
            store._path(f"clocks/{name}"), max_bytes=64 * 1024
        )
        for name in store.files("clocks")
        if name.endswith(".json")
    }
    return {
        "store": store,
        "plan": plan,
        "plan_sha256": cast(str, plan_hash),
        "source_map": source_map,
        "intents": intents,
        "terminals": terminals,
        "skips": skips,
        "raw_responses": raw_responses,
        "dispatch": dispatch,
        "confirmations": confirmations,
        "clock_files": clock_files,
    }


def replay_usage_root(
    writer: DescriptorWriter,
    *,
    root_inventory_sha256: str,
    receipt: Mapping[str, Any],
    storage: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reconcile the exact orphan and return a normal shared-usage snapshot plus replay summary."""
    _verify_receipt_shape(receipt)
    root_stat = os.fstat(writer.root_fd)
    _require(
        receipt["root"] == ROOT
        and receipt["storage"]
        == {
            "root_id": storage.get("root_id"),
            "volume_uuid": str(storage.get("volume_uuid", "")).lower(),
            "device": root_stat.st_dev,
            "inode": root_stat.st_ino,
        },
        "legacy_v4_receipt_storage_binding",
    )
    journal = _read_journal(writer, storage=storage)
    _require(
        journal["plan_sha256"] == receipt["plan_sha256"]
        and sha256(canonical_json(journal["source_map"])) == receipt["source_map_sha256"]
        and journal["plan"]["policy_sha256"] == receipt["policy_sha256"],
        "legacy_v4_receipt_plan_binding",
    )
    actual_prefix = root_prefix_sha256(writer, ROOT)
    _require(actual_prefix == receipt["root_prefix_sha256"], "legacy_v4_root_prefix_changed")

    intents = journal["intents"]
    terminals = journal["terminals"]
    dispatch = journal["dispatch"]
    skips = journal["skips"]
    confirmations = journal["confirmations"]
    orphan_record, policy_pool_ids = _verify_orphan_evidence(journal)
    _require(
        receipt["orphan_clock"]["checked_ns"] == orphan_record["checked_ns"]
        and receipt["orphan_bucket_pool_ids"] == policy_pool_ids,
        "legacy_v4_orphan_receipt_binding",
    )

    orphan_path = f"{ROOT}/{ORPHAN_RELATIVE_PATH}"
    usable_clocks = {
        path: raw for path, raw in journal["clock_files"].items() if path != orphan_path
    }
    closes = sorted(
        {
            row["request"]["bucket_close_ns"]
            for row in intents
            if isinstance(row.get("request"), Mapping)
        }
        | {row["bucket_close_ns"] for row in skips if type(row.get("bucket_close_ns")) is int}
    )
    try:
        summary = verify_v5.verify_journal_v5(
            journal["plan"]["policy"],
            intents,
            terminals,
            journal["raw_responses"],
            dispatch,
            root=ROOT,
            clock_files=usable_clocks,
            skips=skips,
            scheduled_closes_ns=closes,
            confirmations=confirmations,
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise ReconciliationError("legacy_v4_attempt_replay_failed") from exc
    _require(summary.get("status") == "verified_prefix", "legacy_v4_attempt_replay_status")
    attempts = _attempt_summaries(intents, terminals)
    _require(
        summary["intents"] == receipt["replay"]["intents"]
        and summary["dispatched"] == receipt["replay"]["dispatches"]
        and summary["terminal_count"] == receipt["replay"]["terminal_results"]
        and summary["confirmed_result_count"] == receipt["replay"]["confirmations"]
        and summary["raw_bytes"] == receipt["replay"]["raw_bytes"]
        and summary["unresolved_spent"] == receipt["replay"]["unresolved_spent"]
        and len(attempts) == summary["intents"],
        "legacy_v4_receipt_replay_summary_mismatch",
    )
    _require(
        _verify_coordinator(writer, storage=storage, intents=intents, terminals=terminals)
        == receipt["coordinator_root_event_sha256"],
        "legacy_v4_receipt_coordinator_mismatch",
    )
    dispatch_markers = [
        {
            "request_id": row["request_id"],
            "dispatch_ns": row["dispatch_ns"],
            "record_sha256": row["record_sha256"],
        }
        for row in dispatch
    ]
    _require(dispatch_markers == receipt["dispatch_markers"], "legacy_v4_receipt_dispatch_mismatch")
    snapshot = shared_usage.make_verified_root_snapshot(
        root=ROOT,
        status="verified_prefix" if attempts else "verified_none",
        root_inventory_sha256=root_inventory_sha256,
        source_prefix_sha256=actual_prefix,
        attempts=attempts,
    )
    return snapshot, summary


def build_reconciliation_receipt(
    writer: DescriptorWriter,
    *,
    storage: Mapping[str, Any],
    observed_ns: int,
) -> dict[str, Any]:
    """Build the immutable cutover receipt after verifying the pinned historical source closure."""
    _require(type(observed_ns) is int and observed_ns > 0, "legacy_v4_observed_time")
    commit = subprocess.run(
        ["git", "rev-parse", f"{SOURCE_COMMIT}^{{commit}}"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    _verify_historical_source_closure(writer, storage=storage, commit=commit)
    journal = _read_journal(writer, storage=storage)
    root_stat = os.fstat(writer.root_fd)
    root_prefix = root_prefix_sha256(writer, ROOT)
    orphan_path = f"{ROOT}/{ORPHAN_RELATIVE_PATH}"
    orphan_raw = journal["clock_files"].get(orphan_path)
    _require(
        type(orphan_raw) is bytes and sha256(orphan_raw) == ORPHAN_SHA256,
        "legacy_v4_orphan_clock_hash",
    )
    orphan_record = json.loads(orphan_raw)
    clock.validate_clock(orphan_record)
    orphan_record, policy_pool_ids = _verify_orphan_evidence(journal)
    receipt_body: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": 1,
        "source_commit": commit,
        "root": ROOT,
        "storage": {
            "root_id": storage["root_id"],
            "volume_uuid": str(storage["volume_uuid"]).lower(),
            "device": root_stat.st_dev,
            "inode": root_stat.st_ino,
        },
        "plan_sha256": journal["plan_sha256"],
        "policy_sha256": journal["plan"]["policy_sha256"],
        "source_map_sha256": sha256(canonical_json(journal["source_map"])),
        "root_prefix_sha256": root_prefix,
        "orphan_clock": {
            "relative_path": ORPHAN_RELATIVE_PATH,
            "sha256": sha256(orphan_raw),
            "size_bytes": len(orphan_raw),
            "bucket_close_ns": ORPHAN_BUCKET_CLOSE_NS,
            "checked_ns": orphan_record["checked_ns"],
            "semantics": "validated clock metadata with no intent/dispatch reference",
        },
        "orphan_bucket_pool_ids": policy_pool_ids,
        "observed_ns": observed_ns,
        "dispatch_markers": [
            {
                "request_id": row["request_id"],
                "dispatch_ns": row["dispatch_ns"],
                "record_sha256": row["record_sha256"],
            }
            for row in journal["dispatch"]
        ],
        "replay": {
            "status": "verified_prefix",
            "intents": 0,
            "dispatches": 0,
            "terminal_results": 0,
            "confirmations": 0,
            "raw_bytes": 0,
            "unresolved_spent": 0,
        },
        "provider_charge_status": "unknown_unverified",
    }
    try:
        no_orphan_clocks = {
            path: raw for path, raw in journal["clock_files"].items() if path != orphan_path
        }
        closes = sorted(
            {
                row["request"]["bucket_close_ns"]
                for row in journal["intents"]
                if isinstance(row.get("request"), Mapping)
            }
            | {
                row["bucket_close_ns"]
                for row in journal["skips"]
                if type(row.get("bucket_close_ns")) is int
            }
        )
        summary = verify_v5.verify_journal_v5(
            journal["plan"]["policy"],
            journal["intents"],
            journal["terminals"],
            journal["raw_responses"],
            journal["dispatch"],
            root=ROOT,
            clock_files=no_orphan_clocks,
            skips=journal["skips"],
            scheduled_closes_ns=closes,
            confirmations=journal["confirmations"],
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise ReconciliationError("legacy_v4_attempt_replay_failed") from exc
    receipt_body["replay"] = {
        "status": summary["status"],
        "intents": summary["intents"],
        "dispatches": summary["dispatched"],
        "terminal_results": summary["terminal_count"],
        "confirmations": summary["confirmed_result_count"],
        "raw_bytes": summary["raw_bytes"],
        "unresolved_spent": summary["unresolved_spent"],
    }
    _require(summary.get("status") == "verified_prefix", "legacy_v4_attempt_replay_status")
    receipt_body["coordinator_root_event_sha256"] = _verify_coordinator(
        writer,
        storage=storage,
        intents=journal["intents"],
        terminals=journal["terminals"],
    )
    receipt_body["receipt_sha256"] = sha256(canonical_json(receipt_body))
    _verify_receipt_shape(receipt_body)
    return receipt_body


def load_reconciliation_receipt() -> dict[str, Any]:
    raw = RECEIPT_PATH.read_bytes()
    _require(
        RECEIPT_FILE_SHA256 != "PENDING" and sha256(raw) == RECEIPT_FILE_SHA256,
        "legacy_v4_receipt_file_hash",
    )
    try:
        receipt = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReconciliationError("legacy_v4_receipt_json") from exc
    _require(
        type(receipt) is dict and canonical_json(receipt) == raw,
        "legacy_v4_receipt_canonical",
    )
    _verify_receipt_shape(receipt)
    return cast(dict[str, Any], receipt)


def _verify_historical_source_closure(
    writer: DescriptorWriter, *, storage: Mapping[str, Any], commit: str
) -> None:
    journal = _read_journal(writer, storage=storage)
    source_map = journal["source_map"]
    _require(commit == SOURCE_COMMIT, "legacy_v4_source_commit")
    live_manifest_path = runtime_v4.SOURCE_BINDING_KEY
    pinned = live_sources.verify_source_binding(
        writer,
        expected_root_id=str(storage["root_id"]),
        expected_volume_uuid=str(storage["volume_uuid"]).lower(),
    )
    _require(
        sha256(writer.read_bytes(live_manifest_path, max_bytes=1_000_000))
        == source_map[live_manifest_path]
        == pinned.manifest_sha256,
        "legacy_v4_source_manifest_hash",
    )
    handoff_path = capture_runtime.HANDOFF_PATH
    handoff, handoff_raw = interpreter_handoff.load_receipt(
        str(handoff_path), owner_uid=os.getuid()
    )
    interpreter_handoff.verify_receipt(
        handoff,
        plist_path=str(capture_runtime.PLIST_PATH),
        expected_label=capture_runtime.LABEL,
        expected_argv=capture_runtime._expected_argv(EXPECTED_CAPTURE_ID),
        owner_uid=os.getuid(),
    )
    _require(
        sha256(handoff_raw) == source_map[capture_runtime.HANDOFF_SOURCE_KEY],
        "legacy_v4_handoff_source_hash",
    )
    for path, expected in source_map.items():
        if path in {live_manifest_path, capture_runtime.HANDOFF_SOURCE_KEY}:
            continue
        result = subprocess.run(
            ["git", "show", f"{commit}:{path}"],
            cwd=REPO,
            check=True,
            capture_output=True,
            timeout=10,
        )
        _require(sha256(result.stdout) == expected, "legacy_v4_source_pin_mismatch")


def _verify_coordinator(
    writer: DescriptorWriter,
    *,
    storage: Mapping[str, Any],
    intents: Sequence[Mapping[str, Any]],
    terminals: Sequence[Mapping[str, Any]],
) -> str:
    try:
        with writer.lock(capacity.LOCK_PATH, blocking=False):
            policy_raw = writer.read_bytes(capacity.POLICY_PATH, max_bytes=1_000_000)
            policy = json.loads(policy_raw)
            ledger_raw = writer.read_bytes(capacity.LEDGER_PATH, max_bytes=64_000_000)
            events = capacity._verify_ledger(ledger_raw)
    except (OSError, ValueError, KeyError, TypeError, capacity.CoordinatorError) as exc:
        raise ReconciliationError("legacy_v4_coordinator_replay_failed") from exc
    root_stat = os.fstat(writer.root_fd)
    _require(
        canonical_json(policy) == policy_raw
        and policy.get("root_binding")
        == {
            "root_id": storage.get("root_id"),
            "volume_uuid": str(storage.get("volume_uuid", "")).lower(),
            "device": root_stat.st_dev,
            "inode": root_stat.st_ino,
        },
        "legacy_v4_coordinator_policy_binding",
    )
    reservations, terminal_reservations = capacity._reservation_state(events)
    intent_by_id = {str(row["request_id"]): row for row in intents}
    result_by_id = {str(row["request_id"]): row for row in terminals}
    relevant: list[dict[str, Any]] = []
    found_ids: set[str] = set()
    for reservation_id, fields in reservations.items():
        if fields.get("intent_path", "").startswith(f"{ROOT}/intents/"):
            found_ids.add(str(fields.get("request_id")))
            intent_id = str(fields.get("request_id"))
            intent_path = f"{ROOT}/intents/{intent_id}.json"
            outcome_path = f"{ROOT}/results/{intent_id}.json"
            intent_raw = writer.read_bytes(intent_path, max_bytes=8 * 1024 * 1024)
            _require(
                fields.get("service") == "gecko"
                and fields.get("reservation_id") == reservation_id
                and fields.get("request_id") == intent_id
                and fields.get("intent_path") == intent_path
                and fields.get("outcome_path") == outcome_path
                and fields.get("intent_sha256") == sha256(intent_raw)
                and intent_id in intent_by_id,
                "legacy_v4_coordinator_intent_binding",
            )
            terminal_event = next(
                (
                    event
                    for event in events
                    if event["kind"] in {"settled", "ambiguous_terminal"}
                    and event["fields"].get("reservation_id") == reservation_id
                ),
                None,
            )
            result_raw = writer.read_bytes(outcome_path, max_bytes=8 * 1024 * 1024)
            _require(
                reservation_id in terminal_reservations
                and terminal_event is not None
                and terminal_event["fields"].get("outcome_sha256") == sha256(result_raw)
                and intent_id in result_by_id,
                "legacy_v4_coordinator_terminal_binding",
            )
            relevant.append(
                {
                    "reservation_id": reservation_id,
                    "intent_sha256": sha256(intent_raw),
                    "outcome_sha256": sha256(result_raw),
                    "terminal_event_sha256": (
                        terminal_event.get("event_sha256") if terminal_event else None
                    ),
                }
            )
    _require(found_ids == set(intent_by_id) == set(result_by_id), "legacy_v4_coordinator_closure")
    return sha256(canonical_json(relevant))


def _verify_orphan_evidence(journal: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    orphan_path = f"{ROOT}/{ORPHAN_RELATIVE_PATH}"
    clocks = cast(dict[str, bytes], journal["clock_files"])
    orphan_raw = clocks.get(orphan_path)
    _require(
        type(orphan_raw) is bytes and sha256(orphan_raw) == ORPHAN_SHA256,
        "legacy_v4_orphan_clock_hash",
    )
    orphan_raw = cast(bytes, orphan_raw)
    try:
        orphan_record = json.loads(orphan_raw)
        _require(canonical_json(orphan_record) == orphan_raw, "legacy_v4_orphan_clock_json")
        clock.validate_clock(orphan_record)
    except (UnicodeDecodeError, json.JSONDecodeError, clock.ClockError) as exc:
        raise ReconciliationError("legacy_v4_orphan_clock_invalid") from exc
    intents = cast(list[dict[str, Any]], journal["intents"])
    terminals = cast(list[dict[str, Any]], journal["terminals"])
    dispatch = cast(list[dict[str, Any]], journal["dispatch"])
    skips = cast(list[dict[str, Any]], journal["skips"])
    referenced_clocks = {row.get("clock_path") for row in intents}
    orphan_skips = [row for row in skips if row.get("bucket_close_ns") == ORPHAN_BUCKET_CLOSE_NS]
    policy_pool_ids = sorted(
        cast(str, row["pool_id"]) for row in journal["plan"]["policy"]["pools"]
    )
    orphan_skip_pool_ids = [row.get("pool_id") for row in orphan_skips]
    request_ids_for_bucket = {
        row.get("request_id")
        for row in intents
        if row.get("request", {}).get("bucket_close_ns") == ORPHAN_BUCKET_CLOSE_NS
    }
    _require(
        sorted(set(clocks) - referenced_clocks) == [orphan_path]
        and not request_ids_for_bucket
        and not any(row.get("request_id") in request_ids_for_bucket for row in dispatch)
        and not any(row.get("request_id") in request_ids_for_bucket for row in terminals)
        and all(type(pool_id) is str for pool_id in orphan_skip_pool_ids)
        and sorted(cast(list[str], orphan_skip_pool_ids)) == policy_pool_ids
        and all(row.get("reason") == "missed_dispatch" for row in orphan_skips)
        and all(type(row.get("at_ns")) is int for row in orphan_skips)
        and min(row["at_ns"] for row in orphan_skips) >= orphan_record["checked_ns"]
        and max(row["at_ns"] for row in orphan_skips) - orphan_record["checked_ns"]
        <= 10_000_000_000,
        "legacy_v4_orphan_not_exact_all_skipped_bucket",
    )
    return cast(dict[str, Any], orphan_record), policy_pool_ids


def _attempt_summaries(
    intents: Sequence[Mapping[str, Any]], terminals: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    terminal_by_id = {row["request_id"]: row for row in terminals}
    attempts = []
    for intent in intents:
        request_id = intent.get("request_id")
        decision_ns = intent.get("decision_ns")
        _require(type(request_id) is str and type(decision_ns) is int, "legacy_v4_usage_shape")
        terminal = terminal_by_id.get(cast(str, request_id))
        response_sha = terminal.get("response_sha256") if terminal is not None else None
        response_bytes = terminal.get("response_bytes") if terminal is not None else None
        attempts.append(
            {
                "request_id": request_id,
                "decision_ns": decision_ns,
                "maximum_response_bytes": 16 * 1024,
                "response_bytes": response_bytes if response_sha is not None else None,
                "response_sha256": response_sha if response_sha is not None else None,
            }
        )
    return sorted(attempts, key=lambda row: (row["decision_ns"], row["request_id"]))


def _json_rows(store: Any, directory: str, *, max_bytes: int) -> list[dict[str, Any]]:
    names = store.files(directory)
    _require(all(name.endswith(".json") for name in names), "legacy_v4_journal_file_closure")
    result = []
    for name in names:
        raw = store.writer.read_bytes(store._path(f"{directory}/{name}"), max_bytes=max_bytes)
        try:
            row = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReconciliationError("legacy_v4_journal_json") from exc
        _require(type(row) is dict and canonical_json(row) == raw, "legacy_v4_journal_canonical")
        result.append(row)
    return result


def _verify_receipt_shape(receipt: Mapping[str, Any]) -> None:
    expected = {
        "schema",
        "schema_version",
        "source_commit",
        "root",
        "storage",
        "plan_sha256",
        "policy_sha256",
        "source_map_sha256",
        "root_prefix_sha256",
        "orphan_clock",
        "orphan_bucket_pool_ids",
        "observed_ns",
        "dispatch_markers",
        "replay",
        "provider_charge_status",
        "coordinator_root_event_sha256",
        "receipt_sha256",
    }
    core = dict(receipt)
    claimed = core.pop("receipt_sha256", None)
    _require(
        set(receipt) == expected
        and receipt.get("schema") == SCHEMA
        and receipt.get("schema_version") == 1
        and receipt.get("source_commit") == SOURCE_COMMIT
        and receipt.get("root") == ROOT
        and type(receipt.get("storage")) is dict
        and set(cast(dict[str, Any], receipt.get("storage")))
        == {"root_id", "volume_uuid", "device", "inode"}
        and type(receipt.get("observed_ns")) is int
        and all(
            type(receipt.get(key)) is str and HASH_RE.fullmatch(cast(str, receipt.get(key)))
            for key in ("plan_sha256", "policy_sha256", "source_map_sha256", "root_prefix_sha256")
        )
        and type(claimed) is str
        and claimed == sha256(canonical_json(core))
        and receipt.get("orphan_clock")
        == {
            "relative_path": ORPHAN_RELATIVE_PATH,
            "sha256": ORPHAN_SHA256,
            "size_bytes": 606,
            "bucket_close_ns": ORPHAN_BUCKET_CLOSE_NS,
            "checked_ns": 1_791_844_431_034_498_000,
            "semantics": "validated clock metadata with no intent/dispatch reference",
        }
        and type(receipt.get("orphan_bucket_pool_ids")) is list
        and type(receipt.get("dispatch_markers")) is list
        and type(receipt.get("replay")) is dict
        and set(cast(dict[str, Any], receipt.get("replay")))
        == {
            "status",
            "intents",
            "dispatches",
            "terminal_results",
            "confirmations",
            "raw_bytes",
            "unresolved_spent",
        }
        and cast(dict[str, Any], receipt.get("replay")).get("status") == "verified_prefix"
        and all(
            _is_nonnegative_int(cast(dict[str, Any], receipt.get("replay")).get(name))
            for name in (
                "intents",
                "dispatches",
                "terminal_results",
                "confirmations",
                "raw_bytes",
                "unresolved_spent",
            )
        )
        and all(
            type(row) is dict
            and set(row) == {"request_id", "dispatch_ns", "record_sha256"}
            and type(row.get("request_id")) is str
            and type(row.get("dispatch_ns")) is int
            and type(row.get("record_sha256")) is str
            and HASH_RE.fullmatch(cast(str, row.get("record_sha256"))) is not None
            for row in cast(list[Any], receipt.get("dispatch_markers"))
        )
        and receipt.get("provider_charge_status") == "unknown_unverified"
        and type(receipt.get("coordinator_root_event_sha256")) is str
        and HASH_RE.fullmatch(cast(str, receipt.get("coordinator_root_event_sha256"))) is not None,
        "legacy_v4_reconciliation_receipt_invalid",
    )


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise ReconciliationError(reason)


def _is_nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


__all__ = [
    "ORPHAN_BUCKET_CLOSE_NS",
    "ORPHAN_RELATIVE_PATH",
    "ORPHAN_SHA256",
    "ROOT",
    "ReconciliationError",
    "build_reconciliation_receipt",
    "load_reconciliation_receipt",
    "replay_usage_root",
]
