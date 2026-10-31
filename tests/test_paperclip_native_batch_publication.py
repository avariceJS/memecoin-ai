"""Focused checks for the Luna-led batch acceptance and immutable Git input."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from engineering import paperclip_native_delivery as delivery

_XCODE_GIT = Path("/Applications/Xcode.app/Contents/Developer/usr/bin/git")
GIT_BIN = str(_XCODE_GIT) if _XCODE_GIT.is_file() else "/usr/bin/git"


@pytest.fixture(autouse=True)
def use_local_git_binary(monkeypatch):
    monkeypatch.setattr(delivery, "GIT", GIT_BIN)
    monkeypatch.setattr(delivery.publisher, "_GIT", GIT_BIN)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def acceptance(**overrides):
    value = {
        "schema": delivery.BATCH_ACCEPTANCE_SCHEMA,
        "decision": "accept",
        "source_base_sha": "1" * 40,
        "assembled_tip": "2" * 40,
        "canonical_expected_tip": "3" * 40,
        "files": {"src/mod.py": sha(b"new\n"), "tests/test_mod.py": sha(b"test\n")},
        "source_task_ids": ["a" * 8 + "-1111-4111-8111-111111111111"],
        "checks": [{"name": "focused behavior", "result": "passed"}],
    }
    value.update(overrides)
    return value


class FakeApi:
    m = {"company_id": "c" * 8 + "-1111-4111-8111-111111111111"}

    def __init__(self, document):
        self.document = document

    def request(self, method, route):
        assert method == "GET"
        assert route.endswith("/documents/batch-acceptance")
        return self.document


def acceptance_doc(body: str):
    return {
        "id": "d" * 8 + "-1111-4111-8111-111111111111",
        "companyId": FakeApi.m["company_id"],
        "issueId": "b" * 8 + "-1111-4111-8111-111111111111",
        "key": "batch-acceptance",
        "body": body,
        "latestRevisionId": "e" * 8 + "-1111-4111-8111-111111111111",
        "createdByAgentId": "f" * 8 + "-1111-4111-8111-111111111111",
        "updatedByAgentId": "f" * 8 + "-1111-4111-8111-111111111111",
    }


def test_acceptance_binds_native_document_and_strict_body():
    body = json.dumps(acceptance())
    result, revision, body_hash = delivery._batch_acceptance(
        FakeApi(acceptance_doc(body)),
        {"id": acceptance_doc(body)["issueId"]},
        acceptance_doc(body)["createdByAgentId"],
    )
    assert result["decision"] == "accept"
    assert revision == acceptance_doc(body)["latestRevisionId"]
    assert body_hash == sha(body.encode())


def test_acceptance_v2_requires_explicit_parent_close_opt_in():
    body = json.dumps(acceptance(schema=delivery.BATCH_ACCEPTANCE_V2_SCHEMA, close_parent=True))
    doc = acceptance_doc(body)
    parsed, _, _ = delivery._batch_acceptance(
        FakeApi(doc), {"id": doc["issueId"]}, doc["createdByAgentId"]
    )
    assert parsed["close_parent"] is True
    for overrides in ({"close_parent": False}, {}):
        invalid = acceptance(schema=delivery.BATCH_ACCEPTANCE_V2_SCHEMA, close_parent=True)
        if overrides:
            invalid.update(overrides)
        else:
            invalid.pop("close_parent")
        invalid["schema"] = delivery.BATCH_ACCEPTANCE_V2_SCHEMA
        candidate = acceptance_doc(json.dumps(invalid))
        with pytest.raises(delivery.DeliveryError, match="batch_acceptance_invalid"):
            delivery._batch_acceptance(
                FakeApi(candidate), {"id": candidate["issueId"]}, candidate["createdByAgentId"]
            )


def test_acceptance_rejects_wrong_document_owner():
    body = json.dumps(acceptance())
    doc = acceptance_doc(body)
    doc["updatedByAgentId"] = "0" * 8 + "-1111-4111-8111-111111111111"
    with pytest.raises(delivery.DeliveryError, match="batch_acceptance_document_invalid"):
        delivery._batch_acceptance(FakeApi(doc), {"id": doc["issueId"]}, doc["createdByAgentId"])


@pytest.mark.parametrize(
    "body",
    [
        '{"schema":"x","schema":"y"}',
        '{"schema":"x","n":NaN}',
    ],
)
def test_strict_json_rejects_duplicate_keys_and_nonfinite_values(body):
    with pytest.raises(delivery.DeliveryError):
        delivery._strict_json_object(body, "bad")


@pytest.mark.parametrize(
    "file_map",
    [
        {"src/../escape.py": "0" * 64, "tests/t.py": "0" * 64},
        {"src\\escape.py": "0" * 64, "tests/t.py": "0" * 64},
        {"src/a.py": "0" * 63, "tests/t.py": "0" * 64},
        {"src/a.py": "0" * 64},
    ],
)
def test_acceptance_rejects_unsafe_file_maps(file_map):
    body = json.dumps(acceptance(files=file_map))
    doc = acceptance_doc(body)
    with pytest.raises(delivery.DeliveryError):
        delivery._batch_acceptance(FakeApi(doc), {"id": doc["issueId"]}, doc["createdByAgentId"])


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output([GIT_BIN, "-C", str(repo), *args], text=True).strip()


def commit(repo: Path, message: str) -> str:
    subprocess.run([GIT_BIN, "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        [
            GIT_BIN,
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            message,
        ],
        check=True,
    )
    return git(repo, "rev-parse", "HEAD")


def test_source_extracts_only_exact_regular_utf8_blobs_and_old_projection(tmp_path):
    source = tmp_path / "source"
    canonical = tmp_path / "canonical"
    for repo in (source, canonical):
        repo.mkdir()
        subprocess.run([GIT_BIN, "init", "-q", str(repo)], check=True)
    for repo in (source, canonical):
        (repo / "src").mkdir()
        (repo / "tests").mkdir()
        (repo / "src/mod.py").write_bytes(b"old\n")
        (repo / "tests/test_mod.py").write_bytes(b"old test\n")
        (repo / "README.md").write_bytes(b"preserve me\n")
    source_base = commit(source, "base")
    canonical_base = commit(canonical, "canonical")
    (source / "src/mod.py").write_bytes(b"new\n")
    (source / "tests/test_mod.py").write_bytes(b"test\n")
    assembled = commit(source, "batch")
    accept = acceptance(
        source_base_sha=source_base,
        assembled_tip=assembled,
        canonical_expected_tip=canonical_base,
        files={"src/mod.py": sha(b"new\n"), "tests/test_mod.py": sha(b"test\n")},
    )
    old, new, files = delivery._batch_source(accept, source, canonical, canonical_base)
    assert set(files) == set(accept["files"])
    assert files["src/mod.py"] == b"new\n"
    assert old["src/mod.py"] is not None
    assert new["tests/test_mod.py"] != old["tests/test_mod.py"]
    canonical_ref = git(canonical, "symbolic-ref", "HEAD")
    prepared = delivery.publisher.prepare(
        canonical,
        canonical_ref,
        canonical_base,
        old,
        new,
        files,
        allowed_paths=list(accept["files"]),
    )
    inspected = delivery.publisher.inspect(
        canonical, canonical_ref, prepared, allowed_paths=list(accept["files"])
    )
    assert inspected["ref_state"] == "prepared"
    done = delivery.publisher.reconcile(
        canonical,
        canonical_ref,
        prepared,
        prepared["candidate"],
        prepared["receipt_digest"],
        allowed_paths=list(accept["files"]),
    )
    assert done["status"] == "reconciled"
    assert git(canonical, "rev-parse", canonical_ref) == prepared["candidate"]
    assert (canonical / "README.md").read_bytes() == b"preserve me\n"
    assert (canonical / "src/mod.py").read_bytes() == b"new\n"


def test_source_rejects_extra_changes_and_hash_mismatch(tmp_path):
    source = tmp_path / "source"
    canonical = tmp_path / "canonical"
    for repo in (source, canonical):
        repo.mkdir()
        subprocess.run([GIT_BIN, "init", "-q", str(repo)], check=True)
        (repo / "src").mkdir()
        (repo / "tests").mkdir()
        (repo / "src/mod.py").write_bytes(b"old\n")
        (repo / "tests/test_mod.py").write_bytes(b"old test\n")
    base = commit(source, "base")
    canonical_base = commit(canonical, "base")
    (source / "src/mod.py").write_bytes(b"new\n")
    (source / "tests/test_mod.py").write_bytes(b"test\n")
    (source / "src/extra.py").write_bytes(b"extra\n")
    tip = commit(source, "extra")
    accepted = acceptance(
        source_base_sha=base, assembled_tip=tip, canonical_expected_tip=canonical_base
    )
    with pytest.raises(delivery.DeliveryError, match="batch_source_diff_mismatch"):
        delivery._batch_source(accepted, source, canonical, canonical_base)
    accepted["files"]["src/extra.py"] = sha(b"wrong\n")
    with pytest.raises(delivery.DeliveryError, match="batch_source_file_hash_invalid"):
        delivery._batch_source(accepted, source, canonical, canonical_base)


def test_source_accepts_unrelated_base_tree_but_rejects_changed_old_blob(tmp_path):
    canonical, source, base, _, _, _ = _make_batch_repositories(tmp_path)
    alternate = tmp_path / "alternate"
    subprocess.run([GIT_BIN, "init", "-q", str(alternate)], check=True)
    (alternate / "src").mkdir()
    (alternate / "tests").mkdir()
    (alternate / "src/mod.py").write_bytes(b"old\n")
    (alternate / "tests/test_mod.py").write_bytes(b"old test\n")
    (alternate / "README.md").write_bytes(b"preserve me\n")
    unrelated_base = _commit_repo(alternate, "same tree, unrelated history")
    subprocess.run(
        [GIT_BIN, "-C", str(source), "fetch", str(alternate), unrelated_base], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    assembled = git(source, "rev-parse", "HEAD")
    accepted = acceptance(
        source_base_sha=unrelated_base,
        assembled_tip=assembled,
        canonical_expected_tip=base,
        files={"src/mod.py": sha(b"new\n"), "tests/test_mod.py": sha(b"test\n")},
    )
    old, _, _ = delivery._batch_source(accepted, source, canonical, base)
    assert all(old.values())
    (canonical / "src/mod.py").write_bytes(b"drift\n")
    drift = _commit_repo(canonical, "changed canonical old blob")
    accepted["canonical_expected_tip"] = drift
    with pytest.raises(delivery.DeliveryError, match="batch_canonical_old_blob_mismatch"):
        delivery._batch_source(accepted, source, canonical, drift)


@pytest.mark.parametrize("action", ["stage", "publish"])
def test_existing_cli_actions_keep_manifest_dispatch(monkeypatch, capsys, action):
    calls = []
    monkeypatch.setattr(delivery, "load_manifest", lambda _: ({"old": True}, "hash"))
    monkeypatch.setattr(
        delivery, "stage", lambda m, h: calls.append(("stage", m, h)) or {"route": "stage"}
    )
    monkeypatch.setattr(
        delivery, "publish", lambda m, h: calls.append(("publish", m, h)) or {"route": "publish"}
    )
    assert delivery.main([action, "--manifest", "old.json"]) == 0
    assert calls[0][0] == action
    assert json.loads(capsys.readouterr().out)["route"] == action


COMPANY = "c0000000-1111-4111-8111-111111111111"
PUBLISHER = "10000000-1111-4111-8111-111111111111"
LEAD = "20000000-1111-4111-8111-111111111111"
OTHER_LEADS = [
    "21000000-1111-4111-8111-111111111111",
    "22000000-1111-4111-8111-111111111111",
    "23000000-1111-4111-8111-111111111111",
    "24000000-1111-4111-8111-111111111111",
    "25000000-1111-4111-8111-111111111111",
]
DEVELOPER = "30000000-1111-4111-8111-111111111111"
BATCH = "40000000-1111-4111-8111-111111111111"
PUBLISH_TASK = "50000000-1111-4111-8111-111111111111"
SOURCE_TASK = "60000000-1111-4111-8111-111111111111"
PROCESS_RUN_1 = "70000000-1111-4111-8111-111111111111"
SOURCE_RUN = "80000000-1111-4111-8111-111111111111"
ACCEPTANCE_DOC = "90000000-1111-4111-8111-111111111111"
ACCEPTANCE_REV = "a0000000-1111-4111-8111-111111111111"
RECEIPT_REV = "b0000000-1111-4111-8111-111111111111"


def _commit_repo(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    subprocess.run(
        [
            GIT_BIN,
            "-C",
            str(repo),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            message,
        ],
        check=True,
    )
    return git(repo, "rev-parse", "HEAD")


def _make_batch_repositories(tmp_path: Path):
    canonical = tmp_path / "canonical"
    source = tmp_path / "source"
    canonical.mkdir()
    subprocess.run(
        [GIT_BIN, "-c", "init.defaultBranch=brain/development", "init", "-q", str(canonical)],
        check=True,
    )
    (canonical / "src").mkdir()
    (canonical / "tests").mkdir()
    (canonical / "src/mod.py").write_bytes(b"old\n")
    (canonical / "tests/test_mod.py").write_bytes(b"old test\n")
    (canonical / "README.md").write_bytes(b"preserve me\n")
    base = _commit_repo(canonical, "canonical base")
    branch = git(canonical, "symbolic-ref", "HEAD")
    subprocess.run([GIT_BIN, "clone", "-q", "--local", str(canonical), str(source)], check=True)
    (source / "src/mod.py").write_bytes(b"new\n")
    (source / "tests/test_mod.py").write_bytes(b"test\n")
    assembled = _commit_repo(source, "assembled batch")
    files = {"src/mod.py": sha(b"new\n"), "tests/test_mod.py": sha(b"test\n")}
    accepted = acceptance(
        source_base_sha=base,
        assembled_tip=assembled,
        canonical_expected_tip=base,
        files=files,
        source_task_ids=[SOURCE_TASK],
    )
    body = json.dumps(accepted, sort_keys=True, separators=(",", ":"))
    acceptance_document = {
        "id": ACCEPTANCE_DOC,
        "companyId": COMPANY,
        "issueId": BATCH,
        "key": "batch-acceptance",
        "body": body,
        "latestRevisionId": ACCEPTANCE_REV,
        "createdByAgentId": LEAD,
        "updatedByAgentId": LEAD,
    }
    return canonical, source, base, branch, files, acceptance_document


class BatchApiFixture:
    def __init__(self, document: dict, publisher_run_id: str):
        self.document = document
        self.publisher_run_id = publisher_run_id
        self.publisher_status = "in_progress"
        self.checkout_run_id = publisher_run_id
        self.execution_run_id = publisher_run_id
        self.recovery_action = None
        self.source_context_id = SOURCE_TASK
        self.receipt_document = None
        self.receipt_puts = 0
        self.completions = 0
        self.parent_status = "in_progress"
        self.parent_blockers = []
        self.parent_descriptor = None
        self.parent_runs = []
        self.parent_hold = None
        self.parent_patches = 0
        self.events = []
        self.parent_reads = 0
        self.fresh_parent_after_initial = False

    def request(
        self,
        api: delivery.Paperclip,
        method: str,
        route: str,
        body: object | None = None,
        raw: bool = False,
    ):
        assert not raw
        if method == "GET" and route == "/agents/me":
            return {"id": PUBLISHER, "companyId": COMPANY}
        if method == "GET" and route.startswith("/heartbeat-runs/"):
            run_id = route.rsplit("/", 1)[1]
            if run_id == self.publisher_run_id:
                return {
                    "id": run_id,
                    "agentId": PUBLISHER,
                    "companyId": COMPANY,
                    "runtimeMode": "legacy",
                    "status": "running",
                    "contextSnapshot": {"issueId": PUBLISH_TASK},
                }
            if run_id == SOURCE_RUN:
                return {
                    "id": run_id,
                    "agentId": DEVELOPER,
                    "companyId": COMPANY,
                    "issueId": SOURCE_TASK,
                    "nativeIssueId": SOURCE_TASK,
                    "runtimeMode": "native",
                    "status": "succeeded",
                    "runnerProfileJson": {
                        "nativeExecutionInput": {
                            "provider": {
                                "kind": "codex",
                                "model": "gpt-6-luna",
                                "reasoningEffort": "high",
                            }
                        }
                    },
                    "usageJson": {"billingType": "subscription"},
                }
            raise AssertionError(f"unexpected run {run_id}")
        if method == "GET" and route == f"/issues/{PUBLISH_TASK}":
            return {
                "id": PUBLISH_TASK,
                "companyId": COMPANY,
                "parentId": BATCH,
                "assigneeAgentId": PUBLISHER,
                "createdByAgentId": LEAD,
                "status": self.publisher_status,
                "checkoutRunId": self.checkout_run_id,
                "executionRunId": self.execution_run_id,
                "activeRecoveryAction": self.recovery_action,
            }
        if method == "GET" and route == f"/issues/{BATCH}":
            value = {
                "id": BATCH,
                "companyId": COMPANY,
                "status": self.parent_status,
                "assigneeAgentId": LEAD,
                "activeRecoveryAction": None,
                "reviewPolicy": "anyone",
                "blockedBy": [{"id": blocker} for blocker in self.parent_blockers],
                "unblockDescriptor": self.parent_descriptor,
                **(self.parent_hold or {}),
            }
            self.parent_reads += 1
            if self.fresh_parent_after_initial and self.parent_reads == 2:
                self.parent_status = "blocked"
                self.parent_blockers = [SOURCE_TASK, PUBLISH_TASK]
            return value
        if method == "GET" and route == f"/issues/{BATCH}/runs":
            rows = self.parent_runs
            if self.fresh_parent_after_initial and self.parent_reads >= 2:
                self.parent_runs = []
            return rows
        if method == "GET" and route == f"/issues/{SOURCE_TASK}":
            return {
                "id": SOURCE_TASK,
                "companyId": COMPANY,
                "parentId": BATCH,
                "assigneeAgentId": DEVELOPER,
                "status": "done",
                "activeRecoveryAction": None,
            }
        if method == "GET" and route.endswith("/interactions"):
            return {"interactions": []}
        if method == "GET" and route == f"/companies/{COMPANY}":
            return {"id": COMPANY, "status": "active"}
        if method == "GET" and route == f"/agents/{LEAD}":
            return {
                "id": LEAD,
                "companyId": COMPANY,
                "adapterConfig": {"model": "gpt-6-luna", "modelReasoningEffort": "high"},
            }
        if method == "GET" and route == f"/agents/{DEVELOPER}":
            return {
                "id": DEVELOPER,
                "companyId": COMPANY,
                "adapterConfig": {"model": "gpt-6-luna", "modelReasoningEffort": "high"},
            }
        if method == "GET" and route == f"/issues/{BATCH}/documents/batch-acceptance":
            return self.document
        if method == "GET" and route == f"/issues/{SOURCE_TASK}/runs":
            return [
                {
                    "agentId": DEVELOPER,
                    "contextIssueId": self.source_context_id,
                    "status": "succeeded",
                    "runId": SOURCE_RUN,
                    "finishedAt": "2026-10-31T00:00:00Z",
                }
            ]
        if method == "GET" and route == f"/issues/{PUBLISH_TASK}/documents?includeSystem=true":
            if self.receipt_document is None:
                return []
            return [
                {
                    k: self.receipt_document[k]
                    for k in ("id", "companyId", "issueId", "key", "latestRevisionId")
                }
            ]
        if method == "GET" and route == f"/issues/{PUBLISH_TASK}/documents/publication-receipt":
            return self.receipt_document
        if method == "PUT" and route == f"/issues/{PUBLISH_TASK}/documents/publication-receipt":
            assert isinstance(body, dict)
            assert body["format"] == "markdown" and body["baseRevisionId"] is None
            self.receipt_puts += 1
            self.receipt_document = {
                "id": "c0000000-1111-4111-8111-111111111111",
                "companyId": COMPANY,
                "issueId": PUBLISH_TASK,
                "key": "publication-receipt",
                "body": body["body"],
                "latestRevisionId": RECEIPT_REV,
            }
            return self.receipt_document
        if method == "PATCH" and route == f"/issues/{PUBLISH_TASK}":
            assert body == {"status": "done"}
            self.events.append("publisher_done")
            self.publisher_status = "done"
            self.completions += 1
            return {"id": PUBLISH_TASK, "status": "done"}
        if method == "PATCH" and route == f"/issues/{BATCH}":
            assert body == {
                "status": "done",
                "blockedByIssueIds": [SOURCE_TASK],
                "unblockDescriptor": None,
            }
            self.events.append("parent_done")
            self.parent_patches += 1
            self.parent_status = "done"
            self.parent_blockers = [SOURCE_TASK]
            self.parent_descriptor = None
            return {"id": BATCH, "status": "done"}
        raise AssertionError(f"unexpected API request: {method} {route}")


def _run_batch(
    monkeypatch, api_fixture: BatchApiFixture, policy: dict, policy_sha: str, run_id: str
):
    monkeypatch.setenv("PAPERCLIP_API_KEY", "fixture-api-token")
    monkeypatch.setenv("PAPERCLIP_RUN_ID", run_id)
    monkeypatch.setenv("PAPERCLIP_AGENT_ID", PUBLISHER)
    monkeypatch.setenv("PAPERCLIP_COMPANY_ID", COMPANY)
    monkeypatch.setenv("PAPERCLIP_API_URL", "http://127.0.0.1:3310")
    monkeypatch.delenv("PAPERCLIP_TASK_ID", raising=False)
    assert "PAPERCLIP_TASK_ID" not in os.environ

    def request(api, method, route, body=None, raw=False):
        return api_fixture.request(api, method, route, body=body, raw=raw)

    monkeypatch.setattr(delivery.Paperclip, "request", request)
    return delivery.publish_batch(policy, policy_sha)


def test_publish_batch_success_derives_task_and_reuses_saved_receipt_on_new_run(
    tmp_path, monkeypatch
):
    canonical, source, base, branch, files, document = _make_batch_repositories(tmp_path)
    custody = tmp_path / "custody"
    custody.mkdir(mode=0o700)
    policy = {
        "api_origin": "http://127.0.0.1:3310",
        "company_id": COMPANY,
        "publisher_agent_id": PUBLISHER,
        "lead_agent_ids": [LEAD, *OTHER_LEADS],
        "source_seed_repo": str(source),
        "custody_dir": str(custody),
        "canonical": {
            "repo": str(canonical),
            "ref": branch,
            "remote_name": "origin",
            "remote_url": "https://github.com/avariceJS/memecoin-ai.git",
        },
    }
    fake_api = BatchApiFixture(document, PROCESS_RUN_1)
    remote = {branch: base}
    pushes = []

    def remote_refs(_manifest):
        return dict(remote)

    def push_ref(_manifest, source_ref, target_ref):
        pushes.append(target_ref)
        remote[target_ref] = source_ref

    monkeypatch.setattr(delivery, "remote_refs", remote_refs)
    monkeypatch.setattr(delivery, "push_ref", push_ref)
    prepare_calls = []
    real_prepare = delivery.publisher.prepare

    def counted_prepare(*args, **kwargs):
        prepare_calls.append(True)
        return real_prepare(*args, **kwargs)

    monkeypatch.setattr(delivery.publisher, "prepare", counted_prepare)
    first = _run_batch(monkeypatch, fake_api, policy, "f" * 64, PROCESS_RUN_1)
    assert first["status"] == "published"
    assert fake_api.receipt_puts == 1 and fake_api.completions == 1
    assert (canonical / "README.md").read_bytes() == b"preserve me\n"
    assert (canonical / "src/mod.py").read_bytes() == b"new\n"
    first_doc_body = fake_api.receipt_document["body"]
    receipt = json.loads(first_doc_body)
    assert receipt["native_process_run_id"] == PROCESS_RUN_1
    assert receipt["source_run_ids"] == [SOURCE_RUN]
    assert remote[branch] == receipt["candidate_sha"]

    process_run_2 = "d0000000-1111-4111-8111-111111111111"
    fake_api.publisher_run_id = process_run_2
    fake_api.checkout_run_id = process_run_2
    fake_api.execution_run_id = process_run_2
    fake_api.publisher_status = "in_progress"
    second = _run_batch(monkeypatch, fake_api, policy, "f" * 64, process_run_2)
    assert second == first
    assert fake_api.receipt_puts == 1 and fake_api.completions == 2
    assert fake_api.receipt_document["body"] == first_doc_body
    assert json.loads(first_doc_body)["native_process_run_id"] == PROCESS_RUN_1
    assert len(prepare_calls) == 1


def test_publish_batch_v2_closes_fresh_yielded_parent_before_own_completion(tmp_path, monkeypatch):
    canonical, source, base, branch, _, document = _make_batch_repositories(tmp_path)
    acceptance_body = json.loads(document["body"])
    acceptance_body.update({"schema": delivery.BATCH_ACCEPTANCE_V2_SCHEMA, "close_parent": True})
    document["body"] = json.dumps(acceptance_body, sort_keys=True, separators=(",", ":"))
    custody = tmp_path / "custody"
    custody.mkdir(mode=0o700)
    policy = {
        "api_origin": "http://127.0.0.1:3310", "company_id": COMPANY,
        "publisher_agent_id": PUBLISHER, "lead_agent_ids": [LEAD, *OTHER_LEADS],
        "source_seed_repo": str(source), "custody_dir": str(custody),
        "canonical": {"repo": str(canonical), "ref": branch, "remote_name": "origin",
                      "remote_url": "https://github.com/avariceJS/memecoin-ai.git"},
    }
    fake_api = BatchApiFixture(document, PROCESS_RUN_1)
    fake_api.parent_status = "in_progress"
    fake_api.parent_blockers = [SOURCE_TASK]
    fake_api.parent_descriptor = {"owner": {"agentId": LEAD}}
    fake_api.fresh_parent_after_initial = True
    fake_api.parent_runs = [
        {"agentId": LEAD, "contextIssueId": BATCH, "status": "running"}
    ]
    monkeypatch.setattr(delivery.time, "sleep", lambda _seconds: None)
    remote = {branch: base}
    monkeypatch.setattr(delivery, "remote_refs", lambda _m: dict(remote))
    prepare_calls = []
    real_prepare = delivery.publisher.prepare

    def counted_prepare(*args, **kwargs):
        prepare_calls.append(True)
        return real_prepare(*args, **kwargs)

    monkeypatch.setattr(delivery.publisher, "prepare", counted_prepare)

    def push_ref(_manifest, source_ref, target_ref):
        remote[target_ref] = source_ref

    monkeypatch.setattr(delivery, "push_ref", push_ref)
    result = _run_batch(monkeypatch, fake_api, policy, "f" * 64, PROCESS_RUN_1)
    assert result["status"] == "published"
    assert fake_api.parent_patches == 1
    assert fake_api.events == ["parent_done", "publisher_done"]
    assert fake_api.parent_blockers == [SOURCE_TASK]
    assert fake_api.completions == 1

    retry_run = "d0000000-1111-4111-8111-111111111111"
    fake_api.publisher_run_id = retry_run
    fake_api.checkout_run_id = retry_run
    fake_api.execution_run_id = retry_run
    fake_api.publisher_status = "in_progress"
    retried = _run_batch(monkeypatch, fake_api, policy, "f" * 64, retry_run)
    assert retried == result
    assert fake_api.parent_patches == 1 and fake_api.receipt_puts == 1
    assert fake_api.events == ["parent_done", "publisher_done", "publisher_done"]
    assert prepare_calls == [True]


def test_publish_batch_done_v2_without_saved_operation_stops_before_prepare_or_refs(
    tmp_path, monkeypatch
):
    canonical, source, base, branch, _, document = _make_batch_repositories(tmp_path)
    acceptance_body = json.loads(document["body"])
    acceptance_body.update({"schema": delivery.BATCH_ACCEPTANCE_V2_SCHEMA, "close_parent": True})
    document["body"] = json.dumps(acceptance_body, sort_keys=True, separators=(",", ":"))
    custody = tmp_path / "custody"
    custody.mkdir(mode=0o700)
    policy = {
        "api_origin": "http://127.0.0.1:3310", "company_id": COMPANY,
        "publisher_agent_id": PUBLISHER, "lead_agent_ids": [LEAD, *OTHER_LEADS],
        "source_seed_repo": str(source), "custody_dir": str(custody),
        "canonical": {"repo": str(canonical), "ref": branch, "remote_name": "origin",
                      "remote_url": "https://github.com/avariceJS/memecoin-ai.git"},
    }
    fake_api = BatchApiFixture(document, PROCESS_RUN_1)
    fake_api.parent_status = "done"
    fake_api.parent_blockers = [SOURCE_TASK]
    ref_reads = []
    prepares = []
    monkeypatch.setattr(delivery, "remote_refs", lambda _m: ref_reads.append(True) or {branch: base})
    monkeypatch.setattr(delivery.publisher, "prepare", lambda *a, **k: prepares.append(True))
    with pytest.raises(delivery.DeliveryError, match="batch_done_receipt_missing"):
        _run_batch(monkeypatch, fake_api, policy, "f" * 64, PROCESS_RUN_1)
    assert prepares == [] and ref_reads == []


@pytest.mark.parametrize(
    ("reason", "descriptor", "blockers", "runs", "hold"),
    [
        ("batch_parent_lead_still_active", {"owner": {"agentId": LEAD}},
         [SOURCE_TASK, PUBLISH_TASK],
         [{"agentId": LEAD, "contextSnapshot": {"issueId": BATCH}, "status": "running"}], None),
        ("batch_parent_unblock_hold", {"owner": {"userId": "human"}},
         [SOURCE_TASK, PUBLISH_TASK], [], None),
        ("batch_parent_blocker_set_changed", {"owner": {"agentId": LEAD}},
         [SOURCE_TASK, PUBLISH_TASK, "a" * 8 + "-1111-4111-8111-111111111111"], [], None),
        ("batch_task_has_hold", {"owner": {"agentId": LEAD}},
         [SOURCE_TASK, PUBLISH_TASK], [], {"manualHold": True}),
        ("batch_task_has_hold", {"owner": {"agentId": LEAD}},
         [SOURCE_TASK, PUBLISH_TASK], [], {"recoveryStatus": "unknown_effect"}),
    ],
)
def test_batch_parent_closure_refuses_active_or_held_parent(
    monkeypatch, reason, descriptor, blockers, runs, hold
):
    document = acceptance_doc(json.dumps(acceptance(schema=delivery.BATCH_ACCEPTANCE_V2_SCHEMA,
                                                    close_parent=True)))
    document["createdByAgentId"] = LEAD
    document["updatedByAgentId"] = LEAD
    document["issueId"] = BATCH
    document["companyId"] = COMPANY
    fake_api = BatchApiFixture(document, PROCESS_RUN_1)
    fake_api.parent_status = "blocked"
    fake_api.parent_blockers = blockers
    fake_api.parent_descriptor = descriptor
    fake_api.parent_runs = runs
    fake_api.parent_hold = hold
    fake_api.receipt_document = {
        "companyId": COMPANY, "issueId": PUBLISH_TASK, "key": "publication-receipt",
        "body": "{}", "latestRevisionId": RECEIPT_REV,
    }
    monkeypatch.setenv("PAPERCLIP_API_KEY", "fixture-api-token")
    monkeypatch.setenv("PAPERCLIP_RUN_ID", PROCESS_RUN_1)
    monkeypatch.setenv("PAPERCLIP_AGENT_ID", PUBLISHER)
    monkeypatch.setenv("PAPERCLIP_COMPANY_ID", COMPANY)
    monkeypatch.setenv("PAPERCLIP_API_URL", "http://127.0.0.1:3310")

    def request(api, method, route, body=None, raw=False):
        return fake_api.request(api, method, route, body=body, raw=raw)

    monkeypatch.setattr(delivery.Paperclip, "request", request)
    api = delivery.Paperclip({"api_origin": "http://127.0.0.1:3310", "company_id": COMPANY})
    if reason == "batch_parent_lead_still_active":
        times = iter([0.0, 61.0])
        monkeypatch.setattr(delivery.time, "monotonic", lambda: next(times))
    with pytest.raises(delivery.DeliveryError, match=reason):
        delivery._close_batch_parent(
            api, {"company_id": COMPANY}, BATCH, PUBLISH_TASK, LEAD, [SOURCE_TASK],
            document["latestRevisionId"], sha(document["body"].encode()), "{}",
        )
    assert fake_api.parent_patches == 0


def test_batch_parent_closure_refuses_changed_acceptance(monkeypatch):
    document = acceptance_doc(json.dumps(acceptance(schema=delivery.BATCH_ACCEPTANCE_V2_SCHEMA,
                                                    close_parent=True)))
    document.update({"createdByAgentId": LEAD, "updatedByAgentId": LEAD,
                     "issueId": BATCH, "companyId": COMPANY})
    original_sha = sha(document["body"].encode())
    changed = dict(document, latestRevisionId="f" * 8 + "-1111-4111-8111-111111111111")
    fixture = BatchApiFixture(changed, PROCESS_RUN_1)
    fixture.parent_status = "blocked"
    fixture.parent_blockers = [SOURCE_TASK, PUBLISH_TASK]
    fixture.parent_descriptor = {"owner": {"agentId": LEAD}}
    monkeypatch.setenv("PAPERCLIP_API_KEY", "fixture-api-token")
    monkeypatch.setenv("PAPERCLIP_RUN_ID", PROCESS_RUN_1)
    monkeypatch.setenv("PAPERCLIP_AGENT_ID", PUBLISHER)
    monkeypatch.setenv("PAPERCLIP_COMPANY_ID", COMPANY)
    monkeypatch.setenv("PAPERCLIP_API_URL", "http://127.0.0.1:3310")
    monkeypatch.setattr(
        delivery.Paperclip, "request",
        lambda api, method, route, body=None, raw=False:
            fixture.request(api, method, route, body=body, raw=raw),
    )
    api = delivery.Paperclip({"api_origin": "http://127.0.0.1:3310", "company_id": COMPANY})
    with pytest.raises(delivery.DeliveryError, match="batch_acceptance_changed"):
        delivery._close_batch_parent(
            api, {"company_id": COMPANY}, BATCH, PUBLISH_TASK, LEAD, [SOURCE_TASK],
            document["latestRevisionId"], original_sha, "{}",
        )
    assert fixture.parent_patches == 0


def test_publish_batch_holds_recovery_action_and_remote_drift_before_ref_effects(
    tmp_path, monkeypatch
):
    canonical, source, base, branch, _, document = _make_batch_repositories(tmp_path)
    custody = tmp_path / "custody"
    custody.mkdir(mode=0o700)
    policy = {
        "api_origin": "http://127.0.0.1:3310",
        "company_id": COMPANY,
        "publisher_agent_id": PUBLISHER,
        "lead_agent_ids": [LEAD, *OTHER_LEADS],
        "source_seed_repo": str(source),
        "custody_dir": str(custody),
        "canonical": {
            "repo": str(canonical),
            "ref": branch,
            "remote_name": "origin",
            "remote_url": "https://github.com/avariceJS/memecoin-ai.git",
        },
    }
    fake_api = BatchApiFixture(document, PROCESS_RUN_1)
    remote = {branch: "e" * 40}
    pushes = []
    monkeypatch.setattr(delivery, "remote_refs", lambda _m: dict(remote))
    monkeypatch.setattr(delivery, "push_ref", lambda *args: pushes.append(args))
    fake_api.recovery_action = {"kind": "recovery"}
    with pytest.raises(delivery.DeliveryError, match="batch_task_has_hold"):
        _run_batch(monkeypatch, fake_api, policy, "f" * 64, PROCESS_RUN_1)
    fake_api.recovery_action = None
    with pytest.raises(delivery.DeliveryError, match="batch_remote_state_conflict"):
        _run_batch(monkeypatch, fake_api, policy, "f" * 64, PROCESS_RUN_1)
    assert git(canonical, "rev-parse", branch) == base
    assert pushes == []


def test_publish_batch_rejects_wrong_native_source_run_identity(tmp_path, monkeypatch):
    canonical, source, base, branch, _, document = _make_batch_repositories(tmp_path)
    custody = tmp_path / "custody"
    custody.mkdir(mode=0o700)
    policy = {
        "api_origin": "http://127.0.0.1:3310",
        "company_id": COMPANY,
        "publisher_agent_id": PUBLISHER,
        "lead_agent_ids": [LEAD, *OTHER_LEADS],
        "source_seed_repo": str(source),
        "custody_dir": str(custody),
        "canonical": {
            "repo": str(canonical),
            "ref": branch,
            "remote_name": "origin",
            "remote_url": "https://github.com/avariceJS/memecoin-ai.git",
        },
    }
    fake_api = BatchApiFixture(document, PROCESS_RUN_1)
    fake_api.source_context_id = BATCH
    monkeypatch.setattr(delivery, "remote_refs", lambda _m: {branch: base})
    with pytest.raises(delivery.DeliveryError, match="latest_task_run_not_successful"):
        _run_batch(monkeypatch, fake_api, policy, "f" * 64, PROCESS_RUN_1)
    assert git(canonical, "rev-parse", branch) == base
