from __future__ import annotations

import plistlib
import subprocess
from collections.abc import Sequence

import pytest

from tools import brain_host_recovery_preflight_v1 as preflight

DATA_UUID = "430740CA-42E7-4E6C-A07A-E4E7370152CC"
GROUP_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SYSTEM_UUID = "4251D615-AD28-447A-85EA-98292ACE13E0"
CONTAINER_UUID = "414BC719-2FEA-44A6-9952-D12CD31CB0CA"
DEVICE = "disk3s5"


def _plist(payload: object) -> bytes:
    def clean(value: object) -> object:
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if item is not None}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return plistlib.dumps(clean(payload), fmt=plistlib.FMT_XML)


class ProbeRunner:
    def __init__(
        self,
        *,
        expected_uuid: str = DATA_UUID,
        filevault: bool = True,
        locked: bool = True,
        mountpoint: str | None = None,
        mount_output: str = "",
        snapshots: list[dict[str, object]] | None = None,
        fail_command: str | None = None,
        mount_alias_info: dict[str, dict[str, object]] | None = None,
        container_uuid: str = CONTAINER_UUID,
    ) -> None:
        self.expected_uuid = expected_uuid
        self.filevault = filevault
        self.locked = locked
        self.mountpoint = mountpoint
        self.mount_output = mount_output
        self.snapshots = snapshots if snapshots is not None else []
        self.fail_command = fail_command
        self.mount_alias_info = mount_alias_info or {}
        self.container_uuid = container_uuid
        self.calls: list[list[str]] = []

    def __call__(self, args: Sequence[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
        command = list(args)
        self.calls.append(command)
        if self.fail_command and self.fail_command in command:
            return subprocess.CompletedProcess(command, 1, b"", b"")
        if command[:3] == ["/usr/sbin/diskutil", "info", "-plist"]:
            if command[3] in self.mount_alias_info:
                return subprocess.CompletedProcess(
                    command, 0, _plist(self.mount_alias_info[command[3]]), b""
                )
            actual_uuid = self.expected_uuid if command[3] == DATA_UUID else command[3]
            return subprocess.CompletedProcess(
                command,
                0,
                _plist(
                    {
                        "VolumeUUID": actual_uuid,
                        "DeviceIdentifier": DEVICE,
                        "APFSVolumeGroupID": GROUP_UUID,
                        "APFSContainerReference": "disk3",
                        "FileVault": self.filevault,
                        "Locked": self.locked,
                        "MountPoint": self.mountpoint,
                    }
                ),
                b"",
            )
        if command == ["/usr/sbin/diskutil", "apfs", "list", "-plist"]:
            return subprocess.CompletedProcess(
                command,
                0,
                _plist(
                    {
                        "Containers": [
                            {
                                "APFSContainerUUID": self.container_uuid,
                                "ContainerReference": "disk3",
                                "Volumes": [
                                    {
                                        "APFSVolumeUUID": SYSTEM_UUID,
                                        "DeviceIdentifier": "disk3s1",
                                        "Roles": ["System"],
                                    },
                                    {
                                        "APFSVolumeUUID": DATA_UUID,
                                        "DeviceIdentifier": DEVICE,
                                        "Roles": ["Data"],
                                        "FileVault": self.filevault,
                                        "Locked": self.locked,
                                    },
                                ],
                            }
                        ]
                    }
                ),
                b"",
            )
        if command == [
            "/usr/sbin/diskutil",
            "apfs",
            "listVolumeGroups",
            "-plist",
            "disk3",
        ]:
            return subprocess.CompletedProcess(
                command,
                0,
                _plist(
                    {
                        "Containers": [
                            {
                                "APFSContainerUUID": self.container_uuid,
                                "VolumeGroups": [
                                    {
                                        "APFSVolumeGroupUUID": GROUP_UUID,
                                        "Volumes": [
                                            {"DeviceIdentifier": "disk3s1", "Roles": ["System"]},
                                            {"DeviceIdentifier": DEVICE, "Role": "Data"},
                                        ],
                                    }
                                ],
                            }
                        ]
                    }
                ),
                b"",
            )
        if command == [
            "/usr/sbin/diskutil",
            "apfs",
            "listSnapshots",
            "-plist",
            DEVICE,
        ]:
            return subprocess.CompletedProcess(
                command, 0, _plist({"Snapshots": self.snapshots}), b""
            )
        if command == ["/sbin/mount"]:
            return subprocess.CompletedProcess(command, 0, self.mount_output.encode("utf-8"), b"")
        raise AssertionError(f"unexpected command: {command}")


def _observe(
    runner: ProbeRunner,
    *,
    system: str = "Darwin",
    expected_container_uuid: str = CONTAINER_UUID,
) -> dict[str, object]:
    return preflight.observe_data_volume(
        DATA_UUID,
        expected_group_uuid=GROUP_UUID,
        expected_container_uuid=expected_container_uuid,
        expected_device_identifier=DEVICE,
        runner=runner,
        system=system,
    )


def test_locked_first_action_observation_is_point_in_time_only() -> None:
    runner = ProbeRunner()
    result = _observe(runner)

    assert result["state"] == "observed"
    assert result["volume_identity"] == "matched"
    assert result["expected_data_volume_uuid"] == DATA_UUID
    assert result["expected_volume_group_uuid"] == GROUP_UUID.upper()
    assert result["expected_container_uuid"] == CONTAINER_UUID
    assert result["expected_device_identifier"] == DEVICE
    assert result["observed_data_volume_uuid"] == DATA_UUID
    assert result["filevault_enabled"] is True
    assert result["locked"] is True
    assert result["mounted"] is False
    assert result["container_uuid"] == CONTAINER_UUID
    assert result["volume_group_uuid"] == GROUP_UUID.upper()
    assert result["group_members"] == [
        {"device_identifier": "disk3s1", "roles": "System"},
        {"device_identifier": DEVICE, "roles": "Data"},
    ]
    assert result["data_snapshots"] == []
    assert result["matching_mount_entries"] == []
    assert result["first_action_locked_gate"] == "point_in_time_locked_and_unmounted"
    assert result["history_boundary"] == "not_assessed"
    assert result["no_prior_writable_mount_proven"] is False
    assert result["safe_to_erase"] is False
    assert all(command[0] in {"/usr/sbin/diskutil", "/sbin/mount"} for command in runner.calls)


def test_data_snapshot_mount_alias_is_detected_without_emitting_mountpoint() -> None:
    runner = ProbeRunner(
        snapshots=[
            {
                "SnapshotUUID": "11111111-2222-3333-4444-555555555555",
                "SnapshotXID": 42,
                "SnapshotName": "private-name",
            }
        ],
        mount_output=(
            "/dev/disk3s5s1 on /System/Volumes/Data/.snapshot/private-name "
            "(apfs, local, read-only, nobrowse)\n"
        ),
    )

    result = _observe(runner)

    assert result["mounted"] is True
    assert result["first_action_locked_gate"] == "blocked_alias_mounted"
    assert result["matching_mount_entries"] == [
        {
            "device_identifier": "disk3s5s1",
            "filesystem_type": "apfs",
            "read_only_flag": True,
            "mount_flags": ["local", "nobrowse", "read-only"],
        }
    ]
    assert result["data_snapshots"] == [{"uuid": "11111111-2222-3333-4444-555555555555", "xid": 42}]
    assert "private-name" not in str(result)
    assert result["no_prior_writable_mount_proven"] is False


def test_apfs_mount_on_another_device_identifier_is_resolved_by_volume_uuid() -> None:
    runner = ProbeRunner(
        mount_output="/dev/disk99s1 on /private/var/synthetic (apfs, local, read-only)\n",
        mount_alias_info={
            "disk99s1": {
                "VolumeUUID": DATA_UUID,
                "APFSVolumeGroupID": GROUP_UUID,
                "APFSContainerReference": "disk3",
            }
        },
    )

    result = _observe(runner)

    assert result["mounted"] is True
    assert result["first_action_locked_gate"] == "blocked_alias_mounted"
    assert result["matching_mount_entries"][0]["device_identifier"] == "disk99s1"
    assert "/private/var/synthetic" not in str(result)


def test_unmapped_apfs_alias_in_the_data_container_fails_closed() -> None:
    runner = ProbeRunner(
        mount_output="/dev/disk99s1 on /private/var/synthetic (apfs, local)\n",
        mount_alias_info={
            "disk99s1": {
                "VolumeUUID": SYSTEM_UUID,
                "APFSVolumeGroupID": GROUP_UUID,
                "APFSContainerReference": "disk3",
            }
        },
    )

    result = _observe(runner)

    assert result["state"] == "unknown"
    assert result["reason"] == "mounted_apfs_group_alias_ambiguous"
    assert result["first_action_locked_gate"] == "unknown"
    assert result["safe_to_erase"] is False


def test_system_snapshot_alias_is_not_mistaken_for_data_alias() -> None:
    runner = ProbeRunner(mount_output="/dev/disk3s1s1 on /synthetic (apfs, local)\n")

    result = _observe(runner)

    assert result["mounted"] is False
    assert result["first_action_locked_gate"] == "point_in_time_locked_and_unmounted"
    assert result["matching_mount_entries"] == []


def test_unparseable_mount_entry_does_not_look_like_absence() -> None:
    runner = ProbeRunner(mount_output="/dev/disk3s5s1 unexpected format\n")

    result = _observe(runner)

    assert result["state"] == "unknown"
    assert result["reason"] == "mount_table_entry_unparseable"
    assert result["first_action_locked_gate"] == "unknown"


def test_apfs_mount_with_non_device_source_fails_closed() -> None:
    runner = ProbeRunner(mount_output="unmapped-source on /synthetic (apfs, local)\n")

    result = _observe(runner)

    assert result["state"] == "unknown"
    assert result["reason"] == "mounted_apfs_source_unresolved"
    assert result["first_action_locked_gate"] == "unknown"


def test_live_unlocked_mounted_data_is_blocked() -> None:
    runner = ProbeRunner(locked=False, mountpoint="/System/Volumes/Data")
    result = _observe(runner)
    assert result["first_action_locked_gate"] == "blocked_unlocked"
    assert result["mounted"] is True
    assert "/System/Volumes/Data" not in str(result)


def test_wrong_uuid_blocks_before_querying_other_storage_metadata() -> None:
    runner = ProbeRunner(expected_uuid="00000000-0000-0000-0000-000000000000")
    result = _observe(runner)
    assert result["state"] == "blocked"
    assert result["reason"] == "volume_uuid_mismatch"
    assert result["expected_data_volume_uuid"] == DATA_UUID
    assert result["observed_data_volume_uuid"] == "00000000-0000-0000-0000-000000000000"
    assert len(runner.calls) == 1
    assert result["safe_to_erase"] is False


def test_container_pin_mismatch_blocks_identity() -> None:
    result = _observe(ProbeRunner(), expected_container_uuid="00000000-0000-0000-0000-000000000000")

    assert result["state"] == "blocked"
    assert result["volume_identity"] == "mismatch"
    assert result["reason"] == "volume_identity_pin_mismatch"
    assert result["container_uuid"] == CONTAINER_UUID
    assert result["first_action_locked_gate"] == "unknown"
    assert result["safe_to_erase"] is False


def test_invalid_observed_container_uuid_fails_closed() -> None:
    result = _observe(ProbeRunner(container_uuid="not-a-uuid"))

    assert result["state"] == "unknown"
    assert result["reason"] == "data_container_identity_mismatch"
    assert result["first_action_locked_gate"] == "unknown"
    assert result["safe_to_erase"] is False


def test_unencrypted_volume_does_not_pass_locked_gate() -> None:
    runner = ProbeRunner(filevault=False, locked=True)
    result = _observe(runner)
    assert result["first_action_locked_gate"] == "blocked_unencrypted"
    assert result["no_prior_writable_mount_proven"] is False


def test_failed_metadata_command_is_unknown_and_non_authoritative() -> None:
    runner = ProbeRunner(fail_command="listVolumeGroups")
    result = _observe(runner)
    assert result["state"] == "unknown"
    assert result["reason"] == "command_failed"
    assert result["first_action_locked_gate"] == "unknown"
    assert result["safe_to_erase"] is False


def test_non_darwin_host_is_not_probed() -> None:
    runner = ProbeRunner()
    result = _observe(runner, system="Linux")
    assert result["state"] == "unknown"
    assert result["reason"] == "darwin_required"
    assert runner.calls == []


def test_expected_uuid_format_is_required() -> None:
    with pytest.raises(ValueError, match="expected_data_uuid_required"):
        preflight.observe_data_volume(
            "not-a-uuid",
            expected_group_uuid=GROUP_UUID,
            expected_container_uuid=CONTAINER_UUID,
            expected_device_identifier=DEVICE,
            runner=ProbeRunner(),
            system="Darwin",
        )


def test_cli_exit_code_is_nonzero_for_an_observed_but_blocked_gate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        preflight,
        "observe_data_volume",
        lambda *_args, **_kwargs: {
            "schema": "brain-host-recovery-preflight-observation-v1",
            "state": "observed",
            "first_action_locked_gate": "blocked_unlocked",
            "safe_to_erase": False,
        },
    )

    assert (
        preflight.main(
            [
                "--expected-data-uuid",
                DATA_UUID,
                "--expected-volume-group-uuid",
                GROUP_UUID,
                "--expected-container-uuid",
                CONTAINER_UUID,
                "--expected-device-identifier",
                DEVICE,
            ]
        )
        == 2
    )
    assert '"first_action_locked_gate": "blocked_unlocked"' in capsys.readouterr().out


def test_cli_zero_exit_only_marks_the_point_in_time_preflight(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        preflight,
        "observe_data_volume",
        lambda *_args, **_kwargs: {
            "schema": "brain-host-recovery-preflight-observation-v1",
            "state": "observed",
            "first_action_locked_gate": "point_in_time_locked_and_unmounted",
            "no_prior_writable_mount_proven": False,
            "safe_to_erase": False,
        },
    )

    assert (
        preflight.main(
            [
                "--expected-data-uuid",
                DATA_UUID,
                "--expected-volume-group-uuid",
                GROUP_UUID,
                "--expected-container-uuid",
                CONTAINER_UUID,
                "--expected-device-identifier",
                DEVICE,
            ]
        )
        == 0
    )
    assert '"safe_to_erase": false' in capsys.readouterr().out
