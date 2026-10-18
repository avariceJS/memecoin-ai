"""Synthetic operator roots. No live services, raw captures, or weight files."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

from memecoin_operator.control import initialize
from memecoin_operator.history import (
    NS,
    _snapshot_db,
    build_report,
    history_series,
    render_markdown,
)

T0 = 1_700_000_000 * NS
NOW = T0 + 10_000 * NS
ACTIVATION = T0 + 5_000 * NS
ROOT = Path(__file__).resolve().parents[2]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


_SIDECARS = {"operator.sqlite-wal", "operator.sqlite-shm"}


def _files(root: Path) -> dict[str, str]:
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.name in _SIDECARS:
            continue
        found[path.relative_to(root).as_posix()] = _sha(path)
    return found


def _control_tables(root: Path) -> dict[str, list[tuple[object, ...]]]:
    uri = (root / "operator.sqlite").resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        db.execute("PRAGMA query_only=ON")
        found = {}
        for table in ("meta", "revisions", "commands", "jobs", "events", "publications"):
            found[table] = [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY 1")]
        return found
    finally:
        db.close()


def _boot(path: Path, *, heartbeats: bool = True) -> Path:
    root = path / "op"
    initialize(root)
    db = sqlite3.connect(root / "operator.sqlite")
    db.execute("UPDATE revisions SET created_ns=?", (T0,))
    db.execute("UPDATE events SET created_ns=?", (T0,))
    if heartbeats:
        db.execute(
            """CREATE TABLE heartbeats(
                 at_ns INTEGER NOT NULL,
                 service TEXT NOT NULL,
                 exam_id TEXT,
                 healthy INTEGER NOT NULL,
                 PRIMARY KEY (at_ns, service))"""
        )
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    return root


def _job(db: sqlite3.Connection, **overrides: object) -> None:
    row = {
        "job_id": "job",
        "action": "train",
        "params_json": "{}",
        "status": "interrupted",
        "origin": "auto",
        "result_json": None,
        "error_reason": "interrupted",
        "pid": None,
        "deadline_ns": None,
        "started_ns": None,
        "finished_ns": None,
        "reserved_ns": None,
        "dataset_id": None,
        "snapshot_id": None,
        "snapshot_hash": None,
        "candidate_id": None,
        "exam_id": None,
        "matured_samples": None,
        "published": 0,
        "created_ns": T0,
    }
    row.update(overrides)
    columns = list(row)
    db.execute(
        f"INSERT INTO jobs ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        tuple(row[column] for column in columns),
    )


def _rich(tmp: Path) -> Path:
    root = _boot(tmp)
    source = tmp / "source"
    legacy = tmp / "legacy"
    source.mkdir()
    legacy.mkdir()
    _dump(
        source / "health.json",
        {"at_ns": NOW - 10 * NS, "attempts": 12, "failures": 4, "cooldown_ns": 0},
    )
    _dump(legacy / "health.json", {"at_ns": T0})
    _dump(
        legacy / "paper-health.json",
        {"status": "failed", "category": "worker_failed", "exit_code": 1},
    )
    _dump(legacy / "STOP", {"reason": "supervisor_guard", "at_ns": T0})
    (tmp / "champion.pt").write_text("CHAMPION_WEIGHT_SENTINEL", encoding="utf-8")
    _dump(
        root / "config.json",
        {
            "champion_model_path": str(tmp / "champion.pt"),
            "champion_model_sha": "ab" * 32,
            "created_ns": T0,
            "expires_ns": NOW + 86_400 * NS,
            "legacy_papers": [{"label": "b10", "port": 8790, "root": str(legacy)}],
            "max_training_runs": 1,
            "min_refit_interval_s": 86_400,
            "minimum_new_matured": 100,
            "paid_budget_usd": 0,
            "port": 8792,
            "real_execution_enabled": False,
            "source_root": str(source),
        },
    )
    bundle = root / "runtime-bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text('{"runtime": true}\n', encoding="utf-8")
    _dump(
        root / "deployment.json",
        {"bundle": str(bundle), "pin": _sha(bundle / "manifest.json")},
    )
    dataset = root / "artifacts" / "snap1"
    (dataset / "dataset").mkdir(parents=True)
    (dataset / "dataset" / "manifest.json").write_text("{}\n", encoding="utf-8")
    _dump(
        dataset / "coverage.json",
        {
            "as_of_ns": T0,
            "historical_test_independent": False,
            "matured_samples": 140,
            "recipe": {"version": "B13-pool-day-v1"},
            "recipe_sha": "cd" * 32,
            "retained": {
                "test": {"days": 2, "decisions": 20, "pools": 6},
                "train": {"days": 10, "decisions": 100, "pools": 4},
                "validation": {"days": 2, "decisions": 20, "pools": 4},
            },
            "unseen_test_pools": ["poolA", "poolB"],
        },
    )
    _dump(dataset / "manifest.json", {"sealed": True})
    _dump(
        root / "datasets" / "snap1.json",
        {
            "artifact": str(dataset),
            "created_ns": T0 + 100 * NS,
            "dataset_sha": _sha(dataset / "dataset" / "manifest.json"),
            "id": "snap1",
            "manifest_sha": _sha(dataset / "manifest.json"),
            "status": "completed",
        },
    )
    model = root / "artifacts" / "cand1" / "model"
    model.mkdir(parents=True)
    (model / "manifest.json").write_text('{"model": true}\n', encoding="utf-8")
    (model / "weights.pt").write_text("WEIGHT_SENTINEL", encoding="utf-8")
    _dump(
        model / "model-card.json",
        {
            "historical_test_independent": True,
            "model_version": "Brain-B13-autonomous",
            "promoted": True,
            "recipe": {"steps": 1000, "version": "B13-pool-day-v1"},
            "selected": "gru",
            "test": {
                "empirical": {"pool_macro_pinball": "0.424242"},
                "gru": {"pool_macro_pinball": "0.424242"},
            },
            "validation": {
                "empirical": {"pool_macro_pinball": "0.200000"},
                "gru": {"pool_macro_pinball": "0.100000"},
                "linear": {"pool_macro_pinball": "0.150000"},
            },
        },
    )
    candidate = root / "artifacts" / "cand1"
    _dump(candidate / "manifest.json", {"sealed": True})
    _dump(
        root / "candidates" / "cand1.json",
        {
            "artifact": str(candidate),
            "created_ns": T0 + 4_000 * NS,
            "dataset_id": "snap1",
            "id": "cand1",
            "manifest_sha": _sha(candidate / "manifest.json"),
            "model_sha": _sha(model / "manifest.json"),
            "promoted": True,
        },
    )
    exam = root / "paper" / "exam1"
    _dump(exam / "activation.json", {"at_ns": ACTIVATION, "mode": "frozen_future_exam"})
    _dump(
        exam / "dashboard.json",
        {
            "books": [
                {
                    "candidate": "gru",
                    "cash": "900.00000000",
                    "closed": [{"pool": "poolA"}],
                    "equity": "1001.25000000",
                    "fees": "0.50000000",
                    "mark_stale": True,
                    "model_sha": "cd" * 32,
                    "pending": [],
                    "positions": [{"pool": "poolA"}],
                    "realized": "-2.50000000",
                }
            ],
            "generated_ns": NOW - 1_000 * NS,
            "model_behavior": {
                "gru": {
                    "decisions": 10,
                    "entries": 1,
                    "exits": 1,
                    "reasons": {
                        "already_exposed": 1,
                        "enter_next_observed_close": 2,
                        "median_below_cost_buffer": 7,
                    },
                }
            },
            "schema": "brain-paper-v1",
        },
    )
    _dump(
        root / "exams" / "exam1.json",
        {
            "activation_ns": ACTIVATION,
            "candidate_id": "cand1",
            "created_ns": ACTIVATION,
            "exam_root": str(exam),
            "id": "exam1",
        },
    )
    _prediction(exam, "h1.json", ACTIVATION + 10 * NS, "poolA", 100, 1, "scored", "0.111111", True)
    _prediction(exam, "h3.json", ACTIVATION + 10 * NS, "poolA", 100, 3, None, None, True)
    _prediction(
        exam, "censored.json", ACTIVATION + 20 * NS, "poolB", 200, 1, "censored", None, True
    )
    _prediction(
        exam, "early.json", ACTIVATION - 10 * NS, "poolA", 50, 1, "scored", "0.999001", True
    )
    _prediction(exam, "late.json", NOW + 5 * NS, "poolC", 300, 1, None, None, True)
    _prediction(
        exam, "badsha.json", ACTIVATION + 30 * NS, "poolD", 400, 1, "scored", "0.777777", False
    )
    _dump(
        root / "progress" / "job_int.json",
        {
            "at_ns": T0 + 3_100 * NS,
            "completed_fits": 1,
            "fit": "gru-7",
            "status": "running",
            "total_fits": 4,
        },
    )
    db = sqlite3.connect(root / "operator.sqlite")
    beats = [
        (T0, "source", None, 1),
        (T0 + 90 * NS, "source", None, 1),
        (T0 + 290 * NS, "source", None, 1),
        (T0, "paper", None, 1),
        (T0 + 30 * NS, "paper", None, 0),
    ]
    db.executemany("INSERT INTO heartbeats VALUES (?,?,?,?)", beats)
    _job(
        db,
        job_id="job_int",
        status="interrupted",
        error_reason="interrupted",
        created_ns=T0 + 3_000 * NS,
        started_ns=T0 + 3_000 * NS,
        finished_ns=T0 + 3_100 * NS,
        reserved_ns=T0 + 3_000 * NS,
    )
    _job(
        db,
        job_id="job_ok",
        status="completed",
        error_reason="published",
        created_ns=T0 + 3_500 * NS,
        started_ns=T0 + 3_500 * NS,
        finished_ns=T0 + 4_000 * NS,
        reserved_ns=T0 + 3_500 * NS,
        candidate_id="cand1",
        published=1,
    )
    db.execute(
        "INSERT INTO events(kind, reason, payload_json, created_ns) VALUES (?,?,?,?)",
        (
            "history",
            "sample",
            json.dumps(
                {
                    "bars": 100,
                    "cmd": "rm -rf /",
                    "event_days": 1,
                    "ns": T0,
                    "pools": 2,
                    "samples": 10,
                }
            ),
            T0,
        ),
    )
    db.execute(
        "INSERT INTO events(kind, reason, payload_json, created_ns) VALUES (?,?,?,?)",
        (
            "history",
            "sample",
            json.dumps(
                {"bars": 250, "event_days": 3, "ns": T0 + 8_000 * NS, "pools": 5, "samples": 40}
            ),
            T0 + 8_000 * NS,
        ),
    )
    db.execute(
        "INSERT INTO events(kind, reason, payload_json, created_ns) VALUES (?,?,?,?)",
        ("audit", "transport", "{}", T0 + 50 * NS),
    )
    db.execute(
        "INSERT INTO events(kind, reason, payload_json, created_ns) VALUES (?,?,?,?)",
        (
            "incident",
            "interrupted",
            json.dumps({"crash": True, "job_id": "job_int"}),
            T0 + 3_100 * NS,
        ),
    )
    db.execute(
        """INSERT INTO revisions(
             mode, phase, collect_profile, selected_candidate_id, active_exam_id,
             resume_mode, auto_reason, cycle_json, reason, created_ns)
           VALUES (
             'auto','waiting','balanced','cand1','exam1',
             NULL,NULL,'{"expect":"snapshot"}','auto',?)""",
        (T0 + 1_000 * NS,),
    )
    db.execute(
        """INSERT INTO revisions(
             mode, phase, collect_profile, selected_candidate_id, active_exam_id,
             resume_mode, auto_reason, cycle_json, reason, created_ns)
           VALUES (
             'paused','paused','balanced','cand1','exam1',
             'auto','quota','{"expect":"none"}','quota',?)""",
        (T0 + 2_000 * NS,),
    )
    db.execute(
        """INSERT INTO publications(
             job_id, kind, public_id, payload_json, published_ns)
           VALUES (?,?,?,?,?)""",
        ("job_ok", "candidate", "cand1", "{}", T0 + 4_000 * NS),
    )
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    return root


def _prediction(
    exam: Path,
    name: str,
    predicted: int,
    pool: str,
    close_s: int,
    horizon: int,
    status: str | None,
    pinball: str | None,
    sha_ok: bool,
) -> None:
    path = exam / "shadow" / "predictions" / name
    _dump(
        path,
        {
            "event_close_s": close_s,
            "horizon": horizon,
            "model_sha": "cd" * 32,
            "pool": pool,
            "predicted_ns": predicted,
        },
    )
    if status is None:
        return
    outcome: dict[str, object] = {
        "prediction_sha": _sha(path) if sha_ok else "ab" * 32,
        "status": status,
    }
    if status == "scored":
        outcome["scored_ns"] = predicted + 60 * NS
        outcome["candidate_metrics"] = {
            "empirical": {"coverage": [True, False, True], "pinball": pinball}
        }
    else:
        outcome["observed_ns"] = predicted + 60 * NS
    _dump(exam / "shadow" / "outcomes" / name, outcome)


def test_missing_partial_and_bad_schema(tmp_path: Path) -> None:
    missing = build_report(tmp_path / "absent", now_ns=NOW)
    assert missing["ok"] is False
    assert missing["reason"] == "missing_root"
    assert missing["observed"]["status"] == "unknown"
    assert missing["observed"]["observed_hours"] is None
    assert missing["observed"]["observed_ns"] is None

    partial = tmp_path / "partial"
    partial.mkdir()
    _dump(partial / "config.json", {"paid_budget_usd": 0, "real_execution_enabled": False})
    report = build_report(partial, now_ns=NOW)
    assert report["reason"] == "uninitialized"
    assert report["hashes"]["config_sha256"]
    assert report["observed"]["reason"] == "no_heartbeat_table"

    bad = tmp_path / "bad"
    bad.mkdir()
    db = sqlite3.connect(bad / "operator.sqlite")
    db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    db.commit()
    db.close()
    refused = build_report(bad, now_ns=NOW)
    assert refused["reason"] == "unsupported_schema"
    assert refused["observed"]["observed_ns"] is None


def test_symlink_and_protected_roots_are_not_followed(tmp_path: Path) -> None:
    real = _boot(tmp_path, heartbeats=False)
    (real / "note.txt").write_text("ROOT_SENTINEL", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    report = build_report(link, now_ns=NOW)
    assert report["reason"] == "symlink_root"
    assert "ROOT_SENTINEL" not in json.dumps(report)

    protected = tmp_path / "holdout"
    protected.mkdir()
    (protected / "secret.txt").write_text("HOLDOUT_SENTINEL", encoding="utf-8")
    blocked = build_report(protected, now_ns=NOW)
    assert blocked["reason"] == "protected_root"
    assert "HOLDOUT_SENTINEL" not in json.dumps(blocked)


def test_unknown_hours_when_history_has_no_heartbeats(tmp_path: Path) -> None:
    root = _boot(tmp_path, heartbeats=False)
    db = sqlite3.connect(root / "operator.sqlite")
    db.execute(
        "INSERT INTO events(kind, reason, payload_json, created_ns) VALUES (?,?,?,?)",
        ("history", "sample", json.dumps({"bars": 10, "ns": T0, "samples": 1}), T0),
    )
    db.execute(
        "INSERT INTO events(kind, reason, payload_json, created_ns) VALUES (?,?,?,?)",
        ("history", "sample", json.dumps({"bars": 25, "ns": T0 + NS, "samples": 2}), T0 + NS),
    )
    db.commit()
    db.close()
    report = build_report(root, now_ns=NOW)
    assert report["ok"] is True
    assert report["observed"]["status"] == "unknown"
    assert report["observed"]["reason"] == "no_heartbeat_table"
    assert report["observed"]["observed_hours"] is None
    assert report["coverage"]["growth"]["status"] == "measured"
    assert report["coverage"]["growth"]["bars_delta"] == 15


def test_empty_heartbeat_table_is_unknown(tmp_path: Path) -> None:
    root = _boot(tmp_path, heartbeats=True)
    report = build_report(root, now_ns=NOW)
    assert report["observed"]["status"] == "unknown"
    assert report["observed"]["reason"] == "no_heartbeat_rows"
    assert report["observed"]["observed_ns"] is None


def test_gap_boundary_counts_only_intervals_within_90s(tmp_path: Path) -> None:
    root = _boot(tmp_path)
    base = T0
    db = sqlite3.connect(root / "operator.sqlite")
    db.executemany(
        "INSERT INTO heartbeats VALUES (?,?,?,?)",
        [
            (base, "source", None, 1),
            (base + 90 * NS, "source", None, 1),
            (base + 180 * NS + 1, "source", None, 1),
        ],
    )
    db.commit()
    db.close()
    report = build_report(root, now_ns=base + 200 * NS)
    observed = report["observed"]
    assert observed["status"] == "measured"
    assert observed["observed_ns"] == 90 * NS
    assert observed["elapsed_ns"] == 200 * NS
    gaps = [gap for gap in observed["gaps"] if not gap["trailing"]]
    assert len(gaps) == 1
    assert gaps[0]["gap_ns"] == 90 * NS + 1


def test_rich_report_is_repeatable_and_read_only(tmp_path: Path) -> None:
    root = _rich(tmp_path)
    (root / "STOP").write_text("frozen\n", encoding="utf-8")
    before = _files(root)
    before_tables = _control_tables(root)
    first = build_report(root, now_ns=NOW)
    second = build_report(root, now_ns=NOW)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert _files(root) == before
    assert _control_tables(root) == before_tables
    blob = json.dumps(first)
    assert str(tmp_path) not in blob
    for sentinel in ("CHAMPION_WEIGHT_SENTINEL", "WEIGHT_SENTINEL", "rm -rf"):
        assert sentinel not in blob
    assert first["ok"] is True
    assert first["integrity_ok"] is True
    assert first["promotions"] == []
    assert first["promotion"] is None
    assert first["weights_read"] is False
    assert first["fills_recomputed"] is False
    assert first["future_includes_historical_test"] is False
    manifest = root / "runtime-bundle" / "manifest.json"
    assert first["hashes"]["operator_runtime_manifest_sha256"] == _sha(manifest)
    assert first["observed"]["observed_ns"] == 90 * NS
    assert first["observed"]["observer_ns"] == 0
    assert first["observed"]["healthy_service_ns"] == 90 * NS
    assert first["observed"]["uptime_claim"] is False
    assert first["observed"]["elapsed_ns"] == 10_000 * NS
    assert any(gap["gap_ns"] == 200 * NS for gap in first["observed"]["gaps"])
    assert first["coverage"]["growth"]["bars_delta"] == 150
    assert first["coverage"]["growth"]["pools_delta"] == 3
    model = first["models"][0]
    assert model["selected_family"] == "gru"
    assert model["selection_basis"] == "recorded_validation_only"
    assert model["promoted"] is None
    assert set(model["validation"]) == {"empirical", "linear", "gru"}
    assert model["historical_test"]["label"] == "reused_descriptive"
    assert model["historical_test"]["independent"] is False
    assert "winner" not in blob
    forecasts = json.dumps(first["forecasts"])
    assert "0.111111" in forecasts
    for leaked in ("0.424242", "0.999001", "0.777777"):
        assert leaked not in forecasts
    assert first["forecasts"]["prediction_count"] == 4
    assert first["forecasts"]["independent_groups"] == 3
    assert first["forecasts"]["pending"] == 2
    assert first["forecasts"]["scored"] == 1
    assert first["forecasts"]["censored"] == 1
    assert first["forecasts"]["excluded"]["before_activation"] == 1
    assert first["forecasts"]["excluded"]["after_cutoff"] == 1
    assert first["forecasts"]["excluded"]["unverified_outcome"] == 1
    assert first["forecasts"]["calibration"]["available"] is False
    assert first["forecasts"]["groups"]["any_pending"] == 2
    assert first["forecasts"]["groups"]["any_censored"] == 1
    assert first["paper"]["stale"] is True
    assert "mark_stale" in first["paper"]["stale_reasons"]
    assert "dashboard_age" in first["paper"]["stale_reasons"]
    assert first["paper"]["actions"] == {
        "WAIT": 7,
        "ENTER": 2,
        "HOLD": 1,
        "EXIT": 0,
        "unclassified": 0,
    }
    assert first["paper"]["stages"] == {
        "intent": 2,
        "existing_or_pending_exposure": 1,
        "decision": 7,
    }
    assert first["paper"]["fills"] == {"entries": 1, "exits": 1}
    assert first["paper"]["intent_counted_as_fill"] is False
    assert first["paper"]["realized_total"] == "-2.50000000"
    assert first["paper"]["mtm_equity_total"] == "1001.25000000"
    assert first["paper"]["recomputed_fills"] is False
    assert first["training"]["interrupted_without_candidate"] == 1
    assert first["training"]["published_candidates"] == 1
    assert first["providers"]["consecutive_failures"] == 4
    assert any(item["code"] == "promotion_claim_ignored" for item in first["read_first"])
    assert any(item["code"] == "stale_paper" for item in first["read_first"])
    assert first["read_first"][0]["severity"] == "high"
    text = render_markdown(first)
    assert "Следующему Codex читать сначала" in text
    assert "docs/brain-autonomous-operator.md" in text
    assert "docs/brain-operator-evidence.md" in text
    assert str(tmp_path) not in text
    series = history_series(root, limit=500)
    assert {row["kind"] for row in series} >= {"source", "corpus", "training", "paper"}
    assert history_series(root, limit=1) == [series[-1]]
    assert series[-1]["ns"] == T0 + 8_000 * NS
    assert all(not str(row["id"]).startswith("/") for row in series if row["id"])


def test_outside_symlink_and_tampered_artifacts(tmp_path: Path) -> None:
    root = _boot(tmp_path, heartbeats=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "coverage.json").write_text("SECRET_SENTINEL", encoding="utf-8")
    _dump(
        root / "datasets" / "outside.json",
        {"artifact": str(outside), "created_ns": T0, "id": "outside", "manifest_sha": "ab" * 32},
    )
    link = root / "linked"
    link.symlink_to(outside, target_is_directory=True)
    _dump(
        root / "datasets" / "linked.json",
        {"artifact": str(link), "created_ns": T0, "id": "linked", "manifest_sha": "ab" * 32},
    )
    tampered = root / "artifacts" / "bad"
    tampered.mkdir(parents=True)
    (tampered / "coverage.json").write_text("PWNED_COMMAND", encoding="utf-8")
    _dump(tampered / "manifest.json", {"sealed": False})
    _dump(
        root / "datasets" / "bad.json",
        {
            "artifact": str(tampered),
            "created_ns": T0,
            "id": "bad",
            "manifest_sha": "ab" * 32,
        },
    )
    report = build_report(root, now_ns=NOW)
    blob = json.dumps(report)
    assert "SECRET_SENTINEL" not in blob
    assert "PWNED_COMMAND" not in blob
    assert str(tmp_path) not in blob
    codes = {item["code"] for item in report["refused"]}
    assert {"outside_root", "symlink_artifact", "tampered_manifest"} <= codes
    assert report["integrity_ok"] is False
    assert all(item["verified"] is False for item in report["snapshots"])


def test_cli_refuses_existing_and_ledger_outputs(tmp_path: Path) -> None:
    root = _boot(tmp_path, heartbeats=False)
    output = tmp_path / "report.json"
    markdown = tmp_path / "report.md"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [
        sys.executable,
        str(ROOT / "tools" / "brain_operator_report.py"),
        "--root",
        str(root),
        "--output",
        str(output),
        "--markdown-output",
        str(markdown),
        "--now-ns",
        str(NOW),
    ]
    first = subprocess.run(command, check=False, capture_output=True, text=True, env=env)
    assert first.returncode == 0, first.stderr
    written = output.read_text(encoding="utf-8")
    second = subprocess.run(command, check=False, capture_output=True, text=True, env=env)
    assert second.returncode == 2
    assert output.read_text(encoding="utf-8") == written
    assert "Следующему Codex читать сначала" in markdown.read_text(encoding="utf-8")
    ledger = root / "datasets" / "new.json"
    ledger.parent.mkdir()
    blocked = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "brain_operator_report.py"),
            "--root",
            str(root),
            "--output",
            str(ledger),
            "--now-ns",
            str(NOW),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert blocked.returncode == 2
    assert not ledger.exists()


def test_live_backup_hides_uncommitted_writes(tmp_path: Path) -> None:
    root = _boot(tmp_path, heartbeats=False)
    database = root / "operator.sqlite"
    started = threading.Event()
    release = threading.Event()

    def writer() -> None:
        db = sqlite3.connect(database, timeout=5, isolation_level=None)
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO meta(key, value) VALUES('race', 'partial')")
        started.set()
        assert release.wait(2)
        db.execute("UPDATE meta SET value='committed' WHERE key='race'")
        db.execute("COMMIT")
        db.close()

    thread = threading.Thread(target=writer)
    thread.start()
    assert started.wait(2)
    seen = []
    for _ in range(5):
        memory = _snapshot_db(database)
        try:
            seen.extend(
                row["value"] for row in memory.execute("SELECT value FROM meta WHERE key='race'")
            )
        finally:
            memory.close()
    assert "partial" not in seen
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    memory = _snapshot_db(database)
    try:
        values = [row["value"] for row in memory.execute("SELECT value FROM meta WHERE key='race'")]
    finally:
        memory.close()
    assert values == ["committed"]


def _register_exam(root: Path, exam: Path, exam_id: str = "exam1") -> None:
    _dump(exam / "activation.json", {"at_ns": ACTIVATION, "mode": "frozen_future_exam"})
    _dump(
        root / "exams" / f"{exam_id}.json",
        {
            "activation_ns": ACTIVATION,
            "created_ns": ACTIVATION,
            "exam_root": str(exam),
            "id": exam_id,
        },
    )


def _lane_prediction(
    directory: Path,
    name: str,
    model: str | None,
    horizon: int,
    predicted: int,
    pool: str,
    close_s: int,
    status: str | None,
    pinball: object | None,
) -> Path:
    path = directory / name
    payload: dict[str, object] = {
        "event_close_s": close_s,
        "horizon": horizon,
        "model_path": "/tmp/hidden-weight",
        "pool": pool,
        "predicted_ns": predicted,
        "q": [0.1, 0.2, 0.3],
        "q_by_candidate": {"gru": [0.1, 0.2, 0.3]},
    }
    if model is not None:
        payload["model_sha"] = model
    _dump(path, payload)
    if status is None:
        return path
    outcome: dict[str, object] = {
        "log_return": 0.5,
        "prediction_sha": _sha(path),
        "status": status,
    }
    if status == "scored":
        outcome["scored_ns"] = predicted + 60 * NS
        outcome["candidate_metrics"] = {
            "empirical": {"coverage": [True, False, True], "pinball": pinball}
        }
    else:
        outcome["observed_ns"] = predicted + 60 * NS
    _dump(directory.parent / "outcomes" / name, outcome)
    return path


def test_missing_shadow_ledger_is_unknown_not_zero(tmp_path: Path) -> None:
    root = _boot(tmp_path, heartbeats=False)
    _register_exam(root, root / "paper" / "bare")
    report = build_report(root, now_ns=NOW)
    forecasts = report["forecasts"]
    assert forecasts["status"] == "unknown"
    assert forecasts["reason"] == "shadow_ledger_missing"
    assert forecasts["prediction_count"] is None
    assert forecasts["outcome_count"] is None
    assert forecasts["loss"] is None
    assert "0" != forecasts["reason"]


def test_empty_shadow_directory_is_measured_zero(tmp_path: Path) -> None:
    root = _boot(tmp_path, heartbeats=False)
    exam = root / "paper" / "exam1"
    lane = exam / "candidate-shadows" / ("aa" * 32) / "shadow" / "predictions"
    lane.mkdir(parents=True)
    _register_exam(root, exam)
    report = build_report(root, now_ns=NOW)
    forecasts = report["forecasts"]
    assert forecasts["status"] == "measured"
    assert forecasts["prediction_count"] == 0
    assert forecasts["outcome_count"] == 0
    assert forecasts["by_horizon"] == {}


def test_candidate_and_legacy_shadows_keep_model_horizons(tmp_path: Path) -> None:
    root = _boot(tmp_path, heartbeats=False)
    exam = root / "paper" / "exam1"
    model_a = "aa" * 32
    model_b = "bb" * 32
    lane_a = exam / "candidate-shadows" / model_a / "shadow" / "predictions"
    lane_b = exam / "candidate-shadows" / model_b / "shadow" / "predictions"
    original = _lane_prediction(
        lane_a, "same.json", model_a, 1, ACTIVATION + 10 * NS, "poolA", 100, "scored", "0.200000"
    )
    _lane_prediction(
        lane_a, "wait.json", model_a, 3, ACTIVATION + 10 * NS, "poolA", 100, None, None
    )
    _lane_prediction(
        lane_b, "same.json", model_b, 1, ACTIVATION + 10 * NS, "poolA", 100, "scored", 0.125
    )
    _lane_prediction(
        lane_b, "later.json", model_b, 3, ACTIVATION + 10 * NS, "poolA", 100, "scored", "0.400000"
    )
    _lane_prediction(
        lane_a,
        "foreign.json",
        model_b,
        1,
        ACTIVATION + 10 * NS,
        "poolA",
        100,
        "scored",
        "9.999999",
    )
    _lane_prediction(
        lane_a, "early.json", model_a, 1, ACTIVATION - 10 * NS, "poolA", 50, "scored", "8.888888"
    )
    legacy = exam / "shadow" / "predictions" / "same.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(original.read_bytes())
    _lane_prediction(
        exam / "shadow" / "predictions",
        "loose.json",
        None,
        1,
        ACTIVATION + 10 * NS,
        "poolZ",
        700,
        "scored",
        "7.777777",
    )
    bundle = root / "rt"
    source = bundle / "runtime" / "memecoin_brain" / "source.py"
    source.parent.mkdir(parents=True)
    source.write_text("BAR = 300\n", encoding="utf-8")
    _dump(exam / "deployment.json", {"bundle": str(bundle), "pin": "cd" * 32})
    _dump(
        exam / "dashboard.json",
        {
            "books": [
                {
                    "candidate": "empirical",
                    "cash": "1000",
                    "closed": [],
                    "equity": "1000",
                    "fees": "0",
                    "mark_stale": False,
                    "model_sha": model_a,
                    "pending": [],
                    "positions": [],
                    "realized": "0",
                },
                {
                    "candidate": "gru",
                    "cash": "1000",
                    "closed": [],
                    "equity": "1000",
                    "fees": "0",
                    "mark_stale": False,
                    "model_sha": model_b,
                    "pending": [],
                    "positions": [],
                    "realized": "0",
                },
            ],
            "generated_ns": NOW - 10 * NS,
            "model_behavior": {
                "empirical": {
                    "decisions": 1,
                    "entries": 0,
                    "exits": 0,
                    "reasons": {"median_below_cost_buffer": 1},
                },
                "gru": {
                    "decisions": 1,
                    "entries": 0,
                    "exits": 0,
                    "reasons": {"median_below_cost_buffer": 1},
                },
            },
            "schema": "brain-paper-v1",
        },
    )
    _register_exam(root, exam)
    report = build_report(root, now_ns=NOW)
    forecasts = report["forecasts"]
    blob = json.dumps(forecasts)
    assert forecasts["status"] == "measured"
    assert forecasts["prediction_count"] == 4
    assert forecasts["outcome_count"] == 3
    assert forecasts["pending"] == 1
    assert forecasts["scored"] == 3
    assert forecasts["duplicates_folded"] == 1
    assert forecasts["excluded"]["model_mismatch"] == 1
    assert forecasts["excluded"]["model_unassociated"] == 1
    assert forecasts["excluded"]["before_activation"] == 1
    assert forecasts["loss_combined"] is False
    assert forecasts["loss"] is None
    assert forecasts["independent_groups"] == 2
    assert forecasts["clock"] == {"bar_s": 300, "bar_source": "pinned_runtime"}
    assert forecasts["by_horizon"]["1"]["minutes"] == 5
    assert forecasts["by_horizon"]["3"]["minutes"] == 15
    assert forecasts["by_horizon"]["1"]["prediction_count"] == 2
    assert forecasts["by_horizon"]["3"]["prediction_count"] == 2
    assert forecasts["by_horizon"]["3"]["pending"] == 1
    assert forecasts["by_horizon"]["3"]["outcome_count"] == 1
    by_sha = {row["model_sha"]: row for row in forecasts["by_model"]}
    assert by_sha[model_a]["prediction_count"] == 2
    assert by_sha[model_a]["outcome_count"] == 1
    assert by_sha[model_a]["by_horizon"]["1"]["loss"]["empirical"] == {
        "n": 1,
        "pinball_sum": "0.200000",
    }
    assert by_sha[model_a]["by_horizon"]["3"]["loss"] == {}
    assert by_sha[model_b]["by_horizon"]["1"]["loss"]["empirical"]["pinball_sum"] == "0.125"
    assert by_sha[model_b]["by_horizon"]["3"]["loss"]["empirical"]["pinball_sum"] == "0.400000"
    assert forecasts["calibration"]["available"] is False
    assert forecasts["descriptive_only"] is True
    leaked_values = (
        "/tmp/hidden-weight",
        "q_by_candidate",
        "log_return",
        "poolA",
        "9.999999",
        "8.888888",
        "7.777777",
    )
    for leaked in leaked_values:
        assert leaked not in blob
    assert len(report["paper"]["books"]) == 2
    assert report["paper"]["mtm_equity_total"] == "2000"
    assert report["paper"]["fills"] == {"entries": 0, "exits": 0}
    text = render_markdown(report)
    assert "5 мин" in text
    assert "15 мин" in text
    assert "uptime_claim остаётся false" in text
