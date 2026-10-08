"""Append-only, paged fact snapshots; SQLite views are disposable derived indexes."""

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from memecoin_brain.source import EXCLUDED
from memecoin_experience.storage import (
    PIN,
    atomic_json,
    canonical,
    digest,
    lock,
    object_path,
    safe_path,
    seal_files,
    sync_dir,
    verify_files,
)

SHARD_ROWS = 2048
PAGE_SHARDS = 256
MAX_TIME = 2**63 - 1
ROLES = {"train", "research_evaluation"}


def entity(value):
    if (
        not isinstance(value, str)
        or len(value) > 256
        or len(value.split("/")) != 3
        or not all(value.split("/"))
    ):
        raise ValueError("population_entity_namespace")
    if value.split("/")[-1] in EXCLUDED:
        raise ValueError("population_protected_entity")
    return value


def checked(row, kind, cutoff):
    common = {"entity", "start_ns", "end_ns", "known_ns", "evidence"}
    extra = (
        {"attributes"}
        if kind == "population"
        else {"stream", "contract", "stage", "status", "unit", "expected", "observed"}
    )
    if set(row) != common | extra:
        raise ValueError("population_record_schema")
    entity(row["entity"])
    if any(type(row[k]) is not int for k in ("start_ns", "end_ns", "known_ns")) or not (
        0 <= row["start_ns"] < row["end_ns"] <= MAX_TIME and 0 <= row["known_ns"] <= cutoff
    ):
        raise ValueError("population_record_time")
    if (
        not isinstance(row["evidence"], list)
        or not row["evidence"]
        or any(not isinstance(p, str) or not PIN.fullmatch(p) for p in row["evidence"])
    ):
        raise ValueError("population_record_evidence")
    if kind == "population":
        attrs = row["attributes"]
        if (
            not isinstance(attrs, dict)
            or len(attrs) > 32
            or any(
                not isinstance(k, str)
                or not k
                or len(k) > 64
                or (
                    v is not None
                    and (not isinstance(v, str) or not v or len(v) > 256 or v == "__unknown__")
                )
                for k, v in attrs.items()
            )
        ):
            raise ValueError("population_attributes")
    else:
        if not isinstance(row["contract"], str) or not PIN.fullmatch(row["contract"]):
            raise ValueError("population_stream_identity")
        if row["stage"] not in {"capture", "qualified"} or row["status"] not in {
            "complete",
            "partial",
            "empty",
            "unsupported",
            "failed",
            "unknown",
        }:
            raise ValueError("population_coverage_status")
        if any(
            not isinstance(row[k], str) or not row[k] or len(row[k]) > 256
            for k in ("stream", "unit")
        ):
            raise ValueError("population_coverage_unit")
        for k in ("expected", "observed"):
            if row[k] is not None and (type(row[k]) is not int or not 0 <= row[k] <= MAX_TIME):
                raise ValueError("population_coverage_count")
        if row["status"] == "empty" and row["observed"] != 0:
            raise ValueError("population_coverage_empty")
        if row["status"] in {"failed", "unsupported", "unknown"} and row["observed"] is not None:
            raise ValueError("population_coverage_unknown_is_not_zero")
        if row["status"] in {"complete", "partial"} and row["observed"] is None:
            raise ValueError("population_coverage_missing_count")
    if len(canonical(row)) > 32768:
        raise ValueError("population_record_too_large")
    return row


def put(store, name, payload):
    stage = Path(tempfile.mkdtemp(prefix="facts-", dir=store / "staging"))
    try:
        with (stage / name).open("wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        pin = seal_files(stage)
        dest = object_path(store, pin)
        dest.parent.mkdir(parents=True, exist_ok=True)
        sync_dir(store / "objects")
        if dest.exists():
            verify_files(dest, pin)
        else:
            os.rename(stage, dest)
            sync_dir(dest.parent)
        return pin
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def manifest(store, pin):
    if not isinstance(pin, str) or not PIN.fullmatch(pin):
        raise ValueError("population_pin")
    value = json.loads(safe_path(Path(store) / "snapshots" / (pin + ".json")).read_text())
    if (
        digest(value) != pin
        or value.get("version") != "market-facts-v1"
        or value.get("kind") not in {"population", "coverage"}
        or value["definition"].get("role") not in ROLES
    ):
        raise ValueError("population_manifest")
    return value


def publish(store, kind, rows, *, cutoff_ns, definition, parents=()):
    if (
        kind not in {"population", "coverage"}
        or definition.get("role") not in ROLES
        or type(cutoff_ns) is not int
        or not 0 <= cutoff_ns <= MAX_TIME
    ):
        raise ValueError("population_contract")
    store = safe_path(store)
    store.mkdir(parents=True, exist_ok=True)
    sync_dir(store.parent)
    with lock(store / ".publication.lock"):
        for part in ("objects", "staging", "snapshots"):
            (store / part).mkdir(exist_ok=True)
        sync_dir(store)
        for pin in parents:
            parent = manifest(store, pin)
            if (
                parent["kind"] != kind
                or parent["definition"] != definition
                or parent["cutoff_ns"] > cutoff_ns
            ):
                raise ValueError("population_parent_contract")
        pages, shards, buffer = [], [], []
        count = 0

        def flush_page():
            if shards:
                pages.append(
                    dict(
                        pin=put(store, "page.json", canonical(shards)),
                        shards=len(shards),
                        records=sum(s["records"] for s in shards),
                    )
                )
                shards.clear()

        def flush():
            if buffer:
                shards.append(
                    dict(
                        pin=put(
                            store, "records.jsonl", b"".join(canonical(r) + b"\n" for r in buffer)
                        ),
                        records=len(buffer),
                    )
                )
                buffer.clear()
                if len(shards) == PAGE_SHARDS:
                    flush_page()

        for row in rows:
            buffer.append(json.loads(canonical(checked(row, kind, cutoff_ns))))
            count += 1
            if len(buffer) == SHARD_ROWS:
                flush()
        flush()
        flush_page()
        value = dict(
            version="market-facts-v1",
            kind=kind,
            cutoff_ns=cutoff_ns,
            definition=definition,
            parents=sorted(set(parents)),
            pages=pages,
            records=count,
        )
        pin = digest(value)
        atomic_json(store / "snapshots" / (pin + ".json"), value)
        return pin


def records(store, pin):
    """Deduplicates shared ancestry; exact fact deduplication belongs to the disk view."""
    root = manifest(store, pin)
    pending = [pin]
    visited = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        value = manifest(store, current)
        if (
            value["kind"] != root["kind"]
            or value["definition"] != root["definition"]
            or value["cutoff_ns"] > root["cutoff_ns"]
        ):
            raise ValueError("population_parent_contract")
        pending.extend(reversed(value["parents"]))
        n = 0
        for page in value["pages"]:
            path = object_path(store, page["pin"])
            verify_files(path, page["pin"])
            shards = json.loads((path / "page.json").read_text())
            if (
                len(shards) != page["shards"]
                or sum(s["records"] for s in shards) != page["records"]
            ):
                raise ValueError("population_page_count")
            for shard in shards:
                path = object_path(store, shard["pin"])
                verify_files(path, shard["pin"])
                got = 0
                with (path / "records.jsonl").open() as f:
                    for line in f:
                        got += 1
                        yield checked(json.loads(line), value["kind"], value["cutoff_ns"])
                if got != shard["records"]:
                    raise ValueError("population_shard_count")
                n += got
        if n != value["records"]:
            raise ValueError("population_snapshot_count")


def cell(row):
    return digest(
        {k: row[k] for k in ("entity", "stream", "contract", "start_ns", "end_ns", "unit")}
    )


class View:
    def __init__(self, path, *, readonly=False):
        path = safe_path(path)
        self.db = (
            sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
            if readonly
            else sqlite3.connect(path)
        )
        self.db.execute("pragma cache_size=-8192")
        self.db.execute("pragma temp_store=FILE")
        if readonly:
            return
        self.db.executescript("""
        create table if not exists members(id text primary key, entity text,
          start integer, end integer, known integer, payload text);
        create index if not exists member_lookup on members(entity,known desc,start,end);
        create table if not exists coverage(id text primary key, cell text,
          stage text, known integer, payload text);
        create index if not exists coverage_lookup on coverage(cell,stage,known desc);
        """)

    def ingest(self, store, pin):
        kind = manifest(store, pin)["kind"]
        with self.db:
            for row in records(store, pin):
                if kind == "population":
                    self.db.execute(
                        "insert or ignore into members values(?,?,?,?,?,?)",
                        (
                            digest(row),
                            row["entity"],
                            row["start_ns"],
                            row["end_ns"],
                            row["known_ns"],
                            canonical(row).decode(),
                        ),
                    )
                else:
                    self.db.execute(
                        "insert or ignore into coverage values(?,?,?,?,?)",
                        (
                            digest(row),
                            cell(row),
                            row["stage"],
                            row["known_ns"],
                            canonical(row).decode(),
                        ),
                    )

    def attributes(self, entity, at, known):
        rows = self.db.execute(
            "select known,payload from members where entity=? and start<=? and end>? "
            "and known<=? order by known desc,id limit 2",
            (entity, at, at, known),
        ).fetchall()
        if len(rows) == 2 and rows[0][0] == rows[1][0]:
            raise ValueError("population_ambiguous_metadata")
        return json.loads(rows[0][1])["attributes"] if rows else {}

    def intervals(self, start, end, known):
        """Union enrollment intervals without loading an entity's history into RAM."""
        last = None
        for e, a, b in self.db.execute(
            "select entity,max(start,?),min(end,?) from members where start<? and end>? "
            "and known<=? order by entity,start,end",
            (start, end, end, start, known),
        ):
            if last and last[0] == e and a <= last[2]:
                last = (e, last[1], max(last[2], b))
            else:
                if last:
                    yield last
                last = (e, a, b)
        if last:
            yield last

    def observation(self, key, stage, known):
        rows = self.db.execute(
            "select known,payload from coverage where cell=? and stage=? and known<=? "
            "order by known desc,id limit 2",
            (key, stage, known),
        ).fetchall()
        if len(rows) == 2 and rows[0][0] == rows[1][0]:
            raise ValueError("population_ambiguous_coverage")
        return json.loads(rows[0][1]) if rows else None

    def close(self):
        self.db.close()


def compact(store, pin):
    """Collapse ancestry while retaining every immutable time-versioned fact."""
    root = manifest(store, pin)
    with tempfile.TemporaryDirectory(prefix="population-compact-") as tmp:
        db = sqlite3.connect(Path(tmp) / "facts.sqlite")
        try:
            db.execute("pragma cache_size=-8192")
            db.execute("create table facts(id text primary key,payload text) without rowid")
            with db:
                for row in records(store, pin):
                    db.execute(
                        "insert or ignore into facts values(?,?)",
                        (digest(row), canonical(row).decode()),
                    )
            return publish(
                store,
                root["kind"],
                (json.loads(p) for (p,) in db.execute("select payload from facts order by id")),
                cutoff_ns=root["cutoff_ns"],
                definition=root["definition"],
            )
        finally:
            db.close()


def replicate(source, target, pin):
    target = safe_path(target)
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    with lock(target / ".publication.lock"):
        for name in ("objects", "snapshots", "staging"):
            (target / name).mkdir(exist_ok=True)
        pending = [pin]
        seen = set()
        nodes = []
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            value = manifest(source, key)
            nodes.append((key, value))
            pending.extend(value["parents"])

            def objects(value=value):
                for page in value["pages"]:
                    origin = object_path(source, page["pin"])
                    verify_files(origin, page["pin"])
                    yield page["pin"]
                    for shard in json.loads((origin / "page.json").read_text()):
                        yield shard["pin"]

            for obj in objects():
                origin = object_path(source, obj)
                verify_files(origin, obj)
                dest = object_path(target, obj)
                if dest.exists():
                    verify_files(dest, obj)
                    continue
                stage = Path(tempfile.mkdtemp(prefix="replica-", dir=target / "staging"))
                try:
                    shutil.copytree(origin, stage, dirs_exist_ok=True)
                    verify_files(stage, obj)
                    for f in stage.iterdir():
                        with f.open("rb") as stream:
                            os.fsync(stream.fileno())
                    sync_dir(stage)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    sync_dir(target / "objects")
                    os.rename(stage, dest)
                    sync_dir(dest.parent)
                    copied += 1
                finally:
                    if stage.exists():
                        shutil.rmtree(stage)
        # Children publish last. A requested root never points at missing parent manifests.
        remaining = dict(nodes)
        while remaining:
            ready = [k for k, v in remaining.items() if not set(v["parents"]) & remaining.keys()]
            if not ready:
                raise ValueError("population_parent_cycle")
            for k in ready:
                atomic_json(target / "snapshots" / (k + ".json"), remaining.pop(k))
    return dict(snapshot=pin, copied=copied)
