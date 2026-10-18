"""Explicit fixtures test versions, as-of, transactional rebuild and corruption detection."""

import json

import pytest

from memecoin_continuous.store import audit, connect, ingest, view
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish


def capture(path, available, value=1, duplicate=False):
    path.mkdir()
    publish(path / "request.json", dict(started_ns=available - 2, lane="historical", url="fixture"))
    raw = [[0, value, value, value, value, 1]]
    if duplicate:
        raw = raw + raw
    (path / "response.bin").write_text(json.dumps(dict(data=dict(attributes=dict(ohlcv_list=raw)))))
    publish(path / "receipt.json", dict(received_ns=available - 1, available_ns=available))
    seal(path)
    return path


def test_first_observed_revisions_out_of_order_asof_and_rebuild(tmp_path):
    a = capture(tmp_path / "a", 400 * 10**9, 1)
    b = capture(tmp_path / "b", 500 * 10**9, 2)
    db = connect(tmp_path / "index.sqlite")
    ingest(db, b, "pool")
    ingest(db, a, "pool")
    ingest(db, b, "pool")
    db.close()
    assert view(tmp_path, 399 * 10**9)[0] == []
    assert view(tmp_path, 450 * 10**9)[0][0]["ohlcv"][3] == 1
    assert view(tmp_path, 600 * 10**9)[0][0]["ohlcv"][3] == 1
    assert audit(tmp_path, 600 * 10**9)["exact"]
    with connect(tmp_path / "index.sqlite") as db:
        assert db.execute("select count(*) from versions").fetchone()[0] == 2
        db.execute("update firsts set payload='{}'")
    with pytest.raises(ValueError, match="corruption"):
        view(tmp_path, 600 * 10**9)


def test_entire_bad_response_quarantine_and_manifest_pin(tmp_path):
    a = capture(tmp_path / "a", 400 * 10**9, duplicate=True)
    with connect(tmp_path / "index.sqlite") as db:
        assert ingest(db, a, "pool")["status"] == "duplicate_bar_in_response"
    assert not view(tmp_path, 500 * 10**9)[0]
    b = capture(tmp_path / "b", 500 * 10**9)
    with connect(tmp_path / "index.sqlite") as db:
        ingest(db, b, "pool")
    (b / "manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="capture_pin"):
        view(tmp_path, 600 * 10**9)
