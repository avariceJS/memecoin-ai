from __future__ import annotations

import copy
import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from tools import brain_senses_gecko_lane_policy_v3 as frozen_v3  # noqa: E402
from tools.brain_senses_gecko_rotating_scout_v1 import (  # noqa: E402
    BUCKET_NS,
    MAX_INTENTS_PER_ROLLING_30D,
    MAX_INTENTS_PER_UTC_DAY,
    MAX_RAW_BYTES_PER_ROLLING_30D,
    MAX_RAW_BYTES_PER_UTC_DAY,
    POLICY_ID,
    RETENTION_BUCKETS,
    SelectionError,
    compile_pinned_population,
    plan_bucket,
    pool_order,
    verify_pinned_population,
)

SOURCE_PIN = hashlib.sha256(b"frozen-train-population").hexdigest()
POPULATION = [
    {
        "pool_id": f"pool-{index:03d}",
        "base_mint": f"mint-{index:03d}",
        "quote_mint": "wsol",
        "rank_sha256": hashlib.sha256(f"{SOURCE_PIN}\0pool-{index:03d}".encode()).hexdigest(),
    }
    for index in range(170)
]
ORDERED = pool_order(POPULATION, SOURCE_PIN)
FIRST_CLOSE = 1_800_000_000 * 1_000_000_000
FIRST_CLOSE -= FIRST_CLOSE % BUCKET_NS


def _evidence(pool_id: str, previous_close: int, available: int) -> dict[str, Any]:
    return {
        "pool_id": pool_id,
        "evidence_bucket_close_ns": previous_close,
        "request_started_ns": previous_close + 60_000_000_000,
        "receipt_ns": available - 1_000_000_000,
        "availability_ns": available,
        "received": True,
        "durable": True,
        "eligible_new_open": True,
    }


def test_unretained_cycle_covers_170_with_28_or_29_bucket_revisits() -> None:
    cursor = 0
    picks: list[str] = []
    for index in range(29):
        plan = plan_bucket(
            POPULATION,
            source_pin=SOURCE_PIN,
            bucket_close_ns=FIRST_CLOSE + index * BUCKET_NS,
            cursor=cursor,
        )
        assert len(plan["selected_pool_ids"]) == 6
        assert len(set(plan["selected_pool_ids"])) == 6
        assert plan["retention"] == []
        picks.extend(plan["scout_pool_ids"])
        cursor = plan["next_cursor"]
    expected_cycle = [row["pool_id"] for row in ORDERED]
    assert picks == expected_cycle + expected_cycle[:4]


@settings(max_examples=25, deadline=None)
@given(cursor=st.integers(min_value=0, max_value=169), permutation=st.permutations(POPULATION))
def test_population_input_order_does_not_change_selection(
    cursor: int, permutation: tuple[dict[str, str], ...]
) -> None:
    baseline = plan_bucket(
        POPULATION,
        source_pin=SOURCE_PIN,
        bucket_close_ns=FIRST_CLOSE,
        cursor=cursor,
    )
    shuffled = plan_bucket(
        list(permutation),
        source_pin=SOURCE_PIN,
        bucket_close_ns=FIRST_CLOSE,
        cursor=cursor,
    )
    assert shuffled == baseline


def test_two_retention_slots_replace_scout_overlaps_without_duplicate_pool() -> None:
    previous = FIRST_CLOSE - BUCKET_NS
    retained_ids = [ORDERED[0]["pool_id"], ORDERED[1]["pool_id"]]
    evidence = [
        _evidence(retained_ids[0], previous, previous + 225_000_000_000),
        _evidence(retained_ids[1], previous, previous + 226_000_000_000),
    ]
    plan = plan_bucket(
        POPULATION,
        source_pin=SOURCE_PIN,
        bucket_close_ns=FIRST_CLOSE,
        cursor=0,
        prior_evidence=evidence,
    )
    assert [row["pool_id"] for row in plan["retention"]] == retained_ids
    assert len(plan["scout_pool_ids"]) == 4
    assert not set(retained_ids) & set(plan["scout_pool_ids"])
    assert len(plan["selected_pool_ids"]) == len(set(plan["selected_pool_ids"])) == 6
    assert plan["next_cursor"] == 6
    assert (
        plan["retention"][0]["expires_after_close_ns"]
        == FIRST_CLOSE + (RETENTION_BUCKETS - 1) * BUCKET_NS
    )
    next_close = FIRST_CLOSE + BUCKET_NS
    continued = plan_bucket(
        POPULATION,
        source_pin=SOURCE_PIN,
        bucket_close_ns=next_close,
        cursor=plan["next_cursor"],
        prior_retention=plan["retention"],
        prior_evidence=[
            _evidence(retained_ids[0], FIRST_CLOSE, FIRST_CLOSE + 90_000_000_000),
            _evidence(retained_ids[1], FIRST_CLOSE, FIRST_CLOSE + 91_000_000_000),
            _evidence(ORDERED[2]["pool_id"], FIRST_CLOSE, FIRST_CLOSE + 80_000_000_000),
        ],
    )
    assert [row["pool_id"] for row in continued["retention"]] == retained_ids
    assert (
        continued["retention"][0]["expires_after_close_ns"]
        == plan["retention"][0]["expires_after_close_ns"]
    )


def test_retention_rank_is_availability_then_population_hash() -> None:
    previous = FIRST_CLOSE - BUCKET_NS
    first, second, third = [row["pool_id"] for row in ORDERED[:3]]
    prior = [
        {
            "pool_id": first,
            "retention_started_from_evidence_close_ns": previous,
            "latest_evidence_bucket_close_ns": previous,
            "availability_ns": previous + 100_000_000_000,
            "expires_after_close_ns": previous + RETENTION_BUCKETS * BUCKET_NS,
        }
    ]
    newly_eligible = _evidence(third, previous, previous + 90_000_000_000)
    earlier_new = _evidence(second, previous, previous + 80_000_000_000)
    plan = plan_bucket(
        POPULATION,
        source_pin=SOURCE_PIN,
        bucket_close_ns=FIRST_CLOSE,
        cursor=0,
        prior_retention=prior,
        prior_evidence=[newly_eligible, earlier_new],
    )
    assert [row["pool_id"] for row in plan["retention"]] == [first, second]


def test_only_immediately_prior_bucket_can_seed_retention_and_output_is_capture_only() -> None:
    stale = _evidence(ORDERED[0]["pool_id"], FIRST_CLOSE - 2 * BUCKET_NS, FIRST_CLOSE - BUCKET_NS)
    plan = plan_bucket(
        POPULATION,
        source_pin=SOURCE_PIN,
        bucket_close_ns=FIRST_CLOSE,
        cursor=0,
        prior_evidence=[stale],
    )
    assert plan["retention"] == []
    assert plan["provider_io"] is False
    assert plan["retry"] is False
    assert plan["backfill"] is False
    assert plan["market_source_qualified"] is False
    assert plan["training_admission"] is False
    assert plan["economic_admission"] is False
    assert plan["policy_id"] == POLICY_ID


def test_noneligible_prior_rows_stay_out_of_retention_and_expired_rows_age_out() -> None:
    previous = FIRST_CLOSE - BUCKET_NS
    stale = {
        "pool_id": ORDERED[0]["pool_id"],
        "retention_started_from_evidence_close_ns": previous - 4 * BUCKET_NS,
        "latest_evidence_bucket_close_ns": previous - 4 * BUCKET_NS,
        "availability_ns": previous - 4 * BUCKET_NS + 200_000_000_000,
        "expires_after_close_ns": previous,
    }
    ineligible = _evidence(ORDERED[1]["pool_id"], previous, previous + 220_000_000_000)
    ineligible["eligible_new_open"] = False
    plan = plan_bucket(
        POPULATION,
        source_pin=SOURCE_PIN,
        bucket_close_ns=FIRST_CLOSE,
        cursor=0,
        prior_retention=[stale],
        prior_evidence=[ineligible],
    )
    assert plan["retention"] == []
    assert len(plan["scout_pool_ids"]) == 6


def test_repeated_eligibility_cannot_extend_a_term_beyond_four_selected_buckets() -> None:
    pool_id = ORDERED[0]["pool_id"]
    retention: list[dict[str, Any]] = []
    original_expiry = FIRST_CLOSE + (RETENTION_BUCKETS - 1) * BUCKET_NS
    for offset in range(RETENTION_BUCKETS):
        close = FIRST_CLOSE + offset * BUCKET_NS
        previous = close - BUCKET_NS
        plan = plan_bucket(
            POPULATION,
            source_pin=SOURCE_PIN,
            bucket_close_ns=close,
            cursor=0,
            prior_retention=retention,
            prior_evidence=[_evidence(pool_id, previous, previous + 200_000_000_000)],
        )
        retained = next(row for row in plan["retention"] if row["pool_id"] == pool_id)
        assert retained["expires_after_close_ns"] == original_expiry
        assert retained["retention_started_from_evidence_close_ns"] == FIRST_CLOSE - BUCKET_NS
        retention = plan["retention"]

    close = FIRST_CLOSE + RETENTION_BUCKETS * BUCKET_NS
    previous = close - BUCKET_NS
    restarted_term = plan_bucket(
        POPULATION,
        source_pin=SOURCE_PIN,
        bucket_close_ns=close,
        cursor=0,
        prior_retention=retention,
        prior_evidence=[_evidence(pool_id, previous, previous + 200_000_000_000)],
    )
    new_term = next(row for row in restarted_term["retention"] if row["pool_id"] == pool_id)
    assert new_term["retention_started_from_evidence_close_ns"] == previous
    assert new_term["expires_after_close_ns"] == previous + RETENTION_BUCKETS * BUCKET_NS


def test_malformed_or_future_retention_evidence_fails_closed() -> None:
    with pytest.raises(SelectionError, match="pool_rank_mismatch"):
        bad = [dict(row) for row in POPULATION]
        bad[0]["rank_sha256"] = "0" * 64
        pool_order(bad, SOURCE_PIN)

    future = {
        "pool_id": ORDERED[0]["pool_id"],
        "retention_started_from_evidence_close_ns": FIRST_CLOSE,
        "latest_evidence_bucket_close_ns": FIRST_CLOSE,
        "availability_ns": FIRST_CLOSE + 1,
        "expires_after_close_ns": FIRST_CLOSE + 4 * BUCKET_NS,
    }
    with pytest.raises(SelectionError, match="future_evidence_rejected"):
        plan_bucket(
            POPULATION,
            source_pin=SOURCE_PIN,
            bucket_close_ns=FIRST_CLOSE,
            cursor=0,
            prior_retention=[future],
        )

    forged = {
        "pool_id": ORDERED[0]["pool_id"],
        "retention_started_from_evidence_close_ns": FIRST_CLOSE - BUCKET_NS,
        "latest_evidence_bucket_close_ns": FIRST_CLOSE - BUCKET_NS,
        "availability_ns": FIRST_CLOSE - BUCKET_NS + 200_000_000_000,
        "expires_after_close_ns": FIRST_CLOSE + 100 * BUCKET_NS,
    }
    with pytest.raises(SelectionError, match="retention_expiry_invalid"):
        plan_bucket(
            POPULATION,
            source_pin=SOURCE_PIN,
            bucket_close_ns=FIRST_CLOSE,
            cursor=0,
            prior_retention=[forged],
        )


def test_population_digest_binds_pool_mints_in_addition_to_rank() -> None:
    changed = [dict(row) for row in POPULATION]
    changed[0]["base_mint"] = "different-mint"
    plan_a = plan_bucket(POPULATION, source_pin=SOURCE_PIN, bucket_close_ns=FIRST_CLOSE, cursor=0)
    plan_b = plan_bucket(changed, source_pin=SOURCE_PIN, bucket_close_ns=FIRST_CLOSE, cursor=0)
    assert plan_a["selected_pool_ids"] == plan_b["selected_pool_ids"]
    assert plan_a["population_sha256"] != plan_b["population_sha256"]


def test_source_bound_compiler_emits_all_train_rows_and_replay_rejects_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = [
        {
            "role": "train",
            "pool": f"pool-{index:03d}",
            "base_mint": f"mint-{index:03d}",
            "quote_mint": "wsol",
        }
        for index in range(170)
    ]
    monkeypatch.setattr(frozen_v3, "compile_pinned_policy", lambda **_kwargs: {"ok": True})
    monkeypatch.setattr(frozen_v3, "strict_json", lambda _raw, _code: {"cases": cases})
    source = {
        "enrollment_bytes": b"enrollment",
        "role_manifest_bytes": b"role-manifest",
        "plan_bytes": b"plan",
        "selection_bytes": b"selection",
    }
    policy = compile_pinned_population(**source)
    assert policy["population_count"] == 170
    assert len(policy["population"]) == 170
    assert policy["training_admission"] is False
    assert policy["activation_authorized"] is False
    verify_pinned_population(policy, **source)

    mutated = copy.deepcopy(policy)
    mutated["population"][0]["base_mint"] = "different-mint"
    with pytest.raises(SelectionError, match="pinned_policy_source_mismatch"):
        verify_pinned_population(mutated, **source)


def test_finite_provider_caps_include_prior_v4_usage_without_reset() -> None:
    assert MAX_INTENTS_PER_UTC_DAY == 576
    assert MAX_INTENTS_PER_ROLLING_30D == 17_280
    assert MAX_RAW_BYTES_PER_UTC_DAY == 10 * 1024 * 1024
    assert MAX_RAW_BYTES_PER_ROLLING_30D == 300 * 1024 * 1024
