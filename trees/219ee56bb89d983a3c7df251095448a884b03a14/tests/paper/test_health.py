from memecoin_paper.health import operational
from memecoin_runner.ledger import publish


def test_live_health_never_confuses_stopped_paper_with_healthy_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    publish(
        tmp_path / "config.json",
        dict(source=str(source), expires_ns=10**15, activation_ns=10, training_max_runs=4),
    )
    publish(source / "health.json", dict(at_ns=100, cooldown_ns=0, attempts=30))
    publish(tmp_path / "health.json", dict(at_ns=100, status="running"))
    publish(
        tmp_path / "paper-health.json",
        dict(status="failed", category="supervisor_guard", exit_code=-9),
    )
    publish(tmp_path / "STOP", dict(reason="failure"))
    h = operational(tmp_path, 200)
    assert h["source"]["healthy"]
    assert not h["paper"]["healthy"] and h["paper"]["stopped"]
    assert h["paper"]["failure_category"] == "supervisor_guard"
    assert h["training"]["state"] == "stopped"


def test_training_due_is_not_permission_to_ignore_maturity(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    publish(
        tmp_path / "config.json",
        dict(source=str(source), expires_ns=10**15, activation_ns=10, training_max_runs=4),
    )
    p = tmp_path / "training/000000"
    p.mkdir(parents=True)
    publish(p / "reservation.json", dict(started_ns=100))
    h = operational(tmp_path, 1000)
    assert h["training"]["not_before_ns"] == 86400 * 10**9 + 100
    assert h["training"]["state"] == "waiting_daily_gate"
    assert (
        operational(tmp_path, 86400 * 10**9 + 101)["training"]["state"]
        == "awaiting_scheduler_and_maturity"
    )
