"""No-model fault harness around real durable state and mocked external effects."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from engineering import supervisor
from engineering.confinement import SandboxResult
from engineering.org_state import Store
from engineering.tests.test_service import deployment as deployment_fixture
from engineering.usage_ledger import UsageLedger


class FakeCodex:
    """Only a local fake; never starts an app server or calls a model."""

    response = "{malformed"

    def __init__(self, *args, **kwargs):
        self.proc = SimpleNamespace(pid=123)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def start(self, *args):
        return "fake-thread"

    def turn(self, *args, **kwargs):
        return {"text": self.response, "usage": {"total": {"totalTokens": 3}}}

    def call(self, *args, **kwargs):
        return {"ordinaryUsageAllowed": False}


@contextmanager
def fake_metered_client(client):
    yield client


def metered_clients(*clients):
    iterator = iter(clients)

    @contextmanager
    def factory(*args, **kwargs):
        yield next(iterator)

    return factory


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        (self.state / "intents").mkdir()
        self.now = 1000.0
        self.db = Store(self.state / "organization.sqlite", clock=lambda: self.now)
        self.addCleanup(self.db.close)
        self.cfg = {
            "python": sys.executable,
            "controller": str(self.state),
            "contracts": {"schema-validation": {"meaning": "strict validation v2"}},
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

    def task(self, task_id="T"):
        self.db.add_task(task_id, "team", "Synthetic task", scope=["src/example.py"])

    def candidate(self, task_id="T", sha="submitted", target_state="accepted"):
        self.task(task_id)
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
            supervisor.verify_one(self.state, self.db.get_task("T"))
        self.assertEqual(self.db.get_task("T")["state"], "pending")
        second = self.db.claim("T", "worker")
        self.assertEqual(second["epoch"], 2)

    def verification_failure(self, stdout, reviewer_text="{malformed"):
        """Drive the actual verifier callback with pure fake tool/model responses."""
        self.candidate(target_state="submitted")
        checkout = self.state / "candidate-checkout"
        checkout.mkdir()
        candidate = SimpleNamespace(sha="merged", integration_tip="base", checkout=checkout)
        gate = MagicMock()
        gate.submit.return_value = candidate
        policy = supervisor.VerificationPolicy(((sys.executable, "fixed-acceptance"),))
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
                supervisor, "metered_client", side_effect=metered_clients(reviewer)
            ) as model,
            patch.object(supervisor, "git", return_value="synthetic diff"),
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

    def test_malformed_independent_reviewer_json_cannot_accept_candidate(self):
        model = self.verification_failure(
            json.dumps({"schema": "org-pilot-acceptance-v1", "case": "T", "passed": True})
        )
        model.assert_called_once()

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

    def test_malformed_lead_output_retries_without_running_worker(self):
        self.task()
        attempt = self.db.claim("T", "worker", 100)
        calls = []

        def fake_git(repo, *args):
            calls.append(args)
            return "base"

        with (
            patch.object(supervisor, "metered_client", side_effect=metered_clients(FakeCodex())),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "git", side_effect=fake_git),
            patch.object(supervisor, "process_identity", return_value="fake"),
        ):
            supervisor.run_attempt(self.state, attempt["id"])
        self.assertEqual(self.db.get_task("T")["state"], "pending")
        self.assertEqual(self.db.list_attempts()[0]["state"], "failed")
        self.assertFalse(any("commit" in args for args in calls))
        self.assertEqual(self.db.usage_summary()["total"]["tokens_unknown"], 1)

    def test_malformed_worker_output_retries_before_commit(self):
        self.task()
        attempt = self.db.claim("T", "worker", 100)
        lead, worker = FakeCodex(), FakeCodex()
        lead.response = json.dumps(
            {
                "decision": "approve",
                "worker_brief": "bounded",
                "rationale": "settled",
                "contract_ack": False,
            }
        )
        worker.response = "{malformed"
        calls = []

        def fake_git(repo, *args):
            calls.append(args)
            return " M src/example.py" if args[0] == "status" else "candidate"

        with (
            patch.object(supervisor, "metered_client", side_effect=metered_clients(lead, worker)),
            patch.object(supervisor, "runner", return_value=MagicMock()),
            patch.object(supervisor, "git", side_effect=fake_git),
            patch.object(supervisor, "process_identity", return_value="fake"),
        ):
            supervisor.run_attempt(self.state, attempt["id"])
        self.assertEqual(self.db.get_task("T")["state"], "pending")
        self.assertFalse(any("commit" in args for args in calls))

    def test_contract_activation_requires_both_matching_hash_ack_and_is_idempotent(self):
        self.candidate("EXP-001", target_state="integrated")
        digest = hashlib.sha256(
            json.dumps(self.cfg["contracts"]["schema-validation"], sort_keys=True).encode()
        ).hexdigest()
        self.db.put_decision(
            "contract-ack/" + digest + "/Experience",
            {"contract_hash": digest, "version": 2, "contract_ack": True},
        )
        self.reconcile("merged")
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 1)
        self.db.put_decision("contract-ack/" + "wrong-hash" + "/Platform", {"contract_ack": True})
        self.reconcile("merged")
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 1)
        self.db.put_decision(
            "contract-ack/" + digest + "/Platform",
            {"contract_hash": digest, "version": 2, "contract_ack": True},
        )
        self.reconcile("merged")
        self.reconcile("merged")
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 2)
        self.assertEqual(
            self.db.get_contract("schema-validation")["body"],
            self.cfg["contracts"]["schema-validation"],
        )
        published = [event for event in self.db.events() if event["kind"] == "contract_published"]
        self.assertEqual(len(published), 2)  # original v1, one activation v2

    def test_integrated_producer_without_two_lead_ack_keeps_contract_v1(self):
        self.candidate("EXP-001", target_state="integrated")
        self.reconcile("merged")
        self.assertEqual(self.db.get_contract("schema-validation")["version"], 1)

    def run_quiescence(self, reason, *, pending=False, slots=1):
        if pending:
            self.task()
        else:
            self.task()
            attempt = self.db.claim("T", "worker", 100)
            self.db.submit(attempt["id"], attempt["epoch"], "sha", {})
            self.db.verify("T", "sha", True, {})
            self.db.accept("T", "sha")
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
            supervisor.supervise(self.state, max_seconds=10)
        executive.assert_not_called()
        model_turn.assert_not_called()
        handoffs = [
            event
            for event in self.db.events()[events_before:]
            if event["kind"] == "decision_recorded"
            and event.get("entity", "").startswith("sol-handoff/")
        ]
        self.assertEqual(len(handoffs), 1)
        details = self.db.get_decision(handoffs[-1]["entity"])["body"]
        self.assertEqual(details["reason"], reason)
        (self.state / "STOP").unlink(missing_ok=True)

    def test_quiescence_catalogue_exhausted_handoff_has_no_chief_call(self):
        self.run_quiescence("catalogue-exhausted")

    def test_quiescence_blocked_dependencies_handoff_has_no_chief_call(self):
        self.run_quiescence("blocked-dependencies", pending=True)

    def test_quiescence_unavailable_quota_handoff_has_no_chief_call(self):
        self.run_quiescence("quota-unavailable", slots=0)

    def test_measured_usage_keeps_history_separate_from_metered_union(self):
        self.task()
        attempt = self.db.claim("T", "worker", 100)
        self.db.record_usage(attempt["id"], attempt["epoch"], {"tokens": 42})
        ledger_path = self.state / "usage-ledger.sqlite"
        ledger = UsageLedger(ledger_path)
        baseline = {
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_output_tokens": 0,
            "total_tokens": 0,
        }
        latest = {**baseline, "input_tokens": 10, "total_tokens": 10}
        ledger.register("thread", "epoch-1", "gpt-6-luna", baseline, evidence="new")
        ledger.observe(
            "thread",
            "epoch-1",
            "receipt-1",
            latest,
            source="qualified-test",
            sequence=10,
            observed_at=1000,
        )
        ledger.close()
        measured = supervisor.measured_usage(self.state, self.db)
        self.assertEqual(measured["total"]["tokens"], 42)
        self.assertEqual(measured["current_metering"]["incremental"]["total_tokens"], 10)
        self.assertEqual(measured["historical_usage"]["total"]["tokens"], 42)

    def test_measured_usage_does_not_create_missing_metering_database(self):
        path = self.state / "usage-ledger.sqlite"
        self.assertFalse(path.exists())
        measured = supervisor.measured_usage(self.state, self.db)
        self.assertIsNone(measured["current_metering"])
        self.assertFalse(path.exists())

    def test_recovery_gate_requires_coherent_owner_state_and_no_stop(self):
        ledger = UsageLedger(self.state / "usage-ledger.sqlite")
        self.assertTrue(supervisor.organization_state_reconciled(self.state, ledger, 1000, self.db))
        (self.state / "STOP").touch()
        self.assertFalse(
            supervisor.organization_state_reconciled(self.state, ledger, 1000, self.db)
        )
        (self.state / "STOP").unlink()
        self.task()
        attempt = self.db.claim("T", "worker", 100)
        self.db.submit(
            attempt["id"],
            attempt["epoch"],
            "submitted",
            {"checkout": str(self.state / "worker"), "base": "base"},
        )
        self.assertFalse(
            supervisor.organization_state_reconciled(self.state, ledger, 1000, self.db)
        )
        ledger.close()

    def test_supervisor_quota_reads_share_durable_pause(self):
        from engineering.metered_codex import Controller
        from engineering.supervisor import supervisor_quota_sample

        current = {"time": 1000}

        def sample(percent):
            return {
                "ordinaryUsageAllowed": True,
                "rateLimitsByLimitId": {
                    "codex": {
                        "primary": {"usedPercent": percent, "resetsAt": 9999},
                        "secondary": None,
                        "rateLimitReachedType": None,
                        "spendControlReached": False,
                        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                    }
                },
            }

        ledger = UsageLedger(self.state / "usage-ledger.sqlite")
        controller = Controller(
            ledger,
            lambda: {"rate_limits": sample(99), "observed_at": current["time"]},
            state=self.state,
            clock=lambda: current["time"],
            state_reconciler=lambda now: True,
        )
        limits = supervisor_quota_sample(controller, sample(99), current["time"])
        self.assertIs(limits["ordinaryUsageAllowed"], False)
        self.assertIsNotNone(ledger.get_controller_state("resource_pause"))
        current["time"] = 1001
        limits = supervisor_quota_sample(controller, sample(68), current["time"])
        self.assertIs(limits["ordinaryUsageAllowed"], False)
        current["time"] = 1061
        limits = supervisor_quota_sample(controller, sample(68), current["time"])
        self.assertIs(limits["ordinaryUsageAllowed"], True)
        self.assertIsNone(ledger.get_controller_state("resource_pause"))
        controller.close()
        ledger.close()

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
