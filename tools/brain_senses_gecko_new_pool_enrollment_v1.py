"""Read-only replay from Gecko discovery pages to diagnostic pool enrollment.

This is an observation bridge only. It never makes price requests, assigns
training/evaluation roles, opens protected datasets, or claims a complete pool
universe. Protected exclusion remains explicitly unchecked until an approved
membership interface is available.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tools import brain_senses_gecko_new_pool_discovery_runtime_v2 as runtime

SCHEMA = "brain-senses-gecko-new-pool-enrollment-v1"
GAP_SCHEMA = f"{SCHEMA}-gap"
NS = 1_000_000_000
SCAN_NS = 300 * NS


class EnrollmentError(ValueError):
    """Invalid campaign timeline or discovery-to-enrollment replay."""


def _need(ok: bool, reason: str) -> None:
    if not ok:
        raise EnrollmentError(reason)


def _process_sha256() -> str:
    """Hash this exact projector file without following a replaced symlink."""
    path = Path(__file__)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        _need(
            stat.S_ISREG(before.st_mode) and before.st_size <= 8_000_000,
            "enrollment_projector_file",
        )
        chunks = []
        remaining = before.st_size
        while remaining:
            block = os.read(fd, min(1_048_576, remaining))
            _need(bool(block), "enrollment_projector_short_read")
            chunks.append(block)
            remaining -= len(block)
        after = os.fstat(fd)
        _need(
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
            "enrollment_projector_changed_during_read",
        )
        return hashlib.sha256(b"".join(chunks)).hexdigest()
    finally:
        os.close(fd)


def _gap_event(
    *, epoch_index: int, scan_index: int, scheduled_ns: int, reason: str
) -> dict[str, Any]:
    if (
        type(epoch_index) is not int
        or epoch_index < 0
        or type(scan_index) is not int
        or scan_index < 0
        or type(scheduled_ns) is not int
        or scheduled_ns <= 0
        or type(reason) is not str
        or not reason
        or len(reason) > 128
    ):
        raise EnrollmentError("enrollment_gap_input")
    core = {
        "schema": GAP_SCHEMA,
        "schema_version": 1,
        "epoch_index": epoch_index,
        "scan_index": scan_index,
        "scheduled_ns": scheduled_ns,
        "reason": reason,
        "selected_pool_ids": None,
        "cursor_in": None,
        "cursor_out": None,
        "pool_opportunity_count": 0,
    }
    return {**core, "gap_sha256": runtime.sha256(runtime.canonical(core))}


def _verify_gap(
    value: Any, *, epoch_index: int, scan_index: int, scheduled_ns: int
) -> dict[str, Any]:
    _need(type(value) is dict, "enrollment_gap_shape")
    _need(
        set(value)
        == {
            "schema",
            "schema_version",
            "epoch_index",
            "scan_index",
            "scheduled_ns",
            "reason",
            "selected_pool_ids",
            "cursor_in",
            "cursor_out",
            "pool_opportunity_count",
            "gap_sha256",
        },
        "enrollment_gap_shape",
    )
    core = dict(value)
    digest = core.pop("gap_sha256")
    _need(
        core["schema"] == GAP_SCHEMA
        and core["schema_version"] == 1
        and core["epoch_index"] == epoch_index
        and core["scan_index"] == scan_index
        and core["scheduled_ns"] == scheduled_ns
        and type(core["reason"]) is str
        and 1 <= len(core["reason"]) <= 128
        and core["selected_pool_ids"] is None
        and core["cursor_in"] is None
        and core["cursor_out"] is None
        and core["pool_opportunity_count"] == 0
        and digest == runtime.sha256(runtime.canonical(core)),
        "enrollment_gap_binding",
    )
    return dict(value)


def replay_enrollment_campaign(
    *,
    epoch_history: Sequence[Mapping[str, Any]],
    timeline: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Replay an explicit contiguous prefix of plans and gaps.

    A timeline item is either ``{"kind":"gap", "gap": event}`` or
    ``{"kind":"plan", "plan": ..., "page_records": [...],
    "raw_by_request_id": {id: bytes}}``; each also carries epoch_index and
    scan_index. Page order is normalized by the frozen plan, while timeline
    order and every scheduled scan index are checked as a contiguous prefix.
    Raw bodies are supplied by a descriptor-bound caller and are never copied
    into this receipt.
    """
    history = runtime.verify_epoch_history(list(epoch_history))
    process_sha256 = _process_sha256()
    _need(type(timeline) in (list, tuple) and len(timeline) > 0, "enrollment_timeline_empty")
    policies = {row["epoch"]["epoch_index"]: row["policy"] for row in epoch_history}
    epochs = {row["epoch"]["epoch_index"]: row["epoch"] for row in epoch_history}
    _need(
        len(policies) == len(epoch_history)
        and list(sorted(policies)) == list(range(len(policies))),
        "enrollment_epoch_indices",
    )

    expected_epoch = 0
    expected_scan = 0
    seen_pool_ids: list[str] = []
    enrollment_by_pool: dict[str, dict[str, Any]] = {}
    page_states: Counter[str] = Counter()
    timeline_receipts: list[dict[str, Any]] = []
    raw_bytes = 0
    planned_scans = 0
    gap_scans = 0
    planned_pages = 0
    malformed_rows = 0
    appearances = 0
    within_page_duplicates = 0
    cross_page_duplicates = 0

    for item in timeline:
        _need(type(item) is dict, "enrollment_timeline_item")
        _need(item.get("epoch_index") == expected_epoch, "enrollment_timeline_epoch_order")
        _need(item.get("scan_index") == expected_scan, "enrollment_timeline_scan_order")
        _need(expected_epoch in policies, "enrollment_timeline_unknown_epoch")
        policy = policies[expected_epoch]
        epoch = epochs[expected_epoch]
        _need(expected_scan < policy["max_scan_count"], "enrollment_timeline_scan_cap")
        scheduled_ns = (
            policy["first_scan_ns"] + expected_scan * policy["scan_interval_seconds"] * NS
        )
        kind = item.get("kind")
        if kind == "gap":
            _need(
                set(item) == {"kind", "epoch_index", "scan_index", "gap"},
                "enrollment_gap_item_shape",
            )
            gap = _verify_gap(
                item["gap"],
                epoch_index=expected_epoch,
                scan_index=expected_scan,
                scheduled_ns=scheduled_ns,
            )
            gap_scans += 1
            timeline_receipts.append(
                {
                    "kind": "gap",
                    "epoch_index": expected_epoch,
                    "scan_index": expected_scan,
                    "scheduled_ns": scheduled_ns,
                    "gap_sha256": gap["gap_sha256"],
                    "pool_ids": None,
                }
            )
        elif kind == "plan":
            _need(
                set(item)
                == {
                    "kind",
                    "epoch_index",
                    "scan_index",
                    "plan",
                    "page_records",
                    "raw_by_request_id",
                },
                "enrollment_plan_item_shape",
            )
            _need(
                policy["sample"]["numerator"] == 0,
                "enrollment_followup_sampling_must_be_zero",
            )
            plan = runtime.verify_epoch_scan_plan(item["plan"], policy=policy, epoch=epoch)
            _need(plan["scan_index"] == expected_scan, "enrollment_plan_scan_index")
            raw_by_request_id = item["raw_by_request_id"]
            _need(type(raw_by_request_id) is dict, "enrollment_raw_mapping")
            replay = runtime.verify_persisted_scan(
                policy=policy,
                plan=item["plan"],
                page_records=item["page_records"],
                raw_by_request_id=raw_by_request_id,
                previously_seen_pool_ids=seen_pool_ids,
            )
            _need(
                replay["page_summary"]["planned_pages"] == len(policy["page_range"])
                and replay["followup_sample_pool_ids"] == [],
                "enrollment_page_denominator_or_followup",
            )
            for _page, page_result in replay["page_results"].items():
                status = page_result["slot_status"]
                page_states[status] += 1
                planned_pages += 1
                malformed_rows += page_result.get("malformed_row_count") or 0
                within_page_duplicates += page_result.get("duplicate_pool_appearance_count") or 0
            raw_bytes += sum(len(raw) for raw in raw_by_request_id.values() if type(raw) is bytes)
            appearances += len(replay["pool_appearances"])
            pool_page_sets: dict[str, set[int]] = {}
            for row in replay["pool_appearances"]:
                pool_page_sets.setdefault(row["pool_id"], set()).add(row["page"])
                pool_id = row["pool_id"]
                prior = enrollment_by_pool.get(pool_id)
                claims = {
                    "base_token_id_assertion": row["base_token_id"],
                    "quote_token_id_assertion": row["quote_token_id"],
                    "dex_id_assertion": row["dex_id"],
                }
                if prior is None:
                    record = {
                        "pool_id": pool_id,
                        "role": "unassigned_observation",
                        "protected_exclusion_state": "not_checked",
                        "population_admission": "diagnostic_pending_protected_exclusion",
                        "first_seen_epoch_index": expected_epoch,
                        "first_seen_scan_index": expected_scan,
                        "first_seen_page": row["page"],
                        "first_seen_request_id": row["request_id"],
                        "first_seen_scheduled_ns": row["scheduled_ns"],
                        "first_seen_request_started_ns": row["request_started_ns"],
                        "first_seen_receipt_ns": row["receipt_ns"],
                        "first_durable_available_ns": row["durable_available_ns"],
                        "provider_pool_created_at": row["provider_pool_created_at"],
                        "provider_pool_created_at_ns": row["provider_pool_created_at_ns"],
                        "provider_pool_created_at_state": row["provider_pool_created_at_state"],
                        "base_token_id_assertion": row["base_token_id"],
                        "base_token_state": row["base_token_state"],
                        "quote_token_id_assertion": row["quote_token_id"],
                        "quote_token_state": row["quote_token_state"],
                        "dex_id_assertion": row["dex_id"],
                        "dex_state": row["dex_state"],
                        "source_policy_sha256": policy["policy_sha256"],
                        "source_epoch_sha256": epoch["epoch_sha256"],
                        "source_plan_sha256": plan["plan_sha256"],
                        "source_request_id": row["request_id"],
                        "source_raw_sha256": replay["page_results"][str(row["page"])]["raw_sha256"],
                        "source_row_sha256": row["row_sha256"],
                        "identity_conflict": False,
                        "conflict_observations": [],
                    }
                    enrollment_by_pool[pool_id] = record
                    seen_pool_ids.append(pool_id)
                elif any(prior[key] != value for key, value in claims.items()):
                    changed_fields = sorted(
                        key for key, value in claims.items() if prior[key] != value
                    )
                    prior["identity_conflict"] = True
                    prior["population_admission"] = "quarantined_conflicting_identity_claims"
                    prior["conflict_observations"].append(
                        {
                            "epoch_index": expected_epoch,
                            "scan_index": expected_scan,
                            "page": row["page"],
                            "request_id": row["request_id"],
                            "raw_sha256": replay["page_results"][str(row["page"])]["raw_sha256"],
                            "row_sha256": row["row_sha256"],
                            "changed_fields": changed_fields,
                        }
                    )
            cross_page_duplicates += sum(
                max(0, len(pages) - 1) for pages in pool_page_sets.values()
            )
            planned_scans += 1
            timeline_receipts.append(
                {
                    "kind": "plan",
                    "epoch_index": expected_epoch,
                    "scan_index": expected_scan,
                    "scheduled_ns": scheduled_ns,
                    "plan_sha256": plan["plan_sha256"],
                    "scan_receipt_sha256": replay["receipt_sha256"],
                    "page_summary": replay["page_summary"],
                    "first_seen_pool_ids": replay["first_seen_pool_ids"],
                    "page_raw_sha256": {
                        page: result["raw_sha256"]
                        for page, result in sorted(replay["page_results"].items())
                    },
                }
            )
        else:
            raise EnrollmentError("enrollment_timeline_kind")

        expected_scan += 1
        if expected_scan == policy["max_scan_count"] and expected_epoch < len(policies) - 1:
            _need(
                epoch["epoch_end_ns"] == policies[expected_epoch + 1]["first_scan_ns"],
                "enrollment_epoch_boundary",
            )
            expected_epoch += 1
            expected_scan = 0

    _need(expected_epoch == len(policies) - 1, "enrollment_epoch_history_not_fully_accounted")

    enrollments = [enrollment_by_pool[key] for key in sorted(enrollment_by_pool)]
    page_counts = {
        "planned_page_slots": planned_pages,
        "received": page_states["received"],
        "http_error": page_states["http_error"],
        "transport_ambiguous": page_states["transport_ambiguous"],
        "missed": page_states["missed"],
        "oversize_response": page_states["oversize_response"],
        "malformed_rows": malformed_rows,
        "within_page_duplicate_appearances": within_page_duplicates,
        "cross_page_duplicate_appearances": cross_page_duplicates,
    }
    core = {
        "schema": SCHEMA,
        "schema_version": 1,
        "projector_sha256": process_sha256,
        "release_id": history["release_id"],
        "source_sha256": history["source_sha256"],
        "epoch_history_sha256": runtime.sha256(runtime.canonical(list(epoch_history))),
        "policy_contract_sha256": history["policy_contract_sha256"],
        "timeline": timeline_receipts,
        "denominators": {
            "scheduled_scan_slots": len(timeline_receipts),
            "planned_scans": planned_scans,
            "gap_scans": gap_scans,
            "planned_page_slots": planned_pages,
            "pool_appearances": appearances,
            "unique_provider_discovered_pool_ids": len(enrollments),
            "raw_bytes_replayed": raw_bytes,
        },
        "page_outcomes": page_counts,
        "enrollments": enrollments,
        "claims": {
            "population_kind": "provider_discovered_diagnostic_candidates",
            "protected_exclusion_verified": False,
            "role_assignment_complete": False,
            "price_followup_authorized": False,
            "market_universe_complete": False,
            "provider_index_exhaustive": False,
            "chain_birth_verified": False,
            "lifecycle_death_inferred": False,
            "economic_settlement_qualified": False,
            "training_admission": False,
            "evaluation_admission": False,
            "historical_point_in_time": False,
        },
        "provider_io": False,
    }
    return {**core, "receipt_sha256": runtime.sha256(runtime.canonical(core))}


def verify_enrollment_receipt(
    receipt: Mapping[str, Any],
    *,
    epoch_history: Sequence[Mapping[str, Any]],
    timeline: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Recompute a receipt from immutable runtime evidence and compare all bytes."""
    _need(type(receipt) is dict, "enrollment_receipt_shape")
    expected = replay_enrollment_campaign(epoch_history=epoch_history, timeline=timeline)
    _need(dict(receipt) == expected, "enrollment_receipt_replay_mismatch")
    return dict(receipt)
