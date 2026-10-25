"""Synthetic contract tests for the immutable accepted-source reader."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from engineering.paperclip_accepted_source_reader import (
    AcceptedSourceReadError,
    build_snapshot,
    list_files,
    read_lines,
    search_text,
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(
        repo,
        "-c",
        "user.name=synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        message,
    )
    return git(repo, "rev-parse", "HEAD")


class AcceptedSourceReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="accepted-source-reader-")
        self.root = Path(self.temp.name)
        self.repo = self.root / "projection"
        self.repo.mkdir()
        subprocess.run(["/usr/bin/git", "init", "-q", "-b", "main", str(self.repo)], check=True)
        self._write("src/pkg/module.py", "needle first\nsecond needle\n")
        self._write("tests/test_module.py", "from pkg import module\n")
        self._write("pyproject.toml", "[project]\nname='synthetic'\n")
        self.pin = commit(self.repo, "sanitized projection v1")
        self.canonical = "a" * 40
        self.receipt = "b" * 64
        self.owner_digest = "c" * 64

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, relative: str, content: str):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def snapshot(self):
        return build_snapshot(
            str(self.repo),
            self.pin,
            self.canonical,
            self.receipt,
            ("src/", "tests/", "pyproject.toml"),
        )

    def test_list_pages_cover_complete_manifest_and_bind_cursor_to_pin(self):
        snapshot = self.snapshot()
        first = list_files(str(self.repo), snapshot, self.owner_digest, limit=2)
        self.assertTrue(first["truncated"])
        self.assertEqual(len(first["files"]), 2)
        second = list_files(
            str(self.repo), snapshot, self.owner_digest, limit=2, cursor=first["next_cursor"]
        )
        self.assertFalse(second["truncated"])
        self.assertEqual(
            [row["path"] for row in first["files"] + second["files"]],
            ["pyproject.toml", "src/pkg/module.py", "tests/test_module.py"],
        )

    def test_search_pages_do_not_repeat_hits_and_read_returns_bounded_lines(self):
        snapshot = self.snapshot()
        first = search_text(
            str(self.repo), snapshot, self.owner_digest, "needle", prefix="src/", limit=1
        )
        self.assertTrue(first["truncated"])
        second = search_text(
            str(self.repo), snapshot, self.owner_digest, "needle", prefix="src/", limit=1,
            cursor=first["next_cursor"],
        )
        self.assertFalse(second["truncated"])
        self.assertEqual([row["line"] for row in first["matches"] + second["matches"]], [1, 2])
        content = read_lines(str(self.repo), snapshot, "src/pkg/module.py", start_line=2, line_count=1)
        self.assertEqual(content["lines"], [{"line": 2, "text": "second needle", "line_truncated": False}])

    def test_search_covers_multiple_files_once_with_and_without_paging(self):
        for name in ("a.py", "b.py", "c.py"):
            self._write(f"src/multi/{name}", f"needle in {name}\n")
        self.pin = commit(self.repo, "add three-file search fixture")
        snapshot = self.snapshot()
        expected = [f"src/multi/{name}" for name in ("a.py", "b.py", "c.py")]

        single_page = search_text(
            str(self.repo), snapshot, self.owner_digest, "needle", prefix="src/multi/", limit=50
        )
        self.assertFalse(single_page["truncated"])
        self.assertEqual([row["path"] for row in single_page["matches"]], expected)

        pages = []
        cursor = None
        while True:
            page = search_text(
                str(self.repo), snapshot, self.owner_digest, "needle", prefix="src/multi/",
                limit=1, cursor=cursor,
            )
            pages.extend(row["path"] for row in page["matches"])
            if not page["truncated"]:
                break
            cursor = page["next_cursor"]
        self.assertEqual(pages, expected)
        self.assertEqual(len(pages), len(set(pages)))

    def test_cursor_rejects_projection_drift_and_manifest_rejects_unapproved_root(self):
        snapshot = self.snapshot()
        first = list_files(str(self.repo), snapshot, self.owner_digest, limit=1)
        self._write("scripts/private.py", "not admitted by the projection envelope\n")
        advanced = commit(self.repo, "unsafe projection change")
        self.pin = advanced
        with self.assertRaisesRegex(AcceptedSourceReadError, "outside_manifest"):
            self.snapshot()
        self._write("scripts/private.py", "")
        git(self.repo, "rm", "-f", "scripts/private.py")
        self.pin = commit(self.repo, "new accepted projection")
        current = self.snapshot()
        with self.assertRaisesRegex(AcceptedSourceReadError, "cursor_stale_restart_required"):
            list_files(str(self.repo), current, self.owner_digest, limit=1, cursor=first["next_cursor"])

    def test_reader_refuses_paths_outside_manifest(self):
        with self.assertRaisesRegex(AcceptedSourceReadError, "manifest_invalid"):
            read_lines(str(self.repo), self.snapshot(), "../secret.txt")


if __name__ == "__main__":
    unittest.main()
