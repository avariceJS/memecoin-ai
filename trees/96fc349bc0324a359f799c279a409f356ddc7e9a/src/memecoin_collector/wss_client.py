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
from typing import Protocol

from memecoin_collector.wss_campaign import WssReferenceCampaign
from memecoin_collector.wss_reference import (
    DISK_FLOOR_BYTES,
    MAX_CONTROL_RESPONSE_BYTES,
    MAX_SESSION_NS,
    PUMP_BONDING_CURVE_PROGRAM,
)
from memecoin_collector.wss_tls import TLSVerification
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


class DeadlineByteStream(Protocol):
    """A TLS-protected stream with deadline-aware exact reads and complete writes."""

    tls_verification: TLSVerification

    def read_exactly(self, size: int, *, deadline_monotonic_ns: int) -> bytes: ...

    def write_all(self, data: bytes, *, deadline_monotonic_ns: int) -> None: ...


class WssClientError(ValueError):
    """Sanitized protocol outcome; never includes endpoint, headers, or payload text."""

    def __init__(self, reason: str, *, evidence: bytes = b"", progress_bytes: int = 0) -> None:
        super().__init__(reason)
        self.evidence = evidence[:MAX_HTTP_HEADER_BYTES]
        self.progress_bytes = max(0, min(int(progress_bytes), 2**63 - 1))


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
        self.progress_bytes = 0

    def reset_progress(self) -> None:
        self.progress_bytes = 0

    def check(self) -> None:
        now_ns = self.clock_ns()
        if now_ns < self.session.last_event_ns:
            raise WssClientError("session_clock_regression")
        if now_ns >= self.authorization_expires_ns:
            raise WssClientError("authorization_expired")
        if self.monotonic_ns() >= self.deadline_ns:
            raise WssClientError("client_deadline")

    def read_exactly(self, size: int) -> bytes:
        try:
            self.check()
        except WssClientError as exc:
            raise WssClientError(exc.args[0], progress_bytes=self.progress_bytes) from None
        try:
            value = self.stream.read_exactly(size, deadline_monotonic_ns=self.deadline_ns)
        except TimeoutError as exc:
            raise WssClientError(
                "stream_timeout",
                progress_bytes=self.progress_bytes + getattr(exc, "bytes_completed", 0),
            ) from None
        except (OSError, EOFError) as exc:
            raise WssClientError(
                "stream_read_failed",
                progress_bytes=self.progress_bytes + getattr(exc, "bytes_completed", 0),
            ) from None
        if not isinstance(value, bytes) or len(value) != size:
            self.progress_bytes += len(value) if isinstance(value, bytes) else 0
            raise WssClientError("stream_read_incomplete", progress_bytes=self.progress_bytes)
        self.progress_bytes += len(value)
        if self.monotonic_ns() >= self.deadline_ns:
            raise WssClientError(
                "client_deadline", evidence=value, progress_bytes=self.progress_bytes
            )
        try:
            self.check()
        except WssClientError as exc:
            evidence = value if exc.args == ("client_deadline",) else b""
            raise WssClientError(
                exc.args[0], evidence=evidence, progress_bytes=self.progress_bytes
            ) from None
        return value

    def write_all(self, data: bytes) -> None:
        try:
            self.check()
        except WssClientError as exc:
            raise WssClientError(exc.args[0], progress_bytes=self.progress_bytes) from None
        try:
            self.stream.write_all(data, deadline_monotonic_ns=self.deadline_ns)
        except TimeoutError:
            raise WssClientError("stream_timeout", progress_bytes=self.progress_bytes) from None
        except (OSError, EOFError):
            raise WssClientError(
                "stream_write_failed", progress_bytes=self.progress_bytes
            ) from None
        try:
            self.check()
        except WssClientError as exc:
            raise WssClientError(exc.args[0], progress_bytes=self.progress_bytes) from None


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
        raise WssClientError(
            exc.args[0],
            evidence=bytes(data) + exc.evidence,
            progress_bytes=max(io.progress_bytes, exc.progress_bytes),
        ) from None
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
    progress_bytes: int = 0,
    at_ns: int,
) -> None:
    if campaign.active is not None and not campaign.terminal:
        at_ns = max(at_ns, campaign.active.last_event_ns)
        campaign.active.record_transport_failure(
            phase=phase,
            reason=reason,
            raw_evidence=evidence[:MAX_HTTP_HEADER_BYTES],
            progress_bytes=progress_bytes,
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
        stream: DeadlineByteStream,
        *,
        clock_ns: Callable[[], int],
        monotonic_ns: Callable[[], int],
        free_disk_bytes: Callable[[], int],
        deadline_ns: int,
    ) -> None:
        self.campaign = campaign
        self.session = campaign.active
        self.io = io
        self.stream = stream
        self.closed = False
        self.clock_ns = clock_ns
        self.monotonic_ns = monotonic_ns
        self.free_disk_bytes = free_disk_bytes
        self.deadline_ns = deadline_ns

    def _assert_session_owner(self) -> None:
        if self.closed:
            raise WssClientError("connection_closed")
        if self.campaign.active is not self.session:
            self._close_stream()
            raise WssClientError("connection_session_superseded")

    def _next_subscription_response(self) -> dict | None:
        self.io.reset_progress()
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
                    progress_bytes=self.io.progress_bytes,
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
                    progress_bytes=(
                        exc.progress_bytes
                        if isinstance(exc, WssClientError)
                        else self.io.progress_bytes
                    ),
                    at_ns=self.clock_ns(),
                )
                raise WssClientError(failure_reason) from None
            if result is None:
                continue
            kind, body = result
            if kind == "ping":
                _preflight_connection(
                    self.campaign,
                    clock_ns=self.clock_ns,
                    monotonic_ns=self.monotonic_ns,
                    free_disk_bytes=self.free_disk_bytes,
                    allow_connected=True,
                    allow_subscribe_sent=True,
                )
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
                        progress_bytes=self.io.progress_bytes,
                        at_ns=self.clock_ns(),
                    )
                    raise
                continue
            if kind == "pong":
                _preflight_connection(
                    self.campaign,
                    clock_ns=self.clock_ns,
                    monotonic_ns=self.monotonic_ns,
                    free_disk_bytes=self.free_disk_bytes,
                    allow_connected=True,
                    allow_subscribe_sent=True,
                )
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
            _preflight_connection(
                self.campaign,
                clock_ns=self.clock_ns,
                monotonic_ns=self.monotonic_ns,
                free_disk_bytes=self.free_disk_bytes,
                allow_connected=True,
                allow_subscribe_sent=True,
            )
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
            self._assert_session_owner()
            self.io.reset_progress()
            self.io.check()
            row = self.campaign.receive_json_message(
                self.io,
                at_ns=self.clock_ns(),
                free_disk_bytes=self.free_disk_bytes(),
                clock_ns=self.clock_ns,
                send_client_frame=self.io.write_all,
            )
            if self.campaign.terminal or self.campaign.active is None:
                self.close()
            return row
        except BaseException as exc:
            try:
                if (
                    self.campaign.active is self.session
                    and self.session is not None
                    and not self.campaign.terminal
                ):
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
                    progress_bytes = (
                        exc.progress_bytes
                        if isinstance(exc, WssClientError)
                        else self.io.progress_bytes
                        if not isinstance(exc, Exception)
                        else 0
                    )
                    if progress_bytes:
                        self.campaign.active.record_transport_failure(
                            phase="notification",
                            reason=(
                                reason
                                if reason
                                in {
                                    "client_deadline",
                                    "authorization_expired",
                                    "session_clock_regression",
                                }
                                else "notification_io"
                            ),
                            raw_evidence=b"",
                            progress_bytes=progress_bytes,
                            at_ns=max(self.clock_ns(), self.campaign.active.last_event_ns),
                        )
                    if not isinstance(exc, Exception):
                        stop_reason = "client_cancelled"
                    elif isinstance(exc, (TimeoutError, OSError, EOFError, WssClientError)):
                        stop_reason = reason
                    else:
                        stop_reason = "notification_client_failure"
                    self.campaign.stop(
                        stop_reason,
                        max(self.clock_ns(), self.campaign.active.last_event_ns),
                    )
            finally:
                self._close_stream()
            if not isinstance(exc, Exception):
                raise
            raise WssClientError("notification_transport_failed") from None

    def _close_stream(self) -> None:
        if self.closed:
            return
        self.closed = True
        close = getattr(self.stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def close(self) -> None:
        """Seal this observation window and close TLS without an unwrap frame."""
        if self.closed:
            return
        try:
            if (
                self.campaign.active is self.session
                and self.session is not None
                and not self.campaign.terminal
            ):
                active = self.session
                self.campaign.stop_session(
                    "connection_disconnected",
                    max(self.clock_ns(), active.last_event_ns),
                )
        finally:
            self._close_stream()

    def __enter__(self) -> WssClientConnection:
        if self.closed:
            raise WssClientError("connection_closed")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


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
    """Transfer stream ownership only to a successfully subscribed connection."""
    session = campaign.active
    try:
        connection = _upgrade_and_subscribe_owned(
            campaign,
            stream,
            host=host,
            request_target=request_target,
            clock_ns=clock_ns,
            monotonic_ns=monotonic_ns,
            free_disk_bytes=free_disk_bytes,
        )
    except BaseException:
        _close_stream(stream)
        if session is not None and campaign.active is session and not campaign.terminal:
            campaign.stop(
                "client_failure",
                max(clock_ns(), session.last_event_ns),
            )
        raise
    if connection is None:
        _close_stream(stream)
    return connection


def _upgrade_and_subscribe_owned(
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
        _close_stream(stream)
        raise WssClientError("campaign_session_missing")
    try:
        deadline_ns = _preflight_connection(
            campaign,
            clock_ns=clock_ns,
            monotonic_ns=monotonic_ns,
            free_disk_bytes=free_disk_bytes,
        )
    except WssClientError:
        _close_stream(stream)
        raise
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
        _close_stream(stream)
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
            progress_bytes=exc.progress_bytes,
            at_ns=clock_ns(),
        )
        _close_stream(stream)
        raise WssClientError("http_upgrade_failed") from None
    try:
        deadline_ns = _preflight_connection(
            campaign,
            clock_ns=clock_ns,
            monotonic_ns=monotonic_ns,
            free_disk_bytes=free_disk_bytes,
            allow_connected=True,
        )
    except WssClientError:
        _close_stream(stream)
        raise
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
        _close_stream(stream)
        raise WssClientError("subscribe_request_failed") from None
    connection = WssClientConnection(
        campaign,
        io,
        stream,
        clock_ns=clock_ns,
        monotonic_ns=monotonic_ns,
        free_disk_bytes=free_disk_bytes,
        deadline_ns=deadline_ns,
    )
    try:
        response = connection._next_subscription_response()
    except WssClientError:
        connection._close_stream()
        raise
    if response is None:
        connection._close_stream()
        return None
    return connection


def _close_stream(stream: DeadlineByteStream) -> None:
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


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
