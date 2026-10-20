import tempfile
import unittest
from pathlib import Path

from engineering.org_state import StateError, Store
from engineering.planning import admit, eligible


def advance_contract(db, name, from_version, to_version, body):
    producer_id = f"contract-producer-{name}-{to_version}"
    db.add_task(producer_id, "Producer", "Contract producer", scope=["contract"])
    attempt = db.claim(producer_id, "synthetic")
    db.submit(attempt["id"], attempt["epoch"], f"candidate-{to_version}", {})
    db.verify(producer_id, f"candidate-{to_version}", True, {})
    db.accept(producer_id, f"candidate-{to_version}")
    db.integrate(producer_id, f"candidate-{to_version}", f"merge-{to_version}")
    transition = db.propose_contract_transition(
        name,
        from_version,
        to_version,
        body,
        producer_task_id=producer_id,
        producer_team="Producer",
        consumer_teams=["Consumer"],
    )
    digest = transition["body"]["contract_hash"]
    db.acknowledge_contract_transition(name, to_version, "Producer", digest)
    db.acknowledge_contract_transition(name, to_version, "Consumer", digest)
    db.activate_contract_transition(name, to_version)


class PlanningTests(unittest.TestCase):
    def test_fixed_envelope_and_duplicate_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Store(Path(tmp) / "state.db")
            self.addCleanup(db.close)
            db.add_objective("offline-brain-engineering", "Offline work")
            cfg = {
                "catalogue": ["NEXT"],
                "tasks": {
                    "NEXT": {
                        "team": "Platform",
                        "title": "Strict input",
                        "brief": "Fixed brief",
                        "scope": ["tools/check.py"],
                        "dependencies": [],
                        "contracts": {},
                    }
                },
            }
            proposal = {
                "card": "NEXT",
                "rationale": "Useful ambiguity rejection",
                "evidence": "Prior integrated CLI exists",
            }
            with self.assertRaises(StateError):
                admit(cfg, db, {**proposal, "checks": ["evil"]}, "base")
            with self.assertRaises(StateError):
                admit(cfg, db, {**proposal, "card": "UNAUTHORIZED"}, "base")
            self.assertEqual(eligible(cfg, db), ["NEXT"])
            self.assertEqual(admit(cfg, db, proposal, "base"), "NEXT")
            self.assertEqual(db.get_task("NEXT")["scope"], ["tools/check.py"])
            with self.assertRaises(StateError):
                admit(cfg, db, proposal, "base")

    def test_dependent_work_not_admitted_early(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Store(Path(tmp) / "state.db")
            self.addCleanup(db.close)
            cfg = {
                "catalogue": ["NEXT"],
                "tasks": {"NEXT": {"dependencies": ["producer"], "contracts": {}}},
            }
            self.assertEqual(eligible(cfg, db), [])


class PlanningAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Store(self.root / "state.sqlite")
        self.addCleanup(self.db.close)
        self.db.add_objective("offline-brain-engineering", "Bounded offline work")
        self.cfg = {
            "catalogue": ["PLAT-002"],
            "tasks": {
                "PLAT-002": {
                    "team": "Platform",
                    "title": "Reject ambiguous JSON",
                    "brief": "Fixed approved work",
                    "scope": ["tools/check.py", "tests/test_check.py"],
                    "checks": [["trusted-python", "fixed-test"]],
                    "dependencies": [],
                    "contracts": {},
                }
            },
        }
        self.proposal = {
            "card": "PLAT-002",
            "rationale": "Useful ambiguity rejection",
            "evidence": "Prior integrated CLI evidence",
        }

    def test_injected_crash_after_task_event_rolls_back_task_and_decision(self):
        from unittest.mock import patch

        original = self.db._event

        def crash(kind, *args):
            original(kind, *args)
            if kind == "task_added":
                raise RuntimeError("crash before admission decision")

        before = self.db.events()
        with patch.object(self.db, "_event", side_effect=crash):
            with self.assertRaises(RuntimeError):
                admit(self.cfg, self.db, self.proposal, "base")
        self.assertEqual(self.db.list_tasks(), [])
        self.assertIsNone(self.db.get_decision("admission/PLAT-002"))
        self.assertEqual(self.db.events(), before)
        self.assertEqual(admit(self.cfg, self.db, self.proposal, "base"), "PLAT-002")

    def test_crash_after_decision_insert_also_rolls_back_journal_and_task(self):
        from unittest.mock import patch

        original = self.db._event

        def crash(kind, *args):
            original(kind, *args)
            if kind == "decision_recorded":
                raise RuntimeError("crash before commit")

        before = self.db.events()
        with patch.object(self.db, "_event", side_effect=crash):
            with self.assertRaises(RuntimeError):
                admit(self.cfg, self.db, self.proposal, "base")
        self.assertEqual(self.db.list_tasks(), [])
        self.assertIsNone(self.db.get_decision("admission/PLAT-002"))
        self.assertEqual(self.db.events(), before)

    def test_concurrent_duplicate_admission_has_one_task_one_decision(self):
        from concurrent.futures import ThreadPoolExecutor

        alternate = Store(self.root / "state.sqlite")
        try:

            def run(db):
                try:
                    return admit(self.cfg, db, self.proposal, "base")
                except StateError:
                    return None

            with ThreadPoolExecutor(2) as pool:
                results = list(pool.map(run, [self.db, alternate]))
            self.assertEqual(results.count("PLAT-002"), 1)
            self.assertEqual(len(self.db.list_tasks()), 1)
            decisions = [e for e in self.db.events() if e["entity"] == "admission/PLAT-002"]
            self.assertEqual(len(decisions), 1)
        finally:
            alternate.close()

    def test_malformed_or_capability_expanding_proposals_leave_no_records(self):
        malformed = [
            None,
            [],
            "PLAT-002",
            {},
            {**self.proposal, "card": []},
            {**self.proposal, "card": None},
            {**self.proposal, "rationale": " " * 20},
            {**self.proposal, "evidence": "x" * 4001},
            {**self.proposal, "scope": ["/Users"]},
            {**self.proposal, "checks": [["arbitrary"]]},
            {**self.proposal, "objective_id": "unapproved-objective"},
            {**self.proposal, "card": "UNAPPROVED"},
        ]
        for proposal in malformed:
            with self.subTest(proposal=proposal), self.assertRaises(StateError):
                admit(self.cfg, self.db, proposal, "base")
        self.assertEqual(self.db.list_tasks(), [])
        self.assertIsNone(self.db.get_decision("admission/PLAT-002"))

    def test_admission_copies_only_frozen_card_and_hardcoded_objective(self):
        admit(self.cfg, self.db, self.proposal, "immutable-base")
        task = self.db.get_task("PLAT-002")
        self.assertEqual(task["objective_id"], "offline-brain-engineering")
        self.assertEqual(task["scope"], ["tools/check.py", "tests/test_check.py"])
        self.assertEqual(task["brief"], "Fixed approved work")
        self.assertEqual(task["base_sha"], "immutable-base")
        self.assertEqual(task["max_attempts"], 3)
        decision = self.db.get_decision("admission/PLAT-002")
        self.assertEqual(decision["body"], {"proposal": self.proposal, "base": "immutable-base"})

    def test_missing_or_superseded_contract_is_ineligible_without_exception(self):
        self.cfg["tasks"]["PLAT-002"]["contracts"] = {"api": 2}
        self.assertEqual(eligible(self.cfg, self.db), [])
        self.db.put_contract("api", 1, {})
        self.assertEqual(eligible(self.cfg, self.db), [])
        advance_contract(self.db, "api", 1, 2, {})
        self.assertEqual(eligible(self.cfg, self.db), ["PLAT-002"])
        advance_contract(self.db, "api", 2, 3, {})
        self.assertEqual(eligible(self.cfg, self.db), [])
        with self.assertRaises(StateError):
            admit(self.cfg, self.db, self.proposal, "base")

    def test_accepted_dependency_is_insufficient_until_integrated(self):
        self.cfg["tasks"]["PLAT-002"]["dependencies"] = ["PLAT-001"]
        self.db.add_task("PLAT-001", "Platform", "Producer")
        attempt = self.db.claim("PLAT-001", "worker")
        self.db.submit(attempt["id"], attempt["epoch"], "candidate", {})
        self.db.verify("PLAT-001", "candidate", True, {})
        self.db.accept("PLAT-001", "candidate")
        self.assertEqual(eligible(self.cfg, self.db), [])
        self.db.integrate("PLAT-001", "candidate", "merged")
        self.assertEqual(eligible(self.cfg, self.db), ["PLAT-002"])
        admit(self.cfg, self.db, self.proposal, "base")
        self.assertEqual(self.db.get_task("PLAT-002")["dependencies"], ["PLAT-001"])

    def planning(self, text):
        from unittest.mock import MagicMock, patch

        from engineering import planning

        fake = MagicMock()
        fake.__enter__.return_value = fake
        fake.start.return_value = "fake-sol-thread"
        fake.turn.return_value = {"text": text}
        return (
            fake,
            patch.object(planning, "config", return_value=self.cfg),
            patch.object(planning, "metered_client", return_value=fake),
            patch.object(planning, "git", return_value="base"),
            patch.object(planning, "log_event"),
        )

    def test_sol_selects_fixed_card_and_restart_never_reissues_admission(self):
        import json

        from engineering.planning import plan_next

        fake, cfg, codex, git, log = self.planning(json.dumps(self.proposal))
        with cfg, codex, git, log:
            self.assertEqual(plan_next(self.root, self.db), "PLAT-002")
            self.assertIsNone(plan_next(self.root, self.db))
        fake.turn.assert_called_once()
        self.assertEqual(len(self.db.list_tasks()), 1)

    def test_rejected_generated_policy_is_persisted_and_not_retried_for_same_dossier(self):
        import json

        from engineering.planning import plan_next

        fake, cfg, codex, git, log = self.planning(json.dumps({**self.proposal, "scope": ["/"]}))
        with cfg, codex, git, log:
            self.assertIsNone(plan_next(self.root, self.db))
            self.assertIsNone(plan_next(self.root, self.db))
        fake.turn.assert_called_once()
        self.assertEqual(self.db.list_tasks(), [])
        decisions = [e for e in self.db.events() if e["entity"].startswith("planning/")]
        self.assertEqual(decisions[-1]["body"]["body"]["status"], "rejected")

    def test_malformed_json_is_rejection_not_uncaught_model_response(self):
        from engineering.planning import plan_next

        fake, cfg, codex, git, log = self.planning("not JSON")
        with cfg, codex, git, log:
            self.assertIsNone(plan_next(self.root, self.db))
            self.assertIsNone(plan_next(self.root, self.db))
        fake.turn.assert_called_once()
        self.assertEqual(self.db.list_tasks(), [])

    def test_deferred_choice_is_bounded_and_preserves_objective(self):
        import json

        from engineering.planning import plan_next

        fake, cfg, codex, git, log = self.planning(json.dumps({**self.proposal, "card": None}))
        with cfg, codex, git, log:
            self.assertIsNone(plan_next(self.root, self.db))
            self.assertIsNone(plan_next(self.root, self.db))
        fake.turn.assert_called_once()
        self.assertEqual(self.db.list_tasks(), [])
        decisions = [e for e in self.db.events() if e["entity"].startswith("planning/")]
        self.assertEqual(decisions[-1]["body"]["body"]["status"], "deferred")

    def test_interrupted_model_episode_is_not_retried_with_unchanged_evidence(self):
        from engineering.planning import plan_next

        fake, cfg, codex, git, log = self.planning("unused")
        fake.turn.side_effect = RuntimeError("simulated app-server loss")
        with cfg, codex, git, log:
            self.assertIsNone(plan_next(self.root, self.db))
            self.assertIsNone(plan_next(self.root, self.db))
        fake.turn.assert_called_once()
        decisions = [e for e in self.db.events() if e["entity"].startswith("planning/")]
        self.assertEqual(decisions[0]["body"]["body"]["status"], "requested")
        self.assertEqual(decisions[-1]["body"]["body"]["status"], "interrupted")
        self.assertTrue(decisions[-1]["body"]["body"]["requires_executive_review"])

    def test_requested_episode_is_durable_before_model_call_and_survives_crash(self):
        from engineering.planning import plan_next

        fake, cfg, codex, git, log = self.planning("unused")

        def crash(*args, **kwargs):
            decisions = [e for e in self.db.events() if e["entity"].startswith("planning/")]
            self.assertEqual(decisions[-1]["body"]["body"]["status"], "requested")
            raise KeyboardInterrupt("simulated hard interruption")

        fake.turn.side_effect = crash
        with cfg, codex, git, log:
            with self.assertRaises(KeyboardInterrupt):
                plan_next(self.root, self.db)
            self.assertIsNone(plan_next(self.root, self.db))
        fake.turn.assert_called_once()
        self.assertEqual(self.db.list_tasks(), [])

    def test_concurrent_planner_cannot_issue_second_episode_while_first_is_running(self):
        import json
        import threading
        from concurrent.futures import ThreadPoolExecutor

        from engineering.planning import plan_next

        entered, finish = threading.Event(), threading.Event()
        fake, cfg, codex, git, log = self.planning("unused")

        def response(*args, **kwargs):
            entered.set()
            if not finish.wait(2):
                raise AssertionError("test did not release fake model")
            return {"text": json.dumps(self.proposal)}

        fake.turn.side_effect = response
        alternate = Store(self.root / "state.sqlite")
        try:
            with cfg, codex, git, log, ThreadPoolExecutor(1) as pool:
                first = pool.submit(plan_next, self.root, self.db)
                try:
                    self.assertTrue(entered.wait(2))
                    self.assertIsNone(plan_next(self.root, alternate))
                finally:
                    finish.set()
                self.assertEqual(first.result(timeout=2), "PLAT-002")
            fake.turn.assert_called_once()
        finally:
            alternate.close()
