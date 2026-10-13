"""Read-only positive-bar projection for independently replayed Gecko v6 opportunities.

This module does not open providers, write capture roots, or publish qualified coverage.
Its input plan/opportunity records must come from a separate successful v6 root replay.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from tools.brain_senses_gecko_lane_engine_v3 import parse_response  # noqa: E402

SCHEMA = "brain-senses-gecko-v6-observed-values-audit-v1"
CONTRACT = {
    "version": "gecko-observed-values-process-v1",
    "provider": "geckoterminal",
    "modality": "ohlcv",
    "value_contract": "usd_price_per_base_token_and_base_token_volume",
    "bar_interval_seconds": 300,
    "currency": "usd",
    "priced_token": "base",
    "entity_namespace": "solana/pool/",
    "event_identity": "(pool_id, bar_open_ns)",
    "event_time": "five_minute_bar_open_utc_ns",
    "decision_time": "sampling_bucket_close_utc_ns",
    "receipt_time": "provider_response_receipt_utc_ns",
    "availability_time": "durable_confirmation_utc_ns",
    "revision_policy": "retain_new_open_only; revisions_are_audit_only",
    "value_scope": "positive_values_present_in_sealed_raw_response_only",
    "absence_semantics": "unknown; no zero, inactivity, death, or complete_support inference",
}
PARSER_SOURCE_SHA256 = hashlib.sha256(
    (Path(_TOOLS_DIR) / "brain_senses_gecko_lane_engine_v3.py").read_bytes()
).hexdigest()
CONTRACT_SHA256 = hashlib.sha256(
    json.dumps(
        {"contract": CONTRACT, "parser_source_sha256": PARSER_SOURCE_SHA256},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()
PIN_FIELDS = ("source_sha256", "policy_sha256", "selector_sha256", "population_sha256")
NS = 1_000_000_000
BAR_NS = 300 * NS
BUCKET_NS = 900 * NS


class ProjectionError(ValueError):
    """The sealed positive-value evidence is inconsistent or out of contract."""


def _sha(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        return False
    try:
        return len(bytes.fromhex(value)) == 32
    except ValueError:
        return False


def _exact_int(value: Any) -> bool:
    return type(value) is int


def _verified_pins(pins: Mapping[str, str]) -> dict[str, str]:
    if (
        not isinstance(pins, Mapping)
        or set(pins) != set(PIN_FIELDS)
        or any(not _sha(pins.get(k)) for k in PIN_FIELDS)
    ):
        raise ProjectionError("observed_values_pins")
    return dict(pins)


def _population_index(population: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    if not isinstance(population, Sequence) or isinstance(population, (str, bytes)):
        raise ProjectionError("observed_values_population")
    result: dict[str, dict[str, str]] = {}
    for row in population:
        if not isinstance(row, Mapping):
            raise ProjectionError("observed_values_population_row")
        pool = row.get("pool_id")
        base = row.get("base_mint")
        quote = row.get("quote_mint")
        if (
            not isinstance(pool, str)
            or not pool
            or not isinstance(base, str)
            or not base
            or not isinstance(quote, str)
            or not quote
            or pool in result
        ):
            raise ProjectionError("observed_values_population_identity")
        result[pool] = {"base_mint": base, "quote_mint": quote}
    if not result:
        raise ProjectionError("observed_values_population_empty")
    return result


def _plan_key(plan: Mapping[str, Any]) -> tuple[int, tuple[str, ...]]:
    close = plan.get("close_ns")
    pools = plan.get("selected_pool_ids")
    if (
        not _exact_int(close)
        or close <= 0
        or close % BUCKET_NS
        or not isinstance(pools, list)
        or not pools
        or any(not isinstance(pool, str) or not pool for pool in pools)
        or len(pools) != len(set(pools))
    ):
        raise ProjectionError("observed_values_plan_shape")
    return close, tuple(pools)


def audit_verified_prefix(
    *,
    replay_report: Mapping[str, Any],
    plans: Sequence[Mapping[str, Any]],
    gaps: Sequence[Mapping[str, Any]],
    opportunities: Sequence[Mapping[str, Any]],
    population: Sequence[Mapping[str, Any]],
    pins: Mapping[str, str],
) -> dict[str, Any]:
    """Reconstruct explicit opportunity denominators and positive new-open bar facts.

    ``plans``, ``gaps`` and ``opportunities`` are read-only rows extracted only after the
    independent v6 journal/artifact replay succeeds. Every plan slot is retained in the
    denominator, including absent intents and non-success terminals. Gaps have no pool slots.
    """
    checked_pins = _verified_pins(pins)
    pools = _population_index(population)
    if (
        not isinstance(plans, Sequence)
        or isinstance(plans, (str, bytes))
        or not isinstance(gaps, Sequence)
        or isinstance(gaps, (str, bytes))
        or not isinstance(opportunities, Sequence)
        or isinstance(opportunities, (str, bytes))
    ):
        raise ProjectionError("observed_values_input_sequences")
    if (
        not isinstance(replay_report, Mapping)
        or replay_report.get("schema") != "brain-senses-gecko-capture-verify-v6"
        or replay_report.get("provider_io") is not False
        or replay_report.get("market_source_qualified") is not False
        or replay_report.get("training_admission") is not False
        or replay_report.get("economic_admission") is not False
        or replay_report.get("policy_sha256") != checked_pins["policy_sha256"]
        or replay_report.get("selector_sha256") != checked_pins["selector_sha256"]
        or not _sha(replay_report.get("epoch_sha256"))
        or not _sha(replay_report.get("event_tail_sha256"))
        or not _exact_int(replay_report.get("event_count"))
        or replay_report["event_count"] <= 0
        or not _exact_int(replay_report.get("plan_count"))
        or not isinstance(replay_report.get("slots"), Mapping)
    ):
        raise ProjectionError("observed_values_replay_report")
    if not plans:
        raise ProjectionError("observed_values_plans_empty")
    plan_by_slot: dict[tuple[int, str], Mapping[str, Any]] = {}
    bucket_states: list[tuple[int, str]] = []
    previous_close: int | None = None
    for raw_plan in plans:
        if not isinstance(raw_plan, Mapping):
            raise ProjectionError("observed_values_plan_row")
        close, selected = _plan_key(raw_plan)
        if previous_close is not None and close != previous_close + BUCKET_NS:
            raise ProjectionError("observed_values_bucket_sequence")
        previous_close = close
        if any(pool not in pools for pool in selected):
            raise ProjectionError("observed_values_pool_outside_population")
        bucket_states.append((close, "plan"))
        for pool in selected:
            key = (close, pool)
            if key in plan_by_slot:
                raise ProjectionError("observed_values_duplicate_plan_slot")
            plan_by_slot[key] = raw_plan

    expected_slots = {f"{close}:{pool}" for close, pool in plan_by_slot}
    reported_slots = replay_report["slots"]
    if (
        set(reported_slots) != expected_slots
        or any(
            not isinstance(state, str) or state not in {"intent", "missed", "unspent"}
            for state in reported_slots.values()
        )
        or replay_report["plan_count"] != len(plans)
    ):
        raise ProjectionError("observed_values_replay_slots")

    for gap in gaps:
        if not isinstance(gap, Mapping):
            raise ProjectionError("observed_values_gap_row")
        close = gap.get("close_ns")
        if (
            not _exact_int(close)
            or close <= 0
            or close % BUCKET_NS
            or gap.get("selected_pool_ids") is not None
            or gap.get("cursor_out") != gap.get("cursor_in")
        ):
            raise ProjectionError("observed_values_gap_shape")
        bucket_states.append((close, "gap"))
    bucket_states.sort()
    if len({close for close, _ in bucket_states}) != len(bucket_states):
        raise ProjectionError("observed_values_duplicate_bucket")
    if any(
        b[0] != a[0] + BUCKET_NS for a, b in zip(bucket_states, bucket_states[1:], strict=False)
    ):
        raise ProjectionError("observed_values_bucket_sequence")

    opportunity_by_slot: dict[tuple[int, str], Mapping[str, Any]] = {}
    for opportunity in opportunities:
        if not isinstance(opportunity, Mapping):
            raise ProjectionError("observed_values_opportunity_row")
        close, pool = opportunity.get("close_ns"), opportunity.get("pool_id")
        key = (close, pool)
        if (
            not _exact_int(close)
            or not isinstance(pool, str)
            or key not in plan_by_slot
            or key in opportunity_by_slot
        ):
            raise ProjectionError("observed_values_opportunity_binding")
        opportunity_by_slot[key] = opportunity

    dispositions: Counter[str] = Counter()
    terminal_states: Counter[str] = Counter()
    availability_states: Counter[str] = Counter()
    observed_values: list[dict[str, Any]] = []
    observed_identities: set[tuple[str, int]] = set()
    per_pool = {
        pool: {
            "planned_opportunities": 0,
            "attempted": 0,
            "successful_responses": 0,
            "durable_confirmations": 0,
            "timely_confirmations": 0,
            "eligible_new_open_values": 0,
            "age_classification": "unknown",
        }
        for pool in pools
    }
    for (close, pool), _plan in sorted(plan_by_slot.items()):
        per_pool[pool]["planned_opportunities"] += 1
        opportunity = opportunity_by_slot.get((close, pool))
        if opportunity is None:
            replay_state = reported_slots[f"{close}:{pool}"]
            terminal_states["missed" if replay_state == "missed" else "no_intent_unknown"] += 1
            continue
        status = opportunity.get("status")
        if not isinstance(status, str) or not status:
            raise ProjectionError("observed_values_terminal_status")
        terminal_states[status] += 1
        if status != "missed":
            per_pool[pool]["attempted"] += 1
        raw = opportunity.get("raw_body")
        if status not in {"received", "empty_response"}:
            if raw is not None:
                raise ProjectionError("observed_values_raw_on_non_success")
            continue
        if not isinstance(raw, bytes):
            raise ProjectionError("observed_values_raw_missing")
        per_pool[pool]["successful_responses"] += 1
        raw_sha = hashlib.sha256(raw).hexdigest()
        if not _sha(opportunity.get("raw_sha256")) or raw_sha != opportunity["raw_sha256"]:
            raise ProjectionError("observed_values_raw_hash")
        request_ns = opportunity.get("request_started_ns")
        receipt_ns = opportunity.get("receipt_ns")
        available_ns = opportunity.get("durable_available_ns")
        confirmed = opportunity.get("confirmed")
        outcome_timely = opportunity.get("timely")
        if (
            not _exact_int(request_ns)
            or not _exact_int(receipt_ns)
            or type(confirmed) is not bool
            or type(outcome_timely) is not bool
            or request_ns < close + 60 * NS
            or request_ns >= close + 300 * NS
            or receipt_ns < request_ns
            or (confirmed and not _exact_int(available_ns))
            or (confirmed and available_ns < receipt_ns)
            or (not confirmed and available_ns is not None)
            or (confirmed and not outcome_timely and available_ns < close + 300 * NS)
        ):
            raise ProjectionError("observed_values_time_order")
        if confirmed:
            per_pool[pool]["durable_confirmations"] += 1
        try:
            parsed = parse_response(raw, close, receipt_ns)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProjectionError("observed_values_response_parse") from exc
        if status == "empty_response" and parsed:
            raise ProjectionError("observed_values_empty_mismatch")
        if status == "received" and not parsed:
            raise ProjectionError("observed_values_received_mismatch")
        timely = confirmed and outcome_timely and available_ns < close + 300 * NS
        if not confirmed:
            availability_state = "durable_confirmation_missing"
        elif timely:
            availability_state = "durable_before_window_end"
            per_pool[pool]["timely_confirmations"] += 1
        else:
            availability_state = "durable_after_window_end"
        availability_states[availability_state] += 1
        identity = pools[pool]
        for bar in parsed:
            disposition = bar["disposition"]
            dispositions[disposition] += 1
            if disposition != "eligible_new_open" or not timely:
                continue
            identity_key = (pool, bar["event_time_ns"])
            if identity_key in observed_identities:
                raise ProjectionError("observed_values_duplicate_new_open")
            observed_identities.add(identity_key)
            per_pool[pool]["eligible_new_open_values"] += 1
            observed_values.append(
                {
                    "entity": "solana/pool/" + pool,
                    "pool_id": pool,
                    "base_mint": identity["base_mint"],
                    "quote_mint": identity["quote_mint"],
                    "bar_open_ns": bar["event_time_ns"],
                    "decision_bucket_close_ns": close,
                    "request_started_ns": request_ns,
                    "provider_receipt_ns": receipt_ns,
                    "durable_available_ns": available_ns,
                    "raw_response_sha256": raw_sha,
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "volume": bar["volume"],
                    "age_classification": "unknown",
                    "birth_evidence_pin": None,
                    "source_contract_sha256": CONTRACT_SHA256,
                    "parser_source_sha256": PARSER_SOURCE_SHA256,
                    **checked_pins,
                }
            )

    return {
        "schema": SCHEMA,
        "replay_identity": {
            "epoch_sha256": replay_report["epoch_sha256"],
            "event_tail_sha256": replay_report["event_tail_sha256"],
            "event_count": replay_report["event_count"],
            "plan_count": replay_report["plan_count"],
        },
        "contract": CONTRACT,
        "contract_sha256": CONTRACT_SHA256,
        "parser_source_sha256": PARSER_SOURCE_SHA256,
        "scheduled_bucket_denominator": len(bucket_states),
        "durable_plan_buckets": sum(kind == "plan" for _, kind in bucket_states),
        "unplanned_gap_buckets": sum(kind == "gap" for _, kind in bucket_states),
        "planned_pool_opportunity_denominator": len(plan_by_slot),
        "opportunity_terminal_states": dict(sorted(terminal_states.items())),
        "response_availability_states": dict(sorted(availability_states.items())),
        "bar_dispositions": dict(sorted(dispositions.items())),
        "distinct_pools_planned": len({pool for _, pool in plan_by_slot}),
        "population_size": len(pools),
        "per_pool": per_pool,
        "age_band_denominators": {"unknown_birth_evidence": len(plan_by_slot)},
        "observed_value_count": len(observed_values),
        "observed_values": observed_values,
        "population_coverage_qualified": False,
        "complete_support": False,
        "zero_or_death_inference": False,
        "training_admission": False,
        "market_source_qualified": False,
        "provider_io": False,
    }
