"""Compose disjoint immutable experience increments without rewriting their payloads."""

import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from .storage import Snapshot, atomic_json, code_identity, digest, lock, safe_path


def bounds(rows, schema=None):
    if schema is not None and schema.get("schema") == "brain-experience-v3":
        from .coordinate_samples import coordinate

        if (
            len({r["pool"] for r in rows}) != 1
            or len({r["origin"]["domain_pin"] for r in rows}) != 1
        ):
            raise ValueError("experience_partition_entity")
        return dict(
            entity=rows[0]["pool"],
            coordinate_domain_pin=rows[0]["origin"]["domain_pin"],
            first_coordinate=min(coordinate(r) for r in rows),
            last_coordinate=max(coordinate(r) for r in rows),
        )
    times = [r["decision_ns"] if "decision_ns" in r else r["origin_s"] * 10**9 for r in rows]
    if len({r["pool"] for r in rows}) != 1:
        raise ValueError("experience_partition_entity")
    return dict(entity=rows[0]["pool"], first_ns=min(times), last_ns=max(times))


def compose(store, pins):
    if not pins or len(set(pins)) != len(pins):
        raise ValueError("experience_composition_inputs")
    store = safe_path(store)
    snapshots = [Snapshot(store, pin) for pin in sorted(pins)]
    if any("admission" in s.manifest["binding"] for s in snapshots):
        raise ValueError("experience_compose_before_admission")
    schema = snapshots[0].schema
    coordinate = schema.get("schema") == "brain-experience-v3"
    role = snapshots[0].manifest["binding"].get("role", "train")
    if any(
        s.schema != schema or s.manifest["binding"].get("role", "train") != role for s in snapshots
    ):
        raise ValueError("experience_composition_contract")
    with (
        lock(store / ".publication.lock"),
        tempfile.TemporaryDirectory(prefix="experience-compose-") as tmp,
    ):
        db = sqlite3.connect(Path(tmp) / "ranges.sqlite")
        try:
            db.execute("pragma cache_size=-8192")
            db.execute(
                "create table ranges(pin text primary key, entity text, domain text, "
                "start integer, end integer, part text)"
            )
            import json

            for snapshot in snapshots:
                for i, entry in enumerate(snapshot.parts):
                    if db.execute("select 1 from ranges where pin=?", (entry["pin"],)).fetchone():
                        continue
                    extent = entry.get("bounds")
                    if extent is None:
                        if coordinate:
                            raise ValueError("experience_coordinate_composition_bounds_required")
                        _, rows = snapshot.partition(i)
                        extent = bounds(rows)
                    # Bounds are authenticated by the snapshot, checked against payload on read.
                    db.execute(
                        "insert into ranges values(?,?,?,?,?,?)",
                        (
                            entry["pin"],
                            extent["entity"],
                            extent.get("coordinate_domain_pin"),
                            extent["first_coordinate"] if coordinate else extent["first_ns"],
                            extent["last_coordinate"] if coordinate else extent["last_ns"],
                            json.dumps(dict(entry, bounds=extent)),
                        ),
                    )
            db.commit()
            from .catalog import Writer

            parts, statuses = Writer(store), Counter()
            previous = None
            for entity, domain, start, end, raw in db.execute(
                "select entity,domain,start,end,part from ranges order by entity,domain,start,pin"
            ):
                if previous and (entity, domain) == previous[:2] and start <= previous[2]:
                    raise ValueError("experience_composition_overlap")
                previous = entity, domain, end
                entry = json.loads(raw)
                parts.append(entry)
                statuses.update(entry["statuses"])
            manifest = dict(
                binding=dict(
                    contract=schema,
                    role=role,
                    sources=[dict(kind="snapshot", pin=s.pin) for s in snapshots],
                    cutoff_ns=max(s.manifest["binding"]["cutoff_ns"] for s in snapshots),
                    chunk=max(s.manifest["binding"]["chunk"] for s in snapshots),
                    code=code_identity(),
                ),
                **parts.finish(),
                decisions=parts.decisions,
                target_statuses=dict(statuses),
                source_counts=None,
                composition_counts=dict(parent_snapshots=len(pins), unique_partitions=parts.count),
                source_accounting=dict(
                    mode="per_parent_not_additive",
                    parents={s.pin: s.manifest.get("source_counts") for s in snapshots},
                ),
                composition=(
                    "disjoint_entity_native_coordinate_intervals; identical_objects_deduplicated"
                    if coordinate
                    else "disjoint_entity_decision_intervals; identical_objects_deduplicated"
                ),
                production_eligible=False,
            )
            pin = digest(manifest)
            atomic_json(store / "snapshots" / (pin + ".json"), manifest)
            return dict(snapshot=pin, **manifest)
        finally:
            db.close()
