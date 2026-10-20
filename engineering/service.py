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
import subprocess
import tempfile
from pathlib import Path

from .usage_ledger import UsageLedger

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


def _copy_private_tree(source: Path, destination: Path, *, skip_lock: bool = False) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for root, directories, files in os.walk(source, followlinks=False):
        root_path = Path(root)
        relative_root = root_path.relative_to(source)
        target_root = destination / relative_root
        target_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        for name in list(directories):
            item = root_path / name
            if item.is_symlink() or not item.is_dir():
                raise ValueError("snapshot_tree_permissions")
            (target_root / name).mkdir(mode=0o700, exist_ok=True)
        for name in files:
            item = root_path / name
            relative = item.relative_to(source)
            if skip_lock and relative == Path("supervisor.lock"):
                continue
            if item.is_symlink() or not _regular_file(item):
                raise ValueError("snapshot_tree_permissions")
            if item.name.endswith(("-wal", "-shm")):
                continue
            target = destination / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(item, target)
            os.chmod(target, 0o600)


def _sqlite_backup(source: Path, destination: Path) -> None:
    if source.is_symlink() or not _regular_file(source):
        raise ValueError("snapshot_database_permissions")
    input_db = output_db = None
    try:
        input_db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        output_db = sqlite3.connect(destination)
        input_db.backup(output_db)
        if output_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("snapshot_database_integrity")
    except sqlite3.DatabaseError as exc:
        raise ValueError("snapshot_database_integrity") from exc
    finally:
        if output_db is not None:
            output_db.close()
        if input_db is not None:
            input_db.close()
    os.chmod(destination, 0o600)


def _inventory(root: Path) -> dict[str, str]:
    inventory = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("snapshot_tree_permissions")
        if path.is_dir():
            continue
        if not path.is_file() or not _regular_file(path):
            raise ValueError("snapshot_tree_permissions")
        inventory[str(path.relative_to(root))] = _digest(path)
    return inventory


_ORGANIZATION_SCHEMA = {
    "acceptances": ("task_id", "sha", "attempt_id", "epoch"),
    "attempts": ("id", "task_id", "epoch", "worker", "lease_until", "state", "session"),
    "candidates": ("task_id", "sha", "attempt_id", "epoch", "evidence"),
    "contracts": ("name", "version", "body"),
    "decisions": ("name", "version", "body", "replaces", "replaces_version"),
    "events": ("seq", "at", "kind", "entity", "body"),
    "integrations": ("task_id", "sha", "integration_sha", "details"),
    "objectives": ("id", "title", "brief"),
    "task_specs": (
        "task_id", "body", "policy_sha256", "admission", "decision_name", "decision_version"
    ),
    "tasks": (
        "id", "team", "title", "brief", "scope", "acceptance", "dependencies",
        "contracts", "base_sha", "objective_id", "max_attempts", "state", "epoch",
        "active_attempt", "candidate_sha",
    ),
    "usage": ("id", "attempt_id", "epoch", "body"),
    "verifications": ("id", "task_id", "sha", "attempt_id", "epoch", "passed", "details"),
}


def _assert_no_running_attempts(database: Path) -> None:
    with sqlite3.connect(database) as db:
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if tables != set(_ORGANIZATION_SCHEMA):
            raise ValueError("snapshot_organization_schema")
        for table, expected_columns in _ORGANIZATION_SCHEMA.items():
            columns = tuple(row[1] for row in db.execute(f"PRAGMA table_info({table})"))
            if columns != expected_columns:
                raise ValueError("snapshot_organization_schema")
        try:
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("snapshot_organization_integrity")
            running = db.execute(
                "SELECT 1 FROM attempts WHERE state='running' LIMIT 1"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise ValueError("snapshot_organization_schema") from exc
        if running:
            raise ValueError("snapshot_live_attempts")


def _validate_integration_repository(repository: Path) -> None:
    git = repository / ".git"
    if git.is_symlink() or not git.is_dir():
        raise ValueError("snapshot_integration_repository_missing")
    forbidden = (
        git / "objects/info/alternates",
        git / "shallow",
        git / "info/grafts",
    )
    if any(path.exists() or path.is_symlink() for path in forbidden):
        raise ValueError("snapshot_integration_repository_external_objects")
    environment = os.environ.copy()
    for key in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"
    ):
        environment.pop(key, None)
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    for args in (
        ("rev-parse", "--verify", "HEAD^{commit}"),
        ("fsck", "--full", "--strict", "--no-reflogs", "--no-progress"),
    ):
        try:
            result = subprocess.run(
                ["git", "-C", str(repository), *args],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )
        except OSError as exc:
            raise ValueError("snapshot_integration_repository_invalid") from exc
        if result.returncode != 0:
            raise ValueError("snapshot_integration_repository_invalid")


def validate_deployment(state: str | Path, *, allow_requalification: bool = False) -> dict:
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
    if cfg.get("restore_requires_host_requalification") is True and not allow_requalification:
        raise ValueError("deployment_requalification_required")
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
    executable = Path(executable_value)
    try:
        executable = executable.resolve(strict=True)
    except OSError as exc:
        if not allow_requalification:
            raise ValueError("codex_executable_binding") from exc
        executable = Path(executable_value)
    if executable.exists():
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError("codex_executable_binding")
        executable_digest = _digest(executable)
        if cfg.get("codex_executable_sha256") != executable_digest:
            raise ValueError("codex_executable_binding")
        if reports["native-profile.json"].get("executable_sha256") != executable_digest:
            raise ValueError("native_executable_binding")
    elif not allow_requalification:
        raise ValueError("codex_executable_binding")
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


def backup_snapshot(state: str | Path, destination: str | Path) -> Path:
    """Create a complete private checkpoint of a stopped deployment."""
    deployment = validate_deployment(state)
    state = deployment["state"].resolve(strict=True)
    destination_input = Path(destination).absolute()
    destination = destination_input.parent.resolve() / destination_input.name
    if (
        destination == state
        or state in destination.parents
        or destination.exists()
        or destination.is_symlink()
    ):
        raise ValueError("snapshot_destination")
    stop = state / "STOP"
    if stop.is_symlink() or not stop.exists() or not _regular_file(stop):
        raise ValueError("snapshot_requires_STOP")
    lock_path = state / "supervisor.lock"
    if lock_path.is_symlink() or (lock_path.exists() and not _regular_file(lock_path)):
        raise ValueError("snapshot_lock_permissions")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ValueError("snapshot_lock_permissions") from exc
    if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
        os.close(lock_fd)
        raise ValueError("snapshot_lock_permissions")
    os.fchmod(lock_fd, 0o600)
    lock_file = os.fdopen(lock_fd, "r+b")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise ValueError("snapshot_supervisor_active") from exc
    temp = None
    try:
        if stop.is_symlink() or not stop.exists() or not _regular_file(stop):
            raise ValueError("snapshot_requires_STOP")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
        os.chmod(temp, 0o700)
        _copy_private_tree(state, temp, skip_lock=True)
        (temp / "organization.sqlite").unlink(missing_ok=True)
        (temp / "usage-ledger.sqlite").unlink(missing_ok=True)
        _sqlite_backup(state / "organization.sqlite", temp / "organization.sqlite")
        _sqlite_backup(state / "usage-ledger.sqlite", temp / "usage-ledger.sqlite")
        ledger = UsageLedger(temp / "usage-ledger.sqlite")
        ledger.close()
        os.chmod(temp / "organization.sqlite", 0o400)
        os.chmod(temp / "usage-ledger.sqlite", 0o400)
        if stop.is_symlink() or not stop.exists() or not _regular_file(stop):
            raise ValueError("snapshot_requires_STOP")
        _validate_integration_repository(temp / "integration")
        _assert_no_running_attempts(temp / "organization.sqlite")
        inventory = _inventory(temp)
        (temp / "snapshot.json").write_text(
            json.dumps(
                {
                    "schema": "brain-organization-snapshot-v2",
                    "files": inventory,
                    "stop_preserved": True,
                    "running_attempts": 0,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        os.chmod(temp / "snapshot.json", 0o600)
        if destination.exists() or destination.is_symlink():
            raise ValueError("snapshot_destination")
        os.rename(temp, destination)
        return destination
    except Exception:
        if temp is not None:
            shutil.rmtree(temp, ignore_errors=True)
        raise
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def restore_snapshot(snapshot: str | Path, destination: str | Path) -> Path:
    """Restore a verified checkpoint to a fresh path and keep STOP asserted."""
    snapshot_path = Path(snapshot).absolute()
    if snapshot_path.is_symlink() or not snapshot_path.is_dir():
        raise ValueError("restore_path")
    snapshot = snapshot_path.resolve(strict=True)
    if snapshot.stat().st_uid != os.getuid() or snapshot.stat().st_mode & 0o077:
        raise ValueError("snapshot_permissions")
    destination_input = Path(destination).absolute()
    destination = destination_input.parent.resolve() / destination_input.name
    if destination.exists() or destination.is_symlink():
        raise ValueError("restore_path")
    if destination == snapshot or snapshot in destination.parents:
        raise ValueError("restore_path")
    manifest_path = snapshot / "snapshot.json"
    if manifest_path.is_symlink() or not manifest_path.exists() or not _regular_file(manifest_path):
        raise ValueError("snapshot_manifest_invalid")
    manifest = _json(manifest_path)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "brain-organization-snapshot-v2"
        or manifest.get("stop_preserved") is not True
        or manifest.get("running_attempts") != 0
        or not isinstance(manifest.get("files"), dict)
    ):
        raise ValueError("snapshot_manifest_invalid")
    expected = manifest["files"]
    actual = _inventory(snapshot)
    actual.pop("snapshot.json", None)
    if actual != expected:
        raise ValueError("snapshot_inventory_mismatch")
    for name in expected:
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts or name == "snapshot.json":
            raise ValueError("snapshot_inventory_path")
    required_files = {
        "STOP",
        "organization.sqlite",
        "usage-ledger.sqlite",
        "config.json",
        "config.sha256",
        "controller-manifest.json",
        "integration/.git/HEAD",
    }
    if not required_files <= expected.keys():
        raise ValueError("snapshot_incomplete")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    os.chmod(temp, 0o700)
    published = False
    try:
        _copy_private_tree(snapshot, temp)
        cfg_path = temp / "config.json"
        cfg = _json(cfg_path)
        original_controller = Path(cfg.get("controller", "")).resolve()
        original_executable = Path(cfg.get("codex_executable", "")).resolve()
        if (
            not original_controller.is_absolute()
            or original_controller.name != "controller"
            or not original_executable.is_absolute()
        ):
            raise ValueError("restore_config_binding")
        executable_relative = None
        try:
            executable_relative = original_executable.relative_to(original_controller.parent)
        except ValueError:
            pass
        if executable_relative is not None:
            if ".." in executable_relative.parts:
                raise ValueError("restore_config_binding")
            copied_executable = temp / executable_relative
            if copied_executable.is_symlink() or not _regular_file(copied_executable):
                raise ValueError("codex_executable_binding")
            cfg["codex_executable"] = str(copied_executable)
        cfg["controller"] = str((temp / "controller").resolve())
        cfg["restore_requires_host_requalification"] = True
        cfg_bytes = json.dumps(cfg, sort_keys=True, indent=2).encode()
        cfg_path.write_bytes(cfg_bytes)
        (temp / "config.sha256").write_text(hashlib.sha256(cfg_bytes).hexdigest())
        os.chmod(cfg_path, 0o600)
        os.chmod(temp / "config.sha256", 0o600)
        for path in temp.rglob("*"):
            if path.is_dir():
                os.chmod(path, 0o700)
            elif path.is_file():
                os.chmod(path, 0o600)
        if executable_relative is not None:
            os.chmod(temp / executable_relative, 0o700)
        if not (temp / "STOP").is_file():
            raise ValueError("restore_STOP_missing")
        ledger = UsageLedger(temp / "usage-ledger.sqlite")
        ledger.close()
        _assert_no_running_attempts(temp / "organization.sqlite")
        _validate_integration_repository(temp / "integration")
        validate_deployment(temp, allow_requalification=True)
        _sqlite_backup(temp / "organization.sqlite", temp / ".organization-check.sqlite")
        (temp / ".organization-check.sqlite").unlink()
        _sqlite_backup(temp / "usage-ledger.sqlite", temp / ".usage-check.sqlite")
        (temp / ".usage-check.sqlite").unlink()
        receipt_dir = temp / "receipts"
        for item in receipt_dir.glob("*") if receipt_dir.exists() else ():
            if item.is_symlink() or not item.is_file():
                raise ValueError("snapshot_receipt_permissions")
            try:
                receipt = json.loads(item.read_bytes())
            except (OSError, ValueError) as exc:
                raise ValueError("snapshot_receipt_invalid") from exc
            raw = item.read_bytes()
            expected_name = hashlib.sha256(_canonical(receipt)).hexdigest() + ".json"
            if item.name != expected_name or raw != _canonical(receipt):
                raise ValueError("snapshot_receipt_hash")
        if destination.exists() or destination.is_symlink():
            raise ValueError("restore_path")
        cfg["controller"] = str(destination / "controller")
        if executable_relative is not None:
            cfg["codex_executable"] = str(destination / executable_relative)
        cfg_bytes = json.dumps(cfg, sort_keys=True, indent=2).encode()
        cfg_path.write_bytes(cfg_bytes)
        (temp / "config.sha256").write_text(hashlib.sha256(cfg_bytes).hexdigest())
        os.rename(temp, destination)
        published = True
        validate_deployment(destination, allow_requalification=True)
        restore_receipt = {
            "schema": "brain-organization-restore-v1",
            "snapshot_manifest_sha256": _digest(destination / "snapshot.json"),
            "restored_path": str(destination),
            "stop_preserved": True,
            "structural_deployment_validation": True,
            "runtime_ready": False,
            "host_requalification_required": True,
        }
        receipt_path = destination / "restore-receipt.json"
        receipt_path.write_text(json.dumps(restore_receipt, sort_keys=True, indent=2) + "\n")
        os.chmod(receipt_path, 0o600)
        return destination
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        if published:
            shutil.rmtree(destination, ignore_errors=True)
        raise
