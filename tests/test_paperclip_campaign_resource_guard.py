from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from engineering import paperclip_campaign_resource_guard as guard

COMPANY = "10000000-0000-4000-8000-000000000001"
CONNECTION = "20000000-0000-4000-8000-000000000002"
ROOT = "30000000-0000-4000-8000-000000000003"
CHILD = "30000000-0000-4000-8000-000000000004"
UNRELATED = "30000000-0000-4000-8000-000000000005"
RUN = "40000000-0000-4000-8000-000000000006"
NOW = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)


def policy(**overrides):
    value = {
        "schema": guard.POLICY_SCHEMA,
        "company_id": COMPANY,
        "connection_id": CONNECTION,
        "issued_at_utc": "2026-10-28T09:00:00Z",
        "deadline_utc": "2026-10-28T17:00:00Z",
        "max_model_runs": 80,
        "min_remaining_percent": 15,
        "task_scope_root_ids": [ROOT],
    }
    value.update(overrides)
    return value


def usage(**overrides):
    value = {
        "connectionId": CONNECTION,
        "grantId": "not-secret",
        "provider": "openai",
        "method": "subscription",
        "source": "codex_wham",
        "checkedAt": NOW.isoformat().replace("+00:00", "Z"),
        "status": "ok",
        "limits": [
            {
                "id": "codex.primary_window",
                "allowed": True,
                "usedPercent": 20,
                "remainingPercent": 80,
            },
            {"id": "spend_control", "allowed": None},
        ],
        "overage": {
            "enabled": None,
            "available": False,
            "unlimited": False,
            "used": None,
            "balance": 0,
        },
    }
    value.update(overrides)
    return value


def test_preflight_saves_only_small_private_nonsecret_observation(tmp_path: Path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(guard, "read_board_usage", lambda *_args, **_kwargs: usage())
    path = tmp_path / "receipt.json"
    receipt = guard.preflight(policy(), COMPANY, "setup", [], path, now=NOW)
    saved = json.loads(path.read_text())
    assert receipt == saved
    assert path.stat().st_mode & 0o777 == 0o600
    assert "grantId" not in saved and "token" not in saved
    assert saved["remaining_percent"] == 80
    assert saved["observed_model_run_count"] == 0


@pytest.mark.parametrize(
    ("policy_overrides", "usage_overrides", "reason"),
    [
        (
            {"issued_at_utc": "2026-10-28T08:00:00Z", "deadline_utc": "2026-10-28T09:00:00Z"},
            {},
            "resource_policy_deadline_expired",
        ),
        ({"max_model_runs": 1}, {}, "resource_run_cap_exceeded"),
        ({}, {"checkedAt": "2026-10-28T09:58:00Z"}, "resource_usage_stale"),
        ({}, {"method": "api_key"}, "resource_subscription_mismatch"),
        (
            {},
            {
                "limits": [
                    {
                        "id": "codex.primary_window",
                        "allowed": True,
                        "usedPercent": 86,
                        "remainingPercent": 14,
                    }
                ]
            },
            "resource_headroom_insufficient",
        ),
        ({}, {"overage": {"available": True, "unlimited": False}}, "resource_overage_active"),
        ({}, {"limits": []}, "resource_primary_limit_missing"),
    ],
)
def test_preflight_fails_closed_on_bad_policy_or_telemetry(
    tmp_path: Path, monkeypatch, policy_overrides, usage_overrides, reason
):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(
        guard, "read_board_usage", lambda *_args, **_kwargs: usage(**usage_overrides)
    )
    runs = (
        [RUN, "40000000-0000-4000-8000-000000000007"]
        if reason == "resource_run_cap_exceeded"
        else []
    )
    with pytest.raises(guard.ResourceGuardError, match=reason):
        guard.preflight(
            policy(**policy_overrides),
            COMPANY,
            "stage",
            runs,
            tmp_path / "receipt.json",
            now=NOW,
        )
    assert not (tmp_path / "receipt.json").exists()


def test_policy_reference_is_hash_pinned_and_private(tmp_path: Path):
    tmp_path.chmod(0o700)
    path = tmp_path / "policy.json"
    raw = (json.dumps(policy(), sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(raw)
    path.chmod(0o400)
    reference = {"path": str(path), "sha256": guard._sha(raw)}
    loaded, sha = guard.load_policy_reference(reference, COMPANY)
    assert loaded == policy() and sha == reference["sha256"]
    with pytest.raises(guard.ResourceGuardError, match="resource_policy_hash_mismatch"):
        guard.load_policy_reference({**reference, "sha256": "0" * 64}, COMPANY)


def test_scope_counts_only_unique_native_codex_runs_in_root_descendants():
    class API:
        def request(self, method: str, route: str):
            agent = "50000000-0000-4000-8000-000000000008"
            if route.startswith("/companies/"):
                return [
                    {"id": ROOT, "companyId": COMPANY, "parentId": None, "assigneeAgentId": agent},
                    {"id": CHILD, "companyId": COMPANY, "parentId": ROOT, "assigneeAgentId": agent},
                    {
                        "id": UNRELATED,
                        "companyId": COMPANY,
                        "parentId": None,
                        "assigneeAgentId": agent,
                    },
                ]
            if route == f"/issues/{ROOT}/runs":
                return [{"id": RUN, "contextIssueId": ROOT}]
            if route == f"/issues/{CHILD}/runs" or route == f"/issues/{UNRELATED}/runs":
                return []
            if route == f"/heartbeat-runs/{RUN}":
                return {
                    "id": RUN,
                    "runtimeMode": "native",
                    "companyId": COMPANY,
                    "issueId": ROOT,
                    "nativeIssueId": ROOT,
                    "agentId": agent,
                    "contextSnapshot": {"issueId": ROOT},
                    "runnerProfileJson": {"nativeExecutionInput": {"provider": {"kind": "codex"}}},
                    "usageJson": {"billingType": "subscription"},
                }
            raise AssertionError(route)

    assert guard.campaign_model_run_ids(API(), COMPANY, policy()) == [RUN]


def test_board_usage_request_has_no_bearer_credential(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            return json.dumps(usage()).encode()

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(guard.urllib.request, "urlopen", fake_urlopen)
    result = guard.validate_usage(
        guard.read_board_usage(COMPANY, CONNECTION, now=NOW), CONNECTION, now=NOW
    )
    assert result["remaining_percent"] == 80
    assert captured["timeout"] == 5
    assert captured["request"].full_url.startswith(guard.BOARD_ORIGIN)
    assert captured["request"].get_header("Authorization") is None
