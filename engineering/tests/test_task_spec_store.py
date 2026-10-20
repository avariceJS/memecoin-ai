import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from engineering.git_gate import VerificationPolicy
from engineering.org_state import StateError, Store
from engineering.verification_plan import VerificationPlanError, prepare_verification_plan

H = "a" * 64
BASE = "b" * 40
POLICY = "c" * 64
SCOPE = ["engineering/org_state.py", "engineering/tests/test_task_spec_store.py"]
SPEC = {
    "schema": "brain-task-spec-v1",
    "id": "PLAT-010",
    "objective_id": "offline-brain-engineering",
    "team": "Platform",
    "title": "Persist a bounded task spec atomically",
    "brief": "Store the validated synthetic task spec as immutable durable state.",
    "base_sha": BASE,
    "scope": SCOPE,
    "dependencies": [],
    "contracts": {"store-api": {"version": 1, "proposal_sha256": H}},
    "risk": "low",
    "capabilities": ["offline_code", "synthetic_tests"],
    "resources": {"max_attempts": 2, "max_turn_seconds": 600, "estimated_wpu": 1},
    "acceptance": [{"id": "atomic-spec-store", "sha256": H}],
    "evidence_refs": ["synthetic-fixture:atomic-store"],
}
ENVELOPE = {
    "schema": "brain-task-envelope-v1",
    "objective_id": SPEC["objective_id"],
    "teams": ["Platform"],
    "allowed_paths": SCOPE,
    "capabilities": ["offline_code", "synthetic_tests"],
    "max_attempts": 3,
    "max_turn_seconds": 600,
    "allowed_contracts": {"store-api": {"1": H}},
    "base_sha": BASE,
    "risk_levels": ["low"],
}
REGISTRY = {
    "atomic-spec-store": {
        "sha256": H,
        "objective_id": SPEC["objective_id"],
        "scope": SCOPE,
        "contracts": SPEC["contracts"],
        "independent": True,
        "frozen": True,
        "risk": "low",
    }
}
ADMISSION = {
    "rationale": "Atomic storage is required before scheduler integration.",
    "evidence": "The existing Store has no canonical task-spec persistence API.",
}
VERIFIER_REGISTRY = {
    "atomic-spec-store": {
        "acceptance_sha256": H,
        "policy_id": "atomic-store-v1",
        "commands": [["/trusted/python", "/trusted/accept.py", "{task_id}", "{checkout}"]],
        "env": {"LANG": "C"},
        "harness_sha256": "d" * 64,
        "result_schema": "org-acceptance-v1",
        "case_field": "case",
        "passed_field": "passed",
    }
}


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
        args = {
            "envelope": copy.deepcopy(ENVELOPE),
            "acceptance_registry": copy.deepcopy(REGISTRY),
            "policy_sha256": POLICY,
            "admission": copy.deepcopy(ADMISSION),
        }
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

    def test_admission_binds_trusted_verifier_and_resolves_exact_decision(self):
        self.assertEqual(
            self.admit(
                verifier_registry=copy.deepcopy(VERIFIER_REGISTRY),
                controller_release_sha256="e" * 64,
                integration_policy_sha256="f" * 64,
            ),
            SPEC["id"],
        )
        binding = self.store.resolve_task_execution_binding(
            SPEC["id"], controller_release_sha256="e" * 64
        )
        self.assertEqual(
            binding["spec_sha256"],
            __import__("hashlib")
            .sha256(json.dumps(SPEC, sort_keys=True, separators=(",", ":")).encode())
            .hexdigest(),
        )
        self.assertEqual(
            binding["verifiers"][0]["commands"], VERIFIER_REGISTRY["atomic-spec-store"]["commands"]
        )
        self.assertEqual(binding["controller_release_sha256"], "e" * 64)

    def test_bound_verification_plan_never_falls_back_to_catalogue_or_executes(self):
        self.admit(
            verifier_registry=copy.deepcopy(VERIFIER_REGISTRY),
            controller_release_sha256="e" * 64,
            integration_policy_sha256="f" * 64,
        )
        task = self.store.get_task(SPEC["id"])
        before_events = self.store.events()
        plan = prepare_verification_plan(
            Path("/synthetic/state"),
            self.store,
            task,
            {"controller_release_sha256": "e" * 64, "integration_policy_sha256": "f" * 64},
        )
        self.assertFalse(plan.execution_qualified)
        self.assertEqual(plan.reason, "verifier_manifest_not_qualified")
        self.assertEqual(plan.decision_name, "admission/task-spec/PLAT-010")
        self.assertEqual(plan.decision_version, 1)
        self.assertEqual(plan.acceptance_identities, (f"atomic-spec-store:{H}",))
        self.assertEqual(self.store.get_task(SPEC["id"])["state"], "pending")
        self.assertEqual(self.store.events(), before_events)

    def test_bound_verification_plan_rejects_missing_binding_without_legacy_fallback(self):
        self.admit()
        with self.assertRaisesRegex(VerificationPlanError, "missing_execution_binding"):
            prepare_verification_plan(
                Path("/synthetic/state"),
                self.store,
                self.store.get_task(SPEC["id"]),
                {
                    "controller_release_sha256": "e" * 64,
                    "integration_policy_sha256": "f" * 64,
                    "python": "/synthetic/python",
                    "tasks": {SPEC["id"]: {"checks": [["must-not-be-used"]]}},
                },
            )

    def test_bound_verification_plan_rejects_malformed_persisted_command(self):
        self.admit(
            verifier_registry=copy.deepcopy(VERIFIER_REGISTRY),
            controller_release_sha256="e" * 64,
            integration_policy_sha256="f" * 64,
        )
        binding = self.store.resolve_task_execution_binding(
            SPEC["id"], controller_release_sha256="e" * 64
        )
        binding["verifiers"][0]["commands"] = [None]
        with patch.object(self.store, "resolve_task_execution_binding", return_value=binding):
            with self.assertRaisesRegex(VerificationPlanError, "bound_verifier_command_invalid"):
                prepare_verification_plan(
                    Path("/synthetic/state"),
                    self.store,
                    self.store.get_task(SPEC["id"]),
                    {
                        "controller_release_sha256": "e" * 64,
                        "integration_policy_sha256": "f" * 64,
                    },
                )

    def test_legacy_verification_plan_keeps_exact_existing_policy_digest(self):
        self.store.add_task("LEGACY", "Platform", "Legacy fixed catalogue")
        task = self.store.get_task("LEGACY")
        cfg = {
            "python": "/synthetic/python",
            "tasks": {"LEGACY": {"checks": [["check", "{checkout}"]]}},
        }
        plan = prepare_verification_plan(Path("/synthetic/state"), self.store, task, cfg)
        expected = VerificationPolicy(
            (
                (
                    "/synthetic/python",
                    "/synthetic/state/trusted/pilot_acceptance.py",
                    "LEGACY",
                    "{checkout}",
                ),
                ("check", "{checkout}"),
            ),
            policy_id="brain-org-pilot-v1:LEGACY",
        )
        self.assertTrue(plan.execution_qualified)
        self.assertEqual(plan.policy.digest, expected.digest)
        self.assertIsNone(plan.binding_sha256)

    def test_execution_binding_rejects_untrusted_command_and_controller_drift(self):
        registry = copy.deepcopy(VERIFIER_REGISTRY)
        registry["atomic-spec-store"]["commands"] = [["{checkout};touch /tmp/pwned"]]
        with self.assertRaisesRegex(StateError, "invalid_verifier_placeholder"):
            self.admit(
                verifier_registry=registry,
                controller_release_sha256="e" * 64,
                integration_policy_sha256="f" * 64,
            )
        self.admit(
            verifier_registry=copy.deepcopy(VERIFIER_REGISTRY),
            controller_release_sha256="e" * 64,
            integration_policy_sha256="f" * 64,
        )
        with self.assertRaisesRegex(StateError, "execution_controller_release_mismatch"):
            self.store.resolve_task_execution_binding(
                SPEC["id"], controller_release_sha256="a" * 64
            )

    def test_execution_binding_rejects_task_and_spec_copy_drift(self):
        self.admit(
            verifier_registry=copy.deepcopy(VERIFIER_REGISTRY),
            controller_release_sha256="e" * 64,
            integration_policy_sha256="f" * 64,
        )
        self.store.db.execute(
            "UPDATE tasks SET dependencies=? WHERE id=?",
            (json.dumps(["OTHER-001"]), SPEC["id"]),
        )
        with self.assertRaisesRegex(StateError, "execution_task_binding_mismatch"):
            self.store.resolve_task_execution_binding(
                SPEC["id"], controller_release_sha256="e" * 64
            )
        self.store.db.execute(
            "UPDATE tasks SET dependencies=? WHERE id=?",
            (json.dumps([]), SPEC["id"]),
        )
        self.store.db.execute(
            "UPDATE task_specs SET body=? WHERE task_id=?",
            (json.dumps({**SPEC, "title": "drifted"}), SPEC["id"]),
        )
        with self.assertRaisesRegex(StateError, "execution_admission_record_mismatch"):
            self.store.resolve_task_execution_binding(
                SPEC["id"], controller_release_sha256="e" * 64
            )

    def test_execution_binding_uses_json_type_exact_comparisons(self):
        self.admit(
            verifier_registry=copy.deepcopy(VERIFIER_REGISTRY),
            controller_release_sha256="e" * 64,
            integration_policy_sha256="f" * 64,
        )
        self.store.db.execute(
            "UPDATE tasks SET contracts=? WHERE id=?",
            (json.dumps({"store-api": True}), SPEC["id"]),
        )
        with self.assertRaisesRegex(StateError, "execution_task_binding_mismatch"):
            self.store.resolve_task_execution_binding(
                SPEC["id"], controller_release_sha256="e" * 64
            )

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
