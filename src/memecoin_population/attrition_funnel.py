"""Descriptive attrition diagnostics over explicit, caller-supplied cohort counts.

Input is a mapping with ordered ``stages`` (unique nonempty strings), ``cohorts``
(unique ``cohort`` identities and a ``counts`` mapping), and optional
``completeness`` records. A count may be a nonnegative integer or ``null``;
omitted and null counts both mean unknown, never zero. Every adjacent pair is
reported for each cohort. Absolute loss is ``from_count - to_count`` when both
counts are known; conditional retention is ``to_count / from_count`` only for
a positive starting count. A zero starting count has a distinct
``zero_denominator`` status and undefined retention. Increases between known
adjacent counts are invalid.

Global transition totals and retention are computed only for cohorts named in
an explicit completeness record of the form ``{"from_stage": ..., "to_stage":
..., "complete_cohorts": [...]}``. Those cohorts must have both counts present.
No record means global metrics are null, not estimates from available rows.
Largest attrition is the first adjacent transition with the greatest evidenced
absolute loss; ties follow stage order. This diagnostic does not discover,
store, impute, or authorize use of data.

The JSON CLI reads one payload from stdin and writes one common diagnostic
envelope to stdout. Its input digest uses sorted, compact UTF-8 JSON with
non-ASCII preserved and non-finite values forbidden.
"""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any


_KIND = "attrition_funnel"
_SCHEMA = "brain-research-diagnostic.v1"


def _canonical(payload: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("payload_not_canonical_json") from exc


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_payload(payload: Any) -> tuple[list[str], list[dict[str, Any]], dict[tuple[str, str], list[str]]]:
    if not isinstance(payload, dict) or set(payload) - {"stages", "cohorts", "completeness"}:
        raise ValueError("payload_structure")
    if "stages" not in payload or "cohorts" not in payload:
        raise ValueError("payload_structure")

    stages = payload["stages"]
    if not isinstance(stages, list) or not stages or any(not _is_nonempty_string(s) for s in stages):
        raise ValueError("stages_structure")
    if len(set(stages)) != len(stages):
        raise ValueError("duplicate_stage")
    stage_set = set(stages)

    raw_cohorts = payload["cohorts"]
    if not isinstance(raw_cohorts, list) or not raw_cohorts:
        raise ValueError("cohorts_structure")
    cohorts: list[dict[str, Any]] = []
    cohort_names: set[str] = set()
    for item in raw_cohorts:
        if not isinstance(item, dict) or set(item) != {"cohort", "counts"}:
            raise ValueError("cohort_structure")
        name = item["cohort"]
        counts = item["counts"]
        if not _is_nonempty_string(name):
            raise ValueError("cohort_identity")
        if name in cohort_names:
            raise ValueError("duplicate_cohort")
        cohort_names.add(name)
        if not isinstance(counts, dict) or any(key not in stage_set for key in counts):
            raise ValueError("counts_structure")
        normalized: dict[str, int | None] = {}
        for stage in stages:
            count = counts.get(stage)
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("count_value")
            normalized[stage] = count
        cohorts.append({"cohort": name, "counts": normalized})

    complete: dict[tuple[str, str], list[str]] = {}
    records = payload.get("completeness", [])
    if not isinstance(records, list):
        raise ValueError("completeness_structure")
    stage_indices = {stage: i for i, stage in enumerate(stages)}
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "from_stage", "to_stage", "complete_cohorts"
        }:
            raise ValueError("completeness_record")
        left, right, names = (
            record["from_stage"], record["to_stage"], record["complete_cohorts"]
        )
        if (
            not isinstance(left, str)
            or not isinstance(right, str)
            or left not in stage_indices
            or right not in stage_indices
            or stage_indices[right] != stage_indices[left] + 1
        ):
            raise ValueError("completeness_transition")
        key = (left, right)
        if key in complete:
            raise ValueError("duplicate_completeness")
        if not isinstance(names, list) or any(not _is_nonempty_string(n) for n in names):
            raise ValueError("complete_cohorts_structure")
        if len(set(names)) != len(names):
            raise ValueError("duplicate_complete_cohort")
        if any(name not in cohort_names for name in names):
            raise ValueError("unknown_complete_cohort")
        by_name = {row["cohort"]: row["counts"] for row in cohorts}
        if any(by_name[name][left] is None or by_name[name][right] is None for name in names):
            raise ValueError("incomplete_cohort_counts")
        complete[key] = names

    for cohort in cohorts:
        counts = cohort["counts"]
        for left, right in zip(stages, stages[1:]):
            before, after = counts[left], counts[right]
            if before is not None and after is not None and after > before:
                raise ValueError("increasing_counts")
    return stages, cohorts, complete


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic per-cohort and explicitly supported global results."""
    stages, cohorts, complete = _validate_payload(payload)
    input_sha256 = hashlib.sha256(_canonical(payload)).hexdigest()
    transitions: list[dict[str, Any]] = []
    cohort_results: list[dict[str, Any]] = []

    for cohort in cohorts:
        rows: list[dict[str, Any]] = []
        evidenced: list[tuple[int, int]] = []
        counts = cohort["counts"]
        for index, (left, right) in enumerate(zip(stages, stages[1:])):
            before, after = counts[left], counts[right]
            if before is None or after is None:
                status = "missing_support"
                loss = retention = None
            elif before == 0:
                status = "zero_denominator"
                loss, retention = 0, None
                evidenced.append((index, loss))
            else:
                status = "observed"
                loss = before - after
                retention = after / before
                evidenced.append((index, loss))
            rows.append({
                "from_stage": left,
                "to_stage": right,
                "from_count": before,
                "to_count": after,
                "status": status,
                "absolute_loss": loss,
                "conditional_retention": retention,
            })
        largest = max(evidenced, key=lambda pair: pair[1]) if evidenced else None
        largest_result = None if largest is None else {
            "from_stage": stages[largest[0]],
            "to_stage": stages[largest[0] + 1],
            "absolute_loss": largest[1],
        }
        cohort_results.append({
            "cohort": cohort["cohort"],
            "transitions": rows,
            "largest_evidenced_attrition": largest_result,
        })

    by_name = {row["cohort"]: row["counts"] for row in cohorts}
    for left, right in zip(stages, stages[1:]):
        names = complete.get((left, right))
        if names is None:
            global_result = {
                "status": "completeness_unspecified",
                "complete_cohorts": None,
                "support": None,
                "from_total": None,
                "to_total": None,
                "absolute_loss": None,
                "conditional_retention": None,
            }
        else:
            before = sum(by_name[name][left] for name in names)
            after = sum(by_name[name][right] for name in names)
            global_result = {
                "status": "observed" if before > 0 else "zero_denominator",
                "complete_cohorts": names,
                "support": len(names),
                "from_total": before,
                "to_total": after,
                "absolute_loss": before - after,
                "conditional_retention": after / before if before > 0 else None,
            }
        transitions.append({"from_stage": left, "to_stage": right, **global_result})

    return {
        "schema": _SCHEMA,
        "kind": _KIND,
        "input_sha256": input_sha256,
        "summary": {
            "cohort_count": len(cohorts),
            "stage_count": len(stages),
            "transition_count": max(0, len(stages) - 1),
        },
        "details": {"transitions": transitions, "cohorts": cohort_results},
        "limitations": [
            "Descriptive diagnostic over caller-supplied counts; it does not establish causation or data quality.",
            "Missing counts are unknown, and global metrics require an explicit complete-cohort list for each transition.",
            "Conditional retention is undefined when the starting count is zero.",
        ],
    }


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def main() -> None:
    payload = json.load(sys.stdin, object_pairs_hook=_reject_duplicate_json_keys)
    sys.stdout.write(json.dumps(analyze(payload), ensure_ascii=False, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
