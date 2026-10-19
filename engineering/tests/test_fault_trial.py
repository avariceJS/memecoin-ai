"""Synthetic evidence auditing and signal guards; no live fault injection."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engineering.executive import Executive
from engineering.fault_trial import TrialError, _kill_identity, audit, observe, run_trial
from engineering.org_state import Store


class FaultTrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.db = Store(self.state / "organization.sqlite", clock=lambda: 1000)
        self.addCleanup(self.db.close)

    def task(self, name, team):
        self.db.add_task(name, team, "Synthetic work")
        return self.db.claim(name, "synthetic-worker")

    def integrate(self, name, attempt):
        self.db.submit(attempt["id"], attempt["epoch"], "candidate-" + name, {})
        self.db.verify(name, "candidate-" + name, True, {})
        self.db.accept(name, "candidate-" + name)
        self.db.integrate(name, "candidate-" + name, "merge-" + name)

    def receipt(self, name, value):
        (self.state / name).write_text(json.dumps(value))

    def test_missing_receipts_never_infer_fault_from_ordinary_retry(self):
        first = self.task("EXP-001", "Experience")
        self.db.fail(first["id"], first["epoch"], "ordinary failure")
        self.db.claim("EXP-001", "second-worker")
        report = audit(self.state)
        self.assertFalse(report["passed"])
        self.assertFalse(report["checks"]["worker_fault_recorded"])
        self.assertFalse(report["checks"]["faulted_worker_fence_replaced"])

    def test_audit_checks_exact_fault_fence_and_journal_uniqueness(self):
        exp = self.task("EXP-001", "Experience")
        ctx = self.task("CTX-001", "Platform")
        self.db.record_stage_session(
            exp["id"], exp["epoch"], "synthetic-worker-thread", "gpt-6-luna", stage="worker"
        )
        exp = self.db.list_attempts("EXP-001")[0]
        initial = observe(self.state)
        for attempt in initial["attempts"]:
            attempt["session"] = {"thread_id": "synthetic-thread"}
        self.receipt(
            "chief-fault.json",
            {
                "chief": {"epoch": 1},
                "killed": {"runner": True, "appserver": True},
                "tasks": initial["tasks"],
                "attempts": initial["attempts"],
            },
        )
        self.receipt(
            "worker-fault.json", {"attempt": exp, "killed": {"runner": True, "appserver": True}}
        )
        self.receipt(
            "supervisor-fault.json",
            {"killed": True, "replacement": {"pid": 12345, "birth": "synthetic"}},
        )
        self.receipt("operations.jsonl", {})  # overwritten with one append-format event below
        (self.state / "operations.jsonl").write_text(
            json.dumps({"kind": "supervisor_started", "pid": 12345, "birth": "synthetic"}) + "\n"
        )
        executive = Executive(self.db)
        first = executive.claim("old")
        executive.release(first)
        second = executive.claim("replacement")
        executive.decision(second, "executive-episode-2", "Synthetic executive completion")
        executive.release(second)
        self.db.fail(exp["id"], exp["epoch"], "synthetic injected failure")
        replacement = self.db.claim("EXP-001", "replacement")
        self.integrate("EXP-001", replacement)
        self.integrate("CTX-001", ctx)
        plat = self.task("PLAT-001", "Platform")
        self.integrate("PLAT-001", plat)
        report = audit(self.state)
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["faulted_attempt"], exp["id"])
        self.assertEqual(report["replacement_attempt"], replacement["id"])
        self.db._event("candidate_integrated", "EXP-001", {"synthetic_duplicate": True})
        self.assertFalse(audit(self.state)["checks"]["unique_integrations"])

    def test_nonmatching_manual_fence_is_not_counted_as_interruption(self):
        first = self.task("EXP-001", "Experience")
        self.receipt(
            "worker-fault.json",
            {"attempt": {**first, "epoch": 999}, "killed": {"runner": True, "appserver": True}},
        )
        self.assertFalse(audit(self.state)["checks"]["worker_fault_recorded"])

    def test_observer_does_not_create_or_mutate_journal(self):
        self.task("T", "Platform")
        before = self.db.events()
        self.assertEqual(observe(self.state)["tasks"][0]["id"], "T")
        self.assertEqual(self.db.events(), before)

    def test_signal_refusal_is_failure_and_never_retargets(self):
        identity = {
            "pid": 1001,
            "birth": "runner-birth",
            "app_server_pid": 1002,
            "app_server_birth": "app-birth",
        }
        with patch("engineering.fault_trial.terminate_owned", return_value=False) as kill:
            with self.assertRaises(TrialError):
                _kill_identity(identity)
        self.assertEqual(
            [call.args for call in kill.call_args_list],
            [(1001, "runner-birth"), (1002, "app-birth")],
        )

    def test_existing_trial_refuses_all_new_signals_and_launches(self):
        self.receipt("fault-trial.json", {"status": "running"})
        with (
            patch("engineering.fault_trial.config", return_value={}),
            patch("engineering.fault_trial.terminate_owned") as kill,
            patch("engineering.fault_trial.subprocess.Popen") as spawn,
        ):
            with self.assertRaises(TrialError):
                run_trial(self.state)
        kill.assert_not_called()
        spawn.assert_not_called()
