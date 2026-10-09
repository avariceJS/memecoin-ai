from __future__ import annotations

import fcntl
import http.client
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from memecoin_ai.domain import ValidationError, canonical, digest, integer, strict_json

READ_METHODS = frozenset(
    {
        "getVersion",
        "getGenesisHash",
        "getEpochSchedule",
        "getSlot",
        "getFirstAvailableBlock",
        "getBlock",
        "getBlocks",
        "getAccountInfo",
        "getMultipleAccounts",
        "getSignaturesForAddress",
        "getSignatureStatuses",
        "getTransaction",
    }
)


class SourceError(RuntimeError):
    pass


def _reserve_shared_request_start(path: Path, interval_ns: int = 1_000_000_000) -> None:
    """Serialize request start times across local processes using one durable ledger."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            try:
                value = json.loads(path.read_text())
                next_allowed_ns = value["next_allowed_ns"]
            except (OSError, KeyError, TypeError, ValueError) as exc:
                raise SourceError("shared request gate ledger is invalid") from exc
            if type(next_allowed_ns) is not int or next_allowed_ns < 0:
                raise SourceError("shared request gate ledger is invalid")
        else:
            next_allowed_ns = 0
        wait_ns = max(0, next_allowed_ns - time.time_ns())
        if wait_ns:
            time.sleep(wait_ns / 1_000_000_000)
        reserved_ns = time.time_ns()
        row = {
            "schema_version": 1,
            "interval_ns": interval_ns,
            "next_allowed_ns": reserved_ns + interval_ns,
        }
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temp.open("x") as stream:
                stream.write(canonical(row) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, path)
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if temp.exists():
                temp.unlink()


class HTTPSourceError(SourceError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"RPC HTTP {status}; endpoint details redacted")


class TransportSourceError(SourceError):
    def __init__(self, reason: str) -> None:
        if reason not in {"timeout", "dns", "tls", "connection", "other"}:
            raise ValueError("invalid transport category")
        self.reason = reason
        super().__init__(f"RPC transport failed ({reason}); endpoint details redacted")


@dataclass(frozen=True)
class Capture:
    source_alias: str
    method: str
    request_json: str
    request_started_ns: int
    received_ns: int
    response: bytes

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=False)
        metadata = {
            "schema_version": 1,
            **{k: v for k, v in asdict(self).items() if k != "response"},
            "response_sha256": digest(self.response),
            "semantics": "HTTP receipt now; not historical market availability",
        }
        (path / "response.json").write_bytes(self.response)
        (path / "receipt.json").write_text(canonical(metadata) + "\n")

    @classmethod
    def load(cls, path: Path) -> Capture:
        metadata = strict_json((path / "receipt.json").read_text())
        body = (path / "response.json").read_bytes()
        if metadata.get("schema_version") != 1 or digest(body) != metadata["response_sha256"]:
            raise SourceError("capture checksum/schema mismatch")
        request = strict_json(metadata["request_json"])
        if request.get("method") != metadata["method"] or request.get("id") != 1:
            raise SourceError("capture request mismatch")
        for name in ("request_started_ns", "received_ns"):
            integer(metadata[name], name)
        if metadata["received_ns"] < metadata["request_started_ns"]:
            raise SourceError("capture clock regression")
        return cls(
            metadata["source_alias"],
            metadata["method"],
            metadata["request_json"],
            metadata["request_started_ns"],
            metadata["received_ns"],
            body,
        )

    def result(self) -> object:
        try:
            decoded = strict_json(self.response.decode())
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceError("RPC response is not JSON") from exc
        if not isinstance(decoded, dict) or decoded.get("jsonrpc") != "2.0":
            raise SourceError("invalid JSON-RPC envelope")
        if type(decoded.get("id")) is not int or decoded.get("id") != 1:
            raise SourceError("RPC response ID mismatch")
        if "error" in decoded:
            raise SourceError("RPC returned an error; raw response retained")
        if "result" not in decoded:
            raise SourceError("RPC result missing")
        return decoded["result"]


Transport = Callable[[str, bytes, float, int], bytes]


class PersistentHTTPTransport:
    """Serial, endpoint-bound verified TLS. Never retries or follows redirects."""

    def __init__(self) -> None:
        self._endpoint: str | None = None
        self._connection: http.client.HTTPSConnection | None = None
        self._context = ssl.create_default_context()
        self.last_measurement: dict[str, object] = {}

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __call__(self, endpoint: str, request: bytes, timeout: float, limit: int) -> bytes:
        url = urlsplit(endpoint)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
        ):
            raise ValidationError("persistent transport requires HTTPS without userinfo/fragment")
        if self._endpoint is not None and endpoint != self._endpoint:
            raise ValidationError("persistent transport is bound to one endpoint")
        self._endpoint = endpoint
        started = time.monotonic()
        deadline = started + timeout
        phase = "connect"
        reused = self._connection is not None and self._connection.sock is not None
        self.last_measurement = dict(reused_connection=reused, http_status=None)
        try:
            if self._connection is None:
                self._connection = http.client.HTTPSConnection(
                    url.hostname, url.port, timeout=timeout, context=self._context
                )
            conn = self._connection
            if not reused:
                conn.timeout = timeout
                conn.connect()
            self.last_measurement["connect_ns"] = int((time.monotonic() - started) * 10**9)
            sock = conn.sock
            if sock is None:
                raise ConnectionError("missing connected socket")

            def remaining() -> None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError()
                sock.settimeout(left)

            phase = "send"
            remaining()
            target = (url.path or "/") + ("?" + url.query if url.query else "")
            conn.request(
                "POST",
                target,
                body=request,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "memecoin-ai/0.1 read-only-research",
                    "Accept-Encoding": "identity",
                },
            )
            phase = "headers"
            remaining()
            response = conn.getresponse()
            self.last_measurement.update(
                http_status=response.status,
                headers_ns=int((time.monotonic() - started) * 10**9),
            )
            if response.status != 200:
                raise HTTPSourceError(response.status)
            if response.getheader("Content-Encoding", "identity") != "identity":
                raise SourceError("unexpected content encoding in persistent transport")
            expected = response.length
            if expected is not None and expected > limit:
                raise SourceError("RPC response exceeded configured byte limit")
            phase = "body"
            body = bytearray()
            while True:
                remaining()
                chunk = response.read1(min(65536, limit + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > limit:
                    raise SourceError("RPC response exceeded configured byte limit")
            if expected is not None and len(body) != expected:
                raise SourceError("incomplete HTTP response body")
            self.last_measurement["connection_retained"] = conn.sock is not None
            return bytes(body)
        except (OSError, http.client.HTTPException) as exc:
            self.last_measurement["failed_phase"] = phase
            self.close()
            reason = (
                "timeout"
                if isinstance(exc, TimeoutError)
                else "dns"
                if isinstance(exc, socket.gaierror)
                else "tls"
                if isinstance(exc, ssl.SSLError)
                else "connection"
            )
            raise TransportSourceError(reason) from None
        except SourceError:
            self.last_measurement["failed_phase"] = phase
            self.close()
            raise
        finally:
            self.last_measurement["total_ns"] = int((time.monotonic() - started) * 10**9)


def http_transport(endpoint: str, request: bytes, timeout: float, limit: int) -> bytes:
    message = urllib.request.Request(
        endpoint,
        data=request,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "memecoin-ai/0.1 read-only-research",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(message, timeout=timeout) as response:
            body = response.read(limit + 1)
    except urllib.error.HTTPError as exc:
        raise HTTPSourceError(exc.code) from None
    except (urllib.error.URLError, OSError) as exc:
        # Classify exception types only: their messages can contain endpoint credentials.
        cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        reason = (
            "timeout"
            if isinstance(cause, TimeoutError)
            else "dns"
            if isinstance(cause, socket.gaierror)
            else "tls"
            if isinstance(cause, ssl.SSLError)
            else "connection"
            if isinstance(cause, ConnectionError)
            else "other"
        )
        raise TransportSourceError(reason) from None
    if len(body) > limit:
        raise SourceError("RPC response exceeded configured byte limit")
    return bytes(body)


class ReadOnlyRPC:
    def __init__(
        self,
        endpoint: str,
        source_alias: str,
        *,
        timeout: float = 10.0,
        max_bytes: int = 4_000_000,
        transport: Transport = http_transport,
        clock: Callable[[], int] = time.time_ns,
        keep_alive: bool = False,
        shared_start_gate: Path | None = None,
    ) -> None:
        url = urlsplit(endpoint)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValidationError("RPC endpoint must be HTTPS without embedded user credentials")
        if not source_alias or any(
            c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in source_alias
        ):
            raise ValidationError("source_alias must be a non-secret lowercase identifier")
        if not 0 < timeout <= 60:
            raise ValidationError("timeout must be in (0,60]")
        integer(max_bytes, "max_bytes", 1)
        self._endpoint = endpoint
        self.source_alias = source_alias
        if shared_start_gate is None and source_alias == "solana-public":
            configured_gate = os.environ.get("MEMECOIN_SOLANA_PUBLIC_RATE_LEDGER")
            shared_start_gate = Path(configured_gate) if configured_gate else None
        self.shared_start_gate = shared_start_gate
        self.timeout = timeout
        self.max_bytes = max_bytes
        if keep_alive and transport is not http_transport:
            raise ValidationError("choose either persistent or custom transport")
        self.transport: Transport = PersistentHTTPTransport() if keep_alive else transport
        self.transport_profile = "stdlib_persistent_v1" if keep_alive else "stdlib_urllib"
        self.clock = clock

    def close(self) -> None:
        if isinstance(self.transport, PersistentHTTPTransport):
            self.transport.close()

    def transport_measurement(self) -> dict[str, object] | None:
        if isinstance(self.transport, PersistentHTTPTransport):
            return dict(self.transport.last_measurement)
        return None

    @property
    def endpoint_origin(self) -> str:
        """Public routing identity only; never path/query/API credentials."""
        url = urlsplit(self._endpoint)
        host = url.hostname or ""
        if ":" in host:
            host = "[" + host + "]"
        return "https://" + host + (":" + str(url.port) if url.port else "")

    def fetch(self, method: str, params: list[object]) -> Capture:
        if method not in READ_METHODS:
            raise ValidationError("RPC method is not in the read-only allowlist")
        if self.shared_start_gate is not None:
            _reserve_shared_request_start(self.shared_start_gate)
        request = canonical({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        start = self.clock()
        body = self.transport(self._endpoint, request.encode(), self.timeout, self.max_bytes)
        received = self.clock()
        if received < start:
            raise SourceError("local clock moved backwards")
        return Capture(self.source_alias, method, request, start, received, body)


def probe_rpc(client: ReadOnlyRPC, output: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=False)
    results: dict[str, object] = {}
    for method in ("getVersion", "getSlot", "getFirstAvailableBlock"):
        params: list[object] = [{"commitment": "finalized"}] if method == "getSlot" else []
        try:
            capture = client.fetch(method, params)
            capture.save(output / method)
            results[method] = capture.result()
        except SourceError as exc:
            results[method] = {"error": str(exc)}
    report = {
        "source_alias": client.source_alias,
        "results": results,
        "market_dataset_qualified": False,
        "historical_arrival_times_available": False,
        "limitation": "Connectivity and retention probe only; no pool decoding or coverage proof",
    }
    (output / "report.json").write_text(canonical(report) + "\n")
    return report
