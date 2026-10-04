"""Audited source/paper repair. Synthetic roots only; live STOP files stay untouched."""

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from memecoin_continuous.hot import authorization
from memecoin_continuous.runtime import guard_source
from memecoin_continuous.source import seed_state
from memecoin_continuous.source import state as journal_state
from memecoin_continuous.store import connect, ingest
from memecoin_ml.data import read, sha
from memecoin_operator.repair import recover_paper, recover_source, stage_bundle
from memecoin_paper.audit import replay
from memecoin_paper.engine import advance, initial
from memecoin_registry.contracts import digest, seal, verify
from memecoin_runner.ledger import Ledger, publish

NS = 10**9
ELAPSED = 600.007758
INCIDENT_NS = 1_791_145_945_693_553_000
PREFIX = "ai.memecoin.brain.b13."


def hold(path):
    script = (
        "import fcntl,sys,time\n"
        "f=open(sys.argv[1],'a')\n"
        "fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
        "sys.stdout.write('held\\n');sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script, str(path)],
        stdout=subprocess.PIPE,
    )
    if proc.stdout.readline().strip() != b"held":
        proc.kill()
        proc.wait()
        raise RuntimeError("lock holder failed")
    return proc


def capture(path, available=400 * NS):
    path.mkdir()
    publish(path / "request.json", dict(started_ns=available - 2, lane="historical", url="fixture"))
    raw = dict(data=dict(attributes=dict(ohlcv_list=[[0, 1, 1, 1, 1, 1]])))
    (path / "response.bin").write_text(json.dumps(raw))
    publish(path / "receipt.json", dict(received_ns=available - 1, available_ns=available))
    seal(path)
    return path


def pool(status="active", cursor=111, **extra):
    row = dict(
        next_ns=50,
        status=status,
        streak=0,
        probe_day=1,
        probes=0,
        cursor=cursor,
        history_pages=1,
        history_done=True,
    )
    row.update(extra)
    return row


def deadline(root, elapsed=ELAPSED, incident_ns=INCIDENT_NS, exit_code=-9):
    start = incident_ns - int(float(elapsed) * 1_000_000_000)
    name = f"incident-{incident_ns}.json"
    body = dict(category="source_stop_or_deadline", elapsed_seconds=elapsed, exit_code=exit_code)
    publish(root / name, body)
    publish(root / "STOP", dict(incident=name, reason="source_worker_failure"))
    (root / f"audit-worker-{start}.log").write_bytes(b"")
    publish(root / "failure-health.json", dict(at_ns=incident_ns, **body))
    return name


def build_source(tmp, *, expires_ns=None):
    old = tmp / "old-source"
    old.mkdir(parents=True)
    now = time.time_ns()
    latest = dict(
        last_ns=10,
        cooldown_ns=20,
        failures=0,
        rate_events=[3, 4],
        bytes=99,
        attempts=9,
        background_attempts=2,
        background_next_ns=30,
        pools={
            "hot": pool(cursor=111),
            "cold": pool(
                status="quarantined", cursor=222, probes=3, history_pages=0, history_done=False
            ),
            "fresh": pool(cursor=None),
        },
    )
    publish(
        old / "config.json",
        dict(
            created_ns=now - 86_400 * NS,
            expires_ns=now + 86_400 * NS if expires_ns is None else expires_ns,
            max_requests=25000,
            max_background_requests=4000,
            hot_interval_s=120,
            cold_interval_s=1800,
            request_spacing_s=12,
            hot_pools=["hot"],
            qualification_expires_ns=now - NS,
            qualification_max_requests=3,
            service_prefix="ai.memecoin.brain.b09.",
            paid_budget_usd=0,
        ),
    )
    publish(
        old / "universe.json",
        dict(pools=[dict(pool=name) for name in ("hot", "cold", "fresh")]),
    )
    publish(
        old / "provider-seed.json",
        dict(last_ns=1, cooldown_ns=0, failures=1, rate_events=[3], attempts=2, bytes=1),
    )
    publish(old / "seed.json", dict(quality={}, scheduler_state={}, lineage="old"))
    publish(old / "qualification.json", dict(approved=True, requests=73, inherited=True))
    publish(old / "health.json", dict(at_ns=now - 1000 * NS, attempts=2, cooldown_ns=0))
    (old / "qualification-samples").mkdir()
    (old / "qualification-samples" / "000000.json").write_text("{}\n")
    (old / "requests").mkdir()
    cap = capture(old / "requests" / "000000")
    with connect(old / "index.sqlite") as db:
        ingest(db, cap, "hot")
        # Reproduce the pre-fix source schema behind the real audit timeout.
        db.execute("drop index versions_pin_open")
    deadline(old)
    older = INCIDENT_NS - 3600 * NS
    (old / f"audit-worker-{older}.log").write_bytes(b"previous")
    publish(old / f"audit-{older + 500 * NS}.json", dict(verified=1))
    binding = dict(config_sha=sha(old / "config.json"), scope="B08_source")
    with Ledger(old / "journal", binding) as ledger:
        ledger.append("outcome", state=dict(latest, attempts=2, rate_events=[3]))
        ledger.append("outcome", state=latest)
    stage_bundle(old, tmp / "old-runtime")
    return old, latest, cap


def changed(old, new):
    return {key for key in set(old) | set(new) if old.get(key) != new.get(key)}


def named_bytes(root, pattern):
    return {path.name: path.read_bytes() for path in root.glob(pattern)}


def logical_rows(root):
    db = sqlite3.connect((root / "index.sqlite").as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("BEGIN")
        tables = [
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ]
        return {
            name: sorted(db.execute('SELECT * FROM "' + name + '"').fetchall(), key=repr)
            for name in tables
        }
    finally:
        db.close()


def test_source_repair_keeps_budget_and_provider_history(tmp_path):
    old, latest, cap = build_source(tmp_path)
    before_stop = (old / "STOP").read_bytes()
    before_config = (old / "config.json").read_bytes()
    before_qualification = (old / "qualification.json").read_bytes()
    before_index = logical_rows(old)
    before_events = named_bytes(old / "journal", "event-*.json")
    launch = Path.home() / "Library" / "LaunchAgents"
    agents = {path.name for path in launch.glob("*.plist")} if launch.is_dir() else set()
    new = tmp_path / "source"
    receipt = recover_source(old, new, tmp_path / "source-runtime", PREFIX)
    assert (old / "STOP").read_bytes() == before_stop
    assert (old / "config.json").read_bytes() == before_config
    assert (old / "qualification.json").read_bytes() == before_qualification
    assert (new / "health.json").read_bytes() == (old / "health.json").read_bytes()
    assert read(new / "health.json")["at_ns"] == read(old / "health.json")["at_ns"]
    assert logical_rows(old) == before_index
    assert named_bytes(old / "journal", "event-*.json") == before_events
    assert not (new / "STOP").exists()
    assert list(new.glob("requests/*")) == []
    fresh = read(new / "config.json")
    previous = read(old / "config.json")
    assert changed(previous, fresh) == {"service_prefix", "migration_sha"}
    assert fresh["service_prefix"] == PREFIX
    assert fresh["expires_ns"] == previous["expires_ns"]
    assert fresh["max_requests"] == 25000
    assert fresh["max_background_requests"] == 4000
    assert fresh["hot_interval_s"] == 120
    assert fresh["cold_interval_s"] == 1800
    assert fresh["migration_sha"] == sha(new / "migration.json")
    assert receipt["old_attempts"] == receipt["new_attempts"] == 9
    assert receipt["expires_ns"] == previous["expires_ns"]
    assert receipt["max_requests"] == 25000
    assert receipt["old_chain_rewritten"] is False
    assert receipt["qualification_rerun"] is False
    assert receipt["raw_audit"]["exact"] is True
    assert any("versions_pin_open" in str(item) for row in receipt["query_plan"] for item in row)
    carried = read(new / "provider-seed.json")
    assert carried["attempts"] == 9
    assert carried["rate_events"] == [3, 4]
    assert carried["cooldown_ns"] == 20
    assert carried["pools"]["cold"]["status"] == "quarantined"
    assert carried["pools"]["cold"]["cursor"] == 222
    assert carried["pools"]["hot"]["cursor"] == 111
    assert carried["pools"]["fresh"]["cursor"] is None
    assert "prior_provider_attempts" not in carried
    assert seed_state(new) == latest
    assert journal_state(new) == latest
    assert read(new / "seed.json")["lineage"] == "technical_repair_genesis"
    assert read(new / "seed.json")["scheduler_state"] == latest["pools"]
    events = list((new / "journal").glob("event-*.json"))
    assert len(events) == 1
    assert read(events[0])["kind"] == "repair_genesis"
    assert read(events[0])["payload"]["inherited_head"] == receipt["old_ledger_head"]
    with sqlite3.connect(f"file:{new / 'index.sqlite'}?mode=ro", uri=True) as db:
        path = db.execute("select path from captures").fetchone()[0]
        names = {row[0] for row in db.execute("select name from sqlite_master where type='index'")}
    assert path == str(cap.resolve())
    assert Path(path).is_absolute()
    assert "versions_pin_open" in names
    with sqlite3.connect(f"file:{old / 'index.sqlite'}?mode=ro", uri=True) as db:
        old_names = {
            row[0] for row in db.execute("select name from sqlite_master where type='index'")
        }
    assert "versions_pin_open" not in old_names
    activation = read(new / "activation.json")
    assert activation["qualification_sha"] == sha(old / "qualification.json")
    assert activation["qualification_rerun"] is False
    assert activation["inherited_qualification"] is True
    assert activation["config_sha"] == sha(new / "config.json")
    assert activation["runtime_pin"] == read(new / "deployment.json")["pin"]
    guard_source(new)
    authorization(new, fresh, time.time_ns(), 9)
    later = {path.name for path in launch.glob("*.plist")} if launch.is_dir() else set()
    assert later == agents


def test_source_repair_refuses_inflight_unknown_deadline_and_expiry(tmp_path):
    old = tmp_path / "busy"
    old.mkdir()
    proc = hold(old / ".audit-supervisor.lock")
    try:
        with pytest.raises(ValueError, match="repair_inflight"):
            recover_source(old, tmp_path / "new-busy", tmp_path / "bundle-busy", PREFIX)
    finally:
        proc.kill()
        proc.wait()
    assert not (tmp_path / "new-busy").exists()

    short = tmp_path / "short"
    short.mkdir()
    deadline(short, elapsed=240.1, incident_ns=INCIDENT_NS + 5_000_000_000)
    stop = (short / "STOP").read_bytes()
    with pytest.raises(ValueError, match="repair_stop_not_audit_deadline"):
        recover_source(short, tmp_path / "new-short", tmp_path / "bundle-short", PREFIX)
    assert (short / "STOP").read_bytes() == stop
    assert not (tmp_path / "new-short").exists()

    extra = tmp_path / "extra"
    extra.mkdir()
    name = deadline(extra)
    body = read(extra / name)
    body["detail"] = "unclassified"
    (extra / name).unlink()
    publish(extra / name, body)
    with pytest.raises(ValueError, match="repair_competing_error"):
        recover_source(extra, tmp_path / "new-extra", tmp_path / "bundle-extra", PREFIX)

    expired, _latest, _cap = build_source(tmp_path / "expired", expires_ns=time.time_ns() - NS)
    before = (expired / "STOP").read_bytes()
    with pytest.raises(ValueError, match="repair_source_expired"):
        recover_source(expired, tmp_path / "new-expired", tmp_path / "bundle-expired", PREFIX)
    assert (expired / "STOP").read_bytes() == before
    assert not (tmp_path / "new-expired").exists()


def observation(pool_name, close_s):
    return dict(
        pool=pool_name,
        price="1.25",
        close_s=close_s,
        available_ns=close_s * NS + NS,
        quality="active",
        capture_sha=f"synthetic-{pool_name}-{close_s}",
    )


def prediction(pool_name, now, model):
    return dict(
        id=f"synthetic-{pool_name}-{now}",
        pool=pool_name,
        predicted_ns=now,
        input_available_ns=now - 1,
        event_close_s=now // NS,
        model_sha=model,
        q_by_candidate=dict(gru=[-0.01, 0.05, 0.2]),
    )


def commit(ledger, state, tick):
    state, records = advance(state, tick)
    ledger.append("tick", tick=tick, result_sha=digest(dict(state=state, records=records)))
    return state


def build_paper(tmp, source, *, runs, frozen=False, stopped=False):
    old = tmp / "paper"
    old.mkdir(parents=True)
    now = time.time_ns()
    close1 = 1_700_000_000
    now1 = close1 * NS + 5 * NS
    close2 = close1 + 300
    now2 = close2 * NS + 5 * NS
    config = dict(
        source=str(tmp / "previous-source"),
        source_backend="continuous",
        source_runtime_pin="old-pin",
        service_prefix="ai.memecoin.brain.old.",
        port=1,
        expires_ns=now + 2 * 86400 * NS,
        activation_ns=now1 - 1000,
        training_max_runs=runs,
        real_execution_enabled=False,
        paid_budget_usd=0,
        champion_model_sha="synthetic",
    )
    if frozen:
        config.update(
            exam_start_ns=now1 - 1000,
            frozen_exam_models=[dict(path="/synthetic/model", sha="ab" * 32, label="exam")],
        )
    publish(old / "config.json", config)
    publish(old / "activation.json", dict(at_ns=now1, verdict="approved_virtual_migration"))
    (old / "training" / "000000").mkdir(parents=True)
    publish(old / "training" / "000000" / "result.json", dict(status="completed", fits=runs))
    (old / "training/000000/model/ledger").mkdir(parents=True)
    (old / "training/000000/model/ledger/.lock").write_bytes(b"")
    seal(old / "training/000000")
    (old / "candidate-ready").mkdir()
    publish(old / "candidate-ready" / "000000.json", dict(ready=True))
    (old / "candidate-shadows").mkdir()
    (old / "candidate-shadows" / "lane.txt").write_text("shadow")
    (old / "recovery").mkdir()
    publish(old / "recovery" / "attempt-000000.json", dict(reserved_ns=1))
    database = sqlite3.connect(old / "soak.sqlite")
    database.execute("create table soak(k text)")
    database.execute("insert into soak values ('synthetic')")
    database.commit()
    database.close()
    if stopped:
        publish(old / "STOP", dict(reason="paper_worker_failure", incident="kept"))
    state = initial()
    with Ledger(old / "ledger", dict(scope="synthetic-virtual-paper", trial_ids=[])) as ledger:
        state = commit(
            ledger,
            state,
            dict(
                at_ns=now1,
                activation_ns=now1 - 1000,
                market=[observation("poola", close1)],
                predictions=[prediction("poola", now1, "synthetic-model")],
                source_ok=True,
                deployment_config_sha="synthetic",
            ),
        )
        state = commit(
            ledger,
            state,
            dict(
                at_ns=now2,
                activation_ns=now1 - 1000,
                market=[observation("poola", close2), observation("poolb", close2)],
                predictions=[],
                source_ok=True,
                deployment_config_sha="synthetic",
            ),
        )
        state = commit(
            ledger,
            state,
            dict(
                at_ns=now2 + 20 * NS,
                activation_ns=now1 - 1000,
                market=[observation("poola", close2), observation("poolb", close2)],
                predictions=[prediction("poolb", now2 + 20 * NS, "synthetic-model")],
                source_ok=True,
                deployment_config_sha="synthetic",
            ),
        )
    return old, state


def book(state):
    return next(iter(state["books"].values()))


def test_paper_repair_replays_both_training_budgets(tmp_path):
    old_source, _latest, _cap = build_source(tmp_path / "source-work")
    source = tmp_path / "source-work" / "repaired"
    recover_source(old_source, source, tmp_path / "source-work" / "runtime", PREFIX)
    pin = read(source / "deployment.json")["pin"]
    for name, runs, frozen, stopped, port in (
        ("open", 4, False, False, 8790),
        ("frozen", 0, True, True, 8791),
    ):
        root = tmp_path / name
        old, state = build_paper(root, source, runs=runs, frozen=frozen, stopped=stopped)
        before_events = named_bytes(old / "ledger", "event-*.json")
        before_activation = (old / "activation.json").read_bytes()
        before_config = read(old / "config.json")
        stop_before = (old / "STOP").read_bytes() if stopped else None
        new = root / "revision"
        carried = recover_paper(old, new, source, PREFIX + name + ".", port)
        assert replay(new, carried["ledger_head"]) == replay(old, carried["ledger_head"])
        assert named_bytes(new / "ledger", "event-*.json") == before_events
        assert named_bytes(old / "ledger", "event-*.json") == before_events
        assert (new / "activation.json").read_bytes() == before_activation
        assert not (new / "deployment.json").exists()
        assert not (new / "STOP").exists()
        fresh = read(new / "config.json")
        assert changed(before_config, fresh) <= {
            "source",
            "source_runtime_pin",
            "service_prefix",
            "port",
            "migration_sha",
        }
        assert fresh["source"] == str(source.resolve())
        assert fresh["source_runtime_pin"] == pin
        assert fresh["service_prefix"] == PREFIX + name + "."
        assert fresh["port"] == port
        assert fresh["training_max_runs"] == runs
        assert fresh["expires_ns"] == before_config["expires_ns"]
        assert fresh["activation_ns"] == before_config["activation_ns"]
        if frozen:
            assert fresh["exam_start_ns"] == before_config["exam_start_ns"]
            assert fresh["frozen_exam_models"] == before_config["frozen_exam_models"]
        migration = read(new / "migration.json")
        assert fresh["migration_sha"] == sha(new / "migration.json")
        assert migration["reason"] == "explicit_source_repair"
        assert migration["automatic_activation"] is False
        assert migration["no_cash_reset"] is True
        assert migration["ledger_binding"]["scope"] == "synthetic-virtual-paper"
        assert migration["prefix_events"] == 3
        assert migration["prefix_head"] == carried["ledger_head"]
        assert migration["prefix_state_sha"] == carried["state_sha"]
        assert migration["source_runtime_pin"] == pin
        if stopped:
            assert (old / "STOP").read_bytes() == stop_before
            assert migration["parent_stop_sha"] == sha(old / "STOP")
        else:
            assert migration["parent_stop_sha"] is None
            assert not (old / "STOP").exists()
        with Ledger(new / "ledger", migration["ledger_binding"]) as ledger:
            prefix = ledger.events[: migration["prefix_events"]]
            assert prefix[-1]["sha256"] == migration["prefix_head"]
        original = book(state)
        copied = book(walk(new))
        assert copied["cash"] == original["cash"] != "1000"
        assert copied["positions"] == original["positions"]
        assert copied["pending"] == original["pending"]
        assert read(new / "training" / "000000" / "result.json")["status"] == "completed"
        verify(new / "training/000000")
        assert (new / "training/000000/model/ledger/.lock").exists()
        assert read(new / "candidate-ready" / "000000.json")["ready"] is True
        assert (new / "candidate-shadows" / "lane.txt").read_text() == "shadow"
        assert read(new / "recovery" / "attempt-000000.json")["reserved_ns"] == 1
        with sqlite3.connect(f"file:{new / 'soak.sqlite'}?mode=ro", uri=True) as db:
            assert db.execute("select k from soak").fetchone()[0] == "synthetic"


def walk(root):
    state = initial()
    binding = read(root / "ledger" / "binding.json")
    with Ledger(root / "ledger", binding) as ledger:
        for event in ledger.audit():
            state, _records = advance(state, event["payload"]["tick"])
    return state


def test_paper_repair_refuses_an_inflight_worker(tmp_path):
    old = tmp_path / "paper"
    old.mkdir()
    proc = hold(old / ".paper.lock")
    try:
        with pytest.raises(ValueError, match="repair_inflight"):
            recover_paper(old, tmp_path / "new", tmp_path / "source", PREFIX, 8790)
    finally:
        proc.kill()
        proc.wait()
    assert not (tmp_path / "new").exists()
