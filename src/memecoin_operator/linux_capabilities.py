"""Read-only, bounded Linux host capability report for bootstrap and deployment planning."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata
import json
import os
import platform
import pwd
import re
import selectors
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCHEMA = "brain-linux-capabilities-v1"
HASH_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
MIN_SOURCE_FREE_BYTES = 1024**3
MAX_COMMAND_OUTPUT = 4096
SAFE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LC_ALL": "C",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}
SAFE_EXECUTABLE = re.compile(r"^[A-Za-z0-9_+.-]+$")
TOOL_PATHS = {
    "git": ("/usr/bin/git", "/usr/local/bin/git"),
    "make": ("/usr/bin/make", "/usr/local/bin/make"),
    "systemctl": ("/usr/bin/systemctl", "/bin/systemctl"),
    "findmnt": ("/usr/bin/findmnt", "/usr/bin/findmnt"),
    "blkid": ("/usr/sbin/blkid", "/sbin/blkid", "/usr/bin/blkid"),
    "udevadm": ("/usr/bin/udevadm", "/usr/sbin/udevadm", "/sbin/udevadm"),
    "nsenter": ("/usr/bin/nsenter", "/usr/sbin/nsenter"),
    "nvidia-smi": ("/usr/bin/nvidia-smi", "/usr/local/bin/nvidia-smi"),
    "sudo": ("/usr/bin/sudo", "/bin/sudo"),
}


def _tool(name: str) -> str | None:
    for candidate in TOOL_PATHS[name]:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return candidate
    return None


def _check(status: str, reason: str, **fields: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, **fields}


def _command(argv: list[str], *, timeout: float = 2.0) -> tuple[str, str] | None:
    """Run an allowlisted probe, stopping as soon as combined output exceeds its cap."""
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=SAFE_ENV,
        )
    except FileNotFoundError:
        return None
    except OSError:
        return None
    assert process.stdout is not None and process.stderr is not None
    deadline = time.monotonic() + timeout
    try:
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                events = selector.select(remaining)
                if not events:
                    raise subprocess.TimeoutExpired(argv, timeout)
                for key, _ in events:
                    chunk = os.read(key.fd, MAX_COMMAND_OUTPUT + 1 - len(output))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output.extend(chunk)
                    if len(output) > MAX_COMMAND_OUTPUT:
                        return ("", "output_oversize")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(argv, timeout)
        returncode = process.wait(timeout=remaining)
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()
    text_output = bytes(output).decode("utf-8", errors="replace").strip()
    if returncode != 0:
        return ("", "command_failed")
    return (text_output, "ok")


def _aggregate(checks: dict[str, dict[str, Any]], required: tuple[str, ...]) -> dict[str, Any]:
    states = [checks[name]["status"] for name in required]
    if all(state == "available" for state in states):
        return {"status": "available", "required_checks": list(required)}
    if "indeterminate" in states:
        return {"status": "indeterminate", "required_checks": list(required)}
    return {"status": "absent", "required_checks": list(required)}


def _python_version() -> str:
    return platform.python_version()


def _source_os() -> dict[str, Any]:
    try:
        os_release = platform.freedesktop_os_release()
    except (OSError, AttributeError):
        return _check("indeterminate", "os_release_unavailable")
    if os_release.get("ID") != "ubuntu" or os_release.get("VERSION_ID") != "24.04":
        return _check(
            "absent",
            "ubuntu_24_04_required",
            id=os_release.get("ID", "unknown"),
            version=os_release.get("VERSION_ID", "unknown"),
        )
    return _check("available", "supported_ubuntu", id="ubuntu", version="24.04")


def _version_check(name: str) -> dict[str, Any]:
    executable = _tool(name)
    if executable is None:
        return _check("absent", f"{name.replace('-', '_')}_missing")
    result = _command([executable, "--version"])
    if result is None:
        return _check("indeterminate", f"{name.replace('-', '_')}_probe_failed")
    output, status = result
    if status != "ok" or not output:
        return _check("indeterminate", f"{name.replace('-', '_')}_probe_failed")
    return _check("available", "present", version=output.splitlines()[0][:256])


def _systemd_check() -> dict[str, Any]:
    try:
        pid1 = Path("/proc/1/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return _check("indeterminate", "pid1_unavailable")
    if pid1 != "systemd":
        return _check("absent", "systemd_not_pid1", pid1=pid1[:64])
    executable = _tool("systemctl")
    if executable is None:
        return _check("absent", "systemctl_missing", pid1="systemd")
    result = _command([executable, "show", "--property=Version", "--value"])
    if result is None:
        return _check("indeterminate", "systemctl_bus_unavailable", pid1="systemd")
    version, status = result
    if status != "ok" or not version:
        return _check("indeterminate", "systemctl_bus_unavailable", pid1="systemd")
    return _check("available", "systemd_pid1_and_bus", pid1="systemd", version=version[:64])


def _ext4_check(path: Path | None) -> dict[str, Any]:
    if path is None:
        return _check("indeterminate", "state_path_not_supplied")
    if not path.is_dir():
        return _check("indeterminate", "state_path_not_directory")
    executable = _tool("findmnt")
    if executable is None:
        return _check("absent", "findmnt_missing")
    result = _command([executable, "--noheadings", "--output", "FSTYPE", "--target", str(path)])
    if result is None:
        return _check("indeterminate", "findmnt_probe_failed")
    filesystem, status = result
    if status != "ok" or not filesystem:
        return _check("indeterminate", "filesystem_type_unknown")
    if filesystem != "ext4":
        return _check("absent", "ext4_required", filesystem=filesystem[:64])
    return _check("available", "ext4", filesystem="ext4")


def _root_action_check() -> dict[str, Any]:
    if os.geteuid() == 0:
        return _check("available", "running_as_root")
    executable = _tool("sudo")
    if executable is None:
        return _check("absent", "root_or_sudo_missing")
    result = _command([executable, "-n", "true"])
    if result is None:
        return _check("indeterminate", "sudo_noninteractive_probe_failed")
    _output, status = result
    if status != "ok":
        return _check("absent", "passwordless_sudo_unavailable")
    return _check("available", "passwordless_sudo")


def _brain_account_check() -> dict[str, Any]:
    try:
        account = pwd.getpwnam("brain")
    except KeyError:
        return _check("absent", "brain_account_missing")
    if account.pw_uid == 0:
        return _check("absent", "brain_account_must_not_be_root")
    return _check("available", "brain_account_present", uid=account.pw_uid, gid=account.pw_gid)


def _python_build_checks(workspace: Path) -> dict[str, dict[str, Any]]:
    version = sys.version_info
    checks = {
        "python_version": _check(
            "available" if version >= (3, 11) else "absent",
            "supported_python" if version >= (3, 11) else "python_3_11_required",
            version=_python_version(),
            executable=sys.executable,
            abi=sysconfig_tag(),
        )
    }
    try:
        import venv  # noqa: F401

        checks["venv"] = _check("available", "python_venv_module_present")
    except ImportError:
        checks["venv"] = _check("absent", "python_venv_missing")
    try:
        import ensurepip

        checks["ensurepip"] = _check("available", "ensurepip_present", version=ensurepip.version())
    except (ImportError, AttributeError):
        checks["ensurepip"] = _check("absent", "ensurepip_missing")
    checks["git"] = _version_check("git")
    checks["make"] = _version_check("make")
    checks["free_space"] = _free_space(workspace)
    return checks


def _setup_interpreter_check(command: str) -> dict[str, Any]:
    """Probe the exact executable Make will use, without creating an environment."""
    if not command or command != command.strip() or "\x00" in command:
        raise ValueError("setup_python_invalid")
    candidate = Path(command)
    if candidate.is_absolute():
        executable = str(candidate)
    elif SAFE_EXECUTABLE.fullmatch(command):
        executable = shutil.which(command, path=SAFE_ENV["PATH"])
    else:
        raise ValueError("setup_python_invalid")
    if executable is None or not Path(executable).is_file() or not os.access(executable, os.X_OK):
        return _check("absent", "make_setup_python_missing", command=command[:256])
    script = (
        "import ensurepip,json,sys,venv; "
        "print(json.dumps({'version':list(sys.version_info[:3]), "
        "'venv':True,'ensurepip_version':ensurepip.version()}))"
    )
    result = _command([executable, "-I", "-B", "-c", script], timeout=5.0)
    if result is None:
        return _check("indeterminate", "make_setup_python_probe_failed", command=command[:256])
    output, status = result
    if status != "ok":
        return _check("indeterminate", "make_setup_python_probe_failed", command=command[:256])
    try:
        payload = json.loads(output)
        version = payload["version"]
        venv_available = payload["venv"]
        ensurepip_version = payload["ensurepip_version"]
        if (
            not isinstance(version, list)
            or len(version) != 3
            or any(type(part) is not int for part in version)
            or type(venv_available) is not bool
            or not isinstance(ensurepip_version, str)
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return _check("indeterminate", "make_setup_python_probe_output_invalid")
    details = {
        "command": command[:256],
        "executable": executable,
        "version": ".".join(str(part) for part in version),
        "venv": venv_available,
        "ensurepip": bool(ensurepip_version),
        "ensurepip_version": ensurepip_version[:64],
    }
    if tuple(version) < (3, 11):
        return _check("absent", "make_setup_python_3_11_required", **details)
    if not venv_available:
        return _check("absent", "make_setup_python_venv_missing", **details)
    if not ensurepip_version:
        return _check("absent", "make_setup_python_ensurepip_missing", **details)
    return _check("available", "make_setup_python_ready", **details)


def _source_checkout(workspace: Path, expected_commit: str | None = None) -> dict[str, Any]:
    git = _tool("git")
    if git is None:
        return _check("absent", "git_missing")
    head = _command(
        [
            git,
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"safe.directory={workspace}",
            "-C",
            str(workspace),
            "rev-parse",
            "HEAD",
        ]
    )
    if head is None or head[1] != "ok" or not HASH_RE.fullmatch(head[0]):
        return _check("indeterminate", "source_head_probe_failed")
    status = _command(
        [
            git,
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.untrackedCache=false",
            "-c",
            f"safe.directory={workspace}",
            "-C",
            str(workspace),
            "status",
            "--porcelain=v1",
        ]
    )
    if status is None or status[1] != "ok":
        return _check("indeterminate", "source_status_probe_failed")
    dirty_entries = sum(1 for line in status[0].splitlines() if line)
    matches = expected_commit is None or head[0] == expected_commit
    clean = dirty_entries == 0
    if not matches:
        return _check(
            "absent",
            "source_commit_mismatch",
            observed_commit=head[0],
            clean=clean,
            dirty_entry_count=dirty_entries,
        )
    if not clean:
        return _check(
            "absent",
            "source_checkout_dirty",
            observed_commit=head[0],
            clean=False,
            dirty_entry_count=dirty_entries,
        )
    return _check(
        "available",
        "source_commit_matches_clean_checkout",
        observed_commit=head[0],
        clean=True,
        dirty_entry_count=0,
    )


def sysconfig_tag() -> str:
    try:
        import sysconfig

        return str(sysconfig.get_config_var("SOABI") or "unknown")[:128]
    except (ImportError, AttributeError):
        return "unknown"


def _free_space(path: Path) -> dict[str, Any]:
    try:
        available = shutil.disk_usage(path).free
    except OSError:
        return _check("indeterminate", "free_space_unavailable")
    if available < MIN_SOURCE_FREE_BYTES:
        return _check("absent", "less_than_1_gib_free", available_bytes=available)
    return _check("available", "at_least_1_gib_free", available_bytes=available)


def _service_checks(path: Path | None, unit: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {
        "systemd": _systemd_check(),
        "ext4": _ext4_check(path),
        "brain_user": _brain_account_check(),
    }
    for name in ("nsenter", "blkid", "udevadm"):
        checks[name] = _version_check(name)
    for name, proc_path in (
        ("mountinfo", Path("/proc/self/mountinfo")),
        ("fdinfo", Path("/proc/self/fdinfo")),
    ):
        checks[name] = (
            _check("available", "proc_interface_present")
            if proc_path.is_dir() or proc_path.is_file()
            else _check("absent", "proc_interface_missing")
        )
    checks["root_action"] = _root_action_check()
    if unit is None:
        checks["unit_inert"] = _check("indeterminate", "unit_not_supplied")
    elif unit.get("status") != "available":
        checks["unit_inert"] = _check("indeterminate", "unit_status_unavailable")
    elif (
        unit.get("LoadState") == "loaded"
        and unit.get("ActiveState") == "inactive"
        and unit.get("UnitFileState") == "static"
        and unit.get("MainPID") == "0"
    ):
        checks["unit_inert"] = _check("available", "unit_static_inactive_pid_zero")
    else:
        checks["unit_inert"] = _check("absent", "unit_not_inert")
    return checks


def _cpu_ml_check() -> dict[str, Any]:
    try:
        numpy_version = importlib.metadata.version("numpy")
        torch_version = importlib.metadata.version("torch")
    except importlib.metadata.PackageNotFoundError:
        return _check("absent", "cpu_ml_dependencies_missing")
    script = (
        "import json, numpy, torch; "
        "a=float(numpy.array([1.0, 2.0]).sum()); "
        "b=float(torch.tensor([1.0, 2.0]).sum().item()); "
        "print(json.dumps({'numpy':numpy.__version__,'torch':torch.__version__,'cpu_sum':a+b}))"
    )
    expected = _ml_pins()
    if expected is None:
        return _check("indeterminate", "cpu_ml_pins_unavailable")
    if numpy_version != expected["numpy"] or torch_version != expected["torch"]:
        return _check(
            "absent",
            "cpu_ml_versions_mismatch",
            numpy=numpy_version[:64],
            torch=torch_version[:64],
            expected_numpy=expected["numpy"],
            expected_torch=expected["torch"],
        )
    result = _command([sys.executable, "-I", "-B", "-c", script], timeout=15.0)
    if result is None:
        return _check("indeterminate", "cpu_ml_probe_failed")
    payload, status = result
    if status != "ok":
        return _check("indeterminate", "cpu_ml_import_or_operation_failed")
    try:
        values = json.loads(payload)
    except json.JSONDecodeError:
        return _check("indeterminate", "cpu_ml_probe_output_invalid")
    if values.get("cpu_sum") != 6.0:
        return _check("indeterminate", "cpu_ml_operation_failed")
    if values.get("numpy") != expected["numpy"] or values.get("torch") != expected["torch"]:
        return _check("indeterminate", "cpu_ml_imported_versions_mismatch")
    return _check(
        "available",
        "numpy_torch_cpu_operation_passed",
        numpy=numpy_version[:64],
        torch=torch_version[:64],
    )


def _ml_pins() -> dict[str, str] | None:
    try:
        requirements = importlib.metadata.requires("memecoin-ai")
    except importlib.metadata.PackageNotFoundError:
        return None
    if requirements is None:
        return None
    pins: dict[str, str] = {}
    for line in requirements:
        match = re.fullmatch(
            r"(numpy|torch)==([A-Za-z0-9.+!-]+);\s*extra\s*==\s*['\"]ml['\"]",
            line.strip(),
        )
        if match:
            pins[match.group(1)] = match.group(2)
    return pins if set(pins) == {"numpy", "torch"} else None


def _gpu_observation() -> dict[str, Any]:
    executable = _tool("nvidia-smi")
    if executable is None:
        return _check("absent", "nvidia_smi_missing")
    result = _command(
        [executable, "--query-gpu=name,driver_version", "--format=csv,noheader"], timeout=5.0
    )
    if result is None:
        return _check("indeterminate", "nvidia_driver_probe_failed")
    output, status = result
    if status != "ok" or not output:
        return _check("indeterminate", "nvidia_driver_probe_failed")
    return _check("available", "driver_and_device_observed", observation=output.splitlines()[:8])


def _unit_observation(unit: str | None) -> dict[str, Any] | None:
    if unit is None:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.@:-]{1,192}\.service", unit):
        raise ValueError("unit_name_invalid")
    executable = _tool("systemctl")
    if executable is None:
        return _check("indeterminate", "systemctl_missing")
    result = _command(
        [
            executable,
            "show",
            "--no-pager",
            "--property=LoadState,ActiveState,UnitFileState,MainPID",
            unit,
        ]
    )
    if result is None:
        return _check("indeterminate", "unit_status_probe_failed")
    output, status = result
    if status != "ok":
        return _check("indeterminate", "unit_status_probe_failed")
    fields: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in {"LoadState", "ActiveState", "UnitFileState", "MainPID"}:
            return _check("indeterminate", "unit_status_output_invalid")
        fields[key] = value[:128]
    if set(fields) != {"LoadState", "ActiveState", "UnitFileState", "MainPID"}:
        return _check("indeterminate", "unit_status_output_invalid")
    return _check("available", "unit_status_observed", **fields)


def report(
    *,
    source_commit: str,
    workspace: Path,
    setup_python: str = "python3",
    state_path: Path | None = None,
    unit: str | None = None,
) -> dict[str, Any]:
    if not HASH_RE.fullmatch(source_commit):
        raise ValueError("source_commit_invalid")
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("workspace_not_directory")
    source_checks = _python_build_checks(workspace)
    source_checks["setup_interpreter"] = _setup_interpreter_check(setup_python)
    source_checks["source_checkout"] = _source_checkout(workspace, source_commit)
    source_checks["os"] = _source_os()
    source_checks["architecture"] = _check(
        "available" if platform.machine().lower() in {"x86_64", "amd64"} else "absent",
        "supported_x86_64"
        if platform.machine().lower() in {"x86_64", "amd64"}
        else "x86_64_required",
        value=platform.machine()[:64],
    )
    resolved_state = state_path.resolve(strict=True) if state_path else None
    unit_observation = _unit_observation(unit)
    service_checks = _service_checks(resolved_state, unit_observation)
    checks = {**source_checks, **{f"service_{key}": value for key, value in service_checks.items()}}
    cpu_ml = _cpu_ml_check()
    gpu = _gpu_observation()
    return {
        "schema": SCHEMA,
        "source_commit": source_commit,
        "observed_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "host": {
            "os": platform.system(),
            "architecture": platform.machine(),
            "python": {
                "executable": sys.executable,
                "version": _python_version(),
                "abi": sysconfig_tag(),
            },
        },
        "checks": checks,
        "roles": {
            "source_build": _aggregate(
                source_checks,
                (
                    "os",
                    "architecture",
                    "python_version",
                    "setup_interpreter",
                    "venv",
                    "ensurepip",
                    "git",
                    "make",
                    "free_space",
                    "source_checkout",
                ),
            ),
            "inert_service_host_ext4": _aggregate(
                service_checks,
                (
                    "systemd",
                    "ext4",
                    "brain_user",
                    "nsenter",
                    "mountinfo",
                    "fdinfo",
                    "blkid",
                    "udevadm",
                    "root_action",
                    "unit_inert",
                ),
            ),
            "cpu_ml": {"status": cpu_ml["status"], "optional": True, "check": cpu_ml},
            "gpu": {
                "status": "indeterminate",
                "optional": True,
                "reason": "cuda_execution_requires_separate_qualification",
                "observation": gpu,
            },
        },
        "tool_versions": {key: source_checks[key] for key in ("git", "make")},
        "unit": unit_observation,
        "scope": "capability_observation_only_no_deployment_or_activation",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--source-commit", required=True)
    report_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    report_parser.add_argument(
        "--setup-python",
        default=os.environ.get("PYTHON", "python3"),
        help="single executable used by `make setup` (defaults to PYTHON or python3)",
    )
    report_parser.add_argument("--state-path", type=Path)
    report_parser.add_argument("--unit")
    report_parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                report(
                    source_commit=args.source_commit,
                    workspace=args.workspace,
                    setup_python=args.setup_python,
                    state_path=args.state_path,
                    unit=args.unit,
                ),
                sort_keys=True,
            )
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        reason = str(exc) if re.fullmatch(r"[a-z0-9_:-]+", str(exc)) else "capability_report_failed"
        print(json.dumps({"schema": SCHEMA, "error": reason}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
