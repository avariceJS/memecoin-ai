#!/usr/bin/env python3
"""Capture and verify declared static host roots through a held descriptor.

This v1 format is for explicit non-protected static roots only. A recovery point
identifier is an assertion supplied by the caller; this tool does not establish
global or cross-root atomicity. It excludes SQLite families and protected roots.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from memecoin_research_storage import StorageError, writer_guard
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SCHEMA = "brain-host-static-recovery-v1"
MEMBERSHIP_SCHEMA = "brain-host-static-recovery-membership-v1"
COMPLETE_SCHEMA = "brain-host-static-recovery-complete-v1"
RESERVATION_SCHEMA = "brain-host-static-recovery-reservation-v1"
RESTORE_SCHEMA = "brain-host-static-recovery-restore-complete-v1"
CHUNK_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_TOTAL_BYTES = 512 * 1024**3
MAX_METADATA_BYTES = 64 * 1024**2
CAPTURE_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
SQLITE_SUFFIXES = (".sqlite", ".sqlite3", ".db", ".db3", "-wal", "-shm", "-journal")
SENSITIVE_COMPONENTS = {".ssh", "keychains", "credentials", "secrets", "wallets"}
PROTECTED_COMPONENTS = {"f01", "d43", "holdout", "reserved", "protected"}
CAPTURES_DIR = "host-recovery/captures"


class RecoveryCaptureError(ValueError):
    """A declared capture or restore set is unsafe, incomplete, or inconsistent."""


@dataclass(frozen=True)
class StaticRoot:
    name: str
    authority_id: str
    recovery_point_id: str
    path: Path
    scope: str = "non_protected_static"


@dataclass(frozen=True)
class SourceEntry:
    path: str
    kind: str
    mode: int
    uid: int
    gid: int
    size_bytes: int
    mtime_ns: int
    device: int
    inode: int
    ctime_ns: int

    def signature(self) -> tuple[int, ...]:
        return (
            self.device,
            self.inode,
            self.mode,
            self.uid,
            self.gid,
            self.size_bytes,
            self.mtime_ns,
            self.ctime_ns,
        )

    def manifest(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "mode": self.mode,
            "uid": self.uid,
            "gid": self.gid,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "source_device": self.device,
            "source_inode": self.inode,
            "source_ctime_ns": self.ctime_ns,
        }


@dataclass
class OpenRoot:
    declaration: StaticRoot
    fd: int
    device: int
    inode: int
    entries: list[SourceEntry]


def _json_load(raw: bytes, label: str) -> dict[str, Any]:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RecoveryCaptureError(f"{label}_duplicate_key")
            result[key] = value
        return result

    if len(raw) > MAX_METADATA_BYTES:
        raise RecoveryCaptureError(f"{label}_too_large")
    try:
        parsed = json.loads(raw, object_pairs_hook=unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryCaptureError(f"{label}_invalid_json") from exc
    if not isinstance(parsed, dict):
        raise RecoveryCaptureError(f"{label}_invalid_shape")
    return parsed


def _valid_component(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or ":" in value
        or any(ord(char) < 32 for char in value)
    ):
        raise RecoveryCaptureError("unsafe_path_component")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise RecoveryCaptureError("path_component_not_utf8") from exc
    lowered = value.casefold()
    if lowered in SENSITIVE_COMPONENTS or lowered in PROTECTED_COMPONENTS:
        raise RecoveryCaptureError("excluded_path_component")
    if lowered.startswith(".env") or lowered.endswith((".pem", ".key")):
        raise RecoveryCaptureError("excluded_sensitive_member")
    if lowered.endswith(SQLITE_SUFFIXES):
        raise RecoveryCaptureError("sqlite_member_requires_online_backup")
    return value


def _valid_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise RecoveryCaptureError("unsafe_relative_path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RecoveryCaptureError("unsafe_relative_path")
    for component in path.parts:
        _valid_component(component)
    return value


def _validate_declaration(root: StaticRoot) -> None:
    if not NAME_RE.fullmatch(root.name):
        raise RecoveryCaptureError("invalid_root_name")
    if root.scope != "non_protected_static":
        raise RecoveryCaptureError("static_root_scope_required")
    for label in (root.name, root.authority_id, root.recovery_point_id):
        if not isinstance(label, str) or not label or len(label) > 128:
            raise RecoveryCaptureError("invalid_root_identity")
        if any(ord(char) < 32 for char in label):
            raise RecoveryCaptureError("invalid_root_identity")
    if not root.path.is_absolute():
        raise RecoveryCaptureError("absolute_source_root_required")
    if ".." in root.path.parts:
        raise RecoveryCaptureError("source_root_parent_traversal")
    for component in root.path.parts[1:]:
        _valid_component(component)


def _open_absolute_directory(path: Path) -> int:
    """Open every component without following symlinks, starting at the volume root."""
    if not path.is_absolute() or ".." in path.parts:
        raise RecoveryCaptureError("invalid_absolute_directory")
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in {"", ".", ".."}:
                raise RecoveryCaptureError("invalid_absolute_directory")
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = next_fd
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise RecoveryCaptureError("source_root_not_directory")
        result = current
        current = -1
        return result
    except OSError as exc:
        raise RecoveryCaptureError("source_root_open_failed") from exc
    finally:
        if current >= 0:
            os.close(current)


def _open_relative_directory(root_fd: int, relative: str) -> int:
    current = os.dup(root_fd)
    if relative == "":
        return current
    try:
        for component in _valid_relative(relative).split("/"):
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = next_fd
        result = current
        current = -1
        return result
    except OSError as exc:
        raise RecoveryCaptureError("source_path_changed") from exc
    finally:
        if current >= 0:
            os.close(current)


def _signature(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        stat.S_IMODE(st.st_mode),
        st.st_uid,
        st.st_gid,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


def _inventory(root_fd: int, root_device: int) -> list[SourceEntry]:
    result: list[SourceEntry] = []

    def walk(directory_fd: int, prefix: str) -> None:
        try:
            names: list[str] = []
            with os.scandir(directory_fd) as iterator:
                for entry in iterator:
                    names.append(_valid_component(entry.name))
                    if len(result) + len(names) > MAX_ENTRIES:
                        raise RecoveryCaptureError("source_entry_limit")
            names.sort()
            for name in names:
                relative = f"{prefix}/{name}" if prefix else name
                _valid_relative(relative)
                try:
                    metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError as exc:
                    raise RecoveryCaptureError("source_inventory_changed") from exc
                if metadata.st_dev != root_device:
                    raise RecoveryCaptureError("source_mount_boundary")
                if stat.S_ISLNK(metadata.st_mode):
                    raise RecoveryCaptureError("source_symlink_rejected")
                mode = stat.S_IMODE(metadata.st_mode)
                if mode & 0o7000:
                    raise RecoveryCaptureError("special_permission_bits_rejected")
                if stat.S_ISDIR(metadata.st_mode):
                    entry = SourceEntry(
                        relative,
                        "directory",
                        mode,
                        metadata.st_uid,
                        metadata.st_gid,
                        0,
                        metadata.st_mtime_ns,
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_ctime_ns,
                    )
                    result.append(entry)
                    if len(result) > MAX_ENTRIES:
                        raise RecoveryCaptureError("source_entry_limit")
                    child_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                    try:
                        if _signature(os.fstat(child_fd)) != _signature(metadata):
                            raise RecoveryCaptureError("source_inventory_changed")
                        walk(child_fd, relative)
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(metadata.st_mode):
                    if metadata.st_nlink != 1:
                        raise RecoveryCaptureError("source_hardlink_rejected")
                    entry = SourceEntry(
                        relative,
                        "file",
                        mode,
                        metadata.st_uid,
                        metadata.st_gid,
                        metadata.st_size,
                        metadata.st_mtime_ns,
                        metadata.st_dev,
                        metadata.st_ino,
                        metadata.st_ctime_ns,
                    )
                    result.append(entry)
                    if len(result) > MAX_ENTRIES:
                        raise RecoveryCaptureError("source_entry_limit")
                else:
                    raise RecoveryCaptureError("source_special_file_rejected")
        except RecoveryCaptureError:
            raise
        except OSError as exc:
            raise RecoveryCaptureError("source_inventory_failed") from exc

    root_stat = os.fstat(root_fd)
    if root_stat.st_dev != root_device or not stat.S_ISDIR(root_stat.st_mode):
        raise RecoveryCaptureError("source_root_changed")
    # The root directory itself is an explicit member with the empty relative path.
    result.append(
        SourceEntry(
            "",
            "directory",
            stat.S_IMODE(root_stat.st_mode),
            root_stat.st_uid,
            root_stat.st_gid,
            0,
            root_stat.st_mtime_ns,
            root_stat.st_dev,
            root_stat.st_ino,
            root_stat.st_ctime_ns,
        )
    )
    walk(root_fd, "")
    result.sort(key=lambda entry: (entry.path.count("/"), entry.path))
    return result


def _open_root(root: StaticRoot) -> OpenRoot:
    _validate_declaration(root)
    fd = _open_absolute_directory(root.path)
    metadata = os.fstat(fd)
    return OpenRoot(root, fd, metadata.st_dev, metadata.st_ino, [])


def _open_source_file(root_fd: int, relative: str, expected: SourceEntry) -> int:
    parent_path, _, name = relative.rpartition("/")
    parent_fd = _open_relative_directory(root_fd, parent_path)
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        os.close(parent_fd)
        raise RecoveryCaptureError("source_file_open_failed") from exc
    os.close(parent_fd)
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or _signature(metadata) != expected.signature():
        os.close(fd)
        raise RecoveryCaptureError("source_file_changed_before_read")
    return fd


def _read_chunk(fd: int, maximum: int) -> bytes:
    contents = bytearray()
    while len(contents) < maximum:
        part = os.read(fd, maximum - len(contents))
        if not part:
            break
        contents.extend(part)
    return bytes(contents)


def _capture_file(
    writer: DescriptorWriter,
    capture_path: str,
    root: OpenRoot,
    entry: SourceEntry,
    total_bytes: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    fd = _open_source_file(root.fd, entry.path, entry)
    digest = hashlib.sha256()
    chunks: list[dict[str, Any]] = []
    byte_count = 0
    path_digest = hashlib.sha256(entry.path.encode("utf-8")).hexdigest()
    try:
        while True:
            payload = _read_chunk(fd, CHUNK_BYTES)
            if not payload:
                break
            byte_count += len(payload)
            if byte_count > entry.size_bytes or total_bytes + byte_count > MAX_TOTAL_BYTES:
                raise RecoveryCaptureError("source_file_grew_during_read")
            digest.update(payload)
            chunk_sha = sha256(payload)
            relative_chunk = (
                f"{capture_path}/chunks/{root.declaration.name}/{path_digest}/"
                f"{len(chunks):08d}-{chunk_sha}.bin"
            )
            writer.create(relative_chunk, payload)
            chunks.append(
                {
                    "index": len(chunks),
                    "path": relative_chunk,
                    "size_bytes": len(payload),
                    "sha256": chunk_sha,
                }
            )
        if byte_count != entry.size_bytes:
            raise RecoveryCaptureError("source_file_changed_during_read")
        if _signature(os.fstat(fd)) != entry.signature():
            raise RecoveryCaptureError("source_file_changed_during_read")
    finally:
        os.close(fd)
    manifest_entry = entry.manifest()
    manifest_entry.update({"sha256": digest.hexdigest(), "chunks": chunks})
    members = [
        {"path": chunk["path"], "size_bytes": chunk["size_bytes"], "sha256": chunk["sha256"]}
        for chunk in chunks
    ]
    return manifest_entry, members, byte_count


def _root_manifest(root: OpenRoot, file_records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    entries = []
    for entry in root.entries:
        if entry.kind == "file":
            entries.append(file_records[entry.path])
        else:
            entries.append(entry.manifest())
    root_stat = os.fstat(root.fd)
    return {
        "name": root.declaration.name,
        "scope": root.declaration.scope,
        "authority_id": root.declaration.authority_id,
        "recovery_point_id": root.declaration.recovery_point_id,
        "recovery_point_source": "caller_asserted",
        "source_device": root_stat.st_dev,
        "source_inode": root_stat.st_ino,
        "entries": entries,
    }


def _writer_tree(writer: DescriptorWriter, relative: str) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    count = 0

    def visit(directory: str) -> None:
        nonlocal count
        for name in writer.list_names(directory):
            _valid_component(name)
            child = f"{directory}/{name}"
            metadata = writer.stat(child)
            count += 1
            if count > MAX_ENTRIES:
                raise RecoveryCaptureError("archive_entry_limit")
            if stat.S_ISDIR(metadata.st_mode):
                directories.add(child)
                visit(child)
            elif stat.S_ISREG(metadata.st_mode):
                files.add(child)
            else:
                raise RecoveryCaptureError("archive_member_type_rejected")

    visit(relative)
    return files, directories


def _strict_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise RecoveryCaptureError(f"{label}_invalid_hash")
    return value


def _validate_manifest(
    manifest: dict[str, Any], capture_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if (
        set(manifest)
        != {
            "schema",
            "capture_id",
            "chunk_bytes",
            "created_ns",
            "consistency_claim",
            "membership_sha256",
            "member_count",
            "total_source_bytes",
            "metadata_policy",
            "roots",
        }
        or manifest.get("schema") != SCHEMA
        or manifest.get("capture_id") != capture_id
        or manifest.get("chunk_bytes") != CHUNK_BYTES
        or manifest.get("consistency_claim")
        != "caller_asserted_recovery_points_no_global_atomicity"
        or manifest.get("metadata_policy")
        != {"recorded": ["mode", "uid", "gid", "mtime_ns"], "restore": ["mode", "mtime_ns"]}
        or type(manifest.get("created_ns")) is not int
    ):
        raise RecoveryCaptureError("manifest_contract_invalid")
    _strict_hash(manifest.get("membership_sha256"), "manifest_membership")
    roots = manifest.get("roots")
    if not isinstance(roots, list) or not roots or len(roots) > 128:
        raise RecoveryCaptureError("manifest_roots_invalid")
    names: set[str] = set()
    chunk_members: list[dict[str, Any]] = []
    total_bytes = 0
    entry_count = 0
    for root in roots:
        if not isinstance(root, dict) or set(root) != {
            "name",
            "scope",
            "authority_id",
            "recovery_point_id",
            "recovery_point_source",
            "source_device",
            "source_inode",
            "entries",
        }:
            raise RecoveryCaptureError("manifest_root_invalid")
        name = root.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name) or name in names:
            raise RecoveryCaptureError("manifest_root_name_invalid")
        names.add(name)
        if (
            root.get("scope") != "non_protected_static"
            or root.get("recovery_point_source") != "caller_asserted"
            or not isinstance(root.get("authority_id"), str)
            or not root["authority_id"]
            or len(root["authority_id"]) > 128
            or not isinstance(root.get("recovery_point_id"), str)
            or not root["recovery_point_id"]
            or len(root["recovery_point_id"]) > 128
            or any(ord(char) < 32 for char in root["authority_id"])
            or any(ord(char) < 32 for char in root["recovery_point_id"])
            or type(root.get("source_device")) is not int
            or type(root.get("source_inode")) is not int
        ):
            raise RecoveryCaptureError("manifest_root_scope_invalid")
        entries = root.get("entries")
        if not isinstance(entries, list):
            raise RecoveryCaptureError("manifest_entries_invalid")
        paths: set[str] = set()
        previous_path: tuple[int, str] | None = None
        for entry in entries:
            if not isinstance(entry, dict):
                raise RecoveryCaptureError("manifest_entry_invalid")
            kind = entry.get("kind")
            path = entry.get("path")
            if path == "":
                if kind != "directory":
                    raise RecoveryCaptureError("manifest_root_entry_invalid")
            else:
                _valid_relative(path)
            order = (str(path).count("/"), str(path))
            if previous_path is not None and order <= previous_path:
                raise RecoveryCaptureError("manifest_entry_order_invalid")
            previous_path = order
            if path in paths:
                raise RecoveryCaptureError("manifest_entry_duplicate")
            paths.add(path)
            entry_count += 1
            if entry_count > MAX_ENTRIES:
                raise RecoveryCaptureError("manifest_entry_limit")
            for field in (
                "mode",
                "uid",
                "gid",
                "size_bytes",
                "mtime_ns",
                "source_device",
                "source_inode",
                "source_ctime_ns",
            ):
                if type(entry.get(field)) is not int:
                    raise RecoveryCaptureError("manifest_metadata_invalid")
            if any(
                entry[field] < 0
                for field in (
                    "uid",
                    "gid",
                    "size_bytes",
                    "source_device",
                    "source_inode",
                    "source_ctime_ns",
                )
            ):
                raise RecoveryCaptureError("manifest_metadata_invalid")
            if entry["mode"] < 0 or entry["mode"] & 0o7000:
                raise RecoveryCaptureError("manifest_mode_invalid")
            if kind == "directory":
                if (
                    set(entry)
                    != {
                        "path",
                        "kind",
                        "mode",
                        "uid",
                        "gid",
                        "size_bytes",
                        "mtime_ns",
                        "source_device",
                        "source_inode",
                        "source_ctime_ns",
                    }
                    or entry["size_bytes"] != 0
                ):
                    raise RecoveryCaptureError("manifest_directory_invalid")
            elif kind == "file":
                if set(entry) != {
                    "path",
                    "kind",
                    "mode",
                    "uid",
                    "gid",
                    "size_bytes",
                    "mtime_ns",
                    "source_device",
                    "source_inode",
                    "source_ctime_ns",
                    "sha256",
                    "chunks",
                }:
                    raise RecoveryCaptureError("manifest_file_invalid")
                size = entry["size_bytes"]
                if size < 0:
                    raise RecoveryCaptureError("manifest_file_size_invalid")
                digest = _strict_hash(entry.get("sha256"), "manifest_file")
                chunk_refs = entry.get("chunks")
                if not isinstance(chunk_refs, list):
                    raise RecoveryCaptureError("manifest_chunks_invalid")
                expected_count = (size + CHUNK_BYTES - 1) // CHUNK_BYTES
                if len(chunk_refs) != expected_count:
                    raise RecoveryCaptureError("manifest_chunk_count_invalid")
                chunk_total = 0
                path_digest = hashlib.sha256(path.encode("utf-8")).hexdigest()
                for index, chunk in enumerate(chunk_refs):
                    if not isinstance(chunk, dict) or set(chunk) != {
                        "index",
                        "path",
                        "size_bytes",
                        "sha256",
                    }:
                        raise RecoveryCaptureError("manifest_chunk_invalid")
                    if type(chunk.get("index")) is not int or chunk.get("index") != index:
                        raise RecoveryCaptureError("manifest_chunk_order_invalid")
                    member_path = _valid_relative(chunk.get("path"))
                    chunk_hash = _strict_hash(chunk.get("sha256"), "manifest_chunk")
                    expected_path = (
                        f"{CAPTURES_DIR}/{capture_id}/chunks/{name}/{path_digest}/"
                        f"{index:08d}-{chunk_hash}.bin"
                    )
                    if member_path != expected_path:
                        raise RecoveryCaptureError("manifest_chunk_path_invalid")
                    chunk_size = chunk.get("size_bytes")
                    if type(chunk_size) is not int or not 0 < chunk_size <= CHUNK_BYTES:
                        raise RecoveryCaptureError("manifest_chunk_size_invalid")
                    expected_size = min(CHUNK_BYTES, size - index * CHUNK_BYTES)
                    if chunk_size != expected_size:
                        raise RecoveryCaptureError("manifest_chunk_size_invalid")
                    chunk_total += chunk_size
                    chunk_members.append(
                        {"path": member_path, "size_bytes": chunk_size, "sha256": chunk_hash}
                    )
                if chunk_total != size or (size == 0 and digest != hashlib.sha256(b"").hexdigest()):
                    raise RecoveryCaptureError("manifest_file_chunk_total_invalid")
                total_bytes += size
            else:
                raise RecoveryCaptureError("manifest_entry_kind_invalid")
    if (
        type(manifest.get("member_count")) is not int
        or type(manifest.get("total_source_bytes")) is not int
        or total_bytes != manifest.get("total_source_bytes")
        or len(chunk_members) != manifest.get("member_count")
        or total_bytes > MAX_TOTAL_BYTES
    ):
        raise RecoveryCaptureError("manifest_accounting_invalid")
    if chunk_members != sorted(chunk_members, key=lambda row: row["path"]):
        raise RecoveryCaptureError("manifest_chunk_membership_order_invalid")
    return roots, chunk_members


def _expected_directories(paths: Iterable[str]) -> set[str]:
    directories: set[str] = set()
    for path in paths:
        parts = path.split("/")[:-1]
        for end in range(1, len(parts) + 1):
            directories.add("/".join(parts[:end]))
    return directories


def _verify_reserved_capture(
    writer: DescriptorWriter,
    capture_id: str,
    *,
    verify_bytes: bool,
) -> dict[str, Any]:
    if not CAPTURE_ID_RE.fullmatch(capture_id):
        raise RecoveryCaptureError("capture_id_invalid")
    capture_path = f"{CAPTURES_DIR}/{capture_id}"
    try:
        complete_bytes = writer.read_bytes(f"{capture_path}/COMPLETE", max_bytes=16_384)
        reserve_bytes = writer.read_bytes(f"{capture_path}/RESERVATION.json", max_bytes=16_384)
        membership_bytes = writer.read_bytes(
            f"{capture_path}/membership.json", max_bytes=MAX_METADATA_BYTES
        )
        manifest_bytes = writer.read_bytes(
            f"{capture_path}/manifest.json", max_bytes=MAX_METADATA_BYTES
        )
    except (OSError, StorageError) as exc:
        raise RecoveryCaptureError("capture_artifact_missing") from exc
    complete = _json_load(complete_bytes, "complete")
    reservation = _json_load(reserve_bytes, "reservation")
    membership = _json_load(membership_bytes, "membership")
    manifest = _json_load(manifest_bytes, "manifest")
    if canonical_json(complete) != complete_bytes or set(complete) != {
        "schema",
        "capture_id",
        "manifest_sha256",
        "membership_sha256",
        "reservation_sha256",
        "member_count",
        "completed_ns",
    }:
        raise RecoveryCaptureError("complete_contract_invalid")
    if (
        complete.get("schema") != COMPLETE_SCHEMA
        or complete.get("capture_id") != capture_id
        or type(complete.get("member_count")) is not int
        or complete.get("member_count") != manifest.get("member_count")
        or type(complete.get("completed_ns")) is not int
        or complete.get("manifest_sha256") != sha256(manifest_bytes)
        or complete.get("membership_sha256") != sha256(membership_bytes)
        or complete.get("reservation_sha256") != sha256(reserve_bytes)
    ):
        raise RecoveryCaptureError("complete_hash_mismatch")
    if (
        canonical_json(reservation) != reserve_bytes
        or set(reservation) != {"schema", "capture_id", "created_ns", "state"}
        or reservation.get("schema") != RESERVATION_SCHEMA
        or reservation.get("capture_id") != capture_id
    ):
        raise RecoveryCaptureError("reservation_contract_invalid")
    if canonical_json(manifest) != manifest_bytes:
        raise RecoveryCaptureError("manifest_not_canonical")
    roots, chunk_members = _validate_manifest(manifest, capture_id)
    if (
        canonical_json(membership) != membership_bytes
        or set(membership) != {"schema", "capture_id", "members"}
        or membership.get("schema") != MEMBERSHIP_SCHEMA
        or membership.get("capture_id") != capture_id
        or membership.get("members") != chunk_members
        or manifest.get("membership_sha256") != sha256(membership_bytes)
    ):
        raise RecoveryCaptureError("membership_mismatch")
    expected_files = {
        f"{capture_path}/RESERVATION.json",
        f"{capture_path}/membership.json",
        f"{capture_path}/manifest.json",
        f"{capture_path}/COMPLETE",
        *(member["path"] for member in chunk_members),
    }
    expected_dirs = {
        f"{capture_path}/{path}"
        for path in _expected_directories(
            path.removeprefix(f"{capture_path}/") for path in expected_files
        )
    }
    try:
        actual_files, actual_dirs = _writer_tree(writer, capture_path)
    except (OSError, StorageError) as exc:
        raise RecoveryCaptureError("capture_tree_unreadable") from exc
    if actual_files != expected_files or actual_dirs != expected_dirs:
        raise RecoveryCaptureError("capture_membership_drift")
    if verify_bytes:
        for root in roots:
            for entry in root["entries"]:
                if entry["kind"] != "file":
                    continue
                file_digest = hashlib.sha256()
                actual_size = 0
                for chunk in entry["chunks"]:
                    try:
                        data = writer.read_bytes(chunk["path"], max_bytes=CHUNK_BYTES)
                    except (OSError, StorageError) as exc:
                        raise RecoveryCaptureError("capture_chunk_unreadable") from exc
                    if len(data) != chunk["size_bytes"] or sha256(data) != chunk["sha256"]:
                        raise RecoveryCaptureError("capture_chunk_hash_mismatch")
                    actual_size += len(data)
                    file_digest.update(data)
                if actual_size != entry["size_bytes"] or file_digest.hexdigest() != entry["sha256"]:
                    raise RecoveryCaptureError("capture_file_hash_mismatch")
    return {
        "schema": SCHEMA,
        "capture_id": capture_id,
        "manifest_sha256": sha256(manifest_bytes),
        "membership_sha256": sha256(membership_bytes),
        "member_count": len(chunk_members),
        "root_count": len(roots),
        "source_bytes": manifest["total_source_bytes"],
        "verified_bytes": verify_bytes,
        "complete": True,
    }


def verify_capture(writer: DescriptorWriter, capture_id: str) -> dict[str, Any]:
    """Verify manifest, exact membership, and every chunk without extraction."""
    return _verify_reserved_capture(writer, capture_id, verify_bytes=True)


def capture_static_roots(
    writer: DescriptorWriter,
    roots: Sequence[StaticRoot],
    *,
    capture_id: str | None = None,
    now_ns: int | None = None,
) -> dict[str, Any]:
    """Copy only declared, stable, non-protected roots; COMPLETE is published last."""
    if not roots or len(roots) > 128:
        raise RecoveryCaptureError("declared_root_count_invalid")
    chosen_id = capture_id or secrets.token_hex(16)
    if not CAPTURE_ID_RE.fullmatch(chosen_id):
        raise RecoveryCaptureError("capture_id_invalid")
    declarations = sorted(roots, key=lambda item: item.name)
    if len({root.name for root in declarations}) != len(declarations):
        raise RecoveryCaptureError("duplicate_root_name")
    opened: list[OpenRoot] = []
    try:
        for declaration in declarations:
            opened.append(_open_root(declaration))
        identities = {(root.device, root.inode) for root in opened}
        if len(identities) != len(opened):
            raise RecoveryCaptureError("duplicate_source_root")
        total_preflight = 0
        total_entries = 0
        for root in opened:
            root.entries = _inventory(root.fd, root.device)
            total_entries += len(root.entries)
            total_preflight += sum(
                entry.size_bytes for entry in root.entries if entry.kind == "file"
            )
        if total_entries > MAX_ENTRIES or total_preflight > MAX_TOTAL_BYTES:
            raise RecoveryCaptureError("source_inventory_limit")

        writer.mkdir("host-recovery/captures")
        capture_path = f"{CAPTURES_DIR}/{chosen_id}"
        with writer.lock("host-recovery/.static-capture.lock"):
            try:
                writer.list_names(capture_path)
            except FileNotFoundError:
                pass
            else:
                raise RecoveryCaptureError("capture_id_collision")
            writer.mkdir(capture_path)
            created_ns = now_ns if now_ns is not None else time.time_ns()
            reservation = {
                "schema": RESERVATION_SCHEMA,
                "capture_id": chosen_id,
                "created_ns": created_ns,
                "state": "incomplete_until_complete_marker",
            }
            reservation_bytes = canonical_json(reservation)
            writer.create(f"{capture_path}/RESERVATION.json", reservation_bytes)

            membership_rows: list[dict[str, Any]] = []
            total_copied = 0
            root_records: list[dict[str, Any]] = []
            for root in opened:
                file_records: dict[str, dict[str, Any]] = {}
                for entry in root.entries:
                    if entry.kind != "file":
                        continue
                    record, member_rows, copied = _capture_file(
                        writer, capture_path, root, entry, total_copied
                    )
                    file_records[entry.path] = record
                    membership_rows.extend(member_rows)
                    total_copied += copied
                if total_copied > MAX_TOTAL_BYTES:
                    raise RecoveryCaptureError("capture_byte_limit")
                current_entries = _inventory(root.fd, root.device)
                if [item.signature() for item in current_entries] != [
                    item.signature() for item in root.entries
                ]:
                    raise RecoveryCaptureError("source_membership_or_metadata_changed")
                current_path_fd = _open_absolute_directory(root.declaration.path)
                try:
                    if (os.fstat(current_path_fd).st_dev, os.fstat(current_path_fd).st_ino) != (
                        root.device,
                        root.inode,
                    ):
                        raise RecoveryCaptureError("source_root_path_replaced")
                finally:
                    os.close(current_path_fd)
                root_records.append(_root_manifest(root, file_records))

            membership_rows.sort(key=lambda row: row["path"])
            membership = {
                "schema": MEMBERSHIP_SCHEMA,
                "capture_id": chosen_id,
                "members": membership_rows,
            }
            membership_bytes = canonical_json(membership)
            if len(membership_bytes) > MAX_METADATA_BYTES:
                raise RecoveryCaptureError("membership_too_large")
            manifest = {
                "schema": SCHEMA,
                "capture_id": chosen_id,
                "chunk_bytes": CHUNK_BYTES,
                "created_ns": created_ns,
                "consistency_claim": "caller_asserted_recovery_points_no_global_atomicity",
                "membership_sha256": sha256(membership_bytes),
                "member_count": len(membership_rows),
                "total_source_bytes": total_copied,
                "metadata_policy": {
                    "recorded": ["mode", "uid", "gid", "mtime_ns"],
                    "restore": ["mode", "mtime_ns"],
                },
                "roots": root_records,
            }
            manifest_bytes = canonical_json(manifest)
            if len(manifest_bytes) > MAX_METADATA_BYTES:
                raise RecoveryCaptureError("manifest_too_large")
            writer.create(f"{capture_path}/membership.json", membership_bytes)
            writer.create(f"{capture_path}/manifest.json", manifest_bytes)

            # Read every artifact back through the held descriptor before COMPLETE.
            if (
                writer.read_bytes(f"{capture_path}/RESERVATION.json", max_bytes=16_384)
                != reservation_bytes
            ):
                raise RecoveryCaptureError("reservation_readback_mismatch")
            if (
                writer.read_bytes(f"{capture_path}/membership.json", max_bytes=MAX_METADATA_BYTES)
                != membership_bytes
            ):
                raise RecoveryCaptureError("membership_readback_mismatch")
            if (
                writer.read_bytes(f"{capture_path}/manifest.json", max_bytes=MAX_METADATA_BYTES)
                != manifest_bytes
            ):
                raise RecoveryCaptureError("manifest_readback_mismatch")
            for member in membership_rows:
                chunk = writer.read_bytes(member["path"], max_bytes=CHUNK_BYTES)
                if len(chunk) != member["size_bytes"] or sha256(chunk) != member["sha256"]:
                    raise RecoveryCaptureError("chunk_readback_mismatch")

            complete = {
                "schema": COMPLETE_SCHEMA,
                "capture_id": chosen_id,
                "manifest_sha256": sha256(manifest_bytes),
                "membership_sha256": sha256(membership_bytes),
                "reservation_sha256": sha256(reservation_bytes),
                "member_count": len(membership_rows),
                "completed_ns": now_ns if now_ns is not None else time.time_ns(),
            }
            complete_bytes = canonical_json(complete)
            writer.create(f"{capture_path}/COMPLETE", complete_bytes)
            if writer.read_bytes(f"{capture_path}/COMPLETE", max_bytes=16_384) != complete_bytes:
                raise RecoveryCaptureError("complete_readback_mismatch")
        return verify_capture(writer, chosen_id)
    except (StorageError, OSError) as exc:
        raise RecoveryCaptureError("descriptor_capture_failed") from exc
    finally:
        for root in opened:
            os.close(root.fd)


def _open_target_parent(destination: Path) -> tuple[int, str]:
    if (
        not destination.is_absolute()
        or ".." in destination.parts
        or destination.name in {"", ".", ".."}
    ):
        raise RecoveryCaptureError("restore_destination_invalid")
    parent_fd = _open_absolute_directory(destination.parent)
    return parent_fd, destination.name


def _destination_child(parent_fd: int, name: str, *, mode: int = 0o700) -> int:
    _valid_component(name)
    os.mkdir(name, mode=mode, dir_fd=parent_fd)
    os.fsync(parent_fd)
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise RecoveryCaptureError("restore_directory_invalid")
    return fd


def _open_target_relative(root_fd: int, relative: str) -> int:
    if not relative:
        return os.dup(root_fd)
    current = os.dup(root_fd)
    try:
        for part in _valid_relative(relative).split("/"):
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = next_fd
        result = current
        current = -1
        return result
    except OSError as exc:
        raise RecoveryCaptureError("restore_parent_invalid") from exc
    finally:
        if current >= 0:
            os.close(current)


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("short restore write")
        view = view[written:]


def _extract_file(
    writer: DescriptorWriter,
    destination_root_fd: int,
    entry: dict[str, Any],
) -> None:
    relative = _valid_relative(entry["path"])
    parent_path, _, filename = relative.rpartition("/")
    parent_fd = _open_target_relative(destination_root_fd, parent_path)
    fd = -1
    try:
        fd = os.open(
            filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        digest = hashlib.sha256()
        total = 0
        for chunk in entry["chunks"]:
            payload = writer.read_bytes(chunk["path"], max_bytes=CHUNK_BYTES)
            if len(payload) != chunk["size_bytes"] or sha256(payload) != chunk["sha256"]:
                raise RecoveryCaptureError("restore_chunk_changed")
            _write_all(fd, payload)
            digest.update(payload)
            total += len(payload)
        if total != entry["size_bytes"] or digest.hexdigest() != entry["sha256"]:
            raise RecoveryCaptureError("restore_file_hash_mismatch")
        os.fchmod(fd, entry["mode"] & 0o777)
        os.utime(fd, ns=(entry["mtime_ns"], entry["mtime_ns"]))
        os.fsync(fd)
        os.fsync(parent_fd)
    except FileExistsError as exc:
        raise RecoveryCaptureError("restore_target_collision") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(parent_fd)


def extract_capture(
    writer: DescriptorWriter,
    capture_id: str,
    destination: Path,
) -> dict[str, Any]:
    """Restore a verified set into a new destination, then write its marker last."""
    verified = verify_capture(writer, capture_id)
    capture_path = f"{CAPTURES_DIR}/{capture_id}"
    manifest = _json_load(
        writer.read_bytes(f"{capture_path}/manifest.json", max_bytes=MAX_METADATA_BYTES),
        "manifest",
    )
    parent_fd, destination_name = _open_target_parent(destination)
    root_fds: dict[str, int] = {}
    try:
        group_fd = _destination_child(parent_fd, destination_name)
        try:
            for root in manifest["roots"]:
                root_fds[root["name"]] = _destination_child(group_fd, root["name"])
            for root in manifest["roots"]:
                root_fd = root_fds[root["name"]]
                entries = root["entries"]
                directories = [entry for entry in entries if entry["kind"] == "directory"]
                for entry in sorted(
                    (entry for entry in directories if entry["path"]),
                    key=lambda item: (item["path"].count("/"), item["path"]),
                ):
                    relative = _valid_relative(entry["path"])
                    parent_path, _, name = relative.rpartition("/")
                    directory_parent = _open_target_relative(root_fd, parent_path)
                    try:
                        child_fd = _destination_child(directory_parent, name)
                        os.close(child_fd)
                    finally:
                        os.close(directory_parent)
                for entry in entries:
                    if entry["kind"] == "file":
                        _extract_file(writer, root_fd, entry)
                # Restore recorded directory modes and timestamps after all children exist.
                for entry in sorted(
                    directories,
                    key=lambda item: item["path"].count("/"),
                    reverse=True,
                ):
                    directory_fd = _open_target_relative(root_fd, entry["path"])
                    try:
                        os.fchmod(directory_fd, entry["mode"] & 0o777)
                        os.utime(
                            directory_fd,
                            ns=(entry["mtime_ns"], entry["mtime_ns"]),
                        )
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            marker = {
                "schema": RESTORE_SCHEMA,
                "capture_id": capture_id,
                "manifest_sha256": verified["manifest_sha256"],
                "membership_sha256": verified["membership_sha256"],
                "root_names": [root["name"] for root in manifest["roots"]],
                "restored_owner": "current_user",
                "source_uid_gid_recorded_not_applied": True,
            }
            marker_bytes = canonical_json(marker)
            marker_fd = os.open(
                "RESTORE_COMPLETE.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=group_fd,
            )
            try:
                _write_all(marker_fd, marker_bytes)
                os.fsync(marker_fd)
            finally:
                os.close(marker_fd)
            os.fsync(group_fd)
            return {**verified, "restore_path": str(destination), "restore_complete": True}
        finally:
            os.close(group_fd)
    except (OSError, StorageError) as exc:
        raise RecoveryCaptureError("restore_failed") from exc
    finally:
        for fd in root_fds.values():
            os.close(fd)
        os.close(parent_fd)


def _load_roots(path: Path) -> list[StaticRoot]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RecoveryCaptureError("root_declaration_unavailable") from exc
    payload = _json_load(raw, "root_declaration")
    if set(payload) != {"schema", "roots"} or payload.get("schema") != "brain-host-static-roots-v1":
        raise RecoveryCaptureError("root_declaration_contract_invalid")
    rows = payload.get("roots")
    if not isinstance(rows, list):
        raise RecoveryCaptureError("root_declaration_roots_invalid")
    result: list[StaticRoot] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "name",
            "authority_id",
            "recovery_point_id",
            "scope",
            "path",
        }:
            raise RecoveryCaptureError("root_declaration_entry_invalid")
        result.append(
            StaticRoot(
                row["name"],
                row["authority_id"],
                row["recovery_point_id"],
                Path(row["path"]),
                row["scope"],
            )
        )
    return result


def _open_storage_writer(config_path: Path):
    """Yield a DescriptorWriter rooted at the already verified configured storage root."""
    from contextlib import contextmanager

    @contextmanager
    def opened():
        with writer_guard(config_path) as guard:
            current = os.dup(guard.mount_fd)
            try:
                for part in guard.config.data_root.split("/"):
                    next_fd = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=current,
                    )
                    os.close(current)
                    current = next_fd
                with DescriptorWriter(current, before_write=guard.revalidate) as writer:
                    yield writer
            finally:
                os.close(current)

    return opened()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture", help="capture declared static roots")
    capture_parser.add_argument("--storage-config", type=Path, required=True)
    capture_parser.add_argument("--roots", type=Path, required=True)
    capture_parser.add_argument("--capture-id")
    verify_parser = sub.add_parser("verify", help="verify bytes without extracting")
    verify_parser.add_argument("--storage-config", type=Path, required=True)
    verify_parser.add_argument("--capture-id", required=True)
    restore_parser = sub.add_parser("extract", help="extract a verified capture to a new target")
    restore_parser.add_argument("--storage-config", type=Path, required=True)
    restore_parser.add_argument("--capture-id", required=True)
    restore_parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with _open_storage_writer(args.storage_config) as writer:
            if args.command == "capture":
                result = capture_static_roots(
                    writer, _load_roots(args.roots), capture_id=args.capture_id
                )
            elif args.command == "verify":
                result = verify_capture(writer, args.capture_id)
            else:
                result = extract_capture(writer, args.capture_id, args.destination)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (RecoveryCaptureError, StorageError, OSError, ValueError) as exc:
        print(json.dumps({"state": "failed", "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
