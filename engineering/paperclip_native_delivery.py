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
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from engineering import paperclip_campaign_resource_guard as resource_guard
from engineering import paperclip_canonical_publish as publisher

MANIFEST_SCHEMA = "brain-native-delivery-manifest-v1"
STAGE_SCHEMA = "brain-native-delivery-stage-v1"
PUBLISH_SCHEMA = "brain-native-delivery-publication-v1"
QA_SCHEMA = "brain-native-qa-result-v1"
PREPARATION_SCHEMA = "brain-native-delivery-preparation-v1"
DELIVERY_PLAN_SCHEMA = "brain-native-delivery-plan-v1"
PREPARATION_RECEIPT_SCHEMA = "brain-native-delivery-preparation-receipt-v1"
PUBLISH_REF = "refs/heads/brain/development"
PROCESS_WORKSPACE_IDS = {
    "stage": "bb6956ed-1210-48a0-8e6a-b28f584855fc",
    "publish": "3a62ff9a-ff85-499f-b5e9-de265565d669",
}
GIT = "/usr/bin/git"
ORG_ROOT = Path("/Users/root1/Developer/brain-org-build")
GH_HELPER = "!/opt/homebrew/bin/gh auth git-credential"
GH_CONFIG_DIR = "/Users/root1/.config/gh"
GH_OWNER_HOME = "/Users/root1"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

PREPARATION_MANAGER_GUIDANCE = """Finite delivery preparation contract:
1. Use the owner-frozen envelope and only its one or two slot path/acceptance bindings.
2. Register one clean QA project workspace per slot at the exact envelope-derived path. On an
   uncertain POST, GET the project workspace list and accept one exact name/cwd/repoRef/defaultRef
   match; stop on zero or multiple matches. Never replay an uncertain POST.
3. Create each source, stage, QA, and publisher task with the envelope actors and exact dependency
   graph. Pass workspaceSelection in the initial issue create body: source uses the frozen source
   workspace with managed_isolated mode; QA uses its slot workspace with shared mode; stage and
   publisher use existing workspace IDs bb6956ed-1210-48a0-8e6a-b28f584855fc and
   3a62ff9a-ff85-499f-b5e9-de265565d669 respectively. Source settings inherit the owner-frozen
   git-worktree project policy and base SHA.
4. Keep source and publisher in backlog and stage/QA blocked. Pin stage/publisher process argv to
   the deterministic manifest paths. Attach one DELIVERY-PLAN.json to the completed planning task;
   include the company/planning/execution IDs and each slot's title, module, benefit, acceptance,
   nonduplication evidence, paths, four task IDs, and QA workspace ID.
5. Do not start source tasks. The prepare action validates the complete graph, creates safe QA
   clones/manifests/receipt, and completes only its own process task. Manager execution admits
   work after that receipt is read back.

This is one finite owner-frozen programme input, not a general strategy-to-source admission grant.
Each programme needs a fresh envelope binding its accepted paths, contract hashes, source base,
canonical tip, actors/models, project workspaces and deterministic output root.
An optional max_corrections value on that owner-frozen envelope permits one to three corrections;
omitting it preserves the legacy one-correction limit.
An optional predecessor_candidates list may bind at most four immutable preparation receipts by
receipt path, source envelope hash and slot. Preparation selects exactly one candidate whose
native publication, receipt, and local/remote canonical refs verify, then freezes that exact
manifest/task/publication receipt binding and the resulting expected tip.

DELIVERY-PLAN.json shape: {"schema":"brain-native-delivery-plan-v1","company_id":"UUID",
"planning_task_id":"UUID","manager_task_id":"UUID","slots":[{"slot":1,
"title":"...","module":"...","benefit":"...","acceptance":"...",
"nonduplication_evidence":"...","paths":["src/...","tests/..."],
"source_task_id":"UUID","stage_task_id":"UUID","qa_task_id":"UUID",
"publish_task_id":"UUID","qa_workspace_id":"UUID"}]}."""


class DeliveryError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise DeliveryError(reason)


def validated_role_models(manifest: dict[str, Any]) -> dict[str, str]:
    if "role_models" not in manifest:
        return {"developer": "gpt-6-luna", "qa": "gpt-6-luna"}
    role_models = manifest.get("role_models")
    require(
        type(role_models) is dict and set(role_models) == {"developer", "qa"},
        "manifest_role_models_invalid",
    )
    require(
        type(role_models["developer"]) is str
        and role_models["developer"] == "gpt-6-luna"
        and type(role_models["qa"]) is str
        and role_models["qa"] in {"gpt-6-luna", "gpt-6.1-sol"},
        "manifest_role_models_invalid",
    )
    return role_models


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
    manifest_fields = set(m)
    optional_manifest_fields = {
        "manager_task_id",
        "predecessor",
        "role_models",
        "lineage_depth",
        "predecessor_base_tip",
        "resource_policy",
        "max_corrections",
    }
    require(
        base_fields <= manifest_fields
        and manifest_fields - base_fields <= optional_manifest_fields
        and ("predecessor_base_tip" not in manifest_fields or "predecessor" in manifest_fields)
        and ("predecessor_base_tip" not in manifest_fields or "lineage_depth" in manifest_fields),
        "manifest_fields_invalid",
    )
    if "resource_policy" in m:
        try:
            resource_guard.load_policy_reference(m["resource_policy"], m["company_id"])
        except resource_guard.ResourceGuardError as exc:
            raise DeliveryError(str(exc)) from exc
    if "max_corrections" in m:
        require(
            type(m["max_corrections"]) is int and 1 <= m["max_corrections"] <= 3,
            "manifest_max_corrections_invalid",
        )
    if "role_models" in m:
        validated_role_models(m)
    if "manager_task_id" in m:
        require(UUID.fullmatch(str(m["manager_task_id"])), "manifest_manager_task_invalid")
    if "lineage_depth" in m:
        require(
            type(m["lineage_depth"]) is int
            and 0 <= m["lineage_depth"] <= 8
            and ((m["lineage_depth"] == 0) == ("predecessor" not in m)),
            "manifest_lineage_depth_invalid",
        )
    if "predecessor_base_tip" in m:
        require(
            OID.fullmatch(str(m["predecessor_base_tip"])) is not None,
            "manifest_predecessor_base_tip_invalid",
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

    def current(self, action: str, *, checkout_if_missing: bool = True) -> str:
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
        if checkout_if_missing:
            if (
                issue.get("checkoutRunId") != self.run_id
                or issue.get("executionRunId") != self.run_id
            ):
                issue = self.request(
                    "POST",
                    f"/issues/{task['task_id']}/checkout",
                    {
                        "agentId": self.agent_id,
                        "expectedStatuses": ["todo", "blocked", "in_progress"],
                    },
                )
            require(
                issue.get("id") == task["task_id"]
                and issue.get("companyId") == self.m["company_id"]
                and issue.get("assigneeAgentId") == self.agent_id
                and issue.get("status") == "in_progress"
                and issue.get("checkoutRunId") == self.run_id
                and issue.get("executionRunId") == self.run_id,
                "current_task_checkout_invalid",
            )
        else:
            require(
                issue.get("status") in {"todo", "blocked", "in_progress"}
                and issue.get("executionRunId") == self.run_id
                and issue.get("checkoutRunId") in {None, self.run_id},
                "current_task_execution_invalid",
            )
        return task["task_id"]

    def no_pending(self, task_id: str) -> None:
        rows = self.request("GET", f"/issues/{task_id}/interactions")
        rows = rows.get("interactions") if type(rows) is dict else rows
        if type(rows) is not list or any(type(row) is not dict for row in rows):
            raise DeliveryError("interactions_response_invalid")
        if any(row.get("status") == "pending" for row in rows):
            raise DeliveryError("pending_interaction")

    def no_pending_terminal(self, task_id: str) -> None:
        rows = self.request("GET", f"/issues/{task_id}/interactions")
        rows = rows.get("interactions") if type(rows) is dict else rows
        if type(rows) is not list or any(type(row) is not dict for row in rows):
            raise DeliveryError("interactions_response_invalid")
        terminal = {"accepted", "rejected", "answered", "cancelled", "expired", "failed"}
        if any(row.get("status") not in terminal for row in rows):
            raise DeliveryError("predecessor_interaction_not_terminal")

    def complete(self, task_id: str) -> None:
        if self.issue(task_id).get("status") == "done":
            return
        self.request("PATCH", f"/issues/{task_id}", {"status": "done"})
        if self.issue(task_id).get("status") != "done":
            raise DeliveryError("task_completion_readback_failed")

    def latest_run(self, task: dict[str, str]) -> dict[str, Any]:
        role = next(
            (name for name in ("developer", "qa", "stage", "publish") if self.m.get(name) == task),
            None,
        )
        if role is None:
            raise DeliveryError("task_role_binding_invalid")
        model = validated_role_models(self.m).get(role, "gpt-6-luna")
        return self.latest_run_for_task(task, model)

    def latest_run_for_task(self, task: dict[str, str], model: str) -> dict[str, Any]:
        rows = self.request("GET", f"/issues/{task['task_id']}/runs")
        if type(rows) is not list or not rows:
            raise DeliveryError("task_runs_missing")
        pinned_runs = [
            row
            for row in rows
            if type(row) is dict
            and row.get("agentId") == task["agent_id"]
            and row.get("contextIssueId") == task["task_id"]
        ]
        if not pinned_runs:
            raise DeliveryError("latest_task_run_not_successful")
        latest = max(
            pinned_runs,
            key=lambda r: str(r.get("finishedAt") or r.get("startedAt") or ""),
        )
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
            or provider.get("model") != model
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

    def qa_artifact(
        self, task: dict[str, str], run: dict[str, Any], name: str, limit: int
    ) -> tuple[dict[str, Any], bytes]:
        """Read a QA report from its exact run-owned attachment or document revision."""
        try:
            meta, content = self.artifact(task, run, name, limit)
        except DeliveryError as exc:
            if str(exc) != f"{name}_missing":
                raise
        else:
            return {**meta, "artifactType": "attachment"}, content

        rows = self.request(
            "GET", f"/issues/{task['task_id']}/documents?includeSystem=true"
        )
        if type(rows) is not list or any(type(row) is not dict for row in rows):
            raise DeliveryError("qa_documents_response_invalid")
        key_names = {name.lower(), Path(name).stem.lower()}
        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for document in rows:
            key = document.get("key")
            title = document.get("title")
            normalized_key = key.lower().replace("_", "-") if type(key) is str else ""
            named_result = normalized_key in key_names or title == name
            document_id = document.get("id")
            if (
                not UUID.fullmatch(str(document_id or ""))
                or type(key) is not str
                or document.get("companyId") != self.m["company_id"]
                or document.get("issueId") != task["task_id"]
            ):
                raise DeliveryError("qa_document_metadata_invalid")
            revisions = self.request(
                "GET",
                f"/issues/{task['task_id']}/documents/{quote(str(key), safe='')}/revisions",
            )
            if type(revisions) is not list or any(type(row) is not dict for row in revisions):
                raise DeliveryError("qa_document_revisions_invalid")
            matching = [
                revision
                for revision in revisions
                if revision.get("documentId") == document_id
                and revision.get("companyId") == self.m["company_id"]
                and revision.get("issueId") == task["task_id"]
                and revision.get("createdByAgentId") == task["agent_id"]
                and revision.get("createdByRunId") == run["runId"]
            ]
            if matching:
                if any(
                    type(revision.get("revisionNumber")) is not int
                    or revision["revisionNumber"] < 1
                    or not UUID.fullmatch(str(revision.get("id") or ""))
                    for revision in matching
                ):
                    raise DeliveryError("qa_document_revision_metadata_invalid")
                latest_revision_number = max(
                    revision.get("revisionNumber", -1) for revision in matching
                )
                latest = [
                    revision
                    for revision in matching
                    if revision.get("revisionNumber") == latest_revision_number
                ]
                if len(latest) != 1:
                    raise DeliveryError("qa_document_revision_ambiguous")
                body = latest[0].get("body")
                if type(body) is not str:
                    if named_result:
                        raise DeliveryError("qa_document_revision_metadata_invalid")
                    continue
                try:
                    content = body.encode("utf-8")
                except UnicodeEncodeError as exc:
                    if named_result:
                        raise DeliveryError("qa_document_body_invalid_utf8") from exc
                    continue
                try:
                    report = json.loads(content)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    report = None
                if named_result or (type(report) is dict and report.get("schema") == QA_SCHEMA):
                    candidates.append((document, latest[0]))

        if len(candidates) != 1:
            raise DeliveryError(f"{name}_missing" if not candidates else f"{name}_ambiguous")
        document, revision = candidates[0]
        body = revision.get("body")
        revision_id = revision.get("id")
        if (
            type(body) is not str
            or not UUID.fullmatch(str(revision_id or ""))
            or type(revision.get("revisionNumber")) is not int
            or revision.get("revisionNumber") < 1
        ):
            raise DeliveryError("qa_document_revision_metadata_invalid")
        try:
            content = body.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise DeliveryError("qa_document_body_invalid_utf8") from exc
        if not 0 < len(content) <= limit:
            raise DeliveryError(f"{name}_metadata_invalid")
        return {
            "id": document["id"],
            "revisionId": revision_id,
            "revisionNumber": revision["revisionNumber"],
            "createdByAgentId": task["agent_id"],
            "originatingRunId": run["runId"],
            "byteSize": len(content),
            "sha256": digest(content),
            "artifactType": "document_revision",
        }, content


def private_json_file(filename: str, name: str) -> tuple[dict[str, Any], str]:
    path = Path(filename)
    require(path.is_absolute(), f"{name}_path_invalid")
    safe_dir(str(path.parent), f"{name}_parent", private=True)
    info = path.lstat()
    require(
        stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and info.st_uid == os.getuid()
        and not stat.S_IMODE(info.st_mode) & 0o077,
        f"{name}_not_owner_private",
    )
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryError(f"{name}_invalid_json") from exc
    require(type(value) is dict, f"{name}_object_required")
    return value, digest(raw)


def _resource_preflight(
    api: Paperclip,
    reference: object,
    phase: str,
    receipt_directory: Path | None = None,
    reserve_model_runs: int = 0,
) -> Path | None:
    if reference is None:
        return None
    try:
        policy, _policy_hash = resource_guard.load_policy_reference(reference, api.m["company_id"])
        runs = resource_guard.campaign_model_run_ids(api, api.m["company_id"], policy)
        if receipt_directory is None:
            receipt_directory = Path(reference["path"]).parent
        safe_dir(str(receipt_directory), "resource_receipt_directory", private=True)
        receipt_path = receipt_directory / f"resource-{phase}-{api.run_id}-{time.time_ns()}.json"
        resource_guard.preflight(
            policy,
            api.m["company_id"],
            phase,
            runs,
            receipt_path,
            reserve_model_runs=reserve_model_runs,
        )
        return receipt_path
    except resource_guard.ResourceGuardError as exc:
        raise DeliveryError(str(exc)) from exc


def _uuid(value: object) -> bool:
    return type(value) is str and UUID.fullmatch(value) is not None


def _strict_paths(value: object, expected_count: int = 2) -> list[str]:
    require(
        type(value) is list
        and len(value) == expected_count
        and all(type(path) is str for path in value)
        and len(set(value)) == len(value),
        "preparation_paths_invalid",
    )
    paths = list(value)
    require(
        sum(path.startswith("src/") for path in paths) == 1
        and sum(path.startswith("tests/") for path in paths) == 1,
        "preparation_paths_invalid",
    )
    for path in paths:
        require(
            "\\" not in path
            and str(PurePosixPath(path)) == path
            and not any(part in {"", ".", ".."} for part in path.split("/"))
            and bool(re.fullmatch(r"[A-Za-z0-9._/-]+", path)),
            "preparation_path_unsafe",
        )
    return paths


def validate_preparation_envelope(value: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "schema",
        "api_origin",
        "company_id",
        "planning",
        "prepare",
        "manager",
        "roles",
        "role_models",
        "source",
        "canonical",
        "output_root",
        "slots",
    }
    optional_fields = {"predecessor_candidates", "resource_policy", "max_corrections"}
    present_optional = optional_fields & set(value)
    require(
        expected <= set(value)
        and set(value) <= expected | optional_fields
        and value.get("schema") == PREPARATION_SCHEMA,
        "preparation_envelope_fields_invalid",
    )
    require(
        value["api_origin"] == "http://127.0.0.1:3310" and _uuid(value["company_id"]),
        "preparation_envelope_api_invalid",
    )
    if "resource_policy" in value:
        try:
            resource_guard.load_policy_reference(value["resource_policy"], value["company_id"])
        except resource_guard.ResourceGuardError as exc:
            raise DeliveryError(str(exc)) from exc
    for name in ("planning", "prepare", "manager"):
        row = value[name]
        require(
            type(row) is dict
            and set(row) == {"task_id", "agent_id"}
            and _uuid(row["task_id"])
            and _uuid(row["agent_id"]),
            f"preparation_{name}_binding_invalid",
        )
    roles = value["roles"]
    require(
        type(roles) is dict
        and set(roles) == {"developer", "stage", "qa", "publish"}
        and all(_uuid(agent) for agent in roles.values())
        and len(set(roles.values())) == len(roles),
        "preparation_roles_invalid",
    )
    models = value["role_models"]
    require(
        type(models) is dict
        and set(models) == {"planning", "manager", "developer", "qa"}
        and all(type(model) is str for model in models.values())
        and models["developer"] == "gpt-6-luna"
        and models["qa"] in {"gpt-6-luna", "gpt-6.1-sol"}
        and models["planning"] in {"gpt-6-luna", "gpt-6.1-sol"}
        and models["manager"] in {"gpt-6-luna", "gpt-6.1-sol"},
        "preparation_role_models_invalid",
    )
    source = value["source"]
    require(
        type(source) is dict
        and set(source)
        == {"project_id", "project_workspace_id", "workspace_path", "seed_repo", "base_sha"}
        and _uuid(source["project_id"])
        and _uuid(source["project_workspace_id"])
        and type(source["workspace_path"]) is str
        and Path(source["workspace_path"]).is_absolute()
        and source["workspace_path"] == source["seed_repo"]
        and bool(OID.fullmatch(str(source["base_sha"]))),
        "preparation_source_binding_invalid",
    )
    safe_dir(source["seed_repo"], "preparation_seed")
    canonical = value["canonical"]
    require(
        type(canonical) is dict
        and set(canonical) == {"repo", "ref", "expected_tip", "remote_name", "remote_url"}
        and type(canonical["repo"]) is str
        and Path(canonical["repo"]).is_absolute()
        and canonical["ref"] == PUBLISH_REF
        and bool(OID.fullmatch(str(canonical["expected_tip"])))
        and type(canonical["remote_name"]) is str
        and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", canonical["remote_name"])
        and type(canonical["remote_url"]) is str
        and canonical["remote_url"].startswith("https://github.com/")
        and "\n" not in canonical["remote_url"]
        and "\r" not in canonical["remote_url"],
        "preparation_canonical_binding_invalid",
    )
    safe_dir(canonical["repo"], "preparation_canonical")
    if "resource_policy" in present_optional:
        policy_ref = value["resource_policy"]
        require(
            type(policy_ref) is dict
            and set(policy_ref) == {"path", "sha256"}
            and type(policy_ref["path"]) is str
            and Path(policy_ref["path"]).is_absolute()
            and re.fullmatch(r"[0-9a-f]{64}", str(policy_ref["sha256"])),
            "preparation_resource_policy_binding_invalid",
        )
    if "max_corrections" in present_optional:
        require(
            type(value["max_corrections"]) is int and 1 <= value["max_corrections"] <= 3,
            "preparation_max_corrections_invalid",
        )
    if "predecessor_candidates" in present_optional:
        candidates = value["predecessor_candidates"]
        require(
            type(candidates) is list and 1 <= len(candidates) <= 4,
            "preparation_predecessor_candidates_invalid",
        )
        for binding in candidates:
            require(
                type(binding) is dict
                and set(binding)
                == {"preparation_receipt_path", "preparation_envelope_sha256", "slot"}
                and type(binding["preparation_receipt_path"]) is str
                and Path(binding["preparation_receipt_path"]).is_absolute()
                and re.fullmatch(r"[0-9a-f]{64}", str(binding["preparation_envelope_sha256"]))
                and type(binding["slot"]) is int
                and binding["slot"] == 1,
                "preparation_predecessor_binding_invalid",
            )
        require(
            len(
                {
                    (
                        binding["preparation_receipt_path"],
                        binding["preparation_envelope_sha256"],
                        binding["slot"],
                    )
                    for binding in candidates
                }
            )
            == len(candidates),
            "preparation_predecessor_candidates_ambiguous",
        )
    output_root = value["output_root"]
    output_path = Path(output_root) if type(output_root) is str else Path()
    require(
        type(output_root) is str
        and output_path.is_absolute()
        and output_path != Path(output_path.anchor),
        "preparation_output_root_invalid",
    )
    safe_dir(str(output_path.parent), "preparation_output_parent", private=True)
    require(not output_path.exists() and not output_path.is_symlink(), "preparation_output_exists")
    slots = value["slots"]
    require(type(slots) is list and 1 <= len(slots) <= 2, "preparation_slots_invalid")
    all_paths: list[str] = []
    for number, slot in enumerate(slots, 1):
        require(
            type(slot) is dict
            and set(slot)
            == {"slot", "paths", "acceptance_sha256", "qa_project_id", "candidate_ref"}
            and type(slot["slot"]) is int
            and slot["slot"] == number
            and re.fullmatch(r"[0-9a-f]{64}", str(slot["acceptance_sha256"]))
            and _uuid(slot["qa_project_id"])
            and type(slot["candidate_ref"]) is str
            and re.fullmatch(r"refs/heads/brain/[A-Za-z0-9._/-]{1,100}", slot["candidate_ref"])
            and slot["candidate_ref"] != PUBLISH_REF
            and ".." not in slot["candidate_ref"]
            and "//" not in slot["candidate_ref"],
            "preparation_slot_binding_invalid",
        )
        all_paths.extend(_strict_paths(slot["paths"]))
    require(len(set(all_paths)) == len(all_paths), "preparation_slot_paths_overlap")
    require(
        len({slot["candidate_ref"] for slot in slots}) == len(slots)
        and len({slot["qa_project_id"] for slot in slots}) == len(slots),
        "preparation_slot_targets_overlap",
    )
    return value


def _workspace_selection(issue: dict[str, Any]) -> dict[str, Any]:
    value = issue.get("workspaceSelection")
    require(
        type(value) is dict
        and value.get("version") == 1
        and value.get("source") == "explicit"
        and type(value.get("selection")) is dict,
        "preparation_workspace_selection_missing",
    )
    return value["selection"]


def _blocked_by(issue: dict[str, Any]) -> set[str]:
    rows = issue.get("blockedBy")
    require(
        type(rows) is list and all(type(row) is dict and _uuid(row.get("id")) for row in rows),
        "preparation_dependency_shape_invalid",
    )
    return {row["id"] for row in rows}


def _expected_process_override(action: str, manifest_path: Path) -> dict[str, Any]:
    return {
        "adapterConfig": {
            "cwd": str(ORG_ROOT),
            "args": [
                "-m",
                "engineering.paperclip_native_delivery",
                action,
                "--manifest",
                str(manifest_path),
            ],
            "command": str(ORG_ROOT / ".venv/bin/python"),
            "timeoutSec": 300,
        },
        "useProjectWorkspace": False,
    }


def _task_row(
    api: Paperclip,
    task_id: str,
    company_id: str,
    parent_id: str,
    agent_id: str,
    status: str,
    blocker_ids: set[str],
    execution_workspace_id: str | None = None,
) -> dict[str, Any]:
    issue = api.issue(task_id)
    require(
        issue.get("id") == task_id
        and issue.get("companyId") == company_id
        and issue.get("parentId") == parent_id
        and issue.get("assigneeAgentId") == agent_id
        and issue.get("status") == status
        and issue.get("executionWorkspaceId") == execution_workspace_id
        and _blocked_by(issue) == blocker_ids,
        "preparation_graph_issue_mismatch",
    )
    return issue


def _verify_project_workspace(
    api: Paperclip, project_id: str, workspace_id: str, cwd: str, base_sha: str
) -> None:
    rows = api.request("GET", f"/projects/{project_id}/workspaces")
    require(
        type(rows) is list and all(type(row) is dict for row in rows),
        "preparation_project_workspaces_invalid",
    )
    matches = [row for row in rows if row.get("id") == workspace_id]
    require(
        len(matches) == 1
        and matches[0].get("companyId") == api.m["company_id"]
        and matches[0].get("projectId") == project_id
        and matches[0].get("sourceType") == "local_path"
        and matches[0].get("cwd") == cwd
        and matches[0].get("repoRef") == base_sha
        and matches[0].get("defaultRef") == base_sha
        and matches[0].get("repoUrl") in (None, ""),
        "preparation_project_workspace_mismatch",
    )


def _validate_delivery_plan(
    value: dict[str, Any], envelope: dict[str, Any]
) -> list[dict[str, Any]]:
    require(
        set(value) == {"schema", "company_id", "planning_task_id", "manager_task_id", "slots"}
        and value.get("schema") == DELIVERY_PLAN_SCHEMA
        and value.get("company_id") == envelope["company_id"]
        and value.get("planning_task_id") == envelope["planning"]["task_id"]
        and value.get("manager_task_id") == envelope["manager"]["task_id"]
        and type(value.get("slots")) is list
        and len(value["slots"]) == len(envelope["slots"]),
        "preparation_delivery_plan_invalid",
    )
    paths_seen: set[str] = set()
    for index, (task, approved) in enumerate(
        zip(value["slots"], envelope["slots"], strict=True), 1
    ):
        require(
            type(task) is dict
            and set(task)
            == {
                "slot",
                "title",
                "module",
                "benefit",
                "acceptance",
                "nonduplication_evidence",
                "paths",
                "source_task_id",
                "stage_task_id",
                "qa_task_id",
                "publish_task_id",
                "qa_workspace_id",
            }
            and task.get("slot") == index
            and all(
                type(task.get(field)) is str and task[field].strip()
                for field in ("title", "module", "benefit", "acceptance", "nonduplication_evidence")
            )
            and _strict_paths(task.get("paths")) == approved["paths"]
            and digest(task["acceptance"].encode()) == approved["acceptance_sha256"]
            and all(
                _uuid(task.get(field))
                for field in (
                    "source_task_id",
                    "stage_task_id",
                    "qa_task_id",
                    "publish_task_id",
                    "qa_workspace_id",
                )
            ),
            "preparation_delivery_slot_invalid",
        )
        require(not (paths_seen & set(task["paths"])), "preparation_slot_paths_overlap")
        paths_seen.update(task["paths"])
    ids = [
        task[field]
        for task in value["slots"]
        for field in ("source_task_id", "stage_task_id", "qa_task_id", "publish_task_id")
    ]
    require(len(set(ids)) == len(ids), "preparation_task_ids_overlap")
    require(
        len({task["qa_workspace_id"] for task in value["slots"]}) == len(value["slots"]),
        "preparation_qa_workspaces_overlap",
    )
    return value["slots"]


def _preflight_preparation_graph(
    api: Paperclip, envelope: dict[str, Any], plan: dict[str, Any]
) -> list[dict[str, Any]]:
    company_id = envelope["company_id"]
    manager = envelope["manager"]
    manager_issue = api.issue(manager["task_id"])
    require(
        manager_issue.get("id") == manager["task_id"]
        and manager_issue.get("companyId") == company_id
        and manager_issue.get("assigneeAgentId") == manager["agent_id"]
        and manager_issue.get("status") == "blocked"
        and _blocked_by(manager_issue) == {envelope["prepare"]["task_id"]},
        "preparation_manager_issue_mismatch",
    )
    children = api.request(
        "GET",
        f"/companies/{company_id}/issues?parentId={manager['task_id']}&limit=100&includeBlockedBy=true",
    )
    require(
        type(children) is list and all(type(row) is dict for row in children),
        "preparation_manager_children_invalid",
    )
    expected_ids = {
        task[field]
        for task in plan
        for field in ("source_task_id", "stage_task_id", "qa_task_id", "publish_task_id")
    }
    require(
        {row.get("id") for row in children} == expected_ids, "preparation_manager_graph_not_exact"
    )

    source = envelope["source"]
    source_project = api.request("GET", f"/projects/{source['project_id']}")
    policy = (
        source_project.get("executionWorkspacePolicy") if type(source_project) is dict else None
    )
    strategy = policy.get("workspaceStrategy") if type(policy) is dict else None
    require(
        type(source_project) is dict
        and source_project.get("id") == source["project_id"]
        and source_project.get("companyId") == company_id
        and type(policy) is dict
        and policy.get("enabled") is True
        and type(strategy) is dict
        and strategy.get("type") == "git_worktree"
        and strategy.get("baseRef") == source["base_sha"],
        "preparation_source_project_policy_mismatch",
    )
    _verify_project_workspace(
        api,
        source["project_id"],
        source["project_workspace_id"],
        source["workspace_path"],
        source["base_sha"],
    )
    out = Path(envelope["output_root"])
    rows_out: list[dict[str, Any]] = []
    for task, approved in zip(plan, envelope["slots"], strict=True):
        slot = task["slot"]
        manifest_path = out / f"slot{slot}" / "manifest.json"
        qa_path = out / f"slot{slot}" / "qa"
        source_issue = _task_row(
            api,
            task["source_task_id"],
            company_id,
            manager["task_id"],
            envelope["roles"]["developer"],
            "backlog",
            set(),
        )
        require(
            source_issue.get("projectId") == source["project_id"]
            and source_issue.get("projectWorkspaceId") == source["project_workspace_id"]
            and _workspace_selection(source_issue)
            == {
                "kind": "configured_source",
                "projectWorkspaceId": source["project_workspace_id"],
                "mode": "managed_isolated",
            }
            and source_issue.get("executionWorkspaceSettings", {}).get("mode")
            == "isolated_workspace"
            and source_issue.get("title") == task["title"]
            and task["acceptance"] in source_issue.get("description", "")
            and all(path in source_issue.get("description", "") for path in task["paths"])
            and source_issue.get("assigneeAdapterOverrides")
            == {"adapterConfig": {"cwd": source["seed_repo"]}, "useProjectWorkspace": True},
            "preparation_source_binding_mismatch",
        )
        stage_issue = _task_row(
            api,
            task["stage_task_id"],
            company_id,
            manager["task_id"],
            envelope["roles"]["stage"],
            "blocked",
            {task["source_task_id"]},
            PROCESS_WORKSPACE_IDS["stage"],
        )
        require(
            stage_issue.get("title") == f"Stage slot {slot}: {task['title']}"
            and _workspace_selection(stage_issue)
            == {"kind": "existing", "workspaceId": PROCESS_WORKSPACE_IDS["stage"]}
            and stage_issue.get("assigneeAdapterOverrides")
            == _expected_process_override("stage", manifest_path),
            "preparation_stage_binding_mismatch",
        )
        qa_issue = _task_row(
            api,
            task["qa_task_id"],
            company_id,
            manager["task_id"],
            envelope["roles"]["qa"],
            "blocked",
            {task["stage_task_id"]},
        )
        _verify_project_workspace(
            api,
            approved["qa_project_id"],
            task["qa_workspace_id"],
            str(qa_path),
            source["base_sha"],
        )
        require(
            qa_issue.get("title") == f"Independent QA slot {slot}: {task['title']}"
            and qa_issue.get("projectId") == approved["qa_project_id"]
            and qa_issue.get("projectWorkspaceId") == task["qa_workspace_id"]
            and _workspace_selection(qa_issue)
            == {
                "kind": "configured_source",
                "projectWorkspaceId": task["qa_workspace_id"],
                "mode": "shared",
            }
            and qa_issue.get("assigneeAdapterOverrides")
            == {"adapterConfig": {"cwd": str(qa_path)}, "useProjectWorkspace": False}
            and task["acceptance"] in qa_issue.get("description", ""),
            "preparation_qa_binding_mismatch",
        )
        publish_issue = _task_row(
            api,
            task["publish_task_id"],
            company_id,
            manager["task_id"],
            envelope["roles"]["publish"],
            "backlog",
            {task["qa_task_id"]},
            PROCESS_WORKSPACE_IDS["publish"],
        )
        require(
            publish_issue.get("title") == f"Publish slot {slot}: {task['title']}"
            and _workspace_selection(publish_issue)
            == {"kind": "existing", "workspaceId": PROCESS_WORKSPACE_IDS["publish"]}
            and publish_issue.get("assigneeAdapterOverrides")
            == _expected_process_override("publish", manifest_path),
            "preparation_publish_binding_mismatch",
        )
        rows_out.append(
            {
                "plan": task,
                "approved": approved,
                "manifest_path": manifest_path,
                "qa_path": qa_path,
                "custody_path": out / f"slot{slot}" / "custody",
            }
        )
    return rows_out


def _validate_preparation_seed(envelope: dict[str, Any]) -> None:
    seed_repo = Path(envelope["source"]["seed_repo"])
    tracked_changes = git(seed_repo, "status", "--porcelain", "--untracked-files=no")
    unexpected_changes = git(
        seed_repo,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        ".",
        ":(exclude).paperclip/worktrees",
    )
    require(
        git(seed_repo, "rev-parse", "HEAD").decode().strip() == envelope["source"]["base_sha"]
        and not tracked_changes
        and not unexpected_changes
        and not git(seed_repo, "remote", "-v"),
        "preparation_seed_not_clean_frozen_base",
    )


def _validate_preparation_inputs(envelope: dict[str, Any]) -> None:
    source = envelope["source"]
    _validate_preparation_seed(envelope)
    canonical = envelope["canonical"]
    require(
        git(Path(canonical["repo"]), "rev-parse", canonical["ref"]).decode().strip()
        == canonical["expected_tip"],
        "preparation_canonical_tip_changed",
    )
    for slot in envelope["slots"]:
        for path in slot["paths"]:
            require(
                not git(Path(source["seed_repo"]), "ls-tree", "-r", source["base_sha"], "--", path)
                and not git(
                    Path(canonical["repo"]), "ls-tree", "-r", canonical["expected_tip"], "--", path
                ),
                "preparation_path_not_absent_from_base",
            )


def _resolve_preparation_predecessor(
    api: Paperclip, envelope: dict[str, Any], plan: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Select the sole fully verified publication among owner-pinned alternatives."""
    candidates = envelope.get("predecessor_candidates")
    if candidates is None:
        return None
    first = plan[0]
    matches: list[tuple[dict[str, Any], str, int, str]] = []
    for candidate_binding in candidates:
        receipt_path = Path(candidate_binding["preparation_receipt_path"])
        try:
            receipt_parent = safe_dir(
                str(receipt_path.parent), "preparation_predecessor_receipt_parent", private=True
            )
            receipt_parent_info = receipt_parent.lstat()
            receipt_info = receipt_path.lstat()
        except FileNotFoundError:
            # An unselected programme may not have been prepared. Missing options are not
            # publication evidence; selection below still requires one exact verified match.
            continue
        except OSError as exc:
            raise DeliveryError("preparation_predecessor_receipt_missing") from exc
        require(
            receipt_path.is_absolute()
            and stat.S_ISREG(receipt_info.st_mode)
            and receipt_info.st_uid == os.getuid()
            and not stat.S_IMODE(receipt_info.st_mode) & 0o077
            and not stat.S_IMODE(receipt_info.st_mode) & 0o222
            and stat.S_ISDIR(receipt_parent_info.st_mode)
            and receipt_parent_info.st_uid == os.getuid()
            and not stat.S_IMODE(receipt_parent_info.st_mode) & 0o077,
            "preparation_predecessor_receipt_not_private",
        )
        try:
            prior_receipt = json.loads(receipt_path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeliveryError("preparation_predecessor_receipt_invalid") from exc
        require(
            type(prior_receipt) is dict
            and prior_receipt.get("schema") == PREPARATION_RECEIPT_SCHEMA
            and prior_receipt.get("company_id") == envelope["company_id"]
            and prior_receipt.get("canonical_ref") == envelope["canonical"]["ref"]
            and prior_receipt.get("canonical_base_tip", prior_receipt.get("canonical_expected_tip"))
            == envelope["canonical"]["expected_tip"]
            and prior_receipt.get("envelope_sha256")
            == candidate_binding["preparation_envelope_sha256"],
            "preparation_predecessor_receipt_binding_mismatch",
        )
        rows = prior_receipt.get("slots")
        require(
            type(rows) is list and all(type(row) is dict for row in rows),
            "preparation_predecessor_receipt_invalid",
        )
        selected_rows = [row for row in rows if row.get("slot") == candidate_binding["slot"]]
        require(len(selected_rows) == 1, "preparation_predecessor_slot_missing")
        prior_slot = selected_rows[0]
        require(
            type(prior_slot.get("manifest_path")) is str
            and Path(prior_slot["manifest_path"]).is_absolute()
            and re.fullmatch(r"[0-9a-f]{64}", str(prior_slot.get("manifest_sha256", "")))
            and _uuid(prior_slot.get("source_task_id"))
            and _uuid(prior_slot.get("publish_task_id")),
            "preparation_predecessor_manifest_binding_mismatch",
        )
        parent, parent_hash = load_manifest(prior_slot["manifest_path"])
        require(
            parent_hash == prior_slot.get("manifest_sha256")
            and parent["developer"]["task_id"] == prior_slot.get("source_task_id")
            and parent["publish"]["task_id"] == prior_slot.get("publish_task_id"),
            "preparation_predecessor_manifest_binding_mismatch",
        )
        parent_depth = parent.get("lineage_depth", 0)
        require(
            type(parent_depth) is int
            and 0 <= parent_depth < 8
            and ("predecessor" not in parent or "lineage_depth" in parent),
            "preparation_predecessor_lineage_invalid",
        )
        binding = {
            "manifest_path": prior_slot["manifest_path"],
            "manifest_sha256": prior_slot["manifest_sha256"],
            "source_task_id": prior_slot["source_task_id"],
            "publish_task_id": prior_slot["publish_task_id"],
            "receipt_path": str(
                Path(parent["custody_dir"]) / f"publication-{prior_slot['source_task_id']}.json"
            ),
        }
        probe = {
            "company_id": envelope["company_id"],
            "developer": {"task_id": first["source_task_id"]},
            "source": {
                "base_sha": envelope["source"]["base_sha"],
                "paths": first["paths"],
            },
            "canonical": {
                **envelope["canonical"],
                "candidate_ref": envelope["slots"][0]["candidate_ref"],
            },
            "predecessor_base_tip": parent["canonical"]["expected_tip"],
            "lineage_depth": parent_depth + 1,
            "custody_dir": str(Path(envelope["output_root"]) / "slot1" / "custody"),
            "predecessor": binding,
        }
        try:
            candidate = predecessor_expected_tip(probe, "0" * 64, api)
        except DeliveryError as exc:
            # A candidate which is not the current canonical predecessor is not selected.
            # Malformed/native-incomplete candidates cannot match; if none match, preparation
            # fails closed below and writes no output.
            if str(exc) in {
                "predecessor_publish_task_not_done",
                "predecessor_publish_run_not_terminal",
                "predecessor_publish_run_not_succeeded",
                "predecessor_canonical_head_unexpected",
                "predecessor_remote_head_unexpected",
            }:
                continue
            raise
        require(candidate is not None, "preparation_predecessor_unresolved")
        matches.append((binding, candidate, parent_depth + 1, parent["canonical"]["expected_tip"]))
    require(len(matches) == 1, "preparation_predecessor_not_unique")
    binding, candidate, lineage_depth, base_tip = matches[0]
    return {
        "binding": binding,
        "expected_tip": candidate,
        "lineage_depth": lineage_depth,
        "base_tip": base_tip,
    }


def prepare(envelope_path: str) -> dict[str, Any]:
    envelope_value, envelope_hash = private_json_file(envelope_path, "preparation_envelope")
    envelope = validate_preparation_envelope(envelope_value)
    _validate_preparation_seed(envelope)
    api = Paperclip(
        {
            "api_origin": envelope["api_origin"],
            "company_id": envelope["company_id"],
            "prepare": envelope["prepare"],
        }
    )
    own_task = api.current("prepare")
    require(own_task == envelope["prepare"]["task_id"], "preparation_task_not_pinned")
    planning = envelope["planning"]
    planning_issue = api.issue(planning["task_id"])
    require(
        planning_issue.get("id") == planning["task_id"]
        and planning_issue.get("companyId") == envelope["company_id"]
        and planning_issue.get("assigneeAgentId") == planning["agent_id"]
        and planning_issue.get("status") == "done",
        "preparation_planning_task_not_done",
    )
    api.no_pending(planning["task_id"])
    planning_run = api.latest_run_for_task(planning, envelope["role_models"]["planning"])
    plan_meta, plan_bytes = api.artifact(planning, planning_run, "DELIVERY-PLAN.json", 512 * 1024)
    try:
        plan_value = json.loads(plan_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeliveryError("preparation_delivery_plan_invalid_json") from exc
    require(type(plan_value) is dict, "preparation_delivery_plan_object_required")
    plan = _validate_delivery_plan(plan_value, envelope)
    prepared_slots = _preflight_preparation_graph(api, envelope, plan)
    predecessor = _resolve_preparation_predecessor(api, envelope, plan)
    effective_envelope = dict(envelope)
    effective_envelope["canonical"] = dict(envelope["canonical"])
    if predecessor is not None:
        effective_envelope["canonical"]["expected_tip"] = predecessor["expected_tip"]
    first_lineage_depth = predecessor["lineage_depth"] if predecessor is not None else 0
    require(
        first_lineage_depth + len(prepared_slots) - 1 <= 8,
        "preparation_lineage_depth_exceeded",
    )
    _validate_preparation_inputs(effective_envelope)
    resource_receipt = _resource_preflight(
        api, envelope.get("resource_policy"), "prepare", reserve_model_runs=8
    )

    output_root = Path(envelope["output_root"])
    output_root.mkdir(mode=0o700)
    manifests: list[tuple[Path, dict[str, Any], str]] = []
    for row in prepared_slots:
        qa_path = row["qa_path"]
        slot_dir = qa_path.parent
        slot_dir.mkdir(mode=0o700)
        row["custody_path"].mkdir(mode=0o700)
        subprocess.run(
            [
                GIT,
                "clone",
                "--local",
                "--no-hardlinks",
                str(envelope["source"]["seed_repo"]),
                str(qa_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/var/empty",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
            },
        )
        os.chmod(qa_path, 0o700)
        git(qa_path, "remote", "remove", "origin")
        require(
            git(qa_path, "rev-parse", "HEAD").decode().strip() == envelope["source"]["base_sha"]
            and not git(qa_path, "remote", "-v")
            and not git(qa_path, "status", "--porcelain", "--untracked-files=all"),
            "preparation_qa_clone_invalid",
        )
        task = row["plan"]
        manifest: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA,
            "api_origin": envelope["api_origin"],
            "company_id": envelope["company_id"],
            "manager_task_id": envelope["manager"]["task_id"],
            "developer": {
                "task_id": task["source_task_id"],
                "agent_id": envelope["roles"]["developer"],
            },
            "qa": {"task_id": task["qa_task_id"], "agent_id": envelope["roles"]["qa"]},
            "stage": {"task_id": task["stage_task_id"], "agent_id": envelope["roles"]["stage"]},
            "publish": {
                "task_id": task["publish_task_id"],
                "agent_id": envelope["roles"]["publish"],
            },
            "source": {
                "seed_repo": envelope["source"]["seed_repo"],
                "base_sha": envelope["source"]["base_sha"],
                "paths": task["paths"],
            },
            "canonical": {
                **effective_envelope["canonical"],
                "candidate_ref": row["approved"]["candidate_ref"],
            },
            "lineage_depth": first_lineage_depth + row["plan"]["slot"] - 1,
            "qa_workspace": str(qa_path),
            "custody_dir": str(row["custody_path"]),
            "role_models": {
                "developer": envelope["role_models"]["developer"],
                "qa": envelope["role_models"]["qa"],
            },
        }
        if "max_corrections" in envelope:
            manifest["max_corrections"] = envelope["max_corrections"]
        if "resource_policy" in envelope:
            manifest["resource_policy"] = envelope["resource_policy"]
        if row["plan"]["slot"] == 1 and predecessor is not None:
            manifest["predecessor"] = predecessor["binding"]
            manifest["predecessor_base_tip"] = predecessor["base_tip"]
        elif row["plan"]["slot"] == 2:
            prior_path, prior_manifest, prior_hash = manifests[0]
            manifest["predecessor"] = {
                "manifest_path": str(prior_path),
                "manifest_sha256": prior_hash,
                "source_task_id": prior_manifest["developer"]["task_id"],
                "publish_task_id": prior_manifest["publish"]["task_id"],
                "receipt_path": str(
                    Path(prior_manifest["custody_dir"])
                    / f"publication-{prior_manifest['developer']['task_id']}.json"
                ),
            }
        data = json_bytes(manifest)
        write_once(row["manifest_path"], data, 0o600)
        load_manifest(str(row["manifest_path"]))
        manifests.append((row["manifest_path"], manifest, digest(data)))

    receipt = {
        "schema": PREPARATION_RECEIPT_SCHEMA,
        "envelope_sha256": envelope_hash,
        "planning_task_id": planning["task_id"],
        "planning_agent_id": planning["agent_id"],
        "planning_run_id": planning_run["runId"],
        "planning_attachment_id": plan_meta["id"],
        "planning_attachment_sha256": digest(plan_bytes),
        "manager_task_id": envelope["manager"]["task_id"],
        "company_id": envelope["company_id"],
        "source_base_sha": envelope["source"]["base_sha"],
        "canonical_ref": envelope["canonical"]["ref"],
        "canonical_expected_tip": effective_envelope["canonical"]["expected_tip"],
        "canonical_base_tip": envelope["canonical"]["expected_tip"],
        **({"predecessor": predecessor["binding"]} if predecessor is not None else {}),
        "slots": [
            {
                "slot": row["plan"]["slot"],
                "source_task_id": row["plan"]["source_task_id"],
                "stage_task_id": row["plan"]["stage_task_id"],
                "qa_task_id": row["plan"]["qa_task_id"],
                "publish_task_id": row["plan"]["publish_task_id"],
                "lineage_depth": manifest["lineage_depth"],
                "qa_workspace_id": row["plan"]["qa_workspace_id"],
                "qa_workspace": str(row["qa_path"]),
                "manifest_path": str(manifest_path),
                "manifest_sha256": manifest_sha,
            }
            for row, (manifest_path, manifest, manifest_sha) in zip(
                prepared_slots, manifests, strict=True
            )
        ],
    }
    if resource_receipt is not None:
        receipt["resource_receipt"] = str(resource_receipt)
    save_record(output_root / "prepare-receipt.json", receipt)
    api.complete(own_task)
    return {"status": "prepared", "receipt": str(output_root / "prepare-receipt.json")}


def latest_successful(api: Paperclip, task: dict[str, str]) -> dict[str, Any]:
    issue = api.issue(task["task_id"])
    if issue.get("status") != "done" or issue.get("assigneeAgentId") != task["agent_id"]:
        raise DeliveryError("source_or_qa_task_not_done")
    api.no_pending(task["task_id"])
    return api.latest_run(task)


def stage_path(custody: Path, developer_id: str, run_id: str) -> Path:
    return custody / f"stage-{developer_id}-{run_id}.json"


def stage_history(
    custody: Path, developer_id: str, manifest_hash: str, max_corrections: int = 1
) -> list[tuple[Path, dict[str, Any]]]:
    require(
        type(max_corrections) is int and 1 <= max_corrections <= 3,
        "max_corrections_invalid",
    )
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
            and type(value.get("revision")) is int
            and 1 <= value.get("revision") <= 1 + max_corrections,
            "stage_history_identity_invalid",
        )
        rows.append((path, value))
    rows.sort(key=lambda item: int(item[1]["revision"]))
    require(
        len(rows) <= 1 + max_corrections
        and [row[1]["revision"] for row in rows] == list(range(1, len(rows) + 1)),
        "correction_limit_reached",
    )
    for index in range(1, len(rows)):
        require(
            rows[index][1].get("previous_stage_sha256") == digest(rows[index - 1][0].read_bytes()),
            "stage_history_chain_invalid",
        )
    return rows


def next_stage_revision(prior: dict[str, Any] | None, max_corrections: int = 1) -> int:
    require(
        type(max_corrections) is int and 1 <= max_corrections <= 3,
        "max_corrections_invalid",
    )
    if prior is None:
        return 1
    revision = prior.get("revision")
    require(
        type(revision) is int and 1 <= revision <= max_corrections,
        "correction_limit_reached",
    )
    return revision + 1


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


def preserve_qa_predecessor(
    handoff: Path,
    prior: dict[str, Any],
    qa_run_id: str,
    qa_artifact: str | dict[str, Any],
    qa_result: bytes,
) -> None:
    """Keep the rejected predecessor inside the QA clone before a correction replaces it."""
    paths = prior.get("paths")
    require(
        type(paths) is list
        and all(type(path) is str for path in paths)
        and len(paths) == len(set(paths))
        and type(prior.get("files")) is dict
        and set(prior["files"]) == set(paths),
        "correction_history_record_invalid",
    )

    staged: dict[str, bytes] = {}
    for name, key in (("candidate.patch", "patch"), ("demonstration.json", "demonstration")):
        meta = prior.get(key)
        require(
            type(meta) is dict and type(meta.get("sha256")) is str,
            "correction_history_record_invalid",
        )
        path = target(handoff, name)
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode), "correction_artifact_changed")
        data = path.read_bytes()
        require(digest(data) == meta["sha256"], "correction_artifact_changed")
        staged[name] = data

    file_bytes: dict[str, bytes] = {}
    for relative in paths:
        entry = prior["files"][relative]
        require(
            type(entry) is dict and type(entry.get("sha256")) is str,
            "correction_history_record_invalid",
        )
        path = target(handoff.parent, relative)
        info = path.lstat()
        require(
            not stat.S_ISLNK(info.st_mode) and stat.S_ISREG(info.st_mode),
            "correction_target_not_regular",
        )
        data = path.read_bytes()
        require(digest(data) == entry["sha256"], "correction_target_changed")
        file_bytes[relative] = data

    record_path = target(handoff, "native-delivery.json")
    record_info = record_path.lstat()
    require(stat.S_ISREG(record_info.st_mode), "correction_artifact_changed")
    record_bytes = record_path.read_bytes()
    require(record_bytes == json_bytes(prior), "correction_artifact_changed")
    staged["native-delivery.json"] = record_bytes
    staged["QA-RESULT.json"] = qa_result
    staged.update({f"files/{relative}": data for relative, data in file_bytes.items()})
    if type(qa_artifact) is str:
        # Keep the immutable v1 attachment archive byte-identical on replay.
        snapshot_index = {
            "schema": "brain-native-qa-predecessor-history-v1",
            "revision": prior.get("revision"),
            "developer_run_id": prior.get("developer_run_id"),
            "qa_run_id": qa_run_id,
            "qa_attachment_id": qa_artifact,
            "files": {name: digest(data) for name, data in sorted(staged.items())},
        }
    else:
        snapshot_index = {
            "schema": "brain-native-qa-predecessor-history-v2",
            "revision": prior.get("revision"),
            "developer_run_id": prior.get("developer_run_id"),
            "candidate_patch_sha256": prior.get("patch", {}).get("sha256"),
            "qa_run_id": qa_run_id,
            "qa_artifact": {
                "type": qa_artifact.get("artifactType"),
                "id": qa_artifact.get("id"),
                "revision_id": qa_artifact.get("revisionId"),
                "sha256": digest(qa_result),
                "verdict": json.loads(qa_result).get("verdict"),
            },
            "files": {name: digest(data) for name, data in sorted(staged.items())},
        }
    history_root = target(handoff, "history", create=True)
    try:
        history_root.mkdir(mode=0o700)
    except FileExistsError:
        info = history_root.lstat()
        require(
            not stat.S_ISLNK(info.st_mode) and stat.S_ISDIR(info.st_mode),
            "correction_history_invalid",
        )
    snapshot = history_root / f"revision-{prior['revision']}"
    try:
        snapshot.mkdir(mode=0o700)
    except FileExistsError:
        info = snapshot.lstat()
        require(
            not stat.S_ISLNK(info.st_mode) and stat.S_ISDIR(info.st_mode),
            "correction_history_invalid",
        )
    file_root = snapshot / "files"
    try:
        file_root.mkdir(mode=0o700)
    except FileExistsError:
        info = file_root.lstat()
        require(
            not stat.S_ISLNK(info.st_mode) and stat.S_ISDIR(info.st_mode),
            "correction_history_invalid",
        )
    for name, data in staged.items():
        destination = target(snapshot, name, create=True)
        write_once(destination, data, 0o400)
    index_bytes = json_bytes(snapshot_index)
    write_once(snapshot / "history.json", index_bytes, 0o400)
    expected_files = set(staged) | {"history.json"}
    expected_dirs = {
        str(parent)
        for name in expected_files
        for parent in Path(name).parents
        if parent != Path(".")
    }
    actual_files: set[str] = set()
    actual_dirs: set[str] = set()
    for child in snapshot.rglob("*"):
        info = child.lstat()
        relative = child.relative_to(snapshot).as_posix()
        require(not stat.S_ISLNK(info.st_mode), "correction_history_invalid")
        if stat.S_ISDIR(info.st_mode):
            actual_dirs.add(relative)
        elif stat.S_ISREG(info.st_mode):
            actual_files.add(relative)
        else:
            raise DeliveryError("correction_history_invalid")
    require(
        actual_files == expected_files and actual_dirs == expected_dirs,
        "correction_history_invalid",
    )


def notify_manager(
    api: Paperclip,
    manifest: dict[str, Any],
    action: str,
    task_id: str,
    marker: str,
    body: str,
    manifest_hash: str,
    *,
    resume: bool = False,
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
        comment = {"body": body}
        if resume:
            comment["resume"] = True
        api.request("POST", f"/issues/{manager_id}/comments", comment)
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


def notify_preparation_failure(envelope_path: str, error: DeliveryError) -> None:
    """Wake the pinned planning lead after a validated prepare run fails."""
    value, envelope_hash = private_json_file(envelope_path, "preparation_envelope")
    envelope = validate_preparation_envelope(value)
    api = Paperclip(
        {
            "api_origin": envelope["api_origin"],
            "company_id": envelope["company_id"],
            "prepare": envelope["prepare"],
        }
    )
    require(
        api.current("prepare", checkout_if_missing=False) == envelope["prepare"]["task_id"],
        "preparation_failure_run_not_pinned",
    )
    planning = envelope["planning"]
    planning_issue = api.issue(planning["task_id"])
    require(
        planning_issue.get("id") == planning["task_id"]
        and planning_issue.get("companyId") == envelope["company_id"]
        and planning_issue.get("assigneeAgentId") == planning["agent_id"]
        and planning_issue.get("status") == "done",
        "preparation_failure_planning_task_not_routable",
    )
    api.no_pending(planning["task_id"])
    reason = str(error).replace("\r", " ").replace("\n", " ")[:500]
    marker = f"prepare-failure:{envelope_hash}:{reason}"
    body = (
        f"Native preparation failed for envelope {envelope_hash}: {reason}. "
        f"Review the preparation inputs and instructions, then continue through this existing "
        f"planning task for prepare task {envelope['prepare']['task_id']}. Do not replay any "
        f"acknowledged setup effects."
    )
    notify_manager(
        api,
        {
            "manager_task_id": planning["task_id"],
            "custody_dir": str(Path(envelope_path).parent),
        },
        "prepare_failure",
        envelope["prepare"]["task_id"],
        marker,
        body,
        envelope_hash,
        resume=True,
    )


def stage(m: dict[str, Any], manifest_hash: str) -> dict[str, Any]:
    api = Paperclip(m)
    own_task = api.current("stage")
    require(own_task == m["stage"]["task_id"], "task_not_pinned")
    resource_receipt = _resource_preflight(
        api,
        m.get("resource_policy"),
        "stage",
        Path(m["custody_dir"]),
        reserve_model_runs=8,
    )
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
    max_corrections = m.get("max_corrections", 1)
    history = stage_history(custody, developer["task_id"], manifest_hash, max_corrections)
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
        revision = next_stage_revision(prior, max_corrections)
        if revision > 1:
            require(m.get("manager_task_id"), "manager_task_not_pinned")
        if prior is not None:
            prior_qa_run = latest_successful(api, qa)
            prior_meta, prior_result = api.qa_artifact(
                qa, prior_qa_run, "QA-RESULT.json", 256 * 1024
            )
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
                prior_meta.get("originatingRunId") == prior_qa_run["runId"]
                and prior_meta.get("createdByAgentId") == qa["agent_id"],
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
            preserve_qa_predecessor(
                prior_handoff,
                prior,
                prior_qa_run["runId"],
                prior_meta["id"]
                if prior_meta.get("artifactType") == "attachment"
                else prior_meta,
                prior_result,
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
    if revision > 1:
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
        **({} if resource_receipt is None else {"resource_receipt": str(resource_receipt)}),
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


def predecessor_expected_tip(m: dict[str, Any], manifest_hash: str, api: Paperclip) -> str | None:
    """Resolve a second slot only from its explicitly pinned first-slot receipt."""
    binding = m.get("predecessor")
    if binding is None:
        return None
    parent, parent_hash = load_manifest(binding["manifest_path"])
    require(parent_hash == binding["manifest_sha256"], "predecessor_manifest_hash_mismatch")
    parent_depth = parent.get("lineage_depth", 0)
    if "lineage_depth" in m or "lineage_depth" in parent or "predecessor" in parent:
        require(
            type(parent_depth) is int
            and 0 <= parent_depth <= 8
            and ("predecessor" not in parent or parent_depth > 0)
            and type(m.get("lineage_depth")) is int
            and m["lineage_depth"] == parent_depth + 1
            and m["lineage_depth"] <= 8,
            "predecessor_lineage_depth_invalid",
        )
    require(
        parent["company_id"] == m["company_id"]
        and parent["source"]["base_sha"] == m["source"]["base_sha"]
        and parent["canonical"]["repo"] == m["canonical"]["repo"]
        and parent["canonical"]["ref"] == m["canonical"]["ref"]
        and parent["canonical"]["remote_name"] == m["canonical"]["remote_name"]
        and parent["canonical"]["remote_url"] == m["canonical"]["remote_url"]
        and parent["canonical"]["expected_tip"]
        == m.get("predecessor_base_tip", m["canonical"]["expected_tip"]),
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
    api.no_pending_terminal(binding["publish_task_id"])
    parent_runs = api.request("GET", f"/issues/{binding['publish_task_id']}/runs")
    publisher_runs = (
        [
            row
            for row in parent_runs
            if type(row) is dict
            and row.get("agentId") == parent["publish"]["agent_id"]
            and row.get("contextIssueId") == binding["publish_task_id"]
        ]
        if type(parent_runs) is list
        else []
    )
    require(
        bool(publisher_runs)
        and all(
            row.get("status") in {"succeeded", "failed", "cancelled", "timed_out"}
            for row in publisher_runs
        ),
        "predecessor_publish_run_not_terminal",
    )
    latest = max(
        publisher_runs,
        key=lambda row: str(row.get("finishedAt") or row.get("startedAt") or ""),
    )
    run_id = latest.get("runId") or latest.get("id")
    require(
        latest.get("status") == "succeeded" and UUID.fullmatch(str(run_id or "")),
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
    publisher._check_candidate(
        repo.resolve(strict=True), parent["canonical"]["ref"], receipt, tuple(paths)
    )
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
    local_head = git(repo, "rev-parse", parent["canonical"]["ref"]).decode().strip()
    require(
        local_head == candidate or (own_candidate is not None and local_head == own_candidate),
        "predecessor_canonical_head_unexpected",
    )
    if local_head == candidate:
        publisher.inspect(repo, parent["canonical"]["ref"], receipt, allowed_paths=paths)
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
    qa_meta, qa_data = api.qa_artifact(qa, qa_run, "QA-RESULT.json", 256 * 1024)
    require(
        qa_meta.get("originatingRunId") == qa_run["runId"]
        and qa_meta.get("createdByAgentId") == qa["agent_id"],
        "qa_artifact_binding_invalid",
    )
    validate_qa(
        qa_data, dev["task_id"], record["patch"]["sha256"], record["files"], record["paths"]
    )
    native = Path(m["custody_dir"]) / f"native-handoff-{dev['task_id']}-{source_run['runId']}"
    files = {p: target(native / "files", p).read_bytes() for p in record["paths"]}
    if any(digest(files[p]) != record["files"][p]["sha256"] for p in record["paths"]):
        raise DeliveryError("custodied_file_hash_mismatch")
    c = slot["canonical"]
    repo = Path(c["repo"])
    expected = publication_expected_tip(m, manifest_hash, api)
    if "predecessor_base_tip" in m:
        require(expected == c["expected_tip"], "predecessor_expected_tip_changed")
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
        "qa_sha256": digest(qa_data),
        "source_patch_sha256": record["patch"]["sha256"],
        "files": {p: record["files"][p]["sha256"] for p in record["paths"]},
        "expected_tip": expected,
        "canonical_ref": c["ref"],
        "candidate_ref": c["candidate_ref"],
    }
    if qa_meta.get("artifactType") == "document_revision":
        op["qa_document_id"] = qa_meta["id"]
        op["qa_document_revision_id"] = qa_meta["revisionId"]
    else:
        op["qa_attachment_id"] = qa_meta["id"]
    # Quota is checked before publisher.prepare writes Git objects, reconcile advances the local
    # canonical ref, or either remote ref is pushed. Each attempt gets a distinct immutable receipt.
    resource_receipt = _resource_preflight(
        api,
        m.get("resource_policy"),
        "publish",
        Path(m["custody_dir"]),
        reserve_model_runs=8,
    )
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
        f"Native publication passed remote readback for {dev['task_id']}: candidate {candidate}."
        + ("" if resource_receipt is None else f" Resource receipt: {resource_receipt}."),
        manifest_hash,
    )
    api.complete(own_task)
    return {
        "status": "published",
        "candidate_sha": candidate,
        "receipt": str(receipt_file),
        **({} if resource_receipt is None else {"resource_receipt": str(resource_receipt)}),
    }


def publication_expected_tip(m: dict[str, Any], manifest_hash: str, api: Paperclip) -> str:
    """Resolve the publish base while preserving legacy in-campaign slot chaining."""
    expected = predecessor_expected_tip(m, manifest_hash, api)
    if expected is not None:
        if "predecessor_base_tip" in m:
            require(expected == m["canonical"]["expected_tip"], "predecessor_expected_tip_changed")
        return expected
    return m["canonical"]["expected_tip"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "stage", "publish"))
    parser.add_argument("--manifest")
    parser.add_argument("--envelope")
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            if args.envelope is None or args.manifest is not None:
                raise DeliveryError("preparation_envelope_required")
            result = prepare(args.envelope)
        else:
            if args.manifest is None or args.envelope is not None:
                raise DeliveryError("manifest_required")
            m, sha = load_manifest(args.manifest)
            result = stage(m, sha) if args.action == "stage" else publish(m, sha)
    except (DeliveryError, OSError, ValueError) as exc:
        if (
            args.action == "prepare"
            and args.envelope is not None
            and isinstance(exc, DeliveryError)
        ):
            try:
                notify_preparation_failure(args.envelope, exc)
            except Exception:
                pass
        print(
            json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True), file=sys.stderr
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
