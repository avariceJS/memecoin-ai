from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast

import pytest

from tools import brain_senses_external_orchestrator_v1 as engine
from tools import brain_senses_external_schema_v1 as schema
from tools import brain_senses_external_successor_v2 as successor
from tools import brain_senses_external_verify_v1 as verifier

_FIXTURE_PATH = Path(__file__).with_name("test_brain_senses_external_verify_v1.py")
_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "external_verify_fixture_module", _FIXTURE_PATH
)
assert _FIXTURE_SPEC is not None and _FIXTURE_SPEC.loader is not None
_FIXTURES = importlib.util.module_from_spec(_FIXTURE_SPEC)
_FIXTURE_SPEC.loader.exec_module(_FIXTURES)


def _reader(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
    monkeypatch.setattr(engine, "source_bindings", lambda: {"source.py": "a" * 64})
    return cast(tuple[Any, dict[str, Any]], _FIXTURES.fixture())


def _compile(reader: Any, plan: dict[str, Any], **changes: Any) -> dict[str, Any]:
    expires = plan["schedule"]["expires_ns"]
    defaults = {
        "expected_plan_sha256": verifier.sha(reader.files[schema.PLAN_FILE]),
        "expected_root_id": plan["storage"]["root_id"],
        "expected_volume_uuid": plan["storage"]["volume_uuid"],
        "proposed_service_id": "test-finite-successor",
        "proposed_bootstrap_start_ns": expires + 60_000_000_000,
        "proposed_frame_start_ns": expires + 360_000_000_000,
        "now_ns": expires,
    }
    defaults.update(changes)
    return successor.compile_candidate(reader, **defaults)


def _refused(reason: str, function: Any) -> None:
    with pytest.raises(successor.SuccessorRefused) as caught:
        function()
    assert caught.value.reason == reason


def test_compiles_nonauthorizing_candidate_from_sealed_v1_and_preserves_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, plan = _reader(monkeypatch)
    before = dict(reader.files)
    candidate = _compile(reader, plan)

    assert set(candidate) == {
        "schema",
        "schema_version",
        "predecessor",
        "predecessor_sha256",
        "proposal",
        "activation_authorized",
        "dispatch_authorized",
        "replay_authorized",
        "provider_io",
        "provider_charge_units",
        "provider_charge_status",
    }
    assert candidate["schema"] == "brain-senses-external-successor-v2-candidate"
    assert candidate["schema_version"] == 2
    assert candidate["predecessor"]["kind"] == "external-v1-sealed"
    events = verifier.verify_events(reader.files[schema.EVENTS_FILE])
    assert candidate["predecessor"]["terminal_sealed_ns"] == events[-1]["at_ns"]
    assert candidate["predecessor"]["event_tail_sha256"] == events[-1]["event_sha256"]
    assert candidate["predecessor"]["events_file_sha256"] == verifier.sha(
        reader.files[schema.EVENTS_FILE]
    )
    assert candidate["predecessor"]["frontier"] == 103
    assert candidate["predecessor"]["requests"] == 7
    assert candidate["predecessor"]["raw_bytes"] == candidate["predecessor"]["stored_bytes"]
    assert candidate["predecessor_sha256"] == verifier.sha(
        verifier.canonical(candidate["predecessor"])
    )
    assert candidate["proposal"]["caps"] == plan["caps"]
    assert (
        candidate["proposal"]["schedule"]["expires_ns"]
        == (candidate["proposal"]["schedule"]["frame"]["end_ns"])
    )
    assert candidate["activation_authorized"] is False
    assert candidate["dispatch_authorized"] is False
    assert candidate["replay_authorized"] is False
    assert candidate["provider_io"] is False
    assert candidate["provider_charge_units"] is None
    assert candidate["provider_charge_status"] == "unknown_unverified"
    assert reader.files == before


def test_every_verified_gap_is_carried_into_predecessor(monkeypatch: pytest.MonkeyPatch) -> None:
    original_plan = _FIXTURES.plan

    def plan_with_gap() -> dict[str, Any]:
        value = cast(dict[str, Any], original_plan())
        value["predecessor"]["gap_intervals"] = [
            {"start": 70, "end_exclusive": 80, "reason": "prior_unknown_interval"}
        ]
        return value

    monkeypatch.setattr(_FIXTURES, "plan", plan_with_gap)
    reader, plan = _reader(monkeypatch)
    candidate = _compile(reader, plan)
    assert candidate["predecessor"]["gap_intervals"][0] == plan["predecessor"]["gap_intervals"][0]
    assert len(candidate["predecessor"]["gap_intervals"]) == 2


def test_one_sided_stop_and_missing_seal_require_separate_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, plan = _reader(monkeypatch)
    frame = plan["schedule"]["frame"]["bucket"]
    stop = {
        "schema": schema.STOP_SCHEMA,
        "schema_version": 1,
        "reason": "test_terminal",
        "stopped_ns": plan["schedule"]["expires_ns"] + 1_000_000_000,
        "last_bucket": frame,
        "bundle_sha256": verifier.sha(reader.files[f"buckets/{frame}/bundle.json"]),
        "post_stop_requests": 0,
    }
    reader.files[schema.STOP_FILE] = verifier.canonical(stop)
    _refused("requires_separate_failure_review", lambda: _compile(reader, plan))

    reader, plan = _reader(monkeypatch)
    frame = plan["schedule"]["frame"]["bucket"]
    del reader.files[f"buckets/{frame}/seal.json"]
    _refused("requires_separate_failure_review", lambda: _compile(reader, plan))


def test_stale_plan_pin_and_tampered_event_tail_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, plan = _reader(monkeypatch)
    _refused(
        "stale_plan_pin",
        lambda: _compile(reader, plan, expected_plan_sha256="0" * 64),
    )

    reader, plan = _reader(monkeypatch)
    reader.files[schema.EVENTS_FILE] += b"not-a-complete-event\n"
    _refused("requires_separate_failure_review", lambda: _compile(reader, plan))


def test_spent_reservation_cannot_be_reclassified_as_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, plan = _reader(monkeypatch)
    frame = plan["schedule"]["frame"]["bucket"]
    assert f"buckets/{frame}/reservation.json" in reader.files
    reader.event("gap", frame, plan["schedule"]["frame"]["end_ns"], {})
    _refused("requires_separate_failure_review", lambda: _compile(reader, plan))


def _rewrite_frame_decision_before_prior_availability(reader: Any, plan: dict[str, Any]) -> None:
    bucket = plan["schedule"]["frame"]["bucket"]
    base = f"buckets/{bucket}"
    request_path = f"{base}/requests/01.json"
    result_path = f"{base}/results/01.json"
    receipt_path = f"{base}/captures/01/receipt.json"
    previous_result = verifier.decode(reader.files[f"{base}/results/00.json"])
    request = verifier.decode(reader.files[request_path])
    result = verifier.decode(reader.files[result_path])
    receipt = verifier.decode(reader.files[receipt_path])
    decision_ns = previous_result["availability_ns"] - 1
    assert decision_ns >= plan["schedule"]["frame"]["start_ns"] + 10_000_000_000
    assert decision_ns < request["request_started_ns"]
    request["decision_ns"] = decision_ns
    receipt["decision_ns"] = decision_ns
    result["decision_ns"] = decision_ns
    reader.files[request_path] = verifier.canonical(request)
    receipt_bytes = verifier.canonical(receipt)
    reader.files[receipt_path] = receipt_bytes
    result["receipt_sha256"] = verifier.sha(receipt_bytes)
    result_bytes = verifier.canonical(result)
    reader.files[result_path] = result_bytes

    bundle_path = f"{base}/bundle.json"
    bundle = verifier.decode(reader.files[bundle_path])
    row = next(row for row in bundle["requests"] if row["request_id"] == request["request_id"])
    row["receipt_sha256"] = verifier.sha(receipt_bytes)
    row["result_sha256"] = verifier.sha(result_bytes)
    bundle_bytes = verifier.canonical(bundle)
    reader.files[bundle_path] = bundle_bytes
    bundle_sha = verifier.sha(bundle_bytes)

    seal_path = f"{base}/seal.json"
    seal = verifier.decode(reader.files[seal_path])
    seal["bundle_sha256"] = bundle_sha
    seal_bytes = verifier.canonical(seal)
    reader.files[seal_path] = seal_bytes
    seal_sha = verifier.sha(seal_bytes)

    events = verifier.verify_events(reader.files[schema.EVENTS_FILE])
    for event in events:
        if event["bucket"] != bucket:
            continue
        if event["kind"] == "bundle":
            event["payload"]["bundle_sha256"] = bundle_sha
        elif event["kind"] == "seal":
            event["payload"]["seal_sha256"] = seal_sha
    previous = "0" * 64
    for event in events:
        event["previous_sha256"] = previous
        event.pop("event_sha256", None)
        event["event_sha256"] = verifier.sha(verifier.canonical(event))
        previous = event["event_sha256"]
    reader.files[schema.EVENTS_FILE] = b"".join(verifier.canonical(event) for event in events)


def _rewrite_frame_seal_time(reader: Any, plan: dict[str, Any], sealed_ns: int) -> None:
    bucket = plan["schedule"]["frame"]["bucket"]
    base = f"buckets/{bucket}"
    seal_path = f"{base}/seal.json"
    seal = verifier.decode(reader.files[seal_path])
    seal["sealed_ns"] = sealed_ns
    seal_bytes = verifier.canonical(seal)
    reader.files[seal_path] = seal_bytes
    events = verifier.verify_events(reader.files[schema.EVENTS_FILE])
    seal_event = next(
        event for event in events if event["kind"] == "seal" and event["bucket"] == bucket
    )
    seal_event["at_ns"] = sealed_ns
    seal_event["payload"]["seal_sha256"] = verifier.sha(seal_bytes)
    previous = "0" * 64
    for event in events:
        event["previous_sha256"] = previous
        event.pop("event_sha256", None)
        event["event_sha256"] = verifier.sha(verifier.canonical(event))
        previous = event["event_sha256"]
    reader.files[schema.EVENTS_FILE] = b"".join(verifier.canonical(event) for event in events)


def test_v1_clean_prefix_with_dispatch_decision_before_prior_availability_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, plan = _reader(monkeypatch)
    _rewrite_frame_decision_before_prior_availability(reader, plan)
    state = verifier.verify(reader, expected_sources={"source.py": "a" * 64})
    assert state["status"] == "verified_prefix"
    _refused(
        "acquisition_decision_before_prior_availability",
        lambda: _compile(reader, plan),
    )


def test_candidate_waits_for_late_terminal_seal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, plan = _reader(monkeypatch)
    late_seal_ns = plan["schedule"]["expires_ns"] + 2_000_000_000
    _rewrite_frame_seal_time(reader, plan, late_seal_ns)
    state = verifier.verify(reader, expected_sources={"source.py": "a" * 64})
    assert state["status"] == "verified_prefix"
    _refused(
        "predecessor_not_expired",
        lambda: _compile(reader, plan, now_ns=plan["schedule"]["expires_ns"]),
    )


def test_during_verification_evidence_mutation_is_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader, plan = _reader(monkeypatch)
    original_verify = verifier.verify

    def verify_then_mutate(*args: Any, **kwargs: Any) -> dict[str, Any]:
        state = original_verify(*args, **kwargs)
        reader.files[schema.EVENTS_FILE] += b" "
        return state

    monkeypatch.setattr(verifier, "verify", verify_then_mutate)
    _refused("requires_separate_failure_review", lambda: _compile(reader, plan))


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"now_ns": 1_799_999_999_999_999_999}, "predecessor_not_expired"),
        (
            {
                "proposed_frame_start_ns": 1_800_000_000_000_000_000
                + 480_000_000_000
                + 60_000_000_000,
                "now_ns": 1_800_000_000_000_000_000 + 480_000_000_000,
            },
            "schedule_spacing_invalid",
        ),
        ({"proposed_service_id": "test-finite"}, "service_id_reuse"),
        ({"proposed_service_id": "test.finite"}, "service_id_invalid"),
        ({"proposed_service_id": "s" * 81}, "service_id_invalid"),
    ],
)
def test_early_overlapping_and_reused_proposals_are_refused(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any], reason: str
) -> None:
    reader, plan = _reader(monkeypatch)
    _refused(reason, lambda: _compile(reader, plan, **changes))
