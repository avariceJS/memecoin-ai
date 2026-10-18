"""Synthetic HTTP peers: reuse, bounds, failure propagation, no hidden requests."""

import io
import ssl

import pytest

from memecoin_ai.domain import ValidationError
from memecoin_ai.sources import (
    HTTPSourceError,
    PersistentHTTPTransport,
    ReadOnlyRPC,
    SourceError,
    TransportSourceError,
)


class Socket:
    def settimeout(self, value):
        assert value > 0


class Response(io.BytesIO):
    status = 200

    def __init__(self, data=b'{"jsonrpc":"2.0","id":1,"result":123}', *, declared=None):
        super().__init__(data)
        self.length = len(data) if declared is None else declared
        self.encoding = "identity"

    def getheader(self, name, default=None):
        return self.encoding if name == "Content-Encoding" else default


class Connection:
    def __init__(self):
        self.sock = None
        self.requests = []
        self.responses = []
        self.connects = 0
        self.failure = None

    def connect(self):
        self.connects += 1
        if self.failure:
            raise self.failure
        self.sock = Socket()

    def request(self, method, target, **kwargs):
        self.requests.append((method, target, kwargs))

    def getresponse(self):
        return self.responses.pop(0) if self.responses else Response()

    def close(self):
        self.sock = None


def install(monkeypatch):
    conn = Connection()

    def factory(host, port, *, timeout, context):
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        assert host == "example.invalid"
        return conn

    monkeypatch.setattr("memecoin_ai.sources.http.client.HTTPSConnection", factory)
    return conn


def test_reuses_tls_but_each_capture_is_a_new_actual_request(monkeypatch):
    conn = install(monkeypatch)
    rpc = ReadOnlyRPC("https://example.invalid/?key=SECRET", "fixture", keep_alive=True)
    a = rpc.fetch("getSlot", [])
    b = rpc.fetch("getSlot", [])
    assert a.result() == b.result() == 123
    assert conn.connects == 1 and len(conn.requests) == 2
    assert a.received_ns <= b.request_started_ns
    assert rpc.transport_measurement()["reused_connection"]
    assert "SECRET" not in str(rpc.transport_measurement())
    rpc.close()
    assert conn.sock is None


def test_transport_cannot_switch_endpoint_or_follow_redirect(monkeypatch):
    conn = install(monkeypatch)
    t = PersistentHTTPTransport()
    t("https://example.invalid/a", b"{}", 1, 100)
    with pytest.raises(ValidationError, match="bound to one"):
        t("https://example.invalid/b", b"{}", 1, 100)
    response = Response()
    response.status = 302
    conn.responses.append(response)
    with pytest.raises(HTTPSourceError) as caught:
        t("https://example.invalid/a", b"{}", 1, 100)
    assert caught.value.status == 302 and len(conn.requests) == 2
    assert conn.sock is None


@pytest.mark.parametrize("reason", ["timeout", "tls", "connection"])
def test_failed_connection_is_closed_without_retry_or_secret_message(monkeypatch, reason):
    conn = install(monkeypatch)
    conn.failure = dict(
        timeout=TimeoutError("SECRET"),
        tls=ssl.SSLError("SECRET"),
        connection=ConnectionResetError("SECRET"),
    )[reason]
    t = PersistentHTTPTransport()
    with pytest.raises(TransportSourceError) as caught:
        t("https://example.invalid/?key=SECRET", b"{}", 1, 100)
    assert caught.value.reason == reason and "SECRET" not in str(caught.value)
    assert t.last_measurement["failed_phase"] == "connect"
    assert conn.connects == 1 and not conn.requests and conn.sock is None


@pytest.mark.parametrize("kind", ["declared_large", "stream_large", "truncated", "compressed"])
def test_response_bounds_and_framing_fail_closed(monkeypatch, kind):
    conn = install(monkeypatch)
    response = Response(b"0123456789", declared=10)
    if kind == "stream_large":
        response.length = None
    elif kind == "truncated":
        response.length = 20
    elif kind == "compressed":
        response.encoding = "gzip"
    conn.responses.append(response)
    limit = 5 if kind.endswith("large") else 100
    t = PersistentHTTPTransport()
    with pytest.raises(SourceError):
        t("https://example.invalid", b"{}", 1, limit)
    assert conn.sock is None and len(conn.requests) == 1


def test_body_deadline_is_total_not_reset_for_each_chunk(monkeypatch):
    conn = install(monkeypatch)
    now = [0.0]
    monkeypatch.setattr("memecoin_ai.sources.time.monotonic", lambda: now[0])
    response = Response(b"0123456789")

    def drip(size):
        now[0] += 0.6
        return b"x"

    response.read1 = drip
    conn.responses.append(response)
    t = PersistentHTTPTransport()
    with pytest.raises(TransportSourceError) as caught:
        t("https://example.invalid", b"{}", 1, 100)
    assert caught.value.reason == "timeout"
    assert t.last_measurement["failed_phase"] == "body" and conn.sock is None


def test_campaign_rejects_transport_change_before_network(tmp_path):
    from test_prospective import setup

    from memecoin_ai.prospective import resume_campaign

    root = tmp_path / "campaign"
    setup(root)
    rpc = ReadOnlyRPC(
        "https://example.invalid",
        "synthetic-campaign",
        timeout=15,
        max_bytes=1_000_000,
        keep_alive=True,
    )
    with pytest.raises(ValidationError, match="transport profile"):
        resume_campaign(root, rpc)
