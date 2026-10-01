import json

import pytest

from memecoin_ai.domain import ValidationError
from memecoin_ai.sources import ReadOnlyRPC, SourceError, probe_rpc


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
