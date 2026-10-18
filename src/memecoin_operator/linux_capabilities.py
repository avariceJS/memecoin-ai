"""Read-only, bounded Linux host capability report for bootstrap and deployment planning."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = "brain-linux-capabilities-v1"
HASH_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
MIN_SOURCE_FREE_BYTES = 1024**3
MAX_COMMAND_OUTPUT = 4096
SAFE_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
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
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=SAFE_ENV,
        )
    except FileNotFoundError:
        return None
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = result.stdout.strip()
    if (
        len(result.stdout.encode()) > MAX_COMMAND_OUTPUT
        or len(result.stderr.encode()) > MAX_COMMAND_OUTPUT
    ):
        return None
    if result.returncode != 0:
        return ("", "command_failed")
    return (output, "ok")


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


def _ext4_check(path: Path) -> dict[str, Any]:
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


def _service_checks(path: Path) -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {"systemd": _systemd_check(), "ext4": _ext4_check(path)}
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
    result = _command([sys.executable, "-I", "-c", script], timeout=15.0)
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
    return _check(
        "available",
        "numpy_torch_cpu_operation_passed",
        numpy=numpy_version[:64],
        torch=torch_version[:64],
    )


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


def report(*, source_commit: str, workspace: Path, unit: str | None = None) -> dict[str, Any]:
    if not HASH_RE.fullmatch(source_commit):
        raise ValueError("source_commit_invalid")
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("workspace_not_directory")
    source_checks = _python_build_checks(workspace)
    source_checks["os"] = _source_os()
    source_checks["architecture"] = _check(
        "available" if platform.machine().lower() in {"x86_64", "amd64"} else "absent",
        "supported_x86_64"
        if platform.machine().lower() in {"x86_64", "amd64"}
        else "x86_64_required",
        value=platform.machine()[:64],
    )
    service_checks = _service_checks(workspace)
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
                    "venv",
                    "ensurepip",
                    "git",
                    "make",
                    "free_space",
                ),
            ),
            "inert_service_ext4": _aggregate(
                service_checks,
                (
                    "systemd",
                    "ext4",
                    "nsenter",
                    "mountinfo",
                    "fdinfo",
                    "blkid",
                    "udevadm",
                    "root_action",
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
        "unit": _unit_observation(unit),
        "scope": "capability_observation_only_no_deployment_or_activation",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    report_parser = sub.add_parser("report")
    report_parser.add_argument("--source-commit", required=True)
    report_parser.add_argument("--workspace", type=Path, default=Path.cwd())
    report_parser.add_argument("--unit")
    report_parser.add_argument("--format", choices=("json",), default="json")
    args = parser.parse_args(argv)
    try:
        print(
            json.dumps(
                report(source_commit=args.source_commit, workspace=args.workspace, unit=args.unit),
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
