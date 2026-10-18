import time

import pytest

from memecoin_brain import service
from memecoin_brain.source import initialize
from memecoin_ml.data import read, write


def setup(root, monkeypatch, *, prior=0):
    initialize(root)
    write(root / "activation.json", dict(prior_consecutive_acquisition_failures=prior))
    monkeypatch.setattr(service, "verify_runtime", lambda *a: None)
    monkeypatch.setattr(service, "command", lambda *a: [])
    model = root / "models/fixture"
    model.mkdir()
    write(model / "model-card.json", dict(finished_ns=time.time_ns()))
    monkeypatch.setattr(service, "models", lambda *_: [model])


def test_one_cycle_no_repeat_and_no_catchup(tmp_path, monkeypatch):
    root = tmp_path / "root"
    setup(root, monkeypatch)
    calls = []

    def child(command, log, seconds, guard):
        calls.append(seconds)
        guard()
        write(log.parent / "collection.json", dict(fixture=True))
        return dict(category="completed", exit_code=0)

    monkeypatch.setattr(service, "child", child)
    assert service.tick(root, "pin")["status"] == "completed"
    assert service.tick(root, "pin")["status"] == "already_reserved"
    assert calls == [240]


def test_failure_budget_includes_historical_acquisition(tmp_path, monkeypatch):
    root = tmp_path / "root"
    setup(root, monkeypatch, prior=1)
    monkeypatch.setattr(service, "child", lambda *a: dict(category="worker_failed", exit_code=1))
    assert service.tick(root, "pin")["status"] == "failed"
    assert service.tick(root, "pin")["status"] == "stopped"
    assert read(root / "STOP")["reason"] == "two_consecutive_acquisition_failures"


def test_crash_does_not_resume_request_or_train(tmp_path, monkeypatch):
    root = tmp_path / "root"
    setup(root, monkeypatch, prior=1)
    p = root / "cycles/000"
    p.mkdir()
    write(p / "reservation.json", dict(started_ns=0))
    monkeypatch.setattr(
        service, "child", lambda *a: pytest.fail("must not start after second failure")
    )
    assert service.tick(root, "pin")["status"] == "stopped"
    assert read(p / "result.json")["category"] == "interrupted_no_retry"


def test_cooldown_does_not_reserve_cycle(tmp_path, monkeypatch):
    root = tmp_path / "root"
    setup(root, monkeypatch)
    monkeypatch.setattr(service, "cooldown", lambda _: time.time_ns() + 10**12)
    monkeypatch.setattr(service, "child", lambda *a: pytest.fail("network"))
    assert service.tick(root, "pin")["status"] == "provider_cooldown"
    assert not list((root / "cycles").iterdir())


def test_concurrent_controller_rejected(tmp_path, monkeypatch):
    import fcntl

    root = tmp_path / "root"
    setup(root, monkeypatch)
    with (root / ".lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            service.tick(root, "pin")


def test_runtime_binding_rejects_config_tamper(tmp_path):
    from memecoin_brain.runtime import stage, verify_runtime

    root = tmp_path / "brain-b01/dev01"
    initialize(root)
    write(root / "universe.json", dict(pools=[]))
    write(
        root / "activation.json",
        dict(decision="approved", scope="brain_development_72h", paid_budget_usd=0),
    )
    write(root / "external-request-debit.json", dict(requests=0))
    stage(root, tmp_path / "bundle", tmp_path / "service.plist")
    verify_runtime(root)
    (root / "config.json").write_text("{}")
    with pytest.raises(ValueError, match="binding"):
        verify_runtime(root)
