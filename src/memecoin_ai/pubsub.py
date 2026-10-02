"""Bounded read-only RFC6455 transport and conservative snapshot trigger reducer.

Notifications never become account snapshots or proof of a complete write history.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

from memecoin_ai.domain import ValidationError, canonical, digest, integer
from memecoin_ai.sources import SourceError, TransportSourceError


class StreamError(SourceError):
    def __init__(self, category: str, code: int | None = None) -> None:
        self.category, self.code = category, code
        super().__init__(f"stream {category}; code={code}; endpoint redacted")


class ReadOnlyWebSocket:
    """One verified TLS connection; no redirects, compression or hidden reconnect.

    Supports fragmented text and ping/pong. Every frame/message and its assembly
    time is bounded. Timeout is terminal (never resume a partially consumed frame).
    """

    def __init__(self, endpoint: str, *, timeout: float = 10, max_bytes: int = 65536):
        url = urlsplit(endpoint)
        if (
            url.scheme != "wss"
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
            or any(c in endpoint for c in "\r\n")
        ):
            raise ValidationError("WSS endpoint required without userinfo/fragment")
        if not 0 < timeout <= 60 or not 125 <= max_bytes <= 1_000_000:
            raise ValidationError("invalid stream bounds")
        self.timeout, self.max_bytes = timeout, max_bytes
        self.sock: ssl.SSLSocket | None = None
        self.received_bytes = 0
        self.sent_bytes = 0
        self.phase = "connect"
        raw = None
        try:
            deadline = time.monotonic() + timeout
            raw = socket.create_connection((url.hostname, url.port or 443), timeout)
            raw.settimeout(max(0.001, deadline - time.monotonic()))
            self.phase = "tls"
            self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=url.hostname)
            self.phase = "upgrade"
            key = base64.b64encode(os.urandom(16)).decode()
            path = (url.path or "/") + ("?" + url.query if url.query else "")
            host = url.hostname + (f":{url.port}" if url.port else "")
            request = (
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\nUser-Agent: memecoin-ai/0.1\r\n\r\n"
            ).encode()
            self.sock.sendall(request)
            headers = bytearray()
            while not headers.endswith(b"\r\n\r\n"):
                if len(headers) >= 16384:
                    raise StreamError("header_limit")
                headers.extend(self._exact(1, deadline))
            lines = bytes(headers).decode("latin1").split("\r\n")
            status = int(lines[0].split()[1])
            if status != 101:
                raise StreamError("http_upgrade", status)
            fields = dict(line.lower().split(":", 1) for line in lines[1:] if ":" in line)
            accept = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
            ).decode()
            # Header names are case-insensitive; accept value is case-sensitive.
            original = dict(
                (k.lower(), v.strip())
                for k, v in (line.split(":", 1) for line in lines[1:] if ":" in line)
            )
            if (
                original.get("sec-websocket-accept") != accept
                or fields.get("upgrade", "").strip() != "websocket"
                or "upgrade" not in fields.get("connection", "")
                or "sec-websocket-extensions" in fields
                or "sec-websocket-protocol" in fields
            ):
                raise StreamError("invalid_upgrade")
            self.phase = "frames"
        except (OSError, ValueError, SourceError) as exc:
            if raw is not None:
                raw.close()
            self.close()
            raise self._safe(exc) from None

    @staticmethod
    def _safe(exc: Exception) -> SourceError:
        if isinstance(exc, SourceError):
            return exc
        reason = (
            "timeout"
            if isinstance(exc, TimeoutError)
            else "tls"
            if isinstance(exc, ssl.SSLError)
            else "dns"
            if isinstance(exc, socket.gaierror)
            else "connection"
        )
        return TransportSourceError(reason)

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
            self.sock = None

    def _exact(self, count: int, deadline: float) -> bytes:
        result = bytearray()
        while len(result) < count:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError()
            if self.sock is None:
                raise StreamError("closed")
            self.sock.settimeout(left)
            chunk = self.sock.recv(count - len(result))
            if not chunk:
                raise StreamError("eof")
            self.received_bytes += len(chunk)
            result.extend(chunk)
        return bytes(result)

    def _send(self, opcode: int, body: bytes) -> None:
        if self.sock is None:
            raise StreamError("closed")
        mask = os.urandom(4)
        n = len(body)
        header = bytes([128 | opcode, 128 | (n if n < 126 else 126)])
        if n >= 126:
            header += struct.pack("!H", n)
        packet = header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(body))
        self.sock.sendall(packet)
        self.sent_bytes += len(packet)

    def subscribe(self, request_id: int, address: str | None) -> None:
        integer(request_id, "subscription request id", 1)
        if address is not None:
            from memecoin_ai.raydium import public_key

            public_key(address)
        message = dict(
            jsonrpc="2.0",
            id=request_id,
            method="rootSubscribe" if address is None else "accountSubscribe",
            params=[]
            if address is None
            else [address, dict(commitment="finalized", encoding="base64")],
        )
        try:
            self._send(1, canonical(message).encode())
        except OSError as exc:
            self.close()
            raise self._safe(exc) from None

    def receive(self) -> bytes:
        try:
            deadline = time.monotonic() + self.timeout
            message = bytearray()
            fragmented = False
            while True:
                a, b = self._exact(2, deadline)
                fin, opcode = bool(a & 128), a & 15
                if a & 112 or b & 128:
                    raise StreamError("invalid_frame_flags")
                size = b & 127
                if size == 126:
                    size = struct.unpack("!H", self._exact(2, deadline))[0]
                    if size < 126:
                        raise StreamError("noncanonical_length")
                elif size == 127:
                    size = struct.unpack("!Q", self._exact(8, deadline))[0]
                    if size < 65536 or size >= 2**63:
                        raise StreamError("noncanonical_length")
                if size > self.max_bytes or (opcode < 8 and len(message) + size > self.max_bytes):
                    raise StreamError("message_limit")
                if opcode >= 8 and (not fin or size > 125):
                    raise StreamError("invalid_control_frame")
                body = self._exact(size, deadline)
                if opcode == 8:
                    raise StreamError(
                        "peer_close", struct.unpack("!H", body[:2])[0] if len(body) >= 2 else None
                    )
                if opcode == 9:
                    self._send(10, body)
                    continue
                if opcode == 10:
                    continue
                if opcode not in (0, 1) or (opcode == 0) != fragmented:
                    raise StreamError("unsupported_frame_sequence")
                message.extend(body)
                if fin:
                    bytes(message).decode("utf8")
                    return bytes(message)
                fragmented = True
        except (OSError, ValueError, SourceError) as exc:
            self.close()
            raise self._safe(exc) from None


class SnapshotTriggers:
    """Bounded hints, not a state merger. Caller persists raw before applying hints."""

    def __init__(self, dependencies: dict[str, list[str]], *, dedup_limit: int = 4096):
        if not dependencies or dedup_limit < 1:
            raise ValidationError("empty dependencies or invalid dedup limit")
        self.dependencies = dependencies
        self.anchors = dict.fromkeys(dependencies, -1)
        self.dirty = dict.fromkeys(dependencies, -1)
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.dedup_limit = dedup_limit
        self.highwater: dict[str, int] = {}

    def notification(self, address: str, slot: int, value: Any) -> str:
        integer(slot, "notification slot")
        pools = [p for p, addresses in self.dependencies.items() if address in addresses]
        if not pools:
            raise ValidationError("notification outside frozen dependencies")
        key = digest(canonical([address, slot, value]).encode())
        if key in self.seen:
            return "duplicate"
        self.seen[key] = None
        if len(self.seen) > self.dedup_limit:
            self.seen.popitem(last=False)
        late = slot < self.highwater.get(address, -1)
        self.highwater[address] = max(slot, self.highwater.get(address, -1))
        for pool in pools:
            if slot > self.anchors[pool]:
                self.dirty[pool] = max(slot, self.dirty[pool])
        return "late" if late else "hint"

    def anchor(self, pool: str, slot: int) -> None:
        integer(slot, "snapshot slot")
        if slot < self.anchors[pool]:
            raise ValidationError("snapshot anchor regressed")
        self.anchors[pool] = slot
        if self.dirty[pool] <= slot:
            self.dirty[pool] = -1

    def needed(self, pool: str) -> bool:
        return self.dirty[pool] > self.anchors[pool]
