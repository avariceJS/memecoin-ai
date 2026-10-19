from __future__ import annotations

from engineering.resources import choose_capacity


def inputs():
    return (
        {
            "ordinaryUsageAllowed": True,
            "rateLimitsByLimitId": {
                "short": {"usedPercent": 50, "limitReached": False},
                "weekly": {"usedPercent": 40, "limitReached": False},
            },
        },
        {"modelUsageTokens": 100_000},
        {"available_memory_bytes": 2_000_000_000, "cpu_count": 8, "active_tokens": 0},
    )


def test_defaults_reserve_two_workers_within_token_budget():
    decision = choose_capacity(*inputs())
    assert decision["slots"] == 2
    assert decision["reserved_tokens"] == 100_000
    assert decision["reason"] == "capacity available"


def test_active_reservations_count_against_budget():
    rate, usage, resources = inputs()
    usage["modelUsageTokens"] = 20_000
    small_budget = {
        "total_token_budget": 180_000,
        "reserve_tokens": 20_000,
        "per_worker_tokens": 20_000,
    }
    assert (
        choose_capacity(rate, usage, resources, small_budget, active_tokens=120_000)["slots"] == 1
    )
    assert (
        choose_capacity(rate, usage, resources, small_budget, active_tokens=130_000)["slots"] == 0
    )


def test_store_usage_and_nested_codex_rate_limit_shape():
    rate = {
        "ordinaryUsageAllowed": True,
        "rateLimitsByLimitId": {
            "codex": {
                "primary": {
                    "usedPercent": 54,
                    "windowDurationMins": 10_080,
                    "resetsAt": "2030-01-01T00:00:00Z",
                },
                "secondary": None,
                "rateLimitReachedType": None,
            }
        },
    }
    resources = {"available_memory_bytes": 2_000_000_000, "cpu_count": 8, "active_tokens": 0}
    assert choose_capacity(rate, {"total": {}}, resources)["slots"] == 2
    rate["rateLimitsByLimitId"]["codex"]["secondary"] = {"usedPercent": 88}
    assert choose_capacity(rate, {"total": {"tokens": 640_000}}, resources)["slots"] == 1
    rate["rateLimitsByLimitId"]["codex"]["rateLimitReachedType"] = "secondary"
    assert choose_capacity(rate, {"total": {"tokens": 1}}, resources)["slots"] == 0


def test_store_usage_and_missing_nested_rate_bucket_fail_closed():
    rate = {
        "ordinaryUsageAllowed": True,
        "rateLimitsByLimitId": {
            "codex": {"primary": {"usedPercent": 10}, "rateLimitReachedType": None}
        },
    }
    resources = {"available_memory_bytes": 2_000_000_000, "cpu_count": 8, "active_tokens": 0}
    result = choose_capacity(rate, {"total": {}}, resources)
    assert result["slots"] == 0
    assert "unknown" in result["reason"]
    rate["rateLimitsByLimitId"]["codex"]["secondary"] = None
    assert choose_capacity(rate, {"total": {"tokens": 690_000}}, resources)["slots"] == 0


def test_unknown_quota_or_ordinary_usage_fails_closed():
    rate, usage, resources = inputs()
    del rate["ordinaryUsageAllowed"]
    assert choose_capacity(rate, usage, resources)["slots"] == 0
    rate["ordinaryUsageAllowed"] = True
    del rate["rateLimitsByLimitId"]
    decision = choose_capacity(rate, usage, resources)
    assert decision["slots"] == 0
    assert "unknown" in decision["reason"]


def test_every_quota_bucket_is_checked_and_near_ceiling_caps_one():
    rate, usage, resources = inputs()
    rate["rateLimitsByLimitId"]["weekly"]["usedPercent"] = 89
    assert choose_capacity(rate, usage, resources)["slots"] == 1
    rate["rateLimitsByLimitId"]["short"]["limitReached"] = True
    assert choose_capacity(rate, usage, resources)["slots"] == 0
    rate["rateLimitsByLimitId"]["short"]["limitReached"] = False
    rate["rateLimitsByLimitId"]["short"]["usedPercent"] = 90
    assert choose_capacity(rate, usage, resources)["slots"] == 0


def test_invalid_numeric_usage_and_low_memory_fail_closed():
    rate, usage, resources = inputs()
    usage["modelUsageTokens"] = float("nan")
    assert choose_capacity(rate, usage, resources)["slots"] == 0
    usage["modelUsageTokens"] = 1
    resources["available_memory_bytes"] = 1_073_741_823
    decision = choose_capacity(rate, usage, resources)
    assert decision["slots"] == 0
    assert "memory" in decision["reason"]


def test_tasks_respect_scope_prefix_conflicts_and_one_per_team():
    rate, usage, resources = inputs()
    tasks = [
        {"id": "a1", "team": "alpha", "scope": "src/model", "priority": 9, "age_seconds": 5},
        {"id": "a2", "team": "alpha", "scope": "docs", "priority": 8, "age_seconds": 90},
        {"id": "b1", "team": "beta", "scope": "src/other", "priority": 7, "age_seconds": 40},
        {"id": "c1", "team": "gamma", "scope": "tests", "priority": 6, "age_seconds": 10},
    ]
    result = choose_capacity(
        rate,
        usage,
        resources,
        active_scopes=["src"],
        ready_tasks=tasks,
    )
    assert [task["id"] for task in result["selected_tasks"]] == ["a2", "c1"]
    assert len({task["team"] for task in result["selected_tasks"]}) == len(result["selected_tasks"])
    overlapping = [
        {"id": "root", "team": "alpha", "scope": "repo", "priority": 9},
        {"id": "child", "team": "beta", "scope": "repo/sub", "priority": 8},
        {"id": "free", "team": "gamma", "scope": "docs", "priority": 1},
    ]
    picked = choose_capacity(rate, usage, resources, ready_tasks=overlapping)["selected_tasks"]
    assert [task["id"] for task in picked] == ["root", "free"]


def test_file_list_scopes_conflict_on_any_exact_or_prefix_path():
    rate, usage, resources = inputs()
    tasks = [
        {
            "id": "blocked",
            "team": "alpha",
            "scope": ["tools/agent_context.py", "tests/test_agent_context.py"],
            "priority": 10,
        },
        {"id": "free", "team": "beta", "scope": ["engineering/resources.py"], "priority": 5},
        {"id": "overlap", "team": "gamma", "scope": ["tests"], "priority": 1},
    ]
    result = choose_capacity(
        rate,
        usage,
        resources,
        active_scopes=["tools/agent_context.py"],
        ready_tasks=tasks,
    )
    assert [task["id"] for task in result["selected_tasks"]] == ["free", "overlap"]


def test_task_priority_age_and_team_rotation_are_deterministic():
    rate, usage, resources = inputs()
    tasks = [
        {"id": "a", "team": "alpha", "scope": "a", "priority": 5, "age_seconds": 10},
        {"id": "b", "team": "beta", "scope": "b", "priority": 5, "age_seconds": 10},
        {"id": "c", "team": "gamma", "scope": "c", "priority": 5, "age_seconds": 3},
    ]
    first = choose_capacity(rate, usage, resources, ready_tasks=tasks)
    rotated = choose_capacity(rate, usage, resources, ready_tasks=tasks, last_team="alpha")
    assert [task["team"] for task in first["selected_tasks"]] == ["alpha", "beta"]
    assert [task["team"] for task in rotated["selected_tasks"]] == ["beta", "alpha"]


def test_recent_rework_caps_capacity_only_with_enough_completed_outcomes():
    rate, usage, resources = inputs()
    usage["recentOutcomes"] = [
        {"status": "rework"},
        {"status": "rework"},
        {"status": "accepted"},
    ]
    result = choose_capacity(rate, usage, resources)
    assert result["slots"] == 1
    assert "rework" in result["reason"]
