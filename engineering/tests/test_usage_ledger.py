import tempfile
import time
import unittest
from pathlib import Path

from engineering.usage_ledger import UsageLedger


def c(i=0, ci=0, o=0, r=0):
    return dict(
        input_tokens=i,
        cached_input_tokens=ci,
        output_tokens=o,
        reasoning_output_tokens=r,
        total_tokens=i + o,
    )


class _DatabaseProxy:
    def __init__(self, database, after_first_event_read=None, fail_query=None):
        self._database = database
        self._after_first_event_read = after_first_event_read
        self._fail_query = fail_query
        self._event_read_seen = False

    def __getattr__(self, name):
        return getattr(self._database, name)

    def execute(self, query, parameters=()):
        if self._fail_query and self._fail_query in query:
            self._fail_query = None
            raise RuntimeError("injected summary read failure")
        cursor = self._database.execute(query, parameters)
        if (
            not self._event_read_seen
            and self._after_first_event_read
            and "SELECT * FROM events WHERE identity=?" in query
        ):
            self._event_read_seen = True
            self._after_first_event_read()
        return cursor


class UsageLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.sqlite"
        self.l = UsageLedger(self.path)
        self.l.register("a", "e1", "gpt-6.1-sol", c(), evidence="baseline")

    def tearDown(self):
        self.l.close()
        self.tmp.cleanup()

    def observe(self, eid, seq, counters, source="provider", epoch="e1"):
        self.l.observe("a", epoch, eid, counters, source=source, sequence=seq, observed_at=seq)

    def test_receipt_redelivery_preserves_first_observed_at(self):
        evidence = {"thread_id": "a", "turn_id": "turn", "value": 3}
        self.l.observe_receipt("receipt-1", "d1", None, "turn", "token_usage", None, 1.0, evidence)
        self.l.observe_receipt("receipt-1", "d1", None, "turn", "token_usage", None, 2.0, evidence)
        receipt = next(r for r in self.l.summary()["receipts"] if r["event_id"] == "receipt-1")
        self.assertEqual(receipt["observed_at"], 1.0)
        with self.assertRaisesRegex(ValueError, "receipt_id_payload_conflict"):
            self.l.observe_receipt(
                "receipt-1",
                "d1",
                None,
                "turn",
                "token_usage",
                None,
                3.0,
                {"thread_id": "a", "turn_id": "turn", "value": 4},
            )

    def test_compatible_overlapping_model_intervals_are_unioned(self):
        base, mid, end = c(), c(10), c(20)

        def interval(identity, start, finish, at):
            return {
                "id": identity,
                "model": "gpt-6-luna",
                "start": start,
                "end": finish,
                "event_at": float(at),
                "source": "independent-rollout",
                "evidence": {"id": identity},
            }

        first = {
            "schema": "brain-org-usage-import-v1",
            "streams": [
                {
                    "identity": "overlap-thread",
                    "epoch": "e",
                    "baseline": base,
                    "intervals": [interval("left", base, mid, 1), interval("right", mid, end, 2)],
                }
            ],
            "unresolved": [],
        }
        second = {
            "schema": "brain-org-usage-import-v1",
            "streams": [
                {
                    "identity": "overlap-thread",
                    "epoch": "e",
                    "baseline": base,
                    "intervals": [interval("whole", base, end, 3)],
                }
            ],
            "unresolved": [],
        }
        for batch in (first, second):
            self.l.import_external(
                batch, "codex-rollout-intervals-v1", self.l.external_batch_hash(batch)
            )
        summary = self.l.summary()
        self.assertEqual(summary["incremental"]["total_tokens"], 20)
        self.assertEqual(summary["by_model"]["gpt-6-luna"]["total_tokens"], 20)
        self.assertNotIn("model_interval_conflict", [r["reason"] for r in summary["unresolved"]])

    def test_dispatch_receipts_are_immutable_and_readonly_summary_is_noncreating(self):
        summary_path = Path(self.tmp.name) / "missing.sqlite"
        self.assertIsNone(UsageLedger.readonly_summary(summary_path))
        row = self.l.reserve(
            "dispatch-1",
            "thread-1",
            "gpt-6-luna",
            {"tokens": 10, "wpu": 1},
            1234,
            context={"role": "worker", "team": "Experience"},
        )
        self.assertEqual(row["status"], "reserved")
        self.assertTrue(self.l.consume_dispatch("dispatch-1"))
        self.assertFalse(self.l.consume_dispatch("dispatch-1"))
        self.l.bind("dispatch-1", "thread-1", "turn-1", {"started": True})
        self.l.bind("dispatch-1", "thread-1", "turn-1", {"started": False})
        self.l.record_outcome("dispatch-1", "completed", {"usage_terminal": False})
        self.l.record_outcome("dispatch-1", "completed", {"usage_terminal": False})
        self.assertEqual(len(self.l.summary()["reservations"]), 1)
        self.assertTrue(UsageLedger.readonly_summary(self.path)["reservations"])
        with self.assertRaises(ValueError):
            self.l.record_outcome("dispatch-1", "completed", {"usage_terminal": True})

    def test_dispatch_rejects_unrepresentable_estimates_and_deadlines(self):
        with self.assertRaisesRegex(ValueError, "tokens must be finite and positive"):
            self.l.reserve(
                "huge",
                "thread-huge",
                "gpt-6-luna",
                {"tokens": 10**400, "wpu": 1},
                1234,
                context={"role": "worker", "team": "Experience"},
            )
        with self.assertRaisesRegex(ValueError, "deadline_at must be a finite timestamp"):
            self.l.reserve(
                "huge-time",
                "thread-huge",
                "gpt-6-luna",
                {"tokens": 1, "wpu": 1},
                10**5000,
                context={"role": "worker", "team": "Experience"},
            )

    def test_dispatch_settlement_requires_exact_qualified_terminal_and_stream_coverage(self):
        baseline = c(0)
        end = c(10, 2, 3, 1)
        row = self.l.reserve(
            "settle-1",
            "a",
            "gpt-6-luna",
            {"tokens": 20, "wpu": 1},
            time.time() + 2000,
            context={"role": "worker", "team": "Experience"},
            baseline=baseline,
        )
        self.assertEqual(row["status"], "reserved")
        self.assertTrue(self.l.consume_dispatch("settle-1"))
        self.l.bind("settle-1", "a", "turn-1")
        self.l.record_outcome("settle-1", "completed", {"turn_id": "turn-1"})
        rpc_end = {
            "inputTokens": end["input_tokens"],
            "cachedInputTokens": end["cached_input_tokens"],
            "outputTokens": end["output_tokens"],
            "reasoningOutputTokens": end["reasoning_output_tokens"],
            "totalTokens": end["total_tokens"],
        }
        self.l.observe_receipt(
            "settle-1:terminal",
            "settle-1",
            None,
            "turn-1",
            "turn_completed",
            1.0,
            2.0,
            {
                "thread_id": "a",
                "provider_thread_id": "a",
                "turn_id": "turn-1",
                "status": "completed",
                "usage_terminal": True,
                "usage_end": rpc_end,
            },
        )
        with self.assertRaisesRegex(ValueError, "terminal_usage_not_qualified"):
            self.l.cover_dispatch(
                "settle-1",
                thread_id="a",
                turn_id="turn-1",
                model="gpt-6-luna",
                counters=end,
                terminal_qualified=False,
            )
        with self.assertRaisesRegex(ValueError, "terminal_attribution_turn_mismatch"):
            self.l.cover_dispatch(
                "settle-1",
                thread_id="a",
                turn_id="wrong",
                model="gpt-6-luna",
                counters=end,
                terminal_qualified=True,
            )
        self.assertEqual(len(self.l.summary()["reservations"]), 1)
        self.l.cover_dispatch(
            "settle-1",
            thread_id="a",
            turn_id="turn-1",
            model="gpt-6-luna",
            counters=end,
            terminal_qualified=True,
            event_at=1.0,
            observed_at=2.0,
        )
        summary = self.l.summary()
        self.assertEqual(summary["reservations"], [])
        self.assertEqual(summary["incremental"], end)
        self.assertEqual(summary["by_model"]["gpt-6-luna"]["total_tokens"], end["total_tokens"])
        self.assertEqual(self.l.dispatch("settle-1")["status"], "covered")

    def test_dispatch_replay_cannot_reserve_same_stream_after_unknown_outcome(self):
        self.l.reserve(
            "uncertain-1",
            "a",
            "gpt-6-luna",
            {"tokens": 20, "wpu": 1},
            time.time() + 2000,
            context={"role": "worker", "team": "Experience"},
            baseline=c(),
        )
        with self.assertRaisesRegex(ValueError, "unresolved_stream_dispatch"):
            self.l.reserve(
                "uncertain-2",
                "a",
                "gpt-6-luna",
                {"tokens": 20, "wpu": 1},
                2000,
                context={"role": "worker", "team": "Experience"},
                baseline=c(),
            )

    def test_new_stream_settlement_is_distinct_from_raw_outcome(self):
        self.l.reserve(
            "start-1",
            "start-1",
            "gpt-6-luna",
            {"tokens": 1, "wpu": 0.1},
            2000,
            context={"role": "worker", "team": "Experience"},
        )
        self.l.consume_dispatch("start-1")
        with self.assertRaisesRegex(ValueError, "invalid outcome"):
            self.l.record_outcome("start-1", "settled", {"new_unused_stream": True})
        self.l.settle_new_stream("start-1", "new-thread", evidence="thread-start-receipt")
        self.assertEqual(
            self.l.current_snapshot("new-thread"), {"epoch": "initial", "counters": c()}
        )
        self.assertEqual(self.l.dispatch("start-1")["status"], "settled")

    def test_external_import_deduplicates_usage_and_attributes_only_evidenced_models(self):
        base, sol_end, luna_end = c(100, 10, 20, 4), c(110, 12, 22, 4), c(115, 13, 23, 5)
        batch = {
            "schema": "brain-org-usage-import-v1",
            "streams": [
                {
                    "identity": "external-thread",
                    "epoch": "epoch-1",
                    "baseline": base,
                    "intervals": [
                        {
                            "id": "i-1",
                            "model": "gpt-6.1-sol",
                            "start": base,
                            "end": sol_end,
                            "event_at": 1.0,
                            "source": "rollout",
                            "evidence": {"turn": "one"},
                        },
                        {
                            "id": "i-2",
                            "model": "gpt-6-luna",
                            "start": sol_end,
                            "end": luna_end,
                            "event_at": 2.0,
                            "source": "rollout",
                            "evidence": {"turn": "two"},
                        },
                    ],
                }
            ],
            "unresolved": [{"identity": "tail", "reason": "inflight_tail", "evidence": {}}],
        }
        digest = self.l.external_batch_hash(batch)
        first = self.l.import_external(batch, "codex-rollout-intervals-v1", digest)
        self.assertFalse(first["duplicate"])
        self.assertTrue(
            self.l.import_external(batch, "codex-rollout-intervals-v1", digest)["duplicate"]
        )
        duplicate_source = {
            **batch,
            "streams": [
                {
                    **batch["streams"][0],
                    "intervals": [batch["streams"][0]["intervals"][0]],
                }
            ],
            "unresolved": [],
        }
        self.l.import_external(
            duplicate_source,
            "codex-rollout-intervals-v1",
            self.l.external_batch_hash(duplicate_source),
        )
        self.l.observe(
            "external-thread",
            "epoch-1",
            "after-import",
            c(118, 13, 25, 5),
            source="later-observation",
            sequence=140,
            observed_at=3.0,
        )
        summary = self.l.summary()
        self.assertEqual(summary["incremental"], c(18, 3, 5, 1))
        self.assertEqual(
            summary["by_model"]["gpt-6.1-sol"],
            {
                **c(10, 2, 2, 0),
                "wpu": (8 * 50 + 2 * 2.5 + 2 * 250) / 1e6,
            },
        )
        self.assertEqual(
            summary["by_model"]["gpt-6-luna"],
            {
                **c(5, 1, 1, 1),
                "wpu": (4 * 2.5 + 1 * 0.25 + 1 * 12.5) / 1e6,
            },
        )
        self.assertEqual(
            summary["by_model"]["unclassified"],
            {
                **c(3, 0, 2, 0),
                "wpu": None,
            },
        )
        self.assertEqual(len(summary["external_imports"]), 2)
        self.assertEqual(len(summary["external_unresolved"]), 1)
        self.assertEqual(summary["reservations"], [])

    def test_external_import_unknown_baseline_and_conflicts_fail_closed(self):
        unknown = {
            "schema": "brain-org-usage-import-v1",
            "streams": [{"identity": "unknown", "epoch": "e", "baseline": None, "intervals": []}],
            "unresolved": [
                {
                    "identity": "unknown",
                    "reason": "baseline_unavailable",
                    "evidence": {"source": "test"},
                }
            ],
        }
        unknown_hash = self.l.external_batch_hash(unknown)
        self.l.import_external(unknown, "codex-rollout-intervals-v1", unknown_hash)
        unknown_summary = self.l.summary()
        self.assertTrue(
            any(
                item["identity"] == "unknown" and item["reason"] == "baseline_unknown"
                for item in unknown_summary["unresolved"]
            )
        )
        self.assertEqual(
            unknown_summary["external_unresolved"][0]["reason"], "baseline_unavailable"
        )

        batch = {
            "schema": "brain-org-usage-import-v1",
            "streams": [
                {
                    "identity": "conflict",
                    "epoch": "e",
                    "baseline": c(),
                    "intervals": [
                        {
                            "id": "x",
                            "model": "gpt-6.1-sol",
                            "start": c(),
                            "end": c(4),
                            "event_at": 1.0,
                            "source": "rollout",
                            "evidence": {},
                        }
                    ],
                }
            ],
            "unresolved": [],
        }
        digest = self.l.external_batch_hash(batch)
        self.l.import_external(batch, "codex-rollout-intervals-v1", digest)
        conflicting = {
            **batch,
            "streams": [
                {
                    **batch["streams"][0],
                    "intervals": [
                        {
                            **batch["streams"][0]["intervals"][0],
                            "model": "gpt-6-luna",
                        }
                    ],
                }
            ],
        }
        conflicting_hash = self.l.external_batch_hash(conflicting)
        before = self.l.db.execute("SELECT COUNT(*) FROM external_batches").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "external_model_interval_conflict"):
            self.l.import_external(conflicting, "codex-rollout-intervals-v1", conflicting_hash)
        self.assertEqual(
            self.l.db.execute("SELECT COUNT(*) FROM external_batches").fetchone()[0], before
        )
        with self.assertRaisesRegex(ValueError, "external_evidence_hash_mismatch"):
            self.l.import_external(conflicting, "codex-rollout-intervals-v1", digest)
        with self.assertRaisesRegex(ValueError, "external_collector_not_allowed"):
            self.l.import_external(unknown, "unknown-collector", unknown_hash)

    def test_disjoint_and_overlapping_cumulative_union(self):
        self.observe("1", 1, c(10, 2, 3), "x")
        self.observe("2", 1, c(10, 2, 3), "y")
        self.observe("3", 2, c(15, 4, 5), "x")
        self.observe("4", 2, c(15, 4, 5), "y")
        self.l.register("b", "e1", "gpt-6-luna", c(), evidence="baseline")
        self.l.observe("b", "e1", "b1", c(2), source="z", sequence=1, observed_at=1)
        s = self.l.summary()
        self.assertEqual(s["incremental"], c(17, 4, 5))
        self.assertAlmostEqual(
            s["by_model"]["gpt-6.1-sol"]["wpu"], (11 * 50 + 4 * 2.5 + 5 * 250) / 1e6
        )

    def test_late_sequence_and_decrease_rejected(self):
        self.observe("new", 3, c(8))
        self.observe("late", 2, c(5))
        self.assertEqual(self.l.summary()["incremental"]["input_tokens"], 8)
        with self.assertRaises(ValueError):
            self.observe("bad", 4, c(7))

    def test_correction_is_head_once_and_fork_rejected(self):
        self.observe("old", 1, c(8))
        self.l.correct("old", "fixed", c(6), reason="source correction", source="audit")
        self.l.correct("old", "fixed", c(6), reason="source correction", source="audit")
        self.assertEqual(self.l.summary()["incremental"]["input_tokens"], 6)
        with self.assertRaises(ValueError):
            self.l.correct("old", "fork", c(7), reason="fork", source="audit")

    def test_correction_chain_and_append_timestamp(self):
        self.l.observe(
            "a",
            "e1",
            "original",
            c(8),
            source="provider",
            sequence=1,
            observed_at=1,
            event_at=0.5,
        )
        first_time = self.l.db.execute(
            "SELECT observed_at FROM events WHERE event_id=?", ("original",)
        ).fetchone()[0]
        self.l.correct("original", "corrected-1", c(6), reason="first fix", source="audit")
        first = self.l.db.execute(
            "SELECT observed_at,event_at FROM events WHERE event_id=?", ("corrected-1",)
        ).fetchone()
        self.assertGreater(first["observed_at"], first_time)
        self.assertEqual(first["event_at"], 0.5)
        self.l.correct("corrected-1", "corrected-2", c(5), reason="second fix", source="audit")
        self.l.correct("corrected-1", "corrected-2", c(5), reason="second fix", source="audit")
        self.assertEqual(self.l.summary()["incremental"]["input_tokens"], 5)

    def test_interval_validation_for_observations_and_turns(self):
        self.observe("base", 1, c(4, 1, 2, 1))
        for invalid in (c(5, 3, 2, 1), c(5, 1, 3, 3)):
            with self.assertRaises(ValueError):
                self.observe("invalid-" + str(invalid), 2, invalid)
        self.l.start_turn(
            "invalid-turn",
            "a",
            "e1",
            c(4, 1, 2, 1),
            reservation_tokens=5,
            reservation_wpu=0.1,
            deadline_at=1,
            evidence="dispatch",
        )
        with self.assertRaises(ValueError):
            self.l.finish_turn("invalid-turn", c(5, 3, 2, 1), receipt="bad")
        self.assertEqual(self.l.summary()["turns"]["invalid-turn"], None)

    def test_reset_epoch_unknown_and_new_identity(self):
        self.observe("one", 1, c(9))
        self.l.register("a", "e2", "gpt-6.1-sol", c(1), evidence="reset")
        self.l.observe("a", "e2", "two", c(4), source="p", sequence=1, observed_at=2)
        self.l.register("unknown", "e", "gpt-6-luna", None, evidence="partial")
        self.l.register(
            "fresh", "e", "gpt-6-astra", None, evidence="created", started_after_T0=True
        )
        self.assertEqual(self.l.summary()["incremental"]["input_tokens"], 12)
        self.assertEqual(len(self.l.summary()["unresolved"]), 1)

    def test_validation(self):
        for bad in (
            dict(c(1), total_tokens=9),
            dict(c(1), cached_input_tokens=2),
            dict(c(1), input_tokens=True, total_tokens=2),
            dict(c(1), reasoning_output_tokens=1),
        ):
            with self.assertRaises(ValueError):
                self.observe("bad" + str(len(str(bad))), 1, bad)

    def test_turn_delta_failure_retry_and_reservation(self):
        self.l.start_turn(
            "t1",
            "a",
            "e1",
            c(3),
            reservation_tokens=10,
            reservation_wpu=0.2,
            deadline_at=1,
            evidence="dispatch",
        )
        self.assertEqual(self.l.summary()["turns"]["t1"], None)
        self.l.finish_turn("t1", c(7, 1, 2, 1), receipt="r1", failed=True)
        self.l.finish_turn("t1", c(7, 1, 2, 1), receipt="r1", failed=True)
        self.observe("cover-t1", 1, c(7, 1, 2, 1))
        self.l.start_turn(
            "t2",
            "a",
            "e1",
            c(7, 1, 2, 1),
            reservation_tokens=4,
            reservation_wpu=0.1,
            deadline_at=0,
            evidence="retry",
        )
        s = self.l.summary()
        self.assertEqual(s["turns"]["t1"], c(4, 1, 2, 1))
        self.assertEqual(len(s["reservations"]), 1)
        with self.assertRaises(ValueError):
            self.l.finish_turn("t1", c(8, 1, 2, 1), receipt="other")

    def test_finished_turn_reservation_waits_for_union_coverage(self):
        self.l.start_turn(
            "covered-later",
            "a",
            "e1",
            c(),
            reservation_tokens=50,
            reservation_wpu=1.0,
            deadline_at=10,
            evidence="dispatch",
        )
        self.l.finish_turn("covered-later", c(50, 10, 5), receipt="r", failed=True)
        pending = self.l.summary()
        self.assertEqual(pending["turns"]["covered-later"], c(50, 10, 5))
        self.assertEqual([r["turn_id"] for r in pending["reservations"]], ["covered-later"])
        self.assertTrue(
            any(item.get("turn_id") == "covered-later" for item in pending["unresolved"])
        )
        self.observe("late-coverage", 1, c(50, 10, 5))
        covered = self.l.summary()
        self.assertEqual(covered["incremental"], c(50, 10, 5))
        self.assertEqual(covered["reservations"], [])
        self.assertFalse(
            any(item.get("turn_id") == "covered-later" for item in covered["unresolved"])
        )

    def test_summary_uses_one_wal_snapshot_for_totals_and_turn_coverage(self):
        writer = UsageLedger(self.path)
        self.l.start_turn(
            "race-turn",
            "a",
            "e1",
            c(),
            reservation_tokens=50,
            reservation_wpu=1.0,
            deadline_at=10,
            evidence="dispatch",
        )
        self.l.finish_turn("race-turn", c(50), receipt="failed-receipt", failed=True)
        self.l.db.execute("PRAGMA journal_mode=WAL")

        def observe_after_reader_event_query():
            writer.observe(
                "a", "e1", "late-50", c(50), source="provider", sequence=1, observed_at=1
            )

        database = self.l.db
        self.l.db = _DatabaseProxy(
            database, after_first_event_read=observe_after_reader_event_query
        )
        try:
            first = self.l.summary()
            self.assertEqual(first["incremental"], c())
            self.assertEqual([item["turn_id"] for item in first["reservations"]], ["race-turn"])
            self.assertTrue(any(item.get("turn_id") == "race-turn" for item in first["unresolved"]))
            self.assertFalse(database.in_transaction)

            self.l.db = database
            second = self.l.summary()
            self.assertEqual(second["incremental"], c(50))
            self.assertEqual(second["reservations"], [])
            self.assertFalse(
                any(item.get("turn_id") == "race-turn" for item in second["unresolved"])
            )
        finally:
            self.l.db = database
            writer.close()

    def test_summary_transaction_cleanup_and_caller_transaction(self):
        database = self.l.db
        self.l.db = _DatabaseProxy(database, fail_query="SELECT * FROM turns ORDER BY turn_id")
        try:
            with self.assertRaisesRegex(RuntimeError, "injected summary read failure"):
                self.l.summary()
            self.assertFalse(database.in_transaction)
        finally:
            self.l.db = database

        database.execute("BEGIN")
        self.l.summary()
        self.assertTrue(database.in_transaction)
        database.execute("ROLLBACK")
        self.assertFalse(database.in_transaction)

    def test_atomic_correction_batch_repairs_review4_stream(self):
        self.observe("A", 1, c(100, 50))
        self.observe("B", 2, c(200, 100))
        self.l.correct_many(
            [
                {
                    "event_id": "A",
                    "replacement_event_id": "A-fixed",
                    "counters": c(150, 0),
                    "reason": "audit",
                    "source": "audit",
                },
                {
                    "event_id": "B",
                    "replacement_event_id": "B-fixed",
                    "counters": c(250, 40),
                    "reason": "audit",
                    "source": "audit",
                },
            ]
        )
        summary = self.l.summary()
        self.assertEqual(summary["incremental"], c(250, 40))
        self.assertAlmostEqual(
            summary["by_model"]["gpt-6.1-sol"]["wpu"], (210 * 50 + 40 * 2.5) / 1_000_000
        )
        self.l.correct_many(
            [
                {
                    "event_id": "A",
                    "replacement_event_id": "A-fixed",
                    "counters": c(150, 0),
                    "reason": "audit",
                    "source": "audit",
                },
                {
                    "event_id": "B",
                    "replacement_event_id": "B-fixed",
                    "counters": c(250, 40),
                    "reason": "audit",
                    "source": "audit",
                },
            ]
        )
        self.l.close()
        self.l = UsageLedger(self.path)
        self.assertEqual(self.l.summary()["incremental"], c(250, 40))

    def test_invalid_correction_batch_rolls_back_and_rejects_fork(self):
        self.observe("A", 1, c(100, 50))
        self.observe("B", 2, c(200, 100))
        batch = [
            {
                "event_id": "A",
                "replacement_event_id": "A-fixed",
                "counters": c(150, 0),
                "reason": "audit",
                "source": "audit",
            },
            {
                "event_id": "B",
                "replacement_event_id": "B-bad",
                "counters": c(50),
                "reason": "audit",
                "source": "audit",
            },
        ]
        with self.assertRaises(ValueError):
            self.l.correct_many(batch)
        self.assertEqual(self.l.summary()["incremental"], c(200, 100))
        self.assertIsNone(
            self.l.db.execute("SELECT 1 FROM events WHERE event_id='A-fixed'").fetchone()
        )
        self.l.correct_many(
            [
                {
                    "event_id": "A",
                    "replacement_event_id": "A-fixed",
                    "counters": c(150, 0),
                    "reason": "audit",
                    "source": "audit",
                },
                {
                    "event_id": "B",
                    "replacement_event_id": "B-fixed",
                    "counters": c(250, 40),
                    "reason": "audit",
                    "source": "audit",
                },
            ]
        )
        with self.assertRaises(ValueError):
            self.l.correct_many(
                [
                    {
                        "event_id": "A",
                        "replacement_event_id": "fork",
                        "counters": c(150),
                        "reason": "fork",
                        "source": "audit",
                    },
                    {
                        "event_id": "B",
                        "replacement_event_id": "B-fixed",
                        "counters": c(250, 40),
                        "reason": "audit",
                        "source": "audit",
                    },
                ]
            )
        self.assertEqual(self.l.summary()["incremental"], c(250, 40))

    def test_expired_unknown_releases_global_slot_but_locks_same_stream(self):
        now = time.time()
        self.l.reserve(
            "unknown-1",
            "a",
            "gpt-6-luna",
            {"tokens": 20, "wpu": 1},
            now + 1,
            context={"role": "worker", "team": "Experience"},
            baseline=c(),
            now=now,
        )
        self.l.consume_dispatch("unknown-1")
        self.l.record_outcome("unknown-1", "unknown", {"error": "lost receipt"})
        with self.assertRaisesRegex(ValueError, "unresolved_stream_dispatch"):
            self.l.reserve(
                "same-stream-retry",
                "a",
                "gpt-6-luna",
                {"tokens": 20, "wpu": 1},
                now + 100,
                context={"role": "worker", "team": "Experience"},
                baseline=c(),
                now=now + 2,
            )
        row = self.l.reserve(
            "other-stream",
            "b",
            "gpt-6-luna",
            {"tokens": 20, "wpu": 1},
            now + 100,
            context={"role": "worker", "team": "Experience"},
            now=now + 2,
        )
        self.assertEqual(row["status"], "reserved")

    def test_reopen_durable_and_duplicate_event_conflict(self):
        self.observe("x", 1, c(3))
        self.l.close()
        self.l = UsageLedger(self.path)
        self.assertEqual(self.l.summary()["incremental"]["input_tokens"], 3)
        with self.assertRaises(ValueError):
            self.observe("x", 1, c(4))


if __name__ == "__main__":
    unittest.main()
