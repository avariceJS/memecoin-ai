"""Operator journal, quotas, crash fencing, and honest projections."""

import json
import os
import signal
import sqlite3
import threading
import time

import pytest

from memecoin_operator import control
from memecoin_operator.control import (
    assess_result,
    command,
    events,
    initialize,
    map_action,
    now_ns,
    recover,
    set_clock,
    status,
    tick,
)

FAKE = r"""
import json
import time
from pathlib import Path

def _read(root, name, default):
    path = Path(root) / name
    if not path.exists():
        return default
    return json.loads(path.read_text())

def inspect(root):
    return _read(root, "inspect.json", {})

def run(root, action, params, output):
    root = Path(root)
    with (root / "runs.jsonl").open("a") as handle:
        handle.write(json.dumps({"action": action, "params": params}) + "\n")
    behavior = _read(root, "adapter_run.json", {}).get(action, {})
    if behavior.get("sleep"):
        time.sleep(float(behavior["sleep"]))
    if behavior.get("raise"):
        raise RuntimeError("boom secret /tmp/hidden")
    result = dict(behavior.get("result") or {"ok": True})
    if action == "collect" and "profile" not in result:
        result["profile"] = params.get("profile")
    return result

def maintain(root, state):
    Path(root, "maintain_state.json").write_text(json.dumps(state))
    extra = _read(root, "maintain.json", {})
    return extra if isinstance(extra, dict) else {}

def stop(root, full=False):
    path = Path(root) / "stops.json"
    calls = json.loads(path.read_text()) if path.exists() else []
    calls.append({"full": bool(full)})
    path.write_text(json.dumps(calls))
    behavior = _read(root, "stop.json", {})
    if behavior.get("raise"):
        raise RuntimeError("stop failed")
    if "stopped" in behavior:
        return {"stopped": bool(behavior["stopped"]), "full": bool(full)}
    return {"ok": True, "full": bool(full)}
"""

HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture(autouse=True)
def _reset_clock():
    yield
    set_clock(None)


def make_root(tmp_path, config=None, inspect=None, run=None):
    root = tmp_path / "root"
    root.mkdir()
    if inspect is not None:
        (root / "inspect.json").write_text(json.dumps(inspect))
    if run is not None:
        (root / "adapter_run.json").write_text(json.dumps(run))
    (root / "STOP").write_text("keep")
    base = {
        "expires_ns": 9_000_000_000_000_000_000,
        "champion_model_sha": HASH_A,
        "champion_model_path": "/Users/secret/model.pt",
        "source_root": "/Users/secret/source",
    }
    if config:
        base.update(config)
    initialize(root, base)
    return root


def drive(root, predicate, seconds=8):
    deadline = time.time() + seconds
    last = None
    while time.time() < deadline:
        last = tick(root)
        if predicate(last):
            return last
        time.sleep(0.05)
    raise AssertionError(json.dumps(last, default=str)[:2000])


def job_pid(root, job_id):
    db = control._connect(root)
    try:
        return int(db.execute("SELECT pid FROM jobs WHERE job_id=?", (job_id,)).fetchone()["pid"])
    finally:
        db.close()


def runs(root):
    path = root / "runs.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_initialize_is_durable_and_does_not_overwrite_config(adapter, tmp_path):
    root = make_root(tmp_path)
    first = json.loads((root / "config.json").read_text())
    initialize(root, {"max_training_runs": 1, "champion_model_path": "/tmp/other"})
    assert json.loads((root / "config.json").read_text()) == first
    paused = command(root, "pause_once", "pause", {})
    assert paused["ok"] is True
    again = initialize(root)
    assert again["recovered"] == []
    assert status(root)["mode"] == "paused"
    assert status(root)["source"]["unseen_test_pools"] is None
    assert status(root)["source"]["succession_pending"] is None
    assert status(root)["coverage"]["unseen_test_pools"] is None
    db = control._connect(root)
    try:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
    finally:
        db.close()


def test_idempotent_command_conflict_and_concurrent_duplicate(adapter, tmp_path):
    root = make_root(tmp_path, run={"snapshot": {"result": {"ok": True, "dataset_id": "snap1"}}})
    first = command(root, "req_snap", "snapshot", {})
    second = command(root, "req_snap", "snapshot", {})
    assert first["ok"] and second["duplicate"] is True and first["job_id"] == second["job_id"]
    clash = command(root, "req_snap", "pause", {})
    assert clash["category"] == "conflict" and clash["http_status"] == 409
    drive(root, lambda view: any(job["status"] == "completed" for job in view["jobs"]))
    barrier = threading.Barrier(2)
    results = []

    def once():
        barrier.wait()
        results.append(command(root, "req_parallel", "snapshot", {}))

    threads = [threading.Thread(target=once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(item["ok"] for item in results) == 2
    assert len({item["job_id"] for item in results}) == 1
    assert any(item["duplicate"] for item in results)
    assert len([job for job in status(root)["jobs"] if job["action"] == "snapshot"]) == 2


def test_bad_params_do_not_enqueue(adapter, tmp_path):
    root = make_root(tmp_path)
    rejected = command(root, "bad1", "train", {"snapshot_id": "../etc", "recipe": "rm"})
    assert rejected["ok"] is False
    assert rejected["reason"] in {"bad_id", "bad_params"}
    assert status(root)["jobs"] == []
    bare = command(root, "bad2", "stop", {})
    assert bare["reason"] == "not_confirmed"
    assert status(root)["mode"] == "idle"


def test_pause_lets_inflight_finish_and_blocks_new_work(adapter, tmp_path):
    root = make_root(
        tmp_path,
        run={"snapshot": {"sleep": 0.4, "result": {"ok": True, "dataset_id": "snap_pause"}}},
    )
    try:
        assert command(root, "s1", "snapshot", {})["ok"]
        tick(root)
        assert command(root, "p1", "pause", {})["mode"] == "paused"
        blocked = command(root, "s2", "snapshot", {})
        assert blocked["reason"] == "paused"
        done = drive(root, lambda view: any(job["status"] == "completed" for job in view["jobs"]))
        assert done["mode"] == "paused"
        assert (root / "STOP").read_text() == "keep"
        assert command(root, "r1", "resume", {})["mode"] == "idle"
    finally:
        recover(root)


def test_stop_and_stop_all(adapter, tmp_path):
    root = make_root(
        tmp_path, run={"snapshot": {"sleep": 30, "result": {"ok": True, "dataset_id": "slow"}}}
    )
    try:
        command(root, "s1", "snapshot", {})
        tick(root)
        running = next(job for job in status(root)["jobs"] if job["status"] == "running")
        pid = job_pid(root, running["job_id"])
        assert control.alive(pid)
        stopped = command(root, "stop1", "stop", {"confirm": True})
        assert stopped["mode"] == "stopped"
        assert json.loads((root / "stops.json").read_text()) == [{"full": False}]
        assert status(root)["jobs"][0]["status"] == "interrupted"
        assert not control.alive(pid)
        command(root, "go", "resume", {})
        command(root, "all", "stop_all", {"confirm": True})
        assert json.loads((root / "stops.json").read_text())[-1] == {"full": True}
        assert (root / "STOP").read_text() == "keep"
    finally:
        recover(root)


def test_resume_does_not_clear_domain_stop(adapter, tmp_path):
    root = make_root(tmp_path, inspect={"limits": {"domain_stop": True, "eligible": False}})
    command(root, "p", "pause", {})
    resumed = command(root, "r", "resume", {})
    assert resumed["mode"] == "idle"
    assert status(root)["domain_stop"] is True
    denied = command(root, "t", "train", {"snapshot_id": "snap1"})
    assert denied["reason"] == "domain_stop"
    assert (root / "STOP").read_text() == "keep"


def test_expiry_quota_interval_and_growth_gates(adapter, tmp_path):
    start = 10**15
    expires = 10**18
    root = make_root(
        tmp_path,
        config={"expires_ns": expires, "max_training_runs": 6, "min_refit_interval_s": 86400},
        inspect={
            "source": {"matured_samples": 10},
            "datasets": [
                {"dataset_id": "old", "snapshot_hash": HASH_A, "matured_samples": 10},
                {"dataset_id": "new", "snapshot_hash": HASH_B, "matured_samples": 10},
            ],
            "limits": {"eligible": True},
        },
        run={"train": {"result": {"ok": True, "candidate_id": "cand_old", "snapshot_id": "old"}}},
    )
    set_clock(lambda: expires)
    expired = command(root, "e", "snapshot", {})
    assert expired["reason"] == "expired"
    set_clock(lambda: start)
    assert status(root)["gates"]["runs_max"] == 6
    assert status(root)["gates"]["initial_run"] is True
    accepted = command(root, "t1", "train", {"snapshot_id": "old"})
    assert accepted["ok"] is True
    drive(root, lambda view: any(job["status"] == "completed" for job in view["jobs"]))
    view = status(root)
    assert view["gates"]["initial_run"] is False
    assert view["gates"]["runs_used"] == 1
    blocked = command(root, "t2", "train", {"snapshot_id": "new"})
    assert blocked["reason"] == "interval"
    set_clock(lambda: start + 86400 * 1_000_000_000 + 10)
    assert command(root, "same", "train", {"snapshot_id": "old"})["reason"] == "same_snapshot"
    assert command(root, "s_manual", "snapshot", {})["ok"] is True


def test_failed_train_consumes_budget_and_partial_is_not_a_candidate(adapter, tmp_path):
    root = make_root(
        tmp_path,
        config={"max_training_runs": 1, "min_refit_interval_s": 0},
        inspect={
            "source": {"matured_samples": 5},
            "datasets": [
                {"dataset_id": "one", "snapshot_hash": HASH_A},
                {"dataset_id": "two", "snapshot_hash": HASH_B},
            ],
            "limits": {"eligible": True},
        },
        run={"train": {"result": {"ok": True}}},
    )
    try:
        assert command(root, "t", "train", {"snapshot_id": "one"})["ok"]
        failed = drive(root, lambda view: view["jobs"][-1]["status"] == "failed")
        assert failed["jobs"][-1]["reason"] == "partial_result"
        assert failed["candidates"] == []
        assert failed["promotion"] is False
        assert failed["gates"]["runs_used"] == 1
        quota = command(root, "t2", "train", {"snapshot_id": "two"})
        assert quota["reason"] == "quota"
    finally:
        recover(root)


def test_promotion_flag_is_rejected(adapter, tmp_path):
    root = make_root(
        tmp_path,
        config={"min_refit_interval_s": 0},
        inspect={
            "datasets": [{"dataset_id": "one", "snapshot_hash": HASH_A}],
            "limits": {"eligible": True},
        },
        run={
            "train": {
                "result": {
                    "ok": True,
                    "candidate_id": "cand_bad",
                    "snapshot_id": "one",
                    "promoted": True,
                }
            }
        },
    )
    command(root, "t", "train", {"snapshot_id": "one"})
    view = drive(root, lambda item: item["jobs"][-1]["status"] == "failed")
    assert view["jobs"][-1]["reason"] == "invalid_result"
    assert view["candidates"] == []
    assert view["champion"]["promoted"] is False
    assert (
        assess_result(
            "train", {"snapshot_id": "one"}, {"ok": True, "candidate_id": "c", "champion": "x"}
        )[0]
        is False
    )


def test_recover_keeps_an_authentic_marker_for_the_next_tick(adapter, tmp_path):
    root = make_root(
        tmp_path,
        config={"min_refit_interval_s": 0},
        inspect={
            "datasets": [{"dataset_id": "one", "snapshot_hash": HASH_A}],
            "limits": {"eligible": True},
        },
        run={
            "train": {
                "sleep": 0,
                "result": {"ok": True, "candidate_id": "cand_crash", "snapshot_id": "one"},
            }
        },
    )
    try:
        command(root, "t", "train", {"snapshot_id": "one"})
        tick(root)
        job_id = status(root)["jobs"][-1]["job_id"]
        art = control.artifact_dir(root, job_id)
        end = time.time() + 5
        while time.time() < end and not (art / "operator-published.marker").exists():
            time.sleep(0.02)
        assert (art / "operator-published.marker").exists()
        assert job_id in recover(root)["recovered"]
        held = status(root)
        assert held["jobs"][-1]["status"] == "running"
        assert held["candidates"] == []
        published = _published(root, job_id)
        assert published == 0
        done = drive(root, lambda item: item["jobs"][-1]["status"] == "completed")
        assert done["jobs"][-1]["reason"] == "published"
        assert done["candidates"][0]["candidate_id"] == "cand_crash"
        assert [line["action"] for line in runs(root)].count("train") == 1
    finally:
        recover(root)


def test_recover_does_not_signal_a_foreign_pid(adapter, tmp_path):
    root = make_root(
        tmp_path,
        config={"min_refit_interval_s": 0},
        inspect={
            "datasets": [{"dataset_id": "one", "snapshot_hash": HASH_A}],
            "limits": {"eligible": True},
        },
        run={"train": {"sleep": 30, "result": {"ok": True, "candidate_id": "cand_live"}}},
    )
    real = None
    try:
        command(root, "t", "train", {"snapshot_id": "one"})
        tick(root)
        end = time.time() + 5
        while time.time() < end and not runs(root):
            time.sleep(0.02)
        job_id = status(root)["jobs"][-1]["job_id"]
        real = job_pid(root, job_id)
        db = control._connect(root)
        db.execute("UPDATE jobs SET pid=? WHERE job_id=?", (os.getpid(), job_id))
        db.close()
        control.lease_path(root, job_id).write_text(json.dumps({"job_id": job_id, "expires_ns": 1}))
        assert job_id in recover(root)["recovered"]
        os.kill(os.getpid(), 0)
        assert status(root)["jobs"][-1]["status"] == "interrupted"
        assert status(root)["candidates"] == []
        tick(root)
        assert [line["action"] for line in runs(root)].count("train") == 1
    finally:
        if real is not None:
            try:
                os.killpg(real, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass


def _published(root, job_id):
    db = sqlite3.connect(root / "operator.sqlite")
    row = db.execute("SELECT published FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    db.close()
    return int(row[0])


def test_deadline_kills_process_group(adapter, tmp_path, monkeypatch):
    monkeypatch.setitem(control.DEADLINES, "snapshot", 1)
    root = make_root(
        tmp_path, run={"snapshot": {"sleep": 30, "result": {"ok": True, "dataset_id": "late"}}}
    )
    try:
        command(root, "s", "snapshot", {})
        tick(root)
        time.sleep(1.2)
        view = tick(root)
        assert view["jobs"][-1]["status"] == "failed"
        assert view["jobs"][-1]["reason"] == "deadline"
        assert view["snapshots"] == []
    finally:
        recover(root)


def test_adapter_exception_stays_out_of_the_projection(adapter, tmp_path):
    root = make_root(
        tmp_path,
        config={"min_refit_interval_s": 0},
        inspect={
            "datasets": [{"dataset_id": "one", "snapshot_hash": HASH_A}],
            "limits": {"eligible": True},
        },
        run={"train": {"raise": True}},
    )
    command(root, "t", "train", {"snapshot_id": "one"})
    view = drive(root, lambda item: item["jobs"][-1]["status"] in {"failed", "interrupted"})
    text = json.dumps(view)
    assert "secret" not in text and "Traceback" not in text and "/tmp/hidden" not in text
    assert view["jobs"][-1]["reason"] == "adapter_error"
    private = (root / "operator" / "logs" / "operator.log").read_text()
    assert "Traceback" in private


def test_auto_cycle_pauses_on_failure_and_never_promotes(adapter, tmp_path):
    root = make_root(
        tmp_path,
        inspect={
            "source": {
                "provider": "geckoterminal",
                "matured_samples": 1000,
                "healthy": True,
                "freshness_s": 4,
            },
            "coverage": {"bars": 12, "pools": 2, "history": [{"ns": 5, "samples": 3, "bars": 3}]},
            "limits": {"eligible": True, "domain_stop": False},
            "paper": {
                "cash": "1000.00",
                "decisions": [
                    {"reason": "median_below_cost_buffer", "pool": "POOL1"},
                    {"action": "mpc_exit", "reason": "learned_exit", "pool": "POOL1"},
                ],
            },
            "legacy": [
                {"id": "b10", "port": 8790, "healthy": True, "path": "/Users/secret/legacy"}
            ],
        },
        run={
            "collect": {"result": {"ok": True, "profile": "balanced"}},
            "snapshot": {
                "result": {
                    "ok": True,
                    "dataset_id": "snap1",
                    "snapshot_hash": HASH_A,
                    "matured_samples": 1000,
                }
            },
            "train": {"result": {"ok": False, "reason": "inadequate_split"}},
        },
    )
    try:
        assert command(root, "auto1", "auto", {"profile": "balanced"})["mode"] == "auto"
        view = drive(
            root,
            lambda item: item["mode"] == "paused" and item["auto_reason"] == "inadequate_split",
        )
        assert view["candidates"] == []
        assert view["promotion"] is False
        assert view["engine"]["name"] == "fixed_time_15m"
        assert view["engine"]["mpc"] is False
        assert view["paper"]["decisions"][0]["action"] == "WAIT"
        assert view["paper"]["decisions"][1]["action"] is None
        blob = json.dumps(view)
        assert "/Users" not in blob and "secret" not in blob
        assert view["legacy"][0]["url"] == "http://127.0.0.1:8790"
        assert view["gates"]["runs_used"] == 1
        command(root, "again", "resume", {})
        tick(root)
        assert [line["action"] for line in runs(root)].count("train") == 1
    finally:
        recover(root)


def test_auto_happy_path_exposes_next_gate(adapter, tmp_path):
    root = make_root(
        tmp_path,
        inspect={
            "source": {
                "provider": "geckoterminal",
                "matured_samples": 1000,
                "healthy": True,
                "budget_used": 2,
                "budget_max": 9,
            },
            "coverage": {"bars": 40, "pools": 3},
            "limits": {"eligible": True},
        },
        run={
            "collect": {"result": {"ok": True, "profile": "history"}},
            "snapshot": {
                "result": {
                    "ok": True,
                    "dataset_id": "snap1",
                    "snapshot_hash": HASH_A,
                    "matured_samples": 1000,
                }
            },
            "train": {"result": {"ok": True, "candidate_id": "cand1", "snapshot_id": "snap1"}},
            "paper": {"result": {"ok": True, "exam_id": "exam1", "candidate_id": "cand1"}},
        },
    )
    try:
        command(root, "auto1", "auto", {"profile": "history"})
        view = drive(
            root,
            lambda item: (
                any(exam["exam_id"] == "exam1" for exam in item["exams"])
                and item["phase"] == "waiting"
            ),
        )
        assert view["mode"] == "auto"
        assert view["champion"]["promoted"] is False
        assert view["candidates"][0]["promoted"] is False
        assert view["gates"]["runs_used"] == 1
        assert view["gates"]["auto"]["eligible"] is False
        assert (
            "interval" in view["gates"]["auto"]["reasons"]
            or "insufficient_growth" in view["gates"]["auto"]["reasons"]
        )
        assert view["gates"]["manual"]["snapshot"]["eligible"] is True
        before = len(view["jobs"])
        for _ in range(3):
            tick(root)
        assert len(status(root)["jobs"]) == before
        listed = events(root, 0, 50)
        assert listed["events"]
        tail = events(root, listed["next_after"], 10)
        assert tail["events"] == [] or tail["events"][0]["event_id"] > listed["next_after"]
        assert map_action("enter_next_observed_close") == "ENTER"
        assert map_action("fixed_15m_exit") == "EXIT"
        assert map_action("learned_policy_exit") is None
    finally:
        recover(root)


def test_finished_child_is_completed_not_deadline(adapter, tmp_path):
    root = make_root(
        tmp_path,
        run={
            "snapshot": {
                "sleep": 0,
                "result": {"ok": True, "dataset_id": "fast", "snapshot_hash": HASH_A},
            }
        },
    )
    try:
        command(root, "s", "snapshot", {})
        view = drive(root, lambda item: item["jobs"][-1]["status"] == "completed")
        assert view["jobs"][-1]["reason"] == "published"
        assert view["snapshots"][0]["dataset_id"] == "fast"
    finally:
        recover(root)


def test_projection_keeps_whitelisted_adapter_fields(adapter, tmp_path):
    root = make_root(
        tmp_path,
        inspect={
            "source": {
                "provider": "geckoterminal",
                "matured_samples": 1200,
                "healthy": True,
                "borrowed": True,
                "profile_effective": "frozen_B09",
                "single_provider": True,
                "attempts": 3,
                "max_requests": 9,
                "heartbeat_age_s": 1.5,
                "expires_ns": 5,
                "event_days": 2,
                "receipt_days": 2,
                "observed_pools": 4,
                "first_s": 10,
                "last_s": 20,
                "per_pool": [{"pool": "POOL1", "bars": 3}],
                "quarantine": [{"pool": "POOL1"}],
                "succession_pending": False,
                "unseen_test_pools": [],
                "artifact": "/Users/secret/source",
            },
            "coverage": {
                "bars": 8,
                "pools": 4,
                "observed_pools": 4,
                "matured_samples": 1200,
                "event_days": 2,
                "receipt_days": 2,
                "first_s": 10,
                "last_s": 20,
                "per_pool": [{"pool": "POOL1", "bars": 3}],
                "unseen_test_pools": ["POOL_A", "SYN-NEW"],
                "path": "/Users/secret/coverage",
            },
            "datasets": [
                {
                    "dataset_id": "snap1",
                    "snapshot_hash": HASH_A,
                    "dataset_sha": HASH_A,
                    "matured_samples": 1200,
                    "as_of_ns": 7,
                    "recipe": {"family": "linear"},
                    "retained": {"pools": 2},
                    "original": {"pools": 4},
                    "archive": {"days": 1},
                    "artifact": "/Users/secret/snap",
                }
            ],
            "candidates": [
                {
                    "candidate_id": "cand1",
                    "dataset_id": "snap1",
                    "model_sha": HASH_B,
                    "dataset_sha": HASH_A,
                    "selected": {"family": "linear"},
                    "validation": {"pool_macro_pinball": 0.2},
                    "test": {"score": 0.1},
                    "support": {"pools": 2},
                    "recipe": {"name": "v1"},
                    "finished_ns": 9,
                    "promoted": False,
                }
            ],
            "paper": {
                "cash": "1000",
                "equity": "1000",
                "realized": "1.5",
                "unrealized": "0",
                "fees": "0.25",
                "books": [{"name": "fixed"}],
                "forecast_scores": [{"pool": "POOL1", "score": 0.1}],
                "positions": [{"pool": "POOL1", "qty": "1", "status": "pending"}],
                "decisions": [
                    {
                        "action": "ENTER",
                        "reason": "enter_next_observed_close",
                        "stage": "intent",
                        "filled": False,
                        "q": {"median": 0.1},
                        "pool": "POOL1",
                        "market": "synthetic",
                    }
                ],
            },
            "legacy": [
                {"id": "b10", "port": 8790, "healthy": True, "root": "/Users/secret/b10"},
                {
                    "id": "recovered",
                    "label": "b12r",
                    "port": 8793,
                    "healthy": True,
                    "source": {"healthy": False},
                },
            ],
            "training_progress": [{"step": 2, "steps": 5, "checkpoint_id": "fit1"}],
            "policy": {"name": "fixed", "holding_minutes": 15, "champion": "empirical"},
            "limits": {"eligible": True, "domain_stop": False},
        },
    )
    view = status(root)
    blob = json.dumps(view)
    assert "/Users" not in blob and "secret" not in blob
    assert view["coverage"]["per_pool"][0]["bars"] == 3
    assert view["coverage"]["event_days"] == 2
    assert view["source"]["profile_effective"] == "frozen_B09"
    assert view["source"]["borrowed"] is True
    assert view["source"]["succession_pending"] is False
    assert view["source"]["unseen_test_pools"] == []
    assert view["coverage"]["unseen_test_pools"] == ["POOL_A", "SYN-NEW"]
    assert isinstance(view["host"], dict)
    assert view["stop_effects"]["phase"] == "none"
    assert view["snapshots"][0]["retained"]["pools"] == 2
    assert view["snapshots"][0]["dataset_sha"] == HASH_A
    assert view["candidates"][0]["model_sha"] == HASH_B
    assert view["candidates"][0]["status"] == "unknown"
    assert view["candidates"][0]["promoted"] is False
    assert view["source"]["hot_pools"] is None
    assert view["recovery"] is None
    assert "synthetic" not in view
    assert "data_kind" not in view
    assert view["paper"]["realized"] == "1.5"
    assert view["paper"]["decisions"][0]["stage"] == "intent"
    assert view["paper"]["decisions"][0]["filled"] is False
    assert view["paper"]["decisions"][0]["action"] == "ENTER"
    assert view["legacy"][1]["url"] == "http://127.0.0.1:8793"
    assert view["legacy"][1]["source_healthy"] is False
    assert "root" not in view["legacy"][0]
    assert view["training"]["step"] == 2
    assert view["training"]["checkpoint_id"] == "fit1"
    assert view["policy"]["learned_exit"] is False
    assert view["block_reasons"]
    (root / "maintain.json").write_text(
        json.dumps(
            {"ok": True, "training_progress": {"step": 3, "steps": 5, "checkpoint_id": "fit2"}}
        )
    )
    tick(root)
    maintained = json.loads((root / "maintain_state.json").read_text())
    assert maintained["phase"] == status(root)["phase"]
    assert status(root)["training"]["step"] == 3


def test_completed_training_elapsed_stays_at_finish(adapter, tmp_path):
    root = make_root(
        tmp_path,
        config={"min_refit_interval_s": 0},
        inspect={
            "datasets": [{"dataset_id": "one", "snapshot_hash": HASH_A}],
            "limits": {"eligible": True},
        },
        run={"train": {"sleep": 0, "result": {"ok": True, "candidate_id": "cand_fast"}}},
    )
    try:
        command(root, "t", "train", {"snapshot_id": "one"})
        done = drive(root, lambda item: item["jobs"][-1]["status"] == "completed")
        first = done["training"]["elapsed_s"]
        assert first is not None
        time.sleep(1.1)
        assert status(root)["training"]["elapsed_s"] == first
    finally:
        recover(root)


def _stop_calls(root):
    path = root / "stops.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def test_stop_survives_crash_before_adapter_and_blocks_resume(adapter, tmp_path, monkeypatch):
    root = make_root(tmp_path)
    original = control._dispatch_stop
    monkeypatch.setattr(control, "_dispatch_stop", lambda path: None)
    accepted = command(root, "all", "stop_all", {"confirm": True})
    assert accepted["ok"] is True
    assert accepted["mode"] == "stopped"
    held = status(root)["stop_effects"]
    assert held["phase"] == "pending"
    assert held["confirmed"] is False
    assert held["attempts"] == 0
    assert _stop_calls(root) == []
    denied = command(root, "resume-early", "resume", {})
    assert denied["ok"] is False
    assert denied["reason"] == "stop_pending"
    assert status(root)["mode"] == "stopped"
    monkeypatch.setattr(control, "_dispatch_stop", original)
    initialize(root)
    applied = status(root)["stop_effects"]
    assert applied["phase"] == "applied"
    assert applied["confirmed"] is True
    assert applied["needs_operator"] is False
    assert _stop_calls(root) == [{"full": True}]
    assert (root / "STOP").read_text() == "keep"
    assert command(root, "resume-late", "resume", {})["mode"] == "idle"


def test_stop_false_and_throw_keep_intent_until_retry(adapter, tmp_path):
    root = make_root(tmp_path)
    (root / "stop.json").write_text(json.dumps({"stopped": False}))
    refused = command(root, "all-false", "stop_all", {"confirm": True})
    assert refused["ok"] is True
    failed = status(root)["stop_effects"]
    assert failed["phase"] == "failed"
    assert failed["confirmed"] is False
    assert failed["attempts"] == 3
    assert failed["reason"] == "not_confirmed"
    assert failed["needs_operator"] is True
    assert len(failed["attempt_ns"]) == 3
    assert len(_stop_calls(root)) == 3
    reasons = {item["reason"] for item in events(root, 0, 200)["events"]}
    assert "stop_failed" in reasons
    assert "stop_applied" not in reasons
    again = command(root, "all-false", "stop_all", {"confirm": True})
    assert again["duplicate"] is True
    assert len(_stop_calls(root)) == 3
    blocked = command(root, "resume-failed", "resume", {})
    assert blocked["reason"] == "stop_failed"
    (root / "stop.json").write_text(json.dumps({"raise": True}))
    command(root, "all-throw", "stop_all", {"confirm": True})
    thrown = status(root)["stop_effects"]
    assert thrown["phase"] == "failed"
    assert thrown["reason"] == "adapter_error"
    assert thrown["confirmed"] is False
    assert len(_stop_calls(root)) == 6
    (root / "stop.json").write_text(json.dumps({"stopped": True}))
    retried = command(root, "all-retry", "stop_all", {"confirm": True})
    assert retried["ok"] is True
    assert retried["duplicate"] is False
    assert status(root)["stop_effects"]["phase"] == "applied"
    assert status(root)["stop_effects"]["confirmed"] is True
    assert len(_stop_calls(root)) == 7


def test_applied_stop_is_idempotent(adapter, tmp_path):
    root = make_root(tmp_path)
    assert command(root, "all", "stop_all", {"confirm": True})["reason"] == "stop_all"
    tick(root)
    assert _stop_calls(root) == [{"full": True}]
    second = command(root, "all-2", "stop_all", {"confirm": True})
    assert second["reason"] == "already_stopped"
    assert _stop_calls(root) == [{"full": True}]
    duplicate = command(root, "all", "stop_all", {"confirm": True})
    assert duplicate["duplicate"] is True
    assert status(root)["stop_effects"]["confirmed"] is True


def test_inflight_stop_ack_cannot_confirm_a_newer_request(adapter, tmp_path, monkeypatch):
    root = make_root(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    calls = []

    class Adapter:
        def inspect(self, _root):
            return {"limits": {"eligible": True}}

        def stop(self, _root, full=False):
            calls.append(bool(full))
            if len(calls) == 1:
                entered.set()
                assert release.wait(5)
                return {"stopped": True}
            return {"stopped": False}

        def maintain(self, _root, state):
            return {"ok": True}

        def run(self, _root, action, params, output):
            return {"ok": True}

    monkeypatch.setattr(control, "load_adapter", lambda: Adapter())
    first = threading.Thread(
        target=lambda: command(root, "first-stop", "stop_all", {"confirm": True})
    )
    first.start()
    assert entered.wait(5)
    second = threading.Thread(
        target=lambda: command(root, "second-stop", "stop_all", {"confirm": True})
    )
    second.start()
    deadline = time.time() + 5
    while time.time() < deadline and status(root)["stop_effects"]["request_id"] != "second-stop":
        time.sleep(0.02)
    assert status(root)["stop_effects"]["request_id"] == "second-stop"
    assert status(root)["stop_effects"]["confirmed"] is False
    release.set()
    first.join(5)
    second.join(5)
    effect = status(root)["stop_effects"]
    assert effect["confirmed"] is False
    assert effect["request_id"] == "second-stop"
    assert effect["phase"] == "failed"
    assert effect["reason"] == "not_confirmed"
    assert status(root)["mode"] == "stopped"


def test_projection_keeps_incomplete_candidates_and_domain_facts(adapter, tmp_path):
    root = make_root(
        tmp_path,
        inspect={
            "synthetic": True,
            "data_kind": "synthetic",
            "source": {"hot_pools": ["SYN01", "SYN02"], "provider": "geckoterminal"},
            "recovery": {
                "paused": True,
                "resume_attempts": 2,
                "retry_after_ns": 50,
                "services": [
                    {
                        "label": "ai.memecoin.brain.b12r2.paper",
                        "loaded": False,
                        "reason": "bootout_failed",
                    },
                    {"label": "/Users/secret/label", "loaded": True, "reason": "ok"},
                ],
                "traceback": "/Users/secret/trace",
            },
            "incidents": [
                {
                    "id": "inc1",
                    "kind": "service_resume",
                    "reason": "bootout_failed",
                    "at_ns": 9,
                    "success": False,
                    "stopped": True,
                    "traceback": "Traceback /Users/secret",
                    "path": "/Users/secret/incidents/inc1.json",
                },
                {"id": "../etc", "kind": "leak", "reason": "leak"},
            ],
            "candidates": [
                {"candidate_id": "keep", "status": "incomplete"},
                {"candidate_id": "bad", "status": "failed", "model_sha": HASH_A},
                {"candidate_id": "odd", "status": "banana"},
                {"candidate_id": "ready_bare", "status": "ready"},
                {"candidate_id": "ready_gap", "status": "ready", "model_sha": None},
                {"candidate_id": "done_gap", "status": "completed", "model_sha": None},
                {
                    "candidate_id": "ready_ok",
                    "status": "ready",
                    "model_sha": HASH_A,
                    "support": {"pools": 2},
                },
                {"candidate_id": "stub", "status": "completed"},
            ],
        },
    )
    db = control._connect(root)
    db.execute("BEGIN IMMEDIATE")
    control._event(db, "incident", "adapter_error", {"job_id": "job1"}, 4)
    db.execute("COMMIT")
    db.close()
    view = status(root)
    blob = json.dumps(view)
    assert "/Users" not in blob and "traceback" not in blob and "secret" not in blob
    by_id = {item["candidate_id"]: item["status"] for item in view["candidates"]}
    assert by_id["keep"] == "incomplete"
    assert by_id["bad"] == "failed"
    assert by_id["odd"] == "unknown"
    assert by_id["ready_bare"] == "incomplete"
    assert by_id["ready_gap"] == "incomplete"
    assert by_id["done_gap"] == "incomplete"
    assert by_id["ready_ok"] == "ready"
    assert by_id["stub"] == "completed"
    ready = next(item for item in view["candidates"] if item["candidate_id"] == "ready_ok")
    assert ready["support"]["pools"] == 2
    assert view["synthetic"] is True
    assert view["data_kind"] == "synthetic"
    assert view["source"]["hot_pools"] == ["SYN01", "SYN02"]
    assert view["recovery"]["paused"] is True
    assert view["recovery"]["resume_attempts"] == 2
    assert view["recovery"]["retry_after_ns"] == 50
    assert view["recovery"]["services"][0]["label"] == "ai.memecoin.brain.b12r2.paper"
    assert view["recovery"]["services"][0]["loaded"] is False
    assert view["recovery"]["services"][0]["reason"] == "bootout_failed"
    assert view["recovery"]["services"][1]["label"] is None
    domain = [item for item in view["incidents"] if item.get("origin") == "domain"]
    assert domain[0]["id"] == "inc1"
    assert domain[0]["success"] is False
    assert domain[0]["stopped"] is True
    assert any(item.get("reason") == "adapter_error" for item in view["incidents"])
    loaded = control._load_view(root)
    db = control._connect(root)
    known = control._known_candidates(db, loaded)
    db.close()
    assert "ready_ok" in known and "stub" in known
    assert "ready_gap" not in known and "ready_bare" not in known
    assert "done_gap" not in known and "keep" not in known and "bad" not in known
    denied = command(root, "p-gap", "paper", {"candidate_id": "ready_gap"})
    assert denied["ok"] is False and denied["reason"] == "missing_candidate"
    opened = command(root, "p-ok", "paper", {"candidate_id": "ready_ok"})
    assert opened["ok"] is True
    stored = assess_result(
        "train",
        {"snapshot_id": "snap1"},
        {"ok": True, "candidate_id": "candx", "snapshot_id": "snap1", "model_sha": HASH_B},
    )
    assert stored[0] is True and stored[2]["model_sha"] == HASH_B


def test_corrupt_stop_effect_blocks_until_a_new_stop(adapter, tmp_path):
    root = make_root(tmp_path)
    raw = (
        '{"phase":"applied","kind":"everywhere","confirmed":true,'
        '"attempts":2,"attempt_ns":[5],"request_id":"old-stop"}'
    )
    db = control._connect(root)
    control._meta_set(db, "stop_effect", raw)
    db.close()
    view = status(root)
    assert view["stop_effects"]["phase"] == "failed"
    assert view["stop_effects"]["confirmed"] is False
    assert view["stop_effects"]["needs_operator"] is True
    assert view["stop_effects"]["reason"] == "stop_effect_invalid"
    assert view["stop_effects"]["attempts"] == 2
    assert view["stop_effects"]["attempt_ns"] == [5]
    assert view["stop_effects"]["request_id"] == "old-stop"
    db = control._connect(root)
    assert control._meta_get(db, "stop_effect") == raw
    db.close()
    denied = command(root, "go", "resume")
    assert denied["ok"] is False and denied["reason"] == "stop_failed"
    other = tmp_path / "other"
    other.mkdir()
    broken = make_root(other)
    db = control._connect(broken)
    control._meta_set(db, "stop_effect", "{")
    db.close()
    assert status(broken)["stop_effects"]["phase"] == "failed"
    assert status(broken)["stop_effects"]["confirmed"] is False
    db = control._connect(broken)
    assert control._meta_get(db, "stop_effect") == "{"
    db.close()
    fresh = command(root, "again", "stop_all", {"confirm": True})
    assert fresh["ok"] is True
    effect = status(root)["stop_effects"]
    assert effect["phase"] == "applied" and effect["confirmed"] is True
    db = control._connect(root)
    stored = control._meta_get(db, "stop_effect")
    db.close()
    assert stored != raw
    assert json.loads(stored)["phase"] == "applied"


def test_actions_and_clock_helpers():
    assert map_action("WAIT") == "WAIT"
    assert map_action("predicted_tail_risk") == "WAIT"
    assert map_action("mystery") is None
    set_clock(lambda: 123)
    assert now_ns() == 123
    set_clock(None)
    assert now_ns() > 123
