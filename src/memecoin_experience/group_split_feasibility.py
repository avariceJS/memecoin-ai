"""Exact bounded subset-sum diagnostic for indivisible research groups."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from typing import Any

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)
_LIMITATIONS = [
    "Exact quota feasibility concerns only caller-declared disjoint indivisible group count vectors; group membership, disjointness and omitted examples are not verified.",
    "A feasible witness is a group-level split candidate, not temporal, maturity, leakage, representativeness, independence, data-access or training qualification.",
    "The rectangular bound is the product of requested per-stratum counts plus one; limit_exceeded leaves feasibility unknown even when a sparse or shortcut solution might exist.",
    "At most 64 groups, 8 strata, counts through 1000000 and a state budget through 50000 are supported; no approximate infeasibility is reported.",
]


def _fail(message: str) -> None:
    raise ValueError(f"group_split_feasibility_{message}")


def _integer(value: Any, low: int, high: int, field: str) -> int:
    if type(value) is not int or not low <= value <= high:
        _fail(f"invalid_{field}")
    return value


def _identifier(value: Any, field: str) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        _fail(f"invalid_{field}")
    return value


def analyze(payload: dict) -> dict:
    """Validate and decide exact grouped quota feasibility without I/O or mutation."""
    if type(payload) is not dict or set(payload) != {"groups", "holdout_counts", "max_states"}:
        _fail("invalid_input_keys")
    targets = payload["holdout_counts"]
    if type(targets) is not dict or not 1 <= len(targets) <= 8:
        _fail("invalid_holdout_counts")
    strata = sorted(_identifier(key, "stratum_id") for key in targets)
    if len(set(strata)) != len(strata):
        _fail("duplicate_stratum_id")
    for key in strata:
        _integer(targets[key], 0, 1_000_000, "holdout_count")
    groups = payload["groups"]
    if type(groups) is not list or len(groups) > 64:
        _fail("invalid_groups")
    budget = _integer(payload["max_states"], 1, 50_000, "max_states")
    ids: set[str] = set()
    normalized: list[tuple[str, tuple[int, ...]]] = []
    for group in groups:
        if type(group) is not dict or set(group) != {"id", "counts"}:
            _fail("invalid_group_keys")
        group_id = _identifier(group["id"], "group_id")
        if group_id in ids:
            _fail("duplicate_group_id")
        ids.add(group_id)
        counts = group["counts"]
        if type(counts) is not dict or set(counts) != set(strata):
            _fail("invalid_group_counts")
        vector = tuple(_integer(counts[s], 0, 1_000_000, "group_count") for s in strata)
        normalized.append((group_id, vector))

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    limits = tuple(targets[s] for s in strata)
    bound = 1
    for count in limits:
        bound *= count + 1
    totals = tuple(sum(vector[i] for _, vector in normalized) for i in range(len(strata)))
    requested = sum(limits)
    if bound > budget:
        status = "limit_exceeded"
        witness = None
    else:
        # State values are sorted ID tuples, ordered exactly by the contract's
        # (cardinality, sorted IDs) witness preference.
        states: dict[tuple[int, ...], tuple[str, ...]] = {(0,) * len(strata): ()}
        for group_id, vector in sorted(normalized):
            if not any(vector):
                continue
            additions: dict[tuple[int, ...], tuple[str, ...]] = {}
            for state, selected in tuple(states.items()):
                updated = tuple(a + b for a, b in zip(state, vector))
                if any(updated[i] > limits[i] for i in range(len(strata))):
                    continue
                candidate = tuple(sorted((*selected, group_id)))
                previous = states.get(updated, additions.get(updated))
                if previous is None or (len(candidate), candidate) < (len(previous), previous):
                    additions[updated] = candidate
            for state, candidate in additions.items():
                previous = states.get(state)
                if previous is None or (len(candidate), candidate) < (len(previous), previous):
                    states[state] = candidate
        witness = states.get(limits)
        status = "feasible" if witness is not None else "infeasible"
    chosen = set(witness) if witness is not None else set()
    details = [
        {"stratum": s, "total_count": totals[i], "requested_holdout_count": limits[i],
         "training_count": totals[i] - limits[i] if witness is not None else None}
        for i, s in enumerate(strata)
    ]
    return {
        "schema": "brain-research-diagnostic.v1",
        "kind": "group_split_feasibility",
        "input_sha256": digest,
        "summary": {"status": status, "group_count": len(groups), "stratum_count": len(strata),
                    "total_count": sum(totals), "requested_holdout_count": requested,
                    "state_space_upper_bound": bound, "state_budget": budget},
        "details": {"strata": details,
                    "holdout_group_ids": list(witness) if witness is not None else None,
                    "train_group_ids": sorted(ids - chosen) if witness is not None else None},
        "limitations": list(_LIMITATIONS),
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("group_split_feasibility_duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("group_split_feasibility_nonfinite_json_constant")


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("group_split_feasibility_input_too_large")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        if type(value) is not dict:
            _fail("invalid_input_object")
        result = analyze(value)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        message = str(exc)
        if not message.startswith("group_split_feasibility_"):
            message = "group_split_feasibility_invalid_json"
        print(message, file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(result, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
