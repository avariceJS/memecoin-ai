"""Compare two caller-pinned example-weight states without loading source data."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


_INVALID = "weight_state_delta_invalid_input"
_ROOT_KEYS = {"before", "after"}
_STATE_KEYS = {"state_id", "state_sha256", "examples"}
_ROW_KEYS = {"example_id", "entity_id", "cohort_id", "weight"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_MAX_JSON_BYTES = 8 * 1024 * 1024
_MAX_EXAMPLES = 10_000
_MIN_WEIGHT = 1e-100
_MAX_WEIGHT = 1e100
_MAX_ID_LENGTH = 128


def _invalid() -> ValueError:
    return ValueError(_INVALID)


def _identifier(value: Any) -> str:
    if (type(value) is not str or not 1 <= len(value) <= _MAX_ID_LENGTH
            or any(not "!" <= character <= "~" for character in value)):
        raise _invalid()
    return value


def _weight(value: Any) -> int | float:
    if type(value) is int:
        if value < 0 or value > 10**100 or (value != 0 and value < 1):
            raise _invalid()
    elif type(value) is float:
        if not math.isfinite(value) or value < 0 or (value != 0.0 and not _MIN_WEIGHT <= value <= _MAX_WEIGHT):
            raise _invalid()
    else:
        raise _invalid()
    return value


def _state(value: Any) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    if type(value) is not dict or set(value) != _STATE_KEYS:
        raise _invalid()
    state_id = _identifier(value["state_id"])
    pin = value["state_sha256"]
    if type(pin) is not str or _SHA256.fullmatch(pin) is None:
        raise _invalid()
    rows = value["examples"]
    if type(rows) is not list or len(rows) > _MAX_EXAMPLES:
        raise _invalid()
    checked: dict[str, dict[str, Any]] = {}
    for row in rows:
        if type(row) is not dict or set(row) != _ROW_KEYS:
            raise _invalid()
        example_id = _identifier(row["example_id"])
        if example_id in checked:
            raise _invalid()
        checked[example_id] = {
            "example_id": example_id,
            "entity_id": _identifier(row["entity_id"]),
            "cohort_id": _identifier(row["cohort_id"]),
            "weight": _weight(row["weight"]),
        }
    return {"state_id": state_id, "state_sha256": pin}, checked


def _sum(values: list[float]) -> float:
    return math.fsum(sorted(values))


def _metrics(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    weights = [float(row["weight"]) for row in rows.values()]
    total = _sum(weights)
    q_example = _sum([weight * weight for weight in weights])
    entities: dict[str, list[float]] = {}
    for row in rows.values():
        entities.setdefault(row["entity_id"], []).append(float(row["weight"]))
    entity_masses = [_sum(group) for group in entities.values()]
    q_entity = _sum([mass * mass for mass in entity_masses])
    if total == 0.0:
        example_concentration = entity_concentration = example_ess = entity_ess = None
        status = "zero_total_weight"
    else:
        total_squared = total * total
        example_concentration = q_example / total_squared
        entity_concentration = q_entity / total_squared
        example_ess = total_squared / q_example
        entity_ess = total_squared / q_entity
        status = "positive_weight"
    return {
        "status": status,
        "example_count": len(rows),
        "positive_example_count": sum(weight > 0 for weight in weights),
        "entity_count": len(entities),
        "positive_entity_count": sum(mass > 0 for mass in entity_masses),
        "sum_weights": total,
        "sum_squared_example_weights": q_example,
        "sum_squared_entity_weights": q_entity,
        "example_concentration": example_concentration,
        "entity_concentration": entity_concentration,
        "example_ess": example_ess,
        "entity_ess": entity_ess,
    }


def _cohort_mass(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]],
                 before_total: float, after_total: float) -> dict[str, Any]:
    """Summarize declared cohort support and mass over the union of both states."""
    cohort_ids = sorted(
        {row["cohort_id"] for row in before.values()}
        | {row["cohort_id"] for row in after.values()}
    )

    def by_cohort(rows: dict[str, dict[str, Any]], total: float) -> dict[str, dict[str, Any]]:
        groups: dict[str, list[float]] = {}
        for row in rows.values():
            groups.setdefault(row["cohort_id"], []).append(float(row["weight"]))
        result = {}
        for cohort_id in cohort_ids:
            weights = groups.get(cohort_id, [])
            mass = _sum(weights)
            result[cohort_id] = {
                "example_count": len(weights),
                "positive_example_count": sum(weight > 0 for weight in weights),
                "total_weight": mass,
                # A missing cohort has zero mass and therefore a zero share when
                # the state has positive mass. A zero-total state has no share.
                "mass_share": mass / total if total > 0 else None,
            }
        return result

    before_cohorts = by_cohort(before, before_total)
    after_cohorts = by_cohort(after, after_total)
    deltas = {}
    for cohort_id in cohort_ids:
        left, right = before_cohorts[cohort_id], after_cohorts[cohort_id]
        deltas[cohort_id] = {
            "example_count": right["example_count"] - left["example_count"],
            "positive_example_count": right["positive_example_count"] - left["positive_example_count"],
            "total_weight": right["total_weight"] - left["total_weight"],
            # Shares are comparable only when both state denominators exist.
            "mass_share": (right["mass_share"] - left["mass_share"]
                           if left["mass_share"] is not None and right["mass_share"] is not None else None),
        }
    return {
        "denominators": {
            "before": {"total_weight": before_total, "status": "positive" if before_total > 0 else "zero"},
            "after": {"total_weight": after_total, "status": "positive" if after_total > 0 else "zero"},
            "mass_share_delta": "defined only when both state total weights are positive",
            "absent_cohort": "zero support and zero total weight; share is zero when its state total is positive",
        },
        "before": before_cohorts,
        "after": after_cohorts,
        "deltas": deltas,
    }


def analyze(payload: dict, *, include_cohort_mass: bool = False) -> dict:
    """Compare exactly two supplied, pinned weight states, paired by example ID.

    State hashes are caller-declared references. Their provenance and relation
    to upstream artifacts are not independently checked.
    """
    try:
        if type(include_cohort_mass) is not bool:
            raise _invalid()
        if type(payload) is not dict or set(payload) != _ROOT_KEYS:
            raise _invalid()
        before_meta, before = _state(payload["before"])
        after_meta, after = _state(payload["after"])
        if before_meta["state_id"] == after_meta["state_id"]:
            raise _invalid()

        before_ids, after_ids = set(before), set(after)
        matched_ids = sorted(before_ids & after_ids)
        missing_ids = sorted(before_ids - after_ids)
        added_ids = sorted(after_ids - before_ids)
        changed_weights = []
        changed_identity = []
        zero_to_positive = []
        positive_to_zero = []
        for example_id in matched_ids:
            left, right = before[example_id], after[example_id]
            if left["weight"] != right["weight"]:
                changed_weights.append({"example_id": example_id, "before": left["weight"], "after": right["weight"], "delta": float(right["weight"]) - float(left["weight"])})
            if left["entity_id"] != right["entity_id"] or left["cohort_id"] != right["cohort_id"]:
                changed_identity.append({
                    "example_id": example_id,
                    "before": {"entity_id": left["entity_id"], "cohort_id": left["cohort_id"]},
                    "after": {"entity_id": right["entity_id"], "cohort_id": right["cohort_id"]},
                })
            left_positive, right_positive = float(left["weight"]) > 0, float(right["weight"]) > 0
            if not left_positive and right_positive:
                zero_to_positive.append(example_id)
            elif left_positive and not right_positive:
                positive_to_zero.append(example_id)

        before_metrics, after_metrics = _metrics(before), _metrics(after)
        metric_keys = ("sum_weights", "example_concentration", "entity_concentration", "example_ess", "entity_ess")
        metric_deltas = {
            key: (after_metrics[key] - before_metrics[key]
                  if before_metrics[key] is not None and after_metrics[key] is not None else None)
            for key in metric_keys
        }
        result = {
            "schema": "brain-research-diagnostic.v1",
            "kind": "weight_state_delta",
            "states": {"before": before_meta, "after": after_meta},
            "metrics": {"before": before_metrics, "after": after_metrics, "deltas": metric_deltas},
            "support": {
                "identity_denominators": {
                    "before": len(before_ids), "after": len(after_ids),
                    "matched": len(matched_ids), "missing_from_after": len(missing_ids),
                    "added_in_after": len(added_ids),
                },
                "matched_id_denominator": len(matched_ids),
                "weight_changed_count": len(changed_weights),
                "identity_changed_count": len(changed_identity),
                "positive_weight_denominators": {
                    "before": before_metrics["positive_example_count"],
                    "after": after_metrics["positive_example_count"],
                    "matched_before": sum(float(before[key]["weight"]) > 0 for key in matched_ids),
                    "matched_after": sum(float(after[key]["weight"]) > 0 for key in matched_ids),
                },
                "zero_to_positive_count": len(zero_to_positive),
                "positive_to_zero_count": len(positive_to_zero),
                "missing_example_ids": missing_ids,
                "added_example_ids": added_ids,
                "changed_weights": changed_weights,
                "changed_identity": changed_identity,
                "zero_to_positive_example_ids": zero_to_positive,
                "positive_to_zero_example_ids": positive_to_zero,
            },
            "limitations": [
                "Only supplied examples and caller-declared state pins are compared; pin provenance and state correctness are not verified.",
                "Examples pair only by exact example_id; missing and added identities have no inferred counterpart.",
                "Concentration and Kish effective size are descriptive and do not establish independence, uncertainty, representativeness, or learning quality.",
                "Weights must be finite, zero or within [1e-100, 1e100], with at most 10000 examples per state; results use binary64 arithmetic.",
            ],
        }
        if include_cohort_mass:
            result["cohort_mass"] = _cohort_mass(
                before, after, before_metrics["sum_weights"], after_metrics["sum_weights"]
            )
        return result
    except RecursionError as exc:
        raise _invalid() from exc


def analyze_json(document: str | bytes, *, include_cohort_mass: bool = False) -> dict:
    """Parse a bounded JSON document, rejecting duplicate keys and nonfinite values."""
    try:
        if type(document) is str:
            encoded = document.encode("utf-8")
        elif type(document) is bytes:
            encoded = document
        else:
            raise _invalid()
        if len(encoded) > _MAX_JSON_BYTES:
            raise _invalid()

        def unique_object(pairs: list[tuple[str, Any]]) -> dict:
            result = {}
            for key, value in pairs:
                if key in result:
                    raise _invalid()
                result[key] = value
            return result

        def reject_constant(_value: str) -> None:
            raise _invalid()

        payload = json.loads(encoded, object_pairs_hook=unique_object, parse_constant=reject_constant)
        return analyze(payload, include_cohort_mass=include_cohort_mass)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise _invalid() from exc


def canonical_state_sha256(state_id: str, examples: list[dict]) -> str:
    """Build a deterministic pin for synthetic or caller-side state construction."""
    try:
        state_id = _identifier(state_id)
        _meta, checked = _state({"state_id": state_id, "state_sha256": "0" * 64, "examples": examples})
        canonical = {"state_id": state_id, "examples": [checked[key] for key in sorted(checked)]}
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except (TypeError, UnicodeError, RecursionError) as exc:
        raise _invalid() from exc
