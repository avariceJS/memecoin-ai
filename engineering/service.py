"""Explicit launchd packaging and private snapshots for the org supervisor.

Importing this module has no host side effects. ``install`` only writes a plist;
starting or loading a LaunchAgent remains an operator action.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path

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
    for report_name in ("confinement.json", "native-profile.json"):
        report = state / report_name
        if not _regular_file(report) or _json(report).get("qualified") is not True:
            raise ValueError("deployment_not_qualified")
    return {"state": state, "config": cfg, "controller": controller, "python": python}


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
            "PYTHONPATH": str(root), "HOME": str(Path.home()),
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
    """Create a private point-in-time SQLite + policy + receipt snapshot."""
    deployment = validate_deployment(state)
    state, destination = deployment["state"], Path(destination).absolute()
    if destination == state or state in destination.parents or destination.exists():
        raise ValueError("snapshot_destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    os.chmod(temp, 0o700)
    try:
        source_db = state / "organization.sqlite"
        if not _regular_file(source_db):
            raise ValueError("database_permissions")
        target_db = temp / "organization.sqlite"
        with sqlite3.connect(f"file:{source_db}?mode=ro", uri=True) as source:
            with sqlite3.connect(target_db) as target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("snapshot_database_integrity")
        os.chmod(target_db, 0o400)
        files = [
            "config.json",
            "config.sha256",
            "controller-manifest.json",
            "confinement.json",
            "native-profile.json",
        ]
        for name in files:
            shutil.copyfile(state / name, temp / name)
        manifest = _json(state / "controller-manifest.json")
        for name in manifest:
            rel = Path(name)
            root = state / "trusted" if name.startswith("trusted/") else state / "controller"
            tail = Path(*rel.parts[1:]) if name.startswith("trusted/") else rel
            source = root / tail
            target_root = temp / ("trusted" if name.startswith("trusted/") else "controller")
            target = target_root / tail
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        for directory in ("receipts",):
            src = state / directory
            if src.exists():
                (temp / directory).mkdir(mode=0o700)
                for item in src.iterdir():
                    if item.is_symlink() or not item.is_file() or not _regular_file(item):
                        raise ValueError("snapshot_receipt_permissions")
                    try:
                        receipt = json.loads(item.read_bytes())
                    except (OSError, ValueError) as exc:
                        raise ValueError("snapshot_receipt_invalid") from exc
                    receipt_hash = hashlib.sha256(_canonical(receipt)).hexdigest()
                    if item.name != receipt_hash + ".json" or item.read_bytes() != _canonical(
                        receipt
                    ):
                        raise ValueError("snapshot_receipt_hash")
                    shutil.copyfile(item, temp / directory / item.name)
        inventory = {}
        for path in sorted(p for p in temp.rglob("*") if p.is_file()):
            if path.name != "snapshot.json":
                inventory[str(path.relative_to(temp))] = _digest(path)
                os.chmod(path, 0o400)
        (temp / "snapshot.json").write_text(
            json.dumps(
                {"schema": "brain-organization-snapshot-v1", "files": inventory},
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
        os.chmod(temp / "snapshot.json", 0o400)
        os.replace(temp, destination)
        return destination
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
