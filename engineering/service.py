"""Explicit launchd packaging and private snapshots for the org supervisor.

Importing this module has no host side effects. ``install`` only writes a plist;
starting or loading a LaunchAgent remains an operator action.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import plistlib
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path, PurePosixPath

LABEL = "com.brain.engineering.organization"
MAX_EPISODE_SECONDS = 14_400


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("deployment_evidence_invalid") from exc


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _regular_file(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISREG(info.st_mode)


def validate_deployment(state: str | Path) -> dict:
    """Verify a private, qualified bootstrap before exposing launchd arguments."""
    state = Path(state).absolute()
    if state.is_symlink() or not state.is_dir() or state.stat().st_uid != os.getuid():
        raise ValueError("deployment_owner_or_type")
    state = state.resolve(strict=True)
    if state.stat().st_mode & 0o077:
        raise ValueError("deployment_permissions")
    cfg_path, hash_path = state / "config.json", state / "config.sha256"
    if not _regular_file(cfg_path) or not _regular_file(hash_path):
        raise ValueError("deployment_policy_permissions")
    cfg = _json(cfg_path)
    if _digest(cfg_path) != hash_path.read_text().strip():
        raise ValueError("deployment_policy_hash")
    if cfg.get("schema") != "brain-engineering-deployment-v1":
        raise ValueError("deployment_schema")
    if cfg.get("remote_publication_enabled") is not False:
        raise ValueError("remote_publication_must_remain_disabled")
    controller = (state / "controller").resolve(strict=True)
    if controller != state / "controller" or cfg.get("controller") != str(controller):
        raise ValueError("controller_path")
    python = Path(cfg.get("python", ""))
    if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("python_executable")
    manifest_path = state / "controller-manifest.json"
    if not _regular_file(manifest_path):
        raise ValueError("manifest_permissions")
    manifest = _json(manifest_path)
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("manifest_shape")
    seen = set()
    for name, expected in manifest.items():
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts or not isinstance(expected, str):
            raise ValueError("manifest_path")
        root = state / "trusted" if name.startswith("trusted/") else controller
        tail = Path(*rel.parts[1:]) if name.startswith("trusted/") else rel
        path = root / tail
        parents = [path.parent, *path.parent.parents]
        if (
            path.is_symlink()
            or any(parent.is_symlink() for parent in parents if parent != state.parent)
            or not path.is_file()
            or not _regular_file(path)
        ):
            raise ValueError("manifest_file_permissions")
        if _digest(path) != expected:
            raise ValueError("manifest_hash")
        seen.add(path.resolve())
    required = {p.resolve() for p in (controller / "engineering").rglob("*.py")}
    required.add((state / "trusted/pilot_acceptance.py").resolve())
    if not required or not required <= seen:
        raise ValueError("manifest_incomplete")
    reports = {}
    for report_name in ("confinement.json", "native-profile.json"):
        report = state / report_name
        if not _regular_file(report):
            raise ValueError("deployment_not_qualified")
        reports[report_name] = _json(report)
        if reports[report_name].get("qualified") is not True:
            raise ValueError("deployment_not_qualified")

    adapter = controller / "engineering/codex_rpc.py"
    if not adapter.is_file() or not _regular_file(adapter):
        raise ValueError("native_adapter_binding")
    if reports["native-profile.json"].get("adapter_sha256") != _digest(adapter):
        raise ValueError("native_adapter_binding")

    executable_value = cfg.get("codex_executable")
    if not isinstance(executable_value, str) or not Path(executable_value).is_absolute():
        raise ValueError("codex_executable_binding")
    try:
        executable = Path(executable_value).resolve(strict=True)
    except OSError as exc:
        raise ValueError("codex_executable_binding") from exc
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("codex_executable_binding")
    executable_digest = _digest(executable)
    if cfg.get("codex_executable_sha256") != executable_digest:
        raise ValueError("codex_executable_binding")
    if reports["native-profile.json"].get("executable_sha256") != executable_digest:
        raise ValueError("native_executable_binding")
    return {
        "state": state,
        "config": cfg,
        "controller": controller,
        "python": python,
        "codex_executable": executable,
    }


def launch_agent_plist(state: str | Path, *, max_seconds: int = 1800) -> dict:
    deployment = validate_deployment(state)
    if type(max_seconds) is not int or not 1 <= max_seconds <= MAX_EPISODE_SECONDS:
        raise ValueError("episode_bound")
    root, python = deployment["controller"], deployment["python"]
    log_dir = deployment["state"] / "service-logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(python),
            "-m",
            "engineering",
            "supervise",
            "--state",
            str(deployment["state"]),
            "--max-seconds",
            str(max_seconds),
        ],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {
            "PYTHONPATH": str(root),
            "HOME": str(Path.home()),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(log_dir / "stdout.log"),
        "StandardErrorPath": str(log_dir / "stderr.log"),
        "ProcessType": "Background",
    }


def install_launch_agent(
    state: str | Path, *, max_seconds: int = 1800, launch_agents: str | Path | None = None
) -> Path:
    """Write the plist atomically. Does not call launchctl or start the service."""
    payload = launch_agent_plist(state, max_seconds=max_seconds)
    state_path = Path(state).resolve(strict=True)
    (state_path / "service-logs").mkdir(mode=0o700, exist_ok=True)
    os.chmod(state_path / "service-logs", 0o700)
    directory = Path(launch_agents) if launch_agents else Path.home() / "Library/LaunchAgents"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise ValueError("launch_agents_directory")
    destination = directory / f"{LABEL}.plist"
    fd, temporary = tempfile.mkstemp(prefix=f".{LABEL}.", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            plistlib.dump(payload, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


_SNAPSHOT_SCHEMA = "brain-organization-snapshot-v2"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_SQLITE_SIDECARS = {"organization.sqlite-wal", "organization.sqlite-shm"}


def _snapshot_manifest_bytes(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def _snapshot_error(code: str, exc: BaseException | None = None) -> None:
    if exc is None:
        raise ValueError(code)
    raise ValueError(code) from exc


def _safe_relative(value: str) -> bool:
    if not value or "\\" in value or "\x00" in value:
        return False
    parts = value.split("/")
    return (
        not value.startswith("/")
        and all(part not in ("", ".", "..") for part in parts)
        and PurePosixPath(value).as_posix() == value
    )


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _paths_overlap(left: Path, right: Path) -> bool:
    return _inside(left, right) or _inside(right, left)


def _file_digest(path: Path, error_code: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                _snapshot_error(error_code)
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
            after = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) or size != after.st_size:
                _snapshot_error(error_code)
    except OSError as exc:
        _snapshot_error(error_code, exc)
    return size, digest.hexdigest()


def _source_inventory(state: Path) -> dict[str, dict]:
    owner = os.getuid()
    inventory: dict[str, dict] = {}

    def walk(directory: Path, prefix: str = "") -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            _snapshot_error("snapshot_unsafe_entry", exc)
        for child in children:
            relative = f"{prefix}/{child.name}" if prefix else child.name
            path = state / relative
            if not _safe_relative(relative) or relative == "snapshot.json":
                _snapshot_error("snapshot_unsafe_entry")
            try:
                info = child.stat(follow_symlinks=False)
            except OSError as exc:
                _snapshot_error("snapshot_unsafe_entry", exc)
            if info.st_uid != owner:
                _snapshot_error("snapshot_unsafe_entry")
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISDIR(info.st_mode):
                inventory[relative] = {"type": "directory", "mode": mode}
                walk(path, relative)
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                _snapshot_error("snapshot_unsafe_entry")
            if relative in _SQLITE_SIDECARS:
                continue
            if relative == "organization.sqlite":
                inventory[relative] = {"type": "file", "mode": mode}
            elif relative == "supervisor.lock":
                inventory[relative] = {
                    "type": "file",
                    "mode": mode,
                    "size": 0,
                    "sha256": _EMPTY_SHA256,
                }
            else:
                size, digest = _file_digest(path, "snapshot_unsafe_entry")
                inventory[relative] = {
                    "type": "file",
                    "mode": mode,
                    "size": size,
                    "sha256": digest,
                }

    walk(state)
    if "organization.sqlite" not in inventory:
        _snapshot_error("snapshot_database")
    if not {"STOP", "supervisor.lock"} <= set(inventory):
        _snapshot_error("snapshot_unsafe_entry")
    return inventory


def _copy_file(
    source: Path,
    destination: Path,
    *,
    mode: int,
    expected_size: int,
    expected_sha256: str,
    error_code: str,
    snapshot_private: bool = False,
) -> None:
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    digest = hashlib.sha256()
    size = 0
    try:
        source_fd = os.open(source, source_flags)
        with os.fdopen(source_fd, "rb") as src:
            before = os.fstat(src.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or (snapshot_private and stat.S_IMODE(before.st_mode) & 0o077)
            ):
                _snapshot_error(error_code)
            destination_fd = os.open(destination, destination_flags, 0o600)
            with os.fdopen(destination_fd, "wb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                dst.flush()
                os.fsync(dst.fileno())
                os.fchmod(dst.fileno(), mode)
            after = os.fstat(src.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                _snapshot_error(error_code)
    except OSError as exc:
        destination.unlink(missing_ok=True)
        _snapshot_error(error_code, exc)
    if size != expected_size or digest.hexdigest() != expected_sha256:
        destination.unlink(missing_ok=True)
        _snapshot_error(error_code)


def _create_empty_file(path: Path, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.flush()
        os.fsync(stream.fileno())
        os.fchmod(stream.fileno(), mode)


def _snapshot_file_mode(source_mode: int) -> int:
    return 0o600 | (source_mode & 0o100)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _supervisor_barrier(state: Path) -> int:
    path = state / "supervisor.lock"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            os.close(fd)
            _snapshot_error("snapshot_unsafe_entry")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        _snapshot_error("snapshot_busy", exc)
    except OSError as exc:
        _snapshot_error("snapshot_busy", exc)
    return fd


def _backup_database(source_path: Path, target_path: Path, mode: int) -> None:
    source = None
    target = None
    try:
        source_uri = source_path.as_uri() + "?mode=ro"
        source = sqlite3.connect(source_uri, uri=True)
        source.execute("PRAGMA query_only=ON")
        if source.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            _snapshot_error("snapshot_database")
        columns = {row[1] for row in source.execute("PRAGMA table_info(tasks)").fetchall()}
        if not {"id", "state"} <= columns:
            _snapshot_error("snapshot_schema")
        try:
            active = source.execute("SELECT 1 FROM tasks WHERE state='running' LIMIT 1").fetchone()
        except sqlite3.Error as exc:
            _snapshot_error("snapshot_schema", exc)
        if active:
            _snapshot_error("snapshot_active_attempts")
        target = sqlite3.connect(target_path)
        source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            _snapshot_error("snapshot_database")
        journal_mode = target.execute("PRAGMA journal_mode").fetchone()[0].lower()
        if journal_mode == "wal":
            checkpoint = target.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is None or checkpoint[0] != 0:
                _snapshot_error("snapshot_database")
            journal_mode = target.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower()
            if journal_mode != "delete":
                _snapshot_error("snapshot_database")
        target.close()
        target = None
        source.close()
        source = None
        sidecars = (target_path.with_name(target_path.name + suffix) for suffix in ("-wal", "-shm"))
        if any(path.exists() for path in sidecars):
            _snapshot_error("snapshot_database")
        with sqlite3.connect(f"{target_path.as_uri()}?mode=ro", uri=True) as verify:
            if verify.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                _snapshot_error("snapshot_database")
        os.chmod(target_path, mode)
    except ValueError:
        raise
    except (sqlite3.Error, OSError) as exc:
        _snapshot_error("snapshot_database", exc)
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()


def backup_snapshot(state: str | Path, destination: str | Path) -> Path:
    """Create a complete private snapshot while holding the supervisor barrier."""
    deployment = validate_deployment(state)
    state = deployment["state"]
    destination = Path(os.path.abspath(destination))
    if os.path.lexists(destination) or _paths_overlap(state, destination):
        _snapshot_error("snapshot_destination")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent = destination.parent.resolve(strict=True)
        destination = parent / destination.name
    except OSError as exc:
        _snapshot_error("snapshot_destination", exc)
    if os.path.lexists(destination) or _paths_overlap(state, destination):
        _snapshot_error("snapshot_destination")
    temp = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    os.chmod(temp, 0o700)
    lock_fd = None
    try:
        stop = state / "STOP"
        try:
            stop_info = stop.lstat()
        except OSError as exc:
            _snapshot_error("snapshot_requires_stop", exc)
        if not stat.S_ISREG(stop_info.st_mode) or stop_info.st_uid != os.getuid():
            _snapshot_error("snapshot_requires_stop")
        lock_fd = _supervisor_barrier(state)
        source_entries = _source_inventory(state)
        inventory_before = {relative: dict(entry) for relative, entry in source_entries.items()}
        source_root_mode = stat.S_IMODE(state.stat().st_mode)
        for relative, entry in source_entries.items():
            target = temp / relative
            if entry["type"] == "directory":
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if relative == "organization.sqlite":
                _backup_database(state / relative, target, _snapshot_file_mode(entry["mode"]))
                size, digest = _file_digest(target, "snapshot_database")
                entry["size"], entry["sha256"] = size, digest
            elif relative == "supervisor.lock":
                _create_empty_file(target, _snapshot_file_mode(entry["mode"]))
            else:
                _copy_file(
                    state / relative,
                    target,
                    mode=_snapshot_file_mode(entry["mode"]),
                    expected_size=entry["size"],
                    expected_sha256=entry["sha256"],
                    error_code="snapshot_inventory",
                )
        inventory_after = _source_inventory(state)
        if inventory_after != inventory_before:
            _snapshot_error("snapshot_inventory")
        if stat.S_IMODE(state.stat().st_mode) != source_root_mode:
            _snapshot_error("snapshot_inventory")
        for relative, entry in source_entries.items():
            if entry["type"] == "directory":
                os.chmod(temp / relative, 0o700)
        manifest = {
            "schema": _SNAPSHOT_SCHEMA,
            "source_state": str(state),
            "source_state_mode": source_root_mode,
            "entries": source_entries,
        }
        manifest_path = temp / "snapshot.json"
        fd = os.open(
            manifest_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(_snapshot_manifest_bytes(manifest))
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o400)
        os.chmod(temp, 0o700)
        _fsync_directory(temp)
        if os.path.lexists(destination):
            _snapshot_error("snapshot_destination")
        os.rename(temp, destination)
        try:
            _fsync_directory(destination.parent)
        except OSError:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        return destination
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)


def _read_snapshot_manifest(snapshot: Path, expected_sha256: str) -> tuple[dict, bytes]:
    if (
        snapshot.is_symlink()
        or not snapshot.is_dir()
        or snapshot.stat().st_uid != os.getuid()
        or stat.S_IMODE(snapshot.stat().st_mode) != 0o700
    ):
        _snapshot_error("snapshot_permissions")
    manifest_path = snapshot / "snapshot.json"
    try:
        info = manifest_path.lstat()
    except OSError as exc:
        _snapshot_error("snapshot_manifest_entry", exc)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o400
    ):
        _snapshot_error("snapshot_permissions")
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        _snapshot_error("snapshot_manifest_entry", exc)
    if (
        type(expected_sha256) is not str
        or len(expected_sha256) != 64
        or hashlib.sha256(raw).hexdigest() != expected_sha256
    ):
        _snapshot_error("snapshot_manifest_hash")
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        _snapshot_error("snapshot_manifest_entry", exc)
    if (
        type(manifest) is not dict
        or set(manifest) != {"schema", "source_state", "source_state_mode", "entries"}
        or manifest.get("schema") != _SNAPSHOT_SCHEMA
        or type(manifest.get("source_state")) is not str
        or not Path(manifest["source_state"]).is_absolute()
        or type(manifest.get("source_state_mode")) is not int
        or not 0 <= manifest["source_state_mode"] <= 0o7777
        or type(manifest.get("entries")) is not dict
        or raw != _snapshot_manifest_bytes(manifest)
    ):
        _snapshot_error("snapshot_manifest_entry")
    return manifest, raw


def _validate_manifest_entries(entries: dict) -> None:
    if not {"STOP", "supervisor.lock", "organization.sqlite"} <= set(entries):
        _snapshot_error("snapshot_manifest_entry")
    for relative, entry in entries.items():
        if type(relative) is not str or not _safe_relative(relative):
            _snapshot_error("snapshot_manifest_path")
        if relative == "snapshot.json" or relative in _SQLITE_SIDECARS:
            _snapshot_error("snapshot_manifest_path")
        if (
            type(entry) is not dict
            or type(entry.get("type")) is not str
            or entry["type"] not in {"file", "directory"}
        ):
            _snapshot_error("snapshot_manifest_entry")
        expected_keys = {"type", "mode"}
        if entry["type"] == "file":
            expected_keys |= {"size", "sha256"}
        if set(entry) != expected_keys:
            _snapshot_error("snapshot_manifest_entry")
        if type(entry.get("mode")) is not int or not 0 <= entry["mode"] <= 0o7777:
            _snapshot_error("snapshot_manifest_entry")
        if entry["type"] == "file" and (
            type(entry.get("size")) is not int
            or entry["size"] < 0
            or type(entry.get("sha256")) is not str
            or len(entry["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in entry["sha256"])
        ):
            _snapshot_error("snapshot_manifest_entry")
    for relative in entries:
        parts = PurePosixPath(relative).parts
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent not in entries or entries[parent]["type"] != "directory":
                _snapshot_error("snapshot_manifest_path")
    lock = entries["supervisor.lock"]
    if lock.get("type") != "file" or lock.get("size") != 0 or lock.get("sha256") != _EMPTY_SHA256:
        _snapshot_error("snapshot_manifest_entry")


def _snapshot_inventory(snapshot: Path, entries: dict) -> None:
    owner = os.getuid()
    observed: dict[str, dict] = {}

    def walk(directory: Path, prefix: str = "") -> None:
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            _snapshot_error("snapshot_unsafe_entry", exc)
        for child in children:
            relative = f"{prefix}/{child.name}" if prefix else child.name
            if relative == "snapshot.json":
                continue
            if not _safe_relative(relative):
                _snapshot_error("snapshot_manifest_path")
            try:
                info = child.stat(follow_symlinks=False)
            except OSError as exc:
                _snapshot_error("snapshot_unsafe_entry", exc)
            if info.st_uid != owner:
                _snapshot_error("snapshot_permissions")
            if stat.S_ISDIR(info.st_mode):
                if stat.S_IMODE(info.st_mode) != 0o700:
                    _snapshot_error("snapshot_permissions")
                observed[relative] = {"type": "directory"}
                walk(snapshot / relative, relative)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077:
                    _snapshot_error("snapshot_unsafe_entry")
                size, digest = _file_digest(snapshot / relative, "snapshot_unsafe_entry")
                observed[relative] = {"type": "file", "size": size, "sha256": digest}
            else:
                _snapshot_error("snapshot_unsafe_entry")

    walk(snapshot)
    if set(observed) != set(entries):
        _snapshot_error("snapshot_inventory")
    for relative, item in entries.items():
        seen = observed[relative]
        if seen["type"] != item["type"]:
            _snapshot_error("snapshot_inventory")
        if item["type"] == "file" and (
            seen["size"] != item["size"] or seen["sha256"] != item["sha256"]
        ):
            _snapshot_error("snapshot_hash")


def _verify_restored_tree(state: Path, entries: dict, root_mode: int) -> None:
    if stat.S_IMODE(state.stat().st_mode) != root_mode:
        _snapshot_error("snapshot_inventory")
    observed = _source_inventory(state)
    if set(observed) != set(entries):
        _snapshot_error("snapshot_inventory")
    for relative, expected in entries.items():
        actual = observed[relative]
        if actual.get("type") != expected["type"] or actual.get("mode") != expected["mode"]:
            _snapshot_error("snapshot_inventory")
        if expected["type"] == "file":
            if relative == "organization.sqlite":
                size, digest = _file_digest(state / relative, "snapshot_database")
            else:
                size, digest = actual.get("size"), actual.get("sha256")
            if size != expected["size"] or digest != expected["sha256"]:
                _snapshot_error("snapshot_hash")
    _check_database(state / "organization.sqlite", check_running=False)


def _check_database(path: Path, *, check_running: bool) -> None:
    try:
        with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                _snapshot_error("snapshot_database")
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
            if not {"id", "state"} <= columns:
                _snapshot_error("snapshot_schema")
            if (
                check_running
                and db.execute("SELECT 1 FROM tasks WHERE state='running' LIMIT 1").fetchone()
            ):
                _snapshot_error("snapshot_active_attempts")
    except ValueError:
        raise
    except sqlite3.Error as exc:
        _snapshot_error("snapshot_database", exc)


def restore_snapshot(
    snapshot: str | Path,
    destination: str | Path,
    *,
    expected_manifest_sha256: str,
) -> Path:
    """Verify a snapshot completely, then publish it with one sibling rename."""
    snapshot = Path(os.path.abspath(snapshot))
    manifest, _ = _read_snapshot_manifest(snapshot, expected_manifest_sha256)
    entries = manifest["entries"]
    _validate_manifest_entries(entries)
    expected_destination = Path(manifest["source_state"])
    destination = Path(os.path.abspath(destination))
    if destination != expected_destination:
        _snapshot_error("snapshot_restore_destination")
    if os.path.lexists(destination):
        _snapshot_error("snapshot_destination")
    try:
        parent = destination.parent.resolve(strict=True)
        parent_info = parent.stat()
    except OSError as exc:
        _snapshot_error("snapshot_restore_destination", exc)
    if (
        parent != destination.parent
        or not stat.S_ISDIR(parent_info.st_mode)
        or parent_info.st_uid != os.getuid()
    ):
        _snapshot_error("snapshot_restore_destination")
    if _paths_overlap(snapshot, destination) or _paths_overlap(snapshot.resolve(), destination):
        _snapshot_error("snapshot_restore_destination")
    _snapshot_inventory(snapshot, entries)
    temp = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore.", dir=parent))
    os.chmod(temp, 0o700)
    published = False
    try:
        directories = [
            relative for relative, item in entries.items() if item["type"] == "directory"
        ]
        for relative in sorted(directories, key=lambda value: (value.count("/"), value)):
            (temp / relative).mkdir(mode=0o700, parents=True, exist_ok=True)
        files = [relative for relative, item in entries.items() if item["type"] == "file"]
        for relative in files:
            item = entries[relative]
            target = temp / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if relative == "supervisor.lock":
                _create_empty_file(target, item["mode"])
            else:
                _copy_file(
                    snapshot / relative,
                    target,
                    mode=item["mode"],
                    expected_size=item["size"],
                    expected_sha256=item["sha256"],
                    error_code="snapshot_hash",
                    snapshot_private=True,
                )
        for relative in sorted(
            directories, key=lambda value: (value.count("/"), value), reverse=True
        ):
            os.chmod(temp / relative, entries[relative]["mode"])
        os.chmod(temp, manifest["source_state_mode"])
        _verify_restored_tree(temp, entries, manifest["source_state_mode"])
        if os.path.lexists(destination):
            _snapshot_error("snapshot_destination")
        os.rename(temp, destination)
        published = True
        _fsync_directory(parent)
        try:
            validate_deployment(destination)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            published = False
            raise
        return destination
    except Exception:
        if published:
            shutil.rmtree(destination, ignore_errors=True)
        else:
            shutil.rmtree(temp, ignore_errors=True)
        raise
