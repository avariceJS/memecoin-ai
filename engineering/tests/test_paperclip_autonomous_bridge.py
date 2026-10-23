import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from engineering.git_gate import Candidate
from engineering.paperclip_autonomous_bridge import (
    BridgeConfig,
    BridgeError,
    _binding,
    _bounded_candidate_diff,
    _verifier,
    dispatch,
)


class FakeBroker:
    def __init__(self, candidate, journal_dir):
        self.candidate = candidate
        self.journal_dir = journal_dir

    def prepare(self, binding, _checkout):
        record = {
            "schema": "paperclip-git-operation-v1",
            "operation_key": binding.operation_key,
            "binding": binding.as_dict(),
            "state": "prepared",
            "candidate": {
                "submitted_sha": self.candidate.submitted_sha,
                "base_sha": self.candidate.base_sha,
                "integration_tip": "a" * 40,
                "sha": self.candidate.sha,
                "checkout": str(self.candidate.checkout),
                "changed_paths": list(self.candidate.changed_paths),
            },
        }
        path = self.journal_dir / f"{binding.operation_key}.json"
        path.write_text(json.dumps(record))
        path.chmod(0o600)
        return self.candidate

    def _check_candidate(self, _record, _candidate):
        return None

    def recover_candidate(self, binding):
        path = self.journal_dir / f"{binding.operation_key}.json"
        record = json.loads(path.read_text())
        return record, self.candidate

    def reject(self, binding, candidate, approval):
        return SimpleNamespace(
            status="review_rejected",
            operation_key=binding.operation_key,
            candidate_sha=candidate.sha,
            evidence_digest=approval.evidence_digest,
        )


class PaperclipAutonomousBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.candidate = SimpleNamespace(
            sha="c" * 40,
            submitted_sha="b" * 40,
            base_sha="a" * 40,
            integration_tip="a" * 40,
            changed_paths=("engineering/example.py",),
            checkout=root / "candidate",
        )
        self.config = BridgeConfig(
            company_id="company-1",
            project_id="project-1",
            worker_agent_id="worker-1",
            reviewer_agent_id="reviewer-1",
            source_issue_id="issue-worker",
            reviewer_issue_id="issue-reviewer",
            source_repo=root / "source",
            worker_checkout=root / "worker",
            publisher_repo=root / "publisher",
            broker_journal_dir=root / "broker-journal",
            bridge_journal_dir=root / "bridge-journal",
            receipt_store=root / "receipts",
            verifier_scratch_root=root / "scratch",
            python_executable=Path("/usr/bin/python3"),
            allowed_scope=("engineering/example.py",),
            verifier_commands=(("/usr/bin/python3", "-c", "pass"),),
            verifier_env=(),
            verifier_policy_id="policy-1",
            fixed_dev_ref="refs/heads/brain/paperclip-integration",
            expected_dev_ref="refs/heads/brain/paperclip-integration",
        )
        self.config.broker_journal_dir.mkdir(mode=0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def worker_payload(self):
        return {
            "company_id": "company-1",
            "project_id": "project-1",
            "source_issue_id": "issue-worker",
            "worker_agent_id": "worker-1",
            "run_id": "worker-run-1",
            "base_sha": "a" * 40,
            "submitted_sha": "b" * 40,
        }

    def reviewer_payload(self, operation_key, **overrides):
        payload = {
            "company_id": "company-1",
            "project_id": "project-1",
            "reviewer_agent_id": "reviewer-1",
            "reviewer_run_id": "reviewer-run-1",
            "reviewer_issue_id": "issue-reviewer",
            "operation_key": operation_key,
            "candidate_sha": "c" * 40,
            "approved": False,
            "evidence": {
                "reviewed_paths": ["engineering/example.py"],
                "reason": "synthetic rejection",
            },
        }
        payload.update(overrides)
        return payload

    def inspect_payload(self, operation_key, **overrides):
        payload = {
            "company_id": "company-1",
            "project_id": "project-1",
            "reviewer_agent_id": "reviewer-1",
            "reviewer_run_id": "reviewer-run-1",
            "reviewer_issue_id": "issue-reviewer",
            "operation_key": operation_key,
            "candidate_sha": "c" * 40,
        }
        payload.update(overrides)
        return payload

    def test_submit_rejects_caller_supplied_identity_or_path_fields(self):
        payload = self.worker_payload()
        payload["worker_checkout"] = "/tmp/attacker"
        with self.assertRaisesRegex(BridgeError, "submit_payload_shape_invalid"):
            dispatch(self.config, {"action": "submit_candidate", "payload": payload})

    def test_submit_rejects_mismatched_worker_or_issue(self):
        for key, value in (("worker_agent_id", "reviewer-1"), ("source_issue_id", "other-issue")):
            payload = self.worker_payload()
            payload[key] = value
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(BridgeError, "actor_or_issue_binding_mismatch"),
            ):
                dispatch(self.config, {"action": "submit_candidate", "payload": payload})

    def test_submit_persists_operation_binding_and_candidate(self):
        with patch.object(
            BridgeConfig,
            "broker",
            return_value=FakeBroker(self.candidate, self.config.broker_journal_dir),
        ):
            result = dispatch(
                self.config, {"action": "submit_candidate", "payload": self.worker_payload()}
            )
        journal = json.loads(
            (
                self.config.bridge_journal_dir / f"operation-{result['operation_key']}.json"
            ).read_text()
        )
        self.assertEqual(journal["binding"]["run_id"], "worker-run-1")
        self.assertEqual(journal["candidate"]["candidate_sha"], "c" * 40)
        self.assertIsNone(journal["review"])

    def test_content_delivery_needs_no_worker_checkout_and_binds_exact_snapshot(self):
        root = Path(self.tmp.name) / "content-lane"
        source = root / "source"
        publisher = root / "publisher"
        root.mkdir()

        def git(repo, *args):
            result = subprocess.run(
                ["/usr/bin/git", "-C", str(repo), *args],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                raise AssertionError(result.stderr)
            return result.stdout.strip()

        source.mkdir()
        subprocess.run(["/usr/bin/git", "init", "-q", "-b", "main", str(source)], check=True)
        for path in (
            "src/memecoin_brain_sources/events.py",
            "tests/brain/test_raw_sources.py",
        ):
            target = source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# owner base\n")
        git(source, "add", "-A")
        git(
            source,
            "-c", "user.name=owner",
            "-c", "user.email=owner@example.invalid",
            "commit", "-m", "base",
        )
        base = git(source, "rev-parse", "HEAD")
        subprocess.run(["/usr/bin/git", "clone", "-q", str(source), str(publisher)], check=True)
        config = replace(
            self.config,
            source_repo=source,
            worker_checkout=None,
            publisher_repo=publisher,
            broker_journal_dir=root / "broker-journal",
            bridge_journal_dir=root / "bridge-journal",
            receipt_store=root / "receipts",
            expected_dev_ref="refs/heads/brain/paperclip-integration",
            fixed_dev_ref="refs/heads/brain/paperclip-integration",
            expected_base_sha=None,
            allowed_scope=("attacker/path.py",),
        )
        bodies = (
            "events = ['synthetic']\n",
            "def test_raw_sources():\n    assert True\n",
        )
        files = [
            {
                "path_id": path,
                "content": body,
                "size": len(body.encode()),
                "sha256": hashlib.sha256(body.encode()).hexdigest(),
            }
            for path, body in zip(
                ("src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"),
                bodies,
                strict=True,
            )
        ]
        payload = {
            "company_id": "company-1",
            "project_id": "project-1",
            "source_issue_id": "issue-worker",
            "worker_agent_id": "worker-1",
            "run_id": "content-run-1",
            "base_sha": base,
            "files": files,
        }
        dev_bound = replace(
            config,
            expected_dev_ref="refs/heads/brain/development",
            fixed_dev_ref="refs/heads/brain/development",
            expected_base_sha=base,
        )
        with self.assertRaisesRegex(BridgeError, "development_base_not_owner_admitted"):
            dispatch(
                dev_bound,
                {"action": "deliver_source_content", "payload": {**payload, "base_sha": "0" * 40}},
            )
        prepared = dispatch(config, {"action": "deliver_source_content", "payload": payload})
        self.assertEqual(
            prepared["changed_paths"],
            ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"],
        )
        journal_path = config.bridge_journal_dir / f"operation-{prepared['operation_key']}.json"
        journal = json.loads(journal_path.read_text())
        self.assertEqual(journal["source_snapshot_sha256"], prepared["source_snapshot_sha256"])
        self.assertEqual(journal["binding"]["scope"], [
            "src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"
        ])
        self.assertNotIn(str(self.config.worker_checkout), journal["binding"]["owner_roots"])
        mutated = {**payload, "files": [dict(item) for item in files]}
        mutated["files"][0]["content"] += "# different\n"
        mutated["files"][0]["size"] = len(mutated["files"][0]["content"].encode())
        mutated["files"][0]["sha256"] = hashlib.sha256(
            mutated["files"][0]["content"].encode()
        ).hexdigest()
        with self.assertRaisesRegex(BridgeError, "source_delivery_replay_content_mismatch"):
            dispatch(config, {"action": "deliver_source_content", "payload": mutated})
        with self.assertRaisesRegex(BridgeError, "source_delivery_replay_binding_mismatch"):
            dispatch(
                config,
                {"action": "deliver_source_content", "payload": {**payload, "base_sha": "f" * 40}},
            )
        reviewer = {
            "company_id": "company-1", "project_id": "project-1",
            "reviewer_agent_id": "reviewer-1", "reviewer_run_id": "review-run-1",
            "reviewer_issue_id": "issue-reviewer", "operation_key": prepared["operation_key"],
            "candidate_sha": prepared["candidate_sha"],
        }
        with self.assertRaisesRegex(BridgeError, "candidate_does_not_match_prepared_journal"):
            dispatch(
                config,
                {"action": "inspect_candidate", "payload": {**reviewer, "candidate_sha": "0" * 40}},
            )
        inspected = dispatch(config, {"action": "inspect_candidate", "payload": reviewer})
        review = dispatch(config, {"action": "approve_candidate", "payload": {
            **reviewer,
            "approved": False,
            "evidence": {
                "inspection_digest": inspected["inspection_digest"],
                "reason": "synthetic decline",
            },
        }})
        self.assertEqual(review["status"], "review_rejected")
        final_journal = json.loads(journal_path.read_text())
        self.assertEqual(final_journal["review"]["candidate_sha"], prepared["candidate_sha"])
        self.assertEqual(final_journal["review"]["reviewer_run_id"], "review-run-1")
        self.assertEqual(
            final_journal["review"]["inspection_digest"], inspected["inspection_digest"]
        )

    def test_review_requires_configured_reviewer_and_exact_candidate(self):
        with patch.object(
            BridgeConfig,
            "broker",
            return_value=FakeBroker(self.candidate, self.config.broker_journal_dir),
        ):
            prepared = dispatch(
                self.config, {"action": "submit_candidate", "payload": self.worker_payload()}
            )
            payload = self.reviewer_payload(prepared["operation_key"], reviewer_agent_id="worker-1")
            with self.assertRaisesRegex(BridgeError, "reviewer_actor_or_issue_binding_mismatch"):
                dispatch(self.config, {"action": "approve_candidate", "payload": payload})
            payload = self.reviewer_payload(prepared["operation_key"], candidate_sha="d" * 40)
            with self.assertRaisesRegex(BridgeError, "candidate_does_not_match_prepared_journal"):
                dispatch(self.config, {"action": "approve_candidate", "payload": payload})

    def test_review_custody_is_exact_and_conflicting_replay_fails_closed(self):
        fake_broker = FakeBroker(self.candidate, self.config.broker_journal_dir)
        with (
            patch.object(BridgeConfig, "broker", return_value=fake_broker),
            patch.object(
                BridgeConfig, "gate", return_value=SimpleNamespace(load_receipt=lambda *_: None)
            ),
            patch(
                "engineering.paperclip_autonomous_bridge._bounded_candidate_diff",
                return_value="diff --git exact candidate",
            ),
        ):
            prepared = dispatch(
                self.config, {"action": "submit_candidate", "payload": self.worker_payload()}
            )
            inspection = dispatch(
                self.config,
                {
                    "action": "inspect_candidate",
                    "payload": self.inspect_payload(prepared["operation_key"]),
                },
            )
            self.assertEqual(inspection["diff"], "diff --git exact candidate")
            payload = self.reviewer_payload(prepared["operation_key"])
            payload["evidence"] = {
                "inspection_digest": inspection["inspection_digest"],
                "reason": "synthetic rejection",
            }
            result = dispatch(self.config, {"action": "approve_candidate", "payload": payload})
            self.assertEqual(result["status"], "review_rejected")
            journal_path = (
                self.config.bridge_journal_dir / f"operation-{prepared['operation_key']}.json"
            )
            journal = json.loads(journal_path.read_text())
            self.assertEqual(journal["review"]["evidence"], payload["evidence"])
            self.assertEqual(journal["review"]["reviewer_run_id"], "reviewer-run-1")
            self.assertEqual(journal["state"], "review_rejected")
            self.assertEqual(
                dispatch(self.config, {"action": "approve_candidate", "payload": payload})[
                    "status"
                ],
                "review_rejected",
            )
            altered = {**payload, "evidence": {"reviewed_paths": []}}
            with self.assertRaisesRegex(BridgeError, "review_evidence_replay_mismatch"):
                dispatch(self.config, {"action": "approve_candidate", "payload": altered})

    def test_approval_requires_same_run_to_inspect_exact_candidate(self):
        fake_broker = FakeBroker(self.candidate, self.config.broker_journal_dir)
        with patch.object(BridgeConfig, "broker", return_value=fake_broker):
            prepared = dispatch(
                self.config, {"action": "submit_candidate", "payload": self.worker_payload()}
            )
            with self.assertRaisesRegex(BridgeError, "reviewer_must_inspect_candidate_first"):
                dispatch(
                    self.config,
                    {
                        "action": "approve_candidate",
                        "payload": self.reviewer_payload(prepared["operation_key"]),
                    },
                )

    def test_inspection_rejects_wrong_reviewer_issue_or_candidate(self):
        fake_broker = FakeBroker(self.candidate, self.config.broker_journal_dir)
        with (
            patch.object(BridgeConfig, "broker", return_value=fake_broker),
            patch.object(
                BridgeConfig, "gate", return_value=SimpleNamespace(load_receipt=lambda *_: None)
            ),
            patch(
                "engineering.paperclip_autonomous_bridge._bounded_candidate_diff",
                return_value="diff",
            ),
        ):
            prepared = dispatch(
                self.config, {"action": "submit_candidate", "payload": self.worker_payload()}
            )
            with self.assertRaisesRegex(BridgeError, "reviewer_actor_or_issue_binding_mismatch"):
                dispatch(
                    self.config,
                    {
                        "action": "inspect_candidate",
                        "payload": self.inspect_payload(
                            prepared["operation_key"], reviewer_issue_id="other"
                        ),
                    },
                )
            with self.assertRaisesRegex(BridgeError, "candidate_does_not_match_prepared_journal"):
                dispatch(
                    self.config,
                    {
                        "action": "inspect_candidate",
                        "payload": self.inspect_payload(
                            prepared["operation_key"], candidate_sha="d" * 40
                        ),
                    },
                )

    def test_inspect_returns_complete_text_diff_and_rejects_binary_candidate(self):
        repo = Path(self.tmp.name) / "diff-repo"
        repo.mkdir()

        def git(*args):
            subprocess.run(
                ["/usr/bin/git", "-C", str(repo), *args], check=True, capture_output=True
            )

        git("init", "-q")
        git("config", "user.name", "Bridge Test")
        git("config", "user.email", "bridge-test@example.invalid")
        (repo / "example.py").write_text("value = 1\n")
        git("add", "example.py")
        git("commit", "-qm", "base")
        tip = subprocess.run(
            ["/usr/bin/git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        (repo / "example.py").write_text("value = 2\n")
        git("commit", "-qam", "candidate")
        sha = subprocess.run(
            ["/usr/bin/git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        candidate = Candidate(sha, tip, tip, sha, repo, ("example.py",))
        diff = _bounded_candidate_diff(candidate)
        self.assertIn("-value = 1", diff)
        self.assertIn("+value = 2", diff)
        (repo / "payload.bin").write_bytes(b"\x00\x01")
        git("add", "payload.bin")
        git("commit", "-qm", "binary candidate")
        binary_sha = subprocess.run(
            ["/usr/bin/git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        with self.assertRaisesRegex(BridgeError, "binary_candidate_diff_not_reviewable"):
            _bounded_candidate_diff(
                Candidate(binary_sha, tip, tip, binary_sha, repo, ("example.py", "payload.bin"))
            )

    @unittest.skipUnless(Path("/usr/bin/sandbox-exec").exists(), "macOS Seatbelt is unavailable")
    def test_integrated_gitgate_submit_inspect_approve_publish_and_exact_replay(self):
        root = Path(self.tmp.name) / "integrated"
        source = root / "source"
        worker = root / "worker"
        publisher = root / "publisher"
        root.mkdir()

        def git(repo, *args):
            result = subprocess.run(
                ["/usr/bin/git", "-C", str(repo), *args],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                raise AssertionError(result.stderr)
            return result.stdout.strip()

        source.mkdir()
        subprocess.run(["/usr/bin/git", "init", "-q", "-b", "main", str(source)], check=True)
        git(source, "config", "user.name", "Bridge Owner")
        git(source, "config", "user.email", "bridge-owner@example.invalid")
        target = source / "src" / "sample.py"
        target.parent.mkdir()
        target.write_text("value = 1\n")
        git(source, "add", "-A")
        git(source, "commit", "-qm", "base")
        base = git(source, "rev-parse", "HEAD")
        git(source, "branch", "brain/development", base)
        subprocess.run(["/usr/bin/git", "clone", "-q", str(source), str(worker)], check=True)
        subprocess.run(["/usr/bin/git", "clone", "-q", str(source), str(publisher)], check=True)
        for repo, name, email in (
            (worker, "Bridge Worker", "bridge-worker@example.invalid"),
            (publisher, "Bridge Publisher", "bridge-publisher@example.invalid"),
        ):
            git(repo, "config", "user.name", name)
            git(repo, "config", "user.email", email)
        git(publisher, "branch", "brain/development", base)
        git(publisher, "checkout", "--detach", base)
        (worker / "src" / "sample.py").write_text("value = 2\n")
        git(worker, "add", "-A")
        git(worker, "commit", "-qm", "candidate")
        submitted = git(worker, "rev-parse", "HEAD")

        python = Path(sys.executable).resolve()
        config = BridgeConfig(
            company_id="company-1",
            project_id="project-1",
            worker_agent_id="worker-1",
            reviewer_agent_id="reviewer-1",
            source_issue_id="issue-worker",
            reviewer_issue_id="issue-reviewer",
            source_repo=source,
            worker_checkout=worker,
            publisher_repo=publisher,
            broker_journal_dir=root / "broker-journal",
            bridge_journal_dir=root / "bridge-journal",
            receipt_store=root / "receipts",
            verifier_scratch_root=root / "scratch",
            python_executable=python,
            allowed_scope=("src/sample.py",),
            verifier_commands=(
                (
                    str(python),
                    "-c",
                    (
                        "from pathlib import Path; "
                        "assert Path('src/sample.py').read_text() == 'value = 2\\n'"
                    ),
                ),
            ),
            verifier_env=(),
            verifier_policy_id="integrated-test",
            fixed_dev_ref="refs/heads/brain/development",
            expected_dev_ref="refs/heads/brain/development",
            expected_base_sha=base,
        )
        worker_payload = {
            "company_id": "company-1",
            "project_id": "project-1",
            "source_issue_id": "issue-worker",
            "worker_agent_id": "worker-1",
            "run_id": "worker-run-integrated",
            "base_sha": base,
            "submitted_sha": submitted,
        }
        submitted_result = dispatch(
            config, {"action": "submit_candidate", "payload": worker_payload}
        )
        reviewer_payload = {
            "company_id": "company-1",
            "project_id": "project-1",
            "reviewer_agent_id": "reviewer-1",
            "reviewer_run_id": "reviewer-run-integrated",
            "reviewer_issue_id": "issue-reviewer",
            "operation_key": submitted_result["operation_key"],
            "candidate_sha": submitted_result["candidate_sha"],
        }
        inspected = dispatch(config, {"action": "inspect_candidate", "payload": reviewer_payload})
        self.assertIn("+value = 2", inspected["diff"])
        review_payload = {
            **reviewer_payload,
            "approved": True,
            "evidence": {
                "inspection_digest": inspected["inspection_digest"],
                "verdict": "accepted",
                "reviewed_paths": ["src/sample.py"],
            },
        }
        published = dispatch(config, {"action": "approve_candidate", "payload": review_payload})
        self.assertEqual(published["status"], "published")
        self.assertEqual(
            git(publisher, "rev-parse", "refs/heads/brain/development"),
            published["candidate_sha"],
        )
        published_inspection = dispatch(
            config, {"action": "inspect_candidate", "payload": reviewer_payload}
        )
        self.assertEqual(
            published_inspection["verifier"]["receipt_digest"],
            published["receipt_digest"],
        )
        self.assertNotIn("checkout", published_inspection)
        self.assertNotIn(str(publisher), str(published_inspection))
        replay = dispatch(config, {"action": "approve_candidate", "payload": review_payload})
        self.assertEqual(replay, published)
        operation = json.loads(
            (
                config.bridge_journal_dir / f"operation-{submitted_result['operation_key']}.json"
            ).read_text()
        )
        self.assertEqual(operation["review"]["evidence"], review_payload["evidence"])
        self.assertEqual(operation["state"], "published")

    def test_unknown_action_and_extra_review_fields_are_rejected(self):
        with self.assertRaisesRegex(BridgeError, "request_action_invalid"):
            dispatch(self.config, {"action": "create_task", "payload": {}})
        with self.assertRaisesRegex(BridgeError, "operation_key_invalid"):
            dispatch(
                self.config, {"action": "approve_candidate", "payload": {"worker_run_id": "forged"}}
            )

    def test_owner_config_digest_and_lane_are_bound_into_operation(self):
        original = _binding(self.config, self.worker_payload())
        development = replace(
            self.config,
            fixed_dev_ref="refs/heads/brain/development",
            expected_dev_ref="refs/heads/brain/development",
            expected_base_sha="a" * 40,
        )
        lane_binding = _binding(development, self.worker_payload())
        moved_root = replace(development, publisher_repo=Path(self.tmp.name) / "other-publisher")
        roots_binding = _binding(moved_root, self.worker_payload())
        executable_alias = replace(
            development, python_executable=Path("/usr/bin/../bin/python3")
        )
        alias_binding = _binding(executable_alias, self.worker_payload())
        self.assertEqual(original.active_ref, "refs/heads/brain/paperclip-integration")
        self.assertEqual(lane_binding.active_ref, "refs/heads/brain/development")
        self.assertNotEqual(original.owner_config_digest, lane_binding.owner_config_digest)
        self.assertNotEqual(lane_binding.owner_roots, roots_binding.owner_roots)
        self.assertNotEqual(lane_binding.owner_config_digest, roots_binding.owner_config_digest)
        self.assertNotEqual(lane_binding.owner_config_digest, alias_binding.owner_config_digest)

    def test_prepared_journal_cannot_replay_under_different_lane(self):
        with patch.object(BridgeConfig, "broker", return_value=FakeBroker(
            self.candidate, self.config.broker_journal_dir
        )):
            result = dispatch(
                self.config,
                {"action": "submit_candidate", "payload": self.worker_payload()},
            )
        development = replace(
            self.config,
            fixed_dev_ref="refs/heads/brain/development",
            expected_dev_ref="refs/heads/brain/development",
            expected_base_sha="a" * 40,
        )
        payload = {
            "company_id": "company-1",
            "project_id": "project-1",
            "reviewer_agent_id": "reviewer-1",
            "reviewer_run_id": "reviewer-run-1",
            "reviewer_issue_id": "issue-reviewer",
            "operation_key": result["operation_key"],
            "candidate_sha": self.candidate.sha,
        }
        with self.assertRaisesRegex(BridgeError, "operation_binding_mismatch"):
            dispatch(development, {"action": "inspect_candidate", "payload": payload})

    def test_development_submit_rejects_unadmitted_worker_base(self):
        development = replace(
            self.config,
            fixed_dev_ref="refs/heads/brain/development",
            expected_dev_ref="refs/heads/brain/development",
            expected_base_sha="b" * 40,
        )
        with self.assertRaisesRegex(BridgeError, "development_base_not_owner_admitted"):
            dispatch(
                development,
                {"action": "submit_candidate", "payload": self.worker_payload()},
            )

    def test_prepared_journal_cannot_replay_after_owner_root_drift(self):
        with patch.object(
            BridgeConfig,
            "broker",
            return_value=FakeBroker(self.candidate, self.config.broker_journal_dir),
        ):
            result = dispatch(
                self.config,
                {"action": "submit_candidate", "payload": self.worker_payload()},
            )
        changed = replace(self.config, publisher_repo=Path(self.tmp.name) / "changed-publisher")
        payload = {
            "company_id": "company-1",
            "project_id": "project-1",
            "reviewer_agent_id": "reviewer-1",
            "reviewer_run_id": "reviewer-run-1",
            "reviewer_issue_id": "issue-reviewer",
            "operation_key": result["operation_key"],
            "candidate_sha": self.candidate.sha,
        }
        with self.assertRaisesRegex(BridgeError, "operation_binding_mismatch"):
            dispatch(changed, {"action": "inspect_candidate", "payload": payload})

    def test_verifier_runtime_root_cannot_grant_publisher_contents(self):
        publisher = Path(self.tmp.name) / "publisher"
        publisher.mkdir()
        (publisher / "private-marker").write_text("publisher-owned\n")
        config = replace(self.config, verifier_runtime_read_roots=(publisher,))
        with patch("engineering.paperclip_autonomous_bridge.python_runtime_roots", return_value=()):
            with self.assertRaisesRegex(
                BridgeError, "verifier_runtime_root_overlaps_publisher"
            ):
                _verifier(config, Path(self.tmp.name) / "candidate")

    def test_owner_config_rejects_loose_permissions_and_non_absolute_paths(self):
        raw = {
            "schema": "paperclip-autonomous-bridge-config-v1",
            "company_id": "company-1",
            "project_id": "project-1",
            "worker_agent_id": "worker-1",
            "reviewer_agent_id": "reviewer-1",
            "source_issue_id": "issue-worker",
            "reviewer_issue_id": "issue-reviewer",
            "source_repo": "/tmp/source",
            "worker_checkout": "/tmp/worker",
            "publisher_repo": "/tmp/publisher",
            "broker_journal_dir": "/tmp/broker",
            "bridge_journal_dir": "/tmp/bridge",
            "receipt_store": "/tmp/receipts",
            "verifier_scratch_root": "/tmp/scratch",
            "python_executable": "/usr/bin/python3",
            "allowed_scope": ["engineering/example.py"],
            "verifier_commands": [["/usr/bin/python3", "-c", "pass"]],
            "verifier_env": {},
            "verifier_policy_id": "policy-1",
            "fixed_dev_ref": "refs/heads/brain/paperclip-integration",
            "expected_dev_ref": "refs/heads/brain/paperclip-integration",
        }
        path = Path(self.tmp.name) / "owner.json"
        path.write_text(json.dumps(raw))
        path.chmod(0o644)
        with self.assertRaisesRegex(BridgeError, "owner_file_permissions_invalid"):
            BridgeConfig.load(path)
        path.chmod(0o600)
        raw["worker_checkout"] = "relative/worker"
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        with self.assertRaisesRegex(BridgeError, "owner_config_paths_must_be_absolute"):
            BridgeConfig.load(path)
        raw["worker_checkout"] = "/tmp/worker"
        raw["verifier_env"] = {"PYTHONDONTWRITEBYTECODE": "1"}
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        with self.assertRaisesRegex(BridgeError, "owner_config_verifier_environment_invalid"):
            BridgeConfig.load(path)
        raw["verifier_env"] = {}
        raw["fixed_dev_ref"] = "refs/heads/brain/development"
        raw["expected_dev_ref"] = "refs/heads/brain/development"
        raw["expected_base_sha"] = "a" * 40
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        self.assertEqual(
            BridgeConfig.load(path).expected_dev_ref, "refs/heads/brain/development"
        )
        raw.pop("worker_checkout")
        raw["fixed_dev_ref"] = "refs/heads/brain/paperclip-integration"
        raw["expected_dev_ref"] = "refs/heads/brain/paperclip-integration"
        raw.pop("expected_base_sha", None)
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        content_only = BridgeConfig.load(path)
        self.assertIsNone(content_only.worker_checkout)
        self.assertNotIn("/tmp/worker", content_only.owner_roots)
        raw["fixed_dev_ref"] = "refs/heads/main"
        raw["expected_dev_ref"] = "refs/heads/main"
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        with self.assertRaisesRegex(BridgeError, "owner_config_development_ref_invalid"):
            BridgeConfig.load(path)

    def test_preparation_failure_propagates_as_failure_without_claiming_candidate(self):
        class FailedBroker:
            def prepare(self, _binding, _checkout):
                raise RuntimeError("candidate import failed")

        with patch.object(BridgeConfig, "broker", return_value=FailedBroker()):
            with self.assertRaisesRegex(RuntimeError, "candidate import failed"):
                dispatch(
                    self.config, {"action": "submit_candidate", "payload": self.worker_payload()}
                )
        self.assertFalse(list(self.config.bridge_journal_dir.glob("operation-*.json")))


if __name__ == "__main__":
    unittest.main()
