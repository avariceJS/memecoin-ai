"""Read-only coverage cache: snapshot, cutoff, and synthetic scale. Not live latency."""

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from memecoin_operator import readmodel
from memecoin_operator.adapters import _source_coverage
from memecoin_operator.readmodel import _ROWS_SQL, NS, coverage

FULL = """
create table firsts(
  pool text, open_s integer, available integer,
  pin text, payload text, checksum text,
  primary key(pool, open_s)
)
"""
CORE = ("pool", "bars", "days", "first_s", "last_s")


@pytest.fixture(autouse=True)
def _reset_cache():
    readmodel._clear()
    yield
    readmodel._clear()


def _rows(values):
    return [(p, o, a, "pin", "{}", "c") for p, o, a in values]


def make_source(path: Path, rows, *, wal: bool = False, schema: str = FULL) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path / "index.sqlite")
    if wal:
        db.execute("PRAGMA journal_mode=WAL")
    db.executescript(schema)
    if rows:
        width = len(rows[0])
        if width == 3:
            db.executemany("INSERT INTO firsts VALUES (?,?,?)", rows)
        else:
            db.executemany("INSERT INTO firsts VALUES (?,?,?,?,?,?)", rows)
    db.commit()
    db.close()
    return path


def core(got: dict) -> dict:
    return dict(
        bars=got["bars"],
        observed_pools=got["observed_pools"],
        matured_samples=got["matured_samples"],
        event_days=got["event_days"],
        receipt_days=got["receipt_days"],
        per_pool=[{k: pool[k] for k in CORE} for pool in got["per_pool"]],
        first_s=got["first_s"],
        last_s=got["last_s"],
    )


def test_matches_legacy_coverage_and_mature_window(tmp_path):
    now = 90_000 * NS
    rows = _rows([("A", i * 300, 1_000 * NS) for i in range(11)])
    rows += _rows([("B", open_s, 2_000 * NS) for open_s in (0, 300, 900)])
    rows += _rows([("C", 86400, 86400 * NS), ("A", 3300, now + NS)])
    root = make_source(tmp_path / "src", rows)
    got = coverage(root, now, ttl_s=0)
    assert core(got) == core(_source_coverage(root, now))
    assert got["matured_samples"] == 1
    assert got["event_days"] == 2
    assert got["receipt_days"] == 2
    pool_b = next(pool for pool in got["per_pool"] if pool["pool"] == "B")
    assert pool_b["gaps"] == [dict(after_s=300, before_s=900, missing_bars=1)]
    assert pool_b["ranges"] == [
        dict(first_s=0, last_s=300, bars=2),
        dict(first_s=900, last_s=900, bars=1),
    ]
    blob = repr(got).lower()
    assert "dead" not in blob and "no_trading" not in blob and "inactive" not in blob
    assert "payload" not in _ROWS_SQL.lower()
    assert got["computed_ns"] == now
    assert got["cache_age_s"] == 0.0


def test_labels_are_not_read(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    db = sqlite3.connect(root / "index.sqlite")
    db.execute("CREATE TABLE firsts(pool TEXT, open_s INTEGER, available INTEGER)")
    db.execute("INSERT INTO firsts VALUES ('p', 0, 1)")
    db.commit()
    db.close()
    got = coverage(root, 10, ttl_s=0)
    assert got["bars"] == 1 and got["per_pool"][0]["ranges"][0]["bars"] == 1
    marked = make_source(
        tmp_path / "marked",
        [("p", 0, 1, "pin", "LABEL-SECRET-DO-NOT-LEAK", "c")],
    )
    assert "LABEL-SECRET-DO-NOT-LEAK" not in repr(coverage(marked, 10, ttl_s=0))


def test_duplicate_open_matches_legacy_row_count(tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    db = sqlite3.connect(root / "index.sqlite")
    db.execute("CREATE TABLE firsts(pool TEXT, open_s INTEGER, available INTEGER)")
    db.executemany("INSERT INTO firsts VALUES (?,?,?)", [("p", 0, 5), ("p", 0, 5)])
    db.commit()
    db.close()
    got = coverage(root, 10, ttl_s=0)
    assert core(got) == core(_source_coverage(root, 10))
    assert got["bars"] == 2
    assert got["per_pool"][0]["bars"] == 1
    assert sum(item["bars"] for item in got["per_pool"][0]["ranges"]) == 1


def test_old_cutoff_never_reuses_a_newer_snapshot(tmp_path):
    root = make_source(tmp_path / "src", _rows([("p", 0, 1 * NS), ("p", 300, 30 * NS)]))
    early = coverage(root, 2 * NS, ttl_s=15)
    late = coverage(root, 40 * NS, ttl_s=15)
    assert early["bars"] == 1 and early["computed_ns"] == 2 * NS
    assert late["bars"] == 2 and late["computed_ns"] == 40 * NS
    again_early = coverage(root, 2 * NS, ttl_s=15)
    again_late = coverage(root, 40 * NS, ttl_s=15)
    assert again_early["bars"] == 1 and again_early["computed_ns"] == 2 * NS
    assert again_late["bars"] == 2 and again_late["computed_ns"] == 40 * NS
    assert again_late["cache_age_s"] >= 0


def test_ttl_delays_growth_until_expiry_and_exact_reads_stay_fresh(tmp_path, monkeypatch):
    clock = {"wall": 10**18, "mono": 10**15}
    monkeypatch.setattr(readmodel.time, "time_ns", lambda: clock["wall"])
    monkeypatch.setattr(readmodel.time, "monotonic_ns", lambda: clock["mono"])
    root = make_source(tmp_path / "src", _rows([("p", 0, 1)]))
    now = 100
    first = coverage(root, now, ttl_s=15)
    assert first["bars"] == 1 and first["computed_ns"] == now
    db = sqlite3.connect(root / "index.sqlite")
    db.execute("INSERT INTO firsts VALUES ('p', 300, 50, 'pin', '{}', 'c')")
    db.commit()
    db.close()
    assert coverage(root, now + 5 * NS, ttl_s=0)["bars"] == 2
    held = coverage(root, now + 5 * NS, ttl_s=15)
    assert held["bars"] == 1 and held["computed_ns"] == now
    clock["mono"] += 15 * NS
    boundary = coverage(root, now, ttl_s=15)
    assert boundary["bars"] == 1 and boundary["cache_age_s"] == 15.0
    clock["mono"] += 1
    expired = coverage(root, now, ttl_s=15)
    assert expired["bars"] == 2 and expired["cache_age_s"] == 0.0 and expired["computed_ns"] == now


def test_clock_regression_drops_cached_growth(tmp_path, monkeypatch):
    clock = {"wall": 10**18, "mono": 10**15}
    monkeypatch.setattr(readmodel.time, "time_ns", lambda: clock["wall"])
    monkeypatch.setattr(readmodel.time, "monotonic_ns", lambda: clock["mono"])
    root = make_source(tmp_path / "src", _rows([("p", 0, 1)]))

    def grow():
        db = sqlite3.connect(root / "index.sqlite")
        db.execute("INSERT INTO firsts VALUES ('p', 300, 1, 'pin', '{}', 'c')")
        db.commit()
        db.close()

    assert coverage(root, 10, ttl_s=15)["bars"] == 1
    grow()
    clock["wall"] -= 1
    assert coverage(root, 10, ttl_s=15)["bars"] == 2

    readmodel._clear()
    root2 = make_source(tmp_path / "src2", _rows([("p", 0, 1)]))
    clock["wall"] += 5
    clock["mono"] += 5
    assert coverage(root2, 10, ttl_s=15)["bars"] == 1
    db = sqlite3.connect(root2 / "index.sqlite")
    db.execute("INSERT INTO firsts VALUES ('p', 300, 1, 'pin', '{}', 'c')")
    db.commit()
    db.close()
    clock["mono"] -= 1
    assert coverage(root2, 10, ttl_s=15)["bars"] == 2


def test_replacement_and_resolved_identity(tmp_path, monkeypatch):
    root = make_source(tmp_path / "src", _rows([("p", 0, 1)]))
    now = 10**18
    reads = {"n": 0}
    real = readmodel._read

    def wrapped(index, now_ns):
        reads["n"] += 1
        return real(index, now_ns)

    monkeypatch.setattr(readmodel, "_read", wrapped)
    assert coverage(root, now, ttl_s=15)["bars"] == 1
    assert coverage((root / ".").resolve(), now, ttl_s=15)["bars"] == 1
    assert reads["n"] == 1
    other = make_source(tmp_path / "other", _rows([("p", i * 300, 1) for i in range(3)]))
    index = root / "index.sqlite"
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(index) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    index.unlink()
    other.joinpath("index.sqlite").replace(index)
    replaced = coverage(root, now, ttl_s=15)
    assert replaced["bars"] == 3 and replaced["computed_ns"] == now
    assert reads["n"] == 2


def test_return_values_are_independent_copies(tmp_path):
    root = make_source(tmp_path / "src", _rows([("p", 0, 1), ("p", 600, 1)]))
    got = coverage(root, 10, ttl_s=15)
    got["bars"] = -1
    got["per_pool"][0]["pool"] = "mutated"
    got["per_pool"][0]["gaps"].append(dict(after_s=1, before_s=2, missing_bars=0))
    got["per_pool"][0]["ranges"][0]["bars"] = -5
    fresh = coverage(root, 10, ttl_s=15)
    assert fresh["bars"] == 2
    assert fresh["per_pool"][0]["pool"] == "p"
    assert fresh["per_pool"][0]["gaps"] == [dict(after_s=0, before_s=600, missing_bars=1)]
    assert fresh["per_pool"][0]["ranges"][0]["bars"] == 1
    assert "healthy" not in fresh and "stopped" not in fresh and "freshness_s" not in fresh
    uneven = make_source(tmp_path / "uneven", _rows([("p", 0, 1), ("p", 100, 1)]))
    gap = coverage(uneven, 10, ttl_s=0)["per_pool"][0]
    assert gap["gaps"] == [dict(after_s=0, before_s=100, missing_bars=0)]
    assert [item["bars"] for item in gap["ranges"]] == [1, 1]


def test_missing_database_creates_nothing(tmp_path):
    ghost = tmp_path / "missing"
    with pytest.raises(ValueError, match="readmodel_missing_database"):
        coverage(ghost, 0)
    assert not ghost.exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="readmodel_missing_database"):
        coverage(empty, 0)
    assert list(empty.iterdir()) == []
    root = make_source(tmp_path / "src", _rows([("p", 0, 1)]))
    coverage(root, 10, ttl_s=15)
    (root / "index.sqlite").unlink()
    with pytest.raises(ValueError, match="readmodel_missing_database"):
        coverage(root, 10, ttl_s=15)


def test_malformed_schema_is_not_rewritten(tmp_path):
    root = tmp_path / "bad"
    root.mkdir()
    index = root / "index.sqlite"
    db = sqlite3.connect(index)
    db.execute("CREATE TABLE firsts(a INTEGER)")
    db.commit()
    db.close()
    digest = index.read_bytes()
    names = sorted(p.name for p in root.iterdir())
    with pytest.raises(ValueError, match="readmodel_malformed_schema"):
        coverage(root, 0)
    assert index.read_bytes() == digest
    assert sorted(p.name for p in root.iterdir()) == names

    typed = tmp_path / "typed"
    typed.mkdir()
    db = sqlite3.connect(typed / "index.sqlite")
    db.execute("CREATE TABLE firsts(pool TEXT, open_s TEXT, available INTEGER)")
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="readmodel_malformed_schema"):
        coverage(typed, 0)

    junk = tmp_path / "junk"
    junk.mkdir()
    target = junk / "index.sqlite"
    target.write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError):
        coverage(junk, 0)
    assert target.read_bytes() == b"not a database"
    assert list(junk.glob("index.sqlite-*")) == []


def test_schema_change_same_inode_invalidates(tmp_path, monkeypatch):
    root = make_source(tmp_path / "src", _rows([("p", 0, 1)]))
    reads = {"n": 0}
    real = readmodel._read

    def wrapped(index, now_ns):
        reads["n"] += 1
        return real(index, now_ns)

    monkeypatch.setattr(readmodel, "_read", wrapped)
    assert coverage(root, 10, ttl_s=15)["bars"] == 1
    db = sqlite3.connect(root / "index.sqlite")
    db.execute("ALTER TABLE firsts ADD COLUMN extra INTEGER")
    db.commit()
    db.close()
    assert coverage(root, 10, ttl_s=15)["bars"] == 1
    assert reads["n"] == 2


def _logical_rows(root: Path) -> list:
    db = sqlite3.connect(f"file:{root / 'index.sqlite'}?mode=ro", uri=True, timeout=5)
    try:
        db.execute("PRAGMA query_only=ON")
        return db.execute(
            "SELECT pool, open_s, available FROM firsts ORDER BY pool, open_s"
        ).fetchall()
    finally:
        db.close()


def _marker_bytes(root: Path) -> dict:
    return {
        "config": (root / "config.json").read_bytes(),
        "stop": (root / "STOP").read_bytes(),
        "raw": (root / "raw" / "capture.bin").read_bytes(),
    }


def _plant_markers(root: Path) -> dict:
    (root / "config.json").write_text('{"pin":"same"}', encoding="utf-8")
    (root / "STOP").write_text('{"reason":"audit_worker"}', encoding="utf-8")
    raw = root / "raw"
    raw.mkdir(exist_ok=True)
    (raw / "capture.bin").write_bytes(b"raw-bytes")
    return _marker_bytes(root)


def test_read_only_transaction_leaves_logical_source_unchanged(tmp_path, monkeypatch):
    root = make_source(tmp_path / "src", _rows([("p", 0, 1)]), wal=True)
    markers = _plant_markers(root)
    logical = _logical_rows(root)
    sqls: list[str] = []
    uris: list[str] = []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        uris.append(str(args[0] if args else kwargs.get("database")))
        db = real_connect(*args, **kwargs)
        db.set_trace_callback(sqls.append)
        return db

    monkeypatch.setattr(readmodel.sqlite3, "connect", connect)
    got = coverage(root, 10, ttl_s=15)
    assert got["bars"] == 1
    text = "\n".join(sqls).lower()
    assert "pragma query_only" in text
    assert "begin" in text and "rollback" in text
    assert "create" not in text and "journal_mode" not in text
    assert "immutable" not in text
    assert "from firsts" in text and "payload" not in text
    assert uris and all("mode=ro" in uri and "immutable" not in uri for uri in uris)
    assert _logical_rows(root) == logical
    assert _marker_bytes(root) == markers
    assert "stopped" not in got and "healthy" not in got and "freshness_s" not in got
    sqls.clear()
    uris.clear()
    assert coverage(root, 10, ttl_s=15)["bars"] == 1
    cached = "\n".join(sqls).lower()
    assert "from firsts" not in cached
    assert "begin" in cached and "rollback" in cached
    assert all("immutable" not in uri for uri in uris)
    assert _logical_rows(root) == logical
    assert _marker_bytes(root) == markers
    db = sqlite3.connect(root / "index.sqlite", timeout=1)
    db.execute("BEGIN IMMEDIATE")
    db.execute("COMMIT")
    db.close()


def test_consistent_snapshot_hides_commit_inside_the_read(tmp_path, monkeypatch):
    root = make_source(tmp_path / "src", _rows([("p", 0, 1)]), wal=True)
    holder = sqlite3.connect(root / "index.sqlite")
    holder.execute("SELECT count(*) FROM firsts").fetchone()
    assert (root / "index.sqlite-wal").exists()
    real = readmodel._schema_token
    state = {"done": False}

    def token(db):
        value = real(db)
        if not state["done"]:
            state["done"] = True
            writer = sqlite3.connect(root / "index.sqlite", timeout=2)
            writer.execute("INSERT INTO firsts VALUES ('z', 300, 1, 'pin', '{}', 'c')")
            writer.commit()
            writer.close()
        return value

    monkeypatch.setattr(readmodel, "_schema_token", token)
    try:
        assert coverage(root, 10, ttl_s=0)["bars"] == 1
        assert coverage(root, 10, ttl_s=0)["bars"] == 2
    finally:
        holder.close()


def test_writer_starts_without_wal_and_checkpoints_during_view(tmp_path, monkeypatch):
    """No sidecars at open. Writer then commits and checkpoints while BEGIN is open."""
    root = make_source(tmp_path / "src", _rows([("p", 0, 1)]), wal=True)
    markers = _plant_markers(root)
    db = sqlite3.connect(root / "index.sqlite")
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    for suffix in ("-wal", "-shm"):
        sidecar = root / f"index.sqlite{suffix}"
        if sidecar.exists():
            sidecar.unlink()
    assert not (root / "index.sqlite-wal").exists()
    real = readmodel._schema_token
    started = threading.Event()
    finished = threading.Event()
    state = {"checkpoint": None, "error": None, "armed": True}

    def checkpoint():
        writer = sqlite3.connect(root / "index.sqlite", timeout=5)
        started.set()
        try:
            state["checkpoint"] = writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except BaseException as exc:  # noqa: BLE001 - surfaced after the read snapshot closes
            state["error"] = exc
        finally:
            writer.close()
            finished.set()

    def token(connection):
        value = real(connection)
        if state["armed"]:
            state["armed"] = False
            writer = sqlite3.connect(root / "index.sqlite", timeout=5)
            writer.execute("INSERT INTO firsts VALUES ('z', 300, 1, 'pin', '{}', 'c')")
            writer.commit()
            writer.close()
            threading.Thread(target=checkpoint, daemon=True).start()
            assert started.wait(2)
            time.sleep(0.05)
        return value

    monkeypatch.setattr(readmodel, "_schema_token", token)
    seen = coverage(root, 10, ttl_s=0)
    assert finished.wait(5)
    assert state["error"] is None
    assert state["checkpoint"] is not None
    assert seen["bars"] == 1
    assert seen["observed_pools"] == 1
    assert seen["per_pool"][0]["pool"] == "p"
    assert seen["bars"] == sum(pool["bars"] for pool in seen["per_pool"])
    ranged = sum(item["bars"] for item in seen["per_pool"][0]["ranges"])
    assert ranged == seen["per_pool"][0]["bars"]
    assert "stopped" not in seen
    assert _marker_bytes(root) == markers
    assert coverage(root, 10, ttl_s=0)["bars"] == 2
    assert _logical_rows(root) == [("p", 0, 1), ("z", 300, 1)]
    assert _marker_bytes(root) == markers


def test_wal_uncommitted_rows_stay_invisible(tmp_path):
    root = make_source(tmp_path / "src", _rows([("p", 0, 1)]), wal=True)
    writer = sqlite3.connect(root / "index.sqlite", timeout=2)
    writer.execute("BEGIN")
    writer.execute("INSERT INTO firsts VALUES ('p', 300, 1, 'pin', '{}', 'c')")
    seen = coverage(root, 10, ttl_s=0)
    assert seen["bars"] == 1
    writer.commit()
    writer.close()
    assert coverage(root, 10, ttl_s=0)["bars"] == 2


def test_wal_concurrent_ingest_stays_consistent(tmp_path):
    root = make_source(tmp_path / "src", [], wal=True)
    setup = sqlite3.connect(root / "index.sqlite", timeout=5)
    for i in range(10):
        setup.execute(
            "INSERT INTO firsts VALUES (?,?,?,?,?,?)",
            (f"p{i % 4}", i * 300, 10**18, "pin", "{}", "c"),
        )
    setup.commit()
    setup.close()
    index = root / "index.sqlite"
    inode = index.stat().st_ino
    barrier = threading.Barrier(5)
    errors = []
    sequences = []

    def check(got):
        assert got["computed_ns"] == 10**18
        assert got["bars"] == sum(pool["bars"] for pool in got["per_pool"])
        assert got["observed_pools"] == len(got["per_pool"])
        if got["per_pool"]:
            assert got["first_s"] == min(pool["first_s"] for pool in got["per_pool"])
            assert got["last_s"] == max(pool["last_s"] for pool in got["per_pool"])
        for pool in got["per_pool"]:
            assert sum(item["bars"] for item in pool["ranges"]) == pool["bars"]

    def reader():
        local = []
        try:
            barrier.wait()
            for _ in range(25):
                got = coverage(root, 10**18, ttl_s=0)
                check(got)
                local.append(got["bars"])
        except Exception as exc:  # noqa: BLE001 - fail the test from the thread result
            errors.append(exc)
        sequences.append(local)

    def ingest():
        db = sqlite3.connect(root / "index.sqlite", timeout=5)
        try:
            barrier.wait()
            for i in range(10, 110):
                db.execute(
                    "INSERT INTO firsts VALUES (?,?,?,?,?,?)",
                    (f"p{i % 4}", i * 300, 10**18, "pin", "{}", "c"),
                )
                db.commit()
        finally:
            db.close()

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads.append(threading.Thread(target=ingest))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
        assert not thread.is_alive()
    assert errors == []
    for local in sequences:
        assert local == sorted(local)
        assert local[-1] >= 10
    final = coverage(root, 10**18, ttl_s=0)
    check(final)
    assert final["bars"] == 110
    assert index.stat().st_ino == inode
    assert not Path(str(index) + "-journal").exists()


def test_request_coalescing_returns_private_dicts(tmp_path, monkeypatch):
    root = make_source(tmp_path / "src", _rows([("p", i * 300, 1) for i in range(20)]))
    real = readmodel._read
    calls = []

    def wrapped(index, now_ns):
        calls.append(threading.get_ident())
        time.sleep(0.3)
        return real(index, now_ns)

    monkeypatch.setattr(readmodel, "_read", wrapped)
    barrier = threading.Barrier(8)
    found = []
    errors = []

    def worker():
        try:
            barrier.wait()
            found.append(coverage(root, 10, ttl_s=15))
        except Exception as exc:  # noqa: BLE001 - collected across threads
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()
    assert errors == []
    assert len(calls) == 1
    assert len({id(item) for item in found}) == 8
    found[0]["bars"] = -1
    assert all(item["bars"] == 20 for item in found[1:])
    assert readmodel._FLIGHTS == {}


def test_cache_is_bounded(tmp_path, monkeypatch):
    reads = {"n": 0}
    real = readmodel._read

    def wrapped(index, now_ns):
        reads["n"] += 1
        return real(index, now_ns)

    monkeypatch.setattr(readmodel, "_read", wrapped)
    roots = [
        make_source(tmp_path / str(i), _rows([(f"p{i}", 0, 1)]))
        for i in range(readmodel._MAX_CACHE + 4)
    ]
    now = 10
    for root in roots:
        assert coverage(root, now, ttl_s=15)["bars"] == 1
    assert reads["n"] == len(roots)
    assert len(readmodel._CACHE) == readmodel._MAX_CACHE
    coverage(roots[-1], now, ttl_s=15)
    assert reads["n"] == len(roots)
    coverage(roots[0], now, ttl_s=15)
    assert reads["n"] == len(roots) + 1


def test_health_markers_do_not_change_coverage(tmp_path):
    root = make_source(tmp_path / "src", _rows([("p", 0, 1)]))
    (root / "STOP").write_text("stop", encoding="utf-8")
    (root / "health.json").write_text('{"at_ns": 1}', encoding="utf-8")
    first = coverage(root, 10, ttl_s=15)
    (root / "STOP").unlink()
    second = coverage(root, 10, ttl_s=15)
    assert first["bars"] == second["bars"] == 1
    assert set(first) == {
        "bars",
        "observed_pools",
        "matured_samples",
        "event_days",
        "receipt_days",
        "per_pool",
        "first_s",
        "last_s",
        "computed_ns",
        "cache_age_s",
    }


def test_arguments_are_rejected(tmp_path):
    root = make_source(tmp_path / "src", [])
    with pytest.raises(TypeError, match="readmodel_now_ns"):
        coverage(root, True)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="readmodel_ttl_s"):
        coverage(root, 0, ttl_s=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="readmodel_ttl_s"):
        coverage(root, 0, ttl_s=-1)
    empty = coverage(root, 0, ttl_s=0)
    assert empty["bars"] == 0 and empty["per_pool"] == []
    assert empty["first_s"] is None and empty["last_s"] is None
    assert empty["matured_samples"] == 0
    assert core(empty) == core(_source_coverage(root, 0))


def _scale(path: Path, n: int, pools: int = 24) -> int:
    path.mkdir()
    db = sqlite3.connect(path / "index.sqlite")
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute(
        """CREATE TABLE firsts(
            pool TEXT, open_s INTEGER, available INTEGER,
            pin TEXT, payload TEXT, checksum TEXT,
            PRIMARY KEY(pool, open_s)
        )"""
    )

    def rows():
        for i in range(n):
            open_s = (i // pools) * 300
            yield (f"p{i % pools:02d}", open_s, (open_s + 300) * NS, "pin", "{}", "c")

    db.executemany("INSERT INTO firsts VALUES (?,?,?,?,?,?)", rows())
    db.commit()
    db.close()
    last_open = ((n - 1) // pools) * 300
    return (last_open + 300) * NS


def test_synthetic_100k_and_300k_cached_skips_scan(tmp_path):
    for n in (100_000, 300_000):
        root = tmp_path / str(n)
        now = _scale(root, n)
        started = time.perf_counter()
        cold = coverage(root, now, ttl_s=15)
        cold_s = time.perf_counter() - started
        started = time.perf_counter()
        warm = coverage(root, now, ttl_s=15)
        warm_s = time.perf_counter() - started
        assert cold["bars"] == n and warm["bars"] == n
        assert cold["observed_pools"] == 24
        assert warm["per_pool"] == cold["per_pool"]
        assert warm["matured_samples"] == cold["matured_samples"]
        assert cold["cache_age_s"] == 0.0
        assert warm["computed_ns"] == cold["computed_ns"] == now
        assert warm_s < cold_s, f"rows={n} cold_s={cold_s:.6f} warm_s={warm_s:.6f}"
        print(
            f"SYNTHETIC rows={n} cold_s={cold_s:.6f} warm_s={warm_s:.6f} "
            f"pools={cold['observed_pools']} matured={cold['matured_samples']}"
        )
