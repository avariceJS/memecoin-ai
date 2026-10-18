"""Create and verify inert, content-addressed Linux release candidates."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pwd
import re
import secrets
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

from memecoin_operator import linux_service

RELEASE_BASE = Path("/opt/brain/releases")
UNIT_BASE = Path("/etc/systemd/system")
STATE_BASE = Path("/var/lib/brain-lab/releases")
STAGE_ATTEMPT_BASE = Path("/var/lib/brain-release-stage-attempts")
STAGE_LOCK_BASE = Path("/run/lock")
SCHEMA = "brain-linux-release-v1"
STAGE_ATTEMPT_SCHEMA = "brain-linux-release-stage-attempt-v1"
STAGE_ATTEMPT_ID_RE = re.compile(r"^[0-9a-f]{12}-[0-9a-f]{16}$")
MAX_WHEEL_BYTES = 100 * 1024 * 1024
SAFE_WHEEL_NAME = re.compile(
    r"^[A-Za-z0-9_]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+\.whl$"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(argv: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        check=True,
        capture_output=capture,
        text=True,
        timeout=120,
        env={
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": "/root",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_NO_INPUT": "1",
            "PIP_CONFIG_FILE": "/dev/null",
        },
    )


def _stage_slice_name(attempt_id: str) -> str:
    if not STAGE_ATTEMPT_ID_RE.fullmatch(attempt_id):
        raise ValueError("stage_attempt_id_invalid")
    return f"brain-release-stage-{attempt_id}.slice"


def _stage_attempt_path(attempt_id: str) -> Path:
    if not STAGE_ATTEMPT_ID_RE.fullmatch(attempt_id):
        raise ValueError("stage_attempt_id_invalid")
    return STAGE_ATTEMPT_BASE / f"{attempt_id}.json"


def _open_stage_attempt_directory() -> int:
    parent_fd = os.open("/var/lib", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != 0
            or stat.S_IMODE(parent.st_mode) & 0o022
        ):
            raise RuntimeError("stage_attempt_parent_unsafe")
        try:
            os.mkdir(STAGE_ATTEMPT_BASE.name, mode=0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        directory_fd = os.open(
            STAGE_ATTEMPT_BASE.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    finally:
        os.close(parent_fd)
    metadata = os.fstat(directory_fd)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        os.close(directory_fd)
        raise RuntimeError("stage_attempt_directory_unsafe")
    return directory_fd


def _write_stage_attempt(path: Path, record: dict[str, object], *, create: bool = False) -> None:
    directory_fd = _open_stage_attempt_directory()
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    try:
        if create:
            fd = os.open(
                path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            try:
                view = memoryview(payload)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("stage_attempt_write_failed")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
        else:
            temporary = f".{path.name}.{secrets.token_hex(8)}.tmp"
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            try:
                view = memoryview(payload)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("stage_attempt_write_failed")
                    view = view[written:]
                os.fsync(fd)
                os.replace(
                    temporary,
                    path.name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
            finally:
                os.close(fd)
                try:
                    os.unlink(temporary, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _read_stage_attempt(attempt_id: str) -> tuple[Path, dict[str, object]]:
    path = _stage_attempt_path(attempt_id)
    directory_fd = _open_stage_attempt_directory()
    fd = -1
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) not in {0o600, 0o444}
            or metadata.st_size > 16 * 1024
        ):
            raise RuntimeError("stage_attempt_record_unsafe")
        payload = bytearray()
        while len(payload) <= 16 * 1024:
            block = os.read(fd, 4096)
            if not block:
                break
            payload.extend(block)
        if len(payload) > 16 * 1024:
            raise RuntimeError("stage_attempt_record_unsafe")
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("stage_attempt_record_unavailable") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(directory_fd)
    expected = {
        "schema",
        "attempt_id",
        "release_id",
        "wheel_sha256",
        "source_commit",
        "interpreter",
        "builder_name",
        "builder_uid",
        "staging_name",
        "staging_dev",
        "staging_ino",
        "slice_name",
        "phase",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value.get("schema") != STAGE_ATTEMPT_SCHEMA
        or value.get("attempt_id") != attempt_id
        or not isinstance(value.get("release_id"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["release_id"])
        or not isinstance(value.get("wheel_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["wheel_sha256"])
        or value.get("staging_name") != f".stage-{attempt_id}"
        or value.get("slice_name") != _stage_slice_name(attempt_id)
        or value.get("phase")
        not in {
            "intent",
            "builder-created",
            "building",
            "publishing",
            "complete",
            "recovery-required",
            "recovered",
        }
        or type(value.get("staging_dev")) is not int
        or type(value.get("staging_ino")) is not int
        or (value.get("builder_uid") is not None and type(value.get("builder_uid")) is not int)
        or not isinstance(value.get("builder_name"), str)
        or not isinstance(value.get("source_commit"), str)
        or not isinstance(value.get("interpreter"), dict)
    ):
        raise RuntimeError("stage_attempt_record_invalid")
    return path, value


def _acquire_stage_lock(release_id: str) -> int:
    path = STAGE_LOCK_BASE / f"brain-release-stage-{release_id}.lock"
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    metadata = os.fstat(fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        os.close(fd)
        raise RuntimeError("stage_lock_file_unsafe")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise RuntimeError("stage_already_in_progress") from exc
    return fd


def _uid_has_processes(uid: int) -> bool:
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            status = (entry / "status").read_text(encoding="ascii")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        uid_line = next((line for line in status.splitlines() if line.startswith("Uid:")), "")
        fields = uid_line.split()
        if len(fields) >= 5 and str(uid) in fields[1:5]:
            return True
    return False


def _stage_slice_cgroup(slice_name: str) -> Path | None:
    if not re.fullmatch(r"brain-release-stage-[0-9a-f]{12}-[0-9a-f]{16}\.slice", slice_name):
        raise RuntimeError("stage_slice_name_invalid")
    result = subprocess.run(
        ["systemctl", "show", "--property=ControlGroup", "--value", slice_name],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root"},
    )
    if result.returncode != 0:
        if result.returncode == 1 and any(
            phrase in result.stderr.lower()
            for phrase in ("not found", "could not be found", "not loaded")
        ):
            return None
        raise RuntimeError("stage_cgroup_status_unavailable")
    value = result.stdout.strip()
    if not value:
        return None
    parts = Path(value).parts
    if not value.startswith("/") or ".." in parts:
        raise RuntimeError("stage_cgroup_path_invalid")
    return Path("/sys/fs/cgroup").joinpath(*parts[1:])


def _require_stage_slice_empty(slice_name: str) -> None:
    cgroup = _stage_slice_cgroup(slice_name)
    if cgroup is None:
        return
    if not cgroup.exists():
        return
    try:
        events = (cgroup / "cgroup.events").read_text(encoding="ascii").splitlines()
        processes = (cgroup / "cgroup.procs").read_text(encoding="ascii").splitlines()
    except OSError as exc:
        raise RuntimeError("stage_cgroup_status_unavailable") from exc
    populated = next((line for line in events if line.startswith("populated ")), "")
    if populated != "populated 0" or processes:
        raise RuntimeError("stage_builder_processes_remaining")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _record_stage_phase(
    path: Path, record: dict[str, object], phase: str, *, seal: bool = False
) -> None:
    record["phase"] = phase
    _write_stage_attempt(path, record)
    if seal:
        directory_fd = _open_stage_attempt_directory()
        try:
            os.chmod(path.name, 0o444, dir_fd=directory_fd, follow_symlinks=False)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _release_stage_artifacts(release_id: str) -> list[Path]:
    artifacts = [RELEASE_BASE / release_id, UNIT_BASE / f"memecoin-brain-{release_id}.service"]
    pin = RELEASE_BASE / ".pins" / f"{release_id}.sha256"
    state = STATE_BASE / release_id
    artifacts.extend([pin, state])
    return [path for path in artifacts if os.path.lexists(path)]


def _recover_stage_locked(attempt_id: str) -> dict[str, str]:
    record_path, record = _read_stage_attempt(attempt_id)
    if record["phase"] in {"complete", "recovered"}:
        raise RuntimeError("stage_attempt_not_recoverable")
    release_id = str(record["release_id"])
    if _release_stage_artifacts(release_id):
        raise RuntimeError("stage_partial_publication_requires_manual_review")
    slice_name = str(record["slice_name"])
    _stop_stage_slice(slice_name)
    builder_name = str(record["builder_name"])
    uid_value = record["builder_uid"]
    try:
        builder = pwd.getpwnam(builder_name)
    except KeyError:
        builder = None
    if builder is not None:
        if uid_value is not None and builder.pw_uid != uid_value:
            raise RuntimeError("stage_builder_identity_mismatch")
        uid_value = builder.pw_uid
        if builder.pw_dir != "/nonexistent" or builder.pw_shell != "/usr/sbin/nologin":
            raise RuntimeError("stage_builder_identity_mismatch")
        if _uid_has_processes(uid_value):
            raise RuntimeError("stage_builder_processes_remaining")
    elif uid_value is not None and _uid_has_processes(uid_value):
        raise RuntimeError("stage_builder_processes_remaining")
    staging = RELEASE_BASE / str(record["staging_name"])
    incomplete = RELEASE_BASE / f".incomplete-{attempt_id}"
    try:
        metadata = staging.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None and (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_dev != record["staging_dev"]
        or metadata.st_ino != record["staging_ino"]
        or metadata.st_uid != 0
    ):
        raise RuntimeError("stage_staging_identity_mismatch")
    try:
        quarantined_metadata = incomplete.lstat()
    except FileNotFoundError:
        quarantined_metadata = None
    if metadata is None and quarantined_metadata is None:
        raise RuntimeError("stage_staging_evidence_missing")
    if quarantined_metadata is not None and (
        metadata is not None
        or not stat.S_ISDIR(quarantined_metadata.st_mode)
        or quarantined_metadata.st_dev != record["staging_dev"]
        or quarantined_metadata.st_ino != record["staging_ino"]
        or quarantined_metadata.st_uid != 0
    ):
        raise RuntimeError("stage_incomplete_destination_mismatch")
    if builder is not None:
        result = subprocess.run(
            ["userdel", builder_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root"},
        )
        if result.returncode != 0:
            raise RuntimeError("stage_builder_account_removal_failed")
    if metadata is not None:
        os.replace(staging, incomplete)
    if metadata is not None or quarantined_metadata is not None:
        marker = incomplete / "INCOMPLETE"
        expected_marker = "release staging interrupted; quarantined for evidence\n"
        if os.path.lexists(marker):
            if (
                marker.is_symlink()
                or not marker.is_file()
                or marker.read_text(encoding="utf-8") != expected_marker
            ):
                raise RuntimeError("stage_incomplete_marker_mismatch")
        else:
            marker.write_text(expected_marker, encoding="utf-8")
        marker.chmod(0o444)
        _seal(incomplete, pwd.getpwnam("brain").pw_gid)
    _record_stage_phase(record_path, record, "recovered", seal=True)
    return {"attempt_id": attempt_id, "recovered": "true"}


def recover_stage(attempt_id: str) -> dict[str, str]:
    if os.geteuid() != 0:
        raise PermissionError("stage_recovery_requires_root")
    _, record = _read_stage_attempt(attempt_id)
    lock_fd = _acquire_stage_lock(str(record["release_id"]))
    try:
        return _recover_stage_locked(attempt_id)
    finally:
        os.close(lock_fd)


def _stop_stage_slice(slice_name: str) -> None:
    if not re.fullmatch(r"brain-release-stage-[0-9a-f]{12}-[0-9a-f]{16}\.slice", slice_name):
        raise RuntimeError("stage_slice_name_invalid")
    result = subprocess.run(
        ["systemctl", "stop", slice_name],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        _stage_slice_cgroup(slice_name)
        if _stage_slice_cgroup(slice_name) is not None:
            raise RuntimeError("stage_builder_slice_stop_failed")
    _require_stage_slice_empty(slice_name)


def _run_builder(
    argv: list[str], *, attempt_id: str, phase: str, timeout: int = 120
) -> subprocess.CompletedProcess:
    if not re.fullmatch(r"[a-z0-9-]{1,24}", phase):
        raise ValueError("stage_builder_phase_invalid")
    slice_name = _stage_slice_name(attempt_id)
    unit = f"brain-release-stage-{attempt_id}-{phase}.scope"
    env = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/root",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_NO_INPUT": "1",
        "PIP_CONFIG_FILE": "/dev/null",
    }
    try:
        result = subprocess.run(
            [
                "systemd-run",
                "--scope",
                "--quiet",
                f"--slice={slice_name}",
                f"--unit={unit}",
                "--property=KillMode=control-group",
                "--",
                *argv,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("stage_builder_command_failed") from exc
    if result.returncode != 0:
        raise RuntimeError("stage_builder_command_failed")
    _require_stage_slice_empty(slice_name)
    return result


def _interpreter(python: Path) -> dict[str, str]:
    resolved = python.resolve(strict=True)
    code = (
        "import json,sys,sysconfig;"
        "print(json.dumps({'version':sys.version.split()[0],"
        "'soabi':sysconfig.get_config_var('SOABI') or ''}))"
    )
    result = _run([str(resolved), "-I", "-c", code])
    try:
        details = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("interpreter_probe_malformed") from exc
    if not isinstance(details, dict) or not details.get("version"):
        raise RuntimeError("interpreter_probe_malformed")
    return {"path": str(resolved), "version": details["version"], "soabi": details["soabi"]}


def _identity(wheel_sha: str, interpreter: dict[str, str]) -> str:
    body = json.dumps(
        {"schema": SCHEMA, "wheel_sha256": wheel_sha, "interpreter": interpreter},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(body).hexdigest()


def _inventory(root: Path) -> tuple[list[dict[str, str]], str]:
    entries: list[dict[str, str]] = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        base = Path(parent)
        for name in [*dirs, *files]:
            path = base / name
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                item = {"path": relative, "kind": "symlink", "target": os.readlink(path)}
            elif stat.S_ISDIR(metadata.st_mode):
                item = {"path": relative, "kind": "directory"}
            elif stat.S_ISREG(metadata.st_mode):
                item = {"path": relative, "kind": "file", "sha256": _sha256(path)}
            else:
                raise RuntimeError("release_contains_special_file")
            entries.append(item)
    entries.sort(key=lambda item: item["path"])
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return entries, hashlib.sha256(encoded).hexdigest()


def _seal(root: Path, group_id: int) -> None:
    for parent, dirs, files in os.walk(root, topdown=False, followlinks=False):
        for name in files:
            path = Path(parent) / name
            if path.is_symlink():
                os.chown(path, 0, group_id, follow_symlinks=False)
                continue
            mode = path.stat().st_mode
            path.chmod(0o555 if mode & 0o111 else 0o444)
            os.chown(path, 0, group_id)
        for name in dirs:
            path = Path(parent) / name
            if path.is_symlink():
                os.chown(path, 0, group_id, follow_symlinks=False)
            else:
                path.chmod(0o555)
                os.chown(path, 0, group_id)
    root.chmod(0o555)
    os.chown(root, 0, group_id)


def _verify_permissions(root: Path, unit_path: Path) -> None:
    group_id = pwd.getpwnam("brain").pw_gid
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in [".", *dirs, *files]:
            path = Path(parent) if name == "." else Path(parent) / name
            metadata = path.lstat()
            if metadata.st_uid != 0 or metadata.st_gid != group_id:
                raise RuntimeError("release_ownership_mismatch")
            if not stat.S_ISLNK(metadata.st_mode) and metadata.st_mode & 0o022:
                raise RuntimeError("release_writable_by_service_group")
    unit_stat = unit_path.stat()
    if unit_stat.st_uid != 0 or unit_stat.st_mode & 0o022:
        raise RuntimeError("release_unit_permissions_mismatch")


def _unit_properties(unit: str) -> dict[str, str]:
    names = (
        "FragmentPath",
        "DropInPaths",
        "NeedDaemonReload",
        "ExecStart",
        "User",
        "Group",
        "PrivateNetwork",
        "WorkingDirectory",
        "ReadWritePaths",
        "StateDirectory",
        "ProtectSystem",
        "Restart",
    )
    result = _run(
        ["systemctl", "show", "--no-pager", *[f"--property={name}" for name in names], unit]
    )
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in names or key in fields:
            raise RuntimeError("release_effective_unit_malformed")
        fields[key] = value
    if set(fields) != set(names):
        raise RuntimeError("release_effective_unit_malformed")
    return fields


def _verify_effective_unit(unit: str, root: Path, state_root: Path) -> None:
    fields = _unit_properties(unit)
    expected = {
        "FragmentPath": str(UNIT_BASE / unit),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "User": "brain",
        "Group": "brain",
        "PrivateNetwork": "yes",
        "WorkingDirectory": str(root),
        "ReadWritePaths": str(state_root),
        "StateDirectory": "brain-lab",
        "ProtectSystem": "strict",
        "Restart": "no",
    }
    if any(fields[key] != value for key, value in expected.items()):
        raise RuntimeError("release_effective_unit_mismatch")
    match = re.search(r"(?:^| )argv\[\]=(.*?) ; ignore_errors=", fields["ExecStart"])
    if match is None:
        raise RuntimeError("release_effective_execstart_malformed")
    try:
        argv = shlex.split(match.group(1))
    except ValueError as exc:
        raise RuntimeError("release_effective_execstart_malformed") from exc
    expected_argv = [
        str(root / "venv/bin/python"),
        "-B",
        "-I",
        "-m",
        "memecoin_operator.runtime",
        "--root",
        str(state_root),
        "--port",
        "18792",
    ]
    if argv != expected_argv:
        raise RuntimeError("release_effective_execstart_mismatch")


def _verify_lab_state(root: Path, release_id: str) -> None:
    state_root = STATE_BASE / release_id / "operator"
    try:
        state_root.relative_to(STATE_BASE)
    except ValueError as exc:
        raise RuntimeError("release_state_root_mismatch") from exc
    if not os.path.lexists(state_root):
        return
    if state_root.is_symlink() or not state_root.is_dir():
        raise RuntimeError("release_lab_state_missing")
    if not any(state_root.iterdir()):
        return
    try:
        config = json.loads((state_root / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("release_lab_config_invalid") from exc
    if (
        not isinstance(config, dict)
        or config.get("mode") != "operator"
        or config.get("real_execution_enabled") is not False
        or config.get("paid_budget_usd") != 0
    ):
        raise RuntimeError("release_lab_config_unsafe")
    database = state_root / "operator.sqlite"
    if not os.path.lexists(database) and {path.name for path in state_root.iterdir()} == {
        "config.json"
    }:
        return
    if database.is_symlink() or not database.is_file():
        raise RuntimeError("release_lab_database_missing")
    try:
        db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "jobs" not in tables or db.execute("SELECT count(*) FROM jobs").fetchone()[0] != 0:
                raise RuntimeError("release_lab_state_not_idle")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("release_lab_database_invalid")
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise RuntimeError("release_lab_database_invalid") from exc


def _commit_and_clean(source: Path, expected: str) -> None:
    brain_uid = pwd.getpwnam("brain").pw_uid
    if source.stat().st_uid != brain_uid:
        raise RuntimeError("source_owner_mismatch")
    git_prefix = ["/usr/sbin/runuser", "-u", "brain", "--", "git", "-C", str(source)]
    result = _run([*git_prefix, "rev-parse", "HEAD"])
    if result.stdout.strip() != expected:
        raise RuntimeError("source_commit_mismatch")
    dirty = _run([*git_prefix, "status", "--porcelain", "--untracked-files=all"])
    if dirty.stdout:
        raise RuntimeError("source_checkout_dirty")


def stage(
    source: Path,
    wheel: Path,
    source_commit: str,
    expected_wheel_sha256: str,
    python: Path = Path("/usr/bin/python3"),
) -> dict[str, str]:
    """Build a root-owned, service-read-only venv and unique disabled unit."""
    if os.geteuid() != 0:
        raise PermissionError("stage_requires_root")
    if not re.fullmatch(r"[0-9a-f]{40,64}", source_commit):
        raise ValueError("invalid_source_commit")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_wheel_sha256):
        raise ValueError("invalid_wheel_sha256")
    source = source.resolve(strict=True)
    wheel = wheel.resolve(strict=True)
    if (
        wheel.stat().st_size > MAX_WHEEL_BYTES
        or not wheel.is_file()
        or not SAFE_WHEEL_NAME.fullmatch(wheel.name)
    ):
        raise ValueError("invalid_wheel_file")
    _commit_and_clean(source, source_commit)
    if _sha256(wheel) != expected_wheel_sha256:
        raise RuntimeError("wheel_sha256_mismatch")
    interpreter = _interpreter(python)
    release_id = _identity(expected_wheel_sha256, interpreter)
    release_root = RELEASE_BASE / release_id
    unit_name = f"memecoin-brain-{release_id}.service"
    unit_path = UNIT_BASE / unit_name
    if RELEASE_BASE.is_symlink() or (RELEASE_BASE.exists() and not RELEASE_BASE.is_dir()):
        raise RuntimeError("release_root_unsafe")
    RELEASE_BASE.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(release_root) or os.path.lexists(unit_path):
        raise RuntimeError("release_already_exists")
    user = pwd.getpwnam("brain")
    lock_fd = _acquire_stage_lock(release_id)
    record_path: Path | None = None
    record: dict[str, object] | None = None
    try:
        if pwd.getpwnam(f"brain-stage-{release_id[:12]}"):
            raise RuntimeError("stage_incomplete_requires_recovery")
    except KeyError:
        pass
    if any(STAGE_ATTEMPT_BASE.glob(f"{release_id[:12]}-*.json")):
        for candidate in STAGE_ATTEMPT_BASE.glob(f"{release_id[:12]}-*.json"):
            try:
                _, previous = _read_stage_attempt(candidate.stem)
            except RuntimeError as exc:
                os.close(lock_fd)
                raise RuntimeError("stage_attempt_record_requires_manual_review") from exc
            if previous["release_id"] == release_id and previous["phase"] not in {
                "complete",
                "recovered",
            }:
                os.close(lock_fd)
                raise RuntimeError("stage_incomplete_requires_recovery")
    if any(RELEASE_BASE.glob(f".stage-{release_id[:12]}-*")):
        os.close(lock_fd)
        raise RuntimeError("stage_unrecorded_staging_requires_manual_review")
    attempt_id = f"{release_id[:12]}-{secrets.token_hex(8)}"
    builder_name = f"bs-{attempt_id}"
    if len(builder_name) > 32:
        os.close(lock_fd)
        raise RuntimeError("stage_builder_name_invalid")
    staging = RELEASE_BASE / f".stage-{attempt_id}"
    staging.mkdir(mode=0o711)
    staging_metadata = staging.lstat()
    record_path = _stage_attempt_path(attempt_id)
    record = {
        "schema": STAGE_ATTEMPT_SCHEMA,
        "attempt_id": attempt_id,
        "release_id": release_id,
        "wheel_sha256": expected_wheel_sha256,
        "source_commit": source_commit,
        "interpreter": interpreter,
        "builder_name": builder_name,
        "builder_uid": None,
        "staging_name": staging.name,
        "staging_dev": staging_metadata.st_dev,
        "staging_ino": staging_metadata.st_ino,
        "slice_name": _stage_slice_name(attempt_id),
        "phase": "intent",
    }
    try:
        _write_stage_attempt(record_path, record, create=True)
    except BaseException:
        staging.rmdir()
        os.close(lock_fd)
        raise
    builder_root = staging / "build"
    wheel_input = staging / wheel.name
    venv = builder_root / "venv"
    try:
        builder_root.mkdir(mode=0o700)
        _run(
            [
                "useradd",
                "--system",
                "--no-create-home",
                "--home-dir",
                "/nonexistent",
                "--shell",
                "/usr/sbin/nologin",
                builder_name,
            ]
        )
        builder = pwd.getpwnam(builder_name)
        record["builder_uid"] = builder.pw_uid
        _record_stage_phase(record_path, record, "builder-created")
        os.chown(builder_root, builder.pw_uid, builder.pw_gid)
        shutil.copyfile(wheel, wheel_input)
        wheel_input.chmod(0o444)
        os.chown(wheel_input, 0, 0)
        if _sha256(wheel_input) != expected_wheel_sha256:
            raise RuntimeError("staged_wheel_sha256_mismatch")
        _record_stage_phase(record_path, record, "building")
        _run_builder(
            [
                "/usr/sbin/runuser",
                "-u",
                builder_name,
                "--",
                interpreter["path"],
                "-m",
                "venv",
                str(venv),
            ],
            attempt_id=attempt_id,
            phase="venv",
        )
        _run_builder(
            [
                "/usr/sbin/runuser",
                "-u",
                builder_name,
                "--",
                str(venv / "bin/python"),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--no-compile",
                "--no-cache-dir",
                str(wheel_input),
            ],
            attempt_id=attempt_id,
            phase="pip",
        )
        if _sha256(wheel_input) != expected_wheel_sha256:
            raise RuntimeError("staged_wheel_changed_during_install")
        probe = _run_builder(
            [
                "/usr/sbin/runuser",
                "-u",
                builder_name,
                "--",
                str(venv / "bin/python"),
                "-B",
                "-I",
                "-c",
                "import importlib.metadata,memecoin_operator.runtime;"
                "print(importlib.metadata.version('memecoin-ai'))",
            ],
            attempt_id=attempt_id,
            phase="probe",
        )
        version = probe.stdout.strip()
        if not version:
            raise RuntimeError("release_import_probe_failed")
        wheel_input.unlink()
        inventory, inventory_sha = _inventory(venv)
        _require_stage_slice_empty(str(record["slice_name"]))
        if _uid_has_processes(builder.pw_uid):
            raise RuntimeError("stage_builder_processes_remaining")
        result = subprocess.run(
            ["userdel", builder_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root"},
        )
        if result.returncode != 0:
            raise RuntimeError("stage_builder_account_removal_failed")
        _record_stage_phase(record_path, record, "publishing")
        release_root.mkdir(mode=0o755)
        os.replace(venv, release_root / "venv")
        builder_root.rmdir()
        staging.rmdir()
        unit_text = linux_service.render_unit(
            release_root / "venv/bin/python",
            STATE_BASE / release_id / "operator",
            18792,
            code_root=release_root,
            readwrite_path=STATE_BASE / release_id / "operator",
        )
        unit_text = unit_text.replace(
            "Description=Disposable Brain operator lab (manual activation only)",
            f"Description=Brain release {release_id} (manual activation only)",
        )
        manifest = {
            "schema": SCHEMA,
            "release_id": release_id,
            "source_commit": source_commit,
            "wheel_sha256": expected_wheel_sha256,
            "package_version": version,
            "interpreter": interpreter,
            "installed_inventory_sha256": inventory_sha,
            "installed_inventory": inventory,
            "unit_name": unit_name,
            "unit_sha256": hashlib.sha256(unit_text.encode()).hexdigest(),
            "install_policy": "create-only-disabled-no-activation",
        }
        (release_root / "release-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        os.chown(release_root / "release-manifest.json", 0, user.pw_gid)
        (release_root / "release-manifest.json").chmod(0o444)
        _seal(release_root, user.pw_gid)
        pin_dir = RELEASE_BASE / ".pins"
        pin_dir.mkdir(mode=0o755, exist_ok=True)
        if os.path.lexists(pin_dir / f"{release_id}.sha256"):
            raise RuntimeError("release_pin_already_exists")
        manifest_sha = _sha256(release_root / "release-manifest.json")
        pin_path = pin_dir / f"{release_id}.sha256"
        with pin_path.open("x", encoding="ascii") as handle:
            handle.write(f"{manifest_sha}  release-manifest.json\n")
        pin_path.chmod(0o444)
        os.chown(pin_path, 0, 0)
        state_parent = STATE_BASE / release_id
        state_root = state_parent / "operator"
        if os.path.lexists(state_parent):
            raise RuntimeError("release_lab_state_already_exists")
        state_parent.mkdir(mode=0o700, parents=True)
        os.chown(state_parent, user.pw_uid, user.pw_gid)
        state_root.mkdir(mode=0o700)
        os.chown(state_root, user.pw_uid, user.pw_gid)
        lab_config = {
            "mode": "operator",
            "real_execution_enabled": False,
            "paid_budget_usd": 0,
            "expires_ns": 0,
        }
        config_path = state_root / "config.json"
        config_path.write_text(json.dumps(lab_config, sort_keys=True) + "\n", encoding="utf-8")
        config_path.chmod(0o600)
        os.chown(config_path, user.pw_uid, user.pw_gid)
        linux_service.install(unit_text, unit_path=unit_path, unit=unit_name)
        _record_stage_phase(record_path, record, "complete", seal=True)
    except BaseException:
        try:
            _record_stage_phase(record_path, record, "recovery-required")
        except BaseException:
            pass
        raise
    finally:
        os.close(lock_fd)
    return {"release_id": release_id, "unit": unit_name, "release_path": str(release_root)}


def verify(release_id: str) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{64}", release_id):
        raise ValueError("invalid_release_id")
    root = RELEASE_BASE / release_id
    unit_name = f"memecoin-brain-{release_id}.service"
    unit_path = UNIT_BASE / unit_name
    if root.is_symlink() or not root.is_dir() or unit_path.is_symlink() or not unit_path.is_file():
        raise RuntimeError("release_or_unit_missing")
    manifest = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or manifest.get("release_id") != release_id:
        raise RuntimeError("release_manifest_mismatch")
    wheel_sha = manifest.get("wheel_sha256")
    interpreter = manifest.get("interpreter")
    if (
        not isinstance(wheel_sha, str)
        or not re.fullmatch(r"[0-9a-f]{64}", wheel_sha)
        or not isinstance(interpreter, dict)
        or _identity(wheel_sha, interpreter) != release_id
        or manifest.get("unit_name") != unit_name
    ):
        raise RuntimeError("release_manifest_mismatch")
    pin_path = RELEASE_BASE / ".pins" / f"{release_id}.sha256"
    if pin_path.is_symlink() or not pin_path.is_file():
        raise RuntimeError("release_manifest_pin_missing")
    expected_pin = f"{_sha256(root / 'release-manifest.json')}  release-manifest.json\n"
    if pin_path.read_text(encoding="ascii") != expected_pin:
        raise RuntimeError("release_manifest_pin_mismatch")
    inventory, inventory_sha = _inventory(root / "venv")
    if inventory != manifest.get("installed_inventory") or inventory_sha != manifest.get(
        "installed_inventory_sha256"
    ):
        raise RuntimeError("release_inventory_mismatch")
    _verify_permissions(root, unit_path)
    if _interpreter(root / "venv/bin/python") != interpreter:
        raise RuntimeError("release_interpreter_mismatch")
    unit_text = unit_path.read_text(encoding="utf-8")
    if hashlib.sha256(unit_text.encode()).hexdigest() != manifest.get("unit_sha256"):
        raise RuntimeError("release_unit_mismatch")
    expected_python = str(root / "venv/bin/python")
    if f"ExecStart={expected_python} -B -I -m memecoin_operator.runtime " not in unit_text:
        raise RuntimeError("release_unit_path_mismatch")
    state = linux_service._unit_status(unit_name)
    if state != ("loaded", "inactive", "disabled", 0):
        raise RuntimeError("release_unit_not_inert")
    state_root = STATE_BASE / release_id / "operator"
    _verify_effective_unit(unit_name, root, state_root)
    _verify_lab_state(root, release_id)
    return {
        "release_id": release_id,
        "unit": unit_name,
        "wheel_sha256": wheel_sha,
        "source_commit": manifest.get("source_commit"),
        "package_version": manifest.get("package_version"),
        "installed_inventory_sha256": inventory_sha,
        "unit_sha256": manifest.get("unit_sha256"),
        "manifest_sha256": _sha256(root / "release-manifest.json"),
        "unit_state": state,
    }


def activate(release_id: str) -> None:
    verify(release_id)
    _run(["systemctl", "start", f"memecoin-brain-{release_id}.service"], capture=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("stage", help="create a disabled content-addressed release candidate")
    install.add_argument("--source", type=Path, required=True)
    install.add_argument("--wheel", type=Path, required=True)
    install.add_argument("--source-commit", required=True)
    install.add_argument("--wheel-sha256", required=True)
    install.add_argument("--python", type=Path, default=Path("/usr/bin/python3"))
    recover = sub.add_parser(
        "recover-stage", help="stop and quarantine one precisely recorded interrupted stage"
    )
    recover.add_argument("--attempt-id", required=True)
    check = sub.add_parser("verify", help="read back a release and its inert unit")
    check.add_argument("--release-id", required=True)
    start = sub.add_parser("activate", help="verify then explicitly start a release")
    start.add_argument("--release-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "stage":
            result = stage(
                args.source, args.wheel, args.source_commit, args.wheel_sha256, args.python
            )
        elif args.command == "recover-stage":
            result = recover_stage(args.attempt_id)
        elif args.command == "verify":
            result = verify(args.release_id)
        else:
            activate(args.release_id)
            result = {"release_id": args.release_id, "activated": True}
        print(json.dumps(result, sort_keys=True))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"linux_release_error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
