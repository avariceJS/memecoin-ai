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
    "acceptances": (
        ("task_id", "TEXT", 0, 1, None),
        ("sha", "TEXT", 1, 0, None),
        ("attempt_id", "INTEGER", 1, 0, None),
        ("epoch", "INTEGER", 1, 0, None),
    ),
    "attempts": (
        ("id", "INTEGER", 0, 1, None),
        ("task_id", "TEXT", 1, 0, None),
        ("epoch", "INTEGER", 1, 0, None),
        ("worker", "TEXT", 1, 0, None),
        ("lease_until", "REAL", 1, 0, None),
        ("state", "TEXT", 1, 0, None),
        ("session", "TEXT", 0, 0, None),
    ),
    "candidates": (
        ("task_id", "TEXT", 1, 1, None),
        ("sha", "TEXT", 1, 2, None),
        ("attempt_id", "INTEGER", 1, 0, None),
        ("epoch", "INTEGER", 1, 0, None),
        ("evidence", "TEXT", 1, 0, None),
    ),
    "contracts": (
        ("name", "TEXT", 1, 1, None),
        ("version", "INTEGER", 1, 2, None),
        ("body", "TEXT", 1, 0, None),
    ),
    "decisions": (
        ("name", "TEXT", 1, 1, None),
        ("version", "INTEGER", 1, 2, None),
        ("body", "TEXT", 1, 0, None),
        ("replaces", "TEXT", 0, 0, None),
        ("replaces_version", "INTEGER", 0, 0, None),
    ),
    "events": (
        ("seq", "INTEGER", 0, 1, None),
        ("at", "REAL", 1, 0, None),
        ("kind", "TEXT", 1, 0, None),
        ("entity", "TEXT", 1, 0, None),
        ("body", "TEXT", 1, 0, None),
    ),
    "integrations": (
        ("task_id", "TEXT", 0, 1, None),
        ("sha", "TEXT", 1, 0, None),
        ("integration_sha", "TEXT", 1, 0, None),
        ("details", "TEXT", 1, 0, None),
    ),
    "objectives": (
        ("id", "TEXT", 0, 1, None),
        ("title", "TEXT", 1, 0, None),
        ("brief", "TEXT", 1, 0, None),
    ),
    "quiescence_revision": (
        ("id", "INTEGER", 0, 1, None),
        ("value", "INTEGER", 1, 0, None),
    ),
    "task_specs": (
        ("task_id", "TEXT", 0, 1, None),
        ("body", "TEXT", 1, 0, None),
        ("policy_sha256", "TEXT", 1, 0, None),
        ("admission", "TEXT", 1, 0, None),
        ("decision_name", "TEXT", 1, 0, None),
        ("decision_version", "INTEGER", 1, 0, None),
    ),
    "tasks": (
        ("id", "TEXT", 0, 1, None),
        ("team", "TEXT", 1, 0, None),
        ("title", "TEXT", 1, 0, None),
        ("brief", "TEXT", 1, 0, None),
        ("scope", "TEXT", 1, 0, None),
        ("acceptance", "TEXT", 1, 0, None),
        ("dependencies", "TEXT", 1, 0, None),
        ("contracts", "TEXT", 1, 0, None),
        ("base_sha", "TEXT", 1, 0, None),
        ("objective_id", "TEXT", 0, 0, None),
        ("max_attempts", "INTEGER", 1, 0, None),
        ("state", "TEXT", 1, 0, "'pending'"),
        ("epoch", "INTEGER", 1, 0, "0"),
        ("active_attempt", "INTEGER", 0, 0, None),
        ("candidate_sha", "TEXT", 0, 0, None),
    ),
    "usage": (
        ("id", "INTEGER", 0, 1, None),
        ("attempt_id", "INTEGER", 1, 0, None),
        ("epoch", "INTEGER", 1, 0, None),
        ("body", "TEXT", 1, 0, None),
    ),
    "verifications": (
        ("id", "INTEGER", 0, 1, None),
        ("task_id", "TEXT", 1, 0, None),
        ("sha", "TEXT", 1, 0, None),
        ("attempt_id", "INTEGER", 1, 0, None),
        ("epoch", "INTEGER", 1, 0, None),
        ("passed", "INTEGER", 1, 0, None),
        ("details", "TEXT", 1, 0, None),
    ),
}
_ORGANIZATION_FOREIGN_KEYS = {
    "acceptances": {("tasks", (("task_id", "id"),))},
    "attempts": {("tasks", (("task_id", "id"),))},
    "candidates": {("attempts", (("attempt_id", "id"),)), ("tasks", (("task_id", "id"),))},
    "integrations": {("tasks", (("task_id", "id"),))},
    "task_specs": {("tasks", (("task_id", "id"),))},
    "tasks": {("objectives", (("objective_id", "id"),))},
    "usage": {("attempts", (("attempt_id", "id"),))},
}
_ORGANIZATION_UNIQUE = {
    "acceptances": {("task_id",)},
    "attempts": {("task_id", "epoch")},
    "candidates": {("task_id", "sha")},
    "contracts": {("name", "version")},
    "decisions": {("name", "version")},
    "integrations": {("task_id",)},
    "objectives": {("id",)},
    "task_specs": {("task_id",)},
    "tasks": {("id",)},
}
_LEGACY_ORGANIZATION_SCHEMA_V2 = {
    table: columns
    for table, columns in _ORGANIZATION_SCHEMA.items()
    if table != "quiescence_revision"
}


def _assert_no_running_attempts(database: Path) -> None:
    with sqlite3.connect(database) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables == set(_ORGANIZATION_SCHEMA):
            schema = _ORGANIZATION_SCHEMA
        elif tables == set(_LEGACY_ORGANIZATION_SCHEMA_V2):
            schema = _LEGACY_ORGANIZATION_SCHEMA_V2
        else:
            raise ValueError("snapshot_organization_schema")
        for table, expected_columns in schema.items():
            columns = tuple(
                (row[1], row[2].upper(), row[3], row[5], row[4])
                for row in db.execute(f"PRAGMA table_info({table})")
            )
            if columns != expected_columns:
                raise ValueError("snapshot_organization_schema")
            grouped = {}
            for row in db.execute(f"PRAGMA foreign_key_list({table})"):
                grouped.setdefault(row[0], []).append((row[1], row[2], row[3], row[4]))
            foreign_keys = {
                (
                    rows[0][1],
                    tuple((row[2], row[3]) for row in sorted(rows, key=lambda item: item[0])),
                )
                for rows in grouped.values()
            }
            if foreign_keys != _ORGANIZATION_FOREIGN_KEYS.get(table, set()):
                raise ValueError("snapshot_organization_schema")
            unique_indexes = set()
            for index in db.execute(f"PRAGMA index_list({table})"):
                if index[2]:
                    if index[4]:
                        raise ValueError("snapshot_organization_schema")
                    unique_indexes.add(
                        tuple(
                            row[2]
                            for row in db.execute(f"PRAGMA index_info({index[1]})")
                            if row[2] is not None
                        )
                    )
            if unique_indexes != _ORGANIZATION_UNIQUE.get(table, set()):
                raise ValueError("snapshot_organization_schema")
        try:
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("snapshot_organization_integrity")
            running = db.execute("SELECT 1 FROM attempts WHERE state='running' LIMIT 1").fetchone()
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
        git / "objects/info/http-alternates",
        git / "shallow",
        git / "info/grafts",
    )
    if any(path.exists() or path.is_symlink() for path in forbidden):
        raise ValueError("snapshot_integration_repository_external_objects")
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(repository),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    try:
        config = subprocess.run(
            ["git", "-C", str(repository), "config", "--local", "--null", "--list"],
            capture_output=True,
            check=False,
            env=environment,
        )
    except OSError as exc:
        raise ValueError("snapshot_integration_repository_invalid") from exc
    if config.returncode != 0:
        raise ValueError("snapshot_integration_repository_invalid")
    local_settings = []
    for entry in config.stdout.decode("utf-8", errors="replace").split("\0"):
        if "\n" in entry:
            key, value = entry.split("\n", 1)
            local_settings.append((key.lower(), value.lower()))
    if any(
        key == "extensions.partialclone"
        or (
            key.startswith("remote.")
            and key.endswith(".promisor")
            and value in {"true", "yes", "on", "1"}
        )
        for key, value in local_settings
    ) or any((repository / ".git/objects").rglob("*.promisor")):
        raise ValueError("snapshot_integration_repository_external_objects")
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
    try:
        cfg_bytes = cfg_path.read_bytes()
        cfg = json.loads(cfg_bytes.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("deployment_evidence_invalid") from exc
    if hashlib.sha256(cfg_bytes).hexdigest() != hash_path.read_text().strip():
        raise ValueError("deployment_policy_hash")
    if cfg.get("schema") != "brain-engineering-deployment-v1":
        raise ValueError("deployment_schema")
    if cfg.get("remote_publication_enabled") is not False:
        raise ValueError("remote_publication_must_remain_disabled")
    if cfg.get("restore_requires_host_requalification") is True and not allow_requalification:
        raise ValueError("deployment_requalification_required")
    if allow_requalification and cfg.get("restore_requires_host_requalification") is not True:
        raise ValueError("deployment_requalification_barrier_missing")
    controller = (state / "controller").resolve(strict=True)
    if controller != state / "controller" or cfg.get("controller") != str(controller):
        raise ValueError("controller_path")
    python_value = cfg.get("python")
    if not isinstance(python_value, str) or not Path(python_value).is_absolute():
        raise ValueError("python_executable")
    python = Path(python_value)
    if not python.is_file() or not os.access(python, os.X_OK):
        if not allow_requalification:
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


def reconcile_stopped_restore(state: str | Path) -> dict:
    """Record or read back a passive A05 hold from this exact restored controller.

    This explicit recovery entrypoint validates the structurally restored deployment but
    never enters ``supervise`` or consumes a lifecycle request. It requires a current STOP,
    the exact restore receipt, and the frozen service module from the restored controller.
    """
    deployment = validate_deployment(state, allow_requalification=True)
    root = deployment["state"]
    expected_module = (deployment["controller"] / "engineering/service.py").resolve(strict=True)
    if Path(__file__).resolve(strict=True) != expected_module:
        raise ValueError("restore_reconcile_controller_identity")

    from .org_state import StateError, Store

    db = Store(root / "organization.sqlite")
    lease = None
    created = False
    try:
        snapshot = db.capture_passive_lifecycle_gate_snapshot(root)
        if (
            snapshot["stop"]["present"] is not True
            or snapshot["runtime_ready"] is not False
            or snapshot["host_requalification_required"] is not True
        ):
            raise StateError("restore_reconcile_gate_unqualified")

        decision = db.find_passive_lifecycle_hold(snapshot)
        if decision is None:
            lease = db.claim_lifecycle_owner("passive-restore-reconciler", lease_seconds=30)
            if lease is None:
                decision = db.find_passive_lifecycle_hold(snapshot)
                if decision is None:
                    raise StateError("restore_reconcile_owner_busy")
            else:
                snapshot = db.capture_passive_lifecycle_gate_snapshot(root)
                if (
                    snapshot["stop"]["present"] is not True
                    or snapshot["runtime_ready"] is not False
                    or snapshot["host_requalification_required"] is not True
                ):
                    raise StateError("restore_reconcile_gate_unqualified")
                decision = db.find_passive_lifecycle_hold(snapshot)
                if decision is None:
                    decision = db.record_passive_lifecycle_hold(lease, root, snapshot)
                    created = decision["body"].get("source_snapshot") == snapshot

        if lease is not None:
            db.release_lifecycle_owner(lease)
            lease = None

        current = db.capture_passive_lifecycle_gate_snapshot(root)
        if any(
            current[key] != snapshot[key]
            for key in (
                "schema",
                "stop",
                "restore_receipt_sha256",
                "runtime_ready",
                "host_requalification_required",
                "controller_parse",
            )
        ):
            raise StateError("lifecycle_gate_snapshot_changed")
        worklist = db.lifecycle_request_worklist(limit=100)
        if worklist["overflow"]:
            raise StateError("lifecycle_worklist_overflow")
        request_id = decision["id"].removeprefix("lifecycle/request/")
        item = next(
            (entry for entry in worklist["items"] if entry["request_id"] == request_id), None
        )
        if item is None or item["action"] != "hold":
            raise StateError("restore_reconcile_readback_missing")
        return {
            "status": "held" if created else "already_held",
            "decision_id": decision["id"],
            "decision_version": decision["version"],
            "action": item["action"],
            "reason": item["reason"],
            "worklist": worklist,
        }
    finally:
        try:
            if lease is not None:
                db.release_lifecycle_owner(lease)
        finally:
            db.close()


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
