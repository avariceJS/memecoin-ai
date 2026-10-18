"""Offline verification for the narrowly approved Gecko successor handoff."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from tools import brain_senses_gecko_shared_usage_v1 as shared_usage

SCHEMA = "brain-senses-gecko-capture-only-v2-handoff-v1"
JOURNAL_NAME = "handoff-v1.jsonl"
OLD_ROOT = "services/gecko-market-v1/capture-only-v2/3569c27ff9916382c209ea71ef23e345"
OLD_PARTIAL_CLOSE_NS = 1_791_865_800_000_000_000  # 2026-10-13T04:30:00Z
UNPLANNED_GAP_CLOSE_NS = 1_791_866_700_000_000_000  # 2026-10-13T04:45:00Z
BUCKET_NS = 900_000_000_000
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
ID_RE = re.compile(r"[0-9a-f]{32}\Z")
UUID_RE = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


class HandoffError(ValueError):
    """A successor handoff is incomplete, inconsistent, or not source-bound."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise HandoffError(reason)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json(value: Any) -> bytes:
    return shared_usage.canonical_json(value)


def _hash(value: Any) -> str:
    return _sha(canonical_json(value))


def _record(
    previous_sha256: str | None, sequence: int, kind: str, fields: Mapping[str, Any]
) -> dict[str, Any]:
    core = {
        "schema": SCHEMA,
        "schema_version": 1,
        "sequence": sequence,
        "kind": kind,
        "previous_sha256": previous_sha256,
        "fields": dict(fields),
    }
    return {**core, "record_sha256": _hash(core)}


def encode_journal(records: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(canonical_json(dict(row)) for row in records)


def make_journal(
    genesis_fields: Mapping[str, Any],
    partial_fields: Mapping[str, Any],
    gap_fields: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> bytes:
    first = _record(None, 0, "genesis", genesis_fields)
    second = _record(first["record_sha256"], 1, "partial_plan_cutover", partial_fields)
    gaps = (
        [dict(gap_fields)] if isinstance(gap_fields, Mapping) else [dict(row) for row in gap_fields]
    )
    rows = [first, second]
    previous = second["record_sha256"]
    for offset, gap in enumerate(gaps, start=2):
        gap["predecessor_record_sha256"] = previous
        row = _record(previous, offset, "unplanned_bucket_gap", gap)
        rows.append(row)
        previous = row["record_sha256"]
    return encode_journal(rows)


def _parse_journal(raw: bytes) -> list[dict[str, Any]]:
    _require(type(raw) is bytes and 0 < len(raw) <= 256 * 1024, "handoff_journal_size")
    _require(raw.endswith(b"\n"), "handoff_journal_torn")
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HandoffError("handoff_journal_json") from exc
        _require(type(row) is dict and canonical_json(row) == line + b"\n", "handoff_not_canonical")
        rows.append(row)
    _require(len(rows) >= 3, "handoff_record_count")
    previous: str | None = None
    for sequence, row in enumerate(rows):
        _require(
            set(row)
            == {
                "schema",
                "schema_version",
                "sequence",
                "kind",
                "previous_sha256",
                "fields",
                "record_sha256",
            }
            and row.get("schema") == SCHEMA
            and row.get("schema_version") == 1
            and row.get("sequence") == sequence
            and row.get("previous_sha256") == previous
            and type(row.get("fields")) is dict,
            "handoff_record_shape",
        )
        core = dict(row)
        claimed = core.pop("record_sha256")
        _require(type(claimed) is str and claimed == _hash(core), "handoff_record_hash")
        previous = claimed
    _require(
        [row["kind"] for row in rows[:2]] == ["genesis", "partial_plan_cutover"]
        and all(row["kind"] == "unplanned_bucket_gap" for row in rows[2:]),
        "handoff_record_order",
    )
    return rows


def _valid_hash(value: Any) -> bool:
    return type(value) is str and HASH_RE.fullmatch(value) is not None


def _verify_genesis(
    fields: Mapping[str, Any], *, expected: Mapping[str, Any], successor_root: str
) -> None:
    _require(
        set(fields)
        == {
            "successor_root",
            "successor_release_id",
            "source_sha256",
            "policy_sha256",
            "population_sha256",
            "storage",
            "predecessor_root",
            "predecessor_file_prefix_sha256",
            "predecessor_chain_tail_sha256",
            "predecessor_attempt_tail_sha256",
            "predecessor_snapshot_sha256",
            "preinit_census_sha256",
            "preinit_snapshot_set_sha256",
            "coordinator_prefix_sha256",
            "coordinator_usage_sha256",
            "clock_proof_sha256",
            "old_agent_quiescence_sha256",
            "old_agent_absent",
            "provider_io",
            "cursor_in",
            "cursor_out",
        },
        "handoff_genesis_shape",
    )
    _require(dict(fields) == dict(expected), "handoff_genesis_binding_mismatch")
    release_id = successor_root.rsplit("/", 1)[-1]
    _require(
        ID_RE.fullmatch(release_id) is not None
        and successor_root.startswith("services/gecko-market-v1/capture-only-v2/")
        and successor_root != OLD_ROOT,
        "handoff_successor_root_invalid",
    )
    required_hashes = (
        "source_sha256",
        "policy_sha256",
        "population_sha256",
        "predecessor_file_prefix_sha256",
        "predecessor_chain_tail_sha256",
        "predecessor_attempt_tail_sha256",
        "predecessor_snapshot_sha256",
        "preinit_census_sha256",
        "preinit_snapshot_set_sha256",
        "coordinator_prefix_sha256",
        "coordinator_usage_sha256",
        "clock_proof_sha256",
        "old_agent_quiescence_sha256",
    )
    _require(
        all(_valid_hash(fields.get(key)) for key in required_hashes), "handoff_genesis_hash_shape"
    )
    storage = fields.get("storage")
    _require(
        type(storage) is dict
        and set(storage) == {"root_id", "volume_uuid", "device", "inode"}
        and type(storage.get("root_id")) is str
        and ID_RE.fullmatch(storage["root_id"]) is not None
        and type(storage.get("volume_uuid")) is str
        and UUID_RE.fullmatch(storage["volume_uuid"]) is not None,
        "handoff_storage_binding_shape",
    )
    storage_map = cast(dict[str, Any], storage)
    _require(
        type(storage_map.get("device")) is int
        and storage_map["device"] > 0
        and type(storage_map.get("inode")) is int
        and storage_map["inode"] > 0,
        "handoff_storage_identity_invalid",
    )
    _require(
        fields.get("successor_root") == successor_root
        and fields.get("successor_release_id") == release_id
        and fields.get("predecessor_root") == OLD_ROOT
        and fields.get("old_agent_absent") is True
        and fields.get("provider_io") is False
        and fields.get("cursor_in") == 6,
        "handoff_genesis_semantics",
    )


def _verify_partial_cutover(
    fields: Mapping[str, Any],
    *,
    predecessor_attempts: Sequence[Mapping[str, Any]],
    predecessor_slots: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    _require(
        set(fields)
        == {
            "bucket_close_ns",
            "cursor_in",
            "cursor_out",
            "prior_plan_cursor_out",
            "selected_slots",
            "attempts",
            "expired_slots",
        },
        "handoff_partial_shape",
    )
    _require(fields.get("bucket_close_ns") == OLD_PARTIAL_CLOSE_NS, "handoff_partial_bucket")
    _require(
        fields.get("cursor_in") == 6
        and fields.get("cursor_out") == 12
        and fields.get("prior_plan_cursor_out") == 12,
        "handoff_partial_cursor",
    )
    selected = fields.get("selected_slots")
    _require(type(selected) is list and len(selected) == 6, "handoff_selected_slots_count")
    selected_rows = [cast(dict[str, Any], row) for row in cast(list[Any], selected)]
    _require(
        all(
            type(row) is dict
            and set(row) == {"slot_index", "pool_id"}
            and row.get("slot_index") == 6 + index
            and type(row.get("pool_id")) is str
            and bool(row.get("pool_id"))
            for index, row in enumerate(selected_rows)
        )
        and len({row["pool_id"] for row in selected_rows}) == 6,
        "handoff_selected_slots_invalid",
    )
    _require(
        type(predecessor_slots) is list and len(predecessor_slots) == 6, "handoff_predecessor_slots"
    )
    for index, slot in enumerate(predecessor_slots):
        _require(
            type(slot) is dict
            and set(slot)
            == {
                "slot_index",
                "pool_id",
                "intent_present",
                "dispatch_present",
                "skip_present",
                "request_id",
                "terminal_sha256",
                "coordinator_reserved_event_sha256",
                "coordinator_terminal_event_sha256",
                "coordinator_charge_count",
                "outcome",
            }
            and slot.get("slot_index") == 6 + index
            and slot.get("pool_id") == selected_rows[index]["pool_id"],
            "handoff_predecessor_slot_shape",
        )
    attempts = fields.get("attempts")
    _require(type(attempts) is list and len(attempts) == 4, "handoff_attempt_count")
    attempt_rows = [cast(dict[str, Any], row) for row in cast(list[Any], attempts)]
    _require(
        type(predecessor_attempts) is list
        and all(
            type(row) is dict and type(row.get("request_id")) is str for row in predecessor_attempts
        ),
        "handoff_predecessor_attempt_ids",
    )
    by_id = {row["request_id"]: row for row in predecessor_attempts}
    _require(len(by_id) == len(predecessor_attempts), "handoff_predecessor_attempt_ids")
    for index, row in enumerate(attempt_rows):
        _require(
            type(row) is dict
            and set(row)
            == {
                "slot_index",
                "pool_id",
                "request_id",
                "terminal_sha256",
                "coordinator_reserved_event_sha256",
                "coordinator_terminal_event_sha256",
                "coordinator_charge_count",
                "outcome",
            }
            and row.get("slot_index") == 6 + index
            and row.get("pool_id") == selected_rows[index]["pool_id"]
            and type(row.get("request_id")) is str
            and _valid_hash(row.get("terminal_sha256"))
            and _valid_hash(row.get("coordinator_reserved_event_sha256"))
            and _valid_hash(row.get("coordinator_terminal_event_sha256"))
            and row.get("coordinator_charge_count") == 1
            and row.get("outcome") == ("received" if index < 3 else "ambiguous_spent"),
            "handoff_attempt_row_invalid",
        )
        slot = predecessor_slots[index]
        _require(
            slot.get("intent_present") is True
            and slot.get("dispatch_present") is True
            and slot.get("skip_present") is False
            and slot.get("request_id") == row["request_id"]
            and slot.get("terminal_sha256") == row["terminal_sha256"]
            and slot.get("coordinator_reserved_event_sha256")
            == row["coordinator_reserved_event_sha256"]
            and slot.get("coordinator_terminal_event_sha256")
            == row["coordinator_terminal_event_sha256"]
            and slot.get("coordinator_charge_count") == 1
            and slot.get("outcome") == row["outcome"],
            "handoff_attempt_slot_mismatch",
        )
        predecessor = by_id.get(row["request_id"])
        _require(
            type(predecessor) is dict
            and predecessor.get("slot_index") == row["slot_index"]
            and predecessor.get("pool_id") == row["pool_id"]
            and predecessor.get("terminal_sha256") == row["terminal_sha256"]
            and predecessor.get("coordinator_reserved_event_sha256")
            == row["coordinator_reserved_event_sha256"]
            and predecessor.get("coordinator_terminal_event_sha256")
            == row["coordinator_terminal_event_sha256"]
            and predecessor.get("coordinator_charge_count") == 1
            and predecessor.get("outcome") == row["outcome"],
            "handoff_attempt_not_in_verified_predecessor",
        )
    _require(
        len({row["request_id"] for row in attempt_rows}) == 4,
        "handoff_attempt_duplicate",
    )
    expired = fields.get("expired_slots")
    _require(type(expired) is list and len(expired) == 2, "handoff_expired_count")
    expected_expired = selected_rows[4:]
    for slot in predecessor_slots[4:]:
        _require(
            slot.get("intent_present") is False
            and slot.get("dispatch_present") is False
            and slot.get("skip_present") is False
            and slot.get("request_id") is None
            and slot.get("terminal_sha256") is None
            and slot.get("coordinator_reserved_event_sha256") is None
            and slot.get("coordinator_terminal_event_sha256") is None
            and slot.get("coordinator_charge_count") == 0
            and slot.get("outcome") == "expired_without_intent_or_dispatch",
            "handoff_expired_slot_has_old_artifact",
        )
    _require(
        expired
        == [
            {
                **row,
                "intent_present": False,
                "dispatch_present": False,
                "provider_io": False,
                "charged_attempts": 0,
                "disposition": "expired_without_intent_or_dispatch",
            }
            for row in expected_expired
        ],
        "handoff_expired_slots_invalid",
    )
    seen_attempts = {row["request_id"] for row in attempt_rows}
    _require(
        seen_attempts == {row.get("request_id") for row in predecessor_attempts},
        "handoff_predecessor_attempt_set_mismatch",
    )
    return {"selected_slots": selected_rows, "attempts": attempt_rows, "expired_slots": expired}


def _verify_unplanned_gap(
    fields: Mapping[str, Any], *, predecessor_record_sha256: str, expected_close_ns: int
) -> None:
    _require(
        fields
        == {
            "bucket_close_ns": expected_close_ns,
            "next_close_ns": expected_close_ns + BUCKET_NS,
            "potential_opportunities": 6,
            "selected_pool_ids": None,
            "selection_status": "unobserved_no_plan",
            "plan_present": False,
            "intent_count": 0,
            "dispatch_count": 0,
            "provider_io": False,
            "cursor_in": 12,
            "cursor_out": 12,
            "retention_changed": False,
            "predecessor_record_sha256": predecessor_record_sha256,
        },
        "handoff_unplanned_gap_invalid",
    )


def verify_journal(
    raw: bytes,
    *,
    successor_root: str,
    expected_genesis: Mapping[str, Any],
    predecessor_attempts: Sequence[Mapping[str, Any]],
    predecessor_slots: Sequence[Mapping[str, Any]],
    observed_now_ns: int,
) -> dict[str, Any]:
    """Verify exact 04:30 partial cutover and 04:45 no-plan gap; grants no dispatch authority."""
    _require(type(observed_now_ns) is int and observed_now_ns > 0, "handoff_observed_time_invalid")
    rows = _parse_journal(raw)
    genesis = cast(dict[str, Any], rows[0]["fields"])
    _verify_genesis(genesis, expected=expected_genesis, successor_root=successor_root)
    partial = _verify_partial_cutover(
        cast(dict[str, Any], rows[1]["fields"]),
        predecessor_attempts=predecessor_attempts,
        predecessor_slots=predecessor_slots,
    )
    previous = rows[1]
    expected_close = UNPLANNED_GAP_CLOSE_NS
    verified_gaps = []
    for row in rows[2:]:
        _verify_unplanned_gap(
            cast(dict[str, Any], row["fields"]),
            predecessor_record_sha256=previous["record_sha256"],
            expected_close_ns=expected_close,
        )
        if expected_close > observed_now_ns:
            raise HandoffError("handoff_future_gap_record")
        verified_gaps.append(row["record_sha256"])
        previous = row
        expected_close += BUCKET_NS
    expected_gap_count = (observed_now_ns - UNPLANNED_GAP_CLOSE_NS) // BUCKET_NS + 1
    _require(len(verified_gaps) == expected_gap_count, "handoff_elapsed_gap_coverage")
    _require(genesis.get("cursor_out") == 12, "handoff_genesis_cursor_out")
    return {
        "schema": SCHEMA,
        "status": "verified_handoff",
        "successor_root": successor_root,
        "handoff_tail_sha256": rows[-1]["record_sha256"],
        "genesis_sha256": rows[0]["record_sha256"],
        "partial_cutover_sha256": rows[1]["record_sha256"],
        "unplanned_gap_sha256": rows[2]["record_sha256"],
        "unplanned_gap_record_sha256s": verified_gaps,
        "selected_count": 6,
        "attempt_count": 4,
        "received_count": 3,
        "ambiguous_spent_count": 1,
        "expired_without_intent_count": 2,
        "unplanned_gap_count": len(verified_gaps),
        "unplanned_gap_opportunities": 6 * len(verified_gaps),
        "next_unaccounted_close_ns": expected_close,
        "cursor_out": 12,
        "provider_io": False,
        "retry_authorized": False,
        "dispatch_authorized": False,
        **partial,
    }


__all__ = [
    "HandoffError",
    "JOURNAL_NAME",
    "OLD_PARTIAL_CLOSE_NS",
    "OLD_ROOT",
    "SCHEMA",
    "UNPLANNED_GAP_CLOSE_NS",
    "encode_journal",
    "make_journal",
    "verify_journal",
]
