"""Create and verify inert, content-addressed Linux release candidates."""

from __future__ import annotations

import argparse
import ctypes
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
STATE_BASE_V2 = Path("/var/lib/brain-release-state")
STATE_PREPARED_BASE = Path("/var/lib/brain-release-prepared")
STAGE_ATTEMPT_BASE = Path("/var/lib/brain-release-stage-attempts")
STAGE_LOCK_BASE = Path("/run/brain-release-stage-locks")
SCHEMA = "brain-linux-release-v1"
SCHEMA_V2 = "brain-linux-release-v2"
STAGE_ATTEMPT_SCHEMA = "brain-linux-release-stage-attempt-v1"
PUBLISH_INTENT_SCHEMA = "brain-linux-release-publish-intent-v2"
DISCARD_INTENT_SCHEMA = "brain-linux-release-stage-discard-intent-v1"
DISCARD_RECEIPT_SCHEMA = "brain-linux-release-stage-discard-receipt-v1"
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


def _publish_intent_path(attempt_id: str) -> Path:
    if not STAGE_ATTEMPT_ID_RE.fullmatch(attempt_id):
        raise ValueError("stage_attempt_id_invalid")
    return STAGE_ATTEMPT_BASE / f"{attempt_id}.publish.json"


def _discard_intent_path(attempt_id: str) -> Path:
    if not STAGE_ATTEMPT_ID_RE.fullmatch(attempt_id):
        raise ValueError("stage_attempt_id_invalid")
    return STAGE_ATTEMPT_BASE / f"{attempt_id}.discard.json"


def _discard_receipt_path(attempt_id: str) -> Path:
    if not STAGE_ATTEMPT_ID_RE.fullmatch(attempt_id):
        raise ValueError("stage_attempt_id_invalid")
    return STAGE_ATTEMPT_BASE / f"{attempt_id}.discarded.json"


def _discard_quarantine_path(attempt_id: str, item: dict[str, object]) -> Path:
    return Path(str(item["source_parent"])) / f".brain-discard-{attempt_id}-{item['name']}"


def _ensure_root_directory(path: Path, mode: int) -> None:
    """Create one /var/lib child and require a root-controlled directory."""
    parent_fd = os.open("/var/lib", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(parent_fd)
        if parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) & 0o022:
            raise RuntimeError("release_state_parent_unsafe")
        try:
            os.mkdir(path.name, mode=mode, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            pass
        fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != mode
        ):
            raise RuntimeError("release_state_directory_unsafe")
    finally:
        os.close(fd)


def _ensure_unit_base() -> None:
    for path in (Path("/etc"), Path("/etc/systemd"), UNIT_BASE):
        metadata = path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise RuntimeError("release_unit_parent_unsafe")


def _tree_sha256(path: Path) -> str:
    return _inventory(path)[1]


def _rename_noreplace(
    source_parent: Path, source_name: str, destination_parent: Path, destination_name: str
) -> None:
    if any(
        name in {"", ".", ".."} or "/" in name or "\\" in name
        for name in (source_name, destination_name)
    ):
        raise RuntimeError("release_publish_name_invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    source_fd = os.open(source_parent, flags)
    destination_fd = os.open(destination_parent, flags)
    try:
        if os.fstat(source_fd).st_dev != os.fstat(destination_fd).st_dev:
            raise RuntimeError("release_publish_cross_filesystem")
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise RuntimeError("release_publish_noreplace_unsupported")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        if (
            renameat2(
                source_fd,
                os.fsencode(source_name),
                destination_fd,
                os.fsencode(destination_name),
                1,
            )
            != 0
        ):
            error = ctypes.get_errno()
            if error == 17:
                raise RuntimeError("release_publish_destination_exists")
            raise RuntimeError("release_publish_failed") from OSError(error, os.strerror(error))
        os.fsync(source_fd)
        if source_fd != destination_fd:
            os.fsync(destination_fd)
    finally:
        os.close(source_fd)
        os.close(destination_fd)


def _artifact_identity(path: Path, *, kind: str) -> dict[str, object]:
    metadata = path.lstat()
    if kind == "directory":
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("release_publish_artifact_type_mismatch")
        content_sha = _tree_sha256(path)
    elif kind == "file":
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("release_publish_artifact_type_mismatch")
        content_sha = _sha256(path)
    else:
        raise RuntimeError("release_publish_artifact_type_invalid")
    return {
        "dev": metadata.st_dev,
        "ino": metadata.st_ino,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
        "kind": kind,
        "content_sha256": content_sha,
    }


def _write_publish_intent(attempt_id: str, record: dict[str, object]) -> None:
    path = _publish_intent_path(attempt_id)
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    directory_fd = _open_stage_attempt_directory()
    temporary = f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
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
                    raise OSError("publish_intent_write_failed")
                view = view[written:]
            os.fsync(fd)
            os.fchmod(fd, 0o444)
        finally:
            os.close(fd)
        _rename_noreplace(STAGE_ATTEMPT_BASE, temporary, STAGE_ATTEMPT_BASE, path.name)
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def _read_publish_intent(attempt_id: str) -> dict[str, object] | None:
    path = _publish_intent_path(attempt_id)
    directory_fd = _open_stage_attempt_directory()
    fd = -1
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o444
            or metadata.st_size > 32 * 1024
        ):
            raise RuntimeError("release_publish_intent_unsafe")
        payload = bytearray()
        while len(payload) <= 32 * 1024:
            block = os.read(fd, 4096)
            if not block:
                break
            payload.extend(block)
        if len(payload) > 32 * 1024:
            raise RuntimeError("release_publish_intent_unsafe")
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("release_publish_intent_unavailable") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(directory_fd)
    if (
        not isinstance(value, dict)
        or value.get("schema") != PUBLISH_INTENT_SCHEMA
        or value.get("attempt_id") != attempt_id
    ):
        raise RuntimeError("release_publish_intent_invalid")
    return value


def _ensure_release_base() -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        current_fd = os.open("/opt", flags)
    except OSError as exc:
        raise RuntimeError("release_parent_unavailable") from exc
    try:
        parent = os.fstat(current_fd)
        if (
            not stat.S_ISDIR(parent.st_mode)
            or parent.st_uid != 0
            or stat.S_IMODE(parent.st_mode) & 0o022
        ):
            raise RuntimeError("release_parent_unsafe")
        for name in ("brain", "releases"):
            try:
                os.mkdir(name, mode=0o755, dir_fd=current_fd)
                os.fsync(current_fd)
            except FileExistsError:
                pass
            next_fd = os.open(name, flags, dir_fd=current_fd)
            metadata = os.fstat(next_fd)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != 0
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                os.close(next_fd)
                raise RuntimeError("release_parent_unsafe")
            os.close(current_fd)
            current_fd = next_fd
    finally:
        os.close(current_fd)


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
    return path, _validate_stage_attempt_record(attempt_id, value)


def _validate_stage_attempt_record(attempt_id: str, value: object) -> dict[str, object]:
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
        or value.get("builder_name") != f"bs-{attempt_id}"
        or attempt_id[:12] != value.get("release_id", "")[:12]
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
        or (
            value.get("builder_uid") is not None
            and (type(value.get("builder_uid")) is not int or not 1 <= value["builder_uid"] < 2**31)
        )
        or not isinstance(value.get("builder_name"), str)
        or not isinstance(value.get("source_commit"), str)
        or not re.fullmatch(r"[0-9a-f]{40,64}", value["source_commit"])
        or not isinstance(value.get("interpreter"), dict)
        or set(value["interpreter"]) != {"path", "version", "soabi"}
        or any(not isinstance(item, str) or not item for item in value["interpreter"].values())
        or not Path(value["interpreter"]["path"]).is_absolute()
        or value["release_id"]
        not in {
            _identity(value["wheel_sha256"], value["interpreter"], SCHEMA),
            _identity(value["wheel_sha256"], value["interpreter"], SCHEMA_V2),
        }
    ):
        raise RuntimeError("stage_attempt_record_invalid")
    return value


def _acquire_stage_lock(release_id: str) -> int:
    if not re.fullmatch(r"[0-9a-f]{64}", release_id):
        raise ValueError("invalid_release_id")
    run_fd = os.open("/run", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        run_metadata = os.fstat(run_fd)
        if (
            not stat.S_ISDIR(run_metadata.st_mode)
            or run_metadata.st_uid != 0
            or stat.S_IMODE(run_metadata.st_mode) & 0o022
        ):
            raise RuntimeError("stage_lock_parent_unsafe")
        try:
            os.mkdir(STAGE_LOCK_BASE.name, mode=0o700, dir_fd=run_fd)
            os.fsync(run_fd)
        except FileExistsError:
            pass
        lock_dir_fd = os.open(
            STAGE_LOCK_BASE.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=run_fd,
        )
    finally:
        os.close(run_fd)
    try:
        lock_metadata = os.fstat(lock_dir_fd)
        if (
            not stat.S_ISDIR(lock_metadata.st_mode)
            or lock_metadata.st_uid != 0
            or stat.S_IMODE(lock_metadata.st_mode) != 0o700
        ):
            raise RuntimeError("stage_lock_directory_unsafe")
        fd = os.open(
            f"brain-release-stage-{release_id}.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=lock_dir_fd,
        )
    finally:
        os.close(lock_dir_fd)
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


def _uid_has_processes(uid: int, proc_root: Path = Path("/proc")) -> bool:
    for entry in proc_root.iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            status = (entry / "status").read_text(encoding="ascii")
        except (FileNotFoundError, ProcessLookupError):
            continue
        except OSError as exc:
            raise RuntimeError("stage_process_census_unavailable") from exc
        uid_line = next((line for line in status.splitlines() if line.startswith("Uid:")), "")
        fields = uid_line.split()
        if len(fields) != 5 or any(not field.isdecimal() for field in fields[1:]):
            raise RuntimeError("stage_process_census_unavailable")
        if str(uid) in fields[1:]:
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


def _sync_tree(root: Path) -> None:
    for parent, _dirs, files in os.walk(root, topdown=False, followlinks=False):
        for name in files:
            path = Path(parent) / name
            if path.is_symlink():
                continue
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _publish_artifact_paths(attempt_id: str, release_id: str) -> list[dict[str, str]]:
    unit_name = f"memecoin-brain-{release_id}.service"
    return [
        {
            "name": "release",
            "source_parent": str(RELEASE_BASE),
            "source_name": f".stage-{attempt_id}",
            "destination_parent": str(RELEASE_BASE),
            "destination_name": release_id,
            "kind": "directory",
        },
        {
            "name": "pin",
            "source_parent": str(RELEASE_BASE / ".pins"),
            "source_name": f".{release_id}.{attempt_id}.tmp",
            "destination_parent": str(RELEASE_BASE / ".pins"),
            "destination_name": f"{release_id}.sha256",
            "kind": "file",
        },
        {
            "name": "state",
            "source_parent": str(STATE_PREPARED_BASE),
            "source_name": f".{attempt_id}",
            "destination_parent": str(STATE_BASE_V2),
            "destination_name": release_id,
            "kind": "directory",
        },
        {
            "name": "unit",
            "source_parent": str(UNIT_BASE),
            "source_name": f".{unit_name}.{attempt_id}.tmp",
            "destination_parent": str(UNIT_BASE),
            "destination_name": unit_name,
            "kind": "file",
        },
    ]


def _validate_publish_intent(attempt_id: str, record: dict[str, object]) -> list[dict[str, object]]:
    release_id = record.get("release_id")
    if (
        record.get("schema") != PUBLISH_INTENT_SCHEMA
        or record.get("attempt_id") != attempt_id
        or not isinstance(release_id, str)
        or not re.fullmatch(r"[0-9a-f]{64}", release_id)
        or attempt_id[:12] != release_id[:12]
        or not isinstance(record.get("source_commit"), str)
        or not re.fullmatch(r"[0-9a-f]{40,64}", str(record.get("source_commit")))
        or not isinstance(record.get("wheel_sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("wheel_sha256")))
        or not isinstance(record.get("interpreter"), dict)
        or set(record["interpreter"]) != {"path", "version", "soabi"}
        or any(not isinstance(value, str) or not value for value in record["interpreter"].values())
        or not isinstance(record.get("artifacts"), list)
        or _identity(str(record.get("wheel_sha256")), record["interpreter"], SCHEMA_V2)
        != release_id
    ):
        raise RuntimeError("release_publish_intent_invalid")
    expected = _publish_artifact_paths(attempt_id, release_id)
    artifacts = record["artifacts"]
    if len(artifacts) != len(expected):
        raise RuntimeError("release_publish_intent_invalid")
    for item, spec in zip(artifacts, expected, strict=True):
        if not isinstance(item, dict) or set(item) != {*spec, "identity"}:
            raise RuntimeError("release_publish_intent_invalid")
        if any(item.get(key) != value for key, value in spec.items()):
            raise RuntimeError("release_publish_intent_invalid")
        identity = item.get("identity")
        if (
            not isinstance(identity, dict)
            or set(identity) != {"dev", "ino", "uid", "gid", "mode", "kind", "content_sha256"}
            or identity.get("kind") != spec["kind"]
            or any(
                type(identity.get(key)) is not int or identity[key] < 0
                for key in ("dev", "ino", "uid", "gid", "mode")
            )
            or not isinstance(identity.get("content_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", identity["content_sha256"])
        ):
            raise RuntimeError("release_publish_intent_invalid")
        if os.stat(spec["source_parent"], follow_symlinks=False).st_dev != identity["dev"]:
            raise RuntimeError("release_publish_filesystem_mismatch")
        if os.stat(spec["destination_parent"], follow_symlinks=False).st_dev != identity["dev"]:
            raise RuntimeError("release_publish_filesystem_mismatch")
        brain = pwd.getpwnam("brain")
        policy = {
            "release": (0, brain.pw_gid, 0o555),
            "pin": (0, 0, 0o444),
            "state": (0, 0, 0o711),
            "unit": (0, 0, 0o644),
        }[spec["name"]]
        if (identity["uid"], identity["gid"], identity["mode"]) != policy:
            raise RuntimeError("release_publish_artifact_policy_mismatch")
    return artifacts


def _write_sealed_stage_json(path: Path, payload: dict[str, object]) -> None:
    directory_fd = _open_stage_attempt_directory()
    temporary = f".{path.name}.{secrets.token_hex(8)}.tmp"
    encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            view = memoryview(encoded)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("stage_discard_write_failed")
                view = view[written:]
            os.fsync(fd)
            os.fchmod(fd, 0o444)
        finally:
            os.close(fd)
        _rename_noreplace(STAGE_ATTEMPT_BASE, temporary, STAGE_ATTEMPT_BASE, path.name)
        os.fsync(directory_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def _read_sealed_stage_json(path: Path, *, schema: str) -> dict[str, object] | None:
    directory_fd = _open_stage_attempt_directory()
    fd = -1
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        except FileNotFoundError:
            return None
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o444
            or metadata.st_size > 32 * 1024
        ):
            raise RuntimeError("stage_discard_record_unsafe")
        payload = bytearray()
        while len(payload) <= 32 * 1024:
            block = os.read(fd, 4096)
            if not block:
                break
            payload.extend(block)
        if len(payload) > 32 * 1024:
            raise RuntimeError("stage_discard_record_unsafe")
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("stage_discard_record_unavailable") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(directory_fd)
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise RuntimeError("stage_discard_record_invalid")
    return value


def _artifact_location(path: Path, identity: dict[str, object], kind: str) -> bool:
    if not os.path.lexists(path):
        return False
    if path.is_symlink() or _artifact_identity(path, kind=kind) != identity:
        raise RuntimeError("stage_discard_artifact_mismatch")
    return True


def _require_root_directory_chain(path: Path) -> None:
    if not path.is_absolute():
        raise RuntimeError("stage_discard_parent_unsafe")
    for directory in reversed((path, *path.parents)):
        metadata = directory.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise RuntimeError("stage_discard_parent_unsafe")


def _artifact_size(path: Path) -> int:
    metadata = path.lstat()
    if stat.S_ISREG(metadata.st_mode):
        return metadata.st_size
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError("stage_discard_artifact_type_mismatch")
    total = 0
    for parent, dirs, files in os.walk(path, followlinks=False):
        for name in [*dirs, *files]:
            item = Path(parent) / name
            metadata = item.lstat()
            if stat.S_ISREG(metadata.st_mode):
                total += metadata.st_size
            elif not stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
                raise RuntimeError("stage_discard_artifact_type_mismatch")
    return total


def _discard_preflight(
    attempt_id: str,
) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    _, record = _read_stage_attempt(attempt_id)
    intent = _read_publish_intent(attempt_id)
    if intent is None:
        raise RuntimeError("stage_discard_publish_intent_required")
    artifacts = _validate_publish_intent(attempt_id, intent)
    for item in artifacts:
        _require_root_directory_chain(Path(str(item["source_parent"])))
        _require_root_directory_chain(Path(str(item["destination_parent"])))
    discard_intent = _read_sealed_stage_json(
        _discard_intent_path(attempt_id), schema=DISCARD_INTENT_SCHEMA
    )
    expected_discard_intent = {
        "schema": DISCARD_INTENT_SCHEMA,
        "attempt_id": attempt_id,
        "release_id": record["release_id"],
        "artifacts": [
            {
                "name": item["name"],
                "source_parent": item["source_parent"],
                "source_name": item["source_name"],
                "quarantine_name": _discard_quarantine_path(attempt_id, item).name,
                "identity": item["identity"],
            }
            for item in artifacts
        ],
    }
    if discard_intent is not None and discard_intent != expected_discard_intent:
        raise RuntimeError("stage_discard_intent_invalid")
    if record.get("phase") not in {"publishing", "recovery-required"} or any(
        record.get(key) != intent.get(key)
        for key in ("release_id", "source_commit", "wheel_sha256", "interpreter")
    ):
        raise RuntimeError("stage_discard_attempt_not_abandoned")
    for item in artifacts:
        identity = item["identity"]
        source = Path(str(item["source_parent"])) / str(item["source_name"])
        destination = Path(str(item["destination_parent"])) / str(item["destination_name"])
        source_exists = _artifact_location(source, identity, str(item["kind"]))
        if os.path.lexists(destination):
            raise RuntimeError("stage_discard_destination_exists")
        quarantine = _discard_quarantine_path(attempt_id, item)
        quarantine_exists = _artifact_location(quarantine, identity, str(item["kind"]))
        if source_exists == quarantine_exists:
            raise RuntimeError("stage_discard_artifact_location_ambiguous")
        if discard_intent is None and not source_exists:
            raise RuntimeError("stage_discard_source_missing")
    unit_name = f"memecoin-brain-{record['release_id']}.service"
    load, active, file_state, pid = linux_service._unit_status(unit_name)
    if (
        load != "not-found"
        or active != "inactive"
        or file_state not in {"", "disabled"}
        or pid != 0
    ):
        raise RuntimeError("stage_discard_unit_requires_manual_review")
    _require_stage_slice_empty(str(record["slice_name"]))
    builder_uid = record["builder_uid"]
    if builder_uid is not None and _uid_has_processes(builder_uid):
        raise RuntimeError("stage_discard_builder_processes_remaining")
    return record, intent, artifacts


def _validate_discarded_attempt(
    attempt_id: str,
    record: dict[str, object],
    publish_intent: dict[str, object] | None,
    receipt: dict[str, object],
) -> None:
    if publish_intent is None:
        raise RuntimeError("stage_discard_receipt_mismatch")
    artifacts = _validate_publish_intent(attempt_id, publish_intent)
    expected_intent = {
        "schema": DISCARD_INTENT_SCHEMA,
        "attempt_id": attempt_id,
        "release_id": record["release_id"],
        "artifacts": [
            {
                "name": item["name"],
                "source_parent": item["source_parent"],
                "source_name": item["source_name"],
                "quarantine_name": _discard_quarantine_path(attempt_id, item).name,
                "identity": item["identity"],
            }
            for item in artifacts
        ],
    }
    if (
        _read_sealed_stage_json(_discard_intent_path(attempt_id), schema=DISCARD_INTENT_SCHEMA)
        != expected_intent
    ):
        raise RuntimeError("stage_discard_intent_invalid")
    expected_receipt = {
        "schema": DISCARD_RECEIPT_SCHEMA,
        "attempt_id": attempt_id,
        "release_id": record["release_id"],
        "source_commit": record["source_commit"],
        "quarantined": [
            {
                "name": item["name"],
                "quarantine": str(_discard_quarantine_path(attempt_id, item)),
                "identity": item["identity"],
            }
            for item in artifacts
        ],
        "status": "quarantined-retained",
    }
    if receipt != expected_receipt or any(
        os.path.lexists(Path(str(item["source_parent"])) / str(item["source_name"]))
        or os.path.lexists(Path(str(item["destination_parent"])) / str(item["destination_name"]))
        or not _artifact_location(
            _discard_quarantine_path(attempt_id, item),
            item["identity"],
            str(item["kind"]),
        )
        for item in artifacts
    ):
        raise RuntimeError("stage_discard_receipt_mismatch")


def audit_stage(attempt_id: str | None = None) -> dict[str, object]:
    """Read-only inventory of interrupted publish attempts and discard eligibility."""
    if os.geteuid() != 0:
        raise PermissionError("stage_audit_requires_root")
    try:
        metadata = STAGE_ATTEMPT_BASE.lstat()
    except FileNotFoundError:
        return {"attempts": [], "count": 0, "read_only": True}
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise RuntimeError("stage_attempt_directory_unsafe")
    if attempt_id is None:
        ids = sorted(
            path.name[:-5]
            for path in STAGE_ATTEMPT_BASE.glob("*.json")
            if STAGE_ATTEMPT_ID_RE.fullmatch(path.name[:-5])
        )
    else:
        ids = [attempt_id]
    entries = []
    for current_id in ids:
        entry: dict[str, object] = {"attempt_id": current_id, "classification": "manual-review"}
        try:
            record, intent, artifacts = _discard_preflight(current_id)
            receipt = _read_sealed_stage_json(
                _discard_receipt_path(current_id), schema=DISCARD_RECEIPT_SCHEMA
            )
            if receipt is not None:
                _validate_discarded_attempt(current_id, record, intent, receipt)
            bytes_pinned = 0
            for item in artifacts:
                location = Path(str(item["source_parent"])) / str(item["source_name"])
                if not os.path.lexists(location):
                    location = _discard_quarantine_path(current_id, item)
                bytes_pinned += _artifact_size(location)
            entry.update(
                {
                    "classification": (
                        "quarantined-retained" if receipt is not None else "discard-eligible"
                    ),
                    "release_id": record["release_id"],
                    "source_commit": intent["source_commit"],
                    "artifacts": len(artifacts),
                    "bytes": bytes_pinned,
                    "record_mtime_ns": _stage_attempt_path(current_id).stat().st_mtime_ns,
                }
            )
        except (OSError, RuntimeError, ValueError) as exc:
            entry["reason"] = str(exc)
        entries.append(entry)
    return {"attempts": entries, "count": len(entries), "read_only": True}


def discard_stage(attempt_id: str) -> dict[str, object]:
    """Quarantine one exact abandoned publish attempt; never activates or deletes it."""
    if os.geteuid() != 0:
        raise PermissionError("stage_discard_requires_root")
    _, record = _read_stage_attempt(attempt_id)
    lock_fd = _acquire_stage_lock(str(record["release_id"]))
    try:
        receipt_path = _discard_receipt_path(attempt_id)
        receipt = _read_sealed_stage_json(receipt_path, schema=DISCARD_RECEIPT_SCHEMA)
        _, intent, artifacts = _discard_preflight(attempt_id)
        existing = _read_sealed_stage_json(
            _discard_intent_path(attempt_id), schema=DISCARD_INTENT_SCHEMA
        )
        expected_intent = {
            "schema": DISCARD_INTENT_SCHEMA,
            "attempt_id": attempt_id,
            "release_id": record["release_id"],
            "artifacts": [
                {
                    "name": item["name"],
                    "source_parent": item["source_parent"],
                    "source_name": item["source_name"],
                    "quarantine_name": _discard_quarantine_path(attempt_id, item).name,
                    "identity": item["identity"],
                }
                for item in artifacts
            ],
        }
        if existing is None:
            _write_sealed_stage_json(_discard_intent_path(attempt_id), expected_intent)
        elif existing != expected_intent:
            raise RuntimeError("stage_discard_intent_invalid")
        if receipt is not None:
            expected_quarantine = [
                {
                    "name": item["name"],
                    "quarantine": str(_discard_quarantine_path(attempt_id, item)),
                    "identity": item["identity"],
                }
                for item in artifacts
            ]
            if receipt != {
                "schema": DISCARD_RECEIPT_SCHEMA,
                "attempt_id": attempt_id,
                "release_id": record["release_id"],
                "source_commit": intent["source_commit"],
                "quarantined": expected_quarantine,
                "status": "quarantined-retained",
            } or any(
                os.path.lexists(Path(str(item["source_parent"])) / str(item["source_name"]))
                or not os.path.lexists(_discard_quarantine_path(attempt_id, item))
                for item in artifacts
            ):
                raise RuntimeError("stage_discard_receipt_mismatch")
            return receipt
        moved = []
        for item in artifacts:
            identity = item["identity"]
            source = Path(str(item["source_parent"])) / str(item["source_name"])
            quarantine = _discard_quarantine_path(attempt_id, item)
            source_exists = _artifact_location(source, identity, str(item["kind"]))
            quarantine_exists = _artifact_location(quarantine, identity, str(item["kind"]))
            if source_exists == quarantine_exists:
                raise RuntimeError("stage_discard_artifact_location_ambiguous")
            if source_exists:
                _rename_noreplace(source.parent, source.name, quarantine.parent, quarantine.name)
            moved.append(
                {"name": item["name"], "quarantine": str(quarantine), "identity": identity}
            )
        result = {
            "schema": DISCARD_RECEIPT_SCHEMA,
            "attempt_id": attempt_id,
            "release_id": record["release_id"],
            "source_commit": intent["source_commit"],
            "quarantined": moved,
            "status": "quarantined-retained",
        }
        _write_sealed_stage_json(receipt_path, result)
        return result
    finally:
        os.close(lock_fd)


def _finish_publish_locked(
    attempt_id: str, record: dict[str, object], intent: dict[str, object]
) -> dict[str, str]:
    artifacts = _validate_publish_intent(attempt_id, intent)
    if record.get("release_id") != intent.get("release_id") or any(
        record.get(key) != intent.get(key)
        for key in ("source_commit", "wheel_sha256", "interpreter")
    ):
        raise RuntimeError("release_publish_attempt_mismatch")
    release_id = str(intent["release_id"])
    if record.get("phase") == "complete":
        _record_stage_phase(_stage_attempt_path(attempt_id), record, "complete", seal=True)
        verification = verify(release_id)
        if (
            verification["source_commit"] != intent["source_commit"]
            or verification["wheel_sha256"] != intent["wheel_sha256"]
        ):
            raise RuntimeError("release_publish_verify_identity_mismatch")
        return {
            "release_id": release_id,
            "unit": f"memecoin-brain-{release_id}.service",
            "release_path": str(RELEASE_BASE / release_id),
        }
    _ensure_release_base()
    _ensure_root_directory(STATE_BASE_V2, 0o755)
    _ensure_root_directory(STATE_PREPARED_BASE, 0o700)
    _ensure_unit_base()
    _stop_stage_slice(str(record["slice_name"]))
    builder_name = str(record["builder_name"])
    uid_value = record["builder_uid"]
    try:
        builder = pwd.getpwnam(builder_name)
    except KeyError:
        builder = None
    if builder is not None:
        if (
            (uid_value is not None and builder.pw_uid != uid_value)
            or builder.pw_dir != "/nonexistent"
            or builder.pw_shell != "/usr/sbin/nologin"
        ):
            raise RuntimeError("stage_builder_identity_mismatch")
        uid_value = builder.pw_uid
        if _uid_has_processes(uid_value):
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
    elif uid_value is not None and _uid_has_processes(uid_value):
        raise RuntimeError("stage_builder_processes_remaining")
    unit_name = f"memecoin-brain-{release_id}.service"
    load, active, file_state, pid = linux_service._unit_status(unit_name)
    if active != "inactive" or pid != 0 or file_state not in {"", "disabled", "static"}:
        raise RuntimeError("release_publish_unit_requires_manual_review")
    publish_pending = []
    for item in artifacts:
        spec = {
            key: item[key]
            for key in (
                "source_parent",
                "source_name",
                "destination_parent",
                "destination_name",
                "kind",
            )
        }
        identity = item["identity"]
        source = Path(str(item["source_parent"])) / str(item["source_name"])
        destination = Path(str(item["destination_parent"])) / str(item["destination_name"])
        source_exists = os.path.lexists(source)
        destination_exists = os.path.lexists(destination)
        if source_exists and destination_exists:
            raise RuntimeError("release_publish_ambiguous_artifacts")
        current = source if source_exists else destination if destination_exists else None
        if current is None or _artifact_identity(current, kind=str(spec["kind"])) != identity:
            raise RuntimeError("release_publish_artifact_mismatch")
        if source_exists:
            publish_pending.append((item, destination, spec["kind"], identity))
    for item, destination, kind, identity in publish_pending:
        _rename_noreplace(
            Path(str(item["source_parent"])),
            str(item["source_name"]),
            Path(str(item["destination_parent"])),
            str(item["destination_name"]),
        )
        if _artifact_identity(destination, kind=str(kind)) != identity:
            raise RuntimeError("release_publish_readback_mismatch")
    _run(["systemctl", "daemon-reload"])
    if linux_service._unit_status(unit_name) != ("loaded", "inactive", "disabled", 0):
        raise RuntimeError("release_publish_unit_not_inert")
    verification = verify(release_id)
    if (
        verification["source_commit"] != intent["source_commit"]
        or verification["wheel_sha256"] != intent["wheel_sha256"]
    ):
        raise RuntimeError("release_publish_verify_identity_mismatch")
    record_path = _stage_attempt_path(attempt_id)
    _record_stage_phase(record_path, record, "complete", seal=True)
    return {
        "release_id": release_id,
        "unit": unit_name,
        "release_path": str(RELEASE_BASE / release_id),
    }


def _recover_stage_locked(attempt_id: str) -> dict[str, str]:
    _ensure_release_base()
    record_path, record = _read_stage_attempt(attempt_id)
    intent = _read_publish_intent(attempt_id)
    if (
        _read_sealed_stage_json(_discard_intent_path(attempt_id), schema=DISCARD_INTENT_SCHEMA)
        is not None
        or _read_sealed_stage_json(_discard_receipt_path(attempt_id), schema=DISCARD_RECEIPT_SCHEMA)
        is not None
    ):
        raise RuntimeError("stage_discard_recovery_required")
    if intent is not None:
        return _finish_publish_locked(attempt_id, record, intent)
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
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        if "No space left on device" in output or "Errno 28" in output:
            raise RuntimeError("stage_builder_no_space")
        try:
            filesystem = os.statvfs(RELEASE_BASE)
        except OSError:
            filesystem = None
        if filesystem is not None and (filesystem.f_bavail == 0 or filesystem.f_favail == 0):
            raise RuntimeError("stage_builder_no_space")
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


def _identity(wheel_sha: str, interpreter: dict[str, str], schema: str = SCHEMA) -> str:
    body = json.dumps(
        {"schema": schema, "wheel_sha256": wheel_sha, "interpreter": interpreter},
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


def _verify_effective_unit(
    unit: str,
    root: Path,
    state_root: Path,
    *,
    state_layout: str = "legacy",
) -> None:
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
        "StateDirectory": "brain-lab" if state_layout == "legacy" else "",
        "ProtectSystem": "strict",
        "Restart": "no",
    }
    if any(fields[key] != value for key, value in expected.items()):
        raise RuntimeError("release_effective_unit_mismatch")
    if state_layout == "release-v2":
        environment = _run(
            ["systemctl", "show", "--property=Environment", "--value", unit]
        ).stdout.strip()
        if environment != f"HOME={state_root} PYTHONNOUSERSITE=1":
            raise RuntimeError("release_effective_environment_mismatch")
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


def _verify_lab_state(root: Path, release_id: str, state_root: Path | None = None) -> None:
    if state_root is None:
        state_root = STATE_BASE / release_id / "operator"
    allowed_roots = {
        STATE_BASE / release_id / "operator",
        STATE_BASE_V2 / release_id / "operator",
    }
    if state_root not in allowed_roots:
        raise RuntimeError("release_state_root_mismatch")
    try:
        state_root.relative_to(state_root.parents[1])
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
    release_id = _identity(expected_wheel_sha256, interpreter, SCHEMA_V2)
    release_root = RELEASE_BASE / release_id
    unit_name = f"memecoin-brain-{release_id}.service"
    unit_path = UNIT_BASE / unit_name
    _ensure_release_base()
    _ensure_root_directory(STATE_BASE_V2, 0o755)
    _ensure_root_directory(STATE_PREPARED_BASE, 0o700)
    _ensure_unit_base()
    attempt_dir_fd = _open_stage_attempt_directory()
    os.close(attempt_dir_fd)
    user = pwd.getpwnam("brain")
    lock_fd = _acquire_stage_lock(release_id)
    record_path: Path | None = None
    record: dict[str, object] | None = None
    try:
        if pwd.getpwnam(f"brain-stage-{release_id[:12]}"):
            raise RuntimeError("stage_incomplete_requires_recovery")
    except KeyError:
        pass
    for candidate in STAGE_ATTEMPT_BASE.glob(f"{release_id[:12]}-*.json"):
        if not STAGE_ATTEMPT_ID_RE.fullmatch(candidate.stem):
            continue
        try:
            _, previous = _read_stage_attempt(candidate.stem)
        except RuntimeError as exc:
            os.close(lock_fd)
            raise RuntimeError("stage_attempt_record_requires_manual_review") from exc
        if previous["release_id"] != release_id:
            continue
        previous_intent = _read_publish_intent(candidate.stem)
        discarded = _read_sealed_stage_json(
            _discard_receipt_path(candidate.stem), schema=DISCARD_RECEIPT_SCHEMA
        )
        discard_intent = _read_sealed_stage_json(
            _discard_intent_path(candidate.stem), schema=DISCARD_INTENT_SCHEMA
        )
        if discarded is not None:
            try:
                _validate_discarded_attempt(candidate.stem, previous, previous_intent, discarded)
            except RuntimeError:
                os.close(lock_fd)
                raise
            continue
        if discard_intent is not None:
            os.close(lock_fd)
            raise RuntimeError("stage_discard_recovery_required")
        if previous_intent is not None:
            if previous["source_commit"] != source_commit:
                os.close(lock_fd)
                raise RuntimeError("stage_existing_release_source_commit_mismatch")
            try:
                result = _finish_publish_locked(candidate.stem, previous, previous_intent)
            except RuntimeError:
                os.close(lock_fd)
                raise
            os.close(lock_fd)
            return result
        if previous["phase"] not in {"complete", "recovered"}:
            os.close(lock_fd)
            raise RuntimeError("stage_incomplete_requires_recovery")
    if os.path.lexists(release_root) or os.path.lexists(unit_path):
        os.close(lock_fd)
        raise RuntimeError("release_already_exists")
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
        os.replace(venv, staging / "venv")
        builder_root.rmdir()
        state_parent = STATE_BASE_V2 / release_id
        state_root = state_parent / "operator"
        state_temp = STATE_PREPARED_BASE / f".{attempt_id}"
        state_temp.mkdir(mode=0o711)
        os.chown(state_temp, 0, 0)
        prepared_state = state_temp / "operator"
        prepared_state.mkdir(mode=0o700)
        os.chown(prepared_state, user.pw_uid, user.pw_gid)
        lab_config = {
            "mode": "operator",
            "real_execution_enabled": False,
            "paid_budget_usd": 0,
            "expires_ns": 0,
        }
        config_path = prepared_state / "config.json"
        with config_path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(lab_config, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        config_path.chmod(0o600)
        os.chown(config_path, user.pw_uid, user.pw_gid)
        state_fd = os.open(state_temp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(state_fd)
        finally:
            os.close(state_fd)
        unit_text = linux_service.render_unit(
            release_root / "venv/bin/python",
            state_root,
            18792,
            code_root=release_root,
            readwrite_path=state_root,
            state_directory=False,
            state_base=STATE_BASE_V2,
            home=state_root,
            state_layout="release-v2",
            prepared_python=staging / "venv/bin/python",
        )
        unit_text = unit_text.replace(
            "Description=Disposable Brain operator lab (manual activation only)",
            f"Description=Brain release {release_id} (manual activation only)",
        )
        manifest = {
            "schema": SCHEMA_V2,
            "release_id": release_id,
            "source_commit": source_commit,
            "wheel_sha256": expected_wheel_sha256,
            "package_version": version,
            "interpreter": interpreter,
            "installed_inventory_sha256": inventory_sha,
            "installed_inventory": inventory,
            "unit_name": unit_name,
            "unit_sha256": hashlib.sha256(unit_text.encode()).hexdigest(),
            "state_layout": "release-v2",
            "state_root": str(state_root),
            "install_policy": "create-only-disabled-no-activation",
        }
        manifest_path = staging / "release-manifest.json"
        with manifest_path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(manifest_path, 0, user.pw_gid)
        manifest_path.chmod(0o444)
        _seal(staging, user.pw_gid)
        pin_dir = RELEASE_BASE / ".pins"
        pin_dir.mkdir(mode=0o755, exist_ok=True)
        pin_metadata = pin_dir.lstat()
        if (
            not stat.S_ISDIR(pin_metadata.st_mode)
            or pin_metadata.st_uid != 0
            or stat.S_IMODE(pin_metadata.st_mode) & 0o022
        ):
            raise RuntimeError("release_pin_directory_unsafe")
        manifest_sha = _sha256(manifest_path)
        pin_temp = pin_dir / f".{release_id}.{attempt_id}.tmp"
        with pin_temp.open("x", encoding="ascii") as handle:
            handle.write(f"{manifest_sha}  release-manifest.json\n")
            handle.flush()
            os.fsync(handle.fileno())
        pin_temp.chmod(0o444)
        os.chown(pin_temp, 0, 0)
        unit_temp = UNIT_BASE / f".{unit_name}.{attempt_id}.tmp"
        unit_fd = os.open(unit_temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            payload = unit_text.encode()
            view = memoryview(payload)
            while view:
                written = os.write(unit_fd, view)
                if written <= 0:
                    raise OSError("release_unit_temp_write_failed")
                view = view[written:]
            os.fsync(unit_fd)
            os.fchown(unit_fd, 0, 0)
            os.fchmod(unit_fd, 0o644)
        finally:
            os.close(unit_fd)
        _sync_tree(staging)
        _sync_tree(state_temp)
        intent = {
            "schema": PUBLISH_INTENT_SCHEMA,
            "attempt_id": attempt_id,
            "release_id": release_id,
            "source_commit": source_commit,
            "wheel_sha256": expected_wheel_sha256,
            "interpreter": interpreter,
            "artifacts": [],
        }
        for spec in _publish_artifact_paths(attempt_id, release_id):
            path = Path(spec["source_parent"]) / spec["source_name"]
            intent["artifacts"].append(
                {**spec, "identity": _artifact_identity(path, kind=spec["kind"])}
            )
        _validate_publish_intent(attempt_id, intent)
        _write_publish_intent(attempt_id, intent)
        persisted_intent = _read_publish_intent(attempt_id)
        if persisted_intent is None:
            raise RuntimeError("release_publish_intent_unavailable")
        result = _finish_publish_locked(attempt_id, record, persisted_intent)
    except BaseException:
        try:
            _record_stage_phase(record_path, record, "recovery-required")
        except BaseException:
            pass
        raise
    finally:
        os.close(lock_fd)
    return result


def verify(release_id: str) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{64}", release_id):
        raise ValueError("invalid_release_id")
    root = RELEASE_BASE / release_id
    unit_name = f"memecoin-brain-{release_id}.service"
    unit_path = UNIT_BASE / unit_name
    if root.is_symlink() or not root.is_dir() or unit_path.is_symlink() or not unit_path.is_file():
        raise RuntimeError("release_or_unit_missing")
    manifest = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
    manifest_schema = manifest.get("schema")
    if manifest_schema not in {SCHEMA, SCHEMA_V2} or manifest.get("release_id") != release_id:
        raise RuntimeError("release_manifest_mismatch")
    wheel_sha = manifest.get("wheel_sha256")
    interpreter = manifest.get("interpreter")
    if (
        not isinstance(wheel_sha, str)
        or not re.fullmatch(r"[0-9a-f]{64}", wheel_sha)
        or not isinstance(interpreter, dict)
        or _identity(wheel_sha, interpreter, manifest_schema) != release_id
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
    if manifest_schema == SCHEMA_V2:
        state_root = STATE_BASE_V2 / release_id / "operator"
        if manifest.get("state_layout") != "release-v2" or manifest.get("state_root") != str(
            state_root
        ):
            raise RuntimeError("release_state_layout_mismatch")
        state_base_metadata = STATE_BASE_V2.lstat()
        state_parent_metadata = (STATE_BASE_V2 / release_id).lstat()
        operator_metadata = state_root.lstat()
        if (
            state_base_metadata.st_uid != 0
            or stat.S_IMODE(state_base_metadata.st_mode) != 0o755
            or state_parent_metadata.st_uid != 0
            or stat.S_IMODE(state_parent_metadata.st_mode) != 0o711
            or operator_metadata.st_uid != pwd.getpwnam("brain").pw_uid
            or stat.S_IMODE(operator_metadata.st_mode) != 0o700
        ):
            raise RuntimeError("release_state_ownership_mismatch")
        _verify_effective_unit(unit_name, root, state_root, state_layout="release-v2")
    else:
        state_root = STATE_BASE / release_id / "operator"
        _verify_effective_unit(unit_name, root, state_root)
    _verify_lab_state(root, release_id, state_root)
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
        "recover-stage", help="recover one precisely recorded interrupted stage without activation"
    )
    recover.add_argument("--attempt-id", required=True)
    audit = sub.add_parser("audit-stage", help="read-only audit of abandoned publish attempts")
    audit.add_argument("--attempt-id")
    discard = sub.add_parser("discard-stage", help="quarantine one exact abandoned publish attempt")
    discard.add_argument("--attempt-id", required=True)
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
        elif args.command == "audit-stage":
            result = audit_stage(args.attempt_id)
        elif args.command == "discard-stage":
            result = discard_stage(args.attempt_id)
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
