"""Offline, create-only staging and verification for approved closed research roots.

The caller must establish that a root is closed before staging. This module never
removes source data and does not contact providers or research runtime services.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, cast

CHUNK_SIZE = 8 * 1024 * 1024
LEGACY_MANIFEST_VERSION = 2
MANIFEST_VERSION = 3
# The names are intentionally narrow. Callers must still provide the exact expected
# source path for the selected group; this is not a general-purpose directory copier.
PROTECTED_PARTS = frozenset({"f01", "d43", "holdout", "reserved", "protected"})
PRESERVED_METADATA = [
    "relative_path",
    "file_type",
    "file_size",
    "sha256_content",
    "permission_mode",
    "mtime_ns",
    "extended_attribute_values_sha256",
    "symlink_target",
    "in_root_hardlink_groups",
]
NOT_PRESERVED_METADATA = ["uid_gid", "ctime_ns", "atime_ns"]
REQUIRED_ABSENT_METADATA = ["extended_acl", "bsd_flags"]
WRITER_LOCK_NAMES = frozenset(
    {".research-storage.lock", ".writer.lock", ".collector.lock", ".campaign.lock"}
)


class MigrationError(RuntimeError):
    """A migration precondition or exact-copy verification failed."""


def _fail(message: str) -> NoReturn:
    raise MigrationError(message)


def _plain_absolute(path: Path, label: str) -> Path:
    if not path.is_absolute() or "\x00" in str(path) or ".." in path.parts:
        _fail(f"invalid_{label}_path")
    normalized = Path(os.path.abspath(path))
    cursor = Path(normalized.anchor)
    for part in normalized.parts[1:]:
        cursor /= part
        try:
            st = cursor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(st.st_mode):
            _fail(f"symlink_{label}_path")
    return normalized


def _validate_source(source: Path, expected: Path, group: str) -> Path:
    source = _plain_absolute(source, "source")
    expected = _plain_absolute(expected, "expected_source")
    if source != expected:
        _fail("source_does_not_match_expected_root")
    if source == Path(source.anchor):
        _fail("filesystem_root_rejected")
    if not source.is_dir():
        _fail("source_root_missing")
    if _is_protected_name(source.name):
        _fail("protected_source_path")
    if group != source.name:
        _fail("group_must_match_source_basename")
    return source


def _validate_destination(external_root: Path, destination: Path) -> tuple[Path, Path]:
    external_root = _plain_absolute(external_root, "external_root")
    destination = _plain_absolute(destination, "destination")
    if not external_root.is_dir():
        _fail("external_root_missing")
    try:
        relative = destination.relative_to(external_root)
    except ValueError as exc:
        raise MigrationError("destination_outside_external_root") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        _fail("invalid_destination_relative_path")
    if any(_is_protected_name(part) for part in relative.parts):
        _fail("protected_destination_path")
    if destination == external_root:
        _fail("destination_must_be_child")
    return external_root, destination


def _is_protected_name(value: str) -> bool:
    tokens = {token for token in re.split(r"[^a-z0-9]+", value.casefold()) if token}
    return not tokens.isdisjoint(PROTECTED_PARTS)


def _safe_relpath(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not value or any(part in {"", ".", ".."} for part in value.split("/")):
        _fail("unsafe_manifest_path")
    return value


def _symlink_target_ok(rel: str, target: str) -> bool:
    if not target or os.path.isabs(target):
        return False
    depth = len(PurePosixPath(rel).parent.parts)
    for part in target.replace("\\", "/").split("/"):
        if part == "..":
            depth -= 1
            if depth < 0:
                return False
        elif part not in {"", "."}:
            depth += 1
    return True


def _snapshot_full(
    root: Path,
    *,
    before_read: Callable[[], None] | None = None,
    expected_device: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Inventory a tree without following symlinks and detect unstable reads."""
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    if before_read is not None:
        before_read()
    try:
        root_fd = os.open(root, directory_flags)
    except OSError as exc:
        raise MigrationError(f"root_directory_open_failed: {exc}") from exc
    root_stat = os.fstat(root_fd)
    if not stat.S_ISDIR(root_stat.st_mode):
        os.close(root_fd)
        _fail("root_not_directory")
    if expected_device is not None and root_stat.st_dev != expected_device:
        os.close(root_fd)
        _fail("destination_volume_device_changed")
    root_metadata = {
        "mode": stat.S_IMODE(root_stat.st_mode),
        "atime_ns": root_stat.st_atime_ns,
        "mtime_ns": root_stat.st_mtime_ns,
        "xattrs": _check_metadata_fd(root_fd, root_stat, "<root>"),
    }
    records: list[dict[str, Any]] = []
    hardlinks: dict[tuple[int, int], str] = {}
    stack = [""]
    try:
        while stack:
            prefix = stack.pop()
            directory_fd = _open_dir_at(
                root_fd,
                prefix,
                create=False,
                before_write=None,
                expected_device=expected_device,
            )
            try:
                with os.scandir(directory_fd) as iterator:
                    children = sorted(iterator, key=lambda entry: entry.name)
                for entry in children:
                    rel = f"{prefix}/{entry.name}" if prefix else entry.name
                    _safe_relpath(rel)
                    if _is_protected_name(entry.name) or entry.name in WRITER_LOCK_NAMES:
                        _fail(f"protected_or_writer_entry: {rel}")
                    if before_read is not None:
                        before_read()
                    if (
                        expected_device is not None
                        and os.fstat(directory_fd).st_dev != expected_device
                    ):
                        _fail("destination_volume_device_changed")
                    before = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                    mode = stat.S_IMODE(before.st_mode)
                    if stat.S_ISDIR(before.st_mode):
                        child_fd = os.open(entry.name, directory_flags, dir_fd=directory_fd)
                        try:
                            child_xattrs = _check_metadata_fd(child_fd, os.fstat(child_fd), rel)
                        except BaseException:
                            os.close(child_fd)
                            raise
                        os.close(child_fd)
                        records.append(
                            {
                                "path": rel,
                                "type": "directory",
                                "size": 0,
                                "mode": mode,
                                "atime_ns": before.st_atime_ns,
                                "mtime_ns": before.st_mtime_ns,
                                "xattrs": child_xattrs,
                                "source_signature": list(_stat_signature(before)),
                            }
                        )
                        stack.append(rel)
                    elif stat.S_ISREG(before.st_mode):
                        file_fd = os.open(
                            entry.name,
                            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=directory_fd,
                        )
                        try:
                            opened = os.fstat(file_fd)
                            file_xattrs = _check_metadata_fd(file_fd, opened, rel)
                            if _stat_signature(before) != _stat_signature(opened):
                                _fail(f"source_changed_during_hash: {rel}")
                            digest = _hash_fd(file_fd, check=before_read)
                            after = os.fstat(file_fd)
                            if _stat_signature(before) != _stat_signature(after):
                                _fail(f"source_changed_during_hash: {rel}")
                        finally:
                            os.close(file_fd)
                        record: dict[str, Any] = {
                            "path": rel,
                            "type": "file",
                            "size": before.st_size,
                            "mode": mode,
                            "atime_ns": before.st_atime_ns,
                            "mtime_ns": before.st_mtime_ns,
                            "xattrs": file_xattrs,
                            "sha256": digest,
                            "source_signature": list(_stat_signature(before)),
                        }
                        if before.st_nlink > 1:
                            key = (before.st_dev, before.st_ino)
                            group = hardlinks.setdefault(key, f"hl-{len(hardlinks):06d}")
                            record["hardlink_group"] = group
                        records.append(record)
                    elif stat.S_ISLNK(before.st_mode):
                        target = os.readlink(entry.name, dir_fd=directory_fd)
                        path = root / rel
                        symlink_xattrs = _check_metadata_path(path, before, rel)
                        if not _symlink_target_ok(rel, target):
                            _fail(f"symlink_target_escapes_root: {rel}")
                        after = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                        if _stat_signature(before) != _stat_signature(after):
                            _fail(f"source_changed_during_read: {rel}")
                        records.append(
                            {
                                "path": rel,
                                "type": "symlink",
                                "size": len(os.fsencode(target)),
                                "mode": mode,
                                "atime_ns": before.st_atime_ns,
                                "mtime_ns": before.st_mtime_ns,
                                "xattrs": symlink_xattrs,
                                "target": target,
                                "source_signature": list(_stat_signature(before)),
                            }
                        )
                    else:
                        _fail(f"unsupported_special_file: {rel}")
            finally:
                os.close(directory_fd)
    except BaseException:
        raise
    finally:
        os.close(root_fd)
    records.sort(key=lambda item: item["path"])
    return records, root_metadata


def _snapshot(root: Path) -> list[dict[str, Any]]:
    return _snapshot_full(root)[0]


def _stat_signature(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
        st.st_nlink,
    )


def _native_xattrs_fd(fd: int) -> list[str]:
    if hasattr(os, "listxattr"):
        try:
            return cast(list[str], os.listxattr(fd))
        except TypeError:
            pass
    if sys.platform != "darwin":
        _fail("xattr_inspection_unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "flistxattr", None)
    if function is None:
        _fail("xattr_inspection_unavailable")
    function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    function.restype = ctypes.c_ssize_t
    size = function(fd, None, 0, 0)
    if size < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if size == 0:
        return []
    buffer = ctypes.create_string_buffer(size)
    read_size = function(fd, buffer, size, 0)
    if read_size < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return [
        value.decode("utf-8", "surrogateescape")
        for value in buffer.raw[:read_size].split(b"\x00")
        if value
    ]


def _native_xattrs_path(path: Path) -> list[str]:
    if hasattr(os, "listxattr"):
        try:
            return cast(list[str], os.listxattr(path, follow_symlinks=False))
        except TypeError:
            pass
    if sys.platform != "darwin":
        _fail("xattr_inspection_unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "listxattr", None)
    if function is None:
        _fail("xattr_inspection_unavailable")
    function.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    function.restype = ctypes.c_ssize_t
    encoded_path = os.fsencode(path)
    nofollow = 0x0001  # XATTR_NOFOLLOW
    size = function(encoded_path, None, 0, nofollow)
    if size < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if size == 0:
        return []
    buffer = ctypes.create_string_buffer(size)
    read_size = function(encoded_path, buffer, size, nofollow)
    if read_size < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return [
        value.decode("utf-8", "surrogateescape")
        for value in buffer.raw[:read_size].split(b"\x00")
        if value
    ]


def _get_xattr_fd(fd: int, name: str) -> bytes:
    if hasattr(os, "getxattr"):
        try:
            return cast(bytes, os.getxattr(fd, name))
        except TypeError:
            pass
    if sys.platform != "darwin":
        _fail("xattr_read_unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.fgetxattr
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_longlong,
        ctypes.c_int,
    ]
    function.restype = ctypes.c_ssize_t
    encoded_name = os.fsencode(name)
    size = function(fd, encoded_name, None, 0, 0, 0)
    if size < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    buffer = ctypes.create_string_buffer(size)
    read_size = function(fd, encoded_name, buffer, size, 0, 0)
    if read_size < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return buffer.raw[:read_size]


def _set_xattr_fd(fd: int, name: str, value: bytes) -> None:
    if hasattr(os, "setxattr"):
        try:
            os.setxattr(fd, name, value)
            return
        except TypeError:
            pass
    if sys.platform != "darwin":
        _fail("xattr_write_unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.fsetxattr
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_longlong,
        ctypes.c_int,
    ]
    function.restype = ctypes.c_int
    value_buffer = ctypes.create_string_buffer(value)
    if function(fd, os.fsencode(name), value_buffer, len(value), 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _remove_xattr_fd(fd: int, name: str) -> None:
    if hasattr(os, "removexattr"):
        try:
            os.removexattr(fd, name)
            return
        except TypeError:
            pass
    if sys.platform != "darwin":
        _fail("xattr_remove_unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.fremovexattr
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    function.restype = ctypes.c_int
    if function(fd, os.fsencode(name), 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _get_xattr_path(path: Path, name: str) -> bytes:
    if hasattr(os, "getxattr"):
        try:
            return cast(bytes, os.getxattr(path, name, follow_symlinks=False))
        except TypeError:
            pass
    if sys.platform != "darwin":
        _fail("xattr_read_unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.getxattr
    function.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_longlong,
        ctypes.c_int,
    ]
    function.restype = ctypes.c_ssize_t
    encoded_path = os.fsencode(path)
    encoded_name = os.fsencode(name)
    size = function(encoded_path, encoded_name, None, 0, 0, 0x0001)
    if size < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    buffer = ctypes.create_string_buffer(size)
    read_size = function(encoded_path, encoded_name, buffer, size, 0, 0x0001)
    if read_size < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return buffer.raw[:read_size]


def _set_xattr_path(path: Path, name: str, value: bytes) -> None:
    if hasattr(os, "setxattr"):
        try:
            os.setxattr(path, name, value, follow_symlinks=False)
            return
        except TypeError:
            pass
    if sys.platform != "darwin":
        _fail("xattr_write_unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.setxattr
    function.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_longlong,
        ctypes.c_int,
    ]
    function.restype = ctypes.c_int
    value_buffer = ctypes.create_string_buffer(value)
    if function(os.fsencode(path), os.fsencode(name), value_buffer, len(value), 0, 0x0001) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _xattr_records_fd(fd: int) -> list[dict[str, Any]]:
    result = []
    for name in sorted(_native_xattrs_fd(fd)):
        value = _get_xattr_fd(fd, name)
        result.append(
            {"name": name, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        )
    return result


def _xattr_records_path(path: Path) -> list[dict[str, Any]]:
    result = []
    for name in sorted(_native_xattrs_path(path)):
        value = _get_xattr_path(path, name)
        result.append(
            {"name": name, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        )
    return result


def _destination_xattr_additions(
    dst_fd: int, expected: list[dict[str, Any]], rel: str
) -> list[dict[str, Any]]:
    actual = _xattr_records_fd(dst_fd)
    expected_by_name = {record["name"]: record for record in expected}
    actual_by_name = {record["name"]: record for record in actual}
    for name, record in expected_by_name.items():
        if actual_by_name.get(name) != record:
            _fail(f"destination_source_xattr_mismatch: {rel}")
    additions = [record for record in actual if record["name"] not in expected_by_name]
    if any(record["name"] != "com.apple.provenance" for record in additions):
        _fail(f"unsupported_destination_xattr: {rel}")
    return additions


def _copy_xattrs_fd(
    src_fd: int, dst_fd: int, expected: list[dict[str, Any]], rel: str
) -> list[dict[str, Any]]:
    source_names = sorted(_native_xattrs_fd(src_fd))
    copied = []
    source_values: list[tuple[str, bytes]] = []
    for name in source_names:
        value = _get_xattr_fd(src_fd, name)
        record = {"name": name, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        copied.append(record)
        source_values.append((name, value))
    if copied != expected:
        _fail(f"source_xattrs_changed_during_copy: {rel}")
    expected_names = {item["name"] for item in expected}
    try:
        for name in _native_xattrs_fd(dst_fd):
            if name not in expected_names:
                _remove_xattr_fd(dst_fd, name)
        for name, value in source_values:
            _set_xattr_fd(dst_fd, name, value)
    except OSError as exc:
        raise MigrationError(f"destination_xattr_reconciliation_failed: {rel}") from exc
    return _destination_xattr_additions(dst_fd, expected, rel)


def _copy_xattrs_path(
    source: Path, destination: Path, expected: list[dict[str, Any]], rel: str
) -> None:
    copied = []
    for name in sorted(_native_xattrs_path(source)):
        value = _get_xattr_path(source, name)
        copied.append(
            {"name": name, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        )
        _set_xattr_path(destination, name, value)
    if copied != expected or _xattr_records_path(destination) != expected:
        _fail(f"source_xattrs_changed_during_copy: {rel}")


def _check_acl_fd(fd: int, rel: str) -> None:
    if sys.platform != "darwin":
        return
    libc = ctypes.CDLL(None, use_errno=True)
    acl_get = getattr(libc, "acl_get_fd_np", None) or getattr(libc, "acl_get_fd", None)
    acl_to_text = getattr(libc, "acl_to_text", None)
    acl_free = getattr(libc, "acl_free", None)
    if acl_get is None or acl_to_text is None or acl_free is None:
        _fail("acl_inspection_unavailable")
    acl_get.argtypes = [ctypes.c_int, ctypes.c_int]
    acl_get.restype = ctypes.c_void_p
    acl_to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
    acl_to_text.restype = ctypes.c_void_p
    acl_free.argtypes = [ctypes.c_void_p]
    acl_free.restype = ctypes.c_int
    acl = acl_get(fd, 0x00000100)  # Darwin ACL_TYPE_EXTENDED
    if not acl:
        error = ctypes.get_errno()
        if error in {0, 2, 22, 45, 93}:  # no extended ACL / unsupported ACL type
            return
        _fail(f"acl_inspection_failed: {rel}")
    try:
        length = ctypes.c_ssize_t()
        text_ptr = acl_to_text(acl, ctypes.byref(length))
        if not text_ptr:
            _fail(f"acl_inspection_failed: {rel}")
        try:
            text = ctypes.string_at(text_ptr, length.value)
            if text.strip():
                _fail(f"unsupported_extended_acl: {rel}")
        finally:
            acl_free(text_ptr)
    finally:
        acl_free(acl)


def _check_acl_link(path: Path, rel: str) -> None:
    if sys.platform != "darwin":
        return
    libc = ctypes.CDLL(None, use_errno=True)
    acl_get = getattr(libc, "acl_get_link_np", None)
    acl_to_text = getattr(libc, "acl_to_text", None)
    acl_free = getattr(libc, "acl_free", None)
    if acl_get is None or acl_to_text is None or acl_free is None:
        _fail("acl_inspection_unavailable")
    acl_get.argtypes = [ctypes.c_char_p, ctypes.c_int]
    acl_get.restype = ctypes.c_void_p
    acl_to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
    acl_to_text.restype = ctypes.c_void_p
    acl_free.argtypes = [ctypes.c_void_p]
    acl_free.restype = ctypes.c_int
    acl = acl_get(os.fsencode(path), 0x00000100)
    if not acl:
        error = ctypes.get_errno()
        if error in {0, 2, 22, 45, 93}:
            return
        _fail(f"acl_inspection_failed: {rel}")
    try:
        length = ctypes.c_ssize_t()
        text_ptr = acl_to_text(acl, ctypes.byref(length))
        if not text_ptr:
            _fail(f"acl_inspection_failed: {rel}")
        try:
            if ctypes.string_at(text_ptr, length.value).strip():
                _fail(f"unsupported_extended_acl: {rel}")
        finally:
            acl_free(text_ptr)
    finally:
        acl_free(acl)


def _check_metadata_fd(fd: int, st: os.stat_result, rel: str) -> list[dict[str, Any]]:
    if getattr(st, "st_flags", 0):
        _fail(f"unsupported_bsd_flags: {rel}")
    _check_acl_fd(fd, rel)
    try:
        return _xattr_records_fd(fd)
    except (OSError, TypeError) as exc:
        raise MigrationError(f"xattr_inspection_failed: {rel}") from exc


def _check_metadata_path(path: Path, st: os.stat_result, rel: str) -> list[dict[str, Any]]:
    if getattr(st, "st_flags", 0):
        _fail(f"unsupported_bsd_flags: {rel}")
    _check_acl_link(path, rel)
    try:
        return _xattr_records_path(path)
    except (OSError, TypeError) as exc:
        raise MigrationError(f"xattr_inspection_failed: {rel}") from exc


def _hash_file(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        return _hash_fd(fd)
    finally:
        os.close(fd)


def _read_file_nofollow(
    path: Path, *, max_bytes: int | None = None
) -> tuple[bytes, os.stat_result]:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            _fail("expected_regular_file")
        if max_bytes is not None and before.st_size > max_bytes:
            _fail("receipt_too_large")
        chunks: list[bytes] = []
        while chunk := os.read(fd, CHUNK_SIZE):
            chunks.append(chunk)
        after = os.fstat(fd)
        if _stat_signature(before) != _stat_signature(after):
            _fail("file_changed_during_read")
        return b"".join(chunks), after
    finally:
        os.close(fd)


def _unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def load_independent_receipt(
    external_root: Path, receipt_path: Path, expected_sha256: str
) -> dict[str, Any]:
    external = _plain_absolute(external_root, "external_root")
    receipt = _plain_absolute(receipt_path, "verification_receipt")
    try:
        receipt.relative_to(external)
    except ValueError as exc:
        raise MigrationError("verification_receipt_outside_external_root") from exc
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        _fail("invalid_verification_receipt_sha256")
    raw, st = _read_file_nofollow(receipt, max_bytes=64 * 1024)
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o444
        or st.st_nlink != 1
    ):
        _fail("unsafe_verification_receipt")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        _fail("verification_receipt_hash_mismatch")
    try:
        body = json.loads(raw, object_pairs_hook=_unique_object_pairs)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MigrationError("verification_receipt_invalid") from exc
    if not isinstance(body, dict):
        _fail("verification_receipt_invalid")
    return cast(dict[str, Any], body)


def load_preflight_receipt(
    receipt_path: Path,
    *,
    expected_source_root: Path,
    group: str,
    expected_sha256: str | None = None,
) -> tuple[dict[str, Any], bytes]:
    receipt = _plain_absolute(receipt_path, "preflight_receipt")
    raw, st = _read_file_nofollow(receipt, max_bytes=64 * 1024)
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) not in {0o600, 0o444}
        or st.st_nlink != 1
    ):
        _fail("unsafe_preflight_receipt")
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        _fail("preflight_receipt_hash_mismatch")
    try:
        body = json.loads(raw, object_pairs_hook=_unique_object_pairs)
    except (ValueError, UnicodeDecodeError) as exc:
        raise MigrationError("preflight_receipt_invalid") from exc
    source = _plain_absolute(expected_source_root, "expected_source")
    closure_evidence = body.get("closure_evidence") if isinstance(body, dict) else None
    if (
        not isinstance(body, dict)
        or body.get("schema_version") != 1
        or body.get("group") != group
        or body.get("source_root") != str(source)
        or body.get("closed") is not True
        or body.get("no_active_writers") is not True
        or body.get("no_protected_payloads") is not True
        or not isinstance(closure_evidence, list)
        or not closure_evidence
        or any(not isinstance(item, str) or not item.strip() for item in closure_evidence)
    ):
        _fail("preflight_receipt_binding_mismatch")
    return body, raw


def _hash_fd(fd: int, *, check: Callable[[], None] | None = None) -> str:
    digest = hashlib.sha256()
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        _fail("source_file_type_changed")
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, CHUNK_SIZE):
        digest.update(chunk)
        if check is not None:
            check()
    after = os.fstat(fd)
    if _stat_signature(before) != _stat_signature(after):
        _fail("source_file_changed_during_hash")
    return digest.hexdigest()


def _open_source_file(root: Path, rel: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(root, flags)
    try:
        parts = PurePosixPath(rel).parts
        for part in parts[:-1]:
            child = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child
        return os.open(parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
    finally:
        os.close(directory_fd)


def _open_source_dir(root: Path, rel: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(root, flags)
    try:
        for part in PurePosixPath(rel).parts:
            child = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child
        result = directory_fd
        directory_fd = -1
        return result
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def _open_link_at(directory_fd: int, name: str) -> int:
    if sys.platform == "darwin":
        flags = os.O_RDONLY | 0x00200000  # O_SYMLINK: open the link itself for xattr access.
    else:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    return os.open(name, flags, dir_fd=directory_fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _rename_create_only(source: Path, destination: Path) -> None:
    """Use the host's no-replace rename primitive; never overwrite a target."""
    libc = ctypes.CDLL(None, use_errno=True)
    if hasattr(libc, "renamex_np"):
        rename = libc.renamex_np
        flags = 0x00000004  # macOS RENAME_EXCL
    elif hasattr(libc, "renameat2"):
        rename = libc.renameat2
        flags = 0x00000001  # Linux RENAME_NOREPLACE
    else:
        _fail("create_only_rename_unavailable")
    rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    result = rename(os.fsencode(source), os.fsencode(destination), flags)
    if result != 0:
        error = ctypes.get_errno()
        if error == 17:  # EEXIST on supported hosts
            _fail("destination_already_exists")
        raise OSError(error, os.strerror(error), str(destination))


def _write_create_only(path: Path, payload: bytes, mode: int) -> None:
    path = _plain_absolute(path, "created_file")
    try:
        fd = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode
        )
    except FileExistsError as exc:
        raise MigrationError("create_only_collision") from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_dir(path.parent)


def _fsync_dir_fd(fd: int) -> None:
    os.fsync(fd)


def _check_write_location(
    root_fd: int,
    expected_device: int | None,
    before_write: Callable[[], None] | None,
) -> None:
    if before_write is not None:
        before_write()
    if expected_device is not None and os.fstat(root_fd).st_dev != expected_device:
        _fail("destination_volume_device_changed")


def _open_dir_at(
    root_fd: int,
    relative: str,
    *,
    create: bool,
    before_write: Callable[[], None] | None,
    expected_device: int | None,
) -> int:
    current = os.dup(root_fd)
    try:
        if relative:
            for part in PurePosixPath(relative).parts:
                if create:
                    try:
                        _check_write_location(current, expected_device, before_write)
                        os.mkdir(part, mode=0o700, dir_fd=current)
                        os.fsync(current)
                    except FileExistsError:
                        pass
                child = os.open(
                    part,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current,
                )
                os.close(current)
                current = child
        if expected_device is not None and os.fstat(current).st_dev != expected_device:
            _fail("destination_volume_device_changed")
        return current
    except BaseException:
        os.close(current)
        raise


def _write_create_only_at(dir_fd: int, name: str, payload: bytes, mode: int) -> None:
    try:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=dir_fd,
        )
    except FileExistsError as exc:
        raise MigrationError("create_only_collision") from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(dir_fd)


def _rename_create_only_at(
    source_name: str,
    source_dir_fd: int,
    destination_name: str,
    destination_dir_fd: int,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if hasattr(libc, "renameatx_np"):
        rename = libc.renameatx_np
        flags = 0x00000004  # macOS RENAME_EXCL
    elif hasattr(libc, "renameat2"):
        rename = libc.renameat2
        flags = 0x00000001  # Linux RENAME_NOREPLACE
    else:
        _fail("create_only_rename_unavailable")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    result = rename(
        source_dir_fd,
        os.fsencode(source_name),
        destination_dir_fd,
        os.fsencode(destination_name),
        flags,
    )
    if result != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            _fail("destination_already_exists")
        raise OSError(error, os.strerror(error), destination_name)


def _copy_tree(
    source: Path,
    external_fd: int,
    temporary_name: str,
    records: list[dict[str, Any]],
    *,
    root_mode: int,
    root_atime_ns: int,
    root_mtime_ns: int,
    root_xattrs: list[dict[str, Any]],
    before_write: Callable[[], None] | None,
    expected_device: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _check_write_location(external_fd, expected_device, before_write)
    os.mkdir(temporary_name, mode=0o700, dir_fd=external_fd)
    _fsync_dir_fd(external_fd)
    temp_fd = os.open(
        temporary_name,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=external_fd,
    )
    directories = [r for r in records if r["type"] == "directory"]
    files = [r for r in records if r["type"] == "file"]
    symlinks = [r for r in records if r["type"] == "symlink"]
    hardlink_targets: dict[str, str] = {}
    destination_added_xattrs: list[dict[str, Any]] = []
    destination_added_root_xattrs: list[dict[str, Any]] = []
    try:
        for record in directories:
            relative = record["path"]
            parent, name = relative.rsplit("/", 1) if "/" in relative else ("", relative)
            parent_fd = _open_dir_at(
                temp_fd,
                parent,
                create=True,
                before_write=before_write,
                expected_device=expected_device,
            )
            try:
                _check_write_location(external_fd, expected_device, before_write)
                os.mkdir(name, mode=0o700, dir_fd=parent_fd)
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        for record in files:
            rel = record["path"]
            parent_rel, name = rel.rsplit("/", 1) if "/" in rel else ("", rel)
            parent_fd = _open_dir_at(
                temp_fd,
                parent_rel,
                create=True,
                before_write=before_write,
                expected_device=expected_device,
            )
            try:
                _check_write_location(external_fd, expected_device, before_write)
                link_group = record.get("hardlink_group")
                if link_group in hardlink_targets:
                    target_rel = hardlink_targets[link_group]
                    target_parent, target_name = (
                        target_rel.rsplit("/", 1) if "/" in target_rel else ("", target_rel)
                    )
                    target_parent_fd = _open_dir_at(
                        temp_fd,
                        target_parent,
                        create=False,
                        before_write=None,
                        expected_device=expected_device,
                    )
                    try:
                        os.link(
                            target_name,
                            name,
                            src_dir_fd=target_parent_fd,
                            dst_dir_fd=parent_fd,
                            follow_symlinks=False,
                        )
                        os.fsync(parent_fd)
                        target_file_fd = os.open(
                            target_name,
                            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=target_parent_fd,
                        )
                        try:
                            additions = _destination_xattr_additions(
                                target_file_fd, record["xattrs"], rel
                            )
                        finally:
                            os.close(target_file_fd)
                        if additions:
                            destination_added_xattrs.append({"path": rel, "xattrs": additions})
                    finally:
                        os.close(target_parent_fd)
                    continue
                src_fd = _open_source_file(source, rel)
                dst_fd = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
                digest = hashlib.sha256()
                try:
                    if expected_device is not None and os.fstat(dst_fd).st_dev != expected_device:
                        _fail("destination_file_volume_changed")
                    before = os.fstat(src_fd)
                    while chunk := os.read(src_fd, CHUNK_SIZE):
                        digest.update(chunk)
                        view = memoryview(chunk)
                        while view:
                            written = os.write(dst_fd, view)
                            if written <= 0:
                                raise OSError("short copy write")
                            view = view[written:]
                        # Bounded chunk callbacks enforce the 64 MiB copy limit and let the
                        # CLI maintain its five-second full-check deadline.
                        _check_write_location(external_fd, expected_device, before_write)
                        if (
                            expected_device is not None
                            and os.fstat(dst_fd).st_dev != expected_device
                        ):
                            _fail("destination_file_volume_changed")
                    after = os.fstat(src_fd)
                    if _stat_signature(before) != _stat_signature(after):
                        _fail(f"source_changed_during_copy: {rel}")
                    if digest.hexdigest() != record["sha256"]:
                        _fail(f"source_hash_changed_during_copy: {rel}")
                    os.fchmod(dst_fd, record["mode"])
                    additions = _copy_xattrs_fd(src_fd, dst_fd, record["xattrs"], rel)
                    if additions:
                        destination_added_xattrs.append({"path": rel, "xattrs": additions})
                    os.utime(dst_fd, ns=(record["atime_ns"], record["mtime_ns"]))
                    os.fsync(dst_fd)
                finally:
                    os.close(dst_fd)
                    os.close(src_fd)
                if link_group:
                    hardlink_targets[link_group] = rel
            finally:
                os.close(parent_fd)
        for record in symlinks:
            rel = record["path"]
            parent_rel, name = rel.rsplit("/", 1) if "/" in rel else ("", rel)
            parent_fd = _open_dir_at(
                temp_fd,
                parent_rel,
                create=True,
                before_write=before_write,
                expected_device=expected_device,
            )
            try:
                _check_write_location(external_fd, expected_device, before_write)
                os.symlink(record["target"], name, dir_fd=parent_fd)
                source_parent_rel, source_name = rel.rsplit("/", 1) if "/" in rel else ("", rel)
                source_parent_fd = _open_source_dir(source, source_parent_rel)
                source_link_fd = -1
                destination_link_fd = -1
                try:
                    source_link_fd = _open_link_at(source_parent_fd, source_name)
                    destination_link_fd = _open_link_at(parent_fd, name)
                    additions = _copy_xattrs_fd(
                        source_link_fd, destination_link_fd, record["xattrs"], rel
                    )
                    if additions:
                        destination_added_xattrs.append({"path": rel, "xattrs": additions})
                finally:
                    if destination_link_fd >= 0:
                        os.close(destination_link_fd)
                    if source_link_fd >= 0:
                        os.close(source_link_fd)
                    os.close(source_parent_fd)
                os.utime(
                    name,
                    ns=(record["atime_ns"], record["mtime_ns"]),
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        for record in sorted(directories, key=lambda item: item["path"].count("/"), reverse=True):
            relative = record["path"]
            directory_fd = _open_dir_at(
                temp_fd,
                relative,
                create=False,
                before_write=None,
                expected_device=expected_device,
            )
            try:
                os.fchmod(directory_fd, record["mode"])
                src_directory_fd = _open_source_dir(source, relative)
                try:
                    additions = _copy_xattrs_fd(
                        src_directory_fd, directory_fd, record["xattrs"], relative
                    )
                    if additions:
                        destination_added_xattrs.append({"path": relative, "xattrs": additions})
                finally:
                    os.close(src_directory_fd)
                os.utime(directory_fd, ns=(record["atime_ns"], record["mtime_ns"]))
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        os.fchmod(temp_fd, root_mode)
        src_root_fd = _open_source_dir(source, ".")
        try:
            destination_added_root_xattrs = _copy_xattrs_fd(
                src_root_fd, temp_fd, root_xattrs, "<root>"
            )
        finally:
            os.close(src_root_fd)
        os.utime(temp_fd, ns=(root_atime_ns, root_mtime_ns))
        os.fsync(temp_fd)
        _fsync_dir_fd(external_fd)
    finally:
        os.close(temp_fd)
    destination_added_xattrs.sort(key=lambda item: item["path"])
    return destination_added_xattrs, destination_added_root_xattrs


def _assert_locked_root(
    root: Path, root_fd: int, lock_fd: int, expected_device: int | None
) -> None:
    root_st = os.fstat(root_fd)
    path_st = root.lstat()
    lock_st = os.fstat(lock_fd)
    current_lock_st = os.stat(".research-migration.lock", dir_fd=root_fd, follow_symlinks=False)
    if (root_st.st_dev, root_st.st_ino) != (path_st.st_dev, path_st.st_ino):
        _fail("external_root_path_replaced")
    if expected_device is not None and root_st.st_dev != expected_device:
        _fail("destination_volume_device_changed")
    if (lock_st.st_dev, lock_st.st_ino) != (current_lock_st.st_dev, current_lock_st.st_ino):
        _fail("migration_lock_replaced")
    if (
        not stat.S_ISREG(current_lock_st.st_mode)
        or current_lock_st.st_uid != os.getuid()
        or stat.S_IMODE(current_lock_st.st_mode) != 0o600
        or current_lock_st.st_nlink != 1
    ):
        _fail("unsafe_migration_lock")


def _lock_root(
    root: Path,
    *,
    before_write: Callable[[], None] | None,
    expected_device: int | None,
) -> tuple[int, int]:
    """Pin the external root and serialize migration operations under it."""
    _check_before = before_write
    if _check_before is not None:
        _check_before()
    root_fd = os.open(
        root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    lock_fd = -1
    try:
        if expected_device is not None and os.fstat(root_fd).st_dev != expected_device:
            _fail("destination_volume_device_changed")
        fd = os.open(
            ".research-migration.lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=root_fd,
        )
        lock_fd = fd
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600
            or st.st_nlink != 1
        ):
            _fail("unsafe_migration_lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        _assert_locked_root(root, root_fd, fd, expected_device)
        return root_fd, fd
    except BaseException:
        if lock_fd >= 0:
            os.close(lock_fd)
        os.close(root_fd)
        raise


def stage_migration(
    source_root: Path,
    external_root: Path,
    destination_root: Path,
    *,
    group: str,
    expected_source_root: Path,
    closed_preflight: Callable[[Path], None],
    preflight_receipt_path: Path,
    preflight_receipt_sha256: str,
    before_write: Callable[[], None] | None = None,
    before_publish: Callable[[], None] | None = None,
    expected_destination_device: int | None = None,
) -> dict[str, Any]:
    """Copy one explicitly approved closed root, then write its create-only manifest.

    ``closed_preflight`` must raise unless independent operational checks establish
    there are no active writers. Source contents are never deleted.
    """
    source = _validate_source(source_root, expected_source_root, group)
    external, destination = _validate_destination(external_root, destination_root)
    if destination.name != source.name:
        _fail("destination_must_use_source_basename")
    if not preflight_receipt_sha256 or len(preflight_receipt_sha256) != 64:
        _fail("preflight_receipt_hash_required")
    receipt_path = _plain_absolute(preflight_receipt_path, "preflight_receipt")
    load_preflight_receipt(
        receipt_path,
        expected_source_root=source,
        group=group,
        expected_sha256=preflight_receipt_sha256,
    )
    relative = destination.relative_to(external)
    destination_parent_relative = "/".join(relative.parts[:-1])
    destination_name = relative.parts[-1]
    manifest_name = destination_name + ".manifest.json"
    source_root_mode_before = stat.S_IMODE(source.lstat().st_mode)
    source_root_signature_before = _stat_signature(source.lstat())
    source_root_atime_ns = source.lstat().st_atime_ns
    source_root_mtime_ns = source.lstat().st_mtime_ns
    # Closure is established before any corpus inventory or hashing begins.
    closed_preflight(source)
    source_before, source_root_metadata = _snapshot_full(source)
    temp = external / f".migration-stage-{group}-{secrets.token_hex(8)}"
    manifest_path = destination.with_name(manifest_name)
    root_fd, lock_fd = _lock_root(
        external, before_write=before_write, expected_device=expected_destination_device
    )
    destination_parent_fd = -1
    try:
        if before_write is not None:
            before_write()
        _assert_locked_root(external, root_fd, lock_fd, expected_destination_device)
        # Recheck the closure claim once migration operations are serialized.
        closed_preflight(source)
        destination_parent_fd = _open_dir_at(
            root_fd,
            destination_parent_relative,
            create=False,
            before_write=None,
            expected_device=expected_destination_device,
        )
        for name in (destination_name, manifest_name):
            try:
                os.stat(name, dir_fd=destination_parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            _fail("destination_or_manifest_already_exists")
        destination_added_xattrs, destination_added_root_xattrs = _copy_tree(
            source,
            root_fd,
            temp.name,
            source_before,
            root_mode=source_root_mode_before,
            root_atime_ns=source_root_atime_ns,
            root_mtime_ns=source_root_mtime_ns,
            root_xattrs=source_root_metadata["xattrs"],
            before_write=before_write,
            expected_device=expected_destination_device,
        )
        destination_added_xattrs_by_path = _validate_destination_xattr_additions(
            destination_added_xattrs,
            source_before,
            label="destination_added_xattrs",
        )
        destination_added_root_xattrs = _validate_root_xattr_additions(
            destination_added_root_xattrs,
            source_root_metadata["xattrs"],
        )
        if before_write is not None:
            before_write()
        _assert_locked_root(external, root_fd, lock_fd, expected_destination_device)
        closed_preflight(source)
        source_after, source_root_metadata_after = _snapshot_full(source)
        source_root_mode_after = stat.S_IMODE(source.lstat().st_mode)
        source_root_signature_after = _stat_signature(source.lstat())
        if (
            not _same_source_inventory(source_before, source_after)
            or {key: value for key, value in source_root_metadata.items() if key != "atime_ns"}
            != {
                key: value for key, value in source_root_metadata_after.items() if key != "atime_ns"
            }
            or source_root_mode_before != source_root_mode_after
            or source_root_signature_before != source_root_signature_after
        ):
            _fail("source_changed_during_migration")
        # Hash and structure verification occurs before publication too.
        source_root_mode = source_root_mode_before
        _verify_records(
            temp,
            source_before,
            source_root_mode,
            source_root_atime_ns,
            source_root_mtime_ns,
            source_root_metadata["xattrs"],
            before_read=before_write,
            expected_device=expected_destination_device,
            destination_added_xattrs=destination_added_xattrs_by_path,
            destination_added_root_xattrs=destination_added_root_xattrs,
        )
        manifest_version = (
            MANIFEST_VERSION
            if destination_added_xattrs or destination_added_root_xattrs
            else LEGACY_MANIFEST_VERSION
        )
        manifest: dict[str, Any] = {
            "version": manifest_version,
            "group": group,
            "source_root": str(source),
            "destination_root": str(destination),
            "source_root_mode": source_root_mode,
            "source_root_signature": list(source_root_signature_before),
            "source_root_atime_ns": source_root_atime_ns,
            "source_root_mtime_ns": source_root_mtime_ns,
            "source_root_xattrs": source_root_metadata["xattrs"],
            "metadata_scope": PRESERVED_METADATA,
            "metadata_not_preserved": NOT_PRESERVED_METADATA,
            "required_absent_metadata": REQUIRED_ABSENT_METADATA,
            "created_ns": time.time_ns(),
            "preflight_receipt_path": str(receipt_path),
            "preflight_receipt_sha256": preflight_receipt_sha256,
            "entries": source_before,
            "summary": _summary(source_before),
        }
        if manifest_version == MANIFEST_VERSION:
            manifest["destination_added_xattrs"] = destination_added_xattrs
            manifest["destination_added_root_xattrs"] = destination_added_root_xattrs
        encoded = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if before_publish is not None:
            before_publish()
        elif before_write is not None:
            before_write()
        _assert_locked_root(external, root_fd, lock_fd, expected_destination_device)
        _write_create_only_at(destination_parent_fd, manifest_name, encoded, 0o444)
        if before_publish is not None:
            before_publish()
        elif before_write is not None:
            before_write()
        _assert_locked_root(external, root_fd, lock_fd, expected_destination_device)
        if before_publish is not None:
            before_publish()
        elif before_write is not None:
            before_write()
        _assert_locked_root(external, root_fd, lock_fd, expected_destination_device)
        _rename_create_only_at(temp.name, root_fd, destination_name, destination_parent_fd)
        _fsync_dir_fd(root_fd)
        _fsync_dir_fd(destination_parent_fd)
        if before_publish is not None:
            before_publish()
        elif before_write is not None:
            before_write()
        _assert_locked_root(external, root_fd, lock_fd, expected_destination_device)
        manifest["manifest_path"] = str(manifest_path)
        manifest["manifest_sha256"] = hashlib.sha256(encoded).hexdigest()
        return manifest
    except BaseException:
        # Keep a failed stage for diagnosis/recovery; never remove source data.
        raise
    finally:
        if destination_parent_fd >= 0:
            os.close(destination_parent_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        os.close(root_fd)


def _summary(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "entries": len(records),
        "files": sum(r["type"] == "file" for r in records),
        "directories": sum(r["type"] == "directory" for r in records),
        "symlinks": sum(r["type"] == "symlink" for r in records),
        "bytes": sum(r["size"] for r in records if r["type"] == "file"),
    }


def _same_source_inventory(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> bool:
    def normalize(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {key: value for key, value in item.items() if key != "atime_ns"} for item in records
        ]

    return normalize(before) == normalize(after)


def _validate_destination_xattr_additions(
    additions: Any,
    source_records: list[dict[str, Any]],
    *,
    label: str,
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(additions, list):
        _fail(f"manifest_{label}_invalid")
    source_by_path = {record["path"]: record for record in source_records}
    mapped: dict[str, list[dict[str, Any]]] = {}
    previous_path = ""
    for item in additions:
        if not isinstance(item, dict) or set(item) != {"path", "xattrs"}:
            _fail(f"manifest_{label}_invalid")
        path = item["path"]
        attrs = item["xattrs"]
        if (
            not isinstance(path, str)
            or path not in source_by_path
            or path <= previous_path
            or not isinstance(attrs, list)
            or len(attrs) != 1
        ):
            _fail(f"manifest_{label}_invalid")
        previous_path = path
        attr = attrs[0]
        if (
            not isinstance(attr, dict)
            or set(attr) != {"name", "size", "sha256"}
            or attr.get("name") != "com.apple.provenance"
            or not isinstance(attr.get("size"), int)
            or attr["size"] < 0
            or not isinstance(attr.get("sha256"), str)
            or len(attr["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in attr["sha256"])
        ):
            _fail(f"manifest_{label}_invalid")
        source_names = {xattr["name"] for xattr in source_by_path[path].get("xattrs", [])}
        if "com.apple.provenance" in source_names:
            _fail(f"manifest_{label}_source_contains_exception")
        mapped[path] = attrs
    return mapped


def _validate_root_xattr_additions(
    additions: Any, source_root_xattrs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not isinstance(additions, list) or len(additions) > 1:
        _fail("manifest_destination_added_root_xattrs_invalid")
    if not additions:
        return []
    attr = additions[0]
    if (
        not isinstance(attr, dict)
        or set(attr) != {"name", "size", "sha256"}
        or attr.get("name") != "com.apple.provenance"
        or not isinstance(attr.get("size"), int)
        or attr["size"] < 0
        or not isinstance(attr.get("sha256"), str)
        or len(attr["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in attr["sha256"])
    ):
        _fail("manifest_destination_added_root_xattrs_invalid")
    if any(item["name"] == "com.apple.provenance" for item in source_root_xattrs):
        _fail("manifest_destination_added_root_xattrs_source_contains_exception")
    return cast(list[dict[str, Any]], additions)


def _verify_records(
    root: Path,
    expected: list[dict[str, Any]],
    root_mode: int | None = None,
    root_atime_ns: int | None = None,
    root_mtime_ns: int | None = None,
    root_xattrs: list[dict[str, Any]] | None = None,
    before_read: Callable[[], None] | None = None,
    expected_device: int | None = None,
    destination_added_xattrs: dict[str, list[dict[str, Any]]] | None = None,
    destination_added_root_xattrs: list[dict[str, Any]] | None = None,
) -> None:
    actual, actual_root_metadata = _snapshot_full(
        root, before_read=before_read, expected_device=expected_device
    )
    destination_added_xattrs = destination_added_xattrs or {}
    normalized_expected = []
    for record in expected:
        normalized = {
            key: value
            for key, value in record.items()
            if key not in {"source_signature", "atime_ns"}
        }
        normalized["xattrs"] = sorted(
            [*record.get("xattrs", []), *destination_added_xattrs.get(record["path"], [])],
            key=lambda item: item["name"],
        )
        normalized_expected.append(normalized)
    normalized_actual = [
        {key: value for key, value in record.items() if key not in {"source_signature", "atime_ns"}}
        for record in actual
    ]
    if normalized_actual != normalized_expected:
        _fail("tree_manifest_mismatch")
    if root_mode is not None and actual_root_metadata["mode"] != root_mode:
        _fail("tree_root_mode_mismatch")
    if root_mtime_ns is not None and actual_root_metadata["mtime_ns"] != root_mtime_ns:
        _fail("tree_root_mtime_mismatch")
    expected_root_xattrs = sorted(
        [*(root_xattrs or []), *(destination_added_root_xattrs or [])],
        key=lambda item: item["name"],
    )
    if root_xattrs is not None and actual_root_metadata["xattrs"] != expected_root_xattrs:
        _fail("tree_root_xattr_mismatch")
    # The inventory derives hardlink groups from opened inode identities, so matching
    # groups above verifies equivalence without path lookups that could escape the pin.


def verify_migration(
    source_root: Path,
    destination_root: Path,
    manifest_path: Path,
    *,
    expected_source_root: Path,
    expected_destination_root: Path,
    before_read: Callable[[], None] | None = None,
    expected_destination_device: int | None = None,
) -> dict[str, Any]:
    """Read-only exact-tree verification suitable for a separate verifier process."""
    source = _plain_absolute(source_root, "source")
    destination = _plain_absolute(destination_root, "destination")
    if source != _plain_absolute(expected_source_root, "expected_source"):
        _fail("source_does_not_match_expected_root")
    if destination != _plain_absolute(expected_destination_root, "expected_destination"):
        _fail("destination_does_not_match_expected_root")
    manifest_path = _plain_absolute(manifest_path, "manifest")
    if manifest_path != destination.with_name(destination.name + ".manifest.json"):
        _fail("manifest_path_mismatch")
    try:
        if before_read is not None:
            before_read()
        raw, manifest_stat = _read_file_nofollow(manifest_path)
        if (
            not stat.S_ISREG(manifest_stat.st_mode)
            or manifest_stat.st_uid != os.getuid()
            or stat.S_IMODE(manifest_stat.st_mode) != 0o444
            or manifest_stat.st_nlink != 1
            or (
                expected_destination_device is not None
                and manifest_stat.st_dev != expected_destination_device
            )
        ):
            _fail("unsafe_manifest_file")
        manifest = json.loads(raw, object_pairs_hook=_unique_object_pairs)
    except (OSError, ValueError) as exc:
        raise MigrationError(f"manifest_unreadable: {exc}") from exc
    version = manifest.get("version")
    if (
        version not in {LEGACY_MANIFEST_VERSION, MANIFEST_VERSION}
        or manifest.get("source_root") != str(source)
        or manifest.get("destination_root") != str(destination)
        or manifest.get("metadata_scope") != PRESERVED_METADATA
        or manifest.get("metadata_not_preserved") != NOT_PRESERVED_METADATA
        or manifest.get("required_absent_metadata") != REQUIRED_ABSENT_METADATA
    ):
        _fail("manifest_binding_mismatch")
    _validate_source(source, source, str(manifest.get("group", "")))
    source_root_signature = manifest.get("source_root_signature")
    if source_root_signature != list(_stat_signature(source.lstat())):
        _fail("source_root_changed_since_stage")
    receipt_path = manifest.get("preflight_receipt_path")
    receipt_sha = manifest.get("preflight_receipt_sha256")
    if not isinstance(receipt_path, str) or not isinstance(receipt_sha, str):
        _fail("preflight_attestation_missing")
    load_preflight_receipt(
        Path(receipt_path),
        expected_source_root=source,
        group=str(manifest.get("group", "")),
        expected_sha256=receipt_sha,
    )
    records = manifest.get("entries")
    if not isinstance(records, list) or any(
        not isinstance(record, dict) or not isinstance(record.get("path"), str)
        for record in records
    ):
        _fail("manifest_entries_invalid")
    if version == LEGACY_MANIFEST_VERSION:
        if "destination_added_xattrs" in manifest or "destination_added_root_xattrs" in manifest:
            _fail("legacy_manifest_has_destination_xattr_exceptions")
        added_xattrs: dict[str, list[dict[str, Any]]] = {}
        added_root_xattrs: list[dict[str, Any]] = []
    else:
        if (
            "destination_added_xattrs" not in manifest
            or "destination_added_root_xattrs" not in manifest
        ):
            _fail("manifest_destination_xattr_exceptions_missing")
        added_xattrs = _validate_destination_xattr_additions(
            manifest["destination_added_xattrs"],
            records,
            label="destination_added_xattrs",
        )
        source_root_xattrs = manifest.get("source_root_xattrs")
        if not isinstance(source_root_xattrs, list):
            _fail("manifest_source_root_xattrs_invalid")
        added_root_xattrs = _validate_root_xattr_additions(
            manifest["destination_added_root_xattrs"], source_root_xattrs
        )
        if not added_xattrs and not added_root_xattrs:
            _fail("unaffected_manifest_must_use_legacy_version")
    source_records, source_root_metadata = _snapshot_full(source, before_read=before_read)
    if not _same_source_inventory(source_records, records):
        _fail("source_does_not_match_manifest")
    if source_root_metadata.get("xattrs") != manifest.get("source_root_xattrs"):
        _fail("source_root_xattrs_changed")
    _verify_records(
        destination,
        records,
        manifest.get("source_root_mode"),
        manifest.get("source_root_atime_ns"),
        manifest.get("source_root_mtime_ns"),
        manifest.get("source_root_xattrs"),
        before_read=before_read,
        expected_device=expected_destination_device,
        destination_added_xattrs=added_xattrs,
        destination_added_root_xattrs=added_root_xattrs,
    )
    source_records_after, source_root_metadata_after = _snapshot_full(
        source, before_read=before_read
    )
    if (
        not _same_source_inventory(source_records, source_records_after)
        or list(_stat_signature(source.lstat())) != source_root_signature
        or source_root_metadata_after.get("xattrs") != manifest.get("source_root_xattrs")
    ):
        _fail("source_changed_during_verification")
    if before_read is not None:
        before_read()
    manifest_raw_after, manifest_stat_after = _read_file_nofollow(manifest_path)
    if (
        manifest_raw_after != raw
        or _stat_signature(manifest_stat_after) != _stat_signature(manifest_stat)
        or (
            expected_destination_device is not None
            and manifest_stat_after.st_dev != expected_destination_device
        )
    ):
        _fail("manifest_changed_during_verification")
    return {
        "accepted": True,
        "group": manifest.get("group"),
        "source_root": str(source),
        "destination_root": str(destination),
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "summary": _summary(records),
    }


def publish_locator(
    external_root: Path,
    locator_path: Path,
    *,
    group: str,
    old_root: Path,
    new_root: Path,
    verification_receipt_path: Path,
    verification_receipt_sha256: str,
    expected_destination_device: int | None = None,
    before_write: Callable[[], None] | None = None,
    before_publish: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Freshly replay a pinned independent receipt, then publish a create-only locator."""
    external = _plain_absolute(external_root, "external_root")
    locator = _plain_absolute(locator_path, "locator")
    old = _plain_absolute(old_root, "old_root")
    new = _plain_absolute(new_root, "new_root")
    try:
        locator.relative_to(external)
    except ValueError as exc:
        raise MigrationError("locator_outside_external_root") from exc
    if new.name != group or old.name != group:
        _fail("locator_group_binding_mismatch")
    try:
        receipt = load_independent_receipt(
            external, verification_receipt_path, verification_receipt_sha256
        )
    except MigrationError as exc:
        raise MigrationError(f"independent_verification_receipt_invalid: {exc}") from exc

    root_fd, lock_fd = _lock_root(
        external, before_write=before_write, expected_device=expected_destination_device
    )
    parent_fd = -1
    try:
        _assert_locked_root(external, root_fd, lock_fd, expected_destination_device)
        # Recompute source, destination, metadata, and current manifest digest while serialized.
        manifest_path = new.with_name(new.name + ".manifest.json")
        fresh = verify_migration(
            old,
            new,
            manifest_path,
            expected_source_root=old,
            expected_destination_root=new,
            before_read=before_write,
            expected_destination_device=expected_destination_device,
        )
        for key in (
            "accepted",
            "group",
            "source_root",
            "destination_root",
            "manifest_sha256",
            "summary",
        ):
            if receipt.get(key) != fresh.get(key):
                _fail(f"independent_verification_receipt_mismatch: {key}")
        payload = {
            "version": 1,
            "group": group,
            "old_root": str(old),
            "new_root": str(new),
            "manifest_sha256": fresh["manifest_sha256"],
            "verified_summary": fresh["summary"],
            "verification_receipt_path": str(
                _plain_absolute(verification_receipt_path, "verification_receipt")
            ),
            "verification_receipt_sha256": verification_receipt_sha256,
            "locator_created_ns": time.time_ns(),
        }
        relative_parent = locator.parent.relative_to(external)
        parent_fd = _open_dir_at(
            root_fd,
            "/".join(relative_parent.parts),
            create=False,
            before_write=None,
            expected_device=expected_destination_device,
        )
        if before_publish is not None:
            before_publish()
        elif before_write is not None:
            before_write()
        _assert_locked_root(external, root_fd, lock_fd, expected_destination_device)
        _write_create_only_at(
            parent_fd,
            locator.name,
            (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode(),
            0o444,
        )
        if before_publish is not None:
            before_publish()
        elif before_write is not None:
            before_write()
        _assert_locked_root(external, root_fd, lock_fd, expected_destination_device)
        return payload
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        os.close(root_fd)
