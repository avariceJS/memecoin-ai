"""Minimal host adapter for descriptor-bound scout-chain v2 capture.

No owner paths, provider transport, launchd controls, or activation are exposed
here. Callers must supply independently verified release context and a one-shot
fetch function; recovery paths never invoke that function.
"""

from __future__ import annotations

import base64
import multiprocessing
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple, cast

from tools import brain_senses_gecko_capture_only_v2_service as service
from tools import brain_senses_gecko_capture_only_v3_verify as verify
from tools import brain_senses_gecko_lane_engine_v3 as engine
from tools import brain_senses_gecko_rotating_scout_v1 as scout
from tools.brain_senses_external_clock_v1 import ClockError, validate_progress
from tools.brain_senses_external_writer_v1 import canonical_json
from tools.brain_senses_gecko_capture_descriptor_v2 import (
    CaptureWriteError,
    DescriptorCaptureWriterV2,
    sha256,
)


class HostError(ValueError):
    """A fail-closed release, timing, replay, or transaction error."""


def _validate_clock_sample(wall_ns: int, monotonic_ns: int, record: Mapping[str, Any]) -> None:
    try:
        validate_progress(dict(record), wall_ns=wall_ns, monotonic_ns=monotonic_ns)
    except (ClockError, TypeError, ValueError) as exc:
        raise HostError("capture_clock_invalid_or_stale") from exc


RELEASE_SCHEMA = "brain-senses-gecko-capture-host-v2-release"
ACTIVATION_SCHEMA = "brain-senses-gecko-capture-host-v2-activation"
_HASH = re.compile(r"^[0-9a-f]{64}$")


def verify_release_context(
    release: Mapping[str, Any],
    activation: Mapping[str, Any],
    *,
    expected_sources: Mapping[str, str],
    actual_storage: Mapping[str, Any],
    actual_plist_sha256: str,
    actual_watchdog_sha256: str,
    actual_clock_sha256: str,
    actual_plan_sha256: str,
    actual_certificate_sha256: str,
    clock_record: Mapping[str, Any],
    now_ns: int,
    monotonic_ns: int,
) -> None:
    """Bind a runtime invocation to exact source, storage, plist and watchdog pins."""
    release_core = dict(release)
    claimed = release_core.pop("release_sha256", None)
    source_bindings = release.get("source_bindings")
    if (
        release.get("schema") != RELEASE_SCHEMA
        or release.get("schema_version") != 2
        or type(claimed) is not str
        or claimed != sha256(canonical_json(release_core))
        or source_bindings != dict(expected_sources)
        or not source_bindings
        or any(
            type(value) is not str or _HASH.fullmatch(value) is None
            for value in source_bindings.values()
        )
        or release.get("storage") != dict(actual_storage)
        or release.get("plist_sha256") != actual_plist_sha256
        or release.get("watchdog_sha256") != actual_watchdog_sha256
        or release.get("clock_sha256") != actual_clock_sha256
        or release.get("plan_sha256") != actual_plan_sha256
        or release.get("cutover_certificate_sha256") != actual_certificate_sha256
        or release.get("capture_authorized") is not True
        or release.get("market_source_qualified") is not False
        or release.get("training_admission") is not False
        or release.get("economic_admission") is not False
    ):
        raise HostError("capture_release_binding_invalid")
    activation_core = dict(activation)
    activation_sha = activation_core.pop("activation_sha256", None)
    if (
        activation.get("schema") != ACTIVATION_SCHEMA
        or activation.get("schema_version") != 2
        or type(activation_sha) is not str
        or activation_sha != sha256(canonical_json(activation_core))
        or activation.get("release_sha256") != claimed
        or activation.get("source_bindings") != dict(expected_sources)
        or activation.get("storage") != dict(actual_storage)
        or activation.get("plist_sha256") != actual_plist_sha256
        or activation.get("watchdog_sha256") != actual_watchdog_sha256
        or activation.get("clock_sha256") != actual_clock_sha256
        or activation.get("plan_sha256") != actual_plan_sha256
        or activation.get("cutover_certificate_sha256") != actual_certificate_sha256
        or activation.get("dispatch_authorized") is not True
    ):
        raise HostError("capture_activation_binding_invalid")
    _validate_clock_sample(now_ns, monotonic_ns, clock_record)


class FetchObservation(NamedTuple):
    fetched: service.FetchResult
    started_ns: int
    started_monotonic_ns: int
    receipt_ns: int
    receipt_monotonic_ns: int
    timed_out: bool
    not_started: bool = False


def _fetch_child(
    connection: Any,
    fetch_once: Callable[[Mapping[str, Any]], Any],
    request: Mapping[str, Any],
    latest_start_ns: int,
) -> None:
    started_ns = time.time_ns()
    started_monotonic_ns = time.monotonic_ns()
    connection.send(("started", None, started_ns, started_monotonic_ns))
    if started_ns >= latest_start_ns:
        connection.send(("not_started", None, started_ns, started_monotonic_ns))
        connection.close()
        return
    try:
        fetched = fetch_once(request)
        connection.send(("ok", fetched, started_ns, started_monotonic_ns))
    except BaseException as exc:  # The parent records only an allowlisted class.
        connection.send(("error", exc.__class__.__name__, started_ns, started_monotonic_ns))
    finally:
        connection.close()


def fetch_with_hard_deadline(
    fetch_once: Callable[[Mapping[str, Any]], Any],
    request: Mapping[str, Any],
    *,
    timeout_seconds: float = 30.0,
    latest_start_ns: int | None = None,
) -> FetchObservation:
    """Run one injected transport in a killable process with a hard wall bound."""
    if not 0 < timeout_seconds <= 30:
        raise HostError("hard_deadline_invalid")
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_fetch_child,
        args=(child, fetch_once, dict(request), latest_start_ns or 2**63 - 1),
    )
    start_wall, start_mono = time.time_ns(), time.monotonic_ns()
    process.start()
    child.close()
    started_wall, started_mono = start_wall, start_mono
    result_message: tuple[Any, ...] | None = None
    if parent.poll(min(1.0, timeout_seconds)):
        message = parent.recv()
        if message[0] == "started":
            started_wall, started_mono = message[2], message[3]
        else:
            result_message = message
    remaining = max(
        0.0,
        min(
            timeout_seconds,
            (started_mono + int(timeout_seconds * 1_000_000_000) - time.monotonic_ns())
            / 1_000_000_000,
        ),
    )
    process.join(remaining)
    timed_out = process.is_alive()
    if timed_out:
        process.terminate()
        process.join(1.0)
        if process.is_alive():
            process.kill()
            process.join(1.0)
        if process.is_alive():
            parent.close()
            raise HostError("hard_deadline_child_survived")
        receipt_wall, receipt_mono = time.time_ns(), time.monotonic_ns()
        parent.close()
        return FetchObservation(
            service.FetchResult(None, None, {}, "TimeoutError"),
            started_wall,
            started_mono,
            receipt_wall,
            receipt_mono,
            True,
        )
    receipt_wall, receipt_mono = time.time_ns(), time.monotonic_ns()
    try:
        if result_message is None and parent.poll():
            result_message = parent.recv()
        if result_message is None:
            fetched = service.FetchResult(None, None, {}, "WorkerExit")
        else:
            kind, value, result_wall, result_mono = result_message
            if kind == "started":
                fetched = service.FetchResult(None, None, {}, "WorkerExit")
            elif kind == "ok" and isinstance(value, service.FetchResult):
                fetched = value
                started_wall, started_mono = result_wall, result_mono
            elif kind == "not_started":
                return FetchObservation(
                    service.FetchResult(None, None, {}, "LateStart"),
                    result_wall,
                    result_mono,
                    receipt_wall,
                    receipt_mono,
                    False,
                    True,
                )
            else:
                fetched = service.FetchResult(None, None, {}, str(value)[:64])
    finally:
        parent.close()
        process.join(0)
    return FetchObservation(fetched, started_wall, started_mono, receipt_wall, receipt_mono, False)


def _bars(body: bytes | None, close_ns: int, receipt_ns: int) -> list[dict[str, Any]]:
    if body is None:
        return []
    parsed = engine.parse_response(body, close_ns, receipt_ns)
    rows: list[dict[str, Any]] = []
    for bar in parsed:
        open_ns = bar["event_time_ns"]
        region = (
            "new_open"
            if close_ns - 900 * scout.NS <= open_ns < close_ns
            else "overlap_revision"
            if close_ns - 1800 * scout.NS <= open_ns < close_ns - 900 * scout.NS
            else "out_of_window"
        )
        rows.append(
            {
                key: bar[key]
                for key in ("open_s", "event_time_ns", "open", "high", "low", "close", "volume")
            }
            | {"region": region}
        )
    return rows


def _terminal(
    *,
    intent: Mapping[str, Any],
    dispatch: Mapping[str, Any],
    observation: FetchObservation,
) -> tuple[dict[str, Any], bool]:
    fetched = observation.fetched
    status_code = fetched.http_status
    body = fetched.body
    over_budget = body is not None and len(body) > scout.MAX_RESPONSE_BYTES
    if over_budget:
        body = None
        fetched = service.FetchResult(
            fetched.http_status,
            None,
            fetched.headers,
            fetched.transport_error_class,
            fetched.retry_after_values,
            fetched.retry_after_count,
            fetched.retry_after_lengths,
        )
    retry_seconds, retry_class, retry_evidence, retry_count = service._retry_after_evidence(fetched)
    ambiguous = False
    stop_reason: str | None = None
    cooldown: int | None = None
    if observation.timed_out or fetched.transport_error_class is not None or status_code is None:
        status, body, ambiguous = "transport_uncertain_possibly_spent", None, True
        stop_reason = "transport_uncertain"
        cooldown = observation.receipt_ns + 900 * scout.NS
    elif status_code == 429:
        status = "rate_limited"
        stop_reason = (
            "review_required_retry_after"
            if retry_class not in {"absent", "valid"}
            else "rate_limited"
        )
        if stop_reason == "rate_limited":
            cooldown = observation.receipt_ns + max(3600, retry_seconds or 0) * scout.NS
    elif status_code == 408 or status_code >= 500:
        status = "provider_error"
        stop_reason = (
            "review_required_retry_after"
            if retry_class not in {"absent", "valid"}
            else "provider_error"
        )
        if stop_reason == "provider_error":
            cooldown = observation.receipt_ns + max(900, retry_seconds or 0) * scout.NS
    elif 400 <= status_code < 500:
        status, stop_reason = "client_or_access_error", "client_or_access_error"
    elif over_budget:
        status, ambiguous = "response_over_budget", True
        stop_reason = "response_over_budget"
    elif body is None:
        status, ambiguous = "transport_uncertain_possibly_spent", True
        stop_reason = "transport_uncertain"
        cooldown = observation.receipt_ns + 900 * scout.NS
    elif len(body) > scout.MAX_RESPONSE_BYTES:
        status, body, ambiguous = "response_over_budget", None, True
        stop_reason = "response_over_budget"
    else:
        try:
            parsed = _bars(body, intent["request"]["bucket_close_ns"], observation.receipt_ns)
        except (ValueError, TypeError, KeyError, OverflowError):
            status = "malformed_response"
        else:
            status = "received" if parsed else "empty_response"
    bars = (
        _bars(body, intent["request"]["bucket_close_ns"], observation.receipt_ns)
        if body is not None and status in {"received", "empty_response"}
        else []
    )
    request = intent["request"]
    raw_b64 = base64.b64encode(body).decode("ascii") if body is not None else None
    terminal = {
        "schema": verify.TERMINAL_SCHEMA,
        "schema_version": 2,
        "request_id": intent["request_id"],
        "intent_sha256": intent["intent_sha256"],
        "plan_sha256": request["plan_record_sha256"],
        "dispatch_record_sha256": dispatch["record_sha256"],
        "dispatch_ns": dispatch["dispatch_ns"],
        "dispatch_monotonic_ns": dispatch["dispatch_monotonic_ns"],
        "request_started_ns": observation.started_ns,
        "request_started_monotonic_ns": observation.started_monotonic_ns,
        "receipt_ns": observation.receipt_ns,
        "receipt_monotonic_ns": observation.receipt_monotonic_ns,
        "deadline_monotonic_ns": observation.started_monotonic_ns + verify.HARD_REQUEST_DEADLINE_NS,
        "status": status,
        "http_status": status_code,
        "transport_error_class": "TimeoutError"
        if observation.timed_out
        else fetched.transport_error_class,
        "retry_after_seconds": retry_seconds,
        "retry_after_header_count": retry_count,
        "retry_after_evidence": retry_evidence,
        "retry_after_class": retry_class,
        "cooldown_until_ns": cooldown,
        "stop_reason": stop_reason,
        "provider_io": True,
        "raw_body_b64": raw_b64,
        "response_bytes": len(body) if body is not None else None,
        "response_sha256": sha256(body) if body is not None else None,
        "bars": bars,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }
    return terminal, ambiguous


def execute_one_slot(
    *,
    store: DescriptorCaptureWriterV2,
    plan_record: Mapping[str, Any],
    slot_index: int,
    policy: Mapping[str, Any],
    binding: Mapping[str, Any],
    certificate: Mapping[str, Any],
    certificate_inputs: Mapping[str, Any],
    replay_arguments: Callable[[], Mapping[str, Any]],
    admission_factory: Callable[[str, int], Mapping[str, Any]],
    clock_sample: Callable[[], tuple[int, int, Mapping[str, Any]]],
    fetch_once: Callable[[Mapping[str, Any]], Any],
    hard_timeout_seconds: float = 30.0,
    release_context: tuple[
        Mapping[str, Any],
        Mapping[str, Any],
        Mapping[str, str],
        Mapping[str, bytes],
        Mapping[str, Any],
    ]
    | None = None,
) -> dict[str, Any]:
    """Perform one future slot transaction; a spent or uncertain slot never replays."""
    result: dict[str, Any] = {
        "provider_io": False,
        "provider_io_possible": False,
        "attempted": False,
    }
    try:
        if release_context is None:
            raise HostError("capture_release_context_required")
        release, activation, expected_sources, source_bytes, observed = release_context
        actual_source_map = {path: sha256(data) for path, data in source_bytes.items()}
        if actual_source_map != dict(expected_sources):
            raise HostError("capture_source_bytes_changed")
        verify_release_context(
            release,
            activation,
            expected_sources=expected_sources,
            actual_storage=cast(Mapping[str, Any], observed["storage"]),
            actual_plist_sha256=str(observed["plist_sha256"]),
            actual_watchdog_sha256=str(observed["watchdog_sha256"]),
            actual_clock_sha256=str(observed["clock_sha256"]),
            actual_plan_sha256=str(observed["plan_sha256"]),
            actual_certificate_sha256=str(observed["cutover_certificate_sha256"]),
            clock_record=cast(Mapping[str, Any], observed["clock_record"]),
            now_ns=cast(int, observed["now_ns"]),
            monotonic_ns=cast(int, observed["monotonic_ns"]),
        )
        if (
            dict(binding.get("storage", {})) != dict(observed["storage"])
            or plan_record.get("record_sha256") != observed["plan_sha256"]
            or certificate.get("certificate_sha256") != observed["cutover_certificate_sha256"]
        ):
            raise HostError("capture_release_plan_binding_invalid")
        with store.locked() as lane_lease:
            replay_now_ns, replay_mono_ns, replay_clock = clock_sample()
            _validate_clock_sample(replay_now_ns, replay_mono_ns, replay_clock)
            replay_inputs = dict(replay_arguments())
            if (
                replay_inputs.get("root") != store.root
                or replay_inputs.get("binding") != dict(binding)
                or replay_inputs.get("policy") != dict(policy)
                or replay_inputs.get("certificate") != dict(certificate)
                or replay_inputs.get("certificate_inputs") != dict(certificate_inputs)
            ):
                raise HostError("capture_replay_context_mismatch")
            replay = verify.verify_capture_prefix_v3(**replay_inputs, observed_now_ns=replay_now_ns)
            if (
                replay.get("status") != "verified_prefix"
                or replay.get("dispatch_blocked") is True
                or replay.get("unresolved_count") != 0
                or replay.get("expired_unaccounted_slots")
            ):
                raise HostError("capture_prefix_blocks_dispatch")
            chain_records = replay_inputs["chain_records"]
            if not any(dict(row) == dict(plan_record) for row in chain_records):
                raise HostError("capture_plan_not_in_verified_chain")
            request_slot = plan_record["sequence"], slot_index
            accounted_slots = {
                (row["request"]["plan_sequence"], row["request"]["slot_index"])
                for row in replay_inputs["intents"]
            } | {(row["plan_sequence"], row["slot_index"]) for row in replay_inputs["skips"]}
            if request_slot in accounted_slots or any(
                (plan_record["sequence"], prior_slot) not in accounted_slots
                for prior_slot in range(slot_index)
            ):
                raise HostError("capture_slot_order_invalid")
            request = verify.expected_request(
                policy,
                str(binding["binding_sha256"]),
                plan_record,
                slot_index,
            )
            existing = store.writer.exists(store.path(f"intents/{request['request_id']}.json"))
            if existing:
                raise HostError("capture_slot_already_spent")
            decision_ns, decision_mono, decision_clock = clock_sample()
            _validate_clock_sample(decision_ns, decision_mono, decision_clock)
            close_ns = request["bucket_close_ns"]
            if (
                not close_ns + verify.WINDOW_START_NS
                <= decision_ns
                < close_ns + verify.REQUEST_START_END_NS
            ):
                raise HostError("capture_dispatch_window_closed")
            if (
                decision_ns + verify.HARD_REQUEST_DEADLINE_NS + 5 * scout.NS
                >= close_ns + verify.AVAILABILITY_END_NS
            ):
                raise HostError("capture_deadline_margin_insufficient")
            admission = dict(admission_factory(request["request_id"], decision_ns))
            intent = verify.build_intent(
                root=store.root,
                policy=policy,
                binding=binding,
                plan_record=plan_record,
                slot_index=slot_index,
                decision_ns=decision_ns,
                decision_monotonic_ns=decision_mono,
                clock_record=decision_clock,
                admission=admission,
            )
            reservation = store.create_intent_and_reserve(
                intent=intent,
                reserve_bytes=verify.MAX_RESPONSE_BYTES,
                lane_lease=lane_lease,
            )
            result.update({"attempted": True, "request_id": intent["request_id"]})
            dispatch_ns, dispatch_mono, dispatch_clock = clock_sample()
            _validate_clock_sample(dispatch_ns, dispatch_mono, dispatch_clock)
            if (
                not close_ns + verify.WINDOW_START_NS
                <= dispatch_ns
                < close_ns + verify.REQUEST_START_END_NS
                or dispatch_ns + verify.HARD_REQUEST_DEADLINE_NS + 5 * scout.NS
                >= close_ns + verify.AVAILABILITY_END_NS
            ):
                result["status"] = "spent_before_dispatch_deadline"
                return result
            dispatch = store.append_dispatch(
                intent=intent,
                dispatch_ns=dispatch_ns,
                dispatch_monotonic_ns=dispatch_mono,
            )
            result["provider_io_possible"] = True
            observation = fetch_with_hard_deadline(
                fetch_once,
                request,
                timeout_seconds=hard_timeout_seconds,
                latest_start_ns=close_ns + verify.REQUEST_START_END_NS,
            )
            if observation.not_started:
                result["status"] = "spent_before_transport_start"
                result["provider_io_possible"] = False
                return result
            result["provider_io"] = True
            terminal, ambiguous = _terminal(
                intent=intent, dispatch=dispatch, observation=observation
            )
            store.create_terminal_and_settle(
                reservation=reservation,
                terminal=terminal,
                ambiguous=ambiguous,
                lane_lease=lane_lease,
            )
            availability_ns, availability_mono, availability_clock = clock_sample()
            _validate_clock_sample(availability_ns, availability_mono, availability_clock)
            if availability_ns >= close_ns + verify.AVAILABILITY_END_NS:
                result.update(
                    {
                        "status": "terminal_persisted_confirmation_late",
                        "provider_io": True,
                        "decision_eligible_ns": None,
                    }
                )
                return result
            # Confirmation is the earliest downstream decision-eligibility point.
            confirmation = store.create_confirmation(
                request_id=intent["request_id"],
                terminal=terminal,
                availability_ns=availability_ns,
                availability_monotonic_ns=availability_mono,
                availability_clock=availability_clock,
                coordinator_event=store.last_coordinator_terminal(reservation.reservation_id),
            )
            result.update(
                {
                    "status": terminal["status"],
                    "availability_ns": confirmation["availability_ns"],
                    "decision_eligible_ns": confirmation["availability_ns"],
                    "provider_charge_status": "unknown_unverified",
                }
            )
        return result
    except (HostError, CaptureWriteError, ValueError, OSError, RuntimeError) as exc:
        return {
            **result,
            "status": "failed_closed",
            "reason": exc.__class__.__name__,
            "provider_io_possible": result.get("provider_io_possible", False),
        }


def recover_slot_without_provider(
    *,
    store: DescriptorCaptureWriterV2,
    request_id: str,
    replay_arguments: Callable[[], Mapping[str, Any]],
    clock_sample: Callable[[], tuple[int, int, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Reconcile one spent request without a transport callable or replay path."""
    try:
        with store.locked() as lane_lease:
            now_ns, _, _ = clock_sample()
            args = dict(replay_arguments())
            replay = verify.verify_capture_prefix_v3(**args, observed_now_ns=now_ns)
            intents = {row["request_id"]: row for row in args["intents"]}
            terminals = {row["request_id"]: row for row in args["terminals"]}
            confirmations = {row["request_id"]: row for row in args["confirmations"]}
            intent = intents.get(request_id)
            if intent is None:
                return {"status": "no_attempt", "provider_io": False}
            if request_id not in terminals:
                store.coordinator.recover_service(
                    "gecko",
                    intent_exists=lambda writer, path: writer.exists(path),
                    lane_lease=lane_lease,
                )
                return {
                    "status": "spent_unresolved",
                    "request_id": request_id,
                    "provider_io": False,
                    "replay_forbidden": True,
                }
            if request_id in confirmations:
                return {
                    "status": "already_confirmed",
                    "request_id": request_id,
                    "provider_io": False,
                    "replay_forbidden": True,
                }
            terminal = terminals[request_id]
            _, coordinator_events = store.coordinator._load_ledger()
            reserved = [
                row
                for row in coordinator_events
                if row.get("kind") == "reserved"
                and row.get("fields", {}).get("request_id") == request_id
            ]
            if len(reserved) != 1:
                raise HostError("recovery_reservation_ambiguous")
            reservation_id = reserved[0]["fields"]["reservation_id"]
            settled = [
                row
                for row in coordinator_events
                if row.get("kind") in {"settled", "ambiguous_terminal"}
                and row.get("fields", {}).get("reservation_id") == reservation_id
            ]
            if len(settled) != 1 or settled[0]["fields"].get("outcome_sha256") != sha256(
                canonical_json(terminal)
            ):
                raise HostError("recovery_terminal_coordinator_mismatch")
            availability_ns, availability_mono, availability_clock = clock_sample()
            close_ns = intent["request"]["bucket_close_ns"]
            if (
                availability_ns < terminal["receipt_ns"]
                or availability_ns >= close_ns + verify.AVAILABILITY_END_NS
            ):
                return {
                    "status": "spent_terminal_unconfirmed",
                    "request_id": request_id,
                    "provider_io": False,
                    "replay_forbidden": True,
                }
            store.create_confirmation(
                request_id=request_id,
                terminal=terminal,
                availability_ns=availability_ns,
                availability_monotonic_ns=availability_mono,
                availability_clock=availability_clock,
                coordinator_event=settled[0],
            )
            after = verify.verify_capture_prefix_v3(
                **dict(replay_arguments()), observed_now_ns=availability_ns
            )
            if after.get("unresolved_count") != replay.get("unresolved_count", 1) - 1:
                raise HostError("recovery_confirmation_not_replayed")
            return {
                "status": "recovered_confirmation",
                "request_id": request_id,
                "availability_ns": availability_ns,
                "decision_eligible_ns": availability_ns,
                "provider_io": False,
                "replay_forbidden": True,
            }
    except (HostError, CaptureWriteError, ValueError, OSError, RuntimeError) as exc:
        return {
            "status": "failed_closed",
            "reason": exc.__class__.__name__,
            "provider_io": False,
            "replay_forbidden": True,
        }


__all__ = [
    "FetchObservation",
    "HostError",
    "execute_one_slot",
    "fetch_with_hard_deadline",
    "recover_slot_without_provider",
]
