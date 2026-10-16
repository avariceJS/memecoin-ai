from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

import memecoin_research_storage as storage
from memecoin_research_storage import StorageConfig, VolumeInfo, create_root, writer_guard
from tools.brain_host_capture_writer_v1 import CaptureWriterError, HeldKingstonCapture

VOLUME_UUID = "11111111-2222-3333-4444-555555555555"
ROOT_ID = "a" * 32


class Probe:
    def __init__(self, mount: Path) -> None:
        self.mount = mount

    def inspect(self, mount_path: Path) -> VolumeInfo:
        assert mount_path == self.mount
        return VolumeInfo(
            mount_path=self.mount,
            volume_uuid=VOLUME_UUID,
            filesystem="Case-sensitive APFS",
            filesystem_type="apfs",
            owners_enabled=True,
            read_only=False,
            available_bytes=10**12,
            is_mountpoint=True,
            mount_options=("apfs", "local", "journaled"),
        )


def _guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mount = tmp_path / "kingston"
    mount.mkdir(mode=0o700)
    config_path = tmp_path / "private" / "storage.json"
    config_path.parent.mkdir(mode=0o700)
    config = StorageConfig(ROOT_ID, VOLUME_UUID, mount, "BrainData", 100)
    config_path.write_text(json.dumps(config.to_json()), encoding="utf-8")
    config_path.chmod(0o600)
    probe = Probe(mount)
    create_root(config_path, probe=probe)
    monkeypatch.setattr(storage.os.path, "ismount", lambda path: Path(path) == mount)
    return config_path, mount, probe


def test_stream_objects_readback_and_complete_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_path, _mount, probe = _guard(tmp_path, monkeypatch)
    body = b"descriptor-bound-kingston-payload" * 100_000
    body_hash = hashlib.sha256(body).hexdigest()
    manifest = b'{"schema":"fixture-manifest-v1"}\n'
    marker = b'{"schema":"fixture-complete-v1"}\n'
    with writer_guard(config_path, probe=probe) as guard:
        with HeldKingstonCapture(
            guard,
            "1" * 32,
            source_volume_uuid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            source_device=os.fstat(guard.mount_fd).st_dev + 1,
            min_free_bytes=100,
        ) as capture:
            with capture.create_object("payload.bin") as sink:
                for offset in range(0, len(body), 97_001):
                    sink.write(body[offset : offset + 97_001])
                result = sink.finalize(expected_size=len(body), expected_sha256=body_hash)
            assert result.size_bytes == len(body)
            with capture.create_object("manifest.json") as sink:
                sink.write(manifest)
                manifest_result = sink.finalize(
                    expected_size=len(manifest),
                    expected_sha256=hashlib.sha256(manifest).hexdigest(),
                )
            assert manifest_result.name == "manifest.json"
            with pytest.raises(CaptureWriterError, match="manifest_must_be_last"):
                capture.create_object("late-payload.bin")
            complete = capture.publish_complete(marker)
            assert complete.name == "COMPLETE"
            assert capture.read_verified_object("COMPLETE", max_bytes=1024) == marker
            assert capture._complete is True
            with pytest.raises(CaptureWriterError, match="capture_already_complete"):
                capture.create_object("after-complete.bin")

    path = _mount / "BrainData" / "host-recovery" / "data-view-captures" / ("1" * 32)
    assert (path / "payload.bin").read_bytes() == body
    assert (path / "COMPLETE").read_bytes() == marker
    for member in path.iterdir():
        assert stat.S_IMODE(member.stat().st_mode) == 0o600


def test_partial_stream_stays_unpublished_and_blocks_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_path, _mount, probe = _guard(tmp_path, monkeypatch)
    with writer_guard(config_path, probe=probe) as guard:
        with HeldKingstonCapture(
            guard,
            "2" * 32,
            source_volume_uuid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            source_device=os.fstat(guard.mount_fd).st_dev + 1,
            min_free_bytes=100,
        ) as capture:
            sink = capture.create_object("interrupted.bin")
            sink.write(b"prefix")
            sink._close_unfinished()
            capture._active = None
            with pytest.raises(CaptureWriterError, match="manifest_must_be_verified"):
                capture.publish_complete(b"not accepted")
    path = _mount / "BrainData" / "host-recovery" / "data-view-captures" / ("2" * 32)
    assert (path / "interrupted.bin").read_bytes() == b"prefix"
    assert not (path / "COMPLETE").exists()


def test_collision_symlink_and_same_volume_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_path, mount, probe = _guard(tmp_path, monkeypatch)
    capture_path = mount / "BrainData" / "host-recovery" / "data-view-captures" / ("3" * 32)
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    (capture_path.parent.parent).chmod(0o700)
    capture_path.parent.chmod(0o700)
    capture_path.mkdir(mode=0o700)
    with writer_guard(config_path, probe=probe) as guard:
        with pytest.raises(CaptureWriterError, match="capture_directory_collision"):
            HeldKingstonCapture(
                guard,
                "3" * 32,
                source_volume_uuid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                source_device=os.fstat(guard.mount_fd).st_dev + 1,
                min_free_bytes=100,
            )
        with pytest.raises(CaptureWriterError, match="source_and_destination_volume_same"):
            HeldKingstonCapture(
                guard,
                "4" * 32,
                source_volume_uuid=VOLUME_UUID,
                source_device=os.fstat(guard.mount_fd).st_dev + 1,
                min_free_bytes=100,
            )

    # A symlink at the reserved leaf is not followed and remains untouched.
    capture_path.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    capture_path.symlink_to(outside, target_is_directory=True)
    with writer_guard(config_path, probe=probe) as guard:
        with pytest.raises(CaptureWriterError, match="capture_directory_collision"):
            HeldKingstonCapture(
                guard,
                "3" * 32,
                source_volume_uuid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                source_device=os.fstat(guard.mount_fd).st_dev + 1,
                min_free_bytes=100,
            )
    assert list(outside.iterdir()) == []


def test_reverifies_every_member_before_complete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_path, mount, probe = _guard(tmp_path, monkeypatch)
    with writer_guard(config_path, probe=probe) as guard:
        with HeldKingstonCapture(
            guard,
            "5" * 32,
            source_volume_uuid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            source_device=os.fstat(guard.mount_fd).st_dev + 1,
            min_free_bytes=100,
        ) as capture:
            payload = b"bound manifest"
            with capture.create_object("manifest.json") as sink:
                sink.write(payload)
                sink.finalize(
                    expected_size=len(payload), expected_sha256=hashlib.sha256(payload).hexdigest()
                )
            target = mount / "BrainData" / "host-recovery" / "data-view-captures" / ("5" * 32)
            (target / "manifest.json").write_bytes(b"tampered")
            with pytest.raises(CaptureWriterError, match="object_readback_mismatch"):
                capture.publish_complete(b"{}\n")
            assert not (target / "COMPLETE").exists()


def test_stream_handles_eintr_and_short_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_path, mount, probe = _guard(tmp_path, monkeypatch)
    payload = b"bounded-stream" * 100
    with writer_guard(config_path, probe=probe) as guard:
        with HeldKingstonCapture(
            guard,
            "6" * 32,
            source_volume_uuid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            source_device=os.fstat(guard.mount_fd).st_dev + 1,
            min_free_bytes=100,
        ) as capture:
            with capture.create_object("payload.bin") as sink:
                real_write = os.write
                first = True

                def short_write(fd: int, chunk: bytes | memoryview) -> int:
                    nonlocal first
                    if fd == sink.fd:
                        if first:
                            first = False
                            raise InterruptedError
                        return real_write(fd, bytes(chunk[:3]))
                    return real_write(fd, chunk)

                monkeypatch.setattr(os, "write", short_write)
                sink.write(payload)
                result = sink.finalize(
                    expected_size=len(payload), expected_sha256=hashlib.sha256(payload).hexdigest()
                )
                assert result.size_bytes == len(payload)
                monkeypatch.setattr(os, "write", real_write)
    assert (
        mount / "BrainData" / "host-recovery" / "data-view-captures" / ("6" * 32) / "payload.bin"
    ).read_bytes() == payload


def test_enospc_keeps_only_unpublished_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_path, mount, probe = _guard(tmp_path, monkeypatch)
    with writer_guard(config_path, probe=probe) as guard:
        with HeldKingstonCapture(
            guard,
            "7" * 32,
            source_volume_uuid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            source_device=os.fstat(guard.mount_fd).st_dev + 1,
            min_free_bytes=100,
        ) as capture:
            with capture.create_object("partial.bin") as sink:
                real_write = os.write

                def full_disk(fd: int, chunk: bytes | memoryview) -> int:
                    if fd == sink.fd:
                        raise OSError(errno.ENOSPC, "fixture disk full")
                    return real_write(fd, chunk)

                monkeypatch.setattr(os, "write", full_disk)
                with pytest.raises(CaptureWriterError, match="destination_write_failed"):
                    sink.write(b"will not fit")
                monkeypatch.setattr(os, "write", real_write)
    path = mount / "BrainData" / "host-recovery" / "data-view-captures" / ("7" * 32)
    assert (path / "partial.bin").read_bytes() == b""
    assert not (path / "COMPLETE").exists()


def test_capture_parent_replacement_stops_stream_before_next_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config_path, mount, probe = _guard(tmp_path, monkeypatch)
    capture_path = mount / "BrainData" / "host-recovery" / "data-view-captures" / ("8" * 32)
    moved_path = capture_path.with_name("8" * 32 + ".moved")
    with writer_guard(config_path, probe=probe) as guard:
        with HeldKingstonCapture(
            guard,
            "8" * 32,
            source_volume_uuid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            source_device=os.fstat(guard.mount_fd).st_dev + 1,
            min_free_bytes=100,
        ) as capture:
            sink = capture.create_object("payload.bin")
            sink.write(b"first")
            capture_path.rename(moved_path)
            capture_path.mkdir(mode=0o700)
            with pytest.raises(CaptureWriterError, match="capture_directory_path_replaced"):
                sink.write(b"second")
            sink.__exit__(None, None, None)
    assert (moved_path / "payload.bin").read_bytes() == b"first"
    assert not (capture_path / "COMPLETE").exists()
