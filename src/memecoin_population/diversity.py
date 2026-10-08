"""Population-relative diversity audit. Counts and gates never resample or reweight rows."""

import json
import math
import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from memecoin_experience.schema import membership, target_names
from memecoin_experience.storage import (
    Snapshot,
    atomic_json,
    canonical,
    code_identity,
    digest,
    lock,
    safe_path,
    seal_files,
    sync_dir,
    verify_files,
)

from .catalog import MAX_TIME, View, manifest
from .planning import selected

OBJECTIVE = "full_pass_macro_present_horizon_batch_mean_v1"
UNKNOWN = "__unknown__"


def policy(value):
    if set(value) != {
        "entity_prefix",
        "knowledge",
        "axes",
        "time_bucket_ns",
        "selection",
        "enforcement",
        "min_train_entities",
        "max_entity_share",
        "max_unknown_fraction",
        "objective",
    }:
        raise ValueError("diversity_policy")
    if (
        value["knowledge"] not in {"at_decision", "at_cutoff"}
        or value["enforcement"] not in {"report", "require"}
        or value["objective"] != OBJECTIVE
    ):
        raise ValueError("diversity_policy_semantics")
    if (
        not isinstance(value["entity_prefix"], str)
        or not value["entity_prefix"].endswith("/")
        or type(value["time_bucket_ns"]) is not int
        or not 0 < value["time_bucket_ns"] <= MAX_TIME
    ):
        raise ValueError("diversity_policy_mapping")
    axes = value["axes"]
    if (
        not isinstance(axes, list)
        or not 1 <= len(axes) <= 16
        or ["@entity"] not in axes
        or any(
            not isinstance(a, list)
            or not 1 <= len(a) <= 3
            or any(
                not isinstance(k, str)
                or not k
                or (k.startswith("@") and k not in {"@entity", "@period", "@representation"})
                for k in a
            )
            for a in axes
        )
    ):
        raise ValueError("diversity_policy_axes")
    if len({tuple(a) for a in axes}) != len(axes):
        raise ValueError("diversity_policy_axes")
    if (
        type(value["min_train_entities"]) is not int
        or value["min_train_entities"] < 0
        or any(
            type(value[k]) not in (int, float) or not 0 <= value[k] <= 1
            for k in ("max_entity_share", "max_unknown_fraction")
        )
    ):
        raise ValueError("diversity_policy_thresholds")
    s = value["selection"]
    if (
        set(s) != {"salt", "numerator", "denominator"}
        or not isinstance(s["salt"], str)
        or not s["salt"]
        or any(type(s[k]) is not int for k in ("numerator", "denominator"))
        or not 0 < s["numerator"] <= s["denominator"] <= 2**32
    ):
        raise ValueError("diversity_policy_selection")
    return value


def classify(view, config, entity, at, cutoff, representation):
    attrs = view.attributes(
        entity, at, min(cutoff, at) if config["knowledge"] == "at_decision" else cutoff
    )
    attrs.update(
        {
            "@entity": entity,
            "@period": str(at // config["time_bucket_ns"]),
            "@representation": representation,
        }
    )
    return [
        (canonical(axis).decode(), canonical([attrs.get(k) or UNKNOWN for k in axis]).decode())
        for axis in config["axes"]
    ]


def summary(db, table="counts", metric="usable", role="train"):
    output = {}
    for (axis,) in db.execute(
        f"select distinct axis from {table} where metric=? and role=? order by axis", (metric, role)
    ):
        n, total, square, logsum, maximum = 0, 0.0, 0.0, 0.0, 0.0
        for (v,) in db.execute(
            f"select n from {table} where axis=? and role=? and metric=? and n>0",
            (axis, role, metric),
        ):
            n += 1
            total += v
            square += v * v
            logsum += v * math.log(v)
            maximum = max(maximum, v)
        output[axis] = dict(
            supported_values=n,
            total=total,
            max_share=maximum / total if total else None,
            effective_shannon=math.exp(math.log(total) - logsum / total) if total else 0,
            effective_simpson=total * total / square if square else 0,
        )
    return output


def counts_table(db):
    db.execute(
        "create table counts(axis text,value text,role text,metric text,n "
        "real, primary key(axis,value,role,metric)) without rowid"
    )


def add_counts(db, counts):
    db.executemany(
        "insert into counts values(?,?,?,?,?) on "
        "conflict(axis,value,role,metric) do update set n=n+excluded.n",
        ((*key, n) for key, n in counts.items()),
    )


def enrolled_segments(view, config, start, end, cutoff):
    for entity, a, b in view.intervals(start, end, cutoff):
        if not selected(entity, config["selection"]):
            continue
        query = (
            "select start from members where entity=? and start>? and start<? "
            "and known<=? union select end from members where entity=? and "
            "end>? and end<? and known<=?"
        )
        params = (entity, a, b, cutoff, entity, a, b, cutoff)
        if config["knowledge"] == "at_decision":
            query += (
                " union select known from members where entity=? and known>? and "
                "known<? and known<=?"
            )
            params += (entity, a, b, cutoff)
        points = view.db.execute(query + " order by 1", params)
        previous = a
        for t in (p[0] for p in points):
            while previous < t:
                until = min(
                    t, (previous // config["time_bucket_ns"] + 1) * config["time_bucket_ns"]
                )
                yield entity, previous, until
                previous = until
        while previous < b:
            until = min(b, (previous // config["time_bucket_ns"] + 1) * config["time_bucket_ns"])
            yield entity, previous, until
            previous = until


def audit(
    store, snapshot, population_store, population, output, *, split, config, coverage_plan=None
):
    policy(config)
    from memecoin_experience.records import split_plan

    split_plan(split)
    data = Snapshot(store, snapshot)
    pop = manifest(population_store, population)
    cutoff = data.manifest["binding"]["cutoff_ns"]
    if (
        pop["kind"] != "population"
        or pop["definition"]["role"] != data.manifest["binding"].get("role", "train")
        or pop["cutoff_ns"] > cutoff
    ):
        raise ValueError("diversity_population_binding")
    coverage = None
    if coverage_plan:
        verify_files(coverage_plan["path"], coverage_plan["pin"])
        cp = json.loads((Path(coverage_plan["path"]) / "plan.json").read_text())
        if (
            cp["population"] != population
            or cp["role"] != pop["definition"]["role"]
            or cp["plan"]["selection"] != config["selection"]
            or cp["plan"]["cutoff_ns"] > cutoff
            or cp["plan"]["start_ns"] > split["train_start_s"] * 10**9
            or cp["plan"]["end_ns"] < split["test_end_s"] * 10**9
        ):
            raise ValueError("diversity_coverage_plan_binding")
        coverage = json.loads((Path(coverage_plan["path"]) / "report.json").read_text())
    binding = dict(
        version="market-diversity-audit-v1",
        code=code_identity(),
        snapshot=snapshot,
        population=population,
        split=split,
        config=config,
        coverage_plan=coverage_plan["pin"] if coverage_plan else None,
        cutoff_ns=cutoff,
    )
    output = safe_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with lock(output.parent / ("." + output.name + ".lock")):
        if output.exists():
            verify_files(output)
            result = json.loads((output / "report.json").read_text())
            if result["binding"] != binding:
                raise ValueError("diversity_audit_conflict")
            return result
        stage = Path(tempfile.mkdtemp(prefix=".diversity-", dir=output.parent))
        view = View(stage / "population.sqlite")
        db = sqlite3.connect(stage / "counts.sqlite")
        try:
            view.ingest(population_store, population)
            db.execute("pragma cache_size=-8192")
            db.execute("pragma temp_store=FILE")
            counts_table(db)
            db.execute(
                "create table enrollment(axis text,value text,entity text,primary "
                "key(axis,value,entity)) without rowid"
            )
            representation = digest(data.schema)
            enrolled = 0
            last = None
            for e, a, b in enrolled_segments(
                view, config, split["train_start_s"] * 10**9, split["test_end_s"] * 10**9, cutoff
            ):
                if e != last:
                    enrolled += 1
                    last = e
                facets = classify(view, config, e, a, cutoff, representation)
                for axis, value in facets:
                    db.execute("insert or ignore into enrollment values(?,?,?)", (axis, value, e))
                add_counts(
                    db,
                    Counter(
                        {
                            (axis, value, "population", "entity_seconds"): (b - a) / 10**9
                            for axis, value in facets
                        }
                    ),
                )
            totals = Counter()
            unknown = Counter()
            unmapped = 0
            for i in range(len(data.parts)):
                _, rows = data.partition(i)
                counts = Counter()
                for row in rows:
                    role, mask = membership(row, split, data.schema)
                    at = row.get("decision_ns", row.get("origin_s", 0) * 10**9)
                    e = config["entity_prefix"] + row["pool"]
                    included = view.db.execute(
                        "select 1 from members where entity=? and start<=? and end>? and "
                        "known<=? limit 1",
                        (e, at, at, cutoff),
                    ).fetchone()
                    if not included or not selected(e, config["selection"]):
                        unmapped += 1
                    totals[role] += 1
                    totals[role + "_usable"] += int(any(mask))
                    for axis, value in classify(view, config, e, at, cutoff, representation):
                        counts[axis, value, role, "rows"] += 1
                        counts[axis, value, role, "usable"] += int(any(mask))
                        if role == "train" and any(mask) and UNKNOWN in json.loads(value):
                            unknown[axis] += 1
                        for name, valid in zip(target_names(data.schema), mask, strict=True):
                            counts[axis, value, role, "target:" + str(name)] += int(valid)
                add_counts(db, counts)
                db.commit()
            distribution = summary(db)
            entity_stats = distribution.get('["@entity"]', dict(supported_values=0, max_share=None))
            violations = []
            if unmapped:
                violations.append("rows_outside_declared_population")
            if entity_stats["supported_values"] < config["min_train_entities"]:
                violations.append("insufficient_train_entities")
            if (
                entity_stats["max_share"] is not None
                and entity_stats["max_share"] > config["max_entity_share"]
            ):
                violations.append("entity_concentration")
            for axis, n in unknown.items():
                if n / max(1, totals["train_usable"]) > config["max_unknown_fraction"]:
                    violations.append("unknown_metadata:" + axis)
            result = dict(
                binding=binding,
                enrolled_entities=enrolled,
                population=dict(totals),
                unmapped_rows=unmapped,
                unknown_train_usable=dict(unknown),
                train_distribution=distribution,
                coverage=coverage,
                violations=violations,
                training_allowed=not violations or config["enforcement"] == "report",
                interpretation="descriptive_counts_no_reweighting_no_independence_claim",
                representation=representation,
            )
            db.commit()
            db.close()
            view.close()
            atomic_json(stage / "report.json", result)
            for name in ("counts.sqlite", "population.sqlite"):
                with (stage / name).open("rb") as f:
                    os.fsync(f.fileno())
            seal_files(stage)
            os.rename(stage, output)
            sync_dir(output.parent)
            return result
        finally:
            db.close()
            view.close()
            if stage.exists():
                shutil.rmtree(stage)
