from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engineering import paperclip_campaign_resource_guard as guard


COMPANY = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
ROOT = "00000000-0000-4000-8000-000000000003"
AGENT = "00000000-0000-4000-8000-000000000004"


def policy_v1(now: datetime) -> dict:
    return {
        "schema": guard.POLICY_SCHEMA,
        "company_id": COMPANY,
        "connection_id": CONNECTION,
        "issued_at_utc": now.isoformat().replace("+00:00", "Z"),
        "deadline_utc": (now + timedelta(hours=8)).isoformat().replace("+00:00", "Z"),
        "max_model_runs": 80,
        "min_remaining_percent": 15,
        "task_scope_root_ids": [ROOT],
    }


def policy_v2(now: datetime, *, max_model_runs=None, remaining=8) -> dict:
    return {
        "schema": guard.POLICY_SCHEMA_V2,
        "company_id": COMPANY,
        "connection_id": CONNECTION,
        "issued_at_utc": now.isoformat().replace("+00:00", "Z"),
        "deadline_utc": (now + timedelta(hours=12)).isoformat().replace("+00:00", "Z"),
        "max_model_runs": max_model_runs,
        "min_remaining_percent": remaining,
        "task_scope_root_ids": [ROOT],
    }


def usage(now: datetime, remaining: float) -> dict:
    return {
        "connectionId": CONNECTION,
        "source": "codex_wham",
        "provider": "openai",
        "method": "subscription",
        "status": "ok",
        "checkedAt": now.isoformat().replace("+00:00", "Z"),
        "limits": [
            {
                "id": "codex.primary_window",
                "allowed": True,
                "remainingPercent": remaining,
                "usedPercent": 100 - remaining,
            }
        ],
        "overage": {"available": False, "unlimited": False, "enabled": False, "used": 0, "balance": 0},
    }


def test_v1_policy_bounds_remain_unchanged():
    now = datetime(2026, 10, 30, 8, tzinfo=UTC)
    valid = policy_v1(now)
    assert guard.validate_policy(valid, COMPANY) == valid

    too_long = dict(valid, deadline_utc=(now + timedelta(hours=12)).isoformat().replace("+00:00", "Z"))
    with pytest.raises(guard.ResourceGuardError, match="resource_policy_lifetime_invalid"):
        guard.validate_policy(too_long, COMPANY)
    no_cap = dict(valid, max_model_runs=None)
    with pytest.raises(guard.ResourceGuardError, match="resource_policy_run_cap_invalid"):
        guard.validate_policy(no_cap, COMPANY)
    guard.validate_usage(usage(now, 15), CONNECTION, now=now)
    with pytest.raises(guard.ResourceGuardError, match="resource_headroom_insufficient"):
        guard.validate_usage(usage(now, 14), CONNECTION, now=now)


def test_v2_allows_twelve_hours_null_cap_and_eight_percent_floor(tmp_path, monkeypatch):
    now = datetime(2026, 10, 30, 8, tzinfo=UTC)
    policy = policy_v2(now)
    assert guard.validate_policy(policy, COMPANY) == policy
    run_ids = [str(uuid.uuid4()) for _ in range(120)]
    monkeypatch.setattr(guard, "read_board_usage", lambda *_a, **_k: usage(now, 8))
    receipt = guard.preflight(
        policy, COMPANY, "prepare", run_ids, tmp_path / "v2-receipt.json", now=now + timedelta(minutes=1)
    )
    assert receipt["schema"] == guard.RECEIPT_SCHEMA_V2
    assert receipt["policy_max_model_runs"] is None
    assert receipt["observed_model_run_count"] == 120


def test_v2_quota_floor_accepts_eight_and_denies_seven():
    now = datetime(2026, 10, 30, 8, tzinfo=UTC)
    guard.validate_usage(usage(now, 8), CONNECTION, now=now, minimum_remaining_percent=8)
    with pytest.raises(guard.ResourceGuardError, match="resource_headroom_insufficient"):
        guard.validate_usage(usage(now, 7), CONNECTION, now=now, minimum_remaining_percent=8)


def test_null_native_usage_is_counted_as_unknown_without_crashing():
    run_id = "00000000-0000-4000-8000-000000000005"

    class API:
        def request(self, method, path):
            if path == f"/companies/{COMPANY}/issues?limit=100&offset=0":
                return [{"id": ROOT, "companyId": COMPANY, "parentId": None}]
            if path == f"/issues/{ROOT}/runs":
                return [{"runId": run_id, "agentId": AGENT}]
            if path == f"/heartbeat-runs/{run_id}":
                return {
                    "id": run_id,
                    "runtimeMode": "native",
                    "companyId": COMPANY,
                    "issueId": ROOT,
                    "nativeIssueId": ROOT,
                    "agentId": AGENT,
                    "status": "running",
                    "contextSnapshot": {"issueId": ROOT},
                    "runnerProfileJson": {"nativeExecutionInput": {"provider": {"kind": "codex"}}},
                    "usageJson": None,
                }
            raise AssertionError(path)

    ids = guard.campaign_model_run_ids(API(), COMPANY, policy_v2(datetime.now(UTC)))
    assert ids == [run_id]
