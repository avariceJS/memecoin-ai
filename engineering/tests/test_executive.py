"""Durable governance tests: no models, jobs, data or network involved."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from engineering.executive import Executive, ExecutiveError, StaleExecutive
from engineering.org_state import Store


class ExecutiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "org.sqlite"
        self.now = 1000.0
        self.store = Store(self.path, clock=lambda: self.now)
        self.executive = Executive(self.store)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def lease(self, owner="episode-1", **kwargs):
        return self.executive.claim(owner, **kwargs)

    def request(self, lease, request_id="R1", **kwargs):
        return self.executive.request(
            lease,
            request_id,
            "resource",
            "Platform",
            "Additional offline CPU budget",
            "Queue is measured",
            "Continue existing bounded tasks",
            **kwargs,
        )

    def test_model_and_reasoning_identity_are_checked_without_starting_model(self):
        for wrong in ({"model": "gpt-6.1-sol"}, {"reasoning": "low"}, {"reasoning": "High"}):
            with self.assertRaises(ExecutiveError):
                self.lease(**wrong)
        self.assertEqual(self.store.events(), [])
        lease = self.lease()
        self.assertEqual((lease["model"], lease["reasoning"]), ("gpt-6-astra", "high"))

    def test_only_one_live_episode_across_connections(self):
        other = Store(self.path, clock=lambda: self.now)
        try:
            alternate = Executive(other)
            with ThreadPoolExecutor(2) as pool:
                leases = list(
                    pool.map(
                        lambda pair: pair[0].claim(pair[1]),
                        [(self.executive, "one"), (alternate, "two")],
                    )
                )
            self.assertEqual(sum(lease is not None for lease in leases), 1)
            self.assertEqual(len(self.store.events()), 1)
        finally:
            other.close()

    def test_expiry_replacement_reconstructs_obligations_and_rejects_stale_episode(self):
        first = self.lease(lease_seconds=10)
        self.executive.obligation(
            first,
            "O1",
            "Preserve evidence",
            rationale="Recovery",
            evidence_refs=["receipt:synthetic"],
        )
        self.request(first)
        self.now += 10
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.executive = Executive(self.store)
        second = self.lease("replacement")
        self.assertEqual(second["epoch"], first["epoch"] + 1)
        self.assertEqual(self.executive.snapshot()["obligations"][0]["id"], "O1")
        self.assertEqual(self.executive.snapshot()["requests"][0]["id"], "R1")
        for mutation in (
            lambda: self.executive.decision(first, "D1", "Stale"),
            lambda: self.executive.transition_obligation(
                first, "O1", "completed", completion_evidence=["fake"]
            ),
            lambda: self.executive.resolve_request(first, "R1", "resolved", "late"),
            lambda: self.executive.renew(first),
            lambda: self.executive.release(first),
        ):
            with self.assertRaises(StaleExecutive):
                mutation()
        self.executive.decision(second, "D2", "Continue existing obligation")

    def test_exact_deadline_rejects_even_before_replacement(self):
        first = self.lease(lease_seconds=1)
        self.now += 1
        with self.assertRaises(StaleExecutive):
            self.executive.decision(first, "D1", "Too late")
        self.assertEqual(self.executive.snapshot()["role"]["status"], "expired")

    def test_release_is_episodic_and_old_fence_cannot_reactivate(self):
        first = self.lease()
        self.executive.release(first, "Episode complete; teams continue")
        self.assertEqual(self.executive.snapshot()["role"]["status"], "released")
        with self.assertRaises(StaleExecutive):
            self.executive.renew(first)
        second = self.lease("episode-2")
        self.assertEqual(second["epoch"], 2)

    def test_renewal_retains_fence_and_records_latest_event(self):
        first = self.lease(lease_seconds=10)
        self.now += 5
        renewed = self.executive.renew(first, lease_seconds=20)
        self.assertEqual(renewed["epoch"], first["epoch"])
        self.assertEqual(renewed["lease_until"], 1025)
        self.executive.decision(first, "D1", "Original fence still identifies this episode")
        self.assertGreater(
            self.executive.snapshot()["role"]["last_event_seq"], renewed["last_event_seq"]
        )

    def test_decisions_immutable_idempotent_and_explicit_supersession(self):
        lease = self.lease()
        first = self.executive.decision(lease, "D1", "Initial scope", evidence_refs=["synthetic:1"])
        count = len(self.store.events())
        self.assertEqual(
            first,
            self.executive.decision(lease, "D1", "Initial scope", evidence_refs=["synthetic:1"]),
        )
        self.assertEqual(len(self.store.events()), count)
        with self.assertRaises(ExecutiveError):
            self.executive.decision(lease, "D1", "Silent overwrite")
        second = self.executive.decision(lease, "D2", "Revised scope", supersedes="D1")
        self.assertEqual(second["supersedes"], "D1")
        self.assertEqual(self.executive.get_decision("D1")["summary"], "Initial scope")
        with self.assertRaises(ExecutiveError):
            self.executive.decision(lease, "D3", "Unknown reference", supersedes="missing")

    def test_duplicate_request_after_replacement_is_idempotent(self):
        first = self.lease()
        request = self.request(first)
        self.executive.release(first)
        second = self.lease("replacement")
        count = len(self.store.events())
        self.assertEqual(self.request(second), request)
        self.assertEqual(len(self.store.events()), count)
        self.assertEqual(self.executive.snapshot()["counts"]["requests"], 1)

    def test_obligation_transitions_are_journaled_and_terminal_cannot_reopen(self):
        lease = self.lease()
        initial = self.executive.obligation(
            lease, "O1", "Review contract", owner_role="lead", team="Platform", blocked_by=["R1"]
        )
        self.assertEqual(initial["status"], "blocked")
        self.executive.transition_obligation(lease, "O1", "open")
        done = self.executive.transition_obligation(
            lease, "O1", "completed", completion_evidence=["receipt:approved"]
        )
        self.assertGreater(done["updated_seq"], initial["created_seq"])
        with self.assertRaises(ExecutiveError):
            self.executive.transition_obligation(lease, "O1", "open")
        self.assertEqual(self.executive.snapshot()["obligations"], [])
        self.assertEqual(
            self.executive.get_obligation("O1")["completion_evidence"], ["receipt:approved"]
        )
        transitions = [
            e for e in self.store.events() if e["kind"] == "executive_obligations_transition"
        ]
        self.assertEqual([e["body"]["to"] for e in transitions], ["open", "completed"])
        self.executive.obligation(lease, "O2", "Follow-up review")

    def test_completion_and_blockers_require_evidence(self):
        lease = self.lease()
        self.executive.obligation(lease, "O1", "Review")
        for status in ("completed", "blocked"):
            with self.assertRaises(ExecutiveError):
                self.executive.transition_obligation(lease, "O1", status)
        self.assertEqual(self.executive.get_obligation("O1")["status"], "open")

    def test_cancellation_requires_evidence_and_is_terminal(self):
        lease = self.lease()
        self.executive.obligation(lease, "O1", "Resolve dependency")
        with self.assertRaisesRegex(ExecutiveError, "cancellation requires evidence"):
            self.executive.transition_obligation(lease, "O1", "cancelled")
        cancelled = self.executive.transition_obligation(
            lease,
            "O1",
            "cancelled",
            cancellation_evidence=["dependency-readiness:sha256"],
        )
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["cancellation_evidence"], ["dependency-readiness:sha256"])
        with self.assertRaisesRegex(ExecutiveError, "terminal records cannot reopen"):
            self.executive.transition_obligation(lease, "O1", "open")

    def test_pending_resource_request_and_ceo_expiration_do_not_block_tasks(self):
        self.store.add_task("T1", "Platform", "Independent task")
        lease = self.lease(lease_seconds=1)
        self.request(lease)
        self.now += 2
        self.assertEqual([task["id"] for task in self.store.ready_tasks()], ["T1"])
        attempt = self.store.claim("T1", "worker")
        self.assertIsNotNone(attempt)
        self.assertEqual(self.executive.snapshot()["requests"][0]["status"], "open")

    def test_request_resolution_records_disposition_without_policy_or_task_changes(self):
        lease = self.lease()
        self.request(lease)
        self.store.put_contract("boundary", 1, {"paid_execution": False})
        resolved = self.executive.resolve_request(
            lease,
            "R1",
            "resolved",
            "Human response recorded",
            evidence_refs=["user:message-reference"],
        )
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(
            self.executive.get_request("R1")["resolution_evidence"], ["user:message-reference"]
        )
        self.assertEqual(self.store.get_contract("boundary")["body"], {"paid_execution": False})
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(self.executive.snapshot()["requests"], [])
        with self.assertRaises(ExecutiveError):
            self.executive.resolve_request(lease, "R1", "declined", "Rewrite history")

    def test_snapshot_and_russian_report_are_bounded_and_keep_detailed_history(self):
        lease = self.lease()
        for index in range(30):
            self.executive.decision(lease, f"D{index}", "Синтетическое решение " + str(index))
        self.request(lease)
        view = self.executive.snapshot(history_limit=3, open_limit=1)
        self.assertEqual(len(view["decisions"]), 3)
        self.assertEqual(len(view["events"]), 3)
        self.assertEqual(view["counts"]["decisions"], 30)
        self.assertEqual(self.executive.get_decision("D0")["summary"], "Синтетическое решение 0")
        self.assertLessEqual(len(self.executive.render_report(max_chars=200)), 200)
        self.assertIn("Ожидающие запросы", self.executive.render_report())
        self.assertEqual(len(self.executive.snapshot(history_limit=0)["events"]), 0)
        with self.assertRaises(ExecutiveError):
            self.executive.snapshot(history_limit=21)

    def test_founder_snapshot_uses_bounded_active_task_projection(self):
        for index in range(105):
            self.store.add_task(f"TASK-{index:03d}", "Engineering", f"Task {index}")
        view = self.executive.snapshot()
        self.assertEqual(len(view["tasks"]), 100)
        self.assertEqual(view["counts"]["tasks"], 105)
        self.assertEqual(view["counts"]["active_tasks"], 105)
        self.assertEqual(view["counts"]["omitted_active_tasks"], 5)
        self.assertEqual(view["counts"]["task_states"], {"pending": 105})
        self.assertEqual(len(self.store.list_tasks()), 105)
        report = self.executive.render_report()
        self.assertIn("pending: 105", report)
        self.assertIn("100 из 105", report)

    def test_journal_and_projection_rollback_together(self):
        lease = self.lease()
        before = self.store.events()
        original = self.executive._journal

        def fail_after_journal(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("simulated crash boundary")

        with patch.object(self.executive, "_journal", side_effect=fail_after_journal):
            with self.assertRaises(RuntimeError):
                self.executive.decision(lease, "D1", "Must rollback")
        self.assertIsNone(self.executive.get_decision("D1"))
        self.assertEqual(self.store.events(), before)
        self.executive.decision(lease, "D1", "Can retry after rollback")


if __name__ == "__main__":
    unittest.main()
