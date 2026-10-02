"""Synthetic transport faults and conservative handoff; no fabricated live evidence."""

import json
import struct
from pathlib import Path

import pytest

from memecoin_ai.domain import ValidationError, canonical, digest
from memecoin_ai.hybrid import Reducer, StreamReader
from memecoin_ai.pubsub import ReadOnlyWebSocket, SnapshotTriggers, StreamError
from memecoin_ai.sources import Capture


class Sock:
    def __init__(self, data):
        self.data = bytearray(data)
        self.sent = []
        self.closed = False

    def recv(self, n):
        part = self.data[: min(n, 3)]
        del self.data[: len(part)]
        return bytes(part)

    def sendall(self, data):
        self.sent.append(data)

    def settimeout(self, timeout):
        assert timeout > 0

    def shutdown(self, how):
        pass

    def close(self):
        self.closed = True


def socket_peer(raw, limit=1024):
    ws = object.__new__(ReadOnlyWebSocket)
    ws.sock = Sock(raw)
    ws.timeout, ws.max_bytes = 1, limit
    ws.sent_bytes = ws.received_bytes = 0
    return ws


def frame(raw, op=1, fin=True):
    n = len(raw)
    return (
        bytes([(128 if fin else 0) | op, n if n < 126 else 126])
        + (struct.pack("!H", n) if n >= 126 else b"")
        + raw
    )


def test_fragmented_utf8_with_interleaved_ping_and_masked_pong():
    ws = socket_peer(frame(b'{"x":"\xd0', fin=False) + frame(b"hi", 9) + frame(b'\xb0"}', 0))
    sock = ws.sock
    assert json.loads(ws.receive()) == {"x": "а"}
    reply = sock.sent[0]
    assert reply[:2] == b"\x8a\x82"
    assert bytes(b ^ reply[2 + i % 4] for i, b in enumerate(reply[6:])) == b"hi"


@pytest.mark.parametrize(
    "raw,category",
    [
        (b"\x81\xff", "invalid_frame_flags"),
        (frame(b"x", 0), "unsupported_frame_sequence"),
        (frame(b"x", 2), "unsupported_frame_sequence"),
        (frame(b"x", 9, False), "invalid_control_frame"),
        (b"\x81\x7e\x00\x01x", "noncanonical_length"),
        (b"\x81\x7e\x10\x00", "message_limit"),
        (frame(struct.pack("!H", 1008), 8), "peer_close"),
        (b"\x81\x05x", "eof"),
    ],
)
def test_invalid_or_overlimit_peer_stops_without_retry(raw, category):
    ws = socket_peer(raw)
    sock = ws.sock
    with pytest.raises(StreamError) as e:
        ws.receive()
    assert e.value.category == category and sock.closed


def test_duplicate_late_same_slot_and_bounded_dedup_do_not_merge_state():
    t = SnapshotTriggers({"p": ["a", "shared"], "q": ["shared"]}, dedup_limit=2)
    assert t.notification("a", 100, {"data": 1}) == "hint"
    assert t.notification("a", 100, {"data": 1}) == "duplicate"
    assert t.notification("a", 100, {"data": 2}) == "hint"
    assert t.notification("a", 99, {"data": 3}) == "late"
    assert len(t.seen) == 2
    t.anchor("p", 100)
    assert not t.needed("p")
    t.notification("shared", 101, {})
    assert t.needed("p") and t.needed("q")
    t.anchor("p", 100)  # Notification ahead of HTTP bank survives handoff.
    assert t.needed("p")
    with pytest.raises(ValidationError):
        t.anchor("p", 99)


def test_overflow_retains_raw_before_stopping(tmp_path):
    class Peer:
        def receive(self):
            return b'{"synthetic":true}'

        def close(self):
            pass

    r = StreamReader(
        Peer(),
        tmp_path / "raw",
        dict(
            max_pending_messages=1,
            max_notifications_per_segment=20,
            max_wss_message_bytes=128,
            max_wss_bytes_per_segment=10000,
        ),
    )
    r.run()
    assert r.failure["category"] == "queue_overflow"
    assert r.count == 2 and len(list((tmp_path / "raw").glob("*.bin"))) == 2
    assert r.pending.qsize() == 1


BASE = Capture.load(Path(__file__).parent / "fixtures/token2022/checkpoint-2")
PARAMS = json.loads(BASE.request_json)["params"]
POOL = PARAMS[0][0]


def test_subscription_barrier_restart_and_late_receipt_guard(tmp_path):
    r = Reducer(tmp_path, dict(cohort=[dict(pool_id=POOL, params=PARAMS)]))
    BASE.save(tmp_path / "cap")
    now = BASE.received_ns
    snapshot = dict(kind="snapshot", available_ns=now + 10, arm="hybrid", pool=POOL, capture="cap")
    with pytest.raises(ValidationError, match="barrier"):
        r.apply(snapshot)
    r = Reducer(tmp_path, r.plan)
    r.apply(dict(kind="connect", available_ns=now, generation=0, requests={"1": POOL}))
    with pytest.raises(ValidationError, match="barrier"):
        r.apply(snapshot)
    raw = canonical(dict(jsonrpc="2.0", id=1, result=900)).encode()
    (tmp_path / "ack").write_bytes(raw)
    ack = dict(
        kind="stream",
        available_ns=now + 11,
        received_ns=now + 1,
        generation=0,
        file="ack",
        sha256=digest(raw),
    )
    r.apply(ack)
    snapshot["available_ns"] = now + 12
    assert r.apply(snapshot)["status"] == "advanced"
    assert r.books["hybrid"].features(now + 13)[0]["reasons"] == ["history_warmup"]
    r.apply(dict(kind="gap", arm="hybrid", available_ns=now + 14, reason="disconnect"))
    r.apply(dict(kind="connect", available_ns=now + 15, generation=1, requests={"1": POOL}))
    ack["available_ns"] = now + 16
    with pytest.raises(ValidationError, match="generation"):
        r.apply(ack)
    ack.update(generation=1, available_ns=now + 17, received_ns=now + 18)
    with pytest.raises(ValidationError, match="availability"):
        r.apply(ack)


def test_wss_notification_never_enters_causal_book(tmp_path):
    r = Reducer(tmp_path, dict(cohort=[dict(pool_id=POOL, params=PARAMS)]))
    r.apply(dict(kind="connect", available_ns=1, generation=0, requests={"1": POOL}))
    for i, msg in enumerate(
        [
            dict(jsonrpc="2.0", id=1, result=7),
            dict(
                jsonrpc="2.0",
                method="accountNotification",
                params=dict(
                    subscription=7,
                    result=dict(context=dict(slot=999), value={"fake_atomic_pool": 1000000}),
                ),
            ),
        ]
    ):
        raw = canonical(msg).encode()
        name = str(i)
        (tmp_path / name).write_bytes(raw)
        r.apply(
            dict(
                kind="stream",
                available_ns=i + 2,
                received_ns=i + 2,
                generation=0,
                file=name,
                sha256=digest(raw),
            )
        )
    assert r.triggers.needed(POOL)
    assert not r.books["hybrid"].last


def test_tls_upgrade_validation_and_secret_safe_errors(monkeypatch):
    import base64
    import hashlib
    import ssl

    class UpgradeSock(Sock):
        def sendall(self, data):
            text = data.decode()
            key = next(
                line.split(": ", 1)[1]
                for line in text.split("\r\n")
                if line.startswith("Sec-WebSocket-Key:")
            )
            value = base64.b64encode(
                hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
            ).decode()
            self.data = bytearray(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {value}\r\n\r\n"
                ).encode()
            )

    peer = UpgradeSock(b"")
    context = ssl.create_default_context()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED

    class TLS:
        def wrap_socket(self, raw, *, server_hostname):
            assert server_hostname == "example.invalid"
            return peer

    monkeypatch.setattr("memecoin_ai.pubsub.socket.create_connection", lambda *a: peer)
    monkeypatch.setattr("memecoin_ai.pubsub.ssl.create_default_context", lambda: TLS())
    ws = ReadOnlyWebSocket("wss://example.invalid/v2/SECRET")
    assert ws.phase == "frames"
    ws.close()
    assert peer.closed


def test_recovery_never_backdates_missing_online_history(tmp_path):
    from test_causal import observation

    r = Reducer(tmp_path, dict(cohort=[dict(pool_id=POOL, params=PARAMS)]))
    b = r.books["hybrid"]
    b.ingest(observation(10, 100))
    b.ingest(observation(20, 101))
    original = b.features(21 * 10**9)
    assert original[0]["usable"]
    r.apply(dict(kind="gap", arm="hybrid", available_ns=22 * 10**9, reason="queue_overflow"))
    assert not b.features(23 * 10**9)[0]["usable"]
    b.ingest(observation(30, 105))
    recovered = b.features(31 * 10**9)[0]
    assert recovered["reasons"] == ["history_warmup"]
    assert recovered["input_available_ns"] == 30 * 10**9
    assert original[0]["input_available_ns"] == 20 * 10**9
    assert recovered["segment"] > original[0]["segment"]
