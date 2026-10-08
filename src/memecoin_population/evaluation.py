"""Preregistered population-relative evaluation cohorts with disk-backed denominators."""

import json
import os

from memecoin_experience.storage import canonical, digest

from .catalog import View, manifest
from .diversity import OBJECTIVE, add_counts, classify, counts_table, enrolled_segments, policy


def config(value):
    if set(value) != {"entity_prefix", "knowledge", "axes", "time_bucket_ns", "selection"}:
        raise ValueError("population_evaluation_config")
    policy(
        dict(
            value,
            enforcement="report",
            min_train_entities=0,
            max_entity_share=1,
            max_unknown_fraction=1,
            objective=OBJECTIVE,
        )
    )
    return value


def contract(store, population, cfg, known_ns):
    config(cfg)
    pop = manifest(store, population)
    if (
        pop["kind"] != "population"
        or pop["definition"]["role"] != "research_evaluation"
        or pop["cutoff_ns"] > known_ns
    ):
        raise ValueError("population_evaluation_reference")
    return dict(population=population, config=cfg, cutoff_ns=pop["cutoff_ns"])


class Cohorts:
    def __init__(self, stage, store, binding, start, end, schema):
        if (
            contract(store, binding["population"], binding["config"], binding["cutoff_ns"])
            != binding
        ):
            raise ValueError("population_evaluation_binding")
        self.binding, self.representation = binding, digest(schema)
        self.view = View(stage / "population.sqlite")
        self.db = self.view.db
        try:
            self.view.ingest(store, binding["population"])
            self.db.execute(
                "create table support(entity text primary key,rows integer,usable integer)"
            )
            self.db.execute(
                "create table enrollment(axis text,value text,entity text,"
                "primary key(axis,value,entity)) without rowid"
            )
            counts_table(self.db)
            for name, a, b in enrolled_segments(
                self.view, binding["config"], start * 10**9, end * 10**9, binding["cutoff_ns"]
            ):
                self.db.execute("insert or ignore into support values(?,0,0)", (name,))
                for axis, value in self.facets(name, a):
                    self.db.execute(
                        "insert or ignore into enrollment values(?,?,?)", (axis, value, name)
                    )
                    add_counts(
                        self.db, {(axis, value, "population", "entity_seconds"): (b - a) / 10**9}
                    )
            if not self.db.execute("select 1 from support limit 1").fetchone():
                raise ValueError("population_empty_evaluation_enrollment")
            self.db.commit()
        except BaseException:
            self.view.close()
            raise

    def facets(self, entity, at):
        return classify(
            self.view,
            self.binding["config"],
            entity,
            at,
            self.binding["cutoff_ns"],
            self.representation,
        )

    def contains(self, pool):
        name = self.binding["config"]["entity_prefix"] + pool
        return (
            self.db.execute("select 1 from support where entity=?", (name,)).fetchone() is not None
        )

    def observe(self, row, mask):
        name = self.binding["config"]["entity_prefix"] + row["pool"]
        at = row.get("decision_ns", row.get("origin_s", 0) * 10**9)
        if not self.db.execute(
            "select 1 from members where entity=? and start<=? and end>? and known<=? limit 1",
            (name, at, at, self.binding["cutoff_ns"]),
        ).fetchone():
            raise ValueError("population_evaluation_origin_outside_enrollment")
        self.db.execute(
            "update support set rows=rows+1,usable=usable+? where entity=?", (int(any(mask)), name)
        )
        for axis, value in self.facets(name, at):
            add_counts(
                self.db,
                {(axis, value, "test", "rows"): 1, (axis, value, "test", "usable"): int(any(mask))},
            )
            add_counts(
                self.db,
                {(axis, value, "test", "target:" + str(i)): int(m) for i, m in enumerate(mask)},
            )

    def keys(self, row):
        name = self.binding["config"]["entity_prefix"] + row["pool"]
        at = row.get("decision_ns", row.get("origin_s", 0) * 10**9)
        return [
            "cohort:" + canonical([json.loads(a), json.loads(v)]).decode()
            for a, v in self.facets(name, at)
        ]

    def finish(self, stage):
        self.db.commit()
        n, supported = self.db.execute("select count(*),sum(usable>0) from support").fetchone()
        for name, query, columns in (
            (
                "population-support.jsonl",
                "select * from support order by entity",
                ("entity", "rows", "usable"),
            ),
            (
                "cohort-support.jsonl",
                "select * from counts order by axis,value,role,metric",
                ("axis", "value", "role", "metric", "n"),
            ),
            (
                "cohort-enrollment.jsonl",
                "select axis,value,count(*) from enrollment "
                "group by axis,value order by axis,value",
                ("axis", "value", "entities"),
            ),
        ):
            with (stage / name).open("wb") as stream:
                for row in self.db.execute(query):
                    stream.write(canonical(dict(zip(columns, row, strict=True))) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
        return dict(
            enrollment=n,
            supported_pools=supported,
            cohorts=self.binding,
            support_artifact="population-support.jsonl",
            cohort_denominators="cohort-support.jsonl",
            cohort_enrollment="cohort-enrollment.jsonl",
        )

    def close(self):
        self.view.close()
