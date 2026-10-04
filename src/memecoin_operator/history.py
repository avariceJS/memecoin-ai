"""Read-only life ledger for the B13 operator. No writers, weights, or raw reads."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from memecoin_operator.control import HASH_RE, ID_RE, POOL_RE, REASON_RE, map_action
from memecoin_operator.telemetry import parse_payload

SCHEMA = "operator-evidence-v1"
NS = 1_000_000_000
MAX_GAP_NS = 90 * NS
STALE_DASHBOARD_NS = 120 * NS
JSON_LIMIT = 8_000_000
SERIES_CAP = 5000
READ_FIRST_CAP = 40

TABLES: dict[str, str] = {
    "meta": "key",
    "revisions": "revision",
    "commands": "request_id",
    "jobs": "job_id",
    "events": "event_id",
    "publications": "job_id",
}
REQUIRED: dict[str, frozenset[str]] = {
    "meta": frozenset({"key", "value"}),
    "revisions": frozenset(
        {
            "revision",
            "mode",
            "phase",
            "collect_profile",
            "selected_candidate_id",
            "active_exam_id",
            "resume_mode",
            "auto_reason",
            "cycle_json",
            "reason",
            "created_ns",
        }
    ),
    "commands": frozenset(
        {
            "request_id",
            "fingerprint",
            "action",
            "params_json",
            "http_status",
            "result_json",
            "created_ns",
        }
    ),
    "jobs": frozenset(
        {
            "job_id",
            "action",
            "params_json",
            "status",
            "origin",
            "result_json",
            "error_reason",
            "pid",
            "deadline_ns",
            "started_ns",
            "finished_ns",
            "reserved_ns",
            "dataset_id",
            "snapshot_id",
            "snapshot_hash",
            "candidate_id",
            "exam_id",
            "matured_samples",
            "published",
            "created_ns",
        }
    ),
    "events": frozenset({"event_id", "kind", "reason", "payload_json", "created_ns"}),
    "publications": frozenset({"job_id", "kind", "public_id", "payload_json", "published_ns"}),
}
PAYLOAD_KEYS = frozenset(
    {
        "job_id",
        "action",
        "status",
        "origin",
        "mode",
        "phase",
        "ns",
        "samples",
        "bars",
        "pools",
        "event_days",
        "crash",
        "reason",
        "exam_id",
        "service",
        "healthy",
        "dataset_id",
        "candidate_id",
        "snapshot_id",
    }
)
FAMILIES = frozenset({"empirical", "linear", "gru"})
BOTTLENECKS = frozenset(
    {
        "quota",
        "interval",
        "insufficient_growth",
        "unknown_growth",
        "domain_stop",
        "expired",
        "paused",
        "stopped",
        "busy",
        "missing_candidate",
        "missing_snapshot",
        "same_snapshot",
        "exam_capacity",
        "interrupted",
        "ineligible",
        "deadline",
        "lease_lost",
        "invalid_result",
        "partial_result",
        "adapter_error",
        "adapter_unavailable",
        "collect_first",
    }
)
LEDGER_PARTS = frozenset(
    {"datasets", "candidates", "exams", "paper", "shadow", "ledger", "raw", "weights"}
)
DOCUMENTS = (
    "docs/brain-autonomous-operator.md",
    "docs/brain-operator-evidence.md",
    "docs/brain-paper-loop.md",
    "docs/brain-development.md",
    "memory/evaluation.md",
)
LABEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}\Z")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,160}\Z")
VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}\Z")
METRIC_RE = re.compile(r"-?\d{1,12}(\.\d{1,12})?\Z")
SERVICE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}\Z")
BAR_LINE_RE = re.compile(r"(?m)^BAR = ([0-9]{2,5})$")
FORECAST_NAMESPACE = "candidate-shadows/<model>/shadow and legacy exam/shadow"


class _Ctx:
    def __init__(self, root: Path, cutoff: int) -> None:
        self.root = root
        self.cutoff = cutoff
        self.reason: str | None = None
        self.db: sqlite3.Connection | None = None
        self.schema_ok = False
        self.config: dict[str, Any] = {}
        self.config_sha: str | None = None
        self.runtime_manifest_sha: str | None = None
        self.warnings: list[str] = []
        self.refused: list[dict[str, Any]] = []
        self.inputs: list[dict[str, str]] = []
        self.bound: list[dict[str, str]] = []
        self.journal_sha: str | None = None
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.heartbeats: list[dict[str, Any]] = []
        self.heartbeat_reason = "no_heartbeat_table"
        self.telemetry: list[dict[str, Any]] = []

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def refuse(self, code: str, kind: str, item_id: str | None) -> None:
        self.refused.append({"code": code, "kind": kind, "id": item_id})


def _int(value: Any, *, low: int | None = None, high: int | None = None) -> int | None:
    if type(value) is not int:
        return None
    if low is not None and value < low:
        return None
    if high is not None and value > high:
        return None
    return value


def _token(value: Any, pattern: re.Pattern[str]) -> str | None:
    if isinstance(value, str) and pattern.fullmatch(value):
        return value
    return None


def _metric(value: Any) -> str | None:
    if isinstance(value, str) and METRIC_RE.fullmatch(value):
        return value
    if type(value) is int:
        return str(value)
    if isinstance(value, Decimal) and value.is_finite():
        return format(value, "f")
    if type(value) is float and math.isfinite(value):
        return format(Decimal(str(value)), "f")
    return None


def _hours(ns: int) -> str:
    quantum = Decimal("0.000000001")
    return format((Decimal(ns) / Decimal(3_600_000_000_000)).quantize(quantum), "f")


def _sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("ascii")).hexdigest()


def _protected(path: Path) -> bool:
    for part in path.parts:
        low = part.lower()
        if "holdout" in low or low == "f01" or low.startswith(("f01-", "f01_")):
            return True
        if low == "d43" or low.startswith(("d43-", "d43_")):
            return True
    return False


def _load_json(
    path: Path, limit: int = JSON_LIMIT, *, decimals: bool = False
) -> tuple[Any | None, str | None]:
    try:
        size = path.stat().st_size
    except OSError:
        return None, "unreadable"
    if size > limit:
        return None, "oversized"
    try:
        text = path.read_text(encoding="utf-8")
        if decimals:
            return json.loads(text, parse_float=Decimal), None
        return json.loads(text), None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "unreadable"


def _within(base: Path, relative: Path) -> Path | None:
    if relative.is_absolute() or ".." in relative.parts:
        return None
    cursor = base
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return None
    try:
        if not cursor.exists() or not cursor.resolve().is_relative_to(base.resolve()):
            return None
    except OSError:
        return None
    return cursor


def _note_input(ctx: _Ctx, path: Path) -> None:
    relative = None
    try:
        relative = path.resolve().relative_to(ctx.root.resolve()).as_posix()
    except (OSError, ValueError):
        return
    digest = _sha256(path)
    if digest is None:
        return
    row = {"path": relative, "sha256": digest}
    if row not in ctx.inputs:
        ctx.inputs.append(row)


def _locate(ctx: _Ctx, raw: Any, kind: str, item_id: str | None) -> Path | None:
    if not isinstance(raw, str) or not raw or len(raw) > 4096 or "\x00" in raw:
        ctx.refuse("bad_artifact_path", kind, item_id)
        return None
    candidate = Path(raw)
    if candidate.is_symlink():
        ctx.refuse("symlink_artifact", kind, item_id)
        return None
    if not candidate.is_absolute():
        if ".." in candidate.parts:
            ctx.refuse("outside_root", kind, item_id)
            return None
        cursor = ctx.root
        for part in candidate.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                ctx.refuse("symlink_artifact", kind, item_id)
                return None
        full = cursor
    else:
        full = candidate
    if full.is_symlink():
        ctx.refuse("symlink_artifact", kind, item_id)
        return None
    try:
        resolved = full.resolve(strict=True)
        root_real = ctx.root.resolve()
    except OSError:
        ctx.refuse("missing_artifact", kind, item_id)
        return None
    if _protected(resolved) or not resolved.is_relative_to(root_real):
        code = "protected_path" if _protected(resolved) else "outside_root"
        ctx.refuse(code, kind, item_id)
        return None
    walk = root_real
    for part in resolved.relative_to(root_real).parts:
        walk = walk / part
        if walk.is_symlink():
            ctx.refuse("symlink_artifact", kind, item_id)
            return None
    return walk


def _bound_dir(ctx: _Ctx, raw: Any, kind: str) -> Path | None:
    if not isinstance(raw, str) or not raw or len(raw) > 4096 or "\x00" in raw:
        ctx.refuse("bad_bound_path", kind, None)
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        if ".." in candidate.parts:
            ctx.refuse("outside_root", kind, None)
            return None
        candidate = ctx.root / candidate
    if candidate.is_symlink() or _protected(candidate):
        ctx.refuse("protected_path" if _protected(candidate) else "symlink_artifact", kind, None)
        return None
    if not candidate.is_dir():
        ctx.refuse("missing_artifact", kind, None)
        return None
    return candidate


def _snapshot_db(path: Path) -> sqlite3.Connection:
    """Consistent live backup. Do not copy WAL/SHM bytes and do not set immutable=1."""
    source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        source.execute("PRAGMA query_only=ON")
        memory = sqlite3.connect(":memory:")
        source.backup(memory)
    finally:
        source.close()
    memory.row_factory = sqlite3.Row
    return memory


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})")}


def _table_names(db: sqlite3.Connection) -> set[str]:
    rows = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {str(row["name"]) for row in rows}


def _read_table(db: sqlite3.Connection, table: str, order: str) -> list[dict[str, Any]]:
    rows = []
    for row in db.execute(f"SELECT * FROM {table} ORDER BY {order}"):
        rows.append({key: row[key] for key in row.keys()})
    return rows


def _payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw) > 65536:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    clean: dict[str, Any] = {}
    for key in PAYLOAD_KEYS:
        if key not in value:
            continue
        item = value[key]
        if key in {"job_id", "dataset_id", "candidate_id", "snapshot_id", "exam_id"}:
            token = _token(item, ID_RE)
            if token:
                clean[key] = token
        elif key in {"action", "status", "origin", "mode", "phase", "reason", "service"}:
            token = _token(item, REASON_RE) or _token(item, SERVICE_RE)
            if token:
                clean[key] = token
        elif key in {"ns", "samples", "bars", "pools", "event_days"}:
            number = _int(item, low=0)
            if number is not None:
                clean[key] = number
        elif key in {"crash", "healthy"} and isinstance(item, bool):
            clean[key] = item
    return clean


def _open(root: Path | str, now_ns: int | None) -> _Ctx:
    if now_ns is None:
        cutoff = time.time_ns()
    elif type(now_ns) is not int or now_ns < 0:
        ctx = _Ctx(Path(root), 0)
        ctx.reason = "bad_cutoff"
        return ctx
    else:
        cutoff = now_ns
    path = Path(root)
    ctx = _Ctx(path, cutoff)
    if path.is_symlink() or _protected(path):
        ctx.reason = "protected_root" if _protected(path) else "symlink_root"
        return ctx
    if not path.exists():
        ctx.reason = "missing_root"
        return ctx
    if not path.is_dir():
        ctx.reason = "bad_root"
        return ctx
    config_path = path / "config.json"
    if config_path.is_symlink():
        ctx.refuse("symlink_artifact", "config", None)
    elif config_path.is_file():
        ctx.config_sha = _sha256(config_path)
        _note_input(ctx, config_path)
        loaded, error = _load_json(config_path, limit=1_000_000)
        if error or not isinstance(loaded, dict):
            ctx.warnings.append("config_unreadable")
        else:
            ctx.config = loaded
    db_path = path / "operator.sqlite"
    if db_path.is_symlink():
        ctx.reason = "symlink_database"
        return ctx
    if not db_path.is_file():
        ctx.reason = "uninitialized"
        return ctx
    try:
        ctx.db = _snapshot_db(db_path)
    except (OSError, sqlite3.Error):
        ctx.reason = "sqlite_unreadable"
        return ctx
    names = _table_names(ctx.db)
    missing = [name for name in TABLES if name not in names]
    if missing:
        ctx.reason = "unsupported_schema"
        ctx.warnings.append("missing_table")
        return ctx
    for name, required in REQUIRED.items():
        if not required <= _columns(ctx.db, name):
            ctx.reason = "unsupported_schema"
            ctx.warnings.append("missing_column")
            return ctx
    schema_row = ctx.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    if schema_row is not None and str(schema_row["value"]) != "operator-status-v1":
        ctx.reason = "unsupported_schema"
        return ctx
    ctx.schema_ok = True
    for name, order in TABLES.items():
        ctx.tables[name] = _read_table(ctx.db, name, order)
    _load_telemetry(ctx)
    if "heartbeats" not in names:
        ctx.heartbeat_reason = "no_heartbeat_table"
        _seal_journal(ctx)
        return ctx
    columns = _columns(ctx.db, "heartbeats")
    if "at_ns" not in columns:
        ctx.heartbeat_reason = "heartbeat_schema_unsupported"
        ctx.warnings.append("heartbeat_schema_unsupported")
        _seal_journal(ctx)
        return ctx
    select = ["at_ns"]
    for column in ("service", "exam_id", "healthy"):
        if column in columns:
            select.append(column)
    sql = f"SELECT {', '.join(select)} FROM heartbeats WHERE at_ns<=? ORDER BY at_ns"
    for row in ctx.db.execute(sql, (cutoff,)):
        at_ns = _int(row["at_ns"], low=0)
        if at_ns is None:
            continue
        item: dict[str, Any] = {"at_ns": at_ns, "service": "recorded"}
        if "service" in select:
            item["service"] = _token(row["service"], SERVICE_RE) or "unclassified"
        if "exam_id" in select:
            item["exam_id"] = _token(row["exam_id"], ID_RE)
        if "healthy" in select and type(row["healthy"]) is int and row["healthy"] in {0, 1}:
            item["healthy"] = row["healthy"]
        ctx.heartbeats.append(item)
    ctx.heartbeat_reason = "no_heartbeat_rows" if not ctx.heartbeats else ""
    _seal_journal(ctx)
    return ctx


def _load_telemetry(ctx: _Ctx) -> None:
    if ctx.db is None or "telemetry" not in _table_names(ctx.db):
        return
    columns = _columns(ctx.db, "telemetry")
    if not {"at_ns", "payload_json"} <= columns:
        ctx.warnings.append("telemetry_schema_unsupported")
        return
    rows = ctx.db.execute(
        "SELECT at_ns, payload_json FROM telemetry WHERE at_ns<=? ORDER BY at_ns",
        (ctx.cutoff,),
    ).fetchall()
    if len(rows) > SERIES_CAP:
        ctx.warnings.append("telemetry_truncated")
        rows = rows[-SERIES_CAP:]
    skipped = False
    for row in rows:
        parsed = parse_payload(row["payload_json"])
        if parsed is None or parsed.get("ns") != row["at_ns"]:
            skipped = True
            continue
        ctx.telemetry.append(parsed)
    if skipped:
        ctx.warnings.append("telemetry_unreadable")


def _seal_journal(ctx: _Ctx) -> None:
    body = {name: ctx.tables[name] for name in sorted(TABLES)}
    body["heartbeats"] = ctx.heartbeats
    body["telemetry"] = ctx.telemetry
    ctx.journal_sha = _digest(body)


def _bounds(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "created_ns": _int(config.get("created_ns"), low=0),
        "expires_ns": _int(config.get("expires_ns"), low=0),
        "max_training_runs": _int(config.get("max_training_runs"), low=1, high=100),
        "min_refit_interval_s": _int(config.get("min_refit_interval_s"), low=0, high=366 * 86400),
        "minimum_new_matured": _int(config.get("minimum_new_matured"), low=0, high=10**12),
        "paid_budget_usd": config["paid_budget_usd"]
        if type(config.get("paid_budget_usd")) is int
        else None,
        "real_execution_enabled": config.get("real_execution_enabled")
        if isinstance(config.get("real_execution_enabled"), bool)
        else None,
        "port": _int(config.get("port"), low=1, high=65535),
        "champion_model_sha": _token(config.get("champion_model_sha"), HASH_RE),
    }


def _revisions(ctx: _Ctx) -> list[dict[str, Any]]:
    rows = []
    for row in ctx.tables.get("revisions", []):
        created = _int(row.get("created_ns"), low=0)
        if created is None or created > ctx.cutoff:
            continue
        cycle_expect = None
        raw_cycle = row.get("cycle_json")
        if isinstance(raw_cycle, str):
            try:
                cycle = json.loads(raw_cycle)
            except json.JSONDecodeError:
                cycle = None
            if isinstance(cycle, dict):
                cycle_expect = (
                    cycle.get("expect")
                    if cycle.get("expect")
                    in {
                        "none",
                        "collect",
                        "snapshot",
                        "train",
                        "paper",
                    }
                    else None
                )
        rows.append(
            {
                "revision": _int(row.get("revision"), low=0),
                "mode": _token(row.get("mode"), SERVICE_RE) or "unclassified",
                "phase": _token(row.get("phase"), SERVICE_RE) or "unclassified",
                "reason": _token(row.get("reason"), REASON_RE) or "unclassified",
                "auto_reason": _token(row.get("auto_reason"), REASON_RE),
                "collect_profile": _token(row.get("collect_profile"), SERVICE_RE),
                "selected_candidate_id": _token(row.get("selected_candidate_id"), ID_RE),
                "active_exam_id": _token(row.get("active_exam_id"), ID_RE),
                "expect": cycle_expect,
                "ns": created,
            }
        )
    return rows


def _jobs(ctx: _Ctx) -> list[dict[str, Any]]:
    rows = []
    for row in ctx.tables.get("jobs", []):
        created = _int(row.get("created_ns"), low=0)
        if created is None or created > ctx.cutoff:
            continue
        rows.append(
            {
                "job_id": _token(row.get("job_id"), ID_RE),
                "action": _token(row.get("action"), SERVICE_RE),
                "status": _token(row.get("status"), SERVICE_RE),
                "origin": _token(row.get("origin"), SERVICE_RE),
                "reason": _token(row.get("error_reason"), REASON_RE),
                "created_ns": created,
                "started_ns": _int(row.get("started_ns"), low=0),
                "finished_ns": _int(row.get("finished_ns"), low=0),
                "reserved_ns": _int(row.get("reserved_ns"), low=0),
                "dataset_id": _token(row.get("dataset_id"), ID_RE),
                "candidate_id": _token(row.get("candidate_id"), ID_RE),
                "exam_id": _token(row.get("exam_id"), ID_RE),
                "published": row.get("published") == 1 or row.get("published") is True,
            }
        )
    return rows


def _events(ctx: _Ctx) -> list[dict[str, Any]]:
    rows = []
    for row in ctx.tables.get("events", []):
        created = _int(row.get("created_ns"), low=0)
        if created is None or created > ctx.cutoff:
            continue
        kind = _token(row.get("kind"), SERVICE_RE) or "unclassified"
        rows.append(
            {
                "event_id": _int(row.get("event_id"), low=0),
                "kind": kind,
                "reason": _token(row.get("reason"), REASON_RE) or "unclassified",
                "ns": created,
                "summary": _payload(row.get("payload_json")),
            }
        )
    return rows


def _publications(ctx: _Ctx) -> list[dict[str, Any]]:
    rows = []
    for row in ctx.tables.get("publications", []):
        published = _int(row.get("published_ns"), low=0)
        if published is None or published > ctx.cutoff:
            continue
        rows.append(
            {
                "job_id": _token(row.get("job_id"), ID_RE),
                "kind": _token(row.get("kind"), SERVICE_RE),
                "public_id": _token(row.get("public_id"), ID_RE),
                "ns": published,
                "summary": _payload(row.get("payload_json")),
            }
        )
    return rows


def _union_ns(intervals: list[tuple[int, int]]) -> int:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start for start, end in merged)


def _unknown_observed(reason: str) -> dict[str, Any]:
    return {
        "status": "unknown",
        "reason": reason,
        "observed_ns": None,
        "observed_hours": None,
        "elapsed_ns": None,
        "elapsed_hours": None,
        "observer_ns": None,
        "observer_hours": None,
        "healthy_service_ns": None,
        "healthy_service_hours": None,
        "presence_label": "recorded_intervals_including_unhealthy_not_uptime",
        "uptime_claim": False,
        "heartbeat_rows": 0,
        "unhealthy_heartbeats": None,
        "max_counted_interval_s": 90,
        "per_service": [],
        "gaps": [],
        "telemetry_rows": 0,
    }


def _observed(ctx: _Ctx) -> dict[str, Any]:
    if ctx.heartbeat_reason:
        unknown = _unknown_observed(ctx.heartbeat_reason)
        unknown["telemetry_rows"] = len(ctx.telemetry)
        return unknown
    grouped: dict[tuple[str, str], dict[int, int | None]] = {}
    unhealthy = 0
    for beat in ctx.heartbeats:
        exam = beat.get("exam_id") or ""
        stamps = grouped.setdefault((str(beat["service"]), str(exam)), {})
        at_ns = int(beat["at_ns"])
        healthy = beat.get("healthy") if beat.get("healthy") in {0, 1} else None
        if at_ns not in stamps:
            stamps[at_ns] = healthy
        else:
            previous = stamps[at_ns]
            if healthy == 0 or previous == 0:
                stamps[at_ns] = 0
            elif healthy == 1 and previous == 1:
                stamps[at_ns] = 1
            elif previous is None:
                stamps[at_ns] = healthy
        if healthy == 0:
            unhealthy += 1
    intervals: list[tuple[int, int]] = []
    observer_intervals: list[tuple[int, int]] = []
    healthy_intervals: list[tuple[int, int]] = []
    per_service = []
    gaps = []
    for (service, exam), stamps in sorted(grouped.items()):
        ordered = sorted(stamps)
        observed = 0
        healthy_observed = 0
        for left, right in zip(ordered, ordered[1:], strict=False):
            delta = right - left
            if delta <= MAX_GAP_NS:
                observed += delta
                intervals.append((left, right))
                if service == "operator":
                    observer_intervals.append((left, right))
                if service != "operator" and stamps[left] == 1 and stamps[right] == 1:
                    healthy_observed += delta
                    healthy_intervals.append((left, right))
            else:
                gaps.append(
                    {
                        "service": service,
                        "exam_id": exam or None,
                        "start_ns": left,
                        "end_ns": right,
                        "gap_ns": delta,
                        "trailing": False,
                    }
                )
        if ctx.cutoff - ordered[-1] > MAX_GAP_NS:
            gaps.append(
                {
                    "service": service,
                    "exam_id": exam or None,
                    "start_ns": ordered[-1],
                    "end_ns": ctx.cutoff,
                    "gap_ns": ctx.cutoff - ordered[-1],
                    "trailing": True,
                }
            )
        per_service.append(
            {
                "service": service,
                "exam_id": exam or None,
                "heartbeats": len(ordered),
                "observed_ns": observed,
                "observed_hours": _hours(observed),
                "healthy_service_ns": healthy_observed if service != "operator" else 0,
                "first_ns": ordered[0],
                "last_ns": ordered[-1],
            }
        )
    observed_ns = _union_ns(intervals)
    observer_ns = _union_ns(observer_intervals)
    healthy_ns = _union_ns(healthy_intervals)
    first = min(beat["at_ns"] for beat in ctx.heartbeats)
    elapsed = ctx.cutoff - first
    return {
        "status": "measured",
        "reason": None,
        "observed_ns": observed_ns,
        "observed_hours": _hours(observed_ns),
        "elapsed_ns": elapsed,
        "elapsed_hours": _hours(elapsed),
        "observer_ns": observer_ns,
        "observer_hours": _hours(observer_ns),
        "healthy_service_ns": healthy_ns,
        "healthy_service_hours": _hours(healthy_ns),
        "presence_label": "recorded_intervals_including_unhealthy_not_uptime",
        "uptime_claim": False,
        "heartbeat_rows": len(ctx.heartbeats),
        "unhealthy_heartbeats": unhealthy,
        "max_counted_interval_s": 90,
        "per_service": per_service,
        "gaps": gaps,
        "telemetry_rows": len(ctx.telemetry),
    }


def _catalogue(ctx: _Ctx, folder: str) -> list[dict[str, Any]]:
    directory = ctx.root / folder
    if directory.is_symlink() or not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink() or not path.is_file() or not NAME_RE.fullmatch(path.name):
            code = "symlink_artifact" if path.is_symlink() else "bad_artifact_path"
            ctx.refuse(code, folder, path.name)
            continue
        _note_input(ctx, path)
        loaded, error = _load_json(path)
        if error or not isinstance(loaded, dict):
            ctx.refuse(error or "unreadable", folder, path.stem)
            continue
        item_id = _token(loaded.get("id"), ID_RE) or _token(path.stem, ID_RE)
        created = _int(loaded.get("created_ns"), low=0)
        if created is not None and created > ctx.cutoff:
            continue
        rows.append({"id": item_id, "record": loaded, "path": path})
    return rows


def _verified_tree(
    ctx: _Ctx, record: dict[str, Any], kind: str, item_id: str | None
) -> Path | None:
    artifact = _locate(ctx, record.get("artifact"), kind, item_id)
    if artifact is None or not artifact.is_dir():
        if artifact is not None:
            ctx.refuse("missing_artifact", kind, item_id)
        return None
    manifest = _within(artifact, Path("manifest.json"))
    if manifest is None or not manifest.is_file():
        ctx.refuse("unpinned_artifact", kind, item_id)
        return None
    actual = _sha256(manifest)
    claimed = _token(record.get("manifest_sha"), HASH_RE)
    if actual is None or claimed is None or actual != claimed.lower():
        ctx.refuse("tampered_manifest", kind, item_id)
        return None
    _note_input(ctx, manifest)
    return artifact


def _support(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    decisions = _int(value.get("decisions"), low=0)
    pools = _int(value.get("pools"), low=0)
    days = _int(value.get("days"), low=0)
    found = {
        key: value
        for key, value in (("decisions", decisions), ("pools", pools), ("days", days))
        if value is not None
    }
    return found or None


def _family_metrics(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        return {}
    found = {}
    for name, item in value.items():
        if name not in FAMILIES or not isinstance(item, dict):
            continue
        pinball = _metric(item.get("pool_macro_pinball"))
        if pinball is not None:
            found[name] = {"pool_macro_pinball": pinball}
    return found


def _snapshots(ctx: _Ctx) -> list[dict[str, Any]]:
    found = []
    for entry in _catalogue(ctx, "datasets"):
        record = entry["record"]
        item_id = entry["id"]
        artifact = _verified_tree(ctx, record, "dataset", item_id)
        coverage = None
        dataset_sha = _token(record.get("dataset_sha"), HASH_RE)
        verified = False
        if artifact is not None:
            coverage_path = _within(artifact, Path("coverage.json"))
            dataset_manifest = _within(artifact, Path("dataset/manifest.json"))
            if dataset_manifest is not None and dataset_manifest.is_file():
                actual = _sha256(dataset_manifest)
                verified = bool(dataset_sha and actual == dataset_sha.lower())
                if dataset_sha and not verified:
                    ctx.refuse("tampered_snapshot", "dataset", item_id)
            if verified and coverage_path is not None and coverage_path.is_file():
                loaded, error = _load_json(coverage_path)
                if error:
                    ctx.warnings.append("coverage_unreadable")
                elif isinstance(loaded, dict):
                    coverage = loaded
                    _note_input(ctx, coverage_path)
        retained = {}
        if isinstance(coverage, dict) and isinstance(coverage.get("retained"), dict):
            for fold in ("train", "validation", "test"):
                support = _support(coverage["retained"].get(fold))
                if support:
                    retained[fold] = support
        unseen = []
        if isinstance(coverage, dict) and isinstance(coverage.get("unseen_test_pools"), list):
            for pool in coverage["unseen_test_pools"][:50]:
                token = _token(pool, POOL_RE)
                if token:
                    unseen.append(token)
        recipe = {}
        if isinstance(coverage, dict):
            raw_recipe = coverage.get("recipe") if isinstance(coverage.get("recipe"), dict) else {}
            version = _token(raw_recipe.get("version"), VERSION_RE)
            if version:
                recipe["version"] = version
            recipe_sha = _token(coverage.get("recipe_sha"), HASH_RE)
            if recipe_sha:
                recipe["sha256"] = recipe_sha.lower()
        found.append(
            {
                "dataset_id": item_id,
                "created_ns": _int(record.get("created_ns"), low=0),
                "dataset_sha": dataset_sha.lower() if dataset_sha else None,
                "manifest_sha": _token(record.get("manifest_sha"), HASH_RE),
                "verified": verified,
                "as_of_ns": _int(coverage.get("as_of_ns"), low=0)
                if isinstance(coverage, dict)
                else None,
                "matured_samples": _int(coverage.get("matured_samples"), low=0)
                if isinstance(coverage, dict)
                else _int(record.get("matured_samples"), low=0),
                "retained": retained,
                "unseen_test_pools": unseen,
                "historical_test_independent": False,
                "recipe": recipe,
            }
        )
    found.sort(key=lambda item: (item["created_ns"] or 0, item["dataset_id"] or ""))
    return found


def _models(ctx: _Ctx, publications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    published = {row["public_id"] for row in publications if row["kind"] == "candidate"}
    found = []
    claimed_promotion = False
    for entry in _catalogue(ctx, "candidates"):
        record = entry["record"]
        item_id = entry["id"]
        if record.get("promoted") is True:
            claimed_promotion = True
        artifact = _verified_tree(ctx, record, "candidate", item_id)
        card = None
        model_sha = _token(record.get("model_sha"), HASH_RE)
        verified = False
        if artifact is not None:
            manifest = _within(artifact, Path("model/manifest.json"))
            card_path = _within(artifact, Path("model/model-card.json"))
            if manifest is not None and manifest.is_file():
                actual = _sha256(manifest)
                verified = bool(model_sha and actual == model_sha.lower())
                if model_sha and not verified:
                    ctx.refuse("tampered_model", "candidate", item_id)
            if verified and card_path is not None and card_path.is_file():
                loaded, error = _load_json(card_path)
                if not error and isinstance(loaded, dict):
                    card = loaded
                    _note_input(ctx, card_path)
                    if card.get("promoted") is True:
                        claimed_promotion = True
        selected = None
        validation: dict[str, dict[str, str]] = {}
        historical = None
        recipe: dict[str, Any] = {}
        if isinstance(card, dict):
            selected = card.get("selected") if card.get("selected") in FAMILIES else None
            validation = _family_metrics(card.get("validation"))
            test_metrics = _family_metrics(card.get("test"))
            if test_metrics:
                historical = {
                    "label": "reused_descriptive",
                    "independent": False,
                    "claimed_independent": card.get("historical_test_independent") is True,
                    "pool_macro_pinball": {
                        name: item["pool_macro_pinball"] for name, item in test_metrics.items()
                    },
                }
            raw_recipe = card.get("recipe") if isinstance(card.get("recipe"), dict) else {}
            version = _token(raw_recipe.get("version"), VERSION_RE) or _token(
                card.get("model_version"), VERSION_RE
            )
            if version:
                recipe["version"] = version
            if _int(raw_recipe.get("steps"), low=0) is not None:
                recipe["steps"] = raw_recipe["steps"]
        found.append(
            {
                "candidate_id": item_id,
                "dataset_id": _token(record.get("dataset_id"), ID_RE),
                "model_sha": model_sha.lower() if model_sha else None,
                "verified": verified,
                "published": item_id in published,
                "selected_family": selected,
                "selection_basis": "recorded_validation_only" if selected else None,
                "validation": validation,
                "historical_test": historical,
                "recipe": recipe,
                "promoted": None,
            }
        )
    if claimed_promotion:
        ctx.warnings.append("promotion_claim_ignored")
    found.sort(key=lambda item: item["candidate_id"] or "")
    return found


def _pinball_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    found = {}
    for name, item in value.items():
        if not _token(name, SERVICE_RE):
            continue
        metric = item.get("pinball") if isinstance(item, dict) else item
        number = _metric(metric)
        if number is not None:
            found[name] = number
    return found


def _unknown_forecasts(reason: str) -> dict[str, Any]:
    return {
        "status": "unknown",
        "reason": reason,
        "prediction_count": None,
        "outcome_count": None,
        "pending": None,
        "scored": None,
        "censored": None,
        "invalid": None,
        "excluded": None,
        "duplicates_folded": None,
        "independent_groups": None,
        "group_key": "exam_id,model_sha,pool,event_close_s",
        "prediction_count_is_not_independent_group_count": True,
        "groups": None,
        "loss": None,
        "loss_combined": False,
        "by_model": None,
        "by_horizon": None,
        "clock": {"bar_s": None, "bar_source": None},
        "calibration": {"available": False, "reason": "not_published"},
        "descriptive_only": True,
        "historical_metrics_included": False,
        "namespace": FORECAST_NAMESPACE,
    }


def _bar_assignment(path: Path) -> int | None:
    try:
        if path.is_symlink() or path.stat().st_size > 65_536:
            return None
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = BAR_LINE_RE.search(text)
    if match is None:
        return None
    value = int(match.group(1))
    if value <= 0 or value > 86_400:
        return None
    return value


def _exam_clock(ctx: _Ctx, exam: Path) -> dict[str, Any]:
    """Bar length from the exam config, otherwise the pinned runtime constant."""
    config_path = _within(exam, Path("config.json"))
    if config_path is not None and config_path.is_file():
        loaded, error = _load_json(config_path, limit=1_000_000)
        if not error and isinstance(loaded, dict):
            policy = loaded.get("policy")
            raw = policy.get("bar_s") if isinstance(policy, dict) else None
            if raw is None:
                raw = loaded.get("bar_s")
            bar = _int(raw, low=1, high=86_400)
            if bar is not None:
                return {"bar_s": bar, "bar_source": "exam_config"}
    deployment = _within(exam, Path("deployment.json"))
    if deployment is None or not deployment.is_file():
        return {"bar_s": None, "bar_source": None}
    loaded, error = _load_json(deployment, limit=1_000_000)
    if error or not isinstance(loaded, dict):
        return {"bar_s": None, "bar_source": None}
    bundle = _locate(ctx, loaded.get("bundle"), "runtime", None)
    if bundle is None:
        return {"bar_s": None, "bar_source": None}
    source = _within(bundle, Path("runtime/memecoin_brain/source.py"))
    if source is None or not source.is_file():
        return {"bar_s": None, "bar_source": None}
    bar = _bar_assignment(source)
    if bar is None:
        return {"bar_s": None, "bar_source": None}
    _note_input(ctx, source)
    return {"bar_s": bar, "bar_source": "pinned_runtime"}


def _horizon_minutes(bar_s: int | None, horizon: int) -> int | None:
    if bar_s is None or (bar_s * horizon) % 60:
        return None
    return bar_s * horizon // 60


def _shadow_lanes(ctx: _Ctx, exam: Path, exam_id: str) -> list[dict[str, Any]]:
    lanes = []
    root = _within(exam, Path("candidate-shadows"))
    if root is not None and root.is_dir():
        for lane in sorted(root.iterdir(), key=lambda item: item.name):
            if lane.is_symlink():
                ctx.refuse("symlink_artifact", "shadow_lane", exam_id)
                continue
            if not lane.is_dir() or not HASH_RE.fullmatch(lane.name):
                continue
            predictions = _within(lane, Path("shadow/predictions"))
            if predictions is None or not predictions.is_dir():
                continue
            outcomes = _within(lane, Path("shadow/outcomes"))
            lanes.append(
                {
                    "layout": "candidate-shadows",
                    "model_sha": lane.name.lower(),
                    "predictions": predictions,
                    "outcomes": outcomes if outcomes is not None and outcomes.is_dir() else None,
                }
            )
    legacy = _within(exam, Path("shadow/predictions"))
    if legacy is not None and legacy.is_dir():
        outcomes = _within(exam, Path("shadow/outcomes"))
        lanes.append(
            {
                "layout": "legacy-shadow",
                "model_sha": None,
                "predictions": legacy,
                "outcomes": outcomes if outcomes is not None and outcomes.is_dir() else None,
            }
        )
    return lanes


def _blank_counts() -> dict[str, int]:
    return {"predictions": 0, "outcomes": 0, "pending": 0, "scored": 0, "censored": 0, "invalid": 0}


def _named_counts(counts: dict[str, int]) -> dict[str, int]:
    return {
        "prediction_count": counts["predictions"],
        "outcome_count": counts["outcomes"],
        "pending": counts["pending"],
        "scored": counts["scored"],
        "censored": counts["censored"],
        "invalid": counts["invalid"],
    }


def _loss_map(buckets: dict[str, list[str]]) -> dict[str, Any]:
    found = {}
    for name, numbers in sorted(buckets.items()):
        total = sum((Decimal(number) for number in numbers), Decimal(0))
        found[name] = {"n": len(numbers), "pinball_sum": format(total, "f")}
    return found


def _outcome_for(
    ctx: _Ctx,
    exam_id: str,
    directory: Path | None,
    name: str,
    file_sha: str,
    excluded: dict[str, int],
) -> dict[str, Any] | None:
    if directory is None:
        return None
    path = directory / name
    if not path.is_file():
        return None
    if path.is_symlink() or not NAME_RE.fullmatch(path.name):
        ctx.refuse("symlink_artifact", "outcome", exam_id)
        return None
    loaded, error = _load_json(path, decimals=True)
    if error or not isinstance(loaded, dict):
        excluded["unverified_outcome"] += 1
        return None
    claimed = _token(loaded.get("prediction_sha"), HASH_RE)
    if claimed is None or claimed.lower() != file_sha:
        excluded["unverified_outcome"] += 1
        return None
    status = loaded.get("status")
    scored = _int(loaded.get("scored_ns"), low=0)
    observed_ns = _int(loaded.get("observed_ns"), low=0)
    published_ns = scored or observed_ns
    if (
        published_ns is None
        or published_ns > ctx.cutoff
        or status
        not in {
            "scored",
            "censored",
            "invalid",
        }
    ):
        return None
    _note_input(ctx, path)
    metrics = _pinball_map(loaded.get("candidate_metrics")) if status == "scored" else {}
    return {"status": status, "metrics": metrics, "sha256": _sha256(path)}


def _forecasts(ctx: _Ctx) -> dict[str, Any]:
    counts = _blank_counts()
    excluded = {
        "before_activation": 0,
        "after_cutoff": 0,
        "unverified_outcome": 0,
        "model_mismatch": 0,
        "model_unassociated": 0,
    }
    groups: dict[tuple[str, str, str, int], set[str]] = {}
    kept: dict[tuple[str, str, str], dict[str, Any]] = {}
    clocks: list[dict[str, Any]] = []
    saw_lane = False
    saw_gap = False
    duplicates = 0
    for entry in _catalogue(ctx, "exams"):
        record = entry["record"]
        exam_id = entry["id"] or ""
        exam = _locate(ctx, record.get("exam_root"), "exam", exam_id)
        if exam is None or not exam.is_dir():
            saw_gap = True
            continue
        activation = _int(record.get("activation_ns"), low=0)
        activation_path = _within(exam, Path("activation.json"))
        if activation_path is not None and activation_path.is_file():
            loaded, error = _load_json(activation_path, limit=1_000_000)
            if not error and isinstance(loaded, dict):
                _note_input(ctx, activation_path)
                file_activation = _int(loaded.get("at_ns"), low=0) or _int(
                    loaded.get("exam_start_ns"), low=0
                )
                if file_activation is not None:
                    activation = file_activation
        if activation is None:
            ctx.warnings.append("activation_unknown")
            saw_gap = True
            continue
        lanes = _shadow_lanes(ctx, exam, exam_id)
        if not lanes:
            saw_gap = True
            continue
        saw_lane = True
        clocks.append(_exam_clock(ctx, exam))
        for lane in lanes:
            forced = lane["model_sha"]
            for path in sorted(lane["predictions"].glob("*.json")):
                if path.is_symlink() or not NAME_RE.fullmatch(path.name):
                    ctx.refuse("symlink_artifact", "prediction", exam_id)
                    continue
                loaded, error = _load_json(path)
                if error or not isinstance(loaded, dict):
                    ctx.refuse(error or "unreadable", "prediction", exam_id)
                    continue
                model = _token(loaded.get("model_sha"), HASH_RE)
                if forced is not None and (model is None or model.lower() != forced):
                    excluded["model_mismatch"] += 1
                    continue
                if model is None:
                    excluded["model_unassociated"] += 1
                    continue
                model = model.lower()
                predicted = _int(loaded.get("predicted_ns"), low=0)
                if predicted is None:
                    continue
                if predicted > ctx.cutoff:
                    excluded["after_cutoff"] += 1
                    continue
                if predicted < activation:
                    excluded["before_activation"] += 1
                    continue
                file_sha = _sha256(path)
                if file_sha is None:
                    ctx.refuse("unreadable", "prediction", exam_id)
                    continue
                _note_input(ctx, path)
                key = (exam_id, model, file_sha)
                slot = kept.get(key)
                if slot is None:
                    kept[key] = {
                        "exam_id": exam_id,
                        "model_sha": model,
                        "sha256": file_sha,
                        "names": [path.name],
                        "layouts": [lane["layout"]],
                        "outcome_dirs": [lane["outcomes"]],
                        "pool": _token(loaded.get("pool"), POOL_RE),
                        "event_close_s": _int(loaded.get("event_close_s"), low=0),
                        "horizon": _int(loaded.get("horizon"), low=1, high=1024),
                        "status": "pending",
                        "metrics": {},
                    }
                    continue
                duplicates += 1
                if path.name not in slot["names"]:
                    slot["names"].append(path.name)
                if lane["layout"] not in slot["layouts"]:
                    slot["layouts"].append(lane["layout"])
                if lane["outcomes"] not in slot["outcome_dirs"]:
                    slot["outcome_dirs"].append(lane["outcomes"])
    if not saw_lane:
        return _unknown_forecasts("shadow_ledger_missing")
    if saw_gap:
        ctx.warnings.append("forecast_ledger_missing")
    for item in kept.values():
        chosen: dict[str, Any] | None = None
        conflict = False
        for directory in item["outcome_dirs"]:
            for name in item["names"]:
                outcome = _outcome_for(
                    ctx, item["exam_id"], directory, name, item["sha256"], excluded
                )
                if outcome is None:
                    continue
                if chosen is None:
                    chosen = outcome
                    continue
                if chosen["status"] != outcome["status"] or chosen["metrics"] != outcome["metrics"]:
                    conflict = True
        if conflict or chosen is None:
            if conflict:
                excluded["unverified_outcome"] += 1
            item["status"] = "pending"
            item["metrics"] = {}
        else:
            item["status"] = chosen["status"]
            item["metrics"] = chosen["metrics"]
        status = item["status"]
        counts["predictions"] += 1
        if status in counts:
            counts[status] += 1
        if status in {"scored", "censored", "invalid"}:
            counts["outcomes"] += 1
        pool = item["pool"]
        close_s = item["event_close_s"]
        if pool is not None and close_s is not None:
            groups.setdefault((item["exam_id"], item["model_sha"], pool, close_s), set()).add(
                status
            )
    by_model: dict[tuple[str, str], dict[str, Any]] = {}
    for item in kept.values():
        slot = by_model.setdefault(
            (item["exam_id"], item["model_sha"]),
            {"counts": _blank_counts(), "horizons": {}},
        )
        slot["counts"]["predictions"] += 1
        status = item["status"]
        if status in slot["counts"]:
            slot["counts"][status] += 1
        if status in {"scored", "censored", "invalid"}:
            slot["counts"]["outcomes"] += 1
        horizon = item["horizon"]
        if horizon is None:
            continue
        horizon_slot = slot["horizons"].setdefault(horizon, {"counts": _blank_counts(), "loss": {}})
        horizon_slot["counts"]["predictions"] += 1
        if status in horizon_slot["counts"]:
            horizon_slot["counts"][status] += 1
        if status in {"scored", "censored", "invalid"}:
            horizon_slot["counts"]["outcomes"] += 1
        if status == "scored":
            for name, number in item["metrics"].items():
                horizon_slot["loss"].setdefault(name, []).append(number)
    bar_values = {clock["bar_s"] for clock in clocks if clock.get("bar_s") is not None}
    if len(bar_values) == 1:
        bar_s = next(iter(bar_values))
        bar_source = next(clock["bar_source"] for clock in clocks if clock.get("bar_s") == bar_s)
    else:
        bar_s = None
        bar_source = None
    model_rows = []
    horizon_rows: dict[int, dict[str, Any]] = {}
    for (exam_id, model_sha), slot in sorted(by_model.items()):
        horizons = {}
        for horizon, horizon_slot in sorted(slot["horizons"].items()):
            minutes = _horizon_minutes(bar_s, horizon)
            loss = _loss_map(horizon_slot["loss"])
            horizons[str(horizon)] = {
                "horizon": horizon,
                "minutes": minutes,
                **_named_counts(horizon_slot["counts"]),
                "loss": loss,
            }
            rolled = horizon_rows.setdefault(
                horizon,
                {
                    "horizon": horizon,
                    "minutes": minutes,
                    **_named_counts(_blank_counts()),
                    "loss_by_model": [],
                },
            )
            named = _named_counts(horizon_slot["counts"])
            for field in (
                "prediction_count",
                "outcome_count",
                "pending",
                "scored",
                "censored",
                "invalid",
            ):
                rolled[field] += named[field]
            rolled["loss_by_model"].append(
                {"exam_id": exam_id, "model_sha": model_sha, "loss": loss}
            )
        model_rows.append(
            {
                "exam_id": exam_id,
                "model_sha": model_sha,
                **_named_counts(slot["counts"]),
                "by_horizon": horizons,
            }
        )
    fully_scored = 0
    any_pending = 0
    any_censored = 0
    for statuses in groups.values():
        if statuses == {"scored"}:
            fully_scored += 1
        if "pending" in statuses:
            any_pending += 1
        if "censored" in statuses:
            any_censored += 1
    combined: dict[str, list[str]] = {}
    if len(model_rows) == 1:
        for horizon_slot in by_model[next(iter(by_model))]["horizons"].values():
            for name, numbers in horizon_slot["loss"].items():
                combined.setdefault(name, []).extend(numbers)
    return {
        "status": "measured",
        "reason": None,
        "prediction_count": counts["predictions"],
        "outcome_count": counts["outcomes"],
        "pending": counts["pending"],
        "scored": counts["scored"],
        "censored": counts["censored"],
        "invalid": counts["invalid"],
        "excluded": excluded,
        "duplicates_folded": duplicates,
        "independent_groups": len(groups),
        "group_key": "exam_id,model_sha,pool,event_close_s",
        "prediction_count_is_not_independent_group_count": True,
        "groups": {
            "independent": len(groups),
            "fully_scored": fully_scored,
            "any_pending": any_pending,
            "any_censored": any_censored,
        },
        "loss": _loss_map(combined) if len(model_rows) == 1 else None,
        "loss_combined": len(model_rows) == 1,
        "by_model": model_rows,
        "by_horizon": {str(horizon): horizon_rows[horizon] for horizon in sorted(horizon_rows)},
        "clock": {"bar_s": bar_s, "bar_source": bar_source},
        "calibration": {"available": False, "reason": "not_published"},
        "descriptive_only": True,
        "historical_metrics_included": False,
        "namespace": FORECAST_NAMESPACE,
    }


def _money(value: Any) -> str | None:
    if isinstance(value, str) and METRIC_RE.fullmatch(value):
        return value
    return _metric(value)


def _sum_money(values: list[str]) -> str | None:
    if not values:
        return None
    return format(sum((Decimal(item) for item in values), Decimal(0)), "f")


def _decision_stage(reason: Any) -> str:
    if reason == "enter_next_observed_close":
        return "intent"
    if reason == "already_exposed":
        return "existing_or_pending_exposure"
    return "decision"


def _paper(ctx: _Ctx) -> dict[str, Any]:
    books = []
    actions = {"WAIT": 0, "ENTER": 0, "HOLD": 0, "EXIT": 0, "unclassified": 0}
    stages = {"intent": 0, "existing_or_pending_exposure": 0, "decision": 0}
    fills = {"entries": 0, "exits": 0}
    source = "absent"
    generated = []
    stale_reasons: list[str] = []
    for entry in _catalogue(ctx, "exams"):
        exam_id = entry["id"]
        exam = _locate(ctx, entry["record"].get("exam_root"), "exam", exam_id)
        if exam is None:
            continue
        dashboard_path = _within(exam, Path("dashboard.json"))
        if dashboard_path is None or not dashboard_path.is_file():
            stale_reasons.append("dashboard_missing")
            continue
        loaded, error = _load_json(dashboard_path)
        if error or not isinstance(loaded, dict):
            ctx.warnings.append("dashboard_unreadable")
            continue
        _note_input(ctx, dashboard_path)
        generated_ns = _int(loaded.get("generated_ns"), low=0)
        if generated_ns is None or generated_ns > ctx.cutoff:
            stale_reasons.append("dashboard_missing")
            continue
        generated.append(generated_ns)
        if ctx.cutoff - generated_ns > STALE_DASHBOARD_NS:
            stale_reasons.append("dashboard_age")
        raw_behavior = loaded.get("model_behavior")
        behavior = raw_behavior if isinstance(raw_behavior, dict) else None
        if behavior:
            source = "model_behavior"
            for item in behavior.values():
                if not isinstance(item, dict):
                    continue
                reasons = item.get("reasons") if isinstance(item.get("reasons"), dict) else {}
                for reason, count in reasons.items():
                    number = _int(count, low=0)
                    if number is None:
                        continue
                    mapped = map_action(reason)
                    actions[mapped or "unclassified"] += number
                    stages[_decision_stage(reason)] += number
                entries = _int(item.get("entries"), low=0)
                exits = _int(item.get("exits"), low=0)
                fills["entries"] += entries or 0
                fills["exits"] += exits or 0
        elif source != "model_behavior":
            source = "dashboard_signals_truncated"
        for book in loaded.get("books") or []:
            if not isinstance(book, dict):
                continue
            if book.get("mark_stale") is True:
                stale_reasons.append("mark_stale")
            positions = book.get("positions") if isinstance(book.get("positions"), list) else []
            pending = book.get("pending") if isinstance(book.get("pending"), list) else []
            closed = book.get("closed") if isinstance(book.get("closed"), list) else []
            if source == "dashboard_signals_truncated":
                for signal in book.get("signals") or []:
                    if not isinstance(signal, dict):
                        continue
                    mapped = map_action(signal.get("reason") or signal.get("action"))
                    actions[mapped or "unclassified"] += 1
                    stages[_decision_stage(signal.get("reason"))] += 1
            books.append(
                {
                    "exam_id": exam_id,
                    "candidate": book.get("candidate")
                    if book.get("candidate") in FAMILIES
                    else None,
                    "model_sha": _token(book.get("model_sha"), HASH_RE),
                    "cash": _money(book.get("cash")),
                    "equity": _money(book.get("equity")),
                    "realized": _money(book.get("realized")),
                    "fees": _money(book.get("fees")),
                    "mark_stale": book.get("mark_stale") is True,
                    "open_positions": len(positions),
                    "pending": len(pending),
                    "closed_trades": len(closed),
                }
            )
    realized_values = [book["realized"] for book in books if book["realized"] is not None]
    equity_values = [book["equity"] for book in books if book["equity"] is not None]
    unique_reasons = []
    for reason in stale_reasons:
        if reason not in unique_reasons:
            unique_reasons.append(reason)
    return {
        "stale": bool(unique_reasons),
        "stale_reasons": unique_reasons,
        "generated_ns": max(generated) if generated else None,
        "stale_after_s": 120,
        "recomputed_fills": False,
        "actions_source": source,
        "actions": actions,
        "stages": stages,
        "intent_counted_as_fill": False,
        "fills": fills,
        "books": books,
        "realized_total": _sum_money(realized_values)
        if books and len(realized_values) == len(books)
        else None,
        "mtm_equity_total": _sum_money(equity_values)
        if books and len(equity_values) == len(books)
        else None,
    }


def _health_file(ctx: _Ctx, directory: Path, name: str, label: str) -> dict[str, Any] | None:
    path = _within(directory, Path(name))
    if path is None or not path.is_file() or path.is_symlink():
        return None
    digest = _sha256(path)
    if digest:
        ctx.bound.append({"label": label, "sha256": digest})
    loaded, error = _load_json(path, limit=1_000_000)
    if error or not isinstance(loaded, dict):
        return {"unreadable": True}
    return loaded


def _providers(ctx: _Ctx, events: list[dict[str, Any]]) -> dict[str, Any]:
    health = None
    stopped = None
    stop_reason = None
    source = _bound_dir(ctx, ctx.config.get("source_root"), "source") if ctx.config else None
    if (ctx.root / "successor-ready.json").exists():
        from .acquisition import current_source

        try:
            source = _bound_dir(ctx, str(current_source(ctx.root)), "current_source")
        except (OSError, ValueError):
            source = None
            ctx.warnings.append("current_source_unverified")
    if source is not None:
        health = _health_file(ctx, source, "health.json", "source_health")
        stop_path = _within(source, Path("STOP"))
        if stop_path is not None and stop_path.is_file():
            loaded, error = _load_json(stop_path, limit=100_000)
            at_ns = _int(loaded.get("at_ns"), low=0) if isinstance(loaded, dict) else None
            if isinstance(loaded, dict) and (at_ns is None or at_ns <= ctx.cutoff):
                stopped = True
                stop_reason = _token(loaded.get("reason"), REASON_RE)
            elif error:
                stopped = True
                stop_reason = "stop_marker"
    failures = None
    at_ns = None
    cooldown = None
    attempts = None
    if isinstance(health, dict) and not health.get("unreadable"):
        at_ns = _int(health.get("at_ns"), low=0)
        if at_ns is not None and at_ns <= ctx.cutoff:
            failures = _int(health.get("failures"), low=0)
            cooldown = _int(health.get("cooldown_ns"), low=0)
            attempts = _int(health.get("attempts"), low=0)
        else:
            at_ns = None
    provider_reasons = {
        "transport",
        "rate_limit",
        "http_429",
        "cooldown",
        "provider_failure",
        "timeout",
    }
    provider_events = [
        {"ns": row["ns"], "reason": row["reason"]}
        for row in events
        if row["reason"] in provider_reasons
    ]
    history = []
    for point in ctx.telemetry:
        source = point.get("source") if isinstance(point.get("source"), dict) else None
        if not source:
            continue
        history.append({"ns": point["ns"], **source})
    recovery = []
    previous = None
    for point in history:
        if point.get("stopped") is True:
            current = "stopped"
        elif point.get("healthy") is False:
            current = "unhealthy"
        elif point.get("healthy") is True:
            current = "healthy"
        else:
            current = "unknown"
        if previous in {"stopped", "unhealthy"} and current == "healthy":
            recovery.append({"ns": point["ns"], "from": previous, "to": "healthy"})
        previous = current
    from .source_history import snapshot as source_lineage

    return {
        "lineage": source_lineage(ctx.root, ctx.cutoff),
        "consecutive_failures": failures,
        "health_at_ns": at_ns,
        "attempts": attempts,
        "cooldown_until_ns": cooldown,
        "stopped": stopped,
        "stop_reason": stop_reason,
        "events": provider_events,
        "history": history,
        "recovery": recovery,
    }


def _legacy(ctx: _Ctx) -> list[dict[str, Any]]:
    rows = []
    entries = ctx.config.get("legacy_papers")
    if not isinstance(entries, list):
        return rows
    for entry in entries[:20]:
        if not isinstance(entry, dict):
            ctx.refuse("bad_bound_path", "legacy", None)
            continue
        label = _token(entry.get("label"), LABEL_RE)
        directory = _bound_dir(ctx, entry.get("root"), "legacy")
        if directory is None:
            rows.append(
                {
                    "label": label,
                    "port": _int(entry.get("port"), low=1, high=65535),
                    "status": "refused",
                }
            )
            continue
        health = _health_file(ctx, directory, "health.json", f"legacy:{label or 'paper'}")
        paper_label = f"legacy-paper:{label or 'paper'}"
        paper = _health_file(ctx, directory, "paper-health.json", paper_label)
        stop = _within(directory, Path("STOP"))
        stopped = bool(stop is not None and stop.is_file() and not stop.is_symlink())
        at_ns = _int(health.get("at_ns"), low=0) if isinstance(health, dict) else None
        age = None if at_ns is None or at_ns > ctx.cutoff else (ctx.cutoff - at_ns) // NS
        status = _token(paper.get("status"), SERVICE_RE) if isinstance(paper, dict) else None
        healthy = None
        if at_ns is not None and at_ns <= ctx.cutoff:
            healthy = (
                not stopped
                and age is not None
                and age <= 120
                and status
                not in {
                    "failed",
                    "recovery_cooldown",
                }
            )
        rows.append(
            {
                "label": label,
                "port": _int(entry.get("port"), low=1, high=65535),
                "status": "ok",
                "stopped": stopped,
                "heartbeat_age_s": age,
                "supervisor_status": status,
                "healthy": healthy,
            }
        )
    return rows


def _runtime_manifest(ctx: _Ctx) -> None:
    path = ctx.root / "deployment.json"
    if not path.is_file() or path.is_symlink():
        return
    _note_input(ctx, path)
    loaded, error = _load_json(path, limit=1_000_000)
    if error or not isinstance(loaded, dict):
        ctx.warnings.append("deployment_unreadable")
        return
    bundle = _locate(ctx, loaded.get("bundle"), "runtime", None)
    if bundle is None or not bundle.is_dir():
        return
    manifest = _within(bundle, Path("manifest.json"))
    if manifest is None or not manifest.is_file():
        ctx.refuse("unpinned_artifact", "runtime", None)
        return
    actual = _sha256(manifest)
    claimed = _token(loaded.get("pin"), HASH_RE)
    if actual is None or claimed is None or actual != claimed.lower():
        ctx.refuse("tampered_manifest", "runtime", None)
        return
    ctx.runtime_manifest_sha = actual
    _note_input(ctx, manifest)


def _history_points(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points = []
    for row in events:
        if row["kind"] != "history":
            continue
        summary = row["summary"]
        ns = _int(summary.get("ns"), low=0) or row["ns"]
        if ns is None or ns > 10**19:
            continue
        points.append(
            {
                "ns": ns,
                "samples": summary.get("samples"),
                "bars": summary.get("bars"),
                "pools": summary.get("pools"),
                "event_days": summary.get("event_days"),
            }
        )
    points.sort(key=lambda item: item["ns"])
    return points


def _telemetry_coverage(ctx: _Ctx) -> list[dict[str, Any]]:
    rows = []
    for point in ctx.telemetry:
        coverage = point.get("coverage") if isinstance(point.get("coverage"), dict) else None
        if not coverage:
            continue
        rows.append({"ns": point["ns"], **coverage})
    return rows


def _growth(points: list[dict[str, Any]], snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    def series(key: str) -> list[tuple[int, int]]:
        rows = []
        for point in points:
            value = point.get(key)
            if type(value) is int:
                rows.append((int(point["ns"]), value))
        return rows

    bars = series("bars")
    pools = series("pools")
    days = series("event_days")
    if len(pools) < 2:
        for snap in snapshots:
            retained = snap.get("retained") or {}
            train = retained.get("train") or {}
            if snap.get("created_ns") is not None and type(train.get("pools")) is int:
                pools.append((snap["created_ns"], train["pools"]))
                if type(train.get("days")) is int:
                    days.append((snap["created_ns"], train["days"]))

    def delta(rows: list[tuple[int, int]]) -> int | None:
        if len(rows) < 2:
            return None
        ordered = sorted(rows)
        return ordered[-1][1] - ordered[0][1]

    bars_delta = delta(bars)
    pools_delta = delta(pools)
    days_delta = delta(days)
    measured = bars_delta is not None or pools_delta is not None or days_delta is not None
    return {
        "status": "measured" if measured else "unknown",
        "bars_first": sorted(bars)[0][1] if len(bars) >= 2 else None,
        "bars_last": sorted(bars)[-1][1] if len(bars) >= 2 else None,
        "bars_delta": bars_delta,
        "pools_first": sorted(pools)[0][1] if len(pools) >= 2 else None,
        "pools_last": sorted(pools)[-1][1] if len(pools) >= 2 else None,
        "pools_delta": pools_delta,
        "event_days_delta": days_delta,
    }


def _gates(
    bounds: dict[str, Any],
    revisions: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    cutoff: int,
) -> list[dict[str, Any]]:
    latest = revisions[-1] if revisions else None
    mode = latest["mode"] if latest else None
    trains = []
    for job in jobs:
        reserved = job["reserved_ns"]
        if job["action"] == "train" and reserved is not None and reserved <= cutoff:
            trains.append(job)
    expires = bounds["expires_ns"]
    expiry_pass = None if expires is None else cutoff < expires
    mode_pass = None if mode is None else mode not in {"paused", "stopped"}
    runs_max = bounds["max_training_runs"]
    quota_pass = None if runs_max is None else len(trains) < runs_max
    interval_s = bounds["min_refit_interval_s"]
    last = max((job["reserved_ns"] for job in trains), default=None)
    if not trains or interval_s is None or last is None:
        interval_pass = True if trains == [] else None if interval_s is None else True
    else:
        interval_pass = cutoff - last >= interval_s * NS
    domain_reason = latest and (
        latest["reason"] == "domain_stop" or latest["auto_reason"] == "domain_stop"
    )
    domain = bool(domain_reason)
    return [
        {"id": "expiry", "pass": expiry_pass, "expires_ns": expires},
        {"id": "operator_mode", "pass": mode_pass, "mode": mode},
        {"id": "quota", "pass": quota_pass, "runs_used": len(trains), "runs_max": runs_max},
        {"id": "interval", "pass": interval_pass, "last_reservation_ns": last},
        {"id": "domain_stop", "pass": not domain},
        {"id": "promotion", "pass": True, "value": None},
    ]


def _services(jobs: list[dict[str, Any]], revisions: list[dict[str, Any]]) -> dict[str, Any]:
    crashes = []
    for job in jobs:
        if job["status"] not in {"interrupted", "failed"}:
            continue
        later = any(
            other["action"] == job["action"]
            and other["status"] == "completed"
            and (other["finished_ns"] or 0) > (job["finished_ns"] or job["created_ns"] or 0)
            for other in jobs
        )
        started = job["created_ns"] or 0
        resumed = any(row["reason"] == "resumed" and row["ns"] >= started for row in revisions)
        crashes.append(
            {
                "ns": job["finished_ns"] or job["created_ns"],
                "job_id": job["job_id"],
                "action": job["action"],
                "status": job["status"],
                "reason": job["reason"],
                "candidate_id": job["candidate_id"],
                "recovered": later or resumed,
            }
        )
    transitions = []
    previous = None
    for row in revisions:
        if previous is None or previous["mode"] != row["mode"] or previous["phase"] != row["phase"]:
            transitions.append(
                {
                    "revision": row["revision"],
                    "ns": row["ns"],
                    "from_mode": None if previous is None else previous["mode"],
                    "mode": row["mode"],
                    "phase": row["phase"],
                    "reason": row["reason"],
                }
            )
        previous = row
    bottlenecks = []
    for row in revisions:
        if row["reason"] in BOTTLENECKS or row["auto_reason"] in BOTTLENECKS:
            bottlenecks.append(
                {
                    "ns": row["ns"],
                    "reason": row["auto_reason"] or row["reason"],
                    "source": "revision",
                }
            )
    for job in jobs:
        if job["reason"] in BOTTLENECKS or job["status"] in {"interrupted", "failed"}:
            bottlenecks.append(
                {
                    "ns": job["finished_ns"] or job["created_ns"],
                    "reason": job["reason"] or job["status"],
                    "source": "job",
                    "job_id": job["job_id"],
                }
            )
    return {"crashes": crashes, "transitions": transitions, "bottlenecks": bottlenecks}


def _progress(ctx: _Ctx) -> list[dict[str, Any]]:
    directory = ctx.root / "progress"
    if directory.is_symlink() or not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json")):
        if path.is_symlink() or not NAME_RE.fullmatch(path.name):
            continue
        loaded, error = _load_json(path, limit=1_000_000)
        if error or not isinstance(loaded, dict):
            continue
        at_ns = _int(loaded.get("at_ns"), low=0)
        if at_ns is not None and at_ns > ctx.cutoff:
            continue
        _note_input(ctx, path)
        rows.append(
            {
                "id": _token(path.stem, ID_RE),
                "status": _token(loaded.get("status"), SERVICE_RE),
                "fit": _token(loaded.get("fit"), VERSION_RE),
                "completed_fits": _int(loaded.get("completed_fits"), low=0),
                "total_fits": _int(loaded.get("total_fits"), low=0),
                "at_ns": at_ns,
                "selected": loaded.get("selected") if loaded.get("selected") in FAMILIES else None,
            }
        )
    return rows


def _incidents(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [row for row in events if row["kind"] == "incident"]
    rows.sort(key=lambda item: item["ns"] or 0, reverse=True)
    return [
        {
            "event_id": row["event_id"],
            "ns": row["ns"],
            "reason": row["reason"],
            "summary": row["summary"],
        }
        for row in rows[:50]
    ]


def _read_first(ctx: _Ctx, report: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def add(severity: str, code: str, ns: int | None, text: str) -> None:
        items.append({"severity": severity, "code": code, "ns": ns, "text": text})

    if ctx.reason and not report["ok"]:
        add("high", ctx.reason, None, f"Журнал не прочитан: {ctx.reason}.")
    for refused in ctx.refused:
        add(
            "high",
            str(refused["code"]),
            None,
            f"Артефакт {refused['kind']} {refused['id'] or '—'} отклонён: {refused['code']}.",
        )
    bounds = report["bounds"]
    if bounds.get("real_execution_enabled") is True:
        add("high", "real_execution", None, "В config включён real_execution_enabled.")
    paid = bounds.get("paid_budget_usd")
    if type(paid) is int and paid != 0:
        add("high", "paid_budget", None, f"paid_budget_usd={paid}.")
    if "promotion_claim_ignored" in ctx.warnings:
        add(
            "high",
            "promotion_claim_ignored",
            None,
            "Запись promoted=true проигнорирована. Promotion none.",
        )
    for crash in report["services"]["crashes"]:
        candidate = crash["candidate_id"] or "кандидат не опубликован"
        add(
            "high",
            "service_crash",
            crash["ns"],
            f"{crash['action']} {crash['job_id']} завершился {crash['status']}"
            f" ({crash['reason'] or 'без причины'}); {candidate}.",
        )
    providers = report["providers"]
    if providers.get("stopped"):
        add(
            "high",
            "source_stop",
            providers.get("health_at_ns"),
            f"Источник остановлен: {providers.get('stop_reason') or 'stop_marker'}.",
        )
    failures = providers.get("consecutive_failures")
    if type(failures) is int and failures > 0:
        add(
            "medium",
            "provider_failures",
            providers.get("health_at_ns"),
            f"Текущий счётчик consecutive failures источника: {failures}.",
        )
    for event in providers.get("events") or []:
        add("medium", "provider_event", event["ns"], f"Событие провайдера {event['reason']}.")
    for incident in report["incidents"]:
        add("high", "incident", incident["ns"], f"Инцидент {incident['reason']}.")
    for gate in report["gates"]:
        if gate["pass"] is not False or gate["id"] == "promotion":
            continue
        if gate["id"] == "quota":
            text = f"Training reservations {gate.get('runs_used')}/{gate.get('runs_max')}."
        elif gate["id"] == "operator_mode":
            text = f"Режим оператора {gate.get('mode')}: новые циклы остановлены."
        elif gate["id"] == "expiry":
            text = "Срок rollout на cutoff уже наступил."
        elif gate["id"] == "interval":
            text = "Интервал до следующего refit на cutoff ещё не прошёл."
        else:
            text = f"Gate {gate['id']} не проходит."
        add("medium", f"gate_{gate['id']}", None, text)
    observed = report["observed"]
    if observed["status"] == "unknown":
        add(
            "medium",
            "observed_unknown",
            None,
            "Наблюдаемые часы неизвестны: запись heartbeat отсутствует. Это неизмеренное значение.",
        )
    gaps = sorted(observed.get("gaps") or [], key=lambda item: int(item["gap_ns"]), reverse=True)
    gaps = gaps[:5]
    for gap in gaps:
        add(
            "medium",
            "heartbeat_gap",
            gap["start_ns"],
            (
                f"Разрыв {gap['service']} {gap['gap_ns'] // NS} с. "
                "Интервал длиннее 90 с в observed не входит."
            ),
        )
    if observed.get("unhealthy_heartbeats"):
        add(
            "medium",
            "unhealthy_heartbeat",
            None,
            f"Heartbeat healthy=0: {observed['unhealthy_heartbeats']}.",
        )
    paper = report["paper"]
    if paper.get("stale"):
        add(
            "medium",
            "stale_paper",
            paper.get("generated_ns"),
            "Paper PnL несёт stale mark: " + ", ".join(paper["stale_reasons"]) + ".",
        )
    for legacy in report["legacy"]:
        if legacy.get("status") == "refused":
            add("high", "legacy_refused", None, f"Legacy {legacy.get('label') or '—'} не прочитан.")
        elif legacy.get("stopped") or legacy.get("healthy") is False:
            add(
                "medium",
                "legacy_health",
                None,
                (
                    f"Legacy {legacy.get('label') or '—'} healthy={legacy.get('healthy')} "
                    f"stopped={legacy.get('stopped')}."
                ),
            )
    growth = report["coverage"]["growth"]
    if growth["status"] == "unknown" and report["ok"]:
        add("low", "coverage_unknown", None, "Рост рынков и суток по журналу не измерен.")
    elif type(growth.get("bars_delta")) is int and growth["bars_delta"] < 0:
        shrunk = abs(growth["bars_delta"])
        add("medium", "coverage_shrink", None, f"Число bars в истории снизилось на {shrunk}.")
    for snap in report["snapshots"]:
        unseen = snap.get("unseen_test_pools") or []
        if unseen:
            add(
                "low",
                "unseen_pools",
                snap.get("created_ns"),
                (
                    f"Исторический test snapshot {snap['dataset_id']} "
                    f"содержит {len(unseen)} пулов вне train."
                ),
            )
        if snap.get("verified") and snap.get("historical_test_independent") is False:
            add(
                "low",
                "historical_test_descriptive",
                snap.get("created_ns"),
                f"Test snapshot {snap['dataset_id']} помечен reused/descriptive.",
            )
    for model in report["models"]:
        historical = model.get("historical_test")
        if historical and historical.get("claimed_independent"):
            add(
                "medium",
                "historical_claim",
                None,
                f"Карточка {model['candidate_id']} заявляет independent historical test. "
                "В отчёте он остаётся reused/descriptive.",
            )
    for row in report["training"]["progress"]:
        running = any(job["status"] == "running" for job in report["jobs"])
        if row.get("status") == "running" and not running:
            add(
                "medium",
                "stale_training_progress",
                row.get("at_ns"),
                f"Progress {row.get('id') or '—'} всё ещё running, активного job нет.",
            )
    deduped = []
    seen = set()
    for item in items:
        key = (item["code"], item["text"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    severity = {"high": 0, "medium": 1, "low": 2}
    deduped.sort(key=lambda item: (severity[item["severity"]], -(item["ns"] or 0), item["code"]))
    if len(deduped) > READ_FIRST_CAP:
        ctx.warnings.append("read_first_truncated")
        deduped = deduped[:READ_FIRST_CAP]
    return deduped


def _observations(ctx: _Ctx) -> list[dict[str, Any]]:
    if not ctx.schema_ok:
        return []
    rows: list[dict[str, Any]] = []

    def add(ns: int | None, kind: str, origin: str, item_id: str | None, **fields: Any) -> None:
        if ns is None or ns > ctx.cutoff:
            return
        item = {
            "ns": ns,
            "kind": kind,
            "origin": origin,
            "id": item_id,
            "samples": None,
            "bars": None,
            "pools": None,
            "event_days": None,
        }
        item.update(fields)
        rows.append(item)

    kind_for = {
        "source": "source",
        "operator": "operator",
        "paper": "paper",
        "exam": "paper",
        "recorded": "operator",
        "unclassified": "operator",
    }
    for beat in ctx.heartbeats:
        add(
            beat["at_ns"],
            kind_for.get(str(beat["service"]), "operator"),
            "heartbeat",
            beat.get("exam_id"),
            service=beat["service"],
        )
    for event in _events(ctx):
        if event["kind"] != "history":
            continue
        summary = event["summary"]
        add(
            summary.get("ns") or event["ns"],
            "corpus",
            "event",
            None,
            samples=summary.get("samples"),
            bars=summary.get("bars"),
            pools=summary.get("pools"),
            event_days=summary.get("event_days"),
        )
    for point in ctx.telemetry:
        coverage = point.get("coverage") if isinstance(point.get("coverage"), dict) else {}
        add(
            point.get("ns"),
            "corpus",
            "telemetry",
            None,
            samples=coverage.get("matured_samples"),
            bars=coverage.get("bars"),
            pools=coverage.get("pools"),
            event_days=coverage.get("event_days"),
        )
    action_kind = {"train": "training", "snapshot": "corpus", "paper": "paper", "collect": "source"}
    for job in _jobs(ctx):
        add(
            job["finished_ns"] or job["created_ns"],
            action_kind.get(job["action"] or "", "operator"),
            "job",
            job["job_id"],
            status=job["status"],
        )
    publication_kind = {
        "dataset": "corpus",
        "candidate": "training",
        "exam": "paper",
        "collect": "source",
    }
    for row in _publications(ctx):
        kind = publication_kind.get(row["kind"] or "", "operator")
        add(row["ns"], kind, "publication", row["public_id"])
    rows.sort(key=lambda item: (item["ns"], item["kind"], item["origin"], item["id"] or ""))
    return rows


def _empty_sections() -> dict[str, Any]:
    return {
        "operator": {},
        "observed": _observed(_Ctx(Path("."), 0)),
        "providers": {
            "consecutive_failures": None,
            "health_at_ns": None,
            "attempts": None,
            "cooldown_until_ns": None,
            "stopped": None,
            "stop_reason": None,
            "events": [],
            "history": [],
            "recovery": [],
        },
        "services": {"crashes": [], "transitions": [], "bottlenecks": []},
        "coverage": {"points": [], "telemetry": [], "growth": {"status": "unknown"}},
        "snapshots": [],
        "models": [],
        "forecasts": _unknown_forecasts("not_read"),
        "paper": {
            "stale": False,
            "stale_reasons": [],
            "generated_ns": None,
            "stale_after_s": 120,
            "recomputed_fills": False,
            "actions_source": "absent",
            "actions": {"WAIT": 0, "ENTER": 0, "HOLD": 0, "EXIT": 0, "unclassified": 0},
            "stages": {"intent": 0, "existing_or_pending_exposure": 0, "decision": 0},
            "intent_counted_as_fill": False,
            "fills": {"entries": 0, "exits": 0},
            "books": [],
            "realized_total": None,
            "mtm_equity_total": None,
        },
        "auto": {"transitions": [], "bottlenecks": []},
        "incidents": [],
        "legacy": [],
        "gates": [],
        "jobs": [],
        "training": {"published_candidates": 0, "interrupted_without_candidate": 0, "progress": []},
    }


def _assemble(ctx: _Ctx) -> dict[str, Any]:
    base = _empty_sections()
    base["observed"] = _observed(ctx)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "ok": ctx.schema_ok and ctx.reason is None,
        "reason": ctx.reason,
        "cutoff_ns": ctx.cutoff,
        "weights_read": False,
        "fills_recomputed": False,
        "promotion": None,
        "promotions": [],
        "historical_test_label": "reused_descriptive",
        "future_includes_historical_test": False,
        "documents": list(DOCUMENTS),
        "bounds": _bounds(ctx.config),
        "warnings": list(ctx.warnings),
        "refused": list(ctx.refused),
        **base,
    }
    if not ctx.schema_ok:
        report["legacy"] = _legacy(ctx) if ctx.config else []
        report["hashes"] = _hashes(ctx)
        report["read_first"] = _read_first(ctx, report)
        report["warnings"] = list(ctx.warnings)
        report["refused"] = list(ctx.refused)
        report["integrity_ok"] = not ctx.refused
        return report
    revisions = _revisions(ctx)
    jobs = _jobs(ctx)
    events = _events(ctx)
    publications = _publications(ctx)
    latest = revisions[-1] if revisions else None
    _runtime_manifest(ctx)
    snapshots = _snapshots(ctx)
    models = _models(ctx, publications)
    points = _history_points(events)
    telemetry_coverage = _telemetry_coverage(ctx)
    services = _services(jobs, revisions)
    progress = _progress(ctx)
    interrupted = [
        job
        for job in jobs
        if job["action"] == "train"
        and job["status"] in {"interrupted", "failed"}
        and not job["candidate_id"]
        and not job["published"]
    ]
    report.update(
        {
            "operator": {
                "revision": None if latest is None else latest["revision"],
                "mode": None if latest is None else latest["mode"],
                "phase": None if latest is None else latest["phase"],
                "reason": None if latest is None else latest["reason"],
                "auto_reason": None if latest is None else latest["auto_reason"],
                "expect": None if latest is None else latest["expect"],
                "selected_candidate_id": None
                if latest is None
                else latest["selected_candidate_id"],
                "active_exam_id": None if latest is None else latest["active_exam_id"],
            },
            "providers": _providers(ctx, events),
            "services": services,
            "auto": {
                "transitions": services["transitions"],
                "bottlenecks": services["bottlenecks"],
            },
            "coverage": {
                "points": points,
                "telemetry": telemetry_coverage,
                "growth": _growth(points + telemetry_coverage, snapshots),
            },
            "snapshots": snapshots,
            "models": models,
            "forecasts": _forecasts(ctx),
            "paper": _paper(ctx),
            "incidents": _incidents(events),
            "legacy": _legacy(ctx),
            "gates": _gates(report["bounds"], revisions, jobs, ctx.cutoff),
            "jobs": [
                {
                    "job_id": job["job_id"],
                    "action": job["action"],
                    "status": job["status"],
                    "reason": job["reason"],
                    "created_ns": job["created_ns"],
                    "finished_ns": job["finished_ns"],
                    "candidate_id": job["candidate_id"],
                    "published": job["published"],
                }
                for job in jobs
            ],
            "training": {
                "published_candidates": sum(1 for model in models if model["published"]),
                "interrupted_without_candidate": len(interrupted),
                "progress": progress,
            },
        }
    )
    report["hashes"] = _hashes(ctx)
    report["read_first"] = _read_first(ctx, report)
    report["warnings"] = list(dict.fromkeys(ctx.warnings))
    report["refused"] = list(ctx.refused)
    report["integrity_ok"] = not ctx.refused
    return report


def _hashes(ctx: _Ctx) -> dict[str, Any]:
    ctx.inputs.sort(key=lambda item: item["path"])
    ctx.bound.sort(key=lambda item: (item["label"], item["sha256"]))
    body = {"files": ctx.inputs, "bound_health": ctx.bound, "journal": ctx.journal_sha}
    return {
        "config_sha256": ctx.config_sha,
        "operator_runtime_manifest_sha256": ctx.runtime_manifest_sha,
        "operator_journal_sha256": ctx.journal_sha,
        "inputs_sha256": _digest(body),
        "inputs": ctx.inputs,
        "bound_health": ctx.bound,
    }


def build_report(root: Path, now_ns: int | None = None) -> dict[str, Any]:
    """Deterministic evidence at cutoff. Does not write the operator root."""
    ctx = _open(root, now_ns)
    try:
        return _assemble(ctx)
    finally:
        ctx.close()


def history_series(root: Path, limit: int = 500) -> list[dict[str, Any]]:
    """Timestamped source, corpus, training, and paper observations. No writer."""
    if type(limit) is not int or limit <= 0:
        return []
    ctx = _open(root, None)
    try:
        rows = _observations(ctx)
    finally:
        ctx.close()
    return rows[-min(limit, SERIES_CAP) :]


def exclusive_output(root: Path, output: Path) -> str | None:
    """Refuse an output that already exists, is a symlink, or lands in a ledger."""
    if output.exists() or output.is_symlink():
        return "output_exists"
    parent = output.parent
    if not parent.is_dir() or parent.is_symlink():
        return "output_parent"
    final = parent.resolve() / output.name
    if _protected(final):
        return "protected_output"
    try:
        relative = final.relative_to(Path(root).resolve())
    except ValueError:
        return None
    if output.name in {"operator.sqlite", "config.json", "deployment.json"}:
        return "ledger_output"
    if any(part in LEDGER_PARTS for part in relative.parts):
        return "ledger_output"
    return None


def _forecast_sentence(forecasts: dict[str, Any]) -> str:
    if forecasts.get("status") != "measured":
        return (
            "Прогнозы: shadow ledger неизвестен "
            f"(`{forecasts.get('reason')}`). Счётчик остаётся неизвестным."
        )
    clock = forecasts.get("clock") if isinstance(forecasts.get("clock"), dict) else {}
    horizons = forecasts.get("by_horizon") if isinstance(forecasts.get("by_horizon"), dict) else {}
    parts = []
    for key in sorted(horizons, key=lambda item: int(item) if str(item).isdigit() else 0):
        row = horizons[key]
        if not isinstance(row, dict):
            continue
        minutes = row.get("minutes")
        label = f"{minutes} мин" if isinstance(minutes, int) else f"h{key}"
        parts.append(
            f"{label}: predictions {row.get('prediction_count')}, "
            f"outcomes {row.get('outcome_count')}, pending {row.get('pending')}"
        )
    detail = f" {'; '.join(parts)}." if parts else ""
    bar = f" Бар `{clock.get('bar_s')}` с." if isinstance(clock.get("bar_s"), int) else ""
    return (
        f"Прогнозы `{forecasts.get('prediction_count')}`, outcomes "
        f"`{forecasts.get('outcome_count')}`, независимые группы "
        f"`{forecasts.get('independent_groups')}` "
        f"(pending {forecasts.get('pending')}, scored {forecasts.get('scored')}, "
        f"censored {forecasts.get('censored')}).{bar}{detail} "
        "Горизонт без persisted outcome остаётся pending. uptime_claim остаётся false."
    )


def render_markdown(report: dict[str, Any]) -> str:
    """Concise Russian brief. The JSON report remains the machine ledger."""
    lines = [
        "# Журнал автономного оператора",
        "",
        "Следующему Codex читать сначала:",
        "",
    ]
    for item in report.get("read_first") or []:
        lines.append(f"- [{item['severity']}] {item['text']}")
    if not report.get("read_first"):
        lines.append("- На этом cutoff записанных инцидентов и блокирующих gates нет.")
    observed = report.get("observed") or {}
    forecasts = report.get("forecasts") or {}
    paper = report.get("paper") or {}
    lines.extend(
        [
            "",
            f"Cutoff `{report.get('cutoff_ns')}`. Схема `{report.get('schema')}`. "
            f"ok=`{report.get('ok')}`.",
            f"Наблюдаемые часы: `{observed.get('status')}`"
            f" ({observed.get('observed_hours')}). Elapsed: `{observed.get('elapsed_hours')}`. "
            f"Observer `{observed.get('observer_hours')}`, healthy service "
            f"`{observed.get('healthy_service_hours')}`. Это не uptime.",
            _forecast_sentence(forecasts),
            f"Paper stale=`{paper.get('stale')}`, realized `{paper.get('realized_total')}`, "
            f"MTM `{paper.get('mtm_equity_total')}`. Promotion none. "
            "ENTER-intent не считается fill: fills — записанные ledger entries/exits.",
            "Исторический test — reused/descriptive. Будущий loss только из shadow outcomes "
            "после activation. Калибровка не вычислялась.",
            "",
            "Документы: `docs/brain-autonomous-operator.md`, `docs/brain-operator-evidence.md`,",
            "`docs/brain-paper-loop.md`, `docs/brain-development.md`, `memory/evaluation.md`.",
            "",
        ]
    )
    return "\n".join(lines)
