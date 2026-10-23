"""Synthetic, disposable-repository tests for the Paperclip GitGate broker."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

from engineering.git_gate import GitGate, VerificationPolicy
from engineering.paperclip_git_broker import (
    ACTIVE_REF,
    BrokerError,
    OperationBinding,
    PaperclipGitBroker,
    ReviewerApproval,
    begin_company_drain,
    read_company_fence,
)


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def init(path: Path) -> None:
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "synthetic")
    git(path, "config", "user.email", "synthetic@example.invalid")


def commit(path: Path, message: str) -> str:
    git(path, "add", "-A")
    git(path, "commit", "-m", message)
    return git(path, "rev-parse", "HEAD")


class PaperclipGitBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="paperclip-git-broker-")
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        init(self.source)
        (self.source / "README.md").write_text("synthetic base\n")
        self.base = commit(self.source, "synthetic base")
        self.integration = self.root / "integration"
        git(self.root, "clone", str(self.source), str(self.integration))
        self.worker = self.root / "worker"
        git(self.root, "clone", str(self.source), str(self.worker))
        git(self.worker, "config", "user.name", "worker")
        git(self.worker, "config", "user.email", "worker@example.invalid")
        (self.worker / "src").mkdir()
        (self.worker / "src" / "change.py").write_text("value = 'synthetic'\n")
        self.submitted = commit(self.worker, "synthetic worker change")
        self.policy = VerificationPolicy((("python", "-m", "unittest"),), (), "paperclip-test")
        self.gate = GitGate(
            self.source,
            self.integration,
            self.policy,
            self.root / "receipts",
            operation_guard=nullcontext,
        )
        self.broker = PaperclipGitBroker(self.gate, self.root / "journal")
        self.binding = OperationBinding(
            "company-local",
            "issue-17",
            "run-1",
            "worker-luna",
            "reviewer-sol",
            self.base,
            self.submitted,
            ("src",),
            self.policy.digest,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def approval(self, candidate, *, approved=True) -> ReviewerApproval:
        return ReviewerApproval(
            self.binding.company_id,
            self.binding.run_id,
            candidate.sha,
            self.binding.policy_digest,
            self.binding.reviewer_agent_id,
            "review-run-9",
            "e" * 64,
            approved,
        )

    def test_prepare_review_verify_and_publish_are_bound_and_idempotent(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        self.assertEqual(candidate, self.broker.prepare(self.binding, self.worker))
        self.assertEqual(self.broker.gate.active_ref, ACTIVE_REF)
        with self.assertRaises(AttributeError):
            self.broker.active_ref = "refs/heads/main"
        verify_calls = 0

        def verifier(checkout, sha, policy):
            nonlocal verify_calls
            verify_calls += 1
            self.assertEqual(git(checkout, "rev-parse", "HEAD"), sha)
            self.assertEqual(policy.digest, self.binding.policy_digest)
            return {"status": "passed"}

        result = self.broker.integrate(self.binding, candidate, self.approval(candidate), verifier)
        duplicate = self.broker.integrate(
            self.binding, candidate, self.approval(candidate), verifier
        )
        self.assertEqual(result, duplicate)
        self.assertEqual(result.status, "published")
        self.assertEqual(verify_calls, 1)
        self.assertEqual(git(self.integration, "rev-parse", ACTIVE_REF), candidate.sha)
        journal = json.loads(
            (self.root / "journal" / f"{self.binding.operation_key}.json").read_text()
        )
        self.assertEqual(journal["approval"]["reviewer_run_id"], "review-run-9")
        self.assertEqual(journal["approval"]["evidence_digest"], "e" * 64)

    def test_old_unbound_journal_is_not_replayed(self) -> None:
        self.broker.prepare(self.binding, self.worker)
        path = self.root / "journal" / f"{self.binding.operation_key}.json"
        journal = json.loads(path.read_text())
        journal["binding"].pop("active_ref")
        journal["binding"].pop("owner_config_digest")
        journal["binding"].pop("owner_roots")
        path.write_text(json.dumps(journal, sort_keys=True, separators=(",", ":")))
        with self.assertRaisesRegex(BrokerError, "substituted binding"):
            self.broker.recover_candidate(self.binding)

    def test_development_lane_rejects_unbound_direct_binding(self) -> None:
        gate = GitGate(
            self.source,
            self.integration,
            self.policy,
            self.root / "dev-receipts",
            active_ref="refs/heads/brain/development",
        )
        broker = PaperclipGitBroker(
            gate,
            self.root / "dev-journal",
            active_ref="refs/heads/brain/development",
        )
        binding = OperationBinding(
            self.binding.company_id,
            self.binding.issue_id,
            self.binding.run_id,
            self.binding.worker_agent_id,
            self.binding.reviewer_agent_id,
            self.binding.base_sha,
            self.binding.submitted_sha,
            self.binding.scope,
            self.binding.policy_digest,
            active_ref="refs/heads/brain/development",
        )
        with self.assertRaisesRegex(BrokerError, "requires owner config and roots"):
            broker.prepare(binding, self.worker)

    def test_development_lane_requires_detached_publisher_head(self) -> None:
        ref = "refs/heads/brain/development"
        git(self.source, "update-ref", ref, self.base)
        git(self.integration, "update-ref", ref, self.base)
        receipts = self.root / "bound-receipts"
        journal = self.root / "bound-journal"
        gate = GitGate(
            self.source,
            self.integration,
            self.policy,
            receipts,
            active_ref=ref,
        )
        broker = PaperclipGitBroker(gate, journal, active_ref=ref)
        roots = tuple(
            str(path.resolve())
            for path in (self.source, self.integration, receipts, journal, self.worker)
        )
        binding = OperationBinding(
            self.binding.company_id,
            self.binding.issue_id,
            self.binding.run_id,
            self.binding.worker_agent_id,
            self.binding.reviewer_agent_id,
            self.binding.base_sha,
            self.binding.submitted_sha,
            self.binding.scope,
            self.binding.policy_digest,
            active_ref=ref,
            owner_config_digest="f" * 64,
            owner_roots=roots,
        )
        with self.assertRaisesRegex(BrokerError, "preparation outcome is held"):
            broker.prepare(binding, self.worker)

    def test_candidate_recovery_settles_publish_intent_from_exact_ref_and_receipt(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        approval = self.approval(candidate)
        result = self.broker.integrate(
            self.binding, candidate, approval, lambda *_: {"status": "passed"}
        )
        journal_path = self.root / "journal" / f"{self.binding.operation_key}.json"
        journal = json.loads(journal_path.read_text())
        journal["state"] = "publish_intent"
        journal_path.write_text(json.dumps(journal, sort_keys=True, separators=(",", ":")))

        recovered_record, recovered_candidate = self.broker.recover_candidate(self.binding)
        self.assertEqual(recovered_record["state"], "published")
        self.assertEqual(recovered_candidate, candidate)
        self.assertEqual(
            self.broker.integrate(self.binding, recovered_candidate, approval, None), result
        )
        published_record, published_candidate = self.broker.recover_candidate(self.binding)
        self.assertEqual(published_record["state"], "published")
        self.assertEqual(published_candidate, candidate)

    def test_reviewer_rejection_is_terminal_bound_and_idempotent(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        rejection = self.approval(candidate, approved=False)
        result = self.broker.reject(self.binding, candidate, rejection)
        replay = self.broker.reject(self.binding, candidate, rejection)
        self.assertEqual(result, replay)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.evidence_digest, rejection.evidence_digest)
        self.assertIsNone(self._active_tip())
        journal = json.loads(
            (self.root / "journal" / f"{self.binding.operation_key}.json").read_text()
        )
        self.assertEqual(journal["state"], "rejected")
        self.assertEqual(journal["approval"]["approved"], False)
        with self.assertRaisesRegex(BrokerError, "differs from the recorded evidence"):
            self.broker.reject(
                self.binding,
                candidate,
                replace(rejection, evidence_digest="f" * 64),
            )

    def test_substituted_reviewer_binding_cannot_reuse_operation_key(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        altered = OperationBinding(
            **{**self.binding.__dict__, "reviewer_agent_id": "reviewer-other"}
        )
        with self.assertRaisesRegex(BrokerError, "substituted binding"):
            self.broker.prepare(altered, self.worker)
        with self.assertRaisesRegex(BrokerError, "substituted binding"):
            self.broker.integrate(altered, candidate, self.approval(candidate), lambda *_: {})

    def test_published_replay_rejects_substituted_reviewer_evidence(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        approval = self.approval(candidate)
        self.broker.integrate(self.binding, candidate, approval, lambda *_: {"status": "passed"})
        for altered in (
            replace(approval, reviewer_run_id="review-run-substituted"),
            replace(approval, evidence_digest="f" * 64),
        ):
            with self.assertRaisesRegex(BrokerError, "differs from the recorded"):
                self.broker.integrate(
                    self.binding, candidate, altered, lambda *_: {"status": "passed"}
                )

    def test_approval_must_bind_exact_candidate_and_distinct_identity(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        bad = ReviewerApproval(
            self.binding.company_id,
            self.binding.run_id,
            self.base,
            self.binding.policy_digest,
            self.binding.reviewer_agent_id,
            "review-run-9",
            "e" * 64,
            True,
        )
        with self.assertRaisesRegex(BrokerError, "exact operation"):
            self.broker.integrate(self.binding, candidate, bad, lambda *_: {"status": "passed"})
        self.assertIsNone(self._active_tip())

    def test_lost_publication_ack_is_reconciled_from_exact_dev_ref(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        original_publish = self.broker.gate.publish

        def publish_then_lose_ack(*args, **kwargs):
            original_publish(*args, **kwargs)
            raise TimeoutError("synthetic lost acknowledgement")

        self.broker.gate.publish = publish_then_lose_ack
        result = self.broker.integrate(
            self.binding,
            candidate,
            self.approval(candidate),
            lambda *_: {"status": "passed"},
        )
        self.assertEqual(result.status, "published")
        self.assertEqual(self._active_tip(), candidate.sha)

    def test_missing_receipt_rejected_on_published_replay(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        approval = self.approval(candidate)
        result = self.broker.integrate(
            self.binding, candidate, approval, lambda *_: {"status": "passed"}
        )
        (self.root / "receipts" / f"{result.receipt_digest}.json").unlink()
        with self.assertRaisesRegex(BrokerError, "receipt"):
            self.broker.integrate(
                self.binding, candidate, approval, lambda *_: {"status": "passed"}
            )

    def test_tampered_receipt_rejected_before_publish_intent_reconciliation(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        approval = self.approval(candidate)
        result = self.broker.integrate(
            self.binding, candidate, approval, lambda *_: {"status": "passed"}
        )
        journal_path = next((self.root / "journal").glob("*.json"))
        journal = json.loads(journal_path.read_text())
        journal["state"] = "publish_intent"
        journal_path.write_text(json.dumps(journal, sort_keys=True, separators=(",", ":")))
        receipt_path = self.root / "receipts" / f"{result.receipt_digest}.json"
        receipt_path.chmod(0o600)
        receipt_path.write_text('{"tampered":true}')
        with self.assertRaisesRegex(BrokerError, "receipt"):
            self.broker.integrate(
                self.binding, candidate, approval, lambda *_: {"status": "passed"}
            )

    def test_two_broker_processes_share_the_operation_flock(self) -> None:
        self.broker.prepare(self.binding, self.worker)
        counter = self.root / "verifier-calls.jsonl"
        script = """
import os, sys, time
from contextlib import nullcontext
from pathlib import Path

from engineering.git_gate import GitGate, VerificationPolicy
from engineering.paperclip_git_broker import (
    OperationBinding,
    PaperclipGitBroker,
    ReviewerApproval,
)
root, base, submitted, policy_digest, counter = sys.argv[1:]
root = Path(root)
policy = VerificationPolicy((("python", "-m", "unittest"),), (), "paperclip-test")
gate = GitGate(
    root / "source",
    root / "integration",
    policy,
    root / "receipts",
    operation_guard=nullcontext,
)
broker = PaperclipGitBroker(gate, root / "journal")
binding = OperationBinding(
    "company-local",
    "issue-17",
    "run-1",
    "worker-luna",
    "reviewer-sol",
    base,
    submitted,
    ("src",),
    policy_digest,
)
candidate = broker.prepare(binding, root / "worker")
approval = ReviewerApproval(
    "company-local",
    "run-1",
    candidate.sha,
    policy_digest,
    "reviewer-sol",
    "review-run-9",
    "e" * 64,
    True,
)
def verify(*_args):
    fd = os.open(counter, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as stream:
        stream.write("called\\n")
        stream.flush()
        os.fsync(stream.fileno())
    time.sleep(0.15)
    return {"status": "passed"}
print(broker.integrate(binding, candidate, approval, verify).status)
"""
        args = [
            sys.executable,
            "-c",
            script,
            str(self.root),
            self.base,
            self.submitted,
            self.policy.digest,
            str(counter),
        ]
        children = [
            subprocess.Popen(
                args,
                cwd=Path.cwd(),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        outcomes = [child.communicate(timeout=10) for child in children]
        self.assertEqual([child.returncode for child in children], [0, 0], outcomes)
        self.assertEqual([stdout.strip() for stdout, _ in outcomes], ["published", "published"])
        self.assertEqual(counter.read_text().splitlines(), ["called"])

    def test_unknown_publication_without_exact_tip_is_held(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        calls = 0

        def verifier(*_args):
            nonlocal calls
            calls += 1
            return {"status": "passed"}

        self.broker.gate.publish = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("synthetic unknown publication outcome")
        )
        for _ in range(2):
            with self.assertRaisesRegex(BrokerError, "held"):
                self.broker.integrate(self.binding, candidate, self.approval(candidate), verifier)
        self.assertEqual(calls, 1)
        self.assertIsNone(self._active_tip())

    def test_unknown_verification_is_held_without_blind_retry(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        calls = 0

        def uncertain(*_args):
            nonlocal calls
            calls += 1
            raise TimeoutError("synthetic unknown verifier outcome")

        for _ in range(2):
            with self.assertRaisesRegex(BrokerError, "held"):
                self.broker.integrate(self.binding, candidate, self.approval(candidate), uncertain)
        self.assertEqual(calls, 1)

    def test_drain_blocks_candidate_registration_and_publication_for_its_company(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        begin_company_drain(self.root / "journal", self.binding.company_id, "a" * 64)
        with self.assertRaisesRegex(BrokerError, "durably draining"):
            self.broker.prepare(self.binding, self.worker)
        with self.assertRaisesRegex(BrokerError, "durably draining"):
            self.broker.integrate(
                self.binding, candidate, self.approval(candidate), lambda *_: {"status": "passed"}
            )

        other_binding = replace(self.binding, company_id="company-other", issue_id="issue-other")
        other_candidate = self.broker.prepare(other_binding, self.worker)
        self.assertEqual(other_candidate.submitted_sha, self.submitted)
        self.assertEqual(
            read_company_fence(self.root / "journal", self.binding.company_id)["state"],
            "draining",
        )

    def test_drain_blocks_reconciliation_of_an_unknown_publish_outcome(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        approval = self.approval(candidate)
        self.broker.integrate(self.binding, candidate, approval, lambda *_: {"status": "passed"})
        self.assertEqual(self._active_tip(), candidate.sha)
        journal_path = self.root / "journal" / f"{self.binding.operation_key}.json"
        journal = json.loads(journal_path.read_text())
        # Model a process exit after the durable intent and Git ref update, before
        # the broker's post-publication receipt readback persisted its terminal state.
        journal["state"] = "publish_intent"
        journal_path.write_text(json.dumps(journal, sort_keys=True, separators=(",", ":")))
        begin_company_drain(self.root / "journal", self.binding.company_id, "d" * 64)
        with self.assertRaisesRegex(BrokerError, "durably draining"):
            self.broker.integrate(
                self.binding, candidate, approval, lambda *_: {"status": "passed"}
            )
        journal = json.loads(journal_path.read_text())
        self.assertEqual(journal["state"], "publish_intent")

    def test_cross_process_publish_that_started_first_completes_before_drain_fences(self) -> None:
        candidate = self.broker.prepare(self.binding, self.worker)
        marker = self.root / "verifier-started"
        publish_script = """
import sys, time
from contextlib import nullcontext
from pathlib import Path
from engineering.git_gate import GitGate, VerificationPolicy
from engineering.paperclip_git_broker import OperationBinding, PaperclipGitBroker, ReviewerApproval
root, base, submitted, policy_digest, marker, sha, checkout = sys.argv[1:]
root = Path(root)
policy = VerificationPolicy(((\"python\", \"-m\", \"unittest\"),), (), \"paperclip-test\")
gate = GitGate(
    root / \"source\",
    root / \"integration\",
    policy,
    root / \"receipts\",
    operation_guard=nullcontext,
)
broker = PaperclipGitBroker(gate, root / \"journal\")
binding = OperationBinding(
    \"company-local\", \"issue-17\", \"run-1\", \"worker-luna\", \"reviewer-sol\",
    base, submitted, (\"src\",), policy_digest,
)
candidate = broker.prepare(binding, root / \"worker\")
approval = ReviewerApproval(
    \"company-local\", \"run-1\", candidate.sha, policy_digest, \"reviewer-sol\",
    \"review-run-9\", \"e\" * 64, True,
)
def verify(*_):
    Path(marker).write_text(\"started\")
    time.sleep(0.35)
    return {\"status\": \"passed\"}
print(broker.integrate(binding, candidate, approval, verify).status)
"""
        publish = subprocess.Popen(
            [
                sys.executable,
                "-c",
                publish_script,
                str(self.root),
                self.base,
                self.submitted,
                self.policy.digest,
                str(marker),
                candidate.sha,
                str(candidate.checkout),
            ],
            cwd=Path.cwd(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists(), "publisher never entered its verified operation")
        drain_script = """
import sys
from pathlib import Path
from engineering.paperclip_git_broker import begin_company_drain, company_operation
root = Path(sys.argv[1])
with company_operation(root / \"journal\", \"company-local\"):
    begin_company_drain(root / \"journal\", \"company-local\", \"b\" * 64)
print(\"draining\")
"""
        drain = subprocess.Popen(
            [sys.executable, "-c", drain_script, str(self.root)],
            cwd=Path.cwd(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        publish_out = publish.communicate(timeout=10)
        drain_out = drain.communicate(timeout=10)
        self.assertEqual(publish.returncode, 0, publish_out)
        self.assertEqual(drain.returncode, 0, drain_out)
        self.assertEqual(publish_out[0].strip(), "published")
        self.assertEqual(drain_out[0].strip(), "draining")
        self.assertEqual(
            read_company_fence(self.root / "journal", "company-local")["state"],
            "draining",
        )
        self.assertEqual(git(self.integration, "rev-parse", ACTIVE_REF), candidate.sha)

    def _active_tip(self) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(self.integration), "rev-parse", "--verify", ACTIVE_REF],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None


if __name__ == "__main__":
    unittest.main()
