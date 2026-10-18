#!/usr/bin/env python3
"""Observe the pinned APFS Data volume before a possible Recovery source-view rehearsal.

This is a read-only sequential probe. It never unlocks or mounts a volume, reads
project payloads, provides an atomic APFS snapshot, or proves that the volume was not
mounted earlier in the boot.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import platform
import plistlib
import re
import subprocess
from collections.abc import Callable, Sequence
from typing import Any

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z")
DEVICE_RE = re.compile(r"disk\d+(?:s\d+)*\Z")
MOUNT_LINE_RE = re.compile(r"(?P<source>.+?) on (?P<mountpoint>.+) \((?P<options>[^()]*)\)\Z")

Runner = Callable[[Sequence[str], int], subprocess.CompletedProcess[bytes]]


def run_command(args: Sequence[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(list(args), capture_output=True, timeout=timeout, check=False)


def _run_plist(command: Sequence[str], runner: Runner) -> dict[str, Any]:
    result = runner(command, 15)
    if result.returncode != 0:
        raise ValueError("command_failed")
    try:
        payload = plistlib.loads(result.stdout)
    except (plistlib.InvalidFileException, ValueError, TypeError) as exc:
        raise ValueError("invalid_plist") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_plist_root")
    return payload


def _device(value: Any) -> str:
    if not isinstance(value, str) or not DEVICE_RE.fullmatch(value):
        raise ValueError("invalid_device_identifier")
    return value


def _snapshots(device: str, runner: Runner) -> list[dict[str, int | str]]:
    payload = _run_plist(["/usr/sbin/diskutil", "apfs", "listSnapshots", "-plist", device], runner)
    snapshots = payload.get("Snapshots")
    if not isinstance(snapshots, list):
        raise ValueError("invalid_snapshot_list")
    result: list[dict[str, int | str]] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            raise ValueError("invalid_snapshot_entry")
        snapshot_uuid = snapshot.get("SnapshotUUID")
        snapshot_xid = snapshot.get("SnapshotXID")
        if not isinstance(snapshot_uuid, str) or not UUID_RE.fullmatch(snapshot_uuid):
            raise ValueError("snapshot_uuid_missing")
        if type(snapshot_xid) is not int or snapshot_xid < 0:
            raise ValueError("snapshot_xid_missing")
        # SnapshotName is intentionally excluded; names are not needed for this gate.
        result.append({"uuid": snapshot_uuid.upper(), "xid": snapshot_xid})
    return sorted(result, key=lambda row: (row["xid"], row["uuid"]))


def _matching_mounts(
    data_uuid: str,
    data_group_uuid: str,
    container_ref: str,
    container_members: dict[str, list[str]],
    runner: Runner,
) -> list[dict[str, Any]]:
    result = runner(["/sbin/mount"], 15)
    if result.returncode != 0:
        raise ValueError("mount_table_unavailable")
    try:
        output = result.stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid_mount_table") from exc
    aliases: list[dict[str, Any]] = []
    for line in output.splitlines():
        if not line:
            continue
        match = MOUNT_LINE_RE.fullmatch(line)
        if match is None:
            raise ValueError("mount_table_entry_unparseable")
        source = match.group("source")
        options = [part.strip() for part in match.group("options").split(",")]
        filesystem_type = options[0] if options else "unknown"
        if filesystem_type != "apfs":
            continue
        if not source.startswith("/dev/"):
            raise ValueError("mounted_apfs_source_unresolved")
        mounted_device = _device(source[5:])

        # APFS may expose snapshots as child device identifiers. Resolve against the
        # container's role map; never infer Data solely from a partial textual match.
        parent_devices = [
            device
            for device in container_members
            if mounted_device == device or mounted_device.startswith(device + "s")
        ]
        if parent_devices:
            parent = max(parent_devices, key=len)
            is_data_alias = "Data" in container_members[parent]
        else:
            # An APFS mount on another device number can still alias the pinned Data
            # volume. Resolve it through diskutil; uncertainty in the pinned container
            # is a hard block rather than an empty alias list.
            info = _run_plist(["/usr/sbin/diskutil", "info", "-plist", mounted_device], runner)
            actual_uuid = info.get("VolumeUUID")
            actual_group = info.get("APFSVolumeGroupID")
            actual_container = info.get("APFSContainerReference")
            if actual_uuid is not None and (
                not isinstance(actual_uuid, str) or not UUID_RE.fullmatch(actual_uuid)
            ):
                raise ValueError("mounted_volume_uuid_invalid")
            if actual_group is not None and (
                not isinstance(actual_group, str) or not UUID_RE.fullmatch(actual_group)
            ):
                raise ValueError("mounted_volume_group_invalid")
            if actual_container is not None:
                actual_container = _device(actual_container)
            if actual_uuid is None or actual_container is None:
                raise ValueError("mounted_apfs_identity_incomplete")
            if actual_uuid.upper() == data_uuid or (
                isinstance(actual_group, str) and actual_group.upper() == data_group_uuid.upper()
            ):
                # Group membership alone may identify the paired System volume too.
                # Without a role mapping, it is ambiguous and cannot pass the gate.
                if actual_uuid.upper() == data_uuid:
                    is_data_alias = True
                else:
                    raise ValueError("mounted_apfs_group_alias_ambiguous")
            elif actual_container == container_ref:
                raise ValueError("mounted_apfs_alias_unmapped")
            else:
                is_data_alias = False
        if not is_data_alias:
            continue
        aliases.append(
            {
                "device_identifier": mounted_device,
                "filesystem_type": filesystem_type,
                "read_only_flag": "read-only" in options or "rdonly" in options,
                "mount_flags": sorted(
                    flag for flag in options[1:] if re.fullmatch(r"[A-Za-z0-9_-]+", flag)
                ),
            }
        )
    return sorted(aliases, key=lambda row: row["device_identifier"])


def observe_data_volume(
    expected_uuid: str,
    *,
    expected_group_uuid: str,
    expected_container_uuid: str,
    expected_device_identifier: str,
    runner: Runner = run_command,
    system: str | None = None,
) -> dict[str, Any]:
    """Return fail-closed identity, encryption, snapshot and current mount observations."""
    if not isinstance(expected_uuid, str) or not UUID_RE.fullmatch(expected_uuid):
        raise ValueError("expected_data_uuid_required")
    if not isinstance(expected_group_uuid, str) or not UUID_RE.fullmatch(expected_group_uuid):
        raise ValueError("expected_group_uuid_required")
    if not isinstance(expected_container_uuid, str) or not UUID_RE.fullmatch(
        expected_container_uuid
    ):
        raise ValueError("expected_container_uuid_required")
    expected_device = _device(expected_device_identifier)
    detected_system = system or platform.system()
    if detected_system != "Darwin":
        return {
            "schema": "brain-host-recovery-preflight-observation-v1",
            "state": "unknown",
            "reason": "darwin_required",
            "no_prior_writable_mount_proven": False,
            "safe_to_erase": False,
        }
    expected = expected_uuid.upper()
    observation: dict[str, Any] = {
        "schema": "brain-host-recovery-preflight-observation-v1",
        "observed_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "system_clock_trusted": False,
        "expected_data_volume_uuid": expected,
        "expected_volume_group_uuid": expected_group_uuid.upper(),
        "expected_container_uuid": expected_container_uuid.upper(),
        "expected_device_identifier": expected_device,
        "observed_data_volume_uuid": None,
        "volume_identity": "unknown",
        "filevault_enabled": None,
        "locked": None,
        "mounted": None,
        "container_uuid": None,
        "volume_group_uuid": None,
        "device_identifier": None,
        "data_snapshots": None,
        "matching_mount_entries": None,
        "first_action_locked_gate": "unknown",
        "no_prior_writable_mount_proven": False,
        "safe_to_erase": False,
    }
    try:
        info = _run_plist(["/usr/sbin/diskutil", "info", "-plist", expected], runner)
        actual_uuid = info.get("VolumeUUID")
        if not isinstance(actual_uuid, str):
            raise ValueError("volume_uuid_missing")
        observation["observed_data_volume_uuid"] = actual_uuid.upper()
        if actual_uuid.upper() != expected:
            observation["volume_identity"] = "mismatch"
            observation["state"] = "blocked"
            observation["reason"] = "volume_uuid_mismatch"
            return observation
        device = _device(info.get("DeviceIdentifier"))
        group_uuid = info.get("APFSVolumeGroupID")
        if not isinstance(group_uuid, str) or not UUID_RE.fullmatch(group_uuid):
            raise ValueError("invalid_volume_group_id")
        container_ref = _device(info.get("APFSContainerReference"))
        fv = info.get("FileVault")
        locked = info.get("Locked")
        mountpoint = info.get("MountPoint")
        if type(fv) is not bool or type(locked) is not bool:
            raise ValueError("volume_security_state_missing")
        if mountpoint is not None and not isinstance(mountpoint, str):
            raise ValueError("invalid_mountpoint")

        all_volumes = _run_plist(["/usr/sbin/diskutil", "apfs", "list", "-plist"], runner).get(
            "Containers"
        )
        if not isinstance(all_volumes, list):
            raise ValueError("invalid_apfs_container_list")
        container_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for container in all_volumes:
            if not isinstance(container, dict):
                raise ValueError("invalid_apfs_container")
            volumes = container.get("Volumes")
            if not isinstance(volumes, list):
                raise ValueError("invalid_apfs_volumes")
            found_volumes = [
                volume
                for volume in volumes
                if isinstance(volume, dict)
                and str(volume.get("APFSVolumeUUID", "")).upper() == expected
            ]
            if found_volumes:
                if len(found_volumes) != 1:
                    raise ValueError("data_volume_ambiguous_in_container")
                container_matches.append((container, found_volumes[0]))
        if len(container_matches) != 1:
            raise ValueError("data_volume_container_ambiguous")
        container, listed_data = container_matches[0]
        container_uuid = container.get("APFSContainerUUID")
        listed_ref = _device(container.get("ContainerReference"))
        if (
            not isinstance(container_uuid, str)
            or not UUID_RE.fullmatch(container_uuid)
            or listed_ref != container_ref
        ):
            raise ValueError("data_container_identity_mismatch")
        listed_device = _device(listed_data.get("DeviceIdentifier"))
        listed_roles = listed_data.get("Roles")
        if (
            listed_device != device
            or not isinstance(listed_roles, list)
            or "Data" not in listed_roles
            or listed_data.get("FileVault") is not fv
            or listed_data.get("Locked") is not locked
        ):
            raise ValueError("data_volume_listing_mismatch")

        container_members: dict[str, list[str]] = {}
        for volume in container.get("Volumes", []):
            if not isinstance(volume, dict):
                raise ValueError("invalid_apfs_volume")
            volume_device = _device(volume.get("DeviceIdentifier"))
            roles = volume.get("Roles")
            if not isinstance(roles, list) or any(not isinstance(role, str) for role in roles):
                raise ValueError("invalid_apfs_volume_roles")
            if volume_device in container_members:
                raise ValueError("duplicate_apfs_volume_device")
            container_members[volume_device] = roles

        group_members: list[dict[str, str]] = []
        groups_payload = _run_plist(
            ["/usr/sbin/diskutil", "apfs", "listVolumeGroups", "-plist", container_ref],
            runner,
        ).get("Containers")
        if not isinstance(groups_payload, list):
            raise ValueError("invalid_volume_group_list")
        groups: list[dict[str, Any]] = []
        for group_container in groups_payload:
            if not isinstance(group_container, dict):
                raise ValueError("invalid_group_container")
            if str(group_container.get("APFSContainerUUID", "")).upper() != container_uuid.upper():
                continue
            raw_groups = group_container.get("VolumeGroups")
            if not isinstance(raw_groups, list):
                raise ValueError("invalid_volume_groups")
            groups.extend(
                group
                for group in raw_groups
                if isinstance(group, dict)
                and str(group.get("APFSVolumeGroupUUID", "")).upper() == group_uuid.upper()
            )
        if len(groups) != 1:
            raise ValueError("data_volume_group_ambiguous")
        raw_members = groups[0].get("Volumes")
        if not isinstance(raw_members, list):
            raise ValueError("invalid_volume_group_members")
        for member in raw_members:
            if not isinstance(member, dict):
                raise ValueError("invalid_volume_group_member")
            roles = member.get("Roles")
            if roles is None and isinstance(member.get("Role"), str):
                roles = [member["Role"]]
            member_device = _device(member.get("DeviceIdentifier"))
            if not isinstance(roles, list) or any(not isinstance(role, str) for role in roles):
                raise ValueError("invalid_volume_group_roles")
            listed_member_roles = container_members.get(member_device)
            if listed_member_roles is None or sorted(listed_member_roles) != sorted(roles):
                raise ValueError("volume_group_role_listing_mismatch")
            group_members.append(
                {"device_identifier": member_device, "roles": ",".join(sorted(roles))}
            )
        if sum("Data" in row["roles"].split(",") for row in group_members) != 1 or not any(
            row["device_identifier"] == device and "Data" in row["roles"].split(",")
            for row in group_members
        ):
            raise ValueError("data_volume_group_membership_mismatch")

        observation.update(
            {
                "container_uuid": container_uuid.upper(),
                "volume_group_uuid": group_uuid.upper(),
                "device_identifier": device,
            }
        )
        if (
            container_uuid.upper() != expected_container_uuid.upper()
            or group_uuid.upper() != expected_group_uuid.upper()
            or device != expected_device
        ):
            observation.update(
                {
                    "volume_identity": "mismatch",
                    "state": "blocked",
                    "reason": "volume_identity_pin_mismatch",
                }
            )
            return observation

        snapshots = _snapshots(device, runner)
        listed_mountpoint = listed_data.get("MountPoint")
        if "MountPoint" in listed_data:
            if listed_mountpoint is not None and not isinstance(listed_mountpoint, str):
                raise ValueError("invalid_listed_mountpoint")
            if listed_mountpoint != mountpoint:
                raise ValueError("data_mountpoint_listing_mismatch")
        mounts = _matching_mounts(
            expected,
            group_uuid,
            container_ref,
            container_members,
            runner,
        )
        mounted = mountpoint is not None or listed_mountpoint is not None or bool(mounts)
        observation.update(
            {
                "volume_identity": "matched",
                "filevault_enabled": fv,
                "locked": locked,
                "mounted": mounted,
                "container_uuid": container_uuid.upper(),
                "volume_group_uuid": group_uuid.upper() if group_uuid else None,
                "device_identifier": device,
                "group_members": sorted(group_members, key=lambda row: row["device_identifier"]),
                "data_snapshots": snapshots,
                "matching_mount_entries": mounts,
            }
        )
        if not fv:
            gate = "blocked_unencrypted"
        elif not locked:
            gate = "blocked_unlocked"
        elif mounts:
            gate = "blocked_alias_mounted"
        elif mounted:
            gate = "blocked_mounted"
        else:
            gate = "point_in_time_locked_and_unmounted"
        observation.update(
            {
                "first_action_locked_gate": gate,
                "state": "observed",
                "history_boundary": "not_assessed",
                "limitation": (
                    "Point-in-time state only; earlier unlock/mount/relock and incomplete boot "
                    "logs are not excluded."
                ),
            }
        )
        return observation
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        observation.update(
            {
                "state": "unknown",
                "reason": str(exc) if isinstance(exc, ValueError) else "probe_failed",
                "first_action_locked_gate": "unknown",
                "history_boundary": "not_assessed",
            }
        )
        return observation


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-data-uuid", required=True)
    parser.add_argument("--expected-volume-group-uuid", required=True)
    parser.add_argument("--expected-container-uuid", required=True)
    parser.add_argument("--expected-device-identifier", required=True)
    args = parser.parse_args(argv)
    try:
        result = observe_data_volume(
            args.expected_data_uuid,
            expected_group_uuid=args.expected_volume_group_uuid,
            expected_container_uuid=args.expected_container_uuid,
            expected_device_identifier=args.expected_device_identifier,
        )
    except ValueError as exc:
        result = {
            "schema": "brain-host-recovery-preflight-observation-v1",
            "state": "invalid_request",
            "reason": str(exc),
            "no_prior_writable_mount_proven": False,
            "safe_to_erase": False,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return (
        0 if result.get("first_action_locked_gate") == "point_in_time_locked_and_unmounted" else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
