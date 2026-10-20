"""Adversarial, offline state-machine tests; no project/runtime data required."""

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from unittest.mock import patch

from engineering.org_state import StaleAttempt, StateError, Store


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "org.sqlite"
        self.now = 1000.0
        self.store = Store(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def task(self, task_id="a", **kwargs):
        self.store.add_task(task_id, "engineering", f"Task {task_id}", **kwargs)

    def submit(self, task_id="a", sha="candidate"):
        attempt = self.store.claim(task_id, "worker", 10)
        self.store.submit(attempt["id"], attempt["epoch"], sha, {"tests": ["focused"]})
        return attempt

    def finish(self, task_id="a", sha="candidate"):
        self.submit(task_id, sha)
        self.store.verify(task_id, sha, True, {"checked": True})
        self.store.accept(task_id, sha)
        self.store.integrate(task_id, sha, f"integrated-{sha}")

    def advance_contract(self, name, from_version, to_version, body):
        producer_id = f"contract-producer-{name}-{to_version}"
        self.store.add_task(producer_id, "Producer", "Contract producer", scope=["contract"])
        attempt = self.store.claim(producer_id, "synthetic")
        self.store.submit(attempt["id"], attempt["epoch"], f"candidate-{to_version}", {})
        self.store.verify(producer_id, f"candidate-{to_version}", True, {})
        self.store.accept(producer_id, f"candidate-{to_version}")
        self.store.integrate(producer_id, f"candidate-{to_version}", f"merge-{to_version}")
        transition = self.store.propose_contract_transition(
            name,
            from_version,
            to_version,
            body,
            producer_task_id=producer_id,
            producer_team="Producer",
            consumer_teams=["Consumer"],
        )
        digest = transition["body"]["contract_hash"]
        self.store.acknowledge_contract_transition(name, to_version, "Producer", digest)
        self.store.acknowledge_contract_transition(name, to_version, "Consumer", digest)
        self.store.activate_contract_transition(name, to_version)

    def test_sqlite_durability_settings(self):
        self.assertEqual(self.store.db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.store.db.execute("PRAGMA synchronous").fetchone()[0], 2)
        self.assertEqual(self.store.db.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_quiescence_handoff_rechecks_snapshot_atomically_and_is_idempotent(self):
        snapshot = self.store.quiescence_snapshot()
        body = {
            "owner": "Sol",
            "status": "requested",
            "reason": "catalogue-exhausted",
            "resource_reason": "synthetic",
        }

        def fingerprint(current_snapshot):
            identity = {
                "snapshot": current_snapshot,
                "resource_reason": body["resource_reason"],
                "reason": body["reason"],
            }
            return hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()

        self.task("changed")
        stale_digest = fingerprint(snapshot)
        with self.assertRaisesRegex(StateError, "fingerprint mismatch"):
            self.store.record_quiescence_handoff_if_current(
                "0" * 64, snapshot, {**body, "fingerprint": "0" * 64}
            )
        self.assertFalse(
            self.store.record_quiescence_handoff_if_current(
                stale_digest, snapshot, {**body, "fingerprint": stale_digest}
            )
        )
        current = self.store.quiescence_snapshot()
        digest = fingerprint(current)
        exact_body = {**body, "fingerprint": digest}
        self.assertTrue(
            self.store.record_quiescence_handoff_if_current(digest, current, exact_body)
        )
        events = len(self.store.events())
        self.assertTrue(
            self.store.record_quiescence_handoff_if_current(digest, current, exact_body)
        )
        self.assertEqual(len(self.store.events()), events)
        with self.assertRaisesRegex(StateError, "decision_namespace_reserved"):
            self.store.put_decision("sol-handoff/" + digest, exact_body)

    def test_quiescence_handoff_rechecks_snapshot_after_second_connection_lock_wait(self):
        snapshot = self.store.quiescence_snapshot()
        body = {
            "owner": "Sol",
            "status": "requested",
            "reason": "catalogue-exhausted",
            "resource_reason": "synthetic",
        }

        def fingerprint(current_snapshot):
            identity = {
                "snapshot": current_snapshot,
                "resource_reason": body["resource_reason"],
                "reason": body["reason"],
            }
            return hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()

        stale_digest = fingerprint(snapshot)
        other = type(self.store)(self.path, clock=lambda: self.now)
        waiting_for_write_lock = threading.Event()
        self.store.db.set_trace_callback(
            lambda statement: (
                waiting_for_write_lock.set()
                if statement.strip().upper() == "BEGIN IMMEDIATE"
                else None
            )
        )
        other.db.execute("BEGIN IMMEDIATE")
        try:
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(
                    self.store.record_quiescence_handoff_if_current,
                    stale_digest,
                    snapshot,
                    {**body, "fingerprint": stale_digest},
                )
                try:
                    self.assertTrue(waiting_for_write_lock.wait(2))
                    with self.assertRaises(FutureTimeout):
                        future.result(timeout=0.05)

                    # The second connection changes readiness while the Store call is
                    # waiting for this transaction's write lock.
                    other.db.execute(
                        """INSERT INTO tasks(
                          id,team,title,brief,scope,acceptance,dependencies,contracts,
                          base_sha,max_attempts
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (
                            "arrived-during-lock-wait",
                            "engineering",
                            "New ready work",
                            "Synthetic task",
                            "[]",
                            "[]",
                            "[]",
                            "{}",
                            "",
                            3,
                        ),
                    )
                    other.db.execute("COMMIT")
                    self.assertFalse(future.result(timeout=2))
                finally:
                    # Unblock a waiting Store call before executor shutdown on every
                    # assertion or SQLite error path.
                    if other.db.in_transaction:
                        other.db.execute("ROLLBACK")
                    if not future.done():
                        future.result(timeout=2)

            self.assertIsNone(self.store.get_decision("sol-handoff/" + stale_digest))
            fresh_snapshot = self.store.quiescence_snapshot()
            fresh_digest = fingerprint(fresh_snapshot)
            exact_body = {**body, "fingerprint": fresh_digest}
            self.assertTrue(
                self.store.record_quiescence_handoff_if_current(
                    fresh_digest, fresh_snapshot, exact_body
                )
            )
            self.assertIsNotNone(self.store.get_decision("sol-handoff/" + fresh_digest))
        finally:
            self.store.db.set_trace_callback(None)
            if other.db.in_transaction:
                other.db.execute("ROLLBACK")
            other.close()

    def test_active_task_projection_is_bounded_and_preserves_total_state_counts(self):
        self.task("completed")
        self.finish("completed")
        for index in range(105):
            self.task(f"pending-{index:03d}")

        projection = self.store.active_task_projection(limit=100)
        self.assertEqual(len(projection["tasks"]), 100)
        self.assertEqual(projection["total_count"], 106)
        self.assertEqual(projection["active_count"], 105)
        self.assertEqual(projection["omitted_active_count"], 5)
        self.assertEqual(projection["state_counts"], {"integrated": 1, "pending": 105})
        self.assertEqual(len(self.store.list_tasks()), 106)
        with self.assertRaises(StateError):
            self.store.active_task_projection(True)

    def test_active_task_projection_keeps_unknown_state_visible_as_unclassified(self):
        self.task("corrupt")
        self.store.db.execute("UPDATE tasks SET state='unknown-state' WHERE id='corrupt'")
        projection = self.store.active_task_projection()
        self.assertEqual(projection["tasks"], [])
        self.assertEqual(projection["active_count"], 0)
        self.assertEqual(projection["unclassified_count"], 1)
        self.assertEqual(projection["state_counts"], {"unclassified": 1})

    def test_claim_is_atomic_across_connections(self):
        self.task()
        other = Store(self.path, clock=lambda: self.now)
        try:
            with ThreadPoolExecutor(2) as pool:
                results = list(
                    pool.map(lambda store: store.claim("a", "worker"), [self.store, other])
                )
            self.assertEqual(sum(result is not None for result in results), 1)
            self.assertEqual(len(self.store.list_attempts()), 1)
            self.assertEqual(
                len([e for e in self.store.events() if e["kind"] == "attempt_claimed"]), 1
            )
        finally:
            other.close()

    def test_expiry_at_exact_deadline_fences_old_result(self):
        self.task()
        first = self.store.claim("a", "one", 10)
        self.now += 10
        with self.assertRaises(StaleAttempt):
            self.store.submit(first["id"], first["epoch"], "old", {})
        with self.assertRaises(StaleAttempt):
            self.store.renew(first["id"], first["epoch"])
        second = self.store.claim("a", "two", 10)
        self.assertEqual(second["epoch"], 2)
        for operation in (
            lambda: self.store.fail(first["id"], first["epoch"], "late"),
            lambda: self.store.record_usage(first["id"], first["epoch"], {"tokens": 10}),
            lambda: self.store.record_session(first["id"], first["epoch"], "thread", "model"),
        ):
            with self.assertRaises(StaleAttempt):
                operation()
        self.assertEqual(self.store.get_task("a")["active_attempt"], second["id"])

    def test_forged_epoch_and_cross_task_fences_rejected(self):
        self.task("a")
        self.task("b")
        attempt = self.store.claim("a", "one")
        self.store.claim("b", "two")
        with self.assertRaises(StaleAttempt):
            self.store.submit(attempt["id"], attempt["epoch"] + 1, "bad", {})
        self.assertEqual(self.store.get_task("b")["state"], "running")

    def test_controller_fault_circuit_is_fenced_atomic_and_version_bound(self):
        self.task(max_attempts=2)
        attempt = self.store.claim("a", "worker")
        other = Store(self.path, clock=lambda: self.now)
        try:
            with self.assertRaises(StaleAttempt):
                other.open_controller_parse_fault(
                    attempt["id"] + 100,
                    attempt["epoch"],
                    stage="lead",
                    exception_type="ValueError",
                )
            self.assertIsNone(self.store.get_decision("circuit/controller-parse"))
        finally:
            other.close()

        self.store.db.execute(
            """CREATE TRIGGER fail_recovery_obligation BEFORE INSERT ON decisions
               WHEN NEW.name='recovery-obligation/controller-parse'
               BEGIN SELECT RAISE(ABORT, 'injected obligation failure'); END"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.open_controller_parse_fault(
                attempt["id"], attempt["epoch"], stage="lead", exception_type="ValueError"
            )
        self.assertIsNone(self.store.get_decision("circuit/controller-parse"))
        self.assertEqual(self.store.get_task("a")["state"], "running")
        self.store.db.execute("DROP TRIGGER fail_recovery_obligation")

        self.store.open_controller_parse_fault(
            attempt["id"], attempt["epoch"], stage="lead", exception_type="ValueError"
        )
        circuit = self.store.get_decision("circuit/controller-parse")
        obligation = self.store.get_decision("recovery-obligation/controller-parse")
        self.assertEqual(circuit["body"]["status"], "open")
        self.assertEqual(obligation["body"]["status"], "open")
        self.assertEqual(self.store.get_task("a")["state"], "failed")
        self.assertEqual(self.store.list_attempts()[0]["state"], "failed")

        with self.assertRaises(StateError):
            self.store.resolve_controller_parse_fault(
                expected_circuit_version=circuit["version"] + 1,
                expected_obligation_version=obligation["version"],
                recovery_id="recovery-1",
                fix_sha256="a" * 64,
                review_ref="sol-review",
                authorization_ref="founder-auth",
            )
        self.store.resolve_controller_parse_fault(
            expected_circuit_version=circuit["version"],
            expected_obligation_version=obligation["version"],
            recovery_id="recovery-1",
            fix_sha256="a" * 64,
            review_ref="sol-review",
            authorization_ref="founder-auth",
        )
        self.assertEqual(
            self.store.get_decision("circuit/controller-parse")["body"]["status"], "resolved"
        )
        self.assertEqual(
            self.store.get_decision("recovery-obligation/controller-parse")["body"]["status"],
            "resolved",
        )

    def test_recovery_is_durable_idempotent_and_bounded(self):
        self.task(max_attempts=2)
        first = self.store.claim("a", "worker", 1)
        self.now += 2
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertEqual(self.store.recover_expired(), [first["id"]])
        self.assertEqual(self.store.recover_expired(), [])
        self.store.claim("a", "worker", 1)
        self.now += 2
        self.store.recover_expired()
        self.assertEqual(self.store.get_task("a")["state"], "failed")
        self.assertIsNone(self.store.claim("a", "worker"))
        self.assertEqual(len(self.store.list_attempts()), 2)

    def test_lease_renewal_does_not_change_fence(self):
        self.task()
        attempt = self.store.claim("a", "worker", 10)
        self.now += 8
        self.assertEqual(self.store.renew(attempt["id"], attempt["epoch"], 20), 1028)
        self.now += 5
        self.assertEqual(self.store.recover_expired(), [])
        self.store.submit(attempt["id"], attempt["epoch"], "result", {})

    def test_dependencies_require_integration_not_just_acceptance(self):
        self.task("producer")
        self.task("consumer", dependencies=["producer"])
        self.task("independent")
        self.assertEqual({t["id"] for t in self.store.ready_tasks()}, {"producer", "independent"})
        self.submit("producer")
        self.store.verify("producer", "candidate", True, {})
        self.store.accept("producer", "candidate")
        self.assertIsNone(self.store.claim("consumer", "worker"))
        self.assertIsNotNone(self.store.claim("independent", "worker"))
        self.store.integrate("producer", "candidate", "merged-sha")
        self.assertIsNotNone(self.store.claim("consumer", "worker"))

    def test_readiness_report_names_dependency_and_contract_blockers_without_suppressing_ready_work(
        self,
    ):
        self.task("producer")
        self.task("blocked", dependencies=["producer"], contracts={"api": 2})
        self.task("independent")

        report = {item["task_id"]: item for item in self.store.readiness_report()}
        self.assertEqual(
            report["blocked"]["blockers"],
            [
                {"kind": "dependency", "id": "producer", "state": "pending"},
                {
                    "kind": "contract",
                    "name": "api",
                    "required_version": 2,
                    "current_version": None,
                },
            ],
        )
        self.assertFalse(report["blocked"]["eligible"])
        self.assertTrue(report["producer"]["eligible"])
        self.assertTrue(report["independent"]["eligible"])
        self.assertEqual(
            {task["id"] for task in self.store.ready_tasks()}, {"producer", "independent"}
        )
        self.assertIsNotNone(self.store.claim("independent", "worker"))

    def test_contract_requirements_block_until_exact_current_version(self):
        self.task(contracts={"api": 2})
        self.assertEqual(self.store.ready_tasks(), [])
        self.store.put_contract("api", 1, {"field": "old"})
        self.assertIsNone(self.store.claim("a", "worker"))
        self.advance_contract("api", 1, 2, {"field": "current"})
        self.assertEqual([t["id"] for t in self.store.ready_tasks()], ["a"])
        self.advance_contract("api", 2, 3, {"field": "new"})
        self.assertIsNone(self.store.claim("a", "worker"))
        self.assertEqual(self.store.get_contract("api", 1)["body"], {"field": "old"})
        self.assertEqual(self.store.get_contract("api")["version"], 3)
        with self.assertRaises(StateError):
            self.store.put_contract("api", 2, {"overwritten": True})

    def test_contract_change_during_execution_rejects_submission(self):
        self.store.put_contract("api", 1, {})
        self.task(contracts={"api": 1})
        attempt = self.store.claim("a", "worker")
        self.advance_contract("api", 1, 2, {})
        with self.assertRaises(StateError):
            self.store.submit(attempt["id"], attempt["epoch"], "stale", {})
        self.assertEqual(self.store.get_task("a")["state"], "running")
        self.store.fail(attempt["id"], attempt["epoch"], "contract changed")
        self.assertEqual(self.store.ready_tasks(), [])

    def test_contract_change_after_verification_blocks_acceptance(self):
        self.store.put_contract("api", 1, {})
        self.task(contracts={"api": 1})
        self.submit()
        self.store.verify("a", "candidate", True, {})
        self.advance_contract("api", 1, 2, {})
        with self.assertRaises(StateError):
            self.store.accept("a", "candidate")
        self.assertEqual(self.store.get_task("a")["state"], "verified")

    def test_accept_requires_verification_and_is_unique(self):
        self.task()
        self.submit()
        with self.assertRaises(StateError):
            self.store.accept("a", "candidate")
        with self.assertRaises(StaleAttempt):
            self.store.verify("a", "different-sha", True, {})
        self.store.verify("a", "candidate", True, {"tests": "passed"})
        self.store.accept("a", "candidate")
        with self.assertRaises(StateError):
            self.store.accept("a", "candidate")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM acceptances").fetchone()[0], 1)

    def test_rejected_verification_retries_and_old_candidate_cannot_be_accepted(self):
        self.task(max_attempts=2)
        first = self.submit(sha="old")
        self.store.verify("a", "old", False, {"reason": "test failure"})
        second = self.submit(sha="new")
        self.assertGreater(second["epoch"], first["epoch"])
        with self.assertRaises(StaleAttempt):
            self.store.accept("a", "old")
        self.store.verify("a", "new", False, {})
        self.assertEqual(self.store.get_task("a")["state"], "failed")
        self.assertIsNone(self.store.claim("a", "worker"))

    def test_fail_retry_and_permanent_failure(self):
        self.task()
        first = self.store.claim("a", "worker")
        self.store.fail(first["id"], first["epoch"], "transient")
        second = self.store.claim("a", "worker")
        self.store.fail(second["id"], second["epoch"], "invalid brief", retryable=False)
        self.assertEqual(self.store.get_task("a")["state"], "failed")
        self.assertIsNone(self.store.claim("a", "worker"))

    def test_submitted_candidate_survives_deadline_and_reopen(self):
        self.task()
        self.submit()
        self.now += 1000
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertEqual(self.store.recover_expired(), [])
        self.store.verify("a", "candidate", True, {})
        self.store.accept("a", "candidate")
        self.store.integrate("a", "candidate", "merge", {"smoke": "passed"})
        self.assertEqual(self.store.get_task("a")["state"], "integrated")

    def test_session_persists_and_cannot_switch_identity(self):
        self.task()
        attempt = self.store.claim("a", "worker")
        self.store.record_session(attempt["id"], attempt["epoch"], "thread-1", "model", {"pid": 1})
        with self.assertRaises(StateError):
            self.store.record_session(attempt["id"], attempt["epoch"], "thread-2", "model")
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertEqual(self.store.list_attempts("a")[0]["session"]["thread_id"], "thread-1")

    def test_stage_sessions_preserve_lead_worker_history_and_duplicate_is_noop(self):
        self.task()
        attempt = self.store.claim("a", "worker")
        args = attempt["id"], attempt["epoch"]
        self.store.record_stage_session(*args, "lead-thread", "sol", {"pid": 1}, stage="lead")
        self.store.record_stage_session(*args, "worker-thread", "luna", {"pid": 2}, stage="worker")
        before = self.store.events()
        # Replaying an old stage must not move latest recovery pointer backwards.
        self.store.record_stage_session(*args, "lead-thread", "sol", {"pid": 1}, stage="lead")
        self.assertEqual(self.store.events(), before)
        with self.assertRaises(StateError):
            self.store.record_stage_session(*args, "different", "sol", stage="lead")
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        latest = self.store.list_attempts("a")[0]["session"]
        self.assertEqual(latest["thread_id"], "worker-thread")
        self.assertEqual(latest["stage"], "worker")
        history = [
            event["body"]["session"]
            for event in self.store.events()
            if event["kind"] == "session_stage_recorded"
        ]
        self.assertEqual([item["stage"] for item in history], ["lead", "worker"])

    def test_decision_versions_and_replacement_persist(self):
        self.assertEqual(self.store.put_decision("old", {"choice": "A"}), 1)
        self.store.put_decision("new", {"choice": "B"}, replaces="old")
        self.store.put_decision("new", {"choice": "C"})
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertEqual(self.store.get_decision("new", 1)["replaces"], "old")
        self.assertEqual(self.store.get_decision("new", 1)["replaces_version"], 1)
        self.assertEqual(self.store.get_decision("new")["replaces_version"], 1)
        self.assertEqual(self.store.get_decision("new")["replaces"], "new")
        self.store.put_decision("old", {"choice": "later"})
        self.assertEqual(self.store.get_decision("new", 1)["replaces_version"], 1)
        self.assertEqual(self.store.get_decision("new", 1)["body"], {"choice": "B"})
        self.assertEqual(self.store.get_decision("new")["body"], {"choice": "C"})
        with self.assertRaises(StateError):
            self.store.put_decision("bad", {}, replaces="missing")

    def test_usage_includes_failed_attempt_and_rejects_invalid_measurements(self):
        self.task()
        first = self.store.claim("a", "worker")
        self.store.record_usage(first["id"], first["epoch"], {"tokens": 10, "seconds": 2.5})
        self.store.fail(first["id"], first["epoch"], "crash")
        second = self.store.claim("a", "worker")
        self.store.record_usage(second["id"], second["epoch"], {"tokens": 5})
        for bad in (-1, float("nan"), float("inf"), True, "unknown"):
            with self.assertRaises(StateError):
                self.store.record_usage(second["id"], second["epoch"], {"tokens": bad})
        summary = self.store.usage_summary()
        self.assertEqual(summary["total"], {"tokens": 15, "seconds": 2.5})
        self.assertEqual(summary["teams"]["engineering"], summary["total"])

    def test_mutation_and_journal_rollback_together(self):
        self.task()
        attempt = self.store.claim("a", "worker")
        before = self.store.events()
        with self.assertRaises(ValueError):
            self.store.submit(attempt["id"], attempt["epoch"], "bad", {"nan": float("nan")})
        self.assertEqual(self.store.events(), before)
        self.assertEqual(self.store.get_task("a")["state"], "running")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 0)
        self.store.submit(attempt["id"], attempt["epoch"], "good", {})

    def test_graph_and_objective_constraints_rollback_cleanly(self):
        with self.assertRaises(StateError):
            self.task(dependencies=["missing"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.task(objective_id="missing")
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(self.store.events(), [])
        self.store.add_objective("goal", "Goal", "Scope")
        self.task(objective_id="goal", scope=["engineering/"], acceptance=["offline checks"])
        with self.assertRaises(sqlite3.IntegrityError):
            self.task()
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_event_resume_cursor_and_detached_snapshots(self):
        self.task(scope=["one"])
        task = self.store.get_task("a")
        task["scope"].append("other")
        self.assertEqual(self.store.get_task("a")["scope"], ["one"])
        cursor = self.store.events()[-1]["seq"]
        self.store.claim("a", "worker")
        events = self.store.events(after=cursor)
        self.assertEqual([event["kind"] for event in events], ["attempt_claimed"])
        self.assertGreater(events[0]["seq"], cursor)

    def test_process_crash_between_mutation_and_journal_is_rolled_back(self):
        self.task("durable")
        code = """
import os, sys
from engineering.org_state import Store
store = Store(sys.argv[1])
store._event = lambda *args: os._exit(19)
store.add_task('uncommitted', 'engineering', 'Must rollback')
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.path)],
            cwd=Path(__file__).resolve().parents[2],
            check=False,
        )
        self.assertEqual(result.returncode, 19)
        self.assertEqual([task["id"] for task in self.store.list_tasks()], ["durable"])
        self.assertEqual([event["entity"] for event in self.store.events()], ["durable"])
        self.assertIsNotNone(self.store.claim("durable", "worker"))

    def test_candidate_evidence_is_immutable_and_available_for_resume(self):
        self.task()
        attempt = self.submit(sha="old")
        self.assertEqual(self.store.get_candidate("a")["attempt_id"], attempt["id"])
        self.store.verify("a", "old", False, {})
        self.submit(sha="new")
        self.assertEqual(self.store.get_candidate("a")["sha"], "new")
        self.assertEqual(self.store.get_candidate("a", "old")["evidence"], {"tests": ["focused"]})
        self.assertIsNone(self.store.get_candidate("a", "unknown"))

    def test_bad_lease_lengths_cannot_mutate_state(self):
        self.task()
        for value in (0, -1, float("inf"), float("nan"), True):
            with self.assertRaises(StateError):
                self.store.claim("a", "worker", value)
        self.assertEqual(self.store.list_attempts(), [])

    def test_reviewed_reopen_preserves_failures_usage_fences_and_retry_bound(self):
        self.task(scope=["src/example.py"], acceptance=["frozen check"], max_attempts=3)
        old_attempts = []
        for _ in range(3):
            attempt = self.store.claim("a", "worker")
            old_attempts.append(attempt)
            self.store.record_usage(attempt["id"], attempt["epoch"], {"tokens": 10})
            self.store.fail(attempt["id"], attempt["epoch"], "controller parsing defect")
        before = self.store.events()
        reopened = self.store.reopen_failed(
            "a",
            "parser-fix",
            reason="Qualified parser controller patch",
            evidence_refs=["receipt:parser", "receipt:tool-probe"],
            extra_attempts=3,
        )
        self.assertEqual(
            (reopened["state"], reopened["epoch"], reopened["max_attempts"]), ("pending", 3, 6)
        )
        self.assertEqual(reopened["scope"], ["src/example.py"])
        self.assertEqual(reopened["acceptance"], ["frozen check"])
        self.assertEqual(self.store.events()[: len(before)], before)
        self.assertEqual(self.store.usage_summary()["total"]["tokens"], 30)
        self.assertEqual(len(self.store.list_attempts()), 3)
        after = self.store.events()
        for _ in range(2):
            self.store.reopen_failed(
                "a",
                "parser-fix",
                reason="Qualified parser controller patch",
                evidence_refs=["receipt:parser", "receipt:tool-probe"],
            )
        self.assertEqual(self.store.events(), after)
        for old in old_attempts:
            with self.assertRaises(StaleAttempt):
                self.store.submit(old["id"], old["epoch"], "stale", {})
        for epoch in (4, 5, 6):
            attempt = self.store.claim("a", "worker")
            self.assertEqual(attempt["epoch"], epoch)
            self.store.fail(attempt["id"], epoch, "bounded failure")
        self.assertEqual(self.store.get_task("a")["state"], "failed")
        self.assertIsNone(self.store.claim("a", "worker"))
        self.assertEqual(len(self.store.list_attempts()), 6)

    def test_reopen_requires_failure_evidence_and_immutable_recovery_identity(self):
        self.task(max_attempts=1)
        valid = dict(reason="Qualified controller repair", evidence_refs=["receipt:qualified"])
        with self.assertRaises(StateError):
            self.store.reopen_failed("a", "repair", **valid)
        attempt = self.store.claim("a", "worker")
        self.store.fail(attempt["id"], attempt["epoch"], "broken controller")
        for changes in (
            {"reason": " "},
            {"evidence_refs": []},
            {"evidence_refs": "file"},
            {"extra_attempts": 4},
            {"extra_attempts": True},
            {"extra_attempts": 0},
        ):
            with self.assertRaises(StateError):
                self.store.reopen_failed("a", "repair", **{**valid, **changes})
        self.store.reopen_failed("a", "repair", **valid)
        with self.assertRaises(StateError):
            self.store.reopen_failed("a", "repair", **{**valid, "reason": "different"})
        self.assertEqual(self.store.get_task("a")["max_attempts"], 4)

    def test_reopen_and_recovery_decision_rollback_atomically(self):
        self.task(max_attempts=1)
        attempt = self.store.claim("a", "worker")
        self.store.fail(attempt["id"], attempt["epoch"], "broken controller")
        before = self.store.get_task("a")
        with patch.object(self.store, "_event", side_effect=RuntimeError("journal failure")):
            with self.assertRaises(RuntimeError):
                self.store.reopen_failed(
                    "a", "repair", reason="Repair qualified", evidence_refs=["receipt"]
                )
        self.assertEqual(self.store.get_task("a"), before)
        self.assertIsNone(self.store.get_decision("recovery/repair/a"))

    def test_reopen_preserves_rejected_candidate_but_cannot_accept_it(self):
        self.task(max_attempts=1)
        self.submit(sha="rejected")
        self.store.verify("a", "rejected", False, {"failure": "controller defect"})
        self.store.reopen_failed(
            "a", "repair", reason="Repair qualified", evidence_refs=["receipt"]
        )
        self.assertEqual(self.store.get_candidate("a", "rejected")["sha"], "rejected")
        with self.assertRaises(StaleAttempt):
            self.store.accept("a", "rejected")


if __name__ == "__main__":
    unittest.main()
