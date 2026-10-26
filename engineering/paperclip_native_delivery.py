"""One-shot native Paperclip stage and reviewed publication actions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

from engineering import paperclip_canonical_publish as publisher

MANIFEST_SCHEMA = "brain-native-delivery-manifest-v1"
STAGE_SCHEMA = "brain-native-delivery-stage-v1"
PUBLISH_SCHEMA = "brain-native-delivery-publication-v1"
QA_SCHEMA = "brain-native-qa-result-v1"
PUBLISH_REF = "refs/heads/brain/development"
GIT = "/usr/bin/git"
GH_HELPER = "!/opt/homebrew/bin/gh auth git-credential"
GH_CONFIG_DIR = "/Users/root1/.config/gh"
GH_OWNER_HOME = "/Users/root1"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class DeliveryError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise DeliveryError(reason)


def safe_dir(raw: object, name: str, private: bool = False) -> Path:
    require(type(raw) is str and Path(raw).is_absolute(), f"{name}_path_invalid")
    path = Path(raw)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        require(not stat.S_ISLNK(current.lstat().st_mode), f"{name}_symlink")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode), f"{name}_not_directory")
    require(
        not private or (info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077),
        f"{name}_not_private",
    )
    return path


def target(root: Path, relative: str, create: bool = False) -> Path:
    path = PurePosixPath(relative)
    require(
        not path.is_absolute() and not any(part in {"", ".", ".."} for part in path.parts),
        "relative_target_invalid",
    )
    current = root
    for part in path.parts[:-1]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not create:
                raise DeliveryError("target_parent_missing") from None
            current.mkdir(mode=0o700)
            info = current.lstat()
        require(
            not stat.S_ISLNK(info.st_mode) and stat.S_ISDIR(info.st_mode), "target_parent_invalid"
        )
    return current / path.parts[-1]


def write_once(path: Path, data: bytes, mode: int) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        require(not stat.S_ISLNK(info.st_mode) and stat.S_ISREG(info.st_mode), "target_not_regular")
        require(path.read_bytes() == data, "target_collision")
        return
    fd, temp_name = tempfile.mkstemp(prefix=".native-handoff-", dir=path.parent)
    tmp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, mode)
        try:
            os.link(tmp, path, follow_symlinks=False)
        except FileExistsError:
            require(
                not path.is_symlink() and path.is_file() and path.read_bytes() == data,
                "target_collision",
            )
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        tmp.unlink(missing_ok=True)


def load_manifest(filename: str) -> tuple[dict[str, Any], str]:
    path = Path(filename)
    info = path.lstat()
    parent = path.parent.lstat()
    require(
        path.is_absolute()
        and not stat.S_ISLNK(info.st_mode)
        and stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and not stat.S_IMODE(info.st_mode) & 0o077
        and not stat.S_ISLNK(parent.st_mode)
        and stat.S_ISDIR(parent.st_mode)
        and not stat.S_IMODE(parent.st_mode) & 0o077,
        "manifest_not_owner_private",
    )
    raw = path.read_bytes()
    try:
        m = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryError("manifest_invalid_json") from exc
    require(type(m) is dict and m.get("schema") == MANIFEST_SCHEMA, "manifest_schema_invalid")
    base_fields = {
        "schema",
        "api_origin",
        "company_id",
        "developer",
        "qa",
        "stage",
        "publish",
        "source",
        "canonical",
        "qa_workspace",
        "custody_dir",
    }
    require(
        frozenset(m)
        in {
            frozenset(base_fields),
            frozenset(base_fields | {"manager_task_id"}),
            frozenset(base_fields | {"predecessor"}),
            frozenset(base_fields | {"manager_task_id", "predecessor"}),
        },
        "manifest_fields_invalid",
    )
    if "manager_task_id" in m:
        require(UUID.fullmatch(str(m["manager_task_id"])), "manifest_manager_task_invalid")
    require(
        m.get("api_origin") == "http://127.0.0.1:3310"
        and UUID.fullmatch(str(m.get("company_id", ""))),
        "manifest_api_or_company_invalid",
    )
    for role in ("developer", "qa", "stage", "publish"):
        row = m.get(role)
        require(
            type(row) is dict
            and set(row) == {"task_id", "agent_id"}
            and all(UUID.fullmatch(str(row[k])) for k in row),
            f"manifest_{role}_invalid",
        )
    require(m["developer"]["agent_id"] != m["qa"]["agent_id"], "developer_and_qa_must_be_distinct")
    src = m.get("source")
    require(
        type(src) is dict and set(src) == {"seed_repo", "base_sha", "paths"},
        "manifest_source_invalid",
    )
    paths = src["paths"]
    require(
        type(paths) is list
        and len(paths) == 2
        and all(type(p) is str for p in paths)
        and len(set(paths)) == 2,
        "manifest_source_paths_invalid",
    )
    require(
        sum(type(p) is str and p.startswith("src/") for p in paths) == 1
        and sum(type(p) is str and p.startswith("tests/") for p in paths) == 1,
        "manifest_source_paths_invalid",
    )
    for item in paths:
        require(
            type(item) is str
            and "\\" not in item
            and str(PurePosixPath(item)) == item
            and not any(x in {"", ".", ".."} for x in item.split("/"))
            and bool(re.fullmatch(r"[A-Za-z0-9._/-]+", item)),
            "manifest_source_path_unsafe",
        )
    require(bool(OID.fullmatch(str(src["base_sha"]))), "manifest_source_base_invalid")
    canonical = m.get("canonical")
    require(
        type(canonical) is dict
        and set(canonical)
        == {"repo", "ref", "expected_tip", "remote_name", "remote_url", "candidate_ref"},
        "manifest_canonical_invalid",
    )
    require(
        canonical["ref"] == PUBLISH_REF
        and type(canonical["candidate_ref"]) is str
        and re.fullmatch(r"refs/heads/brain/[A-Za-z0-9._/-]{1,100}", canonical["candidate_ref"])
        and canonical["candidate_ref"] != canonical["ref"]
        and ".." not in canonical["candidate_ref"]
        and "//" not in canonical["candidate_ref"]
        and OID.fullmatch(str(canonical["expected_tip"])),
        "manifest_canonical_target_invalid",
    )
    require(
        type(canonical["remote_name"]) is str
        and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", canonical["remote_name"]),
        "manifest_remote_name_invalid",
    )
    url = canonical["remote_url"]
    require(
        type(url) is str
        and url.startswith("https://github.com/")
        and "\n" not in url
        and "\r" not in url,
        "manifest_remote_url_invalid",
    )
    safe_dir(src["seed_repo"], "seed")
    safe_dir(canonical["repo"], "canonical")
    require(
        publisher._git(Path(canonical["repo"]), "check-ref-format", canonical["candidate_ref"])
        == b"",
        "manifest_candidate_ref_invalid",
    )
    safe_dir(m["qa_workspace"], "qa_workspace")
    safe_dir(m["custody_dir"], "custody", private=True)
    if "predecessor" in m:
        predecessor = m["predecessor"]
        require(
            type(predecessor) is dict
            and set(predecessor)
            == {
                "manifest_path",
                "manifest_sha256",
                "source_task_id",
                "publish_task_id",
                "receipt_path",
            },
            "predecessor_binding_invalid",
        )
        require(
            type(predecessor["manifest_path"]) is str
            and Path(predecessor["manifest_path"]).is_absolute()
            and type(predecessor["receipt_path"]) is str
            and Path(predecessor["receipt_path"]).is_absolute()
            and re.fullmatch(r"[0-9a-f]{64}", str(predecessor["manifest_sha256"]))
            and UUID.fullmatch(str(predecessor["source_task_id"]))
            and UUID.fullmatch(str(predecessor["publish_task_id"])),
            "predecessor_binding_invalid",
        )
    return m, digest(raw)


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    return publisher._git(repo, *args, data=data)


def normalize_patch(patch: bytes, seed: Path, base: str, paths: list[str]) -> dict[str, bytes]:
    if git(seed, "rev-parse", f"{base}^{{commit}}").decode().strip() != base:
        raise DeliveryError("seed_base_mismatch")
    if git(seed, "ls-tree", "-rz", "--full-tree", base, "--", *paths):
        raise DeliveryError("candidate_paths_not_new_in_seed")
    with tempfile.TemporaryDirectory(prefix="native-handoff-normalize-") as temp:
        repo = Path(temp)
        init = subprocess.run(
            [GIT, "init", "--quiet", str(repo)],
            capture_output=True,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/var/empty",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
            },
            check=False,
        )
        if init.returncode:
            raise DeliveryError("normalizer_init_failed")
        git(
            repo,
            "-c",
            "user.name=normalizer",
            "-c",
            "user.email=normalizer@localhost",
            "commit",
            "--quiet",
            "--allow-empty",
            "--no-gpg-sign",
            "-m",
            "seed",
        )
        git(repo, "apply", "--cached", "--", data=patch)
        rows = git(repo, "ls-files", "--stage", "-z").rstrip(b"\0").split(b"\0")
        found: dict[str, str] = {}
        for row in rows:
            meta, raw_path = row.split(b"\t", 1)
            mode, oid, stage = meta.decode("ascii").split()
            name = raw_path.decode("utf-8")
            if mode != "100644" or stage != "0" or name in found:
                raise DeliveryError("candidate_file_mode_invalid")
            found[name] = oid
        if set(found) != set(paths):
            raise DeliveryError("candidate_path_set_invalid")
        files = {name: git(repo, "cat-file", "blob", oid) for name, oid in found.items()}
        if any(not data or not _utf8(data) for data in files.values()):
            raise DeliveryError("candidate_file_invalid")
        return files


def _utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def task_binding(manifest: dict[str, Any], action: str, agent_id: str) -> dict[str, str]:
    task = manifest[action]
    if task.get("agent_id") != agent_id:
        raise DeliveryError("current_actor_mismatch")
    return task


class Paperclip:
    def __init__(self, manifest: dict[str, Any]):
        env = os.environ
        self.m = manifest
        self.origin = manifest["api_origin"]
        self.base = self.origin + "/api"
        self.token = env.get("PAPERCLIP_API_KEY", "")
        self.run_id = env.get("PAPERCLIP_RUN_ID", "")
        self.agent_id = env.get("PAPERCLIP_AGENT_ID", "")
        if (
            not self.token
            or not UUID.fullmatch(self.run_id)
            or env.get("PAPERCLIP_COMPANY_ID") != manifest["company_id"]
            or env.get("PAPERCLIP_API_URL") != self.origin
        ):
            raise DeliveryError("process_adapter_identity_missing")

    def request(
        self, method: str, route: str, body: object | None = None, raw: bool = False
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "X-Paperclip-Run-Id": self.run_id,
        }
        data = None if body is None else json_bytes(body)
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + route, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                content = response.read(16 * 1024 * 1024 + 1)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise DeliveryError("paperclip_request_failed") from exc
        if len(content) > 16 * 1024 * 1024:
            raise DeliveryError("paperclip_response_too_large")
        if raw:
            return content
        return json.loads(content) if content else None

    def issue(self, task_id: str) -> dict[str, Any]:
        value = self.request("GET", f"/issues/{task_id}")
        return value.get("issue", value) if type(value) is dict else {}

    def current(self, action: str) -> str:
        task = task_binding(self.m, action, self.agent_id)
        me = self.request("GET", "/agents/me")
        run = self.request("GET", f"/heartbeat-runs/{self.run_id}")
        issue = self.issue(task["task_id"])
        if (
            me.get("id") != self.agent_id
            or self.agent_id != task["agent_id"]
            or me.get("companyId") != self.m["company_id"]
        ):
            raise DeliveryError("current_actor_mismatch")
        if (
            run.get("id") != self.run_id
            or run.get("agentId") != self.agent_id
            or run.get("companyId") != self.m["company_id"]
            or run.get("runtimeMode") != "legacy"
            or run.get("status") != "running"
        ):
            raise DeliveryError("current_run_invalid")
        context = run.get("contextSnapshot")
        if (
            type(context) is not dict
            or context.get("issueId") != task["task_id"]
            or issue.get("id") != task["task_id"]
            or issue.get("companyId") != self.m["company_id"]
            or issue.get("assigneeAgentId") != self.agent_id
            or issue.get("status") not in {"todo", "blocked", "in_progress"}
        ):
            raise DeliveryError("current_task_mismatch")
        self.no_pending(task["task_id"])
        if issue.get("checkoutRunId") != self.run_id or issue.get("executionRunId") != self.run_id:
            issue = self.request(
                "POST",
                f"/issues/{task['task_id']}/checkout",
                {"agentId": self.agent_id, "expectedStatuses": ["todo", "blocked", "in_progress"]},
            )
        if (
            issue.get("id") != task["task_id"]
            or issue.get("companyId") != self.m["company_id"]
            or issue.get("assigneeAgentId") != self.agent_id
            or issue.get("status") != "in_progress"
            or issue.get("checkoutRunId") != self.run_id
            or issue.get("executionRunId") != self.run_id
        ):
            raise DeliveryError("current_task_checkout_invalid")
        return task["task_id"]

    def no_pending(self, task_id: str) -> None:
        rows = self.request("GET", f"/issues/{task_id}/interactions")
        rows = rows.get("interactions") if type(rows) is dict else rows
        if type(rows) is not list or any(type(row) is not dict for row in rows):
            raise DeliveryError("interactions_response_invalid")
        if any(row.get("status") == "pending" for row in rows):
            raise DeliveryError("pending_interaction")

    def complete(self, task_id: str) -> None:
        if self.issue(task_id).get("status") == "done":
            return
        self.request("PATCH", f"/issues/{task_id}", {"status": "done"})
        if self.issue(task_id).get("status") != "done":
            raise DeliveryError("task_completion_readback_failed")

    def latest_run(self, task: dict[str, str]) -> dict[str, Any]:
        rows = self.request("GET", f"/issues/{task['task_id']}/runs")
        if type(rows) is not list or not rows:
            raise DeliveryError("task_runs_missing")
        latest = max(rows, key=lambda r: str(r.get("finishedAt") or r.get("startedAt") or ""))
        if latest.get("status") != "succeeded" or latest.get("agentId") != task["agent_id"]:
            raise DeliveryError("latest_task_run_not_successful")
        run_id = latest.get("runId") or latest.get("id")
        if not UUID.fullmatch(str(run_id or "")):
            raise DeliveryError("task_run_id_invalid")
        run = self.request("GET", f"/heartbeat-runs/{run_id}")
        native = run.get("runnerProfileJson", {}).get("nativeExecutionInput", {})
        provider = native.get("provider", {})
        usage = run.get("usageJson", {})
        if (
            run.get("status") != "succeeded"
            or run.get("runtimeMode") != "native"
            or run.get("agentId") != task["agent_id"]
            or run.get("companyId") != self.m["company_id"]
            or run.get("issueId") != task["task_id"]
            or run.get("nativeIssueId") not in {None, task["task_id"]}
            or provider.get("kind") != "codex"
            or provider.get("model") != "gpt-6-luna"
            or provider.get("reasoningEffort") != "high"
            or usage.get("billingType", usage.get("billing_type"))
            not in {"subscription", "subscription_included"}
        ):
            raise DeliveryError("latest_run_identity_or_subscription_invalid")
        return {**latest, "runId": run_id}

    def attachments(self, task_id: str) -> list[dict[str, Any]]:
        rows = self.request("GET", f"/issues/{task_id}/attachments")
        if type(rows) is not list or any(type(row) is not dict for row in rows):
            raise DeliveryError("attachments_response_invalid")
        return rows

    def artifact(
        self, task: dict[str, str], run: dict[str, Any], name: str, limit: int
    ) -> tuple[dict[str, Any], bytes]:
        rows = [
            r
            for r in self.attachments(task["task_id"])
            if r.get("originalFilename") == name and r.get("originatingRunId") == run["runId"]
        ]
        if not rows:
            raise DeliveryError(f"{name}_missing")
        row = max(rows, key=lambda r: str(r.get("createdAt") or ""))
        if sum(r.get("createdAt") == row.get("createdAt") for r in rows) != 1:
            raise DeliveryError(f"{name}_ambiguous")
        if (
            not UUID.fullmatch(str(row.get("id", "")))
            or row.get("createdByAgentId") != task["agent_id"]
            or type(row.get("byteSize")) is not int
            or not 0 < row["byteSize"] <= limit
        ):
            raise DeliveryError(f"{name}_metadata_invalid")
        content = self.request("GET", f"/attachments/{row['id']}/content", raw=True)
        if len(content) != row["byteSize"] or digest(content) != str(row.get("sha256", "")).lower():
            raise DeliveryError(f"{name}_hash_mismatch")
        return row, content


def latest_successful(api: Paperclip, task: dict[str, str]) -> dict[str, Any]:
    issue = api.issue(task["task_id"])
    if issue.get("status") != "done" or issue.get("assigneeAgentId") != task["agent_id"]:
        raise DeliveryError("source_or_qa_task_not_done")
    api.no_pending(task["task_id"])
    return api.latest_run(task)


def stage_path(custody: Path, developer_id: str, run_id: str) -> Path:
    return custody / f"stage-{developer_id}-{run_id}.json"


def stage_history(
    custody: Path, developer_id: str, manifest_hash: str
) -> list[tuple[Path, dict[str, Any]]]:
    rows = []
    for path in custody.glob(f"stage-{developer_id}-*.json"):
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode), "stage_history_not_regular")
        value = json.loads(path.read_bytes())
        require(
            value.get("schema") == STAGE_SCHEMA
            and value.get("manifest_sha256") == manifest_hash
            and value.get("developer_task_id") == developer_id
            and value.get("developer_run_id") == path.stem.removeprefix(f"stage-{developer_id}-")
            and value.get("revision") in {1, 2},
            "stage_history_identity_invalid",
        )
        rows.append((path, value))
    rows.sort(key=lambda item: int(item[1]["revision"]))
    require(
        len(rows) <= 2 and len({row[1]["revision"] for row in rows}) == len(rows),
        "correction_limit_reached",
    )
    require(not rows or rows[0][1]["revision"] == 1, "stage_history_chain_invalid")
    if len(rows) == 2:
        require(
            rows[1][1].get("previous_stage_sha256") == digest(rows[0][0].read_bytes()),
            "stage_history_chain_invalid",
        )
    return rows


def next_stage_revision(prior: dict[str, Any] | None) -> int:
    if prior is None:
        return 1
    require(prior.get("revision") == 1, "correction_limit_reached")
    return 2


def stage_replay_state(
    history: list[tuple[Path, dict[str, Any]]], run_id: str
) -> tuple[tuple[Path, dict[str, Any]] | None, tuple[Path, dict[str, Any]] | None]:
    current = next((row for row in history if row[1]["developer_run_id"] == run_id), None)
    if current is None:
        return None, history[-1] if history else None
    position = history.index(current)
    return current, history[position - 1] if position > 0 else None


def replace_known(path: Path, data: bytes, old_sha256: str | None) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        require(old_sha256 is None, "correction_target_missing")
        write_once(path, data, 0o444)
        return
    require(
        not stat.S_ISLNK(info.st_mode) and stat.S_ISREG(info.st_mode),
        "correction_target_not_regular",
    )
    current = path.read_bytes()
    if digest(current) == digest(data):
        return
    require(old_sha256 is not None and digest(current) == old_sha256, "correction_target_changed")
    fd, name = tempfile.mkstemp(prefix=".native-correction-", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o444)
        require(
            digest(path.read_bytes()) == old_sha256 and not path.is_symlink(),
            "correction_target_changed",
        )
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def save_record(path: Path, value: dict[str, Any]) -> None:
    data = json_bytes(value)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or path.read_bytes() != data
        ):
            raise DeliveryError("immutable_receipt_conflict")
        return
    write_once(path, data, 0o400)


def notify_manager(
    api: Paperclip,
    manifest: dict[str, Any],
    action: str,
    task_id: str,
    marker: str,
    body: str,
    manifest_hash: str,
) -> None:
    manager_id = manifest.get("manager_task_id")
    require(UUID.fullmatch(str(manager_id or "")), "manager_task_not_pinned")
    custody = safe_dir(manifest["custody_dir"], "custody", private=True)
    intent = {
        "schema": "brain-native-manager-notification-v1",
        "manifest_sha256": manifest_hash,
        "manager_task_id": manager_id,
        "created_by_agent_id": api.agent_id,
        "body": body,
        "marker": marker,
    }
    stem = f"notification-{action}-{task_id}-{digest(marker.encode())[:16]}"
    intent_path = custody / f"{stem}.json"
    attempt_path = custody / f"{stem}.post-attempt.json"
    ack_path = custody / f"{stem}.ack.json"
    save_record(intent_path, intent)

    def readback() -> dict[str, Any] | None:
        rows = api.request("GET", f"/issues/{manager_id}/comments?order=asc&limit=200")
        rows = rows.get("comments") if type(rows) is dict else rows
        require(
            type(rows) is list and all(type(row) is dict for row in rows),
            "manager_comments_invalid",
        )
        matches = [
            row
            for row in rows
            if row.get("authorAgentId") == api.agent_id and row.get("body") == body
        ]
        require(len(matches) <= 1, "manager_notification_ambiguous")
        return matches[0] if matches else None

    found = readback()
    if found is not None:
        ack = {
            "schema": intent["schema"],
            "intent_sha256": digest(json_bytes(intent)),
            "comment_id": found.get("id"),
        }
        save_record(ack_path, ack)
        return
    require(not ack_path.exists(), "manager_notification_readback_lost")
    require(not attempt_path.exists(), "manager_notification_outcome_unknown")
    save_record(
        attempt_path,
        {"schema": intent["schema"], "intent_sha256": digest(json_bytes(intent))},
    )
    try:
        api.request("POST", f"/issues/{manager_id}/comments", {"body": body, "resume": True})
    except DeliveryError:
        found = readback()
        if found is None:
            raise DeliveryError("manager_notification_outcome_unknown") from None
    else:
        found = readback()
        require(found is not None, "manager_notification_readback_missing")
    ack = {
        "schema": intent["schema"],
        "intent_sha256": digest(json_bytes(intent)),
        "comment_id": found.get("id"),
    }
    save_record(ack_path, ack)


def stage(m: dict[str, Any], manifest_hash: str) -> dict[str, Any]:
    api = Paperclip(m)
    own_task = api.current("stage")
    require(own_task == m["stage"]["task_id"], "task_not_pinned")
    slot = m
    developer, qa = slot["developer"], slot["qa"]
    source_run = latest_successful(api, developer)
    patch_meta, patch = api.artifact(developer, source_run, "candidate.patch", 4 * 1024 * 1024)
    demo_meta, demo = api.artifact(developer, source_run, "demonstration.json", 1024 * 1024)
    try:
        example = json.loads(demo)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryError("demonstration_invalid_json") from exc
    if type(example) is not dict or not {"module", "input", "output"} <= set(example):
        raise DeliveryError("demonstration_contract_invalid")
    source = slot["source"]
    paths = source["paths"]
    files = normalize_patch(patch, Path(source["seed_repo"]), source["base_sha"], paths)
    root = safe_dir(slot["qa_workspace"], "qa_workspace")
    custody = safe_dir(m["custody_dir"], "custody", private=True)
    handoff = root / ".native-delivery"
    try:
        handoff.mkdir(mode=0o700)
    except FileExistsError:
        if stat.S_ISLNK(handoff.lstat().st_mode) or not handoff.is_dir():
            raise DeliveryError("qa_handoff_directory_invalid") from None
    native = custody / f"native-handoff-{developer['task_id']}-{source_run['runId']}"
    try:
        native.mkdir(mode=0o700)
    except FileExistsError:
        if stat.S_ISLNK(native.lstat().st_mode) or not native.is_dir():
            raise DeliveryError("custody_directory_invalid") from None
    history = stage_history(custody, developer["task_id"], manifest_hash)
    current_stage, previous_stage = stage_replay_state(history, source_run["runId"])
    current_record = current_stage[1] if current_stage else None
    prior_path, prior = previous_stage if previous_stage else (None, None)
    record_path = stage_path(custody, developer["task_id"], source_run["runId"])
    if current_record is not None:
        require(
            current_record.get("patch", {}).get("sha256") == digest(patch),
            "stage_run_candidate_changed",
        )
        record = current_record
        revision = int(current_record.get("revision", 1))
    else:
        revision = next_stage_revision(prior)
        if revision == 2:
            require(m.get("manager_task_id"), "manager_task_not_pinned")
        if prior is not None:
            prior_qa_run = latest_successful(api, qa)
            prior_meta, prior_result = api.artifact(qa, prior_qa_run, "QA-RESULT.json", 256 * 1024)
            verdict = validate_qa(
                prior_result,
                developer["task_id"],
                prior["patch"]["sha256"],
                prior["files"],
                prior["paths"],
                allow_reject=True,
            )
            require(
                verdict == "reject" and prior_qa_run["runId"] != source_run["runId"],
                "correction_requires_latest_qa_reject",
            )
            require(digest(patch) != prior["patch"]["sha256"], "correction_candidate_unchanged")
            prior_result_value = json.loads(prior_result)
            require(prior_result_value["findings"], "correction_rejection_findings_missing")
            require(
                prior_meta.get("originatingRunId") == prior_qa_run["runId"],
                "correction_qa_binding_invalid",
            )
            for path in paths:
                target_path = target(root, path)
                require(
                    target_path.is_file() and not target_path.is_symlink(),
                    "correction_target_missing",
                )
                require(
                    digest(target_path.read_bytes()) == prior["files"][path]["sha256"],
                    "correction_target_changed",
                )
            prior_handoff = root / ".native-delivery"
            for name, key in (
                ("candidate.patch", "patch"),
                ("demonstration.json", "demonstration"),
            ):
                old_path = prior_handoff / name
                require(
                    old_path.is_file()
                    and not old_path.is_symlink()
                    and digest(old_path.read_bytes()) == prior[key]["sha256"],
                    "correction_artifact_changed",
                )
        record = {
            "schema": STAGE_SCHEMA,
            "manifest_sha256": manifest_hash,
            "developer_task_id": developer["task_id"],
            "developer_agent_id": developer["agent_id"],
            "developer_run_id": source_run["runId"],
            "source_base_sha": source["base_sha"],
            "revision": revision,
            "previous_stage_sha256": None
            if prior_path is None
            else digest(prior_path.read_bytes()),
            "paths": paths,
            "patch": {
                "attachment_id": patch_meta["id"],
                "sha256": digest(patch),
                "size": len(patch),
            },
            "demonstration": {
                "attachment_id": demo_meta["id"],
                "sha256": digest(demo),
                "size": len(demo),
            },
            "files": {p: {"sha256": digest(files[p]), "size": len(files[p])} for p in paths},
        }
    prior_files = {} if prior is None else prior["files"]
    save_record(record_path, record)
    write_once(native / "candidate.patch", patch, 0o400)
    write_once(native / "demonstration.json", demo, 0o400)
    for name, data, key in (
        ("candidate.patch", patch, "patch"),
        ("demonstration.json", demo, "demonstration"),
    ):
        old_hash = None if prior is None else prior[key]["sha256"]
        replace_known(handoff / name, data, old_hash)
    files_root = native / "files"
    try:
        files_root.mkdir(mode=0o700)
    except FileExistsError:
        if stat.S_ISLNK(files_root.lstat().st_mode) or not files_root.is_dir():
            raise DeliveryError("custody_files_directory_invalid") from None
    for p in paths:
        old_hash = None if prior is None else prior_files[p]["sha256"]
        replace_known(target(root, p), files[p], old_hash)
        write_once(target(files_root, p, create=True), files[p], 0o400)
    old_record_hash = None if prior is None else digest(json_bytes(prior))
    replace_known(handoff / "native-delivery.json", json_bytes(record), old_record_hash)
    if revision == 2:
        marker = (
            f"correction:{developer['task_id']}:{source_run['runId']}:{record['patch']['sha256']}"
        )
        notify_manager(
            api,
            m,
            "correction",
            developer["task_id"],
            marker,
            (
                f"Corrected candidate {developer['task_id']} is durably staged; "
                f"reopen QA after stage completion. Patch {record['patch']['sha256']}."
            ),
            manifest_hash,
        )
    api.complete(own_task)
    return {
        "status": "staged",
        "source_task_id": developer["task_id"],
        "qa_task_id": qa["task_id"],
    }


def validate_qa(
    data: bytes,
    source_id: str,
    patch_hash: str,
    files: dict[str, Any],
    paths: list[str],
    *,
    allow_reject: bool = False,
) -> str:
    if len(data) > 256 * 1024:
        raise DeliveryError("qa_result_too_large")
    try:
        result = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryError("qa_result_invalid_json") from exc
    expected = {p: files[p]["sha256"] for p in paths}
    verdict = result.get("verdict") if type(result) is dict else None
    keys = {"schema", "verdict", "source_task_id", "source_patch_sha256", "files", "checks"}
    if verdict == "reject" and allow_reject:
        keys.add("findings")
    if (
        type(result) is not dict
        or set(result) != keys
        or result.get("schema") != QA_SCHEMA
        or verdict not in ({"accept", "reject"} if allow_reject else {"accept"})
        or result.get("source_task_id") != source_id
        or result.get("source_patch_sha256") != patch_hash
        or result.get("files") != expected
    ):
        raise DeliveryError("qa_result_candidate_binding_invalid")
    checks = result["checks"]
    if (
        type(checks) is not list
        or not checks
        or any(
            type(x) is not dict
            or set(x) != {"name", "result"}
            or not str(x["name"]).strip()
            or x["result"] not in ({"passed", "failed"} if verdict == "reject" else {"passed"})
            for x in checks
        )
    ):
        raise DeliveryError("qa_result_checks_invalid")
    if verdict == "reject":
        findings = result["findings"]
        if (
            type(findings) is not list
            or not findings
            or any(
                type(item) is not dict
                or set(item) != {"check", "evidence"}
                or not str(item["check"]).strip()
                or not str(item["evidence"]).strip()
                or item["check"] not in {check["name"] for check in checks}
                for item in findings
            )
        ):
            raise DeliveryError("qa_result_findings_invalid")
    return verdict


def remote_git(repo: Path, *args: str, github_auth: bool = False) -> bytes:
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": "/var/empty",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    if github_auth:
        config_dir = safe_dir(GH_CONFIG_DIR, "gh_config")
        require(config_dir.lstat().st_uid == os.getuid(), "gh_config_not_owner_owned")
        env["GH_CONFIG_DIR"] = GH_CONFIG_DIR
        env["HOME"] = GH_OWNER_HOME
    if os.environ.get("SSH_AUTH_SOCK"):
        env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    command = [GIT, "-c", "core.hooksPath=/dev/null", "-c", "credential.helper="]
    if github_auth:
        command.extend(["-c", f"credential.helper={GH_HELPER}"])
    result = subprocess.run(
        [*command, "-C", str(repo), *args],
        capture_output=True,
        env=env,
        check=False,
        timeout=45,
    )
    if result.returncode:
        raise DeliveryError("pinned_remote_git_failed")
    return result.stdout


def remote_refs(m: dict[str, Any]) -> dict[str, str]:
    c = m["canonical"]
    repo = Path(c["repo"])
    if git(repo, "remote", "get-url", c["remote_name"]).decode().strip() != c["remote_url"]:
        raise DeliveryError("remote_url_changed")
    if not c["remote_url"].startswith("https://github.com/"):
        raise DeliveryError("remote_transport_not_approved")
    raw = remote_git(
        repo, "ls-remote", c["remote_name"], c["ref"], c["candidate_ref"], github_auth=True
    )
    refs = {}
    for line in raw.decode("ascii").splitlines():
        oid, ref = line.split("\t")
        if not OID.fullmatch(oid) or ref in refs:
            raise DeliveryError("remote_refs_malformed")
        refs[ref] = oid
    return refs


def check_publication_head(
    repo: Path, ref: str, expected: str, receipt: dict[str, Any], durable_receipt: bool
) -> None:
    head = git(repo, "rev-parse", ref).decode().strip()
    require(
        head == expected or (durable_receipt and head == receipt.get("candidate")),
        "canonical_expected_tip_changed",
    )


def push_ref(m: dict[str, Any], source: str, target_ref: str) -> None:
    c = m["canonical"]
    repo = Path(c["repo"])
    if git(repo, "remote", "get-url", c["remote_name"]).decode().strip() != c[
        "remote_url"
    ] or not c["remote_url"].startswith("https://github.com/"):
        raise DeliveryError("remote_url_changed")
    remote_git(repo, "push", c["remote_name"], f"{source}:{target_ref}", github_auth=True)


def predecessor_expected_tip(
    m: dict[str, Any], manifest_hash: str, api: Paperclip
) -> str | None:
    """Resolve a second slot only from its explicitly pinned first-slot receipt."""
    binding = m.get("predecessor")
    if binding is None:
        return None
    parent, parent_hash = load_manifest(binding["manifest_path"])
    require(parent_hash == binding["manifest_sha256"], "predecessor_manifest_hash_mismatch")
    require("predecessor" not in parent, "predecessor_chain_depth_invalid")
    require(
        parent["company_id"] == m["company_id"]
        and parent["source"]["base_sha"] == m["source"]["base_sha"]
        and parent["canonical"]["repo"] == m["canonical"]["repo"]
        and parent["canonical"]["ref"] == m["canonical"]["ref"]
        and parent["canonical"]["remote_name"] == m["canonical"]["remote_name"]
        and parent["canonical"]["remote_url"] == m["canonical"]["remote_url"]
        and parent["canonical"]["expected_tip"] == m["canonical"]["expected_tip"],
        "predecessor_target_mismatch",
    )
    require(
        set(parent["source"]["paths"]).isdisjoint(m["source"]["paths"]),
        "predecessor_source_paths_overlap",
    )
    require(
        parent["developer"]["task_id"] == binding["source_task_id"]
        and parent["publish"]["task_id"] == binding["publish_task_id"],
        "predecessor_task_binding_mismatch",
    )
    issue = api.issue(binding["publish_task_id"])
    require(
        issue.get("id") == binding["publish_task_id"]
        and issue.get("companyId") == m["company_id"]
        and issue.get("assigneeAgentId") == parent["publish"]["agent_id"]
        and issue.get("status") == "done",
        "predecessor_publish_task_not_done",
    )
    api.no_pending(binding["publish_task_id"])
    parent_runs = api.request("GET", f"/issues/{binding['publish_task_id']}/runs")
    require(
        type(parent_runs) is list
        and bool(parent_runs)
        and not any(
            type(row) is not dict or row.get("status") in {"queued", "running", "pending"}
            for row in parent_runs
        ),
        "predecessor_publish_run_pending",
    )
    latest = max(
        parent_runs,
        key=lambda row: str(row.get("finishedAt") or row.get("startedAt") or "")
        if type(row) is dict
        else "",
    )
    run_id = latest.get("runId") or latest.get("id") if type(latest) is dict else None
    require(
        type(latest) is dict
        and latest.get("status") == "succeeded"
        and latest.get("agentId") == parent["publish"]["agent_id"]
        and UUID.fullmatch(str(run_id or "")),
        "predecessor_publish_run_not_succeeded",
    )
    native_run = api.request("GET", f"/heartbeat-runs/{run_id}")
    require(
        type(native_run) is dict
        and native_run.get("id") == run_id
        and native_run.get("status") == "succeeded"
        and native_run.get("runtimeMode") == "legacy"
        and native_run.get("agentId") == parent["publish"]["agent_id"]
        and native_run.get("companyId") == m["company_id"]
        and native_run.get("issueId") == binding["publish_task_id"],
        "predecessor_publish_run_not_succeeded",
    )

    path = Path(binding["receipt_path"])
    try:
        info = path.lstat()
        parent_info = path.parent.lstat()
    except OSError as exc:
        raise DeliveryError("predecessor_publication_receipt_missing") from exc
    require(
        path.is_absolute()
        and stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and not stat.S_IMODE(info.st_mode) & 0o077
        and not stat.S_IMODE(info.st_mode) & 0o222
        and stat.S_ISDIR(parent_info.st_mode)
        and parent_info.st_uid == os.getuid()
        and not stat.S_IMODE(parent_info.st_mode) & 0o077,
        "predecessor_publication_receipt_not_private",
    )
    try:
        saved = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryError("predecessor_publication_receipt_invalid") from exc
    require(type(saved) is dict, "predecessor_publication_receipt_invalid")
    unsigned = {k: v for k, v in saved.items() if k != "publication_digest"}
    receipt = saved.get("canonical_receipt")
    require(
        type(saved) is dict
        and saved.get("schema") == PUBLISH_SCHEMA
        and saved.get("manifest_sha256") == parent_hash
        and saved.get("source_task_id") == binding["source_task_id"]
        and saved.get("expected_tip") == parent["canonical"]["expected_tip"]
        and saved.get("canonical_ref") == parent["canonical"]["ref"]
        and saved.get("candidate_ref") == parent["canonical"]["candidate_ref"]
        and type(receipt) is dict
        and OID.fullmatch(str(receipt.get("candidate", "")))
        and saved.get("publication_digest") == digest(json_bytes(unsigned)),
        "predecessor_publication_receipt_invalid",
    )
    paths = list(parent["source"]["paths"])
    require(
        type(saved.get("files")) is dict and set(saved["files"]) == set(paths),
        "predecessor_publication_receipt_invalid",
    )
    repo = Path(m["canonical"]["repo"])
    publisher.inspect(repo, parent["canonical"]["ref"], receipt, allowed_paths=paths)
    candidate = receipt["candidate"]
    own_receipt_path = Path(m["custody_dir"]) / f"publication-{m['developer']['task_id']}.json"
    own_candidate = None
    if own_receipt_path.exists() or own_receipt_path.is_symlink():
        own_info = own_receipt_path.lstat()
        require(
            stat.S_ISREG(own_info.st_mode)
            and own_info.st_uid == os.getuid()
            and not stat.S_IMODE(own_info.st_mode) & 0o077
            and not stat.S_IMODE(own_info.st_mode) & 0o222,
            "publication_receipt_not_private",
        )
        try:
            own_saved = json.loads(own_receipt_path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeliveryError("publication_receipt_invalid") from exc
        require(type(own_saved) is dict, "publication_receipt_invalid")
        own_unsigned = {k: v for k, v in own_saved.items() if k != "publication_digest"}
        own_receipt = own_saved.get("canonical_receipt")
        require(
            own_saved.get("schema") == PUBLISH_SCHEMA
            and own_saved.get("source_task_id") == m["developer"]["task_id"]
            and own_saved.get("manifest_sha256") == manifest_hash
            and own_saved.get("expected_tip") == candidate
            and own_saved.get("canonical_ref") == m["canonical"]["ref"]
            and own_saved.get("candidate_ref") == m["canonical"]["candidate_ref"]
            and type(own_receipt) is dict
            and OID.fullmatch(str(own_receipt.get("candidate", "")))
            and own_saved.get("publication_digest") == digest(json_bytes(own_unsigned)),
            "publication_receipt_conflict",
        )
        publisher.inspect(
            repo, m["canonical"]["ref"], own_receipt, allowed_paths=list(m["source"]["paths"])
        )
        own_candidate = own_receipt["candidate"]
    local_head = git(repo, parent["canonical"]["ref"]).decode().strip()
    require(
        local_head == candidate or (own_candidate is not None and local_head == own_candidate),
        "predecessor_canonical_head_unexpected",
    )
    c = parent["canonical"]
    raw = remote_git(
        repo,
        "ls-remote",
        c["remote_name"],
        c["ref"],
        c["candidate_ref"],
        m["canonical"]["candidate_ref"],
        github_auth=True,
    )
    refs: dict[str, str] = {}
    try:
        for line in raw.decode("ascii").splitlines():
            oid, ref = line.split("\t")
            require(bool(OID.fullmatch(oid)) and ref not in refs, "remote_refs_malformed")
            refs[ref] = oid
    except (UnicodeDecodeError, ValueError) as exc:
        raise DeliveryError("remote_refs_malformed") from exc
    require(
        refs.get(parent["canonical"]["ref"])
        in ({candidate} | ({own_candidate} if own_candidate is not None else set()))
        and refs.get(parent["canonical"]["candidate_ref"]) == candidate
        and (
            own_candidate is None
            or refs.get(m["canonical"]["candidate_ref"]) in {None, own_candidate}
        ),
        "predecessor_remote_head_unexpected",
    )
    return candidate


def publish(m: dict[str, Any], manifest_hash: str) -> dict[str, Any]:
    api = Paperclip(m)
    own_task = api.current("publish")
    require(own_task == m["publish"]["task_id"], "task_not_pinned")
    require(m.get("manager_task_id"), "manager_task_not_pinned")
    slot = m
    dev, qa = slot["developer"], slot["qa"]
    source_run = latest_successful(api, dev)
    qa_run = latest_successful(api, qa)
    if source_run["agentId"] == qa_run["agentId"]:
        raise DeliveryError("qa_not_independent")
    record_path = stage_path(Path(m["custody_dir"]), dev["task_id"], source_run["runId"])
    record = json.loads(record_path.read_bytes())
    if (
        record.get("schema") != STAGE_SCHEMA
        or record.get("manifest_sha256") != manifest_hash
        or record.get("developer_run_id") != source_run["runId"]
    ):
        raise DeliveryError("stage_receipt_missing_or_stale")
    qa_meta, qa_data = api.artifact(qa, qa_run, "QA-RESULT.json", 256 * 1024)
    validate_qa(
        qa_data, dev["task_id"], record["patch"]["sha256"], record["files"], record["paths"]
    )
    native = Path(m["custody_dir"]) / f"native-handoff-{dev['task_id']}-{source_run['runId']}"
    files = {p: target(native / "files", p).read_bytes() for p in record["paths"]}
    if any(digest(files[p]) != record["files"][p]["sha256"] for p in record["paths"]):
        raise DeliveryError("custodied_file_hash_mismatch")
    c = slot["canonical"]
    repo = Path(c["repo"])
    expected = predecessor_expected_tip(m, manifest_hash, api) or c["expected_tip"]
    old = {p: None for p in record["paths"]}
    new = {
        p: publisher._git(repo, "hash-object", "--stdin", data=files[p]).decode().strip()
        for p in record["paths"]
    }
    op = {
        "schema": PUBLISH_SCHEMA,
        "manifest_sha256": manifest_hash,
        "source_task_id": dev["task_id"],
        "source_run_id": source_run["runId"],
        "qa_task_id": qa["task_id"],
        "qa_run_id": qa_run["runId"],
        "qa_attachment_id": qa_meta["id"],
        "qa_sha256": digest(qa_data),
        "source_patch_sha256": record["patch"]["sha256"],
        "files": {p: record["files"][p]["sha256"] for p in record["paths"]},
        "expected_tip": expected,
        "canonical_ref": c["ref"],
        "candidate_ref": c["candidate_ref"],
    }
    receipt_file = Path(m["custody_dir"]) / f"publication-{dev['task_id']}.json"
    if receipt_file.exists():
        saved = json.loads(receipt_file.read_bytes())
        receipt = saved.get("canonical_receipt")
        base = {
            k: v for k, v in saved.items() if k not in {"canonical_receipt", "publication_digest"}
        }
        unsigned_saved = {k: v for k, v in saved.items() if k != "publication_digest"}
        if base != op or saved.get("publication_digest") != digest(json_bytes(unsigned_saved)):
            raise DeliveryError("publication_receipt_conflict")
    else:
        receipt = publisher.prepare(
            repo, c["ref"], expected, old, new, files, allowed_paths=record["paths"]
        )
        saved = {**op, "canonical_receipt": receipt}
        saved["publication_digest"] = digest(json_bytes(saved))
        save_record(receipt_file, saved)
    require(type(receipt) is dict, "publication_receipt_invalid")
    check_publication_head(repo, c["ref"], expected, receipt, receipt_file.exists())
    refs = remote_refs(m)
    candidate = receipt["candidate"]
    if refs.get(c["ref"]) not in {expected, candidate} or refs.get(c["candidate_ref"]) not in {
        None,
        candidate,
    }:
        raise DeliveryError("remote_refs_unexpected")
    publisher.inspect(repo, c["ref"], receipt, allowed_paths=record["paths"])
    done = publisher.reconcile(
        repo, c["ref"], receipt, candidate, receipt["receipt_digest"], allowed_paths=record["paths"]
    )
    if done.get("status") != "reconciled" or done.get("candidate") != candidate:
        raise DeliveryError("canonical_readback_failed")
    if refs.get(c["candidate_ref"]) is None:
        push_ref(m, candidate, c["candidate_ref"])
    refs = remote_refs(m)
    if refs.get(c["candidate_ref"]) != candidate or refs.get(c["ref"]) not in {expected, candidate}:
        raise DeliveryError("candidate_remote_readback_failed")
    if refs.get(c["ref"]) == expected:
        push_ref(m, candidate, c["ref"])
    refs = remote_refs(m)
    if refs.get(c["candidate_ref"]) != candidate or refs.get(c["ref"]) != candidate:
        raise DeliveryError("publication_remote_readback_failed")
    marker = f"published:{dev['task_id']}:{candidate}:{receipt['receipt_digest']}"
    notify_manager(
        api,
        m,
        "publish",
        dev["task_id"],
        marker,
        f"Native publication passed remote readback for {dev['task_id']}: candidate {candidate}.",
        manifest_hash,
    )
    api.complete(own_task)
    return {"status": "published", "candidate_sha": candidate, "receipt": str(receipt_file)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "publish"))
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    try:
        m, sha = load_manifest(args.manifest)
        result = stage(m, sha) if args.action == "stage" else publish(m, sha)
    except (DeliveryError, OSError, ValueError) as exc:
        print(
            json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True), file=sys.stderr
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
