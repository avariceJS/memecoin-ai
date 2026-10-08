"""Registered research evaluation: candidate/enrollment fixed before reading evaluation payload."""

import json
import os
import shutil
import sqlite3
import tempfile
import time
from collections import Counter
from pathlib import Path

from memecoin_brain.source import EXCLUDED

from .schema import membership, target_names
from .storage import (
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


def register(
    root,
    artifact,
    artifact_pin,
    *,
    start_s,
    end_s,
    pools=None,
    scope="retrospective",
    cohorts=None,
    input_admission=None,
):
    from .predict import Predictor

    now_ns = time.time_ns()
    pools = pools or []
    if cohorts is not None and pools:
        raise ValueError("experience_evaluation_enrollment_modes")
    if (
        scope not in {"retrospective", "preregistered_forward_replay"}
        or type(start_s) is not int
        or type(end_s) is not int
        or not 1 < start_s < end_s
        or (not pools and cohorts is None)
        or len(set(pools)) != len(pools)
        or any(not isinstance(p, str) or not p or p.rsplit("/", 1)[-1] in EXCLUDED for p in pools)
        or type(now_ns) is not int
        or now_ns < 0
    ):
        raise ValueError("experience_evaluation_protocol")
    model = Predictor(artifact, artifact_pin)
    if start_s < model.report["seen_end_s"]:
        raise ValueError("experience_evaluation_seen_interval")
    if scope == "preregistered_forward_replay" and (
        start_s * 10**9 <= now_ns or model.spec["schema"].get("availability") != "point_in_time"
    ):
        raise ValueError("experience_evaluation_not_prospective")
    protocol = dict(
        version="brain-research-evaluation-v1",
        candidate=artifact_pin,
        schema=model.spec["schema"],
        start_s=start_s,
        end_s=end_s,
        pools=sorted(pools),
        scope=scope,
        registered_ns=now_ns,
        metrics="macro_horizon_pinball_coverage_and_per_pool_day_v1",
        quantiles=model.spec["quantiles"],
        data_role="research_evaluation",
        production_eligible=False,
    )
    if input_admission is not None:
        from .admission import validate_policy

        protocol["input_admission"] = validate_policy(input_admission)
    if cohorts is not None:
        from memecoin_population.evaluation import contract

        protocol["cohorts"] = contract(
            cohorts["store"], cohorts["population"], cohorts["config"], now_ns
        )
    pin = digest(protocol)
    root = safe_path(root)
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root / (pin + ".json"), protocol)
    return dict(protocol=pin, **protocol)


def score(
    protocol_root,
    protocol_pin,
    store,
    snapshot_pin,
    artifact,
    output,
    *,
    batch=256,
    population_store=None,
):
    import numpy as np

    from .learning import batches
    from .predict import Predictor
    from .storage import PIN

    if (
        not isinstance(protocol_pin, str)
        or not PIN.fullmatch(protocol_pin)
        or type(batch) is not int
        or not 1 <= batch <= 4096
    ):
        raise ValueError("experience_evaluation_reference")
    protocol = json.loads(safe_path(Path(protocol_root) / (protocol_pin + ".json")).read_text())
    if (
        digest(protocol) != protocol_pin
        or protocol.get("version") != "brain-research-evaluation-v1"
        or any(
            not isinstance(p, str) or p.rsplit("/", 1)[-1] in EXCLUDED
            for p in protocol.get("pools", [])
        )
    ):
        raise ValueError("experience_evaluation_protocol_pin")
    snapshot = Snapshot(store, snapshot_pin)
    if snapshot.manifest["binding"].get("role") != "research_evaluation":
        raise ValueError("experience_evaluation_data_role")
    if snapshot.manifest["binding"]["cutoff_ns"] < protocol["end_s"] * 10**9:
        raise ValueError("experience_evaluation_window_open")
    model = Predictor(artifact, protocol["candidate"])
    if (
        protocol["start_s"] < model.report["seen_end_s"]
        or not 1 < protocol["start_s"] < protocol["end_s"]
        or protocol["quantiles"] != model.spec["quantiles"]
        or protocol["data_role"] != "research_evaluation"
        or (not protocol["pools"] and "cohorts" not in protocol)
        or (bool(protocol["pools"]) and "cohorts" in protocol)
        or len(set(protocol["pools"])) != len(protocol["pools"])
        or protocol["scope"] not in {"retrospective", "preregistered_forward_replay"}
        or (
            protocol["scope"] == "preregistered_forward_replay"
            and (
                protocol["registered_ns"] >= protocol["start_s"] * 10**9
                or model.spec["schema"].get("availability") != "point_in_time"
            )
        )
    ):
        raise ValueError("experience_evaluation_protocol")
    if (
        "cohorts" in protocol
        and protocol["cohorts"]["cutoff_ns"] > snapshot.manifest["binding"]["cutoff_ns"]
    ):
        raise ValueError("experience_evaluation_population_cutoff")
    if ("cohorts" in protocol) != (population_store is not None):
        raise ValueError("experience_evaluation_population_transport")
    admission = snapshot.manifest["binding"].get("admission")
    if (
        (admission is None) != ("input_admission" not in protocol)
        or admission is not None
        and (
            admission["policy"] != protocol["input_admission"] or admission["selection"] is not None
        )
    ):
        raise ValueError("experience_evaluation_admission_protocol")
    if snapshot.schema != protocol["schema"] or model.spec["schema"] != snapshot.schema:
        raise ValueError("experience_evaluation_schema")
    output = safe_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    import platform

    import torch

    binding = dict(
        environment=dict(
            torch=str(torch.__version__),
            numpy=np.__version__,
            python=platform.python_version(),
            device="cpu",
        ),
        protocol=protocol_pin,
        snapshot=snapshot_pin,
        candidate=protocol["candidate"],
        code=code_identity(),
        batch=batch,
    )
    with lock(output.parent / ("." + output.name + ".evaluation.lock")):
        if output.exists():
            verify_files(output)
            result = json.loads((output / "report.json").read_text())
            if result["binding"] != binding:
                raise ValueError("experience_evaluation_binding")
            return result
        stage = Path(tempfile.mkdtemp(prefix=".evaluation-", dir=output.parent))
        db = sqlite3.connect(stage / "scratch.sqlite")
        cohorts = None
        try:
            if "cohorts" in protocol:
                from memecoin_population.evaluation import Cohorts

                cohorts = Cohorts(
                    stage,
                    population_store,
                    protocol["cohorts"],
                    protocol["start_s"],
                    protocol["end_s"],
                    snapshot.schema,
                )
            db.execute("pragma cache_size=-8192")
            db.execute("pragma temp_store=FILE")
            db.execute(
                "create table strata(key text,h integer,n integer,p real,e real,c integer, "
                "primary key(key,h)) without rowid"
            )
            plan = dict(
                train_start_s=0,
                train_end_s=1,
                validation_end_s=protocol["start_s"],
                test_end_s=protocol["end_s"],
            )
            population, denominators = Counter(), Counter()
            support = {p: 0 for p in protocol["pools"]}
            usable_support = dict(support)
            for index in range(len(snapshot.parts)):
                _, rows = snapshot.partition(index)
                for row in rows:
                    if (
                        (not cohorts.contains(row["pool"]))
                        if cohorts
                        else row["pool"] not in support
                    ):
                        raise ValueError("experience_evaluation_enrollment")
                    role, mask = membership(row, plan, snapshot.schema)
                    population[role] += 1
                    if role == "test":
                        if cohorts:
                            cohorts.observe(row, mask)
                        else:
                            support[row["pool"]] += 1
                            usable_support[row["pool"]] += int(any(mask))
                        population["usable"] += int(any(mask))
                        if admission is not None:
                            population["admission/" + row["_admission"]] += 1
                        for name, status, valid in zip(
                            target_names(snapshot.schema), row["target_status"], mask, strict=True
                        ):
                            denominators[f"{name}/{status}"] += 1
                            denominators[f"{name}/usable"] += int(valid)
            totals = np.zeros((len(target_names(snapshot.schema)), 4))
            with (stage / "predictions.jsonl").open("wb") as stream:
                for x, y, mask, metadata, _ in batches(
                    snapshot, plan, "test", batch, with_metadata=True
                ):
                    pred = model.predict(x)
                    empirical = np.broadcast_to(model.stats["baseline"], pred.shape)
                    q = np.array(protocol["quantiles"])
                    accum = {}
                    for i, row in enumerate(metadata):
                        decision = row.get("decision_ns", row.get("origin_s", 0) * 10**9)
                        stream.write(
                            canonical(
                                dict(
                                    id=row["id"],
                                    entity=row["pool"],
                                    decision_ns=decision,
                                    prediction=pred[i].tolist(),
                                    target_mask=mask[i].tolist(),
                                    input_available_ns=row["input_available_ns"],
                                )
                            )
                            + b"\n"
                        )
                        for h in range(len(totals)):
                            if not mask[i, h]:
                                continue
                            error, base_error = y[i, h] - pred[i, h], y[i, h] - empirical[i, h]
                            values = np.array(
                                [
                                    1,
                                    np.maximum(q * error, (q - 1) * error).mean(),
                                    np.maximum(q * base_error, (q - 1) * base_error).mean(),
                                    int(pred[i, h, 0] <= y[i, h] <= pred[i, h, 2]),
                                ]
                            )
                            totals[h] += values
                            keys = (
                                cohorts.keys(row)
                                if cohorts
                                else [
                                    "pool:" + row["pool"],
                                    "day:" + str(decision // (86400 * 10**9)),
                                ]
                            )
                            for key in keys:
                                accum.setdefault((key, h), np.zeros(4))[:] += values
                    db.executemany(
                        "insert into strata values(?,?,?,?,?,?) on conflict(key,h) do update set "
                        "n=n+excluded.n,p=p+excluded.p,e=e+excluded.e,c=c+excluded.c",
                        (
                            (key, h, int(v[0]), float(v[1]), float(v[2]), int(v[3]))
                            for (key, h), v in accum.items()
                        ),
                    )
                    db.commit()
                stream.flush()
                os.fsync(stream.fileno())
            if any(t[0] == 0 for t in totals):
                raise ValueError("experience_empty_evaluation_horizon")
            with (stage / "strata.jsonl").open("wb") as stream:
                for key, h, n, p, e, c in db.execute("select * from strata order by key,h"):
                    stream.write(
                        canonical(
                            dict(
                                stratum=key,
                                target=target_names(snapshot.schema)[h],
                                n=n,
                                pinball=p / n,
                                empirical_pinball=e / n,
                                coverage=c / n,
                            )
                        )
                        + b"\n"
                    )
                stream.flush()
                os.fsync(stream.fileno())
            result = dict(
                status="completed",
                binding=binding,
                scope=protocol["scope"],
                population=dict(population),
                denominators=dict(denominators),
                enrollment=len(support),
                supported_pools=sum(n > 0 for n in usable_support.values()),
                per_pool_records=support,
                window_closed=True,
                coverage_claim="observed_support_only_not_complete_market_coverage",
                per_pool_usable=usable_support,
                metrics=[
                    dict(
                        target=name,
                        n=int(n),
                        pinball=float(p / n),
                        empirical_pinball=float(e / n),
                        coverage=float(c / n),
                    )
                    for name, (n, p, e, c) in zip(
                        target_names(snapshot.schema), totals, strict=True
                    )
                ],
                uncertainty="not_estimated; overlapping_origins_are_dependent",
                execution="not_evaluated",
                production_eligible=False,
            )
            if cohorts:
                result.pop("per_pool_records")
                result.pop("per_pool_usable")
                result.update(cohorts.finish(stage))
                cohorts.close()
                (stage / "population.sqlite").unlink()
            db.close()
            (stage / "scratch.sqlite").unlink()
            atomic_json(stage / "protocol.json", protocol)
            atomic_json(stage / "report.json", result)
            seal_files(stage)
            os.rename(stage, output)
            sync_dir(output.parent)
            return result
        finally:
            if cohorts:
                cohorts.close()
            db.close()
            if stage.exists():
                shutil.rmtree(stage)
