#!/usr/bin/env python3
"""Run one offline Rust build stage under a fail-closed Kingston space monitor.

This is a bounded supervisor, not a storage writer or recovery service. It never
retries or removes stage data. A storage identity/probe/path/capacity failure
terminates the entire child process group and exits nonzero.
"""

from __future__ import annotations

import argparse
import os
import pwd
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Protocol

from memecoin_research_storage import (
    MacOSVolumeProbe,
    ResolvedRoot,
    StorageError,
    VolumeProbe,
    verify_root,
)

EXPECTED_ROOT_ID = "9428695af6d4f46c08e1a444cdc66de8"
EXPECTED_VOLUME_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"
EXPECTED_MOUNT = Path("/Volumes/Kingston")
EXPECTED_ROOT = EXPECTED_MOUNT / "BrainData"
STORAGE_CONFIG = Path.home() / ".config/brain-research/storage-v1.json"

GIB = 1024**3
ALLOCATION_CAP_BYTES = 32 * GIB
PEAK_MARGIN_BYTES = 8 * GIB
FREE_FLOOR_BYTES = 50 * GIB
INITIAL_FREE_BYTES = 90 * GIB
MIN_FREE_BYTES = FREE_FLOOR_BYTES + PEAK_MARGIN_BYTES
RUST_VERSION = "1.86.0"
POLL_SECONDS = 0.20
PROBE_TIMEOUT_SECONDS = 2.0
TERM_GRACE_SECONDS = 1.0


class GuardFailure(RuntimeError):
    """A fail-closed stage precondition or monitor failure."""


@dataclass(frozen=True)
class Snapshot:
    root_id: str
    volume_uuid: str
    mount_path: Path
    root_path: Path
    available_bytes: int
    mount_device: int
    mount_inode: int


class SnapshotProvider(Protocol):
    def inspect(self) -> Snapshot: ...


class ManagedProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class BoundStorageSnapshot:
    """Repeat the full configured-root verification on every monitor sample."""

    def __init__(
        self,
        config_path: Path = STORAGE_CONFIG,
        *,
        probe: VolumeProbe | None = None,
    ) -> None:
        self.config_path = config_path
        self.probe = probe or MacOSVolumeProbe()

    def inspect(self) -> Snapshot:
        root: ResolvedRoot = verify_root(
            self.config_path, min_free_bytes=MIN_FREE_BYTES, probe=self.probe
        )
        if (
            root.root_id != EXPECTED_ROOT_ID
            or root.volume_uuid.lower() != EXPECTED_VOLUME_UUID
            or root.mount_path != EXPECTED_MOUNT
            or root.path != EXPECTED_ROOT
        ):
            raise GuardFailure("bound_storage_identity_changed")
        return Snapshot(
            root.root_id,
            root.volume_uuid,
            root.mount_path,
            root.path,
            root.available_bytes,
            root.mount_device,
            root.mount_inode,
        )


def _check_unprivileged_root1() -> None:
    if os.getuid() != os.geteuid() or os.geteuid() == 0:
        raise GuardFailure("must_run_as_unprivileged_root1")
    try:
        username = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError as exc:
        raise GuardFailure("unrecognized_effective_user") from exc
    if username != "root1" or any(
        key in os.environ for key in ("SUDO_UID", "SUDO_GID", "SUDO_COMMAND")
    ):
        raise GuardFailure("must_run_as_unprivileged_root1")


def _plain_descendant(path: Path, root: Path, label: str) -> Path:
    if not path.is_absolute() or ".." in path.parts or "\x00" in str(path):
        raise GuardFailure(f"invalid_{label}_path")
    normalized = Path(os.path.abspath(path))
    try:
        relative = normalized.relative_to(root)
    except ValueError as exc:
        raise GuardFailure(f"{label}_outside_bound_root") from exc
    if not relative.parts:
        raise GuardFailure(f"{label}_must_be_child_path")
    cursor = root
    for part in relative.parts:
        cursor /= part
        try:
            st = cursor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(st.st_mode):
            raise GuardFailure(f"symlink_{label}_path")
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            raise GuardFailure(f"unsafe_{label}_path_component")
        if stat.S_IMODE(st.st_mode) & 0o022:
            raise GuardFailure(f"writable_by_other_{label}_path_component")
        if st.st_dev != root.stat().st_dev:
            raise GuardFailure(f"{label}_crosses_volume")
    return normalized


def _check_stage_paths(paths: Sequence[Path], snapshot: Snapshot) -> tuple[Path, ...]:
    labels = ("source", "cargo_home", "target_dir", "tmpdir")
    checked = tuple(
        _plain_descendant(path, snapshot.root_path, label)
        for path, label in zip(paths, labels, strict=True)
    )
    for index, left in enumerate(checked):
        for right in checked[index + 1 :]:
            if left in right.parents or right in left.parents:
                raise GuardFailure("stage_paths_overlap")
    for path, label in zip(checked, labels, strict=True):
        if path.exists() and not path.is_dir():
            raise GuardFailure(f"{label}_not_directory")
    git_marker = checked[0] / ".git"
    try:
        git_marker_stat = git_marker.lstat()
    except FileNotFoundError:
        git_marker_stat = None
    if git_marker_stat is not None and (
        not stat.S_ISDIR(git_marker_stat.st_mode)
        or git_marker_stat.st_uid != os.getuid()
        or git_marker_stat.st_dev != snapshot.mount_device
    ):
        # A Git worktree's .git file/symlink redirects source state outside this checkout.
        raise GuardFailure("source_checkout_must_own_git_directory")
    return checked


def _allocated_bytes(
    paths: Sequence[Path], root_device: int, *, allow_cargo_git_checkout_symlinks: bool = False
) -> int:
    """Count blocks while allowing only explicitly scoped internal source links."""
    total = 0
    stack = list(paths)
    source_root = paths[0]
    resolved_source = source_root.resolve(strict=False)
    cargo_checkout_root = paths[1] / "git" / "checkouts" if len(paths) > 1 else None
    resolved_cargo_checkout_root = (
        cargo_checkout_root.resolve(strict=False) if cargo_checkout_root is not None else None
    )
    while stack:
        current = stack.pop()
        try:
            st = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(st.st_mode):
            allowed_root: Path | None = None
            resolved_allowed_root: Path | None = None
            if current.is_relative_to(source_root):
                allowed_root = source_root
                resolved_allowed_root = resolved_source
            elif (
                allow_cargo_git_checkout_symlinks
                and cargo_checkout_root is not None
                and resolved_cargo_checkout_root is not None
                and current != cargo_checkout_root
                and current.is_relative_to(cargo_checkout_root)
            ):
                # Cargo's pinned Git checkout preserves upstream internal source links.
                # Bind the exception to one ordinary repo/revision tree, never the whole
                # checkouts directory or its structural repo/revision entries.
                relative = current.relative_to(cargo_checkout_root)
                if len(relative.parts) >= 3:
                    repository_root = cargo_checkout_root / relative.parts[0]
                    revision_root = repository_root / relative.parts[1]
                    try:
                        for structural in (cargo_checkout_root, repository_root, revision_root):
                            structural_stat = structural.lstat()
                            if (
                                not stat.S_ISDIR(structural_stat.st_mode)
                                or structural_stat.st_uid != os.getuid()
                                or structural_stat.st_dev != root_device
                            ):
                                raise GuardFailure("unsafe_cargo_checkout_structure")
                        link_text = os.readlink(current)
                        if os.path.isabs(link_text):
                            raise GuardFailure("absolute_cargo_checkout_symlink")
                        resolved_allowed_root = revision_root.resolve(strict=False)
                        resolved = current.resolve(strict=False)
                        resolved.relative_to(resolved_allowed_root)
                        try:
                            target_stat = resolved.stat()
                        except FileNotFoundError:
                            target_stat = None
                    except GuardFailure:
                        raise
                    except (OSError, RuntimeError, ValueError) as exc:
                        raise GuardFailure("unsafe_cargo_checkout_symlink") from exc
                    allowed_root = revision_root
            if allowed_root is None or resolved_allowed_root is None:
                raise GuardFailure("symlink_in_non_source_stage")
            try:
                # Upstream source trees can intentionally include dangling relative
                # links. Their normalized destination must stay in the exact source or
                # pinned Git checkout, and any existing target must stay on this volume.
                resolved = current.resolve(strict=False)
                resolved.relative_to(resolved_allowed_root)
                try:
                    target_stat = resolved.stat()
                except FileNotFoundError:
                    target_stat = None
            except (OSError, RuntimeError, ValueError) as exc:
                raise GuardFailure("unsafe_source_symlink") from exc
            if target_stat is not None and target_stat.st_dev != root_device:
                raise GuardFailure("source_symlink_crosses_volume")
            total += int(st.st_blocks) * 512
            continue
        if st.st_dev != root_device:
            raise GuardFailure("stage_tree_crosses_volume")
        if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
            raise GuardFailure("unsupported_stage_file_type")
        total += int(st.st_blocks) * 512
        if stat.S_ISDIR(st.st_mode):
            try:
                with os.scandir(current) as entries:
                    stack.extend(Path(entry.path) for entry in entries)
            except OSError as exc:
                raise GuardFailure("stage_tree_scan_failed") from exc
    return total


def _secure_mkdirs(paths: Sequence[Path], snapshot: Snapshot) -> None:
    root_fd = os.open(
        snapshot.root_path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        if os.fstat(root_fd).st_dev != snapshot.mount_device:
            raise GuardFailure("storage_root_descriptor_changed")
        for path in paths:
            parts = path.relative_to(snapshot.root_path).parts
            parent_fd = os.dup(root_fd)
            try:
                for part in parts[:-1]:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                    next_fd = os.open(
                        part,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_fd,
                    )
                    os.close(parent_fd)
                    parent_fd = next_fd
                leaf = parts[-1]
                try:
                    os.mkdir(leaf, mode=0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                leaf_fd = os.open(
                    leaf,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=parent_fd,
                )
                try:
                    st = os.fstat(leaf_fd)
                    if st.st_uid != os.getuid() or st.st_dev != snapshot.mount_device:
                        raise GuardFailure("unsafe_stage_directory")
                    if stat.S_IMODE(st.st_mode) & 0o077:
                        raise GuardFailure("stage_directory_not_private")
                finally:
                    os.close(leaf_fd)
            finally:
                os.close(parent_fd)
    finally:
        os.close(root_fd)


def _tool_version(command: str, env: dict[str, str]) -> str:
    try:
        result = subprocess.run(
            [command, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GuardFailure(f"{command}_version_probe_failed") from exc
    return result.stdout.strip()


def _check_rust_toolchain(env: dict[str, str]) -> None:
    rustc_version = _tool_version("rustc", env)
    cargo_version = _tool_version("cargo", env)
    if not rustc_version.startswith(f"rustc {RUST_VERSION} "):
        raise GuardFailure("rustc_version_not_pinned")
    if not cargo_version.startswith(f"cargo {RUST_VERSION} "):
        raise GuardFailure("cargo_version_not_pinned")


def _build_environment(paths: Sequence[Path]) -> dict[str, str]:
    source, cargo_home, target_dir, tmpdir = paths
    del source
    rustup_home = Path(pwd.getpwnam("root1").pw_dir) / ".rustup"
    # Keep only non-secret host settings needed to invoke the fixed local toolchain.
    allowed = (
        "PATH",
        "LANG",
        "LC_ALL",
        "TERM",
        "DEVELOPER_DIR",
        "SDKROOT",
        "MACOSX_DEPLOYMENT_TARGET",
    )
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(
        {
            "HOME": str(cargo_home),
            "CARGO_HOME": str(cargo_home),
            "CARGO_TARGET_DIR": str(target_dir),
            "TMPDIR": str(tmpdir),
            "TMP": str(tmpdir),
            "TEMP": str(tmpdir),
            "CARGO_NET_OFFLINE": "true",
            "RUSTUP_TOOLCHAIN": RUST_VERSION,
            # rustup's pinned compiler installation is a read-only host toolchain;
            # Cargo registry/cache and every build output remain on the bound volume.
            "RUSTUP_HOME": str(rustup_home),
        }
    )
    return env


def _terminate_group(process: ManagedProcess, *, grace_seconds: float) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            # Children in the process group may still be alive after the leader exits.
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            break
        time.sleep(0.05)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        if process.poll() is None:
            raise
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def _inspect_stage(
    snapshot_provider: SnapshotProvider,
    paths: Sequence[Path],
    first: Snapshot,
    *,
    allow_cargo_git_checkout_symlinks: bool = False,
) -> tuple[Snapshot, int]:
    current = snapshot_provider.inspect()
    current_paths = _check_stage_paths(paths, current)
    allocation = _allocated_bytes(
        current_paths,
        current.mount_device,
        allow_cargo_git_checkout_symlinks=allow_cargo_git_checkout_symlinks,
    )
    if current.available_bytes < MIN_FREE_BYTES + max(0, ALLOCATION_CAP_BYTES - allocation):
        raise GuardFailure("free_space_below_stage_envelope")
    if allocation > ALLOCATION_CAP_BYTES:
        raise GuardFailure("allocation_over_32_gib")
    if (
        current.root_id != first.root_id
        or current.volume_uuid.lower() != first.volume_uuid.lower()
        or current.mount_path != first.mount_path
        or current.root_path != first.root_path
        or current.mount_device != first.mount_device
        or current.mount_inode != first.mount_inode
    ):
        raise GuardFailure("storage_identity_changed")
    return current, allocation


def _bounded_inspect_stage(
    snapshot_provider: SnapshotProvider,
    paths: Sequence[Path],
    first: Snapshot,
    *,
    timeout_seconds: float,
    allow_cargo_git_checkout_symlinks: bool = False,
) -> tuple[Snapshot, int]:
    """Run filesystem/provider probes off-thread so their syscalls cannot hold supervision."""
    result: Queue[tuple[bool, object]] = Queue(maxsize=1)

    def inspect() -> None:
        try:
            result.put(
                (
                    True,
                    _inspect_stage(
                        snapshot_provider,
                        paths,
                        first,
                        allow_cargo_git_checkout_symlinks=allow_cargo_git_checkout_symlinks,
                    ),
                )
            )
        except BaseException as exc:
            result.put((False, exc))

    threading.Thread(target=inspect, daemon=True, name="brain-storage-probe").start()
    try:
        ok, value = result.get(timeout=timeout_seconds)
    except Empty as exc:
        raise GuardFailure("storage_probe_timeout") from exc
    if not ok:
        assert isinstance(value, BaseException)
        raise value
    assert isinstance(value, tuple)
    return value


def run_guarded_stage(
    command: Sequence[str],
    *,
    source: Path,
    cargo_home: Path,
    target_dir: Path,
    tmpdir: Path,
    initial: bool,
    snapshot_provider: SnapshotProvider,
    working_directory: Path | None = None,
    poll_seconds: float = POLL_SECONDS,
    term_grace_seconds: float = TERM_GRACE_SECONDS,
    allow_cargo_git_checkout_symlinks: bool = False,
    popen: Callable[..., ManagedProcess] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    if not command or poll_seconds <= 0 or term_grace_seconds < 0:
        raise GuardFailure("invalid_stage_arguments")
    _check_unprivileged_root1()
    first = snapshot_provider.inspect()
    paths = _check_stage_paths((source, cargo_home, target_dir, tmpdir), first)
    working_directory = working_directory or (paths[0] if paths[0].is_dir() else first.root_path)
    if working_directory != first.root_path:
        working_directory = _plain_descendant(
            working_directory, first.root_path, "working_directory"
        )
    if not working_directory.is_dir():
        raise GuardFailure("working_directory_missing")
    if initial and first.available_bytes < INITIAL_FREE_BYTES:
        raise GuardFailure("initial_free_below_90_gib")
    allocation = _allocated_bytes(
        paths,
        first.mount_device,
        allow_cargo_git_checkout_symlinks=allow_cargo_git_checkout_symlinks,
    )
    if allocation > ALLOCATION_CAP_BYTES:
        raise GuardFailure("allocation_over_32_gib")
    required_free = MIN_FREE_BYTES + max(0, ALLOCATION_CAP_BYTES - allocation)
    if first.available_bytes < required_free:
        raise GuardFailure("free_space_below_stage_envelope")

    _secure_mkdirs(paths[1:], first)
    # Full identity and all paths are rechecked immediately before process start.
    before_start = snapshot_provider.inspect()
    if before_start != first and (
        before_start.root_id,
        before_start.volume_uuid,
        before_start.mount_path,
        before_start.root_path,
        before_start.mount_device,
        before_start.mount_inode,
    ) != (
        first.root_id,
        first.volume_uuid,
        first.mount_path,
        first.root_path,
        first.mount_device,
        first.mount_inode,
    ):
        raise GuardFailure("storage_identity_changed_before_stage")
    paths = _check_stage_paths(paths, before_start)
    before_start_allocation = _allocated_bytes(
        paths,
        before_start.mount_device,
        allow_cargo_git_checkout_symlinks=allow_cargo_git_checkout_symlinks,
    )
    if before_start_allocation > ALLOCATION_CAP_BYTES:
        raise GuardFailure("allocation_over_32_gib")
    if before_start.available_bytes < MIN_FREE_BYTES + max(
        0, ALLOCATION_CAP_BYTES - before_start_allocation
    ):
        raise GuardFailure("free_space_below_stage_envelope")

    env = _build_environment(paths)
    _check_rust_toolchain(env)
    process = popen(
        list(command),
        cwd=working_directory,
        env=env,
        start_new_session=True,
        close_fds=True,
        pass_fds=(),
    )
    while True:
        try:
            current, _allocation = _bounded_inspect_stage(
                snapshot_provider,
                paths,
                first,
                timeout_seconds=PROBE_TIMEOUT_SECONDS,
                allow_cargo_git_checkout_symlinks=allow_cargo_git_checkout_symlinks,
            )
        except BaseException as exc:
            _terminate_group(process, grace_seconds=term_grace_seconds)
            if isinstance(exc, GuardFailure):
                raise
            raise GuardFailure("storage_probe_or_path_failed") from exc
        return_code = process.poll()
        if return_code is not None:
            try:
                # Cargo build scripts may leave background descendants. Close the group
                # before reporting either success or failure so no in-group writer survives.
                _terminate_group(process, grace_seconds=term_grace_seconds)
                if return_code != 0:
                    return int(return_code)
                # A final independent sample closes the child-exit/detach race.
                final, _allocation = _bounded_inspect_stage(
                    snapshot_provider,
                    paths,
                    first,
                    timeout_seconds=PROBE_TIMEOUT_SECONDS,
                    allow_cargo_git_checkout_symlinks=allow_cargo_git_checkout_symlinks,
                )
                if final.available_bytes < MIN_FREE_BYTES:
                    raise GuardFailure("final_storage_check_failed")
                return 0
            except BaseException:
                _terminate_group(process, grace_seconds=term_grace_seconds)
                raise
        try:
            sleep(poll_seconds)
        except BaseException:
            _terminate_group(process, grace_seconds=term_grace_seconds)
            raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--cargo-home", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--tmpdir", type=Path, required=True)
    parser.add_argument("--cwd", type=Path)
    parser.add_argument(
        "--initial",
        action="store_true",
        help="require at least 90 GiB free before the first stage",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        _check_unprivileged_root1()
        config = STORAGE_CONFIG
        # Preflight authenticates the configured binding before arguments start work.
        if not config.is_file():
            raise GuardFailure("storage_binding_missing")
        code = run_guarded_stage(
            command,
            source=args.source,
            cargo_home=args.cargo_home,
            target_dir=args.target_dir,
            tmpdir=args.tmpdir,
            initial=args.initial,
            snapshot_provider=BoundStorageSnapshot(config),
            working_directory=args.cwd,
        )
        return code
    except (GuardFailure, StorageError, OSError) as exc:
        reason = str(exc).split(":", 1)[0]
        if not reason.replace("_", "").isalnum():
            reason = "build_storage_guard_failed"
        print(f"build storage guard stopped: {reason}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
