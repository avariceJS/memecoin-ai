#!/usr/bin/env python3
"""Create a bounded read-only SQLite online backup in disposable local storage.

This helper is for explicitly classified non-protected databases. It never
initializes, checkpoints, vacuums, or changes the source database. In WAL mode,
SQLite may update transient shared-memory coordination bytes while a reader is
open; source logical/durable writes are not initiated by this helper.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import stat
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class SQLiteBackupError(ValueError):
    """A requested source is unsafe, outside scope, or did not back up cleanly."""


def _signature(path: Path) -> dict[str, int] | None:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode):
        raise SQLiteBackupError("source_family_member_not_regular")
    return {
        "device": value.st_dev,
        "inode": value.st_ino,
        "mode": stat.S_IMODE(value.st_mode),
        "uid": value.st_uid,
        "gid": value.st_gid,
        "size_bytes": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }


def _family_signatures(source: Path) -> dict[str, dict[str, int] | None]:
    return {
        suffix: _signature(Path(f"{source}{suffix}")) for suffix in ("", "-wal", "-shm", "-journal")
    }


def _validate_scope(source: Path, allowed_root: Path, protected_roots: Sequence[Path]) -> Path:
    try:
        root_stat = allowed_root.lstat()
        source_stat = source.lstat()
    except OSError as exc:
        raise SQLiteBackupError("source_or_scope_unavailable") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise SQLiteBackupError("allowed_root_not_directory")
    if not stat.S_ISREG(source_stat.st_mode) or stat.S_ISLNK(source_stat.st_mode):
        raise SQLiteBackupError("source_not_regular_file")
    resolved_root = allowed_root.resolve(strict=True)
    resolved_source = source.resolve(strict=True)
    if resolved_root != allowed_root or resolved_source != source:
        raise SQLiteBackupError("source_or_scope_parent_symlink")
    if resolved_root not in resolved_source.parents:
        raise SQLiteBackupError("source_outside_declared_root")
    for protected in protected_roots:
        try:
            protected_root = protected.resolve(strict=False)
        except OSError as exc:
            raise SQLiteBackupError("protected_scope_invalid") from exc
        if resolved_source == protected_root or protected_root in resolved_source.parents:
            raise SQLiteBackupError("protected_source_forbidden")
    if source.suffix.lower() not in {".sqlite", ".sqlite3", ".db", ".db3"}:
        raise SQLiteBackupError("source_extension_not_declared_sqlite")
    return resolved_source


def _uri(path: Path) -> str:
    return f"{path.as_uri()}?mode=ro"


def backup_read_only(
    source: Path,
    destination: Path,
    *,
    allowed_root: Path,
    protected_roots: Sequence[Path] = (),
    logical_id: str,
    timeout_seconds: float = 300.0,
    busy_timeout_ms: int = 1000,
    pages_per_step: int = 128,
    sleep_seconds: float = 0.01,
    max_output_bytes: int = 8 * 1024**3,
) -> dict[str, Any]:
    """Back up a classified source DB to a new disposable local SQLite file."""
    if not logical_id or len(logical_id) > 128 or any(ord(c) < 32 for c in logical_id):
        raise SQLiteBackupError("logical_identity_invalid")
    if timeout_seconds <= 0 or timeout_seconds > 3600:
        raise SQLiteBackupError("deadline_out_of_bounds")
    if not 1 <= busy_timeout_ms <= 60_000 or not 1 <= pages_per_step <= 4096:
        raise SQLiteBackupError("sqlite_bounds_invalid")
    if sleep_seconds < 0 or sleep_seconds > 1:
        raise SQLiteBackupError("sleep_out_of_bounds")
    if not 1 <= max_output_bytes <= 16 * 1024**3:
        raise SQLiteBackupError("output_limit_out_of_bounds")

    resolved_source = _validate_scope(source, allowed_root, protected_roots)
    destination = Path(os.path.abspath(destination))
    if destination.exists() or destination.is_symlink():
        raise SQLiteBackupError("destination_must_be_absent")
    parent = destination.parent
    try:
        parent_stat = parent.lstat()
    except OSError as exc:
        raise SQLiteBackupError("destination_parent_unavailable") from exc
    if not stat.S_ISDIR(parent_stat.st_mode) or stat.S_ISLNK(parent_stat.st_mode):
        raise SQLiteBackupError("destination_parent_not_directory")
    if parent.resolve(strict=True) != parent:
        raise SQLiteBackupError("destination_parent_symlink")
    if parent_stat.st_uid != os.getuid() or stat.S_IMODE(parent_stat.st_mode) != 0o700:
        raise SQLiteBackupError("destination_parent_not_private")
    if shutil.disk_usage(parent).free < min(max_output_bytes, 2 * 1024**3):
        raise SQLiteBackupError("destination_free_space_below_floor")

    start_utc = datetime.now(UTC).isoformat(timespec="microseconds")
    start = time.monotonic()
    family_before = _family_signatures(resolved_source)
    source_conn: sqlite3.Connection | None = None
    destination_conn: sqlite3.Connection | None = None
    try:
        destination_fd = os.open(
            destination,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        os.close(destination_fd)
        source_conn = sqlite3.connect(
            _uri(resolved_source),
            uri=True,
            timeout=busy_timeout_ms / 1000,
        )
        source_conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        # This is a read-only diagnostic. Do not change source journal/locking modes.
        schema_version = int(source_conn.execute("PRAGMA schema_version").fetchone()[0])
        destination_conn = sqlite3.connect(destination, timeout=busy_timeout_ms / 1000)
        os.chmod(destination, 0o600)

        def progress(_status: int, _remaining: int, _total: int) -> None:
            if time.monotonic() - start > timeout_seconds:
                raise TimeoutError("sqlite_backup_deadline")
            try:
                size = destination.stat().st_size
            except FileNotFoundError:
                size = 0
            if size > max_output_bytes:
                raise SQLiteBackupError("sqlite_backup_output_limit")

        source_conn.backup(
            destination_conn,
            pages=pages_per_step,
            progress=progress,
            sleep=sleep_seconds,
        )
        destination_conn.close()
        destination_conn = None
        source_conn.close()
        source_conn = None

        verify_conn = sqlite3.connect(_uri(destination), uri=True, timeout=busy_timeout_ms / 1000)
        try:

            def verify_deadline() -> int:
                return int(time.monotonic() - start > timeout_seconds)

            verify_conn.set_progress_handler(verify_deadline, 1000)
            check = verify_conn.execute("PRAGMA integrity_check").fetchone()
            if check != ("ok",):
                raise SQLiteBackupError("backup_integrity_check_failed")
            page_count = int(verify_conn.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(verify_conn.execute("PRAGMA page_size").fetchone()[0])
        finally:
            verify_conn.close()
        family_after = _family_signatures(resolved_source)
        if set(family_before) != set(family_after):
            raise SQLiteBackupError("source_family_membership_changed")
        # DB/WAL durable members may legitimately change due to live writers. Keep the
        # observations; never attribute those changes to this reader or declare them stable.
        shm_changed = family_before.get("-shm") != family_after.get("-shm")
        destination_stat = destination.stat()
        if destination_stat.st_size > max_output_bytes:
            raise SQLiteBackupError("sqlite_backup_output_limit")
        end_utc = datetime.now(UTC).isoformat(timespec="microseconds")
        return {
            "schema": "brain-host-sqlite-backup-v1",
            "logical_id": logical_id,
            "status": "complete",
            "source_path_recorded": False,
            "source_logical_writes_initiated": False,
            "source_durable_writes_initiated": False,
            "transient_shm_coordination_may_change": True,
            "source_shm_signature_changed": shm_changed,
            "rollback_journal_present_at_start": family_before["-journal"] is not None,
            "source_family_before": family_before,
            "source_family_after": family_after,
            "source_changes_attributed": False,
            "source_schema_version": schema_version,
            "backup_page_count": page_count,
            "backup_page_size": page_size,
            "backup_size_bytes": destination_stat.st_size,
            "backup_integrity": "ok",
            "started_at_utc": start_utc,
            "completed_at_utc": end_utc,
            "elapsed_seconds": round(time.monotonic() - start, 6),
            "timeout_seconds": timeout_seconds,
        }
    except BaseException:
        if destination_conn is not None:
            destination_conn.close()
        if source_conn is not None:
            source_conn.close()
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--allowed-root", type=Path, required=True)
    parser.add_argument("--protected-root", type=Path, action="append", default=[])
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--logical-id", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--busy-timeout-ms", type=int, default=1000)
    parser.add_argument("--pages-per-step", type=int, default=128)
    parser.add_argument("--max-output-bytes", type=int, default=8 * 1024**3)
    args = parser.parse_args(argv)
    try:
        result = backup_read_only(
            args.source,
            args.destination,
            allowed_root=args.allowed_root,
            protected_roots=args.protected_root,
            logical_id=args.logical_id,
            timeout_seconds=args.timeout_seconds,
            busy_timeout_ms=args.busy_timeout_ms,
            pages_per_step=args.pages_per_step,
            max_output_bytes=args.max_output_bytes,
        )
    except (OSError, sqlite3.Error, SQLiteBackupError, TimeoutError) as exc:
        print(f"brain-host-sqlite-backup: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
