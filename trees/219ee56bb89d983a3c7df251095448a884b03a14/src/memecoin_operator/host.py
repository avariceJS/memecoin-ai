"""Read-only host display for the operator system tab.

Disk, power, and the approved launchd labels may be reused for at most 60 seconds.
The snapshot is not a policy or health gate. Source STOP, background manager jobs,
and raw captures are not read here.
"""

import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

_OPERATOR = "ai.memecoin.brain.operator"
_LEGACY = (
    "ai.memecoin.brain.b09.source",
    "ai.memecoin.brain.b09.audit",
    "ai.memecoin.brain.b10.paper",
    "ai.memecoin.brain.b10.train",
    "ai.memecoin.brain.b12.paper",
    "ai.memecoin.brain.b12r1.paper",
    "ai.memecoin.brain.b09r1.source",
    "ai.memecoin.brain.b09r1.audit",
    "ai.memecoin.brain.b10r1.paper",
    "ai.memecoin.brain.b10r1.train",
    "ai.memecoin.brain.b10r2.paper",
    "ai.memecoin.brain.b10r2.train",
    "ai.memecoin.brain.b12r2.paper",
)
_LEGACY_SET = frozenset(_LEGACY)
_TTL_S = 60.0
_BUDGET_S = 6.0
_CALL_TIMEOUT_S = 1.0
_WAIT_S = 7.0
_MAX_CACHE = 8
_STATE = re.compile(r"[A-Za-z0-9 ._-]{1,64}\Z")
_NUMBER = re.compile(r"-?\d+\Z")
_PERCENT = re.compile(r"(?<![A-Za-z0-9])(\d{1,3})%")
_BOOT_SEC = re.compile(r"sec\s*=\s*(\d+)")

_LOCK = threading.Lock()
_CACHE: OrderedDict[tuple, "_Entry"] = OrderedDict()
_FLIGHTS: dict[tuple, "_Flight"] = {}
_LAST_WALL: int | None = None
_LAST_MONO: int | None = None


class _Entry:
    __slots__ = ("measured_ns", "mono_ns", "payload")

    def __init__(self, measured_ns: int, mono_ns: int, payload: dict):
        self.measured_ns = measured_ns
        self.mono_ns = mono_ns
        self.payload = payload


class _Flight:
    __slots__ = ("entry", "event", "failed")

    def __init__(self):
        self.event = threading.Event()
        self.entry: _Entry | None = None
        self.failed = False


def _clear() -> None:
    """Drop the process-local display cache. Tests and clock regression use this."""
    global _LAST_WALL, _LAST_MONO
    with _LOCK:
        _CACHE.clear()
        _FLIGHTS.clear()
        _LAST_WALL = None
        _LAST_MONO = None


def _home() -> Path:
    return Path.home()


def _blank(reason: str) -> dict:
    return {
        "measured_ns": time.time_ns(),
        "cache_age_s": 0.0,
        "reason": reason,
        "disk": _disk_unknown("disk_unavailable"),
        "power": _power_unknown("power_unavailable"),
        "uptime": _uptime_unknown(),
        "operator": _operator_unknown(),
        "services": [],
        "rejected_count": 0,
        "config_reason": None,
    }


def _disk_unknown(reason: str) -> dict:
    return {"total_bytes": None, "used_bytes": None, "free_bytes": None, "reason": reason}


def _power_unknown(reason: str) -> dict:
    return {"source": None, "percent": None, "reason": reason}


def _uptime_unknown() -> dict:
    return {
        "process_s": None,
        "process_pid": None,
        "process_reason": "process_not_measured",
        "boot_s": None,
        "boot_reason": "boot_not_measured",
    }


def _operator_unknown() -> dict:
    return {
        "label": _OPERATOR,
        "loaded": None,
        "disposition": None,
        "pid": None,
        "state": None,
        "runs": None,
        "last_exit": None,
        "reason": None,
    }


def _observe_clocks() -> None:
    global _LAST_WALL, _LAST_MONO
    wall = time.time_ns()
    mono = time.monotonic_ns()
    with _LOCK:
        regressed = (_LAST_WALL is not None and wall < _LAST_WALL) or (
            _LAST_MONO is not None and mono < _LAST_MONO
        )
        _LAST_WALL = wall
        _LAST_MONO = mono
        if regressed:
            _CACHE.clear()


def _age_s(entry: _Entry) -> float:
    return (time.monotonic_ns() - entry.mono_ns) / 1e9


def _fresh(entry: _Entry) -> bool:
    age = _age_s(entry)
    return 0 <= age <= _TTL_S


def _lookup(key: tuple) -> _Entry | None:
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        if not _fresh(entry):
            del _CACHE[key]
            return None
        _CACHE.move_to_end(key)
        return entry


def _store(key: tuple, payload: dict) -> _Entry:
    entry = _Entry(time.time_ns(), time.monotonic_ns(), payload)
    with _LOCK:
        _CACHE[key] = entry
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_CACHE:
            _CACHE.popitem(last=False)
    return entry


def _expose(entry: _Entry, *, fresh: bool) -> dict:
    out = copy.deepcopy(entry.payload)
    out["measured_ns"] = entry.measured_ns
    out["cache_age_s"] = 0.0 if fresh else max(0.0, _age_s(entry))
    return out


def _claim(key: tuple) -> tuple[_Flight | _Entry | None, str]:
    with _LOCK:
        flight = _FLIGHTS.get(key)
        if flight is not None:
            return flight, "waiter"
        entry = _CACHE.get(key)
        if entry is not None and _fresh(entry):
            _CACHE.move_to_end(key)
            return entry, "cached"
        created = _Flight()
        _FLIGHTS[key] = created
        return created, "leader"


def _release(key: tuple, flight: _Flight, entry: _Entry | None, failed: bool) -> None:
    flight.entry = entry
    flight.failed = failed
    with _LOCK:
        if _FLIGHTS.get(key) is flight:
            del _FLIGHTS[key]
    flight.event.set()


def _config_token(root: Path) -> str:
    path = root / "config.json"
    try:
        payload = path.read_bytes()
    except OSError:
        return "missing"
    return hashlib.sha256(payload).hexdigest()


def _labels(root: Path) -> tuple[list[str], int, str | None]:
    path = root / "config.json"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], 0, "config_missing"
    except OSError:
        return [], 0, "config_unreadable"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], 0, "config_malformed"
    if not isinstance(data, dict):
        return [], 0, "config_malformed"
    if "managed_legacy_labels" not in data:
        return list(_LEGACY), 0, None
    managed = data["managed_legacy_labels"]
    if not isinstance(managed, list):
        return [], 0, "config_labels_malformed"
    accepted: list[str] = []
    rejected = 0
    for item in managed:
        if item == _OPERATOR or item in accepted:
            continue
        if item in _LEGACY_SET:
            accepted.append(item)
            continue
        rejected += 1
    return accepted, rejected, None


def _number(value: str, *, minimum: int | None = None) -> int | None:
    if not _NUMBER.fullmatch(value):
        return None
    number = int(value)
    if number > 2**31 - 1 or number < -(2**31):
        return None
    if minimum is not None and number < minimum:
        return None
    return number


def _parse_job(text: str) -> tuple[dict, bool]:
    """Top-level launchctl fields only. Nested coalition state is not the job."""
    depth = 0
    found = {"state": None, "runs": None, "pid": None, "last_exit": None}
    saw = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        opens = stripped.count("{")
        closes = stripped.count("}")
        if depth == 1 and "=" in stripped and opens == 0:
            key, value = (part.strip() for part in stripped.split("=", 1))
            if key == "state" and found["state"] is None:
                found["state"] = value if _STATE.fullmatch(value) else None
                saw = True
            elif key == "runs" and found["runs"] is None:
                found["runs"] = _number(value, minimum=0)
                saw = True
            elif key == "pid" and found["pid"] is None:
                found["pid"] = _number(value, minimum=1)
                saw = True
            elif key == "last exit code" and found["last_exit"] is None:
                found["last_exit"] = _number(value)
                saw = True
        depth = max(0, depth + opens - closes)
    return found, saw


def _run(argv: list[str], deadline: float) -> tuple[subprocess.CompletedProcess | None, str | None]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, "inspection_budget"
    timeout = min(_CALL_TIMEOUT_S, remaining)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except (FileNotFoundError, OSError):
        return None, "unavailable"
    return proc, None


def _disk(root: Path) -> dict:
    try:
        usage = shutil.disk_usage(root)
        numbers = (usage.total, usage.used, usage.free)
    except OSError:
        return _disk_unknown("disk_unavailable")
    except Exception:
        return _disk_unknown("disk_unavailable")
    if any(
        isinstance(number, bool) or not isinstance(number, int) or number < 0 for number in numbers
    ):
        return _disk_unknown("disk_unavailable")
    return {
        "total_bytes": numbers[0],
        "used_bytes": numbers[1],
        "free_bytes": numbers[2],
        "reason": None,
    }


def _parse_power(text: str) -> dict:
    source = None
    for line in text.splitlines():
        if "Now drawing from" not in line:
            continue
        if "Battery Power" in line:
            source = "battery"
        elif "AC Power" in line:
            source = "ac"
        break
    percent = None
    match = _PERCENT.search(text)
    if match:
        value = int(match.group(1))
        if 0 <= value <= 100:
            percent = value
    if source is None and percent is None:
        return _power_unknown("power_unparsed")
    return {"source": source, "percent": percent, "reason": None}


def _power(deadline: float) -> dict:
    if sys.platform != "darwin":
        return _power_unknown("power_unsupported")
    proc, reason = _run(["pmset", "-g", "batt"], deadline)
    if reason == "inspection_budget":
        return _power_unknown("inspection_budget")
    if reason == "timeout":
        return _power_unknown("power_timeout")
    if reason is not None or proc is None or proc.returncode != 0:
        return _power_unknown("power_unavailable")
    return _parse_power(proc.stdout or "")


def _service(label: str, deadline: float) -> dict:
    record = {
        "label": label,
        "loaded": None,
        "pid": None,
        "state": None,
        "runs": None,
        "last_exit": None,
        "reason": None,
    }
    proc, reason = _run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], deadline)
    if reason == "inspection_budget":
        record["reason"] = "inspection_budget"
        return record
    if reason == "timeout":
        record["reason"] = "launchctl_timeout"
        return record
    if reason is not None or proc is None:
        record["reason"] = "launchctl_unavailable"
        return record
    if proc.returncode != 0:
        record["loaded"] = False
        return record
    parsed, saw = _parse_job(proc.stdout or "")
    record.update(parsed)
    record["loaded"] = True
    if not saw:
        record["reason"] = "launchctl_unparsed"
    return record


def _operator(service: dict) -> dict:
    out = dict(service)
    loaded = service["loaded"]
    if loaded is True:
        out["disposition"] = "launchd"
        return out
    if loaded is not False:
        out["disposition"] = None
        return out
    try:
        installed = (_home() / "Library" / "LaunchAgents" / (_OPERATOR + ".plist")).is_file()
    except OSError:
        out["disposition"] = None
        if out["reason"] is None:
            out["reason"] = "plist_unreadable"
        return out
    out["disposition"] = "not_loaded" if installed else "dev"
    return out


def _process_uptime(
    pid: int | None, deadline: float
) -> tuple[float | None, int | None, str | None]:
    if pid is None:
        return None, None, "process_not_running"
    proc, reason = _run(["ps", "-o", "etimes=", "-p", str(pid)], deadline)
    if reason == "inspection_budget":
        return None, pid, "inspection_budget"
    if reason is not None or proc is None or proc.returncode != 0:
        return None, pid, "process_unavailable"
    text = (proc.stdout or "").strip()
    if not _NUMBER.fullmatch(text):
        return None, pid, "process_unavailable"
    seconds = int(text)
    if seconds < 0:
        return None, pid, "process_unavailable"
    return float(seconds), pid, None


def _boot(deadline: float) -> tuple[float | None, str | None]:
    if sys.platform != "darwin":
        return None, "boot_unsupported"
    proc, reason = _run(["sysctl", "-n", "kern.boottime"], deadline)
    if reason == "inspection_budget":
        return None, "inspection_budget"
    if reason == "timeout":
        return None, "boot_timeout"
    if reason is not None or proc is None or proc.returncode != 0:
        return None, "boot_unavailable"
    match = _BOOT_SEC.search(proc.stdout or "")
    if match is None:
        return None, "boot_unparsed"
    started = int(match.group(1))
    now = time.time()
    if started <= 0 or now < started:
        return None, "boot_unparsed"
    return now - started, None


def _inspect(root: Path) -> dict:
    deadline = time.monotonic() + _BUDGET_S
    disk = _disk(root)
    power = _power(deadline)
    labels, rejected, config_reason = _labels(root)
    operator = _operator(_service(_OPERATOR, deadline))
    if operator["loaded"] is True and operator["pid"] is not None:
        process_s, process_pid, process_reason = _process_uptime(operator["pid"], deadline)
    elif operator["loaded"] is True:
        process_s, process_pid, process_reason = None, None, "process_not_running"
    elif operator["disposition"] == "dev":
        process_s, process_pid, process_reason = None, None, "operator_not_launchd"
    elif operator["disposition"] == "not_loaded":
        process_s, process_pid, process_reason = None, None, "operator_not_loaded"
    else:
        process_s, process_pid, process_reason = None, None, "process_not_measured"
    services = [_service(label, deadline) for label in labels]
    boot_s, boot_reason = _boot(deadline)
    return {
        "reason": None,
        "disk": disk,
        "power": power,
        "uptime": {
            "process_s": process_s,
            "process_pid": process_pid,
            "process_reason": process_reason,
            "boot_s": boot_s,
            "boot_reason": boot_reason,
        },
        "operator": operator,
        "services": services,
        "rejected_count": rejected,
        "config_reason": config_reason,
    }


def _lead(key: tuple, root: Path, flight: _Flight) -> dict:
    entry: _Entry | None = None
    failed = False
    try:
        entry = _store(key, _inspect(root))
        return _expose(entry, fresh=True)
    except Exception:
        failed = True
        entry = None
        return _blank("inspection_failed")
    finally:
        _release(key, flight, entry, failed)


def _wait(flight: _Flight) -> dict:
    if not flight.event.wait(_WAIT_S):
        return _blank("inspection_timeout")
    if flight.failed or flight.entry is None:
        return _blank("inspection_failed")
    return _expose(flight.entry, fresh=False)


def snapshot(root: Path) -> dict:
    """Host display snapshot. Callers must not share the returned dict.

    ``measured_ns`` is the wall time of the measurement. ``cache_age_s`` is how
    long that measurement has been reused. A cache hit is still the same
    measurement; it is not a newer probe. Unknown values stay null.
    """
    if isinstance(root, str):
        root = Path(root)
    elif not isinstance(root, Path):
        return _blank("bad_root")
    try:
        resolved = root.expanduser().resolve(strict=False)
    except OSError:
        return _blank("bad_root")
    try:
        _observe_clocks()
        key = (str(resolved), _config_token(resolved))
        cached = _lookup(key)
        if cached is not None:
            return _expose(cached, fresh=False)
        claimed, role = _claim(key)
        if role == "cached" and isinstance(claimed, _Entry):
            return _expose(claimed, fresh=False)
        if role == "waiter" and isinstance(claimed, _Flight):
            return _wait(claimed)
        if role == "leader" and isinstance(claimed, _Flight):
            return _lead(key, resolved, claimed)
        return _blank("inspection_failed")
    except Exception:
        return _blank("inspection_failed")
