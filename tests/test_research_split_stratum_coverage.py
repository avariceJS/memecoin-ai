from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from memecoin_experience.split_stratum_coverage import analyze


def _cli(data: bytes, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    return subprocess.run([sys.executable, "-m", "memecoin_experience.split_stratum_coverage", *args],
                          input=data, capture_output=True, env=env)


class SplitStratumCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.complete = {
            "rows": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
            "strata": [{"id": "small", "supported_splits": ["train", "test"]},
                       {"id": "large", "supported_splits": ["train", "validation"]}],
            "row_strata": [{"row_id": "a", "stratum_id": "small"},
                           {"row_id": "b", "stratum_id": "small"},
                           {"row_id": "c", "stratum_id": "large"}],
            "row_splits": [{"row_id": "a", "split": "train"},
                           {"row_id": "b", "split": "test"},
                           {"row_id": "c", "split": "validation"}],
        }

    def test_complete_declarations_expose_denominators(self) -> None:
        result = analyze(self.complete)
        self.assertEqual(result["summary"]["status"], "clean")
        self.assertEqual(result["summary"]["row_stratum_denominator"], 3)
        self.assertEqual(result["summary"]["row_split_denominator"], 3)
        self.assertEqual(result["summary"]["supported_split_declaration_count"], 3)
        self.assertEqual(result["details"]["strata"], [
            {"stratum_id": "large", "supported_splits": ["train", "validation"],
             "expected_row_count": 1, "split_declaration_count": 1,
             "supported_declaration_count": 1, "unsupported_declaration_count": 0,
             "unsupported_declarations": []},
            {"stratum_id": "small", "supported_splits": ["test", "train"],
             "expected_row_count": 2, "split_declaration_count": 2,
             "supported_declaration_count": 2, "unsupported_declaration_count": 0,
             "unsupported_declarations": []},
        ])

    def test_unsupported_split_is_reported_against_declared_stratum(self) -> None:
        payload = {**self.complete, "row_splits": [*self.complete["row_splits"][:-1], {"row_id": "c", "split": "test"}]}
        result = analyze(payload)
        self.assertEqual(result["details"]["unsupported_split_declarations"],
                         [{"row_id": "c", "stratum_id": "large", "split": "test"}])
        self.assertEqual(result["summary"]["unsupported_split_declaration_count"], 1)

    def test_missing_and_unknown_declarations(self) -> None:
        payload = {**self.complete, "row_strata": self.complete["row_strata"][:-1],
                   "row_splits": [*self.complete["row_splits"][:-1], {"row_id": "ghost", "split": "test"}]}
        result = analyze(payload)
        self.assertEqual(result["details"]["missing_stratum_row_ids"], ["c"])
        self.assertEqual(result["details"]["missing_split_row_ids"], ["c"])
        self.assertEqual(result["details"]["unknown_split_declarations"],
                         [{"declaration_index": 2, "row_id": "ghost", "split": "test"}])

    def test_duplicate_declarations_count_extra_records(self) -> None:
        payload = {**self.complete,
                   "row_strata": [*self.complete["row_strata"], {"row_id": "a", "stratum_id": "small"}],
                   "row_splits": [*self.complete["row_splits"], {"row_id": "a", "split": "train"}]}
        result = analyze(payload)
        self.assertEqual(result["details"]["duplicate_stratum_declarations"],
                         [{"row_id": "a", "declaration_count": 2, "extra_declaration_count": 1}])
        self.assertEqual(result["details"]["duplicate_split_declarations"],
                         [{"row_id": "a", "declaration_count": 2, "extra_declaration_count": 1}])

    def test_cli_is_deterministic_and_rejects_duplicate_json_keys(self) -> None:
        encoded = json.dumps(self.complete, separators=(",", ":")).encode()
        result = _cli(encoded)
        self.assertEqual((result.returncode, result.stderr), (0, b""))
        self.assertEqual(json.loads(result.stdout), analyze(self.complete))
        self.assertEqual(result.stdout, json.dumps(analyze(self.complete), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n")
        invalid = _cli(b'{"rows":[],"rows":[],"strata":[],"row_strata":[],"row_splits":[]}')
        self.assertEqual((invalid.returncode, invalid.stdout, invalid.stderr),
                         (2, b"", b'{"error":"split_stratum_coverage_invalid_input"}\n'))

    def test_cli_rejects_input_over_byte_limit(self) -> None:
        oversized = _cli(b" " * (1_048_576 + 1))
        self.assertEqual((oversized.returncode, oversized.stdout, oversized.stderr),
                         (2, b"", b'{"error":"split_stratum_coverage_invalid_input"}\n'))


if __name__ == "__main__":
    unittest.main()
