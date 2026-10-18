"""A background collector waits for live acquisition and preserves failed read attempts."""

import importlib.util
import json
import time
from pathlib import Path

import pytest

from memecoin_brain.source import SourceFailure
from memecoin_ml.data import read, sha
from memecoin_runner.ledger import publish


@pytest.fixture
def campaign():
    path = Path(__file__).resolve().parents[2] / "tools/brain_birth_admission.py"
    spec = importlib.util.spec_from_file_location("birth_campaign", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_cooldown_waits_but_stop_and_expiry_remain_fatal(tmp_path, campaign):
    source = tmp_path / "source"
    (source / "journal").mkdir(parents=True)
    now = time.time_ns()
    publish(source / "config.json", dict(expires_ns=now + 10**12))
    binding = dict(source=str(source), source_config_sha=sha(source / "config.json"))
    state = dict(cooldown_ns=now + 10**11, last_ns=now, failures=1)
    p = source / "journal/event-000000.json"
    publish(p, dict(kind="outcome", payload=dict(status="valid", state=state)))
    assert not campaign.control(binding, allow_wait=True)["ready"]
    with pytest.raises(ValueError, match="not_ready"):
        campaign.control(binding)
    state.update(cooldown_ns=0, failures=0)
    p.write_text(json.dumps(dict(kind="reserved", payload=dict(state=state))))
    assert not campaign.control(binding, allow_wait=True)["ready"]
    p.write_text(json.dumps(dict(kind="outcome", payload=dict(status="valid", state=state))))
    assert campaign.control(binding)["ready"]
    publish(source / "STOP", dict(reason="synthetic"))
    with pytest.raises(ValueError, match="stopped"):
        campaign.control(binding, allow_wait=True)


@pytest.mark.parametrize(
    "code,error,status",
    [
        (None, "URLError", "transport_gap"),
        (404, "HTTPError", "not_found"),
        (429, "HTTPError", None),
        (401, "HTTPError", None),
        (403, "HTTPError", None),
    ],
)
def test_worker_records_transport_debt_but_does_not_retry_or_ignore_auth_rate(
    tmp_path, campaign, monkeypatch, code, error, status
):
    monkeypatch.setenv("MEMECOIN_LEASE_FD", "0")
    monkeypatch.setattr("memecoin_operational.worker.lease_watchdog", lambda fd: None)
    monkeypatch.setattr(campaign, "control", lambda *a, **k: dict(ready=True))
    (tmp_path / "requests/000000").mkdir(parents=True)
    (tmp_path / "results").mkdir()
    publish(tmp_path / "binding.json", dict(cases=[dict(pool="synthetic")]))
    publish(tmp_path / "requests/000000/failure.json", dict(http_status=code, error_type=error))
    calls = []

    def fail(*a, **k):
        calls.append(k)
        raise SourceFailure("public_http_failure")

    monkeypatch.setattr(campaign, "ohlcv", fail)
    # Pool metadata is supplied before the network function constructs the call.
    (tmp_path / "binding.json").write_text(
        json.dumps(dict(cases=[dict(pool="synthetic", event_s=0)]))
    )
    if status is None:
        with pytest.raises(SourceFailure):
            campaign.worker(tmp_path, 0)
        assert not list((tmp_path / "results").iterdir())
    else:
        campaign.worker(tmp_path, 0)
        assert read(tmp_path / "results/000000.json")["status"] == status
    assert len(calls) == 1
