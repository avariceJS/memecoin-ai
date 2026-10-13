import hashlib
import json

import pytest

from tools.brain_senses_gecko_v6_observed_values_v1 import (
    ProjectionError,
    audit_verified_prefix,
    extract_verified_events,
)

NS = 1_000_000_000
CLOSE = 1_800 * NS
PINS = {
    "source_sha256": "a" * 64,
    "policy_sha256": "b" * 64,
    "selector_sha256": "c" * 64,
    "population_sha256": "d" * 64,
}
POPULATION = [
    {"pool_id": f"pool-{i}", "base_mint": f"base-{i}", "quote_mint": f"quote-{i}"} for i in range(6)
]
PLAN = {"close_ns": CLOSE, "selected_pool_ids": [row["pool_id"] for row in POPULATION]}
NEW_AND_OVERLAP = json.dumps(
    {
        "data": {
            "attributes": {
                "ohlcv_list": [
                    [1500, 2.0, 3.0, 1.0, 2.5, 10.0],
                    [600, 1.0, 2.0, 0.5, 1.5, 7.0],
                ]
            }
        }
    },
    separators=(",", ":"),
).encode()
EMPTY = json.dumps({"data": {"attributes": {"ohlcv_list": []}}}, separators=(",", ":")).encode()


def _opportunity(pool, status, raw, *, close=CLOSE, available=None):
    request_ns = close + 60 * NS
    receipt_ns = request_ns + 2 * NS
    if available is None:
        available = receipt_ns + NS
    return {
        "close_ns": close,
        "pool_id": pool,
        "status": status,
        "raw_body": raw,
        "raw_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
        "request_started_ns": request_ns,
        "receipt_ns": receipt_ns,
        "durable_available_ns": available,
        "confirmed": True,
        "timely": True,
    }


def _audit(opportunities, *, gaps=(), plans=(PLAN,), population=POPULATION, pins=PINS):
    slots = {}
    for plan in plans:
        for pool in plan["selected_pool_ids"]:
            opportunity = next(
                (
                    row
                    for row in opportunities
                    if row["close_ns"] == plan["close_ns"] and row["pool_id"] == pool
                ),
                None,
            )
            slots[f"{plan['close_ns']}:{pool}"] = (
                "missed"
                if opportunity and opportunity["status"] == "missed"
                else "intent"
                if opportunity
                else "unspent"
            )
    replay_report = {
        "schema": "brain-senses-gecko-capture-verify-v6",
        "epoch_sha256": "e" * 64,
        "event_tail_sha256": "f" * 64,
        "event_count": max(1, len(plans) + len(gaps) + len(opportunities)),
        "plan_count": len(plans),
        "slots": slots,
        "policy_sha256": pins["policy_sha256"],
        "selector_sha256": pins["selector_sha256"],
        "provider_io": False,
        "market_source_qualified": False,
        "training_admission": False,
        "economic_admission": False,
    }
    return audit_verified_prefix(
        replay_report=replay_report,
        plans=plans,
        gaps=gaps,
        opportunities=opportunities,
        population=population,
        pins=pins,
    )


def test_projects_only_timely_new_open_positive_values_and_keeps_denominators():
    result = _audit(
        [
            _opportunity("pool-0", "received", NEW_AND_OVERLAP),
            _opportunity("pool-1", "empty_response", EMPTY),
            _opportunity("pool-2", "transport_uncertain_possibly_spent", None),
        ],
        gaps=[
            {
                "close_ns": CLOSE + 900 * NS,
                "selected_pool_ids": None,
                "cursor_in": 11,
                "cursor_out": 11,
            }
        ],
    )
    assert result["scheduled_bucket_denominator"] == 2
    assert result["durable_plan_buckets"] == 1
    assert result["unplanned_gap_buckets"] == 1
    assert result["planned_pool_opportunity_denominator"] == 6
    assert result["opportunity_terminal_states"] == {
        "empty_response": 1,
        "no_intent_unknown": 3,
        "received": 1,
        "transport_uncertain_possibly_spent": 1,
    }
    assert result["bar_dispositions"] == {
        "eligible_new_open": 1,
        "eligible_overlap_revision": 1,
    }
    assert result["observed_value_count"] == 1
    assert result["per_pool"]["pool-0"]["eligible_new_open_values"] == 1
    assert result["per_pool"]["pool-3"]["planned_opportunities"] == 1
    assert result["per_pool"]["pool-3"]["successful_responses"] == 0
    assert result["age_band_denominators"] == {"unknown_birth_evidence": 6}
    fact = result["observed_values"][0]
    assert fact["entity"] == "solana/pool/pool-0"
    assert fact["bar_open_ns"] == 1500 * NS
    assert fact["decision_bucket_close_ns"] == CLOSE
    assert fact["provider_receipt_ns"] < fact["durable_available_ns"]
    assert fact["age_classification"] == "unknown"
    assert fact["birth_evidence_pin"] is None
    assert result["complete_support"] is False
    assert result["population_coverage_qualified"] is False
    assert result["zero_or_death_inference"] is False
    assert result["training_admission"] is False
    assert result["provider_io"] is False


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (lambda opportunities, gaps: opportunities[0].update(raw_sha256="0" * 64), "raw_hash"),
        (
            lambda opportunities, gaps: opportunities[0].update(pool_id="outside"),
            "opportunity_binding",
        ),
        (
            lambda opportunities, gaps: opportunities[0].update(
                durable_available_ns=CLOSE + 61 * NS
            ),
            "time_order",
        ),
        (
            lambda opportunities, gaps: gaps[0].update(selected_pool_ids=["invented-pool"]),
            "gap_shape",
        ),
    ],
)
def test_mutations_fail_closed(change, reason):
    opportunities = [_opportunity("pool-0", "received", NEW_AND_OVERLAP)]
    gaps = [
        {
            "close_ns": CLOSE + 900 * NS,
            "selected_pool_ids": None,
            "cursor_in": 11,
            "cursor_out": 11,
        }
    ]
    change(opportunities, gaps)
    with pytest.raises(ProjectionError, match=reason):
        _audit(opportunities, gaps=gaps)


def test_rejects_duplicate_pool_slot_and_duplicate_response_bar():
    duplicate_plan = {
        "close_ns": CLOSE,
        "selected_pool_ids": ["pool-0", "pool-0"],
    }
    with pytest.raises(ProjectionError, match="plan_shape"):
        _audit([], plans=[duplicate_plan])
    duplicate_bar = json.dumps(
        {
            "data": {
                "attributes": {
                    "ohlcv_list": [
                        [1500, 2, 3, 1, 2.5, 10],
                        [1500, 2, 3, 1, 2.5, 10],
                    ]
                }
            }
        },
        separators=(",", ":"),
    ).encode()
    with pytest.raises(ProjectionError, match="response_parse"):
        _audit([_opportunity("pool-0", "received", duplicate_bar)])


def test_rejects_gap_cursor_advance_or_pool_attribution():
    bad_gap = {
        "close_ns": CLOSE + 900 * NS,
        "selected_pool_ids": None,
        "cursor_in": 11,
        "cursor_out": 12,
    }
    with pytest.raises(ProjectionError, match="gap_shape"):
        _audit([], gaps=[bad_gap])


def test_late_durable_confirmation_is_retained_as_unavailable_for_new_open_fact():
    result = _audit(
        [
            _opportunity(
                "pool-0",
                "received",
                NEW_AND_OVERLAP,
                available=CLOSE + 300 * NS,
            )
        ]
    )
    assert result["response_availability_states"] == {"durable_after_window_end": 1}
    assert result["bar_dispositions"] == {
        "eligible_new_open": 1,
        "eligible_overlap_revision": 1,
    }
    assert result["observed_value_count"] == 0


def test_unconfirmed_successful_response_is_not_projected_as_available_value():
    opportunity = _opportunity("pool-0", "received", NEW_AND_OVERLAP)
    opportunity["confirmed"] = False
    opportunity["durable_available_ns"] = None
    result = _audit([opportunity])
    assert result["response_availability_states"] == {"durable_confirmation_missing": 1}
    assert result["per_pool"]["pool-0"]["successful_responses"] == 1
    assert result["per_pool"]["pool-0"]["durably_confirmed_successful_responses"] == 0
    assert result["observed_value_count"] == 0


def test_raw_error_response_is_hash_checked_but_never_decoded_as_values():
    raw_error = b"not-an-ohlcv-response"
    opportunity = _opportunity("pool-0", "malformed_response", None)
    opportunity["raw_body"] = raw_error
    opportunity["raw_sha256"] = hashlib.sha256(raw_error).hexdigest()
    result = _audit([opportunity])
    assert result["non_success_raw_response_count"] == 1
    assert result["observed_value_count"] == 0


def test_extracts_plan_slots_and_replays_confirmed_raw_bytes_by_bound_path():
    pool = "pool-0"
    request_id = "req-0"
    raw_hash = hashlib.sha256(NEW_AND_OVERLAP).hexdigest()
    events = [
        {"kind": "plan", "fields": {"close_ns": CLOSE, "selected_pool_ids": [pool]}},
        {
            "kind": "gap",
            "fields": {
                "close_ns": CLOSE + 900 * NS,
                "selected_pool_ids": None,
                "cursor_in": 0,
                "cursor_out": 0,
            },
        },
        {
            "kind": "intent",
            "fields": {
                "request_id": request_id,
                "close_ns": CLOSE,
                "pool_id": pool,
                "intent_ns": CLOSE + 50 * NS,
            },
        },
        {
            "kind": "dispatch",
            "fields": {"request_id": request_id, "request_started_ns": CLOSE + 60 * NS},
        },
        {
            "kind": "outcome",
            "fields": {
                "request_id": request_id,
                "status": "received",
                "raw_path": f"responses/{request_id}.bin",
                "raw_sha256": raw_hash,
                "receipt_ns": CLOSE + 62 * NS,
                "timely": True,
            },
        },
        {
            "kind": "confirmation",
            "fields": {
                "request_id": request_id,
                "availability_ns": CLOSE + 63 * NS,
            },
        },
    ]
    plans, gaps, opportunities = extract_verified_events(
        events,
        read_raw=lambda path: (
            NEW_AND_OVERLAP
            if path == f"responses/{request_id}.bin"
            else pytest.fail("unexpected raw path")
        ),
    )
    result = _audit(opportunities, plans=plans, gaps=gaps, population=POPULATION[:1])
    assert result["observed_value_count"] == 1
    assert result["scheduled_bucket_denominator"] == 2
    assert result["unplanned_gap_buckets"] == 1
    assert result["observed_values"][0]["raw_response_sha256"] == raw_hash


def test_event_extraction_rejects_unbound_raw_path_and_duplicate_event():
    base = {
        "kind": "outcome",
        "fields": {
            "request_id": "req-0",
            "status": "received",
            "raw_path": "responses/other.bin",
            "raw_sha256": hashlib.sha256(NEW_AND_OVERLAP).hexdigest(),
            "receipt_ns": CLOSE + 62 * NS,
            "timely": True,
        },
    }
    events = [
        {"kind": "plan", "fields": {"close_ns": CLOSE, "selected_pool_ids": ["pool-0"]}},
        {
            "kind": "intent",
            "fields": {"request_id": "req-0", "close_ns": CLOSE, "pool_id": "pool-0"},
        },
        {
            "kind": "dispatch",
            "fields": {"request_id": "req-0", "request_started_ns": CLOSE + 60 * NS},
        },
        base,
    ]
    with pytest.raises(ProjectionError, match="raw_path"):
        extract_verified_events(events, read_raw=lambda _: NEW_AND_OVERLAP)
    with pytest.raises(ProjectionError, match="duplicate_request_event"):
        extract_verified_events([*events, base], read_raw=lambda _: NEW_AND_OVERLAP)
