"""Bounded background supervision for operator-owned source and paper exams.

The operator process only holds a lease write-end and an in-memory handle. A
daemon supervisor inherits an exclusive flock for the task key and starts the
domain command through ``memecoin_operational.supervisor.child``. The domain
command must read ``MEMECOIN_LEASE_FD`` (EOF means the supervisor is gone).
The supervisor reads ``MEMECOIN_PARENT_LEASE_FD`` (EOF means the operator is
gone) and then kills the domain process group. Locks are not rebuilt from
pid files.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import select
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from memecoin_operational.contract import GateError
from memecoin_operational.supervisor import Watch, child
from memecoin_runner.ledger import publish

Row = dict[str, Any]
_KEY = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_ACTIVE = frozenset({"collect", "paper", "auto"})
_GRACEFUL = frozenset(
    {
        "completed",
        "operator_cancelled",
        "operator_interrupted",
        "expired",
        "inactive",
        "duplicate",
    }
)
_SOURCE_EXIT = {10: "operator_cancelled", 11: "expired", 12: "inactive"}
_SOURCE_CHILD_S = 240.0
_MARGIN_S = 15.0
_COUNTER = 0
_COUNTER_LOCK = threading.Lock()


def _attempt_id() -> str:
    global _COUNTER
    with _COUNTER_LOCK:
        _COUNTER += 1
        number = _COUNTER
    return f"{time.time_ns()}-{os.getpid()}-{number}"


def _root(path: Path) -> Path:
    root = Path(path)
    if root.is_symlink():
        raise ValueError("symlink_root")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _deadline(seconds: float) -> float:
    if isinstance(seconds, bool) or type(seconds) not in (int, float):
        raise ValueError("invalid_deadline")
    value = float(seconds)
    if not 0 < value <= 600:
        raise ValueError("invalid_deadline")
    return value


def _key(key: str) -> str:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("invalid_key")
    return key


def _command(command: list[str]) -> list[str]:
    if not isinstance(command, list) or not command:
        raise ValueError("invalid_command")
    if any(type(part) is not str or not part or "\x00" in part for part in command):
        raise ValueError("invalid_command")
    return command


def _package_root() -> str:
    """Directory that contains this package. Passed on argv, never via PYTHONPATH."""
    return str(Path(__file__).resolve().parents[1])


def _env() -> dict[str, str]:
    """Child environment. PYTHONPATH is left untouched and ignored by ``-I``."""
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _isolated(body: str, *args: str) -> list[str]:
    """``python -I -B -c`` that inserts this checkout before importing."""
    script = "import sys\nsys.path.insert(0, sys.argv.pop(1))\n" + body
    return [sys.executable, "-I", "-B", "-c", script, _package_root(), *args]


def _dirs(root: Path) -> None:
    for name in ("locks", "history", "logs", "receipts", "cancel", "specs"):
        folder = root / "background" / name
        if folder.is_symlink():
            raise ValueError("symlink_background")
        folder.mkdir(parents=True, exist_ok=True)


def _rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _append(root: Path, key: str, record: Row) -> None:
    path = root / "background" / "history" / f"{key}.jsonl"
    if path.is_symlink():
        raise ValueError("symlink_history")
    line = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    raw = line.encode()
    if len(raw) > 512:
        raise ValueError("history_record_too_large")
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)


def history(root: Path, key: str) -> list[Row]:
    """Append-only attempt records for one task key. Empty when none exist."""
    path = _root(root) / "background" / "history" / f"{_key(key)}.jsonl"
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise ValueError("symlink_history")
    rows = []
    for line in path.read_text().splitlines():
        if line:
            rows.append(json.loads(line))
    return rows


def _emit(path: Path, value: Row) -> bool:
    if path.is_symlink():
        raise ValueError("symlink_receipt")
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        publish(path, value)
    except FileExistsError:
        return False
    return True


def _phase(category: str) -> str:
    if category in {"completed", "inactive", "expired"}:
        return "completed"
    return "interrupted"


def _finish(
    root: Path,
    key: str,
    attempt: str,
    started_ns: int,
    category: str,
    exit_code: int | None,
    log: str | None,
) -> Row:
    finished = time.time_ns()
    record: Row = {
        "at_ns": finished,
        "attempt_id": attempt,
        "category": category,
        "exit_code": exit_code,
        "key": key,
        "log": log,
        "phase": _phase(category),
        "started_ns": started_ns,
    }
    _append(root, key, record)
    receipt = dict(record, finished_ns=finished)
    _emit(root / "background" / "receipts" / f"{attempt}.json", receipt)
    return receipt


def _fatal(category: str) -> bool:
    return category not in _GRACEFUL


def _incident(
    root: Path, attempt: str, key: str, category: str, exit_code: int | None, log: str | None
) -> None:
    folder = root / "incidents"
    if folder.is_symlink():
        raise ValueError("symlink_incidents")
    folder.mkdir(exist_ok=True)
    _emit(
        folder / f"background-{attempt}.json",
        {
            "at_ns": time.time_ns(),
            "attempt_id": attempt,
            "category": category,
            "exit_code": exit_code,
            "kind": "background_failure",
            "log": log,
            "service": key,
        },
    )


def _load_mode(root: Path) -> str | None:
    path = root / "operator.sqlite"
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("operator_mode_unreadable")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        db.execute("PRAGMA query_only=ON")
        try:
            row = db.execute("SELECT mode FROM revisions ORDER BY revision DESC LIMIT 1").fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return None
            raise ValueError("operator_mode_unreadable") from exc
    finally:
        db.close()
    if row is None or type(row[0]) is not str:
        return None
    return row[0]


def owned_successor(root: Path) -> Path | None:
    """Operator-owned successor directory, or None while the original source is current."""
    from memecoin_operator.acquisition import _load_operator, current_source

    op = _load_operator(root)
    current = current_source(root)
    original = Path(op["source_root"]).resolve()
    owned = (root / "source").resolve()
    if current.resolve() != owned or owned == original or Path(root / "source").is_symlink():
        return None
    return owned


def _source_blocked(root: Path) -> str | None:
    marker = root / "background" / "source-fail-closed.json"
    if marker.is_symlink():
        raise ValueError("symlink_background")
    if marker.is_file():
        return "source_fail_closed"
    try:
        owned = owned_successor(root)
    except (OSError, ValueError):
        return "source_unverified"
    if owned is not None and (owned / "STOP").exists():
        return "source_stopped"
    return None


def _seal_source(
    root: Path, attempt: str, category: str, exit_code: int | None, log: str | None
) -> None:
    """Unknown source failure. Never writes the original source tree."""
    _emit(
        root / "background" / "source-fail-closed.json",
        {
            "at_ns": time.time_ns(),
            "attempt_id": attempt,
            "category": category,
            "exit_code": exit_code,
        },
    )
    try:
        owned = owned_successor(root)
    except (OSError, ValueError):
        return
    if owned is None:
        return
    incident = owned / f"incident-{attempt}.json"
    _emit(
        incident,
        {
            "at_ns": time.time_ns(),
            "attempt_id": attempt,
            "category": category,
            "exit_code": exit_code,
            "log": log,
        },
    )
    if not (owned / "STOP").exists():
        _emit(
            owned / "STOP",
            {
                "at_ns": time.time_ns(),
                "category": category,
                "incident": incident.name,
                "reason": "source_worker_failure",
            },
        )


def _interpret(key: str, result: Row, cancelled: bool, parent_dead: bool) -> str:
    if cancelled:
        return "operator_cancelled"
    category = str(result.get("category") or "worker_failed")
    if parent_dead and category != "completed":
        return "operator_interrupted"
    code = result.get("exit_code")
    if key == "source" and category == "worker_failed" and code in _SOURCE_EXIT:
        return str(_SOURCE_EXIT[code])
    return category


def _lease_open(fd: int) -> bool:
    readable, _, _ = select.select([fd], [], [], 0)
    if not readable:
        return True
    try:
        data = os.read(fd, 1)
    except OSError:
        return False
    return bool(data)


def _arm_parent(
    fd: int, cancel_path: Path, cancelled: threading.Event, dead: threading.Event
) -> None:
    if cancel_path.is_file():
        cancelled.set()
    if not _lease_open(fd):
        dead.set()
        return

    def watch() -> None:
        try:
            os.read(fd, 1)
        except OSError:
            pass
        if cancel_path.is_file():
            cancelled.set()
        dead.set()

    threading.Thread(target=watch, name="parent-lease", daemon=True).start()


def _log_name(root: Path, spec_log: str | None, log: Path) -> str | None:
    if spec_log and log.is_file():
        return spec_log
    if log.is_file():
        return _rel(root, log)
    return None


def supervise(spec_path: str) -> int:
    """Daemon entry. Holds the inherited task flock until the domain child ends."""
    spec = json.loads(Path(spec_path).read_text())
    root = _root(Path(spec["root"]))
    key = _key(spec["key"])
    command = _command(list(spec["command"]))
    seconds = _deadline(spec["seconds"])
    attempt = str(spec["attempt_id"])
    started_ns = int(spec["started_ns"])
    log = root / str(spec["log"])
    lock_fd = int(os.environ["MEMECOIN_SUPERVISOR_LOCK_FD"])
    parent_fd = int(os.environ["MEMECOIN_PARENT_LEASE_FD"])
    os.fstat(lock_fd)
    os.fstat(parent_fd)
    log_rel = str(spec["log"])
    _append(
        root,
        key,
        {
            "at_ns": started_ns,
            "attempt_id": attempt,
            "category": None,
            "exit_code": None,
            "key": key,
            "log": log_rel,
            "phase": "begun",
            "started_ns": started_ns,
        },
    )
    cancelled = threading.Event()
    dead = threading.Event()
    cancel_path = root / "background" / "cancel" / f"{attempt}.json"
    _arm_parent(parent_fd, cancel_path, cancelled, dead)
    result: Row
    if cancelled.is_set() or dead.is_set():
        result = {"exit_code": None, "category": "pending"}
    else:
        os.environ["MEMECOIN_BACKGROUND_BUDGET_S"] = str(seconds)
        watch = Watch(seconds)

        def guard() -> None:
            if cancel_path.is_file() or cancelled.is_set():
                raise GateError("operator_cancelled")
            if dead.is_set():
                raise GateError("operator_interrupted")
            if watch.remaining() <= 0:
                raise GateError("child_deadline")

        try:
            result = child(command, log, seconds, guard)
        except GateError as exc:
            result = {"exit_code": None, "category": str(exc)}
        except Exception:
            result = {"exit_code": None, "category": "supervisor_guard"}
    parent_dead = dead.is_set() or not _lease_open(parent_fd)
    category = _interpret(key, result, cancel_path.is_file() or cancelled.is_set(), parent_dead)
    exit_code = result.get("exit_code")
    code = exit_code if type(exit_code) is int else None
    pointer = _log_name(root, log_rel, log)
    try:
        _finish(root, key, attempt, started_ns, category, code, pointer)
        if _fatal(category):
            _incident(root, attempt, key, category, code, pointer)
            if key == "source":
                _seal_source(root, attempt, category, code, pointer)
    finally:
        os.close(lock_fd)
        os.close(parent_fd)
    return 0 if category in _GRACEFUL else 1


class Manager:
    """In-process owner of lease write-ends. Restarted processes do not signal old pids."""

    def __init__(self, root: Path) -> None:
        self.root = _root(root)
        _dirs(self.root)
        self._handles: dict[str, Row] = {}
        self._mu = threading.Lock()

    def start(self, key: str, command: list[str], seconds: float) -> Row:
        key = _key(key)
        command = _command(command)
        seconds = _deadline(seconds)
        if key == "source":
            reason = _source_blocked(self.root)
            if reason is not None:
                return {"started": False, "key": key, "reason": reason}
        with self._mu:
            current = self._handles.get(key)
            if current is not None and current["proc"].poll() is None:
                return {
                    "started": False,
                    "key": key,
                    "reason": "already_running",
                    "attempt_id": current["attempt_id"],
                }
        lock_path = self.root / "background" / "locks" / f"{key}.lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        write_fd = -1
        try:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"started": False, "key": key, "reason": "already_running"}
            attempt = _attempt_id()
            started_ns = time.time_ns()
            log_rel = f"background/logs/{key}-{attempt}.log"
            spec = {
                "attempt_id": attempt,
                "command": command,
                "key": key,
                "log": log_rel,
                "root": str(self.root.resolve()),
                "seconds": seconds,
                "started_ns": started_ns,
            }
            spec_path = self.root / "background" / "specs" / f"{attempt}.json"
            _emit(spec_path, spec)
            read_fd, write_fd = os.pipe()
            os.set_inheritable(read_fd, True)
            os.set_inheritable(lock_fd, True)
            os.set_inheritable(write_fd, False)
            env = _env()
            env["MEMECOIN_PARENT_LEASE_FD"] = str(read_fd)
            env["MEMECOIN_SUPERVISOR_LOCK_FD"] = str(lock_fd)
            try:
                proc = subprocess.Popen(
                    _isolated(
                        "from memecoin_operator.background import supervise\n"
                        "raise SystemExit(supervise(sys.argv[1]))\n",
                        str(spec_path),
                    ),
                    env=env,
                    pass_fds=(read_fd, lock_fd),
                    start_new_session=True,
                    cwd=str(self.root),
                )
            except Exception:
                os.close(read_fd)
                raise
            os.close(read_fd)
            handle: Row = {
                "attempt_id": attempt,
                "key": key,
                "lease": write_fd,
                "proc": proc,
                "started_ns": started_ns,
            }
            write_fd = -1
            with self._mu:
                self._handles[key] = handle
        finally:
            os.close(lock_fd)
            if write_fd >= 0:
                os.close(write_fd)
        return {
            "started": True,
            "key": key,
            "attempt_id": attempt,
            "supervisor_pid": proc.pid,
            "log": log_rel,
        }

    def poll(self) -> Row:
        with self._mu:
            items = list(self._handles.items())
        running = []
        reaped = []
        for key, handle in items:
            proc: subprocess.Popen[bytes] = handle["proc"]
            if proc.poll() is None:
                running.append({"key": key, "attempt_id": handle["attempt_id"], "state": "running"})
                continue
            with self._mu:
                self._handles.pop(key, None)
            reaped.append(self._collect(handle))
        return {"running": running, "reaped": reaped}

    def stop(self) -> Row:
        """Intentional pause/stop. Records operator_cancelled and does not write source STOP."""
        with self._mu:
            handles = list(self._handles.values())
        for handle in handles:
            self._cancel(handle)
            self._close_lease(handle)
        tasks = []
        for handle in handles:
            proc: subprocess.Popen[bytes] = handle["proc"]
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
            with self._mu:
                self._handles.pop(handle["key"], None)
            tasks.append(self._collect(handle))
        return {"cancelled": [handle["key"] for handle in handles], "tasks": tasks}

    def _cancel(self, handle: Row) -> None:
        folder = self.root / "background" / "cancel"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{handle['attempt_id']}.json"
        if path.exists():
            return
        temporary = folder / f".{handle['attempt_id']}.tmp"
        temporary.write_text(
            json.dumps(
                {
                    "at_ns": time.time_ns(),
                    "attempt_id": handle["attempt_id"],
                    "category": "operator_cancelled",
                    "key": handle["key"],
                },
                sort_keys=True,
            )
        )
        os.replace(temporary, path)

    def _close_lease(self, handle: Row) -> None:
        fd = handle.get("lease", -1)
        if type(fd) is int and fd >= 0:
            os.close(fd)
            handle["lease"] = -1

    def _collect(self, handle: Row) -> Row:
        self._close_lease(handle)
        attempt = str(handle["attempt_id"])
        receipt_path = self.root / "background" / "receipts" / f"{attempt}.json"
        proc: subprocess.Popen[bytes] = handle["proc"]
        if receipt_path.is_file():
            return json.loads(receipt_path.read_text())
        code = proc.returncode
        category = "supervisor_lost"
        receipt = _finish(
            self.root,
            str(handle["key"]),
            attempt,
            int(handle["started_ns"]),
            category,
            code if type(code) is int else None,
            None,
        )
        if str(handle["key"]) == "source" and code not in (0, None):
            _incident(self.root, attempt, "source", category, code, None)
            _seal_source(self.root, attempt, category, code if type(code) is int else None, None)
        return receipt


def _inner_budget() -> float:
    raw = os.environ.get("MEMECOIN_BACKGROUND_BUDGET_S")
    if raw is None:
        return _SOURCE_CHILD_S
    try:
        outer = float(raw)
    except ValueError:
        return _SOURCE_CHILD_S
    inner = min(_SOURCE_CHILD_S, outer - _MARGIN_S)
    if inner < 1:
        return 1.0
    return inner


def _real_tick_command(root: Path) -> list[str]:
    """Pinned tick. Lease EOF kills the whole session, including grandchildren."""
    return _isolated(
        "import os, signal, threading\n"
        "from pathlib import Path\n"
        "def _watch() -> None:\n"
        "    try:\n"
        "        os.read(int(os.environ['MEMECOIN_LEASE_FD']), 1)\n"
        "    finally:\n"
        "        try:\n"
        "            os.killpg(os.getpid(), signal.SIGKILL)\n"
        "        finally:\n"
        "            os._exit(125)\n"
        "threading.Thread(target=_watch, daemon=True).start()\n"
        "from memecoin_operator.background import source_tick\n"
        "raise SystemExit(source_tick(Path(sys.argv[1])))\n",
        str(root.resolve()),
    )


def _tick_command(root: Path, op: Row, owned: Path | None) -> list[str]:
    raw = os.environ.get("MEMECOIN_SYNTHETIC_TICK")
    if raw is None:
        return _real_tick_command(root)
    marker = root / "source" / "SYNTHETIC"
    allowed = (
        op.get("data_kind") == "synthetic_fixture"
        and owned is not None
        and marker.is_file()
        and not marker.is_symlink()
        and marker.read_text() == "synthetic_fixture\n"
    )
    if not allowed:
        return _real_tick_command(root)
    command = json.loads(raw)
    return _command(command)


def source_tick(root: Path) -> int:
    """One real successor tick. Transport policy stays inside the source."""
    from memecoin_operator.acquisition import run_source

    try:
        run_source(Path(root))
    except BlockingIOError:
        return 12
    return 0


def source_command(root: Path) -> list[str]:
    """Domain argv for Manager.start. ``source_main`` consumes the supervisor lease."""
    return _isolated(
        "from pathlib import Path\n"
        "from memecoin_operator.background import source_main\n"
        "raise SystemExit(source_main(Path(sys.argv[1])))\n",
        str(Path(root).resolve()),
    )


def paper_command(runtime: Path, exam_root: Path) -> list[str]:
    """Paper argv. The exam runtime is inserted before any project import."""
    script = (
        "import os, sys\n"
        "sys.path.insert(0, sys.argv.pop(1))\n"
        "from memecoin_operational.worker import lease_watchdog\n"
        "lease_watchdog(int(os.environ['MEMECOIN_LEASE_FD']))\n"
        "from memecoin_paper.runtime import main\n"
        "main()\n"
    )
    return [
        sys.executable,
        "-I",
        "-B",
        "-c",
        script,
        str(Path(runtime).resolve()),
        str(Path(exam_root).resolve()),
        "paper",
    ]


def _source_code(category: str) -> int:
    return {"operator_cancelled": 10, "expired": 11, "inactive": 12, "completed": 0}.get(
        category, 1
    )


def source_main(root: Path) -> int:
    """One supervised successor tick. Does not create, authorize, or renew a source."""
    from memecoin_operational.worker import lease_watchdog

    root = Path(root)
    lease = os.environ.get("MEMECOIN_LEASE_FD")
    if lease is None:
        _incident(root, _attempt_id(), "source", "supervisor_guard", None, None)
        return 1
    lease_watchdog(int(lease))
    try:
        return _source_main(root)
    except Exception as exc:
        attempt = _attempt_id()
        _incident(root, attempt, "source", "supervisor_guard", None, None)
        _seal_source(root, attempt, "supervisor_guard", None, None)
        try:
            print(
                json.dumps(
                    {"category": "supervisor_guard", "error_type": type(exc).__name__},
                    sort_keys=True,
                ),
                flush=True,
            )
        except OSError:
            pass
        return 1


def _source_main(root: Path) -> int:
    from memecoin_continuous.runtime import guard_source
    from memecoin_operator import acquisition as acquisition

    try:
        op = acquisition._load_operator(root)
    except ValueError as exc:
        if str(exc) in {"operator_expired", "operator_stop"}:
            return _source_code("expired" if str(exc) == "operator_expired" else "inactive")
        raise
    now = time.time_ns()
    try:
        acquisition._clock_bounds(op, now)
    except ValueError as exc:
        if str(exc) == "operator_expired":
            return _source_code("expired")
        raise
    if acquisition._stop_reason(root) is not None:
        return _source_code("inactive")
    if _load_mode(root) not in _ACTIVE:
        return _source_code("inactive")
    owned = owned_successor(root)
    if owned is None or not acquisition._authorization_bound(owned):
        return _source_code("inactive")
    if (owned / "STOP").exists():
        return _source_code("inactive")
    config = guard_source(owned)
    if type(config.get("expires_ns")) is not int:
        raise ValueError("source_expiry_missing")
    if now >= config["expires_ns"] or now >= op["expires_ns"]:
        return _source_code("expired")
    command = _tick_command(root, op, owned)
    budget = _inner_budget()
    log = root / "background" / "logs" / f"source-tick-{time.time_ns()}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    lock_path = owned / ".operator-source.lock"
    lock_handle = lock_path.open("a")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _source_code("inactive")
        watch = Watch(budget)
        expires_ns = int(config["expires_ns"])
        operator_expires = int(op["expires_ns"])

        def guard() -> None:
            if watch.remaining() <= 0:
                raise GateError("child_deadline")
            if _load_mode(root) not in _ACTIVE or acquisition._stop_reason(root) is not None:
                raise GateError("operator_cancelled")
            if (owned / "STOP").exists():
                raise GateError("operator_cancelled")
            if time.time_ns() >= expires_ns or time.time_ns() >= operator_expires:
                raise GateError("expired")
            guard_source(owned)

        try:
            result = child(command, log, budget, guard)
        except GateError as exc:
            result = {"exit_code": None, "category": str(exc)}
    finally:
        lock_handle.close()
    category = _interpret("source", result, False, False)
    code = _source_code(category)
    if code == 1:
        pointer = _rel(root, log) if log.is_file() else None
        exit_code = result.get("exit_code")
        _seal_source(
            root,
            _attempt_id(),
            category,
            exit_code if type(exit_code) is int else None,
            pointer,
        )
    try:
        print(json.dumps({"category": category, "exit": code}, sort_keys=True), flush=True)
    except OSError:
        pass
    return code
