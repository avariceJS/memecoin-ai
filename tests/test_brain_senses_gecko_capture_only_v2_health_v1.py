from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json
from tools.brain_senses_gecko_capture_only_v2_health_v1 import (
    ROOT_PREFIX,
    HealthError,
    initialize_snapshot,
    publish_snapshot,
    read_snapshot,
)

RELEASE_ID = "a" * 32


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fixture(tmp_path: Path) -> tuple[DescriptorWriter, dict[str, Any], dict[str, Any]]:
    path = tmp_path / "data"
    path.mkdir(mode=0o700)
    root_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd)
    os.close(root_fd)
    st = os.fstat(writer.root_fd)
    storage = {
        "root_id": "b" * 32,
        "volume_uuid": "12345678-1234-1234-1234-123456789abc",
        "device": st.st_dev,
        "inode": st.st_ino,
    }
    fields: dict[str, Any] = {
        "release_sha256": _hash(b"release"),
        "source_sha256": _hash(b"source"),
        "binding_sha256": _hash(b"binding"),
        **storage,
        "observed_ns": 1_900_000_000_000_000_000,
        "monotonic_ns": 1_000_000_000,
        "status": "paused",
        "reason": "outside_window",
        "stop_latched": False,
        "request_prefix_sha256": _hash(b"request-prefix"),
        "coordinator_policy_sha256": _hash(b"coordinator-policy"),
        "coordinator_sequence": 0,
        "coordinator_tail_sha256": _hash(b"coordinator-tail"),
        "cutover_prefix_sha256": _hash(b"cutover"),
        "cutover_last_dispatch_ns": 1_899_999_000_000_000_000,
        "last_dispatch_ns": None,
        "bucket_close_ns": None,
        "plan_sequence": None,
        "plan_record_sha256": _hash(b"plan"),
        "cursor": None,
        "selected_count": 0,
        "intent_count": 0,
        "received_count": 0,
        "durable_count": 0,
        "eligible_new_open_count": 0,
        "missing_count": 0,
        "daily_request_count": 0,
        "daily_response_bytes": 0,
        "rolling_request_count": 0,
        "rolling_response_bytes": 0,
        "allocated_bytes": 0,
        "free_bytes": 200 * 1024**3,
        "cooldown_until_ns": None,
        "last_progress_ns": 1_900_000_000_000_000_000,
        "last_error_code": None,
    }
    return writer, storage, fields


def _update(
    prior: dict[str, Any], *, status: str, reason: str, stop_latched: bool
) -> dict[str, Any]:
    fields = {
        key: value
        for key, value in prior.items()
        if key
        not in {
            "schema",
            "schema_version",
            "release_id",
            "generation",
            "transition_count",
            "transition_tail_sha256",
            "snapshot_sha256",
        }
    }
    fields.update(
        observed_ns=prior["observed_ns"] + 1,
        monotonic_ns=prior["monotonic_ns"] + 1,
        status=status,
        reason=reason,
        stop_latched=stop_latched,
    )
    return fields


def test_health_snapshot_is_bounded_and_transitioned_atomically(tmp_path: Path) -> None:
    writer, storage, fields = _fixture(tmp_path)
    try:
        initial = initialize_snapshot(writer, release_id=RELEASE_ID, storage=storage, fields=fields)
        assert initial["status"] == "paused"
        healthy = publish_snapshot(
            writer,
            release_id=RELEASE_ID,
            storage=storage,
            fields=_update(initial, status="healthy", reason="resumed", stop_latched=False),
        )
        assert healthy["generation"] == 1
        assert healthy["transition_count"] == 1
        assert read_snapshot(writer, release_id=RELEASE_ID, storage=storage) == healthy
        stopped = publish_snapshot(
            writer,
            release_id=RELEASE_ID,
            storage=storage,
            fields=_update(healthy, status="stopped", reason="integrity_error", stop_latched=True),
        )
        assert stopped["transition_count"] == 2
        assert stopped["stop_latched"] is True
        assert read_snapshot(writer, release_id=RELEASE_ID, storage=storage) == stopped
    finally:
        writer.close()


def test_health_snapshot_detects_mutated_transition_and_binding(tmp_path: Path) -> None:
    writer, storage, fields = _fixture(tmp_path)
    try:
        snapshot = initialize_snapshot(
            writer, release_id=RELEASE_ID, storage=storage, fields=fields
        )
        # A wrong Kingston identity cannot accept a validly hashed local snapshot.
        wrong_storage = dict(storage, root_id="c" * 32)
        with pytest.raises(HealthError, match="health_snapshot_invalid"):
            read_snapshot(writer, release_id=RELEASE_ID, storage=wrong_storage)
        healthy = publish_snapshot(
            writer,
            release_id=RELEASE_ID,
            storage=storage,
            fields=_update(snapshot, status="healthy", reason="resumed", stop_latched=False),
        )
        event_path = f"{ROOT_PREFIX}/{RELEASE_ID}/events/000000.json"
        event = json.loads(writer.read_bytes(event_path))
        event["reason"] = "outside_window"
        writer.atomic_write(event_path, canonical_json(event), create_only=False)
        with pytest.raises(HealthError, match="health_event_invalid"):
            read_snapshot(writer, release_id=RELEASE_ID, storage=storage)
        assert healthy["transition_count"] == 1
    finally:
        writer.close()


def test_forced_detach_during_health_snapshot_replace_never_writes_underlay(
    tmp_path: Path,
) -> None:
    mount = tmp_path / "mounted"
    detached = tmp_path / "detached"
    mount.mkdir(mode=0o700)
    root_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd)
    os.close(root_fd)
    st = os.fstat(writer.root_fd)
    storage = {
        "root_id": "d" * 32,
        "volume_uuid": "12345678-1234-1234-1234-123456789abc",
        "device": st.st_dev,
        "inode": st.st_ino,
    }
    fields: dict[str, Any] = {
        "release_sha256": _hash(b"release"),
        "source_sha256": _hash(b"source"),
        "binding_sha256": _hash(b"binding"),
        **storage,
        "observed_ns": 1_900_000_000_000_000_000,
        "monotonic_ns": 1_000_000_000,
        "request_prefix_sha256": _hash(b"request-prefix"),
        "coordinator_policy_sha256": _hash(b"coordinator-policy"),
        "coordinator_sequence": 0,
        "coordinator_tail_sha256": _hash(b"coordinator-tail"),
        "cutover_prefix_sha256": _hash(b"cutover"),
        "cutover_last_dispatch_ns": None,
        "last_dispatch_ns": None,
        "bucket_close_ns": None,
        "plan_sequence": None,
        "plan_record_sha256": _hash(b"plan"),
        "cursor": None,
        "selected_count": 0,
        "intent_count": 0,
        "received_count": 0,
        "durable_count": 0,
        "eligible_new_open_count": 0,
        "missing_count": 0,
        "daily_request_count": 0,
        "daily_response_bytes": 0,
        "rolling_request_count": 0,
        "rolling_response_bytes": 0,
        "allocated_bytes": 0,
        "free_bytes": 200 * 1024**3,
        "cooldown_until_ns": None,
        "last_progress_ns": 1_900_000_000_000_000_000,
        "last_error_code": None,
    }
    try:
        initial = initialize_snapshot(writer, release_id=RELEASE_ID, storage=storage, fields=fields)
        detached_once = False

        def detach(operation: str) -> None:
            nonlocal detached_once
            if operation == "atomic_rename" and not detached_once:
                detached_once = True
                os.rename(mount, detached)
                mount.mkdir(mode=0o700)
                (mount / "underlay-sentinel").write_text("preserve")

        writer._fault_hook = detach
        updated = _update(
            initial,
            status="paused",
            reason="outside_window",
            stop_latched=False,
        )
        publish_snapshot(
            writer,
            release_id=RELEASE_ID,
            storage=storage,
            fields=updated,
        )
        assert detached_once
        assert (mount / "underlay-sentinel").read_text() == "preserve"
        assert (
            detached / "services/gecko-market-v1/host-health-v1" / RELEASE_ID / "snapshot.json"
        ).is_file()
        assert not (mount / "services").exists()
    finally:
        writer.close()
