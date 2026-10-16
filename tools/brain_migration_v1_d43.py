#!/usr/bin/env python3
"""Opaque, create-only custody for the protected D43 migration subtree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import brain_migration_v1 as base
else:
    from tools import brain_migration_v1 as base

SCHEMA = "brain-practical-migration-v1-d43-opaque"
LABEL = "ai.memecoin.d43.calendar01"
SOURCE_ROOT = Path("/Users/root1/Desktop/memecoin-ai/work/calendar-execution-01")
PLIST_PATH = Path("/Users/root1/Library/LaunchAgents/ai.memecoin.d43.calendar01.plist")
PLIST_SHA256_PIN = "1558480d219673a7bc35683bc2e24bb6a1c80bb667daf15dc89569653c7f6e82"
MIGRATION_DIR = "BrainMigrationV1"
PROTECTED_DIR = "protected-candidates"
STABILITY_SECONDS = 65
STOP_WAIT_SECONDS = 180
CHUNK = 1024 * 1024
ALLOCATION_CUSHION_BYTES = 256 * 1024 * 1024


class D43MigrationError(RuntimeError):
    """Fail-closed operator error with a stable code."""


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _write_json_create(parent_fd: int, name: str, value: dict[str, Any]) -> None:
    """Publish one create-only metadata record with durable readback."""
    raw = _canonical(value)
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent_fd,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise D43MigrationError("metadata_write_stalled")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    base._fsync_directory(parent_fd)
    read_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        observed = base._read_fd(read_fd, max_bytes=1024 * 1024)
    finally:
        os.close(read_fd)
    if observed != raw:
        raise D43MigrationError("metadata_readback_mismatch")


def _plist_identity() -> tuple[str, os.stat_result]:
    try:
        parent_fd = base._open_directory(PLIST_PATH.parent)
        try:
            fd = os.open(
                PLIST_PATH.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd
            )
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    raise D43MigrationError("d43_service_plist_type_invalid")
                digest = hashlib.sha256()
                while block := os.read(fd, CHUNK):
                    digest.update(block)
                return digest.hexdigest(), st
            finally:
                os.close(fd)
        finally:
            os.close(parent_fd)
    except (OSError, base.MigrationError) as exc:
        raise D43MigrationError("plist_read_failed") from exc


def _launchctl_list() -> tuple[bool, str | None]:
    try:
        result = subprocess.run(
            ["/bin/launchctl", "list"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise D43MigrationError("launchctl_list_failed") from exc
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2] == LABEL:
            return True, fields[0]
    return False, None


def _disabled_state() -> bool:
    domain = f"gui/{os.getuid()}"
    try:
        result = subprocess.run(
            ["/bin/launchctl", "print-disabled", domain],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise D43MigrationError("launchctl_disabled_state_failed") from exc
    for line in result.stdout.splitlines():
        if LABEL in line:
            if "=> disabled" in line:
                return True
            if "=> enabled" in line:
                return False
            raise D43MigrationError("launchctl_disabled_state_unrecognized")
    return False  # No override means launchd's default enabled state.


def _root_identity(path: Path) -> os.stat_result:
    if path != SOURCE_ROOT:
        raise D43MigrationError("protected_root_path_mismatch")
    try:
        fd = base._open_directory(path)
    except (OSError, base.MigrationError) as exc:
        raise D43MigrationError("protected_root_unavailable") from exc
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    if not stat.S_ISDIR(st.st_mode):
        raise D43MigrationError("protected_root_type_invalid")
    return st


def _require_source_device(st: os.stat_result, root_device: int) -> None:
    if st.st_dev != root_device:
        raise D43MigrationError("d43_nested_mount_rejected")


def _require_separate_source_target(source_device: int, target_device: int) -> None:
    if source_device == target_device:
        raise D43MigrationError("protected_source_target_volume_alias")


def _target_capacity_guard(
    target_root: Path,
    target_fd: int,
    target_pin: base.TargetPin,
    initial_remaining_bytes: int,
) -> Callable[[int], None]:
    last_identity_check: float | None = None
    last_remaining = initial_remaining_bytes
    bytes_since_identity_check = 0

    def check(remaining: int) -> None:
        nonlocal last_identity_check, last_remaining, bytes_since_identity_check
        bytes_since_identity_check += max(0, last_remaining - remaining)
        last_remaining = remaining
        now = time.monotonic()
        if (
            last_identity_check is None
            or bytes_since_identity_check >= base.TARGET_RECHECK_BYTES
            or now - last_identity_check >= base.TARGET_RECHECK_SECONDS
        ):
            base._assert_held_target_identity(target_root, target_fd, target_pin)
            last_identity_check = time.monotonic()
            bytes_since_identity_check = 0
        base._require_free(
            target_fd,
            max(0, remaining)
            + base.FREE_FLOOR_BYTES
            + base.OPERATION_RESERVE_BYTES
            + ALLOCATION_CUSHION_BYTES,
        )

    return check


def _tree_members(root: Path) -> list[dict[str, Any]]:
    """Metadata-only walk relative to held directory descriptors."""
    members: list[dict[str, Any]] = []

    def visit(directory_fd: int, relative: str, st: os.stat_result, root_device: int) -> None:
        _require_source_device(st, root_device)
        if stat.S_ISDIR(st.st_mode):
            kind = "directory"
            link_target = None
        elif stat.S_ISREG(st.st_mode):
            kind = "file"
            link_target = None
        elif stat.S_ISLNK(st.st_mode):
            kind = "symlink"
            try:
                link_target = os.readlink(relative.rsplit("/", 1)[-1], dir_fd=directory_fd)
            except OSError as exc:
                raise D43MigrationError("source_symlink_read_failed") from exc
        else:
            raise D43MigrationError("source_special_node_rejected")
        members.append(
            {
                "path": relative,
                "type": kind,
                "source_signature": base._stat_sig(st),
                "source_device": st.st_dev,
                "source_inode": st.st_ino,
                "source_link_count": st.st_nlink,
                "source_mode": stat.S_IMODE(st.st_mode),
                "link_target": link_target,
            }
        )
        if kind == "directory":
            try:
                children = sorted(os.listdir(directory_fd))
            except OSError as exc:
                raise D43MigrationError("source_directory_read_failed") from exc
            for child_name in children:
                child_relative = child_name if not relative else f"{relative}/{child_name}"
                base._relative_path(child_relative)
                try:
                    child_stat = os.stat(child_name, dir_fd=directory_fd, follow_symlinks=False)
                    _require_source_device(child_stat, root_device)
                    if stat.S_ISDIR(child_stat.st_mode):
                        child_fd = os.open(
                            child_name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory_fd,
                        )
                        try:
                            opened = os.fstat(child_fd)
                            if (opened.st_dev, opened.st_ino) != (
                                child_stat.st_dev,
                                child_stat.st_ino,
                            ):
                                raise D43MigrationError("source_directory_identity_changed")
                            _require_source_device(opened, root_device)
                            visit(child_fd, child_relative, opened, root_device)
                        finally:
                            os.close(child_fd)
                    else:
                        visit(directory_fd, child_relative, child_stat, root_device)
                except OSError as exc:
                    raise D43MigrationError("source_member_stat_failed") from exc

    try:
        root_fd = base._open_directory(root)
    except base.MigrationError as exc:
        raise D43MigrationError("protected_root_open_failed") from exc
    try:
        root_stat = os.fstat(root_fd)
        visit(root_fd, "", root_stat, root_stat.st_dev)
    finally:
        os.close(root_fd)
    return members


def _inventory_signature(members: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in members:
        digest.update(_canonical(row))
    return digest.hexdigest()


def _lsof_writers(root: Path) -> list[dict[str, str]]:
    try:
        result = subprocess.run(
            ["/usr/sbin/lsof", "-Fpcaf", "+D", str(root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise D43MigrationError("lsof_protected_root_failed") from exc
    current: dict[str, Any] | None = None
    entries: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        tag, value = line[:1], line[1:]
        if tag == "p":
            if current:
                entries.append(current)
            current = {"pid": value, "command": "", "access": set()}
        elif tag == "c" and current is not None:
            current["command"] = value
        elif tag == "a" and current is not None:
            if value not in {"r", "w", "u"}:
                raise D43MigrationError("lsof_access_mode_unrecognized")
            current["access"].add(value)
    if current:
        entries.append(current)
    writers = [
        {"pid": row["pid"], "command": row["command"]}
        for row in entries
        if any(access in {"w", "u"} for access in row["access"])
    ]
    if result.returncode not in {0, 1}:
        raise D43MigrationError("lsof_protected_root_failed")
    if any(not row["access"] for row in entries):
        raise D43MigrationError("lsof_access_mode_missing")
    return writers


def _pause_exact_label(candidate_fd: int) -> dict[str, Any]:
    loaded, pid = _launchctl_list()
    previously_disabled = _disabled_state()
    if not loaded or pid != "-" or previously_disabled:
        raise D43MigrationError("d43_service_initial_state_unexpected")
    plist_sha, _ = _plist_identity()
    if plist_sha != PLIST_SHA256_PIN:
        raise D43MigrationError("d43_service_plist_sha256_mismatch")
    before = _root_identity(SOURCE_ROOT)
    initial_members = _tree_members(SOURCE_ROOT)
    initial_signature = _inventory_signature(initial_members)
    initial_writers = _lsof_writers(SOURCE_ROOT)
    if initial_writers:
        raise D43MigrationError("d43_source_has_writer_before_pause")

    intent = {
        "schema": SCHEMA,
        "label": LABEL,
        "prior_loaded": True,
        "prior_pid": "-",
        "prior_disabled_override": False,
        "disable_requested": True,
        "plist_sha256": plist_sha,
        "source_path": str(SOURCE_ROOT),
        "source_device": int(before.st_dev),
        "source_inode": int(before.st_ino),
        "pre_pause_inventory_signature_sha256": initial_signature,
        "pre_pause_member_count": len(initial_members),
        "pre_pause_logical_bytes": sum(
            int(row["source_signature"][4]) for row in initial_members if row["type"] == "file"
        ),
        "automatic_resume": False,
    }
    _write_json_create(candidate_fd, "PAUSE_INTENT.json", intent)
    intent_sha256 = hashlib.sha256(_canonical(intent)).hexdigest()

    domain_target = f"gui/{os.getuid()}/{LABEL}"
    try:
        subprocess.run(
            ["/bin/launchctl", "disable", domain_target],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise D43MigrationError("d43_disable_failed") from exc
    started = {
        "schema": SCHEMA,
        "label": LABEL,
        "prior_loaded": True,
        "prior_pid": "-",
        "prior_disabled_override": False,
        "disable_requested": True,
        "plist_sha256": plist_sha,
        "source_path": str(SOURCE_ROOT),
        "source_device": int(before.st_dev),
        "source_inode": int(before.st_ino),
        "automatic_resume": False,
        "pause_intent_sha256": intent_sha256,
    }
    _write_json_create(candidate_fd, "PAUSE_STARTED.json", started)

    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while True:
        loaded_now, pid_now = _launchctl_list()
        if not loaded_now or pid_now == "-":
            break
        if time.monotonic() >= deadline:
            raise D43MigrationError("d43_process_did_not_exit_naturally")
        time.sleep(2)
    # Keep the disabled, PID-free job loaded. Unloading could terminate a new
    # process between the PID sample and bootout; Migration v1 does not need it.
    stable_deadline = time.monotonic() + STABILITY_SECONDS
    while time.monotonic() < stable_deadline:
        loaded_now, pid_now = _launchctl_list()
        if loaded_now and pid_now != "-":
            raise D43MigrationError("d43_process_appeared_during_quiescence")
        if not _disabled_state():
            raise D43MigrationError("d43_disable_override_lost")
        if _lsof_writers(SOURCE_ROOT):
            raise D43MigrationError("d43_source_writer_during_quiescence")
        time.sleep(min(2, max(0.0, stable_deadline - time.monotonic())))
    loaded_after, pid_after = _launchctl_list()
    if (loaded_after and pid_after != "-") or not _disabled_state():
        raise D43MigrationError("d43_pause_not_confirmed")
    if _plist_identity()[0] != plist_sha:
        raise D43MigrationError("d43_service_plist_changed_during_pause")
    after = _root_identity(SOURCE_ROOT)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise D43MigrationError("d43_root_identity_changed_during_pause")
    if _lsof_writers(SOURCE_ROOT):
        raise D43MigrationError("d43_source_has_writer_after_pause")
    paused_members = _tree_members(SOURCE_ROOT)
    paused_signature = _inventory_signature(paused_members)
    # The job may have completed natural writes while launchd disabled it. The
    # post-stop inventory is authoritative; require it to stabilize afterward.
    time.sleep(STABILITY_SECONDS)
    stable_members = _tree_members(SOURCE_ROOT)
    stable_signature = _inventory_signature(stable_members)
    if stable_signature != paused_signature:
        raise D43MigrationError("d43_source_not_stable_after_pause")
    if _lsof_writers(SOURCE_ROOT):
        raise D43MigrationError("d43_source_writer_reappeared")
    loaded_final, pid_final = _launchctl_list()
    if (loaded_final and pid_final != "-") or not _disabled_state():
        raise D43MigrationError("d43_service_reactivated_during_stability_window")
    return {
        "label": LABEL,
        "prior_loaded": True,
        "prior_pid": "-",
        "prior_disabled_override": False,
        "disabled_after_pause": True,
        "loaded_after_pause": loaded_after,
        "loaded_after_pid": pid_after,
        "plist_sha256": plist_sha,
        "source_path": str(SOURCE_ROOT),
        "source_device": int(before.st_dev),
        "source_inode": int(before.st_ino),
        "pre_pause_inventory_signature_sha256": initial_signature,
        "pause_intent_sha256": intent_sha256,
        "post_pause_inventory_signature_sha256": stable_signature,
        "post_pause_member_count": len(stable_members),
        "post_pause_logical_bytes": sum(
            int(row["source_signature"][4]) for row in stable_members if row["type"] == "file"
        ),
        "stable_interval_seconds": STABILITY_SECONDS,
        "source_mutated_by_tool": False,
        "auto_resume": False,
    }


def _safe_parts(relative: str) -> tuple[str, ...]:
    path = PurePosixPath(relative)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in relative
        or "\x00" in relative
        or "/".join(path.parts) != relative
    ):
        raise D43MigrationError("manifest_path_invalid")
    return path.parts


def _mkdir_path(root_fd: int, parts: tuple[str, ...]) -> int:
    current = os.dup(root_fd)
    try:
        for part in parts:
            try:
                next_fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
                )
            except FileNotFoundError:
                next_fd = base._mkdir_open(current, part)
            os.close(current)
            current = next_fd
        return current
    except BaseException:
        os.close(current)
        raise


def _open_path(root_fd: int, parts: tuple[str, ...]) -> int:
    current = os.dup(root_fd)
    try:
        for part in parts:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = next_fd
        return current
    except BaseException:
        os.close(current)
        raise


def _ensure_directory(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except FileNotFoundError:
        return base._mkdir_open(parent_fd, name)


def _source_parent_fd(
    source_root_fd: int,
    parts: tuple[str, ...],
    source_rows_by_path: dict[str, dict[str, Any]],
) -> int:
    current = os.dup(source_root_fd)
    relative = ""
    try:
        for part in parts:
            relative = part if not relative else f"{relative}/{part}"
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            expected = source_rows_by_path.get(relative)
            opened = os.fstat(next_fd)
            if (
                expected is None
                or expected["type"] != "directory"
                or (opened.st_dev, opened.st_ino)
                != (expected["source_device"], expected["source_inode"])
            ):
                os.close(next_fd)
                raise D43MigrationError("source_parent_identity_changed")
            os.close(current)
            current = next_fd
        return current
    except BaseException:
        os.close(current)
        raise


def _copy_one(
    source_parent_fd: int,
    source_name: str,
    target_parent_fd: int,
    leaf: str,
    expected: dict[str, Any],
    *,
    remaining_bytes: int = 0,
    target_guard: Callable[[int], None] | None = None,
) -> tuple[int, str | None, str | None]:
    if target_guard is not None:
        target_guard(remaining_bytes)
    before = os.stat(source_name, dir_fd=source_parent_fd, follow_symlinks=False)
    kind = expected["type"]
    if base._stat_sig(before) != expected["source_signature"]:
        raise D43MigrationError("source_member_changed_before_copy")
    if kind == "directory":
        target_fd = base._mkdir_open(target_parent_fd, leaf)
        os.close(target_fd)
        return 0, None, None
    if kind == "symlink":
        link_target = os.readlink(source_name, dir_fd=source_parent_fd)
        if link_target != expected.get("link_target"):
            raise D43MigrationError("source_symlink_changed_before_copy")
        after = os.stat(source_name, dir_fd=source_parent_fd, follow_symlinks=False)
        if base._stat_sig(after) != expected["source_signature"]:
            raise D43MigrationError("source_symlink_changed_during_copy")
        os.symlink(link_target, leaf, dir_fd=target_parent_fd)
        payload = os.fsencode(link_target)
        return len(payload), hashlib.sha256(payload).hexdigest(), link_target
    src_fd = os.open(
        source_name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=source_parent_fd,
    )
    try:
        opened = os.fstat(src_fd)
        if base._stat_sig(opened) != expected["source_signature"]:
            raise D43MigrationError("source_file_identity_changed_before_copy")
        dst_fd = os.open(
            leaf,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=target_parent_fd,
        )
    except BaseException:
        os.close(src_fd)
        raise
    digest = hashlib.sha256()
    size = 0
    try:
        while block := os.read(src_fd, CHUNK):
            view = memoryview(block)
            while view:
                written = os.write(dst_fd, view)
                if written <= 0:
                    raise D43MigrationError("target_write_stalled")
                view = view[written:]
            digest.update(block)
            size += len(block)
            if target_guard is not None:
                target_guard(max(0, remaining_bytes - size))
        after = os.fstat(src_fd)
        if base._stat_sig(after) != expected["source_signature"] or size != before.st_size:
            raise D43MigrationError("source_file_changed_during_copy")
        os.fsync(dst_fd)
    finally:
        os.close(src_fd)
        os.close(dst_fd)
    return size, digest.hexdigest(), None


def _copy_members(
    source_rows: list[dict[str, Any]],
    payload_fd: int,
    source_root_fd: int,
    *,
    remaining_bytes: int = 0,
    target_guard: Callable[[int], None] | None = None,
) -> list[dict[str, Any]]:
    copied_rows: list[dict[str, Any]] = []
    source_rows_by_path = {row["path"]: row for row in source_rows}
    for source_row in source_rows:
        relative = source_row["path"]
        if relative == "":
            root_fd = _mkdir_path(payload_fd, ("calendar-execution-01",))
            os.close(root_fd)
            copied_rows.append(
                {
                    "record_type": "member",
                    "alias": "protected_d43",
                    "path": "calendar-execution-01",
                    "type": "directory",
                    "size_bytes": 0,
                    "sha256": None,
                    "custody_class": "protected_opaque",
                    "source_path": str(SOURCE_ROOT),
                    "source_signature": source_row["source_signature"],
                    "source_device": source_row["source_device"],
                    "source_inode": source_row["source_inode"],
                    "source_link_count": source_row["source_link_count"],
                    "source_mode": source_row["source_mode"],
                    "link_target": None,
                }
            )
            continue
        parts = _safe_parts(relative)
        source_parent_fd = _source_parent_fd(source_root_fd, parts[:-1], source_rows_by_path)
        parent_fd = _mkdir_path(payload_fd, ("calendar-execution-01", *parts[:-1]))
        try:
            size, digest, link_target = _copy_one(
                source_parent_fd,
                parts[-1],
                parent_fd,
                parts[-1],
                source_row,
                remaining_bytes=remaining_bytes,
                target_guard=target_guard,
            )
        finally:
            os.close(parent_fd)
            os.close(source_parent_fd)
        record = {
            "record_type": "member",
            "alias": "protected_d43",
            "path": f"calendar-execution-01/{relative}",
            "type": source_row["type"],
            "size_bytes": size,
            "sha256": digest,
            "custody_class": "protected_opaque",
            "source_path": str(SOURCE_ROOT / relative),
            "source_signature": source_row["source_signature"],
            "source_device": source_row["source_device"],
            "source_inode": source_row["source_inode"],
            "source_link_count": source_row["source_link_count"],
            "source_mode": source_row["source_mode"],
            "link_target": source_row["link_target"],
        }
        if link_target is not None:
            record["link_target"] = link_target
        copied_rows.append(record)
        if source_row["type"] == "file":
            remaining_bytes = max(0, remaining_bytes - size)
        if target_guard is not None:
            target_guard(remaining_bytes)
    return copied_rows


def _verify_payload(payload_fd: int, rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        relative = row["path"]
        parts = _safe_parts(relative)
        parent_fd = _open_path(payload_fd, parts[:-1])
        try:
            name = parts[-1]
            st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            kind = base._entry_kind(st.st_mode)
            if kind != row["type"]:
                raise D43MigrationError("readback_member_type_mismatch")
            count += 1
            if kind == "directory":
                continue
            if kind == "symlink":
                link = os.readlink(name, dir_fd=parent_fd)
                body = os.fsencode(link)
                size, sha = len(body), hashlib.sha256(body).hexdigest()
                if (
                    link != row.get("link_target")
                    or size != row["size_bytes"]
                    or sha != row["sha256"]
                ):
                    raise D43MigrationError("readback_symlink_mismatch")
            else:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
                try:
                    digest = hashlib.sha256()
                    size = 0
                    while block := os.read(fd, CHUNK):
                        digest.update(block)
                        size += len(block)
                    sha = digest.hexdigest()
                finally:
                    os.close(fd)
                if size != row["size_bytes"] or sha != row["sha256"]:
                    raise D43MigrationError("readback_member_digest_mismatch")
        finally:
            os.close(parent_fd)
    return count


def capture_d43(
    *,
    target_root: Path,
    candidate_id: str | None = None,
    paused_candidate_id: str | None = None,
) -> dict[str, Any]:
    candidate_id = candidate_id or uuid.uuid4().hex
    if not base.ID_RE.fullmatch(candidate_id):
        raise D43MigrationError("candidate_id_invalid")
    target_fd, target_pin = base._validate_target(target_root)
    candidate_fd = -1
    try:
        before_members = _tree_members(SOURCE_ROOT)
        before_bytes = (
            sum(int(row["source_signature"][4]) for row in before_members if row["type"] == "file")
            + _plist_identity()[1].st_size
        )
        if before_bytes > base.PAYLOAD_CAP_BYTES:
            raise D43MigrationError("protected_payload_cap_exceeded")
        base._require_free(
            target_fd,
            before_bytes
            + base.FREE_FLOOR_BYTES
            + base.OPERATION_RESERVE_BYTES
            + ALLOCATION_CUSHION_BYTES,
        )
        continuation = (
            _resume_existing_pause(target_fd, target_pin, paused_candidate_id)
            if paused_candidate_id is not None
            else None
        )
        migration_fd = os.open(
            MIGRATION_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=target_fd
        )
        try:
            protected_fd = os.open(
                PROTECTED_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=migration_fd
            )
        except FileNotFoundError:
            protected_fd = base._mkdir_open(migration_fd, PROTECTED_DIR)
        try:
            protected_stat = os.fstat(protected_fd)
            if (
                protected_stat.st_uid != os.geteuid()
                or stat.S_IMODE(protected_stat.st_mode) != 0o700
            ):
                raise D43MigrationError("protected_candidates_permissions_invalid")
            try:
                os.stat(candidate_id, dir_fd=protected_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise D43MigrationError("candidate_already_exists")
            candidate_fd = base._mkdir_open(protected_fd, candidate_id)
        finally:
            os.close(protected_fd)
            os.close(migration_fd)
        start = {
            "schema": SCHEMA,
            "candidate_id": candidate_id,
            "state": "incomplete_until_manifest_published",
            "target_volume_uuid": target_pin.volume_uuid,
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _write_json_create(candidate_fd, "CAPTURE_STARTED.json", start)
        base._assert_held_target_identity(target_root, target_fd, target_pin)

        if continuation is None:
            pause = _pause_exact_label(candidate_fd)
            _write_json_create(candidate_fd, "PAUSE_WITNESS.json", pause)
        else:
            _write_json_create(candidate_fd, "PAUSE_INTENT.json", continuation["intent"])
            _write_json_create(candidate_fd, "PAUSE_STARTED.json", continuation["started"])
            pause = continuation["witness"]
            _write_json_create(candidate_fd, "PAUSE_WITNESS.json", pause)
            _write_json_create(candidate_fd, "PAUSE_CONTINUATION.json", continuation["record"])

        source_root = SOURCE_ROOT
        root_stat = _root_identity(source_root)
        source_rows = _tree_members(source_root)
        source_signature = _inventory_signature(source_rows)
        if source_signature != pause["post_pause_inventory_signature_sha256"]:
            raise D43MigrationError("d43_inventory_changed_after_pause")
        plist_sha, plist_stat = _plist_identity()
        if plist_sha != PLIST_SHA256_PIN or plist_sha != pause["plist_sha256"]:
            raise D43MigrationError("d43_service_plist_sha256_mismatch")
        plist_parent_fd = base._open_directory(PLIST_PATH.parent)
        try:
            plist_parent_stat = os.fstat(plist_parent_fd)
        finally:
            os.close(plist_parent_fd)
        source_rows.append(
            {
                "path": "LaunchAgents",
                "type": "directory",
                "source_signature": base._stat_sig(plist_parent_stat),
                "source_device": plist_parent_stat.st_dev,
                "source_inode": plist_parent_stat.st_ino,
                "source_link_count": plist_parent_stat.st_nlink,
                "source_mode": stat.S_IMODE(plist_parent_stat.st_mode),
                "link_target": None,
                "copy_source_path": str(PLIST_PATH.parent),
            }
        )
        source_rows.append(
            {
                "path": "LaunchAgents/ai.memecoin.d43.calendar01.plist",
                "type": "file",
                "source_signature": base._stat_sig(plist_stat),
                "source_device": plist_stat.st_dev,
                "source_inode": plist_stat.st_ino,
                "source_link_count": plist_stat.st_nlink,
                "source_mode": stat.S_IMODE(plist_stat.st_mode),
                "link_target": None,
                "copy_source_path": str(PLIST_PATH),
            }
        )
        source_bytes = sum(
            int(row["source_signature"][4]) for row in source_rows if row["type"] == "file"
        )
        if source_bytes > base.PAYLOAD_CAP_BYTES:
            raise D43MigrationError("protected_payload_cap_exceeded")
        _require_separate_source_target(root_stat.st_dev, os.fstat(target_fd).st_dev)
        required = source_bytes + base.FREE_FLOOR_BYTES + base.OPERATION_RESERVE_BYTES
        base._require_free(target_fd, required + ALLOCATION_CUSHION_BYTES)

        target_guard = _target_capacity_guard(target_root, target_fd, target_pin, source_bytes)
        target_guard(source_bytes)
        payload_fd = base._mkdir_open(candidate_fd, "payload")
        try:
            d43_rows = [row for row in source_rows if "copy_source_path" not in row]
            source_root_fd = base._open_directory(source_root)
            try:
                opened_root = os.fstat(source_root_fd)
                if (opened_root.st_dev, opened_root.st_ino) != (
                    root_stat.st_dev,
                    root_stat.st_ino,
                ):
                    raise D43MigrationError("d43_root_identity_changed_after_pause")
                d43_file_bytes = sum(
                    int(row["source_signature"][4]) for row in d43_rows if row["type"] == "file"
                )
                records = _copy_members(
                    d43_rows,
                    payload_fd,
                    source_root_fd,
                    remaining_bytes=source_bytes,
                    target_guard=target_guard,
                )
            finally:
                os.close(source_root_fd)
            plist_dir_row, plist_row = source_rows[-2:]
            plist_parent = _mkdir_path(payload_fd, ("LaunchAgents",))
            records.append(
                {
                    "record_type": "member",
                    "alias": "protected_d43",
                    "path": "LaunchAgents",
                    "type": "directory",
                    "size_bytes": 0,
                    "sha256": None,
                    "custody_class": "protected_opaque",
                    "source_path": str(PLIST_PATH.parent),
                    "source_signature": plist_dir_row["source_signature"],
                    "source_device": plist_dir_row["source_device"],
                    "source_inode": plist_dir_row["source_inode"],
                    "source_link_count": plist_dir_row["source_link_count"],
                    "source_mode": plist_dir_row["source_mode"],
                    "link_target": None,
                }
            )
            plist_source_parent = base._open_directory(PLIST_PATH.parent)
            try:
                plist_size, plist_hash, _ = _copy_one(
                    plist_source_parent,
                    PLIST_PATH.name,
                    plist_parent,
                    PLIST_PATH.name,
                    plist_row,
                    remaining_bytes=max(0, source_bytes - d43_file_bytes),
                    target_guard=target_guard,
                )
            finally:
                os.close(plist_parent)
                os.close(plist_source_parent)
            if plist_hash != PLIST_SHA256_PIN:
                raise D43MigrationError("d43_plist_copy_hash_mismatch")
            records.append(
                {
                    "record_type": "member",
                    "alias": "protected_d43",
                    "path": f"LaunchAgents/{PLIST_PATH.name}",
                    "type": "file",
                    "size_bytes": plist_size,
                    "sha256": plist_hash,
                    "custody_class": "protected_opaque",
                    "source_path": str(PLIST_PATH),
                    "source_signature": plist_row["source_signature"],
                    "source_device": plist_row["source_device"],
                    "source_inode": plist_row["source_inode"],
                    "source_link_count": plist_row["source_link_count"],
                    "source_mode": plist_row["source_mode"],
                    "link_target": None,
                }
            )
            os.fsync(payload_fd)
        finally:
            os.close(payload_fd)
        if (
            _inventory_signature(_tree_members(source_root))
            != pause["post_pause_inventory_signature_sha256"]
        ):
            raise D43MigrationError("d43_source_changed_during_capture")
        if _plist_identity()[0] != PLIST_SHA256_PIN:
            raise D43MigrationError("d43_plist_changed_during_capture")
        if _lsof_writers(source_root):
            raise D43MigrationError("d43_writer_during_capture")
        loaded, pid = _launchctl_list()
        if (loaded and pid != "-") or not _disabled_state():
            raise D43MigrationError("d43_service_reactivated_during_capture")
        target_guard(0)
        payload_fd = os.open(
            "payload", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=candidate_fd
        )
        try:
            member_count = _verify_payload(payload_fd, records)
        finally:
            os.close(payload_fd)
        candidate_stat = os.fstat(candidate_fd)
        header = {
            "record_type": "header",
            "schema": SCHEMA,
            "version": 1,
            "candidate_id": candidate_id,
            "candidate_status": "complete",
            "candidate_device": int(candidate_stat.st_dev),
            "candidate_inode": int(candidate_stat.st_ino),
            "role": "D43_protected_opaque_custody_only",
            "protected_semantics_used": False,
            "target_volume_uuid": target_pin.volume_uuid,
            "target_device_identifier": target_pin.device_identifier,
            "source_root_path": str(source_root),
            "source_root_device": int(root_stat.st_dev),
            "source_root_inode": int(root_stat.st_ino),
            "source_inventory_signature_sha256": pause["post_pause_inventory_signature_sha256"],
            "source_inventory_member_count": pause["post_pause_member_count"],
            "source_logical_bytes": pause["post_pause_logical_bytes"],
            "launch_agent_label": LABEL,
            "launch_agent_plist_sha256": PLIST_SHA256_PIN,
            "pause_witness": pause,
            "pause_mode": (
                "continued_existing_pause" if continuation is not None else "fresh_disable"
            ),
            "pause_continuation": continuation["record"] if continuation is not None else None,
            "member_count": member_count,
            "payload_bytes": sum(
                int(row["size_bytes"]) for row in records if row["type"] == "file"
            ),
            "start_or_activation": False,
        }
        manifest_fd = os.open(
            "MANIFEST.jsonl",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=candidate_fd,
        )
        try:
            for row in (header, *records):
                view = memoryview(_canonical(row))
                while view:
                    written = os.write(manifest_fd, view)
                    if written <= 0:
                        raise D43MigrationError("manifest_write_stalled")
                    view = view[written:]
            os.fsync(manifest_fd)
        finally:
            os.close(manifest_fd)
        base._fsync_directory(candidate_fd)
        base._assert_held_target_identity(target_root, target_fd, target_pin)
        manifest_fd = os.open("MANIFEST.jsonl", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=candidate_fd)
        try:
            manifest_bytes = base._read_fd(manifest_fd, max_bytes=64 * 1024 * 1024)
        finally:
            os.close(manifest_fd)
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        parsed_header, parsed_rows = _parse_manifest(manifest_bytes, manifest_sha)
        candidate_stat = os.fstat(candidate_fd)
        payload_fd = os.open(
            "payload", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=candidate_fd
        )
        try:
            _verify_candidate_payload(
                payload_fd, parsed_rows, parsed_header, candidate_id, target_pin, candidate_stat
            )
        finally:
            os.close(payload_fd)
        seal = {
            "schema": SCHEMA,
            "state": "capture_readback_verified",
            "candidate_id": candidate_id,
            "target_volume_uuid": target_pin.volume_uuid,
            "target_device_identifier": target_pin.device_identifier,
            "candidate_device": int(candidate_stat.st_dev),
            "candidate_inode": int(candidate_stat.st_ino),
            "manifest_sha256": manifest_sha,
            "member_count": parsed_header["member_count"],
            "payload_bytes": parsed_header["payload_bytes"],
            "start_or_activation": False,
        }
        _write_json_create(candidate_fd, "CAPTURE_COMPLETE.json", seal)
        base._assert_held_target_identity(target_root, target_fd, target_pin)
        return verify_d43(
            target_root=target_root,
            candidate_id=candidate_id,
            expected_manifest_sha256=manifest_sha,
        )
    finally:
        if candidate_fd >= 0:
            os.close(candidate_fd)
        os.close(target_fd)


def _parse_manifest(
    raw: bytes, expected_sha256: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not base.SHA_RE.fullmatch(expected_sha256):
        raise D43MigrationError("manifest_sha256_pin_invalid")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise D43MigrationError("manifest_sha256_mismatch")
    rows: list[dict[str, Any]] = []
    header: dict[str, Any] | None = None
    try:
        for index, line in enumerate(raw.splitlines(keepends=True)):
            if not line.endswith(b"\n"):
                raise D43MigrationError("manifest_final_line_unterminated")
            row = json.loads(line, object_pairs_hook=base._pairs_no_duplicates)
            if not isinstance(row, dict):
                raise D43MigrationError("manifest_row_invalid")
            if index == 0:
                header = row
            else:
                rows.append(row)
    except (UnicodeDecodeError, json.JSONDecodeError, base.MigrationError) as exc:
        raise D43MigrationError("manifest_read_failed") from exc
    if (
        header is None
        or not rows
        or header.get("record_type") != "header"
        or header.get("schema") != SCHEMA
        or header.get("version") != 1
        or header.get("candidate_status") != "complete"
        or header.get("role") != "D43_protected_opaque_custody_only"
        or header.get("protected_semantics_used") is not False
        or header.get("start_or_activation") is not False
        or header.get("launch_agent_label") != LABEL
        or header.get("launch_agent_plist_sha256") != PLIST_SHA256_PIN
        or header.get("source_root_path") != str(SOURCE_ROOT)
        or not isinstance(header.get("candidate_id"), str)
        or not base.ID_RE.fullmatch(header["candidate_id"])
        or header.get("pause_mode") not in {"fresh_disable", "continued_existing_pause"}
    ):
        raise D43MigrationError("manifest_schema_or_state_invalid")
    continuation = header.get("pause_continuation")
    if header["pause_mode"] == "fresh_disable":
        if continuation is not None:
            raise D43MigrationError("manifest_pause_mode_join_invalid")
    elif (
        not isinstance(continuation, dict)
        or continuation.get("state") != "continued_existing_pause"
        or continuation.get("prior_candidate_id") == header["candidate_id"]
        or continuation.get("automatic_resume") is not False
        or continuation.get("source_inventory_signature_sha256")
        != header.get("source_inventory_signature_sha256")
        or continuation.get("revalidated_stable_interval_seconds", 0) < STABILITY_SECONDS
        or any(
            not base.SHA_RE.fullmatch(str(continuation.get(field, "")))
            for field in (
                "pause_intent_sha256",
                "pause_started_sha256",
                "prior_pause_witness_sha256",
            )
        )
    ):
        raise D43MigrationError("manifest_pause_continuation_invalid")
    pause = header.get("pause_witness")
    if (
        not isinstance(pause, dict)
        or pause.get("label") != LABEL
        or pause.get("source_path") != str(SOURCE_ROOT)
        or pause.get("plist_sha256") != PLIST_SHA256_PIN
        or pause.get("prior_loaded") is not True
        or pause.get("prior_pid") != "-"
        or pause.get("prior_disabled_override") is not False
        or pause.get("disabled_after_pause") is not True
        or pause.get("loaded_after_pause") not in {True, False}
        or pause.get("loaded_after_pid") not in {None, "-"}
        or pause.get("auto_resume") is not False
        or not base.SHA_RE.fullmatch(str(pause.get("pause_intent_sha256", "")))
        or pause.get("source_mutated_by_tool") is not False
        or pause.get("source_device") != header.get("source_root_device")
        or pause.get("source_inode") != header.get("source_root_inode")
        or pause.get("post_pause_inventory_signature_sha256")
        != header.get("source_inventory_signature_sha256")
        or pause.get("post_pause_member_count") != header.get("source_inventory_member_count")
        or pause.get("post_pause_logical_bytes") != header.get("source_logical_bytes")
        or pause.get("stable_interval_seconds", 0) < STABILITY_SECONDS
        or not base.SHA_RE.fullmatch(str(pause.get("post_pause_inventory_signature_sha256", "")))
        or (
            header["pause_mode"] == "continued_existing_pause"
            and (
                pause.get("continued_from_candidate_id") != continuation.get("prior_candidate_id")
                or pause.get("continuation_inventory_signature_sha256")
                != continuation.get("source_inventory_signature_sha256")
                or pause.get("continuation_stable_interval_seconds", 0) < STABILITY_SECONDS
            )
        )
        or (header["pause_mode"] == "fresh_disable" and "continued_from_candidate_id" in pause)
    ):
        raise D43MigrationError("manifest_pause_witness_invalid")
    seen: set[str] = set()
    for row in rows:
        relative = row.get("path")
        if (
            row.get("record_type") != "member"
            or row.get("alias") != "protected_d43"
            or row.get("custody_class") != "protected_opaque"
            or row.get("type") not in {"file", "directory", "symlink"}
            or not isinstance(relative, str)
            or relative in seen
        ):
            raise D43MigrationError("manifest_member_invalid")
        _safe_parts(relative)
        seen.add(relative)
        if row["type"] == "file":
            if (
                not isinstance(row.get("size_bytes"), int)
                or row["size_bytes"] < 0
                or not isinstance(row.get("sha256"), str)
                or not base.SHA_RE.fullmatch(row["sha256"])
            ):
                raise D43MigrationError("manifest_file_digest_invalid")
        elif row["type"] == "symlink":
            if (
                not isinstance(row.get("link_target"), str)
                or not isinstance(row.get("size_bytes"), int)
                or row["size_bytes"] != len(os.fsencode(row["link_target"]))
                or not isinstance(row.get("sha256"), str)
                or row["sha256"] != hashlib.sha256(os.fsencode(row["link_target"])).hexdigest()
            ):
                raise D43MigrationError("manifest_symlink_digest_invalid")
        elif row.get("size_bytes") != 0 or row.get("sha256") is not None:
            raise D43MigrationError("manifest_nonfile_digest_invalid")
        elif row.get("link_target") is not None:
            raise D43MigrationError("manifest_nonlink_target_invalid")
        if (
            not isinstance(row.get("source_path"), str)
            or not isinstance(row.get("source_signature"), list)
            or len(row["source_signature"]) != 11
            or any(not isinstance(value, int) or value < 0 for value in row["source_signature"])
            or any(
                not isinstance(row.get(field), int) or row[field] < 0
                for field in ("source_device", "source_inode", "source_link_count", "source_mode")
            )
        ):
            raise D43MigrationError("manifest_lineage_invalid")
        if (
            row["source_signature"][0] != row["source_device"]
            or row["source_signature"][1] != row["source_inode"]
            or row["source_signature"][3] != row["source_link_count"]
            or stat.S_IMODE(row["source_signature"][2]) != row["source_mode"]
            or base._entry_kind(row["source_signature"][2]) != row["type"]
            or (row["type"] == "file" and row["source_signature"][4] != row["size_bytes"])
        ):
            raise D43MigrationError("manifest_lineage_signature_mismatch")
        if relative == "calendar-execution-01":
            expected_source_path = str(SOURCE_ROOT)
        elif relative == "LaunchAgents":
            expected_source_path = str(PLIST_PATH.parent)
        elif relative == f"LaunchAgents/{PLIST_PATH.name}":
            expected_source_path = str(PLIST_PATH)
        elif relative.startswith("calendar-execution-01/"):
            expected_source_path = str(
                SOURCE_ROOT / relative.removeprefix("calendar-execution-01/")
            )
        else:
            raise D43MigrationError("manifest_path_outside_d43_roots")
        if row["source_path"] != expected_source_path:
            raise D43MigrationError("manifest_lineage_path_mismatch")
    if "calendar-execution-01" not in seen or f"LaunchAgents/{PLIST_PATH.name}" not in seen:
        raise D43MigrationError("manifest_required_roots_missing")
    plist_row = next(row for row in rows if row["path"] == f"LaunchAgents/{PLIST_PATH.name}")
    if plist_row.get("sha256") != PLIST_SHA256_PIN:
        raise D43MigrationError("manifest_plist_hash_mismatch")
    source_rows = [row for row in rows if row["path"].startswith("calendar-execution-01")]
    source_signature_rows = [
        {
            "path": row["path"].removeprefix("calendar-execution-01/")
            if row["path"] != "calendar-execution-01"
            else "",
            "type": row["type"],
            "source_signature": row["source_signature"],
            "source_device": row["source_device"],
            "source_inode": row["source_inode"],
            "source_link_count": row["source_link_count"],
            "source_mode": row["source_mode"],
            "link_target": row.get("link_target"),
        }
        for row in source_rows
    ]
    if (
        len(source_rows) != header.get("source_inventory_member_count")
        or _inventory_signature(source_signature_rows)
        != header.get("source_inventory_signature_sha256")
        or sum(row["source_signature"][4] for row in source_rows if row["type"] == "file")
        != header.get("source_logical_bytes")
        or source_rows[0]["source_device"] != header.get("source_root_device")
        or source_rows[0]["source_inode"] != header.get("source_root_inode")
        or not isinstance(header.get("candidate_device"), int)
        or header["candidate_device"] < 0
        or not isinstance(header.get("candidate_inode"), int)
        or header["candidate_inode"] < 1
        or sum(row["size_bytes"] for row in rows if row["type"] == "file")
        != header.get("payload_bytes")
        or len(rows) != header.get("member_count")
    ):
        raise D43MigrationError("manifest_inventory_join_invalid")
    return header, rows


def _read_manifest(path: Path, expected_sha256: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            raw = base._read_fd(fd, max_bytes=64 * 1024 * 1024)
        finally:
            os.close(fd)
    except (OSError, base.MigrationError) as exc:
        raise D43MigrationError("manifest_read_failed") from exc
    return _parse_manifest(raw, expected_sha256)


def verify_d43(
    *, target_root: Path, candidate_id: str, expected_manifest_sha256: str
) -> dict[str, Any]:
    target_fd, pin = base._validate_target(target_root)
    try:
        candidate_fd = _open_candidate_fd(target_fd, candidate_id)
        try:
            header, rows = _read_manifest_at(candidate_fd, expected_manifest_sha256)
            _verify_candidate_metadata(
                candidate_fd, target_fd, candidate_id, pin, header, expected_manifest_sha256
            )
            candidate_stat = os.fstat(candidate_fd)
            payload_fd = os.open(
                "payload", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=candidate_fd
            )
            try:
                count = _verify_candidate_payload(
                    payload_fd, rows, header, candidate_id, pin, candidate_stat
                )
            finally:
                os.close(payload_fd)
        finally:
            os.close(candidate_fd)
        base._assert_held_target_identity(target_root, target_fd, pin)
        return {
            "candidate_id": candidate_id,
            "manifest_sha256": expected_manifest_sha256,
            "member_count": count,
            "payload_bytes": header.get("payload_bytes"),
            "state": "protected_opaque_candidate_verified",
            "protected_semantics_used": False,
            "target_volume_uuid": pin.volume_uuid,
        }
    finally:
        os.close(target_fd)


def _open_candidate_fd(target_fd: int, candidate_id: str) -> int:
    if not base.ID_RE.fullmatch(candidate_id):
        raise D43MigrationError("candidate_id_invalid")
    current = os.dup(target_fd)
    try:
        for index, name in enumerate((MIGRATION_DIR, PROTECTED_DIR, candidate_id)):
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = next_fd
            st = os.fstat(current)
            if index > 0 and (st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700):
                raise D43MigrationError("candidate_directory_permissions_invalid")
        return current
    except BaseException:
        os.close(current)
        raise


def _read_manifest_at(
    candidate_fd: int, expected_sha256: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest_fd = os.open("MANIFEST.jsonl", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=candidate_fd)
    try:
        raw = base._read_fd(manifest_fd, max_bytes=64 * 1024 * 1024)
    except base.MigrationError as exc:
        raise D43MigrationError("manifest_read_failed") from exc
    finally:
        os.close(manifest_fd)
    return _parse_manifest(raw, expected_sha256)


def _read_json_at(candidate_fd: int, name: str) -> dict[str, Any]:
    value, _raw = _read_json_hashed_at(candidate_fd, name)
    return value


def _read_json_hashed_at(candidate_fd: int, name: str) -> tuple[dict[str, Any], bytes]:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=candidate_fd)
    try:
        raw = base._read_fd(fd, max_bytes=1024 * 1024)
        value = json.loads(raw, object_pairs_hook=base._pairs_no_duplicates)
    except (json.JSONDecodeError, base.MigrationError) as exc:
        raise D43MigrationError("candidate_metadata_invalid") from exc
    finally:
        os.close(fd)
    if not isinstance(value, dict):
        raise D43MigrationError("candidate_metadata_invalid")
    return value, raw


def _resume_existing_pause(
    target_fd: int, pin: base.TargetPin, prior_candidate_id: str
) -> dict[str, Any]:
    """Revalidate an existing durable pause without reenabling or rereading its payload."""
    prior_fd = _open_candidate_fd(target_fd, prior_candidate_id)
    try:
        if set(os.listdir(prior_fd)) != {
            "CAPTURE_STARTED.json",
            "PAUSE_INTENT.json",
            "PAUSE_STARTED.json",
            "PAUSE_WITNESS.json",
            "payload",
        }:
            raise D43MigrationError("continuation_source_not_incomplete_pause")
        started_capture = _read_json_at(prior_fd, "CAPTURE_STARTED.json")
        intent, intent_raw = _read_json_hashed_at(prior_fd, "PAUSE_INTENT.json")
        started, started_raw = _read_json_hashed_at(prior_fd, "PAUSE_STARTED.json")
        prior_witness, witness_raw = _read_json_hashed_at(prior_fd, "PAUSE_WITNESS.json")
        intent_sha = hashlib.sha256(intent_raw).hexdigest()
        intent_fields_valid = (
            started_capture.get("schema") == SCHEMA
            and started_capture.get("candidate_id") == prior_candidate_id
            and started_capture.get("state") == "incomplete_until_manifest_published"
            and started_capture.get("target_volume_uuid") == pin.volume_uuid
            and intent.get("schema") == SCHEMA
            and intent.get("label") == LABEL
            and intent.get("prior_loaded") is True
            and intent.get("prior_pid") == "-"
            and intent.get("prior_disabled_override") is False
            and intent.get("disable_requested") is True
            and intent.get("plist_sha256") == PLIST_SHA256_PIN
            and intent.get("source_path") == str(SOURCE_ROOT)
            and intent.get("automatic_resume") is False
            and started.get("schema") == SCHEMA
            and started.get("label") == LABEL
            and started.get("prior_loaded") is True
            and started.get("prior_pid") == "-"
            and started.get("prior_disabled_override") is False
            and started.get("disable_requested") is True
            and started.get("plist_sha256") == PLIST_SHA256_PIN
            and started.get("source_path") == str(SOURCE_ROOT)
            and started.get("pause_intent_sha256") == intent_sha
            and started.get("automatic_resume") is False
        )
        source_device = intent.get("source_device")
        source_inode = intent.get("source_inode")
        if (
            not intent_fields_valid
            or not isinstance(source_device, int)
            or not isinstance(source_inode, int)
            or source_device < 0
            or source_inode < 1
            or started.get("source_device") != source_device
            or started.get("source_inode") != source_inode
            or prior_witness.get("label") != LABEL
            or prior_witness.get("source_path") != str(SOURCE_ROOT)
            or prior_witness.get("plist_sha256") != PLIST_SHA256_PIN
            or prior_witness.get("prior_loaded") is not True
            or prior_witness.get("prior_pid") != "-"
            or prior_witness.get("prior_disabled_override") is not False
            or prior_witness.get("disabled_after_pause") is not True
            or prior_witness.get("loaded_after_pause") is not True
            or prior_witness.get("loaded_after_pid") != "-"
            or prior_witness.get("auto_resume") is not False
            or prior_witness.get("source_mutated_by_tool") is not False
            or prior_witness.get("source_device") != source_device
            or prior_witness.get("source_inode") != source_inode
            or prior_witness.get("pause_intent_sha256") != intent_sha
            or prior_witness.get("stable_interval_seconds", 0) < STABILITY_SECONDS
            or not base.SHA_RE.fullmatch(
                str(prior_witness.get("post_pause_inventory_signature_sha256", ""))
            )
        ):
            raise D43MigrationError("continuation_pause_records_invalid")

        base._assert_held_target_identity(Path(pin.mount_point), target_fd, pin)
        loaded, pid = _launchctl_list()
        if not loaded or pid != "-" or not _disabled_state():
            raise D43MigrationError("continuation_d43_not_disabled_pid_free")
        if _plist_identity()[0] != PLIST_SHA256_PIN:
            raise D43MigrationError("continuation_plist_pin_mismatch")
        initial_root = _root_identity(SOURCE_ROOT)
        if (initial_root.st_dev, initial_root.st_ino) != (source_device, source_inode):
            raise D43MigrationError("continuation_source_root_identity_mismatch")
        if _lsof_writers(SOURCE_ROOT):
            raise D43MigrationError("continuation_source_writer_present")
        initial_signature = _inventory_signature(_tree_members(SOURCE_ROOT))
        expected_signature = prior_witness["post_pause_inventory_signature_sha256"]
        if initial_signature != expected_signature:
            raise D43MigrationError("continuation_source_inventory_mismatch")

        deadline = time.monotonic() + STABILITY_SECONDS
        last_lsof_check = time.monotonic()
        while time.monotonic() < deadline:
            loaded_now, pid_now = _launchctl_list()
            if not loaded_now or pid_now != "-" or not _disabled_state():
                raise D43MigrationError("continuation_d43_state_changed")
            if time.monotonic() - last_lsof_check >= 15:
                if _lsof_writers(SOURCE_ROOT):
                    raise D43MigrationError("continuation_source_writer_during_stability")
                last_lsof_check = time.monotonic()
            time.sleep(min(2, max(0.0, deadline - time.monotonic())))
        final_root = _root_identity(SOURCE_ROOT)
        if (final_root.st_dev, final_root.st_ino) != (source_device, source_inode):
            raise D43MigrationError("continuation_source_root_changed")
        final_signature = _inventory_signature(_tree_members(SOURCE_ROOT))
        if final_signature != expected_signature or _lsof_writers(SOURCE_ROOT):
            raise D43MigrationError("continuation_source_not_stable")
        loaded_final, pid_final = _launchctl_list()
        if not loaded_final or pid_final != "-" or not _disabled_state():
            raise D43MigrationError("continuation_d43_state_changed_after_stability")
        if _plist_identity()[0] != PLIST_SHA256_PIN:
            raise D43MigrationError("continuation_plist_changed")
        base._assert_held_target_identity(Path(pin.mount_point), target_fd, pin)
    finally:
        os.close(prior_fd)

    observed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    continued_witness = {
        **prior_witness,
        "continued_from_candidate_id": prior_candidate_id,
        "continuation_stable_interval_seconds": STABILITY_SECONDS,
        "continuation_inventory_signature_sha256": final_signature,
        "continuation_observed_at_utc": observed_at,
    }
    record = {
        "schema": SCHEMA,
        "state": "continued_existing_pause",
        "prior_candidate_id": prior_candidate_id,
        "pause_intent_sha256": hashlib.sha256(intent_raw).hexdigest(),
        "pause_started_sha256": hashlib.sha256(started_raw).hexdigest(),
        "prior_pause_witness_sha256": hashlib.sha256(witness_raw).hexdigest(),
        "source_inventory_signature_sha256": final_signature,
        "revalidated_stable_interval_seconds": STABILITY_SECONDS,
        "revalidated_at_utc": observed_at,
        "automatic_resume": False,
    }
    return {
        "intent": intent,
        "started": started,
        "witness": continued_witness,
        "record": record,
    }


def _verify_candidate_metadata(
    candidate_fd: int,
    target_fd: int,
    candidate_id: str,
    pin: base.TargetPin,
    header: dict[str, Any],
    manifest_sha256: str,
) -> None:
    expected_names = {
        "CAPTURE_STARTED.json",
        "PAUSE_INTENT.json",
        "PAUSE_STARTED.json",
        "PAUSE_WITNESS.json",
        "CAPTURE_COMPLETE.json",
        "MANIFEST.jsonl",
        "payload",
    }
    if header.get("pause_mode") == "continued_existing_pause":
        expected_names.add("PAUSE_CONTINUATION.json")
    if set(os.listdir(candidate_fd)) != expected_names:
        raise D43MigrationError("candidate_top_level_membership_mismatch")
    started = _read_json_at(candidate_fd, "CAPTURE_STARTED.json")
    if (
        started.get("schema") != SCHEMA
        or started.get("candidate_id") != candidate_id
        or started.get("state") != "incomplete_until_manifest_published"
        or started.get("target_volume_uuid") != pin.volume_uuid
    ):
        raise D43MigrationError("candidate_start_witness_invalid")
    pause_intent = _read_json_at(candidate_fd, "PAUSE_INTENT.json")
    intent_sha256 = hashlib.sha256(_canonical(pause_intent)).hexdigest()
    if (
        pause_intent.get("schema") != SCHEMA
        or pause_intent.get("label") != LABEL
        or pause_intent.get("prior_loaded") is not True
        or pause_intent.get("prior_pid") != "-"
        or pause_intent.get("prior_disabled_override") is not False
        or pause_intent.get("disable_requested") is not True
        or pause_intent.get("plist_sha256") != PLIST_SHA256_PIN
        or pause_intent.get("source_path") != str(SOURCE_ROOT)
        or pause_intent.get("source_device") != header.get("source_root_device")
        or pause_intent.get("source_inode") != header.get("source_root_inode")
        or pause_intent.get("automatic_resume") is not False
    ):
        raise D43MigrationError("candidate_pause_intent_invalid")
    pause_started = _read_json_at(candidate_fd, "PAUSE_STARTED.json")
    if (
        pause_started.get("schema") != SCHEMA
        or pause_started.get("label") != LABEL
        or pause_started.get("prior_loaded") is not True
        or pause_started.get("prior_pid") != "-"
        or pause_started.get("prior_disabled_override") is not False
        or pause_started.get("disable_requested") is not True
        or pause_started.get("plist_sha256") != PLIST_SHA256_PIN
        or pause_started.get("source_path") != str(SOURCE_ROOT)
        or pause_started.get("source_device") != header.get("source_root_device")
        or pause_started.get("source_inode") != header.get("source_root_inode")
        or pause_started.get("automatic_resume") is not False
        or pause_started.get("pause_intent_sha256") != intent_sha256
    ):
        raise D43MigrationError("candidate_pause_start_witness_invalid")
    pause_witness = _read_json_at(candidate_fd, "PAUSE_WITNESS.json")
    if pause_witness != header.get("pause_witness"):
        raise D43MigrationError("candidate_pause_witness_mismatch")
    if pause_witness.get("pause_intent_sha256") != intent_sha256:
        raise D43MigrationError("candidate_pause_intent_join_invalid")
    if header.get("pause_mode") == "continued_existing_pause":
        record = _read_json_at(candidate_fd, "PAUSE_CONTINUATION.json")
        if record != header.get("pause_continuation"):
            raise D43MigrationError("candidate_pause_continuation_mismatch")
        prior_candidate_id = record.get("prior_candidate_id")
        if (
            not isinstance(prior_candidate_id, str)
            or not base.ID_RE.fullmatch(prior_candidate_id)
            or prior_candidate_id == candidate_id
            or record.get("schema") != SCHEMA
            or record.get("state") != "continued_existing_pause"
            or record.get("automatic_resume") is not False
        ):
            raise D43MigrationError("candidate_pause_continuation_invalid")
        prior_fd = _open_candidate_fd(target_fd, prior_candidate_id)
        try:
            if set(os.listdir(prior_fd)) != {
                "CAPTURE_STARTED.json",
                "PAUSE_INTENT.json",
                "PAUSE_STARTED.json",
                "PAUSE_WITNESS.json",
                "payload",
            }:
                raise D43MigrationError("continuation_prior_candidate_not_incomplete")
            _prior_started = _read_json_at(prior_fd, "CAPTURE_STARTED.json")
            _prior_intent, intent_raw = _read_json_hashed_at(prior_fd, "PAUSE_INTENT.json")
            _prior_pause_started, pause_started_raw = _read_json_hashed_at(
                prior_fd, "PAUSE_STARTED.json"
            )
            _prior_pause_witness, pause_witness_raw = _read_json_hashed_at(
                prior_fd, "PAUSE_WITNESS.json"
            )
            hashes = (
                hashlib.sha256(intent_raw).hexdigest(),
                hashlib.sha256(pause_started_raw).hexdigest(),
                hashlib.sha256(pause_witness_raw).hexdigest(),
            )
            expected_hashes = (
                record.get("pause_intent_sha256"),
                record.get("pause_started_sha256"),
                record.get("prior_pause_witness_sha256"),
            )
            if hashes != expected_hashes:
                raise D43MigrationError("continuation_prior_pause_digest_mismatch")
        finally:
            os.close(prior_fd)
    elif "PAUSE_CONTINUATION.json" in os.listdir(candidate_fd):
        raise D43MigrationError("unexpected_pause_continuation_record")
    seal = _read_json_at(candidate_fd, "CAPTURE_COMPLETE.json")
    if (
        seal.get("schema") != SCHEMA
        or seal.get("state") != "capture_readback_verified"
        or seal.get("candidate_id") != candidate_id
        or seal.get("target_volume_uuid") != pin.volume_uuid
        or seal.get("target_device_identifier") != pin.device_identifier
        or seal.get("candidate_device") != header.get("candidate_device")
        or seal.get("candidate_inode") != header.get("candidate_inode")
        or seal.get("manifest_sha256") != manifest_sha256
        or seal.get("member_count") != header.get("member_count")
        or seal.get("payload_bytes") != header.get("payload_bytes")
        or seal.get("start_or_activation") is not False
    ):
        raise D43MigrationError("candidate_completion_seal_invalid")


def _verify_candidate_payload(
    payload_fd: int,
    rows: list[dict[str, Any]],
    header: dict[str, Any],
    candidate_id: str,
    pin: base.TargetPin,
    candidate_stat: os.stat_result,
) -> int:
    if (
        header.get("candidate_id") != candidate_id
        or header.get("target_volume_uuid") != pin.volume_uuid
        or header.get("target_device_identifier") != pin.device_identifier
        or header.get("candidate_device") != candidate_stat.st_dev
        or header.get("candidate_inode") != candidate_stat.st_ino
    ):
        raise D43MigrationError("manifest_target_or_candidate_identity_mismatch")
    count = _verify_payload(payload_fd, rows)
    expected = {(row["path"], row["type"]) for row in rows}
    actual = {(row["path"], row["type"]) for row in _tree_members_from_fd(payload_fd)}
    if actual != expected or count != header.get("member_count"):
        raise D43MigrationError("candidate_membership_or_count_mismatch")
    return count


def _validate_restore_devices(
    parent_stat: os.stat_result, target_stat: os.stat_result, home_device: int
) -> None:
    if (
        parent_stat.st_uid != os.geteuid()
        or parent_stat.st_dev != home_device
        or parent_stat.st_dev == target_stat.st_dev
    ):
        raise D43MigrationError("quarantine_parent_identity_invalid")


def _tree_members_from_fd(root_fd: int) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []

    def visit(fd: int, prefix: str) -> None:
        for name in sorted(os.listdir(fd)):
            relative = name if not prefix else f"{prefix}/{name}"
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            kind = base._entry_kind(st.st_mode)
            result.append({"path": relative, "type": kind})
            if kind == "directory":
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    visit(child, relative)
                finally:
                    os.close(child)

    visit(root_fd, "")
    return result


def restore_d43_inert(
    *, target_root: Path, candidate_id: str, expected_manifest_sha256: str, quarantine_root: Path
) -> dict[str, Any]:
    home = Path.home()
    try:
        relative = quarantine_root.relative_to(home)
    except ValueError as exc:
        raise D43MigrationError("quarantine_must_be_under_user_home") from exc
    if not quarantine_root.is_absolute() or not relative.parts or ".." in relative.parts:
        raise D43MigrationError("quarantine_path_invalid")
    target_fd, pin = base._validate_target(target_root)
    candidate_fd = -1
    payload_fd = -1
    home_fd = -1
    parent_fd = -1
    quarantine_fd = -1
    try:
        candidate_fd = _open_candidate_fd(target_fd, candidate_id)
        header, rows = _read_manifest_at(candidate_fd, expected_manifest_sha256)
        _verify_candidate_metadata(
            candidate_fd, target_fd, candidate_id, pin, header, expected_manifest_sha256
        )
        candidate_stat = os.fstat(candidate_fd)
        payload_fd = os.open(
            "payload", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=candidate_fd
        )
        _verify_candidate_payload(payload_fd, rows, header, candidate_id, pin, candidate_stat)
        home_fd, parent_fd, home_device = base._open_home_parent(home, quarantine_root.parent)
        parent_stat = os.fstat(parent_fd)
        target_stat = os.fstat(target_fd)
        _validate_restore_devices(parent_stat, target_stat, home_device)
        required_free = int(header["payload_bytes"]) + base.RESTORE_OPERATION_RESERVE_BYTES
        if base._free_bytes(parent_fd) < required_free:
            raise D43MigrationError("quarantine_capacity_reserve_failed")
        try:
            os.stat(quarantine_root.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise D43MigrationError("quarantine_must_be_new")
        quarantine_fd = base._mkdir_open(parent_fd, quarantine_root.name)
        qst = os.fstat(quarantine_fd)
        if qst.st_dev != home_device or stat.S_IMODE(qst.st_mode) != 0o700:
            raise D43MigrationError("quarantine_root_identity_invalid")
        os.close(parent_fd)
        parent_fd = -1
        os.close(home_fd)
        home_fd = -1
        restored_fd = base._mkdir_open(quarantine_fd, "payload")
        try:
            for row in rows:
                parts = _safe_parts(row["path"])
                parent_fd = _mkdir_path(restored_fd, parts[:-1])
                try:
                    leaf = parts[-1]
                    source_parent = _open_path(payload_fd, parts[:-1])
                    try:
                        if row["type"] == "directory":
                            child_fd = _ensure_directory(parent_fd, leaf)
                            os.close(child_fd)
                        elif row["type"] == "symlink":
                            link_target = os.readlink(leaf, dir_fd=source_parent)
                            os.symlink(link_target, leaf, dir_fd=parent_fd)
                        else:
                            src_fd = os.open(
                                leaf,
                                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                dir_fd=source_parent,
                            )
                            dst_fd = os.open(
                                leaf,
                                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                0o600,
                                dir_fd=parent_fd,
                            )
                            try:
                                while block := os.read(src_fd, CHUNK):
                                    view = memoryview(block)
                                    while view:
                                        written = os.write(dst_fd, view)
                                        if written <= 0:
                                            raise D43MigrationError("restore_write_stalled")
                                        view = view[written:]
                                os.fsync(dst_fd)
                            finally:
                                os.close(src_fd)
                                os.close(dst_fd)
                    finally:
                        os.close(source_parent)
                finally:
                    os.close(parent_fd)
                    parent_fd = -1
            restored_payload_fd = os.open(
                "payload", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=quarantine_fd
            )
            try:
                count = _verify_payload(restored_payload_fd, rows)
                expected = {(row["path"], row["type"]) for row in rows}
                actual = {
                    (row["path"], row["type"]) for row in _tree_members_from_fd(restored_payload_fd)
                }
                if actual != expected:
                    raise D43MigrationError("restore_membership_mismatch")
            finally:
                os.close(restored_payload_fd)
            os.fsync(quarantine_fd)
            base._fsync_directory(restored_fd)
        finally:
            os.close(restored_fd)
    finally:
        if payload_fd >= 0:
            os.close(payload_fd)
        if candidate_fd >= 0:
            os.close(candidate_fd)
        if parent_fd >= 0:
            os.close(parent_fd)
        if home_fd >= 0:
            os.close(home_fd)
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        try:
            base._assert_held_target_identity(target_root, target_fd, pin)
        finally:
            os.close(target_fd)
    return {
        "candidate_id": candidate_id,
        "manifest_sha256": expected_manifest_sha256,
        "member_count": count,
        "state": "protected_opaque_inert_quarantine_verified",
        "protected_semantics_used": False,
        "start_or_activation": False,
        "role": header["role"],
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser(
        "capture", help="pause only the exact D43 label and create opaque custody"
    )
    capture.add_argument("--target-root", type=Path, required=True)
    capture.add_argument("--candidate-id")
    capture.add_argument(
        "--paused-candidate-id",
        help="continue from a pinned incomplete pause witness without reenabling D43",
    )
    verify = sub.add_parser(
        "verify", help="verify protected opaque candidate without semantic access"
    )
    verify.add_argument("--target-root", type=Path, required=True)
    verify.add_argument("--candidate-id", required=True)
    verify.add_argument("--manifest-sha256", required=True)
    restore = sub.add_parser(
        "restore-inert", help="restore protected bytes to a new inert quarantine"
    )
    restore.add_argument("--target-root", type=Path, required=True)
    restore.add_argument("--candidate-id", required=True)
    restore.add_argument("--manifest-sha256", required=True)
    restore.add_argument("--quarantine-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            result = capture_d43(
                target_root=args.target_root,
                candidate_id=args.candidate_id,
                paused_candidate_id=args.paused_candidate_id,
            )
        elif args.command == "verify":
            result = verify_d43(
                target_root=args.target_root,
                candidate_id=args.candidate_id,
                expected_manifest_sha256=args.manifest_sha256,
            )
        else:
            result = restore_d43_inert(
                target_root=args.target_root,
                candidate_id=args.candidate_id,
                expected_manifest_sha256=args.manifest_sha256,
                quarantine_root=args.quarantine_root,
            )
    except (D43MigrationError, base.MigrationError, OSError) as exc:
        error = str(exc) if isinstance(exc, D43MigrationError) else "underlying_operation_failed"
        print(
            json.dumps(
                {
                    "state": "failed_closed",
                    "error": error,
                    "candidate_accepted": False,
                    "automatic_resume": False,
                    "operator_action": (
                        "inspect exact D43 label and PAUSE_INTENT/PAUSE_STARTED/PAUSE_WITNESS "
                        "before any manual service decision"
                    ),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        print(
            json.dumps(
                {
                    "state": "failed_closed",
                    "error": "operator_interrupted",
                    "candidate_accepted": False,
                    "automatic_resume": False,
                    "operator_action": (
                        "inspect exact D43 label and PAUSE_INTENT/PAUSE_STARTED/PAUSE_WITNESS "
                        "before any manual service decision"
                    ),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 130
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
