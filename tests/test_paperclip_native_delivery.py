from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from engineering import paperclip_native_delivery as delivery


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _seed_envelope(repo: Path) -> dict[str, object]:
    return {"source": {"seed_repo": str(repo), "base_sha": _git(repo, "rev-parse", "HEAD")}}


def test_preparation_seed_allows_untracked_worker_worktrees_but_keeps_other_checks(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "seed"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    tracked = repo / ".paperclip" / "worktrees" / "tracked.txt"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("frozen\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "frozen seed")
    envelope = _seed_envelope(repo)

    worker_artifact = repo / ".paperclip" / "worktrees" / "MEM-221" / "notes.txt"
    worker_artifact.parent.mkdir()
    worker_artifact.write_text("worker state\n")
    delivery._validate_preparation_seed(envelope)

    unrelated = repo / "unrelated.txt"
    unrelated.write_text("must be rejected\n")
    with pytest.raises(delivery.DeliveryError, match="preparation_seed_not_clean_frozen_base"):
        delivery._validate_preparation_seed(envelope)
    unrelated.unlink()

    tracked.write_text("modified\n")
    with pytest.raises(delivery.DeliveryError, match="preparation_seed_not_clean_frozen_base"):
        delivery._validate_preparation_seed(envelope)


def test_qa_artifact_reads_exact_run_authored_document_revision() -> None:
    api = object.__new__(delivery.Paperclip)
    task = {"task_id": "task-id", "agent_id": "qa-agent"}
    run = {"runId": "qa-run"}
    body = b'{"schema":"brain-native-qa-result-v1","verdict":"reject"}'
    document_id = "cbdb206c-545b-4709-b393-58076fb969f2"
    revision_id = "d87a206c-545b-4709-b393-58076fb969f2"
    requests: list[str] = []

    api.attachments = lambda _task_id: [
        {
            "id": "5af56c43-8724-43cb-b208-b46a347e3a90",
            "originalFilename": "QA-RESULT.json",
            "originatingRunId": "manager-run",
            "createdByAgentId": "manager-agent",
            "byteSize": 12,
        }
    ]

    def request(method: str, route: str) -> list[dict[str, object]]:
        assert method == "GET"
        requests.append(route)
        if route.endswith("documents?includeSystem=true"):
            return [
                {
                    "id": document_id,
                    "companyId": "company-id",
                    "issueId": task["task_id"],
                    "key": "independent-review",
                    "title": "Independent review",
                }
            ]
        return [
            {
                "id": "bad-revision-id",
                "documentId": document_id,
                "issueId": task["task_id"],
                "createdByAgentId": "manager-agent",
                "createdByRunId": "manager-run",
                "revisionNumber": 3,
                "body": "manager transport copy",
            },
            {
                "id": revision_id,
                "companyId": "company-id",
                "documentId": document_id,
                "issueId": task["task_id"],
                "createdByAgentId": task["agent_id"],
                "createdByRunId": run["runId"],
                "revisionNumber": 2,
                "body": body.decode("utf-8"),
            },
            {
                "id": "other-run-revision",
                "documentId": document_id,
                "issueId": task["task_id"],
                "createdByAgentId": task["agent_id"],
                "createdByRunId": "qa-run-retry",
                "revisionNumber": 4,
                "body": "not this QA run",
            },
        ]

    api.request = request
    api.m = {"company_id": "company-id"}

    meta, content = api.qa_artifact(task, run, "QA-RESULT.json", 256 * 1024)

    assert content == body
    assert meta == {
        "id": document_id,
        "revisionId": revision_id,
        "revisionNumber": 2,
        "createdByAgentId": task["agent_id"],
        "originatingRunId": run["runId"],
        "byteSize": len(body),
        "sha256": delivery.digest(body),
        "artifactType": "document_revision",
    }
    assert requests == [
        f"/issues/{task['task_id']}/documents?includeSystem=true",
        f"/issues/{task['task_id']}/documents/independent-review/revisions",
    ]


def test_qa_document_requires_revision_from_pinned_qa_run() -> None:
    api = object.__new__(delivery.Paperclip)
    task = {"task_id": "task-id", "agent_id": "qa-agent"}
    run = {"runId": "qa-run"}

    api.attachments = lambda _task_id: [
        {
            "id": "5af56c43-8724-43cb-b208-b46a347e3a90",
            "originalFilename": "QA-RESULT.json",
            "originatingRunId": "manager-run",
            "createdByAgentId": "manager-agent",
            "byteSize": 12,
        }
    ]

    def request(_method: str, route: str) -> list[dict[str, object]]:
        if route.endswith("documents?includeSystem=true"):
            return [
                {
                    "id": "cbdb206c-545b-4709-b393-58076fb969f2",
                    "companyId": "company-id",
                    "issueId": task["task_id"],
                    "key": "qa-result",
                }
            ]
        return [
            {
                "id": "d87a206c-545b-4709-b393-58076fb969f2",
                "companyId": "company-id",
                "documentId": "cbdb206c-545b-4709-b393-58076fb969f2",
                "issueId": task["task_id"],
                "createdByAgentId": "manager-agent",
                "createdByRunId": "manager-run",
                "revisionNumber": 1,
                "body": "manager copy of QA result",
            }
        ]

    api.request = request
    api.m = {"company_id": "company-id"}
    with pytest.raises(delivery.DeliveryError, match="QA-RESULT.json_missing"):
        api.qa_artifact(task, run, "QA-RESULT.json", 256 * 1024)


def test_rejected_qa_document_is_preserved_with_candidate_and_run_binding(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "qa"
    handoff = workspace / ".native-delivery"
    handoff.mkdir(parents=True)
    relative = "tests/test_candidate.py"
    candidate = b"candidate source\n"
    patch = b"candidate patch\n"
    demonstration = b'{"module":"example","input":{},"output":{}}\n'
    (workspace / relative).parent.mkdir(parents=True)
    (workspace / relative).write_bytes(candidate)
    (handoff / "candidate.patch").write_bytes(patch)
    (handoff / "demonstration.json").write_bytes(demonstration)
    prior = {
        "schema": delivery.STAGE_SCHEMA,
        "developer_task_id": "developer-task",
        "developer_run_id": "developer-run",
        "revision": 1,
        "paths": [relative],
        "patch": {"sha256": delivery.digest(patch)},
        "demonstration": {"sha256": delivery.digest(demonstration)},
        "files": {relative: {"sha256": delivery.digest(candidate)}},
    }
    qa_result = delivery.json_bytes(
        {
            "schema": delivery.QA_SCHEMA,
            "verdict": "reject",
            "source_task_id": prior["developer_task_id"],
            "source_patch_sha256": prior["patch"]["sha256"],
            "files": {relative: prior["files"][relative]["sha256"]},
            "checks": [{"name": "x", "result": "failed"}],
            "findings": [{"check": "x", "evidence": "reproduced"}],
        }
    )
    assert (
        delivery.validate_qa(
            qa_result,
            prior["developer_task_id"],
            prior["patch"]["sha256"],
            prior["files"],
            prior["paths"],
            allow_reject=True,
        )
        == "reject"
    )
    (handoff / "native-delivery.json").write_bytes(delivery.json_bytes(prior))
    qa_artifact = {
        "id": "cbdb206c-545b-4709-b393-58076fb969f2",
        "revisionId": "d87a206c-545b-4709-b393-58076fb969f2",
        "artifactType": "document_revision",
    }

    delivery.preserve_qa_predecessor(
        handoff, prior, "qa-run", qa_artifact, qa_result
    )

    snapshot = handoff / "history" / "revision-1"
    saved_index = json.loads((snapshot / "history.json").read_bytes())
    assert (snapshot / "QA-RESULT.json").read_bytes() == qa_result
    assert saved_index["schema"] == "brain-native-qa-predecessor-history-v2"
    assert saved_index["developer_run_id"] == "developer-run"
    assert saved_index["candidate_patch_sha256"] == delivery.digest(patch)
    assert saved_index["qa_run_id"] == "qa-run"
    assert saved_index["qa_artifact"] == {
        "type": "document_revision",
        "id": qa_artifact["id"],
        "revision_id": qa_artifact["revisionId"],
        "sha256": delivery.digest(qa_result),
        "verdict": "reject",
    }
    assert saved_index["files"]["QA-RESULT.json"] == delivery.digest(qa_result)
