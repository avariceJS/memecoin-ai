#!/usr/bin/env python3
"""Opt-in, snapshot-bound Ubuntu package bootstrap for disposable nspawn labs."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import platform
import pwd
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

SCHEMA = "brain-linux-package-plan-v1"
ROOT = Path("/var/lib/brain-lab-provision")
SOURCE_FILE = ROOT / "ubuntu.sources"
LISTS = ROOT / "lists"
ARCHIVES = ROOT / "archives"
RECEIPTS = ROOT / "receipts"
POLICY_RC = Path("/usr/sbin/policy-rc.d")
SNAPSHOT_RE = re.compile(r"^20\d{6}T\d{6}Z$")
COMMIT_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]+$")
ROLES: dict[str, tuple[str, ...]] = {
    "source-build": (
        "ca-certificates",
        "git",
        "make",
        "python3",
        "python3.12",
        "python3.12-venv",
    ),
    "inert-service-host": (
        "iproute2",
        "sudo",
        "systemd",
        "systemd-sysv",
        "udev",
        "util-linux",
    ),
}
SAFE_ENV = {
    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
    "LC_ALL": "C",
    "LANG": "C",
    "DEBIAN_FRONTEND": "noninteractive",
    "APT_LISTCHANGES_FRONTEND": "none",
    "HOME": "/root",
}


class ProvisionError(RuntimeError):
    """A precondition failed before a package transaction could proceed."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


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
        raise ProvisionError(f"command_failed:{Path(argv[0]).name}:{type(exc).__name__}") from exc
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip()[-1200:]
        raise ProvisionError(f"command_failed:{Path(argv[0]).name}:{result.returncode}:{detail}")
    return result


def _os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def _require_lab(*, mutating: bool) -> None:
    if os.geteuid() != 0:
        raise ProvisionError("root_required")
    if platform.machine() not in {"x86_64", "amd64"}:
        raise ProvisionError("unsupported_architecture")
    release = _os_release()
    if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04":
        raise ProvisionError("ubuntu_24_04_required")
    marker = Path("/run/systemd/container")
    if not marker.exists() or marker.read_text(encoding="utf-8").strip() != "systemd-nspawn":
        raise ProvisionError("systemd_nspawn_lab_required")
    detected = _run(["/usr/bin/systemd-detect-virt", "--container"], check=False)
    if detected.returncode or detected.stdout.strip() != "systemd-nspawn":
        raise ProvisionError("systemd_nspawn_lab_required")
    if mutating and not shutil.which("apt-get"):
        raise ProvisionError("apt_get_missing")


def _ensure_private_dir(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        path.mkdir(mode=0o700)
        info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ProvisionError(f"unsafe_directory:{path}")


def _ensure_paths() -> None:
    _ensure_private_dir(ROOT) if ROOT.exists() or ROOT.is_symlink() else None
    if not ROOT.exists() and not ROOT.is_symlink():
        ROOT.mkdir(mode=0o700)
    for path in (LISTS, ARCHIVES, RECEIPTS):
        _ensure_private_dir(path) if path.exists() or path.is_symlink() else path.mkdir(mode=0o700)


def _apt_base() -> list[str]:
    return [
        "/usr/bin/apt-get",
        "-o",
        "Dir::Etc::main=/dev/null",
        "-o",
        "Dir::Etc::parts=-",
        "-o",
        f"Dir::Etc::sourcelist={SOURCE_FILE}",
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
    ]


def _sources(snapshot: str) -> bytes:
    return (
        "Types: deb\n"
        "URIs: https://archive.ubuntu.com/ubuntu\n"
        "Suites: noble noble-updates\n"
        "Components: main universe\n"
        "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        f"Snapshot: {snapshot}\n\n"
        "Types: deb\n"
        "URIs: https://security.ubuntu.com/ubuntu\n"
        "Suites: noble-security\n"
        "Components: main universe\n"
        "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        f"Snapshot: {snapshot}\n"
    ).encode()


def _index_inventory() -> dict[str, str]:
    result: dict[str, str] = {}
    if not LISTS.is_dir():
        return result
    allowed_owners = {LISTS.stat().st_uid, pwd.getpwnam("_apt").pw_uid}
    for path in sorted(LISTS.rglob("*")):
        relative = path.relative_to(LISTS)
        if path.name == "lock" or "partial" in relative.parts:
            continue
        info = path.lstat()
        if info.st_uid not in allowed_owners or info.st_mode & 0o022:
            raise ProvisionError(f"unsafe_index_file:{path.name}")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ProvisionError(f"unsafe_index_file:{path.name}")
        result[str(relative)] = _file_sha(path)
    return result


def _snapshot_receipt(snapshot: str) -> Path:
    return RECEIPTS / f"indexes-{snapshot}.json"


def _validate_snapshot(value: str) -> str:
    if not SNAPSHOT_RE.fullmatch(value):
        raise ProvisionError("invalid_snapshot_id")
    try:
        dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ")
    except ValueError as exc:
        raise ProvisionError("invalid_snapshot_id") from exc
    return value


def _write_create_only(path: Path, value: dict[str, Any]) -> None:
    data = _canonical(value) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _read_private_json(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ProvisionError(f"unsafe_receipt_file:{path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def refresh_indexes(snapshot: str, source_commit: str) -> dict[str, Any]:
    _require_lab(mutating=True)
    _validate_snapshot(snapshot)
    if not COMMIT_RE.fullmatch(source_commit):
        raise ProvisionError("invalid_source_commit")
    _ensure_paths()
    receipt_path = _snapshot_receipt(snapshot)
    if receipt_path.exists() or receipt_path.is_symlink():
        prior = _read_private_json(receipt_path)
        if (
            prior.get("source_sha256") == _sha(_sources(snapshot))
            and prior.get("indexes") == _index_inventory()
        ):
            return {"status": "already_refreshed", "receipt": str(receipt_path)}
        raise ProvisionError("snapshot_refresh_receipt_conflict")
    source_bytes = _sources(snapshot)
    if SOURCE_FILE.exists() or SOURCE_FILE.is_symlink():
        info = SOURCE_FILE.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o077
            or SOURCE_FILE.read_bytes() != source_bytes
        ):
            raise ProvisionError("apt_source_file_conflict")
    else:
        fd = os.open(SOURCE_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(source_bytes)
            output.flush()
            os.fsync(output.fileno())
    command = [*_apt_base(), "update"]
    result = _run(command)
    indexes = _index_inventory()
    if not indexes or not any(name.endswith("_InRelease") for name in indexes):
        raise ProvisionError("signed_release_indexes_missing")
    receipt = {
        "schema": "brain-linux-apt-index-refresh-v1",
        "recorded_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "snapshot": snapshot,
        "source_commit": source_commit,
        "tool_sha256": _file_sha(Path(__file__).resolve()),
        "source_sha256": _file_sha(SOURCE_FILE),
        "indexes": indexes,
        "apt_version": _run(["/usr/bin/apt-get", "--version"]).stdout.splitlines()[0],
        "refresh_output_sha256": _sha((result.stdout + result.stderr).encode()),
        "signature_validation": "apt-secure-verified",
    }
    _write_create_only(receipt_path, receipt)
    return {"status": "refreshed", "receipt": str(receipt_path), "indexes": len(indexes)}


def _require_indexes(snapshot: str) -> dict[str, Any]:
    path = _snapshot_receipt(snapshot)
    if not path.is_file() or path.is_symlink():
        raise ProvisionError("snapshot_indexes_not_refreshed")
    receipt = _read_private_json(path)
    if receipt.get("source_sha256") != _sha(_sources(snapshot)):
        raise ProvisionError("snapshot_source_drift")
    if receipt.get("indexes") != _index_inventory():
        raise ProvisionError("snapshot_index_drift")
    return receipt


def _unit_state() -> list[str]:
    result = _run(
        ["/usr/bin/systemctl", "list-units", "--all", "--type=service", "--plain", "--no-legend"],
        check=False,
    )
    if result.returncode:
        raise ProvisionError("systemd_state_unavailable")
    units = []
    for line in result.stdout.splitlines():
        columns = line.split()
        if columns and columns[0].startswith("brain"):
            units.append(line.strip())
    active = [row for row in units if len(row.split()) > 2 and row.split()[2] == "active"]
    if active:
        raise ProvisionError("brain_units_active")
    return units


def _installed_versions() -> dict[str, str]:
    result = _run(
        ["/usr/bin/dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${db:Status-Status}\n"],
        check=False,
    )
    installed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[2] == "installed":
            installed[fields[0].split(":", 1)[0]] = fields[1]
    return installed


def _package_tool_versions() -> dict[str, str]:
    return {
        "apt": _run(["/usr/bin/apt-get", "--version"]).stdout.splitlines()[0],
        "dpkg": _run(["/usr/bin/dpkg", "--version"]).stdout.splitlines()[0],
    }


def _candidate(package: str) -> str:
    result = _run(["/usr/bin/apt-cache", *_apt_base()[1:], "policy", package])
    match = re.search(r"^\s*Candidate:\s+(\S+)$", result.stdout, re.MULTILINE)
    if not match or match.group(1) == "(none)":
        raise ProvisionError(f"candidate_missing:{package}")
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
        match = re.match(r"^\s+(?:Pre)?Depends:\s+(\S+)", line)
        if match:
            package = match.group(1).strip("<>").split(":", 1)[0]
            if PACKAGE_RE.fullmatch(package):
                closure.add(package)
    return closure


def _simulate(
    requested: dict[str, str], installed: dict[str, str]
) -> tuple[list[dict[str, str]], str]:
    package_args = [f"{name}={version}" for name, version in sorted(requested.items())]
    result = _run([*_apt_base(), "--simulate", "--no-install-recommends", "install", *package_args])
    transactions: list[dict[str, str]] = []
    removals = []
    dependency_closure = _dependency_closure(requested)
    for line in result.stdout.splitlines():
        if line.startswith("Remv "):
            removals.append(line)
            continue
        match = re.match(r"^Inst (\S+)(?: \[([^]]+)\])? \((\S+)(?:\s|\))", line)
        if not match:
            continue
        package, old_version, new_version = match.groups()
        package = package.split(":", 1)[0]
        installed_version = installed.get(package)
        if (
            old_version is not None or installed_version is not None
        ) and package not in dependency_closure:
            raise ProvisionError(f"unrelated_package_change_refused:{package}")
        if installed_version is not None:
            comparison = _run(
                ["/usr/bin/dpkg", "--compare-versions", installed_version, "gt", new_version],
                check=False,
            )
            if comparison.returncode == 0:
                raise ProvisionError(f"package_downgrade_refused:{package}")
            if comparison.returncode not in {0, 1}:
                raise ProvisionError(f"package_version_compare_failed:{package}")
        transactions.append(
            {"package": package, "from_version": installed_version or "", "version": new_version}
        )
    if removals:
        raise ProvisionError("package_removal_refused")
    if not transactions and any(name not in installed for name in requested):
        raise ProvisionError("empty_package_plan")
    return transactions, result.stdout


def _validate_source_checkout(report: dict[str, Any], *, allow_git_missing: bool) -> None:
    check = report.get("checks", {}).get("source_checkout", {})
    if check.get("status") == "available":
        return
    if (
        allow_git_missing
        and check.get("status") == "absent"
        and check.get("reason") == "git_missing"
    ):
        return
    raise ProvisionError("brain_source_checkout_not_clean_at_requested_commit")


def _capability_report(
    workspace: Path, source_commit: str, *, allow_git_missing: bool = False
) -> dict[str, Any]:
    checkout = workspace.resolve()
    script = checkout / "src/memecoin_operator/linux_capabilities.py"
    if not checkout.is_dir() or not script.is_file():
        raise ProvisionError("brain_source_checkout_required")
    result = _run(
        [
            "/usr/bin/python3.12",
            "-B",
            str(script),
            "report",
            "--source-commit",
            source_commit,
            "--workspace",
            str(checkout),
        ],
        timeout=60,
    )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProvisionError("capability_report_invalid_json") from exc
    if report.get("source_commit") != source_commit:
        raise ProvisionError("capability_report_source_commit_mismatch")
    _validate_source_checkout(report, allow_git_missing=allow_git_missing)
    return report


def _capability_fingerprint(report: dict[str, Any]) -> dict[str, Any]:
    checks = {}
    for name, value in report.get("checks", {}).items():
        checks[name] = {
            key: item
            for key, item in value.items()
            if key not in {"available_bytes", "observed_at"}
        }
    return {
        "host": report.get("host"),
        "checks": checks,
        "roles": report.get("roles"),
        "unit": report.get("unit"),
    }


def _plan_payload(role: str, snapshot: str, source_commit: str, workspace: Path) -> dict[str, Any]:
    _require_lab(mutating=False)
    if not COMMIT_RE.fullmatch(source_commit):
        raise ProvisionError("invalid_source_commit")
    _validate_snapshot(snapshot)
    _require_indexes(snapshot)
    if role not in ROLES:
        raise ProvisionError("role_not_approved")
    capability_before = _capability_report(
        workspace, source_commit, allow_git_missing=role == "source-build"
    )
    units = _unit_state()
    installed = _installed_versions()
    requested = {name: _candidate(name) for name in ROLES[role]}
    transactions, simulation = _simulate(requested, installed)
    status_path = Path("/var/lib/dpkg/status")
    return {
        "schema": SCHEMA,
        "role": role,
        "snapshot": snapshot,
        "source_commit": source_commit,
        "source_workspace": str(workspace.resolve()),
        "source_capability_tool_sha256": _file_sha(
            workspace.resolve() / "src/memecoin_operator/linux_capabilities.py"
        ),
        "capability_report_before": capability_before,
        "capability_fingerprint_before": _capability_fingerprint(capability_before),
        "tool_sha256": _file_sha(Path(__file__).resolve()),
        "os_release_sha256": _file_sha(Path("/etc/os-release")),
        "architecture": platform.machine(),
        "package_tool_versions": _package_tool_versions(),
        "source_sha256": _file_sha(SOURCE_FILE),
        "index_refresh_receipt_sha256": _file_sha(_snapshot_receipt(snapshot)),
        "indexes": _index_inventory(),
        "dpkg_status_sha256": _file_sha(status_path),
        "brain_units_before": units,
        "requested": requested,
        "transaction": transactions,
        "simulation_sha256": _sha(simulation.encode()),
        "recorded_at_utc": dt.datetime.now(dt.UTC).isoformat(),
    }


def create_plan(role: str, snapshot: str, source_commit: str, workspace: Path) -> dict[str, Any]:
    _ensure_paths()
    payload = _plan_payload(role, snapshot, source_commit, workspace)
    digest = _sha(_canonical(payload))
    plan = {**payload, "plan_sha256": digest}
    path = RECEIPTS / f"plan-{digest}.json"
    _write_create_only(path, plan)
    return {
        "status": "planned",
        "plan_sha256": digest,
        "plan": str(path),
        "transaction": payload["transaction"],
    }


def apply_plan(plan_path: Path, plan_sha256: str) -> dict[str, Any]:
    _require_lab(mutating=True)
    _ensure_paths()
    if plan_path.resolve().parent != RECEIPTS.resolve():
        raise ProvisionError("plan_outside_root_receipts")
    plan_info = plan_path.lstat()
    if not stat.S_ISREG(plan_info.st_mode) or plan_info.st_uid != 0 or plan_info.st_mode & 0o077:
        raise ProvisionError("unsafe_plan_file")
    plan = _read_private_json(plan_path)
    payload = {key: value for key, value in plan.items() if key != "plan_sha256"}
    digest = _sha(_canonical(payload))
    if digest != plan_sha256 or plan.get("plan_sha256") != plan_sha256:
        raise ProvisionError("plan_digest_mismatch")
    applied_path = RECEIPTS / f"applied-{digest}.json"
    if applied_path.exists() or applied_path.is_symlink():
        prior = _read_private_json(applied_path)
        if prior.get("status") == "applied" and _file_sha(
            Path("/var/lib/dpkg/status")
        ) == prior.get("dpkg_status_after_sha256"):
            return {"status": "already_applied", "receipt": str(applied_path)}
        raise ProvisionError("prior_apply_receipt_conflict")
    if plan.get("tool_sha256") != _file_sha(Path(__file__).resolve()):
        raise ProvisionError("tool_drift")
    _require_indexes(plan["snapshot"])
    if plan.get("source_sha256") != _file_sha(SOURCE_FILE):
        raise ProvisionError("source_drift")
    if plan.get("os_release_sha256") != _file_sha(Path("/etc/os-release")):
        raise ProvisionError("os_release_drift")
    if plan.get("architecture") != platform.machine():
        raise ProvisionError("architecture_drift")
    if plan.get("package_tool_versions") != _package_tool_versions():
        raise ProvisionError("package_tool_version_drift")
    if plan.get("dpkg_status_sha256") != _file_sha(Path("/var/lib/dpkg/status")):
        raise ProvisionError("installed_package_state_drift")
    if plan.get("brain_units_before") != _unit_state():
        raise ProvisionError("brain_unit_state_drift")
    role = plan.get("role")
    if role not in ROLES or set(plan["requested"]) != set(ROLES[role]):
        raise ProvisionError("role_or_package_set_not_approved")
    workspace = Path(plan["source_workspace"])
    if _file_sha(workspace / "src/memecoin_operator/linux_capabilities.py") != plan.get(
        "source_capability_tool_sha256"
    ):
        raise ProvisionError("capability_tool_drift")
    capability_before = _capability_report(
        workspace, plan["source_commit"], allow_git_missing=role == "source-build"
    )
    if _capability_fingerprint(capability_before) != plan.get("capability_fingerprint_before"):
        raise ProvisionError("capability_state_drift")
    installed = _installed_versions()
    transactions, simulation = _simulate(plan["requested"], installed)
    if transactions != plan.get("transaction") or _sha(simulation.encode()) != plan.get(
        "simulation_sha256"
    ):
        raise ProvisionError("solver_plan_drift")
    command = [
        *_apt_base(),
        "--assume-yes",
        "--no-install-recommends",
        "install",
        *[f"{name}={version}" for name, version in sorted(plan["requested"].items())],
    ]
    if POLICY_RC.exists() or POLICY_RC.is_symlink():
        raise ProvisionError("preexisting_policy_rc_d_refused")
    policy_data = (
        b"#!/bin/sh\n"
        b"# brain-linux-lab-provisioner: deny service activation during package apply\n"
        b"exit 101\n"
    )
    fd = os.open(POLICY_RC, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
    with os.fdopen(fd, "wb") as output:
        output.write(policy_data)
        output.flush()
        os.fsync(output.fileno())
    result: subprocess.CompletedProcess[str] | None = None
    apply_error: ProvisionError | None = None
    try:
        result = _run(command)
    except ProvisionError as exc:
        apply_error = exc
    finally:
        if (
            POLICY_RC.is_file()
            and not POLICY_RC.is_symlink()
            and _file_sha(POLICY_RC) == _sha(policy_data)
        ):
            POLICY_RC.unlink()
    units_after = _unit_state()
    capability_after = _capability_report(workspace, plan["source_commit"])
    if (
        role == "source-build"
        and capability_after.get("roles", {}).get("source_build", {}).get("status") != "available"
    ):
        apply_error = apply_error or ProvisionError("source_build_role_not_ready_after_apply")
    status = _file_sha(Path("/var/lib/dpkg/status"))
    if apply_error is not None:
        outcome = "apply_failed"
    elif units_after != plan["brain_units_before"]:
        outcome = "unit_state_changed"
    else:
        outcome = "applied"
    receipt = {
        "schema": "brain-linux-package-apply-v1",
        "plan_sha256": digest,
        "tool_sha256": _file_sha(Path(__file__).resolve()),
        "recorded_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "status": outcome,
        "requested": plan["requested"],
        "transaction": transactions,
        "apt_output_sha256": _sha(
            ((result.stdout + result.stderr) if result else str(apply_error)).encode()
        ),
        "brain_units_before": plan["brain_units_before"],
        "brain_units_after": units_after,
        "capability_report_before": capability_before,
        "capability_report_after": capability_after,
        "capability_fingerprint_after": _capability_fingerprint(capability_after),
        "dpkg_status_before_sha256": plan["dpkg_status_sha256"],
        "dpkg_status_after_sha256": status,
        "installed_requested": {
            name: _installed_versions().get(name) for name in plan["requested"]
        },
    }
    _write_create_only(applied_path, receipt)
    if apply_error is not None:
        raise apply_error
    if outcome != "applied":
        raise ProvisionError("brain_unit_state_changed_during_apply")
    return {"status": outcome, "receipt": str(applied_path), "transaction": transactions}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("refresh-indexes", "plan"):
        item = sub.add_parser(name)
        item.add_argument("--lab-only", action="store_true", required=True)
        item.add_argument("--snapshot", required=True)
        item.add_argument("--source-commit", required=True)
        if name == "plan":
            item.add_argument("--role", choices=sorted(ROLES), required=True)
            item.add_argument("--workspace", type=Path, required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--lab-only", action="store_true", required=True)
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--plan-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "refresh-indexes":
            result = refresh_indexes(args.snapshot, args.source_commit)
        elif args.action == "plan":
            result = create_plan(args.role, args.snapshot, args.source_commit, args.workspace)
        else:
            result = apply_plan(args.plan, args.plan_sha256)
    except (ProvisionError, OSError, ValueError, KeyError) as exc:
        print(
            json.dumps({"status": "refused", "reason": str(exc)}, sort_keys=True), file=sys.stderr
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
