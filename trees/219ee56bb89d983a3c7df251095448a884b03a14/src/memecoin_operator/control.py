"""Durable operator journal, gates, and one-at-a-time job supervisor.

Scientific work stays in the parent adapter. This module records intent, enforces
local quotas, and refuses to republish a job that was in flight across a crash.
"""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA = "operator-status-v1"
COMMAND_SCHEMA = "operator-command-v1"
EVENTS_SCHEMA = "operator-events-v1"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
HASH_RE = re.compile(r"^[A-Fa-f0-9]{16,128}$")
POOL_RE = re.compile(r"^[A-Za-z0-9]{1,80}$")
MODES = {"idle", "collect", "paper", "auto", "paused", "stopped"}
PROFILES = {"balanced", "history"}
HEAVY = {"snapshot", "train", "paper", "collect"}
DEADLINES = {"snapshot": 600, "train": 900, "paper": 120, "collect": 60}
LEASE_TTL_S = 20
MAINTAIN_EVERY_S = 15
BODY_LIMIT = 65536
ENVELOPE_S = 7 * 24 * 3600
STOP_ATTEMPTS = 3
_DISPATCH_LOCKS: dict[str, threading.Lock] = {}
_DISPATCH_GUARD = threading.Lock()

ACTION_MAP = {
    "wait": "WAIT",
    "cash": "WAIT",
    "median_below_cost_buffer": "WAIT",
    "predicted_tail_risk": "WAIT",
    "stale_or_quarantined": "WAIT",
    "source_paused": "WAIT",
    "late_prediction_no_backfill": "WAIT",
    "position_limit": "WAIT",
    "cash_limit": "WAIT",
    "enter": "ENTER",
    "enter_next_observed_close": "ENTER",
    "hold": "HOLD",
    "already_exposed": "HOLD",
    "position_open": "HOLD",
    "exit": "EXIT",
    "time_exit": "EXIT",
    "fixed_time_exit": "EXIT",
    "fixed_15m_exit": "EXIT",
}

_LOCKS: dict[str, threading.Lock] = {}
_LOCK_GUARD = threading.Lock()
_CHILDREN: dict[tuple[str, str], subprocess.Popen[bytes]] = {}
_PARENT_FDS: list[int] = []
_OWNER_FDS: dict[str, int] = {}
_CLOCK: Callable[[], int] | None = None
_DROP_KEYS = {
    "artifact",
    "authorization",
    "api_key",
    "bundle",
    "champion_model_path",
    "credential",
    "exam_root",
    "file",
    "password",
    "path",
    "private_key",
    "root",
    "secret",
    "source_root",
    "token",
    "url",
}


class Rejected(Exception):
    def __init__(self, category: str, reason: str, http: int = 409) -> None:
        super().__init__(reason)
        self.category = category
        self.reason = reason
        self.http = http


def set_clock(clock: Callable[[], int] | None) -> None:
    """Test hook. None restores the wall clock. Deadlines and leases stay on wall time."""
    global _CLOCK
    _CLOCK = clock


def now_ns() -> int:
    if _CLOCK is not None:
        return int(_CLOCK())
    return time.time_ns()


def wall_ns() -> int:
    return time.time_ns()


def lock_for(root: Path) -> threading.Lock:
    key = str(root)
    with _LOCK_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = threading.Lock()
        return _LOCKS[key]


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def private_log(root: Path, message: str, exc: BaseException | None = None) -> None:
    try:
        directory = root / "operator" / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "operator.log"
        if path.exists() and path.stat().st_size > 1_000_000:
            tail = path.read_bytes()[-262144:]
            path.write_bytes(tail)
        text = message
        if exc is not None:
            text += "\n" + "".join(traceback.format_exception(exc))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text.rstrip() + "\n")
    except OSError:
        return


def load_adapter() -> Any:
    override = os.environ.get("MEMECOIN_OPERATOR_ADAPTER")
    if override:
        spec = importlib.util.spec_from_file_location("memecoin_operator_adapter_dyn", override)
        if spec is None or spec.loader is None:
            raise ImportError("adapter_unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return importlib.import_module("memecoin_operator.adapters")


def _connect(root: Path) -> sqlite3.Connection:
    db = sqlite3.connect(root / "operator.sqlite", timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _schema(db: sqlite3.Connection) -> None:
    # executescript() issues COMMIT first and would end the caller's BEGIN IMMEDIATE.
    for statement in (
        """CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS revisions (
          revision INTEGER PRIMARY KEY AUTOINCREMENT,
          mode TEXT NOT NULL,
          phase TEXT NOT NULL,
          collect_profile TEXT NOT NULL,
          selected_candidate_id TEXT,
          active_exam_id TEXT,
          resume_mode TEXT,
          auto_reason TEXT,
          cycle_json TEXT NOT NULL,
          reason TEXT NOT NULL,
          created_ns INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS commands (
          request_id TEXT PRIMARY KEY,
          fingerprint TEXT NOT NULL,
          action TEXT NOT NULL,
          params_json TEXT NOT NULL,
          http_status INTEGER NOT NULL,
          result_json TEXT NOT NULL,
          created_ns INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS jobs (
          job_id TEXT PRIMARY KEY,
          action TEXT NOT NULL,
          params_json TEXT NOT NULL,
          status TEXT NOT NULL,
          origin TEXT NOT NULL,
          result_json TEXT,
          error_reason TEXT,
          pid INTEGER,
          deadline_ns INTEGER,
          started_ns INTEGER,
          finished_ns INTEGER,
          reserved_ns INTEGER,
          dataset_id TEXT,
          snapshot_id TEXT,
          snapshot_hash TEXT,
          candidate_id TEXT,
          exam_id TEXT,
          matured_samples INTEGER,
          published INTEGER NOT NULL DEFAULT 0,
          created_ns INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS events (
          event_id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT NOT NULL,
          reason TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          created_ns INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS publications (
          job_id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          public_id TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          published_ns INTEGER NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status)",
        "CREATE INDEX IF NOT EXISTS events_kind ON events(kind, event_id)",
    ):
        db.execute(statement)


def _meta_get(db: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return default if row is None else str(row["value"])


def _meta_set(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def _cycle_default() -> dict[str, Any]:
    return {"expect": "none", "snapshot_id": None, "candidate_id": None}


def _state(db: sqlite3.Connection) -> dict[str, Any]:
    row = db.execute("SELECT * FROM revisions ORDER BY revision DESC LIMIT 1").fetchone()
    if row is None:
        raise Rejected("bad_request", "uninitialized", 400)
    cycle = json.loads(row["cycle_json"])
    return {
        "revision": int(row["revision"]),
        "mode": row["mode"],
        "phase": row["phase"],
        "collect_profile": row["collect_profile"],
        "selected_candidate_id": row["selected_candidate_id"],
        "active_exam_id": row["active_exam_id"],
        "resume_mode": row["resume_mode"],
        "auto_reason": row["auto_reason"],
        "cycle": cycle,
        "reason": row["reason"],
        "created_ns": int(row["created_ns"]),
    }


def _event(
    db: sqlite3.Connection, kind: str, reason: str, summary: dict[str, Any], now: int
) -> None:
    db.execute(
        "INSERT INTO events(kind, reason, payload_json, created_ns) VALUES(?,?,?,?)",
        (kind, reason, json.dumps(summary, sort_keys=True, allow_nan=False), now),
    )


def _push(db: sqlite3.Connection, state: dict[str, Any], reason: str, now: int) -> dict[str, Any]:
    db.execute(
        """INSERT INTO revisions(mode, phase, collect_profile, selected_candidate_id,
           active_exam_id, resume_mode, auto_reason, cycle_json, reason, created_ns)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            state["mode"],
            state["phase"],
            state["collect_profile"],
            state["selected_candidate_id"],
            state["active_exam_id"],
            state["resume_mode"],
            state["auto_reason"],
            json.dumps(state["cycle"], sort_keys=True),
            reason,
            now,
        ),
    )
    state["revision"] = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
    state["reason"] = reason
    state["created_ns"] = now
    _event(db, "audit", reason, {"mode": state["mode"], "phase": state["phase"]}, now)
    return state


def _same(left: dict[str, Any], right: dict[str, Any]) -> bool:
    keys = (
        "mode",
        "phase",
        "collect_profile",
        "selected_candidate_id",
        "active_exam_id",
        "resume_mode",
        "auto_reason",
    )
    if any(left[key] != right[key] for key in keys):
        return False
    return left["cycle"] == right["cycle"]


def _save(
    db: sqlite3.Connection, previous: dict[str, Any], new: dict[str, Any], reason: str, now: int
) -> dict[str, Any]:
    if _same(previous, new):
        return previous
    return _push(db, new, reason, now)


def _read_config(root: Path) -> tuple[dict[str, Any], str | None]:
    path = root / "config.json"
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        private_log(root, "config_unreadable", exc)
        return {}, "config_unreadable"
    if not isinstance(data, dict):
        return {}, "config_unreadable"
    return data, None


def _as_int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    if value < low or value > high:
        return default
    return value


def _default_bounds(expires: int | None) -> dict[str, Any]:
    return {
        "expires_ns": expires,
        "max_training_runs": 6,
        "min_refit_interval_s": 86400,
        "minimum_new_matured": 100,
        "envelope_s": ENVELOPE_S,
        "champion_sha": None,
        "virtual_blocked": False,
    }


def _expires_invalid(config: dict[str, Any]) -> bool:
    expires = config.get("expires_ns")
    return isinstance(expires, bool) or not isinstance(expires, int)


def bounds_of(config: dict[str, Any], broken: bool = False) -> dict[str, Any]:
    if broken or _expires_invalid(config):
        return _default_bounds(0)
    expires = config.get("expires_ns")
    if not isinstance(expires, int):
        expires = None
    bounds = _default_bounds(expires)
    bounds.update(
        {
            "max_training_runs": _as_int(config.get("max_training_runs"), 6, 1, 100),
            "min_refit_interval_s": _as_int(
                config.get("min_refit_interval_s"), 86400, 0, 366 * 86400
            ),
            "minimum_new_matured": _as_int(config.get("minimum_new_matured"), 100, 0, 10**12),
            "envelope_s": _as_int(config.get("run_envelope_s"), ENVELOPE_S, 60, 366 * 86400),
            "champion_sha": _safe_hash(config.get("champion_model_sha")),
        }
    )
    return bounds


def _virtual_blocked(config: dict[str, Any]) -> bool:
    if config.get("real_execution_enabled") is True:
        return True
    paid = config.get("paid_budget_usd", 0)
    return not isinstance(paid, bool) and isinstance(paid, (int, float)) and paid != 0


def _deployment_ok(root: Path) -> bool:
    if not (root / "deployment.json").exists():
        return True
    try:
        from memecoin_operator.deployment import guard

        guard(root)
    except Exception as exc:
        private_log(root, "deployment_rejected", exc)
        return False
    return True


def _bounds(root: Path) -> tuple[dict[str, Any], str | None]:
    config, err = _read_config(root)
    bounds = bounds_of(config, broken=err is not None)
    if not _deployment_ok(root):
        bounds = dict(bounds)
        bounds["expires_ns"] = 0
    bounds["virtual_blocked"] = _virtual_blocked(config)
    if bounds["virtual_blocked"]:
        bounds["expires_ns"] = 0
    return bounds, err


def _claim(root: Path) -> bool:
    key = str(root.resolve())
    if key in _OWNER_FDS:
        return True
    folder = root / "operator"
    if folder.is_symlink():
        return False
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder / "owner.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    _OWNER_FDS[key] = fd
    return True


def _safe_hash(value: Any) -> str | None:
    if isinstance(value, str) and HASH_RE.fullmatch(value):
        return value.lower()
    return None


def _safe_id(value: Any) -> str | None:
    if isinstance(value, str) and ID_RE.fullmatch(value):
        return value
    return None


def _safe_reason(value: Any, default: str = "adapter_error") -> str:
    if isinstance(value, str) and REASON_RE.fullmatch(value):
        return value
    return default


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    # Nanosecond clocks already exceed 1e18; signed int64 still covers them.
    if abs(value) > 2**63 - 1:
        return None
    return value


def _safe_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _money(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if _safe_float(value) is None:
            return None
        return format(value, ".8f")
    if isinstance(value, str) and re.fullmatch(r"-?\d{1,20}(\.\d{1,8})?", value):
        return value
    return None


def map_action(value: Any) -> str | None:
    """Map a fixed-time engine label. Unknown and MPC/learned labels stay unclassified."""
    if not isinstance(value, str):
        return None
    if value in {"WAIT", "ENTER", "HOLD", "EXIT"}:
        return value
    key = value.strip().lower()
    if "mpc" in key or "learned" in key:
        return None
    return ACTION_MAP.get(key)


def assess_result(
    action: str, params: dict[str, Any], result: Any
) -> tuple[bool, str, dict[str, Any]]:
    if not isinstance(result, dict):
        return False, "invalid_result", {}
    if "champion" in result or ("promoted" in result and result.get("promoted") is not False):
        return False, "invalid_result", {}
    if "ok" in result and result.get("ok") is not True:
        reason = result.get("reason")
        if result.get("ok") is False:
            return False, _safe_reason(reason), {}
        return False, "invalid_result", {}
    if action == "snapshot":
        dataset = _safe_id(result.get("dataset_id"))
        if dataset is None:
            return False, "partial_result", {}
        return (
            True,
            "ok",
            {
                "dataset_id": dataset,
                "snapshot_hash": _safe_hash(
                    result.get("snapshot_hash") or result.get("dataset_sha")
                ),
                "matured_samples": _safe_int(result.get("matured_samples")),
            },
        )
    if action == "train":
        candidate = _safe_id(result.get("candidate_id"))
        if candidate is None:
            return False, "partial_result", {}
        snap = _safe_id(result.get("snapshot_id")) or params.get("snapshot_id")
        body = {
            "candidate_id": candidate,
            "snapshot_id": snap,
            "snapshot_hash": _safe_hash(result.get("snapshot_hash") or result.get("dataset_sha")),
        }
        model_sha = _safe_hash(result.get("model_sha"))
        if model_sha:
            body["model_sha"] = model_sha
        return True, "ok", body
    if action == "paper":
        exam = _safe_id(result.get("exam_id"))
        if exam is None:
            return False, "partial_result", {}
        return (
            True,
            "ok",
            {
                "exam_id": exam,
                "candidate_id": _safe_id(result.get("candidate_id")) or params.get("candidate_id"),
                "expires_ns": _safe_int(result.get("expires_ns")),
            },
        )
    if action == "collect":
        profile = result.get("profile", params.get("profile"))
        if profile not in PROFILES:
            return False, "partial_result", {}
        return True, "ok", {"profile": profile}
    return False, "invalid_result", {}


def artifact_dir(root: Path, job_id: str) -> Path:
    if _safe_id(job_id) is None:
        raise Rejected("bad_request", "bad_id", 400)
    return root / "operator" / "jobs" / job_id


def lease_path(root: Path, job_id: str) -> Path:
    return root / "operator" / "leases" / f"{job_id}.json"


def _fingerprint(action: str, params: dict[str, Any]) -> str:
    blob = json.dumps(
        {"action": action, "params": params},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def _canonical(action: str, params: Any) -> dict[str, Any]:
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise Rejected("bad_request", "bad_params", 400)
    allowed = {
        "collect": {"profile"},
        "snapshot": set(),
        "train": {"snapshot_id"},
        "paper": {"candidate_id"},
        "auto": {"profile"},
        "pause": set(),
        "resume": set(),
        "stop": {"confirm"},
        "stop_all": {"confirm"},
    }
    if action not in allowed:
        raise Rejected("bad_request", "unknown_action", 400)
    if set(params) - allowed[action]:
        raise Rejected("bad_request", "bad_params", 400)
    out: dict[str, Any] = {}
    if "profile" in allowed[action] and "profile" in params:
        if params["profile"] not in PROFILES:
            raise Rejected("bad_request", "bad_params", 400)
        out["profile"] = params["profile"]
    elif action == "collect":
        raise Rejected("bad_request", "bad_params", 400)
    if "snapshot_id" in allowed[action] and action == "train":
        snap = _safe_id(params.get("snapshot_id"))
        if snap is None:
            raise Rejected("bad_request", "bad_id", 400)
        out["snapshot_id"] = snap
    if "candidate_id" in allowed[action] and action == "paper":
        cand = _safe_id(params.get("candidate_id"))
        if cand is None:
            raise Rejected("bad_request", "bad_id", 400)
        out["candidate_id"] = cand
    if action in {"stop", "stop_all"}:
        if params.get("confirm") is not True:
            raise Rejected("bad_request", "not_confirmed", 400)
        out["confirm"] = True
    return out


def _load_view(root: Path) -> dict[str, Any]:
    empty: dict[str, Any] = {
        "ok": False,
        "warning": "adapter_unavailable",
        "source": {},
        "datasets": [],
        "candidates": [],
        "exams": [],
        "paper": {},
        "coverage": {},
        "legacy": [],
        "limits": {},
        "policy": {},
        "training_progress": [],
        "progress": None,
    }
    try:
        adapter = load_adapter()
        raw = adapter.inspect(root)
    except Exception as exc:
        private_log(root, "inspect_failed", exc)
        return empty
    if not isinstance(raw, dict):
        return empty
    empty["ok"] = True
    empty["warning"] = None
    empty["source"] = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    empty["paper"] = raw.get("paper") if isinstance(raw.get("paper"), dict) else {}
    empty["coverage"] = raw.get("coverage") if isinstance(raw.get("coverage"), dict) else {}
    empty["limits"] = raw.get("limits") if isinstance(raw.get("limits"), dict) else {}
    empty["policy"] = raw.get("policy") if isinstance(raw.get("policy"), dict) else {}
    progress = raw.get("training_progress")
    if isinstance(progress, dict):
        progress = [progress]
    empty["training_progress"] = progress if isinstance(progress, list) else []
    for key in ("datasets", "candidates", "exams", "legacy"):
        value = raw.get(key)
        empty[key] = value if isinstance(value, list) else []
    incidents = raw.get("incidents")
    empty["incidents"] = incidents if isinstance(incidents, list) else []
    recovery = raw.get("recovery")
    empty["recovery"] = recovery if isinstance(recovery, dict) else None
    if "synthetic" in raw:
        empty["synthetic"] = raw.get("synthetic")
    if "data_kind" in raw:
        empty["data_kind"] = raw.get("data_kind")
    return empty


def _jobs(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(db.execute("SELECT * FROM jobs ORDER BY created_ns ASC, job_id ASC"))


def _active(rows: list[sqlite3.Row]) -> sqlite3.Row | None:
    for row in rows:
        if row["status"] in {"queued", "running"}:
            return row
    return None


def _train_rows(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    return [row for row in rows if row["action"] == "train"]


def _known_snapshots(db: sqlite3.Connection, view: dict[str, Any]) -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for row in db.execute("SELECT * FROM publications WHERE kind='dataset'"):
        payload = json.loads(row["payload_json"])
        found[row["public_id"]] = payload.get("snapshot_hash")
    for item in view["datasets"]:
        if not isinstance(item, dict):
            continue
        dataset = _safe_id(item.get("dataset_id"))
        if dataset:
            found[dataset] = _safe_hash(
                item.get("snapshot_hash") or item.get("dataset_sha")
            ) or found.get(dataset)
    return found


_CANDIDATE_STATUS = {"completed", "ready", "published", "failed", "incomplete", "unknown"}
_SELECTABLE_STATUS = {"completed", "ready", "published"}


def _candidate_status(item: dict[str, Any]) -> str:
    raw = item.get("status")
    status = raw if isinstance(raw, str) and raw in _CANDIDATE_STATUS else "unknown"
    sha = _safe_hash(item.get("model_sha"))
    if status in {"ready", "published"} and sha is None:
        return "incomplete"
    if status == "completed" and "model_sha" in item and sha is None:
        return "incomplete"
    return status


def _inspect_selectable(item: dict[str, Any]) -> bool:
    status = _candidate_status(item)
    if status not in _SELECTABLE_STATUS:
        return False
    if "model_sha" in item or status in {"ready", "published"}:
        return _safe_hash(item.get("model_sha")) is not None
    return True


def _known_candidates(db: sqlite3.Connection, view: dict[str, Any]) -> set[str]:
    found = {
        row["public_id"]
        for row in db.execute("SELECT public_id FROM publications WHERE kind='candidate'")
    }
    for item in view.get("candidates") or []:
        if not isinstance(item, dict) or not _inspect_selectable(item):
            continue
        cand = _safe_id(item.get("candidate_id"))
        if cand:
            found.add(cand)
    return found


def _matured_now(view: dict[str, Any]) -> int | None:
    source = view.get("source") if isinstance(view.get("source"), dict) else {}
    coverage = view.get("coverage") if isinstance(view.get("coverage"), dict) else {}
    value = source.get("matured_samples")
    if value is None:
        value = coverage.get("matured_samples")
    return _safe_int(value)


def _exam_is_open(item: dict[str, Any], now: int) -> bool:
    status = item.get("status")
    if status in {"expired", "stopped", "closed", "failed"}:
        return False
    expires = _safe_int(item.get("expires_ns"))
    if expires is not None and now >= expires:
        return False
    return status in {None, "running", "open", "active"}


def _open_exams(db: sqlite3.Connection, view: dict[str, Any], now: int | None = None) -> int:
    del db
    moment = now_ns() if now is None else now
    seen: set[str] = set()
    count = 0
    for item in view["exams"]:
        if not isinstance(item, dict):
            continue
        exam = _safe_id(item.get("exam_id"))
        if not exam or exam in seen:
            continue
        seen.add(exam)
        if _exam_is_open(item, moment):
            count += 1
    return count


def _last_snapshot_matured(db: sqlite3.Connection) -> tuple[int | None, int | None]:
    row = db.execute(
        """SELECT payload_json, published_ns FROM publications
           WHERE kind='dataset' ORDER BY published_ns DESC LIMIT 1"""
    ).fetchone()
    if row is None:
        return None, None
    payload = json.loads(row["payload_json"])
    return payload.get("matured_samples"), int(row["published_ns"])


def evaluate(
    state: dict[str, Any],
    bounds: dict[str, Any],
    rows: list[sqlite3.Row],
    view: dict[str, Any],
    now: int,
) -> dict[str, Any]:
    trains = _train_rows(rows)
    window_start = now - bounds["envelope_s"] * 1_000_000_000
    used = [
        row
        for row in trains
        if row["reserved_ns"] is not None and int(row["reserved_ns"]) >= window_start
    ]
    last_res = max((int(row["reserved_ns"]) for row in trains if row["reserved_ns"]), default=None)
    initial = len(trains) == 0
    interval_ok = (
        initial
        or last_res is None
        or now - last_res >= bounds["min_refit_interval_s"] * 1_000_000_000
    )
    quota_ok = len(used) < bounds["max_training_runs"]
    matured_now = _matured_now(view)
    matured_prior, _published = _last_snapshot_matured_from(rows, state)
    # prior successful snapshot is an operator publication; caller passes it via state cache
    delta = None if matured_now is None or matured_prior is None else matured_now - matured_prior
    growth_ok = matured_prior is None or (
        matured_now is not None and delta is not None and delta >= bounds["minimum_new_matured"]
    )
    expired = bounds["expires_ns"] is not None and now >= bounds["expires_ns"]
    domain = bool(view["limits"].get("domain_stop")) if isinstance(view["limits"], dict) else False
    eligible_flag = view["limits"].get("eligible") if isinstance(view["limits"], dict) else None
    science_ok = eligible_flag is not False and view.get("ok") is not False
    busy = _active(rows) is not None
    mode = state["mode"]
    blocked_mode = mode in {"paused", "stopped"}
    not_before = (
        None
        if last_res is None or initial
        else last_res + bounds["min_refit_interval_s"] * 1_000_000_000
    )

    def pack(reasons: list[str]) -> dict[str, Any]:
        unique: list[str] = []
        for reason in reasons:
            if reason not in unique:
                unique.append(reason)
        return {"eligible": not unique, "reasons": unique}

    def common(extra: list[str]) -> list[str]:
        reasons: list[str] = []
        if expired:
            reasons.append("expired")
        if mode == "paused":
            reasons.append("paused")
        if mode == "stopped":
            reasons.append("stopped")
        if busy:
            reasons.append("busy")
        reasons.extend(extra)
        return reasons

    snapshot_reasons = common([])
    if domain:
        snapshot_reasons.append("domain_stop")
    manual_snapshot = pack(snapshot_reasons)
    collect_reasons = common([])
    if domain:
        collect_reasons.append("domain_stop")
    manual_collect = pack(collect_reasons)
    train_reasons = common([])
    if domain:
        train_reasons.append("domain_stop")
    if not science_ok:
        train_reasons.append("ineligible")
    if not quota_ok:
        train_reasons.append("quota")
    if not interval_ok:
        train_reasons.append("interval")
    manual_train = pack(train_reasons)
    paper_reasons = common([])
    if domain:
        paper_reasons.append("domain_stop")
    if not science_ok:
        paper_reasons.append("ineligible")
    manual_paper = pack(paper_reasons)

    auto_reasons: list[str] = []
    next_action = "none"
    if mode != "auto":
        auto_reasons.append("mode_idle" if mode == "idle" else mode)
        next_action = "none"
    elif expired:
        auto_reasons.append("expired")
    elif domain:
        auto_reasons.append("domain_stop")
        next_action = "none"
    elif busy:
        auto_reasons.append("busy")
        next_action = state["phase"] if state["phase"] in HEAVY else "none"
    else:
        expect = state["cycle"].get("expect", "none")
        if expect == "collect" or _meta_flag_from_state(state) is False:
            next_action = "collect"
        elif expect == "train":
            next_action = "train"
            if not science_ok:
                auto_reasons.append("ineligible")
            if not interval_ok:
                auto_reasons.append("interval")
            if not quota_ok:
                auto_reasons.append("quota")
        elif expect == "paper":
            next_action = "paper"
        else:
            next_action = "snapshot"
            if not science_ok:
                auto_reasons.append("ineligible")
            if not interval_ok:
                auto_reasons.append("interval")
            if matured_prior is not None and matured_now is None:
                auto_reasons.append("unknown_growth")
            elif not growth_ok:
                auto_reasons.append("insufficient_growth")
            if not quota_ok:
                auto_reasons.append("quota")
    checks = [
        {"id": "expiry", "pass": not expired, "reason": "expired" if expired else None},
        {"id": "operator_mode", "pass": not blocked_mode, "reason": mode if blocked_mode else None},
        {"id": "domain_stop", "pass": not domain, "reason": "domain_stop" if domain else None},
        {"id": "busy", "pass": not busy, "reason": "busy" if busy else None},
        {"id": "eligible", "pass": science_ok, "reason": None if science_ok else "ineligible"},
        {"id": "growth", "pass": growth_ok, "reason": None if growth_ok else "insufficient_growth"},
        {"id": "interval", "pass": interval_ok, "reason": None if interval_ok else "interval"},
        {"id": "quota", "pass": quota_ok, "reason": None if quota_ok else "quota"},
        {
            "id": "initial_collect",
            "pass": bool(state.get("initial_collect_done")),
            "reason": None if state.get("initial_collect_done") else "collect_first",
        },
    ]
    return {
        "auto": {
            "next_action": next_action,
            "eligible": not auto_reasons and mode == "auto",
            "reasons": auto_reasons,
        },
        "manual": {
            "snapshot": manual_snapshot,
            "train": manual_train,
            "paper": manual_paper,
            "collect": manual_collect,
        },
        "checks": checks,
        "minimum_new_matured": bounds["minimum_new_matured"],
        "runs_used": len(used),
        "runs_max": bounds["max_training_runs"],
        "envelope_s": bounds["envelope_s"],
        "min_refit_interval_s": bounds["min_refit_interval_s"],
        "last_reservation_ns": last_res,
        "matured_now": matured_now,
        "matured_prior": matured_prior,
        "matured_delta": delta,
        "initial_run": initial,
        "initial_collect_done": bool(state.get("initial_collect_done")),
        "busy": busy,
        "not_before_ns": not_before,
        "expired": expired,
        "domain_stop": domain,
        "science_ok": science_ok,
        "quota_ok": quota_ok,
        "interval_ok": interval_ok,
        "growth_ok": growth_ok,
    }


def _last_snapshot_matured_from(
    rows: list[sqlite3.Row], state: dict[str, Any]
) -> tuple[int | None, int | None]:
    value = state.get("matured_prior")
    return (value if isinstance(value, int) else None), None


def _meta_flag_from_state(state: dict[str, Any]) -> bool:
    return bool(state.get("initial_collect_done"))


def _attach_db_facts(db: sqlite3.Connection, state: dict[str, Any]) -> dict[str, Any]:
    state = dict(state)
    state["initial_collect_done"] = _meta_get(db, "initial_collect_done", "0") == "1"
    matured, _ns = _last_snapshot_matured(db)
    state["matured_prior"] = matured
    return state


def _body(
    state: dict[str, Any],
    *,
    ok: bool,
    category: str,
    reason: str,
    request_id: str,
    duplicate: bool,
    job_id: str | None,
    http: int,
) -> dict[str, Any]:
    return {
        "schema": COMMAND_SCHEMA,
        "ok": ok,
        "category": category,
        "reason": reason,
        "request_id": request_id,
        "duplicate": duplicate,
        "revision": state["revision"],
        "mode": state["mode"],
        "phase": state["phase"],
        "job_id": job_id,
        "http_status": http,
    }


def _new_job_id(db: sqlite3.Connection) -> str:
    for _ in range(5):
        job_id = "job_" + os.urandom(8).hex()
        if db.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone() is None:
            return job_id
    raise Rejected("internal", "internal", 500)


def _enqueue(
    db: sqlite3.Connection,
    *,
    action: str,
    params: dict[str, Any],
    origin: str,
    now: int,
    snapshot_hash: str | None = None,
    matured: int | None = None,
) -> str:
    if _active(_jobs(db)) is not None:
        raise Rejected("busy", "busy")
    job_id = _new_job_id(db)
    reserved = now if action == "train" else None
    db.execute(
        """INSERT INTO jobs(job_id, action, params_json, status, origin, error_reason, pid,
           deadline_ns, started_ns, finished_ns, reserved_ns, dataset_id, snapshot_id,
           snapshot_hash, candidate_id, exam_id, matured_samples, published, created_ns)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            job_id,
            action,
            json.dumps(params, sort_keys=True),
            "queued",
            origin,
            None,
            None,
            None,
            None,
            None,
            reserved,
            None,
            params.get("snapshot_id"),
            snapshot_hash,
            params.get("candidate_id"),
            None,
            matured,
            0,
            now,
        ),
    )
    _event(db, "audit", "queued", {"job_id": job_id, "action": action, "origin": origin}, now)
    return job_id


def _used_snapshot(rows: list[sqlite3.Row], snapshot_id: str, snapshot_hash: str | None) -> bool:
    for row in _train_rows(rows):
        if row["snapshot_id"] == snapshot_id:
            return True
        if snapshot_hash and row["snapshot_hash"] and row["snapshot_hash"] == snapshot_hash:
            return True
    return False


def _require_operator(state: dict[str, Any], gates: dict[str, Any]) -> None:
    if gates["expired"]:
        raise Rejected("expired", "expired")
    if state["mode"] == "paused":
        raise Rejected("paused", "paused")
    if state["mode"] == "stopped":
        raise Rejected("stopped", "stopped")
    if gates["busy"]:
        raise Rejected("busy", "busy")


def _apply(
    db: sqlite3.Connection,
    root: Path,
    state: dict[str, Any],
    action: str,
    params: dict[str, Any],
    view: dict[str, Any],
    bounds: dict[str, Any],
    now: int,
    request_id: str = "",
) -> tuple[dict[str, Any], str | None, str]:
    state = _attach_db_facts(db, state)
    rows = _jobs(db)
    gates = evaluate(state, bounds, rows, view, now)
    job_id: str | None = None
    reason = "accepted"
    new = dict(state)
    new["cycle"] = dict(state["cycle"])

    if action == "pause":
        if state["mode"] == "paused":
            return state, None, "already_paused"
        if state["mode"] == "stopped":
            raise Rejected("stopped", "stopped")
        new["resume_mode"] = state["mode"]
        new["mode"] = "paused"
        new["phase"] = "paused"
        state = _save(db, state, new, "paused", now)
        return state, None, "paused"

    if action == "resume":
        effect = _read_effect(db)
        if _stop_open(effect):
            reason = "stop_failed" if effect["phase"] == "failed" else "stop_pending"
            raise Rejected("conflict", reason, 409)
        if state["mode"] not in {"paused", "stopped"}:
            return state, None, "already_running"
        target = (
            state["resume_mode"]
            if state["resume_mode"] in {"idle", "collect", "paper", "auto"}
            else "idle"
        )
        new["mode"] = target
        new["phase"] = "waiting" if target == "auto" else target
        new["auto_reason"] = None
        if target == "auto" and state["cycle"].get("expect") in {None, "none"}:
            expect = "collect" if not state.get("initial_collect_done") else "snapshot"
            new["cycle"] = {"expect": expect, "snapshot_id": None, "candidate_id": None}
        state = _save(db, state, new, "resumed", now)
        return state, None, "resumed"

    if action in {"stop", "stop_all"}:
        full = action == "stop_all"
        kind = "full" if full else "own"
        current = _read_effect(db)
        if (
            state["mode"] == "stopped"
            and current["confirmed"]
            and current["phase"] == "applied"
            and current["kind"] == kind
        ):
            return state, None, "already_stopped"
        _halt_jobs(db, root, now, "operator_stop")
        new["resume_mode"] = (
            state["mode"] if state["mode"] not in {"paused", "stopped"} else state["resume_mode"]
        )
        new["mode"] = "stopped"
        new["phase"] = "stopped"
        new["cycle"] = _cycle_default()
        new["auto_reason"] = "stop_all" if full else "stopped"
        state = _save(db, state, new, "stop_all" if full else "stopped", now)
        _meta_set(db, "last_stop_full", "1" if full else "0")
        _schedule_stop(db, kind, request_id, state["revision"], now)
        return state, None, "stop_all" if full else "stopped"

    _require_operator(state, gates)
    if view.get("ok") is False:
        raise Rejected("ineligible", "adapter_unavailable")
    if bounds.get("virtual_blocked"):
        raise Rejected("forbidden", "rejected", 403)
    if action == "collect":
        if gates["domain_stop"]:
            raise Rejected("ineligible", "domain_stop")
        profile = params["profile"]
        job_id = _enqueue(
            db, action="collect", params={"profile": profile}, origin="manual", now=now
        )
        new["collect_profile"] = profile
        new["mode"] = "collect" if state["mode"] in {"idle", "collect"} else state["mode"]
        new["phase"] = "collect"
        state = _save(db, state, new, "accepted", now)
        return state, job_id, reason

    if action == "snapshot":
        if gates["domain_stop"]:
            raise Rejected("ineligible", "domain_stop")
        job_id = _enqueue(db, action="snapshot", params={}, origin="manual", now=now)
        new["phase"] = "snapshot"
        if state["mode"] == "idle":
            new["mode"] = "idle"
        state = _save(db, state, new, "accepted", now)
        return state, job_id, reason

    if action == "train":
        if gates["domain_stop"]:
            raise Rejected("ineligible", "domain_stop")
        if not gates["science_ok"]:
            raise Rejected("ineligible", "ineligible")
        if not gates["quota_ok"]:
            raise Rejected("quota", "quota")
        if not gates["interval_ok"]:
            raise Rejected("ineligible", "interval")
        known = _known_snapshots(db, view)
        snap = params["snapshot_id"]
        if snap not in known:
            raise Rejected("ineligible", "missing_snapshot")
        if _used_snapshot(rows, snap, known.get(snap)):
            raise Rejected("ineligible", "same_snapshot")
        job_id = _enqueue(
            db,
            action="train",
            params={"snapshot_id": snap},
            origin="manual",
            now=now,
            snapshot_hash=known.get(snap),
        )
        new["phase"] = "train"
        state = _save(db, state, new, "accepted", now)
        return state, job_id, reason

    if action == "paper":
        if gates["domain_stop"]:
            raise Rejected("ineligible", "domain_stop")
        if not gates["science_ok"]:
            raise Rejected("ineligible", "ineligible")
        cand = params["candidate_id"]
        if cand not in _known_candidates(db, view):
            raise Rejected("ineligible", "missing_candidate")
        limit = (
            _safe_int(view["limits"].get("max_open_exams"))
            if isinstance(view["limits"], dict)
            else None
        )
        if limit is not None and _open_exams(db, view, now) >= limit:
            raise Rejected("ineligible", "exam_capacity")
        job_id = _enqueue(
            db, action="paper", params={"candidate_id": cand}, origin="manual", now=now
        )
        new["selected_candidate_id"] = cand
        if state["mode"] in {"idle", "collect", "paper"}:
            new["mode"] = "paper"
        new["phase"] = "paper"
        state = _save(db, state, new, "accepted", now)
        return state, job_id, reason

    if action == "auto":
        if gates["domain_stop"]:
            raise Rejected("ineligible", "domain_stop")
        if state["mode"] == "auto":
            return state, None, "already_auto"
        profile = params.get("profile", state["collect_profile"])
        new["mode"] = "auto"
        new["collect_profile"] = profile
        new["auto_reason"] = None
        new["resume_mode"] = "auto"
        if not state.get("initial_collect_done"):
            new["cycle"] = {"expect": "collect", "snapshot_id": None, "candidate_id": None}
            new["phase"] = "collect"
        else:
            new["cycle"] = {"expect": "snapshot", "snapshot_id": None, "candidate_id": None}
            new["phase"] = "waiting"
        state = _save(db, state, new, "auto", now)
        return state, None, "accepted"
    raise Rejected("bad_request", "unknown_action", 400)


def _halt_jobs(db: sqlite3.Connection, root: Path, now: int, why: str) -> None:
    for row in _jobs(db):
        if row["status"] == "queued":
            db.execute(
                "UPDATE jobs SET status='interrupted', error_reason='stopped', "
                "finished_ns=? WHERE job_id=?",
                (now, row["job_id"]),
            )
        elif row["status"] == "running":
            _mark_stop(root, row["job_id"], why)
            if row["pid"]:
                _signal_owned(int(row["pid"]))
            db.execute(
                """UPDATE jobs SET status='interrupted', error_reason='interrupted', finished_ns=?,
                   published=0 WHERE job_id=?""",
                (now, row["job_id"]),
            )
            _event(db, "incident", "interrupted", {"job_id": row["job_id"]}, now)


def _mark_stop(root: Path, job_id: str, why: str) -> None:
    path = artifact_dir(root, job_id) / "operator-stop-reason"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        atomic_write(path, why.encode())


def terminate(pid: int) -> None:
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
    for _ in range(30):
        if not alive(pid):
            break
        try:
            waited, _status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if waited == pid:
            return
        time.sleep(0.01)
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return


def alive(pid: int) -> bool:
    for proc in _CHILDREN.values():
        if proc.pid == pid:
            return proc.poll() is None
    try:
        waited, _status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        waited = 0
    else:
        if waited == pid:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def command(
    root: Path, request_id: str, action: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    root = Path(root)
    if _safe_id(request_id) is None:
        return _bare(
            False, "bad_request", "bad_id", request_id if isinstance(request_id, str) else "", 400
        )
    try:
        canonical = _canonical(action, params)
    except Rejected as exc:
        return _bare(False, exc.category, exc.reason, request_id, exc.http)
    if not _claim(root):
        return _bare(False, "conflict", "owned", request_id, 409)
    fingerprint = _fingerprint(action, canonical)
    view = (
        _load_view(root)
        if action in HEAVY or action in {"auto", "stop", "stop_all", "resume"}
        else None
    )
    db = _connect(root)
    body: dict[str, Any] | None = None
    try:
        lock_for(root).acquire()
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                body = _command_tx(
                    db, root, request_id, action, canonical, fingerprint, view, now_ns()
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        finally:
            lock_for(root).release()
    except Rejected as exc:
        body = _bare(False, exc.category, exc.reason, request_id, exc.http)
    except Exception as exc:
        private_log(root, "command_failed", exc)
        body = _bare(False, "internal", "internal", request_id, 500)
    finally:
        db.close()
    if body and body.get("ok") and action in {"stop", "stop_all"}:
        try:
            _dispatch_stop(root)
        except Exception as exc:
            private_log(root, "stop_dispatch_failed", exc)
    return body if body is not None else _bare(False, "internal", "internal", request_id, 500)


def _command_tx(
    db: sqlite3.Connection,
    root: Path,
    request_id: str,
    action: str,
    canonical: dict[str, Any],
    fingerprint: str,
    view: dict[str, Any] | None,
    now: int,
) -> dict[str, Any]:
    _schema(db)
    if db.execute("SELECT 1 FROM revisions LIMIT 1").fetchone() is None:
        raise Rejected("bad_request", "uninitialized", 400)
    existing = db.execute("SELECT * FROM commands WHERE request_id=?", (request_id,)).fetchone()
    if existing is not None:
        if existing["fingerprint"] != fingerprint:
            raise Rejected("conflict", "conflict")
        stored = json.loads(existing["result_json"])
        stored["duplicate"] = True
        stored["http_status"] = int(existing["http_status"])
        return stored
    state = _state(db)
    bounds, _err = _bounds(root)
    if view is None:
        view = {
            "source": {},
            "datasets": [],
            "candidates": [],
            "exams": [],
            "paper": {},
            "coverage": {},
            "legacy": [],
            "limits": {},
            "policy": {},
        }
    try:
        state, job_id, reason = _apply(
            db, root, state, action, canonical, view, bounds, now, request_id
        )
    except Rejected as exc:
        state = _try_state(db)
        body = _body(
            state,
            ok=False,
            category=exc.category,
            reason=exc.reason,
            request_id=request_id,
            duplicate=False,
            job_id=None,
            http=exc.http,
        )
        if exc.reason != "conflict":
            db.execute(
                """INSERT INTO commands(request_id, fingerprint, action, params_json,
                   http_status, result_json, created_ns) VALUES(?,?,?,?,?,?,?)""",
                (
                    request_id,
                    fingerprint,
                    action,
                    json.dumps(canonical, sort_keys=True),
                    exc.http,
                    json.dumps(body, sort_keys=True),
                    now,
                ),
            )
        return body
    body = _body(
        state,
        ok=True,
        category="ok",
        reason=reason,
        request_id=request_id,
        duplicate=False,
        job_id=job_id,
        http=200,
    )
    db.execute(
        """INSERT INTO commands(request_id, fingerprint, action, params_json, http_status,
           result_json, created_ns) VALUES(?,?,?,?,?,?,?)""",
        (
            request_id,
            fingerprint,
            action,
            json.dumps(canonical, sort_keys=True),
            200,
            json.dumps(body, sort_keys=True),
            now,
        ),
    )
    return body


def _dispatch_lock(root: Path) -> threading.Lock:
    key = str(Path(root).resolve())
    with _DISPATCH_GUARD:
        lock = _DISPATCH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _DISPATCH_LOCKS[key] = lock
        return lock


def _effect_blank() -> dict[str, Any]:
    return {
        "token": 0,
        "kind": None,
        "request_id": None,
        "phase": "none",
        "attempts": 0,
        "max_attempts": STOP_ATTEMPTS,
        "reason": None,
        "requested_ns": None,
        "updated_ns": None,
        "attempt_ns": [],
        "confirmed": False,
        "state_revision": 0,
    }


def _plain_int(value: Any, *, minimum: int | None = None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if minimum is not None and value < minimum:
        return None
    return value


def _effect_unconfirmed(data: dict[str, Any] | None = None) -> dict[str, Any]:
    effect = _effect_blank()
    effect["phase"] = "failed"
    effect["confirmed"] = False
    effect["reason"] = "stop_effect_invalid"
    if not isinstance(data, dict):
        return effect
    effect["attempts"] = _plain_int(data.get("attempts"), minimum=0) or 0
    effect["token"] = _plain_int(data.get("token"), minimum=0) or 0
    effect["requested_ns"] = _plain_int(data.get("requested_ns"))
    effect["updated_ns"] = _plain_int(data.get("updated_ns"))
    effect["state_revision"] = _plain_int(data.get("state_revision"), minimum=0) or 0
    request_id = data.get("request_id")
    if isinstance(request_id, str) and ID_RE.fullmatch(request_id):
        effect["request_id"] = request_id
    stamps = data.get("attempt_ns")
    if isinstance(stamps, list):
        effect["attempt_ns"] = [item for item in stamps if _plain_int(item) is not None][:6]
    return effect


def _read_effect(db: sqlite3.Connection) -> dict[str, Any]:
    raw = _meta_get(db, "stop_effect")
    if not raw:
        return _effect_blank()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _effect_unconfirmed()
    if not isinstance(data, dict):
        return _effect_unconfirmed()
    phase = data.get("phase")
    kind = data.get("kind")
    phase_ok = phase in {"none", "pending", "retrying", "applied", "failed"}
    kind_ok = kind in {"own", "full"} or (kind is None and phase == "none")
    if not phase_ok or not kind_ok:
        return _effect_unconfirmed(data)
    effect = _effect_blank()
    effect["phase"] = phase
    effect["kind"] = kind if kind in {"own", "full"} else None
    effect["token"] = _plain_int(data.get("token"), minimum=0) or 0
    effect["attempts"] = _plain_int(data.get("attempts"), minimum=0) or 0
    request_id = data.get("request_id")
    effect["request_id"] = request_id if isinstance(request_id, str) else None
    reason = data.get("reason")
    effect["reason"] = reason if isinstance(reason, str) else None
    effect["requested_ns"] = _plain_int(data.get("requested_ns"))
    effect["updated_ns"] = _plain_int(data.get("updated_ns"))
    stamps = data.get("attempt_ns")
    effect["attempt_ns"] = (
        [item for item in stamps if _plain_int(item) is not None]
        if isinstance(stamps, list)
        else []
    )
    effect["state_revision"] = _plain_int(data.get("state_revision"), minimum=0) or 0
    effect["confirmed"] = data.get("confirmed") is True and effect["phase"] == "applied"
    return effect


def _write_effect(db: sqlite3.Connection, effect: dict[str, Any], now: int) -> None:
    effect["updated_ns"] = now
    effect["max_attempts"] = STOP_ATTEMPTS
    _meta_set(db, "stop_effect", json.dumps(effect, sort_keys=True))
    if effect.get("confirmed") is True:
        _meta_set(db, "pending_stop", "")
    elif effect.get("kind") in {"own", "full"}:
        _meta_set(db, "pending_stop", str(effect["kind"]))


def _stop_open(effect: dict[str, Any]) -> bool:
    return effect.get("phase") in {"pending", "retrying", "failed"}


def _public_effect(effect: dict[str, Any]) -> dict[str, Any]:
    request_id = effect.get("request_id")
    reason = effect.get("reason")
    kind = effect.get("kind")
    phase = effect.get("phase")
    return {
        "phase": phase if phase in {"none", "pending", "retrying", "applied", "failed"} else "none",
        "kind": kind if kind in {"own", "full"} else None,
        "attempts": effect["attempts"] if isinstance(effect.get("attempts"), int) else 0,
        "max_attempts": STOP_ATTEMPTS,
        "reason": reason if isinstance(reason, str) and REASON_RE.fullmatch(reason) else None,
        "requested_ns": _safe_int(effect.get("requested_ns")),
        "updated_ns": _safe_int(effect.get("updated_ns")),
        "attempt_ns": [
            item
            for item in effect.get("attempt_ns") or []
            if isinstance(item, int) and not isinstance(item, bool)
        ][:6],
        "confirmed": effect.get("confirmed") is True,
        "needs_operator": phase == "failed",
        "request_id": request_id
        if isinstance(request_id, str) and ID_RE.fullmatch(request_id)
        else None,
    }


def _schedule_stop(
    db: sqlite3.Connection, kind: str, request_id: str, revision: int, now: int
) -> None:
    token = int(_meta_get(db, "stop_token", "0") or "0") + 1
    _meta_set(db, "stop_token", str(token))
    effect = _effect_blank()
    effect.update(
        {
            "token": token,
            "kind": kind,
            "request_id": request_id,
            "phase": "pending",
            "attempts": 0,
            "requested_ns": now,
            "state_revision": revision,
            "confirmed": False,
        }
    )
    _write_effect(db, effect, now)
    _event(
        db,
        "incident",
        "stop_pending",
        {"token": token, "kind": kind, "request_id": request_id, "revision": revision},
        now,
    )


def _stop_acked(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if "stopped" in result:
        return result.get("stopped") is True
    return result.get("ok") is True


def _effect_db(root: Path, mutate) -> Any:
    db = _connect(root)
    try:
        lock_for(root).acquire()
        db.execute("BEGIN IMMEDIATE")
        try:
            value = mutate(db)
            db.execute("COMMIT")
            return value
        except Exception:
            db.execute("ROLLBACK")
            raise
    finally:
        lock_for(root).release()
        db.close()


def _begin_attempt(root: Path) -> tuple[int, str] | None:
    def mutate(db: sqlite3.Connection) -> tuple[int, str] | None:
        effect = _read_effect(db)
        if effect["phase"] not in {"pending", "retrying"} or effect["kind"] not in {"own", "full"}:
            return None
        if effect["attempts"] >= STOP_ATTEMPTS:
            effect["phase"] = "failed"
            effect["confirmed"] = False
            effect["reason"] = effect["reason"] or "not_confirmed"
            now = now_ns()
            _write_effect(db, effect, now)
            _event(
                db,
                "incident",
                "stop_failed",
                {"token": effect["token"], "kind": effect["kind"], "attempts": effect["attempts"]},
                now,
            )
            return None
        now = now_ns()
        effect["attempts"] = int(effect["attempts"]) + 1
        effect["phase"] = "retrying"
        effect["confirmed"] = False
        effect["attempt_ns"] = [*effect["attempt_ns"], now][-6:]
        _write_effect(db, effect, now)
        _event(
            db,
            "incident",
            "stop_attempt",
            {
                "token": effect["token"],
                "kind": effect["kind"],
                "attempt": effect["attempts"],
                "ns": now,
            },
            now,
        )
        return int(effect["token"]), str(effect["kind"])

    return _effect_db(root, mutate)


def _complete_attempt(root: Path, token: int, acked: bool, reason: str) -> str:
    def mutate(db: sqlite3.Connection) -> str:
        effect = _read_effect(db)
        if int(effect["token"]) != int(token):
            return "stale"
        now = now_ns()
        if acked:
            effect["phase"] = "applied"
            effect["confirmed"] = True
            effect["reason"] = None
            _write_effect(db, effect, now)
            _event(
                db,
                "incident",
                "stop_applied",
                {"token": token, "kind": effect["kind"], "attempts": effect["attempts"]},
                now,
            )
            return "applied"
        safe = reason if reason in {"adapter_error", "not_confirmed"} else "adapter_error"
        effect["reason"] = safe
        effect["confirmed"] = False
        if int(effect["attempts"]) >= STOP_ATTEMPTS:
            effect["phase"] = "failed"
            _write_effect(db, effect, now)
            _event(
                db,
                "incident",
                "stop_failed",
                {
                    "token": token,
                    "kind": effect["kind"],
                    "attempts": effect["attempts"],
                    "reason": safe,
                },
                now,
            )
            return "failed"
        effect["phase"] = "retrying"
        _write_effect(db, effect, now)
        _event(
            db,
            "incident",
            "stop_retry",
            {
                "token": token,
                "kind": effect["kind"],
                "attempts": effect["attempts"],
                "reason": safe,
            },
            now,
        )
        return "retry"

    return _effect_db(root, mutate)


def _dispatch_stop(root: Path) -> None:
    """Call adapter.stop until it acknowledges, the token changes, or attempts are spent."""
    lock = _dispatch_lock(root)
    if not lock.acquire(blocking=False):
        return
    try:
        while True:
            claimed = _begin_attempt(root)
            if claimed is None:
                return
            token, kind = claimed
            acked = False
            reason = "adapter_error"
            try:
                result = load_adapter().stop(root, full=kind == "full")
                acked = _stop_acked(result)
                reason = "not_confirmed"
            except Exception as exc:
                private_log(root, "adapter_stop_failed", exc)
                acked = False
                reason = "adapter_error"
            outcome = _complete_attempt(root, token, acked, reason)
            if outcome in {"retry", "stale"}:
                continue
            return
    finally:
        lock.release()


def _call_stop(root: Path) -> None:
    _dispatch_stop(root)


def _try_state(db: sqlite3.Connection) -> dict[str, Any]:
    try:
        return _state(db)
    except Exception:
        return {
            "revision": 0,
            "mode": "idle",
            "phase": "idle",
            "collect_profile": "balanced",
            "selected_candidate_id": None,
            "active_exam_id": None,
            "resume_mode": None,
            "auto_reason": None,
            "cycle": _cycle_default(),
        }


def _bare(ok: bool, category: str, reason: str, request_id: str, http: int) -> dict[str, Any]:
    return {
        "schema": COMMAND_SCHEMA,
        "ok": ok,
        "category": category,
        "reason": reason,
        "request_id": request_id,
        "duplicate": False,
        "revision": 0,
        "mode": "idle",
        "phase": "idle",
        "job_id": None,
        "http_status": http,
    }


def initialize(root: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    root = Path(root)
    if root.is_symlink():
        raise Rejected("bad_request", "bad_root", 400)
    root.mkdir(parents=True, exist_ok=True)
    if config is not None:
        path = root / "config.json"
        if not path.exists():
            atomic_write(path, json.dumps(config, sort_keys=True, indent=2).encode())
    if not _claim(root):
        return {
            "ok": False,
            "schema": "operator-init-v1",
            "reason": "owned",
            "revision": 0,
            "recovered": [],
        }
    db = _connect(root)
    recovered: list[str] = []
    try:
        lock_for(root).acquire()
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        if db.execute("SELECT 1 FROM revisions LIMIT 1").fetchone() is None:
            state = {
                "mode": "idle",
                "phase": "idle",
                "collect_profile": "balanced",
                "selected_candidate_id": None,
                "active_exam_id": None,
                "resume_mode": None,
                "auto_reason": None,
                "cycle": _cycle_default(),
            }
            _push(db, state, "initialized", now_ns())
        recovered = _recover_locked(db, root, now_ns())
        revision = int(db.execute("SELECT MAX(revision) FROM revisions").fetchone()[0])
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    finally:
        lock_for(root).release()
        db.close()
    try:
        _dispatch_stop(root)
    except Exception as exc:
        private_log(root, "stop_dispatch_failed", exc)
    return {"ok": True, "schema": "operator-init-v1", "revision": revision, "recovered": recovered}


def recover(root: Path) -> dict[str, Any]:
    root = Path(root)
    db = _connect(root)
    try:
        lock_for(root).acquire()
        db.execute("BEGIN IMMEDIATE")
        _schema(db)
        found = _recover_locked(db, root, now_ns())
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    finally:
        lock_for(root).release()
        db.close()
    try:
        _dispatch_stop(root)
    except Exception as exc:
        private_log(root, "stop_dispatch_failed", exc)
    return {"ok": True, "recovered": found}


def _tracked(pid: int | None) -> subprocess.Popen[bytes] | None:
    if pid is None:
        return None
    for proc in _CHILDREN.values():
        if proc.pid == pid:
            return proc
    return None


def _signal_owned(pid: int) -> None:
    proc = _tracked(pid)
    if proc is not None and proc.poll() is None:
        terminate(pid)


def _read_result(art: Path) -> tuple[bytes | None, dict[str, Any] | None]:
    path = art / "operator-result.json"
    if not path.exists():
        return None, None
    try:
        raw = path.read_bytes()
        result = json.loads(raw.decode())
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None, None
    if not isinstance(result, dict):
        return raw, None
    return raw, result


def _marker_ok(art: Path, job_id: str, raw: bytes | None) -> bool:
    marker = art / "operator-published.marker"
    if raw is None or not marker.exists():
        return False
    try:
        lines = marker.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False
    digest = hashlib.sha256(raw).hexdigest()
    return len(lines) >= 3 and lines[0] == "v1" and lines[1] == job_id and lines[2] == digest


def _lease_open(root: Path, job_id: str) -> bool:
    path = lease_path(root, job_id)
    if not path.exists():
        return False
    try:
        lease = json.loads(path.read_text(encoding="utf-8"))
        return wall_ns() < int(lease["expires_ns"])
    except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        return False


def _recover_locked(db: sqlite3.Connection, root: Path, now: int) -> list[str]:
    """Do not signal a pid this process did not spawn. A valid success marker stays for tick."""
    found: list[str] = []
    if db.execute("SELECT 1 FROM revisions LIMIT 1").fetchone() is None:
        return found
    interrupted = False
    for row in db.execute("SELECT * FROM jobs WHERE status='running'"):
        art = artifact_dir(root, row["job_id"])
        raw, result = _read_result(art)
        if result is not None and _marker_ok(art, row["job_id"], raw):
            found.append(row["job_id"])
            continue
        if result is not None:
            params = json.loads(row["params_json"])
            ok, reason, _extracted = assess_result(row["action"], params, result)
            if ok:
                reason = "invalid_result"
            _finish_job(db, row, "failed", reason, now, None)
            _after_terminal(db, root, row, "failed", reason, now)
            found.append(row["job_id"])
            continue
        pid = int(row["pid"]) if row["pid"] else None
        tracked = _tracked(pid) if pid else None
        if tracked is not None and tracked.poll() is None:
            found.append(row["job_id"])
            continue
        if tracked is None and _lease_open(root, row["job_id"]):
            found.append(row["job_id"])
            continue
        _finish_job(db, row, "interrupted", "interrupted", now, None)
        _event(db, "incident", "interrupted", {"job_id": row["job_id"], "crash": True}, now)
        _after_terminal(db, root, row, "interrupted", "interrupted", now)
        found.append(row["job_id"])
        interrupted = True
    if interrupted:
        state = _attach_db_facts(db, _state(db))
        if state["mode"] == "auto":
            new = dict(state)
            expect = "collect" if not state.get("initial_collect_done") else "snapshot"
            new["cycle"] = {"expect": expect, "snapshot_id": None, "candidate_id": None}
            new["resume_mode"] = "auto"
            new["mode"] = "paused"
            new["phase"] = "paused"
            new["auto_reason"] = "interrupted"
            _save(db, state, new, "interrupted", now)
    return found


def tick(root: Path) -> dict[str, Any]:
    root = Path(root)
    try:
        _dispatch_stop(root)
    except Exception as exc:
        private_log(root, "stop_dispatch_failed", exc)
    view = _load_view(root)
    db = _connect(root)
    display: dict[str, Any] | None = None
    maintain_due = False
    state: dict[str, Any] | None = None
    try:
        lock_for(root).acquire()
        db.execute("BEGIN IMMEDIATE")
        try:
            _schema(db)
            if db.execute("SELECT 1 FROM revisions LIMIT 1").fetchone() is None:
                raise Rejected("bad_request", "uninitialized", 400)
            _sync_locked(db, root, view, now_ns())
            last = _meta_get(db, "last_maintain_ns", "0") or "0"
            maintain_due = wall_ns() - int(last) >= MAINTAIN_EVERY_S * 1_000_000_000
            if maintain_due:
                _meta_set(db, "last_maintain_ns", str(wall_ns()))
            state = _state(db)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    finally:
        lock_for(root).release()
        db.close()
    if maintain_due and state is not None:
        display = _maintain(root, state)
    if display is not None:
        _store_display(root, display, view)
    return status(root)


def _maintain(root: Path, state: dict[str, Any]) -> dict[str, Any] | None:
    payload = {
        "mode": state["mode"],
        "phase": state["phase"],
        "collect_profile": state["collect_profile"],
        "selected_candidate_id": state["selected_candidate_id"],
        "active_exam_id": state["active_exam_id"],
    }
    try:
        result = load_adapter().maintain(root, payload)
    except Exception as exc:
        private_log(root, "maintain_failed", exc)
        return {"ok": False, "reason": "adapter_error"}
    return result if isinstance(result, dict) else {}


def _store_display(root: Path, display: dict[str, Any], view: dict[str, Any]) -> None:
    progress = (
        display.get("training_progress")
        if isinstance(display.get("training_progress"), dict)
        else None
    )
    safe_progress = None
    if progress:
        checkpoint = progress.get("checkpoint_id")
        safe_progress = {
            "step": _safe_int(progress.get("step")),
            "steps": _safe_int(progress.get("steps")),
            "checkpoint_id": checkpoint
            if isinstance(checkpoint, str) and ID_RE.fullmatch(checkpoint)
            else None,
        }
    coverage = view["coverage"] if isinstance(view.get("coverage"), dict) else {}
    point = {
        "ns": now_ns(),
        "samples": _matured_now(view),
        "bars": _safe_int(coverage.get("bars")),
    }
    db = _connect(root)
    try:
        lock_for(root).acquire()
        db.execute("BEGIN IMMEDIATE")
        if safe_progress:
            _meta_set(db, "display", json.dumps(safe_progress, sort_keys=True))
        if point["samples"] is not None or point["bars"] is not None:
            _event(db, "history", "sample", point, point["ns"])
        if display.get("ok") is False:
            _event(db, "incident", _safe_reason(display.get("reason")), {}, point["ns"])
        db.execute("COMMIT")
    except Exception as exc:
        private_log(root, "display_store_failed", exc)
        try:
            db.execute("ROLLBACK")
        except sqlite3.Error:
            pass
    finally:
        lock_for(root).release()
        db.close()


def _sync_locked(db: sqlite3.Connection, root: Path, view: dict[str, Any], now: int) -> None:
    for row in db.execute("SELECT * FROM jobs WHERE status='running'"):
        pid = int(row["pid"]) if row["pid"] else None
        tracked = _tracked(pid)
        art = artifact_dir(root, row["job_id"])
        raw = _read_result(art)[0]
        overdue = row["deadline_ns"] is not None and wall_ns() >= int(row["deadline_ns"])
        if tracked is not None:
            if tracked.poll() is not None:
                _reap(pid, root)
                _classify(db, root, row, view, now)
                continue
            if overdue and not _marker_ok(art, row["job_id"], raw):
                _mark_stop(root, row["job_id"], "deadline")
                _signal_owned(pid)
                _reap(pid, root)
                _classify(db, root, row, view, now)
                continue
            _refresh_lease(root, row["job_id"])
            continue
        if _lease_open(root, row["job_id"]):
            continue
        _classify(db, root, row, view, now)
    state = _attach_db_facts(db, _state(db))
    bounds, _err = _bounds(root)
    _advance(db, root, state, view, bounds, now)
    _start_one(db, root, now, bounds, view)


def _reap(pid: int, root: Path) -> None:
    key = None
    for child_key, proc in list(_CHILDREN.items()):
        if proc.pid == pid:
            key = child_key
            proc.wait(timeout=2)
            break
    if key:
        _CHILDREN.pop(key, None)
    else:
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return


def _refresh_lease(root: Path, job_id: str) -> None:
    payload = {"job_id": job_id, "expires_ns": wall_ns() + int(LEASE_TTL_S * 1_000_000_000)}
    atomic_write(lease_path(root, job_id), json.dumps(payload).encode())


def _classify(
    db: sqlite3.Connection, root: Path, row: sqlite3.Row, view: dict[str, Any], now: int
) -> None:
    job_id = row["job_id"]
    current = db.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if current is None or current["status"] != "running":
        return
    art = artifact_dir(root, job_id)
    raw, parsed = _read_result(art)
    if _marker_ok(art, job_id, raw) and parsed is not None:
        stop = art / "operator-stop-reason"
        if stop.exists() and stop.read_text(encoding="utf-8").strip() == "deadline":
            stop.unlink(missing_ok=True)
    stop = art / "operator-stop-reason"
    if stop.exists() and not _marker_ok(art, job_id, raw):
        why = stop.read_text(encoding="utf-8").strip()
        reason = {
            "deadline": "deadline",
            "recover": "interrupted",
            "operator_stop": "interrupted",
        }.get(why, "interrupted")
        status = "failed" if reason == "deadline" else "interrupted"
        _finish_job(db, row, status, reason, now, None)
        _after_terminal(db, root, row, status, reason, now)
        return
    marker = art / "operator-published.marker"
    result_path = art / "operator-result.json"
    if not result_path.exists():
        _finish_job(db, row, "interrupted", "lease_lost", now, None)
        _after_terminal(db, root, row, "interrupted", "lease_lost", now)
        return
    try:
        raw = result_path.read_bytes()
        result = json.loads(raw.decode())
    except (OSError, json.JSONDecodeError, UnicodeError):
        _finish_job(db, row, "failed", "invalid_result", now, None)
        _after_terminal(db, root, row, "failed", "invalid_result", now)
        return
    params = json.loads(row["params_json"])
    ok, reason, extracted = assess_result(row["action"], params, result)
    marker_ok = False
    if marker.exists():
        try:
            lines = marker.read_text(encoding="utf-8").splitlines()
            digest = hashlib.sha256(raw).hexdigest()
            marker_ok = (
                len(lines) >= 3 and lines[0] == "v1" and lines[1] == job_id and lines[2] == digest
            )
        except (OSError, UnicodeError):
            marker_ok = False
    if ok and not marker_ok:
        ok, reason = False, "invalid_result"
    if ok and row["action"] == "snapshot" and extracted.get("matured_samples") is None:
        extracted["matured_samples"] = _matured_now(view)
    if not ok:
        _finish_job(db, row, "failed", reason, now, None)
        _after_terminal(db, root, row, "failed", reason, now)
        return
    if db.execute("SELECT 1 FROM publications WHERE job_id=?", (job_id,)).fetchone():
        _finish_job(db, row, "completed", "published", now, extracted)
        return
    kind = {"snapshot": "dataset", "train": "candidate", "paper": "exam", "collect": "collect"}[
        row["action"]
    ]
    public = {
        "snapshot": extracted.get("dataset_id"),
        "train": extracted.get("candidate_id"),
        "paper": extracted.get("exam_id"),
        "collect": extracted.get("profile"),
    }[row["action"]]
    db.execute(
        "INSERT INTO publications(job_id, kind, public_id, payload_json, published_ns) "
        "VALUES(?,?,?,?,?)",
        (job_id, kind, public, json.dumps(extracted, sort_keys=True), now),
    )
    _finish_job(db, row, "completed", "published", now, extracted)
    _after_terminal(db, root, row, "completed", "published", now)


def _finish_job(
    db: sqlite3.Connection,
    row: sqlite3.Row,
    status: str,
    reason: str,
    now: int,
    extracted: dict[str, Any] | None,
) -> None:
    extracted = extracted or {}
    db.execute(
        """UPDATE jobs SET status=?, error_reason=?, finished_ns=?, published=?,
           dataset_id=COALESCE(?, dataset_id), candidate_id=COALESCE(?, candidate_id),
           exam_id=COALESCE(?, exam_id), snapshot_hash=COALESCE(?, snapshot_hash),
           matured_samples=COALESCE(?, matured_samples),
           result_json=? WHERE job_id=?""",
        (
            status,
            reason,
            now,
            1 if status == "completed" else 0,
            extracted.get("dataset_id"),
            extracted.get("candidate_id"),
            extracted.get("exam_id"),
            extracted.get("snapshot_hash"),
            extracted.get("matured_samples"),
            json.dumps(extracted, sort_keys=True),
            row["job_id"],
        ),
    )
    kind = "audit" if status == "completed" else "incident"
    _event(
        db, kind, reason, {"job_id": row["job_id"], "action": row["action"], "status": status}, now
    )


def _after_terminal(
    db: sqlite3.Connection, root: Path, row: sqlite3.Row, status: str, reason: str, now: int
) -> None:
    state = _attach_db_facts(db, _state(db))
    if status == "completed" and row["action"] == "collect":
        _meta_set(db, "initial_collect_done", "1")
        state["initial_collect_done"] = True
    if status != "completed":
        if row["origin"] == "auto" or state["mode"] == "auto":
            new = dict(state)
            expect = "collect" if not state.get("initial_collect_done") else "snapshot"
            new["cycle"] = {"expect": expect, "snapshot_id": None, "candidate_id": None}
            new["resume_mode"] = (
                "auto"
                if state["mode"] == "auto" or state["resume_mode"] == "auto"
                else state["resume_mode"]
            )
            new["mode"] = "paused"
            new["phase"] = "paused"
            new["auto_reason"] = reason
            _save(db, state, new, reason, now)
        return
    if row["origin"] == "auto" and state["mode"] == "paused":
        new = dict(state)
        new["cycle"] = _next_cycle(state, row, db)
        if row["action"] == "train":
            new["selected_candidate_id"] = new["cycle"].get("candidate_id")
        if row["action"] == "paper":
            new["active_exam_id"] = new["cycle"].get("exam_hold")
        _save(db, state, new, "published", now)
        return
    if state["mode"] != "auto" or row["origin"] != "auto":
        if row["action"] == "paper":
            new = dict(state)
            new["active_exam_id"] = json.loads(
                db.execute(
                    "SELECT result_json FROM jobs WHERE job_id=?", (row["job_id"],)
                ).fetchone()[0]
            ).get("exam_id")
            _save(db, state, new, "published", now)
        if row["action"] == "train":
            new = dict(state)
            new["selected_candidate_id"] = json.loads(
                db.execute(
                    "SELECT result_json FROM jobs WHERE job_id=?", (row["job_id"],)
                ).fetchone()[0]
            ).get("candidate_id")
            _save(db, state, new, "published", now)
        return
    new = dict(state)
    new["cycle"] = dict(state["cycle"])
    result = json.loads(
        db.execute("SELECT result_json FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()[0]
    )
    if row["action"] == "collect":
        new["cycle"] = {"expect": "snapshot", "snapshot_id": None, "candidate_id": None}
        new["phase"] = "waiting"
    elif row["action"] == "snapshot":
        new["cycle"] = {
            "expect": "train",
            "snapshot_id": result.get("dataset_id"),
            "candidate_id": None,
        }
        new["phase"] = "train"
    elif row["action"] == "train":
        new["cycle"] = {
            "expect": "paper",
            "snapshot_id": state["cycle"].get("snapshot_id"),
            "candidate_id": result.get("candidate_id"),
        }
        new["selected_candidate_id"] = result.get("candidate_id")
        new["phase"] = "paper"
    elif row["action"] == "paper":
        new["cycle"] = {"expect": "snapshot", "snapshot_id": None, "candidate_id": None}
        new["active_exam_id"] = result.get("exam_id")
        new["phase"] = "waiting"
    _save(db, state, new, "published", now)


def _next_cycle(state: dict[str, Any], row: sqlite3.Row, db: sqlite3.Connection) -> dict[str, Any]:
    result = json.loads(
        db.execute("SELECT result_json FROM jobs WHERE job_id=?", (row["job_id"],)).fetchone()[0]
    )
    if row["action"] == "collect":
        return {"expect": "snapshot", "snapshot_id": None, "candidate_id": None}
    if row["action"] == "snapshot":
        return {"expect": "train", "snapshot_id": result.get("dataset_id"), "candidate_id": None}
    if row["action"] == "train":
        return {
            "expect": "paper",
            "snapshot_id": state["cycle"].get("snapshot_id"),
            "candidate_id": result.get("candidate_id"),
        }
    if row["action"] == "paper":
        return {
            "expect": "snapshot",
            "snapshot_id": None,
            "candidate_id": None,
            "exam_hold": result.get("exam_id"),
        }
    return dict(state["cycle"])


def _advance(
    db: sqlite3.Connection,
    root: Path,
    state: dict[str, Any],
    view: dict[str, Any],
    bounds: dict[str, Any],
    now: int,
) -> None:
    del root
    if state["mode"] != "auto":
        return
    if _active(_jobs(db)) is not None:
        return
    rows = _jobs(db)
    gates = evaluate(state, bounds, rows, view, now)
    if gates["expired"]:
        _pause_auto(db, state, "expired", now)
        return
    if gates["domain_stop"]:
        _pause_auto(db, state, "domain_stop", now)
        return
    expect = state["cycle"].get("expect", "none")
    if expect == "collect" or not state.get("initial_collect_done"):
        _enqueue(
            db,
            action="collect",
            params={"profile": state["collect_profile"]},
            origin="auto",
            now=now,
        )
        new = dict(state)
        new["phase"] = "collect"
        new["cycle"] = {"expect": "collect", "snapshot_id": None, "candidate_id": None}
        _save(db, state, new, "queued", now)
        return
    if expect == "train":
        snap = state["cycle"].get("snapshot_id")
        if not gates["science_ok"]:
            _pause_auto(db, state, "ineligible", now)
            return
        if not gates["quota_ok"]:
            _pause_auto(db, state, "quota", now)
            return
        if not gates["interval_ok"]:
            return
        known = _known_snapshots(db, view)
        if not snap or snap not in known or _used_snapshot(rows, snap, known.get(snap)):
            _pause_auto(db, state, "same_snapshot" if snap else "missing_snapshot", now)
            return
        _enqueue(
            db,
            action="train",
            params={"snapshot_id": snap},
            origin="auto",
            now=now,
            snapshot_hash=known.get(snap),
        )
        new = dict(state)
        new["phase"] = "train"
        _save(db, state, new, "queued", now)
        return
    if expect == "paper":
        cand = state["cycle"].get("candidate_id") or state.get("selected_candidate_id")
        if not gates["science_ok"]:
            _pause_auto(db, state, "ineligible", now)
            return
        if not cand or cand not in _known_candidates(db, view):
            _pause_auto(db, state, "missing_candidate", now)
            return
        limit = (
            _safe_int(view["limits"].get("max_open_exams"))
            if isinstance(view["limits"], dict)
            else None
        )
        if limit is not None and _open_exams(db, view, now) >= limit:
            _pause_auto(db, state, "exam_capacity", now)
            return
        _enqueue(db, action="paper", params={"candidate_id": cand}, origin="auto", now=now)
        new = dict(state)
        new["phase"] = "paper"
        new["selected_candidate_id"] = cand
        _save(db, state, new, "queued", now)
        return
    if expect == "snapshot" and gates["auto"]["eligible"]:
        _enqueue(db, action="snapshot", params={}, origin="auto", now=now)
        new = dict(state)
        new["phase"] = "snapshot"
        new["cycle"] = {"expect": "snapshot", "snapshot_id": None, "candidate_id": None}
        _save(db, state, new, "queued", now)


def _pause_auto(db: sqlite3.Connection, state: dict[str, Any], reason: str, now: int) -> None:
    new = dict(state)
    new["mode"] = "paused"
    new["phase"] = "paused"
    new["resume_mode"] = "auto"
    new["auto_reason"] = reason
    new["cycle"] = _cycle_default()
    _save(db, state, new, reason, now)


def _runtime_root(root: Path) -> str:
    packaged = Path(__file__).resolve().parents[1]
    if (root / "deployment.json").exists() and _deployment_ok(root):
        from memecoin_operator.deployment import guard

        info = guard(root)
        return str(Path(info["bundle"]) / "runtime")
    return str(packaged)


def _spawn_worker(root: Path, job_id: str, log_handle: Any) -> subprocess.Popen[bytes]:
    read_fd, write_fd = os.pipe()
    os.set_inheritable(read_fd, True)
    os.set_inheritable(write_fd, False)
    runtime_root = _runtime_root(root)
    code = (
        "import sys; sys.path.insert(0, sys.argv.pop(1)); "
        "from memecoin_operator.runtime import main; raise SystemExit(main())"
    )
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["OPERATOR_PARENT_FD"] = str(read_fd)
    env.pop("PYTHONPATH", None)
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                code,
                runtime_root,
                "--worker",
                "--root",
                str(root),
                "--job",
                job_id,
            ],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
            cwd=str(root),
            pass_fds=(read_fd,),
        )
    except OSError:
        os.close(write_fd)
        raise
    finally:
        os.close(read_fd)
    _PARENT_FDS.append(write_fd)
    return proc


def _job_dir_ok(root: Path, art: Path) -> bool:
    operator = root / "operator"
    jobs = operator / "jobs"
    if operator.is_symlink() or jobs.is_symlink() or art.is_symlink():
        return False
    try:
        base = root.resolve()
        if jobs.exists() and not jobs.resolve().is_relative_to(base):
            return False
        if art.exists() and not art.resolve().is_relative_to(base):
            return False
    except OSError:
        return False
    return True


def _start_one(
    db: sqlite3.Connection,
    root: Path,
    now: int,
    bounds: dict[str, Any],
    view: dict[str, Any],
) -> None:
    state = _state(db)
    if state["mode"] in {"paused", "stopped"}:
        return
    if _stop_open(_read_effect(db)):
        return
    if bounds["expires_ns"] is not None and now_ns() >= int(bounds["expires_ns"]):
        return
    if isinstance(view.get("limits"), dict) and view["limits"].get("domain_stop"):
        return
    if bounds.get("virtual_blocked"):
        return
    running = db.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone()
    if running is not None:
        return
    row = db.execute(
        "SELECT * FROM jobs WHERE status='queued' ORDER BY created_ns ASC, job_id ASC LIMIT 1"
    ).fetchone()
    if row is None:
        return
    job_id = row["job_id"]
    art = artifact_dir(root, job_id)
    if not _job_dir_ok(root, art):
        db.execute(
            "UPDATE jobs SET status='failed', error_reason='rejected', finished_ns=? "
            "WHERE job_id=?",
            (now, job_id),
        )
        return
    art.mkdir(parents=True, exist_ok=True)
    request = {"job_id": job_id, "action": row["action"], "params": json.loads(row["params_json"])}
    atomic_write(art / "operator-request.json", json.dumps(request, sort_keys=True).encode())
    _refresh_lease(root, job_id)
    deadline = wall_ns() + int(DEADLINES[row["action"]] * 1_000_000_000)
    log_path = art / "worker.log"
    log_handle = log_path.open("ab")
    try:
        proc = _spawn_worker(root, job_id, log_handle)
    except OSError as exc:
        private_log(root, "spawn_failed", exc)
        db.execute(
            "UPDATE jobs SET status='failed', error_reason='adapter_unavailable', "
            "finished_ns=? WHERE job_id=?",
            (now, job_id),
        )
        return
    finally:
        log_handle.close()
    _CHILDREN[(str(root), job_id)] = proc
    db.execute(
        "UPDATE jobs SET status='running', pid=?, started_ns=?, deadline_ns=? WHERE job_id=?",
        (proc.pid, now, deadline, job_id),
    )
    _event(db, "audit", "running", {"job_id": job_id, "action": row["action"]}, now)


def status(root: Path) -> dict[str, Any]:
    root = Path(root)
    host = _host_view(root)
    view = _load_view(root)
    bounds, config_error = _bounds(root)
    db = _connect(root)
    try:
        lock_for(root).acquire()
        db.execute("BEGIN")
        _schema(db)
        if db.execute("SELECT 1 FROM revisions LIMIT 1").fetchone() is None:
            db.execute("COMMIT")
            empty = _empty_status(bounds, ["uninitialized"])
            empty["host"] = host
            empty["stop_effects"] = _public_effect(_effect_blank())
            return empty
        state = _attach_db_facts(db, _state(db))
        rows = _jobs(db)
        gates = evaluate(state, bounds, rows, view, now_ns())
        projected = _project(db, state, rows, view, bounds, gates, config_error)
        projected["host"] = host
        projected["stop_effects"] = _public_effect(_read_effect(db))
        db.execute("COMMIT")
        return projected
    finally:
        lock_for(root).release()
        db.close()


def _empty_status(bounds: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "ok": True,
        "revision": 0,
        "mode": "idle",
        "phase": "idle",
        "collect_profile": "balanced",
        "selected_candidate_id": None,
        "active_exam_id": None,
        "auto_reason": None,
        "resume_mode": None,
        "expired": False,
        "expires_ns": bounds["expires_ns"],
        "now_ns": now_ns(),
        "domain_stop": False,
        "promotion": False,
        "engine": _engine(),
        "gates": {},
        "source": {},
        "coverage": {"bars": None, "pools": None, "history": []},
        "paper": {"cash": None, "equity": None, "positions": [], "trades": [], "decisions": []},
        "snapshots": [],
        "candidates": [],
        "champion": {"sha": bounds["champion_sha"], "promoted": False},
        "exams": [],
        "jobs": [],
        "commands": [],
        "incidents": [],
        "recovery": None,
        "legacy": _legacy([]),
        "training": _training(None, None),
        "timeline": [],
        "limits": {"eligible": None, "reasons": [], "max_open_exams": None},
        "host": None,
        "stop_effects": _public_effect(_effect_blank()),
        "warnings": warnings,
    }


def _engine() -> dict[str, Any]:
    return {
        "name": "fixed_time_15m",
        "exit": "fixed_15m",
        "learned_exit": False,
        "mpc": False,
        "price_proxy": "geckoterminal_5m",
        "slippage_bps": 50,
        "fee_bps_per_side": 25,
        "champion_policy": "empirical_unchanged",
    }


def _project(
    db: sqlite3.Connection,
    state: dict[str, Any],
    rows: list[sqlite3.Row],
    view: dict[str, Any],
    bounds: dict[str, Any],
    gates: dict[str, Any],
    config_error: str | None,
) -> dict[str, Any]:
    warnings = []
    if view.get("warning"):
        warnings.append(view["warning"])
    if config_error:
        warnings.append(config_error)
    public_gates = {
        key: gates[key]
        for key in (
            "auto",
            "manual",
            "checks",
            "minimum_new_matured",
            "runs_used",
            "runs_max",
            "envelope_s",
            "min_refit_interval_s",
            "last_reservation_ns",
            "matured_now",
            "matured_prior",
            "matured_delta",
            "initial_run",
            "initial_collect_done",
            "busy",
            "not_before_ns",
        )
    }
    projected = {
        "schema": SCHEMA,
        "ok": True,
        "revision": state["revision"],
        "mode": state["mode"],
        "phase": state["phase"],
        "collect_profile": state["collect_profile"],
        "selected_candidate_id": state["selected_candidate_id"],
        "active_exam_id": state["active_exam_id"],
        "auto_reason": state["auto_reason"],
        "resume_mode": state["resume_mode"],
        "expired": gates["expired"],
        "expires_ns": bounds["expires_ns"],
        "now_ns": now_ns(),
        "domain_stop": gates["domain_stop"],
        "promotion": False,
        "engine": _engine(),
        "gates": public_gates,
        "source": _source(view),
        "coverage": _coverage(db, view),
        "paper": _paper(view),
        "snapshots": _snapshots(db, view),
        "candidates": _candidates(db, view),
        "champion": {"sha": bounds["champion_sha"], "promoted": False},
        "exams": _exams(db, view),
        "jobs": [_job_public(row) for row in rows][-20:],
        "commands": _commands(db),
        "incidents": _incidents(db, view),
        "recovery": _recovery(view),
        "legacy": _legacy(view.get("legacy") or []),
        "training": _training_view(db, rows, view),
        "timeline": _timeline(db),
        "limits": _limits(view),
        "policy": _policy(view),
        "block_reasons": _block_reasons(gates, state),
        "warnings": warnings,
    }
    kind = view.get("data_kind")
    if isinstance(kind, str) and kind in {"synthetic", "recorded", "live"}:
        projected["data_kind"] = kind
    if isinstance(view.get("synthetic"), bool):
        projected["synthetic"] = view["synthetic"]
    return projected


def _scrub(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= 2**63 - 1 else None
    if isinstance(value, float):
        return value if _safe_float(value) is not None else None
    if isinstance(value, str):
        if "/" in value or "\\" in value or "\x00" in value or len(value) > 240:
            return None
        return value
    if isinstance(value, list):
        out = []
        for item in value[:40]:
            clean = _scrub(item, depth + 1)
            if clean is not None:
                out.append(clean)
        return out
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:40]:
            if not isinstance(key, str) or key.lower() in _DROP_KEYS:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key):
                continue
            clean = _scrub(item, depth + 1)
            if clean is not None:
                out[key] = clean
        return out
    return None


def _flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _name_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    names: list[str] = []
    for item in value[:40]:
        if isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", item):
            names.append(item)
    return names


def _host_view(root: Path) -> dict[str, Any] | None:
    try:
        from memecoin_operator.host import snapshot

        raw = snapshot(root)
    except Exception as exc:
        private_log(root, "host_unavailable", exc)
        return None
    if not isinstance(raw, dict):
        return None
    clean = _scrub(raw)
    return clean if isinstance(clean, dict) else None


def _source(view: dict[str, Any]) -> dict[str, Any]:
    source = view.get("source") if isinstance(view.get("source"), dict) else {}
    reason = source.get("stop_reason")
    profile = source.get("profile_effective")
    if profile not in {"balanced", "history", "frozen_B09"}:
        profile = None
    return {
        "provider": source.get("provider") if source.get("provider") in {"geckoterminal"} else None,
        "bar_seconds": _safe_int(source.get("bar_seconds")),
        "heartbeat_ns": _safe_int(source.get("heartbeat_ns")),
        "heartbeat_age_s": _safe_float(source.get("heartbeat_age_s")),
        "freshness_s": _safe_int(source.get("freshness_s")),
        "budget_used": _safe_int(source.get("budget_used")),
        "budget_max": _safe_int(source.get("budget_max")),
        "attempts": _safe_int(source.get("attempts")),
        "max_requests": _safe_int(source.get("max_requests")),
        "cooldown_until_ns": _safe_int(source.get("cooldown_until_ns")),
        "cooldown_ns": _safe_int(source.get("cooldown_ns")),
        "failures": _safe_int(source.get("failures")),
        "expires_ns": _safe_int(source.get("expires_ns")),
        "matured_samples": _matured_now(view),
        "event_days": _safe_int(source.get("event_days")),
        "receipt_days": _safe_int(source.get("receipt_days")),
        "observed_pools": _safe_int(source.get("observed_pools")),
        "first_s": _safe_int(source.get("first_s")),
        "last_s": _safe_int(source.get("last_s")),
        "per_pool": _scrub(source.get("per_pool")) or [],
        "hot_pools": _name_list(source.get("hot_pools")),
        "healthy": _flag(source.get("healthy")),
        "stopped": _flag(source.get("stopped")),
        "borrowed": _flag(source.get("borrowed")),
        "single_provider": _flag(source.get("single_provider")),
        "profile_effective": profile,
        "quarantine": _scrub(source.get("quarantine")),
        "succession_pending": _flag(source.get("succession_pending")),
        "unseen_test_pools": _name_list(source.get("unseen_test_pools")),
        "stop_reason": _safe_reason(reason, "unknown") if isinstance(reason, str) else None,
    }


def _history_points(db: sqlite3.Connection, view: dict[str, Any]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for bucket in (view.get("source", {}).get("history"), view.get("coverage", {}).get("history")):
        if not isinstance(bucket, list):
            continue
        for item in bucket[-240:]:
            if not isinstance(item, dict):
                continue
            ns = _safe_int(item.get("ns"))
            if ns is None:
                continue
            points.append(
                {
                    "ns": ns,
                    "samples": _safe_int(item.get("samples")),
                    "bars": _safe_int(item.get("bars")),
                }
            )
    for row in db.execute(
        "SELECT payload_json FROM events WHERE kind='history' ORDER BY event_id DESC LIMIT 240"
    ):
        item = json.loads(row["payload_json"])
        points.append(
            {"ns": item.get("ns"), "samples": item.get("samples"), "bars": item.get("bars")}
        )
    points.sort(key=lambda item: item["ns"] or 0)
    dedup: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in points:
        if item["ns"] in seen:
            continue
        seen.add(item["ns"])
        dedup.append(item)
    return dedup[-240:]


def _coverage(db: sqlite3.Connection, view: dict[str, Any]) -> dict[str, Any]:
    coverage = view.get("coverage") if isinstance(view.get("coverage"), dict) else {}
    pools = coverage.get("pools")
    if isinstance(pools, dict):
        pools = None
    return {
        "bars": _safe_int(coverage.get("bars")),
        "pools": _safe_int(pools if pools is not None else coverage.get("observed_pools")),
        "observed_pools": _safe_int(coverage.get("observed_pools")),
        "matured_samples": _safe_int(coverage.get("matured_samples")),
        "event_days": _safe_int(coverage.get("event_days")),
        "receipt_days": _safe_int(coverage.get("receipt_days")),
        "first_s": _safe_int(coverage.get("first_s")),
        "last_s": _safe_int(coverage.get("last_s")),
        "per_pool": _scrub(coverage.get("per_pool")) or [],
        "unseen_test_pools": _name_list(coverage.get("unseen_test_pools")),
        "history": _history_points(db, view),
    }


def _paper(view: dict[str, Any]) -> dict[str, Any]:
    paper = view.get("paper") if isinstance(view.get("paper"), dict) else {}
    decisions = []
    for item in paper.get("decisions") or []:
        if not isinstance(item, dict):
            continue
        reason = item.get("reason") if isinstance(item.get("reason"), str) else item.get("action")
        stage = item.get("stage") if isinstance(item.get("stage"), str) else None
        if stage is not None and (
            not REASON_RE.fullmatch(stage)
            and stage
            not in {
                "intent",
                "fill",
                "decision",
                "existing_or_pending_exposure",
            }
        ):
            stage = None
        market = item.get("market") if isinstance(item.get("market"), str) else None
        if market is not None and ("/" in market or len(market) > 80):
            market = None
        decisions.append(
            {
                "ns": _safe_int(item.get("decision_ns") or item.get("ns")),
                "action": map_action(item.get("action") if item.get("action") else reason),
                "reason": _safe_reason(reason, "unclassified") if isinstance(reason, str) else None,
                "stage": stage,
                "filled": _flag(item.get("filled")),
                "q": _scrub(item.get("q")),
                "pool": item.get("pool")
                if isinstance(item.get("pool"), str) and POOL_RE.fullmatch(item.get("pool"))
                else None,
                "candidate_id": _safe_id(item.get("candidate_id")),
                "exam_id": _safe_id(item.get("exam_id")),
                "model_sha": _safe_hash(item.get("model_sha")),
                "market": market,
            }
        )
    positions = []
    for item in paper.get("positions") or []:
        if not isinstance(item, dict):
            continue
        positions.append(
            {
                "pool": item.get("pool")
                if isinstance(item.get("pool"), str) and POOL_RE.fullmatch(item["pool"])
                else None,
                "qty": _money(item.get("qty")),
                "status": item.get("status") if item.get("status") in {"open", "pending"} else None,
                "entry_ns": _safe_int(item.get("entry_ns")),
                "exit_due_ns": _safe_int(item.get("exit_due_ns") or item.get("exit_due_s")),
                "candidate_id": _safe_id(item.get("candidate_id")),
                "exam_id": _safe_id(item.get("exam_id")),
                "model_sha": _safe_hash(item.get("model_sha")),
            }
        )
    trades = []
    for item in paper.get("trades") or []:
        if not isinstance(item, dict):
            continue
        trades.append(
            {
                "pool": item.get("pool")
                if isinstance(item.get("pool"), str) and POOL_RE.fullmatch(item["pool"])
                else None,
                "net_pnl": _money(item.get("net_pnl")),
                "fees": _money(item.get("fees")),
                "entry_ns": _safe_int(item.get("entry_ns")),
                "exit_ns": _safe_int(item.get("exit_ns")),
                "exit_kind": "fixed_15m",
                "candidate_id": _safe_id(item.get("candidate_id")),
                "exam_id": _safe_id(item.get("exam_id")),
                "model_sha": _safe_hash(item.get("model_sha")),
            }
        )
    pending = [item for item in positions if item.get("status") == "pending"]
    extra_pending = _scrub(paper.get("pending"))
    if isinstance(extra_pending, list):
        pending.extend(item for item in extra_pending if isinstance(item, dict))
    return {
        "cash": _money(paper.get("cash")),
        "equity": _money(paper.get("equity")),
        "realized": _money(paper.get("realized")),
        "unrealized": _money(paper.get("unrealized")),
        "fees": _money(paper.get("fees")),
        "independent_books": _flag(paper.get("independent_books")),
        "capital_per_book": _money(paper.get("capital_per_book")),
        "positions": positions[-40:],
        "pending": pending[-40:],
        "trades": trades[-40:],
        "decisions": decisions[-40:],
        "books": _scrub(paper.get("books")) or [],
        "forecast_scores": _scrub(paper.get("forecast_scores")) or [],
        "generated_ns": _safe_int(paper.get("generated_ns")),
    }


def _snapshot_fields(item: dict[str, Any]) -> dict[str, Any]:
    digest = _safe_hash(item.get("snapshot_hash") or item.get("dataset_sha"))
    return {
        "snapshot_hash": digest,
        "dataset_sha": _safe_hash(item.get("dataset_sha")) or digest,
        "matured_samples": _safe_int(item.get("matured_samples")),
        "as_of_ns": _safe_int(item.get("as_of_ns")),
        "recipe_sha": _safe_hash(item.get("recipe_sha")),
        "recipe": _scrub(item.get("recipe")),
        "retained": _scrub(item.get("retained")),
        "original": _scrub(item.get("original")),
        "archive": _scrub(item.get("archive")),
        "new_fits": _safe_int(item.get("new_fits")),
        "historical_test_independent": _flag(item.get("historical_test_independent")),
        "unseen_test_pools": _scrub(item.get("unseen_test_pools")),
    }


def _snapshots(db: sqlite3.Connection, view: dict[str, Any]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for row in db.execute("SELECT * FROM publications WHERE kind='dataset' ORDER BY published_ns"):
        payload = json.loads(row["payload_json"])
        found[row["public_id"]] = {
            "dataset_id": row["public_id"],
            "created_ns": int(row["published_ns"]),
            "origin": "operator",
            **_snapshot_fields(payload),
        }
    for item in view.get("datasets") or []:
        if not isinstance(item, dict):
            continue
        dataset = _safe_id(item.get("dataset_id") or item.get("id"))
        if not dataset:
            continue
        fields = _snapshot_fields(item)
        fields["created_ns"] = _safe_int(item.get("created_ns"))
        if dataset in found:
            current = found[dataset]
            for key, value in fields.items():
                if value not in (None, [], {}) or key not in current:
                    if value not in (None, [], {}):
                        current[key] = value
            continue
        found[dataset] = {"dataset_id": dataset, "origin": "inspect", **fields}
    return list(found.values())


def _validation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, item in list(value.items())[:20]:
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,40}", key):
            continue
        if isinstance(item, bool):
            out[key] = item
        elif isinstance(item, int) and abs(item) <= 10**18:
            out[key] = item
        elif isinstance(item, float) and _safe_float(item) is not None:
            out[key] = item
        elif isinstance(item, str) and REASON_RE.fullmatch(item):
            out[key] = item
    return out


def _candidates(db: sqlite3.Connection, view: dict[str, Any]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for row in db.execute(
        "SELECT * FROM publications WHERE kind='candidate' ORDER BY published_ns"
    ):
        payload = json.loads(row["payload_json"])
        found[row["public_id"]] = {
            "candidate_id": row["public_id"],
            "snapshot_id": payload.get("snapshot_id"),
            "snapshot_hash": payload.get("snapshot_hash"),
            "dataset_sha": _safe_hash(payload.get("dataset_sha")),
            "model_sha": _safe_hash(payload.get("model_sha")),
            "status": "completed",
            "promoted": False,
            "selected": _scrub(payload.get("selected")),
            "validation": _validation(payload.get("validation")),
            "test": _scrub(payload.get("test")),
            "support": _scrub(payload.get("support")),
            "recipe": _scrub(payload.get("recipe")),
            "finished_ns": _safe_int(payload.get("finished_ns")),
            "origin": "operator",
        }
    for item in view.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        cand = _safe_id(item.get("candidate_id"))
        if not cand:
            continue
        slot = found.get(cand)
        detail = item.get("validation_scores")
        if not isinstance(detail, dict):
            detail = item.get("validation") if isinstance(item.get("validation"), dict) else None
        validation = _validation(item.get("validation"))
        rich = {
            "snapshot_id": _safe_id(item.get("snapshot_id") or item.get("dataset_id")),
            "snapshot_hash": _safe_hash(item.get("snapshot_hash") or item.get("dataset_sha")),
            "dataset_sha": _safe_hash(item.get("dataset_sha")),
            "model_sha": _safe_hash(item.get("model_sha")),
            "selected": _scrub(item.get("selected")),
            "validation": validation,
            "validation_scores": _scrub(detail),
            "test": _scrub(item.get("test")),
            "support": _scrub(item.get("support")),
            "recipe": _scrub(item.get("recipe")),
            "finished_ns": _safe_int(item.get("finished_ns")),
        }
        if slot is None:
            found[cand] = {
                "candidate_id": cand,
                "status": _candidate_status(item),
                "promoted": False,
                "origin": "inspect",
                **rich,
            }
        else:
            projected = _candidate_status(item)
            raw = item.get("status")
            if raw in {"failed", "incomplete", "unknown"} or (
                projected == "incomplete" and slot.get("model_sha") is None
            ):
                slot["status"] = projected
            for key, value in rich.items():
                if value not in (None, {}, []):
                    slot[key] = value
    return list(found.values())


def _exams(db: sqlite3.Connection, view: dict[str, Any]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for row in db.execute("SELECT * FROM publications WHERE kind='exam' ORDER BY published_ns"):
        payload = json.loads(row["payload_json"])
        found[row["public_id"]] = {
            "exam_id": row["public_id"],
            "candidate_id": payload.get("candidate_id"),
            "started_ns": int(row["published_ns"]),
            "expires_ns": _safe_int(payload.get("expires_ns")),
            "status": "active",
            "origin": "operator",
        }
    for item in view.get("exams") or []:
        if not isinstance(item, dict):
            continue
        exam = _safe_id(item.get("exam_id"))
        if not exam:
            continue
        status = item.get("status")
        if not (isinstance(status, str) and REASON_RE.fullmatch(status)):
            status = None
        expires = _safe_int(item.get("expires_ns"))
        if expires is not None and now_ns() >= expires:
            status = "expired"
        slot = found.get(exam)
        if slot is None:
            found[exam] = {
                "exam_id": exam,
                "candidate_id": _safe_id(item.get("candidate_id")),
                "started_ns": _safe_int(item.get("started_ns")),
                "expires_ns": expires,
                "status": status,
                "origin": "inspect",
            }
            continue
        if status is not None:
            slot["status"] = status
        if expires is not None:
            slot["expires_ns"] = expires
        candidate = _safe_id(item.get("candidate_id"))
        if candidate:
            slot["candidate_id"] = candidate
        slot["origin"] = "operator+inspect" if slot["origin"] == "operator" else slot["origin"]
    rows = list(found.values())
    for row in rows:
        expires = row.get("expires_ns")
        if isinstance(expires, int) and now_ns() >= expires:
            row["status"] = "expired"
    return rows[-20:]


def _job_public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "action": row["action"],
        "status": row["status"],
        "origin": row["origin"],
        "reason": row["error_reason"],
        "created_ns": row["created_ns"],
        "started_ns": row["started_ns"],
        "finished_ns": row["finished_ns"],
        "dataset_id": row["dataset_id"],
        "candidate_id": row["candidate_id"],
        "exam_id": row["exam_id"],
    }


def _commands(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute("SELECT * FROM commands ORDER BY created_ns DESC LIMIT 15").fetchall()
    out = []
    for row in rows:
        body = json.loads(row["result_json"])
        out.append(
            {
                "request_id": row["request_id"],
                "action": row["action"],
                "ok": body.get("ok"),
                "reason": body.get("reason"),
                "created_ns": row["created_ns"],
                "job_id": body.get("job_id"),
            }
        )
    return out


def _domain_incidents(view: dict[str, Any]) -> list[dict[str, Any]]:
    raw = view.get("incidents")
    if not isinstance(raw, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw[:40]:
        if not isinstance(item, dict):
            continue
        ident = item.get("id")
        if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
            continue
        kind = item.get("kind")
        reason = item.get("reason")
        safe_kind = kind if isinstance(kind, str) and REASON_RE.fullmatch(kind) else None
        safe_reason = reason if isinstance(reason, str) and REASON_RE.fullmatch(reason) else None
        rows.append(
            {
                "id": ident,
                "kind": safe_kind,
                "reason": safe_reason,
                "at_ns": _safe_int(item.get("at_ns")),
                "success": item.get("success") if isinstance(item.get("success"), bool) else None,
                "stopped": item.get("stopped") if isinstance(item.get("stopped"), bool) else None,
                "origin": "domain",
            }
        )
    return rows


_SERVICE_LABEL = re.compile(r"[A-Za-z0-9._-]{1,120}")


def _recovery(view: dict[str, Any]) -> dict[str, Any] | None:
    raw = view.get("recovery")
    if not isinstance(raw, dict) or not raw:
        return None
    services: list[dict[str, Any]] = []
    items = raw.get("services")
    if isinstance(items, list):
        for item in items[:20]:
            if not isinstance(item, dict):
                continue
            label = item.get("label")
            reason = item.get("reason")
            if not (isinstance(reason, str) and REASON_RE.fullmatch(reason)):
                reason = None
            services.append(
                {
                    "label": label
                    if isinstance(label, str) and _SERVICE_LABEL.fullmatch(label)
                    else None,
                    "loaded": item.get("loaded") if isinstance(item.get("loaded"), bool) else None,
                    "reason": reason,
                }
            )
    return {
        "paused": raw.get("paused") if isinstance(raw.get("paused"), bool) else None,
        "resume_attempts": _safe_int(raw.get("resume_attempts")),
        "retry_after_ns": _safe_int(raw.get("retry_after_ns")),
        "services": services,
    }


def _incidents(db: sqlite3.Connection, view: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    domain = _domain_incidents(view) if isinstance(view, dict) else []
    rows = db.execute(
        """SELECT event_id, reason, payload_json, created_ns FROM events
           WHERE kind='incident' ORDER BY event_id DESC LIMIT 30"""
    ).fetchall()
    operator = [
        {
            "event_id": row["event_id"],
            "reason": row["reason"],
            "ns": row["created_ns"],
            "summary": json.loads(row["payload_json"]),
        }
        for row in rows
    ]
    return domain + operator


def _legacy(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        ident = item.get("id") if isinstance(item.get("id"), str) else item.get("label")
        if not isinstance(ident, str) or not ID_RE.fullmatch(ident) or ident in seen:
            continue
        port = item.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            port = None
        paper = item.get("paper") if isinstance(item.get("paper"), dict) else {}
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        label = item.get("label")
        out.append(
            {
                "id": ident,
                "label": label if isinstance(label, str) and ID_RE.fullmatch(label) else ident,
                "port": port,
                "url": f"http://127.0.0.1:{port}" if port else None,
                "healthy": _flag(item.get("healthy"))
                if _flag(item.get("healthy")) is not None
                else _flag(paper.get("healthy")),
                "stopped": _flag(item.get("stopped"))
                if _flag(item.get("stopped")) is not None
                else _flag(paper.get("stopped")),
                "source_healthy": _flag(source.get("healthy")),
            }
        )
        seen.add(ident)
    return out


def _training(job: sqlite3.Row | None, progress: dict[str, Any] | None) -> dict[str, Any]:
    progress = progress or {}
    if job is None:
        return {
            "job_id": None,
            "status": None,
            "step": progress.get("step"),
            "steps": progress.get("steps"),
            "checkpoint_id": progress.get("checkpoint_id"),
            "elapsed_s": None,
            "deadline_s": None,
        }
    elapsed = None
    if job["started_ns"]:
        done = job["status"] in {"completed", "failed", "interrupted"} and job["finished_ns"]
        end = int(job["finished_ns"]) if done else wall_ns()
        elapsed = max(0, int((end - int(job["started_ns"])) / 1_000_000_000))
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "step": progress.get("step"),
        "steps": progress.get("steps"),
        "checkpoint_id": progress.get("checkpoint_id"),
        "elapsed_s": elapsed,
        "deadline_s": DEADLINES.get(job["action"]),
    }


def _training_view(
    db: sqlite3.Connection, rows: list[sqlite3.Row], view: dict[str, Any] | None = None
) -> dict[str, Any]:
    raw = _meta_get(db, "display")
    progress = json.loads(raw) if raw else None
    if not isinstance(progress, dict):
        progress = None
    current = next((row for row in reversed(rows) if row["status"] == "running"), None)
    if current is None:
        current = next((row for row in reversed(rows) if row["action"] == "train"), None)
    body = _training(current, progress)
    items = []
    listed = (view or {}).get("training_progress")
    if isinstance(listed, dict):
        listed = [listed]
    if isinstance(listed, list):
        for item in listed[:20]:
            clean = _scrub(item)
            if isinstance(clean, dict):
                items.append(clean)
    if isinstance(progress, dict):
        items.insert(0, progress)
    body["progress"] = items[:20]
    if body.get("step") is None and items:
        body["step"] = _safe_int(items[0].get("step"))
        body["steps"] = body.get("steps") or _safe_int(items[0].get("steps"))
        if body.get("checkpoint_id") is None:
            checkpoint = items[0].get("checkpoint_id") or items[0].get("fit") or items[0].get("id")
            body["checkpoint_id"] = (
                checkpoint if isinstance(checkpoint, str) and ID_RE.fullmatch(checkpoint) else None
            )
    return body


def _policy(view: dict[str, Any]) -> dict[str, Any]:
    policy = view.get("policy") if isinstance(view.get("policy"), dict) else {}
    name = policy.get("name") if isinstance(policy.get("name"), str) else None
    if name is None or "/" in name or len(name) > 80:
        name = "B07 fixed-time price proxy"
    champion = policy.get("champion")
    if not isinstance(champion, str) or "/" in champion or len(champion) > 40:
        champion = "empirical"
    return {
        "name": name,
        "holding_minutes": _safe_int(policy.get("holding_minutes")) or 15,
        "learned_exit": False,
        "slippage_bps": _safe_int(policy.get("slippage_bps")) or 50,
        "fee_bps": _safe_int(policy.get("fee_bps")) or 25,
        "capital_usd": _money(policy.get("capital_usd")) or 1000,
        "automatic_promotion": False,
        "champion_model_sha": _safe_hash(policy.get("champion_model_sha")),
        "champion": champion,
    }


def _block_reasons(gates: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    manual = gates.get("manual") if isinstance(gates.get("manual"), dict) else {}
    for action, spec in manual.items():
        if isinstance(spec, dict) and spec.get("reasons"):
            rows.append({"action": action, "reasons": list(spec["reasons"])})
    auto = gates.get("auto") if isinstance(gates.get("auto"), dict) else {}
    if auto.get("reasons"):
        rows.append({"action": "auto", "reasons": list(auto["reasons"])})
    if state.get("auto_reason"):
        rows.append({"action": "resume", "reasons": [state["auto_reason"]]})
    return rows


def _timeline(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT revision, mode, phase, reason, created_ns FROM revisions "
        "ORDER BY revision DESC LIMIT 24"
    ).fetchall()
    return [
        {
            "revision": row["revision"],
            "mode": row["mode"],
            "phase": row["phase"],
            "reason": row["reason"],
            "ns": row["created_ns"],
        }
        for row in reversed(rows)
    ]


def _limits(view: dict[str, Any]) -> dict[str, Any]:
    limits = view.get("limits") if isinstance(view.get("limits"), dict) else {}
    reasons = []
    for reason in limits.get("reasons") or []:
        if isinstance(reason, str) and REASON_RE.fullmatch(reason):
            reasons.append(reason)
    eligible = limits.get("eligible") if isinstance(limits.get("eligible"), bool) else None
    return {
        "eligible": eligible,
        "reasons": reasons[:12],
        "max_open_exams": _safe_int(limits.get("max_open_exams")),
    }


def events(root: Path, after: int = 0, limit: int = 50) -> dict[str, Any]:
    root = Path(root)
    if isinstance(after, bool) or not isinstance(after, int) or after < 0:
        after = 0
    if isinstance(limit, bool) or not isinstance(limit, int):
        limit = 50
    limit = min(200, max(1, limit))
    db = _connect(root)
    try:
        lock_for(root).acquire()
        db.execute("BEGIN")
        _schema(db)
        rows = db.execute(
            """SELECT event_id, kind, reason, payload_json, created_ns FROM events
               WHERE event_id > ? ORDER BY event_id ASC LIMIT ?""",
            (after, limit),
        ).fetchall()
        db.execute("COMMIT")
    finally:
        lock_for(root).release()
        db.close()
    payload = []
    for row in rows:
        summary = json.loads(row["payload_json"])
        payload.append(
            {
                "event_id": row["event_id"],
                "kind": row["kind"],
                "reason": row["reason"],
                "ns": row["created_ns"],
                "summary": summary if isinstance(summary, dict) else {},
            }
        )
    next_after = payload[-1]["event_id"] if payload else after
    return {"schema": EVENTS_SCHEMA, "ok": True, "events": payload, "next_after": next_after}
