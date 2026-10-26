"""Focused checks for the bounded native delivery adapter."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engineering import paperclip_native_delivery as delivery


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

    def test_candidate_attempts_are_initial_plus_one_correction(self) -> None:
        self.assertEqual(delivery.next_stage_revision(None), 1)
        self.assertEqual(delivery.next_stage_revision({"revision": 1}), 2)
        with self.assertRaisesRegex(delivery.DeliveryError, "correction_limit_reached"):
            delivery.next_stage_revision({"revision": 2})

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
            ) -> None:
                self.rows = []
                self.posts = 0
                self.lose_post = lose_post
                self.post_then_error = post_then_error
                self.fail_first_get = fail_first_get
                self.gets = 0

            def request(self, method, route, body=None):
                if method == "GET":
                    self.gets += 1
                    if self.fail_first_get and self.gets == 1:
                        raise delivery.DeliveryError("paperclip_request_failed")
                    return list(self.rows)
                if method == "POST":
                    self.posts += 1
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
        self.assertEqual(
            loaded["canonical"]["candidate_ref"], manifest["canonical"]["candidate_ref"]
        )
        manifest["canonical"]["candidate_ref"] = "refs/heads/main"
        path.write_bytes(delivery.json_bytes(manifest))
        with self.assertRaisesRegex(delivery.DeliveryError, "manifest_canonical_target_invalid"):
            delivery.load_manifest(str(path.resolve()))

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
        api.m = {"company_id": company}
        run_record = {
            "id": run_id,
            "issueId": task_id,
            "nativeIssueId": task_id,
            "agentId": agent_id,
            "companyId": company,
            "runtimeMode": "native",
            "status": "succeeded",
            "runnerProfileJson": {
                "nativeExecutionInput": {
                    "provider": {"kind": "codex", "model": "gpt-6-luna", "reasoningEffort": "high"}
                }
            },
            "usageJson": {"billingType": "subscription_included"},
        }

        def response(_method: str, route: str, **_kwargs):
            if route == f"/issues/{task_id}/runs":
                return [
                    {"id": run_id, "issueId": task_id, "agentId": agent_id, "status": "succeeded"}
                ]
            if route == f"/heartbeat-runs/{run_id}":
                return run_record
            raise AssertionError(route)

        with patch.object(api, "request", side_effect=response):
            run = api.latest_run({"task_id": task_id, "agent_id": agent_id})
        self.assertEqual(run["runId"], run_id)


if __name__ == "__main__":
    unittest.main()
