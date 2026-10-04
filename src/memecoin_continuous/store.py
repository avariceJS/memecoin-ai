"""SQLite is a derived index. Immutable captures remain the source of truth."""

import json
import sqlite3
from pathlib import Path

from memecoin_brain.source import bars
from memecoin_ml.data import read, sha
from memecoin_registry.contracts import digest, verify

SCHEMA_ERRORS = {"duplicate_bar_in_response", "invalid_OHLCV", "bar_schema_or_alignment"}


def connect(path):
    db = sqlite3.connect(path, timeout=20)
    db.execute("pragma journal_mode=WAL")
    db.executescript("""
    create table if not exists captures(pin text primary key, path text, available integer,
      status text, n integer, record text, checksum text);
    create table if not exists versions(pool text, open_s integer, available integer,
      pin text, payload text, checksum text, primary key(pool,open_s,pin));
    create index if not exists version_time on versions(available);
    create index if not exists version_open on versions(open_s,available);
    create table if not exists firsts(pool text, open_s integer, available integer,
      pin text, payload text, checksum text, primary key(pool,open_s));
    create index if not exists first_time on firsts(open_s,available);
    """)
    return db


def ingest(db, capture, pool):
    verify(capture)
    pin = sha(capture / "manifest.json")
    found = db.execute("select record,checksum from captures where pin=?", (pin,)).fetchone()
    if found:
        r = json.loads(found[0])
        if digest(r) != found[1]:
            raise ValueError("index_capture_corruption")
        return r
    request, receipt = read(capture / "request.json"), read(capture / "receipt.json")
    if not request["started_ns"] <= receipt["received_ns"] <= receipt["available_ns"]:
        raise ValueError("capture_clock_order")
    try:
        rows = bars(capture, pool)
        status = "valid"
    except ValueError as exc:
        if str(exc) not in SCHEMA_ERRORS:
            raise
        rows = []
        status = str(exc)
    record = dict(
        pin=pin,
        path=str(capture.resolve()),
        pool=pool,
        available_ns=receipt["available_ns"],
        status=status,
        n=len(rows),
    )
    with db:
        for row in rows:
            payload = json.dumps(row, sort_keys=True, allow_nan=False)
            values = (pool, row["open_s"], row["available_ns"], pin, payload, digest(row))
            db.execute("insert into versions values(?,?,?,?,?,?)", values)
            db.execute(
                """insert into firsts values(?,?,?,?,?,?) on conflict(pool,open_s)
              do update set available=excluded.available,pin=excluded.pin,
              payload=excluded.payload,checksum=excluded.checksum
              where excluded.available<firsts.available""",
                values,
            )
        db.execute(
            "insert into captures values(?,?,?,?,?,?,?)",
            (
                pin,
                str(capture.resolve()),
                receipt["available_ns"],
                status,
                len(rows),
                json.dumps(record, sort_keys=True),
                digest(record),
            ),
        )
    return record


def view(root, cutoff, *, since=0):
    db = connect(root / "index.sqlite")
    try:
        values = db.execute(
            "select payload,checksum from firsts where available<=? and open_s>=? "
            "order by pool,open_s",
            (cutoff, since),
        ).fetchall()
        expected = db.execute(
            "select count(*) from (select pool,open_s from versions "
            "where available<=? and open_s>=? group by pool,open_s)",
            (cutoff, since),
        ).fetchone()[0]
        if expected != len(values):
            raise ValueError("index_membership_corruption")
        mismatch = db.execute(
            "select count(*) from firsts f left join versions v on f.pool=v.pool "
            "and f.open_s=v.open_s and f.pin=v.pin where f.available<=? and f.open_s>=? "
            "and (v.pin is null or f.payload!=v.payload)",
            (cutoff, since),
        ).fetchone()[0]
        if mismatch:
            raise ValueError("index_version_binding_corruption")
        rows = []
        for payload, checksum in values:
            row = json.loads(payload)
            if digest(row) != checksum:
                raise ValueError("index_row_corruption")
            rows.append(row)
        pins = sorted({r["capture_sha"] for r in rows})
        captures = []
        for pin in pins:
            found = db.execute(
                "select record,checksum from captures where pin=?", (pin,)
            ).fetchone()
            if not found:
                raise ValueError("index_missing_capture")
            r = json.loads(found[0])
            if digest(r) != found[1] or sha(Path(r["path"]) / "manifest.json") != pin:
                raise ValueError("index_capture_pin")
            captures.append(dict(path=r["path"], pool=r["pool"], manifest_sha=pin))
        return rows, dict(
            as_of_ns=cutoff,
            captures=captures,
            revisions=[],
            first_observed_versions=True,
            historical_online_availability_known=False,
            index_selection_sha=digest(rows),
            revisions_in_separate_index=True,
        )
    finally:
        db.close()


def stats(root):
    with connect(root / "index.sqlite") as db:
        return dict(
            bars=db.execute("select count(*) from firsts").fetchone()[0],
            captures=db.execute("select count(*) from captures").fetchone()[0],
            versions=db.execute("select count(*) from versions").fetchone()[0],
            observed_pools=db.execute("select count(distinct pool) from firsts").fetchone()[0],
        )


def audit(root, cutoff):
    from memecoin_brain_scale.audit import merge

    with connect(root / "index.sqlite") as db:
        caps = [
            (Path(p), json.loads(r)["pool"])
            for p, r in db.execute(
                "select path,record from captures where status='valid' and available<=? "
                "order by available,path",
                (cutoff,),
            )
        ]
    rows, _ = merge(caps, cutoff)
    cached, _ = view(root, cutoff)
    if rows != cached:
        raise ValueError("index_full_raw_mismatch")
    return dict(exact=True, bars=len(rows), fingerprint=digest(rows), captures=len(caps))


def audit_batch(root, now, limit=512):
    """Bounded rotating raw verification; no network or first-observed mutation."""
    db = connect(root / "index.sqlite")
    try:
        db.execute("create table if not exists audited(pin text primary key, at_ns integer)")
        todo = db.execute(
            """select c.pin,c.path,c.record from captures c left join audited a
                           on c.pin=a.pin order by coalesce(a.at_ns,0),c.available limit ?""",
            (limit,),
        ).fetchall()
        for pin, path, raw in todo:
            cap = Path(path)
            verify(cap)
            record = json.loads(raw)
            expected = bars(cap, record["pool"]) if record["status"] == "valid" else []
            got = []
            for payload, checksum in db.execute(
                "select payload,checksum from versions where pin=? order by open_s", (pin,)
            ):
                row = json.loads(payload)
                if digest(row) != checksum:
                    raise ValueError("index_version_corruption")
                got.append(row)
            if expected != got:
                raise ValueError("index_raw_versions_mismatch")
            with db:
                db.execute("insert or replace into audited values(?,?)", (pin, now))
        missing = db.execute("""select count(*) from (select pool,open_s,min(available) av
                             from versions group by pool,open_s) v left join firsts f
                             on v.pool=f.pool and v.open_s=f.open_s
                             where f.pool is null or f.available!=v.av""").fetchone()[0]
        if missing:
            raise ValueError("index_first_membership_corruption")
        return dict(verified=len(todo), at_ns=now, first_membership=True)
    finally:
        db.close()
