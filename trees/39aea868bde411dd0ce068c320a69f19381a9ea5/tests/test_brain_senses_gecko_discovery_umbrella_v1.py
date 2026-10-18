from __future__ import annotations

import copy
import hashlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tools import brain_senses_gecko_discovery_umbrella_v1 as umbrella

H = "a" * 64


def ident(value: int) -> str:
    return f"{value:032x}"


def genesis():
    return umbrella.build_umbrella(
        umbrella_id=ident(1),
        release_sha256=H,
        source_sha256="b" * 64,
        policy_sha256="c" * 64,
        start_ns=1_800_000_000 * umbrella.NS,
        lease_id=ident(2),
        root_id=ident(3),
    )


def gap_batch(count: int, reason: str = "not_scheduled"):
    return [{"status": "gap", "reason": reason} for _ in range(count)]


def request(request_id: int, dispatch_ns: int, *, status="confirmed", raw_bytes=123):
    row = {
        "request_id": hashlib.sha256(str(request_id).encode()).hexdigest(),
        "dispatch_ns": dispatch_ns,
        "status": status,
    }
    if status == "confirmed":
        row["raw_bytes"] = raw_bytes
    elif status == "ambiguous_unclassified":
        row["classification"] = "prior_ambiguity_unclassified"
    elif status == "classified_transient":
        row["classification_sha256"] = H
        row["cooldown_until_ns"] = dispatch_ns + 60 * umbrella.NS
    elif status == "fatal_stop":
        row["classification"] = "fatal_fail_closed"
    return row


def append_first_scan(state, reqs):
    return umbrella.record_scans(state, [{"status": "scanned", "requests": reqs}])


def seal_gaps(state, successor_id=4):
    now = state["epoch_end_ns"] - umbrella.STANDBY_NS
    prepared = umbrella.prepare_handoff(
        state,
        successor_lease_id=ident(successor_id),
        successor_root_id=ident(successor_id + 1),
        now_ns=now,
    )
    remaining = (state["epoch_index"] + 1) * umbrella.SCANS_PER_EPOCH - state["next_scan_index"]
    state = umbrella.record_scans(state, gap_batch(remaining))
    seal = umbrella.seal_epoch(
        state,
        prepared,
        now_ns=state["epoch_end_ns"],
        terminal_sha256=H,
        independent_replay_sha256="d" * 64,
        quiescence_sha256="e" * 64,
        all_requests_settled=True,
        independently_replayed=True,
        transport_quiescent=True,
        pins_and_accounting_verified=True,
    )
    return state, prepared, seal


def test_policy_arithmetic_and_fixed_45_day_schedule():
    assert umbrella.EPOCH_COUNT == 15
    assert umbrella.EPOCH_NS * umbrella.EPOCH_COUNT == umbrella.UMBRELLA_NS
    assert umbrella.SCANS_PER_EPOCH * umbrella.EPOCH_COUNT == 12_960
    assert umbrella.MAX_REQUESTS_TOTAL == 12_960 * umbrella.MAX_REQUESTS_PER_SCAN
    assert (
        umbrella.MAX_RAW_BYTES_TOTAL
        == umbrella.MAX_REQUESTS_TOTAL * umbrella.MAX_RAW_BYTES_PER_REQUEST
    )
    assert umbrella.MAX_PHYSICAL_BYTES_TOTAL == (
        umbrella.MAX_REQUESTS_TOTAL * umbrella.MAX_PHYSICAL_BYTES_PER_REQUEST
    )
    assert umbrella.MAX_REQUESTS_PER_DAY == 864
    assert umbrella.MAX_REQUESTS_PER_ROLLING_30D == 25_920


def test_dispatch_gates_and_epoch_end_boundary():
    state = genesis()
    args = {
        "release_sha256": H,
        "source_sha256": "b" * 64,
        "policy_sha256": "c" * 64,
        "source_verified": True,
        "storage_verified": True,
        "identity_verified": True,
        "accounting_verified": True,
        "quiescence_verified": True,
        "prior_handoff_replayed": True,
    }
    allowed = umbrella.authorize_dispatch(state, now_ns=state["epoch_start_ns"], **args)
    assert allowed["allowed"] is True
    assert allowed["provider_io"] is False
    assert (
        umbrella.authorize_dispatch(state, now_ns=state["epoch_end_ns"], **args)["allowed"] is False
    )
    assert (
        umbrella.authorize_dispatch(
            state, now_ns=state["epoch_start_ns"], **{**args, "storage_verified": False}
        )["allowed"]
        is False
    )


def test_ambiguity_is_preserved_as_debt_and_stops_dispatch():
    state = genesis()
    scan_ns = state["start_ns"]
    state = append_first_scan(
        state,
        [request(1, scan_ns + 1, status="ambiguous_unclassified")],
    )
    assert state["accounting"]["ambiguous_request_count"] == 1
    assert state["accounting"]["ambiguous_raw_debt_bytes"] == umbrella.MAX_RAW_BYTES_PER_REQUEST
    assert (
        state["accounting"]["ambiguous_physical_debt_bytes"]
        == umbrella.MAX_PHYSICAL_BYTES_PER_REQUEST
    )
    assert state["stop_latched"] is True
    decision = umbrella.authorize_dispatch(
        state,
        now_ns=scan_ns + 600,
        release_sha256=H,
        source_sha256="b" * 64,
        policy_sha256="c" * 64,
        source_verified=True,
        storage_verified=True,
        identity_verified=True,
        accounting_verified=True,
        quiescence_verified=True,
        prior_handoff_replayed=True,
    )
    assert decision["allowed"] is False
    assert "stop_latch" in decision["reason"]


def test_classified_transient_cooldown_and_fatal_status():
    state = genesis()
    start = state["start_ns"]
    transient = request(2, start + 1, status="classified_transient")
    transient["cooldown_until_ns"] = start + 400 * umbrella.NS
    state = append_first_scan(state, [transient])
    assert state["cooldown_until_ns"] == start + 400 * umbrella.NS
    with pytest.raises(umbrella.UmbrellaError, match="cooldown_active"):
        umbrella.record_scans(
            state, [{"status": "scanned", "requests": [request(3, start + 300 * umbrella.NS + 1)]}]
        )
    fatal = genesis()
    fatal = append_first_scan(fatal, [request(4, start + 2, status="fatal_stop")])
    assert fatal["stop_latched"] is True


def test_duplicate_or_malformed_request_and_dispatch_rate_fail_closed():
    state = genesis()
    start = state["start_ns"]
    duplicated = request(5, start + 10)
    with pytest.raises(umbrella.UmbrellaError, match="request_replay"):
        append_first_scan(state, [duplicated, duplicated])
    with pytest.raises(umbrella.UmbrellaError, match="rolling_60s_cap"):
        umbrella.record_scans(
            state,
            [
                {
                    "status": "scanned",
                    "requests": [request(i, start + 299 * umbrella.NS + 1) for i in range(10, 13)],
                },
                {
                    "status": "scanned",
                    "requests": [request(i, start + 300 * umbrella.NS + 1) for i in range(13, 16)],
                },
            ],
        )
    malformed = request(20, start + 1)
    malformed["raw_bytes"] = umbrella.MAX_RAW_BYTES_PER_REQUEST + 1
    with pytest.raises(umbrella.UmbrellaError, match="confirmed_raw_size"):
        append_first_scan(state, [malformed])


def test_standby_prepare_seal_transfer_preserve_denominator_and_accounting():
    state = genesis()
    start = state["start_ns"]
    state = append_first_scan(state, [request(31, start + 1, raw_bytes=19)])
    state = umbrella.record_scans(state, gap_batch(10))
    assert umbrella.standby_due(state, state["epoch_end_ns"] - umbrella.STANDBY_NS)
    before_seal, prepared, seal = seal_gaps(state, successor_id=7)
    assert seal["dispatch_allowed"] is False
    assert seal["accounting"]["request_count"] == 1
    next_state = umbrella.transfer_epoch(before_seal, prepared, seal)
    assert next_state["epoch_index"] == 1
    assert next_state["next_scan_index"] == umbrella.SCANS_PER_EPOCH
    assert next_state["accounting"] == before_seal["accounting"]
    assert next_state["requests"] == before_seal["requests"]
    assert next_state["prior_seal_sha256"] == seal["seal_sha256"]
    assert len(next_state["epoch_history"]) == 2


def test_seal_requires_exact_frozen_prefix_and_complete_scan_denominator():
    state = genesis()
    state = umbrella.record_scans(state, [{"status": "gap", "reason": "first"}])
    prepared = umbrella.prepare_handoff(
        state,
        successor_lease_id=ident(40),
        successor_root_id=ident(41),
        now_ns=state["epoch_end_ns"] - umbrella.STANDBY_NS,
    )
    full = umbrella.record_scans(state, gap_batch(umbrella.SCANS_PER_EPOCH - 1))
    mutated = copy.deepcopy(full)
    mutated["scan_records"][0]["reason"] = "edited"
    mutated["state_sha256"] = umbrella.sha256(umbrella.canonical(umbrella._state_core(mutated)))
    with pytest.raises(umbrella.UmbrellaError):
        umbrella.seal_epoch(
            mutated,
            prepared,
            now_ns=mutated["epoch_end_ns"],
            terminal_sha256=H,
            independent_replay_sha256="d" * 64,
            quiescence_sha256="e" * 64,
            all_requests_settled=True,
            independently_replayed=True,
            transport_quiescent=True,
            pins_and_accounting_verified=True,
        )
    partial = umbrella.record_scans(state, gap_batch(umbrella.SCANS_PER_EPOCH - 2))
    with pytest.raises(umbrella.UmbrellaError, match="terminal_scan_denominator"):
        umbrella.seal_epoch(
            partial,
            prepared,
            now_ns=partial["epoch_end_ns"],
            terminal_sha256=H,
            independent_replay_sha256="d" * 64,
            quiescence_sha256="e" * 64,
            all_requests_settled=True,
            independently_replayed=True,
            transport_quiescent=True,
            pins_and_accounting_verified=True,
        )


@given(st.lists(st.booleans(), min_size=0, max_size=30))
def test_gap_property_preserves_exact_scan_order(bits):
    state = genesis()
    rows = [
        (
            {"status": "gap", "reason": "property_gap"}
            if bit
            else {"status": "gap", "reason": "other_gap"}
        )
        for bit in bits
    ]
    if rows:
        result = umbrella.record_scans(state, rows)
        assert result["next_scan_index"] == len(bits)
        assert [row["scan_index"] for row in result["scan_records"]] == list(range(len(bits)))
        assert result["accounting"]["gap_count"] == len(bits)


def test_14_exact_epoch_handoffs_carry_accounting_and_never_reuse_ids():
    state = genesis()
    prior_seal = None
    for epoch in range(umbrella.EPOCH_COUNT):
        if epoch:
            assert state["epoch_index"] == epoch
            assert state["prior_seal_sha256"] == prior_seal
        prepared = None
        if epoch < umbrella.EPOCH_COUNT - 1:
            prepared = umbrella.prepare_handoff(
                state,
                successor_lease_id=ident(100 + epoch * 2),
                successor_root_id=ident(101 + epoch * 2),
                now_ns=state["epoch_end_ns"] - umbrella.STANDBY_NS,
            )
        if state["next_scan_index"] < (epoch + 1) * umbrella.SCANS_PER_EPOCH:
            state = umbrella.record_scans(
                state,
                gap_batch((epoch + 1) * umbrella.SCANS_PER_EPOCH - state["next_scan_index"]),
            )
        if epoch == umbrella.EPOCH_COUNT - 1:
            assert state["next_scan_index"] == 12_960
            assert state["epoch_end_ns"] == state["end_ns"]
            break
        seal = umbrella.seal_epoch(
            state,
            prepared,
            now_ns=state["epoch_end_ns"],
            terminal_sha256=hashlib.sha256(f"terminal-{epoch}".encode()).hexdigest(),
            independent_replay_sha256=hashlib.sha256(f"replay-{epoch}".encode()).hexdigest(),
            quiescence_sha256=hashlib.sha256(f"quiet-{epoch}".encode()).hexdigest(),
            all_requests_settled=True,
            independently_replayed=True,
            transport_quiescent=True,
            pins_and_accounting_verified=True,
        )
        prior_seal = seal["seal_sha256"]
        transferred = umbrella.transfer_epoch(state, prepared, seal)
        # A simulated crash after transfer can reconstruct from the serialized mapping.
        state = umbrella.verify_state(copy.deepcopy(transferred))
    assert len(state["epoch_history"]) == 15
    assert len({row["lease_id"] for row in state["epoch_history"]}) == 15
    assert len({row["root_id"] for row in state["epoch_history"]}) == 15
    assert state["accounting"]["gap_count"] == 12_960


def test_day30_day40_day45_alerts_and_dispatch_closure():
    state = genesis()
    assert umbrella.alerts(state, state["start_ns"] + 30 * umbrella.DAY_NS) == [
        "day30_usage_review"
    ]
    assert umbrella.alerts(state, state["start_ns"] + 40 * umbrella.DAY_NS) == [
        "day30_usage_review",
        "day40_remaining_budget_review",
    ]
    assert umbrella.alerts(state, state["end_ns"]) == [
        "day30_usage_review",
        "day40_remaining_budget_review",
        "day45_dispatch_closed",
    ]
    decision = umbrella.authorize_dispatch(
        state,
        now_ns=state["end_ns"],
        release_sha256=H,
        source_sha256="b" * 64,
        policy_sha256="c" * 64,
        source_verified=True,
        storage_verified=True,
        identity_verified=True,
        accounting_verified=True,
        quiescence_verified=True,
        prior_handoff_replayed=True,
    )
    assert decision["allowed"] is False
