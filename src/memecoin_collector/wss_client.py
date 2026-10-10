"""Injectable, offline-testable WebSocket Upgrade and logsSubscribe client boundary.

The caller must supply a TLS-protected byte stream whose adapter has verified the
peer certificate and hostname. This module does not open sockets, create TLS, load
credentials, or contact a provider.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from memecoin_collector.wss_campaign import WssReferenceCampaign
from memecoin_collector.wss_reference import (
    DISK_FLOOR_BYTES,
    MAX_CONTROL_RESPONSE_BYTES,
    MAX_SESSION_NS,
    PUMP_BONDING_CURVE_PROGRAM,
)
from memecoin_collector.wss_transport import (
    FrameProtocolError,
    FrameTooLarge,
    ServerFrame,
    WebSocketMessageAssembler,
    encode_client_frame,
    read_server_frame_header,
)

MAX_HTTP_HEADER_BYTES = 16 * 1024
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


@dataclass(frozen=True)
class TLSVerification:
    """Adapter assertion; a production adapter must derive this from verified TLS state."""

    peer_hostname: str
    certificate_verified: bool
    hostname_verified: bool


class DeadlineByteStream(Protocol):
    """A TLS-protected stream with deadline-aware exact reads and complete writes."""

    tls_verification: TLSVerification

    def read_exactly(self, size: int, *, deadline_monotonic_ns: int) -> bytes: ...

    def write_all(self, data: bytes, *, deadline_monotonic_ns: int) -> None: ...


class WssClientError(ValueError):
    """Sanitized protocol outcome; never includes endpoint, headers, or payload text."""

    def __init__(self, reason: str, *, evidence: bytes = b"") -> None:
        super().__init__(reason)
        self.evidence = evidence[:MAX_HTTP_HEADER_BYTES]


class _DeadlineIO:
    def __init__(
        self,
        stream,
        *,
        deadline_ns: int,
        monotonic_ns: Callable[[], int],
        clock_ns: Callable[[], int],
        authorization_expires_ns: int,
        session,
    ) -> None:
        self.stream = stream
        self.deadline_ns = deadline_ns
        self.monotonic_ns = monotonic_ns
        self.clock_ns = clock_ns
        self.authorization_expires_ns = authorization_expires_ns
        self.session = session

    def check(self) -> None:
        now_ns = self.clock_ns()
        if now_ns < self.session.last_event_ns:
            raise WssClientError("session_clock_regression")
        if now_ns >= self.authorization_expires_ns:
            raise WssClientError("authorization_expired")
        if self.monotonic_ns() >= self.deadline_ns:
            raise WssClientError("client_deadline")

    def read_exactly(self, size: int) -> bytes:
        self.check()
        try:
            value = self.stream.read_exactly(size, deadline_monotonic_ns=self.deadline_ns)
        except TimeoutError as exc:
            raise WssClientError("stream_timeout") from exc
        except (OSError, EOFError) as exc:
            raise WssClientError("stream_read_failed") from exc
        if not isinstance(value, bytes) or len(value) != size:
            raise WssClientError("stream_read_incomplete")
        if self.monotonic_ns() >= self.deadline_ns:
            raise WssClientError("client_deadline", evidence=value)
        try:
            self.check()
        except WssClientError as exc:
            evidence = value if exc.args == ("client_deadline",) else b""
            raise WssClientError(exc.args[0], evidence=evidence) from None
        return value

    def write_all(self, data: bytes) -> None:
        self.check()
        try:
            self.stream.write_all(data, deadline_monotonic_ns=self.deadline_ns)
        except TimeoutError as exc:
            raise WssClientError("stream_timeout") from exc
        except (OSError, EOFError) as exc:
            raise WssClientError("stream_write_failed") from exc
        self.check()


def _validate_authority(host: str, target: str, tls: TLSVerification) -> None:
    if (
        not isinstance(host, str)
        or not host
        or any(ord(c) < 33 or ord(c) > 126 or c in "\r\n/@" for c in host)
        or not isinstance(target, str)
        or not target.startswith("/")
        or any(ord(c) < 32 or ord(c) > 126 or c in "\r\n #" for c in target)
        or not isinstance(tls, TLSVerification)
        or tls.peer_hostname.casefold() != host.split(":", 1)[0].casefold()
        or tls.certificate_verified is not True
        or tls.hostname_verified is not True
    ):
        raise WssClientError("tls_or_upgrade_authority_invalid")


def _upgrade_request(host: str, target: str, key: str) -> bytes:
    return (
        f"GET {target} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).encode("ascii")


def _read_upgrade_headers(io: _DeadlineIO) -> bytes:
    data = bytearray()
    try:
        while len(data) < MAX_HTTP_HEADER_BYTES:
            data.extend(io.read_exactly(1))
            if data.endswith(b"\r\n\r\n"):
                return bytes(data)
    except WssClientError as exc:
        raise WssClientError(exc.args[0], evidence=bytes(data) + exc.evidence) from None
    raise WssClientError("http_upgrade_headers_too_large", evidence=bytes(data))


def _validate_upgrade(response: bytes, *, key: str) -> None:
    try:
        lines = response[:-4].decode("ascii").split("\r\n")
    except UnicodeDecodeError as exc:
        raise WssClientError("http_upgrade_headers_invalid") from exc
    if not lines or lines[0] != "HTTP/1.1 101 Switching Protocols":
        raise WssClientError("http_upgrade_status_invalid")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or line[0] in " \t" or ":" not in line:
            raise WssClientError("http_upgrade_headers_invalid")
        name, value = line.split(":", 1)
        name, value = name.strip().lower(), value.strip()
        if not name or name in headers or any(ord(c) < 32 and c != "\t" for c in value):
            raise WssClientError("http_upgrade_headers_invalid")
        headers[name] = value
    expected_accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    connection_tokens = {
        token.strip().lower() for token in headers.get("connection", "").split(",")
    }
    if (
        headers.get("upgrade", "").lower() != "websocket"
        or "upgrade" not in connection_tokens
        or headers.get("sec-websocket-accept") != expected_accept
        or "sec-websocket-extensions" in headers
        or "sec-websocket-protocol" in headers
        or "transfer-encoding" in headers
        or headers.get("content-length", "0") != "0"
    ):
        raise WssClientError("http_upgrade_binding_invalid")


def _safe_upgrade_failure_evidence(raw: bytes) -> bytes:
    """Keep a bounded status/header-name witness without persisting reflected values."""
    digest = hashlib.sha256(raw).hexdigest()
    try:
        lines = raw[:MAX_HTTP_HEADER_BYTES].decode("ascii").split("\r\n")
    except UnicodeDecodeError:
        lines = []
    status = "unknown"
    if lines:
        parts = lines[0].split(" ", 2)
        if len(parts) >= 2 and parts[0] == "HTTP/1.1" and len(parts[1]) == 3 and parts[1].isdigit():
            status = parts[1]
    known_names = {
        "connection",
        "content-length",
        "sec-websocket-accept",
        "sec-websocket-extensions",
        "sec-websocket-protocol",
        "transfer-encoding",
        "upgrade",
    }
    header_names = sorted(
        {
            name.strip().lower()
            for line in lines[1:]
            if ":" in line
            for name in [line.split(":", 1)[0]]
            if name.strip().lower() in known_names
        }
    )
    summary = f"HTTP/1.1 {status}\nheader_names={','.join(header_names)}\n"
    return f"{summary}raw_bytes={len(raw)} sha256={digest}\n".encode("ascii")[:1024]


def _stop_with_failure(
    campaign: WssReferenceCampaign,
    *,
    phase: str,
    reason: str,
    evidence: bytes,
    at_ns: int,
) -> None:
    if campaign.active is not None and not campaign.terminal:
        at_ns = max(at_ns, campaign.active.last_event_ns)
        campaign.active.record_transport_failure(
            phase=phase,
            reason=reason,
            raw_evidence=evidence[:MAX_HTTP_HEADER_BYTES],
            at_ns=at_ns,
        )
        campaign.stop(reason, at_ns)


def _io_failure_reason(io: _DeadlineIO, *, default: str) -> str:
    try:
        io.check()
    except WssClientError as exc:
        if exc.args and exc.args[0] in {
            "authorization_expired",
            "session_clock_regression",
            "client_deadline",
        }:
            return exc.args[0]
    return default


class WssClientConnection:
    """One deadline-bound testable protocol session over an injected TLS stream."""

    def __init__(
        self,
        campaign: WssReferenceCampaign,
        io: _DeadlineIO,
        *,
        clock_ns: Callable[[], int],
        monotonic_ns: Callable[[], int],
        free_disk_bytes: Callable[[], int],
        deadline_ns: int,
    ) -> None:
        self.campaign = campaign
        self.io = io
        self.clock_ns = clock_ns
        self.monotonic_ns = monotonic_ns
        self.free_disk_bytes = free_disk_bytes
        self.deadline_ns = deadline_ns

    def _next_subscription_response(self) -> dict | None:
        if not self.campaign.prepare_control_receive(
            at_ns=self.clock_ns(), free_disk_bytes=self.free_disk_bytes()
        ):
            return None
        assembler = WebSocketMessageAssembler(max_message_bytes=MAX_CONTROL_RESPONSE_BYTES)
        while True:
            try:
                header = read_server_frame_header(
                    self.io, max_payload_bytes=assembler.remaining_message_bytes
                )
                payload = self.io.read_exactly(header.payload_length)
                result = assembler.accept(ServerFrame(header, payload))
            except FrameTooLarge as exc:
                _stop_with_failure(
                    self.campaign,
                    phase="subscribe_response",
                    reason="subscribe_response_cap",
                    evidence=exc.header.raw,
                    at_ns=self.clock_ns(),
                )
                raise WssClientError("subscribe_response_cap") from None
            except (FrameProtocolError, WssClientError) as exc:
                if isinstance(exc, WssClientError) and exc.args == ("client_deadline",):
                    failure_reason = "client_deadline"
                elif isinstance(exc, WssClientError) and exc.args == ("authorization_expired",):
                    failure_reason = "authorization_expired"
                elif isinstance(exc, WssClientError) and exc.args == ("session_clock_regression",):
                    failure_reason = "session_clock_regression"
                elif isinstance(exc, WssClientError) and exc.args in {
                    ("stream_timeout",),
                    ("stream_read_failed",),
                }:
                    failure_reason = "subscribe_response_io"
                else:
                    failure_reason = "subscribe_response_invalid"
                _stop_with_failure(
                    self.campaign,
                    phase="subscribe_response",
                    reason=failure_reason,
                    evidence=exc.evidence if isinstance(exc, WssClientError) else b"",
                    at_ns=self.clock_ns(),
                )
                raise WssClientError(failure_reason) from None
            if result is None:
                continue
            kind, body = result
            _preflight_connection(
                self.campaign,
                clock_ns=self.clock_ns,
                monotonic_ns=self.monotonic_ns,
                free_disk_bytes=self.free_disk_bytes,
                allow_connected=True,
                allow_subscribe_sent=True,
            )
            if kind == "ping":
                try:
                    self.io.write_all(encode_client_frame(0xA, body))
                except WssClientError:
                    reason = _io_failure_reason(
                        self.io,
                        default="subscribe_response_io",
                    )
                    _stop_with_failure(
                        self.campaign,
                        phase="subscribe_response",
                        reason=reason,
                        evidence=b"",
                        at_ns=self.clock_ns(),
                    )
                    raise
                continue
            if kind == "pong":
                continue
            if kind == "close":
                self.campaign.active.record_transport_failure(
                    phase="subscribe_response",
                    reason="connection_disconnected",
                    raw_evidence=header.raw,
                    at_ns=self.clock_ns(),
                )
                self.campaign.stop_session("connection_disconnected", self.clock_ns())
                return None
            received_ns = self.clock_ns()
            try:
                row = self.campaign.record_subscribe_response(
                    body,
                    received_ns=received_ns,
                    clock_ns=self.clock_ns,
                )
            except ValueError:
                if self.campaign.active is not None and self.campaign.active.terminal:
                    stop = WssReferenceCampaign._last_stop(self.campaign.active)
                    self.campaign.stop(stop["reason"], max(self.clock_ns(), stop["at_ns"]))
                elif not self.campaign.terminal:
                    _stop_with_failure(
                        self.campaign,
                        phase="subscribe_response",
                        reason="subscribe_response_invalid",
                        evidence=body[:MAX_CONTROL_RESPONSE_BYTES],
                        at_ns=received_ns,
                    )
                raise WssClientError("subscribe_response_invalid") from None
            if row["response_kind"] == "ack":
                self.io.deadline_ns = _preflight_connection(
                    self.campaign,
                    clock_ns=self.clock_ns,
                    monotonic_ns=self.monotonic_ns,
                    free_disk_bytes=self.free_disk_bytes,
                    allow_connected=True,
                    allow_subscribe_sent=True,
                )
                self.campaign.active.acknowledge(
                    row["subscription_id"],
                    self.clock_ns(),
                    request_id=row["request_id"],
                    response_sha256=row["raw_sha256"],
                )
                return row
            if row["response_kind"] == "error":
                self.campaign.stop("subscription_error", self.clock_ns())
                raise WssClientError("subscription_error")
            self.campaign.stop("subscription_response_invalid", self.clock_ns())
            raise WssClientError("subscription_response_invalid")

    def receive_notification(self) -> dict | None:
        try:
            self.io.check()
            return self.campaign.receive_json_message(
                self.io,
                at_ns=self.clock_ns(),
                free_disk_bytes=self.free_disk_bytes(),
                clock_ns=self.clock_ns,
                send_client_frame=self.io.write_all,
            )
        except (TimeoutError, OSError, EOFError, WssClientError) as exc:
            if self.campaign.active is not None and not self.campaign.terminal:
                caught_reason = (
                    exc.args[0]
                    if isinstance(exc, WssClientError)
                    and exc.args
                    and exc.args[0]
                    in {
                        "authorization_expired",
                        "session_clock_regression",
                        "client_deadline",
                    }
                    else None
                )
                reason = _io_failure_reason(
                    self.io,
                    default=caught_reason
                    or (
                        "client_deadline"
                        if self.monotonic_ns() >= self.deadline_ns
                        else "subscribe_response_io"
                    ),
                )
                self.campaign.stop(
                    reason,
                    max(self.clock_ns(), self.campaign.active.last_event_ns),
                )
            raise WssClientError("notification_transport_failed") from None


def upgrade_and_subscribe(
    campaign: WssReferenceCampaign,
    stream: DeadlineByteStream,
    *,
    host: str,
    request_target: str,
    clock_ns: Callable[[], int],
    monotonic_ns: Callable[[], int],
    free_disk_bytes: Callable[[], int],
) -> WssClientConnection | None:
    """Perform a strict HTTP Upgrade and one logsSubscribe exchange on an injected stream."""
    if campaign.terminal or campaign.active is None:
        raise WssClientError("campaign_session_missing")
    deadline_ns = _preflight_connection(
        campaign,
        clock_ns=clock_ns,
        monotonic_ns=monotonic_ns,
        free_disk_bytes=free_disk_bytes,
    )
    try:
        _validate_authority(host, request_target, getattr(stream, "tls_verification", None))
    except WssClientError:
        _stop_with_failure(
            campaign,
            phase="http_upgrade",
            reason="http_upgrade_rejected",
            evidence=b"",
            at_ns=clock_ns(),
        )
        raise WssClientError("tls_or_upgrade_authority_invalid") from None
    active = campaign.active
    io = _DeadlineIO(
        stream,
        deadline_ns=deadline_ns,
        monotonic_ns=monotonic_ns,
        clock_ns=clock_ns,
        authorization_expires_ns=campaign.authorization["expires_at_ns"],
        session=active,
    )
    active.connection_opened(clock_ns())
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    header_bytes = b""
    try:
        io.write_all(_upgrade_request(host, request_target, key))
        header_bytes = _read_upgrade_headers(io)
        _validate_upgrade(header_bytes, key=key)
        active.record_upgrade_verified(clock_ns(), header_bytes)
    except WssClientError as exc:
        if exc.args == ("client_deadline",):
            reason = "client_deadline"
        elif exc.args == ("authorization_expired",):
            reason = "authorization_expired"
        elif exc.args == ("session_clock_regression",):
            reason = "session_clock_regression"
        elif exc.args in {("stream_timeout",), ("stream_read_failed",), ("stream_write_failed",)}:
            reason = "http_upgrade_io"
        else:
            reason = "http_upgrade_rejected"
        _stop_with_failure(
            campaign,
            phase="http_upgrade",
            reason=reason,
            evidence=_safe_upgrade_failure_evidence(header_bytes or exc.evidence),
            at_ns=clock_ns(),
        )
        raise WssClientError("http_upgrade_failed") from None
    deadline_ns = _preflight_connection(
        campaign,
        clock_ns=clock_ns,
        monotonic_ns=monotonic_ns,
        free_disk_bytes=free_disk_bytes,
        allow_connected=True,
    )
    io.deadline_ns = deadline_ns
    active.request_subscribe(clock_ns())
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "logsSubscribe",
        "params": [
            {"mentions": [PUMP_BONDING_CURVE_PROGRAM]},
            {"commitment": "finalized"},
        ],
    }
    try:
        io.write_all(encode_client_frame(0x1, json.dumps(request, separators=(",", ":")).encode()))
    except WssClientError as exc:
        reason = "client_deadline" if exc.args == ("client_deadline",) else "subscribe_response_io"
        _stop_with_failure(
            campaign,
            phase="subscribe_response",
            reason=reason,
            evidence=b"",
            at_ns=clock_ns(),
        )
        raise WssClientError("subscribe_request_failed") from None
    connection = WssClientConnection(
        campaign,
        io,
        clock_ns=clock_ns,
        monotonic_ns=monotonic_ns,
        free_disk_bytes=free_disk_bytes,
        deadline_ns=deadline_ns,
    )
    try:
        response = connection._next_subscription_response()
    except WssClientError:
        raise
    if response is None:
        return None
    return connection


def _preflight_connection(
    campaign: WssReferenceCampaign,
    *,
    clock_ns: Callable[[], int],
    monotonic_ns: Callable[[], int],
    free_disk_bytes: Callable[[], int],
    allow_connected: bool = False,
    allow_subscribe_sent: bool = False,
) -> int:
    """Fail closed before protocol I/O unless the existing session still fits its gates."""
    active = campaign.active
    if campaign.terminal or active is None:
        raise WssClientError("campaign_session_missing")
    now_ns = clock_ns()
    if now_ns < active.last_event_ns:
        campaign.stop("session_clock_regression", active.last_event_ns)
        raise WssClientError("session_clock_regression")
    if not (
        campaign.authorization["authorized_at_ns"]
        <= now_ns
        < campaign.authorization["expires_at_ns"]
    ):
        campaign.stop("authorization_expired", max(now_ns, active.last_event_ns))
        raise WssClientError("authorization_expired")
    if now_ns - active.opened_ns >= MAX_SESSION_NS:
        campaign.stop("session_duration_cap", now_ns)
        raise WssClientError("session_duration_cap")
    if (active.subscribe_sent and not allow_subscribe_sent) or (
        active.connected and not allow_connected
    ):
        campaign.stop("connection_already_open", now_ns)
        raise WssClientError("connection_already_open")
    measured_free_disk_bytes = free_disk_bytes()
    if type(measured_free_disk_bytes) is not int or measured_free_disk_bytes < DISK_FLOOR_BYTES:
        campaign.stop("disk_floor", now_ns)
        raise WssClientError("disk_floor")
    opened_monotonic_ns = active.opened_monotonic_ns
    now_monotonic_ns = monotonic_ns()
    if (
        type(now_monotonic_ns) is not int
        or now_monotonic_ns < 0
        or now_monotonic_ns < opened_monotonic_ns
    ):
        campaign.stop("client_deadline", now_ns)
        raise WssClientError("session_deadline_invalid")
    deadline_ns = opened_monotonic_ns + MAX_SESSION_NS
    if now_monotonic_ns >= deadline_ns:
        campaign.stop("client_deadline", now_ns)
        raise WssClientError("client_deadline")
    authorization_deadline_ns = now_monotonic_ns + (
        campaign.authorization["expires_at_ns"] - now_ns
    )
    return min(deadline_ns, authorization_deadline_ns)
