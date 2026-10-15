"""Registered research evaluation: candidate/enrollment fixed before reading evaluation payload."""

import hashlib
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

COORDINATE_METRICS_V1 = "native_coordinate_macro_pinball_coverage_v1"
COORDINATE_METRICS_V2 = "native_coordinate_macro_pinball_coverage_train_baseline_v2"
COORDINATE_METRICS_V3 = "native_coordinate_macro_pinball_coverage_train_baseline_pool_coverage_v3"


def register_coordinate(
    root,
    artifact,
    artifact_pin,
    *,
    split_plan,
    pools,
    scope="retrospective",
):
    """Register evaluation over native coordinate intervals with a separate UTC cutoff."""
    from .coordinate_samples import domain_registry
    from .predict import Predictor
    from .records import split_plan as validate_plan

    plan = validate_plan(split_plan)
    now_ns = time.time_ns()
    pools = sorted(pools)
    if (
        plan.get("version") != "coordinate-split-plan-v1"
        or scope not in {"retrospective", "preregistered_forward_replay"}
        or not plan["roles"]["test"]["intervals"]
        or not pools
        or len(set(pools)) != len(pools)
        or any(
            not isinstance(pool, str) or not pool or pool.rsplit("/", 1)[-1] in EXCLUDED
            for pool in pools
        )
    ):
        raise ValueError("experience_coordinate_evaluation_protocol")
    model = Predictor(artifact, artifact_pin)
    schema = model.spec["schema"]
    if (
        schema.get("schema") != "brain-experience-v3"
        or schema.get("coordinate_domain_pin") != plan["coordinate_domain_pin"]
        or sorted(domain_registry(schema)) != plan["coordinate_domain_pins"]
    ):
        raise ValueError("experience_coordinate_evaluation_domain")
    from .coordinate_frontier import from_report, require_test_after

    coordinate_frontier = from_report(model.report, schema)
    require_test_after(coordinate_frontier, plan)
    if scope == "preregistered_forward_replay" and (
        schema["availability"] != "point_in_time"
        or plan["roles"]["test"]["knowledge_cutoff_ns"] <= now_ns
    ):
        raise ValueError("experience_coordinate_evaluation_not_prospective")
    protocol = dict(
        version="brain-native-coordinate-evaluation-v1",
        candidate=artifact_pin,
        schema=schema,
        coordinate_domain_pin=plan["coordinate_domain_pin"],
        seen_coordinate_frontier=coordinate_frontier,
        split_plan=plan,
        pools=pools,
        scope=scope,
        registered_ns=now_ns,
        metrics=COORDINATE_METRICS_V3,
        quantiles=model.spec["quantiles"],
        data_role="research_evaluation",
        production_eligible=False,
    )
    pin = digest(protocol)
    root = safe_path(root)
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(root / (pin + ".json"), protocol)
    return dict(protocol=pin, **protocol)


def _score_coordinate(protocol_root, protocol_pin, store, snapshot_pin, artifact, output, batch):
    import platform

    import numpy as np
    import torch

    from .coordinate_samples import domain_registry, eligibility
    from .learning import batches
    from .predict import Predictor

    protocol = json.loads(safe_path(Path(protocol_root) / (protocol_pin + ".json")).read_text())
    if (
        digest(protocol) != protocol_pin
        or protocol.get("version") != "brain-native-coordinate-evaluation-v1"
        or protocol.get("production_eligible") is not False
        or protocol.get("scope") not in {"retrospective", "preregistered_forward_replay"}
        or protocol.get("data_role") != "research_evaluation"
        or protocol.get("metrics")
        not in {COORDINATE_METRICS_V1, COORDINATE_METRICS_V2, COORDINATE_METRICS_V3}
        or type(protocol.get("registered_ns")) is not int
        or protocol["registered_ns"] < 0
        or not isinstance(protocol.get("pools"), list)
        or not protocol["pools"]
        or len(set(protocol["pools"])) != len(protocol["pools"])
        or any(
            not isinstance(pool, str) or not pool or pool.rsplit("/", 1)[-1] in EXCLUDED
            for pool in protocol["pools"]
        )
    ):
        raise ValueError("experience_coordinate_evaluation_protocol_pin")
    snapshot = Snapshot(store, snapshot_pin)
    if (
        snapshot.schema != protocol["schema"]
        or snapshot.schema.get("schema") != "brain-experience-v3"
        or snapshot.manifest["binding"].get("role") != "research_evaluation"
    ):
        raise ValueError("experience_coordinate_evaluation_snapshot")
    plan = protocol["split_plan"]
    from .records import split_plan as validate_plan

    plan = validate_plan(plan)
    if (
        plan != protocol["split_plan"]
        or plan["coordinate_domain_pin"] != snapshot.schema["coordinate_domain_pin"]
        or plan["coordinate_domain_pins"] != sorted(domain_registry(snapshot.schema))
        or snapshot.manifest["binding"]["cutoff_ns"] < plan["roles"]["test"]["knowledge_cutoff_ns"]
    ):
        raise ValueError("experience_coordinate_evaluation_binding")
    model = Predictor(artifact, protocol["candidate"])
    from .coordinate_frontier import from_report, require_test_after

    coordinate_frontier = from_report(model.report, snapshot.schema)
    require_test_after(coordinate_frontier, plan)
    if (
        model.spec["schema"] != snapshot.schema
        or model.spec["quantiles"] != protocol["quantiles"]
        or protocol["coordinate_domain_pin"] != snapshot.schema["coordinate_domain_pin"]
        or "seen_coordinate_frontier" in protocol
        and protocol["seen_coordinate_frontier"] != coordinate_frontier
        or protocol["scope"] == "preregistered_forward_replay"
        and (
            snapshot.schema["availability"] != "point_in_time"
            or protocol["registered_ns"] >= plan["roles"]["test"]["knowledge_cutoff_ns"]
        )
    ):
        raise ValueError("experience_coordinate_evaluation_candidate")
    output = safe_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
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
        schema=protocol["schema"],
        split_plan=protocol["split_plan"],
        code=code_identity(),
        batch=batch,
    )
    with lock(output.parent / ("." + output.name + ".evaluation.lock")):
        if output.exists():
            verify_files(output)
            result = json.loads((output / "report.json").read_text())
            if result["binding"] != binding:
                raise ValueError("experience_coordinate_evaluation_binding")
            return result

        target_names_v3 = target_names(snapshot.schema)
        population = Counter()
        denominators = Counter()
        census_scored_digest = hashlib.sha256()
        pools = set(protocol["pools"])
        snapshot_pools = set()
        test_assigned_pools = set()
        exact_scored_pools = set()
        for index in range(len(snapshot.parts)):
            _, rows = snapshot.partition(index)
            population["snapshot_rows"] += len(rows)
            for row in rows:
                if row["pool"] not in pools:
                    raise ValueError("experience_coordinate_evaluation_enrollment")
                snapshot_pools.add(row["pool"])
                assignment = eligibility(row, snapshot.schema, plan, "test")
                if assignment["assigned_role"] == "test":
                    test_assigned_pools.add(row["pool"])
                if (
                    protocol["scope"] == "preregistered_forward_replay"
                    and assignment["assigned_role"] == "test"
                    and row["decision_ns"] <= protocol["registered_ns"]
                ):
                    raise ValueError("experience_coordinate_evaluation_not_future")
                if assignment["assigned_role"] != "test":
                    population["outside_test_intervals"] += 1
                    continue
                population["test_assigned_origins"] += 1
                if assignment["reason"] == "input_extent_crosses_role_boundary":
                    population["test_purged_origins"] += 1
                exact = assignment["exact_mask"]
                if any(exact):
                    exact_scored_pools.add(row["pool"])
                    population["test_exact_usable_origins"] += 1
                    identity = canonical(
                        dict(
                            pool=row["pool"],
                            id=row["id"],
                            origin=row["origin"],
                            exact_mask=[bool(value) for value in exact],
                        )
                    )
                    census_scored_digest.update(len(identity).to_bytes(8, "big"))
                    census_scored_digest.update(identity)
                for name, status, exact_valid, lower_valid in zip(
                    target_names_v3,
                    row["target_status"],
                    exact,
                    assignment["lower_bound_mask"],
                    strict=True,
                ):
                    denominators[f"test/{name}/{status}"] += 1
                    denominators[f"test/{name}/exact_usable"] += int(exact_valid)
                    denominators[f"test/{name}/eligible_lower_bound"] += int(lower_valid)
                    population["test_exact_usable_cells"] += int(exact_valid)
                    population["test_pending_target_cells"] += int(status == "pending")
                    population["test_lower_bound_target_cells"] += int(status == "lower_bound")
        stage = Path(tempfile.mkdtemp(prefix=".coordinate-evaluation-", dir=output.parent))
        try:
            baseline_enabled = protocol["metrics"] in {
                COORDINATE_METRICS_V2,
                COORDINATE_METRICS_V3,
            }
            baseline_quantiles = None
            if baseline_enabled:
                raw_baseline = model.stats.get("baseline")
                if not isinstance(raw_baseline, list) or len(raw_baseline) != len(target_names_v3):
                    raise ValueError("experience_coordinate_evaluation_baseline")
                baseline_quantiles = []
                for values in raw_baseline:
                    if values is None:
                        baseline_quantiles.append(None)
                        continue
                    try:
                        quantile_values = np.asarray(values, dtype=np.float64)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("experience_coordinate_evaluation_baseline") from exc
                    if (
                        quantile_values.shape != (len(protocol["quantiles"]),)
                        or not np.isfinite(quantile_values).all()
                        or np.any(np.diff(quantile_values) < 0)
                    ):
                        raise ValueError("experience_coordinate_evaluation_baseline")
                    baseline_quantiles.append(quantile_values)
            totals = np.zeros((len(target_names_v3), 3), dtype=np.float64)
            baseline_totals = np.zeros_like(totals) if baseline_enabled else None
            scored_digest = hashlib.sha256()
            with (stage / "predictions.jsonl").open("wb") as stream:
                for x, y, mask, metadata, _ in batches(
                    snapshot, plan, "test", batch, with_metadata=True
                ):
                    prediction = model.predict(x)
                    population["test"] += len(metadata)
                    population["test_scored_origins"] += len(metadata)
                    for row, pred, truth, valid in zip(metadata, prediction, y, mask, strict=True):
                        identity = canonical(
                            dict(
                                pool=row["pool"],
                                id=row["id"],
                                origin=row["origin"],
                                exact_mask=[bool(value) for value in valid],
                            )
                        )
                        scored_digest.update(len(identity).to_bytes(8, "big"))
                        scored_digest.update(identity)
                        stream.write(
                            canonical(
                                dict(
                                    id=row["id"],
                                    entity=row["pool"],
                                    origin=row["origin"],
                                    decision_ns=row["decision_ns"],
                                    input_available_ns=row["input_available_ns"],
                                    target_status=row["target_status"],
                                    target_available_ns=row["target_available_ns"],
                                    prediction=pred.tolist(),
                                    target_mask=valid.tolist(),
                                )
                            )
                            + b"\n"
                        )
                        for h, selected in enumerate(valid):
                            if not selected:
                                continue
                            denominators[f"test/{target_names_v3[h]}/scored_exact_usable"] += 1
                            error = truth[h] - pred[h]
                            quantiles = np.asarray(protocol["quantiles"])
                            totals[h] += [
                                1,
                                np.maximum(quantiles * error, (quantiles - 1) * error).mean(),
                                int(pred[h, 0] <= truth[h] <= pred[h, -1]),
                            ]
                            if baseline_enabled and baseline_quantiles[h] is not None:
                                baseline_error = truth[h] - baseline_quantiles[h]
                                baseline_totals[h] += [
                                    1,
                                    np.maximum(
                                        quantiles * baseline_error,
                                        (quantiles - 1) * baseline_error,
                                    ).mean(),
                                    int(
                                        baseline_quantiles[h][0]
                                        <= truth[h]
                                        <= baseline_quantiles[h][-1]
                                    ),
                                ]
                stream.flush()
                os.fsync(stream.fileno())
            if (
                population["test_scored_origins"] != population["test_exact_usable_origins"]
                or scored_digest.digest() != census_scored_digest.digest()
                or any(
                    totals[h, 0] != denominators[f"test/{name}/exact_usable"]
                    for h, name in enumerate(target_names_v3)
                )
            ):
                raise ValueError("experience_coordinate_evaluation_census")
            if not np.any(totals[:, 0]):
                raise ValueError("experience_coordinate_empty_evaluation")
            metric_rows = []
            for index, (name, values) in enumerate(zip(target_names_v3, totals, strict=True)):
                metric = dict(
                    target=name,
                    n=int(values[0]),
                    pinball=float(values[1] / values[0]) if values[0] else None,
                    coverage=float(values[2] / values[0]) if values[0] else None,
                )
                if baseline_enabled:
                    baseline = baseline_quantiles[index]
                    baseline_n = int(baseline_totals[index, 0])
                    if baseline is None:
                        if baseline_n != 0:
                            raise ValueError("experience_coordinate_evaluation_baseline_census")
                        metric["train_baseline"] = dict(
                            n=0,
                            pinball=None,
                            coverage=None,
                            unavailable_reason="no_exact_train_baseline",
                        )
                    else:
                        if baseline_n != int(values[0]):
                            raise ValueError("experience_coordinate_evaluation_baseline_census")
                        metric["train_baseline"] = dict(
                            n=baseline_n,
                            pinball=(
                                float(baseline_totals[index, 1] / baseline_n)
                                if baseline_n
                                else None
                            ),
                            coverage=(
                                float(baseline_totals[index, 2] / baseline_n)
                                if baseline_n
                                else None
                            ),
                            unavailable_reason=(
                                None if baseline_n else "no_exact_evaluation_cells"
                            ),
                        )
                metric_rows.append(metric)
            report = dict(
                status="completed",
                binding=binding,
                scope=protocol["scope"],
                coordinate_domain_pin=protocol["coordinate_domain_pin"],
                test_intervals=plan["roles"]["test"]["intervals"],
                population=dict(population),
                denominators=dict(denominators),
                metrics=metric_rows,
                availability="coordinate_replay; no slot_to_utc_conversion",
                uncertainty="not_estimated; adjacent native-coordinate origins may depend",
                execution="not_evaluated",
                production_eligible=False,
            )
            if protocol["metrics"] == COORDINATE_METRICS_V3:
                report["pool_coverage"] = dict(
                    registered=len(pools),
                    present_in_snapshot=len(snapshot_pools),
                    test_assigned=len(test_assigned_pools),
                    exact_scored=len(exact_scored_pools),
                    missing_registered=len(pools - snapshot_pools),
                    missing_pool_ids=sorted(pools - snapshot_pools),
                    present_without_exact_scored=sorted(snapshot_pools - exact_scored_pools),
                )
            atomic_json(stage / "protocol.json", protocol)
            atomic_json(stage / "report.json", report)
            seal_files(stage)
            os.rename(stage, output)
            sync_dir(output.parent)
            return report
        finally:
            if stage.exists():
                shutil.rmtree(stage)


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
    if protocol.get("version") == "brain-native-coordinate-evaluation-v1":
        return _score_coordinate(
            protocol_root, protocol_pin, store, snapshot_pin, artifact, output, batch
        )
    if (
        digest(protocol) != protocol_pin
        or protocol.get("version") != "brain-research-evaluation-v1"
        or protocol.get("production_eligible") is not False
        or protocol.get("metrics") != "macro_horizon_pinball_coverage_and_per_pool_day_v1"
        or type(protocol.get("registered_ns")) is not int
        or protocol["registered_ns"] < 0
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
            census_scored_digest = hashlib.sha256()
            census_scored_origins = 0
            exact_cells = Counter()
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
                            exact_cells[name] += int(valid)
                        if any(mask):
                            census_scored_origins += 1
                            identity = canonical(
                                dict(
                                    pool=row["pool"],
                                    id=row["id"],
                                    decision_ns=row.get("decision_ns"),
                                    origin_s=row.get("origin_s"),
                                    exact_mask=[bool(value) for value in mask],
                                )
                            )
                            census_scored_digest.update(len(identity).to_bytes(8, "big"))
                            census_scored_digest.update(identity)
            totals = np.zeros((len(target_names(snapshot.schema)), 4))
            scored_digest = hashlib.sha256()
            scored_origins = 0
            with (stage / "predictions.jsonl").open("wb") as stream:
                for x, y, mask, metadata, _ in batches(
                    snapshot, plan, "test", batch, with_metadata=True
                ):
                    pred = model.predict(x)
                    baseline = model.stats["baseline"]
                    empirical = np.broadcast_to(
                        np.array([v if v is not None else [0.0, 0.0, 0.0] for v in baseline]),
                        pred.shape,
                    )
                    q = np.array(protocol["quantiles"])
                    accum = {}
                    for i, row in enumerate(metadata):
                        valid = mask[i]
                        identity = canonical(
                            dict(
                                pool=row["pool"],
                                id=row["id"],
                                decision_ns=row.get("decision_ns"),
                                origin_s=row.get("origin_s"),
                                exact_mask=[bool(value) for value in valid],
                            )
                        )
                        scored_digest.update(len(identity).to_bytes(8, "big"))
                        scored_digest.update(identity)
                        scored_origins += 1
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
                            error = y[i, h] - pred[i, h]
                            values = np.array(
                                [
                                    1,
                                    np.maximum(q * error, (q - 1) * error).mean(),
                                    0.0,
                                    int(pred[i, h, 0] <= y[i, h] <= pred[i, h, 2]),
                                ]
                            )
                            if baseline[h] is not None:
                                base_error = y[i, h] - empirical[i, h]
                                values[2] = np.maximum(q * base_error, (q - 1) * base_error).mean()
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
            if (
                scored_origins != census_scored_origins
                or scored_digest.digest() != census_scored_digest.digest()
                or any(
                    totals[index, 0] != exact_cells[name]
                    for index, name in enumerate(target_names(snapshot.schema))
                )
            ):
                raise ValueError("experience_evaluation_census")
            if not any(t[0] > 0 for t in totals):
                raise ValueError("experience_empty_evaluation_horizon")
            with (stage / "strata.jsonl").open("wb") as stream:
                for key, h, n, p, e, c in db.execute("select * from strata order by key,h"):
                    stream.write(
                        canonical(
                            dict(
                                stratum=key,
                                target=target_names(snapshot.schema)[h],
                                n=n,
                                pinball=p / n if n else None,
                                empirical_pinball=(
                                    e / n if n and model.stats["baseline"][h] is not None else None
                                ),
                                coverage=c / n if n else None,
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
                        pinball=float(p / n) if n else None,
                        empirical_pinball=(
                            float(e / n) if n and model.stats["baseline"][i] is not None else None
                        ),
                        coverage=float(c / n) if n else None,
                    )
                    for i, (name, (n, p, e, c)) in enumerate(
                        zip(target_names(snapshot.schema), totals, strict=True)
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
