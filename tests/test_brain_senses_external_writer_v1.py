from __future__ import annotations

import os
from pathlib import Path

import pytest

from memecoin_ai.sources import Capture
from memecoin_research_storage import StorageError
from tools.brain_senses_external_writer_v1 import (
    DescriptorWriter,
    canonical_json,
    semantic_request_sha256,
    wire_request_sha256,
    write_capture,
)


def _root(tmp_path: Path) -> tuple[Path, Path, int]:
    mount = tmp_path / "mount"
    root = mount / "data" / "services" / "svc"
    root.mkdir(parents=True, mode=0o700)
    for path in (mount, mount / "data", mount / "data" / "services", root):
        path.chmod(0o700)
    return mount, root, os.open(root, os.O_RDONLY | os.O_DIRECTORY)


@pytest.mark.parametrize(
    "relative", ["", ".", "..", "../x", "/x", "a/../b", "a//b", "a\\b", "a\x00b"]
)
def test_rejects_noncanonical_paths(tmp_path: Path, relative: str) -> None:
    mount, _, root_fd = _root(tmp_path)
    try:
        with DescriptorWriter(root_fd) as writer:
            with pytest.raises(StorageError, match="external_writer_"):
                writer.read_bytes(relative)
    finally:
        os.close(root_fd)


def test_writer_rejects_symlink_directory_and_file(tmp_path: Path) -> None:
    mount, root, root_fd = _root(tmp_path)
    (root / "target").write_text("sentinel")
    (root / "target").chmod(0o600)
    (root / "linked").symlink_to("target")
    (root / "linked-dir").symlink_to(root, target_is_directory=True)
    try:
        with DescriptorWriter(root_fd) as writer:
            with pytest.raises(StorageError, match="symlink_rejected"):
                writer.read_bytes("linked")
            with pytest.raises(StorageError, match="symlink_rejected"):
                writer.create("linked-dir/file", b"bad")
            with pytest.raises(StorageError, match="symlink_rejected"):
                writer.atomic_write("linked", b"bad")
    finally:
        os.close(root_fd)


@pytest.mark.parametrize("operation", ["mkdir", "create", "append", "atomic_rename", "lock_open"])
def test_detach_after_revalidation_stays_on_held_volume(tmp_path: Path, operation: str) -> None:
    mount, root, root_fd = _root(tmp_path)
    detached = tmp_path / "detached-volume"
    if operation == "append":
        (root / "ledger.jsonl").write_bytes(b"prior\n")
        (root / "ledger.jsonl").chmod(0o600)
    if operation == "atomic_rename":
        (root / "state.json").write_bytes(b"old")
        (root / "state.json").chmod(0o600)
    detached_once = False

    def detach_after_revalidation(name: str) -> None:
        nonlocal detached_once
        trigger = "append_open" if operation == "append" else operation
        if name != trigger or detached_once:
            return
        detached_once = True
        os.rename(mount, detached)
        mount.mkdir(mode=0o700)
        (mount / "underlying-mountpoint-sentinel").write_text("untouched")
        (mount / "underlying-mountpoint-sentinel").chmod(0o600)

    try:
        with DescriptorWriter(root_fd, fault_hook=detach_after_revalidation) as writer:
            if operation == "mkdir":
                writer.mkdir("created")
            elif operation == "create":
                writer.create("created.json", b"payload")
            elif operation == "append":
                writer.append("ledger.jsonl", b"next\n")
            elif operation == "atomic_rename":
                writer.atomic_write("state.json", b"new")
            elif operation == "lock_open":
                with writer.lock():
                    pass
        assert detached_once
        assert (mount / "underlying-mountpoint-sentinel").read_text() == "untouched"
        assert list(mount.iterdir()) == [mount / "underlying-mountpoint-sentinel"]
        held_root = detached / "data" / "services" / "svc"
        if operation == "mkdir":
            assert (held_root / "created").is_dir()
        elif operation == "create":
            assert (held_root / "created.json").read_bytes() == b"payload"
        elif operation == "append":
            assert (held_root / "ledger.jsonl").read_bytes() == b"prior\nnext\n"
        elif operation == "atomic_rename":
            assert (held_root / "state.json").read_bytes() == b"new"
        elif operation == "lock_open":
            assert (held_root / ".writer.lock").is_file()
    finally:
        os.close(root_fd)


def test_create_only_and_atomic_write_are_durable_and_collision_safe(tmp_path: Path) -> None:
    mount, _, root_fd = _root(tmp_path)
    try:
        with DescriptorWriter(root_fd) as writer:
            writer.atomic_write("nested/state.json", b"first", create_only=True)
            with pytest.raises(StorageError, match="create_only_collision"):
                writer.atomic_write("nested/state.json", b"other", create_only=True)
            writer.atomic_write("nested/state.json", b"second")
            writer.append("nested/events.jsonl", b"one\n")
            assert writer.read_bytes("nested/state.json") == b"second"
            assert writer.read_bytes("nested/events.jsonl") == b"one\n"
            assert writer.stat("nested/state.json").st_size == 6
            assert writer.list_files() == ["nested/events.jsonl", "nested/state.json"]
    finally:
        os.close(root_fd)


def test_atomic_write_stream_publishes_complete_bytes_and_cleans_partial_failure(
    tmp_path: Path,
) -> None:
    _, root, root_fd = _root(tmp_path)
    try:
        with DescriptorWriter(root_fd) as writer:
            written, digest = writer.atomic_write_stream(
                "stream/payload.bin", iter((b"first", b"-second")), max_bytes=32
            )
            assert written == 12
            assert digest == "79be082f29fd48f6922ef9e7c161190ba1e076790e82e9fa490b58205b5e9a44"
            assert writer.read_bytes("stream/payload.bin") == b"first-second"

            callbacks = 0

            def fail_midstream() -> None:
                nonlocal callbacks
                callbacks += 1
                if callbacks == 2:
                    raise RuntimeError("source view changed")

            with pytest.raises(RuntimeError, match="source view changed"):
                writer.atomic_write_stream(
                    "stream/incomplete.bin",
                    iter((b"prefix", b"suffix")),
                    max_bytes=32,
                    before_chunk=fail_midstream,
                )
            with pytest.raises(StorageError, match="stream_file_too_large"):
                writer.atomic_write_stream(
                    "stream/oversized.bin",
                    iter((b"012345", b"6789")),
                    max_bytes=8,
                )
            assert not (root / "stream" / "incomplete.bin").exists()
            assert not (root / "stream" / "oversized.bin").exists()
            assert sorted(path.name for path in (root / "stream").iterdir()) == ["payload.bin"]
    finally:
        os.close(root_fd)


def test_capture_writer_persists_payload_and_receipt_with_separate_times(tmp_path: Path) -> None:
    _, _, root_fd = _root(tmp_path)
    capture = Capture(
        source_alias="helius-free",
        method="getSlot",
        request_json=canonical_json({"jsonrpc": "2.0", "id": 1, "method": "getSlot", "params": []})
        .decode()
        .strip(),
        request_started_ns=10,
        received_ns=12,
        response=b'{"jsonrpc":"2.0","id":1,"result":123}',
    )
    try:
        with DescriptorWriter(root_fd) as writer:
            receipt = write_capture(
                writer,
                capture=capture,
                provider="helius-free",
                request_id="20261011T120000Z-00",
                method="getSlot",
                params=[],
                request_sha256=semantic_request_sha256("getSlot", []),
                wire_request_sha256=wire_request_sha256("getSlot", []),
                decision_ns=9,
                relative_directory="captures/00",
                storage_codec="raw",
            )
            assert receipt["received_ns"] == 12
            assert receipt["availability_ns"] is None
            assert receipt["decision_ns"] == 9
            assert receipt["response_bytes"] == len(capture.response)
            assert writer.read_bytes("captures/00/response.json") == capture.response
            assert b'"availability_ns":null' in writer.read_bytes("captures/00/receipt.json")
    finally:
        os.close(root_fd)
