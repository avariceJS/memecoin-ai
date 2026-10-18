"""Read-only gate for the dedicated Linux Brain service identity."""

from __future__ import annotations

import grp
import hashlib
import json
import os
import pwd
import re
import shlex
import stat
import subprocess
from pathlib import Path
from typing import Any

SCHEMA = "brain-linux-service-identity-v1"
ADMIN_GROUPS = frozenset(
    {
        "admin",
        "sudo",
        "wheel",
        "root",
        "docker",
        "lxd",
        "disk",
        "shadow",
        "libvirt",
        "libvirt-qemu",
        "kvm",
        "adm",
        "systemd-journal",
    }
)
NOLOGIN_SHELLS = frozenset({"/bin/false", "/sbin/nologin", "/usr/sbin/nologin"})
PASSWD = Path("/usr/bin/passwd")
SUDO = Path("/usr/bin/sudo")
SSHD = Path("/usr/sbin/sshd")


class IdentityError(RuntimeError):
    """The service identity cannot be proven to satisfy the host contract."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"},
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IdentityError(f"identity_probe_unavailable:{Path(argv[0]).name}") from exc


def _operator_uid() -> int:
    raw = os.environ.get("SUDO_UID") if os.geteuid() == 0 else None
    if raw is None:
        return os.getuid()
    if not re.fullmatch(r"[0-9]+", raw):
        raise IdentityError("operator_uid_unavailable")
    return int(raw)


def _password_is_locked(username: str) -> bool:
    result = _run([str(PASSWD), "-S", username])
    fields = result.stdout.split()
    if (
        result.returncode
        or len(fields) < 2
        or fields[0] != username
        or fields[1] not in {"L", "P", "NP"}
    ):
        raise IdentityError("password_lock_state_unavailable")
    if fields[1] != "L":
        raise IdentityError("service_password_unlocked")
    return True


def _group_inventory(username: str, gid: int) -> list[dict[str, int | str]]:
    try:
        gids = sorted(set(os.getgrouplist(username, gid)) | {gid})
        groups = [{"gid": group_id, "name": grp.getgrgid(group_id).gr_name} for group_id in gids]
    except (OSError, KeyError, OverflowError) as exc:
        raise IdentityError("service_group_inventory_unavailable") from exc
    if any(item["name"] in ADMIN_GROUPS for item in groups):
        raise IdentityError("service_account_in_admin_group")
    return groups


def _authorized_key_paths(username: str, uid: int, home: Path) -> list[Path]:
    paths = [home / ".ssh" / "authorized_keys", home / ".ssh" / "authorized_keys2"]
    sshd = SSHD
    if sshd.exists():
        result = _run([str(sshd), "-T", "-C", f"user={username},host=localhost,addr=127.0.0.1"])
        if result.returncode:
            raise IdentityError("ssh_authorized_keys_policy_unavailable")
        settings: dict[str, list[str]] = {}
        for line in result.stdout.splitlines():
            fields = line.split(None, 1)
            if len(fields) == 2 and fields[0].lower() in {
                "authorizedkeysfile",
                "authorizedkeyscommand",
            }:
                settings.setdefault(fields[0].lower(), []).append(fields[1])
        entries = settings.get("authorizedkeysfile", [])
        commands = settings.get("authorizedkeyscommand", [])
        if len(entries) != 1 or len(commands) != 1:
            raise IdentityError("ssh_authorized_keys_policy_ambiguous")
        if commands[0].strip().lower() != "none":
            raise IdentityError("ssh_authorized_keys_command_present")
        try:
            configured = shlex.split(entries[0])
        except ValueError as exc:
            raise IdentityError("ssh_authorized_keys_policy_ambiguous") from exc
        for item in configured:
            if item == "none":
                continue
            if re.search(r"[*?\[\]]", item):
                raise IdentityError("ssh_authorized_keys_policy_ambiguous")
            expanded = item.replace("%h", str(home)).replace("%u", username).replace("%U", str(uid))
            if "%" in expanded:
                raise IdentityError("ssh_authorized_keys_policy_ambiguous")
            path = Path(expanded)
            paths.append(path if path.is_absolute() else home / path)
    unique = sorted(set(paths), key=str)
    ssh_dir = home / ".ssh"
    if os.path.lexists(ssh_dir):
        info = ssh_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
            raise IdentityError("service_ssh_directory_unsafe")
        try:
            for item in ssh_dir.iterdir():
                if item.name.startswith("authorized_keys") and item not in unique:
                    unique.append(item)
        except OSError as exc:
            raise IdentityError("service_ssh_directory_unavailable") from exc
    existing = [str(path) for path in unique if os.path.lexists(path)]
    if existing:
        raise IdentityError("service_ssh_authorized_keys_present")
    return unique


def snapshot() -> dict[str, Any]:
    """Return the canonical identity record, or refuse if any property is unknown/unsafe."""
    try:
        account = pwd.getpwnam("brain")
    except KeyError as exc:
        raise IdentityError("service_identity_missing") from exc
    operator_uid = _operator_uid()
    if account.pw_uid <= 0 or account.pw_gid <= 0:
        raise IdentityError("service_identity_root_or_invalid")
    if account.pw_uid == operator_uid:
        raise IdentityError("service_identity_shares_operator_uid")
    if account.pw_shell not in NOLOGIN_SHELLS or not os.access(account.pw_shell, os.X_OK):
        raise IdentityError("service_login_shell_refused")
    home = Path(account.pw_dir)
    if not home.is_absolute() or not os.path.lexists(home):
        raise IdentityError("service_home_unavailable")
    home_info = home.lstat()
    if not stat.S_ISDIR(home_info.st_mode) or home_info.st_uid != account.pw_uid:
        raise IdentityError("service_home_ownership_mismatch")
    groups = _group_inventory(account.pw_name, account.pw_gid)
    password_locked = _password_is_locked(account.pw_name)
    keys = _authorized_key_paths(account.pw_name, account.pw_uid, home)
    value = {
        "schema": SCHEMA,
        "username": account.pw_name,
        "uid": account.pw_uid,
        "gid": account.pw_gid,
        "groups": groups,
        "home": str(home),
        "shell": account.pw_shell,
        "password_locked": password_locked,
        "ssh_authorized_keys": [],
        "sudo_denied": True,
        "operator_uid_distinct": True,
    }
    sudo = _run([str(SUDO), "-n", "-l", "-U", account.pw_name])
    denial = re.search(r"^User\s+brain\s+is not allowed to run sudo\b", sudo.stdout, re.MULTILINE)
    # sudo -l -U <user> reports a denied user with exit status 0 on Ubuntu 24.04;
    # some sudo builds use 1 for the same negative query. The denial text is the proof.
    if sudo.returncode not in {0, 1} or not denial:
        raise IdentityError("service_sudo_denial_unproven")
    # Include the concrete, configured key locations in the digest without storing key material.
    value["checked_ssh_authorized_key_paths"] = [str(path) for path in keys]
    digest = hashlib.sha256(_canonical(value)).hexdigest()
    return {**value, "sha256": digest}


def verify_bound(expected: object) -> dict[str, Any]:
    """Validate a saved identity record and require that it still matches this host."""
    if not isinstance(expected, dict) or not isinstance(expected.get("sha256"), str):
        raise IdentityError("service_identity_binding_missing")
    payload = {key: value for key, value in expected.items() if key != "sha256"}
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    if digest != expected["sha256"] or expected.get("schema") != SCHEMA:
        raise IdentityError("service_identity_binding_invalid")
    current = snapshot()
    if current != expected:
        raise IdentityError("service_identity_drift")
    return current
