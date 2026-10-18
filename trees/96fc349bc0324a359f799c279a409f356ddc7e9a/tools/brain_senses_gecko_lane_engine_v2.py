"""Injected-only, one-shot Gecko lane protocol helpers; no network or storage defaults."""

from __future__ import annotations

import datetime as dt
import email.utils
import json
import math
import urllib.parse
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

from brain_senses_gecko_lane_policy_v2 import (
    BAR_SECONDS,
    BUCKET_SECONDS,
    NS,
    canonical,
    sha256,
    verify_policy,
)

STATUS_RECEIVED = "received"
STATUS_EMPTY = "empty_response"
STATUS_UNSUPPORTED = "unsupported_at_receipt"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_CLIENT_ERROR = "client_or_access_error"
STATUS_PROVIDER_ERROR = "provider_error"
STATUS_TRANSPORT = "transport_uncertain_possibly_spent"
STATUS_MALFORMED = "malformed_response"
STATUS_OVERSIZE = "response_over_budget"
STATUS_NOT_DISPATCHED = "intent_not_dispatched_window_expired"
STATUS_NOT_DISPATCHED_SPACING = "intent_not_dispatched_spacing"
STATUS_NOT_DISPATCHED_STATE = "intent_not_dispatched_state_changed"


class FetchResult(NamedTuple):
    http_status: int
    raw_bytes: bytes
    headers: Mapping[str, str]


class ReservationResult(NamedTuple):
    status: str
    at_ns: int


def request_for_bucket(
    policy: dict[str, Any], pool_id: str, bucket_close_ns: int
) -> dict[str, Any]:
    verify_policy(policy)
    if (
        type(bucket_close_ns) is not int
        or bucket_close_ns <= 0
        or bucket_close_ns % (BUCKET_SECONDS * NS)
    ):
        raise ValueError("gecko_bucket_close")
    pool = next((row for row in policy["pools"] if row["pool_id"] == pool_id), None)
    if pool is None:
        raise ValueError("gecko_pool_not_frozen")
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
    query_string = urllib.parse.urlencode(query)
    url = "https://api.geckoterminal.com" + path + "?" + query_string
    headers = dict(policy["protocol"]["headers"])
    semantic = {"method": "GET", "url": url, "headers": headers}
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
        "schema": "brain-senses-gecko-request-v2",
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
        "semantic_request_sha256": sha256(canonical(semantic)),
        "max_response_bytes": min(
            policy["protocol"]["maximum_raw_response_bytes"],
            policy["protocol"]["peak_storage_bytes"],
            policy["protocol"]["maximum_raw_bytes_per_utc_day"],
            policy["protocol"]["maximum_raw_bytes_per_rolling_30d"],
        ),
    }


def dispatch_window(bucket_close_ns: int) -> tuple[int, int]:
    if (
        type(bucket_close_ns) is not int
        or bucket_close_ns <= 0
        or bucket_close_ns % (BUCKET_SECONDS * NS)
    ):
        raise ValueError("gecko_bucket_close")
    return bucket_close_ns + 60 * NS, bucket_close_ns + 180 * NS


def bucket_schedule(policy: dict[str, Any], bucket_close_ns: int) -> list[dict[str, Any]]:
    """Return the frozen four-request order for one bucket; no request is dispatched here."""
    verify_policy(policy)
    earliest, deadline = dispatch_window(bucket_close_ns)
    result = []
    for ordinal, pool in enumerate(policy["pools"]):
        request = request_for_bucket(policy, pool["pool_id"], bucket_close_ns)
        result.append(
            {
                "ordinal": ordinal,
                "pool_id": pool["pool_id"],
                "request_id": request["request_id"],
                "earliest_dispatch_ns": earliest,
                "dispatch_deadline_ns": deadline,
                "minimum_spacing_ns": 2 * NS,
            }
        )
    return result


def make_skip_record(
    pool_id: str,
    bucket_close_ns: int,
    at_ns: int,
    reason: str,
    cooldown_until_ns_value: int | None = None,
) -> dict[str, Any]:
    if reason not in {
        "missed_dispatch",
        "rate_limit_cooldown",
        "provider_or_transport_cooldown",
        "contract_access_review_required",
    }:
        raise ValueError("gecko_skip_reason")
    start, end = dispatch_window(bucket_close_ns)
    if reason == "missed_dispatch":
        if at_ns < end or cooldown_until_ns_value is not None:
            raise ValueError("gecko_skip_time")
    elif not start <= at_ns < end:
        raise ValueError("gecko_skip_time")
    return {
        "schema": "brain-senses-gecko-skip-v2",
        "pool_id": pool_id,
        "bucket_close_ns": bucket_close_ns,
        "at_ns": at_ns,
        "reason": reason,
        "cooldown_until_ns": cooldown_until_ns_value,
    }


def classify_dispatch(bucket_close_ns: int, now_ns: int) -> str:
    if type(now_ns) is not int or now_ns < 0:
        raise ValueError("gecko_clock")
    start, end = dispatch_window(bucket_close_ns)
    if now_ns < start:
        return "not_due"
    if now_ns >= end:
        return "missed_dispatch"
    return "due"


def execute_one_shot(
    policy: dict[str, Any],
    request: dict[str, Any],
    *,
    decision_ns: int,
    previous_dispatch_ns: int | None,
    clock_ns: Callable[[], int],
    validate_dispatch_state: Callable[[dict[str, Any], int], bool],
    reserve_intent: Callable[[dict[str, Any], int, int], ReservationResult],
    fetch_once: Callable[[dict[str, Any], int], FetchResult],
    persist_terminal: Callable[[dict[str, Any], bytes | None], None],
    persist_skip: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """One-shot adapter; the injected state gate/coordinator must check all v2 caps each call.

    ``validate_dispatch_state`` must verify the pinned coordinator-v2 source and current
    shared capacity/day/30-day usage before this reservation. ``reserve_intent`` must
    persist through that coordinator with the 80 KiB physical reservation. Neither
    callback has a permissive default, and this module has no network/storage defaults.
    """
    verify_policy(policy)
    _verify_request(policy, request)
    if classify_dispatch(request["bucket_close_ns"], decision_ns) != "due":
        raise ValueError("gecko_dispatch_window")
    if previous_dispatch_ns is not None and decision_ns - previous_dispatch_ns < 2 * NS:
        raise ValueError("gecko_request_spacing")
    if previous_dispatch_ns is not None and decision_ns < previous_dispatch_ns:
        raise ValueError("gecko_clock_regression")
    if validate_dispatch_state(request, decision_ns) is not True:
        raise ValueError("gecko_dispatch_state_gate")
    start_ns, end_ns = dispatch_window(request["bucket_close_ns"])
    capture_body: bytes | None = None
    intent = {
        "schema": "brain-senses-gecko-intent-v2",
        "request_id": request["request_id"],
        "request": request,
        "decision_ns": decision_ns,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }
    intent["intent_sha256"] = sha256(canonical(intent))
    # The callback must recheck the deadline atomically with durable intent creation.
    reserved = reserve_intent(
        intent,
        policy["protocol"]["physical_reserve_per_request_bytes"],
        end_ns,
    )
    if not isinstance(reserved, ReservationResult) or type(reserved.at_ns) is not int:
        raise ValueError("gecko_reservation_result")
    if reserved.status == "late":
        skip = make_skip_record(
            request["pool_id"], request["bucket_close_ns"], reserved.at_ns, "missed_dispatch"
        )
        persist_skip(skip)
        return {"status": "missed_dispatch", "provider_io": False, "skip": skip}
    if reserved.status != "reserved":
        raise ValueError("gecko_intent_reservation_rejected")
    if not decision_ns <= reserved.at_ns < end_ns:
        raise ValueError("gecko_reservation_time")

    # The callback must persist the request intent before returning. The request is now spent.
    dispatch_ns = clock_ns()
    if type(dispatch_ns) is not int or dispatch_ns < decision_ns:
        raise ValueError("gecko_dispatch_clock")
    start_ns, end_ns = dispatch_window(request["bucket_close_ns"])
    if not start_ns <= dispatch_ns < end_ns:
        terminal = _terminal(
            intent, STATUS_NOT_DISPATCHED, dispatch_ns, None, b"", None, None, [], dispatch_ns
        )
        persist_terminal(terminal, None)
        return terminal
    if validate_dispatch_state(request, dispatch_ns) is not True:
        terminal = _terminal(
            intent, STATUS_NOT_DISPATCHED_STATE, dispatch_ns, None, b"", None, None, [], dispatch_ns
        )
        persist_terminal(terminal, None)
        return terminal
    if previous_dispatch_ns is not None and dispatch_ns - previous_dispatch_ns < 2 * NS:
        terminal = _terminal(
            intent,
            STATUS_NOT_DISPATCHED_SPACING,
            dispatch_ns,
            None,
            b"",
            None,
            None,
            [],
            dispatch_ns,
        )
        persist_terminal(terminal, None)
        return terminal

    received_ns: int | None = None
    try:
        fetched = fetch_once(request, request["max_response_bytes"])
        if not isinstance(fetched, FetchResult):
            raise TypeError("fetch_result_shape")
        received_ns = clock_ns()
        if type(received_ns) is not int or received_ns < dispatch_ns:
            raise ValueError("gecko_receipt_clock")
        status, retry_after_s = classify_http(fetched.http_status, fetched.headers, received_ns)
        body = fetched.raw_bytes
        if not isinstance(body, bytes):
            status, retry_after_s, body = STATUS_MALFORMED, None, b""
        if len(body) > request["max_response_bytes"]:
            status = STATUS_OVERSIZE
            bars = []
            response_sha = None
            capture_body = None
        elif status == STATUS_RECEIVED:
            try:
                bars = parse_response(body, request["bucket_close_ns"], received_ns)
                if not bars:
                    status = STATUS_EMPTY
            except ValueError:
                status, bars = STATUS_MALFORMED, []
            response_sha = sha256(body)
            capture_body = body
        else:
            bars = []
            response_sha = sha256(body)
            capture_body = body
        terminal = _terminal(
            intent,
            status,
            received_ns,
            fetched.http_status,
            b"" if status == STATUS_OVERSIZE else body,
            response_sha,
            retry_after_s,
            bars,
            dispatch_ns,
            response_length=len(body),
        )
    except Exception as exc:  # no exception text is retained
        error_class = type(exc).__name__
        safe_class = (
            error_class
            if error_class in {"TimeoutError", "ConnectionError", "OSError", "HTTPException"}
            else "Other"
        )
        receipt_ns = clock_ns()
        terminal = _terminal(
            intent, STATUS_TRANSPORT, receipt_ns, None, b"", None, None, [], dispatch_ns
        )
        terminal["transport_error_class"] = safe_class
        capture_body = None
    try:
        persist_terminal(terminal, capture_body)
    except Exception:
        # Durable intent remains spent. Caller must treat missing terminal as possibly spent.
        terminal = dict(terminal)
        terminal["persistence_state"] = "terminal_persist_failed_possibly_spent"
    return terminal


def classify_http(
    http_status: int, headers: Mapping[str, str], receipt_ns: int
) -> tuple[str, int | None]:
    if type(http_status) is not int or not 100 <= http_status <= 599:
        raise ValueError("gecko_http_status")
    if http_status == 200:
        return STATUS_RECEIVED, None
    if http_status in {404, 410, 422}:
        return STATUS_UNSUPPORTED, None
    if http_status == 429:
        return STATUS_RATE_LIMITED, _retry_after_seconds(headers, receipt_ns)
    if 400 <= http_status < 500:
        return STATUS_CLIENT_ERROR, None
    if 500 <= http_status < 600:
        return STATUS_PROVIDER_ERROR, None
    return STATUS_PROVIDER_ERROR, None


def cooldown_until_ns(
    status: str, receipt_ns: int, retry_after_s: int | None
) -> tuple[int | None, str | None]:
    if type(receipt_ns) is not int or receipt_ns < 0:
        raise ValueError("gecko_receipt_clock")
    if status == STATUS_RATE_LIMITED:
        delay = max(3600, retry_after_s or 0)
        if delay > 86400:
            return None, "review_required_rate_limit"
        return receipt_ns + delay * NS, "rate_limit_cooldown"
    if status in {STATUS_PROVIDER_ERROR, STATUS_TRANSPORT}:
        return receipt_ns + 900 * NS, "provider_or_transport_cooldown"
    if status == STATUS_CLIENT_ERROR:
        return None, "contract_access_review_required"
    return None, None


def parse_response(raw: bytes, bucket_close_ns: int, received_ns: int) -> list[dict[str, Any]]:
    """Parse raw JSON but preserve row order and classify every row against declared windows."""
    if not isinstance(raw, bytes) or type(received_ns) is not int or received_ns < 0:
        raise ValueError("gecko_response_input")
    value = _strict_json(raw)
    try:
        rows = value["data"]["attributes"]["ohlcv_list"]
    except (KeyError, TypeError) as exc:
        raise ValueError("gecko_response_shape") from exc
    if not isinstance(rows, list):
        raise ValueError("gecko_response_rows")
    if len(rows) > 6:
        raise ValueError("gecko_response_row_cap")
    close_s = bucket_close_ns // NS
    received_s = received_ns // NS
    result = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, list) or len(row) != 6:
            raise ValueError("gecko_bar_shape")
        open_s = row[0]
        if type(open_s) is not int or open_s % BAR_SECONDS or open_s in seen:
            raise ValueError("gecko_bar_open")
        seen.add(open_s)
        values = row[1:]
        try:
            numbers = [float(number) for number in values]
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError("gecko_bar_numeric") from exc
        if any(type(number) not in {int, float} for number in values) or any(
            not math.isfinite(number) for number in numbers
        ):
            raise ValueError("gecko_bar_numeric")
        if any(number <= 0 for number in numbers[:4]) or numbers[4] < 0:
            raise ValueError("gecko_bar_value")
        if numbers[1] < max(numbers[0], numbers[3]) or numbers[2] > min(numbers[0], numbers[3]):
            raise ValueError("gecko_bar_ohlc_order")
        if close_s - 900 <= open_s < close_s:
            region = "new_open"
        elif close_s - 1800 <= open_s < close_s - 900:
            region = "overlap_revision"
        else:
            region = "out_of_window"
        is_closed = open_s + BAR_SECONDS <= received_s
        if not is_closed:
            disposition = "excluded_unclosed"
        elif region == "out_of_window":
            disposition = "excluded_out_of_window"
        else:
            disposition = "eligible_" + region
        result.append(
            {
                "open_s": open_s,
                "event_time_ns": open_s * NS,
                "source_available_ns": received_ns,
                "open": numbers[0],
                "high": numbers[1],
                "low": numbers[2],
                "close": numbers[3],
                "volume": numbers[4],
                "disposition": disposition,
            }
        )
    return result


def _terminal(
    intent: dict[str, Any],
    status: str,
    receipt_ns: int,
    http_status: int | None,
    body: bytes,
    response_sha256: str | None,
    retry_after_s: int | None,
    bars: list[dict[str, Any]],
    request_started_ns: int,
    *,
    response_length: int | None = None,
) -> dict[str, Any]:
    until, cooldown_reason = cooldown_until_ns(status, receipt_ns, retry_after_s)
    capture_state = (
        "raw_response_durable_candidate"
        if response_sha256 is not None
        else "response_over_budget_unretained"
        if status == STATUS_OVERSIZE
        else "no_response"
    )
    return {
        "schema": "brain-senses-gecko-terminal-v2",
        "response_schema": "brain-senses-gecko-response-v2",
        "response_capture_state": capture_state,
        "request_id": intent["request_id"],
        "intent_sha256": intent.get("intent_sha256"),
        "status": status,
        "http_status": http_status,
        "response_bytes": len(body) if response_length is None else response_length,
        "response_sha256": response_sha256,
        "receipt_ns": receipt_ns,
        "availability_ns": receipt_ns if response_sha256 is not None else None,
        "request_started_ns": request_started_ns,
        "retry_after_seconds": retry_after_s,
        "cooldown_until_ns": until,
        "cooldown_reason": cooldown_reason,
        "bars": bars,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }


def _retry_after_seconds(headers: Mapping[str, str], receipt_ns: int) -> int | None:
    raw = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    if raw is None:
        return None
    try:
        value = int(raw)
        return value if value >= 0 else None
    except (TypeError, ValueError):
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
        if when.tzinfo is None:
            return None
        receipt = dt.datetime.fromtimestamp(receipt_ns / NS, dt.UTC)
        return max(0, int((when - receipt).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


def _strict_json(raw: bytes) -> Any:
    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("gecko_duplicate_json_key")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=hook,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("gecko_response_json") from exc


def _verify_request(policy: dict[str, Any], request: dict[str, Any]) -> None:
    if not isinstance(request, dict) or request.get("schema") != "brain-senses-gecko-request-v2":
        raise ValueError("gecko_request_schema")
    pool_id = request.get("pool_id")
    bucket_close_ns = request.get("bucket_close_ns")
    if not isinstance(pool_id, str) or type(bucket_close_ns) is not int:
        raise ValueError("gecko_request_identity")
    expected = request_for_bucket(policy, pool_id, bucket_close_ns)
    if request != expected:
        raise ValueError("gecko_request_mismatch")
