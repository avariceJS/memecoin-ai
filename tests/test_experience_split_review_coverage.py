from __future__ import annotations

import unittest

from memecoin_experience.population_inclusion_audit import analyze as analyze_inclusion
from memecoin_experience.split_input_lineage import analyze as analyze_lineage
from memecoin_experience.split_review_coverage import analyze


class SplitReviewCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = {
            "snapshot_id": "snapshot-1",
            "snapshot_sha256": "a" * 64,
            "row_ids": ["mixed-in", "mixed-out", "kept"],
        }
        self.decisions = [
            {"row_id": "mixed-in", "group_id": "mixed", "disposition": "included", "reason": None},
            {"row_id": "mixed-out", "group_id": "mixed", "disposition": "excluded", "reason": "review"},
            {"row_id": "kept", "group_id": "whole", "disposition": "included", "reason": None},
        ]
        inclusion = analyze_inclusion(self.snapshot, self.decisions)
        self.plan = {"plan_id": "plan-1", "plan_sha256": "b" * 64,
                     "declared_splits": ["test", "train"]}
        snapshot_pin = {"snapshot_id": self.snapshot["snapshot_id"],
                        "snapshot_sha256": self.snapshot["snapshot_sha256"]}
        self.lineage = analyze_lineage(snapshot_pin, inclusion,
                                       [{"group_id": "whole", "split": "test"}], self.plan)
        # This is the frozen A interface: the lineage and inclusion envelopes
        # above come from their real producers; A's assignable rows exclude the
        # mixed group, including its individually included row.
        self.population = {
            "inclusion": inclusion,
            "lineage": self.lineage,
            "rows": [{"id": "kept", "group_id": "whole"}],
            "row_splits": [{"row_id": "kept", "split": "test"}],
            "denominators": {
                "snapshot_rows": 3, "individually_included_rows": 2,
                "assignable_rows": 1, "whole_group_excluded_rows": 2,
                "snapshot_groups": 2, "assignable_groups": 1, "excluded_groups": 1,
            },
        }
        self.strata = [{"id": "cohort", "supported_splits": ["train", "test"]}]

    def test_filters_excluded_rows_but_keeps_unknown_declarations(self) -> None:
        result = analyze(self.population, self.strata, [
            {"row_id": "kept", "stratum_id": "cohort"},
            {"row_id": "mixed-in", "stratum_id": "cohort"},
            {"row_id": "mixed-out", "stratum_id": "cohort"},
            {"row_id": "outside", "stratum_id": "cohort"},
        ])
        self.assertEqual(result["denominators"]["row_stratum_declarations_filtered_for_known_excluded_rows"], 2)
        self.assertEqual(result["denominators"]["row_stratum_declarations_audited"], 2)
        self.assertEqual(result["coverage"]["details"]["unknown_stratum_declarations"], [
            {"declaration_index": 1, "row_id": "outside", "stratum_id": "cohort"},
        ])
        self.assertEqual(result["target_deviation"], {"status": "not_requested", "reason": "missing_target_counts"})

    def test_coverage_preserves_missing_duplicate_and_unsupported_evidence(self) -> None:
        result = analyze(self.population,
                         [{"id": "cohort", "supported_splits": ["train"]}],
                         [{"row_id": "kept", "stratum_id": "cohort"},
                          {"row_id": "kept", "stratum_id": "cohort"},
                          {"row_id": "ghost", "stratum_id": "absent"}])
        self.assertEqual(result["coverage"]["details"]["duplicate_stratum_declarations"][0]["row_id"], "kept")
        self.assertEqual(result["coverage"]["details"]["unknown_stratum_declarations"][0]["row_id"], "ghost")
        unsupported = analyze(self.population,
                              [{"id": "cohort", "supported_splits": ["train"]}],
                              [{"row_id": "kept", "stratum_id": "cohort"}])
        self.assertEqual(unsupported["coverage"]["details"]["unsupported_split_declarations"], [
            {"row_id": "kept", "stratum_id": "cohort", "split": "test"},
        ])

    def test_target_deviation_uses_eligible_rows_and_zero_denominators(self) -> None:
        result = analyze(self.population, self.strata,
                         [{"row_id": "kept", "stratum_id": "cohort"}],
                         target_counts={"train": {"cohort": 0}, "test": {"cohort": 1}})
        deviation = result["target_deviation"]
        self.assertEqual(deviation["status"], "available")
        cells = deviation["audit"]["details"]["cells"]
        self.assertEqual(cells[0]["split"], "test")
        self.assertEqual(cells[0]["observed_count"], 1)
        self.assertIsNone(cells[1]["absolute_deviation_over_target"])
        self.assertEqual(cells[1]["denominator_status"], "zero_target")

    def test_ambiguous_or_unknown_attribution_does_not_invent_zero_counts(self) -> None:
        targets = {"train": {"cohort": 0}, "test": {"cohort": 1}}
        missing = analyze(self.population, self.strata, [], target_counts=targets)
        self.assertEqual(missing["target_deviation"], {
            "status": "unknown", "reason": "incomplete_or_ambiguous_row_strata"})
        unknown = analyze(self.population, self.strata,
                          [{"row_id": "kept", "stratum_id": "cohort"},
                           {"row_id": "ghost", "stratum_id": "cohort"}], target_counts=targets)
        self.assertEqual(unknown["target_deviation"], {
            "status": "unknown", "reason": "unknown_row_stratum_declarations"})

    def test_targets_must_exactly_align_with_declared_splits_and_strata(self) -> None:
        with self.assertRaisesRegex(ValueError, "^split_review_coverage_target_splits$"):
            analyze(self.population, self.strata, [{"row_id": "kept", "stratum_id": "cohort"}],
                    target_counts={"train": {"cohort": 0}, "validation": {"cohort": 0}})
        with self.assertRaisesRegex(ValueError, "^split_review_coverage_target_strata$"):
            analyze(self.population, self.strata, [{"row_id": "kept", "stratum_id": "cohort"}],
                    target_counts={"train": {}, "test": {"cohort": 1}})

    def test_empty_population_has_explicit_availability_and_zero_observations(self) -> None:
        population = {**self.population, "rows": [], "row_splits": []}
        result = analyze(population, self.strata, [],
                         target_counts={"train": {"cohort": 0}, "test": {"cohort": 0}})
        self.assertEqual(result["denominators"]["coverage_availability"], "empty_population")
        self.assertEqual(result["coverage"]["summary"]["expected_row_count"], 0)
        self.assertEqual(result["target_deviation"]["status"], "available")

    def test_empty_strata_keeps_target_deviation_unknown(self) -> None:
        result = analyze(self.population, [], [], target_counts={"train": {}, "test": {}})
        self.assertEqual(result["denominators"]["coverage_availability"], "empty_strata")
        self.assertEqual(result["target_deviation"], {"status": "unknown", "reason": "empty_strata"})


if __name__ == "__main__":
    unittest.main()
