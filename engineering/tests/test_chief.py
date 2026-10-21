"""Executive episodes recover durable responsibility; no model service required."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engineering import chief
from engineering.executive import Executive, ExecutiveError, StaleExecutive
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
        self.db.add_task("consumer", "Platform", "Dependent work", dependencies=["T"])
        active = self.executive.claim("live-director")
        self.assertEqual(chief.chief(self.path)["status"], "executive_busy")
        self.assertFalse(
            any(
                row["id"].startswith("blocked-dependencies/")
                for row in self.executive.snapshot(open_limit=100)["obligations"]
            )
        )
        (self.path / "STOP").touch()
        self.assertEqual(chief.chief(self.path)["status"], "stopped")
        self.assertEqual(self.executive.snapshot()["role"]["epoch"], active["epoch"])
        self.assertFalse(
            any(
                row["id"].startswith("blocked-dependencies/")
                for row in self.executive.snapshot(open_limit=100)["obligations"]
            )
        )

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

    def test_dependency_obligation_tracks_only_blockers_and_preserves_ready_work(self):
        self.db.add_task("consumer", "Platform", "Dependent work", dependencies=["T"])
        self.db.add_task("independent", "Platform", "Ready work")
        chief.chief(self.path, trigger="blocked-dependencies")
        rows = [
            row
            for row in self.executive.snapshot()["obligations"]
            if row["id"].startswith("blocked-dependencies/")
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "blocked")
        self.assertIn("task:consumer", rows[0]["blocked_by"])
        self.assertNotIn("task:T", rows[0]["blocked_by"])
        snapshot = rows[0]["details"]["snapshot"]
        self.assertEqual([row["task_id"] for row in snapshot["blocked"]], ["consumer"])
        self.assertEqual(snapshot["ready"], ["T", "independent"])
        self.assertEqual(self.db.get_task("consumer")["state"], "pending")

        chief.chief(self.path, trigger="owner-review")
        rows_after_retry = [
            row
            for row in self.executive.snapshot(open_limit=100)["obligations"]
            if row["id"].startswith("blocked-dependencies/")
        ]
        self.assertEqual(len(rows_after_retry), 1)
        self.assertEqual(rows_after_retry[0]["id"], rows[0]["id"])

        self.db.db.execute(
            "UPDATE tasks SET dependencies=? WHERE id='consumer'",
            (json.dumps(["other-producer"], separators=(",", ":")),),
        )
        chief.chief(self.path, trigger="accepted-milestone")
        obligations = [
            row
            for row in self.executive.snapshot(open_limit=100)["obligations"]
            if row["id"].startswith("blocked-dependencies/")
        ]
        self.assertEqual(len(obligations), 1)
        self.assertNotEqual(obligations[0]["id"], rows[0]["id"])
        self.assertEqual(obligations[0]["status"], "blocked")
        self.assertEqual(
            obligations[0]["details"]["snapshot"]["blocked"][0]["blockers"][0]["id"],
            "other-producer",
        )
        self.assertEqual(self.executive.get_obligation(rows[0]["id"])["status"], "cancelled")

    def test_dependency_obligation_cancels_atomically_when_readiness_clears(self):
        self.db.add_task("consumer", "Platform", "Dependent work", dependencies=["T"])
        chief.chief(self.path, trigger="blocked-dependencies")
        obligation = next(
            row
            for row in self.executive.snapshot(open_limit=100)["obligations"]
            if row["id"].startswith("blocked-dependencies/")
        )

        attempt = self.db.claim("T", "worker")
        self.db.submit(attempt["id"], attempt["epoch"], "candidate", {})
        self.db.verify("T", "candidate", True, {})
        self.db.accept("T", "candidate")
        self.db.integrate("T", "candidate", "merged")
        chief.chief(self.path, trigger="blocked-dependencies")

        closed = self.executive.get_obligation(obligation["id"])
        self.assertEqual(closed["status"], "cancelled")
        self.assertEqual(len(closed["cancellation_evidence"]), 1)
        evidence_ref = closed["cancellation_evidence"][0]
        self.assertTrue(evidence_ref.startswith("dependency-readiness:"))

        # Later readiness changes must not erase the immutable cancellation basis.
        self.db.add_task("later", "Platform", "Later ready task")
        chief.chief(self.path, trigger="blocked-dependencies")
        evidence = self.db.db.execute(
            "SELECT body FROM events WHERE kind='dependency_readiness_reconciled' AND entity=?",
            (evidence_ref,),
        ).fetchone()
        self.assertIsNotNone(evidence)
        evidence_body = json.loads(evidence[0])
        self.assertEqual(evidence_body["snapshot"]["blocked"], [])
        self.assertEqual(
            hashlib.sha256(
                json.dumps(
                    evidence_body["snapshot"],
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest(),
            evidence_body["snapshot_sha256"],
        )
        self.assertEqual(evidence_ref, "dependency-readiness:" + evidence_body["snapshot_sha256"])
        self.assertEqual(evidence_body["snapshot"]["ready"], ["consumer"])
        self.assertEqual(self.db.get_task("consumer")["state"], "pending")
        self.assertEqual([task["id"] for task in self.db.ready_tasks()], ["consumer", "later"])
        self.assertFalse(
            any(
                row["id"].startswith("blocked-dependencies/")
                for row in self.executive.snapshot(open_limit=100)["obligations"]
            )
        )

    def test_dependency_obligations_reconcile_on_unrelated_fresh_episodes(self):
        for index, trigger in enumerate(
            (
                "owner-review",
                "replacement",
                "accepted-milestone",
                "catalogue-exhausted",
                "quota-unavailable",
            )
        ):
            with self.subTest(trigger=trigger):
                state = self.path / f"episode-{index}"
                state.mkdir()
                db = Store(state / "organization.sqlite")
                try:
                    executive = Executive(db)
                    db.add_task("producer", "Experience", "Produce dependency")
                    db.add_task("consumer", "Platform", "Dependent work", dependencies=["producer"])
                    with patch.object(
                        chief, "structured", side_effect=AssertionError("model call")
                    ):
                        chief.chief(state, trigger="owner-review")
                    obligation = next(
                        row
                        for row in executive.snapshot(open_limit=100)["obligations"]
                        if row["id"].startswith("blocked-dependencies/")
                    )

                    attempt = db.claim("producer", "worker")
                    db.submit(attempt["id"], attempt["epoch"], "candidate", {})
                    db.verify("producer", "candidate", True, {})
                    db.accept("producer", "candidate")
                    db.integrate("producer", "candidate", "merged")
                    db.close()

                    # A new Store/Executive episode observes only durable state.
                    db = Store(state / "organization.sqlite")
                    executive = Executive(db)
                    task_state_before = db.list_tasks()
                    with patch.object(
                        chief, "structured", side_effect=AssertionError("model call")
                    ):
                        chief.chief(state, trigger=trigger)

                    closed = executive.get_obligation(obligation["id"])
                    self.assertEqual(closed["status"], "cancelled")
                    self.assertEqual(len(closed["cancellation_evidence"]), 1)
                    evidence_ref = closed["cancellation_evidence"][0]
                    self.assertTrue(evidence_ref.startswith("dependency-readiness:"))
                    evidence = db.db.execute(
                        "SELECT body FROM events "
                        "WHERE kind='dependency_readiness_reconciled' AND entity=?",
                        (evidence_ref,),
                    ).fetchone()
                    self.assertIsNotNone(evidence)
                    self.assertEqual(json.loads(evidence[0])["snapshot"]["blocked"], [])
                    self.assertEqual(db.list_tasks(), task_state_before)
                finally:
                    db.close()

    def test_dependency_obligation_records_contract_mismatch_and_leaves_ready_task(self):
        self.db.put_contract("api", 1, {"schema": "v1"})
        self.db.add_task("contract-consumer", "Platform", "Contract task", contracts={"api": 2})
        self.db.add_task("ready", "Platform", "Unrelated ready task")
        chief.chief(self.path, trigger="blocked-dependencies")

        obligation = next(
            row
            for row in self.executive.snapshot(open_limit=100)["obligations"]
            if row["id"].startswith("blocked-dependencies/")
        )
        self.assertEqual(
            obligation["details"]["snapshot"]["blocked"],
            [
                {
                    "task_id": "contract-consumer",
                    "blockers": [
                        {
                            "kind": "contract",
                            "name": "api",
                            "required_version": 2,
                            "current_version": 1,
                        }
                    ],
                }
            ],
        )
        self.assertIn("ready", obligation["details"]["snapshot"]["ready"])
        self.assertEqual([task["id"] for task in self.db.ready_tasks()], ["T", "ready"])

    def test_unrelated_obligation_details_do_not_block_dependency_reconciliation(self):
        lease = self.executive.claim("prior-owner")
        self.executive.obligation(lease, "unrelated", "Unrelated", details=[])
        self.executive.release(lease)
        self.db.add_task("consumer", "Platform", "Dependent work", dependencies=["T"])

        chief.chief(self.path, trigger="blocked-dependencies")

        self.assertTrue(
            any(
                row["id"].startswith("blocked-dependencies/")
                for row in self.executive.snapshot(open_limit=100)["obligations"]
            )
        )

    def test_dependency_reconciliation_does_not_decode_rows_outside_its_id_range(self):
        self.db.add_task("consumer", "Platform", "Dependent work", dependencies=["T"])
        unrelated_large = {
            "id": "unrelated-large",
            "status": "open",
            "details": {"payload": "x" * (1024 * 1024)},
        }
        for record_id, body_json in (
            ("unrelated-corrupt", "not-json"),
            ("Blocked-dependencies/case-variant", "not-json"),
            ("blocked-dependencies0-adjacent", "not-json"),
            (
                "unrelated-large",
                json.dumps(unrelated_large, separators=(",", ":")),
            ),
            (
                "blocked-dependencies/",
                json.dumps(
                    {"id": "blocked-dependencies/", "status": "completed", "details": {}},
                    separators=(",", ":"),
                ),
            ),
        ):
            self.db.db.execute(
                "INSERT INTO executive_obligations(id, body) VALUES(?, ?)",
                (record_id, body_json),
            )
        plan = self.db.db.execute(
            "EXPLAIN QUERY PLAN SELECT id, body FROM executive_obligations "
            "WHERE id >= ? AND id < ? ORDER BY id",
            ("blocked-dependencies/", "blocked-dependencies0"),
        ).fetchall()
        self.assertTrue(any("SEARCH" in row[3] and "id>? AND id<?" in row[3] for row in plan))

        lease = self.executive.claim("direct-reconciler")
        try:
            result = self.executive.reconcile_dependency_obligations(lease)
        finally:
            self.executive.release(lease)

        self.assertTrue(result["obligation_id"].startswith("blocked-dependencies/"))

    def test_dependency_reconciliation_rejects_corrupt_scoped_row_atomically(self):
        self.db.add_task("consumer", "Platform", "Dependent work", dependencies=["T"])
        lease = self.executive.claim("direct-reconciler")
        first = self.executive.reconcile_dependency_obligations(lease)
        obligation_id = first["obligation_id"]
        prior_role = self.db.db.execute(
            "SELECT body FROM executive_role WHERE role='CEO'"
        ).fetchone()[0]
        self.db.db.execute(
            "UPDATE executive_obligations SET body='not-json' WHERE id=?", (obligation_id,)
        )
        prior_events = self.db.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        with self.assertRaises(json.JSONDecodeError):
            self.executive.reconcile_dependency_obligations(lease)

        self.assertEqual(
            self.db.db.execute(
                "SELECT body FROM executive_obligations WHERE id=?", (obligation_id,)
            ).fetchone()[0],
            "not-json",
        )
        self.assertEqual(
            self.db.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], prior_events
        )
        self.assertEqual(
            self.db.db.execute("SELECT body FROM executive_role WHERE role='CEO'").fetchone()[0],
            prior_role,
        )
        self.executive.release(lease)

    def test_dependency_reconciliation_rejects_row_body_id_mismatch(self):
        self.db.add_task("consumer", "Platform", "Dependent work", dependencies=["T"])
        lease = self.executive.claim("direct-reconciler")
        first = self.executive.reconcile_dependency_obligations(lease)
        obligation_id = first["obligation_id"]
        row = self.executive.get_obligation(obligation_id)
        row["id"] = "blocked-dependencies/other/1"
        self.db.db.execute(
            "UPDATE executive_obligations SET body=? WHERE id=?",
            (json.dumps(row, sort_keys=True, separators=(",", ":")), obligation_id),
        )
        prior_events = self.db.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

        with self.assertRaisesRegex(ExecutiveError, "history is malformed"):
            self.executive.reconcile_dependency_obligations(lease)

        self.assertEqual(
            self.db.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], prior_events
        )
        self.executive.release(lease)

    def test_dependency_reconciliation_counts_terminal_generations(self):
        self.db.add_task("consumer", "Platform", "Dependent work", dependencies=["T"])
        lease = self.executive.claim("direct-reconciler")
        first = self.executive.reconcile_dependency_obligations(lease)
        row = self.executive.get_obligation(first["obligation_id"])
        row["status"] = "cancelled"
        self.db.db.execute(
            "UPDATE executive_obligations SET body=? WHERE id=?",
            (json.dumps(row, sort_keys=True, separators=(",", ":")), row["id"]),
        )

        second = self.executive.reconcile_dependency_obligations(lease)

        self.assertEqual(second["obligation_id"], first["obligation_id"].rsplit("/", 1)[0] + "/2")
        self.executive.release(lease)

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
