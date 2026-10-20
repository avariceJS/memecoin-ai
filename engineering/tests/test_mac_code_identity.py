"""Exercise running-code identity only with disposable, ad-hoc-signed test binaries."""

from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from engineering import confinement
from engineering.confinement import ConfinementError, SandboxRunner
from engineering.mac_code_identity import (
    MacCodeIdentityError,
    running_code_directory_hash,
    running_code_identity,
    static_code_directory_hash,
)


@unittest.skipUnless(
    platform.system() == "Darwin" and shutil.which("clang") and shutil.which("codesign"),
    "requires macOS Security.framework and local compiler/code-signing tools",
)
class MacCodeIdentityTests(unittest.TestCase):
    def _signed_binary(self, root: Path, name: str, marker: str) -> Path:
        source = root / f"{name}.c"
        binary = root / name
        source.write_text(
            "#include <unistd.h>\n"
            f"int main(void) {{ const char c = '{marker}'; "
            "write(1, &c, 1); sleep(2); return 0; }\n"
        )
        subprocess.run([shutil.which("clang"), str(source), "-o", str(binary)], check=True)
        subprocess.run(
            [shutil.which("codesign"), "--force", "--sign", "-", str(binary)],
            check=True,
            capture_output=True,
            text=True,
        )
        return binary.resolve(strict=True)

    def _execing_signed_binary(
        self, root: Path, name: str, target: Path, release_marker: Path
    ) -> Path:
        source = root / f"{name}.c"
        binary = root / name
        source.write_text(
            "#include <unistd.h>\n"
            "int main(int argc, char **argv) { "
            "while (access(argv[2], F_OK) != 0) usleep(1000); "
            "execl(argv[1], argv[1], (char *)0); return 127; }\n"
        )
        subprocess.run([shutil.which("clang"), str(source), "-o", str(binary)], check=True)
        subprocess.run(
            [shutil.which("codesign"), "--force", "--sign", "-", str(binary)],
            check=True,
            capture_output=True,
            text=True,
        )
        return binary.resolve(strict=True)

    def test_running_identity_rejects_pid_outside_cfnumber_sint32(self):
        for pid in (0, -1, 0x80000000):
            with (
                self.subTest(pid=pid),
                self.assertRaisesRegex(MacCodeIdentityError, "mac_code_identity_pid_invalid"),
            ):
                running_code_identity(pid)

    def test_running_identity_survives_path_replacement(self):
        with tempfile.TemporaryDirectory(prefix="mac-code-identity-") as directory:
            root = Path(directory).resolve()
            first = self._signed_binary(root, "first", "A")
            second = self._signed_binary(root, "second", "B")
            expected = static_code_directory_hash(first)
            other = static_code_directory_hash(second)
            self.assertNotEqual(expected, other)

            active_path = root / "active"
            first.rename(active_path)
            process = subprocess.Popen(
                [str(active_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 3
                observed = None
                while time.monotonic() < deadline:
                    try:
                        observed = running_code_directory_hash(process.pid)
                        break
                    except MacCodeIdentityError:
                        if process.poll() is not None:
                            self.fail("signed test process exited before identity observation")
                        time.sleep(0.01)
                self.assertEqual(observed, expected)
                self.assertEqual(running_code_identity(process.pid)[1], str(active_path))

                displaced = root / "displaced"
                active_path.rename(displaced)
                second.rename(active_path)
                self.assertEqual(static_code_directory_hash(active_path), other)
                self.assertEqual(static_code_directory_hash(displaced), expected)
                self.assertEqual(running_code_directory_hash(process.pid), expected)
                self.assertEqual(running_code_identity(process.pid)[1], str(displaced))
                self.assertNotEqual(
                    hashlib.sha256(active_path.read_bytes()).hexdigest(),
                    hashlib.sha256(displaced.read_bytes()).hexdigest(),
                )

                scratch = root / "scratch"
                scratch.mkdir()
                runner = SandboxRunner(
                    writable_roots=[scratch],
                    home=scratch,
                    read_roots=[root],
                )
                result = runner.run(
                    [str(active_path)],
                    cwd=scratch,
                    timeout=5,
                    expected_executable_sha256=hashlib.sha256(active_path.read_bytes()).hexdigest(),
                    expected_executable_cdhash=other,
                    expected_executable_main_path=active_path,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "B")
                self.assertEqual(result.process_code_directory_hash, other)
                self.assertGreater(result.process_identity_observations, 1)
                with self.assertRaisesRegex(ConfinementError, "code identity does not match"):
                    runner.run(
                        [str(active_path)],
                        cwd=scratch,
                        timeout=5,
                        expected_executable_sha256=hashlib.sha256(
                            active_path.read_bytes()
                        ).hexdigest(),
                        expected_executable_cdhash=other,
                        expected_executable_main_path=displaced,
                    )
                with self.assertRaisesRegex(ConfinementError, "code identity does not match"):
                    runner.run(
                        [str(active_path)],
                        cwd=scratch,
                        timeout=5,
                        expected_executable_sha256=hashlib.sha256(
                            active_path.read_bytes()
                        ).hexdigest(),
                        expected_executable_cdhash=expected,
                        expected_executable_main_path=active_path,
                    )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)

    def test_sandbox_runner_rejects_process_image_change_during_output_collection(self):
        with tempfile.TemporaryDirectory(prefix="mac-code-monitor-") as directory:
            root = Path(directory).resolve()
            scratch = root / "scratch"
            scratch.mkdir()
            target = self._signed_binary(root, "target", "T")
            marker = scratch / "release-launcher"
            launcher = self._execing_signed_binary(root, "launcher", target, marker)
            runner = SandboxRunner(
                writable_roots=[scratch],
                home=scratch,
                read_roots=[root],
            )
            expected = static_code_directory_hash(launcher)
            observed = []
            actual_identity = confinement.running_code_identity

            def observe_then_release(pid):
                identity = actual_identity(pid)
                observed.append(identity)
                if identity == (expected, str(launcher)):
                    marker.write_text("go")
                return identity

            with (
                patch(
                    "engineering.confinement.running_code_identity",
                    side_effect=observe_then_release,
                ),
                self.assertRaisesRegex(ConfinementError, "code identity does not match"),
            ):
                runner.run(
                    [str(launcher), str(target), str(marker)],
                    cwd=scratch,
                    timeout=5,
                    expected_executable_sha256=hashlib.sha256(launcher.read_bytes()).hexdigest(),
                    expected_executable_cdhash=expected,
                    expected_executable_main_path=launcher,
                )
            self.assertIn((expected, str(launcher)), observed)
            self.assertTrue(any(identity != (expected, str(launcher)) for identity in observed))


if __name__ == "__main__":
    unittest.main()
