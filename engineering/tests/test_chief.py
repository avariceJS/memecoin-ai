"""Executive episodes recover durable responsibility; no model service required."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engineering import chief
from engineering.executive import Executive, StaleExecutive
from engineering.org_state import Store

EPISODE_RESPONSE = {
    "summary": "Отделы продолжают разрешённую работу.",
    "rationale": "Обязательство сохраняется в SQLite.",
    "next_horizon": "Проверить независимую приёмку.",
    "limitations": "Длительная работа не измерена.",
}


class ChiefTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.db = Store(self.path / "organization.sqlite")
        self.addCleanup(self.db.close)
        self.executive = Executive(self.db)
        self.db.add_objective("offline", "Offline work")
        self.db.add_task("T", "Experience", "Bounded work", objective_id="offline")
        prior = self.executive.claim("previous-director")
        self.executive.obligation(prior, "O", "Keep responsibility", objective_id="offline")
        self.executive.release(prior)
        self.render_patch = patch.object(
            chief,
            "render_desk",
            side_effect=lambda state, db: (state / "OWNER-DESK.md").write_text(
                Executive(db).render_report()
            ),
        )
        self.render_patch.start()
        self.addCleanup(self.render_patch.stop)

    def test_fresh_episodes_reconstruct_obligations_without_changing_tasks(self):
        before = self.db.list_tasks()
        first = chief.chief(self.path, trigger="replacement")
        second = chief.chief(self.path, trigger="accepted-milestone")
        self.assertEqual((first["epoch"], second["epoch"]), (2, 3))
        self.assertEqual(first["status"], "blocked")
        for obligation_id in ("ORG-ACCEPT-01", "ORG-CONTINUE-01", "ORG-LONGRUN-01"):
            obligation = self.executive.get_obligation(obligation_id)
            self.assertEqual(obligation["status"], "open")
            self.assertEqual(obligation["created_epoch"], 2)
        self.assertEqual(self.db.list_tasks(), before)
        self.assertEqual(self.executive.get_obligation("O")["status"], "open")
        self.assertEqual(self.executive.snapshot()["role"]["status"], "released")
        self.assertIsNone(self.executive.get_decision("executive-episode-3"))
        projection = json.loads((self.path / "chief.json").read_text())
        self.assertEqual(projection["status"], "blocked")
        self.assertNotIn("pid", projection)

    def test_manual_inference_is_blocked_without_recording_decision(self):
        result = chief.chief(self.path)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.executive.snapshot()["role"]["status"], "released")
        self.assertIsNone(self.executive.get_decision("executive-episode-2"))
        self.assertEqual(self.executive.get_obligation("O")["status"], "open")

    def test_active_director_and_stop_marker_do_not_start_new_model(self):
        active = self.executive.claim("live-director")
        self.assertEqual(chief.chief(self.path)["status"], "executive_busy")
        (self.path / "STOP").touch()
        self.assertEqual(chief.chief(self.path)["status"], "stopped")
        self.assertEqual(self.executive.snapshot()["role"]["epoch"], active["epoch"])

    def test_blocked_inference_does_not_cancel_already_authorized_work(self):
        attempt = self.db.claim("T", "luna", 120)
        result = chief.chief(self.path)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(self.db.get_task("T")["active_attempt"], attempt["id"])
        self.assertEqual(self.db.get_task("T")["state"], "running")
        self.assertEqual(self.executive.snapshot()["role"]["status"], "released")

    def test_exhausted_catalogue_persists_obligation_without_model_inference(self):
        result = chief.chief(self.path, trigger="catalogue-exhausted")
        self.assertEqual(result["status"], "blocked")
        obligation = self.executive.get_obligation("next-policy-horizon")
        self.assertEqual(obligation["status"], "open")
        self.assertEqual(obligation["owner_role"], "CEO")
        chief.chief(self.path, trigger="catalogue-exhausted")
        self.assertEqual(self.executive.get_obligation("next-policy-horizon"), obligation)
        self.assertIn("next-policy-horizon", (self.path / "OWNER-DESK.md").read_text())

    def test_blocked_dependency_boundary_preserves_specific_owned_obligation(self):
        self.db.add_task("consumer", "Platform", "Dependent work", dependencies=["T"])
        chief.chief(self.path, trigger="blocked-dependencies")
        rows = [
            row
            for row in self.executive.snapshot()["obligations"]
            if row["id"].startswith("blocked-dependencies/")
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "blocked")
        self.assertIn("task:consumer", rows[0]["blocked_by"])
        self.assertEqual(self.db.get_task("consumer")["state"], "pending")

    def test_dossier_bound_preserves_authority_and_total_counts(self):
        packet = {
            "authority": {"main": "unchanged"},
            "organization": {
                "counts": {"obligations": 100},
                "obligations": [
                    {"id": str(index), "rationale": "x" * 4000, "refs": ["r" * 500] * 50}
                    for index in range(100)
                ],
                "events": [],
            },
        }
        bounded = chief.bounded_dossier(packet)
        self.assertLessEqual(len(json.dumps(bounded, ensure_ascii=False, sort_keys=True)), 24000)
        self.assertEqual(bounded["authority"]["main"], "unchanged")
        self.assertEqual(bounded["organization"]["counts"]["obligations"], 100)
        self.assertEqual(len(packet["organization"]["obligations"]), 100)
        self.assertIn("projection_limits", bounded)

    def test_expired_lease_cannot_record_or_overwrite_replacement_projection(self):
        clock = [1000.0]
        self.db.clock = lambda: clock[0]
        lease = self.executive.claim("expired-director")
        self.executive.release(lease)
        replacement = self.executive.claim("replacement")
        (self.path / "chief.json").write_text(json.dumps({"epoch": replacement["epoch"]}))
        with self.assertRaises(StaleExecutive):
            self.executive.decision(lease, "stale", "stale output")
        self.assertIsNone(self.executive.get_decision("stale"))
        self.assertEqual(self.executive.snapshot()["role"]["owner"], "replacement")
        self.assertEqual(
            json.loads((self.path / "chief.json").read_text()), {"epoch": replacement["epoch"]}
        )

    def test_schema_rejects_overlong_empty_and_nonstring_decisions(self):
        for bad in ("", " " * 10, "a" * 1001, 5, None):
            response = {**EPISODE_RESPONSE, "summary": bad}
            with self.assertRaises(ValueError):
                chief.validate_response(json.dumps(response))
