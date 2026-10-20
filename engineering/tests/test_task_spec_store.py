import copy
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from engineering.org_state import StateError, Store

H = "a" * 64
BASE = "b" * 40
POLICY = "c" * 64
SCOPE = ["engineering/org_state.py", "engineering/tests/test_task_spec_store.py"]
SPEC = {
    "schema": "brain-task-spec-v1", "id": "PLAT-010",
    "objective_id": "offline-brain-engineering", "team": "Platform",
    "title": "Persist a bounded task spec atomically",
    "brief": "Store the validated synthetic task spec as immutable durable state.",
    "base_sha": BASE, "scope": SCOPE, "dependencies": [],
    "contracts": {"store-api": {"version": 1, "proposal_sha256": H}},
    "risk": "low", "capabilities": ["offline_code", "synthetic_tests"],
    "resources": {"max_attempts": 2, "max_turn_seconds": 600, "estimated_wpu": 1},
    "acceptance": [{"id": "atomic-spec-store", "sha256": H}],
    "evidence_refs": ["synthetic-fixture:atomic-store"],
}
ENVELOPE = {"schema": "brain-task-envelope-v1", "objective_id": SPEC["objective_id"],
    "teams": ["Platform"], "allowed_paths": SCOPE,
    "capabilities": ["offline_code", "synthetic_tests"], "max_attempts": 3,
    "max_turn_seconds": 600, "allowed_contracts": {"store-api": {"1": H}},
    "base_sha": BASE, "risk_levels": ["low"]}
REGISTRY = {"atomic-spec-store": {"sha256": H, "objective_id": SPEC["objective_id"],
    "scope": SCOPE, "contracts": SPEC["contracts"], "independent": True,
    "frozen": True, "risk": "low"}}
ADMISSION = {"rationale": "Atomic storage is required before scheduler integration.",
    "evidence": "The existing Store has no canonical task-spec persistence API."}

class TaskSpecStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.sqlite"
        self.store = Store(self.path)
        self.store.add_objective(SPEC["objective_id"], "Synthetic objective")
        self.store.put_contract("store-api", 1, {"synthetic": True})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def admit(self, **kwargs):
        args = {"envelope": copy.deepcopy(ENVELOPE),
            "acceptance_registry": copy.deepcopy(REGISTRY), "policy_sha256": POLICY,
            "admission": copy.deepcopy(ADMISSION)}
        args.update(kwargs)
        return self.store.admit_task_spec(copy.deepcopy(SPEC), **args)

    def test_admission_persists_canonical_snapshot_and_detached_getter(self):
        self.assertEqual(self.admit(), SPEC["id"])
        saved = self.store.get_task_spec(SPEC["id"])
        self.assertEqual(saved["spec"], SPEC)
        self.assertEqual(saved["decision"], {"name": "admission/task-spec/PLAT-010", "version": 1})
        self.assertEqual(self.store.get_task(SPEC["id"])["acceptance"], [f"atomic-spec-store:{H}"])
        saved["spec"]["scope"].clear()
        self.assertEqual(self.store.get_task_spec(SPEC["id"])["spec"], SPEC)

    def test_rejections_leave_database_unchanged(self):
        before = self.store.events()
        with self.assertRaisesRegex(StateError, "invalid_task_spec_policy_hash"):
            self.admit(policy_sha256="C" * 64)
        with self.assertRaisesRegex(StateError, "invalid_task_spec_admission"):
            self.admit(admission={"rationale": "short", "evidence": ADMISSION["evidence"]})
        self.assertEqual(self.store.events(), before)

    def test_trigger_failure_rolls_back_all_admission_rows_and_events(self):
        self.store.db.execute(
            """CREATE TRIGGER reject_spec BEFORE INSERT ON task_specs
            BEGIN SELECT RAISE(ABORT, 'synthetic'); END"""
        )
        before = self.store.events()
        with self.assertRaises(sqlite3.IntegrityError):
            self.admit()
        self.assertEqual(self.store.list_tasks(), [])
        self.assertIsNone(self.store.get_task_spec(SPEC["id"]))
        self.assertEqual(self.store.events(), before)

    def test_admission_metadata_is_detached_before_waiting_for_write_lock(self):
        writer = Store(self.path)
        writer.db.execute("BEGIN IMMEDIATE")
        validation_done = threading.Event()
        release = threading.Event()
        original_tx = self.store._tx
        admission = copy.deepcopy(ADMISSION)
        errors = []

        @contextmanager
        def gated_tx():
            validation_done.set()
            if not release.wait(5):
                raise AssertionError("test timed out before releasing admission")
            with original_tx():
                yield

        def admit():
            try:
                self.store.admit_task_spec(
                    copy.deepcopy(SPEC),
                    envelope=copy.deepcopy(ENVELOPE),
                    acceptance_registry=copy.deepcopy(REGISTRY),
                    policy_sha256=POLICY,
                    admission=admission,
                )
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=admit, daemon=True)
        with patch.object(self.store, "_tx", gated_tx):
            thread.start()
            self.assertTrue(validation_done.wait(3), "admission did not reach the write lock")
            admission["rationale"] = "x"
            admission["commands"] = [["untrusted"]]
            release.set()
            writer.db.execute("COMMIT")
            thread.join(10)

        self.assertFalse(thread.is_alive(), "admission thread did not finish")
        writer.close()
        self.assertEqual(errors, [])
        saved = self.store.get_task_spec(SPEC["id"])
        self.assertEqual(saved["admission"], ADMISSION)
        decision = self.store.get_decision("admission/task-spec/" + SPEC["id"], 1)
        self.assertEqual(decision["body"]["admission"], ADMISSION)

if __name__ == "__main__":
    unittest.main()
