from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256
from brain_senses_gecko_lane_monitor_v2 import (
    EVENT_DIR,
    MAX_EVENTS,
    SNAPSHOT_PATH,
    MonitorError,
    publish_snapshot,
    read_transition_receipts,
    snapshot_is_stale,
    validate_snapshot,
)


def _sha(label: str) -> str:
    return sha256(label.encode())


COORD_POLICY = _sha("coordinator-policy")


def _writer(root: Path, *, fault_hook=None) -> DescriptorWriter:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        return DescriptorWriter(fd, fault_hook=fault_hook)
    finally:
        os.close(fd)


def _publish(writer: DescriptorWriter, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "observed_at_ns": 1_000,
        "monotonic_sample_ns": 500,
        "status": "healthy",
        "reason": "none",
        "root_id": "brain-data-test",
        "volume_uuid": "ABCD-1234",
        "request_count": 4,
        "result_count": 4,
        "request_tail_sha256": _sha("requests-1"),
        "result_tail_sha256": _sha("results-1"),
        "coordinator_sequence": 7,
        "coordinator_tail_sha256": _sha("coordinator-1"),
        "coordinator_policy_sha256": COORD_POLICY,
        "active_bucket_close_ns": 900,
        "cooldown_until_ns": None,
        "stop_latched": False,
        "free_bytes": 100_000_000,
        "source_sha256": _sha("sources"),
        "plan_sha256": _sha("plan"),
    }
    fields.update(overrides)
    return publish_snapshot(writer, **fields)


def _validate(writer: DescriptorWriter, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "expected_request_tail_sha256": _sha("requests-1"),
        "expected_result_tail_sha256": _sha("results-1"),
        "verified_coordinator_ancestors": [
            {
                "sequence": 7,
                "tail_sha256": _sha("coordinator-1"),
                "policy_sha256": COORD_POLICY,
                "root_id": "brain-data-test",
                "volume_uuid": "abcd-1234",
            },
            {
                "sequence": 8,
                "tail_sha256": _sha("helius-append"),
                "policy_sha256": COORD_POLICY,
                "root_id": "brain-data-test",
                "volume_uuid": "abcd-1234",
            },
        ],
        "expected_coordinator_policy_sha256": COORD_POLICY,
        "expected_root_id": "brain-data-test",
        "expected_volume_uuid": "abcd-1234",
    }
    fields.update(overrides)
    return validate_snapshot(writer, **fields)


class _MemoryWriter:
    """Fast deterministic sink for the 30-day O(1)-storage heartbeat test."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def list_names(self, relative: str) -> tuple[str, ...]:
        prefix = f"{relative}/"
        return tuple(sorted(path[len(prefix) :] for path in self.files if path.startswith(prefix)))

    def read_bytes(self, relative: str, *, max_bytes: int) -> bytes:
        del max_bytes
        try:
            return self.files[relative]
        except KeyError as exc:
            raise FileNotFoundError(relative) from exc

    def atomic_write(self, relative: str, payload: bytes, *, create_only: bool = False) -> None:
        if create_only and relative in self.files:
            raise FileExistsError(relative)
        self.files[relative] = payload

    def create(self, relative: str, payload: bytes) -> None:
        if relative in self.files:
            raise FileExistsError(relative)
        self.files[relative] = payload


def test_healthy_ticks_replace_constant_size_snapshot_without_events(tmp_path: Path) -> None:
    with _writer(tmp_path / "root") as writer:
        first = _publish(writer)
        first_size = writer.stat(SNAPSHOT_PATH).st_size
        for index in range(1, 40):
            _publish(writer, observed_at_ns=1_000 + index, monotonic_sample_ns=500 + index)
        latest = _validate(writer)
        assert latest["generation"] == 39
        assert latest["snapshot_sha256"] != first["snapshot_sha256"]
        assert writer.stat(SNAPSHOT_PATH).st_size <= 8192
        assert writer.stat(SNAPSHOT_PATH).st_size - first_size <= 2
        assert read_transition_receipts(writer) == []
        assert writer.list_files() == [SNAPSHOT_PATH]


def test_alarm_is_sparse_retained_through_recovery_and_cooldown_does_not_repeat(
    tmp_path: Path,
) -> None:
    with _writer(tmp_path / "root") as writer:
        alarm = _publish(
            writer,
            status="stopped",
            reason="rate_limited",
            stop_latched=True,
            cooldown_until_ns=10_000,
        )
        _publish(
            writer,
            observed_at_ns=1_001,
            monotonic_sample_ns=501,
            status="stopped",
            reason="rate_limited",
            stop_latched=True,
            cooldown_until_ns=10_000,
        )
        _publish(
            writer,
            observed_at_ns=1_002,
            monotonic_sample_ns=502,
            status="stopped",
            reason="rate_limited",
            stop_latched=True,
            cooldown_until_ns=10_000,
        )
        events = read_transition_receipts(writer)
        assert len(events) == 1
        assert events[0]["kind"] == "alarm"
        assert _validate(writer)["active_alarm_reason"] == "rate_limited"

        recovered = _publish(
            writer,
            observed_at_ns=1_003,
            monotonic_sample_ns=503,
            status="healthy",
            reason="none",
            stop_latched=False,
            cooldown_until_ns=None,
        )
        events = read_transition_receipts(writer)
        assert [event["kind"] for event in events] == ["alarm", "recovery"]
        assert events[0]["event_sha256"] == alarm["alarm_ledger_tail_sha256"]
        assert recovered["active_alarm_reason"] is None
        _publish(writer, observed_at_ns=1_004, monotonic_sample_ns=504)
        assert len(read_transition_receipts(writer)) == 2


def test_alarm_reason_change_closes_previous_before_opening_new(tmp_path: Path) -> None:
    with _writer(tmp_path / "root") as writer:
        _publish(writer, status="stopped", reason="rate_limited", stop_latched=True)
        _publish(
            writer,
            observed_at_ns=1_001,
            monotonic_sample_ns=501,
            status="stopped",
            reason="storage_error",
            stop_latched=True,
        )
        events = read_transition_receipts(writer)
        assert [event["kind"] for event in events] == ["alarm", "recovery", "alarm"]
        assert [event["reason"] for event in events] == [
            "rate_limited",
            "rate_limited",
            "storage_error",
        ]


def test_snapshot_must_match_independently_verified_journal_tails(tmp_path: Path) -> None:
    with _writer(tmp_path / "root") as writer:
        _publish(writer)
        with pytest.raises(MonitorError, match="journal_tail_mismatch"):
            _validate(writer, expected_result_tail_sha256=_sha("different"))
        with pytest.raises(MonitorError, match="coordinator_ancestor_mismatch"):
            _validate(
                writer,
                verified_coordinator_ancestors=[
                    {
                        "sequence": 7,
                        "tail_sha256": _sha("modified-prefix"),
                        "policy_sha256": COORD_POLICY,
                        "root_id": "brain-data-test",
                        "volume_uuid": "abcd-1234",
                    }
                ],
            )
        with pytest.raises(MonitorError, match="volume_uuid_mismatch"):
            _validate(writer, expected_volume_uuid="different-volume")


def test_snapshot_coordinator_tail_is_verified_ancestor_before_unrelated_append(
    tmp_path: Path,
) -> None:
    with _writer(tmp_path / "root") as writer:
        _publish(writer)
        snapshot = _validate(writer)
        assert snapshot["coordinator_sequence"] == 7
        assert snapshot["coordinator_tail_sha256"] == _sha("coordinator-1")
        assert snapshot["coordinator_sequence"] < 8  # later shared-ledger append is unrelated


def test_journal_recovery_transition_binds_old_and_new_tails(tmp_path: Path) -> None:
    with _writer(tmp_path / "root") as writer:
        prior = _publish(writer)
        reconciliation = {
            "prior_snapshot_sha256": prior["snapshot_sha256"],
            "prior_request_tail_sha256": prior["request_tail_sha256"],
            "prior_result_tail_sha256": prior["result_tail_sha256"],
            "prior_coordinator_sequence": prior["coordinator_sequence"],
            "prior_coordinator_tail_sha256": prior["coordinator_tail_sha256"],
            "new_request_tail_sha256": _sha("requests-replayed"),
            "new_result_tail_sha256": _sha("results-replayed"),
            "new_coordinator_sequence": 9,
            "new_coordinator_tail_sha256": _sha("coordinator-replayed"),
            "recovery_proof_sha256": _sha("verified-recovery-proof"),
        }
        _publish(
            writer,
            observed_at_ns=1_001,
            monotonic_sample_ns=501,
            request_count=5,
            result_count=5,
            request_tail_sha256=reconciliation["new_request_tail_sha256"],
            result_tail_sha256=reconciliation["new_result_tail_sha256"],
            coordinator_sequence=9,
            coordinator_tail_sha256=reconciliation["new_coordinator_tail_sha256"],
            reconciliation=reconciliation,
        )
        events = read_transition_receipts(writer)
        assert len(events) == 1
        assert events[0]["kind"] == "journal_reconciled"
        assert events[0]["reconciliation"] == reconciliation
        with pytest.raises(MonitorError, match="coordinator_ancestor_mismatch"):
            validate_snapshot(
                writer,
                expected_request_tail_sha256=reconciliation["new_request_tail_sha256"],
                expected_result_tail_sha256=reconciliation["new_result_tail_sha256"],
                expected_coordinator_policy_sha256=COORD_POLICY,
                verified_coordinator_ancestors=[],
            )
        verified = validate_snapshot(
            writer,
            expected_request_tail_sha256=reconciliation["new_request_tail_sha256"],
            expected_result_tail_sha256=reconciliation["new_result_tail_sha256"],
            expected_coordinator_policy_sha256=COORD_POLICY,
            verified_coordinator_ancestors=[
                {
                    "sequence": 9,
                    "tail_sha256": reconciliation["new_coordinator_tail_sha256"],
                    "policy_sha256": COORD_POLICY,
                    "root_id": "brain-data-test",
                    "volume_uuid": "abcd-1234",
                }
            ],
        )
        assert verified["request_count"] == 5


def test_local_tail_advance_requires_reconciliation_receipt(tmp_path: Path) -> None:
    with _writer(tmp_path / "root") as writer:
        _publish(writer)
        with pytest.raises(MonitorError, match="unreceipted_journal_advance"):
            _publish(
                writer,
                observed_at_ns=1_001,
                monotonic_sample_ns=501,
                request_tail_sha256=_sha("changed-without-proof"),
            )


def test_reconciliation_event_before_snapshot_is_idempotently_recovered(tmp_path: Path) -> None:
    root = tmp_path / "root"
    with _writer(root) as writer:
        prior = _publish(writer)
    reconciliation = {
        "prior_snapshot_sha256": prior["snapshot_sha256"],
        "prior_request_tail_sha256": prior["request_tail_sha256"],
        "prior_result_tail_sha256": prior["result_tail_sha256"],
        "prior_coordinator_sequence": prior["coordinator_sequence"],
        "prior_coordinator_tail_sha256": prior["coordinator_tail_sha256"],
        "new_request_tail_sha256": _sha("recovered-requests"),
        "new_result_tail_sha256": _sha("recovered-results"),
        "new_coordinator_sequence": 9,
        "new_coordinator_tail_sha256": _sha("recovered-coordinator"),
        "recovery_proof_sha256": _sha("recovery-proof"),
    }

    def fail_before_rename(operation: str) -> None:
        if operation == "atomic_rename":
            raise OSError("crash before latest snapshot rename")

    with _writer(root, fault_hook=fail_before_rename) as writer:
        with pytest.raises(OSError, match="crash before"):
            _publish(
                writer,
                observed_at_ns=1_001,
                monotonic_sample_ns=501,
                request_count=5,
                result_count=5,
                request_tail_sha256=reconciliation["new_request_tail_sha256"],
                result_tail_sha256=reconciliation["new_result_tail_sha256"],
                coordinator_sequence=9,
                coordinator_tail_sha256=reconciliation["new_coordinator_tail_sha256"],
                reconciliation=reconciliation,
            )
        assert len(read_transition_receipts(writer)) == 1

    with _writer(root) as writer:
        repaired = _publish(
            writer,
            observed_at_ns=1_002,
            monotonic_sample_ns=502,
            request_count=5,
            result_count=5,
            request_tail_sha256=reconciliation["new_request_tail_sha256"],
            result_tail_sha256=reconciliation["new_result_tail_sha256"],
            coordinator_sequence=9,
            coordinator_tail_sha256=reconciliation["new_coordinator_tail_sha256"],
            reconciliation=reconciliation,
        )
        assert repaired["generation"] == 1
        assert len(read_transition_receipts(writer)) == 1


def test_coordinator_only_recovery_progress_gets_distinct_receipt(tmp_path: Path) -> None:
    with _writer(tmp_path / "root") as writer:
        prior = _publish(writer)
        reconciliation = {
            "prior_snapshot_sha256": prior["snapshot_sha256"],
            "prior_request_tail_sha256": prior["request_tail_sha256"],
            "prior_result_tail_sha256": prior["result_tail_sha256"],
            "prior_coordinator_sequence": prior["coordinator_sequence"],
            "prior_coordinator_tail_sha256": prior["coordinator_tail_sha256"],
            "new_request_tail_sha256": prior["request_tail_sha256"],
            "new_result_tail_sha256": prior["result_tail_sha256"],
            "new_coordinator_sequence": 8,
            "new_coordinator_tail_sha256": _sha("gecko-recovery-release"),
            "recovery_proof_sha256": _sha("coordinator-recovery-proof"),
        }
        _publish(
            writer,
            observed_at_ns=1_001,
            monotonic_sample_ns=501,
            coordinator_sequence=8,
            coordinator_tail_sha256=reconciliation["new_coordinator_tail_sha256"],
            reconciliation=reconciliation,
        )
        events = read_transition_receipts(writer)
        assert len(events) == 1
        assert events[0]["kind"] == "journal_reconciled"
        assert events[0]["reconciliation"] == reconciliation


def test_completed_operation_can_advance_local_tails_without_recovery_event(
    tmp_path: Path,
) -> None:
    with _writer(tmp_path / "root") as writer:
        _publish(writer)
        _publish(
            writer,
            observed_at_ns=1_001,
            monotonic_sample_ns=501,
            request_count=5,
            result_count=5,
            request_tail_sha256=_sha("normal-new-requests"),
            result_tail_sha256=_sha("normal-new-results"),
            completed_operation=True,
        )
        assert read_transition_receipts(writer) == []
        assert (
            _validate(
                writer,
                expected_request_tail_sha256=_sha("normal-new-requests"),
                expected_result_tail_sha256=_sha("normal-new-results"),
            )["request_count"]
            == 5
        )


def test_snapshot_reader_rejects_rehashed_wrong_field_types(tmp_path: Path) -> None:
    with _writer(tmp_path / "root") as writer:
        _publish(writer)
        record = json.loads(writer.read_bytes(SNAPSHOT_PATH))
        record.pop("snapshot_sha256")
        record["request_count"] = "4"
        record["snapshot_sha256"] = sha256(canonical_json(record))
        writer.atomic_write(SNAPSHOT_PATH, canonical_json(record))
        with pytest.raises(MonitorError, match="snapshot_request_count"):
            _validate(writer)


def test_30_day_sixty_second_heartbeat_keeps_latest_snapshot_bounded() -> None:
    writer = _MemoryWriter()
    count = 30 * 24 * 60
    for index in range(count):
        _publish(
            writer,  # type: ignore[arg-type]
            observed_at_ns=1_000 + index * 60_000_000_000,
            monotonic_sample_ns=500 + index * 60_000_000_000,
        )
    snapshot = _read_memory_snapshot(writer)
    assert snapshot["generation"] == count - 1
    assert len(writer.files) == 1
    assert len(writer.files[SNAPSHOT_PATH]) <= 8192


def _read_memory_snapshot(writer: _MemoryWriter) -> dict[str, Any]:
    import brain_senses_gecko_lane_monitor_v2 as monitor

    snapshot = monitor._read_snapshot(writer)  # type: ignore[arg-type]
    assert snapshot is not None
    return snapshot


def test_liveness_staleness_is_external_and_not_persisted(tmp_path: Path) -> None:
    with _writer(tmp_path / "root") as writer:
        snapshot = _publish(writer)
        assert snapshot_is_stale(snapshot, now_ns=1_100, max_age_ns=100) is False
        assert snapshot_is_stale(snapshot, now_ns=1_101, max_age_ns=100) is True
        assert "stale" not in snapshot
        assert "liveness" not in snapshot
        with pytest.raises(MonitorError, match="monitor_stale"):
            _validate(writer, now_ns=1_101, max_age_ns=100)


def test_atomic_snapshot_fault_before_rename_preserves_old_valid_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "root"
    with _writer(root) as writer:
        old = _publish(writer)
        old_raw = writer.read_bytes(SNAPSHOT_PATH)

    def fault(operation: str) -> None:
        if operation == "atomic_rename":
            raise OSError("synthetic before rename")

    with _writer(root, fault_hook=fault) as writer:
        with pytest.raises(OSError, match="synthetic before rename"):
            _publish(writer, observed_at_ns=1_001, monotonic_sample_ns=501)
    with _writer(root) as writer:
        assert writer.read_bytes(SNAPSHOT_PATH) == old_raw
        assert _validate(writer)["snapshot_sha256"] == old["snapshot_sha256"]
        assert not any(name.endswith(".tmp") for name in writer.list_files())


def test_detach_before_atomic_replace_does_not_write_to_mountpoint_underlay(tmp_path: Path):
    mount = tmp_path / "mount"
    held = tmp_path / "held-volume"
    mount.mkdir(mode=0o700)
    with _writer(mount) as writer:
        old = _publish(writer)
        old_raw = writer.read_bytes(SNAPSHOT_PATH)

    def detach_before_rename(operation: str) -> None:
        if operation == "atomic_rename":
            os.rename(mount, held)
            mount.mkdir(mode=0o700)
            raise OSError("synthetic detach before rename")

    with _writer(mount, fault_hook=detach_before_rename) as writer:
        with pytest.raises(OSError, match="synthetic detach"):
            _publish(writer, observed_at_ns=1_001, monotonic_sample_ns=501)
    assert list(mount.iterdir()) == []
    with _writer(held) as writer:
        assert writer.read_bytes(SNAPSHOT_PATH) == old_raw
        assert _validate(writer)["snapshot_sha256"] == old["snapshot_sha256"]
        assert not any(name.endswith(".tmp") for name in writer.list_files())


def test_atomic_snapshot_fault_after_rename_leaves_new_valid_snapshot(tmp_path: Path) -> None:
    root = tmp_path / "root"
    with _writer(root) as writer:
        _publish(writer)
        original = writer.atomic_write

        def write_then_fail(relative: str, payload: bytes, *, create_only: bool = False) -> None:
            original(relative, payload, create_only=create_only)
            if relative == SNAPSHOT_PATH:
                raise OSError("synthetic after rename")

        writer.atomic_write = write_then_fail  # type: ignore[method-assign]
        with pytest.raises(OSError, match="synthetic after rename"):
            _publish(writer, observed_at_ns=1_001, monotonic_sample_ns=501)
        del writer.atomic_write
    with _writer(root) as writer:
        snapshot = _validate(writer)
        assert snapshot["generation"] == 1
        assert snapshot["observed_at_ns"] == 1_001


def test_restart_rejects_snapshot_when_transition_ledger_is_changed(tmp_path: Path) -> None:
    with _writer(tmp_path / "root") as writer:
        _publish(writer, status="stopped", reason="provider_error", stop_latched=True)
        event_path = f"{EVENT_DIR}/000000.json"
        original = writer.read_bytes(event_path)
        writer.atomic_write(event_path, original.replace(b"provider_error", b"clock_error"))
        with pytest.raises(MonitorError):
            _validate(writer)


def test_transition_event_cap_fails_closed_without_replacing_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import brain_senses_gecko_lane_monitor_v2 as monitor

    with _writer(tmp_path / "root") as writer:
        old = _publish(writer, status="stopped", reason="provider_error", stop_latched=True)
        old_raw = writer.read_bytes(SNAPSHOT_PATH)
        monkeypatch.setattr(monitor, "MAX_EVENTS", 1)
        with pytest.raises(MonitorError, match="event_cap"):
            _publish(
                writer,
                observed_at_ns=1_001,
                monotonic_sample_ns=501,
                status="healthy",
                reason="none",
                stop_latched=False,
            )
        assert writer.read_bytes(SNAPSHOT_PATH) == old_raw
        assert _validate(writer)["snapshot_sha256"] == old["snapshot_sha256"]


def test_snapshot_and_event_files_are_private_and_bounded(tmp_path: Path) -> None:
    with _writer(tmp_path / "root") as writer:
        _publish(writer, status="degraded", reason="capacity_error")
        for path in writer.list_files():
            metadata = writer.stat(path)
            assert stat.S_IMODE(metadata.st_mode) == 0o600
            assert metadata.st_size <= (MAX_EVENTS * 2048 if path.startswith(EVENT_DIR) else 8192)
