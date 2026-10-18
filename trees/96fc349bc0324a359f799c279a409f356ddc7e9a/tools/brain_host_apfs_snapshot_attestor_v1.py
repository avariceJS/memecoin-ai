#!/usr/bin/env python3
"""Attest a caller-approved, already-mounted read-only APFS snapshot.

This module never creates, mounts, or unmounts snapshots.  Its Darwin backend
cross-checks diskutil plist observations with held directory descriptors and
fstatfs.  The injected backend exists only for synthetic contract tests and
cannot produce an owner T qualification.
"""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

SCHEMA = "brain-host-apfs-snapshot-attestation-v1"
ROOT_COUNT = 3
UUID_RE = re.compile(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\Z")
DEVICE_RE = re.compile(r"disk[0-9]+s[0-9]+\Z")
MNT_RDONLY = 0x00000001
MAXPATHLEN = 1024


class SnapshotAttestationError(ValueError):
    """The mounted snapshot does not match the approved source plan."""


@dataclass(frozen=True)
class ApprovedHostPlan:
    """Independently reviewed host bindings; callers must not derive from mount data.

    Approval is an external authority decision.  This object deliberately has
    no JSON loader and the attestor does not treat a serialized caller manifest
    as self-authenticating.
    """

    approval_id: str
    data_volume_uuid: str
    data_device_identifier: str
    data_container_reference: str
    data_volume_group_id: str
    snapshot_uuid: str
    snapshot_xid: int
    root_locators: Mapping[str, str]


class SnapshotOS(Protocol):
    """OS observations needed by the contract; tests inject synthetic values."""

    def disk_info(self, target: str) -> dict[str, Any]: ...

    def snapshots(self, target: str) -> list[dict[str, Any]]: ...

    def filesystem(self, fd: int) -> dict[str, Any]: ...


class DarwinSnapshotOS:
    """Read-only macOS diskutil and fstatfs adapter."""

    class _StatFS(ctypes.Structure):
        _fields_ = [
            ("f_bsize", ctypes.c_uint32),
            ("f_iosize", ctypes.c_int32),
            ("f_blocks", ctypes.c_uint64),
            ("f_bfree", ctypes.c_uint64),
            ("f_bavail", ctypes.c_uint64),
            ("f_files", ctypes.c_uint64),
            ("f_ffree", ctypes.c_uint64),
            ("f_fsid", ctypes.c_int32 * 2),
            ("f_owner", ctypes.c_uint32),
            ("f_type", ctypes.c_uint32),
            ("f_flags", ctypes.c_uint32),
            ("f_fssubtype", ctypes.c_uint32),
            ("f_fstypename", ctypes.c_char * 16),
            ("f_mntonname", ctypes.c_char * MAXPATHLEN),
            ("f_mntfromname", ctypes.c_char * MAXPATHLEN),
            ("f_flags_ext", ctypes.c_uint32),
            ("f_reserved", ctypes.c_uint32 * 7),
        ]

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise SnapshotAttestationError("darwin_backend_required")

    @staticmethod
    def _run_plist(args: Sequence[str]) -> dict[str, Any]:
        try:
            result = subprocess.run(
                ["/usr/sbin/diskutil", *args],
                capture_output=True,
                check=False,
                timeout=20,
                stdin=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SnapshotAttestationError("diskutil_query_failed") from exc
        if result.returncode != 0:
            raise SnapshotAttestationError("diskutil_query_failed")
        try:
            payload = plistlib.loads(result.stdout)
        except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
            raise SnapshotAttestationError("diskutil_plist_invalid") from exc
        if not isinstance(payload, dict):
            raise SnapshotAttestationError("diskutil_plist_invalid")
        return payload

    def disk_info(self, target: str) -> dict[str, Any]:
        return self._run_plist(["info", "-plist", target])

    def snapshots(self, target: str) -> list[dict[str, Any]]:
        payload = self._run_plist(["apfs", "listSnapshots", "-plist", target])
        snapshots = payload.get("Snapshots")
        if not isinstance(snapshots, list) or any(not isinstance(row, dict) for row in snapshots):
            raise SnapshotAttestationError("snapshot_list_invalid")
        return snapshots

    def filesystem(self, fd: int) -> dict[str, Any]:
        libc = ctypes.CDLL(None, use_errno=True)
        fstatfs = libc.fstatfs
        fstatfs.argtypes = [ctypes.c_int, ctypes.POINTER(self._StatFS)]
        fstatfs.restype = ctypes.c_int
        value = self._StatFS()
        if fstatfs(fd, ctypes.byref(value)) != 0:
            raise SnapshotAttestationError("fstatfs_failed")
        try:
            fs_type = bytes(value.f_fstypename).split(b"\0", 1)[0].decode("ascii")
            mountpoint = bytes(value.f_mntonname).split(b"\0", 1)[0].decode("utf-8")
            source = bytes(value.f_mntfromname).split(b"\0", 1)[0].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SnapshotAttestationError("fstatfs_identity_invalid") from exc
        return {
            "fs_type": fs_type,
            "flags": int(value.f_flags),
            "fsid": tuple(int(item) for item in value.f_fsid),
            "mountpoint": mountpoint,
            "source": source,
        }


def _valid_uuid(value: object) -> bool:
    return isinstance(value, str) and UUID_RE.fullmatch(value) is not None


def _relative_parts(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise SnapshotAttestationError("root_locator_invalid")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SnapshotAttestationError("root_locator_invalid")
    if path.as_posix() != value:
        raise SnapshotAttestationError("root_locator_noncanonical")
    return path.parts


def _open_absolute_directory_nofollow(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute() or ".." in absolute.parts or absolute != path:
        raise SnapshotAttestationError("mount_path_invalid")
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in absolute.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = child
        result = current
        current = -1
        return result
    except OSError as exc:
        raise SnapshotAttestationError("mount_path_open_failed") from exc
    finally:
        if current >= 0:
            os.close(current)


def _same_fs(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        left.get("fs_type") == right.get("fs_type")
        and left.get("fsid") == right.get("fsid")
        and left.get("source") == right.get("source")
        and left.get("mountpoint") == right.get("mountpoint")
        and bool(left.get("flags", 0) & MNT_RDONLY) == bool(right.get("flags", 0) & MNT_RDONLY)
    )


def _snapshot_source_matches(source: object, device: str, snapshot_name: object) -> bool:
    if not isinstance(source, str):
        return False
    if not isinstance(snapshot_name, str) or not snapshot_name:
        return False
    return source == f"{snapshot_name}@/dev/{device}"


def approved_host_plan_sha256(plan: ApprovedHostPlan) -> str:
    """Digest the exact plan contents for a separate trusted approval pin."""
    canonical = json.dumps(
        {
            "schema": "brain-host-approved-snapshot-plan-v1",
            "approval_id": plan.approval_id,
            "data_volume_uuid": plan.data_volume_uuid.lower(),
            "data_device_identifier": plan.data_device_identifier,
            "data_container_reference": plan.data_container_reference,
            "data_volume_group_id": plan.data_volume_group_id.lower(),
            "snapshot_uuid": plan.snapshot_uuid.lower(),
            "snapshot_xid": plan.snapshot_xid,
            "root_locators": dict(plan.root_locators),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256((canonical + "\n").encode()).hexdigest()


def _identity(st: os.stat_result) -> dict[str, int]:
    return {"device": int(st.st_dev), "inode": int(st.st_ino)}


class HeldSnapshotT(AbstractContextManager["HeldSnapshotT"]):
    """Held, read-only source capability for exactly one approved snapshot.

    A successful production instance attests snapshot identity and three root
    directory handles.  It does not prove root completeness, application
    consistency, capture completeness, Kingston durability, restore, or erase.
    """

    def __init__(
        self,
        plan: ApprovedHostPlan,
        mountpoint: Path,
        *,
        os_api: SnapshotOS | None = None,
        synthetic: bool = False,
        trusted_plan_sha256: str | None = None,
    ) -> None:
        self.plan = plan
        self.mountpoint = mountpoint
        if os_api is not None and not synthetic:
            raise SnapshotAttestationError("synthetic_backend_requires_synthetic_mode")
        self.os_api = os_api or DarwinSnapshotOS()
        self.synthetic = synthetic or os_api is not None
        self.trusted_plan_sha256 = trusted_plan_sha256
        self.root_fds: dict[str, int] = {}
        self._closed = False
        self._validate_plan()
        if not self.mountpoint.is_absolute() or self.mountpoint == Path("/"):
            raise SnapshotAttestationError("mountpoint_must_be_nonroot_absolute")
        try:
            self.mount_parent_fd = _open_absolute_directory_nofollow(mountpoint.parent)
            parent_stat = os.fstat(self.mount_parent_fd)
            self._mount_parent_identity = _identity(parent_stat)
            self._validate_mount_parent(parent_stat)
            self.mount_fd = os.open(
                mountpoint.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=self.mount_parent_fd,
            )
            self._mount_identity = _identity(os.fstat(self.mount_fd))
            self._mount_fs = self.os_api.filesystem(self.mount_fd)
            self._attestation = self._attest_os_identity()
            self._open_roots()
            self.revalidate()
        except BaseException:
            self.close()
            raise

    def _validate_mount_parent(self, parent_stat: os.stat_result) -> None:
        if not self.synthetic and (
            parent_stat.st_uid != 0 or stat.S_IMODE(parent_stat.st_mode) & 0o022
        ):
            raise SnapshotAttestationError("mount_parent_not_root_owned_and_private")

    def _validate_plan(self) -> None:
        if not isinstance(self.plan.approval_id, str) or not self.plan.approval_id.strip():
            raise SnapshotAttestationError("host_plan_approval_missing")
        if not _valid_uuid(self.plan.data_volume_uuid):
            raise SnapshotAttestationError("host_plan_data_uuid_invalid")
        if not DEVICE_RE.fullmatch(self.plan.data_device_identifier):
            raise SnapshotAttestationError("host_plan_data_device_invalid")
        if not isinstance(self.plan.data_container_reference, str) or not re.fullmatch(
            r"disk[0-9]+", self.plan.data_container_reference
        ):
            raise SnapshotAttestationError("host_plan_container_invalid")
        if not _valid_uuid(self.plan.data_volume_group_id):
            raise SnapshotAttestationError("host_plan_volume_group_invalid")
        if not _valid_uuid(self.plan.snapshot_uuid):
            raise SnapshotAttestationError("host_plan_snapshot_uuid_invalid")
        if type(self.plan.snapshot_xid) is not int or self.plan.snapshot_xid <= 0:
            raise SnapshotAttestationError("host_plan_snapshot_xid_invalid")
        if (
            not isinstance(self.trusted_plan_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.trusted_plan_sha256)
            or approved_host_plan_sha256(self.plan) != self.trusted_plan_sha256
        ):
            raise SnapshotAttestationError("approved_host_plan_pin_mismatch")
        locators = dict(self.plan.root_locators)
        if len(locators) != ROOT_COUNT or len(locators) != len(self.plan.root_locators):
            raise SnapshotAttestationError("host_plan_requires_exactly_three_roots")
        parsed = {role: _relative_parts(locator) for role, locator in locators.items()}
        if any(not isinstance(role, str) or not role.strip() for role in parsed):
            raise SnapshotAttestationError("host_plan_root_role_invalid")
        values = list(parsed.values())
        for index, first in enumerate(values):
            for second in values[index + 1 :]:
                if first[: len(second)] == second or second[: len(first)] == first:
                    raise SnapshotAttestationError("host_plan_roots_overlap")
        self._root_parts = parsed

    def _attest_os_identity(self) -> dict[str, Any]:
        base = self.os_api.disk_info(f"/dev/{self.plan.data_device_identifier}")
        if (
            base.get("FilesystemType") != "apfs"
            or str(base.get("VolumeUUID", "")).lower() != self.plan.data_volume_uuid.lower()
            or base.get("DeviceIdentifier") != self.plan.data_device_identifier
            or base.get("APFSContainerReference") != self.plan.data_container_reference
            or str(base.get("APFSVolumeGroupID", "")).lower()
            != self.plan.data_volume_group_id.lower()
            or base.get("APFSSnapshot") is True
        ):
            raise SnapshotAttestationError("pinned_data_volume_mismatch")
        mounted_before = self.os_api.disk_info(str(self.mountpoint))
        if self._mount_fs.get("fs_type") != "apfs":
            raise SnapshotAttestationError("mounted_filesystem_not_apfs")
        if not isinstance(self._mount_fs.get("flags"), int) or not (
            self._mount_fs["flags"] & MNT_RDONLY
        ):
            raise SnapshotAttestationError("mounted_filesystem_not_readonly")
        if (
            mounted_before.get("FilesystemType") != "apfs"
            or mounted_before.get("APFSSnapshot") is not True
            or str(mounted_before.get("APFSSnapshotUUID", "")).lower()
            != self.plan.snapshot_uuid.lower()
            or str(mounted_before.get("APFSVolumeGroupID", "")).lower()
            != self.plan.data_volume_group_id.lower()
            or mounted_before.get("MountPoint") != str(self.mountpoint)
        ):
            raise SnapshotAttestationError("mounted_snapshot_identity_mismatch")
        snapshot_uuid = mounted_before.get("APFSSnapshotUUID")
        if not _valid_uuid(snapshot_uuid):
            raise SnapshotAttestationError("mounted_snapshot_uuid_missing")
        rows = self.os_api.snapshots(f"/dev/{self.plan.data_device_identifier}")
        matches = [
            row
            for row in rows
            if str(row.get("SnapshotUUID", "")).lower() == self.plan.snapshot_uuid.lower()
        ]
        if len(matches) != 1:
            raise SnapshotAttestationError("snapshot_not_unique_on_pinned_data_volume")
        snapshot_xid = matches[0].get("SnapshotXID")
        if type(snapshot_xid) is not int:
            raise SnapshotAttestationError("snapshot_xid_missing")
        if snapshot_xid != self.plan.snapshot_xid:
            raise SnapshotAttestationError("snapshot_xid_does_not_match_approved_selection")
        if snapshot_xid <= 0:
            raise SnapshotAttestationError("snapshot_xid_missing")
        snapshot_name = matches[0].get("SnapshotName")
        if not _snapshot_source_matches(
            self._mount_fs.get("source"), self.plan.data_device_identifier, snapshot_name
        ):
            raise SnapshotAttestationError("snapshot_mount_source_unbound")
        if not self._mount_fs.get("fsid") or not self._mount_fs.get("mountpoint"):
            raise SnapshotAttestationError("mounted_filesystem_identity_missing")
        if self._mount_fs.get("mountpoint") != str(self.mountpoint):
            raise SnapshotAttestationError("held_mountpoint_path_mismatch")
        created_at = mounted_before.get("APFSSnapshotCreateTime")
        if created_at is not None:
            if not isinstance(created_at, datetime):
                raise SnapshotAttestationError("snapshot_creation_time_invalid")
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            else:
                created_at = created_at.astimezone(UTC)
            created_at = created_at.isoformat(timespec="seconds").replace("+00:00", "Z")
        # Recheck around all diskutil queries to detect path replacement or remount.
        fs_after = self.os_api.filesystem(self.mount_fd)
        st_after = _identity(os.fstat(self.mount_fd))
        mounted_after = self.os_api.disk_info(str(self.mountpoint))
        if (
            st_after != self._mount_identity
            or not _same_fs(self._mount_fs, fs_after)
            or mounted_after.get("APFSSnapshot") is not True
            or str(mounted_after.get("APFSSnapshotUUID", "")).lower() != str(snapshot_uuid).lower()
            or mounted_after.get("MountPoint") != str(self.mountpoint)
        ):
            raise SnapshotAttestationError("mount_identity_changed_during_attestation")
        fs_final = self.os_api.filesystem(self.mount_fd)
        if (
            _identity(os.fstat(self.mount_fd)) != self._mount_identity
            or not _same_fs(self._mount_fs, fs_final)
            or fs_final.get("mountpoint") != str(self.mountpoint)
        ):
            raise SnapshotAttestationError("mount_identity_changed_during_attestation")
        return {
            "snapshot_uuid": str(snapshot_uuid).lower(),
            "snapshot_xid": snapshot_xid,
            "snapshot_name": snapshot_name,
            "data_volume_uuid": self.plan.data_volume_uuid.lower(),
            "data_device_identifier": self.plan.data_device_identifier,
            "data_container_reference": self.plan.data_container_reference,
            "data_volume_group_id": self.plan.data_volume_group_id.lower(),
            "mounted_fs_type": self._mount_fs["fs_type"],
            "mounted_fsid": list(self._mount_fs["fsid"]),
            "mountpoint": str(self.mountpoint),
            "source": self._mount_fs["source"],
            "readonly": True,
            "snapshot_created_at": created_at,
        }

    def _open_locator(self, parts: tuple[str, ...]) -> int:
        current = os.dup(self.mount_fd)
        try:
            for component in parts:
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
                fs = self.os_api.filesystem(child)
                if not _same_fs(self._mount_fs, fs):
                    os.close(child)
                    raise SnapshotAttestationError("root_crosses_mount_boundary")
                os.close(current)
                current = child
            return current
        except OSError as exc:
            os.close(current)
            raise SnapshotAttestationError("root_open_failed") from exc
        except BaseException:
            os.close(current)
            raise

    def _open_roots(self) -> None:
        roots: list[tuple[str, tuple[str, ...], dict[str, int]]] = []
        for role, parts in sorted(self._root_parts.items()):
            fd = self._open_locator(parts)
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode) or st.st_dev != os.fstat(self.mount_fd).st_dev:
                os.close(fd)
                raise SnapshotAttestationError("root_type_or_device_mismatch")
            self.root_fds[role] = fd
            roots.append((role, parts, _identity(st)))
        identities = [item[2] for item in roots]
        if len({(item["device"], item["inode"]) for item in identities}) != ROOT_COUNT:
            raise SnapshotAttestationError("root_identity_not_distinct")
        self._root_identities = {role: identity for role, _parts, identity in roots}

    def revalidate(self) -> None:
        if self._closed:
            raise SnapshotAttestationError("snapshot_handle_released")
        fs = self.os_api.filesystem(self.mount_fd)
        if (
            _identity(os.fstat(self.mount_fd)) != self._mount_identity
            or not _same_fs(self._mount_fs, fs)
            or not (fs.get("flags", 0) & MNT_RDONLY)
        ):
            raise SnapshotAttestationError("mounted_snapshot_changed")
        parent_again = _open_absolute_directory_nofollow(self.mountpoint.parent)
        try:
            parent_stat = os.fstat(parent_again)
            self._validate_mount_parent(parent_stat)
            if _identity(parent_stat) != self._mount_parent_identity:
                raise SnapshotAttestationError("mount_parent_replaced")
            mount_again = os.open(
                self.mountpoint.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_again,
            )
            try:
                if _identity(os.fstat(mount_again)) != self._mount_identity or not _same_fs(
                    fs, self.os_api.filesystem(mount_again)
                ):
                    raise SnapshotAttestationError("mount_path_replaced")
            finally:
                os.close(mount_again)
        except OSError as exc:
            raise SnapshotAttestationError("mount_path_open_failed") from exc
        finally:
            os.close(parent_again)
        for role, parts in self._root_parts.items():
            held = _identity(os.fstat(self.root_fds[role]))
            if held != self._root_identities[role]:
                raise SnapshotAttestationError("held_root_changed")
            current = self._open_locator(parts)
            try:
                if _identity(os.fstat(current)) != held:
                    raise SnapshotAttestationError("root_path_replaced")
            finally:
                os.close(current)
        mounted = self.os_api.disk_info(str(self.mountpoint))
        base = self.os_api.disk_info(f"/dev/{self.plan.data_device_identifier}")
        rows = self.os_api.snapshots(f"/dev/{self.plan.data_device_identifier}")
        uuid_rows = [
            row
            for row in rows
            if str(row.get("SnapshotUUID", "")).lower() == self.plan.snapshot_uuid.lower()
        ]
        if (
            base.get("FilesystemType") != "apfs"
            or str(base.get("VolumeUUID", "")).lower() != self.plan.data_volume_uuid.lower()
            or base.get("DeviceIdentifier") != self.plan.data_device_identifier
            or base.get("APFSContainerReference") != self.plan.data_container_reference
            or str(base.get("APFSVolumeGroupID", "")).lower()
            != self.plan.data_volume_group_id.lower()
            or base.get("APFSSnapshot") is True
            or mounted.get("APFSSnapshot") is not True
            or str(mounted.get("APFSSnapshotUUID", "")).lower()
            != self._attestation["snapshot_uuid"]
            or mounted.get("MountPoint") != str(self.mountpoint)
            or len(uuid_rows) != 1
            or type(uuid_rows[0].get("SnapshotXID")) is not int
            or uuid_rows[0].get("SnapshotXID") != self._attestation["snapshot_xid"]
            or not _snapshot_source_matches(
                self.os_api.filesystem(self.mount_fd).get("source"),
                self.plan.data_device_identifier,
                uuid_rows[0].get("SnapshotName"),
            )
        ):
            raise SnapshotAttestationError("snapshot_identity_changed")
        fs_after_os = self.os_api.filesystem(self.mount_fd)
        if (
            _identity(os.fstat(self.mount_fd)) != self._mount_identity
            or not _same_fs(self._mount_fs, fs_after_os)
            or not (fs_after_os.get("flags", 0) & MNT_RDONLY)
            or fs_after_os.get("mountpoint") != str(self.mountpoint)
        ):
            raise SnapshotAttestationError("mounted_snapshot_changed_after_os_check")
        parent_final = _open_absolute_directory_nofollow(self.mountpoint.parent)
        try:
            parent_stat = os.fstat(parent_final)
            self._validate_mount_parent(parent_stat)
            if _identity(parent_stat) != self._mount_parent_identity:
                raise SnapshotAttestationError("mount_parent_replaced")
            leaf_final = os.open(
                self.mountpoint.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_final,
            )
            try:
                if _identity(os.fstat(leaf_final)) != self._mount_identity or not _same_fs(
                    fs_after_os, self.os_api.filesystem(leaf_final)
                ):
                    raise SnapshotAttestationError("mount_path_replaced")
            finally:
                os.close(leaf_final)
        except OSError as exc:
            raise SnapshotAttestationError("mount_path_open_failed") from exc
        finally:
            os.close(parent_final)

    def result(self) -> dict[str, Any]:
        self.revalidate()
        return {
            "schema": SCHEMA,
            "classification": "synthetic_snapshot_contract_only"
            if self.synthetic
            else "mounted_apfs_snapshot_identity_verified",
            "approval_id": self.plan.approval_id,
            "host_plan_sha256": self.trusted_plan_sha256,
            "trusted_plan_pin_matches": True,
            "approval_authority": "external_pin_not_cryptographically_authenticated",
            "snapshot": dict(self._attestation),
            "roots": {
                role: {
                    "locator": "/".join(self._root_parts[role]),
                    "identity": dict(self._root_identities[role]),
                }
                for role in sorted(self._root_parts)
            },
            "held_root_count": len(self.root_fds),
            "root_completeness": "unproven",
            "application_consistency": "unproven",
            "capture_complete": False,
            "kingston_durable": False,
            "restore_verified": False,
            "erase_ready": False,
            "synthetic": self.synthetic,
        }

    @contextlib.contextmanager
    def borrow_root(self, role: str) -> Iterator[int]:
        if self._closed:
            raise SnapshotAttestationError("snapshot_handle_released")
        try:
            fd = self.root_fds[role]
        except KeyError as exc:
            raise SnapshotAttestationError("unknown_root_role") from exc
        self.revalidate()
        borrowed_fd = os.dup(fd)
        try:
            yield borrowed_fd
        finally:
            os.close(borrowed_fd)
            self.revalidate()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in self.root_fds.values():
            os.close(fd)
        self.root_fds.clear()
        mount_fd = getattr(self, "mount_fd", None)
        if mount_fd is not None:
            os.close(mount_fd)
        parent_fd = getattr(self, "mount_parent_fd", None)
        if parent_fd is not None:
            os.close(parent_fd)

    def __enter__(self) -> HeldSnapshotT:
        if self._closed:
            raise SnapshotAttestationError("snapshot_handle_released")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def attest_mounted_snapshot(
    plan: ApprovedHostPlan,
    mountpoint: Path,
    *,
    trusted_plan_sha256: str,
    os_api: SnapshotOS | None = None,
) -> dict[str, Any]:
    """Return bounded attestation; synthetic backends remain explicitly synthetic."""
    synthetic = os_api is not None
    with HeldSnapshotT(
        plan,
        mountpoint,
        os_api=os_api,
        synthetic=synthetic,
        trusted_plan_sha256=trusted_plan_sha256,
    ) as held:
        return held.result()
