"""Exact synthetic cases for the population inclusion decision delta."""

import json
import unittest

from memecoin_experience.population_inclusion_delta import (
    analyze,
    analyze_json,
    canonical_decision_sha256,
)


def row(row_id, group_id, disposition, reason=None):
    return {"row_id": row_id, "group_id": group_id,
            "disposition": disposition, "reason": reason}


def decision(decision_id, rows):
    return {"decision_id": decision_id,
            "decision_sha256": canonical_decision_sha256(decision_id, rows),
            "rows": rows}


def sample():
    left_rows = [
        row("r1", "g1", "included"),
        row("r2", "g1", "excluded", "low quality"),
        row("r3", "g2", "excluded", "old rule"),
        row("left-only", "g3", "included"),
    ]
    right_rows = [
        row("r1", "g1", "excluded", "new rule"),
        row("r2", "g9", "excluded", "rejected"),
        row("r3", "g2", "included"),
        row("right-only", "g4", "excluded", "new row"),
    ]
    return {"left": decision("decision-A", left_rows),
            "right": decision("decision-B", right_rows)}


class PopulationInclusionDeltaTests(unittest.TestCase):
    def test_exact_matched_transitions_changes_and_identity_denominators(self):
        result = analyze(sample())

        self.assertEqual(result["schema"], "brain-population-inclusion-delta.v1")
        self.assertEqual(result["decisions"]["left"]["decision_id"], "decision-A")
        self.assertEqual(result["decisions"]["right"]["decision_id"], "decision-B")
        self.assertEqual(result["matched"], [
            {"row_id": "r1", "left": {"disposition": "included", "reason": None, "group_id": "g1"},
             "right": {"disposition": "excluded", "reason": "new rule", "group_id": "g1"}},
            {"row_id": "r2", "left": {"disposition": "excluded", "reason": "low quality", "group_id": "g1"},
             "right": {"disposition": "excluded", "reason": "rejected", "group_id": "g9"}},
            {"row_id": "r3", "left": {"disposition": "excluded", "reason": "old rule", "group_id": "g2"},
             "right": {"disposition": "included", "reason": None, "group_id": "g2"}},
        ])
        self.assertEqual(result["transitions"], {
            "included_to_excluded": ["r1"],
            "excluded_to_included": ["r3"],
            "disposition_unchanged": ["r2"],
        })
        self.assertEqual(result["reason_changes"], [
            {"row_id": "r1", "left": None, "right": "new rule"},
            {"row_id": "r2", "left": "low quality", "right": "rejected"},
            {"row_id": "r3", "left": "old rule", "right": None},
        ])
        self.assertEqual(result["group_changes"], [
            {"row_id": "r2", "left": "g1", "right": "g9"},
        ])
        self.assertEqual(result["identity_denominators"], {
            "left": 4, "right": 4, "matched": 3, "left_only": 1, "right_only": 1,
        })
        self.assertEqual(result["left_only_row_ids"], ["left-only"])
        self.assertEqual(result["right_only_row_ids"], ["right-only"])

    def test_json_interface_and_order_independent_pin(self):
        payload = sample()
        reordered = {
            "left": decision("decision-A", list(reversed(payload["left"]["rows"]))),
            "right": payload["right"],
        }
        self.assertEqual(payload["left"]["decision_sha256"], reordered["left"]["decision_sha256"])
        self.assertEqual(analyze_json(json.dumps(payload)), analyze(payload))

    def test_empty_decisions_have_zero_denominators(self):
        payload = {"left": decision("empty-left", []), "right": decision("empty-right", [])}
        result = analyze(payload)
        self.assertEqual(result["identity_denominators"], {
            "left": 0, "right": 0, "matched": 0, "left_only": 0, "right_only": 0,
        })
        self.assertEqual(result["matched"], [])

    def test_rejects_duplicate_row_identity(self):
        duplicate_rows = [row("same", "g", "included"), row("same", "g", "excluded", "reason")]
        payload = {"left": {"decision_id": "left", "decision_sha256": "a" * 64,
                            "rows": duplicate_rows},
                   "right": decision("right", [])}
        with self.assertRaisesRegex(ValueError, "population_inclusion_delta_invalid_input"):
            analyze(payload)

    def test_rejects_same_decision_identity_and_malformed_declaration(self):
        item = decision("same", [])
        with self.assertRaisesRegex(ValueError, "population_inclusion_delta_invalid_input"):
            analyze({"left": item, "right": item})
        with self.assertRaisesRegex(ValueError, "population_inclusion_delta_invalid_input"):
            analyze({"left": item})

    def test_rejects_bad_pin_and_excluded_row_without_reason(self):
        payload = sample()
        payload["left"]["decision_sha256"] = "not-a-pin"
        with self.assertRaisesRegex(ValueError, "population_inclusion_delta_invalid_input"):
            analyze(payload)
        bad = {"left": {"decision_id": "left", "decision_sha256": "a" * 64,
                         "rows": [row("r", "g", "excluded")]},
               "right": decision("right", [])}
        with self.assertRaisesRegex(ValueError, "population_inclusion_delta_invalid_input"):
            analyze(bad)

    def test_json_rejects_nonfinite_duplicate_keys_and_oversized_input(self):
        with self.assertRaisesRegex(ValueError, "population_inclusion_delta_invalid_input"):
            analyze_json(b'{"left":NaN,"right":{}}')
        with self.assertRaisesRegex(ValueError, "population_inclusion_delta_invalid_input"):
            analyze_json('{"left":{},"left":{},"right":{}}')
        with self.assertRaisesRegex(ValueError, "population_inclusion_delta_invalid_input"):
            analyze_json(b" " * (1_048_576 + 1))


if __name__ == "__main__":
    unittest.main()
