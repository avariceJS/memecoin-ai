"""Inert macOS Seatbelt supervisor prototype for disposable Agave-build fixtures.

This module is a qualification harness, not a build runner. A passing filesystem
test does not establish mount identity: Seatbelt grants are path-based, so the
mountpoint replacement probe is intentionally part of the qualification gate.
"""

from __future__ import annotations

import hashlib
import os
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class ConfinementError(RuntimeError):
    """Invalid or unavailable OS confinement; callers must fail closed."""


OUTPUT_LIMIT = 64 * 1024
SYSTEM_READ_ROOTS = (
    "/System",
    "/usr",
    "/Library/Apple",
    "/private/var/db/timezone",
    "/dev",
)
HOME_ROOT = "/Users"


@dataclass(frozen=True)
class SandboxPolicy:
    stage_root: str
    read_roots: tuple[str, ...]
    executable_paths: tuple[str, ...]
    profile: str
    profile_sha256: str


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    observed_descendant_pids: tuple[int, ...]
    survivors_after_cleanup: tuple[int, ...]
    process_census_complete: bool


def _canonical_existing_dir(value: str | os.PathLike[str], code: str) -> str:
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except OSError as exc:
        raise ConfinementError(code) from exc
    if (
        not path.is_absolute()
        or str(path) != str(resolved)
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
    ):
        raise ConfinementError(code)
    return str(resolved)


def _canonical_executable(value: str | os.PathLike[str]) -> str:
    path = Path(value)
    try:
        resolved = path.resolve(strict=True)
        info = path.lstat()
    except OSError as exc:
        raise ConfinementError("executable_unavailable") from exc
    if (
        not path.is_absolute()
        or str(path) != str(resolved)
        or stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_mode & 0o022
        or not info.st_mode & 0o111
    ):
        raise ConfinementError("executable_identity_invalid")
    return str(resolved)


def _sbpl_quote(path: str) -> str:
    if not path.startswith("/") or any(ch in path for ch in '\\"\n\r\x00'):
        raise ConfinementError("profile_path_invalid")
    return f'"{path}"'


def build_policy(
    *,
    stage_root: str | os.PathLike[str],
    read_roots: Sequence[str | os.PathLike[str]],
    executable_paths: Sequence[str | os.PathLike[str]],
) -> SandboxPolicy:
    """Build a default-deny Seatbelt profile from exact existing roots.

    No root may be `/`, `/Users`, or an ancestor of the current home. The
    profile explicitly denies user-home reads and all network operations.
    Generated stage build scripts may execute only from the exact stage root.
    """
    if os.uname().sysname != "Darwin":
        raise ConfinementError("macos_required")
    stage = _canonical_existing_dir(stage_root, "stage_root_invalid")
    stage_info = os.stat(stage, follow_symlinks=False)
    if stage_info.st_uid != os.getuid() or stage_info.st_mode & 0o077:
        raise ConfinementError("stage_root_permissions")
    home = str(Path.home().resolve(strict=True))
    forbidden = ("/", HOME_ROOT, home)
    roots = {_canonical_existing_dir(root, "read_root_invalid") for root in read_roots}
    roots.update(_canonical_existing_dir(root, "system_root_missing") for root in SYSTEM_READ_ROOTS)
    roots.add(stage)
    for root in roots:
        overlaps_forbidden = any(
            root == denied or denied != "/" and root.startswith(denied.rstrip("/") + "/")
            for denied in forbidden
        )
        if overlaps_forbidden:
            raise ConfinementError("broad_or_home_read_root")
    execs = tuple(sorted({_canonical_executable(path) for path in executable_paths}))
    if not execs:
        raise ConfinementError("empty_executable_allowlist")
    read_terms = " ".join(f"(subpath {_sbpl_quote(root)})" for root in sorted(roots))
    map_terms = " ".join(f"(subpath {_sbpl_quote(root)})" for root in sorted(roots))
    exec_terms = " ".join(f"(literal {_sbpl_quote(path)})" for path in execs)
    profile = " ".join(
        (
            "(version 1)",
            "(deny default)",
            '(import "dyld-support.sb")',
            "(allow sysctl-read)",
            "(allow process-fork)",
            f"(allow process-exec* {exec_terms})",
            f"(allow process-exec* (subpath {_sbpl_quote(stage)}))",
            f"(allow file-read* {read_terms})",
            f"(allow file-map-executable {map_terms})",
            f"(allow file-map-executable {exec_terms})",
            *(
                "(allow file-read-metadata file-test-existence "
                f"(path-ancestors {_sbpl_quote(path)}))"
                for path in execs
            ),
            f"(allow file-read-metadata (path-ancestors {_sbpl_quote(stage)}))",
            f"(allow file-write* (subpath {_sbpl_quote(stage)}))",
            f"(deny file-read* (subpath {_sbpl_quote(HOME_ROOT)}))",
            f"(deny file-read* (subpath {_sbpl_quote(home)}))",
            "(deny network*)",
        )
    )
    return SandboxPolicy(
        stage_root=stage,
        read_roots=tuple(sorted(roots)),
        executable_paths=execs,
        profile=profile,
        profile_sha256=hashlib.sha256(profile.encode()).hexdigest(),
    )


def _process_table() -> dict[int, int]:
    result = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid="],
        check=False,
        capture_output=True,
        text=True,
        timeout=2,
        close_fds=True,
    )
    if result.returncode:
        raise ConfinementError("process_census_failed")
    table: dict[int, int] = {}
    try:
        for row in result.stdout.splitlines():
            fields = row.split()
            if len(fields) == 2:
                table[int(fields[0])] = int(fields[1])
    except ValueError as exc:
        raise ConfinementError("process_census_malformed") from exc
    return table


def _descendants(root_pid: int) -> set[int]:
    table = _process_table()
    found: set[int] = set()
    frontier = {root_pid}
    while frontier:
        children = {pid for pid, parent in table.items() if parent in frontier} - found
        found.update(children)
        frontier = children
    found.discard(root_pid)
    return found


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _signal_pids(pids: Sequence[int], sig: int) -> None:
    for pid in sorted(set(pids), reverse=True):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def _drain_limited(pipe: object, target: bytearray) -> None:
    stream = pipe
    while True:
        chunk = stream.read(8192)  # type: ignore[attr-defined]
        if not chunk:
            return
        remaining = OUTPUT_LIMIT - len(target)
        if remaining > 0:
            target.extend(chunk[:remaining])


def run_confined(
    argv: Sequence[str],
    policy: SandboxPolicy,
    *,
    timeout_seconds: float,
    extra_env: dict[str, str] | None = None,
    poll_seconds: float = 0.025,
) -> RunResult:
    """Run a disposable fixture with closed fds, a new process group, and census.

    `argv[0]` must be an executable in the policy allowlist. Environment is
    deliberately reconstructed; arbitrary parent variables are never inherited.
    """
    if (
        not argv
        or not os.path.isabs(argv[0])
        or os.path.realpath(argv[0]) not in policy.executable_paths
        or timeout_seconds <= 0
        or timeout_seconds > 3600
        or poll_seconds <= 0
    ):
        raise ConfinementError("run_arguments_invalid")
    sandbox_exec = "/usr/bin/sandbox-exec"
    if not os.path.isfile(sandbox_exec):
        raise ConfinementError("sandbox_exec_unavailable")
    env = {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": policy.stage_root,
    }
    allowed_env = {"CARGO_HOME", "CARGO_TARGET_DIR", "RUSTUP_HOME", "RUSTFLAGS", "RUSTUP_TOOLCHAIN"}
    if extra_env:
        if set(extra_env) - allowed_env:
            raise ConfinementError("environment_key_not_allowed")
        env.update(extra_env)
    process = subprocess.Popen(
        [sandbox_exec, "-p", policy.profile, *argv],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=policy.stage_root,
        env=env,
        close_fds=True,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    census_done = threading.Event()
    census_failed = threading.Event()
    observed: set[int] = set()

    def census() -> None:
        while not census_done.is_set():
            try:
                observed.update(_descendants(process.pid))
            except ConfinementError:
                census_failed.set()
                return
            census_done.wait(poll_seconds)

    readers = [
        threading.Thread(target=_drain_limited, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=_drain_limited, args=(process.stderr, stderr), daemon=True),
        threading.Thread(target=census, daemon=True),
    ]
    for thread in readers:
        thread.start()
    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    while process.poll() is None and not census_failed.is_set():
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(min(poll_seconds, max(0.001, deadline - time.monotonic())))
    if census_failed.is_set() or timed_out:
        try:
            observed.update(_descendants(process.pid))
        except ConfinementError:
            pass
        _signal_pids(tuple(observed), signal.SIGTERM)
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.15)
        except subprocess.TimeoutExpired:
            pass
        _signal_pids(tuple(observed), signal.SIGKILL)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=2)
    # Include detached descendants observed by the census and any still linked
    # to the supervisor after its main process exits.
    census_done.set()
    try:
        observed.update(_descendants(process.pid))
    except ConfinementError:
        census_failed.set()
    _signal_pids(tuple(observed), signal.SIGKILL)
    for thread in readers:
        thread.join(timeout=2)
    survivors = tuple(sorted(pid for pid in observed if _pid_alive(pid)))
    if survivors or census_failed.is_set() or any(thread.is_alive() for thread in readers):
        raise ConfinementError("process_census_incomplete_or_survivor")
    return RunResult(
        returncode=process.returncode,
        stdout=bytes(stdout),
        stderr=bytes(stderr),
        timed_out=timed_out,
        observed_descendant_pids=tuple(sorted(observed)),
        survivors_after_cleanup=survivors,
        process_census_complete=True,
    )


def path_replacement_is_safe(*, underlay_before: bytes, underlay_after: bytes) -> bool:
    """A qualification predicate: any changed underlay means no-go."""
    return underlay_before == underlay_after
