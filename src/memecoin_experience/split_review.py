"""Compose the explicit population, allocation, and coverage split diagnostics."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

_REQUIRED = {"snapshot", "decisions", "assignments", "split_plan", "strata", "row_strata"}
_OPTIONAL = {"target_counts", "max_states", "expected_manifest_sha256"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _validate(payload: Any) -> dict[str, Any]:
    if type(payload) is not dict or not _REQUIRED <= set(payload) or set(payload) - _REQUIRED - _OPTIONAL:
        raise ValueError("experience_split_review_invalid_request_keys")
    for key in ("snapshot", "split_plan"):
        if type(payload[key]) is not dict:
            raise ValueError(f"experience_split_review_invalid_{key}")
    for key in ("decisions", "assignments", "strata", "row_strata"):
        if type(payload[key]) is not list:
            raise ValueError(f"experience_split_review_invalid_{key}")
    if "max_states" in payload and (type(payload["max_states"]) is not int or not 1 <= payload["max_states"] <= 50000):
        raise ValueError("experience_split_review_invalid_max_states")
    if "expected_manifest_sha256" in payload:
        pin = payload["expected_manifest_sha256"]
        if type(pin) is not str or _SHA256.fullmatch(pin) is None:
            raise ValueError("experience_split_review_invalid_expected_manifest_sha256")
    if "target_counts" in payload and type(payload["target_counts"]) is not dict:
        raise ValueError("experience_split_review_invalid_target_counts")
    try:
        _canonical(payload)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("experience_split_review_invalid_json_value") from exc
    request = deepcopy(payload)
    # These declarations are unordered. Sort without deduplicating so repeated
    # labels remain evidence for the coverage producer.
    for key in ("decisions", "assignments", "row_strata"):
        request[key].sort(key=_canonical)
    for key, field in (("snapshot", "row_ids"), ("split_plan", "declared_splits")):
        if type(request[key].get(field)) is list:
            request[key][field].sort(key=_canonical)
    for stratum in request["strata"]:
        if type(stratum) is dict and type(stratum.get("supported_splits")) is list:
            stratum["supported_splits"].sort(key=_canonical)
    request["strata"].sort(key=_canonical)
    return request


def analyze(payload: dict) -> dict:
    """Review caller-declared split evidence without changing assignments or qualifying data."""
    request = _validate(payload)
    from .split_review_population import analyze as analyze_population
    from .split_review_allocation import analyze as analyze_allocation
    from .split_review_coverage import analyze as analyze_coverage

    population = analyze_population(
        request["snapshot"],
        request["decisions"],
        request["assignments"],
        request["split_plan"],
        expected_manifest_sha256=request.get("expected_manifest_sha256"),
    )
    allocation = analyze_allocation(
        population,
        request["strata"],
        request["row_strata"],
        target_counts=request.get("target_counts"),
        max_states=request.get("max_states", 50000),
    )
    coverage = analyze_coverage(
        population,
        request["strata"],
        request["row_strata"],
        target_counts=request.get("target_counts"),
    )
    body = {
        "schema": "brain-split-review.v1",
        "inclusion": population["inclusion"],
        "lineage": population["lineage"],
        "allocation": allocation["allocation"],
        "feasibility": allocation["feasibility"],
        "coverage": coverage["coverage"],
        "target_deviation": coverage["target_deviation"],
        "denominators": {
            **population["denominators"],
            **coverage["denominators"],
        },
        "provenance": {
            "request_sha256": hashlib.sha256(_canonical(request).encode("utf-8")).hexdigest(),
            "snapshot_sha256": request["snapshot"].get("snapshot_sha256"),
            "plan_sha256": request["split_plan"].get("plan_sha256"),
            "expected_manifest_sha256": request.get("expected_manifest_sha256"),
        },
        "limitations": sorted(set(population.get("limitations", [])) | set(allocation.get("limitations", [])) | set(coverage.get("limitations", [])) | {
            "Declaration indices refer to normalized declaration order; duplicate declarations are retained.",
            "This review does not establish admission, training, model or selection qualification.",
        }),
    }
    body["content_sha256"] = hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()
    return body
