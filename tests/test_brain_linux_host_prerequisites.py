"""Linux host-device prerequisite contract tests."""

import pytest

from memecoin_operator import linux_host_prerequisites as host


@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        ({"is_character": False}, "null_device_not_character_device"),
        ({"major": 0}, "null_device_identity_mismatch"),
        ({"minor": 4}, "null_device_identity_mismatch"),
        ({"uid": 1}, "null_device_owner_or_mode_mismatch"),
        ({"gid": 1}, "null_device_owner_or_mode_mismatch"),
        ({"mode": 0o644}, "null_device_owner_or_mode_mismatch"),
        ({"service_uid": None}, "service_identity_missing"),
        ({"service_write_open": False}, "service_null_device_write_denied"),
    ],
)
def test_null_device_contract_refuses_invalid_observations(facts, reason):
    values = {
        "is_character": True,
        "major": 1,
        "minor": 3,
        "uid": 0,
        "gid": 0,
        "mode": 0o666,
        "service_uid": 999,
        "service_write_open": True,
    }
    values.update(facts)
    assert reason in host._validation_errors(
        is_character=values["is_character"],
        major=values["major"],
        minor=values["minor"],
        uid=values["uid"],
        gid=values["gid"],
        mode=values["mode"],
        service_uid=values["service_uid"],
        service_write_open=values["service_write_open"],
    )


def test_null_device_contract_accepts_expected_device_and_ignores_inode():
    expected = {
        "path": "/dev/null",
        "file_type": "character",
        "rdev_major": 1,
        "rdev_minor": 3,
        "uid": 0,
        "gid": 0,
        "mode": 0o666,
        "inode": 10,
        "st_dev_major": 0,
        "st_dev_minor": 5,
        "service_username": "brain",
        "service_uid": 999,
        "service_gid": 988,
        "service_write_open": True,
        "valid": True,
        "errors": [],
    }
    rebuilt = {**expected, "inode": 44}
    assert host.null_device_contract(expected) == host.null_device_contract(rebuilt)


def test_required_null_device_refuses_preexisting_drift(monkeypatch):
    monkeypatch.setattr(
        host,
        "inspect_null_device",
        lambda _username="brain": {
            "valid": False,
            "errors": ["null_device_owner_or_mode_mismatch"],
        },
    )
    with pytest.raises(host.HostPrerequisiteError, match="null_device_owner_or_mode_mismatch"):
        host.require_null_device()
