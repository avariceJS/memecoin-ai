"""Focused checks for the bounded native delivery adapter."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from engineering import paperclip_native_delivery as delivery


def write_v1_policy_v2_renewal(root: Path, company_id: str) -> tuple[dict[str, str], Path]:
    resource_root = root / "resource-policy"
    resource_root.mkdir(mode=0o700)
    now = datetime.now(UTC)
    original = {
        "schema": delivery.resource_guard.POLICY_SCHEMA,
        "company_id": company_id,
        "connection_id": "00000000-0000-4000-8000-000000009001",
        "issued_at_utc": (now - timedelta(hours=12)).isoformat().replace("+00:00", "Z"),
        "deadline_utc": (now - timedelta(hours=4)).isoformat().replace("+00:00", "Z"),
        "max_model_runs": 80,
        "min_remaining_percent": 15,
        "task_scope_root_ids": ["00000000-0000-4000-8000-000000009002"],
    }
    new_policy = {
        **original,
        "schema": delivery.resource_guard.POLICY_SCHEMA_V2,
        "issued_at_utc": original["deadline_utc"],
        "deadline_utc": (now + timedelta(hours=8)).isoformat().replace("+00:00", "Z"),
        "max_model_runs": None,
        "min_remaining_percent": 8,
    }
    policy_path = resource_root / "original-v1.json"
    policy_raw = delivery.json_bytes(original)
    policy_path.write_bytes(policy_raw)
    policy_path.chmod(0o400)
    reference = {"path": str(policy_path), "sha256": delivery.digest(policy_raw)}
    renewal = {
        "schema": delivery.resource_guard.RENEWAL_SCHEMA_V2,
        "original_policy_sha256": reference["sha256"],
        "new_policy": new_policy,
    }
    renewal_path = resource_root / "renewal-v2.json"
    renewal_path.write_bytes(delivery.json_bytes(renewal))
    renewal_path.chmod(0o400)
    return reference, renewal_path


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), *args], input=data, capture_output=True, check=False
    )
    if result.returncode:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return result.stdout


def init_repo(path: Path) -> str:
    path.mkdir(parents=True)
    subprocess.run(["/usr/bin/git", "init", "--quiet", str(path)], check=True)
    git(
        path,
        "-c",
        "user.name=fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "--quiet",
        "--allow-empty",
        "--no-gpg-sign",
        "-m",
        "seed",
    )
    return git(path, "rev-parse", "HEAD").decode().strip()


class NativeDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="native-delivery-")
        self.root = Path(self.temp.name)
        self.seed = self.root / "seed"
        self.base = init_repo(self.seed)
        self.paths = ["src/new_module.py", "tests/test_new_module.py"]
        self.work = self.root / "work"
        init_repo(self.work)
        for path, data in (
            (self.paths[0], b"VALUE = 1\n"),
            (self.paths[1], b"assert True\n"),
        ):
            target = self.work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        git(self.work, "add", "--", *self.paths)
        self.patch = git(self.work, "diff", "--cached", "--binary")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_normalizer_accepts_only_exact_new_regular_source_and_test_files(self) -> None:
        files = delivery.normalize_patch(self.patch, self.seed, self.base, self.paths)
        self.assertEqual(files, {self.paths[0]: b"VALUE = 1\n", self.paths[1]: b"assert True\n"})

        extra = self.work / "README.md"
        extra.write_text("unexpected\n")
        git(self.work, "add", "README.md")
        with self.assertRaisesRegex(delivery.DeliveryError, "candidate_path_set_invalid"):
            delivery.normalize_patch(
                git(self.work, "diff", "--cached", "--binary"), self.seed, self.base, self.paths
            )

    def test_normalizer_rejects_non_new_files_and_executable_modes(self) -> None:
        (self.seed / self.paths[0]).parent.mkdir(parents=True)
        (self.seed / self.paths[0]).write_bytes(b"occupied\n")
        git(self.seed, "add", self.paths[0])
        git(
            self.seed,
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "occupied",
        )
        occupied = git(self.seed, "rev-parse", "HEAD").decode().strip()
        with self.assertRaisesRegex(delivery.DeliveryError, "candidate_paths_not_new_in_seed"):
            delivery.normalize_patch(self.patch, self.seed, occupied, self.paths)

        git(self.work, "update-index", "--chmod=+x", self.paths[0])
        with self.assertRaisesRegex(delivery.DeliveryError, "candidate_file_mode_invalid"):
            delivery.normalize_patch(
                git(self.work, "diff", "--cached", "--binary"), self.seed, self.base, self.paths
            )

    def test_qa_result_binds_exact_source_and_file_hashes(self) -> None:
        files = delivery.normalize_patch(self.patch, self.seed, self.base, self.paths)
        hashes = {p: hashlib.sha256(files[p]).hexdigest() for p in self.paths}
        patch_hash = hashlib.sha256(self.patch).hexdigest()
        result = {
            "schema": delivery.QA_SCHEMA,
            "verdict": "accept",
            "source_task_id": "source-task",
            "source_patch_sha256": patch_hash,
            "files": hashes,
            "checks": [{"name": "focused checks", "result": "passed"}],
        }
        delivery.validate_qa(
            json.dumps(result).encode(),
            "source-task",
            patch_hash,
            {p: {"sha256": h} for p, h in hashes.items()},
            self.paths,
        )
        for altered in (
            {**result, "files": {**hashes, self.paths[0]: "0" * 64}},
            {**result, "files": {self.paths[0]: hashes[self.paths[0]]}},
            {**result, "source_patch_sha256": "0" * 64},
            {**result, "verdict": "reject"},
        ):
            with self.subTest(altered=altered):
                with self.assertRaises(delivery.DeliveryError):
                    delivery.validate_qa(
                        json.dumps(altered).encode(),
                        "source-task",
                        patch_hash,
                        {p: {"sha256": h} for p, h in hashes.items()},
                        self.paths,
                    )

    def test_qa_rejection_binds_exact_candidate_and_requires_findings(self) -> None:
        files = delivery.normalize_patch(self.patch, self.seed, self.base, self.paths)
        hashes = {p: hashlib.sha256(files[p]).hexdigest() for p in self.paths}
        patch_hash = hashlib.sha256(self.patch).hexdigest()
        rejected = {
            "schema": delivery.QA_SCHEMA,
            "verdict": "reject",
            "source_task_id": "source-task",
            "source_patch_sha256": patch_hash,
            "files": hashes,
            "checks": [{"name": "focused checks", "result": "failed"}],
            "findings": [{"check": "focused checks", "evidence": "assertion failed at line 1"}],
        }
        bound = {p: {"sha256": h} for p, h in hashes.items()}
        self.assertEqual(
            delivery.validate_qa(
                json.dumps(rejected).encode(),
                "source-task",
                patch_hash,
                bound,
                self.paths,
                allow_reject=True,
            ),
            "reject",
        )
        for altered in (
            {**rejected, "source_patch_sha256": "0" * 64},
            {**rejected, "files": {**hashes, self.paths[0]: "0" * 64}},
            {**rejected, "findings": []},
            {**rejected, "findings": [{"check": "other", "evidence": "not bound"}]},
        ):
            with self.subTest(altered=altered):
                with self.assertRaises(delivery.DeliveryError):
                    delivery.validate_qa(
                        json.dumps(altered).encode(),
                        "source-task",
                        patch_hash,
                        bound,
                        self.paths,
                        allow_reject=True,
                    )

    def test_single_correction_replaces_only_unchanged_staged_bytes(self) -> None:
        target = self.root / "qa-file.py"
        target.write_bytes(b"first candidate\n")
        old_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        delivery.replace_known(target, b"corrected candidate\n", old_hash)
        self.assertEqual(target.read_bytes(), b"corrected candidate\n")
        target.chmod(0o644)
        target.write_bytes(b"owner edit\n")
        with self.assertRaisesRegex(delivery.DeliveryError, "correction_target_changed"):
            delivery.replace_known(target, b"another candidate\n", old_hash)
        self.assertEqual(target.read_bytes(), b"owner edit\n")

    def test_rejected_predecessor_is_preserved_immutably_in_qa_workspace(self) -> None:
        qa_root = self.root / "qa"
        handoff = qa_root / ".native-delivery"
        handoff.mkdir(parents=True)
        file_data = {self.paths[0]: b"VALUE = 1\n", self.paths[1]: b"assert True\n"}
        for relative, data in file_data.items():
            path = qa_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        patch_bytes = b"exact prior patch\n"
        demo_bytes = b'{"module":"m","input":1,"output":1}\n'
        (handoff / "candidate.patch").write_bytes(patch_bytes)
        (handoff / "demonstration.json").write_bytes(demo_bytes)
        prior = {
            "schema": delivery.STAGE_SCHEMA,
            "revision": 1,
            "developer_run_id": "developer-run-1",
            "paths": self.paths,
            "patch": {"sha256": delivery.digest(patch_bytes)},
            "demonstration": {"sha256": delivery.digest(demo_bytes)},
            "files": {p: {"sha256": delivery.digest(data)} for p, data in file_data.items()},
        }
        record_bytes = delivery.json_bytes(prior)
        (handoff / "native-delivery.json").write_bytes(record_bytes)
        rejection = b'{"verdict":"reject","findings":[{"check":"test","evidence":"failed"}]}\n'

        delivery.preserve_qa_predecessor(handoff, prior, "qa-run-1", "qa-attachment-1", rejection)

        snapshot = handoff / "history" / "revision-1"
        expected = {
            "candidate.patch": patch_bytes,
            "demonstration.json": demo_bytes,
            "native-delivery.json": record_bytes,
            "QA-RESULT.json": rejection,
            **{f"files/{p}": data for p, data in file_data.items()},
        }
        for relative, data in expected.items():
            path = snapshot / relative
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o400)
        index = json.loads((snapshot / "history.json").read_bytes())
        self.assertEqual(index["qa_run_id"], "qa-run-1")
        self.assertEqual(index["qa_attachment_id"], "qa-attachment-1")
        self.assertEqual(index["files"], {p: delivery.digest(data) for p, data in expected.items()})
        self.assertEqual(
            (snapshot / "history.json").read_bytes(),
            delivery.json_bytes(
                {
                    "schema": "brain-native-qa-predecessor-history-v1",
                    "revision": 1,
                    "developer_run_id": "developer-run-1",
                    "qa_run_id": "qa-run-1",
                    "qa_attachment_id": "qa-attachment-1",
                    "files": {p: delivery.digest(data) for p, data in sorted(expected.items())},
                }
            ),
        )

        (handoff / "candidate.patch").write_bytes(b"corrected patch\n")
        (qa_root / self.paths[0]).write_bytes(b"corrected source\n")
        self.assertEqual((snapshot / "candidate.patch").read_bytes(), patch_bytes)
        self.assertEqual(
            (snapshot / "files" / self.paths[0]).read_bytes(), file_data[self.paths[0]]
        )
        (snapshot / "QA-RESULT.json").chmod(0o600)
        (snapshot / "QA-RESULT.json").write_bytes(b"tampered\n")
        with self.assertRaisesRegex(delivery.DeliveryError, "target_collision"):
            delivery.write_once(snapshot / "QA-RESULT.json", rejection, 0o400)

    def test_rejected_predecessor_snapshot_rejects_symlinked_candidate(self) -> None:
        qa_root = self.root / "qa-symlink"
        handoff = qa_root / ".native-delivery"
        handoff.mkdir(parents=True)
        outside = self.root / "outside.py"
        outside.write_bytes(b"VALUE = 1\n")
        linked = qa_root / self.paths[0]
        linked.parent.mkdir(parents=True)
        linked.symlink_to(outside)
        prior = {
            "revision": 1,
            "developer_run_id": "developer-run-1",
            "paths": self.paths,
            "patch": {"sha256": delivery.digest(b"patch")},
            "demonstration": {"sha256": delivery.digest(b"demo")},
            "files": {
                p: {
                    "sha256": delivery.digest(
                        b"VALUE = 1\n" if p == self.paths[0] else b"assert True\n"
                    )
                }
                for p in self.paths
            },
        }
        (handoff / "candidate.patch").write_bytes(b"patch")
        (handoff / "demonstration.json").write_bytes(b"demo")
        (handoff / "native-delivery.json").write_bytes(delivery.json_bytes(prior))
        with self.assertRaisesRegex(delivery.DeliveryError, "target_not_regular"):
            delivery.preserve_qa_predecessor(
                handoff, prior, "qa-run-1", "qa-attachment-1", b"reject"
            )

    def test_candidate_attempts_respect_configured_correction_limit(self) -> None:
        self.assertEqual(delivery.next_stage_revision(None), 1)
        self.assertEqual(delivery.next_stage_revision({"revision": 1}), 2)
        with self.assertRaisesRegex(delivery.DeliveryError, "correction_limit_reached"):
            delivery.next_stage_revision({"revision": 2})
        self.assertEqual(delivery.next_stage_revision({"revision": 3}, 3), 4)
        with self.assertRaisesRegex(delivery.DeliveryError, "correction_limit_reached"):
            delivery.next_stage_revision({"revision": 4}, 3)
        for invalid in (True, 0, 4):
            with self.subTest(max_corrections=invalid):
                with self.assertRaisesRegex(delivery.DeliveryError, "max_corrections_invalid"):
                    delivery.next_stage_revision(None, invalid)

    def _exercise_stage_corrections(self, max_corrections: int | None, attempt_count: int) -> None:
        resolved_root = self.root.resolve()
        qa_root = resolved_root / f"qa-chain-{max_corrections or 'legacy'}"
        custody = resolved_root / f"custody-chain-{max_corrections or 'legacy'}"
        qa_root.mkdir()
        custody.mkdir(mode=0o700)
        for relative in self.paths:
            (qa_root / relative).parent.mkdir(parents=True, exist_ok=True)
        developer = {"task_id": "source-task", "agent_id": "developer-agent"}
        qa = {"task_id": "qa-task", "agent_id": "qa-agent"}
        manifest: dict[str, Any] = {
            "api_origin": "http://127.0.0.1:3310",
            "company_id": "company-id",
            "manager_task_id": "00000000-0000-4000-8000-000000000051",
            "developer": developer,
            "qa": qa,
            "stage": {"task_id": "stage-task", "agent_id": "stage-agent"},
            "publish": {"task_id": "publish-task", "agent_id": "publish-agent"},
            "source": {"seed_repo": str(self.seed), "base_sha": self.base, "paths": self.paths},
            "canonical": {"expected_tip": "a" * 40},
            "qa_workspace": str(qa_root),
            "custody_dir": str(custody),
        }
        if max_corrections is not None:
            manifest["max_corrections"] = max_corrections
        manifest_hash = delivery.digest(delivery.json_bytes(manifest))
        patch_by_run: dict[str, bytes] = {}
        demo_by_run: dict[str, bytes] = {}
        qa_result = b""
        qa_run = {"runId": "qa-run-0"}
        source_run = {"runId": "developer-run-1"}

        class FakeApi:
            def current(self, _role):
                return "stage-task"

            def issue(self, task_id):
                agent = developer["agent_id"] if task_id == developer["task_id"] else qa["agent_id"]
                return {"status": "done", "assigneeAgentId": agent}

            def no_pending(self, _task_id):
                return None

            def latest_run(self, task):
                return source_run if task == developer else qa_run

            def artifact(self, task, run, name, _limit):
                if task == developer:
                    data = (
                        patch_by_run[run["runId"]]
                        if name == "candidate.patch"
                        else demo_by_run[run["runId"]]
                    )
                    return {"id": f"{run['runId']}-{name}"}, data
                return {
                    "id": "qa-rejection",
                    "originatingRunId": run["runId"],
                    "createdByAgentId": qa["agent_id"],
                }, qa_result

            def qa_artifact(self, task, run, name, limit):
                meta, data = self.artifact(task, run, name, limit)
                if (
                    task != qa
                    or meta.get("originatingRunId") != run["runId"]
                    or meta.get("createdByAgentId") != qa["agent_id"]
                ):
                    raise delivery.DeliveryError("qa_artifact_binding_invalid")
                return {**meta, "artifactType": "attachment"}, data

            def complete(self, _task_id):
                return None

        api = FakeApi()
        records: list[dict[str, Any]] = []
        patches: list[bytes] = []
        results: list[bytes] = []
        notifications: list[tuple[Any, ...]] = []
        for revision in range(1, attempt_count + 1):
            git(self.work, "reset", "--hard")
            git(self.work, "clean", "-fd")
            values = {
                self.paths[0]: f"VALUE = {revision}\n".encode(),
                self.paths[1]: f"assert VALUE == {revision}\n".encode(),
            }
            for relative, data in values.items():
                path = self.work / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            git(self.work, "add", "--", *self.paths)
            patch_bytes = git(self.work, "diff", "--cached", "--binary")
            run_id = f"developer-run-{revision}"
            source_run = {"runId": run_id}
            patch_by_run[run_id] = patch_bytes
            demo_by_run[run_id] = json.dumps(
                {"module": "fixture", "input": revision, "output": revision}
            ).encode()
            if revision > 1:
                previous = records[-1]
                qa_run = {"runId": f"qa-run-{revision - 1}"}
                qa_result = delivery.json_bytes(
                    {
                        "schema": delivery.QA_SCHEMA,
                        "verdict": "reject",
                        "source_task_id": developer["task_id"],
                        "source_patch_sha256": previous["patch"]["sha256"],
                        "files": {p: previous["files"][p]["sha256"] for p in self.paths},
                        "checks": [{"name": "focused check", "result": "failed"}],
                        "findings": [
                            {
                                "check": "focused check",
                                "evidence": f"revision {revision - 1} failed",
                            }
                        ],
                    }
                )
            with (
                patch.object(delivery, "Paperclip", return_value=api),
                patch.object(
                    delivery,
                    "notify_manager",
                    side_effect=lambda *args: notifications.append(args),
                ),
            ):
                delivery.stage(manifest, manifest_hash)
            record_path = delivery.stage_path(custody, developer["task_id"], run_id)
            record = json.loads(record_path.read_bytes())
            records.append(record)
            patches.append(patch_bytes)
            results.append(qa_result)

        self.assertEqual([row["revision"] for row in records], list(range(1, attempt_count + 1)))
        self.assertEqual(len(notifications), attempt_count - 1)
        for revision in range(1, attempt_count):
            snapshot = qa_root / ".native-delivery" / "history" / f"revision-{revision}"
            previous = records[revision - 1]
            self.assertEqual((snapshot / "candidate.patch").read_bytes(), patches[revision - 1])
            self.assertEqual(
                (snapshot / "demonstration.json").read_bytes(),
                demo_by_run[previous["developer_run_id"]],
            )
            self.assertEqual(
                (snapshot / "native-delivery.json").read_bytes(), delivery.json_bytes(previous)
            )
            self.assertEqual((snapshot / "QA-RESULT.json").read_bytes(), results[revision])
            index = json.loads((snapshot / "history.json").read_bytes())
            self.assertEqual(index["revision"], revision)
            self.assertEqual(index["qa_run_id"], f"qa-run-{revision}")
            self.assertEqual(index["qa_attachment_id"], "qa-rejection")
            for relative in self.paths:
                self.assertEqual(
                    (snapshot / "files" / relative).read_bytes(),
                    f"VALUE = {revision}\n".encode()
                    if relative == self.paths[0]
                    else f"assert VALUE == {revision}\n".encode(),
                )
        if max_corrections is not None:
            next_revision = attempt_count + 1
            git(self.work, "reset", "--hard")
            git(self.work, "clean", "-fd")
            for relative, data in {
                self.paths[0]: f"VALUE = {next_revision}\n".encode(),
                self.paths[1]: f"assert VALUE == {next_revision}\n".encode(),
            }.items():
                path = self.work / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            git(self.work, "add", "--", *self.paths)
            run_id = f"developer-run-{next_revision}"
            source_run = {"runId": run_id}
            patch_by_run[run_id] = git(self.work, "diff", "--cached", "--binary")
            demo_by_run[run_id] = json.dumps(
                {"module": "fixture", "input": next_revision, "output": next_revision}
            ).encode()
            previous = records[-1]
            qa_run = {"runId": f"qa-run-{attempt_count}"}
            qa_result = results[-1]
            before = (qa_root / ".native-delivery" / "candidate.patch").read_bytes()
            with (
                patch.object(delivery, "Paperclip", return_value=api),
                patch.object(delivery, "notify_manager"),
                self.assertRaisesRegex(delivery.DeliveryError, "correction_limit_reached"),
            ):
                delivery.stage(manifest, manifest_hash)
            self.assertEqual(
                (qa_root / ".native-delivery" / "candidate.patch").read_bytes(), before
            )

    def test_stage_allows_three_bound_corrections_and_archives_each_rejection(self) -> None:
        self._exercise_stage_corrections(max_corrections=3, attempt_count=4)

    def test_stage_legacy_default_stops_after_one_correction(self) -> None:
        self._exercise_stage_corrections(max_corrections=None, attempt_count=2)

    def test_restage_replay_uses_predecessor_cas_and_rejects_unknown_bytes(self) -> None:
        target = self.root / "qa-source.py"
        initial_bytes, corrected_bytes = b"initial\n", b"corrected\n"
        delivery.replace_known(target, initial_bytes, None)
        missing_initial = self.root / "partial-initial.py"
        delivery.replace_known(missing_initial, initial_bytes, None)
        self.assertEqual(missing_initial.read_bytes(), initial_bytes)
        initial_path = self.root / "stage-initial.json"
        corrected_path = self.root / "stage-corrected.json"
        initial = {"developer_run_id": "run-initial", "revision": 1}
        corrected = {"developer_run_id": "run-corrected", "revision": 2}
        initial_path.write_bytes(delivery.json_bytes(initial))
        corrected_path.write_bytes(delivery.json_bytes(corrected))

        current, predecessor = delivery.stage_replay_state(
            [(initial_path, initial), (corrected_path, corrected)], "run-corrected"
        )
        self.assertEqual(current, (corrected_path, corrected))
        self.assertEqual(predecessor, (initial_path, initial))
        old_hash = delivery.digest(initial_bytes)
        delivery.replace_known(target, corrected_bytes, old_hash)
        delivery.replace_known(target, corrected_bytes, old_hash)  # interrupted-write replay
        self.assertEqual(target.read_bytes(), corrected_bytes)

        target.chmod(0o644)
        target.write_bytes(b"unknown edit\n")
        with self.assertRaisesRegex(delivery.DeliveryError, "correction_target_changed"):
            delivery.replace_known(target, corrected_bytes, old_hash)

    def test_notification_is_read_back_and_never_blindly_reposted(self) -> None:
        class FakeApi:
            agent_id = "00000000-0000-4000-8000-000000000051"

            def __init__(
                self,
                *,
                lose_post: bool = False,
                post_then_error: bool = False,
                fail_first_get: bool = False,
                dependency_blocked: bool = False,
            ) -> None:
                self.rows = []
                self.posts = 0
                self.payloads = []
                self.lose_post = lose_post
                self.post_then_error = post_then_error
                self.fail_first_get = fail_first_get
                self.dependency_blocked = dependency_blocked
                self.gets = 0

            def request(self, method, route, body=None):
                if method == "GET":
                    self.gets += 1
                    if self.fail_first_get and self.gets == 1:
                        raise delivery.DeliveryError("paperclip_request_failed")
                    return list(self.rows)
                if method == "POST":
                    self.posts += 1
                    self.payloads.append(body)
                    if self.dependency_blocked and "resume" in body:
                        raise delivery.DeliveryError("blocked_manager_resume_rejected")
                    self.rows.append(
                        {"id": "comment-1", "authorAgentId": self.agent_id, "body": body["body"]}
                    )
                    if self.lose_post and not self.post_then_error:
                        self.rows.clear()
                    if self.lose_post:
                        raise delivery.DeliveryError("paperclip_request_failed")
                    return {}
                raise AssertionError((method, route, body))

        custody = self.root / "notify-custody"
        custody.mkdir(mode=0o700)
        manifest = {
            "custody_dir": str(custody.resolve()),
            "manager_task_id": "00000000-0000-4000-8000-000000000052",
        }
        kwargs = (manifest, "publish", "task-1", "marker-1", "published", "a" * 64)
        api = FakeApi()
        delivery.notify_manager(api, *kwargs)
        delivery.notify_manager(api, *kwargs)
        self.assertEqual(api.posts, 1)

        blocked = FakeApi(dependency_blocked=True)
        blocked_kwargs = (
            manifest,
            "correction",
            "task-blocked",
            "marker-blocked",
            "corrected candidate staged",
            "a" * 64,
        )
        delivery.notify_manager(blocked, *blocked_kwargs)
        self.assertEqual(blocked.posts, 1)
        self.assertEqual(blocked.payloads, [{"body": blocked_kwargs[4]}])
        self.assertEqual(blocked.rows[0]["body"], blocked_kwargs[4])

        committed_but_uncertain = FakeApi(lose_post=True, post_then_error=True)
        committed_kwargs = (
            manifest,
            "publish",
            "task-3",
            "marker-3",
            "published after response loss",
            "a" * 64,
        )
        delivery.notify_manager(committed_but_uncertain, *committed_kwargs)
        self.assertEqual(committed_but_uncertain.posts, 1)

        get_retry = FakeApi(fail_first_get=True)
        get_retry_kwargs = (
            manifest,
            "correction",
            "task-4",
            "marker-4",
            "corrected candidate",
            "a" * 64,
        )
        with self.assertRaisesRegex(delivery.DeliveryError, "paperclip_request_failed"):
            delivery.notify_manager(get_retry, *get_retry_kwargs)
        delivery.notify_manager(get_retry, *get_retry_kwargs)
        self.assertEqual(get_retry.posts, 1)

        uncertain = FakeApi(lose_post=True)
        uncertain_kwargs = (
            manifest,
            "correction",
            "task-2",
            "marker-2",
            "correction staged",
            "a" * 64,
        )
        with self.assertRaisesRegex(delivery.DeliveryError, "manager_notification_outcome_unknown"):
            delivery.notify_manager(uncertain, *uncertain_kwargs)
        with self.assertRaisesRegex(delivery.DeliveryError, "manager_notification_outcome_unknown"):
            delivery.notify_manager(uncertain, *uncertain_kwargs)
        self.assertEqual(uncertain.posts, 1)

    def test_publication_replay_accepts_only_expected_or_receipted_candidate_head(self) -> None:
        repo = Path("/tmp/canonical")
        receipt = {"candidate": "b" * 40}
        with patch.object(delivery, "git", return_value=("a" * 40).encode()):
            delivery.check_publication_head(
                repo, "refs/heads/brain/development", "a" * 40, receipt, False
            )
        with patch.object(delivery, "git", return_value=("b" * 40).encode()):
            delivery.check_publication_head(
                repo, "refs/heads/brain/development", "a" * 40, receipt, True
            )
        with patch.object(delivery, "git", return_value=("c" * 40).encode()):
            with self.assertRaisesRegex(delivery.DeliveryError, "canonical_expected_tip_changed"):
                delivery.check_publication_head(
                    repo, "refs/heads/brain/development", "a" * 40, receipt, True
                )

    def test_manifest_pins_one_safe_candidate_ref_and_manager_task(self) -> None:
        qa = self.root / "qa-workspace"
        qa.mkdir()
        custody = self.root / "custody"
        custody.mkdir(mode=0o700)

        def task(number: int) -> str:
            return f"00000000-0000-4000-8000-{number:012d}"

        manifest = {
            "schema": delivery.MANIFEST_SCHEMA,
            "api_origin": "http://127.0.0.1:3310",
            "company_id": task(1),
            "developer": {"task_id": task(2), "agent_id": task(3)},
            "qa": {"task_id": task(4), "agent_id": task(5)},
            "stage": {"task_id": task(6), "agent_id": task(7)},
            "publish": {"task_id": task(8), "agent_id": task(9)},
            "manager_task_id": task(10),
            "source": {
                "seed_repo": str(self.seed.resolve()),
                "base_sha": self.base,
                "paths": self.paths,
            },
            "canonical": {
                "repo": str(self.work.resolve()),
                "ref": delivery.PUBLISH_REF,
                "expected_tip": init_repo(self.root / "canonical"),
                "remote_name": "origin",
                "remote_url": "https://github.com/example/brain.git",
                "candidate_ref": "refs/heads/brain/native-report-mem132-20261026",
            },
            "qa_workspace": str(qa.resolve()),
            "custody_dir": str(custody.resolve()),
        }
        path = self.root / "manifest.json"
        path.write_bytes(delivery.json_bytes(manifest))
        path.chmod(0o600)
        loaded, _ = delivery.load_manifest(str(path.resolve()))
        self.assertNotIn("role_models", loaded)
        self.assertEqual(
            loaded["canonical"]["candidate_ref"], manifest["canonical"]["candidate_ref"]
        )
        manifest["role_models"] = {"developer": "gpt-6-luna", "qa": "gpt-6.1-sol"}
        path.write_bytes(delivery.json_bytes(manifest))
        loaded, _ = delivery.load_manifest(str(path.resolve()))
        self.assertEqual(loaded["role_models"], manifest["role_models"])
        for role_models in (
            None,
            {"developer": "gpt-6.1-sol", "qa": "gpt-6-luna"},
            {"developer": "gpt-6-luna", "qa": "gpt-unknown"},
            {"developer": "gpt-6-luna", "qa": []},
            {"developer": "gpt-6-luna"},
            {"developer": "gpt-6-luna", "qa": "gpt-6.1-sol", "stage": "gpt-6-luna"},
            ["gpt-6-luna", "gpt-6.1-sol"],
        ):
            with self.subTest(role_models=role_models):
                manifest["role_models"] = role_models
                path.write_bytes(delivery.json_bytes(manifest))
                with self.assertRaisesRegex(delivery.DeliveryError, "manifest_role_models_invalid"):
                    delivery.load_manifest(str(path.resolve()))
        manifest.pop("role_models")
        manifest["canonical"]["candidate_ref"] = "refs/heads/main"
        path.write_bytes(delivery.json_bytes(manifest))
        with self.assertRaisesRegex(delivery.DeliveryError, "manifest_canonical_target_invalid"):
            delivery.load_manifest(str(path.resolve()))

    def test_manifest_loader_accepts_hash_bound_v2_renewal_for_v1_reference(self) -> None:
        qa = self.root / "qa-resource"
        qa.mkdir()
        custody = self.root / "custody-resource"
        custody.mkdir(mode=0o700)
        company = "00000000-0000-4000-8000-000000009100"
        reference, renewal_path = write_v1_policy_v2_renewal(self.root, company)
        task = lambda number: f"00000000-0000-4000-8000-{number:012d}"
        canonical = init_repo(self.root / "canonical-resource")
        manifest = {
            "schema": delivery.MANIFEST_SCHEMA,
            "api_origin": "http://127.0.0.1:3310",
            "company_id": company,
            "developer": {"task_id": task(1), "agent_id": task(2)},
            "qa": {"task_id": task(3), "agent_id": task(4)},
            "stage": {"task_id": task(5), "agent_id": task(6)},
            "publish": {"task_id": task(7), "agent_id": task(8)},
            "source": {
                "seed_repo": str(self.seed.resolve()),
                "base_sha": self.base,
                "paths": ["src/new.py", "tests/test_new.py"],
            },
            "canonical": {
                "repo": str((self.root / "canonical-resource").resolve()),
                "ref": delivery.PUBLISH_REF,
                "expected_tip": canonical,
                "remote_name": "origin",
                "remote_url": "https://github.com/example/brain.git",
                "candidate_ref": "refs/heads/brain/resource-v2",
            },
            "qa_workspace": str(qa.resolve()),
            "custody_dir": str(custody.resolve()),
            "resource_policy": reference,
        }
        path = self.root / "manifest-resource.json"
        path.write_bytes(delivery.json_bytes(manifest))
        path.chmod(0o600)
        with patch.dict(os.environ, {delivery.resource_guard.RENEWAL_ENV: str(renewal_path)}):
            loaded, _ = delivery.load_manifest(str(path.resolve()))
        self.assertEqual(loaded["resource_policy"], reference)

    def test_fixed_path_safety_and_immutable_write_collision(self) -> None:
        root = self.root / "qa"
        root.mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (root / "src").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(delivery.DeliveryError, "target_parent_invalid"):
            delivery.target(root, "src/new.py", create=True)
        with self.assertRaisesRegex(delivery.DeliveryError, "relative_target_invalid"):
            delivery.target(root, "../escape")

        collision = self.root / "candidate.py"
        collision.write_bytes(b"owner data")
        with self.assertRaisesRegex(delivery.DeliveryError, "target_collision"):
            delivery.write_once(collision, b"worker data", 0o400)
        self.assertEqual(collision.read_bytes(), b"owner data")

    def test_pinned_predecessor_receipt_resolves_only_a_done_exact_chain(self) -> None:
        def task(number):
            return f"00000000-0000-4000-8000-{number:012d}"

        candidate = "f" * 40
        base = "e" * 40
        parent_manifest = {
            "company_id": task(1),
            "source": {"base_sha": base, "paths": ["src/one.py", "tests/test_one.py"]},
            "developer": {"task_id": task(2)},
            "publish": {"task_id": task(3), "agent_id": task(4)},
            "canonical": {
                "repo": str(self.work.resolve()),
                "ref": delivery.PUBLISH_REF,
                "expected_tip": base,
                "remote_name": "origin",
                "remote_url": "https://github.com/example/brain.git",
                "candidate_ref": "refs/heads/brain/first",
            },
        }
        manifest_hash = "a" * 64
        receipt_dir = self.root / "parent-custody"
        receipt_dir.mkdir(mode=0o700)
        receipt_path = receipt_dir / "publication.json"
        canonical_receipt = {"candidate": candidate, "receipt_digest": "b" * 64}
        saved = {
            "schema": delivery.PUBLISH_SCHEMA,
            "manifest_sha256": manifest_hash,
            "source_task_id": task(2),
            "expected_tip": base,
            "canonical_ref": delivery.PUBLISH_REF,
            "candidate_ref": "refs/heads/brain/first",
            "files": {"src/one.py": "1" * 64, "tests/test_one.py": "2" * 64},
            "canonical_receipt": canonical_receipt,
        }
        saved["publication_digest"] = delivery.digest(delivery.json_bytes(saved))
        receipt_path.write_bytes(delivery.json_bytes(saved))
        receipt_path.chmod(0o400)
        child_custody = self.root / "child-custody"
        child_custody.mkdir(mode=0o700)
        child = {
            "company_id": task(1),
            "source": {"base_sha": base, "paths": ["src/two.py", "tests/test_two.py"]},
            "developer": {"task_id": task(6)},
            "custody_dir": str(child_custody.resolve()),
            "canonical": {
                **parent_manifest["canonical"],
                "candidate_ref": "refs/heads/brain/second",
            },
            "predecessor": {
                "manifest_path": str(self.root / "parent-manifest.json"),
                "manifest_sha256": manifest_hash,
                "source_task_id": task(2),
                "publish_task_id": task(3),
                "receipt_path": str(receipt_path),
            },
        }

        class API:
            runs_override = None
            interactions_override = None
            issue_value = {
                "id": task(3),
                "companyId": task(1),
                "assigneeAgentId": task(4),
                "status": "done",
            }

            def issue(self, _task_id):
                return self.issue_value

            def no_pending(self, _task_id):
                return None

            def no_pending_terminal(self, _task_id):
                if self.interactions_override is not None and any(
                    row.get("status")
                    not in {"accepted", "rejected", "answered", "cancelled", "expired", "failed"}
                    for row in self.interactions_override
                ):
                    raise delivery.DeliveryError("predecessor_interaction_not_terminal")

            def request(self, _method, _route):
                if "/runs" in _route:
                    return self.runs_override or [
                        {
                            "status": "succeeded",
                            "id": task(5),
                            "agentId": task(4),
                            "contextIssueId": task(3),
                            "adapterType": "codex_local",
                        }
                    ]
                return {
                    "id": task(5),
                    "status": "succeeded",
                    "runtimeMode": "legacy",
                    "agentId": task(4),
                    "companyId": task(1),
                    "issueId": task(3),
                }

            def latest_run(self, _task):
                return {"status": "succeeded"}

        api = API()
        with (
            patch.object(delivery, "load_manifest", return_value=(parent_manifest, manifest_hash)),
            patch.object(delivery.publisher, "_check_candidate"),
            patch.object(delivery.publisher, "inspect"),
            patch.object(delivery, "git", return_value=candidate.encode()),
            patch.object(
                delivery,
                "remote_git",
                return_value=f"{candidate}\t{delivery.PUBLISH_REF}\n{candidate}\trefs/heads/brain/first\n".encode(),
            ),
        ):
            self.assertEqual(
                delivery.predecessor_expected_tip(child, manifest_hash, api), candidate
            )

            with self.subTest("later unrelated manager runs do not replace publisher run"):
                api.runs_override = [
                    {
                        "status": "succeeded",
                        "id": task(5),
                        "agentId": task(4),
                        "contextIssueId": task(3),
                        "adapterType": "codex_local",
                        "finishedAt": "2026-10-26T18:54:33.974Z",
                    },
                    {
                        "status": "succeeded",
                        "id": task(7),
                        "agentId": task(8),
                        "contextIssueId": task(3),
                        "adapterType": "codex_local",
                        "finishedAt": "2026-10-26T18:54:44.265Z",
                    },
                    {
                        "status": "running",
                        "id": task(9),
                        "agentId": task(8),
                        "contextIssueId": task(3),
                        "adapterType": "codex_local",
                        "startedAt": "2026-10-26T18:55:00.000Z",
                    },
                    {
                        "status": "failed",
                        "id": task(11),
                        "agentId": task(8),
                        "contextIssueId": task(3),
                        "adapterType": "codex_local",
                        "finishedAt": "2026-10-26T18:56:00.000Z",
                    },
                ]
                self.assertEqual(
                    delivery.predecessor_expected_tip(child, manifest_hash, api), candidate
                )
                api.runs_override = None

            with self.subTest("latest pinned publisher failure blocks historical success"):
                api.runs_override = [
                    {
                        "status": "succeeded",
                        "id": task(5),
                        "agentId": task(4),
                        "contextIssueId": task(3),
                        "finishedAt": "2026-10-26T18:54:33.974Z",
                    },
                    {
                        "status": "failed",
                        "id": task(13),
                        "agentId": task(4),
                        "contextIssueId": task(3),
                        "finishedAt": "2026-10-26T18:57:00.000Z",
                    },
                ]
                with self.assertRaisesRegex(
                    delivery.DeliveryError, "predecessor_publish_run_not_succeeded"
                ):
                    delivery.predecessor_expected_tip(child, manifest_hash, api)
                api.runs_override = None

            with self.subTest("latest pinned publisher running blocks historical success"):
                api.runs_override = [
                    {
                        "status": "succeeded",
                        "id": task(5),
                        "agentId": task(4),
                        "contextIssueId": task(3),
                        "finishedAt": "2026-10-26T18:54:33.974Z",
                    },
                    {
                        "status": "running",
                        "id": task(14),
                        "agentId": task(4),
                        "contextIssueId": task(3),
                        "startedAt": "2026-10-26T18:57:00.000Z",
                    },
                ]
                with self.assertRaisesRegex(
                    delivery.DeliveryError, "predecessor_publish_run_not_terminal"
                ):
                    delivery.predecessor_expected_tip(child, manifest_hash, api)
                api.runs_override = None

            with self.subTest("missing pinned publisher run blocks unrelated success"):
                api.runs_override = [
                    {
                        "status": "succeeded",
                        "id": task(15),
                        "agentId": task(8),
                        "contextIssueId": task(3),
                        "finishedAt": "2026-10-26T18:57:00.000Z",
                    },
                    {
                        "status": "succeeded",
                        "id": task(16),
                        "agentId": task(4),
                        "contextIssueId": task(17),
                        "finishedAt": "2026-10-26T18:58:00.000Z",
                    },
                ]
                with self.assertRaisesRegex(
                    delivery.DeliveryError, "predecessor_publish_run_not_terminal"
                ):
                    delivery.predecessor_expected_tip(child, manifest_hash, api)
                api.runs_override = None

            with self.subTest("durable second-slot replay after canonical advanced"):
                child_candidate = "d" * 40
                own_receipt = {
                    "schema": delivery.PUBLISH_SCHEMA,
                    "source_task_id": task(6),
                    "manifest_sha256": manifest_hash,
                    "expected_tip": candidate,
                    "canonical_ref": delivery.PUBLISH_REF,
                    "candidate_ref": child["canonical"]["candidate_ref"],
                    "canonical_receipt": {"candidate": child_candidate, "receipt_digest": "c" * 64},
                }
                own_receipt["publication_digest"] = delivery.digest(
                    delivery.json_bytes(own_receipt)
                )
                own_path = child_custody / f"publication-{task(6)}.json"
                own_path.write_bytes(delivery.json_bytes(own_receipt))
                own_path.chmod(0o400)
                with (
                    patch.object(delivery, "git", return_value=child_candidate.encode()),
                    patch.object(
                        delivery,
                        "remote_git",
                        return_value=(
                            f"{child_candidate}\t{delivery.PUBLISH_REF}\n"
                            f"{candidate}\trefs/heads/brain/first\n"
                            f"{child_candidate}\t{child['canonical']['candidate_ref']}\n"
                        ).encode(),
                    ),
                ):
                    self.assertEqual(
                        delivery.predecessor_expected_tip(child, manifest_hash, api), candidate
                    )
                with (
                    patch.object(
                        delivery,
                        "git",
                        return_value=child_candidate.encode(),
                    ),
                    patch.object(
                        delivery,
                        "remote_git",
                        return_value=(
                            f"{child_candidate}\t{delivery.PUBLISH_REF}\n"
                            f"{candidate}\trefs/heads/brain/first\n"
                        ).encode(),
                    ),
                ):
                    self.assertEqual(
                        delivery.predecessor_expected_tip(child, manifest_hash, api), candidate
                    )
                with (
                    patch.object(
                        delivery,
                        "git",
                        return_value=child_candidate.encode(),
                    ),
                    patch.object(
                        delivery,
                        "remote_git",
                        return_value=(
                            f"{child_candidate}\t{delivery.PUBLISH_REF}\n"
                            f"{candidate}\trefs/heads/brain/first\n"
                            f"{'a' * 40}\t{child['canonical']['candidate_ref']}\n"
                        ).encode(),
                    ),
                ):
                    with self.assertRaisesRegex(delivery.DeliveryError, "remote_head_unexpected"):
                        delivery.predecessor_expected_tip(child, manifest_hash, api)

                own_path.unlink()

            with self.subTest("missing receipt"):
                receipt_path.unlink()
                with self.assertRaisesRegex(delivery.DeliveryError, "receipt_missing"):
                    delivery.predecessor_expected_tip(child, manifest_hash, api)
                receipt_path.write_bytes(delivery.json_bytes(saved))
                receipt_path.chmod(0o400)

            with self.subTest("tampered receipt"):
                tampered = {**saved, "candidate_ref": "refs/heads/brain/forged"}
                receipt_path.chmod(0o600)
                receipt_path.write_bytes(delivery.json_bytes(tampered))
                receipt_path.chmod(0o400)
                with self.assertRaisesRegex(delivery.DeliveryError, "receipt_invalid"):
                    delivery.predecessor_expected_tip(child, manifest_hash, api)
                receipt_path.chmod(0o600)
                receipt_path.write_bytes(delivery.json_bytes(saved))
                receipt_path.chmod(0o400)

            with self.subTest("wrong target"):
                wrong = {**child, "canonical": {**child["canonical"], "repo": str(self.seed)}}
                with self.assertRaisesRegex(delivery.DeliveryError, "target_mismatch"):
                    delivery.predecessor_expected_tip(wrong, manifest_hash, api)

            with self.subTest("manifest identity mismatch"):
                with patch.object(
                    delivery, "load_manifest", return_value=(parent_manifest, "c" * 64)
                ):
                    with self.assertRaisesRegex(delivery.DeliveryError, "manifest_hash_mismatch"):
                        delivery.predecessor_expected_tip(child, manifest_hash, api)

            with self.subTest("publish task not done"):
                api.issue_value = {**api.issue_value, "status": "in_progress"}
                with self.assertRaisesRegex(delivery.DeliveryError, "not_done"):
                    delivery.predecessor_expected_tip(child, manifest_hash, api)
                api.issue_value = {**api.issue_value, "status": "done"}

            with self.subTest("publish run still active"):
                api.runs_override = [{"status": "running", "id": task(5), "agentId": task(4)}]
                with self.assertRaisesRegex(delivery.DeliveryError, "run_not_terminal"):
                    delivery.predecessor_expected_tip(child, manifest_hash, api)
                api.runs_override = None

            for status in ("interrupted", "scheduled_retry", "unknown"):
                with self.subTest(run_status=status):
                    api.runs_override = [{"status": status, "id": task(5), "agentId": task(4)}]
                    with self.assertRaisesRegex(delivery.DeliveryError, "run_not_terminal"):
                        delivery.predecessor_expected_tip(child, manifest_hash, api)
            api.runs_override = None

            for status in ("pending", "unknown"):
                with self.subTest(interaction_status=status):
                    api.interactions_override = [{"status": status}]
                    with self.assertRaisesRegex(delivery.DeliveryError, "interaction_not_terminal"):
                        delivery.predecessor_expected_tip(child, manifest_hash, api)
            api.interactions_override = None

            with self.subTest("unexpected local head"):
                with patch.object(delivery, "git", return_value=base.encode()):
                    with self.assertRaisesRegex(
                        delivery.DeliveryError, "canonical_head_unexpected"
                    ):
                        delivery.predecessor_expected_tip(child, manifest_hash, api)

            with self.subTest("unexpected remote head"):
                with patch.object(
                    delivery,
                    "remote_git",
                    return_value=f"{base}\t{delivery.PUBLISH_REF}\n{candidate}\trefs/heads/brain/first\n".encode(),
                ):
                    with self.assertRaisesRegex(delivery.DeliveryError, "remote_head_unexpected"):
                        delivery.predecessor_expected_tip(child, manifest_hash, api)

    def test_predecessor_receipt_replay_after_real_child_reconcile(self) -> None:
        def task(number):
            return f"00000000-0000-4000-8000-{number:012d}"

        repo = self.root / "chain-repo"
        base = init_repo(repo)
        ref = delivery.PUBLISH_REF
        git(repo, "update-ref", ref, base)
        git(repo, "symbolic-ref", "HEAD", ref)
        parent_paths = ["src/chain_one.py", "tests/test_chain_one.py"]
        child_paths = ["src/chain_two.py", "tests/test_chain_two.py"]
        parent_files = {parent_paths[0]: b"ONE = 1\n", parent_paths[1]: b"assert ONE == 1\n"}
        child_files = {child_paths[0]: b"TWO = 2\n", child_paths[1]: b"assert TWO == 2\n"}
        for files in (parent_files, child_files):
            for name, data in files.items():
                target = repo / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        git(repo, "add", "--", *parent_paths, *child_paths)

        def publish_objects(expected: str, files: dict[str, bytes], paths: list[str]):
            old = {name: None for name in paths}
            new = {
                name: delivery.publisher._git(repo, "hash-object", "--stdin", data=data)
                .decode()
                .strip()
                for name, data in files.items()
            }
            return delivery.publisher.prepare(
                repo, ref, expected, old, new, files, allowed_paths=paths
            )

        parent_candidate_receipt = publish_objects(base, parent_files, parent_paths)
        parent_candidate = parent_candidate_receipt["candidate"]
        delivery.publisher.reconcile(
            repo,
            ref,
            parent_candidate_receipt,
            parent_candidate,
            parent_candidate_receipt["receipt_digest"],
            allowed_paths=parent_paths,
        )

        company = task(1)
        parent_hash = "a" * 64
        parent_custody = self.root / "real-parent-custody"
        parent_custody.mkdir(mode=0o700)
        child_custody = self.root / "real-child-custody"
        child_custody.mkdir(mode=0o700)
        parent_manifest = {
            "company_id": company,
            "source": {"base_sha": base, "paths": parent_paths},
            "developer": {"task_id": task(2)},
            "publish": {"task_id": task(3), "agent_id": task(4)},
            "canonical": {
                "repo": str(repo.resolve()),
                "ref": ref,
                "expected_tip": base,
                "remote_name": "origin",
                "remote_url": "https://github.com/example/brain.git",
                "candidate_ref": "refs/heads/brain/chain-one",
            },
        }
        parent_receipt_path = parent_custody / f"publication-{task(2)}.json"
        parent_op = {
            "schema": delivery.PUBLISH_SCHEMA,
            "manifest_sha256": parent_hash,
            "source_task_id": task(2),
            "expected_tip": base,
            "canonical_ref": ref,
            "candidate_ref": parent_manifest["canonical"]["candidate_ref"],
            "files": {
                name: hashlib.sha256(value).hexdigest() for name, value in parent_files.items()
            },
        }
        parent_saved = {**parent_op, "canonical_receipt": parent_candidate_receipt}
        parent_saved["publication_digest"] = delivery.digest(delivery.json_bytes(parent_saved))
        delivery.save_record(parent_receipt_path, parent_saved)

        child = {
            "company_id": company,
            "source": {"base_sha": base, "paths": child_paths},
            "developer": {"task_id": task(6)},
            "custody_dir": str(child_custody.resolve()),
            "canonical": {
                **parent_manifest["canonical"],
                "expected_tip": parent_candidate,
                "candidate_ref": "refs/heads/brain/chain-two",
            },
            "predecessor_base_tip": base,
            "lineage_depth": 1,
            "predecessor": {
                "manifest_path": str(self.root / "parent-manifest.json"),
                "manifest_sha256": parent_hash,
                "source_task_id": task(2),
                "publish_task_id": task(3),
                "receipt_path": str(parent_receipt_path.resolve()),
            },
        }
        child_candidate_receipt = publish_objects(parent_candidate, child_files, child_paths)
        child_candidate = child_candidate_receipt["candidate"]
        child_manifest_path = self.root / "child-manifest.json"
        child_manifest_bytes = delivery.json_bytes(
            {
                "schema": delivery.MANIFEST_SCHEMA,
                "api_origin": "http://127.0.0.1:3310",
                "company_id": company,
                "developer": {"task_id": task(6), "agent_id": task(9)},
                "qa": {"task_id": task(7), "agent_id": task(10)},
                "stage": {"task_id": task(11), "agent_id": task(12)},
                "publish": {"task_id": task(8), "agent_id": task(13)},
                "source": {"seed_repo": str(repo), "base_sha": base, "paths": child_paths},
                "canonical": child["canonical"],
                "qa_workspace": str(repo),
                "custody_dir": str(child_custody),
                "predecessor": child["predecessor"],
                "predecessor_base_tip": base,
                "lineage_depth": 1,
            }
        )
        child_manifest_path.write_bytes(child_manifest_bytes)
        child_manifest_path.chmod(0o600)
        child_hash = delivery.digest(child_manifest_bytes)
        child_op = {
            "schema": delivery.PUBLISH_SCHEMA,
            "manifest_sha256": child_hash,
            "source_task_id": task(6),
            "expected_tip": parent_candidate,
            "canonical_ref": ref,
            "candidate_ref": child["canonical"]["candidate_ref"],
            "files": {
                name: hashlib.sha256(value).hexdigest() for name, value in child_files.items()
            },
        }
        child_saved = {**child_op, "canonical_receipt": child_candidate_receipt}
        child_saved["publication_digest"] = delivery.digest(delivery.json_bytes(child_saved))
        delivery.save_record(child_custody / f"publication-{task(6)}.json", child_saved)

        class API:
            def issue(self, task_id):
                if task_id == task(3):
                    publisher_task, publisher_agent = task(3), task(4)
                elif task_id == task(8):
                    publisher_task, publisher_agent = task(8), task(13)
                else:
                    raise AssertionError(task_id)
                return {
                    "id": publisher_task,
                    "companyId": company,
                    "assigneeAgentId": publisher_agent,
                    "status": "done",
                }

            def no_pending(self, _task_id):
                return None

            def no_pending_terminal(self, _task_id):
                return None

            def request(self, _method, route):
                if f"/issues/{task(3)}/runs" == route:
                    return [
                        {
                            "status": "succeeded",
                            "id": task(5),
                            "agentId": task(4),
                            "contextIssueId": task(3),
                        }
                    ]
                if f"/issues/{task(8)}/runs" == route:
                    return [
                        {
                            "status": "succeeded",
                            "id": task(14),
                            "agentId": task(13),
                            "contextIssueId": task(8),
                        }
                    ]
                if f"/heartbeat-runs/{task(14)}" == route:
                    return {
                        "id": task(14),
                        "status": "succeeded",
                        "runtimeMode": "legacy",
                        "agentId": task(13),
                        "companyId": company,
                        "issueId": task(8),
                    }
                return {
                    "id": task(5),
                    "status": "succeeded",
                    "runtimeMode": "legacy",
                    "agentId": task(4),
                    "companyId": company,
                    "issueId": task(3),
                }

        remote_canonical = parent_candidate
        remote_child_candidate = None

        def remote_refs(_repo, *_args, **_kwargs):
            rows = [f"{remote_canonical}\t{ref}", f"{parent_candidate}\trefs/heads/brain/chain-one"]
            if remote_child_candidate is not None:
                rows.append(f"{remote_child_candidate}\trefs/heads/brain/chain-two")
            return ("\n".join(rows) + "\n").encode()

        child_manifest = json.loads(child_manifest_bytes)
        manifests = {
            str((self.root / "parent-manifest.json").resolve()): (parent_manifest, parent_hash),
            str(child_manifest_path.resolve()): (child_manifest, child_hash),
        }
        with (
            patch.object(
                delivery,
                "load_manifest",
                side_effect=lambda p: manifests[str(Path(p).resolve())],
            ),
            patch.object(delivery, "remote_git", side_effect=remote_refs),
        ):
            api = API()
            # Receipt saved, but canonical reconcile has not happened yet.
            self.assertEqual(
                delivery.predecessor_expected_tip(child, child_hash, api), parent_candidate
            )

            # Reconcile child locally and remotely, while its remote candidate ref is still absent.
            delivery.publisher.reconcile(
                repo,
                ref,
                child_candidate_receipt,
                child_candidate,
                child_candidate_receipt["receipt_digest"],
                allowed_paths=child_paths,
            )
            remote_canonical = child_candidate
            self.assertEqual(
                delivery.predecessor_expected_tip(child, child_hash, api), parent_candidate
            )
            # Once the candidate ref has been pushed, completed publication replay stays valid.
            remote_child_candidate = child_candidate
            self.assertEqual(
                delivery.predecessor_expected_tip(child, child_hash, api), parent_candidate
            )
            grandchild = {
                "company_id": company,
                "source": {
                    "base_sha": base,
                    "paths": ["src/chain_three.py", "tests/test_chain_three.py"],
                },
                "developer": {"task_id": task(15)},
                "custody_dir": str((self.root / "real-grandchild-custody").resolve()),
                "canonical": {
                    **parent_manifest["canonical"],
                    "expected_tip": child_candidate,
                    "candidate_ref": "refs/heads/brain/chain-three",
                },
                "predecessor_base_tip": parent_candidate,
                "lineage_depth": 2,
                "predecessor": {
                    "manifest_path": str(child_manifest_path.resolve()),
                    "manifest_sha256": child_hash,
                    "source_task_id": task(6),
                    "publish_task_id": task(8),
                    "receipt_path": str((child_custody / f"publication-{task(6)}.json").resolve()),
                },
            }
            self.assertEqual(
                delivery.predecessor_expected_tip(grandchild, "c" * 64, api), child_candidate
            )
            with self.assertRaisesRegex(
                delivery.DeliveryError, "predecessor_lineage_depth_invalid"
            ):
                delivery.predecessor_expected_tip({**grandchild, "lineage_depth": 9}, "c" * 64, api)

    def test_wrong_current_actor_cannot_complete_a_pinned_task(self) -> None:
        api = delivery.Paperclip.__new__(delivery.Paperclip)
        api.m = {
            "company_id": "00000000-0000-4000-8000-000000000001",
            "stage": {
                "task_id": "00000000-0000-4000-8000-000000000002",
                "agent_id": "00000000-0000-4000-8000-000000000003",
            },
        }
        api.agent_id = "00000000-0000-4000-8000-000000000004"
        api.run_id = "00000000-0000-4000-8000-000000000005"
        with patch.object(
            api,
            "request",
            return_value={"id": api.agent_id, "companyId": api.m["company_id"]},
        ):
            with self.assertRaisesRegex(delivery.DeliveryError, "current_actor_mismatch"):
                api.current("stage")

    def test_process_task_uses_legacy_context_and_run_bound_checkout(self) -> None:
        company = "00000000-0000-4000-8000-000000000021"
        task_id = "00000000-0000-4000-8000-000000000022"
        agent = "00000000-0000-4000-8000-000000000023"
        run_id = "00000000-0000-4000-8000-000000000024"
        manifest = {"company_id": company, "stage": {"task_id": task_id, "agent_id": agent}}
        api = delivery.Paperclip.__new__(delivery.Paperclip)
        api.m, api.agent_id, api.run_id = manifest, agent, run_id
        issue = {
            "id": task_id,
            "companyId": company,
            "assigneeAgentId": agent,
            "status": "todo",
            "checkoutRunId": None,
            "executionRunId": None,
        }
        calls = []

        def response(method: str, route: str, body=None, raw=False):
            calls.append((method, route, body))
            if route == "/agents/me":
                return {"id": agent, "companyId": company}
            if route == f"/heartbeat-runs/{run_id}":
                return {
                    "id": run_id,
                    "agentId": agent,
                    "companyId": company,
                    "runtimeMode": "legacy",
                    "status": "running",
                    "contextSnapshot": {"issueId": task_id},
                }
            if route == f"/issues/{task_id}" and method == "GET":
                return dict(issue)
            if route == f"/issues/{task_id}/interactions":
                return []
            if route == f"/issues/{task_id}/checkout" and method == "POST":
                issue.update(status="in_progress", checkoutRunId=run_id, executionRunId=run_id)
                return dict(issue)
            raise AssertionError((method, route, body, raw))

        with patch.object(api, "request", side_effect=response):
            self.assertEqual(api.current("stage"), task_id)
        self.assertIn(
            (
                "POST",
                f"/issues/{task_id}/checkout",
                {"agentId": agent, "expectedStatuses": ["todo", "blocked", "in_progress"]},
            ),
            calls,
        )

    def test_process_api_url_is_origin_and_writes_are_run_bound(self) -> None:
        env = {
            "PAPERCLIP_API_KEY": "synthetic",
            "PAPERCLIP_RUN_ID": "00000000-0000-4000-8000-000000000031",
            "PAPERCLIP_AGENT_ID": "00000000-0000-4000-8000-000000000032",
            "PAPERCLIP_COMPANY_ID": "00000000-0000-4000-8000-000000000033",
            "PAPERCLIP_API_URL": "http://127.0.0.1:3310",
        }
        manifest = {
            "api_origin": "http://127.0.0.1:3310",
            "company_id": env["PAPERCLIP_COMPANY_ID"],
        }
        with patch.dict(os.environ, env, clear=True):
            api = delivery.Paperclip(manifest)
        self.assertEqual(api.base, "http://127.0.0.1:3310/api")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _size):
                return b"{}"

        with patch(
            "engineering.paperclip_native_delivery.urllib.request.urlopen", return_value=Response()
        ) as opened:
            api.request("PATCH", "/issues/example", {"status": "done"})
        req = opened.call_args.args[0]
        self.assertEqual(req.get_header("X-paperclip-run-id"), env["PAPERCLIP_RUN_ID"])

    def test_git_uses_only_the_pinned_gh_helper_and_config_directory(self) -> None:
        result = subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with patch(
            "engineering.paperclip_native_delivery.subprocess.run", return_value=result
        ) as run:
            delivery.remote_git(self.work, "ls-remote", "origin", github_auth=True)
        command = run.call_args.args[0]
        self.assertIn("credential.helper=", command)
        self.assertIn(f"credential.helper={delivery.GH_HELPER}", command)
        self.assertEqual(run.call_args.kwargs["env"]["HOME"], delivery.GH_OWNER_HOME)
        self.assertEqual(run.call_args.kwargs["env"]["GH_CONFIG_DIR"], delivery.GH_CONFIG_DIR)
        self.assertNotIn("PAPERCLIP_API_KEY", run.call_args.kwargs["env"])

    def test_saved_native_run_shape_uses_id_and_included_subscription_billing(self) -> None:
        company = "00000000-0000-4000-8000-000000000011"
        task_id = "00000000-0000-4000-8000-000000000012"
        agent_id = "00000000-0000-4000-8000-000000000013"
        run_id = "00000000-0000-4000-8000-000000000014"
        api = delivery.Paperclip.__new__(delivery.Paperclip)
        developer = {"task_id": task_id, "agent_id": agent_id}
        qa = {
            "task_id": "00000000-0000-4000-8000-000000000015",
            "agent_id": "00000000-0000-4000-8000-000000000016",
        }
        api.m = {
            "company_id": company,
            "developer": developer,
            "qa": qa,
            "role_models": {"developer": "gpt-6-luna", "qa": "gpt-6.1-sol"},
        }
        provider_model = "gpt-6.1-sol"
        current_task_id = qa["task_id"]
        current_agent_id = qa["agent_id"]

        def response(_method: str, route: str, **_kwargs):
            if route == f"/issues/{current_task_id}/runs":
                return [
                    {
                        "id": run_id,
                        "issueId": current_task_id,
                        "contextIssueId": current_task_id,
                        "agentId": current_agent_id,
                        "status": "succeeded",
                    }
                ]
            if route == f"/heartbeat-runs/{run_id}":
                return {
                    "id": run_id,
                    "issueId": current_task_id,
                    "nativeIssueId": current_task_id,
                    "agentId": current_agent_id,
                    "companyId": company,
                    "runtimeMode": "native",
                    "status": "succeeded",
                    "runnerProfileJson": {
                        "nativeExecutionInput": {
                            "provider": {
                                "kind": "codex",
                                "model": provider_model,
                                "reasoningEffort": "high",
                            }
                        }
                    },
                    "usageJson": {"billingType": "subscription_included"},
                }
            raise AssertionError(route)

        with patch.object(api, "request", side_effect=response):
            run = api.latest_run(qa)
        self.assertEqual(run["runId"], run_id)
        for task, model in ((developer, "gpt-6.1-sol"), (qa, "gpt-6-luna")):
            current_task_id = task["task_id"]
            current_agent_id = task["agent_id"]
            provider_model = model
            with patch.object(api, "request", side_effect=response):
                with self.assertRaisesRegex(
                    delivery.DeliveryError, "latest_run_identity_or_subscription_invalid"
                ):
                    api.latest_run(task)
        api.m.pop("role_models")
        current_task_id = qa["task_id"]
        current_agent_id = qa["agent_id"]
        provider_model = "gpt-6-luna"
        with patch.object(api, "request", side_effect=response):
            self.assertEqual(api.latest_run(qa)["runId"], run_id)
        with self.assertRaisesRegex(delivery.DeliveryError, "task_role_binding_invalid"):
            api.latest_run({"task_id": "unbound", "agent_id": agent_id})
        api.m["role_models"] = {"developer": "gpt-6.1-sol", "qa": "gpt-6.1-sol"}
        with self.assertRaisesRegex(delivery.DeliveryError, "manifest_role_models_invalid"):
            api.latest_run(qa)

    def test_latest_run_ignores_activity_linked_runs_and_fails_closed(self) -> None:
        company = "00000000-0000-4000-8000-000000000041"
        qa_task = "00000000-0000-4000-8000-000000000042"
        qa_agent = "00000000-0000-4000-8000-000000000043"
        manager_task = "00000000-0000-4000-8000-000000000044"
        manager_agent = "00000000-0000-4000-8000-000000000045"
        successful_run = "00000000-0000-4000-8000-000000000046"
        latest_run = "00000000-0000-4000-8000-000000000047"
        qa = {"task_id": qa_task, "agent_id": qa_agent}
        api = delivery.Paperclip.__new__(delivery.Paperclip)
        api.m = {
            "company_id": company,
            "developer": {
                "task_id": "00000000-0000-4000-8000-000000000048",
                "agent_id": "00000000-0000-4000-8000-000000000049",
            },
            "qa": qa,
        }
        rows = [
            {
                "runId": successful_run,
                "runtimeMode": "native",
                "status": "succeeded",
                "agentId": qa_agent,
                "startedAt": "2026-10-26T20:42:08.806Z",
                "finishedAt": "2026-10-26T20:42:40.047Z",
                "contextIssueId": qa_task,
            },
            {
                "runId": latest_run,
                "runtimeMode": "native",
                "status": "succeeded",
                "agentId": manager_agent,
                "startedAt": "2026-10-26T20:41:23.058Z",
                "finishedAt": "2026-10-26T20:43:09.578Z",
                "contextIssueId": manager_task,
            },
            "malformed-row",
        ]
        heartbeat = {
            "id": successful_run,
            "issueId": qa_task,
            "nativeIssueId": qa_task,
            "agentId": qa_agent,
            "companyId": company,
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
            "usageJson": {"billingType": "subscription_included"},
        }

        def response(_method: str, route: str, **_kwargs):
            if route == f"/issues/{qa_task}/runs":
                return rows
            if route == f"/heartbeat-runs/{successful_run}":
                return heartbeat
            if route == f"/heartbeat-runs/{latest_run}":
                return {
                    **heartbeat,
                    "id": latest_run,
                    "issueId": qa_task,
                    "status": rows[0]["status"],
                }
            raise AssertionError(route)

        with patch.object(api, "request", side_effect=response):
            self.assertEqual(api.latest_run(qa)["runId"], successful_run)

        for status in ("failed", "running"):
            with self.subTest(latest_correct_actor_status=status):
                rows[:] = [
                    {
                        "runId": successful_run,
                        "agentId": qa_agent,
                        "contextIssueId": qa_task,
                        "status": "succeeded",
                        "finishedAt": "2026-10-26T20:42:40.047Z",
                    },
                    {
                        "runId": latest_run,
                        "agentId": qa_agent,
                        "contextIssueId": qa_task,
                        "status": status,
                        "finishedAt": "2026-10-26T20:43:09.578Z",
                    },
                    {
                        "runId": "00000000-0000-4000-8000-000000000050",
                        "agentId": manager_agent,
                        "contextIssueId": manager_task,
                        "status": "succeeded",
                        "finishedAt": "2026-10-26T20:44:09.578Z",
                    },
                ]
                with patch.object(api, "request", side_effect=response):
                    with self.assertRaisesRegex(
                        delivery.DeliveryError, "latest_task_run_not_successful"
                    ):
                        api.latest_run(qa)

        rows[:] = [
            {
                "runId": latest_run,
                "agentId": manager_agent,
                "contextIssueId": manager_task,
                "status": "succeeded",
                "finishedAt": "2026-10-26T20:43:09.578Z",
            },
            {
                "runId": successful_run,
                "agentId": qa_agent,
                "contextIssueId": manager_task,
                "status": "succeeded",
                "finishedAt": "2026-10-26T20:42:40.047Z",
            },
        ]
        with patch.object(api, "request", side_effect=response):
            with self.assertRaisesRegex(delivery.DeliveryError, "latest_task_run_not_successful"):
                api.latest_run(qa)


class ResourceUpgradePreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="native-resource-preflight-")
        self.root = Path(self.temp.name).resolve()
        self.company = "00000000-0000-4000-8000-000000009110"
        self.reference, self.renewal_path = write_v1_policy_v2_renewal(self.root, self.company)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_prepare_stage_and_publish_preflight_accept_v2_renewal(self) -> None:
        now = datetime.now(UTC)
        connection = "00000000-0000-4000-8000-000000009001"
        usage = {
            "connectionId": connection,
            "source": "codex_wham",
            "provider": "openai",
            "method": "subscription",
            "status": "ok",
            "checkedAt": now.isoformat().replace("+00:00", "Z"),
            "limits": [
                {
                    "id": "codex.primary_window",
                    "allowed": True,
                    "remainingPercent": 80,
                    "usedPercent": 20,
                }
            ],
            "overage": {
                "available": False,
                "unlimited": False,
                "enabled": False,
                "used": 0,
                "balance": 0,
            },
        }

        class ReadOnlyFakeAPI:
            m = {"company_id": self.company}
            run_id = "00000000-0000-4000-8000-000000009111"

            def request(_self, method: str, route: str):
                if method == "GET" and route == (
                    f"/companies/{self.company}/issues?limit=100&offset=0"
                ):
                    return [
                        {
                            "id": "00000000-0000-4000-8000-000000009002",
                            "companyId": self.company,
                            "parentId": None,
                        }
                    ]
                if method == "GET" and route == "/issues/00000000-0000-4000-8000-000000009002/runs":
                    return []
                raise AssertionError(f"unexpected fake API call: {method} {route}")

        with (
            patch.dict(os.environ, {delivery.resource_guard.RENEWAL_ENV: str(self.renewal_path)}),
            patch.object(delivery.resource_guard, "read_board_usage", return_value=usage),
        ):
            for phase in ("prepare", "stage", "publish"):
                receipt_path = delivery._resource_preflight(
                    ReadOnlyFakeAPI(),
                    self.reference,
                    phase,
                    self.root / "resource-policy",
                    reserve_model_runs=8 if phase == "prepare" else 0,
                )
                self.assertIsNotNone(receipt_path)
                receipt = json.loads(receipt_path.read_bytes())
                self.assertEqual(receipt["schema"], delivery.resource_guard.RECEIPT_SCHEMA_V2)
                self.assertEqual(receipt["phase"], phase)


class PreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="native-preparation-")
        self.root = Path(self.temp.name).resolve()
        self.seed = self.root / "seed"
        self.base = init_repo(self.seed)
        self.canonical = self.root / "canonical"
        init_repo(self.canonical)
        git(self.canonical, "update-ref", delivery.PUBLISH_REF, "HEAD")
        self.output = self.root / "prepared"
        self.ids = {
            name: f"00000000-0000-4000-8000-{number:012d}"
            for number, name in enumerate(
                (
                    "company",
                    "planning",
                    "planning_agent",
                    "prepare",
                    "prepare_agent",
                    "manager",
                    "manager_agent",
                    "developer",
                    "stage",
                    "qa",
                    "publish",
                    "source_project",
                    "source_workspace",
                    "qa_project",
                    "qa_workspace",
                    "source_task",
                    "stage_task",
                    "qa_task",
                    "publish_task",
                    "run",
                    "attachment",
                ),
                100,
            )
        }
        self.acceptance = "The module and focused unit check behave deterministically."
        self.envelope = {
            "schema": delivery.PREPARATION_SCHEMA,
            "api_origin": "http://127.0.0.1:3310",
            "company_id": self.ids["company"],
            "planning": {"task_id": self.ids["planning"], "agent_id": self.ids["planning_agent"]},
            "prepare": {"task_id": self.ids["prepare"], "agent_id": self.ids["prepare_agent"]},
            "manager": {"task_id": self.ids["manager"], "agent_id": self.ids["manager_agent"]},
            "roles": {name: self.ids[name] for name in ("developer", "stage", "qa", "publish")},
            "role_models": {
                "planning": "gpt-6.1-sol",
                "manager": "gpt-6.1-sol",
                "developer": "gpt-6-luna",
                "qa": "gpt-6.1-sol",
            },
            "source": {
                "project_id": self.ids["source_project"],
                "project_workspace_id": self.ids["source_workspace"],
                "workspace_path": str(self.seed),
                "seed_repo": str(self.seed),
                "base_sha": self.base,
            },
            "canonical": {
                "repo": str(self.canonical),
                "ref": delivery.PUBLISH_REF,
                "expected_tip": git(self.canonical, "rev-parse", delivery.PUBLISH_REF)
                .decode()
                .strip(),
                "remote_name": "origin",
                "remote_url": "https://github.com/brain/example.git",
            },
            "output_root": str(self.output),
            "slots": [
                {
                    "slot": 1,
                    "paths": ["src/new_module.py", "tests/test_new_module.py"],
                    "acceptance_sha256": hashlib.sha256(self.acceptance.encode()).hexdigest(),
                    "qa_project_id": self.ids["qa_project"],
                    "candidate_ref": "refs/heads/brain/candidate-slot-1",
                }
            ],
        }
        self.envelope_path = self.root / "prepare.json"
        self.envelope_path.write_bytes(delivery.json_bytes(self.envelope))
        self.envelope_path.chmod(0o600)
        self.plan = {
            "schema": delivery.DELIVERY_PLAN_SCHEMA,
            "company_id": self.ids["company"],
            "planning_task_id": self.ids["planning"],
            "manager_task_id": self.ids["manager"],
            "slots": [
                {
                    "slot": 1,
                    "title": "Add bounded feature",
                    "module": "Senses",
                    "benefit": "Answers a concrete data-quality question.",
                    "acceptance": self.acceptance,
                    "nonduplication_evidence": "Current source lacks this exact bounded check.",
                    "paths": self.envelope["slots"][0]["paths"],
                    "source_task_id": self.ids["source_task"],
                    "stage_task_id": self.ids["stage_task"],
                    "qa_task_id": self.ids["qa_task"],
                    "publish_task_id": self.ids["publish_task"],
                    "qa_workspace_id": self.ids["qa_workspace"],
                }
            ],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fake_api(
        self,
        *,
        bad_qa_selection: bool = False,
        bad_stage_selection: bool = False,
        manager_blockers: list[str] | None = None,
        manager_status: str = "blocked",
    ):
        ids, task = self.ids, self.plan["slots"][0]
        manifest_path = self.output / "slot1" / "manifest.json"
        qa_path = self.output / "slot1" / "qa"
        plan_bytes = delivery.json_bytes(self.plan)

        def issue_row(
            task_id: str, agent: str, title: str, status: str, blocked: list[str], extra: dict
        ):
            return {
                "id": task_id,
                "companyId": ids["company"],
                "parentId": ids["manager"],
                "assigneeAgentId": agent,
                "status": status,
                "executionWorkspaceId": None,
                "blockedBy": [{"id": item} for item in blocked],
                "title": title,
                **extra,
            }

        source = issue_row(
            ids["source_task"],
            ids["developer"],
            task["title"],
            "backlog",
            [],
            {
                "projectId": ids["source_project"],
                "projectWorkspaceId": ids["source_workspace"],
                "workspaceSelection": {
                    "version": 1,
                    "source": "explicit",
                    "selection": {
                        "kind": "configured_source",
                        "projectWorkspaceId": ids["source_workspace"],
                        "mode": "managed_isolated",
                    },
                },
                "executionWorkspaceSettings": {"mode": "isolated_workspace"},
                "description": self.acceptance + " src/new_module.py tests/test_new_module.py",
                "assigneeAdapterOverrides": {
                    "adapterConfig": {"cwd": str(self.seed)},
                    "useProjectWorkspace": True,
                },
            },
        )
        stage = issue_row(
            ids["stage_task"],
            ids["stage"],
            f"Stage slot 1: {task['title']}",
            "blocked",
            [ids["source_task"]],
            {
                "executionWorkspaceId": delivery.PROCESS_WORKSPACE_IDS["stage"],
                "workspaceSelection": {
                    "version": 1,
                    "source": "explicit",
                    "selection": {
                        "kind": "existing",
                        "workspaceId": (
                            self.ids["stage"]
                            if bad_stage_selection
                            else delivery.PROCESS_WORKSPACE_IDS["stage"]
                        ),
                    },
                },
                "assigneeAdapterOverrides": delivery._expected_process_override(
                    "stage", manifest_path
                ),
            },
        )
        selection = {
            "kind": "configured_source",
            "projectWorkspaceId": ids["qa_workspace"],
            "mode": "managed_isolated" if bad_qa_selection else "shared",
        }
        qa = issue_row(
            ids["qa_task"],
            ids["qa"],
            f"Independent QA slot 1: {task['title']}",
            "blocked",
            [ids["stage_task"]],
            {
                "projectId": ids["qa_project"],
                "projectWorkspaceId": ids["qa_workspace"],
                "workspaceSelection": {"version": 1, "source": "explicit", "selection": selection},
                "assigneeAdapterOverrides": {
                    "adapterConfig": {"cwd": str(qa_path)},
                    "useProjectWorkspace": False,
                },
                "description": self.acceptance,
            },
        )
        publish = issue_row(
            ids["publish_task"],
            ids["publish"],
            f"Publish slot 1: {task['title']}",
            "backlog",
            [ids["qa_task"]],
            {
                "executionWorkspaceId": delivery.PROCESS_WORKSPACE_IDS["publish"],
                "workspaceSelection": {
                    "version": 1,
                    "source": "explicit",
                    "selection": {
                        "kind": "existing",
                        "workspaceId": delivery.PROCESS_WORKSPACE_IDS["publish"],
                    },
                },
                "assigneeAdapterOverrides": delivery._expected_process_override(
                    "publish", manifest_path
                ),
            },
        )
        rows = {row["id"]: row for row in (source, stage, qa, publish)}
        source_workspace = {
            "id": ids["source_workspace"],
            "companyId": ids["company"],
            "projectId": ids["source_project"],
            "sourceType": "local_path",
            "cwd": str(self.seed),
            "repoRef": self.base,
            "defaultRef": self.base,
            "repoUrl": None,
            "isPrimary": True,
        }
        qa_workspace = {
            "id": ids["qa_workspace"],
            "companyId": ids["company"],
            "projectId": ids["qa_project"],
            "sourceType": "local_path",
            "cwd": str(qa_path),
            "repoRef": self.base,
            "defaultRef": self.base,
            "repoUrl": None,
            "isPrimary": True,
        }

        class FakeApi:
            completed = []
            m = {"company_id": ids["company"]}

            def current(_self, _action):
                return ids["prepare"]

            def issue(_self, task_id):
                if task_id in rows:
                    return rows[task_id]
                if task_id == ids["planning"]:
                    return {
                        "id": task_id,
                        "companyId": ids["company"],
                        "assigneeAgentId": ids["planning_agent"],
                        "status": "done",
                    }
                if task_id == ids["manager"]:
                    return {
                        "id": task_id,
                        "companyId": ids["company"],
                        "assigneeAgentId": ids["manager_agent"],
                        "status": (
                            "todo"
                            if ids["prepare"] in getattr(_self, "completed", [])
                            else manager_status
                        ),
                        "blockedBy": [
                            {"id": blocker}
                            for blocker in (
                                []
                                if ids["prepare"] in getattr(_self, "completed", [])
                                else manager_blockers
                                if manager_blockers is not None
                                else [ids["prepare"]]
                            )
                        ],
                    }
                raise AssertionError(task_id)

            def no_pending(_self, _task_id):
                pass

            def latest_run_for_task(_self, _binding, _model):
                return {"runId": ids["run"]}

            def artifact(_self, _binding, _run, _name, _limit):
                return ({"id": ids["attachment"]}, plan_bytes)

            def complete(_self, task_id):
                _self.completed.append(task_id)

            def request(_self, _method, route):
                if route.startswith("/companies/"):
                    return [{"id": key} for key in rows]
                if route == f"/projects/{ids['source_project']}":
                    return {
                        "id": ids["source_project"],
                        "companyId": ids["company"],
                        "executionWorkspacePolicy": {
                            "enabled": True,
                            "workspaceStrategy": {"type": "git_worktree", "baseRef": self.base},
                        },
                    }
                if route == f"/projects/{ids['source_project']}/workspaces":
                    return [source_workspace]
                if route == f"/projects/{ids['qa_project']}/workspaces":
                    return [qa_workspace]
                raise AssertionError(route)

        return FakeApi()

    def test_read_only_current_accepts_heartbeat_binding_without_checkout_and_rejects_foreign(self):
        company_id = self.ids["company"]
        task_id = self.ids["prepare"]
        agent_id = self.ids["prepare_agent"]
        run_id = self.ids["run"]

        def make_api(checkout_run_id: str | None):
            api = delivery.Paperclip.__new__(delivery.Paperclip)
            api.m = {
                "company_id": company_id,
                "prepare": {"task_id": task_id, "agent_id": agent_id},
            }
            api.agent_id = agent_id
            api.run_id = run_id
            requests = []

            def request(method: str, route: str, body: Any = None) -> Any:
                requests.append((method, route, body))
                if method != "GET":
                    raise AssertionError((method, route, body))
                if route == "/agents/me":
                    return {"id": agent_id, "companyId": company_id}
                if route == f"/heartbeat-runs/{run_id}":
                    return {
                        "id": run_id,
                        "agentId": agent_id,
                        "companyId": company_id,
                        "runtimeMode": "legacy",
                        "status": "running",
                        "contextSnapshot": {"issueId": task_id},
                    }
                if route == f"/issues/{task_id}":
                    return {
                        "id": task_id,
                        "companyId": company_id,
                        "assigneeAgentId": agent_id,
                        "status": "todo",
                        "executionRunId": run_id,
                        "checkoutRunId": checkout_run_id,
                    }
                if route == f"/issues/{task_id}/interactions":
                    return []
                raise AssertionError(route)

            return api, requests, request

        api, requests, request = make_api(None)
        with patch.object(api, "request", side_effect=request):
            self.assertEqual(api.current("prepare", checkout_if_missing=False), task_id)
        self.assertTrue(all(method == "GET" for method, _route, _body in requests))

        foreign_api, _requests, foreign_request = make_api("00000000-0000-4000-8000-000000009999")
        with patch.object(foreign_api, "request", side_effect=foreign_request):
            with self.assertRaisesRegex(delivery.DeliveryError, "current_task_execution_invalid"):
                foreign_api.current("prepare", checkout_if_missing=False)

    def test_prepare_clones_qa_and_writes_bound_manifest_and_receipt(self) -> None:
        api = self.fake_api()
        with patch.object(delivery, "Paperclip", return_value=api):
            result = delivery.prepare(str(self.envelope_path))
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(api.completed, [self.ids["prepare"]])
        self.assertEqual(api.issue(self.ids["manager"])["status"], "todo")
        self.assertEqual(api.issue(self.ids["manager"])["blockedBy"], [])
        receipt = json.loads(Path(result["receipt"]).read_bytes())
        self.assertEqual(receipt["planning_task_id"], self.ids["planning"])
        manifest_path = self.output / "slot1" / "manifest.json"
        manifest, _ = delivery.load_manifest(str(manifest_path))
        self.assertEqual(manifest["role_models"], {"developer": "gpt-6-luna", "qa": "gpt-6.1-sol"})
        self.assertNotIn("max_corrections", manifest)
        self.assertEqual(git(Path(manifest["qa_workspace"]), "remote", "-v"), b"")

    def test_prepare_pins_owner_configured_correction_limit(self) -> None:
        self.envelope["max_corrections"] = 3
        self.envelope_path.write_bytes(delivery.json_bytes(self.envelope))
        api = self.fake_api()
        with patch.object(delivery, "Paperclip", return_value=api):
            delivery.prepare(str(self.envelope_path))
        manifest_path = self.output / "slot1" / "manifest.json"
        manifest, _ = delivery.load_manifest(str(manifest_path))
        self.assertEqual(manifest["max_corrections"], 3)
        original = manifest_path.read_bytes()
        for invalid in (True, 0, 4):
            with self.subTest(manifest_max_corrections=invalid):
                manifest_path.chmod(0o600)
                manifest_path.write_bytes(
                    delivery.json_bytes({**manifest, "max_corrections": invalid})
                )
                with self.assertRaisesRegex(
                    delivery.DeliveryError, "manifest_max_corrections_invalid"
                ):
                    delivery.load_manifest(str(manifest_path))
        manifest_path.write_bytes(original)
        manifest_path.chmod(0o600)

    def test_prepare_delivery_error_wakes_only_pinned_planning_task_idempotently(self) -> None:
        ids = self.ids

        class FailureApi:
            agent_id = ids["prepare_agent"]
            run_id = ids["run"]
            m = {"company_id": ids["company"]}

            def __init__(self) -> None:
                self.comments: list[dict[str, str]] = []
                self.posts: list[tuple[str, dict[str, Any]]] = []
                self.current_calls: list[tuple[str, bool]] = []

            def current(self, action: str, *, checkout_if_missing: bool = True) -> str:
                self.current_calls.append((action, checkout_if_missing))
                return ids["prepare"]

            def issue(self, task_id: str) -> dict[str, Any]:
                if task_id == ids["planning"]:
                    return {
                        "id": task_id,
                        "companyId": ids["company"],
                        "assigneeAgentId": ids["planning_agent"],
                        "status": "done",
                    }
                raise AssertionError(task_id)

            def no_pending(self, _task_id: str) -> None:
                return None

            def request(
                self, method: str, route: str, body: dict[str, Any] | None = None
            ) -> Any:
                if method == "GET" and route.startswith(f"/issues/{ids['planning']}/comments?"):
                    return list(self.comments)
                if method == "POST" and route == f"/issues/{ids['planning']}/comments":
                    self.posts.append((route, body or {}))
                    self.comments.append(
                        {
                            "id": f"comment-{len(self.comments) + 1}",
                            "authorAgentId": self.agent_id,
                            "body": (body or {})["body"],
                        }
                    )
                    return {}
                raise AssertionError((method, route, body))

        api = FailureApi()
        error_output = io.StringIO()
        with (
            patch.object(
                delivery,
                "prepare",
                side_effect=delivery.DeliveryError("preparation_seed_not_clean_frozen_base"),
            ),
            patch.object(delivery, "Paperclip", return_value=api),
            redirect_stderr(error_output),
        ):
            self.assertEqual(delivery.main(["prepare", "--envelope", str(self.envelope_path)]), 2)
            self.assertEqual(delivery.main(["prepare", "--envelope", str(self.envelope_path)]), 2)

        self.assertEqual(api.current_calls, [("prepare", False), ("prepare", False)])
        self.assertEqual(len(api.posts), 1)
        route, body = api.posts[0]
        self.assertEqual(route, f"/issues/{ids['planning']}/comments")
        self.assertTrue(body["resume"])
        self.assertIn("preparation_seed_not_clean_frozen_base", body["body"])
        self.assertIn(self.envelope["prepare"]["task_id"], body["body"])
        self.assertEqual(error_output.getvalue().count("preparation_seed_not_clean_frozen_base"), 2)

    def test_prepare_failure_notification_rejects_wrong_run_and_preserves_original_error(
        self,
    ) -> None:
        ids = self.ids

        class WrongRunApi:
            def __init__(self) -> None:
                self.m = {"company_id": ids["company"]}
                self.agent_id = ids["prepare_agent"]
                self.run_id = ids["run"]
                self.posts = 0

            def current(self, action: str, *, checkout_if_missing: bool = True) -> str:
                return "00000000-0000-4000-8000-000000009999"

        api = WrongRunApi()
        error_output = io.StringIO()
        with (
            patch.object(
                delivery,
                "prepare",
                side_effect=delivery.DeliveryError("preparation_seed_not_clean_frozen_base"),
            ),
            patch.object(delivery, "Paperclip", return_value=api),
            redirect_stderr(error_output),
        ):
            self.assertEqual(delivery.main(["prepare", "--envelope", str(self.envelope_path)]), 2)

        self.assertEqual(api.posts, 0)
        self.assertIn('"reason": "preparation_seed_not_clean_frozen_base"', error_output.getvalue())

    def test_prepare_failure_notification_ignores_invalid_envelope(self) -> None:
        self.envelope_path.write_text("{}\n")
        error_output = io.StringIO()
        with (
            patch.object(
                delivery,
                "prepare",
                side_effect=delivery.DeliveryError("preparation_seed_not_clean_frozen_base"),
            ),
            patch.object(delivery, "Paperclip", side_effect=AssertionError("must not construct")),
            redirect_stderr(error_output),
        ):
            self.assertEqual(delivery.main(["prepare", "--envelope", str(self.envelope_path)]), 2)

        self.assertIn('"reason": "preparation_seed_not_clean_frozen_base"', error_output.getvalue())

    def test_prepare_freezes_resolved_predecessor_in_manifest_and_receipt(self) -> None:
        initial_tip = self.envelope["canonical"]["expected_tip"]
        prior_dir = self.root / "prior"
        prior_dir.mkdir(mode=0o700)
        prior_custody = prior_dir / "custody"
        prior_custody.mkdir(mode=0o700)
        prior_qa = prior_dir / "qa"
        prior_qa.mkdir(mode=0o700)
        prior_source_task = "00000000-0000-4000-8000-000000000201"
        prior_publish_task = "00000000-0000-4000-8000-000000000202"
        prior_publish_agent = "00000000-0000-4000-8000-000000000203"
        prior_run = "00000000-0000-4000-8000-000000000204"
        prior_files = {
            "src/prior.py": b"PRIOR = 1\n",
            "tests/test_prior.py": b"assert PRIOR == 1\n",
        }
        prior_hashes = {
            path: delivery.publisher._git(self.canonical, "hash-object", "-w", "--stdin", data=data)
            .decode()
            .strip()
            for path, data in prior_files.items()
        }
        prior_candidate_receipt = delivery.publisher.prepare(
            self.canonical,
            delivery.PUBLISH_REF,
            initial_tip,
            {path: None for path in prior_files},
            prior_hashes,
            prior_files,
            allowed_paths=list(prior_files),
        )
        resolved_tip = prior_candidate_receipt["candidate"]
        git(self.canonical, "symbolic-ref", "HEAD", delivery.PUBLISH_REF)
        delivery.publisher.reconcile(
            self.canonical,
            delivery.PUBLISH_REF,
            prior_candidate_receipt,
            resolved_tip,
            prior_candidate_receipt["receipt_digest"],
            allowed_paths=list(prior_files),
        )
        prior_candidate_ref = "refs/heads/brain/previous-slot"
        prior_manifest = {
            "schema": delivery.MANIFEST_SCHEMA,
            "api_origin": "http://127.0.0.1:3310",
            "company_id": self.ids["company"],
            "developer": {
                "task_id": prior_source_task,
                "agent_id": "00000000-0000-4000-8000-000000000205",
            },
            "qa": {
                "task_id": "00000000-0000-4000-8000-000000000206",
                "agent_id": "00000000-0000-4000-8000-000000000207",
            },
            "stage": {
                "task_id": "00000000-0000-4000-8000-000000000208",
                "agent_id": "00000000-0000-4000-8000-000000000209",
            },
            "publish": {"task_id": prior_publish_task, "agent_id": prior_publish_agent},
            "source": {
                "seed_repo": str(self.seed),
                "base_sha": self.base,
                "paths": list(prior_files),
            },
            "canonical": {
                **self.envelope["canonical"],
                "expected_tip": initial_tip,
                "candidate_ref": prior_candidate_ref,
            },
            "qa_workspace": str(prior_qa),
            "custody_dir": str(prior_custody),
        }
        prior_manifest_path = prior_dir / "manifest.json"
        delivery.write_once(prior_manifest_path, delivery.json_bytes(prior_manifest), 0o600)
        prior_manifest_hash = delivery.digest(prior_manifest_path.read_bytes())
        prior_publication = {
            "schema": delivery.PUBLISH_SCHEMA,
            "manifest_sha256": prior_manifest_hash,
            "source_task_id": prior_source_task,
            "expected_tip": initial_tip,
            "canonical_ref": delivery.PUBLISH_REF,
            "candidate_ref": prior_candidate_ref,
            "files": {path: hashlib.sha256(data).hexdigest() for path, data in prior_files.items()},
            "canonical_receipt": prior_candidate_receipt,
        }
        prior_publication["publication_digest"] = delivery.digest(
            delivery.json_bytes(prior_publication)
        )
        prior_publication_path = prior_custody / f"publication-{prior_source_task}.json"
        delivery.save_record(prior_publication_path, prior_publication)
        prior_prepare_receipt_path = prior_dir / "prepare-receipt.json"
        delivery.save_record(
            prior_prepare_receipt_path,
            {
                "schema": delivery.PREPARATION_RECEIPT_SCHEMA,
                "envelope_sha256": "b" * 64,
                "company_id": self.ids["company"],
                "canonical_ref": delivery.PUBLISH_REF,
                "canonical_expected_tip": initial_tip,
                "canonical_base_tip": initial_tip,
                "slots": [
                    {
                        "slot": 1,
                        "source_task_id": prior_source_task,
                        "publish_task_id": prior_publish_task,
                        "manifest_path": str(prior_manifest_path),
                        "manifest_sha256": prior_manifest_hash,
                    }
                ],
            },
        )
        self.envelope["predecessor_candidates"] = [
            {
                "preparation_receipt_path": str(prior_prepare_receipt_path),
                "preparation_envelope_sha256": "b" * 64,
                "slot": 1,
            }
        ]
        self.envelope_path.write_bytes(delivery.json_bytes(self.envelope))
        api = self.fake_api()
        original_issue = api.issue
        api.issue = lambda task_id: (
            {
                "id": prior_publish_task,
                "companyId": self.ids["company"],
                "assigneeAgentId": prior_publish_agent,
                "status": "done",
            }
            if task_id == prior_publish_task
            else original_issue(task_id)
        )
        api.no_pending_terminal = lambda _task_id: None
        original_request = api.request

        def request(method, route):
            if route == f"/issues/{prior_publish_task}/runs":
                return [
                    {
                        "status": "succeeded",
                        "runId": prior_run,
                        "agentId": prior_publish_agent,
                        "contextIssueId": prior_publish_task,
                    }
                ]
            if route == f"/heartbeat-runs/{prior_run}":
                return {
                    "id": prior_run,
                    "status": "succeeded",
                    "runtimeMode": "legacy",
                    "agentId": prior_publish_agent,
                    "companyId": self.ids["company"],
                    "issueId": prior_publish_task,
                }
            return original_request(method, route)

        api.request = request

        def remote_refs(_repo, *_args, **_kwargs):
            return (
                f"{resolved_tip}\t{delivery.PUBLISH_REF}\n{resolved_tip}\t{prior_candidate_ref}\n"
            ).encode()

        with (
            patch.object(delivery, "Paperclip", return_value=api),
            patch.object(delivery, "remote_git", side_effect=remote_refs),
        ):
            result = delivery.prepare(str(self.envelope_path))
        manifest, _ = delivery.load_manifest(str(self.output / "slot1" / "manifest.json"))
        manifest_hash = delivery.digest((self.output / "slot1" / "manifest.json").read_bytes())
        receipt = json.loads(Path(result["receipt"]).read_bytes())
        self.assertEqual(manifest["canonical"]["expected_tip"], resolved_tip)
        self.assertEqual(manifest["predecessor_base_tip"], initial_tip)
        self.assertEqual(manifest["lineage_depth"], 1)
        self.assertEqual(manifest["predecessor"]["manifest_sha256"], prior_manifest_hash)
        self.assertEqual(receipt["canonical_expected_tip"], resolved_tip)
        self.assertEqual(receipt["canonical_base_tip"], initial_tip)
        self.assertEqual(receipt["slots"][0]["lineage_depth"], 1)
        self.assertEqual(receipt["predecessor"], manifest["predecessor"])
        with patch.object(delivery, "remote_git", side_effect=remote_refs):
            self.assertEqual(
                delivery.predecessor_expected_tip(manifest, manifest_hash, api), resolved_tip
            )
            self.assertEqual(
                delivery.publication_expected_tip(manifest, manifest_hash, api), resolved_tip
            )

    def test_internal_slot_publish_base_keeps_legacy_expected_tip_behavior(self) -> None:
        api = object()
        internal_slot = {
            "canonical": {"expected_tip": "e" * 40},
            "predecessor": {},
            "lineage_depth": 1,
        }
        with patch.object(delivery, "predecessor_expected_tip", return_value="c" * 40):
            self.assertEqual(
                delivery.publication_expected_tip(internal_slot, "a" * 64, api), "c" * 40
            )

        external_slot = {
            "canonical": {"expected_tip": "c" * 40},
            "predecessor": {},
            "predecessor_base_tip": "e" * 40,
            "lineage_depth": 1,
        }
        with patch.object(delivery, "predecessor_expected_tip", return_value="d" * 40):
            with self.assertRaisesRegex(delivery.DeliveryError, "predecessor_expected_tip_changed"):
                delivery.publication_expected_tip(external_slot, "a" * 64, api)

    def test_preparation_envelope_bounds_and_deduplicates_predecessor_candidates(self) -> None:
        candidate = {
            "preparation_receipt_path": str(self.root / "prior" / "prepare-receipt.json"),
            "preparation_envelope_sha256": "b" * 64,
            "slot": 1,
        }
        value = {**self.envelope, "predecessor_candidates": [candidate]}
        self.assertEqual(
            delivery.validate_preparation_envelope(value)["predecessor_candidates"], [candidate]
        )
        for invalid in (
            [],
            [candidate, candidate],
            [candidate] * 5,
            [{**candidate, "slot": True}],
            [{**candidate, "slot": 2}],
            [{**candidate, "extra": "dynamic policy"}],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(delivery.DeliveryError):
                    delivery.validate_preparation_envelope(
                        {**self.envelope, "predecessor_candidates": invalid}
                    )

    def test_preparation_envelope_accepts_hash_bound_v2_renewal_for_v1_reference(self) -> None:
        reference, renewal_path = write_v1_policy_v2_renewal(
            self.root, self.ids["company"]
        )
        envelope = {**self.envelope, "resource_policy": reference}
        with patch.dict(os.environ, {delivery.resource_guard.RENEWAL_ENV: str(renewal_path)}):
            validated = delivery.validate_preparation_envelope(envelope)
        self.assertEqual(validated["resource_policy"], reference)

    def test_preparation_correction_limit_is_optional_and_strictly_bounded(self) -> None:
        self.assertNotIn("max_corrections", delivery.validate_preparation_envelope(self.envelope))
        for value in (True, False, 0, -1, 4, "2"):
            with self.subTest(max_corrections=value):
                with self.assertRaisesRegex(
                    delivery.DeliveryError, "preparation_max_corrections_invalid"
                ):
                    delivery.validate_preparation_envelope(
                        {**self.envelope, "max_corrections": value}
                    )
        self.assertEqual(
            delivery.validate_preparation_envelope({**self.envelope, "max_corrections": 3})[
                "max_corrections"
            ],
            3,
        )
        second_slot = {
            "slot": 2,
            "paths": ["src/second.py", "tests/test_second.py"],
            "acceptance_sha256": "d" * 64,
            "qa_project_id": "00000000-0000-4000-8000-000000000901",
            "candidate_ref": "refs/heads/brain/candidate-slot-2",
        }
        two_slot = delivery.validate_preparation_envelope(
            {**self.envelope, "max_corrections": 3, "slots": [*self.envelope["slots"], second_slot]}
        )
        self.assertEqual(len(two_slot["slots"]), 2)

    def test_preparation_predecessor_selects_only_unique_current_publication(self) -> None:
        bindings = []
        for number in (1, 2):
            prior_dir = self.root / f"prior-{number}"
            prior_dir.mkdir(mode=0o700)
            custody = prior_dir / "custody"
            custody.mkdir(mode=0o700)
            manifest_path = prior_dir / "manifest.json"
            preparation_receipt_path = prior_dir / "prepare-receipt.json"
            prior_receipt = {
                "schema": delivery.PREPARATION_RECEIPT_SCHEMA,
                "envelope_sha256": str(number) * 64,
                "company_id": self.ids["company"],
                "source_base_sha": self.base,
                "canonical_ref": delivery.PUBLISH_REF,
                "canonical_expected_tip": self.envelope["canonical"]["expected_tip"],
                "slots": [
                    {
                        "slot": 1,
                        "source_task_id": self.ids["source_task"],
                        "publish_task_id": self.ids["publish_task"],
                        "manifest_path": str(manifest_path),
                        "manifest_sha256": str(number + 2) * 64,
                    }
                ],
            }
            delivery.save_record(preparation_receipt_path, prior_receipt)
            bindings.append(
                {
                    "preparation_receipt_path": str(preparation_receipt_path),
                    "preparation_envelope_sha256": prior_receipt["envelope_sha256"],
                    "slot": 1,
                }
            )
        bindings.append(
            {
                "preparation_receipt_path": str(
                    self.root / "not-yet-prepared" / "prepare-receipt.json"
                ),
                "preparation_envelope_sha256": "5" * 64,
                "slot": 1,
            }
        )
        parent = {
            "custody_dir": str(self.root / "unused-custody"),
            "canonical": {
                "repo": self.envelope["canonical"]["repo"],
                "ref": delivery.PUBLISH_REF,
                "expected_tip": self.envelope["canonical"]["expected_tip"],
                "remote_name": "origin",
                "remote_url": "https://github.com/brain/example.git",
                "candidate_ref": "refs/heads/brain/previous",
            },
            "developer": {"task_id": self.ids["source_task"]},
            "publish": {"task_id": self.ids["publish_task"]},
        }
        hashes = {"1": "3" * 64, "2": "4" * 64}

        def load(filename):
            return parent, hashes[Path(filename).parent.name[-1]]

        def predecessor(m, _manifest_hash, _api):
            if m["predecessor"]["manifest_sha256"] == hashes["1"]:
                raise delivery.DeliveryError("predecessor_remote_head_unexpected")
            return "c" * 40

        plan = [
            {
                "source_task_id": self.ids["source_task"],
                "paths": self.envelope["slots"][0]["paths"],
            }
        ]
        with (
            patch.object(delivery, "load_manifest", side_effect=load),
            patch.object(delivery, "predecessor_expected_tip", side_effect=predecessor) as verify,
        ):
            selected = delivery._resolve_preparation_predecessor(
                self.fake_api(), {**self.envelope, "predecessor_candidates": bindings}, plan
            )
        self.assertEqual(selected["expected_tip"], "c" * 40)
        self.assertEqual(selected["binding"]["manifest_sha256"], hashes["2"])
        self.assertEqual(selected["base_tip"], self.envelope["canonical"]["expected_tip"])
        self.assertEqual(verify.call_count, 2)
        with (
            patch.object(delivery, "load_manifest", side_effect=load),
            patch.object(
                delivery,
                "predecessor_expected_tip",
                side_effect=delivery.DeliveryError("predecessor_remote_head_unexpected"),
            ),
        ):
            with self.assertRaisesRegex(
                delivery.DeliveryError, "preparation_predecessor_not_unique"
            ):
                delivery._resolve_preparation_predecessor(
                    self.fake_api(), {**self.envelope, "predecessor_candidates": bindings}, plan
                )
        with (
            patch.object(delivery, "load_manifest", side_effect=load),
            patch.object(delivery, "predecessor_expected_tip", return_value="c" * 40),
        ):
            with self.assertRaisesRegex(
                delivery.DeliveryError, "preparation_predecessor_not_unique"
            ):
                delivery._resolve_preparation_predecessor(
                    self.fake_api(), {**self.envelope, "predecessor_candidates": bindings}, plan
                )

    def test_preparation_predecessor_missing_options_are_skipped_but_not_selected(self) -> None:
        binding = {
            "preparation_receipt_path": str(self.root / "not-prepared" / "prepare-receipt.json"),
            "preparation_envelope_sha256": "6" * 64,
            "slot": 1,
        }
        envelope = {**self.envelope, "predecessor_candidates": [binding]}
        plan = [
            {
                "source_task_id": self.ids["source_task"],
                "paths": self.envelope["slots"][0]["paths"],
            }
        ]
        with self.assertRaisesRegex(delivery.DeliveryError, "preparation_predecessor_not_unique"):
            delivery._resolve_preparation_predecessor(self.fake_api(), envelope, plan)

    def test_preparation_predecessor_corrupt_or_symlink_receipt_fails_closed(self) -> None:
        prior = self.root / "bad-prior"
        prior.mkdir(mode=0o700)
        corrupt_path = prior / "corrupt.json"
        corrupt_path.write_bytes(b"not-json\n")
        corrupt_path.chmod(0o400)
        link_path = prior / "linked.json"
        link_path.symlink_to(corrupt_path)
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(prior, target_is_directory=True)
        missing_under_link = linked_parent / "missing.json"
        plan = [
            {
                "source_task_id": self.ids["source_task"],
                "paths": self.envelope["slots"][0]["paths"],
            }
        ]
        for filename, reason in (
            (corrupt_path, "preparation_predecessor_receipt_invalid"),
            (link_path, "preparation_predecessor_receipt_not_private"),
            (missing_under_link, "preparation_predecessor_receipt_parent_symlink"),
        ):
            envelope = {
                **self.envelope,
                "predecessor_candidates": [
                    {
                        "preparation_receipt_path": str(filename),
                        "preparation_envelope_sha256": "7" * 64,
                        "slot": 1,
                    }
                ],
            }
            with (
                self.subTest(path=filename),
                self.assertRaisesRegex(delivery.DeliveryError, reason),
            ):
                delivery._resolve_preparation_predecessor(self.fake_api(), envelope, plan)
        denied_path = prior / "denied.json"
        delivery.save_record(denied_path, {"placeholder": "must not be read"})
        denied_envelope = {
            **self.envelope,
            "predecessor_candidates": [
                {
                    "preparation_receipt_path": str(denied_path),
                    "preparation_envelope_sha256": "8" * 64,
                    "slot": 1,
                }
            ],
        }
        original_lstat = Path.lstat

        def permission_denied(path):
            if path == denied_path:
                raise PermissionError("read denied")
            return original_lstat(path)

        with (
            patch.object(Path, "lstat", permission_denied),
            self.assertRaisesRegex(
                delivery.DeliveryError, "preparation_predecessor_receipt_missing"
            ),
        ):
            delivery._resolve_preparation_predecessor(self.fake_api(), denied_envelope, plan)

    def test_prepare_graph_binding_mismatch_fails_before_output_writes(self) -> None:
        api = self.fake_api(bad_qa_selection=True)
        with patch.object(delivery, "Paperclip", return_value=api):
            with self.assertRaisesRegex(delivery.DeliveryError, "preparation_qa_binding_mismatch"):
                delivery.prepare(str(self.envelope_path))
        self.assertFalse(self.output.exists())
        self.assertEqual(api.completed, [])

    def test_prepare_requires_native_manager_dependency_before_activation(self) -> None:
        cases = (
            ("backlog", [self.ids["prepare"]]),
            ("blocked", []),
            ("blocked", [self.ids["planning"]]),
        )
        for status, blockers in cases:
            with self.subTest(status=status, blockers=blockers):
                api = self.fake_api(manager_status=status, manager_blockers=blockers)
                with patch.object(delivery, "Paperclip", return_value=api):
                    with self.assertRaisesRegex(
                        delivery.DeliveryError, "preparation_manager_issue_mismatch"
                    ):
                        delivery.prepare(str(self.envelope_path))
                self.assertFalse(self.output.exists())
                self.assertEqual(api.completed, [])

    def test_process_actions_require_initial_existing_workspace_bindings(self) -> None:
        api = self.fake_api(bad_stage_selection=True)
        with patch.object(delivery, "Paperclip", return_value=api):
            with self.assertRaisesRegex(
                delivery.DeliveryError, "preparation_stage_binding_mismatch"
            ):
                delivery.prepare(str(self.envelope_path))
        self.assertFalse(self.output.exists())
        self.assertEqual(api.completed, [])

    def test_prepare_replay_rejects_existing_output_root(self) -> None:
        self.output.mkdir(mode=0o700)
        with self.assertRaisesRegex(delivery.DeliveryError, "preparation_output_exists"):
            delivery.validate_preparation_envelope(self.envelope)

    def test_cli_dispatches_prepare_with_private_envelope_argument(self) -> None:
        with (
            patch.object(delivery, "prepare", return_value={"status": "prepared"}) as prepare,
            patch("sys.stdout"),
        ):
            self.assertEqual(delivery.main(["prepare", "--envelope", str(self.envelope_path)]), 0)
        prepare.assert_called_once_with(str(self.envelope_path))


if __name__ == "__main__":
    unittest.main()
