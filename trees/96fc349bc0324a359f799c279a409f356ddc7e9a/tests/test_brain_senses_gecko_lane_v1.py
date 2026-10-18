import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from brain_senses_gecko_lane_engine_v1 import (
    STATUS_NOT_DISPATCHED,
    STATUS_PROVIDER_ERROR,
    STATUS_RATE_LIMITED,
    STATUS_RECEIVED,
    STATUS_TRANSPORT,
    STATUS_UNSUPPORTED,
    FetchResult,
    ReservationResult,
    bucket_schedule,
    classify_dispatch,
    cooldown_until_ns,
    dispatch_window,
    execute_one_shot,
    request_for_bucket,
)
from brain_senses_gecko_lane_policy_v1 import (
    NS,
    POOL_ROWS,
    SOURCE_PATHS,
    TRAIN_COUNT,
    TRAIN_ENROLLMENT_SHA256,
    TRAIN_MANIFEST_SHA256,
    TRAIN_PLAN_SHA256,
    TRAIN_SELECTION_SHA256,
    TRAIN_SOURCE_PIN,
    canonical,
    compile_pinned_policy,
    protocol_constants,
    rank_and_select,
    rank_pool,
    sha256,
    verify_policy,
)
from brain_senses_gecko_lane_verify_v1 import verify_journal


def policy():
    body = {
        "schema": "brain-senses-gecko-prospective-lane-v1",
        "schema_version": 1,
        "policy_id": "gecko-train-four-15m-v1",
        "source": {
            "role": "train",
            "source_pin": TRAIN_SOURCE_PIN,
            "enrollment_sha256": TRAIN_ENROLLMENT_SHA256,
            "role_manifest_sha256": TRAIN_MANIFEST_SHA256,
            "plan_sha256": TRAIN_PLAN_SHA256,
            "population_selection_sha256": TRAIN_SELECTION_SHA256,
            "enrolled_train_count": TRAIN_COUNT,
            "ranking": "sha256(utf8(source_pin + NUL + pool_id)),ascending",
            "protected_screen_fields": ["pool_id", "base_mint"],
            "quote_mint_rule": "preserve_and_match_pinned_pool_metadata_only",
            "quote_token_features": False,
        },
        "pools": list(POOL_ROWS),
        "protocol": protocol_constants(),
        "activation_authorized": False,
        "provider_io": False,
        "economic_admission": False,
    }
    body["policy_sha256"] = sha256(canonical(body))
    return body


def raw_ohlcv(rows):
    return json.dumps(
        {"data": {"attributes": {"ohlcv_list": rows}}}, separators=(",", ":")
    ).encode()


def execute(
    policy_obj, *, http_status=200, raw=None, headers=None, fetch_error=None, dispatch_at=None
):
    close = 1_800_000_000 * NS
    request = request_for_bucket(policy_obj, POOL_ROWS[0]["pool_id"], close)
    start, _ = dispatch_window(close)
    events = []
    intents = []
    terminals = []
    persisted_raw = []
    calls = []

    def reserve(intent, reserve_bytes, deadline_ns):
        events.append("reserve")
        assert reserve_bytes == 80 * 1024
        assert deadline_ns == dispatch_window(close)[1]
        intents.append(intent)
        return ReservationResult("reserved", intent["decision_ns"])

    def fetch(req, max_bytes):
        events.append("fetch")
        calls.append((req["request_id"], max_bytes))
        if fetch_error:
            raise fetch_error
        return FetchResult(http_status, raw or b"", headers or {})

    def persist(terminal, body):
        events.append("persist")
        terminals.append(terminal)
        persisted_raw.append(body)

    samples = iter(
        [
            dispatch_at if dispatch_at is not None else start,
            (dispatch_at if dispatch_at is not None else start) + 100,
        ]
    )
    terminal = execute_one_shot(
        policy_obj,
        request,
        decision_ns=start,
        previous_dispatch_ns=None,
        clock_ns=lambda: next(samples),
        validate_dispatch_state=lambda _request, _decision: True,
        reserve_intent=reserve,
        fetch_once=fetch,
        persist_terminal=persist,
        persist_skip=lambda _skip: pytest.fail("unexpected skip"),
    )
    assert events[0] == "reserve"
    if calls:
        assert events.index("fetch") > events.index("reserve")
    assert events[-1] == "persist"
    return request, intents, terminals, calls, terminal, persisted_raw[0]


def test_frozen_top_four_train_pool_rank_order_is_pinned():
    assert [row["rank_sha256"] for row in POOL_ROWS] == sorted(
        row["rank_sha256"] for row in POOL_ROWS
    )
    for row in POOL_ROWS:
        assert (
            rank_pool(
                "3d3ff36feb8a73b3130611d9a9c22d3121fdc4ed2bfc7e6df2d2503be49fd181", row["pool_id"]
            )
            == row["rank_sha256"]
        )
    candidates = [
        {"pool_id": row["pool_id"], "base_mint": row["base_mint"], "quote_mint": row["quote_mint"]}
        for row in reversed(POOL_ROWS)
    ]
    assert rank_and_select(
        "3d3ff36feb8a73b3130611d9a9c22d3121fdc4ed2bfc7e6df2d2503be49fd181", candidates
    ) == list(POOL_ROWS)


def test_population_selector_rejects_duplicate_pool_and_wrong_pin():
    duplicate = {"pool_id": "P" * 32, "base_mint": "B" * 32, "quote_mint": "Q" * 32}
    with pytest.raises(ValueError, match="duplicate"):
        rank_and_select("a" * 64, [duplicate, duplicate])
    with pytest.raises(ValueError, match="enrollment_pin"):
        from brain_senses_gecko_lane_policy_v1 import compile_pinned_policy

        compile_pinned_policy(
            enrollment_bytes=b"tampered",
            role_manifest_bytes=b"",
            plan_bytes=b"",
            selection_bytes=b"",
        )


def test_real_pinned_train_metadata_compiles_without_opening_capture_bodies():
    if not all(Path(path).is_file() for path in SOURCE_PATHS.values()):
        pytest.skip("pinned Kingston/train metadata not mounted")
    raw = {key: Path(path).read_bytes() for key, path in SOURCE_PATHS.items()}
    compiled = compile_pinned_policy(
        enrollment_bytes=raw["train_enrollment"],
        role_manifest_bytes=raw["train_role_manifest"],
        plan_bytes=raw["train_plan"],
        selection_bytes=raw["population_selection"],
    )
    verify_policy(compiled)
    assert compiled["pools"] == list(POOL_ROWS)
    assert compiled["source"]["protected_screen_fields"] == ["pool_id", "base_mint"]
    assert compiled["source"]["quote_token_features"] is False


def test_policy_hash_and_no_activation_are_mandatory():
    p = policy()
    verify_policy(p)
    p["activation_authorized"] = True
    with pytest.raises(ValueError, match="authority"):
        verify_policy(p)


def test_exact_query_and_dispatch_window():
    p = policy()
    close = 1_800_000_000 * NS
    req = request_for_bucket(p, POOL_ROWS[0]["pool_id"], close)
    assert req["path"] == f"/api/v2/networks/solana/pools/{POOL_ROWS[0]['pool_id']}/ohlcv/minute"
    assert req["query"] == {
        "aggregate": "5",
        "currency": "usd",
        "token": "base",
        "include_empty_intervals": "false",
        "limit": "6",
        "before_timestamp": "1800000000",
    }
    assert "api_key" not in req["url"] and req["headers"] == p["protocol"]["headers"]
    start, end = dispatch_window(close)
    assert classify_dispatch(close, start - 1) == "not_due"
    assert classify_dispatch(close, start) == "due"
    assert classify_dispatch(close, end - 1) == "due"
    assert classify_dispatch(close, end) == "missed_dispatch"
    schedule = bucket_schedule(p, close)
    assert len(schedule) == 4
    assert schedule[0]["pool_id"] == POOL_ROWS[0]["pool_id"]
    assert all(item["minimum_spacing_ns"] == 2 * NS for item in schedule)
    assert request_for_bucket(p, POOL_ROWS[0]["pool_id"], close)["max_response_bytes"] == 16 * 1024


def test_one_shot_reserves_before_fetch_and_verifies_closed_bars():
    p = policy()
    close_s = 1_800_000_000
    raw = raw_ohlcv(
        [
            [close_s - 300, 1, 2, 0.5, 1.5, 10],
            [close_s - 900, 1, 2, 0.5, 1.5, 11],
            [close_s - 1200, 1, 2, 0.5, 1.5, 12],
        ]
    )
    request, intents, terminals, calls, terminal, captured = execute(p, raw=raw)
    assert len(intents) == len(terminals) == len(calls) == 1
    assert terminal["status"] == STATUS_RECEIVED
    assert [row["disposition"] for row in terminal["bars"]] == [
        "eligible_new_open",
        "eligible_new_open",
        "eligible_overlap_revision",
    ]
    assert terminal["bars"][0]["event_time_ns"] < terminal["availability_ns"]
    assert captured == raw
    report = verify_journal(p, intents, terminals, {request["request_id"]: raw})
    assert report["status"] == "verified_prefix" and report["intents"] == 1
    assert report["activation_authorized"] is False


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (404, {}, STATUS_UNSUPPORTED),
        (410, {}, STATUS_UNSUPPORTED),
        (422, {}, STATUS_UNSUPPORTED),
        (429, {"Retry-After": "7200"}, STATUS_RATE_LIMITED),
        (503, {}, STATUS_PROVIDER_ERROR),
    ],
)
def test_terminal_http_classes_and_cooldowns(status, headers, expected):
    p = policy()
    request, intents, terminals, _calls, terminal, captured = execute(
        p, http_status=status, raw=b"provider body", headers=headers
    )
    assert terminal["status"] == expected
    if status == 429:
        assert terminal["cooldown_until_ns"] - terminal["receipt_ns"] == 7200 * NS
    elif status == 503:
        assert terminal["cooldown_until_ns"] - terminal["receipt_ns"] == 900 * NS
    assert captured == b"provider body"
    report = verify_journal(p, intents, terminals, {request["request_id"]: b"provider body"})
    assert report["terminal_count"] == 1


def test_transport_failure_is_spent_and_never_auto_retried():
    p = policy()
    request, intents, terminals, calls, terminal, captured = execute(
        p, fetch_error=TimeoutError("secret endpoint token")
    )
    assert (
        terminal["status"] == STATUS_TRANSPORT
        and terminal["transport_error_class"] == "TimeoutError"
    )
    assert "secret" not in json.dumps(terminal)
    assert captured is None
    report = verify_journal(p, intents, terminals, {})
    assert report["intents"] == 1 and report["raw_bytes"] == 0
    assert calls


def test_verifier_rejects_dispatch_during_prior_429_cooldown():
    p = policy()
    intents = []
    terminals = []
    raw_by_id = {}

    def send(close_ns, status, previous=None, headers=None):
        request = request_for_bucket(p, POOL_ROWS[0]["pool_id"], close_ns)
        decision = dispatch_window(close_ns)[0]
        body = b"limited" if status == 429 else b"unsupported"
        samples = iter([decision, decision + 100])

        def reserve(intent, _reserve, _deadline):
            intents.append(intent)
            return ReservationResult("reserved", decision)

        def persist(terminal, _body):
            terminals.append(terminal)

        terminal = execute_one_shot(
            p,
            request,
            decision_ns=decision,
            previous_dispatch_ns=previous,
            clock_ns=lambda: next(samples),
            validate_dispatch_state=lambda _request, _now: True,
            reserve_intent=reserve,
            fetch_once=lambda _request, _limit: FetchResult(status, body, headers or {}),
            persist_terminal=persist,
            persist_skip=lambda _skip: pytest.fail("not a skip"),
        )
        raw_by_id[request["request_id"]] = body
        return terminal

    close = 1_800_000_000 * NS
    first = send(close, 429)
    send(close + 900 * NS, 404, first["request_started_ns"])
    with pytest.raises(ValueError, match="cooldown"):
        verify_journal(p, intents, terminals, raw_by_id)


def test_expired_after_durable_intent_does_not_call_fetch():
    p = policy()
    close = 1_800_000_000 * NS
    request = request_for_bucket(p, POOL_ROWS[0]["pool_id"], close)
    start, end = dispatch_window(close)
    intents = []
    terminals = []
    calls = []
    samples = iter([end])
    terminal = execute_one_shot(
        p,
        request,
        decision_ns=start,
        previous_dispatch_ns=None,
        clock_ns=lambda: next(samples),
        validate_dispatch_state=lambda _request, _decision: True,
        reserve_intent=lambda intent, _reserve, _deadline: (
            intents.append(intent) or ReservationResult("reserved", intent["decision_ns"])
        ),
        fetch_once=lambda *_args: calls.append(True),
        persist_terminal=lambda terminal, _body: terminals.append(terminal),
        persist_skip=lambda *_skip: pytest.fail("unexpected skip"),
    )
    assert terminal["status"] == STATUS_NOT_DISPATCHED and not calls
    assert verify_journal(p, intents, terminals, {})["unresolved_spent_intents"] == 0


def test_reservation_boundary_crossing_deadline_records_missed_without_intent_or_fetch():
    p = policy()
    close = 1_800_000_000 * NS
    request = request_for_bucket(p, POOL_ROWS[0]["pool_id"], close)
    start, end = dispatch_window(close)
    skips = []
    calls = []
    result = execute_one_shot(
        p,
        request,
        decision_ns=start,
        previous_dispatch_ns=None,
        clock_ns=lambda: pytest.fail("reservation callback declined before dispatch"),
        validate_dispatch_state=lambda _request, _decision: True,
        reserve_intent=lambda _intent, _reserve, deadline: ReservationResult("late", deadline),
        fetch_once=lambda *_: calls.append(True),
        persist_terminal=lambda *_: pytest.fail("no durable intent exists"),
        persist_skip=skips.append,
    )
    assert result["status"] == "missed_dispatch"
    assert skips[0]["at_ns"] == end and not calls


def test_dispatch_state_rechecked_after_intent_before_provider_call():
    p = policy()
    close = 1_800_000_000 * NS
    request = request_for_bucket(p, POOL_ROWS[0]["pool_id"], close)
    start, _ = dispatch_window(close)
    gate_results = iter([True, False])
    intents = []
    terminals = []
    calls = []
    result = execute_one_shot(
        p,
        request,
        decision_ns=start,
        previous_dispatch_ns=None,
        clock_ns=lambda: start,
        validate_dispatch_state=lambda _request, _now: next(gate_results),
        reserve_intent=lambda intent, _reserve, _deadline: (
            intents.append(intent) or ReservationResult("reserved", start)
        ),
        fetch_once=lambda *_: calls.append(True),
        persist_terminal=lambda terminal, _raw: terminals.append(terminal),
        persist_skip=lambda *_: pytest.fail("intent was already reserved"),
    )
    assert result["status"] == "intent_not_dispatched_state_changed"
    assert len(intents) == len(terminals) == 1 and not calls
    assert verify_journal(p, intents, terminals, {})["terminal_count"] == 1


def test_spacing_and_intent_hash_tampering_fail_closed():
    p = policy()
    close = 1_800_000_000 * NS
    request = request_for_bucket(p, POOL_ROWS[0]["pool_id"], close)
    start, _ = dispatch_window(close)
    with pytest.raises(ValueError, match="spacing"):
        execute_one_shot(
            p,
            request,
            decision_ns=start,
            previous_dispatch_ns=start - NS,
            clock_ns=lambda: start,
            validate_dispatch_state=lambda *_: True,
            reserve_intent=lambda *_: pytest.fail("must reject before intent"),
            fetch_once=lambda *_: pytest.fail("must not call provider"),
            persist_terminal=lambda *_: None,
            persist_skip=lambda *_: None,
        )
    request, intents, terminals, _calls, _terminal, _captured = execute(p, raw=b"not json")
    tampered = dict(intents[0], decision_ns=intents[0]["decision_ns"] + 1)
    with pytest.raises(ValueError, match="intent_hash"):
        verify_journal(p, [tampered], terminals, {request["request_id"]: b"not json"})


def test_parser_rejects_duplicate_and_bad_rows_but_keeps_out_of_window_rows_typed():
    p = policy()
    close_s = 1_800_000_000
    duplicate = b'{"data":{"attributes":{"ohlcv_list":[]}},"data":{}}'
    _, _, _, _, malformed, _ = execute(p, raw=duplicate)
    assert malformed["status"] == "malformed_response"
    # Payload remains parseable; outside scope is retained as an explicit excluded row.
    raw = raw_ohlcv([[close_s - 2100, 1, 2, 0.5, 1.5, 1]])
    _, _, _, _, terminal, _ = execute(p, raw=raw)
    assert terminal["bars"][0]["disposition"] == "excluded_out_of_window"
    huge = raw_ohlcv([[close_s - 300, 10**400, 1, 1, 1, 1]])
    _, intents, terminals, _, huge_terminal, captured = execute(p, raw=huge)
    assert huge_terminal["status"] == "malformed_response" and captured == huge
    assert (
        verify_journal(p, intents, terminals, {intents[0]["request_id"]: huge})["terminal_count"]
        == 1
    )


def test_oversize_response_is_not_retained_and_blocks_future_intents():
    p = policy()
    request, intents, terminals, _calls, terminal, capture = execute(p, raw=b"x" * (16 * 1024 + 1))
    assert terminal["status"] == "response_over_budget" and capture is None
    assert terminal["response_bytes"] == 16 * 1024 + 1
    report = verify_journal(p, intents, terminals, {})
    assert report["raw_bytes"] == 0 and report["observed_response_bytes"] == 16 * 1024 + 1


def test_cooldown_limits_and_schedule_denominator():
    receipt = 100 * NS
    assert (
        cooldown_until_ns(STATUS_RATE_LIMITED, receipt, 25 * 3600)[1]
        == "review_required_rate_limit"
    )
    assert cooldown_until_ns(STATUS_PROVIDER_ERROR, receipt, None) == (
        receipt + 900 * NS,
        "provider_or_transport_cooldown",
    )
    p = policy()
    request, intents, terminals, _calls, _terminal, _captured = execute(
        p, http_status=404, raw=b"missing"
    )
    # A single observed request cannot certify a full four-pool bucket.
    with pytest.raises(ValueError, match="schedule_denominator"):
        verify_journal(
            p,
            intents,
            terminals,
            {request["request_id"]: b"missing"},
            scheduled_closes_ns=[request["bucket_close_ns"]],
        )
