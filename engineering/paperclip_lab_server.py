"""Explicit, custody-bound launcher for the pinned local Paperclip lab server.

The helper never installs dependencies, reads or prints the private env file,
uses ambient credentials, or stops a process it did not start and identify.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engineering.processes import process_identity, terminate_owned

PAPERCLIP_PATCH_SOURCE_CHAIN = (
    "3eb3e6d50b91707bb6c06b7dfef9a608643f87fd",
    "d560a09538a255c779e4a10b14d41a3114bf10f2",
    "44b4a1c13d23094283914b8326c902fea9d0d994",
    "b89d1f5e82f7275e372087799f21566721814029",
    "b00893db62c0e1719f731c94f20c9c70cecfac11",
    "c9a2ac81b0c7cdcf6903b2e39ab29b55d9cf9343",
    "aa0cdc262ea5a1d81ef95aa9a7dadc0f981a9e24",
    "89cd827fdb36ede8d12fce16302cf595c88d2ade",
    "0e0c1eb22c879203337cee9710471c6fd30b00b1",
    "f24926c8e7fd231e4cac127532ed642247b222c2",
    "09ec93b95111d7d5e8b9c4a867f37e30d7462de1",
    "d2f6dedfe456473e7cf74799a1ac687ea90a1484",
    "16ae5c543a18db635a03c14f81e4ab7921d9c9a6",
    "35fbfa29a1b2ebaf81019d8022fdf60d70628ead",
    "a0cdea58195eed97c6f1ba4518c88826c778bb02",
    "407762fa09288fad6a9e39771efc5d738b57be7b",
    "0f478de9d9ef0dbac4e2f32bd4f80e8f28dcd9fb",
    "67ae03445e7c4a13d06ebbd1894fcc339f44907b",
    "d142b1dee5c25980169109f0d6161a8c4c7380a2",
    "01302914b5501fbb274b39477e087ac1692d9b6a",
    "b853ab3fa7addbbdba51087bb8e46dc89ca29fda",
    "fecf7b18def542fb6b58e7e665011e66599ffffb",
    "519c892646ec7b06996e990e9e728f794e1a689e",
    "bd1a12fbf94068884cb2d65db3d2fdab5f0a9595",
    "043f2514e67c50320433a0013fb228a75a51325c",
)
PAPERCLIP_SOURCE_CHAIN = (
    "8d994c9f56224b65944e3fab02add1d7fe8431e6",
    "1d9eccf898ec00f9f59ef5f536b69154c211a359",
    "ffc6a3c844faa92dcad570f0a1184434a9a3d6d0",
    "4f969bc74ea0aca1c2a81edde74796c92f221d1c",
    "9667379fcc3e0247ac632659850fbedf59b99229",
    "aa0c5b08f0e8ce09343ffd146f3441e984ede8f7",
    "1637ad0b50c17c5896784b95c9fb97a31defbf4c",
    "8aa17aa0600aa952ff50af9373a05a01bd6125a8",
    "2905d23263ceb188689cd68624332bfa2d56b600",
    "15f93e91bd7a561e6b4c3460ccd619923e15165b",
    "d25b4b51f9f81298c49aea42957fb487f7a17ae5",
    *PAPERCLIP_PATCH_SOURCE_CHAIN,
)
PAPERCLIP_PIN, PAPERCLIP_PARENT_PIN = PAPERCLIP_SOURCE_CHAIN[:2]
PAPERCLIP_PATCH_PIN, PAPERCLIP_PATCH_PARENT_PIN = PAPERCLIP_PATCH_SOURCE_CHAIN[:2]
PAPERCLIP_UPSTREAM_PIN = PAPERCLIP_SOURCE_CHAIN[-1]
PAPERCLIP_STOP_CUSTODY_PINS = frozenset(
    {
        "16ae5c543a18db635a03c14f81e4ab7921d9c9a6",
        "09ec93b95111d7d5e8b9c4a867f37e30d7462de1",
        "0e0c1eb22c879203337cee9710471c6fd30b00b1",
        "35fbfa29a1b2ebaf81019d8022fdf60d70628ead",
        "a0cdea58195eed97c6f1ba4518c88826c778bb02",
        "407762fa09288fad6a9e39771efc5d738b57be7b",
        "0f478de9d9ef0dbac4e2f32bd4f80e8f28dcd9fb",
        "67ae03445e7c4a13d06ebbd1894fcc339f44907b",
        "d142b1dee5c25980169109f0d6161a8c4c7380a2",
        "01302914b5501fbb274b39477e087ac1692d9b6a",
        "b853ab3fa7addbbdba51087bb8e46dc89ca29fda",
        "fecf7b18def542fb6b58e7e665011e66599ffffb",
        "519c892646ec7b06996e990e9e728f794e1a689e",
        "bd1a12fbf94068884cb2d65db3d2fdab5f0a9595",
        "aa0cdc262ea5a1d81ef95aa9a7dadc0f981a9e24",
    }
)
PAPERCLIP_CLEANUP_ONLY_PINS = PAPERCLIP_STOP_CUSTODY_PINS | frozenset(
    {
        PAPERCLIP_PATCH_PIN,
        "8aa17aa0600aa952ff50af9373a05a01bd6125a8",
        "15f93e91bd7a561e6b4c3460ccd619923e15165b",
    }
)
DEFAULT_LAB_ROOT = Path("/Users/root1/Developer/brain-paperclip-lab")
PORT = 3310
HEALTH_URL = f"http://127.0.0.1:{PORT}/api/health"
PID_FILENAME = "paperclip-lab-server.pid.json"
MAX_START_SECONDS = 45
STOP_WAIT_SECONDS = 15


class LabServerError(RuntimeError):
    """Refuse an unsafe or ambiguous lab-server action."""


@dataclass(frozen=True)
class LabPaths:
    root: Path
    source: Path
    home: Path
    codex_home: Path
    state: Path
    env_file: Path
    config_file: Path
    loader: Path
    pid_file: Path
    log_file: Path
    tmp: Path

    @classmethod
    def resolve(cls, root: Path) -> LabPaths:
        root = root.expanduser().resolve()
        state = root / "state"
        instance = state / "instances" / "lab"
        source = root / "paperclip"
        return cls(
            root=root,
            source=source,
            home=root / "home",
            codex_home=root / "synthetic-codex-home",
            state=state,
            env_file=instance / ".env",
            config_file=instance / "config.json",
            loader=source / "server" / "node_modules" / "tsx" / "dist" / "loader.mjs",
            pid_file=state / PID_FILENAME,
            log_file=state / "logs" / "paperclip-lab-server.log",
            tmp=state / "tmp",
        )


def _run(args: list[str], *, cwd: Path | None = None, timeout: float = 5) -> str:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=timeout,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LabServerError(f"command_unavailable:{Path(args[0]).name}") from exc
    if result.returncode:
        raise LabServerError(f"command_failed:{Path(args[0]).name}:{result.returncode}")
    return result.stdout.strip()


def _validate_source(paths: LabPaths) -> str:
    if not paths.source.is_dir():
        raise LabServerError("paperclip_source_missing")
    for index, expected in enumerate(PAPERCLIP_SOURCE_CHAIN):
        revision = "HEAD" + "^" * index
        actual = _run(["git", "rev-parse", revision], cwd=paths.source)
        if actual != expected:
            if index == 0:
                error = "paperclip_source_pin_mismatch"
            elif index == 1:
                error = "paperclip_parent_pin_mismatch"
            elif index == len(PAPERCLIP_SOURCE_CHAIN) - 1:
                error = "paperclip_upstream_pin_mismatch"
            else:
                error = "paperclip_ancestor_pin_mismatch"
            raise LabServerError(error)
    head = PAPERCLIP_PIN
    dirty = _run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=paths.source)
    if dirty:
        raise LabServerError("paperclip_source_not_clean")
    return head


def _validate_private_inputs(paths: LabPaths) -> None:
    required = (
        paths.home,
        paths.codex_home,
        paths.state,
        paths.env_file,
        paths.config_file,
        paths.loader,
    )
    if any(not item.exists() for item in required):
        raise LabServerError("paperclip_lab_input_missing")
    env_mode = stat.S_IMODE(paths.env_file.stat().st_mode)
    if env_mode & 0o077:
        raise LabServerError("paperclip_env_file_permissions_too_open")
    if not paths.home.is_dir() or not paths.state.is_dir():
        raise LabServerError("paperclip_private_root_invalid")


def _node_version(node: Path) -> str:
    value = _run([str(node), "--version"])
    if not value.startswith("v24."):
        raise LabServerError("node_major_version_mismatch")
    return value


def build_command(paths: LabPaths, node: Path) -> list[str]:
    return [
        str(node),
        f"--env-file={paths.env_file}",
        "--import",
        str(paths.loader),
        "server/src/index.ts",
    ]


def build_environment(paths: LabPaths, node: Path) -> dict[str, str]:
    """Construct an allowlisted environment; Node alone reads the private env file."""
    return {
        "HOME": str(paths.home),
        "PAPERCLIP_HOME": str(paths.state),
        "CODEX_HOME": str(paths.codex_home),
        "PAPERCLIP_INSTANCE_ID": "lab",
        "PAPERCLIP_CONFIG": str(paths.config_file),
        "PAPERCLIP_UI_DEV_MIDDLEWARE": "true",
        "PAPERCLIP_RUNNER_NETWORK_ACCESS": "disabled",
        "PAPERCLIP_LISTEN_HOST": "127.0.0.1",
        "PAPERCLIP_LISTEN_PORT": str(PORT),
        "HOST": "127.0.0.1",
        "PORT": str(PORT),
        "PAPERCLIP_ANNOUNCEMENTS_ENABLED": "false",
        # Keep Paperclip's scheduler alive for drain, recovery, reconciliation, and finalizers.
        "HEARTBEAT_SCHEDULER_ENABLED": "true",
        "PATH": os.pathsep.join((str(node.parent), "/usr/bin", "/bin")),
        "TMPDIR": str(paths.tmp),
        "LANG": "C.UTF-8",
    }


def _process_info(pid: int) -> tuple[str, str] | None:
    ps = "/bin/ps" if Path("/bin/ps").exists() else "ps"
    try:
        started = _run([ps, "-p", str(pid), "-o", "lstart="])
        command = _run([ps, "-ww", "-p", str(pid), "-o", "command="])
    except LabServerError as exc:
        if "command_failed" in str(exc):
            return None
        raise
    return None if not started or not command else (started, command)


def _health(timeout: float = 2) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as response:
            if response.status != 200:
                return None
            body = json.loads(response.read(16_384))
            return body if type(body) is dict and body.get("status") == "ok" else None
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None


def _read_manifest(path: Path, *, allow_historical_source: bool = False) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LabServerError("server_pid_manifest_invalid") from exc
    expected = {
        "schema",
        "pid",
        "birth_identity",
        "command_sha256",
        "source_pin",
        "upstream_pin",
        "node_version",
    }
    current_source = (
        type(data) is dict
        and data.get("source_pin") == PAPERCLIP_PIN
        and data.get("upstream_pin") == PAPERCLIP_UPSTREAM_PIN
    )
    historical_source = (
        type(data) is dict
        and type(data.get("source_pin")) is str
        and data.get("source_pin") in PAPERCLIP_CLEANUP_ONLY_PINS
        and data.get("upstream_pin") == PAPERCLIP_UPSTREAM_PIN
    )
    if (
        type(data) is not dict
        or set(data) != expected
        or data["schema"] != "paperclip-lab-process-v3"
        or type(data["pid"]) is not int
        or data["pid"] <= 1
        or type(data["birth_identity"]) is not str
        or not data["birth_identity"].startswith("darwin:")
        or not current_source
        and not (allow_historical_source and historical_source)
    ):
        raise LabServerError("server_pid_manifest_invalid")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise LabServerError("server_pid_manifest_permissions_too_open")
    return data


def _owned_process(
    paths: LabPaths, *, allow_historical_source: bool = False
) -> tuple[dict[str, Any], str] | None:
    if not paths.pid_file.exists():
        return None
    manifest = _read_manifest(paths.pid_file, allow_historical_source=allow_historical_source)
    info = _process_info(manifest["pid"])
    if info is None:
        return manifest, "stopped"
    _, command = info
    birth_identity = process_identity(manifest["pid"])
    if birth_identity is None:
        return manifest, "stopped"
    digest = hashlib.sha256(command.encode()).hexdigest()
    if birth_identity != manifest["birth_identity"] or digest != manifest["command_sha256"]:
        return manifest, "identity_mismatch"
    if (
        str(paths.env_file) not in command
        or str(paths.loader) not in command
        or "server/src/index.ts" not in command
    ):
        return manifest, "identity_mismatch"
    return manifest, "running"


def _write_manifest(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, sort_keys=True, separators=(",", ":"))
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    except BaseException:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def start(paths: LabPaths, node: Path) -> dict[str, Any]:
    source_pin = _validate_source(paths)
    _validate_private_inputs(paths)
    node = node.expanduser().resolve(strict=True)
    version = _node_version(node)
    owned = _owned_process(paths)
    if owned is not None:
        _, state = owned
        if state == "running":
            raise LabServerError("paperclip_lab_server_already_owned")
        if state == "identity_mismatch":
            raise LabServerError("paperclip_lab_pid_identity_mismatch")
        paths.pid_file.unlink()
    if _health() is not None:
        raise LabServerError("paperclip_server_active_but_not_owned")

    paths.tmp.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths.log_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(paths.tmp, 0o700)
    os.chmod(paths.log_file.parent, 0o700)
    command = build_command(paths, node)
    environment = build_environment(paths, node)
    with paths.log_file.open("ab", buffering=0) as log:
        os.chmod(paths.log_file, 0o600)
        try:
            child = subprocess.Popen(
                command,
                cwd=paths.source,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        except OSError as exc:
            raise LabServerError("paperclip_lab_spawn_failed") from exc
    info = _process_info(child.pid)
    birth_identity = process_identity(child.pid)
    if info is None or birth_identity is None:
        child.terminate()
        child.wait(timeout=STOP_WAIT_SECONDS)
        raise LabServerError("paperclip_lab_spawn_not_observed")
    _, actual_command = info
    manifest = {
        "schema": "paperclip-lab-process-v3",
        "pid": child.pid,
        "birth_identity": birth_identity,
        "command_sha256": hashlib.sha256(actual_command.encode()).hexdigest(),
        "source_pin": source_pin,
        "upstream_pin": PAPERCLIP_UPSTREAM_PIN,
        "node_version": version,
    }
    try:
        _write_manifest(paths.pid_file, manifest)
    except BaseException:
        terminate_owned(child.pid, birth_identity)
        try:
            child.wait(timeout=STOP_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        raise
    deadline = time.monotonic() + MAX_START_SECONDS
    while time.monotonic() < deadline:
        health = _health(timeout=1)
        if health is not None:
            return {"status": "ready", "pid": child.pid, "health": health.get("status")}
        if child.poll() is not None:
            break
        time.sleep(0.25)
    try:
        stop(paths)
    except LabServerError:
        pass
    raise LabServerError("paperclip_lab_health_timeout")


def status(paths: LabPaths) -> dict[str, Any]:
    owned = _owned_process(paths)
    if owned is None:
        health = _health()
        return {"status": "active_unowned" if health else "stopped"}
    manifest, state = owned
    health = _health() if state == "running" else None
    return {
        "status": "ready" if state == "running" and health else state,
        "pid": manifest["pid"],
        "healthy": health is not None,
        "source_pin": manifest["source_pin"],
        "upstream_pin": manifest["upstream_pin"],
    }


def stop(paths: LabPaths) -> dict[str, Any]:
    owned = _owned_process(paths, allow_historical_source=True)
    if owned is None:
        if _health() is not None:
            raise LabServerError("paperclip_server_active_but_not_owned")
        return {"status": "stopped"}
    manifest, state = owned
    if state == "identity_mismatch":
        raise LabServerError("paperclip_lab_pid_identity_mismatch")
    if state == "stopped":
        paths.pid_file.unlink()
        return {"status": "stopped", "stale_pid_record_removed": True}
    pid = manifest["pid"]
    birth_identity = manifest["birth_identity"]
    if process_identity(pid) != birth_identity:
        raise LabServerError("paperclip_lab_pid_identity_mismatch")
    try:
        if os.getpgid(pid) != pid or os.getsid(pid) != pid:
            raise LabServerError("paperclip_lab_pid_group_mismatch")
        if process_identity(pid) != birth_identity:
            raise LabServerError("paperclip_lab_pid_identity_mismatch")
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as exc:
        raise LabServerError("paperclip_lab_stop_signal_failed") from exc
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        if process_identity(pid) is None:
            paths.pid_file.unlink(missing_ok=True)
            return {"status": "stopped", "pid": pid}
        time.sleep(0.2)
    if terminate_owned(pid, birth_identity):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if process_identity(pid) is None:
                paths.pid_file.unlink(missing_ok=True)
                return {"status": "stopped", "pid": pid, "forced_after_timeout": True}
            time.sleep(0.1)
    raise LabServerError("paperclip_lab_stop_timeout; owned process remains recorded")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "status", "stop"))
    parser.add_argument("--lab-root", type=Path, default=DEFAULT_LAB_ROOT)
    parser.add_argument("--node", type=Path, default=Path("/Users/root1/.local/bin/node"))
    args = parser.parse_args(argv)
    paths = LabPaths.resolve(args.lab_root)
    try:
        if args.command == "start":
            result = start(paths, args.node)
        elif args.command == "stop":
            result = stop(paths)
        else:
            result = status(paths)
    except LabServerError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
