"""Describe example, entity, and cohort weight concentration for caller data."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from typing import Any


_MAX_EXAMPLES = 10_000
_MAX_STDIN_BYTES = 8 * 1024 * 1024
_MIN_WEIGHT = 1e-100
_MAX_WEIGHT = 1e100
_LIMITATIONS = [
    "Descriptive weight concentration only; Kish effective sample size does not establish statistical independence, uncertainty, representativeness, or learning quality.",
    "Example, entity and cohort identities and weights are caller claims; no missing population, provenance or omitted examples are inferred.",
    "Entity weights aggregate across cohorts in the total; cohort entity-square sums need not add to the global entity-square sum.",
    "Only finite weights equal to zero or within [1e-100, 1e100] and at most 10000 examples are accepted; results use binary64 arithmetic.",
]
def _invalid(suffix: str) -> ValueError:
    return ValueError("weight_concentration_" + suffix)


def _valid_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and all("!" <= character <= "~" for character in value)
    )


def _validate(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {"examples"}:
        raise _invalid("payload")
    examples = payload["examples"]
    if not isinstance(examples, list) or len(examples) > _MAX_EXAMPLES:
        raise _invalid("examples")

    seen_ids: set[str] = set()
    for row in examples:
        if not isinstance(row, dict) or set(row) != {"id", "entity", "cohort", "weight"}:
            raise _invalid("example")
        for key in ("id", "entity", "cohort"):
            if not _valid_id(row[key]):
                raise _invalid("identifier")
        if row["id"] in seen_ids:
            raise _invalid("duplicate_id")
        seen_ids.add(row["id"])

        weight = row["weight"]
        if type(weight) is int:
            # Compare as integers first: float(10**100 + 1) can round down to
            # the binary64 representation of 1e100.
            if weight < 0 or weight > 10**100:
                raise _invalid("weight")
            if weight != 0 and weight < 1:
                raise _invalid("weight")
        elif type(weight) is float:
            if not math.isfinite(weight) or weight < 0:
                raise _invalid("weight")
            if weight != 0.0 and not (_MIN_WEIGHT <= weight <= _MAX_WEIGHT):
                raise _invalid("weight")
        else:
            raise _invalid("weight")
    return examples


def _ordered_sum(values: list[float]) -> float:
    return math.fsum(sorted(values))


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    weights = [float(row["weight"]) for row in rows]
    total = _ordered_sum(weights)
    example_squares = _ordered_sum([weight * weight for weight in weights])
    entity_weights: dict[str, list[float]] = {}
    for row, weight in zip(rows, weights):
        entity_weights.setdefault(row["entity"], []).append(weight)
    entity_masses = [_ordered_sum(group) for group in entity_weights.values()]
    entity_squares = _ordered_sum([mass * mass for mass in entity_masses])
    positive_examples = sum(weight > 0.0 for weight in weights)
    positive_entities = sum(mass > 0.0 for mass in entity_masses)

    if total == 0.0:
        concentration_x = concentration_e = ess_x = ess_e = None
        status = "zero_total_weight"
    else:
        total_squared = total * total
        concentration_x = example_squares / total_squared
        concentration_e = entity_squares / total_squared
        ess_x = total_squared / example_squares
        ess_e = total_squared / entity_squares
        status = "positive_weight"

    return {
        "status": status,
        "example_count": len(rows),
        "positive_example_count": positive_examples,
        "entity_count": len(entity_weights),
        "positive_entity_count": positive_entities,
        "sum_weights": total,
        "sum_squared_example_weights": example_squares,
        "sum_squared_entity_weights": entity_squares,
        "example_concentration": concentration_x,
        "entity_concentration": concentration_e,
        "example_ess": ess_x,
        "entity_ess": ess_e,
    }


def analyze(payload: dict) -> dict:
    """Return deterministic concentration summaries without mutating ``payload``."""
    examples = _validate(payload)
    try:
        canonical_input = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeEncodeError) as exc:
        raise _invalid("payload") from exc

    summary = _metrics(examples)
    cohort_groups: dict[str, list[dict[str, Any]]] = {}
    entity_groups: dict[str, list[dict[str, Any]]] = {}
    for row in examples:
        cohort_groups.setdefault(row["cohort"], []).append(row)
        entity_groups.setdefault(row["entity"], []).append(row)
    total = summary["sum_weights"]
    summary["cohort_count"] = len(cohort_groups)
    summary["positive_cohort_count"] = sum(
        _ordered_sum([float(row["weight"]) for row in group]) > 0.0
        for group in cohort_groups.values()
    )

    example_details = []
    for row in sorted(examples, key=lambda item: item["id"]):
        weight = float(row["weight"])
        example_details.append(
            {
                "id": row["id"],
                "entity": row["entity"],
                "cohort": row["cohort"],
                "weight": row["weight"],
                "weight_share": weight / total if total > 0.0 else None,
            }
        )

    entity_details = []
    for entity in sorted(entity_groups):
        metrics = _metrics(entity_groups[entity])
        entity_details.append(
            {
                "entity": entity,
                "weight_share": metrics["sum_weights"] / total if total > 0.0 else None,
                "metrics": metrics,
            }
        )

    cohort_details = []
    for cohort in sorted(cohort_groups):
        metrics = _metrics(cohort_groups[cohort])
        cohort_details.append(
            {
                "cohort": cohort,
                "weight_share": metrics["sum_weights"] / total if total > 0.0 else None,
                "metrics": metrics,
            }
        )

    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "weight_concentration",
        "input_sha256": hashlib.sha256(canonical_input).hexdigest(),
        "summary": summary,
        "details": {
            "examples": example_details,
            "entities": entity_details,
            "cohorts": cohort_details,
        },
        "limitations": list(_LIMITATIONS),
    }


def _reject_constant(_value: str) -> None:
    raise _invalid("nonfinite_json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid("duplicate_key")
        result[key] = value
    return result


def main() -> int:
    raw = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
    if len(raw) > _MAX_STDIN_BYTES:
        error = _invalid("input_too_large")
    else:
        try:
            text = raw.decode("utf-8", errors="strict")
            payload = json.loads(
                text,
                parse_constant=_reject_constant,
                object_pairs_hook=_unique_object,
            )
            result = analyze(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            error = exc if str(exc).startswith("weight_concentration_") else _invalid("json")
        else:
            json.dump(
                result,
                sys.stdout,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            sys.stdout.write("\n")
            return 0
    print(str(error) or "weight_concentration_invalid_input", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
