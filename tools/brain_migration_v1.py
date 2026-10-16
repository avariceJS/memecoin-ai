#!/usr/bin/env python3
"""Practical, create-only Brain migration custody and inert restore v1.

This tool copies explicitly supplied owner/research roots to a new Kingston
candidate. It never installs, loads, starts, or activates copied services.
Database/WAL bytes are opaque custody only and require external source quiescence;
symlink targets are not followed and may leave a restore non-self-contained.
Hardlink topology and metadata restoration are not qualified or reproduced.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import plistlib
import posixpath
import re
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCHEMA = "brain-practical-migration-v1"
MANIFEST = "MANIFEST.jsonl"
STARTED = "CAPTURE_STARTED.json"
MIGRATION_DIR = "BrainMigrationV1"
CANDIDATES_DIR = "candidates"
EXPECTED_KINGSTON_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"
MEBI = 1024 * 1024
GIB = 1024 * MEBI
FREE_FLOOR_BYTES = 50 * GIB
OPERATION_RESERVE_BYTES = 8 * GIB
RESTORE_OPERATION_RESERVE_BYTES = 8 * GIB
PAYLOAD_CAP_BYTES = 35 * GIB
TARGET_RECHECK_BYTES = 1 * GIB
TARGET_RECHECK_SECONDS = 30.0
CHUNK = 1024 * 1024
ID_RE = re.compile(r"[0-9a-f]{32}\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
ROOT_ALIASES = (
    "owner_work",
    "owner_git",
    "research_work",
    "research_git",
    "brain_local_config",
    "recovery_terminal_transcript",
    "launch_agents",
)
REQUIRED_ROOT_ALIASES = frozenset(ROOT_ALIASES)
REISSUE_EXCLUSIONS = {
    ".env": "provider_credentials_reissue",
    ".env.local": "provider_credentials_reissue",
    ".netrc": "provider_credentials_reissue",
    ".git-credentials": "provider_credentials_reissue",
    ".npmrc": "provider_credentials_reissue",
    ".pypirc": "provider_credentials_reissue",
    ".codex": "codex_session_store_reissue",
    ".cursor": "cursor_session_store_reissue",
    "keychain": "keychain_reauthentication",
    "keychains": "keychain_reauthentication",
    "credentials": "provider_credentials_reissue",
    "provider_credentials": "provider_credentials_reissue",
    "provider-credentials": "provider_credentials_reissue",
    "sessions": "provider_session_reissue",
    "session-store": "provider_session_reissue",
}
AUTH_KEY_PARTS = ("password", "secret", "token", "credential", "api_key", "apikey")
_TEST_CAPTURE_CAPABILITY = object()


class MigrationError(RuntimeError):
    """Fail-closed migration error with a stable operator-facing code."""


@dataclass(frozen=True)
class TargetPin:
    volume_uuid: str
    mount_point: str
    filesystem: str
    device_identifier: str


@dataclass(frozen=True)
class Census:
    entry_count: int
    payload_bytes: int
    logical_payload_bytes: int
    protected_d43_excluded_count: int
    signature_sha256: str


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise MigrationError("json_duplicate_field")
        value[key] = item
    return value


def _read_fd(fd: int, *, max_bytes: int | None = None) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        part = os.read(fd, CHUNK if max_bytes is None else min(CHUNK, max_bytes + 1 - size))
        if not part:
            return b"".join(chunks)
        chunks.append(part)
        size += len(part)
        if max_bytes is not None and size > max_bytes:
            raise MigrationError("file_size_limit")


def _safe_absolute(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts or "\x00" in str(path):
        raise MigrationError("absolute_path_invalid")
    return path


def _open_directory(path: Path) -> int:
    _safe_absolute(path)
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in {"", ".", ".."}:
                raise MigrationError("directory_path_invalid")
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = nxt
        return current
    except OSError as exc:
        os.close(current)
        raise MigrationError("directory_open_failed") from exc
    except BaseException:
        os.close(current)
        raise


def _target_pin(target_root: Path) -> TargetPin:
    _safe_absolute(target_root)
    try:
        result = subprocess.run(
            ["/usr/sbin/diskutil", "info", "-plist", str(target_root)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        info = plistlib.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException) as exc:
        raise MigrationError("target_diskutil_probe_failed") from exc
    if not isinstance(info, Mapping):
        raise MigrationError("target_diskutil_result_invalid")
    volume_uuid = info.get("VolumeUUID")
    mount_point = info.get("MountPoint")
    filesystem = info.get("FilesystemType") or info.get("FilesystemName")
    device = info.get("DeviceIdentifier")
    if not all(
        isinstance(item, str) and item for item in (volume_uuid, mount_point, filesystem, device)
    ):
        raise MigrationError("target_diskutil_identity_incomplete")
    return TargetPin(volume_uuid.lower(), mount_point, filesystem.lower(), device)


def _validate_target(
    target_root: Path, *, expected_uuid: str = EXPECTED_KINGSTON_UUID
) -> tuple[int, TargetPin]:
    pin = _target_pin(target_root)
    if pin.volume_uuid != expected_uuid.lower():
        raise MigrationError("target_volume_uuid_mismatch")
    if "apfs" not in pin.filesystem:
        raise MigrationError("target_filesystem_not_apfs")
    if os.path.normpath(str(target_root)) != os.path.normpath(pin.mount_point):
        raise MigrationError("target_root_not_volume_mountpoint")
    fd = _open_directory(target_root)
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode):
        os.close(fd)
        raise MigrationError("target_root_not_directory")
    return fd, pin


def _revalidate_target(target_root: Path, expected: TargetPin) -> None:
    observed = _target_pin(target_root)
    if observed != expected:
        raise MigrationError("target_volume_identity_changed")


def _assert_held_target_identity(
    target_root: Path, held_target_fd: int, expected: TargetPin
) -> None:
    _revalidate_target(target_root, expected)
    fresh_fd = _open_directory(target_root)
    try:
        held = os.fstat(held_target_fd)
        fresh = os.fstat(fresh_fd)
        if (held.st_dev, held.st_ino) != (fresh.st_dev, fresh.st_ino):
            raise MigrationError("target_mountpoint_fd_identity_changed")
    finally:
        os.close(fresh_fd)


def _open_home_parent(home: Path, parent: Path) -> tuple[int, int, int]:
    try:
        relative = parent.relative_to(home)
    except ValueError as exc:
        raise MigrationError("quarantine_must_be_under_user_home") from exc
    home_fd = _open_directory(home)
    home_stat = os.fstat(home_fd)
    if home_stat.st_uid != os.geteuid():
        os.close(home_fd)
        raise MigrationError("user_home_not_user_owned")
    current = os.dup(home_fd)
    try:
        for component in relative.parts:
            if component in {"", ".", ".."}:
                raise MigrationError("quarantine_path_invalid")
            nxt = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            if os.fstat(nxt).st_dev != home_stat.st_dev:
                os.close(nxt)
                raise MigrationError("quarantine_nested_mount_rejected")
            os.close(current)
            current = nxt
        return home_fd, current, home_stat.st_dev
    except OSError as exc:
        os.close(current)
        os.close(home_fd)
        raise MigrationError("quarantine_parent_open_failed") from exc
    except BaseException:
        os.close(current)
        os.close(home_fd)
        raise


def _free_bytes(fd: int) -> int:
    values = os.fstatvfs(fd)
    return int(values.f_bavail) * int(values.f_frsize)


def _relative_path(path: str) -> str:
    if path == "":
        return path
    pure = PurePosixPath(path)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts) or "\\" in path:
        raise MigrationError("relative_path_invalid")
    return path


def _exclude_reason(alias: str, relative: str) -> str | None:
    if alias == "owner_work" and relative == "calendar-execution-01":
        return "protected_D43_source_present_uncaptured"
    if alias not in {
        "owner_work",
        "owner_git",
        "research_work",
        "research_git",
        "brain_local_config",
    }:
        return None
    basename = PurePosixPath(relative).name.casefold()
    # Git config may contain credential-bearing remote URLs. Local refs,
    # worktree administration, and object data remain in custody; the two
    # config files are re-created on the destination host.
    if (
        alias in {"owner_git", "research_git"}
        and relative
        in {
            "config",
            "config.worktree",
        }
        or (alias in {"owner_git", "research_git"} and basename == "config.worktree")
    ):
        return "git_remote_configuration_recreate"
    if basename in REISSUE_EXCLUSIONS:
        return REISSUE_EXCLUSIONS[basename]
    if basename.startswith(".env."):
        return "provider_credentials_reissue"
    if basename in {".aws", ".azure"}:
        return "provider_session_reissue"
    if basename in {
        "auth.json",
        "auth.plist",
        "helius-auth.json",
        "helius_auth.json",
        "api-token.json",
        "api_token.json",
        "provider-auth.json",
        "provider_auth.json",
        "credentials.json",
        "secrets.json",
        "tokens.json",
    } or basename.endswith(
        ("-auth.json", "_auth.json", "-token.json", "_token.json", "-credentials.json")
    ):
        return "provider_credentials_reissue"
    if basename in {"session.sqlite", "session.db", "sessions.json", "session.json"}:
        return "provider_session_reissue"
    if alias == "brain_local_config":
        # This entire root is private configuration. Preserve it only as opaque
        # quarantine/reissue custody; never assert that it is credential-free.
        return None
    return None


def _launch_plist_allowed(raw: bytes) -> bool:
    try:
        value = plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError, TypeError):
        return False
    if not isinstance(value, Mapping):
        return False
    label = value.get("Label")
    if not isinstance(label, str) or not label.startswith("ai.memecoin.brain."):
        return False

    def has_sensitive_key(node: Any) -> bool:
        if isinstance(node, Mapping):
            for key, child in node.items():
                folded = str(key).casefold().replace("-", "_")
                if any(part in folded for part in AUTH_KEY_PARTS):
                    return True
                if has_sensitive_key(child):
                    return True
        elif isinstance(node, (list, tuple)):
            return any(has_sensitive_key(child) for child in node)
        return False

    return not has_sensitive_key(value)


def _entry_kind(mode: int) -> str:
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    raise MigrationError("source_special_node_rejected")


def _classify_link_target(link_target: str) -> str:
    """Classify a symlink lexically; never resolve or open its target."""
    if link_target.startswith("/"):
        return "absolute"
    normalized = posixpath.normpath(link_target)
    if normalized == ".." or normalized.startswith("../"):
        return "relative_escape"
    return "relative_within_root"


def _custody_class(relative: str) -> str:
    protected_prefixes = ("f01", "d43", "reserved", "holdout")
    if any(
        any(prefix in part.casefold() for prefix in protected_prefixes)
        for part in PurePosixPath(relative).parts
    ):
        return "protected_path_name_candidate_opaque"
    return "ordinary_opaque"


def _rooted_custody_class(root_path: Path, relative: str) -> str:
    if (
        _custody_class(root_path.name) == "protected_path_name_candidate_opaque"
        or _custody_class(relative) == "protected_path_name_candidate_opaque"
    ):
        return "protected_path_name_candidate_opaque"
    return "ordinary_opaque"


def _record_custody_class(
    alias: str, root_path: Path, relative: str, reason: str | None = None
) -> str:
    if reason == "protected_D43_source_present_uncaptured":
        return "protected_path_name_candidate_opaque"
    return _rooted_custody_class(root_path, relative)


def _stat_sig(st: os.stat_result) -> list[int]:
    return [
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_nlink,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
        st.st_uid,
        st.st_gid,
        getattr(st, "st_flags", 0),
        getattr(st, "st_blocks", 0),
    ]


def _allocation_bytes(st: os.stat_result) -> int:
    return max(int(st.st_size), int(getattr(st, "st_blocks", 0)) * 512)


def _record_hardlink_bytes(
    observed: dict[tuple[int, int], tuple[int, str]],
    st: os.stat_result,
    size: int,
    digest: str,
) -> None:
    if st.st_nlink <= 1:
        return
    key = (int(st.st_dev), int(st.st_ino))
    value = (size, digest)
    previous = observed.setdefault(key, value)
    if previous != value:
        raise MigrationError("hardlink_alias_bytes_disagree")


def _iter_source(
    root_fd: int, alias: str, root_path: Path | None = None
) -> Iterator[tuple[str, str, os.stat_result, str | None]]:
    """Yield deterministic no-follow members; excluded subtrees are not opened."""
    root_st = os.fstat(root_fd)
    root_kind = _entry_kind(root_st.st_mode)
    if root_kind != "directory":
        yield "", root_kind, root_st, None
        return

    def visit(
        directory_fd: int, prefix: str
    ) -> Iterator[tuple[str, str, os.stat_result, str | None]]:
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as exc:
            raise MigrationError("source_directory_read_failed") from exc
        for name in names:
            relative = f"{prefix}/{name}" if prefix else name
            _relative_path(relative)
            if alias == "launch_agents" and any(
                "d43" in part.casefold() for part in PurePosixPath(relative).parts
            ):
                try:
                    st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError as exc:
                    raise MigrationError("source_member_stat_failed") from exc
                yield relative, "excluded", st, "d43_launch_agent_definition_excluded"
                continue
            # Credential/session exclusions apply even below a path-name
            # protected candidate. Custody classification never overrides the
            # explicit reissue boundary.
            reason = _exclude_reason(alias, relative)
            if reason:
                st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                yield relative, "excluded", st, reason
                continue
            try:
                st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise MigrationError("source_member_stat_failed") from exc
            kind = _entry_kind(st.st_mode)
            if alias == "launch_agents":
                if kind == "directory":
                    yield relative, "directory", st, None
                    child = os.open(
                        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd
                    )
                    try:
                        yield from visit(child, relative)
                    finally:
                        os.close(child)
                elif kind == "file" and name.endswith(".plist"):
                    folded_relative = relative.casefold()
                    if folded_relative.startswith("ai.memecoin.d43.") or not PurePosixPath(
                        relative
                    ).name.casefold().startswith("ai.memecoin.brain."):
                        yield relative, "excluded", st, "launch_agent_not_allowlisted"
                        continue
                    fd = os.open(
                        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
                    )
                    try:
                        before = os.fstat(fd)
                        if _stat_sig(before) != _stat_sig(st):
                            raise MigrationError("launch_plist_changed_before_parse")
                        body = _read_fd(fd, max_bytes=4 * MEBI)
                        after = os.fstat(fd)
                        if _stat_sig(before) != _stat_sig(after):
                            raise MigrationError("launch_plist_changed_during_parse")
                        if not _launch_plist_allowed(body):
                            yield (
                                relative,
                                "excluded",
                                st,
                                "launch_agent_not_allowlisted_or_sensitive",
                            )
                        else:
                            yield relative, kind, st, None
                    finally:
                        os.close(fd)
                else:
                    yield relative, "excluded", st, "launch_agent_not_allowlisted"
                continue
            if kind == "directory":
                yield relative, kind, st, None
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd
                )
                try:
                    yield from visit(child, relative)
                finally:
                    os.close(child)
            else:
                yield relative, kind, st, None

    yield "", "directory", root_st, None
    yield from visit(root_fd, "")


def _open_source_root(path: Path) -> tuple[int, os.stat_result]:
    _safe_absolute(path)
    parent = _open_directory(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except OSError as exc:
        os.close(parent)
        raise MigrationError("source_root_open_failed") from exc
    os.close(parent)
    st = os.fstat(fd)
    _entry_kind(st.st_mode)
    return fd, st


def _root_signature(alias: str, path: Path) -> Census:
    fd, root_stat = _open_source_root(path)
    digest = hashlib.sha256()
    count = 0
    total = 0
    logical_total = 0
    protected_d43_excluded_count = 0
    try:
        for relative, kind, st, reason in _iter_source(fd, alias, path):
            row = {"path": relative, "type": kind, "signature": _stat_sig(st), "exclusion": reason}
            digest.update(_canonical(row))
            count += 1
            if reason == "protected_D43_source_present_uncaptured":
                if kind != "excluded" or not stat.S_ISDIR(st.st_mode):
                    raise MigrationError("protected_d43_source_type_invalid")
                protected_d43_excluded_count += 1
            if kind == "file":
                # V1 copies each selected pathname as independent bytes. The
                # census charges every alias; it does not promise topology or
                # custody of aliases outside the selected roots.
                total += _allocation_bytes(st)
                logical_total += int(st.st_size)
            elif kind == "symlink":
                parent_fd = _open_parent_fd_for_relative(fd, relative)
                leaf = PurePosixPath(relative).parts[-1]
                try:
                    before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                    if not stat.S_ISLNK(before.st_mode):
                        raise MigrationError("source_member_changed_type")
                    link_target = os.readlink(leaf, dir_fd=parent_fd)
                    after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
                    if _stat_sig(before) != _stat_sig(after):
                        raise MigrationError("source_symlink_changed_during_census")
                    logical_total += len(os.fsencode(link_target))
                finally:
                    os.close(parent_fd)
        after = os.fstat(fd)
        if _stat_sig(root_stat) != _stat_sig(after):
            raise MigrationError("source_root_changed_during_census")
    finally:
        os.close(fd)
    return Census(count, total, logical_total, protected_d43_excluded_count, digest.hexdigest())


def _copyfile_metadata(src_fd: int, dst_fd: int) -> None:
    if sys.platform != "darwin":
        raise MigrationError("metadata_copy_requires_darwin")
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = library.fcopyfile
    function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    function.restype = ctypes.c_int
    # COPYFILE_METADATA = STAT | ACL | XATTR; DATA is streamed and hashed below.
    if function(src_fd, dst_fd, None, (1 << 1) | (1 << 0) | (1 << 2)) != 0:
        err = ctypes.get_errno()
        raise MigrationError(f"metadata_copy_failed_errno_{err}")


def _copy_regular(
    src_dir_fd: int,
    name: str,
    dst_dir_fd: int,
    dst_name: str,
    progress: Callable[[int], None] | None = None,
) -> tuple[int, str]:
    src_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=src_dir_fd)
    try:
        return _copy_open_regular(src_fd, dst_dir_fd, dst_name, progress)
    finally:
        os.close(src_fd)


def _copy_open_regular(
    src_fd: int,
    dst_dir_fd: int,
    dst_name: str,
    progress: Callable[[int], None] | None = None,
) -> tuple[int, str]:
    before = os.fstat(src_fd)
    if not stat.S_ISREG(before.st_mode):
        raise MigrationError("source_member_changed_type")
    dst_fd = os.open(
        dst_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=dst_dir_fd,
    )
    digest = hashlib.sha256()
    copied = 0
    try:
        while data := os.read(src_fd, CHUNK):
            view = memoryview(data)
            while view:
                written = os.write(dst_fd, view)
                if written <= 0:
                    raise MigrationError("target_write_stalled")
                view = view[written:]
            digest.update(data)
            copied += len(data)
            if progress is not None:
                progress(len(data))
        after = os.fstat(src_fd)
        if _stat_sig(before) != _stat_sig(after) or copied != before.st_size:
            raise MigrationError("source_file_changed_during_copy")
        os.fsync(dst_fd)
        _copyfile_metadata(src_fd, dst_fd)
        os.fsync(dst_fd)
    finally:
        os.close(dst_fd)
    return copied, digest.hexdigest()


def _fsync_directory(fd: int) -> None:
    os.fsync(fd)


def _mkdir_open(parent_fd: int, name: str, mode: int = 0o700) -> int:
    os.mkdir(name, mode, dir_fd=parent_fd)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    _fsync_directory(parent_fd)
    return fd


def _manifest_line(fd: int, record: Mapping[str, Any]) -> None:
    raw = _canonical(dict(record))
    view = memoryview(raw)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise MigrationError("manifest_write_stalled")
        view = view[count:]


def _root_arguments(values: Sequence[str], launch_agents_root: str | None) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for item in values:
        if "=" not in item:
            raise MigrationError("root_argument_must_be_alias_path")
        alias, raw_path = item.split("=", 1)
        if alias not in ROOT_ALIASES or alias == "launch_agents" or alias in roots:
            raise MigrationError("root_alias_invalid_or_duplicate")
        roots[alias] = _safe_absolute(Path(raw_path))
    if launch_agents_root is not None:
        if "launch_agents" in roots:
            raise MigrationError("launch_agents_root_duplicate")
        roots["launch_agents"] = _safe_absolute(Path(launch_agents_root))
    if set(roots) != REQUIRED_ROOT_ALIASES:
        raise MigrationError("required_root_roster_incomplete")
    return roots


def _source_root_record(
    alias: str, path: Path, st: os.stat_result, census: Census
) -> dict[str, Any]:
    return {
        "record_type": "root",
        "alias": alias,
        "source_path": str(path),
        "source_device": st.st_dev,
        "source_inode": st.st_ino,
        "source_kind": "directory" if stat.S_ISDIR(st.st_mode) else "file",
        "custody_class": _custody_class(path.name),
        "inventory_entry_count": census.entry_count,
        "inventory_payload_bytes": census.payload_bytes,
        "inventory_signature_sha256": census.signature_sha256,
    }


def _candidate_parent(target_fd: int, *, remaining_bytes: int) -> tuple[int, int]:
    try:
        migration_fd = os.open(
            MIGRATION_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=target_fd
        )
    except FileNotFoundError:
        _require_free(target_fd, remaining_bytes + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES)
        migration_fd = _mkdir_open(target_fd, MIGRATION_DIR)
    st = os.fstat(migration_fd)
    if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
        os.close(migration_fd)
        raise MigrationError("migration_parent_permissions_invalid")
    try:
        candidates_fd = os.open(
            CANDIDATES_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=migration_fd
        )
    except FileNotFoundError:
        _require_free(target_fd, remaining_bytes + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES)
        candidates_fd = _mkdir_open(migration_fd, CANDIDATES_DIR)
    st = os.fstat(candidates_fd)
    if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
        os.close(candidates_fd)
        os.close(migration_fd)
        raise MigrationError("migration_candidates_permissions_invalid")
    return migration_fd, candidates_fd


def _require_free(target_fd: int, required_bytes: int) -> int:
    available = _free_bytes(target_fd)
    if available < required_bytes:
        raise MigrationError("capacity_floor_failed")
    return available


def capture(
    *,
    roots: Mapping[str, Path],
    target_root: Path,
    expected_uuid: str = EXPECTED_KINGSTON_UUID,
    candidate_id: str | None = None,
    _test_capability: object | None = None,
) -> dict[str, Any]:
    """Create a new candidate. The private injection is test-only, never a CLI option."""
    if set(roots) != REQUIRED_ROOT_ALIASES:
        raise MigrationError("required_root_roster_incomplete")
    census: dict[str, Census] = {}
    root_stats: dict[str, os.stat_result] = {}
    for alias in ROOT_ALIASES:
        fd, st = _open_source_root(roots[alias])
        os.close(fd)
        root_stats[alias] = st
        current = _root_signature(alias, roots[alias])
        census[alias] = current
    payload_bytes = sum(item.payload_bytes for item in census.values())
    logical_payload_bytes = sum(item.logical_payload_bytes for item in census.values())
    if census["owner_work"].protected_d43_excluded_count != 1:
        raise MigrationError("required_protected_d43_gap_root_missing")
    if payload_bytes > PAYLOAD_CAP_BYTES:
        raise MigrationError("payload_cap_exceeded")
    candidate_id = candidate_id or uuid.uuid4().hex
    if not ID_RE.fullmatch(candidate_id):
        raise MigrationError("candidate_id_invalid")

    if _test_capability is _TEST_CAPTURE_CAPABILITY:
        target_fd = _open_directory(target_root)
        pin = _target_pin(target_root)
    else:
        target_fd, pin = _validate_target(target_root, expected_uuid=expected_uuid)
    if pin.volume_uuid.lower() != expected_uuid.lower():
        os.close(target_fd)
        raise MigrationError("target_volume_uuid_mismatch")
    migration_fd = candidates_fd = candidate_fd = payload_fd = -1
    hardlink_bytes: dict[tuple[int, int], tuple[int, str]] = {}
    try:
        last_target_check = 0.0
        bytes_since_target_check = 0

        def target_progress(byte_count: int = 0, *, force: bool = False) -> None:
            nonlocal last_target_check, bytes_since_target_check
            bytes_since_target_check += byte_count
            now = time.monotonic()
            if (
                force
                or bytes_since_target_check >= TARGET_RECHECK_BYTES
                or now - last_target_check >= TARGET_RECHECK_SECONDS
            ):
                _assert_held_target_identity(target_root, target_fd, pin)
                last_target_check = time.monotonic()
                bytes_since_target_check = 0

        required = payload_bytes + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES
        _require_free(target_fd, required)
        target_progress(force=True)
        for st in root_stats.values():
            if (
                st.st_dev == os.fstat(target_fd).st_dev
                and _test_capability is not _TEST_CAPTURE_CAPABILITY
            ):
                raise MigrationError("source_and_target_volume_alias")
        migration_fd, candidates_fd = _candidate_parent(target_fd, remaining_bytes=payload_bytes)
        if candidate_id in os.listdir(candidates_fd):
            raise MigrationError("candidate_already_exists")
        _require_free(target_fd, required)
        candidate_fd = _mkdir_open(candidates_fd, candidate_id)
        started = {
            "schema": SCHEMA,
            "candidate_id": candidate_id,
            "state": "incomplete_until_manifest_published",
            "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "target_volume_uuid": pin.volume_uuid,
            "payload_cap_bytes": PAYLOAD_CAP_BYTES,
        }
        _require_free(target_fd, payload_bytes + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES)
        start_fd = os.open(
            STARTED,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=candidate_fd,
        )
        try:
            os.write(start_fd, _canonical(started))
            os.fsync(start_fd)
        finally:
            os.close(start_fd)
        _require_free(target_fd, payload_bytes + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES)
        payload_fd = _mkdir_open(candidate_fd, "payload")
        _require_free(target_fd, payload_bytes + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES)
        manifest_tmp = ".MANIFEST.pending"
        manifest_fd = os.open(
            manifest_tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=candidate_fd,
        )
        try:
            _manifest_line(
                manifest_fd,
                {
                    "record_type": "header",
                    "schema": SCHEMA,
                    "version": 1,
                    "candidate_id": candidate_id,
                    "candidate_status": "complete",
                    "target_volume_uuid": pin.volume_uuid,
                    "target_device_identifier": pin.device_identifier,
                    "payload_bytes": logical_payload_bytes,
                    "preflight_allocated_bytes": payload_bytes,
                    "free_floor_bytes": FREE_FLOOR_BYTES,
                    "operation_reserve_bytes": OPERATION_RESERVE_BYTES,
                    "payload_cap_bytes": PAYLOAD_CAP_BYTES,
                    "reissue_exclusions": sorted(set(REISSUE_EXCLUSIONS.values())),
                    "root_dispositions": {
                        "brain_local_config": "private_quarantine_reissue_only",
                        "launch_agents": "brain_labels_only_d43_excluded",
                    },
                    "payload_interpretation": "opaque_bytes_custody_only_no_semantic_processing",
                    "protected_path_name_policy": (
                        "case_insensitive_component_substring_candidate_only"
                    ),
                    "protected_custody_disposition": "protected_D43_source_present_uncaptured",
                    "protected_custody_complete": False,
                    "hardlink_policy": "expand_each_path_to_independent_bytes_topology_unpreserved",
                    "hardlink_lineage": "unresolved_external_aliases_not_custodied",
                    "authority": "copy_verify_restore_inert_only",
                    "start_or_activation": False,
                    "source_point_qualified": False,
                },
            )
            remaining = payload_bytes
            for alias in ROOT_ALIASES:
                target_progress(force=True)
                src_fd, src_st = _open_source_root(roots[alias])
                source_root_is_directory = stat.S_ISDIR(src_st.st_mode)
                if source_root_is_directory:
                    _require_free(target_fd, remaining + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES)
                root_parent_fd = (
                    _mkdir_open(payload_fd, alias)
                    if source_root_is_directory
                    else os.dup(payload_fd)
                )
                try:
                    if source_root_is_directory:
                        target_alias_fd = os.open(
                            alias, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=payload_fd
                        )
                        try:
                            _copyfile_metadata(src_fd, target_alias_fd)
                        finally:
                            os.close(target_alias_fd)
                    _manifest_line(
                        manifest_fd,
                        _source_root_record(alias, roots[alias], src_st, census[alias]),
                    )
                    initial = census[alias]
                    current_signature = hashlib.sha256()
                    seen_count = copied_bytes = 0
                    for relative, kind, st, reason in _iter_source(src_fd, alias, roots[alias]):
                        target_progress()
                        sig_row = {
                            "path": relative,
                            "type": kind,
                            "signature": _stat_sig(st),
                            "exclusion": reason,
                        }
                        current_signature.update(_canonical(sig_row))
                        seen_count += 1
                        if kind == "excluded":
                            _manifest_line(
                                manifest_fd,
                                {
                                    "record_type": "excluded",
                                    "alias": alias,
                                    "path": relative,
                                    "reason": reason,
                                    "custody_class": _record_custody_class(
                                        alias, roots[alias], relative, reason
                                    ),
                                },
                            )
                            continue
                        if relative == "" and kind == "directory":
                            _manifest_line(
                                manifest_fd,
                                {
                                    "record_type": "member",
                                    "alias": alias,
                                    "path": "",
                                    "type": "directory",
                                    "size_bytes": 0,
                                    "sha256": None,
                                    "custody_class": _rooted_custody_class(roots[alias], relative),
                                },
                            )
                            continue
                        if relative == "" and kind == "file":
                            if (
                                _free_bytes(target_fd)
                                < remaining + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES
                            ):
                                raise MigrationError("capacity_floor_failed_before_member")
                            size, digest = _copy_open_regular(
                                src_fd, payload_fd, alias, target_progress
                            )
                            _record_hardlink_bytes(hardlink_bytes, st, size, digest)
                            copied_bytes += size
                            remaining -= _allocation_bytes(src_st)
                            _require_free(
                                target_fd, remaining + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES
                            )
                            _manifest_line(
                                manifest_fd,
                                {
                                    "record_type": "member",
                                    "alias": alias,
                                    "path": "",
                                    "type": "file",
                                    "size_bytes": size,
                                    "sha256": digest,
                                    "custody_class": _rooted_custody_class(roots[alias], relative),
                                },
                            )
                            continue
                        parent_fd = root_parent_fd
                        owned_fds: list[int] = []
                        parts = PurePosixPath(relative).parts
                        for part in parts[:-1]:
                            try:
                                next_fd = os.open(
                                    part,
                                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=parent_fd,
                                )
                            except FileNotFoundError:
                                _require_free(
                                    target_fd,
                                    remaining + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES,
                                )
                                next_fd = _mkdir_open(parent_fd, part)
                            if parent_fd != root_parent_fd:
                                pass
                            owned_fds.append(next_fd)
                            parent_fd = next_fd
                        leaf = parts[-1]
                        try:
                            if kind == "directory":
                                _require_free(
                                    target_fd,
                                    remaining + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES,
                                )
                                child_fd = _mkdir_open(parent_fd, leaf)
                                source_parent_fd = _open_parent_fd_for_relative(src_fd, relative)
                                try:
                                    source_child_fd = os.open(
                                        leaf,
                                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=source_parent_fd,
                                    )
                                    try:
                                        _copyfile_metadata(source_child_fd, child_fd)
                                    finally:
                                        os.close(source_child_fd)
                                finally:
                                    os.close(source_parent_fd)
                                    os.close(child_fd)
                                _manifest_line(
                                    manifest_fd,
                                    {
                                        "record_type": "member",
                                        "alias": alias,
                                        "path": relative,
                                        "type": kind,
                                        "size_bytes": 0,
                                        "sha256": None,
                                        "custody_class": _rooted_custody_class(
                                            roots[alias], relative
                                        ),
                                    },
                                )
                            elif kind == "file":
                                if (
                                    _free_bytes(target_fd)
                                    < remaining + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES
                                ):
                                    raise MigrationError("capacity_floor_failed_before_member")
                                source_parent_fd = _open_parent_fd_for_relative(src_fd, relative)
                                try:
                                    read_bytes, digest = _copy_regular(
                                        source_parent_fd,
                                        leaf,
                                        parent_fd,
                                        leaf,
                                        target_progress,
                                    )
                                    _record_hardlink_bytes(hardlink_bytes, st, read_bytes, digest)
                                finally:
                                    os.close(source_parent_fd)
                                copied_bytes += read_bytes
                                remaining -= _allocation_bytes(st)
                                _require_free(
                                    target_fd,
                                    remaining + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES,
                                )
                                _manifest_line(
                                    manifest_fd,
                                    {
                                        "record_type": "member",
                                        "alias": alias,
                                        "path": relative,
                                        "type": kind,
                                        "size_bytes": read_bytes,
                                        "sha256": digest,
                                        "custody_class": _rooted_custody_class(
                                            roots[alias], relative
                                        ),
                                    },
                                )
                            elif kind == "symlink":
                                _require_free(
                                    target_fd,
                                    remaining + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES,
                                )
                                source_parent_fd = _open_parent_fd_for_relative(src_fd, relative)
                                try:
                                    source_before = os.stat(
                                        leaf, dir_fd=source_parent_fd, follow_symlinks=False
                                    )
                                    if not stat.S_ISLNK(source_before.st_mode):
                                        raise MigrationError("source_member_changed_type")
                                    link_target = os.readlink(leaf, dir_fd=source_parent_fd)
                                    source_after = os.stat(
                                        leaf, dir_fd=source_parent_fd, follow_symlinks=False
                                    )
                                    if _stat_sig(source_before) != _stat_sig(source_after):
                                        raise MigrationError("source_symlink_changed_during_read")
                                finally:
                                    os.close(source_parent_fd)
                                os.symlink(link_target, leaf, dir_fd=parent_fd)
                                _fsync_directory(parent_fd)
                                _require_free(
                                    target_fd,
                                    remaining + FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES,
                                )
                                encoded = os.fsencode(link_target)
                                _manifest_line(
                                    manifest_fd,
                                    {
                                        "record_type": "member",
                                        "alias": alias,
                                        "path": relative,
                                        "type": kind,
                                        "size_bytes": len(encoded),
                                        "sha256": hashlib.sha256(encoded).hexdigest(),
                                        "link_target": link_target,
                                        "link_class": _classify_link_target(link_target),
                                        "custody_class": _rooted_custody_class(
                                            roots[alias], relative
                                        ),
                                    },
                                )
                            else:
                                raise MigrationError("source_special_node_rejected")
                        finally:
                            for fd in reversed(owned_fds):
                                os.close(fd)
                    if (
                        seen_count != initial.entry_count
                        or current_signature.hexdigest() != initial.signature_sha256
                    ):
                        raise MigrationError("source_changed_since_preflight")
                    _fsync_directory(root_parent_fd)
                finally:
                    os.close(root_parent_fd)
                    os.close(src_fd)
            if remaining != 0:
                raise MigrationError("copied_payload_total_mismatch")
            for alias in ROOT_ALIASES:
                final = _root_signature(alias, roots[alias])
                if final != census[alias]:
                    raise MigrationError("source_changed_during_capture")
            target_progress(force=True)
            os.fsync(manifest_fd)
        finally:
            os.close(manifest_fd)

        if _free_bytes(target_fd) < FREE_FLOOR_BYTES + OPERATION_RESERVE_BYTES:
            raise MigrationError("capacity_floor_failed_after_capture")
        os.close(payload_fd)
        payload_fd = -1
        _fsync_directory(candidate_fd)
        # Verify staged bytes before publishing the accepted manifest.
        target_progress(force=True)
        pending_path = target_root / MIGRATION_DIR / CANDIDATES_DIR / candidate_id / manifest_tmp
        _verify_candidate_path(
            pending_path,
            expected_sha256=None,
            allow_pending=True,
            expected_target_uuid=pin.volume_uuid,
        )
        os.link(
            manifest_tmp,
            MANIFEST,
            src_dir_fd=candidate_fd,
            dst_dir_fd=candidate_fd,
            follow_symlinks=False,
        )
        os.fsync(candidate_fd)
        target_progress(force=True)
        accepted = target_root / MIGRATION_DIR / CANDIDATES_DIR / candidate_id / MANIFEST
        result = _verify_candidate_path(
            accepted, expected_sha256=None, expected_target_uuid=pin.volume_uuid
        )
        target_progress(force=True)
        return {
            "candidate_id": candidate_id,
            "manifest_sha256": result["manifest_sha256"],
            "member_count": result["member_count"],
            "payload_bytes": logical_payload_bytes,
            "protected_custody_disposition": "protected_D43_source_present_uncaptured",
            "protected_custody_complete": False,
            "state": "verified_inert_candidate",
        }
    finally:
        for fd in (payload_fd, candidate_fd, candidates_fd, migration_fd, target_fd):
            if fd >= 0:
                os.close(fd)


def _open_parent_fd_for_relative(root_fd: int, relative: str) -> int:
    parts = PurePosixPath(relative).parts
    current = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = nxt
        return current
    except BaseException:
        os.close(current)
        raise


def _manifest_digest(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise MigrationError("manifest_file_invalid")
        digest = hashlib.sha256()
        while chunk := os.read(fd, CHUNK):
            digest.update(chunk)
        after = os.fstat(fd)
        if _stat_sig(before) != _stat_sig(after):
            raise MigrationError("manifest_changed_during_hash")
        return digest.hexdigest()
    finally:
        os.close(fd)


def _manifest_records(path: Path, expected_sha256: str) -> Iterator[dict[str, Any]]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise MigrationError("manifest_file_invalid")
        digest = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=True) as stream:
            fd = -1
            for line in stream:
                digest.update(line)
                try:
                    row = json.loads(line.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise MigrationError("manifest_json_invalid") from exc
                if not isinstance(row, dict) or _canonical(row) != line:
                    raise MigrationError("manifest_line_not_canonical")
                yield row
            after = os.fstat(stream.fileno())
            if _stat_sig(before) != _stat_sig(after) or digest.hexdigest() != expected_sha256:
                raise MigrationError("manifest_changed_during_parse")
    finally:
        if fd >= 0:
            os.close(fd)


def _read_manifest(
    path: Path, expected_sha256: str | None, *, allow_pending: bool = False
) -> tuple[dict[str, Any], Iterator[dict[str, Any]], str]:
    if expected_sha256 is not None and not SHA_RE.fullmatch(expected_sha256):
        raise MigrationError("external_manifest_pin_invalid")
    try:
        digest = _manifest_digest(path)
    except OSError as exc:
        raise MigrationError("manifest_open_failed") from exc
    if expected_sha256 is not None and digest != expected_sha256:
        raise MigrationError("external_manifest_pin_mismatch")
    iterator = _manifest_records(path, digest)
    try:
        header = next(iterator)
    except StopIteration as exc:
        raise MigrationError("manifest_header_missing") from exc
    if header.get("record_type") != "header":
        raise MigrationError("manifest_header_missing")
    if (
        header.get("schema") != SCHEMA
        or header.get("version") != 1
        or header.get("candidate_status") != "complete"
    ):
        raise MigrationError("manifest_schema_or_state_invalid")
    if (
        header.get("start_or_activation") is not False
        or header.get("authority") != "copy_verify_restore_inert_only"
    ):
        raise MigrationError("manifest_authority_invalid")
    return header, iterator, digest


def _hash_target_member(
    root_fd: int, relative: str, kind: str
) -> tuple[int, str | None, str | None]:
    parent_fd = _open_parent_fd_for_relative(root_fd, relative)
    leaf = PurePosixPath(relative).parts[-1]
    try:
        st = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        actual_kind = _entry_kind(st.st_mode)
        if actual_kind != kind:
            raise MigrationError("candidate_member_type_mismatch")
        if kind == "directory":
            return 0, None, None
        if kind == "symlink":
            target = os.readlink(leaf, dir_fd=parent_fd)
            payload = os.fsencode(target)
            return len(payload), hashlib.sha256(payload).hexdigest(), target
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        try:
            before = os.fstat(fd)
            digest = hashlib.sha256()
            size = 0
            while chunk := os.read(fd, CHUNK):
                digest.update(chunk)
                size += len(chunk)
            after = os.fstat(fd)
            if _stat_sig(before) != _stat_sig(after) or size != before.st_size:
                raise MigrationError("candidate_member_changed_during_verify")
            return size, digest.hexdigest(), None
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def _candidate_payload_fd(candidate_fd: int) -> int:
    return os.open("payload", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=candidate_fd)


def _verify_candidate_path(
    manifest_path: Path,
    expected_sha256: str | None,
    *,
    allow_pending: bool = False,
    expected_target_uuid: str | None = None,
) -> dict[str, Any]:
    if manifest_path.name not in ({MANIFEST, ".MANIFEST.pending"} if allow_pending else {MANIFEST}):
        raise MigrationError("manifest_path_invalid")
    candidate_dir = manifest_path.parent
    candidate_fd = _open_directory(candidate_dir)
    try:
        started_fd = os.open(STARTED, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=candidate_fd)
        try:
            started = json.loads(_read_fd(started_fd, max_bytes=64 * 1024).decode())
        finally:
            os.close(started_fd)
        if started.get("state") != "incomplete_until_manifest_published":
            raise MigrationError("capture_start_marker_invalid")
        if started.get("candidate_id") != candidate_dir.name:
            raise MigrationError("capture_start_identity_mismatch")
        header, records, manifest_sha = _read_manifest(
            manifest_path, expected_sha256, allow_pending=allow_pending
        )
        if header.get("candidate_id") != candidate_dir.name:
            raise MigrationError("manifest_candidate_identity_mismatch")
        if (
            expected_target_uuid is not None
            and header.get("target_volume_uuid", "").lower() != expected_target_uuid.lower()
        ):
            raise MigrationError("manifest_target_identity_mismatch")
        if (
            expected_target_uuid is not None
            and started.get("target_volume_uuid", "").lower() != expected_target_uuid.lower()
        ):
            raise MigrationError("capture_start_target_identity_mismatch")
        payload_fd = _candidate_payload_fd(candidate_fd)
        try:
            actual_iter = iter(_walk_target_members(payload_fd))
            member_count = 0
            root_aliases: list[str] = []
            active_alias: str | None = None
            last_locator_key: tuple[str, ...] | None = None
            logical_payload_bytes = 0
            header_fields = {
                "record_type",
                "schema",
                "version",
                "candidate_id",
                "candidate_status",
                "target_volume_uuid",
                "target_device_identifier",
                "payload_bytes",
                "preflight_allocated_bytes",
                "free_floor_bytes",
                "operation_reserve_bytes",
                "payload_cap_bytes",
                "reissue_exclusions",
                "root_dispositions",
                "payload_interpretation",
                "protected_path_name_policy",
                "protected_custody_disposition",
                "protected_custody_complete",
                "hardlink_policy",
                "hardlink_lineage",
                "authority",
                "start_or_activation",
                "source_point_qualified",
            }
            if set(header) != header_fields:
                raise MigrationError("manifest_header_fields_invalid")
            for name in (
                "payload_bytes",
                "preflight_allocated_bytes",
                "free_floor_bytes",
                "operation_reserve_bytes",
                "payload_cap_bytes",
            ):
                if type(header.get(name)) is not int or header[name] < 0:
                    raise MigrationError("manifest_header_byte_field_invalid")
            if header["free_floor_bytes"] != FREE_FLOOR_BYTES:
                raise MigrationError("manifest_free_floor_invalid")
            if header["operation_reserve_bytes"] != OPERATION_RESERVE_BYTES:
                raise MigrationError("manifest_operation_reserve_invalid")
            if header["payload_cap_bytes"] != PAYLOAD_CAP_BYTES:
                raise MigrationError("manifest_payload_cap_invalid")
            if header.get("root_dispositions") != {
                "brain_local_config": "private_quarantine_reissue_only",
                "launch_agents": "brain_labels_only_d43_excluded",
            }:
                raise MigrationError("manifest_root_dispositions_invalid")
            if (
                header.get("payload_interpretation")
                != "opaque_bytes_custody_only_no_semantic_processing"
                or header.get("protected_path_name_policy")
                != "case_insensitive_component_substring_candidate_only"
            ):
                raise MigrationError("manifest_custody_policy_invalid")
            if (
                header.get("protected_custody_disposition")
                != "protected_D43_source_present_uncaptured"
                or header.get("protected_custody_complete") is not False
                or header.get("hardlink_policy")
                != "expand_each_path_to_independent_bytes_topology_unpreserved"
                or header.get("hardlink_lineage") != "unresolved_external_aliases_not_custodied"
            ):
                raise MigrationError("manifest_protected_or_hardlink_disposition_invalid")
            source_roots: dict[str, Path] = {}
            protected_d43_excluded_count = 0
            for row in records:
                record_type = row.get("record_type")
                if record_type == "root":
                    if set(row) != {
                        "record_type",
                        "alias",
                        "source_path",
                        "source_device",
                        "source_inode",
                        "source_kind",
                        "custody_class",
                        "inventory_entry_count",
                        "inventory_payload_bytes",
                        "inventory_signature_sha256",
                    }:
                        raise MigrationError("manifest_root_fields_invalid")
                    alias = row.get("alias")
                    expected_alias = (
                        ROOT_ALIASES[len(root_aliases)]
                        if len(root_aliases) < len(ROOT_ALIASES)
                        else None
                    )
                    if alias != expected_alias:
                        raise MigrationError("manifest_root_roster_or_order_invalid")
                    if (
                        not isinstance(row.get("source_path"), str)
                        or type(row.get("source_device")) is not int
                        or type(row.get("source_inode")) is not int
                        or row.get("source_kind") not in {"directory", "file"}
                        or type(row.get("inventory_entry_count")) is not int
                        or row["inventory_entry_count"] < 1
                        or type(row.get("inventory_payload_bytes")) is not int
                        or row["inventory_payload_bytes"] < 0
                        or not isinstance(row.get("inventory_signature_sha256"), str)
                        or not SHA_RE.fullmatch(row["inventory_signature_sha256"])
                        or row.get("custody_class") != _custody_class(Path(row["source_path"]).name)
                    ):
                        raise MigrationError("manifest_root_value_invalid")
                    root_aliases.append(alias)
                    source_roots[alias] = Path(row["source_path"])
                    active_alias = alias
                    last_locator_key = None
                    continue
                if record_type not in {"member", "excluded"}:
                    raise MigrationError("manifest_record_type_invalid")
                alias, relative = row.get("alias"), row.get("path")
                if (
                    alias not in REQUIRED_ROOT_ALIASES
                    or not isinstance(relative, str)
                    or alias != active_alias
                ):
                    raise MigrationError("manifest_record_locator_invalid")
                _relative_path(relative)
                locator_key = tuple(PurePosixPath(relative).parts)
                if last_locator_key is not None and locator_key <= last_locator_key:
                    raise MigrationError("manifest_record_order_or_duplicate_invalid")
                last_locator_key = locator_key
                if record_type == "excluded":
                    if set(row) != {
                        "record_type",
                        "alias",
                        "path",
                        "reason",
                        "custody_class",
                    }:
                        raise MigrationError("manifest_excluded_fields_invalid")
                    if not isinstance(row.get("reason"), str) or not row["reason"]:
                        raise MigrationError("manifest_excluded_reason_invalid")
                    if row.get("custody_class") != _rooted_custody_class(
                        source_roots[alias], relative
                    ):
                        if not (
                            alias == "owner_work"
                            and relative == "calendar-execution-01"
                            and row.get("reason") == "protected_D43_source_present_uncaptured"
                            and row.get("custody_class") == "protected_path_name_candidate_opaque"
                        ):
                            raise MigrationError("manifest_excluded_custody_class_invalid")
                    if row.get("reason") == "protected_D43_source_present_uncaptured":
                        if alias != "owner_work" or relative != "calendar-execution-01":
                            raise MigrationError("manifest_protected_d43_locator_invalid")
                        protected_d43_excluded_count += 1
                    continue
                kind = row.get("type")
                expected_fields = {
                    "record_type",
                    "alias",
                    "path",
                    "type",
                    "size_bytes",
                    "sha256",
                    "custody_class",
                }
                if kind == "symlink":
                    expected_fields |= {"link_target", "link_class"}
                if kind not in {"directory", "file", "symlink"} or set(row) != expected_fields:
                    raise MigrationError("manifest_member_fields_invalid")
                if row.get("custody_class") != _rooted_custody_class(source_roots[alias], relative):
                    raise MigrationError("manifest_member_custody_class_invalid")
                if type(row.get("size_bytes")) is not int or row["size_bytes"] < 0:
                    raise MigrationError("manifest_member_size_invalid")
                if kind == "directory":
                    if row["size_bytes"] != 0 or row.get("sha256") is not None:
                        raise MigrationError("manifest_directory_digest_invalid")
                elif kind == "file":
                    if not isinstance(row.get("sha256"), str) or not SHA_RE.fullmatch(
                        row["sha256"]
                    ):
                        raise MigrationError("manifest_file_digest_invalid")
                    logical_payload_bytes += row["size_bytes"]
                else:
                    if (
                        not isinstance(row.get("link_target"), str)
                        or row.get("link_class") != _classify_link_target(row["link_target"])
                        or not isinstance(row.get("sha256"), str)
                        or not SHA_RE.fullmatch(row["sha256"])
                    ):
                        raise MigrationError("manifest_symlink_fields_invalid")
                    logical_payload_bytes += row["size_bytes"]
                try:
                    actual_alias, actual_relative, actual_kind = next(actual_iter)
                except StopIteration as exc:
                    raise MigrationError("candidate_membership_missing") from exc
                if (actual_alias, actual_relative, actual_kind) != (alias, relative, kind):
                    raise MigrationError("candidate_membership_order_or_type_mismatch")
                size, digest, target = _hash_target_member(
                    payload_fd, f"{alias}/{relative}" if relative else alias, kind
                )
                if size != row.get("size_bytes") or digest != row.get("sha256"):
                    raise MigrationError("candidate_member_digest_mismatch")
                if kind == "symlink" and target != row.get("link_target"):
                    raise MigrationError("candidate_symlink_target_mismatch")
                if kind == "symlink" and row.get("link_class") != _classify_link_target(
                    target or ""
                ):
                    raise MigrationError("candidate_symlink_class_mismatch")
                member_count += 1
            try:
                next(actual_iter)
            except StopIteration:
                pass
            else:
                raise MigrationError("candidate_membership_extra")
            if root_aliases != list(ROOT_ALIASES):
                raise MigrationError("manifest_root_roster_incomplete")
            if protected_d43_excluded_count != 1:
                raise MigrationError("manifest_protected_d43_gap_root_incomplete")
            if logical_payload_bytes != header["payload_bytes"]:
                raise MigrationError("manifest_logical_payload_bytes_mismatch")
        finally:
            os.close(payload_fd)
        return {
            "manifest_sha256": manifest_sha,
            "member_count": member_count,
            "payload_bytes": logical_payload_bytes,
            "protected_custody_disposition": "protected_D43_source_present_uncaptured",
            "protected_custody_complete": False,
        }
    finally:
        os.close(candidate_fd)


def _walk_target_members(payload_fd: int) -> Iterator[tuple[str, str, str]]:
    aliases = os.listdir(payload_fd)
    if set(aliases) != REQUIRED_ROOT_ALIASES or len(aliases) != len(REQUIRED_ROOT_ALIASES):
        raise MigrationError("candidate_root_roster_mismatch")
    for alias in ROOT_ALIASES:
        root_st = os.stat(alias, dir_fd=payload_fd, follow_symlinks=False)
        root_kind = _entry_kind(root_st.st_mode)
        yield alias, "", root_kind
        if root_kind != "directory":
            continue
        root_fd = os.open(alias, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=payload_fd)
        try:

            def visit(
                fd: int, prefix: str, root_alias: str = alias
            ) -> Iterator[tuple[str, str, str]]:
                for name in sorted(os.listdir(fd)):
                    rel = f"{prefix}/{name}" if prefix else name
                    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    kind = _entry_kind(st.st_mode)
                    yield root_alias, rel, kind
                    if kind == "directory":
                        child = os.open(
                            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                        )
                        try:
                            yield from visit(child, rel, root_alias)
                        finally:
                            os.close(child)

            yield from visit(root_fd, "")
        finally:
            os.close(root_fd)


def verify(
    *, target_root: Path, candidate_id: str, expected_manifest_sha256: str
) -> dict[str, Any]:
    if not ID_RE.fullmatch(candidate_id):
        raise MigrationError("candidate_id_invalid")
    target_fd, pin = _validate_target(target_root)
    try:
        _assert_held_target_identity(target_root, target_fd, pin)
        candidate = target_root / MIGRATION_DIR / CANDIDATES_DIR / candidate_id / MANIFEST
        result = _verify_candidate_path(
            candidate, expected_manifest_sha256, expected_target_uuid=pin.volume_uuid
        )
        _assert_held_target_identity(target_root, target_fd, pin)
        return result
    finally:
        os.close(target_fd)


def restore_inert(
    *,
    target_root: Path,
    candidate_id: str,
    expected_manifest_sha256: str,
    quarantine_root: Path,
    _test_capability: object | None = None,
) -> dict[str, Any]:
    if not quarantine_root.is_absolute() or ".." in quarantine_root.parts:
        raise MigrationError("quarantine_path_invalid")
    verified = verify(
        target_root=target_root,
        candidate_id=candidate_id,
        expected_manifest_sha256=expected_manifest_sha256,
    )
    candidate_manifest = target_root / MIGRATION_DIR / CANDIDATES_DIR / candidate_id / MANIFEST
    home = Path.home()
    try:
        relative_quarantine = quarantine_root.relative_to(home)
    except ValueError as exc:
        raise MigrationError("quarantine_must_be_under_user_home") from exc
    if not relative_quarantine.parts or any(
        part.casefold() in {"kingston", "braindata", "personalbackup", "personal-backup"}
        for part in relative_quarantine.parts
    ):
        raise MigrationError("quarantine_location_forbidden")
    home_fd, parent_fd, home_device = _open_home_parent(home, quarantine_root.parent)
    target_fd = -1
    try:
        target_fd, target_pin = _validate_target(target_root)
        if os.fstat(parent_fd).st_uid != os.geteuid():
            raise MigrationError("quarantine_parent_not_user_owned")
        if os.fstat(parent_fd).st_dev != home_device:
            raise MigrationError("quarantine_nested_mount_rejected")
        if (
            os.fstat(parent_fd).st_dev == os.fstat(target_fd).st_dev
            and _test_capability is not _TEST_CAPTURE_CAPABILITY
        ):
            raise MigrationError("quarantine_must_be_off_kingston_volume")
        try:
            os.stat(quarantine_root.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise MigrationError("quarantine_must_be_new")
        required = int(verified["payload_bytes"]) + RESTORE_OPERATION_RESERVE_BYTES
        _require_free(parent_fd, required)
        _assert_held_target_identity(target_root, target_fd, target_pin)
        quarantine_fd = _mkdir_open(parent_fd, quarantine_root.name, 0o700)
    finally:
        if target_fd >= 0:
            os.close(target_fd)
        os.close(parent_fd)
        os.close(home_fd)
    _header, records, _digest = _read_manifest(candidate_manifest, expected_manifest_sha256)
    candidate_root_fd = _open_directory(candidate_manifest.parent)
    candidate_payload_fd = _candidate_payload_fd(candidate_root_fd)
    try:
        restored_payload_fd = _mkdir_open(quarantine_fd, "payload")
        try:
            for row in records:
                if row.get("record_type") != "member":
                    continue
                alias, relative, kind = row["alias"], row["path"], row["type"]
                parts = (alias, *PurePosixPath(relative).parts) if relative else (alias,)
                parent_fd = restored_payload_fd
                opened_parents: list[int] = []
                for part in parts[:-1]:
                    try:
                        next_fd = os.open(
                            part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
                        )
                    except FileNotFoundError:
                        next_fd = _mkdir_open(parent_fd, part)
                    opened_parents.append(next_fd)
                    parent_fd = next_fd
                leaf = parts[-1]
                try:
                    if kind == "directory" and relative == "":
                        _mkdir_open(restored_payload_fd, alias)
                        continue
                    src_parent = _open_parent_fd_for_relative(
                        candidate_payload_fd, f"{alias}/{relative}" if relative else alias
                    )
                    try:
                        src_name = PurePosixPath(
                            f"{alias}/{relative}" if relative else alias
                        ).parts[-1]
                        if kind == "directory":
                            if relative == "":
                                continue
                            _mkdir_open(parent_fd, leaf)
                        elif kind == "symlink":
                            target = os.readlink(src_name, dir_fd=src_parent)
                            os.symlink(target, leaf, dir_fd=parent_fd)
                        else:
                            src_fd = os.open(
                                src_name,
                                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                dir_fd=src_parent,
                            )
                            dst_fd = os.open(
                                leaf,
                                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                0o600,
                                dir_fd=parent_fd,
                            )
                            try:
                                digest = hashlib.sha256()
                                size = 0
                                while block := os.read(src_fd, CHUNK):
                                    digest.update(block)
                                    size += len(block)
                                    view = memoryview(block)
                                    while view:
                                        written = os.write(dst_fd, view)
                                        view = view[written:]
                                os.fsync(dst_fd)
                                if size != row["size_bytes"] or digest.hexdigest() != row["sha256"]:
                                    raise MigrationError("restored_file_digest_mismatch")
                            finally:
                                os.close(src_fd)
                                os.close(dst_fd)
                    finally:
                        os.close(src_parent)
                finally:
                    for opened_fd in reversed(opened_parents):
                        os.close(opened_fd)
            manifest_copy = os.open(
                "MIGRATION_MANIFEST.jsonl",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=quarantine_fd,
            )
            try:
                src = os.open(MANIFEST, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=candidate_root_fd)
                try:
                    source_digest = hashlib.sha256()
                    while block := os.read(src, CHUNK):
                        source_digest.update(block)
                        view = memoryview(block)
                        while view:
                            written = os.write(manifest_copy, view)
                            if written <= 0:
                                raise MigrationError("restored_manifest_short_write")
                            view = view[written:]
                    if source_digest.hexdigest() != expected_manifest_sha256:
                        raise MigrationError("source_manifest_digest_changed_during_restore")
                    os.fsync(manifest_copy)
                finally:
                    os.close(src)
            finally:
                os.close(manifest_copy)
            manifest_readback = os.open(
                "MIGRATION_MANIFEST.jsonl", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=quarantine_fd
            )
            try:
                restored_manifest_digest = hashlib.sha256()
                while block := os.read(manifest_readback, CHUNK):
                    restored_manifest_digest.update(block)
                if restored_manifest_digest.hexdigest() != expected_manifest_sha256:
                    raise MigrationError("restored_manifest_readback_mismatch")
            finally:
                os.close(manifest_readback)
        finally:
            os.close(restored_payload_fd)
        os.fsync(quarantine_fd)
        _header, verification_records, _digest = _read_manifest(
            candidate_manifest, expected_manifest_sha256
        )
        restored = _verify_restored(quarantine_fd, verification_records)
        _revalidate_target(target_root, target_pin)
        return {
            "candidate_id": candidate_id,
            "manifest_sha256": verified["manifest_sha256"],
            "member_count": restored,
            "state": "inert_quarantine_verified",
            "start_or_activation": False,
            "protected_custody_disposition": verified["protected_custody_disposition"],
            "protected_custody_complete": verified["protected_custody_complete"],
        }
    finally:
        os.close(candidate_payload_fd)
        os.close(candidate_root_fd)
        os.close(quarantine_fd)


def _verify_restored(quarantine_fd: int, records: Iterator[Mapping[str, Any]]) -> int:
    payload_fd = os.open(
        "payload", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=quarantine_fd
    )
    try:
        actual_iter = iter(_walk_target_members(payload_fd))
        member_count = 0
        for row in records:
            if row.get("record_type") != "member":
                continue
            alias, relative, kind = row["alias"], row["path"], row["type"]
            try:
                actual_alias, actual_relative, actual_kind = next(actual_iter)
            except StopIteration as exc:
                raise MigrationError("restored_member_missing") from exc
            if (actual_alias, actual_relative, actual_kind) != (alias, relative, kind):
                raise MigrationError("restored_membership_order_or_type_mismatch")
            path = f"{alias}/{relative}" if relative else alias
            size, digest, target = _hash_target_member(payload_fd, path, kind)
            if size != row["size_bytes"] or digest != row["sha256"]:
                raise MigrationError("restored_member_digest_mismatch")
            if kind == "symlink" and target != row.get("link_target"):
                raise MigrationError("restored_symlink_mismatch")
            member_count += 1
        try:
            next(actual_iter)
        except StopIteration:
            pass
        else:
            raise MigrationError("restored_member_extra")
        if stat.S_IMODE(os.fstat(quarantine_fd).st_mode) != 0o700:
            raise MigrationError("quarantine_permissions_invalid")
        return member_count
    finally:
        os.close(payload_fd)


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture", help="create and verify a new migration candidate")
    capture_parser.add_argument("--root", action="append", default=[], metavar="ALIAS=/ABS/PATH")
    capture_parser.add_argument("--launch-agents-root", required=True)
    capture_parser.add_argument("--target-root", type=Path, required=True)
    verify_parser = sub.add_parser("verify", help="independently verify a candidate read-only")
    verify_parser.add_argument("--target-root", type=Path, required=True)
    verify_parser.add_argument("--candidate-id", required=True)
    verify_parser.add_argument("--manifest-sha256", required=True)
    restore_parser = sub.add_parser(
        "restore-inert", help="restore bytes into a new inert quarantine"
    )
    restore_parser.add_argument("--target-root", type=Path, required=True)
    restore_parser.add_argument("--candidate-id", required=True)
    restore_parser.add_argument("--manifest-sha256", required=True)
    restore_parser.add_argument("--quarantine-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            roots = _root_arguments(args.root, args.launch_agents_root)
            result = capture(roots=roots, target_root=args.target_root)
        elif args.command == "verify":
            result = verify(
                target_root=args.target_root,
                candidate_id=args.candidate_id,
                expected_manifest_sha256=args.manifest_sha256,
            )
        elif args.command == "restore-inert":
            result = restore_inert(
                target_root=args.target_root,
                candidate_id=args.candidate_id,
                expected_manifest_sha256=args.manifest_sha256,
                quarantine_root=args.quarantine_root,
            )
        else:
            raise MigrationError("unsupported_command")
    except MigrationError as exc:
        print(
            json.dumps({"state": "failed_closed", "error": str(exc)}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
