"""Two AUTO cycles through the real control loop.

Collect, snapshot and paper run the real adapter on a synthetic archive.
Train is the explicit stub below: no torch fit and no claim of a market result.
Manager.start only records commands. No provider, launchctl, or live port.
"""

import json
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path

import pytest

from memecoin_continuous.store import connect, ingest
from memecoin_ml.data import sha
from memecoin_operator import adapters, control
from memecoin_operator.background import paper_command
from memecoin_operator.control import (
    artifact_dir,
    command,
    initialize,
    recover,
    set_clock,
    status,
    tick,
)
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish

NS = 10**9
DAY = 86400 * NS
STUB = "explicit-stub-learning"
STARTS: list[dict] = []

STUB_SOURCE = """
LEARNING = "explicit-stub-learning"

def run(root, action, params, output):
    import os
    if action == "train" and os.environ.get("MEMECOIN_STUB_TRAIN") == "fail":
        return {"ok": False, "reason": "fit_failed"}
    if action != "train":
        from memecoin_operator.adapters import run as real_run
        return real_run(root, action, params, output)
    import time
    from pathlib import Path
    from memecoin_ml.data import sha
    from memecoin_operator.adapters import register, registered
    from memecoin_registry.contracts import seal
    from memecoin_runner.ledger import publish
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifact = output / "artifact"
    artifact.mkdir(exist_ok=False)
    row, _dataset = registered(root, "datasets", params["snapshot_id"])
    model = artifact / "model"
    model.mkdir()
    finished = time.time_ns() - 5 * 10**9
    publish(model / "model-card.json", dict(
        learning=LEARNING, fits=0, selected="empirical",
        training_as_of_ns=finished - 10**9, finished_ns=finished,
        confirmatory_F01=False, production_trade_authorized=False,
        prospective_shadow_required=True,
    ))
    publish(model / "synthetic.json", dict(learning=LEARNING, trained=False))
    seal(model)
    result = dict(
        status="completed", learning=LEARNING, trained=False, new_fits=0,
        promoted=False, dataset_id=row["id"], dataset_sha=row["dataset_sha"],
        snapshot_id=row["id"], model_sha=sha(model / "manifest.json"),
        selected="empirical", finished_ns=finished,
        validation={"empirical": {"pool_macro_pinball": 1.0}},
    )
    publish(artifact / "result.json", result)
    seal(artifact)
    entry = register(root, "candidates", artifact, result)
    return dict(candidate_id=entry["id"], **entry)

def __getattr__(name):
    from memecoin_operator import adapters as real
    return getattr(real, name)
"""


class Clock:
    def __init__(self) -> None:
        self.now = time.time_ns()

    def __call__(self) -> int:
        return self.now


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    set_clock(None)
    monkeypatch.delenv("MEMECOIN_OPERATOR_ADAPTER", raising=False)
    monkeypatch.delenv("MEMECOIN_STUB_TRAIN", raising=False)
    monkeypatch.setattr(control, "MAINTAIN_EVERY_S", 10**12)
    STARTS.clear()

    def record_start(self, key, command, seconds):
        STARTS.append({"key": key, "command": list(command), "seconds": seconds})
        return {"started": False, "key": key, "reason": "recorded_not_started"}

    monkeypatch.setattr("memecoin_operator.background.Manager.start", record_start)
    yield
    set_clock(None)
    for proc in list(control._CHILDREN.values()):
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)


def ohlcv(i):
    close = 1.0 + (i % 23) * 0.001
    open_ = close * 0.999
    high = close * 1.002
    low = min(open_, close) * 0.998
    return [open_, high, low, close, 10.0 + (i % 7)]


def capture(path, opens):
    path.mkdir(parents=True)
    raw = [[t, *ohlcv(t)] for t in opens]
    received = (max(opens) + 300) * NS
    publish(
        path / "request.json",
        dict(started_ns=received - 2, lane="historical", url="synthetic"),
    )
    (path / "response.bin").write_text(json.dumps(dict(data=dict(attributes=dict(ohlcv_list=raw)))))
    publish(path / "receipt.json", dict(received_ns=received, available_ns=received + 2))
    seal(path)
    return path


def source_config(expires_ns):
    return dict(
        expires_ns=expires_ns,
        history_floor_s=1_700_000_000,
        hot_pools=list("abcdef"),
        hot_interval_s=90,
        cold_interval_s=900,
        request_spacing_s=30,
        max_requests=25000,
        paid_budget_usd=0,
        http_timeout_s=20,
    )


def seal_source(source: Path, expires_ns: int) -> Path:
    source = source.resolve()
    source.mkdir(parents=True)
    publish(source / "config.json", source_config(expires_ns))
    bundle = source / "runtime-bundle"
    bundle.mkdir()
    publish(
        bundle / "binding.json",
        dict(
            root=str(source),
            python_sha=sha(Path(sys.executable).resolve()),
            files={"config.json": sha(source / "config.json")},
        ),
    )
    publish(bundle / "marker.json", dict(synthetic=True))
    seal(bundle)
    publish(
        source / "deployment.json",
        dict(bundle=str(bundle.resolve()), pin=sha(bundle / "manifest.json")),
    )
    connect(source / "index.sqlite").close()
    publish(source / "health.json", dict(at_ns=time.time_ns(), attempts=1, cooldown_ns=0))
    return source


def fill_archive(source: Path) -> int:
    end = time.time_ns() // NS // 300 * 300 - 300
    old = list(range(end - 60 * 3600, end, 300))
    young = list(range(end - 12000, end, 300))
    db = connect(source / "index.sqlite")
    ingest(db, capture(source / "captures" / "old", old), "oldpool")
    ingest(db, capture(source / "captures" / "young", young), "youngpool")
    db.close()
    return end


def extend_archive(source: Path, start: int, bars: int) -> None:
    opens = [start + i * 300 for i in range(bars)]
    db = connect(source / "index.sqlite")
    ingest(db, capture(source / "captures" / f"more-{start}", opens), "oldpool")
    db.close()
    refresh_health(source)


def refresh_health(source: Path) -> None:
    path = source / "health.json"
    payload = json.loads(path.read_text())
    payload["at_ns"] = time.time_ns()
    path.unlink()
    publish(path, payload)


def operator_config(source: Path, expires_ns: int, **extra) -> dict:
    payload = dict(
        product="operator",
        mode="operator",
        provider="geckoterminal",
        profile="balanced",
        init_ns=time.time_ns() - NS,
        real_execution_enabled=False,
        paid_budget_usd=0,
        additional_requests=0,
        max_background_requests=6000,
        expires_ns=expires_ns,
        source_root=str(source),
        champion_model_path=str((source / "champion").resolve()),
        champion_model_sha="ab" * 32,
        max_training_runs=6,
        min_refit_interval_s=86400,
        minimum_new_matured=100,
    )
    payload.update(extra)
    return payload


def build(tmp: Path, *, archive: bool, **extra):
    horizon = time.time_ns() + 6 * DAY
    source = seal_source(tmp / "source", horizon)
    end = fill_archive(source) if archive else None
    root = tmp / "op"
    root.mkdir()
    publish(root / "config.json", operator_config(source, horizon, **extra))
    initialize(root)
    return root, source, end


def rewrite(path: Path, **updates) -> None:
    payload = json.loads(path.read_text())
    payload.update(updates)
    path.unlink()
    path.write_text(json.dumps(payload, sort_keys=True))


def connect_db(root: Path) -> sqlite3.Connection:
    db = sqlite3.connect(root / "operator.sqlite")
    db.row_factory = sqlite3.Row
    return db


def jobs(root: Path) -> list[dict]:
    db = connect_db(root)
    try:
        return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY rowid")]
    finally:
        db.close()


def cycle_of(root: Path) -> dict:
    db = connect_db(root)
    try:
        row = db.execute(
            "SELECT cycle_json FROM revisions ORDER BY revision DESC LIMIT 1"
        ).fetchone()
    finally:
        db.close()
    return json.loads(row["cycle_json"])


def install_stub(tmp: Path, monkeypatch) -> None:
    path = tmp / "explicit_stub_learning.py"
    path.write_text(STUB_SOURCE)
    monkeypatch.setenv("MEMECOIN_OPERATOR_ADAPTER", str(path))


def pump(root: Path, done, timeout: float = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        tick(root)
        if done():
            return
        time.sleep(0.05)
    rows = jobs(root)
    logs = []
    for row in rows:
        if row["status"] == "completed":
            continue
        log = artifact_dir(root, row["job_id"]) / "worker.log"
        if log.exists():
            logs.append(log.read_text(errors="replace")[-1500:])
    raise AssertionError((rows, status(root)["gates"], logs))


def completed(root: Path) -> list[str]:
    return [row["action"] for row in jobs(root) if row["status"] == "completed"]


def idle(root: Path) -> bool:
    return not any(row["status"] in {"queued", "running"} for row in jobs(root))


def gate(body: dict, name: str) -> dict:
    return next(item for item in body["gates"]["checks"] if item["id"] == name)


def await_growth(root: Path, prior: int, minimum: int, timeout: float = 20) -> dict:
    deadline = time.time() + timeout
    last = status(root)["gates"]
    while time.time() < deadline:
        last = status(root)["gates"]
        now_m = last["matured_now"]
        if now_m is not None and now_m - prior >= minimum:
            return last
        time.sleep(0.25)
    return last


def test_failed_reservation_keeps_the_quota(tmp_path, monkeypatch):
    clock = Clock()
    set_clock(clock)
    root, _source, _end = build(tmp_path, archive=True, max_training_runs=1)
    install_stub(tmp_path, monkeypatch)
    monkeypatch.setenv("MEMECOIN_STUB_TRAIN", "fail")
    assert command(root, "req_auto", "auto", {"profile": "balanced"})["ok"] is True
    pump(
        root,
        lambda: (
            any(row["action"] == "train" and row["status"] == "failed" for row in jobs(root))
            and idle(root)
        ),
    )
    train = next(row for row in jobs(root) if row["action"] == "train")
    reserved = train["reserved_ns"]
    assert train["error_reason"] == "fit_failed"
    assert train["published"] == 0
    assert reserved is not None
    body = status(root)
    assert body["mode"] == "paused"
    assert body["auto_reason"] == "fit_failed"
    assert body["gates"]["runs_used"] == 1
    assert body["gates"]["runs_max"] == 1
    assert gate(body, "quota")["pass"] is False
    assert body["gates"]["initial_collect_done"] is True
    assert completed(root) == ["collect", "snapshot"]
    resumed = command(root, "req_resume", "resume")
    assert resumed["ok"] is True and resumed["mode"] == "auto"
    tick(root)
    assert jobs(root)[-1]["job_id"] == train["job_id"]
    clock.now = reserved + DAY
    tick(root)
    assert [row["job_id"] for row in jobs(root) if row["action"] == "train"] == [train["job_id"]]
    assert next(row["reserved_ns"] for row in jobs(root) if row["action"] == "train") == reserved
    later = status(root)
    assert later["mode"] == "auto"
    assert later["gates"]["runs_used"] == 1
    assert gate(later, "quota")["pass"] is False
    assert "quota" in later["gates"]["auto"]["reasons"]
    assert later["domain_stop"] is False


def test_expiry_window_drains_without_a_domain_stop(tmp_path):
    root, source, _end = build(tmp_path, archive=False)
    now = time.time_ns()
    specs = (
        ("near", now + 30 * NS),
        ("held", now + 300 * NS),
        ("gone", now - NS),
    )
    (root / "exams").mkdir()
    for name, expires in specs:
        exam = root / "paper" / name
        exam.mkdir(parents=True)
        publish(
            exam / "config.json",
            dict(
                activation_ns=now,
                exam_start_ns=now,
                expires_ns=expires,
                source=str(source),
                training_max_runs=0,
                real_execution_enabled=False,
                frozen_exam_models=[],
            ),
        )
        bundle = root / "runtimes" / name
        bundle.mkdir(parents=True)
        publish(exam / "deployment.json", dict(bundle=str(bundle.resolve()), pin="cd" * 32))
        publish(
            root / "exams" / f"{name}.json",
            dict(
                id=name,
                exam_id=name,
                candidate_id="cand" + name,
                status="active",
                activation_ns=now,
                expires_ns=expires,
                exam_root=str(exam),
                artifact=str(exam),
                manifest_sha="ab" * 32,
                created_ns=now,
            ),
        )
    maintained = adapters.maintain(root, {"mode": "auto", "phase": "waiting"})
    assert maintained["status"] == "running"
    keys = [item["key"] for item in STARTS]
    assert keys == ["exam-held"]
    assert STARTS[0]["seconds"] == 150
    runtime = Path(json.loads((root / "paper" / "held" / "deployment.json").read_text())["bundle"])
    assert STARTS[0]["command"] == paper_command(runtime / "runtime", root / "paper" / "held")
    assert list(root.rglob("STOP")) == []
    view = adapters.inspect(root)
    assert view["limits"]["domain_stop"] is False
    assert {item["exam_id"]: item["status"] for item in view["exams"]}["gone"] == "expired"
    assert view["policy"]["automatic_promotion"] is False
    opened = command(root, "req_auto", "auto", {"profile": "balanced"})
    assert opened["ok"] is True and opened["reason"] == "accepted"
    tick(root)
    body = status(root)
    assert body["mode"] == "auto"
    assert body["domain_stop"] is False
    assert body["promotion"] is False
    assert body["auto_reason"] is None
    assert [row["action"] for row in jobs(root)] == ["collect"]
    assert list(root.rglob("STOP")) == []


def test_auto_two_cycles_survive_restart_and_the_day_boundary(tmp_path, monkeypatch):
    clock = Clock()
    set_clock(clock)
    root, source, end = build(tmp_path, archive=True)
    install_stub(tmp_path, monkeypatch)
    assert command(root, "req_auto", "auto", {"profile": "balanced"})["ok"] is True
    pump(root, lambda: completed(root) == ["collect", "snapshot", "train", "paper"] and idle(root))
    rows = jobs(root)
    assert [row["origin"] for row in rows] == ["auto"] * 4
    assert [row["status"] for row in rows] == ["completed"] * 4
    snapshot_job, train_job, paper_job = rows[1], rows[2], rows[3]
    report = json.loads(
        (artifact_dir(root, snapshot_job["job_id"]) / "artifact" / "result.json").read_text()
    )
    assert report["raw_audit"]["exact"] is True
    assert report["historical_test_independent"] is False
    assert report["new_fits"] == 0
    card_path = artifact_dir(root, train_job["job_id"]) / "artifact" / "model" / "model-card.json"
    card = json.loads(card_path.read_text())
    assert card["learning"] == STUB
    assert card["confirmatory_F01"] is False
    assert card["production_trade_authorized"] is False
    train_result = json.loads(
        (artifact_dir(root, train_job["job_id"]) / "operator-result.json").read_text()
    )
    assert train_result["trained"] is False
    assert train_result["promoted"] is False
    assert train_result["new_fits"] == 0
    exam = root / "paper" / paper_job["job_id"]
    exam_config = json.loads((exam / "config.json").read_text())
    activation = json.loads((exam / "activation.json").read_text())
    assert card["finished_ns"] < exam_config["exam_start_ns"] == exam_config["activation_ns"]
    assert exam_config["training_max_runs"] == 0
    assert exam_config["real_execution_enabled"] is False
    assert exam_config["expires_ns"] - exam_config["activation_ns"] == DAY
    assert activation["promotion"] is False
    assert activation["no_backfill"] is True
    assert not (exam / "STOP").exists()
    reserved = train_job["reserved_ns"]
    assert status(root)["gates"]["not_before_ns"] == reserved + DAY

    control._CHILDREN.clear()
    recovered = recover(root)
    assert recovered == {"ok": True, "recovered": []}
    restarted = status(root)
    assert restarted["mode"] == "auto"
    assert restarted["active_exam_id"] == paper_job["exam_id"]
    assert restarted["promotion"] is False
    assert restarted["domain_stop"] is False
    assert cycle_of(root)["expect"] == "snapshot"
    assert cycle_of(root)["candidate_id"] is None

    clock.now = reserved + DAY - 1
    tick(root)
    assert completed(root) == ["collect", "snapshot", "train", "paper"]
    early = status(root)
    assert gate(early, "interval")["pass"] is False
    assert "interval" in early["gates"]["auto"]["reasons"]
    assert early["mode"] == "auto"

    delta = early["gates"]["matured_delta"]
    prior = early["gates"]["matured_prior"]
    rewrite(root / "config.json", minimum_new_matured=(0 if delta is None else delta) + 1)
    clock.now = reserved + DAY
    tick(root)
    assert len(jobs(root)) == 4
    blocked = status(root)
    assert gate(blocked, "interval")["pass"] is True
    assert blocked["gates"]["auto"]["reasons"][0] in {"insufficient_growth", "unknown_growth"}
    same = command(root, "req_same", "train", {"snapshot_id": snapshot_job["dataset_id"]})
    assert same["ok"] is False and same["reason"] == "same_snapshot"

    rewrite(root / "config.json", minimum_new_matured=100)
    if prior is None or (delta or 0) < 100:
        bars = 100 - (delta or 0) + 40
        extend_archive(source, end - 60 * 3600 - bars * 300, bars)
        grown = await_growth(root, prior, 100)
        assert grown["matured_now"] - prior >= 100, grown
    refresh_health(source)
    pump(
        root,
        lambda: (
            completed(root)
            == ["collect", "snapshot", "train", "paper", "snapshot", "train", "paper"]
            and idle(root)
        ),
    )
    second = jobs(root)
    assert [row["origin"] for row in second] == ["auto"] * 7
    first_candidate = paper_job["candidate_id"]
    papers = [row for row in second if row["action"] == "paper"]
    assert papers[0]["candidate_id"] == first_candidate
    assert papers[1]["candidate_id"] != first_candidate
    assert papers[1]["exam_id"] != papers[0]["exam_id"]
    assert [row["candidate_id"] for row in papers].count(first_candidate) == 1
    second_exam = json.loads((root / "paper" / papers[1]["job_id"] / "config.json").read_text())
    second_card = json.loads(
        (
            artifact_dir(root, second[5]["job_id"]) / "artifact" / "model" / "model-card.json"
        ).read_text()
    )
    assert second_card["finished_ns"] < second_exam["activation_ns"]
    assert second_card["confirmatory_F01"] is False
    assert second_exam["training_max_runs"] == 0
    final = status(root)
    assert final["mode"] == "auto"
    assert final["promotion"] is False
    assert final["champion"]["promoted"] is False
    assert final["domain_stop"] is False
    assert final["gates"]["runs_used"] == 2
    # The scheduler issued one exam for this candidate. A direct command still queues another.
    repeat = command(root, "req_repeat", "paper", {"candidate_id": first_candidate})
    assert repeat["ok"] is True and repeat["reason"] == "accepted"
    queued = jobs(root)[-1]
    assert queued["status"] == "queued" and queued["origin"] == "manual"
    assert queued["action"] == "paper"
    assert json.loads(queued["params_json"])["candidate_id"] == first_candidate
