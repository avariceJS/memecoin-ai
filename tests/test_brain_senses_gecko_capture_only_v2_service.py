from __future__ import annotations

import sys
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from tools import brain_senses_gecko_capture_only_v2_service as service  # noqa: E402
from tools import brain_senses_gecko_capture_only_v2_verify as verify  # noqa: E402
from tools import brain_senses_gecko_rotating_scout_v1 as scout  # noqa: E402
from tools.brain_senses_external_writer_v1 import canonical_json  # noqa: E402


def _request() -> dict[str, object]:
    return {
        "url": "https://api.geckoterminal.com/api/v2/networks/solana/pools/Pool123/ohlcv/minute?limit=6",
        "pool_id": "Pool123",
        "maximum_response_bytes": scout.MAX_RESPONSE_BYTES,
        "timeout_seconds": scout.HTTP_TIMEOUT_SECONDS,
        "headers": {"Accept": "application/json"},
    }


def test_fetch_once_disables_proxy_redirect_and_reads_only_limit_plus_one() -> None:
    response = Mock()
    response.status = 200
    response.headers.items.return_value = []
    response.read.return_value = b"x" * (scout.MAX_RESPONSE_BYTES + 1)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response

    with (
        patch.object(service.urllib.request, "build_opener", return_value=opener) as build,
        patch.object(service.urllib.request, "ProxyHandler") as proxy,
    ):
        fetched = service.fetch_once(_request())

    assert fetched.http_status == 200
    assert len(fetched.body or b"") == scout.MAX_RESPONSE_BYTES + 1
    build.assert_called_once()
    proxy.assert_called_once_with({})
    handlers = build.call_args.args
    assert any(type(handler).__name__ == "NoRedirect" for handler in handlers)
    opener.open.assert_called_once()
    assert opener.open.call_args.kwargs["timeout"] == scout.HTTP_TIMEOUT_SECONDS
    response.read.assert_called_once_with(scout.MAX_RESPONSE_BYTES + 1)


def test_fetch_once_preserves_duplicate_retry_after_header_evidence() -> None:
    response = Mock()
    response.status = 429
    headers = Message()
    headers.add_header("Retry-After", "12")
    headers.add_header("Retry-After", "24")
    response.headers = headers
    response.read.return_value = b"limited"
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    with patch.object(service.urllib.request, "build_opener", return_value=opener):
        fetched = service.fetch_once(_request())
    assert fetched.retry_after_count == 2
    assert fetched.retry_after_values == ("12", "24")
    assert fetched.retry_after_lengths == (2, 2)


def test_fetch_once_converts_response_read_failure_to_transport_uncertainty() -> None:
    response = Mock()
    response.status = 200
    response.headers.items.return_value = []
    response.read.side_effect = TimeoutError("details must not escape")
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = response
    with patch.object(service.urllib.request, "build_opener", return_value=opener):
        fetched = service.fetch_once(_request())
    assert fetched.http_status == 200
    assert fetched.body is None
    assert fetched.transport_error_class == "TimeoutError"


@pytest.mark.parametrize(
    "change",
    [
        {"url": "http://api.geckoterminal.com/api/v2/networks/solana/pools/Pool123/ohlcv/minute"},
        {"url": "https://evil.example/api/v2/networks/solana/pools/Pool123/ohlcv/minute"},
        {"maximum_response_bytes": 99},
        {"timeout_seconds": 600},
    ],
)
def test_fetch_once_rejects_unpinned_transport_shape(change: dict[str, object]) -> None:
    request = _request() | change
    with patch.object(service.urllib.request, "build_opener") as build:
        with pytest.raises(service.ServiceError):
            service.fetch_once(request)
    build.assert_not_called()


def _terminal_inputs() -> tuple[dict[str, object], dict[str, object]]:
    request = {
        "bucket_close_ns": 1_800_000_000_000_000_000,
        "plan_record_sha256": "a" * 64,
    }
    intent = {"request_id": "b" * 64, "intent_sha256": "c" * 64, "request": request}
    dispatch = {"dispatch_ns": 1_800_000_060_000_000_000, "record_sha256": "d" * 64}
    return intent, dispatch


def test_terminal_classifies_uncertain_transport_without_body_or_retry() -> None:
    intent, dispatch = _terminal_inputs()
    receipt_ns = 1_800_000_061_000_000_000
    terminal, ambiguous = service.terminal_envelope(
        intent=intent,
        dispatch=dispatch,
        request_started_ns=dispatch["dispatch_ns"],
        receipt_ns=receipt_ns,
        fetched=service.FetchResult(None, None, {}, "TimeoutError"),
    )
    assert ambiguous is True
    assert terminal["status"] == "transport_uncertain_possibly_spent"
    assert terminal["raw_body_b64"] is None
    assert terminal["provider_charge_status"] == "unknown_unverified"
    assert terminal["cooldown_until_ns"] == receipt_ns + 900 * scout.NS
    assert len(canonical_json(terminal)) <= 64 * 1024


def test_terminal_rate_limit_respects_longer_retry_after_and_stops() -> None:
    intent, dispatch = _terminal_inputs()
    receipt_ns = 1_800_000_061_000_000_000
    terminal, ambiguous = service.terminal_envelope(
        intent=intent,
        dispatch=dispatch,
        request_started_ns=dispatch["dispatch_ns"],
        receipt_ns=receipt_ns,
        fetched=service.FetchResult(429, b"limited", {"Retry-After": "3600"}),
    )
    assert ambiguous is False
    assert terminal["status"] == "rate_limited"
    assert terminal["stop_reason"] == "rate_limited"
    assert terminal["cooldown_until_ns"] == receipt_ns + 3600 * scout.NS
    assert terminal["response_sha256"] == verify.sha256(b"limited")


@pytest.mark.parametrize(
    ("header", "expected_class", "expected_seconds"),
    [
        (None, "absent", None),
        ("0", "valid", 0),
        ("3600", "valid", 3600),
        ("86400", "valid", 86400),
        ("86401", "over_24h", None),
        ("9" * 1000, "oversize", None),
        ("-1", "malformed", None),
        ("+1", "malformed", None),
        ("1.0", "malformed", None),
        ("1e3", "malformed", None),
        ("١", "malformed", None),
        ("1 0", "malformed", None),
        ("Wed, 21 Oct 2015 07:28:00 GMT", "malformed", None),
    ],
)
def test_retry_after_is_strict_bounded_and_never_clamped(
    header: str | None, expected_class: str, expected_seconds: int | None
) -> None:
    intent, dispatch = _terminal_inputs()
    headers = {} if header is None else {"Retry-After": header}
    terminal, _ = service.terminal_envelope(
        intent=intent,
        dispatch=dispatch,
        request_started_ns=dispatch["dispatch_ns"],
        receipt_ns=1_800_000_061_000_000_000,
        fetched=service.FetchResult(429, None, headers),
    )
    assert terminal["status"] == "rate_limited"
    assert terminal["retry_after_class"] == expected_class
    assert terminal["retry_after_seconds"] == expected_seconds
    if expected_class in {"over_24h", "oversize", "malformed"}:
        assert terminal["stop_reason"] == "review_required_retry_after"
        assert terminal["cooldown_until_ns"] is None
    else:
        assert terminal["stop_reason"] == "rate_limited"
        assert terminal["cooldown_until_ns"] == 1_800_000_061_000_000_000 + max(
            3600, expected_seconds or 0
        ) * scout.NS


def test_retry_after_duplicate_requires_review() -> None:
    intent, dispatch = _terminal_inputs()
    terminal, _ = service.terminal_envelope(
        intent=intent,
        dispatch=dispatch,
        request_started_ns=dispatch["dispatch_ns"],
        receipt_ns=1_800_000_061_000_000_000,
        fetched=service.FetchResult(
            429,
            b"x" * (scout.MAX_RESPONSE_BYTES + 1),
            {},
            retry_after_values=("0", "1"),
            retry_after_count=2,
            retry_after_lengths=(1, 1),
        ),
    )
    assert terminal["raw_body_b64"] is None
    assert terminal["stop_reason"] == "review_required_retry_after"
    assert terminal["cooldown_until_ns"] is None


@pytest.mark.parametrize("status", [408, 500, 503])
def test_server_errors_stop_for_at_least_fifteen_minutes(status: int) -> None:
    intent, dispatch = _terminal_inputs()
    receipt = 1_800_000_061_000_000_000
    terminal, _ = service.terminal_envelope(
        intent=intent,
        dispatch=dispatch,
        request_started_ns=dispatch["dispatch_ns"],
        receipt_ns=receipt,
        fetched=service.FetchResult(status, None, {}),
    )
    assert terminal["status"] == "provider_error"
    assert terminal["cooldown_until_ns"] == receipt + 900 * scout.NS


def test_server_error_honors_longer_retry_after_and_malformed_requires_review() -> None:
    intent, dispatch = _terminal_inputs()
    receipt = 1_800_000_061_000_000_000
    long_retry, _ = service.terminal_envelope(
        intent=intent,
        dispatch=dispatch,
        request_started_ns=dispatch["dispatch_ns"],
        receipt_ns=receipt,
        fetched=service.FetchResult(503, None, {"Retry-After": "3600"}),
    )
    assert long_retry["cooldown_until_ns"] == receipt + 3600 * scout.NS
    malformed, _ = service.terminal_envelope(
        intent=intent,
        dispatch=dispatch,
        request_started_ns=dispatch["dispatch_ns"],
        receipt_ns=receipt,
        fetched=service.FetchResult(503, None, {"Retry-After": "1.5"}),
    )
    assert malformed["stop_reason"] == "review_required_retry_after"
    assert malformed["cooldown_until_ns"] is None


def test_terminal_oversize_is_possibly_spent_and_body_is_not_persisted() -> None:
    intent, dispatch = _terminal_inputs()
    body = b"x" * (scout.MAX_RESPONSE_BYTES + 1)
    terminal, ambiguous = service.terminal_envelope(
        intent=intent,
        dispatch=dispatch,
        request_started_ns=dispatch["dispatch_ns"],
        receipt_ns=1_800_000_061_000_000_000,
        fetched=service.FetchResult(200, body, {}),
    )
    assert ambiguous is True
    assert terminal["status"] == "response_over_budget"
    assert terminal["raw_body_b64"] is None
    assert terminal["response_bytes"] is None
