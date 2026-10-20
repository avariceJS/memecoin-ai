import tempfile
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

    def test_certified_unused_start_settles_only_after_registered_success(self):
        self.l.reserve(
            "start:certified",
            "a",
            "gpt-6.1-sol",
            {"tokens": 1, "wpu": 1},
            100,
            context={"team": "Alpha"},
        )
        self.l.consume_dispatch("start:certified")
        self.l.record_outcome("start:certified", "completed", {"thread_id": "thread-1"})
        self.assertEqual(len(self.l.summary()["reservations"]), 1)
        self.l.register(
            "thread-1",
            "initial",
            "gpt-6.1-sol",
            c(),
            evidence="provider-certified-new-thread:start:certified",
            started_after_T0=True,
        )
        self.l.settle_certified_start("start:certified", "thread-1")
        self.assertEqual(self.l.summary()["reservations"], [])
        self.l.reserve(
            "start:ambiguous",
            "a",
            "gpt-6.1-sol",
            {"tokens": 1, "wpu": 1},
            100,
            context={"team": "Alpha"},
        )
        self.l.consume_dispatch("start:ambiguous")
        with self.assertRaisesRegex(ValueError, "outcome_missing"):
            self.l.settle_certified_start("start:ambiguous", "thread-2")
        self.assertEqual(len(self.l.summary()["reservations"]), 1)

    def test_attributed_interval_preserves_unknown_counter_gap(self):
        self.l.observe("a", "e1", "zero", c(), source="baseline", sequence=0, observed_at=0)
        self.l.attribute_dispatch(
            "external-turn",
            "a",
            "e1",
            "gpt-6.1-sol",
            c(100),
            c(200),
            evidence={"event_id": "sol-200", "receipt_sha256": "fixture"},
        )
        summary = self.l.summary()
        self.assertEqual(summary["incremental"], c(200))
        self.assertEqual(summary["by_model"]["gpt-6.1-sol"]["input_tokens"], 100)
        gaps = [row for row in summary["unresolved"] if row["reason"] == "model_attribution_gap"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["counters"], c(100))
        with self.assertRaisesRegex(ValueError, "overlapping_attribution_interval"):
            self.l.attribute_dispatch(
                "overlap",
                "a",
                "e1",
                "gpt-6-luna",
                c(150),
                c(250),
                evidence={"event_id": "luna-250", "receipt_sha256": "other"},
            )
        self.assertEqual(self.l.summary()["by_model"]["gpt-6.1-sol"]["input_tokens"], 100)

    def test_raw_rpc_ingress_is_byte_exact_and_persists_on_separate_connection(self):
        raw = b'{"method":"turn/started"}\r\n'
        event_id = self.l.persist_raw_rpc(raw)
        self.l.close()
        reopened = UsageLedger(self.path)
        try:
            row = reopened.db.execute(
                "SELECT raw_bytes FROM rpc_ingress WHERE ingress_id=?", (event_id,)
            ).fetchone()
            self.assertEqual(bytes(row["raw_bytes"]), raw)
        finally:
            reopened.close()
        self.l = UsageLedger(self.path)

    def test_dispatch_reservation_summary_retains_team_identity(self):
        self.l.reserve(
            "dispatch-alpha",
            "a",
            "gpt-6.1-sol",
            {"tokens": 1000, "wpu": 0.1},
            100,
            context={"team": "Alpha"},
        )
        reservations = self.l.summary()["reservations"]
        self.assertEqual(len(reservations), 1)
        self.assertEqual(reservations[0]["team"], "Alpha")

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

    def test_reopen_durable_and_duplicate_event_conflict(self):
        self.observe("x", 1, c(3))
        self.l.close()
        self.l = UsageLedger(self.path)
        self.assertEqual(self.l.summary()["incremental"]["input_tokens"], 3)
        with self.assertRaises(ValueError):
            self.observe("x", 1, c(4))


if __name__ == "__main__":
    unittest.main()
