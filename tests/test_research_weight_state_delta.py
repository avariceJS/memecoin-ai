"""Synthetic exact cases for the pinned example-weight state comparison."""

import json
import unittest

from memecoin_experience.weight_state_delta import analyze, analyze_json, canonical_state_sha256


def row(example_id, entity_id, cohort_id, weight):
    return {"example_id": example_id, "entity_id": entity_id,
            "cohort_id": cohort_id, "weight": weight}


def state(state_id, examples):
    return {"state_id": state_id,
            "state_sha256": canonical_state_sha256(state_id, examples),
            "examples": examples}


def unvalidated_state(state_id, examples):
    """Construct declarations for negative tests without validating the rows."""
    return {"state_id": state_id, "state_sha256": "a" * 64, "examples": examples}


class WeightStateDeltaTests(unittest.TestCase):
    def test_exact_metrics_and_support_denominators(self):
        before = [row("a", "shared", "c1", 1), row("b", "shared", "c2", 1),
                  row("c", "other", "c2", 0), row("gone", "old", "c3", 2)]
        after = [row("a", "shared", "c1", 2), row("b", "moved", "c2-new", 0),
                 row("c", "other", "c2", 1), row("new", "fresh", "c4", 1)]
        result = analyze({"before": state("weights-A", before), "after": state("weights-B", after)})

        self.assertEqual(result["schema"], "brain-research-diagnostic.v1")
        self.assertEqual(result["kind"], "weight_state_delta")
        self.assertEqual(result["metrics"]["before"], {
            "status": "positive_weight", "example_count": 4, "positive_example_count": 3,
            "entity_count": 3, "positive_entity_count": 2, "sum_weights": 4.0,
            "sum_squared_example_weights": 6.0, "sum_squared_entity_weights": 8.0,
            "example_concentration": 0.375, "entity_concentration": 0.5,
            "example_ess": 8 / 3, "entity_ess": 2.0,
        })
        self.assertEqual(result["metrics"]["after"]["sum_weights"], 4.0)
        self.assertEqual(result["metrics"]["after"]["sum_squared_example_weights"], 6.0)
        self.assertEqual(result["metrics"]["after"]["sum_squared_entity_weights"], 6.0)
        self.assertEqual(result["metrics"]["deltas"], {
            "sum_weights": 0.0, "example_concentration": 0.0,
            "entity_concentration": -0.125, "example_ess": 0.0,
            "entity_ess": (8 / 3) - 2.0,
        })
        support = result["support"]
        self.assertEqual(support["identity_denominators"], {
            "before": 4, "after": 4, "matched": 3,
            "missing_from_after": 1, "added_in_after": 1,
        })
        self.assertEqual(support["matched_id_denominator"], 3)
        self.assertEqual(support["positive_weight_denominators"], {
            "before": 3, "after": 3, "matched_before": 2, "matched_after": 2,
        })
        self.assertEqual(support["missing_example_ids"], ["gone"])
        self.assertEqual(support["added_example_ids"], ["new"])
        self.assertEqual(support["zero_to_positive_example_ids"], ["c"])
        self.assertEqual(support["positive_to_zero_example_ids"], ["b"])
        self.assertEqual([item["example_id"] for item in support["changed_weights"]], ["a", "b", "c"])
        self.assertEqual(support["changed_identity"], [{
            "example_id": "b",
            "before": {"entity_id": "shared", "cohort_id": "c2"},
            "after": {"entity_id": "moved", "cohort_id": "c2-new"},
        }])

    def test_all_zero_weights_have_null_concentration_ess_and_deltas(self):
        result = analyze({
            "before": state("zero-A", [row("a", "e", "c", 0)]),
            "after": state("zero-B", [row("a", "e", "c", 0), row("b", "f", "d", 0)]),
        })
        self.assertEqual(result["metrics"]["before"]["status"], "zero_total_weight")
        self.assertIsNone(result["metrics"]["before"]["example_ess"])
        self.assertIsNone(result["metrics"]["after"]["entity_concentration"])
        self.assertEqual(result["metrics"]["deltas"], {
            "sum_weights": 0.0, "example_concentration": None,
            "entity_concentration": None, "example_ess": None, "entity_ess": None,
        })

    def test_json_interface_duplicate_keys_nonfinite_and_bound(self):
        payload = {"before": state("json-A", [row("x", "e", "c", 1)]),
                   "after": state("json-B", [row("x", "e", "c", 2)])}
        self.assertEqual(analyze_json(json.dumps(payload)), analyze(payload))
        self.assertEqual(payload["before"]["state_sha256"], canonical_state_sha256(
            "json-A", list(reversed(payload["before"]["examples"]))))
        for bad in (b'{"before":NaN,"after":{}}',
                    b'{"before":{},"before":{},"after":{}}',
                    b" " * (8 * 1024 * 1024 + 1)):
            with self.subTest(bad=bad[:24]), self.assertRaisesRegex(ValueError, "weight_state_delta_invalid_input"):
                analyze_json(bad)

    def test_rejects_malformed_states_rows_weights_and_oversized_support(self):
        valid = state("valid", [])
        other = state("other", [])
        bad_payloads = [
            {},
            {"before": valid, "after": valid},
            {"before": {**valid, "state_sha256": "bad"}, "after": other},
            {"before": unvalidated_state("duplicate", [row("x", "e", "c", 1), row("x", "f", "d", 2)]), "after": other},
            {"before": unvalidated_state("boolean", [row("x", "e", "c", True)]), "after": other},
            {"before": unvalidated_state("negative", [row("x", "e", "c", -1)]), "after": other},
            {"before": unvalidated_state("tiny", [row("x", "e", "c", 1e-101)]), "after": other},
            {"before": unvalidated_state("large", [row("x", "e", "c", 1e101)]), "after": other},
            {"before": unvalidated_state("nan", [row("x", "e", "c", float("nan"))]), "after": other},
            {"before": unvalidated_state("extra", [{**row("x", "e", "c", 1), "extra": 0}]), "after": other},
            {"before": unvalidated_state("id", [row("not allowed", "e", "c", 1)]), "after": other},
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "weight_state_delta_invalid_input"):
                analyze(payload)
        too_many = [row(str(index), "e", "c", 1) for index in range(10_001)]
        with self.assertRaisesRegex(ValueError, "weight_state_delta_invalid_input"):
            analyze({"before": unvalidated_state("too-many", too_many), "after": other})

    def test_maximum_accepted_state_remains_finite(self):
        examples = [row(f"{index:05}", "one", "cohort", 1e100) for index in range(10_000)]
        result = analyze({"before": state("large-A", examples), "after": state("large-B", examples)})
        self.assertEqual(result["metrics"]["before"]["sum_squared_entity_weights"], 1e208)
        self.assertEqual(result["metrics"]["before"]["example_ess"], 10_000.0)
        self.assertEqual(result["metrics"]["before"]["entity_ess"], 1.0)


if __name__ == "__main__":
    unittest.main()
