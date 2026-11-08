#!/usr/bin/env python3
"""One-shot campaign evidence capture and trusted stop.

Root-session text and complete Paperclip heartbeat records are never copied to models. This
helper uses the existing delivery and company-handoff clients, writes hash-bound private
checkpoints, and does not run a watcher or retry loop. Consolidated from the final
reviewed scaling-campaign candidate; importing it never invokes a runtime action.
Historical policies and task identities remain bound and do not authorize a new campaign.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ORG_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ORG_ROOT))
from engineering import paperclip_campaign_resource_guard as resource_guard  # noqa: E402
from engineering import paperclip_canonical_publish as publisher  # noqa: E402
from engineering import paperclip_company_handoff as company_handoff  # noqa: E402
from engineering import paperclip_native_delivery as delivery  # noqa: E402

RUNTIME_ROOT = (
    Path(os.environ.get("BRAIN_RUNTIME_ROOT", str(Path.home() / "Developer" / "memecoin-runtime")))
    .expanduser()
    .resolve()
)
STATE = RUNTIME_ROOT / "state" / "scaling-20261030"
FROZEN_DIRECTIVE_PATH = STATE / "founder-directive.txt"
SOURCE_DIRECTIVE_PATH = (
    Path(os.environ.get("BRAIN_FOUNDER_DIRECTIVE", str(FROZEN_DIRECTIVE_PATH)))
    .expanduser()
    .resolve()
)
POLICY_SCHEMA = "brain-campaign-finalization-policy-v1"
EVIDENCE_SCHEMA = "brain-campaign-final-evidence-v1"
QA_SCHEMA = "brain-campaign-final-acceptance-v1"
CONTROL_SCHEMA = "brain-campaign-process-control-v2"
AUTHORITY_SCHEMA = "brain-campaign-control-authority-v2"
COMPLETION_ISSUE_ID = "1001cb5d-a6be-4f58-977c-01495348caf7"
SETUP_SENTINEL_ISSUE_ID = "24ee8aca-24bd-479d-85af-4167fc00dfe3"
MAX_POLICY_BYTES = 512 * 1024
MAX_DIRECTIVE_BYTES = 128 * 1024
MAX_SESSION_BYTES = 128 * 1024 * 1024
MAX_RUNS = 100
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UUID = delivery.UUID
ACTIVE = {"queued", "running", "scheduled_retry"}
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "timed_out"}


class FinalizeError(RuntimeError):
    pass


def require(ok: bool, reason: str) -> None:
    if not ok:
        raise FinalizeError(reason)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()


def utc(value: object, reason: str) -> datetime:
    require(type(value) is str and value.endswith("Z"), reason)
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise FinalizeError(reason) from exc
    require(result.tzinfo is not None and result.utcoffset() == timedelta(0), reason)
    return result


def read_private(path: Path, label: str, limit: int, *, readonly: bool = False) -> bytes:
    require(path.is_absolute(), f"{label}_path_invalid")
    delivery.safe_dir(str(path.parent), f"{label}_parent")
    try:
        fd = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
    except OSError as exc:
        raise FinalizeError(f"{label}_read_failed") from exc
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and not stat.S_IMODE(info.st_mode) & 0o077
            and (not readonly or not stat.S_IMODE(info.st_mode) & 0o222)
            and info.st_size <= limit,
            f"{label}_not_private_or_too_large",
        )
        data = os.read(fd, limit + 1)
        require(len(data) <= limit, f"{label}_too_large")
        return data
    finally:
        os.close(fd)


def read_founder_directive(path: Path) -> bytes:
    """Read the current source directive or its owner-private frozen campaign copy."""
    require(
        path.is_absolute() and path in {SOURCE_DIRECTIVE_PATH, FROZEN_DIRECTIVE_PATH},
        "founder_directive_path_invalid",
    )
    frozen = path == FROZEN_DIRECTIVE_PATH
    delivery.safe_dir(str(path.parent), "founder_directive_parent", private=frozen)
    try:
        fd = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
    except OSError as exc:
        raise FinalizeError("founder_directive_read_failed") from exc
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and info.st_size <= MAX_DIRECTIVE_BYTES
            and (
                not frozen
                or (
                    not stat.S_IMODE(info.st_mode) & 0o077
                    and not stat.S_IMODE(info.st_mode) & 0o222
                )
            ),
            "founder_directive_not_readable_or_too_large",
        )
        data = os.read(fd, MAX_DIRECTIVE_BYTES + 1)
        require(len(data) <= MAX_DIRECTIVE_BYTES, "founder_directive_too_large")
        return data
    finally:
        os.close(fd)


def verify_founder_directive(path: Path, expected_sha256: object) -> bytes:
    require(
        type(expected_sha256) is str and SHA256.fullmatch(expected_sha256) is not None,
        "founder_directive_hash_invalid",
    )
    data = read_founder_directive(path)
    require(digest(data) == expected_sha256, "founder_directive_hash_mismatch")
    return data


def read_json(
    path: Path, label: str, limit: int = MAX_POLICY_BYTES
) -> tuple[dict[str, Any], bytes]:
    raw = read_private(path, label, limit)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalizeError(f"{label}_invalid_json") from exc
    require(type(value) is dict, f"{label}_not_object")
    return value, raw


def write_once(path: Path, data: bytes, mode: int = 0o400) -> str:
    delivery.safe_dir(str(path.parent), "checkpoint_parent", private=True)
    try:
        fd = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode
        )
    except FileExistsError:
        require(
            read_private(path, "checkpoint", max(1, len(data))) == data,
            "immutable_checkpoint_conflict",
        )
    else:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, mode)
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    return digest(data)


def validate_policy(p: object) -> dict[str, Any]:
    fields = {
        "schema",
        "company_id",
        "resource_policy_ref",
        "process_agent_id",
        "stage_workspace_id",
        "stage_workspace_path",
        "campaign_root_ids",
        "root_session",
        "source",
        "canonical",
        "campaign_root",
        "private_evidence_root",
        "owner_desk",
        "outcomes",
        "final_evidence_task_id",
        "final_qa_task_id",
        "final_qa_agent_id",
        "final_qa_project_id",
        "final_qa_workspace_id",
        "final_qa_workspace_path",
        "astra_conclusion_task_id",
        "astra_agent_id",
        "astra_workspace_id",
        "astra_workspace_path",
        "stop_task_id",
    }
    require(
        type(p) is dict and set(p) == fields and p.get("schema") == POLICY_SCHEMA,
        "finalization_policy_fields_invalid",
    )
    for key in (
        "company_id",
        "process_agent_id",
        "stage_workspace_id",
        "final_evidence_task_id",
        "final_qa_task_id",
        "final_qa_agent_id",
        "final_qa_project_id",
        "final_qa_workspace_id",
        "astra_conclusion_task_id",
        "astra_agent_id",
        "astra_workspace_id",
        "stop_task_id",
    ):
        require(type(p[key]) is str and UUID.fullmatch(p[key]), f"policy_{key}_invalid")
    require(p["stage_workspace_path"] == str(ORG_ROOT), "stage_workspace_path_mismatch")
    for key in (
        "campaign_root",
        "private_evidence_root",
        "final_qa_workspace_path",
        "astra_workspace_path",
    ):
        require(type(p[key]) is str and Path(p[key]).is_absolute(), f"policy_{key}_invalid")
    require(
        Path(p["private_evidence_root"]).is_relative_to(Path(p["campaign_root"]))
        and Path(p["final_qa_workspace_path"]).is_relative_to(Path(p["campaign_root"])),
        "policy_path_outside_campaign",
    )
    roots = p["campaign_root_ids"]
    require(
        type(roots) is list
        and 1 <= len(roots) <= 32
        and len(set(roots)) == len(roots)
        and all(type(x) is str and UUID.fullmatch(x) for x in roots),
        "policy_roots_invalid",
    )
    ref = p["resource_policy_ref"]
    require(
        type(ref) is dict
        and set(ref) == {"path", "sha256"}
        and type(ref["path"]) is str
        and Path(ref["path"]).is_absolute()
        and type(ref["sha256"]) is str
        and SHA256.fullmatch(ref["sha256"]),
        "policy_resource_ref_invalid",
    )
    session = p["root_session"]
    require(
        type(session) is dict
        and set(session) == {"path", "session_id", "turn_id", "admission_at"}
        and type(session["path"]) is str
        and Path(session["path"]).is_absolute()
        and UUID.fullmatch(str(session["session_id"]))
        and type(session["turn_id"]) is str
        and UUID.fullmatch(session["turn_id"]),
        "policy_root_session_invalid",
    )
    utc(session["admission_at"], "policy_admission_time_invalid")
    source, canonical = p["source"], p["canonical"]
    require(
        type(source) is dict
        and set(source) == {"repo", "ref", "expected_sha", "remote_name", "remote_url"}
        and type(source["repo"]) is str
        and Path(source["repo"]).is_absolute()
        and type(source["expected_sha"]) is str
        and delivery.OID.fullmatch(source["expected_sha"])
        and source["remote_name"] is None
        and source["remote_url"] is None,
        "policy_safe_source_invalid",
    )
    require(
        type(canonical) is dict
        and set(canonical) == {"repo", "ref", "expected_base_sha", "remote_name", "remote_url"}
        and canonical["ref"] == "refs/heads/develop"
        and canonical["remote_name"] == "origin"
        and canonical["remote_url"] == "https://github.com/avariceJS/memecoin-ai.git"
        and type(canonical["repo"]) is str
        and Path(canonical["repo"]).is_absolute()
        and type(canonical["expected_base_sha"]) is str
        and delivery.OID.fullmatch(canonical["expected_base_sha"]),
        "policy_canonical_pin_invalid",
    )
    owner = p["owner_desk"]
    require(
        type(owner) is dict
        and set(owner) == {"path", "initial_sha256"}
        and (
            (owner["path"] is None and owner["initial_sha256"] is None)
            or (
                owner["path"] == str(ORG_ROOT / "engineering" / "OWNER-DESK.md")
                and type(owner["initial_sha256"]) is str
                and SHA256.fullmatch(owner["initial_sha256"])
            )
        ),
        "policy_owner_desk_pin_invalid",
    )
    require(type(p["outcomes"]) is list and len(p["outcomes"]) == 8, "policy_outcome_count_invalid")
    return p


def load_policy(path: Path, expected_sha: str) -> tuple[dict[str, Any], str]:
    raw = read_private(path, "finalization_policy", MAX_POLICY_BYTES, readonly=True)
    actual = digest(raw)
    require(
        type(expected_sha) is str and SHA256.fullmatch(expected_sha) and actual == expected_sha,
        "finalization_policy_hash_mismatch",
    )
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalizeError("finalization_policy_invalid_json") from exc
    return validate_policy(value), actual


def root_final_meta(policy: dict[str, Any]) -> dict[str, Any]:
    """Extract one pinned final record's timestamp/hash/offset; ignore its content entirely."""
    bind = policy["root_session"]
    path = Path(bind["path"])
    delivery.safe_dir(str(path.parent), "root_session_parent")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    offset = 0
    current_turn = None
    session_ids = []
    finals = []
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and info.st_size <= MAX_SESSION_BYTES,
            "root_session_file_invalid",
        )
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while True:
                line_offset = offset
                raw = stream.readline(1024 * 1024 + 1)
                if not raw:
                    break
                offset += len(raw)
                require(
                    len(raw) <= 1024 * 1024 and offset <= MAX_SESSION_BYTES,
                    "root_session_bounds_exceeded",
                )
                if not raw.strip():
                    continue
                try:
                    event = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise FinalizeError("root_session_invalid_jsonl") from exc
                require(type(event) is dict, "root_session_event_invalid")
                body = event.get("payload")
                if event.get("type") == "session_meta":
                    require(
                        type(body) is dict and type(body.get("id")) is str,
                        "root_session_meta_invalid",
                    )
                    session_ids.append(body["id"])
                elif event.get("type") == "turn_context":
                    require(
                        type(body) is dict and type(body.get("turn_id")) is str,
                        "root_turn_context_invalid",
                    )
                    current_turn = body["turn_id"]
                elif (
                    event.get("type") == "response_item"
                    and type(body) is dict
                    and body.get("type") == "message"
                    and body.get("role") == "assistant"
                    and body.get("channel") == "final"
                    and current_turn == bind["turn_id"]
                ):
                    stamp = utc(event.get("timestamp"), "root_final_timestamp_invalid")
                    require(
                        stamp >= utc(bind["admission_at"], "root_admission_time_invalid"),
                        "root_final_before_admission",
                    )
                    finals.append(
                        {
                            "timestamp": stamp.isoformat().replace("+00:00", "Z"),
                            "byte_offset": line_offset,
                            "record_line_sha256": digest(raw),
                        }
                    )
    finally:
        os.close(fd)
    require(session_ids == [bind["session_id"]], "root_session_id_mismatch")
    require(len(finals) == 1, "root_final_missing_or_ambiguous")
    return {"session_id": bind["session_id"], "turn_id": bind["turn_id"], **finals[0]}


def make_process_api(policy: dict[str, Any], task_id: str) -> delivery.Paperclip:
    api = delivery.Paperclip(
        {
            "api_origin": "http://127.0.0.1:3310",
            "company_id": policy["company_id"],
            "publish": {"task_id": task_id, "agent_id": policy["process_agent_id"]},
        }
    )
    actor = api.request("GET", "/agents/me")
    run = api.request("GET", f"/heartbeat-runs/{api.run_id}")
    issue = api.issue(task_id)
    context = run.get("contextSnapshot") if type(run) is dict else None
    require(
        type(actor) is dict
        and actor.get("id") == policy["process_agent_id"]
        and actor.get("companyId") == policy["company_id"],
        "finalizer_process_actor_mismatch",
    )
    require(
        type(run) is dict
        and run.get("id") == api.run_id
        and run.get("companyId") == policy["company_id"]
        and run.get("agentId") == policy["process_agent_id"]
        and run.get("issueId") == task_id
        and run.get("runtimeMode") == "legacy"
        and run.get("status") == "running"
        and type(context) is dict
        and context.get("issueId") == task_id,
        "finalizer_process_run_invalid",
    )
    require(
        type(issue) is dict
        and issue.get("id") == task_id
        and issue.get("companyId") == policy["company_id"]
        and issue.get("assigneeAgentId") == policy["process_agent_id"]
        and issue.get("status") in {"todo", "in_progress", "done"}
        and issue.get("executionRunId") == api.run_id
        and issue.get("checkoutRunId") == api.run_id,
        "finalizer_process_task_invalid",
    )
    return api


def issue_summary(
    api: delivery.Paperclip,
    task_id: str,
    actor: str | None = None,
    parent_id: str | None = None,
    roots: list[str] | None = None,
) -> dict[str, Any]:
    row = api.issue(task_id)
    require(
        type(row) is dict
        and row.get("id") == task_id
        and row.get("companyId") == api.m["company_id"],
        "campaign_issue_identity_invalid",
    )
    require(row.get("status") in delivery.KNOWN_ISSUE_STATUSES, "campaign_issue_status_invalid")
    if actor is not None:
        require(row.get("assigneeAgentId") == actor, "campaign_issue_actor_mismatch")
    if parent_id is not None:
        require(row.get("parentId") == parent_id, "campaign_issue_parent_mismatch")
    ancestry = []
    cursor = row.get("parentId")
    while cursor is not None and roots is not None and len(ancestry) < 12:
        require(type(cursor) is str and UUID.fullmatch(cursor), "campaign_issue_parent_invalid")
        ancestry.append(cursor)
        if cursor in roots:
            break
        parent = api.issue(cursor)
        require(
            type(parent) is dict
            and parent.get("id") == cursor
            and parent.get("companyId") == api.m["company_id"],
            "campaign_issue_ancestry_invalid",
        )
        cursor = parent.get("parentId")
    if roots is not None:
        require(bool(ancestry) and ancestry[-1] in roots, "campaign_issue_outside_root_scope")
    blocked = row.get("blockedByIssueIds", [])
    result = {
        key: row.get(key)
        for key in (
            "id",
            "companyId",
            "parentId",
            "status",
            "assigneeAgentId",
            "executionRunId",
            "updatedAt",
        )
    } | {"blocked_by": sorted(blocked) if type(blocked) is list else []}
    if roots is not None:
        result["campaign_ancestry"] = ancestry
    return result


def safe_run(row: dict[str, Any]) -> dict[str, Any]:
    profile = row.get("runnerProfileJson", {})
    native = profile.get("nativeExecutionInput", {}) if type(profile) is dict else {}
    provider = native.get("provider", {}) if type(native) is dict else {}
    usage = row.get("usageJson", {})
    if type(usage) is not dict:
        usage = {}
    counts = {}
    for out, keys in {
        "input": ("inputTokens", "input_tokens"),
        "output": ("outputTokens", "output_tokens"),
        "cached_input": ("cachedInputTokens", "cached_input_tokens"),
    }.items():
        value = next(
            (usage.get(key) for key in keys if type(usage.get(key)) is int and usage.get(key) >= 0),
            None,
        )
        if value is not None:
            counts[out] = value
    for output in ("input", "output", "cached_input"):
        counts.setdefault(output, None)
    return {
        key: row.get(key)
        for key in (
            "id",
            "companyId",
            "agentId",
            "issueId",
            "runtimeMode",
            "status",
            "startedAt",
            "finishedAt",
        )
    } | {
        "context_issue_id": (row.get("contextSnapshot") or {}).get("issueId")
        if type(row.get("contextSnapshot")) is dict
        else None,
        "provider": provider.get("kind") if type(provider) is dict else None,
        "model": provider.get("model") if type(provider) is dict else None,
        "reasoning_effort": provider.get("reasoningEffort") if type(provider) is dict else None,
        "billing_type": usage.get("billingType", usage.get("billing_type")),
        "usage": counts,
        "usage_status": "complete"
        if all(value is not None for value in counts.values())
        else "partial_or_pending",
        "error_code": row.get("errorCode"),
    }


def exact_run_summaries(
    api: delivery.Paperclip, task_id: str, agent_id: str, runtime_mode: str
) -> list[dict[str, Any]]:
    rows = api.request("GET", f"/issues/{task_id}/runs")
    require(
        type(rows) is list and len(rows) <= MAX_RUNS and all(type(row) is dict for row in rows),
        "campaign_runs_invalid",
    )
    result = []
    seen = set()
    for ref in rows:
        if ref.get("agentId") != agent_id or ref.get("contextIssueId") != task_id:
            continue
        run_id = ref.get("runId") or ref.get("id")
        require(
            type(run_id) is str and UUID.fullmatch(run_id) and run_id not in seen,
            "campaign_run_id_invalid",
        )
        seen.add(run_id)
        full = api.request("GET", f"/heartbeat-runs/{run_id}")
        ctx = full.get("contextSnapshot") if type(full) is dict else None
        require(
            type(full) is dict
            and full.get("id") == run_id
            and full.get("companyId") == api.m["company_id"]
            and full.get("agentId") == agent_id
            and full.get("issueId") == task_id
            and full.get("nativeIssueId") in {None, task_id}
            and full.get("runtimeMode") == runtime_mode
            and type(ctx) is dict
            and ctx.get("issueId") == task_id
            and full.get("status") in ACTIVE | TERMINAL,
            "campaign_exact_run_invalid",
        )
        summary = safe_run(full)
        if runtime_mode == "native":
            billing = summary["billing_type"]
            provider = summary["provider"]
            summary["resource_classification"] = (
                "subscription_model_run"
                if provider == "codex" and billing in {"subscription", "subscription_included"}
                else "unverified_or_non_subscription"
            )
        result.append(summary)
    result.sort(key=lambda r: str(r.get("finishedAt") or r.get("startedAt") or ""))
    return result


def validate_outcomes(policy: dict[str, Any]) -> None:
    fields = {
        "key",
        "programme_id",
        "department",
        "title",
        "paths",
        "planning_task_id",
        "prepare_task_id",
        "manager_task_id",
        "lead_agent_id",
        "developer_agent_id",
        "qa_agent_id",
        "envelope_path",
        "envelope_sha256",
        "manifest_path",
        "output_root",
        "candidate_ref",
        "predecessor_candidates",
    }
    rows = policy["outcomes"]
    require(type(rows) is list and len(rows) == 8, "policy_outcome_count_invalid")
    seen = set()
    for row in rows:
        require(type(row) is dict and set(row) == fields, "policy_outcome_fields_invalid")
        require(
            type(row["key"]) is str and row["key"] and row["key"] not in seen,
            "policy_outcome_key_invalid",
        )
        seen.add(row["key"])
        for key in (
            "programme_id",
            "planning_task_id",
            "prepare_task_id",
            "manager_task_id",
            "lead_agent_id",
            "developer_agent_id",
            "qa_agent_id",
        ):
            require(
                type(row[key]) is str and UUID.fullmatch(row[key]), f"policy_outcome_{key}_invalid"
            )
        require(
            type(row["envelope_sha256"]) is str and SHA256.fullmatch(row["envelope_sha256"]),
            "policy_envelope_hash_invalid",
        )
        for key in ("envelope_path", "manifest_path", "output_root"):
            require(
                type(row[key]) is str and Path(row[key]).is_absolute(),
                f"policy_outcome_{key}_invalid",
            )
        require(
            type(row["paths"]) is list
            and 1 <= len(row["paths"]) <= 8
            and all(type(x) is str and x for x in row["paths"]),
            "policy_outcome_paths_invalid",
        )
        require(
            type(row["predecessor_candidates"]) is list and len(row["predecessor_candidates"]) <= 4,
            "policy_predecessors_invalid",
        )


def attachment(
    api: delivery.Paperclip,
    task_id: str,
    actor_id: str,
    name: str,
    limit: int,
    runtime_mode: str = "native",
) -> tuple[dict[str, Any], bytes]:
    task = {"task_id": task_id, "agent_id": actor_id}
    runs = exact_run_summaries(api, task_id, actor_id, runtime_mode)
    require(bool(runs), f"{name}_run_missing")
    latest = runs[-1]
    row, content = api.artifact(task, {"runId": latest["id"]}, name, limit)
    return {
        "task_id": task_id,
        "run": latest,
        "attachment": {
            k: row.get(k)
            for k in (
                "id",
                "originalFilename",
                "createdAt",
                "originatingRunId",
                "byteSize",
                "sha256",
            )
        },
    }, content


def _sha_file(path: Path, label: str, max_bytes: int = 16 * 1024 * 1024) -> dict[str, Any]:
    data = read_private(path, label, max_bytes)
    return {"path": str(path), "sha256": digest(data), "bytes": len(data)}


def _campaign_run_ledger(
    api: delivery.Paperclip, company_id: str, roots: list[str]
) -> dict[str, Any]:
    issues: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        page = api.request("GET", f"/companies/{company_id}/issues?limit=100&offset={offset}")
        require(
            type(page) is list and len(page) <= 100 and all(type(x) is dict for x in page),
            "campaign_issue_listing_invalid",
        )
        for issue in page:
            identifier = issue.get("id")
            require(
                type(identifier) is str
                and UUID.fullmatch(identifier)
                and issue.get("companyId") == company_id
                and identifier not in issues,
                "campaign_issue_listing_identity_invalid",
            )
            issues[identifier] = issue
        require(len(issues) <= 1000, "campaign_issue_listing_too_large")
        if len(page) < 100:
            break
        offset += 100
    require(all(root in issues for root in roots), "campaign_root_readback_missing")
    scope = set(roots)
    while True:
        children = {
            identifier
            for identifier, row in issues.items()
            if row.get("parentId") in scope and identifier not in scope
        }
        if not children:
            break
        scope.update(children)
        require(len(scope) <= 1000, "campaign_issue_scope_too_large")
    issue_summaries = []
    run_summaries = []
    exact_run_ids = set()
    excluded_activity_links = 0
    ledger_errors = []
    for identifier in sorted(scope):
        issue = issues[identifier]
        issue_summaries.append(
            {
                key: issue.get(key)
                for key in (
                    "id",
                    "parentId",
                    "status",
                    "assigneeAgentId",
                    "executionRunId",
                    "updatedAt",
                    "title",
                )
            }
        )
        try:
            rows = api.request("GET", f"/issues/{identifier}/runs")
            require(
                type(rows) is list and len(rows) <= 100 and all(type(row) is dict for row in rows),
                "campaign_run_listing_invalid",
            )
        except Exception as exc:
            ledger_errors.append({"issue_id": identifier, "error": type(exc).__name__})
            continue
        for ref in rows:
            run_id = ref.get("runId") or ref.get("id")
            if type(run_id) is not str or not UUID.fullmatch(run_id):
                ledger_errors.append({"issue_id": identifier, "error": "invalid_run_id"})
                continue
            if run_id in exact_run_ids:
                continue
            try:
                full = api.request("GET", f"/heartbeat-runs/{run_id}")
                context = full.get("contextSnapshot") if type(full) is dict else None
                context_issue = context.get("issueId") if type(context) is dict else None
                if context_issue != identifier:
                    excluded_activity_links += 1
                    continue
                require(
                    type(full) is dict
                    and full.get("id") == run_id
                    and full.get("companyId") == company_id
                    and full.get("issueId") == identifier
                    and full.get("nativeIssueId") in {None, identifier}
                    and (ref.get("agentId") is None or ref.get("agentId") == full.get("agentId"))
                    and UUID.fullmatch(str(full.get("agentId", ""))),
                    "campaign_run_identity_invalid",
                )
                summary = safe_run(full)
                summary["resource_classification"] = (
                    "subscription_model_run"
                    if summary["runtimeMode"] == "native"
                    and summary["provider"] == "codex"
                    and summary["billing_type"] in {"subscription", "subscription_included"}
                    else "unverified_or_non_subscription"
                    if summary["runtimeMode"] == "native"
                    else "trusted_process_run"
                    if summary["runtimeMode"] == "legacy"
                    else "unknown_runtime"
                )
                run_summaries.append(summary)
                exact_run_ids.add(run_id)
                require(len(exact_run_ids) <= 500, "campaign_run_ledger_too_large")
            except Exception as exc:
                ledger_errors.append(
                    {"issue_id": identifier, "run_id": run_id, "error": type(exc).__name__}
                )
    run_summaries.sort(key=lambda row: str(row.get("startedAt") or ""))
    return {
        "scope_root_ids": roots,
        "scope_issue_count": len(scope),
        "issues": issue_summaries,
        "runs": run_summaries,
        "exact_run_count": len(exact_run_ids),
        "native_codex_subscription_run_ids": [
            row["id"]
            for row in run_summaries
            if row["resource_classification"] == "subscription_model_run"
        ],
        "excluded_activity_link_count": excluded_activity_links,
        "read_errors": ledger_errors,
    }


def _publication_readback(
    manifest: dict[str, Any],
    manifest_sha: str,
    source_task_id: str,
    paths: list[str],
    policy: dict[str, Any],
) -> dict[str, Any]:
    custody = Path(manifest["custody_dir"])
    path = custody / f"publication-{source_task_id}.json"
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    saved, saved_sha = delivery.private_json_file(str(path), "publication_receipt")
    receipt = saved.get("canonical_receipt") if type(saved) is dict else None
    expected_fields = {
        "schema",
        "manifest_sha256",
        "source_task_id",
        "source_run_id",
        "qa_task_id",
        "qa_run_id",
        "qa_attachment_id",
        "qa_sha256",
        "source_patch_sha256",
        "files",
        "expected_tip",
        "canonical_ref",
        "candidate_ref",
        "canonical_receipt",
        "publication_digest",
    }
    unsigned = {key: value for key, value in saved.items() if key != "publication_digest"}
    require(
        set(saved) == expected_fields
        and saved.get("schema") == "brain-native-delivery-publication-v1"
        and saved.get("manifest_sha256") == manifest_sha
        and saved.get("source_task_id") == source_task_id
        and saved.get("qa_task_id") == manifest["qa"]["task_id"]
        and saved.get("canonical_ref") == policy["canonical"]["ref"]
        and saved.get("candidate_ref") == manifest["canonical"]["candidate_ref"]
        and type(saved.get("files")) is dict
        and set(saved["files"]) == set(paths)
        and all(
            type(value) is str and SHA256.fullmatch(value) for value in saved["files"].values()
        ),
        "publication_wrapper_invalid",
    )
    require(
        saved.get("publication_digest") == delivery.digest(delivery.json_bytes(unsigned)),
        "publication_wrapper_digest_invalid",
    )
    require(
        type(receipt) is dict and receipt.get("expected_tip") == saved.get("expected_tip"),
        "publication_canonical_receipt_invalid",
    )
    receipt_files = {
        item["path"]: item["sha256"]
        for item in receipt.get("files", [])
        if type(item) is dict and set(item) == {"path", "mode", "old", "new", "sha256"}
    }
    require(
        saved["files"] == receipt_files and set(receipt_files) == set(paths),
        "publication_file_binding_invalid",
    )
    repo = Path(policy["canonical"]["repo"])
    publisher._check_candidate(repo, policy["canonical"]["ref"], receipt, tuple(paths))
    for item in receipt["files"]:
        content = delivery.git(repo, "cat-file", "blob", item["new"])
        require(
            digest(content) == saved["files"][item["path"]],
            "publication_file_blob_hash_invalid",
        )
    head = delivery.git(repo, "rev-parse", policy["canonical"]["ref"]).decode().strip()
    delivery.git(repo, "merge-base", "--is-ancestor", receipt["candidate"], head)
    raw = read_private(path, "publication_receipt", 512 * 1024)
    artifact_dir = Path(policy["final_qa_workspace_path"]) / "artifacts"
    try:
        artifact_dir.mkdir(mode=0o700)
    except FileExistsError:
        pass
    delivery.safe_dir(str(artifact_dir), "final_qa_artifacts", private=True)
    safe_path = artifact_dir / f"publication-{source_task_id}.json"
    copied_sha = write_once(safe_path, raw, 0o400)
    require(copied_sha == saved_sha, "publication_evidence_copy_mismatch")
    return {
        "status": "verified_local_ancestor",
        "path": str(path),
        "safe_path": str(safe_path),
        "sha256": saved_sha,
        "expected_tip": saved["expected_tip"],
        "candidate": receipt["candidate"],
        "candidate_ref": saved["candidate_ref"],
        "local_canonical_head": head,
        "source_run_id": saved["source_run_id"],
        "source_patch_sha256": saved["source_patch_sha256"],
        "qa_run_id": saved["qa_run_id"],
        "qa_attachment_id": saved["qa_attachment_id"],
        "qa_sha256": saved["qa_sha256"],
        "file_sha256s": saved["files"],
    }


def collect_outcomes(api: delivery.Paperclip, policy: dict[str, Any]) -> dict[str, Any]:
    records = []
    manifests = []
    ledger = _campaign_run_ledger(api, policy["company_id"], policy["campaign_root_ids"])
    runs_by_issue: dict[str, list[dict[str, Any]]] = {}
    for run in ledger["runs"]:
        runs_by_issue.setdefault(run["issueId"], []).append(run)
    for bind in policy["outcomes"]:
        record = {
            "key": bind["key"],
            "programme_id": bind["programme_id"],
            "department": bind["department"],
            "evidence_status": "unknown",
        }
        try:
            env, env_raw = read_json(Path(bind["envelope_path"]), "outcome_envelope")
            require(
                digest(env_raw) == bind["envelope_sha256"]
                and env.get("company_id") == policy["company_id"],
                "outcome_envelope_hash_or_company_mismatch",
            )
            record["envelope_sha256"] = bind["envelope_sha256"]
            record["planning_task"] = issue_summary(
                api,
                bind["planning_task_id"],
                actor=bind["lead_agent_id"],
                roots=policy["campaign_root_ids"],
            )
            record["manager_task"] = issue_summary(
                api,
                bind["manager_task_id"],
                actor=bind["lead_agent_id"],
                roots=policy["campaign_root_ids"],
            )
            record["manager_task"]["runs"] = runs_by_issue.get(bind["manager_task_id"], [])
            record["planning_runs"] = runs_by_issue.get(bind["planning_task_id"], [])
            if not Path(bind["manifest_path"]).exists():
                record["evidence_status"] = "not_prepared"
                records.append(record)
                continue
            manifest, manifest_sha = delivery.load_manifest(bind["manifest_path"])
            require(
                manifest["company_id"] == policy["company_id"]
                and manifest["source"]["base_sha"] == policy["source"]["expected_sha"]
                and manifest["canonical"]["repo"] == policy["canonical"]["repo"]
                and manifest["canonical"]["ref"] == policy["canonical"]["ref"]
                and manifest["canonical"]["remote_name"] == policy["canonical"]["remote_name"]
                and manifest["canonical"]["remote_url"] == policy["canonical"]["remote_url"],
                "outcome_manifest_pin_mismatch",
            )
            require(
                manifest.get("manager_task_id") == bind["manager_task_id"]
                and manifest["developer"]["agent_id"] == bind["developer_agent_id"]
                and manifest["qa"]["agent_id"] == bind["qa_agent_id"],
                "outcome_manifest_task_binding_mismatch",
            )
            prepare_receipt_path = Path(bind["output_root"]) / "prepare-receipt.json"
            prep, prep_sha = delivery.private_json_file(
                str(prepare_receipt_path), "preparation_receipt"
            )
            require(
                prep.get("schema") == delivery.PREPARATION_RECEIPT_SCHEMA
                and prep.get("company_id") == policy["company_id"]
                and prep.get("envelope_sha256") == bind["envelope_sha256"]
                and prep.get("planning_task_id") == bind["planning_task_id"]
                and prep.get("manager_task_id") == bind["manager_task_id"]
                and prep.get("source_base_sha") == policy["source"]["expected_sha"]
                and prep.get("canonical_ref") == policy["canonical"]["ref"]
                and prep.get("canonical_base_tip") == policy["canonical"]["expected_base_sha"],
                "preparation_receipt_pin_mismatch",
            )
            plan_meta, plan_bytes = attachment(
                api,
                bind["planning_task_id"],
                bind["lead_agent_id"],
                "DELIVERY-PLAN.json",
                512 * 1024,
            )
            require(
                plan_meta["run"]["id"] == prep["planning_run_id"]
                and plan_meta["attachment"]["id"] == prep["planning_attachment_id"]
                and plan_meta["attachment"]["sha256"] == prep["planning_attachment_sha256"],
                "planning_attachment_receipt_mismatch",
            )
            slots = delivery._validate_delivery_plan(json.loads(plan_bytes), env)
            prep_slots = {row["slot"]: row for row in prep.get("slots", []) if type(row) is dict}
            require(len(prep_slots) == len(slots), "preparation_slot_count_mismatch")
            slot_rows = []
            for slot in slots:
                row = prep_slots.get(slot["slot"])
                expected_manifest_path = (
                    Path(bind["output_root"]) / f"slot{slot['slot']}" / "manifest.json"
                )
                require(
                    type(row) is dict
                    and row.get("source_task_id") == slot["source_task_id"]
                    and row.get("stage_task_id") == slot["stage_task_id"]
                    and row.get("qa_task_id") == slot["qa_task_id"]
                    and row.get("publish_task_id") == slot["publish_task_id"]
                    and row.get("manifest_path") == str(expected_manifest_path),
                    "preparation_slot_binding_mismatch",
                )
                slot_manifest, slot_manifest_sha = delivery.load_manifest(row["manifest_path"])
                require(
                    row.get("manifest_sha256") == slot_manifest_sha
                    and slot_manifest["company_id"] == policy["company_id"]
                    and slot_manifest["source"]["base_sha"] == policy["source"]["expected_sha"]
                    and slot_manifest["canonical"]["repo"] == policy["canonical"]["repo"]
                    and slot_manifest["canonical"]["ref"] == policy["canonical"]["ref"]
                    and slot_manifest["developer"]["task_id"] == slot["source_task_id"]
                    and slot_manifest["developer"]["agent_id"] == bind["developer_agent_id"]
                    and slot_manifest["qa"]["task_id"] == slot["qa_task_id"]
                    and slot_manifest["qa"]["agent_id"] == bind["qa_agent_id"]
                    and slot_manifest["stage"]["task_id"] == slot["stage_task_id"]
                    and slot_manifest["publish"]["task_id"] == slot["publish_task_id"],
                    "prepared_manifest_receipt_mismatch",
                )
                tasks = {}
                bound = {
                    "source": (slot["source_task_id"], bind["developer_agent_id"]),
                    "stage": (slot["stage_task_id"], slot_manifest["stage"]["agent_id"]),
                    "qa": (slot["qa_task_id"], bind["qa_agent_id"]),
                    "publish": (slot["publish_task_id"], slot_manifest["publish"]["agent_id"]),
                    "prepare": (bind["prepare_task_id"], policy["process_agent_id"]),
                }
                for kind, (task_id, agent_id) in bound.items():
                    tasks[kind] = issue_summary(
                        api, task_id, actor=agent_id, roots=policy["campaign_root_ids"]
                    )
                    tasks[kind]["runs"] = runs_by_issue.get(task_id, [])
                qa_evidence = []
                for run in tasks["qa"]["runs"]:
                    refs = [
                        x
                        for x in api.attachments(slot["qa_task_id"])
                        if x.get("originatingRunId") == run["id"]
                        and x.get("originalFilename") == "QA-RESULT.json"
                    ]
                    for ref in refs[:2]:
                        content = api.request("GET", f"/attachments/{ref['id']}/content", raw=True)
                        require(
                            len(content) == ref.get("byteSize")
                            and len(content) <= 256 * 1024
                            and digest(content) == str(ref.get("sha256", "")).lower(),
                            "qa_artifact_hash_mismatch",
                        )
                        artifact_dir = Path(policy["final_qa_workspace_path"]) / "artifacts"
                        try:
                            artifact_dir.mkdir(mode=0o700)
                        except FileExistsError:
                            pass
                        delivery.safe_dir(str(artifact_dir), "final_qa_artifacts", private=True)
                        target = artifact_dir / f"{ref['id']}.json"
                        file_sha = write_once(target, content, 0o400)
                        qa_evidence.append(
                            {
                                "attachment_id": ref["id"],
                                "originating_run_id": run["id"],
                                "created_at": ref.get("createdAt"),
                                "sha256": file_sha,
                                "bytes": len(content),
                                "safe_path": str(target),
                            }
                        )
                custody = Path(slot_manifest["custody_dir"])
                history = delivery.stage_history(custody, slot["source_task_id"], slot_manifest_sha)
                stage_records = [
                    {
                        "sha256": digest(path.read_bytes()),
                        "revision": receipt.get("revision"),
                        "developer_run_id": receipt.get("developer_run_id"),
                        "previous_stage_sha256": receipt.get("previous_stage_sha256"),
                    }
                    for path, receipt in history
                ]
                publication = _publication_readback(
                    slot_manifest, slot_manifest_sha, slot["source_task_id"], slot["paths"], policy
                )
                manifests.append(slot_manifest)
                slot_rows.append(
                    {
                        "slot": slot["slot"],
                        "title": slot["title"],
                        "module": slot["module"],
                        "paths": slot["paths"],
                        "candidate_ref": slot_manifest["canonical"]["candidate_ref"],
                        "tasks": tasks,
                        "qa_artifacts": qa_evidence,
                        "stage_history": stage_records,
                        "publication": publication,
                    }
                )
            record.update(
                {
                    "evidence_status": "prepared",
                    "manifest_sha256": manifest_sha,
                    "prepare_receipt_sha256": prep_sha,
                    "planning_attachment": plan_meta,
                    "slots": slot_rows,
                }
            )
        except Exception as exc:
            record["evidence_status"] = "blocked_or_incomplete"
            record["evidence_error"] = type(exc).__name__
        records.append(record)
    remote = {"status": "not_attempted", "refs": {}}
    if manifests:
        try:
            canonical = policy["canonical"]
            repo = Path(canonical["repo"])
            require(
                delivery.git(repo, "remote", "get-url", canonical["remote_name"]).decode().strip()
                == canonical["remote_url"],
                "campaign_remote_url_mismatch",
            )
            refs_requested = sorted(
                {canonical["ref"], *[m["canonical"]["candidate_ref"] for m in manifests]}
            )
            raw = delivery.remote_git(
                repo, "ls-remote", canonical["remote_name"], *refs_requested, github_auth=True
            )
            refs = {}
            for line in raw.decode("ascii").splitlines():
                oid, name = line.split("\t")
                require(
                    delivery.OID.fullmatch(oid) and name in refs_requested and name not in refs,
                    "campaign_remote_refs_invalid",
                )
                refs[name] = oid
            local = delivery.git(repo, "rev-parse", canonical["ref"]).decode().strip()
            remote = {
                "status": "read",
                "refs": refs,
                "local_canonical_head": local,
                "remote_canonical_matches_local": refs.get(canonical["ref"]) == local,
            }
        except Exception as exc:
            remote = {"status": "unknown", "error": type(exc).__name__, "refs": {}}
    for outcome in records:
        for slot in outcome.get("slots", []):
            pub = slot.get("publication", {})
            if pub.get("status") == "verified_local_ancestor":
                observed = remote.get("refs", {}).get(pub.get("candidate_ref"))
                pub["remote_candidate_ref_observed"] = observed
                pub["remote_candidate_matches_receipt"] = observed == pub.get("candidate")
    return {"outcomes": records, "remote_readback": remote, "campaign_run_ledger": ledger}


def preflight(
    policy: dict[str, Any], phase: str, reserve: int, receipt_name: str
) -> dict[str, Any]:
    try:
        resource, _ = resource_guard.load_policy_reference(
            policy["resource_policy_ref"], policy["company_id"]
        )
        api_stub = delivery.Paperclip(
            {
                "api_origin": "http://127.0.0.1:3310",
                "company_id": policy["company_id"],
                "publish": {
                    "task_id": policy["final_evidence_task_id"],
                    "agent_id": policy["process_agent_id"],
                },
            }
        )
        ids = resource_guard.campaign_model_run_ids(api_stub, policy["company_id"], resource)
        path = Path(policy["private_evidence_root"]) / receipt_name
        result = resource_guard.preflight(
            resource, policy["company_id"], phase, ids, path, reserve_model_runs=reserve
        )
        return {
            "ok": True,
            "path": str(path),
            "sha256": digest(path.read_bytes()),
            "observed_at": result["observed_at"],
            "remaining_percent": result["remaining_percent"],
            "observed_model_run_count": result["observed_model_run_count"],
        }
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "reason": str(exc)[:160]}


def final_evidence(
    policy: dict[str, Any], policy_sha: str, api: delivery.Paperclip
) -> dict[str, Any]:
    require(api.run_id and api.agent_id == policy["process_agent_id"], "finalizer_identity_missing")
    try:
        final_meta = {"status": "matched", **root_final_meta(policy)}
    except Exception as exc:
        final_meta = {"status": "unknown", "reason": type(exc).__name__}
    quota = preflight(policy, "stage", 8, f"final-evidence-quota-{api.run_id}.json")
    collection_error = None
    try:
        outcome_evidence = collect_outcomes(api, policy)
    except Exception as exc:
        outcome_evidence = {
            "outcomes": [],
            "remote_readback": {"status": "unknown"},
            "campaign_run_ledger": {"status": "unknown"},
        }
        collection_error = type(exc).__name__
    payload = {
        "schema": EVIDENCE_SCHEMA,
        "company_id": policy["company_id"],
        "policy_sha256": policy_sha,
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "root_final": final_meta,
        "quota_preflight": quota,
        **outcome_evidence,
        "collection_error": collection_error,
        "final_qa_task_id": policy["final_qa_task_id"],
        "limitations": [
            "Native dispatch is not an atomic quota reservation.",
            "This ledger supports independent review and does not replace it.",
        ],
    }
    raw = encode(payload)
    dest = Path(policy["final_qa_workspace_path"]) / "FINAL-CAMPAIGN-EVIDENCE.json"
    evidence_sha = write_once(dest, raw, 0o400)
    # Stash an owner-private immutable copy before making the final QA task runnable.
    private_copy = Path(policy["private_evidence_root"]) / "FINAL-CAMPAIGN-EVIDENCE.json"
    write_once(private_copy, raw, 0o400)
    if not quota["ok"] or collection_error:
        pause = controlled_pause(
            policy,
            api,
            reason="final_evidence_quota_preflight_blocked"
            if not quota["ok"]
            else "final_evidence_collection_failed",
        )
        return {
            "status": "evidence_blocked_and_controlled_stop_attempted",
            "evidence_sha256": evidence_sha,
            "quota": quota,
            "collection_error": collection_error,
            "pause": pause,
            "final_qa_activated": False,
        }
    api.complete(policy["final_evidence_task_id"])
    return {
        "status": "evidence_materialized",
        "evidence_sha256": evidence_sha,
        "path": str(dest),
        "quota": quota,
    }


def verify_final_tasks(
    api: delivery.Paperclip, policy: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    qa = issue_summary(api, policy["final_qa_task_id"], actor=policy["final_qa_agent_id"])
    astra = issue_summary(api, policy["astra_conclusion_task_id"], actor=policy["astra_agent_id"])
    require(qa["status"] == "done" and astra["status"] == "done", "final_qa_or_astra_task_not_done")
    qa_run_meta, qa_bytes = attachment(
        api,
        policy["final_qa_task_id"],
        policy["final_qa_agent_id"],
        "FINAL-ACCEPTANCE.json",
        32 * 1024,
    )
    qa_value = json.loads(qa_bytes)
    expected = {
        "schema",
        "company_id",
        "final_evidence_sha256",
        "verdict",
        "accepted_outcome_keys",
        "blocked_outcome_keys",
        "limitations",
    }
    require(
        type(qa_value) is dict
        and set(qa_value) == expected
        and qa_value.get("schema") == QA_SCHEMA
        and qa_value.get("company_id") == policy["company_id"]
        and qa_value.get("verdict") in {"accept", "reject", "blocked"}
        and type(qa_value.get("accepted_outcome_keys")) is list
        and type(qa_value.get("blocked_outcome_keys")) is list
        and type(qa_value.get("limitations")) is list
        and all(type(key) is str for key in qa_value["accepted_outcome_keys"])
        and all(type(key) is str for key in qa_value["blocked_outcome_keys"])
        and all(type(value) is str for value in qa_value["limitations"])
        and set(qa_value["accepted_outcome_keys"]).isdisjoint(qa_value["blocked_outcome_keys"])
        and set(qa_value["accepted_outcome_keys"] + qa_value["blocked_outcome_keys"])
        <= {row["key"] for row in policy["outcomes"]},
        "final_acceptance_artifact_invalid",
    )
    evidence = Path(policy["final_qa_workspace_path"]) / "FINAL-CAMPAIGN-EVIDENCE.json"
    require(
        digest(read_private(evidence, "final_evidence", 4 * 1024 * 1024))
        == qa_value["final_evidence_sha256"],
        "final_acceptance_evidence_hash_mismatch",
    )
    astra_meta, astra_bytes = attachment(
        api,
        policy["astra_conclusion_task_id"],
        policy["astra_agent_id"],
        "FOUNDER-CONCLUSION.md",
        24 * 1024,
    )
    require(
        qa_run_meta["run"]["status"] == "succeeded" and astra_meta["run"]["status"] == "succeeded",
        "final_native_run_not_succeeded",
    )
    try:
        conclusion = astra_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FinalizeError("founder_conclusion_not_utf8") from exc
    require(conclusion.strip() and "\ufffd" not in conclusion, "founder_conclusion_invalid")
    require(
        any("\u0400" <= char <= "\u04ff" for char in conclusion), "founder_conclusion_not_russian"
    )
    qa_record = {
        "task": qa,
        "run": qa_run_meta["run"],
        "attachment": qa_run_meta["attachment"],
        "sha256": digest(qa_bytes),
        "decision": qa_value,
    }
    astra_record = {
        "task": astra,
        "run": astra_meta["run"],
        "attachment": astra_meta["attachment"],
        "sha256": digest(astra_bytes),
        "safe_text": conclusion,
    }
    return qa_record, astra_record


def current_live(api: company_handoff.PaperclipAPI, company_id: str) -> list[dict[str, Any]]:
    rows = api.get(f"/companies/{company_id}/live-runs?limit=50&minCount=0")
    if type(rows) is dict:
        rows = rows.get("runs", rows.get("items"))
    require(
        type(rows) is list and len(rows) < 50 and all(type(r) is dict for r in rows),
        "live_runs_readback_invalid",
    )
    return [
        {k: row.get(k) for k in ("id", "runId", "agentId", "issueId", "status", "startedAt")}
        for row in rows
    ]


def controlled_pause(
    policy: dict[str, Any], api: delivery.Paperclip, reason: str
) -> dict[str, Any]:
    """Persist a blocked projection, then request one Board pause; pause may cancel this run."""
    trusted = company_handoff.PaperclipAPI()
    path = f"/api/companies/{policy['company_id']}"
    company = trusted.get(path)
    company = company.get("company", company) if type(company) is dict else {}
    live = current_live(trusted, policy["company_id"])
    others = [r for r in live if (r.get("id") or r.get("runId")) != api.run_id]
    root = Path(policy["private_evidence_root"])
    projection = root / "FOUNDER-CONCLUSION.md"
    projection_text = (
        "Итог кампании неизвестен: подготовка финальных доказательств была остановлена "
        "проверкой ресурса или ошибкой чтения. Независимая финальная оценка Sol и "
        "заключение Astra не выполнялись. Состояние остановки — STOP_PENDING; "
        "подтверждённый статус паузы не заявляется."
    )
    projection_sha = write_once(projection, projection_text.encode(), 0o400)
    evidence_path = Path(policy["final_qa_workspace_path"]) / "FINAL-CAMPAIGN-EVIDENCE.json"
    evidence_sha = (
        digest(read_private(evidence_path, "blocked_evidence", 4 * 1024 * 1024))
        if evidence_path.exists()
        else None
    )
    intent_path = root / "quota-stop-intent.json"
    intent = {
        "schema": "brain-campaign-quota-stop-intent-v1",
        "state": "STOP_PENDING",
        "company_id": policy["company_id"],
        "process_run_id": api.run_id,
        "reason": reason,
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "company_status_before": company.get("status"),
        "pending_live_runs_before_pause": live,
        "other_live_runs_before_pause": others,
        "founder_projection_path": str(projection),
        "founder_projection_sha256": projection_sha,
        "evidence_path": str(evidence_path),
        "evidence_sha256": evidence_sha,
    }
    if intent_path.exists():
        old, raw = read_json(intent_path, "quota_stop_intent", 16 * 1024)
        require(
            old.get("company_id") == policy["company_id"] and old.get("reason") == reason,
            "quota_stop_intent_binding_mismatch",
        )
        intent_sha = digest(raw)
        if old.get("process_run_id") != api.run_id:
            company = trusted.get(path)
            company = company.get("company", company) if type(company) is dict else {}
            return {
                "intent_sha256": intent_sha,
                "status_after": company.get("status"),
                "other_live_runs": current_live(trusted, policy["company_id"]),
                "own_process_run_id": api.run_id,
                "pause_attempted": False,
                "replay": "readback_only_prior_attempt",
            }
    else:
        intent_sha = write_once(intent_path, encode(intent), 0o400)
    checkpoint_path = root / "quota-stop-checkpoint.json"
    if not checkpoint_path.exists():
        write_once(
            checkpoint_path,
            encode(
                {
                    "schema": "brain-campaign-pre-pause-checkpoint-v1",
                    "state": "STOP_PENDING",
                    "intent_sha256": intent_sha,
                    "company_status_before": company.get("status"),
                    "pending_live_runs_before_pause": live,
                    "own_process_run_id": api.run_id,
                    "own_process_terminal_observed": False,
                    "founder_projection_sha256": projection_sha,
                    "evidence_sha256": evidence_sha,
                }
            ),
            0o400,
        )
    marker = intent_path.with_name("quota-stop-attempted")
    first = not marker.exists()
    should_patch = company.get("status") == "active" and not others
    if first and should_patch:
        write_once(marker, encode({"intent_sha256": intent_sha}), 0o400)
    if first and should_patch:
        trusted.patch(path, {"status": "paused"})
    after = trusted.get(path)
    after = after.get("company", after) if type(after) is dict else {}
    return {
        "state": "STOP_PENDING",
        "intent_sha256": intent_sha,
        "status_before": company.get("status"),
        "status_after": after.get("status"),
        "other_live_runs": others,
        "own_process_run_id": api.run_id,
        "own_process_terminal_observed": False,
        "pause_attempted": bool(marker.exists()),
        "pause_attempt_state": (
            "ATTEMPT_MARKED"
            if marker.exists()
            else "UNATTEMPTED_OTHER_LIVE_RUNS"
            if others
            else "UNATTEMPTED_ALREADY_PAUSED"
            if company.get("status") == "paused"
            else "UNATTEMPTED_COMPANY_NOT_ACTIVE"
            if company.get("status") != "active"
            else "UNATTEMPTED"
        ),
    }


def stop_campaign(
    policy: dict[str, Any], policy_sha: str, api: delivery.Paperclip
) -> dict[str, Any]:
    quota = preflight(policy, "stop", 0, f"stop-quota-{api.run_id}.json")
    verification_error = None
    try:
        qa_record, astra_record = verify_final_tasks(api, policy)
        conclusion = astra_record["safe_text"]
    except Exception as exc:
        qa_record, astra_record = {}, {}
        conclusion = "Итоговое подтверждение отсутствует; результат остаётся неизвестным."
        verification_error = type(exc).__name__
    try:
        run_ledger = _campaign_run_ledger(api, policy["company_id"], policy["campaign_root_ids"])
    except Exception as exc:
        run_ledger = {"status": "unknown", "error": type(exc).__name__}
    private_root = Path(policy["private_evidence_root"])
    desk_status = "owner_desk_not_updated_private_projection_only"
    api_owner = company_handoff.PaperclipAPI()
    company_path = f"/api/companies/{policy['company_id']}"
    company = api_owner.get(company_path)
    company = company.get("company", company) if type(company) is dict else {}
    require(company.get("id") == policy["company_id"], "stop_company_identity_invalid")
    live = current_live(api_owner, policy["company_id"])
    others = [r for r in live if (r.get("id") or r.get("runId")) != api.run_id]
    projection = private_root / "FOUNDER-CONCLUSION.md"
    projection_sha = write_once(projection, conclusion.encode(), 0o400)
    evidence = {
        "schema": "brain-campaign-stop-intent-v1",
        "state": "STOP_PENDING",
        "company_id": policy["company_id"],
        "policy_sha256": policy_sha,
        "process_run_id": api.run_id,
        "requested_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "company_status_before": company.get("status"),
        "pending_live_runs_before_pause": live,
        "other_live_runs_before_pause": others,
        "quota_preflight": quota,
        "final_qa": qa_record,
        "astra": astra_record,
        "verification_error": verification_error,
        "campaign_run_ledger": run_ledger,
        "founder_projection_path": str(projection),
        "founder_projection_sha256": projection_sha,
        "owner_desk_status": desk_status,
    }
    intent_path = private_root / "stop-intent.json"
    intent_attempt = private_root / "stop-attempted"
    checkpoint_path = private_root / "pre-pause-checkpoint.json"
    if intent_path.exists():
        prior, prior_raw = read_json(intent_path, "stop_intent", 2 * 1024 * 1024)
        require(
            prior.get("policy_sha256") == policy_sha
            and prior.get("company_id") == policy["company_id"],
            "stop_intent_binding_mismatch",
        )
        evidence, intent_sha = prior, digest(prior_raw)
        if prior.get("process_run_id") != api.run_id:
            company = api_owner.get(company_path)
            company = company.get("company", company) if type(company) is dict else {}
            return {
                "schema": "brain-campaign-stop-readback-v1",
                "company_id": policy["company_id"],
                "prior_intent_sha256": intent_sha,
                "company_status": company.get("status"),
                "live_runs": current_live(api_owner, policy["company_id"]),
                "current_process_run_id": api.run_id,
                "replay": "readback_only_prior_attempt",
            }
    else:
        checkpoint = {
            "schema": "brain-campaign-pre-pause-checkpoint-v1",
            "state": "STOP_PENDING",
            "intent": evidence,
            "intent_sha256": digest(encode(evidence)),
            "own_process_run_id": api.run_id,
            "own_process_terminal_observed": False,
        }
        write_once(checkpoint_path, encode(checkpoint), 0o400)
        intent_sha = write_once(intent_path, encode(evidence), 0o400)
    first_attempt = not intent_attempt.exists()
    should_patch = company.get("status") == "active" and not others
    if first_attempt and should_patch:
        write_once(
            intent_attempt,
            encode(
                {
                    "intent_sha256": intent_sha,
                    "before_patch_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                }
            ),
            0o400,
        )
    if first_attempt and should_patch:
        api_owner.patch(company_path, {"status": "paused"})
    try:
        after = api_owner.get(company_path)
        after = after.get("company", after) if type(after) is dict else {}
        live_after = current_live(api_owner, policy["company_id"])
        readback_error = None
    except Exception as exc:
        after, live_after = {}, []
        readback_error = type(exc).__name__
    result = {
        "schema": "brain-campaign-stop-readback-v1",
        "pre_pause_state": "STOP_PENDING",
        "company_id": policy["company_id"],
        "intent_sha256": intent_sha,
        "company_status_before": company.get("status"),
        "company_status_after": after.get("status"),
        "pause_requested": bool(intent_attempt.exists()),
        "pause_attempt_state": (
            "ATTEMPT_MARKED"
            if intent_attempt.exists()
            else "UNATTEMPTED_OTHER_LIVE_RUNS"
            if others
            else "UNATTEMPTED_ALREADY_PAUSED"
            if company.get("status") == "paused"
            else "UNATTEMPTED_COMPANY_NOT_ACTIVE"
            if company.get("status") != "active"
            else "UNATTEMPTED"
        ),
        "other_live_runs_before": others,
        "live_runs_after_if_observed": live_after,
        "readback_error": readback_error,
        "own_process_run_id": api.run_id,
        "own_process_terminal_observed": False,
        "quota_preflight": quota,
        "campaign_run_ledger": run_ledger,
        "owner_desk_status": desk_status,
    }
    receipt = private_root / f"stop-readback-{api.run_id}.json"
    if not receipt.exists():
        write_once(receipt, encode(result), 0o400)
    return result


def validate_control(value: object, policy: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema",
        "company_id",
        "control_issue_id",
        "control_process_agent_id",
        "completion_issue_id",
        "setup_sentinel_issue_id",
        "allowed_finish_blocker_issue_ids",
        "outcome_manager_issue_ids",
        "authority_ref",
        "renewal_ref",
    }
    require(type(value) is dict and set(value) == fields, "control_config_fields_invalid")
    require(value.get("schema") == CONTROL_SCHEMA, "control_config_schema_invalid")
    require(value.get("company_id") == policy["company_id"], "control_company_mismatch")
    for key in ("control_issue_id", "control_process_agent_id"):
        require(type(value[key]) is str and UUID.fullmatch(value[key]), f"control_{key}_invalid")
    require(
        value["completion_issue_id"] == COMPLETION_ISSUE_ID, "control_completion_issue_unpinned"
    )
    require(
        value["setup_sentinel_issue_id"] == SETUP_SENTINEL_ISSUE_ID,
        "control_setup_sentinel_unpinned",
    )
    allowed = value["allowed_finish_blocker_issue_ids"]
    managers = value["outcome_manager_issue_ids"]
    require(
        type(allowed) is list
        and 1 <= len(allowed) <= 32
        and all(type(item) is str and UUID.fullmatch(item) for item in allowed)
        and len(set(allowed)) == len(allowed),
        "control_finish_blocker_scope_invalid",
    )
    require(
        type(managers) is list
        and 1 <= len(managers) <= 16
        and all(type(item) is str and UUID.fullmatch(item) for item in managers)
        and len(set(managers)) == len(managers)
        and set(managers) <= set(allowed),
        "control_outcome_manager_scope_invalid",
    )
    for key in ("authority_ref", "renewal_ref"):
        ref = value[key]
        require(
            type(ref) is dict
            and set(ref) == {"path", "sha256"}
            and type(ref["path"]) is str
            and Path(ref["path"]).is_absolute()
            and type(ref["sha256"]) is str
            and SHA256.fullmatch(ref["sha256"]),
            f"control_{key}_invalid",
        )
    return value


def load_control_reference(path: Path, expected_sha: str) -> tuple[dict[str, Any], str]:
    raw = read_private(path, "control_config", MAX_POLICY_BYTES, readonly=True)
    actual = digest(raw)
    require(
        type(expected_sha) is str and SHA256.fullmatch(expected_sha) and actual == expected_sha,
        "control_config_hash_mismatch",
    )
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalizeError("control_config_invalid_json") from exc
    require(type(value) is dict, "control_config_not_object")
    return value, actual


def _control_authority(
    control: dict[str, Any], policy: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], str]:
    raw = read_private(
        Path(control["authority_ref"]["path"]), "renewal_authority", 64 * 1024, readonly=True
    )
    sha = digest(raw)
    require(sha == control["authority_ref"]["sha256"], "renewal_authority_hash_mismatch")
    try:
        authority = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FinalizeError("renewal_authority_invalid_json") from exc
    fields = {
        "schema",
        "founder_directive_path",
        "founder_directive_sha256",
        "issued_at_utc",
        "deadline_utc",
        "company_id",
        "connection_id",
        "task_scope_root_ids",
        "max_model_runs",
        "quota_heavy_admission_stop_percent",
        "quota_minimum_target_percent",
        "resource_policy_sha256",
    }
    require(
        type(authority) is dict and set(authority) == fields, "renewal_authority_fields_invalid"
    )
    require(authority["schema"] == AUTHORITY_SCHEMA, "renewal_authority_schema_invalid")
    require(
        type(authority["founder_directive_path"]) is str
        and authority["founder_directive_path"]
        in {str(SOURCE_DIRECTIVE_PATH), str(FROZEN_DIRECTIVE_PATH)}
        and type(authority["founder_directive_sha256"]) is str
        and SHA256.fullmatch(authority["founder_directive_sha256"])
        and authority["company_id"] == policy["company_id"]
        and UUID.fullmatch(str(authority["connection_id"]))
        and type(authority["task_scope_root_ids"]) is list
        and 1 <= len(authority["task_scope_root_ids"]) <= 32
        and all(
            type(item) is str and UUID.fullmatch(item) for item in authority["task_scope_root_ids"]
        )
        and len(set(authority["task_scope_root_ids"])) == len(authority["task_scope_root_ids"])
        and authority["max_model_runs"] is None
        and type(authority["quota_heavy_admission_stop_percent"]) in {int, float}
        and authority["quota_heavy_admission_stop_percent"] == 8
        and type(authority["quota_minimum_target_percent"]) in {int, float}
        and authority["quota_minimum_target_percent"] == 5
        and type(authority["resource_policy_sha256"]) is str
        and SHA256.fullmatch(authority["resource_policy_sha256"]),
        "renewal_authority_scope_or_limits_invalid",
    )
    verify_founder_directive(
        Path(authority["founder_directive_path"]), authority["founder_directive_sha256"]
    )
    issued = utc(authority["issued_at_utc"], "renewal_authority_issued_at_invalid")
    deadline = utc(authority["deadline_utc"], "renewal_authority_deadline_invalid")
    require(
        issued < deadline <= issued + timedelta(hours=12),
        "renewal_authority_bounds_invalid",
    )
    renewal_raw = read_private(
        Path(control["renewal_ref"]["path"]),
        "resource_policy_renewal",
        MAX_POLICY_BYTES,
        readonly=True,
    )
    require(
        digest(renewal_raw) == control["renewal_ref"]["sha256"],
        "resource_policy_renewal_hash_mismatch",
    )
    resource_ref = policy["resource_policy_ref"]
    require(
        type(resource_ref) is dict
        and resource_ref.get("sha256") == authority["resource_policy_sha256"],
        "renewal_original_resource_policy_missing",
    )
    # The sibling resource guard validates renewal lineage, company, connection and scope.
    prior = os.environ.get(resource_guard.RENEWAL_ENV)
    renewal_path = control["renewal_ref"]["path"]
    require(prior in (None, renewal_path), "renewal_environment_mismatch")
    os.environ[resource_guard.RENEWAL_ENV] = renewal_path
    try:
        effective, effective_sha = resource_guard.load_policy_reference(
            resource_ref, policy["company_id"], allow_v1_to_v2=True
        )
    except Exception as exc:
        raise FinalizeError("renewal_resource_policy_invalid") from exc
    require(
        effective_sha == control["renewal_ref"]["sha256"]
        and effective["deadline_utc"] == authority["deadline_utc"]
        and effective["schema"] == resource_guard.POLICY_SCHEMA_V2
        and effective["max_model_runs"] == authority["max_model_runs"]
        and effective["min_remaining_percent"] == authority["quota_heavy_admission_stop_percent"]
        and effective["company_id"] == authority["company_id"]
        and effective["connection_id"] == authority["connection_id"]
        and effective["task_scope_root_ids"] == authority["task_scope_root_ids"]
        and effective["task_scope_root_ids"] == policy["campaign_root_ids"],
        "renewal_resource_policy_bounds_mismatch",
    )
    return authority, effective, sha


def make_control_api(
    policy: dict[str, Any], control: dict[str, Any], task_id: str | None = None
) -> delivery.Paperclip:
    task_id = task_id or control["control_issue_id"]
    actor_id = control["control_process_agent_id"]
    api = delivery.Paperclip(
        {
            "api_origin": "http://127.0.0.1:3310",
            "company_id": policy["company_id"],
            "publish": {"task_id": task_id, "agent_id": actor_id},
        }
    )
    actor = api.request("GET", "/agents/me")
    run = api.request("GET", f"/heartbeat-runs/{api.run_id}")
    issue = api.issue(task_id)
    context = run.get("contextSnapshot") if type(run) is dict else None
    parent = issue.get("parentId") if type(issue) is dict else None
    require(
        type(actor) is dict
        and actor.get("id") == actor_id
        and actor.get("companyId") == policy["company_id"],
        "control_process_actor_mismatch",
    )
    require(
        type(run) is dict
        and run.get("id") == api.run_id
        and run.get("companyId") == policy["company_id"]
        and run.get("agentId") == actor_id
        and run.get("issueId") == task_id
        and run.get("runtimeMode") == "legacy"
        and run.get("status") == "running"
        and type(context) is dict
        and context.get("issueId") == task_id,
        "control_process_run_invalid",
    )
    require(
        type(issue) is dict
        and issue.get("id") == task_id
        and issue.get("companyId") == policy["company_id"]
        and issue.get("assigneeAgentId") == actor_id
        and issue.get("status") in {"todo", "in_progress"}
        and issue.get("executionRunId") == api.run_id
        and parent in policy["campaign_root_ids"],
        "control_process_task_invalid",
    )
    # The interruption field is a presentation projection and never grants control authority.
    if task_id == control["control_issue_id"]:
        require(
            issue.get("blockedBy") == []
            and issue.get("executionBlocker") is None
            and issue.get("activeRecoveryAction") is None,
            "control_monitor_not_dependency_free",
        )
    # Existing legacy process tasks may have executionRunId before checkoutRunId.
    # Reuse the established delivery checkout path only after actor/run/task identity is pinned.
    require(api.current("publish") == task_id, "control_process_task_checkout_invalid")
    checked_out = api.issue(task_id)
    require(
        type(checked_out) is dict
        and checked_out.get("id") == task_id
        and checked_out.get("companyId") == policy["company_id"]
        and checked_out.get("assigneeAgentId") == actor_id
        and checked_out.get("status") == "in_progress"
        and checked_out.get("executionRunId") == api.run_id
        and checked_out.get("checkoutRunId") == api.run_id
        and checked_out.get("parentId") in policy["campaign_root_ids"],
        "control_process_task_checkout_readback_invalid",
    )
    return api


def _control_stop(
    policy: dict[str, Any],
    control: dict[str, Any],
    api: delivery.Paperclip,
    reason: str,
    authority_sha: str,
    renewal_sha: str,
    count: int | None,
    blocker_readback: list[dict[str, Any]] | None = None,
    schedule_effect: str | None = None,
) -> dict[str, Any]:
    root = Path(policy["private_evidence_root"])
    intent_path = root / "renewal-stop-intent.json"
    checkpoint_path = root / "renewal-stop-checkpoint.json"
    projection_path = root / "RECOVERY-STOP-PENDING.md"
    projection = (
        "Recovery campaign stopped at its finite resource boundary. Product acceptance remains "
        "unassessed; independent QA/Astra completion and child-task status are not inferred. "
        "The Board pause request may have cancelled the process. STOP_PENDING is durable; a later "
        "native readback must establish company and process status.\n"
    )
    projection_sha = write_once(projection_path, projection.encode(), 0o400)
    trusted = company_handoff.PaperclipAPI()
    try:
        live_before: object = current_live(trusted, policy["company_id"])
    except Exception:
        live_before = {"status": "unknown"}
    try:
        company_before_value = trusted.get(f"/companies/{policy['company_id']}")
        company_before_value = (
            company_before_value.get("company", company_before_value)
            if type(company_before_value) is dict
            else {}
        )
        company_status_before = company_before_value.get("status")
    except Exception:
        company_status_before = None
    intent = {
        "schema": "brain-campaign-renewal-stop-intent-v1",
        "state": "STOP_PENDING",
        "company_id": policy["company_id"],
        "control_issue_id": control["control_issue_id"],
        "control_process_agent_id": control["control_process_agent_id"],
        "completion_issue_id": control["completion_issue_id"],
        "process_run_id": api.run_id,
        "reason": reason,
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "company_status_before": company_status_before,
        "pending_live_runs_before_pause": live_before,
        "authority_sha256": authority_sha,
        "renewal_sha256": renewal_sha,
        "original_resource_policy_sha256": policy["resource_policy_ref"]["sha256"],
        "observed_model_run_count": count,
        "projection_path": str(projection_path),
        "projection_sha256": projection_sha,
        "completion_blocker_readback": blocker_readback,
        "control_schedule_effect": schedule_effect,
    }
    if intent_path.exists():
        old, raw = read_json(intent_path, "renewal_stop_intent", 32 * 1024)
        require(
            old.get("schema") == "brain-campaign-renewal-stop-intent-v1"
            and old.get("state") == "STOP_PENDING"
            and old.get("company_id") == policy["company_id"]
            and old.get("control_issue_id") == control["control_issue_id"]
            and old.get("control_process_agent_id") == control["control_process_agent_id"]
            and old.get("completion_issue_id") == control["completion_issue_id"]
            and old.get("authority_sha256") == authority_sha
            and old.get("renewal_sha256") == renewal_sha
            and old.get("original_resource_policy_sha256")
            == policy["resource_policy_ref"]["sha256"],
            "renewal_stop_intent_binding_mismatch",
        )
        intent = old
        intent_sha = digest(raw)
    else:
        intent_sha = write_once(intent_path, encode(intent), 0o400)
    if not checkpoint_path.exists():
        write_once(
            checkpoint_path,
            encode(
                {
                    "schema": "brain-campaign-renewal-pre-pause-v1",
                    "state": "STOP_PENDING",
                    "intent_sha256": intent_sha,
                    "company_id": intent["company_id"],
                    "process_run_id": intent["process_run_id"],
                    "own_process_terminal_observed": False,
                    "authority_sha256": intent["authority_sha256"],
                    "renewal_sha256": intent["renewal_sha256"],
                    "original_resource_policy_sha256": intent["original_resource_policy_sha256"],
                    "reason": intent["reason"],
                    "observed_model_run_count": intent.get("observed_model_run_count"),
                    "completion_blocker_readback": intent.get("completion_blocker_readback"),
                    "completion_blocker_readback_sha256": (
                        digest(encode(intent["completion_blocker_readback"]))
                        if intent.get("completion_blocker_readback") is not None
                        else None
                    ),
                    "control_schedule_effect": intent.get("control_schedule_effect"),
                }
            ),
            0o400,
        )
    else:
        checkpoint, _ = read_json(checkpoint_path, "renewal_stop_checkpoint", 32 * 1024)
        require(
            checkpoint.get("state") == "STOP_PENDING"
            and checkpoint.get("intent_sha256") == intent_sha,
            "renewal_stop_checkpoint_binding_mismatch",
        )
    marker = root / "renewal-stop-attempted"
    if not marker.exists():
        write_once(marker, encode({"intent_sha256": intent_sha}), 0o400)
        trusted.patch(f"/companies/{policy['company_id']}", {"status": "paused"})
        pause_attempted = True
    else:
        pause_attempted = False
    status_after = None
    try:
        value = company_handoff.PaperclipAPI().get(f"/companies/{policy['company_id']}")
        value = value.get("company", value) if type(value) is dict else {}
        status_after = value.get("status")
    except Exception:
        pass
    return {
        "state": "STOP_PENDING",
        "reason": intent["reason"],
        "intent_sha256": intent_sha,
        "company_status_after_if_observed": status_after,
        "pause_attempted_this_tick": pause_attempted,
        "pause_attempt_state": "ATTEMPTED_THIS_TICK"
        if pause_attempted
        else "READBACK_ONLY_PRIOR_ATTEMPT",
        "own_process_run_id": api.run_id,
        "own_process_terminal_observed": False,
        "full_stop_claim": False,
    }


def control_tick(
    policy: dict[str, Any],
    control: dict[str, Any],
    api: delivery.Paperclip,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One task-scheduled process tick. It schedules one next tick or persists STOP_PENDING."""
    current = now or datetime.now(UTC)
    require(
        current.tzinfo is not None and current.utcoffset() == timedelta(0), "control_clock_invalid"
    )
    authority, effective, authority_sha = _control_authority(control, policy)
    deadline = utc(authority["deadline_utc"], "renewal_deadline_invalid")
    renewal_sha = control["renewal_ref"]["sha256"]
    count: int | None = None
    resource_observation: dict[str, Any] | None = None
    resource_observation_sha256: str | None = None
    reason = None
    try:
        if effective["max_model_runs"] is None:
            require(
                effective["schema"] == resource_guard.POLICY_SCHEMA_V2
                and authority["max_model_runs"] is None,
                "renewal_uncapped_policy_authority_mismatch",
            )
            run_ids = None
        else:
            run_ids = resource_guard.campaign_model_run_ids(api, policy["company_id"], effective)
            count = len(run_ids)
        current = now or datetime.now(UTC)
        require(
            current.tzinfo is not None and current.utcoffset() == timedelta(0),
            "control_clock_invalid",
        )
        if current >= deadline:
            reason = "renewal_deadline_reached"
        elif (
            authority["max_model_runs"] is not None
            and count is not None
            and count >= authority["max_model_runs"]
        ):
            reason = "renewal_run_cap_reached"
        else:
            receipt = (
                Path(policy["private_evidence_root"]) / f"renewal-tick-quota-{api.run_id}.json"
            )
            resource_observation = resource_guard.preflight(
                effective,
                policy["company_id"],
                "prepare",
                run_ids,
                receipt,
                now=now,
            )
            try:
                resource_observation_sha256 = digest(receipt.read_bytes())
            except OSError:
                pass
            current = now or datetime.now(UTC)
            require(
                current.tzinfo is not None and current.utcoffset() == timedelta(0),
                "control_clock_invalid",
            )
            if current >= deadline:
                reason = "renewal_deadline_reached"
    except Exception as exc:
        reason = (
            "renewal_deadline_reached"
            if isinstance(exc, resource_guard.ResourceGuardError)
            and str(exc) == "resource_policy_deadline_expired"
            else "renewal_quota_or_readback_invalid"
        )
    if reason is not None:
        return _control_stop(policy, control, api, reason, authority_sha, renewal_sha, count)

    try:
        issue = api.issue(control["control_issue_id"])
        current = now or datetime.now(UTC)
        require(
            current.tzinfo is not None and current.utcoffset() == timedelta(0),
            "control_clock_invalid",
        )
        require(current < deadline, "renewal_deadline_reached")
        require(
            type(issue) is dict
            and issue.get("companyId") == policy["company_id"]
            and issue.get("status") == "in_progress"
            and issue.get("assigneeAgentId") == control["control_process_agent_id"]
            and issue.get("blockedBy") == []
            and issue.get("parentId") in policy["campaign_root_ids"]
            and issue.get("executionBlocker") is None
            and issue.get("activeRecoveryAction") is None,
            "control_task_changed_before_schedule",
        )
        execution = issue.get("executionPolicy")
        require(execution is None or type(execution) is dict, "control_execution_policy_invalid")
        next_check = min(current + timedelta(minutes=5), deadline)
        execution = dict(execution) if type(execution) is dict else {}
        execution["monitor"] = {
            "nextCheckAt": next_check.isoformat().replace("+00:00", "Z"),
            "notes": (
                "Bounded renewal control tick; next wake rechecks deadline, run count, and quota."
            ),
            "scheduledBy": "board",
            "kind": "external_service",
            "serviceName": "campaign recovery control",
            "maxAttempts": None,
        }
        api.request(
            "PATCH",
            f"/issues/{control['control_issue_id']}",
            {
                "status": "in_progress",
                "assigneeAgentId": control["control_process_agent_id"],
                "executionPolicy": execution,
            },
        )
        returned = api.issue(control["control_issue_id"])
        monitor = (
            returned.get("executionPolicy", {}).get("monitor")
            if type(returned.get("executionPolicy")) is dict
            else None
        )
        require(
            type(returned) is dict
            and returned.get("id") == control["control_issue_id"]
            and returned.get("companyId") == policy["company_id"]
            and returned.get("status") == "in_progress"
            and returned.get("assigneeAgentId") == control["control_process_agent_id"]
            and returned.get("blockedBy") == []
            and type(monitor) is dict
            and monitor.get("nextCheckAt") == execution["monitor"]["nextCheckAt"],
            "control_schedule_readback_invalid",
        )
    except Exception:
        return _control_stop(
            policy,
            control,
            api,
            "control_schedule_unconfirmed",
            authority_sha,
            renewal_sha,
            count,
            schedule_effect="unknown_no_retry",
        )
    return {
        "state": "CONTINUATION_SCHEDULED",
        "company_id": policy["company_id"],
        "control_issue_id": control["control_issue_id"],
        "next_check_at": monitor["nextCheckAt"],
        "observed_model_run_count": count,
        "authority_sha256": authority_sha,
        "renewal_sha256": renewal_sha,
        "product_acceptance_claim": False,
        "resource_observation": resource_observation,
        "resource_observation_sha256": resource_observation_sha256,
    }


def control_finish(
    policy: dict[str, Any],
    control: dict[str, Any],
    api: delivery.Paperclip,
) -> dict[str, Any]:
    """Pause after 217's native completion signal and validated resolved blockers."""
    require(
        api.m["publish"]["task_id"] == control["completion_issue_id"],
        "control_finish_task_mismatch",
    )
    issue = api.issue(control["completion_issue_id"])
    require(
        type(issue) is dict
        and issue.get("id") == control["completion_issue_id"]
        and issue.get("companyId") == policy["company_id"]
        and issue.get("assigneeAgentId") == control["control_process_agent_id"]
        and issue.get("status") == "in_progress"
        and issue.get("parentId") == control["setup_sentinel_issue_id"],
        "control_finish_issue_identity_invalid",
    )
    blockers = issue.get("blockedBy")
    require(type(blockers) is list and len(blockers) > 0, "control_finish_blocker_readback_empty")
    blocker_ids = [row.get("id") for row in blockers if type(row) is dict]
    require(
        len(blocker_ids) == len(blockers)
        and len(set(blocker_ids)) == len(blocker_ids)
        and control["setup_sentinel_issue_id"] not in blocker_ids
        and set(blocker_ids) <= set(control["allowed_finish_blocker_issue_ids"]),
        "control_finish_blocker_scope_invalid",
    )
    require(
        set(blocker_ids) & set(control["outcome_manager_issue_ids"]),
        "control_finish_manager_signal_missing",
    )
    blocker_readback = []
    for summary in blockers:
        native = api.issue(summary["id"])
        require(
            type(native) is dict
            and native.get("id") == summary["id"]
            and native.get("companyId") == policy["company_id"]
            and native.get("status") == "done"
            and summary.get("status") == native.get("status"),
            "control_finish_blockers_unresolved",
        )
        blocker_readback.append(
            {
                "id": native["id"],
                "company_id": native["companyId"],
                "status": native["status"],
                "identifier": native.get("identifier"),
            }
        )
    attention = issue.get("blockerAttention")
    require(
        type(attention) is dict
        and attention.get("state") == "none"
        and attention.get("unresolvedBlockerCount") == 0
        and attention.get("pendingFinalizeBlockerIssueIds", []) == [],
        "control_finish_blocker_attention_active",
    )
    require(
        issue.get("executionBlocker") is None
        and issue.get("activeRecoveryAction") is None
        and issue.get("blockedInboxAttention") is None,
        "control_finish_native_hold_present",
    )
    _authority, _effective, authority_sha = _control_authority(control, policy)
    return _control_stop(
        policy,
        control,
        api,
        "native_completion_issue_217_blockers_resolved",
        authority_sha,
        control["renewal_ref"]["sha256"],
        None,
        blocker_readback,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("final-evidence", "stop", "control-tick", "control-finish")
    )
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--policy-sha256", required=True)
    parser.add_argument("--control", type=Path)
    parser.add_argument("--control-sha256")
    args = parser.parse_args(argv)
    try:
        policy, policy_sha = load_policy(args.policy, args.policy_sha256)
        if args.action in {"control-tick", "control-finish"}:
            require(
                args.control is not None and args.control_sha256 is not None,
                "control_config_reference_required",
            )
            control_value, _ = load_control_reference(args.control, args.control_sha256)
            control = validate_control(control_value, policy)
            _control_authority(control, policy)
            task_id = (
                control["completion_issue_id"]
                if args.action == "control-finish"
                else control["control_issue_id"]
            )
            api = make_control_api(policy, control, task_id)
            result = (
                control_finish(policy, control, api)
                if args.action == "control-finish"
                else control_tick(policy, control, api)
            )
            print(json.dumps(result, sort_keys=True, ensure_ascii=False))
            return 0
        validate_outcomes(policy)
        require(
            args.control is None and args.control_sha256 is None,
            "unexpected_control_config_reference",
        )
        task_id = (
            policy["final_evidence_task_id"]
            if args.action == "final-evidence"
            else policy["stop_task_id"]
        )
        api = make_process_api(policy, task_id)
        result = (
            final_evidence(policy, policy_sha, api)
            if args.action == "final-evidence"
            else stop_campaign(policy, policy_sha, api)
        )
        print(json.dumps(result, sort_keys=True, ensure_ascii=False))
        return 0
    except Exception as exc:
        # Never print exception bodies from remote services or file contents.
        error = {"status": "blocked", "error": type(exc).__name__}
        reason_code = str(exc)
        if isinstance(exc, FinalizeError) and re.fullmatch(r"[a-z][a-z0-9_]{0,120}", reason_code):
            error["reason_code"] = reason_code
        print(
            json.dumps(error, sort_keys=True),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
