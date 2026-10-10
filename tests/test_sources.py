import json
import shutil

import pytest

from memecoin_ai.domain import ValidationError
from memecoin_ai.sources import Capture, ReadOnlyRPC, SourceError, probe_rpc


def client(response=b'{"jsonrpc":"2.0","id":1,"result":123}'):
    clock = iter(range(100, 1000))
    return ReadOnlyRPC(
        "https://example.invalid/?api-key=SECRET",
        "fixture",
        transport=lambda *args: response,
        clock=lambda: next(clock),
    )


def test_read_only_receipts_and_no_endpoint_leak(tmp_path):
    rpc = client()
    capture = rpc.fetch("getSlot", [])
    assert capture.result() == 123
    assert capture.request_started_ns == 100
    assert capture.received_ns == 101
    capture.save(tmp_path / "capture")
    metadata = (tmp_path / "capture/receipt.json").read_text()
    assert "SECRET" not in metadata
    assert "not historical" in json.loads(metadata)["semantics"]
    with pytest.raises(ValidationError, match="read-only"):
        rpc.fetch("sendTransaction", ["anything"])
    with pytest.raises(FileExistsError):
        capture.save(tmp_path / "capture")


def test_rpc_error_is_preserved_and_probe_never_qualifies_market(tmp_path):
    rpc = client(b'{"jsonrpc":"2.0","id":1,"error":{"code":-32000}}')
    report = probe_rpc(rpc, tmp_path / "probe")
    assert report["market_dataset_qualified"] is False
    assert "error" in report["results"]["getSlot"]
    assert (tmp_path / "probe/getSlot/response.json").exists()


def test_invalid_envelope_and_clock_regression():
    with pytest.raises(SourceError, match="ID"):
        client(b'{"jsonrpc":"2.0","id":2,"result":1}').fetch("getSlot", []).result()
    clock = iter([100, 99])
    rpc = ReadOnlyRPC(
        "https://example.invalid",
        "fixture",
        transport=lambda *args: b"{}",
        clock=lambda: next(clock),
    )
    with pytest.raises(SourceError, match="backwards"):
        rpc.fetch("getSlot", [])


def test_network_identity_is_read_only_and_response_is_preserved():
    body = b'{"jsonrpc":"2.0","id":1,"result":"synthetic-genesis"}'
    cap = client(body).fetch("getGenesisHash", [])
    assert cap.response == body
    assert cap.result() == "synthetic-genesis"


@pytest.mark.skipif(shutil.which("zstd") is None, reason="zstd executable unavailable")
def test_zstd_capture_round_trip_keeps_original_receipt_and_detects_damage(tmp_path):
    body = b'{"jsonrpc":"2.0","id":1,"result":{"rows":[' + b'"market",' * 20_000 + b"null]}}"
    cap = client(body).fetch("getBlock", [123, {"transactionDetails": "full"}])
    root = tmp_path / "compressed"
    cap.save(root, storage_codec="zstd")
    receipt = json.loads((root / "receipt.json").read_text())
    assert receipt["schema_version"] == 2
    assert receipt["response_bytes"] == len(body)
    assert receipt["storage"]["codec"] == "zstd"
    assert receipt["storage"]["bytes"] < len(body)
    assert Capture.load(root) == cap

    compressed_path = root / "response.json.zst"
    compressed_path.write_bytes(compressed_path.read_bytes()[:-1] + b"x")
    with pytest.raises(SourceError, match="compressed capture checksum"):
        Capture.load(root)


def test_configured_zstd_is_used_with_minimal_launchd_path(monkeypatch):
    from memecoin_ai.sources import _zstd_executable

    executable = shutil.which("zstd")
    assert executable is not None
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    monkeypatch.setenv("BRAIN_ZSTD_EXECUTABLE", executable)
    _zstd_executable.cache_clear()
    try:
        assert _zstd_executable()[0] == executable
    finally:
        _zstd_executable.cache_clear()


def test_invalid_configured_zstd_fails_closed(monkeypatch):
    from memecoin_ai.sources import _zstd_executable

    monkeypatch.setenv("BRAIN_ZSTD_EXECUTABLE", "relative/zstd")
    _zstd_executable.cache_clear()
    try:
        with pytest.raises(SourceError, match="configured zstd executable"):
            _zstd_executable()
    finally:
        _zstd_executable.cache_clear()


def test_shared_public_rpc_gate_serializes_calls_across_clients(tmp_path, monkeypatch):
    import memecoin_ai.sources as sources

    now = [0]
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        now[0] += int(seconds * 1_000_000_000)

    monkeypatch.setattr(sources.time, "time_ns", lambda: now[0])
    monkeypatch.setattr(sources.time, "sleep", sleep)
    gate = tmp_path / "public-rate-ledger.json"
    response = b'{"jsonrpc":"2.0","id":1,"result":123}'
    for _ in range(2):
        rpc = ReadOnlyRPC(
            "https://api.mainnet-beta.solana.com",
            "solana-public",
            transport=lambda *args: response,
            clock=lambda: 100,
            shared_start_gate=gate,
        )
        assert rpc.fetch("getSlot", []).result() == 123
    assert waits == [1.0]
    assert json.loads(gate.read_text())["next_allowed_ns"] == 2_000_000_000


def test_http_error_status_without_credential_leak(monkeypatch):
    import urllib.error
    import urllib.request

    from memecoin_ai.sources import HTTPSourceError, http_transport

    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("https://host/?key=SECRET", 429, "SECRET", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(HTTPSourceError) as caught:
        http_transport("https://host/?key=SECRET", b"{}", 1, 100)
    assert caught.value.status == 429
    assert "SECRET" not in str(caught.value)


def test_transport_identifies_application_without_changing_rpc_payload(monkeypatch):
    import io
    import urllib.request

    from memecoin_ai.sources import http_transport

    payload = b'{"jsonrpc":"2.0","id":1,"method":"getSlot","params":[]}'
    response = b'{"jsonrpc":"2.0","id":1,"result":123}'

    def receive(message, *, timeout):
        assert message.get_header("User-agent") == "memecoin-ai/0.1 read-only-research"
        assert message.data == payload and message.method == "POST"
        assert timeout == 3
        return io.BytesIO(response)

    monkeypatch.setattr(urllib.request, "urlopen", receive)
    assert http_transport("https://example.invalid", payload, 3, 1000) == response


@pytest.mark.parametrize("category", ["timeout", "dns", "tls", "connection", "other"])
def test_transport_diagnostics_expose_category_not_secret(monkeypatch, category):
    import socket
    import ssl
    import urllib.error
    import urllib.request

    from memecoin_ai.sources import TransportSourceError, http_transport

    causes = {
        "timeout": TimeoutError("SECRET"),
        "dns": socket.gaierror("SECRET"),
        "tls": ssl.SSLError("SECRET"),
        "connection": ConnectionResetError("SECRET"),
        "other": "SECRET",
    }

    def fail(*args, **kwargs):
        raise urllib.error.URLError(causes[category])

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(TransportSourceError) as caught:
        http_transport("https://host/SECRET", b"{}", 1, 100)
    assert caught.value.reason == category
    assert "SECRET" not in str(caught.value)
