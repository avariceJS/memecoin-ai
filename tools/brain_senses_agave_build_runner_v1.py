#!/usr/bin/env python3
"""Run one pinned, offline Agave build/test sequence on Kingston only.

The runner is inert until explicitly invoked. Its command list is fixed to two focused test
commands followed by a feature-off check. Every invocation is network-denied and source/cache/repo
write-denied by macOS sandbox-exec; build outputs stay in one new Kingston stage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

_SOURCE_ROOT = str(Path(__file__).resolve().parents[1] / "src")
_TOOLS_ROOT = str(Path(__file__).resolve().parent)
for _root in (_SOURCE_ROOT, _TOOLS_ROOT):
    if _root not in sys.path:
        sys.path.insert(0, _root)

import brain_senses_agave_metadata_adjudicate_v1 as adjudicator  # noqa: E402
import brain_senses_agave_metadata_preflight_v2 as metadata  # noqa: E402
import brain_senses_build_storage_guard as build_guard  # noqa: E402
from brain_senses_agave_dependency_fetch_v1 import (  # noqa: E402
    EXPECTED_AGAVE_HEAD,
    EXPECTED_COMPOSITE_DIFF,
    EXPECTED_POST_V5_LOCK,
)

from memecoin_research_storage import writer_guard  # noqa: E402

ROOT_ID = "9428695af6d4f46c08e1a444cdc66de8"
VOLUME_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"
REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE_RELATIVE = Path("qualifications/agave-v3.0.0-token2022-runtime-20261012/source")
CACHE_RELATIVE = Path("qualifications/agave-v3.0.0-token2022-runtime-20261012/cargo-home")
BUILD_RELATIVE = Path("qualifications/agave-v3.0.0-token2022-build-v1-20261012")
CARGO_HOME_RELATIVE = BUILD_RELATIVE / "cargo-home"
TARGET_RELATIVE = BUILD_RELATIVE / "target"
TMP_RELATIVE = BUILD_RELATIVE / "tmp"
RECEIPTS_RELATIVE = TARGET_RELATIVE / ".build-receipts"
RUN_START_RELATIVE = RECEIPTS_RELATIVE / "run-start-v1.json"
TERMINAL_RELATIVE = RECEIPTS_RELATIVE / "terminal-v1.json"
METADATA_ADJUDICATION_SHA256 = "2ffe9c73f3324477f9ee1ce9a742aa6fa3b65fef45373d05f9f81294b3935b7e"
SOURCE_CACHE_SHA256 = "8d7b00b284e11a416ff8c852b4ead27f3945b32ce88a78837f67eb78c2229450"
LOCK_SHA256 = "fc3fdf681e0c0a89399c07376747e13cdd5a31d61f6b209df5437a9594fc76a5"
MAX_TOTAL_WALL_SECONDS = 6 * 60 * 60
MAX_CAPTURE_BYTES = 64 * 1024**2
MAX_RETAINED_BYTES = 64 * 1024
MAX_CAPTURE_CHARS = 16 * 1024
MAX_METADATA_CHARS = 4096
FIXED_PATH = "/Users/root1/.cargo/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
GIB = 1024**3
COLLECTOR_RESERVES = {
    "service_operating": 8 * GIB,
    "next_epoch_allocation": 4_000_000_000,
    "service_peak_metadata": 322_000_000,
    "metadata_margin": 32_000_000,
    "per_request_metadata": 128_000,
}
EXTRA_RESERVED_BYTES = sum(COLLECTOR_RESERVES.values())
BUILD_SCRIPT_MAX_BYTES = 16 * 1024**2
BUILD_SCRIPT_MAX_COUNT = 512
GLOBAL_CACHE_PATH = ".global-cache"
GLOBAL_CACHE_MODE = 0o644
GLOBAL_CACHE_SIZE = 176_128
BUILD_SPAWN_PATTERNS = {
    "setsid": rb"\bsetsid\b",
    "setpgid": rb"\bsetpgid\b",
    "fork_call": rb"\bfork\s*\(",
    "daemon_call": rb"\bdaemon\s*\(",
    "process_group": rb"\bprocess_group\b",
    "command_ext": rb"CommandExt",
}
ORDINARY_PROCESS_SPAWN = re.compile(
    rb"(?:(?:std\s*::\s*)?(?:process\s*::\s*)?)Command\s*::\s*new\s*\("
)
ALLOWED_COMMANDS = (
    (
        "candidate_position_lib",
        (
            "test",
            "--offline",
            "--locked",
            "--jobs",
            "2",
            "-p",
            "solana-program-test",
            "--features",
            "dev-context-only-utils",
            "--lib",
            "candidate_position_",
        ),
    ),
    (
        "cpi_integration",
        (
            "test",
            "--offline",
            "--locked",
            "--jobs",
            "2",
            "-p",
            "solana-program-test",
            "--features",
            "dev-context-only-utils",
            "--test",
            "cpi",
        ),
    ),
    (
        "feature_off_check",
        (
            "check",
            "--offline",
            "--locked",
            "--jobs",
            "2",
            "-p",
            "solana-program-test",
            "-p",
            "solana-svm",
            "-p",
            "solana-program-runtime",
        ),
    ),
)
SOURCE_FILES = (
    "tools/brain_senses_agave_build_runner_v1.py",
    "tools/brain_senses_build_storage_guard.py",
    "tools/brain_senses_agave_metadata_preflight_v2.py",
    "tools/brain_senses_agave_metadata_adjudicate_v1.py",
    "tools/brain_senses_agave_dependency_fetch_v1.py",
    "src/memecoin_research_storage/__init__.py",
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class BuildRunnerError(RuntimeError):
    """Finite sanitized runner failure."""

    def __init__(self, code: str) -> None:
        self.code = code if re.fullmatch(r"[a-z0-9_]+", code) else "build_runner_failed"
        super().__init__(self.code)


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise BuildRunnerError(f"{label}_duplicate_key")
            out[key] = value
        return out

    try:
        value = json.loads(raw, object_pairs_hook=pairs)
    except BuildRunnerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildRunnerError(f"{label}_invalid_json") from exc
    if not isinstance(value, dict):
        raise BuildRunnerError(f"{label}_invalid_shape")
    return value


def source_bindings(repo: Path = REPOSITORY) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in SOURCE_FILES:
        raw = (repo / relative).read_bytes()
        result[relative] = hashlib.sha256(raw).hexdigest()
    return result


def build_sandbox_profile(repo: Path, source: Path, source_cache: Path) -> str:
    def quoted(path: Path) -> str:
        value = str(path)
        if not path.is_absolute() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise BuildRunnerError("sandbox_path_invalid")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    return " ".join(
        (
            "(version 1)",
            "(allow default)",
            "(deny network*)",
            f"(deny file-write* (subpath {quoted(repo)}))",
            f"(deny file-write* (subpath {quoted(source)}))",
            f"(deny file-write* (subpath {quoted(source_cache)}))",
        )
    )


def command_vectors(
    cargo_path: str,
    profile: str,
    *,
    env_path: str = "/usr/bin/env",
    nice_path: str = "/usr/bin/nice",
    sandbox_path: str = "/usr/bin/sandbox-exec",
) -> list[dict[str, Any]]:
    if not Path(cargo_path).is_absolute() or not Path(cargo_path).is_file():
        raise BuildRunnerError("cargo_executable_unpinned")
    return [
        {
            "id": command_id,
            "cargo_argv": [cargo_path, *args],
            "execution_argv": [
                sandbox_path,
                "-p",
                profile,
                nice_path,
                "-n",
                "10",
                env_path,
                "CARGO_BUILD_JOBS=2",
                cargo_path,
                *args,
            ],
        }
        for command_id, args in ALLOWED_COMMANDS
    ]


def _sanitize(data: bytes, roots: Sequence[Path]) -> str:
    return metadata._sanitize(data, tuple(roots))[:MAX_CAPTURE_CHARS]


class _PipeCapture:
    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self.digest = hashlib.sha256()
        self.size = 0
        self.retained = bytearray()
        self.exceeded = False
        self.error = False
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        try:
            while chunk := self.stream.read(64 * 1024):
                self.size += len(chunk)
                self.digest.update(chunk)
                if len(self.retained) < MAX_RETAINED_BYTES:
                    remaining = MAX_RETAINED_BYTES - len(self.retained)
                    self.retained.extend(chunk[:remaining])
                if self.size > MAX_CAPTURE_BYTES:
                    self.exceeded = True
        except OSError:
            self.error = True
        finally:
            try:
                self.stream.close()
            except OSError:
                self.error = True

    def record(self, roots: Sequence[Path]) -> dict[str, Any]:
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise BuildRunnerError("capture_drain_timeout")
        return {
            "sha256": self.digest.hexdigest(),
            "bytes": self.size,
            "over_limit": self.exceeded,
            "read_error": self.error,
            "complete": not self.thread.is_alive() and not self.error,
            "sanitized_excerpt": _sanitize(bytes(self.retained), roots),
        }


class CapturedProcess:
    """Managed process adapter that drains bounded, hashed, sanitized streams."""

    def __init__(self, process: Any, roots: Sequence[Path]) -> None:
        self.process = process
        self.pid = process.pid
        self.stdout = _PipeCapture(process.stdout)
        self.stderr = _PipeCapture(process.stderr)
        self.roots = tuple(roots)
        self._limit_signalled = False

    def poll(self) -> int | None:
        if (self.stdout.exceeded or self.stderr.exceeded) and not self._limit_signalled:
            self._limit_signalled = True
            try:
                os.killpg(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        status = self.process.poll()
        if self._limit_signalled and status is not None and status == 0:
            return 125
        return None if status is None else int(status)

    def wait(self, timeout: float | None = None) -> int:
        return int(self.process.wait(timeout=timeout))

    def record(self) -> dict[str, Any]:
        return {"stdout": self.stdout.record(self.roots), "stderr": self.stderr.record(self.roots)}


class ReservedSnapshotProvider:
    """Revalidate Kingston and subtract collector reserves from every guard sample."""

    def __init__(self, guard: Any, *, deadline: float | None = None) -> None:
        self.guard = guard
        self.deadline = deadline

    def inspect(self) -> build_guard.Snapshot:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise build_guard.GuardFailure("total_build_wall_timeout")
        root = self.guard.revalidate(min_free_bytes=build_guard.MIN_FREE_BYTES)
        mount_stat = os.fstat(self.guard.mount_fd)
        return build_guard.Snapshot(
            root.root_id,
            root.volume_uuid,
            root.mount_path,
            root.path,
            root.available_bytes - EXTRA_RESERVED_BYTES,
            mount_stat.st_dev,
            mount_stat.st_ino,
        )


def _open_root(guard: Any) -> int:
    fd = adjudicator._open_root_from_guard(guard)
    st = os.fstat(fd)
    if st.st_dev != os.fstat(guard.mount_fd).st_dev:
        os.close(fd)
        raise BuildRunnerError("data_root_device_mismatch")
    return fd


def _write_record(
    root_fd: int,
    relative: Path,
    value: dict[str, Any],
    *,
    device: int,
    revalidate: Callable[[], None],
) -> str:
    payload = canonical(value)
    return metadata._write_create_only_beneath_root(
        root_fd, relative, payload, device=device, revalidate=revalidate
    )


def _write_cache_manifest(
    root_fd: int,
    relative: Path,
    snapshot: dict[str, Any],
    *,
    device: int,
    revalidate: Callable[[], None],
) -> str:
    """Persist the exact tree rows behind each per-command cache snapshot digest."""
    value = {
        "schema": "brain-senses-agave-build-cache-manifest-v1",
        "schema_version": 1,
        "tree_sha256": snapshot["sha256"],
        "entry_count": snapshot["entry_count"],
        "file_bytes": snapshot["file_bytes"],
        "rows": snapshot["rows"],
    }
    return _write_record(root_fd, relative, value, device=device, revalidate=revalidate)


def _read_artifact(root_fd: int, relative: Path, *, device: int, cap: int = 256 * 1024) -> bytes:
    return adjudicator._read_relative(root_fd, relative, device=device, cap=cap)


def _validate_metadata_basis(
    root_fd: int, device: int, source: Path, source_cache: Path
) -> dict[str, Any]:
    raw = _read_artifact(root_fd, adjudicator.ADJUDICATION_RELATIVE, device=device)
    if hashlib.sha256(raw).hexdigest() != METADATA_ADJUDICATION_SHA256:
        raise BuildRunnerError("metadata_adjudication_hash_mismatch")
    receipt = adjudicator._canonical_exact(raw, "metadata_adjudication")
    if (
        receipt.get("status") != "verified_offline_metadata_graph"
        or receipt.get("raw_stdout_to_graph_replay") is not False
        or receipt.get("source", {}).get("head") != EXPECTED_AGAVE_HEAD
        or receipt.get("source", {}).get("diff_sha256") != EXPECTED_COMPOSITE_DIFF
        or receipt.get("source", {}).get("cargo_lock_sha256") != EXPECTED_POST_V5_LOCK
        or receipt.get("cache", {}).get("source_manifest_sha256") != SOURCE_CACHE_SHA256
    ):
        raise BuildRunnerError("metadata_adjudication_mismatch")
    source_pins = metadata._source_pins(source, source_cache)
    if source_pins[:4] != (
        EXPECTED_AGAVE_HEAD,
        EXPECTED_COMPOSITE_DIFF,
        EXPECTED_POST_V5_LOCK,
        1075,
    ):
        raise BuildRunnerError("source_pin_mismatch")
    source_cache_manifest = metadata._locked_cache_manifest(source_cache)
    if source_cache_manifest != SOURCE_CACHE_SHA256:
        raise BuildRunnerError("source_cache_pin_mismatch")
    return {
        "metadata_adjudication_sha256": METADATA_ADJUDICATION_SHA256,
        "source_head": source_pins[0],
        "source_diff_sha256": source_pins[1],
        "cargo_lock_sha256": source_pins[2],
        "lock_package_count": source_pins[3],
        "source_status_sha256": source_pins[4],
        "source_cache_manifest_sha256": source_cache_manifest,
    }


def _read_pinned_file(path: Path, *, device: int, cap: int) -> tuple[bytes, os.stat_result]:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_dev != device
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size > cap
        ):
            raise BuildRunnerError("build_closure_file_identity")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, cap + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > cap:
                raise BuildRunnerError("build_closure_file_limit")
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
        ) or total != before.st_size:
            raise BuildRunnerError("build_closure_file_changed")
        return b"".join(chunks), before
    finally:
        os.close(fd)


def _walk_build_inputs(root: Path, *, device: int) -> list[Path]:
    if root.is_symlink() or not root.is_dir() or root.stat().st_dev != device:
        raise BuildRunnerError("build_closure_root_identity")
    found: list[Path] = []
    for parent, dirs, files in os.walk(root, topdown=True, followlinks=False):
        parent_path = Path(parent)
        kept_dirs: list[str] = []
        for name in dirs:
            child = parent_path / name
            st = child.lstat()
            if stat.S_ISLNK(st.st_mode):
                raise BuildRunnerError("build_closure_symlink_directory")
            if (
                not stat.S_ISDIR(st.st_mode)
                or st.st_dev != device
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) & 0o022
            ):
                raise BuildRunnerError("build_closure_directory_identity")
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in files:
            child = parent_path / name
            try:
                st = child.lstat()
            except FileNotFoundError as exc:
                raise BuildRunnerError("build_closure_tree_changed") from exc
            if stat.S_ISLNK(st.st_mode):
                if name in {"Cargo.toml", "build.rs"}:
                    raise BuildRunnerError("build_closure_symlink_input")
                continue
            if (
                not stat.S_ISREG(st.st_mode)
                or st.st_dev != device
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) & 0o022
            ):
                raise BuildRunnerError("build_closure_file_identity")
            if name in {"Cargo.toml", "build.rs"}:
                found.append(child)
            if len(found) > BUILD_SCRIPT_MAX_COUNT * 4:
                raise BuildRunnerError("build_closure_entry_limit")
    return found


def _check_parent_chain(root: Path, path: Path, *, device: int) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise BuildRunnerError("build_closure_path_escape") from exc
    cursor = root
    root_stat = cursor.lstat()
    if (
        stat.S_ISLNK(root_stat.st_mode)
        or not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_dev != device
        or root_stat.st_uid != os.getuid()
    ):
        raise BuildRunnerError("build_closure_root_identity")
    for part in relative.parts[:-1]:
        cursor /= part
        st = cursor.lstat()
        if (
            stat.S_ISLNK(st.st_mode)
            or not stat.S_ISDIR(st.st_mode)
            or st.st_dev != device
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) & 0o022
        ):
            raise BuildRunnerError("build_closure_parent_identity")


def _process_spawn_closure(source: Path, source_cache: Path, *, device: int) -> dict[str, Any]:
    """Bind all build-script declarations and reject known detached-process markers."""
    roots = (("source", source), ("source-cache", source_cache))
    inputs = {label: _walk_build_inputs(root, device=device) for label, root in roots}
    scripts: dict[Path, dict[str, Any]] = {}
    declarations: list[dict[str, Any]] = []
    disabled_script_paths: set[Path] = set()
    for label, root in roots:
        for manifest_path in inputs[label]:
            if manifest_path.name != "Cargo.toml":
                continue
            _check_parent_chain(root, manifest_path, device=device)
            raw, manifest_stat = _read_pinned_file(
                manifest_path, device=device, cap=BUILD_SCRIPT_MAX_BYTES
            )
            try:
                data = tomllib.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                raise BuildRunnerError("build_manifest_invalid") from exc
            package = data.get("package")
            if not isinstance(package, dict):
                continue
            declaration = package.get("build")
            manifest_binding = {
                "path": f"{label}/{manifest_path.relative_to(root).as_posix()}",
                "kind": "regular_file",
                "mode": stat.S_IMODE(manifest_stat.st_mode),
                "size": manifest_stat.st_size,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
            if declaration is False:
                script_path = manifest_path.parent / "build.rs"
                if script_path.exists():
                    disabled_script_paths.add(script_path)
                declarations.append(
                    {
                        "manifest": manifest_binding,
                        "package_build": "false",
                        "script_path": None,
                        "script_status": "disabled",
                    }
                )
                continue
            implicit = declaration is None
            if declaration is True or implicit:
                declaration = "build.rs"
            if not isinstance(declaration, str) or not declaration or "\x00" in declaration:
                raise BuildRunnerError("build_manifest_path_invalid")
            relative_script = Path(declaration)
            if relative_script.is_absolute() or any(
                part in {"", ".", ".."} for part in relative_script.parts
            ):
                raise BuildRunnerError("build_manifest_path_invalid")
            script_path = manifest_path.parent / relative_script
            _check_parent_chain(root, script_path, device=device)
            try:
                script_stat = script_path.lstat()
            except FileNotFoundError as exc:
                if implicit:
                    declarations.append(
                        {
                            "manifest": manifest_binding,
                            "package_build": "implicit",
                            "script_path": None,
                            "script_status": "implicit_script_absent",
                        }
                    )
                    continue
                raise BuildRunnerError("declared_build_script_missing") from exc
            if not stat.S_ISREG(script_stat.st_mode):
                raise BuildRunnerError("declared_build_script_not_regular")
            scripts[script_path] = {
                "path": f"{label}/{script_path.relative_to(root).as_posix()}",
                "declaration": "active",
                "manifest": {
                    **manifest_binding,
                },
            }
            declarations.append(
                {
                    "manifest": manifest_binding,
                    "package_build": "implicit" if implicit else "explicit",
                    "script_path": f"{label}/{script_path.relative_to(root).as_posix()}",
                    "script_status": "present",
                }
            )
    # Include physical build.rs files even when Cargo.toml disables them; these are
    # hash-bound and scanned conservatively, but are not represented as executable.
    for label, root in roots:
        for path in inputs[label]:
            if path.name == "build.rs" and path not in scripts:
                scripts[path] = {
                    "path": f"{label}/{path.relative_to(root).as_posix()}",
                    "declaration": (
                        "disabled_build_rs"
                        if path in disabled_script_paths
                        else "unreferenced_build_rs"
                    ),
                    "manifest": None,
                }
    if len(scripts) > BUILD_SCRIPT_MAX_COUNT or len(declarations) > BUILD_SCRIPT_MAX_COUNT * 4:
        raise BuildRunnerError("build_script_count_invalid")

    rows: list[dict[str, Any]] = []
    spawn_count = 0
    spawn_files = 0
    for path in sorted(scripts, key=lambda item: scripts[item]["path"]):
        row = scripts[path]
        raw, st = _read_pinned_file(path, device=device, cap=BUILD_SCRIPT_MAX_BYTES)
        if any(re.search(pattern, raw) for pattern in BUILD_SPAWN_PATTERNS.values()):
            raise BuildRunnerError("detached_process_marker")
        file_spawn_count = len(ORDINARY_PROCESS_SPAWN.findall(raw))
        spawn_count += file_spawn_count
        spawn_files += int(file_spawn_count > 0)
        rows.append(
            {
                **row,
                "kind": "regular_file",
                "mode": stat.S_IMODE(st.st_mode),
                "size": st.st_size,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return {
        "script_count": len(rows),
        "active_declaration_count": sum(row["declaration"] == "active" for row in rows),
        "declaration_count": len(declarations),
        "declarations": declarations,
        "ordinary_process_spawn_pattern": "Command::new(",
        "ordinary_process_spawn_files": spawn_files,
        "ordinary_process_spawn_matches": spawn_count,
        "detached_marker_patterns": list(BUILD_SPAWN_PATTERNS),
        "detached_marker_matches": 0,
        "scripts": rows,
        "sha256": hashlib.sha256(
            canonical({"declarations": declarations, "scripts": rows})
        ).hexdigest(),
        "scope_limit": (
            "Static build-script scan only; not an exhaustive process-spawn safety proof."
        ),
    }


def _cache_snapshot(path: Path) -> dict[str, Any]:
    rows = metadata._tree_manifest_rows(path)
    return {
        "rows": rows,
        "sha256": hashlib.sha256(canonical(rows)).hexdigest(),
        "entry_count": len(rows),
        "file_bytes": sum(int(row.get("size", 0)) for row in rows),
    }


def _cache_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_rows = {row["path"]: row for row in before["rows"]}
    after_rows = {row["path"]: row for row in after["rows"]}
    changed = [
        path
        for path in sorted(before_rows.keys() | after_rows.keys())
        if before_rows.get(path) != after_rows.get(path)
    ]
    allowed: dict[str, Any] | None = None
    if changed:
        if changed != [GLOBAL_CACHE_PATH]:
            raise BuildRunnerError("copied_cache_unexpected_delta")
        old = before_rows.get(GLOBAL_CACHE_PATH)
        new = after_rows.get(GLOBAL_CACHE_PATH)
        if (
            old is None
            or new is None
            or old.get("kind") != "file"
            or new.get("kind") != "file"
            or old.get("mode") != GLOBAL_CACHE_MODE
            or new.get("mode") != GLOBAL_CACHE_MODE
            or old.get("size") != GLOBAL_CACHE_SIZE
            or new.get("size") != GLOBAL_CACHE_SIZE
            or not _SHA256.fullmatch(str(old.get("sha256", "")))
            or not _SHA256.fullmatch(str(new.get("sha256", "")))
        ):
            raise BuildRunnerError("copied_cache_global_bookkeeping_mismatch")
        allowed = {
            "path": GLOBAL_CACHE_PATH,
            "kind": "regular_file",
            "mode": GLOBAL_CACHE_MODE,
            "size": GLOBAL_CACHE_SIZE,
            "before_sha256": old["sha256"],
            "after_sha256": new["sha256"],
            "classification": "opaque_cargo_bookkeeping_only",
        }
    return {
        "before_manifest_sha256": before["sha256"],
        "after_manifest_sha256": after["sha256"],
        "before_entry_count": before["entry_count"],
        "after_entry_count": after["entry_count"],
        "before_file_bytes": before["file_bytes"],
        "after_file_bytes": after["file_bytes"],
        "allowed_delta": allowed,
    }


def _require_setup_headroom(snapshot: build_guard.Snapshot) -> None:
    required = build_guard.MIN_FREE_BYTES + build_guard.ALLOCATION_CAP_BYTES
    if snapshot.available_bytes < required:
        raise BuildRunnerError("free_space_below_reserved_stage_envelope")


def _copy_source_cache(
    root_fd: int, root_path: Path, source_cache: Path, destination: Path, device: int
) -> tuple[str, int, int]:
    source_before = metadata._locked_cache_manifest(source_cache)
    if source_before != SOURCE_CACHE_SHA256:
        raise BuildRunnerError("source_cache_pin_mismatch")
    copied, entries, byte_count = metadata._copy_cache_once(
        root_fd, root_path, source_cache, destination, device=device
    )
    if not copied:
        raise BuildRunnerError("build_cache_already_exists")
    if metadata._locked_cache_manifest(source_cache) != source_before:
        raise BuildRunnerError("source_cache_changed_during_copy")
    copied_hash = metadata._locked_cache_manifest(destination)
    if copied_hash != source_before:
        raise BuildRunnerError("build_cache_copy_mismatch")
    return copied_hash, entries, byte_count


def _cargo_binary() -> str:
    found = shutil.which("cargo")
    expected = Path.home() / ".cargo" / "bin" / "cargo"
    if found is None or Path(found) != expected or not expected.is_file():
        raise BuildRunnerError("cargo_executable_path_unpinned")
    rustc = shutil.which("rustc")
    expected_rustc = Path.home() / ".cargo" / "bin" / "rustc"
    if rustc is None or Path(rustc) != expected_rustc or not expected_rustc.is_file():
        raise BuildRunnerError("rustc_executable_path_unpinned")
    return str(expected)


def _terminal_record(
    *,
    run_id: str,
    status: str,
    reason: str | None,
    completed: list[dict[str, Any]],
    source: dict[str, Any],
    bindings: dict[str, str],
    start_ns: int,
    cache_evidence: dict[str, Any] | None = None,
    process_spawn_closure: dict[str, Any] | None = None,
    resource_snapshot: dict[str, Any] | None = None,
    post_run_source_pins: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "brain-senses-agave-build-terminal-v1",
        "schema_version": 1,
        "run_id": run_id,
        "status": status,
        "failure_reason": reason,
        "completed_commands": completed,
        "source_pins": source,
        "post_run_source_pins": post_run_source_pins,
        "implementation_sha256": bindings,
        "cache_evidence": cache_evidence,
        "process_spawn_closure": process_spawn_closure,
        "terminal_resource_snapshot": resource_snapshot,
        "started_ns": start_ns,
        "finished_ns": time.time_ns(),
        "cargo_execution_count": len(completed),
        "network_policy": "sandbox_denied",
        "source_cache_repo_writes": "sandbox_denied",
        "provider_requests": 0,
        "collector_reserves": COLLECTOR_RESERVES,
        "stage_allocation_cap_bytes": build_guard.ALLOCATION_CAP_BYTES,
        "free_floor_bytes": build_guard.FREE_FLOOR_BYTES,
        "existing_peak_margin_bytes": build_guard.PEAK_MARGIN_BYTES,
        "runner_total_timeout_seconds": MAX_TOTAL_WALL_SECONDS,
        "raw_stdout_persisted": False,
        "limitations": ["Local program behavior only; no runtime-law or economic admission."],
    }


def execute_commands(
    *,
    commands: list[dict[str, Any]],
    source: Path,
    source_cache: Path,
    cargo_home: Path,
    target_dir: Path,
    tmpdir: Path,
    snapshot_provider: build_guard.SnapshotProvider,
    root_fd: int,
    device: int,
    source_pins: dict[str, Any],
    implementation_pins: dict[str, str],
    run_id: str,
    start_ns: int,
    stage_runner: Callable[..., int] = build_guard.run_guarded_stage,
    process_factory: Callable[..., Any] = subprocess.Popen,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    revalidate: Callable[[], None] = lambda: None,
    deadline: float | None = None,
    verify_inputs: Callable[[], None] = lambda: None,
    stage_paths: Sequence[Path] | None = None,
    cache_snapshot: Callable[[], dict[str, Any]] | None = None,
    source_cache_manifest: Callable[[], str] | None = None,
    process_spawn_closure: dict[str, Any] | None = None,
    terminal_snapshot: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute the immutable three-command sequence, ending permanently on first failure."""
    deadline = deadline if deadline is not None else clock() + MAX_TOTAL_WALL_SECONDS
    records: list[dict[str, Any]] = []
    last_copied_cache_sha: str | None = None

    def finish(status: str, reason: str | None) -> dict[str, Any]:
        post_run_verification: str | None = None
        try:
            verify_inputs()
        except BuildRunnerError as exc:
            post_run_verification = exc.code
            status = "failed_terminal_no_retry"
            if reason is None:
                reason = exc.code
        latest_cache = cache_snapshot() if cache_snapshot is not None else None
        latest_source_cache = source_cache_manifest() if source_cache_manifest is not None else None
        source_cache_matches = latest_source_cache == source_pins.get(
            "source_cache_manifest_sha256"
        )
        copied_cache_matches = (
            latest_cache is None
            or last_copied_cache_sha is None
            or latest_cache["sha256"] == last_copied_cache_sha
        )
        if not source_cache_matches:
            status = "failed_terminal_no_retry"
            if reason is None:
                reason = "source_cache_terminal_pin_mismatch"
        if not copied_cache_matches:
            status = "failed_terminal_no_retry"
            if reason is None:
                reason = "copied_cache_terminal_changed"
        terminal_cache_manifest = None
        if latest_cache is not None:
            manifest_path = RECEIPTS_RELATIVE / "cache-terminal.json"
            manifest_sha = _write_cache_manifest(
                root_fd,
                manifest_path,
                latest_cache,
                device=device,
                revalidate=revalidate,
            )
            terminal_cache_manifest = {
                "path": manifest_path.as_posix(),
                "sha256": manifest_sha,
                "tree_sha256": latest_cache["sha256"],
            }
        resources = terminal_snapshot() if terminal_snapshot is not None else None
        cache_evidence = None
        if latest_cache is not None:
            cache_evidence = {
                "copied_cache_manifest_sha256": latest_cache["sha256"],
                "copied_cache_entries": latest_cache["entry_count"],
                "copied_cache_file_bytes": latest_cache["file_bytes"],
                "copied_cache_manifest_path": (
                    terminal_cache_manifest["path"] if terminal_cache_manifest else None
                ),
                "copied_cache_manifest_artifact_sha256": (
                    terminal_cache_manifest["sha256"] if terminal_cache_manifest else None
                ),
                "source_cache_manifest_sha256": latest_source_cache,
                "source_cache_matches_pin": source_cache_matches,
                "copied_cache_matches_last_command": copied_cache_matches,
            }
        terminal = _terminal_record(
            run_id=run_id,
            status=status,
            reason=reason,
            completed=records,
            source=source_pins,
            bindings=implementation_pins,
            start_ns=start_ns,
            cache_evidence=cache_evidence,
            process_spawn_closure=process_spawn_closure,
            resource_snapshot=resources,
            post_run_source_pins={
                **source_pins,
                "source_cache_manifest_sha256": latest_source_cache,
                "copied_cache_manifest_sha256": (
                    latest_cache["sha256"] if latest_cache is not None else None
                ),
                "process_spawn_closure_sha256": (
                    process_spawn_closure.get("sha256") if process_spawn_closure else None
                ),
                "post_run_verification_failure": post_run_verification,
            },
        )
        _write_record(root_fd, TERMINAL_RELATIVE, terminal, device=device, revalidate=revalidate)
        return terminal

    for ordinal, spec in enumerate(commands):
        reason: str | None = None
        cache_before: dict[str, Any] | None = None
        cache_delta: dict[str, Any] | None = None
        cache_before_artifact_sha: str | None = None
        cache_after_artifact_sha: str | None = None
        cache_after_tree_sha: str | None = None
        source_cache_before: str | None = None
        source_cache_after: str | None = None
        if clock() >= deadline:
            reason = "total_build_wall_timeout"
            result = -1
            captured: dict[str, Any] = {"stdout": {}, "stderr": {}}
        else:
            captures: list[CapturedProcess] = []
            roots = (source, source_cache, cargo_home, target_dir, tmpdir, REPOSITORY)

            def popen_factory(
                command: Sequence[str],
                *,
                _captures: list[CapturedProcess] = captures,
                _roots: tuple[Path, ...] = roots,
                **kwargs: Any,
            ) -> CapturedProcess:
                environment = dict(kwargs["env"])
                environment["PATH"] = FIXED_PATH
                process = process_factory(
                    list(command),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **{**kwargs, "env": environment},
                )
                wrapped = CapturedProcess(process, _roots)
                _captures.append(wrapped)
                return wrapped

            def deadline_sleep(seconds: float) -> None:
                if clock() + seconds >= deadline:
                    raise build_guard.GuardFailure("total_build_wall_timeout")
                sleep(seconds)

            try:
                revalidate()
                verify_inputs()
                if cache_snapshot is not None:
                    cache_before = cache_snapshot()
                    if (
                        last_copied_cache_sha is not None
                        and cache_before["sha256"] != last_copied_cache_sha
                    ):
                        raise BuildRunnerError("copied_cache_changed_between_commands")
                    cache_before_artifact_sha = _write_cache_manifest(
                        root_fd,
                        RECEIPTS_RELATIVE / f"cache-command-{ordinal:02d}-before.json",
                        cache_before,
                        device=device,
                        revalidate=revalidate,
                    )
                if source_cache_manifest is not None:
                    source_cache_before = source_cache_manifest()
                result = stage_runner(
                    spec["execution_argv"],
                    source=source,
                    cargo_home=cargo_home,
                    target_dir=target_dir,
                    tmpdir=tmpdir,
                    initial=False,
                    snapshot_provider=snapshot_provider,
                    working_directory=source,
                    allow_cargo_git_checkout_symlinks=True,
                    popen=popen_factory,
                    sleep=deadline_sleep,
                )
                if clock() > deadline:
                    result = -1
                    reason = "total_build_wall_timeout"
                else:
                    reason = None if result == 0 else "command_failed"
            except build_guard.GuardFailure as exc:
                result = -1
                reason = getattr(exc, "code", "storage_guard_failed")
            except BuildRunnerError as exc:
                result = -1
                reason = exc.code
            except Exception as exc:
                result = -1
                reason = type(exc).__name__
            try:
                verify_inputs()
                if source_cache_manifest is not None:
                    source_cache_after = source_cache_manifest()
                if cache_snapshot is not None and cache_before is not None:
                    cache_after = cache_snapshot()
                    last_copied_cache_sha = cache_after["sha256"]
                    cache_after_tree_sha = cache_after["sha256"]
                    cache_after_artifact_sha = _write_cache_manifest(
                        root_fd,
                        RECEIPTS_RELATIVE / f"cache-command-{ordinal:02d}-after.json",
                        cache_after,
                        device=device,
                        revalidate=revalidate,
                    )
                    cache_delta = _cache_delta(cache_before, cache_after)
                if source_cache_before is not None and source_cache_after != source_cache_before:
                    raise BuildRunnerError("source_cache_changed_during_command")
            except BuildRunnerError as exc:
                result = -1
                reason = exc.code
            except Exception as exc:
                result = -1
                reason = type(exc).__name__
            if captures:
                try:
                    captured = captures[0].record()
                    streams = (captured["stdout"], captured["stderr"])
                    if result == 0 and any(
                        stream["over_limit"] or stream["read_error"] or not stream["complete"]
                        for stream in streams
                    ):
                        result = -1
                        reason = "capture_output_limit_or_read_failure"
                except BuildRunnerError as exc:
                    captured = {"capture_error": exc.code}
                    result = -1
                    reason = exc.code
            else:
                captured = {"stdout": {}, "stderr": {}}
        cache_manifest_artifacts = None
        if cache_before is not None:
            cache_manifest_artifacts = {
                "before": {
                    "path": (
                        RECEIPTS_RELATIVE / f"cache-command-{ordinal:02d}-before.json"
                    ).as_posix(),
                    "artifact_sha256": cache_before_artifact_sha,
                    "tree_sha256": cache_before["sha256"],
                },
                "after": {
                    "path": (
                        RECEIPTS_RELATIVE / f"cache-command-{ordinal:02d}-after.json"
                    ).as_posix(),
                    "artifact_sha256": cache_after_artifact_sha,
                    "tree_sha256": cache_after_tree_sha,
                },
            }
        record = {
            "schema": "brain-senses-agave-build-command-v1",
            "schema_version": 1,
            "run_id": run_id,
            "ordinal": ordinal,
            "command_id": spec["id"],
            "cargo_argv": spec["cargo_argv"],
            "execution_argv_sha256": hashlib.sha256(canonical(spec["execution_argv"])).hexdigest(),
            "return_code": result,
            "status": "passed" if result == 0 else "failed",
            "failure_reason": None if result == 0 else reason,
            "capture": captured,
            "source_cache_manifest_before_sha256": source_cache_before,
            "source_cache_manifest_after_sha256": source_cache_after,
            "copied_cache_delta": cache_delta,
            "copied_cache_manifests": cache_manifest_artifacts,
            "process_spawn_closure_sha256": (
                process_spawn_closure.get("sha256") if process_spawn_closure else None
            ),
            "finished_ns": time.time_ns(),
        }
        _write_record(
            root_fd,
            RECEIPTS_RELATIVE / f"command-{ordinal:02d}.json",
            record,
            device=device,
            revalidate=revalidate,
        )
        records.append(record)
        if result != 0:
            return finish("failed_terminal_no_retry", record["failure_reason"])
    return finish("all_commands_passed", None)


def run_build(config_path: Path) -> dict[str, Any]:
    """Create a new build stage, copy the pinned cache, then execute the fixed sequence once."""
    start_wall_ns = time.time_ns()
    deadline = time.monotonic() + MAX_TOTAL_WALL_SECONDS
    with writer_guard(config_path, min_free_bytes=build_guard.MIN_FREE_BYTES) as storage:
        if storage.root_id != ROOT_ID or storage.volume_uuid.lower() != VOLUME_UUID:
            raise BuildRunnerError("storage_binding_mismatch")
        root_fd = _open_root(storage)
        try:
            device = os.fstat(root_fd).st_dev
            root_path = storage.path
            source = root_path / SOURCE_RELATIVE
            source_cache = root_path / CACHE_RELATIVE
            cargo_home = root_path / CARGO_HOME_RELATIVE
            target_dir = root_path / TARGET_RELATIVE
            tmpdir = root_path / TMP_RELATIVE
            try:
                existing_fd = metadata._open_directory_beneath(
                    root_fd, BUILD_RELATIVE, device=device
                )
            except FileNotFoundError:
                pass
            else:
                os.close(existing_fd)
                raise BuildRunnerError("build_stage_already_exists")
            initial_pins = _validate_metadata_basis(root_fd, device, source, source_cache)
            implementation_pins = source_bindings()
            process_closure = _process_spawn_closure(source, source_cache, device=device)
            cargo = _cargo_binary()
            profile = build_sandbox_profile(REPOSITORY, source, source_cache)
            commands = command_vectors(cargo, profile)
            run_id = hashlib.sha256(
                canonical(
                    {
                        "started_ns": start_wall_ns,
                        "source": initial_pins,
                        "implementation": implementation_pins,
                    }
                )
            ).hexdigest()[:24]
            provider = ReservedSnapshotProvider(storage, deadline=deadline)

            def setup_preflight() -> build_guard.Snapshot:
                snapshot = provider.inspect()
                _require_setup_headroom(snapshot)
                return snapshot

            setup_preflight()
            storage.create_directory(BUILD_RELATIVE.as_posix())
            # Create the stage directories through the held StorageGuard. Cache copy itself
            # creates the build CARGO_HOME through the same mount-anchored descriptor.
            setup_preflight()
            storage.create_directory(TARGET_RELATIVE.as_posix())
            setup_preflight()
            storage.create_directory(TMP_RELATIVE.as_posix())
            setup_preflight()
            copied_manifest, copied_entries, copied_bytes = _copy_source_cache(
                root_fd, root_path, source_cache, cargo_home, device
            )
            setup_preflight()
            if source_bindings() != implementation_pins:
                raise BuildRunnerError("implementation_changed_during_setup")
            if _validate_metadata_basis(root_fd, device, source, source_cache) != initial_pins:
                raise BuildRunnerError("source_changed_during_setup")
            if _process_spawn_closure(source, source_cache, device=device) != process_closure:
                raise BuildRunnerError("build_script_closure_changed_during_setup")
            setup_preflight()
            storage.create_directory(RECEIPTS_RELATIVE.as_posix())
            stage_paths = (source, cargo_home, target_dir, tmpdir)
            first_snapshot = provider.inspect()

            def full_revalidate() -> None:
                build_guard._inspect_stage(
                    provider,
                    stage_paths,
                    first_snapshot,
                    allow_cargo_git_checkout_symlinks=True,
                )

            start_record = {
                "schema": "brain-senses-agave-build-start-v1",
                "schema_version": 1,
                "run_id": run_id,
                "started_ns": start_wall_ns,
                "source_pins": initial_pins,
                "implementation_sha256": implementation_pins,
                "process_spawn_closure": process_closure,
                "build_cache_manifest_sha256": copied_manifest,
                "build_cache_entries": copied_entries,
                "build_cache_bytes": copied_bytes,
                "commands": [
                    {"id": item["id"], "cargo_argv": item["cargo_argv"]} for item in commands
                ],
                "cargo_version": build_guard.RUST_VERSION,
                "rustc_version": build_guard.RUST_VERSION,
                "toolchain_probe": "run_guarded_stage checks Rust 1.86.0 per command",
                "cargo_executable": cargo,
                "max_jobs": 2,
                "nice_level": 10,
                "sandbox_profile_sha256": hashlib.sha256(profile.encode()).hexdigest(),
                "network_denied": True,
                "source_cache_repo_write_denied": True,
                "stage_relative_root": BUILD_RELATIVE.as_posix(),
                "limits": {
                    "wall_seconds": MAX_TOTAL_WALL_SECONDS,
                    "max_capture_bytes_per_stream": MAX_CAPTURE_BYTES,
                    "max_retained_bytes_per_stream": MAX_RETAINED_BYTES,
                    "collector_reserves": COLLECTOR_RESERVES,
                    "reserved_bytes": EXTRA_RESERVED_BYTES,
                    "base_floor_bytes": build_guard.MIN_FREE_BYTES,
                    "stage_allocation_cap_bytes": build_guard.ALLOCATION_CAP_BYTES,
                },
            }
            _write_record(
                root_fd,
                RUN_START_RELATIVE,
                start_record,
                device=device,
                revalidate=full_revalidate,
            )

            def verify_inputs() -> None:
                if source_bindings() != implementation_pins:
                    raise BuildRunnerError("implementation_changed_during_build")
                if _validate_metadata_basis(root_fd, device, source, source_cache) != initial_pins:
                    raise BuildRunnerError("source_changed_during_build")
                if _process_spawn_closure(source, source_cache, device=device) != process_closure:
                    raise BuildRunnerError("build_script_closure_changed_during_build")

            def terminal_snapshot() -> dict[str, Any]:
                full_revalidate()
                actual = storage.revalidate(min_free_bytes=build_guard.MIN_FREE_BYTES)
                reserved = provider.inspect()
                allocation = {
                    "source": build_guard._allocated_bytes(
                        (source,), device, allow_cargo_git_checkout_symlinks=True
                    ),
                    "copied_cache": build_guard._allocated_bytes(
                        (cargo_home,), device, allow_cargo_git_checkout_symlinks=True
                    ),
                    "target": build_guard._allocated_bytes(
                        (target_dir,), device, allow_cargo_git_checkout_symlinks=True
                    ),
                    "tmp": build_guard._allocated_bytes(
                        (tmpdir,), device, allow_cargo_git_checkout_symlinks=True
                    ),
                }
                allocation["stage_total"] = sum(allocation.values())
                full_revalidate()
                return {
                    "sampled_ns": time.time_ns(),
                    "root_id": actual.root_id,
                    "volume_uuid": actual.volume_uuid,
                    "mount_device": actual.mount_device,
                    "mount_inode": actual.mount_inode,
                    "actual_free_bytes": actual.available_bytes,
                    "free_after_collector_reserves_bytes": reserved.available_bytes,
                    "collector_reserves": COLLECTOR_RESERVES,
                    "allocations": allocation,
                }

            commands_run = [{**item, "execution_argv": item["execution_argv"]} for item in commands]
            terminal = execute_commands(
                commands=commands_run,
                source=source,
                source_cache=source_cache,
                cargo_home=cargo_home,
                target_dir=target_dir,
                tmpdir=tmpdir,
                snapshot_provider=provider,
                root_fd=root_fd,
                device=device,
                source_pins={**initial_pins, "build_cache_manifest_sha256": copied_manifest},
                implementation_pins=implementation_pins,
                run_id=run_id,
                start_ns=start_wall_ns,
                revalidate=full_revalidate,
                deadline=deadline,
                verify_inputs=verify_inputs,
                stage_paths=stage_paths,
                cache_snapshot=lambda: _cache_snapshot(cargo_home),
                source_cache_manifest=lambda: metadata._locked_cache_manifest(source_cache),
                process_spawn_closure=process_closure,
                terminal_snapshot=terminal_snapshot,
            )
            return {
                "status": terminal["status"],
                "run_id": run_id,
                "commands_completed": len(terminal["completed_commands"]),
                "build_cache_manifest_sha256": copied_manifest,
                "build_cache_entries": copied_entries,
                "build_cache_bytes": copied_bytes,
                "terminal_receipt_path": TERMINAL_RELATIVE.as_posix(),
                "terminal_receipt_sha256": hashlib.sha256(canonical(terminal)).hexdigest(),
            }
        finally:
            os.close(root_fd)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--storage-config",
        type=Path,
        default=Path.home() / "Developer/memecoin-runtime/brain/config/storage-v1.json",
    )
    args = parser.parse_args(argv)
    try:
        result = run_build(args.storage_config)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "all_commands_passed" else 2
    except Exception as exc:
        reason = exc.code if isinstance(exc, BuildRunnerError) else type(exc).__name__
        if not re.fullmatch(r"[A-Za-z0-9_]+", reason):
            reason = "build_runner_failed"
        print(json.dumps({"status": "refused", "reason": reason}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
