"""Bounded single-attempt Gecko v2 transport helpers.

This module has no automatic retry, authentication, redirect following, or alternate storage.
"""

from __future__ import annotations

import base64
import http.client
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, NamedTuple, cast

from tools import brain_senses_gecko_capture_only_v2_verify as verify
from tools import brain_senses_gecko_lane_engine_v3 as engine
from tools import brain_senses_gecko_rotating_scout_v1 as scout
from tools.brain_senses_external_writer_v1 import canonical_json

_RETRY_AFTER = re.compile(r"[0-9]+\Z")
_RETRY_AFTER_CAPTURE_MAX = 64


class FetchResult(NamedTuple):
    http_status: int | None
    body: bytes | None
    headers: Any
    transport_error_class: str | None = None
    retry_after_values: tuple[str, ...] | None = None
    retry_after_count: int | None = None
    retry_after_lengths: tuple[int, ...] | None = None


class ServiceError(ValueError):
    """Invalid request or terminal envelope."""


def _retry_after_occurrences(headers: Any) -> tuple[tuple[str, ...], int, tuple[int, ...]]:
    getter = getattr(headers, "get_all", None)
    values = getter("Retry-After") if callable(getter) else None
    if not isinstance(values, (list, tuple)):
        values = [
            value for key, value in headers.items() if key.lower() == "retry-after"
        ]
    values = [value if isinstance(value, str) else "" for value in values]
    return (
        tuple(value[:_RETRY_AFTER_CAPTURE_MAX] for value in values[:2]),
        len(values),
        tuple(len(value) for value in values[:2]),
    )


def _retry_after_evidence(
    fetched: FetchResult,
) -> tuple[int | None, str, list[dict[str, Any]], int]:
    values = fetched.retry_after_values
    count = fetched.retry_after_count
    lengths = fetched.retry_after_lengths
    if values is None or count is None:
        values, count, lengths = _retry_after_occurrences(fetched.headers)
    if lengths is None:
        lengths = tuple(len(value) for value in values)
    if len(lengths) != len(values):
        lengths = tuple(len(value) for value in values)
    evidence = [
        {
            "captured": value,
            "original_length": lengths[index],
        }
        for index, value in enumerate(values)
    ]
    if count > 1:
        return None, "duplicate", evidence, count
    if count == 0:
        return None, "absent", evidence, count
    if len(evidence) != 1:
        return None, "oversize", evidence, count
    entry = evidence[0]
    raw = cast(str, entry["captured"])
    token = raw.strip(" \t")
    original_length = cast(int, entry["original_length"])
    if original_length > _RETRY_AFTER_CAPTURE_MAX or len(token) > _RETRY_AFTER_CAPTURE_MAX:
        return None, "oversize", evidence, count
    if _RETRY_AFTER.fullmatch(token) is None:
        return None, "malformed", evidence, count
    normalized = token.lstrip("0") or "0"
    if len(normalized) > 5:
        return None, "over_24h", evidence, count
    seconds = int(normalized)
    if seconds > 86_400:
        return None, "over_24h", evidence, count
    return seconds, "valid", evidence, count


def fetch_once(request: Mapping[str, Any]) -> FetchResult:
    """Issue exactly one bounded HTTPS GET, rejecting redirects and system proxies."""
    url = request.get("url")
    parsed = urllib.parse.urlsplit(url) if isinstance(url, str) else None
    pool_id = request.get("pool_id")
    maximum = request.get("maximum_response_bytes")
    timeout = request.get("timeout_seconds")
    if (
        parsed is None
        or parsed.scheme != "https"
        or parsed.hostname != "api.geckoterminal.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
        or type(pool_id) is not str
        or parsed.path != f"/api/v2/networks/solana/pools/{pool_id}/ohlcv/minute"
        or type(maximum) is not int
        or maximum != scout.MAX_RESPONSE_BYTES
        or type(timeout) is not int
        or timeout != scout.HTTP_TIMEOUT_SECONDS
        or type(request.get("headers")) is not dict
    ):
        raise ServiceError("gecko_v2_request_binding")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(
            self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
        ) -> None:
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    http_request = urllib.request.Request(
        cast(str, url), headers=dict(request["headers"]), method="GET"
    )
    try:
        response = opener.open(http_request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        retry_values, retry_count, retry_lengths = _retry_after_occurrences(exc.headers)
        try:
            body = exc.read(maximum + 1)
            return FetchResult(
                exc.code, body, exc.headers, None, retry_values, retry_count, retry_lengths
            )
        except OSError as read_error:
            return FetchResult(
                exc.code,
                None,
                exc.headers,
                read_error.__class__.__name__,
                retry_values,
                retry_count,
                retry_lengths,
            )
        finally:
            exc.close()
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return FetchResult(None, None, {}, exc.__class__.__name__)
    try:
        with response:
            headers = response.headers
            retry_values, retry_count, retry_lengths = _retry_after_occurrences(headers)
            body = response.read(maximum + 1)
            return FetchResult(
                response.status, body, headers, None, retry_values, retry_count, retry_lengths
            )
    except (OSError, http.client.HTTPException, TimeoutError) as exc:
        return FetchResult(
            response.status, None, response.headers, exc.__class__.__name__,
            retry_values, retry_count, retry_lengths,
        )


def _retry_after(headers: Mapping[str, str]) -> int | None:
    value = next((v for key, v in headers.items() if key.lower() == "retry-after"), None)
    if not isinstance(value, str) or _RETRY_AFTER.fullmatch(value.strip()) is None:
        return None
    parsed = int(value.strip())
    return min(parsed, 86_400)


def terminal_envelope(
    *,
    intent: Mapping[str, Any],
    dispatch: Mapping[str, Any],
    request_started_ns: int,
    receipt_ns: int,
    fetched: FetchResult,
) -> tuple[dict[str, Any], bool]:
    """Classify one response into the exact replayable v2 terminal schema."""
    request = intent.get("request")
    if (
        not isinstance(request, Mapping)
        or type(request_started_ns) is not int
        or type(receipt_ns) is not int
        or type(dispatch.get("dispatch_ns")) is not int
    ):
        raise ServiceError("gecko_v2_terminal_input")
    http_status = fetched.http_status
    response_body = fetched.body
    raw_body: bytes | None = response_body
    status: str
    (
        retry_after,
        retry_after_class,
        retry_after_evidence,
        retry_after_count,
    ) = _retry_after_evidence(fetched)
    ambiguous = False
    stop_reason: str | None = None
    cooldown_until: int | None = None
    bars: list[dict[str, Any]] = []

    if http_status == 429:
        status = "rate_limited"
        raw_body = (
            response_body
            if response_body is not None and len(response_body) <= scout.MAX_RESPONSE_BYTES
            else None
        )
        if retry_after_class in {"absent", "valid"}:
            stop_reason = "rate_limited"
            cooldown_until = receipt_ns + max(3600, retry_after or 0) * scout.NS
        else:
            stop_reason = "review_required_retry_after"
    elif http_status == 408 or (http_status is not None and http_status >= 500):
        status = "provider_error"
        raw_body = (
            response_body
            if response_body is not None and len(response_body) <= scout.MAX_RESPONSE_BYTES
            else None
        )
        if retry_after_class in {"absent", "valid"}:
            stop_reason = "provider_error"
            cooldown_until = receipt_ns + max(900, retry_after or 0) * scout.NS
        else:
            stop_reason = "review_required_retry_after"
    elif http_status is not None and 400 <= http_status < 500:
        status = "client_or_access_error"
        raw_body = (
            response_body
            if response_body is not None and len(response_body) <= scout.MAX_RESPONSE_BYTES
            else None
        )
        stop_reason = "client_or_access_error"
    elif fetched.transport_error_class is not None or http_status is None:
        status = "transport_uncertain_possibly_spent"
        raw_body = None
        ambiguous = True
        stop_reason = "transport_uncertain"
        cooldown_until = receipt_ns + 900 * scout.NS
    elif response_body is None:
        status = "transport_uncertain_possibly_spent"
        ambiguous = True
        stop_reason = "transport_uncertain"
        cooldown_until = receipt_ns + 900 * scout.NS
    elif len(response_body) > scout.MAX_RESPONSE_BYTES:
        status = "response_over_budget"
        raw_body = None
        ambiguous = True
        stop_reason = "response_over_budget"
    else:
        if 200 <= http_status < 300:
            try:
                parsed_bars = engine.parse_response(
                    response_body,
                    cast(int, request["bucket_close_ns"]),
                    receipt_ns,
                )
            except (ValueError, TypeError, KeyError, OverflowError):
                status = "malformed_response"
            else:
                status = "received" if parsed_bars else "empty_response"
                close_ns = cast(int, request["bucket_close_ns"])
                for bar in parsed_bars:
                    open_ns = bar["event_time_ns"]
                    region = (
                        "new_open"
                        if close_ns - 900 * scout.NS <= open_ns < close_ns
                        else "overlap_revision"
                        if close_ns - 1800 * scout.NS <= open_ns < close_ns - 900 * scout.NS
                        else "out_of_window"
                    )
                    bars.append(
                        {
                            key: bar[key]
                            for key in (
                                "open_s",
                                "event_time_ns",
                                "open",
                                "high",
                                "low",
                                "close",
                                "volume",
                            )
                        }
                        | {"region": region}
                    )
        else:
            status = "client_or_access_error"
            stop_reason = "client_or_access_error"

    raw_b64 = base64.b64encode(raw_body).decode("ascii") if raw_body is not None else None
    terminal = {
        "schema": verify.TERMINAL_SCHEMA,
        "schema_version": 1,
        "request_id": intent["request_id"],
        "intent_sha256": intent["intent_sha256"],
        "plan_sha256": request["plan_record_sha256"],
        "dispatch_record_sha256": dispatch["record_sha256"],
        "dispatch_ns": dispatch["dispatch_ns"],
        "request_started_ns": request_started_ns,
        "receipt_ns": receipt_ns,
        "status": status,
        "http_status": http_status,
        "transport_error_class": fetched.transport_error_class,
        "retry_after_seconds": retry_after,
        "retry_after_header_count": retry_after_count,
        "retry_after_evidence": retry_after_evidence,
        "retry_after_class": retry_after_class,
        "cooldown_until_ns": cooldown_until,
        "stop_reason": stop_reason,
        "provider_io": True,
        "raw_body_b64": raw_b64,
        "response_bytes": len(raw_body) if raw_body is not None else None,
        "response_sha256": verify.sha256(raw_body) if raw_body is not None else None,
        "bars": bars,
        "provider_charge_units": None,
        "provider_charge_status": "unknown_unverified",
    }
    if len(canonical_json(terminal)) > 64 * 1024:
        raise ServiceError("gecko_v2_terminal_over_budget")
    return terminal, ambiguous


__all__ = ["FetchResult", "ServiceError", "fetch_once", "terminal_envelope"]
