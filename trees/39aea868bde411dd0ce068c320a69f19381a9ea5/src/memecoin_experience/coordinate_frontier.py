"""Versioned per-domain native-coordinate history bounds for v3 models."""

from .coordinate_samples import domain_registry
from .records import split_plan

FRONTIER_VERSION = "brain-native-coordinate-frontier-v1"


def from_plan(plan):
    """Conservatively bound every declared role extent in each native domain."""
    plan = split_plan(plan)
    pins = plan["coordinate_domain_pins"]
    intervals = {pin: [] for pin in pins}
    primary = plan["coordinate_domain_pin"]
    for role in ("train", "validation", "test"):
        spec = plan["roles"][role]
        intervals[primary].extend(spec["intervals"])
        for pin, spans in spec["dependency_intervals"].items():
            intervals[pin].extend(spans)
    return {
        "version": FRONTIER_VERSION,
        "coordinate_domain_pins": pins,
        "exclusive_ends": {
            pin: max((end for _, end in intervals[pin]), default=None) for pin in pins
        },
    }


def validate(frontier, domain_pins):
    pins = sorted(domain_pins)
    if (
        not isinstance(frontier, dict)
        or set(frontier) != {"version", "coordinate_domain_pins", "exclusive_ends"}
        or frontier["version"] != FRONTIER_VERSION
        or frontier["coordinate_domain_pins"] != pins
        or not isinstance(frontier["exclusive_ends"], dict)
        or set(frontier["exclusive_ends"]) != set(pins)
        or any(
            value is not None and (type(value) is not int or value < 0)
            for value in frontier["exclusive_ends"].values()
        )
    ):
        raise ValueError("experience_coordinate_frontier")
    return frontier


def merge(current, inherited):
    """Merge a plan frontier with a fully proven parent frontier."""
    pins = current["coordinate_domain_pins"]
    validate(current, pins)
    validate(inherited, pins)
    ends = {}
    for pin in pins:
        known = [
            value
            for value in (current["exclusive_ends"][pin], inherited["exclusive_ends"][pin])
            if value is not None
        ]
        ends[pin] = max(known) if known else None
    return {
        "version": FRONTIER_VERSION,
        "coordinate_domain_pins": pins,
        "exclusive_ends": ends,
    }


def from_report(report, schema):
    """Read a proven v3 frontier or derive one for a standalone legacy artifact."""
    pins = sorted(domain_registry(schema))
    primary = schema["coordinate_domain_pin"]
    binding = report.get("binding")
    if not isinstance(binding, dict):
        raise ValueError("experience_coordinate_frontier_unproven")
    try:
        plan_frontier = from_plan(binding.get("plan"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("experience_coordinate_frontier_unproven") from exc
    validate(plan_frontier, pins)
    has_frontier = "seen_coordinate_frontier" in report
    frontier = report.get("seen_coordinate_frontier")
    if not has_frontier:
        if (
            "parent" not in binding
            or binding["parent"] is not None
            or binding.get("initialization") != "seeded_fresh"
            or "ancestors" not in report
            or report["ancestors"] != []
        ):
            raise ValueError("experience_coordinate_frontier_unproven_legacy_lineage")
        frontier = plan_frontier
    validate(frontier, pins)
    for pin in pins:
        plan_end = plan_frontier["exclusive_ends"][pin]
        reported_end = frontier["exclusive_ends"][pin]
        if plan_end is not None and (reported_end is None or reported_end < plan_end):
            raise ValueError("experience_coordinate_frontier_understated")
    primary_end = frontier["exclusive_ends"][primary]
    if has_frontier and (
        type(report.get("seen_end_coordinate")) is not type(primary_end)
        or report.get("seen_end_coordinate") != primary_end
    ):
        raise ValueError("experience_coordinate_frontier_scalar")
    return frontier


def require_test_after(frontier, plan):
    """Require every test primary/dependency extent to start at or beyond history."""
    plan = split_plan(plan)
    validate(frontier, plan["coordinate_domain_pins"])
    primary = plan["coordinate_domain_pin"]
    for pin in plan["coordinate_domain_pins"]:
        spans = (
            plan["roles"]["test"]["intervals"]
            if pin == primary
            else plan["roles"]["test"]["dependency_intervals"][pin]
        )
        end = frontier["exclusive_ends"][pin]
        if spans and (end is None or any(start < end for start, _ in spans)):
            raise ValueError("experience_coordinate_evaluation_seen_interval")
