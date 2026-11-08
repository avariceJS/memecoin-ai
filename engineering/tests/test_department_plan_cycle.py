import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engineering import department_plan_cycle
from engineering.executive import Executive
from engineering.org_state import Store


class DepartmentPlanCycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.db_path = self.state / "organization.sqlite"
        self.now = 1000.0
        self.db = Store(self.db_path, clock=lambda: self.now)
        self.addCleanup(self.db.close)
        self.db.add_objective("offline-brain-engineering", "Synthetic objective")
        self.db.add_task("unrelated-ready", "Ops", "Existing ready work")
        self.cfg = {
            "catalogue": ["EXP-1", "PLAT-1"],
            "tasks": {
                "EXP-1": {
                    "team": "Experience",
                    "title": "Producer capability",
                    "brief": "Add one bounded producer feature.",
                    "scope": ["src/producer.py"],
                    "dependencies": [],
                    "contracts": {},
                },
                "PLAT-1": {
                    "team": "Platform",
                    "title": "Consumer capability",
                    "brief": "Consume one integrated producer feature.",
                    "scope": ["src/consumer.py"],
                    "dependencies": [],
                    "contracts": {},
                },
            },
        }
        self.config_patch = patch.object(department_plan_cycle, "config", return_value=self.cfg)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def transport(self, request):
        card_id = sorted(request["cards"])[0]
        return {
            "summary": "Advance the fixed synthetic objective.",
            "rationale": "The trusted card is eligible in the current Store snapshot.",
            "priorities": ["catalogue-card:" + card_id],
        }

    def only_experience(self):
        self.cfg["catalogue"] = ["EXP-1"]
        self.cfg["tasks"] = {"EXP-1": self.cfg["tasks"]["EXP-1"]}

    def test_controller_creates_both_fenced_plans_from_current_cards_idempotently(self):
        calls = []

        def transport(request):
            calls.append(request)
            return self.transport(request)

        self.assertTrue(
            department_plan_cycle.ensure_department_plans(
                self.state, self.db, transport, stop_guard=lambda: True
            )
        )
        self.assertEqual([request["team"] for request in calls], ["Experience", "Platform"])
        self.assertEqual(
            [request["model"] for request in calls],
            ["gpt-6.1-sol", "gpt-6.1-sol"],
        )
        nominations = Executive(self.db).department_plan_nominations(
            {"EXP-1": "Experience", "PLAT-1": "Platform"},
            "offline-brain-engineering",
        )["nominations"]
        self.assertEqual(set(nominations), {"EXP-1", "PLAT-1"})
        self.assertEqual(nominations["EXP-1"]["owner"], department_plan_cycle.PLAN_OWNER)
        self.assertEqual(nominations["PLAT-1"]["owner"], department_plan_cycle.PLAN_OWNER)
        self.assertEqual(self.db.get_task("unrelated-ready")["state"], "pending")
        self.assertFalse(
            department_plan_cycle.ensure_department_plans(
                self.state,
                self.db,
                lambda _request: self.fail("fresh plans must not be resent"),
                stop_guard=lambda: True,
            )
        )
        statuses = [
            self.db.get_decision(row[0])["body"]["status"]
            for row in self.db.db.execute(
                "SELECT DISTINCT name FROM decisions WHERE name GLOB 'department-plan-cycle/*'"
            )
        ]
        self.assertEqual(statuses, ["planned", "planned"])

    def test_response_custody_replays_after_hard_interruption_without_transport(self):
        self.only_experience()
        original = Executive.record_department_plan

        def interrupt_after_custody(_executive, *_args, **_kwargs):
            raise KeyboardInterrupt("synthetic crash after response custody")

        with (
            patch.object(Executive, "record_department_plan", side_effect=interrupt_after_custody),
            self.assertRaises(KeyboardInterrupt),
        ):
            department_plan_cycle.ensure_department_plans(
                self.state,
                self.db,
                self.transport,
                stop_guard=lambda: True,
            )
        journal = self.db.db.execute(
            "SELECT name FROM decisions WHERE name GLOB 'department-plan-cycle/*'"
        ).fetchone()[0]
        self.assertEqual(self.db.get_decision(journal)["body"]["status"], "response_recorded")
        self.db.close()
        self.db = Store(self.db_path, clock=lambda: self.now)
        with patch.object(Executive, "record_department_plan", original):
            self.assertTrue(
                department_plan_cycle.ensure_department_plans(
                    self.state,
                    self.db,
                    lambda _request: self.fail("custodied response must replay locally"),
                    stop_guard=lambda: True,
                )
            )
        self.assertEqual(self.db.get_decision(journal)["body"]["status"], "planned")
        self.assertIsNotNone(Executive(self.db).get_department_plan("Experience"))

    def test_plan_commit_before_terminal_marker_replays_idempotently_after_restart(self):
        self.only_experience()
        original_append = department_plan_cycle._append

        def interrupt_before_planned_marker(db, request_id, body, *, expected_status=None):
            if expected_status == "response_recorded" and body.get("status") == "planned":
                raise KeyboardInterrupt("synthetic crash after plan commit")
            return original_append(db, request_id, body, expected_status=expected_status)

        with (
            patch.object(
                department_plan_cycle, "_append", side_effect=interrupt_before_planned_marker
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            department_plan_cycle.ensure_department_plans(
                self.state, self.db, self.transport, stop_guard=lambda: True
            )
        row = self.db.db.execute(
            "SELECT name FROM decisions WHERE name GLOB 'department-plan-cycle/*'"
        ).fetchone()
        journal = row[0]
        self.assertEqual(self.db.get_decision(journal)["body"]["status"], "response_recorded")
        self.assertIsNotNone(Executive(self.db).get_department_plan("Experience"))
        self.db.close()
        self.db = Store(self.db_path, clock=lambda: self.now)
        with patch.object(department_plan_cycle, "_append", wraps=original_append):
            self.assertTrue(
                department_plan_cycle.ensure_department_plans(
                    self.state,
                    self.db,
                    lambda _request: self.fail("committed department plan must replay locally"),
                    stop_guard=lambda: True,
                )
            )
        self.assertEqual(self.db.get_decision(journal)["body"]["status"], "planned")
        self.assertEqual(Executive(self.db).get_department_plan("Experience")["revision"], 1)

    def test_unknown_transport_outcome_is_held_without_resend(self):
        self.only_experience()
        calls = []

        def interrupted(_request):
            calls.append("called")
            raise RuntimeError("synthetic ambiguous transport outcome")

        self.assertFalse(
            department_plan_cycle.ensure_department_plans(
                self.state, self.db, interrupted, stop_guard=lambda: True
            )
        )
        self.assertFalse(
            department_plan_cycle.ensure_department_plans(
                self.state,
                self.db,
                lambda _request: calls.append("resent"),
                stop_guard=lambda: True,
            )
        )
        self.assertEqual(calls, ["called"])
        row = self.db.db.execute(
            "SELECT name FROM decisions WHERE name GLOB 'department-plan-cycle/*'"
        ).fetchone()
        record = self.db.get_decision(row[0])["body"]
        self.assertEqual(record["status"], "outcome_unknown")
        self.assertEqual(record["usage"], "unknown")
        self.assertIsNone(Executive(self.db).get_department_plan("Experience"))

    def test_stop_after_intent_cancels_before_transport_then_can_retry(self):
        self.only_experience()
        checks = iter((True, True, False))
        calls = []
        self.assertFalse(
            department_plan_cycle.ensure_department_plans(
                self.state,
                self.db,
                lambda request: calls.append(request),
                stop_guard=lambda: next(checks),
            )
        )
        self.assertEqual(calls, [])
        row = self.db.db.execute(
            "SELECT name FROM decisions WHERE name GLOB 'department-plan-cycle/*'"
        ).fetchone()
        request_id = row[0].removeprefix(department_plan_cycle.DISPATCH_PREFIX)
        self.assertEqual(
            self.db.get_decision(row[0])["body"]["status"], "cancelled_before_dispatch"
        )
        self.assertTrue(
            department_plan_cycle.ensure_department_plans(
                self.state, self.db, self.transport, stop_guard=lambda: True
            )
        )
        self.assertEqual(self.db.get_decision(row[0])["body"]["status"], "planned")
        self.assertTrue(request_id)

    def test_losing_concurrent_intent_creator_never_dispatches_duplicate_request(self):
        self.only_experience()
        original_append = department_plan_cycle._append
        raced = False

        def create_as_winner_then_lose(db, request_id, body, *, expected_status=None):
            nonlocal raced
            if expected_status is None and not raced:
                raced = True
                original_append(db, request_id, body)
            return original_append(db, request_id, body, expected_status=expected_status)

        with patch.object(department_plan_cycle, "_append", side_effect=create_as_winner_then_lose):
            self.assertFalse(
                department_plan_cycle.ensure_department_plans(
                    self.state,
                    self.db,
                    lambda _request: self.fail("losing controller must not dispatch"),
                    stop_guard=lambda: True,
                )
            )
        row = self.db.db.execute(
            "SELECT name FROM decisions WHERE name GLOB 'department-plan-cycle/*'"
        ).fetchone()
        self.assertEqual(self.db.get_decision(row[0])["body"]["status"], "dispatching")

    def test_stale_menu_before_dispatch_cancels_intent_without_transport(self):
        self.only_experience()
        calls = []
        with patch.object(department_plan_cycle, "_currentness", return_value=False):
            self.assertFalse(
                department_plan_cycle.ensure_department_plans(
                    self.state,
                    self.db,
                    lambda request: calls.append(request),
                    stop_guard=lambda: True,
                )
            )
        row = self.db.db.execute(
            "SELECT name FROM decisions WHERE name GLOB 'department-plan-cycle/*'"
        ).fetchone()
        self.assertEqual(calls, [])
        self.assertEqual(
            self.db.get_decision(row[0])["body"]["status"], "cancelled_before_dispatch"
        )

    def test_out_of_menu_response_is_custodied_then_rejected(self):
        self.only_experience()
        response = {
            "summary": "Select a card owned by a different team.",
            "rationale": "The transport cannot amend trusted assignment.",
            "priorities": ["catalogue-card:PLAT-1"],
        }
        self.assertFalse(
            department_plan_cycle.ensure_department_plans(
                self.state,
                self.db,
                lambda _request: response,
                stop_guard=lambda: True,
            )
        )
        row = self.db.db.execute(
            "SELECT name FROM decisions WHERE name GLOB 'department-plan-cycle/*'"
        ).fetchone()
        self.assertEqual(self.db.get_decision(row[0])["body"]["status"], "rejected")
        self.assertIsNone(Executive(self.db).get_department_plan("Experience"))

    def test_stale_menu_during_transport_never_commits_a_plan(self):
        self.only_experience()

        def changed_menu(_request):
            self.db.add_task("EXP-1", "Experience", "Unexpected direct insertion")
            return self.transport(
                {
                    "team": "Experience",
                    "cards": {"EXP-1": {"team": "Experience"}},
                }
            )

        self.assertFalse(
            department_plan_cycle.ensure_department_plans(
                self.state, self.db, changed_menu, stop_guard=lambda: True
            )
        )
        row = self.db.db.execute(
            "SELECT name FROM decisions WHERE name GLOB 'department-plan-cycle/*'"
        ).fetchone()
        self.assertEqual(self.db.get_decision(row[0])["body"]["status"], "response_recorded")
        self.assertIsNone(Executive(self.db).get_department_plan("Experience"))

    def test_scope_only_menu_drift_invalidates_custodied_response(self):
        self.only_experience()

        def changed_scope(request):
            self.cfg["tasks"]["EXP-1"]["scope"] = ["src/changed.py"]
            return self.transport(request)

        self.assertFalse(
            department_plan_cycle.ensure_department_plans(
                self.state, self.db, changed_scope, stop_guard=lambda: True
            )
        )
        row = self.db.db.execute(
            "SELECT name FROM decisions WHERE name GLOB 'department-plan-cycle/*'"
        ).fetchone()
        self.assertEqual(self.db.get_decision(row[0])["body"]["status"], "response_recorded")
        self.assertIsNone(Executive(self.db).get_department_plan("Experience"))

    def test_store_revision_race_before_plan_commit_is_rejected(self):
        self.only_experience()
        original_record = Executive.record_department_plan

        def mutate_after_menu_check(executive, *args, **kwargs):
            self.db.add_task("raced-store-change", "Ops", "Concurrent Store change")
            return original_record(executive, *args, **kwargs)

        with patch.object(Executive, "record_department_plan", side_effect=mutate_after_menu_check):
            self.assertFalse(
                department_plan_cycle.ensure_department_plans(
                    self.state, self.db, self.transport, stop_guard=lambda: True
                )
            )
        row = self.db.db.execute(
            "SELECT name FROM decisions WHERE name GLOB 'department-plan-cycle/*'"
        ).fetchone()
        self.assertEqual(self.db.get_decision(row[0])["body"]["status"], "rejected")
        self.assertIsNone(Executive(self.db).get_department_plan("Experience"))

    def test_expired_lease_is_replanned_before_review_deadline(self):
        self.only_experience()
        self.assertTrue(
            department_plan_cycle.ensure_department_plans(
                self.state, self.db, self.transport, stop_guard=lambda: True
            )
        )
        first_plan = Executive(self.db).get_department_plan("Experience")
        first_lease = Executive(self.db).get_department_lease("Experience")
        self.now = first_lease["lease_until"] + 1
        self.assertLess(self.now, first_plan["review_at"])
        calls = []
        self.assertTrue(
            department_plan_cycle.ensure_department_plans(
                self.state,
                self.db,
                lambda request: calls.append(request) or self.transport(request),
                stop_guard=lambda: True,
            )
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(Executive(self.db).get_department_plan("Experience")["revision"], 2)

    def test_expired_lease_rejects_custodied_plan_response(self):
        self.only_experience()

        def expire_lease(request):
            self.now = request["lease"]["lease_until"] + 1
            return self.transport(request)

        self.assertFalse(
            department_plan_cycle.ensure_department_plans(
                self.state, self.db, expire_lease, stop_guard=lambda: True
            )
        )
        row = self.db.db.execute(
            "SELECT name FROM decisions WHERE name GLOB 'department-plan-cycle/*'"
        ).fetchone()
        self.assertEqual(self.db.get_decision(row[0])["body"]["status"], "rejected")
        self.assertIsNone(Executive(self.db).get_department_plan("Experience"))


if __name__ == "__main__":
    unittest.main()
