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
CANDIDATE_REF = "refs/heads/brain/native-report-20261026"
GIT = "/usr/bin/git"
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
    require(
        set(m)
        == {
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
        },
        "manifest_fields_invalid",
    )
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
        and canonical["candidate_ref"] == CANDIDATE_REF
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
    safe_dir(m["qa_workspace"], "qa_workspace")
    safe_dir(m["custody_dir"], "custody", private=True)
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
        task = self.m[action]
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


def stage(m: dict[str, Any], manifest_hash: str) -> dict[str, Any]:
    api = Paperclip(m)
    own_task = api.current("stage")
    developer = m["developer"]
    source_run = latest_successful(api, developer)
    patch_meta, patch = api.artifact(developer, source_run, "candidate.patch", 4 * 1024 * 1024)
    demo_meta, demo = api.artifact(developer, source_run, "demonstration.json", 1024 * 1024)
    try:
        example = json.loads(demo)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryError("demonstration_invalid_json") from exc
    if type(example) is not dict or not {"module", "input", "output"} <= set(example):
        raise DeliveryError("demonstration_contract_invalid")
    paths = m["source"]["paths"]
    files = normalize_patch(patch, Path(m["source"]["seed_repo"]), m["source"]["base_sha"], paths)
    root = safe_dir(m["qa_workspace"], "qa_workspace")
    custody = safe_dir(m["custody_dir"], "custody", private=True)
    handoff = root / ".native-delivery"
    try:
        handoff.mkdir(mode=0o700)
    except FileExistsError:
        if stat.S_ISLNK(handoff.lstat().st_mode) or not handoff.is_dir():
            raise DeliveryError("qa_handoff_directory_invalid") from None
    native = custody / f"native-handoff-{developer['task_id']}"
    try:
        native.mkdir(mode=0o700)
    except FileExistsError:
        if stat.S_ISLNK(native.lstat().st_mode) or not native.is_dir():
            raise DeliveryError("custody_directory_invalid") from None
    record = {
        "schema": STAGE_SCHEMA,
        "manifest_sha256": manifest_hash,
        "developer_task_id": developer["task_id"],
        "developer_agent_id": developer["agent_id"],
        "developer_run_id": source_run["runId"],
        "source_base_sha": m["source"]["base_sha"],
        "paths": paths,
        "patch": {"attachment_id": patch_meta["id"], "sha256": digest(patch), "size": len(patch)},
        "demonstration": {
            "attachment_id": demo_meta["id"],
            "sha256": digest(demo),
            "size": len(demo),
        },
        "files": {p: {"sha256": digest(files[p]), "size": len(files[p])} for p in paths},
    }
    record_path = custody / f"stage-{developer['task_id']}.json"
    if not record_path.exists():
        for p in paths:
            if target(root, p).exists():
                raise DeliveryError("qa_source_target_collision")
        for name in ("candidate.patch", "demonstration.json", "native-delivery.json"):
            if (handoff / name).exists() or (handoff / name).is_symlink():
                raise DeliveryError("qa_artifact_target_collision")
    save_record(record_path, record)
    write_once(native / "candidate.patch", patch, 0o400)
    write_once(native / "demonstration.json", demo, 0o400)
    write_once(handoff / "candidate.patch", patch, 0o444)
    write_once(handoff / "demonstration.json", demo, 0o444)
    files_root = native / "files"
    try:
        files_root.mkdir(mode=0o700)
    except FileExistsError:
        if stat.S_ISLNK(files_root.lstat().st_mode) or not files_root.is_dir():
            raise DeliveryError("custody_files_directory_invalid") from None
    for p in paths:
        write_once(target(root, p), files[p], 0o444)
        write_once(target(files_root, p, create=True), files[p], 0o400)
    write_once(handoff / "native-delivery.json", json_bytes(record), 0o444)
    api.complete(own_task)
    return {
        "status": "staged",
        "source_task_id": developer["task_id"],
        "qa_task_id": m["qa"]["task_id"],
    }


def validate_qa(
    data: bytes, source_id: str, patch_hash: str, files: dict[str, Any], paths: list[str]
) -> None:
    if len(data) > 256 * 1024:
        raise DeliveryError("qa_result_too_large")
    try:
        result = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryError("qa_result_invalid_json") from exc
    expected = {p: files[p]["sha256"] for p in paths}
    if (
        type(result) is not dict
        or set(result)
        != {"schema", "verdict", "source_task_id", "source_patch_sha256", "files", "checks"}
        or result.get("schema") != QA_SCHEMA
        or result.get("verdict") != "accept"
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
            or x["result"] != "passed"
            for x in checks
        )
    ):
        raise DeliveryError("qa_result_checks_invalid")


def remote_git(repo: Path, *args: str, osxkeychain: bool = False) -> bytes:
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": "/var/empty",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    if os.environ.get("SSH_AUTH_SOCK"):
        env["SSH_AUTH_SOCK"] = os.environ["SSH_AUTH_SOCK"]
    command = [GIT, "-c", "core.hooksPath=/dev/null", "-c", "credential.helper="]
    if osxkeychain:
        command.extend(["-c", "credential.helper=osxkeychain"])
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
        repo, "ls-remote", c["remote_name"], c["ref"], c["candidate_ref"], osxkeychain=True
    )
    refs = {}
    for line in raw.decode("ascii").splitlines():
        oid, ref = line.split("\t")
        if not OID.fullmatch(oid) or ref in refs:
            raise DeliveryError("remote_refs_malformed")
        refs[ref] = oid
    return refs


def push_ref(m: dict[str, Any], source: str, target_ref: str) -> None:
    c = m["canonical"]
    repo = Path(c["repo"])
    if git(repo, "remote", "get-url", c["remote_name"]).decode().strip() != c[
        "remote_url"
    ] or not c["remote_url"].startswith("https://github.com/"):
        raise DeliveryError("remote_url_changed")
    remote_git(repo, "push", c["remote_name"], f"{source}:{target_ref}", osxkeychain=True)


def publish(m: dict[str, Any], manifest_hash: str) -> dict[str, Any]:
    api = Paperclip(m)
    own_task = api.current("publish")
    dev, qa = m["developer"], m["qa"]
    source_run = latest_successful(api, dev)
    qa_run = latest_successful(api, qa)
    if source_run["agentId"] == qa_run["agentId"]:
        raise DeliveryError("qa_not_independent")
    record_path = Path(m["custody_dir"]) / f"stage-{dev['task_id']}.json"
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
    native = Path(m["custody_dir"]) / f"native-handoff-{dev['task_id']}"
    files = {p: target(native / "files", p).read_bytes() for p in record["paths"]}
    if any(digest(files[p]) != record["files"][p]["sha256"] for p in record["paths"]):
        raise DeliveryError("custodied_file_hash_mismatch")
    c = m["canonical"]
    repo = Path(c["repo"])
    expected = c["expected_tip"]
    if git(repo, "rev-parse", c["ref"]).decode().strip() != expected:
        raise DeliveryError("canonical_expected_tip_changed")
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
        if base != op or saved.get("publication_digest") != digest(
            json_bytes({**base, "canonical_receipt": receipt})
        ):
            raise DeliveryError("publication_receipt_conflict")
    else:
        receipt = publisher.prepare(
            repo, c["ref"], expected, old, new, files, allowed_paths=record["paths"]
        )
        saved = {**op, "canonical_receipt": receipt}
        saved["publication_digest"] = digest(json_bytes(saved))
        save_record(receipt_file, saved)
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
