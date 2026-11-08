#!/usr/bin/env python3
"""Qualify descriptor writes and flock on a private Kingston scratch subtree.

The configured external research root is held under ``StorageGuard`` for the
entire probe. Only a unique temporary directory directly below the service
root is created, read, and removed. No archive, legacy alias, provider, or
credential is accessed.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import platform
import stat
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

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

CONFIG_PATH = Path.home() / "Developer/memecoin-runtime/brain/config/storage-v1.json"
EXPECTED_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"


def _independent_lock_attempt(
    config_string: str,
    lock_relative: str,
    queue: multiprocessing.Queue,
) -> None:
    """Attempt a nonblocking descriptor lock in a fresh process."""
    try:
        config_path = Path(config_string)
        root = resolve(config_path)
        root_fd = os.open(root.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with DescriptorWriter(root_fd) as writer:
                try:
                    with writer.lock(lock_relative, blocking=False):
                        queue.put({"result": "acquired_unexpectedly"})
                except StorageError as exc:
                    if str(exc) == "external_writer_already_active":
                        queue.put(
                            {"result": "rejected", "reason": "external_writer_already_active"}
                        )
                    else:
                        queue.put({"result": "child_error", "type": "StorageError"})
        finally:
            os.close(root_fd)
    except BaseException as exc:
        queue.put({"result": "child_error", "type": type(exc).__name__})


def run_qualification() -> dict[str, object]:
    if platform.system() != "Darwin":
        raise RuntimeError("macos_required")
    config = StorageConfig.load(CONFIG_PATH)
    probe = MacOSVolumeProbe()
    volume = probe.inspect(config.mount_path)
    if (
        config.volume_uuid.lower() != EXPECTED_UUID
        or volume.volume_uuid.lower() != EXPECTED_UUID
        or volume.filesystem.lower().strip() != "case-sensitive apfs"
        or not volume.owners_enabled
        or volume.read_only
        or not volume.is_mountpoint
    ):
        raise RuntimeError("configured_volume_identity_or_ownership_failed")

    started_ns = time.time_ns()
    started_mono_ns = time.monotonic_ns()
    scratch_name = f"writer-qualification-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex}"
    payload = os.urandom(128)
    observed: dict[str, object] = {
        "config_path": str(CONFIG_PATH),
        "config_mode": oct(stat.S_IMODE(CONFIG_PATH.stat().st_mode)),
        "volume_uuid": volume.volume_uuid.lower(),
        "root_id": config.root_id,
        "mount_path": str(config.mount_path),
        "configured_data_root": config.data_root,
        "service_root": None,
        "filesystem": volume.filesystem,
        "owners_enabled": volume.owners_enabled,
        "read_only": volume.read_only,
        "is_mountpoint": volume.is_mountpoint,
        "available_bytes_before": volume.available_bytes,
        "minimum_free_bytes": config.min_free_bytes,
        "scratch_subtree": scratch_name,
        "payload_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    cleaned = False
    with writer_guard(CONFIG_PATH, probe=probe) as guard:
        if guard.volume_uuid.lower() != EXPECTED_UUID or guard.root_id != config.root_id:
            raise RuntimeError("guard_binding_mismatch")
        observed["service_root"] = str(guard.path)
        root_fd = os.open(guard.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with DescriptorWriter(root_fd, before_write=guard.revalidate) as writer:
                created_files: list[str] = []
                try:
                    writer.mkdir(scratch_name)
                    digest_path = f"{scratch_name}/payload.bin"
                    writer.create(digest_path, payload)
                    created_files.append(digest_path)
                    readback = writer.read_bytes(digest_path)
                    if readback != payload:
                        raise RuntimeError("descriptor_readback_mismatch")
                    observed["readback_sha256"] = hashlib.sha256(readback).hexdigest()
                    observed["readback_bytes"] = len(readback)

                    rename_path = f"{scratch_name}/renamed.json"
                    writer.atomic_write(rename_path, b"before-rename", create_only=True)
                    created_files.append(rename_path)
                    writer.atomic_write(rename_path, b"after-rename")
                    if writer.read_bytes(rename_path) != b"after-rename":
                        raise RuntimeError("atomic_rename_readback_mismatch")
                    observed["atomic_rename_sha256"] = hashlib.sha256(
                        writer.read_bytes(rename_path)
                    ).hexdigest()

                    collision = None
                    try:
                        writer.atomic_write(
                            rename_path, b"collision-must-not-replace", create_only=True
                        )
                    except StorageError as exc:
                        collision = str(exc)
                    if collision != "external_writer_create_only_collision":
                        raise RuntimeError(f"create_only_collision_unexpected:{collision}")
                    if writer.read_bytes(rename_path) != b"after-rename":
                        raise RuntimeError("create_only_collision_changed_payload")
                    observed["create_only_collision"] = collision
                    observed["collision_preserved_existing_sha256"] = hashlib.sha256(
                        writer.read_bytes(rename_path)
                    ).hexdigest()

                    lock_relative = f"{scratch_name}/qualification.lock"
                    with writer.lock(lock_relative):
                        ctx = multiprocessing.get_context("spawn")
                        queue = ctx.Queue()
                        child = ctx.Process(
                            target=_independent_lock_attempt,
                            args=(str(CONFIG_PATH), lock_relative, queue),
                        )
                        child.start()
                        child.join(timeout=20)
                        if child.is_alive():
                            child.terminate()
                            child.join(timeout=5)
                            raise RuntimeError("independent_lock_probe_timeout")
                        if child.exitcode != 0:
                            raise RuntimeError(f"independent_lock_child_exit:{child.exitcode}")
                        try:
                            lock_result = queue.get(timeout=3)
                        except Exception as exc:
                            raise RuntimeError("independent_lock_result_missing") from exc
                        finally:
                            queue.close()
                            queue.join_thread()
                        if (
                            lock_result.get("result") != "rejected"
                            or lock_result.get("reason") != "external_writer_already_active"
                        ):
                            raise RuntimeError(f"independent_lock_not_excluded:{lock_result}")
                        observed["independent_process_lock"] = lock_result
                        observed["independent_process_lock_path"] = lock_relative
                finally:
                    for relative in reversed(created_files):
                        writer.unlink(relative, missing_ok=True)
                    writer.unlink(f"{scratch_name}/qualification.lock", missing_ok=True)
                    guard.revalidate()
                    os.rmdir(scratch_name, dir_fd=writer.root_fd)
                    os.fsync(writer.root_fd)
                    cleaned = True
        finally:
            os.close(root_fd)

    if not cleaned:
        raise RuntimeError("scratch_cleanup_not_completed")
    resolved_after = resolve(CONFIG_PATH, probe=probe)
    observed["root_binding_after"] = {
        "volume_uuid": resolved_after.volume_uuid,
        "root_id": resolved_after.root_id,
        "scratch_absent": not (resolved_after.path / scratch_name).exists(),
        "available_bytes_after": resolved_after.available_bytes,
    }
    if not observed["root_binding_after"]["scratch_absent"]:
        raise RuntimeError("scratch_subtree_remained")

    source_paths = {
        "qualification_harness": Path(__file__).resolve(),
        "descriptor_writer": REPO / "tools/brain_senses_external_writer_v1.py",
        "storage_library": REPO / "src/memecoin_research_storage/__init__.py",
    }
    completed_ns = time.time_ns()
    return {
        "schema_version": 1,
        "contract": "research_kingston_writer_qualify_v1",
        "status": "passed",
        "scope": "live configured Kingston service root; disposable unique scratch subtree only",
        "provider_requests": 0,
        "archives_or_aliases_touched": False,
        "unmount_performed": False,
        "descriptor_writer_before_write": "StorageGuard.revalidate",
        "observed": observed,
        "cleanup": (
            "Files unlinked through DescriptorWriter; unique directory removed with "
            "rmdir(dir_fd=held_root_fd) and confirmed absent."
        ),
        "limitations": [
            (
                "This proves only the recorded live mount identity, scratch operations, and "
                "flock exclusion at qualification time."
            ),
            (
                "It does not prove device-loss behavior; the separate disposable APFS detach "
                "receipt covers that mechanism."
            ),
            (
                "Independent lock exclusion does not prove collector authorization or lease "
                "activation."
            ),
        ],
        "provenance": {
            "command": "PYTHONPATH=src python3 tools/research_kingston_writer_qualify_v1.py",
            "started_utc": datetime.fromtimestamp(started_ns / 1_000_000_000, UTC).isoformat(),
            "completed_utc": datetime.fromtimestamp(completed_ns / 1_000_000_000, UTC).isoformat(),
            "elapsed_monotonic_ns": time.monotonic_ns() - started_mono_ns,
            "platform": platform.platform(),
            "python": platform.python_version(),
            "source_sha256": {
                name: hashlib.sha256(path.read_bytes()).hexdigest()
                for name, path in source_paths.items()
            },
        },
    }


if __name__ == "__main__":
    try:
        print(json.dumps(run_qualification(), sort_keys=True, indent=2))
    except (OSError, StorageError, RuntimeError) as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}:{exc}"}))
        raise SystemExit(2) from None
