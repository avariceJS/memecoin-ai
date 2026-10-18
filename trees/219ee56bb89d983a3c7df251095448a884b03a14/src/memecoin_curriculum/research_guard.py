"""Read-only macOS process inventory and isolated research owner fencing."""

import ctypes
import errno
import fcntl
import os
import sqlite3
import struct
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from memecoin_ml.data import sha
from memecoin_registry.contracts import digest
from memecoin_runner.ledger import publish


def decode_procargs(raw):
    """KERN_PROCARGS2: argc, executable, padding, argc NUL-terminated arguments.

    The remaining buffer may contain environment bytes. Never decode or return it.
    """
    if len(raw) < 5:
        raise ValueError("procargs_short")
    argc = struct.unpack_from("=i", raw)[0]
    if not 1 <= argc <= 65536:
        raise ValueError("procargs_argc")
    pos = raw.index(b"\0", 4) + 1
    while pos < len(raw) and raw[pos] == 0:
        pos += 1
    argv = []
    for _ in range(argc):
        end = raw.index(b"\0", pos)
        argv.append(raw[pos:end].decode("utf-8", errors="surrogateescape"))
        pos = end + 1
    return argv


def process_argv(pid):
    if sys.platform != "darwin":
        raise ValueError("research_inventory_requires_macos")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.sysctl.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    libc.sysctl.restype = ctypes.c_int
    maximum, intsize = ctypes.c_int(), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
    if (
        libc.sysctl(
            (ctypes.c_int * 2)(1, 8), 2, ctypes.byref(maximum), ctypes.byref(intsize), None, 0
        )
        != 0
        or not 0 < maximum.value <= 8 * 1024**2
    ):
        raise ValueError("process_argmax_unavailable")
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN / KERN_PROCARGS2 (SDK sysctl.h).
    size = ctypes.c_size_t(maximum.value)
    buffer = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
        error = ctypes.get_errno()
        if error == errno.ESRCH:
            return None
        raise OSError(error, "process_argv_unavailable")
    return decode_procargs(buffer.raw[: size.value])


def argv_identity(argv):
    return digest(argv[1:])


@contextmanager
def research_lease(path):
    """Separate research lock: never take over a production lease or unlink locks."""
    if path.is_symlink():
        raise ValueError("research_lease_symlink")
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def inventory(rules, *, ignore_pids=()):
    """All Python processes plus project-related non-Python processes, no raw argv logs.

    This is a project-host guard, not an account-wide egress firewall. An unreadable
    candidate fails closed. Disappeared processes are recorded, never allowlisted.
    """
    allowed = {r["argv_sha"]: r for r in rules}
    text = subprocess.check_output(
        ["ps", "-axo", "pid=,ppid=,uid=,lstart=,comm=,args="], text=True, timeout=10
    )
    rows = []
    for line in text.splitlines():
        fields = line.split(maxsplit=8)
        if len(fields) != 9:
            raise ValueError("process_inventory_schema")
        pid, ppid, uid = map(int, fields[:3])
        shown = fields[8]
        if pid in {os.getpid(), *ignore_pids}:
            continue
        # comm is prepended to args, so the executable is not inferred from a
        # Python/code snippet occurring only inside an SSH transport command.
        executable = shown.split(maxsplit=1)[0]
        python = "python" in Path(executable).name.lower()
        project = "memecoin" in shown
        if not python and not project:
            continue
        if Path(executable).name in {"ssh", "scp", "sshd", "sshd-session", "zsh", "ps"}:
            continue
        if not python:
            # IDE processes are not provider workers; child interpreters are inventoried.
            if "Cursor" in executable or "Helper" in shown and "extension-host" in shown:
                continue
        row = dict(
            pid=pid,
            ppid=ppid,
            uid=uid,
            started=" ".join(fields[3:8]),
            executable=Path(executable).name,
        )
        try:
            argv = process_argv(pid)
        except (OSError, ValueError):
            row.update(role="unreadable", allowed=False)
        else:
            if argv is None:
                row.update(role="exited_during_inventory", allowed=True)
            else:
                pin = argv_identity(argv)
                rule = allowed.get(pin)
                if rule and sha(Path(argv[0]).resolve()) not in rule["executables"]:
                    rule = None
                row.update(
                    argv_sha=pin, role=rule["role"] if rule else "unknown", allowed=bool(rule)
                )
                if rule:
                    row["label"] = rule["label"]
        rows.append(row)
    return dict(at_ns=time.time_ns(), processes=rows, ready=all(r["allowed"] for r in rows))


def require_consumers(plan, receipt=None):
    result = inventory(plan["consumer_rules"])
    if receipt:
        publish(receipt, result)
    if not result["ready"]:
        bad = [r for r in result["processes"] if not r["allowed"]]
        raise ValueError("research_consumer_block:" + digest(bad))
    return result


def owner_guard(plan):
    """No production writes, no fallback UI expiry, no reliance on a live heartbeat."""
    owner = plan["retired_owner"]
    now = time.time_ns()
    if now >= plan["expires_ns"]:
        raise ValueError("research_envelope_expired")
    for path, pin in owner["pins"].items():
        if sha(Path(path)) != pin:
            raise ValueError("research_owner_pin_changed")
    if "source_journal" in owner:
        latest = max(Path(owner["source_journal"]).glob("event-*.json"))
        if str(latest) != owner["source_last_event"]:
            raise ValueError("research_source_progressed")
    if now < owner["source_expires_ns"] or now < owner["cooldown_until_ns"]:
        raise ValueError("research_owner_not_retired_or_cooldown")
    db = sqlite3.connect(f"file:{owner['operator_db']}?mode=ro", uri=True, timeout=1)
    try:
        db.execute("BEGIN")
        state = db.execute(
            "select revision,mode,phase from revisions order by revision desc limit 1"
        ).fetchone()
        busy = db.execute(
            "select count(*) from jobs where status in ('queued','running')"
        ).fetchone()[0]
    finally:
        db.close()
    if state != (owner["revision"], "paused", "paused") or busy:
        raise ValueError("research_owner_changed_or_busy")
    if any(now < t for t in owner["exam_expiries_ns"]):
        raise ValueError("research_managed_exam_active")
    return True
