"""Offline launcher fault checks; mocked reviews are never provider/billing evidence."""

import importlib.util
import json
from pathlib import Path

import pytest

from memecoin_ml.data import read
from memecoin_operational.contract import GateError

spec = importlib.util.spec_from_file_location(
    "live_launcher", Path(__file__).parents[2] / "tools/f01_live_qualification.py"
)
assert spec and spec.loader
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    output = tmp_path / "attempts/live-01"
    output.parent.mkdir()
    c = dict(
        settings=str(tmp_path / "settings"),
        source_root=str(tmp_path / "f01-source"),
        free_floor=0,
        window_bytes=1024**2,
        proposal_sha="fixture-proposal",
    )
    r = dict(
        attempt_root=str(output),
        budget_review=dict(
            valid_until_ns=10**30,
            mapping_evidence_path=str(tmp_path / "mapping"),
            billing_evidence_path=str(tmp_path / "billing"),
        ),
    )
    monkeypatch.setattr(launcher, "check_bundle", lambda *_: c)
    monkeypatch.setattr(launcher, "authorize", lambda *a, **kw: r)
    monkeypatch.setattr(launcher, "endpoint", lambda _: "fixture-secret-never-network")
    monkeypatch.setattr(launcher, "window", lambda *a, **kw: pytest.fail("unexpected worker"))
    return (bundle, "fixture-pin", tmp_path / "receipt", "fixture-receipt-pin", output), c, r


def test_denied_before_attempt_or_worker(setup, monkeypatch):
    args, _, _ = setup

    def deny(*a, **kw):
        raise GateError("separate_decision_required")

    monkeypatch.setattr(launcher, "authorize", deny)
    with pytest.raises(GateError, match="decision_required"):
        launcher.run(*args)
    assert not args[-1].exists()


def test_preflight_never_reserves(setup):
    args, c, _ = setup
    assert launcher.preflight(*args) == c
    assert not args[-1].exists()


def test_single_attempt_and_durable_reservation_before_supervisor(setup, monkeypatch):
    args, _, _ = setup
    seen = []

    def worker(bundle, pin, path, reservation, **kwargs):
        assert read(path / "reservation.json") == reservation
        assert reservation["reserved_rpc"] == 180
        assert kwargs["scope"] == "bounded_qualification"
        kwargs["live_guard"]()
        seen.append(path)
        return dict(status="completed")

    monkeypatch.setattr(launcher, "window", worker)
    assert launcher.run(*args)["status"] == "completed"
    with pytest.raises(GateError, match="exists_no_retry"):
        launcher.run(*args)
    assert seen == [args[-1]]


def test_crash_retains_full_reservation_no_retry_or_secret(setup, monkeypatch):
    args, _, _ = setup

    def crash(*a, **kw):
        raise RuntimeError("fixture-secret-never-log")

    monkeypatch.setattr(launcher, "window", crash)
    with pytest.raises(RuntimeError):
        launcher.run(*args)
    failure = args[-1] / "launcher-failure.json"
    assert read(failure)["reserved_rpc"] == 180
    assert "fixture-secret" not in failure.read_text()
    with pytest.raises(GateError, match="exists_no_retry"):
        launcher.run(*args)


def test_claim_before_reservation_crash_is_not_retried(setup):
    args, _, _ = setup
    args[-1].mkdir()
    with pytest.raises(GateError, match="exists_no_retry"):
        launcher.run(*args)


def test_scope_path_and_protected_destination_rejected_before_read(setup):
    args, c, r = setup
    other = args[-1].with_name("other")
    with pytest.raises(GateError, match="reuse"):
        launcher.preflight(*args[:-1], other)
    protected = Path(c["source_root"]).parent / "calendar-execution-01"
    r["attempt_root"] = str(protected)
    with pytest.raises(ValueError, match="disjoint"):
        launcher.preflight(*args[:-1], protected)
    assert not protected.exists()


def test_short_validity_stop_and_disk_fail_before_reservation(setup, monkeypatch):
    import shutil
    from types import SimpleNamespace

    args, _, r = setup
    r["budget_review"]["valid_until_ns"] = 1
    with pytest.raises(GateError, match="cover_window"):
        launcher.run(*args)
    r["budget_review"]["valid_until_ns"] = 10**30
    stop = args[-1].parent.parent / "STOP"
    stop.touch()
    with pytest.raises(GateError, match="stop_requested"):
        launcher.run(*args)
    stop.unlink()
    monkeypatch.setattr(shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(GateError, match="disk_headroom"):
        launcher.run(*args)
    assert not args[-1].exists()


def test_cli_suppresses_exception_payload(setup, monkeypatch, capsys):
    args, _, _ = setup

    def bad(*a, **kw):
        raise ValueError("https://provider/fixture-secret")

    monkeypatch.setattr(launcher, "preflight", bad)
    assert (
        launcher.main([str(args[0]), "--bundle-sha", "pin", "--output", str(args[-1]), "--check"])
        == 1
    )
    text = capsys.readouterr().out
    assert "fixture-secret" not in text
    assert json.loads(text)["status"] == "closed"


def test_simultaneous_launch_claims_only_once(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    args, _, _ = setup
    barrier = Barrier(2)
    original = launcher.preflight
    calls = []

    def checked(*a):
        c = original(*a)
        barrier.wait(timeout=5)
        return c

    def worker(*a, **kw):
        kw["live_guard"]()
        calls.append(1)
        return dict(status="completed")

    monkeypatch.setattr(launcher, "preflight", checked)
    monkeypatch.setattr(launcher, "window", worker)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(launcher.run, *args) for _ in range(2)]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except FileExistsError:
                results.append("claim_rejected")
    assert calls == [1]
    assert results.count("claim_rejected") == 1


def test_symlink_parent_does_not_write_through(setup):
    args, _, r = setup
    alias = args[-1].parent.with_name("alias")
    alias.symlink_to(args[-1].parent, target_is_directory=True)
    output = alias / "live-01"
    r["attempt_root"] = str(output)
    with pytest.raises(GateError, match="symlink"):
        launcher.run(*args[:-1], output)
    assert not args[-1].exists()
