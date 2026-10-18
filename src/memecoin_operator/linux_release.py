"""Create and verify inert, content-addressed Linux release candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from memecoin_operator import linux_service

RELEASE_BASE = Path("/opt/brain/releases")
UNIT_BASE = Path("/etc/systemd/system")
STATE_BASE = Path("/var/lib/brain-lab/releases")
SCHEMA = "brain-linux-release-v1"
MAX_WHEEL_BYTES = 100 * 1024 * 1024
SAFE_WHEEL_NAME = re.compile(
    r"^[A-Za-z0-9_]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+\.whl$"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(argv: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        check=True,
        capture_output=capture,
        text=True,
        timeout=120,
        env={
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": "/root",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PIP_NO_INPUT": "1",
            "PIP_CONFIG_FILE": "/dev/null",
        },
    )


def _interpreter(python: Path) -> dict[str, str]:
    resolved = python.resolve(strict=True)
    code = (
        "import json,sys,sysconfig;"
        "print(json.dumps({'version':sys.version.split()[0],"
        "'soabi':sysconfig.get_config_var('SOABI') or ''}))"
    )
    result = _run([str(resolved), "-I", "-c", code])
    try:
        details = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError("interpreter_probe_malformed") from exc
    if not isinstance(details, dict) or not details.get("version"):
        raise RuntimeError("interpreter_probe_malformed")
    return {"path": str(resolved), "version": details["version"], "soabi": details["soabi"]}


def _identity(wheel_sha: str, interpreter: dict[str, str]) -> str:
    body = json.dumps(
        {"schema": SCHEMA, "wheel_sha256": wheel_sha, "interpreter": interpreter},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(body).hexdigest()


def _inventory(root: Path) -> tuple[list[dict[str, str]], str]:
    entries: list[dict[str, str]] = []
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        base = Path(parent)
        for name in [*dirs, *files]:
            path = base / name
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                item = {"path": relative, "kind": "symlink", "target": os.readlink(path)}
            elif stat.S_ISDIR(metadata.st_mode):
                item = {"path": relative, "kind": "directory"}
            elif stat.S_ISREG(metadata.st_mode):
                item = {"path": relative, "kind": "file", "sha256": _sha256(path)}
            else:
                raise RuntimeError("release_contains_special_file")
            entries.append(item)
    entries.sort(key=lambda item: item["path"])
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return entries, hashlib.sha256(encoded).hexdigest()


def _seal(root: Path, group_id: int) -> None:
    for parent, dirs, files in os.walk(root, topdown=False, followlinks=False):
        for name in files:
            path = Path(parent) / name
            if path.is_symlink():
                os.chown(path, 0, group_id, follow_symlinks=False)
                continue
            mode = path.stat().st_mode
            path.chmod(0o555 if mode & 0o111 else 0o444)
            os.chown(path, 0, group_id)
        for name in dirs:
            path = Path(parent) / name
            if path.is_symlink():
                os.chown(path, 0, group_id, follow_symlinks=False)
            else:
                path.chmod(0o555)
                os.chown(path, 0, group_id)
    root.chmod(0o555)
    os.chown(root, 0, group_id)


def _verify_permissions(root: Path, unit_path: Path) -> None:
    group_id = pwd.getpwnam("brain").pw_gid
    for parent, dirs, files in os.walk(root, followlinks=False):
        for name in [".", *dirs, *files]:
            path = Path(parent) if name == "." else Path(parent) / name
            metadata = path.lstat()
            if metadata.st_uid != 0 or metadata.st_gid != group_id:
                raise RuntimeError("release_ownership_mismatch")
            if not stat.S_ISLNK(metadata.st_mode) and metadata.st_mode & 0o022:
                raise RuntimeError("release_writable_by_service_group")
    unit_stat = unit_path.stat()
    if unit_stat.st_uid != 0 or unit_stat.st_mode & 0o022:
        raise RuntimeError("release_unit_permissions_mismatch")


def _unit_properties(unit: str) -> dict[str, str]:
    names = (
        "FragmentPath",
        "DropInPaths",
        "NeedDaemonReload",
        "ExecStart",
        "User",
        "Group",
        "PrivateNetwork",
        "WorkingDirectory",
        "ReadWritePaths",
        "StateDirectory",
        "ProtectSystem",
        "Restart",
    )
    result = _run(
        ["systemctl", "show", "--no-pager", *[f"--property={name}" for name in names], unit]
    )
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in names or key in fields:
            raise RuntimeError("release_effective_unit_malformed")
        fields[key] = value
    if set(fields) != set(names):
        raise RuntimeError("release_effective_unit_malformed")
    return fields


def _verify_effective_unit(unit: str, root: Path, state_root: Path) -> None:
    fields = _unit_properties(unit)
    expected = {
        "FragmentPath": str(UNIT_BASE / unit),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "User": "brain",
        "Group": "brain",
        "PrivateNetwork": "yes",
        "WorkingDirectory": str(root),
        "ReadWritePaths": str(STATE_BASE),
        "StateDirectory": "brain-lab",
        "ProtectSystem": "strict",
        "Restart": "no",
    }
    if any(fields[key] != value for key, value in expected.items()):
        raise RuntimeError("release_effective_unit_mismatch")
    match = re.search(r"(?:^| )argv\[\]=(.*?) ; ignore_errors=", fields["ExecStart"])
    if match is None:
        raise RuntimeError("release_effective_execstart_malformed")
    try:
        argv = shlex.split(match.group(1))
    except ValueError as exc:
        raise RuntimeError("release_effective_execstart_malformed") from exc
    expected_argv = [
        str(root / "venv/bin/python"),
        "-I",
        "-m",
        "memecoin_operator.runtime",
        "--root",
        str(state_root),
        "--port",
        "18792",
    ]
    if argv != expected_argv:
        raise RuntimeError("release_effective_execstart_mismatch")


def _verify_lab_state(root: Path, release_id: str) -> None:
    state_root = STATE_BASE / release_id / "operator"
    try:
        state_root.relative_to(STATE_BASE)
    except ValueError as exc:
        raise RuntimeError("release_state_root_mismatch") from exc
    if state_root.is_symlink() or not state_root.is_dir():
        raise RuntimeError("release_lab_state_missing")
    try:
        config = json.loads((state_root / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("release_lab_config_invalid") from exc
    if (
        not isinstance(config, dict)
        or config.get("mode") != "operator"
        or config.get("real_execution_enabled") is not False
        or config.get("paid_budget_usd") != 0
    ):
        raise RuntimeError("release_lab_config_unsafe")
    database = state_root / "operator.sqlite"
    if database.is_symlink() or not database.is_file():
        raise RuntimeError("release_lab_database_missing")
    try:
        db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "jobs" not in tables or db.execute("SELECT count(*) FROM jobs").fetchone()[0] != 0:
                raise RuntimeError("release_lab_state_not_idle")
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("release_lab_database_invalid")
        finally:
            db.close()
    except sqlite3.Error as exc:
        raise RuntimeError("release_lab_database_invalid") from exc


def _commit_and_clean(source: Path, expected: str) -> None:
    brain_uid = pwd.getpwnam("brain").pw_uid
    if source.stat().st_uid != brain_uid:
        raise RuntimeError("source_owner_mismatch")
    git_prefix = ["/usr/sbin/runuser", "-u", "brain", "--", "git", "-C", str(source)]
    result = _run([*git_prefix, "rev-parse", "HEAD"])
    if result.stdout.strip() != expected:
        raise RuntimeError("source_commit_mismatch")
    dirty = _run([*git_prefix, "status", "--porcelain", "--untracked-files=all"])
    if dirty.stdout:
        raise RuntimeError("source_checkout_dirty")


def stage(
    source: Path,
    wheel: Path,
    source_commit: str,
    expected_wheel_sha256: str,
    python: Path = Path("/usr/bin/python3"),
) -> dict[str, str]:
    """Build a root-owned, service-read-only venv and unique disabled unit."""
    if os.geteuid() != 0:
        raise PermissionError("stage_requires_root")
    if not re.fullmatch(r"[0-9a-f]{40,64}", source_commit):
        raise ValueError("invalid_source_commit")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_wheel_sha256):
        raise ValueError("invalid_wheel_sha256")
    source = source.resolve(strict=True)
    wheel = wheel.resolve(strict=True)
    if (
        wheel.stat().st_size > MAX_WHEEL_BYTES
        or not wheel.is_file()
        or not SAFE_WHEEL_NAME.fullmatch(wheel.name)
    ):
        raise ValueError("invalid_wheel_file")
    _commit_and_clean(source, source_commit)
    if _sha256(wheel) != expected_wheel_sha256:
        raise RuntimeError("wheel_sha256_mismatch")
    interpreter = _interpreter(python)
    release_id = _identity(expected_wheel_sha256, interpreter)
    release_root = RELEASE_BASE / release_id
    unit_name = f"memecoin-brain-{release_id}.service"
    unit_path = UNIT_BASE / unit_name
    if RELEASE_BASE.is_symlink() or (RELEASE_BASE.exists() and not RELEASE_BASE.is_dir()):
        raise RuntimeError("release_root_unsafe")
    RELEASE_BASE.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(release_root) or os.path.lexists(unit_path):
        raise RuntimeError("release_already_exists")
    user = pwd.getpwnam("brain")
    builder_name = f"brain-stage-{release_id[:12]}"
    staging = Path(tempfile.mkdtemp(prefix=f".stage-{release_id[:12]}-", dir=RELEASE_BASE))
    builder_root = staging / "build"
    builder_root.mkdir(mode=0o700)
    wheel_input = staging / wheel.name
    venv = builder_root / "venv"
    builder_created = False
    try:
        _run(
            [
                "useradd",
                "--system",
                "--no-create-home",
                "--home-dir",
                "/nonexistent",
                "--shell",
                "/usr/sbin/nologin",
                builder_name,
            ]
        )
        builder_created = True
        builder = pwd.getpwnam(builder_name)
        os.chown(builder_root, builder.pw_uid, builder.pw_gid)
        shutil.copyfile(wheel, wheel_input)
        wheel_input.chmod(0o444)
        os.chown(wheel_input, 0, 0)
        if _sha256(wheel_input) != expected_wheel_sha256:
            raise RuntimeError("staged_wheel_sha256_mismatch")
        _run(
            [
                "/usr/sbin/runuser",
                "-u",
                builder_name,
                "--",
                interpreter["path"],
                "-m",
                "venv",
                str(venv),
            ]
        )
        _run(
            [
                "/usr/sbin/runuser",
                "-u",
                builder_name,
                "--",
                str(venv / "bin/python"),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--no-compile",
                "--no-cache-dir",
                str(wheel_input),
            ]
        )
        if _sha256(wheel_input) != expected_wheel_sha256:
            raise RuntimeError("staged_wheel_changed_during_install")
        probe = _run(
            [
                "/usr/sbin/runuser",
                "-u",
                builder_name,
                "--",
                str(venv / "bin/python"),
                "-I",
                "-c",
                "import importlib.metadata,memecoin_operator.runtime;"
                "print(importlib.metadata.version('memecoin-ai'))",
            ]
        )
        version = probe.stdout.strip()
        if not version:
            raise RuntimeError("release_import_probe_failed")
        wheel_input.unlink()
        inventory, inventory_sha = _inventory(venv)
        release_root.mkdir(mode=0o755)
        os.replace(venv, release_root / "venv")
        builder_root.rmdir()
        staging.rmdir()
        unit_text = linux_service.render_unit(
            release_root / "venv/bin/python",
            STATE_BASE / release_id / "operator",
            18792,
            code_root=release_root,
        )
        unit_text = unit_text.replace(
            "Description=Disposable Brain operator lab (manual activation only)",
            f"Description=Brain release {release_id} (manual activation only)",
        )
        manifest = {
            "schema": SCHEMA,
            "release_id": release_id,
            "source_commit": source_commit,
            "wheel_sha256": expected_wheel_sha256,
            "package_version": version,
            "interpreter": interpreter,
            "installed_inventory_sha256": inventory_sha,
            "installed_inventory": inventory,
            "unit_name": unit_name,
            "unit_sha256": hashlib.sha256(unit_text.encode()).hexdigest(),
            "install_policy": "create-only-disabled-no-activation",
        }
        (release_root / "release-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        os.chown(release_root / "release-manifest.json", 0, user.pw_gid)
        (release_root / "release-manifest.json").chmod(0o444)
        _seal(release_root, user.pw_gid)
        pin_dir = RELEASE_BASE / ".pins"
        pin_dir.mkdir(mode=0o755, exist_ok=True)
        if os.path.lexists(pin_dir / f"{release_id}.sha256"):
            raise RuntimeError("release_pin_already_exists")
        manifest_sha = _sha256(release_root / "release-manifest.json")
        pin_path = pin_dir / f"{release_id}.sha256"
        with pin_path.open("x", encoding="ascii") as handle:
            handle.write(f"{manifest_sha}  release-manifest.json\n")
        pin_path.chmod(0o444)
        os.chown(pin_path, 0, 0)
        linux_service.install(unit_text, unit_path=unit_path, unit=unit_name)
    except BaseException:
        pin_path = RELEASE_BASE / ".pins" / f"{release_id}.sha256"
        pin_path.unlink(missing_ok=True)
        if release_root.exists() and (release_root / "venv").exists():
            candidate = release_root
            if staging.exists():
                staging.rmdir()
        else:
            candidate = staging if staging.exists() else None
            if release_root.exists():
                release_root.rmdir()
        if candidate is not None:
            incomplete = RELEASE_BASE / f".incomplete-{release_id[:12]}-{os.getpid()}"
            if candidate == release_root:
                candidate.chmod(0o755)
            else:
                os.chown(candidate, 0, user.pw_gid)
            os.replace(candidate, incomplete)
            (incomplete / "INCOMPLETE").write_text(
                "release staging did not complete\n", encoding="utf-8"
            )
            _seal(incomplete, user.pw_gid)
        raise
    finally:
        if builder_created:
            subprocess.run(
                ["userdel", builder_name],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
    return {"release_id": release_id, "unit": unit_name, "release_path": str(release_root)}


def verify(release_id: str) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{64}", release_id):
        raise ValueError("invalid_release_id")
    root = RELEASE_BASE / release_id
    unit_name = f"memecoin-brain-{release_id}.service"
    unit_path = UNIT_BASE / unit_name
    if root.is_symlink() or not root.is_dir() or unit_path.is_symlink() or not unit_path.is_file():
        raise RuntimeError("release_or_unit_missing")
    manifest = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA or manifest.get("release_id") != release_id:
        raise RuntimeError("release_manifest_mismatch")
    wheel_sha = manifest.get("wheel_sha256")
    interpreter = manifest.get("interpreter")
    if (
        not isinstance(wheel_sha, str)
        or not re.fullmatch(r"[0-9a-f]{64}", wheel_sha)
        or not isinstance(interpreter, dict)
        or _identity(wheel_sha, interpreter) != release_id
        or manifest.get("unit_name") != unit_name
    ):
        raise RuntimeError("release_manifest_mismatch")
    pin_path = RELEASE_BASE / ".pins" / f"{release_id}.sha256"
    if pin_path.is_symlink() or not pin_path.is_file():
        raise RuntimeError("release_manifest_pin_missing")
    expected_pin = f"{_sha256(root / 'release-manifest.json')}  release-manifest.json\n"
    if pin_path.read_text(encoding="ascii") != expected_pin:
        raise RuntimeError("release_manifest_pin_mismatch")
    inventory, inventory_sha = _inventory(root / "venv")
    if inventory != manifest.get("installed_inventory") or inventory_sha != manifest.get(
        "installed_inventory_sha256"
    ):
        raise RuntimeError("release_inventory_mismatch")
    _verify_permissions(root, unit_path)
    if _interpreter(root / "venv/bin/python") != interpreter:
        raise RuntimeError("release_interpreter_mismatch")
    unit_text = unit_path.read_text(encoding="utf-8")
    if hashlib.sha256(unit_text.encode()).hexdigest() != manifest.get("unit_sha256"):
        raise RuntimeError("release_unit_mismatch")
    expected_python = str(root / "venv/bin/python")
    if f"ExecStart={expected_python} -I -m memecoin_operator.runtime " not in unit_text:
        raise RuntimeError("release_unit_path_mismatch")
    state = linux_service._unit_status(unit_name)
    if state != ("loaded", "inactive", "disabled", 0):
        raise RuntimeError("release_unit_not_inert")
    state_root = STATE_BASE / release_id / "operator"
    _verify_effective_unit(unit_name, root, state_root)
    _verify_lab_state(root, release_id)
    return {
        "release_id": release_id,
        "unit": unit_name,
        "wheel_sha256": wheel_sha,
        "source_commit": manifest.get("source_commit"),
        "package_version": manifest.get("package_version"),
        "installed_inventory_sha256": inventory_sha,
        "unit_sha256": manifest.get("unit_sha256"),
        "manifest_sha256": _sha256(root / "release-manifest.json"),
        "unit_state": state,
    }


def activate(release_id: str) -> None:
    verify(release_id)
    _run(["systemctl", "start", f"memecoin-brain-{release_id}.service"], capture=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("stage", help="create a disabled content-addressed release candidate")
    install.add_argument("--source", type=Path, required=True)
    install.add_argument("--wheel", type=Path, required=True)
    install.add_argument("--source-commit", required=True)
    install.add_argument("--wheel-sha256", required=True)
    install.add_argument("--python", type=Path, default=Path("/usr/bin/python3"))
    check = sub.add_parser("verify", help="read back a release and its inert unit")
    check.add_argument("--release-id", required=True)
    start = sub.add_parser("activate", help="verify then explicitly start a release")
    start.add_argument("--release-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "stage":
            result = stage(
                args.source, args.wheel, args.source_commit, args.wheel_sha256, args.python
            )
        elif args.command == "verify":
            result = verify(args.release_id)
        else:
            activate(args.release_id)
            result = {"release_id": args.release_id, "activated": True}
        print(json.dumps(result, sort_keys=True))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"linux_release_error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
