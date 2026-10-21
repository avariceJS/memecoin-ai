from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from engineering import org_state
from engineering.lifecycle import plan_lifecycle
from engineering.org_state import StaleAttempt, StateError, Store


def launch_plan(owner: str, epoch: int, digest="a" * 64):
    return {
        "action": "launch_request",
        "reason": "all_gates_clear",
        "retry_at": None,
        "attempt_id": None,
        "request_owner": owner,
        "request_epoch": epoch,
        "decision_sha256": digest,
    }


def launch_observations(**overrides):
    values = {
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
        "now": 1000.0,
        "launch_authorized": True,
    }
    values.update(overrides)
    return values


def hold_plan(**overrides):
    return {
        "action": "hold",
        "reason": "launch_not_authorized",
        "retry_at": None,
        "attempt_id": None,
        "request_owner": None,
        "request_epoch": None,
        "decision_sha256": "b" * 64,
        **overrides,
    }


class LifecycleStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "org.sqlite"
        self.now = 1000.0
        self.store = Store(self.path, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_claim_is_durable_exclusive_and_monotonically_fenced(self):
        lease = self.store.claim_lifecycle_owner("luna", lease_seconds=10)
        self.assertEqual(lease["epoch"], 1)
        self.assertIsNone(self.store.claim_lifecycle_owner("other", lease_seconds=10))

        self.now += 10
        replacement = self.store.claim_lifecycle_owner("other", lease_seconds=10)
        self.assertEqual(replacement["epoch"], 2)
        with self.assertRaisesRegex(StateError, "stale_lifecycle_owner"):
            self.store.record_lifecycle_evaluation(lease, launch_observations())

        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertEqual(
            self.store.get_decision("lifecycle/owner")["body"],
            {
                "status": "active",
                "owner": "other",
                "epoch": 2,
                "lease_until": 1020.0,
            },
        )

    def test_unstarted_claim_does_not_spend_retry_and_expiry_fences_replacement(self):
        self.store.add_task(
            "dispatch-window", "Platform", "Synthetic dispatch window", max_attempts=2
        )
        first = self.store.claim("dispatch-window", "worker", lease_seconds=5)
        self.store.cancel_unstarted_claim(first["id"], first["epoch"], reason="stop_before_spawn")
        task = self.store.get_task("dispatch-window")
        self.assertEqual(task["state"], "pending")
        self.assertEqual(task["epoch"], 1)
        self.assertEqual(
            self.store.list_attempts("dispatch-window")[0]["state"], "cancelled_before_spawn"
        )

        self.now += 5
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        second = self.store.claim("dispatch-window", "worker", lease_seconds=5)
        self.assertEqual(second["epoch"], 2)
        self.now += 5
        with self.assertRaisesRegex(StateError, "unstarted_claim_lease_expired"):
            self.store.cancel_unstarted_claim(
                second["id"], second["epoch"], reason="stop_before_spawn"
            )
        self.assertEqual(self.store.recover_expired(), [second["id"]])
        self.assertEqual(self.store.get_task("dispatch-window")["state"], "pending")
        self.assertIsNone(self.store.claim("dispatch-window", "unreaped-worker", 5))
        self.store.record_attempt_process_reaped(
            second["id"], second["epoch"], [], disposition="no_work_before_custody"
        )
        third = self.store.claim("dispatch-window", "worker", lease_seconds=5)
        self.assertEqual(third["epoch"], 3)
        with self.assertRaisesRegex(StaleAttempt, "superseded attempt fence"):
            self.store.fail(second["id"], second["epoch"], "stale", retryable=True)
        self.store.fail(third["id"], third["epoch"], "synthetic failure", retryable=True)
        self.assertEqual(self.store.get_task("dispatch-window")["state"], "failed")
        attempts = self.store.list_attempts("dispatch-window")
        self.assertEqual(
            [attempt["state"] for attempt in attempts],
            ["cancelled_before_spawn", "expired", "failed"],
        )

    def test_registered_child_has_one_owner_and_STOP_outcome_is_durable(self):
        self.store.add_task("child-owner", "Platform", "Synthetic child custody", max_attempts=2)
        attempt = self.store.claim("child-owner", "supervisor", lease_seconds=20)
        owner = {"pid": 321, "birth": "darwin:321:1000:000001:501"}
        duplicate = {"pid": 322, "birth": "darwin:322:1000:000002:501"}

        self.assertTrue(
            self.store.record_attempt_child_start(attempt["id"], attempt["epoch"], owner)
        )
        self.assertFalse(
            self.store.record_attempt_child_start(attempt["id"], attempt["epoch"], owner)
        )
        with self.assertRaisesRegex(StateError, "attempt_child_already_owned"):
            self.store.record_attempt_child_start(attempt["id"], attempt["epoch"], duplicate)
        with self.assertRaisesRegex(StateError, "attempt_already_started"):
            self.store.cancel_unstarted_claim(
                attempt["id"], attempt["epoch"], reason="stop_before_spawn"
            )

        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertTrue(
            self.store.stop_started_child_before_work(attempt["id"], attempt["epoch"], owner)
        )
        self.assertFalse(
            self.store.stop_started_child_before_work(attempt["id"], attempt["epoch"], owner)
        )
        self.assertEqual(self.store.get_task("child-owner")["state"], "pending")
        stored = self.store.list_attempts("child-owner")[0]
        self.assertEqual(stored["state"], "stopped_before_work")
        self.assertIsNone(stored["session"])
        self.assertEqual(
            self.store.attempt_child_start_identity(attempt["id"], attempt["epoch"]), owner
        )
        events = [event for event in self.store.events() if event["entity"] == "child-owner"]
        self.assertEqual(sum(event["kind"] == "attempt_child_started" for event in events), 1)
        self.assertEqual(sum(event["kind"] == "attempt_stopped_before_work" for event in events), 1)

        next_attempt = self.store.claim("child-owner", "supervisor", lease_seconds=20)
        self.assertEqual(next_attempt["epoch"], 2)
        self.store.fail(next_attempt["id"], next_attempt["epoch"], "synthetic failure")
        self.assertEqual(self.store.get_task("child-owner")["state"], "pending")
        final_attempt = self.store.claim("child-owner", "supervisor", lease_seconds=20)
        self.store.fail(final_attempt["id"], final_attempt["epoch"], "synthetic failure")
        self.assertEqual(self.store.get_task("child-owner")["state"], "failed")

    def test_child_exit_before_or_after_custody_never_refunds_unknown_attempt(self):
        self.store.add_task(
            "before-custody", "Platform", "Crash before child record", max_attempts=2
        )
        before = self.store.claim("before-custody", "supervisor", lease_seconds=5)
        self.store.close()
        crashed = subprocess.run([sys.executable, "-c", "import os; os._exit(23)"])
        self.assertEqual(crashed.returncode, 23)
        self.now += 5
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertEqual(self.store.recover_expired(), [before["id"]])
        self.assertEqual(self.store.list_attempts("before-custody")[0]["state"], "expired")
        self.assertIsNone(self.store.list_attempts("before-custody")[0]["session"])

        self.store.add_task("after-custody", "Platform", "Crash after child record", max_attempts=2)
        after = self.store.claim("after-custody", "supervisor", lease_seconds=5)
        self.store.close()
        child = "\n".join(
            [
                "import os, sys",
                "from engineering.org_state import Store",
                "path, now, attempt, epoch = (",
                "    sys.argv[1], float(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])",
                ")",
                "db = Store(path, clock=lambda: now)",
                "db.record_attempt_child_start(",
                '    attempt, epoch, {"pid": os.getpid(), "birth": f"synthetic:{os.getpid()}"}',
                ")",
                "os._exit(23)",
            ]
        )
        crashed = subprocess.run(
            [
                sys.executable,
                "-c",
                child,
                str(self.path),
                str(self.now),
                str(after["id"]),
                str(after["epoch"]),
            ]
        )
        self.assertEqual(crashed.returncode, 23)
        self.now += 5
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertEqual(self.store.recover_expired(), [after["id"]])
        stored = self.store.list_attempts("after-custody")[0]
        self.assertEqual(stored["state"], "expired")
        self.assertIsNone(stored["session"])
        identity = self.store.attempt_child_start_identity(after["id"], after["epoch"])
        self.assertEqual(identity["birth"], f"synthetic:{identity['pid']}")
        self.assertEqual(self.store.get_task("after-custody")["state"], "pending")
        self.assertIsNone(self.store.claim("after-custody", "unreaped-worker", 5))
        self.store.record_attempt_process_reaped(after["id"], after["epoch"], [identity])
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        replacement = self.store.claim("after-custody", "supervisor", lease_seconds=5)
        self.store.fail(replacement["id"], replacement["epoch"], "synthetic failure")
        self.assertEqual(self.store.get_task("after-custody")["state"], "failed")

    def test_expired_process_recovery_blocks_claim_until_durable_receipt(self):
        self.store.add_task("reap-fence", "Platform", "Await process reap", max_attempts=2)
        self.store.add_task("unrelated", "Research", "Unrelated ready task")
        attempt = self.store.claim("reap-fence", "supervisor", lease_seconds=5)
        owner = {"pid": 511, "birth": "darwin:511:1000:000011:501"}
        self.store.record_attempt_child_start(attempt["id"], attempt["epoch"], owner)
        self.now += 5
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertEqual(self.store.recover_expired(), [attempt["id"]])
        self.assertIsNone(self.store.claim("reap-fence", "direct-claimer", lease_seconds=5))
        self.assertNotIn("reap-fence", [task["id"] for task in self.store.ready_tasks()])
        report = {item["task_id"]: item for item in self.store.readiness_report()}
        self.assertIn(
            {"kind": "attempt_process_recovery", "attempt_ids": [attempt["id"]]},
            report["reap-fence"]["blockers"],
        )
        self.assertIsNotNone(self.store.claim("unrelated", "direct-claimer", lease_seconds=5))
        self.store.record_attempt_process_reaped(attempt["id"], attempt["epoch"], [owner])
        self.store.close()
        self.store = Store(self.path, clock=lambda: self.now)
        replacement = self.store.claim("reap-fence", "after-reaper", lease_seconds=5)
        self.assertEqual(replacement["epoch"], attempt["epoch"] + 1)

    def test_forged_reap_receipt_cannot_invent_a_process_owner(self):
        self.store.add_task("reap-receipt", "Platform", "Validate reap receipt")
        attempt = self.store.claim("reap-receipt", "supervisor", lease_seconds=5)
        self.now += 5
        self.store.recover_expired()
        with self.store._tx():
            self.store._event(
                "attempt_process_reaped",
                "reap-receipt",
                {
                    "attempt_id": attempt["id"],
                    "epoch": attempt["epoch"],
                    "disposition": "processes_reaped",
                    "process_identities": [{"pid": 950001, "birth": "invented:dead"}],
                },
            )
        with self.assertRaisesRegex(StateError, "attempt_process_reap_history_invalid"):
            self.store.claim("reap-receipt", "forged-receipt-claimer", lease_seconds=5)

    def test_reap_receipt_rejects_boolean_attempt_identity_before_write(self):
        self.store.add_task("reap-bool", "Platform", "Reject boolean receipt identity")
        attempt = self.store.claim("reap-bool", "supervisor", lease_seconds=5)
        self.now += 5
        self.store.recover_expired()
        identity = [{"pid": 900003, "birth": "synthetic:reaped"}]
        with self.assertRaisesRegex(StateError, "attempt_process_reap_identity_invalid"):
            self.store.record_attempt_process_reaped(True, True, identity)
        self.assertIsNone(
            self.store.attempt_process_reap_confirmation(attempt["id"], attempt["epoch"])
        )

    def test_legacy_expired_attempt_without_process_identity_stays_held(self):
        self.store.add_task("legacy-reap", "Platform", "Hold unknown process owner")
        attempt = self.store.claim("legacy-reap", "supervisor", lease_seconds=5)
        self.store.record_session(attempt["id"], attempt["epoch"], "thread", "model")
        self.now += 5
        self.store.recover_expired()
        with self.assertRaisesRegex(StateError, "attempt_process_reap_identity_invalid"):
            self.store.record_attempt_process_reaped(attempt["id"], attempt["epoch"], [])
        self.assertIsNone(self.store.claim("legacy-reap", "direct-claimer", lease_seconds=5))

    def test_legacy_no_owner_attempt_cannot_use_new_no_work_disposition(self):
        self.store.add_task("legacy-no-owner", "Platform", "Hold legacy unknown owner")
        attempt = self.store.claim("legacy-no-owner", "supervisor", lease_seconds=5)
        with self.store._tx():
            self.store.db.execute(
                "DELETE FROM events WHERE kind='attempt_custody_required' AND entity=?",
                ("legacy-no-owner",),
            )
        self.now += 5
        self.store.recover_expired()
        with self.assertRaisesRegex(StateError, "attempt_process_reap_no_work_unproven"):
            self.store.record_attempt_process_reaped(
                attempt["id"], attempt["epoch"], [], disposition="no_work_before_custody"
            )
        self.assertIsNone(self.store.claim("legacy-no-owner", "direct-claimer", lease_seconds=5))

    def test_started_child_stop_cannot_erase_work_evidence(self):
        for evidence in ("stage", "dispatch", "usage", "candidate"):
            with self.subTest(evidence=evidence):
                task_id = "child-work-" + evidence
                self.store.add_task(task_id, "Platform", "Work evidence fence")
                attempt = self.store.claim(task_id, "supervisor")
                owner = {"pid": 410, "birth": "darwin:410:1000:000010:501"}
                self.store.record_attempt_child_start(attempt["id"], attempt["epoch"], owner)
                if evidence == "stage":
                    self.store.record_stage_session(
                        attempt["id"],
                        attempt["epoch"],
                        "lead-thread",
                        "Sol",
                        owner,
                        stage="lead",
                    )
                elif evidence == "dispatch":
                    self.store.record_attempt_work_start(
                        attempt["id"], attempt["epoch"], owner, stage="lead"
                    )
                elif evidence == "usage":
                    self.store.record_usage(attempt["id"], attempt["epoch"], {"tokens": 1})
                else:
                    self.store.submit(attempt["id"], attempt["epoch"], "candidate", {})
                with self.assertRaises(StateError):
                    self.store.stop_started_child_before_work(
                        attempt["id"], attempt["epoch"], owner
                    )
                self.assertNotEqual(self.store.get_task(task_id)["state"], "pending")

    def test_unstarted_claim_cancellation_rejects_custodied_execution_evidence(self):
        for evidence in ("session", "usage", "candidate"):
            task_id = "started-" + evidence
            self.store.add_task(task_id, "Platform", "Started synthetic attempt")
            attempt = self.store.claim(task_id, "worker")
            if evidence == "session":
                self.store.record_session(attempt["id"], attempt["epoch"], "thread", "model")
            elif evidence == "usage":
                self.store.record_usage(attempt["id"], attempt["epoch"], {"tokens": 1})
            else:
                self.store.submit(attempt["id"], attempt["epoch"], "candidate-sha", {})
            with self.assertRaisesRegex(StateError, "attempt_already_started"):
                self.store.cancel_unstarted_claim(
                    attempt["id"], attempt["epoch"], reason="stop_before_spawn"
                )
            self.assertEqual(
                self.store.get_task(task_id)["state"],
                "submitted" if evidence == "candidate" else "running",
            )

    def test_current_owner_can_renew_and_release_but_cannot_revive_expired_epoch(self):
        lease = self.store.claim_lifecycle_owner("owner", lease_seconds=5)
        renewed = self.store.renew_lifecycle_owner(lease, lease_seconds=20)
        self.assertEqual(renewed["epoch"], lease["epoch"])
        self.assertGreater(renewed["version"], lease["version"])

        self.store.release_lifecycle_owner(lease)
        next_lease = self.store.claim_lifecycle_owner("owner", lease_seconds=5)
        self.assertEqual(next_lease["epoch"], lease["epoch"] + 1)
        with self.assertRaisesRegex(StateError, "stale_lifecycle_owner"):
            self.store.renew_lifecycle_owner(lease)

    def test_passive_gate_snapshot_freshness_is_checked_after_sqlite_lock_wait(self):
        root = Path(self.temp.name) / "restored"
        root.mkdir()
        (root / "STOP").touch()
        manifest = b'{"schema":"synthetic-snapshot"}\n'
        (root / "snapshot.json").write_bytes(manifest)
        receipt = {
            "schema": "brain-organization-restore-v1",
            "snapshot_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
            "restored_path": str(root),
            "stop_preserved": True,
            "structural_deployment_validation": True,
            "runtime_ready": False,
            "host_requalification_required": True,
        }
        (root / "restore-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        lease = self.store.claim_lifecycle_owner("snapshot-test", lease_seconds=60)
        snapshot = self.store.capture_passive_lifecycle_gate_snapshot(root)
        blocker = Store(self.path, clock=lambda: self.now)
        blocker.db.execute("BEGIN IMMEDIATE")
        entered = threading.Event()

        def record():
            entered.set()
            return self.store.record_passive_lifecycle_hold(lease, root, snapshot)

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(record)
                self.assertTrue(entered.wait(1))
                time.sleep(0.05)
                self.assertFalse(future.done())
                self.now += 31
                blocker.db.execute("COMMIT")
                with self.assertRaisesRegex(StateError, "lifecycle_gate_snapshot_stale_or_invalid"):
                    future.result(timeout=5)
        finally:
            if blocker.db.in_transaction:
                blocker.db.execute("ROLLBACK")
            blocker.close()

    def test_worklist_rejects_self_consistent_but_malformed_source_snapshot(self):
        from engineering.lifecycle import plan_lifecycle

        lease = self.store.claim_lifecycle_owner("snapshot-audit", lease_seconds=60)
        snapshot = {
            "schema": "passive-lifecycle-gates-v1",
            "captured_at": self.now,
            "capture_sequence": 1,
            "stop": {"present": True, "sha256": None},
            "restore_receipt_sha256": "a" * 64,
            "runtime_ready": False,
            "host_requalification_required": True,
            "controller_parse": "clear",
        }
        snapshot["snapshot_sha256"] = hashlib.sha256(
            json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        observations = launch_observations(
            stop_reasons=["deployment_stop"],
            runtime_ready=False,
            accounting_ready=False,
            quota_ready=False,
            process_state="unknown",
            recovery_open=True,
            launch_authorized=False,
        )
        bound = {
            **observations,
            "request_owner": lease["owner"],
            "request_epoch": lease["epoch"],
            "source_snapshot_sha256": snapshot["snapshot_sha256"],
        }
        computed = plan_lifecycle(**bound)
        plan = {
            "action": computed.action,
            "reason": computed.reason,
            "retry_at": computed.retry_at,
            "attempt_id": computed.attempt_id,
            "request_owner": computed.request_owner,
            "request_epoch": computed.request_epoch,
            "decision_sha256": computed.decision_sha256,
        }
        with self.store._tx():
            owner = self.store._lifecycle_fence(lease)
            self.store._persist_lifecycle_evaluation(owner, plan, bound, source_snapshot=snapshot)
        with self.assertRaisesRegex(StateError, "lifecycle_request_invalid"):
            self.store.lifecycle_request_worklist()

    def test_worklist_rejects_source_snapshot_without_planner_hash_link(self):
        from engineering.lifecycle import plan_lifecycle

        root = Path(self.temp.name) / "restored-linked"
        root.mkdir()
        (root / "STOP").touch()
        manifest = b'{"schema":"synthetic-snapshot"}\n'
        (root / "snapshot.json").write_bytes(manifest)
        receipt = {
            "schema": "brain-organization-restore-v1",
            "snapshot_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
            "restored_path": str(root),
            "stop_preserved": True,
            "structural_deployment_validation": True,
            "runtime_ready": False,
            "host_requalification_required": True,
        }
        (root / "restore-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        lease = self.store.claim_lifecycle_owner("snapshot-link", lease_seconds=60)
        snapshot = self.store.capture_passive_lifecycle_gate_snapshot(root)
        observations = launch_observations(
            stop_reasons=["deployment_stop"],
            runtime_ready=False,
            accounting_ready=False,
            quota_ready=False,
            process_state="unknown",
            recovery_open=True,
            launch_authorized=False,
        )
        bound = {
            **observations,
            "request_owner": lease["owner"],
            "request_epoch": lease["epoch"],
        }
        computed = plan_lifecycle(**bound)
        plan = {
            "action": computed.action,
            "reason": computed.reason,
            "retry_at": computed.retry_at,
            "attempt_id": computed.attempt_id,
            "request_owner": computed.request_owner,
            "request_epoch": computed.request_epoch,
            "decision_sha256": computed.decision_sha256,
        }
        with self.store._tx():
            owner = self.store._lifecycle_fence(lease)
            self.store._persist_lifecycle_evaluation(owner, plan, bound, source_snapshot=snapshot)
        with self.assertRaisesRegex(StateError, "lifecycle_request_invalid"):
            self.store.lifecycle_request_worklist()

    def test_lifecycle_source_reader_rejects_fifo_swapped_after_lstat(self):
        path = Path(self.temp.name) / "source"
        path.write_bytes(b"regular")
        real_open = org_state.os.open

        def swap_to_fifo(target, flags, *args, **kwargs):
            if Path(target) == path:
                path.unlink()
                org_state.os.mkfifo(path)
            return real_open(target, flags, *args, **kwargs)

        with patch.object(org_state.os, "open", side_effect=swap_to_fifo):
            with self.assertRaisesRegex(StateError, "lifecycle_source_not_regular"):
                self.store._lifecycle_file_source(path, optional=False)

    def test_owner_history_head_validation_uses_constant_memory_summary(self):
        lease = self.store.claim_lifecycle_owner("owner", lease_seconds=10000)
        for _ in range(20):
            lease = self.store.renew_lifecycle_owner(lease, lease_seconds=10000)
        self.assertEqual(lease["version"], 21)

        statements = []
        self.store.db.set_trace_callback(statements.append)
        try:
            head = self.store._lifecycle_owner_history_head()
            projection = self.store.lifecycle_request_worklist()
        finally:
            self.store.db.set_trace_callback(None)

        self.assertEqual(head, 21)
        self.assertEqual(projection["owner"]["owner"], "owner")
        self.assertTrue(any("COUNT(*) AS version_count" in sql for sql in statements))
        self.assertFalse(
            any(
                "SELECT version FROM decisions WHERE name='lifecycle/owner'" in sql
                for sql in statements
            )
        )

        plan = self.store.db.execute(
            "EXPLAIN QUERY PLAN SELECT COUNT(*),"
            "MAX(length(CAST(body AS BLOB))) FROM events "
            "WHERE kind='decision_recorded' AND entity='lifecycle/owner'"
        ).fetchall()
        self.assertTrue(any("idx_events_lifecycle_owner_decision" in row["detail"] for row in plan))

    def test_owner_history_head_detects_gaps(self):
        lease = self.store.claim_lifecycle_owner("owner", lease_seconds=10000)
        lease = self.store.renew_lifecycle_owner(lease, lease_seconds=10000)
        self.store.renew_lifecycle_owner(lease, lease_seconds=10000)
        self.store.db.execute("DELETE FROM decisions WHERE name='lifecycle/owner' AND version=2")

        with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
            self.store.claim_lifecycle_owner("replacement")

    def test_owner_history_rejects_malformed_root_link_before_reclaim(self):
        lease = self.store.claim_lifecycle_owner("owner")
        self.store.release_lifecycle_owner(lease)
        self.store.db.execute(
            "UPDATE decisions SET replaces=CAST(X'80' AS TEXT) "
            "WHERE name='lifecycle/owner' AND version=1"
        )

        with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
            self.store.claim_lifecycle_owner("replacement")

        self.assertEqual(
            self.store.db.execute(
                "SELECT MAX(version) FROM decisions WHERE name='lifecycle/owner'"
            ).fetchone()[0],
            2,
        )

    def test_owner_history_rejects_malformed_latest_link_before_decode(self):
        lease = self.store.claim_lifecycle_owner("owner")
        self.store.release_lifecycle_owner(lease)
        self.store.db.execute(
            "UPDATE decisions SET replaces=CAST(X'80' AS TEXT) "
            "WHERE name='lifecycle/owner' AND version=2"
        )

        with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
            self.store.claim_lifecycle_owner("replacement")

        self.assertEqual(
            self.store.db.execute(
                "SELECT MAX(version) FROM decisions WHERE name='lifecycle/owner'"
            ).fetchone()[0],
            2,
        )

    def test_owner_history_rejects_dangling_replaces_version_before_reclaim(self):
        lease = self.store.claim_lifecycle_owner("owner")
        self.store.release_lifecycle_owner(lease)
        self.store.db.execute(
            "UPDATE decisions SET replaces_version=9 WHERE name='lifecycle/owner' AND version=2"
        )

        with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
            self.store.claim_lifecycle_owner("replacement")

        self.assertEqual(
            self.store.db.execute(
                "SELECT MAX(version) FROM decisions WHERE name='lifecycle/owner'"
            ).fetchone()[0],
            2,
        )

    def test_owner_history_rejects_invalid_intermediate_owner_transition(self):
        lease = self.store.claim_lifecycle_owner("owner", lease_seconds=100)
        self.store.release_lifecycle_owner(lease)
        self.store.claim_lifecycle_owner("replacement", lease_seconds=100)
        self.store.db.execute(
            "UPDATE decisions SET body=? WHERE name='lifecycle/owner' AND version=2",
            (
                json.dumps(
                    {
                        "status": "active",
                        "owner": "intruder",
                        "epoch": 1,
                        "lease_until": 1100.0,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )

        with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
            self.store.claim_lifecycle_owner("next")

        self.assertEqual(
            self.store.db.execute(
                "SELECT MAX(version) FROM decisions WHERE name='lifecycle/owner'"
            ).fetchone()[0],
            3,
        )

    def test_oversized_owner_history_fails_before_json_hydration(self):
        self.store.claim_lifecycle_owner("owner")
        self.store.db.execute(
            "UPDATE decisions SET body=? WHERE name='lifecycle/owner' AND version=1",
            (json.dumps({"oversized": "x" * org_state._LIFECYCLE_OWNER_RECORD_MAX_BYTES}),),
        )

        with patch("engineering.org_state.json.loads", side_effect=AssertionError("body hydrated")):
            with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
                self.store._lifecycle_owner_history_head()

    def test_owner_history_requires_matching_decision_events_before_mutation(self):
        lease = self.store.claim_lifecycle_owner("owner")
        owner_event = self.store.db.execute(
            "SELECT seq,body FROM events "
            "WHERE kind='decision_recorded' AND entity='lifecycle/owner'"
        ).fetchone()
        event_body = json.loads(owner_event["body"])
        event_body["body"]["owner"] = "tampered"
        self.store.db.execute(
            "UPDATE events SET body=? WHERE seq=?",
            (json.dumps(event_body, sort_keys=True, separators=(",", ":")), owner_event["seq"]),
        )

        with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
            self.store.renew_lifecycle_owner(lease)

        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM decisions WHERE name='lifecycle/owner'"
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM events "
                "WHERE kind='decision_recorded' AND entity='lifecycle/owner'"
            ).fetchone()[0],
            1,
        )

    def test_missing_owner_decision_event_fails_closed(self):
        self.store.claim_lifecycle_owner("owner")
        self.store.db.execute(
            "DELETE FROM events WHERE kind='decision_recorded' AND entity='lifecycle/owner'"
        )

        with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
            self.store.claim_lifecycle_owner("replacement")

        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM decisions WHERE name='lifecycle/owner'"
            ).fetchone()[0],
            1,
        )

    def test_orphan_owner_decision_event_fails_closed_without_history(self):
        self.store.claim_lifecycle_owner("owner")
        self.store.db.execute("DELETE FROM decisions WHERE name='lifecycle/owner'")

        with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
            self.store.claim_lifecycle_owner("replacement")

        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM decisions WHERE name='lifecycle/owner'"
            ).fetchone()[0],
            0,
        )

    def test_invalid_owner_event_timestamp_is_rejected_without_text_hydration(self):
        lease = self.store.claim_lifecycle_owner("owner")
        self.store.db.execute(
            "UPDATE events SET at=CAST(X'80' AS TEXT) "
            "WHERE kind='decision_recorded' AND entity='lifecycle/owner'"
        )
        with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
            self.store.renew_lifecycle_owner(lease)

        invalid_timestamps = (sqlite3.Binary(b"\x80"), "x" * (256 * 1024))
        for timestamp in invalid_timestamps:
            self.store.db.execute(
                "UPDATE events SET at=? "
                "WHERE kind='decision_recorded' AND entity='lifecycle/owner'",
                (timestamp,),
            )
            with self.subTest(timestamp_type=type(timestamp).__name__):
                with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
                    self.store.renew_lifecycle_owner(lease)
                self.assertEqual(
                    self.store.db.execute(
                        "SELECT COUNT(*) FROM decisions WHERE name='lifecycle/owner'"
                    ).fetchone()[0],
                    1,
                )

    def test_oversized_owner_decision_event_fails_before_json_hydration(self):
        self.store.claim_lifecycle_owner("owner")
        self.store.db.execute(
            "UPDATE events SET body=? WHERE kind='decision_recorded' AND entity='lifecycle/owner'",
            (json.dumps({"oversized": "x" * org_state._LIFECYCLE_OWNER_RECORD_MAX_BYTES}),),
        )

        with patch("engineering.org_state.json.loads", side_effect=AssertionError("body hydrated")):
            with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
                self.store._lifecycle_owner_history_head()

    def test_utf16_owner_history_validates_storage_bytes_and_transitions(self):
        utf16_path = Path(self.temp.name) / "owner-utf16.sqlite"
        connection = sqlite3.connect(utf16_path)
        connection.execute("PRAGMA encoding='UTF-16le'")
        connection.execute("CREATE TABLE encoding_seed(value TEXT)")
        connection.close()
        utf16_store = Store(utf16_path, clock=lambda: self.now)
        try:
            lease = utf16_store.claim_lifecycle_owner("owner", lease_seconds=100)
            lease = utf16_store.renew_lifecycle_owner(lease, lease_seconds=100)
            utf16_store.release_lifecycle_owner(lease)
            self.assertEqual(utf16_store._lifecycle_owner_history_head(), 3)
            utf16_store.db.execute(
                "UPDATE decisions SET body=? WHERE name='lifecycle/owner' AND version=1",
                (json.dumps({"oversized": "x" * 33_000}, separators=(",", ":")),),
            )
            with patch(
                "engineering.org_state.json.loads", side_effect=AssertionError("body hydrated")
            ):
                with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
                    utf16_store._lifecycle_owner_history_head()
        finally:
            utf16_store.close()

    def test_owner_history_head_handles_empty_and_rejects_noninteger_version(self):
        self.assertIsNone(self.store._lifecycle_owner_history_head())
        self.store.db.execute(
            "INSERT INTO decisions(name,version,body,replaces,replaces_version) "
            "VALUES('lifecycle/owner','malformed','{}',NULL,NULL)"
        )

        with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
            self.store._lifecycle_owner_history_head()

    def test_evaluation_persists_exact_observations_and_computed_plan_idempotently(self):
        lease = self.store.claim_lifecycle_owner("owner")
        observations = launch_observations()
        first = self.store.record_lifecycle_evaluation(lease, observations)
        second = self.store.record_lifecycle_evaluation(lease, dict(observations))
        self.assertEqual(first, second)
        self.assertEqual(
            first["body"]["observations"],
            {
                **observations,
                "request_owner": "owner",
                "request_epoch": lease["epoch"],
            },
        )
        self.assertEqual(first["body"]["plan"]["action"], "launch_request")
        self.assertEqual(first["body"]["plan"]["request_owner"], "owner")
        self.assertEqual(first["body"]["plan"]["request_epoch"], lease["epoch"])
        expected = plan_lifecycle(
            **{
                **observations,
                "request_owner": "owner",
                "request_epoch": lease["epoch"],
            }
        )
        self.assertEqual(first["body"]["plan"]["decision_sha256"], expected.decision_sha256)
        self.assertEqual(
            self.store.get_decision(first["id"])["body"],
            first["body"],
        )

    def test_request_worklist_is_a_bounded_read_only_projection(self):
        lease = self.store.claim_lifecycle_owner("owner")
        launch = self.store.record_lifecycle_evaluation(lease, launch_observations())
        held = self.store.record_lifecycle_evaluation(
            lease, launch_observations(runtime_ready=False)
        )
        before = self.store.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]

        projection = self.store.lifecycle_request_worklist(limit=2)
        again = self.store.lifecycle_request_worklist(limit=2)
        after = self.store.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]

        self.assertEqual(projection, again)
        self.assertEqual(projection["count"], 2)
        self.assertFalse(projection["overflow"])
        self.assertFalse(projection["dispatch_authority"])
        self.assertFalse(projection["consumption_authority"])
        self.assertEqual(
            projection["owner"],
            {
                "owner": "owner",
                "epoch": 1,
                "status": "active",
                "lease_until": 1030.0,
                "live": True,
            },
        )
        by_name = {item["decision_name"]: item for item in projection["items"]}
        self.assertEqual(by_name[launch["id"]]["action"], "launch_request")
        self.assertTrue(by_name[launch["id"]]["owner_current"])
        self.assertEqual(by_name[held["id"]]["action"], "hold")
        self.assertEqual(after, before)
        with self.assertRaisesRegex(StateError, "worklist limit invalid"):
            self.store.lifecycle_request_worklist(limit=True)

    def test_request_worklist_overflow_returns_no_partial_identities(self):
        lease = self.store.claim_lifecycle_owner("owner")
        first = self.store.record_lifecycle_evaluation(lease, launch_observations())
        second = self.store.record_lifecycle_evaluation(
            lease, launch_observations(backoff_until=2000.0)
        )
        before = self.store.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]

        overflow = self.store.lifecycle_request_worklist(limit=1)

        self.assertTrue(overflow["overflow"])
        self.assertIsNone(overflow["count"])
        self.assertEqual(overflow["items"], [])
        self.assertFalse(overflow["dispatch_authority"])
        self.assertFalse(overflow["consumption_authority"])
        self.assertNotIn(first["id"], str(overflow))
        self.assertNotIn(second["id"], str(overflow))
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0], before
        )

    def test_request_worklist_does_not_hydrate_oversized_journal_names(self):
        name = "lifecycle/request/" + "a" * 64 + "x" * (256 * 1024)
        self.store.db.execute(
            "INSERT INTO decisions(name,version,body) VALUES(?,?,?)", (name, 1, "{}")
        )

        row = self.store.db.execute(
            org_state._LIFECYCLE_REQUEST_FIRST_QUERY, ("lifecycle/request/",)
        ).fetchone()
        self.assertIsNone(row["journal_name"])
        self.assertEqual(row["journal_name_length"], len(name))
        with self.assertRaisesRegex(StateError, "lifecycle_request_identity_invalid"):
            self.store.lifecycle_request_worklist(limit=1)

    def test_request_worklist_does_not_hydrate_malformed_utf8_journal_names(self):
        raw_name = b"lifecycle/request/" + b"a" * 62 + b"\xc2" + b"\x80" * (256 * 1024)
        self.store.db.execute(
            "INSERT INTO decisions(name,version,body) VALUES(CAST(? AS TEXT),?,?)",
            (sqlite3.Binary(raw_name), 1, "{}"),
        )

        row = self.store.db.execute(
            org_state._LIFECYCLE_REQUEST_FIRST_QUERY, ("lifecycle/request/",)
        ).fetchone()
        self.assertIsNone(row["journal_name"])
        self.assertEqual(row["journal_name_storage_bytes"], len(raw_name))
        with self.assertRaisesRegex(StateError, "lifecycle_request_identity_invalid"):
            self.store.lifecycle_request_worklist(limit=1)

    def test_lifecycle_request_discovery_uses_decision_primary_key_range(self):
        for query, cursor, seek_opcode in (
            (org_state._LIFECYCLE_REQUEST_FIRST_QUERY, "lifecycle/request/", "SeekGE"),
            (
                org_state._LIFECYCLE_REQUEST_NEXT_QUERY,
                "lifecycle/request/" + "a" * 64,
                "SeekGT",
            ),
        ):
            plan = self.store.db.execute("EXPLAIN QUERY PLAN " + query, (cursor,)).fetchall()
            self.assertTrue(
                any(
                    "SEARCH decisions USING COVERING INDEX sqlite_autoindex_decisions_1"
                    in row["detail"]
                    and "name>? AND name<?" in row["detail"]
                    for row in plan
                )
            )
            program = self.store.db.execute("EXPLAIN " + query, (cursor,)).fetchall()
            cursor_register = next(row[3] for row in program if row[1] == "Variable")
            lower_seek = next(row for row in program if row[1] == seek_opcode)
            self.assertEqual(lower_seek[4], cursor_register)

    def test_lifecycle_request_name_seek_skips_versions_of_prior_journal(self):
        bare_prefix = "lifecycle/request/"
        first_name = "lifecycle/request/" + "a" * 64
        next_name = "lifecycle/request/" + "b" * 64
        self.store.db.executemany(
            "INSERT INTO decisions(name,version,body) VALUES(?,?,?)",
            [(bare_prefix, 1, "{}")]
            + [(first_name, version, "{}") for version in range(1, 1001)]
            + [(next_name, 1, "{}")],
        )

        with self.store._read_snapshot():
            names = self.store._lifecycle_request_names_in_snapshot(limit=100)

        self.assertEqual(names, [bare_prefix, first_name, next_name])
        with self.assertRaisesRegex(StateError, "lifecycle_request_identity_invalid"):
            self.store.lifecycle_request_worklist()

    def test_request_worklist_rejects_one_oversized_record_before_decoding(self):
        lease = self.store.claim_lifecycle_owner("owner")
        request = self.store.record_lifecycle_evaluation(lease, launch_observations())
        oversized = json.dumps("x" * (64 * 1024), separators=(",", ":"))
        self.store.db.execute(
            "UPDATE decisions SET body=? WHERE name=? AND version=1",
            (oversized, request["id"]),
        )

        with self.assertRaisesRegex(StateError, "lifecycle_request_byte_limit"):
            self.store.lifecycle_request_worklist()

    def test_request_worklist_rejects_nonroot_history_links_before_hydration(self):
        lease = self.store.claim_lifecycle_owner("owner")
        request = self.store.record_lifecycle_evaluation(lease, launch_observations())
        self.store.db.execute(
            "UPDATE decisions SET replaces=?,replaces_version=1 WHERE name=? AND version=1",
            ("x" * (2 * 1024 * 1024), request["id"]),
        )

        with self.assertRaisesRegex(StateError, "lifecycle_request_history_invalid"):
            self.store.lifecycle_request_worklist()

    def test_request_worklist_rejects_malformed_utf8_link_without_decoding_it(self):
        lease = self.store.claim_lifecycle_owner("owner")
        request = self.store.record_lifecycle_evaluation(lease, launch_observations())
        self.store.db.execute(
            "UPDATE decisions SET replaces=CAST(X'80' AS TEXT) WHERE name=? AND version=1",
            (request["id"],),
        )

        with self.assertRaisesRegex(StateError, "lifecycle_request_history_invalid"):
            self.store.lifecycle_request_worklist()

    def test_request_retry_rejects_malformed_root_link_before_body_hydration(self):
        lease = self.store.claim_lifecycle_owner("owner")
        observations = launch_observations()
        request = self.store.record_lifecycle_evaluation(lease, observations)
        self.store.db.execute(
            "UPDATE decisions SET replaces=NULL,replaces_version=1 WHERE name=? AND version=1",
            (request["id"],),
        )

        with self.assertRaisesRegex(StateError, "lifecycle_request_history_invalid"):
            self.store.record_lifecycle_evaluation(lease, observations)

    def test_request_retry_rejects_malformed_utf8_link_before_body_decoding(self):
        lease = self.store.claim_lifecycle_owner("owner")
        observations = launch_observations()
        request = self.store.record_lifecycle_evaluation(lease, observations)
        self.store.db.execute(
            "UPDATE decisions SET replaces=CAST(X'80' AS TEXT),body=CAST(X'80' AS TEXT) "
            "WHERE name=? AND version=1",
            (request["id"],),
        )

        with self.assertRaisesRegex(StateError, "lifecycle_request_history_invalid"):
            self.store.record_lifecycle_evaluation(lease, observations)

    def test_request_retry_rejects_oversized_body_before_json_hydration(self):
        lease = self.store.claim_lifecycle_owner("owner")
        observations = launch_observations()
        request = self.store.record_lifecycle_evaluation(lease, observations)
        oversized = json.dumps("x" * (64 * 1024), separators=(",", ":"))
        self.store.db.execute(
            "UPDATE decisions SET body=? WHERE name=? AND version=1",
            (oversized, request["id"]),
        )

        with self.assertRaisesRegex(StateError, "lifecycle_request_byte_limit"):
            self.store.record_lifecycle_evaluation(lease, observations)

    def test_lifecycle_evaluation_rejects_oversized_request_before_persistence(self):
        lease = self.store.claim_lifecycle_owner("owner")
        observations = launch_observations(stop_reasons=["x" * (64 * 1024)])

        with self.assertRaisesRegex(StateError, "lifecycle_request_byte_limit"):
            self.store.record_lifecycle_evaluation(lease, observations)

        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM decisions WHERE name GLOB 'lifecycle/request/*'"
            ).fetchone()[0],
            0,
        )

    def test_request_worklist_rejects_aggregate_payload_overflow(self):
        lease = self.store.claim_lifecycle_owner("owner")
        requests = [
            self.store.record_lifecycle_evaluation(
                lease, launch_observations(backoff_until=2000.0 + index)
            )
            for index in range(18)
        ]
        large_body = json.dumps("x" * 59_999, separators=(",", ":"))
        self.store.db.executemany(
            "UPDATE decisions SET body=? WHERE name=? AND version=1",
            [(large_body, request["id"]) for request in requests],
        )

        with self.assertRaisesRegex(StateError, "lifecycle_worklist_byte_limit"):
            self.store.lifecycle_request_worklist()

    def test_request_projection_marks_replaced_owner_epoch_noncurrent(self):
        lease = self.store.claim_lifecycle_owner("owner", lease_seconds=5)
        first = self.store.record_lifecycle_evaluation(lease, launch_observations())
        self.now += 5
        replacement = self.store.claim_lifecycle_owner("replacement")
        second = self.store.record_lifecycle_evaluation(replacement, launch_observations())

        projection = self.store.lifecycle_request_worklist(limit=2)
        by_name = {item["decision_name"]: item for item in projection["items"]}

        self.assertFalse(by_name[first["id"]]["owner_current"])
        self.assertTrue(by_name[second["id"]]["owner_current"])
        self.assertEqual(projection["owner"]["epoch"], replacement["epoch"])

    def test_request_projection_uses_one_snapshot_during_owner_release(self):
        lease = self.store.claim_lifecycle_owner("owner", lease_seconds=30)
        request = self.store.record_lifecycle_evaluation(lease, launch_observations())
        other = Store(self.path, clock=lambda: self.now)
        owner_history_read = threading.Event()

        def pause_before_owner_history(statement):
            if (
                statement.strip()
                .upper()
                .startswith("SELECT COUNT(*) AS VERSION_COUNT, MIN(VERSION) AS FIRST_VERSION")
            ):
                owner_history_read.set()
                continue_owner_read.wait()

        continue_owner_read = threading.Event()
        self.store.db.set_trace_callback(pause_before_owner_history)
        pool = ThreadPoolExecutor(1)
        try:
            future = pool.submit(self.store.lifecycle_request_worklist)
            try:
                self.assertTrue(owner_history_read.wait(2))
                other.release_lifecycle_owner(lease)
            finally:
                continue_owner_read.set()
            projection = future.result(timeout=2)

            self.assertEqual(projection["owner"]["status"], "active")
            self.assertTrue(projection["owner"]["live"])
            self.assertTrue(projection["items"][0]["owner_current"])
            self.assertEqual(projection["items"][0]["request_id"], request["id"].rsplit("/", 1)[-1])
            self.assertEqual(
                self.store.get_decision("lifecycle/owner")["body"]["status"], "released"
            )
        finally:
            continue_owner_read.set()
            pool.shutdown(wait=True)
            self.store.db.set_trace_callback(None)
            other.close()

    def test_request_projection_fails_closed_on_identity_corruption(self):
        lease = self.store.claim_lifecycle_owner("owner")
        request = self.store.record_lifecycle_evaluation(lease, launch_observations())
        body = dict(request["body"])
        body["plan"] = {**body["plan"], "decision_sha256": "f" * 64}
        self.store.db.execute(
            "UPDATE decisions SET body=? WHERE name=? AND version=1",
            (json.dumps(body, sort_keys=True, separators=(",", ":")), request["id"]),
        )

        with self.assertRaisesRegex(StateError, "identity_invalid|request_invalid"):
            self.store.lifecycle_request_worklist()

    def test_launch_plan_cannot_bypass_observation_evaluation(self):
        lease = self.store.claim_lifecycle_owner("owner")
        with self.assertRaisesRegex(StateError, "lifecycle_launch_requires_observation"):
            self.store.record_lifecycle_plan(lease, launch_plan("owner", lease["epoch"]))
        with self.assertRaisesRegex(StateError, "stale_lifecycle_owner"):
            self.store.record_lifecycle_evaluation(
                {"owner": "owner", "epoch": True}, launch_observations()
            )

    def test_failed_gate_is_recomputed_and_observation_shape_is_closed(self):
        lease = self.store.claim_lifecycle_owner("owner")
        observations = launch_observations(stop_reasons=["founder_stop"])
        receipt = self.store.record_lifecycle_evaluation(lease, observations)
        self.assertEqual(receipt["body"]["plan"]["action"], "hold")
        self.assertEqual(receipt["body"]["plan"]["reason"], "stop_reason_active")
        self.assertEqual(receipt["body"]["observations"]["stop_reasons"], ["founder_stop"])

        before = self.store.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        with self.assertRaisesRegex(StateError, "invalid_lifecycle_observation"):
            self.store.record_lifecycle_evaluation(
                lease, {**launch_observations(), "unreviewed": True}
            )
        after = self.store.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        self.assertEqual(after, before)

    def test_open_controller_parse_recovery_vetoes_optimistic_launch_observations(self):
        lease = self.store.claim_lifecycle_owner("owner")
        self.store.add_task("task-7", "engineering", "Controller parse fault source")
        attempt = self.store.claim("task-7", "worker")
        self.store.open_controller_parse_fault(
            attempt["id"], attempt["epoch"], stage="lead", exception_type="ValueError"
        )

        with self.assertRaisesRegex(StateError, "controller_parse_recovery_open"):
            self.store.require_controller_parse_recovery_clear()
        with self.assertRaisesRegex(StateError, "controller_parse_recovery_open"):
            self.store.record_lifecycle_evaluation(lease, launch_observations(recovery_open=False))
        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM decisions WHERE name LIKE 'lifecycle/request/%'"
            ).fetchone()[0],
            0,
        )

    def test_repeated_resolved_controller_parse_recovery_history_allows_launch(self):
        lease = self.store.claim_lifecycle_owner("owner")
        for recovery_index in (1, 2):
            task_id = f"task-{recovery_index}"
            self.store.add_task(task_id, "engineering", "Controller parse fault source")
            attempt = self.store.claim(task_id, "worker")
            self.store.open_controller_parse_fault(
                attempt["id"], attempt["epoch"], stage="lead", exception_type="ValueError"
            )
            circuit = self.store.get_decision("circuit/controller-parse")
            obligation = self.store.get_decision("recovery-obligation/controller-parse")
            self.store.resolve_controller_parse_fault(
                expected_circuit_version=circuit["version"],
                expected_obligation_version=obligation["version"],
                recovery_id=f"recovery-{recovery_index}",
                fix_sha256=str(recovery_index) * 64,
                review_ref=f"review-{recovery_index}",
                authorization_ref=f"authorization-{recovery_index}",
            )

        receipt = self.store.record_lifecycle_evaluation(lease, launch_observations())
        self.assertEqual(receipt["body"]["plan"]["action"], "launch_request")
        self.store.require_controller_parse_recovery_clear()

    def test_controller_parse_history_version_summary_handles_long_contiguous_journal(self):
        name = "circuit/controller-parse"
        self.store.db.executemany(
            "INSERT INTO decisions(name,version,body,replaces,replaces_version) VALUES(?,?,?,?,?)",
            [
                (
                    name,
                    version,
                    "{}",
                    None if version == 1 else name,
                    None if version == 1 else version - 1,
                )
                for version in range(1, 1001)
            ],
        )

        self.assertEqual(self.store._lifecycle_decision_versions(name), 1000)
        self.store.db.execute("DELETE FROM decisions WHERE name=? AND version=?", (name, 500))
        with self.assertRaisesRegex(StateError, "controller_parse_recovery_history_invalid"):
            self.store._lifecycle_decision_versions(name)

    def test_controller_parse_resolution_rejects_invalid_or_oversized_references(self):
        self.store.add_task("task-recovery-ref", "engineering", "Recovery reference source")
        attempt = self.store.claim("task-recovery-ref", "worker")
        events_before_open = self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        with self.assertRaisesRegex(StateError, "controller_parse_recovery_record_byte_limit"):
            self.store.open_controller_parse_fault(
                attempt["id"],
                attempt["epoch"],
                stage="x" * org_state._CONTROLLER_PARSE_RECOVERY_RECORD_MAX_BYTES,
                exception_type="ValueError",
            )
        self.assertIsNone(self.store.get_decision("circuit/controller-parse"))
        self.assertIsNone(self.store.get_decision("recovery-obligation/controller-parse"))
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], events_before_open
        )
        self.assertEqual(self.store.get_task("task-recovery-ref")["state"], "running")

        self.store.open_controller_parse_fault(
            attempt["id"], attempt["epoch"], stage="lead", exception_type="ValueError"
        )
        circuit = self.store.get_decision("circuit/controller-parse")
        obligation = self.store.get_decision("recovery-obligation/controller-parse")
        circuit_before = circuit
        obligation_before = obligation
        events_before = self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        invalid_references = (
            {"recovery_id": 1},
            {"fix_sha256": "A" * 64},
            {"review_ref": ""},
            {"authorization_ref": "bad\x00reference"},
            {"recovery_id": "x" * org_state._CONTROLLER_PARSE_RECOVERY_RECORD_MAX_BYTES},
        )

        for replacement in invalid_references:
            arguments = {
                "recovery_id": "recovery-1",
                "fix_sha256": "a" * 64,
                "review_ref": "review-1",
                "authorization_ref": "authorization-1",
                **replacement,
            }
            with self.subTest(replacement=tuple(replacement)):
                with self.assertRaises(StateError):
                    self.store.resolve_controller_parse_fault(
                        expected_circuit_version=circuit["version"],
                        expected_obligation_version=obligation["version"],
                        **arguments,
                    )
                self.assertEqual(
                    self.store.get_decision("circuit/controller-parse"), circuit_before
                )
                self.assertEqual(
                    self.store.get_decision("recovery-obligation/controller-parse"),
                    obligation_before,
                )
                self.assertEqual(
                    self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                    events_before,
                )

        self.store.resolve_controller_parse_fault(
            expected_circuit_version=circuit["version"],
            expected_obligation_version=obligation["version"],
            recovery_id="recovery-1",
            fix_sha256="a" * 64,
            review_ref="review-1",
            authorization_ref="authorization-1",
        )

    def test_generic_decisions_cannot_resolve_controller_parse_recovery(self):
        self.store.add_task("task-generic-recovery", "engineering", "Controller parse source")
        attempt = self.store.claim("task-generic-recovery", "worker")
        self.store.open_controller_parse_fault(
            attempt["id"], attempt["epoch"], stage="lead", exception_type="ValueError"
        )
        circuit = self.store.get_decision("circuit/controller-parse")
        obligation = self.store.get_decision("recovery-obligation/controller-parse")
        events_before = self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        for name in ("circuit/controller-parse", "recovery-obligation/controller-parse"):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(StateError, "decision_namespace_reserved"),
            ):
                self.store.put_decision(name, {"status": "resolved"})
        self.assertEqual(self.store.get_decision("circuit/controller-parse"), circuit)
        self.assertEqual(
            self.store.get_decision("recovery-obligation/controller-parse"), obligation
        )
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], events_before
        )

        adjacent_version = self.store.put_decision(
            "circuit/controller-parse-adjacent", {"status": "resolved"}
        )
        self.assertEqual(adjacent_version, 1)
        with self.assertRaisesRegex(StateError, "controller_recovery_obligation_changed"):
            self.store.resolve_controller_parse_fault(
                expected_circuit_version=circuit["version"] + 1,
                expected_obligation_version=obligation["version"],
                recovery_id="recovery-1",
                fix_sha256="a" * 64,
                review_ref="review-1",
                authorization_ref="authorization-1",
            )
        self.assertEqual(self.store.get_decision("circuit/controller-parse"), circuit)
        self.assertEqual(
            self.store.get_decision("recovery-obligation/controller-parse"), obligation
        )
        self.store.resolve_controller_parse_fault(
            expected_circuit_version=circuit["version"],
            expected_obligation_version=obligation["version"],
            recovery_id="recovery-1",
            fix_sha256="a" * 64,
            review_ref="review-1",
            authorization_ref="authorization-1",
        )

    def test_oversized_controller_parse_record_fails_before_json_hydration(self):
        self.store.add_task("task-large-recovery", "engineering", "Oversized recovery source")
        attempt = self.store.claim("task-large-recovery", "worker")
        self.store.open_controller_parse_fault(
            attempt["id"], attempt["epoch"], stage="lead", exception_type="ValueError"
        )
        current = self.store.get_decision("circuit/controller-parse")
        oversized = {
            **current["body"],
            "extra": "x" * org_state._CONTROLLER_PARSE_RECOVERY_RECORD_MAX_BYTES,
        }
        self.store.db.execute(
            "UPDATE decisions SET body=? WHERE name=? AND version=?",
            (
                json.dumps(oversized, sort_keys=True, separators=(",", ":")),
                "circuit/controller-parse",
                current["version"],
            ),
        )

        with patch("engineering.org_state.json.loads", side_effect=AssertionError("body hydrated")):
            with self.assertRaisesRegex(StateError, "controller_parse_recovery_history_invalid"):
                self.store._require_controller_parse_recovery_clear()

    def test_utf16_controller_parse_record_limits_match_stored_bytes(self):
        utf16_path = Path(self.temp.name) / "utf16.sqlite"
        connection = sqlite3.connect(utf16_path)
        connection.execute("PRAGMA encoding='UTF-16le'")
        connection.execute("CREATE TABLE encoding_seed(value TEXT)")
        connection.close()
        utf16_store = Store(utf16_path, clock=lambda: self.now)
        try:
            utf16_store.add_task("utf16-task", "engineering", "UTF-16 recovery source")
            attempt = utf16_store.claim("utf16-task", "worker")
            events_before = utf16_store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            with self.assertRaisesRegex(StateError, "controller_parse_recovery_record_byte_limit"):
                utf16_store.open_controller_parse_fault(
                    attempt["id"],
                    attempt["epoch"],
                    stage="x" * 33_000,
                    exception_type="ValueError",
                )
            self.assertIsNone(utf16_store.get_decision("circuit/controller-parse"))
            self.assertIsNone(utf16_store.get_decision("recovery-obligation/controller-parse"))
            self.assertEqual(
                utf16_store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], events_before
            )
            self.assertEqual(utf16_store.get_task("utf16-task")["state"], "running")

            utf16_store.open_controller_parse_fault(
                attempt["id"],
                attempt["epoch"],
                stage="x" * 20_000,
                exception_type="ValueError",
            )
            circuit = utf16_store.get_decision("circuit/controller-parse")
            obligation = utf16_store.get_decision("recovery-obligation/controller-parse")
            utf16_store.resolve_controller_parse_fault(
                expected_circuit_version=circuit["version"],
                expected_obligation_version=obligation["version"],
                recovery_id="r" * 20_000,
                fix_sha256="a" * 64,
                review_ref="review-1",
                authorization_ref="authorization-1",
            )
            utf16_store._require_controller_parse_recovery_clear()
        finally:
            utf16_store.close()

    def test_controller_parse_recovery_rejects_broken_journal_links(self):
        lease = self.store.claim_lifecycle_owner("owner")
        for recovery_index in (1, 2):
            task_id = f"task-{recovery_index}"
            self.store.add_task(task_id, "engineering", "Controller parse fault source")
            attempt = self.store.claim(task_id, "worker")
            self.store.open_controller_parse_fault(
                attempt["id"], attempt["epoch"], stage="lead", exception_type="ValueError"
            )
            circuit = self.store.get_decision("circuit/controller-parse")
            obligation = self.store.get_decision("recovery-obligation/controller-parse")
            self.store.resolve_controller_parse_fault(
                expected_circuit_version=circuit["version"],
                expected_obligation_version=obligation["version"],
                recovery_id=f"recovery-{recovery_index}",
                fix_sha256=str(recovery_index) * 64,
                review_ref=f"review-{recovery_index}",
                authorization_ref=f"authorization-{recovery_index}",
            )

        malformed_links = (
            ("circuit/controller-parse", 1, "other-journal", 1),
            ("recovery-obligation/controller-parse", 1, None, 0),
            ("circuit/controller-parse", 2, "circuit/controller-parse", 99),
            ("recovery-obligation/controller-parse", 2, "circuit/controller-parse", 1),
        )
        for name, version, replaces, replaces_version in malformed_links:
            with self.subTest(name=name, version=version, replaces_version=replaces_version):
                row = self.store.db.execute(
                    "SELECT replaces,replaces_version FROM decisions WHERE name=? AND version=?",
                    (name, version),
                ).fetchone()
                events_before = self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                requests_before = self.store.db.execute(
                    "SELECT COUNT(*) FROM decisions WHERE name LIKE 'lifecycle/request/%'"
                ).fetchone()[0]
                self.store.db.execute(
                    "UPDATE decisions SET replaces=?,replaces_version=? WHERE name=? AND version=?",
                    (replaces, replaces_version, name, version),
                )

                with self.assertRaisesRegex(
                    StateError, "controller_parse_recovery_history_invalid"
                ):
                    self.store.record_lifecycle_evaluation(lease, launch_observations())

                self.assertEqual(
                    self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                    events_before,
                )
                self.assertEqual(
                    self.store.db.execute(
                        "SELECT COUNT(*) FROM decisions WHERE name LIKE 'lifecycle/request/%'"
                    ).fetchone()[0],
                    requests_before,
                )
                self.store.db.execute(
                    "UPDATE decisions SET replaces=?,replaces_version=? WHERE name=? AND version=?",
                    (row["replaces"], row["replaces_version"], name, version),
                )

        receipt = self.store.record_lifecycle_evaluation(lease, launch_observations())
        self.assertEqual(receipt["body"]["plan"]["action"], "launch_request")

    def test_malformed_controller_parse_recovery_pair_fails_closed(self):
        lease = self.store.claim_lifecycle_owner("owner")
        self.store.add_task("task-7", "engineering", "Controller parse fault source")
        attempt = self.store.claim("task-7", "worker")
        self.store.open_controller_parse_fault(
            attempt["id"], attempt["epoch"], stage="lead", exception_type="ValueError"
        )
        obligation = self.store.get_decision("recovery-obligation/controller-parse")
        malformed = {
            **obligation["body"],
            "first_attempt_id": float(obligation["body"]["first_attempt_id"]),
        }
        self.store.db.execute(
            "UPDATE decisions SET body=? WHERE name=? AND version=1",
            (
                json.dumps(malformed, sort_keys=True, separators=(",", ":")),
                "recovery-obligation/controller-parse",
            ),
        )

        with self.assertRaisesRegex(StateError, "controller_parse_recovery_history_invalid"):
            self.store.record_lifecycle_evaluation(lease, launch_observations())

    def test_deeply_nested_controller_parse_json_fails_closed(self):
        self.store.add_task("task-deep-recovery", "engineering", "Nested recovery source")
        attempt = self.store.claim("task-deep-recovery", "worker")
        self.store.open_controller_parse_fault(
            attempt["id"], attempt["epoch"], stage="lead", exception_type="ValueError"
        )
        self.store.db.execute(
            "UPDATE decisions SET body=? WHERE name=? AND version=1",
            ("[" * 1100 + "0" + "]" * 1100, "circuit/controller-parse"),
        )
        events_before = self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        with self.assertRaisesRegex(StateError, "controller_parse_recovery_history_invalid"):
            self.store.require_controller_parse_recovery_clear()

        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], events_before
        )

    def test_controller_parse_recovery_added_during_lock_wait_vetoes_launch(self):
        lease = self.store.claim_lifecycle_owner("owner")
        self.store.add_task("task-7", "engineering", "Controller parse fault source")
        attempt = self.store.claim("task-7", "worker")
        other = Store(self.path, clock=lambda: self.now)
        entered_transaction = threading.Event()
        fault_records_written = threading.Event()
        finish_fault = threading.Event()
        original_event = other._event

        def pause_fault_commit(*args, **kwargs):
            fault_records_written.set()
            if not finish_fault.wait(2):
                raise RuntimeError("test_fault_commit_release_timeout")
            return original_event(*args, **kwargs)

        self.store.db.set_trace_callback(
            lambda statement: (
                entered_transaction.set() if statement.startswith("BEGIN IMMEDIATE") else None
            )
        )
        pool = ThreadPoolExecutor(2)
        try:
            with patch.object(other, "_event", pause_fault_commit):
                fault_future = pool.submit(
                    other.open_controller_parse_fault,
                    attempt["id"],
                    attempt["epoch"],
                    stage="lead",
                    exception_type="ValueError",
                )
                self.assertTrue(fault_records_written.wait(2))
                evaluation_future = pool.submit(
                    self.store.record_lifecycle_evaluation,
                    lease,
                    launch_observations(recovery_open=False),
                )
                self.assertTrue(entered_transaction.wait(2))
                finish_fault.set()
                fault_future.result(timeout=3)
                with self.assertRaisesRegex(StateError, "controller_parse_recovery_open"):
                    evaluation_future.result(timeout=3)
            self.assertEqual(
                self.store.db.execute(
                    "SELECT COUNT(*) FROM decisions WHERE name LIKE 'lifecycle/request/%'"
                ).fetchone()[0],
                0,
            )
        finally:
            finish_fault.set()
            pool.shutdown(wait=True)
            self.store.db.set_trace_callback(None)
            other.close()

    def test_observation_snapshot_is_detached_before_sqlite_lock_wait(self):
        lease = self.store.claim_lifecycle_owner("owner")
        other = Store(self.path, clock=lambda: self.now)
        entered_transaction = threading.Event()
        observations = launch_observations()
        other.db.execute("BEGIN IMMEDIATE")
        original_tx = self.store._tx

        @contextmanager
        def signal_transaction_entry():
            entered_transaction.set()
            with original_tx():
                yield

        try:
            with patch.object(self.store, "_tx", signal_transaction_entry):
                with ThreadPoolExecutor(1) as pool:
                    future = pool.submit(
                        self.store.record_lifecycle_evaluation, lease, observations
                    )
                    self.assertTrue(entered_transaction.wait(2))
                    observations["launch_authorized"] = False
                    other.db.execute("COMMIT")
                    receipt = future.result(timeout=2)
            self.assertTrue(receipt["body"]["observations"]["launch_authorized"])
            self.assertEqual(receipt["body"]["plan"]["action"], "launch_request")
        finally:
            if other.db.in_transaction:
                other.db.execute("ROLLBACK")
            other.close()

    def test_malformed_plan_rejected_before_journal_mutation(self):
        lease = self.store.claim_lifecycle_owner("owner")
        malformed = (
            {
                **hold_plan(),
                "action": [],
            },
            {
                **hold_plan(action="wait"),
                "retry_at": 10**10000,
            },
            {**hold_plan(), "extra": True},
        )
        for plan in malformed:
            with self.subTest(plan=plan["action"]):
                with self.assertRaisesRegex(StateError, "invalid_lifecycle_(?:input|plan)"):
                    self.store.record_lifecycle_plan(lease, plan)
        request_count = self.store.db.execute(
            "SELECT COUNT(*) FROM decisions WHERE name LIKE 'lifecycle/request/%'"
        ).fetchone()[0]
        self.assertEqual(request_count, 0)

    def test_corrupt_persisted_owner_state_fails_closed(self):
        self.store.db.execute(
            "INSERT INTO decisions(name,version,body,replaces,replaces_version) "
            "VALUES(?,1,?,NULL,NULL)",
            (
                "lifecycle/owner",
                '{"status":"active","owner":"owner","epoch":true,"lease_until":1100.0}',
            ),
        )
        with self.assertRaisesRegex(StateError, "lifecycle_owner_state_invalid"):
            self.store.claim_lifecycle_owner("replacement")

    def test_generic_decision_writer_cannot_bypass_lifecycle_fencing_or_idempotency(self):
        lease = self.store.claim_lifecycle_owner("owner")

        class AliasedName(str):
            def __eq__(self, other):
                return False

            def startswith(self, *args, **kwargs):
                return False

        with self.assertRaisesRegex(StateError, "decision_namespace_reserved"):
            self.store.put_decision(
                "lifecycle/owner",
                {"status": "active", "owner": "forged", "epoch": 99, "lease_until": 2000},
            )
        with self.assertRaisesRegex(StateError, "plain string"):
            self.store.put_decision(
                AliasedName("lifecycle/owner"),
                {"status": "active", "owner": "forged", "epoch": 99, "lease_until": 2000},
            )

        observations = launch_observations()
        first = self.store.record_lifecycle_evaluation(lease, observations)
        with self.assertRaisesRegex(StateError, "decision_namespace_reserved"):
            self.store.put_decision(first["id"], {"forged": True})
        with self.assertRaisesRegex(StateError, "plain string"):
            self.store.put_decision(AliasedName(first["id"]), {"forged": True})
        second = self.store.record_lifecycle_evaluation(lease, observations)
        self.assertEqual(first, second)

    def test_unexpected_request_history_fails_closed(self):
        lease = self.store.claim_lifecycle_owner("owner")
        observations = launch_observations()
        first = self.store.record_lifecycle_evaluation(lease, observations)
        self.store.db.execute(
            "INSERT INTO decisions(name,version,body,replaces,replaces_version) "
            "VALUES(?,2,?,NULL,NULL)",
            (
                first["id"],
                self.store.db.execute(
                    "SELECT body FROM decisions WHERE name=? AND version=1", (first["id"],)
                ).fetchone()[0],
            ),
        )
        with self.assertRaisesRegex(StateError, "lifecycle_request_history_invalid"):
            self.store.record_lifecycle_evaluation(lease, observations)

    def test_nonpositive_request_history_fails_closed(self):
        lease = self.store.claim_lifecycle_owner("owner")
        observations = launch_observations()
        first = self.store.record_lifecycle_evaluation(lease, observations)
        body = self.store.db.execute(
            "SELECT body FROM decisions WHERE name=? AND version=1", (first["id"],)
        ).fetchone()[0]
        self.store.db.execute(
            "INSERT INTO decisions(name,version,body,replaces,replaces_version) "
            "VALUES(?,0,?,NULL,NULL)",
            (first["id"], body),
        )
        with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
            self.store.record_lifecycle_evaluation(lease, observations)

    def test_gapped_request_history_preserves_decision_history_error(self):
        lease = self.store.claim_lifecycle_owner("owner")
        observations = launch_observations()
        first = self.store.record_lifecycle_evaluation(lease, observations)
        body = self.store.db.execute(
            "SELECT body FROM decisions WHERE name=? AND version=1", (first["id"],)
        ).fetchone()[0]
        self.store.db.execute(
            "INSERT INTO decisions(name,version,body,replaces,replaces_version) "
            "VALUES(?,3,?,NULL,NULL)",
            (first["id"], body),
        )

        with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
            self.store.record_lifecycle_evaluation(lease, observations)

    def test_request_retry_detects_json_type_corruption(self):
        lease = self.store.claim_lifecycle_owner("owner")
        observations = launch_observations()
        first = self.store.record_lifecycle_evaluation(lease, observations)
        body = json.loads(
            self.store.db.execute(
                "SELECT body FROM decisions WHERE name=? AND version=1", (first["id"],)
            ).fetchone()[0]
        )
        body["epoch"] = True
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        self.store.db.execute(
            "UPDATE decisions SET body=? WHERE name=? AND version=1", (encoded, first["id"])
        )
        with self.assertRaisesRegex(StateError, "lifecycle_request_identity_conflict"):
            self.store.record_lifecycle_evaluation(lease, observations)

    def test_claim_race_across_store_connections_has_one_owner(self):
        other = Store(self.path, clock=lambda: self.now)
        try:
            with ThreadPoolExecutor(2) as pool:
                results = list(
                    pool.map(
                        lambda args: args[0].claim_lifecycle_owner(args[1]),
                        ((self.store, "one"), (other, "two")),
                    )
                )
            self.assertEqual(sum(result is not None for result in results), 1)
            persisted = self.store.get_decision("lifecycle/owner")["body"]
            self.assertIn(persisted["owner"], {"one", "two"})
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
