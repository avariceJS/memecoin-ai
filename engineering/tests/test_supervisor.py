"""No-model fault harness around real durable state and mocked external effects."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from engineering import supervisor
from engineering.confinement import SandboxResult
from engineering.git_gate import VerificationPolicy
from engineering.org_state import StateError, Store
from engineering.tests.test_service import deployment as deployment_fixture
from engineering.verification_plan import prepare_verification_plan


class FakeCodex:
    """Only a local fake; never starts an app server or calls a model."""

    response = "{malformed"
    turn_estimates = []
    turn_calls = []
    calls = []

    def __init__(self, *args, **kwargs):
        self.proc = SimpleNamespace(pid=123)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def start(self, *args):
        return "fake-thread"

    def turn(self, *args, **kwargs):
        self.turn_estimates.append(kwargs.get("estimate", {}).get("tokens"))
        self.turn_calls.append(args)
        return {"text": self.response, "usage": {"total": {"totalTokens": 3}}}

    def call(self, *args, **kwargs):
        self.calls.append(args)
        return {"ordinaryUsageAllowed": False}

    def record_outcome(self, *args, **kwargs):
        pass


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        FakeCodex.response = "{malformed"

        FakeCodex.turn_estimates = []
        FakeCodex.turn_calls = []
        FakeCodex.calls = []
        (self.state / "intents").mkdir()
        self.now = 1000.0
        self.db = Store(self.state / "organization.sqlite", clock=lambda: self.now)
        self.addCleanup(self.db.close)
        self.cfg = {
            "python": sys.executable,
            "controller": str(self.state),
            "contracts": {
                "schema-validation": {
                    "meaning": "strict validation v2",
                    "version": 2,
                    "producer": "Experience",
                    "consumer": "Platform",
                }
            },
            "tasks": {"T": {"checks": []}},
            "resources": {"per_worker_tokens": 100},
        }
        self.db.put_contract("schema-validation", 1, {"meaning": "original"})
        self.patches = [
            patch.object(supervisor, "config", return_value=self.cfg),
            patch.object(
                supervisor,
                "store",
                side_effect=lambda state: Store(
                    self.state / "organization.sqlite", clock=lambda: self.now
                ),
            ),
            patch.object(supervisor, "log_event"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_later_bound_command_identity_and_result_contract_reach_verifier_gate(self):
        command = SimpleNamespace(
            executable_sha256="a" * 64,
            runtime_cdhash="b" * 40,
            runtime_path=Path("/trusted/native-check"),
            script_sha256="c" * 64,
            script_path=Path("/trusted/second.py"),
            result={
                "kind": "acceptance_json",
                "acceptance_id": "second-check",
                "result_schema": "second-result-v1",
                "case_field": "case",
                "passed_field": "passed",
            },
        )
        self.assertEqual(
            supervisor._bound_command_runner_identity(command),
            {
                "expected_executable_sha256": "a" * 64,
                "expected_executable_cdhash": "b" * 40,
                "expected_executable_main_path": Path("/trusted/native-check"),
                "expected_script_sha256": "c" * 64,
                "expected_script_path": Path("/trusted/second.py"),
            },
        )
        with self.assertRaisesRegex(ValueError, "not_passing"):
            supervisor.validate_verifier_result(
                json.dumps(
                    {
                        "schema": "second-result-v1",
                        "case": "second-check",
                        "passed": False,
                    }
                ),
                {key: value for key, value in command.result.items() if key != "kind"},
            )

    def test_verify_callback_pins_later_command_and_rejects_its_failed_json(self):
        self.candidate(target_state="submitted")
        checkout = self.state / "candidate-checkout"
        checkout.mkdir()
        commands = (
            ("{interpreter}", "-I", "-S", "-B", "{harness}"),
            ("/trusted/native-check", "--check"),
        )
        policy = VerificationPolicy(commands)
        first_result = {
            "kind": "acceptance_json",
            "acceptance_id": "first-check",
            "result_schema": "first-result-v1",
            "case_field": "case",
            "passed_field": "passed",
        }
        later_result = {
            "kind": "acceptance_json",
            "acceptance_id": "later-check",
            "result_schema": "later-result-v1",
            "case_field": "case",
            "passed_field": "passed",
        }
        later_binding = SimpleNamespace(
            executable_path=Path("/trusted/native-check"),
            executable_sha256="a" * 64,
            runtime_path=Path("/trusted/native-main"),
            runtime_cdhash="b" * 40,
            script_path=Path("/trusted/later.py"),
            script_sha256="c" * 64,
            result=later_result,
        )
        verifier = SimpleNamespace(
            commands=commands,
            command_bindings=(
                SimpleNamespace(
                    executable_sha256="d" * 64,
                    runtime_cdhash="e" * 40,
                    runtime_path=Path("/trusted/python-main"),
                    script_sha256="f" * 64,
                    script_path=Path("/trusted/acceptance.py"),
                    result=first_result,
                ),
                later_binding,
            ),
            harness_path=Path("/trusted/acceptance.py"),
            interpreter_path=Path("/trusted/python"),
            acceptance_id="first-check",
            result_schema="first-result-v1",
            case_field="case",
            passed_field="passed",
        )
        plan = SimpleNamespace(
            execution_qualified=True,
            reason=None,
            binding_sha256="9" * 64,
            plan_sha256="8" * 64,
            policy=policy,
            bound_verifiers=(verifier,),
        )
        candidate = SimpleNamespace(
            submitted_sha="submitted",
            base_sha="base",
            integration_tip="base",
            sha="merged",
            changed_paths=("src/example.py",),
            checkout=checkout,
        )
        gate = MagicMock()
        gate.submit.return_value = candidate

        def invoke_verification(_candidate, callback):
            return callback(checkout, "merged", policy)

        gate.verify.side_effect = invoke_verification
        box = MagicMock()
        box.run.side_effect = [
            SandboxResult(
                ("/trusted/python",),
                0,
                json.dumps(
                    {
                        "schema": "first-result-v1",
                        "case": "first-check",
                        "passed": True,
                    }
                ),
                "",
                False,
                "profile",
                0.01,
            ),
            SandboxResult(
                ("/trusted/native-check",),
                0,
                json.dumps(
                    {
                        "schema": "later-result-v1",
                        "case": "later-check",
                        "passed": False,
                    }
                ),
                "",
                False,
                "profile",
                0.01,
            ),
        ]
        with (
            patch.object(supervisor, "prepare_verification_plan", return_value=plan),
            patch.object(supervisor, "attempt_plan_matches", return_value=(True, None)),
            patch.object(supervisor, "GitGate", return_value=gate),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "SandboxRunner", return_value=box),
            patch.object(supervisor, "metered_client") as review,
            patch.object(supervisor, "git") as diff,
            patch.object(supervisor, "git_raw") as paths,
        ):
            self.assertTrue(supervisor.verify_one(self.state, self.db.get_task("T")))

        later_call = box.run.call_args_list[1]
        self.assertEqual(
            later_call.kwargs["expected_executable_sha256"], later_binding.executable_sha256
        )
        self.assertEqual(
            later_call.kwargs["expected_executable_cdhash"], later_binding.runtime_cdhash
        )
        self.assertEqual(
            later_call.kwargs["expected_executable_main_path"], later_binding.runtime_path
        )
        self.assertEqual(later_call.kwargs["expected_script_sha256"], later_binding.script_sha256)
        self.assertEqual(later_call.kwargs["expected_script_path"], later_binding.script_path)
        gate.publish.assert_not_called()
        review.assert_not_called()
        diff.assert_not_called()
        paths.assert_not_called()
        self.assertEqual(self.db.get_task("T")["state"], "pending")
        verification_event = [
            event for event in self.db.events() if event["kind"] == "candidate_verified"
        ][-1]
        self.assertFalse(verification_event["body"]["passed"])
        self.assertIn(
            "verifier_result_not_passing",
            verification_event["body"]["details"]["error"],
        )

    def task(self, task_id="T", team="team"):
        self.db.add_task(task_id, team, "Synthetic task", scope=["src/example.py"])

    def candidate(self, task_id="T", sha="submitted", target_state="accepted", team="team"):
        self.task(task_id, team)
        attempt = self.db.claim(task_id, "worker", 100)
        self.db.submit(
            attempt["id"],
            attempt["epoch"],
            sha,
            {"checkout": str(self.state / "worker"), "base": "base"},
        )
        if target_state in ("verified", "accepted", "integrated"):
            self.db.verify(task_id, sha, True, {"receipt": "receipt"})
        if target_state in ("accepted", "integrated"):
            self.db.accept(task_id, sha)
        if target_state == "integrated":
            self.db.integrate(task_id, sha, "merged")
        return attempt

    def intent(self, task_id="T", submitted="submitted"):
        intent = {
            "task": task_id,
            "submitted_sha": submitted,
            "candidate_sha": "merged",
            "expected_tip": "base",
            "receipt": "receipt",
        }
        (self.state / "intents" / f"{task_id}.json").write_text(json.dumps(intent))
        return intent

    def reconcile(self, tip="base"):
        calls = []

        def fake_git(repo, *args):
            calls.append(args)
            if args[0] == "rev-parse":
                return tip
            if args[0] == "update-ref":
                return ""
            raise AssertionError(f"unexpected Git command: {args}")

        gate = MagicMock()
        with (
            patch.object(supervisor, "git", side_effect=fake_git),
            patch.object(supervisor, "GitGate", return_value=gate),
        ):
            supervisor.reconcile(self.state, self.db)
        return calls, gate

    def _use_real_preflight(self):
        self.patches[0].stop()

    def _frozen_deployment(self):
        index = getattr(self, "_deployment_index", 0) + 1
        self._deployment_index = index
        state = deployment_fixture(self.state / f"deployment-{index}")
        controller = state / "controller"
        module = controller / "engineering/supervisor.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        source = Path(supervisor.__file__).read_bytes()
        module.write_bytes(source)
        manifest_path = state / "controller-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["engineering/supervisor.py"] = hashlib.sha256(source).hexdigest()
        manifest_path.write_text(json.dumps(manifest, sort_keys=True))
        return state, module

    def test_common_preflight_accepts_matching_frozen_module(self):
        self._use_real_preflight()
        state, module = self._frozen_deployment()
        with patch.object(supervisor, "__file__", str(module)):
            cfg = supervisor.config(state)
        self.assertEqual(Path(cfg["controller"]).resolve(), module.parent.parent.resolve())
        self.assertEqual(cfg, json.loads((state / "config.json").read_text()))

    def test_common_preflight_rejects_source_module_outside_controller(self):
        self._use_real_preflight()
        state, _ = self._frozen_deployment()
        with self.assertRaisesRegex(RuntimeError, "supervisor_module_outside_frozen_controller"):
            supervisor.config(state)

    def test_supported_entries_reject_stale_bindings_before_work(self):
        self._use_real_preflight()
        for entry in ("supervise", "run_attempt"):
            for binding in ("qualified", "adapter_sha256", "executable_sha256"):
                with self.subTest(entry=entry, binding=binding):
                    state, module = self._frozen_deployment()
                    report_path = state / "native-profile.json"
                    report = json.loads(report_path.read_text())
                    report[binding] = False if binding == "qualified" else "0" * 64
                    report_path.write_text(json.dumps(report, sort_keys=True))
                    with (
                        patch.object(supervisor, "__file__", str(module)),
                        patch.object(supervisor, "store") as open_store,
                        patch.object(supervisor, "Codex") as codex,
                        patch.object(supervisor.subprocess, "Popen") as spawn,
                    ):
                        with self.assertRaises(ValueError):
                            if entry == "supervise":
                                supervisor.supervise(state)
                            else:
                                supervisor.run_attempt(state, 1)
                    open_store.assert_not_called()
                    codex.assert_not_called()
                    spawn.assert_not_called()

    def test_accepted_intent_before_publish_recovers_once(self):
        self.candidate()
        self.intent()
        calls, gate = self.reconcile("base")
        self.assertEqual(self.db.get_task("T")["state"], "integrated")
        self.assertEqual(sum(call[0] == "update-ref" for call in calls), 1)
        gate._require_receipt.assert_called_once_with("receipt", "merged")
        again, _ = self.reconcile("merged")
        self.assertFalse(any(call[0] == "update-ref" for call in again))
        events = [event for event in self.db.events() if event["kind"] == "candidate_integrated"]
        self.assertEqual(len(events), 1)

    def test_accepted_intent_after_publish_recovers_without_republish(self):
        self.candidate()
        self.intent()
        calls, _ = self.reconcile("merged")
        self.assertEqual(self.db.get_task("T")["state"], "integrated")
        self.assertFalse(any(call[0] == "update-ref" for call in calls))

    def test_verified_intent_crash_before_acceptance_recovers(self):
        self.candidate(target_state="verified")
        self.intent()
        self.reconcile("base")
        self.assertEqual(self.db.get_task("T")["state"], "integrated")

    def test_submitted_intent_crash_before_db_verification_recovers(self):
        self.candidate(target_state="submitted")
        self.intent()
        self.reconcile("base")
        self.assertEqual(self.db.get_task("T")["state"], "integrated")

    def test_stale_intent_cannot_publish_old_candidate_for_new_attempt(self):
        self.candidate(sha="new-submission")
        self.intent(submitted="old-submission")
        calls = []
        with (
            patch.object(
                supervisor, "git", side_effect=lambda repo, *args: calls.append(args) or "base"
            ),
            patch.object(supervisor, "GitGate"),
        ):
            try:
                supervisor.reconcile(self.state, self.db)
            except (RuntimeError, ValueError):
                pass
        self.assertFalse(any(call[0] == "update-ref" for call in calls))
        self.assertEqual(self.db.get_task("T")["state"], "accepted")

    def test_diverged_ref_fails_closed_without_acceptance_rewrite(self):
        self.candidate()
        self.intent()
        with self.assertRaises(RuntimeError):
            self.reconcile("unrelated")
        self.assertEqual(self.db.get_task("T")["state"], "accepted")

    def test_submit_import_failure_rejects_for_bounded_retry(self):
        self.candidate(target_state="submitted")
        gate = MagicMock()
        gate.submit.side_effect = RuntimeError("scope violation")
        with patch.object(supervisor, "GitGate", return_value=gate):
            self.assertTrue(supervisor.verify_one(self.state, self.db.get_task("T")))
        self.assertEqual(self.db.get_task("T")["state"], "pending")
        second = self.db.claim("T", "worker")
        self.assertEqual(second["epoch"], 2)

    def verification_failure(
        self,
        stdout,
        reviewer_text="{malformed",
        diff="synthetic diff",
        raw_path_output="src/example.py\0",
    ):
        """Drive the actual verifier callback with pure fake tool/model responses."""
        self.candidate(target_state="submitted")
        checkout = self.state / "candidate-checkout"
        checkout.mkdir()
        candidate = SimpleNamespace(
            submitted_sha="submitted",
            base_sha="base",
            integration_tip="base",
            sha="merged",
            changed_paths=("src/example.py",),
            checkout=checkout,
        )
        gate = MagicMock()
        gate.submit.return_value = candidate
        policy = VerificationPolicy(((sys.executable, "fixed-acceptance"),))
        gate.verify.side_effect = lambda value, callback: callback(checkout, "merged", policy)
        result = SandboxResult(("fixed-acceptance",), 0, stdout, "", False, "profile", 0.01)
        box = MagicMock()
        box.run.return_value = result
        reviewer = FakeCodex()
        reviewer.response = reviewer_text
        with (
            patch.object(supervisor, "GitGate", return_value=gate),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "SandboxRunner", return_value=box),
            patch.object(
                supervisor, "metered_client", side_effect=lambda *args, **kwargs: reviewer
            ) as model,
            patch.object(supervisor, "git", return_value=diff),
            patch.object(supervisor, "git_raw", return_value=raw_path_output),
        ):
            supervisor.verify_one(self.state, self.db.get_task("T"))
        self.assertEqual(self.db.get_task("T")["state"], "pending")
        gate.publish.assert_not_called()
        self.assertFalse(any(event["kind"] == "candidate_accepted" for event in self.db.events()))
        return model

    def test_acceptance_early_exit_zero_without_completion_json_is_rejected(self):
        model = self.verification_failure("")
        model.assert_not_called()

    def test_acceptance_wrong_case_completion_json_is_rejected(self):
        model = self.verification_failure(
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "WRONG", "passed": True})
        )
        model.assert_not_called()

    def test_legacy_acceptance_duplicate_passed_key_is_rejected(self):
        model = self.verification_failure(
            '{"schema":"org-pilot-acceptance-v1","case":"T","passed":false,"passed":true}'
        )
        model.assert_not_called()

    def test_legacy_acceptance_duplicate_schema_key_is_rejected(self):
        model = self.verification_failure(
            '{"schema":"wrong","schema":"org-pilot-acceptance-v1","case":"T","passed":true}'
        )
        model.assert_not_called()

    def test_legacy_acceptance_duplicate_case_key_is_rejected(self):
        model = self.verification_failure(
            '{"schema":"org-pilot-acceptance-v1","case":"wrong","case":"T","passed":true}'
        )
        model.assert_not_called()

    def test_legacy_acceptance_nonstandard_number_is_rejected(self):
        model = self.verification_failure(
            '{"schema":"org-pilot-acceptance-v1","case":"T","passed":true,"extra":NaN}'
        )
        model.assert_not_called()

    def test_malformed_independent_reviewer_json_cannot_accept_candidate(self):
        model = self.verification_failure(
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "T", "passed": True})
        )
        model.assert_called_once()
        packet = json.loads(
            (self.state / "verification" / "merged" / "review-packet.json").read_text()
        )
        self.assertEqual(packet["diff"], "synthetic diff")
        self.assertEqual(packet["candidate"]["submitted_sha"], "submitted")
        self.assertEqual(packet["candidate"]["merged_candidate_sha"], "merged")
        self.assertEqual(packet["candidate"]["submitted_changed_paths"], ["src/example.py"])
        self.assertEqual(packet["candidate"]["merged_changed_paths"], ["src/example.py"])
        self.assertEqual(packet["task"]["brief"], "")

    def test_oversized_diff_blocks_before_independent_review_call(self):
        model = self.verification_failure(
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "T", "passed": True}),
            diff="x" * 50001,
        )
        model.assert_not_called()
        self.assertFalse((self.state / "verification" / "merged" / "review-packet.json").exists())

    def test_leading_whitespace_in_git_path_is_not_trimmed_into_scope(self):
        model = self.verification_failure(
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "T", "passed": True}),
            raw_path_output=" src/example.py\0",
        )
        model.assert_not_called()
        self.assertFalse((self.state / "verification" / "merged" / "review-packet.json").exists())

    def test_unqualified_bound_verification_stops_before_git_sandbox_or_model(self):
        class BoundStore:
            def get_candidate(self, task_id):
                return {"evidence": {}}

            def get_task_spec(self, task_id):
                return {"decision": {"name": "admission/task-spec/DYNAMIC", "version": 1}}

            def resolve_task_execution_binding(self, task_id, *, controller_release_sha256):
                return {
                    "binding_sha256": "a" * 64,
                    "integration_policy_sha256": "b" * 64,
                    "verifiers": [
                        {
                            "acceptance_id": "synthetic",
                            "acceptance_sha256": "c" * 64,
                            "commands": [["/synthetic/verifier"]],
                            "env": {},
                        }
                    ],
                }

            def close(self):
                self.closed = True

        db = BoundStore()
        task = {
            "id": "DYNAMIC",
            "candidate_sha": "d" * 40,
            "scope": ["src/example.py"],
            "team": "Platform",
        }
        with (
            patch.object(supervisor, "store", return_value=db),
            patch.object(
                supervisor,
                "config",
                return_value={
                    "controller_release_sha256": "e" * 64,
                    "integration_policy_sha256": "b" * 64,
                    "python": "/synthetic/python",
                },
            ),
            patch.object(supervisor, "log_event") as log_event,
            patch.object(supervisor, "GitGate", side_effect=AssertionError("Git must not run")),
            patch.object(
                supervisor, "SandboxRunner", side_effect=AssertionError("sandbox must not run")
            ),
            patch.object(
                supervisor, "metered_client", side_effect=AssertionError("model must not run")
            ),
        ):
            self.assertFalse(supervisor.verify_one(self.state, task))
        log_event.assert_called_once()
        self.assertEqual(log_event.call_args.args[1], "verification_blocked")
        self.assertTrue(db.closed)

    def test_reconcile_rejects_unqualified_bound_candidate_before_tip_or_publish(self):
        intent = {
            "task": "DYNAMIC",
            "submitted_sha": "a" * 40,
            "candidate_sha": "b" * 40,
            "expected_tip": "c" * 40,
        }
        supervisor.atomic(self.state / "intents" / "DYNAMIC.json", intent)

        class BoundStore:
            accepted = False

            def get_task(self, task_id):
                return {
                    "id": task_id,
                    "state": "verified",
                    "candidate_sha": "a" * 40,
                }

            def accept(self, task_id, candidate_sha):
                self.accepted = True

            def get_task_spec(self, task_id):
                return {"decision": {"name": "admission/task-spec/DYNAMIC", "version": 1}}

            def resolve_task_execution_binding(self, task_id, *, controller_release_sha256):
                return {
                    "binding_sha256": "d" * 64,
                    "integration_policy_sha256": "e" * 64,
                    "verifiers": [
                        {
                            "acceptance_id": "synthetic",
                            "acceptance_sha256": "f" * 64,
                            "commands": [["/synthetic/verifier"]],
                            "env": {},
                        }
                    ],
                }

        db = BoundStore()
        with (
            patch.object(
                supervisor,
                "config",
                return_value={
                    "controller_release_sha256": "1" * 64,
                    "integration_policy_sha256": "e" * 64,
                },
            ),
            patch.object(supervisor, "git", side_effect=AssertionError("Git tip must not be read")),
            patch.object(
                supervisor, "GitGate", side_effect=AssertionError("publication must not run")
            ),
            patch.object(supervisor, "activate_contract"),
        ):
            supervisor.reconcile(self.state, db)
        self.assertFalse(db.accepted)

    def test_git_raw_preserves_nul_delimited_path_identity(self):
        process = SimpleNamespace(returncode=0, stdout=b" src/example.py\0", stderr=b"")
        with patch.object(supervisor.subprocess, "run", return_value=process):
            self.assertEqual(supervisor.git_raw(self.state, "diff", "-z"), " src/example.py\0")

    def test_git_raw_does_not_normalize_crlf_in_path_identity(self):
        process = SimpleNamespace(returncode=0, stdout=b"src/a\rb\0", stderr=b"")
        with patch.object(supervisor.subprocess, "run", return_value=process):
            self.assertEqual(supervisor.git_raw(self.state, "diff", "-z"), "src/a\rb\0")

    def test_restart_does_not_reissue_running_unexpired_attempt(self):
        self.task()
        first = self.db.claim("T", "worker", 100)
        with (
            patch.object(supervisor, "Codex", FakeCodex),
            patch.object(supervisor, "process_identity", return_value="fake"),
            patch.object(supervisor, "available_memory", return_value=10**10),
            patch.object(supervisor, "status", return_value={}),
            patch.object(
                supervisor.time, "sleep", side_effect=lambda _: (self.state / "STOP").touch()
            ),
            patch.object(supervisor.subprocess, "Popen") as spawn,
        ):
            supervisor.supervise(self.state, max_seconds=10)
        spawn.assert_not_called()
        self.assertEqual(self.db.get_task("T")["active_attempt"], first["id"])
        self.assertEqual(len(self.db.list_attempts()), 1)

    def test_initial_rate_limit_read_does_not_depend_on_system_uptime(self):
        self.task()
        with (
            patch.object(supervisor, "Codex", FakeCodex),
            patch.object(supervisor, "process_identity", return_value="fake"),
            patch.object(supervisor, "available_memory", return_value=10**10),
            patch.object(supervisor, "status", return_value={}),
            patch.object(supervisor.time, "monotonic", return_value=10.0),
            patch.object(
                supervisor.time, "sleep", side_effect=lambda _: (self.state / "STOP").touch()
            ),
        ):
            supervisor.supervise(self.state, max_seconds=10)
        self.assertIn(("account/rateLimits/read",), FakeCodex.calls)

    def test_malformed_lead_output_opens_circuit_without_running_worker(self):
        self.task()
        attempt = self.db.claim("T", "worker", 100)
        calls = []

        def fake_git(repo, *args):
            calls.append(args)
            return "base"

        with (
            patch.object(
                supervisor, "metered_client", side_effect=lambda *args, **kwargs: FakeCodex()
            ),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "git", side_effect=fake_git),
            patch.object(supervisor, "process_identity", return_value="fake"),
        ):
            supervisor.run_attempt(self.state, attempt["id"])
        self.assertEqual(self.db.get_task("T")["state"], "failed")
        self.assertEqual(self.db.list_attempts()[0]["state"], "failed")
        self.assertEqual(self.db.get_decision("circuit/controller-parse")["body"]["status"], "open")
        self.assertEqual(
            self.db.get_decision("recovery-obligation/controller-parse")["body"]["status"],
            "open",
        )
        self.assertFalse(any("commit" in args for args in calls))
        self.assertNotIn("tokens", self.db.usage_summary()["total"])
        self.assertEqual(self.db.usage_summary()["total"].get("sol_turns"), 1)

    def test_malformed_worker_output_opens_circuit_before_commit(self):
        self.task()
        attempt = self.db.claim("T", "worker", 100)
        lead, worker = FakeCodex(), FakeCodex()
        lead.response = json.dumps(
            {
                "decision": "approve",
                "worker_brief": "bounded",
                "rationale": "settled",
                "contract_acks": [],
            }
        )
        worker.response = "{malformed"
        calls = []

        def fake_git(repo, *args):
            calls.append(args)
            return " M src/example.py" if args[0] == "status" else "candidate"

        from contextlib import contextmanager

        @contextmanager
        def fake_metered_client(*args, **kwargs):
            client = lead if kwargs["context"]["role"] == "lead" else worker
            yield client

        with (
            patch.object(supervisor, "metered_client", side_effect=fake_metered_client),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "git", side_effect=fake_git),
            patch.object(supervisor, "process_identity", return_value="fake"),
        ):
            supervisor.run_attempt(self.state, attempt["id"])
        self.assertEqual(self.db.get_task("T")["state"], "failed")
        self.assertEqual(self.db.get_decision("circuit/controller-parse")["body"]["status"], "open")
        self.assertFalse(any("commit" in args for args in calls))
        self.assertEqual(len(FakeCodex.turn_estimates), 2)
        self.assertTrue(all(0 < estimate <= 4000 for estimate in FakeCodex.turn_estimates))

    def test_wrong_typed_lead_fields_open_circuit(self):
        self.task()
        attempt = self.db.claim("T", "worker", 100)
        FakeCodex.response = json.dumps(
            {
                "decision": "approve",
                "worker_brief": None,
                "rationale": 123,
                "contract_acks": {},
            }
        )
        with (
            patch.object(supervisor, "metered_client", side_effect=lambda *a, **k: FakeCodex()),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "git", return_value="base"),
            patch.object(supervisor, "process_identity", return_value="fake"),
        ):
            supervisor.run_attempt(self.state, attempt["id"])
        self.assertEqual(self.db.get_task("T")["state"], "failed")
        self.assertEqual(self.db.get_decision("circuit/controller-parse")["body"]["status"], "open")
        self.assertEqual(len(FakeCodex.turn_estimates), 1)

    def test_wrong_typed_worker_fields_open_circuit(self):
        self.task()
        attempt = self.db.claim("T", "worker", 100)
        lead, worker = FakeCodex(), FakeCodex()
        lead.response = json.dumps(
            {
                "decision": "approve",
                "worker_brief": "bounded",
                "rationale": "settled",
                "contract_acks": [],
            }
        )
        worker.response = json.dumps({"summary": None, "tests": 17, "limitations": {}})

        from contextlib import contextmanager

        @contextmanager
        def fake_metered_client(*args, **kwargs):
            yield lead if kwargs["context"]["role"] == "lead" else worker

        with (
            patch.object(supervisor, "metered_client", side_effect=fake_metered_client),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "git", return_value=" M src/example.py"),
            patch.object(supervisor, "process_identity", return_value="fake"),
        ):
            supervisor.run_attempt(self.state, attempt["id"])
        self.assertEqual(self.db.get_task("T")["state"], "failed")
        self.assertEqual(self.db.get_decision("circuit/controller-parse")["body"]["status"], "open")

    def test_open_controller_circuit_stops_queued_attempt_before_dispatch(self):
        self.task("T")
        self.task("T2")
        first = self.db.claim("T", "worker", 100)
        FakeCodex.response = "{malformed"
        with (
            patch.object(supervisor, "metered_client", side_effect=lambda *a, **k: FakeCodex()),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "git", return_value="base"),
            patch.object(supervisor, "process_identity", return_value="fake"),
        ):
            supervisor.run_attempt(self.state, first["id"])
        turns_before = len(FakeCodex.turn_estimates)
        queued = self.db.claim("T2", "worker", 100)
        supervisor.run_attempt(self.state, queued["id"])
        self.assertEqual(len(FakeCodex.turn_estimates), turns_before)
        self.assertEqual(self.db.get_task("T2")["state"], "failed")

    def test_unqualified_verifier_plan_stops_attempt_before_dispatch_or_checkout(self):
        self.task()
        attempt = self.db.claim("T", "worker", 100)
        plan = SimpleNamespace(
            execution_qualified=False,
            reason="verifier_manifest_not_qualified",
            plan_sha256="a" * 64,
        )
        with (
            patch.object(supervisor, "prepare_verification_plan", return_value=plan),
            patch.object(supervisor, "metered_client") as dispatch,
            patch.object(supervisor, "runner") as sandbox,
            patch.object(supervisor, "git") as git,
        ):
            supervisor.run_attempt(self.state, attempt["id"])
        self.assertEqual(self.db.get_task("T")["state"], "failed")
        self.assertEqual(self.db.list_attempts()[0]["state"], "failed")
        dispatch.assert_not_called()
        sandbox.assert_not_called()
        git.assert_not_called()

    def test_verifier_blocks_candidate_when_attempt_plan_identity_changed(self):
        self.task()
        attempt = self.db.claim("T", "worker", 100)
        task = self.db.get_task("T")
        plan = prepare_verification_plan(self.state, self.db, task, self.cfg)
        evidence = {
            "checkout": str(self.state / "checkout"),
            "base": "base",
            "verification_plan": {**plan.identity(), "plan_sha256": "0" * 64},
        }
        self.db.submit(attempt["id"], attempt["epoch"], "candidate", evidence)
        with patch.object(supervisor, "git") as git:
            self.assertFalse(supervisor.verify_one(self.state, self.db.get_task("T")))
        git.assert_not_called()
        self.assertEqual(self.db.get_task("T")["state"], "submitted")

    def test_contract_activation_requires_both_matching_hash_ack_and_is_idempotent(self):
        self.candidate("EXP-001", target_state="integrated", team="Experience")
        proposal = self.db.propose_contract_transition(
            "schema-validation",
            1,
            2,
            self.cfg["contracts"]["schema-validation"],
            producer_task_id="EXP-001",
            producer_team="Experience",
            consumer_teams=["Platform"],
        )
        digest = proposal["body"]["contract_hash"]
        self.db.acknowledge_contract_transition("schema-validation", 2, "Experience", digest)
        self.reconcile("merged")
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 1)
        with self.assertRaisesRegex(StateError, "acknowledgement does not match"):
            self.db.acknowledge_contract_transition("schema-validation", 2, "Platform", "0" * 64)
        self.reconcile("merged")
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 1)
        self.db.acknowledge_contract_transition("schema-validation", 2, "Platform", digest)
        self.reconcile("merged")
        self.reconcile("merged")
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 2)
        self.assertEqual(
            self.db.get_contract("schema-validation")["body"],
            self.cfg["contracts"]["schema-validation"],
        )
        published = [event for event in self.db.events() if event["kind"] == "contract_published"]
        self.assertEqual(len(published), 2)  # original v1, one activation v2

    def test_contract_activation_reconciles_multiple_configured_contracts(self):
        self.cfg["contracts"]["research-output"] = {
            "version": 2,
            "producer": "Experience",
            "producer_task_id": "EXP-002",
            "consumers": ["Platform", "Research"],
            "schema": "research-output-v2",
        }
        self.db.put_contract("research-output", 1, {"schema": "research-output-v1"})
        self.candidate("EXP-002", target_state="integrated", team="Experience")
        transition = self.db.propose_contract_transition(
            "research-output",
            1,
            2,
            self.cfg["contracts"]["research-output"],
            producer_task_id="EXP-002",
            producer_team="Experience",
            consumer_teams=["Platform", "Research"],
        )
        digest = transition["body"]["contract_hash"]
        for team in ("Experience", "Platform", "Research"):
            self.db.acknowledge_contract_transition("research-output", 2, team, digest)

        supervisor.activate_contract(self.state, self.db)

        self.assertEqual(self.db.get_contract("research-output")["version"], 2)
        # An unrelated configured contract stays pending until its own producer and acks exist.
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 1)

    def test_contract_activation_revalidates_ack_history_after_publication(self):
        self.candidate("EXP-001", target_state="integrated", team="Experience")
        transition = self.db.propose_contract_transition(
            "schema-validation",
            1,
            2,
            self.cfg["contracts"]["schema-validation"],
            producer_task_id="EXP-001",
            producer_team="Experience",
            consumer_teams=["Platform"],
        )
        digest = transition["body"]["contract_hash"]
        for team in ("Experience", "Platform"):
            self.db.acknowledge_contract_transition("schema-validation", 2, team, digest)
        supervisor.activate_contract(self.state, self.db)
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 2)

        ack_key = self.db._contract_ack_key("schema-validation", 2, "Platform")
        self.db.db.execute("DELETE FROM decisions WHERE name=?", (ack_key,))
        with self.assertRaisesRegex(StateError, "activation evidence incomplete"):
            supervisor.activate_contract(self.state, self.db)

    def test_integrated_producer_without_two_lead_ack_keeps_contract_v1(self):
        self.candidate("EXP-001", target_state="integrated")
        self.reconcile("merged")
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 1)

    def test_contract_activation_rejects_persisted_proposal_that_differs_from_config(self):
        self.candidate("EXP-001", target_state="integrated", team="Experience")
        persisted = {**self.cfg["contracts"]["schema-validation"], "meaning": "unreviewed"}
        self.db.propose_contract_transition(
            "schema-validation",
            1,
            2,
            persisted,
            producer_task_id="EXP-001",
            producer_team="Experience",
            consumer_teams=["Platform"],
        )
        with self.assertRaisesRegex(StateError, "proposal is immutable"):
            supervisor.activate_contract(self.state, self.db)
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 1)

    def test_contract_proposal_rejects_stale_source_version(self):
        self.db.db.execute(
            "UPDATE contracts SET version=?, body=? WHERE name=?",
            (2, json.dumps({"meaning": "another v2"}), "schema-validation"),
        )
        with self.assertRaisesRegex(StateError, "source version is stale"):
            supervisor._configured_contract_transitions(self.cfg, self.db)

    def test_lead_ack_creates_missing_transition_in_legacy_v1_database(self):
        self.cfg["contracts"]["schema-validation"] = {
            "meaning": "strict validation v2",
            "version": 2,
            "producer": "Experience",
            "consumer": "Platform",
        }
        self.task("T", "Platform")
        attempt = self.db.claim("T", "worker", 100)
        lead, worker = FakeCodex(), FakeCodex()
        proposal = self.cfg["contracts"]["schema-validation"]
        contract_hash = hashlib.sha256(
            json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        lead.response = json.dumps(
            {
                "decision": "approve",
                "worker_brief": "bounded",
                "rationale": "reviewed v2",
                "contract_acks": [
                    {
                        "name": "schema-validation",
                        "version": 2,
                        "contract_hash": contract_hash,
                    }
                ],
            }
        )
        worker.response = "{malformed"

        from contextlib import contextmanager

        @contextmanager
        def fake_metered_client(*args, **kwargs):
            yield lead if kwargs["context"]["role"] == "lead" else worker

        with (
            patch.object(supervisor, "metered_client", side_effect=fake_metered_client),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "git", return_value=" M src/example.py"),
            patch.object(supervisor, "process_identity", return_value="fake"),
        ):
            supervisor.run_attempt(self.state, attempt["id"])

        transition = self.db.get_contract_transition("schema-validation", 2)
        self.assertIsNotNone(transition)
        ack = self.db.db.execute(
            "SELECT body FROM decisions WHERE name LIKE 'contract-ack/%'"
        ).fetchone()
        self.assertIsNotNone(ack)
        self.assertEqual(json.loads(ack[0])["team"], "Platform")

    def test_lead_ack_routes_generic_contract_by_exact_identity_and_team(self):
        proposal = {
            "version": 2,
            "producer": "Experience",
            "producer_task_id": "EXP-002",
            "consumers": ["Platform", "Research"],
            "schema": "research-output-v2",
        }
        self.cfg["contracts"]["research-output"] = proposal
        self.db.put_contract("research-output", 1, {"schema": "research-output-v1"})
        self.task("T", "Research")
        attempt = self.db.claim("T", "worker", 100)
        contract_hash = hashlib.sha256(
            json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        lead, worker = FakeCodex(), FakeCodex()
        lead.response = json.dumps(
            {
                "decision": "approve",
                "worker_brief": "bounded",
                "rationale": "reviewed research-output v2",
                "contract_acks": [
                    {
                        "name": "research-output",
                        "version": 2,
                        "contract_hash": contract_hash,
                    }
                ],
            }
        )
        worker.response = "{malformed"

        from contextlib import contextmanager

        @contextmanager
        def fake_metered_client(*args, **kwargs):
            yield lead if kwargs["context"]["role"] == "lead" else worker

        with (
            patch.object(supervisor, "metered_client", side_effect=fake_metered_client),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "git", return_value=" M src/example.py"),
            patch.object(supervisor, "process_identity", return_value="fake"),
        ):
            supervisor.run_attempt(self.state, attempt["id"])

        transition = self.db.get_contract_transition("research-output", 2)
        self.assertIsNotNone(transition)
        ack = self.db.db.execute(
            "SELECT body FROM decisions WHERE name LIKE 'contract-ack/%' ORDER BY name"
        ).fetchall()
        decoded = [json.loads(row[0]) for row in ack]
        self.assertEqual(
            decoded,
            [
                {
                    "kind": "contract_acknowledgement",
                    "name": "research-output",
                    "version": 2,
                    "team": "Research",
                    "contract_hash": contract_hash,
                }
            ],
        )
        prompt = lead.turn_calls[0][1]
        packet = json.loads(prompt.rsplit("\n", 1)[1])
        self.assertEqual(
            packet["proposed_contracts"],
            [
                {
                    "name": "research-output",
                    "version": 2,
                    "contract_hash": contract_hash,
                    "producer_task_id": "EXP-002",
                    "producer_team": "Experience",
                    "consumer_teams": ["Platform", "Research"],
                    "body": proposal,
                }
            ],
        )

    def test_lead_contract_ack_validator_rejects_unknown_duplicate_and_nonparticipant(self):
        transition = {
            "body": {
                "name": "research-output",
                "to_version": 2,
                "contract_hash": "a" * 64,
                "producer_team": "Experience",
                "consumer_teams": ["Platform", "Research"],
            }
        }
        exact = {"name": "research-output", "version": 2, "contract_hash": "a" * 64}
        self.assertEqual(
            supervisor._validate_lead_contract_acks(
                [exact], team="Research", transitions=[transition]
            ),
            [exact],
        )
        for team, acknowledgements in (
            ("Research", [exact, exact]),
            ("Experience", [{**exact, "contract_hash": "b" * 64}]),
            ("Unrelated", [exact]),
            ("Research", [{**exact, "version": True}]),
        ):
            with self.subTest(team=team, acknowledgements=acknowledgements):
                with self.assertRaises(ValueError):
                    supervisor._validate_lead_contract_acks(
                        acknowledgements, team=team, transitions=[transition]
                    )

    def test_activate_contract_accepts_exact_legacy_v2_without_creating_transition(self):
        proposal = self.cfg["contracts"]["schema-validation"]
        self.db.db.execute(
            "INSERT INTO contracts(name,version,body) VALUES(?,?,?)",
            ("schema-validation", 2, json.dumps(proposal, sort_keys=True, separators=(",", ":"))),
        )
        supervisor.activate_contract(self.state, self.db)
        self.assertIsNone(self.db.get_contract_transition("schema-validation", 2))

    def test_initial_v1_contract_needs_no_transition_evidence(self):
        proposal = {"version": 1, "producer": "Research", "consumer": "Platform"}
        self.cfg["contracts"] = {"research-output": proposal}
        self.db.put_contract("research-output", 1, dict(reversed(list(proposal.items()))))

        self.assertEqual(supervisor._configured_contract_transitions(self.cfg, self.db), [])
        self.assertIsNone(self.db.get_contract_transition("research-output", 1))

    def test_initial_v1_contract_rejects_boolean_integer_drift(self):
        published = {
            "version": 1,
            "producer": "Research",
            "consumer": "Platform",
            "strict": False,
            "nested": {"enabled": True},
        }
        configured = {
            **published,
            "strict": 0,
            "nested": {"enabled": 1},
        }
        self.cfg["contracts"] = {"research-output": configured}
        self.db.put_contract("research-output", 1, published)
        contract_before = self.db.get_contract("research-output")
        events_before = self.db.events()

        with self.assertRaisesRegex(StateError, "source version is stale"):
            supervisor._configured_contract_transitions(self.cfg, self.db)

        self.assertEqual(self.db.get_contract("research-output"), contract_before)
        self.assertEqual(self.db.events(), events_before)
        self.assertIsNone(self.db.get_contract_transition("research-output", 1))

    def test_legacy_v2_contract_rejects_nested_boolean_integer_drift(self):
        published = {
            **self.cfg["contracts"]["schema-validation"],
            "extension": {"enabled": True},
        }
        configured = {**published, "extension": {"enabled": 1}}
        self.cfg["contracts"] = {"schema-validation": configured}
        self.db.db.execute(
            "INSERT INTO contracts(name,version,body) VALUES(?,?,?)",
            (
                "schema-validation",
                2,
                json.dumps(published, sort_keys=True, separators=(",", ":")),
            ),
        )
        contract_before = self.db.get_contract("schema-validation")
        events_before = self.db.events()

        with self.assertRaisesRegex(StateError, "source version is stale"):
            supervisor._configured_contract_transitions(self.cfg, self.db)

        self.assertEqual(self.db.get_contract("schema-validation"), contract_before)
        self.assertEqual(self.db.events(), events_before)
        self.assertIsNone(self.db.get_contract_transition("schema-validation", 2))

    def test_active_generic_contract_without_transition_fails_closed(self):
        proposal = {"version": 2, "producer": "Research", "consumer": "Platform"}
        self.cfg["contracts"] = {"research-output": proposal}
        self.db.db.execute(
            "INSERT INTO contracts(name,version,body) VALUES(?,?,?)",
            ("research-output", 2, json.dumps(proposal, sort_keys=True, separators=(",", ":"))),
        )
        contracts_before = self.db.get_contract("research-output")
        events_before = self.db.events()

        with self.assertRaisesRegex(StateError, "publication evidence missing"):
            supervisor._configured_contract_transitions(self.cfg, self.db)

        self.assertEqual(self.db.get_contract("research-output"), contracts_before)
        self.assertEqual(self.db.events(), events_before)
        self.assertIsNone(self.db.get_contract_transition("research-output", 2))

    def test_published_generic_contract_with_orphan_activation_evidence_fails_closed(self):
        proposal = {"version": 2, "producer": "Research", "consumer": "Platform"}
        self.cfg["contracts"] = {"research-output": proposal}
        self.db.put_contract("research-output", 1, {"version": 1, "producer": "Research"})
        self.candidate("RESEARCH-1", target_state="integrated", team="Research")
        transition = self.db.propose_contract_transition(
            "research-output",
            1,
            2,
            proposal,
            producer_task_id="RESEARCH-1",
            producer_team="Research",
            consumer_teams=["Platform"],
        )
        digest = transition["body"]["contract_hash"]
        self.db.acknowledge_contract_transition("research-output", 2, "Research", digest)
        self.db.acknowledge_contract_transition("research-output", 2, "Platform", digest)
        self.db.activate_contract_transition("research-output", 2)
        self.db.db.execute(
            "DELETE FROM decisions WHERE name=?",
            (self.db._contract_transition_key("research-output", 2),),
        )
        events_before = self.db.events()
        acknowledgements_before = [
            self.db.get_decision(self.db._contract_ack_key("research-output", 2, team))
            for team in ("Research", "Platform")
        ]

        with self.assertRaisesRegex(StateError, "publication evidence missing"):
            supervisor.activate_contract(self.state, self.db)

        self.assertEqual(self.db.get_contract("research-output")["version"], 2)
        self.assertEqual(self.db.events(), events_before)
        self.assertEqual(
            [
                self.db.get_decision(self.db._contract_ack_key("research-output", 2, team))
                for team in ("Research", "Platform")
            ],
            acknowledgements_before,
        )
        activation_key = self.db._contract_transition_key("research-output", 2) + "/activated"
        self.assertIsNotNone(self.db.get_decision(activation_key))

    def test_schema_validation_v3_without_transition_fails_closed(self):
        proposal = {**self.cfg["contracts"]["schema-validation"], "version": 3}
        self.cfg["contracts"]["schema-validation"] = proposal
        self.db.db.execute(
            "INSERT INTO contracts(name,version,body) VALUES(?,?,?)",
            ("schema-validation", 3, json.dumps(proposal, sort_keys=True, separators=(",", ":"))),
        )

        with self.assertRaisesRegex(StateError, "publication evidence missing"):
            supervisor._configured_contract_transitions(self.cfg, self.db)

        self.assertEqual(self.db.get_contract("schema-validation")["version"], 3)
        self.assertIsNone(self.db.get_contract_transition("schema-validation", 3))

    def test_legacy_v2_with_orphan_acknowledgement_fails_closed(self):
        proposal = self.cfg["contracts"]["schema-validation"]
        self.db.db.execute(
            "INSERT INTO contracts(name,version,body) VALUES(?,?,?)",
            ("schema-validation", 2, json.dumps(proposal, sort_keys=True, separators=(",", ":"))),
        )
        ack_key = self.db._contract_ack_key("schema-validation", 2, "Platform")
        with self.db._tx():
            self.db._put_decision_in_transaction(
                ack_key,
                {
                    "kind": "contract_acknowledgement",
                    "name": "schema-validation",
                    "version": 2,
                    "team": "Platform",
                    "contract_hash": hashlib.sha256(
                        json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                },
            )

        with self.assertRaisesRegex(StateError, "orphan transition evidence"):
            supervisor._configured_contract_transitions(self.cfg, self.db)

        self.assertIsNotNone(self.db.get_decision(ack_key))

    def test_configured_contract_failure_leaves_no_earlier_proposal(self):
        early = {
            "version": 2,
            "producer": "Research",
            "consumer": "Platform",
            "producer_task_id": "EARLY-1",
        }
        late = {
            "version": 2,
            "producer": "Research",
            "consumer": "Platform",
            "producer_task_id": "LATE-1",
        }
        self.cfg["contracts"] = {"alpha-contract": early, "zeta-contract": late}
        self.db.put_contract("alpha-contract", 1, {"version": 1})
        self.db.put_contract("zeta-contract", 1, {"version": 1})
        self.db.db.execute(
            "INSERT INTO contracts(name,version,body) VALUES(?,?,?)",
            ("zeta-contract", 2, json.dumps(late, sort_keys=True, separators=(",", ":"))),
        )
        events_before = self.db.events()

        with self.assertRaisesRegex(StateError, "publication evidence missing"):
            supervisor._configured_contract_transitions(self.cfg, self.db)

        self.assertIsNone(self.db.get_contract_transition("alpha-contract", 2))
        self.assertEqual(self.db.events(), events_before)

    def test_contract_configuration_count_limit_precedes_database_reads(self):
        self.cfg["contracts"] = {"first": {}, "second": {}}
        with (
            patch.object(supervisor, "CONTRACT_TRANSITION_BATCH_MAX_PROPOSALS", 1),
            patch.object(self.db, "get_contract", side_effect=AssertionError("unexpected read")),
        ):
            with self.assertRaisesRegex(StateError, "configuration exceeds transition limit"):
                supervisor._configured_contract_transitions(self.cfg, self.db)

    def run_quiescence(
        self, reason, *, pending=False, slots=1, readiness_change=False, review_pending=False
    ):
        if pending:
            self.candidate("EXP-001", target_state="integrated", team="Experience")
            self.db.add_task(
                "T",
                "team",
                "Synthetic blocked task",
                scope=["src/example.py"],
                contracts={"schema-validation": 3},
            )
        else:
            self.task()
            attempt = self.db.claim("T", "worker", 100)
            self.db.submit(attempt["id"], attempt["epoch"], "sha", {})
            self.db.verify("T", "sha", True, {})
            self.db.accept("T", "sha")
        if review_pending:
            self.db.propose_contract_transition(
                "schema-validation",
                1,
                2,
                self.cfg["contracts"]["schema-validation"],
                producer_task_id="EXP-001",
                producer_team="Experience",
                consumer_teams=["Platform"],
            )
        decision = {"slots": slots, "selected_tasks": [], "reason": "synthetic"}
        events_before = len(self.db.events())
        with (
            patch.object(supervisor, "Codex", FakeCodex),
            patch.object(FakeCodex, "call", return_value={"ordinaryUsageAllowed": True}),
            patch.object(supervisor, "process_identity", return_value="fake"),
            patch.object(supervisor, "available_memory", return_value=10**10),
            patch.object(supervisor, "status", return_value={}),
            patch.object(supervisor, "render_desk"),
            patch.object(
                supervisor.time, "sleep", side_effect=lambda _: (self.state / "STOP").touch()
            ),
            patch.object(supervisor.time, "monotonic", return_value=0),
            patch("engineering.planning.plan_next", return_value=None),
            patch("engineering.resources.choose_capacity", return_value=decision),
            patch.object(Store, "ready_tasks", return_value=[]),
            patch("engineering.chief.chief") as executive,
            patch.object(FakeCodex, "turn") as model_turn,
        ):
            supervisor.supervise(self.state, max_seconds=10)
            (self.state / "STOP").unlink(missing_ok=True)
            if readiness_change:
                # Task rows stay byte-for-byte unchanged; only the current contract
                # version changes, so a readiness-aware trigger must get a new identity.
                before = self.db.list_tasks()
                proposal = self.cfg["contracts"]["schema-validation"]
                transition = self.db.get_contract_transition("schema-validation", 2)
                if transition is None:
                    transition = self.db.propose_contract_transition(
                        "schema-validation",
                        1,
                        2,
                        proposal,
                        producer_task_id="EXP-001",
                        producer_team="Experience",
                        consumer_teams=["Platform"],
                    )
                digest = transition["body"]["contract_hash"]
                self.db.acknowledge_contract_transition(
                    "schema-validation", 2, "Experience", digest
                )
                self.db.acknowledge_contract_transition("schema-validation", 2, "Platform", digest)
                self.db.activate_contract_transition("schema-validation", 2)
                self.assertEqual(self.db.list_tasks(), before)
            supervisor.supervise(self.state, max_seconds=10)
        executive.assert_not_called()
        model_turn.assert_not_called()
        handoffs = [
            event
            for event in self.db.events()[events_before:]
            if event["kind"] == "decision_recorded"
            and event.get("entity", "").startswith("sol-handoff/")
        ]
        self.assertEqual(len(handoffs), 2 if readiness_change else 1)
        if readiness_change:
            bodies = [self.db.get_decision(event["entity"])["body"] for event in handoffs]
            self.assertNotEqual(bodies[0]["fingerprint"], bodies[1]["fingerprint"])
        details = self.db.get_decision(handoffs[-1]["entity"])["body"]
        self.assertEqual(details["reason"], "blocked-dependencies" if readiness_change else reason)
        (self.state / "STOP").unlink(missing_ok=True)

    def test_quiescence_catalogue_exhausted_handoff_has_no_chief_call(self):
        self.run_quiescence("contract-reviews-pending")

    def test_quiescence_blocked_dependencies_handoff_has_no_chief_call(self):
        self.run_quiescence("contract-reviews-pending", pending=True)

    def test_quiescence_handoff_identity_tracks_current_contract_readiness(self):
        self.run_quiescence("contract-reviews-pending", pending=True, readiness_change=True)

    def test_quiescence_unavailable_quota_handoff_has_no_chief_call(self):
        self.run_quiescence("quota-unavailable", slots=0)

    def test_quiescence_routes_pending_contract_reviews_to_sol(self):
        self.run_quiescence("contract-reviews-pending", review_pending=True)

    def test_quiescence_handoff_survives_stable_blocked_verifier(self):
        self.task()
        attempt = self.db.claim("T", "worker", 100)
        self.db.submit(attempt["id"], attempt["epoch"], "sha", {})
        decision = {"slots": 1, "selected_tasks": [], "reason": "synthetic"}
        with (
            patch.object(supervisor, "Codex", FakeCodex),
            patch.object(FakeCodex, "call", return_value={"ordinaryUsageAllowed": True}),
            patch.object(supervisor, "process_identity", return_value="fake"),
            patch.object(supervisor, "available_memory", return_value=10**10),
            patch.object(supervisor, "status", return_value={}),
            patch.object(supervisor, "render_desk"),
            patch.object(supervisor.time, "sleep", side_effect=lambda _: None),
            patch.object(supervisor.time, "monotonic", return_value=0),
            patch("engineering.planning.plan_next", return_value=None),
            patch("engineering.resources.choose_capacity", return_value=decision),
            patch.object(Store, "ready_tasks", return_value=[]),
            patch.object(supervisor, "verify_one", return_value=False) as verify,
            patch("engineering.chief.chief") as executive,
            patch.object(FakeCodex, "turn") as model_turn,
        ):
            supervisor.supervise(self.state, max_seconds=10)
        verify.assert_called_once()
        executive.assert_not_called()
        model_turn.assert_not_called()
        handoffs = [
            event
            for event in self.db.events()
            if event["kind"] == "decision_recorded"
            and event.get("entity", "").startswith("sol-handoff/")
        ]
        self.assertEqual(len(handoffs), 1)
        self.assertEqual(self.db.get_task("T")["state"], "submitted")
        (self.state / "STOP").unlink(missing_ok=True)

    def test_stop_prevents_quiescence_resource_or_handoff_work(self):
        (self.state / "STOP").touch()
        with (
            patch.object(supervisor, "Codex", FakeCodex),
            patch.object(FakeCodex, "call") as account_call,
            patch("engineering.resources.choose_capacity") as capacity,
            patch("engineering.chief.chief") as executive,
            patch.object(supervisor, "status", return_value={}),
        ):
            supervisor.supervise(self.state, max_seconds=10)
        account_call.assert_not_called()
        capacity.assert_not_called()
        executive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
