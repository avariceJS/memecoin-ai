#!/usr/bin/env python3
"""Fail-closed synthetic join from Agave attempts through a selected ledger root.

This is an offline consistency verifier. It does not qualify Agave runtime behavior,
market semantics, settlement, or any point-in-time observation.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CONTRACT = "agave_commit_to_root_synthetic_join_v1"
SOURCE_MAP_SHA256 = "2af1c308cae82cb168b1b35e8aca350af51eb94ed821f9b15a37ef486ee9d0f1"
SOURCE_COMMIT = "b6c96e84b10396b92912d4574dae7d03f606da26"
SOURCE_DIFF_SHA256 = "b35b3dba78a4dc90aee3f91827cf1d6467f728d7218d8e3d7c14a861dcc6aa78"
COMPOSITE_PATCHES = ("candidate_position_observer_v5", "token2022_parser_scaffold_corrected_v3")
DISPOSITIONS = {"executed_success", "executed_failed", "fees_only"}
RECEIPT_STAGES = ("attempt", "commit", "freeze", "block", "root")


class JoinError(ValueError):
    """The supplied synthetic records cannot establish a unique consistent join."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _is_int(value: Any) -> bool:
    return type(value) is int


def _required(record: Any, fields: set[str], reason: str) -> Mapping[str, Any]:
    if not isinstance(record, Mapping) or set(record) != fields:
        raise JoinError(reason)
    return record


def _unique(
    rows: list[Mapping[str, Any]], key_fields: tuple[str, ...], reason: str
) -> dict[tuple[Any, ...], Mapping[str, Any]]:
    result: dict[tuple[Any, ...], Mapping[str, Any]] = {}
    for row in rows:
        key = tuple(row[field] for field in key_fields)
        if key in result:
            raise JoinError(reason)
        result[key] = row
    return result


def _table(payload: Mapping[str, Any], name: str, fields: set[str]) -> list[Mapping[str, Any]]:
    rows = payload.get(name)
    if not isinstance(rows, list):
        raise JoinError(f"{name}_missing")
    return [_required(row, fields, f"{name}_row_invalid") for row in rows]


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_rows(
    rows: list[Mapping[str, Any]],
    text_fields: tuple[str, ...],
    nullable_text_fields: tuple[str, ...] = (),
) -> None:
    for row in rows:
        if any(not _nonempty_text(row[field]) for field in text_fields):
            raise JoinError("identifier_or_hash_invalid")
        if any(
            row[field] is not None and not _nonempty_text(row[field])
            for field in nullable_text_fields
        ):
            raise JoinError("nullable_identifier_invalid")


def _verify_time(rows: list[Mapping[str, Any]], decision_ms: Any) -> None:
    if not _is_int(decision_ms) or decision_ms < 0:
        raise JoinError("decision_time_unbounded")
    if not rows:
        raise JoinError("time_receipts_missing")
    _validate_rows(rows, ("receipt_id", "subject_id"))
    keyed = _unique(rows, ("receipt_id",), "receipt_id_duplicate")
    by_stage: dict[str, list[Mapping[str, Any]]] = {stage: [] for stage in RECEIPT_STAGES}
    for row in keyed.values():
        stage = row["stage"]
        lower, upper = row["availability_lower_ms"], row["availability_upper_ms"]
        if not isinstance(stage, str) or stage not in by_stage:
            raise JoinError("time_receipt_stage_invalid")
        if not _is_int(lower) or not _is_int(upper) or lower < 0 or lower > upper:
            raise JoinError("availability_bound_unbounded")
        if upper > decision_ms:
            raise JoinError("availability_after_decision")
        by_stage[stage].append(row)
    for stage in RECEIPT_STAGES:
        subjects = [row["subject_id"] for row in by_stage[stage]]
        if len(subjects) != len(set(subjects)):
            raise JoinError("time_receipt_subject_duplicate")


def verify(payload: Any) -> dict[str, Any]:
    """Return a synthetic consistency verdict, rejecting ambiguous or incomplete joins."""
    top = _required(
        payload,
        {
            "schema",
            "schema_version",
            "source_pin",
            "decision_time_ms",
            "attempts",
            "commit_batches",
            "block_entries",
            "frozen_banks",
            "root_selection",
            "time_receipts",
        },
        "payload_shape_invalid",
    )
    if top["schema"] != CONTRACT or top["schema_version"] != 1:
        raise JoinError("schema_or_version_invalid")
    pin = _required(
        top["source_pin"],
        {"source_map_sha256", "source_commit", "composite_diff_sha256", "composite_patches"},
        "source_pin_invalid",
    )
    if (
        pin["source_map_sha256"] != SOURCE_MAP_SHA256
        or pin["source_commit"] != SOURCE_COMMIT
        or pin["composite_diff_sha256"] != SOURCE_DIFF_SHA256
        or pin["composite_patches"] != list(COMPOSITE_PATCHES)
    ):
        raise JoinError("source_pin_mismatch")

    attempts = _table(
        top,
        "attempts",
        {
            "attempt_id",
            "execution_id",
            "bank_instance_id",
            "transaction_id",
            "signature",
            "message_hash",
            "attempt_status",
            "candidate_trace_id",
            "program_write_disposition",
            "fee_nonce_disposition",
        },
    )
    commits = _table(
        top,
        "commit_batches",
        {
            "commit_batch_id",
            "batch_index",
            "attempt_id",
            "transaction_id",
            "signature",
            "message_hash",
            "commit_disposition",
        },
    )
    entries = _table(
        top,
        "block_entries",
        {
            "bank_instance_id",
            "slot",
            "bank_hash",
            "parent_bank_hash",
            "block_hash",
            "entry_hash",
            "absolute_block_transaction_index",
            "transaction_id",
            "signature",
            "message_hash",
        },
    )
    banks = _table(
        top,
        "frozen_banks",
        {
            "bank_instance_id",
            "slot",
            "bank_hash",
            "parent_bank_hash",
            "frozen",
        },
    )
    roots = _table(
        top, "root_selection", {"root_receipt_id", "root_bank_hash", "root_slot", "selected"}
    )
    times = _table(
        top,
        "time_receipts",
        {
            "receipt_id",
            "stage",
            "subject_id",
            "availability_lower_ms",
            "availability_upper_ms",
        },
    )
    if not attempts or not commits or not entries or not banks:
        raise JoinError("synthetic_tables_empty")
    _validate_rows(
        attempts,
        (
            "attempt_id",
            "execution_id",
            "bank_instance_id",
            "transaction_id",
            "signature",
            "message_hash",
        ),
    )
    _validate_rows(
        commits, ("commit_batch_id", "attempt_id", "transaction_id", "signature", "message_hash")
    )
    _validate_rows(
        entries,
        (
            "bank_instance_id",
            "bank_hash",
            "block_hash",
            "entry_hash",
            "transaction_id",
            "signature",
            "message_hash",
        ),
        ("parent_bank_hash",),
    )
    _validate_rows(banks, ("bank_instance_id", "bank_hash"), ("parent_bank_hash",))
    _validate_rows(roots, ("root_receipt_id", "root_bank_hash"))
    if any(
        not _is_int(row["slot"])
        or row["slot"] < 0
        or not _is_int(row["absolute_block_transaction_index"])
        or row["absolute_block_transaction_index"] < 0
        for row in entries
    ) or any(not _is_int(row["slot"]) or row["slot"] < 0 for row in banks):
        raise JoinError("slot_or_block_index_invalid")
    if any(type(row["frozen"]) is not bool for row in banks) or any(
        type(row["selected"]) is not bool for row in roots
    ):
        raise JoinError("boolean_status_invalid")
    if any(not _is_int(row["batch_index"]) or row["batch_index"] < 0 for row in commits):
        raise JoinError("commit_batch_index_invalid")
    if any(
        not isinstance(row["attempt_status"], str) or row["attempt_status"] not in DISPOSITIONS
        for row in attempts
    ) or any(not isinstance(row["commit_disposition"], str) for row in commits):
        raise JoinError("attempt_or_commit_disposition_invalid")
    _verify_time(times, top["decision_time_ms"])

    attempt_by_id = _unique(attempts, ("attempt_id",), "attempt_id_duplicate")
    _unique(attempts, ("execution_id",), "execution_id_duplicate")
    _unique(attempts, ("bank_instance_id", "transaction_id"), "attempt_transaction_duplicate")
    commit_by_attempt = _unique(commits, ("attempt_id",), "attempt_commit_ambiguous")
    _unique(commits, ("commit_batch_id", "batch_index"), "commit_batch_position_duplicate")
    _unique(
        entries,
        ("bank_instance_id", "block_hash", "absolute_block_transaction_index"),
        "block_position_collision",
    )
    entry_by_bank_tx = _unique(
        entries, ("bank_instance_id", "transaction_id"), "block_transaction_duplicate"
    )
    _unique(entries, ("bank_instance_id", "signature"), "block_signature_duplicate")
    bank_by_id = _unique(banks, ("bank_instance_id",), "bank_instance_duplicate")
    _unique(banks, ("bank_hash",), "bank_hash_not_unique")
    if len(roots) != 1 or roots[0]["selected"] is not True:
        raise JoinError("root_selection_missing_or_ambiguous")
    root = roots[0]
    if root["root_receipt_id"] == "" or not _is_int(root["root_slot"]):
        raise JoinError("root_selection_invalid")
    bank_by_hash = {bank["bank_hash"]: bank for bank in banks}
    selected_root = bank_by_hash.get(root["root_bank_hash"])
    if selected_root is None or selected_root["slot"] != root["root_slot"]:
        raise JoinError("selected_root_bank_missing_or_mismatched")
    if any(bank["frozen"] is not True for bank in banks):
        raise JoinError("unfrozen_bank_present")

    # The selected root's parent chain must be unique, acyclic, and fully represented.
    rooted_hashes: set[str] = set()
    cursor = selected_root
    while True:
        bank_hash = cursor["bank_hash"]
        if bank_hash in rooted_hashes:
            raise JoinError("bank_parent_cycle")
        rooted_hashes.add(bank_hash)
        parent_hash = cursor["parent_bank_hash"]
        if parent_hash is None:
            break
        parent = bank_by_hash.get(parent_hash)
        if parent is None or parent["slot"] >= cursor["slot"]:
            raise JoinError("root_parent_chain_missing_or_invalid")
        cursor = parent

    if set(commit_by_attempt) != set(attempt_by_id):
        raise JoinError("attempt_commit_cardinality_mismatch")
    expected_receipt_subjects = {
        "attempt": {row["attempt_id"] for row in attempts},
        "commit": {row["commit_batch_id"] for row in commits},
        "freeze": {row["bank_instance_id"] for row in banks},
        "block": {row["block_hash"] for row in entries},
        "root": {root["root_receipt_id"]},
    }
    actual_receipt_subjects = {
        stage: {row["subject_id"] for row in times if row["stage"] == stage}
        for stage in RECEIPT_STAGES
    }
    if actual_receipt_subjects != expected_receipt_subjects:
        raise JoinError("time_receipt_subject_coverage_mismatch")
    joined = []
    for (attempt_id,), attempt in attempt_by_id.items():
        status = attempt["attempt_status"]
        commit = commit_by_attempt[(attempt_id,)]
        if status not in DISPOSITIONS or commit["commit_disposition"] != status:
            raise JoinError("attempt_commit_disposition_conflict")
        expected_effects = {
            "executed_success": ("unqualified", "separate_commit_result"),
            "executed_failed": ("rolled_back", "separate_rollback_effect"),
            "fees_only": ("no_executed_trace", "rollback_accounts"),
        }[status]
        if (
            attempt["program_write_disposition"],
            attempt["fee_nonce_disposition"],
        ) != expected_effects:
            raise JoinError("attempt_rollback_effect_type_conflict")
        identity = ("transaction_id", "signature", "message_hash")
        if any(commit[field] != attempt[field] for field in identity):
            raise JoinError("attempt_commit_identity_conflict")
        bank = bank_by_id.get((attempt["bank_instance_id"],))
        if bank is None:
            raise JoinError("attempt_bank_missing")
        candidate_trace_id = attempt["candidate_trace_id"]
        if status == "fees_only":
            if candidate_trace_id is not None:
                raise JoinError("fees_only_candidate_trace_forbidden")
        elif not isinstance(candidate_trace_id, str) or not candidate_trace_id:
            raise JoinError("executed_attempt_trace_missing")
        entry = entry_by_bank_tx.get((bank["bank_instance_id"], attempt["transaction_id"]))
        if entry is None:
            raise JoinError("attempt_block_entry_missing_or_ambiguous")
        if any(entry[field] != attempt[field] for field in identity):
            raise JoinError("attempt_block_identity_conflict")
        if (
            entry["bank_hash"] != bank["bank_hash"]
            or entry["parent_bank_hash"] != bank["parent_bank_hash"]
            or entry["slot"] != bank["slot"]
        ):
            raise JoinError("block_frozen_bank_identity_conflict")
        if entry["bank_hash"] not in rooted_hashes:
            raise JoinError("candidate_bank_not_on_selected_root")
        if (
            not _is_int(entry["absolute_block_transaction_index"])
            or entry["absolute_block_transaction_index"] < 0
        ):
            raise JoinError("absolute_block_index_invalid")
        if (
            not isinstance(entry["block_hash"], str)
            or not entry["block_hash"]
            or not isinstance(entry["entry_hash"], str)
            or not entry["entry_hash"]
        ):
            raise JoinError("block_or_entry_hash_missing")
        if not _is_int(commit["batch_index"]) or commit["batch_index"] < 0:
            raise JoinError("commit_batch_index_invalid")
        joined.append(
            {
                "attempt_id": attempt_id,
                "commit_batch_id": commit["commit_batch_id"],
                "commit_batch_index": commit["batch_index"],
                "bank_instance_id": bank["bank_instance_id"],
                "bank_hash": bank["bank_hash"],
                "block_hash": entry["block_hash"],
                "entry_hash": entry["entry_hash"],
                "absolute_block_transaction_index": entry["absolute_block_transaction_index"],
                "disposition": status,
                "program_write_disposition": attempt["program_write_disposition"],
                "fee_nonce_disposition": attempt["fee_nonce_disposition"],
                "candidate_trace_status": "attempted_only"
                if status == "executed_failed"
                else "unavailable_fees_only"
                if status == "fees_only"
                else "synthetic_trace_bound",
            }
        )
    joined.sort(key=lambda row: row["attempt_id"])
    return {
        "schema": "agave_commit_to_root_synthetic_join_verdict_v1",
        "status": "synthetic_join_consistent",
        "contract": CONTRACT,
        "source_map_sha256": SOURCE_MAP_SHA256,
        "selected_root_bank_hash": root["root_bank_hash"],
        "joined_attempt_count": len(joined),
        "disposition_counts": dict(sorted(Counter(row["disposition"] for row in joined).items())),
        "joins": joined,
        "claims": {
            "source_qualified": False,
            "runtime_law_qualified": False,
            "candidate_position_admitted": False,
            "native_settlement_qualified": False,
            "buyExact25_qualified": False,
            "point_in_time_eligible": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.fixture.read_text(encoding="utf-8"))
        result = verify(payload)
    except (OSError, json.JSONDecodeError, JoinError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
