from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from email.utils import formatdate, parsedate_to_datetime
from typing import Any

import pytest

from tools import brain_senses_external_orchestrator_v2 as engine
from tools import brain_senses_external_schema_v2 as schema
from tools import brain_senses_external_verify_v2 as verify


def _plan() -> dict:
    now = 1_800_000_000_000_000_000
    start = ((now + 2 * 60_000_000_000 + 59_000_000_999) // 60_000_000_000) * 60_000_000_000
    cert = {
        "kind": "external-v1-sealed",
        "service_id": "old-service",
        "plan_sha256": "1" * 64,
        "source_sha256": {"source.py": "2" * 64},
        "storage": {
            "root_id": "test-root",
            "volume_uuid": "test-volume",
            "relative_root": "services/old-service",
        },
        "event_tail_sha256": "3" * 64,
        "events_file_sha256": "4" * 64,
        "bootstrap": {
            "bucket": "20261011T210000Z",
            "bundle_sha256": "5" * 64,
            "seal_sha256": "6" * 64,
        },
        "frame": {"bucket": "20261011T210500Z", "bundle_sha256": "7" * 64, "seal_sha256": "8" * 64},
        "frontier": 455802008,
        "gap_intervals": [],
        "requests": 35,
        "raw_bytes": 64_223_372,
        "stored_bytes": 64_223_372,
        "expires_ns": start - 120_000_000_000,
        "terminal_sealed_ns": start - 120_000_000_000,
    }
    cert_sha = engine.sha(engine.canonical(cert))
    return {
        "schema": schema.PLAN_SCHEMA,
        "schema_version": 2,
        "service_id": "new-service-01",
        "provider": "helius-free",
        "endpoint_origin": "https://mainnet.helius-rpc.com",
        "storage": {
            "root_id": "test-root",
            "volume_uuid": "test-volume",
            "relative_root": "services/new-service-01",
        },
        "code_sha256": {"source.py": "9" * 64},
        "predecessor": {
            "certificate": cert,
            "certificate_sha256": cert_sha,
            "relative_root": "services/old-service",
            "candidate_sha256": "a" * 64,
            "validation_sha256": "b" * 64,
            "completion_sha256": "c" * 64,
        },
        "schedule": engine.build_schedule(start, now),
        "caps": dict(schema.CAPS),
    }


def test_plan_has_exact_thirteen_fixed_windows_and_caps() -> None:
    plan = _plan()
    verify.verify_plan(plan, expected_sources=plan["code_sha256"])
    schedule = plan["schedule"]
    assert len(schedule["frames"]) == 12
    assert schedule["bootstrap"]["end_ns"] - schedule["bootstrap"]["start_ns"] == 60_000_000_000
    assert all(
        frame["end_ns"] - frame["start_ns"] == 180_000_000_000 for frame in schedule["frames"]
    )
    assert (
        len({schedule["bootstrap"]["bucket"], *(frame["bucket"] for frame in schedule["frames"])})
        == 13
    )
    assert plan["caps"]["max_requests_total"] == 409


@pytest.mark.parametrize(
    "mutation", ["unknown_plan_key", "wrong_window", "bad_service", "weaker_caps"]
)
def test_plan_rejects_unreviewed_shape(mutation: str) -> None:
    plan = _plan()
    if mutation == "unknown_plan_key":
        plan["extra"] = True
    elif mutation == "wrong_window":
        plan["schedule"]["frames"][5]["end_ns"] += 1
    elif mutation == "bad_service":
        plan["service_id"] = "bad.service"
    else:
        plan["caps"]["max_requests_total"] -= 1
    with pytest.raises(verify.VerificationError):
        verify.verify_plan(plan)


def test_schedule_refuses_clock_lead_below_two_minutes() -> None:
    minute = 60_000_000_000
    with pytest.raises(engine.ExternalV2Error, match="schedule_bootstrap_invalid"):
        engine.build_schedule(200 * minute, 199 * minute)


def test_frame_and_lease_admission_reserve_one_full_response() -> None:
    response = schema.MAX_RESPONSE_BYTES
    assert engine._frame_bytes_admissible(224_000_000, schema.RAW_LEASE_BYTES - response)
    assert not engine._frame_bytes_admissible(224_000_001, 0)
    assert not engine._frame_bytes_admissible(0, schema.RAW_LEASE_BYTES - response + 1)


def test_utc_day_budget_reserves_full_response_and_intent_at_boundary() -> None:
    plan = _plan()
    dispatch_ns = 1_800_000_000_000_000_000
    day = dispatch_ns // 86_400_000_000_000
    response = schema.MAX_RESPONSE_BYTES
    state = {
        "requests": 0,
        "v2_stored_bytes": 0,
        "v2_day_usage": {
            day: {
                "requests": schema.UTC_DAY_REQUESTS - 1,
                "raw_bytes_reserved": schema.UTC_DAY_BYTES - response,
                "stored_bytes_reserved": schema.UTC_DAY_BYTES - response,
            }
        },
        "predecessor_day_usage": [],
    }
    assert engine._budget_check(state, plan, dispatch_ns, response)
    state["v2_day_usage"][day]["raw_bytes_reserved"] += 1
    state["v2_day_usage"][day]["stored_bytes_reserved"] += 1
    assert not engine._budget_check(state, plan, dispatch_ns, response)
    state["v2_day_usage"][day]["raw_bytes_reserved"] -= 1
    state["v2_day_usage"][day]["stored_bytes_reserved"] -= 1
    state["v2_day_usage"][day]["requests"] += 1
    assert not engine._budget_check(state, plan, dispatch_ns, response)


def test_schedule_buckets_remain_utc_aligned_across_midnight() -> None:
    bootstrap = int(datetime(2026, 10, 12, 23, 30, tzinfo=UTC).timestamp()) * 1_000_000_000
    schedule = engine.build_schedule(bootstrap, bootstrap - 2 * 60_000_000_000)
    assert schedule["bootstrap"]["bucket"] == "20261012T233000Z"
    assert schedule["frames"][5]["bucket"] == "20261013T000000Z"


def test_event_chain_rejects_duplicate_sequence_or_tampering() -> None:
    body = {
        "schema": schema.EVENT_SCHEMA,
        "schema_version": 2,
        "seq": 0,
        "kind": "initialized",
        "bucket": None,
        "at_ns": 1,
        "payload": {"plan_sha256": "d" * 64},
        "previous_sha256": "0" * 64,
    }
    event = {**body, "event_sha256": engine.sha(engine.canonical(body))}
    encoded = engine.canonical(event)
    assert verify.verify_events(encoded) == [event]
    broken = dict(event, at_ns=2)
    with pytest.raises(verify.VerificationError):
        verify.verify_events(engine.canonical(broken))


def test_event_digest_is_sha256_canonical_json() -> None:
    plan = _plan()
    encoded = engine.canonical(plan)
    assert hashlib.sha256(encoded).hexdigest() == engine.sha(encoded)


@pytest.mark.parametrize(
    ("detail", "mutation"),
    [
        ("signatures", "commitment"),
        ("signatures", "encoding"),
        ("signatures", "transactionDetails"),
        ("signatures", "rewards"),
        ("signatures", "version_field"),
        ("full", "commitment"),
        ("full", "encoding"),
        ("full", "transactionDetails"),
        ("full", "rewards"),
        ("full", "maxSupportedTransactionVersion"),
    ],
)
def test_exact_getblock_options_reject_any_semantic_mutation(detail: str, mutation: str) -> None:
    params: list[Any] = [
        455_802_050,
        {
            "commitment": "finalized",
            "encoding": "json",
            "transactionDetails": detail,
            "rewards": False,
            **({"maxSupportedTransactionVersion": 1} if detail == "full" else {}),
        },
    ]
    if mutation == "version_field":
        params[1]["maxSupportedTransactionVersion"] = 1
    elif mutation == "maxSupportedTransactionVersion":
        params[1]["maxSupportedTransactionVersion"] = 0
    elif mutation == "transactionDetails":
        params[1]["transactionDetails"] = "full" if detail == "signatures" else "signatures"
    elif mutation == "commitment":
        params[1]["commitment"] = "confirmed"
    elif mutation == "encoding":
        params[1]["encoding"] = "base64"
    elif mutation == "rewards":
        params[1]["rewards"] = True
    with pytest.raises(verify.VerificationError, match="selected_block_params"):
        verify._check_block_params(params, 455_802_050, detail)


class _MemoryReader:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def read_bytes(self, path: str, **_: object) -> bytes:
        return self.files[path]

    def list_files(self) -> list[str]:
        return sorted(self.files)


def _event(
    seq: int, kind: str, bucket: str | None, at_ns: int, payload: dict, previous: str
) -> dict:
    body = {
        "schema": schema.EVENT_SCHEMA,
        "schema_version": 2,
        "seq": seq,
        "kind": kind,
        "bucket": bucket,
        "at_ns": at_ns,
        "payload": payload,
        "previous_sha256": previous,
    }
    return {**body, "event_sha256": engine.sha(engine.canonical(body))}


def test_durable_request_intent_is_classified_spent_without_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    plan_raw = engine.canonical(plan)
    bucket = plan["schedule"]["bootstrap"]["bucket"]
    clock_path = f"clock/{bucket}-00.json"
    clock_raw = _bootstrap_bundle_reader(plan).files[clock_path]
    clock_hash = engine.sha(clock_raw)
    reservation_file = {
        "schema": schema.BUCKET_SCHEMA,
        "schema_version": 2,
        "clock_check_path": clock_path,
        "clock_check_sha256": clock_hash,
    }
    started_file = {"schema": schema.BUCKET_SCHEMA, "schema_version": 2}
    reservation_raw = engine.canonical(reservation_file)
    started_raw = engine.canonical(started_file)
    reservation = {"reservation_sha256": engine.sha(reservation_raw), "frame_index": None}
    started = {"started_sha256": engine.sha(started_raw)}
    events = [
        _event(
            0,
            "initialized",
            None,
            1,
            {
                "plan_sha256": engine.sha(plan_raw),
                "predecessor_day_usage": [],
                "predecessor_day_usage_sha256": engine.sha(engine.canonical([])),
            },
            "0" * 64,
        )
    ]
    dispatch_ns = plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000
    events.append(
        _event(1, "reservation", bucket, dispatch_ns, reservation, events[-1]["event_sha256"])
    )
    events.append(
        _event(2, "started", bucket, dispatch_ns + 1, started, events[-1]["event_sha256"])
    )
    events.append(
        _event(
            3,
            "request_intent",
            bucket,
            dispatch_ns + 250_000_001,
            {
                "ordinal": 0,
                "request_sha256": "c" * 64,
                "request_started_ns": dispatch_ns + 250_000_001,
            },
            events[-1]["event_sha256"],
        )
    )
    files = {
        schema.PLAN_FILE: plan_raw,
        schema.EVENTS_FILE: b"".join(engine.canonical(event) for event in events),
        f"buckets/{bucket}/reservation.json": reservation_raw,
        f"buckets/{bucket}/started.json": started_raw,
        clock_path: clock_raw,
    }
    reader = _MemoryReader(files)
    monkeypatch.setattr(
        verify,
        "verify_live_predecessor",
        lambda *_: {
            "day_usage": [],
            "plan": {"provider": plan["provider"], "endpoint_origin": plan["endpoint_origin"]},
        },
    )
    state = verify.verify(
        reader,
        expected_plan_sha256=engine.sha(plan_raw),
        expected_sources=plan["code_sha256"],
        predecessor_reader=object(),
    )
    assert state["status"] == "interrupted_no_replay"
    assert state["unresolved_buckets"] == [bucket]
    assert state["dispatch_authorized"] is False
    assert state["replay_authorized"] is False
    assert state["v2_raw_bytes"] == schema.MAX_RESPONSE_BYTES
    assert state["v2_stored_bytes"] == schema.MAX_RESPONSE_BYTES
    with pytest.raises(verify.VerificationError, match="predecessor_lineage_unavailable"):
        verify.verify(
            reader,
            expected_plan_sha256=engine.sha(plan_raw),
            expected_sources=plan["code_sha256"],
        )


def test_intent_day_budget_uses_dispatch_day_when_capture_starts_after_midnight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    start = int(datetime(2026, 10, 12, 23, 59, tzinfo=UTC).timestamp()) * 1_000_000_000
    preflight = start - 120_000_000_000
    plan["schedule"] = engine.build_schedule(start, preflight)
    plan["predecessor"]["certificate"]["expires_ns"] = start - 180_000_000_000
    plan["predecessor"]["certificate"]["terminal_sealed_ns"] = start - 180_000_000_000
    plan["predecessor"]["certificate_sha256"] = engine.sha(
        engine.canonical(plan["predecessor"]["certificate"])
    )
    plan_raw = engine.canonical(plan)
    bucket = plan["schedule"]["bootstrap"]["bucket"]
    base = f"buckets/{bucket}"
    decision_ns = start + 54_900_000_000
    next_day = int(datetime(2026, 10, 13, 0, 0, tzinfo=UTC).timestamp()) * 1_000_000_000
    params = [{"commitment": "finalized"}]
    semantic = engine.sha(engine.canonical({"method": "getSlot", "params": params}))
    wire = engine.sha(
        engine.canonical({"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": params}).rstrip(
            b"\n"
        )
    )
    clock_path = f"clock/{bucket}-00.json"
    clock_fixture = _bootstrap_bundle_reader(plan)
    clock_raw = clock_fixture.files[clock_path]
    clock_record = verify.decode(clock_raw)
    clock_hash = engine.sha(clock_raw)
    request = {
        "schema": schema.REQUEST_SCHEMA,
        "schema_version": 2,
        "provider": plan["provider"],
        "request_id": f"{bucket}-00",
        "reservation_id": "midnight-reservation",
        "ordinal": 0,
        "phase": "bootstrap",
        "frame_index": None,
        "method": "getSlot",
        "params": params,
        "request_sha256": semantic,
        "wire_request_sha256": wire,
        "decision_ns": decision_ns,
        "request_started_ns": decision_ns,
        "monotonic_ns": clock_record["monotonic_ns"] + decision_ns - clock_record["checked_ns"],
        "clock_check_path": clock_path,
        "clock_check_sha256": clock_hash,
    }
    request_raw = engine.canonical(request)
    response_raw = b'{"jsonrpc":"2.0","id":1,"result":455802050}'
    receipt = {
        "schema": schema.RECEIPT_SCHEMA,
        "schema_version": 2,
        "provider": plan["provider"],
        "request_id": request["request_id"],
        "request_sha256": semantic,
        "wire_request_sha256": wire,
        "request_started_ns": next_day + 100_000_000,
        "received_ns": next_day + 200_000_000,
        "availability_ns": None,
        "response_sha256": engine.sha(response_raw),
        "response_bytes": len(response_raw),
        "storage": {"path": "response.json", "bytes": len(response_raw), "codec": "raw"},
    }
    receipt_raw = engine.canonical(receipt)
    reservation = {
        "schema": schema.BUCKET_SCHEMA,
        "schema_version": 2,
        "clock_check_path": clock_path,
        "clock_check_sha256": clock_hash,
    }
    started = {"schema": schema.BUCKET_SCHEMA, "schema_version": 2}
    reservation_raw = engine.canonical(reservation)
    started_raw = engine.canonical(started)
    events = [
        _event(
            0,
            "initialized",
            None,
            preflight,
            {
                "plan_sha256": engine.sha(plan_raw),
                "predecessor_day_usage": [],
                "predecessor_day_usage_sha256": engine.sha(engine.canonical([])),
            },
            "0" * 64,
        ),
        _event(
            1,
            "reservation",
            bucket,
            start + 10_000_000_000,
            {"reservation_sha256": engine.sha(reservation_raw), "frame_index": None},
            "0" * 64,
        ),
    ]
    # Rebuild the second event after its predecessor is known.
    events[1]["previous_sha256"] = events[0]["event_sha256"]
    events[1]["event_sha256"] = engine.sha(
        engine.canonical({key: value for key, value in events[1].items() if key != "event_sha256"})
    )
    events.extend(
        [
            _event(
                2,
                "started",
                bucket,
                start + 10_000_000_001,
                {"started_sha256": engine.sha(started_raw)},
                events[-1]["event_sha256"],
            ),
            _event(
                3,
                "request_intent",
                bucket,
                decision_ns,
                {
                    "ordinal": 0,
                    "request_sha256": engine.sha(request_raw),
                    "request_started_ns": decision_ns,
                },
                "0" * 64,
            ),
        ]
    )
    events[3]["previous_sha256"] = events[2]["event_sha256"]
    events[3]["event_sha256"] = engine.sha(
        engine.canonical({key: value for key, value in events[3].items() if key != "event_sha256"})
    )
    reader = _MemoryReader(
        {
            schema.PLAN_FILE: plan_raw,
            schema.EVENTS_FILE: b"".join(engine.canonical(event) for event in events),
            f"{base}/reservation.json": reservation_raw,
            f"{base}/started.json": started_raw,
            f"{base}/requests/00.json": request_raw,
            f"{base}/captures/00/receipt.json": receipt_raw,
            f"{base}/captures/00/response.json": response_raw,
            clock_path: clock_raw,
        }
    )
    monkeypatch.setattr(
        verify,
        "verify_live_predecessor",
        lambda *_: {
            "day_usage": [],
            "plan": {"provider": plan["provider"], "endpoint_origin": plan["endpoint_origin"]},
        },
    )
    state = verify.verify(
        reader,
        expected_plan_sha256=engine.sha(plan_raw),
        expected_sources=plan["code_sha256"],
        predecessor_reader=object(),
    )
    local_intent_day = decision_ns // 86_400_000_000_000
    transport_day = receipt["request_started_ns"] // 86_400_000_000_000
    assert local_intent_day != transport_day
    assert state["v2_day_usage"][local_intent_day]["requests"] == 1
    assert transport_day not in state["v2_day_usage"]


def _bootstrap_bundle_reader(plan: dict) -> _MemoryReader:
    plan_raw = engine.canonical(plan)
    bucket = plan["schedule"]["bootstrap"]["bucket"]
    reservation_id = "reservation-v2"
    request_id = f"{bucket}-00"
    method = "getSlot"
    params = [{"commitment": "finalized"}]
    semantic = engine.sha(engine.canonical({"method": method, "params": params}))
    wire = engine.sha(
        engine.canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).rstrip(
            b"\n"
        )
    )
    decision_ns = plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000
    clock_wall = decision_ns - 500_000_000
    clock_mono = 10_000_000_000
    clock_rows = []
    for index, host in enumerate(("www.cloudflare.com", "www.google.com")):
        sent = clock_wall - 200_000_000 + index * 10_000_000
        received = sent + 10_000_000
        mono_sent = clock_mono - 200_000_000 + index * 10_000_000
        mono_received = mono_sent + 10_000_000
        clock_rows.append(
            {
                "host": host,
                "status": 200,
                "date": formatdate(sent // 1_000_000_000, usegmt=True),
                "age": None,
                "sent_ns": sent,
                "received_ns": received,
                "monotonic_sent_ns": mono_sent,
                "monotonic_received_ns": mono_received,
            }
        )
    clock_record = {
        "schema": "brain-senses-external-v1-clock",
        "schema_version": 1,
        "samples": clock_rows,
        "checked_ns": clock_rows[-1]["received_ns"],
        "monotonic_ns": clock_rows[-1]["monotonic_received_ns"],
    }
    clock_raw = engine.canonical(clock_record)
    clock_hash = engine.sha(clock_raw)
    request = {
        "schema": schema.REQUEST_SCHEMA,
        "schema_version": 2,
        "provider": plan["provider"],
        "request_id": request_id,
        "reservation_id": reservation_id,
        "ordinal": 0,
        "phase": "bootstrap",
        "frame_index": None,
        "method": method,
        "params": params,
        "request_sha256": semantic,
        "wire_request_sha256": wire,
        "decision_ns": decision_ns,
        "request_started_ns": decision_ns,
        "monotonic_ns": clock_mono + 300_000_000,
        "clock_check_path": f"clock/{bucket}-00.json",
        "clock_check_sha256": clock_hash,
    }
    request_raw = engine.canonical(request)
    response_raw = engine.canonical({"jsonrpc": "2.0", "id": 1, "result": 455802050})
    response_hash = engine.sha(response_raw)
    receipt = {
        "schema": schema.RECEIPT_SCHEMA,
        "schema_version": 2,
        "provider": plan["provider"],
        "request_id": request_id,
        "request_sha256": semantic,
        "wire_request_sha256": wire,
        "request_started_ns": request["request_started_ns"],
        "received_ns": decision_ns + 2,
        "availability_ns": None,
        "response_sha256": response_hash,
        "response_bytes": len(response_raw),
        "storage": {"path": "response.json", "bytes": len(response_raw), "codec": "raw"},
    }
    receipt_raw = engine.canonical(receipt)
    result = {
        "schema": schema.RESULT_SCHEMA,
        "schema_version": 2,
        "provider": plan["provider"],
        "request_id": request_id,
        "request_sha256": semantic,
        "wire_request_sha256": wire,
        "status": "captured",
        "result_status": "parsed",
        "response_sha256": response_hash,
        "response_bytes": len(response_raw),
        "stored_response_bytes": len(response_raw),
        "availability_ns": decision_ns + 3,
        "decision_ns": decision_ns,
        "method": "getSlot",
        "event_slot": 455802050,
        "produced_slots": None,
        "signatures": None,
        "block_identity": None,
        "event_time_ns": None,
        "http_status": None,
        "transport_category": None,
    }
    result_raw = engine.canonical(result)
    base = f"buckets/{bucket}"
    reservation = {
        "schema": schema.BUCKET_SCHEMA,
        "schema_version": 2,
        "bucket": bucket,
        "phase": "bootstrap",
        "frame_index": None,
        "reservation_id": reservation_id,
        "plan_sha256": engine.sha(plan_raw),
        "request_limit": 1,
        "raw_byte_cap": schema.MAX_RESPONSE_BYTES,
        "stored_byte_cap": schema.MAX_RESPONSE_BYTES,
        "window": {
            "start_ns": plan["schedule"]["bootstrap"]["start_ns"],
            "end_ns": plan["schedule"]["bootstrap"]["end_ns"],
        },
        "created_ns": decision_ns - 10,
        "predecessor_event_sha256": "d" * 64,
        "clock_check_path": f"clock/{bucket}-00.json",
        "clock_check_sha256": clock_hash,
    }
    started = {
        "schema": schema.BUCKET_SCHEMA,
        "schema_version": 2,
        "reservation_id": reservation_id,
        "plan_sha256": engine.sha(plan_raw),
        "started_ns": decision_ns - 5,
        "pid": 1,
    }
    bundle = {
        "schema": schema.BUNDLE_SCHEMA,
        "schema_version": 2,
        "provider": plan["provider"],
        "phase": "bootstrap",
        "frame_index": None,
        "bucket": bucket,
        "reservation_id": reservation_id,
        "plan_sha256": engine.sha(plan_raw),
        "status": "complete",
        "request_count": 1,
        "requests": [
            {
                "ordinal": 0,
                "request_sha256": engine.sha(request_raw),
                "receipt_sha256": engine.sha(receipt_raw),
                "result_sha256": engine.sha(result_raw),
                "response_sha256": response_hash,
            }
        ],
        "raw_bytes": len(response_raw),
        "stored_bytes": len(response_raw),
        "bootstrap_slot": 455802050,
        "frame": None,
        "selected_blocks": [],
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
        "terminal_status": "bootstrap_complete",
    }
    bundle_raw = engine.canonical(bundle)
    seal = {
        "schema": schema.SEAL_SCHEMA,
        "schema_version": 2,
        "bucket": bucket,
        "phase": "bootstrap",
        "frame_index": None,
        "reservation_id": reservation_id,
        "plan_sha256": engine.sha(plan_raw),
        "bundle_sha256": engine.sha(bundle_raw),
        "sealed_ns": decision_ns + 5,
        "status": "complete",
    }
    events = [
        _event(0, "initialized", None, 1, {"plan_sha256": engine.sha(plan_raw)}, "0" * 64),
        _event(
            1,
            "reservation",
            bucket,
            reservation["created_ns"],
            {"reservation_sha256": engine.sha(engine.canonical(reservation)), "frame_index": None},
            "0" * 64,
        ),
    ]
    events[1]["previous_sha256"] = events[0]["event_sha256"]
    events[1]["event_sha256"] = engine.sha(
        engine.canonical({key: value for key, value in events[1].items() if key != "event_sha256"})
    )
    events.append(
        _event(
            2,
            "started",
            bucket,
            started["started_ns"],
            {"started_sha256": engine.sha(engine.canonical(started))},
            events[-1]["event_sha256"],
        )
    )
    events.append(
        _event(
            3,
            "request_intent",
            bucket,
            decision_ns,
            {
                "ordinal": 0,
                "request_sha256": engine.sha(request_raw),
                "request_started_ns": request["request_started_ns"],
            },
            events[-1]["event_sha256"],
        )
    )
    events.append(
        _event(
            4,
            "bundle",
            bucket,
            decision_ns + 4,
            {"bundle_sha256": engine.sha(bundle_raw)},
            events[-1]["event_sha256"],
        )
    )
    seal_raw = engine.canonical(seal)
    events.append(
        _event(
            5,
            "seal",
            bucket,
            seal["sealed_ns"],
            {"seal_sha256": engine.sha(seal_raw)},
            events[-1]["event_sha256"],
        )
    )
    files = {
        schema.PLAN_FILE: plan_raw,
        schema.EVENTS_FILE: b"".join(engine.canonical(event) for event in events),
        f"clock/{bucket}-00.json": clock_raw,
        f"{base}/reservation.json": engine.canonical(reservation),
        f"{base}/started.json": engine.canonical(started),
        f"{base}/requests/00.json": request_raw,
        f"{base}/captures/00/receipt.json": receipt_raw,
        f"{base}/captures/00/response.json": response_raw,
        f"{base}/results/00.json": result_raw,
        f"{base}/bundle.json": bundle_raw,
        f"{base}/seal.json": seal_raw,
    }
    return _MemoryReader(files)


def test_independent_verifier_recomputes_bootstrap_bundle_and_seal() -> None:
    plan = _plan()
    bucket = plan["schedule"]["bootstrap"]["bucket"]
    result = verify._verify_bucket(
        _bootstrap_bundle_reader(plan),
        plan,
        bucket,
        plan["predecessor"]["certificate"]["frontier"],
    )
    assert result["bundle"]["bootstrap_slot"] == 455802050
    assert result["frontier"] == 455802050


def test_v2_exact_file_closure_rejects_stray_capture() -> None:
    plan = _plan()
    reader = _bootstrap_bundle_reader(plan)
    bucket = plan["schedule"]["bootstrap"]["bucket"]
    reader.files[f"buckets/{bucket}/captures/99/response.json"] = b"stray"
    _rewrite_event_chain(
        reader,
        0,
        payload={
            "plan_sha256": engine.sha(reader.files[schema.PLAN_FILE]),
            "predecessor_day_usage": [],
            "predecessor_day_usage_sha256": engine.sha(engine.canonical([])),
        },
    )

    # The synthetic v1 lineage is independently mocked in these unit fixtures.
    original = verify.verify_live_predecessor
    verify.verify_live_predecessor = lambda *_: {
        "day_usage": [],
        "plan": {"provider": plan["provider"], "endpoint_origin": plan["endpoint_origin"]},
    }
    try:
        with pytest.raises(verify.VerificationError, match="unreferenced_file"):
            verify.verify(
                reader,
                expected_plan_sha256=engine.sha(reader.files[schema.PLAN_FILE]),
                expected_sources=plan["code_sha256"],
                predecessor_reader=object(),
            )
    finally:
        verify.verify_live_predecessor = original


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("arbitraryMethod", []),
        ("getBlocks", [455802008, 455802050, {"commitment": "finalized"}]),
    ],
)
def test_prefix_machine_rejects_wrong_first_operation(method: str, params: list[Any]) -> None:
    plan = _plan()
    bucket = plan["schedule"]["bootstrap"]["bucket"]
    basis = _bootstrap_bundle_reader(plan)
    request = verify.decode(basis.files[f"buckets/{bucket}/requests/00.json"])
    request.update(method=method, params=params)
    request["request_sha256"] = engine.sha(engine.canonical({"method": method, "params": params}))
    request["wire_request_sha256"] = engine.sha(
        engine.canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).rstrip(
            b"\n"
        )
    )
    paths = {
        f"buckets/{bucket}/requests/00.json": engine.canonical(request),
        f"clock/{bucket}-00.json": basis.files[f"clock/{bucket}-00.json"],
    }
    reader = _MemoryReader(paths)
    with pytest.raises(verify.VerificationError, match="prefix_operation_mismatch"):
        verify._verify_intent_prefix(
            reader,
            plan,
            bucket,
            plan["predecessor"]["certificate"]["frontier"],
            [0],
            set(reader.files),
        )


def test_prefix_machine_rejects_intent_after_unresolved_result() -> None:
    plan = _plan()
    bucket = plan["schedule"]["bootstrap"]["bucket"]
    basis = _bootstrap_bundle_reader(plan)
    first = verify.decode(basis.files[f"buckets/{bucket}/requests/00.json"])
    second = {**first, "ordinal": 1, "request_id": f"{bucket}-01"}
    paths = {
        f"buckets/{bucket}/requests/00.json": engine.canonical(first),
        f"buckets/{bucket}/requests/01.json": engine.canonical(second),
        f"clock/{bucket}-00.json": basis.files[f"clock/{bucket}-00.json"],
    }
    reader = _MemoryReader(paths)
    with pytest.raises(verify.VerificationError, match="intent_after_unresolved_request"):
        verify._verify_intent_prefix(
            reader,
            plan,
            bucket,
            plan["predecessor"]["certificate"]["frontier"],
            [0, 1],
            set(paths),
        )


def test_prefix_machine_rejects_stale_clock_on_unsealed_intent() -> None:
    plan = _plan()
    bucket = plan["schedule"]["bootstrap"]["bucket"]
    basis = _bootstrap_bundle_reader(plan)
    request = verify.decode(basis.files[f"buckets/{bucket}/requests/00.json"])
    clock = verify.decode(basis.files[f"clock/{bucket}-00.json"])
    stale_by = 301_000_000_000
    for sample in clock["samples"]:
        sample["sent_ns"] -= stale_by
        sample["received_ns"] -= stale_by
        sample["monotonic_sent_ns"] -= stale_by
        sample["monotonic_received_ns"] -= stale_by
        old_date = parsedate_to_datetime(sample["date"])
        sample["date"] = formatdate(int(old_date.timestamp()) - 301, usegmt=True)
    clock["checked_ns"] -= stale_by
    clock["monotonic_ns"] -= stale_by
    clock_raw = engine.canonical(clock)
    request["clock_check_sha256"] = engine.sha(clock_raw)
    paths = {
        f"buckets/{bucket}/requests/00.json": engine.canonical(request),
        f"clock/{bucket}-00.json": clock_raw,
    }
    reader = _MemoryReader(paths)
    with pytest.raises(verify.VerificationError, match="prefix_clock_progress"):
        verify._verify_intent_prefix(
            reader,
            plan,
            bucket,
            plan["predecessor"]["certificate"]["frontier"],
            [0],
            set(paths),
        )


def test_v2_descriptor_reader_supports_lease_manifest_and_rejects_over_cap(
    tmp_path: Any,
) -> None:
    from tools.brain_senses_external_verify_v2 import DescriptorReaderV2

    root = tmp_path / "reader-root"
    root.mkdir(mode=0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index in range(1100):
            path = root / f"f{index:04d}"
            path.write_bytes(b"x")
            path.chmod(0o600)
        with DescriptorReaderV2(root_fd) as reader:
            assert len(reader.list_files()) == 1100
        for index in range(1100, DescriptorReaderV2.MAX_FILES):
            path = root / f"f{index:04d}"
            path.write_bytes(b"x")
            path.chmod(0o600)
        extra = root / f"f{DescriptorReaderV2.MAX_FILES:04d}"
        extra.write_bytes(b"x")
        extra.chmod(0o600)
        with DescriptorReaderV2(root_fd) as reader:
            with pytest.raises(ValueError, match="tree_too_large"):
                reader.list_files()
    finally:
        os.close(root_fd)


def test_v2_descriptor_reader_rejects_symlink(tmp_path: Any) -> None:
    from tools.brain_senses_external_verify_v2 import DescriptorReaderV2

    root = tmp_path / "reader-root"
    root.mkdir(mode=0o700)
    (root / "link").symlink_to("missing-target")
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with DescriptorReaderV2(root_fd) as reader:
            with pytest.raises(ValueError, match="unsafe_file"):
                reader.list_files()
    finally:
        os.close(root_fd)


def test_v2_descriptor_reader_rejects_excessive_depth(tmp_path: Any) -> None:
    from tools.brain_senses_external_verify_v2 import DescriptorReaderV2

    root = tmp_path / "reader-root"
    root.mkdir(mode=0o700)
    current = root
    for _ in range(9):
        current = current / "d"
        current.mkdir(mode=0o700)
    leaf = current / "file"
    leaf.write_bytes(b"x")
    leaf.chmod(0o600)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with DescriptorReaderV2(root_fd) as reader:
            with pytest.raises(ValueError, match="tree_too_deep"):
                reader.list_files()
    finally:
        os.close(root_fd)


def test_independent_verifier_rejects_mutated_capture_bytes() -> None:
    plan = _plan()
    bucket = plan["schedule"]["bootstrap"]["bucket"]
    reader = _bootstrap_bundle_reader(plan)
    reader.files[f"buckets/{bucket}/captures/00/response.json"] += b" "
    with pytest.raises(verify.VerificationError, match="capture_digest"):
        verify._verify_bucket(
            reader,
            plan,
            bucket,
            plan["predecessor"]["certificate"]["frontier"],
        )


def _rewrite_event_chain(reader: _MemoryReader, index: int, **changes: object) -> None:
    events = verify.verify_events(reader.files[schema.EVENTS_FILE])
    events[index].update(changes)
    previous = "0" * 64
    encoded = []
    for seq, event in enumerate(events):
        body = {key: value for key, value in event.items() if key != "event_sha256"}
        body["seq"] = seq
        body["previous_sha256"] = previous
        event = {**body, "event_sha256": engine.sha(engine.canonical(body))}
        encoded.append(engine.canonical(event))
        previous = event["event_sha256"]
    reader.files[schema.EVENTS_FILE] = b"".join(encoded)


@pytest.mark.parametrize(
    ("event_index", "delta_ns", "reason"),
    [
        (4, 2, "bundle_event_availability_binding"),
        (5, 3_500_000_000, "seal_event_time_binding"),
    ],
)
def test_independent_verifier_rejects_rehashed_publication_order_tampering(
    event_index: int, delta_ns: int, reason: str
) -> None:
    plan = _plan()
    bucket = plan["schedule"]["bootstrap"]["bucket"]
    reader = _bootstrap_bundle_reader(plan)
    event_ns = plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000 + delta_ns
    events = verify.verify_events(reader.files[schema.EVENTS_FILE])
    if event_index == 5:
        seal_path = f"buckets/{bucket}/seal.json"
        seal = verify.decode(reader.files[seal_path])
        seal["sealed_ns"] = event_ns
        seal_raw = engine.canonical(seal)
        reader.files[seal_path] = seal_raw
        event_payload = {"seal_sha256": engine.sha(seal_raw)}
        events[5]["payload"] = event_payload
    _rewrite_event_chain(reader, event_index, at_ns=event_ns)
    with pytest.raises(verify.VerificationError, match=reason):
        verify._verify_bucket(
            reader,
            plan,
            bucket,
            plan["predecessor"]["certificate"]["frontier"],
        )
