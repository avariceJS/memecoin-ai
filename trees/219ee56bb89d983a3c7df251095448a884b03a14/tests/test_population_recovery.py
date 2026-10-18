import io
import sqlite3
import urllib.error

import pytest
from test_population_acquisition import plan

from memecoin_experience.storage import file_hash
from memecoin_population import recovery
from memecoin_population.acquisition import capture


def stopped(tmp_path, error=None):
    p = plan()
    p["created_ns"] = 10**12
    p["jobs"].append(dict(pool="synthetic", start_s=300, end_s=600))

    def failed(*args, **kwargs):
        raise error or TimeoutError()

    with pytest.raises(ValueError, match="acquisition_provider_stopped"):
        capture(tmp_path, p, guard=lambda: None, opener=failed, clock=lambda: 10**12)
    return p, file_hash(tmp_path / "STOP.json")


def test_reviewed_transport_recovery_skips_failure_and_keeps_stop_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery.time, "time_ns", lambda: 10**12)
    p, pin = stopped(tmp_path)
    stopped_bytes = (tmp_path / "STOP.json").read_bytes()
    report = recovery.reconcile_transport_stop(
        tmp_path,
        stop_pin=pin,
        reason="Reviewed sealed timeout; no retry; fresh owner guard required",
    )
    assert report["attempted"] == 1 and report["remaining"] == 1 and report["retried_requests"] == 0
    assert (tmp_path / "recoveries" / (pin + ".stop.json")).read_bytes() == stopped_bytes
    assert not (tmp_path / "STOP.json").exists()
    assert recovery.reconcile_transport_stop(tmp_path, stop_pin=pin, reason="repeat") == report
    calls = []

    class Response(io.BytesIO):
        status = 200

    def success(request, **kwargs):
        calls.append(request.full_url)
        return Response(b"{}")

    result = capture(
        tmp_path, p, guard=lambda: None, opener=success, clock=lambda: 10**12 + 4 * 10**9
    )
    assert len(calls) == 1 and "before_timestamp=600" in calls[0]
    assert result["statuses"] == {"received": 1, "transport_failure": 1}


def test_recovery_crash_after_receipt_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery.time, "time_ns", lambda: 10**12)
    _, pin = stopped(tmp_path)
    with monkeypatch.context() as patch:

        def crash(*args):
            raise RuntimeError("crash before archive rename")

        patch.setattr(recovery.os, "rename", crash)
        with pytest.raises(RuntimeError, match="crash"):
            recovery.reconcile_transport_stop(tmp_path, stop_pin=pin, reason="review")
    assert (tmp_path / "STOP.json").exists()
    result = recovery.reconcile_transport_stop(tmp_path, stop_pin=pin, reason="review again")
    assert result["reason"] == "review"


def test_unknown_or_provider_stop_cannot_use_transport_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery.time, "time_ns", lambda: 10**12)
    _, pin = stopped(tmp_path)
    with sqlite3.connect(tmp_path / "requests.sqlite") as db:
        db.execute("update requests set status='reserved'")
    with pytest.raises(ValueError, match="unresolved"):
        recovery.reconcile_transport_stop(tmp_path, stop_pin=pin, reason="review")
    assert (tmp_path / "STOP.json").exists()
    other = tmp_path / "provider"
    _, pin = stopped(
        other,
        urllib.error.HTTPError("https://example.invalid", 429, "limit", {}, io.BytesIO(b"{}")),
    )
    with pytest.raises(ValueError, match="requires_other_review"):
        recovery.reconcile_transport_stop(other, stop_pin=pin, reason="review")
    assert (other / "STOP.json").exists()


def test_terminal_commit_before_stop_crash_is_detected_on_restart(tmp_path):
    p, _ = stopped(tmp_path)
    # Simulate the state after terminal commit, before durable STOP publication.
    (tmp_path / "STOP.json").unlink()

    def forbidden(*args, **kwargs):
        raise AssertionError("unreviewed terminal failure must block new GETs")

    with pytest.raises(ValueError, match="unreviewed_terminal_failure"):
        capture(tmp_path, p, guard=lambda: None, opener=forbidden, clock=lambda: 10**12 + 4 * 10**9)
    assert (tmp_path / "STOP.json").exists()


def test_recovery_finishes_when_both_stop_entries_survive(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery.time, "time_ns", lambda: 10**12)
    _, pin = stopped(tmp_path)
    before = (tmp_path / "STOP.json").read_bytes()
    original = recovery.reconcile_transport_stop(tmp_path, stop_pin=pin, reason="review")
    (tmp_path / "STOP.json").write_bytes(before)
    assert recovery.reconcile_transport_stop(tmp_path, stop_pin=pin, reason="finish") == original
    assert not (tmp_path / "STOP.json").exists()
