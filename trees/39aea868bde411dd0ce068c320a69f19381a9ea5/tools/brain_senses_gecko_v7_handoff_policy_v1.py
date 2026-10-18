"""Pure v6-to-v7 Gecko handoff validation and successor scheduling policy.

This module performs no filesystem, process, provider, or launchd operations.  Its
inputs must come from independent root/journal/census and quiescence verifiers.
The output binds those inputs and preserves ambiguous spend without replay.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from tools.brain_senses_gecko_lane_policy_v6 import POPULATION_SHA256, SOURCE_PIN

HANDOFF_SCHEMA = "brain-senses-gecko-v7-handoff-v1"
HANDOFF_VERSION = 1
V7_POLICY_ID = "gecko-train-six-15m-rotating-scout-v7"
TRAIN_COUNT = 170
PREREGISTRATION_SHA256 = "118cf73e4ebf7f0fc42da11a4d7eab71b3bd4f55ee87b16dad64bb7a8ba6633f"
CURSOR_HANDOFF = 6
BUCKET_NS = 900_000_000_000
DISPATCH_EARLIEST_NS = 60_000_000_000
DISPATCH_END_NS = 300_000_000_000
MIN_HANDOFF_COOLDOWN_NS = 900_000_000_000
AMBIGUOUS_RAW_RESERVE = 16_384
PHYSICAL_RESERVE_PER_ATTEMPT = 81_920
MAX_RESPONSE_BYTES = 16_384

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_ID32 = re.compile(r"[0-9a-f]{32}\Z")
_SAFE_POOL = re.compile(r"[A-Za-z0-9_:-]{1,128}\Z")
_HASH_FIELDS = (
    "release_sha256",
    "source_sha256",
    "policy_sha256",
    "epoch_sha256",
    "root_prefix_sha256",
    "replay_sha256",
)


class HandoffError(ValueError):
    """Malformed or inconsistent v7 handoff evidence."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise HandoffError(reason)


def _is_sha(value: Any) -> bool:
    return type(value) is str and _SHA.fullmatch(value) is not None


def _uint(value: Any) -> bool:
    return type(value) is int and value >= 0


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def _exact_map(value: Any, keys: set[str], reason: str) -> Mapping[str, Any]:
    _need(type(value) is dict and set(value) == keys, reason)
    return value


def _check_hashes(obj: Mapping[str, Any], names: Sequence[str], reason: str) -> None:
    _need(all(_is_sha(obj.get(name)) for name in names), reason)


def _verify_v6_plan(plan: Any, old: Mapping[str, Any]) -> list[str]:
    plan_keys = {
        "schema",
        "schema_version",
        "policy_id",
        "policy_sha256",
        "selector_policy_id",
        "selector_sha256",
        "population_sha256",
        "bucket_close_ns",
        "cursor_in",
        "cursor_out",
        "retention",
        "scout_pool_ids",
        "selected_pool_ids",
        "provider_io",
        "retry",
        "backfill",
        "plan_sha256",
    }
    plan_map = _exact_map(plan, plan_keys, "v6_plan_shape")
    core = dict(plan_map)
    claimed_hash = core.pop("plan_sha256")
    _need(
        plan_map.get("schema") == "brain-senses-gecko-capture-plan-v6"
        and plan_map.get("schema_version") == 6
        and plan_map.get("policy_id") == "gecko-train-six-15m-rotating-scout-v6"
        and plan_map.get("policy_sha256") == old.get("policy_sha256")
        and plan_map.get("selector_policy_id") == "gecko-train-six-15m-rotating-scout-v1"
        and plan_map.get("population_sha256") == POPULATION_SHA256
        and plan_map.get("provider_io") is False
        and plan_map.get("retry") is False
        and plan_map.get("backfill") is False
        and _positive(plan_map.get("bucket_close_ns"))
        and plan_map["bucket_close_ns"] % BUCKET_NS == 0
        and plan_map.get("cursor_in") == 0
        and plan_map.get("cursor_out") == CURSOR_HANDOFF
        and _is_sha(plan_map.get("selector_sha256"))
        and _is_sha(claimed_hash)
        and sha256(canonical(core)) == claimed_hash,
        "v6_plan_binding_or_hash",
    )
    _need(
        type(plan_map["retention"]) is list
        and len(plan_map["retention"]) <= 2
        and all(
            type(row) is dict
            and type(row.get("pool_id")) is str
            and _SAFE_POOL.fullmatch(row["pool_id"]) is not None
            for row in plan_map["retention"]
        )
        and type(plan_map["scout_pool_ids"]) is list
        and 4 <= len(plan_map["scout_pool_ids"]) <= 6
        and all(
            type(pool) is str and _SAFE_POOL.fullmatch(pool) for pool in plan_map["scout_pool_ids"]
        )
        and len(set(plan_map["scout_pool_ids"])) == len(plan_map["scout_pool_ids"])
        and type(plan_map["selected_pool_ids"]) is list
        and len(plan_map["selected_pool_ids"]) == 6
        and all(
            type(pool) is str and _SAFE_POOL.fullmatch(pool)
            for pool in plan_map["selected_pool_ids"]
        )
        and len(set(plan_map["selected_pool_ids"])) == 6
        and set(plan_map["selected_pool_ids"])
        == set(plan_map["scout_pool_ids"]) | {row["pool_id"] for row in plan_map["retention"]},
        "v6_plan_pool_sets",
    )
    return list(plan_map["selected_pool_ids"])


def _validate_attempt_rows(rows: Any, selected: list[str]) -> tuple[list[dict[str, Any]], int, int]:
    _need(type(rows) is list and len(rows) == 6, "v6_handoff_slots_shape")
    normalized: list[dict[str, Any]] = []
    confirmed = 0
    ambiguous = 0
    missed = 0
    request_ids: set[str] = set()
    for index, raw in enumerate(rows):
        _need(type(raw) is dict, "v6_handoff_slot_shape")
        disposition = raw.get("disposition")
        base = {"slot_index", "pool_id", "disposition"}
        if disposition == "confirmed":
            expected = base | {
                "request_id",
                "request_sha256",
                "intent_sha256",
                "dispatch_sha256",
                "outcome_sha256",
                "confirmation_sha256",
                "raw_bytes",
            }
        elif disposition == "prior_ambiguity_unclassified":
            expected = base | {
                "request_id",
                "request_sha256",
                "intent_sha256",
                "dispatch_sha256",
                "raw_bytes_upper_bound",
                "physical_bytes_upper_bound",
                "classification",
            }
        elif disposition == "missed":
            expected = base
        else:
            raise HandoffError("v6_handoff_slot_disposition")
        row = dict(_exact_map(raw, expected, "v6_handoff_slot_shape"))
        _need(
            row.get("slot_index") == index and row.get("pool_id") == selected[index],
            "v6_handoff_slot_order",
        )
        if disposition == "confirmed":
            _need(
                _is_sha(row.get("request_id"))
                and all(
                    _is_sha(row.get(k))
                    for k in (
                        "request_sha256",
                        "intent_sha256",
                        "dispatch_sha256",
                        "outcome_sha256",
                        "confirmation_sha256",
                    )
                )
                and type(row.get("raw_bytes")) is int
                and 0 < row["raw_bytes"] <= MAX_RESPONSE_BYTES,
                "v6_handoff_confirmed_attempt",
            )
            confirmed += 1
        elif disposition == "prior_ambiguity_unclassified":
            _need(
                _is_sha(row.get("request_id"))
                and all(
                    _is_sha(row.get(k))
                    for k in ("request_sha256", "intent_sha256", "dispatch_sha256")
                )
                and row.get("classification") == "prior_ambiguity_unclassified"
                and row.get("raw_bytes_upper_bound") == AMBIGUOUS_RAW_RESERVE
                and row.get("physical_bytes_upper_bound") == PHYSICAL_RESERVE_PER_ATTEMPT,
                "v6_handoff_ambiguous_reserve",
            )
            ambiguous += 1
        else:
            missed += 1
        if disposition != "missed":
            request_id = row["request_id"]
            _need(request_id not in request_ids, "v6_handoff_duplicate_request")
            request_ids.add(request_id)
        normalized.append(row)
    _need((confirmed, ambiguous, missed) == (4, 1, 1), "v6_handoff_expected_4_1_1")
    return normalized, confirmed, ambiguous


def build_handoff(evidence: Any) -> dict[str, Any]:
    """Validate independently replayed v6 evidence and build a canonical handoff receipt.

    Evidence is intentionally metadata-only. Its replay/census/quiescence hashes must
    have been produced by the separately pinned host verifiers before this function runs.
    """
    top = _exact_map(
        evidence,
        {"old_root", "slots", "all_root_census", "quiescence", "population"},
        "handoff_evidence_shape",
    )
    old = _exact_map(
        top["old_root"],
        {"root_id", "release_id", *_HASH_FIELDS, "plan"},
        "handoff_old_root_shape",
    )
    _need(
        type(old.get("root_id")) is str
        and _ID32.fullmatch(old["root_id"]) is not None
        and old["root_id"].startswith("94da")
        and type(old.get("release_id")) is str
        and _ID32.fullmatch(old["release_id"]) is not None,
        "handoff_old_root_identity",
    )
    _check_hashes(old, _HASH_FIELDS, "handoff_old_hashes")
    selected = _verify_v6_plan(old.get("plan"), old)
    rows, _, _ = _validate_attempt_rows(top["slots"], selected)
    census = _exact_map(
        top["all_root_census"],
        {"status", "census_sha256", "root_replay_sha256", "root_count", "all_roots_verified"},
        "handoff_census_shape",
    )
    _need(
        census.get("status") == "verified"
        and census.get("all_roots_verified") is True
        and _is_sha(census.get("census_sha256"))
        and _is_sha(census.get("root_replay_sha256"))
        and type(census.get("root_count")) is int
        and census["root_count"] >= 1,
        "handoff_census_unverified",
    )
    quiescence = _exact_map(
        top["quiescence"],
        {
            "status",
            "proof_sha256",
            "recovered_at_ns",
            "old_gecko_agents_absent",
            "transport_children_quiescent",
            "unresolved_transport_children",
        },
        "handoff_quiescence_shape",
    )
    _need(
        quiescence.get("status") == "verified"
        and _is_sha(quiescence.get("proof_sha256"))
        and _positive(quiescence.get("recovered_at_ns"))
        and quiescence.get("old_gecko_agents_absent") is True
        and quiescence.get("transport_children_quiescent") is True
        and quiescence.get("unresolved_transport_children") == 0,
        "handoff_quiescence_unverified",
    )
    population = _exact_map(
        top["population"],
        {
            "source_pin",
            "policy_id",
            "population_sha256",
            "enrolled_train_count",
            "preregistration_sha256",
        },
        "handoff_population_shape",
    )
    _need(
        population.get("source_pin") == SOURCE_PIN
        and population.get("policy_id") == "gecko-train-six-15m-rotating-scout-v1"
        and population.get("population_sha256") == POPULATION_SHA256
        and population.get("enrolled_train_count") == TRAIN_COUNT
        and population.get("preregistration_sha256") == PREREGISTRATION_SHA256,
        "handoff_population_changed",
    )
    confirmed_raw = sum(row["raw_bytes"] for row in rows if row["disposition"] == "confirmed")
    ambiguous = next(row for row in rows if row["disposition"] == "prior_ambiguity_unclassified")
    physical_bound = sum(
        PHYSICAL_RESERVE_PER_ATTEMPT
        for row in rows
        if row["disposition"] in {"confirmed", "prior_ambiguity_unclassified"}
    )
    close_ns = old["plan"]["bucket_close_ns"]
    core = {
        "schema": HANDOFF_SCHEMA,
        "schema_version": HANDOFF_VERSION,
        "decision": "v6_terminal_v7_handoff_candidate",
        "old_v6": {
            "root_id": old["root_id"],
            "release_id": old["release_id"],
            **{key: old[key] for key in _HASH_FIELDS},
            "plan_sha256": old["plan"]["plan_sha256"],
            "plan": old["plan"],
            "bucket_close_ns": close_ns,
            "cursor_out": CURSOR_HANDOFF,
            "root_replay_sha256": census["root_replay_sha256"],
            "all_root_census_sha256": census["census_sha256"],
            "root_count": census["root_count"],
        },
        "population": dict(population),
        "slots": rows,
        "prior_ambiguity_unclassified": {
            "slot_index": ambiguous["slot_index"],
            "pool_id": ambiguous["pool_id"],
            "request_id": ambiguous["request_id"],
            "request_sha256": ambiguous["request_sha256"],
            "intent_sha256": ambiguous["intent_sha256"],
            "dispatch_sha256": ambiguous["dispatch_sha256"],
            "raw_bytes_upper_bound": AMBIGUOUS_RAW_RESERVE,
            "physical_bytes_upper_bound": PHYSICAL_RESERVE_PER_ATTEMPT,
            "classification": "prior_ambiguity_unclassified",
        },
        "max_charge": {
            "attempt_count": 5,
            "raw_bytes_upper_bound": confirmed_raw + AMBIGUOUS_RAW_RESERVE,
            "confirmed_raw_bytes": confirmed_raw,
            "ambiguous_raw_reserve_bytes": AMBIGUOUS_RAW_RESERVE,
            "physical_bytes_upper_bound": physical_bound,
            "ambiguous_physical_reserve_bytes": PHYSICAL_RESERVE_PER_ATTEMPT,
        },
        "recovery": {
            "proof_sha256": quiescence["proof_sha256"],
            "recovered_at_ns": quiescence["recovered_at_ns"],
            "cooldown_until_ns": quiescence["recovered_at_ns"] + MIN_HANDOFF_COOLDOWN_NS,
            "old_gecko_agents_absent": True,
            "transport_children_quiescent": True,
            "unresolved_transport_children": 0,
        },
        "verification": {
            "v6_root_replay": "verified",
            "all_root_census": "verified",
            "provider_io": False,
            "training_admission": False,
            "economic_admission": False,
        },
    }
    return {**core, "handoff_sha256": sha256(canonical(core))}


def verify_handoff(receipt: Any) -> None:
    """Recompute the exact receipt from its frozen evidence payload."""
    _need(type(receipt) is dict, "handoff_receipt_shape")
    core = dict(receipt)
    claimed = core.pop("handoff_sha256", None)
    _need(_is_sha(claimed) and sha256(canonical(core)) == claimed, "handoff_receipt_hash")
    _need(
        receipt.get("schema") == HANDOFF_SCHEMA
        and receipt.get("schema_version") == HANDOFF_VERSION
        and receipt.get("decision") == "v6_terminal_v7_handoff_candidate"
        and receipt.get("verification")
        == {
            "v6_root_replay": "verified",
            "all_root_census": "verified",
            "provider_io": False,
            "training_admission": False,
            "economic_admission": False,
        },
        "handoff_receipt_semantics",
    )
    _need(
        set(receipt)
        == {
            "schema",
            "schema_version",
            "decision",
            "old_v6",
            "population",
            "slots",
            "prior_ambiguity_unclassified",
            "max_charge",
            "recovery",
            "verification",
            "handoff_sha256",
        },
        "handoff_receipt_keys",
    )
    old = _exact_map(
        receipt.get("old_v6"),
        {
            "root_id",
            "release_id",
            *_HASH_FIELDS,
            "plan_sha256",
            "plan",
            "bucket_close_ns",
            "cursor_out",
            "root_replay_sha256",
            "all_root_census_sha256",
            "root_count",
        },
        "handoff_old_v6_shape",
    )
    planned_pool_ids = _verify_v6_plan(old.get("plan"), old)
    _need(
        type(old.get("root_id")) is str
        and _ID32.fullmatch(old["root_id"]) is not None
        and old["root_id"].startswith("94da")
        and type(old.get("release_id")) is str
        and _ID32.fullmatch(old["release_id"]) is not None
        and all(
            _is_sha(old.get(name))
            for name in (
                *_HASH_FIELDS,
                "plan_sha256",
                "root_replay_sha256",
                "all_root_census_sha256",
            )
        )
        and old.get("plan_sha256") == old["plan"].get("plan_sha256")
        and old.get("bucket_close_ns") == old["plan"].get("bucket_close_ns")
        and _positive(old.get("bucket_close_ns"))
        and old["bucket_close_ns"] % BUCKET_NS == 0
        and old.get("cursor_out") == CURSOR_HANDOFF
        and type(old.get("root_count")) is int
        and old["root_count"] >= 1,
        "handoff_old_v6_binding",
    )
    population = _exact_map(
        receipt.get("population"),
        {
            "source_pin",
            "policy_id",
            "population_sha256",
            "enrolled_train_count",
            "preregistration_sha256",
        },
        "handoff_receipt_population_shape",
    )
    _need(
        population.get("source_pin") == SOURCE_PIN
        and population.get("policy_id") == "gecko-train-six-15m-rotating-scout-v1"
        and population.get("population_sha256") == POPULATION_SHA256
        and population.get("enrolled_train_count") == TRAIN_COUNT
        and population.get("preregistration_sha256") == PREREGISTRATION_SHA256,
        "handoff_receipt_population_binding",
    )
    _need(old.get("cursor_out") == CURSOR_HANDOFF, "handoff_cursor")
    rows = receipt.get("slots")
    _need(type(rows) is list and len(rows) == 6, "handoff_slots")
    _need(
        all(type(row) is dict for row in rows)
        and [row.get("pool_id") for row in rows] == planned_pool_ids,
        "handoff_slots_plan_join",
    )
    _validate_attempt_rows(rows, planned_pool_ids)
    ambiguous = next(row for row in rows if row["disposition"] == "prior_ambiguity_unclassified")
    _need(
        receipt.get("prior_ambiguity_unclassified")
        == {
            "slot_index": ambiguous["slot_index"],
            "pool_id": ambiguous["pool_id"],
            "request_id": ambiguous["request_id"],
            "request_sha256": ambiguous["request_sha256"],
            "intent_sha256": ambiguous["intent_sha256"],
            "dispatch_sha256": ambiguous["dispatch_sha256"],
            "raw_bytes_upper_bound": AMBIGUOUS_RAW_RESERVE,
            "physical_bytes_upper_bound": PHYSICAL_RESERVE_PER_ATTEMPT,
            "classification": "prior_ambiguity_unclassified",
        },
        "handoff_ambiguity_binding",
    )
    charge = receipt.get("max_charge")
    _exact_map(
        charge,
        {
            "attempt_count",
            "raw_bytes_upper_bound",
            "confirmed_raw_bytes",
            "ambiguous_raw_reserve_bytes",
            "physical_bytes_upper_bound",
            "ambiguous_physical_reserve_bytes",
        },
        "handoff_charge_shape",
    )
    expected_raw = (
        sum(row["raw_bytes"] for row in rows if row["disposition"] == "confirmed")
        + AMBIGUOUS_RAW_RESERVE
    )
    expected_physical = 5 * PHYSICAL_RESERVE_PER_ATTEMPT
    _need(
        charge
        == {
            "attempt_count": 5,
            "raw_bytes_upper_bound": expected_raw,
            "confirmed_raw_bytes": expected_raw - AMBIGUOUS_RAW_RESERVE,
            "ambiguous_raw_reserve_bytes": AMBIGUOUS_RAW_RESERVE,
            "physical_bytes_upper_bound": expected_physical,
            "ambiguous_physical_reserve_bytes": PHYSICAL_RESERVE_PER_ATTEMPT,
        },
        "handoff_charge_mismatch",
    )
    recovery = receipt.get("recovery")
    _need(
        type(recovery) is dict
        and set(recovery)
        == {
            "proof_sha256",
            "recovered_at_ns",
            "cooldown_until_ns",
            "old_gecko_agents_absent",
            "transport_children_quiescent",
            "unresolved_transport_children",
        }
        and _is_sha(recovery.get("proof_sha256"))
        and _positive(recovery.get("recovered_at_ns"))
        and recovery.get("cooldown_until_ns")
        == recovery.get("recovered_at_ns", 0) + MIN_HANDOFF_COOLDOWN_NS
        and recovery.get("old_gecko_agents_absent") is True
        and recovery.get("transport_children_quiescent") is True
        and recovery.get("unresolved_transport_children") == 0,
        "handoff_recovery_mismatch",
    )


def _gap(close_ns: int) -> dict[str, Any]:
    core = {
        "schema": "brain-senses-gecko-v7-unplanned-gap-v1",
        "bucket_close_ns": close_ns,
        "selected_pool_ids": None,
        "reason": "elapsed_before_successor_plan_no_backfill",
        "cursor_in": CURSOR_HANDOFF,
        "cursor_out": CURSOR_HANDOFF,
        "intent_count": 0,
        "provider_io": False,
    }
    return {**core, "gap_sha256": sha256(canonical(core))}


def decide_successor(
    receipt: Any,
    *,
    now_ns: int,
    current_population_sha256: str,
    current_source_pin: str,
    old_root_replay_sha256: str,
    all_root_census_sha256: str,
    old_agents_absent: bool,
    transport_children_quiescent: bool,
    clock_verified: bool,
    storage_verified: bool,
) -> dict[str, Any]:
    """Return a fail-closed, no-backfill successor/gap decision from verified current gates."""
    verify_handoff(receipt)
    _need(_positive(now_ns), "successor_now_ns")
    checks = {
        "population": current_population_sha256 == POPULATION_SHA256,
        "source": current_source_pin == SOURCE_PIN,
        "old_root_replay": old_root_replay_sha256 == receipt["old_v6"]["root_replay_sha256"],
        "all_root_census": all_root_census_sha256 == receipt["old_v6"]["all_root_census_sha256"],
        "old_agents": old_agents_absent is True,
        "transport_quiescence": transport_children_quiescent is True,
        "clock": clock_verified is True,
        "storage": storage_verified is True,
    }
    failed = [name for name, ok in checks.items() if not ok]
    old_close = receipt["old_v6"]["bucket_close_ns"]
    last_elapsed = ((now_ns - DISPATCH_END_NS) // BUCKET_NS) * BUCKET_NS
    gaps = [_gap(close) for close in range(old_close + BUCKET_NS, last_elapsed + 1, BUCKET_NS)]
    base = {
        "schema": "brain-senses-gecko-v7-successor-decision-v1",
        "handoff_sha256": receipt["handoff_sha256"],
        "now_ns": now_ns,
        "cursor_in": CURSOR_HANDOFF,
        "cursor_out": CURSOR_HANDOFF,
        "retention": [],
        "gaps": gaps,
        "checks": checks,
        "provider_io": False,
    }
    if failed:
        return {
            **base,
            "status": "stop",
            "reason": "successor_gate_failed:" + ",".join(failed),
            "bucket_close_ns": None,
        }
    cooldown_until = receipt["recovery"]["cooldown_until_ns"]
    if now_ns < cooldown_until:
        return {
            **base,
            "status": "cooldown_wait",
            "reason": "minimum_handoff_cooldown",
            "cooldown_until_ns": cooldown_until,
            "bucket_close_ns": None,
        }
    close_ns = (now_ns // BUCKET_NS + 1) * BUCKET_NS
    while close_ns + DISPATCH_EARLIEST_NS < cooldown_until:
        close_ns += BUCKET_NS
    return {
        **base,
        "status": "ready_for_fresh_plan",
        "reason": "future_bucket_after_handoff_cooldown",
        "cooldown_until_ns": cooldown_until,
        "bucket_close_ns": close_ns,
    }


def verify_successor_plan(plan: Any, decision: Any) -> None:
    """Bind a fresh selector plan to the successor decision without advancing on gaps."""
    _need(
        type(decision) is dict and decision.get("status") == "ready_for_fresh_plan",
        "successor_not_ready",
    )
    _need(type(plan) is dict, "successor_plan_shape")
    _need(
        plan.get("policy_id") == V7_POLICY_ID
        and plan.get("population_sha256") == POPULATION_SHA256
        and plan.get("cursor_in") == CURSOR_HANDOFF
        and plan.get("bucket_close_ns") == decision.get("bucket_close_ns")
        and type(plan.get("selected_pool_ids")) is list
        and len(plan["selected_pool_ids"]) == 6
        and len(set(plan["selected_pool_ids"])) == 6
        and type(plan.get("retention")) is list
        and len(plan["retention"]) == 0,
        "successor_plan_continuity",
    )


_FATAL_STATUSES = {
    "http_401",
    "http_403",
    "payment_required",
    "quota_exceeded",
    "cap_exceeded",
    "clock_invalid",
    "storage_invalid",
    "replay_invalid",
    "ambiguous_unclassified",
}


def classify_v7_outcome(
    status: str,
    *,
    event_ns: int,
    fresh_account_limit_check: str | None = None,
    retry_after_until_ns: int | None = None,
    retry_after_verified: bool = False,
    independent_transient_classification: str | None = None,
    verified_cooldown_until_ns: int | None = None,
) -> dict[str, Any]:
    """Classify one already-spent at-most-once request; never creates a retry."""
    _need(type(status) is str, "outcome_status")
    _need(_positive(event_ns), "outcome_event_time")
    if status in _FATAL_STATUSES:
        return {
            "decision": "stop",
            "reason": status,
            "may_retry": False,
            "latches_stop": True,
            "reviewed_handoff_required": True,
        }
    if status == "http_429":
        if fresh_account_limit_check != "verified_within_limit":
            return {
                "decision": "stop",
                "reason": "429_requires_fresh_account_limit_check",
                "may_retry": False,
                "latches_stop": True,
                "reviewed_handoff_required": True,
            }
        retry_after = event_ns + 3_600_000_000_000
        if retry_after_until_ns is not None:
            _need(
                retry_after_verified is True
                and _positive(retry_after_until_ns)
                and retry_after_until_ns >= event_ns,
                "retry_after_invalid",
            )
            retry_after = max(retry_after, retry_after_until_ns)
        return {
            "decision": "cooldown_resume_allowed_after_recheck",
            "reason": "http_429",
            "cooldown_until_ns": retry_after,
            "fresh_account_limit_check_required": True,
            "may_retry_same_request": False,
        }
    if status in {"provider_error", "transport"}:
        if (
            independent_transient_classification != "verified_transient"
            or not _positive(verified_cooldown_until_ns)
            or verified_cooldown_until_ns < event_ns
        ):
            return {
                "decision": "stop",
                "reason": "transient_not_independently_classified",
                "may_retry": False,
                "latches_stop": True,
                "reviewed_handoff_required": True,
            }
        return {
            "decision": "cooldown_resume_allowed",
            "reason": "independently_classified_transient",
            "cooldown_until_ns": verified_cooldown_until_ns,
            "may_retry_same_request": False,
        }
    if status == "confirmed":
        return {"decision": "continue", "reason": "confirmed", "may_retry_same_request": False}
    return {
        "decision": "stop",
        "reason": "unknown_or_unclassified_status",
        "may_retry": False,
        "latches_stop": True,
        "reviewed_handoff_required": True,
    }


def verify_unique_claims(claims: Any) -> None:
    """Reject any duplicate pool/bucket claim, including ambiguous or failed attempts."""
    _need(type(claims) is list, "attempt_claims_shape")
    seen: set[tuple[int, str]] = set()
    request_ids: set[str] = set()
    for claim in claims:
        _need(
            type(claim) is dict
            and set(claim) == {"bucket_close_ns", "pool_id", "request_id"}
            and _positive(claim.get("bucket_close_ns"))
            and claim["bucket_close_ns"] % BUCKET_NS == 0
            and type(claim.get("pool_id")) is str
            and _SAFE_POOL.fullmatch(claim["pool_id"]) is not None
            and _is_sha(claim.get("request_id")),
            "attempt_claim_invalid",
        )
        key = (claim["bucket_close_ns"], claim["pool_id"])
        _need(key not in seen, "attempt_claim_duplicate_pool_bucket")
        _need(claim["request_id"] not in request_ids, "attempt_claim_duplicate_request_id")
        seen.add(key)
        request_ids.add(claim["request_id"])


def may_create_attempt_claim(
    claims: Any, *, bucket_close_ns: int, pool_id: str, request_id: str
) -> bool:
    """Return true only for a new durable claim; callers must persist it before dispatch."""
    verify_unique_claims(claims)
    _need(
        _positive(bucket_close_ns)
        and bucket_close_ns % BUCKET_NS == 0
        and type(pool_id) is str
        and _SAFE_POOL.fullmatch(pool_id) is not None
        and _is_sha(request_id),
        "attempt_claim_invalid",
    )
    return all(
        (claim["bucket_close_ns"], claim["pool_id"]) != (bucket_close_ns, pool_id)
        for claim in claims
    )
