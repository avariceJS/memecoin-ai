"""Recorded-style fixture: consuming the same capture cannot repeat a decision or fill."""

import json
from pathlib import Path

from memecoin_ml.data import sha
from memecoin_paper import service
from memecoin_paper.audit import replay, snapshot
from memecoin_registry.contracts import verify


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_consume_project_restart_and_sealed_reproduction(tmp_path, monkeypatch):
    root, source = tmp_path / "paper", tmp_path / "source"
    for p in (root / "training", source / "shadow/predictions"):
        p.mkdir(parents=True)
    model = tmp_path / "fixture-model"
    put(model / "manifest.json", dict(fixture=True))
    c = dict(
        source=str(source),
        activation_ns=310 * 10**9,
        expires_ns=9000 * 10**9,
        champion_model_sha=sha(model / "manifest.json"),
    )
    for name in ("config.json", "activation.json", "deployment.json"):
        put(root / name, c)
    put(source / "config.json", dict(expires_ns=c["expires_ns"]))
    rows = [
        dict(
            pool="fixture-pool",
            open_s=0,
            ohlcv=[1, 1, 1, 1, 10],
            available_ns=305 * 10**9,
            capture_sha="fixture-capture",
        )
    ]
    monkeypatch.setattr(service, "safe_corpus", lambda *args: (rows, {}))
    monkeypatch.setattr(
        service, "states", lambda _: ({"fixture-pool": {"status": "active"}}, "head")
    )
    monkeypatch.setattr(service, "verify", lambda _: None)  # model transport is a fixture here
    monkeypatch.setattr(service.time, "time_ns", lambda: 320 * 10**9)
    pred = dict(
        horizon=3,
        predicted_ns=315 * 10**9,
        input_available_ns=305 * 10**9,
        pool="fixture-pool",
        event_close_s=300,
        model_path=str(model),
        model_sha=sha(model / "manifest.json"),
        q_by_candidate={"gru": [-0.1, 0.03, 0.1]},
    )
    put(source / "shadow/predictions/p.json", pred)
    put(source / "shadow/predictions/old.json", dict(pred, predicted_ns=309 * 10**9))
    put(source / "shadow/predictions/future.json", dict(pred, predicted_ns=999 * 10**9))
    service.paper(root)
    service.paper(root)
    result = replay(root)
    assert result["counts"] == {"decision": 1} and result["pending"] == 1
    public = json.loads((root / "dashboard.json").read_text())
    assert str(tmp_path) not in json.dumps(public)
    assert public["real_execution_enabled"] is False
    rows[0] = dict(rows[0], open_s=300, available_ns=605 * 10**9)
    monkeypatch.setattr(service.time, "time_ns", lambda: 610 * 10**9)
    service.paper(root)
    service.paper(root)
    assert replay(root)["counts"] == {"decision": 1, "entry": 1}
    output = Path(tmp_path / "sealed")
    report = snapshot(root, output)
    verify(output)
    actual = replay(output, report["ledger_head"])
    assert actual == json.loads((output / "result.json").read_text())


def training_fixture(tmp_path, monkeypatch, new_matured, elapsed=86400):
    import sys
    from types import SimpleNamespace

    previous = 10**18
    now = previous + elapsed * 10**9
    root = tmp_path / "paper"
    put(root / "config.json", dict(source="fixture-source"))
    put(root / "training/000000/reservation.json", dict(started_ns=previous, matured=999999))
    samples = [
        dict(
            targets=[dict(log_return=0.01, available_ns=previous + (1 if i < new_matured else -1))]
        )
        for i in range(120)
    ]
    bundle = dict(samples=samples, fixture=True)
    monkeypatch.setattr(service.time, "time_ns", lambda: now)
    monkeypatch.setattr(service, "safe_corpus", lambda *args: ([dict(open_s=now // 10**9)], {}))
    monkeypatch.setattr(service, "build", lambda *args: bundle)
    calls = []

    def train(dataset, model):
        assert json.loads((dataset / "dataset.json").read_text()) == bundle
        calls.append(dataset)
        put(model / "model-card.json", dict(candidates=[], selected="empirical"))
        put(model / "manifest.json", dict(fixture=True))

    monkeypatch.setitem(sys.modules, "memecoin_brain.model", SimpleNamespace(train=train))
    return root, calls


def test_daily_training_counts_new_maturity_not_growing_rolling_window(tmp_path, monkeypatch):
    root, calls = training_fixture(tmp_path, monkeypatch, 100)
    service.training(root)
    assert len(calls) == 1
    reservation = json.loads((root / "training/000001/reservation.json").read_text())
    assert reservation["new_matured"] == 100 and reservation["matured"] == 120
    assert not reservation["paper_pnl_used_as_training_truth"]
    assert reservation["max_seconds"] == 600


def test_daily_training_waits_for_maturity_threshold(tmp_path, monkeypatch):
    root, calls = training_fixture(tmp_path, monkeypatch, 99)
    service.training(root)
    assert not calls and not (root / "training/000001").exists()


def test_daily_training_waits_full_day_despite_many_new_samples(tmp_path, monkeypatch):
    root, calls = training_fixture(tmp_path, monkeypatch, 120, elapsed=86399)
    service.training(root)
    assert not calls and not (root / "training/000001").exists()


def test_continuous_hot_only_predictions_and_dead_heartbeat(tmp_path, monkeypatch):
    from memecoin_continuous import store

    root, source = tmp_path / "paper", tmp_path / "source"
    (root / "training").mkdir(parents=True)
    put(
        root / "config.json",
        dict(
            source=str(source),
            source_backend="continuous",
            activation_ns=0,
            expires_ns=9000 * 10**9,
            champion_model_sha="fixture",
        ),
    )
    put(
        source / "config.json",
        dict(expires_ns=9000 * 10**9, hot_pools=["hot"], max_requests=10, hot_interval_s=120),
    )
    health = dict(
        at_ns=320 * 10**9,
        cooldown_ns=0,
        attempts=1,
        pools={p: dict(status="active") for p in ["hot", "cold"]},
    )
    put(source / "health.json", health)
    rows = [
        dict(
            pool=p,
            open_s=0,
            ohlcv=[1, 1, 1, 1, 10],
            available_ns=305 * 10**9,
            capture_sha="fixture",
        )
        for p in ["hot", "cold"]
    ]
    monkeypatch.setattr(service, "source_rows", lambda *args: (rows, {}))
    monkeypatch.setattr(store, "stats", lambda _: dict(bars=2, captures=1, observed_pools=2))
    calls = []
    monkeypatch.setattr(
        service, "candidate_tick", lambda r, rows, prov, active: calls.append(active)
    )
    monkeypatch.setattr(service.time, "time_ns", lambda: 320 * 10**9)
    service.paper(root)
    assert calls[-1] == {"hot"}
    public = json.loads((root / "dashboard.json").read_text())
    assert public["source_ok"] and len(public["market"]) == 2
    assert {o["role"] for o in public["market"]} == {"hot", "background"}
    monkeypatch.setattr(service.time, "time_ns", lambda: 501 * 10**9)
    service.paper(root)
    assert calls[-1] == set()
    assert json.loads((root / "dashboard.json").read_text())["source_ok"] is False
