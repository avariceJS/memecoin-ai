from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from engineering import paperclip_campaign_resource_guard as guard


COMPANY = "00000000-0000-4000-8000-000000000001"
CONNECTION = "00000000-0000-4000-8000-000000000002"
ROOT = "00000000-0000-4000-8000-000000000003"
AGENT = "00000000-0000-4000-8000-000000000004"
CONTROL_SCRIPT = Path(
    "/Users/root1/Developer/brain-paperclip-lab/state/scaling-20261030/control-candidate/finalize-campaign.py"
)


def private_control_module():
    spec = importlib.util.spec_from_file_location("scaling_campaign_control_v2", CONTROL_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def test_hash_bound_control_can_upgrade_existing_v1_manifest_to_v2(tmp_path, monkeypatch):
    now = datetime(2026, 10, 30, 8, tzinfo=UTC)
    original = policy_v1(now)
    upgraded = policy_v2(now + timedelta(hours=8))
    upgraded["deadline_utc"] = (now + timedelta(hours=12)).isoformat().replace("+00:00", "Z")
    policy_path = tmp_path / "original-policy.json"
    policy_raw = json.dumps(original, sort_keys=True, separators=(",", ":")).encode()
    policy_path.write_bytes(policy_raw)
    policy_path.chmod(0o400)
    ref = {"path": str(policy_path), "sha256": hashlib.sha256(policy_raw).hexdigest()}

    renewal = {
        "schema": guard.RENEWAL_SCHEMA_V2,
        "original_policy_sha256": ref["sha256"],
        "new_policy": upgraded,
    }
    renewal_path = tmp_path / "renewal.json"
    renewal_raw = json.dumps(renewal, sort_keys=True, separators=(",", ":")).encode()
    renewal_path.write_bytes(renewal_raw)
    renewal_path.chmod(0o400)
    monkeypatch.setenv(guard.RENEWAL_ENV, str(renewal_path))

    effective, renewal_sha = guard.load_policy_reference(
        ref, COMPANY, allow_v1_to_v2=True
    )
    assert effective["schema"] == guard.POLICY_SCHEMA_V2
    assert effective["company_id"] == original["company_id"]
    assert effective["connection_id"] == original["connection_id"]
    assert effective["task_scope_root_ids"] == original["task_scope_root_ids"]
    assert renewal_sha == hashlib.sha256(renewal_raw).hexdigest()


def test_v1_to_v1_renewal_keeps_legacy_run_limit(tmp_path, monkeypatch):
    now = datetime(2026, 10, 30, 8, tzinfo=UTC)
    original = policy_v1(now)
    renewed = policy_v1(now + timedelta(hours=8))
    renewed["deadline_utc"] = (now + timedelta(hours=14)).isoformat().replace("+00:00", "Z")
    renewed["max_model_runs"] = 61
    policy_path = tmp_path / "original-policy.json"
    policy_raw = json.dumps(original, sort_keys=True, separators=(",", ":")).encode()
    policy_path.write_bytes(policy_raw)
    policy_path.chmod(0o400)
    ref = {"path": str(policy_path), "sha256": hashlib.sha256(policy_raw).hexdigest()}
    renewal = {
        "schema": guard.RENEWAL_SCHEMA,
        "original_policy_sha256": ref["sha256"],
        "new_policy": renewed,
    }
    renewal_path = tmp_path / "renewal.json"
    renewal_path.write_text(json.dumps(renewal, sort_keys=True, separators=(",", ":")))
    renewal_path.chmod(0o400)
    monkeypatch.setenv(guard.RENEWAL_ENV, str(renewal_path))
    with pytest.raises(guard.ResourceGuardError, match="resource_policy_renewal_run_cap_invalid"):
        guard.load_policy_reference(ref, COMPANY)


def test_control_rejects_directive_hash_mismatch_and_accepts_current_bytes():
    control = private_control_module()
    directive = control.SOURCE_DIRECTIVE_PATH
    actual = hashlib.sha256(directive.read_bytes()).hexdigest()
    assert control.verify_founder_directive(directive, actual)
    with pytest.raises(control.FinalizeError, match="founder_directive_hash_mismatch"):
        control.verify_founder_directive(directive, "0" * 64)


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
