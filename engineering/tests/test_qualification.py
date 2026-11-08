"""Installed native permissions tested without thread/turn/model requests."""

import hashlib
import json
import platform
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engineering.qualification import qualify_native_profile


class NativeQualificationTests(unittest.TestCase):
    def test_missing_codex_fails_closed_and_persists_safe_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "evidence.json"
            with patch("engineering.qualification.shutil.which", return_value=None):
                report = qualify_native_profile(temporary, evidence_path=receipt)
            self.assertFalse(report["qualified"])
            self.assertTrue(report["no_inference"])
            self.assertEqual(report["error_type"], "RuntimeError")
            self.assertEqual(json.loads(receipt.read_text()), report)
            digest = report.pop("report_sha256")
            self.assertEqual(
                digest,
                hashlib.sha256(
                    json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            )

    @unittest.skipUnless(
        platform.system() == "Darwin" and shutil.which("codex"),
        "requires installed macOS Codex app-server",
    )
    def test_installed_native_readonly_profile(self):
        # Use a real non-temporary parent, since deployment must not rely on a
        # global temporary root being private or treated like task authority.
        base = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="native-test-", dir=base) as temporary:
            receipt = Path(temporary) / "evidence.json"
            report = qualify_native_profile(temporary, evidence_path=receipt)
            self.assertTrue(report["qualified"], report)
            self.assertGreaterEqual(len(report["checks"]), 17)
            self.assertTrue(report["no_inference"])
            self.assertIn("prior_vulnerability", report)
            saved = receipt.read_text()
            self.assertEqual(json.loads(saved), report)
            self.assertNotIn("allowed-disposable-", saved)
            self.assertNotIn("denied-disposable-", saved)
            self.assertNotIn('"stdout":', saved)
            self.assertNotIn('"stderr":', saved)
