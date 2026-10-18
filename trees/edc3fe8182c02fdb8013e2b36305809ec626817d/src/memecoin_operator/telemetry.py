"""Bounded append-only sampler for an existing operator journal.

Parent maintain calls record() about every 15 seconds. This module does not start
services, read raw captures, or rewrite control tables.
"""

from __future__ import annotations

import json
import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from memecoin_operator.control import HASH_RE, ID_RE, MODES, REASON_RE
from memecoin_operator.control import SCHEMA as CONTROL_SCHEMA

NS = 1_000_000_000
MIN_INTERVAL_NS = 60 * NS
FUTURE_SKEW_NS = 2 * NS
TIMEOUT_S = 5
PAYLOAD_LIMIT = 65_536
SCHEMA = "operator-telemetry-v1"
FAMILIES = frozenset({"empirical", "linear", "gru"})
PHASE_RE = REASON_RE

HEARTBEATS_SQL = """
CREATE TABLE IF NOT EXISTS heartbeats (
    at_ns INTEGER NOT NULL,
    service TEXT NOT NULL CHECK (service IN ('operator', 'source', 'paper', 'exam')),
    exam_id TEXT NOT NULL DEFAULT '',
    healthy INTEGER NOT NULL CHECK (healthy IN (0, 1)),
    PRIMARY KEY (at_ns, service, exam_id)
)
"""
TELEMETRY_SQL = """
CREATE TABLE IF NOT EXISTS telemetry (
    at_ns INTEGER PRIMARY KEY,
    payload_json TEXT NOT NULL
)
"""


def record(
    root: Path, view: dict[str, Any], state: dict[str, Any], now_ns: int | None = None
) -> dict[str, Any]:
    """Append one actual observation, or refuse without inventing a point."""
    path = Path(root)
    if path.is_symlink() or _protected(path):
        return _result(False, "protected_root" if _protected(path) else "symlink_root")
    if not path.exists():
        return _result(False, "missing_root")
    if not path.is_dir():
        return _result(False, "bad_root")
    if not isinstance(view, dict) or not isinstance(state, dict):
        return _result(False, "bad_view")
    guard = _config_guard(path)
    if guard is not None:
        return _result(False, guard)
    stamp, stamp_reason = _stamp(now_ns)
    if stamp_reason is not None or stamp is None:
        return _result(False, stamp_reason or "bad_timestamp")
    database = path / "operator.sqlite"
    if database.is_symlink():
        return _result(False, "symlink_database")
    if not database.is_file():
        return _result(False, "uninitialized")
    payload = project_view(view, state, stamp)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) > PAYLOAD_LIMIT:
        return _result(False, "payload_too_large")
    try:
        db = sqlite3.connect(database, timeout=TIMEOUT_S, isolation_level=None)
    except sqlite3.Error:
        return _result(False, "sqlite_unreadable")
    try:
        db.execute(f"PRAGMA busy_timeout={TIMEOUT_S * 1000}")
        db.execute("BEGIN IMMEDIATE")
        schema_reason = _require_control(db)
        if schema_reason is not None:
            _rollback(db)
            return _result(False, schema_reason)
        if not _sampler_compatible(db):
            _rollback(db)
            return _result(False, "unsupported_sampler_schema")
        db.execute(HEARTBEATS_SQL)
        db.execute(TELEMETRY_SQL)
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        last = db.execute("SELECT MAX(at_ns) FROM heartbeats WHERE service='operator'").fetchone()[
            0
        ]
        if type(last) is int:
            if stamp < last:
                _rollback(db)
                return _result(False, "backfill", at_ns=stamp, last_operator_ns=last)
            if stamp == last:
                _rollback(db)
                return _result(True, "duplicate", at_ns=stamp, last_operator_ns=last)
            if stamp - last < MIN_INTERVAL_NS:
                _rollback(db)
                return _result(True, "throttled", at_ns=stamp, last_operator_ns=last)
        rows = _heartbeat_rows(view, stamp)
        db.executemany(
            "INSERT INTO heartbeats(at_ns, service, exam_id, healthy) VALUES (?,?,?,?)",
            rows,
        )
        db.execute("INSERT INTO telemetry(at_ns, payload_json) VALUES (?,?)", (stamp, encoded))
        db.execute("COMMIT")
    except sqlite3.OperationalError as exc:
        _rollback(db)
        locked = "locked" in str(exc).lower() or "busy" in str(exc).lower()
        return _result(False, "busy" if locked else "write_failed", at_ns=stamp)
    except sqlite3.Error:
        _rollback(db)
        return _result(False, "write_failed", at_ns=stamp)
    finally:
        db.close()
    return _result(True, None, at_ns=stamp, heartbeats=len(rows), recorded_ns=stamp)


def project_view(view: dict[str, Any], state: dict[str, Any], now_ns: int) -> dict[str, Any]:
    """Narrow actual fields. Unknown keys and derived fills are dropped."""
    body: dict[str, Any] = {"ns": now_ns}
    source = _source(view.get("source"))
    if source:
        body["source"] = source
    coverage = _coverage(view.get("coverage"))
    if coverage:
        body["coverage"] = coverage
    operator = _operator(state)
    if operator:
        body["operator"] = operator
    exams = _exams(view.get("paper_exams"))
    if exams:
        body["exams"] = exams
    return body


def parse_payload(raw: Any) -> dict[str, Any] | None:
    """Re-apply the allowlist so a stored row cannot widen the report."""
    if not isinstance(raw, str) or len(raw) > PAYLOAD_LIMIT:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    ns = _int(value.get("ns"))
    if ns is None:
        return None
    return project_view(
        {
            "source": value.get("source"),
            "coverage": value.get("coverage"),
            "paper_exams": _exams_from_stored(value.get("exams")),
        },
        value.get("operator") if isinstance(value.get("operator"), dict) else {},
        ns,
    )


def _exams_from_stored(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows = []
    for item in value:
        if not isinstance(item, dict):
            continue
        books = item.get("books") if isinstance(item.get("books"), list) else []
        restored = []
        for book in books:
            if not isinstance(book, dict):
                continue
            restored.append(dict(book))
        rows.append(
            {
                "id": item.get("id"),
                "ledger_head": item.get("ledger_head"),
                "forecast_n": item.get("forecast_n"),
                "operations": {"paper": item.get("diagnostics")},
                "books": restored,
            }
        )
    return rows


def _result(ok: bool, reason: str | None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ok": ok,
        "recorded": bool(ok and reason is None),
        "reason": reason,
        "schema": SCHEMA,
    }
    body.update(extra)
    return body


def _rollback(db: sqlite3.Connection) -> None:
    try:
        db.execute("ROLLBACK")
    except sqlite3.Error:
        pass


def _protected(path: Path) -> bool:
    for part in path.parts:
        low = part.lower()
        if (
            "holdout" in low
            or low in {"f01", "d43"}
            or low.startswith(("f01-", "f01_", "d43-", "d43_"))
        ):
            return True
    return False


def _config_guard(root: Path) -> str | None:
    path = root / "config.json"
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        return "symlink_config" if path.is_symlink() else "bad_config"
    try:
        if path.stat().st_size > 1_000_000:
            return "bad_config"
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "bad_config"
    if not isinstance(loaded, dict):
        return "bad_config"
    if "paid_budget_usd" in loaded and loaded.get("paid_budget_usd") != 0:
        return "paid_budget"
    if loaded.get("real_execution_enabled") is True:
        return "real_execution"
    return None


def _stamp(now_ns: int | None) -> tuple[int | None, str | None]:
    wall = time.time_ns()
    if now_ns is None:
        return wall, None
    if type(now_ns) is not int or now_ns < 0:
        return None, "bad_timestamp"
    if now_ns > wall + FUTURE_SKEW_NS:
        return None, "future_timestamp"
    return now_ns, None


def _require_control(db: sqlite3.Connection) -> str | None:
    names = {str(row[0]) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "meta" not in names or "revisions" not in names:
        return "unsupported_schema"
    columns = {str(row[1]) for row in db.execute("PRAGMA table_info(meta)")}
    if not {"key", "value"} <= columns:
        return "unsupported_schema"
    row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
    if row is not None and str(row[0]) != CONTROL_SCHEMA:
        return "unsupported_schema"
    revision_columns = {str(item[1]) for item in db.execute("PRAGMA table_info(revisions)")}
    if "created_ns" not in revision_columns or "mode" not in revision_columns:
        return "unsupported_schema"
    return None


def _sampler_compatible(db: sqlite3.Connection) -> bool:
    return _table_ok(
        db,
        "heartbeats",
        {"at_ns", "service", "exam_id", "healthy"},
        {"at_ns", "service", "exam_id"},
    ) and _table_ok(db, "telemetry", {"at_ns", "payload_json"}, {"at_ns"})


def _table_ok(db: sqlite3.Connection, name: str, columns: set[str], primary: set[str]) -> bool:
    found = list(db.execute(f"PRAGMA table_info({name})"))
    if not found:
        return True
    have = {str(row[1]) for row in found}
    keys = {str(row[1]) for row in found if row[5]}
    return columns <= have and primary <= keys


def _heartbeat_rows(view: dict[str, Any], stamp: int) -> list[tuple[int, str, str, int]]:
    rows: list[tuple[int, str, str, int]] = [(stamp, "operator", "", 1)]
    source = view.get("source") if isinstance(view.get("source"), dict) else None
    if source is not None and type(source.get("healthy")) is bool:
        rows.append((stamp, "source", "", 1 if source["healthy"] else 0))
    exams = view.get("paper_exams") if isinstance(view.get("paper_exams"), list) else []
    seen: set[str] = set()
    for exam in exams[:16]:
        if not isinstance(exam, dict):
            continue
        raw_id = exam.get("id")
        exam_id = raw_id if isinstance(raw_id, str) and ID_RE.fullmatch(raw_id) else None
        paper = _paper_ops(exam)
        healthy = paper.get("healthy") if isinstance(paper, dict) else None
        if exam_id is None or exam_id in seen or type(healthy) is not bool:
            continue
        seen.add(exam_id)
        rows.append((stamp, "paper", exam_id, 1 if healthy else 0))
    return rows


def _paper_ops(exam: dict[str, Any]) -> dict[str, Any] | None:
    operations = exam.get("operations")
    if not isinstance(operations, dict):
        return None
    paper = operations.get("paper")
    return paper if isinstance(paper, dict) else None


def _int(value: Any, *, high: int = 2**63 - 1) -> int | None:
    if type(value) is not int or value < 0 or value > high:
        return None
    return value


def _token(value: Any, pattern: Any) -> str | None:
    if isinstance(value, str) and pattern.fullmatch(value):
        return value
    return None


def _money(value: Any) -> str | None:
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if value != value or value in {float("inf"), float("-inf")}:
            return None
        return format(Decimal(str(value)), "f")
    if isinstance(value, str) and value and len(value) <= 40:
        try:
            number = Decimal(value)
        except (ArithmeticError, ValueError):
            return None
        if not number.is_finite():
            return None
        return format(number, "f")
    return None


def _count(value: Any) -> int | None:
    if type(value) is int:
        return value if 0 <= value <= 1_000_000 else None
    if isinstance(value, list) and len(value) <= 1_000_000:
        return len(value)
    return None


def _source(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    found: dict[str, Any] = {}
    for key in ("healthy", "stopped"):
        if type(value.get(key)) is bool:
            found[key] = value[key]
    for key in ("heartbeat_ns", "attempts", "failures", "budget_used", "budget_max", "expires_ns"):
        number = _int(value.get(key))
        if number is not None:
            found[key] = number
    cooldown = _int(value.get("cooldown_until_ns"))
    if cooldown is None:
        cooldown = _int(value.get("cooldown_ns"))
    if cooldown is not None:
        found["cooldown_ns"] = cooldown
    return found


def _coverage(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    found: dict[str, Any] = {}
    mapping = (
        ("bars", "bars"),
        ("pools", "pools"),
        ("observed_pools", "pools"),
        ("matured_samples", "matured_samples"),
        ("event_days", "event_days"),
        ("receipt_days", "receipt_days"),
    )
    for source_key, target in mapping:
        if target in found and source_key != target:
            continue
        number = _int(value.get(source_key))
        if number is not None:
            found[target] = number
    return found


def _operator(state: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    mode = state.get("mode")
    if isinstance(mode, str) and mode in MODES:
        found["mode"] = mode
    phase = _token(state.get("phase"), PHASE_RE)
    if phase is not None:
        found["phase"] = phase
    return found


def _diagnostics(paper: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(paper, dict):
        return {}
    found: dict[str, Any] = {}
    for key in ("healthy", "stopped", "expired"):
        if type(paper.get(key)) is bool:
            found[key] = paper[key]
    for key in ("supervisor_status", "failure_category"):
        token = _token(paper.get(key), PHASE_RE)
        if token is not None:
            found[key] = token
    return found


def _book(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    found: dict[str, Any] = {}
    candidate = value.get("candidate")
    if candidate in FAMILIES:
        found["candidate"] = candidate
    for key in ("cash", "equity", "realized", "unrealized", "fees"):
        if key not in value:
            continue
        money = _money(value.get(key))
        if money is not None:
            found[key] = money
    positions = _count(value.get("positions"))
    if positions is not None and "positions" in value:
        found["positions"] = positions
    if "trades" in value:
        trades = _count(value.get("trades"))
    elif "closed" in value:
        trades = _count(value.get("closed"))
    else:
        trades = None
    if trades is not None:
        found["trades"] = trades
    return found or None


def _exams(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows = []
    for exam in value[:16]:
        if not isinstance(exam, dict):
            continue
        exam_id = _token(exam.get("id"), ID_RE)
        if exam_id is None:
            continue
        item: dict[str, Any] = {"id": exam_id}
        head = _token(exam.get("ledger_head"), HASH_RE)
        if head is not None:
            item["ledger_head"] = head.lower()
        diagnostics = _diagnostics(_paper_ops(exam))
        if diagnostics:
            item["diagnostics"] = diagnostics
        books = []
        raw_books = exam.get("books") if isinstance(exam.get("books"), list) else []
        for book in raw_books[:16]:
            projected = _book(book)
            if projected is not None:
                books.append(projected)
        if books:
            item["books"] = books
        if type(exam.get("forecast_n")) is int and exam["forecast_n"] >= 0:
            item["forecast_n"] = exam["forecast_n"]
        elif isinstance(exam.get("forecast_scores"), list):
            item["forecast_n"] = len(exam["forecast_scores"])
        rows.append(item)
    rows.sort(key=lambda item: item["id"])
    return rows
