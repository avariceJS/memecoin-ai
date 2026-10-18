from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from tools import brain_senses_alchemy_corroboration_policy_v1 as policy
from tools import brain_senses_alchemy_corroboration_schema_v1 as schema
from tools import brain_senses_alchemy_corroboration_verify_v1 as verify

HOUR = 1_800_000_000_000_000
HELIUS_AVAILABLE = HOUR + 8 * policy.MINUTE_NS
VERIFIED = HOUR + 9 * policy.MINUTE_NS
WINDOW_START = HOUR + 10 * policy.MINUTE_NS
SERVICE_START = HOUR - policy.HOUR_NS
FREE_BYTES = 300_000_000_000
HELIUS_REMAINING = 150_000_000_000
ROOT_ID = "a" * 32
VOLUME_UUID = "12345678-1234-1234-1234-123456789abc"


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _helius_response(signatures: list[str]) -> bytes:
    return schema.canonical({"jsonrpc": "2.0", "id": 1, "result": {"signatures": signatures}})


def _helius_receipt(request_id: str, response: bytes) -> bytes:
    return schema.canonical(
        {
            "schema": "brain-senses-helius-v4-request-receipt-v1",
            "request_id": request_id,
            "response_sha256": schema.sha(response),
        }
    )


def _projection(
    bucket_start_ns: int = HOUR, selected_count: int = 16
) -> tuple[dict, dict[str, list[str]]]:
    bucket = datetime.fromtimestamp(bucket_start_ns // policy.SECOND_NS, UTC).strftime(
        "%Y%m%dT%H0000Z"
    )
    frame_index = (bucket_start_ns - HOUR) // policy.HOUR_NS
    epoch_anchor = HOUR - policy.HOUR_NS
    epoch_bucket = datetime.fromtimestamp(epoch_anchor // policy.SECOND_NS, UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    inventory = list(range(100, 120))
    selected = list(range(100, 100 + selected_count))
    signatures = {str(slot): [f"sig-{slot}-a", f"sig-{slot}-b"] for slot in selected}
    inventory_response_sha = _digest(b"helius-getBlocks-response")
    captures = [
        {
            "request_id": "helius-getslot",
            "method": "getSlot",
            "response_sha256": _digest(b"getslot response"),
            "receipt_sha256": _digest(b"getslot receipt"),
        },
        {
            "request_id": "helius-getblocks",
            "method": "getBlocks",
            "response_sha256": inventory_response_sha,
            "receipt_sha256": _digest(b"getblocks receipt"),
        },
    ]
    signature_captures = []
    for slot in selected:
        for suffix in ("signatures", "full"):
            request_id = f"helius-{slot}-{suffix}"
            source_response = (
                _helius_response(signatures[str(slot)])
                if suffix == "signatures"
                else schema.canonical({"jsonrpc": "2.0", "id": 1, "result": {}})
            )
            captures.append(
                {
                    "request_id": request_id,
                    "method": "getBlock",
                    "response_sha256": schema.sha(source_response),
                    "receipt_sha256": schema.sha(_helius_receipt(request_id, source_response)),
                }
            )
            if suffix == "signatures":
                signature_captures.append(
                    {
                        "slot": slot,
                        "request_id": request_id,
                        "signatures_sha256": schema.sha(schema.canonical(signatures[str(slot)])),
                        "signature_count": len(signatures[str(slot)]),
                    }
                )
    projection = {
        "schema": schema.FRAME_SCHEMA,
        "schema_version": 2,
        "helius_service_id": "external-rolling-v4",
        "epoch_id": f"{epoch_bucket}-0123456789ab",
        "bucket": bucket,
        "frame_index": frame_index,
        "seal_event_sha256": _digest(b"helius frame sealed event"),
        "interval_start_slot": 100,
        "interval_end_slot": 119,
        "inventory_slots": inventory,
        "inventory_response_sha256": inventory_response_sha,
        "selection_artifact_sha256": _digest(b"canonical Helius selection bytes"),
        "selected_slots": selected,
        "request_captures": captures,
        "helius_signature_captures": signature_captures,
    }
    return projection, signatures


def _append(events: list[dict], kind: str, at_ns: int, fields: dict) -> dict:
    previous = events[-1]["event_sha256"] if events else None
    event = schema.event_record(
        seq=len(events), kind=kind, at_ns=at_ns, previous_sha256=previous, fields=fields
    )
    events.append(event)
    return event


def _pin(bucket_start_ns: int = HOUR) -> dict:
    projection, _signatures = _projection(bucket_start_ns)
    return schema.build_pin(
        projection_bytes=schema.canonical(projection),
        helius_available_ns=bucket_start_ns + 8 * policy.MINUTE_NS,
        verified_ns=bucket_start_ns + 9 * policy.MINUTE_NS,
    )


def _capture_frame(
    events: list[dict], bucket_start_ns: int = HOUR, *, mutate: dict[int, dict] | None = None
) -> dict[str, bytes]:
    projection, helius_signatures = _projection(bucket_start_ns)
    pin = schema.build_pin(
        projection_bytes=schema.canonical(projection),
        helius_available_ns=bucket_start_ns + 8 * policy.MINUTE_NS,
        verified_ns=bucket_start_ns + 9 * policy.MINUTE_NS,
    )
    _append(
        events,
        "frame_admitted",
        pin["verified_ns"],
        {"frame_id": pin["frame_id"], "pin": pin, "pin_sha256": pin["pin_sha256"]},
    )
    calls = [
        (
            "getBlocks",
            [100, 119, {"commitment": "finalized"}],
        )
    ]
    calls.extend(
        (
            "getBlock",
            [
                slot,
                {
                    "commitment": "finalized",
                    "encoding": "json",
                    "transactionDetails": "signatures",
                    "rewards": False,
                },
            ],
        )
        for slot in projection["selected_slots"]
    )
    raw_responses: dict[str, bytes] = {}
    request_ids: list[str] = []
    for ordinal, (method, params) in enumerate(calls):
        decision = (
            bucket_start_ns
            + policy.SEAL_DEADLINE_OFFSET_NS
            + ordinal * policy.MIN_REQUEST_SPACING_NS
        )
        request_id = f"{pin['frame_id']}-{ordinal:02d}"
        wire = schema.canonical(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).rstrip(b"\n")
        _append(
            events,
            "request_intent",
            decision,
            {
                "frame_id": pin["frame_id"],
                "pin_sha256": pin["pin_sha256"],
                "ordinal": ordinal,
                "request_id": request_id,
                "method": method,
                "params": params,
                "decision_ns": decision,
                "request_sha256": schema.sha(wire),
            },
        )
        result = (
            list(projection["inventory_slots"])
            if ordinal == 0
            else {"signatures": helius_signatures[str(params[0])]}
        )
        if mutate and ordinal in mutate:
            result = mutate[ordinal]
        raw = schema.canonical({"jsonrpc": "2.0", "id": 1, "result": result})
        raw_responses[request_id] = raw
        received = decision + 100_000_000
        available = received + 100_000_000
        _append(
            events,
            "request_captured",
            available + 1,
            {
                "request_id": request_id,
                "response_sha256": schema.sha(raw),
                "response_bytes": len(raw),
                "request_started_ns": decision + 1,
                "received_ns": received,
                "availability_ns": available,
                "provider_event_ns": None,
            },
        )
        request_ids.append(request_id)
    _append(
        events,
        "frame_completed",
        events[-1]["at_ns"] + 1,
        {
            "frame_id": pin["frame_id"],
            "pin_sha256": pin["pin_sha256"],
            "request_ids": request_ids,
        },
    )
    return raw_responses


def _replay(
    events: list[dict],
    raw: dict[str, bytes],
    *,
    source_fault: str | None = None,
    source_opened: list[int] | None = None,
) -> dict:
    projection, signatures = _projection()
    if events:
        admitted = next((row for row in events if row["kind"] == "frame_admitted"), None)
        if admitted:
            projection = admitted["fields"]["pin"]["projection"]
            signatures = {
                str(slot): [f"sig-{slot}-a", f"sig-{slot}-b"]
                for slot in projection["selected_slots"]
            }

    def reopen_helius_signature_capture(
        *, epoch_id: str, bucket: str, seal_event_sha256: str, slot: int, request_id: str
    ) -> bytes:
        assert epoch_id == projection["epoch_id"]
        assert bucket == projection["bucket"]
        assert seal_event_sha256 == projection["seal_event_sha256"]
        row = next(item for item in projection["helius_signature_captures"] if item["slot"] == slot)
        assert row["request_id"] == request_id
        if source_opened is not None:
            source_opened.append(slot)
        if source_fault == "missing":
            raise FileNotFoundError(request_id)
        response = _helius_response(signatures[str(slot)])
        if source_fault == "drift" and slot == projection["selected_slots"][0]:
            response += b" "
        receipt = _helius_receipt(request_id, response)
        request_capture = next(
            item for item in projection["request_captures"] if item["request_id"] == request_id
        )
        if source_fault != "drift":
            assert schema.sha(response) == request_capture["response_sha256"]
            assert schema.sha(receipt) == request_capture["receipt_sha256"]
        return response

    return verify.verify_replay(
        event_bytes=b"".join(schema.canonical(event) for event in events),
        raw_response_reopener=lambda request_id: raw[request_id],
        helius_source_reopener=reopen_helius_signature_capture,
        service_start_ns=SERVICE_START,
        helius_remaining_lifetime_bytes=HELIUS_REMAINING,
        fresh_free_bytes_at_activation=FREE_BYTES,
    )


def test_window_is_fixed_and_late_seals_are_unobserved() -> None:
    assert (
        policy.disposition(
            bucket_start_ns=HOUR,
            now_ns=WINDOW_START,
            helius_verified_ns=HOUR + 10 * policy.MINUTE_NS,
        )
        == "eligible"
    )
    assert (
        policy.disposition(
            bucket_start_ns=HOUR,
            now_ns=WINDOW_START,
            helius_verified_ns=HOUR + 10 * policy.MINUTE_NS + 1,
        )
        == "unobserved_late_seal"
    )
    assert (
        policy.disposition(
            bucket_start_ns=HOUR,
            now_ns=HOUR + 20 * policy.MINUTE_NS,
            helius_verified_ns=VERIFIED,
        )
        == "unobserved_expired_window"
    )


def test_projection_uses_exact_canonical_bytes_and_pins_all_helius_selected_slots() -> None:
    projection, signatures = _projection()
    raw = schema.canonical(projection)
    pin = schema.build_pin(
        projection_bytes=raw,
        helius_available_ns=HELIUS_AVAILABLE,
        verified_ns=VERIFIED,
    )
    assert pin["projection_sha256"] == schema.sha(raw)
    assert pin["projection"] == projection
    assert len(pin["projection"]["selected_slots"]) == 16
    assert schema.canonical(projection) == raw
    mutated = dict(projection)
    mutated["selected_slots"] = list(reversed(projection["selected_slots"]))
    with pytest.raises(schema.SchemaError, match="helius_projection_invalid"):
        schema.build_pin(
            projection_bytes=schema.canonical(mutated),
            helius_available_ns=HELIUS_AVAILABLE,
            verified_ns=VERIFIED,
        )
    with pytest.raises(schema.SchemaError, match="helius_projection_not_canonical"):
        schema.build_pin(
            projection_bytes=raw.replace(b",", b", ", 1),
            helius_available_ns=HELIUS_AVAILABLE,
            verified_ns=VERIFIED,
        )


@pytest.mark.parametrize("selected_count", [0, 1, 16])
def test_projection_captures_follow_v4_seal_order_for_selection_width(
    selected_count: int,
) -> None:
    projection, signatures = _projection(selected_count=selected_count)
    captures = projection["request_captures"]
    assert len(captures) == 2 + 2 * selected_count
    assert [capture["method"] for capture in captures] == [
        "getSlot",
        "getBlocks",
        *(["getBlock", "getBlock"] * selected_count),
    ]
    assert list(signatures) == [str(slot) for slot in projection["selected_slots"]]
    assert schema.validate_projection(projection) == projection


def test_projection_size_grows_only_with_bounded_selected_slot_references() -> None:
    sizes = [len(schema.canonical(_projection(selected_count=count)[0])) for count in (0, 1, 16)]
    assert sizes[0] < sizes[1] < sizes[2] < schema.MAX_PROJECTION_BYTES


def test_projection_signature_references_bind_exact_signatures_pair_position() -> None:
    projection, _ = _projection()
    changed = schema.decode(schema.canonical(projection))
    changed["helius_signature_captures"][0]["request_id"] = "helius-100-full"
    with pytest.raises(schema.SchemaError, match="signature_capture_invalid"):
        schema.validate_projection(changed)


def test_projection_pins_bounded_source_references_without_signature_arrays() -> None:
    projection, signatures = _projection()
    raw = schema.canonical(projection)
    pin = schema.build_pin(
        projection_bytes=raw,
        helius_available_ns=HELIUS_AVAILABLE,
        verified_ns=VERIFIED,
    )
    assert len(raw) < schema.MAX_PROJECTION_BYTES
    assert len(pin["projection"]["helius_signature_captures"]) == 16
    assert "helius_signatures" not in pin
    assert all(
        "signatures" not in source for source in pin["projection"]["helius_signature_captures"]
    )
    assert len(signatures) == 16


def test_oversized_projection_is_permanently_unobserved_before_pin() -> None:
    projection, _ = _projection()
    projection["request_captures"][0]["request_id"] = "x" * schema.MAX_PROJECTION_BYTES
    oversized = schema.canonical(projection)
    with pytest.raises(schema.SchemaError, match="oversized_unobserved"):
        schema.build_pin(
            projection_bytes=oversized,
            helius_available_ns=HELIUS_AVAILABLE,
            verified_ns=VERIFIED,
        )


def test_complete_inventory_and_all_signature_sentinels_replay_as_diagnostics() -> None:
    events: list[dict] = []
    raw = _capture_frame(events)
    result = _replay(events, raw)
    assert result["request_intents"] == result["captured_requests"] == 17
    assert result["activation_eligible"] is True
    diagnostic = result["frame_diagnostics"][_pin()["frame_id"]]
    assert diagnostic["inventory"]["status"] == "match"
    assert len(diagnostic["selected_slot_signatures"]) == 16
    assert all(row["status"] == "match" for row in diagnostic["selected_slot_signatures"])
    assert diagnostic["correction"] is False
    assert diagnostic["global_absence_claim"] is False


def test_source_reopens_one_slot_at_a_time_and_fails_closed_on_missing_or_drift() -> None:
    events: list[dict] = []
    raw = _capture_frame(events)
    opened: list[int] = []
    result = _replay(events, raw, source_opened=opened)
    selected = _pin()["projection"]["selected_slots"]
    assert opened == [*selected, *selected]
    assert result["captured_requests"] == 17
    with pytest.raises(verify.VerifyError, match="source_capture_unavailable"):
        _replay(events, raw, source_fault="missing")
    with pytest.raises(verify.VerifyError, match="source_response_hash_mismatch"):
        _replay(events, raw, source_fault="drift")


def test_source_response_has_independent_bounded_single_capture_limit(monkeypatch) -> None:
    projection, signatures = _projection(selected_count=1)
    source_capture = projection["helius_signature_captures"][0]
    monkeypatch.setattr(verify, "MAX_HELIUS_SIGNATURE_RESPONSE_BYTES", 10)
    with pytest.raises(verify.VerifyError, match="source_response_oversized"):
        verify._reopen_helius_signatures(
            projection=projection,
            source_capture=source_capture,
            reopen=lambda **_: _helius_response(signatures["100"]),
        )


def test_source_failure_preflight_prevents_frame_admission_or_intent() -> None:
    projection, _ = _projection()
    events: list[dict] = []
    with pytest.raises(verify.VerifyError, match="source_capture_unavailable"):
        verify.verify_source_projection(
            projection=projection,
            reopen=lambda **_: (_ for _ in ()).throw(FileNotFoundError()),
        )
    assert events == []


def test_gap_never_replays_but_a_future_bucket_is_independently_eligible() -> None:
    events: list[dict] = []
    _append(
        events,
        "frame_unobserved",
        HOUR + 10 * policy.MINUTE_NS,
        {"bucket_start_ns": HOUR, "reason": "seal_not_verified_by_deadline"},
    )
    next_hour = HOUR + policy.HOUR_NS
    raw = _capture_frame(events, next_hour)
    result = _replay(events, raw)
    assert result["unobserved_bucket_count"] == 1
    assert result["request_intents"] == 17
    assert _pin(HOUR)["frame_id"] not in result["frame_diagnostics"]
    old_pin = _pin(HOUR)
    _append(
        events,
        "frame_admitted",
        events[-1]["at_ns"] + 1,
        {"frame_id": old_pin["frame_id"], "pin": old_pin, "pin_sha256": old_pin["pin_sha256"]},
    )
    with pytest.raises(verify.VerifyError, match="admission_binding_or_window_invalid"):
        _replay(events, raw)


def test_inventory_and_ordered_signature_mutations_remain_diagnostic() -> None:
    events: list[dict] = []
    mutations = {
        0: list(range(100, 119)) + [999],
        1: {"signatures": ["changed-order", "sig-100-b"]},
    }
    raw = _capture_frame(events, mutate=mutations)
    diagnostic = _replay(events, raw)["frame_diagnostics"][_pin()["frame_id"]]
    assert diagnostic["inventory"]["status"] == "membership_difference"
    assert diagnostic["inventory"]["helius_only_slots"] == [119]
    assert diagnostic["inventory"]["alchemy_only_slots"] == [999]
    assert diagnostic["selected_slot_signatures"][0]["status"] == "ordered_signature_difference"
    assert diagnostic["correction"] is False
    assert diagnostic["global_absence_claim"] is False


def test_replayed_intent_and_helius_availability_mutation_fail_closed() -> None:
    events: list[dict] = []
    raw = _capture_frame(events)
    first_intent = next(event for event in events if event["kind"] == "request_intent")
    _append(events, "request_intent", events[-1]["at_ns"] + 1, dict(first_intent["fields"]))
    with pytest.raises(verify.VerifyError):
        _replay(events, raw)

    events = []
    raw = _capture_frame(events)
    capture = next(event for event in events if event["kind"] == "request_captured")
    fields = dict(capture["fields"])
    fields["availability_ns"] = HELIUS_AVAILABLE
    events[capture["seq"]] = schema.event_record(
        seq=capture["seq"],
        kind=capture["kind"],
        at_ns=capture["at_ns"],
        previous_sha256=capture["previous_sha256"],
        fields=fields,
    )
    for index in range(capture["seq"] + 1, len(events)):
        old = events[index]
        events[index] = schema.event_record(
            seq=index,
            kind=old["kind"],
            at_ns=old["at_ns"],
            previous_sha256=events[index - 1]["event_sha256"],
            fields=old["fields"],
        )
    with pytest.raises(verify.VerifyError, match="capture_chronology_or_hash_invalid"):
        _replay(events, raw)


def test_activation_and_per_intent_storage_capacity_fail_closed() -> None:
    assert policy.MAX_LIFETIME_ALLOCATION_BYTES == 256_000_000
    fixed_reserves = (
        HELIUS_REMAINING
        + policy.HELIUS_DISK_FLOOR_BYTES
        + policy.OPERATING_RESERVE_BYTES
        + policy.HELIUS_NEXT_EPOCH_BYTES
        + policy.HELIUS_PEAK_METADATA_BYTES
        + policy.ALCHEMY_PEAK_RESERVE_BYTES
        + policy.ALCHEMY_METADATA_RESERVE_BYTES
    )
    assert (
        policy.activation_allocation_cap(
            fresh_free_bytes=fixed_reserves + 600_000_000,
            helius_remaining_lifetime_bytes=HELIUS_REMAINING,
        )
        == policy.MAX_LIFETIME_ALLOCATION_BYTES
    )
    assert (
        policy.activation_allocation_cap(
            fresh_free_bytes=fixed_reserves + policy.MIN_ACTIVATION_ALLOCATION_BYTES - 1,
            helius_remaining_lifetime_bytes=HELIUS_REMAINING,
        )
        == 0
    )
    with pytest.raises(verify.VerifyError, match="activation_capacity_ineligible"):
        verify.verify_replay(
            event_bytes=b"",
            raw_response_reopener=lambda _request_id: b"",
            helius_source_reopener=lambda **_: b"",
            service_start_ns=SERVICE_START,
            helius_remaining_lifetime_bytes=HELIUS_REMAINING,
            fresh_free_bytes_at_activation=fixed_reserves + 100,
        )

    cap = policy.MAX_LIFETIME_ALLOCATION_BYTES
    reserve = policy.MAX_INVENTORY_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES
    protected = fixed_reserves + cap
    args = {
        "fresh_free_bytes": protected + reserve,
        "expected_root_id": ROOT_ID,
        "observed_root_id": ROOT_ID,
        "expected_volume_uuid": VOLUME_UUID,
        "observed_volume_uuid": VOLUME_UUID.upper(),
        "helius_remaining_lifetime_bytes": HELIUS_REMAINING,
        "sidecar_lifetime_used_bytes": 0,
        "sidecar_lifetime_cap_bytes": cap,
        "sidecar_epoch_physical_bytes": 0,
        "frame_raw_bytes": 0,
        "request_method": "getBlocks",
    }
    assert policy.request_storage_admissible(**args)
    assert not policy.request_storage_admissible(**{**args, "observed_root_id": "b" * 32})
    assert not policy.request_storage_admissible(
        **{**args, "fresh_free_bytes": protected + reserve - 1}
    )
    assert policy.response_reservation_bytes("getBlock") == policy.MAX_SIGNATURE_RESPONSE_BYTES
    sig_reserve = policy.MAX_SIGNATURE_RESPONSE_BYTES + policy.PER_REQUEST_METADATA_RESERVE_BYTES
    assert not policy.request_storage_admissible(
        **{
            **args,
            "request_method": "getBlock",
            "frame_raw_bytes": policy.MAX_FRAME_RAW_BYTES - policy.MAX_SIGNATURE_RESPONSE_BYTES + 1,
        }
    )
    assert not policy.request_storage_admissible(
        **{
            **args,
            "request_method": "getBlock",
            "sidecar_epoch_physical_bytes": policy.MAX_EPOCH_PHYSICAL_BYTES - sig_reserve + 1,
        }
    )
    assert not policy.request_storage_admissible(
        **{
            **args,
            "request_method": "getBlock",
            "sidecar_lifetime_used_bytes": cap - sig_reserve + 1,
        }
    )
