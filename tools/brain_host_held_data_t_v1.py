#!/usr/bin/env python3
"""Attest and hold an already-mounted, read-only APFS Data view.

This is a locator/identity capability only. It never unlocks, mounts, unmounts,
repairs, captures, or reads source payloads. A successful result does not prove
shutdown cleanliness, prior mount history, root completeness beyond the pinned
roster, a source cutoff, capture durability, restore, or erase readiness.

Directory descriptors and fstatfs cannot generically distinguish macOS
firmlink traversal from ordinary APFS directory traversal. Where that
provenance is material and not independently established, the root remains
unknown/excluded even when this module verifies the held Data mount identity.
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
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

SCHEMA = "brain-host-held-data-t-v1"
MNT_RDONLY = 0x00000001
MAXPATHLEN = 1024
UUID_RE = re.compile(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\Z")
DEVICE_RE = re.compile(r"disk[0-9]+s[0-9]+\Z")
CONTAINER_RE = re.compile(r"disk[0-9]+\Z")
KERNEL_DEVICE_RE = re.compile(r"disk[0-9]+s[0-9]+(?:s[0-9]+)*\Z")

# This set is the currently required source-root roster. A missing root cannot
# be papered over by a caller-supplied "complete" flag or serialized manifest.
REQUIRED_ROOT_ROLES = frozenset(
    {
        "owner_checkout",
        "research_checkout",
        "owner_codex_worktree",
        "private_brain_config",
        "launch_agents",
        "helius_mcp_wrapper",
        "codex_mcp_config",
        "cursor_mcp_config",
        "verifier_worktree",
    }
)
ROOT_KINDS = {
    "owner_checkout": "directory",
    "research_checkout": "directory",
    "owner_codex_worktree": "directory",
    "private_brain_config": "directory",
    "launch_agents": "directory",
    "helius_mcp_wrapper": "file",
    "codex_mcp_config": "file",
    "cursor_mcp_config": "file",
    "verifier_worktree": "directory",
}


class HeldDataError(ValueError):
    """The already-mounted Data view or declared root roster is invalid."""


@dataclass(frozen=True)
class RootDeclaration:
    """One required root's explicit present locator or unresolved absence."""

    present: bool
    relative_locator: str | None = None
    kind: str | None = None
    unresolved_reason: str | None = None


@dataclass(frozen=True)
class ApprovedDataPlan:
    """Externally pinned Data identity and exact relative source-root roster."""

    approval_id: str
    data_volume_uuid: str
    data_device_identifier: str
    data_container_reference: str
    data_volume_group_id: str
    root_roster: Mapping[str, RootDeclaration]


class DataOS(Protocol):
    def disk_info(self, target: str) -> dict[str, Any]: ...

    def filesystem(self, fd: int) -> dict[str, Any]: ...

    def apfs_volumes(self, container: str) -> list[dict[str, Any]]: ...

    def mount_entries(self) -> list[dict[str, Any]]: ...


class DarwinDataOS:
    """Read-only Darwin diskutil and fstatfs observations."""

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
            raise HeldDataError("darwin_backend_required")

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
            raise HeldDataError("diskutil_query_failed") from exc
        if result.returncode != 0:
            raise HeldDataError("diskutil_query_failed")
        try:
            payload = plistlib.loads(result.stdout)
        except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
            raise HeldDataError("diskutil_plist_invalid") from exc
        if not isinstance(payload, dict):
            raise HeldDataError("diskutil_plist_invalid")
        return payload

    def disk_info(self, target: str) -> dict[str, Any]:
        return self._run_plist(["info", "-plist", target])

    def apfs_volumes(self, container: str) -> list[dict[str, Any]]:
        payload = self._run_plist(["apfs", "list", "-plist", container])
        containers = payload.get("Containers")
        if not isinstance(containers, list):
            raise HeldDataError("apfs_container_inventory_invalid")
        matches = [
            row
            for row in containers
            if isinstance(row, dict) and row.get("ContainerReference") == container
        ]
        if len(matches) != 1 or not isinstance(matches[0].get("Volumes"), list):
            raise HeldDataError("apfs_container_inventory_unresolved")
        volumes = matches[0]["Volumes"]
        if any(not isinstance(row, dict) for row in volumes):
            raise HeldDataError("apfs_volume_inventory_invalid")
        return [
            {
                **row,
                "VolumeUUID": row.get("APFSVolumeUUID", row.get("VolumeUUID")),
                "APFSContainerReference": row.get(
                    "APFSContainerReference", matches[0]["ContainerReference"]
                ),
            }
            for row in volumes
        ]

    def mount_entries(self) -> list[dict[str, Any]]:
        libc = ctypes.CDLL(None, use_errno=True)
        getmntinfo = libc.getmntinfo
        statfs_pointer = ctypes.POINTER(self._StatFS)
        getmntinfo.argtypes = [ctypes.POINTER(statfs_pointer), ctypes.c_int]
        getmntinfo.restype = ctypes.c_int
        entries = statfs_pointer()
        # MNT_NOWAIT: observe the current kernel table without refreshing it.
        count = getmntinfo(ctypes.byref(entries), 2)
        if count < 0:
            raise HeldDataError("kernel_mount_inventory_failed")
        result: list[dict[str, Any]] = []
        for index in range(count):
            row = entries[index]
            try:
                fs_type = bytes(row.f_fstypename).split(b"\0", 1)[0].decode("ascii")
                mountpoint = bytes(row.f_mntonname).split(b"\0", 1)[0].decode("utf-8")
                source = bytes(row.f_mntfromname).split(b"\0", 1)[0].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise HeldDataError("kernel_mount_identity_invalid") from exc
            result.append(
                {
                    "fs_type": fs_type,
                    "flags": int(row.f_flags),
                    "fsid": tuple(int(item) for item in row.f_fsid),
                    "mountpoint": mountpoint,
                    "source": source,
                }
            )
        return result

    def filesystem(self, fd: int) -> dict[str, Any]:
        libc = ctypes.CDLL(None, use_errno=True)
        fstatfs = libc.fstatfs
        fstatfs.argtypes = [ctypes.c_int, ctypes.POINTER(self._StatFS)]
        fstatfs.restype = ctypes.c_int
        value = self._StatFS()
        if fstatfs(fd, ctypes.byref(value)) != 0:
            raise HeldDataError("fstatfs_failed")
        try:
            fs_type = bytes(value.f_fstypename).split(b"\0", 1)[0].decode("ascii")
            mountpoint = bytes(value.f_mntonname).split(b"\0", 1)[0].decode("utf-8")
            source = bytes(value.f_mntfromname).split(b"\0", 1)[0].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HeldDataError("fstatfs_identity_invalid") from exc
        return {
            "fs_type": fs_type,
            "flags": int(value.f_flags),
            "fsid": tuple(int(item) for item in value.f_fsid),
            "mountpoint": mountpoint,
            "source": source,
        }


def _valid_uuid(value: object) -> bool:
    return isinstance(value, str) and UUID_RE.fullmatch(value) is not None


def _normalized_group(value: object) -> str:
    if value is None or value == "":
        return ""
    return str(value).lower()


def _base_volume_device(value: str) -> str:
    snapshot = re.fullmatch(r"(disk[0-9]+s[0-9]+)(?:s[0-9]+)+", value)
    return snapshot.group(1) if snapshot else value


def _relative_parts(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise HeldDataError("root_locator_invalid")
    path = Path(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise HeldDataError("root_locator_invalid")
    if path.as_posix() != value:
        raise HeldDataError("root_locator_noncanonical")
    return path.parts


def _normalized_locator(parts: tuple[str, ...]) -> tuple[str, ...]:
    """Conservative APFS-style case/Unicode normalization for alias checks."""
    return tuple(unicodedata.normalize("NFD", part).casefold() for part in parts)


def _reject_component_identity_aliases(
    root_parts: Mapping[str, tuple[str, ...]],
    identity_chains: Mapping[str, tuple[tuple[int, int], ...]],
) -> None:
    roles = sorted(root_parts)
    for index, first_role in enumerate(roles):
        first_path = _normalized_locator(root_parts[first_role])
        first_chain = identity_chains[first_role]
        for second_role in roles[index + 1 :]:
            second_path = _normalized_locator(root_parts[second_role])
            second_chain = identity_chains[second_role]
            shared_path_prefix = 0
            while (
                shared_path_prefix < min(len(first_path), len(second_path))
                and first_path[shared_path_prefix] == second_path[shared_path_prefix]
            ):
                if first_chain[shared_path_prefix] != second_chain[shared_path_prefix]:
                    raise HeldDataError("declared_root_component_identity_changed")
                shared_path_prefix += 1
            if set(first_chain[shared_path_prefix:]) & set(second_chain[shared_path_prefix:]):
                raise HeldDataError("declared_root_alias_ambiguous")


def _identity(st: os.stat_result) -> dict[str, int]:
    return {"device": int(st.st_dev), "inode": int(st.st_ino)}


def _same_fs(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(
        left.get(key) == right.get(key)
        for key in ("fs_type", "fsid", "source", "mountpoint")
    ) and bool(left.get("flags", 0) & MNT_RDONLY) == bool(
        right.get("flags", 0) & MNT_RDONLY
    )


def approved_data_plan_sha256(plan: ApprovedDataPlan) -> str:
    canonical = json.dumps(
        {
            "schema": "brain-host-approved-data-plan-v1",
            "approval_id": plan.approval_id,
            "data_volume_uuid": plan.data_volume_uuid.lower(),
            "data_device_identifier": plan.data_device_identifier,
            "data_container_reference": plan.data_container_reference,
            "data_volume_group_id": plan.data_volume_group_id.lower(),
            "root_roster": {
                role: {
                    "present": declaration.present,
                    "relative_locator": declaration.relative_locator,
                    "kind": declaration.kind,
                    "unresolved_reason": declaration.unresolved_reason,
                }
                for role, declaration in plan.root_roster.items()
            },
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256((canonical + "\n").encode()).hexdigest()


def _open_absolute_directory_nofollow(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute() or ".." in absolute.parts or absolute != path:
        raise HeldDataError("mount_path_invalid")
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
        raise HeldDataError("mount_path_open_failed") from exc
    finally:
        if current >= 0:
            os.close(current)


class HeldDataT(AbstractContextManager["HeldDataT"]):
    """Held read-only Data identity and descriptor-relative root locators.

    The supplied trusted plan digest is an external pin. It is not authenticated
    by this module. This object intentionally accepts no fields for shutdown,
    mount history, source cutoff, or erase readiness.
    """

    def __init__(
        self,
        plan: ApprovedDataPlan,
        mountpoint: Path,
        *,
        trusted_plan_sha256: str,
        os_api: DataOS | None = None,
        synthetic: bool = False,
    ) -> None:
        self.plan = plan
        self.mountpoint = mountpoint
        if os_api is not None and not synthetic:
            raise HeldDataError("synthetic_backend_requires_synthetic_mode")
        self.synthetic = synthetic or os_api is not None
        self.os_api = os_api or DarwinDataOS()
        self.trusted_plan_sha256 = trusted_plan_sha256
        self.root_fds: dict[str, int] = {}
        self._closed = False
        self._validate_plan()
        if not mountpoint.is_absolute() or mountpoint == Path("/"):
            raise HeldDataError("mountpoint_must_be_nonroot_absolute")
        try:
            self.mount_parent_fd = _open_absolute_directory_nofollow(mountpoint.parent)
            self._mount_parent_identity = _identity(os.fstat(self.mount_parent_fd))
            self.mount_fd = os.open(
                mountpoint.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=self.mount_parent_fd,
            )
            self._mount_identity = _identity(os.fstat(self.mount_fd))
            self._mount_fs = self.os_api.filesystem(self.mount_fd)
            self._data_identity = self._attest_identity()
            self._open_roots()
            self.revalidate()
        except BaseException:
            self.close()
            raise

    def _validate_plan(self) -> None:
        if not isinstance(self.plan.approval_id, str) or not self.plan.approval_id.strip():
            raise HeldDataError("data_plan_approval_missing")
        if not _valid_uuid(self.plan.data_volume_uuid):
            raise HeldDataError("data_plan_uuid_invalid")
        if not DEVICE_RE.fullmatch(self.plan.data_device_identifier):
            raise HeldDataError("data_plan_device_invalid")
        if not CONTAINER_RE.fullmatch(self.plan.data_container_reference):
            raise HeldDataError("data_plan_container_invalid")
        if not _valid_uuid(self.plan.data_volume_group_id):
            raise HeldDataError("data_plan_volume_group_invalid")
        if (
            not isinstance(self.trusted_plan_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.trusted_plan_sha256)
            or approved_data_plan_sha256(self.plan) != self.trusted_plan_sha256
        ):
            raise HeldDataError("approved_data_plan_pin_mismatch")
        roster = dict(self.plan.root_roster)
        if set(roster) != REQUIRED_ROOT_ROLES:
            raise HeldDataError("declared_root_roster_incomplete_or_unexpected")
        parsed: dict[str, tuple[str, ...]] = {}
        unresolved: dict[str, str] = {}
        for role, declaration in roster.items():
            if not isinstance(declaration, RootDeclaration):
                raise HeldDataError("root_declaration_invalid")
            if declaration.present is True:
                if (
                    declaration.relative_locator is None
                    or declaration.kind != ROOT_KINDS[role]
                    or declaration.unresolved_reason is not None
                ):
                    raise HeldDataError("present_root_declaration_invalid")
                parsed[role] = _relative_parts(declaration.relative_locator)
            elif declaration.present is False:
                if declaration.relative_locator is not None or declaration.kind is not None:
                    raise HeldDataError("absent_root_must_not_have_locator_or_kind")
                if (
                    not isinstance(declaration.unresolved_reason, str)
                    or not declaration.unresolved_reason.strip()
                    or not re.fullmatch(
                        r"[a-z][a-z0-9_]{0,63}", declaration.unresolved_reason
                    )
                ):
                    raise HeldDataError("absent_root_requires_unresolved_reason")
                unresolved[role] = declaration.unresolved_reason.strip()
            else:
                raise HeldDataError("root_presence_must_be_explicit_boolean")
        verifier = roster["verifier_worktree"]
        if verifier.present is not False:
            raise HeldDataError("verifier_worktree_must_remain_absent_unresolved")
        values = [_normalized_locator(parts) for parts in parsed.values()]
        for index, first in enumerate(values):
            for second in values[index + 1 :]:
                if first == second:
                    raise HeldDataError("declared_root_alias_ambiguous")
                if first[: len(second)] == second or second[: len(first)] == first:
                    raise HeldDataError("declared_roots_overlap")
        self._root_parts = parsed
        self._root_kinds = {role: ROOT_KINDS[role] for role in parsed}
        self._unresolved_roots = unresolved

    def _disk_matches_pin(self, row: Mapping[str, Any]) -> bool:
        return (
            row.get("FilesystemType") == "apfs"
            and str(row.get("VolumeUUID", "")).lower() == self.plan.data_volume_uuid.lower()
            and row.get("DeviceIdentifier") == self.plan.data_device_identifier
            and row.get("APFSContainerReference") == self.plan.data_container_reference
            and str(row.get("APFSVolumeGroupID", "")).lower()
            == self.plan.data_volume_group_id.lower()
            and row.get("APFSSnapshot") is not True
            and row.get("MountPoint") == str(self.mountpoint)
        )

    def _attest_identity(self) -> dict[str, Any]:
        base = self.os_api.disk_info(f"/dev/{self.plan.data_device_identifier}")
        mounted = self.os_api.disk_info(str(self.mountpoint))
        if not self._disk_matches_pin(base) or not self._disk_matches_pin(mounted):
            raise HeldDataError("pinned_data_volume_mismatch")
        fs = self._mount_fs
        if fs.get("fs_type") != "apfs":
            raise HeldDataError("mounted_filesystem_not_apfs")
        if not isinstance(fs.get("flags"), int) or not (fs["flags"] & MNT_RDONLY):
            raise HeldDataError("mounted_filesystem_not_readonly")
        if (
            not fs.get("fsid")
            or fs.get("mountpoint") != str(self.mountpoint)
            or fs.get("source") != f"/dev/{self.plan.data_device_identifier}"
        ):
            raise HeldDataError("mounted_filesystem_identity_missing_or_unbound")
        aliases = self._require_stable_mount(fs)
        return {
            "data_volume_uuid": self.plan.data_volume_uuid.lower(),
            "data_device_identifier": self.plan.data_device_identifier,
            "data_container_reference": self.plan.data_container_reference,
            "data_volume_group_id": self.plan.data_volume_group_id.lower(),
            "mounted_fs_type": fs["fs_type"],
            "mounted_fsid": list(fs["fsid"]),
            "mountpoint": str(self.mountpoint),
            "source": fs["source"],
            "readonly": True,
            "readonly_alias_mountpoints": aliases,
        }

    def _require_stable_mount(self, before: Mapping[str, Any]) -> list[str]:
        fs_after = self.os_api.filesystem(self.mount_fd)
        mounted_after = self.os_api.disk_info(str(self.mountpoint))
        base_after = self.os_api.disk_info(f"/dev/{self.plan.data_device_identifier}")
        aliases = self._audit_apfs_aliases()
        if hasattr(self, "_data_identity") and aliases != self._data_identity.get(
            "readonly_alias_mountpoints"
        ):
            raise HeldDataError("data_alias_set_changed")
        if (
            _identity(os.fstat(self.mount_fd)) != self._mount_identity
            or not _same_fs(before, fs_after)
            or not self._disk_matches_pin(mounted_after)
            or not self._disk_matches_pin(base_after)
        ):
            raise HeldDataError("mounted_data_identity_changed")
        return aliases

    def _audit_apfs_aliases(self) -> list[str]:
        volumes = self.os_api.apfs_volumes(self.plan.data_container_reference)
        if not volumes:
            raise HeldDataError("apfs_volume_inventory_empty")
        seen_devices: set[str] = set()
        seen_uuids: set[str] = set()
        data_rows: list[Mapping[str, Any]] = []
        inventory_by_device: dict[str, tuple[str, str]] = {}
        for row in volumes:
            device = row.get("DeviceIdentifier")
            volume_uuid = str(row.get("VolumeUUID", "")).lower()
            container = row.get("APFSContainerReference")
            if (
                not isinstance(device, str)
                or not DEVICE_RE.fullmatch(device)
                or container != self.plan.data_container_reference
                or not _valid_uuid(volume_uuid)
            ):
                raise HeldDataError("apfs_volume_inventory_unresolved")
            raw_group = row.get("APFSVolumeGroupID")
            if raw_group is None or raw_group == "":
                details = self.os_api.disk_info(f"/dev/{device}")
                group = _normalized_group(details.get("APFSVolumeGroupID"))
                if (
                    details.get("FilesystemType") != "apfs"
                    or details.get("DeviceIdentifier") != device
                    or str(details.get("VolumeUUID", "")).lower() != volume_uuid
                    or details.get("APFSContainerReference") != container
                    or details.get("APFSSnapshot") is True
                    or (group and not _valid_uuid(group))
                ):
                    raise HeldDataError("apfs_volume_group_resolution_mismatch")
                if (
                    not group
                    and (
                        device == self.plan.data_device_identifier
                        or volume_uuid == self.plan.data_volume_uuid.lower()
                    )
                ):
                    raise HeldDataError("apfs_volume_group_resolution_mismatch")
            elif _valid_uuid(raw_group):
                group = str(raw_group).lower()
            else:
                raise HeldDataError("apfs_volume_group_invalid")
            if device in seen_devices:
                raise HeldDataError("apfs_volume_device_alias_ambiguous")
            if volume_uuid in seen_uuids:
                raise HeldDataError("apfs_volume_uuid_alias_ambiguous")
            seen_devices.add(device)
            seen_uuids.add(volume_uuid)
            inventory_by_device[device] = (volume_uuid, group)
            if volume_uuid == self.plan.data_volume_uuid.lower():
                data_rows.append(row)
            if (
                device == self.plan.data_device_identifier
                and volume_uuid != self.plan.data_volume_uuid.lower()
            ):
                raise HeldDataError("apfs_pinned_device_uuid_mismatch")
            if (
                volume_uuid == self.plan.data_volume_uuid.lower()
                and (
                    device != self.plan.data_device_identifier
                    or group != self.plan.data_volume_group_id.lower()
                )
            ):
                raise HeldDataError("apfs_pinned_volume_identity_mismatch")
        if len(data_rows) != 1:
            raise HeldDataError("apfs_pinned_volume_not_unique")

        aliases: list[str] = []
        candidate_mount_signature: list[tuple[Any, ...]] = []
        selected_mount_seen = False
        entries = self.os_api.mount_entries()
        for entry in entries:
            if entry.get("fs_type") != "apfs":
                continue
            source = entry.get("source")
            mountpoint = entry.get("mountpoint")
            if not isinstance(source, str) or not isinstance(mountpoint, str) or not mountpoint:
                raise HeldDataError("kernel_mount_mapping_unresolved")
            # Resolve every current APFS mount through diskutil. An opaque source
            # cannot be excluded as a possible alias solely from its mount-table
            # spelling.
            source_devices = re.findall(r"disk[0-9]+s[0-9]+(?:s[0-9]+)*", source)
            mapped = self.os_api.disk_info(mountpoint)
            mapped_device = mapped.get("DeviceIdentifier")
            mapped_uuid = str(mapped.get("VolumeUUID", "")).lower()
            mapped_container = mapped.get("APFSContainerReference")
            mapped_group = _normalized_group(mapped.get("APFSVolumeGroupID"))
            is_snapshot = mapped.get("APFSSnapshot") is True
            if (
                mapped.get("FilesystemType") != "apfs"
                or not isinstance(mapped_device, str)
                or not KERNEL_DEVICE_RE.fullmatch(mapped_device)
                or not _valid_uuid(mapped_uuid)
                or not isinstance(mapped_container, str)
            ):
                raise HeldDataError("kernel_mount_mapping_unresolved")
            source_mentions_data = any(
                _base_volume_device(device) == self.plan.data_device_identifier
                for device in source_devices
            )
            fsid_matches_data = tuple(entry.get("fsid", ())) == tuple(
                self._mount_fs.get("fsid", ())
            )
            if mapped_container != self.plan.data_container_reference:
                if source_mentions_data or fsid_matches_data:
                    raise HeldDataError("data_alias_identity_mismatch")
                continue
            mapped_base_device = _base_volume_device(mapped_device)
            source_bound = mapped_device in source_devices or (
                is_snapshot
                and any(_base_volume_device(device) == mapped_device for device in source_devices)
            )
            if not source_devices or not source_bound:
                raise HeldDataError("kernel_mount_source_unbound")
            inventory_identity = inventory_by_device.get(mapped_base_device)
            if inventory_identity is None:
                raise HeldDataError("kernel_mount_inventory_mismatch")
            parent_uuid, parent_group = inventory_identity
            if source_mentions_data and mapped_base_device != self.plan.data_device_identifier:
                raise HeldDataError("data_alias_identity_mismatch")
            if fsid_matches_data and mapped_base_device != self.plan.data_device_identifier:
                raise HeldDataError("data_alias_identity_mismatch")
            if is_snapshot:
                snapshot_uuid = str(mapped.get("APFSSnapshotUUID", "")).lower()
                if not _valid_uuid(snapshot_uuid) or mapped_uuid != snapshot_uuid:
                    raise HeldDataError("snapshot_identity_mismatch")
                if mapped_group != parent_group or mapped_uuid == parent_uuid:
                    raise HeldDataError("snapshot_parent_identity_mismatch")
                if parent_uuid == self.plan.data_volume_uuid.lower():
                    raise HeldDataError("data_snapshot_alias_detected")
            elif inventory_identity != (mapped_uuid, mapped_group):
                raise HeldDataError("kernel_mount_inventory_mismatch")
            candidate_mount_signature.append(
                (
                    source,
                    mountpoint,
                    int(entry.get("flags", -1)),
                    tuple(entry.get("fsid", ())),
                    mapped_device,
                    mapped_uuid,
                    mapped_group,
                    is_snapshot,
                )
            )
            if not is_snapshot and mapped_uuid == self.plan.data_volume_uuid.lower():
                if (
                    mapped_device != self.plan.data_device_identifier
                    or mapped_group != self.plan.data_volume_group_id.lower()
                    or mapped.get("APFSSnapshot") is True
                    or entry.get("source") != f"/dev/{self.plan.data_device_identifier}"
                ):
                    raise HeldDataError("data_alias_identity_mismatch")
                if not isinstance(entry.get("flags"), int) or not (
                    entry["flags"] & MNT_RDONLY
                ):
                    raise HeldDataError("writable_data_alias_detected")
                if entry.get("mountpoint") != str(self.mountpoint):
                    aliases.append(mountpoint)
                else:
                    if (
                        tuple(entry.get("fsid", ())) != tuple(self._mount_fs.get("fsid", ()))
                        or entry.get("source") != f"/dev/{self.plan.data_device_identifier}"
                    ):
                        raise HeldDataError("selected_data_kernel_mount_mismatch")
                    selected_mount_seen = True
            elif mapped_device == self.plan.data_device_identifier:
                raise HeldDataError("data_alias_uuid_mismatch")
        if not selected_mount_seen:
            raise HeldDataError("selected_data_mount_missing_from_kernel_table")
        signature = tuple(sorted(candidate_mount_signature))
        previous = getattr(self, "_candidate_mount_signature", None)
        if previous is not None and signature != previous:
            raise HeldDataError("container_mount_alias_set_changed")
        self._candidate_mount_signature = signature
        return sorted(set(aliases))

    def _open_locator(
        self, parts: tuple[str, ...], expected_kind: str
    ) -> tuple[int, tuple[tuple[int, int], ...]]:
        current = os.dup(self.mount_fd)
        identity_chain: list[tuple[int, int]] = []
        try:
            for index, component in enumerate(parts):
                is_leaf = index == len(parts) - 1
                if is_leaf:
                    try:
                        leaf_stat = os.stat(component, dir_fd=current, follow_symlinks=False)
                    except OSError as exc:
                        raise HeldDataError("declared_root_open_failed") from exc
                    if stat.S_ISLNK(leaf_stat.st_mode):
                        raise HeldDataError("declared_root_symlink_rejected")
                    expected_leaf_type = (
                        stat.S_ISDIR(leaf_stat.st_mode)
                        if expected_kind == "directory"
                        else stat.S_ISREG(leaf_stat.st_mode)
                    )
                    if not expected_leaf_type:
                        raise HeldDataError("declared_root_kind_mismatch")
                flags = os.O_RDONLY | os.O_NOFOLLOW
                if not is_leaf or expected_kind == "directory":
                    flags |= os.O_DIRECTORY
                elif expected_kind == "file":
                    flags |= os.O_NONBLOCK
                child = os.open(component, flags, dir_fd=current)
                try:
                    fs = self.os_api.filesystem(child)
                    st = os.fstat(child)
                    if not _same_fs(self._mount_fs, fs) or st.st_dev != os.fstat(
                        self.mount_fd
                    ).st_dev:
                        raise HeldDataError("root_crosses_mount_or_device_boundary")
                    if is_leaf:
                        expected_regular_type = (
                            stat.S_ISDIR(st.st_mode)
                            if expected_kind == "directory"
                            else stat.S_ISREG(st.st_mode)
                        )
                        if not expected_regular_type:
                            raise HeldDataError("declared_root_kind_mismatch")
                    identity_chain.append((int(st.st_dev), int(st.st_ino)))
                except BaseException:
                    os.close(child)
                    raise
                os.close(current)
                current = child
            return current, tuple(identity_chain)
        except OSError as exc:
            os.close(current)
            raise HeldDataError("declared_root_open_failed") from exc
        except BaseException:
            os.close(current)
            raise

    def _open_roots(self) -> None:
        identity_chains: dict[str, tuple[tuple[int, int], ...]] = {}
        for role, parts in sorted(self._root_parts.items()):
            fd, identity_chain = self._open_locator(parts, self._root_kinds[role])
            self.root_fds[role] = fd
            identity_chains[role] = identity_chain
        if len(self.root_fds) != len(self._root_parts):
            raise HeldDataError("declared_root_roster_unreadable")
        _reject_component_identity_aliases(self._root_parts, identity_chains)
        self._root_component_identities = identity_chains

    def revalidate(self) -> None:
        if self._closed:
            raise HeldDataError("data_view_released")
        fs = self.os_api.filesystem(self.mount_fd)
        if (
            _identity(os.fstat(self.mount_fd)) != self._mount_identity
            or not _same_fs(self._mount_fs, fs)
            or not (fs.get("flags", 0) & MNT_RDONLY)
        ):
            raise HeldDataError("mounted_data_changed_or_writable")
        parent = _open_absolute_directory_nofollow(self.mountpoint.parent)
        try:
            if _identity(os.fstat(parent)) != self._mount_parent_identity:
                raise HeldDataError("mount_parent_replaced")
            current_mount = os.open(
                self.mountpoint.name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            try:
                if (
                    _identity(os.fstat(current_mount)) != self._mount_identity
                    or not _same_fs(self._mount_fs, self.os_api.filesystem(current_mount))
                ):
                    raise HeldDataError("mount_path_replaced")
            finally:
                os.close(current_mount)
        except OSError as exc:
            raise HeldDataError("mount_path_open_failed") from exc
        finally:
            os.close(parent)
        self._require_stable_mount(self._mount_fs)
        for role, parts in self._root_parts.items():
            held = _identity(os.fstat(self.root_fds[role]))
            current, current_chain = self._open_locator(parts, self._root_kinds[role])
            try:
                if (
                    _identity(os.fstat(current)) != held
                    or current_chain != self._root_component_identities[role]
                ):
                    raise HeldDataError("declared_root_replaced")
            finally:
                os.close(current)

    def result(self) -> dict[str, Any]:
        self.revalidate()
        return {
            "schema": SCHEMA,
            "classification": "held_readonly_Data_identity_verified",
            "approval_id": self.plan.approval_id,
            "trusted_plan_pin_matches": True,
            "approval_authority": "external_pin_not_cryptographically_authenticated",
            "data": dict(self._data_identity),
            "identity_uniqueness_scope": "pinned_container_and_current_kernel_mount_table",
            "global_apfs_uuid_uniqueness_verified": False,
            "unmounted_cross_container_duplicates_checked": False,
            "root_locators": {
                role: {
                    "locator": "/".join(self._root_parts[role]),
                    "identity": _identity(os.fstat(self.root_fds[role])),
                    "firmlink_provenance": "not_generically_attestable_unknown_if_material",
                }
                for role in sorted(self.root_fds)
            },
            "held_root_count": len(self.root_fds),
            "root_role_roster_schema_validated": True,
            "required_role_closure": not bool(self._unresolved_roots),
            "unresolved_required_roles": {
                role: {"state": "absent_unresolved", "reason": reason}
                for role, reason in sorted(self._unresolved_roots.items())
            },
            "clean_shutdown_verified": False,
            "prior_writable_mount_history_verified": False,
            "source_cutoff_verified": False,
            "T_qualified": False,
            "capture_complete": False,
            "kingston_durable": False,
            "restore_verified": False,
            "erase_ready": False,
            "mock_backend": self.synthetic,
        }

    @contextlib.contextmanager
    def borrow_root(self, role: str) -> Iterator[int]:
        if self._closed:
            raise HeldDataError("data_view_released")
        try:
            fd = self.root_fds[role]
        except KeyError as exc:
            raise HeldDataError("unknown_root_role") from exc
        self.revalidate()
        borrowed = os.dup(fd)
        try:
            yield borrowed
        finally:
            os.close(borrowed)
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

    def __enter__(self) -> HeldDataT:
        if self._closed:
            raise HeldDataError("data_view_released")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
