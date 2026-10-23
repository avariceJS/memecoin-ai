from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from engineering.paperclip_company_handoff import (
    HandoffError,
    PaperclipAPI,
    _snapshot,
    hibernate,
    resume,
)


class FakeLoopbackPaperclip:
    """In-process fake for the fixed loopback API client; never contacts Paperclip."""

    def __init__(self, *, company_status="active", agents=None, issues=None, runs=None, missing=()):
        self.company_status = company_status
        self.agents = (
            agents
            if agents is not None
            else [
                {
                    "id": "agent-1",
                    "companyId": "company-1",
                    "status": "idle",
                    "lifecycleState": "ready",
                }
            ]
        )
        self.issues = issues if issues is not None else []
        self.runs = runs if runs is not None else []
        self.missing = set(missing)
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method, path, body):
        self.calls.append((method, path, body))
        if path in self.missing:
            raise HandoffError("fake missing response")
        if path == "/companies/company-1":
            if method == "PATCH":
                self.company_status = body["status"]
            return {"id": "company-1", "status": self.company_status, "secret": "never copied"}
        if path == "/companies/company-1/agents":
            return self.agents
        if path.startswith("/companies/company-1/issues?"):
            query = parse_qs(urlsplit(path).query)
            offset = int(query["offset"][0])
            return self.issues[offset : offset + int(query["limit"][0])]
        if path == "/companies/company-1/heartbeat-runs?limit=1000&summary=true":
            return self.runs
        raise AssertionError(f"unexpected fake request: {method} {path}")


class PaperclipCompanyHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="paperclip-company-handoff-")
        self.root = Path(self.temp.name)
        self.journal = self.root / "journal"
        self.journal.mkdir()
        self.handoff = self.root / "private-handoffs"

    def tearDown(self):
        self.temp.cleanup()

    def api(self, fixture):
        return PaperclipAPI(request=fixture.request)

    def test_hibernate_pauses_then_polls_active_run_and_writes_allowlisted_private_handoff(self):
        fixture = FakeLoopbackPaperclip(
            company_status="active",
            runs=[
                {
                    "id": "run-1",
                    "companyId": "company-1",
                    "status": "running",
                    "prompt": "private prompt",
                }
            ],
        )
        # The active status remains visible on the first observation and settles on the next.
        original_request = fixture.request
        reads = 0

        def delayed_settle(method, path, body):
            nonlocal reads
            result = original_request(method, path, body)
            if path == "/companies/company-1/heartbeat-runs?limit=1000&summary=true":
                reads += 1
                if reads == 2:
                    fixture.runs = []
            return result

        result = hibernate(
            self.api(FakeLoopbackPaperclipProxy(fixture, delayed_settle)),
            "company-1",
            self.handoff,
            self.journal,
            0.2,
            0.001,
        )
        self.assertTrue(result["complete"])
        payload = json.loads(Path(result["handoff_path"]).read_text())
        self.assertEqual(payload["snapshot"]["company"]["status"], "paused")
        self.assertEqual(payload["snapshot"]["runs"], [])
        self.assertIn("no OS process proof", payload["quiescence_basis"])
        self.assertNotIn("private prompt", json.dumps(payload))
        self.assertNotIn("secret", json.dumps(payload))
        self.assertEqual(Path(result["handoff_path"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.handoff.stat().st_mode & 0o777, 0o700)

    def test_missing_api_state_is_saved_as_incomplete(self):
        fixture = FakeLoopbackPaperclip(
            company_status="paused", missing={"/companies/company-1/agents"}
        )
        result = hibernate(self.api(fixture), "company-1", self.handoff, self.journal, 0)
        self.assertFalse(result["complete"])
        self.assertFalse(result["snapshot"]["complete"])
        self.assertTrue(any("agents:" in problem for problem in result["snapshot"]["problems"]))

    def test_unknown_run_status_is_held_and_not_quiescent(self):
        fixture = FakeLoopbackPaperclip(
            company_status="paused",
            runs=[{"id": "run-unknown", "companyId": "company-1", "status": "mystery"}],
        )
        result = hibernate(self.api(fixture), "company-1", self.handoff, self.journal, 0)
        self.assertFalse(result["complete"])
        self.assertIn(
            {"kind": "unknown", "detail": "run:run-unknown:unknown_status"},
            result["snapshot"]["unresolved"],
        )

    def test_active_run_status_is_preserved_and_resume_is_refused(self):
        fixture = FakeLoopbackPaperclip(
            company_status="paused",
            runs=[{"id": "run-active", "companyId": "company-1", "status": "queued"}],
        )
        hibernate(self.api(fixture), "company-1", self.handoff, self.journal, 0)
        with self.assertRaisesRegex(HandoffError, "incomplete or unresolved"):
            resume(self.api(fixture), "company-1", "founder-wake-1", self.handoff, self.journal)
        self.assertFalse(
            any(
                method == "PATCH" and body == {"status": "active"}
                for method, _path, body in fixture.calls
            )
        )

    def test_explicit_founder_resume_requires_reconciled_same_company_state(self):
        fixture = FakeLoopbackPaperclip(company_status="paused")
        hibernate(self.api(fixture), "company-1", self.handoff, self.journal, 0)
        result = resume(
            self.api(fixture), "company-1", "founder-wake-2", self.handoff, self.journal
        )
        self.assertTrue(result["complete"])
        self.assertEqual(result["company_status"], "active")
        intent = json.loads(Path(result["intent_path"]).read_text())
        self.assertEqual(intent["founder_command_id"], "founder-wake-2")
        self.assertEqual(
            [body for method, _path, body in fixture.calls if method == "PATCH"],
            [{"status": "active"}],
        )
        fence = json.loads(next(self.journal.glob("company-*.json")).read_text())
        self.assertEqual(fence["state"], "open")

    def test_broker_journal_unknown_state_blocks_resume(self):
        journal_entry = {
            "schema": "paperclip-git-operation-v1",
            "operation_key": hashlib.sha256(
                json.dumps(["company-1", "issue-1", "run-1"], separators=(",", ":")).encode()
            ).hexdigest(),
            "binding": {
                "company_id": "company-1",
                "issue_id": "issue-1",
                "run_id": "run-1",
                "worker_agent_id": "worker-1",
                "reviewer_agent_id": "reviewer-1",
                "base_sha": "a" * 40,
                "submitted_sha": "b" * 40,
                "scope": ["src"],
                "policy_digest": "c" * 64,
            },
            "state": "publish_intent",
        }
        journal_path = self.journal / "op.json"
        journal_path.write_text(json.dumps(journal_entry))
        fixture = FakeLoopbackPaperclip(company_status="paused")
        hibernate(self.api(fixture), "company-1", self.handoff, self.journal, 0)
        with self.assertRaisesRegex(HandoffError, "incomplete or unresolved"):
            resume(self.api(fixture), "company-1", "founder-wake-3", self.handoff, self.journal)
        journal_entry["state"] = "rejected"
        journal_path.write_text(json.dumps(journal_entry))
        settled = _snapshot(self.api(fixture), "company-1", self.journal)
        self.assertTrue(settled["complete"])
        self.assertFalse(settled["unresolved"])

    def test_optional_repository_refs_record_only_branch_head_and_dirty_bit(self):
        fixture = FakeLoopbackPaperclip(company_status="paused")
        outputs = ["org/paperclip-integration\n", f"{'a' * 40}\n", " M private.txt\n"]
        with patch(
            "engineering.paperclip_company_handoff.safe_git",
            side_effect=[SimpleNamespace(stdout=value.encode()) for value in outputs],
        ):
            result = hibernate(
                self.api(fixture),
                "company-1",
                self.handoff,
                self.journal,
                0,
                repo_paths=(Path("/private/repo"),),
            )
        ref = result["snapshot"]["repository_refs"][0]
        self.assertEqual(
            ref,
            {
                "name": "repo",
                "branch": "org/paperclip-integration",
                "head": "a" * 40,
                "dirty": True,
            },
        )

    def test_failed_cancelled_interrupted_timed_out_and_unknown_runs_are_unresolved(self):
        for status in ("failed", "cancelled", "interrupted", "timed_out", "mystery"):
            with self.subTest(status=status):
                fixture = FakeLoopbackPaperclip(
                    company_status="paused",
                    runs=[{"id": "uncertain-run", "companyId": "company-1", "status": status}],
                )
                snapshot = _snapshot(self.api(fixture), "company-1", self.journal)
                self.assertTrue(snapshot["unresolved"])
                if status == "mystery":
                    self.assertFalse(snapshot["complete"])

    def test_missing_or_invalid_journal_path_is_incomplete_and_blocks_actions(self):
        fixture = FakeLoopbackPaperclip(company_status="paused")
        missing = self.root / "missing-journal"
        snapshot = _snapshot(self.api(fixture), "company-1", missing)
        self.assertFalse(snapshot["complete"])
        self.assertTrue(any("broker_journal:" in item for item in snapshot["problems"]))
        with self.assertRaisesRegex(HandoffError, "journal directory is missing or invalid"):
            hibernate(self.api(fixture), "company-1", self.handoff, missing, 0)

    def test_malformed_nested_binding_is_not_dismissed_as_an_unrelated_company(self):
        (self.journal / "broken.json").write_text(
            json.dumps({"schema": "paperclip-git-operation-v1", "binding": None})
        )
        fixture = FakeLoopbackPaperclip(company_status="paused")
        snapshot = _snapshot(self.api(fixture), "company-1", self.journal)
        self.assertFalse(snapshot["complete"])
        self.assertTrue(
            any("broker_journal:broken.json:unknown" in item for item in snapshot["problems"])
        )

    def test_invalid_nested_binding_fields_block_a_terminal_journal_entry(self):
        binding = {
            "company_id": "company-1",
            "issue_id": "issue-1",
            "run_id": "run-1",
            "worker_agent_id": "worker-1",
            "reviewer_agent_id": "reviewer-1",
            "base_sha": "not-a-commit",
            "submitted_sha": "b" * 40,
            "scope": ["src"],
            "policy_digest": "c" * 64,
        }
        operation_key = hashlib.sha256(
            json.dumps(["company-1", "issue-1", "run-1"], separators=(",", ":")).encode()
        ).hexdigest()
        (self.journal / "invalid-terminal.json").write_text(
            json.dumps(
                {
                    "schema": "paperclip-git-operation-v1",
                    "operation_key": operation_key,
                    "binding": binding,
                    "state": "published",
                }
            )
        )
        fixture = FakeLoopbackPaperclip(company_status="paused")
        snapshot = _snapshot(self.api(fixture), "company-1", self.journal)
        self.assertFalse(snapshot["complete"])
        self.assertTrue(
            any(
                "broker_journal:invalid-terminal.json:unknown" in item
                for item in snapshot["problems"]
            )
        )

    def test_resume_unknown_patch_result_is_never_replayed(self):
        fixture = FakeLoopbackPaperclip(company_status="paused")
        hibernate(self.api(fixture), "company-1", self.handoff, self.journal, 0)
        original = fixture.request
        patch_calls = 0

        def lose_patch_ack(method, path, body):
            nonlocal patch_calls
            if method == "PATCH" and body == {"status": "active"}:
                patch_calls += 1
                raise HandoffError("synthetic lost PATCH acknowledgement")
            return original(method, path, body)

        api = PaperclipAPI(request=lose_patch_ack)
        with self.assertRaises(HandoffError):
            resume(api, "company-1", "founder-wake-4", self.handoff, self.journal)
        with self.assertRaisesRegex(HandoffError, "will not be resent"):
            resume(api, "company-1", "founder-wake-4", self.handoff, self.journal)
        self.assertEqual(patch_calls, 1)

    def test_lost_resume_ack_keeps_fence_for_cancelled_unknown_and_active_work(self):
        for status in ("cancelled", "mystery", "queued"):
            with self.subTest(status=status):
                for journal_file in self.journal.glob("*.json"):
                    journal_file.unlink()
                fixture = FakeLoopbackPaperclip(company_status="paused")
                hibernate(self.api(fixture), "company-1", self.handoff, self.journal, 0)
                original = fixture.request
                patch_calls = 0

                def lose_ack_after_patch(method, path, body, original=original):
                    nonlocal patch_calls
                    result = original(method, path, body)
                    if method == "PATCH" and body == {"status": "active"}:
                        patch_calls += 1
                        raise HandoffError("synthetic lost PATCH acknowledgement")
                    return result

                with self.assertRaisesRegex(HandoffError, "lost PATCH"):
                    resume(
                        PaperclipAPI(request=lose_ack_after_patch),
                        "company-1",
                        "founder-wake-recovery",
                        self.handoff,
                        self.journal,
                    )
                fixture.runs = [{"id": "new-run", "companyId": "company-1", "status": status}]
                with self.assertRaisesRegex(HandoffError, "fresh quiescent snapshot"):
                    resume(
                        self.api(fixture),
                        "company-1",
                        "founder-wake-recovery",
                        self.handoff,
                        self.journal,
                    )
                fence = json.loads(next(self.journal.glob("company-*.json")).read_text())
                self.assertEqual(fence["state"], "resume_intent")
                self.assertEqual(patch_calls, 1)

    def test_lost_resume_ack_settles_from_two_fresh_quiescent_snapshots_once(self):
        fixture = FakeLoopbackPaperclip(company_status="paused")
        hibernate(self.api(fixture), "company-1", self.handoff, self.journal, 0)
        original = fixture.request
        patch_calls = 0

        def lose_ack_after_patch(method, path, body):
            nonlocal patch_calls
            result = original(method, path, body)
            if method == "PATCH" and body == {"status": "active"}:
                patch_calls += 1
                raise HandoffError("synthetic lost PATCH acknowledgement")
            return result

        with self.assertRaisesRegex(HandoffError, "lost PATCH"):
            resume(
                PaperclipAPI(request=lose_ack_after_patch),
                "company-1",
                "founder-wake-confirmed",
                self.handoff,
                self.journal,
            )
        result = resume(
            self.api(fixture),
            "company-1",
            "founder-wake-confirmed",
            self.handoff,
            self.journal,
        )
        self.assertTrue(result["complete"])
        self.assertRegex(result["resume_confirmation_sha256"], r"^[0-9a-f]{64}$")
        fence = json.loads(next(self.journal.glob("company-*.json")).read_text())
        self.assertEqual(fence["state"], "open")
        self.assertEqual(fence["resume_confirmation_sha256"], result["resume_confirmation_sha256"])
        self.assertEqual(patch_calls, 1)
        with self.assertRaisesRegex(HandoffError, "durably drained"):
            resume(
                self.api(fixture),
                "company-1",
                "founder-wake-confirmed",
                self.handoff,
                self.journal,
            )

    def test_broker_journal_symlink_is_not_followed(self):
        target = self.root / "outside-journal.json"
        target.write_text(json.dumps({"schema": "paperclip-git-operation-v1"}))
        (self.journal / "linked.json").symlink_to(target)
        fixture = FakeLoopbackPaperclip(company_status="paused")
        snapshot = _snapshot(self.api(fixture), "company-1", self.journal)
        self.assertFalse(snapshot["complete"])
        self.assertTrue(
            any("broker_journal:linked.json:unknown" in item for item in snapshot["problems"])
        )

    def test_repository_reads_use_safe_git_helper(self):
        fixture = FakeLoopbackPaperclip(company_status="paused")
        outputs = ["main\n", f"{'a' * 40}\n", ""]
        with patch(
            "engineering.paperclip_company_handoff.safe_git",
            side_effect=[SimpleNamespace(stdout=value.encode()) for value in outputs],
        ) as helper:
            _snapshot(self.api(fixture), "company-1", self.journal, (Path("/private/repo"),))
        self.assertEqual(helper.call_count, 3)

    def test_repository_inspection_does_not_execute_configured_fsmonitor_command(self):
        import subprocess

        repo = self.root / "repo-with-fsmonitor"
        repo.mkdir()
        subprocess.run(
            ["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True
        )
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "synthetic"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "synthetic@example.invalid"],
            check=True,
        )
        (repo / "tracked.txt").write_text("synthetic\n")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "synthetic"],
            check=True,
            capture_output=True,
        )
        marker = self.root / "fsmonitor-executed"
        subprocess.run(
            ["git", "-C", str(repo), "config", "core.fsmonitor", f"touch {marker}"], check=True
        )
        fixture = FakeLoopbackPaperclip(company_status="paused")
        snapshot = _snapshot(self.api(fixture), "company-1", self.journal, (repo,))
        self.assertTrue(snapshot["repository_refs"][0]["head"])
        self.assertFalse(marker.exists())


class FakeLoopbackPaperclipProxy:
    """Inject an explicit fixture handler while retaining its inspectable state."""

    def __init__(self, fixture, handler):
        self.fixture = fixture
        self.request = handler


if __name__ == "__main__":
    unittest.main()
