"""Summarize adjacent categorical state transitions in declared episodes.

This is a bounded descriptive tally over caller-supplied observations. It does
not infer missing observations, connect episode boundaries, or fit dynamics.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from typing import Any

_PREFIX = "state_transition_summary_"
_MAX_NS = 2**63 - 1
_MAX_INPUT_BYTES = 8 * 1024 * 1024
_LABEL = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)
_ROOT_KEYS = {"states", "episodes", "max_gap_ns"}
_EPISODE_KEYS = {"id", "observations"}
_OBSERVATION_KEYS = {"id", "event_ns", "state"}
_REASONS = ("missing_both", "missing_from", "missing_to", "gap_exceeded")


def _fail(label: str) -> None:
    raise ValueError(f"{_PREFIX}{label}")


def _label(value: Any, label: str) -> str:
    if type(value) is not str or _LABEL.fullmatch(value) is None:
        _fail(label)
    return value


def _timestamp(value: Any, label: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_NS:
        _fail(label)
    return value


def analyze(payload: dict) -> dict:
    """Validate and summarize a payload without modifying it or doing I/O."""
    if type(payload) is not dict or set(payload) != _ROOT_KEYS:
        _fail("payload")
    declared = payload["states"]
    if type(declared) is not list or not 1 <= len(declared) <= 32:
        _fail("states")
    states: list[str] = []
    for state in declared:
        state = _label(state, "state_label")
        if state in states:
            _fail("duplicate_state")
        states.append(state)
    state_set = set(states)
    max_gap = _timestamp(payload["max_gap_ns"], "max_gap_ns")
    episodes = payload["episodes"]
    if type(episodes) is not list or len(episodes) > 1000:
        _fail("episodes")

    parsed: list[tuple[str, list[tuple[str, int, str | None]]]] = []
    episode_ids: set[str] = set()
    total_observations = 0
    missing_count = 0
    for episode in episodes:
        if type(episode) is not dict or set(episode) != _EPISODE_KEYS:
            _fail("episode")
        episode_id = _label(episode["id"], "episode_id")
        if episode_id in episode_ids:
            _fail("duplicate_episode_id")
        episode_ids.add(episode_id)
        observations = episode["observations"]
        if type(observations) is not list or len(observations) > 10000:
            _fail("observations")
        total_observations += len(observations)
        if total_observations > 10000:
            _fail("total_observations")
        rows: list[tuple[str, int, str | None]] = []
        observation_ids: set[str] = set()
        previous_time = -1
        for observation in observations:
            if type(observation) is not dict or set(observation) != _OBSERVATION_KEYS:
                _fail("observation")
            identity = _label(observation["id"], "observation_id")
            if identity in observation_ids:
                _fail("duplicate_observation_id")
            observation_ids.add(identity)
            event_ns = _timestamp(observation["event_ns"], "event_ns")
            if event_ns <= previous_time:
                _fail("timestamp_order")
            previous_time = event_ns
            state = observation["state"]
            if state is not None and (type(state) is not str or state not in state_set):
                _fail("observation_state")
            if state is None:
                missing_count += 1
            rows.append((identity, event_ns, state))
        parsed.append((episode_id, rows))

    sorted_states = sorted(states)
    counts: Counter[tuple[str, str]] = Counter()
    excluded: list[dict[str, Any]] = []
    reason_counts = {reason: 0 for reason in _REASONS}
    candidate_count = 0
    for episode_id, rows in parsed:
        candidate_count += max(len(rows) - 1, 0)
        for edge_index, (left, right) in enumerate(zip(rows, rows[1:])):
            left_id, left_ns, left_state = left
            right_id, right_ns, right_state = right
            gap_ns = right_ns - left_ns
            if left_state is None and right_state is None:
                reason = "missing_both"
            elif left_state is None:
                reason = "missing_from"
            elif right_state is None:
                reason = "missing_to"
            elif gap_ns > max_gap:
                reason = "gap_exceeded"
            else:
                counts[(left_state, right_state)] += 1
                continue
            reason_counts[reason] += 1
            excluded.append({"episode_id": episode_id, "edge_index": edge_index,
                             "from_id": left_id, "to_id": right_id,
                             "gap_ns": gap_ns, "reason": reason})

    excluded.sort(key=lambda item: (item["episode_id"], item["edge_index"]))
    outgoing = {state: sum(counts[(state, target)] for target in sorted_states)
                for state in sorted_states}
    included = sum(counts.values())
    excluded_count = sum(reason_counts.values())
    transitions = []
    for origin in sorted_states:
        denominator = outgoing[origin]
        for destination in sorted_states:
            count = counts[(origin, destination)]
            transitions.append({
                "from_state": origin,
                "to_state": destination,
                "count": count,
                "denominator": denominator,
                "proportion": count / denominator if denominator else None,
            })
    return {
        "schema": "brain-state-transition-summary.v1",
        "states": sorted_states,
        "summary": {
            "episode_count": len(episodes),
            "observation_count": total_observations,
            "missing_state_count": missing_count,
            "candidate_edge_count": candidate_count,
            "included_edge_count": included,
            "excluded_edge_count": excluded_count,
            "excluded_reason_counts": reason_counts,
        },
        "origins": [{"state": state, "outgoing_count": outgoing[state]}
                    for state in sorted_states],
        "transitions": transitions,
        "excluded_edges": excluded,
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_constant: str) -> None:
    _fail("nonfinite_json")


def main() -> int:
    """Read one bounded strict JSON value from stdin and emit its summary."""
    try:
        if len(sys.argv) != 1:
            _fail("arguments_not_supported")
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            _fail("input_too_large")
        source = raw.decode("utf-8", errors="strict")
        payload = json.loads(source, object_pairs_hook=_unique_object,
                             parse_constant=_reject_constant)
        result = analyze(payload)
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
    except (ValueError, UnicodeError, RecursionError) as exc:
        detail = str(exc).splitlines()[0] if str(exc) else "invalid_input"
        print(f"{_PREFIX}invalid_input: {detail}", file=sys.stderr)
        return 2
    sys.stdout.write(encoded + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
