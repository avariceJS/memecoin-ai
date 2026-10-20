"""Synthetic create-only bootstrap wiring checks; no owner runtime is touched."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engineering import bootstrap as bootstrap_module
from engineering.service import validate_deployment
from engineering.usage_ledger import UsageLedger


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.git("init", "-b", "main", str(self.source))
        self.git("-C", str(self.source), "config", "user.name", "Synthetic")
        self.git("-C", str(self.source), "config", "user.email", "synthetic@example.invalid")
        (self.source / "README.md").write_text("synthetic fixture\n")
        self.git("-C", str(self.source), "add", "README.md")
        self.git("-C", str(self.source), "commit", "-m", "synthetic source")
        head = self.git("-C", str(self.source), "rev-parse", "HEAD")
        self.git("-C", str(self.source), "update-ref", "refs/remotes/origin/main", head)
        self.git(
            "-C", str(self.source), "update-ref", "refs/remotes/origin/research/active", head
        )
        self.executable = self.root / "codex"
        self.executable.write_text("#!/bin/sh\nexit 0\n")
        self.executable.chmod(0o700)

    @staticmethod
    def git(*args):
        return subprocess.run(
            ["git", *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def qualify_native(self, _state, *, evidence_path):
        report = {
            "qualified": True,
            "executable_sha256": hashlib.sha256(self.executable.read_bytes()).hexdigest(),
            "adapter_sha256": hashlib.sha256(
                (Path(bootstrap_module.__file__).parent / "codex_rpc.py").read_bytes()
            ).hexdigest(),
        }
        evidence_path.write_text(json.dumps(report))
        return report

    def test_bootstrap_creates_valid_hash_binding_and_versioned_ledger(self):
        state = self.root / "state"
        with (
            patch.object(bootstrap_module, "qualify_sandbox", return_value={"qualified": True}),
            patch.object(
                bootstrap_module, "qualify_native_profile", side_effect=self.qualify_native
            ),
            patch.object(bootstrap_module.shutil, "which", return_value=str(self.executable)),
        ):
            cfg = bootstrap_module.bootstrap(state, self.source, sys.executable)

        self.assertEqual(
            cfg["codex_executable_sha256"], hashlib.sha256(self.executable.read_bytes()).hexdigest()
        )
        self.assertNotIn("codex_sha256", cfg)
        self.assertEqual(
            validate_deployment(state)["codex_executable"], self.executable.resolve()
        )
        ledger = UsageLedger(state / "usage-ledger.sqlite")
        try:
            self.assertEqual(ledger.db.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            ledger.close()

    def test_failed_confinement_qualification_leaves_no_usage_ledger(self):
        state = self.root / "state"
        with (
            patch.object(bootstrap_module, "qualify_sandbox", return_value={"qualified": False}),
            patch.object(
                bootstrap_module, "qualify_native_profile", side_effect=self.qualify_native
            ),
            patch.object(bootstrap_module.shutil, "which", return_value=str(self.executable)),
            self.assertRaisesRegex(RuntimeError, "OS_confinement_not_qualified"),
        ):
            bootstrap_module.bootstrap(state, self.source, sys.executable)
        self.assertFalse((state / "usage-ledger.sqlite").exists())


if __name__ == "__main__":
    unittest.main()
