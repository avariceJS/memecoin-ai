from __future__ import annotations

import copy

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools import brain_senses_gecko_lane_policy_v6 as policy_v6
from tools import brain_senses_gecko_v7_handoff_policy_v1 as handoff_v7


def _sha(label: str) -> str:
    return handoff_v7.sha256(label.encode())


def _plan() -> dict:
    selected = [f"pool-{i:03d}" for i in range(6)]
    core = {
        "schema": "brain-senses-gecko-capture-plan-v6",
        "schema_version": 6,
        "policy_id": "gecko-train-six-15m-rotating-scout-v6",
        "policy_sha256": _sha("v6-policy"),
        "selector_policy_id": "gecko-train-six-15m-rotating-scout-v1",
        "selector_sha256": _sha("selector"),
        "population_sha256": policy_v6.POPULATION_SHA256,
        "bucket_close_ns": 1_791_827_100_000_000_000,
        "cursor_in": 0,
        "cursor_out": 6,
        "retention": [
            {"pool_id": selected[4], "retained": True},
            {"pool_id": selected[5], "retained": True},
        ],
        "scout_pool_ids": selected[:4],
        "selected_pool_ids": selected,
        "provider_io": False,
        "retry": False,
        "backfill": False,
    }
    return {**core, "plan_sha256": handoff_v7.sha256(handoff_v7.canonical(core))}


def _evidence() -> dict:
    plan = _plan()
    rows = []
    for slot in range(4):
        rows.append(
            {
                "slot_index": slot,
                "pool_id": plan["selected_pool_ids"][slot],
                "disposition": "confirmed",
                "request_id": _sha(f"request-{slot}"),
                "request_sha256": _sha(f"request-bytes-{slot}"),
                "intent_sha256": _sha(f"intent-{slot}"),
                "dispatch_sha256": _sha(f"dispatch-{slot}"),
                "outcome_sha256": _sha(f"outcome-{slot}"),
                "confirmation_sha256": _sha(f"confirmation-{slot}"),
                "raw_bytes": 1000 + slot,
            }
        )
    rows.append(
        {
            "slot_index": 4,
            "pool_id": plan["selected_pool_ids"][4],
            "disposition": "prior_ambiguity_unclassified",
            "request_id": _sha("ambiguous-request"),
            "request_sha256": _sha("ambiguous-request-bytes"),
            "intent_sha256": _sha("ambiguous-intent"),
            "dispatch_sha256": _sha("ambiguous-dispatch"),
            "raw_bytes_upper_bound": 16_384,
            "physical_bytes_upper_bound": 81_920,
            "classification": "prior_ambiguity_unclassified",
        }
    )
    rows.append(
        {
            "slot_index": 5,
            "pool_id": plan["selected_pool_ids"][5],
            "disposition": "missed",
        }
    )
    return {
        "old_root": {
            "root_id": "94da" + "a" * 28,
            "release_id": "a" * 32,
            "release_sha256": _sha("release"),
            "source_sha256": _sha("source"),
            "policy_sha256": _sha("v6-policy"),
            "epoch_sha256": _sha("epoch"),
            "root_prefix_sha256": _sha("prefix"),
            "replay_sha256": _sha("old-replay"),
            "plan": plan,
        },
        "slots": rows,
        "all_root_census": {
            "status": "verified",
            "census_sha256": _sha("all-roots-census"),
            "root_replay_sha256": _sha("all-roots-replay"),
            "root_count": 44,
            "all_roots_verified": True,
        },
        "quiescence": {
            "status": "verified",
            "proof_sha256": _sha("quiescence"),
            "recovered_at_ns": 1_791_827_200_000_000_000,
            "old_gecko_agents_absent": True,
            "transport_children_quiescent": True,
            "unresolved_transport_children": 0,
        },
        "population": {
            "source_pin": policy_v6.SOURCE_PIN,
            "policy_id": "gecko-train-six-15m-rotating-scout-v1",
            "population_sha256": policy_v6.POPULATION_SHA256,
            "enrolled_train_count": 170,
            "preregistration_sha256": handoff_v7.PREREGISTRATION_SHA256,
        },
    }


def _receipt() -> dict:
    return handoff_v7.build_handoff(_evidence())


def _gates(receipt: dict, now_ns: int) -> dict:
    return {
        "now_ns": now_ns,
        "current_population_sha256": policy_v6.POPULATION_SHA256,
        "current_source_pin": policy_v6.SOURCE_PIN,
        "old_root_replay_sha256": receipt["old_v6"]["root_replay_sha256"],
        "all_root_census_sha256": receipt["old_v6"]["all_root_census_sha256"],
        "old_agents_absent": True,
        "transport_children_quiescent": True,
        "clock_verified": True,
        "storage_verified": True,
    }


def test_builds_exact_six_slot_handoff_and_conservative_max_charge() -> None:
    receipt = _receipt()
    handoff_v7.verify_handoff(receipt)
    assert [row["disposition"] for row in receipt["slots"]] == [
        "confirmed",
        "confirmed",
        "confirmed",
        "confirmed",
        "prior_ambiguity_unclassified",
        "missed",
    ]
    assert receipt["old_v6"]["cursor_out"] == 6
    assert (
        receipt["prior_ambiguity_unclassified"]["classification"] == "prior_ambiguity_unclassified"
    )
    assert receipt["prior_ambiguity_unclassified"]["raw_bytes_upper_bound"] == 16_384
    assert receipt["prior_ambiguity_unclassified"]["physical_bytes_upper_bound"] == 81_920
    assert receipt["max_charge"] == {
        "attempt_count": 5,
        "raw_bytes_upper_bound": 20_390,
        "confirmed_raw_bytes": 4_006,
        "ambiguous_raw_reserve_bytes": 16_384,
        "physical_bytes_upper_bound": 409_600,
        "ambiguous_physical_reserve_bytes": 81_920,
    }
    assert (
        receipt["recovery"]["cooldown_until_ns"]
        == receipt["recovery"]["recovered_at_ns"] + 900_000_000_000
    )


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda e: e["old_root"].update(root_id="00" * 16), "handoff_old_root_identity"),
        (lambda e: e["old_root"].update(source_sha256="not-a-hash"), "handoff_old_hashes"),
        (lambda e: e["old_root"]["plan"].update(cursor_out=12), "v6_plan_binding_or_hash"),
        (
            lambda e: e["slots"].__setitem__(5, copy.deepcopy(e["slots"][4])),
            "v6_handoff_slot_order",
        ),
        (lambda e: e["slots"][4].update(raw_bytes_upper_bound=1), "v6_handoff_ambiguous_reserve"),
        (lambda e: e["slots"][5].update(request_id=_sha("bad")), "v6_handoff_slot_shape"),
        (
            lambda e: e["all_root_census"].update(all_roots_verified=False),
            "handoff_census_unverified",
        ),
        (
            lambda e: e["quiescence"].update(old_gecko_agents_absent=False),
            "handoff_quiescence_unverified",
        ),
        (
            lambda e: e["population"].update(population_sha256=_sha("changed")),
            "handoff_population_changed",
        ),
    ],
)
def test_handoff_builder_rejects_mutated_evidence(mutate, reason: str) -> None:
    evidence = _evidence()
    mutate(evidence)
    with pytest.raises(handoff_v7.HandoffError, match=reason):
        handoff_v7.build_handoff(evidence)


def test_receipt_hash_and_charge_tampering_are_rejected() -> None:
    receipt = _receipt()
    changed = copy.deepcopy(receipt)
    changed["prior_ambiguity_unclassified"]["classification"] = "transient"
    with pytest.raises(handoff_v7.HandoffError, match="handoff_receipt_hash"):
        handoff_v7.verify_handoff(changed)
    changed = copy.deepcopy(receipt)
    changed["max_charge"]["raw_bytes_upper_bound"] -= 1
    core = dict(changed)
    core.pop("handoff_sha256")
    changed["handoff_sha256"] = handoff_v7.sha256(handoff_v7.canonical(core))
    with pytest.raises(handoff_v7.HandoffError, match="handoff_charge_mismatch"):
        handoff_v7.verify_handoff(changed)
    changed = copy.deepcopy(receipt)
    changed["slots"][0]["pool_id"] = "spliced-pool"
    core = dict(changed)
    core.pop("handoff_sha256")
    changed["handoff_sha256"] = handoff_v7.sha256(handoff_v7.canonical(core))
    with pytest.raises(handoff_v7.HandoffError, match="handoff_slots_plan_join"):
        handoff_v7.verify_handoff(changed)


def test_successor_emits_null_id_gaps_and_preserves_cursor() -> None:
    receipt = _receipt()
    now_ns = (
        receipt["old_v6"]["bucket_close_ns"] + 3 * handoff_v7.BUCKET_NS + handoff_v7.DISPATCH_END_NS
    )
    decision = handoff_v7.decide_successor(receipt, **_gates(receipt, now_ns))
    assert decision["status"] == "ready_for_fresh_plan"
    assert decision["cursor_in"] == decision["cursor_out"] == 6
    assert decision["retention"] == []
    assert len(decision["gaps"]) == 3
    assert all(
        gap["selected_pool_ids"] is None and gap["cursor_in"] == gap["cursor_out"] == 6
        for gap in decision["gaps"]
    )
    assert decision["bucket_close_ns"] > now_ns
    assert decision["bucket_close_ns"] % handoff_v7.BUCKET_NS == 0


def test_gap_waits_until_the_bucket_dispatch_window_has_closed() -> None:
    receipt = _receipt()
    first_unplanned_close = receipt["old_v6"]["bucket_close_ns"] + handoff_v7.BUCKET_NS
    before_expiry = handoff_v7.decide_successor(
        receipt,
        **_gates(receipt, first_unplanned_close + handoff_v7.DISPATCH_END_NS - 1),
    )
    assert before_expiry["gaps"] == []
    at_expiry = handoff_v7.decide_successor(
        receipt,
        **_gates(receipt, first_unplanned_close + handoff_v7.DISPATCH_END_NS),
    )
    assert [gap["bucket_close_ns"] for gap in at_expiry["gaps"]] == [first_unplanned_close]


@given(
    elapsed_buckets=st.integers(min_value=0, max_value=500),
    offset_ns=st.integers(min_value=0, max_value=handoff_v7.BUCKET_NS - 1),
)
@settings(max_examples=80, deadline=None)
def test_elapsed_gap_count_is_exact_and_never_advances_cursor(
    elapsed_buckets: int, offset_ns: int
) -> None:
    receipt = _receipt()
    old_close = receipt["old_v6"]["bucket_close_ns"]
    now_ns = old_close + elapsed_buckets * handoff_v7.BUCKET_NS + offset_ns
    decision = handoff_v7.decide_successor(receipt, **_gates(receipt, now_ns))
    expected = max(0, elapsed_buckets - 1 + (1 if offset_ns >= handoff_v7.DISPATCH_END_NS else 0))
    assert len(decision["gaps"]) == expected
    assert all(gap["selected_pool_ids"] is None for gap in decision["gaps"])
    assert all(gap["cursor_in"] == gap["cursor_out"] == 6 for gap in decision["gaps"])


def test_cooldown_boundary_and_fresh_future_bucket() -> None:
    receipt = _receipt()
    until = receipt["recovery"]["cooldown_until_ns"]
    waiting = handoff_v7.decide_successor(receipt, **_gates(receipt, until - 1))
    assert waiting["status"] == "cooldown_wait"
    assert waiting["bucket_close_ns"] is None
    ready = handoff_v7.decide_successor(receipt, **_gates(receipt, until))
    assert ready["status"] == "ready_for_fresh_plan"
    assert ready["bucket_close_ns"] > until
    assert ready["bucket_close_ns"] + handoff_v7.DISPATCH_EARLIEST_NS >= until


@pytest.mark.parametrize(
    "field",
    [
        "current_population_sha256",
        "current_source_pin",
        "old_root_replay_sha256",
        "all_root_census_sha256",
    ],
)
def test_successor_stops_on_source_population_or_replay_drift(field: str) -> None:
    receipt = _receipt()
    now_ns = receipt["recovery"]["cooldown_until_ns"]
    kwargs = _gates(receipt, now_ns)
    kwargs[field] = "f" * 64
    decision = handoff_v7.decide_successor(receipt, **kwargs)
    assert decision["status"] == "stop"
    assert decision["bucket_close_ns"] is None
    assert decision["provider_io"] is False


def test_new_plan_must_continue_cursor_six_without_prior_retention() -> None:
    receipt = _receipt()
    now_ns = receipt["recovery"]["cooldown_until_ns"]
    decision = handoff_v7.decide_successor(receipt, **_gates(receipt, now_ns))
    plan = {
        "policy_id": handoff_v7.V7_POLICY_ID,
        "population_sha256": policy_v6.POPULATION_SHA256,
        "cursor_in": 6,
        "bucket_close_ns": decision["bucket_close_ns"],
        "selected_pool_ids": [f"next-{i}" for i in range(6)],
        "retention": [],
    }
    handoff_v7.verify_successor_plan(plan, decision)
    plan["cursor_in"] = 0
    with pytest.raises(handoff_v7.HandoffError, match="successor_plan_continuity"):
        handoff_v7.verify_successor_plan(plan, decision)


def test_duplicate_claim_and_prior_request_id_replay_are_rejected() -> None:
    close = 1_791_828_000_000_000_000
    claim = {"bucket_close_ns": close, "pool_id": "pool-x", "request_id": _sha("attempt")}
    handoff_v7.verify_unique_claims([claim])
    assert not handoff_v7.may_create_attempt_claim([claim], **{k: claim[k] for k in claim})
    with pytest.raises(handoff_v7.HandoffError, match="attempt_claim_duplicate_pool_bucket"):
        handoff_v7.verify_unique_claims([claim, {**claim, "request_id": _sha("second")}])
    with pytest.raises(handoff_v7.HandoffError, match="attempt_claim_duplicate_request_id"):
        handoff_v7.verify_unique_claims(
            [
                claim,
                {
                    "bucket_close_ns": close + handoff_v7.BUCKET_NS,
                    "pool_id": "pool-y",
                    "request_id": claim["request_id"],
                },
            ]
        )


def test_unclassified_ambiguity_latches_stop_even_after_repeat() -> None:
    for timestamp in (1_000, 2_000, 3_000):
        result = handoff_v7.classify_v7_outcome("ambiguous_unclassified", event_ns=timestamp)
        assert result == {
            "decision": "stop",
            "reason": "ambiguous_unclassified",
            "may_retry": False,
            "latches_stop": True,
            "reviewed_handoff_required": True,
        }


def test_verified_transient_can_resume_only_after_explicit_cooldown() -> None:
    stop = handoff_v7.classify_v7_outcome("transport", event_ns=10_000)
    assert stop["decision"] == "stop"
    resumed = handoff_v7.classify_v7_outcome(
        "transport",
        event_ns=10_000,
        independent_transient_classification="verified_transient",
        verified_cooldown_until_ns=20_000,
    )
    assert resumed["decision"] == "cooldown_resume_allowed"
    assert resumed["cooldown_until_ns"] == 20_000
    assert resumed["may_retry_same_request"] is False


def test_429_uses_longer_verified_retry_after_and_requires_fresh_limit_check() -> None:
    event_ns = 100_000
    no_check = handoff_v7.classify_v7_outcome("http_429", event_ns=event_ns)
    assert no_check["decision"] == "stop"
    checked = handoff_v7.classify_v7_outcome(
        "http_429",
        event_ns=event_ns,
        fresh_account_limit_check="verified_within_limit",
        retry_after_until_ns=event_ns + 7_200_000_000_000,
        retry_after_verified=True,
    )
    assert checked["decision"] == "cooldown_resume_allowed_after_recheck"
    assert checked["cooldown_until_ns"] == event_ns + 7_200_000_000_000
    assert checked["may_retry_same_request"] is False
    with pytest.raises(handoff_v7.HandoffError, match="retry_after_invalid"):
        handoff_v7.classify_v7_outcome(
            "http_429",
            event_ns=event_ns,
            fresh_account_limit_check="verified_within_limit",
            retry_after_until_ns=event_ns + 7_200_000_000_000,
            retry_after_verified=False,
        )


@pytest.mark.parametrize(
    "status",
    [
        "http_401",
        "http_403",
        "payment_required",
        "quota_exceeded",
        "cap_exceeded",
        "clock_invalid",
        "storage_invalid",
        "replay_invalid",
    ],
)
def test_fatal_operational_outcomes_stop(status: str) -> None:
    assert handoff_v7.classify_v7_outcome(status, event_ns=1_000)["decision"] == "stop"
