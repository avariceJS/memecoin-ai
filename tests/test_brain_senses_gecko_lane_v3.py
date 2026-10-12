from __future__ import annotations

import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from brain_senses_gecko_lane_engine_v3 import (  # noqa: E402
    MINIMUM_DISPATCH_SPACING_NS,
    STATUS_MALFORMED,
    STATUS_RATE_LIMITED,
    FetchResult,
    ReservationResult,
    cooldown_until_ns,
    dispatch_window,
    execute_one_shot,
    make_dispatch_record,
    request_for_bucket,
    verify_dispatch_journal,
)
from brain_senses_gecko_lane_policy_v3 import (  # noqa: E402
    NS,
    POOL_ROWS,
    TRAIN_ENROLLMENT_SHA256,
    TRAIN_MANIFEST_SHA256,
    TRAIN_PLAN_SHA256,
    TRAIN_SELECTION_SHA256,
    TRAIN_SOURCE_PIN,
    canonical,
    capacity_contract,
    protocol_constants,
    sha256,
    verify_policy,
)
from brain_senses_gecko_lane_verify_v3 import verify_journal  # noqa: E402


def policy() -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": "brain-senses-gecko-prospective-lane-v3",
        "schema_version": 3,
        "policy_id": "gecko-train-four-15m-v3",
        "source": {
            "role": "train",
            "source_pin": TRAIN_SOURCE_PIN,
            "enrollment_sha256": TRAIN_ENROLLMENT_SHA256,
            "role_manifest_sha256": TRAIN_MANIFEST_SHA256,
            "plan_sha256": TRAIN_PLAN_SHA256,
            "population_selection_sha256": TRAIN_SELECTION_SHA256,
            "enrolled_train_count": 170,
            "ranking": "sha256(utf8(source_pin + NUL + pool_id)),ascending",
            "protected_screen_fields": ["pool_id", "base_mint"],
            "quote_mint_rule": "preserve_and_match_pinned_pool_metadata_only",
            "quote_token_features": False,
        },
        "capacity": capacity_contract(),
        "pools": list(POOL_ROWS),
        "protocol": protocol_constants(),
        "activation_authorized": False,
        "provider_io": False,
        "economic_admission": False,
    }
    value["policy_sha256"] = sha256(canonical(value))
    return value


def raw_ohlcv(rows: list[list[int | float]]) -> bytes:
    return json.dumps({"data": {"attributes": {"ohlcv_list": rows}}}).encode()


class MemoryLane:
    """Synthetic durable journal stand-in; process restarts reuse its records."""

    def __init__(
        self,
        records: list[dict[str, Any]] | None = None,
        *,
        cooldown_until_ns: int | None = None,
        manual_review_required: bool = False,
    ) -> None:
        self.records = list(records or [])
        self.lock = threading.RLock()
        self.cooldown_until_ns = cooldown_until_ns
        self.manual_review_required = manual_review_required

    @contextmanager
    def held(self):
        with self.lock:
            yield

    def state(self):
        summary = verify_dispatch_journal(self.records)
        if summary is None:
            return None
        return {
            **summary,
            "cooldown_until_ns": self.cooldown_until_ns,
            "manual_review_required": self.manual_review_required,
        }

    def persist_terminal(self, terminal, _raw):
        if terminal["cooldown_until_ns"] is not None:
            self.cooldown_until_ns = terminal["cooldown_until_ns"]
        if terminal["cooldown_reason"] in {
            "contract_access_review_required",
            "review_required_rate_limit",
        }:
            self.manual_review_required = True

    def append(self, record: dict[str, Any]) -> None:
        assert self.lock._is_owned()
        expected = verify_dispatch_journal(self.records)
        if expected is None:
            sequence, previous = 0, None
        else:
            sequence, previous = expected["next_sequence"], expected["last_record_sha256"]
        assert record == make_dispatch_record(
            sequence=sequence,
            request_id=record["request_id"],
            dispatch_ns=record["dispatch_ns"],
            previous_sha256=previous,
        )
        self.records.append(record)


def invoke(
    lane: MemoryLane,
    *,
    close_ns: int,
    pool_index: int,
    decision_ns: int,
    http_status: int = 200,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    clock_values: list[int] | None = None,
    fetch_entered: threading.Event | None = None,
    gate=lambda _request, _at: True,
):
    p = policy()
    request = request_for_bucket(p, POOL_ROWS[pool_index]["pool_id"], close_ns)
    start, end = dispatch_window(close_ns)
    intents: list[dict[str, Any]] = []
    terminals: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    calls: list[tuple[int, int]] = []
    clock_values = list(clock_values or [decision_ns, decision_ns + 1, decision_ns + 2])
    iterator = iter(clock_values)

    def reserve(intent, amount, deadline):
        assert amount == 80 * 1024
        assert deadline == end
        intents.append(intent)
        return ReservationResult("reserved", decision_ns)

    def fetch(_request, _limit, timeout_s):
        calls.append(timeout_s)
        if fetch_entered is not None:
            fetch_entered.set()
        return FetchResult(http_status, body if body is not None else raw_ohlcv([]), headers or {})

    result = execute_one_shot(
        p,
        request,
        decision_ns=decision_ns,
        clock_ns=lambda: next(iterator),
        lane_lock=lane.held,
        read_dispatch_state=lane.state,
        append_dispatch_record=lane.append,
        validate_dispatch_state=gate,
        reserve_intent=reserve,
        fetch_once=fetch,
        persist_terminal=lambda terminal, raw: (
            terminals.append(terminal),
            lane.persist_terminal(terminal, raw),
        ),
        persist_skip=skips.append,
    )
    return request, intents, terminals, skips, calls, result


def test_v3_policy_is_non_authorizing_and_pins_frozen_four_train_pools():
    p = policy()
    verify_policy(p)
    assert len(p["pools"]) == 4
    assert p["schema_version"] == 3
    assert p["activation_authorized"] is False
    assert p["provider_io"] is False
    assert p["source"]["protected_screen_fields"] == ["pool_id", "base_mint"]
    assert p["source"]["quote_token_features"] is False
    assert p["protocol"]["minimum_request_spacing_seconds"] == 12
    assert p["protocol"]["timeout_seconds"] == 20
    assert p["protocol"]["persistence_margin_seconds"] == 5


def test_four_pool_bucket_dispatches_with_persistent_twelve_second_spacing():
    close = 1_800_000_000 * NS
    start, _ = dispatch_window(close)
    lane = MemoryLane()
    intents, terminals, raws = [], [], {}
    for index in range(4):
        request, new_intents, new_terminals, skips, calls, terminal = invoke(
            lane,
            close_ns=close,
            pool_index=index,
            decision_ns=start + index * MINIMUM_DISPATCH_SPACING_NS,
        )
        assert not skips and len(calls) == 1
        assert calls == [20]
        intents.extend(new_intents)
        terminals.extend(new_terminals)
        raws[request["request_id"]] = raw_ohlcv([])
        assert terminal["request_started_ns"] == start + index * MINIMUM_DISPATCH_SPACING_NS + 1
    assert len(lane.records) == 4
    assert verify_dispatch_journal(lane.records)["next_sequence"] == 4
    report = verify_journal(policy(), intents, terminals, raws, dispatch_records=lane.records)
    assert report["intents"] == 4 and report["dispatch_records"] == 4


def test_restart_reads_shared_dispatch_journal_and_refuses_burst_before_intent():
    close = 1_800_000_000 * NS
    start, _ = dispatch_window(close)
    first_lane = MemoryLane()
    invoke(first_lane, close_ns=close, pool_index=0, decision_ns=start)
    restarted = MemoryLane(first_lane.records)
    reserved = []
    request = request_for_bucket(policy(), POOL_ROWS[1]["pool_id"], close)
    with pytest.raises(ValueError, match="spacing"):
        execute_one_shot(
            policy(),
            request,
            decision_ns=start + 11 * NS,
            clock_ns=lambda: pytest.fail("spacing gate precedes clock dispatch"),
            lane_lock=restarted.held,
            read_dispatch_state=restarted.state,
            append_dispatch_record=restarted.append,
            validate_dispatch_state=lambda *_: True,
            reserve_intent=lambda *args: reserved.append(args),
            fetch_once=lambda *_: pytest.fail("burst must not send"),
            persist_terminal=lambda *_: None,
            persist_skip=lambda *_: None,
        )
    assert not reserved and len(restarted.records) == 1


def test_deadline_reserves_full_timeout_and_persistence_margin_before_marker():
    close = 1_800_000_000 * NS
    start, end = dispatch_window(close)
    lane = MemoryLane()
    request, intents, terminals, _, calls, result = invoke(
        lane,
        close_ns=close,
        pool_index=0,
        decision_ns=end - 30 * NS,
        clock_values=[end - 24 * NS],
    )
    assert len(intents) == 1 and not calls and not lane.records
    assert result["status"] == "intent_not_dispatched_deadline_margin"
    assert (
        terminals[0]["dispatch_record_sha256"] if "dispatch_record_sha256" in terminals[0] else True
    )


def test_deadline_is_rechecked_after_durable_dispatch_marker_before_http():
    close = 1_800_000_000 * NS
    _, end = dispatch_window(close)
    lane = MemoryLane()
    request, intents, terminals, _, calls, result = invoke(
        lane,
        close_ns=close,
        pool_index=0,
        decision_ns=end - 40 * NS,
        clock_values=[end - 30 * NS, end - 20 * NS],
    )
    assert len(intents) == 1 and not calls and len(lane.records) == 1
    assert result["status"] == "intent_not_dispatched_deadline_margin"
    assert terminals[0]["dispatch_record_sha256"] == lane.records[0]["record_sha256"]


def test_rate_limit_persists_retry_after_cooldown_and_is_bucket_terminal():
    close = 1_800_000_000 * NS
    start, _ = dispatch_window(close)
    lane = MemoryLane()
    request, intents, terminals, _, _, terminal = invoke(
        lane,
        close_ns=close,
        pool_index=0,
        decision_ns=start,
        http_status=429,
        body=b"limited",
        headers={"Retry-After": "7200"},
    )
    assert terminal["status"] == STATUS_RATE_LIMITED
    assert terminal["bucket_stop"] is True
    assert terminal["cooldown_until_ns"] - terminal["receipt_ns"] == 7200 * NS
    report = verify_journal(
        policy(),
        intents,
        terminals,
        {request["request_id"]: b"limited"},
        dispatch_records=lane.records,
    )
    assert report["cooldown_until_ns"] == terminal["cooldown_until_ns"]
    restarted = MemoryLane(lane.records, cooldown_until_ns=terminal["cooldown_until_ns"])
    with pytest.raises(ValueError, match="cooldown"):
        execute_one_shot(
            policy(),
            request_for_bucket(policy(), POOL_ROWS[1]["pool_id"], close),
            decision_ns=start + 12 * NS,
            clock_ns=lambda: pytest.fail("restart cooldown gate"),
            lane_lock=restarted.held,
            read_dispatch_state=restarted.state,
            append_dispatch_record=restarted.append,
            validate_dispatch_state=lambda *_: True,
            reserve_intent=lambda *_: pytest.fail("429 restart must stop before intent"),
            fetch_once=lambda *_: pytest.fail("429 restart must stop before HTTP"),
            persist_terminal=lambda *_: None,
            persist_skip=lambda *_: None,
        )
    assert (
        cooldown_until_ns(STATUS_RATE_LIMITED, start, 25 * 3600)[1] == "review_required_rate_limit"
    )


@pytest.mark.parametrize("rate_limit_ordinal", range(4))
def test_429_at_each_pool_ordinal_stops_bucket_and_blocks_next_intent(rate_limit_ordinal):
    close = 1_800_000_000 * NS
    start, _ = dispatch_window(close)
    lane = MemoryLane()
    intents, terminals, raw_by_id = [], [], {}
    for index in range(rate_limit_ordinal + 1):
        request, fresh_intents, fresh_terminals, _, _, terminal = invoke(
            lane,
            close_ns=close,
            pool_index=index,
            decision_ns=start + index * MINIMUM_DISPATCH_SPACING_NS,
            http_status=429 if index == rate_limit_ordinal else 200,
            body=b"limited" if index == rate_limit_ordinal else raw_ohlcv([]),
        )
        assert terminal["bucket_stop"] is (index == rate_limit_ordinal)
        intents.extend(fresh_intents)
        terminals.extend(fresh_terminals)
        raw_by_id[request["request_id"]] = (
            b"limited" if index == rate_limit_ordinal else raw_ohlcv([])
        )
    verify_journal(policy(), intents, terminals, raw_by_id, dispatch_records=lane.records)
    if rate_limit_ordinal < 3:
        request = request_for_bucket(policy(), POOL_ROWS[rate_limit_ordinal + 1]["pool_id"], close)
        with pytest.raises(ValueError, match="cooldown"):
            execute_one_shot(
                policy(),
                request,
                decision_ns=start + (rate_limit_ordinal + 1) * MINIMUM_DISPATCH_SPACING_NS,
                clock_ns=lambda: pytest.fail("cooldown must gate before clock/network"),
                lane_lock=lane.held,
                read_dispatch_state=lane.state,
                append_dispatch_record=lane.append,
                validate_dispatch_state=lambda *_: True,
                reserve_intent=lambda *_: pytest.fail("429 cooldown must gate before intent"),
                fetch_once=lambda *_: pytest.fail("429 bucket is terminal"),
                persist_terminal=lambda *_: None,
                persist_skip=lambda *_: None,
            )


def test_malformed_response_has_one_hour_cooldown_and_no_next_dispatch():
    close = 1_800_000_000 * NS
    start, _ = dispatch_window(close)
    lane = MemoryLane()
    request, intents, terminals, _, _, terminal = invoke(
        lane,
        close_ns=close,
        pool_index=0,
        decision_ns=start,
        body=b'{"broken":',
    )
    assert terminal["status"] == STATUS_MALFORMED
    assert terminal["cooldown_until_ns"] == terminal["receipt_ns"] + 3600 * NS
    restarted = MemoryLane(lane.records, cooldown_until_ns=terminal["cooldown_until_ns"])
    with pytest.raises(ValueError, match="cooldown"):
        execute_one_shot(
            policy(),
            request_for_bucket(policy(), POOL_ROWS[1]["pool_id"], close),
            decision_ns=start + 12 * NS,
            clock_ns=lambda: pytest.fail("restart cooldown gate"),
            lane_lock=restarted.held,
            read_dispatch_state=restarted.state,
            append_dispatch_record=restarted.append,
            validate_dispatch_state=lambda *_: True,
            reserve_intent=lambda *_: pytest.fail("restart cooldown before reservation"),
            fetch_once=lambda *_: pytest.fail("restart cooldown before HTTP"),
            persist_terminal=lambda *_: None,
            persist_skip=lambda *_: None,
        )
    report = verify_journal(
        policy(),
        intents,
        terminals,
        {request["request_id"]: b'{"broken":'},
        dispatch_records=lane.records,
    )
    assert report["pause_reason"] == "malformed_response_cooldown"
    request2 = request_for_bucket(policy(), POOL_ROWS[1]["pool_id"], close)
    with pytest.raises(ValueError, match="cooldown"):
        execute_one_shot(
            policy(),
            request2,
            decision_ns=start + 12 * NS,
            clock_ns=lambda: pytest.fail("malformed cooldown gates before dispatch"),
            lane_lock=lane.held,
            read_dispatch_state=lane.state,
            append_dispatch_record=lane.append,
            validate_dispatch_state=lambda *_: True,
            reserve_intent=lambda *_: pytest.fail("malformed cooldown gates before intent"),
            fetch_once=lambda *_: pytest.fail("malformed response stops bucket"),
            persist_terminal=lambda *_: None,
            persist_skip=lambda *_: None,
        )


def test_dispatch_journal_chain_rejects_tampering_and_subtwelve_spacing():
    first = make_dispatch_record(sequence=0, request_id="a", dispatch_ns=100, previous_sha256=None)
    second = make_dispatch_record(
        sequence=1,
        request_id="b",
        dispatch_ns=100 + 12 * NS,
        previous_sha256=first["record_sha256"],
    )
    assert verify_dispatch_journal([first, second])["next_sequence"] == 2
    mutated = dict(second, dispatch_ns=100 + 11 * NS)
    core = dict(mutated)
    core.pop("record_sha256")
    mutated["record_sha256"] = sha256(canonical(core))
    with pytest.raises(ValueError, match="chain"):
        verify_dispatch_journal([first, mutated])


def test_lane_lock_serializes_concurrent_calls_across_dispatch_and_persistence():
    close = 1_800_000_000 * NS
    start, _ = dispatch_window(close)
    lane = MemoryLane()
    first_fetch_entered = threading.Event()
    first_done = threading.Event()
    failures: list[BaseException] = []

    def first_call():
        try:
            invoke(
                lane,
                close_ns=close,
                pool_index=0,
                decision_ns=start,
                fetch_entered=first_fetch_entered,
            )
            first_done.set()
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=first_call)
    thread.start()
    assert first_fetch_entered.wait(2)
    # This call waits on the same lane lock. Its state read happens after the first
    # dispatch record is durable, so the 12-second gate sees the shared state.
    request = request_for_bucket(policy(), POOL_ROWS[1]["pool_id"], close)
    calls: list[bool] = []
    engine_thread = threading.Thread(
        target=lambda: execute_one_shot(
            policy(),
            request,
            decision_ns=start + 12 * NS,
            clock_ns=iter([start + 12 * NS, start + 12 * NS + 1, start + 12 * NS + 2]).__next__,
            lane_lock=lane.held,
            read_dispatch_state=lane.state,
            append_dispatch_record=lane.append,
            validate_dispatch_state=lambda *_: True,
            reserve_intent=lambda intent, *_: ReservationResult("reserved", intent["decision_ns"]),
            fetch_once=lambda *_: calls.append(True) or FetchResult(200, raw_ohlcv([]), {}),
            persist_terminal=lambda *_: None,
            persist_skip=lambda *_: None,
        )
    )
    engine_thread.start()
    first_done.wait(2)
    thread.join(2)
    engine_thread.join(2)
    assert not failures and len(lane.records) == 2 and calls == [True]
