"""Create and verify inert, content-addressed Linux release candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import shutil
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
    if wheel.stat().st_size > MAX_WHEEL_BYTES or not wheel.is_file():
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
    if release_root.exists() or unit_path.exists():
        raise RuntimeError("release_already_exists")
    user = pwd.getpwnam("brain")
    builder = pwd.getpwnam("nobody")
    staging = Path(tempfile.mkdtemp(prefix=f".stage-{release_id[:12]}-", dir=RELEASE_BASE))
    os.chown(staging, builder.pw_uid, builder.pw_gid)
    venv = staging / "venv"
    staged_wheel = staging / "input.whl"
    try:
        shutil.copyfile(wheel, staged_wheel)
        staged_wheel.chmod(0o444)
        os.chown(staged_wheel, builder.pw_uid, builder.pw_gid)
        _run(
            [
                "/usr/sbin/runuser",
                "-u",
                "nobody",
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
                "nobody",
                "--",
                str(venv / "bin/python"),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--no-compile",
                "--no-cache-dir",
                str(staged_wheel),
            ]
        )
        probe = _run(
            [
                "/usr/sbin/runuser",
                "-u",
                "nobody",
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
        staged_wheel.unlink()
        inventory, inventory_sha = _inventory(staging)
        unit_text = linux_service.render_unit(
            venv / "bin/python",
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
        (staging / "release-manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        os.chown(staging / "release-manifest.json", 0, user.pw_gid)
        (staging / "release-manifest.json").chmod(0o444)
        _seal(staging, user.pw_gid)
        os.replace(staging, release_root)
        linux_service.install(unit_text, unit_path=unit_path, unit=unit_name)
    except BaseException:
        candidate = staging if staging.exists() else release_root if release_root.exists() else None
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
    return {
        "release_id": release_id,
        "unit": unit_name,
        "wheel_sha256": wheel_sha,
        "source_commit": manifest.get("source_commit"),
        "package_version": manifest.get("package_version"),
        "installed_inventory_sha256": inventory_sha,
        "unit_sha256": manifest.get("unit_sha256"),
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
