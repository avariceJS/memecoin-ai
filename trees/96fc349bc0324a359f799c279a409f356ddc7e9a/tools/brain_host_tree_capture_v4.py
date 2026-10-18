#!/usr/bin/env python3
"""Descriptor-bound, synthetic-only source capture for the v4 custody plan.

This implementation exercises the real no-follow traversal and streaming read
path against an injected synthetic APFS-shaped mount. It intentionally refuses
non-synthetic ``HeldSnapshotT`` handles; real Kingston publication remains a
separate, unqualified capability.
"""

from __future__ import annotations

import base64
import contextlib
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections import defaultdict
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from tools.brain_host_apfs_snapshot_attestor_v1 import HeldSnapshotT
from tools.brain_host_tree_custody_v4 import plan_group_inventory, verify_plan

SCHEMA = "brain-host-tree-capture-v4-synthetic"
MAX_READ_CHUNK = 1024 * 1024


class TreeCaptureError(ValueError):
    """A descriptor capture was incomplete, unstable, or unsupported."""


def _identity(st: os.stat_result) -> dict[str, int]:
    return {
        "device": st.st_dev,
        "inode": st.st_ino,
        "nlink": st.st_nlink,
        "mode": st.st_mode,
        "uid": st.st_uid,
        "gid": st.st_gid,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "ctime_ns": st.st_ctime_ns,
    }


def _name(value: str) -> str:
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise TreeCaptureError("path_name_not_utf8") from exc
    if not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
        raise TreeCaptureError("path_name_invalid")
    return value


def _list_xattrs(fd: int) -> list[bytes]:
    libc = ctypes.CDLL(
        "/usr/lib/libSystem.B.dylib" if sys.platform == "darwin" else None, use_errno=True
    )
    fn = getattr(libc, "flistxattr", None)
    if fn is None:
        raise TreeCaptureError("xattr_inventory_unsupported")
    fn.restype = ctypes.c_ssize_t
    if sys.platform == "darwin":
        fn.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]

        def call(buf: Any, size: int) -> int:
            return int(fn(fd, buf, size, 0))
    else:
        fn.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]

        def call(buf: Any, size: int) -> int:
            return int(fn(fd, buf, size))

    size = call(None, 0)
    if size < 0:
        raise TreeCaptureError("xattr_inventory_failed")
    if not size:
        return []
    buf = ctypes.create_string_buffer(size)
    actual = call(buf, size)
    if actual < 0:
        raise TreeCaptureError("xattr_inventory_changed")
    names = [part for part in buf.raw[:actual].split(b"\0") if part]
    if len(names) != len(set(names)) or any(b"\0" in n for n in names):
        raise TreeCaptureError("xattr_inventory_invalid")
    return sorted(names)


def _get_xattr(fd: int, name: bytes) -> bytes:
    libc = ctypes.CDLL(
        "/usr/lib/libSystem.B.dylib" if sys.platform == "darwin" else None, use_errno=True
    )
    fn = getattr(libc, "fgetxattr", None)
    if fn is None:
        raise TreeCaptureError("xattr_value_unsupported")
    fn.restype = ctypes.c_ssize_t
    if sys.platform == "darwin":
        fn.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.c_int,
        ]

        def call(buf: Any, size: int) -> int:
            return int(fn(fd, name, buf, size, 0, 0))
    else:
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]

        def call(buf: Any, size: int) -> int:
            return int(fn(fd, name, buf, size))

    size = call(None, 0)
    if size < 0:
        raise TreeCaptureError("xattr_value_read_failed")
    buf = ctypes.create_string_buffer(size)
    actual = call(buf, size)
    if actual < 0 or actual != size:
        raise TreeCaptureError("xattr_value_changed")
    return buf.raw[:actual]


def _acl_value(fd: int) -> tuple[str, bytes | None]:
    if sys.platform != "darwin":
        # Linux POSIX ACLs are represented by the system ACL xattr and appear
        # in the exact xattr inventory captured above.
        return "absent", None
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    fn = getattr(libc, "acl_get_fd_np", None)
    if fn is None:
        raise TreeCaptureError("acl_inventory_unsupported")
    fn.argtypes = [ctypes.c_int, ctypes.c_int]
    fn.restype = ctypes.c_void_p
    ctypes.set_errno(0)
    acl = fn(fd, 0x00000100)
    if not acl:
        err = ctypes.get_errno()
        if err == errno.ENOENT:
            return "absent", None
        raise TreeCaptureError("acl_inventory_failed")
    try:
        to_text = libc.acl_to_text
        to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ssize_t)]
        to_text.restype = ctypes.c_void_p
        length = ctypes.c_ssize_t()
        text_ptr = to_text(acl, ctypes.byref(length))
        if not text_ptr or length.value < 0:
            raise TreeCaptureError("acl_value_serialization_failed")
        try:
            return "value_required", ctypes.string_at(text_ptr, length.value)
        finally:
            libc.acl_free.argtypes = [ctypes.c_void_p]
            libc.acl_free(text_ptr)
    finally:
        libc.acl_free.argtypes = [ctypes.c_void_p]
        libc.acl_free(acl)


def _acl_inventory(fd: int) -> str:
    """Detect ACL presence without serializing its value during roster closure."""
    if sys.platform != "darwin":
        return "absent"
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    fn = getattr(libc, "acl_get_fd_np", None)
    if fn is None:
        raise TreeCaptureError("acl_inventory_unsupported")
    fn.argtypes = [ctypes.c_int, ctypes.c_int]
    fn.restype = ctypes.c_void_p
    ctypes.set_errno(0)
    acl = fn(fd, 0x00000100)
    if acl:
        libc.acl_free.argtypes = [ctypes.c_void_p]
        libc.acl_free(acl)
        return "value_required"
    err = ctypes.get_errno()
    if err == errno.ENOENT:
        return "absent"
    raise TreeCaptureError("acl_inventory_failed")


def _flags(st: os.stat_result) -> tuple[str, int | None]:
    value = getattr(st, "st_flags", 0)
    if type(value) is not int or value < 0:
        raise TreeCaptureError("source_flags_invalid")
    if sys.platform != "darwin":
        if value:
            raise TreeCaptureError("source_flags_platform_unsupported")
        return "absent", None
    supported = 0x00008000  # UF_HIDDEN; mirrors v3's bounded source flag contract.
    if value & ~supported:
        raise TreeCaptureError("source_flags_unsupported")
    return ("value_required", value) if value else ("absent", None)


def _link_xattrs(parent_fd: int, name: str) -> tuple[list[str], dict[str, bytes]]:
    """Read symlink xattrs lexically through a held parent, never its target."""
    path = os.fsencode(f"/proc/self/fd/{parent_fd}/{name}")
    libc = ctypes.CDLL(None, use_errno=True)
    list_fn = getattr(libc, "llistxattr", None)
    get_fn = getattr(libc, "lgetxattr", None)
    if list_fn is None or get_fn is None:
        raise TreeCaptureError("symlink_xattr_inventory_unsupported")
    list_fn.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
    get_fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]

    def list_call(buffer: Any, size: int) -> int:
        return int(list_fn(path, buffer, size))

    def get_call(name: bytes, buffer: Any, size: int) -> int:
        return int(get_fn(path, name, buffer, size))

    list_fn.restype = get_fn.restype = ctypes.c_ssize_t
    size = list_call(None, 0)
    if size < 0:
        raise TreeCaptureError("symlink_xattr_inventory_failed")
    buf = ctypes.create_string_buffer(size) if size else None
    actual = list_call(buf, size) if size else 0
    if actual < 0:
        raise TreeCaptureError("symlink_xattr_inventory_changed")
    raw_names = sorted(part for part in (buf.raw[:actual].split(b"\0") if buf else []) if part)
    values: dict[str, bytes] = {}
    for raw in raw_names:
        value_size = get_call(raw, None, 0)
        if value_size < 0:
            raise TreeCaptureError("symlink_xattr_value_failed")
        value_buf = ctypes.create_string_buffer(value_size)
        read = get_call(raw, value_buf, value_size)
        if read != value_size:
            raise TreeCaptureError("symlink_xattr_value_changed")
        values[base64.b64encode(raw).decode("ascii")] = value_buf.raw[:read]
    return [base64.b64encode(n).decode("ascii") for n in raw_names], values


def _open_symlink(parent_fd: int, name: str) -> int:
    flag = getattr(os, "O_SYMLINK", 0) if sys.platform == "darwin" else 0
    if not flag:
        raise TreeCaptureError("symlink_descriptor_unsupported")
    fd = os.open(name, os.O_RDONLY | flag, dir_fd=parent_fd)
    if not stat.S_ISLNK(os.fstat(fd).st_mode):
        os.close(fd)
        raise TreeCaptureError("source_changed_during_inventory")
    return fd


def _member_record(
    root_key: str,
    path: str,
    kind: str,
    st: os.stat_result,
    xattrs: list[str],
    acl_state: str,
    flags_state: str,
    link_text: bytes | None = None,
    xattr_state: str = "observed",
) -> dict[str, Any]:
    return {
        "root_key": root_key,
        "path": path,
        "kind": kind,
        **_identity(st),
        "xattr_names_b64": xattrs,
        "xattr_inventory_state": xattr_state,
        "acl_state": acl_state,
        "flags_state": flags_state,
        "link_text_b64": base64.b64encode(link_text).decode("ascii")
        if link_text is not None
        else None,
    }


def _open_child(parent_fd: int, name: str, *, directory: bool = False) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    if directory:
        flags |= os.O_DIRECTORY
    return os.open(name, flags, dir_fd=parent_fd)


def _walk_root(
    root_fd: int, root_key: str, locator: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root_st = os.fstat(root_fd)
    if not stat.S_ISDIR(root_st.st_mode):
        raise TreeCaptureError("source_root_not_directory")
    root_record = {
        "root_key": root_key,
        "source_locator": locator,
        **_identity(root_st),
        "xattr_names_b64": [],
        "xattr_inventory_state": "unknown",
        "acl_state": "unknown",
        "flags_state": "unknown",
    }
    rows: list[dict[str, Any]] = []

    def descend(dir_fd: int, prefix: str) -> None:
        for raw_name in sorted(os.listdir(dir_fd)):
            name = _name(raw_name)
            rel = f"{prefix}/{name}" if prefix else name
            st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                child = _open_child(dir_fd, name, directory=True)
                try:
                    opened = os.fstat(child)
                    if _identity(opened) != _identity(st):
                        raise TreeCaptureError("source_changed_during_inventory")
                    rows.append(
                        _member_record(
                            root_key,
                            rel,
                            "directory",
                            opened,
                            [],
                            "unknown",
                            "unknown",
                            xattr_state="unknown",
                        )
                    )
                    descend(child, rel)
                finally:
                    os.close(child)
            elif stat.S_ISREG(st.st_mode):
                rows.append(
                    _member_record(
                        root_key,
                        rel,
                        "file",
                        st,
                        [],
                        "unknown",
                        "unknown",
                        xattr_state="unknown",
                    )
                )
            elif stat.S_ISLNK(st.st_mode):
                # .env.local link text is excluded by policy and never retained.
                if name == ".env.local":
                    link_text = None
                else:
                    target = os.readlink(name, dir_fd=dir_fd)
                    target_b = os.fsencode(target)
                    link_text = target_b
                rows.append(
                    _member_record(
                        root_key,
                        rel,
                        "symlink",
                        st,
                        [],
                        "unknown",
                        "unknown",
                        link_text,
                        xattr_state="unknown",
                    )
                )
            else:
                raise TreeCaptureError("special_file_rejected")

    descend(root_fd, "")
    return root_record, rows


def _metadata_values(
    root_fd: int, row: dict[str, Any], *, root: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read included metadata through a held object and bind it to planned identity."""
    identity_keys = (
        "device",
        "inode",
        "nlink",
        "mode",
        "uid",
        "gid",
        "size",
        "mtime_ns",
        "ctime_ns",
    )
    expected = {key: row[key] for key in identity_keys}

    def ensure_identity(st: os.stat_result) -> dict[str, int]:
        actual = _identity(st)
        if actual != expected:
            raise TreeCaptureError("source_identity_changed_before_metadata")
        return actual

    if root:
        fd = os.dup(root_fd)
        try:
            before = ensure_identity(os.fstat(fd))
            names = _list_xattrs(fd)
            acl_state, acl_value = _acl_value(fd)
            flags_state, flags_value = _flags(os.fstat(fd))
            values = {base64.b64encode(n).decode("ascii"): _get_xattr(fd, n) for n in names}
            if _identity(os.fstat(fd)) != before:
                raise TreeCaptureError("source_identity_changed_during_metadata")
        finally:
            os.close(fd)
    else:
        parts = row["path"].split("/")
        parent = os.dup(root_fd)
        try:
            for component in parts[:-1]:
                child = _open_child(parent, component, directory=True)
                os.close(parent)
                parent = child
            name = parts[-1]
            if row["kind"] == "symlink" and sys.platform != "darwin":
                before = ensure_identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
                names_b64, values = _link_xattrs(parent, name)
                names = [base64.b64decode(item, validate=True) for item in names_b64]
                acl_state, acl_value = "absent", None
                flags_state, flags_value = _flags(
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                )
                after = _identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
            else:
                fd = (
                    _open_symlink(parent, name)
                    if row["kind"] == "symlink"
                    else _open_child(parent, name, directory=row["kind"] == "directory")
                )
                try:
                    before = ensure_identity(os.fstat(fd))
                    names = _list_xattrs(fd)
                    acl_state, acl_value = _acl_value(fd)
                    flags_state, flags_value = _flags(os.fstat(fd))
                    values = {base64.b64encode(n).decode("ascii"): _get_xattr(fd, n) for n in names}
                    after = _identity(os.fstat(fd))
                finally:
                    os.close(fd)
            if after != before:
                raise TreeCaptureError("source_identity_changed_during_metadata")
        finally:
            os.close(parent)
    fields = {
        "xattr_names_b64": [base64.b64encode(name).decode("ascii") for name in names],
        "xattr_inventory_state": "observed",
        "acl_state": acl_state,
        "flags_state": flags_state,
    }
    value_record = {
        "xattrs": values,
        "acl_bytes": acl_value,
        "acl_encoding": "darwin-acl-text-v1" if acl_value is not None else "platform-absent",
        "flags_value": flags_value,
    }
    return fields, value_record


def _write_stream(source_fd: int, destination: Path, expected: dict[str, int]) -> tuple[int, str]:
    before = _identity(os.fstat(source_fd))
    if before != expected:
        raise TreeCaptureError("source_changed_before_read")
    digest = hashlib.sha256()
    total = 0
    with destination.open("xb") as out:
        while True:
            chunk = os.read(source_fd, MAX_READ_CHUNK)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            total += len(chunk)
        out.flush()
        os.fsync(out.fileno())
    if _identity(os.fstat(source_fd)) != before or total != before["size"]:
        raise TreeCaptureError("source_changed_during_read")
    return total, digest.hexdigest()


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    import shutil

    shutil.rmtree(path)


def _metadata_json(values: dict[str, Any]) -> dict[str, Any]:
    xattrs = []
    for name_b64, value in sorted(values["xattrs"].items()):
        xattrs.append(
            {
                "name_b64": name_b64,
                "value_b64": base64.b64encode(value).decode("ascii"),
                "value_sha256": hashlib.sha256(value).hexdigest(),
            }
        )
    acl = values["acl_bytes"]
    return {
        "xattrs": xattrs,
        "acl": {"state": "absent", "value_b64": None, "value_sha256": None}
        if acl is None
        else {
            "state": "value_required",
            "value_b64": base64.b64encode(acl).decode("ascii"),
            "value_sha256": hashlib.sha256(acl).hexdigest(),
        },
        "acl_encoding": values["acl_encoding"],
        "flags_value": values["flags_value"],
    }


def _validate_metadata_obligation(
    row: dict[str, Any], fields: dict[str, Any], values: dict[str, Any]
) -> None:
    if fields["xattr_inventory_state"] != "observed":
        raise TreeCaptureError("xattr_inventory_unresolved")
    if fields["xattr_names_b64"] != row["xattr_names_b64"]:
        raise TreeCaptureError("xattr_roster_mismatch")
    if set(values["xattrs"]) != set(row["xattr_names_b64"]):
        raise TreeCaptureError("xattr_value_roster_mismatch")
    acl_state = row["acl_state"]
    if fields["acl_state"] != acl_state:
        raise TreeCaptureError("acl_state_mismatch")
    if acl_state == "value_required":
        if not isinstance(values["acl_bytes"], bytes):
            raise TreeCaptureError("acl_value_missing")
    elif acl_state == "absent":
        if values["acl_bytes"] is not None:
            raise TreeCaptureError("unexpected_acl_value")
    else:
        raise TreeCaptureError("acl_state_unresolved")
    flags_state = row["flags_state"]
    if fields["flags_state"] != flags_state:
        raise TreeCaptureError("flags_state_mismatch")
    if flags_state == "value_required":
        if type(values["flags_value"]) is not int:
            raise TreeCaptureError("flags_value_missing")
    elif flags_state == "absent":
        if values["flags_value"] is not None:
            raise TreeCaptureError("unexpected_flags_value")
    else:
        raise TreeCaptureError("flags_state_unresolved")


def _roster_projection(plan: dict[str, Any]) -> dict[str, Any]:
    identity_keys = (
        "device",
        "inode",
        "nlink",
        "mode",
        "uid",
        "gid",
        "size",
        "mtime_ns",
        "ctime_ns",
    )
    roots = [
        {key: row[key] for key in ("root_key", "source_locator", *identity_keys)}
        for row in plan["root_roster"]
    ]
    entries = []
    for row in plan["entries"]:
        projected = {
            key: row[key]
            for key in ("root_key", "path", "kind", *identity_keys, "disposition", "link_text_b64")
        }
        if row["kind"] == "file":
            projected["hardlink_group_id"] = row.get("hardlink_group_id")
        entries.append(projected)
    return {"roots": roots, "entries": entries}


def _verify_object_readback(directory: Path, rows: list[dict[str, Any]]) -> None:
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for row in rows:
            fd = os.open(row["path"], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
            try:
                before = os.fstat(fd)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_nlink != 1
                    or before.st_size != row["size_bytes"]
                ):
                    raise TreeCaptureError("artifact_readback_identity_mismatch")
                digest = hashlib.sha256()
                size = 0
                while chunk := os.read(fd, MAX_READ_CHUNK):
                    size += len(chunk)
                    digest.update(chunk)
                after = os.fstat(fd)
                if (
                    _identity(before) != _identity(after)
                    or size != row["size_bytes"]
                    or digest.hexdigest() != row["sha256"]
                ):
                    raise TreeCaptureError("artifact_readback_mismatch")
            finally:
                os.close(fd)
    finally:
        os.close(dir_fd)


def _write_private_file(directory: Path, name: str, payload: bytes) -> None:
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        try:
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise TreeCaptureError("artifact_write_short")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)


def _verify_final_artifact_set(
    directory: Path,
    object_rows: list[dict[str, Any]],
    manifest_bytes: bytes,
    receipt_bytes: bytes,
) -> None:
    dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        expected_names = sorted(
            [row["path"] for row in object_rows] + ["capture.json", "receipt.json"]
        )
        if sorted(os.listdir(dir_fd)) != expected_names:
            raise TreeCaptureError("artifact_member_roster_mismatch")
        expected_hashes = {row["path"]: (row["size_bytes"], row["sha256"]) for row in object_rows}
        expected_hashes["capture.json"] = (
            len(manifest_bytes),
            hashlib.sha256(manifest_bytes).hexdigest(),
        )
        expected_hashes["receipt.json"] = (
            len(receipt_bytes),
            hashlib.sha256(receipt_bytes).hexdigest(),
        )
        for name, (expected_size, expected_digest) in expected_hashes.items():
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise TreeCaptureError("artifact_readback_identity_mismatch")
                digest = hashlib.sha256()
                size = 0
                while chunk := os.read(fd, MAX_READ_CHUNK):
                    digest.update(chunk)
                    size += len(chunk)
                if _identity(before) != _identity(os.fstat(fd)):
                    raise TreeCaptureError("artifact_readback_changed")
                if size != expected_size or digest.hexdigest() != expected_digest:
                    raise TreeCaptureError("artifact_readback_mismatch")
            finally:
                os.close(fd)
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def capture_synthetic_descriptor_tree(
    held: HeldSnapshotT,
    output_parent: Path,
    *,
    nonce: str | None = None,
    interruption_hook: Any = None,
) -> dict[str, Any]:
    """Capture exactly the three held roots into nonce-scoped disposable files.

    Only synthetic held handles are accepted. All source enumeration, metadata
    reads, and payload reads are descriptor-bound. The returned record is
    explicitly synthetic and can never assert a real COMPLETE/K/capture.
    """
    if not isinstance(held, HeldSnapshotT) or not held.synthetic:
        raise TreeCaptureError("synthetic_handle_required")
    if held._closed:
        raise TreeCaptureError("snapshot_handle_released")
    nonce = nonce or secrets.token_hex(16)
    if not re.fullmatch(r"[a-f0-9]{32}", nonce):
        raise TreeCaptureError("nonce_invalid")
    roles = sorted(held.plan.root_locators)
    if len(roles) != 3 or any(
        not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", role) for role in roles
    ):
        raise TreeCaptureError("root_role_invalid")
    root_keys = {role: f"root-{hashlib.sha256(role.encode()).hexdigest()[:7]}" for role in roles}
    records: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    metadata: dict[str, dict[str, Any]] = {}
    with ExitStack() as stack:
        output_holder: dict[str, Path | None] = {"path": None}

        @contextlib.contextmanager
        def cleanup_output_on_error() -> Iterator[None]:
            try:
                yield
            except BaseException:
                output_path = output_holder["path"]
                if output_path is not None:
                    _remove_tree(output_path)
                raise

        stack.enter_context(cleanup_output_on_error())
        borrowed = {role: stack.enter_context(held.borrow_root(role)) for role in roles}
        for role in roles:
            root, rows = _walk_root(borrowed[role], root_keys[role], held.plan.root_locators[role])
            records.append(root)
            entries.extend(rows)
        roster_plan = verify_plan(
            plan_group_inventory(
                source_view_ref=f"synthetic:{nonce}",
                volume_uuid=held.plan.snapshot_uuid,
                roots=records,
                entries=entries,
            )
        )
        record_by_key = {row["root_key"]: row for row in records}
        entry_by_key = {(row["root_key"], row["path"]): row for row in entries}
        for role in roles:
            rk = root_keys[role]
            root_row = next(item for item in roster_plan["root_roster"] if item["root_key"] == rk)
            fields, values = _metadata_values(borrowed[role], root_row, root=True)
            record_by_key[rk].update(fields)
            metadata[f"root:{rk}"] = values
        for row in roster_plan["entries"]:
            if row["disposition"] != "included_opaque":
                continue
            role = next(role for role in roles if root_keys[role] == row["root_key"])
            fields, values = _metadata_values(borrowed[role], row)
            entry_by_key[(row["root_key"], row["path"])].update(fields)
            metadata[f"entry:{row['root_key']}:{row['path']}"] = values
        plan = verify_plan(
            plan_group_inventory(
                source_view_ref=f"synthetic:{nonce}",
                volume_uuid=held.plan.snapshot_uuid,
                roots=records,
                entries=entries,
            )
        )
        if plan["unsupported_metadata_member_count"]:
            raise TreeCaptureError("metadata_inventory_unresolved")
        for row in plan["root_roster"]:
            _validate_metadata_obligation(row, row, metadata[f"root:{row['root_key']}"])
        for row in plan["entries"]:
            if row["disposition"] == "included_opaque":
                _validate_metadata_obligation(
                    row, row, metadata[f"entry:{row['root_key']}:{row['path']}"]
                )
        # Hardlink reconciliation has now excluded .env.local groups. Only then
        # may the payload pass open included regular-file aliases.
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in plan["entries"]:
            if row["kind"] == "file" and row["disposition"] == "included_opaque":
                groups[row["hardlink_group_id"]].append(row)
        out = output_parent / f"brain-capture-{nonce}"
        out.mkdir(mode=0o700)
        output_holder["path"] = out
        try:
            object_rows = []
            for index, (group, aliases) in enumerate(sorted(groups.items())):
                primary = aliases[0]
                role = next(role for role in roles if root_keys[role] == primary["root_key"])
                parts = primary["path"].split("/")
                current = os.dup(borrowed[role])
                try:
                    for component in parts[:-1]:
                        nxt = _open_child(current, component, directory=True)
                        os.close(current)
                        current = nxt
                    source_fd = _open_child(current, parts[-1])
                    try:
                        expected = {
                            key: primary[key]
                            for key in (
                                "device",
                                "inode",
                                "nlink",
                                "mode",
                                "uid",
                                "gid",
                                "size",
                                "mtime_ns",
                                "ctime_ns",
                            )
                        }
                        payload_path = out / f"object-{group}.bin"
                        size, digest = _write_stream(source_fd, payload_path, expected)
                    finally:
                        os.close(source_fd)
                finally:
                    os.close(current)
                object_rows.append(
                    {
                        "hardlink_group_id": group,
                        "path": payload_path.name,
                        "size_bytes": size,
                        "sha256": digest,
                        "alias_count": len(aliases),
                    }
                )
                if interruption_hook is not None:
                    interruption_hook(index, out)
            # Re-enumerate while descriptors remain held and compare complete
            # canonical plan and metadata values to detect source mutation.
            rerecords, reentries, remetadata = [], [], {}
            for role in roles:
                root, rows = _walk_root(
                    borrowed[role], root_keys[role], held.plan.root_locators[role]
                )
                rerecords.append(root)
                reentries.extend(rows)
            replan = verify_plan(
                plan_group_inventory(
                    source_view_ref=f"synthetic:{nonce}",
                    volume_uuid=held.plan.snapshot_uuid,
                    roots=rerecords,
                    entries=reentries,
                )
            )
            if _roster_projection(replan) != _roster_projection(plan):
                raise TreeCaptureError("source_roster_changed_after_capture")
            for role in roles:
                rk = root_keys[role]
                root_row = next(item for item in plan["root_roster"] if item["root_key"] == rk)
                fields, values = _metadata_values(borrowed[role], root_row, root=True)
                _validate_metadata_obligation(root_row, fields, values)
                remetadata[f"root:{rk}"] = values
            for row in plan["entries"]:
                if row["disposition"] != "included_opaque":
                    continue
                role = next(role for role in roles if root_keys[role] == row["root_key"])
                fields, values = _metadata_values(borrowed[role], row)
                _validate_metadata_obligation(row, fields, values)
                remetadata[f"entry:{row['root_key']}:{row['path']}"] = values
            if remetadata != metadata:
                raise TreeCaptureError("source_changed_after_capture")
            _verify_object_readback(out, object_rows)
            manifest = {
                "schema": SCHEMA,
                "synthetic": True,
                "nonce": nonce,
                "source_view_authority": "synthetic_fixture_only",
                "plan": plan,
                "metadata_values": {
                    key: _metadata_json(value) for key, value in sorted(metadata.items())
                },
                "objects": object_rows,
                "complete": False,
                "kingston_durable": False,
                "restore_verified": False,
                "erase_ready": False,
            }
            manifest_bytes = json.dumps(
                manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
            _write_private_file(out, "capture.json", manifest_bytes)
            receipt = {
                "schema": "brain-host-tree-capture-v4-synthetic-receipt",
                "synthetic": True,
                "captured": True,
                "complete_marker_published": False,
                "real_T": False,
                "real_capture": False,
                "kingston_custody": False,
                "restore_verified": False,
                "erase_ready": False,
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            }
            receipt_bytes = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode(
                "ascii"
            )
            _write_private_file(out, "receipt.json", receipt_bytes)
            _verify_final_artifact_set(out, object_rows, manifest_bytes, receipt_bytes)
            return receipt
        except BaseException:
            _remove_tree(out)
            raise
