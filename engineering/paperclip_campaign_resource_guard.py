"""Small fail-closed quota preflight for finite native Paperclip campaigns.

The Board quota endpoint is intentionally read without a Paperclip bearer token. This module
never accepts credentials and only contacts the fixed trusted-local Board origin.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

POLICY_SCHEMA = "brain-campaign-resource-policy-v1"
RENEWAL_SCHEMA = "brain-campaign-resource-renewal-v1"
RECEIPT_SCHEMA = "brain-campaign-resource-receipt-v1"
BOARD_ORIGIN = "http://127.0.0.1:3310"
MAX_SCOPES = 32
MAX_ISSUES = 1000
MAX_RUNS = 80
MAX_RESERVED_RUNS = 8
MAX_AGE = timedelta(seconds=60)
MAX_POLICY_LIFETIME = timedelta(hours=8)
MAX_RENEWAL_LIFETIME = timedelta(hours=6)
MAX_RENEWAL_RUNS = 60
RENEWAL_ENV = "BRAIN_CAMPAIGN_RESOURCE_RENEWAL"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ResourceGuardError(RuntimeError):
    pass


class _RenewedPolicy(dict[str, Any]):
    """Effective policy carrying the verified renewal boundary for run accounting."""

    def __init__(self, value: dict[str, Any], issued_at_utc: str) -> None:
        super().__init__(value)
        self.renewal_issued_at_utc = issued_at_utc


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ResourceGuardError(reason)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc(value: object, reason: str) -> datetime:
    _require(type(value) is str and value.endswith("Z"), reason)
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ResourceGuardError(reason) from exc
    _require(result.tzinfo is not None and result.utcoffset() == timedelta(0), reason)
    return result


def _private_json(path_value: object, name: str) -> tuple[dict[str, Any], bytes]:
    _require(type(path_value) is str and Path(path_value).is_absolute(), f"{name}_path_invalid")
    path = Path(path_value)
    try:
        parent_info = path.parent.lstat()
        info = path.lstat()
    except OSError as exc:
        raise ResourceGuardError(f"{name}_missing") from exc
    _require(
        stat.S_ISDIR(parent_info.st_mode)
        and not stat.S_ISLNK(parent_info.st_mode)
        and parent_info.st_uid == os.getuid()
        and not stat.S_IMODE(parent_info.st_mode) & 0o077
        and stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and info.st_uid == os.getuid()
        and not stat.S_IMODE(info.st_mode) & 0o222
        and not stat.S_IMODE(info.st_mode) & 0o077,
        f"{name}_not_owner_private",
    )
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResourceGuardError(f"{name}_invalid_json") from exc
    _require(type(value) is dict, f"{name}_object_required")
    return value, raw


def validate_policy(value: object, company_id: str) -> dict[str, Any]:
    fields = {
        "schema",
        "company_id",
        "connection_id",
        "issued_at_utc",
        "deadline_utc",
        "max_model_runs",
        "min_remaining_percent",
        "task_scope_root_ids",
    }
    _require(
        type(value) in {dict, _RenewedPolicy} and set(value) == fields,
        "resource_policy_fields_invalid",
    )
    _require(
        value["schema"] == POLICY_SCHEMA
        and value["company_id"] == company_id
        and type(value["connection_id"]) is str
        and UUID.fullmatch(value["connection_id"]) is not None,
        "resource_policy_identity_invalid",
    )
    issued = _utc(value["issued_at_utc"], "resource_policy_issued_at_invalid")
    deadline = _utc(value["deadline_utc"], "resource_policy_deadline_invalid")
    _require(issued < deadline <= issued + MAX_POLICY_LIFETIME, "resource_policy_lifetime_invalid")
    _require(
        type(value["max_model_runs"]) is int and 1 <= value["max_model_runs"] <= MAX_RUNS,
        "resource_policy_run_cap_invalid",
    )
    _require(
        type(value["min_remaining_percent"]) in {int, float}
        and math.isfinite(value["min_remaining_percent"])
        and value["min_remaining_percent"] == 15,
        "resource_policy_headroom_invalid",
    )
    scopes = value["task_scope_root_ids"]
    _require(
        type(scopes) is list
        and 1 <= len(scopes) <= MAX_SCOPES
        and all(type(task_id) is str and UUID.fullmatch(task_id) for task_id in scopes)
        and len(set(scopes)) == len(scopes),
        "resource_policy_scope_roots_invalid",
    )
    return value


def load_policy_reference(reference: object, company_id: str) -> tuple[dict[str, Any], str]:
    _require(
        type(reference) is dict
        and set(reference) == {"path", "sha256"}
        and type(reference["sha256"]) is str
        and SHA256.fullmatch(reference["sha256"]) is not None,
        "resource_policy_reference_invalid",
    )
    value, raw = _private_json(reference["path"], "resource_policy")
    _require(_sha(raw) == reference["sha256"], "resource_policy_hash_mismatch")
    original = validate_policy(value, company_id)
    renewal_path = os.environ.get(RENEWAL_ENV)
    if renewal_path is None:
        return original, reference["sha256"]

    renewal, renewal_raw = _private_json(renewal_path, "resource_policy_renewal")
    _require(
        set(renewal) == {"schema", "original_policy_sha256", "new_policy"}
        and renewal.get("schema") == RENEWAL_SCHEMA
        and renewal.get("original_policy_sha256") == reference["sha256"],
        "resource_policy_renewal_binding_invalid",
    )
    effective = validate_policy(renewal["new_policy"], company_id)
    issued = _utc(effective["issued_at_utc"], "resource_policy_renewal_issued_at_invalid")
    old_deadline = _utc(original["deadline_utc"], "resource_policy_deadline_invalid")
    new_deadline = _utc(effective["deadline_utc"], "resource_policy_deadline_invalid")
    _require(
        effective["connection_id"] == original["connection_id"]
        and effective["task_scope_root_ids"] == original["task_scope_root_ids"],
        "resource_policy_renewal_scope_invalid",
    )
    _require(
        old_deadline <= issued < new_deadline <= issued + MAX_RENEWAL_LIFETIME,
        "resource_policy_renewal_lifetime_invalid",
    )
    _require(
        effective["max_model_runs"] <= MAX_RENEWAL_RUNS,
        "resource_policy_renewal_run_cap_invalid",
    )
    return _RenewedPolicy(effective, effective["issued_at_utc"]), _sha(renewal_raw)


def read_board_usage(
    company_id: str, connection_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Read quota through the local Board's trusted-local route, with no credentials."""
    _require(UUID.fullmatch(company_id) is not None, "resource_company_invalid")
    _require(UUID.fullmatch(connection_id) is not None, "resource_connection_invalid")
    url = f"{BOARD_ORIGIN}/api/companies/{company_id}/ai-connections/{connection_id}/usage"
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            raw = response.read(64 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ResourceGuardError("resource_usage_unavailable") from exc
    _require(len(raw) <= 64 * 1024, "resource_usage_response_too_large")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResourceGuardError("resource_usage_invalid_json") from exc
    _require(type(value) is dict, "resource_usage_invalid")
    validate_usage(value, connection_id, now=now)
    return value


def validate_usage(
    value: object, connection_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    _require(type(value) is dict, "resource_usage_invalid")
    usage = value
    _require(
        usage.get("connectionId") == connection_id
        and usage.get("source") == "codex_wham"
        and usage.get("provider") == "openai"
        and usage.get("method") == "subscription"
        and usage.get("status") == "ok",
        "resource_subscription_mismatch",
    )
    checked = _utc(usage.get("checkedAt"), "resource_checked_at_invalid")
    current = now or datetime.now(UTC)
    _require(
        current.tzinfo is not None and current.utcoffset() == timedelta(0), "resource_clock_invalid"
    )
    _require(checked <= current + timedelta(seconds=5), "resource_checked_at_future")
    _require(current - checked <= MAX_AGE, "resource_usage_stale")
    limits = usage.get("limits")
    _require(type(limits) is list, "resource_limits_invalid")
    primary = [
        row for row in limits if type(row) is dict and row.get("id") == "codex.primary_window"
    ]
    _require(len(primary) == 1, "resource_primary_limit_missing")
    limit = primary[0]
    remaining = limit.get("remainingPercent")
    used = limit.get("usedPercent")
    allowed = limit.get("allowed")
    _require(type(allowed) is bool and allowed, "resource_usage_not_allowed")
    _require(
        type(remaining) in {int, float} and math.isfinite(remaining) and 0 <= remaining <= 100,
        "resource_remaining_invalid",
    )
    _require(
        type(used) in {int, float}
        and math.isfinite(used)
        and 0 <= used <= 100
        and abs(float(used) + float(remaining) - 100) <= 1.0,
        "resource_limits_invalid",
    )
    _require(float(remaining) >= 15, "resource_headroom_insufficient")
    overage = usage.get("overage")
    _require(
        type(overage) is dict
        and overage.get("available") is False
        and overage.get("unlimited") is False
        and overage.get("enabled") in (None, False)
        and overage.get("used") in (None, 0, 0.0)
        and overage.get("balance") in (None, 0, 0.0),
        "resource_overage_active",
    )
    return {
        "connection_id": connection_id,
        "source": "codex_wham",
        "provider": "openai",
        "method": "subscription",
        "checked_at": checked.isoformat().replace("+00:00", "Z"),
        "used_percent": float(used),
        "remaining_percent": float(remaining),
        "allowed": True,
        "overage_available": False,
        "overage_enabled": overage.get("enabled"),
    }


def preflight(
    policy: dict[str, Any],
    company_id: str,
    phase: str,
    observed_run_ids: list[str],
    receipt_path: str | Path,
    *,
    reserve_model_runs: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fail closed on time, run-cap, and quota; persist only a small nonsecret observation."""
    policy = validate_policy(policy, company_id)
    _require(phase in {"prepare", "stage", "publish", "setup", "stop"}, "resource_phase_invalid")
    current = now or datetime.now(UTC)
    deadline = _utc(policy["deadline_utc"], "resource_policy_deadline_invalid")
    issued = _utc(policy["issued_at_utc"], "resource_policy_issued_at_invalid")
    _require(
        current.tzinfo is not None and current.utcoffset() == timedelta(0), "resource_clock_invalid"
    )
    _require(issued <= current, "resource_policy_not_yet_active")
    _require(current < deadline, "resource_policy_deadline_expired")
    _require(
        type(observed_run_ids) is list
        and all(type(run_id) is str and UUID.fullmatch(run_id) for run_id in observed_run_ids)
        and len(set(observed_run_ids)) == len(observed_run_ids),
        "resource_run_ids_invalid",
    )
    _require(
        type(reserve_model_runs) is int and 0 <= reserve_model_runs <= MAX_RESERVED_RUNS,
        "resource_run_reserve_invalid",
    )
    _require(
        len(observed_run_ids) + reserve_model_runs <= policy["max_model_runs"],
        "resource_run_cap_exceeded",
    )
    observation = read_board_usage(company_id, policy["connection_id"], now=current)
    normalized = validate_usage(observation, policy["connection_id"], now=current)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "company_id": company_id,
        "connection_id": policy["connection_id"],
        "phase": phase,
        "observed_at": current.isoformat().replace("+00:00", "Z"),
        "deadline_utc": policy["deadline_utc"],
        "policy_max_model_runs": policy["max_model_runs"],
        "observed_model_run_count": len(observed_run_ids),
        "reserved_model_runs": reserve_model_runs,
        "observed_model_run_ids": observed_run_ids,
        **normalized,
    }
    path = Path(receipt_path)
    _require(path.is_absolute(), "resource_receipt_path_invalid")
    try:
        parent_info = path.parent.lstat()
    except OSError as exc:
        raise ResourceGuardError("resource_receipt_parent_invalid") from exc
    _require(
        stat.S_ISDIR(parent_info.st_mode)
        and not stat.S_ISLNK(parent_info.st_mode)
        and parent_info.st_uid == os.getuid()
        and not stat.S_IMODE(parent_info.st_mode) & 0o077,
        "resource_receipt_parent_not_private",
    )
    data = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            info = path.lstat()
            existing = path.read_bytes()
        except OSError as exc:
            raise ResourceGuardError("resource_receipt_conflict") from exc
        _require(
            stat.S_ISREG(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and info.st_uid == os.getuid()
            and not stat.S_IMODE(info.st_mode) & 0o077
            and existing == data,
            "resource_receipt_conflict",
        )
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    return receipt


def campaign_model_run_ids(api: Any, company_id: str, policy: dict[str, Any]) -> list[str]:
    """Count unique native Codex runs in bounded descendants of immutable campaign roots."""
    policy = validate_policy(policy, company_id)
    issues: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        page = api.request("GET", f"/companies/{company_id}/issues?limit=100&offset={offset}")
        _require(
            type(page) is list and all(type(row) is dict for row in page) and len(page) <= 100,
            "resource_issue_listing_invalid",
        )
        for issue in page:
            issue_id = issue.get("id")
            _require(
                type(issue_id) is str
                and UUID.fullmatch(issue_id) is not None
                and issue.get("companyId") == company_id
                and issue_id not in issues,
                "resource_issue_identity_invalid",
            )
            issues[issue_id] = issue
        _require(len(issues) <= MAX_ISSUES, "resource_issue_listing_too_large")
        if len(page) < 100:
            break
        offset += 100

    roots = policy["task_scope_root_ids"]
    renewed_since = getattr(policy, "renewal_issued_at_utc", None)
    renewed_since_dt = (
        _utc(renewed_since, "resource_policy_renewal_issued_at_invalid")
        if renewed_since is not None
        else None
    )
    _require(all(root_id in issues for root_id in roots), "resource_scope_root_missing")
    scope = set(roots)
    while True:
        children = {
            issue_id
            for issue_id, issue in issues.items()
            if issue.get("parentId") in scope and issue_id not in scope
        }
        if not children:
            break
        scope.update(children)
        _require(len(scope) <= MAX_ISSUES, "resource_scope_too_large")

    run_ids: set[str] = set()
    for issue_id in sorted(scope):
        rows = api.request("GET", f"/issues/{issue_id}/runs")
        _require(
            type(rows) is list and len(rows) <= 100 and all(type(row) is dict for row in rows),
            "resource_run_listing_invalid",
        )
        for row in rows:
            run_id = row.get("runId") or row.get("id")
            _require(
                type(run_id) is str and UUID.fullmatch(run_id) is not None,
                "resource_run_identity_invalid",
            )
            native = api.request("GET", f"/heartbeat-runs/{run_id}")
            profile = native.get("runnerProfileJson", {}) if type(native) is dict else {}
            execution = profile.get("nativeExecutionInput", {}) if type(profile) is dict else {}
            provider = execution.get("provider", {}) if type(execution) is dict else {}
            usage = native.get("usageJson", {}) if type(native) is dict else {}
            _require(type(native) is dict and native.get("id") == run_id, "resource_run_missing")
            if native.get("runtimeMode") == "legacy":
                continue
            context = native.get("contextSnapshot")
            context_issue_id = context.get("issueId") if type(context) is dict else None
            if context_issue_id is not None and context_issue_id != issue_id:
                # Activity-linked run history can surface on another issue; only exact context
                # executions belong to this task's campaign scope.
                continue
            _require(
                native.get("runtimeMode") == "native"
                and native.get("companyId") == company_id
                and native.get("issueId") == issue_id
                and native.get("nativeIssueId") in {None, issue_id}
                and context_issue_id == issue_id
                and UUID.fullmatch(str(native.get("agentId", ""))) is not None
                and (row.get("agentId") is None or row.get("agentId") == native.get("agentId"))
                and provider.get("kind") == "codex"
                and usage.get("billingType", usage.get("billing_type"))
                in {"subscription", "subscription_included"},
                "resource_native_run_subscription_invalid",
            )
            if renewed_since_dt is not None:
                created_at = _utc(native.get("createdAt"), "resource_run_created_at_invalid")
                if created_at < renewed_since_dt:
                    continue
            run_ids.add(run_id)
            _require(len(run_ids) <= MAX_RUNS, "resource_run_cap_exceeded")
    return sorted(run_ids)
