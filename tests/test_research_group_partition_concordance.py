from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from memecoin_experience.group_partition_concordance import analyze


def _cli(raw: bytes, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    return subprocess.run([sys.executable, "-m", "memecoin_experience.group_partition_concordance", *args], input=raw, capture_output=True, env=env)


class GroupPartitionConcordanceTests(unittest.TestCase):
    def test_perfect_agreement_ignores_group_names(self) -> None:
        result = analyze({
            "row_ids": ["a", "b", "c", "d"],
            "left": [{"row_id": "a", "group": "x"}, {"row_id": "b", "group": "x"}, {"row_id": "c", "group": "y"}, {"row_id": "d", "group": "y"}],
            "right": [{"row_id": "a", "group": "one"}, {"row_id": "b", "group": "one"}, {"row_id": "c", "group": "two"}, {"row_id": "d", "group": "two"}],
        })
        self.assertEqual(result["summary"]["pairwise_agreement_rate"], 1.0)
        self.assertEqual(result["summary"]["alignment_rate"], 1.0)
        self.assertEqual(result["details"]["pairwise_cells"], {
            "same_in_both": 2, "same_left_different_right": 0,
            "different_left_same_right": 0, "different_in_both": 4,
        })

    def test_split_and_merge_disagreement_has_clear_pair_denominator(self) -> None:
        result = analyze({
            "row_ids": ["a", "b", "c", "d"],
            "left": [{"row_id": r, "group": "all"} for r in "abcd"],
            "right": [{"row_id": r, "group": "first" if r in "ab" else "second"} for r in "abcd"],
        })
        self.assertEqual(result["summary"]["pairwise_denominator"], 6)
        self.assertEqual(result["summary"]["pairwise_agreement_count"], 2)
        self.assertAlmostEqual(result["summary"]["pairwise_agreement_rate"], 1 / 3)
        self.assertEqual(result["summary"]["alignment_count"], 2)

    def test_missing_and_unassigned_are_separate(self) -> None:
        result = analyze({
            "row_ids": ["a", "b", "c", "d"],
            "left": [{"row_id": "a", "group": "g"}, {"row_id": "b", "group": None}, {"row_id": "c", "group": "g"}],
            "right": [{"row_id": "a", "group": "h"}, {"row_id": "b", "group": "h"}, {"row_id": "d", "group": None}],
        })
        self.assertEqual(result["summary"]["left_missing_row_count"], 1)
        self.assertEqual(result["summary"]["right_missing_row_count"], 1)
        self.assertEqual(result["summary"]["left_unassigned_row_count"], 1)
        self.assertEqual(result["summary"]["right_unassigned_row_count"], 1)
        self.assertEqual(result["summary"]["jointly_assigned_row_count"], 1)
        self.assertIsNone(result["summary"]["pairwise_agreement_rate"])

    def test_duplicate_and_malformed_input_rejected(self) -> None:
        valid = {"row_ids": ["a"], "left": [{"row_id": "a", "group": "x"}], "right": []}
        invalid = [
            None, {}, {**valid, "extra": 1},
            {"row_ids": ["a", "a"], "left": [], "right": []},
            {"row_ids": ["a"], "left": [{"row_id": "a", "group": "x"}, {"row_id": "a", "group": "y"}], "right": []},
            {"row_ids": ["a"], "left": [{"row_id": "unknown", "group": "x"}], "right": []},
            {"row_ids": ["a"], "left": [{"row_id": "a", "group": 2}], "right": []},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, "^group_partition_concordance_invalid_input$"):
                    analyze(payload)

    def test_cli_json_contract_and_stable_errors(self) -> None:
        payload = {"row_ids": ["a", "b"], "left": [{"row_id": "a", "group": "x"}], "right": [{"row_id": "b", "group": "y"}]}
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        good = _cli(encoded)
        self.assertEqual((good.returncode, good.stderr), (0, b""))
        self.assertEqual(json.loads(good.stdout), analyze(payload))
        self.assertTrue(good.stdout.endswith(b"\n"))
        for raw in (b'{"row_ids":[],"row_ids":[],"left":[],"right":[]}', b'{"row_ids":[],"left":[],"right":[],"n":NaN}', b"\xff"):
            bad = _cli(raw)
            self.assertEqual((bad.returncode, bad.stdout, bad.stderr), (2, b"", b'{"error":"group_partition_concordance_invalid_input"}\n'))
        bad_args = _cli(encoded, "extra")
        self.assertEqual((bad_args.returncode, bad_args.stdout, bad_args.stderr), (2, b"", b'{"error":"group_partition_concordance_invalid_input"}\n'))


if __name__ == "__main__":
    unittest.main()
