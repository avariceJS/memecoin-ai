"""Pure request and timing helpers for Gecko lane schedule v4."""

from __future__ import annotations

import urllib.parse
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from brain_senses_gecko_lane_engine_v3 import (
    STATUS_CLIENT_ERROR,
    STATUS_EMPTY,
    STATUS_MALFORMED,
    STATUS_NOT_DISPATCHED,
    STATUS_NOT_DISPATCHED_DEADLINE,
    STATUS_NOT_DISPATCHED_SPACING,
    STATUS_NOT_DISPATCHED_STATE,
    STATUS_OVERSIZE,
    STATUS_PROVIDER_ERROR,
    STATUS_RATE_LIMITED,
    STATUS_RECEIVED,
    STATUS_TRANSPORT,
    STATUS_UNSUPPORTED,
    FetchResult,
    ReservationResult,
    classify_http,
    cooldown_until_ns,
    parse_response,
)
from brain_senses_gecko_lane_engine_v3 import (
    make_dispatch_record as _make_dispatch_record,
)
from brain_senses_gecko_lane_engine_v3 import (
    verify_dispatch_journal as _verify_dispatch_journal,
)
from brain_senses_gecko_lane_policy_v3 import NS, canonical, sha256
from brain_senses_gecko_lane_policy_v4 import BUCKET_SECONDS, verify_policy_v4

REQUEST_SCHEMA = "brain-senses-gecko-request-v4"
INTENT_SCHEMA = "brain-senses-gecko-intent-v4"
TERMINAL_SCHEMA = "brain-senses-gecko-terminal-v4"
MINIMUM_DISPATCH_SPACING_NS = 12 * NS
PERSISTENCE_MARGIN_NS = 5 * NS


def dispatch_window_v4(bucket_close_ns: int) -> tuple[int, int]:
    _validate_close(bucket_close_ns)
    return bucket_close_ns + 60 * NS, bucket_close_ns + 240 * NS


def latest_request_start_ns(bucket_close_ns: int) -> int:
    _validate_close(bucket_close_ns)
    return bucket_close_ns + 215 * NS


def classify_dispatch_v4(bucket_close_ns: int, now_ns: int) -> str:
    start_ns, end_ns = dispatch_window_v4(bucket_close_ns)
    latest_ns = latest_request_start_ns(bucket_close_ns)
    if type(now_ns) is not int or now_ns < 0:
        raise ValueError("gecko_v4_clock")
    if now_ns < start_ns:
        return "not_due"
    if now_ns >= end_ns:
        return "missed_dispatch"
    if now_ns >= latest_ns:
        return "request_start_deadline_passed"
    return "due"


def request_for_bucket_v4(
    policy: dict[str, Any], pool_id: str, bucket_close_ns: int
) -> dict[str, Any]:
    verify_policy_v4(policy)
    _validate_close(bucket_close_ns)
    pool = next((row for row in policy["pools"] if row["pool_id"] == pool_id), None)
    if pool is None:
        raise ValueError("gecko_v4_pool_not_frozen")
    close_s = bucket_close_ns // NS
    path = f"/api/v2/networks/solana/pools/{pool_id}/ohlcv/minute"
    query = {
        "aggregate": "5",
        "currency": "usd",
        "token": "base",
        "include_empty_intervals": "false",
        "limit": "6",
        "before_timestamp": str(close_s),
    }
    url = "https://api.geckoterminal.com" + path + "?" + urllib.parse.urlencode(query)
    headers = dict(policy["protocol"]["headers"])
    request_semantics = {"method": "GET", "url": url, "headers": headers}
    request_id = sha256(
        canonical(
            {
                "policy_sha256": policy["policy_sha256"],
                "pool_id": pool_id,
                "bucket_close_ns": bucket_close_ns,
            }
        )
    )
    return {
        "schema": REQUEST_SCHEMA,
        "request_id": request_id,
        "pool_id": pool_id,
        "base_mint": pool["base_mint"],
        "quote_mint": pool["quote_mint"],
        "bucket_close_ns": bucket_close_ns,
        "bucket_id": str(close_s),
        "path": path,
        "query": query,
        "url": url,
        "headers": headers,
        "semantic_request_sha256": sha256(canonical(request_semantics)),
        "max_response_bytes": min(
            policy["protocol"]["maximum_raw_response_bytes"],
            policy["protocol"]["peak_storage_bytes"],
            policy["protocol"]["maximum_raw_bytes_per_utc_day"],
            policy["protocol"]["maximum_raw_bytes_per_rolling_30d"],
        ),
    }


def request_for_bucket(policy: dict[str, Any], pool_id: str, close_ns: int) -> dict[str, Any]:
    return request_for_bucket_v4(policy, pool_id, close_ns)


def dispatch_window(close_ns: int) -> tuple[int, int]:
    return dispatch_window_v4(close_ns)


def classify_dispatch(close_ns: int, now_ns: int) -> str:
    return classify_dispatch_v4(close_ns, now_ns)


def make_skip_record(
    pool_id: str,
    close_ns: int,
    at_ns: int,
    reason: str,
    cooldown_until_ns: int | None = None,
) -> dict[str, Any]:
    start_ns, end_ns = dispatch_window_v4(close_ns)
    from brain_senses_gecko_lane_policy_v4 import POOL_ROWS

    if not any(row["pool_id"] == pool_id for row in POOL_ROWS):
        raise ValueError("gecko_v4_skip_pool")
    allowed = {
        "missed_dispatch",
        "rate_limit_cooldown",
        "provider_or_transport_cooldown",
        "malformed_response_cooldown",
        "contract_access_review_required",
    }
    if reason not in allowed or type(at_ns) is not int:
        raise ValueError("gecko_v4_skip_time")
    if reason == "missed_dispatch":
        if at_ns < close_ns + 215 * NS or cooldown_until_ns is not None:
            raise ValueError("gecko_v4_skip_time")
    elif not start_ns <= at_ns < end_ns:
        raise ValueError("gecko_v4_skip_window")
    elif cooldown_until_ns is not None and type(cooldown_until_ns) is not int:
        raise ValueError("gecko_v4_skip_cooldown")
    return {
        "schema": "brain-senses-gecko-skip-v4",
        "pool_id": pool_id,
        "bucket_close_ns": close_ns,
        "at_ns": at_ns,
        "reason": reason,
        "cooldown_until_ns": cooldown_until_ns,
    }


def make_dispatch_record(**kwargs: Any) -> dict[str, Any]:
    """Hash-chain dispatch metadata; timing is checked by this v4 module/verifier."""
    return _make_dispatch_record(**kwargs)


def verify_dispatch_journal(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    # The shared hash-chain primitive checks sequence, identity, and minimum spacing;
    # all schedule-window checks remain local to v4 callers/verifier.
    return _verify_dispatch_journal(records)


def execute_one_shot(
    policy: dict[str, Any],
    request: dict[str, Any],
    *,
    decision_ns: int,
    clock_ns: Callable[[], int],
    lane_lock: Callable[[], AbstractContextManager[Any]],
    read_dispatch_state: Callable[[], dict[str, Any] | None],
    append_dispatch_record: Callable[[dict[str, Any]], None],
    validate_dispatch_state: Callable[[dict[str, Any], int], bool],
    reserve_intent: Callable[[dict[str, Any], int, int], ReservationResult],
    fetch_once: Callable[[dict[str, Any], int, int], FetchResult],
    persist_terminal: Callable[[dict[str, Any], bytes | None], None],
    persist_skip: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Run a single durable one-shot using only the v4 schedule boundaries."""
    verify_policy_v4(policy)
    _validate_request(policy, request)
    with lane_lock():
        state = read_dispatch_state()
        if state is None:
            prior_ns, prior_sha, sequence = None, None, 0
            cooldown_until, manual_review = None, False
        else:
            if set(state) != {
                "last_dispatch_ns",
                "last_record_sha256",
                "next_sequence",
                "cooldown_until_ns",
                "manual_review_required",
            }:
                raise ValueError("gecko_v4_dispatch_state_shape")
            prior_ns = state["last_dispatch_ns"]
            prior_sha = state["last_record_sha256"]
            sequence = state["next_sequence"]
            cooldown_until = state["cooldown_until_ns"]
            manual_review = state["manual_review_required"]
            if (
                type(prior_ns) is not int
                or not _is_sha(prior_sha)
                or type(sequence) is not int
                or sequence < 1
                or (cooldown_until is not None and type(cooldown_until) is not int)
                or type(manual_review) is not bool
            ):
                raise ValueError("gecko_v4_dispatch_state_value")

        bucket_close = request["bucket_close_ns"]
        if classify_dispatch_v4(bucket_close, decision_ns) != "due":
            if decision_ns >= latest_request_start_ns(bucket_close):
                skip = make_skip_record(
                    request["pool_id"], bucket_close, decision_ns, "missed_dispatch"
                )
                persist_skip(skip)
                return {"status": "missed_dispatch", "provider_io": False, "skip": skip}
            raise ValueError("gecko_v4_dispatch_window")
        if prior_ns is not None and decision_ns < prior_ns:
            raise ValueError("gecko_v4_clock_regression")
        if prior_ns is not None and decision_ns - prior_ns < MINIMUM_DISPATCH_SPACING_NS:
            raise ValueError("gecko_v4_request_spacing")
        if manual_review:
            raise ValueError("gecko_v4_manual_review_required")
        if cooldown_until is not None and decision_ns < cooldown_until:
            raise ValueError("gecko_v4_dispatch_during_cooldown")
        if validate_dispatch_state(request, decision_ns) is not True:
            raise ValueError("gecko_v4_dispatch_state_gate")

        intent = make_intent_v4(policy, request, decision_ns)
        start_ns, end_ns = dispatch_window_v4(bucket_close)
        reserved = reserve_intent(
            intent, policy["protocol"]["physical_reserve_per_request_bytes"], end_ns
        )
        if not isinstance(reserved, ReservationResult) or type(reserved.at_ns) is not int:
            raise ValueError("gecko_v4_reservation_result")
        if reserved.status == "late":
            skip = make_skip_record(
                request["pool_id"], bucket_close, reserved.at_ns, "missed_dispatch"
            )
            persist_skip(skip)
            return {"status": "missed_dispatch", "provider_io": False, "skip": skip}
        if reserved.status != "reserved":
            raise ValueError("gecko_v4_reservation_rejected")
        if not decision_ns <= reserved.at_ns < end_ns:
            raise ValueError("gecko_v4_reservation_time")

        dispatch_ns = clock_ns()
        if type(dispatch_ns) is not int or dispatch_ns < reserved.at_ns:
            raise ValueError("gecko_v4_dispatch_clock")

        def persist_no_dispatch(status: str) -> dict[str, Any]:
            terminal = _terminal_v4(
                intent,
                status,
                dispatch_ns,
                None,
                0,
                None,
                None,
                [],
                dispatch_ns,
                dispatch_ns=dispatch_ns,
            )
            terminal["provider_io"] = False
            terminal["timeliness"] = "no_response"
            persist_terminal(terminal, None)
            return terminal

        latest_ns = latest_request_start_ns(bucket_close)
        if not start_ns <= dispatch_ns < latest_ns:
            return persist_no_dispatch(STATUS_NOT_DISPATCHED_DEADLINE)
        if validate_dispatch_state(request, dispatch_ns) is not True:
            return persist_no_dispatch(STATUS_NOT_DISPATCHED_STATE)
        if prior_ns is not None and dispatch_ns - prior_ns < MINIMUM_DISPATCH_SPACING_NS:
            return persist_no_dispatch(STATUS_NOT_DISPATCHED_SPACING)
        margin_ns = (
            policy["protocol"]["timeout_seconds"] + policy["protocol"]["persistence_margin_seconds"]
        ) * NS
        if end_ns - dispatch_ns < margin_ns:
            return persist_no_dispatch(STATUS_NOT_DISPATCHED_DEADLINE)

        marker = make_dispatch_record(
            sequence=sequence,
            request_id=request["request_id"],
            dispatch_ns=dispatch_ns,
            previous_sha256=prior_sha,
        )
        append_dispatch_record(marker)
        before_http_ns = clock_ns()
        if (
            type(before_http_ns) is not int
            or before_http_ns < dispatch_ns
            or before_http_ns >= latest_ns
            or end_ns - before_http_ns < margin_ns
            or validate_dispatch_state(request, before_http_ns) is not True
        ):
            terminal = _terminal_v4(
                intent,
                STATUS_NOT_DISPATCHED_DEADLINE,
                before_http_ns,
                None,
                0,
                None,
                None,
                [],
                before_http_ns,
                dispatch_ns=dispatch_ns,
            )
            terminal["provider_io"] = False
            terminal["timeliness"] = "no_response"
            terminal["dispatch_record_sha256"] = marker["record_sha256"]
            persist_terminal(terminal, None)
            return terminal

        capture: bytes | None = None
        try:
            fetched = fetch_once(
                request, request["max_response_bytes"], policy["protocol"]["timeout_seconds"]
            )
            if not isinstance(fetched, FetchResult):
                raise TypeError("fetch_result_shape")
            receipt_ns = clock_ns()
            if type(receipt_ns) is not int or receipt_ns < before_http_ns:
                raise ValueError("gecko_v4_receipt_clock")
            status, retry_after = classify_http(fetched.http_status, fetched.headers, receipt_ns)
            body = fetched.raw_bytes
            if not isinstance(body, bytes):
                status, retry_after, body = STATUS_MALFORMED, None, b""
            if len(body) > request["max_response_bytes"]:
                status, bars, body_sha, capture = STATUS_OVERSIZE, [], None, None
            elif status == STATUS_RECEIVED:
                try:
                    bars = parse_response(body, bucket_close, receipt_ns)
                    if not bars:
                        status = STATUS_EMPTY
                except ValueError:
                    status, bars = STATUS_MALFORMED, []
                body_sha, capture = sha256(body), body
            else:
                bars, body_sha, capture = [], sha256(body), body
            terminal = _terminal_v4(
                intent,
                status,
                receipt_ns,
                fetched.http_status,
                0 if status == STATUS_OVERSIZE else len(body),
                body_sha,
                retry_after,
                bars,
                before_http_ns,
                dispatch_ns=dispatch_ns,
            )
            terminal["dispatch_record_sha256"] = marker["record_sha256"]
            terminal["provider_io"] = True
        except Exception as exc:  # only exception class allowlisted; no exception text persists
            exc_name = type(exc).__name__
            safe_name = (
                exc_name
                if exc_name in {"TimeoutError", "ConnectionError", "OSError", "HTTPException"}
                else "Other"
            )
            receipt_ns = clock_ns()
            terminal = _terminal_v4(
                intent,
                STATUS_TRANSPORT,
                receipt_ns,
                None,
                0,
                None,
                None,
                [],
                before_http_ns,
                dispatch_ns=dispatch_ns,
            )
            terminal["transport_error_class"] = safe_name
            terminal["dispatch_record_sha256"] = marker["record_sha256"]
            terminal["provider_io"] = True
            capture = None

        # The persistence callback must sample and set local availability/timeliness
        # after capture and result fsync, before serializing the result bytes.
        terminal["timeliness"] = (
            "pending_local_durable_write" if capture is not None else "no_response"
        )
        persist_terminal(terminal, capture)
        available_ns = terminal.get("availability_ns")
        if capture is not None:
            if type(available_ns) is not int or available_ns < terminal["receipt_ns"]:
                raise ValueError("gecko_v4_durable_availability_missing")
            if available_ns < end_ns:
                terminal["timeliness"] = "available"
            else:
                terminal["timeliness"] = "late_unknown_spent_no_retry"
        return terminal


def _terminal_v4(
    intent: dict[str, Any],
    status: str,
    receipt_ns: int,
    http_status: int | None,
    response_bytes: int,
    response_sha256: str | None,
    retry_after_seconds: int | None,
    bars: list[dict[str, Any]],
    request_started_ns: int,
    *,
    dispatch_ns: int,
) -> dict[str, Any]:
    until, cooldown_reason = cooldown_until_ns(status, receipt_ns, retry_after_seconds)
    return {
        "schema": TERMINAL_SCHEMA,
        "response_schema": "brain-senses-gecko-response-v4",
        "response_capture_state": (
            "raw_response_durable_candidate" if response_sha256 is not None else "no_response"
        ),
        "request_id": intent["request_id"],
        "intent_sha256": intent["intent_sha256"],
        "status": status,
        "http_status": http_status,
        "response_bytes": response_bytes,
        "response_sha256": response_sha256,
        "receipt_ns": receipt_ns,
        "availability_ns": None,
        "request_started_ns": request_started_ns,
        "dispatch_ns": dispatch_ns,
        "bucket_stop": status
        in {
            STATUS_RATE_LIMITED,
            STATUS_CLIENT_ERROR,
            STATUS_PROVIDER_ERROR,
            STATUS_TRANSPORT,
            STATUS_MALFORMED,
            STATUS_OVERSIZE,
        },
        "retry_after_seconds": retry_after_seconds,
        "cooldown_until_ns": until,
        "cooldown_reason": cooldown_reason,
        "bars": bars,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }


def _is_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _verify_request(policy: dict[str, Any], request: dict[str, Any]) -> None:
    if not isinstance(request, dict) or request.get("schema") != REQUEST_SCHEMA:
        raise ValueError("gecko_v4_request_schema")
    pool_id = request.get("pool_id")
    close_ns = request.get("bucket_close_ns")
    if not isinstance(pool_id, str) or type(close_ns) is not int:
        raise ValueError("gecko_v4_request_identity")
    expected = request_for_bucket_v4(policy, pool_id, close_ns)
    if request != expected:
        raise ValueError("gecko_v4_request_mismatch")


def bucket_schedule_v4(policy: dict[str, Any], bucket_close_ns: int) -> list[dict[str, Any]]:
    verify_policy_v4(policy)
    start_ns, end_ns = dispatch_window_v4(bucket_close_ns)
    latest_ns = latest_request_start_ns(bucket_close_ns)
    return [
        {
            "ordinal": ordinal,
            "pool_id": pool["pool_id"],
            "request_id": request_for_bucket_v4(policy, pool["pool_id"], bucket_close_ns)[
                "request_id"
            ],
            "earliest_dispatch_ns": start_ns,
            "request_start_deadline_ns_exclusive": latest_ns,
            "durable_availability_deadline_ns_exclusive": end_ns,
            "minimum_spacing_ns": MINIMUM_DISPATCH_SPACING_NS,
        }
        for ordinal, pool in enumerate(policy["pools"])
    ]


def make_intent_v4(
    policy: dict[str, Any], request: dict[str, Any], decision_ns: int
) -> dict[str, Any]:
    verify_policy_v4(policy)
    _validate_request(policy, request)
    if classify_dispatch_v4(request["bucket_close_ns"], decision_ns) != "due":
        raise ValueError("gecko_v4_intent_window")
    intent: dict[str, Any] = {
        "schema": INTENT_SCHEMA,
        "request_id": request["request_id"],
        "request": request,
        "decision_ns": decision_ns,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }
    intent["intent_sha256"] = sha256(canonical(intent))
    return intent


def classify_durable_result_v4(
    request: dict[str, Any], *, request_started_ns: int, availability_ns: int
) -> str:
    """Classify a durably captured response; late bytes remain spent and unknown."""
    close_ns = request.get("bucket_close_ns")
    if type(close_ns) is not int:
        raise ValueError("gecko_v4_bucket_close")
    _validate_close(close_ns)
    start_ns, end_ns = dispatch_window_v4(close_ns)
    latest_ns = latest_request_start_ns(close_ns)
    if type(request_started_ns) is not int or type(availability_ns) is not int:
        raise ValueError("gecko_v4_result_time")
    if not start_ns <= request_started_ns < latest_ns:
        raise ValueError("gecko_v4_request_start_window")
    if availability_ns < request_started_ns:
        raise ValueError("gecko_v4_availability_order")
    return "available" if availability_ns < end_ns else "late_unknown_spent_no_retry"


def _validate_request(policy: dict[str, Any], request: dict[str, Any]) -> None:
    if not isinstance(request, dict) or request.get("schema") != REQUEST_SCHEMA:
        raise ValueError("gecko_v4_request_schema")
    pool_id = request.get("pool_id")
    close_ns = request.get("bucket_close_ns")
    if not isinstance(pool_id, str) or type(close_ns) is not int:
        raise ValueError("gecko_v4_request_identity")
    expected = request_for_bucket_v4(policy, pool_id, close_ns)
    if request != expected:
        raise ValueError("gecko_v4_request_mismatch")


def _validate_close(bucket_close_ns: Any) -> None:
    if (
        type(bucket_close_ns) is not int
        or bucket_close_ns <= 0
        or bucket_close_ns % (BUCKET_SECONDS * NS)
    ):
        raise ValueError("gecko_v4_bucket_close")


__all__ = [
    "INTENT_SCHEMA",
    "MINIMUM_DISPATCH_SPACING_NS",
    "REQUEST_SCHEMA",
    "TERMINAL_SCHEMA",
    "FetchResult",
    "ReservationResult",
    "STATUS_CLIENT_ERROR",
    "STATUS_EMPTY",
    "STATUS_MALFORMED",
    "STATUS_NOT_DISPATCHED",
    "STATUS_NOT_DISPATCHED_DEADLINE",
    "STATUS_NOT_DISPATCHED_SPACING",
    "STATUS_NOT_DISPATCHED_STATE",
    "STATUS_OVERSIZE",
    "STATUS_PROVIDER_ERROR",
    "STATUS_RATE_LIMITED",
    "STATUS_RECEIVED",
    "STATUS_TRANSPORT",
    "STATUS_UNSUPPORTED",
    "bucket_schedule_v4",
    "classify_dispatch",
    "classify_dispatch_v4",
    "classify_durable_result_v4",
    "dispatch_window_v4",
    "latest_request_start_ns",
    "make_intent_v4",
    "make_dispatch_record",
    "make_skip_record",
    "parse_response",
    "request_for_bucket",
    "request_for_bucket_v4",
    "dispatch_window",
    "execute_one_shot",
    "verify_dispatch_journal",
    "cooldown_until_ns",
    "classify_http",
]
