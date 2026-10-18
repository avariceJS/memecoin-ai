#!/usr/bin/env python3
"""Write a bounded local health snapshot for the configured research volume."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import sys
import time
from pathlib import Path
from typing import Any

from memecoin_research_storage import StorageConfig, StorageError, VolumeProbe, verify_root

STATUS_NAME = "status.json"
LOCK_NAME = ".lock"
TEMP_NAME = ".status.tmp"
MAX_STATUS_BYTES = 4096


def _plain_absolute(path: Path, label: str) -> Path:
    if not path.is_absolute() or "\x00" in str(path) or ".." in path.parts:
        raise StorageError(f"invalid_{label}_path")
    normalized = Path(os.path.abspath(path))
    cursor = Path(normalized.anchor)
    for part in normalized.parts[1:]:
        cursor /= part
        try:
            if stat.S_ISLNK(cursor.lstat().st_mode):
                raise StorageError(f"symlink_{label}_path")
        except FileNotFoundError:
            pass
    return normalized


def _validate_state_dir(state_dir: Path, home_dir: Path) -> Path:
    state_dir = _plain_absolute(state_dir, "state_dir")
    home = _plain_absolute(home_dir, "home")
    try:
        state_dir.relative_to(home)
    except ValueError as exc:
        raise StorageError("state_directory_must_be_inside_home") from exc
    if not state_dir.is_dir():
        raise StorageError("state_directory_missing")
    st = state_dir.lstat()
    home_st = home.stat()
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o700
        or st.st_dev != home_st.st_dev
    ):
        raise StorageError("unsafe_internal_state_directory")
    return state_dir


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, StorageError):
        candidate = str(exc).split(":", 1)[0]
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", candidate):
            return candidate
    if isinstance(exc, FileNotFoundError):
        return "file_not_found"
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, OSError):
        return "storage_io_error"
    return "storage_check_failed"


def _open_private_file(dir_fd: int, name: str, *, create: bool) -> int:
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    if create:
        flags |= os.O_CREAT
    try:
        fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        raise StorageError("state_file_open_failed") from exc
    st = os.fstat(fd)
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o600
        or st.st_nlink != 1
    ):
        os.close(fd)
        raise StorageError("unsafe_state_file")
    return fd


def _read_previous(dir_fd: int) -> dict[str, Any] | None:
    try:
        fd = os.open(STATUS_NAME, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StorageError("unsafe_status_file") from exc
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600
            or st.st_nlink != 1
            or st.st_size > MAX_STATUS_BYTES
        ):
            raise StorageError("unsafe_status_file")
        chunks: list[bytes] = []
        while chunk := os.read(fd, MAX_STATUS_BYTES + 1):
            chunks.append(chunk)
        payload = b"".join(chunks)
        if len(payload) > MAX_STATUS_BYTES:
            raise StorageError("status_file_too_large")
        try:
            value = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return None
        if (
            isinstance(value, dict)
            and value.get("schema_version") == 1
            and value.get("status") in {"ready", "storage_unavailable"}
        ):
            return value
        return None
    finally:
        os.close(fd)


def _write_status(
    state_dir: Path,
    payload: dict[str, Any],
    expected_directory: tuple[int, int],
) -> None:
    directory_fd = os.open(
        state_dir,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        directory_stat = os.fstat(directory_fd)
        if (
            (directory_stat.st_dev, directory_stat.st_ino) != expected_directory
            or directory_stat.st_uid != os.getuid()
            or stat.S_IMODE(directory_stat.st_mode) != 0o700
        ):
            raise StorageError("state_directory_replaced")
        # A dedicated directory has at most the current status, stable lock, and a
        # temporary file left by an interrupted atomic replacement.
        unexpected = set(os.listdir(directory_fd)) - {STATUS_NAME, LOCK_NAME, TEMP_NAME}
        if unexpected:
            raise StorageError("unexpected_state_directory_entry")
        lock_fd = _open_private_file(directory_fd, LOCK_NAME, create=True)
        try:
            os.fsync(directory_fd)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            previous = _read_previous(directory_fd)
            if previous:
                payload["previous_status"] = previous.get("status")
                prev = previous.get("status")
                current = payload.get("status")
                if prev != current:
                    payload["transition"] = f"{prev}_to_{current}"
            encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
            if len(encoded) > MAX_STATUS_BYTES:
                raise StorageError("status_payload_too_large")
            try:
                os.unlink(TEMP_NAME, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except FileNotFoundError:
                pass
            temp_fd = os.open(
                TEMP_NAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=directory_fd,
            )
            try:
                os.fchmod(temp_fd, 0o600)
                view = memoryview(encoded)
                while view:
                    written = os.write(temp_fd, view)
                    if written <= 0:
                        raise OSError("short status write")
                    view = view[written:]
                os.fsync(temp_fd)
            finally:
                os.close(temp_fd)
            os.replace(
                TEMP_NAME,
                STATUS_NAME,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
    finally:
        os.close(directory_fd)


def check_storage_health(
    config_path: Path,
    state_dir: Path,
    *,
    probe: VolumeProbe | None = None,
    home_dir: Path | None = None,
    checked_at_ns: int | None = None,
) -> dict[str, Any]:
    """Revalidate storage read-only and atomically publish one bounded status."""
    state = _validate_state_dir(state_dir, home_dir or Path.home())
    state_stat = state.lstat()
    expected_directory = (state_stat.st_dev, state_stat.st_ino)
    payload: dict[str, Any] = {"schema_version": 1}
    try:
        config = StorageConfig.load(config_path)
    except Exception as exc:
        payload.update({"status": "storage_unavailable", "error": _safe_error(exc)})
        payload["checked_at_ns"] = checked_at_ns if checked_at_ns is not None else time.time_ns()
        _write_status(state, payload, expected_directory)
        return payload
    mount = _plain_absolute(config.mount_path, "mount")
    try:
        state.relative_to(mount)
    except ValueError:
        pass
    else:
        raise StorageError("state_directory_inside_research_volume")
    try:
        resolved = verify_root(config_path, probe=probe)
        payload.update(
            {
                "status": "ready",
                "root_id": resolved.root_id,
                "volume_uuid": resolved.volume_uuid,
                "mount_path": str(resolved.mount_path),
                "data_root": str(resolved.path),
                "available_bytes": resolved.available_bytes,
            }
        )
    except Exception as exc:
        payload.update({"status": "storage_unavailable", "error": _safe_error(exc)})
    payload["checked_at_ns"] = checked_at_ns if checked_at_ns is not None else time.time_ns()
    _write_status(state, payload, expected_directory)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = check_storage_health(args.config, args.state_dir)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "ready" else 1
    except (StorageError, OSError) as exc:
        print(f"research storage health write failed: {_safe_error(exc)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
