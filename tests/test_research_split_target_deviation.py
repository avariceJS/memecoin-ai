from __future__ import annotations

import json
import subprocess
import sys
import unittest
from copy import deepcopy

from memecoin_experience.split_target_deviation import analyze


class SplitTargetDeviationTests(unittest.TestCase):
    def test_exact_over_and_under_target_cells_and_totals(self) -> None:
        payload = {
            "observed_counts": {
                "train": {"mature": 8, "new": 3},
                "test": {"mature": 1, "new": 5},
            },
            "target_counts": {
                "train": {"mature": 6, "new": 4},
                "test": {"mature": 2, "new": 3},
            },
        }
        before = deepcopy(payload)
        result = analyze(payload)
        self.assertEqual(payload, before)
        self.assertEqual(result["summary"], {
            "status": "deviation", "split_count": 2, "stratum_count": 2, "cell_count": 4,
            "observed_total": 17, "target_total": 15, "signed_deviation": 2,
            "absolute_deviation": 6, "absolute_deviation_over_target": 6 / 15,
            "denominator_status": "positive_target",
        })
        self.assertEqual(result["details"]["cells"], [
            {"split": "test", "stratum": "mature", "observed_count": 1, "target_count": 2,
             "signed_deviation": -1, "absolute_deviation": 1, "absolute_deviation_over_target": 0.5,
             "denominator_status": "positive_target", "quota_support_status": "unsupported", "exact_quota_match": False},
            {"split": "test", "stratum": "new", "observed_count": 5, "target_count": 3,
             "signed_deviation": 2, "absolute_deviation": 2, "absolute_deviation_over_target": 2 / 3,
             "denominator_status": "positive_target", "quota_support_status": "supported", "exact_quota_match": False},
            {"split": "train", "stratum": "mature", "observed_count": 8, "target_count": 6,
             "signed_deviation": 2, "absolute_deviation": 2, "absolute_deviation_over_target": 1 / 3,
             "denominator_status": "positive_target", "quota_support_status": "supported", "exact_quota_match": False},
            {"split": "train", "stratum": "new", "observed_count": 3, "target_count": 4,
             "signed_deviation": -1, "absolute_deviation": 1, "absolute_deviation_over_target": 0.25,
             "denominator_status": "positive_target", "quota_support_status": "unsupported", "exact_quota_match": False},
        ])
        self.assertEqual(result["quota_support"], {
            "status": "unsupported", "supported_cell_count": 2, "unsupported_cell_count": 2,
            "exact_match_cell_count": 0, "cell_denominator": 4,
        })

    def test_zero_targets_are_explicit_and_exact_zero_is_supported(self) -> None:
        result = analyze({"observed_counts": {"test": {"empty": 0, "extra": 3}},
                          "target_counts": {"test": {"empty": 0, "extra": 0}}})
        self.assertIsNone(result["summary"]["absolute_deviation_over_target"])
        self.assertEqual(result["summary"]["denominator_status"], "zero_target")
        self.assertEqual(result["quota_support"]["status"], "supported")
        cells = result["details"]["cells"]
        self.assertEqual(cells[0]["denominator_status"], "zero_target")
        self.assertIsNone(cells[0]["absolute_deviation_over_target"])
        self.assertTrue(cells[0]["exact_quota_match"])
        self.assertEqual(cells[1]["absolute_deviation"], 3)
        self.assertFalse(cells[1]["exact_quota_match"])

    def test_all_zero_exact_table_and_split_totals(self) -> None:
        result = analyze({"observed_counts": {"a": {"x": 0}, "b": {"x": 0}},
                          "target_counts": {"a": {"x": 0}, "b": {"x": 0}}})
        self.assertEqual(result["summary"]["status"], "exact_match")
        self.assertEqual(result["quota_support"]["exact_match_cell_count"], 2)
        self.assertEqual(result["details"]["split_totals"], [
            {"split": "a", "observed_total": 0, "target_total": 0, "signed_deviation": 0,
             "absolute_deviation": 0, "absolute_deviation_over_target": None, "denominator_status": "zero_target"},
            {"split": "b", "observed_total": 0, "target_total": 0, "signed_deviation": 0,
             "absolute_deviation": 0, "absolute_deviation_over_target": None, "denominator_status": "zero_target"},
        ])

    def test_rejects_negative_non_integer_duplicate_or_misaligned_data(self) -> None:
        good = {"observed_counts": {"train": {"a": 1, "b": 2}},
                "target_counts": {"train": {"a": 1, "b": 2}}}
        invalid = [
            None, {}, {**good, "extra": 1}, {"observed_counts": {}, "target_counts": {}},
            {"observed_counts": {"train": {"a": 1}}, "target_counts": {"test": {"a": 1}}},
            {"observed_counts": {"train": {"a": 1}}, "target_counts": {"train": {"b": 1}}},
            {"observed_counts": {"train": {"a": -1}}, "target_counts": {"train": {"a": 0}}},
            {"observed_counts": {"train": {"a": True}}, "target_counts": {"train": {"a": 0}}},
            {"observed_counts": {"train": {"a": 1.0}}, "target_counts": {"train": {"a": 1}}},
            {"observed_counts": {"bad split": {"a": 1}}, "target_counts": {"bad split": {"a": 1}}},
            {"observed_counts": {"train": {"a": 1_000_001}}, "target_counts": {"train": {"a": 1}}},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "^split_target_deviation_invalid_input$"):
                analyze(payload)

    def test_cli_rejects_duplicate_keys_and_accepts_table(self) -> None:
        command = [sys.executable, "-m", "memecoin_experience.split_target_deviation"]
        good = subprocess.run(command, input=json.dumps({"observed_counts": {"test": {"a": 2}},
                                                         "target_counts": {"test": {"a": 1}}}),
                              text=True, capture_output=True)
        self.assertEqual(good.returncode, 0)
        self.assertEqual(json.loads(good.stdout)["summary"]["signed_deviation"], 1)
        duplicate = subprocess.run(command, input='{"observed_counts":{},"observed_counts":{},"target_counts":{}}',
                                   text=True, capture_output=True)
        self.assertEqual((duplicate.returncode, duplicate.stdout, duplicate.stderr),
                         (2, "", '{"error":"split_target_deviation_invalid_input"}\n'))


if __name__ == "__main__":
    unittest.main()
