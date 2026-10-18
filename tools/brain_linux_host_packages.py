#!/usr/bin/env python3
"""Explicit snapshot-bound package adapter for independently provisioned Ubuntu hosts.

This is separate from brain_linux_packages.py, which remains restricted to disposable
systemd-nspawn labs. The host adapter never installs its own trust root or interpreter.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import platform
import pwd
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from tools import brain_linux_packages as shared
except ModuleNotFoundError:  # Direct ``python tools/...`` execution puts tools/ on sys.path.
    import brain_linux_packages as shared

ROOT = Path("/var/lib/brain-host-provision")
SOURCE = ROOT / "ubuntu.sources"
LISTS = ROOT / "lists"
ARCHIVES = ROOT / "archives"
RECEIPTS = ROOT / "receipts"
LOCK = ROOT / "adapter.lock"
POLICY_RC = Path("/usr/sbin/policy-rc.d")
KEYRING = Path(shared.BOOTSTRAP_KEYRING)
MACHINE_ID = Path("/etc/machine-id")
DPKG_STATUS = Path("/var/lib/dpkg/status")
SCHEMA = "brain-linux-host-package-plan-v1"
SAFE_ENV = {**shared.SAFE_ENV, "DEBIAN_FRONTEND": "noninteractive"}


class HostProvisionError(RuntimeError):
    """A host adapter precondition or transaction failed closed."""


def _canonical(value: Any) -> bytes:
    return shared._canonical(value)


def _sha(data: bytes) -> str:
    return shared._sha(data)


def _file_sha(path: Path) -> str:
    return shared._file_sha(path)


def _run(
    argv: list[str], *, timeout: int = 1800, check: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=SAFE_ENV,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HostProvisionError(
            f"command_failed:{Path(argv[0]).name}:{type(exc).__name__}"
        ) from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1000:]
        raise HostProvisionError(
            f"command_failed:{Path(argv[0]).name}:{result.returncode}:{detail}"
        )
    return result


def _apt_base() -> list[str]:
    return [
        "/usr/bin/apt-get",
        "-o",
        "Dir::Etc::main=/dev/null",
        "-o",
        "Dir::Etc::parts=-",
        "-o",
        f"Dir::Etc::sourcelist={SOURCE}",
        "-o",
        "Dir::Etc::sourceparts=-",
        "-o",
        f"Dir::State::lists={LISTS}",
        "-o",
        f"Dir::Cache::archives={ARCHIVES}",
        "-o",
        "Dir::Cache::pkgcache=/dev/null",
        "-o",
        "Dir::Cache::srcpkgcache=/dev/null",
        "-o",
        "Dpkg::Use-Pty=0",
        "-o",
        "Acquire::Retries=0",
        "-o",
        "DPkg::Lock::Timeout=0",
        "-o",
        "APT::Get::Lock::Timeout=0",
    ]


def _require_apt_unlocked() -> None:
    for path in (
        Path("/var/lib/dpkg/lock-frontend"),
        Path("/var/lib/dpkg/lock"),
        Path("/var/lib/apt/lists/lock"),
        Path("/var/cache/apt/archives/lock"),
    ):
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode):
            raise HostProvisionError("unsafe_apt_lock_file")
        try:
            fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        except OSError as exc:
            raise HostProvisionError("apt_lock_state_unavailable") from exc
        try:
            try:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise HostProvisionError("apt_lock_busy") from exc
        finally:
            os.close(fd)


def _require_root() -> None:
    if os.geteuid() != 0:
        raise HostProvisionError("root_required")


def _sources(snapshot: str) -> bytes:
    return shared._sources(snapshot)


def _ensure_dir(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise HostProvisionError(f"unsafe_directory:{path}")


def _ensure_paths() -> None:
    _ensure_dir(ROOT)
    for path in (LISTS, ARCHIVES, RECEIPTS):
        _ensure_dir(path)


def _write_once(path: Path, value: dict[str, Any]) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(_canonical(value) + b"\n")
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _read_private(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise HostProvisionError(f"unsafe_receipt_file:{path.name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HostProvisionError(f"invalid_receipt:{path.name}") from exc


def _locked():
    """Acquire the root-private adapter lock without waiting."""
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if info.st_uid != 0 or info.st_mode & 0o077 or not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise HostProvisionError("unsafe_adapter_lock")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise HostProvisionError("adapter_lock_busy") from exc
    return fd


def _systemd_container() -> str | None:
    probe = _run(["/usr/bin/systemd-detect-virt", "--container"], check=False)
    if not probe.returncode:
        return probe.stdout.strip()
    return None


def _host_identity() -> dict[str, str]:
    release = shared._os_release()
    if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04":
        raise HostProvisionError("ubuntu_24_04_required")
    if platform.machine() not in {"x86_64", "amd64"}:
        raise HostProvisionError("unsupported_architecture")
    container = _systemd_container()
    if container == "systemd-nspawn":
        raise HostProvisionError(
            "systemd_nspawn_container_refused: use brain_linux_packages.py --lab-only"
        )
    if container:
        raise HostProvisionError(f"container_host_refused:{container}")
    try:
        brain = pwd.getpwnam("brain")
    except KeyError as exc:
        raise HostProvisionError("existing_brain_identity_required") from exc
    if brain.pw_uid == 0:
        raise HostProvisionError("brain_identity_must_be_nonroot")
    for exe, label in (
        ("/usr/bin/apt-get", "apt_get"),
        ("/usr/bin/dpkg", "dpkg"),
        ("/usr/bin/python3.12", "python3_12"),
        ("/usr/bin/systemctl", "systemctl"),
        ("/usr/bin/journalctl", "journalctl"),
    ):
        if not os.access(exe, os.X_OK):
            raise HostProvisionError(f"host_prerequisite_missing:{label}")
    if not os.access(KEYRING, os.R_OK):
        raise HostProvisionError("ubuntu_archive_keyring_missing")
    python = _run(
        ["/usr/bin/python3.12", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"]
    )
    if python.stdout.strip() != "3.12":
        raise HostProvisionError("python3_12_required")
    if not DPKG_STATUS.is_file() or not os.access(DPKG_STATUS, os.R_OK | os.W_OK):
        raise HostProvisionError("working_dpkg_required")
    if Path("/proc/1/comm").read_text(encoding="ascii").strip() != "systemd":
        raise HostProvisionError("systemd_pid1_required")
    if not Path("/run/systemd/system").is_dir():
        raise HostProvisionError("systemd_runtime_required")
    machine_id = MACHINE_ID.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{32}", machine_id):
        raise HostProvisionError("host_machine_id_unavailable")
    return {
        "os_release_sha256": _file_sha(Path("/etc/os-release")),
        "architecture": platform.machine(),
        "machine_id_sha256": _sha(machine_id.encode()),
        "brain_uid": str(brain.pw_uid),
        "brain_gid": str(brain.pw_gid),
        "keyring_sha256": _file_sha(KEYRING),
    }


def _unit_inventory() -> dict[str, str]:
    units = _run(
        ["/usr/bin/systemctl", "list-units", "--all", "--type=service", "--plain", "--no-legend"]
    )
    files = _run(["/usr/bin/systemctl", "list-unit-files", "--type=service", "--no-legend"])
    result: dict[str, str] = {}
    for label, output in (("loaded", units.stdout), ("files", files.stdout)):
        for line in output.splitlines():
            fields = line.split()
            if fields and fields[0].lower().startswith(("brain", "memecoin-brain-")):
                result[f"{label}:{fields[0]}"] = " ".join(fields[1:])
    if result:
        raise HostProvisionError("preexisting_brain_unit_refused")
    return result


def _active_units() -> dict[str, str]:
    result = _run(
        [
            "/usr/bin/systemctl",
            "list-units",
            "--all",
            "--type=service",
            "--state=active",
            "--plain",
            "--no-legend",
        ]
    )
    if result.returncode:
        raise HostProvisionError("systemd_state_unavailable")
    return {
        fields[0]: " ".join(fields[1:])
        for line in result.stdout.splitlines()
        if (fields := line.split())
    }


def _installed() -> dict[str, str]:
    result = _run(
        ["/usr/bin/dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${db:Status-Status}\n"]
    )
    installed = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[2] == "installed":
            installed[fields[0].split(":", 1)[0]] = fields[1]
    return installed


def _candidate(package: str) -> str:
    result = _run(["/usr/bin/apt-cache", *_apt_base()[1:], "policy", package])
    match = re.search(r"^\s*Candidate:\s+(\S+)$", result.stdout, re.MULTILINE)
    if not match or match.group(1) == "(none)":
        raise HostProvisionError(f"candidate_missing:{package}")
    return match.group(1)


def _dependency_closure(requested: dict[str, str]) -> set[str]:
    result = _run(
        [
            "/usr/bin/apt-cache",
            *_apt_base()[1:],
            "depends",
            "--recurse",
            "--important",
            *sorted(requested),
        ]
    )
    closure = set(requested)
    for line in result.stdout.splitlines():
        match = re.match(r"^\s+(?:Pre-?Depends|Depends):\s+(\S+)", line)
        if match:
            package = match.group(1).strip("<>").split(":", 1)[0]
            if shared.PACKAGE_RE.fullmatch(package):
                closure.add(package)
    return closure


def _simulate(
    requested: dict[str, str], installed: dict[str, str]
) -> tuple[list[dict[str, str]], str]:
    args = [f"{name}={version}" for name, version in sorted(requested.items())]
    result = _run([*_apt_base(), "--simulate", "--no-install-recommends", "install", *args])
    closure = _dependency_closure(requested)
    transactions = []
    removals = []
    inst_lines = 0
    for line in result.stdout.splitlines():
        if line.startswith("Remv "):
            removals.append(line)
            continue
        if line.startswith("Inst "):
            inst_lines += 1
        match = re.match(r"^Inst (\S+)(?: \[([^]]+)\])? \((\S+)(?:\s|\))", line)
        if line.startswith("Inst ") and not match:
            raise HostProvisionError("unrecognized_solver_transaction")
        if not match:
            continue
        package, old_version, new_version = match.groups()
        package = package.split(":", 1)[0]
        prior = installed.get(package)
        if (old_version is not None or prior is not None) and package not in closure:
            raise HostProvisionError(f"unrelated_package_change_refused:{package}")
        if prior is not None:
            comparison = _run(
                ["/usr/bin/dpkg", "--compare-versions", prior, "gt", new_version], check=False
            )
            if comparison.returncode == 0:
                raise HostProvisionError(f"package_downgrade_refused:{package}")
            if comparison.returncode not in {0, 1}:
                raise HostProvisionError(f"package_version_compare_failed:{package}")
        transactions.append(
            {"package": package, "from_version": prior or "", "version": new_version}
        )
    if removals:
        raise HostProvisionError("package_removal_refused")
    if len(transactions) != inst_lines:
        raise HostProvisionError("incomplete_solver_transaction")
    if not transactions and any(name not in installed for name in requested):
        raise HostProvisionError("empty_package_plan")
    return transactions, result.stdout


def _index_inventory() -> dict[str, str]:
    path = LISTS
    result = {}
    for file in sorted(path.rglob("*")):
        rel = file.relative_to(path)
        if file.name == "lock" or "partial" in rel.parts:
            continue
        info = file.lstat()
        if info.st_mode & 0o022 or info.st_uid not in {0, pwd.getpwnam("_apt").pw_uid}:
            raise HostProvisionError("unsafe_index_file")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise HostProvisionError("unsafe_index_file")
        result[str(rel)] = _file_sha(file)
    return result


def _snapshot_path(snapshot: str) -> Path:
    return RECEIPTS / f"indexes-{snapshot}.json"


def _require_snapshot(snapshot: str) -> dict[str, Any]:
    path = _snapshot_path(snapshot)
    if not path.is_file() or path.is_symlink():
        raise HostProvisionError("snapshot_indexes_not_refreshed")
    receipt = _read_private(path)
    if receipt.get("keyring_sha256") != _file_sha(KEYRING):
        raise HostProvisionError("archive_keyring_drift")
    if receipt.get("host") != _host_identity():
        raise HostProvisionError("host_identity_drift")
    if receipt.get("brain_units") != _unit_inventory():
        raise HostProvisionError("brain_unit_state_drift")
    if receipt.get("source_sha256") != _file_sha(SOURCE) or SOURCE.read_bytes() != _sources(
        snapshot
    ):
        raise HostProvisionError("snapshot_source_drift")
    if receipt.get("indexes") != _index_inventory():
        raise HostProvisionError("snapshot_index_drift")
    return receipt


def preflight() -> dict[str, Any]:
    host = _host_identity()
    _require_apt_unlocked()
    if not Path("/usr/bin/apt-get").is_file() or not Path("/usr/bin/dpkg").is_file():
        raise HostProvisionError("working_apt_dpkg_required")
    units = _unit_inventory()
    return {
        "status": "ready",
        "host": host,
        "brain_units": units,
        "installed_package_state_sha256": _file_sha(DPKG_STATUS),
    }


def refresh_indexes(snapshot: str, source_commit: str) -> dict[str, Any]:
    _require_root()
    shared._validate_snapshot(snapshot)
    if not shared.COMMIT_RE.fullmatch(source_commit):
        raise HostProvisionError("invalid_source_commit")
    host_identity = _host_identity()
    _require_apt_unlocked()
    brain_units = _unit_inventory()
    _ensure_paths()
    fd = _locked()
    try:
        path = _snapshot_path(snapshot)
        if path.exists() or path.is_symlink():
            _require_snapshot(snapshot)
            return {"status": "already_refreshed", "receipt": str(path)}
        source_data = _sources(snapshot)
        if SOURCE.exists() or SOURCE.is_symlink():
            if SOURCE.is_symlink() or not SOURCE.is_file() or SOURCE.read_bytes() != source_data:
                raise HostProvisionError("apt_source_file_conflict")
        else:
            fd_source = os.open(SOURCE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd_source, "wb") as out:
                out.write(source_data)
                out.flush()
                os.fsync(out.fileno())
        result = _run([*_apt_base(), "update"])
        indexes = _index_inventory()
        if not indexes or not any(name.endswith("_InRelease") for name in indexes):
            raise HostProvisionError("signed_release_indexes_missing")
        if _host_identity() != host_identity:
            raise HostProvisionError("host_identity_changed_during_refresh")
        if _unit_inventory() != brain_units:
            raise HostProvisionError("brain_unit_state_changed_during_refresh")
        receipt = {
            "schema": "brain-linux-host-index-refresh-v1",
            "snapshot": snapshot,
            "source_commit": source_commit,
            "source_sha256": _file_sha(SOURCE),
            "tool_sha256": _file_sha(Path(__file__).resolve()),
            "keyring_sha256": _file_sha(KEYRING),
            "host": host_identity,
            "brain_units": brain_units,
            "indexes": indexes,
            "apt_version": _run(["/usr/bin/apt-get", "--version"]).stdout.splitlines()[0],
            "refresh_output_sha256": _sha((result.stdout + result.stderr).encode()),
            "signature_validation": "apt-secure-verified",
            "recorded_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        }
        _write_once(path, receipt)
        return {"status": "refreshed", "receipt": str(path), "indexes": len(indexes)}
    finally:
        os.close(fd)


def _capability(workspace: Path, commit: str, role: str) -> dict[str, Any]:
    report = shared._capability_report(workspace, commit, allow_git_missing=role == "source-build")
    return {"report": report, "fingerprint": shared._capability_fingerprint(report)}


def _tool_versions() -> dict[str, str]:
    return {
        "apt": _run(["/usr/bin/apt-get", "--version"]).stdout.splitlines()[0],
        "dpkg": _run(["/usr/bin/dpkg", "--version"]).stdout.splitlines()[0],
    }


def _create_plan_unlocked(
    role: str, snapshot: str, source_commit: str, workspace: Path
) -> dict[str, Any]:
    _require_apt_unlocked()
    if role not in shared.ROLES:
        raise HostProvisionError("role_not_approved")
    if not shared.COMMIT_RE.fullmatch(source_commit):
        raise HostProvisionError("invalid_source_commit")
    shared._validate_snapshot(snapshot)
    host = _host_identity()
    _unit_inventory()
    _require_snapshot(snapshot)
    capability = _capability(workspace, source_commit, role)
    installed = _installed()
    requested = {name: _candidate(name) for name in shared.ROLES[role]}
    transactions, simulation = _simulate(requested, installed)
    payload = {
        "schema": SCHEMA,
        "role": role,
        "snapshot": snapshot,
        "source_commit": source_commit,
        "workspace": str(workspace.resolve()),
        "host": host,
        "tool_sha256": _file_sha(Path(__file__).resolve()),
        "shared_tool_sha256": _file_sha(Path(shared.__file__).resolve()),
        "source_capability_tool_sha256": _file_sha(
            workspace.resolve() / "src/memecoin_operator/linux_capabilities.py"
        ),
        "capability_fingerprint": capability["fingerprint"],
        "package_tool_versions": _tool_versions(),
        "source_sha256": _file_sha(SOURCE),
        "index_receipt_sha256": _file_sha(_snapshot_path(snapshot)),
        "indexes": _index_inventory(),
        "dpkg_status_sha256": _file_sha(DPKG_STATUS),
        "installed": installed,
        "brain_units": {},
        "active_units": _active_units(),
        "requested": requested,
        "transaction": transactions,
        "simulation_sha256": _sha(simulation.encode()),
        "recorded_at_utc": dt.datetime.now(dt.UTC).isoformat(),
    }
    digest = _sha(_canonical(payload))
    plan = {**payload, "plan_sha256": digest}
    _ensure_paths()
    path = RECEIPTS / f"plan-{digest}.json"
    _write_once(path, plan)
    return {
        "status": "planned",
        "plan_sha256": digest,
        "plan": str(path),
        "transaction": transactions,
    }


def create_plan(role: str, snapshot: str, source_commit: str, workspace: Path) -> dict[str, Any]:
    _require_root()
    _ensure_paths()
    fd = _locked()
    try:
        return _create_plan_unlocked(role, snapshot, source_commit, workspace)
    finally:
        os.close(fd)


def _journal_snapshot(since: str) -> tuple[str, bool]:
    result = _run(
        ["/usr/bin/journalctl", "--since", since, "--output=json", "--no-pager"], check=False
    )
    if result.returncode not in {0, 1}:
        raise HostProvisionError("journal_capture_failed")
    service_activity = False
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
            message = str(entry.get("MESSAGE", ""))
        except ValueError:
            continue
        if re.search(
            r"\b(?:Starting|Started|Stopping|Stopped|Failed to start)\b.*(?:\.service|service)",
            message,
            re.IGNORECASE,
        ):
            service_activity = True
    # Keep only a digest and a boolean in receipts. Journals can contain unrelated
    # host details; retaining raw entries is unnecessary for this check.
    return _sha(result.stdout.encode()), service_activity


def apply_plan(plan_path: Path, plan_sha256: str) -> dict[str, Any]:
    _require_root()
    _host_identity()
    _require_apt_unlocked()
    _ensure_paths()
    fd = _locked()
    try:
        if plan_path.resolve().parent != RECEIPTS.resolve():
            raise HostProvisionError("plan_outside_root_receipts")
        plan = _read_private(plan_path)
        payload = {k: v for k, v in plan.items() if k != "plan_sha256"}
        digest = _sha(_canonical(payload))
        if digest != plan_sha256 or plan.get("plan_sha256") != plan_sha256:
            raise HostProvisionError("plan_digest_mismatch")
        applied = RECEIPTS / f"applied-{digest}.json"
        if applied.exists():
            old = _read_private(applied)
            if (
                old.get("status") == "applied"
                and old.get("dpkg_status_after_sha256") == _file_sha(DPKG_STATUS)
                and old.get("host") == _host_identity()
                and old.get("tool_sha256") == _file_sha(Path(__file__).resolve())
                and old.get("shared_tool_sha256") == _file_sha(Path(shared.__file__).resolve())
                and not _unit_inventory()
            ):
                return {"status": "already_applied", "receipt": str(applied)}
            raise HostProvisionError("prior_apply_receipt_conflict")
        intent = RECEIPTS / f"apply-intent-{digest}.json"
        if intent.exists() or intent.is_symlink():
            raise HostProvisionError("interrupted_apply_requires_manual_review")
        if plan.get("schema") != SCHEMA or plan.get("tool_sha256") != _file_sha(
            Path(__file__).resolve()
        ):
            raise HostProvisionError("tool_or_plan_schema_drift")
        if plan.get("shared_tool_sha256") != _file_sha(Path(shared.__file__).resolve()):
            raise HostProvisionError("shared_tool_drift")
        _require_snapshot(plan["snapshot"])
        if plan.get("host") != _host_identity():
            raise HostProvisionError("host_identity_drift")
        _unit_inventory()
        if plan.get("active_units") != _active_units():
            raise HostProvisionError("host_unit_state_drift")
        if (
            plan.get("dpkg_status_sha256") != _file_sha(DPKG_STATUS)
            or plan.get("installed") != _installed()
        ):
            raise HostProvisionError("installed_package_state_drift")
        if plan.get("package_tool_versions") != _tool_versions():
            raise HostProvisionError("package_tool_version_drift")
        if (
            plan.get("source_sha256") != _file_sha(SOURCE)
            or plan.get("indexes") != _index_inventory()
        ):
            raise HostProvisionError("snapshot_state_drift")
        role = plan.get("role")
        workspace = Path(plan.get("workspace", ""))
        if role not in shared.ROLES or set(plan.get("requested", {})) != set(shared.ROLES[role]):
            raise HostProvisionError("role_or_package_set_not_approved")
        if _file_sha(workspace / "src/memecoin_operator/linux_capabilities.py") != plan.get(
            "source_capability_tool_sha256"
        ):
            raise HostProvisionError("capability_tool_drift")
        capability = _capability(workspace, plan["source_commit"], role)
        if capability["fingerprint"] != plan.get("capability_fingerprint"):
            raise HostProvisionError("capability_state_drift")
        transactions, simulation = _simulate(plan["requested"], _installed())
        if transactions != plan.get("transaction") or _sha(simulation.encode()) != plan.get(
            "simulation_sha256"
        ):
            raise HostProvisionError("solver_plan_drift")
        if POLICY_RC.exists() or POLICY_RC.is_symlink():
            raise HostProvisionError("preexisting_policy_rc_d_refused")
        command = [
            *_apt_base(),
            "--assume-yes",
            "--no-install-recommends",
            "install",
            *[f"{name}={version}" for name, version in sorted(plan["requested"].items())],
        ]
        started = dt.datetime.now(dt.UTC).isoformat()
        journal_before_hash, journal_before_activity = _journal_snapshot("1 minute ago")
        _write_once(
            intent,
            {
                "schema": "brain-linux-host-apply-intent-v1",
                "plan_sha256": digest,
                "host": plan["host"],
                "dpkg_status_sha256": plan["dpkg_status_sha256"],
                "started_at_utc": started,
            },
        )
        policy = (
            b"#!/bin/sh\n# mitigation only; package scripts may still affect services\nexit 101\n"
        )
        policy_fd = os.open(POLICY_RC, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
        with os.fdopen(policy_fd, "wb") as out:
            out.write(policy)
            out.flush()
            os.fsync(out.fileno())
        error = None
        output = ""
        try:
            result = _run(command)
            output = result.stdout + result.stderr
        except HostProvisionError as exc:
            error = str(exc)
        finally:
            policy_matches = (
                POLICY_RC.is_file()
                and not POLICY_RC.is_symlink()
                and _file_sha(POLICY_RC) == _sha(policy)
            )
            if policy_matches:
                POLICY_RC.unlink()
        policy_cleaned = not POLICY_RC.exists() and not POLICY_RC.is_symlink()
        unit_effects = {"before": {}, "after": {}}
        after_error = None
        try:
            _unit_inventory()
        except HostProvisionError as exc:
            after_error = str(exc)
            unit_effects["after"] = {"error": after_error}
        journal_after_hash, service_journal_effect = _journal_snapshot(started)
        dpkg_after = _file_sha(DPKG_STATUS)
        installed_after = _installed()
        expected_after = dict(plan["installed"])
        for item in plan["transaction"]:
            expected_after[item["package"]] = item["version"]
        package_effect_error = installed_after != expected_after
        active_units_after = _active_units()
        unit_state_changed = active_units_after != plan["active_units"]
        outcome = (
            "apply_failed"
            if error
            else "unexpected_unit_or_journal_effect"
            if after_error or unit_state_changed or service_journal_effect or not policy_cleaned
            else "package_state_did_not_match_plan"
            if package_effect_error
            else "applied"
        )
        receipt = {
            "schema": "brain-linux-host-package-apply-v1",
            "plan_sha256": digest,
            "host": plan["host"],
            "source_commit": plan["source_commit"],
            "snapshot": plan["snapshot"],
            "source_sha256": plan["source_sha256"],
            "keyring_sha256": plan["host"]["keyring_sha256"],
            "tool_sha256": plan["tool_sha256"],
            "shared_tool_sha256": plan["shared_tool_sha256"],
            "index_receipt_sha256": plan["index_receipt_sha256"],
            "status": outcome,
            "started_at_utc": started,
            "finished_at_utc": dt.datetime.now(dt.UTC).isoformat(),
            "apply_error": error,
            "apt_output_sha256": _sha(output.encode()),
            "journal_before_sha256": journal_before_hash,
            "journal_after_sha256": journal_after_hash,
            "journal_service_activity_before": journal_before_activity,
            "journal_service_activity_after": service_journal_effect,
            "unit_effects": unit_effects,
            "dpkg_status_before_sha256": plan["dpkg_status_sha256"],
            "dpkg_status_after_sha256": dpkg_after,
            "installed_after": installed_after,
            "package_state_match": not package_effect_error,
            "active_units_before": plan["active_units"],
            "active_units_after": active_units_after,
            "service_journal_effect": service_journal_effect,
            "policy_rc_d_removed": policy_cleaned,
        }
        _write_once(applied, receipt)
        if outcome != "applied":
            raise HostProvisionError("package_apply_recorded:" + outcome)
        return {"status": outcome, "receipt": str(applied), "transaction": transactions}
    finally:
        os.close(fd)


def status() -> dict[str, Any]:
    host = _host_identity()
    return {
        "status": "ready",
        "host": host,
        "installed_package_state_sha256": _file_sha(DPKG_STATUS),
        "receipts": sorted(p.name for p in RECEIPTS.iterdir()) if RECEIPTS.is_dir() else [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("preflight")
    sub.add_parser("status")
    refresh = sub.add_parser("refresh")
    refresh.add_argument("--snapshot", required=True)
    refresh.add_argument("--source-commit", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--snapshot", required=True)
    plan.add_argument("--source-commit", required=True)
    plan.add_argument("--role", choices=sorted(shared.ROLES), required=True)
    plan.add_argument("--workspace", type=Path, required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--plan-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        result = (
            preflight()
            if args.action == "preflight"
            else status()
            if args.action == "status"
            else refresh_indexes(args.snapshot, args.source_commit)
            if args.action == "refresh"
            else create_plan(args.role, args.snapshot, args.source_commit, args.workspace)
            if args.action == "plan"
            else apply_plan(args.plan, args.plan_sha256)
        )
    except (HostProvisionError, shared.ProvisionError, OSError, ValueError, KeyError) as exc:
        print(
            json.dumps({"status": "refused", "reason": str(exc)}, sort_keys=True), file=sys.stderr
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
