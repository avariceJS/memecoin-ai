"""Synthetic repositories for the bounded canonical content publisher."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engineering import paperclip_canonical_publish as publisher


def git(repo: Path, *args: str, data: bytes | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=data,
        capture_output=True,
        text=False,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr.decode(errors="replace"))
    return result.stdout.decode().strip()


class CanonicalPublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="canonical-publisher-")
        self.repo = Path(self.temp.name) / "canonical"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "brain/development")
        git(self.repo, "config", "user.name", "synthetic")
        git(self.repo, "config", "user.email", "synthetic@example.invalid")
        for path in publisher.ALLOWED_PATHS:
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"base:{path}\n".encode())
        (self.repo / "unrelated.txt").write_text("canonical unrelated base\n")
        self.base = self._commit("base")
        self.old = {path: self._blob_at(self.base, path) for path in publisher.ALLOWED_PATHS}
        self.contents = {path: f"updated:{path}\n".encode() for path in publisher.ALLOWED_PATHS}
        self.new = {path: self._blob(self.contents[path]) for path in publisher.ALLOWED_PATHS}
        self.receipt = self._prepare()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _commit(self, message: str) -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def _blob(self, data: bytes) -> str:
        return git(self.repo, "hash-object", "-w", "--stdin", data=data)

    def _blob_at(self, commit: str, path: str) -> str:
        return git(self.repo, "rev-parse", f"{commit}:{path}")

    def _prepare(self) -> dict:
        return publisher.prepare(
            self.repo,
            "refs/heads/brain/development",
            self.base,
            self.old,
            self.new,
            self.contents,
        )

    def _approve(self) -> dict:
        return publisher.publish(
            self.repo,
            "refs/heads/brain/development",
            self.receipt,
            self.receipt["candidate"],
            self.receipt["receipt_digest"],
        )

    def test_prepare_is_deterministic_scoped_and_leaves_checkout_untouched(self) -> None:
        before = {path: (self.repo / path).read_bytes() for path in publisher.ALLOWED_PATHS}
        again = self._prepare()
        self.assertEqual(self.receipt, again)
        self.assertEqual(
            before, {path: (self.repo / path).read_bytes() for path in publisher.ALLOWED_PATHS}
        )
        self.assertEqual(
            git(
                self.repo,
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                self.base,
                self.receipt["candidate"],
            ).splitlines(),
            list(publisher.ALLOWED_PATHS),
        )
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.base)
        self.assertEqual(
            publisher.inspect(self.repo, "refs/heads/brain/development", self.receipt)["ref_state"],
            "prepared",
        )

    def test_exact_path_scope_and_trusted_bytes_are_required(self) -> None:
        bad = dict(self.contents)
        bad["README.md"] = b"outside"
        with self.assertRaisesRegex(publisher.CanonicalPublishError, "exact two allowed"):
            publisher.prepare(
                self.repo, "refs/heads/brain/development", self.base, self.old, self.new, bad
            )
        wrong = dict(self.contents)
        wrong[publisher.ALLOWED_PATHS[0]] = b"unreviewed bytes"
        with self.assertRaisesRegex(publisher.CanonicalPublishError, "trusted new blob"):
            publisher.prepare(
                self.repo, "refs/heads/brain/development", self.base, self.old, self.new, wrong
            )

    def test_dirty_selected_path_is_rejected_and_unrelated_dirty_is_preserved(self) -> None:
        (self.repo / publisher.ALLOWED_PATHS[0]).write_text("owner edit\n")
        (self.repo / "unrelated.txt").write_text("unrelated owner edit\n")
        with self.assertRaisesRegex(publisher.CanonicalPublishError, "unknown user edits"):
            self._approve()
        self.assertEqual(git(self.repo, "rev-parse", "refs/heads/brain/development"), self.base)
        self.assertEqual((self.repo / "unrelated.txt").read_text(), "unrelated owner edit\n")

    def test_stale_compare_and_swap_holds_unknown_third_tip(self) -> None:
        (self.repo / "unrelated.txt").write_text("third writer\n")
        third = self._commit("third writer")
        with self.assertRaisesRegex(publisher.CanonicalPublishError, "unknown third tip"):
            self._approve()
        self.assertEqual(git(self.repo, "rev-parse", "refs/heads/brain/development"), third)

    def test_review_must_bind_exact_candidate_and_receipt(self) -> None:
        with self.assertRaisesRegex(publisher.CanonicalPublishError, "exact candidate receipt"):
            publisher.publish(
                self.repo,
                "refs/heads/brain/development",
                self.receipt,
                self.receipt["candidate"],
                "0" * 64,
            )
        self.assertEqual(git(self.repo, "rev-parse", "refs/heads/brain/development"), self.base)

    def test_retry_reconciles_interruption_after_cas_before_materialization(self) -> None:
        real_git = publisher._git
        failed = False

        def interrupted(repo: Path, *args: str, **kwargs):
            nonlocal failed
            if args[:1] == ("update-index",) and not failed:
                failed = True
                raise publisher.CanonicalPublishError("synthetic interruption")
            return real_git(repo, *args, **kwargs)

        with patch.object(publisher, "_git", side_effect=interrupted):
            with self.assertRaisesRegex(publisher.CanonicalPublishError, "synthetic interruption"):
                self._approve()
        self.assertEqual(
            git(self.repo, "rev-parse", "refs/heads/brain/development"), self.receipt["candidate"]
        )
        self.assertEqual(
            (self.repo / publisher.ALLOWED_PATHS[0]).read_bytes(),
            f"base:{publisher.ALLOWED_PATHS[0]}\n".encode(),
        )
        result = self._approve()
        self.assertEqual(result["status"], "reconciled")
        self.assertEqual(
            (self.repo / publisher.ALLOWED_PATHS[0]).read_bytes(),
            self.contents[publisher.ALLOWED_PATHS[0]],
        )
        self.assertEqual(
            (self.repo / publisher.ALLOWED_PATHS[1]).read_bytes(),
            self.contents[publisher.ALLOWED_PATHS[1]],
        )

    def test_retry_reconciles_interruption_between_selected_materializations(self) -> None:
        real_state = publisher._file_state
        failed = False

        def interrupted(repo: Path, path: str, old: str, new: str) -> str:
            nonlocal failed
            if (
                path == publisher.ALLOWED_PATHS[1]
                and (self.repo / publisher.ALLOWED_PATHS[0]).read_bytes()
                == self.contents[publisher.ALLOWED_PATHS[0]]
                and not failed
            ):
                failed = True
                raise publisher.CanonicalPublishError("synthetic interruption between files")
            return real_state(repo, path, old, new)

        with patch.object(publisher, "_file_state", side_effect=interrupted):
            with self.assertRaisesRegex(publisher.CanonicalPublishError, "between files"):
                self._approve()
        state = publisher.inspect(self.repo, "refs/heads/brain/development", self.receipt)
        self.assertEqual(state["ref_state"], "cas_applied")
        self.assertEqual(state["files_state"], "partial")
        self._approve()
        self.assertEqual(
            {path: (self.repo / path).read_bytes() for path in publisher.ALLOWED_PATHS},
            self.contents,
        )

    def test_unrelated_file_and_index_state_survive_publication(self) -> None:
        (self.repo / "unrelated.txt").write_text("owner dirty unrelated\n")
        git(self.repo, "add", "unrelated.txt")
        result = self._approve()
        self.assertEqual(result["status"], "reconciled")
        self.assertEqual((self.repo / "unrelated.txt").read_text(), "owner dirty unrelated\n")
        self.assertEqual(git(self.repo, "show", ":unrelated.txt"), "owner dirty unrelated")
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.receipt["candidate"])


if __name__ == "__main__":
    unittest.main()
