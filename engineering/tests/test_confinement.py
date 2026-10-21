"""Real OS boundary tests: only disposable canaries, never protected user data."""

from __future__ import annotations

import hashlib
import json
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
from engineering.verifier_result import validate_verifier_result

PYTHON = Path(
    os.environ.get("BRAIN_SANDBOX_TEST_PYTHON", "/opt/homebrew/opt/python@3.13/bin/python3.13")
)
if not PYTHON.exists():
    PYTHON = Path(sys.executable).resolve()
SQLITE = Path("/opt/homebrew/opt/sqlite")
EXTRA_ROOTS = [SQLITE] if SQLITE.exists() else []


class ProcessIdentityDeadlineTests(unittest.TestCase):
    class FakeProcess:
        pid = 42

        @staticmethod
        def poll():
            return None

    def test_late_matching_initial_identity_is_rejected(self):
        with (
            patch("engineering.confinement.time.monotonic", side_effect=[0.0, 1.0, 3.0]),
            patch(
                "engineering.confinement.running_code_identity",
                return_value=("a" * 40, "/trusted/python"),
            ),
            self.assertRaisesRegex(ConfinementError, "observation exceeded deadline"),
        ):
            SandboxRunner._wait_for_process_code_identity(
                self.FakeProcess(),
                "a" * 40,
                Path("/trusted/python"),
                deadline=2.0,
            )

    def test_late_matching_recheck_is_rejected(self):
        with (
            patch("engineering.confinement.time.monotonic", side_effect=[0.0, 11.0]),
            patch(
                "engineering.confinement.running_code_identity",
                return_value=("a" * 40, "/trusted/python"),
            ),
            self.assertRaisesRegex(ConfinementError, "observation exceeded deadline"),
        ):
            SandboxRunner._check_process_code_identity(
                self.FakeProcess(),
                "a" * 40,
                Path("/trusted/python"),
                deadline=10.0,
            )


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

    def test_pinned_executable_is_rechecked_immediately_before_launch(self):
        pinned_python = PYTHON.resolve(strict=True)
        expected = hashlib.sha256(pinned_python.read_bytes()).hexdigest()
        result = self.runner.run(
            [str(pinned_python), "-I", "-c", "print('pinned')"],
            cwd=self.task,
            expected_executable_sha256=expected,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "pinned")
        self.assertEqual(result.prelaunch_executable_sha256, expected)

        with patch("engineering.confinement.subprocess.Popen") as launch:
            with self.assertRaisesRegex(ConfinementError, "digest changed"):
                self.runner.run(
                    [str(pinned_python), "-I", "-c", "print('must not launch')"],
                    cwd=self.task,
                    expected_executable_sha256="0" * 64,
                )
        launch.assert_not_called()

    def test_isolated_no_site_interpreter_flags_under_disposable_sandbox(self):
        pinned_python = PYTHON.resolve(strict=True)
        expected = hashlib.sha256(pinned_python.read_bytes()).hexdigest()
        source = (
            "import json,sys; "
            "assert sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode; "
            "assert 'site' not in sys.modules; "
            "print(json.dumps({'isolated':sys.flags.isolated,'no_site':sys.flags.no_site,"
            "'dont_write_bytecode':sys.flags.dont_write_bytecode,'safe_path':sys.flags.safe_path}))"
        )
        result = self.runner.run(
            [str(pinned_python), "-I", "-S", "-B", "-c", source],
            cwd=self.task,
            expected_executable_sha256=expected,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"isolated": 1, "no_site": 1, "dont_write_bytecode": 1, "safe_path": True},
        )
        self.assertEqual(result.prelaunch_executable_sha256, expected)

    def test_bound_command_shape_runs_trusted_harness_in_exact_disposable_profile(self):
        pinned_python = PYTHON.resolve(strict=True)
        expected = hashlib.sha256(pinned_python.read_bytes()).hexdigest()
        checkout = self.base / "candidate-checkout"
        trusted = self.base / "trusted"
        scratch = self.base / "verification-scratch"
        for root in (checkout, trusted, scratch):
            root.mkdir()
        harness = trusted / "acceptance.py"
        harness.write_text(
            "import json,sys\n"
            "assert sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode\n"
            "assert 'site' not in sys.modules\n"
            "assert sys.argv[1] == 'synthetic-acceptance'\n"
            "print(json.dumps({'schema':'synthetic-result-v1',"
            "'acceptance_id':'synthetic-acceptance','passed':True}))\n"
        )
        box = SandboxRunner(
            writable_roots=[scratch],
            home=scratch,
            read_roots=[checkout, trusted, *python_runtime_roots(pinned_python)],
            protected_roots=[self.outside],
        )
        result = box.run(
            [
                str(pinned_python),
                "-I",
                "-S",
                "-B",
                str(harness),
                "synthetic-acceptance",
                str(checkout),
            ],
            cwd=checkout,
            env_overrides={"LANG": "C"},
            expected_executable_sha256=expected,
            expected_script_sha256=hashlib.sha256(harness.read_bytes()).hexdigest(),
            expected_script_path=harness.resolve(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        validate_verifier_result(
            result.stdout,
            {
                "acceptance_id": "synthetic-acceptance",
                "result_schema": "synthetic-result-v1",
                "case_field": "acceptance_id",
                "passed_field": "passed",
            },
        )
        self.assertEqual(result.prelaunch_executable_sha256, expected)
        harness_sha256 = hashlib.sha256(harness.read_bytes()).hexdigest()
        self.assertEqual(result.prelaunch_script_sha256, harness_sha256)
        self.assertEqual(len(result.profile_sha256), 64)
        self.assertFalse((checkout / "__pycache__").exists())

        harness.write_text("print('changed after plan resolution')\n")
        with patch("engineering.confinement.subprocess.Popen") as launch:
            with self.assertRaisesRegex(ConfinementError, "script digest changed"):
                box.run(
                    [str(pinned_python), "-I", "-S", "-B", str(harness), "synthetic-acceptance"],
                    cwd=checkout,
                    expected_executable_sha256=expected,
                    expected_script_sha256=harness_sha256,
                    expected_script_path=harness.resolve(),
                )
        launch.assert_not_called()

    def test_expired_prelaunch_script_hash_never_starts_verifier(self):
        import engineering.confinement as confinement

        harness = self.task / "acceptance.py"
        harness.write_text("pass\n")
        with (
            patch.object(confinement, "_prelaunch_script_sha256", return_value="a" * 64),
            patch("engineering.confinement.time.monotonic", side_effect=[0.0, 11.0]),
            patch("engineering.confinement.subprocess.Popen") as launch,
            self.assertRaisesRegex(
                ConfinementError, "script verification exceeded command deadline"
            ),
        ):
            self.runner.run(
                [str(PYTHON.resolve()), "-I", "-S", "-B", str(harness)],
                cwd=self.task,
                timeout=10.0,
                expected_script_sha256="a" * 64,
                expected_script_path=harness.resolve(),
            )
        launch.assert_not_called()

    def test_expired_prelaunch_executable_hash_never_starts_verifier(self):
        import engineering.confinement as confinement

        executable_sha256 = "b" * 64
        with (
            patch.object(
                confinement,
                "_prelaunch_executable_sha256",
                return_value=executable_sha256,
            ),
            patch("engineering.confinement.time.monotonic", side_effect=[0.0, 11.0]),
            patch("engineering.confinement.subprocess.Popen") as launch,
            self.assertRaisesRegex(
                ConfinementError, "executable verification exceeded command deadline"
            ),
        ):
            self.runner.run(
                [str(PYTHON.resolve()), "-c", "pass"],
                cwd=self.task,
                timeout=10.0,
                expected_executable_sha256=executable_sha256,
            )
        launch.assert_not_called()

    def test_pinned_executable_rejects_noncanonical_and_nonexecutable_paths(self):
        from engineering.confinement import _prelaunch_executable_sha256

        pinned_python = PYTHON.resolve(strict=True)
        expected = hashlib.sha256(pinned_python.read_bytes()).hexdigest()
        alias = self.task / "python-alias"
        alias.symlink_to(PYTHON)
        with self.assertRaisesRegex(ConfinementError, "canonical"):
            _prelaunch_executable_sha256(alias, expected)
        nonexecutable = self.task / "not-executable"
        nonexecutable.write_text("content")
        with self.assertRaisesRegex(ConfinementError, "regular executable"):
            _prelaunch_executable_sha256(nonexecutable, hashlib.sha256(b"content").hexdigest())

    def test_pinned_executable_hashing_has_a_cumulative_size_limit(self):
        import engineering.confinement as confinement

        executable = self.task / "growing-executable"
        executable.write_bytes(b"x")
        executable.chmod(0o700)
        with (
            patch.object(confinement, "MAX_PINNED_EXECUTABLE_BYTES", 3),
            patch.object(confinement.os, "read", side_effect=[b"ab", b"cd"]),
            self.assertRaisesRegex(ConfinementError, "size limit"),
        ):
            confinement._prelaunch_executable_sha256(executable, "0" * 64)

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

    def test_completion_after_deadline_is_classified_as_timeout(self):
        class FakeStream:
            @staticmethod
            def fileno():
                return 10

        class FakeSelector:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def register(*_args):
                pass

            @staticmethod
            def get_map():
                return {}

        class FakeProcess:
            pid = 42
            stdout = FakeStream()
            stderr = FakeStream()
            returncode = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def wait(timeout):
                return 0

        with (
            patch("engineering.confinement.time.monotonic", side_effect=[0.0, 0.0, 1.0, 6.0, 6.0]),
            patch("engineering.confinement.selectors.DefaultSelector", return_value=FakeSelector()),
            patch("engineering.confinement.os.set_blocking"),
            patch("engineering.confinement.subprocess.Popen", return_value=FakeProcess()),
            patch.object(SandboxRunner, "_kill_process_group"),
        ):
            result = self.runner.run([str(PYTHON), "-I", "-c", "pass"], cwd=self.task, timeout=5.0)

        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.timed_out)

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
