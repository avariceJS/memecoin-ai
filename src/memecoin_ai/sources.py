from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from memecoin_ai.domain import ValidationError, canonical, digest, integer

READ_METHODS = frozenset(
    {
        "getVersion",
        "getSlot",
        "getFirstAvailableBlock",
        "getBlock",
        "getAccountInfo",
        "getMultipleAccounts",
    }
)


class SourceError(RuntimeError):
    pass


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

    def result(self) -> object:
        try:
            decoded = json.loads(self.response)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SourceError("RPC response is not JSON") from exc
        if not isinstance(decoded, dict) or decoded.get("jsonrpc") != "2.0":
            raise SourceError("invalid JSON-RPC envelope")
        if decoded.get("id") != 1:
            raise SourceError("RPC response ID mismatch")
        if "error" in decoded:
            raise SourceError("RPC returned an error; raw response retained")
        if "result" not in decoded:
            raise SourceError("RPC result missing")
        return decoded["result"]


Transport = Callable[[str, bytes, float, int], bytes]


def http_transport(endpoint: str, request: bytes, timeout: float, limit: int) -> bytes:
    message = urllib.request.Request(
        endpoint, data=request, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(message, timeout=timeout) as response:
            body = response.read(limit + 1)
    except (urllib.error.URLError, OSError):
        # URL/query strings can contain API keys. Never copy transport exception text into logs.
        raise SourceError("RPC transport failed; endpoint details redacted") from None
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
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.transport = transport
        self.clock = clock

    def fetch(self, method: str, params: list[object]) -> Capture:
        if method not in READ_METHODS:
            raise ValidationError("RPC method is not in the read-only allowlist")
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
