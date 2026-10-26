"""Versioned representation contracts, independent of source, model and trainer implementation."""

import math
import re

from . import coordinate_samples
from .records import contract

DENSE = "dense-sequence-f64-v1"


def validate(schema):
    if schema.get("schema") == "brain-experience-v1":
        if schema != contract(schema["context"]):
            raise ValueError("experience_schema")
        return schema
    required_v2 = {
        "schema",
        "layout",
        "context",
        "features",
        "targets",
        "representation",
        "availability",
    }
    required = (
        required_v2
        if isinstance(schema, dict) and schema.get("schema") == "brain-experience-v2"
        else required_v2
        | {"coordinate_domain", "coordinate_domain_pin", "coordinate_domains", "sample_contract"}
    )
    if (
        set(schema) != required
        or schema["schema"] not in {"brain-experience-v2", "brain-experience-v3"}
        or schema["layout"] != DENSE
        or type(schema["context"]) is not int
        or not 1 <= schema["context"] <= 65536
        or schema["availability"] not in {"historical_replay", "point_in_time"}
        or set(schema["representation"]) != {"name", "version", "code_sha"}
        or not all(isinstance(v, str) and v for v in schema["representation"].values())
        or not re.fullmatch(r"[0-9a-f]{64}", schema["representation"]["code_sha"])
    ):
        raise ValueError("experience_schema")
    if schema["schema"] == "brain-experience-v3":
        coordinate_samples.validate_domain(
            schema["coordinate_domain"], schema["coordinate_domain_pin"]
        )
        coordinate_samples.domain_registry(schema)
        if schema["sample_contract"] != coordinate_samples.SAMPLE_CONTRACT:
            raise ValueError("experience_coordinate_schema_contract")
    for field in ("features", "targets"):
        columns = schema[field]
        required = {"name", "unit", "modality"} if field == "features" else {"name", "unit", "kind"}
        if (
            not isinstance(columns, list)
            or not columns
            or len({c.get("name") for c in columns}) != len(columns)
            or any(
                set(c) != required or not all(isinstance(v, str) and v for v in c.values())
                for c in columns
            )
        ):
            raise ValueError("experience_schema_columns")
    return schema


def feature_names(schema):
    return (
        schema["feature_names"]
        if schema["schema"] == "brain-experience-v1"
        else [c["name"] for c in schema["features"]]
    )


def target_names(schema):
    return (
        schema["horizons"]
        if schema["schema"] == "brain-experience-v1"
        else [c["name"] for c in schema["targets"]]
    )


def membership(row, plan, schema):
    from .records import membership as legacy

    if schema["schema"] == "brain-experience-v1":
        return legacy(row, plan)
    if schema["schema"] == "brain-experience-v3":
        return coordinate_samples.membership(row, schema, plan)
    for role, start, end in (
        ("train", plan["train_start_s"], plan["train_end_s"]),
        ("validation", plan["train_end_s"], plan["validation_end_s"]),
        ("test", plan["validation_end_s"], plan["test_end_s"]),
    ):
        start, end = start * 10**9, end * 10**9
        if start <= row["decision_ns"] < end:
            if row["input_start_ns"] < start:
                return "purged", [False] * len(schema["targets"])
            return role, [
                row.get("_admission", "eligible") == "eligible"
                and s == "observed"
                and t < end
                and (schema["availability"] == "historical_replay" or a < end)
                for s, t, a in zip(
                    row["target_status"],
                    row["target_maturity_ns"],
                    row["target_available_ns"],
                    strict=True,
                )
            ]
    return "outside", [False] * len(schema["targets"])


def checked_samples(rows, schema, cutoff):
    """Structural/time/provenance gate. Domain qualification belongs to the pinned producer."""
    validate(schema)
    if schema["schema"] == "brain-experience-v3":
        previous = None
        for row in rows:
            from memecoin_brain.source import EXCLUDED

            if (
                row.get("pool") in EXCLUDED
                or str(row.get("pool", "")).rsplit("/", 1)[-1] in EXCLUDED
            ):
                raise ValueError("experience_protected_pool")
            coordinate_samples.validate_sample(row, schema, cutoff)
            key = (
                row["pool"],
                row["origin"]["coordinate"],
                tuple(row["origin"]["position"]),
                row["id"],
            )
            if previous is not None and key <= previous:
                raise ValueError("experience_coordinate_order_or_duplicate")
            previous = key
            yield row
        return
    if schema["schema"] != "brain-experience-v2" or type(cutoff) is not int or cutoff < 0:
        raise ValueError("experience_sample_schema")
    previous = None
    for row in rows:
        from memecoin_brain.source import EXCLUDED

        if row["pool"] in EXCLUDED or str(row["pool"]).rsplit("/", 1)[-1] in EXCLUDED:
            raise ValueError("experience_protected_pool")
        decision, start, available = (
            row[k] for k in ("decision_ns", "input_start_ns", "input_available_ns")
        )
        key = (row["pool"], decision, row["id"])
        if previous is not None and key <= previous:
            raise ValueError("experience_order_or_duplicate")
        previous = key
        n = len(schema["targets"])
        if (
            not isinstance(row["pool"], str)
            or not row["pool"]
            or not isinstance(row["id"], str)
            or not row["id"]
            or any(type(t) is not int for t in (decision, start, available))
            or not 0 <= start <= decision <= cutoff
            or not 0 <= available <= cutoff
            or (schema["availability"] == "point_in_time" and available > decision)
            or len(row["values"]) != schema["context"]
            or any(len(frame) != len(schema["features"]) for frame in row["values"])
            or any(
                v is not None and (type(v) not in (int, float) or not math.isfinite(v))
                for frame in row["values"]
                for v in frame
            )
            or any(
                len(row[k]) != n
                for k in (
                    "targets",
                    "target_status",
                    "target_available_ns",
                    "target_maturity_ns",
                    "target_captures",
                )
            )
            or not row["input_dependencies"]
        ):
            raise ValueError("experience_sample_contract")
        dependencies = row["input_dependencies"]
        for dep in dependencies:
            if (
                set(dep) != {"artifact_sha", "event_ns", "available_ns"}
                or not isinstance(dep["artifact_sha"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", dep["artifact_sha"])
                or any(type(dep[k]) is not int for k in ("event_ns", "available_ns"))
                or not start <= dep["event_ns"] <= decision
                or not dep["event_ns"] <= dep["available_ns"] <= available
            ):
                raise ValueError("experience_dependency_contract")
        if (
            min(d["event_ns"] for d in dependencies) != start
            or max(d["available_ns"] for d in dependencies) != available
        ):
            raise ValueError("experience_dependency_bounds")
        details = row.get("target_details")
        if details is None:
            details = [{} for _ in range(n)]
        if not isinstance(details, list) or len(details) != n:
            raise ValueError("experience_target_details")
        for target, value, status, at, maturity, pins, detail in zip(
            schema["targets"],
            row["targets"],
            row["target_status"],
            row["target_available_ns"],
            row["target_maturity_ns"],
            row["target_captures"],
            details,
            strict=True,
        ):
            if target["kind"].startswith("observed_process_count@") and status == "censored":
                if not isinstance(detail, dict):
                    raise ValueError("experience_censor_detail")
                lower = detail.get("lower_bound")
                available = detail.get("lower_bound_available_ns")
                if lower is not None and (type(lower) is not int or lower < 0):
                    raise ValueError("experience_censor_lower_bound")
                if lower:
                    if type(maturity) is not int or not decision <= maturity <= cutoff:
                        raise ValueError("experience_censor_target_maturity")
                    if type(available) is not int or not decision <= available <= cutoff:
                        raise ValueError("experience_censor_bound_availability")
            if type(maturity) is not int or maturity < decision:
                raise ValueError("experience_target_maturity")
            if status == "observed":
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or type(at) is not int
                    or not maturity <= at <= cutoff
                    or not pins
                    or any(
                        not isinstance(p, str) or not re.fullmatch(r"[0-9a-f]{64}", p) for p in pins
                    )
                ):
                    raise ValueError("experience_target_contract")
                if target["kind"].startswith("observed_process_count@") and (
                    type(value) is not int or value < 0
                ):
                    raise ValueError("experience_count_target")
            elif (
                status not in {"pending", "missing_future", "unsupported", "censored"}
                or value is not None
                or at is not None
                or pins
            ):
                raise ValueError("experience_target_contract")
        yield row
