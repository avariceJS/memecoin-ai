import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from memecoin_runner.research_evidence_gate import analyze


ROOT = Path(__file__).resolve().parents[1]
MODULE_ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
MODULE = "memecoin_runner.research_evidence_gate"


def row(identity="ok", **updates):
    result = {
        "id": identity,
        "decision_ns": 100,
        "input_available_ns": 95,
        "source_status": "qualified",
        "forecast_status": "available",
        "uncertainty": 0.2,
    }
    result.update(updates)
    return result


def payload(rows=None, **policy):
    values = {"max_input_age_ns": 5, "max_uncertainty": 0.5}
    values.update(policy)
    return {"policy": values, "rows": [row()] if rows is None else rows}


class ResearchEvidenceGateTests(unittest.TestCase):
    def test_individual_reasons_and_terminal_statuses(self):
        cases = [
            (row("source-unknown", source_status="unknown"), "source_unknown"),
            (row("source-unsupported", source_status="unsupported"), "source_unsupported"),
            (row("forecast-missing", forecast_status="missing"), "forecast_missing"),
            (row("forecast-unsupported", forecast_status="unsupported"), "forecast_unsupported"),
            (row("availability-unknown", input_available_ns=None), "availability_unknown"),
            (row("future", input_available_ns=101), "not_available_by_decision"),
            (row("stale", input_available_ns=94), "input_stale"),
            (row("uncertainty-unknown", uncertainty=None), "uncertainty_unknown"),
            (row("uncertainty-high", uncertainty=0.5001), "uncertainty_over_limit"),
        ]
        result = analyze(payload([item for item, _ in cases] + [row("eligible")]))
        self.assertEqual([item["reasons"] for item in result["details"][:-1]], [[reason] for _, reason in cases])
        self.assertEqual(result["details"][-1]["status"], "eligible_for_research_review")
        self.assertEqual(result["summary"]["eligible_count"], 1)
        self.assertEqual(result["summary"]["abstain_count"], len(cases))

    def test_all_reasons_are_emitted_in_fixed_order(self):
        result = analyze(payload([row(
            "all",
            source_status="unsupported",
            forecast_status="unsupported",
            input_available_ns=None,
            uncertainty=None,
        )]))
        self.assertEqual(result["details"][0]["reasons"], [
            "source_unsupported",
            "forecast_unsupported",
            "availability_unknown",
            "uncertainty_unknown",
        ])
        # Independent timing and uncertainty failures are both retained.
        result = analyze(payload([row(
            "multi",
            source_status="unknown",
            forecast_status="missing",
            input_available_ns=94,
            uncertainty=0.6,
        )]))
        self.assertEqual(result["details"][0]["reasons"], [
            "source_unknown", "forecast_missing", "input_stale", "uncertainty_over_limit"
        ])

    def test_boundaries_unknown_future_and_denominators(self):
        result = analyze(payload([
            row("age-equality", input_available_ns=95),
            row("uncertainty-equality", uncertainty=0.5),
            row("future", input_available_ns=101),
            row("unknown", input_available_ns=None),
        ]))
        self.assertEqual([d["status"] for d in result["details"]], [
            "eligible_for_research_review", "eligible_for_research_review", "abstain", "abstain"
        ])
        summary = result["summary"]
        self.assertEqual(summary["row_count"], summary["eligible_count"] + summary["abstain_count"])
        self.assertEqual(summary["not_available_by_decision_count"], 1)
        self.assertEqual(summary["availability_unknown_count"], 1)
        self.assertEqual(summary["input_stale_count"], 0)

    def test_empty_input_has_all_zero_counts(self):
        result = analyze(payload([]))
        self.assertEqual(result["details"], [])
        self.assertEqual(set(result["summary"].values()), {0})

    def test_order_permutation_hash_and_input_immutability(self):
        rows = [row("a"), row("b", uncertainty=0.8)]
        original = payload(rows)
        snapshot = copy.deepcopy(original)
        result = analyze(original)
        permuted = analyze(payload(list(reversed(rows))))
        by_id = {item["id"]: item for item in result["details"]}
        permuted_by_id = {item["id"]: item for item in permuted["details"]}
        self.assertEqual(by_id, permuted_by_id)
        self.assertEqual([item["id"] for item in result["details"]], ["a", "b"])
        expected_hash = hashlib.sha256(json.dumps(
            original,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")).hexdigest()
        self.assertEqual(result["input_sha256"], expected_hash)
        self.assertNotEqual(result["input_sha256"], permuted["input_sha256"])
        self.assertEqual(original, snapshot)
        self.assertEqual(set(result), {"schema", "kind", "input_sha256", "summary", "details", "limitations"})
        self.assertEqual(result["schema"], "brain-research-diagnostic.v1")
        self.assertEqual(result["kind"], "research_evidence_gate")

    def test_exact_keys_unique_ids_and_row_limit(self):
        invalid = [
            (None, "payload_must_be_object"),
            ({"policy": {}, "rows": [], "extra": 1}, "payload_keys_must_match_exactly"),
            ({"policy": {"max_input_age_ns": 0}, "rows": []}, "policy_keys_must_match_exactly"),
            ({"policy": payload([])["policy"], "rows": [{**row(), "extra": 1}]}, "row_keys_must_match_exactly"),
            (payload([row(), row()]), "row_ids_must_be_unique"),
            (payload([row(str_id) for str_id in [""]]), "row_id_must_be_nonempty_string"),
            (payload([row(str(i)) for i in range(10_001)]), "rows_exceed_limit"),
        ]
        for bad, message in invalid:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                analyze(bad)

    def test_policy_type_bounds_and_nonfinite_values(self):
        invalid = [
            (payload([], max_input_age_ns=True), "max_input_age_ns_must_be_plain_integer"),
            (payload([], max_input_age_ns=-1), "max_input_age_ns_out_of_range"),
            (payload([], max_input_age_ns=10**18 + 1), "max_input_age_ns_out_of_range"),
            (payload([], max_input_age_ns=1.0), "max_input_age_ns_must_be_plain_integer"),
            (payload([], max_uncertainty=True), "max_uncertainty_must_be_finite_number"),
            (payload([], max_uncertainty=-0.1), "max_uncertainty_out_of_range"),
            (payload([], max_uncertainty=1.1), "max_uncertainty_out_of_range"),
            (payload([], max_uncertainty=float("inf")), "max_uncertainty_out_of_range"),
            (payload([], max_uncertainty=10**1000), "max_uncertainty_out_of_range"),
        ]
        for bad, message in invalid:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                analyze(bad)

    def test_row_types_bounds_and_enums(self):
        invalid = [
            (row("x", decision_ns=True), "decision_ns_must_be_plain_integer"),
            (row("x", decision_ns=-1), "decision_ns_out_of_range"),
            (row("x", decision_ns=10**18 + 1), "decision_ns_out_of_range"),
            (row("x", input_available_ns=False), "input_available_ns_must_be_plain_integer"),
            (row("x", input_available_ns=10**18 + 1), "input_available_ns_out_of_range"),
            (row("x", source_status="bad"), "source_status_invalid"),
            (row("x", forecast_status="bad"), "forecast_status_invalid"),
            (row("x", uncertainty=True), "uncertainty_must_be_finite_number"),
            (row("x", uncertainty=-0.1), "uncertainty_out_of_range"),
            (row("x", uncertainty=1.1), "uncertainty_out_of_range"),
            (row("x", uncertainty=float("nan")), "uncertainty_out_of_range"),
            (row("x", uncertainty=10**1000), "uncertainty_out_of_range"),
        ]
        for bad_row, message in invalid:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                analyze(payload([bad_row]))
        with self.assertRaisesRegex(ValueError, "rows_must_be_list"):
            analyze({"policy": payload([])["policy"], "rows": ()})
        with self.assertRaisesRegex(ValueError, "row_must_be_object"):
            analyze(payload([None]))

    def run_cli(self, text):
        return subprocess.run(
            [sys.executable, "-m", MODULE],
            input=text,
            text=True,
            capture_output=True,
            cwd=ROOT,
            env=MODULE_ENV,
            check=False,
        )

    def test_cli_valid_json_stdout_and_exit_status(self):
        completed = self.run_cli(json.dumps(payload([])))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["kind"], "research_evidence_gate")
        self.assertEqual(result["summary"]["row_count"], 0)

    def test_cli_rejects_duplicate_keys_nonfinite_and_malformed_json(self):
        examples = [
            ('{"policy":{"max_input_age_ns":1,"max_input_age_ns":2,"max_uncertainty":0},"rows":[]}', "duplicate_json_key"),
            ('{"policy":{"max_input_age_ns":1,"max_uncertainty":NaN},"rows":[]}', "nonfinite_json_number"),
            ('{"policy":', "Expecting value"),
        ]
        for text, message in examples:
            with self.subTest(message=message):
                completed = self.run_cli(text)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertIn(message, completed.stderr)


if __name__ == "__main__":
    unittest.main()
