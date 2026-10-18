"""Provider-independent sparse coverage work plans. No IO dispatch or implicit retries."""

import json
import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from memecoin_experience.storage import (
    atomic_json,
    digest,
    lock,
    safe_path,
    seal_files,
    sync_dir,
    verify_files,
)

from .catalog import MAX_TIME, View, cell, manifest


def validate(plan):
    if (
        set(plan) != {"start_ns", "end_ns", "cutoff_ns", "streams", "selection", "partitions"}
        or any(type(plan[k]) is not int for k in ("start_ns", "end_ns", "cutoff_ns", "partitions"))
        or not (
            0 <= plan["start_ns"] < plan["end_ns"] <= MAX_TIME
            and 0 <= plan["cutoff_ns"] <= MAX_TIME
            and 1 <= plan["partitions"] <= 65536
        )
    ):
        raise ValueError("population_work_plan")
    selection = plan["selection"]
    if (
        set(selection) != {"salt", "numerator", "denominator"}
        or not isinstance(selection["salt"], str)
        or not selection["salt"]
        or any(type(selection[k]) is not int for k in ("numerator", "denominator"))
        or not 0 < selection["numerator"] <= selection["denominator"] <= 2**32
    ):
        raise ValueError("population_selection")
    if not plan["streams"] or len({s["name"] for s in plan["streams"]}) != len(plan["streams"]):
        raise ValueError("population_streams")
    for stream in plan["streams"]:
        if (
            set(stream)
            != {"name", "source", "modality", "representation", "unit", "tile_ns", "cadence_ns"}
            or any(
                not isinstance(stream[k], str) or not stream[k]
                for k in ("name", "source", "modality", "representation", "unit")
            )
            or type(stream["tile_ns"]) is not int
            or not 0 < stream["tile_ns"] <= MAX_TIME
        ):
            raise ValueError("population_stream_contract")
        cadence = stream["cadence_ns"]
        if cadence is not None and (
            type(cadence) is not int
            or not 0 < cadence <= stream["tile_ns"]
            or stream["tile_ns"] % cadence
        ):
            raise ValueError("population_stream_cadence")
    return plan


def selected(entity, selection):
    return (
        int(digest([selection["salt"], entity]), 16) % selection["denominator"]
        < selection["numerator"]
    )


def action(capture, qualified, expected):
    if qualified:
        if qualified["status"] == "unsupported":
            return "semantic_review"
        if qualified["status"] in {"complete", "empty"}:
            return (
                "inspect_deficit"
                if expected is not None and qualified["observed"] < expected
                else "covered"
            )
        if qualified["status"] == "partial":
            return "inspect_deficit"
        if qualified["status"] == "stopped":
            return "review_stop"
        if qualified["status"] == "failed":
            return "review_failure"
    if capture:
        if capture["status"] in {"complete", "partial", "empty"}:
            return "qualify_existing"
        if capture["status"] == "failed":
            return "review_failure"
        if capture["status"] == "stopped":
            return "review_stop"
        if capture["status"] == "unsupported":
            return "source_review"
        if capture["status"] == "unknown":
            return "reconcile_unknown"
    if qualified and qualified["status"] == "unknown":
        return "reconcile_unknown"
    return "acquire_unobserved"


def create(store, population, output, plan, *, coverage=None):
    validate(plan)
    pop = manifest(store, population)
    if pop["kind"] != "population" or pop["cutoff_ns"] > plan["cutoff_ns"]:
        raise ValueError("population_plan_reference")
    if coverage:
        cov = manifest(store, coverage)
        if (
            cov["kind"] != "coverage"
            or cov["definition"]["role"] != pop["definition"]["role"]
            or cov["cutoff_ns"] > plan["cutoff_ns"]
        ):
            raise ValueError("population_coverage_reference")
    binding = dict(
        version="market-coverage-plan-v1",
        population=population,
        coverage=coverage,
        role=pop["definition"]["role"],
        plan=plan,
    )
    output = safe_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with lock(output.parent / ("." + output.name + ".lock")):
        if output.exists():
            verify_files(output)
            if json.loads((output / "plan.json").read_text()) != binding:
                raise ValueError("population_plan_conflict")
            return json.loads((output / "report.json").read_text())
        stage = Path(tempfile.mkdtemp(prefix=".coverage-", dir=output.parent))
        view = View(stage / "facts.sqlite")
        db = sqlite3.connect(stage / "cells.sqlite")
        try:
            view.ingest(store, population)
            if coverage:
                view.ingest(store, coverage)
            db.execute("pragma cache_size=-8192")
            db.execute(
                "create table cells(id text primary key, entity text, stream text, "
                "start integer, end integer, worker integer, action text, payload "
                "text)"
            )
            counts = Counter()
            support = {
                s["name"]: dict(
                    cells=0,
                    observed_cells=0,
                    observed_total=0,
                    expected_total=0 if s["cadence_ns"] else None,
                )
                for s in plan["streams"]
            }
            entities = 0
            previous = None
            for entity, start, end in view.intervals(
                plan["start_ns"], plan["end_ns"], plan["cutoff_ns"]
            ):
                if not selected(entity, plan["selection"]):
                    continue
                if entity != previous:
                    entities += 1
                    previous = entity
                for stream in plan["streams"]:
                    tile = stream["tile_ns"]
                    at = start
                    while at < end:
                        until = min(end, (at // tile + 1) * tile)
                        cadence = stream["cadence_ns"]
                        expected = (
                            None
                            if cadence is None
                            else (until + cadence - 1) // cadence - (at + cadence - 1) // cadence
                        )
                        request = dict(
                            entity=entity,
                            stream=stream["name"],
                            contract=digest(stream),
                            start_ns=at,
                            end_ns=until,
                            unit=stream["unit"],
                        )
                        key = cell(request)
                        capture = view.observation(key, "capture", plan["cutoff_ns"])
                        qualified = view.observation(key, "qualified", plan["cutoff_ns"])
                        if any(o and o["expected"] != expected for o in (capture, qualified)):
                            raise ValueError("population_coverage_denominator_conflict")
                        next_action = action(capture, qualified, expected)
                        row = dict(
                            id=key,
                            **request,
                            expected=expected,
                            observed=qualified["observed"] if qualified else None,
                            action=next_action,
                            capture=capture,
                            qualified=qualified,
                        )
                        db.execute(
                            "insert into cells values(?,?,?,?,?,?,?,?)",
                            (
                                key,
                                entity,
                                stream["name"],
                                at,
                                until,
                                int(key, 16) % plan["partitions"],
                                next_action,
                                json.dumps(row, sort_keys=True),
                            ),
                        )
                        counts[next_action] += 1
                        total = support[stream["name"]]
                        total["cells"] += 1
                        if expected is not None:
                            total["expected_total"] += expected
                        if qualified and qualified["observed"] is not None:
                            total["observed_cells"] += 1
                            total["observed_total"] += qualified["observed"]
                        at = until
            db.execute("create index work_partition on cells(worker,id)")
            db.commit()
            db.close()
            view.close()
            (stage / "facts.sqlite").unlink()
            report = dict(
                binding=digest(binding),
                enrolled_entities=entities,
                cells=sum(counts.values()),
                actions=dict(counts),
                streams=support,
                execution="planning_only_no_requests_or_retry_authority",
                unknown_expected="event_streams_have_no_invented_denominator",
            )
            atomic_json(stage / "plan.json", binding)
            atomic_json(stage / "report.json", report)
            with (stage / "cells.sqlite").open("rb") as f:
                os.fsync(f.fileno())
            seal_files(stage)
            os.rename(stage, output)
            sync_dir(output.parent)
            return report
        finally:
            db.close()
            view.close()
            if stage.exists():
                shutil.rmtree(stage)


def work(output, pin, partition=None):
    verify_files(output, pin)
    binding = json.loads((Path(output) / "plan.json").read_text())
    if partition is not None and (
        type(partition) is not int or not 0 <= partition < binding["plan"]["partitions"]
    ):
        raise ValueError("population_worker_partition")
    db = sqlite3.connect(
        (Path(output) / "cells.sqlite").absolute().as_uri() + "?mode=ro&immutable=1", uri=True
    )
    try:
        query, args = (
            ("select payload from cells order by id", ())
            if partition is None
            else ("select payload from cells where worker=? order by id", (partition,))
        )
        for (payload,) in db.execute(query, args):
            yield json.loads(payload)
    finally:
        db.close()
