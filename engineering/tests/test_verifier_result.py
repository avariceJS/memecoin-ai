import unittest

from engineering.verifier_result import VerifierResultError, validate_verifier_result

VERIFIER = {
    "acceptance_id": "acceptance-check",
    "result_schema": "acceptance-result-v1",
    "case_field": "case",
    "passed_field": "passed",
}


class VerifierResultTests(unittest.TestCase):
    def test_accepts_exact_schema_acceptance_and_literal_true(self):
        result = validate_verifier_result(
            '{"schema":"acceptance-result-v1","case":"acceptance-check","passed":true}',
            VERIFIER,
        )
        self.assertIs(result["passed"], True)

    def test_rejects_malformed_ambiguous_and_nonpassing_results(self):
        cases = (
            ("not-json", "invalid_json"),
            ('{"schema":"v1","schema":"v2"}', "duplicate_key"),
            ('{"schema":"v1","case":"acceptance-check","passed":NaN}', "nonstandard_number"),
            ('{"schema":"other","case":"acceptance-check","passed":true}', "schema_mismatch"),
            (
                '{"schema":"acceptance-result-v1","case":"other","passed":true}',
                "acceptance_mismatch",
            ),
            (
                '{"schema":"acceptance-result-v1","case":"acceptance-check","passed":1}',
                "not_passing",
            ),
            ("[]", "not_object"),
        )
        for raw, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(VerifierResultError, error):
                validate_verifier_result(raw, VERIFIER)

    def test_rejects_oversized_result_and_invalid_contract(self):
        with self.assertRaisesRegex(VerifierResultError, "too_large"):
            validate_verifier_result(" " * 200_001, VERIFIER)
        with self.assertRaisesRegex(VerifierResultError, "contract_invalid"):
            validate_verifier_result("{}", {**VERIFIER, "case_field": ""})


if __name__ == "__main__":
    unittest.main()
