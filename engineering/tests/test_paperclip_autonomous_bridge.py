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

from engineering.git_gate import Candidate, GitGate, GitGateError
from engineering.paperclip_autonomous_bridge import (
    AcceptedSourceProgramSpec,
    AcceptedSourceProjectionSpec,
    AcceptedSourceReaderSpec,
    BridgeConfig,
    BridgeError,
    SourceStageSpec,
    VerifierProfile,
    _accepted_base_context,
    _accepted_base_context_note,
    _accepted_base_delivery,
    _binding,
    _bounded_candidate_diff,
    _load_journal,
    _stage_journal_key,
    _trusted_stage_source_cache_identity,
    _trusted_stage_source_cache_path,
    _verifier,
    _write_journal,
    dispatch,
)
from engineering.paperclip_git_broker import OperationBinding


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
        self.accepted_base_delivery_patch = patch(
            "engineering.paperclip_autonomous_bridge._accepted_base_delivery",
            return_value={
                "accepted_base_context_note": {
                    "idempotency_key": "a" * 64 + ":accepted-base-context",
                    "body": "synthetic accepted base context note",
                }
            },
        )
        self.accepted_base_delivery_patch.start()
        self.addCleanup(self.accepted_base_delivery_patch.stop)

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

    def test_accepted_source_reader_uses_reviewed_bootstrap_projection_and_binds_actor(self):
        root = Path(self.tmp.name)
        projection_repo = root / "accepted-projection"
        projection_repo.mkdir()
        subprocess.run(["/usr/bin/git", "init", "-q", "-b", "brain/development", str(projection_repo)], check=True)
        source_file = projection_repo / "engineering" / "example.py"
        source_file.parent.mkdir()
        source_file.write_text("def existing_answer():\n    return 42\n", encoding="utf-8")
        git_args = ["/usr/bin/git", "-C", str(projection_repo)]
        subprocess.run([*git_args, "add", "-A"], check=True)
        subprocess.run([*git_args, "-c", "user.name=owner", "-c", "user.email=owner@example.invalid", "commit", "-m", "accepted projection"], check=True, capture_output=True)
        projection_sha = subprocess.run([*git_args, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        (self.config.bridge_journal_dir).mkdir(mode=0o700, exist_ok=True)
        projection = AcceptedSourceProjectionSpec(
            "platform", projection_repo, "refs/heads/brain/development", ("engineering/",),
            projection_sha, "d" * 40, "e" * 64,
        )
        program = AcceptedSourceProgramSpec("project-1", "mem89-issue", "sol-1", "luna-1", ("qa-1",), ("platform",))
        configured = replace(
            self.config,
            accepted_source_reader=AcceptedSourceReaderSpec((projection,), (program,)),
        )
        self.assertEqual(configured.owner_config_digest, self.config.owner_config_digest)
        payload = {
            "company_id": "company-1", "project_id": "project-1", "program_issue_id": "mem89-issue",
            "issue_id": "source-task", "agent_id": "sol-1", "run_id": "active-run",
            "corpus_id": "platform", "path": "engineering/example.py", "start_line": 1, "line_count": 2,
        }
        ref_reader = lambda repo, ref: subprocess.run(
            ["/usr/bin/git", "-C", str(repo), "rev-parse", "--verify", ref],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        with patch.object(BridgeConfig, "gate", return_value=SimpleNamespace(_ref_sha=ref_reader)):
            result = dispatch(configured, {"action": "read_accepted_source", "payload": payload})
        self.assertEqual(result["projection_sha"], projection_sha)
        self.assertEqual(result["canonical_sha"], "d" * 40)
        self.assertIn("return 42", result["result"]["lines"][1]["text"])
        denied = {**payload, "agent_id": "other-agent"}
        with self.assertRaisesRegex(BridgeError, "actor_not_admitted"):
            dispatch(configured, {"action": "read_accepted_source", "payload": denied})
        source_file.write_text("def existing_answer():\n    return 43\n", encoding="utf-8")
        subprocess.run([*git_args, "add", "-A"], check=True)
        subprocess.run([*git_args, "-c", "user.name=owner", "-c", "user.email=owner@example.invalid", "commit", "-m", "unacknowledged advance"], check=True, capture_output=True)
        list_payload = {
            **{key: value for key, value in payload.items() if key not in {"path", "start_line", "line_count"}},
            "prefix": "engineering/", "cursor": None, "limit": 10,
        }
        with patch.object(BridgeConfig, "gate", return_value=SimpleNamespace(_ref_sha=ref_reader)):
            with self.assertRaisesRegex(BridgeError, "acceptance_unavailable"):
                dispatch(configured, {"action": "list_accepted_source", "payload": list_payload})

    def test_program_source_scope_returns_only_owner_envelope_and_rejects_unmapped_project(self):
        from engineering.paperclip_autonomous_bridge import (
            ProgramAdmissionMission, ProgramAdmissionProfile, ProgramAdmissionSpec,
        )
        mission = ProgramAdmissionMission(
            project_id="senses-project", standing_issue_id="standing-90",
            sol_agent_id="sol", astra_agent_id="astra", luna_agent_id="luna",
            qa_agent_ids=("qa",), goal_ids=("goal-1",),
            allowed_prefixes=("src/memecoin_brain_sources/", "tests/brain/"),
            excluded_prefixes=("src/memecoin_brain_sources/private/",),
            corpus_ids=("brain",), profiles=(ProgramAdmissionProfile(
                "senses-v1", ("src/memecoin_brain_sources/events.py",),
                ("src/memecoin_brain_sources/", "tests/brain/"), ("tests/brain/test_raw_sources.py",), "policy-senses",
            ),), source_repo=Path(self.tmp.name) / "source", publisher_repo=Path(self.tmp.name) / "publisher",
            canonical_repo=Path(self.tmp.name) / "canonical", canonical_ref="refs/heads/brain/development",
            expected_dev_ref="refs/heads/brain/development", source_workspace_id="workspace-senses",
            source_workspace_root=Path(self.tmp.name) / "workspace",
            repository_free_policy_digest="e" * 64,
        )
        config = replace(self.config, program_admission=ProgramAdmissionSpec((mission,)))
        changed_policy = replace(mission, repository_free_policy_digest="f" * 64)
        changed_policy_config = replace(config, program_admission=ProgramAdmissionSpec((changed_policy,)))
        self.assertNotEqual(config.owner_config_digest, changed_policy_config.owner_config_digest)
        result = dispatch(config, {"action": "get_program_source_scope", "payload": {
            "company_id": config.company_id, "project_id": "senses-project", "goal_id": "goal-1",
        }})
        self.assertEqual(result["standing_issue_id"], "standing-90")
        self.assertEqual(result["repository_free_policy_digest"], mission.repository_free_policy_digest)
        self.assertEqual(result["profiles"][0]["required_test_paths"], ["tests/brain/test_raw_sources.py"])
        self.assertNotIn("source_repo", result)
        self.assertNotIn("owner_roots", result)
        from engineering.paperclip_autonomous_bridge import _parse_program_admission
        mission_row = {
            "project_id": mission.project_id, "standing_issue_id": mission.standing_issue_id,
            "sol_agent_id": mission.sol_agent_id, "astra_agent_id": mission.astra_agent_id,
            "luna_agent_id": mission.luna_agent_id, "qa_agent_ids": list(mission.qa_agent_ids),
            "goal_ids": list(mission.goal_ids), "allowed_prefixes": list(mission.allowed_prefixes),
            "excluded_prefixes": list(mission.excluded_prefixes), "corpus_ids": list(mission.corpus_ids),
            "profiles": [{"profile_id": profile.profile_id,
                "allowed_prefixes": list(profile.allowed_prefixes), "required_test_paths": list(profile.required_test_paths),
                "verifier_policy_id": profile.verifier_policy_id} for profile in mission.profiles],
            "source_repo": str(mission.source_repo), "publisher_repo": str(mission.publisher_repo),
            "canonical_repo": str(mission.canonical_repo), "canonical_ref": mission.canonical_ref,
            "expected_dev_ref": mission.expected_dev_ref, "source_workspace_id": mission.source_workspace_id,
            "source_workspace_root": str(mission.source_workspace_root),
            "repository_free_policy_digest": mission.repository_free_policy_digest,
        }
        parsed_admission = _parse_program_admission({"missions": [mission_row]})
        self.assertEqual(parsed_admission.missions[0].repository_free_policy_digest, mission.repository_free_policy_digest)
        invalid_mission_row = dict(mission_row)
        invalid_mission_row["repository_free_policy_digest"] = "invalid"
        with self.assertRaisesRegex(BridgeError, "owner_config_program_admission_invalid"):
            _parse_program_admission({"missions": [invalid_mission_row]})
        proposal = dispatch(config, {"action": "validate_program_source_proposal", "payload": {
            "company_id": config.company_id, "project_id": "senses-project", "goal_id": "goal-1",
            "profile_id": "senses-v1", "path_ids": [
                "src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py",
            ],
        }})
        self.assertEqual(proposal["status"], "admitted")
        self.assertEqual(proposal["path_ids"], ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"])
        from engineering.paperclip_autonomous_bridge import _accepted_source_program
        projection = AcceptedSourceProjectionSpec("brain", Path(self.tmp.name) / "projection",
            "refs/heads/brain/development", ("src/", "tests/"), "a" * 40, "b" * 40, "c" * 64)
        reader_config = replace(config, accepted_source_reader=AcceptedSourceReaderSpec((projection,), ()))
        dynamic_program, dynamic_projection = _accepted_source_program(reader_config, {
            "program_issue_id": "ceo-created-sol-program", "project_id": mission.project_id,
            "agent_id": mission.sol_agent_id, "corpus_id": "brain",
        })
        self.assertEqual(dynamic_program.program_issue_id, "ceo-created-sol-program")
        self.assertEqual(dynamic_projection.corpus_id, "brain")
        with self.assertRaisesRegex(BridgeError, "outside_owner_envelope"):
            dispatch(config, {"action": "validate_program_source_proposal", "payload": {
                "company_id": config.company_id, "project_id": "senses-project", "goal_id": "goal-1",
                "profile_id": "senses-v1", "path_ids": [
                    "src/memecoin_brain_sources/private/secret.py", "tests/brain/test_raw_sources.py",
                ],
            }})
        with self.assertRaisesRegex(BridgeError, "program_source_scope_not_admitted"):
            dispatch(config, {"action": "get_program_source_scope", "payload": {
                "company_id": config.company_id, "project_id": "other-project", "goal_id": "goal-1",
            }})

    def test_dynamic_program_stage_binds_selected_compatible_subset_to_current_accepted_ack(self):
        from engineering.paperclip_autonomous_bridge import (
            ProgramAdmissionMission, ProgramAdmissionProfile, ProgramAdmissionSpec,
            _stage_binding, bind_program_source_stage,
        )
        from engineering.paperclip_accepted_source_reader import AcceptedSourceSnapshot
        root = Path(self.tmp.name)
        source, publisher, canonical, workspace = (root / name for name in ("dynamic-source", "dynamic-publisher", "dynamic-canonical", "dynamic-workspace"))
        for path in (source, publisher, canonical, workspace): path.mkdir()
        paths = ("src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py")
        profile = VerifierProfile("policy-senses", (("/usr/bin/python3", "-c", "pass"),), (), ())
        mission = ProgramAdmissionMission(
            project_id="senses-project", standing_issue_id="standing-90", sol_agent_id="sol",
            astra_agent_id="astra", luna_agent_id="luna", qa_agent_ids=("qa",), goal_ids=("goal-1",),
            allowed_prefixes=("src/memecoin_brain_sources/", "tests/brain/"), excluded_prefixes=("src/private/",),
            corpus_ids=("brain",), profiles=(ProgramAdmissionProfile("senses-v1", (),
                ("src/memecoin_brain_sources/", "tests/brain/"), (paths[1],), "policy-senses"),),
            source_repo=source, publisher_repo=publisher, canonical_repo=canonical,
            canonical_ref="refs/heads/brain/development", expected_dev_ref="refs/heads/brain/development",
            source_workspace_id="senses-workspace", source_workspace_root=workspace,
            repository_free_policy_digest="e" * 64,
        )
        projection = AcceptedSourceProjectionSpec("brain", publisher, "refs/heads/brain/development",
            ("src/", "tests/"), "a" * 40, "b" * 40, "c" * 64)
        config = replace(self.config, bridge_journal_dir=root / "dynamic-journals", source_repo=source,
            publisher_repo=publisher, canonical_repo=canonical, canonical_ref="refs/heads/brain/development",
            canonical_expected_tip="b" * 40, expected_dev_ref="refs/heads/brain/development",
            fixed_dev_ref="refs/heads/brain/development", expected_base_sha="a" * 40,
            verifier_policy_catalog=(profile,), program_admission=ProgramAdmissionSpec((mission,)),
            accepted_source_reader=AcceptedSourceReaderSpec((projection,), ()))
        config.bridge_journal_dir.mkdir(mode=0o700)
        accepted = AcceptedSourceSnapshot("a" * 40, "b" * 40, "c" * 64, "d" * 64, ())
        accepted_rows = [accepted]
        fake_gate = SimpleNamespace(
            _ref_sha=lambda repo, ref: accepted_rows[0].projection_sha if Path(repo) == publisher else accepted_rows[0].canonical_sha,
            _resolve_sha=lambda repo, sha: sha,
        )
        payload = {"company_id": config.company_id, "project_id": mission.project_id,
            "lead_agent_id": "sol", "lead_issue_id": "program-1", "lead_run_id": "program-run",
            "goal_id": "goal-1", "profile_id": "senses-v1", "corpus_id": "brain", "path_ids": list(paths),
            "source_issue_id": "source-1", "source_version": 1, "review_issue_id": "review-1", "review_version": 1,
            "source_instruction_sha256": "1" * 64, "review_instruction_sha256": "2" * 64}
        with (patch("engineering.paperclip_autonomous_bridge._resolve_accepted_source_snapshot", side_effect=lambda *_: (publisher, accepted_rows[0])),
              patch.object(BridgeConfig, "gate", return_value=fake_gate),
              patch("engineering.paperclip_autonomous_bridge.refresh_owner_workspace", return_value="e" * 64)):
            bound = bind_program_source_stage(config, payload)
            next_task_payload = {**payload, "source_issue_id": "source-2", "review_issue_id": "review-2",
                "source_instruction_sha256": "3" * 64, "review_instruction_sha256": "4" * 64}
            second_bound = bind_program_source_stage(config, next_task_payload)
            self.assertNotEqual(bound["stage_key"], second_bound["stage_key"])
            self.assertNotEqual(bound["stage_binding_key"], second_bound["stage_binding_key"])
            accepted_rows[0] = AcceptedSourceSnapshot("5" * 40, "6" * 40, "7" * 64, "8" * 64, ())
            third_task_payload = {**payload, "source_issue_id": "source-3", "review_issue_id": "review-3",
                "source_instruction_sha256": "9" * 64, "review_instruction_sha256": "0" * 64}
            third_bound = bind_program_source_stage(config, third_task_payload)
            self.assertNotEqual(second_bound["stage_key"], third_bound["stage_key"])
            self.assertEqual(third_bound["base_sha"], "5" * 40)
            with self.assertRaisesRegex(BridgeError, "source_stage_bind_replay_mismatch"):
                bind_program_source_stage(config, payload)
        self.assertEqual(bound["base_sha"], "a" * 40)
        self.assertEqual(bound["canonical_base_sha"], "b" * 40)
        self.assertEqual(bound["repository_free_policy_digest"], mission.repository_free_policy_digest)
        record = _stage_binding(config, bound["stage_binding_key"])
        self.assertEqual(record["path_ids"], list(paths))
        self.assertEqual(record["dynamic_stage_spec"]["repository_free_policy_digest"], mission.repository_free_policy_digest)
        self.assertEqual(record["accepted_receipt_digest"], "c" * 64)
        lookup = dispatch(config, {"action": "lookup_source_stage_binding", "payload": {
            "company_id": config.company_id, "project_id": mission.project_id, "lead_agent_id": mission.sol_agent_id,
            "lead_issue_id": payload["lead_issue_id"], "stage_key": bound["stage_key"],
            "source_issue_id": payload["source_issue_id"], "review_issue_id": payload["review_issue_id"],
        }})
        self.assertEqual(lookup["repository_free_policy_digest"], mission.repository_free_policy_digest)
        stage = config.source_stage(bound["stage_key"])
        self.assertEqual(stage.project_id, mission.project_id)
        self.assertEqual(stage.path_ids, paths)
        self.assertEqual(stage.verifier_policy_id, "policy-senses")

    def test_accepted_source_reader_requires_paired_publication_ack_after_bootstrap(self):
        root = Path(self.tmp.name)
        projection_repo = root / "accepted-projection"
        projection_repo.mkdir()
        subprocess.run(["/usr/bin/git", "init", "-q", "-b", "org/development", str(projection_repo)], check=True)
        source_file = projection_repo / "engineering" / "example.py"
        source_file.parent.mkdir()
        source_file.write_text("def accepted_tip():\n    return 17\n", encoding="utf-8")
        git_args = ["/usr/bin/git", "-C", str(projection_repo)]
        subprocess.run([*git_args, "add", "-A"], check=True)
        subprocess.run([*git_args, "-c", "user.name=owner", "-c", "user.email=owner@example.invalid", "commit", "-m", "bootstrap"], check=True, capture_output=True)
        bootstrap_sha = subprocess.run([*git_args, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        source_file.write_text("def accepted_tip():\n    return 18\n", encoding="utf-8")
        subprocess.run([*git_args, "add", "-A"], check=True)
        subprocess.run([*git_args, "-c", "user.name=owner", "-c", "user.email=owner@example.invalid", "commit", "-m", "accepted next tip"], check=True, capture_output=True)
        projected_sha = subprocess.run([*git_args, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        bridge_dir = self.config.bridge_journal_dir
        bridge_dir.mkdir(mode=0o700, exist_ok=True)
        broker_dir = self.config.broker_journal_dir
        canonical_candidate = "f" * 40
        source_repo = root / "source"
        canonical_repo = root / "canonical"
        verifier_profile = VerifierProfile(
            "policy-platform-stage", (("/usr/bin/python3", "-c", "pass"),), (), ()
        )
        stage = SourceStageSpec(
            "PLATFORM-READER-TEST", "source", bootstrap_sha, None,
            ("src/example.py", "tests/test_example.py"), "policy-platform-stage",
            project_id="project-platform", goal_id="mission-platform",
            lead_agent_id="sol-platform", lead_issue_id="mem89-issue",
            source_repo=source_repo, publisher_repo=projection_repo,
            canonical_repo=canonical_repo, canonical_ref="refs/heads/org/development",
            canonical_expected_tip="a" * 40, expected_dev_ref="refs/heads/org/development",
        )
        configured = replace(
            self.config,
            project_id="project-mem17",
            source_repo=root / "mem17-source",
            publisher_repo=projection_repo,
            expected_dev_ref="refs/heads/brain/paperclip-integration",
            fixed_dev_ref="refs/heads/brain/paperclip-integration",
            canonical_repo=canonical_repo,
            canonical_ref="refs/heads/org/development",
            canonical_expected_tip="a" * 40,
            source_stage_catalog=(stage,),
            verifier_policy_catalog=(verifier_profile,),
        )
        stage_binding_key = "8" * 64
        operation_key = hashlib.sha256(
            json.dumps(["company-1", "source-task", "source-run"], separators=(",", ":")).encode()
        ).hexdigest()
        stage_record = {
            "schema": "paperclip-source-stage-binding-v1", "stage_key": stage.stage_key,
            "source_issue_id": "source-task", "review_issue_id": "review-task",
            "lead_issue_id": stage.lead_issue_id, "lead_agent_id": stage.lead_agent_id,
            "lead_run_id": "sol-run", "source_version": 1, "review_version": 1,
            "source_instruction_sha256": "9" * 64, "review_instruction_sha256": "0" * 64,
            "workspace_id": stage.workspace_id, "path_ids": list(stage.path_ids),
            "verifier_policy_id": stage.verifier_policy_id,
            "owner_config_digest": configured.owner_config_digest,
            "base_sha": bootstrap_sha, "canonical_base_sha": "a" * 40,
            "state": "active", "stage_binding_key": stage_binding_key,
            "operation_key": operation_key, "candidate_sha": projected_sha,
        }
        stage_binding_path = bridge_dir / f"operation-{_stage_journal_key(stage.stage_key)}.json"
        stage_binding_path.write_text(json.dumps(stage_record))
        stage_binding_path.chmod(0o600)
        operation_config = configured.for_source_stage(
            stage, source_issue_id="source-task", reviewer_issue_id="review-task",
            base_sha=bootstrap_sha, canonical_base_sha="a" * 40, source_repo=source_repo,
        )
        canonical_receipt = {
            "schema": "paperclip-canonical-content-v1",
            "repo": str(canonical_repo),
            "ref": "refs/heads/org/development",
            "expected_tip": "a" * 40,
            "tree": "b" * 40,
            "candidate": canonical_candidate,
            "files": [{"path": "src/example.py", "mode": "100644", "old": "c" * 40, "new": "d" * 40, "sha256": "e" * 64}],
        }
        canonical_receipt_digest = hashlib.sha256(
            json.dumps(canonical_receipt, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        canonical_receipt["receipt_digest"] = canonical_receipt_digest
        binding = {
            "company_id": "company-1", "issue_id": "source-task", "run_id": "source-run",
            "worker_agent_id": "worker-1", "reviewer_agent_id": "reviewer-1",
            "base_sha": bootstrap_sha, "submitted_sha": "2" * 40, "scope": ["src/example.py"],
            "policy_digest": operation_config.policy().digest, "active_ref": "refs/heads/org/development",
            "owner_config_digest": configured.owner_config_digest, "owner_roots": list(operation_config.owner_roots),
        }
        bridge_record = {
            "schema": "paperclip-autonomous-operation-v1", "operation_key": operation_key, "state": "published",
            "binding": binding, "candidate": {"candidate_sha": projected_sha},
            "source_snapshot_sha256": "4" * 64,
            "source_stage_binding": stage_binding_key, "source_stage_key": stage.stage_key,
            "canonical": {
                "status": "published", "candidate": canonical_candidate,
                "receipt_digest": canonical_receipt_digest, "receipt": canonical_receipt,
                "approved_candidate": canonical_candidate,
                "approved_receipt_digest": canonical_receipt_digest,
                "ack": {"status": "reconciled", "candidate": canonical_candidate,
                    "receipt_digest": canonical_receipt_digest, "ref": "refs/heads/org/development"},
            },
        }
        broker_record = {
            "schema": "paperclip-git-operation-v1", "operation_key": operation_key, "state": "published",
            "publish_ack": "confirmed", "binding": binding, "candidate": {"sha": projected_sha},
            "receipt_digest": "5" * 64,
            "approval": {"approved": True, "company_id": "company-1", "run_id": "source-run",
                "candidate_sha": projected_sha, "reviewer_agent_id": "reviewer-1", "policy_digest": operation_config.policy().digest},
        }
        bridge_path = bridge_dir / f"operation-{operation_key}.json"
        bridge_path.write_text(json.dumps(bridge_record))
        bridge_path.chmod(0o600)
        broker_path = broker_dir / f"{operation_key}.json"
        broker_path.write_text(json.dumps(broker_record))
        broker_path.chmod(0o600)
        projection = AcceptedSourceProjectionSpec(
            "brain", projection_repo, "refs/heads/org/development", ("engineering/",),
            bootstrap_sha, "6" * 40, "7" * 64,
        )
        program = AcceptedSourceProgramSpec("project-platform", "mem89-issue", "sol-1", "luna-1", (), ("brain",))
        configured = replace(configured, accepted_source_reader=AcceptedSourceReaderSpec((projection,), (program,)))
        payload = {
            "company_id": "company-1", "project_id": "project-platform", "program_issue_id": "mem89-issue",
            "issue_id": "source-task", "agent_id": "sol-1", "run_id": "active-run", "corpus_id": "brain",
            "prefix": "engineering/", "cursor": None, "limit": 10,
        }
        read_gate = SimpleNamespace(
            _ref_sha=lambda repo, ref: subprocess.run(
                ["/usr/bin/git", "-C", str(repo), "rev-parse", "--verify", ref],
                check=True, capture_output=True, text=True,
            ).stdout.strip(),
            load_receipt=lambda _digest, _sha: {"result": {"status": "passed"}},
        )
        gate_calls = []
        def gate_for_config(instance):
            gate_calls.append((instance.verifier_policy_id, instance.publisher_repo, instance.expected_dev_ref))
            return read_gate
        with patch.object(BridgeConfig, "gate", gate_for_config):
            result = dispatch(configured, {"action": "list_accepted_source", "payload": payload})
        self.assertEqual(result["projection_sha"], projected_sha)
        self.assertEqual(result["canonical_sha"], canonical_candidate)
        self.assertEqual(result["acceptance_receipt_digest"], canonical_receipt_digest)
        self.assertIn(("policy-platform-stage", projection_repo, "refs/heads/org/development"), gate_calls)
        wrong_roots = list(configured.owner_roots)
        bridge_record["binding"] = {**binding, "owner_roots": wrong_roots}
        broker_record["binding"] = {**binding, "owner_roots": wrong_roots}
        bridge_path.write_text(json.dumps(bridge_record))
        broker_path.write_text(json.dumps(broker_record))
        with patch.object(BridgeConfig, "gate", gate_for_config):
            with self.assertRaisesRegex(BridgeError, "acceptance_unavailable"):
                dispatch(configured, {"action": "list_accepted_source", "payload": payload})

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
            "-c",
            "user.name=owner",
            "-c",
            "user.email=owner@example.invalid",
            "commit",
            "-m",
            "base",
        )
        base = git(source, "rev-parse", "HEAD")
        subprocess.run(["/usr/bin/git", "clone", "-q", str(source), str(publisher)], check=True)
        canonical = root / "canonical"
        subprocess.run(["/usr/bin/git", "clone", "-q", str(source), str(canonical)], check=True)
        git(canonical, "checkout", "-q", "-b", "brain/development")
        for path in (
            "src/memecoin_brain_sources/events.py",
            "tests/brain/test_raw_sources.py",
        ):
            (canonical / path).write_text("canonical pre-capacity baseline\n")
        git(
            canonical,
            "add",
            "--",
            "src/memecoin_brain_sources/events.py",
            "tests/brain/test_raw_sources.py",
        )
        git(
            canonical,
            "-c",
            "user.name=owner",
            "-c",
            "user.email=owner@example.invalid",
            "commit",
            "-m",
            "canonical pre-capacity base",
        )
        canonical_base = git(canonical, "rev-parse", "HEAD")
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
            canonical_repo=canonical,
            canonical_ref="refs/heads/brain/development",
            canonical_expected_tip=canonical_base,
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
        stale_canonical = replace(config, canonical_expected_tip="f" * 40)
        with self.assertRaisesRegex(BridgeError, "canonical_prepare_failed"):
            dispatch(
                stale_canonical,
                {
                    "action": "deliver_source_content",
                    "payload": {**payload, "run_id": "stale-canonical-run"},
                },
            )
        self.assertEqual(
            prepared["changed_paths"],
            ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"],
        )
        journal_path = config.bridge_journal_dir / f"operation-{prepared['operation_key']}.json"
        journal = json.loads(journal_path.read_text())
        self.assertEqual(journal["source_snapshot_sha256"], prepared["source_snapshot_sha256"])
        self.assertEqual(
            journal["binding"]["scope"],
            ["src/memecoin_brain_sources/events.py", "tests/brain/test_raw_sources.py"],
        )
        self.assertEqual(prepared["canonical_candidate_sha"], journal["canonical"]["candidate"])
        self.assertEqual(
            prepared["canonical_receipt_digest"], journal["canonical"]["receipt_digest"]
        )
        self.assertNotEqual(journal["canonical"]["expected_tip"], journal["binding"]["base_sha"])
        self.assertEqual(journal["canonical"]["projected_base"], journal["binding"]["base_sha"])
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
            "company_id": "company-1",
            "project_id": "project-1",
            "reviewer_agent_id": "reviewer-1",
            "reviewer_run_id": "review-run-1",
            "reviewer_issue_id": "issue-reviewer",
            "operation_key": prepared["operation_key"],
            "candidate_sha": prepared["candidate_sha"],
        }
        with self.assertRaisesRegex(BridgeError, "candidate_does_not_match_prepared_journal"):
            dispatch(
                config,
                {"action": "inspect_candidate", "payload": {**reviewer, "candidate_sha": "0" * 40}},
            )
        inspected = dispatch(config, {"action": "inspect_candidate", "payload": reviewer})
        self.assertEqual(inspected["target_ref"], config.expected_dev_ref)
        self.assertEqual(inspected["canonical_ref"], config.canonical_ref)
        self.assertEqual(inspected["canonical_candidate_sha"], prepared["canonical_candidate_sha"])
        self.assertEqual(
            inspected["canonical_receipt_digest"], prepared["canonical_receipt_digest"]
        )
        self.assertNotEqual(
            inspected["canonical_projected_mapping"]["projected_base"],
            inspected["canonical_base_sha"],
        )
        self.assertIn("events = ['synthetic']", inspected["canonical_diff"])
        evidence = {
            "inspection_digest": inspected["inspection_digest"],
            "canonical_candidate_sha": inspected["canonical_candidate_sha"],
            "canonical_receipt_digest": inspected["canonical_receipt_digest"],
            "reason": "synthetic approval of both exact candidates",
        }
        with self.assertRaisesRegex(BridgeError, "canonical_review_binding_mismatch"):
            dispatch(
                config,
                {
                    "action": "approve_candidate",
                    "payload": {
                        **reviewer,
                        "approved": True,
                        "evidence": {**evidence, "canonical_candidate_sha": "0" * 40},
                    },
                },
            )
        from types import SimpleNamespace

        from engineering.paperclip_git_broker import PaperclipGitBroker

        with (
            patch.object(
                PaperclipGitBroker,
                "integrate",
                return_value=SimpleNamespace(
                    status="published",
                    candidate_sha=prepared["candidate_sha"],
                    receipt_digest="1" * 64,
                    operation_key=prepared["operation_key"],
                    evidence_digest="2" * 64,
                ),
            ),
            patch(
                "engineering.paperclip_autonomous_bridge._verifier",
                return_value=(lambda *_args: {}, None),
            ),
            patch(
                "engineering.paperclip_autonomous_bridge.canonical_publisher.reconcile",
                side_effect=RuntimeError("simulated uncertain canonical acknowledgement"),
            ),
        ):
            with self.assertRaisesRegex(BridgeError, "canonical_publish_held"):
                dispatch(
                    config,
                    {
                        "action": "approve_candidate",
                        "payload": {**reviewer, "approved": True, "evidence": evidence},
                    },
                )
        held_journal = json.loads(journal_path.read_text())
        self.assertEqual(held_journal["state"], "canonical_publish_held")
        self.assertEqual(held_journal["canonical"]["status"], "publish_intent")
        self.assertEqual(
            git(canonical, "rev-parse", "refs/heads/brain/development"), canonical_base
        )
        with (
            patch.object(
                PaperclipGitBroker,
                "integrate",
                return_value=SimpleNamespace(
                    status="published",
                    candidate_sha=prepared["candidate_sha"],
                    receipt_digest="1" * 64,
                    operation_key=prepared["operation_key"],
                    evidence_digest="2" * 64,
                ),
            ),
            patch(
                "engineering.paperclip_autonomous_bridge._verifier",
                return_value=(lambda *_args: {}, None),
            ),
        ):
            review = dispatch(
                config,
                {
                    "action": "approve_candidate",
                    "payload": {**reviewer, "approved": True, "evidence": evidence},
                },
            )
        self.assertEqual(review["status"], "published")
        self.assertEqual(review["projection_status"], "published")
        self.assertEqual(review["canonical_status"], "published")
        self.assertEqual(
            git(canonical, "rev-parse", "refs/heads/brain/development"),
            prepared["canonical_candidate_sha"],
        )
        self.assertEqual(
            (canonical / "src/memecoin_brain_sources/events.py").read_text(), bodies[0]
        )
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
                BridgeConfig,
                "gate",
                return_value=SimpleNamespace(
                    load_receipt=lambda *_: None,
                    _run=lambda *_args: SimpleNamespace(stdout=b"synthetic-tree\n"),
                ),
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
        for directory in (
            root / "broker-journal",
            root / "bridge-journal",
            root / "receipts",
            root / "scratch",
        ):
            directory.mkdir(mode=0o700)
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
        executable_alias = replace(development, python_executable=Path("/usr/bin/../bin/python3"))
        alias_binding = _binding(executable_alias, self.worker_payload())
        self.assertEqual(original.active_ref, "refs/heads/brain/paperclip-integration")
        self.assertEqual(lane_binding.active_ref, "refs/heads/brain/development")
        self.assertNotEqual(original.owner_config_digest, lane_binding.owner_config_digest)
        self.assertNotEqual(lane_binding.owner_roots, roots_binding.owner_roots)
        self.assertNotEqual(lane_binding.owner_config_digest, roots_binding.owner_config_digest)
        self.assertNotEqual(lane_binding.owner_config_digest, alias_binding.owner_config_digest)

    def test_reader_v2_legacy_write_and_read_digests_remain_stable(self):
        # The owner config stays private; this local compatibility check asserts only its digests.
        legacy_path = Path(
            "/Users/root1/Developer/brain-paperclip-lab/state/resumption-20261025/reader-release/owner-config-reader-v2.json"
        )
        if not legacy_path.is_file():
            self.skipTest("private reader-v2 compatibility fixture is unavailable")
        legacy = BridgeConfig.load(legacy_path)
        self.assertEqual(
            legacy.owner_config_digest,
            "761441474ec19a013666c1cc70cad78f876797c0d3e4e99911ee73481870bda2",
        )
        self.assertEqual(
            legacy.accepted_source_read_policy_digest,
            "fe569791a81382c664f62c96b7744b62b3d9cdfbc45d6ae58e6fba9275132327",
        )

    def test_prepared_journal_cannot_replay_under_different_lane(self):
        with patch.object(
            BridgeConfig,
            "broker",
            return_value=FakeBroker(self.candidate, self.config.broker_journal_dir),
        ):
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
            with self.assertRaisesRegex(BridgeError, "verifier_runtime_root_overlaps_publisher"):
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
        self.assertEqual(BridgeConfig.load(path).expected_dev_ref, "refs/heads/brain/development")
        raw.pop("worker_checkout")
        raw["fixed_dev_ref"] = "refs/heads/brain/paperclip-integration"
        raw["expected_dev_ref"] = "refs/heads/brain/paperclip-integration"
        raw.pop("expected_base_sha", None)
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        content_only = BridgeConfig.load(path)
        self.assertIsNone(content_only.worker_checkout)
        self.assertNotIn("/tmp/worker", content_only.owner_roots)
        raw.pop("source_issue_id")
        raw.pop("reviewer_issue_id")
        raw.update(
            {
                "expected_base_sha": "a" * 40,
                "finite_lead_agent_id": "lead-agent",
                "finite_lead_issue_id": "lead-issue",
                "source_workspace_id": "workspace-1",
                "source_workspace_root": "/tmp/source-workspace",
                "source_stage_catalog": [
                    {
                        "stage_key": "MEM8",
                        "workspace_id": "source",
                        "initial_base_sha": "a" * 40,
                        "predecessor_stage_key": None,
                        "path_ids": [
                            "src/memecoin_brain_sources/events.py",
                            "tests/brain/test_raw_sources.py",
                        ],
                        "verifier_policy_id": "policy-mem8",
                    },
                    {
                        "stage_key": "MEM9",
                        "workspace_id": "published",
                        "initial_base_sha": None,
                        "predecessor_stage_key": "MEM8",
                        "path_ids": [
                            "src/memecoin_brain_sources/events.py",
                            "tests/brain/test_raw_sources.py",
                        ],
                        "verifier_policy_id": "policy-mem9",
                    },
                ],
            }
        )
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        finite = BridgeConfig.load(path)
        self.assertEqual(
            [stage.stage_key for stage in finite.source_stage_catalog], ["MEM8", "MEM9"]
        )
        self.assertIsNone(finite.worker_checkout)
        first_stage = raw["source_stage_catalog"][0]
        first_stage.update(
            project_id="experience-project",
            goal_id="engineering-goal",
            lead_agent_id="experience-sol",
            lead_issue_id="experience-lead",
            source_workspace_id="experience-workspace",
            source_workspace_root="/tmp/experience-workspace",
            source_repo="/tmp/platform-source",
            publisher_repo="/tmp/platform-publisher",
            canonical_repo="/tmp/platform-canonical",
            canonical_ref="refs/heads/org/development",
            canonical_expected_tip="c" * 40,
            expected_dev_ref="refs/heads/org/development",
        )
        raw["source_stage_catalog"][1].update(
            project_id="experience-project",
            goal_id="engineering-goal",
            lead_agent_id="experience-sol",
            lead_issue_id="experience-lead",
            source_workspace_id="experience-workspace",
            source_workspace_root="/tmp/experience-workspace",
            source_repo="/tmp/platform-source",
            publisher_repo="/tmp/platform-publisher",
            canonical_repo="/tmp/platform-canonical",
            canonical_ref="refs/heads/org/development",
            canonical_expected_tip="c" * 40,
            expected_dev_ref="refs/heads/org/development",
        )
        raw["verifier_policy_catalog"] = [
            {
                "policy_id": "policy-mem8",
                "commands": [["/usr/bin/true"]],
                "env": {},
                "runtime_read_roots": [],
            },
            {
                "policy_id": "policy-mem9",
                "commands": [["/usr/bin/true", "{checkout}"]],
                "env": {},
                "runtime_read_roots": [],
            },
        ]
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        department = BridgeConfig.load(path)
        stage_config = department.for_source_stage(
            department.source_stage("MEM9"),
            source_issue_id="source-1",
            reviewer_issue_id="review-1",
            base_sha="a" * 40,
        )
        self.assertEqual(stage_config.project_id, "experience-project")
        self.assertEqual(stage_config.finite_lead_agent_id, "experience-sol")
        self.assertEqual(stage_config.finite_lead_issue_id, "experience-lead")
        self.assertEqual(stage_config.source_workspace_id, "experience-workspace")
        self.assertEqual(stage_config.expected_dev_ref, "refs/heads/org/development")
        self.assertEqual(stage_config.source_repo, Path("/tmp/platform-source"))
        self.assertEqual(stage_config.canonical_ref, "refs/heads/org/development")
        self.assertEqual(stage_config.verifier_commands, (("/usr/bin/true", "{checkout}"),))
        original_digest = department.owner_config_digest
        cross_lane = dict(raw["source_stage_catalog"][1])
        cross_lane.update(
            stage_key="CORTEX1",
            project_id="cortex-project",
            lead_agent_id="cortex-sol",
            lead_issue_id="cortex-lead",
            source_workspace_id="cortex-workspace",
            source_workspace_root="/tmp/cortex-workspace",
            predecessor_stage_key="MEM9",
            initial_base_sha=None,
        )
        raw["source_stage_catalog"].append(cross_lane)
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        chained = BridgeConfig.load(path)
        self.assertEqual(chained.source_stage("CORTEX1").predecessor_stage_key, "MEM9")
        raw["source_stage_catalog"][2]["expected_dev_ref"] = "refs/heads/brain/linux"
        raw["source_stage_catalog"][2]["canonical_ref"] = "refs/heads/brain/linux"
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        with self.assertRaisesRegex(BridgeError, "owner_config_source_stage_catalog_invalid"):
            BridgeConfig.load(path)
        raw["source_stage_catalog"].pop()
        raw["source_stage_catalog"][0]["path_ids"] = [
            "src/memecoin_experience/records.py",
            "tests/test_experience.py",
        ]
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        drifted = BridgeConfig.load(path)
        self.assertNotEqual(drifted.owner_config_digest, original_digest)
        raw["source_stage_catalog"][0]["path_ids"] = [
            "src/memecoin_brain_sources/events.py",
            "tests/../outside.py",
        ]
        path.write_text(json.dumps(raw))
        path.chmod(0o600)
        with self.assertRaisesRegex(BridgeError, "owner_config_source_stage_catalog_invalid"):
            BridgeConfig.load(path)
        raw["source_stage_catalog"][0]["path_ids"] = [
            "src/memecoin_brain_sources/events.py",
            "tests/brain/test_raw_sources.py",
        ]
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

    @patch(
        "engineering.paperclip_autonomous_bridge._trusted_stage_source_cache_identity",
        return_value="f" * 40,
    )
    def test_finite_stage_binding_cas_activation_and_publication_advance_base(
        self, _cache_identity
    ):
        from engineering.paperclip_source_delivery import SOURCE_PATHS

        stages = (
            SourceStageSpec(
                "MEM8",
                "source",
                "a" * 40,
                None,
                SOURCE_PATHS,
                "policy-mem8",
                self.config.project_id,
                None,
                "lead-1",
                "lead-issue",
            ),
            SourceStageSpec(
                "MEM9",
                "published",
                None,
                "MEM8",
                SOURCE_PATHS,
                "policy-mem9",
                "experience-project",
                "engineering-goal",
                "experience-sol",
                "experience-lead",
            ),
        )
        config = replace(
            self.config,
            source_issue_id=None,
            reviewer_issue_id=None,
            worker_checkout=None,
            expected_base_sha="a" * 40,
            finite_lead_agent_id="lead-1",
            finite_lead_issue_id="lead-issue",
            source_stage_catalog=stages,
            canonical_repo=Path(self.tmp.name) / "canonical",
            canonical_ref="refs/heads/brain/development",
            canonical_expected_tip="b" * 40,
        )

        def stage_payload(stage_key, source_id, review_id, **prior):
            stage = config.source_stage(stage_key)
            return {
                "company_id": config.company_id,
                "project_id": stage.project_id or config.project_id,
                "lead_agent_id": stage.lead_agent_id or "lead-1",
                "lead_issue_id": stage.lead_issue_id or "lead-issue",
                "lead_run_id": "lead-run",
                "stage_key": stage_key,
                "source_issue_id": source_id,
                "source_version": 1,
                "review_issue_id": review_id,
                "review_version": 1,
                "predecessor_operation_key": None,
                "predecessor_source_status": None,
                "predecessor_source_version": None,
                "predecessor_review_status": None,
                "predecessor_review_version": None,
                "predecessor_source_issue_id": None,
                "predecessor_review_issue_id": None,
                "predecessor_dependency_ready": None,
                "source_instruction_sha256": "1" * 64,
                "review_instruction_sha256": "2" * 64,
                **prior,
            }

        first_payload = stage_payload("MEM8", "source-8", "review-8")
        first = dispatch(config, {"action": "bind_source_stage", "payload": first_payload})
        self.assertEqual(first["base_sha"], "a" * 40)
        self.assertEqual(first["state"], "bound")
        lookup = dispatch(
            config,
            {
                "action": "lookup_source_stage_binding",
                "payload": {
                    "company_id": config.company_id,
                    "project_id": config.project_id,
                    "lead_agent_id": "lead-1",
                    "lead_issue_id": "lead-issue",
                    "stage_key": "MEM8",
                    "source_issue_id": "source-8",
                    "review_issue_id": "review-8",
                },
            },
        )
        self.assertEqual(lookup["stage_binding_key"], first["stage_binding_key"])
        self.assertIsNone(lookup["predecessor_operation_key"])
        self.assertIsNone(lookup["predecessor_source_issue_id"])
        self.assertEqual(lookup["source_instruction_sha256"], "1" * 64)
        self.assertEqual(lookup["review_instruction_sha256"], "2" * 64)
        first_stage_key = _stage_journal_key("MEM8")
        first_stage_record = _load_journal(config, first_stage_key)
        for invalid_attempt in (True, None, "1", 0, 4):
            malformed = {**first_stage_record, "attempt": invalid_attempt}
            _write_journal(config, first_stage_key, malformed, create=False)
            with self.subTest(stored_attempt=invalid_attempt):
                for lookup_payload in (
                    {
                        "company_id": config.company_id,
                        "project_id": config.project_id,
                        "lead_agent_id": "lead-1",
                        "lead_issue_id": "lead-issue",
                        "stage_key": "MEM8",
                        "attempt": 1,
                    },
                    {
                        "company_id": config.company_id,
                        "project_id": config.project_id,
                        "lead_agent_id": "lead-1",
                        "lead_issue_id": "lead-issue",
                        "stage_key": "MEM8",
                        "stage_binding_key": first["stage_binding_key"],
                    },
                ):
                    with self.assertRaisesRegex(BridgeError, "source_stage_attempt_invalid"):
                        dispatch(
                            config,
                            {
                                "action": "lookup_source_stage_binding",
                                "payload": lookup_payload,
                            },
                        )
                with self.assertRaisesRegex(BridgeError, "source_stage_attempt_invalid"):
                    dispatch(
                        config,
                        {
                            "action": "source_stage_activation",
                            "payload": {
                                "step": "intent",
                                "stage_binding_key": first["stage_binding_key"],
                                "issue_id": "source-8",
                                "observed_status": "backlog",
                                "observed_version": 1,
                            },
                        },
                    )
        _write_journal(config, first_stage_key, first_stage_record, create=False)
        legacy_lookup = dispatch(
            config,
            {
                "action": "lookup_source_stage_binding",
                "payload": {
                    "company_id": config.company_id,
                    "project_id": config.project_id,
                    "lead_agent_id": "lead-1",
                    "lead_issue_id": "lead-issue",
                    "stage_key": "MEM8",
                    "attempt": 1,
                },
            },
        )
        self.assertEqual(legacy_lookup["stage_binding_key"], first["stage_binding_key"])
        self.assertEqual(
            dispatch(config, {"action": "bind_source_stage", "payload": first_payload}), first
        )
        self.assertIsNone(config.worker_checkout)
        self.assertNotIn(str(Path(self.tmp.name) / "worker"), config.owner_roots)
        with self.assertRaisesRegex(BridgeError, "source_stage_not_active"):
            dispatch(
                config,
                {
                    "action": "deliver_source_stage_content",
                    "payload": {
                        "company_id": config.company_id,
                        "project_id": config.project_id,
                        "source_issue_id": "source-8",
                        "worker_agent_id": config.worker_agent_id,
                        "run_id": "worker-run",
                        "stage_key": "MEM8",
                        "stage_binding_key": first["stage_binding_key"],
                        "source_instruction_sha256": "1" * 64,
                        "files": [],
                    },
                },
            )
        changed = {**first_payload, "source_issue_id": "other-source"}
        with self.assertRaisesRegex(BridgeError, "source_stage_bind_replay_mismatch"):
            dispatch(config, {"action": "bind_source_stage", "payload": changed})

        intent = {
            "step": "intent",
            "stage_binding_key": first["stage_binding_key"],
            "issue_id": "source-8",
            "observed_status": "backlog",
            "observed_version": 1,
        }
        self.assertEqual(
            dispatch(config, {"action": "source_stage_activation", "payload": intent})["intent"],
            "created",
        )
        self.assertEqual(
            dispatch(config, {"action": "source_stage_activation", "payload": intent})["intent"],
            "reconcile_required",
        )
        ack = {
            "step": "ack",
            "stage_binding_key": first["stage_binding_key"],
            "issue_id": "source-8",
            "mutation_status": "todo",
            "mutation_status_version": 2,
        }
        self.assertEqual(
            dispatch(config, {"action": "source_stage_activation", "payload": ack})["state"],
            "active",
        )

        operation_key = "f" * 64
        _write_journal(
            config,
            operation_key,
            {
                "schema": "paperclip-autonomous-operation-v1",
                "operation_key": operation_key,
                "state": "published",
                "source_stage_key": "MEM8",
                "source_stage_binding": first["stage_binding_key"],
                "candidate": {
                    "operation_key": operation_key,
                    "stage_binding_key": first["stage_binding_key"],
                    "candidate_sha": "d" * 40,
                },
            },
            create=True,
        )
        first_stage_record = _load_journal(config, _stage_journal_key("MEM8"))
        first_stage_record["operation_key"] = operation_key
        first_stage_record["candidate_sha"] = "d" * 40
        _write_journal(config, _stage_journal_key("MEM8"), first_stage_record, create=False)
        operation_lookup = dispatch(
            config,
            {
                "action": "lookup_source_stage_binding",
                "payload": {
                    "company_id": config.company_id,
                    "project_id": config.project_id,
                    "lead_agent_id": "lead-1",
                    "lead_issue_id": "lead-issue",
                    "stage_key": "MEM8",
                    "operation_key": operation_key,
                },
            },
        )
        self.assertEqual(operation_lookup["attempt"], 1)
        self.assertEqual(operation_lookup["source_issue_id"], "source-8")
        self.assertEqual(operation_lookup["review_issue_id"], "review-8")
        self.assertEqual(operation_lookup["operation_key"], operation_key)
        second_payload = stage_payload(
            "MEM9",
            "source-9",
            "review-9",
            predecessor_operation_key=operation_key,
            predecessor_source_status=None,
            predecessor_source_version=None,
            predecessor_review_status=None,
            predecessor_review_version=None,
            predecessor_source_issue_id="source-8",
            predecessor_review_issue_id="review-8",
            predecessor_dependency_ready=True,
            predecessor_lead_issue_id="lead-issue",
        )
        with self.assertRaisesRegex(BridgeError, "source_stage_predecessor_not_finalized"):
            dispatch(config, {"action": "bind_source_stage", "payload": second_payload})
        prior = _load_journal(config, operation_key)
        prior["canonical"] = {"status": "published", "candidate": "e" * 40}
        _write_journal(config, operation_key, prior, create=False)
        expected_cache = config.bridge_journal_dir / "trusted-stage-sources" / (f"MEM9-{'d' * 40}")
        with (
            patch.object(BridgeConfig, "gate", return_value=SimpleNamespace()),
            patch(
                "engineering.paperclip_autonomous_bridge._create_trusted_stage_source_cache",
                return_value=(expected_cache, "f" * 40),
            ),
            patch(
                "engineering.paperclip_autonomous_bridge._trusted_stage_source_cache_identity",
                return_value="f" * 40,
            ),
        ):
            second = dispatch(config, {"action": "bind_source_stage", "payload": second_payload})
        self.assertEqual(second["base_sha"], "d" * 40)
        self.assertEqual(second["canonical_base_sha"], "e" * 40)
        self.assertEqual(second["state"], "bound")
        with patch(
            "engineering.paperclip_autonomous_bridge._trusted_stage_source_cache_identity",
            return_value="f" * 40,
        ):
            second_lookup = dispatch(
                config,
                {
                    "action": "lookup_source_stage_binding",
                    "payload": {
                        "company_id": config.company_id,
                        "project_id": "experience-project",
                        "lead_agent_id": "experience-sol",
                        "lead_issue_id": "experience-lead",
                        "stage_key": "MEM9",
                        "source_issue_id": "source-9",
                        "review_issue_id": "review-9",
                    },
                },
            )
        self.assertEqual(second_lookup["predecessor_operation_key"], operation_key)
        self.assertEqual(second_lookup["predecessor_source_issue_id"], "source-8")
        self.assertEqual(second_lookup["predecessor_review_issue_id"], "review-8")
        self.assertEqual(second_lookup["predecessor_lead_issue_id"], "lead-issue")
        second_record = _load_journal(config, _stage_journal_key("MEM9"))
        second_record["operation_key"] = "9" * 64
        second_record["candidate_sha"] = "8" * 40
        _write_journal(config, _stage_journal_key("MEM9"), second_record, create=False)
        second_operation = {
            "schema": "paperclip-autonomous-operation-v1",
            "operation_key": "9" * 64,
            "state": "prepared",
            "source_stage_binding": second["stage_binding_key"],
            "source_stage_key": "MEM9",
            "binding": {"base_sha": "d" * 40},
            "candidate": {"candidate_sha": "8" * 40},
        }
        _write_journal(config, "9" * 64, second_operation, create=True)
        review_payload = {
            "company_id": config.company_id,
            "project_id": "experience-project",
            "reviewer_agent_id": config.reviewer_agent_id,
            "reviewer_run_id": "experience-review-run",
            "reviewer_issue_id": "review-9",
            "stage_key": "MEM9",
            "stage_binding_key": second["stage_binding_key"],
            "review_instruction_sha256": "2" * 64,
        }
        with patch(
            "engineering.paperclip_autonomous_bridge.inspect_candidate",
            return_value={"operation_key": "9" * 64, "candidate_sha": "8" * 40},
        ) as inspect:
            dispatch(config, {"action": "inspect_bound_source_stage", "payload": review_payload})
        self.assertEqual(inspect.call_args.args[1]["project_id"], "experience-project")
        with patch(
            "engineering.paperclip_autonomous_bridge.integrate_candidate",
            return_value={"operation_key": "9" * 64, "status": "published"},
        ) as approve:
            dispatch(
                config,
                {
                    "action": "approve_bound_source_stage",
                    "payload": {
                        **review_payload,
                        "approved": True,
                        "evidence": {"inspection_digest": "3" * 64, "rationale": "verified"},
                    },
                },
            )
        self.assertEqual(approve.call_args.args[1]["project_id"], "experience-project")

    def test_finite_stage_holds_when_predecessor_publication_or_finalization_is_unknown(self):
        from engineering.paperclip_source_delivery import SOURCE_PATHS

        stages = (
            SourceStageSpec("MEM8", "source", "a" * 40, None, SOURCE_PATHS, "policy-mem8"),
            SourceStageSpec("MEM9", "published", None, "MEM8", SOURCE_PATHS, "policy-mem9"),
        )
        config = replace(
            self.config,
            source_issue_id=None,
            reviewer_issue_id=None,
            worker_checkout=None,
            expected_base_sha="a" * 40,
            finite_lead_agent_id="lead-1",
            finite_lead_issue_id="lead-issue",
            source_stage_catalog=stages,
        )
        first_payload = {
            "company_id": config.company_id,
            "project_id": config.project_id,
            "lead_agent_id": "lead-1",
            "lead_issue_id": "lead-issue",
            "lead_run_id": "lead-run",
            "stage_key": "MEM8",
            "source_issue_id": "source-8",
            "source_version": 1,
            "review_issue_id": "review-8",
            "review_version": 1,
            "predecessor_operation_key": None,
            "predecessor_source_status": None,
            "predecessor_source_version": None,
            "predecessor_review_status": None,
            "predecessor_review_version": None,
            "predecessor_source_issue_id": None,
            "predecessor_review_issue_id": None,
            "predecessor_dependency_ready": None,
            "source_instruction_sha256": "1" * 64,
            "review_instruction_sha256": "2" * 64,
        }
        first = dispatch(config, {"action": "bind_source_stage", "payload": first_payload})
        operation_key = "e" * 64
        _write_journal(
            config,
            operation_key,
            {
                "schema": "paperclip-autonomous-operation-v1",
                "operation_key": operation_key,
                "state": "prepared",
                "source_stage_binding": first["stage_binding_key"],
                "candidate": {"operation_key": operation_key, "candidate_sha": "d" * 40},
            },
            create=True,
        )
        second = {
            **first_payload,
            "stage_key": "MEM9",
            "source_issue_id": "source-9",
            "review_issue_id": "review-9",
            "predecessor_operation_key": operation_key,
            "predecessor_source_status": "done",
            "predecessor_source_version": 2,
            "predecessor_review_status": "done",
            "predecessor_review_version": 2,
            "predecessor_source_issue_id": "source-8",
            "predecessor_review_issue_id": "review-8",
            "predecessor_dependency_ready": True,
        }
        with self.assertRaisesRegex(BridgeError, "source_stage_predecessor_not_finalized"):
            dispatch(config, {"action": "bind_source_stage", "payload": second})
        _write_journal(
            config,
            operation_key,
            {
                "schema": "paperclip-autonomous-operation-v1",
                "operation_key": operation_key,
                "state": "published",
                "source_stage_binding": first["stage_binding_key"],
                "candidate": {"operation_key": operation_key, "candidate_sha": "d" * 40},
            },
            create=False,
        )
        second["predecessor_dependency_ready"] = False
        with self.assertRaisesRegex(BridgeError, "source_stage_predecessor_not_finalized"):
            dispatch(config, {"action": "bind_source_stage", "payload": second})

    def test_attempt_three_uses_only_finalized_attempt_two_and_preserves_prior_journals(self):
        from engineering.paperclip_source_delivery import SOURCE_PATHS

        stages = (
            SourceStageSpec("MEM8", "source", "a" * 40, None, SOURCE_PATHS, "policy-mem8"),
            SourceStageSpec("MEM9", "published", None, "MEM8", SOURCE_PATHS, "policy-mem9"),
        )
        config = replace(
            self.config,
            source_issue_id=None,
            reviewer_issue_id=None,
            worker_checkout=None,
            expected_base_sha="a" * 40,
            finite_lead_agent_id="lead-1",
            finite_lead_issue_id="lead-issue",
            source_stage_catalog=stages,
            canonical_repo=Path(self.tmp.name) / "canonical",
            canonical_ref="refs/heads/brain/development",
            canonical_expected_tip="f" * 40,
        )
        first_payload = {
            "company_id": config.company_id,
            "project_id": config.project_id,
            "lead_agent_id": "lead-1",
            "lead_issue_id": "lead-issue",
            "lead_run_id": "lead-run-1",
            "stage_key": "MEM8",
            "source_issue_id": "source-1",
            "source_version": 1,
            "review_issue_id": "review-1",
            "review_version": 1,
            "predecessor_operation_key": None,
            "predecessor_source_status": None,
            "predecessor_source_version": None,
            "predecessor_review_status": None,
            "predecessor_review_version": None,
            "predecessor_source_issue_id": None,
            "predecessor_review_issue_id": None,
            "predecessor_dependency_ready": None,
            "source_instruction_sha256": "1" * 64,
            "review_instruction_sha256": "2" * 64,
        }
        first = dispatch(config, {"action": "bind_source_stage", "payload": first_payload})
        first_journal_key = _stage_journal_key("MEM8")
        first_journal_path = config.bridge_journal_dir / f"operation-{first_journal_key}.json"
        first_record = _load_journal(config, first_journal_key)
        first_record.update(
            {"state": "active", "operation_key": "f" * 64, "candidate_sha": "b" * 40}
        )
        _write_journal(config, first_journal_key, first_record, create=False)
        first_bytes = first_journal_path.read_bytes()
        snapshot_sha = "c" * 64
        rejected_operation = {
            "schema": "paperclip-autonomous-operation-v1",
            "operation_key": "f" * 64,
            "state": "review_rejected",
            "source_stage_binding": first["stage_binding_key"],
            "source_snapshot_sha256": snapshot_sha,
            "source_files": [
                {"path_id": path, "size": 1, "sha256": "d" * 64} for path in SOURCE_PATHS
            ],
            "candidate": {"operation_key": "f" * 64, "candidate_sha": "b" * 40},
            "review": {"approved": False},
            "canonical": {
                "status": "prepared",
                "candidate": "9" * 40,
                "receipt_digest": "8" * 64,
            },
        }
        _write_journal(config, "f" * 64, rejected_operation, create=True)
        retry_payload = {
            **first_payload,
            "lead_run_id": "lead-run-2",
            "source_issue_id": "source-2",
            "source_version": 2,
            "review_issue_id": "review-2",
            "review_version": 2,
            "source_instruction_sha256": "5" * 64,
            "review_instruction_sha256": "6" * 64,
            "attempt": 2,
            "retry_of_operation_key": "f" * 64,
            "retry_of_source_status": "done",
            "retry_of_source_version": 3,
            "retry_of_review_status": "done",
            "retry_of_review_version": 3,
            "retry_of_dependency_ready": True,
        }
        with patch(
            "engineering.paperclip_autonomous_bridge._snapshot_rejected_workspace",
            return_value=(snapshot_sha, "e" * 64),
        ):
            second = dispatch(config, {"action": "bind_source_stage", "payload": retry_payload})
        self.assertEqual(first_journal_path.read_bytes(), first_bytes)
        self.assertNotEqual(first["stage_binding_key"], second["stage_binding_key"])
        self.assertEqual(second["attempt"], 2)
        second_key = _stage_journal_key("MEM8", 2)
        second_record = _load_journal(config, second_key)
        self.assertEqual(second_record["retry_of_operation_key"], "f" * 64)
        self.assertEqual(second_record["base_sha"], first_record["base_sha"])
        lookup = dispatch(
            config,
            {
                "action": "lookup_source_stage_binding",
                "payload": {
                    "company_id": config.company_id,
                    "project_id": config.project_id,
                    "lead_agent_id": "lead-1",
                    "lead_issue_id": "lead-issue",
                    "stage_key": "MEM8",
                    "attempt": 2,
                },
            },
        )
        self.assertEqual(lookup["stage_binding_key"], second["stage_binding_key"])
        self.assertEqual(lookup["retry_of_operation_key"], "f" * 64)
        second_record["state"] = "active"
        second_record["operation_key"] = "7" * 64
        second_record["candidate_sha"] = "c" * 40
        _write_journal(config, second_key, second_record, create=False)
        _write_journal(
            config,
            "7" * 64,
            {
                "schema": "paperclip-autonomous-operation-v1",
                "operation_key": "7" * 64,
                "state": "review_rejected",
                "source_stage_binding": second["stage_binding_key"],
                "review": {"approved": False},
                "candidate": {"operation_key": "7" * 64, "candidate_sha": "c" * 40},
                "canonical": {
                    "status": "prepared",
                    "candidate": "8" * 40,
                    "receipt_digest": "6" * 64,
                },
            },
            create=True,
        )
        second_stage_bytes_before_attempt_three = (
            config.bridge_journal_dir / f"operation-{second_key}.json"
        ).read_bytes()
        rejected_operation_bytes_before_attempt_three = (
            config.bridge_journal_dir / f"operation-{'7' * 64}.json"
        ).read_bytes()
        attempt_three_payload = {
            **first_payload,
            "lead_run_id": "lead-run-3",
            "source_issue_id": "source-3",
            "source_version": 3,
            "review_issue_id": "review-3",
            "review_version": 3,
            "source_instruction_sha256": "7" * 64,
            "review_instruction_sha256": "8" * 64,
            "attempt": 3,
            "retry_of_operation_key": "7" * 64,
            "retry_of_source_status": "done",
            "retry_of_source_version": 5,
            "retry_of_review_status": "done",
            "retry_of_review_version": 5,
            "retry_of_dependency_ready": True,
        }
        for invalid_retry_field in (
            {"retry_of_dependency_ready": False},
            {"retry_of_dependency_ready": None},
            {"retry_of_source_version": True},
            {"retry_of_review_version": -1},
        ):
            with self.subTest(invalid_retry_field=invalid_retry_field):
                with self.assertRaisesRegex(BridgeError, "source_stage_retry_validation_invalid"):
                    dispatch(
                        config,
                        {
                            "action": "bind_source_stage",
                            "payload": {**attempt_three_payload, **invalid_retry_field},
                        },
                    )
        with self.assertRaisesRegex(BridgeError, "source_stage_retry_not_admitted"):
            dispatch(
                config,
                {
                    "action": "bind_source_stage",
                    "payload": {**attempt_three_payload, "retry_of_operation_key": "f" * 64},
                },
            )

        def ref_reader(projected_sha, canonical_sha):
            return SimpleNamespace(
                _ref_sha=lambda repo, _ref, missing_ok=False: (
                    projected_sha if repo == config.publisher_repo else canonical_sha
                )
            )

        for reader, error in (
            (ref_reader("0" * 40, "f" * 40), "source_stage_retry_projected_base_changed"),
            (ref_reader("a" * 40, "0" * 40), "source_stage_retry_canonical_base_changed"),
        ):
            with (
                patch.object(BridgeConfig, "gate", return_value=reader),
                patch(
                    "engineering.paperclip_autonomous_bridge._snapshot_rejected_workspace"
                ) as snapshot_retry,
            ):
                with self.assertRaisesRegex(BridgeError, error):
                    dispatch(
                        config,
                        {"action": "bind_source_stage", "payload": attempt_three_payload},
                    )
            snapshot_retry.assert_not_called()
            self.assertFalse(
                (
                    config.bridge_journal_dir / f"operation-{_stage_journal_key('MEM8', 3)}.json"
                ).exists()
            )
        reader = ref_reader("a" * 40, "f" * 40)
        with (
            patch.object(BridgeConfig, "gate", return_value=reader),
            patch(
                "engineering.paperclip_autonomous_bridge._snapshot_rejected_workspace",
                return_value=("9" * 64, "a" * 64),
            ) as snapshot_retry,
        ):
            third = dispatch(
                config, {"action": "bind_source_stage", "payload": attempt_three_payload}
            )
        self.assertEqual(third["attempt"], 3)
        self.assertEqual(third["base_sha"], first_record["base_sha"])
        self.assertEqual(third["canonical_base_sha"], first_record["canonical_base_sha"])
        third_key = _stage_journal_key("MEM8", 3)
        third_record = _load_journal(config, third_key)
        self.assertEqual(third_record["retry_of_operation_key"], "7" * 64)
        self.assertEqual(third_record["retry_of_attempt"], 2)
        self.assertEqual(third_record["retry_of_stage_binding_key"], second["stage_binding_key"])
        snapshot_retry.assert_called_once()
        self.assertEqual(
            (config.bridge_journal_dir / f"operation-{second_key}.json").read_bytes(),
            second_stage_bytes_before_attempt_three,
        )
        self.assertEqual(first_journal_path.read_bytes(), first_bytes)
        self.assertEqual(
            (config.bridge_journal_dir / f"operation-{'7' * 64}.json").read_bytes(),
            rejected_operation_bytes_before_attempt_three,
        )
        third_lookup = dispatch(
            config,
            {
                "action": "lookup_source_stage_binding",
                "payload": {
                    "company_id": config.company_id,
                    "project_id": config.project_id,
                    "lead_agent_id": "lead-1",
                    "lead_issue_id": "lead-issue",
                    "stage_key": "MEM8",
                    "attempt": 3,
                },
            },
        )
        self.assertEqual(third_lookup["stage_binding_key"], third["stage_binding_key"])
        self.assertEqual(third_lookup["retry_of_attempt"], 2)
        with self.assertRaisesRegex(BridgeError, "source_stage_attempt_invalid"):
            dispatch(
                config,
                {"action": "bind_source_stage", "payload": {**attempt_three_payload, "attempt": 4}},
            )
        stage_three_lookup = dispatch(
            config,
            {
                "action": "lookup_source_stage_binding",
                "payload": {
                    "company_id": config.company_id,
                    "project_id": config.project_id,
                    "lead_agent_id": "lead-1",
                    "lead_issue_id": "lead-issue",
                    "stage_key": "MEM9",
                    "attempt": 3,
                },
            },
        )
        self.assertEqual(stage_three_lookup, {"found": False, "attempt": 3})
        third_record["state"] = "active"
        third_record["operation_key"] = "9" * 64
        third_record["candidate_sha"] = "e" * 40
        _write_journal(config, third_key, third_record, create=False)
        _write_journal(
            config,
            "9" * 64,
            {
                "schema": "paperclip-autonomous-operation-v1",
                "operation_key": "9" * 64,
                "state": "published",
                "source_stage_binding": third["stage_binding_key"],
                "candidate": {"operation_key": "9" * 64, "candidate_sha": "e" * 40},
                "canonical": {"status": "published", "candidate": "b" * 40},
            },
            create=True,
        )
        second_stage_payload = {
            **first_payload,
            "lead_run_id": "lead-run-4",
            "stage_key": "MEM9",
            "source_issue_id": "source-4",
            "review_issue_id": "review-4",
            "predecessor_operation_key": "9" * 64,
            "predecessor_source_status": "done",
            "predecessor_source_version": 6,
            "predecessor_review_status": "done",
            "predecessor_review_version": 6,
            "predecessor_source_issue_id": "source-3",
            "predecessor_review_issue_id": "review-3",
            "predecessor_dependency_ready": True,
        }
        trusted_cache = Path(self.tmp.name) / "trusted-stage-cache"
        with (
            patch(
                "engineering.paperclip_autonomous_bridge._create_trusted_stage_source_cache",
                return_value=(trusted_cache, "a" * 64),
            ),
            patch(
                "engineering.paperclip_autonomous_bridge._trusted_stage_source_cache_identity",
                return_value="a" * 64,
            ),
        ):
            next_stage = dispatch(
                config, {"action": "bind_source_stage", "payload": second_stage_payload}
            )
        self.assertEqual(next_stage["base_sha"], "e" * 40)
        self.assertEqual(next_stage["canonical_base_sha"], "b" * 40)
        self.assertEqual(next_stage["attempt"], 1)
        next_record = _load_journal(config, _stage_journal_key("MEM9"))
        self.assertEqual(next_record["predecessor_operation_key"], "9" * 64)
        self.assertNotIn("retry_of_operation_key", next_record)
        with self.assertRaisesRegex(BridgeError, "source_stage_retry_not_admitted"):
            dispatch(
                config,
                {
                    "action": "bind_source_stage",
                    "payload": {
                        **second_stage_payload,
                        "lead_run_id": "lead-run-5",
                        "source_issue_id": "source-5",
                        "review_issue_id": "review-5",
                        "attempt": 3,
                        "retry_of_operation_key": "7" * 64,
                        "retry_of_source_status": "done",
                        "retry_of_source_version": 5,
                        "retry_of_review_status": "done",
                        "retry_of_review_version": 5,
                        "retry_of_dependency_ready": True,
                    },
                },
            )
        with self.assertRaisesRegex(BridgeError, "source_stage_retry_not_admitted"):
            dispatch(
                config,
                {
                    "action": "bind_source_stage",
                    "payload": {**retry_payload, "retry_of_operation_key": "9" * 64},
                },
            )
        with self.assertRaisesRegex(BridgeError, "source_stage_attempt_invalid"):
            dispatch(
                config,
                {"action": "bind_source_stage", "payload": {**retry_payload, "attempt": 4}},
            )

    def test_content_preflight_failure_is_immutable_and_changed_bytes_can_pass_before_prepare(self):
        from engineering.paperclip_autonomous_bridge import _preflight_source_stage_content
        from engineering.paperclip_source_delivery import SOURCE_PATHS, snapshot_records

        scratch = Path(self.tmp.name) / "preflight-scratch"
        scratch.mkdir()
        config = replace(
            self.config,
            verifier_scratch_root=scratch,
            bridge_journal_dir=Path(self.tmp.name) / "bridge-journal",
            broker_journal_dir=Path(self.tmp.name) / "broker-journal",
        )
        config.broker_journal_dir.mkdir(mode=0o700, exist_ok=True)
        binding = {
            "stage_binding_key": "d" * 64,
            "base_sha": "a" * 40,
            "canonical_base_sha": None,
        }
        payload = {"source_issue_id": "source-8", "run_id": "source-run"}

        def records(contents):
            return [
                {
                    "path_id": path,
                    "content": content,
                    "size": len(content.encode()),
                    "sha256": hashlib.sha256(content.encode()).hexdigest(),
                }
                for path, content in zip(SOURCE_PATHS, contents, strict=True)
            ]

        failed_result = {
            "status": "preflight_failed",
            "failure_code": "sandboxed_verifier_failed",
            "command_index": 0,
            "returncode": 1,
            "timed_out": False,
            "output_truncated": False,
            "stdout": "AssertionError: expected boundary",
            "stderr": "",
            "stdout_sha256": "1" * 64,
            "stderr_sha256": "2" * 64,
            "sandbox_profile_sha256": "3" * 64,
            "prior_commands_sha256": "4" * 64,
        }
        checkout = Path(self.tmp.name) / "candidate"
        projection = SimpleNamespace(checkout=checkout, submitted_sha="b" * 40)
        with (
            patch.object(BridgeConfig, "gate", return_value=SimpleNamespace()),
            patch(
                "engineering.paperclip_autonomous_bridge.materialize_owner_projection",
                return_value=projection,
            ) as materialize,
            patch("engineering.paperclip_autonomous_bridge.discard_projection"),
            patch(
                "engineering.paperclip_autonomous_bridge._verifier",
                return_value=(lambda *_args: failed_result, scratch / "unused-verifier-scratch"),
            ) as verifier,
        ):
            failed = _preflight_source_stage_content(
                config,
                config,
                binding,
                payload,
                snapshot_records(records(["bad events", "bad tests"])),
            )
            replay = _preflight_source_stage_content(
                config,
                config,
                binding,
                payload,
                snapshot_records(records(["bad events", "bad tests"])),
            )
        self.assertEqual(failed["status"], "preflight_failed")
        self.assertEqual(failed["stdout"], failed_result["stdout"])
        self.assertEqual(replay, failed)
        self.assertNotIn("operation_key", failed)
        self.assertNotIn("candidate_sha", failed)
        self.assertEqual(verifier.call_count, 1)
        candidate_parent = materialize.call_args.kwargs["checkout_parent"]
        self.assertEqual(candidate_parent.parent, scratch.resolve())
        self.assertNotEqual(candidate_parent, config.source_repo)
        changed_snapshot = snapshot_records(records(["corrected events", "corrected tests"]))
        with (
            patch.object(BridgeConfig, "gate", return_value=SimpleNamespace()),
            patch(
                "engineering.paperclip_autonomous_bridge.materialize_owner_projection",
                return_value=projection,
            ),
            patch("engineering.paperclip_autonomous_bridge.discard_projection"),
            patch(
                "engineering.paperclip_autonomous_bridge._verifier",
                return_value=(
                    lambda *_args: {"status": "passed", "commands_sha256": "5" * 64},
                    scratch / "unused",
                ),
            ) as passing_verifier,
        ):
            passed = _preflight_source_stage_content(
                config, config, binding, payload, changed_snapshot
            )
        self.assertEqual(passed["status"], "passed")
        self.assertNotEqual(passed["content_check_id"], failed["content_check_id"])
        self.assertEqual(passing_verifier.call_count, 1)
        preflight_root = config.bridge_journal_dir / "source-preflight-v1"
        self.assertEqual(len(list(preflight_root.glob("snapshot-*.json"))), 2)
        self.assertEqual(len(list(preflight_root.glob("result-*.json"))), 2)
        self.assertFalse(list(config.bridge_journal_dir.glob("operation-*.json")))

    def test_content_preflight_unknown_intent_is_not_reexecuted(self):
        from engineering.paperclip_autonomous_bridge import _preflight_source_stage_content
        from engineering.paperclip_source_delivery import SOURCE_PATHS, snapshot_records

        scratch = Path(self.tmp.name) / "preflight-scratch"
        scratch.mkdir()
        config = replace(
            self.config,
            verifier_scratch_root=scratch,
            bridge_journal_dir=Path(self.tmp.name) / "bridge-journal",
            broker_journal_dir=Path(self.tmp.name) / "broker-journal",
        )
        config.broker_journal_dir.mkdir(mode=0o700, exist_ok=True)
        records = [
            {
                "path_id": path,
                "content": "synthetic\n",
                "size": 10,
                "sha256": hashlib.sha256(b"synthetic\n").hexdigest(),
            }
            for path in SOURCE_PATHS
        ]
        snapshot = snapshot_records(records)
        binding = {"stage_binding_key": "d" * 64, "base_sha": "a" * 40, "canonical_base_sha": None}
        payload = {"source_issue_id": "source-8", "run_id": "source-run"}
        projection = SimpleNamespace(
            checkout=Path(self.tmp.name) / "candidate", submitted_sha="b" * 40
        )
        with (
            patch.object(BridgeConfig, "gate", return_value=SimpleNamespace()),
            patch(
                "engineering.paperclip_autonomous_bridge.materialize_owner_projection",
                return_value=projection,
            ) as materialize,
            patch("engineering.paperclip_autonomous_bridge.discard_projection"),
            patch(
                "engineering.paperclip_autonomous_bridge._verifier",
                side_effect=BridgeError("sandbox_unavailable"),
            ) as verifier,
        ):
            first = _preflight_source_stage_content(config, config, binding, payload, snapshot)
            second = _preflight_source_stage_content(config, config, binding, payload, snapshot)
        self.assertEqual(first["status"], "preflight_unknown")
        self.assertEqual(first, second)
        self.assertEqual(materialize.call_count, 1)
        self.assertEqual(verifier.call_count, 1)
        self.assertFalse(list(config.bridge_journal_dir.glob("operation-*.json")))

    def test_stage_delivery_accepts_exact_worker_envelope_and_derives_reviewer_from_binding(self):
        from engineering.paperclip_source_delivery import SOURCE_PATHS

        stages = (
            SourceStageSpec("MEM8", "source", "a" * 40, None, SOURCE_PATHS, "policy-mem8"),
            SourceStageSpec("MEM9", "published", None, "MEM8", SOURCE_PATHS, "policy-mem9"),
        )
        config = replace(
            self.config,
            source_issue_id=None,
            reviewer_issue_id=None,
            worker_checkout=None,
            expected_base_sha="a" * 40,
            finite_lead_agent_id="lead-1",
            finite_lead_issue_id="lead-issue",
            source_stage_catalog=stages,
        )
        stage_payload = {
            "company_id": config.company_id,
            "project_id": config.project_id,
            "lead_agent_id": "lead-1",
            "lead_issue_id": "lead-issue",
            "lead_run_id": "lead-run",
            "stage_key": "MEM8",
            "source_issue_id": "source-8",
            "source_version": 1,
            "review_issue_id": "review-8",
            "review_version": 1,
            "predecessor_operation_key": None,
            "predecessor_source_status": None,
            "predecessor_source_version": None,
            "predecessor_review_status": None,
            "predecessor_review_version": None,
            "predecessor_source_issue_id": None,
            "predecessor_review_issue_id": None,
            "predecessor_dependency_ready": None,
            "source_instruction_sha256": "1" * 64,
            "review_instruction_sha256": "2" * 64,
        }
        with self.assertRaisesRegex(BridgeError, "source_stage_lead_binding_mismatch"):
            dispatch(
                config,
                {
                    "action": "bind_source_stage",
                    "payload": {**stage_payload, "lead_issue_id": "other-lead"},
                },
            )
        bound = dispatch(config, {"action": "bind_source_stage", "payload": stage_payload})
        for payload in (
            {
                "step": "intent",
                "stage_binding_key": bound["stage_binding_key"],
                "issue_id": "source-8",
                "observed_status": "backlog",
                "observed_version": 1,
            },
            {
                "step": "ack",
                "stage_binding_key": bound["stage_binding_key"],
                "issue_id": "source-8",
                "mutation_status": "todo",
                "mutation_status_version": 2,
            },
        ):
            dispatch(config, {"action": "source_stage_activation", "payload": payload})
        files = [
            {
                "path_id": path,
                "content": "synthetic complete content\n",
                "size": 27,
                "sha256": hashlib.sha256(b"synthetic complete content\n").hexdigest(),
            }
            for path in SOURCE_PATHS
        ]
        worker_envelope = {
            "company_id": config.company_id,
            "project_id": config.project_id,
            "source_issue_id": "source-8",
            "worker_agent_id": config.worker_agent_id,
            "run_id": "source-run",
            "stage_key": "MEM8",
            "stage_binding_key": bound["stage_binding_key"],
            "source_instruction_sha256": "1" * 64,
            "files": files,
        }
        with (
            patch("engineering.paperclip_autonomous_bridge._snapshot_base_hashes", return_value={path: "b" * 40 for path in SOURCE_PATHS}),
            patch("engineering.paperclip_autonomous_bridge._preflight_source_stage_content") as preflight,
        ):
            unchanged = dispatch(config, {"action": "deliver_source_stage_content", "payload": worker_envelope})
        self.assertEqual(unchanged["status"], "unchanged_not_submitted")
        self.assertEqual(unchanged["baseline_action"], "verify_source_stage_unchanged")
        self.assertEqual(set(unchanged["path_hashes"]), set(SOURCE_PATHS))
        self.assertNotIn("candidate_sha", unchanged)
        preflight.assert_not_called()
        with self.assertRaisesRegex(BridgeError, "source_stage_instruction_binding_mismatch"):
            dispatch(
                config,
                {
                    "action": "deliver_source_stage_content",
                    "payload": {**worker_envelope, "source_instruction_sha256": "9" * 64},
                },
            )
        review_envelope = {
            "company_id": config.company_id,
            "project_id": config.project_id,
            "reviewer_agent_id": config.reviewer_agent_id,
            "reviewer_run_id": "reviewer-run",
            "reviewer_issue_id": "review-8",
            "stage_key": "MEM8",
            "stage_binding_key": bound["stage_binding_key"],
            "review_instruction_sha256": "9" * 64,
        }
        with self.assertRaisesRegex(BridgeError, "source_stage_reviewer_binding_mismatch"):
            dispatch(config, {"action": "inspect_bound_source_stage", "payload": review_envelope})
        with self.assertRaisesRegex(BridgeError, "source_stage_reviewer_binding_mismatch"):
            dispatch(
                config,
                {
                    "action": "approve_bound_source_stage",
                    "payload": {
                        **review_envelope,
                        "approved": True,
                        "evidence": {"inspection_digest": "3" * 64},
                    },
                },
            )
        result = {
            "operation_key": "f" * 64,
            "candidate_sha": "c" * 40,
            "base_sha": "a" * 40,
        }
        with (
            patch(
                "engineering.paperclip_autonomous_bridge._preflight_source_stage_content",
                return_value={"status": "passed", "content_check_id": "e" * 64},
            ),
            patch(
                "engineering.paperclip_autonomous_bridge.prepare_source_content",
                return_value=result,
            ) as prepare,
        ):
            delivered = dispatch(
                config, {"action": "deliver_source_stage_content", "payload": worker_envelope}
            )
        self.assertEqual(delivered, result)
        self.assertEqual(
            set(worker_envelope),
            {
                "company_id",
                "project_id",
                "source_issue_id",
                "worker_agent_id",
                "run_id",
                "stage_key",
                "stage_binding_key",
                "source_instruction_sha256",
                "files",
            },
        )
        self.assertNotIn("review_issue_id", worker_envelope)
        delegated_config, delegated_payload = prepare.call_args.args[:2]
        self.assertEqual(delegated_config.reviewer_issue_id, "review-8")
        self.assertEqual(delegated_payload["base_sha"], "a" * 40)
        self.assertEqual(prepare.call_args.kwargs["preflight_check_id"], "e" * 64)
        self.assertEqual(
            set(delegated_payload),
            {
                "company_id",
                "project_id",
                "source_issue_id",
                "worker_agent_id",
                "run_id",
                "files",
                "base_sha",
            },
        )

    def test_unchanged_base_verifier_is_distinct_and_refuses_stale_tip(self):
        from engineering.paperclip_autonomous_bridge import (
            _stage_journal_key, verify_source_stage_unchanged,
        )
        root = Path(self.tmp.name)
        repo = root / "accepted-source"
        repo.mkdir()
        subprocess.run(["/usr/bin/git", "init", "-q", "-b", "brain/paperclip-integration", str(repo)], check=True)
        paths = ("src/example.py", "tests/test_example.py")
        for path, content in zip(paths, ("VALUE = 1\n", "def test_value(): assert True\n"), strict=True):
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        git = ["/usr/bin/git", "-C", str(repo)]
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "-c", "user.name=owner", "-c", "user.email=owner@example.invalid", "commit", "-m", "accepted"], check=True, capture_output=True)
        base = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        stage = SourceStageSpec("NOCHANGE-1", "source", base, None, paths, "policy-test")
        profile = VerifierProfile("policy-test", (("/usr/bin/python3", "-c", "pass"),), (), ())
        config = replace(self.config, source_repo=repo, publisher_repo=repo, worker_checkout=None,
            source_issue_id=None, reviewer_issue_id=None, expected_base_sha=base,
            fixed_dev_ref="refs/heads/brain/paperclip-integration", expected_dev_ref="refs/heads/brain/paperclip-integration",
            source_stage_catalog=(stage,), verifier_policy_catalog=(profile,),
            finite_lead_agent_id="lead-1", finite_lead_issue_id="lead-issue",
            bridge_journal_dir=root / "bridge", verifier_scratch_root=root / "scratch")
        config.bridge_journal_dir.mkdir(mode=0o700)
        key = "d" * 64
        record = {"schema": "paperclip-source-stage-binding-v1", "stage_key": stage.stage_key,
            "stage_binding_key": key, "source_issue_id": "source-1", "review_issue_id": "review-1",
            "lead_issue_id": "lead-issue", "lead_agent_id": "lead-1", "lead_run_id": "lead-run",
            "source_version": 1, "review_version": 1, "source_instruction_sha256": "1" * 64,
            "review_instruction_sha256": "2" * 64, "workspace_id": "source", "path_ids": list(paths),
            "verifier_policy_id": "policy-test", "owner_config_digest": config.owner_config_digest,
            "base_sha": base, "canonical_base_sha": None, "state": "active", "source_intent": None,
            "operation_key": None, "candidate_sha": None}
        from engineering.paperclip_autonomous_bridge import _write_journal
        _write_journal(config, _stage_journal_key(stage.stage_key), record, create=True)
        payload = {"company_id": config.company_id, "project_id": config.project_id,
            "worker_agent_id": config.worker_agent_id, "run_id": "source-run", "source_issue_id": "source-1",
            "stage_key": stage.stage_key, "stage_binding_key": key, "source_instruction_sha256": "1" * 64}
        fake_scratch = root / "scratch" / "fake-verifier"
        fake_scratch.mkdir(parents=True)
        with patch("engineering.paperclip_autonomous_bridge._verifier", return_value=(lambda *_args: {"status": "passed", "commands": []}, fake_scratch)):
            result = verify_source_stage_unchanged(config, payload)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["base_sha"], base)
        self.assertEqual(set(result["path_hashes"]), set(paths))
        self.assertNotIn("candidate_sha", result)
        self.assertEqual(verify_source_stage_unchanged(config, payload), result)
        review_payload = {"company_id": config.company_id, "project_id": config.project_id,
            "reviewer_agent_id": config.reviewer_agent_id, "reviewer_run_id": "qa-run",
            "reviewer_issue_id": "review-1", "stage_key": stage.stage_key,
            "stage_binding_key": key, "review_instruction_sha256": "2" * 64}
        inspection = dispatch(config, {"action": "inspect_source_stage_no_change", "payload": review_payload})
        merits = dispatch(config, {"action": "record_source_stage_no_change_merits", "payload": {
            **review_payload, "merits": True,
            "evidence": {"inspection_digest": inspection["inspection_digest"], "rationale": "The accepted baseline meets the scoped product requirement."},
        }})
        self.assertTrue(merits["merits"])
        self.assertEqual(merits["receipt_digest"], result["receipt_digest"])
        self.assertNotIn("operation_key", merits)
        self.assertNotIn("candidate_sha", merits)
        (repo / paths[0]).write_text("VALUE = 2\n", encoding="utf-8")
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "-c", "user.name=owner", "-c", "user.email=owner@example.invalid", "commit", "-m", "tip drift"], check=True, capture_output=True)
        with self.assertRaisesRegex(BridgeError, "source_stage_accepted_tip_changed"):
            verify_source_stage_unchanged(config, payload)

    def test_inspection_binds_passed_preflight_and_final_verifier_failure_holds_both_refs(self):
        from engineering.paperclip_autonomous_bridge import _load_journal, _write_journal
        from engineering.paperclip_git_broker import BrokerError
        from engineering.paperclip_source_delivery import SOURCE_PATHS

        root = Path(self.tmp.name) / "inspection-phase"
        source = root / "source"
        publisher = root / "publisher"
        canonical = root / "canonical"
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
        for directory in (
            root / "broker-journal",
            root / "bridge-journal",
            root / "receipts",
            root / "scratch",
        ):
            directory.mkdir(mode=0o700)
        subprocess.run(["/usr/bin/git", "init", "-q", "-b", "main", str(source)], check=True)
        git(source, "config", "user.name", "Inspection Owner")
        git(source, "config", "user.email", "inspection-owner@example.invalid")
        for path in SOURCE_PATHS:
            target = source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("owner baseline\n")
        git(source, "add", "-A")
        git(source, "commit", "-qm", "initial source base")
        base = git(source, "rev-parse", "HEAD")
        git(source, "branch", "brain/development", base)
        for clone in (publisher, canonical):
            subprocess.run(["/usr/bin/git", "clone", "-q", str(source), str(clone)], check=True)
            git(clone, "config", "user.name", "Inspection Publisher")
            git(clone, "config", "user.email", "inspection-publisher@example.invalid")
            git(clone, "branch", "brain/development", base)
        git(publisher, "checkout", "--detach", base)

        config = replace(
            self.config,
            source_issue_id=None,
            reviewer_issue_id=None,
            worker_checkout=None,
            source_repo=source,
            publisher_repo=publisher,
            broker_journal_dir=root / "broker-journal",
            bridge_journal_dir=root / "bridge-journal",
            receipt_store=root / "receipts",
            verifier_scratch_root=root / "scratch",
            python_executable=Path(sys.executable).resolve(),
            allowed_scope=SOURCE_PATHS,
            verifier_commands=((sys.executable, "-c", "raise SystemExit(0)"),),
            verifier_env=(),
            verifier_policy_id="inspection-policy",
            fixed_dev_ref="refs/heads/brain/development",
            expected_dev_ref="refs/heads/brain/development",
            expected_base_sha=base,
            finite_lead_agent_id="lead-1",
            finite_lead_issue_id="lead-issue",
            source_stage_catalog=(
                SourceStageSpec("MEM8", "source", base, None, SOURCE_PATHS, "inspection-policy"),
                SourceStageSpec(
                    "MEM9", "published", None, "MEM8", SOURCE_PATHS, "inspection-policy"
                ),
            ),
            canonical_repo=canonical,
            canonical_ref="refs/heads/brain/development",
            canonical_expected_tip=base,
        )
        bind_payload = {
            "company_id": config.company_id,
            "project_id": config.project_id,
            "lead_agent_id": "lead-1",
            "lead_issue_id": "lead-issue",
            "lead_run_id": "lead-run",
            "stage_key": "MEM8",
            "source_issue_id": "source-8",
            "source_version": 1,
            "review_issue_id": "review-8",
            "review_version": 1,
            "predecessor_operation_key": None,
            "predecessor_source_status": None,
            "predecessor_source_version": None,
            "predecessor_review_status": None,
            "predecessor_review_version": None,
            "predecessor_source_issue_id": None,
            "predecessor_review_issue_id": None,
            "predecessor_dependency_ready": None,
            "source_instruction_sha256": "1" * 64,
            "review_instruction_sha256": "2" * 64,
        }
        bound = dispatch(config, {"action": "bind_source_stage", "payload": bind_payload})
        dispatch(
            config,
            {
                "action": "source_stage_activation",
                "payload": {
                    "step": "intent",
                    "stage_binding_key": bound["stage_binding_key"],
                    "issue_id": "source-8",
                    "observed_status": "backlog",
                    "observed_version": 1,
                },
            },
        )
        dispatch(
            config,
            {
                "action": "source_stage_activation",
                "payload": {
                    "step": "ack",
                    "stage_binding_key": bound["stage_binding_key"],
                    "issue_id": "source-8",
                    "mutation_status": "todo",
                    "mutation_status_version": 2,
                },
            },
        )
        contents = ("events = ['candidate']\n", "def test_raw_sources():\n    assert True\n")
        files = [
            {
                "path_id": path,
                "content": content,
                "size": len(content.encode("utf-8")),
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
            for path, content in zip(SOURCE_PATHS, contents, strict=True)
        ]

        def verifier_factory(_config, _checkout, *, content_only=False, capture_failure=False):
            if capture_failure:
                return lambda *_args: {"status": "passed", "commands_sha256": "a" * 64}, None

            def fail_final_verifier(*_args):
                raise BridgeError("sandboxed_verifier_failed")

            return fail_final_verifier, None

        with patch(
            "engineering.paperclip_autonomous_bridge._verifier", side_effect=verifier_factory
        ):
            prepared = dispatch(
                config,
                {
                    "action": "deliver_source_stage_content",
                    "payload": {
                        "company_id": config.company_id,
                        "project_id": config.project_id,
                        "source_issue_id": "source-8",
                        "worker_agent_id": config.worker_agent_id,
                        "run_id": "source-run",
                        "stage_key": "MEM8",
                        "stage_binding_key": bound["stage_binding_key"],
                        "source_instruction_sha256": "1" * 64,
                        "files": files,
                    },
                },
            )
            operation = _load_journal(config, prepared["operation_key"])
            legacy_inspection = {
                "reviewer_agent_id": config.reviewer_agent_id,
                "reviewer_issue_id": "review-8",
                "reviewer_run_id": "reviewer-run-old",
                "candidate_sha": prepared["candidate_sha"],
                "policy_digest": operation["binding"]["policy_digest"],
                "legacy_marker": "preserve byte-for-byte",
            }
            operation["inspections"] = [legacy_inspection]
            _write_journal(config, prepared["operation_key"], operation, create=False)
            operation_bytes_before = _load_journal(config, prepared["operation_key"])
            review_envelope = {
                "company_id": config.company_id,
                "project_id": config.project_id,
                "reviewer_agent_id": config.reviewer_agent_id,
                "reviewer_run_id": "reviewer-run-old",
                "reviewer_issue_id": "review-8",
                "stage_key": "MEM8",
                "stage_binding_key": bound["stage_binding_key"],
                "review_instruction_sha256": "2" * 64,
            }
            with self.assertRaisesRegex(BridgeError, "inspection_replay_mismatch"):
                dispatch(
                    config, {"action": "inspect_bound_source_stage", "payload": review_envelope}
                )
            after_old_replay = _load_journal(config, prepared["operation_key"])
            self.assertEqual(after_old_replay["inspections"], [legacy_inspection])
            self.assertIsNone(after_old_replay["review"])
            self.assertEqual(after_old_replay["state"], "prepared")

            from engineering.paperclip_autonomous_bridge import _load_preflight_json

            original_loader = _load_preflight_json

            def foreign_receipt_loader(cfg, kind, check_id):
                value = original_loader(cfg, kind, check_id)
                if kind == "result":
                    return {**value, "policy_digest": "f" * 64}
                return value

            with patch(
                "engineering.paperclip_autonomous_bridge._load_preflight_json",
                side_effect=foreign_receipt_loader,
            ):
                with self.assertRaisesRegex(BridgeError, "source_preflight_receipt_mismatch"):
                    dispatch(
                        config,
                        {
                            "action": "inspect_bound_source_stage",
                            "payload": {
                                **review_envelope,
                                "reviewer_run_id": "reviewer-run-foreign",
                            },
                        },
                    )

            def missing_receipt_loader(cfg, kind, check_id):
                if kind == "result":
                    raise BridgeError("owner_file_unavailable")
                return original_loader(cfg, kind, check_id)

            with patch(
                "engineering.paperclip_autonomous_bridge._load_preflight_json",
                side_effect=missing_receipt_loader,
            ):
                with self.assertRaisesRegex(BridgeError, "source_preflight_receipt_unavailable"):
                    dispatch(
                        config,
                        {
                            "action": "inspect_bound_source_stage",
                            "payload": {
                                **review_envelope,
                                "reviewer_run_id": "reviewer-run-missing",
                            },
                        },
                    )

            inspected = dispatch(
                config,
                {
                    "action": "inspect_bound_source_stage",
                    "payload": {**review_envelope, "reviewer_run_id": "reviewer-run-new"},
                },
            )
            self.assertEqual(inspected["source_preflight"]["status"], "passed")
            self.assertEqual(
                inspected["source_preflight"]["content_check_id"], prepared["preflight_check_id"]
            )
            self.assertEqual(inspected["final_verifier_phase"], "pending_after_independent_QA")
            updated_operation = _load_journal(config, prepared["operation_key"])
            self.assertEqual(updated_operation["inspections"][0], legacy_inspection)
            self.assertIsNone(updated_operation["review"])
            self.assertEqual(updated_operation["state"], "prepared")
            self.assertEqual(updated_operation["candidate"], operation_bytes_before["candidate"])
            self.assertEqual(
                updated_operation["source_snapshot_sha256"],
                operation_bytes_before["source_snapshot_sha256"],
            )
            self.assertEqual(updated_operation["canonical"], operation_bytes_before["canonical"])
            self.assertEqual(len(updated_operation["inspections"]), 2)
            self.assertEqual(
                updated_operation["inspections"][1]["final_verifier_phase"],
                "pending_after_independent_QA",
            )
            with self.assertRaisesRegex(BrokerError, "verification outcome is held"):
                dispatch(
                    config,
                    {
                        "action": "approve_bound_source_stage",
                        "payload": {
                            **review_envelope,
                            "reviewer_run_id": "reviewer-run-new",
                            "approved": True,
                            "evidence": {
                                "inspection_digest": inspected["inspection_digest"],
                                "canonical_candidate_sha": inspected["canonical_candidate_sha"],
                                "canonical_receipt_digest": inspected["canonical_receipt_digest"],
                                "rationale": "exact prepared candidate inspected",
                            },
                        },
                    },
                )
        self.assertEqual(git(publisher, "rev-parse", "refs/heads/brain/development"), base)
        self.assertEqual(git(canonical, "rev-parse", "refs/heads/brain/development"), base)
        final_operation = _load_journal(config, prepared["operation_key"])
        self.assertEqual(final_operation["state"], "review_custodied")
        self.assertEqual(final_operation["canonical"]["status"], "prepared")
        self.assertEqual(final_operation["candidate"]["candidate_sha"], prepared["candidate_sha"])

    def test_published_predecessor_discovery_scans_all_bounded_attempts(self):
        from engineering.paperclip_autonomous_bridge import _lookup_published_stage_predecessor
        from engineering.paperclip_source_delivery import SOURCE_PATHS

        prior_stage = SourceStageSpec(
            "MEM10",
            "source",
            "a" * 40,
            None,
            SOURCE_PATHS,
            "policy-mem10",
            "experience-project",
            "experience-goal",
            "experience-sol",
            "experience-lead",
            canonical_repo=Path(self.tmp.name) / "canonical",
            canonical_ref="refs/heads/org/development",
        )
        successor_stage = SourceStageSpec(
            "MEM11",
            "source",
            None,
            "MEM10",
            SOURCE_PATHS,
            "policy-mem11",
            "cortex-project",
            "cortex-goal",
            "cortex-sol",
            "cortex-lead",
            canonical_repo=Path(self.tmp.name) / "canonical",
            canonical_ref="refs/heads/org/development",
        )
        config = replace(self.config, source_stage_catalog=(prior_stage, successor_stage))
        request = {
            "company_id": config.company_id,
            "project_id": "experience-project",
            "lead_agent_id": "experience-sol",
            "lead_issue_id": "experience-lead",
            "stage_key": "MEM10",
            "published_predecessor_lookup": True,
            "successor_stage_key": "MEM11",
        }
        accepted = {
            "found": True,
            "attempt": 3,
            "state": "active",
            "activation_pending": False,
            "operation_state": "published",
            "operation_review_approved": True,
            "canonical_status": "published",
            "operation_key": "f" * 64,
            "source_issue_id": "mem10-source",
            "review_issue_id": "mem10-review",
            "candidate_sha": "a" * 40,
            "source_receipt_digest": "b" * 64,
            "canonical_candidate_sha": "c" * 40,
            "canonical_receipt_digest": "d" * 64,
            "canonical_approved_receipt_digest": "d" * 64,
            "canonical_receipt_ref": "refs/heads/org/development",
            "stage_binding_key": "e" * 64,
            "operation_source_stage_key": "MEM10",
            "operation_source_stage_binding": "e" * 64,
            "candidate_stage_binding_key": "e" * 64,
        }
        calls = []

        def stage_lookup(_config, payload, **_kwargs):
            calls.append(payload["attempt"])
            return accepted if payload["attempt"] == 3 else {"found": False}

        with patch(
            "engineering.paperclip_autonomous_bridge.lookup_source_stage_binding",
            side_effect=stage_lookup,
        ):
            result = _lookup_published_stage_predecessor(
                config,
                request,
                {"company_id", "project_id", "lead_agent_id", "lead_issue_id", "stage_key"},
            )
        self.assertEqual(calls, [1, 2, 3])

        calls.clear()

        def unknown_lookup(_config, payload, **_kwargs):
            calls.append(payload["attempt"])
            if payload["attempt"] == 1:
                return {**accepted, "attempt": 1, "operation_state": "prepared"}
            return accepted if payload["attempt"] == 3 else {"found": False}

        with patch(
            "engineering.paperclip_autonomous_bridge.lookup_source_stage_binding",
            side_effect=unknown_lookup,
        ):
            with self.assertRaisesRegex(BridgeError, "source_stage_predecessor_state_unknown"):
                _lookup_published_stage_predecessor(
                    config,
                    request,
                    {"company_id", "project_id", "lead_agent_id", "lead_issue_id", "stage_key"},
                )
        self.assertEqual(calls, [1])

        calls.clear()

        def rejected_then_accepted(_config, payload, **_kwargs):
            calls.append(payload["attempt"])
            if payload["attempt"] == 1:
                return {
                    **accepted,
                    "attempt": 1,
                    "operation_state": "review_rejected",
                    "operation_review_approved": False,
                    "canonical_status": "prepared",
                    "canonical_approved_receipt_digest": None,
                }
            return accepted if payload["attempt"] == 3 else {"found": False}

        with patch(
            "engineering.paperclip_autonomous_bridge.lookup_source_stage_binding",
            side_effect=rejected_then_accepted,
        ):
            result = _lookup_published_stage_predecessor(
                config,
                request,
                {"company_id", "project_id", "lead_agent_id", "lead_issue_id", "stage_key"},
            )
        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(result["attempt"], 3)

        calls.clear()

        def ambiguous_lookup(_config, payload, **_kwargs):
            calls.append(payload["attempt"])
            return (
                {**accepted, "attempt": payload["attempt"]}
                if payload["attempt"] in {1, 3}
                else {"found": False}
            )

        with patch(
            "engineering.paperclip_autonomous_bridge.lookup_source_stage_binding",
            side_effect=ambiguous_lookup,
        ):
            with self.assertRaisesRegex(BridgeError, "source_stage_predecessor_ambiguous"):
                _lookup_published_stage_predecessor(
                    config,
                    request,
                    {"company_id", "project_id", "lead_agent_id", "lead_issue_id", "stage_key"},
                )
        self.assertEqual(calls, [1, 2, 3])

    def test_real_rejected_canonical_operation_is_skipped_before_attempt_three_acceptance(self):
        from engineering import paperclip_autonomous_bridge as bridge

        fixtures = sys.modules[__name__]
        original_dispatch = dispatch
        captured = {}

        def reject_qa_approval(config, request):
            if request["action"] == "approve_bound_source_stage":
                request = {
                    **request,
                    "payload": {**request["payload"], "approved": False},
                }
                captured["config"] = config
            result = original_dispatch(config, request)
            if request["action"] == "approve_bound_source_stage":
                captured["result"] = result
            return result

        with patch.object(fixtures, "dispatch", side_effect=reject_qa_approval):
            with self.assertRaises(AssertionError):
                self.test_org_development_stage_prepares_qa_and_publishes_both_refs()
        self.assertEqual(captured["result"]["status"], "review_rejected")
        config = captured["config"]
        request = {
            "company_id": config.company_id,
            "project_id": "experience-project",
            "lead_agent_id": "experience-sol",
            "lead_issue_id": "experience-lead",
            "stage_key": "MEM10",
        }
        rejected = original_dispatch(
            config,
            {
                "action": "lookup_source_stage_binding",
                "payload": {**request, "attempt": 1},
            },
        )
        self.assertEqual(rejected["operation_state"], "review_rejected")
        self.assertIs(rejected["operation_review_approved"], False)
        self.assertEqual(rejected["canonical_status"], "prepared")
        accepted = {
            **rejected,
            "attempt": 3,
            "stage_binding_key": "f" * 64,
            "operation_key": "e" * 64,
            "operation_state": "published",
            "operation_review_approved": True,
            "canonical_status": "published",
            "canonical_approved_receipt_digest": rejected["canonical_receipt_digest"],
            "operation_source_stage_binding": "f" * 64,
            "candidate_stage_binding_key": "f" * 64,
            "source_receipt_digest": "a" * 64,
        }

        def lookup(_config, payload, **_kwargs):
            if payload["attempt"] == 1:
                return rejected
            if payload["attempt"] == 3:
                return accepted
            return {"found": False, "attempt": payload["attempt"]}

        with patch.object(bridge, "lookup_source_stage_binding", side_effect=lookup):
            found = bridge._lookup_published_stage_predecessor(
                config,
                {**request, "published_predecessor_lookup": True, "successor_stage_key": "MEM11"},
                set(request),
            )
        self.assertEqual(found["attempt"], 3)
        self.assertEqual(found["canonical_status"], "published")

    def test_org_development_stage_prepares_qa_and_publishes_both_refs(self):
        from engineering.paperclip_autonomous_bridge import _load_journal
        from engineering.paperclip_source_delivery import SOURCE_PATHS

        root = Path(self.tmp.name) / "org-stage-publication"
        source, publisher, canonical = (
            root / name for name in ("source", "publisher", "canonical")
        )
        root.mkdir()
        for name in ("broker-journal", "bridge-journal", "receipts", "scratch"):
            (root / name).mkdir(mode=0o700)
        subprocess.run(["/usr/bin/git", "init", "-q", "-b", "main", str(source)], check=True)

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

        git(source, "config", "user.name", "Owner")
        git(source, "config", "user.email", "owner@example.invalid")
        for path in SOURCE_PATHS:
            target = source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("owner baseline\n")
        git(source, "add", "-A")
        git(source, "commit", "-qm", "trusted org base")
        base = git(source, "rev-parse", "HEAD")
        git(source, "branch", "org/development", base)
        for repo in (publisher, canonical):
            subprocess.run(["/usr/bin/git", "clone", "-q", str(source), str(repo)], check=True)
            git(repo, "config", "user.name", "Publisher")
            git(repo, "config", "user.email", "publisher@example.invalid")
            git(repo, "branch", "org/development", base)
        git(publisher, "checkout", "--detach", base)
        git(canonical, "checkout", "org/development")

        python = Path(sys.executable).resolve()
        policy_id = "org-stage-policy"
        stage = SourceStageSpec(
            "MEM10",
            "source",
            base,
            None,
            SOURCE_PATHS,
            policy_id,
            "experience-project",
            "engineering-goal",
            "experience-sol",
            "experience-lead",
            source_repo=source,
            publisher_repo=publisher,
            canonical_repo=canonical,
            canonical_ref="refs/heads/org/development",
            canonical_expected_tip=base,
            expected_dev_ref="refs/heads/org/development",
        )
        successor_stage = SourceStageSpec(
            "MEM11",
            "source",
            None,
            "MEM10",
            SOURCE_PATHS,
            policy_id,
            "cortex-project",
            "cortex-goal",
            "cortex-sol",
            "cortex-lead",
            source_repo=source,
            publisher_repo=publisher,
            canonical_repo=canonical,
            canonical_ref="refs/heads/org/development",
            canonical_expected_tip=base,
            expected_dev_ref="refs/heads/org/development",
        )
        config = replace(
            self.config,
            source_issue_id=None,
            reviewer_issue_id=None,
            worker_checkout=None,
            source_repo=source,
            publisher_repo=publisher,
            broker_journal_dir=root / "broker-journal",
            bridge_journal_dir=root / "bridge-journal",
            receipt_store=root / "receipts",
            verifier_scratch_root=root / "scratch",
            python_executable=python,
            verifier_commands=((str(python), "-c", "raise SystemExit(0)"),),
            verifier_env=(),
            verifier_policy_id=policy_id,
            fixed_dev_ref="refs/heads/brain/development",
            expected_dev_ref="refs/heads/brain/development",
            expected_base_sha=base,
            finite_lead_agent_id="experience-sol",
            finite_lead_issue_id="experience-lead",
            source_stage_catalog=(stage, successor_stage),
            canonical_repo=canonical,
            canonical_ref="refs/heads/brain/development",
            canonical_expected_tip=base,
        )
        bind_payload = {
            "company_id": config.company_id,
            "project_id": "experience-project",
            "lead_agent_id": "experience-sol",
            "lead_issue_id": "experience-lead",
            "lead_run_id": "experience-run",
            "stage_key": "MEM10",
            "source_issue_id": "mem10-source",
            "source_version": 1,
            "review_issue_id": "mem10-review",
            "review_version": 1,
            "predecessor_operation_key": None,
            "predecessor_source_status": None,
            "predecessor_source_version": None,
            "predecessor_review_status": None,
            "predecessor_review_version": None,
            "predecessor_source_issue_id": None,
            "predecessor_review_issue_id": None,
            "predecessor_dependency_ready": None,
            "source_instruction_sha256": "1" * 64,
            "review_instruction_sha256": "2" * 64,
        }
        bound = dispatch(config, {"action": "bind_source_stage", "payload": bind_payload})
        dispatch(
            config,
            {
                "action": "source_stage_activation",
                "payload": {
                    "step": "intent",
                    "stage_binding_key": bound["stage_binding_key"],
                    "issue_id": "mem10-source",
                    "observed_status": "backlog",
                    "observed_version": 1,
                },
            },
        )
        dispatch(
            config,
            {
                "action": "source_stage_activation",
                "payload": {
                    "step": "ack",
                    "stage_binding_key": bound["stage_binding_key"],
                    "issue_id": "mem10-source",
                    "mutation_status": "todo",
                    "mutation_status_version": 2,
                },
            },
        )
        files = [
            {
                "path_id": path,
                "content": f"department candidate {index}\n",
                "size": len(f"department candidate {index}\n".encode()),
                "sha256": hashlib.sha256(f"department candidate {index}\n".encode()).hexdigest(),
            }
            for index, path in enumerate(SOURCE_PATHS)
        ]
        prepared = dispatch(
            config,
            {
                "action": "deliver_source_stage_content",
                "payload": {
                    "company_id": config.company_id,
                    "project_id": "experience-project",
                    "source_issue_id": "mem10-source",
                    "worker_agent_id": config.worker_agent_id,
                    "run_id": "mem10-source-run",
                    "stage_key": "MEM10",
                    "stage_binding_key": bound["stage_binding_key"],
                    "source_instruction_sha256": "1" * 64,
                    "files": files,
                },
            },
        )
        review = {
            "company_id": config.company_id,
            "project_id": "experience-project",
            "reviewer_agent_id": config.reviewer_agent_id,
            "reviewer_run_id": "mem10-review-run",
            "reviewer_issue_id": "mem10-review",
            "stage_key": "MEM10",
            "stage_binding_key": bound["stage_binding_key"],
            "review_instruction_sha256": "2" * 64,
        }
        inspected = dispatch(config, {"action": "inspect_bound_source_stage", "payload": review})
        self.assertEqual(inspected["source_preflight"]["status"], "passed")
        approved = dispatch(
            config,
            {
                "action": "approve_bound_source_stage",
                "payload": {
                    **review,
                    "approved": True,
                    "evidence": {
                        "inspection_digest": inspected["inspection_digest"],
                        "canonical_candidate_sha": inspected["canonical_candidate_sha"],
                        "canonical_receipt_digest": inspected["canonical_receipt_digest"],
                        "rationale": "verified exact department candidate",
                    },
                },
            },
        )
        self.assertEqual(approved["status"], "published")
        self.assertEqual(
            git(publisher, "rev-parse", "refs/heads/org/development"), approved["candidate_sha"]
        )
        self.assertEqual(
            git(canonical, "rev-parse", "refs/heads/org/development"),
            approved["canonical_candidate_sha"],
        )
        self.assertEqual(git(publisher, "rev-parse", "refs/heads/main"), base)
        operation = _load_journal(config, prepared["operation_key"])
        self.assertEqual(operation["state"], "published")
        self.assertEqual(operation["binding"]["active_ref"], "refs/heads/org/development")
        self.assertEqual(operation["canonical"]["receipt"]["ref"], "refs/heads/org/development")
        predecessor = dispatch(
            config,
            {
                "action": "lookup_source_stage_binding",
                "payload": {
                    "company_id": config.company_id,
                    "project_id": "experience-project",
                    "lead_agent_id": "experience-sol",
                    "lead_issue_id": "experience-lead",
                    "stage_key": "MEM10",
                    "published_predecessor_lookup": True,
                    "successor_stage_key": "MEM11",
                },
            },
        )
        self.assertEqual(predecessor["attempt"], 1)
        self.assertEqual(predecessor["operation_key"], prepared["operation_key"])
        self.assertEqual(predecessor["source_issue_id"], "mem10-source")
        self.assertEqual(predecessor["review_issue_id"], "mem10-review")
        self.assertEqual(predecessor["operation_review_approved"], True)
        self.assertEqual(predecessor["operation_state"], "published")
        self.assertEqual(predecessor["canonical_status"], "published")
        with self.assertRaisesRegex(BridgeError, "source_stage_predecessor_not_owner_admitted"):
            dispatch(
                config,
                {
                    "action": "lookup_source_stage_binding",
                    "payload": {
                        "company_id": config.company_id,
                        "project_id": "experience-project",
                        "lead_agent_id": "experience-sol",
                        "lead_issue_id": "experience-lead",
                        "stage_key": "MEM10",
                        "published_predecessor_lookup": True,
                        "successor_stage_key": "MEM10",
                    },
                },
            )

    def test_rejected_working_seed_restores_only_the_exact_admitted_file_snapshot(self):
        from engineering.paperclip_source_delivery import (
            SOURCE_PATHS,
            SourceDeliveryError,
            restore_owner_workspace_snapshot,
            snapshot_records,
        )

        workspace = Path(self.tmp.name) / "rejected-seed-workspace"
        workspace.mkdir()
        rejected = [
            {
                "path_id": path,
                "content": f"rejected candidate {index}\n",
                "size": len(f"rejected candidate {index}\n".encode()),
                "sha256": hashlib.sha256(f"rejected candidate {index}\n".encode()).hexdigest(),
            }
            for index, path in enumerate(SOURCE_PATHS)
        ]
        for record in rejected:
            target = workspace / record["path_id"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("accepted Git parent\n")
        unrelated = workspace / "notes.txt"
        unrelated.write_text("outside the admitted file set\n")
        seed_digest = snapshot_records(rejected).digest
        restored_digest = restore_owner_workspace_snapshot(workspace, rejected, seed_digest)
        self.assertEqual(restored_digest, seed_digest)
        for record in rejected:
            self.assertEqual((workspace / record["path_id"]).read_text(), record["content"])
        self.assertEqual(unrelated.read_text(), "outside the admitted file set\n")
        with self.assertRaisesRegex(SourceDeliveryError, "source_workspace_snapshot_mismatch"):
            restore_owner_workspace_snapshot(workspace, rejected, "0" * 64)
        for record in rejected:
            self.assertEqual((workspace / record["path_id"]).read_text(), record["content"])

    def test_controller_archived_rejected_seed_restores_only_after_accepted_base_readback(self):
        from engineering.paperclip_autonomous_bridge import (
            _canonical,
            _persist_preflight_json,
            _restore_rejected_workspace_seed,
        )
        from engineering.paperclip_source_delivery import SOURCE_PATHS, snapshot_records

        root = Path(self.tmp.name) / "controller-rejected-seed"
        workspace = root / "workspace"
        workspace.mkdir(parents=True)
        config = replace(
            self.config,
            bridge_journal_dir=root / "journal",
            source_workspace_root=workspace,
        )
        baseline = [
            {
                "path_id": path,
                "content": f"accepted base {index}\n",
                "size": len(f"accepted base {index}\n".encode()),
                "sha256": hashlib.sha256(f"accepted base {index}\n".encode()).hexdigest(),
            }
            for index, path in enumerate(SOURCE_PATHS)
        ]
        rejected = [
            {
                "path_id": path,
                "content": f"controller rejected seed {index}\n",
                "size": len(f"controller rejected seed {index}\n".encode()),
                "sha256": hashlib.sha256(
                    f"controller rejected seed {index}\n".encode()
                ).hexdigest(),
            }
            for index, path in enumerate(SOURCE_PATHS)
        ]

        def write_records(records):
            for record in records:
                target = workspace / record["path_id"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(record["content"])

        def read_records():
            records = []
            for path in SOURCE_PATHS:
                content = (workspace / path).read_text()
                encoded = content.encode()
                records.append(
                    {
                        "path_id": path,
                        "content": content,
                        "size": len(encoded),
                        "sha256": hashlib.sha256(encoded).hexdigest(),
                    }
                )
            return records

        write_records(baseline)
        accepted_base_digest = snapshot_records(baseline).digest
        rejected_snapshot = snapshot_records(rejected)
        binding = {
            "stage_binding_key": "a" * 64,
            "operation_key": "f" * 64,
            "candidate_sha": "b" * 40,
        }
        operation = {
            "schema": "paperclip-autonomous-operation-v1",
            "operation_key": binding["operation_key"],
            "state": "review_rejected",
            "source_stage_binding": binding["stage_binding_key"],
            "source_snapshot_sha256": rejected_snapshot.digest,
            "source_files": [
                {"path_id": item.path_id, "size": item.size, "sha256": item.sha256}
                for item in rejected_snapshot.files
            ],
            "candidate": {
                "operation_key": binding["operation_key"],
                "candidate_sha": binding["candidate_sha"],
            },
        }
        archive_key = hashlib.sha256(
            _canonical(
                [
                    "source-stage-retry-workspace-v1",
                    binding["stage_binding_key"],
                    binding["operation_key"],
                    rejected_snapshot.digest,
                ]
            )
        ).hexdigest()
        archive = {
            "schema": "paperclip-source-stage-retry-workspace-v1",
            "snapshot_sha256": rejected_snapshot.digest,
            "files": rejected,
        }
        artifact_digest = _persist_preflight_json(config, "retry-snapshot", archive_key, archive)
        _restore_rejected_workspace_seed(
            config,
            binding,
            operation,
            accepted_base_digest,
            rejected_snapshot.digest,
            artifact_digest,
        )
        self.assertEqual(snapshot_records(read_records()).digest, rejected_snapshot.digest)

        write_records(baseline)
        target = workspace / SOURCE_PATHS[0]
        target.write_text("unrecognized workspace edit\n")
        before = target.read_bytes()
        with self.assertRaisesRegex(BridgeError, "source_stage_retry_seed_identity_mismatch"):
            _restore_rejected_workspace_seed(
                config,
                binding,
                operation,
                accepted_base_digest,
                rejected_snapshot.digest,
                artifact_digest,
            )
        self.assertEqual(target.read_bytes(), before)

    def test_stage_bind_refreshes_owner_root_and_replay_does_not_overwrite_source_edits(self):
        from engineering.paperclip_source_delivery import SOURCE_PATHS

        root = Path(self.tmp.name) / "workspace-stage"
        source = root / "source"
        source.mkdir(parents=True)

        def git(*args):
            return subprocess.run(
                ["/usr/bin/git", "-C", str(source), *args],
                capture_output=True,
                check=True,
                text=True,
            ).stdout.strip()

        subprocess.run(["/usr/bin/git", "init", "-q", "-b", "main", str(source)], check=True)
        for path, content in zip(
            SOURCE_PATHS, ("trusted events\n", "trusted tests\n"), strict=True
        ):
            target = source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        git("add", "-A")
        subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(source),
                "-c",
                "user.name=owner",
                "-c",
                "user.email=owner@example.invalid",
                "commit",
                "-m",
                "trusted base",
            ],
            capture_output=True,
            check=True,
        )
        base = git("rev-parse", "HEAD")
        publisher = root / "publisher"
        subprocess.run(["/usr/bin/git", "clone", "-q", str(source), str(publisher)], check=True)
        global_domain_source = root / "global-domain-source-unavailable"
        workspace = root / "no-git-workspace"
        workspace.mkdir()
        stages = (
            SourceStageSpec(
                "MEM8",
                "source",
                base,
                None,
                SOURCE_PATHS,
                "policy-mem8",
                source_repo=source,
                publisher_repo=publisher,
            ),
            SourceStageSpec("MEM9", "published", None, "MEM8", SOURCE_PATHS, "policy-mem9"),
        )
        config = replace(
            self.config,
            source_issue_id=None,
            reviewer_issue_id=None,
            source_repo=global_domain_source,
            publisher_repo=publisher,
            worker_checkout=None,
            expected_base_sha=base,
            finite_lead_agent_id="lead-1",
            finite_lead_issue_id="lead-issue",
            source_stage_catalog=stages,
            source_workspace_id="workspace-project",
            source_workspace_root=workspace,
        )
        payload = {
            "company_id": config.company_id,
            "project_id": config.project_id,
            "lead_agent_id": "lead-1",
            "lead_issue_id": "lead-issue",
            "lead_run_id": "lead-run",
            "stage_key": "MEM8",
            "source_issue_id": "source-8",
            "source_version": 1,
            "review_issue_id": "review-8",
            "review_version": 1,
            "source_instruction_sha256": "1" * 64,
            "review_instruction_sha256": "2" * 64,
            "predecessor_operation_key": None,
            "predecessor_source_status": None,
            "predecessor_source_version": None,
            "predecessor_review_status": None,
            "predecessor_review_version": None,
            "predecessor_source_issue_id": None,
            "predecessor_review_issue_id": None,
            "predecessor_dependency_ready": None,
        }
        first = dispatch(config, {"action": "bind_source_stage", "payload": payload})
        self.assertEqual(config.source_repo, global_domain_source)
        self.assertEqual((source / SOURCE_PATHS[0]).read_text(), "trusted events\n")
        self.assertEqual((workspace / SOURCE_PATHS[0]).read_text(), "trusted events\n")
        self.assertEqual((workspace / SOURCE_PATHS[1]).read_text(), "trusted tests\n")
        self.assertEqual(len(first["workspace_baseline_sha256"]), 64)
        (workspace / SOURCE_PATHS[0]).write_text("worker source edit\n")
        replay = dispatch(config, {"action": "bind_source_stage", "payload": payload})
        self.assertEqual(replay["stage_binding_key"], first["stage_binding_key"])
        self.assertEqual((workspace / SOURCE_PATHS[0]).read_text(), "worker source edit\n")

    def test_owner_platform_stage_prepares_exact_engineering_pair_from_its_distinct_repositories(
        self,
    ):
        root = Path(self.tmp.name) / "platform-engineering-stage"
        source = root / "platform-source"
        publisher = root / "platform-publisher"
        canonical = root / "platform-canonical"
        worker = root / "worker"
        root.mkdir()

        def git(repo, *args):
            result = subprocess.run(
                ["/usr/bin/git", "-C", str(repo), *args],
                capture_output=True,
                check=False,
                text=True,
            )
            if result.returncode:
                raise AssertionError(result.stderr)
            return result.stdout.strip()

        subprocess.run(["/usr/bin/git", "init", "-q", "-b", "main", str(source)], check=True)
        git(source, "config", "user.name", "owner")
        git(source, "config", "user.email", "owner@example.invalid")
        owner_paths = (
            "engineering/paperclip_source_delivery.py",
            "engineering/tests/test_paperclip_source_delivery.py",
        )
        for path, contents in zip(
            owner_paths, ("# accepted source base\n", "# accepted test base\n"), strict=True
        ):
            target = source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents)
        git(source, "add", "-A")
        git(source, "commit", "-m", "Platform accepted base")
        base = git(source, "rev-parse", "HEAD")
        for target in (publisher, canonical):
            subprocess.run(["/usr/bin/git", "clone", "-q", str(source), str(target)], check=True)
        for target in (source, publisher, canonical):
            git(target, "update-ref", "refs/heads/org/development", base)
            git(target, "update-ref", "refs/heads/brain/development", base)
        git(publisher, "checkout", "--detach", base)
        subprocess.run(["/usr/bin/git", "clone", "-q", str(source), str(worker)], check=True)
        git(worker, "config", "user.name", "Platform worker")
        git(worker, "config", "user.email", "platform-worker@example.invalid")
        (worker / owner_paths[0]).write_text("# exact Platform source update\n")
        (worker / owner_paths[1]).write_text("# exact Platform test update\n")
        git(worker, "add", "-A")
        git(worker, "commit", "-m", "Platform source pair")
        submitted = git(worker, "rev-parse", "HEAD")

        org_ref = "refs/heads/org/development"
        stage = SourceStageSpec(
            "MEM58",
            "source",
            base,
            None,
            owner_paths,
            "policy-platform58",
            "platform-project",
            "platform-goal",
            "platform-sol",
            "platform-lead",
            source_repo=source,
            publisher_repo=publisher,
            canonical_repo=canonical,
            canonical_ref=org_ref,
            canonical_expected_tip=base,
            expected_dev_ref=org_ref,
        )
        config = replace(
            self.config,
            project_id="legacy-domain-project",
            source_repo=root / "global-domain-source-unavailable",
            publisher_repo=root / "global-domain-publisher-unavailable",
            expected_dev_ref=org_ref,
            fixed_dev_ref=org_ref,
            expected_base_sha=base,
            finite_lead_agent_id="platform-sol",
            finite_lead_issue_id="platform-lead",
            source_stage_catalog=(stage,),
            canonical_repo=canonical,
            canonical_ref=org_ref,
            canonical_expected_tip=base,
        )
        stage_config = config.for_source_stage(
            stage,
            source_issue_id="platform-source-issue",
            reviewer_issue_id="platform-review-issue",
            base_sha=base,
        )
        broker = stage_config.broker()
        binding = OperationBinding(
            company_id=stage_config.company_id,
            issue_id="platform-source-issue",
            run_id="platform-source-run",
            worker_agent_id=stage_config.worker_agent_id,
            reviewer_agent_id=stage_config.reviewer_agent_id,
            base_sha=base,
            submitted_sha=submitted,
            scope=owner_paths,
            policy_digest=stage_config.policy().digest,
            active_ref=org_ref,
            owner_config_digest=stage_config.owner_config_digest,
            owner_roots=stage_config.owner_roots,
        )
        candidate = broker.prepare(binding, worker)
        self.assertEqual(candidate.changed_paths, owner_paths)
        self.assertEqual(candidate.base_sha, base)
        self.assertEqual(broker.gate.active_ref, org_ref)
        self.assertNotEqual(broker.gate.source_repo, broker.gate.repo)

        default_gate = replace(stage_config, source_stage_key=None).gate()
        with self.assertRaisesRegex(GitGateError, "protected path changed"):
            default_gate.submit(
                worker,
                submitted,
                base,
                owner_paths,
                expected_source_tip=base,
                expected_integration_tip=base,
            )
        brain_gate = replace(
            stage_config,
            expected_dev_ref="refs/heads/brain/development",
            fixed_dev_ref="refs/heads/brain/development",
            canonical_ref="refs/heads/brain/development",
        ).gate()
        with self.assertRaisesRegex(GitGateError, "protected path changed"):
            brain_gate.submit(
                worker,
                submitted,
                base,
                owner_paths,
                expected_source_tip=base,
                expected_integration_tip=base,
            )

        (worker / "engineering" / "unlisted.py").write_text("unlisted\n")
        git(worker, "add", "-A")
        git(worker, "commit", "-m", "unlisted engineering path")
        unlisted = git(worker, "rev-parse", "HEAD")
        with self.assertRaisesRegex(
            GitGateError, "protected path changed: engineering/unlisted.py"
        ):
            broker.gate.submit(
                worker,
                unlisted,
                base,
                (*owner_paths, "engineering/unlisted.py"),
                expected_source_tip=base,
                expected_integration_tip=base,
            )

        symlink_worker = root / "symlink-worker"
        subprocess.run(
            ["/usr/bin/git", "clone", "-q", str(source), str(symlink_worker)], check=True
        )
        git(symlink_worker, "config", "user.name", "Platform worker")
        git(symlink_worker, "config", "user.email", "platform-worker@example.invalid")
        (symlink_worker / owner_paths[0]).unlink()
        (symlink_worker / owner_paths[0]).symlink_to("/tmp/untrusted-platform-source.py")
        git(symlink_worker, "add", "-A")
        git(symlink_worker, "commit", "-m", "symlink in owner path")
        symlink_sha = git(symlink_worker, "rev-parse", "HEAD")
        with self.assertRaisesRegex(GitGateError, "symlink or submodule"):
            broker.gate.submit(
                symlink_worker,
                symlink_sha,
                base,
                owner_paths,
                expected_source_tip=base,
                expected_integration_tip=base,
            )

        submodule_worker = root / "submodule-worker"
        subprocess.run(
            ["/usr/bin/git", "clone", "-q", str(source), str(submodule_worker)], check=True
        )
        git(submodule_worker, "config", "user.name", "Platform worker")
        git(submodule_worker, "config", "user.email", "platform-worker@example.invalid")
        (submodule_worker / owner_paths[0]).unlink()
        git(
            submodule_worker,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{base},{owner_paths[0]}",
        )
        git(submodule_worker, "commit", "-m", "submodule in owner path")
        submodule_sha = git(submodule_worker, "rev-parse", "HEAD")
        with self.assertRaisesRegex(GitGateError, "symlink or submodule"):
            broker.gate.submit(
                submodule_worker,
                submodule_sha,
                base,
                owner_paths,
                expected_source_tip=base,
                expected_integration_tip=base,
            )

        reserved_paths = (
            "engineering/AGENTS.md",
            "engineering/.gitattributes",
            "engineering/.gitmodules",
            "engineering/.github/workflow.yml",
            "engineering/policy/worker.json",
            "engineering/policies/worker.json",
        )
        for index, reserved in enumerate(reserved_paths):
            with (
                self.subTest(reserved_path=reserved),
                self.assertRaisesRegex(GitGateError, "safe exact files"),
            ):
                GitGate(
                    source,
                    publisher,
                    stage_config.policy(),
                    root / f"reserved-receipts-{index}",
                    active_ref=org_ref,
                    _owner_engineering_paths=(reserved,),
                )


class AcceptedBaseContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.publisher = self.root / "publisher"
        self.source = self.root / "global-source"
        self.cache = self.root / "published-cache"
        self.paths = (
            "src/memecoin_experience/pipeline.py",
            "tests/test_experience_pipeline.py",
        )
        self.accepted_content = {
            self.paths[0]: "accepted-base pipeline — café\n",
            self.paths[1]: "accepted-base tests — café\n",
        }
        self.rejected_content = {
            self.paths[0]: "rejected-seed pipeline\n",
            self.paths[1]: "rejected-seed tests\n",
        }
        subprocess.run(
            ["/usr/bin/git", "init", "-q", "-b", "brain/development", str(self.publisher)],
            check=True,
        )
        self._git(self.publisher, "config", "user.name", "owner")
        self._git(self.publisher, "config", "user.email", "owner@example.invalid")
        for path, content in self.accepted_content.items():
            target = self.publisher / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self._git(self.publisher, "add", *self.paths)
        self._git(self.publisher, "commit", "-qm", "accepted base")
        self.accepted_sha = self._git(self.publisher, "rev-parse", "HEAD")
        self._git(self.publisher, "switch", "-qc", "rejected-seed")
        for path, content in self.rejected_content.items():
            (self.publisher / path).write_text(content)
        self._git(self.publisher, "add", *self.paths)
        self._git(self.publisher, "commit", "-qm", "rejected seed")
        self.rejected_sha = self._git(self.publisher, "rev-parse", "HEAD")
        self._git(self.publisher, "switch", "-q", "brain/development")
        subprocess.run(["/usr/bin/git", "init", "-q", "-b", "main", str(self.source)], check=True)
        self._git(self.source, "config", "user.name", "owner")
        self._git(self.source, "config", "user.email", "owner@example.invalid")
        (self.source / "baseline.txt").write_text("global domain repo")
        self._git(self.source, "add", "baseline.txt")
        self._git(self.source, "commit", "-qm", "global baseline")
        self.stage = SourceStageSpec(
            "MEM13",
            "published",
            None,
            "MEM10",
            self.paths,
            "mem13-synthetic-v1",
            "cortex-project",
            "cortex-goal",
            "sol-agent",
            "mem13-lead",
            source_repo=self.source,
            publisher_repo=self.publisher,
            expected_dev_ref="refs/heads/brain/development",
        )
        self.config = BridgeConfig(
            company_id="company-1",
            project_id="fallback-project",
            worker_agent_id="luna-agent",
            reviewer_agent_id="sol-reviewer",
            source_issue_id=None,
            reviewer_issue_id=None,
            source_repo=self.source,
            worker_checkout=None,
            publisher_repo=self.publisher,
            broker_journal_dir=self.root / "broker-journal",
            bridge_journal_dir=self.root / "bridge-journal",
            receipt_store=self.root / "receipts",
            verifier_scratch_root=self.root / "scratch",
            python_executable=Path("/usr/bin/python3"),
            allowed_scope=self.paths,
            verifier_commands=(("/usr/bin/python3", "-c", "pass"),),
            verifier_env=(),
            verifier_policy_id="mem13-synthetic-v1",
            fixed_dev_ref="refs/heads/brain/development",
            expected_dev_ref="refs/heads/brain/development",
            finite_lead_agent_id="sol-agent",
            finite_lead_issue_id="mem13-lead",
            source_stage_catalog=(self.stage,),
        )
        self.config.broker_journal_dir.mkdir(mode=0o700)
        self.config.bridge_journal_dir.mkdir(mode=0o700)
        self.cache = _trusted_stage_source_cache_path(self.config, self.stage, self.accepted_sha)
        self.cache.parent.mkdir(mode=0o700)
        subprocess.run(
            [
                "/usr/bin/git",
                "clone",
                "-q",
                "--no-local",
                "--branch",
                "brain/development",
                str(self.publisher),
                str(self.cache),
            ],
            check=True,
        )
        self._git(self.cache, "checkout", "-q", "--detach", self.accepted_sha)
        self.tree_sha = _trusted_stage_source_cache_identity(
            self.config, self.stage, self.accepted_sha, self.cache
        )

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["/usr/bin/git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def _active_record(self, *, path_ids=None, base_sha=None, cache=None):
        return {
            "schema": "paperclip-source-stage-binding-attempt-v3",
            "stage_key": "MEM13",
            "attempt": 3,
            "stage_binding_key": "a" * 64,
            "owner_config_digest": self.config.owner_config_digest,
            "source_issue_id": "source-67",
            "review_issue_id": "review-68",
            "source_version": 1,
            "review_version": 1,
            "source_instruction_sha256": "1" * 64,
            "review_instruction_sha256": "2" * 64,
            "path_ids": list(self.paths if path_ids is None else path_ids),
            "base_sha": self.accepted_sha if base_sha is None else base_sha,
            "canonical_base_sha": None,
            "state": "active",
            "source_intent": {"acknowledged": True},
            "trusted_source_cache": str(self.cache if cache is None else cache),
            "trusted_source_cache_tree": self.tree_sha,
        }

    def test_active_lookup_delivers_owner_published_accepted_base_not_rejected_seed(self):
        record = self._active_record()
        _write_journal(self.config, _stage_journal_key("MEM13", 3), record, create=True)
        lookup_payload = {
            "company_id": "company-1",
            "project_id": "cortex-project",
            "lead_agent_id": "sol-agent",
            "lead_issue_id": "mem13-lead",
            "stage_key": "MEM13",
            "attempt": 3,
            "source_issue_id": "source-67",
            "review_issue_id": "review-68",
        }
        first = dispatch(
            self.config,
            {"action": "lookup_source_stage_binding", "payload": lookup_payload},
        )
        second = dispatch(
            self.config,
            {"action": "lookup_source_stage_binding", "payload": lookup_payload},
        )
        note = first["accepted_base_context_note"]
        manifest_line = next(
            line
            for line in note["body"].splitlines()
            if line.startswith("accepted_base_context_json:")
        )
        context = json.loads(manifest_line.split(":", 1)[1])
        self.assertEqual(first, second)
        self.assertEqual(note["idempotency_key"], "a" * 64 + ":accepted-base-context")
        self.assertEqual(context["accepted_base_sha"], self.accepted_sha)
        self.assertEqual(context["repository_role"], "owner_pinned_published_base")
        self.assertEqual(context["path_ids"], list(self.paths))
        self.assertEqual(
            [item["content"] for item in context["files"]], list(self.accepted_content.values())
        )
        self.assertIn("rejected_retry_seed:excluded", note["body"])
        self.assertIn(
            "comparison_scope:bounded_base_content_not_full_repository_equivalence",
            note["body"],
        )
        self.assertEqual(
            [item["content_sha256"] for item in context["files"]],
            [
                hashlib.sha256(value.encode()).hexdigest()
                for value in self.accepted_content.values()
            ],
        )
        self.assertNotEqual(self.accepted_sha, self.rejected_sha)
        for path, rejected in self.rejected_content.items():
            self.assertEqual(
                self._git(self.publisher, "show", f"{self.rejected_sha}:{path}"),
                rejected.rstrip("\n"),
            )
            self.assertNotIn(rejected, "".join(item["content"] or "" for item in context["files"]))
        for forged in (
            {"repo": str(self.publisher)},
            {"ref": "refs/heads/rejected-seed"},
            {"path_ids": ["../../outside"]},
        ):
            with (
                self.subTest(forged=forged),
                self.assertRaisesRegex(BridgeError, "source_stage_lookup_payload_shape_invalid"),
            ):
                dispatch(
                    self.config,
                    {
                        "action": "lookup_source_stage_binding",
                        "payload": {**lookup_payload, **forged},
                    },
                )
        with self.assertRaisesRegex(BridgeError, "source_stage_binding_invalid"):
            dispatch(
                self.config,
                {
                    "action": "lookup_source_stage_binding",
                    "payload": {**lookup_payload, "stage_binding_key": "f" * 64},
                },
            )

    def test_accepted_base_context_rejects_mismatched_path_manifest_and_oversize_blob(self):
        record = self._active_record(path_ids=[*self.paths, "extra.py"])
        with self.assertRaisesRegex(BridgeError, "source_stage_accepted_base_binding_invalid"):
            _accepted_base_context(self.config, self.stage, record)

        large_path = "src/memecoin_experience/large.py"
        large_stage = replace(self.stage, path_ids=(*self.paths, large_path))
        large_config = replace(self.config, source_stage_catalog=(large_stage,))
        target = self.publisher / large_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x" * 65_537)
        self._git(self.publisher, "add", large_path)
        self._git(self.publisher, "commit", "-qm", "oversize accepted file")
        large_base = self._git(self.publisher, "rev-parse", "HEAD")
        # Build a detached no-alternates snapshot from the exact current owner ref.
        large_cache = _trusted_stage_source_cache_path(large_config, large_stage, large_base)
        large_cache.parent.mkdir(mode=0o700, exist_ok=True)
        subprocess.run(
            [
                "/usr/bin/git",
                "clone",
                "-q",
                "--no-local",
                "--branch",
                "brain/development",
                str(self.publisher),
                str(large_cache),
            ],
            check=True,
        )
        _record = self._active_record(
            path_ids=[*self.paths, large_path], base_sha=large_base, cache=large_cache
        )
        _record["owner_config_digest"] = large_config.owner_config_digest
        _record["trusted_source_cache_tree"] = _trusted_stage_source_cache_identity(
            large_config, large_stage, large_base, large_cache
        )
        with self.assertRaisesRegex(BridgeError, "source_stage_accepted_base_context_too_large"):
            _accepted_base_context(large_config, large_stage, _record)

    def test_finite_evaluation_pair_fits_bounded_utf8_note_and_response(self):
        (self.publisher / self.paths[0]).write_bytes(b"p" * 54_056)
        (self.publisher / self.paths[1]).write_bytes(b"t" * 11_642)
        self._git(self.publisher, "add", *self.paths)
        self._git(self.publisher, "commit", "-qm", "finite evaluation accepted base")
        base_sha = self._git(self.publisher, "rev-parse", "HEAD")
        evaluation_stage = replace(self.stage, stage_key="MEM14")
        evaluation_config = replace(self.config, source_stage_catalog=(evaluation_stage,))
        evaluation_cache = _trusted_stage_source_cache_path(
            evaluation_config, evaluation_stage, base_sha
        )
        evaluation_cache.parent.mkdir(mode=0o700, exist_ok=True)
        subprocess.run(
            [
                "/usr/bin/git",
                "clone",
                "-q",
                "--no-local",
                "--branch",
                "brain/development",
                str(self.publisher),
                str(evaluation_cache),
            ],
            check=True,
        )
        record = self._active_record(base_sha=base_sha, cache=evaluation_cache)
        record["stage_key"] = "MEM14"
        record["stage_binding_key"] = "d" * 64
        record["owner_config_digest"] = evaluation_config.owner_config_digest
        record["trusted_source_cache_tree"] = _trusted_stage_source_cache_identity(
            evaluation_config, evaluation_stage, base_sha, evaluation_cache
        )
        context = _accepted_base_context(evaluation_config, evaluation_stage, record)
        note = _accepted_base_context_note(evaluation_stage, context)
        delivery = _accepted_base_delivery(evaluation_config, evaluation_stage, record)
        self.assertEqual(context["total_size_bytes"], 65_698)
        self.assertLessEqual(len(note["body"].encode("utf-8")), 150_000)
        self.assertLessEqual(len(json.dumps(delivery, ensure_ascii=False).encode("utf-8")), 170_000)

    def test_non_utf8_admitted_base_blob_fails_closed(self):
        (self.publisher / self.paths[0]).write_bytes(b"\xff\xfe")
        self._git(self.publisher, "add", self.paths[0])
        self._git(self.publisher, "commit", "-qm", "non UTF-8 owner base")
        base_sha = self._git(self.publisher, "rev-parse", "HEAD")
        non_utf8_cache = _trusted_stage_source_cache_path(self.config, self.stage, base_sha)
        non_utf8_cache.parent.mkdir(mode=0o700, exist_ok=True)
        subprocess.run(
            [
                "/usr/bin/git",
                "clone",
                "-q",
                "--no-local",
                "--branch",
                "brain/development",
                str(self.publisher),
                str(non_utf8_cache),
            ],
            check=True,
        )
        record = self._active_record(base_sha=base_sha, cache=non_utf8_cache)
        record["trusted_source_cache_tree"] = _trusted_stage_source_cache_identity(
            self.config, self.stage, base_sha, non_utf8_cache
        )
        with self.assertRaisesRegex(BridgeError, "source_stage_accepted_base_not_utf8"):
            _accepted_base_context(self.config, self.stage, record)


if __name__ == "__main__":
    unittest.main()
