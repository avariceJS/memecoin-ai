from __future__ import annotations

from copy import deepcopy

import pytest

from tools import brain_senses_c5_forward_pilot_v1 as c5

SOURCES = {"c5_policy": "a" * 64, "v3_schedule": "b" * 64}
BOOTSTRAP_BUCKET = "20261012T010000Z"
FRAME_BUCKET = "20261012T020000Z"
AUTHORIZED_NS = 1791681600000000000  # 2026-10-11T01:20:00Z
BOOTSTRAP_TICK_NS = 1791767100000000000  # 2026-10-12T01:05:00Z
FRAME_TICK_NS = 1791770700000000000  # 2026-10-12T02:05:00Z


def plan() -> dict:
    return c5.build_plan(
        pilot_id="c5-forward-pilot-test",
        service_id="c5-service-test",
        bootstrap_bucket=BOOTSTRAP_BUCKET,
        frame_bucket=FRAME_BUCKET,
        authorized_at_ns=AUTHORIZED_NS,
        source_bindings=SOURCES,
    )


def bootstrap_success(*, anchor: int = 455430200, raw: int = 43, stored: int = 56) -> dict:
    return {
        "kind": "bootstrap_sealed",
        "bucket": BOOTSTRAP_BUCKET,
        "outcome": "success",
        "commitment": "finalized",
        "anchor_slot": anchor,
        "raw_bytes": raw,
        "stored_bytes": stored,
        "receipt_ns": 1791767100000000000,
        "availability_ns": 1791767100000000000,
        "request_sha256": "c" * 64,
        "response_sha256": "d" * 64,
        "capture_receipt_sha256": "e" * 64,
    }


def frame_success(*, anchor: int = 455431000, raw: int = 10_000, stored: int = 5_000) -> dict:
    return {
        "kind": "frame_sealed",
        "bucket": FRAME_BUCKET,
        "outcome": "complete",
        "commitment": "finalized",
        "request_count": c5.FRAME_REQUESTS,
        "anchor_slot": anchor,
        "raw_bytes": raw,
        "stored_bytes": stored,
        "request_journal_sha256": "f" * 64,
        "capture_manifest_sha256": "1" * 64,
        "summary_sha256": "2" * 64,
    }


def ready_for_bootstrap() -> tuple[dict, dict]:
    p = plan()
    state = c5.transition(
        p,
        c5.initial_state(p),
        {"kind": "bootstrap_started", "bucket": BOOTSTRAP_BUCKET, "tick_at_ns": BOOTSTRAP_TICK_NS},
    )
    return p, state


def ready_for_frame() -> tuple[dict, dict]:
    p, state = ready_for_bootstrap()
    state = c5.transition(p, state, bootstrap_success())
    state = c5.transition(
        p, state, {"kind": "frame_started", "bucket": FRAME_BUCKET, "tick_at_ns": FRAME_TICK_NS}
    )
    return p, state


def test_plan_pins_c4_metadata_and_two_buckets_without_initializing() -> None:
    p = plan()
    c5.verify_plan(p, current_bindings=SOURCES)
    assert p["predecessor"] == c5.C4_LINEAGE
    assert p["predecessor"]["unknown_ended_gap"]["end_exclusive"] is None
    assert p["schedule"]["bootstrap_bucket"] == BOOTSTRAP_BUCKET
    assert p["schedule"]["frame_bucket"] == FRAME_BUCKET
    assert p["execution"] == "offline_policy_only_no_root_initialized"
    assert p["activation_gates"]["activation_authorized"] is False
    assert plan()["plan_sha256"] == p["plan_sha256"]


@pytest.mark.parametrize(
    ("bootstrap_bucket", "frame_bucket", "authorized_ns"),
    [
        ("20261011T010000Z", "20261011T020000Z", AUTHORIZED_NS),  # past
        ("20261012T010000Z", "20261012T030000Z", AUTHORIZED_NS),  # skipped hour
        ("20261012T250000Z", "20261012T260000Z", AUTHORIZED_NS),  # invalid hour
        ("20261012T010000Z", "20261012T020000Z", 0),
    ],
)
def test_plan_rejects_nonfuture_or_nonadjacent_buckets(
    bootstrap_bucket: str, frame_bucket: str, authorized_ns: int
) -> None:
    with pytest.raises(c5.PilotError):
        c5.build_plan(
            pilot_id="c5-test",
            service_id="c5-service-test",
            bootstrap_bucket=bootstrap_bucket,
            frame_bucket=frame_bucket,
            authorized_at_ns=authorized_ns,
            source_bindings=SOURCES,
        )


def test_source_drift_or_recomputed_budget_escalation_is_rejected() -> None:
    p = plan()
    with pytest.raises(c5.PilotError, match="source binding drift"):
        c5.verify_plan(p, current_bindings={**SOURCES, "v3_schedule": "c" * 64})
    modified = deepcopy(p)
    modified["limits"]["all_in_requests"] += 1
    # Re-hashing is not enough to authorize a policy change.
    modified["plan_sha256"] = c5._canonical_hash(
        {k: v for k, v in modified.items() if k != "plan_sha256"}
    )
    with pytest.raises(c5.PilotError):
        c5.verify_plan(modified, current_bindings=SOURCES)


def test_bootstrap_closes_only_the_unknown_c4_continuation_and_seeds_frame() -> None:
    p, state = ready_for_bootstrap()
    assert state["request_count"] == 1
    state = c5.transition(p, state, bootstrap_success())
    assert state["phase"] == "awaiting_frame"
    assert state["frontier_slot"] == 455430201
    assert state["bootstrap"]["gap_closed"] == {"start": 455430160, "end_exclusive": 455430201}
    state = c5.transition(
        p, state, {"kind": "frame_started", "bucket": FRAME_BUCKET, "tick_at_ns": FRAME_TICK_NS}
    )
    assert state["request_count"] == 35
    state = c5.transition(p, state, frame_success())
    assert state["phase"] == "complete"
    assert state["request_count"] == c5.MAX_REQUESTS
    assert state["frame"]["start"] == 455430201
    assert state["frontier_slot"] == 455431001


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"kind": "missed_tick"}, "missed_tick"),
        ({"kind": "intervening_gap"}, "intervening_gap"),
        ({"kind": "interruption"}, "interruption"),
        ({"kind": "transport_failure"}, "transport_failure"),
        ({"kind": "ambiguous_result"}, "ambiguous_result"),
        ({"kind": "journal_uncertain"}, "journal_uncertain"),
    ],
)
def test_any_miss_interruption_or_uncertain_transport_stops(event: dict, expected: str) -> None:
    p = plan()
    stopped = c5.transition(p, c5.initial_state(p), event)
    assert stopped["phase"] == "stopped_for_review"
    assert stopped["stop_reason"] == expected


def test_wrong_bucket_duplicates_and_bootstrap_failures_stop_without_replay() -> None:
    p = plan()
    state = c5.transition(
        p,
        c5.initial_state(p),
        {"kind": "bootstrap_started", "bucket": FRAME_BUCKET, "tick_at_ns": BOOTSTRAP_TICK_NS},
    )
    assert state["phase"] == "stopped_for_review"
    assert state["request_count"] == 0

    p, state = ready_for_bootstrap()
    duplicate = c5.transition(
        p,
        state,
        {"kind": "bootstrap_started", "bucket": BOOTSTRAP_BUCKET, "tick_at_ns": BOOTSTRAP_TICK_NS},
    )
    assert duplicate["phase"] == "stopped_for_review"
    assert duplicate["request_count"] == 1

    for changes, reason in [
        ({"outcome": "failed"}, "bootstrap_failure_or_bucket_mismatch"),
        ({"commitment": "processed"}, "bootstrap_not_finalized"),
        ({"request_sha256": "not-a-hash"}, "bootstrap_hash_binding_incomplete"),
        ({"anchor_slot": 455430158}, "bootstrap_anchor_regression"),
        ({"raw_bytes": c5.BOOTSTRAP_RAW_BYTES + 1}, "bootstrap_byte_cap"),
        ({"stored_bytes": c5.BOOTSTRAP_STORED_BYTES + 1}, "bootstrap_byte_cap"),
        ({"availability_ns": 1}, "bootstrap_time_order_invalid"),
    ]:
        bad = c5.transition(p, state, {**bootstrap_success(), **changes})
        assert bad["phase"] == "stopped_for_review"
        assert bad["stop_reason"] == reason
        assert bad["request_count"] == 1


@pytest.mark.parametrize("tick_at_ns", [None, 1791766800000000000, 1791768000000000000])
def test_bootstrap_outside_scheduled_tick_window_stops_before_request(
    tick_at_ns: int | None,
) -> None:
    p = plan()
    stopped = c5.transition(
        p,
        c5.initial_state(p),
        {"kind": "bootstrap_started", "bucket": BOOTSTRAP_BUCKET, "tick_at_ns": tick_at_ns},
    )
    assert stopped["phase"] == "stopped_for_review"
    assert stopped["stop_reason"] == "unexpected_or_missed_bootstrap_bucket"
    assert stopped["request_count"] == 0


def test_second_bucket_caps_and_width_boundaries() -> None:
    p, state = ready_for_frame()
    for changes, reason in [
        ({"request_count": c5.FRAME_REQUESTS - 1}, "frame_request_count_mismatch"),
        ({"raw_bytes": c5.FRAME_RAW_BYTES + 1}, "frame_byte_cap"),
        ({"stored_bytes": c5.FRAME_STORED_BYTES + 1}, "frame_byte_cap"),
        ({"anchor_slot": state["frontier_slot"] - 1}, "frame_width_or_frontier_invalid"),
        (
            {"anchor_slot": state["frontier_slot"] + c5.FRAME_WIDTH_MAX},
            "frame_width_or_frontier_invalid",
        ),
        ({"outcome": "partial"}, "frame_failure_or_bucket_mismatch"),
        ({"commitment": "confirmed"}, "frame_not_finalized"),
        ({"summary_sha256": None}, "frame_hash_binding_incomplete"),
    ]:
        bad = c5.transition(p, state, {**frame_success(), **changes})
        assert bad["phase"] == "stopped_for_review"
        assert bad["stop_reason"] == reason
        assert bad["request_count"] == c5.MAX_REQUESTS


def test_completed_or_stopped_state_cannot_start_a_third_bucket() -> None:
    p, state = ready_for_frame()
    state = c5.transition(p, state, frame_success())
    after = c5.transition(
        p,
        state,
        {"kind": "frame_started", "bucket": "20261012T030000Z", "tick_at_ns": FRAME_TICK_NS},
    )
    assert after["phase"] == "stopped_for_review"
    assert after["stop_reason"] == "terminal_state_is_immutable"
    assert after["request_count"] == 35


def test_exhaustive_nonnegative_byte_boundary_grid_never_exceeds_caps() -> None:
    """Deterministic generated checks over boundary-adjacent resource values."""
    p, state = ready_for_frame()
    raw_values = (0, 1, c5.FRAME_RAW_BYTES - 1, c5.FRAME_RAW_BYTES, c5.FRAME_RAW_BYTES + 1)
    stored_values = (
        0,
        1,
        c5.FRAME_STORED_BYTES - 1,
        c5.FRAME_STORED_BYTES,
        c5.FRAME_STORED_BYTES + 1,
    )
    for raw in raw_values:
        for stored in stored_values:
            result = c5.transition(p, state, frame_success(raw=raw, stored=stored))
            feasible = raw <= c5.FRAME_RAW_BYTES and stored <= c5.FRAME_STORED_BYTES
            assert (result["phase"] == "complete") is feasible
            if feasible:
                assert result["frame"]["raw_bytes"] == raw
                assert result["frame"]["stored_bytes"] == stored
