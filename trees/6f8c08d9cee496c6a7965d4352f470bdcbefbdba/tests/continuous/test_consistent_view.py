"""A commit between index reads must not look like membership corruption."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from memecoin_continuous import store
from memecoin_continuous.store import audit, audit_batch, connect, ingest, stats, view
from memecoin_registry.contracts import seal
from memecoin_runner.ledger import publish


def capture(path, available, open_s=0, value=1):
    path.mkdir()
    publish(path / "request.json", dict(started_ns=available - 2, lane="historical", url="fixture"))
    raw = [[open_s, value, value, value, value, 1]]
    (path / "response.bin").write_text(json.dumps(dict(data=dict(attributes=dict(ohlcv_list=raw)))))
    publish(path / "receipt.json", dict(received_ns=available - 1, available_ns=available))
    seal(path)
    return path


def build(root):
    available = 1000 * 10**9
    capture(root / "a", available, open_s=0)
    db = connect(root / "index.sqlite")
    ingest(db, root / "a", "pool")
    db.execute("pragma wal_checkpoint(truncate)")
    db.close()
    return available


def insert_pending(path, cutoff):
    writer = sqlite3.connect(path)
    writer.isolation_level = None
    writer.execute("BEGIN")
    values = ("pool", 300, cutoff, "pin-pending", "{}", "abc")
    writer.execute("insert into versions values(?,?,?,?,?,?)", values)
    writer.execute("insert into firsts values(?,?,?,?,?,?)", values)
    return writer


def test_source_uses_deferred_snapshot_not_immediate_write_lock():
    text = Path(store.__file__).read_text()
    assert "BEGIN DEFERRED" in text
    assert "BEGIN IMMEDIATE" not in text


def test_commit_between_selects_is_false_corruption_without_snapshot_and_consistent_after(tmp_path):
    legacy = tmp_path / "legacy"
    fixed = tmp_path / "fixed"
    legacy.mkdir()
    fixed.mkdir()
    cutoff = 2000 * 10**9
    build(legacy)
    shutil.copy(legacy / "index.sqlite", fixed / "index.sqlite")
    shutil.copytree(legacy / "a", fixed / "a")

    writer = insert_pending(legacy / "index.sqlite", cutoff)
    db = sqlite3.connect(legacy / "index.sqlite")
    db.isolation_level = None
    firsts = db.execute(
        "select payload,checksum from firsts where available<=? and open_s>=?",
        (cutoff, 0),
    ).fetchall()
    writer.commit()
    expected = db.execute(
        "select count(*) from (select pool,open_s from versions "
        "where available<=? and open_s>=? group by pool,open_s)",
        (cutoff, 0),
    ).fetchone()[0]
    db.close()
    writer.close()
    # The inserted pair is consistent. Split autocommit reads are the false alarm.
    assert len(firsts) == 1 and expected == 2

    writer = insert_pending(fixed / "index.sqlite", cutoff)
    original = store.sqlite3.connect

    def wrapped(path, timeout=5.0, **kwargs):
        conn = original(path, timeout=timeout, **kwargs)
        if timeout == 20:

            def trace(sql):
                if "group by pool,open_s" in sql and writer.in_transaction:
                    writer.commit()

            conn.set_trace_callback(trace)
        return conn

    store.sqlite3.connect = wrapped
    try:
        rows, _ = view(fixed, cutoff)
        committed = not writer.in_transaction
    finally:
        store.sqlite3.connect = original
        writer.close()
    assert committed
    assert [row["open_s"] for row in rows] == [0]


def test_deferred_read_does_not_block_a_wal_writer(tmp_path, monkeypatch):
    build(tmp_path)
    seen = []
    original = store.sqlite3.connect

    def wrapped(path, timeout=5.0, **kwargs):
        conn = original(path, timeout=timeout, **kwargs)
        if timeout == 20:

            def trace(sql):
                if "group by pool,open_s" in sql and not seen:
                    seen.append(sql)
                    other = original(path, timeout=0.2)
                    other.isolation_level = None
                    other.execute("BEGIN IMMEDIATE")
                    other.execute("COMMIT")
                    other.close()

            conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(store.sqlite3, "connect", wrapped)
    rows, _ = view(tmp_path, 2000 * 10**9)
    assert len(rows) == 1 and seen


def test_stats_and_audit_keep_one_snapshot_when_a_writer_commits(tmp_path, monkeypatch):
    cutoff = 2000 * 10**9
    build(tmp_path)
    checked = audit_batch(tmp_path, 5)
    assert checked["verified"] == 1 and checked["first_membership"] is True
    writer = insert_pending(tmp_path / "index.sqlite", cutoff)
    original = store.sqlite3.connect

    def wrapped(path, timeout=5.0, **kwargs):
        conn = original(path, timeout=timeout, **kwargs)
        if timeout == 20:

            def trace(sql):
                if "count(distinct pool)" in sql and writer.in_transaction:
                    writer.commit()

            conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(store.sqlite3, "connect", wrapped)
    snapshot = stats(tmp_path)
    monkeypatch.undo()
    writer.close()
    assert snapshot["bars"] == snapshot["observed_pools"] == 1
    assert stats(tmp_path)["bars"] == 2

    # Rebuild a clean index for audit: the pending row above is now committed and invalid.
    clean = tmp_path / "clean"
    clean.mkdir()
    build(clean)
    writer = insert_pending(clean / "index.sqlite", cutoff)
    from memecoin_brain_scale import audit as audit_mod

    real_merge = audit_mod.merge

    def merging(caps, as_of):
        if writer.in_transaction:
            writer.commit()
        return real_merge(caps, as_of)

    monkeypatch.setattr(audit_mod, "merge", merging)
    result = audit(clean, cutoff)
    writer.close()
    assert result["exact"] is True and result["bars"] == 1


def test_true_corruption_is_still_rejected(tmp_path):
    build(tmp_path)
    cutoff = 2000 * 10**9
    db = sqlite3.connect(tmp_path / "index.sqlite")
    db.execute("delete from firsts")
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="index_membership_corruption"):
        view(tmp_path, cutoff)
    with pytest.raises(ValueError, match="index_first_membership_corruption"):
        audit_batch(tmp_path, 1)
    db = sqlite3.connect(tmp_path / "index.sqlite")
    db.execute("insert into firsts select * from versions")
    db.execute("update firsts set payload='{}'")
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="index_version_binding_corruption"):
        view(tmp_path, cutoff)


def test_fresh_index_uses_pin_lookup_for_bounded_raw_audit(tmp_path):
    build(tmp_path)
    with sqlite3.connect(tmp_path / "index.sqlite") as db:
        plan = db.execute(
            "explain query plan select payload,checksum from versions where pin=? order by open_s",
            ("fixture",),
        ).fetchall()
    assert any("versions_pin_open" in row[3] and "SEARCH" in row[3] for row in plan)
