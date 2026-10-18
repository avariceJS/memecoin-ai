from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from brain_senses_gecko_lane_engine_v3 import make_dispatch_record, parse_response
from brain_senses_gecko_lane_engine_v4 import (
    INTENT_SCHEMA,
    MINIMUM_DISPATCH_SPACING_NS,
    FetchResult,
    ReservationResult,
    classify_dispatch_v4,
    classify_durable_result_v4,
    dispatch_window_v4,
    execute_one_shot,
    latest_request_start_ns,
    make_intent_v4,
    request_for_bucket_v4,
)
from brain_senses_gecko_lane_policy_v3 import (
    POOL_ROWS,
    canonical,
    sha256,
)
from brain_senses_gecko_lane_policy_v4 import compile_policy_v4, verify_policy_v4
from brain_senses_gecko_lane_verify_v5 import verify_journal_v5

NS = 1_000_000_000
CLOSE = 1_800_000_000 * NS


def policy() -> dict[str, Any]:
    # Build the base v3 policy from its public fixed contract; v4 only changes schedule.
    from brain_senses_gecko_lane_policy_v3 import (
        TRAIN_ENROLLMENT_SHA256,
        TRAIN_MANIFEST_SHA256,
        TRAIN_PLAN_SHA256,
        TRAIN_SELECTION_SHA256,
        TRAIN_SOURCE_PIN,
        capacity_contract,
        protocol_constants,
    )

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
    return compile_policy_v4(value)


def empty_body() -> bytes:
    return json.dumps({"data": {"attributes": {"ohlcv_list": []}}}).encode()


def _fixture(*, late: bool = False, eligible: bool = False):
    p = policy()
    requests, intents, terminals, bodies, markers = [], [], [], {}, []
    for ordinal, pool in enumerate(p["pools"]):
        request = request_for_bucket_v4(p, pool["pool_id"], CLOSE)
        dispatch = CLOSE + 70 * NS + ordinal * 25 * NS
        intent = make_intent_v4(p, request, dispatch)
        marker = make_dispatch_record(
            sequence=ordinal,
            request_id=request["request_id"],
            dispatch_ns=dispatch,
            previous_sha256=None if not markers else markers[-1]["record_sha256"],
        )
        body = (
            json.dumps(
                {"data": {"attributes": {"ohlcv_list": [[CLOSE // NS - 300, 10, 12, 9, 11, 5]]}}}
            ).encode()
            if eligible and ordinal == 0
            else empty_body()
        )
        receipt = dispatch + NS
        availability = CLOSE + 240 * NS if late and ordinal == 0 else receipt + NS
        parsed_bars = parse_response(body, CLOSE, receipt)
        persisted_bars = []
        for bar in parsed_bars:
            projected = dict(bar)
            projected["provider_receipt_ns"] = projected.pop("source_available_ns")
            projected["provider_receipt_semantics"] = (
                "transport_receipt_only_not_decision_eligibility"
            )
            persisted_bars.append(projected)
        terminal = {
            "schema": "brain-senses-gecko-terminal-v4",
            "intent_sha256": intent["intent_sha256"],
            "request_id": intent["request_id"],
            "dispatch_ns": dispatch,
            "dispatch_record_sha256": marker["record_sha256"],
            "request_started_ns": dispatch,
            "receipt_ns": receipt,
            "availability_ns": availability,
            "http_status": 200,
            "status": "received" if parsed_bars else "empty_response",
            "response_sha256": sha256(body),
            "response_bytes": len(body),
            "bars": persisted_bars,
            "timeliness": (
                "late_unknown_spent_no_retry" if availability >= CLOSE + 240 * NS else "available"
            ),
            "provider_charge_units": None,
            "provider_charge_status": "unknown_unverified",
        }
        requests.append(request)
        intents.append(intent)
        terminals.append(terminal)
        bodies[request["request_id"]] = body
        markers.append(marker)
    return p, intents, terminals, bodies, markers


def confirmations_for(terminals):
    rows = []
    previous = None
    for index, terminal in enumerate(terminals):
        row = {
            "schema": "brain-senses-gecko-result-confirmation-v3",
            "sequence": index,
            "request_id": terminal["request_id"],
            "outcome_sha256": sha256(canonical(terminal)),
            "confirmed_ns": terminal["availability_ns"] + NS,
            "previous_sha256": previous,
        }
        row["record_sha256"] = sha256(canonical(row))
        rows.append(row)
        previous = row["record_sha256"]
    return rows


def test_v4_window_and_strict_request_start_boundaries():
    start, end = dispatch_window_v4(CLOSE)
    assert start == CLOSE + 60 * NS
    assert end == CLOSE + 240 * NS
    assert classify_dispatch_v4(CLOSE, start - 1) == "not_due"
    assert classify_dispatch_v4(CLOSE, start) == "due"
    assert classify_dispatch_v4(CLOSE, CLOSE + 215 * NS) == "request_start_deadline_passed"
    assert classify_dispatch_v4(CLOSE, end) == "missed_dispatch"
    assert latest_request_start_ns(CLOSE) == CLOSE + 215 * NS


def test_v4_durable_availability_boundary_is_strict():
    request = request_for_bucket_v4(policy(), POOL_ROWS[0]["pool_id"], CLOSE)
    started = CLOSE + 214 * NS
    assert (
        classify_durable_result_v4(
            request, request_started_ns=started, availability_ns=CLOSE + 240 * NS - 1
        )
        == "available"
    )
    assert (
        classify_durable_result_v4(
            request, request_started_ns=started, availability_ns=CLOSE + 240 * NS
        )
        == "late_unknown_spent_no_retry"
    )
    with pytest.raises(ValueError, match="request_start_window"):
        classify_durable_result_v4(
            request, request_started_ns=CLOSE + 215 * NS, availability_ns=CLOSE + 216 * NS
        )


def test_v4_intent_shape_is_v4_but_preserves_frozen_request_semantics():
    p = policy()
    verify_policy_v4(p)
    request = request_for_bucket_v4(p, POOL_ROWS[0]["pool_id"], CLOSE)
    intent = make_intent_v4(p, request, CLOSE + 60 * NS)
    assert intent["schema"] == INTENT_SCHEMA
    assert set(intent) == {
        "schema",
        "request_id",
        "request",
        "decision_ns",
        "provider_charge_units",
        "provider_charge_status",
        "intent_sha256",
    }
    assert request["query"] == {
        "aggregate": "5",
        "currency": "usd",
        "token": "base",
        "include_empty_intervals": "false",
        "limit": "6",
        "before_timestamp": str(CLOSE // NS),
    }


def test_independent_v5_verifier_accepts_four_frozen_pool_intents():
    p, intents, terminals, bodies, markers = _fixture()
    result = verify_journal_v5(
        p, intents, terminals, bodies, markers, confirmations=confirmations_for(terminals)
    )
    assert result["schedule_version"] == 4
    assert result["timely_available"] == 4
    assert result["late_unknown_spent_no_retry"] == 0
    assert result["activation_authorized"] is False


def test_full_timely_four_pool_prefix_qualifies_only_with_new_open():
    p, intents, terminals, bodies, markers = _fixture(eligible=True)
    result = verify_journal_v5(
        p, intents, terminals, bodies, markers, confirmations=confirmations_for(terminals)
    )
    assert result["eligible_new_open"] == 1
    assert result["release_qualification_allowed"] is True


def test_scheduled_pool_without_an_intent_is_unknown_and_blocks_release():
    p, intents, terminals, bodies, markers = _fixture(eligible=True)
    intents = intents[:3]
    terminals = terminals[:3]
    markers = markers[:3]
    bodies = {row["request_id"]: bodies[row["request_id"]] for row in intents}
    result = verify_journal_v5(
        p,
        intents,
        terminals,
        bodies,
        markers,
        confirmations=confirmations_for(terminals),
        scheduled_closes_ns=[CLOSE],
    )
    assert result["missing_pool_buckets_unknown"] == [
        {"pool_id": p["pools"][3]["pool_id"], "bucket_close_ns": CLOSE}
    ]
    assert result["release_qualification_allowed"] is False


def test_late_durable_response_is_persisted_but_unknown_and_never_retried():
    p, intents, terminals, bodies, markers = _fixture(late=True)
    result = verify_journal_v5(
        p,
        intents[:1],
        terminals[:1],
        {intents[0]["request_id"]: bodies[intents[0]["request_id"]]},
        markers[:1],
    )
    assert result["late_unknown_spent_no_retry"] == 1
    terminals[0]["timeliness"] = "available"
    with pytest.raises(ValueError, match="late_result_claim"):
        verify_journal_v5(
            p,
            intents[:1],
            terminals[:1],
            {intents[0]["request_id"]: bodies[intents[0]["request_id"]]},
            markers[:1],
        )


def test_verifier_rejects_request_start_at_exclusive_latest_boundary():
    p, intents, terminals, bodies, markers = _fixture()
    intents[0]["decision_ns"] = CLOSE + 215 * NS
    core = dict(intents[0])
    core.pop("intent_sha256")
    intents[0]["intent_sha256"] = sha256(canonical(core))
    terminals[0]["request_started_ns"] = CLOSE + 215 * NS
    with pytest.raises(ValueError, match="intent_window"):
        verify_journal_v5(p, intents, terminals, bodies, markers)


def test_verifier_rejects_mutated_request_or_spacing():
    p, intents, terminals, bodies, markers = _fixture()
    intents[0]["request"]["query"]["token"] = "quote"
    core = dict(intents[0])
    core.pop("intent_sha256")
    intents[0]["intent_sha256"] = sha256(canonical(core))
    terminals[0]["intent_sha256"] = intents[0]["intent_sha256"]
    with pytest.raises(ValueError, match="request_mismatch"):
        verify_journal_v5(p, intents, terminals, bodies, markers)

    p, intents, terminals, bodies, markers = _fixture()
    markers[1]["dispatch_ns"] = markers[0]["dispatch_ns"] + 1
    core = dict(markers[1])
    core.pop("record_sha256")
    markers[1]["record_sha256"] = sha256(canonical(core))
    terminals[1]["dispatch_ns"] = markers[1]["dispatch_ns"]
    terminals[1]["dispatch_record_sha256"] = markers[1]["record_sha256"]
    with pytest.raises(ValueError, match="dispatch_chain"):
        verify_journal_v5(p, intents, terminals, bodies, markers)


def test_clocked_intent_is_bound_to_bucket_and_progress_record():
    p = policy()
    close_ns = CLOSE
    decision_ns = CLOSE + 61 * NS
    date_ns = (decision_ns // NS - 1) * NS
    date = dt.datetime.fromtimestamp(date_ns / NS, dt.UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    samples = []
    for offset, host in enumerate(("www.cloudflare.com", "www.google.com")):
        sample_offset_ns = offset * 100_000_000
        samples.append(
            {
                "host": host,
                "status": 200,
                "date": date,
                "age": None,
                "sent_ns": date_ns + sample_offset_ns,
                "received_ns": date_ns + sample_offset_ns + 100_000_000,
                "monotonic_sent_ns": 90_000_000_000 + sample_offset_ns,
                "monotonic_received_ns": 90_000_000_000 + sample_offset_ns + 100_000_000,
            }
        )
    clock = {
        "schema": "brain-senses-external-v1-clock",
        "schema_version": 1,
        "samples": samples,
        "checked_ns": date_ns + 200_000_000,
        "monotonic_ns": 90_200_000_000,
    }
    clock_raw = canonical(clock)
    root = "services/gecko-market-v1/v4-pilots/" + "a" * 32
    clock_path = f"{root}/clocks/{close_ns}-00.json"
    intent = make_intent_v4(
        p, request_for_bucket_v4(p, POOL_ROWS[0]["pool_id"], close_ns), decision_ns
    )
    intent.update(
        {
            "clock_path": clock_path,
            "clock_sha256": sha256(clock_raw),
            "decision_monotonic_ns": 90_400_000_000,
        }
    )
    core = dict(intent)
    core.pop("intent_sha256")
    intent["intent_sha256"] = sha256(canonical(core))
    verify_journal_v5(
        p,
        [intent],
        [],
        {},
        root=root,
        clock_files={clock_path: clock_raw},
    )
    mutated = dict(intent)
    mutated["decision_monotonic_ns"] += 10 * NS
    core = dict(mutated)
    core.pop("intent_sha256")
    mutated["intent_sha256"] = sha256(canonical(core))
    with pytest.raises(ValueError, match="clock_progress"):
        verify_journal_v5(
            p,
            [mutated],
            [],
            {},
            root=root,
            clock_files={clock_path: clock_raw},
        )


def test_engine_one_shot_persists_timely_result_with_v4_window():
    p = policy()
    request = request_for_bucket_v4(p, POOL_ROWS[0]["pool_id"], CLOSE)
    events: list[dict[str, Any]] = []
    terminals: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    clock_values = iter([CLOSE + 61 * NS, CLOSE + 62 * NS, CLOSE + 63 * NS])

    def reserve(intent, reserve_bytes, deadline):
        assert reserve_bytes == p["protocol"]["physical_reserve_per_request_bytes"]
        assert deadline == CLOSE + 240 * NS
        events.append(intent)
        return ReservationResult("reserved", CLOSE + 60 * NS)

    def persist(terminal, body):
        if body is not None:
            terminal["availability_ns"] = CLOSE + 64 * NS
            terminal["timeliness"] = "available"
        terminals.append(dict(terminal))

    class Lock:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    result = execute_one_shot(
        p,
        request,
        decision_ns=CLOSE + 60 * NS,
        clock_ns=lambda: next(clock_values),
        lane_lock=Lock,
        read_dispatch_state=lambda: None,
        append_dispatch_record=records.append,
        validate_dispatch_state=lambda _request, _at: True,
        reserve_intent=reserve,
        fetch_once=lambda _request, _limit, _timeout: FetchResult(200, empty_body(), {}),
        persist_terminal=persist,
        persist_skip=lambda _skip: pytest.fail("unexpected skip"),
    )
    assert result["status"] == "empty_response"
    assert result["provider_io"] is True
    assert events[0]["schema"] == INTENT_SCHEMA
    assert result["timeliness"] == "available"
    assert len(records) == 1
    assert terminals[0]["availability_ns"] == CLOSE + 64 * NS
    assert MINIMUM_DISPATCH_SPACING_NS == 12 * NS
