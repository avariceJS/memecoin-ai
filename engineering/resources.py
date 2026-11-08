"""Deterministic, fail-closed capacity selection for bounded engineering workers."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

DEFAULT_POLICY: dict[str, int | float] = {
    "max_workers": 2,
    "min_available_memory_bytes": 1_073_741_824,
}


def choose_capacity(
    usage_summary: Mapping[str, Any],
    resources: Mapping[str, Any],
    policy: Mapping[str, Any] | None = None,
    *,
    ready_tasks: Sequence[Mapping[str, Any]] = (),
    active_scopes: Sequence[str] = (),
    last_team: str | None = None,
) -> dict[str, Any]:
    """Return local CPU/memory/concurrency slots and fair task picks."""
    settings = dict(DEFAULT_POLICY)
    if policy is not None:
        settings.update(policy)
    try:
        max_workers = _integer(settings["max_workers"], positive=True)
        min_memory = _integer(settings["min_available_memory_bytes"])
    except (KeyError, TypeError, ValueError) as exc:
        return _decision(0, f"invalid policy: {exc}", [])

    active_teams = set()
    supplied_teams = resources.get("active_team_ids", ())
    if not isinstance(supplied_teams, Sequence) or isinstance(supplied_teams, (str, bytes)):
        return _decision(0, "active team identities are unknown", [])
    for team in supplied_teams:
        if not isinstance(team, str) or not team.strip():
            return _decision(0, "active team identities are unknown", [])
        active_teams.add(team)
    active_count = resources.get("active_attempt_count")
    if active_count is None:
        # Compatibility for callers predating explicit attempt counts.
        active_count = resources.get("active_teams", len(active_teams))
    if isinstance(active_count, bool) or not isinstance(active_count, int) or active_count < 0:
        return _decision(0, "active attempt count is unknown", [])
    try:
        memory = _integer(resources.get("available_memory_bytes"))
        cpus = _integer(resources.get("cpu_count"), positive=True)
    except (TypeError, ValueError):
        return _decision(0, "host resource capacity is unknown", [])
    if memory < min_memory:
        return _decision(0, "available memory is below the configured minimum", [])

    # The founder owns subscription quota. Local scheduling is bounded only by
    # configured workers, active teams, CPU, memory, and conflicting scopes.
    slots = min(max(0, max_workers - active_count), max(0, cpus - active_count))
    reason = "capacity available"
    if slots == 0:
        reason = "worker or CPU capacity is exhausted"

    outcomes = usage_summary.get("recentOutcomes", ())
    if isinstance(outcomes, Sequence) and not isinstance(outcomes, (str, bytes)):
        completed = [
            o
            for o in outcomes
            if isinstance(o, Mapping) and o.get("status") in {"accepted", "rework"}
        ]
        failures = sum(o.get("status") == "rework" for o in completed)
        if len(completed) >= 3 and failures / len(completed) > 1 / 3:
            slots = min(slots, 1)
            if slots:
                reason = "recent rework rate is high; capped to one worker"

    selected = _select_tasks(ready_tasks, slots, active_scopes, last_team)
    return _decision(slots, reason, selected)


def _select_tasks(
    tasks: Sequence[Mapping[str, Any]],
    slots: int,
    active_scopes: Sequence[str],
    last_team: str | None,
) -> list[Mapping[str, Any]]:
    if not slots:
        return []
    busy = [scope for scope in active_scopes if isinstance(scope, str) and scope]
    best: dict[str, tuple[tuple[float, float, str], Mapping[str, Any]]] = {}
    for task in tasks:
        team = task.get("team")
        scope = _scope_paths(task.get("scope"))
        task_id = task.get("id", task.get("task_id"))
        if (
            not isinstance(team, str)
            or not team
            or not scope
            or not isinstance(task_id, str)
            or not task_id
        ):
            continue
        if any(_overlaps(path, current) for path in scope for current in busy):
            continue
        try:
            priority = _number(task.get("priority", 0))
            age = _number(task.get("age_seconds", 0))
        except (TypeError, ValueError):
            continue
        # Highest priority and oldest age first; lexical id is a stable final tie-break.
        key = (-priority, -age, task_id)
        if team not in best or key < best[team][0]:
            best[team] = (key, task)
    teams = sorted(best)
    if last_team in teams:
        idx = teams.index(last_team)
        teams = teams[idx + 1 :] + teams[: idx + 1]
    team_order = {team: index for index, team in enumerate(teams)}
    ranked = sorted(
        teams,
        key=lambda team: (
            best[team][0][0],
            best[team][0][1],
            team_order[team],
            best[team][0][2],
        ),
    )
    selected = []
    for team in ranked:
        task = best[team][1]
        paths = _scope_paths(task["scope"])
        if any(_overlaps(path, scope) for path in paths for scope in busy):
            continue
        selected.append(dict(task))
        busy.extend(paths)
        if len(selected) == slots:
            break
    return selected


def _scope_paths(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if all(isinstance(path, str) and path for path in value):
            return tuple(value)
    return ()


def _limit_buckets(value: Any) -> list[Mapping[str, Any]] | None:
    values = list(value.values()) if isinstance(value, Mapping) else value
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return None
    result: list[Mapping[str, Any]] = []
    for entry in values:
        if not isinstance(entry, Mapping):
            return None
        if any(key in entry for key in ("primary", "secondary", "rateLimitReachedType")):
            if (
                "primary" not in entry
                or "secondary" not in entry
                or "rateLimitReachedType" not in entry
            ):
                return None
            reached_type = entry["rateLimitReachedType"]
            if reached_type not in (None, "primary", "secondary"):
                return None
            for bucket_key in ("primary", "secondary"):
                bucket = entry[bucket_key]
                if bucket is None and bucket_key == "secondary":
                    continue
                if not isinstance(bucket, Mapping):
                    return None
                copied = dict(bucket)
                copied["limitReached"] = bucket_key == reached_type
                result.append(copied)
        else:
            result.append(entry)
    return result


def _measured_tokens(summary: Mapping[str, Any]) -> Any:
    if "total" in summary:
        total = summary["total"]
        if not isinstance(total, Mapping):
            return None
        return total.get("tokens", 0 if not total else None)
    tokens = summary.get("modelUsageTokens")
    if tokens is not None:
        return tokens
    model_usage = summary.get("modelUsage")
    return model_usage.get("measuredTokens") if isinstance(model_usage, Mapping) else None


def _integer(value: Any, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        raise ValueError("expected non-negative integer")
    return value


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("expected finite number")
    return float(value)


def _overlaps(left: str, right: str) -> bool:
    return (
        left == right
        or left.startswith(right.rstrip("/") + "/")
        or right.startswith(left.rstrip("/") + "/")
    )


def _decision(slots: int, reason: str, tasks: list[Mapping[str, Any]]) -> dict[str, Any]:
    return dict(slots=slots, reason=reason, selected_tasks=tasks)
