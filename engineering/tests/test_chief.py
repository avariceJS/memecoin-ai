"""Executive episodes recover durable responsibility; no model service required."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from engineering import chief
from engineering.executive import Executive, StaleExecutive
from engineering.org_state import Store


class EpisodeClient:
    prompts = []
    response = {
        "summary": "Отделы продолжают разрешённую работу.",
        "rationale": "Обязательство сохраняется в SQLite.",
        "next_horizon": "Проверить независимую приёмку.",
        "limitations": "Длительная работа не измерена.",
    }

    def __init__(self, *args, **kwargs):
        self.proc = SimpleNamespace(pid=1)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def start(self, model, instructions):
        assert model == "gpt-6-astra"
        return "episode-thread"

    def turn(self, thread, prompt, **kwargs):
        assert kwargs["model"] == "gpt-6-astra"
        assert kwargs["deadline_seconds"] == 120
        assert kwargs["tool"]({}) == {"error": "chief_has_no_execution_capability"}
        kwargs["heartbeat"]()
        self.prompts.append(prompt)
        return {"text": json.dumps(self.response, ensure_ascii=False)}


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
        self.patches = [
            patch.object(chief, "config", return_value={"schema": "test", "resources": {}}),
            patch.object(chief, "Codex", EpisodeClient),
            patch.object(chief, "process_identity", return_value="test-process"),
            patch.object(
                chief,
                "render_desk",
                side_effect=lambda state, db: (state / "OWNER-DESK.md").write_text(
                    Executive(db).render_report()
                ),
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        EpisodeClient.prompts = []

    def test_fresh_episodes_reconstruct_obligations_without_changing_tasks(self):
        before = self.db.list_tasks()
        first = chief.chief(self.path, trigger="replacement")
        second = chief.chief(self.path, trigger="accepted-milestone")
        self.assertEqual((first["epoch"], second["epoch"]), (2, 3))
        self.assertEqual(first["status"], "completed")
        for obligation_id in ("ORG-ACCEPT-01", "ORG-CONTINUE-01", "ORG-LONGRUN-01"):
            obligation = self.executive.get_obligation(obligation_id)
            self.assertEqual(obligation["status"], "open")
            self.assertEqual(obligation["created_epoch"], 2)
        self.assertEqual(self.db.list_tasks(), before)
        self.assertEqual(self.executive.get_obligation("O")["status"], "open")
        self.assertTrue(all("Keep responsibility" in prompt for prompt in EpisodeClient.prompts))
        self.assertIn("executive-episode-2", EpisodeClient.prompts[1])
        self.assertEqual(self.executive.snapshot()["role"]["status"], "released")
        self.assertIsNotNone(self.executive.get_decision("executive-episode-3"))
        projection = json.loads((self.path / "chief.json").read_text())
        self.assertEqual(projection["status"], "completed")
        self.assertNotIn("pid", projection)

    def test_malformed_output_releases_ceo_without_recording_decision(self):
        with patch.object(EpisodeClient, "response", {"unauthorized": "change policy"}):
            with self.assertRaisesRegex(ValueError, "executive_response_fields"):
                chief.chief(self.path)
        self.assertEqual(self.executive.snapshot()["role"]["status"], "released")
        self.assertIsNone(self.executive.get_decision("executive-episode-2"))
        self.assertEqual(self.executive.get_obligation("O")["status"], "open")

    def test_active_director_and_stop_marker_do_not_start_new_model(self):
        active = self.executive.claim("live-director")
        self.assertEqual(chief.chief(self.path)["status"], "executive_busy")
        (self.path / "STOP").touch()
        self.assertEqual(chief.chief(self.path)["status"], "stopped")
        self.assertEqual(EpisodeClient.prompts, [])
        self.assertEqual(self.executive.snapshot()["role"]["epoch"], active["epoch"])

    def test_model_failure_does_not_cancel_already_authorized_work(self):
        attempt = self.db.claim("T", "luna", 120)
        with patch.object(EpisodeClient, "turn", side_effect=TimeoutError("deadline")):
            with self.assertRaises(TimeoutError):
                chief.chief(self.path)
        self.assertEqual(self.db.get_task("T")["active_attempt"], attempt["id"])
        self.assertEqual(self.db.get_task("T")["state"], "running")
        self.assertEqual(self.executive.snapshot()["role"]["status"], "released")

    def test_exhausted_catalogue_persists_obligation_even_when_director_times_out(self):
        with patch.object(EpisodeClient, "turn", side_effect=TimeoutError("deadline")):
            with self.assertRaises(TimeoutError):
                chief.chief(self.path, trigger="catalogue-exhausted")
        obligation = self.executive.get_obligation("next-policy-horizon")
        self.assertEqual(obligation["status"], "open")
        self.assertEqual(obligation["owner_role"], "CEO")
        chief.chief(self.path, trigger="catalogue-exhausted")
        self.assertEqual(self.executive.get_obligation("next-policy-horizon"), obligation)
        self.assertIn("next-policy-horizon", (self.path / "OWNER-DESK.md").read_text())

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

    def test_expired_episode_cannot_record_or_overwrite_replacement_projection(self):
        clock = [1000.0]
        self.db.clock = lambda: clock[0]

        def replace_during_turn(*args, **kwargs):
            clock[0] += 181
            replacement = self.executive.claim("replacement")
            (self.path / "chief.json").write_text(json.dumps({"epoch": replacement["epoch"]}))
            return {"text": json.dumps(EpisodeClient.response)}

        with (
            patch.object(
                chief,
                "store",
                side_effect=lambda state: Store(
                    self.path / "organization.sqlite", clock=lambda: clock[0]
                ),
            ),
            patch.object(EpisodeClient, "turn", side_effect=replace_during_turn),
        ):
            with self.assertRaises(StaleExecutive):
                chief.chief(self.path)
        self.assertIsNone(self.executive.get_decision("executive-episode-2"))
        self.assertEqual(self.executive.snapshot()["role"]["owner"], "replacement")
        self.assertEqual(json.loads((self.path / "chief.json").read_text()), {"epoch": 3})

    def test_schema_rejects_overlong_empty_and_nonstring_decisions(self):
        for bad in ("", " " * 10, "a" * 1001, 5, None):
            response = {**EpisodeClient.response, "summary": bad}
            with self.assertRaises(ValueError):
                chief.validate_response(json.dumps(response))
