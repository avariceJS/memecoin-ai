import json
import subprocess
import sys
import unittest
from pathlib import Path

from memecoin_experience import split_assignment_plan_delta as diagnostic


class SplitAssignmentPlanDeltaTests(unittest.TestCase):
    def test_directional_moves_denominators_and_split_deltas(self):
        payload = {
            "row_ids": ["a", "b", "c", "d", "e", "f", "g"],
            "before": [
                {"row_id": "a", "split": "train"},
                {"row_id": "b", "split": "train"},
                {"row_id": "c", "split": "validation"},
                {"row_id": "d", "split": "test"},
                {"row_id": "e", "split": None},
            ],
            "after": [
                {"row_id": "a", "split": "train"},
                {"row_id": "b", "split": "test"},
                {"row_id": "c", "split": "validation"},
                {"row_id": "d", "split": None},
                {"row_id": "e", "split": "train"},
                {"row_id": "f", "split": "test"},
            ],
        }
        result = diagnostic.analyze(payload)
        self.assertEqual(result["summary"], {
            "declared_row_count": 7,
            "before_assignment_count": 5,
            "after_assignment_count": 6,
            "same_plan_denominator": 2,
            "moved_assignment_denominator": 3,
            "added_assignment_denominator": 1,
            "removed_assignment_denominator": 0,
            "missing_before_denominator": 2,
            "missing_after_denominator": 1,
            "missing_both_denominator": 1,
            "explicitly_unassigned_before_count": 1,
            "explicitly_unassigned_after_count": 1,
        })
        self.assertEqual(result["details"]["directional_move_matrix"], [
            {"from_split": "train", "to_split": "test", "row_count": 1, "row_ids": ["b"]},
            {"from_split": "test", "to_split": "unassigned", "row_count": 1, "row_ids": ["d"]},
            {"from_split": "unassigned", "to_split": "train", "row_count": 1, "row_ids": ["e"]},
        ])
        self.assertEqual(result["details"]["split_counts"], {
            "train": {"before_count": 2, "after_count": 2, "delta": 0},
            "validation": {"before_count": 1, "after_count": 1, "delta": 0},
            "test": {"before_count": 1, "after_count": 2, "delta": 1},
        })
        self.assertEqual(result["details"]["missing_before_row_ids"], ["f", "g"])
        self.assertEqual(result["details"]["missing_after_row_ids"], ["g"])

    def test_empty_and_explicit_unassigned_plans(self):
        empty = diagnostic.analyze({"row_ids": [], "before": [], "after": []})
        self.assertEqual(empty["summary"]["same_plan_denominator"], 0)
        self.assertEqual(empty["details"]["directional_move_matrix"], [])
        result = diagnostic.analyze({
            "row_ids": ["r1", "r2"],
            "before": [{"row_id": "r1", "split": None}],
            "after": [{"row_id": "r1", "split": None}, {"row_id": "r2", "split": None}],
        })
        self.assertEqual(result["summary"]["same_plan_denominator"], 1)
        self.assertEqual(result["summary"]["added_assignment_denominator"], 1)
        self.assertEqual(result["summary"]["explicitly_unassigned_after_count"], 2)
        self.assertEqual(result["details"]["split_counts"]["train"]["delta"], 0)

    def test_group_containment_and_fragmentation_changes(self):
        payload = {
            "row_ids": ["a", "b", "c", "d", "outside"],
            "groups": [
                {"group_id": "contained", "row_ids": ["a", "b"]},
                {"group_id": "fragmented", "row_ids": ["c", "d"]},
            ],
            "before": [
                {"row_id": "a", "split": "train"}, {"row_id": "b", "split": "train"},
                {"row_id": "c", "split": "validation"}, {"row_id": "d", "split": "test"},
            ],
            "after": [
                {"row_id": "a", "split": "train"}, {"row_id": "b", "split": "test"},
                {"row_id": "c", "split": "validation"}, {"row_id": "d", "split": "validation"},
            ],
        }
        comparison = diagnostic.analyze(payload)["group_comparison"]
        self.assertEqual(comparison["summary"]["contained_to_fragmented_count"], 1)
        self.assertEqual(comparison["summary"]["fragmented_to_contained_count"], 1)
        self.assertEqual(comparison["summary"]["undeclared_row_count"], 1)
        self.assertEqual(comparison["details"]["undeclared_row_ids"], ["outside"])
        self.assertEqual([item["change"] for item in comparison["details"]["changes"]], [
            "contained_to_fragmented", "fragmented_to_contained",
        ])
        self.assertEqual(comparison["details"]["changes"][0]["before"]["observed_splits"], ["train"])
        self.assertEqual(comparison["details"]["changes"][0]["after"]["observed_splits"], ["train", "test"])

    def test_group_missing_unassigned_undeclared_and_zero_support(self):
        result = diagnostic.analyze({
            "row_ids": ["missing", "null", "uncovered"],
            "groups": [
                {"group_id": "empty", "row_ids": []},
                {"group_id": "known", "row_ids": ["missing", "null"]},
            ],
            "before": [{"row_id": "null", "split": None}],
            "after": [{"row_id": "missing", "split": "train"}, {"row_id": "null", "split": None}],
        })["group_comparison"]
        self.assertEqual(result["details"]["undeclared_row_ids"], ["uncovered"])
        self.assertEqual(result["details"]["zero_support_group_ids"], ["empty"])
        self.assertEqual(result["summary"]["before"]["missing_assignment_denominator"], 1)
        self.assertEqual(result["summary"]["after"]["missing_assignment_denominator"], 0)
        before_known = result["details"]["before_groups"][1]
        after_known = result["details"]["after_groups"][1]
        self.assertEqual(before_known["status"], "incomplete")
        self.assertEqual(before_known["explicitly_unassigned_count"], 1)
        self.assertEqual(after_known["status"], "contained")
        self.assertEqual(after_known["missing_assignment_count"], 0)

    def test_group_declarations_are_validated_and_deterministic(self):
        base = {
            "row_ids": ["a", "b"], "before": [], "after": [],
            "groups": [{"group_id": "g", "row_ids": ["a"]}],
        }
        self.assertNotIn("group_comparison", diagnostic.analyze({"row_ids": ["a"], "before": [], "after": []}))
        for groups in (
            [{"group_id": "g", "row_ids": ["unknown"]}],
            [{"group_id": "g", "row_ids": ["a", "a"]}],
            [{"group_id": "g", "row_ids": []}, {"group_id": "g", "row_ids": ["a"]}],
            [{"group_id": "g", "rows": ["a"]}],
            [{"group_id": "bad\n", "row_ids": ["a"]}],
        ):
            with self.subTest(groups=groups), self.assertRaisesRegex(ValueError, "split_assignment_plan_delta_invalid_input"):
                diagnostic.analyze({**base, "groups": groups})
        once = diagnostic.analyze({**base, "groups": [
            {"group_id": "z", "row_ids": ["b"]}, {"group_id": "a", "row_ids": ["a"]},
        ]})
        twice = diagnostic.analyze({**base, "groups": [
            {"group_id": "a", "row_ids": ["a"]}, {"group_id": "z", "row_ids": ["b"]},
        ]})
        self.assertEqual(once["group_comparison"], twice["group_comparison"])
        self.assertEqual(once, diagnostic.analyze({**base, "groups": [
            {"group_id": "z", "row_ids": ["b"]}, {"group_id": "a", "row_ids": ["a"]},
        ]}))

    def test_invalid_identity_split_duplicates_and_unknown_rows(self):
        base = {"row_ids": ["r"], "before": [], "after": []}
        for payload in (
            {"row_ids": ["r", "r"], "before": [], "after": []},
            {"row_ids": ["r"], "before": [{"row_id": "r", "split": "holdout"}], "after": []},
            {"row_ids": ["r"], "before": [{"row_id": "x", "split": "train"}], "after": []},
            {"row_ids": ["r"], "before": [{"row_id": "r", "split": "train"}] * 2, "after": []},
            {"row_ids": ["r\n"], "before": [], "after": []},
            {**base, "extra": True},
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "split_assignment_plan_delta_invalid_input"):
                diagnostic.analyze(payload)

    def test_input_limits(self):
        with self.assertRaisesRegex(ValueError, "split_assignment_plan_delta_invalid_input"):
            diagnostic.analyze({"row_ids": [f"r{i}" for i in range(5001)], "before": [], "after": []})
        with self.assertRaisesRegex(ValueError, "split_assignment_plan_delta_invalid_input"):
            diagnostic.analyze({"row_ids": ["r"], "before": [{"row_id": "r", "split": "train"}] * 10001, "after": []})

    def test_cli_emits_diagnostic_and_rejects_duplicate_json_keys(self):
        root = Path(__file__).resolve().parents[1]
        env_path = str(root / "src")
        script = root / "src" / "memecoin_experience" / "split_assignment_plan_delta.py"
        valid = json.dumps({"row_ids": ["r"], "before": [], "after": [{"row_id": "r", "split": "train"}]})
        success = subprocess.run([sys.executable, str(script)], input=valid, text=True, capture_output=True, env={"PYTHONPATH": env_path})
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(json.loads(success.stdout)["summary"]["added_assignment_denominator"], 1)
        duplicate_key = '{"row_ids":[],"row_ids":[],"before":[],"after":[]}'
        failure = subprocess.run([sys.executable, str(script)], input=duplicate_key, text=True, capture_output=True, env={"PYTHONPATH": env_path})
        self.assertEqual(failure.returncode, 2)
        self.assertEqual(json.loads(failure.stderr), {"error": "split_assignment_plan_delta_invalid_input"})


if __name__ == "__main__":
    unittest.main()
