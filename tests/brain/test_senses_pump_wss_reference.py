import hashlib
import json
import random

import pytest

from memecoin_collector.wss_reference import WssReferenceSession, default_binding


def message(slot, signature, *, err=None, logs=None, subscription=9):
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "logsNotification",
            "params": {
                "subscription": subscription,
                "result": {
                    "context": {"slot": slot},
                    "value": {"signature": signature, "err": err, "logs": logs},
                },
            },
        },
        separators=(",", ":"),
    ).encode()


def session(tmp_path):
    value = WssReferenceSession(tmp_path / "session", default_binding(), opened_ns=100)
    value.connection_opened(101)
    value.request_subscribe(102)
    value.acknowledge(9, 103)
    return value


def deliver(value, raw, received_ns, available_ns):
    assert value.prepare_receive(at_ns=received_ns, free_disk_bytes=51 * 1024**3)
    return value.record_message(raw, received_ns=received_ns, clock_ns=lambda: available_ns)


def test_retains_raw_before_classifying_malformed_message(tmp_path):
    value = session(tmp_path)
    raw = b'{"jsonrpc":"2.0","method":"logsNotification","params":{"subscription":9}}'
    row = deliver(value, raw, 104, 110)
    assert row["parse_state"] == "logs_message_slot_missing"
    assert (value.root / row["raw_path"]).read_bytes() == raw
    assert row["received_ns"] == 104
    assert row["availability_ns"] == 110
    assert row["decision_ns"] is None


def test_duplicate_deliveries_remain_separate_raw_observations(tmp_path):
    value = session(tmp_path)
    raw = message(42, "sig-A", logs=["Program log: x"])
    first = deliver(value, raw, 104, 105)
    second = deliver(value, raw, 106, 107)
    assert first["duplicate_delivery"] is False
    assert second["duplicate_delivery"] is True
    assert first["raw_path"] != second["raw_path"]
    assert (value.root / first["raw_path"]).read_bytes() == raw
    assert (value.root / second["raw_path"]).read_bytes() == raw


def test_wrong_subscription_identity_is_preserved_but_quarantined(tmp_path):
    value = session(tmp_path)
    raw = message(42, "sig-other", subscription=17)
    row = deliver(value, raw, 104, 105)
    assert row["parse_state"] == "logs_message_subscription_mismatch"
    assert row["signature"] is None
    assert (value.root / row["raw_path"]).read_bytes() == raw


def test_generated_reference_delivery_identity_never_discards_duplicates(tmp_path):
    rng = random.Random(20261010)
    signatures = [f"sig-{i}" for i in range(11)]
    deliveries = [(rng.randrange(1, 5), rng.choice(signatures)) for _ in range(300)]
    value = session(tmp_path)
    seen = set()
    duplicate_count = 0
    for index, (slot, signature) in enumerate(deliveries, start=104):
        row = deliver(
            value,
            message(
                slot, signature, err={"InstructionError": [0, "Custom"]} if index % 7 == 0 else None
            ),
            index,
            index + 1,
        )
        identity = (slot, signature)
        assert row["duplicate_delivery"] == (identity in seen)
        duplicate_count += identity in seen
        seen.add(identity)
    files = sorted((value.root / "raw").glob("*.bin"))
    assert len(files) == len(deliveries)
    assert value.raw_bytes == sum(p.stat().st_size for p in files)
    assert duplicate_count > 0


def test_recovery_seals_interrupted_session_and_never_resumes(tmp_path):
    value = session(tmp_path)
    raw = message(51, "sig-B")
    deliver(value, raw, 104, 106)
    recovered = WssReferenceSession.recover(value.root, at_ns=120)
    assert recovered.terminal is True
    stop = json.loads(sorted((value.root / "journal").glob("*.json"))[-1].read_text())
    assert stop["reason"] == "process_interrupted_no_resume"
    with pytest.raises(ValueError, match="wss_session_terminal"):
        recovered.record_message(raw, received_ns=121, clock_ns=lambda: 122)


def test_recovery_accounts_for_raw_write_without_journal_row(tmp_path):
    value = session(tmp_path)
    orphan = b"raw message was durable before interruption"
    path = value.root / "raw" / "00000003.bin"
    path.write_bytes(orphan)
    path.chmod(0o600)
    recovered = WssReferenceSession.recover(value.root, at_ns=120)
    stop = json.loads(sorted((value.root / "journal").glob("*.json"))[-1].read_text())
    assert recovered.terminal is True
    assert stop["reason"] == "process_interrupted_after_raw_write_no_resume"
    assert stop["unreferenced_raw_files"] == [
        {
            "path": "raw/00000003.bin",
            "bytes": len(orphan),
            "sha256": hashlib.sha256(orphan).hexdigest(),
        }
    ]


def test_recovery_fails_closed_on_raw_mutation(tmp_path):
    value = session(tmp_path)
    row = deliver(value, message(9, "sig-C"), 104, 105)
    (value.root / row["raw_path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="wss_raw_capture_hash_mismatch"):
        WssReferenceSession.recover(value.root, at_ns=120)


def test_notification_before_ack_and_time_regression_fail_closed(tmp_path):
    value = WssReferenceSession(tmp_path / "session", default_binding(), opened_ns=100)
    with pytest.raises(ValueError, match="wss_notification_before_ack"):
        value.record_message(message(1, "sig-D"), received_ns=101, clock_ns=lambda: 102)
    value.connection_opened(102)
    value.request_subscribe(103)
    with pytest.raises(ValueError, match="wss_ack_outside_session_window"):
        value.acknowledge(1, 99)


def test_capacity_floor_is_checked_before_any_next_message_read(tmp_path):
    value = session(tmp_path)
    assert value.prepare_receive(at_ns=104, free_disk_bytes=51 * 1024**3) is True
    assert value.prepare_receive(at_ns=105, free_disk_bytes=49 * 1024**3) is False
    stop = json.loads(sorted((value.root / "journal").glob("*.json"))[-1].read_text())
    assert stop["kind"] == "session_stop"
    assert stop["reason"] == "disk_floor"
    assert list((value.root / "raw").iterdir()) == []
