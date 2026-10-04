"""Regression for the supervisor contract and repeated launchd invocations."""

import json
import sys
import time

from memecoin_paper import runtime


def setup(tmp_path, monkeypatch, phase):
    monkeypatch.setattr(sys, "argv", ["worker", str(tmp_path), phase])
    monkeypatch.setattr(runtime, "guard", lambda _: dict(expires_ns=time.time_ns() + 10**12))
    (tmp_path / "deployment.json").write_text(json.dumps(dict(bundle=str(tmp_path))))


def test_repeated_workers_use_exclusive_unique_logs(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, "paper")
    logs = []

    def child(cmd, log, seconds, guard):
        assert 0 < seconds <= 600
        guard()
        with log.open("x") as f:
            f.write("fixture")
        logs.append(log)
        return dict(exit_code=0, category="completed")

    monkeypatch.setattr(runtime, "child", child)
    runtime.main()
    runtime.main()
    assert len(set(logs)) == 2 and not (tmp_path / "STOP").exists()


def test_training_failure_stops_only_training_and_never_retries(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, "train")
    calls = []

    def child(cmd, log, seconds, guard):
        assert seconds == 600
        calls.append(cmd)
        return dict(exit_code=1, category="fixture_failure")

    monkeypatch.setattr(runtime, "child", child)
    runtime.main()
    runtime.main()
    assert len(calls) == 1 and (tmp_path / "TRAIN_STOP").exists()
    assert not (tmp_path / "STOP").exists()
    assert len(list(tmp_path.glob("incident-*.json"))) == 1


def test_parent_exception_produces_durable_incident_and_stops_paper(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, "paper")

    def child(*args):
        raise OSError("fixture")

    monkeypatch.setattr(runtime, "child", child)
    runtime.main()
    assert (tmp_path / "STOP").exists()
    assert (
        json.loads(next(tmp_path.glob("incident-*.json")).read_text())["category"]
        == "supervisor_OSError"
    )


def test_startup_guard_failure_is_durable_and_safe(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, "paper")

    def broken(_):
        raise ValueError("private-endpoint-token")

    monkeypatch.setattr(runtime, "guard", broken)
    runtime.main()
    runtime.main()
    assert (tmp_path / "STOP").exists()
    incidents = list(tmp_path.glob("incident-*.json"))
    assert len(incidents) == 1
    text = incidents[0].read_text()
    assert "private-endpoint-token" not in text
    assert json.loads(text)["detail"]["stage"] == "startup"
