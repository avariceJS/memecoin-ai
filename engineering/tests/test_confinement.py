"""Real OS boundary tests: only disposable canaries, never protected user data."""

from __future__ import annotations

import os
import platform
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engineering.confinement import (
    ConfinementError,
    SandboxRunner,
    python_runtime_roots,
    qualify_sandbox,
)

PYTHON = Path(
    os.environ.get("BRAIN_SANDBOX_TEST_PYTHON", "/opt/homebrew/opt/python@3.13/bin/python3.13")
)
if not PYTHON.exists():
    PYTHON = Path(sys.executable).resolve()
SQLITE = Path("/opt/homebrew/opt/sqlite")
EXTRA_ROOTS = [SQLITE] if SQLITE.exists() else []


@unittest.skipUnless(platform.system() == "Darwin", "requires real macOS Seatbelt")
class ConfinementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sandbox-unit-")
        self.base = Path(self.temporary.name).resolve()
        self.task = self.base / "task"
        self.scratch = self.base / "scratch"
        self.outside = self.base / "outside"
        for path in (self.task, self.scratch, self.outside):
            path.mkdir()
        self.runner = SandboxRunner(
            writable_roots=[self.task, self.scratch],
            home=self.scratch,
            read_roots=[*python_runtime_roots(PYTHON), *EXTRA_ROOTS],
            protected_roots=[self.outside],
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_empirical_qualification(self):
        with patch.dict(
            os.environ,
            {
                "BRAIN_QUALIFICATION_SECRET": "disposable-secret",
                "OPENAI_API_KEY": "disposable-not-a-key",
                "SSH_AUTH_SOCK": str(self.outside / "fake.sock"),
            },
        ):
            report = qualify_sandbox(self.base, python_executable=PYTHON, read_roots=EXTRA_ROOTS)
        failures = [check for check in report["checks"] if not check["passed"]]
        self.assertTrue(report["qualified"], failures)
        self.assertGreaterEqual(len(report["checks"]), 25)
        self.assertEqual(len(report["profile_sha256"]), 64)
        self.assertEqual(len(report["dyld_support_sha256"]), 64)

    def test_unavailable_sandbox_fails_closed(self):
        with patch("engineering.confinement.platform.system", return_value="Linux"):
            with self.assertRaises(ConfinementError):
                SandboxRunner(writable_roots=[self.task], home=self.task)
        with patch("engineering.confinement.SANDBOX_EXEC", self.outside / "missing"):
            with self.assertRaises(ConfinementError):
                SandboxRunner(writable_roots=[self.task], home=self.task)

    def test_rejects_broad_roots_and_overlapping_protection(self):
        for root in ("/", str(Path.home()), "/Users", "/opt/homebrew"):
            with self.subTest(root=root), self.assertRaises(ConfinementError):
                SandboxRunner(writable_roots=[root], home=self.task)
        with self.assertRaises(ConfinementError):
            SandboxRunner(writable_roots=[self.task], home=self.task, protected_roots=[self.task])
        with self.assertRaises(ConfinementError):
            SandboxRunner(writable_roots=[self.task], home=self.task, protected_roots=[self.base])
        with self.assertRaises(ConfinementError):
            SandboxRunner(writable_roots=[self.task], home=self.outside)

    def test_rejects_unconfined_working_directory_or_implicit_executable(self):
        for executable, cwd in (("python3", self.task), (str(PYTHON), self.outside)):
            with self.subTest(executable=executable, cwd=cwd), self.assertRaises(ConfinementError):
                self.runner.run([executable, "-c", "print('bad')"], cwd=cwd)

    def test_verifier_environment_overrides_are_allowlisted(self):
        environment = self.runner.environment({"LANG": "C", "PYTHONHASHSEED": "0"})
        self.assertEqual(environment["LANG"], "C")
        self.assertEqual(environment["PYTHONHASHSEED"], "0")
        with self.assertRaisesRegex(ConfinementError, "verifier_environment_not_allowed"):
            self.runner.environment({"OPENAI_API_KEY": "never-inherit"})
        with self.assertRaisesRegex(ConfinementError, "verifier_environment_invalid"):
            self.runner.environment({"LANG": "C\x00bad"})
        (self.task / "outside-link").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(ConfinementError):
            self.runner.run([str(PYTHON), "-c", "print('bad')"], cwd=self.task / "outside-link")

    def test_readonly_checkout_cwd_does_not_grant_writes(self):
        readonly = self.base / "readonly-checkout"
        readonly.mkdir()
        box = SandboxRunner(
            writable_roots=[self.scratch],
            home=self.scratch,
            read_roots=[readonly, *python_runtime_roots(PYTHON)],
        )
        source = (
            "import errno, pathlib; print(pathlib.Path.cwd()); "
            "\ntry: pathlib.Path('forbidden').write_text('bad')"
            "\nexcept OSError as e: assert e.errno in (errno.EPERM, errno.EACCES)"
            "\nelse: raise AssertionError('readonly cwd became writable')"
        )
        result = box.run([str(PYTHON), "-I", "-c", source], cwd=readonly)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((readonly / "forbidden").exists())

    def test_timeout_is_explicit_failure(self):
        result = self.runner.run(
            [str(PYTHON), "-I", "-c", "import time; time.sleep(30)"], cwd=self.task, timeout=0.1
        )
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)

    def test_output_limit_terminates_infinite_output(self):
        result = self.runner.run(
            [str(PYTHON), "-I", "-c", "import os;\nwhile True: os.write(1,b'x'*65536)"],
            cwd=self.task,
            timeout=3,
            max_output_bytes=4096,
        )
        self.assertTrue(result.output_truncated)
        self.assertLessEqual(len(result.stdout.encode()) + len(result.stderr.encode()), 4096)
        self.assertLess(result.elapsed_seconds, 3)

    def test_timeout_does_not_wait_for_descendant_pipe_eof(self):
        result = self.runner.run(
            [
                str(PYTHON),
                "-I",
                "-c",
                "import os,time; p=os.fork(); time.sleep(30) if p==0 else None",
            ],
            cwd=self.task,
            timeout=0.2,
        )
        self.assertTrue(result.timed_out)
        self.assertLess(result.elapsed_seconds, 3)

    def test_nested_sandbox_cannot_remove_inherited_restrictions(self):
        canary = self.outside / "canary"
        canary.write_text("disposable-canary")
        source = (
            "import subprocess; r=subprocess.run(['/usr/bin/sandbox-exec','-p',"
            "'(version 1)(allow default)','/bin/cat',"
            + repr(str(canary))
            + "],capture_output=True); assert r.returncode!=0; "
            "assert b'disposable-canary' not in r.stdout"
        )
        result = self.runner.run([str(PYTHON), "-I", "-c", source], cwd=self.task)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_environment_is_allowlist(self):
        with patch.dict(os.environ, {"SECRET_UNUSED_CANARY": "disposable"}):
            self.assertNotIn("SECRET_UNUSED_CANARY", self.runner.environment())
        self.assertEqual(self.runner.environment()["HOME"], str(self.scratch))
        self.assertNotIn("DYLD_INSERT_LIBRARIES", self.runner.environment())
        self.assertNotIn("PYTHONPATH", self.runner.environment())


if __name__ == "__main__":
    unittest.main()
