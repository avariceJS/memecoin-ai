"""Sparse event-time materialization with explicit observation-process/censoring semantics."""

import json
import math
import tempfile
from collections import Counter
from pathlib import Path

from . import observations
from .numerics import log_ratio
from .schema import validate
from .storage import build, digest, file_hash

NS = 10**9


def validate_plan(plan, source):
    if source.schema["version"] == "market-observations-v2" and any(
        stream["coordinate_domain"]["kind"] != "utc_unix_ns"
        or stream["coordinate_domain"]["unit"] != "ns"
        for stream in source.schema["streams"].values()
    ):
        raise ValueError("sparse_non_utc_coordinate")
    required = {
        "stream",
        "context",
        "max_history_ns",
        "targets",
        "lifecycle",
        "lifecycle_origins",
        "relative_features",
    }
    if (
        set(plan) != required
        or plan["stream"] not in source.schema["streams"]
        or type(plan["context"]) is not int
        or not 1 <= plan["context"] <= 4096
        or type(plan["max_history_ns"]) is not int
        or not 0 < plan["max_history_ns"] < observations.MAX_TIME
        or type(plan["lifecycle_origins"]) is not bool
    ):
        raise ValueError("sparse_plan")
    features = [c["name"] for c in source.schema["streams"][plan["stream"]]["features"]]
    for field in ("lifecycle", "relative_features"):
        v = plan[field]
        if (
            not isinstance(v, list)
            or len(v) > 32
            or any(not isinstance(s, str) or not s for s in v)
            or len(set(v)) != len(v)
        ):
            raise ValueError("sparse_feature_selection")
    if not set(plan["relative_features"]) <= set(features):
        raise ValueError("sparse_feature_selection")
    targets = plan["targets"]
    if (
        not isinstance(targets, list)
        or not targets
        or len({t["name"] for t in targets}) != len(targets)
    ):
        raise ValueError("sparse_targets")
    for t in targets:
        if (
            set(t) != {"name", "kind", "horizon_ns", "feature"}
            or not isinstance(t["name"], str)
            or not t["name"]
            or t["kind"] not in {"endpoint_log_return", "observed_process_count"}
            or type(t["horizon_ns"]) is not int
            or not 0 < t["horizon_ns"] < observations.MAX_TIME
            or (t["kind"] == "endpoint_log_return" and t["feature"] not in features)
            or (t["kind"] == "observed_process_count" and t["feature"] is not None)
        ):
            raise ValueError("sparse_target")
    return plan


def schema(source, plan):
    validate_plan(plan, source)
    features = []
    for col in source.schema["streams"][plan["stream"]]["features"]:
        relative = col["name"] in plan["relative_features"]
        features.append(
            dict(
                col,
                name="value/" + col["name"],
                unit="log_ratio_to_origin" if relative else col["unit"],
            )
        )
    features += [
        dict(name=n, unit=u, modality="observation_process")
        for n, u in (
            ("frame_present", "indicator"),
            ("event_present", "indicator"),
            ("log_age_seconds", "log1p_seconds"),
            ("log_gap_seconds", "log1p_seconds"),
            ("gap_observed_complete", "indicator"),
        )
    ]
    features += [
        dict(name="lifecycle_age/" + n, unit="log1p_seconds", modality="lifecycle")
        for n in plan["lifecycle"]
    ]
    return validate(
        dict(
            schema="brain-experience-v2",
            layout="dense-sequence-f64-v1",
            context=plan["context"],
            features=features,
            targets=[
                dict(
                    name=t["name"],
                    unit="log_ratio" if t["kind"] == "endpoint_log_return" else "events",
                    kind=t["kind"] + "@" + str(t["horizon_ns"]) + "ns",
                )
                for t in plan["targets"]
            ],
            representation=dict(
                name="sparse-event-sequence",
                version="1",
                code_sha=digest(
                    dict(
                        implementation=file_hash(Path(__file__)),
                        source_contract=dict(
                            version=source.schema["version"],
                            availability=source.schema["availability"],
                            stream=source.schema["streams"][plan["stream"]],
                        ),
                        plan=plan,
                    )
                ),
            ),
            availability=source.schema["availability"],
        )
    )


def support(db, entity, stream, start, end, known):
    """Full union coverage of [start,end); non-complete states never imply absence."""
    if start >= end:
        return dict(complete=True, available=0, end_ns=end, evidence=[], reasons=[])
    cursor = assertion_cursor = start
    available = last = 0
    evidence = set()
    states = set()
    gap = False
    previous = db.execute(
        "select start from support where entity=? and stream=? and start<=? "
        "order by start desc limit 1",
        (entity, stream, start),
    ).fetchone()
    first = previous[0] if previous else start
    for a, b, at, raw in db.execute(
        "select start,end,available,payload from support where entity=? and stream=? "
        "and start>=? and start<? and end>? order by start",
        (entity, stream, first, end, start),
    ):
        row = json.loads(raw)
        if at > known:
            continue
        states.add(row["status"])
        if a > assertion_cursor:
            gap = True
        assertion_cursor = max(assertion_cursor, b)
        if row["status"] == "complete" and a <= cursor:
            cursor = max(cursor, b)
            evidence.add(row["_source_shard"])
            available, last = max(available, at), max(last, b)
    complete = cursor >= end
    reasons = []
    if not complete:
        reason_names = {
            "unknown": "unknown_support",
            "failed": "source_failed",
            "stopped": "source_stopped",
            "unsupported": "unsupported_process",
        }
        reasons.extend(sorted(reason_names[s] for s in states if s in reason_names))
        if gap or assertion_cursor < end:
            reasons.append("unasserted_gap")
        if not reasons:
            reasons.append("incomplete_complete_support")
    return dict(
        complete=complete,
        available=available,
        end_ns=last,
        evidence=sorted(evidence),
        reasons=reasons,
    )


def event_count(db, entity, stream, start, end):
    """Rank endpoints avoid rescanning a dense future window for every origin."""
    first = db.execute(
        "select rank from events where entity=? and stream=? and t>? order by t,id limit 1",
        (entity, stream, start),
    ).fetchone()
    last = db.execute(
        "select rank from events where entity=? and stream=? and t<=? "
        "order by t desc,id desc limit 1",
        (entity, stream, end),
    ).fetchone()
    return max(0, last[0] - first[0] + 1) if first and last else 0


def event_lower_bound(db, entity, stream, start, end):
    """Count retained process events and the latest receipt that supports this bound."""
    count, available = db.execute(
        "select count(*),max(available) from events where entity=? and stream=? and t>? and t<=?",
        (entity, stream, start, end),
    ).fetchone()
    return int(count), available


def samples(db, source, plan, counts=None):
    """O(context) feature memory; per-entity queries use the disposable disk index."""
    validate_plan(plan, source)
    counts = Counter() if counts is None else counts
    cutoff = source.manifest["binding"]["cutoff_ns"]
    stream = plan["stream"]
    names = [c["name"] for c in source.schema["streams"][stream]["features"]]
    relative = {names.index(n) for n in plan["relative_features"]}
    point = source.schema["availability"] == "point_in_time"
    for entity, start, end, enrolled_at, _ in db.execute(
        "select * from members order by entity,start"
    ):
        counts["enrolled_entities"] += 1
        clock = "available" if point else "t"
        query = (
            f"select {clock},id,'event',t from events where entity=? and stream=? and t>=? and t<?"
        )
        args = [entity, stream, start, end]
        if plan["lifecycle_origins"]:
            query += (
                f" union all select {clock},id,'lifecycle',t from lifecycle "
                "where entity=? and t>=? and t<?"
            )
            args.extend([entity, start, end])
        query += " order by 1,3,2"
        # Stable sample ids sort event before lifecycle at equal timestamps.
        for decision, anchor_id, kind, event_at in db.execute(query, args):
            counts["candidate_origins"] += 1
            if decision >= end or decision > cutoff:
                counts["origin_outside_enrollment_or_cutoff"] += 1
                continue
            if point and enrolled_at > decision:
                counts["origin_enrollment_not_yet_known"] += 1
                continue
            known = decision if point else cutoff
            if kind == "event":
                raw = db.execute(
                    "select payload from events where entity=? and stream=? and t=? and id=?",
                    (entity, stream, event_at, anchor_id),
                ).fetchone()[0]
            else:
                raw = db.execute(
                    "select payload from lifecycle where entity=? and t=? and id=?",
                    (entity, event_at, anchor_id),
                ).fetchone()[0]
            anchor = json.loads(raw)
            if anchor["available_ns"] > known:
                raise ValueError("sparse_anchor_clock")
            event_slots = plan["context"] - int(kind == "lifecycle")
            history = [
                json.loads(r[0])
                for r in db.execute(
                    "select payload from events "
                    "where entity=? and stream=? and t>=? and t<=? "
                    "and available<=? order by t desc,id desc limit ?",
                    (
                        entity,
                        stream,
                        max(start, decision - plan["max_history_ns"]),
                        decision,
                        known,
                        event_slots + 1,
                    ),
                )
            ]
            history.reverse()
            # All observations at the same instant are jointly known; targets never choose an
            # arbitrary endpoint among multiple events at that instant.
            frames = []
            deps = []

            def dependency(row, at=None, deps=deps):
                for pin in row["evidence"]:
                    deps.append(
                        dict(
                            artifact_sha=pin,
                            event_ns=row["event_ns"] if at is None else at,
                            available_ns=row["available_ns"],
                        )
                    )

            dependency(anchor)
            current = anchor["values"] if kind == "event" else [None] * len(names)

            def life_values(at, entity=entity, start=start, known=known, dependency=dependency):
                result = []
                for transition in plan["lifecycle"]:
                    # Predicate uses event AND knowledge clocks. Never infer terminal state.
                    r = db.execute(
                        "select payload from lifecycle "
                        "where entity=? and t>=? and t<=? "
                        "and available<=? and json_extract(payload,'$.transition')=? "
                        "order by t desc,id desc limit 1",
                        (entity, start, at, known, transition),
                    ).fetchone()
                    if r:
                        fact = json.loads(r[0])
                        dependency(fact)
                        result.append(math.log1p((at - fact["event_ns"]) / NS))
                    else:
                        result.append(None)
                return result

            for index, row in enumerate(history):
                if index == 0 and len(history) > event_slots:
                    continue
                dependency(row)
                values = []
                for j, v in enumerate(row["values"]):
                    if j in relative:
                        base = current[j]
                        if v is None or base is None:
                            values.append(None)
                        elif v <= 0 or base <= 0:
                            raise ValueError("sparse_relative_nonpositive")
                        else:
                            values.append(log_ratio(v, base))
                    else:
                        values.append(v)
                prior = history[index - 1] if index else None
                gap = None
                complete = None
                if prior:
                    dependency(prior)
                    gap = math.log1p((row["event_ns"] - prior["event_ns"]) / NS)
                    coverage = support(
                        db, entity, stream, prior["event_ns"] + 1, row["event_ns"], known
                    )
                    # A future-ending assertion cannot enter an input, even in historical replay.
                    if coverage["complete"] and coverage["end_ns"] <= decision:
                        complete = 1.0
                        if coverage["available"]:
                            dependency(
                                dict(
                                    event_ns=coverage["end_ns"],
                                    available_ns=coverage["available"],
                                    evidence=coverage["evidence"],
                                )
                            )
                frames.append(
                    values
                    + [1.0, 1.0, math.log1p((decision - row["event_ns"]) / NS), gap, complete]
                    + life_values(row["event_ns"])
                )
            if kind == "lifecycle":
                frames.append(
                    [None] * len(names)
                    + [1.0, 0.0, math.log1p((decision - event_at) / NS), None, None]
                    + life_values(decision)
                )
            frames = frames[-plan["context"] :]
            padding = (
                [None] * len(names) + [0.0, 0.0, None, None, None] + [None] * len(plan["lifecycle"])
            )
            frames = [padding[:] for _ in range(plan["context"] - len(frames))] + frames
            labels = []
            statuses = []
            avail = []
            maturities = []
            pins = []
            details = []
            for target in plan["targets"]:
                maturity = decision + target["horizon_ns"]
                status = "missing_future"
                value = None
                at = None
                evidence = []
                lower = None
                lower_available = None
                reason = "unknown_observation_support"
                support_reasons = []
                if maturity > cutoff:
                    status = "pending"
                    reason = "fit_cutoff"
                elif maturity >= end:
                    status = "censored"
                    reason = "enrollment_boundary"
                elif target["kind"] == "endpoint_log_return":
                    j = names.index(target["feature"])
                    base = current[j]
                    endpoints = db.execute(
                        "select payload from events "
                        "where entity=? and stream=? and t=? and available<=?",
                        (entity, stream, maturity, cutoff),
                    ).fetchmany(2)
                    if kind != "event" or base is None or base <= 0:
                        status = "unsupported"
                        reason = "no_origin_value"
                    elif len(endpoints) > 1:
                        status = "unsupported"
                        reason = "ambiguous_endpoint"
                    elif endpoints:
                        future = json.loads(endpoints[0][0])
                        v = future["values"][j]
                        if v is None or v <= 0:
                            status = "unsupported"
                            reason = "no_endpoint_value"
                        else:
                            status = "observed"
                            reason = "exact_observed_endpoint"
                            value = log_ratio(v, base)
                            at = max(anchor["available_ns"], future["available_ns"])
                            evidence = sorted(set(anchor["evidence"] + future["evidence"]))
                    else:
                        reason = "no_exact_endpoint_unknown_return"
                else:
                    n, lower_available = event_lower_bound(db, entity, stream, decision, maturity)
                    coverage = support(db, entity, stream, decision + 1, maturity + 1, cutoff)
                    if coverage["complete"]:
                        # Completeness is specific to the declared observation process, not all
                        # market activity. Sealed support shards and the source binding retain
                        # evidence without copying the full event window into every label.
                        evidence = coverage["evidence"]
                        at = max(maturity, coverage["available"])
                        status = "observed"
                        reason = "complete_observation_process"
                        value = n
                        evidence = sorted(set(evidence))
                    else:
                        status = "censored"
                        lower = n
                        support_reasons = coverage["reasons"]
                        if maturity < end:
                            reason = (
                                support_reasons[0]
                                if len(support_reasons) == 1
                                else "mixed_incomplete_support"
                            )
                if target["kind"] == "observed_process_count" and status != "observed":
                    lower, lower_available = event_lower_bound(
                        db, entity, stream, decision, min(maturity, end - 1, cutoff)
                    )
                labels.append(value)
                statuses.append(status)
                avail.append(at)
                pins.append(evidence)
                maturities.append(maturity)
                details.append(
                    dict(
                        reason=reason,
                        interval_start_ns=decision + 1,
                        interval_end_ns=maturity + 1,
                        lower_bound=lower,
                        lower_bound_available_ns=lower_available,
                        support_reasons=support_reasons,
                        process=source.schema["streams"][stream]["process"],
                        evidence_scope="snapshot_source_manifest",
                    )
                )
            counts["emitted_origins"] += 1
            counts.update("target_status/" + s for s in statuses)
            unique = {digest(d): d for d in deps}
            deps = sorted(
                unique.values(), key=lambda d: (d["event_ns"], d["available_ns"], d["artifact_sha"])
            )
            yield dict(
                id=kind + ":" + stream + ":" + anchor_id,
                pool=entity,
                decision_ns=decision,
                input_start_ns=min(d["event_ns"] for d in deps),
                input_available_ns=max(d["available_ns"] for d in deps),
                input_dependencies=deps,
                values=frames,
                targets=labels,
                target_status=statuses,
                target_maturity_ns=maturities,
                target_available_ns=avail,
                target_captures=pins,
                target_details=details,
                observation_origin=dict(
                    kind=kind,
                    stream=stream,
                    event_ns=event_at,
                    process=source.schema["streams"][stream]["process"],
                ),
            )


def materialize(store, pin, output, *, plan, role="train", chunk=4096):
    source = observations.Source(store, pin, role=role)
    representation = schema(source, plan)
    with tempfile.TemporaryDirectory(prefix="sparse-index-") as scratch:
        db = source.index(Path(scratch) / "index.sqlite")
        try:
            counts = Counter(source_facts=source.manifest["records"])
            return build(
                output,
                samples(db, source, plan, counts),
                sources=[dict(kind="observations", path=str(store), pin=pin)],
                cutoff=source.manifest["binding"]["cutoff_ns"],
                schema=representation,
                role=role,
                chunk=chunk,
                selection=dict(sparse_plan=plan),
                source_counts=counts,
            )
        finally:
            db.close()
