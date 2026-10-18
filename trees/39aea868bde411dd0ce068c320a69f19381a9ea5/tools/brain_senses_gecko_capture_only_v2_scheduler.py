"""Pure one-slot-per-callback scheduler rules for Gecko capture-only v2."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from tools import brain_senses_gecko_capture_only_v2_verify as verify
from tools import brain_senses_gecko_rotating_scout_v1 as scout
from tools.brain_senses_external_writer_v1 import canonical_json

SKIP_SCHEMA = "brain-senses-gecko-capture-only-v2-skip-v1"


class SchedulerError(ValueError):
    """Invalid plan or conflicting slot state."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise SchedulerError(code)


def dispatch_allowed(
    candidate_ns: int,
    dispatches: Sequence[Mapping[str, Any]],
    *,
    cutover_last_dispatch_ns: int,
) -> bool:
    """Enforce cutover quiet time, cross-restart spacing, and rolling-minute pacing."""
    _require(
        type(candidate_ns) is int
        and candidate_ns > 0
        and type(cutover_last_dispatch_ns) is int
        and cutover_last_dispatch_ns > 0,
        "dispatch_time_invalid",
    )
    times: list[int] = []
    for row in dispatches:
        value = row.get("dispatch_ns")
        _require(type(value) is int and value > 0, "dispatch_record_time_invalid")
        times.append(cast(int, value))
    _require(times == sorted(times) and len(set(times)) == len(times), "dispatch_order_invalid")
    previous_ns = times[-1] if times else cutover_last_dispatch_ns
    required_spacing = scout.MINIMUM_DISPATCH_SPACING_NS
    if not times:
        required_spacing = max(required_spacing, 60 * scout.NS)
    if candidate_ns - previous_ns < required_spacing:
        return False
    recent = [value for value in times if 0 <= candidate_ns - value < 60 * scout.NS]
    return len(recent) < 5


def used_slots(
    plan: Mapping[str, Any],
    intents: Sequence[Mapping[str, Any]],
    skips: Sequence[Mapping[str, Any]],
) -> set[int]:
    """Return selected indexes already durably consumed by an intent or explicit gap."""
    selection = plan.get("selection")
    pools = selection.get("selected_pool_ids") if isinstance(selection, Mapping) else None
    sequence = plan.get("sequence")
    close_ns = plan.get("bucket_close_ns")
    _require(
        type(pools) is list
        and len(pools) == scout.SLOTS_PER_BUCKET
        and len(set(pools)) == len(pools)
        and type(sequence) is int
        and type(close_ns) is int,
        "plan_shape_invalid",
    )
    pools = cast(list[Any], pools)
    sequence = cast(int, sequence)
    close_ns = cast(int, close_ns)
    consumed: set[int] = set()
    for intent in intents:
        request = intent.get("request")
        if not isinstance(request, Mapping) or request.get("plan_sequence") != sequence:
            continue
        slot = request.get("slot_index")
        _require(
            type(slot) is int and 0 <= slot < len(pools) and pools[slot] == request.get("pool_id"),
            "intent_slot_mismatch",
        )
        slot = cast(int, slot)
        _require(slot not in consumed, "slot_consumed_twice")
        consumed.add(slot)
    for skip in skips:
        if skip.get("plan_sequence") != sequence:
            continue
        slot = skip.get("slot_index")
        _require(
            type(slot) is int
            and 0 <= slot < len(pools)
            and skip.get("pool_id") == pools[slot]
            and skip.get("bucket_close_ns") == close_ns
            and skip.get("schema") == SKIP_SCHEMA
            and skip.get("reason") == "missed_dispatch",
            "skip_slot_mismatch",
        )
        slot = cast(int, slot)
        _require(slot not in consumed, "slot_consumed_twice")
        consumed.add(slot)
    return consumed


def due_slot(
    plan: Mapping[str, Any],
    intents: Sequence[Mapping[str, Any]],
    skips: Sequence[Mapping[str, Any]],
    *,
    now_ns: int,
) -> int | None:
    """Choose at most the first unspent slot when the current request window is open."""
    close_ns = plan.get("bucket_close_ns")
    _require(type(close_ns) is int and type(now_ns) is int and now_ns >= 0, "clock_invalid")
    close_ns = cast(int, close_ns)
    if (
        not close_ns + scout.DISPATCH_EARLIEST_NS
        <= now_ns
        < (close_ns + scout.REQUEST_START_END_EXCLUSIVE_NS)
    ):
        return None
    pools = plan.get("selection", {}).get("selected_pool_ids")
    _require(type(pools) is list and len(pools) == scout.SLOTS_PER_BUCKET, "plan_shape_invalid")
    pools = cast(list[Any], pools)
    consumed = used_slots(plan, intents, skips)
    return next((index for index in range(len(pools)) if index not in consumed), None)


def expired_slot_skips(
    plan: Mapping[str, Any],
    intents: Sequence[Mapping[str, Any]],
    skips: Sequence[Mapping[str, Any]],
    *,
    now_ns: int,
) -> list[dict[str, Any]]:
    """Create explicit no-dispatch rows once the plan's request-start window is over."""
    close_ns = plan.get("bucket_close_ns")
    sequence = plan.get("sequence")
    selection = plan.get("selection")
    pools = selection.get("selected_pool_ids") if isinstance(selection, Mapping) else None
    _require(
        type(close_ns) is int
        and type(sequence) is int
        and type(now_ns) is int
        and type(pools) is list
        and len(pools) == scout.SLOTS_PER_BUCKET,
        "plan_shape_invalid",
    )
    close_ns = cast(int, close_ns)
    sequence = cast(int, sequence)
    pools = cast(list[Any], pools)
    _require(now_ns >= close_ns + scout.REQUEST_START_END_EXCLUSIVE_NS, "window_not_expired")
    consumed = used_slots(plan, intents, skips)
    return [
        {
            "schema": SKIP_SCHEMA,
            "plan_sequence": sequence,
            "slot_index": index,
            "pool_id": pools[index],
            "bucket_close_ns": close_ns,
            "at_ns": now_ns,
            "reason": "missed_dispatch",
        }
        for index in range(len(pools))
        if index not in consumed
    ]


def validate_slot_state(
    plan: Mapping[str, Any],
    intents: Sequence[Mapping[str, Any]],
    skips: Sequence[Mapping[str, Any]],
) -> str:
    """Return a stable digest of the current plan's consumed-slot projection."""
    consumed = used_slots(plan, intents, skips)
    return verify.sha256(canonical_json(sorted(consumed)))


__all__ = [
    "SKIP_SCHEMA",
    "SchedulerError",
    "dispatch_allowed",
    "due_slot",
    "expired_slot_skips",
    "used_slots",
    "validate_slot_state",
]
