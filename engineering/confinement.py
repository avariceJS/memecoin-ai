"""Fail-closed macOS Seatbelt execution and disposable, empirical qualification.

The trusted caller owns the root configuration. Policy/control state, credentials,
and protected data must be outside every granted root. This is an OS boundary for
local tools, not a sandbox in which a networked model client can run. No caller
environment, inherited descriptors, shell interpolation, or system.sb is used.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import selectors
import signal
import socket
import stat
import subprocess
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
DYLD_SUPPORT = Path("/System/Library/Sandbox/Profiles/dyld-support.sb")
SYSTEM_READ_ROOTS = ("/System/Library", "/usr/lib", "/usr/share", "/usr/bin", "/bin")
VERIFIER_ENV_KEYS = frozenset({"LANG", "LC_ALL", "TZ", "PYTHONHASHSEED", "PYTHONUTF8"})
MAX_PINNED_EXECUTABLE_BYTES = 512_000_000


class ConfinementError(RuntimeError):
    """Execution cannot be safely started with the requested boundary."""


@dataclass(frozen=True)
class SandboxResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    profile_sha256: str
    elapsed_seconds: float
    output_truncated: bool = False
    prelaunch_executable_sha256: str | None = None


def _prelaunch_executable_sha256(path: str | Path, expected_sha256: str) -> str:
    """Recheck a pinned canonical executable immediately before sandbox launch.

    This hashes the inode opened through a no-follow descriptor. Darwin's
    subprocess API later receives a path, so a trusted host process able to
    replace its resolution after open (including after this function returns)
    remains in the TCB; this is evidence of a prelaunch path check, not proof
    that sandbox-exec executed the hashed inode.
    """
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(char not in "0123456789abcdef" for char in expected_sha256)
    ):
        raise ConfinementError("Expected executable digest must be lowercase SHA-256")
    executable = Path(path)
    if not executable.is_absolute():
        raise ConfinementError("Pinned executable must be an absolute canonical path")
    try:
        if executable.resolve(strict=True) != executable:
            raise ConfinementError("Pinned executable path is not canonical")
        descriptor = os.open(
            executable,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
    except ConfinementError:
        raise
    except OSError as exc:
        raise ConfinementError("Pinned executable is unavailable or follows a symlink") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not before.st_mode & 0o111:
            raise ConfinementError("Pinned executable must be a regular executable file")
        if before.st_size > MAX_PINNED_EXECUTABLE_BYTES:
            raise ConfinementError("Pinned executable exceeds the verification size limit")
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if total > MAX_PINNED_EXECUTABLE_BYTES:
                raise ConfinementError("Pinned executable exceeds the verification size limit")
            digest.update(chunk)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if identity_before != identity_after:
            raise ConfinementError("Pinned executable changed while it was being verified")
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise ConfinementError("Pinned executable digest changed before sandbox launch")
        return actual_sha256
    except OSError as exc:
        raise ConfinementError("Pinned executable could not be verified") from exc
    finally:
        os.close(descriptor)


def _inside(path: Path, roots: Sequence[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _root(value: str | Path) -> Path:
    path = Path(value).resolve(strict=True)
    if not path.is_dir():
        raise ConfinementError(f"Not a directory: {path}")
    # Never admit a broad user, volume, temporary, or package-manager tree.
    forbidden = {
        "/",
        "/Users",
        "/Volumes",
        "/private",
        "/private/tmp",
        "/private/var",
        "/tmp",
        "/var",
        "/opt",
        "/opt/homebrew",
        "/opt/homebrew/Cellar",
        "/Library",
        str(Path.home()),
    }
    if str(path) in forbidden or path in Path.home().parents:
        raise ConfinementError(f"Root is too broad: {path}")
    return path


def python_runtime_roots(executable: str | Path) -> tuple[Path, ...]:
    """Explicit interpreter roots; never inspect a venv's packages or execute .pth.

    Libraries for optional native extensions must be granted separately. This
    includes the venv directory (if any) and the resolved interpreter installation,
    not all of Homebrew or the user's home.
    """
    original = Path(executable).absolute()
    resolved = original.resolve(strict=True)
    roots = [original.parent]
    link = original
    # Preserve every interpreter alias, including venv -> Homebrew opt ->
    # Cellar -> framework. Seatbelt checks intermediate symlink traversal too.
    for _ in range(32):
        if not link.is_symlink():
            break
        target = Path(os.readlink(link))
        link = target if target.is_absolute() else link.parent / target
        roots.append(link.parent)
    else:
        raise ConfinementError("Interpreter symlink chain is too deep")
    if (original.parent.parent / "pyvenv.cfg").is_file():
        roots.append(original.parent.parent)
    parts = resolved.parts
    if "Cellar" in parts:
        index = parts.index("Cellar")
        roots.append(Path(*parts[: index + 3]))
    elif "Python3.framework" in parts:
        index = parts.index("Python3.framework")
        roots.append(Path(*parts[: index + 1]))
    else:
        roots.append(resolved.parent)
    for root in roots:
        _root(root)
    return tuple(dict.fromkeys(roots))


class SandboxRunner:
    def __init__(
        self,
        *,
        writable_roots: Sequence[str | Path],
        read_roots: Sequence[str | Path] = (),
        home: str | Path,
        protected_roots: Sequence[str | Path] = (),
    ) -> None:
        if (
            platform.system() != "Darwin"
            or not SANDBOX_EXEC.is_file()
            or not DYLD_SUPPORT.is_file()
        ):
            raise ConfinementError("Qualified macOS sandbox-exec/dyld support unavailable")
        self.writable_roots = tuple(dict.fromkeys(_root(p) for p in writable_roots))
        if not self.writable_roots:
            raise ConfinementError("At least one task writable root is required")
        self.read_roots = tuple(
            dict.fromkeys([_root(p) for p in SYSTEM_READ_ROOTS] + [_root(p) for p in read_roots])
        )
        self.read_aliases = tuple(Path(p).absolute() for p in read_roots)
        self.home = Path(home).resolve(strict=True)
        if not self.home.is_dir() or not _inside(self.home, self.writable_roots):
            raise ConfinementError("HOME must be an existing task writable directory")
        for value in protected_roots:
            protected = Path(value).resolve(strict=True)
            grants = self.read_roots + self.writable_roots
            if _inside(protected, grants) or any(_inside(p, [protected]) for p in grants):
                raise ConfinementError(f"Protected path is within a granted root: {protected}")
        self.profile = self._profile()
        self.profile_sha256 = hashlib.sha256(self.profile.encode()).hexdigest()

    def _profile(self) -> str:
        def quote(path):
            return json.dumps(str(path))

        readable = self.read_roots + self.writable_roots + self.read_aliases
        read_filters = " ".join(f"(subpath {quote(p)})" for p in readable)
        write_filters = " ".join(f"(subpath {quote(p)})" for p in self.writable_roots)
        ancestors = " ".join(f"(path-ancestors {quote(p)})" for p in readable)
        return "\n".join(
            [
                "(version 1)",
                "(deny default)",
                # Apple loader support only: no system.sb's XPC, syslog or passwd rules.
                '(import "dyld-support.sb")',
                "(allow process-exec)",
                "(allow process-fork)",
                "(allow sysctl-read)",
                f"(allow file-read* file-test-existence file-map-executable {read_filters})",
                f"(allow file-read-metadata file-test-existence {ancestors})",
                f"(allow file-write* {write_filters})",
                '(allow file-read* file-write-data (literal "/dev/null"))',
                '(allow file-read* (literal "/dev/random") (literal "/dev/urandom"))',
                # There are deliberately no network, mach-lookup, signal, task-port,
                # preference, or broad file metadata grants.
            ]
        )

    def environment(self, overrides: dict[str, str] | None = None) -> dict[str, str]:
        environment = {
            "HOME": str(self.home),
            "TMPDIR": str(self.home),
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/usr/bin/false",
            "GIT_OPTIONAL_LOCKS": "0",
        }
        if overrides is None:
            return environment
        if type(overrides) is not dict or set(overrides) - VERIFIER_ENV_KEYS:
            raise ConfinementError("verifier_environment_not_allowed")
        if any(
            type(key) is not str or type(value) is not str or "\x00" in key + value
            for key, value in overrides.items()
        ):
            raise ConfinementError("verifier_environment_invalid")
        environment.update(overrides)
        return environment

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path,
        timeout: float = 30.0,
        max_output_bytes: int = 1_000_000,
        env_overrides: dict[str, str] | None = None,
        expected_executable_sha256: str | None = None,
    ) -> SandboxResult:
        if not argv or not all(isinstance(arg, str) and "\0" not in arg for arg in argv):
            raise ConfinementError("argv must contain non-NUL strings")
        if not Path(argv[0]).is_absolute():
            raise ConfinementError("Executable must be absolute; shell resolution is forbidden")
        prelaunch_executable_sha256 = None
        if expected_executable_sha256 is not None:
            prelaunch_executable_sha256 = _prelaunch_executable_sha256(
                argv[0], expected_executable_sha256
            )
        directory = Path(cwd).resolve(strict=True)
        if not directory.is_dir() or not _inside(directory, self.writable_roots + self.read_roots):
            raise ConfinementError("cwd must be within a granted task read or writable root")
        if timeout <= 0 or timeout > 3600:
            raise ConfinementError("Timeout must be in (0, 3600] seconds")
        if not 1 <= max_output_bytes <= 10_000_000:
            raise ConfinementError("Output limit must be in [1, 10000000] bytes")
        started = time.monotonic()
        with subprocess.Popen(
            [str(SANDBOX_EXEC), "-p", self.profile, *argv],
            cwd=directory,
            env=self.environment(env_overrides),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        ) as process:
            timed_out = False
            output_truncated = False
            buffers = {"stdout": bytearray(), "stderr": bytearray()}
            total = 0
            with selectors.DefaultSelector() as selector:
                for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, label)
                while selector.get_map():
                    remaining = started + timeout - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        room = max_output_bytes - total
                        buffers[key.data].extend(chunk[:room])
                        total += min(len(chunk), room)
                        if len(chunk) > room:
                            output_truncated = True
                            break
                    if output_truncated:
                        break
            if not timed_out and not output_truncated:
                try:
                    process.wait(timeout=max(0.001, started + timeout - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
            # Always clean the original process group, including children that
            # retained pipes after the leader exited. Never wait for pipe EOF
            # after timeout: an adversarial descendant could keep it open.
            process.poll()  # Reap an exited leader before Darwin's killpg check.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                # Darwin can report EPERM for a group containing only its
                # already-exited leader. A live leader must never be ignored.
                if process.poll() is None:
                    raise
            process.wait(timeout=5)
            stdout, stderr = bytes(buffers["stdout"]), bytes(buffers["stderr"])
        return SandboxResult(
            tuple(argv),
            process.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            timed_out,
            self.profile_sha256,
            time.monotonic() - started,
            output_truncated,
            prelaunch_executable_sha256,
        )


def qualify_sandbox(
    base_dir: str | Path,
    *,
    python_executable: str | Path,
    read_roots: Sequence[str | Path] = (),
    git_executable: str | Path = "/Library/Developer/CommandLineTools/usr/bin/git",
) -> dict:
    """Exercise real OS denials against disposable canaries, never real secrets.

    Call from the trusted supervisor, independently of generated task code. A
    successful report is host/profile/interpreter evidence, not an audit of all
    possible kernel vulnerabilities or a resource-exhaustion boundary.
    """
    with tempfile.TemporaryDirectory(prefix="confinement-", dir=base_dir) as temporary:
        base = Path(temporary).resolve()
        task, scratch, outside = (base / name for name in ("task", "scratch", "outside"))
        for directory in (task, scratch, outside):
            directory.mkdir()
        canaries = {}
        for name in ("credentials", "home", "runtime", "protected-data", "policy"):
            path = outside / name
            path.write_text("disposable-canary:" + name)
            canaries[name] = path
        (task / "escape").symlink_to(outside, target_is_directory=True)
        executable = str(Path(python_executable).absolute())
        git = str(Path(git_executable).resolve(strict=True))
        runner = SandboxRunner(
            writable_roots=[task, scratch],
            home=scratch,
            read_roots=[*python_runtime_roots(executable), *read_roots, Path(git).parent.parent],
            protected_roots=[outside],
        )
        checks = []

        def probe(name: str, source: str, *, expected: int = 0) -> None:
            result = runner.run([executable, "-I", "-c", source], cwd=task, timeout=15)
            checks.append(
                {
                    "name": name,
                    "passed": result.returncode == expected and not result.timed_out,
                    "returncode": result.returncode,
                    "stdout": result.stdout[-4096:],
                    "stderr": result.stderr[-4096:],
                    "timed_out": result.timed_out,
                }
            )

        denial = """
import errno, os, pathlib, socket, subprocess, sys
def denied(action):
    try:
        action()
    except OSError as exc:
        assert exc.errno in (errno.EPERM, errno.EACCES), repr(exc)
    else:
        raise AssertionError('OS allowed forbidden operation')
"""
        probe(
            "allowed_python_and_task_write",
            "import pathlib; pathlib.Path('ok').write_text('ok'); print('ok')",
        )
        probe(
            "stdlib_and_subprocess",
            "import json,hashlib,sqlite3,subprocess; "
            "assert subprocess.check_output(['/bin/echo','child']).strip()==b'child'",
        )
        for name, path in canaries.items():
            probe(
                "deny_read_" + name,
                denial + f"denied(lambda: pathlib.Path({str(path)!r}).read_bytes())",
            )
        probe(
            "deny_outside_write",
            denial + f"denied(lambda: pathlib.Path({str(outside / 'new')!r}).write_text('bad'))",
        )
        probe(
            "deny_policy_overwrite",
            denial + f"denied(lambda: pathlib.Path({str(canaries['policy'])!r}).write_text('bad'))",
        )
        probe(
            "deny_symlink_escape_read",
            denial + "denied(lambda: pathlib.Path('escape/credentials').read_bytes())",
        )
        probe(
            "deny_symlink_escape_write",
            denial + "denied(lambda: pathlib.Path('escape/new').write_text('bad'))",
        )
        data_alias = Path("/System/Volumes/Data" + str(canaries["credentials"]))
        if data_alias.exists():
            probe(
                "deny_apfs_data_volume_alias",
                denial + f"denied(lambda: pathlib.Path({str(data_alias)!r}).read_bytes())",
            )
        probe(
            "deny_hardlink_escape",
            denial + f"denied(lambda: os.link({str(canaries['credentials'])!r}, 'stolen-link'))",
        )
        probe("deny_parent_signal_zero", denial + f"denied(lambda: os.kill({os.getpid()}, 0))")
        probe(
            "sanitized_environment",
            "import os; assert set(os.environ)=="
            + repr(set(runner.environment()))
            + "; assert os.environ['HOME']=="
            + repr(str(scratch)),
        )
        with canaries["credentials"].open("rb") as inherited:
            descriptor = inherited.fileno()
            probe(
                "no_inherited_secret_descriptor",
                denial + f"\ntry:\n    os.read({descriptor},100)\n"
                "except OSError as exc:\n    assert exc.errno == errno.EBADF\n"
                "else:\n    raise AssertionError('inherited descriptor remained open')",
            )
        child = (
            denial + f"denied(lambda: pathlib.Path({str(canaries['credentials'])!r}).read_bytes())"
        )
        probe(
            "descendant_remains_confined",
            "import subprocess,sys; subprocess.run([sys.executable,'-I','-c',"
            + repr(child)
            + "],check=True)",
        )
        probe(
            "alternative_shell_remains_confined",
            "import subprocess; result=subprocess.run(['/bin/sh','-c',"
            + repr("cat " + str(canaries["credentials"]))
            + "],capture_output=True); assert result.returncode != 0; "
            "assert b'disposable-canary' not in result.stdout",
        )
        with (
            tempfile.TemporaryDirectory(prefix="bs-", dir="/tmp") as short_directory,
            socket.socket() as listener,
            socket.socket(socket.AF_UNIX) as unix_listener,
        ):
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            # Darwin sockaddr_un has a short path limit; keep the filesystem
            # object inside task scratch using a disposable short alias.
            alias = Path(short_directory) / "s"
            alias.symlink_to(scratch, target_is_directory=True)
            unix_path = alias / "control.sock"
            unix_listener.bind(str(unix_path))
            unix_listener.listen()
            probe(
                "deny_loopback_tcp",
                denial
                + f"denied(lambda: socket.create_connection({listener.getsockname()!r},timeout=1))",
            )
            probe(
                "deny_unix_control",
                denial
                + f"s=socket.socket(socket.AF_UNIX); denied(lambda: s.connect({str(unix_path)!r}))",
            )
            probe(
                "deny_loopback_udp",
                denial + "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); "
                "denied(lambda: s.sendto(b'canary',('127.0.0.1',9)))",
            )
        probe(
            "deny_raw_socket",
            denial
            + "denied(lambda: socket.socket(socket.AF_INET,socket.SOCK_RAW,socket.IPPROTO_ICMP))",
        )
        # Local repository publication uses a real disposable bare destination.
        source_repo, remote_repo = task / "repo", outside / "remote.git"
        subprocess.run([git, "init", "--bare", str(remote_repo)], check=True, capture_output=True)
        subprocess.run([git, "init", str(source_repo)], check=True, capture_output=True)
        (source_repo / "canary.txt").write_text("disposable publication canary")
        subprocess.run(
            [git, "-C", str(source_repo), "add", "canary.txt"], check=True, capture_output=True
        )
        subprocess.run(
            [
                git,
                "-C",
                str(source_repo),
                "-c",
                "user.name=Sandbox Qualification",
                "-c",
                "user.email=qualification.invalid",
                "commit",
                "-m",
                "canary",
            ],
            check=True,
            capture_output=True,
        )
        # Establish that this source can publish, using a *different* disposable
        # destination within the allowed task root. A missing HEAD cannot pass.
        allowed_remote = task / "allowed-remote.git"
        subprocess.run(
            [git, "init", "--bare", str(allowed_remote)], check=True, capture_output=True
        )
        probe(
            "allowed_task_git_publication",
            "import subprocess; subprocess.run(["
            + repr(git)
            + ",'-C',"
            + repr(str(source_repo))
            + ",'push',"
            + repr(str(allowed_remote))
            + ",'HEAD:refs/heads/main'],check=True)",
        )
        probe(
            "allowed_local_git",
            "import subprocess; subprocess.run(["
            + repr(git)
            + ",'-C',"
            + repr(str(source_repo))
            + ",'status','--porcelain'],check=True)",
        )
        probe(
            "deny_git_publication",
            "import subprocess; r=subprocess.run(["
            + repr(git)
            + ",'-C',"
            + repr(str(source_repo))
            + ",'push',"
            + repr(str(remote_repo))
            + ",'HEAD:refs/heads/main'],capture_output=True); assert r.returncode != 0",
        )
        checks.append(
            {
                "name": "outside_git_remote_unchanged",
                "passed": not (remote_repo / "refs/heads/main").exists(),
            }
        )
        checks.append(
            {
                "name": "outside_canaries_unchanged",
                "passed": all(
                    p.read_text() == "disposable-canary:" + name for name, p in canaries.items()
                )
                and not (outside / "new").exists(),
            }
        )
        return {
            "schema_version": 1,
            "qualified": all(c["passed"] for c in checks),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "uid": os.getuid(),
            "python_executable": executable,
            "python_realpath": str(Path(executable).resolve()),
            "profile_sha256": runner.profile_sha256,
            "dyld_support_sha256": hashlib.sha256(DYLD_SUPPORT.read_bytes()).hexdigest(),
            "read_roots": [str(p) for p in runner.read_roots],
            "checks": checks,
            "limitations": [
                "No model/client executed; no production/protected data accessed",
                "Not a kernel exploit or CPU/memory/process-count containment proof",
                "Raw socket denial may also follow ordinary uid privilege restrictions",
                "Qualification binds the actual OS, interpreter, roots and loader support",
            ],
        }
