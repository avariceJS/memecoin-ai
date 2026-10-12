#!/usr/bin/env python3
"""Bounded no-provider Kingston capacity and prefix-recovery qualification.

The 3,104,000,000-byte write is intentionally gated behind ``--execute``.
``--preflight-only`` performs no writes. The execution path creates only a
unique scratch subtree beneath the configured descriptor-bound research root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import stat
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from memecoin_research_storage import (  # noqa: E402
    MacOSVolumeProbe,
    StorageConfig,
    StorageError,
    resolve,
    writer_guard,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402

CONFIG_PATH = Path.home() / ".config/brain-research/storage-v1.json"
EXPECTED_ROOT_ID = "9428695af6d4f46c08e1a444cdc66de8"
EXPECTED_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"
CONTRACT = "research_kingston_capacity_recovery_qualify_v1"
BYTE_LIMIT = 32_000_000
BOOTSTRAP_BYTES = BYTE_LIMIT
FRAME_COUNT = 12
FRAME_BYTES = 256_000_000
FILES_PER_FRAME = 8
MAX_RESPONSE_FILES = 409
MAX_TEMP_PEAK_BYTES = 290_000_000
CONFIGURED_FLOOR_BYTES = 50 * 1024**3
PAYLOAD_BYTES = BOOTSTRAP_BYTES + FRAME_COUNT * FRAME_BYTES
INITIAL_FREE_MINIMUM = CONFIGURED_FLOOR_BYTES + PAYLOAD_BYTES + MAX_TEMP_PEAK_BYTES
RECOVERY_BYTES = 1_000_000


def allocation_bytes(st: os.stat_result) -> int:
    """Return reported physical allocation, not logical file size."""
    blocks = getattr(st, "st_blocks", None)
    if not isinstance(blocks, int) or blocks < 0:
        raise RuntimeError("allocated_blocks_unavailable")
    return blocks * 512


def plan_shape() -> dict[str, int]:
    response_files = 1 + FRAME_COUNT * FILES_PER_FRAME
    if (
        BOOTSTRAP_BYTES > BYTE_LIMIT
        or FRAME_BYTES > BYTE_LIMIT * FILES_PER_FRAME
        or response_files > MAX_RESPONSE_FILES
        or PAYLOAD_BYTES != 3_104_000_000
    ):
        raise RuntimeError("capacity_plan_bounds_invalid")
    return {
        "bootstrap_bytes": BOOTSTRAP_BYTES,
        "frames": FRAME_COUNT,
        "frame_bytes_each": FRAME_BYTES,
        "files_per_frame": FILES_PER_FRAME,
        "max_bytes_per_file": BYTE_LIMIT,
        "response_files": response_files,
        "maximum_response_files": MAX_RESPONSE_FILES,
        "payload_bytes": PAYLOAD_BYTES,
        "max_temp_peak_bytes": MAX_TEMP_PEAK_BYTES,
        "configured_floor_bytes": CONFIGURED_FLOOR_BYTES,
        "initial_free_minimum_bytes": INITIAL_FREE_MINIMUM,
    }


def classify_recovery(
    *, expected_bytes: int, observed_prefix_bytes: int | None, intent_trusted: bool
) -> str:
    """Read-only synthetic recovery: uncertain intent never grants replay."""
    if expected_bytes <= 0 or observed_prefix_bytes is None or observed_prefix_bytes < 0:
        return "unknown_no_replay"
    if observed_prefix_bytes > expected_bytes:
        return "unknown_no_replay"
    if intent_trusted and observed_prefix_bytes > 0:
        return "possibly_spent_no_replay"
    if intent_trusted and observed_prefix_bytes == 0:
        return "unknown_no_replay"
    return "unknown_no_replay"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_hashes() -> dict[str, str]:
    paths = {
        "qualification_harness": Path(__file__).resolve(),
        "descriptor_writer": REPO / "tools/brain_senses_external_writer_v1.py",
        "storage_library": REPO / "src/memecoin_research_storage/__init__.py",
    }
    return {name: _sha256(path.read_bytes()) for name, path in paths.items()}


def _root_listing(
    writer: DescriptorWriter,
) -> tuple[tuple[str, str, int, int, int, int], ...]:
    """Snapshot immediate underlay entries without descending into user data."""
    values: list[tuple[str, str, int, int, int, int]] = []
    for name in sorted(os.listdir(writer.root_fd)):
        if name in {"", ".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise StorageError("capacity_root_invalid_entry_name")
        info = os.stat(name, dir_fd=writer.root_fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or info.st_dev != writer.device:
            raise StorageError("capacity_root_unsafe_entry")
        if stat.S_ISDIR(info.st_mode):
            if stat.S_IMODE(info.st_mode) != 0o700:
                raise StorageError("capacity_root_unsafe_directory_mode")
            kind = "directory"
        elif stat.S_ISREG(info.st_mode):
            mode = stat.S_IMODE(info.st_mode)
            allowed_mode = mode == 0o600 or (name == ".DS_Store" and mode == 0o644)
            if not allowed_mode or info.st_nlink != 1:
                raise StorageError("capacity_root_unsafe_file")
            kind = "file"
        else:
            raise StorageError("capacity_root_unsupported_entry")
        values.append(
            (name, kind, stat.S_IMODE(info.st_mode), info.st_ino, info.st_size, info.st_mtime_ns)
        )
    return tuple(values)


def _nonce_allocation_inventory(writer: DescriptorWriter, nonce: str) -> dict[str, Any]:
    """Descriptor-walk only the unique scratch subtree and sum physical blocks."""
    files: list[dict[str, Any]] = []
    directories: list[dict[str, Any]] = []

    def visit(directory_fd: int, relative: str) -> None:
        for name in sorted(os.listdir(directory_fd)):
            if name in {"", ".", ".."} or "/" in name or "\\" in name or "\x00" in name:
                raise StorageError("capacity_scratch_invalid_entry_name")
            child_relative = f"{relative}/{name}"
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (
                stat.S_ISLNK(info.st_mode)
                or info.st_dev != writer.device
                or info.st_uid != os.getuid()
            ):
                raise StorageError("capacity_scratch_unsafe_entry")
            if stat.S_ISDIR(info.st_mode):
                if stat.S_IMODE(info.st_mode) != 0o700:
                    raise StorageError("capacity_scratch_unsafe_directory_mode")
                directories.append(
                    {"path": child_relative, "allocated_bytes": allocation_bytes(info)}
                )
                child_fd = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                try:
                    opened = os.fstat(child_fd)
                    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                        raise StorageError("capacity_scratch_entry_changed")
                    visit(child_fd, child_relative)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(info.st_mode):
                if stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                    raise StorageError("capacity_scratch_unsafe_file")
                if allocation_bytes(info) < info.st_size:
                    raise StorageError("capacity_scratch_file_not_fully_allocated")
                files.append(
                    {
                        "path": child_relative,
                        "logical_bytes": info.st_size,
                        "allocated_bytes": allocation_bytes(info),
                    }
                )
            else:
                raise StorageError("capacity_scratch_unsupported_entry")

    root_entry = os.stat(nonce, dir_fd=writer.root_fd, follow_symlinks=False)
    if (
        not stat.S_ISDIR(root_entry.st_mode)
        or root_entry.st_uid != os.getuid()
        or stat.S_IMODE(root_entry.st_mode) != 0o700
        or root_entry.st_dev != writer.device
    ):
        raise StorageError("capacity_scratch_root_unsafe")
    directories.append({"path": nonce, "allocated_bytes": allocation_bytes(root_entry)})
    nonce_fd = os.open(
        nonce,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=writer.root_fd,
    )
    try:
        opened = os.fstat(nonce_fd)
        if (opened.st_dev, opened.st_ino) != (root_entry.st_dev, root_entry.st_ino):
            raise StorageError("capacity_scratch_root_changed")
        visit(nonce_fd, nonce)
    finally:
        os.close(nonce_fd)
    files.sort(key=lambda item: item["path"])
    directories.sort(key=lambda item: item["path"])
    return {"files": files, "directories": directories}


def _payload_bytes(run_nonce: str, relative: str, length: int) -> bytes:
    seed = f"kingston-capacity-v1\0{run_nonce}\0{relative}".encode()
    return hashlib.shake_256(seed).digest(length)


def _preflight(config_path: Path) -> tuple[StorageConfig, Any, dict[str, Any]]:
    if platform.system() != "Darwin":
        raise RuntimeError("macos_required")
    config = StorageConfig.load(config_path)
    probe = MacOSVolumeProbe()
    resolved = resolve(config_path, min_free_bytes=INITIAL_FREE_MINIMUM, probe=probe)
    volume = probe.inspect(config.mount_path)
    _validate_preflight_binding(config, resolved, volume)
    summary = {
        "config_path": str(config_path),
        "config_sha256": _sha256(config_path.read_bytes()),
        "config_mode": oct(stat.S_IMODE(config_path.stat().st_mode)),
        "root_id": config.root_id,
        "volume_uuid": config.volume_uuid.lower(),
        "mount_path": str(config.mount_path),
        "data_root": config.data_root,
        "service_root": str(resolved.path),
        "filesystem": volume.filesystem,
        "owners_enabled": volume.owners_enabled,
        "read_only": volume.read_only,
        "is_mountpoint": volume.is_mountpoint,
        "available_bytes_before": volume.available_bytes,
        "minimum_initial_free_bytes": INITIAL_FREE_MINIMUM,
        "plan": plan_shape(),
    }
    return config, probe, summary


def _validate_preflight_binding(config: Any, resolved: Any, volume: Any) -> None:
    if (
        config.root_id != EXPECTED_ROOT_ID
        or config.volume_uuid.lower() != EXPECTED_UUID
        or config.min_free_bytes != CONFIGURED_FLOOR_BYTES
        or resolved.root_id != EXPECTED_ROOT_ID
        or resolved.volume_uuid.lower() != EXPECTED_UUID
        or volume.volume_uuid.lower() != EXPECTED_UUID
        or volume.filesystem.lower().strip() != "case-sensitive apfs"
        or not volume.owners_enabled
        or volume.read_only
        or not volume.is_mountpoint
        or volume.available_bytes < INITIAL_FREE_MINIMUM
        or resolved.available_bytes < INITIAL_FREE_MINIMUM
    ):
        raise StorageError("kingston_preflight_identity_or_capacity_failed")


def _write_recovery_fixture(
    writer: DescriptorWriter,
    *,
    nonce: str,
    guard: Any,
    guard_free_samples: list[int],
) -> dict[str, Any]:
    directory = f"{nonce}/synthetic-interruption"
    sample_start = len(guard_free_samples)
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        try:

            def child_prewrite() -> None:
                bound = guard.revalidate(
                    min_free_bytes=CONFIGURED_FLOOR_BYTES + MAX_TEMP_PEAK_BYTES
                )
                os.write(write_fd, f"{bound.available_bytes}\n".encode())

            with DescriptorWriter(writer.root_fd, before_write=child_prewrite) as child_writer:
                child_writer.mkdir(directory)
                _write_interrupted_prefix(child_writer, nonce=nonce, directory=directory)
            os._exit(73)
        except BaseException:
            os._exit(74)
    os.close(write_fd)
    _, wait_status = os.waitpid(child_pid, 0)
    child_exit = os.waitstatus_to_exitcode(wait_status)
    guard_output = bytearray()
    while chunk := os.read(read_fd, 4096):
        guard_output.extend(chunk)
    os.close(read_fd)
    if child_exit != 73:
        raise RuntimeError(f"synthetic_interrupt_child_exit:{child_exit}")
    try:
        guard_free_samples.extend(int(line) for line in guard_output.decode().splitlines())
    except ValueError as exc:
        raise RuntimeError("synthetic_interrupt_guard_sample_invalid") from exc
    if len(guard_free_samples) - sample_start < 3:
        raise RuntimeError("synthetic_interrupt_guard_samples_missing")

    # Recovery below is strictly read-only: fresh descriptor reads and no replay.
    intent_path = f"{directory}/synthetic-intent.json"
    partial_path = f"{directory}/partial-prefix.bin"
    read_intent = writer.read_bytes(intent_path, max_bytes=4096)
    observed_prefix = writer.stat(partial_path).st_size
    state = json.loads(read_intent)
    if state.get("synthetic") is not True:
        raise RuntimeError("synthetic_recovery_fixture_not_bound")
    status = classify_recovery(
        expected_bytes=int(state["expected_bytes"]),
        observed_prefix_bytes=observed_prefix,
        intent_trusted=True,
    )
    if status != "possibly_spent_no_replay":
        raise RuntimeError("partial_prefix_recovery_classification_failed")
    readback = writer.read_bytes(partial_path, max_bytes=RECOVERY_BYTES)
    if len(readback) != RECOVERY_BYTES:
        raise RuntimeError("partial_prefix_length_mismatch")
    unknown = classify_recovery(
        expected_bytes=int(state["expected_bytes"]),
        observed_prefix_bytes=None,
        intent_trusted=False,
    )
    return {
        "contract": "synthetic_interrupted_prefix_v1",
        "writer_process_exit_code": child_exit,
        "intent_sha256": _sha256(read_intent),
        "expected_bytes": int(state["expected_bytes"]),
        "observed_prefix_bytes": observed_prefix,
        "prefix_sha256": _sha256(readback),
        "recovery": status,
        "unknown_probe": unknown,
        "recovery_writes": 0,
        "recovery_dispatches": 0,
        "provider_or_credential_calls": 0,
        "prefix_allocated_bytes": allocation_bytes(writer.stat(partial_path)),
        "intent_allocated_bytes": allocation_bytes(writer.stat(intent_path)),
    }


def _write_interrupted_prefix(writer: DescriptorWriter, *, nonce: str, directory: str) -> None:
    intent_path = f"{directory}/synthetic-intent.json"
    marker = (
        json.dumps(
            {
                "schema": "kingston-capacity-synthetic-intent-v1",
                "expected_bytes": RECOVERY_BYTES * 4,
                "synthetic": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    writer.create(intent_path, marker)
    partial_path = f"{directory}/partial-prefix.bin"
    writer.create(partial_path, _payload_bytes(nonce, partial_path, RECOVERY_BYTES))


def _independent_lock_attempt(root_fd: int, lock_path: str) -> int:
    """Return 0 only when a distinct process is excluded by the held lock."""
    child_pid = os.fork()
    if child_pid == 0:
        try:
            with DescriptorWriter(root_fd) as child_writer:
                try:
                    with child_writer.lock(lock_path, blocking=False):
                        os._exit(75)
                except StorageError as exc:
                    os._exit(0 if str(exc) == "external_writer_already_active" else 76)
        except BaseException:
            os._exit(77)
    _, status = os.waitpid(child_pid, 0)
    return os.waitstatus_to_exitcode(status)


def run_qualification(config_path: Path, *, execute: bool) -> dict[str, Any]:
    config, probe, preflight = _preflight(config_path)
    if not execute:
        return {
            "schema_version": 1,
            "contract": CONTRACT,
            "status": "preflight_passed_no_writes",
            "preflight": preflight,
            "provider_requests": 0,
            "credential_access": 0,
            "launchagent_or_lease_interaction": False,
        }

    nonce = f"capacity-qualification-v1-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex}"
    started_ns = time.time_ns()
    started_mono_ns = time.monotonic_ns()
    before_root_listing: tuple[tuple[str, str, int, int, int, int], ...] | None = None
    file_receipts: list[dict[str, Any]] = []
    cleaned = False
    recovery: dict[str, Any] | None = None
    remaining = PAYLOAD_BYTES
    guard_free_samples: list[int] = []
    allocated_high_water = 0
    directory_allocations: list[dict[str, Any]] = []
    with writer_guard(config_path, min_free_bytes=INITIAL_FREE_MINIMUM, probe=probe) as guard:
        if guard.root_id != EXPECTED_ROOT_ID or guard.volume_uuid.lower() != EXPECTED_UUID:
            raise StorageError("guard_binding_mismatch")
        root_fd = _open_child(guard.mount_fd, config.data_root)
        root_stat = os.fstat(root_fd)
        mount_stat = os.fstat(guard.mount_fd)
        if (
            root_stat.st_dev != mount_stat.st_dev
            or root_stat.st_uid != os.getuid()
            or stat.S_IMODE(root_stat.st_mode) != 0o700
        ):
            os.close(root_fd)
            raise StorageError("descriptor_bound_root_identity_failed")
        try:

            def before_write() -> None:
                dynamic_floor = CONFIGURED_FLOOR_BYTES + remaining + MAX_TEMP_PEAK_BYTES
                binding = guard.revalidate(min_free_bytes=dynamic_floor)
                guard_free_samples.append(binding.available_bytes)

            with DescriptorWriter(root_fd, before_write=before_write) as writer:
                before_root_listing = _root_listing(writer)
                created_paths: list[str] = []
                created_directories: list[str] = [nonce]
                writer.mkdir(nonce)
                try:
                    groups: list[tuple[str, int]] = [("bootstrap", 1)]
                    groups.extend(
                        (f"frame-{frame:02d}", FILES_PER_FRAME)
                        for frame in range(1, FRAME_COUNT + 1)
                    )
                    for group, count in groups:
                        writer.mkdir(f"{nonce}/{group}")
                        created_directories.append(f"{nonce}/{group}")
                        for ordinal in range(count):
                            relative = f"{nonce}/{group}/response-{ordinal:03d}.bin"
                            chunk = _payload_bytes(nonce, relative, BYTE_LIMIT)
                            writer.create(relative, chunk)
                            created_paths.append(relative)
                            remaining -= len(chunk)
                            if remaining < 0:
                                raise RuntimeError("capacity_payload_exceeded")
                            guard_free_samples.append(
                                guard.revalidate(
                                    min_free_bytes=(
                                        CONFIGURED_FLOOR_BYTES + remaining + MAX_TEMP_PEAK_BYTES
                                    )
                                ).available_bytes
                            )
                            metadata = writer.stat(relative)
                            readback = writer.read_bytes(relative, max_bytes=BYTE_LIMIT)
                            expected_hash = _sha256(chunk)
                            readback_hash = _sha256(readback)
                            if (
                                len(readback) != BYTE_LIMIT
                                or expected_hash != readback_hash
                                or metadata.st_size != BYTE_LIMIT
                            ):
                                raise RuntimeError("capacity_file_readback_mismatch")
                            allocated = allocation_bytes(metadata)
                            if allocated < metadata.st_size:
                                raise RuntimeError("capacity_file_not_physically_allocated")
                            file_receipts.append(
                                {
                                    "path": relative.removeprefix(f"{nonce}/"),
                                    "logical_bytes": metadata.st_size,
                                    "allocated_bytes": allocated,
                                    "expected_sha256": expected_hash,
                                    "readback_sha256": readback_hash,
                                }
                            )
                    if remaining != 0 or len(file_receipts) != 97:
                        raise RuntimeError("capacity_payload_shape_mismatch")
                    recovery = _write_recovery_fixture(
                        writer,
                        nonce=nonce,
                        guard=guard,
                        guard_free_samples=guard_free_samples,
                    )
                    created_directories.append(f"{nonce}/synthetic-interruption")
                    created_paths.extend(
                        (
                            f"{nonce}/synthetic-interruption/synthetic-intent.json",
                            f"{nonce}/synthetic-interruption/partial-prefix.bin",
                        )
                    )
                    # Verify lock exclusion and create-only collision on this nonce only.
                    collision_path = f"{nonce}/bootstrap/response-000.bin"
                    collision = ""
                    try:
                        writer.create(collision_path, b"must-not-replace")
                    except FileExistsError:
                        collision = "create_only_existing_file_preserved"
                    if collision != "create_only_existing_file_preserved":
                        raise RuntimeError("create_only_collision_not_rejected")
                    lock_path = f"{nonce}/qualification.lock"
                    with writer.lock(lock_path):
                        created_paths.append(lock_path)
                        writer.create(f"{nonce}/lock-held", b"lock-test")
                        created_paths.append(f"{nonce}/lock-held")
                        writer.unlink(f"{nonce}/lock-held")
                        created_paths.remove(f"{nonce}/lock-held")
                        lock_exit = _independent_lock_attempt(writer.root_fd, lock_path)
                        if lock_exit != 0:
                            raise RuntimeError("writer_lock_collision_not_rejected")
                    scratch_inventory = _nonce_allocation_inventory(writer, nonce)
                    inventory_files = scratch_inventory["files"]
                    inventory_dirs = scratch_inventory["directories"]
                    directory_allocations = inventory_dirs
                    expected_files = {f"{nonce}/{item['path']}" for item in file_receipts} | {
                        f"{nonce}/synthetic-interruption/synthetic-intent.json",
                        f"{nonce}/synthetic-interruption/partial-prefix.bin",
                        lock_path,
                    }
                    if {item["path"] for item in inventory_files} != expected_files:
                        raise RuntimeError("capacity_scratch_inventory_file_set_mismatch")
                    expected_directories = {nonce, f"{nonce}/synthetic-interruption"}
                    expected_directories.update(f"{nonce}/{group}" for group, _ in groups)
                    if {item["path"] for item in inventory_dirs} != expected_directories:
                        raise RuntimeError("capacity_scratch_inventory_directory_set_mismatch")
                    allocated_high_water = sum(
                        int(item["allocated_bytes"]) for item in inventory_files
                    ) + sum(int(item["allocated_bytes"]) for item in inventory_dirs)
                    if (
                        allocated_high_water < PAYLOAD_BYTES
                        or allocated_high_water > PAYLOAD_BYTES + MAX_TEMP_PEAK_BYTES
                    ):
                        raise RuntimeError("capacity_physical_allocation_out_of_bounds")
                    for relative in reversed(created_paths):
                        writer.unlink(relative)
                    # Remove only the nonce subtree, deepest directories first.
                    for directory in reversed(created_directories[1:]):
                        guard.revalidate(
                            min_free_bytes=CONFIGURED_FLOOR_BYTES + MAX_TEMP_PEAK_BYTES
                        )
                        parent_fd = _open_child(writer.root_fd, directory.rsplit("/", 1)[0])
                        try:
                            os.rmdir(directory.rsplit("/", 1)[-1], dir_fd=parent_fd)
                            os.fsync(parent_fd)
                        finally:
                            os.close(parent_fd)
                    guard.revalidate(min_free_bytes=CONFIGURED_FLOOR_BYTES + MAX_TEMP_PEAK_BYTES)
                    os.rmdir(nonce, dir_fd=writer.root_fd)
                    os.fsync(writer.root_fd)
                    cleaned = True
                    after_root_listing = _root_listing(writer)
                    expected_listing = tuple(
                        item for item in before_root_listing if item[0] != nonce
                    )
                    if after_root_listing != expected_listing:
                        raise RuntimeError("service_root_underlay_listing_changed")
                except BaseException:
                    # Leave a failed run's nonce visible for audit; do not broaden cleanup.
                    raise
        finally:
            os.close(root_fd)
    if not cleaned or recovery is None:
        raise RuntimeError("capacity_cleanup_or_recovery_incomplete")
    with writer_guard(
        config_path, min_free_bytes=CONFIGURED_FLOOR_BYTES, probe=probe
    ) as post_guard:
        post_root_fd = _open_child(post_guard.mount_fd, config.data_root)
        try:
            with DescriptorWriter(post_root_fd) as post_writer:
                nonce_absent = not post_writer.exists(nonce)
        finally:
            os.close(post_root_fd)
        resolved_after = post_guard.revalidate(min_free_bytes=CONFIGURED_FLOOR_BYTES)
    if (resolved_after.root_id, resolved_after.volume_uuid.lower()) != (
        EXPECTED_ROOT_ID,
        EXPECTED_UUID,
    ) or not nonce_absent:
        raise RuntimeError("post_cleanup_binding_or_nonce_check_failed")
    elapsed = time.monotonic_ns() - started_mono_ns
    return {
        "schema_version": 1,
        "contract": CONTRACT,
        "status": "passed",
        "scope": "single nonce scratch subtree under configured Kingston service root",
        "synthetic_only": True,
        "provider_requests": 0,
        "credential_access": 0,
        "provider_client_imports": 0,
        "launchagent_or_lease_interaction": False,
        "preflight": preflight,
        "provenance": {
            "started_utc": datetime.fromtimestamp(started_ns / 1e9, UTC).isoformat(),
            "completed_utc": datetime.now(UTC).isoformat(),
            "elapsed_monotonic_ns": elapsed,
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "command_contract": CONTRACT,
            "source_sha256": _source_hashes(),
            "minimum_prewrite_available_bytes": min(guard_free_samples),
            "prewrite_guard_samples": len(guard_free_samples),
            "end_free_bytes": resolved_after.available_bytes,
        },
        "scratch": {
            "nonce_subtree": nonce,
            "logical_payload_bytes": sum(item["logical_bytes"] for item in file_receipts),
            "allocated_payload_bytes": sum(item["allocated_bytes"] for item in file_receipts),
            "allocated_high_water_bytes": allocated_high_water,
            "directory_allocations": directory_allocations,
            "physical_allocation_cap_bytes": PAYLOAD_BYTES + MAX_TEMP_PEAK_BYTES,
            "maximum_temp_peak_bytes": MAX_TEMP_PEAK_BYTES,
            "files": file_receipts,
            "recovery": recovery,
            "cleanup": "nonce subtree removed via held root descriptor; verified absent",
            "nonce_absent_after": nonce_absent,
            "underlay_listing_unchanged": True,
            "lock_collision": "external_writer_already_active",
            "create_only_collision": "create_only_existing_file_preserved",
        },
        "post_cleanup": {
            "root_id": resolved_after.root_id,
            "volume_uuid": resolved_after.volume_uuid,
            "available_bytes": resolved_after.available_bytes,
            "minimum_free_floor_preserved": (
                resolved_after.available_bytes >= CONFIGURED_FLOOR_BYTES
            ),
        },
    }


def _open_child(root_fd: int, relative: str) -> int:
    current = os.dup(root_fd)
    try:
        for component in relative.split("/"):
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _write_receipt_create_only(receipt_path: Path, payload: dict[str, Any]) -> None:
    evidence_root = REPO / "docs/evidence"
    if receipt_path.parent != evidence_root or receipt_path.suffix != ".json":
        raise ValueError("receipt_path_must_be_in_docs_evidence")
    if evidence_root.is_symlink() or not evidence_root.is_dir():
        raise ValueError("unsafe_evidence_directory")
    encoded = (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    temporary = evidence_root / f".{receipt_path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        view = memoryview(encoded)
        while view:
            count = os.write(descriptor, view)
            if count <= 0:
                raise OSError("short receipt write")
            view = view[count:]
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, receipt_path, follow_symlinks=False)
        os.unlink(temporary)
        directory_fd = os.open(evidence_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _validate_receipt_destination(receipt_path: Path) -> None:
    evidence_root = REPO / "docs/evidence"
    if receipt_path.parent != evidence_root or receipt_path.suffix != ".json":
        raise ValueError("receipt_path_must_be_in_docs_evidence")
    if evidence_root.is_symlink() or not evidence_root.is_dir():
        raise ValueError("unsafe_evidence_directory")
    if receipt_path.exists() or receipt_path.is_symlink():
        raise FileExistsError("receipt_already_exists")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument(
        "--receipt",
        type=Path,
        default=REPO / "docs/evidence/kingston-capacity-recovery-qualification-v1-2026-10-12.json",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.execute:
        _validate_receipt_destination(args.receipt)
    result = run_qualification(args.config, execute=args.execute)
    if args.execute:
        _write_receipt_create_only(args.receipt, result)
        result["receipt_path"] = str(args.receipt)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
