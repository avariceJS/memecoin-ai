"""Validation and owner-projection tests for bounded Paperclip source delivery."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from engineering.git_gate import GitGate, VerificationPolicy
from engineering.paperclip_source_delivery import (
    SOURCE_PATHS,
    SourceDeliveryError,
    discard_projection,
    materialize_owner_projection,
    refresh_owner_workspace,
    snapshot_records,
)


def git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def commit(path: Path, message: str) -> str:
    git(path, "add", "-A")
    git(
        path,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        message,
    )
    return git(path, "rev-parse", "HEAD")


def records(contents: tuple[str, str]) -> list[dict]:
    return [
        {
            "path_id": path,
            "content": content,
            "size": len(content.encode()),
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
        for path, content in zip(SOURCE_PATHS, contents, strict=True)
    ]


def manifest_records(paths: tuple[str, ...], contents: tuple[str, ...]) -> list[dict]:
    return [
        {
            "path_id": path,
            "content": content,
            "size": len(content.encode()),
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
        for path, content in zip(paths, contents, strict=True)
    ]


class PaperclipSourceDeliveryTests(unittest.TestCase):
    def test_valid_records_make_stable_immutable_snapshot(self):
        supplied = records(("events = []\n", "def test_raw_sources(): pass\n"))
        snapshot = snapshot_records(supplied)
        before = snapshot.digest
        supplied[0]["content"] = "mutated"
        supplied.reverse()
        self.assertEqual(snapshot.digest, before)
        self.assertEqual(snapshot.files[0].data, b"events = []\n")
        self.assertEqual(snapshot.files[0].path, SOURCE_PATHS[0])

    def test_wrong_size_and_hash_are_rejected(self):
        supplied = records(("a", "b"))
        supplied[0]["size"] += 1
        with self.assertRaisesRegex(SourceDeliveryError, "size_mismatch"):
            snapshot_records(supplied)
        supplied = records(("a", "b"))
        supplied[0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(SourceDeliveryError, "sha256_mismatch"):
            snapshot_records(supplied)

    def test_file_and_total_byte_limits_are_enforced(self):
        supplied = records(("x" * 1_000_001, ""))
        with self.assertRaisesRegex(SourceDeliveryError, "file_exceeds_limit"):
            snapshot_records(supplied)
        supplied = records(("x" * 900_000, "y" * 700_000))
        with self.assertRaisesRegex(SourceDeliveryError, "delivery_exceeds_limit"):
            snapshot_records(supplied)

    def test_wrong_file_sets_and_path_ids_are_rejected(self):
        supplied = records(("a", "b"))
        for bad in (supplied[:1], supplied + [supplied[0]], [supplied[0], supplied[0]]):
            with self.subTest(count=len(bad)), self.assertRaises(SourceDeliveryError):
                snapshot_records(bad)
        supplied = records(("a", "b"))
        supplied[0]["path_id"] = "src/attacker.py"
        with self.assertRaisesRegex(SourceDeliveryError, "not_allowed"):
            snapshot_records(supplied)

    def test_owner_manifest_accepts_task_specific_exact_source_test_pair(self):
        paths = (
            "src/memecoin_experience/records.py",
            "tests/test_experience.py",
            "tests/test_experience_schema.py",
        )
        snapshot = snapshot_records(
            manifest_records(paths, ("source\n", "test-a\n", "test-b\n")), paths
        )
        self.assertEqual(tuple(item.path_id for item in snapshot.files), paths)
        for invalid in (
            ("src/module.py", "tests/test.py", "tests/../escape.py"),
            ("src/module.py", "tests/test.py", "tests/test.py"),
            ("src/module.py", "tests/test.py", "/tmp/outside.py"),
            ("src/module.py", "docs/readme.md"),
        ):
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(SourceDeliveryError, "manifest_invalid"),
            ):
                snapshot_records(manifest_records(invalid, tuple("x" for _ in invalid)), invalid)

    def test_non_senses_manifest_publishes_only_changed_subset(self):
        paths = (
            "src/memecoin_experience/records.py",
            "tests/test_experience.py",
            "tests/test_experience_schema.py",
        )
        with tempfile.TemporaryDirectory(prefix="department-source-delivery-") as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            git(source, "init", "-b", "main")
            old_contents = ("source base\n", "first test base\n", "second test base\n")
            for path, content in zip(paths, old_contents, strict=True):
                target = source / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            base = commit(source, "base")
            publisher = root / "publisher"
            subprocess.run(
                ["git", "clone", str(source), str(publisher)], check=True, capture_output=True
            )
            gate = GitGate(
                source, publisher, VerificationPolicy((("/usr/bin/true",),)), root / "receipts"
            )
            snapshot = snapshot_records(
                manifest_records(paths, (old_contents[0], "first test changed\n", old_contents[2])),
                paths,
            )
            projection = materialize_owner_projection(gate, base, snapshot, allowed_paths=paths)
            try:
                changed = git(
                    projection.checkout, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"
                ).splitlines()
                self.assertEqual(changed, [paths[1]])
                self.assertEqual(
                    (projection.checkout / paths[1]).read_text(), "first test changed\n"
                )
            finally:
                discard_projection(gate, projection)

    def test_owner_projection_checks_base_and_commits_only_owner_defined_paths(self):
        with tempfile.TemporaryDirectory(prefix="source-delivery-") as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            git(source, "init", "-b", "main")
            for path, content in zip(SOURCE_PATHS, ("old events\n", "old tests\n"), strict=True):
                target = source / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            base = commit(source, "base")
            publisher = root / "publisher"
            subprocess.run(
                ["git", "clone", str(source), str(publisher)],
                check=True,
                capture_output=True,
            )
            gate = GitGate(
                source,
                publisher,
                VerificationPolicy((("/usr/bin/true",),)),
                root / "receipts",
            )
            snapshot = snapshot_records(records(("new events\n", "new tests\n")))
            projection = materialize_owner_projection(gate, base, snapshot)
            try:
                self.assertEqual(
                    git(projection.checkout, "rev-parse", "HEAD"), projection.submitted_sha
                )
                changed = git(
                    projection.checkout,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "HEAD",
                ).splitlines()
                self.assertEqual(tuple(changed), SOURCE_PATHS)
                self.assertEqual(
                    (projection.checkout / SOURCE_PATHS[0]).read_text(), "new events\n"
                )
            finally:
                discard_projection(gate, projection)
            replay = materialize_owner_projection(gate, base, snapshot)
            try:
                self.assertEqual(replay.submitted_sha, projection.submitted_sha)
            finally:
                discard_projection(gate, replay)
            with self.assertRaisesRegex(SourceDeliveryError, "base_sha_invalid"):
                materialize_owner_projection(gate, "not-a-sha", snapshot)

    def test_workspace_refresh_changes_only_fixed_source_files_and_binds_content_hashes(self):
        with tempfile.TemporaryDirectory(prefix="source-workspace-refresh-") as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            git(source, "init", "-b", "main")
            for path, content in zip(
                SOURCE_PATHS, ("owner events\n", "owner tests\n"), strict=True
            ):
                target = source / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            base = commit(source, "trusted base")
            publisher = root / "publisher"
            subprocess.run(
                ["git", "clone", str(source), str(publisher)],
                check=True,
                capture_output=True,
            )
            gate = GitGate(
                source,
                publisher,
                VerificationPolicy((("/usr/bin/true",),)),
                root / "receipts",
            )
            workspace = root / "no-git-workspace"
            workspace.mkdir()
            (workspace / "keep.txt").write_text("untouched\n")
            digest = refresh_owner_workspace(gate, source, base, workspace)
            self.assertEqual(digest, refresh_owner_workspace(gate, source, base, workspace))
            self.assertEqual((workspace / SOURCE_PATHS[0]).read_text(), "owner events\n")
            self.assertEqual((workspace / SOURCE_PATHS[1]).read_text(), "owner tests\n")
            self.assertEqual((workspace / "keep.txt").read_text(), "untouched\n")


if __name__ == "__main__":
    unittest.main()
