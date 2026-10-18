"""Read-only checks for host facilities needed by Brain's Linux runtime."""

from __future__ import annotations

import os
import pwd
import stat
import subprocess
from pathlib import Path
from typing import Any

DEVICE = Path("/dev/null")
RUNUSER = "/usr/sbin/runuser"
PYTHON = "/usr/bin/python3"


class HostPrerequisiteError(RuntimeError):
    """A required Linux host capability is absent or cannot be proven."""


def _validation_errors(
    *,
    is_character: bool,
    major: int | None,
    minor: int | None,
    uid: int,
    gid: int,
    mode: int,
    service_uid: int | None,
    service_write_open: bool,
) -> list[str]:
    errors: list[str] = []
    if service_uid is None:
        errors.append("service_identity_missing")
    if not is_character:
        errors.append("null_device_not_character_device")
    elif (major, minor) != (1, 3):
        errors.append("null_device_identity_mismatch")
    if uid != 0 or gid != 0 or mode != 0o666:
        errors.append("null_device_owner_or_mode_mismatch")
    if not errors and not service_write_open:
        errors.append("service_null_device_write_denied")
    return errors


def _service_open_write(path: Path, username: str) -> bool:
    account = pwd.getpwnam(username)
    probe = (
        "import os,sys; "
        "fd=os.open(sys.argv[1], os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW); "
        "os.close(fd)"
    )
    try:
        result = subprocess.run(
            [RUNUSER, "-u", username, "--", PYTHON, "-I", "-c", probe, str(path)],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"},
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and account.pw_uid > 0


def inspect_null_device(username: str = "brain") -> dict[str, Any]:
    """Return bounded facts and validation errors without changing the device."""
    try:
        info = DEVICE.lstat()
    except OSError:
        return {"path": str(DEVICE), "valid": False, "errors": ["null_device_unavailable"]}

    mode = stat.S_IMODE(info.st_mode)
    is_character = stat.S_ISCHR(info.st_mode)
    major = os.major(info.st_rdev) if is_character else None
    minor = os.minor(info.st_rdev) if is_character else None
    try:
        account = pwd.getpwnam(username)
        service_uid = account.pw_uid
        service_gid = account.pw_gid
    except KeyError:
        service_uid = None
        service_gid = None
    service_write_open = False
    # Avoid running an access probe until the path is already the expected node.
    preliminary_errors = _validation_errors(
        is_character=is_character,
        major=major,
        minor=minor,
        uid=info.st_uid,
        gid=info.st_gid,
        mode=mode,
        service_uid=service_uid,
        service_write_open=True,
    )
    if not preliminary_errors:
        service_write_open = _service_open_write(DEVICE, username)
    errors = _validation_errors(
        is_character=is_character,
        major=major,
        minor=minor,
        uid=info.st_uid,
        gid=info.st_gid,
        mode=mode,
        service_uid=service_uid,
        service_write_open=service_write_open,
    )

    return {
        "path": str(DEVICE),
        "file_type": "character" if is_character else "other",
        "rdev_major": major,
        "rdev_minor": minor,
        "uid": info.st_uid,
        "gid": info.st_gid,
        "mode": mode,
        "inode": info.st_ino,
        "st_dev_major": os.major(info.st_dev),
        "st_dev_minor": os.minor(info.st_dev),
        "service_username": username,
        "service_uid": service_uid,
        "service_gid": service_gid,
        "service_write_open": service_write_open,
        "valid": not errors,
        "errors": errors,
    }


def require_null_device(username: str = "brain") -> dict[str, Any]:
    """Require the conventional root-owned Linux null device and service-user access."""
    facts = inspect_null_device(username)
    if not facts["valid"]:
        raise HostPrerequisiteError(",".join(facts["errors"]))
    return facts


def null_device_contract(facts: dict[str, Any]) -> dict[str, Any]:
    """Return stable device semantics for a content-bound host plan."""
    return {
        key: facts[key]
        for key in (
            "path",
            "file_type",
            "rdev_major",
            "rdev_minor",
            "uid",
            "gid",
            "mode",
            "service_username",
            "service_uid",
            "service_gid",
            "service_write_open",
        )
    }
