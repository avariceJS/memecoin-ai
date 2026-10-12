#!/usr/bin/env python3
"""Independent offline replay for Alchemy Free same-frame sentinel evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from tools import brain_senses_alchemy_corroboration_policy_v1 as policy
from tools import brain_senses_alchemy_corroboration_schema_v1 as schema


class VerifyError(ValueError):
    """Invalid sidecar chronology, request, capture, or correspondence."""


SourceReopener = Callable[..., bytes]
ResponseReopener = Callable[[str], bytes]
MAX_HELIUS_SIGNATURE_RESPONSE_BYTES = 32_000_000


def _verify_pin(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != schema.PIN_SCHEMA:
        raise VerifyError("helius_pin_invalid")
    try:
        projection_bytes = schema.canonical(value["projection"])
        expected = schema.build_pin(
            projection_bytes=projection_bytes,
            helius_available_ns=value["helius_available_ns"],
            verified_ns=value["verified_ns"],
        )
    except schema.SchemaError as exc:
        raise VerifyError("helius_pin_invalid") from exc
    if value != expected:
        raise VerifyError("helius_pin_binding_invalid")
    return expected


def _expected_calls(pin: Mapping[str, Any]) -> list[tuple[str, list[Any]]]:
    projection = pin["projection"]
    calls: list[tuple[str, list[Any]]] = [
        (
            "getBlocks",
            [
                projection["interval_start_slot"],
                projection["interval_end_slot"],
                {"commitment": "finalized"},
            ],
        )
    ]
    for slot in projection["selected_slots"]:
        calls.append(
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
        )
    return calls


def _response_envelope(raw: bytes) -> dict[str, Any]:
    try:
        value = schema.decode(raw)
    except schema.SchemaError as exc:
        raise VerifyError("response_json_invalid") from exc
    if (
        not isinstance(value, dict)
        or value.get("jsonrpc") != "2.0"
        or value.get("id") != 1
        or ("result" in value) == ("error" in value)
        or ("error" in value and not isinstance(value["error"], dict))
    ):
        raise VerifyError("response_envelope_invalid")
    return value


def _alchemy_signatures(result: Any) -> list[str] | None:
    if not isinstance(result, dict):
        return None
    signatures = result.get("signatures")
    if not isinstance(signatures, list) or any(not isinstance(item, str) for item in signatures):
        return None
    return signatures


def _reopen_helius_signatures(
    *, projection: Mapping[str, Any], source_capture: Mapping[str, Any], reopen: SourceReopener
) -> list[str]:
    slot, request_id = source_capture["slot"], source_capture["request_id"]
    try:
        raw = reopen(
            epoch_id=projection["epoch_id"],
            bucket=projection["bucket"],
            seal_event_sha256=projection["seal_event_sha256"],
            slot=slot,
            request_id=request_id,
        )
    except VerifyError:
        raise
    except Exception as exc:
        raise VerifyError("helius_source_capture_unavailable_or_drifted") from exc
    capture_index = 2 + projection["selected_slots"].index(slot) * 2
    capture = projection["request_captures"][capture_index]
    if isinstance(raw, bytes) and len(raw) > MAX_HELIUS_SIGNATURE_RESPONSE_BYTES:
        raise VerifyError("helius_source_response_oversized")
    if not isinstance(raw, bytes) or schema.sha(raw) != capture["response_sha256"]:
        raise VerifyError("helius_source_response_hash_mismatch")
    envelope = _response_envelope(raw)
    if "error" in envelope:
        raise VerifyError("helius_source_response_error")
    signatures = _alchemy_signatures(envelope.get("result"))
    if (
        signatures is None
        or len(signatures) != source_capture["signature_count"]
        or schema.sha(schema.canonical(signatures)) != source_capture["signatures_sha256"]
    ):
        raise VerifyError("helius_source_signature_projection_mismatch")
    return signatures


def verify_source_projection(*, projection: Mapping[str, Any], reopen: SourceReopener) -> None:
    """Preflight each immutable source capture before a caller admits or intents a frame."""
    try:
        schema.validate_projection(dict(projection))
    except schema.SchemaError as exc:
        raise VerifyError("helius_projection_invalid") from exc
    for source_capture in projection["helius_signature_captures"]:
        _reopen_helius_signatures(
            projection=projection, source_capture=source_capture, reopen=reopen
        )


def _diagnostics(
    *,
    pin: Mapping[str, Any],
    calls: list[tuple[str, list[Any]]],
    request_ids: list[str],
    captures: Mapping[str, Mapping[str, Any]],
    reopen_response: ResponseReopener,
    reopen: SourceReopener,
) -> dict[str, Any]:
    def response_at(index: int) -> dict[str, Any]:
        request_id = request_ids[index]
        raw = reopen_response(request_id)
        capture_fields = captures[request_id]
        if (
            not isinstance(raw, bytes)
            or schema.sha(raw) != capture_fields["response_sha256"]
            or len(raw) != capture_fields["response_bytes"]
        ):
            raise VerifyError("capture_raw_response_drifted")
        return _response_envelope(raw)

    inventory_response = response_at(0)
    inventory_result = inventory_response.get("result") if "result" in inventory_response else None
    if (
        not isinstance(inventory_result, list)
        or any(type(slot) is not int for slot in inventory_result)
        or inventory_result != sorted(set(inventory_result))
    ):
        inventory_diag: dict[str, Any] = {"status": "unknown_invalid_or_unavailable"}
    else:
        helius_inventory = pin["projection"]["inventory_slots"]
        inventory_diag = {
            "status": "match" if inventory_result == helius_inventory else "membership_difference",
            "helius_only_slots": sorted(set(helius_inventory) - set(inventory_result)),
            "alchemy_only_slots": sorted(set(inventory_result) - set(helius_inventory)),
        }
    del inventory_response
    signature_diags: list[dict[str, Any]] = []
    projection = pin["projection"]
    for index, source_capture in enumerate(projection["helius_signature_captures"], start=1):
        slot = source_capture["slot"]
        response = response_at(index)
        observed = _alchemy_signatures(response.get("result")) if "result" in response else None
        expected = _reopen_helius_signatures(
            projection=projection, source_capture=source_capture, reopen=reopen
        )
        signature_diags.append(
            {
                "slot": slot,
                "status": (
                    "unknown_invalid_or_unavailable"
                    if observed is None
                    else "match"
                    if observed == expected
                    else "ordered_signature_difference"
                ),
                "helius_signature_count": source_capture["signature_count"],
                "alchemy_signature_count": None if observed is None else len(observed),
            }
        )
        del expected, observed, response
    return {
        "scope": {
            "helius_seal_event_sha256": pin["projection"]["seal_event_sha256"],
            "helius_projection_sha256": pin["projection_sha256"],
            "interval_start_slot": pin["projection"]["interval_start_slot"],
            "interval_end_slot": pin["projection"]["interval_end_slot"],
        },
        "inventory": inventory_diag,
        "selected_slot_signatures": signature_diags,
        "correction": False,
        "global_absence_claim": False,
    }


def verify_replay(
    *,
    event_bytes: bytes,
    raw_response_reopener: ResponseReopener,
    helius_source_reopener: SourceReopener,
    service_start_ns: int,
    helius_remaining_lifetime_bytes: int,
    fresh_free_bytes_at_activation: int,
) -> dict[str, Any]:
    """Verify a complete offline sidecar journal and return diagnostics only."""
    if (
        type(service_start_ns) is not int
        or service_start_ns <= 0
        or type(helius_remaining_lifetime_bytes) is not int
        or helius_remaining_lifetime_bytes < 0
        or type(fresh_free_bytes_at_activation) is not int
        or fresh_free_bytes_at_activation < 0
    ):
        raise VerifyError("replay_binding_invalid")
    cap = policy.activation_allocation_cap(
        fresh_free_bytes=fresh_free_bytes_at_activation,
        helius_remaining_lifetime_bytes=helius_remaining_lifetime_bytes,
    )
    if cap <= 0:
        raise VerifyError("activation_capacity_ineligible")
    try:
        events = schema.verify_event_chain(event_bytes)
    except schema.SchemaError as exc:
        raise VerifyError("event_chain_invalid") from exc

    pins: dict[str, dict[str, Any]] = {}
    states: dict[str, str] = {}
    intents: dict[str, dict[str, Any]] = {}
    captured: dict[str, dict[str, Any]] = {}
    frame_requests: dict[str, list[str]] = {}
    terminal_frames: set[str] = set()
    bucket_ids: set[int] = set()
    all_intent_times: list[int] = []
    frame_raw_bytes: dict[str, int] = {}
    diagnostics: dict[str, dict[str, Any]] = {}

    for event in events:
        kind, fields, at_ns = event["kind"], event["fields"], event["at_ns"]
        if kind == "frame_admitted":
            if set(fields) != {"frame_id", "pin", "pin_sha256"}:
                raise VerifyError("admission_fields_invalid")
            pin = _verify_pin(fields["pin"])
            frame_id = pin["frame_id"]
            window = policy.frame_window(pin["bucket_start_ns"])
            if (
                frame_id in pins
                or pin["bucket_start_ns"] < service_start_ns
                or pin["verified_ns"] > window.seal_deadline_ns
                or at_ns < pin["verified_ns"]
                or at_ns >= window.end_ns
                or fields["frame_id"] != frame_id
                or fields["pin_sha256"] != pin["pin_sha256"]
                or pin["bucket_start_ns"] in bucket_ids
            ):
                raise VerifyError("admission_binding_or_window_invalid")
            verify_source_projection(projection=pin["projection"], reopen=helius_source_reopener)
            pins[frame_id] = pin
            states[frame_id] = "admitted"
            frame_requests[frame_id] = []
            frame_raw_bytes[frame_id] = 0
            bucket_ids.add(pin["bucket_start_ns"])
        elif kind == "frame_unobserved":
            if set(fields) not in (
                {"bucket_start_ns", "reason"},
                {"bucket_start_ns", "reason", "frame_id", "pin_sha256"},
            ):
                raise VerifyError("unobserved_fields_invalid")
            bucket = fields["bucket_start_ns"]
            if type(bucket) is not int or bucket < service_start_ns:
                raise VerifyError("unobserved_bucket_invalid")
            window = policy.frame_window(bucket)
            reason = fields["reason"]
            if "frame_id" in fields:
                frame_id = fields["frame_id"]
                unobserved_pin = pins.get(frame_id)
                if (
                    unobserved_pin is None
                    or frame_id in terminal_frames
                    or states.get(frame_id) != "admitted"
                    or unobserved_pin["bucket_start_ns"] != bucket
                    or fields["pin_sha256"] != unobserved_pin["pin_sha256"]
                    or frame_requests[frame_id]
                    or reason
                    not in {
                        "dispatch_window_expired",
                        "storage_capacity_pause",
                        "request_budget_pause",
                    }
                    or (reason == "dispatch_window_expired" and at_ns < window.end_ns)
                ):
                    raise VerifyError("admitted_frame_unobserved_binding_invalid")
                states[frame_id] = "unobserved"
                terminal_frames.add(frame_id)
            else:
                if bucket in bucket_ids:
                    raise VerifyError("unobserved_bucket_duplicate")
                if reason == "seal_not_verified_by_deadline":
                    if at_ns < window.seal_deadline_ns:
                        raise VerifyError("late_seal_gap_time_invalid")
                elif reason == "dispatch_window_expired":
                    if at_ns < window.end_ns:
                        raise VerifyError("expired_window_gap_time_invalid")
                else:
                    raise VerifyError("unobserved_reason_invalid")
            bucket_ids.add(bucket)
        elif kind == "request_intent":
            required = {
                "frame_id",
                "pin_sha256",
                "ordinal",
                "request_id",
                "method",
                "params",
                "decision_ns",
                "request_sha256",
            }
            if set(fields) != required:
                raise VerifyError("intent_fields_invalid")
            frame_id = fields["frame_id"]
            known_pin = pins.get(frame_id)
            if (
                known_pin is None
                or states[frame_id] != "admitted"
                or known_pin.get("_faulted", False)
            ):
                raise VerifyError("intent_without_admission_or_after_terminal")
            window = policy.frame_window(known_pin["bucket_start_ns"])
            ordinal = len(frame_requests[frame_id])
            expected_calls = _expected_calls(known_pin)
            decision_ns = fields["decision_ns"]
            request_id = fields["request_id"]
            expected_method, expected_params = (
                expected_calls[ordinal] if ordinal < len(expected_calls) else (None, None)
            )
            wire = schema.canonical(
                {"jsonrpc": "2.0", "id": 1, "method": fields["method"], "params": fields["params"]}
            ).rstrip(b"\n")
            if (
                fields["pin_sha256"] != known_pin["pin_sha256"]
                or fields["ordinal"] != ordinal
                or (frame_requests[frame_id] and states[frame_requests[frame_id][-1]] != "captured")
                or request_id != f"{frame_id}-{ordinal:02d}"
                or request_id in intents
                or fields["method"] != expected_method
                or fields["params"] != expected_params
                or type(decision_ns) is not int
                or decision_ns != at_ns
                or not window.start_ns <= at_ns < window.end_ns
                or at_ns < known_pin["verified_ns"]
                or at_ns < known_pin["helius_available_ns"]
                or fields["request_sha256"] != schema.sha(wire)
                or (
                    all_intent_times
                    and at_ns - all_intent_times[-1] < policy.MIN_REQUEST_SPACING_NS
                )
            ):
                raise VerifyError("intent_binding_or_window_invalid")
            intent = dict(fields)
            intents[request_id] = intent
            states[request_id] = "possibly_spent"
            frame_requests[frame_id].append(request_id)
            all_intent_times.append(at_ns)
        elif kind == "request_captured":
            required = {
                "request_id",
                "response_sha256",
                "response_bytes",
                "request_started_ns",
                "received_ns",
                "availability_ns",
                "provider_event_ns",
            }
            if set(fields) != required:
                raise VerifyError("capture_fields_invalid")
            request_id = fields["request_id"]
            captured_intent = intents.get(request_id)
            if (
                captured_intent is None
                or states.get(request_id) != "possibly_spent"
                or request_id in captured
            ):
                raise VerifyError("capture_without_unique_intent")
            frame_id = captured_intent["frame_id"]
            capture_pin = pins[frame_id]
            try:
                raw = raw_response_reopener(request_id)
            except Exception as exc:
                raise VerifyError("capture_raw_response_missing") from exc
            if not isinstance(raw, bytes):
                raise VerifyError("capture_raw_response_missing")
            start_ns, received_ns, available_ns = (
                fields["request_started_ns"],
                fields["received_ns"],
                fields["availability_ns"],
            )
            provider_event_ns = fields["provider_event_ns"]
            if (
                fields["response_sha256"] != schema.sha(raw)
                or fields["response_bytes"] != len(raw)
                or len(raw) > policy.response_reservation_bytes(intents[request_id]["method"])
                or type(start_ns) is not int
                or type(received_ns) is not int
                or type(available_ns) is not int
                or start_ns < captured_intent["decision_ns"]
                or received_ns < start_ns
                or available_ns < received_ns
                or available_ns <= capture_pin["helius_available_ns"]
                or at_ns < available_ns
                or (
                    provider_event_ns is not None
                    and (type(provider_event_ns) is not int or provider_event_ns <= 0)
                )
            ):
                raise VerifyError("capture_chronology_or_hash_invalid")
            envelope = _response_envelope(raw)
            if "error" in envelope:
                raise VerifyError("provider_error_must_be_failed_event")
            captured[request_id] = dict(fields)
            states[request_id] = "captured"
            frame_raw_bytes[frame_id] += len(raw)
            del envelope, raw
            if frame_raw_bytes[frame_id] > policy.MAX_FRAME_RAW_BYTES:
                raise VerifyError("frame_raw_byte_cap_exceeded")
        elif kind == "request_failed":
            if set(fields) != {"request_id", "failure_class", "certainty"}:
                raise VerifyError("failure_fields_invalid")
            request_id = fields["request_id"]
            if (
                states.get(request_id) != "possibly_spent"
                or fields["failure_class"]
                not in {
                    "provider_http",
                    "provider_quota",
                    "provider_transport",
                    "storage_or_clock",
                    "response_oversized",
                    "uncertain_outcome",
                }
                or fields["certainty"] != "possibly_spent"
            ):
                raise VerifyError("failure_binding_invalid")
            states[request_id] = "failed_possibly_spent"
            frame_id = intents[request_id]["frame_id"]
            pins[frame_id]["_faulted"] = True
        elif kind in {"frame_completed", "frame_stopped"}:
            expected_fields = (
                {"frame_id", "pin_sha256", "request_ids"}
                if kind == "frame_completed"
                else {"frame_id", "pin_sha256", "request_ids", "reason"}
            )
            if set(fields) != expected_fields:
                raise VerifyError("frame_terminal_fields_invalid")
            frame_id = fields["frame_id"]
            terminal_pin = pins.get(frame_id)
            request_ids = frame_requests.get(frame_id)
            if (
                terminal_pin is None
                or frame_id in terminal_frames
                or states[frame_id] != "admitted"
                or fields["pin_sha256"] != terminal_pin["pin_sha256"]
                or fields["request_ids"] != request_ids
                or not request_ids
                or terminal_pin.get("_faulted", False) != (kind == "frame_stopped")
            ):
                raise VerifyError("frame_terminal_binding_invalid")
            if kind == "frame_completed":
                if len(request_ids) != len(_expected_calls(terminal_pin)) or any(
                    states[item] != "captured" for item in request_ids
                ):
                    raise VerifyError("frame_completed_incomplete")
                diagnostics[frame_id] = _diagnostics(
                    pin=terminal_pin,
                    calls=_expected_calls(terminal_pin),
                    request_ids=request_ids,
                    captures=captured,
                    reopen_response=raw_response_reopener,
                    reopen=helius_source_reopener,
                )
            else:
                if fields["reason"] != "possibly_spent_or_provider_fault":
                    raise VerifyError("frame_stop_reason_invalid")
                if any(
                    states[item] not in {"captured", "failed_possibly_spent", "possibly_spent"}
                    for item in request_ids
                ):
                    raise VerifyError("frame_stopped_state_invalid")
            states[frame_id] = "terminal"
            terminal_frames.add(frame_id)

    # An unresolved intent remains spent and can never be replayed.
    for frame_id, _pin in pins.items():
        if frame_id not in terminal_frames:
            pending = frame_requests[frame_id]
            if not pending or any(
                states[item] not in {"possibly_spent", "failed_possibly_spent"}
                for item in pending[-1:]
            ):
                raise VerifyError("unterminated_frame_state_invalid")
            if any(states[item] != "captured" for item in pending[:-1]):
                raise VerifyError("unresolved_intent_not_final")
    # Enforce the 24-hour physical cap with a conservative response-plus-metadata charge per intent.
    for instant in all_intent_times:
        in_day = [t for t in all_intent_times if instant - policy.DAY_NS < t <= instant]
        if len(in_day) > policy.MAX_REQUESTS_PER_DAY:
            raise VerifyError("daily_request_cap_exceeded")
        physical = sum(
            (captured[item]["response_bytes"] if item in captured else 0) + 64_000
            for item, intent in intents.items()
            if instant - policy.DAY_NS < intent["decision_ns"] <= instant
        )
        if physical > policy.MAX_EPOCH_PHYSICAL_BYTES:
            raise VerifyError("daily_physical_cap_exceeded")
    for instant in all_intent_times:
        if (
            sum(instant - policy.THIRTY_DAYS_NS < t <= instant for t in all_intent_times)
            > policy.MAX_REQUESTS_PER_30_DAYS
        ):
            raise VerifyError("rolling_request_cap_exceeded")
    lifetime_physical = sum(
        capture["response_bytes"] + 64_000 for capture in captured.values()
    ) + 64_000 * sum(
        state in {"possibly_spent", "failed_possibly_spent"} for state in states.values()
    )
    if lifetime_physical > cap:
        raise VerifyError("lifetime_physical_cap_exceeded")
    return {
        "schema": "brain-senses-alchemy-corroboration-replay-v1",
        "frame_diagnostics": diagnostics,
        "unobserved_bucket_count": sum(event["kind"] == "frame_unobserved" for event in events),
        "request_intents": len(intents),
        "captured_requests": len(captured),
        "activation_allocation_cap_bytes": cap,
        "activation_eligible": True,
        "automatic_correction": False,
        "global_absence_claim": False,
    }
