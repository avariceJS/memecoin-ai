"""Integrity and refusal checks for the reproducible Paperclip source package."""

from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from engineering.paperclip.install_source import install_source
from engineering.paperclip_lab_server import (
    PAPERCLIP_PIN,
    PAPERCLIP_SOURCE_CHAIN,
    PAPERCLIP_UPSTREAM_PIN,
)


class PaperclipSourceInstallTests(unittest.TestCase):
    def test_package_contains_every_admitted_custom_commit_without_upstream_copy(self):
        package = Path(__file__).resolve().parents[1] / "paperclip"
        pin = json.loads((package / "source-pin.json").read_text())
        source = pin["reproducible_source"]
        delta = (package / source["file"]).read_bytes()
        self.assertEqual(hashlib.sha256(delta).hexdigest(), source["sha256"])
        self.assertEqual(len(delta), source["bytes"])
        self.assertEqual(source["commit"], PAPERCLIP_PIN)
        self.assertEqual(source["upstream_commit"], PAPERCLIP_UPSTREAM_PIN)
        self.assertEqual(source["custom_commit_count"], len(PAPERCLIP_SOURCE_CHAIN) - 1)
        payload = gzip.decompress(delta)
        for commit in PAPERCLIP_SOURCE_CHAIN[:-1]:
            self.assertIn(f"original-oid {commit}\n".encode(), payload)
        self.assertNotIn(f"original-oid {PAPERCLIP_UPSTREAM_PIN}\n".encode(), payload)

    def test_existing_destination_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "paperclip"
            destination.mkdir()
            marker = destination / "owner-state"
            marker.write_text("preserve")
            with self.assertRaises(FileExistsError):
                install_source(destination)
            self.assertEqual(marker.read_text(), "preserve")


if __name__ == "__main__":
    unittest.main()
