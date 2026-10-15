#!/usr/bin/env python3
"""Capture a private, metadata-only census of explicitly declared host roots.

The private manifest is published create-only beneath the bound BrainData root.
Stdout contains aggregates, the private manifest locator, and publication digests;
source paths appear only in the private manifest. Reads are limited to bounded
`.git`/`commondir` locator controls and the configured storage binding and
BrainData root marker needed to validate the destination. No owner application,
credential, symlink-target, xattr, or protected payload is read. This live census
is not a source view, common cutoff, or custody capture.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import plistlib
import secrets
import stat
import subprocess
import sys
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from memecoin_research_storage import (
    StorageConfig,
    StorageError,
    _verify_marker_in_root_fd,
    resolve,
    writer_guard,
)

SCHEMA = "brain-host-root-boundary-census-v1"
PRIVATE_DIR = "host-recovery/private-root-boundary-census-v1"
EXPECTED_DATA_VOLUME_UUID = "430740ca-42e7-4e6c-a07a-e4e7370152cc"
EXPECTED_DATA_MOUNT_POINT = "/System/Volumes/Data"
EXPECTED_KINGSTON_VOLUME_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"
EXPECTED_KINGSTON_ROOT_ID = "9428695af6d4f46c08e1a444cdc66de8"
NOFOLLOW_DIRECTORY = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
NOFOLLOW_FILE = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
MAX_CENSUS_ENTRIES = 1_000_000
MAX_CENSUS_DIRECTORIES = 250_000
MAX_CENSUS_ERRORS = 10_000


class CensusError(RuntimeError):
    """The declared-root census or its private publication failed closed."""


class _UnlockedStorageBinding:
    """Pinned Kingston descriptors with full identity revalidation but no writer flock."""

    def __init__(
        self,
        config_path: Path,
        config: StorageConfig,
        mount_fd: int,
        volume_uuid: str,
        root_id: str,
    ) -> None:
        self.config_path = config_path
        self.config = config
        self.mount_fd = mount_fd
        self.volume_uuid = volume_uuid
        self.root_id = root_id

    def revalidate(self) -> None:
        if StorageConfig.load(self.config_path) != self.config:
            raise CensusError("kingston_config_changed")
        current = resolve(self.config_path)
        if current.volume_uuid != self.volume_uuid or current.root_id != self.root_id:
            raise CensusError("kingston_binding_changed")
        held = os.fstat(self.mount_fd)
        path_fd = _root_open(current.mount_path)
        try:
            path_st = os.fstat(path_fd)
            if (held.st_dev, held.st_ino) != (path_st.st_dev, path_st.st_ino):
                raise CensusError("kingston_mount_changed")
            root_fd = _data_root_fd(self)
            os.close(root_fd)
        finally:
            os.close(path_fd)

    def revalidate_identity(self) -> None:
        if StorageConfig.load(self.config_path) != self.config:
            raise CensusError("kingston_config_changed")
        held = os.fstat(self.mount_fd)
        if not os.path.ismount(self.config.mount_path):
            raise CensusError("kingston_mountpoint_not_mounted")
        path_fd = _root_open(self.config.mount_path)
        try:
            current = os.fstat(path_fd)
            if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
                raise CensusError("kingston_mount_changed")
            root_fd = _data_root_fd(self)
            os.close(root_fd)
            vfs = os.fstatvfs(path_fd)
            if vfs.f_flag & getattr(os, "ST_RDONLY", 1):
                raise CensusError("kingston_became_read_only")
        finally:
            os.close(path_fd)


@dataclass(frozen=True)
class RootSpec:
    key: str
    path: Path
    excluded_subtrees: tuple[tuple[str, str], ...] = ()
    expected_kind: str = "directory"


@dataclass(frozen=True)
class CensusRoster:
    """Caller-declared roots and pins for a relocatable metadata census."""

    version: int
    source_volume_uuid: str
    source_mount_point: Path
    required_roles: tuple[str, ...]
    role_dispositions: tuple[dict[str, str], ...]
    roots: tuple[RootSpec, ...]
    git_bindings: tuple[dict[str, Any], ...]
    opaque_expectations: dict[str, dict[str, int]]
    storage_volume_uuid: str
    storage_root_id: str
    roster_sha256: str
    roster_bytes_b64: str


ABSENT_DISPOSITIONS = {
    "reissue",
    "rebuild",
    "proven_disposable",
    "not_present_on_source",
    "unresolved",
}


def _stat_signature(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_nlink,
        st.st_uid,
        st.st_gid,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


def _root_open(path: Path) -> int:
    if not path.is_absolute() or "\x00" in str(path):
        raise CensusError("root_path_invalid")
    fd = os.open("/", NOFOLLOW_DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(component, NOFOLLOW_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise CensusError("root_not_directory")
        result = fd
        fd = -1
        return result
    except OSError as exc:
        raise CensusError("root_open_failed") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def _ancestor_manifest(path: Path) -> list[dict[str, Any]]:
    """Record no-follow identity metadata for every absolute path component."""
    absolute = Path(os.path.abspath(path))
    fd = os.open("/", NOFOLLOW_DIRECTORY)
    root_stat = os.fstat(fd)
    rows = [
        {
            "component": "/",
            "device": root_stat.st_dev,
            "inode": root_stat.st_ino,
            "mode": stat.S_IMODE(root_stat.st_mode),
            "uid": root_stat.st_uid,
            "gid": root_stat.st_gid,
        }
    ]
    try:
        for component in absolute.parts[1:]:
            child = os.open(component, NOFOLLOW_DIRECTORY, dir_fd=fd)
            st = os.fstat(child)
            rows.append(
                {
                    "component": component,
                    "device": st.st_dev,
                    "inode": st.st_ino,
                    "mode": stat.S_IMODE(st.st_mode),
                    "uid": st.st_uid,
                    "gid": st.st_gid,
                }
            )
            os.close(fd)
            fd = child
        return rows
    except OSError as exc:
        raise CensusError("root_ancestor_open_failed") from exc
    finally:
        os.close(fd)


def _entry_record(path: str, st: os.stat_result, kind: str, disposition: str) -> dict[str, Any]:
    return {
        "path": path,
        "kind": kind,
        "disposition": disposition,
        "device": st.st_dev,
        "inode": st.st_ino,
        "mode": stat.S_IMODE(st.st_mode),
        "nlink": st.st_nlink,
        "uid": st.st_uid,
        "gid": st.st_gid,
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "ctime_ns": st.st_ctime_ns,
    }


def _kind(st: os.stat_result) -> str:
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    if stat.S_ISREG(st.st_mode):
        return "regular_file"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    return "special"


def _volume_identity(
    path: Path,
    *,
    held_fd: int,
    is_directory: bool,
    expected_volume_uuid: str = EXPECTED_DATA_VOLUME_UUID,
    expected_mount_point: Path = Path(EXPECTED_DATA_MOUNT_POINT),
) -> dict[str, Any]:
    """Bind diskutil volume metadata to a held filesystem object by device id."""
    del is_directory
    held = os.fstat(held_fd)
    parent_fd = _root_open(path.parent)
    try:
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
    finally:
        os.close(parent_fd)
    if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
        raise CensusError("volume_probe_path_not_held_object")
    try:
        result = subprocess.run(
            ["/usr/sbin/diskutil", "info", "-plist", str(expected_mount_point)],
            capture_output=True,
            check=True,
            timeout=15,
        )
        value = plistlib.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException) as exc:
        raise CensusError("source_volume_identity_unavailable") from exc
    if not isinstance(value, dict):
        raise CensusError("source_volume_identity_invalid")
    volume_uuid = value.get("VolumeUUID")
    mount_point = value.get("MountPoint")
    filesystem_name = value.get("FilesystemName")
    filesystem_type = value.get("FilesystemType")
    device_identifier = value.get("DeviceIdentifier")
    if not isinstance(volume_uuid, str) or not isinstance(mount_point, str):
        raise CensusError("source_volume_identity_incomplete")
    if os.path.normpath(mount_point) != os.path.normpath(str(expected_mount_point)):
        raise CensusError("source_mountpoint_mismatch")
    try:
        volume_uuid = str(uuid.UUID(volume_uuid))
    except ValueError as exc:
        raise CensusError("source_volume_uuid_invalid") from exc
    if filesystem_name is not None and not isinstance(filesystem_name, str):
        raise CensusError("source_filesystem_name_invalid")
    if filesystem_type is not None and not isinstance(filesystem_type, str):
        raise CensusError("source_filesystem_type_invalid")
    if not isinstance(device_identifier, str) or not device_identifier or "/" in device_identifier:
        raise CensusError("source_device_identifier_invalid")
    try:
        device_stat = os.stat(f"/dev/{device_identifier}")
    except OSError as exc:
        raise CensusError("source_device_node_unavailable") from exc
    if not (stat.S_ISCHR(device_stat.st_mode) or stat.S_ISBLK(device_stat.st_mode)) or (
        device_stat.st_rdev != held.st_dev
    ):
        raise CensusError("source_device_node_mismatch")
    if volume_uuid != expected_volume_uuid:
        raise CensusError("source_volume_uuid_mismatch")
    parent_fd = _root_open(path.parent)
    try:
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
    finally:
        os.close(parent_fd)
    if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
        raise CensusError("volume_probe_path_changed")
    return {
        "volume_uuid": volume_uuid,
        "device_identifier": device_identifier,
        "held_device": held.st_dev,
        "held_inode": held.st_ino,
        "mount_point": os.path.normpath(mount_point),
        "filesystem_name": filesystem_name or "unknown",
        "filesystem_type": filesystem_type or "unknown",
    }


def _validate_exclusions(root: RootSpec) -> dict[str, str]:
    result: dict[str, str] = {}
    for path, reason in root.excluded_subtrees:
        candidate = Path(path)
        if (
            not isinstance(path, str)
            or not path
            or candidate.is_absolute()
            or "\\" in path
            or "\x00" in path
            or (
                path != "@basename:wallets"
                and any(part in {"", ".", ".."} for part in path.split("/"))
            )
            or not isinstance(reason, str)
            or reason not in {"opaque_wallets", "opaque_codex_local"}
            or path in result
        ):
            raise CensusError("exclusion_invalid")
        result[path] = reason
    return result


def census_root(
    root: RootSpec,
    *,
    record_sink: Callable[[dict[str, Any]], None] | None = None,
    expected_volume_uuid: str = EXPECTED_DATA_VOLUME_UUID,
    expected_mount_point: Path = Path(EXPECTED_DATA_MOUNT_POINT),
) -> dict[str, Any]:
    """Return one private root record and aggregate counts using held dirfds."""
    exclusions = _validate_exclusions(root)
    parent_fd = _root_open(root.path.parent)
    try:
        try:
            declared_stat = os.stat(root.path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            if root.expected_kind == "file":
                return census_file_root(
                    root,
                    record_sink=record_sink,
                    expected_volume_uuid=expected_volume_uuid,
                    expected_mount_point=expected_mount_point,
                )
            raise CensusError("declared_directory_missing") from exc
        if not stat.S_ISDIR(declared_stat.st_mode):
            if root.expected_kind == "file":
                return census_file_root(
                    root,
                    record_sink=record_sink,
                    expected_volume_uuid=expected_volume_uuid,
                    expected_mount_point=expected_mount_point,
                )
            raise CensusError("declared_directory_kind_mismatch")
        if root.expected_kind != "directory":
            raise CensusError("declared_file_kind_mismatch")
    finally:
        os.close(parent_fd)
    root_fd = _root_open(root.path)
    root_before = os.fstat(root_fd)
    if not stat.S_ISDIR(root_before.st_mode):
        os.close(root_fd)
        raise CensusError("root_identity_invalid")
    ancestors_before = _ancestor_manifest(root.path)
    source_volume_before = _volume_identity(
        root.path,
        held_fd=root_fd,
        is_directory=True,
        expected_volume_uuid=expected_volume_uuid,
        expected_mount_point=expected_mount_point,
    )
    if (
        _stat_signature(declared_stat) != _stat_signature(root_before)
        or not ancestors_before
        or (ancestors_before[-1]["device"], ancestors_before[-1]["inode"])
        != (root_before.st_dev, root_before.st_ino)
    ):
        os.close(root_fd)
        raise CensusError("root_changed_during_open")
    counts: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    disposition_counts: Counter[str] = Counter()
    errors: list[dict[str, str]] = []
    directory_signatures: list[tuple[str, tuple[int, ...]]] = [("", _stat_signature(root_before))]
    stack: list[str] = [""]

    def add_record(record: dict[str, Any]) -> None:
        disposition_counts[record["disposition"]] += 1
        if record_sink is None:
            records.append(record)
        else:
            record_sink(record)

    def add_error(path: str, error: str) -> None:
        if len(errors) >= MAX_CENSUS_ERRORS:
            raise CensusError("census_error_limit_exceeded")
        errors.append({"path": path, "error": error})

    try:
        while stack:
            prefix = stack.pop()
            directory_fd = _open_relative_directory(root_fd, prefix)
            try:
                try:
                    with os.scandir(directory_fd) as iterator:
                        names = sorted(entry.name for entry in iterator)
                except OSError:
                    counts["unreadable"] += 1
                    add_error(prefix, "directory_enumeration_failed")
                    continue
                for name in names:
                    if name in {".", ".."} or "/" in name or "\x00" in name:
                        counts["unreadable"] += 1
                        add_error(prefix, "directory_entry_name_invalid")
                        continue
                    relative = f"{prefix}/{name}" if prefix else name
                    try:
                        st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    except OSError:
                        counts["unreadable"] += 1
                        add_error(relative, "lstat_failed")
                        continue
                    kind = _kind(st)
                    counts["entries"] += 1
                    if counts["entries"] > MAX_CENSUS_ENTRIES:
                        raise CensusError("census_entry_limit_exceeded")
                    counts[kind] += 1
                    if name == ".env.local":
                        if kind in {"regular_file", "symlink"}:
                            add_record(_entry_record(relative, st, kind, "excluded_reauth_reissue"))
                            counts["credential_name_excluded"] += 1
                        else:
                            add_record(
                                _entry_record(
                                    relative,
                                    st,
                                    kind,
                                    "excluded_name_unexpected_kind_unresolved",
                                )
                            )
                            counts["unresolved"] += 1
                            add_error(relative, "credential_exclusion_kind_invalid")
                        continue
                    exclusion = exclusions.get(relative)
                    if (
                        exclusion is None
                        and "@basename:wallets" in exclusions
                        and name == "wallets"
                    ):
                        exclusion = exclusions["@basename:wallets"]
                    if exclusion is not None:
                        counts["unresolved_backup_required"] += 1
                        add_record(
                            _entry_record(
                                relative,
                                st,
                                kind,
                                f"skipped_{exclusion}_unresolved_backup_required",
                            )
                        )
                        counts["opaque_subtrees_skipped"] += 1
                        if kind != "directory":
                            counts["unresolved"] += 1
                            add_error(relative, "opaque_subtree_not_directory")
                        continue
                    if st.st_dev != root_before.st_dev:
                        add_record(_entry_record(relative, st, kind, "cross_device_not_descended"))
                        counts["cross_device"] += 1
                        counts["unresolved"] += 1
                        continue
                    if kind == "symlink":
                        add_record(_entry_record(relative, st, kind, "link_target_not_read"))
                        counts["symlink_targets_unresolved"] += 1
                        continue
                    if kind == "special":
                        add_record(_entry_record(relative, st, kind, "special_unresolved"))
                        counts["unresolved"] += 1
                        continue
                    if kind == "regular_file":
                        add_record(_entry_record(relative, st, kind, "metadata_only"))
                        continue
                    try:
                        child_fd = os.open(name, NOFOLLOW_DIRECTORY, dir_fd=directory_fd)
                        child_st = os.fstat(child_fd)
                    except OSError:
                        counts["unreadable"] += 1
                        add_error(relative, "directory_open_failed")
                        continue
                    if _stat_signature(st) != _stat_signature(child_st):
                        os.close(child_fd)
                        counts["unresolved"] += 1
                        add_error(relative, "directory_changed_during_open")
                        continue
                    add_record(_entry_record(relative, st, kind, "metadata_only"))
                    directory_signatures.append((relative, _stat_signature(child_st)))
                    if len(directory_signatures) > MAX_CENSUS_DIRECTORIES:
                        os.close(child_fd)
                        raise CensusError("census_directory_limit_exceeded")
                    os.close(child_fd)
                    stack.append(relative)
            finally:
                os.close(directory_fd)

        directory_drift: list[str] = []
        for relative, expected in directory_signatures:
            try:
                current_fd = (
                    root_fd if not relative else _open_relative_directory(root_fd, relative)
                )
                try:
                    if _stat_signature(os.fstat(current_fd)) != expected:
                        directory_drift.append(relative)
                finally:
                    if current_fd != root_fd:
                        os.close(current_fd)
            except OSError:
                directory_drift.append(relative)
        root_after = os.fstat(root_fd)
        ancestors_after = _ancestor_manifest(root.path)
        source_volume_after = _volume_identity(
            root.path,
            held_fd=root_fd,
            is_directory=True,
            expected_volume_uuid=expected_volume_uuid,
            expected_mount_point=expected_mount_point,
        )
        parent_fd = _root_open(root.path.parent)
        try:
            path_after = os.stat(root.path.name, dir_fd=parent_fd, follow_symlinks=False)
        finally:
            os.close(parent_fd)
        path_bound_at_end = (path_after.st_dev, path_after.st_ino) == (
            root_before.st_dev,
            root_before.st_ino,
        )
        ancestors_stable = ancestors_before == ancestors_after
        root_stable = _stat_signature(root_before) == _stat_signature(root_after)
        if (
            not root_stable
            or not ancestors_stable
            or not path_bound_at_end
            or source_volume_before != source_volume_after
        ):
            counts["unresolved"] += 1
        if directory_drift:
            counts["directory_metadata_drift"] += len(directory_drift)
            counts["unresolved"] += len(directory_drift)
        if record_sink is None:
            records.sort(key=lambda item: item["path"])
        errors.sort(key=lambda item: (item["path"], item["error"]))
        return {
            "root_key": root.key,
            "absolute_locator": str(root.path),
            "root_device": root_before.st_dev,
            "root_inode": root_before.st_ino,
            "root_mode": stat.S_IMODE(root_before.st_mode),
            "root_uid": root_before.st_uid,
            "root_gid": root_before.st_gid,
            "source_volume_start": source_volume_before,
            "source_volume_end": source_volume_after,
            "ancestor_identities_start": ancestors_before,
            "ancestor_identities_end": ancestors_after,
            "ancestors_stable_at_endpoints": ancestors_stable,
            "root_start_signature": list(_stat_signature(root_before)),
            "root_end_signature": list(_stat_signature(root_after)),
            "root_stable_at_endpoints": (
                root_stable
                and ancestors_stable
                and path_bound_at_end
                and source_volume_before == source_volume_after
            ),
            "counts": dict(sorted(counts.items())),
            "directory_drift_paths": directory_drift,
            "errors": errors,
            "entries": records,
            "entry_disposition_counts": dict(sorted(disposition_counts.items())),
            "complete_traversal": (
                counts["unreadable"] == 0
                and counts["unresolved"] == 0
                and not errors
                and not directory_drift
            ),
            "all_descendants_examined": (
                counts["opaque_subtrees_skipped"] == 0
                and counts["symlink_targets_unresolved"] == 0
                and counts["cross_device"] == 0
                and counts["unresolved"] == 0
                and counts["unreadable"] == 0
            ),
            "completeness_scope": "included_declared_scope_only",
            "limitations": [
                "live sequential traversal, not a common source point",
                "regular-file metadata is observed once without opening payloads",
                "symlink target text and resolution are not checked",
                "same-device firmlink aliases are not distinguishable from ordinary directories",
                "hardlink aliases outside the declared roster are not discoverable from this scan",
                "xattr names/values, ACLs, flags, and file bytes are not read",
            ],
        }
    finally:
        os.close(root_fd)


def census_file_root(
    root: RootSpec,
    *,
    record_sink: Callable[[dict[str, Any]], None] | None = None,
    expected_volume_uuid: str = EXPECTED_DATA_VOLUME_UUID,
    expected_mount_point: Path = Path(EXPECTED_DATA_MOUNT_POINT),
) -> dict[str, Any]:
    """Observe a single declared host-tool file and its no-follow ancestors."""
    if root.excluded_subtrees:
        raise CensusError("file_root_cannot_have_subtree_exclusions")
    parent = root.path.parent
    parent_fd = _root_open(parent)
    parent_before = os.fstat(parent_fd)
    ancestors_before = _ancestor_manifest(parent)
    source_volume_before = _volume_identity(
        parent,
        held_fd=parent_fd,
        is_directory=True,
        expected_volume_uuid=expected_volume_uuid,
        expected_mount_point=expected_mount_point,
    )
    if not ancestors_before or (ancestors_before[-1]["device"], ancestors_before[-1]["inode"]) != (
        parent_before.st_dev,
        parent_before.st_ino,
    ):
        os.close(parent_fd)
        raise CensusError("file_root_parent_changed_during_open")
    errors: list[dict[str, str]] = []
    records: list[dict[str, Any]] = []
    disposition_counts: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    try:
        try:
            st = os.stat(root.path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            counts["missing_declared_file"] += 1
            errors.append({"path": root.path.name, "error": "declared_file_missing"})
        except OSError:
            counts["unreadable"] += 1
            errors.append({"path": root.path.name, "error": "lstat_failed"})
        else:
            kind = _kind(st)
            counts["entries"] += 1
            counts[kind] += 1
            if kind not in {"regular_file", "symlink"}:
                counts["unresolved"] += 1
                errors.append({"path": root.path.name, "error": "declared_file_kind_invalid"})
            disposition = "link_target_not_read" if kind == "symlink" else "metadata_only"
            record = _entry_record(root.path.name, st, kind, disposition)
            disposition_counts[disposition] += 1
            if record_sink is None:
                records.append(record)
            else:
                record_sink(record)
            if kind == "symlink":
                counts["symlink_targets_unresolved"] += 1
        parent_after = os.fstat(parent_fd)
        ancestors_after = _ancestor_manifest(parent)
        source_volume_after = _volume_identity(
            parent,
            held_fd=parent_fd,
            is_directory=True,
            expected_volume_uuid=expected_volume_uuid,
            expected_mount_point=expected_mount_point,
        )
        try:
            file_after = os.stat(root.path.name, dir_fd=parent_fd, follow_symlinks=False)
            file_stable = counts["entries"] == 1 and _stat_signature(file_after) == _stat_signature(
                st
            )
        except OSError:
            file_stable = False
        stable = (
            _stat_signature(parent_before) == _stat_signature(parent_after)
            and ancestors_before == ancestors_after
            and bool(ancestors_after)
            and (ancestors_after[-1]["device"], ancestors_after[-1]["inode"])
            == (parent_after.st_dev, parent_after.st_ino)
            and file_stable
            and source_volume_before == source_volume_after
        )
        return {
            "root_key": root.key,
            "absolute_locator": str(root.path),
            "root_kind": "single_declared_file",
            "parent_device": parent_before.st_dev,
            "parent_inode": parent_before.st_ino,
            "source_volume_start": source_volume_before,
            "source_volume_end": source_volume_after,
            "ancestor_identities_start": ancestors_before,
            "ancestor_identities_end": ancestors_after,
            "parent_stable_at_endpoints": stable,
            "root_stable_at_endpoints": stable,
            "counts": dict(sorted(counts.items())),
            "directory_drift_paths": [] if stable else ["."],
            "errors": errors,
            "entries": records,
            "entry_disposition_counts": dict(sorted(disposition_counts.items())),
            "complete_traversal": stable and counts["unreadable"] == 0 and not errors,
            "all_descendants_examined": True,
            "completeness_scope": "single_declared_entry_only",
            "limitations": [
                "single file observed by lstat only; payload and symlink target are not read",
                "host parent directories are not recursively inventoried",
            ],
        }
    finally:
        os.close(parent_fd)


def _open_relative_directory(root_fd: int, relative: str) -> int:
    if not relative:
        return os.dup(root_fd)
    current = os.dup(root_fd)
    try:
        for component in relative.split("/"):
            child = os.open(component, NOFOLLOW_DIRECTORY, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _data_root_fd(guard: Any) -> int:
    current = os.dup(guard.mount_fd)
    try:
        parts = guard.config.data_root.split("/")
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise CensusError("storage_root_relative_path_invalid")
        for part in parts:
            child = os.open(part, NOFOLLOW_DIRECTORY, dir_fd=current)
            os.close(current)
            current = child
        st = os.fstat(current)
        if (
            st.st_dev != os.fstat(guard.mount_fd).st_dev
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o700
        ):
            raise CensusError("storage_root_device_mismatch")
        _verify_marker_in_root_fd(current, guard.config)
        result = current
        current = -1
        return result
    except OSError as exc:
        raise CensusError("storage_root_open_failed") from exc
    finally:
        if current >= 0:
            os.close(current)


def _open_private_parent(guard: Any, relative: str) -> tuple[int, int, str]:
    parts = relative.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise CensusError("private_output_path_invalid")
    root_fd = _data_root_fd(guard)
    current = root_fd
    try:
        device = os.fstat(root_fd).st_dev
        for component in parts[:-1]:
            child = _mkdir_private(current, component, device)
            if current != root_fd:
                os.close(current)
            current = child
        if current == root_fd:
            parent_fd = os.dup(root_fd)
        else:
            parent_fd = current
            current = -1
        return root_fd, parent_fd, parts[-1]
    except BaseException:
        if current >= 0 and current != root_fd:
            os.close(current)
        os.close(root_fd)
        raise


class _JsonlStream:
    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.digest = hashlib.sha256()
        self.byte_count = 0

    def write(self, record: dict[str, Any]) -> None:
        raw = _canonical(record)
        view = memoryview(raw)
        while view:
            written = os.write(self.fd, view)
            if written <= 0:
                raise CensusError("private_stream_write_incomplete")
            view = view[written:]
        self.digest.update(raw)
        self.byte_count += len(raw)


def _commit_private_stream(
    guard: Any,
    *,
    root_fd: int,
    parent_fd: int,
    pending_name: str,
    final_name: str,
    file_fd: int,
    writer: _JsonlStream,
) -> tuple[str, int]:
    file_open = True
    try:
        guard.revalidate()
        os.fsync(file_fd)
        before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size != writer.byte_count
        ):
            raise CensusError("private_stream_identity")
        os.close(file_fd)
        file_open = False
        os.fsync(parent_fd)
        pending_fd = os.open(pending_name, NOFOLLOW_FILE, dir_fd=parent_fd)
        try:
            digest = hashlib.sha256()
            size = 0
            while block := os.read(pending_fd, 1024 * 1024):
                digest.update(block)
                size += len(block)
            after = os.fstat(pending_fd)
            if (
                size != writer.byte_count
                or size != before.st_size
                or _stat_signature(before) != _stat_signature(after)
                or digest.hexdigest() != writer.digest.hexdigest()
            ):
                raise CensusError("private_stream_readback_mismatch")
        finally:
            os.close(pending_fd)
        try:
            os.link(
                pending_name,
                final_name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise CensusError("private_stream_final_already_exists") from exc
        os.unlink(pending_name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        final_fd = os.open(final_name, NOFOLLOW_FILE, dir_fd=parent_fd)
        try:
            final_before = os.fstat(final_fd)
            digest = hashlib.sha256()
            size = 0
            while block := os.read(final_fd, 1024 * 1024):
                digest.update(block)
                size += len(block)
            final_after = os.fstat(final_fd)
            if (
                size != writer.byte_count
                or _stat_signature(final_before) != _stat_signature(final_after)
                or digest.hexdigest() != writer.digest.hexdigest()
            ):
                raise CensusError("private_stream_final_readback_mismatch")
        finally:
            os.close(final_fd)
        guard.revalidate()
        return writer.digest.hexdigest(), writer.byte_count
    finally:
        if file_open:
            os.close(file_fd)


def _unlink_if_owned_inode(parent_fd: int, name: str, *, device: int, inode: int) -> bool:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    if not stat.S_ISREG(current.st_mode) or current.st_dev != device or current.st_ino != inode:
        return False
    os.unlink(name, dir_fd=parent_fd)
    return True


def _mkdir_private(parent_fd: int, name: str, device: int) -> int:
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileExistsError:
        pass
    try:
        fd = os.open(name, NOFOLLOW_DIRECTORY, dir_fd=parent_fd)
    except OSError as exc:
        raise CensusError("private_output_directory_open_failed") from exc
    st = os.fstat(fd)
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_dev != device
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o700
    ):
        os.close(fd)
        raise CensusError("private_output_directory_identity")
    return fd


def _publish_create_only(guard: Any, relative: str, payload: bytes) -> str:
    parts = relative.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise CensusError("private_output_path_invalid")
    guard.revalidate()
    root_fd = _data_root_fd(guard)
    device = os.fstat(root_fd).st_dev
    current = root_fd
    try:
        for component in parts[:-1]:
            child = _mkdir_private(current, component, device)
            if current != root_fd:
                os.close(current)
            current = child
        guard.revalidate()
        try:
            fd = os.open(
                parts[-1],
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=current,
            )
        except FileExistsError as exc:
            raise CensusError("private_output_already_exists") from exc
        try:
            st = os.fstat(fd)
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_dev != device
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o600
                or st.st_nlink != 1
            ):
                raise CensusError("private_output_file_identity")
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise CensusError("private_output_write_incomplete")
                view = view[written:]
            os.fsync(fd)
            if os.fstat(fd).st_size != len(payload):
                raise CensusError("private_output_size_mismatch")
        finally:
            os.close(fd)
        os.fsync(current)
        read_fd = os.open(parts[-1], NOFOLLOW_FILE, dir_fd=current)
        try:
            read_before = os.fstat(read_fd)
            if (
                not stat.S_ISREG(read_before.st_mode)
                or read_before.st_dev != device
                or read_before.st_uid != os.getuid()
                or stat.S_IMODE(read_before.st_mode) != 0o600
                or read_before.st_nlink != 1
                or read_before.st_size != len(payload)
            ):
                raise CensusError("private_output_readback_identity")
            digest = hashlib.sha256()
            size = 0
            while block := os.read(read_fd, 1024 * 1024):
                digest.update(block)
                size += len(block)
            read_after = os.fstat(read_fd)
            if (
                size != len(payload)
                or _stat_signature(read_before) != _stat_signature(read_after)
                or digest.hexdigest() != hashlib.sha256(payload).hexdigest()
            ):
                raise CensusError("private_output_readback_mismatch")
        finally:
            os.close(read_fd)
        guard.revalidate()
    finally:
        if current != root_fd:
            os.close(current)
        os.close(root_fd)
    return hashlib.sha256(payload).hexdigest()


def _hash_private_file(guard: Any, relative: str) -> tuple[str, int]:
    parts = relative.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise CensusError("private_read_path_invalid")
    guard.revalidate()
    current = _data_root_fd(guard)
    try:
        for component in parts[:-1]:
            child = os.open(component, NOFOLLOW_DIRECTORY, dir_fd=current)
            st = os.fstat(child)
            if (
                st.st_dev != os.fstat(current).st_dev
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o700
            ):
                os.close(child)
                raise CensusError("private_read_directory_identity")
            os.close(current)
            current = child
        fd = os.open(parts[-1], NOFOLLOW_FILE, dir_fd=current)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1
            ):
                raise CensusError("private_read_file_identity")
            digest = hashlib.sha256()
            size = 0
            while block := os.read(fd, 1024 * 1024):
                digest.update(block)
                size += len(block)
            after = os.fstat(fd)
            if _stat_signature(before) != _stat_signature(after):
                raise CensusError("private_read_file_changed")
            return digest.hexdigest(), size
        finally:
            os.close(fd)
    except OSError as exc:
        raise CensusError("private_read_failed") from exc
    finally:
        os.close(current)


def _verify_published_pair(
    guard: Any,
    manifest_relative: str,
    marker_relative: str,
    expected_digest: str,
    expected_bytes: int,
    expected_marker: bytes,
) -> str:
    manifest_digest, manifest_bytes = _hash_private_file(guard, manifest_relative)
    marker_digest, marker_bytes = _hash_private_file(guard, marker_relative)
    if marker_bytes > 64 * 1024:
        raise CensusError("completion_marker_too_large")
    marker_raw = _read_small_private_bytes(guard, marker_relative, marker_bytes)
    try:
        marker = json.loads(marker_raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CensusError("completion_marker_invalid") from exc
    if (
        marker_raw != expected_marker
        or marker_digest != hashlib.sha256(expected_marker).hexdigest()
        or manifest_digest != expected_digest
        or marker.get("manifest_relative_path") != manifest_relative
        or marker.get("manifest_sha256") != manifest_digest
        or marker.get("manifest_bytes") != manifest_bytes
        or manifest_bytes != expected_bytes
        or marker.get("status") != "private_manifest_published_and_reread_verified"
    ):
        raise CensusError("published_pair_verification_failed")
    guard.revalidate()
    return marker_digest


def _read_small_private_bytes(guard: Any, relative: str, limit: int) -> bytes:
    parts = relative.split("/")
    guard.revalidate()
    current = _data_root_fd(guard)
    try:
        for component in parts[:-1]:
            child = os.open(component, NOFOLLOW_DIRECTORY, dir_fd=current)
            os.close(current)
            current = child
        fd = os.open(parts[-1], NOFOLLOW_FILE, dir_fd=current)
        try:
            data = bytearray()
            while block := os.read(fd, min(4096, limit + 1 - len(data))):
                data.extend(block)
                if len(data) > limit:
                    raise CensusError("private_small_file_too_large")
            if len(data) != limit:
                raise CensusError("private_small_file_size_mismatch")
            return bytes(data)
        finally:
            os.close(fd)
    except OSError as exc:
        raise CensusError("private_small_file_read_failed") from exc
    finally:
        os.close(current)


def _aggregate(
    roots: Sequence[dict[str, Any]], expected_volume_uuid: str = EXPECTED_DATA_VOLUME_UUID
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "root_count": len(roots),
        "source_volume_uuids": sorted(
            {
                root["source_volume_start"]["volume_uuid"]
                for root in roots
                if isinstance(root.get("source_volume_start"), dict)
                and isinstance(root["source_volume_start"].get("volume_uuid"), str)
            }
        ),
        "all_roots_on_pinned_data_volume": all(
            root.get("source_volume_start", {}).get("volume_uuid") == expected_volume_uuid
            and root.get("source_volume_end", {}).get("volume_uuid") == expected_volume_uuid
            for root in roots
        ),
        "roots": [
            {
                "root_key": root["root_key"],
                "complete_traversal": root["complete_traversal"],
                "root_stable_at_endpoints": root["root_stable_at_endpoints"],
                "all_descendants_examined": root.get("all_descendants_examined", False),
                "opaque_exclusion_counts_match_expected": root.get(
                    "opaque_exclusion_counts_match_expected", True
                ),
                "counts": root["counts"],
                "limitations": root["limitations"],
            }
            for root in roots
        ],
        "application_payload_bytes_read": False,
        "git_admin_control_bytes_read": True,
        "storage_binding_and_root_marker_read": True,
        "symlink_target_text_read": False,
        "xattr_values_read": False,
        "credential_values_read": False,
        "protected_payloads_read": False,
        "provider_requests": 0,
        "services_changed": False,
        "source_consistency_proven": False,
        "root_closure_proven": False,
        "safe_to_erase": False,
    }


def _scan_complete(roots: Sequence[dict[str, Any]], *, all_on_pinned_volume: bool) -> bool:
    return (
        bool(roots)
        and all_on_pinned_volume
        and all(
            root["complete_traversal"]
            and root["root_stable_at_endpoints"]
            and root.get("opaque_exclusion_counts_match_expected", False)
            for root in roots
        )
    )


def _opaque_counts_match(
    root_key: str,
    observed: dict[str, int],
    expectations: dict[str, dict[str, int]] | None = None,
) -> bool:
    legacy_expected = {
        "research_checkout": {"skipped_opaque_wallets_unresolved_backup_required": 3},
        "owner_codex_worktree": {"skipped_opaque_codex_local_unresolved_backup_required": 1},
    }
    expected = (
        expectations.get(root_key, {})
        if expectations is not None
        else legacy_expected.get(root_key, {})
    )
    return all(observed.get(key, 0) == count for key, count in expected.items()) and all(
        key in expected for key in observed if key.startswith("skipped_")
    )


def load_roster(path: Path) -> CensusRoster:
    """Load a private, explicit v2 roster; this never discovers host paths."""
    try:
        roster_fd = os.open(path, NOFOLLOW_FILE)
        try:
            roster_stat = os.fstat(roster_fd)
            if (
                not stat.S_ISREG(roster_stat.st_mode)
                or roster_stat.st_uid != os.getuid()
                or stat.S_IMODE(roster_stat.st_mode) != 0o600
            ):
                raise CensusError("roster_file_permissions_invalid")
            chunks = bytearray()
            while block := os.read(roster_fd, 64 * 1024):
                chunks.extend(block)
                if len(chunks) > 4 * 1024 * 1024:
                    raise CensusError("roster_file_too_large")
        finally:
            os.close(roster_fd)

        def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    raise CensusError("roster_duplicate_json_key")
                result[key] = item
            return result

        value = json.loads(chunks.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CensusError("roster_unreadable_or_invalid_json") from exc
    if not isinstance(value, dict) or value.get("schema") != "brain-host-root-roster-v2":
        raise CensusError("roster_schema_invalid")
    if value.get("version") != 2:
        raise CensusError("roster_version_invalid")
    try:
        source_uuid = str(uuid.UUID(value["source_volume_uuid"]))
        storage_uuid = str(uuid.UUID(value["storage_volume_uuid"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise CensusError("roster_volume_pin_invalid") from exc
    source_mount = value.get("source_mount_point")
    if not isinstance(source_mount, str) or not Path(source_mount).is_absolute():
        raise CensusError("roster_source_mountpoint_invalid")
    storage_root_id = value.get("storage_root_id")
    if not isinstance(storage_root_id, str) or not storage_root_id:
        raise CensusError("roster_storage_root_pin_invalid")
    required = value.get("required_roles")
    roles = value.get("roles")
    if (
        not isinstance(required, list)
        or not required
        or any(not isinstance(role, str) or not role for role in required)
        or len(set(required)) != len(required)
        or not isinstance(roles, list)
    ):
        raise CensusError("roster_required_roles_invalid")
    roots: list[RootSpec] = []
    dispositions: list[dict[str, str]] = []
    git_bindings: list[dict[str, Any]] = []
    expectations: dict[str, dict[str, int]] = {}
    seen_roles: set[str] = set()
    declared_roles: set[str] = set()
    seen_paths: dict[str, str] = {}
    declared_present_roots: list[RootSpec] = []
    for role in roles:
        if not isinstance(role, dict) or role.get("state") != "present":
            continue
        locator = role.get("path")
        kind = role.get("expected_kind", "directory")
        raw_exclusions = role.get("metadata_only_exclusions", [])
        if (
            not isinstance(locator, str)
            or not Path(locator).is_absolute()
            or not isinstance(kind, str)
            or kind not in {"directory", "file"}
            or not isinstance(raw_exclusions, list)
        ):
            continue
        exclusions: list[tuple[str, str]] = []
        for exclusion in raw_exclusions:
            if not isinstance(exclusion, dict):
                continue
            exclusion_path = exclusion.get("path")
            exclusion_reason = exclusion.get("reason")
            if isinstance(exclusion_path, str) and isinstance(exclusion_reason, str):
                exclusions.append((exclusion_path, exclusion_reason))
        candidate = RootSpec(
            str(role.get("key", "")),
            Path(os.path.normpath(locator)),
            tuple(exclusions),
            kind,
        )
        _validate_exclusions(candidate)
        declared_present_roots.append(candidate)
    for role in roles:
        if not isinstance(role, dict):
            raise CensusError("roster_role_invalid")
        key = role.get("key")
        state = role.get("state")
        if not isinstance(key, str) or not key or key in seen_roles:
            raise CensusError("roster_role_duplicate_or_invalid")
        seen_roles.add(key)
        declared_roles.add(key)
        if state == "absent":
            disposition = role.get("disposition")
            reason = role.get("reason")
            if (
                disposition not in ABSENT_DISPOSITIONS
                or not isinstance(reason, str)
                or not reason.strip()
            ):
                raise CensusError("roster_absent_role_unresolved")
            dispositions.append(
                {"role_key": key, "state": "absent", "disposition": disposition, "reason": reason}
            )
            continue
        if state != "present":
            raise CensusError("roster_role_state_invalid")
        locator = role.get("path")
        kind = role.get("expected_kind", "directory")
        if (
            not isinstance(locator, str)
            or not Path(locator).is_absolute()
            or kind not in {"directory", "file"}
        ):
            raise CensusError("roster_present_role_invalid")
        normalized = os.path.normpath(locator)
        previous = seen_paths.get(normalized)
        if previous is not None:
            raise CensusError("roster_duplicate_path_conflict")
        seen_paths[normalized] = key
        raw_exclusions = role.get("metadata_only_exclusions", [])
        if not isinstance(raw_exclusions, list):
            raise CensusError("roster_exclusions_invalid")
        exclusions: list[tuple[str, str]] = []
        for exclusion in raw_exclusions:
            if not isinstance(exclusion, dict):
                raise CensusError("roster_exclusion_invalid")
            exclusion_path = exclusion.get("path")
            exclusion_reason = exclusion.get("reason")
            if not isinstance(exclusion_path, str) or not isinstance(exclusion_reason, str):
                raise CensusError("roster_exclusion_invalid")
            exclusions.append((exclusion_path, exclusion_reason))
        spec = RootSpec(key, Path(normalized), tuple(exclusions), kind)
        _validate_exclusions(spec)
        roots.append(spec)
        dispositions.append({"role_key": key, "state": "present"})
        raw_expectations = role.get("opaque_expectations", {})
        if not isinstance(raw_expectations, dict) or any(
            not isinstance(name, str)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            for name, count in raw_expectations.items()
        ):
            raise CensusError("roster_opaque_expectations_invalid")
        expectations[key] = dict(raw_expectations)
        checkout_key = role.get("git_checkout_key")
        if checkout_key is not None:
            if kind != "directory" or not isinstance(checkout_key, str) or not checkout_key:
                raise CensusError("roster_git_checkout_role_invalid")
            located = _git_metadata_paths(Path(normalized))
            for git_kind in ("git-dir", "git-common-dir"):
                locator_path = located[git_kind]
                if any(
                    _locator_in_explicit_exclusion(declared_root, locator_path)
                    for declared_root in declared_present_roots
                ):
                    raise CensusError("git_admin_locator_inside_excluded_subtree")
                covered_by = next(
                    (
                        root.key
                        for root in roots
                        if (locator_path == root.path or root.path in locator_path.parents)
                        and _is_scanned_by_root(root, locator_path)
                    ),
                    None,
                )
                if covered_by is None:
                    git_role = f"{key}_{git_kind.replace('-', '_')}"
                    if git_role in seen_roles:
                        raise CensusError("roster_git_role_conflict")
                    seen_roles.add(git_role)
                    roots.append(RootSpec(git_role, locator_path))
                    dispositions.append({"role_key": git_role, "state": "derived_git_metadata"})
                    covered_by = git_role
                located[f"{git_kind}-covered-by"] = Path(covered_by)
            git_bindings.append(
                {
                    "checkout_key": checkout_key,
                    "git_dir_locator": str(located["git-dir"]),
                    "git_dir_covered_by_root_key": str(located["git-dir-covered-by"]),
                    "git_common_dir_locator": str(located["git-common-dir"]),
                    "git_common_dir_covered_by_root_key": str(located["git-common-dir-covered-by"]),
                    "git_admin_distinct_from_common": located["git-dir"]
                    != located["git-common-dir"],
                }
            )
    if not set(required).issubset(declared_roles):
        raise CensusError("roster_required_role_omitted")
    return CensusRoster(
        version=2,
        source_volume_uuid=source_uuid,
        source_mount_point=Path(os.path.normpath(source_mount)),
        required_roles=tuple(required),
        role_dispositions=tuple(dispositions),
        roots=tuple(roots),
        git_bindings=tuple(git_bindings),
        opaque_expectations=expectations,
        storage_volume_uuid=storage_uuid,
        storage_root_id=storage_root_id,
        roster_sha256=hashlib.sha256(chunks).hexdigest(),
        roster_bytes_b64=base64.b64encode(chunks).decode("ascii"),
    )


def default_roster(home: Path, repository: Path) -> tuple[list[RootSpec], list[dict[str, Any]]]:
    """Return the versioned known-root roster; paths remain in the private manifest."""
    codex_base = home / ".codex/worktrees"
    candidates = _discover_linked_worktrees(codex_base)
    if len(candidates) != 1:
        raise CensusError("linked_codex_worktree_locator_ambiguous")
    checkouts = [
        ("owner_checkout", home / "Desktop/memecoin-ai"),
        ("research_checkout", repository),
        ("research_verifier_worktree", Path("/private/tmp/brain-senses-v3-verifier")),
        ("owner_codex_worktree", candidates[0]),
    ]
    roots = [
        RootSpec("owner_checkout", home / "Desktop/memecoin-ai"),
        RootSpec("research_checkout", repository, (("@basename:wallets", "opaque_wallets"),)),
        RootSpec("research_verifier_worktree", Path("/private/tmp/brain-senses-v3-verifier")),
        RootSpec(
            "owner_codex_worktree",
            candidates[0],
            (("apps/control-center/.local", "opaque_codex_local"),),
        ),
        RootSpec("private_brain_config", home / ".config/brain-research"),
        RootSpec("launch_agents", home / "Library/LaunchAgents"),
        RootSpec(
            "helius_mcp_wrapper",
            home / ".local/bin/helius-mcp-secure",
            expected_kind="file",
        ),
        RootSpec("codex_mcp_config", home / ".codex/config.toml", expected_kind="file"),
        RootSpec("cursor_mcp_config", home / ".cursor/mcp.json", expected_kind="file"),
    ]
    declared_paths = [(root.key, Path(os.path.abspath(root.path))) for root in roots]
    roots_by_key = {root.key: root for root in roots}
    git_root_keys: dict[str, str] = {}
    git_bindings: list[dict[str, Any]] = []
    for key, checkout in checkouts:
        located = _git_metadata_paths(checkout)
        for kind in ("git-dir", "git-common-dir"):
            locator = located[kind]
            covering_key = next(
                (
                    root_key
                    for root_key, parent in declared_paths
                    if locator == parent or parent in locator.parents
                    if _is_scanned_by_root(roots_by_key[root_key], locator)
                ),
                None,
            )
            locator_text = str(locator)
            root_key_for_locator = git_root_keys.get(locator_text)
            if covering_key is not None:
                root_key_for_locator = covering_key
            elif root_key_for_locator is None:
                root_key_for_locator = f"{key}_{kind.replace('-', '_')}"
                git_root_keys[locator_text] = root_key_for_locator
                roots.append(RootSpec(root_key_for_locator, locator))
            located[kind] = locator
            located[f"{kind}-covered-by"] = Path(root_key_for_locator)
        git_bindings.append(
            {
                "checkout_key": key,
                "git_dir_locator": str(located["git-dir"]),
                "git_dir_covered_by_root_key": str(located["git-dir-covered-by"]),
                "git_common_dir_locator": str(located["git-common-dir"]),
                "git_common_dir_covered_by_root_key": str(located["git-common-dir-covered-by"]),
                "git_admin_distinct_from_common": located["git-dir"] != located["git-common-dir"],
            }
        )
    return roots, git_bindings


def _is_scanned_by_root(root: RootSpec, locator: Path) -> bool:
    try:
        relative = locator.relative_to(root.path)
    except ValueError:
        return False
    components = relative.parts
    if ".env.local" in components:
        return False
    for excluded_path, _reason in root.excluded_subtrees:
        if excluded_path == "@basename:wallets":
            if "wallets" in components:
                return False
            continue
        excluded_components = tuple(excluded_path.split("/"))
        if components[: len(excluded_components)] == excluded_components:
            return False
    current_fd = _root_open(root.path)
    root_device = os.fstat(current_fd).st_dev
    try:
        for component in components:
            try:
                st = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            except OSError:
                return False
            if not stat.S_ISDIR(st.st_mode) or st.st_dev != root_device:
                return False
            try:
                child_fd = os.open(component, NOFOLLOW_DIRECTORY, dir_fd=current_fd)
            except OSError:
                return False
            child_st = os.fstat(child_fd)
            if (st.st_dev, st.st_ino) != (child_st.st_dev, child_st.st_ino):
                os.close(child_fd)
                return False
            os.close(current_fd)
            current_fd = child_fd
        return True
    except CensusError:
        return False
    finally:
        os.close(current_fd)


def _locator_in_explicit_exclusion(root: RootSpec, locator: Path) -> bool:
    try:
        components = locator.relative_to(root.path).parts
    except ValueError:
        return False
    if ".env.local" in components:
        return True
    for excluded_path, _reason in root.excluded_subtrees:
        if excluded_path == "@basename:wallets":
            if "wallets" in components:
                return True
            continue
        excluded_components = tuple(excluded_path.split("/"))
        if components[: len(excluded_components)] == excluded_components:
            return True
    return False


def _read_small_control_file(parent_fd: int, name: str, limit: int = 4096) -> str | None:
    try:
        fd = os.open(name, NOFOLLOW_FILE, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CensusError("git_control_file_open_failed") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            raise CensusError("git_control_file_invalid")
        raw = os.read(fd, limit + 1)
        if len(raw) > limit or os.read(fd, 1):
            raise CensusError("git_control_file_too_large")
        try:
            return raw.decode("utf-8", "strict").strip()
        except UnicodeDecodeError as exc:
            raise CensusError("git_control_file_encoding_invalid") from exc
    finally:
        os.close(fd)


def _git_metadata_paths(checkout: Path) -> dict[str, Path]:
    """Resolve only bounded .git and commondir control records; never read config."""
    checkout_fd = _root_open(checkout)
    try:
        marker = os.stat(".git", dir_fd=checkout_fd, follow_symlinks=False)
        if stat.S_ISDIR(marker.st_mode):
            git_dir = checkout / ".git"
        elif stat.S_ISREG(marker.st_mode):
            record = _read_small_control_file(checkout_fd, ".git")
            if not record or not record.startswith("gitdir: "):
                raise CensusError("git_pointer_invalid")
            raw = Path(record[8:])
            git_dir = raw if raw.is_absolute() else checkout / raw
            git_dir = Path(os.path.abspath(git_dir))
        else:
            raise CensusError("git_marker_kind_invalid")
    except OSError as exc:
        raise CensusError("git_marker_unavailable") from exc
    finally:
        os.close(checkout_fd)
    git_fd = _root_open(git_dir)
    try:
        common_record = _read_small_control_file(git_fd, "commondir")
    finally:
        os.close(git_fd)
    if common_record:
        common_raw = Path(common_record)
        common_dir = common_raw if common_raw.is_absolute() else git_dir / common_raw
        common_dir = Path(os.path.abspath(common_dir))
    else:
        common_dir = git_dir
    for locator in (git_dir, common_dir):
        fd = _root_open(locator)
        os.close(fd)
    return {"git-dir": git_dir, "git-common-dir": common_dir}


def _discover_linked_worktrees(base: Path) -> list[Path]:
    """Inspect at most two levels via held descriptors; stop at each worktree root."""
    base_fd = _root_open(base)
    candidates: list[Path] = []

    def marker_present(directory_fd: int) -> bool:
        try:
            marker = os.stat(".git", dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise CensusError("codex_git_marker_unreadable") from exc
        return stat.S_ISDIR(marker.st_mode) or stat.S_ISREG(marker.st_mode)

    try:
        with os.scandir(base_fd) as iterator:
            names = sorted(entry.name for entry in iterator)
        for name in names:
            try:
                st = os.stat(name, dir_fd=base_fd, follow_symlinks=False)
            except OSError as exc:
                raise CensusError("codex_worktree_candidate_unreadable") from exc
            if not stat.S_ISDIR(st.st_mode):
                continue
            try:
                child_fd = os.open(name, NOFOLLOW_DIRECTORY, dir_fd=base_fd)
            except OSError as exc:
                raise CensusError("codex_worktree_candidate_open_failed") from exc
            try:
                opened = os.fstat(child_fd)
                if (st.st_dev, st.st_ino) != (opened.st_dev, opened.st_ino):
                    raise CensusError("codex_worktree_candidate_changed")
                if marker_present(child_fd):
                    candidates.append(base / name)
                    continue
                with os.scandir(child_fd) as iterator:
                    child_names = sorted(entry.name for entry in iterator)
                for child_name in child_names:
                    try:
                        child_st = os.stat(child_name, dir_fd=child_fd, follow_symlinks=False)
                    except OSError as exc:
                        raise CensusError("codex_nested_candidate_unreadable") from exc
                    if not stat.S_ISDIR(child_st.st_mode):
                        continue
                    try:
                        grandchild_fd = os.open(child_name, NOFOLLOW_DIRECTORY, dir_fd=child_fd)
                    except OSError as exc:
                        raise CensusError("codex_nested_candidate_open_failed") from exc
                    try:
                        grandchild_st = os.fstat(grandchild_fd)
                        if (child_st.st_dev, child_st.st_ino) != (
                            grandchild_st.st_dev,
                            grandchild_st.st_ino,
                        ):
                            raise CensusError("codex_worktree_candidate_changed")
                        if marker_present(grandchild_fd):
                            candidates.append(base / name / child_name)
                    finally:
                        os.close(grandchild_fd)
            finally:
                os.close(child_fd)
    finally:
        os.close(base_fd)
    return candidates


def run_census(
    roots: Sequence[RootSpec],
    *,
    storage_config: Path,
    git_bindings: Sequence[dict[str, Any]] = (),
    now: dt.datetime | None = None,
    roster: CensusRoster | None = None,
) -> dict[str, Any]:
    if len({root.key for root in roots}) != len(roots):
        raise CensusError("root_roster_duplicate")
    if roster is None:
        if len(roots) < 9:
            raise CensusError("root_roster_incomplete_or_duplicate")
        source_volume_uuid = EXPECTED_DATA_VOLUME_UUID
        source_mount_point = Path(EXPECTED_DATA_MOUNT_POINT)
        expected_storage_uuid = EXPECTED_KINGSTON_VOLUME_UUID
        expected_storage_root_id = EXPECTED_KINGSTON_ROOT_ID
        opaque_expectations = None
        role_dispositions: list[dict[str, str]] = []
        roster_version = 1
    else:
        try:
            roster_bytes = base64.b64decode(roster.roster_bytes_b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise CensusError("root_roster_bytes_invalid") from exc
        if (
            roster.version != 2
            or len(roster.roster_sha256) != 64
            or hashlib.sha256(roster_bytes).hexdigest() != roster.roster_sha256
        ):
            raise CensusError("root_roster_digest_mismatch")
        if not set(roster.required_roles).issubset(
            {root.key for root in roots}
            | {row["role_key"] for row in roster.role_dispositions if row["state"] == "absent"}
        ):
            raise CensusError("root_roster_required_role_omitted")
        source_volume_uuid = roster.source_volume_uuid
        source_mount_point = roster.source_mount_point
        expected_storage_uuid = roster.storage_volume_uuid
        expected_storage_root_id = roster.storage_root_id
        opaque_expectations = roster.opaque_expectations
        role_dispositions = list(roster.role_dispositions)
        roster_version = roster.version
    captured_at = now or dt.datetime.now(dt.UTC)
    created_at_text = captured_at.isoformat().replace("+00:00", "Z")
    stamp = captured_at.strftime("%Y%m%dT%H%M%SZ")
    nonce = secrets.token_hex(8)
    private_relative = f"{PRIVATE_DIR}/census-{stamp}-{nonce}.jsonl"
    pending_relative = f"{private_relative}.pending"
    complete_relative = f"{PRIVATE_DIR}/census-{stamp}-{nonce}.complete.json"
    census_roots: list[dict[str, Any]] = []
    stream_fd = -1
    stream_identity: tuple[int, int] | None = None
    mount_fd = -1
    root_fd = -1
    parent_fd = -1
    with writer_guard(storage_config) as guard:
        if guard.volume_uuid != expected_storage_uuid or guard.root_id != expected_storage_root_id:
            raise CensusError("kingston_binding_mismatch")
        root_fd, parent_fd, pending_name = _open_private_parent(guard, pending_relative)
        final_name = private_relative.rsplit("/", 1)[-1]
        mount_fd = os.dup(guard.mount_fd)
        binding = _UnlockedStorageBinding(
            storage_config,
            guard.config,
            mount_fd,
            guard.volume_uuid,
            guard.root_id,
        )
        try:
            guard.revalidate()
            stream_fd = os.open(
                pending_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_fd,
            )
            created = os.fstat(stream_fd)
            stream_identity = (created.st_dev, created.st_ino)
            if (
                not stat.S_ISREG(created.st_mode)
                or created.st_uid != os.getuid()
                or stat.S_IMODE(created.st_mode) != 0o600
                or created.st_nlink != 1
            ):
                raise CensusError("private_stream_reservation_identity")
            guard.revalidate()
        except FileExistsError as exc:
            os.close(parent_fd)
            os.close(root_fd)
            os.close(mount_fd)
            raise CensusError("private_stream_already_exists") from exc
        except BaseException as original:
            cleanup_error: BaseException | None = None
            try:
                if stream_fd >= 0:
                    os.close(stream_fd)
                    stream_fd = -1
                if stream_identity is not None:
                    pending_cleanup_ok = _unlink_if_owned_inode(
                        parent_fd,
                        pending_name,
                        device=stream_identity[0],
                        inode=stream_identity[1],
                    )
                    if not pending_cleanup_ok:
                        raise CensusError("private_pending_cleanup_ownership_mismatch")
                    os.fsync(parent_fd)
            except BaseException as exc:
                cleanup_error = exc
            finally:
                os.close(parent_fd)
                os.close(root_fd)
                os.close(mount_fd)
            if cleanup_error is not None:
                raise CensusError("private_pending_cleanup_failed") from cleanup_error
            raise original
    writer = _JsonlStream(stream_fd)
    try:
        binding.revalidate()
        writer.write(
            {
                "record_type": "header",
                "schema": SCHEMA,
                "format": "jsonl",
                "created_at_utc": created_at_text,
                "roster_version": roster_version,
                "source_volume_uuid_pin": source_volume_uuid,
                "source_mount_point_pin": str(source_mount_point),
                "storage_volume_uuid_pin": expected_storage_uuid,
                "storage_root_id_pin": expected_storage_root_id,
                "input_roster_sha256": roster.roster_sha256 if roster else None,
                "input_roster_bytes_b64": roster.roster_bytes_b64 if roster else None,
                "required_roles": list(roster.required_roles)
                if roster
                else [root.key for root in roots],
                "role_dispositions": role_dispositions,
                "private_locator_manifest": True,
                "source_cutoff_established": False,
                "git_directory_bindings": list(git_bindings),
                "root_keys": [root.key for root in roots],
            }
        )
        entries_since_revalidation = 0
        for root in roots:
            writer.write({"record_type": "root_start", "root_key": root.key})

            def emit_entry(entry: dict[str, Any], *, root_key: str = root.key) -> None:
                nonlocal entries_since_revalidation
                entries_since_revalidation += 1
                if entries_since_revalidation % 10_000 == 0:
                    binding.revalidate_identity()
                writer.write({"record_type": "entry", "root_key": root_key, "entry": entry})

            root_record = census_root(
                root,
                record_sink=emit_entry,
                expected_volume_uuid=source_volume_uuid,
                expected_mount_point=source_mount_point,
            )
            root_record.pop("entries", None)
            observed = root_record.pop("entry_disposition_counts", {})
            matches = _opaque_counts_match(root.key, observed, opaque_expectations)
            root_record["opaque_exclusion_counts_match_expected"] = matches
            if not matches:
                root_record["counts"]["unresolved"] = root_record["counts"].get("unresolved", 0) + 1
                root_record["complete_traversal"] = False
            writer.write({"record_type": "root_summary", **root_record})
            census_roots.append(root_record)
            binding.revalidate()
        summary = _aggregate(census_roots, expected_volume_uuid=source_volume_uuid)
        summary["scan_complete"] = _scan_complete(
            census_roots,
            all_on_pinned_volume=summary["all_roots_on_pinned_data_volume"],
        )
        summary["declared_root_traversal_complete"] = summary["scan_complete"]
        summary["scan_incomplete_reason"] = "no_present_roots" if not census_roots else None
        summary["root_closure_proven"] = False
        summary["absent_roles"] = [
            row["role_key"] for row in role_dispositions if row["state"] == "absent"
        ]
        binding.revalidate()
        transfer_fd = stream_fd
        stream_fd = -1
        digest, manifest_bytes = _commit_private_stream(
            binding,
            root_fd=root_fd,
            parent_fd=parent_fd,
            pending_name=pending_name,
            final_name=final_name,
            file_fd=transfer_fd,
            writer=writer,
        )
    except BaseException as original:
        if stream_fd >= 0:
            os.close(stream_fd)
            stream_fd = -1
        try:
            if stream_identity is None:
                raise CensusError("private_stream_reservation_identity_missing")
            pending_cleanup_ok = _unlink_if_owned_inode(
                parent_fd,
                pending_name,
                device=stream_identity[0],
                inode=stream_identity[1],
            )
            # A final-name collision belongs to another writer; never remove it.
            _unlink_if_owned_inode(
                parent_fd,
                final_name,
                device=stream_identity[0],
                inode=stream_identity[1],
            )
            if not pending_cleanup_ok:
                raise CensusError("private_pending_cleanup_ownership_mismatch")
            os.fsync(parent_fd)
        except (OSError, CensusError) as cleanup_error:
            raise CensusError("private_partial_cleanup_failed") from cleanup_error
        raise original
    finally:
        if stream_fd >= 0:
            os.close(stream_fd)
        os.close(parent_fd)
        os.close(root_fd)
    completion = {
        "schema": f"{SCHEMA}-complete",
        "status": "private_manifest_published_and_reread_verified",
        "manifest_relative_path": private_relative,
        "manifest_sha256": digest,
        "manifest_bytes": manifest_bytes,
        "input_roster_sha256": roster.roster_sha256 if roster else None,
        "root_count": len(census_roots),
        "git_binding_count": len(git_bindings),
        "scan_complete": summary["scan_complete"],
        "source_consistency_proven": False,
        "safe_to_erase": False,
    }
    completion_raw = _canonical(completion)
    with writer_guard(storage_config) as guard:
        if guard.volume_uuid != expected_storage_uuid or guard.root_id != expected_storage_root_id:
            raise CensusError("kingston_binding_mismatch")
        _publish_create_only(guard, complete_relative, completion_raw)
    completion_sha = _verify_published_pair(
        binding,
        private_relative,
        complete_relative,
        digest,
        manifest_bytes,
        completion_raw,
    )
    try:
        os.close(mount_fd)
    except OSError:
        pass
    summary["observed_at_utc"] = created_at_text
    summary["private_manifest_sha256"] = digest
    summary["private_manifest_complete_marker_sha256"] = completion_sha
    summary["publication_complete"] = True
    summary["private_manifest_bytes"] = manifest_bytes
    summary["roster_version"] = roster_version
    summary["input_roster_sha256"] = roster.roster_sha256 if roster else None
    summary["git_binding_count"] = len(git_bindings)
    summary["git_external_metadata_root_count"] = sum(
        "_git_dir" in root["root_key"] or "_git_common_dir" in root["root_key"]
        for root in census_roots
    )
    summary["kingston_volume_uuid"] = binding.volume_uuid
    summary["kingston_root_id"] = binding.root_id
    summary["private_manifest_locator"] = private_relative
    summary["private_manifest_complete_marker_locator"] = complete_relative
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-config", type=Path)
    parser.add_argument("--roster", type=Path, help="private JSON roster using schema v2")
    parser.add_argument(
        "--legacy-profile",
        action="store_true",
        help="explicitly run the historical hardcoded owner-host v1 census profile",
    )
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        if args.roster is not None and args.legacy_profile:
            raise CensusError("roster_and_legacy_profile_are_mutually_exclusive")
        if args.roster is None and not args.legacy_profile:
            raise CensusError("explicit_roster_required")
        if args.storage_config is None:
            raise CensusError("storage_config_required")
        if args.roster is not None:
            roster = load_roster(args.roster)
            result = run_census(
                roster.roots,
                storage_config=args.storage_config,
                git_bindings=roster.git_bindings,
                roster=roster,
            )
        else:
            roots, git_bindings = default_roster(Path.home(), args.repository)
            result = run_census(
                roots,
                storage_config=args.storage_config,
                git_bindings=git_bindings,
            )
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("scan_complete") else 2
    except (CensusError, StorageError, OSError, ValueError) as exc:
        code = exc.args[0] if isinstance(exc, CensusError) and exc.args else type(exc).__name__
        print(
            json.dumps({"status": "blocked", "reason": str(code)}, sort_keys=True), file=sys.stderr
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
