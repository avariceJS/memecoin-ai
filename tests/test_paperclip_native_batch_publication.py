"""Focused checks for the Luna-led batch acceptance and immutable Git input."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from engineering import paperclip_native_delivery as delivery


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
    result, revision, body_hash = delivery._batch_acceptance(FakeApi(acceptance_doc(body)),
                                                              {"id": acceptance_doc(body)["issueId"]},
                                                              acceptance_doc(body)["createdByAgentId"])
    assert result["decision"] == "accept"
    assert revision == acceptance_doc(body)["latestRevisionId"]
    assert body_hash == sha(body.encode())


def test_acceptance_rejects_wrong_document_owner():
    body = json.dumps(acceptance())
    doc = acceptance_doc(body)
    doc["updatedByAgentId"] = "0" * 8 + "-1111-4111-8111-111111111111"
    with pytest.raises(delivery.DeliveryError, match="batch_acceptance_document_invalid"):
        delivery._batch_acceptance(FakeApi(doc), {"id": doc["issueId"]}, doc["createdByAgentId"])


@pytest.mark.parametrize("body", [
    '{"schema":"x","schema":"y"}',
    '{"schema":"x","n":NaN}',
])
def test_strict_json_rejects_duplicate_keys_and_nonfinite_values(body):
    with pytest.raises(delivery.DeliveryError):
        delivery._strict_json_object(body, "bad")


@pytest.mark.parametrize("file_map", [
    {"src/../escape.py": "0" * 64, "tests/t.py": "0" * 64},
    {"src\\escape.py": "0" * 64, "tests/t.py": "0" * 64},
    {"src/a.py": "0" * 63, "tests/t.py": "0" * 64},
    {"src/a.py": "0" * 64},
])
def test_acceptance_rejects_unsafe_file_maps(file_map):
    body = json.dumps(acceptance(files=file_map))
    doc = acceptance_doc(body)
    with pytest.raises(delivery.DeliveryError):
        delivery._batch_acceptance(FakeApi(doc), {"id": doc["issueId"]}, doc["createdByAgentId"])


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["/usr/bin/git", "-C", str(repo), *args], text=True).strip()


def commit(repo: Path, message: str) -> str:
    subprocess.run(["/usr/bin/git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["/usr/bin/git", "-C", str(repo), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", message], check=True)
    return git(repo, "rev-parse", "HEAD")


def test_source_extracts_only_exact_regular_utf8_blobs_and_old_projection(tmp_path):
    source = tmp_path / "source"
    canonical = tmp_path / "canonical"
    for repo in (source, canonical):
        repo.mkdir()
        subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True)
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
    accept = acceptance(source_base_sha=source_base, assembled_tip=assembled,
                        canonical_expected_tip=canonical_base,
                        files={"src/mod.py": sha(b"new\n"), "tests/test_mod.py": sha(b"test\n")})
    old, new, files = delivery._batch_source(accept, source, canonical, canonical_base)
    assert set(files) == set(accept["files"])
    assert files["src/mod.py"] == b"new\n"
    assert old["src/mod.py"] is not None
    assert new["tests/test_mod.py"] != old["tests/test_mod.py"]
    canonical_ref = git(canonical, "symbolic-ref", "HEAD")
    prepared = delivery.publisher.prepare(canonical, canonical_ref, canonical_base, old, new, files,
                                         allowed_paths=list(accept["files"]))
    inspected = delivery.publisher.inspect(canonical, canonical_ref, prepared,
                                           allowed_paths=list(accept["files"]))
    assert inspected["ref_state"] == "prepared"
    done = delivery.publisher.reconcile(canonical, canonical_ref, prepared, prepared["candidate"],
                                        prepared["receipt_digest"], allowed_paths=list(accept["files"]))
    assert done["status"] == "reconciled"
    assert git(canonical, "rev-parse", canonical_ref) == prepared["candidate"]
    assert (canonical / "README.md").read_bytes() == b"preserve me\n"
    assert (canonical / "src/mod.py").read_bytes() == b"new\n"


def test_source_rejects_extra_changes_and_hash_mismatch(tmp_path):
    source = tmp_path / "source"
    canonical = tmp_path / "canonical"
    for repo in (source, canonical):
        repo.mkdir()
        subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True)
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
    accepted = acceptance(source_base_sha=base, assembled_tip=tip, canonical_expected_tip=canonical_base)
    with pytest.raises(delivery.DeliveryError, match="batch_source_diff_mismatch"):
        delivery._batch_source(accepted, source, canonical, canonical_base)
    accepted["files"]["src/extra.py"] = sha(b"wrong\n")
    with pytest.raises(delivery.DeliveryError, match="batch_source_file_hash_invalid"):
        delivery._batch_source(accepted, source, canonical, canonical_base)


@pytest.mark.parametrize("action", ["stage", "publish"])
def test_existing_cli_actions_keep_manifest_dispatch(monkeypatch, capsys, action):
    calls = []
    monkeypatch.setattr(delivery, "load_manifest", lambda _: ({"old": True}, "hash"))
    monkeypatch.setattr(delivery, "stage", lambda m, h: calls.append(("stage", m, h)) or {"route": "stage"})
    monkeypatch.setattr(delivery, "publish", lambda m, h: calls.append(("publish", m, h)) or {"route": "publish"})
    assert delivery.main([action, "--manifest", "old.json"]) == 0
    assert calls[0][0] == action
    assert json.loads(capsys.readouterr().out)["route"] == action
