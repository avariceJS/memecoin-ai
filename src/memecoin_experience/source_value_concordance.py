"""Compare caller-aligned numeric values across explicitly declared sources.

Input is exactly ``sources``, nonnegative finite absolute/relative tolerances,
and observations with one unique (source, id) identity and a feature-to-number-or-
null mapping. For each feature/source pair, ids are drawn from the union of
observed ids; absent rows and null values are kept distinct. Joint support means
both sources have a non-null value. Agreement uses
``abs(a-b) <= atol + rtol * max(abs(a), abs(b))``. Deltas are signed ``right-left``
and emitted by aligned id in deterministic order. This is descriptive only; ids,
source identity, alignment and provenance are caller claims.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from typing import Any


_PAYLOAD_KEYS = {"sources", "absolute_tolerance", "relative_tolerance", "observations"}


def _number(value: Any, label: str, *, nullable: bool = False) -> float | int | None:
    if nullable and value is None:
        return None
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
        raise ValueError(f"source_value_concordance_{label}")
    return value


def analyze(payload: dict) -> dict:
    """Return deterministic feature-by-source-pair concordance diagnostics."""
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise ValueError("source_value_concordance_payload")
    sources = payload["sources"]
    observations = payload["observations"]
    if (not isinstance(sources, list) or len(sources) < 2 or
            any(not isinstance(s, str) or not s for s in sources) or len(set(sources)) != len(sources)):
        raise ValueError("source_value_concordance_sources")
    if not isinstance(observations, list):
        raise ValueError("source_value_concordance_observations")
    atol = _number(payload["absolute_tolerance"], "absolute_tolerance")
    rtol = _number(payload["relative_tolerance"], "relative_tolerance")
    if atol < 0 or rtol < 0:
        raise ValueError("source_value_concordance_tolerance")
    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("source_value_concordance_json_value") from exc

    declared = set(sources)
    values: dict[str, dict[str, dict[str, float | int | None]]] = {s: {} for s in sources}
    features: set[str] = set()
    for row in observations:
        if not isinstance(row, dict) or set(row) != {"source", "id", "values"}:
            raise ValueError("source_value_concordance_observation")
        source, identity, row_values = row["source"], row["id"], row["values"]
        if not isinstance(source, str) or source not in declared:
            raise ValueError("source_value_concordance_observation_source")
        if not isinstance(identity, str) or not identity:
            raise ValueError("source_value_concordance_observation_id")
        if identity in values[source]:
            raise ValueError("source_value_concordance_duplicate_identity")
        if not isinstance(row_values, dict) or any(not isinstance(k, str) or not k for k in row_values):
            raise ValueError("source_value_concordance_values")
        values[source][identity] = {k: _number(v, "value", nullable=True) for k, v in row_values.items()}
        features.update(row_values)

    null_rates = {}
    for feature in sorted(features):
        null_rates[feature] = {}
        for source in sorted(sources):
            rows = values[source]
            present = sum(feature in row for row in rows.values())
            null_count = sum(feature in row and row[feature] is None for row in rows.values())
            known = present - null_count
            null_rates[feature][source] = {
                "observation_count": len(rows),
                "feature_present_count": present,
                "feature_absent_count": len(rows) - present,
                "null_count": null_count,
                "known_count": known,
                "null_rate": null_count / present if present else None,
            }

    details = []
    for feature in sorted(features):
        for i, left in enumerate(sorted(sources)):
            for right in sorted(sources)[i + 1:]:
                ids = sorted(set(values[left]) | set(values[right]))
                agreement = disagreement = left_only = right_only = both_missing = 0
                left_absent_rows = right_absent_rows = 0
                deltas = []
                for identity in ids:
                    lrow, rrow = values[left].get(identity), values[right].get(identity)
                    left_absent_rows += lrow is None
                    right_absent_rows += rrow is None
                    a = lrow.get(feature) if lrow is not None else None
                    b = rrow.get(feature) if rrow is not None else None
                    if a is not None and b is not None:
                        try:
                            delta = b - a
                            threshold = atol + rtol * max(abs(a), abs(b))
                            if type(delta) is float and not math.isfinite(delta):
                                raise ValueError
                            agrees = abs(delta) <= threshold
                        except (OverflowError, TypeError, ValueError) as exc:
                            raise ValueError("source_value_concordance_numeric_range") from exc
                        deltas.append({"id": identity, "delta": delta})
                        if agrees:
                            agreement += 1
                        else:
                            disagreement += 1
                    elif a is not None:
                        left_only += 1
                    elif b is not None:
                        right_only += 1
                    else:
                        both_missing += 1
                joint = agreement + disagreement
                details.append({
                    "feature": feature, "left_source": left, "right_source": right,
                    "aligned_id_count": len(ids), "joint_support": joint,
                    "agreement_count": agreement, "disagreement_count": disagreement,
                    "agreement_rate": agreement / joint if joint else None,
                    "left_only_known_count": left_only, "right_only_known_count": right_only,
                    "both_missing_count": both_missing,
                    "left_absent_row_count": left_absent_rows,
                    "right_absent_row_count": right_absent_rows,
                    "value_deltas": deltas,
                })
    return {
        "schema": "brain-research-diagnostic.v1", "kind": "source_value_concordance",
        "input_sha256": hashlib.sha256(canonical).hexdigest(),
        "summary": {
            "source_count": len(sources), "observation_count": len(observations),
            "feature_count": len(features), "source_pair_feature_count": len(details),
            "joint_support": sum(item["joint_support"] for item in details),
            "agreement_count": sum(item["agreement_count"] for item in details),
            "disagreement_count": sum(item["disagreement_count"] for item in details),
            "null_rates": null_rates,
        },
        "details": details,
        "limitations": [
            "Alignment ids, source identity, values and provenance are caller claims and are not independently verified.",
            "Only ids with known values on both sources contribute to agreement or disagreement; absent rows and null values remain separate support categories.",
            "This is descriptive concordance under the supplied tolerances; it does not establish accuracy, causality or source completeness.",
        ],
    }


def _reject_constant(_value: str) -> None:
    raise ValueError("source_value_concordance_nonfinite_json")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("source_value_concordance_duplicate_key")
        result[key] = value
    return result


def main() -> int:
    try:
        payload = json.load(sys.stdin, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        result = analyze(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        print(str(exc) or "source_value_concordance_invalid_input", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
