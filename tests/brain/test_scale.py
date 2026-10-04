import time

import pytest

from memecoin_brain.source import BAR, NS, SourceFailure
from memecoin_brain_scale.history import budget_bound, idle_slot
from memecoin_ml.data import write


def test_shared_lifetime_reserve_is_conservative(tmp_path):
    (tmp_path / "requests").mkdir()
    write(tmp_path / "external-request-debit.json", dict(requests=1))
    now = 123 * NS
    write(tmp_path / "config.json", dict(expires_ns=now + BAR * NS, max_requests=49))
    assert budget_bound(tmp_path, now)["future_max"] == 24
    (tmp_path / "requests/000000").mkdir()
    with pytest.raises(SourceFailure, match="shared_lifetime_budget"):
        budget_bound(tmp_path, now)


def test_history_requires_completed_current_cycle_and_boundary_headroom(tmp_path):
    due = 3000
    path = tmp_path / "cycles" / str(due)
    path.mkdir(parents=True)
    assert not idle_slot(tmp_path, (due + 100) * NS)
    write(path / "result.json", dict(status="completed"))
    assert idle_slot(tmp_path, (due + 100) * NS)
    assert not idle_slot(tmp_path, (due + 275) * NS)
    assert not idle_slot(tmp_path, (due + 400) * NS)


def test_history_failed_page_is_not_retried(tmp_path, monkeypatch):
    from memecoin_brain_scale import history

    primary = tmp_path / "primary"
    primary.mkdir()
    (primary / "requests").mkdir()
    output = tmp_path / "new"

    def prepare(*args):
        output.mkdir()
        (output / "requests").mkdir()
        return dict(tasks=[dict(pool="fixture", cursor=1000, floor=0, needed=True)])

    attempts = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise SourceFailure("fixture_HTTP429")

    monkeypatch.setattr(history, "prepare", prepare)
    monkeypatch.setattr(history, "host_guard", lambda p: None)
    monkeypatch.setattr(history, "cooldown", lambda p: 0)
    monkeypatch.setattr(history, "idle_slot", lambda *a: True)
    monkeypatch.setattr(history, "budget_bound", lambda *a: {})
    monkeypatch.setattr(history, "ohlcv", fail)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    result = history.acquire(primary, output)
    assert attempts == [1]
    assert result["status"] == "failed_no_retry"
    assert result["pending"][0]["pool"] == "fixture"
    assert (output / "failure.json").exists()
