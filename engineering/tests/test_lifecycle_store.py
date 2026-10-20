from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

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
            self.store.record_lifecycle_plan(lease, launch_plan("luna", 1))

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

    def test_launch_request_is_persisted_once_and_exact_retry_is_idempotent(self):
        lease = self.store.claim_lifecycle_owner("owner")
        plan = launch_plan("owner", lease["epoch"])
        first = self.store.record_lifecycle_plan(lease, plan)
        second = self.store.record_lifecycle_plan(lease, dict(plan))
        self.assertEqual(first, second)
        self.assertEqual(first["body"]["plan"], plan)
        self.assertEqual(
            self.store.get_decision(first["id"])["body"],
            {"owner": "owner", "epoch": lease["epoch"], "plan": plan},
        )

    def test_request_epoch_mismatch_and_conflicting_plan_reuse_fail_closed(self):
        lease = self.store.claim_lifecycle_owner("owner")
        with self.assertRaisesRegex(StateError, "lifecycle_request_fence_mismatch"):
            self.store.record_lifecycle_plan(lease, launch_plan("owner", lease["epoch"] + 1))
        with self.assertRaisesRegex(StateError, "stale_lifecycle_owner"):
            self.store.record_lifecycle_plan(
                {"owner": "owner", "epoch": True}, launch_plan("owner", lease["epoch"])
            )

        plan = launch_plan("owner", lease["epoch"])
        self.store.record_lifecycle_plan(lease, plan)
        conflict = {**plan, "reason": "rewritten"}
        with self.assertRaisesRegex(StateError, "lifecycle_request_identity_conflict"):
            self.store.record_lifecycle_plan(lease, conflict)

    def test_malformed_plan_rejected_before_journal_mutation(self):
        lease = self.store.claim_lifecycle_owner("owner")
        malformed = (
            {
                "action": [],
                "reason": "x",
                "retry_at": None,
                "attempt_id": None,
                "request_owner": None,
                "request_epoch": None,
                "decision_sha256": "a" * 64,
            },
            {
                "action": "launch_request",
                "reason": "x",
                "retry_at": 10**10000,
                "attempt_id": None,
                "request_owner": "owner",
                "request_epoch": lease["epoch"],
                "decision_sha256": "a" * 64,
            },
            {**launch_plan("owner", lease["epoch"]), "extra": True},
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

        plan = launch_plan("owner", lease["epoch"])
        first = self.store.record_lifecycle_plan(lease, plan)
        with self.assertRaisesRegex(StateError, "decision_namespace_reserved"):
            self.store.put_decision(first["id"], {"forged": True})
        with self.assertRaisesRegex(StateError, "plain string"):
            self.store.put_decision(AliasedName(first["id"]), {"forged": True})
        second = self.store.record_lifecycle_plan(lease, plan)
        self.assertEqual(first, second)

    def test_unexpected_request_history_fails_closed(self):
        lease = self.store.claim_lifecycle_owner("owner")
        plan = launch_plan("owner", lease["epoch"])
        first = self.store.record_lifecycle_plan(lease, plan)
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
            self.store.record_lifecycle_plan(lease, plan)

    def test_nonpositive_request_history_fails_closed(self):
        lease = self.store.claim_lifecycle_owner("owner")
        plan = launch_plan("owner", lease["epoch"])
        first = self.store.record_lifecycle_plan(lease, plan)
        body = self.store.db.execute(
            "SELECT body FROM decisions WHERE name=? AND version=1", (first["id"],)
        ).fetchone()[0]
        self.store.db.execute(
            "INSERT INTO decisions(name,version,body,replaces,replaces_version) "
            "VALUES(?,0,?,NULL,NULL)",
            (first["id"], body),
        )
        with self.assertRaisesRegex(StateError, "lifecycle_decision_history_invalid"):
            self.store.record_lifecycle_plan(lease, plan)

    def test_request_retry_detects_json_type_corruption(self):
        lease = self.store.claim_lifecycle_owner("owner")
        plan = launch_plan("owner", lease["epoch"])
        first = self.store.record_lifecycle_plan(lease, plan)
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
            self.store.record_lifecycle_plan(lease, plan)

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

    def test_plan_input_is_snapshotted_before_waiting_for_sqlite_lock(self):
        lease = self.store.claim_lifecycle_owner("owner")
        other = Store(self.path, clock=lambda: self.now)
        entered_transaction = threading.Event()
        plan = launch_plan("owner", lease["epoch"])
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
                    future = pool.submit(self.store.record_lifecycle_plan, lease, plan)
                    self.assertTrue(entered_transaction.wait(2))
                    plan["reason"] = "mutated while waiting"
                    other.db.execute("COMMIT")
                    receipt = future.result(timeout=2)
            self.assertEqual(receipt["body"]["plan"]["reason"], "all_gates_clear")
        finally:
            if other.db.in_transaction:
                other.db.execute("ROLLBACK")
            other.close()


if __name__ == "__main__":
    unittest.main()
