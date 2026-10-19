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

    def test_reopen_durable_and_duplicate_event_conflict(self):
        self.observe("x", 1, c(3))
        self.l.close()
        self.l = UsageLedger(self.path)
        self.assertEqual(self.l.summary()["incremental"]["input_tokens"], 3)
        with self.assertRaises(ValueError):
            self.observe("x", 1, c(4))


if __name__ == "__main__":
    unittest.main()
