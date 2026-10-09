"""Typed native-coordinate samples and the shared role/availability eligibility gate."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

SLOT_DOMAIN = {
    "version": "typed-coordinate-domain-v1",
    "name": "solana_finalized_slot",
    "network": "solana-mainnet",
    "kind": "finalized_slot",
    "unit": "slot",
}
_DOMAIN_PIN = hashlib.sha256(
    json.dumps(SLOT_DOMAIN, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()

SAMPLE_CONTRACT = {
    "version": "coordinate-sample-contract-v1",
    "identity_rule": "stable_source_identity_plus_native_coordinate_position_v1",
    "position_rule": "domain_defined_native_ordered_position_v1",
    "event_order_rule": "coordinate_then_position_then_identity_v1",
    "dependency_rule": "typed_coordinate_half_open_extents_with_utc_availability_v1",
    "decision_rule": "actual_utc_decision_or_null_only_for_historical_replay_v1",
    "target_rule": "typed_coordinate_window_maturity_completion_and_evidence_v1",
    "eligibility_rule": "coordinate_role_extent_purge_and_utc_knowledge_cutoff_v1",
}
SPLIT_VERSION = "coordinate-split-plan-v1"
ELIGIBILITY_POLICY = "coordinate_sample_eligibility_v1"
PIN = re.compile(r"[0-9a-f]{64}\Z")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def schema(
    *,
    context,
    features,
    targets,
    availability,
    representation_name="native-coordinate-sequence",
    representation_version="1",
    coordinate_domain=SLOT_DOMAIN,
    coordinate_domains=None,
):
    """Build an explicit dense v3 contract with typed native coordinates."""
    domain_pin = digest(coordinate_domain)
    registry = coordinate_domains or [coordinate_domain]
    registry_rows = []
    for descriptor in registry:
        pin = digest(descriptor)
        validate_domain(descriptor, pin)
        registry_rows.append({"descriptor": descriptor, "pin": pin})
    registry_rows.sort(key=lambda item: item["pin"])
    if domain_pin not in {item["pin"] for item in registry_rows}:
        raise ValueError("experience_coordinate_primary_domain_unregistered")
    value = {
        "schema": "brain-experience-v3",
        "layout": "dense-sequence-f64-v1",
        "context": context,
        "features": features,
        "targets": targets,
        "representation": {
            "name": representation_name,
            "version": representation_version,
            "code_sha": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "availability": availability,
        "coordinate_domain": coordinate_domain,
        "coordinate_domain_pin": domain_pin,
        "coordinate_domains": registry_rows,
        "sample_contract": SAMPLE_CONTRACT,
    }
    from .schema import validate

    return validate(value)


def validate_domain(value, pin):
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "name", "network", "kind", "unit"}
        or value.get("version") != "typed-coordinate-domain-v1"
        or any(
            not isinstance(value.get(key), str) or not value[key]
            for key in ("name", "network", "kind", "unit")
        )
        or not isinstance(pin, str)
        or not PIN.fullmatch(pin)
    ):
        raise ValueError("experience_coordinate_domain_unsupported")
    if digest(value) != pin:
        raise ValueError("experience_coordinate_domain_pin")
    return value


def domain_registry(schema):
    rows = schema.get("coordinate_domains")
    if not isinstance(rows, list) or not rows:
        raise ValueError("experience_coordinate_domain_registry")
    registry = {}
    previous = None
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"descriptor", "pin"}:
            raise ValueError("experience_coordinate_domain_registry")
        descriptor, pin = row["descriptor"], row["pin"]
        validate_domain(descriptor, pin)
        if pin in registry or previous is not None and pin <= previous:
            raise ValueError("experience_coordinate_domain_registry_order")
        registry[pin] = descriptor
        previous = pin
    if schema.get("coordinate_domain_pin") not in registry or registry[
        schema["coordinate_domain_pin"]
    ] != schema.get("coordinate_domain"):
        raise ValueError("experience_coordinate_primary_domain_unregistered")
    return registry


def make_split_plan(domain_pin, roles, *, coordinate_domain_pins=None):
    """Create an immutable plan with native-coordinate intervals and UTC knowledge cutoffs."""
    plan = {
        "version": SPLIT_VERSION,
        "coordinate_domain_pin": domain_pin,
        "coordinate_domain_pins": sorted(coordinate_domain_pins or [domain_pin]),
        "eligibility_policy": ELIGIBILITY_POLICY,
        "roles": roles,
    }
    return validate_split_plan(plan)


def validate_split_plan(plan):
    if (
        not isinstance(plan, dict)
        or set(plan)
        != {
            "version",
            "coordinate_domain_pin",
            "coordinate_domain_pins",
            "eligibility_policy",
            "roles",
        }
        or plan["version"] != SPLIT_VERSION
        or plan["eligibility_policy"] != ELIGIBILITY_POLICY
        or not isinstance(plan["coordinate_domain_pins"], list)
        or not plan["coordinate_domain_pins"]
        or plan["coordinate_domain_pins"] != sorted(set(plan["coordinate_domain_pins"]))
        or plan["coordinate_domain_pin"] not in plan["coordinate_domain_pins"]
        or any(
            not isinstance(pin, str) or not PIN.fullmatch(pin)
            for pin in plan["coordinate_domain_pins"]
        )
        or not isinstance(plan["roles"], dict)
        or set(plan["roles"]) != {"train", "validation", "test"}
    ):
        raise ValueError("experience_coordinate_split_plan")
    spans = {pin: [] for pin in plan["coordinate_domain_pins"]}
    cutoffs = []
    for role in ("train", "validation", "test"):
        spec = plan["roles"][role]
        if (
            not isinstance(spec, dict)
            or set(spec) != {"intervals", "dependency_intervals", "knowledge_cutoff_ns"}
            or not isinstance(spec["intervals"], list)
            or not isinstance(spec["dependency_intervals"], dict)
            or set(spec["dependency_intervals"])
            != set(plan["coordinate_domain_pins"]) - {plan["coordinate_domain_pin"]}
            or type(spec["knowledge_cutoff_ns"]) is not int
            or spec["knowledge_cutoff_ns"] < 0
        ):
            raise ValueError("experience_coordinate_split_role")
        cutoffs.append(spec["knowledge_cutoff_ns"])
        interval_map = {
            plan["coordinate_domain_pin"]: spec["intervals"],
            **spec["dependency_intervals"],
        }
        for domain_pin, intervals in interval_map.items():
            if not isinstance(intervals, list):
                raise ValueError("experience_coordinate_split_interval")
            previous = None
            for interval in intervals:
                if (
                    not isinstance(interval, list)
                    or len(interval) != 2
                    or any(type(item) is not int for item in interval)
                    or interval[0] < 0
                    or interval[1] <= interval[0]
                    or previous is not None
                    and interval[0] < previous
                ):
                    raise ValueError("experience_coordinate_split_interval")
                previous = interval[1]
                spans[domain_pin].append((interval[0], interval[1], role))
    if cutoffs != sorted(cutoffs):
        raise ValueError("experience_coordinate_knowledge_cutoff_order")
    for domain_spans in spans.values():
        domain_spans.sort()
        if any(
            left[1] > right[0] for left, right in zip(domain_spans, domain_spans[1:], strict=False)
        ):
            raise ValueError("experience_coordinate_split_overlap")
    return plan


def role_intervals(plan, role):
    validate_split_plan(plan)
    if role not in {"train", "validation", "test"}:
        raise ValueError("experience_coordinate_role")
    return plan["roles"][role]["intervals"]


def role_dependency_intervals(plan, role, domain_pin):
    validate_split_plan(plan)
    if domain_pin not in plan["coordinate_domain_pins"]:
        raise ValueError("experience_coordinate_split_domain")
    if domain_pin == plan["coordinate_domain_pin"]:
        return role_intervals(plan, role)
    return plan["roles"][role]["dependency_intervals"][domain_pin]


def validate_sample(row, schema, cutoff_ns):
    validate_domain(schema["coordinate_domain"], schema["coordinate_domain_pin"])
    registry = domain_registry(schema)
    if schema["sample_contract"] != SAMPLE_CONTRACT or type(cutoff_ns) is not int or cutoff_ns < 0:
        raise ValueError("experience_coordinate_sample_contract")
    required = {
        "id",
        "pool",
        "origin",
        "decision_ns",
        "input_dependencies",
        "input_available_ns",
        "values",
        "targets",
        "target_status",
        "target_windows",
        "target_available_ns",
        "target_details",
        "provenance",
    }
    if not isinstance(row, dict) or not required <= set(row):
        raise ValueError("experience_coordinate_sample_fields")
    origin = row["origin"]
    if (
        not isinstance(row["pool"], str)
        or not row["pool"]
        or not isinstance(row["id"], str)
        or not row["id"]
        or not isinstance(origin, dict)
        or set(origin) != {"domain_pin", "coordinate", "position"}
        or origin["domain_pin"] != schema["coordinate_domain_pin"]
        or type(origin["coordinate"]) is not int
        or origin["coordinate"] < 0
        or not isinstance(origin["position"], list)
        or any(type(i) is not int or i < 0 for i in origin["position"])
    ):
        raise ValueError("experience_coordinate_origin")
    decision = row["decision_ns"]
    if decision is not None and (type(decision) is not int or decision < 0):
        raise ValueError("experience_coordinate_decision")
    if schema["availability"] == "point_in_time" and decision is None:
        raise ValueError("experience_coordinate_pit_decision_required")
    if (
        type(row["input_available_ns"]) is not int
        or not 0 <= row["input_available_ns"] <= cutoff_ns
    ):
        raise ValueError("experience_coordinate_input_availability")
    if decision is not None and decision > cutoff_ns:
        raise ValueError("experience_coordinate_decision_cutoff")
    if schema["availability"] == "point_in_time" and row["input_available_ns"] > decision:
        raise ValueError("experience_coordinate_future_input")

    dependencies = row["input_dependencies"]
    if not isinstance(dependencies, list) or not dependencies:
        raise ValueError("experience_coordinate_dependencies")
    domain_pin = schema["coordinate_domain_pin"]
    dependency_times = []
    for dep in dependencies:
        if (
            not isinstance(dep, dict)
            or set(dep)
            != {
                "artifact_sha",
                "domain_pin",
                "start_coordinate",
                "end_coordinate_exclusive",
                "available_ns",
            }
            or not isinstance(dep["artifact_sha"], str)
            or not PIN.fullmatch(dep["artifact_sha"])
            or dep["domain_pin"] not in registry
            or type(dep["start_coordinate"]) is not int
            or type(dep["end_coordinate_exclusive"]) is not int
            or dep["start_coordinate"] < 0
            or dep["end_coordinate_exclusive"] <= dep["start_coordinate"]
            or dep["domain_pin"] == domain_pin
            and dep["end_coordinate_exclusive"] > origin["coordinate"] + 1
            or type(dep["available_ns"]) is not int
            or not 0 <= dep["available_ns"] <= row["input_available_ns"]
            or dep["available_ns"] > cutoff_ns
            or schema["availability"] == "point_in_time"
            and dep["available_ns"] > decision
        ):
            raise ValueError("experience_coordinate_dependency")
        dependency_times.append(dep["available_ns"])
    if max(dependency_times) != row["input_available_ns"]:
        raise ValueError("experience_coordinate_dependency_availability")
    if (
        len(row["values"]) != schema["context"]
        or any(len(frame) != len(schema["features"]) for frame in row["values"])
        or any(
            value is not None and (type(value) not in (int, float) or not math.isfinite(value))
            for frame in row["values"]
            for value in frame
        )
    ):
        raise ValueError("experience_coordinate_values")
    n = len(schema["targets"])
    if any(
        not isinstance(row[key], list) or len(row[key]) != n
        for key in (
            "targets",
            "target_status",
            "target_windows",
            "target_available_ns",
            "target_details",
        )
    ):
        raise ValueError("experience_coordinate_targets")
    for i, (value, status, window, available, detail) in enumerate(
        zip(
            row["targets"],
            row["target_status"],
            row["target_windows"],
            row["target_available_ns"],
            row["target_details"],
            strict=True,
        )
    ):
        if (
            not isinstance(window, dict)
            or set(window)
            != {
                "domain_pin",
                "start_coordinate",
                "end_coordinate_exclusive",
                "maturity_coordinate_exclusive",
                "completion_status",
                "completion_evidence",
                "support_reasons",
            }
            or window["domain_pin"] != domain_pin
            or any(
                type(window[k]) is not int
                for k in (
                    "start_coordinate",
                    "end_coordinate_exclusive",
                    "maturity_coordinate_exclusive",
                )
            )
            or window["start_coordinate"] <= origin["coordinate"]
            or window["end_coordinate_exclusive"] <= window["start_coordinate"]
            or window["maturity_coordinate_exclusive"] < window["end_coordinate_exclusive"]
            or window["completion_status"] not in {"complete", "incomplete", "unknown"}
            or not isinstance(window["completion_evidence"], list)
            or any(
                not isinstance(p, str) or not PIN.fullmatch(p)
                for p in window["completion_evidence"]
            )
            or not isinstance(window["support_reasons"], list)
            or any(not isinstance(reason, str) for reason in window["support_reasons"])
            or not isinstance(detail, dict)
            or available is not None
            and (type(available) is not int or not 0 <= available <= cutoff_ns)
        ):
            raise ValueError(f"experience_coordinate_target_window_{i}")
        if (
            schema["availability"] == "point_in_time"
            and available is not None
            and available > decision
        ):
            raise ValueError("experience_coordinate_future_target")
        if status == "exact":
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or target_is_count(schema["targets"][i])
                and (type(value) is not int or value < 0)
                or available is None
                or window["completion_status"] != "complete"
                or not window["completion_evidence"]
            ):
                raise ValueError("experience_coordinate_exact_target")
        elif status == "lower_bound":
            lower = detail.get("lower_bound")
            lower_at = detail.get("lower_bound_available_ns")
            if (
                not target_is_count(schema["targets"][i])
                or type(lower) is not int
                or lower <= 0
                or value is not None
                or available is not None
                or type(lower_at) is not int
                or lower_at < 0
                or lower_at > cutoff_ns
                or window["completion_status"] != "incomplete"
                or schema["availability"] == "point_in_time"
                and lower_at > decision
            ):
                raise ValueError("experience_coordinate_lower_bound")
        elif status in {"masked", "pending"}:
            if (
                value is not None
                or available is not None
                or status == "masked"
                and window["completion_status"] == "complete"
                or status == "pending"
                and window["completion_status"] != "unknown"
            ):
                raise ValueError("experience_coordinate_masked_target")
        else:
            raise ValueError("experience_coordinate_target_status")
    provenance = row["provenance"]
    if (
        not isinstance(provenance, list)
        or not provenance
        or any(not isinstance(p, str) or not PIN.fullmatch(p) for p in provenance)
    ):
        raise ValueError("experience_coordinate_provenance")
    return row


def target_is_count(target):
    return target["kind"].startswith("observed_process_count@")


def eligibility(sample, schema, split_plan, role):
    """Return exact/lower-bound masks after native extent purge and independent UTC cutoff."""
    registry = domain_registry(schema)
    validate_split_plan(split_plan)
    if split_plan["coordinate_domain_pin"] != schema["coordinate_domain_pin"] or split_plan[
        "coordinate_domain_pins"
    ] != sorted(registry):
        raise ValueError("experience_coordinate_split_domain")
    if role not in {"train", "validation", "test"}:
        raise ValueError("experience_coordinate_role")
    origin = sample["origin"]["coordinate"]
    assigned = [
        name
        for name in ("train", "validation", "test")
        if any(start <= origin < end for start, end in role_intervals(split_plan, name))
    ]
    if len(assigned) > 1:
        raise ValueError("experience_coordinate_origin_overlap")
    assigned_role = assigned[0] if assigned else "outside"
    spec = split_plan["roles"][role]
    matching = [(start, end) for start, end in spec["intervals"] if start <= origin < end]
    n = len(schema["targets"])
    exact, lower = [False] * n, [False] * n
    result = dict(
        assigned_role=assigned_role, eligible=False, exact_mask=exact, lower_bound_mask=lower
    )
    if assigned_role != role or len(matching) != 1:
        result["reason"] = "origin_outside_role"
        return result
    start, end = matching[0]
    deps = sample["input_dependencies"]
    for dep in deps:
        dep_intervals = role_dependency_intervals(split_plan, role, dep["domain_pin"])
        if not any(
            dep["start_coordinate"] >= dep_start and dep["end_coordinate_exclusive"] <= dep_end
            for dep_start, dep_end in dep_intervals
        ):
            result["reason"] = "input_extent_crosses_role_boundary"
            return result
    cutoff = spec["knowledge_cutoff_ns"]
    decision = sample["decision_ns"]
    if sample["input_available_ns"] > cutoff or (decision is not None and decision > cutoff):
        result["reason"] = "knowledge_cutoff"
        return result
    if schema["availability"] == "point_in_time":
        if decision is None or sample["input_available_ns"] > decision:
            result["reason"] = "future_input_or_missing_decision"
            return result
    for i, (status, window, available, detail) in enumerate(
        zip(
            sample["target_status"],
            sample["target_windows"],
            sample["target_available_ns"],
            sample["target_details"],
            strict=True,
        )
    ):
        if window["domain_pin"] != schema["coordinate_domain_pin"]:
            raise ValueError("experience_coordinate_target_domain")
        window_inside = (
            start <= window["start_coordinate"]
            and window["end_coordinate_exclusive"] <= end
            and window["maturity_coordinate_exclusive"] <= end
        )
        if not window_inside:
            continue
        exact[i] = (
            status == "exact"
            and window["completion_status"] == "complete"
            and available is not None
            and available <= cutoff
            and (schema["availability"] != "point_in_time" or available <= decision)
            and sample.get("_admission", "eligible") == "eligible"
        )
        lower_at = detail.get("lower_bound_available_ns")
        lower[i] = (
            status == "lower_bound"
            and type(detail.get("lower_bound")) in (int, float)
            and detail["lower_bound"] > 0
            and type(lower_at) is int
            and lower_at <= cutoff
            and (schema["availability"] != "point_in_time" or lower_at <= decision)
            and sample.get("_admission", "eligible") == "eligible"
        )
    result.update(eligible=True, exact_mask=exact, lower_bound_mask=lower, reason="eligible")
    return result


def membership(sample, schema, split_plan):
    outcomes = [
        eligibility(sample, schema, split_plan, role) for role in ("train", "validation", "test")
    ]
    # Each result reports the sample's assigned role. Select the matching role query,
    # not the first query (which also carries the assigned-role label).
    matching = next(
        (
            result
            for role, result in zip(("train", "validation", "test"), outcomes, strict=True)
            if result["assigned_role"] == role
        ),
        None,
    )
    if matching is None:
        return "outside", [False] * len(schema["targets"])
    result = matching
    if not result["eligible"]:
        return (
            "purged"
            if result["reason"] == "input_extent_crosses_role_boundary"
            else result["assigned_role"],
            [False] * len(schema["targets"]),
        )
    return result["assigned_role"], result["exact_mask"]


def entity(sample):
    """Stable entity accessor retained for existing population/model consumers."""
    return sample["pool"]


def coordinate(sample):
    return sample["origin"]["coordinate"]
