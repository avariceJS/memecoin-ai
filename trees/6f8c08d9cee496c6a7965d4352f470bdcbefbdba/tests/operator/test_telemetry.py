"""Synthetic telemetry sampler. No live services or provider requests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from memecoin_operator.control import initialize
from memecoin_operator.history import NS, build_report
from memecoin_operator.telemetry import record

T0 = 1_700_000_000 * NS


def _root(tmp: Path) -> Path:
    root = tmp / "op"
    initialize(root)
    return root


def _view(**overrides: object) -> dict:
    view = {
        "source": {
            "healthy": True,
            "stopped": False,
            "attempts": 4,
            "failures": 1,
            "cooldown_until_ns": T0,
            "budget_used": 4,
            "budget_max": 100,
            "expires_ns": T0 + 86_400 * NS,
            "heartbeat_ns": T0,
            "cmd": "rm -rf /",
            "weights": "WEIGHT_SENTINEL",
            "per_pool": [{"pool": "secret"}],
        },
        "coverage": {
            "bars": 10,
            "pools": 2,
            "matured_samples": 7,
            "event_days": 1,
            "receipt_days": 1,
            "per_pool": [{"pool": "secret"}],
        },
        "paper_exams": [
            {
                "id": "exam1",
                "artifact": "/tmp/hidden",
                "ledger_head": "ab" * 32,
                "operations": {
                    "paper": {
                        "healthy": False,
                        "stopped": False,
                        "supervisor_status": "failed",
                    }
                },
                "books": [
                    {
                        "candidate": "gru",
                        "cash": "900.00000000",
                        "equity": "1000",
                        "realized": "-2.5",
                        "unrealized": "1.25",
                        "fees": "0.1",
                        "positions": [{"pool": "poolA"}],
                        "closed": [{"pool": "poolA"}],
                    }
                ],
                "forecast_scores": [{"pinball": "0.999001"}, {"pinball": "0.111111"}],
            }
        ],
        "raw": "RAW_SENTINEL",
    }
    view.update(overrides)
    return view


def _state(mode: str = "paused") -> dict:
    return {"mode": mode, "phase": "paused", "command": "rm -rf /"}


def _payloads(root: Path) -> list[str]:
    db = sqlite3.connect(root / "operator.sqlite")
    try:
        return [row[0] for row in db.execute("SELECT payload_json FROM telemetry ORDER BY at_ns")]
    finally:
        db.close()


def test_throttle_backfill_and_future(tmp_path: Path) -> None:
    root = _root(tmp_path)
    first = record(root, _view(), _state("idle"), T0)
    assert first["ok"] is True and first["recorded"] is True
    early = record(root, _view(), _state(), T0 + 15 * NS)
    assert early["recorded"] is False and early["reason"] == "throttled"
    again = record(root, _view(), _state(), T0)
    assert again["reason"] == "duplicate"
    back = record(root, _view(), _state(), T0 - NS)
    assert back["ok"] is False and back["reason"] == "backfill"
    future = record(root, _view(), _state(), 9_000_000_000 * NS)
    assert future["ok"] is False and future["reason"] == "future_timestamp"
    db = sqlite3.connect(root / "operator.sqlite")
    try:
        count = db.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
        operators = db.execute("SELECT healthy FROM heartbeats WHERE service='operator'").fetchall()
    finally:
        db.close()
    assert count == 1
    assert operators == [(1,)]


def test_unknown_fields_and_intent_are_not_fills(tmp_path: Path) -> None:
    root = _root(tmp_path)
    result = record(root, _view(), _state(), T0)
    assert result["recorded"] is True
    raw = _payloads(root)[0]
    for leaked in (
        "rm -rf",
        "WEIGHT_SENTINEL",
        "RAW_SENTINEL",
        "/tmp/hidden",
        "per_pool",
        "pinball",
    ):
        assert leaked not in raw
    payload = json.loads(raw)
    assert payload["operator"] == {"mode": "paused", "phase": "paused"}
    assert payload["ns"] == T0
    assert payload["source"]["healthy"] is True
    assert payload["source"]["failures"] == 1
    assert payload["source"]["expires_ns"] == T0 + 86_400 * NS
    assert payload["coverage"]["receipt_days"] == 1
    book = payload["exams"][0]["books"][0]
    assert book["positions"] == 1
    assert book["trades"] == 1
    assert book["unrealized"] == "1.25"
    assert payload["exams"][0]["forecast_n"] == 2
    assert "closed" not in book


def test_failures_gaps_and_repeatable_report(tmp_path: Path) -> None:
    root = _root(tmp_path)
    stamps = (T0, T0 + 60 * NS, T0 + 120 * NS, T0 + 400 * NS)
    health = (False, False, True, True)
    failures = (2, 4, 4, 4)
    for stamp, healthy, failure in zip(stamps, health, failures, strict=True):
        view = _view()
        view["source"]["healthy"] = healthy
        view["source"]["failures"] = failure
        view["coverage"]["bars"] = 10 if stamp < T0 + 400 * NS else 15
        assert record(root, view, _state("collect"), stamp)["recorded"] is True
    cutoff = T0 + 400 * NS
    first = build_report(root, now_ns=cutoff)
    second = build_report(root, now_ns=cutoff)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    observed = first["observed"]
    assert observed["observer_ns"] == 120 * NS
    assert observed["healthy_service_ns"] == 0
    assert observed["uptime_claim"] is False
    assert any(
        gap["gap_ns"] == 280 * NS and gap["service"] == "operator" for gap in observed["gaps"]
    )
    history = first["providers"]["history"]
    assert [row["failures"] for row in history] == [2, 4, 4, 4]
    assert first["providers"]["recovery"] == [
        {"ns": T0 + 120 * NS, "from": "unhealthy", "to": "healthy"}
    ]
    assert first["coverage"]["telemetry"][-1]["bars"] == 15
    assert first["coverage"]["growth"]["bars_delta"] == 5
    assert first["promotions"] == []
    series = [row for row in first["coverage"]["points"]]
    assert all(row.get("origin") != "telemetry" for row in series)


def test_crash_rolls_back_heartbeats(tmp_path: Path) -> None:
    root = _root(tmp_path)
    db = sqlite3.connect(root / "operator.sqlite", isolation_level=None)
    db.execute(
        """CREATE TABLE telemetry (
             at_ns INTEGER PRIMARY KEY,
             payload_json TEXT NOT NULL
           )"""
    )
    db.execute(
        """CREATE TRIGGER boom BEFORE INSERT ON telemetry
           BEGIN SELECT RAISE(ROLLBACK, 'boom'); END"""
    )
    db.close()
    result = record(root, _view(), _state(), T0)
    assert result["ok"] is False and result["recorded"] is False
    db = sqlite3.connect(root / "operator.sqlite")
    try:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        beats = (
            db.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0]
            if "heartbeats" in names
            else 0
        )
        points = db.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
    finally:
        db.close()
    assert beats == 0
    assert points == 0


def test_refuses_without_initializing_or_rewriting(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    missing.mkdir()
    assert record(missing, _view(), _state(), T0)["reason"] == "uninitialized"
    assert not (missing / "operator.sqlite").exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    sqlite3.connect(empty / "operator.sqlite").close()
    assert record(empty, _view(), _state(), T0)["reason"] == "unsupported_schema"
    db = sqlite3.connect(empty / "operator.sqlite")
    try:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        db.close()
    assert "heartbeats" not in names
    assert "telemetry" not in names
    root = _root(tmp_path / "paid")
    (root / "config.json").write_text(json.dumps({"paid_budget_usd": 1}), encoding="utf-8")
    before = (root / "operator.sqlite").read_bytes()
    assert record(root, _view(), _state(), T0)["reason"] == "paid_budget"
    assert (root / "operator.sqlite").read_bytes() == before
    link = tmp_path / "link"
    link.symlink_to(root)
    assert record(link, _view(), _state(), T0)["reason"] == "symlink_root"


def test_append_does_not_update_older_rows(tmp_path: Path) -> None:
    root = _root(tmp_path)
    record(root, _view(), _state(), T0)
    changed = _view()
    changed["source"]["failures"] = 9
    changed["coverage"]["bars"] = 99
    assert record(root, changed, _state("auto"), T0 + 60 * NS)["recorded"] is True
    payloads = [json.loads(item) for item in _payloads(root)]
    assert payloads[0]["source"]["failures"] == 1
    assert payloads[0]["coverage"]["bars"] == 10
    assert payloads[1]["source"]["failures"] == 9
    db = sqlite3.connect(root / "operator.sqlite")
    try:
        schema = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        revisions = db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]
    finally:
        db.close()
    assert schema is None
    assert revisions == 1
