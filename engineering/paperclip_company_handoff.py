"""Small founder-operated handoff wrapper around Paperclip company lifecycle APIs.

Paperclip remains the task queue and lifecycle authority. This module only pauses,
observes, records a private handoff, and resumes after a founder command plus a
fresh fail-closed reconciliation. It does not infer OS process quiescence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engineering.paperclip_git_broker import (
    BrokerError,
    begin_company_drain,
    clear_company_drain,
    company_operation,
    read_company_fence,
    record_company_resume_intent,
    safe_git,
)

API_BASE = "http://127.0.0.1:3310/api"
HTTP_TIMEOUT_SECONDS = 5
MAX_RUNS = 1000
PAGE_SIZE = 100
ACTIVE_RUN_STATES = frozenset({"queued", "running", "scheduled_retry"})
UNRESOLVED_RUN_STATES = frozenset({"failed", "cancelled", "interrupted", "timed_out"})
KNOWN_RUN_STATES = (
    ACTIVE_RUN_STATES | UNRESOLVED_RUN_STATES | frozenset({"succeeded", "failed", "cancelled"})
)
KNOWN_AGENT_STATUSES = frozenset(
    {"active", "paused", "idle", "running", "error", "pending_approval", "terminated"}
)
KNOWN_AGENT_LIFECYCLE = frozenset(
    {
        "pending_approval",
        "preparing",
        "verifying",
        "ready",
        "pausing",
        "paused",
        "resuming",
        "terminating",
        "cleaning_up",
        "terminated",
        "rejected",
    }
)
TRANSITIONAL_AGENT_LIFECYCLE = frozenset(
    {"preparing", "verifying", "pausing", "resuming", "terminating", "cleaning_up"}
)
KNOWN_ISSUE_STATUSES = frozenset(
    {"backlog", "todo", "in_progress", "in_review", "done", "blocked", "cancelled"}
)
TERMINAL_BROKER_STATES = frozenset({"published", "rejected"})
KNOWN_BROKER_STATES = frozenset(
    {
        "preparing",
        "prepared",
        "verification_intent",
        "publish_intent",
        "held_unknown",
        "published",
        "rejected",
    }
)
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class HandoffError(RuntimeError):
    pass


class PaperclipAPI:
    """Unauthenticated local API client. Auth is supplied only by local Paperclip policy."""

    def __init__(self, request: Callable[[str, str, dict[str, Any] | None], Any] | None = None):
        self._request = request or self._http_request

    @staticmethod
    def _http_request(method: str, path: str, body: dict[str, Any] | None) -> Any:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        request = urllib.request.Request(
            f"{API_BASE}{path}",
            data=data,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise HandoffError(
                f"Paperclip API {method} {path} failed: {type(exc).__name__}"
            ) from exc

    def get(self, path: str) -> Any:
        return self._request("GET", path, None)

    def patch(self, path: str, body: dict[str, Any]) -> Any:
        return self._request("PATCH", path, body)


def _id(value: Any, label: str) -> str:
    if type(value) is not str or not ID_RE.fullmatch(value):
        raise HandoffError(f"invalid {label}")
    return value


def _rows(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise HandoffError(f"{label} response is not a list of objects")
    return value


def _allow(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: row.get(key) for key in keys if key in row}


def _company_path(company_id: str, tail: str = "") -> str:
    return f"/companies/{urllib.parse.quote(company_id, safe='')}{tail}"


def _repo_refs(paths: tuple[Path, ...], problems: list[str]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for path in paths:
        try:
            branch = safe_git(path, ("rev-parse", "--abbrev-ref", "HEAD")).stdout.decode().strip()
            head = safe_git(path, ("rev-parse", "HEAD")).stdout.decode().strip()
            dirty = safe_git(path, ("status", "--porcelain")).stdout.strip() != b""
            if not branch or not re.fullmatch(r"[0-9a-f]{40,64}", head):
                raise ValueError("missing branch or invalid HEAD")
            refs.append({"name": path.name, "branch": branch, "head": head, "dirty": dirty})
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            problems.append(f"repo:{path.name}:unknown:{type(exc).__name__}")
    return refs


def _snapshot(
    api: PaperclipAPI,
    company_id: str,
    broker_journal: Path | None,
    repo_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    problems: list[str] = []
    snapshot: dict[str, Any] = {"company_id": company_id}
    try:
        company = api.get(_company_path(company_id))
        if (
            not isinstance(company, dict)
            or company.get("id") != company_id
            or company.get("status") not in {"active", "paused", "archived"}
        ):
            raise HandoffError("company response identity or status is missing/unknown")
        snapshot["company"] = _allow(company, ("id", "status", "updatedAt"))
    except HandoffError as exc:
        problems.append(f"company:{exc}")

    try:
        agents = _rows(api.get(_company_path(company_id, "/agents")), "agents")
        snapshot["agents"] = []
        for row in agents:
            if row.get("companyId", company_id) != company_id or not isinstance(row.get("id"), str):
                raise HandoffError("agent identity is missing or belongs to another company")
            if (
                row.get("status") not in KNOWN_AGENT_STATUSES
                or row.get("lifecycleState") not in KNOWN_AGENT_LIFECYCLE
            ):
                problems.append(f"agent:{row['id']}:unknown_status")
            snapshot["agents"].append(_allow(row, ("id", "status", "lifecycleState")))
    except HandoffError as exc:
        problems.append(f"agents:{exc}")

    try:
        issues: list[dict[str, Any]] = []
        offset = 0
        while True:
            query = urllib.parse.urlencode(
                {"limit": PAGE_SIZE, "offset": offset, "view": "compact"}
            )
            page = _rows(api.get(_company_path(company_id, f"/issues?{query}")), "issues")
            for row in page:
                if row.get("companyId", company_id) != company_id or not isinstance(
                    row.get("id"), str
                ):
                    raise HandoffError("issue identity is missing or belongs to another company")
                if row.get("status") not in KNOWN_ISSUE_STATUSES:
                    problems.append(f"issue:{row['id']}:unknown_status")
                issues.append(_allow(row, ("id", "status", "assigneeAgentId", "updatedAt")))
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
            if offset > 100_000:
                raise HandoffError("issue listing exceeded bounded pagination")
        snapshot["tasks"] = issues
    except HandoffError as exc:
        problems.append(f"issues:{exc}")

    try:
        runs = _rows(
            api.get(_company_path(company_id, f"/heartbeat-runs?limit={MAX_RUNS}&summary=true")),
            "heartbeat runs",
        )
        snapshot["runs"] = []
        for row in runs:
            if row.get("companyId", company_id) != company_id or not isinstance(row.get("id"), str):
                raise HandoffError("run identity is missing or belongs to another company")
            if row.get("status") not in KNOWN_RUN_STATES:
                problems.append(f"run:{row['id']}:unknown_status")
            snapshot["runs"].append(_allow(row, ("id", "status", "agentId", "issueId")))
        if len(runs) >= MAX_RUNS:
            problems.append("runs:listing_may_be_truncated")
    except HandoffError as exc:
        problems.append(f"runs:{exc}")

    try:
        snapshot["broker_journal"] = _read_broker_journal(broker_journal, company_id, problems)
    except OSError as exc:
        problems.append(f"broker_journal:unreadable:{type(exc).__name__}")
        snapshot["broker_journal"] = []

    try:
        snapshot["company_fence"] = (
            read_company_fence(broker_journal, company_id) if broker_journal is not None else None
        )
    except BrokerError as exc:
        problems.append(f"company_fence:unknown:{type(exc).__name__}")

    snapshot["repository_refs"] = _repo_refs(repo_paths, problems)

    snapshot["unresolved"] = _unresolved(snapshot, problems)
    snapshot["problems"] = problems
    snapshot["complete"] = not problems
    return snapshot


def _read_broker_journal(
    path: Path | None, company_id: str, problems: list[str]
) -> list[dict[str, Any]]:
    if path is None:
        problems.append("broker_journal:path_not_configured")
        return []
    if path.is_symlink() or not path.is_dir():
        problems.append("broker_journal:path_missing_invalid_or_not_directory")
        return []
    entries: list[dict[str, Any]] = []
    try:
        files = sorted(path.glob("*.json"))
    except OSError as exc:
        problems.append(f"broker_journal:listing_failed:{type(exc).__name__}")
        return []
    for file_path in files:
        try:
            no_follow = getattr(os, "O_NOFOLLOW", None)
            if no_follow is None:
                raise OSError("journal no-follow open is unavailable")
            fd = os.open(
                file_path,
                os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | no_follow,
            )
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 256_000:
                    raise ValueError("not a bounded regular file")
                raw = stream.read(256_001)
            if len(raw) > 256_000:
                raise ValueError("not a bounded regular file")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("journal record is not an object")
            schema = value.get("schema")
            if schema == "paperclip-company-fence-v1":
                if (
                    type(value.get("company_id")) is not str
                    or not ID_RE.fullmatch(value["company_id"])
                    or value.get("state") not in {"draining", "resume_intent", "open"}
                ):
                    raise ValueError("company fence identity or state is invalid")
                continue
            if schema != "paperclip-git-operation-v1":
                raise ValueError("unknown journal schema")
            binding = value.get("binding")
            identities = (
                "company_id",
                "issue_id",
                "run_id",
                "worker_agent_id",
                "reviewer_agent_id",
            )
            if (
                not isinstance(binding, dict)
                or not all(
                    type(binding.get(field)) is str and ID_RE.fullmatch(binding[field])
                    for field in identities
                )
                or binding["worker_agent_id"] == binding["reviewer_agent_id"]
                or type(binding.get("base_sha")) is not str
                or not re.fullmatch(r"[0-9a-f]{40,64}", binding["base_sha"])
                or type(binding.get("submitted_sha")) is not str
                or not re.fullmatch(r"[0-9a-f]{40,64}", binding["submitted_sha"])
                or type(binding.get("policy_digest")) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", binding["policy_digest"])
                or type(binding.get("scope")) is not list
                or not binding["scope"]
                or not all(type(item) is str and item for item in binding["scope"])
                or not isinstance(value.get("operation_key"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", value["operation_key"])
                or value["operation_key"]
                != hashlib.sha256(
                    json.dumps(
                        [binding.get("company_id"), binding.get("issue_id"), binding.get("run_id")],
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest()
                or value.get("state") not in KNOWN_BROKER_STATES
            ):
                raise ValueError("nested operation binding identity is missing or invalid")
            if binding["company_id"] != company_id:
                continue
            state = value.get("state")
            if not isinstance(state, str) or not state:
                raise ValueError("missing operation state")
            entries.append(
                {
                    "operation_key": value.get("operation_key"),
                    "issue_id": binding["issue_id"],
                    "run_id": binding["run_id"],
                    "state": state,
                }
            )
            if state not in TERMINAL_BROKER_STATES:
                problems.append(
                    f"broker_journal:{value.get('operation_key', 'unknown')}:unresolved_{state}"
                )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            problems.append(f"broker_journal:{file_path.name}:unknown:{type(exc).__name__}")
    return entries


def _unresolved(snapshot: dict[str, Any], problems: list[str]) -> list[dict[str, Any]]:
    unresolved: list[dict[str, Any]] = []
    for run in snapshot.get("runs", []):
        if run.get("status") != "succeeded":
            unresolved.append({"kind": "run", "id": run.get("id"), "status": run.get("status")})
    for agent in snapshot.get("agents", []):
        if (
            agent.get("status") == "running"
            or agent.get("lifecycleState") in TRANSITIONAL_AGENT_LIFECYCLE
        ):
            unresolved.append(
                {
                    "kind": "agent",
                    "id": agent.get("id"),
                    "status": agent.get("status"),
                    "lifecycleState": agent.get("lifecycleState"),
                }
            )
    if problems:
        unresolved.extend({"kind": "unknown", "detail": problem} for problem in problems)
    return unresolved


def _private_write(directory: Path, payload: dict[str, Any]) -> Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise HandoffError("handoff directory must not be a symlink")
    os.chmod(directory, 0o700)
    name = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}.json"
    target = directory / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(target, flags, 0o600)
    try:
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return target


def _quiescent(snapshot: dict[str, Any]) -> bool:
    return snapshot.get("complete") is True and not snapshot.get("unresolved")


def _active_quiescent_snapshot(
    api: PaperclipAPI,
    company_id: str,
    broker_journal: Path,
    repo_paths: tuple[Path, ...],
) -> dict[str, Any]:
    current = _snapshot(api, company_id, broker_journal, repo_paths)
    if current.get("company", {}).get("status") != "active" or not _quiescent(current):
        raise HandoffError("active company state is incomplete or unresolved")
    latest = _snapshot(api, company_id, broker_journal, repo_paths)
    if (
        latest != current
        or latest.get("company", {}).get("status") != "active"
        or not _quiescent(latest)
    ):
        raise HandoffError("active company state changed during reconciliation")
    return latest


def _snapshot_digest(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _require_journal_directory(path: Path | None) -> Path:
    if path is None or path.is_symlink() or not path.is_dir():
        raise HandoffError("broker journal directory is missing or invalid; company action is held")
    return path


def hibernate(
    api: PaperclipAPI,
    company_id: str,
    handoff_dir: Path,
    broker_journal: Path | None,
    deadline_seconds: float = 30.0,
    poll_seconds: float = 1.0,
    repo_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    company_id = _id(company_id, "company id")
    journal = _require_journal_directory(broker_journal)
    try:
        with company_operation(journal, company_id):
            begin_company_drain(
                journal,
                company_id,
                hashlib.sha256(
                    f"hibernate-start:{company_id}:{time.time_ns()}".encode()
                ).hexdigest(),
            )
            return _hibernate_locked(
                api, company_id, handoff_dir, journal, deadline_seconds, poll_seconds, repo_paths
            )
    except BrokerError as exc:
        raise HandoffError(f"company handoff fence failed closed: {exc}") from exc


def _hibernate_locked(
    api: PaperclipAPI,
    company_id: str,
    handoff_dir: Path,
    broker_journal: Path | None,
    deadline_seconds: float = 30.0,
    poll_seconds: float = 1.0,
    repo_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    company_id = _id(company_id, "company id")
    if deadline_seconds < 0 or poll_seconds <= 0:
        raise HandoffError("invalid hibernation deadline")
    patch_result: dict[str, Any] | None = None
    try:
        company = api.get(_company_path(company_id))
        if (
            not isinstance(company, dict)
            or company.get("id") != company_id
            or company.get("status") not in {"active", "paused"}
        ):
            raise HandoffError("company identity/status is missing, unknown, or archived")
        if company["status"] != "paused":
            patch_result = api.patch(_company_path(company_id), {"status": "paused"})
    except HandoffError as exc:
        patch_result = {"error": type(exc).__name__}

    deadline = time.monotonic() + deadline_seconds
    snapshots: list[dict[str, Any]] = []
    while True:
        snapshot = _snapshot(api, company_id, broker_journal, repo_paths)
        snapshots.append(snapshot)
        if snapshot.get("company", {}).get("status") == "paused" and _quiescent(snapshot):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))

    final = snapshots[-1]
    complete = final.get("company", {}).get("status") == "paused" and _quiescent(final)
    payload = {
        "schema": "paperclip-company-handoff-v1",
        "operation": "hibernate",
        "created_at": datetime.now(UTC).isoformat(),
        "company_id": company_id,
        "company_patch": {
            "requested": {"status": "paused"},
            "response_status": (patch_result or {}).get("status"),
        },
        "complete": complete,
        "quiescence_basis": "Paperclip API statuses only; no OS process proof",
        "poll_count": len(snapshots),
        "snapshot": final,
    }
    path = _private_write(handoff_dir, payload)
    return {"complete": complete, "handoff_path": str(path), "snapshot": final}


def resume(
    api: PaperclipAPI,
    company_id: str,
    founder_command_id: str,
    handoff_dir: Path,
    broker_journal: Path | None,
    repo_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    company_id = _id(company_id, "company id")
    founder_command_id = _id(founder_command_id, "founder command id")
    journal = _require_journal_directory(broker_journal)
    try:
        with company_operation(journal, company_id):
            fence = read_company_fence(journal, company_id)
            if fence is None:
                raise HandoffError("resume requires a durable prior company drain")
            if fence["state"] == "resume_intent":
                if fence.get("founder_command_id") != founder_command_id:
                    raise HandoffError("resume held: another founder command has uncertain outcome")
                try:
                    confirmed = _active_quiescent_snapshot(api, company_id, journal, repo_paths)
                except HandoffError as exc:
                    raise HandoffError(
                        "resume held: prior active PATCH is not confirmed with a fresh "
                        "quiescent snapshot; command will not be resent"
                    ) from exc
                confirmation_digest = _snapshot_digest(confirmed)
                clear_company_drain(
                    journal,
                    company_id,
                    founder_command_id,
                    fence["reconciled_snapshot_sha256"],
                    resume_confirmation_digest=confirmation_digest,
                )
                return {
                    "complete": True,
                    "intent_path": "previous_resume_intent",
                    "company_status": "active",
                    "resume_confirmation_sha256": confirmation_digest,
                    "api_response": {"id": company_id, "status": "active"},
                }
            if fence["state"] != "draining":
                raise HandoffError("resume requires a durably drained company")
            return _resume_locked(
                api, company_id, founder_command_id, handoff_dir, journal, repo_paths
            )
    except BrokerError as exc:
        raise HandoffError(f"company resume fence failed closed: {exc}") from exc


def _resume_locked(
    api: PaperclipAPI,
    company_id: str,
    founder_command_id: str,
    handoff_dir: Path,
    broker_journal: Path | None,
    repo_paths: tuple[Path, ...] = (),
) -> dict[str, Any]:
    company_id = _id(company_id, "company id")
    founder_command_id = _id(founder_command_id, "founder command id")
    current = _snapshot(api, company_id, broker_journal, repo_paths)
    if current.get("company", {}).get("status") != "paused":
        raise HandoffError("resume requires this company to be currently paused")
    blockers = list(current.get("unresolved", []))
    if not current.get("complete") or blockers:
        raise HandoffError("resume held: current same-company state is incomplete or unresolved")
    # Re-read at the mutation boundary. Paperclip's PATCH endpoint has no CAS
    # precondition, so any intervening observed change must hold this request.
    latest = _snapshot(api, company_id, broker_journal, repo_paths)
    if latest != current or not _quiescent(latest):
        raise HandoffError("resume held: same-company state changed during reconciliation")
    intent = {
        "schema": "paperclip-company-handoff-v1",
        "operation": "resume_request",
        "created_at": datetime.now(UTC).isoformat(),
        "company_id": company_id,
        "founder_command_id": founder_command_id,
        "reconciled_snapshot_sha256": _snapshot_digest(current),
        "snapshot": current,
        "state": "explicit_founder_command_recorded_before_company_resume_patch",
    }
    intent_path = _private_write(handoff_dir, intent)
    fence_intent = record_company_resume_intent(
        broker_journal, company_id, founder_command_id, intent["reconciled_snapshot_sha256"]
    )
    result = api.patch(_company_path(company_id), {"status": "active"})
    try:
        confirmed = _active_quiescent_snapshot(api, company_id, broker_journal, repo_paths)
    except HandoffError as exc:
        raise HandoffError(
            f"resume request recorded at {intent_path}; active quiescent state not confirmed"
        ) from exc
    confirmation_digest = _snapshot_digest(confirmed)
    clear_company_drain(
        broker_journal,
        company_id,
        founder_command_id,
        fence_intent["reconciled_snapshot_sha256"],
        resume_confirmation_digest=confirmation_digest,
    )
    return {
        "complete": True,
        "intent_path": str(intent_path),
        "company_status": confirmed["company"]["status"],
        "resume_confirmation_sha256": confirmation_digest,
        "api_response": _allow(result, ("id", "status")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation in ("hibernate", "resume"):
        command = subparsers.add_parser(operation)
        command.add_argument("company_id")
        if operation == "resume":
            command.add_argument("founder_command_id")
        command.add_argument(
            "--handoff-dir", type=Path, default=Path.home() / ".paperclip" / "company-handoffs"
        )
        command.add_argument("--broker-journal", type=Path, required=True)
        command.add_argument("--repo", type=Path, action="append", default=[])
        if operation == "hibernate":
            command.add_argument("--deadline-seconds", type=float, default=30.0)
            command.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    api = PaperclipAPI()
    try:
        if args.operation == "hibernate":
            result = hibernate(
                api,
                args.company_id,
                args.handoff_dir,
                args.broker_journal,
                args.deadline_seconds,
                args.poll_seconds,
                tuple(args.repo),
            )
        else:
            result = resume(
                api,
                args.company_id,
                args.founder_command_id,
                args.handoff_dir,
                args.broker_journal,
                tuple(args.repo),
            )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["complete"] else 2
    except HandoffError as exc:
        print(json.dumps({"complete": False, "held": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
