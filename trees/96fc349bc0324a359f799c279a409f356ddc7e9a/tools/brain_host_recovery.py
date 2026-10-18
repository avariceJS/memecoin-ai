#!/usr/bin/env python3
"""Read host recovery status or bootstrap an inert research checkout.

This tool never reads application state or protected payloads. Status probes only
repository metadata, root metadata, host tool versions, the bound storage marker,
the currently loaded launchd labels, and PID/name/cwd metadata for processes under
explicitly configured owner or research roots. It does not read process arguments or
environments. Bootstrap writes only a new destination checkout and virtual environment;
it does not install or start services.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
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


def _workspace_processes(
    runner: Runner,
    system: str,
    *,
    research_root: Path | None,
    owner_root: Path | None,
) -> dict[str, Any]:
    roots: dict[str, Path] = {}
    for name, path in (("research", research_root), ("owner", owner_root)):
        if path is None:
            continue
        try:
            resolved = path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if resolved.is_dir():
            roots[name] = resolved
    if not roots:
        return {"state": "not_inspected", "processes": []}
    if system != "Darwin":
        return {"state": "unknown", "reason": "cwd_process_probe_not_supported", "processes": []}
    command = ["lsof", "-nP", "-Fpcn", "-d", "cwd"]
    try:
        result = runner(command, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return {"state": "unknown", "reason": "cwd_process_probe_failed", "processes": []}

    parsed: list[dict[str, Any]] = []
    malformed = False
    current: dict[str, Any] | None = None

    def finish() -> None:
        nonlocal current, malformed
        if current is None:
            return
        pid = current.get("pid")
        name = current.get("name")
        cwd = current.get("cwd")
        if (
            type(pid) is not int
            or pid <= 0
            or not isinstance(name, str)
            or not name
            or any(ord(char) < 32 for char in name)
        ):
            malformed = True
        elif (
            isinstance(cwd, str) and cwd.startswith("/") and not any(ord(char) < 32 for char in cwd)
        ):
            try:
                actual_cwd = Path(cwd).resolve(strict=False)
                matched = sorted(
                    root_name
                    for root_name, root in roots.items()
                    if actual_cwd == root or root in actual_cwd.parents
                )
                if matched:
                    parsed.append(
                        {
                            "pid": pid,
                            "name": name[:128],
                            "cwd": str(actual_cwd),
                            "roots": matched,
                        }
                    )
            except (OSError, RuntimeError):
                malformed = True
        else:
            malformed = True
        current = None

    try:
        for line in result.stdout.splitlines():
            if not line:
                continue
            field, value = line[0], line[1:]
            if field == "p":
                finish()
                try:
                    current = {"pid": int(value)}
                except ValueError:
                    current = {"pid": None}
                    malformed = True
            elif field == "c":
                if current is None:
                    malformed = True
                else:
                    current["name"] = value
            elif field == "n":
                if current is None:
                    malformed = True
                else:
                    current["cwd"] = value
        finish()
    except (TypeError, ValueError):
        malformed = True

    parsed.sort(key=lambda item: (item["pid"], item["name"], item["cwd"]))
    if result.returncode != 0 or malformed:
        return {
            "state": "unknown",
            "reason": "cwd_process_probe_incomplete",
            "processes": parsed,
        }
    return {
        "state": "present" if parsed else "absent",
        "processes": parsed,
        "count": len(parsed),
        "arguments_read": False,
        "environment_read": False,
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
        # For a normal checkout this is a directory. In the offline execution kit it
        # is the /src subpath of source.zip, which zipimport can resolve even though
        # pathlib quite correctly reports that subpath as not a filesystem directory.
        if str(source_root) not in sys.path:
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
    workspace_processes = _workspace_processes(
        runner,
        detected_system,
        research_root=repo,
        owner_root=owner_root,
    )
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
        "processes": {"workspace": workspace_processes},
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
        f"Owner/research cwd processes: {payload['processes']['workspace']['state']}"
        + (
            f"; {payload['processes']['workspace']['count']} found"
            if payload["processes"]["workspace"].get("state") in {"present", "absent"}
            else ""
        ),
        "Protected payloads read: no; secret values read: no; services changed: no;",
        "provider requests: 0",
    ]
    for process in payload["processes"]["workspace"].get("processes", []):
        lines.append(
            f"  PID {process['pid']} {process['name']} cwd={process['cwd']} "
            f"roots={','.join(process['roots'])}"
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class BootstrapOptions:
    remote: str
    commit: str
    destination: Path
    python: Path


class BootstrapError(RuntimeError):
    def __init__(
        self,
        stage: str,
        returncode: int | None = None,
        failure_class: str | None = None,
    ):
        super().__init__(stage)
        self.stage = stage
        self.returncode = returncode
        self.failure_class = failure_class


def _fetch_failure_class(result: subprocess.CompletedProcess[str]) -> str:
    """Map recognizable Git fetch diagnostics to fixed, non-sensitive labels."""
    output = f"{result.stdout}\n{result.stderr}".lower()
    markers = {
        "authentication_or_access": (
            "authentication failed",
            "could not read username",
            "permission denied",
            "repository not found",
            "access denied",
        ),
        "commit_unavailable_or_wrong_pin": (
            "couldn't find remote ref",
            "could not find remote ref",
            "not our ref",
            "unadvertised object",
            "no such ref was fetched",
        ),
        "network_unavailable": (
            "could not resolve host",
            "failed to resolve",
            "failed to connect",
            "connection timed out",
            "connection reset",
            "network is unreachable",
            "operation timed out",
        ),
    }
    for failure_class, phrases in markers.items():
        if any(phrase in output for phrase in phrases):
            return failure_class
    return "fetch_failed"


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
        failure_class = "fetch_failed" if stage == "fetch_exact_commit" else None
        raise BootstrapError(stage, failure_class=failure_class) from exc
    if result.returncode != 0:
        failure_class = _fetch_failure_class(result) if stage == "fetch_exact_commit" else None
        raise BootstrapError(stage, result.returncode, failure_class)


def _isolated_bootstrap_environment(home: Path) -> dict[str, str]:
    """Build a package-install environment without user Python or pip state."""
    temporary = home / "tmp"
    temporary.mkdir(mode=0o700)
    return {
        "PATH": os.defpath,
        "HOME": str(home),
        "TMPDIR": str(temporary),
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_NO_CACHE_DIR": "1",
        "PYTHONNOUSERSITE": "1",
    }


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
        "python_version": ".".join(version_match.groups()),
        "git_transport": {
            "http_version": "HTTP/1.1",
            "scope": "clone_and_fetch_commands_only",
            "persistent_configuration_changed": False,
        },
        "environment": "not_created",
        "smoke": "not_run",
        "services_changed": False,
        "storage_changed": False,
        "bootstrap_isolation": {
            "state": "not_started",
            "scope": "python_venv_install_checks_and_smoke",
        },
    }
    steps: list[tuple[str, list[str], Path | None]] = [
        (
            "clone",
            [
                "git",
                "-c",
                "http.version=HTTP/1.1",
                "clone",
                "--no-checkout",
                "--",
                options.remote,
                str(destination),
            ],
            None,
        ),
        (
            "fetch_exact_commit",
            [
                "git",
                "-c",
                "http.version=HTTP/1.1",
                "-C",
                str(destination),
                "fetch",
                "--no-tags",
                "origin",
                options.commit,
            ],
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
            if exc.failure_class is not None:
                result["failure_class"] = exc.failure_class
            return result
    actual = _result(["git", "-C", str(destination), "rev-parse", "HEAD"], runner=runner)
    if actual != options.commit:
        result.update({"state": "failed", "failed_stage": "verify_commit"})
        return result
    lock_path = destination / "requirements-dev.lock"
    try:
        lock_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    except OSError:
        result.update({"state": "failed", "failed_stage": "read_dependency_lock"})
        return result
    result["dependency_lock_sha256"] = lock_digest
    try:
        with tempfile.TemporaryDirectory(prefix="brain-host-bootstrap-") as isolated_home:
            bootstrap_env = _isolated_bootstrap_environment(Path(isolated_home))
            result["bootstrap_isolation"] = {
                "state": "applied",
                "scope": "python_venv_install_checks_and_smoke",
                "temporary_home": True,
                "pip_configuration": "disabled",
                "pip_cache": "disabled",
                "python_user_site": "disabled",
                "package_index": "https://pypi.org/simple",
            }
            _bootstrap_command(
                [str(python), "-m", "venv", str(destination / ".venv")],
                runner=runner,
                env=bootstrap_env,
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
                    "--no-cache-dir",
                    "--index-url",
                    "https://pypi.org/simple",
                    "-r",
                    "requirements-dev.lock",
                    "-e",
                    ".",
                ],
                runner=runner,
                cwd=destination,
                env=bootstrap_env,
                stage="install_locked_dependencies",
            )
            _bootstrap_command(
                [str(venv_python), "-m", "pip", "check"],
                runner=runner,
                cwd=destination,
                env=bootstrap_env,
                stage="verify_dependencies",
            )
            _bootstrap_command(
                [
                    str(venv_python),
                    "-c",
                    "import site,sys; assert sys.prefix != sys.base_prefix; "
                    "assert site.ENABLE_USER_SITE is False",
                ],
                runner=runner,
                cwd=destination,
                env=bootstrap_env,
                stage="verify_environment_isolation",
            )
            _bootstrap_command(
                [str(venv_python), str(destination / "tools" / "smoke.py")],
                runner=runner,
                cwd=destination,
                env=bootstrap_env,
                stage="smoke",
            )
        if hashlib.sha256(lock_path.read_bytes()).hexdigest() != lock_digest:
            result.update({"state": "failed", "failed_stage": "verify_dependency_lock"})
            return result
        verified_head = _result(["git", "-C", str(destination), "rev-parse", "HEAD"], runner=runner)
        if verified_head != options.commit:
            result.update({"state": "failed", "failed_stage": "verify_commit_after_bootstrap"})
            return result
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
