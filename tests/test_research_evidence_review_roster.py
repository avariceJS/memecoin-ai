from __future__ import annotations

import copy
import unittest

from memecoin_runner.evidence_review_roster import prepare


def row(identity: str = "ok", **updates: object) -> dict:
    value = {
        "id": identity,
        "decision_ns": 100,
        "input_available_ns": 95,
        "source_status": "qualified",
        "forecast_status": "available",
        "uncertainty": 0.2,
        "loss": 2.0,
    }
    value.update(updates)
    return value


def payload(rows: list[dict] | None = None, **updates: object) -> dict:
    value = {
        "policy": {"max_input_age_ns": 5, "max_uncertainty": 0.8},
        "thresholds": [1, 0.2, 0.5],
        "rows": [row()] if rows is None else rows,
    }
    value.update(updates)
    return value


class EvidenceReviewRosterTests(unittest.TestCase):
    def test_prepare_builds_all_rows_in_original_order_and_preserves_null_loss(self) -> None:
        source = payload([
            row("unsupported", source_status="unsupported", loss=None),
            row("known", uncertainty=0.5, loss=3),
            row("missing-loss", uncertainty=0.8, loss=None),
        ])
        gate, frontier = prepare(source)

        self.assertEqual(gate, {
            "policy": {"max_input_age_ns": 5, "max_uncertainty": 0.8},
            "rows": [
                {key: value for key, value in item.items() if key != "loss"}
                for item in source["rows"]
            ],
        })
        self.assertEqual(frontier, {
            "thresholds": [1, 0.2, 0.5],
            "rows": [
                {"id": "unsupported", "supported": True, "uncertainty": 0.2, "loss": None},
                {"id": "known", "supported": True, "uncertainty": 0.5, "loss": 3},
                {"id": "missing-loss", "supported": True, "uncertainty": 0.8, "loss": None},
            ],
        })
        self.assertEqual([item["id"] for item in gate["rows"]], ["unsupported", "known", "missing-loss"])

    def test_prepare_outputs_are_fresh_and_do_not_mutate_input(self) -> None:
        source = payload([row()])
        snapshot = copy.deepcopy(source)
        gate, frontier = prepare(source)

        self.assertEqual(source, snapshot)
        self.assertIsNot(gate["policy"], source["policy"])
        self.assertIsNot(gate["rows"], source["rows"])
        self.assertIsNot(gate["rows"][0], source["rows"][0])
        self.assertIsNot(frontier["thresholds"], source["thresholds"])
        self.assertIsNot(frontier["rows"], source["rows"])
        self.assertIsNot(frontier["rows"][0], source["rows"][0])
        gate["policy"]["max_input_age_ns"] = 1
        frontier["rows"][0]["loss"] = 99
        self.assertEqual(source, snapshot)

    def test_empty_roster_is_valid(self) -> None:
        gate, frontier = prepare(payload([]))
        self.assertEqual(gate["rows"], [])
        self.assertEqual(frontier["rows"], [])

    def test_prepare_rejects_malformed_complete_roster(self) -> None:
        invalid = [
            (None, "payload_must_be_object"),
            ({"policy": {}, "thresholds": [], "rows": [], "extra": 1}, "payload_keys_must_match_exactly"),
            ({"policy": {"max_input_age_ns": 1}, "thresholds": [], "rows": []}, "policy_keys_must_match_exactly"),
            (payload([dict(row(), loss_absent=True)]), "row_keys_must_match_exactly"),
            (payload([row("dup"), row("dup")]), "row_ids_must_be_unique"),
            (payload([row("", source_status="unsupported")]), "row_id_must_be_nonempty_string"),
            (payload([row("bad-loss", source_status="unsupported", loss=True)]), "loss_must_be_finite_number"),
            (payload([row("bad-loss", source_status="unsupported", loss=-1)]), "loss_out_of_range"),
            (payload([row("bad-loss", source_status="unsupported", loss=10**1000)]), "numeric_value_out_of_range"),
            (payload([row("bad-uncertainty", source_status="unsupported", uncertainty=True)]), "uncertainty_must_be_finite_number"),
            (payload([row("bad-uncertainty", source_status="unsupported", uncertainty=float("inf"))]), "uncertainty_out_of_range"),
            (payload([row("bad-threshold")], thresholds=[True]), "threshold_must_be_finite_number"),
            (payload([row("bad-threshold")], thresholds=[10**1000]), "numeric_value_out_of_range"),
        ]
        for bad, message in invalid:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                prepare(bad)

    def test_loss_is_required_but_explicit_null_is_valid(self) -> None:
        missing = row("missing")
        del missing["loss"]
        with self.assertRaisesRegex(ValueError, "row_keys_must_match_exactly"):
            prepare(payload([missing]))
        gate, frontier = prepare(payload([row("null", loss=None)]))
        self.assertNotIn("loss", gate["rows"][0])
        self.assertIsNone(frontier["rows"][0]["loss"])

    def test_gate_policy_and_frontier_threshold_boundaries_are_validated(self) -> None:
        gate, frontier = prepare(payload(
            [row("equality", uncertainty=0.8)],
            policy={"max_input_age_ns": 5, "max_uncertainty": 0.8},
            thresholds=[0.8],
        ))
        self.assertEqual(gate["policy"]["max_uncertainty"], 0.8)
        self.assertEqual(frontier["thresholds"], [0.8])
        with self.assertRaisesRegex(ValueError, "max_uncertainty_must_be_finite_number"):
            prepare(payload([], policy={"max_input_age_ns": 5, "max_uncertainty": True}))


if __name__ == "__main__":
    unittest.main()
