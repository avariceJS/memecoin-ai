import base64
import hashlib
import json

import pytest

from memecoin_collector.wss_campaign import WssReferenceCampaign, default_authorization
from memecoin_collector.wss_client import (
    WS_GUID,
    TLSVerification,
    WssClientError,
    upgrade_and_subscribe,
)
from memecoin_collector.wss_reference import DISK_FLOOR_BYTES, MAX_CONTROL_RESPONSE_BYTES


def authorization():
    return default_authorization(
        c4_stop_sha256="0" * 64,
        account_budget_evidence_sha256="1" * 64,
        authorized_at_ns=100,
        expires_at_ns=100 + 6 * 24 * 60 * 60 * 1_000_000_000,
    )


def server_frame(payload, *, opcode=1, fin=True):
    first = bytes([(0x80 if fin else 0) | opcode])
    size = len(payload)
    if size <= 125:
        header = first + bytes([size])
    elif size <= 0xFFFF:
        header = first + bytes([126]) + size.to_bytes(2, "big")
    else:
        header = first + bytes([127]) + size.to_bytes(8, "big")
    return header + payload


def notification(slot=44):
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "logsNotification",
            "params": {
                "subscription": 77,
                "result": {
                    "context": {"slot": slot},
                    "value": {"signature": f"sig-{slot}", "err": None, "logs": []},
                },
            },
        },
        separators=(",", ":"),
    ).encode()


class FakeTLSStream:
    def __init__(
        self,
        *,
        ack=None,
        before_ack=b"",
        subscribe_frames=None,
        upgrade_status=101,
        upgrade_overrides=None,
        mono=None,
        wall_clock=None,
        expire_on_websocket_payload=False,
        expire_after_first_upgrade_byte=False,
        tls_ok=True,
    ):
        self.tls_verification = TLSVerification("mainnet.helius-rpc.com", tls_ok, tls_ok)
        self.ack = ack or b'{"jsonrpc":"2.0","result":77,"id":1}'
        self.before_ack = before_ack
        self.subscribe_frames = subscribe_frames
        self.upgrade_status = upgrade_status
        self.upgrade_overrides = upgrade_overrides or {}
        self.mono = mono
        self.wall_clock = wall_clock
        self.expire_on_websocket_payload = expire_on_websocket_payload
        self.expire_after_first_upgrade_byte = expire_after_first_upgrade_byte
        self.buffer = bytearray()
        self.read_sizes = []
        self.websocket_read_sizes = []
        self.in_websocket_phase = False
        self.writes = []
        self.request_target_fixture = "/?api-key=fixture-secret"

    def read_exactly(self, size, *, deadline_monotonic_ns):
        self.read_sizes.append(size)
        if self.in_websocket_phase:
            self.websocket_read_sizes.append(size)
        if self.expire_after_first_upgrade_byte and self.buffer and not self.writes[1:]:
            if self.mono is not None:
                self.mono[0] = deadline_monotonic_ns
        if len(self.buffer) < size:
            raise EOFError
        value = bytes(self.buffer[:size])
        del self.buffer[:size]
        if self.expire_on_websocket_payload and self.in_websocket_phase and size > 2:
            self.wall_clock[0] = 100 + 6 * 24 * 60 * 60 * 1_000_000_000
        return value

    def write_all(self, data, *, deadline_monotonic_ns):
        self.writes.append(data)
        if data.startswith(b"GET "):
            key = next(
                line.split(b":", 1)[1].strip()
                for line in data.split(b"\r\n")
                if line.lower().startswith(b"sec-websocket-key:")
            )
            accept = base64.b64encode(hashlib.sha1(key + WS_GUID.encode()).digest())
            headers = [
                f"HTTP/1.1 {self.upgrade_status} Switching Protocols".encode(),
                b"Upgrade: websocket",
                b"Connection: keep-alive, Upgrade",
                b"Sec-WebSocket-Accept: " + accept,
            ]
            for name, value in self.upgrade_overrides.items():
                prefix = name.lower().encode() + b":"
                headers = [line for line in headers if not line.lower().startswith(prefix)]
                headers.append(name.encode() + b": " + value.encode())
            self.buffer.extend(b"\r\n".join(headers) + b"\r\n\r\n")
        elif data[:1] == b"\x81":
            self._verify_subscribe_request(data)
            self.in_websocket_phase = True
            if self.subscribe_frames is not None:
                self.buffer.extend(self.subscribe_frames)
            else:
                self.buffer.extend(self.before_ack)
                self.buffer.extend(server_frame(self.ack))

    @staticmethod
    def _verify_subscribe_request(data):
        assert data[0] == 0x81 and data[1] & 0x80
        size_code = data[1] & 0x7F
        offset = 2
        if size_code == 126:
            size = int.from_bytes(data[offset : offset + 2], "big")
            offset += 2
        elif size_code == 127:
            size = int.from_bytes(data[offset : offset + 8], "big")
            offset += 8
        else:
            size = size_code
        mask = data[offset : offset + 4]
        offset += 4
        payload = bytes(value ^ mask[i % 4] for i, value in enumerate(data[offset:]))
        assert len(payload) == size
        row = json.loads(payload)
        assert row["method"] == "logsSubscribe"
        assert row["id"] == 1
        assert row["params"][1] == {"commitment": "finalized"}


def make_campaign(root, *, opened_monotonic_ns=0, auth=None):
    campaign = WssReferenceCampaign(root, auth or authorization(), opened_ns=100)
    campaign.start_session(
        at_ns=101,
        free_disk_bytes=DISK_FLOOR_BYTES + 1,
        opened_monotonic_ns=opened_monotonic_ns,
    )
    return campaign


def start(campaign, stream, *, mono=None, clock=None, target=None, free_disk=None):
    now = clock or [102]

    def clock_ns():
        now[0] += 1
        return now[0]

    monotonic = mono if mono is not None else [campaign.active.opened_monotonic_ns]
    disk_reader = (
        free_disk
        if callable(free_disk)
        else lambda: DISK_FLOOR_BYTES + 1 if free_disk is None else free_disk
    )
    return upgrade_and_subscribe(
        campaign,
        stream,
        host="mainnet.helius-rpc.com",
        request_target=target or stream.request_target_fixture,
        clock_ns=clock_ns,
        monotonic_ns=lambda: monotonic[0],
        free_disk_bytes=disk_reader,
    ), clock_ns


def test_upgrade_subscribe_ack_is_raw_bound_and_recovers_without_resume(tmp_path):
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream()
    connection, clock_ns = start(campaign, stream)
    assert connection is not None
    rows = sorted((campaign.root / "sessions/01/journal").glob("*.json"))
    protocol = next(
        json.loads(path.read_text()) for path in rows if "protocol_response" in path.read_text()
    )
    ack = json.loads(rows[-1].read_text())
    raw = (campaign.root / "sessions/01" / protocol["raw_path"]).read_bytes()
    assert raw == stream.ack
    assert protocol["response_kind"] == "ack"
    assert protocol["received_ns"] < protocol["availability_ns"]
    assert ack["kind"] == "subscription_ack"
    assert ack["response_sha256"] == protocol["raw_sha256"]
    campaign.close()
    stop = WssReferenceCampaign.recover_interrupted(campaign.root, at_ns=clock_ns() + 5)
    assert stop["raw_message_bytes"] == len(stream.ack)
    assert stop["no_resume"] is True


def test_notification_uses_the_deadline_bound_stream_after_ack(tmp_path):
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream()
    connection, clock_ns = start(campaign, stream)
    stream.buffer.extend(server_frame(notification()))
    row = connection.receive_notification()
    assert row["event_slot"] == 44
    assert row["received_ns"] < row["availability_ns"]
    campaign.stop("synthetic_test_end", clock_ns() + 2)
    campaign.close()


def test_wall_clock_regression_during_notification_still_seals_campaign(tmp_path):
    now = [102]
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream()
    connection, _ = start(campaign, stream, clock=now)
    assert connection is not None
    now[0] = campaign.active.last_event_ns - 2
    with pytest.raises(WssClientError, match="notification_transport_failed"):
        connection.receive_notification()
    stop = json.loads((campaign.root / "CAMPAIGN_STOP.json").read_text())
    assert stop["reason"] == "session_clock_regression"
    assert stop["stopped_ns"] >= campaign.sessions[0].last_event_ns
    campaign.close()


def test_fragmented_ack_with_interleaved_ping_is_assembled_and_answered(tmp_path):
    ack = b'{"jsonrpc":"2.0","result":77,"id":1}'
    frames = (
        server_frame(ack[:13], opcode=1, fin=False)
        + server_frame(b"keepalive", opcode=9)
        + server_frame(ack[13:], opcode=0)
    )
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream(subscribe_frames=frames)
    connection, clock_ns = start(campaign, stream)
    assert connection is not None
    assert any(frame[:1] == b"\x8a" for frame in stream.writes)
    protocol = next(
        json.loads(path.read_text())
        for path in sorted((campaign.root / "sessions/01/journal").glob("*.json"))
        if "protocol_response" in path.read_text()
    )
    assert (campaign.root / "sessions/01" / protocol["raw_path"]).read_bytes() == ack
    campaign.stop("synthetic_test_end", clock_ns() + 1)
    campaign.close()


def test_control_ping_does_not_consume_remaining_application_message_cap(tmp_path):
    ack = b'{"jsonrpc":"2.0","result":77,"id":1}'
    ack += b" " * (MAX_CONTROL_RESPONSE_BYTES - len(ack))
    frames = (
        server_frame(ack, opcode=1, fin=False)
        + server_frame(b"p", opcode=9)
        + server_frame(b"", opcode=0)
    )
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream(subscribe_frames=frames)
    connection, clock_ns = start(campaign, stream)
    assert connection is not None
    assert any(frame[:1] == b"\x8a" for frame in stream.writes)
    campaign.stop("synthetic_test_end", clock_ns() + 1)
    campaign.close()


def test_client_module_has_no_live_network_or_credential_loader_imports():
    import ast
    from pathlib import Path

    source = Path("src/memecoin_collector/wss_client.py").read_text()
    tree = ast.parse(source)
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported.isdisjoint({"socket", "ssl", "requests", "httpx", "urllib", "keyring"})


def test_subscription_error_is_preserved_then_campaign_stops(tmp_path):
    error = b'{"jsonrpc":"2.0","error":{"code":-32600,"message":"rejected"},"id":1}'
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream(ack=error)
    with pytest.raises(WssClientError, match="subscription_error"):
        start(campaign, stream)
    stop = json.loads((campaign.root / "CAMPAIGN_STOP.json").read_text())
    assert stop["reason"] == "subscription_error"
    raw_path = next((campaign.root / "sessions/01/raw").glob("*.bin"))
    assert raw_path.read_bytes() == error
    campaign.close()
    recovered = WssReferenceCampaign.recover_interrupted(campaign.root, at_ns=120)
    assert recovered["raw_message_bytes"] == len(error)


def test_deeply_nested_subscription_response_is_preserved_and_stops(tmp_path):
    malformed = b"[" * 10_000 + b"]" * 10_000
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream(ack=malformed)
    with pytest.raises(WssClientError, match="subscription_response_invalid"):
        start(campaign, stream)
    stop = json.loads((campaign.root / "CAMPAIGN_STOP.json").read_text())
    assert stop["reason"] == "subscription_response_invalid"
    raw_path = next((campaign.root / "sessions/01/raw").glob("*.bin"))
    assert raw_path.read_bytes() == malformed
    assert campaign.sessions[0].terminal
    campaign.close()


def test_deeply_nested_notification_is_raw_and_counted_as_parse_failure(tmp_path):
    malformed = b"[" * 10_000 + b"]" * 10_000
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream()
    connection, clock_ns = start(campaign, stream)
    stream.buffer.extend(server_frame(malformed))
    row = connection.receive_notification()
    assert row["parse_state"] == "malformed_logs_message"
    assert row["raw_bytes"] == len(malformed)
    assert (campaign.root / "sessions/01" / row["raw_path"]).read_bytes() == malformed
    campaign.stop("synthetic_test_end", clock_ns() + 1)
    stop_path = sorted((campaign.root / "sessions/01/journal").glob("*.json"))[-1]
    stop = json.loads(stop_path.read_text())
    assert stop["parse_failures"] == 1
    campaign.close()


def test_bad_upgrade_records_bounded_response_and_never_saves_request_target(tmp_path):
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream(
        upgrade_overrides={
            "Sec-WebSocket-Accept": "wrong",
            "Location": "https://mainnet.helius-rpc.com/?api-key=fixture-secret",
            "X-Debug-fixture-secret": "reflected-fixture-secret",
        }
    )
    with pytest.raises(WssClientError, match="http_upgrade_failed"):
        start(campaign, stream)
    failures = [
        json.loads(path.read_text())
        for path in sorted((campaign.root / "sessions/01/journal").glob("*.json"))
        if "transport_failure" in path.read_text()
    ]
    assert failures[0]["reason"] == "http_upgrade_rejected"
    evidence = (campaign.root / "sessions/01" / failures[0]["raw_path"]).read_bytes()
    assert evidence.startswith(b"HTTP/1.1 101")
    assert b"header_names=" in evidence
    assert b"Location:" not in evidence
    assert b"fixture-secret" not in evidence
    tree = b"".join(path.read_bytes() for path in campaign.root.rglob("*") if path.is_file())
    assert b"fixture-secret" not in tree
    assert b"api-key" not in tree
    assert campaign.terminal
    campaign.close()


def test_upgrade_rejects_unrequested_extension_negotiation(tmp_path):
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream(upgrade_overrides={"Sec-WebSocket-Extensions": "permessage-deflate"})
    with pytest.raises(WssClientError, match="http_upgrade_failed"):
        start(campaign, stream)
    stop = json.loads((campaign.root / "CAMPAIGN_STOP.json").read_text())
    assert stop["reason"] == "http_upgrade_rejected"
    campaign.close()


def test_close_before_subscribe_ack_ends_window_without_resume(tmp_path):
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream(subscribe_frames=server_frame(b"", opcode=8))
    connection, _ = start(campaign, stream)
    assert connection is None
    session_stop = json.loads(
        sorted((campaign.root / "sessions/01/journal").glob("*.json"))[-1].read_text()
    )
    assert session_stop["kind"] == "session_stop"
    assert session_stop["reason"] == "connection_disconnected"
    assert campaign.active is None
    campaign.close()


def test_malformed_pre_ack_frame_fails_closed_with_campaign_stop(tmp_path):
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream(subscribe_frames=b"\xc1\x00")
    with pytest.raises(WssClientError, match="subscribe_response_invalid"):
        start(campaign, stream)
    assert campaign.terminal
    assert json.loads((campaign.root / "CAMPAIGN_STOP.json").read_text())["reason"] == (
        "subscribe_response_invalid"
    )
    campaign.close()


def test_low_disk_blocks_upgrade_and_subscription_io(tmp_path):
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream()
    with pytest.raises(WssClientError, match="disk_floor"):
        start(campaign, stream, free_disk=DISK_FLOOR_BYTES - 1)
    assert stream.writes == []
    campaign.close()


def test_disk_gate_is_rechecked_after_upgrade_before_subscription_write(tmp_path):
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream()
    free_disk = iter((DISK_FLOOR_BYTES + 1, DISK_FLOOR_BYTES - 1))
    with pytest.raises(WssClientError, match="disk_floor"):
        start(campaign, stream, free_disk=lambda: next(free_disk))
    assert len(stream.writes) == 1
    assert stream.writes[0].startswith(b"GET ")
    campaign.close()


def test_expired_authorization_blocks_upgrade_and_subscription_io(tmp_path):
    auth = authorization()
    campaign = make_campaign(tmp_path / "campaign", auth=auth)
    stream = FakeTLSStream()
    with pytest.raises(WssClientError, match="authorization_expired"):
        start(campaign, stream, clock=[auth["expires_at_ns"]])
    assert stream.writes == []
    campaign.close()


def test_authorization_expiring_during_pre_ack_read_is_not_accepted(tmp_path):
    auth = authorization()
    now = [102]
    campaign = make_campaign(tmp_path / "campaign", auth=auth)
    stream = FakeTLSStream(wall_clock=now, expire_on_websocket_payload=True)
    with pytest.raises(WssClientError, match="authorization_expired"):
        start(campaign, stream, clock=now)
    assert len(stream.writes) == 2
    assert not any(
        json.loads(path.read_text()).get("kind") == "subscription_ack"
        for path in campaign.root.rglob("*.json")
    )
    stop = json.loads((campaign.root / "CAMPAIGN_STOP.json").read_text())
    assert stop["reason"] == "authorization_expired"
    campaign.close()


def test_wall_clock_regression_seals_campaign_before_any_io(tmp_path):
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream()
    with pytest.raises(WssClientError, match="session_clock_regression"):
        start(campaign, stream, clock=[50])
    assert stream.writes == []
    stop = json.loads((campaign.root / "CAMPAIGN_STOP.json").read_text())
    assert stop["reason"] == "session_clock_regression"
    assert stop["stopped_ns"] >= campaign.sessions[0].opened_ns
    campaign.close()


def test_future_monotonic_origin_cannot_extend_session_deadline(tmp_path):
    from memecoin_collector.wss_reference import MAX_SESSION_NS

    campaign = make_campaign(tmp_path / "campaign", opened_monotonic_ns=10 * MAX_SESSION_NS)
    stream = FakeTLSStream()
    with pytest.raises(WssClientError, match="session_deadline_invalid"):
        start(campaign, stream, mono=[0])
    assert stream.writes == []
    campaign.close()


def test_unverified_tls_peer_stops_before_any_stream_write(tmp_path):
    campaign = make_campaign(tmp_path / "campaign")
    stream = FakeTLSStream(tls_ok=False)
    with pytest.raises(WssClientError, match="tls_or_upgrade_authority_invalid"):
        start(campaign, stream)
    assert stream.writes == []
    assert json.loads((campaign.root / "CAMPAIGN_STOP.json").read_text())["reason"] == (
        "http_upgrade_rejected"
    )
    campaign.close()


def test_deadline_is_passed_to_partial_header_reads_and_stops(tmp_path):
    campaign = make_campaign(tmp_path / "campaign")
    monotonic = [0]
    stream = FakeTLSStream(mono=monotonic, expire_after_first_upgrade_byte=True)
    with pytest.raises(WssClientError, match="http_upgrade_failed"):
        start(campaign, stream, mono=monotonic)
    assert stream.read_sizes == [1]
    failure = next(
        json.loads(path.read_text())
        for path in sorted((campaign.root / "sessions/01/journal").glob("*.json"))
        if "transport_failure" in path.read_text()
    )
    assert failure["reason"] == "client_deadline"
    evidence = (campaign.root / "sessions/01" / failure["raw_path"]).read_bytes()
    assert b"raw_bytes=1" in evidence
    assert b"GET " not in evidence
    campaign.close()


def test_oversized_subscribe_ack_rejects_from_header_before_payload(tmp_path):
    campaign = make_campaign(tmp_path / "campaign")
    declared = MAX_CONTROL_RESPONSE_BYTES + 1
    stream = FakeTLSStream(subscribe_frames=server_frame_header_only(declared))
    with pytest.raises(WssClientError, match="subscribe_response_cap"):
        start(campaign, stream)
    assert stream.websocket_read_sizes == [2, 8]
    failure = next(
        json.loads(path.read_text())
        for path in sorted((campaign.root / "sessions/01/journal").glob("*.json"))
        if "transport_failure" in path.read_text()
    )
    assert failure["reason"] == "subscribe_response_cap"
    campaign.close()


def server_frame_header_only(size):
    return b"\x81\x7f" + size.to_bytes(8, "big")
