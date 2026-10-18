"""IO boundary fixtures: no provider calls; reservations and cooldown are durable."""

import io
import urllib.error
from types import SimpleNamespace

import pytest

from memecoin_continuous import source
from memecoin_ml.data import read, sha
from memecoin_runner.ledger import Ledger, publish


def root_fixture(root, monkeypatch):
    (root / "requests").mkdir()
    publish(
        root / "config.json",
        dict(expires_ns=10000 * 10**9, max_requests=3, history_floor_s=0, request_spacing_s=12),
    )
    publish(root / "universe.json", dict(pools=[dict(pool="p", pool_created_ns=0)]))
    monkeypatch.setattr(source.time, "time_ns", lambda: 400 * 10**9)
    monkeypatch.setattr(source.time, "time", lambda: 400)
    monkeypatch.setattr(source.shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * 1024**3))
    return dict(config_sha=sha(root / "config.json"), scope="B08_source")


def test_429_receipt_body_and_no_requests_during_cooldown(tmp_path, monkeypatch):
    binding = root_fixture(tmp_path, monkeypatch)
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        raise urllib.error.HTTPError(
            "fixture", 429, "fixture", {"Retry-After": "1200"}, io.BytesIO(b"fixture-body")
        )

    monkeypatch.setattr(source.urllib.request, "urlopen", request)
    with Ledger(tmp_path / "journal", binding) as ledger:
        s, cap = source.fetch(
            tmp_path,
            dict(pool="p", lane="prospective", before=None, limit=100),
            source.seed_state(tmp_path),
            ledger,
        )
        assert s["attempts"] == 1 and s["cooldown_ns"] == 1600 * 10**9
        assert (cap / "error-response.bin").read_bytes() == b"fixture-body"
    source.tick(tmp_path)
    assert len(calls) == 1 and read(tmp_path / "health.json")["attempts"] == 1


def test_crash_consumes_history_reservation_without_repeat(tmp_path, monkeypatch):
    binding = root_fixture(tmp_path, monkeypatch)

    def crash(*args, **kwargs):
        raise RuntimeError("fixture_crash")

    monkeypatch.setattr(source.urllib.request, "urlopen", crash)
    s = source.seed_state(tmp_path)
    s["pools"]["p"]["next_ns"] = 900 * 10**9
    with Ledger(tmp_path / "journal", binding) as ledger:
        with pytest.raises(RuntimeError, match="fixture_crash"):
            source.fetch(
                tmp_path, dict(pool="p", lane="historical", before=0, limit=1000), s, ledger
            )
        last = ledger.events[-1]["payload"]["state"]
        assert last["attempts"] == 1 and last["pools"]["p"]["history_done"]
        assert source.choose(last, [dict(pool="p")], 400 * 10**9) is None
