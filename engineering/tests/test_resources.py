from engineering.resources import choose_capacity


def inputs():
    return (
        {"total": {"tokens": 8_000_000}, "reservations": [], "unresolved": []},
        {"available_memory_bytes": 2_000_000_000, "cpu_count": 8, "active_tokens": 0},
    )


def test_manual_quota_uses_local_capacity_and_ignores_historical_totals():
    decision = choose_capacity(*inputs())
    assert decision["slots"] == 2
    assert decision["reason"] == "capacity available"


def test_only_live_active_teams_consume_worker_capacity():
    usage, resources = inputs()
    resources["active_team_ids"] = ["alpha"]
    usage["reservations"] = [{"team": "alpha"}, {"team": "beta"}]
    assert choose_capacity(usage, resources)["slots"] == 1
    usage["reservations"] = [{"team": "alpha"}]
    assert choose_capacity(usage, resources)["slots"] == 1


def test_concurrency_counts_attempts_even_when_the_team_is_the_same():
    usage, resources = inputs()
    resources["active_team_ids"] = ["alpha"]
    resources["active_attempt_count"] = 2
    assert choose_capacity(usage, resources)["slots"] == 0


def test_unknown_usage_evidence_does_not_gate_manual_quota_or_task_outcomes():
    usage, resources = inputs()
    usage["reservations"] = [{"dispatch_id": "x"}]
    assert choose_capacity(usage, resources)["slots"] == 2
    usage["reservations"] = []
    usage["unresolved"] = [{"reason": "unknown"}]
    assert choose_capacity(usage, resources)["slots"] == 2


def test_quota_snapshot_is_not_required_for_local_capacity():
    usage, resources = inputs()
    decision = choose_capacity(usage, resources)
    assert decision["slots"] == 2


def test_capacity_uses_only_local_resource_and_concurrency_evidence():
    usage, resources = inputs()
    assert choose_capacity(usage, resources)["slots"] == 2


def test_invalid_active_count_and_low_memory_fail_closed():
    usage, resources = inputs()
    resources["active_teams"] = True
    assert choose_capacity(usage, resources)["slots"] == 0
    resources["active_teams"] = 0
    resources["available_memory_bytes"] = 1_073_741_823
    result = choose_capacity(usage, resources)
    assert result["slots"] == 0
    assert "memory" in result["reason"]


def test_tasks_respect_scope_prefix_conflicts_and_one_per_team():
    usage, resources = inputs()
    tasks = [
        {"id": "a1", "team": "alpha", "scope": "src/model", "priority": 9, "age_seconds": 5},
        {"id": "a2", "team": "alpha", "scope": "docs", "priority": 8, "age_seconds": 90},
        {"id": "b1", "team": "beta", "scope": "src/other", "priority": 7, "age_seconds": 40},
        {"id": "c1", "team": "gamma", "scope": "tests", "priority": 6, "age_seconds": 10},
    ]
    result = choose_capacity(usage, resources, active_scopes=["src"], ready_tasks=tasks)
    assert [task["id"] for task in result["selected_tasks"]] == ["a2", "c1"]
    assert len({task["team"] for task in result["selected_tasks"]}) == len(result["selected_tasks"])
    overlapping = [
        {"id": "root", "team": "alpha", "scope": "repo", "priority": 9},
        {"id": "child", "team": "beta", "scope": "repo/sub", "priority": 8},
        {"id": "free", "team": "gamma", "scope": "docs", "priority": 1},
    ]
    picked = choose_capacity(usage, resources, ready_tasks=overlapping)["selected_tasks"]
    assert [task["id"] for task in picked] == ["root", "free"]


def test_file_list_scopes_conflict_on_any_exact_or_prefix_path():
    usage, resources = inputs()
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
        usage, resources, active_scopes=["tools/agent_context.py"], ready_tasks=tasks
    )
    assert [task["id"] for task in result["selected_tasks"]] == ["free", "overlap"]


def test_priority_age_and_team_rotation_are_deterministic():
    usage, resources = inputs()
    tasks = [
        {"id": "a", "team": "alpha", "scope": "a", "priority": 5, "age_seconds": 10},
        {"id": "b", "team": "beta", "scope": "b", "priority": 5, "age_seconds": 10},
        {"id": "c", "team": "gamma", "scope": "c", "priority": 5, "age_seconds": 3},
    ]
    first = choose_capacity(usage, resources, ready_tasks=tasks)
    rotated = choose_capacity(usage, resources, ready_tasks=tasks, last_team="alpha")
    assert [task["team"] for task in first["selected_tasks"]] == ["alpha", "beta"]
    assert [task["team"] for task in rotated["selected_tasks"]] == ["beta", "alpha"]


def test_recent_rework_caps_capacity_only_with_enough_completed_outcomes():
    usage, resources = inputs()
    usage["recentOutcomes"] = [{"status": "rework"}, {"status": "rework"}, {"status": "accepted"}]
    result = choose_capacity(usage, resources)
    assert result["slots"] == 1
    assert "rework" in result["reason"]
