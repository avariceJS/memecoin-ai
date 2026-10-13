#!/usr/bin/env python3
"""Streaming, descriptor-bound opaque tree custody for synthetic or declared roots.

This format records one asserted root recovery point. It does not provide
cross-root atomicity. Files, including SQLite families, are opaque byte streams.
The protected mode changes only classification and public CLI output; it never
parses file contents.
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import errno
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from memecoin_research_storage import StorageError
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SCHEMA = "brain-host-tree-custody-v2"
RESERVATION_SCHEMA = "brain-host-tree-custody-reservation-v2"
COMPLETE_SCHEMA = "brain-host-tree-custody-complete-v2"
RESTORE_SCHEMA = "brain-host-tree-custody-restore-v2"
BASE = "host-recovery/tree-custody"
CHUNK_BYTES = 8 * 1024 * 1024
PAGE_ENTRIES = 512
CHUNK_INDEX_ENTRIES = 512
INDEX_ENTRIES = 1024
MAX_ENTRIES = 2_000_000
MAX_SOURCE_BYTES = 512 * 1024**3
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_COMPONENT_BYTES = 1024
MAX_PATH_DEPTH = 256
UF_HIDDEN = 0x00008000
SUPPORTED_SOURCE_FLAGS = UF_HIDDEN
OMITTED_DARWIN_XATTRS = (
    "com.apple.FinderInfo",
    "com.apple.macl",
    "com.apple.metadata:com_apple_backup_excludeItem",
    "com.apple.provenance",
)
EXTENDED_METADATA_POLICY = {
    "darwin_omitted_xattr_names": list(OMITTED_DARWIN_XATTRS),
    "xattr_counts": "aggregate_name_counts_only",
    "unknown_xattrs": "reject",
    "acl": "reject",
}
STABILITY_CLAIM = "no_drift_observed_across_pre_post_inventory_and_per_file_hashes"
ATOMICITY_SCOPE = "no_cross_file_or_application_atomicity"
ROOT_CLOSURE_SCOPE = (
    "declared_root_only_external_links_not_traversed_mounts_and_external_hardlinks_rejected"
)
CAPTURE_ID_CHARS = "0123456789abcdef"
PROTECTED_COMPONENTS = {"f01", "d43", "holdout", "reserved", "protected"}
PRIVATE_COMPONENTS = {".ssh", "keychains", "credentials", "secrets", "wallets"}


class TreeCustodyError(ValueError):
    """The source, archive, or restore target violates the custody contract."""


@dataclass(frozen=True)
class TreeRoot:
    name: str
    authority_id: str
    recovery_point_id: str
    path: Path
    scope: str = "non_protected"


def _valid_name(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(ord(char) < 32 for char in value)
    ):
        raise TreeCustodyError(f"{label}_invalid")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise TreeCustodyError(f"{label}_invalid") from exc
    return value


def _component(value: object, *, protected: bool) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or len(os.fsencode(value)) > MAX_COMPONENT_BYTES
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or ":" in value
        or any(ord(char) < 32 for char in value)
    ):
        raise TreeCustodyError("unsafe_path_component")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise TreeCustodyError("path_component_not_utf8") from exc
    lowered = value.casefold()
    if lowered in PRIVATE_COMPONENTS:
        raise TreeCustodyError("excluded_path_component")
    if lowered.startswith(".env") or lowered.endswith((".key", ".pem")):
        raise TreeCustodyError("excluded_sensitive_member")
    if not protected and lowered in PROTECTED_COMPONENTS:
        raise TreeCustodyError("excluded_path_component")
    return value


def _relative(value: object, *, protected: bool) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise TreeCustodyError("unsafe_relative_path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or not path.parts:
        raise TreeCustodyError("unsafe_relative_path")
    if len(path.parts) > MAX_PATH_DEPTH:
        raise TreeCustodyError("path_depth_limit")
    for part in path.parts:
        _component(part, protected=protected)
    return value


def _validate_root(root: TreeRoot) -> None:
    _valid_name(root.name, "root_name")
    _valid_name(root.authority_id, "authority_id")
    _valid_name(root.recovery_point_id, "recovery_point_id")
    if root.scope not in {"non_protected", "protected_opaque"}:
        raise TreeCustodyError("explicit_scope_required")
    _component(root.name, protected=root.scope == "protected_opaque")
    if not root.path.is_absolute() or ".." in root.path.parts:
        raise TreeCustodyError("absolute_source_root_required")
    if not root.path.name:
        raise TreeCustodyError("volume_root_not_allowed")
    protected = root.scope == "protected_opaque"
    for part in root.path.parts[1:]:
        _component(part, protected=protected)


def _open_absolute_directory(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise TreeCustodyError("source_root_path_invalid")
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = child
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise TreeCustodyError("source_root_not_directory")
        result = current
        current = -1
        return result
    except OSError as exc:
        raise TreeCustodyError("source_root_open_failed") from exc
    finally:
        if current >= 0:
            os.close(current)


def _sig(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        stat.S_IFMT(st.st_mode),
        stat.S_IMODE(st.st_mode),
        st.st_uid,
        st.st_gid,
        st.st_size,
        st.st_nlink,
        st.st_mtime_ns,
        st.st_ctime_ns,
        getattr(st, "st_flags", 0),
    )


def _validated_source_flags(st: os.stat_result) -> int:
    flags = getattr(st, "st_flags", 0)
    if type(flags) is not int or flags < 0 or flags & ~SUPPORTED_SOURCE_FLAGS:
        raise TreeCustodyError("source_flags_unsupported")
    if flags and sys.platform != "darwin":
        raise TreeCustodyError("source_flags_platform_unsupported")
    return flags


def _check_extended_metadata(path: bytes | str, *, is_link: bool) -> dict[str, int]:
    """Fail closed if xattrs/resource forks/ACL metadata would be omitted."""
    try:
        names = _list_xattrs(path, is_link=is_link)
    except OSError as exc:
        if exc.errno in {errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}:
            names = []
        else:
            raise TreeCustodyError("xattr_detection_failed") from exc
    ignored = (
        {name.encode() for name in OMITTED_DARWIN_XATTRS} if sys.platform == "darwin" else set()
    )
    if set(names) - ignored:
        # This includes macOS resource forks and Finder metadata, plus POSIX ACL
        # xattrs on platforms that represent ACLs this way.
        raise TreeCustodyError("extended_metadata_not_preserved")
    if sys.platform == "darwin" and _darwin_extended_acl(path, is_link=is_link):
        raise TreeCustodyError("extended_metadata_not_preserved")
    if sys.platform not in {"darwin", "linux"}:
        raise TreeCustodyError("acl_detection_unavailable")
    return _xattr_counts(names, ignored)


def _check_extended_metadata_fd(fd: int) -> dict[str, int]:
    """Descriptor-based metadata probe for regular files and directories."""
    names = _list_xattrs_fd(fd)
    ignored = (
        {name.encode() for name in OMITTED_DARWIN_XATTRS} if sys.platform == "darwin" else set()
    )
    if set(names) - ignored:
        raise TreeCustodyError("extended_metadata_not_preserved")
    if sys.platform == "darwin":
        try:
            libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            function = libc.acl_get_fd_np
            function.argtypes = [ctypes.c_int, ctypes.c_int]
            function.restype = ctypes.c_void_p
            ctypes.set_errno(0)
            acl = function(fd, 0x00000100)
            if acl:
                libc.acl_free.argtypes = [ctypes.c_void_p]
                libc.acl_free(acl)
                raise TreeCustodyError("extended_metadata_not_preserved")
            error = ctypes.get_errno()
            if error not in {
                0,
                errno.EINVAL,
                errno.ENOENT,
                getattr(errno, "ENOATTR", errno.ENODATA),
            }:
                raise TreeCustodyError("acl_detection_failed")
        except TreeCustodyError:
            raise
        except Exception as exc:
            raise TreeCustodyError("acl_detection_unavailable") from exc
    elif sys.platform != "linux":
        raise TreeCustodyError("acl_detection_unavailable")
    return _xattr_counts(names, ignored)


def _xattr_counts(names: list[bytes], ignored: set[bytes]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for name in names:
        if name not in ignored:
            raise TreeCustodyError("extended_metadata_not_preserved")
        try:
            counts[name.decode("utf-8", errors="strict")] += 1
        except UnicodeDecodeError as exc:
            raise TreeCustodyError("xattr_name_not_utf8") from exc
    return dict(sorted(counts.items()))


def _list_xattrs_fd(fd: int) -> list[bytes]:
    library = "/usr/lib/libSystem.B.dylib" if sys.platform == "darwin" else None
    libc = ctypes.CDLL(library, use_errno=True)
    function = getattr(libc, "flistxattr", None)
    if function is None:
        raise TreeCustodyError("xattr_detection_unavailable")
    if sys.platform == "darwin":
        function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]

        def invoke(buffer, size):
            return function(fd, buffer, size, 0)
    else:
        function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]

        def invoke(buffer, size):
            return function(fd, buffer, size)

    function.restype = ctypes.c_ssize_t
    size = invoke(None, 0)
    if size < 0:
        error = ctypes.get_errno()
        if error in {errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}:
            return []
        raise TreeCustodyError("xattr_detection_failed")
    if not size:
        return []
    buffer = ctypes.create_string_buffer(size)
    actual = invoke(buffer, size)
    if actual < 0:
        raise TreeCustodyError("xattr_detection_failed")
    return [item for item in buffer.raw[:actual].split(b"\0") if item]


def _list_xattrs(path: bytes | str, *, is_link: bool) -> list[bytes]:
    """Use the platform xattr syscall directly; Python builds may omit os.listxattr."""
    libc = ctypes.CDLL(None, use_errno=True)
    function_name = "llistxattr" if is_link else "listxattr"
    function = getattr(libc, function_name, None)
    if function is None:
        raise TreeCustodyError("xattr_detection_unavailable")
    encoded = os.fsencode(path)
    if sys.platform == "darwin":
        function.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        function.restype = ctypes.c_ssize_t

        def call(buffer, size):
            return function(encoded, buffer, size, 0)
    else:
        function.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
        function.restype = ctypes.c_ssize_t

        def call(buffer, size):
            return function(encoded, buffer, size)

    size = call(None, 0)
    if size < 0:
        error = ctypes.get_errno()
        if error in {errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}:
            return []
        raise OSError(error, os.strerror(error))
    if size == 0:
        return []
    buffer = ctypes.create_string_buffer(size)
    actual = call(buffer, size)
    if actual < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return [item for item in buffer.raw[:actual].split(b"\0") if item]


def _darwin_extended_acl(path: bytes | str, *, is_link: bool) -> bool:
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        function = libc.acl_get_link_np if is_link else libc.acl_get_file
        function.argtypes = [ctypes.c_char_p, ctypes.c_int]
        function.restype = ctypes.c_void_p
        ctypes.set_errno(0)
        acl = function(os.fsencode(path), 0x00000100)  # ACL_TYPE_EXTENDED
        if acl:
            libc.acl_free.argtypes = [ctypes.c_void_p]
            libc.acl_free(acl)
            return True
        error = ctypes.get_errno()
        if error not in {
            0,
            errno.EINVAL,
            errno.ENOENT,
            getattr(errno, "ENOATTR", errno.ENODATA),
        }:
            raise TreeCustodyError("acl_detection_failed")
        return False
    except TreeCustodyError:
        raise
    except Exception as exc:
        raise TreeCustodyError("acl_detection_unavailable") from exc


def _db_open(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute(
        "CREATE TABLE entries(path TEXT PRIMARY KEY, kind TEXT NOT NULL, mode INTEGER NOT NULL, "
        "uid INTEGER NOT NULL, gid INTEGER NOT NULL, size INTEGER NOT NULL, "
        "mtime INTEGER NOT NULL, ctime INTEGER NOT NULL, dev INTEGER NOT NULL, "
        "ino INTEGER NOT NULL, nlink INTEGER NOT NULL, "
        "sig BLOB NOT NULL, link_b64 TEXT, group_id TEXT, primary_path TEXT, digest TEXT, "
        "chunk_count INTEGER NOT NULL DEFAULT 0, chunk_pages TEXT NOT NULL DEFAULT '[]', "
        "omitted_xattrs TEXT NOT NULL DEFAULT '[]')"
    )
    db.execute(
        "CREATE TABLE groups(dev INTEGER NOT NULL, ino INTEGER NOT NULL, count INTEGER NOT NULL, "
        "nlink INTEGER NOT NULL, first_path TEXT NOT NULL, group_id TEXT NOT NULL, "
        "PRIMARY KEY(dev,ino))"
    )
    return db


def _row_from_stat(
    path: str,
    kind: str,
    st: os.stat_result,
    link_b64: str | None = None,
    omitted_xattrs: list[str] | None = None,
) -> tuple[Any, ...]:
    return (
        path,
        kind,
        stat.S_IMODE(st.st_mode),
        st.st_uid,
        st.st_gid,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
        st.st_dev,
        st.st_ino,
        st.st_nlink,
        json.dumps(_sig(st), separators=(",", ":")).encode(),
        link_b64,
        json.dumps(omitted_xattrs or [], separators=(",", ":")),
    )


def _walk_into_db(
    root_fd: int,
    root_device: int,
    db: sqlite3.Connection,
    *,
    protected: bool,
    capture_id: str,
) -> tuple[int, int, dict[str, int]]:
    count = 0
    total_bytes = 0
    xattr_counts: Counter[str] = Counter()

    def add(
        path: str,
        kind: str,
        st: os.stat_result,
        link_b64: str | None = None,
        omitted_xattrs: list[str] | None = None,
    ) -> None:
        nonlocal count, total_bytes
        count += 1
        if count > MAX_ENTRIES:
            raise TreeCustodyError("source_entry_limit")
        if st.st_dev != root_device:
            raise TreeCustodyError("source_mount_boundary")
        _validated_source_flags(st)
        xattr_counts.update(omitted_xattrs or [])
        if stat.S_ISREG(st.st_mode):
            total_bytes += st.st_size
            if total_bytes > MAX_SOURCE_BYTES:
                raise TreeCustodyError("source_size_limit")
        db.execute(
            "INSERT INTO entries(path,kind,mode,uid,gid,size,mtime,ctime,dev,ino,"
            "nlink,sig,link_b64,omitted_xattrs) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            _row_from_stat(path, kind, st, link_b64, omitted_xattrs),
        )
        if kind == "file" and st.st_nlink > 1:
            group_id = hashlib.sha256(
                f"{capture_id}:{st.st_dev}:{st.st_ino}".encode("ascii")
            ).hexdigest()
            db.execute(
                "INSERT INTO groups(dev,ino,count,nlink,first_path,group_id) VALUES(?,?,1,?,?,?) "
                "ON CONFLICT(dev,ino) DO UPDATE SET count=count+1",
                (st.st_dev, st.st_ino, st.st_nlink, path, group_id),
            )

    root_st = os.fstat(root_fd)
    if not stat.S_ISDIR(root_st.st_mode):
        raise TreeCustodyError("source_root_changed")
    root_xattrs = _check_extended_metadata_fd(root_fd)
    add("", "directory", root_st, omitted_xattrs=list(root_xattrs))

    def walk(dir_fd: int, prefix: str) -> None:
        try:
            iterator = os.scandir(dir_fd)
        except OSError as exc:
            raise TreeCustodyError("source_inventory_failed") from exc
        with iterator:
            for item in iterator:
                name = _component(item.name, protected=protected)
                rel = f"{prefix}/{name}" if prefix else name
                _relative(rel, protected=protected)
                try:
                    st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                except OSError as exc:
                    raise TreeCustodyError("source_inventory_changed") from exc
                if st.st_dev != root_device:
                    raise TreeCustodyError("source_mount_boundary")
                if stat.S_ISDIR(st.st_mode):
                    child_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=dir_fd,
                    )
                    try:
                        child_st = os.fstat(child_fd)
                        if _sig(child_st) != _sig(st):
                            raise TreeCustodyError("source_inventory_changed")
                        omitted_xattrs = _check_extended_metadata_fd(child_fd)
                        add(rel, "directory", st, omitted_xattrs=list(omitted_xattrs))
                        walk(child_fd, rel)
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(st.st_mode):
                    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
                    try:
                        if _sig(os.fstat(file_fd)) != _sig(st):
                            raise TreeCustodyError("source_inventory_changed")
                        omitted_xattrs = _check_extended_metadata_fd(file_fd)
                    finally:
                        os.close(file_fd)
                    add(rel, "file", st, omitted_xattrs=list(omitted_xattrs))
                elif stat.S_ISLNK(st.st_mode):
                    if sys.platform == "darwin" and hasattr(os, "O_SYMLINK"):
                        link_fd = os.open(name, os.O_RDONLY | os.O_SYMLINK, dir_fd=dir_fd)
                        try:
                            if not stat.S_ISLNK(os.fstat(link_fd).st_mode):
                                raise TreeCustodyError("source_inventory_changed")
                            omitted_xattrs = _check_extended_metadata_fd(link_fd)
                        finally:
                            os.close(link_fd)
                    else:
                        omitted_xattrs = _check_extended_metadata(
                            f"/dev/fd/{dir_fd}/{name}", is_link=True
                        )
                    try:
                        target = os.readlink(os.fsencode(name), dir_fd=dir_fd)
                        target_bytes = target if isinstance(target, bytes) else os.fsencode(target)
                    except OSError as exc:
                        raise TreeCustodyError("source_symlink_read_failed") from exc
                    add(
                        rel,
                        "symlink",
                        st,
                        base64.b64encode(target_bytes).decode("ascii"),
                        list(omitted_xattrs),
                    )
                else:
                    raise TreeCustodyError("source_special_file_rejected")

    walk(root_fd, "")
    db.commit()
    for dev, ino, group_count, nlink, _first_path, group_id in db.execute(
        "SELECT dev,ino,count,nlink,first_path,group_id FROM groups"
    ):
        if group_count != nlink:
            raise TreeCustodyError("hardlink_escapes_declared_root")
        primary_path = db.execute(
            "SELECT min(path) FROM entries WHERE dev=? AND ino=?", (dev, ino)
        ).fetchone()[0]
        db.execute(
            "UPDATE entries SET group_id=?,primary_path=? WHERE dev=? AND ino=?",
            (group_id, primary_path, dev, ino),
        )
    db.commit()
    return count, total_bytes, dict(sorted(xattr_counts.items()))


def _open_rel_dir(root_fd: int, relative: str) -> int:
    current = os.dup(root_fd)
    if not relative:
        return current
    try:
        for part in _relative(relative, protected=True).split("/"):
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = child
        result = current
        current = -1
        return result
    except OSError as exc:
        raise TreeCustodyError("source_path_changed") from exc
    finally:
        if current >= 0:
            os.close(current)


def _open_rel_file(root_fd: int, relative: str, expected_sig: bytes) -> int:
    parent, _, name = relative.rpartition("/")
    parent_fd = _open_rel_dir(root_fd, parent)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as exc:
        os.close(parent_fd)
        raise TreeCustodyError("source_file_open_failed") from exc
    os.close(parent_fd)
    if (
        not stat.S_ISREG(os.fstat(fd).st_mode)
        or json.dumps(_sig(os.fstat(fd)), separators=(",", ":")).encode() != expected_sig
    ):
        os.close(fd)
        raise TreeCustodyError("source_file_changed_before_read")
    return fd


def _chunk_file(
    writer: DescriptorWriter,
    base: str,
    file_id: str,
    root_fd: int,
    db: sqlite3.Connection,
    row: tuple[Any, ...],
) -> tuple[str, int, list[dict[str, Any]]]:
    path, _kind, _mode, _uid, _gid, size, _mtime, _ctime, _dev, _ino, _nlink, sig, _link, *_ = row
    fd = _open_rel_file(root_fd, path, sig)
    digest = hashlib.sha256()
    chunk_count = 0
    total = 0
    chunk_pages: list[dict[str, Any]] = []
    page_rows: list[dict[str, Any]] = []

    def flush_index_page() -> None:
        if not page_rows:
            return
        page_id = len(chunk_pages)
        rel = f"{base}/chunk-index/{file_id}-{page_id:06d}.json"
        raw = canonical_json(
            {"schema": "brain-host-tree-chunk-index-v1", "file_id": file_id, "rows": page_rows}
        )
        writer.create(rel, raw)
        chunk_pages.append({"path": rel, "sha256": sha256(raw), "count": len(page_rows)})
        page_rows.clear()

    try:
        while True:
            data = os.read(fd, CHUNK_BYTES)
            if not data:
                break
            total += len(data)
            if total > size:
                raise TreeCustodyError("source_file_grew_during_read")
            digest.update(data)
            chunk_sha = sha256(data)
            rel = f"{base}/chunks/{file_id}/{chunk_count:08d}-{chunk_sha}.bin"
            writer.create(rel, data)
            page_rows.append({"path": rel, "size_bytes": len(data), "sha256": chunk_sha})
            chunk_count += 1
            if len(page_rows) >= CHUNK_INDEX_ENTRIES:
                flush_index_page()
        if total != size or _sig(os.fstat(fd)) != tuple(json.loads(sig)):
            raise TreeCustodyError("source_file_changed_during_read")
    finally:
        os.close(fd)
    flush_index_page()
    file_sha = digest.hexdigest()
    db.execute(
        "UPDATE entries SET digest=?,chunk_count=?,chunk_pages=? WHERE path=?",
        (file_sha, chunk_count, json.dumps(chunk_pages, separators=(",", ":")), path),
    )
    db.commit()
    return file_sha, chunk_count, chunk_pages


def _entry_record(row: tuple[Any, ...], *, protected: bool) -> dict[str, Any]:
    (
        path,
        kind,
        mode,
        uid,
        gid,
        size,
        mtime,
        ctime,
        dev,
        ino,
        nlink,
        _sig_bytes,
        link_b64,
        group_id,
        primary_path,
        digest,
        chunk_count,
        chunk_pages,
        omitted_xattrs,
    ) = row
    if path:
        _relative(path, protected=protected)
    item: dict[str, Any] = {
        "path": path,
        "kind": kind,
        "mode": mode,
        "uid": uid,
        "gid": gid,
        "size_bytes": size,
        "mtime_ns": mtime,
        "source_ctime_ns": ctime,
        "source_device": dev,
        "source_inode": ino,
        "source_nlink": nlink,
        "source_flags": json.loads(_sig_bytes)[-1],
    }
    if kind == "symlink":
        item["target_b64"] = link_b64
    omitted_names = json.loads(omitted_xattrs)
    if omitted_names:
        item["omitted_xattrs"] = omitted_names
    if group_id:
        item["hardlink_group"] = group_id
        if kind == "file" and primary_path != path:
            item["kind"] = "hardlink"
            item["link_to"] = primary_path
        elif kind == "file":
            item["primary_path"] = primary_path
    if kind == "file":
        if primary_path in (None, path):
            item.update(
                {
                    "sha256": digest,
                    "chunk_count": chunk_count,
                    "chunk_index_pages": json.loads(chunk_pages),
                }
            )
    return item


def _emit_entry_pages(
    writer: DescriptorWriter, base: str, db: sqlite3.Connection, *, protected: bool
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    page_number = 0
    page_bytes = 0

    def flush() -> None:
        nonlocal page_number, page_bytes
        if not rows:
            return
        rel = f"{base}/entry-pages/{page_number:08d}.jsonl"
        raw = b"\n".join(canonical_json(row).rstrip(b"\n") for row in rows) + b"\n"
        if len(raw) > MAX_PAGE_BYTES:
            raise TreeCustodyError("entry_page_size_limit")
        writer.create(rel, raw)
        refs.append({"path": rel, "sha256": sha256(raw), "count": len(rows)})
        page_number += 1
        page_bytes = 0
        rows.clear()

    for row in db.execute(
        "SELECT path,kind,mode,uid,gid,size,mtime,ctime,dev,ino,nlink,sig,link_b64,"
        "group_id,primary_path,digest,chunk_count,chunk_pages,omitted_xattrs "
        "FROM entries ORDER BY path COLLATE BINARY"
    ):
        item = _entry_record(row, protected=protected)
        encoded = canonical_json(item)
        if rows and (len(rows) >= PAGE_ENTRIES or page_bytes + len(encoded) + 1 > MAX_PAGE_BYTES):
            flush()
        rows.append(item)
        page_bytes += len(encoded) + 1
    flush()
    return refs


def _emit_index_pages(
    writer: DescriptorWriter, base: str, entry_refs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for start in range(0, len(entry_refs), INDEX_ENTRIES):
        rows = entry_refs[start : start + INDEX_ENTRIES]
        index = start // INDEX_ENTRIES
        rel = f"{base}/index-pages/{index:08d}.json"
        raw = canonical_json({"schema": "brain-host-tree-index-v1", "rows": rows})
        writer.create(rel, raw)
        refs.append({"path": rel, "sha256": sha256(raw), "count": len(rows)})
    return refs


def _manifest(
    writer: DescriptorWriter,
    capture_id: str,
    *,
    max_bytes: int = MAX_MANIFEST_BYTES,
    expected_sha256: str | None = None,
) -> tuple[dict[str, Any], bytes, bytes]:
    base = f"{BASE}/{capture_id}"
    raw = writer.read_bytes(f"{base}/MANIFEST.json", max_bytes=max_bytes)
    if expected_sha256 is not None and sha256(raw) != expected_sha256:
        raise TreeCustodyError("restore_manifest_hash_mismatch")
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TreeCustodyError("manifest_invalid") from exc
    if not isinstance(manifest, dict) or canonical_json(manifest) != raw:
        raise TreeCustodyError("manifest_noncanonical")
    return manifest, raw, base.encode()


def _verify_internal(
    writer: DescriptorWriter, capture_id: str, *, require_complete: bool
) -> dict[str, Any]:
    if len(capture_id) != 32 or any(ch not in CAPTURE_ID_CHARS for ch in capture_id):
        raise TreeCustodyError("capture_id_invalid")
    base = f"{BASE}/{capture_id}"
    try:
        reservation_raw = writer.read_bytes(f"{base}/RESERVATION.json", max_bytes=64 * 1024)
        manifest_raw = writer.read_bytes(f"{base}/MANIFEST.json", max_bytes=MAX_MANIFEST_BYTES)
    except (OSError, StorageError) as exc:
        raise TreeCustodyError("capture_artifact_missing") from exc
    try:
        reservation = json.loads(reservation_raw)
        manifest = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TreeCustodyError("capture_metadata_invalid") from exc
    if canonical_json(reservation) != reservation_raw or reservation != {
        "schema": RESERVATION_SCHEMA,
        "capture_id": capture_id,
        "state": "incomplete_until_complete_marker",
    }:
        raise TreeCustodyError("reservation_invalid")
    if canonical_json(manifest) != manifest_raw or not isinstance(manifest, dict):
        raise TreeCustodyError("manifest_noncanonical")
    required = {
        "schema",
        "capture_id",
        "scope",
        "root_name",
        "authority_id",
        "recovery_point_id",
        "recovery_point_source",
        "source_root_sha256",
        "symlink_mode_policy",
        "extended_metadata_policy",
        "xattr_name_counts",
        "source_flags_or",
        "capture_start_ns",
        "capture_end_ns",
        "stability_claim",
        "atomicity_scope",
        "root_closure_scope",
        "created_ns",
        "entry_count",
        "file_count",
        "symlink_count",
        "hardlink_count",
        "source_bytes",
        "entry_page_count",
        "index_pages",
    }
    if (
        set(manifest) != required
        or manifest.get("schema") != SCHEMA
        or manifest.get("capture_id") != capture_id
    ):
        raise TreeCustodyError("manifest_contract_invalid")
    if manifest.get("scope") not in {"non_protected", "protected_opaque"}:
        raise TreeCustodyError("manifest_scope_invalid")
    _valid_name(manifest.get("root_name"), "root_name")
    _component(manifest.get("root_name"), protected=manifest["scope"] == "protected_opaque")
    _valid_name(manifest.get("authority_id"), "authority_id")
    _valid_name(manifest.get("recovery_point_id"), "recovery_point_id")
    if manifest.get("recovery_point_source") != "caller_asserted":
        raise TreeCustodyError("recovery_point_not_asserted")
    if manifest.get("symlink_mode_policy") != "source_mode_recorded_restore_uses_host_default":
        raise TreeCustodyError("symlink_mode_policy_invalid")
    if manifest.get("extended_metadata_policy") != EXTENDED_METADATA_POLICY:
        raise TreeCustodyError("extended_metadata_policy_invalid")
    for key in (
        "entry_count",
        "file_count",
        "symlink_count",
        "hardlink_count",
        "source_bytes",
        "entry_page_count",
        "created_ns",
        "capture_start_ns",
        "capture_end_ns",
    ):
        if type(manifest.get(key)) is not int or manifest[key] < 0:
            raise TreeCustodyError("manifest_count_invalid")
    if manifest["entry_count"] > MAX_ENTRIES or manifest["source_bytes"] > MAX_SOURCE_BYTES:
        raise TreeCustodyError("manifest_limit_exceeded")
    if manifest["capture_end_ns"] < manifest["capture_start_ns"]:
        raise TreeCustodyError("capture_interval_invalid")
    if (
        manifest.get("stability_claim") != STABILITY_CLAIM
        or manifest.get("atomicity_scope") != ATOMICITY_SCOPE
        or manifest.get("root_closure_scope") != ROOT_CLOSURE_SCOPE
    ):
        raise TreeCustodyError("consistency_scope_invalid")
    if (
        type(manifest.get("source_flags_or")) is not int
        or manifest["source_flags_or"] < 0
        or manifest["source_flags_or"] & ~SUPPORTED_SOURCE_FLAGS
    ):
        raise TreeCustodyError("source_flags_invalid")
    declared_xattrs = manifest.get("xattr_name_counts")
    if not isinstance(declared_xattrs, dict) or any(
        name not in OMITTED_DARWIN_XATTRS or type(amount) is not int or amount <= 0
        for name, amount in declared_xattrs.items()
    ):
        raise TreeCustodyError("xattr_counts_invalid")
    _strict_hash(manifest.get("source_root_sha256"), "source_root")
    indexes = manifest.get("index_pages")
    if not isinstance(indexes, list) or len(indexes) > 4096:
        raise TreeCustodyError("index_pages_invalid")

    with tempfile.TemporaryDirectory(prefix="brain-tree-verify-") as temp:
        db = sqlite3.connect(Path(temp) / "verify.sqlite")
        db.execute("CREATE TABLE expected(path TEXT PRIMARY KEY, kind TEXT NOT NULL)")
        db.execute("CREATE TABLE actual(path TEXT PRIMARY KEY, kind TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE entries(path TEXT PRIMARY KEY, kind TEXT, uid INTEGER, gid INTEGER, "
            "mode INTEGER, size INTEGER, mtime INTEGER, digest TEXT, target TEXT, "
            "group_id TEXT, primary_path TEXT, chunks TEXT)"
        )
        db.execute(
            "CREATE TABLE link_groups(group_id TEXT PRIMARY KEY, nlink INTEGER NOT NULL, "
            "count INTEGER NOT NULL)"
        )
        db.executemany(
            "INSERT INTO expected(path,kind) VALUES(?, 'file')",
            [(f"{base}/RESERVATION.json",), (f"{base}/MANIFEST.json",)],
        )
        if require_complete:
            db.execute(
                "INSERT INTO expected(path,kind) VALUES(?, 'file')", (f"{base}/COMPLETE.json",)
            )

        def record_path(path: str, kind: str) -> None:
            _relative(path, protected=True)
            if db.execute("SELECT 1 FROM expected WHERE path=?", (path,)).fetchone():
                raise TreeCustodyError("archive_path_duplicate")
            db.execute("INSERT INTO expected(path,kind) VALUES(?,?)", (path, kind))
            parent = path.rpartition("/")[0]
            while parent and parent != base:
                found = db.execute("SELECT kind FROM expected WHERE path=?", (parent,)).fetchone()
                if found and found[0] != "dir":
                    raise TreeCustodyError("archive_parent_not_directory")
                if not found:
                    db.execute("INSERT INTO expected(path,kind) VALUES(?, 'dir')", (parent,))
                parent = parent.rpartition("/")[0]

        def record_hardlink(group_id: str, nlink: int) -> None:
            cursor = db.execute(
                "INSERT INTO link_groups(group_id,nlink,count) VALUES(?,?,1) "
                "ON CONFLICT(group_id) DO UPDATE SET count=count+1 "
                "WHERE nlink=excluded.nlink",
                (group_id, nlink),
            )
            if cursor.rowcount != 1:
                raise TreeCustodyError("hardlink_group_metadata_mismatch")

        def read_index_page(ref: dict[str, Any]) -> list[dict[str, Any]]:
            if (
                not isinstance(ref, dict)
                or set(ref) != {"path", "sha256", "count"}
                or type(ref.get("count")) is not int
            ):
                raise TreeCustodyError("index_ref_invalid")
            path = _relative(ref.get("path"), protected=True)
            _strict_hash(ref.get("sha256"), "index_page")
            data = writer.read_bytes(path, max_bytes=MAX_PAGE_BYTES)
            if sha256(data) != ref["sha256"]:
                raise TreeCustodyError("index_hash_mismatch")
            obj = json.loads(data)
            if (
                canonical_json(obj) != data
                or set(obj) != {"schema", "rows"}
                or obj["schema"] != "brain-host-tree-index-v1"
                or not isinstance(obj["rows"], list)
                or len(obj["rows"]) != ref["count"]
            ):
                raise TreeCustodyError("index_page_invalid")
            record_path(path, "file")
            return obj["rows"]

        entry_refs: list[dict[str, Any]] = []
        for iref in indexes:
            rows = read_index_page(iref)
            entry_refs.extend(rows)
        if len(entry_refs) != manifest["entry_page_count"]:
            raise TreeCustodyError("entry_page_count_mismatch")
        entry_total = file_total = symlink_total = hardlink_total = bytes_total = 0
        observed_xattrs: Counter[str] = Counter()
        observed_source_flags = 0
        previous_rel: str | None = None
        for ref in entry_refs:
            if (
                not isinstance(ref, dict)
                or set(ref) != {"path", "sha256", "count"}
                or type(ref["count"]) is not int
            ):
                raise TreeCustodyError("entry_page_ref_invalid")
            path = _relative(ref["path"], protected=True)
            raw_page = writer.read_bytes(path, max_bytes=MAX_PAGE_BYTES)
            if sha256(raw_page) != ref["sha256"]:
                raise TreeCustodyError("entry_page_hash_mismatch")
            record_path(path, "file")
            lines = raw_page.splitlines()
            if len(lines) != ref["count"]:
                raise TreeCustodyError("entry_page_count_mismatch")
            for line in lines:
                entry = json.loads(line)
                if not isinstance(entry, dict) or canonical_json(entry).rstrip(b"\n") != line:
                    raise TreeCustodyError("entry_invalid")
                kind = entry.get("kind")
                rel = entry.get("path")
                if not isinstance(rel, str) or (previous_rel is not None and rel <= previous_rel):
                    raise TreeCustodyError("entry_order_invalid")
                previous_rel = rel
                if rel != "":
                    _relative(rel, protected=manifest["scope"] == "protected_opaque")
                if kind not in {"directory", "file", "hardlink", "symlink"}:
                    raise TreeCustodyError("entry_kind_invalid")
                if kind == "directory" and rel == "":
                    pass
                elif not rel:
                    raise TreeCustodyError("root_entry_invalid")
                fields = (
                    "mode",
                    "uid",
                    "gid",
                    "size_bytes",
                    "mtime_ns",
                    "source_ctime_ns",
                    "source_device",
                    "source_inode",
                    "source_nlink",
                    "source_flags",
                )
                if any(type(entry.get(k)) is not int or entry[k] < 0 for k in fields):
                    raise TreeCustodyError("entry_metadata_invalid")
                if entry["mode"] & ~0o7777:
                    raise TreeCustodyError("entry_mode_invalid")
                link_b64 = entry.get("target_b64")
                source_flags = entry["source_flags"]
                if source_flags & ~SUPPORTED_SOURCE_FLAGS:
                    raise TreeCustodyError("source_flags_unsupported")
                observed_source_flags |= source_flags
                omitted_xattrs = entry.get("omitted_xattrs", [])
                if (
                    not isinstance(omitted_xattrs, list)
                    or omitted_xattrs != sorted(set(omitted_xattrs))
                    or any(name not in OMITTED_DARWIN_XATTRS for name in omitted_xattrs)
                ):
                    raise TreeCustodyError("entry_xattr_omission_invalid")
                observed_xattrs.update(omitted_xattrs)
                optional_xattrs = {"omitted_xattrs"} if omitted_xattrs else set()
                group = entry.get("hardlink_group")
                primary = entry.get("primary_path")
                chunk_refs = entry.get("chunk_index_pages", [])
                digest = entry.get("sha256")
                common_fields = set(fields) | {"path", "kind"}
                if kind == "symlink":
                    if set(entry) != common_fields | {"target_b64"} | optional_xattrs:
                        raise TreeCustodyError("symlink_entry_invalid")
                    try:
                        base64.b64decode(link_b64, validate=True)
                    except Exception as exc:
                        raise TreeCustodyError("symlink_target_invalid") from exc
                    symlink_total += 1
                elif kind == "hardlink":
                    link_to = entry.get("link_to")
                    if (
                        set(entry)
                        != common_fields | {"hardlink_group", "link_to"} | optional_xattrs
                        or not isinstance(group, str)
                        or not isinstance(link_to, str)
                        or link_to == rel
                    ):
                        raise TreeCustodyError("hardlink_entry_invalid")
                    _relative(link_to, protected=manifest["scope"] == "protected_opaque")
                    primary = link_to
                    hardlink_total += 1
                    bytes_total += entry["size_bytes"]
                    record_hardlink(group, entry["source_nlink"])
                elif kind == "file":
                    allowed = (
                        common_fields
                        | {"sha256", "chunk_count", "chunk_index_pages"}
                        | optional_xattrs
                    )
                    if group is not None:
                        allowed |= {"hardlink_group"}
                    if set(entry) not in {
                        frozenset(allowed),
                        frozenset(allowed | {"primary_path"}),
                    }:
                        raise TreeCustodyError("file_entry_fields_invalid")
                    if (
                        not isinstance(digest, str)
                        or len(digest) != 64
                        or type(entry.get("chunk_count")) is not int
                        or not isinstance(chunk_refs, list)
                    ):
                        raise TreeCustodyError("file_entry_invalid")
                    _strict_hash(digest, "file")
                    if group is not None and (
                        not isinstance(group, str) or entry.get("primary_path") != rel
                    ):
                        raise TreeCustodyError("hardlink_primary_invalid")
                    if group is not None:
                        record_hardlink(group, entry["source_nlink"])
                    file_total += 1
                    bytes_total += entry["size_bytes"]
                    file_id = hashlib.sha256(rel.encode("utf-8")).hexdigest()
                    file_digest = hashlib.sha256()
                    chunk_total = 0
                    chunk_seen = 0
                    for cref in chunk_refs:
                        if (
                            not isinstance(cref, dict)
                            or set(cref) != {"path", "sha256", "count"}
                            or type(cref["count"]) is not int
                        ):
                            raise TreeCustodyError("chunk_index_ref_invalid")
                        cpath = _relative(cref["path"], protected=True)
                        _strict_hash(cref["sha256"], "chunk_index")
                        cdata = writer.read_bytes(cpath, max_bytes=MAX_PAGE_BYTES)
                        if sha256(cdata) != cref["sha256"]:
                            raise TreeCustodyError("chunk_index_hash_mismatch")
                        cobj = json.loads(cdata)
                        if (
                            canonical_json(cobj) != cdata
                            or set(cobj) != {"schema", "file_id", "rows"}
                            or cobj["schema"] != "brain-host-tree-chunk-index-v1"
                            or cobj["file_id"] != file_id
                            or not isinstance(cobj["rows"], list)
                            or len(cobj["rows"]) != cref["count"]
                        ):
                            raise TreeCustodyError("chunk_index_invalid")
                        record_path(cpath, "file")
                        for chunk in cobj["rows"]:
                            if not isinstance(chunk, dict) or set(chunk) != {
                                "path",
                                "size_bytes",
                                "sha256",
                            }:
                                raise TreeCustodyError("chunk_ref_invalid")
                            chunk_path = _relative(chunk["path"], protected=True)
                            _strict_hash(chunk["sha256"], "chunk")
                            chunk_data = writer.read_bytes(chunk_path, max_bytes=CHUNK_BYTES)
                            if (
                                len(chunk_data) != chunk["size_bytes"]
                                or sha256(chunk_data) != chunk["sha256"]
                            ):
                                raise TreeCustodyError("chunk_hash_mismatch")
                            record_path(chunk_path, "file")
                            file_digest.update(chunk_data)
                            chunk_total += len(chunk_data)
                            chunk_seen += 1
                        chunk_refs_count = len(cobj["rows"])
                        if chunk_refs_count == 0 and entry["size_bytes"] != 0:
                            raise TreeCustodyError("chunk_index_empty")
                    if chunk_total != entry["size_bytes"] or chunk_seen != entry["chunk_count"]:
                        raise TreeCustodyError("file_chunk_accounting_invalid")
                    if file_digest.hexdigest() != digest:
                        raise TreeCustodyError("file_hash_mismatch")
                elif kind == "directory" and set(entry) != common_fields | optional_xattrs:
                    raise TreeCustodyError("directory_entry_fields_invalid")
                entry_total += 1
                try:
                    db.execute(
                        "INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            rel,
                            kind,
                            entry["uid"],
                            entry["gid"],
                            entry["mode"],
                            entry["size_bytes"],
                            entry["mtime_ns"],
                            digest,
                            link_b64,
                            group,
                            primary,
                            json.dumps(chunk_refs, separators=(",", ":")),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise TreeCustodyError("entry_path_duplicate") from exc
        if (
            entry_total != manifest["entry_count"]
            or file_total != manifest["file_count"]
            or symlink_total != manifest["symlink_count"]
            or hardlink_total != manifest["hardlink_count"]
            or bytes_total != manifest["source_bytes"]
            or dict(sorted(observed_xattrs.items())) != declared_xattrs
            or observed_source_flags != manifest["source_flags_or"]
        ):
            raise TreeCustodyError("manifest_totals_mismatch")
        # Every non-root member must have a directory parent; hardlinks resolve
        # only to the first regular-file entry in the same group.
        for rel, kind, group, primary in db.execute(
            "SELECT path,kind,group_id,primary_path FROM entries"
        ):
            if rel:
                parent = rel.rpartition("/")[0]
                found = db.execute("SELECT kind FROM entries WHERE path=?", (parent,)).fetchone()
                if not found or found[0] != "directory":
                    raise TreeCustodyError("entry_parent_missing")
            if kind == "hardlink":
                found = db.execute(
                    "SELECT kind,group_id FROM entries WHERE path=?", (primary,)
                ).fetchone()
                if not found or found != ("file", group):
                    raise TreeCustodyError("hardlink_target_invalid")
        if db.execute("SELECT kind FROM entries WHERE path='' ").fetchone() != ("directory",):
            raise TreeCustodyError("root_directory_missing")
        if db.execute("SELECT 1 FROM link_groups WHERE count != nlink LIMIT 1").fetchone():
            raise TreeCustodyError("hardlink_membership_invalid")
        db.commit()
        # Check the exact immutable archive tree with a bounded temporary index.
        if require_complete:
            complete_raw = writer.read_bytes(f"{base}/COMPLETE.json", max_bytes=64 * 1024)
            complete = json.loads(complete_raw)
            complete_ns = complete.get("complete_ns")
            if (
                canonical_json(complete) != complete_raw
                or complete
                != {
                    "schema": COMPLETE_SCHEMA,
                    "capture_id": capture_id,
                    "manifest_sha256": sha256(manifest_raw),
                    "entry_count": manifest["entry_count"],
                    "capture_start_ns": manifest["capture_start_ns"],
                    "capture_end_ns": manifest["capture_end_ns"],
                    "complete_ns": complete_ns,
                }
                or type(complete_ns) is not int
                or complete_ns < manifest["capture_end_ns"]
            ):
                raise TreeCustodyError("complete_marker_invalid")
        actual_count = 0
        stack = [base]
        while stack:
            d = stack.pop()
            for name in writer.list_names(d):
                _component(name, protected=True)
                p = f"{d}/{name}"
                st = writer.stat(p)
                kind = "dir" if stat.S_ISDIR(st.st_mode) else "file"
                db.execute("INSERT INTO actual(path,kind) VALUES(?,?)", (p, kind))
                actual_count += 1
                if kind == "dir":
                    stack.append(p)
        expected_n = db.execute("SELECT count(*) FROM expected").fetchone()[0]
        if expected_n != actual_count:
            raise TreeCustodyError("archive_membership_mismatch")
        mismatch = db.execute(
            "SELECT 1 FROM expected e LEFT JOIN actual a ON e.path=a.path AND e.kind=a.kind "
            "WHERE a.path IS NULL LIMIT 1"
        ).fetchone()
        if mismatch:
            raise TreeCustodyError("archive_membership_mismatch")
        extra = db.execute(
            "SELECT 1 FROM actual a LEFT JOIN expected e ON e.path=a.path AND e.kind=a.kind "
            "WHERE e.path IS NULL LIMIT 1"
        ).fetchone()
        if extra:
            raise TreeCustodyError("archive_membership_mismatch")
        db.close()

    if require_complete is False:
        # Marker must not already exist when verifying prepublication state.
        if writer.exists(f"{base}/COMPLETE.json"):
            raise TreeCustodyError("complete_marker_already_present")
    result = {
        "schema": SCHEMA,
        "capture_id": capture_id,
        "scope": manifest["scope"],
        "manifest_sha256": sha256(manifest_raw),
        "entry_count": manifest["entry_count"],
        "file_count": manifest["file_count"],
        "symlink_count": manifest["symlink_count"],
        "hardlink_count": manifest["hardlink_count"],
        "source_bytes": manifest["source_bytes"],
        "xattr_name_counts": manifest["xattr_name_counts"],
        "omitted_metadata_reason": "darwin_host_metadata_policy",
        "verified_bytes": True,
        "complete": require_complete,
    }
    if manifest["scope"] != "protected_opaque":
        result["root_name"] = manifest["root_name"]
        result["capture_start_ns"] = manifest["capture_start_ns"]
        result["capture_end_ns"] = manifest["capture_end_ns"]
        if require_complete:
            result["complete_ns"] = complete_ns
        result["stability_claim"] = manifest["stability_claim"]
        result["atomicity_scope"] = manifest["atomicity_scope"]
        result["root_closure_scope"] = manifest["root_closure_scope"]
    return result


def _strict_hash(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise TreeCustodyError(f"{label}_hash_invalid")
    return value


def _restore_read_pinned(
    writer: DescriptorWriter,
    path: str,
    expected_hash: str,
    *,
    max_bytes: int,
    label: str,
) -> bytes:
    """Read an archive object and bind this restore read to its verified digest."""
    try:
        data = writer.read_bytes(path, max_bytes=max_bytes)
    except (OSError, StorageError) as exc:
        raise TreeCustodyError(f"restore_{label}_read_failed") from exc
    if sha256(data) != expected_hash:
        raise TreeCustodyError(f"restore_{label}_hash_mismatch")
    return data


def capture_tree(
    writer: DescriptorWriter,
    root: TreeRoot,
    *,
    capture_id: str | None = None,
) -> dict[str, Any]:
    _validate_root(root)
    capture_start_ns = time.time_ns()
    chosen = capture_id or secrets.token_hex(16)
    if len(chosen) != 32 or any(c not in CAPTURE_ID_CHARS for c in chosen):
        raise TreeCustodyError("capture_id_invalid")
    source_fd = _open_absolute_directory(root.path)
    source_stat = os.fstat(source_fd)
    source_sig = _sig(source_stat)
    base = f"{BASE}/{chosen}"
    with tempfile.TemporaryDirectory(prefix="brain-tree-capture-") as temp:
        db = _db_open(Path(temp) / "inventory.sqlite")
        try:
            entry_count, source_bytes, xattr_name_counts = _walk_into_db(
                source_fd,
                source_stat.st_dev,
                db,
                protected=root.scope == "protected_opaque",
                capture_id=chosen,
            )
            writer.mkdir(BASE)
            with writer.lock("host-recovery/.tree-custody.lock"):
                try:
                    writer.list_names(base)
                except FileNotFoundError:
                    pass
                else:
                    raise TreeCustodyError("capture_id_collision")
                writer.mkdir(base)
                writer.create(
                    f"{base}/RESERVATION.json",
                    canonical_json(
                        {
                            "schema": RESERVATION_SCHEMA,
                            "capture_id": chosen,
                            "state": "incomplete_until_complete_marker",
                        }
                    ),
                )
                file_count = symlink_count = hardlink_count = 0
                for row in db.execute(
                    "SELECT path,kind,mode,uid,gid,size,mtime,ctime,dev,ino,nlink,sig,link_b64,"
                    "group_id,primary_path,digest,chunk_count,chunk_pages FROM entries "
                    "WHERE kind='file' AND (primary_path IS NULL OR primary_path=path) "
                    "ORDER BY path COLLATE BINARY"
                ):
                    path = row[0]
                    file_id = hashlib.sha256(path.encode("utf-8")).hexdigest()
                    digest, chunk_count, _pages = _chunk_file(
                        writer, base, file_id, source_fd, db, row
                    )
                    file_count += 1
                for kind, primary_path, path in db.execute(
                    "SELECT kind,primary_path,path FROM entries"
                ):
                    symlink_count += int(kind == "symlink")
                    hardlink_count += int(kind == "file" and primary_path not in (None, path))
                entry_refs = _emit_entry_pages(
                    writer, base, db, protected=root.scope == "protected_opaque"
                )
                index_refs = _emit_index_pages(writer, base, entry_refs)
                # Re-inventory the declared root and compare every signature and
                # link target before verifying the archive and publishing marker.
                after_db = sqlite3.connect(Path(temp) / "after.sqlite")
                after_db.execute(
                    "CREATE TABLE entries(path TEXT PRIMARY KEY, kind TEXT NOT NULL, "
                    "mode INTEGER NOT NULL, uid INTEGER NOT NULL, gid INTEGER NOT NULL, "
                    "size INTEGER NOT NULL, mtime INTEGER NOT NULL, ctime INTEGER NOT NULL, "
                    "dev INTEGER NOT NULL, ino INTEGER NOT NULL, nlink INTEGER NOT NULL, "
                    "sig BLOB NOT NULL, link_b64 TEXT, group_id TEXT, primary_path TEXT, "
                    "digest TEXT, chunk_count INTEGER NOT NULL DEFAULT 0, "
                    "chunk_pages TEXT NOT NULL DEFAULT '[]', "
                    "omitted_xattrs TEXT NOT NULL DEFAULT '[]')"
                )
                after_db.execute(
                    "CREATE TABLE groups(dev INTEGER NOT NULL, ino INTEGER NOT NULL, "
                    "count INTEGER NOT NULL, nlink INTEGER NOT NULL, first_path TEXT NOT NULL, "
                    "group_id TEXT NOT NULL, PRIMARY KEY(dev,ino))"
                )
                after_count, after_bytes, after_xattrs = _walk_into_db(
                    source_fd,
                    source_stat.st_dev,
                    after_db,
                    protected=root.scope == "protected_opaque",
                    capture_id=chosen,
                )
                if (after_count, after_bytes) != (entry_count, source_bytes):
                    raise TreeCustodyError("source_membership_drift")
                if after_xattrs != xattr_name_counts:
                    raise TreeCustodyError("source_xattr_membership_drift")
                # SQLite cannot join separate connections without ATTACH; compare
                # bounded rows through ordered cursors instead of materializing.
                before_rows = db.execute(
                    "SELECT path,kind,sig,link_b64,group_id,primary_path,omitted_xattrs "
                    "FROM entries "
                    "ORDER BY path COLLATE BINARY"
                )
                after_rows = after_db.execute(
                    "SELECT path,kind,sig,link_b64,group_id,primary_path,omitted_xattrs "
                    "FROM entries "
                    "ORDER BY path COLLATE BINARY"
                )
                for before, after in zip(before_rows, after_rows, strict=True):
                    if before != after:
                        raise TreeCustodyError("source_member_signature_drift")
                source_now_fd = _open_absolute_directory(root.path)
                try:
                    now_stat = os.fstat(source_now_fd)
                    if _sig(now_stat) != source_sig:
                        raise TreeCustodyError("source_root_replaced")
                finally:
                    os.close(source_now_fd)
                capture_end_ns = time.time_ns()
                if capture_end_ns < capture_start_ns:
                    raise TreeCustodyError("capture_clock_moved_backwards")
                source_flags_or = 0
                for (sig_bytes,) in db.execute("SELECT sig FROM entries"):
                    source_flags_or |= json.loads(sig_bytes)[-1]
                manifest = {
                    "schema": SCHEMA,
                    "capture_id": chosen,
                    "scope": root.scope,
                    "root_name": root.name,
                    "authority_id": root.authority_id,
                    "recovery_point_id": root.recovery_point_id,
                    "recovery_point_source": "caller_asserted",
                    "source_root_sha256": hashlib.sha256(os.fsencode(root.path)).hexdigest(),
                    "symlink_mode_policy": "source_mode_recorded_restore_uses_host_default",
                    "extended_metadata_policy": EXTENDED_METADATA_POLICY,
                    "xattr_name_counts": xattr_name_counts,
                    "source_flags_or": source_flags_or,
                    "created_ns": capture_start_ns,
                    "capture_start_ns": capture_start_ns,
                    "capture_end_ns": capture_end_ns,
                    "stability_claim": STABILITY_CLAIM,
                    "atomicity_scope": ATOMICITY_SCOPE,
                    "root_closure_scope": ROOT_CLOSURE_SCOPE,
                    "entry_count": entry_count,
                    "file_count": file_count,
                    "symlink_count": symlink_count,
                    "hardlink_count": hardlink_count,
                    "source_bytes": source_bytes,
                    "entry_page_count": len(entry_refs),
                    "index_pages": index_refs,
                }
                manifest_raw = canonical_json(manifest)
                if len(manifest_raw) > MAX_MANIFEST_BYTES:
                    raise TreeCustodyError("manifest_size_limit")
                writer.create(f"{base}/MANIFEST.json", manifest_raw)
                verified = _verify_internal(writer, chosen, require_complete=False)
                complete_ns = time.time_ns()
                if complete_ns < capture_end_ns:
                    raise TreeCustodyError("capture_clock_moved_backwards")
                complete_raw = canonical_json(
                    {
                        "schema": COMPLETE_SCHEMA,
                        "capture_id": chosen,
                        "manifest_sha256": sha256(manifest_raw),
                        "entry_count": entry_count,
                        "capture_start_ns": capture_start_ns,
                        "capture_end_ns": capture_end_ns,
                        "complete_ns": complete_ns,
                    }
                )
                writer.create(f"{base}/COMPLETE.json", complete_raw)
                readback = writer.read_bytes(f"{base}/COMPLETE.json", max_bytes=64 * 1024)
                if readback != complete_raw:
                    raise TreeCustodyError("complete_marker_readback_mismatch")
                result = dict(verified)
                result["complete"] = True
                result["complete_ns"] = complete_ns
                if root.scope == "protected_opaque":
                    return {
                        "schema": SCHEMA,
                        "capture_id": chosen,
                        "scope": "protected_opaque",
                        "entry_count": entry_count,
                        "file_count": file_count,
                        "symlink_count": symlink_count,
                        "hardlink_count": hardlink_count,
                        "source_bytes": source_bytes,
                        "xattr_name_counts": xattr_name_counts,
                        "omitted_metadata_reason": "darwin_host_metadata_policy",
                        "manifest_sha256": sha256(manifest_raw),
                        "complete": True,
                    }
                return result
        except sqlite3.Error as exc:
            raise TreeCustodyError("inventory_spool_failed") from exc
        finally:
            db.close()
            os.close(source_fd)


def verify_tree(writer: DescriptorWriter, capture_id: str) -> dict[str, Any]:
    return _verify_internal(writer, capture_id, require_complete=True)


@contextmanager
def _destination_parent(path: Path) -> Iterator[tuple[int, str]]:
    absolute = Path(os.path.abspath(path))
    if absolute.exists() or absolute.is_symlink():
        raise TreeCustodyError("restore_target_must_be_absent")
    parent_fd = _open_absolute_directory(absolute.parent)
    try:
        yield parent_fd, absolute.name
    finally:
        os.close(parent_fd)


def _ensure_dirs(parent_fd: int, path: str) -> int:
    current = os.dup(parent_fd)
    try:
        for part in path.split("/") if path else []:
            _component(part, protected=True)
            try:
                os.mkdir(part, mode=0o700, dir_fd=current)
                os.fsync(current)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        result = current
        current = -1
        return result
    finally:
        if current >= 0:
            os.close(current)


def _apply_fd_metadata(fd: int, entry: dict[str, Any], uid: int, gid: int) -> None:
    os.fchown(fd, uid, gid)
    os.fchmod(fd, entry["mode"])
    os.utime(fd, ns=(entry["mtime_ns"], entry["mtime_ns"]))
    _apply_fd_flags(fd, entry["source_flags"])


def _apply_fd_flags(fd: int, flags: int) -> None:
    if not flags:
        return
    if sys.platform != "darwin" or flags & ~SUPPORTED_SOURCE_FLAGS:
        raise TreeCustodyError("restore_source_flags_unmapped")
    function = getattr(os, "fchflags", None)
    if function is not None:
        function(fd, flags)
        return
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        function = libc.fchflags
        function.argtypes = [ctypes.c_int, ctypes.c_uint]
        function.restype = ctypes.c_int
        ctypes.set_errno(0)
        if function(fd, flags) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
    except OSError:
        raise
    except Exception as exc:
        raise TreeCustodyError("restore_source_flags_unmapped") from exc


def _write_all_fd(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short restore write")
        view = view[written:]


def _target_stat(root_fd: int, root_name: str, relative: str) -> os.stat_result:
    full = f"{root_name}/{relative}" if relative else root_name
    parent, _, name = full.rpartition("/")
    parent_fd = _ensure_dirs(root_fd, parent)
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    finally:
        os.close(parent_fd)


def _verify_restored_entries(
    target_fd: int,
    root_name: str,
    entries: Iterator[dict[str, Any]],
    target_uid: int,
    target_gid: int,
) -> None:
    """Reread destination files and verify bytes, link topology, and metadata."""

    def check_stat(st: os.stat_result, entry: dict[str, Any], *, check_mode: bool) -> None:
        if st.st_uid != target_uid or st.st_gid != target_gid:
            raise TreeCustodyError("restore_owner_mapping_mismatch")
        if check_mode and stat.S_IMODE(st.st_mode) != entry["mode"]:
            raise TreeCustodyError("restore_mode_mismatch")
        if st.st_mtime_ns != entry["mtime_ns"]:
            raise TreeCustodyError("restore_mtime_mismatch")
        if getattr(st, "st_flags", 0) != entry["source_flags"]:
            raise TreeCustodyError("restore_flags_mismatch")

    for entry in entries:
        kind = entry["kind"]
        relative = entry["path"]
        st = _target_stat(target_fd, root_name, relative)
        if kind == "directory":
            if not stat.S_ISDIR(st.st_mode):
                raise TreeCustodyError("restore_directory_type_mismatch")
            check_stat(st, entry, check_mode=True)
        elif kind == "symlink":
            if not stat.S_ISLNK(st.st_mode):
                raise TreeCustodyError("restore_symlink_type_mismatch")
            expected_target = base64.b64decode(entry["target_b64"], validate=True)
            full = f"{root_name}/{relative}" if relative else root_name
            parent, _, name = full.rpartition("/")
            parent_fd = _ensure_dirs(target_fd, parent)
            try:
                target = os.readlink(os.fsencode(name), dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
            target_bytes = target if isinstance(target, bytes) else os.fsencode(target)
            if target_bytes != expected_target:
                raise TreeCustodyError("restore_symlink_target_mismatch")
            check_stat(st, entry, check_mode=False)
        elif kind == "hardlink":
            if not stat.S_ISREG(st.st_mode):
                raise TreeCustodyError("restore_hardlink_type_mismatch")
            primary = _target_stat(target_fd, root_name, entry["link_to"])
            if (st.st_dev, st.st_ino) != (primary.st_dev, primary.st_ino):
                raise TreeCustodyError("restore_hardlink_topology_mismatch")
            check_stat(st, entry, check_mode=True)
        elif kind == "file":
            full = f"{root_name}/{relative}" if relative else root_name
            parent, _, name = full.rpartition("/")
            parent_fd = _ensure_dirs(target_fd, parent)
            fd = -1
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    raise TreeCustodyError("restore_file_type_mismatch")
                check_stat(st, entry, check_mode=True)
                digest = hashlib.sha256()
                total = 0
                while True:
                    data = os.read(fd, CHUNK_BYTES)
                    if not data:
                        break
                    digest.update(data)
                    total += len(data)
                if (
                    total != entry["size_bytes"]
                    or digest.hexdigest() != entry["sha256"]
                    or _sig(os.fstat(fd)) != _sig(st)
                ):
                    raise TreeCustodyError("restore_file_hash_mismatch")
            finally:
                if fd >= 0:
                    os.close(fd)
                os.close(parent_fd)


def extract_tree(
    writer: DescriptorWriter,
    capture_id: str,
    destination: Path,
    *,
    target_uid: int | None = None,
    target_gid: int | None = None,
) -> dict[str, Any]:
    verified = verify_tree(writer, capture_id)
    manifest, manifest_raw, _ = _manifest(
        writer,
        capture_id,
        expected_sha256=verified["manifest_sha256"],
    )
    uid = os.geteuid() if target_uid is None else target_uid
    gid = os.getegid() if target_gid is None else target_gid
    if type(uid) is not int or uid < 0 or type(gid) is not int or gid < 0:
        raise TreeCustodyError("restore_owner_mapping_invalid")
    if manifest["source_flags_or"] and sys.platform != "darwin":
        raise TreeCustodyError("restore_source_flags_unmapped")
    if Path(os.path.abspath(destination)).name in {"", ".", ".."}:
        raise TreeCustodyError("restore_target_invalid")
    with _destination_parent(destination) as (parent_fd, target_name):
        os.mkdir(target_name, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        target_fd = os.open(
            target_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
        )
        try:
            root_dir_fd = _ensure_dirs(target_fd, manifest["root_name"])
            os.close(root_dir_fd)

            # Re-stream metadata after verification, pinning every read to the
            # digest recorded in the already-verified parent object.
            def read_index_rows(iref: dict[str, Any]) -> list[dict[str, Any]]:
                if (
                    not isinstance(iref, dict)
                    or set(iref) != {"path", "sha256", "count"}
                    or type(iref.get("count")) is not int
                    or iref["count"] < 0
                ):
                    raise TreeCustodyError("restore_index_ref_invalid")
                path = _relative(iref["path"], protected=True)
                digest = _strict_hash(iref["sha256"], "restore_index")
                raw = _restore_read_pinned(
                    writer, path, digest, max_bytes=MAX_PAGE_BYTES, label="index_page"
                )
                try:
                    index = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise TreeCustodyError("restore_index_page_invalid") from exc
                if (
                    canonical_json(index) != raw
                    or not isinstance(index, dict)
                    or set(index) != {"schema", "rows"}
                    or index.get("schema") != "brain-host-tree-index-v1"
                    or not isinstance(index.get("rows"), list)
                    or len(index["rows"]) != iref["count"]
                ):
                    raise TreeCustodyError("restore_index_page_invalid")
                return index["rows"]

            def read_entry_page(eref: dict[str, Any]) -> list[dict[str, Any]]:
                if (
                    not isinstance(eref, dict)
                    or set(eref) != {"path", "sha256", "count"}
                    or type(eref.get("count")) is not int
                    or eref["count"] < 0
                ):
                    raise TreeCustodyError("restore_entry_page_ref_invalid")
                path = _relative(eref["path"], protected=True)
                digest = _strict_hash(eref["sha256"], "restore_entry_page")
                raw = _restore_read_pinned(
                    writer, path, digest, max_bytes=MAX_PAGE_BYTES, label="entry_page"
                )
                lines = raw.splitlines()
                if len(lines) != eref["count"]:
                    raise TreeCustodyError("restore_entry_page_count_mismatch")
                entries = []
                try:
                    for line in lines:
                        entry = json.loads(line)
                        if (
                            not isinstance(entry, dict)
                            or canonical_json(entry).rstrip(b"\n") != line
                        ):
                            raise TreeCustodyError("restore_entry_page_invalid")
                        entries.append(entry)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise TreeCustodyError("restore_entry_page_invalid") from exc
                return entries

            def iter_entries(*, reverse: bool = False):
                index_refs = (
                    reversed(manifest["index_pages"]) if reverse else manifest["index_pages"]
                )
                for iref in index_refs:
                    rows = read_index_rows(iref)
                    entry_refs = reversed(rows) if reverse else rows
                    for eref in entry_refs:
                        entries = read_entry_page(eref)
                        yield from reversed(entries) if reverse else entries

            def read_chunk_rows(cref: dict[str, Any], file_id: str) -> list[dict[str, Any]]:
                if (
                    not isinstance(cref, dict)
                    or set(cref) != {"path", "sha256", "count"}
                    or type(cref.get("count")) is not int
                    or cref["count"] < 0
                ):
                    raise TreeCustodyError("restore_chunk_index_ref_invalid")
                path = _relative(cref["path"], protected=True)
                digest = _strict_hash(cref["sha256"], "restore_chunk_index")
                raw = _restore_read_pinned(
                    writer, path, digest, max_bytes=MAX_PAGE_BYTES, label="chunk_index_page"
                )
                try:
                    index = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise TreeCustodyError("restore_chunk_index_page_invalid") from exc
                if (
                    canonical_json(index) != raw
                    or not isinstance(index, dict)
                    or set(index) != {"schema", "file_id", "rows"}
                    or index.get("schema") != "brain-host-tree-chunk-index-v1"
                    or index.get("file_id") != file_id
                    or not isinstance(index.get("rows"), list)
                    or len(index["rows"]) != cref["count"]
                ):
                    raise TreeCustodyError("restore_chunk_index_page_invalid")
                for chunk in index["rows"]:
                    if (
                        not isinstance(chunk, dict)
                        or set(chunk) != {"path", "size_bytes", "sha256"}
                        or type(chunk.get("size_bytes")) is not int
                        or not 0 <= chunk["size_bytes"] <= CHUNK_BYTES
                    ):
                        raise TreeCustodyError("restore_chunk_ref_invalid")
                    _relative(chunk["path"], protected=True)
                    _strict_hash(chunk["sha256"], "restore_chunk")
                return index["rows"]

            # directories are already ordered by path; create all before leaves
            for entry in iter_entries():
                if entry["kind"] == "directory" and entry["path"]:
                    rel = f"{manifest['root_name']}/{entry['path']}"
                    parent, _, name = rel.rpartition("/")
                    pfd = _ensure_dirs(target_fd, parent)
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=pfd)
                        os.fsync(pfd)
                    finally:
                        os.close(pfd)
            for entry in iter_entries():
                kind = entry["kind"]
                rel = (
                    f"{manifest['root_name']}/{entry['path']}"
                    if entry["path"]
                    else manifest["root_name"]
                )
                if kind == "directory":
                    continue
                parent, _, name = rel.rpartition("/")
                pfd = _ensure_dirs(target_fd, parent)
                try:
                    if kind == "symlink":
                        target = base64.b64decode(entry["target_b64"], validate=True)
                        os.symlink(target, os.fsencode(name), dir_fd=pfd)
                        os.chown(name, uid, gid, dir_fd=pfd, follow_symlinks=False)
                        os.utime(
                            name,
                            ns=(entry["mtime_ns"], entry["mtime_ns"]),
                            dir_fd=pfd,
                            follow_symlinks=False,
                        )
                        source_flags = entry["source_flags"]
                        if source_flags:
                            if not hasattr(os, "O_SYMLINK"):
                                raise TreeCustodyError("restore_source_flags_unmapped")
                            link_fd = os.open(name, os.O_RDONLY | os.O_SYMLINK, dir_fd=pfd)
                            try:
                                _apply_fd_flags(link_fd, source_flags)
                            finally:
                                os.close(link_fd)
                    elif kind == "hardlink":
                        src = f"{manifest['root_name']}/{entry['link_to']}"
                        src_parent, _, src_name = src.rpartition("/")
                        sfd = _ensure_dirs(target_fd, src_parent)
                        try:
                            os.link(
                                src_name,
                                name,
                                src_dir_fd=sfd,
                                dst_dir_fd=pfd,
                                follow_symlinks=False,
                            )
                        finally:
                            os.close(sfd)
                    else:
                        fd = os.open(
                            name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=pfd,
                        )
                        try:
                            whole = hashlib.sha256()
                            bytes_written = 0
                            file_id = hashlib.sha256(entry["path"].encode("utf-8")).hexdigest()
                            for cref in entry["chunk_index_pages"]:
                                for chunk in read_chunk_rows(cref, file_id):
                                    data = _restore_read_pinned(
                                        writer,
                                        chunk["path"],
                                        chunk["sha256"],
                                        max_bytes=CHUNK_BYTES,
                                        label="chunk",
                                    )
                                    if len(data) != chunk["size_bytes"]:
                                        raise TreeCustodyError("restore_chunk_size_mismatch")
                                    whole.update(data)
                                    view = memoryview(data)
                                    while view:
                                        n = os.write(fd, view)
                                        if n <= 0:
                                            raise OSError("short restore write")
                                        view = view[n:]
                                    bytes_written += len(data)
                            if (
                                bytes_written != entry["size_bytes"]
                                or whole.hexdigest() != entry["sha256"]
                            ):
                                raise TreeCustodyError("restore_file_hash_mismatch")
                            _apply_fd_metadata(fd, entry, uid, gid)
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                    os.fsync(pfd)
                finally:
                    os.close(pfd)

            # Ownership, modes and times for directories are applied postorder.
            # The manifest path order is ascending; reverse index pages and
            # lines to apply directory metadata postorder without materializing.
            for entry in iter_entries(reverse=True):
                if entry["kind"] != "directory":
                    continue
                rel = (
                    f"{manifest['root_name']}/{entry['path']}"
                    if entry["path"]
                    else manifest["root_name"]
                )
                dfd = _ensure_dirs(target_fd, rel)
                try:
                    _apply_fd_metadata(dfd, entry, uid, gid)
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            _verify_restored_entries(
                target_fd,
                manifest["root_name"],
                iter_entries(),
                uid,
                gid,
            )
            restore_map = {
                "schema": RESTORE_SCHEMA,
                "capture_id": capture_id,
                "manifest_sha256": sha256(manifest_raw),
                "source_uid_gid_policy": "preserved_as_evidence_mapped_to_caller_selected_owner",
                "symlink_mode_policy": "source_mode_recorded_restore_uses_host_default",
                "extended_metadata_policy": EXTENDED_METADATA_POLICY,
                "omitted_metadata_reason": "darwin_host_metadata_policy",
                "xattr_name_counts": verified["xattr_name_counts"],
                "target_uid": uid,
                "target_gid": gid,
                "uid_mapping": sorted({(entry["uid"], uid) for entry in iter_entries()}),
                "gid_mapping": sorted({(entry["gid"], gid) for entry in iter_entries()}),
                "entry_count": verified["entry_count"],
                "complete": True,
            }
            restore_raw = canonical_json(restore_map)
            fd = os.open(
                "RESTORE_COMPLETE.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=target_fd,
            )
            try:
                _write_all_fd(fd, restore_raw)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(target_fd)
            return {
                "schema": RESTORE_SCHEMA,
                "capture_id": capture_id,
                "root_count": 1,
                "entry_count": verified["entry_count"],
                "target_uid": uid,
                "target_gid": gid,
                "restore_complete": True,
            }
        finally:
            os.close(target_fd)


@contextmanager
def _storage_writer(config_path: Path) -> Iterator[DescriptorWriter]:
    from memecoin_research_storage import writer_guard

    with writer_guard(config_path) as guard:
        current = os.dup(guard.mount_fd)
        try:
            for part in guard.config.data_root.split("/"):
                next_fd = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
                )
                os.close(current)
                current = next_fd
            with DescriptorWriter(current, before_write=guard.revalidate) as writer:
                yield writer
        finally:
            os.close(current)


def _load_root(path: Path, scope: str) -> TreeRoot:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TreeCustodyError("root_declaration_invalid") from exc
    if not isinstance(value, dict) or set(value) != {
        "name",
        "authority_id",
        "recovery_point_id",
        "path",
    }:
        raise TreeCustodyError("root_declaration_invalid")
    return TreeRoot(
        value["name"], value["authority_id"], value["recovery_point_id"], Path(value["path"]), scope
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("--storage-config", type=Path, required=True)
    capture.add_argument("--root", type=Path, required=True)
    capture.add_argument("--scope", choices=("non_protected", "protected_opaque"), required=True)
    capture.add_argument("--capture-id")
    verify = sub.add_parser("verify")
    verify.add_argument("--storage-config", type=Path, required=True)
    verify.add_argument("--capture-id", required=True)
    extract = sub.add_parser("extract")
    extract.add_argument("--storage-config", type=Path, required=True)
    extract.add_argument("--capture-id", required=True)
    extract.add_argument("--destination", type=Path, required=True)
    extract.add_argument("--target-uid", type=int)
    extract.add_argument("--target-gid", type=int)
    args = parser.parse_args(argv)
    try:
        with _storage_writer(args.storage_config) as writer:
            if args.command == "capture":
                result = capture_tree(
                    writer, _load_root(args.root, args.scope), capture_id=args.capture_id
                )
                if result.get("scope") == "protected_opaque":
                    result = {
                        k: result[k]
                        for k in (
                            "schema",
                            "capture_id",
                            "scope",
                            "entry_count",
                            "file_count",
                            "symlink_count",
                            "hardlink_count",
                            "source_bytes",
                            "xattr_name_counts",
                            "omitted_metadata_reason",
                            "manifest_sha256",
                            "complete",
                        )
                    }
            elif args.command == "verify":
                result = verify_tree(writer, args.capture_id)
                if result.get("scope") == "protected_opaque":
                    result = {
                        k: result[k]
                        for k in (
                            "schema",
                            "capture_id",
                            "scope",
                            "manifest_sha256",
                            "entry_count",
                            "file_count",
                            "symlink_count",
                            "hardlink_count",
                            "source_bytes",
                            "xattr_name_counts",
                            "omitted_metadata_reason",
                            "verified_bytes",
                            "complete",
                        )
                    }
            else:
                result = extract_tree(
                    writer,
                    args.capture_id,
                    args.destination,
                    target_uid=args.target_uid,
                    target_gid=args.target_gid,
                )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (TreeCustodyError, StorageError, OSError, ValueError, sqlite3.Error) as exc:
        if isinstance(exc, OSError):
            safe_error: dict[str, object] = {"error": "os_error", "errno": exc.errno}
        elif isinstance(exc, TreeCustodyError):
            safe_error = {"error": str(exc)}
        elif isinstance(exc, StorageError):
            safe_error = {"error": "storage_error"}
        elif isinstance(exc, sqlite3.Error):
            safe_error = {"error": "inventory_spool_failed"}
        else:
            safe_error = {"error": "operation_invalid"}
        print(json.dumps({"state": "failed", **safe_error}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
