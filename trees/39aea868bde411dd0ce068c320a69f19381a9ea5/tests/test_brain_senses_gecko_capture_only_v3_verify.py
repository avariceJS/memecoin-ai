from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO))

import test_brain_senses_gecko_capture_only_v2_verify as old_verify_fixture  # noqa: E402
import test_brain_senses_gecko_scout_chain_v2 as chain_fixture  # noqa: E402

from tools import brain_senses_capacity_coordinator_v2 as capacity  # noqa: E402
from tools import brain_senses_gecko_capture_only_v3_verify as verify_v3  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools import brain_senses_gecko_scout_chain_v2 as chain_v2  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402


@pytest.fixture
def writer(tmp_path: Path):
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(fd)
    try:
        yield writer
    finally:
        writer.close()
        os.close(fd)


def _verified_plan(writer: DescriptorWriter) -> tuple[Any, Any, Any, list[dict[str, Any]]]:
    inputs = chain_fixture._certificate_inputs(writer)
    policy = chain_fixture._policy()
    certificate = chain_v2.build_cutover_certificate(**inputs)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer,
        root=chain_fixture.ROOT,
        policy=policy,
        certificate=certificate,
        certificate_inputs=inputs,
    )
    journal.initialize()
    first_close = chain_fixture.handoff.UNPLANNED_GAP_CLOSE_NS
    plan_close = first_close + scout.BUCKET_NS
    journal.ensure_plan_for_current_bucket(
        now_ns=plan_close + scout.DISPATCH_EARLIEST_NS,
        bucket_close_ns=plan_close,
    )
    return policy, certificate, inputs, journal.records()


def _coordinator_genesis() -> list[dict[str, Any]]:
    return [
        capacity._event(
            seq=0,
            kind="genesis",
            at_ns=1,
            previous=None,
            fields={"policy_sha256": "a" * 64},
        )
    ]


def _verify(
    policy: dict[str, Any],
    certificate: dict[str, Any],
    inputs: dict[str, Any],
    records: list[dict[str, Any]],
    binding: dict[str, Any],
    observed_now_ns: int | None = None,
) -> dict[str, Any]:
    return verify_v3.verify_capture_prefix_v3(
        root=chain_fixture.ROOT,
        binding=binding,
        policy=policy,
        chain_records=records,
        intents=[],
        dispatch_records=[],
        terminals=[],
        raw_responses={},
        confirmations=[],
        coordinator_events=_coordinator_genesis(),
        skips=[],
        certificate=certificate,
        certificate_inputs=inputs,
        observed_now_ns=observed_now_ns,
    )


def test_v2_capture_prefix_verifier_replays_cursor_twelve_plan_only_root(
    writer: DescriptorWriter,
) -> None:
    policy, certificate, inputs, records = _verified_plan(writer)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer,
        root=chain_fixture.ROOT,
        policy=policy,
        certificate=certificate,
        certificate_inputs=inputs,
    )
    result = _verify(policy, certificate, inputs, records, journal.binding)
    assert result["status"] == "verified_prefix"
    assert result["intent_count"] == 0
    assert result["unresolved_count"] == 0
    assert result["usage_attempts"] == []
    assert result["activation_authorized"] is False
    assert result["market_source_qualified"] is False
    assert len(result["unaccounted_slots"]) == 6
    assert result["expired_unaccounted_slots"] == []


def test_expired_unaccounted_slots_are_reported_as_dispatch_blocking(
    writer: DescriptorWriter,
) -> None:
    policy, certificate, inputs, records = _verified_plan(writer)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer,
        root=chain_fixture.ROOT,
        policy=policy,
        certificate=certificate,
        certificate_inputs=inputs,
    )
    close = records[-1]["bucket_close_ns"]
    replay = _verify(
        policy,
        certificate,
        inputs,
        records,
        journal.binding,
        observed_now_ns=close + verify_v3.AVAILABILITY_END_NS,
    )
    assert len(replay["expired_unaccounted_slots"]) == 6
    assert replay["dispatch_blocked"] is True
    assert replay["dispatch_block_reason"] == "expired_unaccounted_slots"


def test_later_plan_cannot_bypass_unaccounted_prior_slots(writer: DescriptorWriter) -> None:
    policy, certificate, inputs, records = _verified_plan(writer)
    next_close = records[-1]["bucket_close_ns"] + scout.BUCKET_NS
    next_plan = chain_v2.append_plan(policy, records[-1], bucket_close_ns=next_close)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer,
        root=chain_fixture.ROOT,
        policy=policy,
        certificate=certificate,
        certificate_inputs=inputs,
    )
    with pytest.raises(verify_v3.VerifyError, match="v2_previous_plan_slots_unaccounted"):
        _verify(
            policy,
            certificate,
            inputs,
            [*records, next_plan],
            journal.binding,
            observed_now_ns=next_close + verify_v3.WINDOW_START_NS,
        )


def test_v2_capture_prefix_rejects_v1_binding_shape(writer: DescriptorWriter) -> None:
    policy, certificate, inputs, records = _verified_plan(writer)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer,
        root=chain_fixture.ROOT,
        policy=policy,
        certificate=certificate,
        certificate_inputs=inputs,
    )
    old_binding_shape = dict(journal.binding)
    old_binding_shape["schema_version"] = 1
    old_binding_shape.pop("dispatch_authorized")
    old_binding_shape["activation_authorized"] = False
    core = dict(old_binding_shape)
    core.pop("binding_sha256")
    old_binding_shape["binding_sha256"] = verify_v3.sha256(verify_v3.canonical_json(core))
    with pytest.raises(verify_v3.VerifyError, match="v2_binding_fields"):
        _verify(policy, certificate, inputs, records, old_binding_shape)


def test_v2_capture_prefix_rejects_tampered_cutover_certificate(writer: DescriptorWriter) -> None:
    policy, certificate, inputs, records = _verified_plan(writer)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer,
        root=chain_fixture.ROOT,
        policy=policy,
        certificate=certificate,
        certificate_inputs=inputs,
    )
    bad_inputs = dict(inputs)
    bad_inputs["predecessor_last_plan_sha256"] = "f" * 64
    with pytest.raises(verify_v3.VerifyError, match="v2_plan_chain_invalid"):
        _verify(policy, certificate, bad_inputs, records, journal.binding)


def _one_successful_attempt(
    writer: DescriptorWriter,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    bytes,
    dict[str, Any],
]:
    policy, certificate, inputs, records = _verified_plan(writer)
    journal = chain_v2.DescriptorScoutJournalV2(
        writer,
        root=chain_fixture.ROOT,
        policy=policy,
        certificate=certificate,
        certificate_inputs=inputs,
    )
    plan = records[-1]
    close = plan["bucket_close_ns"]
    request = verify_v3.expected_request(policy, journal.binding["binding_sha256"], plan, 0)
    decision_ns = close + verify_v3.WINDOW_START_NS
    decision_mono = 100 * scout.NS
    intent_clock, _ = old_verify_fixture._clock(decision_ns, decision_mono)
    intent = verify_v3.build_intent(
        root=chain_fixture.ROOT,
        policy=policy,
        binding=journal.binding,
        plan_record=plan,
        slot_index=0,
        decision_ns=decision_ns,
        decision_monotonic_ns=decision_mono,
        clock_record=intent_clock,
        admission=old_verify_fixture._admission(request["request_id"], decision_ns),
    )
    dispatch_ns = decision_ns + scout.NS
    dispatch_mono = decision_mono + scout.NS
    dispatch = {
        "schema": verify_v3.DISPATCH_SCHEMA,
        "sequence": 0,
        "request_id": intent["request_id"],
        "intent_sha256": intent["intent_sha256"],
        "dispatch_ns": dispatch_ns,
        "dispatch_monotonic_ns": dispatch_mono,
        "previous_sha256": None,
    }
    dispatch["record_sha256"] = verify_v3.sha256(verify_v3.canonical_json(dispatch))
    started_ns = dispatch_ns + scout.NS
    started_mono = dispatch_mono + scout.NS
    receipt_ns = started_ns + scout.NS
    receipt_mono = started_mono + scout.NS
    body = b'{"data":{"attributes":{"ohlcv_list":[]}}}'
    terminal = {
        "schema": verify_v3.TERMINAL_SCHEMA,
        "schema_version": 2,
        "request_id": intent["request_id"],
        "intent_sha256": intent["intent_sha256"],
        "plan_sha256": request["plan_record_sha256"],
        "dispatch_record_sha256": dispatch["record_sha256"],
        "dispatch_ns": dispatch_ns,
        "dispatch_monotonic_ns": dispatch_mono,
        "request_started_ns": started_ns,
        "request_started_monotonic_ns": started_mono,
        "receipt_ns": receipt_ns,
        "receipt_monotonic_ns": receipt_mono,
        "deadline_monotonic_ns": started_mono + verify_v3.HARD_REQUEST_DEADLINE_NS,
        "status": "empty_response",
        "http_status": 200,
        "transport_error_class": None,
        "retry_after_seconds": None,
        "retry_after_header_count": 0,
        "retry_after_evidence": [],
        "retry_after_class": "absent",
        "cooldown_until_ns": None,
        "stop_reason": None,
        "provider_io": True,
        "raw_body_b64": base64.b64encode(body).decode("ascii"),
        "response_bytes": len(body),
        "response_sha256": verify_v3.sha256(body),
        "bars": [],
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }
    genesis = capacity._event(
        seq=0,
        kind="genesis",
        at_ns=1,
        previous=None,
        fields={"policy_sha256": "a" * 64},
    )
    intent_bytes = verify_v3.canonical_json(intent)
    reservation_fields = {
        "reservation_id": f"reservation-{intent['request_id']}",
        "service": "gecko",
        "request_id": intent["request_id"],
        "intent_path": f"{chain_fixture.ROOT}/intents/{intent['request_id']}.json",
        "outcome_path": f"{chain_fixture.ROOT}/results/{intent['request_id']}.json",
        "intent_sha256": verify_v3.sha256(intent_bytes),
        "reserved_bytes": scout.MAX_RESPONSE_BYTES,
        "intent_bytes_sha256": verify_v3.sha256(intent_bytes),
        "allocated_before_bytes": 0,
    }
    reserved = capacity._event(
        seq=1,
        kind="reserved",
        at_ns=decision_ns,
        previous=genesis["event_sha256"],
        fields=reservation_fields,
    )
    settled = capacity._event(
        seq=2,
        kind="settled",
        at_ns=receipt_ns,
        previous=reserved["event_sha256"],
        fields={
            "reservation_id": reservation_fields["reservation_id"],
            "outcome": "received",
            "outcome_sha256": verify_v3.sha256(verify_v3.canonical_json(terminal)),
            "outcome_path": reservation_fields["outcome_path"],
            "actual_allocated_bytes": len(body),
        },
    )
    availability_ns = receipt_ns + scout.NS
    availability_mono = receipt_mono + scout.NS
    availability_clock, _ = old_verify_fixture._clock(availability_ns, availability_mono)
    confirmation = {
        "schema": verify_v3.CONFIRMATION_SCHEMA,
        "schema_version": 1,
        "request_id": intent["request_id"],
        "outcome_sha256": verify_v3.sha256(verify_v3.canonical_json(terminal)),
        "availability_ns": availability_ns,
        "availability_monotonic_ns": availability_mono,
        "availability_clock": availability_clock,
        "availability_clock_sha256": verify_v3.sha256(verify_v3.canonical_json(availability_clock)),
        "coordinator_event_sha256": settled["event_sha256"],
    }
    confirmation["confirmation_sha256"] = verify_v3.sha256(verify_v3.canonical_json(confirmation))
    return (
        policy,
        certificate,
        inputs,
        records,
        [intent],
        body,
        {
            "binding": journal.binding,
            "dispatch_records": [dispatch],
            "terminals": [terminal],
            "confirmations": [confirmation],
            "coordinator_events": [genesis, reserved, settled],
        },
    )


def test_successful_response_requires_v2_deadline_and_availability_contract(
    writer: DescriptorWriter,
) -> None:
    policy, certificate, inputs, records, intents, body, extras = _one_successful_attempt(writer)
    result = verify_v3.verify_capture_prefix_v3(
        root=chain_fixture.ROOT,
        binding=extras["binding"],
        policy=policy,
        chain_records=records,
        intents=intents,
        dispatch_records=extras["dispatch_records"],
        terminals=extras["terminals"],
        raw_responses={intents[0]["request_id"]: body},
        confirmations=extras["confirmations"],
        coordinator_events=extras["coordinator_events"],
        certificate=certificate,
        certificate_inputs=inputs,
    )
    assert result["confirmation_count"] == 1
    assert result["usage_attempts"][0]["response_bytes"] == len(body)

    late_terminal = dict(extras["terminals"][0])
    late_terminal["request_started_ns"] = (
        records[-1]["bucket_close_ns"] + verify_v3.REQUEST_START_END_NS
    )
    with pytest.raises(verify_v3.VerifyError, match="v2_terminal_times"):
        verify_v3.verify_capture_prefix_v3(
            root=chain_fixture.ROOT,
            binding=extras["binding"],
            policy=policy,
            chain_records=records,
            intents=intents,
            dispatch_records=extras["dispatch_records"],
            terminals=[late_terminal],
            raw_responses={intents[0]["request_id"]: body},
            confirmations=extras["confirmations"],
            coordinator_events=extras["coordinator_events"],
            certificate=certificate,
            certificate_inputs=inputs,
        )

    over_deadline = dict(extras["terminals"][0])
    over_deadline["receipt_ns"] = over_deadline["request_started_ns"] + 31 * scout.NS
    over_deadline["receipt_monotonic_ns"] = (
        over_deadline["request_started_monotonic_ns"] + 31 * scout.NS
    )
    with pytest.raises(verify_v3.VerifyError, match="v2_terminal_times"):
        verify_v3.verify_capture_prefix_v3(
            root=chain_fixture.ROOT,
            binding=extras["binding"],
            policy=policy,
            chain_records=records,
            intents=intents,
            dispatch_records=extras["dispatch_records"],
            terminals=[over_deadline],
            raw_responses={intents[0]["request_id"]: body},
            confirmations=extras["confirmations"],
            coordinator_events=extras["coordinator_events"],
            certificate=certificate,
            certificate_inputs=inputs,
        )

    late_confirmation = dict(extras["confirmations"][0])
    late_confirmation["availability_ns"] = (
        records[-1]["bucket_close_ns"] + verify_v3.AVAILABILITY_END_NS
    )
    late_confirmation["availability_monotonic_ns"] = (
        extras["confirmations"][0]["availability_monotonic_ns"]
        + late_confirmation["availability_ns"]
        - extras["confirmations"][0]["availability_ns"]
    )
    late_confirmation.pop("confirmation_sha256")
    late_confirmation["confirmation_sha256"] = verify_v3.sha256(
        verify_v3.canonical_json(late_confirmation)
    )
    with pytest.raises(verify_v3.VerifyError, match="v2_confirmation_binding"):
        verify_v3.verify_capture_prefix_v3(
            root=chain_fixture.ROOT,
            binding=extras["binding"],
            policy=policy,
            chain_records=records,
            intents=intents,
            dispatch_records=extras["dispatch_records"],
            terminals=extras["terminals"],
            raw_responses={intents[0]["request_id"]: body},
            confirmations=[late_confirmation],
            coordinator_events=extras["coordinator_events"],
            certificate=certificate,
            certificate_inputs=inputs,
        )
