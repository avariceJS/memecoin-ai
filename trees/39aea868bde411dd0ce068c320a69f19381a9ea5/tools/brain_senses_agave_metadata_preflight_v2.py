#!/usr/bin/env python3
"""Run one pinned, offline Cargo metadata resolution under the Kingston guard.

This wrapper performs metadata resolution only. It cannot fetch, build, test,
sign, or run ProgramTest. The caller must supply the SHA-256 of the separately
copied Cargo cache manifest approved for this exact stage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SOURCE_ROOT = str(Path(__file__).resolve().parents[1] / "src")
if _SOURCE_ROOT not in sys.path:
    sys.path.insert(0, _SOURCE_ROOT)

from brain_senses_agave_dependency_fetch_v1 import (  # noqa: E402
    EXPECTED_AGAVE_HEAD,
    EXPECTED_COMPOSITE_DIFF,
    EXPECTED_GIT_SOURCE,
    EXPECTED_POST_V5_LOCK,
    EXPECTED_REGISTRY_SOURCE,
    _git_snapshot,
    _locked_sources,
    _sha256,
    _source_configuration,
    _source_revision,
)
from brain_senses_build_storage_guard import (  # noqa: E402
    ALLOCATION_CAP_BYTES,
    INITIAL_FREE_BYTES,
    MIN_FREE_BYTES,
    POLL_SECONDS,
    PROBE_TIMEOUT_SECONDS,
    RUST_VERSION,
    BoundStorageSnapshot,
    SnapshotProvider,
    _allocated_bytes,
    _bounded_inspect_stage,
    _build_environment,
    _check_stage_paths,
    _check_unprivileged_root1,
    _plain_descendant,
    _terminate_group,
)

TARGET_TRIPLE = "aarch64-apple-darwin"
WORKSPACE_MEMBER_COUNT = 1_075
EXPECTED_RESOLVED_TUPLE_COUNT = 960
EXPECTED_LOCK_ONLY_TUPLE_COUNT = 115
EXPECTED_SOURCE_NULL_WORKSPACE_MEMBERS = 140
MAX_WALL_SECONDS = 10 * 60
MAX_STDOUT_BYTES = 64 * 1024**2
MAX_STDERR_BYTES = 2 * 1024**2
MAX_TARGET_PROBE_BYTES = 16 * 1024
MAX_CACHE_COPY_BYTES = 8 * 1024**3
MAX_CACHE_COPY_ENTRIES = 250_000
MAX_CACHE_MANIFEST_BYTES = 12 * 1024**3
MAX_DIAGNOSTIC_CHARS = 4_096
FIXED_PATH = "/Users/root1/.cargo/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|bearer|token|password|secret|api[_-]?key)\b\s*[:=]\s*[^\s,;]+"
)
_URL_CREDENTIALS = re.compile(r"(?i)(https?://)[^/@\s]+:[^/@\s]+@")
_HOME_PATH = re.compile(r"/Users/[^/\s]+")
_MOUNT_PATH = re.compile(r"/Volumes/[^/\s]+(?:/[^\s,:;]+)*")
_PRIVATE_TEMP_PATH = re.compile(r"/private/var/[^\s,:;]+")
_LOCAL_PACKAGE_ID = re.compile(r"(?i)(path\+file://)([^#]+)(#)")
METADATA_STAGE_RELATIVE = Path("qualifications/agave-v3.0.0-token2022-metadata-v2-20261012")
_CROSSBEAM_CACHE = "crossbeam-5268678e7223f484"
_CROSSBEAM_CHECKOUT = "fd279d7"


@dataclass(frozen=True)
class CapturedStream:
    raw_sha256: str
    byte_count: int
    retained: bytes
    truncated: bool
    read_error: bool
    complete: bool = True


@dataclass(frozen=True)
class MetadataResult:
    command: tuple[str, ...]
    status: str
    storage_root_id: str
    volume_uuid: str
    initial_available_bytes: int
    available_bytes: int
    stage_allocated_bytes: int
    source_head: str
    source_diff_sha256: str
    source_status_sha256: str
    cargo_lock_sha256: str
    cache_source_manifest_sha256: str
    cache_manifest_sha256: str
    cache_after_manifest_sha256: str
    cache_extraction_delta_sha256: str
    cache_copy_entries: int
    cache_copy_bytes: int
    rust_toolchain: str
    target_triple: str
    default_features: bool
    feature_intent: str
    metadata_stdout_sha256: str
    metadata_stdout_bytes: int
    stderr_sha256: str
    stderr_bytes: int
    stderr_sanitized: str
    graph_sha256: str
    graph: dict[str, Any]
    lock_tuple_count: int
    graph_tuple_count: int
    lock_only_tuple_count: int
    source_null_workspace_member_count: int
    target_probe_path: str | None
    target_probe_sha256: str | None
    process_receipt_path: str
    process_receipt_sha256: str
    graph_receipt_path: str
    graph_receipt_sha256: str
    elapsed_seconds: float


class MetadataPreflightError(RuntimeError):
    """Sanitized failure with bounded output metadata, never raw argv or streams."""

    def __init__(
        self,
        code: str,
        *,
        stdout: CapturedStream | None = None,
        stderr: CapturedStream | None = None,
    ) -> None:
        self.code = code
        self.stdout_sha256 = stdout.raw_sha256 if stdout else None
        self.stdout_bytes = stdout.byte_count if stdout else 0
        self.stderr_sha256 = stderr.raw_sha256 if stderr else None
        self.stderr_bytes = stderr.byte_count if stderr else 0
        self.stderr_sanitized = _sanitize(stderr.retained) if stderr else ""
        super().__init__(code)


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def _strict_json(raw: bytes, code: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise MetadataPreflightError(f"{code}_duplicate_key")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=object_pairs)
    except MetadataPreflightError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetadataPreflightError(f"{code}_invalid_json") from exc


def _sanitize(raw: bytes, roots: tuple[Path, ...] = ()) -> str:
    text = raw.decode("utf-8", errors="replace")
    for root in roots:
        text = text.replace(str(root), "<stage>")
    text = _PRIVATE_TEMP_PATH.sub("<temporary-path>", text)
    text = _MOUNT_PATH.sub("<storage-path>", text)
    text = _HOME_PATH.sub("<user-home>", text)
    text = _URL_CREDENTIALS.sub(r"\1<redacted>@", text)
    text = _SECRET_ASSIGNMENT.sub(r"\1=<redacted>", text)
    return text[:MAX_DIAGNOSTIC_CHARS]


def _hash_file_no_follow(path: Path, *, expected_device: int) -> str:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_dev != expected_device
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) & 0o022
            or not _pinned_crossbeam_pack_hardlink(path, before)
        ):
            raise MetadataPreflightError("cache_file_identity")
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
        after = os.fstat(fd)
        if total != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise MetadataPreflightError("cache_file_changed_during_hash")
        return digest.hexdigest()
    finally:
        os.close(fd)


def _pinned_crossbeam_pack_hardlink(path: Path, st: os.stat_result) -> bool:
    """Permit only the exact Cargo Git db/checkout pack hardlinks in the pinned cache."""
    if st.st_nlink == 1:
        return True
    filename = path.name
    if not filename.startswith("pack-") or path.suffix not in {".idx", ".pack"}:
        return False
    try:
        git_root = next(
            parent
            for parent in path.parents
            if parent.name == "git" and parent.parent.name == "cargo-home"
        )
    except StopIteration:
        return False
    cache_root = git_root.parent
    try:
        relative = path.relative_to(cache_root).parts
    except ValueError:
        return False
    db_path = (
        "git",
        "db",
        _CROSSBEAM_CACHE,
        "objects",
        "pack",
        filename,
    )
    checkout_path = (
        "git",
        "checkouts",
        _CROSSBEAM_CACHE,
        _CROSSBEAM_CHECKOUT,
        ".git",
        "objects",
        "pack",
        filename,
    )
    if relative == db_path:
        counterpart = cache_root.joinpath(*checkout_path)
    elif relative == checkout_path:
        counterpart = cache_root.joinpath(*db_path)
    else:
        return False
    try:
        other = counterpart.lstat()
    except OSError:
        return False
    return (
        st.st_nlink == 2
        and stat.S_ISREG(other.st_mode)
        and (st.st_dev, st.st_ino, st.st_nlink) == (other.st_dev, other.st_ino, other.st_nlink)
        and other.st_uid == os.getuid()
        and not (stat.S_IMODE(other.st_mode) & 0o022)
    )


def _tree_manifest_rows(root: Path, *, max_entries: int = 250_000) -> list[dict[str, Any]]:
    """Hash path, kind, mode, size, symlink target, and file bytes without following links."""
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise MetadataPreflightError("cache_root_missing") from exc
    if (
        stat.S_ISLNK(root_stat.st_mode)
        or not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_dev != root.parent.stat().st_dev
        or root_stat.st_uid != os.getuid()
        or stat.S_IMODE(root_stat.st_mode) & 0o022
    ):
        raise MetadataPreflightError("cache_root_identity")
    rows: list[dict[str, Any]] = []
    pending = [root]
    total_bytes = 0
    while pending:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name)
        except FileNotFoundError:
            continue
        for child in children:
            if len(rows) >= max_entries:
                raise MetadataPreflightError("cache_manifest_entry_cap")
            st = child.lstat()
            if (
                st.st_dev != root_stat.st_dev
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) & 0o022
            ):
                raise MetadataPreflightError("cache_entry_identity")
            relative = child.relative_to(root).as_posix()
            if len(Path(relative).parts) == 1 and child.name.lower() in {
                "credentials",
                "credentials.toml",
                ".credentials",
                ".credentials.toml",
                "config",
                "config.toml",
            }:
                raise MetadataPreflightError("cargo_home_private_config_forbidden")
            row: dict[str, Any] = {
                "path": relative,
                "mode": stat_mode(st.st_mode),
            }
            if child.is_symlink():
                row.update({"kind": "symlink", "target": os.readlink(child)})
            elif child.is_dir():
                row["kind"] = "directory"
                pending.append(child)
            elif child.is_file():
                total_bytes += st.st_size
                if total_bytes > MAX_CACHE_MANIFEST_BYTES:
                    raise MetadataPreflightError("cache_manifest_byte_cap")
                row.update(
                    {
                        "kind": "file",
                        "size": st.st_size,
                        "sha256": _hash_file_no_follow(child, expected_device=root_stat.st_dev),
                    }
                )
            else:
                raise MetadataPreflightError("cache_manifest_unsupported_entry")
            rows.append(row)
    rows.sort(key=lambda row: row["path"])
    return rows


def _tree_manifest(root: Path, *, max_entries: int = 250_000) -> str:
    return hashlib.sha256(
        _canonical(_tree_manifest_rows(root, max_entries=max_entries))
    ).hexdigest()


def stat_mode(mode: int) -> int:
    return mode & 0o7777


def _locked_cache_manifest(cache_home: Path) -> str:
    # The shared guard permits only the qualified Cargo Git checkout symlink form.
    # This tree hash then binds both link text and all in-root target bytes.
    return _tree_manifest(cache_home)


def _cache_delta(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    expected_registry_crates: set[str],
) -> list[dict[str, Any]]:
    before_by_path = {row["path"]: row for row in before}
    after_by_path = {row["path"]: row for row in after}
    delta: list[dict[str, Any]] = []
    total_bytes = 0
    for path in sorted(set(before_by_path) | set(after_by_path)):
        old, new = before_by_path.get(path), after_by_path.get(path)
        if old == new:
            continue
        # Cargo metadata may unpack cached crate archives into registry/src only.
        if path != "registry/src" and not path.startswith("registry/src/"):
            raise MetadataPreflightError("unexpected_cargo_cache_mutation")
        if old is not None:
            raise MetadataPreflightError("cargo_registry_source_rewrite")
        if new is None or new.get("kind") not in {"directory", "file"}:
            raise MetadataPreflightError("cargo_registry_source_entry_type")
        parts = Path(path).parts
        if len(parts) >= 4 and parts[3] not in expected_registry_crates:
            raise MetadataPreflightError("unexpected_registry_crate_extraction")
        if len(parts) > 1 and new.get("kind") != "directory" and len(parts) < 5:
            raise MetadataPreflightError("unexpected_registry_extraction_shape")
        total_bytes += int(new.get("size", 0))
        if len(delta) >= MAX_CACHE_COPY_ENTRIES or total_bytes > MAX_CACHE_COPY_BYTES:
            raise MetadataPreflightError("cache_extraction_delta_cap")
        delta.append(
            {
                "path": path,
                "change": "created",
                "kind": new["kind"],
                "size": new.get("size"),
                "sha256": new.get("sha256"),
            }
        )
    return delta


def _open_directory_beneath(root_fd: int, relative: Path, *, device: int) -> int:
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise MetadataPreflightError("cache_path_invalid")
    current = os.dup(root_fd)
    try:
        for part in relative.parts:
            child = os.open(
                part,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current,
            )
            st = os.fstat(child)
            if (
                not stat.S_ISDIR(st.st_mode)
                or st.st_dev != device
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) & 0o022
            ):
                os.close(child)
                raise MetadataPreflightError("cache_directory_type")
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _open_bound_data_root(snapshot: Any) -> int:
    """Open the configured data root through the verified mount descriptor."""
    try:
        relative_root = snapshot.root_path.relative_to(snapshot.mount_path)
    except ValueError as exc:
        raise MetadataPreflightError("storage_root_outside_mount") from exc
    if not relative_root.parts or any(part in {"", ".", ".."} for part in relative_root.parts):
        raise MetadataPreflightError("storage_root_relative_path_invalid")
    mount_fd = os.open(
        snapshot.mount_path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    root_fd = -1
    try:
        mount_stat = os.fstat(mount_fd)
        if (mount_stat.st_dev, mount_stat.st_ino) != (
            snapshot.mount_device,
            snapshot.mount_inode,
        ):
            raise MetadataPreflightError("storage_mount_descriptor_changed")
        root_fd = os.dup(mount_fd)
        for part in relative_root.parts:
            child_fd = os.open(
                part,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=root_fd,
            )
            child_stat = os.fstat(child_fd)
            if (
                not stat.S_ISDIR(child_stat.st_mode)
                or child_stat.st_dev != snapshot.mount_device
                or child_stat.st_uid != os.getuid()
                or stat.S_IMODE(child_stat.st_mode) & 0o077
            ):
                os.close(child_fd)
                raise MetadataPreflightError("storage_root_descriptor_unsafe")
            os.close(root_fd)
            root_fd = child_fd
        marker_fd = os.open(
            ".research-root.json",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        try:
            marker_stat = os.fstat(marker_fd)
            if (
                not stat.S_ISREG(marker_stat.st_mode)
                or marker_stat.st_dev != snapshot.mount_device
                or marker_stat.st_uid != os.getuid()
                or stat.S_IMODE(marker_stat.st_mode) & 0o077
                or marker_stat.st_size > 4096
            ):
                raise MetadataPreflightError("storage_root_marker_unsafe")
            marker_raw = os.read(marker_fd, 4097)
            if len(marker_raw) != marker_stat.st_size:
                raise MetadataPreflightError("storage_root_marker_changed")
        finally:
            os.close(marker_fd)
        marker = _strict_json(marker_raw, "storage_root_marker")
        if marker != {
            "data_root": relative_root.as_posix(),
            "root_id": snapshot.root_id,
            "version": 1,
            "volume_uuid": snapshot.volume_uuid,
        }:
            raise MetadataPreflightError("storage_root_marker_mismatch")
        return root_fd
    except BaseException:
        if root_fd >= 0:
            os.close(root_fd)
        raise
    finally:
        os.close(mount_fd)


def _relative_parts(relative: Path) -> tuple[str, ...]:
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} or "\x00" in part or "/" in part for part in relative.parts)
    ):
        raise MetadataPreflightError("artifact_relative_path_invalid")
    return relative.parts


def _open_or_create_private_dir(parent_fd: int, name: str, *, device: int) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    child_fd = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    st = os.fstat(child_fd)
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_dev != device
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o700
    ):
        os.close(child_fd)
        raise MetadataPreflightError("artifact_directory_identity")
    return child_fd


def _write_create_only_beneath_root(
    root_fd: int,
    relative: Path,
    payload: bytes,
    *,
    device: int,
    revalidate: Any,
    before_create: Any = None,
) -> str:
    """Create and fsync a private artifact using only the held data-root fd."""
    parts = _relative_parts(relative)
    root_st = os.fstat(root_fd)
    if root_st.st_dev != device or root_st.st_uid != os.getuid():
        raise MetadataPreflightError("artifact_root_identity")
    revalidate()
    current_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            child_fd = _open_or_create_private_dir(current_fd, component, device=device)
            os.close(current_fd)
            current_fd = child_fd
        revalidate()
        if before_create is not None:
            before_create()
        try:
            file_fd = os.open(
                parts[-1],
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=current_fd,
            )
        except FileExistsError as exc:
            raise MetadataPreflightError("artifact_already_exists") from exc
        try:
            st = os.fstat(file_fd)
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_dev != device
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o600
                or st.st_nlink != 1
            ):
                raise MetadataPreflightError("artifact_file_identity")
            remaining = memoryview(payload)
            while remaining:
                written = os.write(file_fd, remaining)
                if written <= 0:
                    raise MetadataPreflightError("artifact_write_incomplete")
                remaining = remaining[written:]
            os.fsync(file_fd)
            final_st = os.fstat(file_fd)
            if final_st.st_size != len(payload):
                raise MetadataPreflightError("artifact_size_mismatch")
        finally:
            os.close(file_fd)
        os.fsync(current_fd)
        revalidate()
    finally:
        os.close(current_fd)
    return hashlib.sha256(payload).hexdigest()


def _assert_same_storage(provider: SnapshotProvider, expected: Any) -> None:
    current = provider.inspect()
    if (
        current.root_id != expected.root_id
        or current.volume_uuid != expected.volume_uuid
        or current.mount_device != expected.mount_device
        or current.mount_inode != expected.mount_inode
        or current.root_path != expected.root_path
    ):
        raise MetadataPreflightError("artifact_storage_identity_changed")


def _copy_cache_tree(
    source_fd: int,
    destination_fd: int,
    *,
    device: int,
    limits: list[int],
) -> None:
    names = sorted(os.listdir(source_fd))
    for name in names:
        if name in {"", ".", ".."} or "/" in name or "\x00" in name:
            raise MetadataPreflightError("cache_entry_name")
        limits[0] += 1
        if limits[0] > MAX_CACHE_COPY_ENTRIES:
            raise MetadataPreflightError("cache_copy_entry_cap")
        st = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        if (
            st.st_dev != device
            or st.st_uid != os.getuid()
            or (not stat.S_ISLNK(st.st_mode) and stat.S_IMODE(st.st_mode) & 0o022)
        ):
            raise MetadataPreflightError("cache_source_entry_identity")
        if stat.S_ISLNK(st.st_mode):
            link_text = os.readlink(name, dir_fd=source_fd)
            if os.path.isabs(link_text):
                raise MetadataPreflightError("cache_absolute_symlink")
            os.symlink(link_text, name, dir_fd=destination_fd)
        elif stat.S_ISDIR(st.st_mode):
            os.mkdir(name, mode=st.st_mode & 0o777, dir_fd=destination_fd)
            source_child = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=source_fd,
            )
            destination_child = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=destination_fd,
            )
            try:
                _copy_cache_tree(
                    source_child,
                    destination_child,
                    device=device,
                    limits=limits,
                )
            finally:
                os.close(source_child)
                os.close(destination_child)
            os.fsync(destination_fd)
        elif stat.S_ISREG(st.st_mode):
            limits[1] += st.st_size
            if limits[1] > MAX_CACHE_COPY_BYTES:
                raise MetadataPreflightError("cache_copy_byte_cap")
            source_file = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=source_fd,
            )
            destination_file = -1
            try:
                source_stat = os.fstat(source_file)
                if (source_stat.st_dev, source_stat.st_ino) != (st.st_dev, st.st_ino):
                    raise MetadataPreflightError("cache_source_changed_during_copy")
                destination_file = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    st.st_mode & 0o777,
                    dir_fd=destination_fd,
                )
                while True:
                    chunk = os.read(source_file, 1024 * 1024)
                    if not chunk:
                        break
                    view = memoryview(chunk)
                    while view:
                        count = os.write(destination_file, view)
                        if count <= 0:
                            raise OSError("short cache copy write")
                        view = view[count:]
                os.fsync(destination_file)
            finally:
                os.close(source_file)
                if destination_file >= 0:
                    os.close(destination_file)
            os.fsync(destination_fd)
        else:
            raise MetadataPreflightError("cache_unsupported_entry")


def _copy_cache_once(
    root_fd: int,
    root_path: Path,
    cache_source: Path,
    cargo_home: Path,
    *,
    device: int,
) -> tuple[bool, int, int]:
    try:
        source_rel = cache_source.relative_to(root_path)
        destination_rel = cargo_home.relative_to(root_path)
    except ValueError as exc:
        raise MetadataPreflightError("cache_copy_outside_root") from exc
    if (
        source_rel == destination_rel
        or source_rel in destination_rel.parents
        or destination_rel in source_rel.parents
        or not source_rel.parts
        or not destination_rel.parts
    ):
        raise MetadataPreflightError("cache_copy_paths_overlap")
    pinned_root_fd = os.dup(root_fd)
    try:
        root_stat = os.fstat(pinned_root_fd)
        if root_stat.st_dev != device or root_stat.st_uid != os.getuid():
            raise MetadataPreflightError("cache_copy_root_identity")
        source_fd = _open_directory_beneath(pinned_root_fd, source_rel, device=device)
        try:
            try:
                destination_fd = _open_directory_beneath(
                    pinned_root_fd, destination_rel, device=device
                )
            except FileNotFoundError:
                current_fd = os.dup(pinned_root_fd)
                try:
                    for part in destination_rel.parts[:-1]:
                        try:
                            child_fd = os.open(
                                part,
                                os.O_RDONLY
                                | getattr(os, "O_DIRECTORY", 0)
                                | getattr(os, "O_NOFOLLOW", 0),
                                dir_fd=current_fd,
                            )
                        except FileNotFoundError:
                            os.mkdir(part, mode=0o700, dir_fd=current_fd)
                            child_fd = os.open(
                                part,
                                os.O_RDONLY
                                | getattr(os, "O_DIRECTORY", 0)
                                | getattr(os, "O_NOFOLLOW", 0),
                                dir_fd=current_fd,
                            )
                        os.close(current_fd)
                        parent_stat = os.fstat(child_fd)
                        if (
                            parent_stat.st_dev != device
                            or parent_stat.st_uid != os.getuid()
                            or not stat.S_ISDIR(parent_stat.st_mode)
                            or stat.S_IMODE(parent_stat.st_mode) & 0o022
                        ):
                            os.close(child_fd)
                            raise MetadataPreflightError("cache_destination_parent_identity")
                        current_fd = child_fd
                    leaf = destination_rel.parts[-1]
                    os.mkdir(leaf, mode=0o700, dir_fd=current_fd)
                    destination_fd = os.open(
                        leaf,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=current_fd,
                    )
                    os.fsync(current_fd)
                finally:
                    os.close(current_fd)
                limits = [0, 0]
                try:
                    _copy_cache_tree(source_fd, destination_fd, device=device, limits=limits)
                    os.fsync(destination_fd)
                finally:
                    os.close(destination_fd)
                return True, limits[0], limits[1]
            else:
                os.close(destination_fd)
                raise MetadataPreflightError("copied_cache_destination_already_exists")
        finally:
            os.close(source_fd)
    finally:
        os.close(pinned_root_fd)


def _mkdirs_beneath_root(
    root_fd: int, root_path: Path, paths: tuple[Path, ...], device: int
) -> None:
    """Create stage directories only through the already opened data-root descriptor."""
    for path in paths:
        try:
            relative = path.relative_to(root_path)
        except ValueError as exc:
            raise MetadataPreflightError("stage_directory_outside_storage_root") from exc
        if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            raise MetadataPreflightError("stage_directory_path_invalid")
        current_fd = os.dup(root_fd)
        try:
            for part in relative.parts:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                next_fd = os.open(
                    part,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current_fd,
                )
                current_stat = os.fstat(next_fd)
                if (
                    not stat.S_ISDIR(current_stat.st_mode)
                    or current_stat.st_dev != device
                    or current_stat.st_uid != os.getuid()
                    or stat.S_IMODE(current_stat.st_mode) & 0o077
                ):
                    os.close(next_fd)
                    raise MetadataPreflightError("stage_directory_identity")
                os.close(current_fd)
                current_fd = next_fd
            os.fsync(current_fd)
        finally:
            os.close(current_fd)


def _check_hash(value: str, code: str) -> None:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise MetadataPreflightError(code)


def _source_pins(source: Path, cargo_home: Path) -> tuple[str, str, str, int, str]:
    safe_env = {"PATH": FIXED_PATH, "HOME": str(cargo_home)}
    if _source_revision(source, safe_env) != EXPECTED_AGAVE_HEAD:
        raise MetadataPreflightError("source_head_mismatch")
    source_status = _git_snapshot(source, safe_env)
    if source_status[1] != EXPECTED_COMPOSITE_DIFF:
        raise MetadataPreflightError("source_diff_mismatch")
    lock_path = source / "Cargo.lock"
    lock_sha = _sha256(lock_path.read_bytes())
    if lock_sha != EXPECTED_POST_V5_LOCK:
        raise MetadataPreflightError("cargo_lock_mismatch")
    lock_sources, package_count = _locked_sources(lock_path)
    if set(lock_sources) != {EXPECTED_GIT_SOURCE, EXPECTED_REGISTRY_SOURCE}:
        raise MetadataPreflightError("cargo_lock_sources_mismatch")
    if package_count != WORKSPACE_MEMBER_COUNT:
        raise MetadataPreflightError("cargo_lock_package_count_mismatch")
    _source_configuration(source, cargo_home)
    manifest = source / "program-test" / "Cargo.toml"
    try:
        manifest_stat = manifest.lstat()
    except OSError as exc:
        raise MetadataPreflightError("program_test_manifest_missing") from exc
    if (
        not manifest.is_file()
        or manifest.is_symlink()
        or manifest_stat.st_dev != source.stat().st_dev
    ):
        raise MetadataPreflightError("program_test_manifest_identity")
    return EXPECTED_AGAVE_HEAD, source_status[1], lock_sha, package_count, source_status[0]


def _metadata_graph(raw: bytes) -> dict[str, Any]:
    data = _strict_json(raw, "metadata_stdout")
    if not isinstance(data, dict) or data.get("version") != 1:
        raise MetadataPreflightError("metadata_schema")
    packages = data.get("packages")
    members = data.get("workspace_members")
    resolve = data.get("resolve")
    if not isinstance(packages, list) or not isinstance(members, list):
        raise MetadataPreflightError("metadata_package_graph")
    if not isinstance(resolve, dict) or not isinstance(resolve.get("nodes"), list):
        raise MetadataPreflightError("metadata_resolve_graph")
    package_by_id: dict[str, dict[str, Any]] = {}
    safe_ids: dict[str, str] = {}
    allowed_sources = {EXPECTED_GIT_SOURCE, EXPECTED_REGISTRY_SOURCE}
    for package in packages:
        if not isinstance(package, dict):
            raise MetadataPreflightError("metadata_package_row")
        package_id = package.get("id")
        if not isinstance(package_id, str) or package_id in package_by_id:
            raise MetadataPreflightError("metadata_package_identity")
        for field in ("name", "version"):
            if not isinstance(package.get(field), str):
                raise MetadataPreflightError("metadata_package_fields")
        source = package.get("source")
        if source is not None and (not isinstance(source, str) or source not in allowed_sources):
            raise MetadataPreflightError("metadata_package_source")
        safe_id = _safe_package_id(package_id, package)
        if safe_id in safe_ids.values():
            raise MetadataPreflightError("metadata_package_identity_collision")
        package_by_id[package_id] = package
        safe_ids[package_id] = safe_id
    node_rows: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    for node in resolve["nodes"]:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise MetadataPreflightError("metadata_resolve_node")
        node_id = node["id"]
        if node_id in seen_nodes or node_id not in package_by_id:
            raise MetadataPreflightError("metadata_resolve_node_identity")
        seen_nodes.add(node_id)
        dependencies = node.get("deps", [])
        if not isinstance(dependencies, list):
            raise MetadataPreflightError("metadata_resolve_dependencies")
        dep_rows: list[dict[str, Any]] = []
        for dependency in dependencies:
            if (
                not isinstance(dependency, dict)
                or not isinstance(dependency.get("pkg"), str)
                or dependency["pkg"] not in package_by_id
            ):
                raise MetadataPreflightError("metadata_resolve_dependency_identity")
            dep_kinds = dependency.get("dep_kinds", [])
            if not isinstance(dep_kinds, list):
                raise MetadataPreflightError("metadata_resolve_dependency_kinds")
            dep_rows.append({"pkg": dependency["pkg"], "dep_kinds": dep_kinds})
        features = node.get("features", [])
        if not isinstance(features, list) or not all(isinstance(item, str) for item in features):
            raise MetadataPreflightError("metadata_resolve_features")
        node_rows.append(
            {
                "id": safe_ids[node_id],
                "deps": sorted(
                    [
                        {"pkg": safe_ids[row["pkg"]], "dep_kinds": row["dep_kinds"]}
                        for row in dep_rows
                    ],
                    key=lambda row: row["pkg"],
                ),
                "features": sorted(features),
            }
        )
    if not members or not all(
        isinstance(member, str) and member in package_by_id for member in members
    ):
        raise MetadataPreflightError("metadata_workspace_members")
    root = resolve.get("root")
    if root is not None and (not isinstance(root, str) or root not in package_by_id):
        raise MetadataPreflightError("metadata_resolve_root")
    package_rows = [
        {
            "id": safe_ids[package_id],
            "name": package["name"],
            "version": package["version"],
            "source": package.get("source"),
        }
        for package_id, package in sorted(package_by_id.items())
    ]
    return {
        "schema": "brain-senses-agave-resolved-graph-v1",
        "root": safe_ids[root] if root is not None else None,
        "workspace_members": sorted(safe_ids[member] for member in members),
        "packages": package_rows,
        "nodes": sorted(node_rows, key=lambda row: row["id"]),
    }


def _safe_package_id(package_id: str, package: dict[str, Any]) -> str:
    """Replace a local Cargo ID with a full digest of its complete identity."""
    if _LOCAL_PACKAGE_ID.search(package_id):
        identity = {
            "raw_package_id": package_id,
            "name": package["name"],
            "version": package["version"],
        }
        digest = hashlib.sha256(_canonical(identity)).hexdigest()
        return f"local+sha256:{digest}"
    return _HOME_PATH.sub("<user-home>", package_id)


def _locked_package_tuples(lock_path: Path) -> set[tuple[str, str, str | None]]:
    try:
        document = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise MetadataPreflightError("cargo_lock_parse") from exc
    rows = document.get("package")
    if not isinstance(rows, list) or len(rows) != WORKSPACE_MEMBER_COUNT:
        raise MetadataPreflightError("cargo_lock_tuple_count")
    tuples: set[tuple[str, str, str | None]] = set()
    allowed_sources = {EXPECTED_GIT_SOURCE, EXPECTED_REGISTRY_SOURCE}
    for row in rows:
        if not isinstance(row, dict):
            raise MetadataPreflightError("cargo_lock_package_row")
        name, version, source = row.get("name"), row.get("version"), row.get("source")
        if (
            not isinstance(name, str)
            or not isinstance(version, str)
            or (
                source is not None
                and (not isinstance(source, str) or source not in allowed_sources)
            )
        ):
            raise MetadataPreflightError("cargo_lock_package_identity")
        identity = (name, version, source)
        if identity in tuples:
            raise MetadataPreflightError("cargo_lock_tuple_collision")
        tuples.add(identity)
    return tuples


def _validate_resolved_graph(graph: dict[str, Any], lock_path: Path) -> dict[str, int]:
    """Recheck graph identity closure and injective membership in Cargo.lock."""
    if (
        set(graph) != {"schema", "root", "workspace_members", "packages", "nodes"}
        or graph.get("schema") != "brain-senses-agave-resolved-graph-v1"
    ):
        raise MetadataPreflightError("resolved_graph_schema")
    packages = graph.get("packages")
    nodes = graph.get("nodes")
    members = graph.get("workspace_members")
    root = graph.get("root")
    if (
        not isinstance(packages, list)
        or not isinstance(nodes, list)
        or not isinstance(members, list)
    ):
        raise MetadataPreflightError("resolved_graph_shape")
    package_by_id: dict[str, dict[str, Any]] = {}
    graph_tuples: set[tuple[str, str, str | None]] = set()
    allowed_sources = {EXPECTED_GIT_SOURCE, EXPECTED_REGISTRY_SOURCE}
    for row in packages:
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "name", "version", "source"}
            or not isinstance(row.get("id"), str)
            or not isinstance(row.get("name"), str)
            or not isinstance(row.get("version"), str)
        ):
            raise MetadataPreflightError("resolved_graph_package")
        source = row.get("source")
        if source is not None and (not isinstance(source, str) or source not in allowed_sources):
            raise MetadataPreflightError("resolved_graph_source")
        package_id = row["id"]
        if package_id.startswith("local+sha256:"):
            if not re.fullmatch(r"local\+sha256:[0-9a-f]{64}", package_id):
                raise MetadataPreflightError("resolved_graph_local_id_alias")
        elif package_id.startswith("path+file://") or "/Users/" in package_id:
            raise MetadataPreflightError("resolved_graph_local_id_leak")
        identity = (row["name"], row["version"], source)
        if package_id in package_by_id or identity in graph_tuples:
            raise MetadataPreflightError("resolved_graph_identity_collision")
        package_by_id[package_id] = row
        graph_tuples.add(identity)
    if not isinstance(root, str) or root not in package_by_id:
        raise MetadataPreflightError("resolved_graph_root_closure")
    if (
        not members
        or not all(isinstance(member, str) and member in package_by_id for member in members)
        or len(members) != len(set(members))
    ):
        raise MetadataPreflightError("resolved_graph_workspace_closure")
    node_by_id: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if (
            not isinstance(node, dict)
            or set(node) != {"id", "deps", "features"}
            or not isinstance(node.get("id"), str)
            or not isinstance(node.get("deps"), list)
            or not isinstance(node.get("features"), list)
            or not all(isinstance(feature, str) for feature in node["features"])
        ):
            raise MetadataPreflightError("resolved_graph_node")
        node_id = node["id"]
        if node_id not in package_by_id or node_id in node_by_id:
            raise MetadataPreflightError("resolved_graph_node_closure")
        node_by_id[node_id] = node
        for dependency in node["deps"]:
            if (
                not isinstance(dependency, dict)
                or set(dependency) != {"pkg", "dep_kinds"}
                or not isinstance(dependency.get("pkg"), str)
                or dependency["pkg"] not in package_by_id
                or not isinstance(dependency.get("dep_kinds"), list)
            ):
                raise MetadataPreflightError("resolved_graph_dependency_closure")
    if set(node_by_id) != set(package_by_id) or root not in node_by_id:
        raise MetadataPreflightError("resolved_graph_node_set")
    lock_tuples = _locked_package_tuples(lock_path)
    if not graph_tuples.issubset(lock_tuples):
        raise MetadataPreflightError("resolved_graph_not_in_lock")
    null_workspace_members = sum(package_by_id[member]["source"] is None for member in members)
    if null_workspace_members != len(members):
        raise MetadataPreflightError("resolved_graph_workspace_source")
    return {
        "lock_tuple_count": len(lock_tuples),
        "graph_tuple_count": len(graph_tuples),
        "lock_only_tuple_count": len(lock_tuples - graph_tuples),
        "source_null_workspace_member_count": null_workspace_members,
    }


def _target_probe(target_dir: Path, device: int) -> tuple[str | None, str | None]:
    try:
        directory_fd = os.open(
            target_dir,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except FileNotFoundError:
        return None, None
    try:
        directory_st = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(directory_st.st_mode)
            or directory_st.st_dev != device
            or directory_st.st_uid != os.getuid()
            or stat.S_IMODE(directory_st.st_mode) & 0o077
        ):
            raise MetadataPreflightError("target_directory_identity")
        entries = os.listdir(directory_fd)
        if not entries:
            return None, None
        if entries != [".rustc_info.json"]:
            raise MetadataPreflightError("unexpected_target_artifact")
        marker_fd = os.open(
            ".rustc_info.json",
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        try:
            st = os.fstat(marker_fd)
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_dev != device
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) & 0o022
                or st.st_nlink != 1
                or st.st_size > MAX_TARGET_PROBE_BYTES
            ):
                raise MetadataPreflightError("rustc_probe_identity")
            raw = os.read(marker_fd, MAX_TARGET_PROBE_BYTES + 1)
            if len(raw) != st.st_size or len(raw) > MAX_TARGET_PROBE_BYTES:
                raise MetadataPreflightError("rustc_probe_size_changed")
        finally:
            os.close(marker_fd)
    finally:
        os.close(directory_fd)
    parsed = _strict_json(raw, "rustc_probe")
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {"rustc_fingerprint", "outputs", "successes"}
        or not isinstance(parsed.get("rustc_fingerprint"), int)
        or isinstance(parsed.get("rustc_fingerprint"), bool)
        or not 0 <= parsed["rustc_fingerprint"] < 2**64
        or not isinstance(parsed.get("outputs"), dict)
        or not parsed["outputs"]
        or parsed.get("successes") != {}
    ):
        raise MetadataPreflightError("rustc_probe_schema")
    outputs: list[str] = []
    for probe_id, row in parsed["outputs"].items():
        if (
            not isinstance(probe_id, str)
            or not probe_id.isdecimal()
            or not isinstance(row, dict)
            or set(row) != {"success", "status", "code", "stdout", "stderr"}
            or row.get("success") is not True
            or row.get("status") != ""
            or row.get("code") != 0
            or not isinstance(row.get("stdout"), str)
            or not isinstance(row.get("stderr"), str)
            or len(row["stdout"].encode()) + len(row["stderr"].encode()) > 8 * 1024
        ):
            raise MetadataPreflightError("rustc_probe_output")
        outputs.append(row["stdout"])
    combined = "\n".join(outputs)
    if (
        f"rustc {RUST_VERSION} " not in combined
        or 'target_arch="aarch64"' not in combined
        or 'target_os="macos"' not in combined
        or 'target_vendor="apple"' not in combined
    ):
        raise MetadataPreflightError("rustc_probe_toolchain_or_target")
    return ".rustc_info.json", _sha256(raw)


class _StreamCapture:
    def __init__(self, stream: Any, cap: int) -> None:
        self.stream = stream
        self.cap = cap
        self.data = bytearray()
        self.byte_count = 0
        self.read_error = False
        self.digest = hashlib.sha256()
        self.lock = threading.Lock()

    def drain(self) -> None:
        try:
            while True:
                chunk = self.stream.read(64 * 1024)
                if not chunk:
                    return
                with self.lock:
                    self.byte_count += len(chunk)
                    self.digest.update(chunk)
                    if len(self.data) < self.cap:
                        self.data.extend(chunk[: self.cap - len(self.data)])
        except (OSError, ValueError):
            with self.lock:
                self.read_error = True

    def snapshot(self, *, complete: bool = True) -> CapturedStream:
        with self.lock:
            retained = bytes(self.data)
            return CapturedStream(
                self.digest.hexdigest(),
                self.byte_count,
                retained,
                self.byte_count > self.cap,
                self.read_error,
                complete,
            )


def _collect_process_output(stdout: Any, stderr: Any) -> tuple[CapturedStream, CapturedStream]:
    out = _StreamCapture(stdout, MAX_STDOUT_BYTES)
    err = _StreamCapture(stderr, MAX_STDERR_BYTES)
    threads = [
        threading.Thread(target=out.drain, daemon=True, name="metadata-stdout-capture"),
        threading.Thread(target=err.drain, daemon=True, name="metadata-stderr-capture"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    if any(thread.is_alive() for thread in threads):
        raise MetadataPreflightError("metadata_output_drain_timeout")
    return out.snapshot(), err.snapshot()


def _build_metadata_environment(paths: tuple[Path, ...]) -> dict[str, str]:
    env = _build_environment(paths)
    env["PATH"] = FIXED_PATH
    env["CARGO_NET_OFFLINE"] = "true"
    env.pop("CARGO_NET_RETRY", None)
    env.pop("CARGO_NET_GIT_FETCH_WITH_CLI", None)
    return env


def _target_empty(target_dir: Path) -> None:
    if target_dir.exists() and any(target_dir.iterdir()):
        raise MetadataPreflightError("metadata_target_not_empty")


def _inspect_metadata_stage(
    provider: SnapshotProvider,
    paths: tuple[Path, ...],
    cache_source: Path,
    first: Any,
) -> tuple[Any, int]:
    current, stage_allocation = _bounded_inspect_stage(
        provider,
        paths,
        first,
        timeout_seconds=PROBE_TIMEOUT_SECONDS,
        allow_cargo_git_checkout_symlinks=True,
    )
    cache_allocation = _allocated_bytes(
        (cache_source,), current.mount_device, allow_cargo_git_checkout_symlinks=True
    )
    total = stage_allocation + cache_allocation
    if total > ALLOCATION_CAP_BYTES:
        raise MetadataPreflightError("stage_allocation_over_cap")
    if current.available_bytes < MIN_FREE_BYTES + max(0, ALLOCATION_CAP_BYTES - total):
        raise MetadataPreflightError("stage_free_space_envelope")
    return current, total


def run_metadata_preflight(
    source: Path,
    cache_source: Path,
    cargo_home: Path,
    target_dir: Path,
    tmpdir: Path,
    *,
    expected_cache_manifest_sha256: str,
    snapshot_provider: SnapshotProvider | None = None,
    popen: Any = subprocess.Popen,
    monotonic: Any = time.monotonic,
    sleep: Any = time.sleep,
) -> MetadataResult:
    """Resolve locked package metadata once, with no network or compile step."""
    _check_hash(expected_cache_manifest_sha256, "invalid_expected_cache_manifest_sha256")
    _check_unprivileged_root1()
    provider = snapshot_provider or BoundStorageSnapshot()
    source = Path(source)
    cache_source = Path(cache_source)
    cargo_home = Path(cargo_home)
    target_dir = Path(target_dir)
    tmpdir = Path(tmpdir)
    if cargo_home == Path.home() / ".cargo":
        raise MetadataPreflightError("using_host_cargo_cache_forbidden")
    first = provider.inspect()
    paths = _check_stage_paths((source, cargo_home, target_dir, tmpdir), first)
    source, cargo_home, target_dir, tmpdir = paths
    stage_root = first.root_path / METADATA_STAGE_RELATIVE
    if any(stage_root not in path.parents for path in paths[1:]):
        raise MetadataPreflightError("metadata_stage_namespace_mismatch")
    if stage_root.exists() or stage_root.is_symlink():
        raise MetadataPreflightError("metadata_stage_namespace_not_fresh")
    checked_cache_source = cache_source
    # Validate the source cache path with the same no-symlink component policy.
    checked_cache_source = _plain_descendant(checked_cache_source, first.root_path, "cache_source")
    all_paths = (*paths, checked_cache_source)
    for index, left in enumerate(all_paths):
        for right in all_paths[index + 1 :]:
            if left in right.parents or right in left.parents:
                raise MetadataPreflightError("stage_paths_overlap")
    if first.available_bytes < INITIAL_FREE_BYTES:
        raise MetadataPreflightError("initial_free_below_90_gib")
    source_cache_before = _locked_cache_manifest(checked_cache_source)
    if source_cache_before != expected_cache_manifest_sha256:
        raise MetadataPreflightError("source_cache_manifest_mismatch")
    _inspect_metadata_stage(provider, paths, checked_cache_source, first)
    root_fd = _open_bound_data_root(first)
    try:
        copied, cache_copy_entries, cache_copy_bytes = _copy_cache_once(
            root_fd,
            first.root_path,
            checked_cache_source,
            cargo_home,
            device=first.mount_device,
        )
    finally:
        os.close(root_fd)
    _inspect_metadata_stage(provider, paths, checked_cache_source, first)
    if _locked_cache_manifest(checked_cache_source) != source_cache_before:
        raise MetadataPreflightError("source_cache_changed_during_copy")
    cache_before_rows = _tree_manifest_rows(cargo_home)
    cache_before = hashlib.sha256(_canonical(cache_before_rows)).hexdigest()
    if cache_before != source_cache_before:
        raise MetadataPreflightError("copied_cache_manifest_mismatch")
    root_fd = _open_bound_data_root(first)
    try:
        _mkdirs_beneath_root(root_fd, first.root_path, paths[1:], first.mount_device)
    finally:
        os.close(root_fd)
    source_head, diff_sha, lock_sha, package_count, source_status_sha = _source_pins(
        source, cargo_home
    )
    if package_count != WORKSPACE_MEMBER_COUNT:
        raise MetadataPreflightError("locked_package_count_mismatch")
    _target_empty(target_dir)
    if any(tmpdir.iterdir()):
        raise MetadataPreflightError("metadata_tmp_not_empty")
    initial_allocation = _allocated_bytes(
        paths, first.mount_device, allow_cargo_git_checkout_symlinks=True
    ) + _allocated_bytes(
        (checked_cache_source,), first.mount_device, allow_cargo_git_checkout_symlinks=True
    )
    if initial_allocation > ALLOCATION_CAP_BYTES:
        raise MetadataPreflightError("stage_allocation_over_cap")
    if first.available_bytes < MIN_FREE_BYTES + max(0, ALLOCATION_CAP_BYTES - initial_allocation):
        raise MetadataPreflightError("stage_free_space_envelope")

    env = _build_metadata_environment(paths)
    env["CARGO_HOME"] = str(cargo_home)
    cargo_executable = shutil.which("cargo", path=env["PATH"])
    if not cargo_executable:
        raise MetadataPreflightError("cargo_executable_missing")
    manifest = source / "program-test" / "Cargo.toml"
    command = (
        cargo_executable,
        "metadata",
        "--offline",
        "--locked",
        "--filter-platform",
        TARGET_TRIPLE,
        "--format-version",
        "1",
        "--manifest-path",
        str(manifest),
    )
    _inspect_metadata_stage(provider, paths, checked_cache_source, first)
    # Recheck all pins and storage immediately before the one allowed Cargo call.
    if _source_pins(source, cargo_home)[:3] != (source_head, diff_sha, lock_sha):
        raise MetadataPreflightError("source_changed_before_metadata")
    if _locked_cache_manifest(cargo_home) != cache_before:
        raise MetadataPreflightError("cache_changed_before_metadata")
    _target_empty(target_dir)
    _inspect_metadata_stage(provider, paths, checked_cache_source, first)
    started = monotonic()
    process: Any = None
    captures: tuple[CapturedStream, CapturedStream] | None = None
    stdout_capture: _StreamCapture | None = None
    stderr_capture: _StreamCapture | None = None
    readers: list[threading.Thread] = []
    artifact_root_fd = -1
    try:
        artifact_root_fd = _open_bound_data_root(first)
        process = popen(
            list(command),
            cwd=source,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
            pass_fds=(),
        )
        stdout_capture = _StreamCapture(process.stdout, MAX_STDOUT_BYTES)
        stderr_capture = _StreamCapture(process.stderr, MAX_STDERR_BYTES)
        readers = [
            threading.Thread(target=stdout_capture.drain, daemon=True, name="metadata-stdout"),
            threading.Thread(target=stderr_capture.drain, daemon=True, name="metadata-stderr"),
        ]
        for reader in readers:
            reader.start()
        while True:
            if monotonic() - started > MAX_WALL_SECONDS:
                raise MetadataPreflightError("metadata_timeout")
            _inspect_metadata_stage(provider, paths, checked_cache_source, first)
            status = process.poll()
            if status is not None:
                break
            sleep(POLL_SECONDS)
        for reader in readers:
            reader.join(timeout=10)
        drain_incomplete = any(reader.is_alive() for reader in readers)
        out_stream = stdout_capture.snapshot(complete=not drain_incomplete)
        err_stream = stderr_capture.snapshot(complete=not drain_incomplete)
        captures = (out_stream, err_stream)
        classification = (
            "output_drain_timeout"
            if drain_incomplete
            else "output_read_error"
            if out_stream.read_error or err_stream.read_error
            else "stdout_truncated"
            if out_stream.truncated
            else "stderr_truncated"
            if err_stream.truncated
            else "process_nonzero_exit"
            if status != 0
            else "process_completed"
        )
        process_receipt = {
            "schema": "brain-senses-agave-metadata-process-capture-v2",
            "schema_version": 2,
            "storage_root_id": first.root_id,
            "volume_uuid": first.volume_uuid,
            "stdout_sha256": out_stream.raw_sha256,
            "stdout_bytes": out_stream.byte_count,
            "stdout_truncated": out_stream.truncated,
            "stdout_read_error": out_stream.read_error,
            "stdout_complete": out_stream.complete,
            "stdout_hash_scope": "full_stream" if out_stream.complete else "captured_prefix",
            "stderr_sha256": err_stream.raw_sha256,
            "stderr_bytes": err_stream.byte_count,
            "stderr_truncated": err_stream.truncated,
            "stderr_read_error": err_stream.read_error,
            "stderr_complete": err_stream.complete,
            "stderr_hash_scope": "full_stream" if err_stream.complete else "captured_prefix",
            "stderr_sanitized": _sanitize(
                err_stream.retained, (source, cargo_home, target_dir, tmpdir)
            ),
            "exit_status": int(status),
            "classification": classification,
        }
        process_receipt_bytes = _canonical(process_receipt)
        process_receipt_path = (
            METADATA_STAGE_RELATIVE / "metadata-process-capture.json"
        ).as_posix()
        process_receipt_sha = _write_create_only_beneath_root(
            artifact_root_fd,
            Path(process_receipt_path),
            process_receipt_bytes,
            device=first.mount_device,
            revalidate=lambda: _inspect_metadata_stage(
                provider, paths, checked_cache_source, first
            ),
        )
        if drain_incomplete:
            raise MetadataPreflightError(
                "metadata_output_drain_timeout", stdout=out_stream, stderr=err_stream
            )
        if out_stream.truncated or err_stream.truncated:
            raise MetadataPreflightError(
                "metadata_output_limit_exceeded", stdout=out_stream, stderr=err_stream
            )
        if out_stream.read_error or err_stream.read_error:
            raise MetadataPreflightError(
                "metadata_output_read_failed", stdout=out_stream, stderr=err_stream
            )
        if status != 0:
            raise MetadataPreflightError(
                f"metadata_exit_{status}", stdout=out_stream, stderr=err_stream
            )
        graph = _metadata_graph(out_stream.retained)
        graph_sha = hashlib.sha256(_canonical(graph)).hexdigest()
        graph_receipt_path = (METADATA_STAGE_RELATIVE / "resolved-graph.json").as_posix()
        graph_receipt_sha = _write_create_only_beneath_root(
            artifact_root_fd,
            Path(graph_receipt_path),
            _canonical(graph),
            device=first.mount_device,
            revalidate=lambda: _inspect_metadata_stage(
                provider, paths, checked_cache_source, first
            ),
        )
        resolution_counts = _validate_resolved_graph(graph, source / "Cargo.lock")
        if resolution_counts != {
            "lock_tuple_count": WORKSPACE_MEMBER_COUNT,
            "graph_tuple_count": EXPECTED_RESOLVED_TUPLE_COUNT,
            "lock_only_tuple_count": EXPECTED_LOCK_ONLY_TUPLE_COUNT,
            "source_null_workspace_member_count": EXPECTED_SOURCE_NULL_WORKSPACE_MEMBERS,
        }:
            raise MetadataPreflightError(
                "resolved_graph_accounting_mismatch", stdout=out_stream, stderr=err_stream
            )
        final, allocation = _inspect_metadata_stage(provider, paths, checked_cache_source, first)
        if final.available_bytes < MIN_FREE_BYTES:
            raise MetadataPreflightError("final_free_floor")
        final_source = _source_pins(source, cargo_home)
        if final_source[:3] != (source_head, diff_sha, lock_sha):
            raise MetadataPreflightError("source_changed_after_metadata")
        if _locked_cache_manifest(checked_cache_source) != source_cache_before:
            raise MetadataPreflightError("source_cache_changed_after_metadata")
        cache_after_rows = _tree_manifest_rows(cargo_home)
        expected_registry_crates = {
            f"{package['name']}-{package['version']}"
            for package in graph["packages"]
            if package["source"] == EXPECTED_REGISTRY_SOURCE
        }
        cache_delta = _cache_delta(
            cache_before_rows,
            cache_after_rows,
            expected_registry_crates=expected_registry_crates,
        )
        cache_after = hashlib.sha256(_canonical(cache_after_rows)).hexdigest()
        target_probe_path, target_probe_sha = _target_probe(target_dir, final.mount_device)
        if any(tmpdir.iterdir()):
            raise MetadataPreflightError("metadata_tmp_not_empty_after")
        result = MetadataResult(
            command=command,
            status="offline_metadata_resolved_no_build",
            storage_root_id=final.root_id,
            volume_uuid=final.volume_uuid,
            initial_available_bytes=first.available_bytes,
            available_bytes=final.available_bytes,
            stage_allocated_bytes=allocation,
            source_head=source_head,
            source_diff_sha256=diff_sha,
            source_status_sha256=source_status_sha,
            cargo_lock_sha256=lock_sha,
            cache_source_manifest_sha256=source_cache_before,
            cache_manifest_sha256=cache_before,
            cache_after_manifest_sha256=cache_after,
            cache_extraction_delta_sha256=hashlib.sha256(_canonical(cache_delta)).hexdigest(),
            cache_copy_entries=cache_copy_entries if copied else 0,
            cache_copy_bytes=cache_copy_bytes if copied else 0,
            rust_toolchain=RUST_VERSION,
            target_triple=TARGET_TRIPLE,
            default_features=True,
            feature_intent="default_features_only",
            metadata_stdout_sha256=out_stream.raw_sha256,
            metadata_stdout_bytes=out_stream.byte_count,
            stderr_sha256=err_stream.raw_sha256,
            stderr_bytes=err_stream.byte_count,
            stderr_sanitized=_sanitize(
                err_stream.retained, (source, cargo_home, target_dir, tmpdir)
            ),
            graph_sha256=graph_sha,
            graph=graph,
            lock_tuple_count=resolution_counts["lock_tuple_count"],
            graph_tuple_count=resolution_counts["graph_tuple_count"],
            lock_only_tuple_count=resolution_counts["lock_only_tuple_count"],
            source_null_workspace_member_count=resolution_counts[
                "source_null_workspace_member_count"
            ],
            target_probe_path=target_probe_path,
            target_probe_sha256=target_probe_sha,
            process_receipt_path=process_receipt_path,
            process_receipt_sha256=process_receipt_sha,
            graph_receipt_path=graph_receipt_path,
            graph_receipt_sha256=graph_receipt_sha,
            elapsed_seconds=max(0.0, monotonic() - started),
        )
        os.close(artifact_root_fd)
        artifact_root_fd = -1
        return result
    except BaseException as exc:
        if artifact_root_fd >= 0:
            os.close(artifact_root_fd)
            artifact_root_fd = -1
        if process is not None:
            try:
                _terminate_group(process, grace_seconds=1.0)
            except Exception:
                pass
        for reader in readers:
            reader.join(timeout=2)
        if captures is None and stdout_capture is not None and stderr_capture is not None:
            captures = (stdout_capture.snapshot(), stderr_capture.snapshot())
        if isinstance(exc, MetadataPreflightError):
            if captures is not None and exc.stdout_sha256 is None:
                raise MetadataPreflightError(
                    exc.code,
                    stdout=captures[0],
                    stderr=captures[1],
                ) from exc
            raise
        if captures is not None:
            raise MetadataPreflightError(
                "metadata_stage_failed", stdout=captures[0], stderr=captures[1]
            ) from exc
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument(
        "--cache-source",
        type=Path,
        required=True,
        help="read-only qualified cache beneath the bound Kingston root",
    )
    parser.add_argument(
        "--cargo-home",
        type=Path,
        required=True,
        help="separate Kingston copy of the previously qualified cache",
    )
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--tmpdir", type=Path, required=True)
    parser.add_argument("--expected-cache-manifest-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        result = run_metadata_preflight(
            args.source,
            args.cache_source,
            args.cargo_home,
            args.target_dir,
            args.tmpdir,
            expected_cache_manifest_sha256=args.expected_cache_manifest_sha256,
        )
        print(
            json.dumps(
                {
                    "status": result.status,
                    "operation": (
                        "cargo metadata --offline --locked --filter-platform "
                        f"{TARGET_TRIPLE} --format-version 1"
                    ),
                    "source_head": result.source_head,
                    "cargo_lock_sha256": result.cargo_lock_sha256,
                    "source_diff_sha256": result.source_diff_sha256,
                    "source_status_sha256": result.source_status_sha256,
                    "cache_manifest_sha256": result.cache_manifest_sha256,
                    "cache_source_manifest_sha256": result.cache_source_manifest_sha256,
                    "cache_after_manifest_sha256": result.cache_after_manifest_sha256,
                    "cache_extraction_delta_sha256": result.cache_extraction_delta_sha256,
                    "cache_copy_entries": result.cache_copy_entries,
                    "cache_copy_bytes": result.cache_copy_bytes,
                    "rust_toolchain": result.rust_toolchain,
                    "target_triple": result.target_triple,
                    "default_features": result.default_features,
                    "feature_intent": result.feature_intent,
                    "metadata_operations": 1,
                    "cargo_version_probes": 0,
                    "metadata_stdout_sha256": result.metadata_stdout_sha256,
                    "metadata_stdout_bytes": result.metadata_stdout_bytes,
                    "stderr_sha256": result.stderr_sha256,
                    "stderr_bytes": result.stderr_bytes,
                    "stderr_sanitized": result.stderr_sanitized,
                    "graph_sha256": result.graph_sha256,
                    "graph": result.graph,
                    "package_count": len(result.graph["packages"]),
                    "resolve_node_count": len(result.graph["nodes"]),
                    "lock_tuple_count": result.lock_tuple_count,
                    "graph_tuple_count": result.graph_tuple_count,
                    "lock_only_tuple_count": result.lock_only_tuple_count,
                    "source_null_workspace_member_count": result.source_null_workspace_member_count,
                    "target_probe_path": result.target_probe_path,
                    "target_probe_sha256": result.target_probe_sha256,
                    "process_receipt_path": result.process_receipt_path,
                    "process_receipt_sha256": result.process_receipt_sha256,
                    "graph_receipt_path": result.graph_receipt_path,
                    "graph_receipt_sha256": result.graph_receipt_sha256,
                    "storage_root_id": result.storage_root_id,
                    "volume_uuid": result.volume_uuid,
                    "initial_available_bytes": result.initial_available_bytes,
                    "available_bytes": result.available_bytes,
                    "stage_allocated_bytes": result.stage_allocated_bytes,
                    "storage_caps": {
                        "initial_free_floor_bytes": INITIAL_FREE_BYTES,
                        "final_free_floor_bytes": MIN_FREE_BYTES,
                        "stage_allocation_cap_bytes": ALLOCATION_CAP_BYTES,
                        "cache_copy_cap_bytes": MAX_CACHE_COPY_BYTES,
                        "cache_copy_cap_entries": MAX_CACHE_COPY_ENTRIES,
                        "wall_seconds": MAX_WALL_SECONDS,
                        "stdout_cap_bytes": MAX_STDOUT_BYTES,
                        "stderr_cap_bytes": MAX_STDERR_BYTES,
                    },
                    "elapsed_seconds": result.elapsed_seconds,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        reason = exc.code if isinstance(exc, MetadataPreflightError) else type(exc).__name__
        if not re.fullmatch(r"[a-zA-Z0-9_]+", reason):
            reason = "metadata_preflight_failed"
        detail = exc.stderr_sanitized if isinstance(exc, MetadataPreflightError) else ""
        if detail:
            print(f"metadata preflight stopped: {reason}: {detail}", file=sys.stderr)
        else:
            print(f"metadata preflight stopped: {reason}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
