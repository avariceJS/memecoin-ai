"""Immutable paged observation facts. Completeness is an assertion about a named process."""

import itertools
import json
import math
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from memecoin_population.catalog import MAX_TIME, entity

from .catalog import Parts, Writer
from .storage import (
    PIN,
    atomic_json,
    canonical,
    digest,
    file_hash,
    lock,
    logical_sources,
    object_path,
    safe_path,
    seal_files,
    sync_dir,
    verify_files,
)


def contract(value):
    if (
        set(value) != {"version", "role", "availability", "streams"}
        or value["version"] != "market-observations-v1"
        or value["role"] not in {"train", "research_evaluation"}
        or value["availability"] not in {"historical_replay", "point_in_time"}
        or not isinstance(value["streams"], dict)
        or not value["streams"]
    ):
        raise ValueError("observation_contract")
    for name, stream in value["streams"].items():
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 128
            or set(stream) != {"process", "features"}
            or not isinstance(stream["process"], str)
            or not stream["process"]
            or not isinstance(stream["features"], list)
            or not stream["features"]
        ):
            raise ValueError("observation_stream")
        names = []
        for col in stream["features"]:
            if set(col) != {"name", "unit", "modality"} or any(
                not isinstance(v, str) or not v for v in col.values()
            ):
                raise ValueError("observation_features")
            names.append(col["name"])
        if len(set(names)) != len(names):
            raise ValueError("observation_features")
    return value


def checked(row, schema, cutoff):
    common = {"kind", "entity", "available_ns", "evidence"}
    fields = {
        "enrollment": {"start_ns", "end_ns"},
        "event": {"stream", "id", "event_ns", "values"},
        "support": {"stream", "start_ns", "end_ns", "status"},
        "lifecycle": {"id", "event_ns", "transition"},
    }
    kind = row.get("kind")
    if kind not in fields or set(row) != common | fields[kind]:
        raise ValueError("observation_record")
    entity(row["entity"])
    at = row["available_ns"]
    if type(at) is not int or not 0 <= at <= cutoff:
        raise ValueError("observation_availability")
    if (
        not isinstance(row["evidence"], list)
        or not row["evidence"]
        or any(not isinstance(p, str) or not PIN.fullmatch(p) for p in row["evidence"])
    ):
        raise ValueError("observation_evidence")
    if kind in {"enrollment", "support"}:
        a, b = row["start_ns"], row["end_ns"]
        if any(type(t) is not int for t in (a, b)) or not 0 <= a < b <= MAX_TIME:
            raise ValueError("observation_interval")
    else:
        if (
            type(row["event_ns"]) is not int
            or not 0 <= row["event_ns"] <= at
            or not isinstance(row["id"], str)
            or not row["id"]
        ):
            raise ValueError("observation_event_time")
    if kind in {"event", "support"} and row["stream"] not in schema["streams"]:
        raise ValueError("observation_stream")
    if kind == "event":
        values = row["values"]
        if (
            not isinstance(values, list)
            or len(values) != len(schema["streams"][row["stream"]]["features"])
            or any(
                v is not None and (type(v) not in (int, float) or not math.isfinite(v))
                for v in values
            )
        ):
            raise ValueError("observation_values")
    if kind == "support":
        if row["status"] not in {"complete", "unknown", "failed", "unsupported"}:
            raise ValueError("observation_support")
        if row["status"] == "complete" and at < row["end_ns"]:
            raise ValueError("observation_completeness_before_end")
    if kind == "lifecycle" and (
        not isinstance(row["transition"], str)
        or not row["transition"]
        or len(row["transition"]) > 128
    ):
        raise ValueError("observation_lifecycle")
    if len(canonical(row)) > 65536:
        raise ValueError("observation_record_size")
    return row


def key(row):
    return (
        row["entity"],
        row["kind"],
        row.get("stream", ""),
        row.get("event_ns", row.get("start_ns")),
        row.get("id", ""),
    )


def publish(store, rows, *, schema, cutoff, sources, chunk=4096):
    """Sorted stream -> CAS shards + paged catalog; no in-memory entity/event inventory."""
    contract(schema)
    if (
        type(cutoff) is not int
        or not 0 <= cutoff < MAX_TIME
        or type(chunk) is not int
        or not 1 <= chunk <= 4096
        or not sources
    ):
        raise ValueError("observation_publish")
    store = safe_path(store)
    store.mkdir(parents=True, exist_ok=True)
    binding = dict(
        contract=schema,
        cutoff_ns=cutoff,
        sources=logical_sources(sources),
        chunk=chunk,
        implementation=file_hash(Path(__file__)),
    )
    with lock(store / ".publication.lock"):
        for part in ("objects", "staging", "observation-sources"):
            safe_path(store / part).mkdir(exist_ok=True)
        writer = Writer(store)
        previous = None

        def valid():
            nonlocal previous
            for row in rows:
                checked(row, schema, cutoff)
                if previous is not None and key(row) <= previous:
                    raise ValueError("observation_order_or_duplicate")
                previous = key(row)
                yield row

        for _, stream in itertools.groupby(
            valid(), key=lambda r: (*key(r)[:3], key(r)[3] // (86400 * 10**9))
        ):
            while batch := list(itertools.islice(stream, chunk)):
                stage = Path(tempfile.mkdtemp(prefix="observations-", dir=store / "staging"))
                try:
                    with (stage / "facts.jsonl").open("wb") as f:
                        for row in batch:
                            f.write(canonical(row) + b"\n")
                        f.flush()
                        os.fsync(f.fileno())
                    pin = seal_files(stage)
                    target = object_path(store, pin)
                    target.parent.mkdir(exist_ok=True)
                    sync_dir(store / "objects")
                    if target.exists():
                        verify_files(target, pin)
                    else:
                        os.rename(stage, target)
                        sync_dir(target.parent)
                    writer.append(dict(pin=pin, count=len(batch)))
                finally:
                    if stage.exists():
                        shutil.rmtree(stage)
        if not writer.count:
            raise ValueError("observation_empty_source")
        manifest = dict(
            binding=binding,
            **writer.finish(),
            records=writer.decisions,
            production_eligible=False,
        )
        pin = digest(manifest)
        atomic_json(store / "observation-sources" / (pin + ".json"), manifest)
        return pin


class Source:
    def __init__(self, store, pin, *, role):
        self.store = safe_path(store)
        if not isinstance(pin, str) or not PIN.fullmatch(pin):
            raise ValueError("observation_pin")
        self.manifest = json.loads(
            (self.store / "observation-sources" / (pin + ".json")).read_text()
        )
        if digest(self.manifest) != pin:
            raise ValueError("observation_checksum")
        self.schema = contract(self.manifest["binding"]["contract"])
        # Check role before opening any payload.
        if role != self.schema["role"]:
            raise ValueError("observation_reserved_role")
        self.pin = pin
        self.parts = (
            self.manifest.get("partitions")
            if "partitions" in self.manifest
            else Parts(self.store, dict(self.manifest, decisions=self.manifest["records"]))
        )
        if sum(p["count"] for p in self.parts) != self.manifest["records"]:
            raise ValueError("observation_count")

    def rows(self, *, with_provenance=False):
        previous = None
        for part in self.parts:
            path = object_path(self.store, part["pin"])
            verify_files(path, part["pin"])
            count = 0
            with (path / "facts.jsonl").open() as f:
                for line in f:
                    row = checked(
                        json.loads(line), self.schema, self.manifest["binding"]["cutoff_ns"]
                    )
                    if previous is not None and key(row) <= previous:
                        raise ValueError("observation_order_or_duplicate")
                    previous = key(row)
                    count += 1
                    yield (row, part["pin"]) if with_provenance else row
            if count != part["count"]:
                raise ValueError("observation_count")

    def index(self, path):
        """Disposable query index; bounded cache, sealed facts remain the source of truth."""
        db = sqlite3.connect(path)
        try:
            db.executescript("""pragma cache_size=-8192; pragma temp_store=FILE;
            create table members(entity text primary key,start integer,end integer,
              available integer,
              payload text);
            create table events(entity text,stream text,t integer,available integer,
              id text,rank integer,
              payload text,
              primary key(entity,stream,t,id)) without rowid;
            create table support(entity text,stream text,start integer,end integer,
              available integer,
              payload text,
              primary key(entity,stream,start)) without rowid;
            create table lifecycle(entity text,t integer,available integer,id text,
              payload text,
              primary key(entity,t,id)) without rowid;""")
            previous_end = {}
            event_group, event_rank = None, -1
            for n, (row, shard) in enumerate(self.rows(with_provenance=True)):
                k = row["kind"]
                e = row["entity"]
                payload = canonical(dict(row, _source_shard=shard)).decode()
                a = row["available_ns"]
                if k == "enrollment":
                    db.execute(
                        "insert into members values(?,?,?,?,?)",
                        (e, row["start_ns"], row["end_ns"], a, payload),
                    )
                elif k == "event":
                    group = (e, row["stream"])
                    event_rank = event_rank + 1 if group == event_group else 0
                    event_group = group
                    db.execute(
                        "insert into events values(?,?,?,?,?,?,?)",
                        (e, row["stream"], row["event_ns"], a, row["id"], event_rank, payload),
                    )
                elif k == "lifecycle":
                    db.execute(
                        "insert into lifecycle values(?,?,?,?,?)",
                        (e, row["event_ns"], a, row["id"], payload),
                    )
                else:
                    group = (e, row["stream"])
                    # Rows are grouped by entity/stream; only one previous interval is needed.
                    if previous_end.get("group") == group and row["start_ns"] < previous_end["end"]:
                        raise ValueError("observation_overlapping_support")
                    previous_end = dict(group=group, end=row["end_ns"])
                    db.execute(
                        "insert into support values(?,?,?,?,?,?)",
                        (e, row["stream"], row["start_ns"], row["end_ns"], a, payload),
                    )
                if n % 4096 == 0:
                    db.commit()
            for table, lo, hi in (
                ("events", "t", "t"),
                ("lifecycle", "t", "t"),
                ("support", "start", "end-1"),
            ):
                missing = db.execute(
                    f"select 1 from {table} r left join members m on r.entity=m.entity "
                    f"where m.entity is null or r.{lo}<m.start or r.{hi}>=m.end limit 1"
                ).fetchone()
                if missing:
                    raise ValueError("observation_outside_enrollment")
            inconsistent = db.execute(
                "select 1 from support s join events e on e.entity=s.entity "
                "and e.stream=s.stream and e.t>=s.start and e.t<s.end "
                "where json_extract(s.payload,'$.status')='complete' "
                "and e.available>s.available limit 1"
            ).fetchone()
            if inconsistent:
                raise ValueError("observation_premature_completeness")
            db.execute(
                "create index lifecycle_transition on lifecycle "
                "(entity,json_extract(payload,'$.transition'),t)"
            )
            db.execute("create index event_arrivals on events(entity,stream,available,id)")
            db.execute("create index lifecycle_arrivals on lifecycle(entity,available,id)")
            db.execute("create unique index event_ids on events(entity,stream,id)")
            db.execute("create unique index lifecycle_ids on lifecycle(entity,id)")
            db.commit()
            return db
        except BaseException:
            db.close()
            raise


def replicate(source, target, pin, *, role):
    """Move the normalized source without changing identity; upstream evidence stays pinned."""
    origin = Source(source, pin, role=role)
    target = safe_path(target)
    target.mkdir(parents=True, exist_ok=True)
    copied = reused = 0
    with lock(target / ".publication.lock"):
        for name in ("objects", "staging", "observation-sources"):
            safe_path(target / name).mkdir(exist_ok=True)

        def objects():
            yield from origin.manifest.get("partition_pages", [])
            yield from origin.parts

        for entry in objects():
            pin_object = entry["pin"]
            dest = object_path(target, pin_object)
            if dest.exists():
                verify_files(dest, pin_object)
                reused += 1
                continue
            src = object_path(origin.store, pin_object)
            verify_files(src, pin_object)
            stage = Path(tempfile.mkdtemp(prefix="observation-replica-", dir=target / "staging"))
            try:
                shutil.copytree(src, stage, dirs_exist_ok=True)
                verify_files(stage, pin_object)
                for p in stage.iterdir():
                    with p.open("rb") as f:
                        os.fsync(f.fileno())
                sync_dir(stage)
                dest.parent.mkdir(exist_ok=True)
                sync_dir(target / "objects")
                os.rename(stage, dest)
                sync_dir(dest.parent)
                copied += 1
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        atomic_json(target / "observation-sources" / (pin + ".json"), origin.manifest)
    return dict(source=pin, copied=copied, reused=reused)
