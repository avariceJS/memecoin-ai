"""Suspend interruptions are retryable; all other source failures stay fail-closed."""

from memecoin_continuous import runtime
from memecoin_ml.data import read
from memecoin_runner.ledger import publish


def test_suspend_failure_records_incident_without_stopping_source(tmp_path):
    result = dict(category="clock_jump_or_suspend", elapsed_seconds=5.9, exit_code=-9)

    incident = runtime._record_worker_failure(tmp_path, "source", result)

    assert incident.is_file()
    assert read(incident) == result
    assert not (tmp_path / "STOP").exists()
    pauses = list(tmp_path.glob("sleep-interruption-*.json"))
    assert len(pauses) == 1
    assert read(pauses[0]) == dict(incident=incident.name, phase="source", **result)
    assert read(tmp_path / "failure-health.json")["category"] == "clock_jump_or_suspend"


def test_unknown_source_failure_still_writes_stop(tmp_path):
    result = dict(category="worker_failed", elapsed_seconds=5.9, exit_code=1)

    incident = runtime._record_worker_failure(tmp_path, "source", result)

    stop = read(tmp_path / "STOP")
    assert stop == dict(reason="source_worker_failure", incident=incident.name)
    assert not list(tmp_path.glob("sleep-interruption-*.json"))


def test_repaired_source_can_stage_distinct_launchagents(tmp_path):
    root = tmp_path / "source"
    bundle = tmp_path / "runtime"
    launch = tmp_path / "launchagents"
    root.mkdir()
    (bundle / "runtime").mkdir(parents=True)
    publish(root / "config.json", {"service_prefix": "ai.memecoin.test.repaired."})

    labels = runtime.write_launchagents(root, bundle, launch)

    assert labels == ["ai.memecoin.test.repaired.source", "ai.memecoin.test.repaired.audit"]
    for label, interval, phase in ((labels[0], 5, "source"), (labels[1], 3600, "audit")):
        import plistlib

        value = plistlib.loads((launch / f"{label}.plist").read_bytes())
        assert value["Label"] == label
        assert value["StartInterval"] == interval
        assert str(bundle / "runtime") in value["ProgramArguments"]
        assert str(root.resolve()) in value["ProgramArguments"]
        assert phase in value["ProgramArguments"]
