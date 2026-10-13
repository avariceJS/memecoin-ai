#!/usr/bin/env python3
"""Read host recovery status or bootstrap an inert research checkout.

This tool never reads application state or protected payloads. Status probes only
repository metadata, root metadata, host tool versions, the bound storage marker,
and the currently loaded launchd labels. Bootstrap writes only a new destination
checkout and its virtual environment; it does not install or start services.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
CONFIG_KEYS = {"research_root", "owner_root", "data_root", "storage_config"}
SERVICE_LABEL_PREFIXES = ("ai.memecoin.brain.", "ai.memecoin.d43.")


class Runner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: int = 30,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


def run_command(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 30,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _result(command: Sequence[str], *, runner: Runner, cwd: Path | None = None) -> str | None:
    try:
        completed = runner(command, cwd=cwd, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _load_config(path: Path | None) -> dict[str, Path]:
    if path is None:
        return {}
    path = path.expanduser().absolute()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_config") from exc
    if not isinstance(payload, dict) or set(payload) - CONFIG_KEYS:
        raise ValueError("invalid_config_fields")
    result: dict[str, Path] = {}
    for key, value in payload.items():
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError("invalid_config_path")
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        result[key] = candidate
    return result


def _path_status(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"state": "not_inspected"}
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"state": "absent", "path": str(path)}
    except OSError as exc:
        return {"state": "unknown", "path": str(path), "error_class": type(exc).__name__}
    if stat.S_ISLNK(metadata.st_mode):
        kind = "symlink"
    elif stat.S_ISDIR(metadata.st_mode):
        kind = "directory"
    elif stat.S_ISREG(metadata.st_mode):
        kind = "file"
    else:
        kind = "other"
    return {
        "state": "present",
        "path": str(path),
        "kind": kind,
        "mode": stat.S_IMODE(metadata.st_mode),
        "size_bytes": metadata.st_size,
    }


def _repository(path: Path | None, runner: Runner) -> dict[str, Any]:
    if path is None:
        return {"state": "not_inspected"}
    requested = path.expanduser()
    path_state = _path_status(requested)
    if path_state["state"] != "present":
        return {
            "state": path_state["state"],
            "path": str(requested),
            "head": None,
            "dirty": None,
        }
    if path_state.get("kind") == "symlink":
        return {"state": "unknown", "path": str(requested), "head": None, "dirty": None}
    root = requested.resolve(strict=False)
    head = _result(["git", "-C", str(root), "rev-parse", "HEAD"], runner=runner)
    if head is None or not COMMIT_RE.fullmatch(head):
        return {"state": "unknown", "path": str(root), "head": None, "dirty": None}
    dirty_result = _result(["git", "-C", str(root), "status", "--porcelain"], runner=runner)
    return {
        "state": "present",
        "path": str(root),
        "head": head,
        "dirty": None if dirty_result is None else bool(dirty_result),
    }


def _launchd(runner: Runner, system: str) -> dict[str, Any]:
    if system != "Darwin":
        return {"state": "unknown", "reason": "launchd_not_applicable"}
    output = _result(["launchctl", "list"], runner=runner)
    if output is None:
        return {"state": "unknown", "labels": []}
    labels: list[dict[str, Any]] = []
    for line in output.splitlines():
        fields = line.split(None, 2)
        if len(fields) != 3 or not fields[2].startswith(SERVICE_LABEL_PREFIXES):
            continue
        pid: int | None
        try:
            pid = None if fields[0] == "-" else int(fields[0])
        except ValueError:
            continue
        labels.append({"label": fields[2], "pid": pid, "state": "present"})
    return {
        "state": "present" if labels else "absent",
        "labels": sorted(labels, key=lambda item: item["label"]),
        "loaded_count": len(labels),
        "running_count": sum(item["pid"] is not None for item in labels),
    }


def _storage_status(config_path: Path | None, *, probe: Any = None) -> dict[str, Any]:
    if config_path is None:
        return {"state": "not_inspected"}
    config_path = config_path.absolute()
    config_state = _path_status(config_path)
    if config_state["state"] != "present":
        return {"state": config_state["state"]}
    try:
        # Import only the existing verifier. This does not configure storage or create a root.
        source_root = Path(__file__).resolve().parents[1] / "src"
        if source_root.is_dir() and str(source_root) not in sys.path:
            sys.path.insert(0, str(source_root))
        from memecoin_research_storage import verify_root

        resolved = verify_root(config_path, probe=probe)
    except Exception as exc:
        return {"state": "unknown", "error_class": type(exc).__name__}
    return {
        "state": "present",
        "volume_uuid": resolved.volume_uuid,
        "root_id": resolved.root_id,
        "mount_path": str(resolved.mount_path),
        "data_root": str(resolved.path),
        "available_bytes": resolved.available_bytes,
    }


def status(
    *,
    repo: Path | None,
    config: Path | None = None,
    owner_root: Path | None = None,
    data_root: Path | None = None,
    storage_config: Path | None = None,
    runner: Runner = run_command,
    system: str | None = None,
    python_version: str | None = None,
    storage_probe: Any = None,
) -> dict[str, Any]:
    configured = _load_config(config)
    repo = repo or configured.get("research_root")
    owner_root = owner_root or configured.get("owner_root")
    data_root = data_root or configured.get("data_root")
    storage_config = storage_config or configured.get("storage_config")
    repo = repo.expanduser() if repo is not None else None
    owner_root = owner_root.expanduser() if owner_root is not None else None
    data_root = data_root.expanduser() if data_root is not None else None
    storage_config = storage_config.expanduser() if storage_config is not None else None
    detected_system = system or platform.system()
    python = python_version or platform.python_version()
    git_version = _result(["git", "--version"], runner=runner)
    launchd = _launchd(runner, detected_system)
    return {
        "schema": "brain-host-recovery-status-v1",
        "host": {
            "state": "present",
            "os": detected_system,
            "release": platform.release(),
            "architecture": platform.machine(),
            "python": python,
            "git": git_version,
            "pip": _result([sys.executable, "-m", "pip", "--version"], runner=runner),
        },
        "repositories": {"research": _repository(repo, runner)},
        "roots": {
            "research": _path_status(repo),
            "owner": _path_status(owner_root),
            "data": _path_status(data_root),
        },
        "storage": _storage_status(storage_config, probe=storage_probe),
        "services": {"launchd": launchd},
        "scope": {
            "protected_payloads_read": False,
            "secret_values_read": False,
            "service_changes": False,
            "provider_requests": 0,
        },
    }


def render_status(payload: dict[str, Any]) -> str:
    host = payload["host"]
    repo = payload["repositories"]["research"]
    storage = payload["storage"]
    launchd = payload["services"]["launchd"]
    lines = [
        f"Host: {host['os']} {host['release']} ({host['architecture']})",
        f"Toolchain: Python {host['python']}; {host['git'] or 'git unknown'}",
        f"Research checkout: {repo['state']}"
        + (f"; HEAD {repo['head']}; dirty={repo['dirty']}" if repo["state"] == "present" else ""),
        f"Research root: {payload['roots']['research']['state']}",
        f"Owner root: {payload['roots']['owner']['state']}",
        f"Data root: {payload['roots']['data']['state']}",
        f"Bound storage: {storage['state']}",
        f"Brain launchd: {launchd['state']}"
        + (
            f"; {launchd['loaded_count']} loaded, {launchd['running_count']} running"
            if launchd.get("state") in {"present", "absent"}
            else ""
        ),
        "Protected payloads read: no; secret values read: no; services changed: no;",
        "provider requests: 0",
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class BootstrapOptions:
    remote: str
    commit: str
    destination: Path
    python: Path


class BootstrapError(RuntimeError):
    def __init__(self, stage: str, returncode: int | None = None):
        super().__init__(stage)
        self.stage = stage
        self.returncode = returncode


def _bootstrap_command(
    command: Sequence[str],
    *,
    runner: Runner,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stage: str,
) -> None:
    try:
        result = runner(command, cwd=cwd, timeout=3600, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BootstrapError(stage) from exc
    if result.returncode != 0:
        raise BootstrapError(stage, result.returncode)


def bootstrap(options: BootstrapOptions, *, runner: Runner = run_command) -> dict[str, Any]:
    if not COMMIT_RE.fullmatch(options.commit):
        raise ValueError("exact_40_character_commit_required")
    if not options.remote or "\x00" in options.remote:
        raise ValueError("remote_required")
    python = options.python.expanduser().resolve(strict=False)
    version_text = _result(
        [str(python), "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"],
        runner=runner,
    )
    version_match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\D|$)", version_text or "")
    if version_match is None or tuple(map(int, version_match.groups())) < (3, 11, 0):
        raise ValueError("python_3_11_or_newer_required")
    destination = options.destination.expanduser()
    if not destination.is_absolute():
        raise ValueError("absolute_destination_required")
    if destination.name in {"", ".", ".."}:
        raise ValueError("invalid_destination")
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination_must_be_new")
    # Refuse to create a restore checkout inside any existing Git checkout.
    top = _result(["git", "-C", str(parent), "rev-parse", "--show-toplevel"], runner=runner)
    if top is not None:
        raise ValueError("destination_parent_inside_existing_checkout")
    result: dict[str, Any] = {
        "schema": "brain-host-recovery-bootstrap-v1",
        "state": "in_progress",
        "destination": str(destination),
        "commit": options.commit,
        "environment": "not_created",
        "smoke": "not_run",
        "services_changed": False,
        "storage_changed": False,
    }
    steps: list[tuple[str, list[str], Path | None]] = [
        ("clone", ["git", "clone", "--no-checkout", "--", options.remote, str(destination)], None),
        (
            "fetch_exact_commit",
            ["git", "-C", str(destination), "fetch", "--no-tags", "origin", options.commit],
            None,
        ),
        (
            "checkout_exact_commit",
            ["git", "-C", str(destination), "checkout", "--detach", options.commit],
            None,
        ),
    ]
    for stage, command, cwd in steps:
        try:
            _bootstrap_command(command, runner=runner, cwd=cwd, stage=stage)
        except BootstrapError as exc:
            result.update(
                {"state": "failed", "failed_stage": exc.stage, "exit_code": exc.returncode}
            )
            return result
    actual = _result(["git", "-C", str(destination), "rev-parse", "HEAD"], runner=runner)
    if actual != options.commit:
        result.update({"state": "failed", "failed_stage": "verify_commit"})
        return result
    try:
        _bootstrap_command(
            [str(python), "-m", "venv", str(destination / ".venv")],
            runner=runner,
            stage="create_environment",
        )
        result["environment"] = "created"
        venv_python = destination / ".venv" / "bin" / "python"
        _bootstrap_command(
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "-r",
                str(destination / "requirements-dev.lock"),
            ],
            runner=runner,
            stage="install_locked_dependencies",
        )
        smoke_env = dict(os.environ)
        smoke_env["PYTHONPATH"] = str(destination / "src")
        _bootstrap_command(
            [str(venv_python), str(destination / "tools" / "smoke.py")],
            runner=runner,
            env=smoke_env,
            stage="smoke",
        )
    except BootstrapError as exc:
        result.update({"state": "failed", "failed_stage": exc.stage, "exit_code": exc.returncode})
        return result
    result.update({"state": "complete", "smoke": "passed", "head": actual})
    return result


def _print(payload: dict[str, Any], output_format: str, *, human: bool = False) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif human:
        print(render_status(payload))
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    status_parser = sub.add_parser("status", help="read-only host and recovery inventory")
    status_parser.add_argument("--repo", type=Path)
    status_parser.add_argument("--config", type=Path)
    status_parser.add_argument("--owner-root", type=Path)
    status_parser.add_argument("--data-root", type=Path)
    status_parser.add_argument("--storage-config", type=Path)
    status_parser.add_argument("--format", choices=("text", "json"), default="text")

    bootstrap_parser = sub.add_parser("bootstrap", help="clone and prepare an inert checkout")
    bootstrap_parser.add_argument("--remote", required=True)
    bootstrap_parser.add_argument("--commit", required=True)
    bootstrap_parser.add_argument("--destination", type=Path, required=True)
    bootstrap_parser.add_argument("--python", type=Path, default=Path(sys.executable))
    bootstrap_parser.add_argument("--format", choices=("text", "json"), default="json")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            payload = status(
                repo=args.repo,
                config=args.config,
                owner_root=args.owner_root,
                data_root=args.data_root,
                storage_config=args.storage_config,
            )
            _print(payload, args.format, human=args.format == "text")
            return 0
        payload = bootstrap(
            BootstrapOptions(args.remote, args.commit, args.destination, args.python)
        )
        _print(payload, args.format)
        return 0 if payload["state"] == "complete" else 2
    except (ValueError, OSError) as exc:
        payload = {"state": "invalid_request", "error": str(exc)}
        _print(payload, "json")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
