"""Focused entrypoint tests independent of the sibling review implementation."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import types
import unittest
from unittest import mock

from memecoin_runner import abstention_frontier, research_evidence_gate

MODULES = [research_evidence_gate, abstention_frontier]


class ReviewEntrypointTests(unittest.TestCase):
    def test_real_review_subprocess_rejects_bad_json_and_flags(self):
        cases = [
            (["--review"], '{"policy":{},"policy":{},"thresholds":[0],"rows":[]}'),
            (["--review"], '{"policy":{},"thresholds":[0],"rows":[{"id":"x","id":"y"}]}'),
            (["--review"], '{"policy":{},"thresholds":[Infinity],"rows":[]}'),
            (["--review"], '{"policy":{},"thresholds":[1e999],"rows":[]}'),
            (["--review"], '{"policy":'),
            (["--review", "--review"], "{}"),
            (["--review", "extra"], "{}"),
            (["--unknown"], "{}"),
        ]
        for module in MODULES:
            for flags, text in cases:
                with self.subTest(module=module.__name__, flags=flags, text=text):
                    process = subprocess.run(
                        [sys.executable, "-m", module.__name__, *flags],
                        input=text, text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(process.returncode, 2)
                    self.assertEqual(process.stdout, "")
                    self.assertTrue(process.stderr.startswith("error: "))
                    self.assertNotIn("Traceback", process.stderr)

    def test_analyze_review_lazily_dispatches_to_core_helper(self):
        for module in MODULES:
            with self.subTest(module=module.__name__):
                payload = {"policy": {}, "thresholds": [], "rows": []}
                expected = {"kind": "research_evidence_review"}
                helper = types.ModuleType("memecoin_runner.evidence_review")
                helper.analyze = mock.Mock(return_value=expected)
                with mock.patch.dict(sys.modules, {"memecoin_runner.evidence_review": helper}):
                    self.assertIs(module.analyze_review(payload), expected)
                helper.analyze.assert_called_once_with(payload)

    def test_review_cli_dispatches_and_emits_single_json_envelope(self):
        for module in MODULES:
            with self.subTest(module=module.__name__):
                payload = {"policy": {}, "thresholds": [0.5], "rows": []}
                expected = {"kind": "research_evidence_review", "summary": {"row_count": 0}}
                helper = types.ModuleType("memecoin_runner.evidence_review")
                helper.analyze = mock.Mock(return_value=expected)
                stdout, stderr = io.StringIO(), io.StringIO()
                with (
                    mock.patch.dict(sys.modules, {"memecoin_runner.evidence_review": helper}),
                    contextlib.redirect_stdout(stdout),
                    contextlib.redirect_stderr(stderr),
                    mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))),
                ):
                    status = module.main(["--review"])
                self.assertEqual(status, 0)
                self.assertEqual(json.loads(stdout.getvalue()), expected)
                self.assertTrue(stdout.getvalue().endswith("\n"))
                self.assertEqual(stderr.getvalue(), "")
                helper.analyze.assert_called_once_with(payload)

    def test_review_cli_rejects_duplicate_keys_and_nonfinite_json(self):
        cases = [
            ('{"policy":{"x":1,"x":2},"thresholds":[],"rows":[]}', "duplicate_json_key"),
            ('{"policy":{},"thresholds":[NaN],"rows":[]}', "nonfinite_json_number"),
            ('{"policy":{},"thresholds":[1e999],"rows":[]}', "nonfinite_json_number"),
        ]
        for module in MODULES:
            for input_text, error in cases:
                with self.subTest(module=module.__name__, error=error):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with (
                        contextlib.redirect_stdout(stdout),
                        contextlib.redirect_stderr(stderr),
                        mock.patch.object(sys, "stdin", io.StringIO(input_text)),
                    ):
                        status = module.main(["--review"])
                    self.assertEqual(status, 2)
                    self.assertEqual(stdout.getvalue(), "")
                    self.assertIn(error, stderr.getvalue())

    def test_entrypoint_help_and_malformed_args(self):
        for module in MODULES:
            with self.subTest(module=module.__name__):
                help_out, help_err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(help_out), contextlib.redirect_stderr(help_err):
                    status = module.main(["--help"])
                self.assertEqual(status, 0)
                self.assertEqual(help_err.getvalue(), "")
                for phrase in (
                    "exact-key", "max_uncertainty", "thresholds", "loss", "null", "offline", "--review"
                ):
                    self.assertIn(phrase, help_out.getvalue())

                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    status = module.main(["--review", "--unknown"])
                self.assertEqual(status, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("unrecognized arguments", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
