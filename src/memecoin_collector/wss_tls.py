"""Private endpoint loading and deadline-bound TLS streams for WSS.

This module deliberately does not resolve DNS or establish TCP connections. The
caller owns those preconditions; the TLS adapter owns the supplied socket once
handshake starts and closes it on every failed or completed path.
"""

from __future__ import annotations

import os
import socket
import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path
from select import select
from urllib.parse import urlsplit

from memecoin_collector.private_config import PrivateConfigError, read_private_config


@dataclass(frozen=True)
class TLSVerification:
    """Peer identity asserted only by the successful built-in TLS handshake."""

    peer_hostname: str
    certificate_verified: bool
    hostname_verified: bool
    _token: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._token is not _TLS_VERIFICATION_TOKEN:
            raise ValueError("tls_verification_not_handshake_bound")


REPO_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_CONFIG_PATH = REPO_ROOT / ".env.local"
PRIVATE_ENDPOINT_KEY = "MEMECOIN_HELIUS_RPC_URL"
MAX_CONFIG_BYTES = 16 * 1024
MAX_ENDPOINT_CHARS = 8 * 1024
ALLOWED_HOST_SUFFIX = ".helius-rpc.com"
ALLOWED_EXACT_HOSTS = {"rpc.helius.xyz"}
TLS_HANDSHAKE_TIMEOUT_REASON = "tls_handshake_timeout"
TLS_HANDSHAKE_FAILURE_REASON = "tls_handshake_failed"
TLS_READ_FAILURE_REASON = "tls_read_failed"
TLS_WRITE_FAILURE_REASON = "tls_write_failed"
TLS_DEADLINE_REASON = "tls_deadline"
TLS_KEYLOG_DISABLED_REASON = "tls_keylog_environment_forbidden"
_VERIFIED_STREAM_TOKEN = object()
_TLS_VERIFICATION_TOKEN = object()


@dataclass(frozen=True, repr=False)
class PrivateWssEndpoint:
    """Secret-bearing endpoint with an intentionally redacted representation."""

    host: str
    authority: str = field(repr=False)
    request_target: str = field(repr=False)
    _url: str = field(repr=False)

    def __repr__(self) -> str:
        return "PrivateWssEndpoint(<redacted>)"

    def __str__(self) -> str:
        return "PrivateWssEndpoint(<redacted>)"


def _private_endpoint_from_config(data: bytes) -> PrivateWssEndpoint:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("private_endpoint_config_invalid") from None
    found: list[str] = []
    for line in text.splitlines():
        key, sep, raw = line.partition("=")
        if sep and key.strip() == PRIVATE_ENDPOINT_KEY:
            value = raw.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            found.append(value)
    if len(found) != 1:
        raise ValueError("private_endpoint_config_invalid")
    value = found[0]
    if (
        not value
        or len(value) > MAX_ENDPOINT_CHARS
        or any(ord(char) < 33 or ord(char) > 126 for char in value)
    ):
        raise ValueError("private_endpoint_url_invalid")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("private_endpoint_url_invalid") from None
    if (
        parsed.scheme != "https"
        or not hostname
        or (hostname not in ALLOWED_EXACT_HOSTS and not hostname.endswith(ALLOWED_HOST_SUFFIX))
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (None, 443)
        or not parsed.netloc
        or parsed.netloc.count("@")
        or any(char in parsed.netloc for char in "\\%")
    ):
        raise ValueError("private_endpoint_url_invalid")

    # Keep the opaque path/query bytes as written in the private file. Do not
    # parse, decode, sort, or re-encode query parameters that may contain a key.
    authority_start = len("https://")
    authority_end = len(value)
    for delimiter in ("/", "?"):
        offset = value.find(delimiter, authority_start)
        if offset >= 0:
            authority_end = min(authority_end, offset)
    authority = value[authority_start:authority_end]
    suffix = value[authority_end:]
    if suffix.startswith("?"):
        request_target = "/" + suffix
    elif suffix:
        request_target = suffix
    else:
        request_target = "/"
    if not request_target.startswith("/") or "#" in request_target:
        raise ValueError("private_endpoint_url_invalid")
    return PrivateWssEndpoint(
        host=hostname.lower(),
        authority=authority,
        request_target=request_target,
        _url="wss://" + value[authority_start:],
    )


def load_private_endpoint() -> PrivateWssEndpoint:
    """Load only the designated mode-0600 research config via a verified fd."""
    try:
        data = read_private_config(PRIVATE_CONFIG_PATH, max_bytes=MAX_CONFIG_BYTES)
    except PrivateConfigError as exc:
        reasons = {
            "private_config_unavailable": "private_endpoint_config_unavailable",
            "private_config_permissions_invalid": "private_endpoint_config_permissions_invalid",
            "private_config_too_large": "private_endpoint_config_too_large",
            "private_config_changed_during_read": "private_endpoint_config_changed_during_read",
        }
        raise ValueError(reasons.get(str(exc), "private_endpoint_config_unavailable")) from None
    return _private_endpoint_from_config(data)


class WssTLSFailure(OSError):
    """Sanitized transport failure with bounded progress counts only."""

    def __init__(self, reason: str, *, bytes_completed: int = 0) -> None:
        super().__init__(reason)
        self.reason = reason
        self.bytes_completed = max(0, min(int(bytes_completed), 2**63 - 1))


class VerifiedTLSStream:
    """Nonblocking TLS byte stream with fixed peer verification and deadlines."""

    def __init__(
        self,
        tls_socket: ssl.SSLSocket,
        *,
        expected_hostname: str,
        token: object,
        monotonic_ns=time.monotonic_ns,
    ) -> None:
        if token is not _VERIFIED_STREAM_TOKEN:
            raise ValueError("verified_tls_stream_constructor_private")
        self._socket = tls_socket
        self._monotonic_ns = monotonic_ns
        self._closed = False
        self.tls_verification = TLSVerification(
            peer_hostname=expected_hostname,
            certificate_verified=True,
            hostname_verified=True,
            _token=_TLS_VERIFICATION_TOKEN,
        )

    @classmethod
    def handshake(
        cls,
        connected_tcp_socket: socket.socket,
        *,
        expected_hostname: str,
        deadline_monotonic_ns: int,
        monotonic_ns=time.monotonic_ns,
    ) -> VerifiedTLSStream:
        """Verify a peer over a caller-connected socket; ownership transfers now."""
        raw_socket = connected_tcp_socket
        tls_socket = None
        try:
            if "SSLKEYLOGFILE" in os.environ:
                raise WssTLSFailure(TLS_KEYLOG_DISABLED_REASON)
            if (
                not isinstance(expected_hostname, str)
                or not expected_hostname
                or any(ord(c) < 33 or ord(c) > 126 or c in "/:@?#\\" for c in expected_hostname)
                or type(deadline_monotonic_ns) is not int
                or deadline_monotonic_ns <= monotonic_ns()
            ):
                raise WssTLSFailure(TLS_DEADLINE_REASON)
            context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            if context.keylog_filename is not None:
                raise WssTLSFailure(TLS_KEYLOG_DISABLED_REASON)
            tls_socket = context.wrap_socket(
                raw_socket,
                server_hostname=expected_hostname,
                do_handshake_on_connect=False,
            )
            tls_socket.setblocking(False)
            while True:
                cls._check_deadline(deadline_monotonic_ns, monotonic_ns)
                try:
                    tls_socket.do_handshake()
                    break
                except ssl.SSLWantReadError:
                    cls._wait(
                        tls_socket,
                        want_read=True,
                        deadline_ns=deadline_monotonic_ns,
                        clock=monotonic_ns,
                    )
                except ssl.SSLWantWriteError:
                    cls._wait(
                        tls_socket,
                        want_read=False,
                        deadline_ns=deadline_monotonic_ns,
                        clock=monotonic_ns,
                    )
            cls._check_deadline(deadline_monotonic_ns, monotonic_ns)
            if not tls_socket.getpeercert():
                raise WssTLSFailure(TLS_HANDSHAKE_FAILURE_REASON)
            return cls(
                tls_socket,
                expected_hostname=expected_hostname,
                token=_VERIFIED_STREAM_TOKEN,
                monotonic_ns=monotonic_ns,
            )
        except WssTLSFailure:
            try:
                (tls_socket or raw_socket).close()
            except OSError:
                pass
            raise
        except BaseException as exc:
            try:
                (tls_socket or raw_socket).close()
            except OSError:
                pass
            if not isinstance(exc, (OSError, ssl.SSLError, ValueError)):
                raise
            reason = (
                TLS_HANDSHAKE_TIMEOUT_REASON
                if isinstance(exc, TimeoutError)
                else TLS_HANDSHAKE_FAILURE_REASON
            )
            raise WssTLSFailure(reason) from None

    @staticmethod
    def _check_deadline(deadline_ns: int, clock) -> None:
        if clock() >= deadline_ns:
            raise TimeoutError("deadline")

    @classmethod
    def _wait(cls, sock, *, want_read: bool, deadline_ns: int, clock) -> None:
        now_ns = clock()
        if now_ns >= deadline_ns:
            raise TimeoutError("deadline")
        remaining = (deadline_ns - now_ns) / 1_000_000_000
        try:
            readable, writable, _ = select(
                [sock] if want_read else [], [] if want_read else [sock], [], remaining
            )
        except (OSError, ValueError):
            raise OSError("tls_readiness_wait_failed") from None
        cls._check_deadline(deadline_ns, clock)
        if not (readable if want_read else writable):
            raise TimeoutError("deadline")

    def read_exactly(self, size: int, *, deadline_monotonic_ns: int) -> bytes:
        if self._closed or type(size) is not int or size < 0:
            raise WssTLSFailure(TLS_READ_FAILURE_REASON)
        result = bytearray()
        while len(result) < size:
            self._check_io_deadline(deadline_monotonic_ns, len(result))
            try:
                part = self._socket.recv(size - len(result))
                if not part:
                    raise WssTLSFailure(TLS_READ_FAILURE_REASON, bytes_completed=len(result))
                result.extend(part)
            except ssl.SSLWantReadError:
                try:
                    self._wait(
                        self._socket,
                        want_read=True,
                        deadline_ns=deadline_monotonic_ns,
                        clock=self._monotonic_ns,
                    )
                except TimeoutError:
                    raise WssTLSFailure(TLS_DEADLINE_REASON, bytes_completed=len(result)) from None
                except OSError:
                    raise WssTLSFailure(
                        TLS_READ_FAILURE_REASON, bytes_completed=len(result)
                    ) from None
            except ssl.SSLWantWriteError:
                try:
                    self._wait(
                        self._socket,
                        want_read=False,
                        deadline_ns=deadline_monotonic_ns,
                        clock=self._monotonic_ns,
                    )
                except TimeoutError:
                    raise WssTLSFailure(TLS_DEADLINE_REASON, bytes_completed=len(result)) from None
                except OSError:
                    raise WssTLSFailure(
                        TLS_READ_FAILURE_REASON, bytes_completed=len(result)
                    ) from None
            except WssTLSFailure:
                raise
            except (OSError, ssl.SSLError):
                raise WssTLSFailure(TLS_READ_FAILURE_REASON, bytes_completed=len(result)) from None
        self._check_io_deadline(deadline_monotonic_ns, len(result))
        return bytes(result)

    def write_all(self, data: bytes, *, deadline_monotonic_ns: int) -> None:
        if self._closed or not isinstance(data, bytes):
            raise WssTLSFailure(TLS_WRITE_FAILURE_REASON)
        completed = 0
        while completed < len(data):
            self._check_io_deadline(deadline_monotonic_ns, completed)
            try:
                count = self._socket.send(data[completed:])
                if count <= 0:
                    raise WssTLSFailure(TLS_WRITE_FAILURE_REASON, bytes_completed=completed)
                completed += count
            except ssl.SSLWantReadError:
                try:
                    self._wait(
                        self._socket,
                        want_read=True,
                        deadline_ns=deadline_monotonic_ns,
                        clock=self._monotonic_ns,
                    )
                except TimeoutError:
                    raise WssTLSFailure(TLS_DEADLINE_REASON, bytes_completed=completed) from None
                except OSError:
                    raise WssTLSFailure(
                        TLS_WRITE_FAILURE_REASON, bytes_completed=completed
                    ) from None
            except ssl.SSLWantWriteError:
                try:
                    self._wait(
                        self._socket,
                        want_read=False,
                        deadline_ns=deadline_monotonic_ns,
                        clock=self._monotonic_ns,
                    )
                except TimeoutError:
                    raise WssTLSFailure(TLS_DEADLINE_REASON, bytes_completed=completed) from None
                except OSError:
                    raise WssTLSFailure(
                        TLS_WRITE_FAILURE_REASON, bytes_completed=completed
                    ) from None
            except WssTLSFailure:
                raise
            except (OSError, ssl.SSLError):
                raise WssTLSFailure(TLS_WRITE_FAILURE_REASON, bytes_completed=completed) from None
        self._check_io_deadline(deadline_monotonic_ns, completed)

    def _check_io_deadline(self, deadline_ns: int, bytes_completed: int) -> None:
        try:
            self._check_deadline(deadline_ns, self._monotonic_ns)
        except TimeoutError:
            raise WssTLSFailure(TLS_DEADLINE_REASON, bytes_completed=bytes_completed) from None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._socket.close()
        except OSError:
            pass

    def __enter__(self) -> VerifiedTLSStream:
        if self._closed:
            raise WssTLSFailure(TLS_READ_FAILURE_REASON)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
