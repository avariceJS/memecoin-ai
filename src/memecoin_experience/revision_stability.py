"""Describe supplied numeric observation histories across released revisions.

The final supplied revision is only a reference for absolute-distance and
suffix calculations. This module makes no completeness or future-stability
claim about the supplied histories.
"""

from __future__ import annotations

import json
import math
import re
import sys
from fractions import Fraction
from typing import Any


_INVALID = "revision_stability_invalid_input"
_ROOT_KEYS = {"absolute_tolerance", "observations"}
_OBSERVATION_KEYS = {"id", "revisions"}
_REVISION_KEYS = {"revision", "released_ns", "value"}
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_MAX_VALUE = 10**12
_MAX_REVISION = 2_147_483_647
_MAX_RELEASED_NS = 9_223_372_036_854_775_807
_MAX_STDIN_BYTES = 1_048_576
_LIMITATIONS = [
    "Only caller-supplied numeric revision histories are analyzed; omitted revisions and provenance are unknown.",
    "The final supplied revision is a reference, not truth; a stable supplied suffix does not predict future revisions.",
    "Release ordering and times are caller claims; event, availability, receipt and decision times are not inferred.",
    "Tolerance is absolute in caller-declared value units; histories are not pooled and no accuracy, causal or completeness claim is made.",
]


def _invalid() -> ValueError:
    return ValueError(_INVALID)


def _number(value: Any) -> float:
    if type(value) not in (int, float):
        raise _invalid()
    if type(value) is int:
        if value < -_MAX_VALUE or value > _MAX_VALUE:
            raise _invalid()
    elif not math.isfinite(value) or abs(value) > _MAX_VALUE:
        raise _invalid()
    normalized = float(value)
    return 0.0 if normalized == 0.0 else normalized


def _bounded_int(value: Any, maximum: int) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise _invalid()
    return value


def _as_float(value: Fraction) -> float:
    result = float(value)
    return 0.0 if result == 0.0 else result


def analyze(payload: dict) -> dict:
    """Validate and summarize explicit numeric revision histories."""
    try:
        if type(payload) is not dict or set(payload) != _ROOT_KEYS:
            raise _invalid()
        tolerance = _number(payload["absolute_tolerance"])
        if tolerance < 0.0:
            raise _invalid()
        observations = payload["observations"]
        if type(observations) is not list or len(observations) > 256:
            raise _invalid()

        ids: set[str] = set()
        checked: list[tuple[str, list[tuple[int, int, float, Fraction]]]] = []
        for observation in observations:
            if type(observation) is not dict or set(observation) != _OBSERVATION_KEYS:
                raise _invalid()
            identity = observation["id"]
            if type(identity) is not str or _ID_RE.fullmatch(identity) is None or identity in ids:
                raise _invalid()
            ids.add(identity)
            rows = observation["revisions"]
            if type(rows) is not list or not 1 <= len(rows) <= 64:
                raise _invalid()
            revisions: list[tuple[int, int, float, Fraction]] = []
            ordinals: set[int] = set()
            for row in rows:
                if type(row) is not dict or set(row) != _REVISION_KEYS:
                    raise _invalid()
                ordinal = _bounded_int(row["revision"], _MAX_REVISION)
                released_ns = _bounded_int(row["released_ns"], _MAX_RELEASED_NS)
                value = _number(row["value"])
                if ordinal in ordinals:
                    raise _invalid()
                ordinals.add(ordinal)
                revisions.append((ordinal, released_ns, value, Fraction.from_float(value)))
            revisions.sort(key=lambda item: item[0])
            if any(right[1] < left[1] for left, right in zip(revisions, revisions[1:])):
                raise _invalid()
            checked.append((identity, revisions))

        checked.sort(key=lambda item: item[0])
        tolerance_exact = Fraction.from_float(tolerance)
        output_observations = []
        totals = {
            "observation_count": len(checked),
            "revision_count": 0,
            "transition_count": 0,
            "changed_transition_count": 0,
            "direction_reversal_count": 0,
            "single_revision_count": 0,
        }

        for identity, rows in checked:
            count = len(rows)
            final = rows[-1]
            distances = [abs(row[3] - final[3]) for row in rows]
            within = [distance <= tolerance_exact for distance in distances]
            suffix_start = next(
                start for start in range(count)
                if all(within[index] for index in range(start, count))
            )
            steps = [rows[index][3] - rows[index - 1][3] for index in range(1, count)]
            nonzero_signs = [1 if step > 0 else -1 for step in steps if step]
            changed_count = len(nonzero_signs)
            reversal_count = sum(left != right for left, right in zip(nonzero_signs, nonzero_signs[1:]))
            transition_count = count - 1
            totals["revision_count"] += count
            totals["transition_count"] += transition_count
            totals["changed_transition_count"] += changed_count
            totals["direction_reversal_count"] += reversal_count
            if count == 1:
                totals["single_revision_count"] += 1

            revision_output = []
            for index, (ordinal, released_ns, value, _exact) in enumerate(rows):
                delta = None if index == 0 else _as_float(steps[index - 1])
                revision_output.append({
                    "revision": ordinal,
                    "released_ns": released_ns,
                    "value": value,
                    "delta_from_previous": delta,
                    "absolute_distance_to_reference": _as_float(distances[index]),
                    "within_tolerance": within[index],
                })
            suffix_row = rows[suffix_start]
            output_observations.append({
                "id": identity,
                "revision_count": count,
                "transition_count": transition_count,
                "changed_transition_count": changed_count,
                "direction_reversal_count": reversal_count,
                "reference": {
                    "kind": "final_supplied_revision",
                    "revision": final[0],
                    "released_ns": final[1],
                    "value": final[2],
                },
                "stable_suffix": {
                    "from_revision": suffix_row[0],
                    "from_released_ns": suffix_row[1],
                    "revision_count": count - suffix_start,
                },
                "revisions": revision_output,
            })

        return {
            "schema": "brain-revision-stability.v1",
            "absolute_tolerance": tolerance,
            "summary": totals,
            "observations": output_observations,
            "limitations": list(_LIMITATIONS),
        }
    except RecursionError as exc:
        raise _invalid() from exc


def _reject_constant(_value: str) -> None:
    raise _invalid()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise _invalid()
        raw = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
        if len(raw) > _MAX_STDIN_BYTES:
            raise _invalid()
        document = raw.decode("utf-8", errors="strict")
        payload = json.loads(
            document,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
        result = analyze(payload)
        encoded = json.dumps(
            result, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        message = str(exc)
        if message != _INVALID:
            message = _INVALID
        sys.stderr.write(message + "\n")
        return 2
    sys.stdout.write(encoded + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
