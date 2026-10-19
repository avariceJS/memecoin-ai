"""Kernel birth qualification; signals target only disposable child processes."""

import ctypes
import os
import platform
import subprocess
import unittest
from unittest.mock import patch

from engineering.processes import _ProcBsdInfo, process_identity, terminate_owned


@unittest.skipUnless(platform.system() == "Darwin", "requires macOS libproc")
class ProcessTests(unittest.TestCase):
    def test_current_kernel_identity_is_stable_and_contains_microseconds(self):
        self.assertEqual(ctypes.sizeof(_ProcBsdInfo), 136)
        identity = process_identity(os.getpid())
        self.assertIsNotNone(identity)
        self.assertEqual(identity, process_identity(os.getpid()))
        prefix, pid, seconds, microseconds, uid = identity.split(":")
        self.assertEqual(prefix, "darwin")
        self.assertEqual(int(pid), os.getpid())
        self.assertGreater(int(seconds), 0)
        self.assertEqual(len(microseconds), 6)
        self.assertEqual(int(uid), os.getuid())

    def test_mismatched_birth_never_signals_live_child(self):
        with subprocess.Popen(["/bin/sleep", "30"], start_new_session=True) as child:
            try:
                self.assertFalse(terminate_owned(child.pid, "wrong-birth"))
                self.assertIsNone(child.poll())
                witness = process_identity(child.pid)
                self.assertTrue(terminate_owned(child.pid, witness))
                child.wait(timeout=3)
                self.assertLess(child.returncode, 0)
                self.assertFalse(terminate_owned(child.pid, witness))
            finally:
                if child.poll() is None:
                    child.kill()

    def test_nonleader_and_self_are_never_signalled(self):
        self.assertFalse(terminate_owned(os.getpid(), process_identity(os.getpid())))
        with subprocess.Popen(["/bin/sleep", "30"]) as child:
            try:
                self.assertFalse(terminate_owned(child.pid, process_identity(child.pid)))
                self.assertIsNone(child.poll())
            finally:
                child.kill()

    def test_unavailable_kernel_witness_fails_closed(self):
        with patch("engineering.processes.platform.system", return_value="Linux"):
            self.assertIsNone(process_identity(os.getpid()))
            self.assertFalse(terminate_owned(1234, "unknown"))
        for value in (True, None, "123", -1, 0, 1):
            self.assertIsNone(process_identity(value))
            self.assertFalse(terminate_owned(value, "unknown"))
