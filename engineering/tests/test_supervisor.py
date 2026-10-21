"""No-model fault harness around real durable state and mocked external effects."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from engineering import supervisor
from engineering.confinement import SandboxResult
from engineering.git_gate import GitGate, VerificationPolicy
from engineering.org_state import StateError, Store
from engineering.tests.test_service import deployment as deployment_fixture
from engineering.verification_plan import BoundCommand, prepare_verification_plan


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


def existing_review_allowlist(db, transitions):
    """Inject a prepared source gate for focused contract replay unit tests."""
    rows = db.db.execute(
        "SELECT d.name,d.body FROM decisions d WHERE d.name LIKE 'contract-review/%' "
        "AND d.version=(SELECT MAX(latest.version) FROM decisions latest WHERE latest.name=d.name)"
    ).fetchall()
    requests = []
    for row in rows:
        try:
            body = json.loads(row["body"])["body"]
            if type(body) is dict:
                requests.append(body)
        except (TypeError, ValueError, KeyError):
            continue
    return {
        (transition["name"], transition["version"]): tuple(
            body["request_id"]
            for body in requests
            if body.get("name") == transition["name"]
            and body.get("version") == transition["version"]
            and body.get("contract_hash") == transition["body"]["contract_hash"]
        )
        for transition in transitions
    }


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

    def test_spawn_backoff_wait_uses_earliest_cooldown_with_mixed_pending_blockers(self):
        readiness = [
            {
                "task_id": "first",
                "state": "pending",
                "blockers": [{"kind": "attempt_spawn_backoff", "retry_at": 1030.0}],
            },
            {
                "task_id": "second",
                "state": "pending",
                "blockers": [{"kind": "attempt_spawn_backoff", "retry_at": 1020.0}],
            },
            {"task_id": "done", "state": "integrated", "blockers": []},
        ]
        self.assertEqual(supervisor._spawn_backoff_deadline(readiness), 1020.0)
        readiness[1] = {
            "task_id": "dependency-blocked",
            "state": "pending",
            "blockers": [{"kind": "dependency", "id": "producer", "state": "failed"}],
        }
        self.assertEqual(supervisor._spawn_backoff_deadline(readiness), 1030.0)
        readiness[1]["blockers"].append({"kind": "attempt_spawn_backoff", "retry_at": 1020.0})
        self.assertEqual(supervisor._spawn_backoff_deadline(readiness), 1020.0)
        first_token = supervisor._spawn_backoff_plan_token(readiness, revision=4)
        self.assertEqual(first_token, supervisor._spawn_backoff_plan_token(readiness, revision=4))
        self.assertNotEqual(
            first_token, supervisor._spawn_backoff_plan_token(readiness, revision=5)
        )

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
        later_binding = BoundCommand(
            Path("/trusted/native-check"),
            "a" * 64,
            Path("/trusted/native-main"),
            "b" * 40,
            Path("/trusted/later.py"),
            "c" * 64,
            later_result,
        )
        first_binding = BoundCommand(
            Path("/trusted/python"),
            "d" * 64,
            Path("/trusted/python-main"),
            "e" * 40,
            Path("/trusted/acceptance.py"),
            "f" * 64,
            first_result,
        )
        first_result["kind"] = "exit_zero"
        with self.assertRaisesRegex(ValueError, "bound_command_result_invalid"):
            BoundCommand(
                Path("/trusted/python"),
                "d" * 64,
                Path("/trusted/python-main"),
                "e" * 40,
                Path("/trusted/acceptance.py"),
                "f" * 64,
                {"kind": {"mutable": "nested"}},
            )
        verifier = SimpleNamespace(
            commands=commands,
            command_bindings=(first_binding, later_binding),
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
        self.assertTrue(box.run.call_args_list[0].kwargs["strict_stdout_utf8"])
        self.assertTrue(later_call.kwargs["strict_stdout_utf8"])
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
        self.assertEqual(first_binding.result["kind"], "acceptance_json")
        with self.assertRaises(TypeError):
            first_binding.result["kind"] = "exit_zero"
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

    def test_only_bound_json_result_commands_require_strict_stdout_utf8(self):
        self.assertTrue(
            supervisor._bound_command_requires_strict_stdout_utf8(
                SimpleNamespace(result={"kind": "acceptance_json"})
            )
        )
        self.assertFalse(
            supervisor._bound_command_requires_strict_stdout_utf8(
                SimpleNamespace(result={"kind": "exit_zero"})
            )
        )
        self.assertFalse(supervisor._bound_command_requires_strict_stdout_utf8(None))

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
            details = {
                "task": task_id,
                "submitted_sha": sha,
                "attempt_id": attempt["id"],
                "epoch": attempt["epoch"],
                "candidate_sha": "merged",
                "expected_tip": "base",
                "receipt": "receipt",
            }
            self.db.verify(task_id, sha, True, details)
        if target_state in ("accepted", "integrated"):
            self.db.accept(task_id, sha)
        if target_state == "integrated":
            self.db.integrate(task_id, sha, "merged")
        return attempt

    def approve_contract_review(self, name, version, team):
        key = self.db._contract_ack_key(name, version, team)
        existing = self.db.get_decision(key)
        if existing is not None:
            return existing
        packet = {
            "source_commit": "a" * 40,
            "evidence_refs": [{"ref": "compatibility:synthetic", "sha256": "b" * 64}],
            "compatibility_analysis": "Reviewed the synthetic contract and all participants.",
            "migration_plan": None,
        }
        evidence_revision = hashlib.sha256(
            f"supervisor-test:{name}:{version}:{team}".encode()
        ).hexdigest()
        request = self.db.prepare_contract_review(name, version, team, evidence_revision, packet)
        body = request["body"]
        self.db.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
        self.db.record_contract_review_session(
            body["request_id"],
            body["packet_hash"],
            "synthetic-review",
            "synthetic:independent-reviewer",
            body["dispatch_id"],
            {"pid": 123, "birth": "synthetic"},
        )
        result = {
            "decision": "approve",
            "compatibility": "compatible",
            "rationale": "Synthetic review complete.",
            "findings": [],
            "reviewed_evidence_refs": ["compatibility:synthetic"],
            "affected_consumers_reviewed": body["packet"]["affected_consumer_task_ids"],
            "migration_plan": None,
        }
        raw = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        response = self.db.record_contract_review_response(
            body["request_id"],
            body["packet_hash"],
            body["dispatch_id"],
            "synthetic-turn",
            raw,
        )
        self.db.finish_contract_review(
            body["request_id"], body["packet_hash"], response["body"]["raw_sha256"], result
        )
        return self.db.get_decision(key)

    def intent(self, task_id="T", submitted="submitted"):
        candidate = self.db.get_candidate(task_id)
        intent = {
            "task": task_id,
            "submitted_sha": submitted,
            "attempt_id": candidate["attempt_id"],
            "epoch": candidate["epoch"],
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

        def publish(candidate, receipt_hash, expected_tip):
            if tip == candidate.sha:
                return candidate.sha
            if tip != candidate.integration_tip or expected_tip != tip:
                raise RuntimeError("integration_intent_requires_reconciliation")
            calls.append(("update-ref", candidate.sha, tip))
            return candidate.sha

        gate.publish.side_effect = publish
        with (
            patch.object(supervisor, "git", side_effect=fake_git),
            patch.object(supervisor, "GitGate", return_value=gate),
            patch.object(
                supervisor,
                "_prepare_contract_review_requests",
                side_effect=lambda _state, db, _cfg, transitions: existing_review_allowlist(
                    db, transitions
                ),
            ),
        ):
            supervisor.reconcile(self.state, self.db)
        return calls, gate

    def activate_existing_reviews(self):
        with patch.object(
            supervisor,
            "_prepare_contract_review_requests",
            side_effect=lambda _state, db, _cfg, transitions: existing_review_allowlist(
                db, transitions
            ),
        ):
            return supervisor.activate_contract(self.state, self.db)

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

    def test_run_attempt_entry_STOP_cancels_claim_before_execution(self):
        self.task()
        attempt = self.db.claim("T", "synthetic-worker")
        (self.state / "STOP").touch()
        with (
            patch.object(supervisor, "Codex") as codex,
            patch.object(supervisor, "metered_client") as transport,
            patch.object(supervisor, "process_identity", return_value="synthetic:owner"),
        ):
            supervisor.run_attempt(self.state, attempt["id"])
        codex.assert_not_called()
        transport.assert_not_called()
        self.assertEqual(self.db.get_task("T")["state"], "pending")
        stored = self.db.list_attempts("T")[0]
        self.assertEqual(stored["state"], "stopped_before_work")
        self.assertIsNone(stored["session"])
        self.assertEqual(
            self.db.attempt_child_start_identity(attempt["id"], attempt["epoch"]),
            {"pid": os.getpid(), "birth": "synthetic:owner"},
        )
        self.assertFalse(
            any(
                event["kind"] == "session_stage_recorded"
                for event in self.db.events()
                if event["body"].get("attempt_id") == attempt["id"]
            )
        )
        self.assertFalse(self.db.usage_summary()["total"])

    def test_duplicate_same_process_child_start_cannot_repeat_attempt(self):
        self.task()
        attempt = self.db.claim("T", "synthetic-worker")
        identity = {"pid": os.getpid(), "birth": "synthetic:owner"}
        self.db.record_attempt_child_start(attempt["id"], attempt["epoch"], identity)
        events_before = len(self.db.events())
        with (
            patch.object(supervisor, "process_identity", return_value="synthetic:owner"),
            patch.object(supervisor, "Codex") as codex,
            patch.object(supervisor, "metered_client") as transport,
            patch.object(supervisor, "runner") as sandbox,
        ):
            supervisor.run_attempt(self.state, attempt["id"])
        codex.assert_not_called()
        transport.assert_not_called()
        sandbox.assert_not_called()
        self.assertEqual(len(self.db.events()), events_before)
        self.assertEqual(self.db.get_task("T")["state"], "running")
        self.assertEqual(self.db.list_attempts("T")[0]["state"], "running")

    def test_expired_child_start_identity_is_reaped_idempotently(self):
        self.task()
        attempt = self.db.claim("T", "synthetic-worker", lease_seconds=5)
        identity = {"pid": 4321, "birth": "darwin:4321:1000:000001:501"}
        self.db.record_attempt_child_start(attempt["id"], attempt["epoch"], identity)
        self.now += 5
        with (
            patch.object(supervisor.platform, "system", return_value="Darwin"),
            patch.object(supervisor, "terminate_owned", return_value=True) as terminate,
            patch.object(supervisor, "process_identity", return_value=None),
            patch.object(supervisor.os, "kill", side_effect=ProcessLookupError),
        ):
            self.assertEqual(supervisor.reap_expired(self.state, self.db), [attempt["id"]])
            self.assertEqual(supervisor.reap_expired(self.state, self.db), [])
        self.assertEqual(
            [call.args for call in terminate.call_args_list],
            [(identity["pid"], identity["birth"])],
        )
        marker = json.loads(
            (self.state / "attempts" / str(attempt["id"]) / "reaped.json").read_text()
        )
        self.assertEqual(marker["attempt_id"], attempt["id"])
        self.assertEqual(self.db.get_task("T")["state"], "pending")
        self.assertEqual(
            self.db.attempt_process_reap_confirmation(attempt["id"], attempt["epoch"]),
            {"disposition": "processes_reaped", "process_identities": [identity]},
        )
        replacement = self.db.claim("T", "direct-claimer", lease_seconds=5)
        self.assertEqual(replacement["epoch"], attempt["epoch"] + 1)

    def test_live_child_after_failed_termination_has_no_reap_marker(self):
        self.task()
        attempt = self.db.claim("T", "synthetic-worker", lease_seconds=5)
        identity = {"pid": 4321, "birth": "darwin:4321:1000:000001:501"}
        self.db.record_attempt_child_start(attempt["id"], attempt["epoch"], identity)
        self.now += 5
        marker = self.state / "attempts" / str(attempt["id"]) / "reaped.json"
        with (
            patch.object(supervisor.platform, "system", return_value="Darwin"),
            patch.object(supervisor, "terminate_owned", return_value=False),
            patch.object(supervisor, "process_identity", return_value=identity["birth"]),
        ):
            with self.assertRaisesRegex(StateError, "expired_process_still_owned"):
                supervisor.reap_expired(self.state, self.db)
        self.assertFalse(marker.exists())

    def test_live_pid_with_unreadable_birth_witness_has_no_reap_marker(self):
        self.task()
        attempt = self.db.claim("T", "synthetic-worker", lease_seconds=5)
        identity = {"pid": 4321, "birth": "darwin:4321:1000:000001:501"}
        self.db.record_attempt_child_start(attempt["id"], attempt["epoch"], identity)
        self.now += 5
        marker = self.state / "attempts" / str(attempt["id"]) / "reaped.json"
        with (
            patch.object(supervisor.platform, "system", return_value="Darwin"),
            patch.object(supervisor, "terminate_owned", return_value=False),
            patch.object(supervisor, "process_identity", return_value=None),
            patch.object(supervisor.os, "kill", return_value=None),
        ):
            with self.assertRaisesRegex(StateError, "expired_process_identity_unverifiable"):
                supervisor.reap_expired(self.state, self.db)
        self.assertFalse(marker.exists())

    def test_incomplete_session_runner_cannot_hide_child_start_owner(self):
        self.task()
        attempt = self.db.claim("T", "synthetic-worker", lease_seconds=5)
        child_identity = {"pid": 4321, "birth": "darwin:4321:1000:000001:501"}
        self.db.record_attempt_child_start(attempt["id"], attempt["epoch"], child_identity)
        self.db.record_stage_session(
            attempt["id"],
            attempt["epoch"],
            "synthetic-thread",
            "synthetic-model",
            {"stage": "lead"},
            stage="lead",
        )
        self.now += 5
        marker = self.state / "attempts" / str(attempt["id"]) / "reaped.json"
        with self.assertRaisesRegex(StateError, "expired_session_process_identity_invalid"):
            supervisor.reap_expired(self.state, self.db)
        self.assertFalse(marker.exists())

    def test_reap_crash_after_process_exit_before_receipt_keeps_claim_blocked(self):
        self.task()
        attempt = self.db.claim("T", "synthetic-worker", lease_seconds=5)
        identity = {"pid": 4321, "birth": "darwin:4321:1000:000001:501"}
        self.db.record_attempt_child_start(attempt["id"], attempt["epoch"], identity)
        self.now += 5
        marker = self.state / "attempts" / str(attempt["id"]) / "reaped.json"
        with (
            patch.object(supervisor.platform, "system", return_value="Darwin"),
            patch.object(supervisor, "terminate_owned", return_value=False),
            patch.object(supervisor, "process_identity", return_value=None),
            patch.object(supervisor.os, "kill", side_effect=ProcessLookupError),
            patch.object(
                self.db,
                "record_attempt_process_reaped",
                side_effect=RuntimeError("synthetic crash before receipt"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic crash before receipt"):
                supervisor.reap_expired(self.state, self.db)
        self.assertFalse(marker.exists())
        self.assertIsNone(
            self.db.attempt_process_reap_confirmation(attempt["id"], attempt["epoch"])
        )
        self.assertIsNone(self.db.claim("T", "direct-claimer", lease_seconds=5))
        with (
            patch.object(supervisor.platform, "system", return_value="Darwin"),
            patch.object(supervisor, "terminate_owned", return_value=False),
            patch.object(supervisor, "process_identity", return_value=None),
            patch.object(supervisor.os, "kill", side_effect=ProcessLookupError),
        ):
            self.assertEqual(supervisor.reap_expired(self.state, self.db), [])
        self.assertEqual(
            self.db.attempt_process_reap_confirmation(attempt["id"], attempt["epoch"]),
            {"disposition": "processes_reaped", "process_identities": [identity]},
        )
        self.assertEqual(self.db.claim("T", "after-reap", lease_seconds=5)["epoch"], 2)

    def test_reap_receipt_recovers_marker_after_reopen(self):
        self.task()
        attempt = self.db.claim("T", "synthetic-worker", lease_seconds=5)
        identity = {"pid": 4321, "birth": "darwin:4321:1000:000001:501"}
        self.db.record_attempt_child_start(attempt["id"], attempt["epoch"], identity)
        self.now += 5
        marker = self.state / "attempts" / str(attempt["id"]) / "reaped.json"
        with (
            patch.object(supervisor.platform, "system", return_value="Darwin"),
            patch.object(supervisor, "terminate_owned", return_value=False),
            patch.object(supervisor, "process_identity", return_value=None),
            patch.object(supervisor.os, "kill", side_effect=ProcessLookupError),
            patch.object(supervisor, "atomic", side_effect=RuntimeError("synthetic crash")),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic crash"):
                supervisor.reap_expired(self.state, self.db)
        self.assertFalse(marker.exists())
        self.assertEqual(
            self.db.attempt_process_reap_confirmation(attempt["id"], attempt["epoch"]),
            {"disposition": "processes_reaped", "process_identities": [identity]},
        )
        self.db.close()
        self.db = Store(self.state / "organization.sqlite", clock=lambda: self.now)
        with patch.object(supervisor, "log_event"):
            self.assertEqual(supervisor.reap_expired(self.state, self.db), [])
        self.assertTrue(marker.exists())
        replacement = self.db.claim("T", "after-reopen", lease_seconds=5)
        self.assertEqual(replacement["epoch"], attempt["epoch"] + 1)

    def test_malformed_child_start_history_fails_closed_without_reap_marker(self):
        self.task()
        attempt = self.db.claim("T", "synthetic-worker", lease_seconds=5)
        self.now += 5
        with self.db._tx():
            self.db._event(
                "attempt_child_started",
                "T",
                {
                    "attempt_id": attempt["id"],
                    "epoch": attempt["epoch"],
                    "process_identity": {"pid": 1, "birth": "invalid"},
                },
            )
        marker = self.state / "attempts" / str(attempt["id"]) / "reaped.json"
        with self.assertRaisesRegex(StateError, "attempt_child_start_history_invalid"):
            supervisor.reap_expired(self.state, self.db)
        self.assertFalse(marker.exists())

    def test_duplicate_child_start_history_fails_closed_without_reap_marker(self):
        self.task()
        attempt = self.db.claim("T", "synthetic-worker", lease_seconds=5)
        self.now += 5
        body = {
            "attempt_id": attempt["id"],
            "epoch": attempt["epoch"],
            "process_identity": {"pid": 4321, "birth": "darwin:4321:1000:000001:501"},
        }
        with self.db._tx():
            self.db._event("attempt_child_started", "T", body)
            self.db._event("attempt_child_started", "T", body)
        marker = self.state / "attempts" / str(attempt["id"]) / "reaped.json"
        with self.assertRaisesRegex(StateError, "attempt_child_start_history_invalid"):
            supervisor.reap_expired(self.state, self.db)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(platform.system() == "Darwin", "requires macOS process birth identity")
    def test_real_frozen_child_records_stop_before_work_and_rejects_duplicate(self):
        from engineering.tests.test_service import deployment_with_frozen_controller

        deployment_root = self.state / "frozen-child"
        state = deployment_with_frozen_controller(deployment_root)
        (state / "STOP").touch()
        db = Store(state / "organization.sqlite")
        attempt = db.claim("synthetic", "synthetic-supervisor", lease_seconds=30)
        db.close()
        command = [
            sys.executable,
            "-m",
            "engineering",
            "attempt",
            "--state",
            str(state),
            "--attempt",
            str(attempt["id"]),
        ]
        first = subprocess.run(
            command,
            cwd=state / "controller",
            start_new_session=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        reopened = Store(state / "organization.sqlite")
        try:
            stored = reopened.list_attempts("synthetic")[0]
            self.assertEqual(stored["state"], "stopped_before_work")
            self.assertEqual(reopened.get_task("synthetic")["state"], "pending")
            self.assertIsNone(stored["session"])
            identity = reopened.attempt_child_start_identity(attempt["id"], attempt["epoch"])
            self.assertNotEqual(identity["pid"], os.getpid())
            kinds = [event["kind"] for event in reopened.events()]
            self.assertEqual(kinds.count("attempt_child_started"), 1)
            self.assertEqual(kinds.count("attempt_stopped_before_work"), 1)
            self.assertFalse(any(kind == "session_stage_recorded" for kind in kinds))
            self.assertFalse(reopened.usage_summary()["total"])
            self.assertFalse((state / "integration").exists())
            event_count = len(reopened.events())
        finally:
            reopened.close()

        duplicate = subprocess.run(
            command,
            cwd=state / "controller",
            start_new_session=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(duplicate.returncode, 0, duplicate.stderr)
        reopened = Store(state / "organization.sqlite")
        try:
            self.assertEqual(len(reopened.events()), event_count)
            self.assertEqual(reopened.list_attempts("synthetic")[0]["state"], "stopped_before_work")
            self.assertEqual(reopened.get_task("synthetic")["state"], "pending")
        finally:
            reopened.close()

    def test_accepted_intent_before_publish_recovers_once(self):
        self.candidate()
        self.intent()
        calls, gate = self.reconcile("base")
        self.assertEqual(self.db.get_task("T")["state"], "integrated")
        self.assertEqual(sum(call[0] == "update-ref" for call in calls), 1)
        gate.load_receipt.assert_called_once_with("receipt", "merged")
        again, _ = self.reconcile("merged")
        self.assertFalse(any(call[0] == "update-ref" for call in again))
        events = [event for event in self.db.events() if event["kind"] == "candidate_integrated"]
        self.assertEqual(len(events), 1)

    def test_fixed_task_real_git_acceptance_checkpoint_and_next_dependency(self):
        from engineering import planning
        from engineering.planning import eligible

        def run_git(repo, *args):
            result = subprocess.run(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "core.fsmonitor=false",
                    "-C",
                    str(repo),
                    *args,
                ],
                text=True,
                capture_output=True,
                env=git_env,
                check=False,
            )
            if result.returncode:
                raise AssertionError(result.stderr)
            return result.stdout.strip()

        git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        git_env.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_OPTIONAL_LOCKS": "0",
                "GIT_NO_REPLACE_OBJECTS": "1",
            }
        )

        integration = self.state / "integration"
        integration.mkdir(exist_ok=True)
        run_git(integration, "init", "-b", "main")
        run_git(integration, "config", "user.name", "synthetic")
        run_git(integration, "config", "user.email", "synthetic@example.invalid")
        (integration / "src").mkdir()
        (integration / "src" / "base.py").write_text("BASE = True\n")
        run_git(integration, "add", "-A")
        run_git(integration, "commit", "-m", "base")
        base = run_git(integration, "rev-parse", "HEAD")
        run_git(integration, "update-ref", "refs/heads/research/active", base)

        self.cfg["contracts"] = {
            "research-output": {
                "version": 2,
                "producer": "Experience",
                "producer_task_id": "PLAT-001",
                "consumers": ["Platform"],
                "schema": "research-output-v2",
            }
        }
        self.db.put_contract("research-output", 1, {"schema": "research-output-v1"})
        self.cfg["catalogue"] = ["PLAT-001", "PLAT-002"]
        self.cfg["tasks"] = {
            "PLAT-001": {
                "team": "Experience",
                "title": "Add the synthetic capability",
                "brief": "Implement one bounded, testable source change.",
                "scope": ["src/capability.py"],
                "checks": [],
                "dependencies": [],
                "contracts": {},
            },
            "PLAT-002": {
                "team": "Platform",
                "title": "Continue from the accepted capability",
                "brief": "Consume the integrated first task.",
                "scope": ["src/consumer.py"],
                "checks": [],
                "dependencies": ["PLAT-001"],
                "contracts": {"research-output": 2},
            },
        }
        self.db.add_objective("offline-brain-engineering", "Synthetic integrated cycle")
        self.assertEqual(eligible(self.cfg, self.db), ["PLAT-001"])

        def synthetic_plan(expected_card, rationale, evidence):
            """Exercise the real planner through a deterministic, provider-free turn."""
            import json
            from unittest.mock import MagicMock

            from engineering import planning

            fake = MagicMock()
            fake.__enter__.return_value = fake
            fake.start.return_value = "synthetic-sol-thread"

            def turn(_thread, prompt, **kwargs):
                dossier = json.loads(prompt.rsplit("\n", 1)[-1])
                self.assertEqual(list(dossier["available_cards"]), [expected_card])
                self.assertEqual(kwargs["model"], planning.SOL)
                self.assertEqual(kwargs["dispatch_id"].split(":", 1)[0], "planning-proposal")
                return {
                    "text": json.dumps(
                        {"card": expected_card, "rationale": rationale, "evidence": evidence}
                    )
                }

            fake.turn.side_effect = turn
            with (
                patch.object(planning, "config", return_value=self.cfg),
                patch.object(planning, "metered_client", return_value=fake),
                patch.object(planning, "git", side_effect=run_git),
                patch.object(planning, "log_event"),
            ):
                selected = planning.plan_next(self.state, self.db)
            fake.turn.assert_called_once()
            return selected

        admitted = synthetic_plan(
            "PLAT-001",
            "This fixed task provides the first bounded capability.",
            "The isolated synthetic repository starts from the recorded base.",
        )
        self.assertEqual(admitted, "PLAT-001")
        task = self.db.get_task(admitted)
        attempt = self.db.claim(admitted, "synthetic-worker")
        plan = supervisor.prepare_verification_plan(self.state, self.db, task, self.cfg)
        self.assertTrue(plan.execution_qualified)
        self.assertEqual(task["scope"], ["src/capability.py"])
        self.assertEqual(attempt["task_id"], admitted)

        if platform.system() != "Darwin":
            self.skipTest("integrated worker execution requires disposable macOS Seatbelt")
        escape_path = integration / "sandbox-escape"
        worker_code = (
            "from pathlib import Path\n"
            "Path('src/capability.py').write_text('VALUE = 7\\n')\n"
            f"outside = Path({str(escape_path)!r})\n"
            "try:\n"
            "    outside.write_text('escape')\n"
            "except OSError:\n"
            "    pass\n"
            "else:\n"
            "    raise SystemExit('sandbox allowed an out-of-root write')\n"
        )
        python_executable = str(Path(sys.executable).resolve())
        trusted = self.state / "trusted"
        trusted.mkdir()
        (trusted / "pilot_acceptance.py").write_text(
            "import json, pathlib, sys\n"
            "checkout = pathlib.Path(sys.argv[2])\n"
            "if sys.argv[1] == 'PLAT-001':\n"
            "    passed = (checkout / 'src/capability.py').read_text() == 'VALUE = 7\\n'\n"
            "elif sys.argv[1] == 'PLAT-002':\n"
            "    sys.path.insert(0, str(checkout / 'src'))\n"
            "    from consumer import CONSUMED\n"
            "    passed = (checkout / 'src/capability.py').read_text() == 'VALUE = 7\\n' "
            "and CONSUMED == 8\n"
            "else:\n"
            "    passed = False\n"
            "print(json.dumps({'schema': 'org-pilot-acceptance-v1', "
            "'case': sys.argv[1], 'passed': passed}))\n"
            "raise SystemExit(0 if passed else 1)\n"
        )

        # Give the real snapshot path a complete, strictly validated synthetic deployment.
        # Its qualification flags describe only this fixture; no restored runtime is launched.
        import shutil

        from engineering.service import backup_snapshot, restore_snapshot, validate_deployment
        from engineering.usage_ledger import UsageLedger

        controller = self.state / "controller"
        controller.mkdir()
        shutil.copytree(
            Path(__file__).resolve().parents[1],
            controller / "engineering",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        codex = self.state / "synthetic-codex"
        codex.write_text("#!/bin/sh\nexit 0\n")
        codex.chmod(0o700)
        controller_manifest = {
            f"engineering/{path.relative_to(controller / 'engineering').as_posix()}": (
                hashlib.sha256(path.read_bytes()).hexdigest()
            )
            for path in sorted((controller / "engineering").rglob("*.py"))
        }
        controller_manifest["trusted/pilot_acceptance.py"] = hashlib.sha256(
            (trusted / "pilot_acceptance.py").read_bytes()
        ).hexdigest()
        (self.state / "controller-manifest.json").write_text(
            json.dumps(controller_manifest, sort_keys=True, indent=2)
        )
        adapter_sha = controller_manifest["engineering/codex_rpc.py"]
        codex_sha = hashlib.sha256(codex.read_bytes()).hexdigest()
        deployment_cfg = {
            "schema": "brain-engineering-deployment-v1",
            "python": sys.executable,
            "controller": str(controller.resolve()),
            "remote_publication_enabled": False,
            "codex_executable": str(codex),
            "codex_executable_sha256": codex_sha,
        }
        deployment_cfg_bytes = json.dumps(deployment_cfg, sort_keys=True, indent=2).encode()
        (self.state / "config.json").write_bytes(deployment_cfg_bytes)
        (self.state / "config.sha256").write_text(hashlib.sha256(deployment_cfg_bytes).hexdigest())
        (self.state / "confinement.json").write_text(
            json.dumps({"qualified": True, "evidence": "synthetic-fixture-only"})
        )
        (self.state / "native-profile.json").write_text(
            json.dumps(
                {
                    "qualified": True,
                    "adapter_sha256": adapter_sha,
                    "executable_sha256": codex_sha,
                    "evidence": "synthetic-fixture-only",
                }
            )
        )
        ledger = UsageLedger(self.state / "usage-ledger.sqlite")
        ledger.register(
            "synthetic-checkpoint",
            "synthetic-epoch",
            "gpt-6-luna",
            None,
            evidence="local synthetic snapshot only",
        )
        ledger.close()
        validate_deployment(self.state)

        class SyntheticCodex:
            def __init__(self, role):
                self.role = role
                self.proc = SimpleNamespace(pid=123)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def start(self, *_args):
                return "synthetic-" + self.role + "-thread"

            def turn(self, *_args, **kwargs):
                if self.role == "worker":
                    result = kwargs["tool"](
                        {
                            "argv": [python_executable, "-I", "-c", worker_code],
                            "timeout": 10,
                        }
                    )
                    if result["returncode"] != 0:
                        raise AssertionError(result["stderr"])
                    self_test.assertFalse(escape_path.exists())
                    text = json.dumps(
                        {
                            "summary": "Added one bounded capability.",
                            "tests": ["synthetic sandbox write and escape denial"],
                            "limitations": ["provider and verifier are synthetic"],
                        }
                    )
                elif self.role == "lead":
                    text = json.dumps(
                        {
                            "decision": "approve",
                            "worker_brief": "Add the fixed synthetic capability.",
                            "rationale": "The bounded task and exact contract proposal qualify.",
                            "contract_acks": [],
                        }
                    )
                else:
                    self_test.assertGreaterEqual(len(_args), 2)
                    packet = json.loads(_args[1].rsplit("\n", 1)[-1])
                    self_test.assertEqual(packet["schema"], "brain-independent-review-packet-v1")
                    self_test.assertEqual(packet["task"]["id"], "PLAT-001")
                    self_test.assertEqual(
                        packet["candidate"]["submitted_changed_paths"], ["src/capability.py"]
                    )
                    self_test.assertIn("VALUE = 7", packet["diff"])
                    acceptance = json.loads(packet["checks"][0]["stdout"])
                    self_test.assertEqual(acceptance["case"], "PLAT-001")
                    self_test.assertIs(acceptance["passed"], True)
                    text = json.dumps(
                        {
                            "approved": True,
                            "findings": [],
                            "rationale": "Synthetic exact-candidate review passed.",
                        }
                    )
                return {
                    "text": text,
                    "usage_terminal": True,
                    "usage_delta": {"totalTokens": 3},
                }

            def record_outcome(self, *_args, **_kwargs):
                return None

        self_test = self
        with (
            patch.object(
                supervisor,
                "metered_client",
                side_effect=lambda *_args, **kwargs: SyntheticCodex(kwargs["context"]["role"]),
            ),
            patch.object(supervisor, "process_identity", return_value="synthetic-process"),
        ):
            supervisor.run_attempt(self.state, attempt["id"])
        self.assertFalse(escape_path.exists())
        submitted_task = self.db.get_task(admitted)
        self.assertEqual(submitted_task["state"], "submitted", self.db.events())
        candidate_record = self.db.get_candidate(admitted)
        self.assertEqual(candidate_record["attempt_id"], attempt["id"])
        self.assertEqual(candidate_record["epoch"], attempt["epoch"])
        submitted_evidence = candidate_record["evidence"]
        self.assertEqual(submitted_evidence["verification_plan"], plan.identity())
        intent_path = self.state / "intents" / f"{admitted}.json"
        verification_events = []
        with (
            patch.object(
                supervisor,
                "metered_client",
                side_effect=lambda *_args, **kwargs: SyntheticCodex(kwargs["context"]["role"]),
            ),
            patch.object(supervisor, "process_identity", return_value="synthetic-process"),
            patch.object(
                Store,
                "integrate",
                side_effect=RuntimeError("synthetic interruption after Git ref CAS"),
            ) as crashed_integrate,
            patch.object(
                supervisor,
                "log_event",
                side_effect=lambda _state, kind, **values: verification_events.append(
                    (kind, values)
                ),
            ),
        ):
            self.assertTrue(supervisor.verify_one(self.state, submitted_task))
        self.assertTrue(intent_path.exists(), verification_events)
        intent = supervisor.read(intent_path)
        candidate_sha = intent["candidate_sha"]
        receipt_digest = intent["receipt"]
        crashed_integrate.assert_called_once_with(
            admitted, submitted_task["candidate_sha"], candidate_sha, intent
        )
        self.assertEqual(self.db.get_task(admitted)["state"], "accepted")
        self.assertEqual(run_git(integration, "rev-parse", GitGate.ACTIVE_REF), candidate_sha)
        database_path = self.state / "organization.sqlite"
        self.db.close()
        self.db = Store(database_path, clock=lambda: self.now)
        self.addCleanup(self.db.close)
        intent_path = self.state / "intents" / f"{admitted}.json"
        supervisor.atomic(intent_path, {**intent, "epoch": True})
        supervisor.reconcile(self.state, self.db)
        self.assertEqual(self.db.get_task(admitted)["state"], "accepted")
        self.assertEqual(run_git(integration, "rev-parse", GitGate.ACTIVE_REF), candidate_sha)
        supervisor.atomic(
            intent_path,
            {**intent, "candidate_sha": base},
        )
        with self.assertRaisesRegex(RuntimeError, "candidate parents"):
            supervisor.reconcile(self.state, self.db)
        self.assertEqual(self.db.get_task(admitted)["state"], "accepted")
        self.assertEqual(run_git(integration, "rev-parse", GitGate.ACTIVE_REF), candidate_sha)
        supervisor.atomic(intent_path, intent)
        receipt_path = self.state / "receipts" / f"{receipt_digest}.json"
        receipt_bytes = receipt_path.read_bytes()
        receipt_path.unlink()
        with self.assertRaisesRegex(RuntimeError, "receipt"):
            supervisor.reconcile(self.state, self.db)
        self.assertEqual(self.db.get_task(admitted)["state"], "accepted")
        self.assertEqual(self.db.contract_review_worklist()["count"], 0)
        forged = json.loads(receipt_bytes)
        forged["commands"] = []
        forged_bytes = json.dumps(forged, sort_keys=True, separators=(",", ":")).encode()
        forged_digest = hashlib.sha256(forged_bytes).hexdigest()
        (self.state / "receipts" / f"{forged_digest}.json").write_bytes(forged_bytes)
        supervisor.atomic(intent_path, {**intent, "receipt": forged_digest})
        with self.assertRaisesRegex(RuntimeError, "receipt"):
            supervisor.reconcile(self.state, self.db)
        self.assertEqual(self.db.get_task(admitted)["state"], "accepted")
        self.assertEqual(run_git(integration, "rev-parse", GitGate.ACTIVE_REF), candidate_sha)
        self.assertEqual(self.db.contract_review_worklist()["count"], 0)
        supervisor.atomic(intent_path, intent)
        receipt_path.write_bytes(receipt_bytes)
        prepare_contract_review = self.db.prepare_contract_review
        failed_after_first_request = False

        def interrupt_contract_review(name, version, team, evidence_revision, packet):
            nonlocal failed_after_first_request
            if team == "Platform" and not failed_after_first_request:
                failed_after_first_request = True
                raise RuntimeError("synthetic controller interruption after first review request")
            return prepare_contract_review(name, version, team, evidence_revision, packet)

        with patch.object(
            self.db,
            "prepare_contract_review",
            side_effect=interrupt_contract_review,
        ):
            supervisor.reconcile(self.state, self.db)
        partial_review_items = self.db.contract_review_worklist()["items"]
        partial_review_ids = [
            item["request_id"]
            for item in partial_review_items
            if item["team"] in {"Experience", "Platform"}
        ]
        self.assertEqual(len(partial_review_ids), 1)
        self.db.close()
        self.db = Store(database_path, clock=lambda: self.now)
        self.addCleanup(self.db.close)
        supervisor.reconcile(self.state, self.db)
        recovered_review_items = self.db.contract_review_worklist()["items"]
        recovered_review_ids = [
            item["request_id"]
            for item in recovered_review_items
            if item["team"] in {"Experience", "Platform"}
        ]
        self.assertEqual(len(recovered_review_ids), 2)
        self.assertIn(partial_review_ids[0], recovered_review_ids)
        supervisor.reconcile(self.state, self.db)

        self.assertEqual(self.db.get_task(admitted)["state"], "integrated")
        self.assertEqual(run_git(integration, "rev-parse", "main"), base)
        self.assertEqual(run_git(integration, "rev-parse", GitGate.ACTIVE_REF), candidate_sha)
        integrated_events = [
            event
            for event in self.db.events()
            if event["kind"] == "candidate_integrated" and event["entity"] == admitted
        ]
        self.assertEqual(len(integrated_events), 1)

        self.assertEqual(eligible(self.cfg, self.db), [], self.db.events())
        self.assertEqual(self.db.get_contract("research-output")["version"], 1)
        review_evidence = f"git-receipt:{receipt_digest}"
        transition = self.db.get_contract_transition("research-output", 2)
        contract_hash = transition["body"]["contract_hash"]
        review_items = self.db.contract_review_worklist()["items"]
        prepared_reviews = [
            self.db.get_contract_review_request(item["request_id"])
            for item in review_items
            if item["team"] in {"Experience", "Platform"}
        ]
        self.assertEqual(
            {item["body"]["team"] for item in prepared_reviews}, {"Experience", "Platform"}
        )
        for request in prepared_reviews:
            packet = request["body"]["packet"]
            self.assertEqual(packet["source_commit"], candidate_sha)
            self.assertEqual(packet["evidence_refs"], [review_evidence])
            self.assertIn(
                "not yet been independently established", packet["compatibility_analysis"]
            )

        # Simulate a controller interruption after response custody but before local replay.
        # Reopen SQLite before supervise resumes the durable review cycle.
        replay_request = prepared_reviews[0]["body"]
        self.db.begin_contract_review_dispatch(
            replay_request["request_id"], replay_request["packet_hash"]
        )
        self.db.record_contract_review_session(
            replay_request["request_id"],
            replay_request["packet_hash"],
            "synthetic-custodied-review-thread",
            "synthetic-reviewer",
            replay_request["dispatch_id"],
            {"pid": 123, "birth": "synthetic"},
        )
        replay_result = {
            "decision": "approve",
            "compatibility": "compatible",
            "rationale": "Custodied synthetic response survived controller restart.",
            "findings": [],
            "reviewed_evidence_refs": replay_request["packet"]["evidence_refs"],
            "affected_consumers_reviewed": replay_request["packet"]["affected_consumer_task_ids"],
            "migration_plan": replay_request["packet"]["migration_plan"],
        }
        self.db.record_contract_review_response(
            replay_request["request_id"],
            replay_request["packet_hash"],
            replay_request["dispatch_id"],
            "synthetic-custodied-turn",
            json.dumps(replay_result, sort_keys=True, separators=(",", ":")).encode(),
        )
        self.db.close()
        self.db = Store(self.state / "organization.sqlite", clock=lambda: self.now)
        self.addCleanup(self.db.close)

        # Join the integrated cycle to the real deployment snapshot and fresh-path restore.
        # This synthetic state is stopped and idle; restore may only replay already
        # custodied contract evidence and record a passive lifecycle hold.
        (self.state / "STOP").touch()
        artifact_root = Path(tempfile.mkdtemp(prefix="brain-cycle-snapshot-"))
        self.addCleanup(shutil.rmtree, artifact_root, ignore_errors=True)
        snapshot_path = backup_snapshot(self.state, artifact_root / "snapshot")
        snapshot_manifest = json.loads((snapshot_path / "snapshot.json").read_text())
        restored = restore_snapshot(snapshot_path, artifact_root / "restored")
        restore_receipt = json.loads((restored / "restore-receipt.json").read_text())
        self.assertTrue((restored / "STOP").is_file())
        self.assertFalse(restore_receipt["runtime_ready"])
        self.assertTrue(restore_receipt["host_requalification_required"])
        with self.assertRaisesRegex(ValueError, "deployment_requalification_required"):
            validate_deployment(restored)
        validate_deployment(restored, allow_requalification=True)

        receipt_relative = f"receipts/{receipt_digest}.json"
        durable_paths = [
            "organization.sqlite",
            f"intents/{admitted}.json",
            receipt_relative,
            "integration/.git/HEAD",
        ]
        for relative in durable_paths:
            expected_hash = snapshot_manifest["files"][relative]
            self.assertEqual(
                hashlib.sha256((restored / relative).read_bytes()).hexdigest(),
                expected_hash,
                relative,
            )
        restored_integration_sha = run_git(
            restored / "integration", "rev-parse", GitGate.ACTIVE_REF
        )
        self.assertEqual(restored_integration_sha, candidate_sha)
        run_git(restored / "integration", "cat-file", "-e", f"{candidate_sha}^{{commit}}")
        restored_db = Store(restored / "organization.sqlite", clock=lambda: self.now)
        restored_lifecycle_lease = None
        restored_lifecycle_decision = None
        try:
            self.assertEqual(restored_db.get_task(admitted)["state"], "integrated")
            self.assertEqual(restored_db.get_contract("research-output")["version"], 1)
            restored_review = restored_db.get_contract_review_request(replay_request["request_id"])
            self.assertEqual(restored_review["body"]["status"], "response_recorded")
            self.assertIsNotNone(
                restored_db.get_contract_review_response(replay_request["request_id"])
            )
            self.assertEqual(restored_db.get_contract("research-output")["version"], 1)
            self.assertEqual(eligible(self.cfg, restored_db), [])
            self.assertTrue((restored / "STOP").is_file())
            self.assertEqual(supervisor.activate_contract(restored, restored_db), ())
            self.assertEqual(restored_db.get_contract("research-output")["version"], 1)
            self.assertEqual(
                restored_db.get_contract_review_request(replay_request["request_id"])["body"][
                    "status"
                ],
                "response_recorded",
            )
            # Recovery replays saved bytes through the passive Store API only.
            # Provider dispatch remains outside this STOPped copy.
            transition = restored_db.get_contract_transition("research-output", 2)
            replay = restored_db.replay_contract_review_responses_for_transition(
                "research-output", 2, transition["body"]["contract_hash"]
            )
            self.assertFalse(replay["provider_dispatch"])
            self.assertFalse(replay["dispatch_authority"])
            self.assertEqual(replay["replayed"][0]["request_id"], replay_request["request_id"])
            replayed_review = restored_db.get_contract_review_request(replay_request["request_id"])
            self.assertEqual(replayed_review["body"]["status"], "approved")
            response = restored_db.get_contract_review_response(replay_request["request_id"])
            ack = restored_db.get_decision(
                restored_db._contract_ack_key("research-output", 2, replay_request["team"])
            )
            self.assertEqual(ack["body"]["review_request_id"], replay_request["request_id"])
            self.assertEqual(ack["body"]["review_packet_hash"], replay_request["packet_hash"])
            self.assertEqual(ack["body"]["review_raw_sha256"], response["raw_sha256"])
            self.assertEqual(restored_db.get_contract("research-output")["version"], 1)
            self.assertEqual(eligible(self.cfg, restored_db), [])
            self.assertEqual(
                restored_db.get_contract_review_request(prepared_reviews[1]["body"]["request_id"])[
                    "body"
                ]["status"],
                "prepared",
            )
            self.assertIsNone(
                restored_db.db.execute("SELECT 1 FROM attempts WHERE state='running'").fetchone()
            )
            # Recovery on the restored copy records only a passive hold. This is
            # fixture STOP, not the historical Pilot STOP; unavailable runtime,
            # accounting and quota inputs remain conservative fixture values.
            restored_lifecycle_lease = restored_db.claim_lifecycle_owner(
                "synthetic-restored-controller", lease_seconds=30
            )
            self.assertIsNotNone(restored_lifecycle_lease)
            stale_gate_snapshot = restored_db.capture_passive_lifecycle_gate_snapshot(restored)
            (restored / "STOP").unlink()
            with self.assertRaisesRegex(StateError, "lifecycle_gate_snapshot_changed"):
                restored_db.record_passive_lifecycle_hold(
                    restored_lifecycle_lease, restored, stale_gate_snapshot
                )
            (restored / "STOP").touch()
            gate_snapshot = restored_db.capture_passive_lifecycle_gate_snapshot(restored)
            restored_lifecycle_decision = restored_db.record_passive_lifecycle_hold(
                restored_lifecycle_lease, restored, gate_snapshot
            )
            self.assertEqual(restored_lifecycle_decision["body"]["plan"]["action"], "hold")
            self.assertEqual(
                restored_lifecycle_decision["body"]["plan"]["reason"], "stop_reason_active"
            )
            self.assertFalse(
                restored_lifecycle_decision["body"]["observations"]["accounting_ready"]
            )
            self.assertFalse(restored_lifecycle_decision["body"]["observations"]["runtime_ready"])
            self.assertFalse(
                restored_lifecycle_decision["body"]["observations"]["launch_authorized"]
            )
            self.assertEqual(restored_lifecycle_decision["body"]["source_snapshot"], gate_snapshot)
            self.assertTrue((restored / "STOP").is_file())
            # Exercise the frozen restored controller CLI against this fully integrated
            # checkpoint, not only the standalone deployment fixture used by test_service.
            restored_cli = r"""
import sys
sys.path.insert(0, sys.argv[1])
import engineering.service as service
real_run = service.subprocess.run
def checked_run(arguments, *args, **kwargs):
    if type(arguments) is not list or not arguments or arguments[0] != "git":
        raise AssertionError("unexpected process launch")
    return real_run(arguments, *args, **kwargs)
service.subprocess.run = checked_run
class ForbidRuntimeImports:
    def find_spec(self, fullname, *_args):
        if fullname in {"engineering.supervisor", "engineering.metered_codex"}:
            raise AssertionError("restore reconcile imported runtime code")
sys.meta_path.insert(0, ForbidRuntimeImports())
sys.argv = ["engineering", "restore-reconcile", "--state", sys.argv[2]]
from engineering.__main__ import main
main()
"""
            cli = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    "-c",
                    restored_cli,
                    str(restored / "controller"),
                    str(restored),
                ],
                cwd=restored,
                env={"PATH": os.environ.get("PATH", ""), "LANG": "C"},
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
            )
            self.assertEqual(cli.returncode, 0, cli.stderr)
            cli_result = json.loads(cli.stdout)
            self.assertEqual(cli_result["status"], "already_held")
            self.assertEqual(cli_result["worklist"]["count"], 1)
            self.assertFalse(cli_result["worklist"]["dispatch_authority"])
            self.assertFalse(cli_result["worklist"]["consumption_authority"])
            restored_db.release_lifecycle_owner(restored_lifecycle_lease)
        finally:
            restored_db.close()
        # The passive recovery decision survives Store reopen, while a newer
        # owner epoch fences the lease that wrote it. Nothing consumes a request.
        restored_db = Store(restored / "organization.sqlite", clock=lambda: self.now)
        replacement_db = Store(restored / "organization.sqlite", clock=lambda: self.now + 30)
        try:
            persisted_lifecycle_decision = restored_db.get_decision(
                restored_lifecycle_decision["id"]
            )
            self.assertEqual(
                persisted_lifecycle_decision["body"], restored_lifecycle_decision["body"]
            )
            replacement_lease = replacement_db.claim_lifecycle_owner(
                "synthetic-recovery-reader", lease_seconds=30
            )
            self.assertEqual(replacement_lease["epoch"], restored_lifecycle_lease["epoch"] + 1)
            with self.assertRaisesRegex(StateError, "stale_lifecycle_owner"):
                restored_db.record_lifecycle_evaluation(
                    restored_lifecycle_lease,
                    {
                        "desired_state": "enabled",
                        "stop_reasons": [],
                        "runtime_ready": True,
                        "accounting_ready": True,
                        "quota_ready": True,
                        "process_state": "stopped",
                        "process_owner": None,
                        "lease_owner": None,
                        "active_attempt_id": None,
                        "recovery_open": False,
                        "backoff_until": None,
                        "now": self.now,
                        "launch_authorized": True,
                    },
                )
            projection = restored_db.lifecycle_request_worklist()
            self.assertEqual(projection["count"], 1)
            self.assertEqual(projection["items"][0]["action"], "hold")
            self.assertFalse(projection["dispatch_authority"])
            self.assertFalse(projection["consumption_authority"])
            self.assertTrue((restored / "STOP").is_file())
        finally:
            replacement_db.close()
            restored_db.close()
        restored_ledger = UsageLedger(restored / "usage-ledger.sqlite")
        try:
            self.assertEqual(
                tuple(
                    restored_ledger.db.execute(
                        "SELECT identity,epoch,evidence FROM registrations"
                    ).fetchone()
                ),
                ("synthetic-checkpoint", "synthetic-epoch", "local synthetic snapshot only"),
            )
        finally:
            restored_ledger.close()
        # Continue only the original disposable test state; the restored copy stays STOPped.
        (self.state / "STOP").unlink()

        class SyntheticContractReviewTransport:
            def __init__(self):
                self.started_teams = []
                self.reviewed_teams = []

            def start(self, request):
                body = request["body"]
                self_test.assertEqual(self_test.db.get_contract("research-output")["version"], 1)
                self_test.assertEqual(eligible(self_test.cfg, self_test.db), [])
                self_test.assertEqual(body["name"], "research-output")
                self_test.assertEqual(body["version"], 2)
                self_test.assertIn(body["team"], ("Experience", "Platform"))
                self_test.assertEqual(body["packet"]["contract_hash"], contract_hash)
                self_test.assertEqual(body["packet"]["source_commit"], candidate_sha)
                self_test.assertEqual(body["packet"]["evidence_refs"], [review_evidence])
                if body["team"] == "Platform":
                    self_test.assertEqual(body["packet"]["participant_tasks"], [])
                self.started_teams.append(body["team"])
                return {
                    "thread_id": "synthetic-contract-review-" + body["team"],
                    "model": "synthetic-reviewer",
                    "pid": 123,
                    "birth": "synthetic",
                }

            def review(self, _session, request):
                body = request["body"]
                self.reviewed_teams.append(body["team"])
                result = {
                    "decision": "approve",
                    "compatibility": "compatible",
                    "rationale": "Synthetic evidence review passed.",
                    "findings": [],
                    "reviewed_evidence_refs": body["packet"]["evidence_refs"],
                    "affected_consumers_reviewed": body["packet"]["affected_consumer_task_ids"],
                    "migration_plan": body["packet"]["migration_plan"],
                }
                return {
                    "provider_turn_id": "synthetic-turn-" + body["team"],
                    "raw_result": json.dumps(
                        result, sort_keys=True, separators=(",", ":")
                    ).encode(),
                }

        review_transport = SyntheticContractReviewTransport()
        dependent_worker_code = (
            "from pathlib import Path\n"
            "if Path('src/capability.py').read_text() != 'VALUE = 7\\n':\n"
            "    raise SystemExit('integrated producer output is missing')\n"
            "Path('src/consumer.py').write_text("
            "'from capability import VALUE\\nCONSUMED = VALUE + 1\\n')\n"
        )
        dependent_transport_roles = []
        stop_injected_after_claim = []
        popen_failures = []

        class SyntheticDependentCodex:
            def __init__(self, role):
                self.role = role
                self.proc = SimpleNamespace(pid=789)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def start(self, *_args):
                return "synthetic-dependent-" + self.role

            def turn(self, *_args, **kwargs):
                if self.role == "worker":
                    result = kwargs["tool"](
                        {
                            "argv": [python_executable, "-I", "-c", dependent_worker_code],
                            "timeout": 10,
                        }
                    )
                    if result["returncode"] != 0:
                        raise AssertionError(result["stderr"])
                    text = json.dumps(
                        {
                            "summary": "Consumed the integrated producer capability.",
                            "tests": ["synthetic producer dependency and sandbox"],
                            "limitations": ["provider and reviewer remain synthetic"],
                        }
                    )
                elif self.role == "lead":
                    text = json.dumps(
                        {
                            "decision": "approve",
                            "worker_brief": "Consume the active research-output contract.",
                            "rationale": "The exact contract version is active.",
                            "contract_acks": [],
                        }
                    )
                else:
                    packet = json.loads(_args[1].rsplit("\n", 1)[-1])
                    self_test.assertEqual(packet["task"]["id"], "PLAT-002")
                    self_test.assertEqual(
                        packet["candidate"]["submitted_changed_paths"], ["src/consumer.py"]
                    )
                    self_test.assertIn("CONSUMED = VALUE + 1", packet["diff"])
                    acceptance = json.loads(packet["checks"][0]["stdout"])
                    self_test.assertEqual(acceptance["case"], "PLAT-002")
                    self_test.assertIs(acceptance["passed"], True)
                    text = json.dumps(
                        {
                            "approved": True,
                            "findings": [],
                            "rationale": "Synthetic exact-candidate consumer review passed.",
                        }
                    )
                return {
                    "text": text,
                    "usage_terminal": True,
                    "usage_delta": {"totalTokens": 3},
                }

            def record_outcome(self, *_args, **_kwargs):
                return None

        def synthetic_dependent_client(*_args, **kwargs):
            role = kwargs["context"]["role"]
            dependent_transport_roles.append(role)
            return SyntheticDependentCodex(role)

        spawned = []
        real_popen = subprocess.Popen

        def select_synthetic_capacity(*_args, ready_tasks=(), **_kwargs):
            selected = list(ready_tasks)
            if len(selected) == 1 and selected[0]["id"] == "PLAT-002":
                # Model multiple available slots so a STOP break is required after
                # canceling the first selected claim.
                selected.append(dict(selected[0]))
            return {"slots": 2, "selected_tasks": selected, "reason": "synthetic"}

        def capture_worker(args, **kwargs):
            if "engineering" in args and "attempt" in args:
                attempt_id = int(args[args.index("--attempt") + 1])
                if not popen_failures:
                    popen_failures.append(attempt_id)
                    raise OSError("synthetic exec failure")
                spawned.append((args, kwargs))
                attempt_state = Path(args[args.index("--state") + 1])
                supervisor.run_attempt(attempt_state, attempt_id)
                return SimpleNamespace(pid=456)
            return real_popen(args, **kwargs)

        real_store_claim = Store.claim

        def claim_with_interruption(store, task_id, worker, lease_seconds=300):
            attempt = real_store_claim(store, task_id, worker, lease_seconds)
            if attempt and task_id == "PLAT-002" and not stop_injected_after_claim:
                (self.state / "STOP").touch()
                stop_injected_after_claim.append(attempt["id"])
            return attempt

        verification_calls = []
        supervisor_events = []
        real_verify_one = supervisor.verify_one

        def capture_supervisor_event(state, kind, **values):
            supervisor_events.append((kind, values))

        def capture_verification(state, task):
            result = real_verify_one(state, task)
            verification_calls.append((task["id"], result))
            return result

        def synthetic_planner_client(*_args, **_kwargs):
            fake = MagicMock()
            fake.__enter__.return_value = fake
            fake.start.return_value = "synthetic-planner-thread"

            def planner_turn(_thread, prompt, **kwargs):
                dossier = json.loads(prompt.rsplit("\n", 1)[-1])
                self.assertEqual(list(dossier["available_cards"]), ["PLAT-002"])
                self.assertEqual(kwargs["dispatch_id"].split(":", 1)[0], "planning-proposal")
                return {
                    "text": json.dumps(
                        {
                            "card": "PLAT-002",
                            "rationale": "The exact contract is active after both reviews.",
                            "evidence": f"{admitted} is integrated at {candidate_sha}.",
                        }
                    )
                }

            fake.turn.side_effect = planner_turn
            return fake

        with (
            patch.object(supervisor, "Codex", FakeCodex),
            patch.object(FakeCodex, "call", return_value={"ordinaryUsageAllowed": True}),
            patch.object(supervisor, "process_identity", return_value="synthetic-process"),
            patch.object(supervisor, "available_memory", return_value=10**10),
            patch.object(supervisor, "status", return_value={}),
            patch.object(supervisor, "render_desk"),
            patch.object(supervisor, "verify_one", side_effect=capture_verification),
            patch.object(supervisor, "log_event", side_effect=capture_supervisor_event),
            patch.object(Store, "claim", autospec=True, side_effect=claim_with_interruption),
            patch.object(
                supervisor.time,
                "sleep",
                side_effect=lambda seconds: setattr(self, "now", self.now + seconds),
            ),
            patch.object(
                supervisor,
                "metered_client",
                side_effect=synthetic_dependent_client,
            ),
            patch.object(planning, "config", return_value=self.cfg),
            patch.object(
                planning,
                "metered_client",
                side_effect=synthetic_planner_client,
            ),
            patch.object(planning, "git", side_effect=run_git),
            patch.object(planning, "log_event"),
            patch("engineering.resources.choose_capacity", side_effect=select_synthetic_capacity),
            patch.object(supervisor.subprocess, "Popen", side_effect=capture_worker),
        ):
            supervisor.supervise(
                self.state, max_seconds=10, contract_review_transport=review_transport
            )
            self.assertTrue((self.state / "STOP").is_file())
            cancelled_attempt = self.db.list_attempts("PLAT-002")
            self.assertEqual(len(cancelled_attempt), 1)
            self.assertEqual(cancelled_attempt[0]["state"], "cancelled_before_spawn")
            self.assertEqual(self.db.get_task("PLAT-002")["state"], "pending")
            self.assertEqual(spawned, [])
            (self.state / "STOP").unlink()
            supervisor.supervise(
                self.state, max_seconds=10, contract_review_transport=review_transport
            )
        self.assertEqual(len(review_transport.started_teams), 1)
        self.assertEqual(review_transport.reviewed_teams, review_transport.started_teams)
        self.assertNotIn(replay_request["team"], review_transport.started_teams)
        for request in prepared_reviews:
            body = request["body"]
            ack = self.db.get_decision(
                self.db._contract_ack_key("research-output", 2, body["team"])
            )
            response = self.db.get_contract_review_response(body["request_id"])
            self.assertEqual(ack["body"]["review_request_id"], body["request_id"])
            self.assertEqual(ack["body"]["review_packet_hash"], body["packet_hash"])
            self.assertEqual(ack["body"]["review_raw_sha256"], response["raw_sha256"])
        self.assertEqual(self.db.get_contract("research-output")["version"], 2)
        self.assertEqual(verification_calls, [("PLAT-002", True)], supervisor_events)
        self.assertEqual(eligible(self.cfg, self.db), [], self.db.events())
        self.assertEqual(self.db.get_task("PLAT-002")["base_sha"], candidate_sha)
        self.assertEqual(
            self.db.get_task("PLAT-002")["state"], "integrated", self.db.events()[-12:]
        )
        self.assertEqual(dependent_transport_roles, ["lead", "worker", "verifier"])
        self.assertEqual(self.db.get_task("PLAT-002")["dependencies"], ["PLAT-001"])
        dependent_attempts = self.db.list_attempts("PLAT-002")
        self.assertEqual(len(dependent_attempts), 3)
        self.assertEqual(dependent_attempts[0]["state"], "cancelled_before_spawn")
        self.assertEqual(dependent_attempts[1]["state"], "cancelled_before_spawn")
        self.assertEqual(popen_failures, [dependent_attempts[1]["id"]])
        self.assertEqual(len(spawned), 1)
        self.assertIn("--attempt", spawned[0][0])

        # The actual supervisor loop dispatched, executed, independently verified, and
        # integrated the dependent task without a manual run_attempt/verify_one call.
        dependent_task = self.db.get_task("PLAT-002")
        self.assertEqual(dependent_task["state"], "integrated", self.db.events()[-1])
        dependent_intent = supervisor.read(self.state / "intents/PLAT-002.json")
        dependent_receipt_raw = (
            self.state / "receipts" / f"{dependent_intent['receipt']}.json"
        ).read_bytes()
        dependent_receipt = json.loads(dependent_receipt_raw)
        canonical_receipt = json.dumps(
            dependent_receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        self.assertEqual(hashlib.sha256(canonical_receipt).hexdigest(), dependent_intent["receipt"])
        self.assertEqual(dependent_receipt_raw, canonical_receipt)
        self.assertEqual(dependent_receipt["candidate_sha"], dependent_intent["candidate_sha"])
        self.assertEqual(dependent_receipt["result"]["status"], "passed")
        dependent_parents = run_git(
            integration, "show", "-s", "--format=%P", dependent_intent["candidate_sha"]
        ).split()
        self.assertEqual(dependent_parents, [candidate_sha, dependent_task["candidate_sha"]])
        self.assertEqual(dependent_intent["expected_tip"], candidate_sha)
        self.assertEqual(run_git(integration, "rev-parse", "main"), base)
        self.assertEqual(
            run_git(integration, "rev-parse", GitGate.ACTIVE_REF), dependent_intent["candidate_sha"]
        )
        all_integration_events = [
            event for event in self.db.events() if event["kind"] == "candidate_integrated"
        ]
        self.assertEqual(
            [event["entity"] for event in all_integration_events], ["PLAT-001", "PLAT-002"]
        )

        # Admit one useful off-catalog task against the fully integrated current tip. Its
        # trusted execution binding must reach the verifier gate; the unavailable executable
        # qualification must block before checkout or worker dispatch.
        off_catalog_id = "PLAT-003"
        acceptance_id = "next-source-check"
        acceptance_sha = "4" * 64
        proposal_sha = contract_hash
        scope = ["src/next_capability.py"]
        contract_requirement = {"research-output": {"version": 2, "proposal_sha256": proposal_sha}}
        off_catalog_spec = {
            "schema": "brain-task-spec-v1",
            "id": off_catalog_id,
            "objective_id": "offline-brain-engineering",
            "team": "Platform",
            "title": "Extend the integrated research capability",
            "brief": (
                "Add one bounded source capability that consumes the current integrated contract."
            ),
            "base_sha": dependent_intent["candidate_sha"],
            "scope": scope,
            "dependencies": ["PLAT-002"],
            "contracts": contract_requirement,
            "risk": "low",
            "capabilities": ["offline_code", "synthetic_tests"],
            "resources": {"max_attempts": 1, "max_turn_seconds": 600, "estimated_wpu": 1},
            "acceptance": [{"id": acceptance_id, "sha256": acceptance_sha}],
            "evidence_refs": [f"integrated-task:{off_catalog_id}:synthetic"],
        }
        envelope = {
            "schema": "brain-task-envelope-v1",
            "objective_id": "offline-brain-engineering",
            "teams": ["Platform"],
            "allowed_paths": scope,
            "capabilities": ["offline_code", "synthetic_tests"],
            "max_attempts": 1,
            "max_turn_seconds": 600,
            "allowed_contracts": {"research-output": {"2": proposal_sha}},
            "base_sha": dependent_intent["candidate_sha"],
            "risk_levels": ["low"],
        }
        acceptance_registry = {
            acceptance_id: {
                "sha256": acceptance_sha,
                "objective_id": "offline-brain-engineering",
                "scope": scope,
                "contracts": contract_requirement,
                "independent": True,
                "frozen": True,
                "risk": "low",
            }
        }
        verifier_registry = {
            acceptance_id: {
                "acceptance_sha256": acceptance_sha,
                "policy_id": "next-source-check-v1",
                "commands": [
                    ["{interpreter}", "-I", "-S", "-B", "{harness}", "{task_id}", "{checkout}"]
                ],
                "env": {"LANG": "C"},
                "harness_sha256": "5" * 64,
                "result_schema": "org-acceptance-v1",
                "case_field": "case",
                "passed_field": "passed",
            }
        }
        release_sha = "6" * 64
        integration_policy_sha = "7" * 64
        self.cfg["controller_release_sha256"] = release_sha
        self.cfg["integration_policy_sha256"] = integration_policy_sha
        admitted_off_catalog = self.db.admit_task_spec(
            off_catalog_spec,
            envelope=envelope,
            acceptance_registry=acceptance_registry,
            policy_sha256="8" * 64,
            admission={
                "rationale": "The integrated dependency exposes the next useful source boundary.",
                "evidence": f"PLAT-002 is integrated at {dependent_intent['candidate_sha']}.",
            },
            verifier_registry=verifier_registry,
            controller_release_sha256=release_sha,
            integration_policy_sha256=integration_policy_sha,
        )
        self.assertEqual(admitted_off_catalog, off_catalog_id)
        self.assertNotIn(off_catalog_id, self.cfg["catalogue"])
        self.assertEqual([task["id"] for task in self.db.ready_tasks()], [off_catalog_id])
        off_catalog_task = self.db.get_task(off_catalog_id)
        bound_plan = prepare_verification_plan(self.state, self.db, off_catalog_task, self.cfg)
        self.assertFalse(bound_plan.execution_qualified)
        self.assertEqual(bound_plan.reason, "verifier_manifest_not_qualified")
        self.assertEqual(bound_plan.decision_name, f"admission/task-spec/{off_catalog_id}")
        self.assertEqual(bound_plan.acceptance_identities, (f"{acceptance_id}:{acceptance_sha}",))
        self.assertEqual(
            bound_plan.policy.commands,
            tuple(tuple(command) for command in verifier_registry[acceptance_id]["commands"]),
        )

        activation_name = self.db._contract_transition_key("research-output", 2) + "/activated"
        activation_row = self.db.db.execute(
            "SELECT version,body FROM decisions WHERE name=?", (activation_name,)
        ).fetchone()
        self.assertIsNotNone(activation_row)
        original_activation_body = activation_row["body"]
        activation_event = self.db.db.execute(
            "SELECT seq,body FROM events WHERE kind='decision_recorded' AND entity=?",
            (activation_name,),
        ).fetchone()
        self.assertIsNotNone(activation_event)
        original_activation_event = activation_event["body"]
        forged_activation = json.loads(original_activation_body)
        forged_activation["from_version"] = True
        forged_activation_raw = json.dumps(forged_activation, sort_keys=True, separators=(",", ":"))
        self.db.db.execute(
            "UPDATE decisions SET body=? WHERE name=? AND version=?",
            (
                forged_activation_raw,
                activation_name,
                activation_row["version"],
            ),
        )
        self.db.db.execute(
            "UPDATE events SET body=? WHERE seq=?",
            (
                json.dumps(
                    {
                        "version": 1,
                        "body": json.loads(forged_activation_raw),
                        "replaces": None,
                        "replaces_version": None,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                activation_event["seq"],
            ),
        )
        self.assertEqual(self.db.ready_tasks(), [])
        with self.assertRaisesRegex(ValueError, "task_spec_contract_activation_missing"):
            prepare_verification_plan(self.state, self.db, off_catalog_task, self.cfg)
        self.db.db.execute(
            "UPDATE decisions SET body=? WHERE name=? AND version=?",
            (original_activation_body, activation_name, activation_row["version"]),
        )
        self.db.db.execute(
            "UPDATE events SET body=? WHERE seq=?",
            (original_activation_event, activation_event["seq"]),
        )
        self.assertEqual([task["id"] for task in self.db.ready_tasks()], [off_catalog_id])

        # A forged config catalogue entry cannot replace this task's saved execution binding.
        self.cfg["tasks"][off_catalog_id] = {
            "checks": [["must-not-run"]],
            "scope": ["src/forbidden.py"],
        }
        self.assertEqual(
            prepare_verification_plan(self.state, self.db, off_catalog_task, self.cfg).identity(),
            bound_plan.identity(),
        )
        self.cfg["integration_policy_sha256"] = "9" * 64
        with self.assertRaisesRegex(ValueError, "bound_verifier_integration_policy_mismatch"):
            prepare_verification_plan(self.state, self.db, off_catalog_task, self.cfg)
        self.cfg["integration_policy_sha256"] = integration_policy_sha
        self.cfg["verifier_manifest_sha256"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "verifier_manifest"):
            prepare_verification_plan(self.state, self.db, off_catalog_task, self.cfg)
        self.cfg.pop("verifier_manifest_sha256")

        off_catalog_attempt = self.db.claim(off_catalog_id, "synthetic-off-catalog-owner")
        self.assertIsNotNone(off_catalog_attempt)
        attempt_root = self.state / "attempts" / str(off_catalog_attempt["id"])
        with (
            patch.object(supervisor, "metered_client", side_effect=AssertionError("dispatch")),
            patch.object(supervisor.subprocess, "Popen", side_effect=AssertionError("spawn")),
        ):
            supervisor.run_attempt(self.state, off_catalog_attempt["id"])
        self.assertEqual(self.db.get_task(off_catalog_id)["state"], "failed")
        self.assertEqual(self.db.events()[-1]["kind"], "attempt_failed")
        self.assertEqual(
            self.db.events()[-1]["body"]["reason"],
            "verifier_plan_unqualified:verifier_manifest_not_qualified",
        )
        self.assertFalse(attempt_root.exists())

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

        def git_raw_output(_repo, *args):
            return raw_path_output if "--name-only" in args else diff

        def git_raw_limited_output(_repo, max_bytes, *args, **_kwargs):
            output = raw_path_output if "--name-only" in args else diff
            encoded = output.encode("utf-8")
            if len(encoded) > max_bytes:
                raise RuntimeError("git_output_exceeds_limit")
            return output

        with (
            patch.object(supervisor, "GitGate", return_value=gate),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "SandboxRunner", return_value=box),
            patch.object(
                supervisor, "metered_client", side_effect=lambda *args, **kwargs: reviewer
            ) as model,
            patch.object(supervisor, "git", return_value="normalized output"),
            patch.object(supervisor, "git_raw", side_effect=git_raw_output),
            patch.object(supervisor, "git_raw_limited", side_effect=git_raw_limited_output),
        ):
            supervisor.verify_one(self.state, self.db.get_task("T"))
        self.assertEqual(self.db.get_task("T")["state"], "pending")
        gate.publish.assert_not_called()
        self.assertFalse(any(event["kind"] == "candidate_accepted" for event in self.db.events()))
        return model

    def verification_artifacts(self, candidate_sha="merged"):
        runs = sorted((self.state / "verification" / candidate_sha).glob("run-*"))
        self.assertTrue(runs)
        return runs[-1]

    def test_concurrent_same_candidate_verifications_keep_run_artifacts_bound(self):
        self.candidate(task_id="T", target_state="submitted")
        self.candidate(task_id="T2", sha="submitted-2", target_state="submitted")
        self.cfg["tasks"]["T2"] = self.cfg["tasks"]["T"]
        candidates = {
            "submitted": SimpleNamespace(
                submitted_sha="submitted",
                base_sha="base",
                integration_tip="base",
                sha="merged",
                changed_paths=("src/example.py",),
                checkout=self.state / "candidate-A",
            ),
            "submitted-2": SimpleNamespace(
                submitted_sha="submitted-2",
                base_sha="base",
                integration_tip="base",
                sha="merged",
                changed_paths=("src/example.py",),
                checkout=self.state / "candidate-B",
            ),
        }
        policy = VerificationPolicy(((sys.executable, "fixed-acceptance"),))
        gate = MagicMock()
        gate.submit.side_effect = lambda _checkout, submitted, _base, _scope: candidates[submitted]
        results = {}

        def verify(candidate, callback):
            task_id = "T" if candidate.submitted_sha == "submitted" else "T2"
            results[task_id] = callback(candidate.checkout, candidate.sha, policy)
            return SimpleNamespace(digest="receipt-" + task_id)

        gate.verify.side_effect = verify
        reviewer_a_entered = threading.Event()
        release_reviewer_a = threading.Event()
        reviewer_text_a = json.dumps({"approved": True, "findings": [], "rationale": "ok"})

        class Reviewer:
            def __init__(self, task_id):
                self.task_id = task_id

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def start(self, *_args):
                return "thread-" + self.task_id

            def turn(self, *_args, **_kwargs):
                if self.task_id == "T":
                    reviewer_a_entered.set()
                    if not release_reviewer_a.wait(timeout=5):
                        raise TimeoutError("reviewer A test barrier timed out")
                    text = reviewer_text_a
                else:
                    text = "{malformed reviewer B response"
                return {
                    "text": text,
                    "usage_terminal": True,
                    "usage_delta": {"totalTokens": 5},
                }

            def record_outcome(self, *_args, **_kwargs):
                return None

        boxes = {}
        for task_id in ("T", "T2"):
            stdout = json.dumps(
                {"schema": "org-pilot-acceptance-v1", "case": task_id, "passed": True}
            )
            box = MagicMock()
            box.run.return_value = SandboxResult(
                ("fixed-acceptance",), 0, stdout, "", False, "profile", 0.01
            )
            boxes[task_id] = box

        def sandbox_runner(**_kwargs):
            task_id = "T" if threading.current_thread().name == "verification-A" else "T2"
            return boxes[task_id]

        def git_raw_output(_repo, *args):
            return "src/example.py\0" if "--name-only" in args else "synthetic diff"

        def git_raw_limited_output(_repo, _max_bytes, *args, **_kwargs):
            return "src/example.py\0" if "--name-only" in args else "synthetic diff"

        failures = {}

        def verify_task(task_id):
            try:
                supervisor.verify_one(self.state, self.db.get_task(task_id))
            except BaseException as exc:
                failures[task_id] = exc

        with (
            patch.object(supervisor, "GitGate", return_value=gate),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "SandboxRunner", side_effect=sandbox_runner),
            patch.object(
                supervisor,
                "metered_client",
                side_effect=lambda *args, **kwargs: Reviewer(kwargs["context"]["task"]),
            ),
            patch.object(supervisor, "git", return_value="normalized output"),
            patch.object(supervisor, "git_raw", side_effect=git_raw_output),
            patch.object(supervisor, "git_raw_limited", side_effect=git_raw_limited_output),
            patch.object(supervisor, "activate_contract"),
        ):
            first = threading.Thread(
                target=verify_task, args=("T",), name="verification-A", daemon=True
            )
            first.start()
            try:
                self.assertTrue(
                    reviewer_a_entered.wait(timeout=5), repr((failures, self.db.events()))
                )
                second = threading.Thread(
                    target=verify_task, args=("T2",), name="verification-B", daemon=True
                )
                second.start()
                second.join(timeout=5)
                self.assertFalse(second.is_alive())
            finally:
                release_reviewer_a.set()
                first.join(timeout=5)
            self.assertFalse(first.is_alive())

        self.assertEqual(failures, {})
        runs = list((self.state / "verification" / "merged").glob("run-*"))
        self.assertEqual(len(runs), 2)
        run_a = self.state / "verification" / results["T"]["verification_run"]
        run_b = next(run for run in runs if run != run_a)
        self.assertNotEqual(run_a, run_b)
        self.assertEqual(
            results["T"]["commands_sha256"],
            hashlib.sha256((run_a / "commands.json").read_bytes()).hexdigest(),
        )
        self.assertEqual(
            results["T"]["review_sha256"],
            hashlib.sha256((run_a / "review.json").read_bytes()).hexdigest(),
        )
        self.assertEqual(
            json.loads((run_a / "commands.json").read_text())[0]["stdout"],
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "T", "passed": True}),
        )
        self.assertEqual(
            json.loads((run_b / "commands.json").read_text())[0]["stdout"],
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "T2", "passed": True}),
        )
        self.assertEqual(
            json.loads((run_a / "review-raw.json").read_text())["text"], reviewer_text_a
        )
        self.assertEqual(
            json.loads((run_b / "review-raw.json").read_text())["text"],
            "{malformed reviewer B response",
        )
        self.assertTrue((run_a / "review.json").is_file())
        self.assertTrue((run_b / "review-raw.json").is_file())
        self.assertFalse((run_b / "review.json").exists())
        self.assertEqual(self.db.get_task("T")["state"], "integrated")
        self.assertEqual(self.db.get_task("T2")["state"], "pending")

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

    def test_legacy_acceptance_exponent_overflow_is_rejected(self):
        model = self.verification_failure(
            '{"schema":"org-pilot-acceptance-v1","case":"T","passed":true,'
            '"metrics":{"positive":1e9999,"negative":-1e9999,"values":[1e9999,-1e9999]}}'
        )
        model.assert_not_called()

    def test_legacy_acceptance_rejects_negative_and_array_exponent_overflow(self):
        contract = {
            "acceptance_id": "T",
            "result_schema": "org-pilot-acceptance-v1",
            "case_field": "case",
            "passed_field": "passed",
        }
        for raw in (
            '{"schema":"org-pilot-acceptance-v1","case":"T","passed":true,'
            '"metrics":{"negative":-1e9999}}',
            '{"schema":"org-pilot-acceptance-v1","case":"T","passed":true,"metrics":[1e9999]}',
            '{"schema":"org-pilot-acceptance-v1","case":"T","passed":true,"metrics":[-1e9999]}',
        ):
            with (
                self.subTest(raw=raw),
                self.assertRaisesRegex(ValueError, "verifier_result_nonstandard_number"),
            ):
                supervisor.validate_verifier_result(raw, contract)

    def test_legacy_acceptance_finite_exponent_remains_accepted(self):
        from engineering.verifier_result import validate_verifier_result

        value = validate_verifier_result(
            '{"schema":"org-pilot-acceptance-v1","case":"T","passed":true,'
            '"metrics":{"value":1e300}}',
            {
                "acceptance_id": "T",
                "result_schema": "org-pilot-acceptance-v1",
                "case_field": "case",
                "passed_field": "passed",
            },
        )
        self.assertEqual(value["metrics"]["value"], 1e300)

    def test_malformed_independent_reviewer_json_cannot_accept_candidate(self):
        model = self.verification_failure(
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "T", "passed": True})
        )
        model.assert_called_once()
        packet = json.loads((self.verification_artifacts() / "review-packet.json").read_text())
        self.assertEqual(packet["diff"], "synthetic diff")
        self.assertEqual(packet["candidate"]["submitted_sha"], "submitted")
        self.assertEqual(packet["candidate"]["merged_candidate_sha"], "merged")
        self.assertEqual(packet["candidate"]["submitted_changed_paths"], ["src/example.py"])
        self.assertEqual(packet["candidate"]["merged_changed_paths"], ["src/example.py"])
        self.assertEqual(packet["task"]["brief"], "")

    def test_independent_review_decision_requires_exact_local_schema(self):
        valid_approval = json.dumps(
            {"approved": True, "findings": [], "rationale": "Checks passed."}
        )
        valid_rejection = json.dumps(
            {"approved": False, "findings": ["defect"], "rationale": "Found a defect."}
        )
        self.assertTrue(supervisor.parse_independent_review_decision(valid_approval)["approved"])
        self.assertFalse(supervisor.parse_independent_review_decision(valid_rejection)["approved"])
        self.assertTrue(
            supervisor.parse_independent_review_decision("```json\n" + valid_approval + "\n```")[
                "approved"
            ]
        )

        invalid = (
            '{"approved":false,"approved":true,"findings":[],"rationale":"conflict"}',
            '{"approved":true,"approved":false,"findings":[],"rationale":"conflict"}',
            '{"approved":true,"findings":[]}',
            '{"approved":true,"findings":[],"rationale":"ok","extra":1}',
            '{"approved":1,"findings":[],"rationale":"ok"}',
            '{"approved":true,"findings":[1],"rationale":"ok"}',
            '{"approved":true,"findings":[],"rationale":1}',
            '{"approved":true,"findings":[],"rationale":"ok","extra":NaN}',
            '{"approved":true,"findings":[],"rationale":"ok","extra":Infinity}',
            '{"approved":true,"findings":[],"rationale":"ok","extra":1e9999}',
            '```json\n{"approved":true,"findings":[],"rationale":"ok"}',
            '```json\n{"approved":true,"findings":[],"rationale":"ok"}\n```\ntrailing',
        )
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                supervisor.parse_independent_review_decision(raw)

    def test_ambiguous_independent_review_approval_fails_in_callback(self):
        model = self.verification_failure(
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "T", "passed": True}),
            reviewer_text=(
                '{"approved":false,"approved":true,"findings":[],'
                '"rationale":"conflicting decisions"}'
            ),
        )
        model.assert_called_once()
        events = self.db.events()
        verification_event = [event for event in events if event["kind"] == "candidate_verified"][
            -1
        ]
        self.assertFalse(verification_event["body"]["passed"])
        self.assertIn(
            "review_decision_duplicate_key",
            verification_event["body"]["details"]["error"],
        )
        artifacts = self.verification_artifacts()
        self.assertTrue((artifacts / "review-raw.json").is_file())
        self.assertFalse((artifacts / "review.json").exists())

    def test_unclosed_review_fence_cannot_hide_conflicting_decision_in_callback(self):
        model = self.verification_failure(
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "T", "passed": True}),
            reviewer_text=(
                '```json\n{"approved":true,"findings":[],"rationale":"ok"}\n'
                '{"approved":false,"findings":["defect"],"rationale":"reject"}'
            ),
        )
        model.assert_called_once()
        verification_event = [
            event for event in self.db.events() if event["kind"] == "candidate_verified"
        ][-1]
        self.assertFalse(verification_event["body"]["passed"])
        self.assertIn(
            "review_decision_invalid_fence",
            verification_event["body"]["details"]["error"],
        )
        artifacts = self.verification_artifacts()
        self.assertTrue((artifacts / "review-raw.json").is_file())
        self.assertFalse((artifacts / "review.json").exists())

    def test_oversized_diff_blocks_before_independent_review_call(self):
        model = self.verification_failure(
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "T", "passed": True}),
            diff="x" * 50001,
        )
        model.assert_not_called()
        self.assertFalse((self.verification_artifacts() / "review-packet.json").exists())

    def test_oversized_changed_path_inventory_blocks_before_review_dispatch(self):
        paths = []
        path_output = ""
        while len(path_output.encode("utf-8")) <= supervisor.MAX_PACKET_BYTES:
            paths.append(f"src/path-{len(paths):06d}-" + "x" * 120)
            path_output = "\0".join(paths) + "\0"
        model = self.verification_failure(
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "T", "passed": True}),
            raw_path_output=path_output,
        )
        model.assert_not_called()
        self.assertFalse((self.verification_artifacts() / "review-packet.json").exists())
        verification_event = [
            event for event in self.db.events() if event["kind"] == "candidate_verified"
        ][-1]
        self.assertIn(
            "git_output_exceeds_limit",
            verification_event["body"]["details"]["error"],
        )

    def test_review_packet_preserves_git_diff_crlf_and_trailing_whitespace(self):
        diff = "diff --git a/src/example.py b/src/example.py\r\n+value = 1   \r\n"
        self.verification_failure(
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "T", "passed": True}),
            diff=diff,
        )
        packet = json.loads((self.verification_artifacts() / "review-packet.json").read_text())
        self.assertEqual(packet["diff"], diff)
        self.assertEqual(packet["diff_sha256"], hashlib.sha256(diff.encode()).hexdigest())

    def test_leading_whitespace_in_git_path_is_not_trimmed_into_scope(self):
        model = self.verification_failure(
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "T", "passed": True}),
            raw_path_output=" src/example.py\0",
        )
        model.assert_not_called()
        self.assertFalse((self.verification_artifacts() / "review-packet.json").exists())

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

    def test_git_raw_rejects_invalid_utf8(self):
        process = SimpleNamespace(returncode=0, stdout=b"diff \xff", stderr=b"")
        with patch.object(supervisor.subprocess, "run", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "git_path_not_utf8"):
                supervisor.git_raw(self.state, "diff")

    def test_git_raw_limited_accepts_exact_limit_and_rejects_limit_plus_one(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "-C", directory, "init", "-q"], check=True)
            payload = b"x" * 64
            written = subprocess.run(
                ["git", "-C", directory, "hash-object", "-w", "--stdin"],
                input=payload,
                capture_output=True,
                check=True,
            )
            object_id = written.stdout.decode("ascii").strip()
            self.assertEqual(
                supervisor.git_raw_limited(directory, len(payload), "cat-file", "blob", object_id),
                payload.decode("ascii"),
            )
            with self.assertRaisesRegex(RuntimeError, "git_output_exceeds_limit"):
                supervisor.git_raw_limited(
                    directory, len(payload) - 1, "cat-file", "blob", object_id
                )

    def test_git_raw_limited_rejects_invalid_timeout_before_spawn(self):
        for timeout in (True, 0, float("inf"), 301):
            with (
                self.subTest(timeout=timeout),
                patch.object(supervisor.subprocess, "Popen") as popen,
            ):
                with self.assertRaisesRegex(ValueError, "timeout"):
                    supervisor.git_raw_limited(self.state, 10, "diff", timeout=timeout)
                popen.assert_not_called()

    def test_git_raw_limited_selector_allocation_failure_precedes_spawn(self):
        with (
            patch.object(
                supervisor.selectors,
                "DefaultSelector",
                side_effect=OSError("selector allocation failed"),
            ),
            patch.object(supervisor.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(OSError, "selector allocation failed"):
                supervisor.git_raw_limited(self.state, 10, "diff")
        popen.assert_not_called()

    def test_git_raw_limited_cleans_process_and_pipes_after_selector_read_error(self):
        selector = MagicMock()
        selector.get_map.return_value = {1: object()}
        selector.select.side_effect = OSError("synthetic read failure")
        process = MagicMock()
        process.pid = 321
        process.stdout.fileno.return_value = 11
        process.stderr.fileno.return_value = 12

        with (
            patch.object(supervisor.selectors, "DefaultSelector", return_value=selector),
            patch.object(supervisor.subprocess, "Popen", return_value=process) as popen,
            patch.object(supervisor.os, "set_blocking"),
            patch.object(supervisor.os, "killpg") as killpg,
        ):
            with self.assertRaisesRegex(OSError, "synthetic read failure"):
                supervisor.git_raw_limited(self.state, 10, "diff")

        popen.assert_called_once()
        selector.close.assert_called_once()
        killpg.assert_called_once_with(321, supervisor.signal.SIGKILL)
        process.wait.assert_called_once_with(timeout=5)
        process.stdout.close.assert_called_once()
        process.stderr.close.assert_called_once()

    def test_verification_runs_get_distinct_artifact_directories(self):
        first = supervisor._verification_run_dir(self.state, "merged")
        raw = first / "review-raw.json"
        raw.write_text('{"text":"first response"}')

        second = supervisor._verification_run_dir(self.state, "merged")
        (second / "review-raw.json").write_text('{"text":"second response"}')

        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, second.parent)
        self.assertEqual(raw.read_text(), '{"text":"first response"}')
        self.assertEqual((second / "review-raw.json").read_text(), '{"text":"second response"}')

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

    def test_lead_decision_parser_rejects_ambiguous_and_malformed_contracts(self):
        approved = {
            "decision": "approve",
            "worker_brief": "bounded",
            "rationale": "reviewed",
            "contract_acks": [],
        }
        blocked = {**approved, "decision": "blocked"}
        self.assertEqual(supervisor.parse_lead_decision(json.dumps(approved)), approved)
        self.assertEqual(
            supervisor.parse_lead_decision("```json\n" + json.dumps(blocked) + "\n```"),
            blocked,
        )
        invalid = (
            '{"decision":"blocked","decision":"approve","worker_brief":"x",'
            '"rationale":"x","contract_acks":[]}',
            '{"decision":"approve","decision":"blocked","worker_brief":"x",'
            '"rationale":"x","contract_acks":[]}',
            '{"decision":"approve","worker_brief":"x","rationale":"x",'
            '"contract_acks":[{"name":"schema-validation","name":"other",'
            '"version":2,"contract_hash":"' + "a" * 64 + '"}]}',
            '{"decision":"approve","worker_brief":"x","rationale":"x"}',
            '{"decision":"approve","worker_brief":"x","rationale":"x",'
            '"contract_acks":[],"extra":true}',
            '{"decision":true,"worker_brief":"x","rationale":"x","contract_acks":[]}',
            '{"decision":"approve","worker_brief":null,"rationale":"x","contract_acks":[]}',
            '{"decision":"approve","worker_brief":"x","rationale":"x",'
            '"contract_acks":[],"extra":NaN}',
            '{"decision":"approve","worker_brief":"x","rationale":"x",'
            '"contract_acks":[],"extra":Infinity}',
            '{"decision":"approve","worker_brief":"x","rationale":"x",'
            '"contract_acks":[],"extra":1e9999}',
            "```json\n" + json.dumps(approved),
            "```json\n" + json.dumps(approved) + "\n```\ntrailing",
        )
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                supervisor.parse_lead_decision(raw)

    def test_duplicate_lead_decision_cannot_persist_ack_or_start_worker(self):
        self.task("T", "Platform")
        attempt = self.db.claim("T", "worker", 100)
        proposal = self.cfg["contracts"]["schema-validation"]
        contract_hash = hashlib.sha256(
            json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        raw = (
            '{"decision":"blocked","decision":"approve","worker_brief":"bounded",'
            '"rationale":"conflicting decisions","contract_acks":[{"name":'
            '"schema-validation","version":2,"contract_hash":"' + contract_hash + '"}]}'
        )
        FakeCodex.response = raw
        roles = []
        git_calls = []

        def fake_metered_client(*_args, **kwargs):
            roles.append(kwargs["context"]["role"])
            return FakeCodex()

        def fake_git(_repo, *args):
            git_calls.append(args)
            return "base"

        with (
            patch.object(supervisor, "metered_client", side_effect=fake_metered_client),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "git", side_effect=fake_git),
            patch.object(supervisor, "process_identity", return_value="fake"),
        ):
            supervisor.run_attempt(self.state, attempt["id"])

        self.assertEqual(roles, ["lead"])
        stages = [
            event["body"]["session"]["stage"]
            for event in self.db.events()
            if event["kind"] == "session_stage_recorded"
        ]
        self.assertEqual(stages, ["lead"])
        self.assertEqual(self.db.get_task("T")["state"], "failed")
        self.assertEqual(self.db.list_attempts("T")[0]["state"], "failed")
        self.assertEqual(self.db.get_decision("circuit/controller-parse")["body"]["status"], "open")
        self.assertEqual(
            self.db.get_decision("recovery-obligation/controller-parse")["body"]["status"],
            "open",
        )
        self.assertIsNone(self.db.get_decision("lead-T"))
        self.assertIsNone(
            self.db.get_decision(self.db._contract_ack_key("schema-validation", 2, "Platform"))
        )
        self.assertTrue((self.state / "attempts" / str(attempt["id"]) / "lead-raw.json").is_file())
        self.assertFalse((self.state / "attempts" / str(attempt["id"]) / "lead.json").exists())
        self.assertEqual(self.db.usage_summary()["total"].get("sol_turns"), 1)
        self.assertFalse(any("commit" in args for args in git_calls))

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

    def test_mismatched_controller_recovery_history_stops_before_verifier(self):
        self.task("T")
        self.task("T2")
        fault_attempt = self.db.claim("T", "worker", 100)
        self.db.open_controller_parse_fault(
            fault_attempt["id"], fault_attempt["epoch"], stage="lead", exception_type="ValueError"
        )
        circuit = self.db.get_decision("circuit/controller-parse")
        self.db.db.execute(
            "INSERT INTO decisions(name,version,body,replaces,replaces_version) VALUES(?,?,?,?,?)",
            (
                "circuit/controller-parse",
                circuit["version"] + 1,
                json.dumps({"status": "resolved"}, separators=(",", ":")),
                "circuit/controller-parse",
                circuit["version"],
            ),
        )
        attempt = self.db.claim("T2", "worker", 100)

        with (
            patch.object(supervisor, "prepare_verification_plan") as prepare,
            patch.object(supervisor, "metered_client") as dispatch,
            patch.object(supervisor, "runner") as sandbox,
            patch.object(supervisor, "git") as git,
        ):
            supervisor.run_attempt(self.state, attempt["id"])

        self.assertEqual(self.db.get_task("T")["state"], "failed")
        self.assertEqual(self.db.list_attempts()[0]["state"], "failed")
        failure = next(
            event
            for event in reversed(self.db.events())
            if event["kind"] == "attempt_failed" and event["body"]["attempt_id"] == attempt["id"]
        )
        self.assertEqual(failure["body"]["reason"], "controller_recovery_history_invalid")
        prepare.assert_not_called()
        dispatch.assert_not_called()
        sandbox.assert_not_called()
        git.assert_not_called()

    def test_malformed_controller_recovery_json_stops_before_verifier(self):
        self.task("T")
        self.task("T2")
        fault_attempt = self.db.claim("T", "worker", 100)
        self.db.open_controller_parse_fault(
            fault_attempt["id"], fault_attempt["epoch"], stage="lead", exception_type="ValueError"
        )
        self.db.db.execute(
            "UPDATE decisions SET body='{' WHERE name='circuit/controller-parse' AND version=1"
        )
        attempt = self.db.claim("T2", "worker", 100)

        with (
            patch.object(supervisor, "prepare_verification_plan") as prepare,
            patch.object(supervisor, "metered_client") as dispatch,
            patch.object(supervisor, "runner") as sandbox,
            patch.object(supervisor, "git") as git,
        ):
            supervisor.run_attempt(self.state, attempt["id"])

        self.assertEqual(self.db.get_task("T2")["state"], "failed")
        self.assertEqual(self.db.list_attempts("T2")[0]["state"], "failed")
        failure = next(
            event
            for event in reversed(self.db.events())
            if event["kind"] == "attempt_failed" and event["body"]["attempt_id"] == attempt["id"]
        )
        self.assertEqual(failure["body"]["reason"], "controller_recovery_history_invalid")
        prepare.assert_not_called()
        dispatch.assert_not_called()
        sandbox.assert_not_called()
        git.assert_not_called()

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
        with self.assertRaisesRegex(StateError, "independent review"):
            self.db.acknowledge_contract_transition("schema-validation", 2, "Experience", digest)
        self.reconcile("merged")
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 1)
        with self.assertRaisesRegex(StateError, "independent review"):
            self.db.acknowledge_contract_transition("schema-validation", 2, "Platform", "0" * 64)
        self.reconcile("merged")
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 1)
        self.approve_contract_review("schema-validation", 2, "Experience")
        self.approve_contract_review("schema-validation", 2, "Platform")
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
        self.db.propose_contract_transition(
            "research-output",
            1,
            2,
            self.cfg["contracts"]["research-output"],
            producer_task_id="EXP-002",
            producer_team="Experience",
            consumer_teams=["Platform", "Research"],
        )
        for team in ("Experience", "Platform", "Research"):
            self.approve_contract_review("research-output", 2, team)

        self.activate_existing_reviews()

        self.assertEqual(self.db.get_contract("research-output")["version"], 2)
        # An unrelated configured contract stays pending until its own producer and acks exist.
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 1)

    def test_contract_review_identity_survives_unrelated_active_tip_advance_and_ack(self):
        proposal = {
            "version": 2,
            "producer": "Experience",
            "producer_task_id": "EXP-003",
            "consumers": ["Platform"],
            "schema": "research-output-v2",
        }
        self.cfg["contracts"] = {"research-output": proposal}
        self.db.put_contract("research-output", 1, {"schema": "research-output-v1"})
        self.candidate("EXP-003", target_state="integrated", team="Experience")
        self.db.propose_contract_transition(
            "research-output",
            1,
            2,
            proposal,
            producer_task_id="EXP-003",
            producer_team="Experience",
            consumer_teams=["Platform"],
        )
        source = {
            "source_commit": "a" * 40,
            "receipt": "b" * 64,
            "policy_sha256": "c" * 64,
            "plan_sha256": "d" * 64,
            "active_tip": "e" * 40,
        }
        with (
            patch.object(supervisor, "config", return_value=self.cfg),
            patch.object(supervisor, "_contract_review_source", return_value=source),
        ):
            initial_ids = supervisor.activate_contract(self.state, self.db)
        self.assertEqual(len(initial_ids), 2)
        producer_request = next(
            self.db.get_contract_review_request(request_id)
            for request_id in initial_ids
            if self.db.get_contract_review_request(request_id)["body"]["team"] == "Experience"
        )
        producer_body = producer_request["body"]
        self.db.begin_contract_review_dispatch(
            producer_body["request_id"], producer_body["packet_hash"]
        )
        self.db.record_contract_review_session(
            producer_body["request_id"],
            producer_body["packet_hash"],
            "synthetic-tip-advance-review",
            "synthetic-reviewer",
            producer_body["dispatch_id"],
            {"pid": 123, "birth": "synthetic"},
        )
        result = {
            "decision": "approve",
            "compatibility": "compatible",
            "rationale": "Synthetic exact request approval.",
            "findings": [],
            "reviewed_evidence_refs": producer_body["packet"]["evidence_refs"],
            "affected_consumers_reviewed": producer_body["packet"]["affected_consumer_task_ids"],
            "migration_plan": producer_body["packet"]["migration_plan"],
        }
        raw = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        self.db.record_contract_review_response(
            producer_body["request_id"],
            producer_body["packet_hash"],
            producer_body["dispatch_id"],
            "synthetic-tip-advance-turn",
            raw,
        )
        self.db.replay_contract_review_response(
            producer_body["request_id"], producer_body["packet_hash"]
        )

        advanced_source = {**source, "active_tip": "f" * 40}
        with (
            patch.object(supervisor, "config", return_value=self.cfg),
            patch.object(supervisor, "_contract_review_source", return_value=advanced_source),
        ):
            resumed_ids = supervisor.activate_contract(self.state, self.db)
        self.assertEqual(resumed_ids, initial_ids)
        self.assertEqual(self.db.contract_review_worklist()["count"], 2)

    def test_configured_contracts_are_detached_before_database_reads(self):
        configured = {
            "alpha-contract": {
                "version": 2,
                "producer": "Research",
                "producer_task_id": "ALPHA-1",
                "consumers": ["Platform"],
                "schema": "alpha-v2",
            },
            "zeta-contract": {
                "version": 2,
                "producer": "Experience",
                "producer_task_id": "ZETA-1",
                "consumers": ["Research"],
                "schema": "zeta-v2",
            },
        }
        self.cfg["contracts"] = configured
        self.db.put_contract("alpha-contract", 1, {"schema": "alpha-v1"})
        self.db.put_contract("zeta-contract", 1, {"schema": "zeta-v1"})
        original_get_contract = self.db.get_contract
        mutated = False

        def mutate_configuration_during_read(name):
            nonlocal mutated
            if not mutated:
                mutated = True
                configured["alpha-contract"].update(
                    producer="ChangedAlpha",
                    producer_task_id="CHANGED-ALPHA",
                    consumers=["ChangedAlphaConsumer"],
                    schema="changed-alpha-v2",
                )
                configured["zeta-contract"].update(
                    producer="ChangedZeta",
                    producer_task_id="CHANGED-ZETA",
                    consumers=["ChangedZetaConsumer"],
                    schema="changed-zeta-v2",
                )
            return original_get_contract(name)

        with patch.object(self.db, "get_contract", side_effect=mutate_configuration_during_read):
            transitions = supervisor._configured_contract_transitions(self.cfg, self.db)

        self.assertEqual(
            {
                item["name"]: (
                    item["body"]["body"],
                    item["body"]["producer_team"],
                    item["body"]["producer_task_id"],
                    item["body"]["consumer_teams"],
                )
                for item in transitions
            },
            {
                "alpha-contract": (
                    {
                        "version": 2,
                        "producer": "Research",
                        "producer_task_id": "ALPHA-1",
                        "consumers": ["Platform"],
                        "schema": "alpha-v2",
                    },
                    "Research",
                    "ALPHA-1",
                    ["Platform"],
                ),
                "zeta-contract": (
                    {
                        "version": 2,
                        "producer": "Experience",
                        "producer_task_id": "ZETA-1",
                        "consumers": ["Research"],
                        "schema": "zeta-v2",
                    },
                    "Experience",
                    "ZETA-1",
                    ["Research"],
                ),
            },
        )

    def test_invalid_configured_contract_json_fails_before_database_reads(self):
        self.cfg["contracts"]["schema-validation"]["nonfinite"] = float("nan")
        events_before = self.db.events()
        with (
            patch.object(self.db, "get_contract", side_effect=AssertionError("database read")),
            self.assertRaisesRegex(StateError, "contract configuration invalid"),
        ):
            supervisor._configured_contract_transitions(self.cfg, self.db)
        self.assertEqual(self.db.events(), events_before)

    def test_configured_contract_plain_types_are_checked_before_detaching(self):
        self.cfg["contracts"]["schema-validation"]["consumers"] = ("Platform",)
        events_before = self.db.events()
        with (
            patch.object(self.db, "get_contract", side_effect=AssertionError("database read")),
            self.assertRaisesRegex(StateError, "contract configuration invalid"),
        ):
            supervisor._configured_contract_transitions(self.cfg, self.db)
        self.assertEqual(self.db.events(), events_before)

    def test_contract_activation_revalidates_ack_history_after_publication(self):
        self.candidate("EXP-001", target_state="integrated", team="Experience")
        self.db.propose_contract_transition(
            "schema-validation",
            1,
            2,
            self.cfg["contracts"]["schema-validation"],
            producer_task_id="EXP-001",
            producer_team="Experience",
            consumer_teams=["Platform"],
        )
        for team in ("Experience", "Platform"):
            self.approve_contract_review("schema-validation", 2, team)
        self.activate_existing_reviews()
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 2)

        ack_key = self.db._contract_ack_key("schema-validation", 2, "Platform")
        ack = self.db.get_decision(ack_key)
        tampered = {
            **ack["body"],
            "review_packet_hash": "1" * 64,
            "review_raw_sha256": "2" * 64,
        }
        tampered_event = json.dumps(
            {
                "version": ack["version"],
                "body": tampered,
                "replaces": None,
                "replaces_version": None,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        tampered_body = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
        self.db.db.execute(
            "UPDATE decisions SET body=? WHERE name=? AND version=?",
            (tampered_body, ack_key, ack["version"]),
        )
        self.db.db.execute(
            "UPDATE events SET body=? WHERE kind='decision_recorded' AND entity=?",
            (tampered_event, ack_key),
        )
        with self.assertRaisesRegex(StateError, "review provenance invalid"):
            self.activate_existing_reviews()
        self.db.db.execute("DELETE FROM decisions WHERE name=?", (ack_key,))
        with self.assertRaisesRegex(StateError, "activation evidence incomplete"):
            self.activate_existing_reviews()

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
        hashlib.sha256(
            json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        lead.response = json.dumps(
            {
                "decision": "approve",
                "worker_brief": "bounded",
                "rationale": "reviewed v2",
                "contract_acks": [],
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
        self.assertEqual(self.db.get_decision("lead-T")["body"]["plan"]["decision"], "approve")
        ack = self.db.db.execute(
            "SELECT body FROM decisions WHERE name LIKE 'contract-ack/%'"
        ).fetchone()
        self.assertIsNone(ack)

    def test_superseded_attempt_cannot_persist_lead_acknowledgement(self):
        self.task("T", "Platform")
        attempt = self.db.claim("T", "worker", 100)
        proposal = self.cfg["contracts"]["schema-validation"]
        hashlib.sha256(
            json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        lead, worker = FakeCodex(), FakeCodex()
        lead.response = json.dumps(
            {
                "decision": "approve",
                "worker_brief": "bounded",
                "rationale": "reviewed v2",
                "contract_acks": [],
            }
        )
        roles = []
        replacement_attempts = []
        original = Store.record_lead_result

        def supersede_before_lead_commit(store, attempt_id, epoch, **kwargs):
            self.now = 1101.0
            store.recover_expired()
            replacement_attempts.append(store.claim("T", "replacement", 100))
            return original(store, attempt_id, epoch, **kwargs)

        from contextlib import contextmanager

        @contextmanager
        def fake_metered_client(*args, **kwargs):
            role = kwargs["context"]["role"]
            roles.append(role)
            yield lead if role == "lead" else worker

        with (
            patch.object(supervisor, "metered_client", side_effect=fake_metered_client),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "git", return_value="base"),
            patch.object(supervisor, "process_identity", return_value="fake"),
            patch.object(Store, "record_lead_result", new=supersede_before_lead_commit),
        ):
            supervisor.run_attempt(self.state, attempt["id"])

        self.assertEqual(roles, ["lead"])
        attempts = self.db.list_attempts("T")
        self.assertEqual(attempts[0]["state"], "expired")
        replacement = self.db.get_task("T")
        self.assertEqual(replacement["state"], "pending")
        self.assertEqual(replacement["epoch"], 1)
        self.assertEqual(replacement_attempts, [None])
        self.assertEqual(self.db.usage_summary()["total"], {"sol_turns": 1})
        self.assertIsNone(self.db.get_decision("lead-T"))
        self.assertIsNone(
            self.db.get_decision(self.db._contract_ack_key("schema-validation", 2, "Platform"))
        )

    def test_fenced_lead_result_rolls_back_all_writes_on_later_invalid_ack(self):
        self.task("T", "Platform")
        attempt = self.db.claim("T", "worker", 100)
        proposal = self.cfg["contracts"]["schema-validation"]
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
        events_before = self.db.events()
        plan = {
            "decision": "approve",
            "worker_brief": "bounded",
            "rationale": "reviewed",
            "contract_acks": [
                {"name": "schema-validation", "version": 2, "contract_hash": digest},
                {"name": "missing-transition", "version": 2, "contract_hash": "a" * 64},
            ],
        }

        with self.assertRaisesRegex(StateError, "lead result plan invalid"):
            self.db.record_lead_result(
                attempt["id"],
                attempt["epoch"],
                thread="lead-thread",
                model="sol-test",
                base="base-sha",
                plan=plan,
            )

        self.assertEqual(self.db.events(), events_before)
        self.assertIsNone(self.db.get_decision("lead-T"))
        self.assertIsNone(
            self.db.get_decision(self.db._contract_ack_key("schema-validation", 2, "Platform"))
        )
        self.assertEqual(self.db.get_task("T")["state"], "running")

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
                "contract_acks": [],
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
        self.assertEqual(decoded, [])
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
        with self.assertRaisesRegex(ValueError, "independent_review"):
            supervisor._validate_lead_contract_acks(
                [exact], team="Research", transitions=[transition]
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
        self.activate_existing_reviews()
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
        transition["body"]["contract_hash"]
        self.approve_contract_review("research-output", 2, "Research")
        self.approve_contract_review("research-output", 2, "Platform")
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
            self.activate_existing_reviews()

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
                transition["body"]["contract_hash"]
                self.approve_contract_review("schema-validation", 2, "Experience")
                self.approve_contract_review("schema-validation", 2, "Platform")
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
