"""Strict, metadata-only Cortex research protocol contracts.

Protocols declare work; they do not authorize reading inputs, training, scoring,
or writing research artifacts. The controller binds an activated protocol through
the ordinary reviewed contract transition path.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

CONTRACT_NAME = "cortex-research-protocol"
SCHEMA = "brain-cortex-research-protocol-v1"
_HASH = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^CORTEX-[0-9]{3,6}$")
_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_FIELDS = {
    "schema",
    "version",
    "protocol_id",
    "objective_id",
    "hypothesis",
    "input_pins",
    "recipe_sha256",
    "code_sha256",
    "baseline",
    "metrics",
    "population",
    "evaluation_window",
    "resource_budget",
    "stop_criteria",
    "output_owner",
}


class ResearchProtocolError(ValueError):
    """Malformed, incomplete, or contradictory research preregistration."""


def _fail(code: str) -> None:
    raise ResearchProtocolError(code)


def _text(value: Any, code: str, maximum: int, *, minimum: int = 1) -> None:
    if type(value) is not str or not minimum <= len(value.strip()) <= maximum:
        _fail(code)


def _hash(value: Any, code: str) -> None:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        _fail(code)


def _positive_int(value: Any, code: str, maximum: int) -> None:
    if type(value) is not int or value <= 0 or value > maximum:
        _fail(code)


def canonical_protocol(value: Any, *, version: int | None = None) -> dict:
    """Return canonical detached protocol JSON and reject ambiguous declarations."""
    try:
        detached = json.loads(
            json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        _fail("invalid_cortex_protocol_json")
    if type(detached) is not dict or set(detached) != _FIELDS:
        _fail("invalid_cortex_protocol_fields")
    if (
        detached["schema"] != SCHEMA
        or type(detached["version"]) is not int
        or detached["version"] < 2
        or (version is not None and detached["version"] != version)
    ):
        _fail("invalid_cortex_protocol_version")
    if type(detached["protocol_id"]) is not str or _ID.fullmatch(detached["protocol_id"]) is None:
        _fail("invalid_cortex_protocol_id")
    _text(detached["objective_id"], "invalid_cortex_protocol_objective", 64)
    _text(detached["hypothesis"], "invalid_cortex_protocol_hypothesis", 2000, minimum=20)
    _hash(detached["recipe_sha256"], "invalid_cortex_protocol_recipe_hash")
    _hash(detached["code_sha256"], "invalid_cortex_protocol_code_hash")
    if detached["output_owner"] != "Cortex":
        _fail("invalid_cortex_protocol_output_owner")

    pins = detached["input_pins"]
    if type(pins) is not list or not 2 <= len(pins) <= 3:
        _fail("invalid_cortex_protocol_input_pins")
    roles = []
    pin_hashes = []
    for pin in pins:
        if type(pin) is not dict or set(pin) != {
            "role",
            "artifact_sha256",
            "available_at_ns",
        }:
            _fail("invalid_cortex_protocol_input_pin")
        role = pin["role"]
        if type(role) is not str or role not in {
            "train",
            "validation",
            "research_evaluation",
        }:
            _fail("invalid_cortex_protocol_input_role")
        _hash(pin["artifact_sha256"], "invalid_cortex_protocol_input_hash")
        _positive_int(pin["available_at_ns"], "invalid_cortex_protocol_availability", 2**63 - 1)
        roles.append(role)
        pin_hashes.append(pin["artifact_sha256"])
    if (
        len(set(roles)) != len(roles)
        or len(set(pin_hashes)) != len(pin_hashes)
        or not {"train", "research_evaluation"} <= set(roles)
    ):
        _fail("invalid_cortex_protocol_input_roles")

    baseline = detached["baseline"]
    if type(baseline) is not dict or set(baseline) != {"name", "artifact_sha256"}:
        _fail("invalid_cortex_protocol_baseline")
    if type(baseline["name"]) is not str or _SLUG.fullmatch(baseline["name"]) is None:
        _fail("invalid_cortex_protocol_baseline")
    _hash(baseline["artifact_sha256"], "invalid_cortex_protocol_baseline_hash")

    metrics = detached["metrics"]
    if type(metrics) is not list or not 1 <= len(metrics) <= 16:
        _fail("invalid_cortex_protocol_metrics")
    metric_ids = []
    for metric in metrics:
        if type(metric) is not dict or set(metric) != {"id", "direction"}:
            _fail("invalid_cortex_protocol_metric")
        if type(metric["id"]) is not str or _SLUG.fullmatch(metric["id"]) is None:
            _fail("invalid_cortex_protocol_metric")
        if type(metric["direction"]) is not str or metric["direction"] not in {
            "higher_is_better",
            "lower_is_better",
        }:
            _fail("invalid_cortex_protocol_metric_direction")
        metric_ids.append(metric["id"])
    if len(set(metric_ids)) != len(metric_ids):
        _fail("invalid_cortex_protocol_metrics")

    population = detached["population"]
    if type(population) is not dict or set(population) != {"definition", "definition_sha256"}:
        _fail("invalid_cortex_protocol_population")
    _text(population["definition"], "invalid_cortex_protocol_population", 1000, minimum=10)
    _hash(population["definition_sha256"], "invalid_cortex_protocol_population_hash")
    definition_hash = hashlib.sha256(population["definition"].encode("utf-8")).hexdigest()
    if definition_hash != population["definition_sha256"]:
        _fail("cortex_protocol_population_hash_mismatch")

    window = detached["evaluation_window"]
    if type(window) is not dict or set(window) != {"start_ns", "end_ns"}:
        _fail("invalid_cortex_protocol_window")
    _positive_int(window["start_ns"], "invalid_cortex_protocol_window", 2**63 - 1)
    _positive_int(window["end_ns"], "invalid_cortex_protocol_window", 2**63 - 1)
    if window["end_ns"] <= window["start_ns"] or any(
        pin["available_at_ns"] > window["start_ns"] for pin in pins
    ):
        _fail("invalid_cortex_protocol_window_availability")

    budget = detached["resource_budget"]
    if type(budget) is not dict or set(budget) != {"max_compute_seconds", "max_output_bytes"}:
        _fail("invalid_cortex_protocol_budget")
    _positive_int(budget["max_compute_seconds"], "invalid_cortex_protocol_budget", 86_400)
    _positive_int(budget["max_output_bytes"], "invalid_cortex_protocol_budget", 268_435_456)

    stops = detached["stop_criteria"]
    if type(stops) is not list or not 1 <= len(stops) <= 12:
        _fail("invalid_cortex_protocol_stop_criteria")
    for item in stops:
        _text(item, "invalid_cortex_protocol_stop_criteria", 500)
    if len({item.strip() for item in stops}) != len(stops):
        _fail("invalid_cortex_protocol_stop_criteria")
    return detached


def protocol_sha256(value: Any, *, version: int | None = None) -> str:
    canonical = json.dumps(
        canonical_protocol(value, version=version),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
