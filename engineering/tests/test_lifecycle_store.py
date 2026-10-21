from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from engineering import org_state
from engineering.lifecycle import plan_lifecycle
from engineering.org_state import StateError, Store


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
