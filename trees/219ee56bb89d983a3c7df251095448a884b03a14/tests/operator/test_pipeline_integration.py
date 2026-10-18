"""Control worker plus the real adapter. Train in this file is an explicit stub.

The four-fit protocol stays in test_learning_ml.py. A separate optional torch test
checks code hashes and inference identity on a ten-step recipe. No exam process is started.
"""

import hashlib
import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from memecoin_continuous.store import audit, connect, ingest
from memecoin_ml.data import read, sha
from memecoin_operator import adapters, control
from memecoin_operator.control import (
    artifact_dir,
    command,
    initialize,
    recover,
    set_clock,
    tick,
)
from memecoin_registry.contracts import seal, verify
from memecoin_runner.ledger import publish

NS = 10**9
STUB = "explicit-stub-learning"
STARTS: list[dict] = []


@pytest.fixture(autouse=True)
def quiet_supervisor(monkeypatch):
    set_clock(None)
    monkeypatch.delenv("MEMECOIN_OPERATOR_ADAPTER", raising=False)
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


def b09_config(expires_ns: int, **extra) -> dict:
    """Same source keys the acquisition builder requires, plus the 20s timeout."""
    payload = dict(
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
    payload.update(extra)
    return {key: value for key, value in payload.items() if value is not None}


def seal_source(source: Path, expires_ns: int, **extra) -> Path:
    source = source.resolve()
    source.mkdir(parents=True)
    publish(source / "config.json", b09_config(expires_ns, **extra))
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


def fill_archive(source: Path) -> None:
    end = time.time_ns() // NS // 300 * 300 - 300
    old = list(range(end - 60 * 3600, end, 300))
    young = list(range(end - 12000, end, 300))
    db = connect(source / "index.sqlite")
    ingest(db, capture(source / "captures" / "old", old), "oldpool")
    ingest(db, capture(source / "captures" / "young", young), "youngpool")
    db.close()


def bind_source(source: Path, expires_ns: int) -> Path:
    sealed = seal_source(source, expires_ns)
    fill_archive(sealed)
    return sealed


def operator_root(tmp_path: Path) -> Path:
    now = time.time_ns()
    source = bind_source(tmp_path / "source", now + 6 * 86400 * NS)
    root = tmp_path / "op"
    root.mkdir()
    publish(
        root / "config.json",
        dict(
            product="operator",
            mode="operator",
            provider="geckoterminal",
            profile="balanced",
            init_ns=now - NS,
            real_execution_enabled=False,
            paid_budget_usd=0,
            additional_requests=0,
            max_background_requests=6000,
            expires_ns=now + 6 * 86400 * NS,
            source_root=str(source),
            champion_model_path=str((source / "champion").resolve()),
            champion_model_sha="ab" * 32,
            max_training_runs=6,
            min_refit_interval_s=86400,
        ),
    )
    initialize(root)
    return root


def open_operator(tmp_path: Path, source_expires: int, operator_expires: int, **source_extra):
    source = seal_source(tmp_path / "source", source_expires, **source_extra)
    root = tmp_path / "op"
    root.mkdir()
    publish(
        root / "config.json",
        dict(
            product="operator",
            mode="operator",
            provider="geckoterminal",
            profile="balanced",
            init_ns=time.time_ns() - NS,
            real_execution_enabled=False,
            paid_budget_usd=0,
            additional_requests=0,
            max_background_requests=6000,
            expires_ns=operator_expires,
            source_root=str(source),
            champion_model_path=str((source / "champion").resolve()),
            champion_model_sha="ab" * 32,
            max_training_runs=6,
            min_refit_interval_s=86400,
        ),
    )
    initialize(root)
    return root, source


def seal_candidate(root: Path, name: str, finished_ns: int):
    art = root / "manual" / name
    model = art / "model"
    model.mkdir(parents=True)
    publish(
        model / "model-card.json",
        dict(
            learning=STUB,
            fits=0,
            selected="empirical",
            training_as_of_ns=finished_ns - NS,
            finished_ns=finished_ns,
            confirmatory_F01=False,
            production_trade_authorized=False,
            prospective_shadow_required=True,
        ),
    )
    publish(model / "synthetic.json", dict(learning=STUB, trained=False))
    seal(model)
    result = dict(
        status="completed",
        learning=STUB,
        trained=False,
        new_fits=0,
        promoted=False,
        dataset_id="snap",
        dataset_sha="ab" * 32,
        model_sha=sha(model / "manifest.json"),
        selected="empirical",
        finished_ns=finished_ns,
        validation={"empirical": {"pool_macro_pinball": 1.0}},
    )
    publish(art / "result.json", result)
    seal(art)
    return adapters.register(root, "candidates", art, result)


def connect_db(root: Path):
    return control._connect(root)


def job_row(root: Path, job_id: str) -> dict:
    db = connect_db(root)
    try:
        return dict(db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone())
    finally:
        db.close()


def publications(root: Path) -> list[dict]:
    db = connect_db(root)
    try:
        rows = db.execute(
            "SELECT job_id, kind, public_id, payload_json FROM publications"
        ).fetchall()
    finally:
        db.close()
    return [dict(row) for row in rows]


def enqueue(root: Path, request: str, action: str, params: dict | None = None) -> str:
    body = command(root, request, action, params or {})
    assert body["ok"] is True, body
    return body["job_id"]


def drive(root: Path, job_id: str) -> dict:
    deadline = time.time() + 90
    row = None
    while time.time() < deadline:
        tick(root)
        row = job_row(root, job_id)
        if row["status"] not in {"queued", "running"}:
            break
        time.sleep(0.05)
    log_path = artifact_dir(root, job_id) / "worker.log"
    log = log_path.read_text(errors="replace")[-2000:] if log_path.exists() else ""
    assert row is not None and row["status"] == "completed", (row, log)
    return row


def write_result(root: Path, job_id: str, payload: dict, *, marker: str | None) -> None:
    art = artifact_dir(root, job_id)
    art.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, sort_keys=True).encode()
    (art / "operator-result.json").write_bytes(raw)
    if marker is not None:
        (art / "operator-published.marker").write_text(marker)


def mark_running(root: Path, job_id: str) -> None:
    db = connect_db(root)
    try:
        db.execute(
            "UPDATE jobs SET status='running', pid=NULL, started_ns=? WHERE job_id=?",
            (time.time_ns(), job_id),
        )
    finally:
        db.close()


def test_worker_snapshot_publishes_an_exact_raw_audit(tmp_path):
    root = operator_root(tmp_path)
    job_id = enqueue(root, "req_snapshot", "snapshot")
    row = drive(root, job_id)
    art = artifact_dir(root, job_id) / "artifact"
    verify(art)
    report = read(art / "result.json")
    assert report["raw_audit"]["exact"] is True
    assert report["raw_audit"]["bars"] > 0
    again = audit(art, time.time_ns())
    assert again["exact"] is True
    assert again["fingerprint"] == report["raw_audit"]["fingerprint"]
    assert again["bars"] == report["raw_audit"]["bars"]
    assert "youngpool" in report["unseen_test_pools"]
    assert "oldpool" not in report["unseen_test_pools"]
    assert report["historical_test_independent"] is False
    assert report["new_fits"] == 0
    published = publications(root)
    assert len(published) == 1
    assert published[0]["kind"] == "dataset"
    assert published[0]["job_id"] == job_id
    assert published[0]["public_id"] == row["dataset_id"] == job_id
    payload = json.loads(published[0]["payload_json"])
    assert payload["snapshot_hash"] == report["dataset_sha"]
    marker = (artifact_dir(root, job_id) / "operator-published.marker").read_text().splitlines()
    raw = (artifact_dir(root, job_id) / "operator-result.json").read_bytes()
    assert marker[0] == "v1"
    assert marker[1] == job_id
    assert marker[2] == hashlib.sha256(raw).hexdigest()
    assert not (root / "process-logs").exists()


def test_marker_must_match_before_a_snapshot_is_published(tmp_path):
    now = time.time_ns()
    root, _source = open_operator(tmp_path, now + 6 * 86400 * NS, now + 6 * 86400 * NS)
    payload = {"dataset_id": "snap1", "snapshot_hash": "ab" * 32, "matured_samples": 4}
    raw = json.dumps(payload, sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    good = enqueue(root, "req_good", "snapshot")
    write_result(root, good, payload, marker=f"v1\n{good}\n{digest}\n")
    mark_running(root, good)
    tick(root)
    assert job_row(root, good)["status"] == "completed"
    assert job_row(root, good)["published"] == 1
    assert publications(root)[0]["public_id"] == "snap1"

    missing = enqueue(root, "req_missing", "snapshot")
    write_result(root, missing, payload, marker=None)
    mark_running(root, missing)
    tick(root)
    assert job_row(root, missing)["status"] == "failed"
    assert job_row(root, missing)["error_reason"] == "invalid_result"
    assert [row["job_id"] for row in publications(root)] == [good]

    bad = enqueue(root, "req_bad", "snapshot")
    write_result(root, bad, payload, marker=f"v1\n{bad}\n{'0' * 64}\n")
    mark_running(root, bad)
    tick(root)
    assert job_row(root, bad)["status"] == "failed"
    assert job_row(root, bad)["error_reason"] == "invalid_result"
    assert [row["job_id"] for row in publications(root)] == [good]


def test_recover_publishes_a_finished_marker_instead_of_dropping_it(tmp_path):
    now = time.time_ns()
    root, _source = open_operator(tmp_path, now + 6 * 86400 * NS, now + 6 * 86400 * NS)
    payload = {"dataset_id": "snap1", "snapshot_hash": "cd" * 32, "matured_samples": 4}
    raw = json.dumps(payload, sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()
    job_id = enqueue(root, "req_crash", "snapshot")
    write_result(root, job_id, payload, marker=f"v1\n{job_id}\n{digest}\n")
    mark_running(root, job_id)
    found = recover(root)
    assert found["recovered"] == [job_id]
    assert job_row(root, job_id)["status"] == "running"
    assert publications(root) == []
    assert not (artifact_dir(root, job_id) / "operator-stop-reason").exists()
    tick(root)
    assert job_row(root, job_id)["status"] == "completed"
    assert job_row(root, job_id)["published"] == 1
    assert publications(root)[0]["public_id"] == "snap1"

    bare = enqueue(root, "req_bare", "snapshot")
    write_result(root, bare, payload, marker=None)
    mark_running(root, bare)
    recover(root)
    assert job_row(root, bare)["status"] == "failed"
    assert job_row(root, bare)["error_reason"] == "invalid_result"
    assert [row["job_id"] for row in publications(root)] == [job_id]


def write_stub(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "LEARNING = 'explicit-stub-learning'",
                "def run(root, action, params, output):",
                "    if action != 'train':",
                "        from memecoin_operator.adapters import run as real_run",
                "        return real_run(root, action, params, output)",
                "    import time",
                "    from pathlib import Path",
                "    from memecoin_ml.data import sha",
                "    from memecoin_operator.adapters import registered, register",
                "    from memecoin_registry.contracts import seal",
                "    from memecoin_runner.ledger import publish",
                "    output = Path(output).resolve()",
                "    output.mkdir(parents=True, exist_ok=True)",
                "    artifact = output / 'artifact'",
                "    artifact.mkdir(exist_ok=False)",
                "    row, _dataset = registered(root, 'datasets', params['snapshot_id'])",
                "    model = artifact / 'model'",
                "    model.mkdir()",
                "    finished = time.time_ns() - 5 * 10**9",
                "    publish(model / 'model-card.json', dict(",
                "        learning=LEARNING, fits=0, selected='empirical',",
                "        training_as_of_ns=finished - 10**9, finished_ns=finished,",
                "        confirmatory_F01=False, production_trade_authorized=False,",
                "        prospective_shadow_required=True,",
                "    ))",
                "    publish(model / 'synthetic.json', dict(learning=LEARNING, trained=False))",
                "    seal(model)",
                "    result = dict(",
                "        status='completed', learning=LEARNING, trained=False, new_fits=0,",
                "        promoted=False, dataset_id=row['id'], dataset_sha=row['dataset_sha'],",
                "        snapshot_id=row['id'], model_sha=sha(model / 'manifest.json'),",
                "        selected='empirical', finished_ns=finished,",
                "        validation={'empirical': {'pool_macro_pinball': 1.0}},",
                "    )",
                "    publish(artifact / 'result.json', result)",
                "    seal(artifact)",
                "    entry = register(root, 'candidates', artifact, result)",
                "    return dict(candidate_id=entry['id'], **entry)",
                "def __getattr__(name):",
                "    from memecoin_operator import adapters as real",
                "    return getattr(real, name)",
                "",
            ]
        )
    )


def test_stub_candidate_freezes_into_a_future_exam(tmp_path, monkeypatch):
    root = operator_root(tmp_path)
    snap = enqueue(root, "req_snapshot", "snapshot")
    drive(root, snap)
    dataset_id = job_row(root, snap)["dataset_id"]
    stub = tmp_path / "explicit_stub_learning.py"
    write_stub(stub)
    monkeypatch.setenv("MEMECOIN_OPERATOR_ADAPTER", str(stub))
    trained = enqueue(root, "req_train", "train", {"snapshot_id": dataset_id})
    drive(root, trained)
    monkeypatch.delenv("MEMECOIN_OPERATOR_ADAPTER")
    result = read(artifact_dir(root, trained) / "operator-result.json")
    assert result["learning"] == STUB
    assert result["trained"] is False
    assert result["new_fits"] == 0
    card = read(artifact_dir(root, trained) / "artifact" / "model" / "model-card.json")
    assert card["learning"] == STUB
    assert card["confirmatory_F01"] is False
    paper = enqueue(root, "req_paper", "paper", {"candidate_id": result["candidate_id"]})
    drive(root, paper)
    exam = root / "paper" / paper
    config = read(exam / "config.json")
    assert config["training_max_runs"] == 0
    assert config["real_execution_enabled"] is False
    assert config["exam_start_ns"] == config["activation_ns"]
    assert config["expires_ns"] - config["activation_ns"] == 86400 * NS
    assert config["expires_ns"] - config["activation_ns"] >= 1800 * NS
    assert card["training_as_of_ns"] <= card["finished_ns"] < config["exam_start_ns"]
    frozen = config["frozen_exam_models"]
    model = artifact_dir(root, trained) / "artifact" / "model"
    assert frozen[0]["label"] == paper
    assert Path(frozen[0]["path"]).resolve() == model.resolve()
    assert frozen[0]["sha"] == result["model_sha"] == sha(model / "manifest.json")
    activation = read(exam / "activation.json")
    assert activation["no_backfill"] is True
    assert activation["promotion"] is False
    assert activation["mode"] == "frozen_future_exam"
    verify(Path(read(exam / "deployment.json")["bundle"]))
    verify(model)
    assert publications(root)[-1]["kind"] == "exam"
    assert not (exam / "STOP").exists()
    assert not (root / "process-logs").exists()
    assert job_row(root, paper)["published"] == 1


def test_exam_expiry_follows_the_tighter_bound_above_thirty_minutes(tmp_path):
    now = time.time_ns()
    horizon = now + 6 * 86400 * NS
    four_hours = now + 4 * 3600 * NS
    root, source = open_operator(tmp_path / "four", four_hours, horizon)
    row = seal_candidate(root, "ready", now - 60 * NS)
    opened = adapters.run(root, "paper", {"candidate_id": row["id"]}, root / "jobs" / "exam4h")
    assert opened["expires_ns"] == read(source / "config.json")["expires_ns"]
    span = opened["expires_ns"] - opened["activation_ns"]
    assert 1800 * NS <= span < 86400 * NS
    assert span <= 4 * 3600 * NS

    tight = now + 5000 * NS
    root_op, _source = open_operator(tmp_path / "operator", four_hours, tight)
    row_op = seal_candidate(root_op, "ready", now - 60 * NS)
    opened_op = adapters.run(
        root_op, "paper", {"candidate_id": row_op["id"]}, root_op / "jobs" / "exambound"
    )
    assert opened_op["expires_ns"] == tight
    assert opened_op["expires_ns"] - opened_op["activation_ns"] >= 1800 * NS

    root_wait, _source = open_operator(tmp_path / "wait", now + 1200 * NS, horizon)
    row_wait = seal_candidate(root_wait, "ready", now - 60 * NS)
    with pytest.raises(ValueError, match="operator_source_expiry_wait_successor"):
        adapters.run(
            root_wait, "paper", {"candidate_id": row_wait["id"]}, root_wait / "jobs" / "examwait"
        )
    assert not (root_wait / "paper" / "examwait" / "config.json").exists()
    assert list((root_wait / "exams").glob("*.json")) == []


def test_valid_source_schedules_the_paper_exam_without_a_process(tmp_path, monkeypatch):
    from memecoin_operator.background import paper_command

    now = time.time_ns()
    horizon = now + 6 * 86400 * NS
    root, source = open_operator(tmp_path, horizon, horizon)
    row = seal_candidate(root, "ready", now - 60 * NS)
    STARTS.clear()
    adapters.run(root, "paper", {"candidate_id": row["id"]}, root / "jobs" / "examday")
    exam = root / "paper" / "examday"
    view = adapters.inspect(root)
    assert view["source"]["succession_pending"] is False
    assert view["source"]["hot_pools"] == list("abcdef")
    assert read(source / "config.json")["http_timeout_s"] == 20
    STARTS.clear()
    maintained = adapters.maintain(root, {"mode": "paper", "phase": "paper"})
    assert maintained["status"] == "running"
    assert maintained["source_succession"]["phase"] == "borrowed"
    assert maintained["telemetry"]["recorded"] is True
    assert len(STARTS) == 1
    runtime = Path(read(exam / "deployment.json")["bundle"]) / "runtime"
    assert STARTS[0]["key"] == "exam-examday"
    assert STARTS[0]["seconds"] == 150
    assert STARTS[0]["command"] == paper_command(runtime, exam)
    assert not (root / "process-logs").exists()
    assert list((root / "background").glob("logs/*")) == []
    expiry = read(exam / "config.json")["expires_ns"]
    monkeypatch.setattr(adapters.time, "time_ns", lambda: expiry - 120 * NS)
    STARTS.clear()
    adapters.maintain(root, {"mode": "paper", "phase": "paper"})
    assert STARTS == []  # Graceful deadline drain, no late child and no fabricated STOP.
    assert not (exam / "STOP").exists()


def test_incomplete_source_config_fails_closed_before_a_start(tmp_path):
    now = time.time_ns()
    horizon = now + 6 * 86400 * NS
    cases = (
        (dict(history_floor_s=None), "successor_history_floor"),
        (dict(http_timeout_s=-1), "successor_http_timeout"),
        (dict(hot_pools=["only"]), "successor_cohort"),
    )
    for extra, code in cases:
        root, _source = open_operator(tmp_path / code, horizon, horizon, **extra)
        STARTS.clear()
        with pytest.raises(ValueError, match=code):
            adapters.maintain(root, {"mode": "paper", "phase": "paper"})
        assert STARTS == []


def test_ten_step_fits_seal_code_hashes_and_inference_identity(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    from memecoin_brain.model import identity
    from memecoin_operator.learning import RECIPE, prepare, train

    monkeypatch.setitem(RECIPE, "steps", 10)
    rows = []
    for pool, start in (("old", 0), ("young", 100)):
        for i in range(start, 300):
            close = 1.0 + (i % 17) * 0.002
            open_ = close * 0.998
            rows.append(
                dict(
                    pool=pool,
                    open_s=i * 300,
                    available_ns=(i + 1) * 300 * NS + 1,
                    ohlcv=[open_, close * 1.01, open_ * 0.99, close, 100.0],
                    capture_sha=f"synthetic-{pool}-{i}",
                )
            )
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    prepare(rows, dict(as_of_ns=300 * 300 * NS + 1, synthetic=True), snapshot)
    seal(snapshot)
    card = train(snapshot, tmp_path / "model", tmp_path / "progress.json")
    model = tmp_path / "model"
    verify(model)
    binding = read(model / "binding.json")
    assert binding["source_identity"] == identity()
    assert binding["operator_learning_sha"] == sha(Path(train.__code__.co_filename))
    src = Path(train.__code__.co_filename).resolve().parents[1]
    for name, digest in binding["training_code"].items():
        assert digest == sha(src / name)
    assert binding["trial_ids"] == ["linear-7", "linear-19", "gru-7", "gru-19"]
    assert card["confirmatory_F01"] is False
    assert card["production_trade_authorized"] is False
    assert card["prospective_shadow_required"] is True
    assert RECIPE["steps"] == 10
    weights = model / "linear-7" / "weights.pt"
    weights.write_bytes(weights.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="artifact_inventory_mismatch"):
        verify(model)
