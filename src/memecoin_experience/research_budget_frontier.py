"""Exact cost/benefit frontier for caller-declared research options.

This module performs bounded descriptive arithmetic only. It does not plan or
execute research.
"""

import json
import re
import sys


_ERROR = "research_budget_frontier_invalid_input"
_MAX_BUDGET = 16_000_000
_MAX_VALUE = 1_000_000
_MAX_OPTIONS = 16
_MAX_INPUT_BYTES = 1_048_576
_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_LIMITATIONS = [
    "Costs and additive benefits are caller declarations; no measured utility, uncertainty or interactions are inferred.",
    "The frontier describes feasible subsets only; it does not authorize, schedule or execute research.",
    "Exact results are limited to at most 16 options and 65536 subsets; one canonical witness is retained per frontier point.",
]


def _invalid():
    raise ValueError(_ERROR)


def _validate(payload):
    if type(payload) is not dict or set(payload) != {"budget_units", "options"}:
        _invalid()
    budget = payload["budget_units"]
    options = payload["options"]
    if type(budget) is not int or not 0 <= budget <= _MAX_BUDGET:
        _invalid()
    if type(options) is not list or len(options) > _MAX_OPTIONS:
        _invalid()
    seen = set()
    for option in options:
        if type(option) is not dict or set(option) != {"id", "cost_units", "benefit_units"}:
            _invalid()
        identifier = option["id"]
        if (type(identifier) is not str or _ID_PATTERN.fullmatch(identifier) is None
                or identifier in seen):
            _invalid()
        seen.add(identifier)
        for key in ("cost_units", "benefit_units"):
            value = option[key]
            if type(value) is not int or not 0 <= value <= _MAX_VALUE:
                _invalid()
    return budget, options


def analyze(payload: dict) -> dict:
    """Return all nondominated feasible cost/benefit points and witnesses."""
    budget, options = _validate(payload)
    # Coordinate classes retain only the canonical witness, independent of
    # option input order.
    witnesses = {}
    count = len(options)
    for mask in range(1 << count):
        cost = benefit = 0
        ids = []
        for index, option in enumerate(options):
            if mask & (1 << index):
                cost += option["cost_units"]
                if cost > budget:
                    break
                benefit += option["benefit_units"]
                ids.append(option["id"])
        else:
            ids.sort()
            coordinate = (cost, benefit)
            candidate = tuple(ids)
            previous = witnesses.get(coordinate)
            if previous is None or (len(candidate), candidate) < (len(previous), previous):
                witnesses[coordinate] = candidate

    frontier = []
    # For each cost, visit the largest benefit first. Any later point with a
    # benefit no greater than the running maximum is dominated by an earlier
    # (or same-cost) coordinate.
    best_benefit = -1
    for cost, benefit in sorted(witnesses, key=lambda point: (point[0], -point[1])):
        if benefit <= best_benefit:
            continue
        best_benefit = benefit
        frontier.append({"cost_units": cost, "benefit_units": benefit,
                         "option_ids": list(witnesses[(cost, benefit)])})

    return {
        "schema": "research-budget-frontier-v1",
        "summary": {"option_count": count, "budget_units": budget,
                    "subset_count_bound": 1 << count,
                    "frontier_point_count": len(frontier)},
        "frontier": frontier,
        "limitations": list(_LIMITATIONS),
    }


def _reject_constant(_value):
    raise ValueError(_ERROR)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_ERROR)
        result[key] = value
    return result


def main():
    """Strict stdin JSON interface; return the process exit status."""
    try:
        if len(sys.argv) != 1:
            _invalid()
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            _invalid()
        decoded = raw.decode("utf-8", errors="strict")
        payload = json.loads(decoded, object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant)
        result = analyze(payload)
        sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False) + "\n")
        return 0
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        sys.stderr.write('{"error":"research_budget_frontier_invalid_input"}\n')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
