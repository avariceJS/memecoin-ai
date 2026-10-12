"""Pure, capture-only rotating sampler for the 170-pool Gecko train population."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, cast

from tools import brain_senses_gecko_lane_policy_v3 as frozen_v3

SCHEMA = "brain-senses-gecko-rotating-scout-v1"
SCHEMA_VERSION = 1
POLICY_ID = "gecko-train-six-15m-rotating-scout-v1"
NS = 1_000_000_000
BUCKET_NS = 900 * NS
POOL_COUNT = 170
SLOTS_PER_BUCKET = 6
RETENTION_SLOTS = 2
RETENTION_BUCKETS = 4
SCOUT_SLOTS_WITH_FULL_RETENTION = 4
HTTP_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 16 * 1024
MINIMUM_DISPATCH_SPACING_NS = 12 * NS
ROLLING_MINUTE_DISPATCH_CAP = 5
DISPATCH_EARLIEST_NS = 60 * NS
DISPATCH_END_EXCLUSIVE_NS = 300 * NS
REQUEST_START_END_EXCLUSIVE_NS = 270 * NS
AVAILABILITY_END_EXCLUSIVE_NS = 300 * NS
MAX_INTENTS_PER_UTC_DAY = 576
MAX_INTENTS_PER_ROLLING_30D = 17_280
MAX_RAW_BYTES_PER_UTC_DAY = 10 * 1024**2
MAX_RAW_BYTES_PER_ROLLING_30D = 300 * 1024**2
MAX_PHYSICAL_RESERVE_PER_ROLLING_30D = 2 * 1024**3
MAX_RESIDENT_BYTES = 16 * 1024**3
MIN_FREE_BYTES = 50 * 1024**3
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class SelectionError(ValueError):
    """Invalid population, cursor, timing, or prior-only evidence."""


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SelectionError(reason)


def _uint(value: Any, reason: str) -> int:
    _require(type(value) is int and value >= 0, reason)
    return cast(int, value)


def pool_order(population: Sequence[Mapping[str, Any]], source_pin: str) -> list[dict[str, str]]:
    """Validate the pinned pool-ranking rule and return its deterministic cycle."""
    _require(
        type(source_pin) is str and HASH_RE.fullmatch(source_pin) is not None, "source_pin_invalid"
    )
    _require(
        type(population) in {list, tuple} and len(population) == POOL_COUNT,
        "population_count_invalid",
    )
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in population:
        _require(type(raw) is dict, "pool_shape_invalid")
        _require(
            set(raw) == {"pool_id", "base_mint", "quote_mint", "rank_sha256"}, "pool_shape_invalid"
        )
        pool_id = raw["pool_id"]
        base_mint = raw["base_mint"]
        quote_mint = raw["quote_mint"]
        rank = raw["rank_sha256"]
        _require(type(pool_id) is str and bool(pool_id), "pool_id_invalid")
        _require(pool_id not in seen, "pool_id_duplicate")
        _require(type(base_mint) is str and bool(base_mint), "base_mint_invalid")
        _require(type(quote_mint) is str and bool(quote_mint), "quote_mint_invalid")
        expected_rank = hashlib.sha256(f"{source_pin}\0{pool_id}".encode()).hexdigest()
        _require(rank == expected_rank, "pool_rank_mismatch")
        seen.add(pool_id)
        rows.append(
            {
                "pool_id": pool_id,
                "base_mint": base_mint,
                "quote_mint": quote_mint,
                "rank_sha256": expected_rank,
            }
        )
    return sorted(rows, key=lambda row: (row["rank_sha256"], row["pool_id"]))


def population_digest(rows: Sequence[Mapping[str, str]]) -> str:
    """Bind membership and selected source metadata, not just the hash ranking."""
    payload = json.dumps(list(rows), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def compile_pinned_population(
    *,
    enrollment_bytes: bytes,
    role_manifest_bytes: bytes,
    plan_bytes: bytes,
    selection_bytes: bytes,
) -> dict[str, Any]:
    """Validate frozen v3 source bytes, then publish all 170 train pool metadata rows.

    The existing v3 compiler remains the authority for byte pins, role joins, blocked/protected
    screens and training-eligibility checks. Its four-row top-rank output is deliberately not the
    v5 acquisition population.
    """
    frozen_v3.compile_pinned_policy(
        enrollment_bytes=enrollment_bytes,
        role_manifest_bytes=role_manifest_bytes,
        plan_bytes=plan_bytes,
        selection_bytes=selection_bytes,
    )
    plan = frozen_v3.strict_json(plan_bytes, "gecko_v5_plan")
    train_cases = [case for case in plan["cases"] if case.get("role") == frozen_v3.TRAIN_ROLE]
    raw_rows = [
        {
            "pool_id": case["pool"],
            "base_mint": case["base_mint"],
            "quote_mint": case["quote_mint"],
        }
        for case in train_cases
    ]
    ranked = [
        {
            **row,
            "rank_sha256": frozen_v3.rank_pool(frozen_v3.TRAIN_SOURCE_PIN, row["pool_id"]),
        }
        for row in raw_rows
    ]
    population = pool_order(ranked, frozen_v3.TRAIN_SOURCE_PIN)
    _require(len(population) == frozen_v3.TRAIN_COUNT, "pinned_population_count_invalid")
    source_hashes = {
        "train_enrollment": frozen_v3.sha256(enrollment_bytes),
        "train_role_manifest": frozen_v3.sha256(role_manifest_bytes),
        "train_plan": frozen_v3.sha256(plan_bytes),
        "population_selection": frozen_v3.sha256(selection_bytes),
    }
    core: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "source": {
            "role": frozen_v3.TRAIN_ROLE,
            "source_pin": frozen_v3.TRAIN_SOURCE_PIN,
            "source_sha256": source_hashes,
            "source_binding_sha256": hashlib.sha256(canonical_json(source_hashes)).hexdigest(),
            "enrolled_train_count": frozen_v3.TRAIN_COUNT,
            "ranking": "sha256(utf8(source_pin + NUL + pool_id)),ascending",
            "protected_screen_fields": ["pool_id", "base_mint"],
            "quote_mint_rule": "preserve_and_match_pinned_pool_metadata_only",
            "training_eligible": False,
        },
        "population_count": len(population),
        "population_sha256": population_digest(population),
        "population": population,
        "sampling": {
            "slots_per_bucket": SLOTS_PER_BUCKET,
            "cycle_order": "pinned_source_hash_rank_ascending",
            "retention_slots_max": RETENTION_SLOTS,
            "retention_selected_buckets": RETENTION_BUCKETS,
            "retention_entry": "prior_bucket_received_durable_timely_eligible_new_open_only",
            "active_retention_displacement": False,
            "new_retention_rank": "availability_ns_then_population_rank_sha256",
            "same_bucket_replacement": False,
            "retry": False,
            "backfill": False,
        },
        "transport": {
            "minimum_dispatch_spacing_ns": MINIMUM_DISPATCH_SPACING_NS,
            "maximum_dispatches_per_rolling_60s": ROLLING_MINUTE_DISPATCH_CAP,
            "dispatch_window": "[bucket_close+60s,bucket_close+300s)",
            "request_start_deadline": "strictly_before_bucket_close+270s",
            "durable_availability_deadline": "strictly_before_bucket_close+300s",
            "timeout_seconds": HTTP_TIMEOUT_SECONDS,
            "maximum_response_bytes": MAX_RESPONSE_BYTES,
        },
        "capacity": {
            "maximum_intents_per_utc_day": MAX_INTENTS_PER_UTC_DAY,
            "maximum_intents_per_rolling_30d": MAX_INTENTS_PER_ROLLING_30D,
            "maximum_raw_bytes_per_utc_day": MAX_RAW_BYTES_PER_UTC_DAY,
            "maximum_raw_bytes_per_rolling_30d": MAX_RAW_BYTES_PER_ROLLING_30D,
            "maximum_physical_reserve_per_rolling_30d": MAX_PHYSICAL_RESERVE_PER_ROLLING_30D,
            "resident_allocation_ceiling_bytes": MAX_RESIDENT_BYTES,
            "minimum_free_bytes": MIN_FREE_BYTES,
            "existing_same_provider_usage_counts": True,
        },
        "provider_io": False,
        "activation_authorized": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }
    return {**core, "policy_sha256": hashlib.sha256(canonical_json(core)).hexdigest()}


def verify_pinned_population(
    policy: Mapping[str, Any],
    *,
    enrollment_bytes: bytes,
    role_manifest_bytes: bytes,
    plan_bytes: bytes,
    selection_bytes: bytes,
) -> None:
    """Recompile from the exact pinned source bytes and require a byte-identical policy."""
    expected = compile_pinned_population(
        enrollment_bytes=enrollment_bytes,
        role_manifest_bytes=role_manifest_bytes,
        plan_bytes=plan_bytes,
        selection_bytes=selection_bytes,
    )
    _require(
        type(policy) is dict and canonical_json(policy) == canonical_json(expected),
        "pinned_policy_source_mismatch",
    )


def plan_bucket(
    population: Sequence[Mapping[str, Any]],
    *,
    source_pin: str,
    bucket_close_ns: int,
    cursor: int,
    prior_retention: Sequence[Mapping[str, Any]] = (),
    prior_evidence: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Select distinct retention/scout pools using only evidence available before this bucket.

    Persist the returned plan and ``next_cursor`` before dispatch. Recovery must reuse that
    exact plan; this pure selector has no request, retry, clock, or storage side effects.
    """
    rows = pool_order(population, source_pin)
    _uint(bucket_close_ns, "bucket_close_invalid")
    _require(bucket_close_ns > 0 and bucket_close_ns % BUCKET_NS == 0, "bucket_close_invalid")
    _require(type(cursor) is int and 0 <= cursor < POOL_COUNT, "cursor_invalid")
    by_id = {row["pool_id"]: row for row in rows}
    candidates: dict[str, dict[str, int | str]] = {}
    active_retention_ids: set[str] = set()

    def retain(
        pool_id: Any,
        term_start_close: Any,
        latest_evidence_close: Any,
        available: Any,
        expires: Any,
    ) -> None:
        _require(type(pool_id) is str and pool_id in by_id, "retention_pool_unknown")
        term_ns = _uint(term_start_close, "retention_term_start_invalid")
        evidence_ns = _uint(latest_evidence_close, "retention_evidence_time_invalid")
        available_ns = _uint(available, "retention_availability_invalid")
        expires_ns = _uint(expires, "retention_expiry_invalid")
        _require(
            term_ns < bucket_close_ns
            and evidence_ns < bucket_close_ns
            and available_ns < bucket_close_ns,
            "future_evidence_rejected",
        )
        _require(
            term_ns <= evidence_ns < available_ns < evidence_ns + AVAILABILITY_END_EXCLUSIVE_NS,
            "retention_availability_invalid",
        )
        _require(
            expires_ns == term_ns + RETENTION_BUCKETS * BUCKET_NS,
            "retention_expiry_invalid",
        )
        if bucket_close_ns > expires_ns:
            return
        row = {
            "pool_id": pool_id,
            "retention_started_from_evidence_close_ns": term_ns,
            "latest_evidence_bucket_close_ns": evidence_ns,
            "availability_ns": available_ns,
            "expires_after_close_ns": expires_ns,
        }
        prior = candidates.get(pool_id)
        if prior is None or int(row["availability_ns"]) > int(prior["availability_ns"]):
            if prior is not None:
                row["retention_started_from_evidence_close_ns"] = prior[
                    "retention_started_from_evidence_close_ns"
                ]
                row["expires_after_close_ns"] = prior["expires_after_close_ns"]
            candidates[pool_id] = row

    for raw in prior_retention:
        _require(
            type(raw) is dict
            and set(raw)
            == {
                "pool_id",
                "retention_started_from_evidence_close_ns",
                "latest_evidence_bucket_close_ns",
                "availability_ns",
                "expires_after_close_ns",
            },
            "retention_shape_invalid",
        )
        retain(
            raw["pool_id"],
            raw["retention_started_from_evidence_close_ns"],
            raw["latest_evidence_bucket_close_ns"],
            raw["availability_ns"],
            raw["expires_after_close_ns"],
        )
        pool_id = str(raw["pool_id"])
        if pool_id in candidates:
            active_retention_ids.add(pool_id)

    _require(len(active_retention_ids) <= RETENTION_SLOTS, "active_retention_overflow")

    previous_close = bucket_close_ns - BUCKET_NS
    for raw in prior_evidence:
        expected = {
            "pool_id",
            "evidence_bucket_close_ns",
            "request_started_ns",
            "receipt_ns",
            "availability_ns",
            "received",
            "durable",
            "eligible_new_open",
        }
        _require(type(raw) is dict and set(raw) == expected, "evidence_shape_invalid")
        if raw["evidence_bucket_close_ns"] != previous_close:
            continue
        _require(
            type(raw["received"]) is bool
            and type(raw["durable"]) is bool
            and type(raw["eligible_new_open"]) is bool,
            "retention_evidence_flags_invalid",
        )
        if not (raw["received"] and raw["durable"] and raw["eligible_new_open"]):
            continue
        pool_id = raw["pool_id"]
        _require(type(pool_id) is str and pool_id in by_id, "evidence_pool_unknown")
        started = _uint(raw["request_started_ns"], "evidence_start_invalid")
        receipt = _uint(raw["receipt_ns"], "evidence_receipt_invalid")
        available = _uint(raw["availability_ns"], "evidence_availability_invalid")
        _require(
            previous_close + DISPATCH_EARLIEST_NS
            <= started
            < previous_close + REQUEST_START_END_EXCLUSIVE_NS,
            "evidence_start_out_of_window",
        )
        _require(
            started <= receipt <= available < previous_close + AVAILABILITY_END_EXCLUSIVE_NS,
            "evidence_time_order_invalid",
        )
        retain(
            pool_id,
            previous_close,
            previous_close,
            available,
            previous_close + RETENTION_BUCKETS * BUCKET_NS,
        )

    rank_by_id = {row["pool_id"]: row["rank_sha256"] for row in rows}
    incumbents = sorted(
        (row for pool_id, row in candidates.items() if pool_id in active_retention_ids),
        key=lambda row: (
            int(row["retention_started_from_evidence_close_ns"]),
            rank_by_id[str(row["pool_id"])],
        ),
    )
    new_candidates = sorted(
        (row for pool_id, row in candidates.items() if pool_id not in active_retention_ids),
        key=lambda row: (int(row["availability_ns"]), rank_by_id[str(row["pool_id"])]),
    )
    retention = incumbents + new_candidates[: RETENTION_SLOTS - len(incumbents)]
    retained_ids = {str(row["pool_id"]) for row in retention}
    scout_target = SLOTS_PER_BUCKET - len(retention)
    scouts: list[str] = []
    inspected = 0
    while len(scouts) < scout_target:
        pool_id = rows[(cursor + inspected) % POOL_COUNT]["pool_id"]
        inspected += 1
        if pool_id not in retained_ids:
            scouts.append(pool_id)
    selected = [str(row["pool_id"]) for row in retention] + scouts
    _require(
        len(selected) == SLOTS_PER_BUCKET and len(set(selected)) == SLOTS_PER_BUCKET,
        "selection_not_six_distinct",
    )
    _require(inspected <= POOL_COUNT, "selection_scan_overflow")
    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "bucket_close_ns": bucket_close_ns,
        "source_pin": source_pin,
        "population_count": len(rows),
        "population_sha256": population_digest(rows),
        "cursor": cursor,
        "next_cursor": (cursor + inspected) % POOL_COUNT,
        "retention": retention,
        "scout_pool_ids": scouts,
        "selected_pool_ids": selected,
        "provider_io": False,
        "retry": False,
        "backfill": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }


__all__ = [
    "AVAILABILITY_END_EXCLUSIVE_NS",
    "BUCKET_NS",
    "DISPATCH_EARLIEST_NS",
    "HTTP_TIMEOUT_SECONDS",
    "DISPATCH_END_EXCLUSIVE_NS",
    "MAX_INTENTS_PER_ROLLING_30D",
    "MAX_INTENTS_PER_UTC_DAY",
    "MAX_PHYSICAL_RESERVE_PER_ROLLING_30D",
    "MAX_RAW_BYTES_PER_ROLLING_30D",
    "MAX_RAW_BYTES_PER_UTC_DAY",
    "MAX_RESIDENT_BYTES",
    "MAX_RESPONSE_BYTES",
    "MIN_FREE_BYTES",
    "MINIMUM_DISPATCH_SPACING_NS",
    "POLICY_ID",
    "ROLLING_MINUTE_DISPATCH_CAP",
    "RETENTION_BUCKETS",
    "RETENTION_SLOTS",
    "SCHEMA",
    "SCHEMA_VERSION",
    "SelectionError",
    "SLOTS_PER_BUCKET",
    "plan_bucket",
    "population_digest",
    "pool_order",
]
