import fcntl
import hashlib
import json
import os

import pytest

from memecoin_collector.wss_campaign import (
    WssReferenceCampaign,
    default_authorization,
)
from memecoin_collector.wss_reference import DISK_FLOOR_BYTES, MAX_SESSION_NS


def authorization():
    return default_authorization(
        c4_stop_sha256="0" * 64,
        account_budget_evidence_sha256="1" * 64,
        authorized_at_ns=100,
        expires_at_ns=100 + 6 * 24 * 60 * 60 * 1_000_000_000,
    )


def notification(slot, signature):
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "logsNotification",
            "params": {
                "subscription": 9,
                "result": {
                    "context": {"slot": slot},
                    "value": {"signature": signature, "err": None, "logs": []},
                },
            },
        },
        separators=(",", ":"),
    ).encode()


class Reader:
    def __init__(self, data):
        self.data = data
        self.offset = 0
        self.read_sizes = []

    def read_exactly(self, size):
        self.read_sizes.append(size)
        end = self.offset + size
        result = self.data[self.offset : end]
        self.offset = end
        return result


def frame(opcode, payload, *, fin=True):
    first = bytes([(0x80 if fin else 0) | opcode])
    if len(payload) <= 125:
        header = first + bytes([len(payload)])
    else:
        header = first + bytes([126]) + len(payload).to_bytes(2, "big")
    return header + payload


def acknowledged_campaign(root):
    campaign = WssReferenceCampaign(root, authorization(), opened_ns=100)
    session = campaign.start_session(at_ns=101, free_disk_bytes=DISK_FLOOR_BYTES + 1)
    session.connection_opened(102)
    session.request_subscribe(103)
    session.acknowledge(9, 104)
    return campaign


def connected(campaign, at):
    session = campaign.start_session(at_ns=at, free_disk_bytes=DISK_FLOOR_BYTES + 1)
    session.connection_opened(at + 1)
    session.request_subscribe(at + 2)
    session.acknowledge(9, at + 3)
    assert campaign.prepare_receive(at_ns=at + 4, free_disk_bytes=DISK_FLOOR_BYTES + 1)
    session.record_message(
        notification(at, f"sig-{at}"),
        received_ns=at + 5,
        clock_ns=lambda: at + 6,
    )
    return session


def test_two_sessions_keep_budgets_and_explicit_disconnect_gap(tmp_path):
    campaign = WssReferenceCampaign(tmp_path / "campaign", authorization(), opened_ns=100)
    connected(campaign, 101)
    with pytest.raises(ValueError, match="wss_campaign_duration_cap_not_reached"):
        campaign.stop_session("session_duration_cap", 149)
    first_stop_ns = 101 + MAX_SESSION_NS
    campaign.stop_session("session_duration_cap", first_stop_ns)
    second_open_ns = first_stop_ns + 10
    connected(campaign, second_open_ns)
    with pytest.raises(ValueError, match="wss_campaign_duration_cap_not_reached"):
        campaign.stop_session("session_duration_cap", second_open_ns + 9)
    stop = campaign.stop_session("connection_disconnected", second_open_ns + 10)
    assert stop["raw_bytes"] == len(notification(second_open_ns, f"sig-{second_open_ns}"))
    campaign_stop = json.loads((campaign.root / "CAMPAIGN_STOP.json").read_text())
    assert campaign_stop["session_count"] == 2
    assert campaign_stop["raw_message_bytes"] == len(notification(101, "sig-101")) + len(
        notification(second_open_ns, f"sig-{second_open_ns}")
    )
    assert campaign_stop["no_backfill_or_http_retrieval"] is True
    second_start = json.loads((campaign.root / "sessions/02/campaign-start.json").read_text())
    assert second_start["preceding_gap"] == {
        "start_ns": first_stop_ns,
        "end_ns": second_open_ns,
        "explicit": True,
    }
    with pytest.raises(ValueError, match="wss_campaign_terminal"):
        campaign.start_session(at_ns=151, free_disk_bytes=DISK_FLOOR_BYTES + 1)
    campaign.close()


def test_one_writer_lock_and_recovery_seal_without_resume(tmp_path):
    root = tmp_path / "campaign"
    campaign = WssReferenceCampaign(root, authorization(), opened_ns=100)
    connected(campaign, 101)
    fd = os.open(root / ".writer.lock", os.O_RDONLY)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="wss_campaign_writer_active"):
            WssReferenceCampaign.recover_interrupted(root, at_ns=120)
    finally:
        os.close(fd)
    session = campaign.active
    assert session is not None
    journal_count = len(list((root / "sessions/01/journal").glob("*.json")))
    campaign.close()
    with pytest.raises(ValueError, match="wss_campaign_writer_released"):
        session.record_transport_failure(
            phase="http_upgrade",
            reason="http_upgrade_rejected",
            raw_evidence=b"",
            at_ns=120,
        )
    assert len(list((root / "sessions/01/journal").glob("*.json"))) == journal_count
    with pytest.raises(ValueError, match="wss_campaign_writer_released"):
        campaign.start_session(at_ns=120, free_disk_bytes=DISK_FLOOR_BYTES + 1)
    stop = WssReferenceCampaign.recover_interrupted(root, at_ns=120)
    assert stop["reason"] == "process_interrupted_no_resume"
    assert stop["session_count"] == 1
    session_rows = sorted((root / "sessions/01/journal").glob("*.json"))
    session_stop = json.loads(session_rows[-1].read_text())
    assert session_stop["reason"] == "process_interrupted_no_resume"
    with pytest.raises(ValueError, match="wss_campaign_root_exists"):
        WssReferenceCampaign(root, authorization(), opened_ns=130)


def test_authorization_expiry_and_binding_mutation_fail_closed(tmp_path):
    bad = default_authorization(
        c4_stop_sha256="0" * 64,
        account_budget_evidence_sha256="1" * 64,
        authorized_at_ns=100,
        expires_at_ns=100 + 8 * 24 * 60 * 60 * 1_000_000_000,
    )
    with pytest.raises(ValueError, match="wss_campaign_authorization_window_invalid"):
        WssReferenceCampaign(tmp_path / "expired", bad, opened_ns=100)

    campaign = WssReferenceCampaign(tmp_path / "bound", authorization(), opened_ns=100)
    campaign.close()
    path = tmp_path / "bound" / "authorization.json"
    data = json.loads(path.read_text())
    data["max_sessions"] = 3
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="wss_campaign_authorization_binding_mismatch"):
        WssReferenceCampaign.recover_interrupted(tmp_path / "bound", at_ns=110)


def test_campaign_stop_record_is_hash_sealed(tmp_path):
    campaign = WssReferenceCampaign(tmp_path / "sealed", authorization(), opened_ns=100)
    campaign.stop("synthetic_test_end", 110)
    root = campaign.root
    campaign.close()
    path = root / "CAMPAIGN_STOP.json"
    row = json.loads(path.read_text())
    row["reason"] = "changed"
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="wss_campaign_stop_hash_mismatch"):
        WssReferenceCampaign.recover_interrupted(root, at_ns=120)


def test_campaign_stop_recovery_rederives_aggregate_accounting(tmp_path):
    root = tmp_path / "accounted"
    campaign = WssReferenceCampaign(root, authorization(), opened_ns=100)
    connected(campaign, 101)
    campaign.stop("synthetic_test_end", 110)
    campaign.close()
    path = root / "CAMPAIGN_STOP.json"
    row = json.loads(path.read_text())
    row.pop("sha256")
    row["raw_message_bytes"] += 1
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    row["sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    path.write_text(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="wss_campaign_stop_accounting_mismatch"):
        WssReferenceCampaign.recover_interrupted(root, at_ns=120)


def test_campaign_preflight_precedes_frame_header_read(tmp_path):
    campaign = acknowledged_campaign(tmp_path / "campaign")
    reader = Reader(b"\x81\x00")
    assert (
        campaign.receive_json_message(
            reader,
            at_ns=105,
            free_disk_bytes=DISK_FLOOR_BYTES - 1,
            clock_ns=lambda: 106,
        )
        is None
    )
    assert reader.read_sizes == []
    assert (campaign.root / "CAMPAIGN_STOP.json").is_file()
    campaign.close()


def test_oversized_frame_is_journaled_without_payload_read(tmp_path):
    from memecoin_collector.wss_reference import MAX_MESSAGE_BYTES

    campaign = acknowledged_campaign(tmp_path / "campaign")
    declared = MAX_MESSAGE_BYTES + 1
    reader = Reader(b"\x81\x7f" + declared.to_bytes(8, "big"))
    assert (
        campaign.receive_json_message(
            reader,
            at_ns=105,
            free_disk_bytes=DISK_FLOOR_BYTES + 1,
            clock_ns=lambda: 106,
        )
        is None
    )
    assert reader.read_sizes == [2, 8]
    session_stop_path = sorted((campaign.root / "sessions/01/journal").glob("*.json"))[-1]
    session_stop = json.loads(session_stop_path.read_text())
    assert session_stop["reason"] == "websocket_frame_payload_cap"
    assert session_stop["rejected_messages_or_frames"] == 1
    campaign_stop = json.loads((campaign.root / "CAMPAIGN_STOP.json").read_text())
    assert campaign_stop["reason"] == "websocket_payload_cap"
    campaign.close()


def test_receive_reassembles_text_and_answers_ping_with_masked_pong(tmp_path):
    campaign = acknowledged_campaign(tmp_path / "campaign")
    raw = notification(42, "sig-fragmented")
    split = 15
    data = frame(1, raw[:split], fin=False) + frame(9, b"ping") + frame(0, raw[split:])
    reader = Reader(data)
    response = []
    ticks = iter((105, 106, 107, 108, 109))
    row = campaign.receive_json_message(
        reader,
        at_ns=105,
        free_disk_bytes=DISK_FLOOR_BYTES + 1,
        clock_ns=lambda: next(ticks),
        send_client_frame=response.append,
    )
    assert row["signature"] == "sig-fragmented"
    assert row["event_slot"] == 42
    assert row["received_ns"] == 108
    assert reader.offset == len(data)
    assert len(response) == 1 and response[0][0] == 0x8A and response[0][1] & 0x80
    campaign.stop("synthetic_test_end", 110)
    campaign.close()
