import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from engineering.metered_codex import Controller
from engineering.resources import choose_capacity
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

    def test_schema_version_is_initialized_and_newer_versions_fail_closed(self):
        self.assertEqual(self.l.db.execute("PRAGMA user_version").fetchone()[0], 1)
        self.l.close()
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA user_version=2")
        with self.assertRaisesRegex(ValueError, "usage_ledger_schema_newer_than_controller"):
            UsageLedger(self.path)
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA user_version=1")
        self.l = UsageLedger(self.path)

    def test_unrecognized_existing_schema_is_not_adopted(self):
        self.l.close()
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE unrelated(value TEXT)")
        with self.assertRaisesRegex(ValueError, "usage_ledger_schema_unrecognized"):
            UsageLedger(self.path)
        self.l = UsageLedger(self.tmp.name + "/fresh.sqlite")

    def test_incompatible_known_table_schema_is_not_stamped(self):
        self.l.close()
        incompatible = Path(self.tmp.name) / "incompatible.sqlite"
        with sqlite3.connect(incompatible) as db:
            db.executescript(
                """
                CREATE TABLE registrations(identity TEXT);
                CREATE TABLE events(event_id TEXT);
                CREATE TABLE turns(turn_id TEXT);
                CREATE TABLE turn_finishes(turn_id TEXT);
                CREATE TABLE dispatches(dispatch_id TEXT);
                CREATE TABLE receipts(event_id TEXT);
                """
            )
        with self.assertRaisesRegex(ValueError, "usage_ledger_schema_unrecognized"):
            UsageLedger(incompatible)
        self.l = UsageLedger(self.tmp.name + "/fresh.sqlite")

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
        self.l.attribute_dispatch(
            "overlap",
            "a",
            "e1",
            "gpt-6-luna",
            c(150),
            c(250),
            evidence={"event_id": "luna-250", "receipt_sha256": "other"},
        )
        overlap_summary = self.l.summary()
        self.assertEqual(overlap_summary["incremental"], c(250))
        self.assertEqual(overlap_summary["by_model"]["gpt-6.1-sol"]["input_tokens"], 100)
        self.assertEqual(overlap_summary["by_model"]["gpt-6-luna"]["input_tokens"], 50)
        overlaps = [
            row
            for row in overlap_summary["unresolved"]
            if row["reason"] == "model_attribution_overlap"
        ]
        self.assertEqual(len(overlaps), 1)
        self.assertEqual(overlaps[0]["counters"], c(50))

    def test_external_interval_overlaps_local_union_without_double_counting(self):
        self.l.observe(
            "a",
            "e1",
            "local-100",
            c(100),
            source="codex-rpc",
            sequence=100,
            observed_at=1,
        )
        self.l.attribute_dispatch(
            "external-local-duplicate",
            "a",
            "e1",
            "gpt-6-luna",
            c(),
            c(100),
            evidence={"event_id": "external-100", "receipt_sha256": "fixture"},
        )
        summary = self.l.summary()
        self.assertEqual(summary["incremental"], c(100))
        self.assertEqual(summary["by_model"]["gpt-6-luna"]["input_tokens"], 100)
        self.assertEqual(
            [row for row in summary["unresolved"] if row["reason"] == "model_attribution_overlap"],
            [],
        )

    def test_conflicting_models_at_same_endpoint_remain_unattributed(self):
        self.l.attribute_dispatch(
            "sol-claim",
            "a",
            "e1",
            "gpt-6.1-sol",
            c(),
            c(100),
            evidence={"event_id": "a-sol-100", "receipt_sha256": "sol"},
        )
        self.l.attribute_dispatch(
            "luna-claim",
            "a",
            "e1",
            "gpt-6-luna",
            c(),
            c(100),
            evidence={"event_id": "z-luna-100", "receipt_sha256": "luna"},
        )
        summary = self.l.summary()
        self.assertEqual(summary["incremental"], c(100))
        self.assertEqual(summary["by_model"], {})
        self.assertTrue(
            any(row["reason"] == "ambiguous_model_attribution" for row in summary["unresolved"])
        )

    def test_same_model_overlapping_claims_at_shared_endpoint_are_not_ambiguous(self):
        self.l.attribute_dispatch(
            "full-claim",
            "a",
            "e1",
            "gpt-6-luna",
            c(),
            c(100),
            evidence={"event_id": "full-100"},
        )
        self.l.attribute_dispatch(
            "partial-claim",
            "a",
            "e1",
            "gpt-6-luna",
            c(50),
            c(100),
            evidence={"event_id": "partial-100"},
        )
        summary = self.l.summary()
        self.assertEqual(summary["incremental"], c(100))
        self.assertEqual(summary["by_model"]["gpt-6-luna"]["input_tokens"], 100)
        self.assertFalse(
            any(row["reason"] == "ambiguous_model_attribution" for row in summary["unresolved"])
        )

    def test_public_external_import_accepts_mixed_model_interval_chain(self):
        batch = [
            {
                "kind": "thread_start",
                "event_id": "ext-start",
                "thread_id": "ext-thread",
                "epoch": "ext-epoch",
                "model": "gpt-6-luna",
                "counters": c(),
                "started_after_T0": True,
            },
            {
                "kind": "turn_interval",
                "event_id": "ext-luna-100",
                "turn_id": "ext-turn-1",
                "start_event_id": "ext-start",
                "thread_id": "ext-thread",
                "epoch": "ext-epoch",
                "model": "gpt-6-luna",
                "start": c(),
                "end": c(100),
            },
            {
                "kind": "turn_interval",
                "event_id": "ext-sol-200",
                "turn_id": "ext-turn-2",
                "start_event_id": "ext-start",
                "thread_id": "ext-thread",
                "epoch": "ext-epoch",
                "model": "gpt-6.1-sol",
                "start": c(100),
                "end": c(200),
            },
            {
                "kind": "turn_interval",
                "event_id": "ext-luna-300",
                "turn_id": "ext-turn-3",
                "start_event_id": "ext-start",
                "thread_id": "ext-thread",
                "epoch": "ext-epoch",
                "model": "gpt-6-luna",
                "start": c(200),
                "end": c(300),
            },
        ]
        encoded = json.dumps(batch, sort_keys=True, separators=(",", ":")).encode()
        result = self.l.import_external(
            batch,
            "trusted-fixture",
            encoded,
            hashlib.sha256(encoded).hexdigest(),
            external_allowlist={"trusted-fixture"},
        )
        summary = self.l.summary()
        self.assertEqual(result["intervals"], 3)
        self.assertEqual(summary["incremental"], c(300))
        self.assertEqual(summary["by_model"]["gpt-6-luna"]["input_tokens"], 200)
        self.assertEqual(summary["by_model"]["gpt-6.1-sol"]["input_tokens"], 100)

    def test_external_import_can_append_next_batch_to_existing_epoch(self):
        start = {
            "kind": "thread_start",
            "event_id": "append-start",
            "thread_id": "append-thread",
            "epoch": "append-epoch",
            "model": "gpt-6-luna",
            "counters": c(),
            "started_after_T0": True,
        }
        luna = {
            "kind": "turn_interval",
            "event_id": "append-luna-100",
            "turn_id": "append-turn-1",
            "start_event_id": "append-start",
            "thread_id": "append-thread",
            "epoch": "append-epoch",
            "model": "gpt-6-luna",
            "start": c(),
            "end": c(100),
        }
        sol = {
            "kind": "turn_interval",
            "event_id": "append-sol-200",
            "turn_id": "append-turn-2",
            "start_event_id": "append-start",
            "thread_id": "append-thread",
            "epoch": "append-epoch",
            "model": "gpt-6.1-sol",
            "start": c(100),
            "end": c(200),
        }

        def import_batch(batch):
            encoded = json.dumps(batch, sort_keys=True, separators=(",", ":")).encode()
            return self.l.import_external(
                batch,
                "trusted-fixture",
                encoded,
                hashlib.sha256(encoded).hexdigest(),
                external_allowlist={"trusted-fixture"},
            )

        import_batch([start, luna])
        self.assertEqual(import_batch([start, luna, sol])["intervals"], 2)
        summary = self.l.summary()
        self.assertEqual(summary["incremental"], c(200))
        self.assertEqual(summary["by_model"]["gpt-6-luna"]["input_tokens"], 100)
        self.assertEqual(summary["by_model"]["gpt-6.1-sol"]["input_tokens"], 100)

    def test_conflicting_duplicate_external_start_event_is_rejected(self):
        batch = [
            {
                "kind": "thread_start",
                "event_id": "duplicate-start",
                "thread_id": "duplicate-thread",
                "epoch": "duplicate-epoch",
                "model": "gpt-6-luna",
                "counters": c(),
                "started_after_T0": True,
            },
            {
                "kind": "thread_start",
                "event_id": "duplicate-start",
                "thread_id": "duplicate-thread",
                "epoch": "duplicate-epoch",
                "model": "gpt-6-luna",
                "counters": c(100),
                "started_after_T0": True,
            },
            {
                "kind": "turn_interval",
                "event_id": "duplicate-turn",
                "turn_id": "duplicate-turn-id",
                "start_event_id": "duplicate-start",
                "thread_id": "duplicate-thread",
                "epoch": "duplicate-epoch",
                "model": "gpt-6-luna",
                "start": c(),
                "end": c(100),
            },
        ]
        encoded = json.dumps(batch, sort_keys=True, separators=(",", ":")).encode()
        with self.assertRaisesRegex(ValueError, "external_event_id_conflict"):
            self.l.import_external(
                batch,
                "trusted-fixture",
                encoded,
                hashlib.sha256(encoded).hexdigest(),
                external_allowlist={"trusted-fixture"},
            )
        self.assertEqual(self.l.summary()["incremental"], c())

    def test_external_active_reservation_occupies_model_and_team_slots(self):
        batch = [
            {
                "kind": "active_reservation",
                "event_id": "active-sol-alpha",
                "team": "Alpha",
                "model": "gpt-6.1-sol",
                "tokens": 2500,
                "wpu": 1,
                "deadline_at": 9999,
                "status": "active",
            }
        ]
        encoded = json.dumps(batch, sort_keys=True, separators=(",", ":")).encode()
        self.l.import_external(
            batch,
            "trusted-fixture",
            encoded,
            hashlib.sha256(encoded).hexdigest(),
            external_allowlist={"trusted-fixture"},
        )
        active = self.l.summary()["reservations"]
        self.assertTrue(any(row.get("team") == "Alpha" for row in active))
        self.l.reserve(
            "local-sol-beta",
            "b",
            "gpt-6.1-sol",
            {"tokens": 100, "wpu": 1},
            9999,
            context={"team": "Beta"},
        )
        with self.assertRaisesRegex(
            RuntimeError, "model_dispatch_slots_full|team_dispatch_slots_full"
        ):
            self.l.reserve(
                "blocked-sol-gamma",
                "c",
                "gpt-6.1-sol",
                {"tokens": 100, "wpu": 1},
                9999,
                context={"team": "Gamma"},
            )

    def test_public_import_of_historical_unknown_adds_no_retrospective_reservation(self):
        batch = [
            {"kind": "historical_unknown", "event_id": "history-unknown-1", "counters": c(120)}
        ]
        encoded = json.dumps(batch, sort_keys=True, separators=(",", ":")).encode()
        controller = Controller(
            self.l,
            lambda: {},
            state=self.tmp.name,
            external_allowlist={"trusted-history"},
        )
        before = self.l.summary()
        controller.import_external(
            batch,
            "trusted-history",
            encoded,
            hashlib.sha256(encoded).hexdigest(),
        )
        after = self.l.summary()
        self.assertEqual(after["incremental"], before["incremental"])
        self.assertEqual(after["reservations"], before["reservations"])
        self.assertTrue(
            any(
                row["status"] == "unattributed_historical_usage" and row["counters"] == c(120)
                for row in after["historical_unknown"]
            )
        )
        rate = {
            "ordinaryUsageAllowed": True,
            "observed_at": 1_000,
            "evaluated_at": 1_000,
            "rateLimitsByLimitId": {
                "codex": {
                    "primary": {"usedPercent": 68, "resetsAt": 2_000},
                    "secondary": None,
                    "credits": {"hasCredits": False, "unlimited": False, "balance": 0},
                    "rateLimitReachedType": None,
                    "spendControlReached": False,
                },
                "weekly": {
                    "primary": {"usedPercent": 50, "resetsAt": 8_000},
                    "secondary": None,
                    "credits": {"hasCredits": False, "unlimited": False, "balance": 0},
                    "rateLimitReachedType": None,
                    "spendControlReached": False,
                },
            },
        }
        resources = {
            "available_memory_bytes": 2_000_000_000,
            "cpu_count": 8,
            "active_tokens": 0,
        }
        self.assertEqual(choose_capacity(rate, after, resources)["slots"], 2)

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
