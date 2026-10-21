from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from engineering.git_gate import GitGate, GitGateError, VerificationPolicy


def git(path: Path, *args: str, input: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        input=input,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def init(path: Path) -> None:
    path.mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "test")
    git(path, "config", "user.email", "test@example.invalid")


def commit(path: Path, message: str) -> str:
    git(path, "add", "-A")
    git(path, "commit", "-m", message)
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def setup_gate(tmp_path: Path):
    integration = tmp_path / "integration"
    init(integration)
    (integration / "README.md").write_text("base\n")
    base = commit(integration, "base")
    worker = tmp_path / "worker"
    git(tmp_path, "clone", str(integration), str(worker))
    git(worker, "config", "user.name", "worker")
    git(worker, "config", "user.email", "worker@example.invalid")
    policy = VerificationPolicy(
        (("python", "-m", "pytest"),), (("PYTHONDONTWRITEBYTECODE", "1"),), "pilot"
    )
    gate = GitGate(integration, integration, policy, tmp_path / "receipts")
    return tmp_path, integration, worker, base, gate


def submit(setup, path: str = "src/module.py"):
    _, _, worker, base, gate = setup
    target = worker / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("value = 1\n")
    submitted = commit(worker, "worker change")
    candidate = gate.submit(worker, submitted, base, ["src"])
    return candidate


def verifier(checkout: Path, sha: str, policy: VerificationPolicy):
    assert git(checkout, "rev-parse", "HEAD") == sha
    assert policy.commands
    return {"status": "passed"}


def test_verified_candidate_publishes_once_and_duplicate_is_recoverable(setup_gate):
    _, integration, _, _, gate = setup_gate
    candidate = submit(setup_gate)
    receipt = gate.verify(candidate, verifier)
    zero = "0" * 40
    assert gate.publish(candidate, receipt.digest, zero) == candidate.sha
    assert git(integration, "rev-parse", "refs/heads/research/active") == candidate.sha
    # Retrying after a lost acknowledgement is safe and idempotent.
    assert gate.publish(candidate, receipt.digest, zero) == candidate.sha
    assert git(integration, "rev-parse", "refs/heads/research/active") == candidate.sha


def test_unverified_candidate_cannot_publish(setup_gate):
    _, _, _, _, gate = setup_gate
    candidate = submit(setup_gate)
    with pytest.raises(GitGateError, match="receipt"):
        gate.publish(candidate, "0" * 64, "0" * 40)


def test_tampered_receipt_is_rejected(setup_gate):
    _, _, _, _, gate = setup_gate
    candidate = submit(setup_gate)
    receipt = gate.verify(candidate, verifier)
    path = gate.receipt_store / f"{receipt.digest}.json"
    os.chmod(path, 0o640)
    path.write_text('{"candidate_sha":"forged"}')
    with pytest.raises(GitGateError, match="altered"):
        gate.publish(candidate, receipt.digest, "0" * 40)


def test_candidate_refuses_stale_integration_tip(setup_gate):
    _, integration, _, _, gate = setup_gate
    candidate = submit(setup_gate)
    (integration / "another.txt").write_text("advance\n")
    commit(integration, "advance integration")
    receipt = gate.verify(candidate, verifier)
    with pytest.raises(GitGateError, match="active tip moved"):
        gate.publish(candidate, receipt.digest, "0" * 40)


def test_candidate_refuses_active_ref_moved_after_construction(setup_gate):
    _, integration, worker, base, gate = setup_gate
    first = submit(setup_gate)
    first_receipt = gate.verify(first, verifier)
    gate.publish(first, first_receipt.digest, "0" * 40)

    (worker / "src" / "second.py").write_text("value = 2\n")
    second_sha = commit(worker, "second worker change")
    second = gate.submit(worker, second_sha, base, ["src"])
    second_receipt = gate.verify(second, verifier)

    git(integration, "update-ref", gate.ACTIVE_REF, base, first.sha)
    with pytest.raises(GitGateError, match="active ref differs from caller's expected tip"):
        gate.publish(second, second_receipt.digest, first.sha)
    assert git(integration, "rev-parse", gate.ACTIVE_REF) == base


def test_sequential_candidates_build_on_and_preserve_active_tip(setup_gate):
    _, integration, worker, base, gate = setup_gate
    first = submit(setup_gate)
    first_receipt = gate.verify(first, verifier)
    gate.publish(first, first_receipt.digest, "0" * 40)

    (worker / "src" / "second.py").write_text("value = 2\n")
    second_sha = commit(worker, "second worker change")
    second = gate.submit(worker, second_sha, base, ["src"])
    assert second.integration_tip == first.sha
    assert git(second.checkout, "show", "HEAD:src/module.py") == "value = 1"
    second_receipt = gate.verify(second, verifier)
    gate.publish(second, second_receipt.digest, first.sha)
    assert git(integration, "rev-parse", gate.ACTIVE_REF) == second.sha
    assert git(second.checkout, "show", "HEAD:src/second.py") == "value = 2"


def test_verifier_cannot_mutate_candidate_checkout(setup_gate):
    _, _, _, _, gate = setup_gate
    candidate = submit(setup_gate)

    def mutating_verifier(checkout: Path, sha: str, policy: VerificationPolicy):
        (checkout / "src" / "module.py").write_text("changed during check\n")
        return {"status": "passed"}

    with pytest.raises(GitGateError, match="modifications or untracked"):
        gate.verify(candidate, mutating_verifier)


def test_unscoped_and_protected_changes_are_rejected(setup_gate):
    _, _, worker, base, gate = setup_gate
    (worker / "docs").mkdir()
    (worker / "docs" / "escape.md").write_text("outside\n")
    submitted = commit(worker, "outside scope")
    with pytest.raises(GitGateError, match="outside allowed scope"):
        gate.submit(worker, submitted, base, ["src"])

    (worker / "engineering").mkdir()
    (worker / "engineering" / "evil.py").write_text("raise RuntimeError()\n")
    submitted2 = commit(worker, "protected path")
    with pytest.raises(GitGateError, match="protected path"):
        gate.submit(worker, submitted2, base, ["docs", "engineering"])


def test_symlink_change_rejected_even_when_in_scope(setup_gate):
    _, _, worker, base, gate = setup_gate
    (worker / "src").mkdir()
    os.symlink("../../outside", worker / "src" / "link.py")
    submitted = commit(worker, "symlink")
    with pytest.raises(GitGateError, match="symlink"):
        gate.submit(worker, submitted, base, ["src"])


def test_main_is_not_a_publish_target(setup_gate):
    _, integration, _, _, gate = setup_gate
    candidate = submit(setup_gate)
    receipt = gate.verify(candidate, verifier)
    assert gate.ACTIVE_REF == "refs/heads/research/active"
    gate.publish(candidate, receipt.digest, "0" * 40)
    assert git(integration, "rev-parse", "main") == candidate.integration_tip


def test_local_bare_remote_checks_active_and_main_before_normal_push(setup_gate, tmp_path):
    _, integration, worker, base, _ = setup_gate
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    # Seed remote main and a stable research/active baseline.
    git(integration, "remote", "add", "origin", str(bare))
    git(integration, "push", "origin", "main:main")
    git(integration, "push", "origin", "main:research/active")
    remote_active = git(integration, "ls-remote", "origin", "refs/heads/research/active").split()[0]
    remote_main = git(integration, "ls-remote", "origin", "refs/heads/main").split()[0]
    git(integration, "update-ref", "refs/heads/research/active", remote_active)
    gate = GitGate(
        integration,
        integration,
        VerificationPolicy((("make", "check"),)),
        tmp_path / "remote-receipts",
        remote="origin",
    )
    candidate = submit((tmp_path, integration, worker, base, gate))
    receipt = gate.verify(candidate, verifier)
    with pytest.raises(GitGateError, match="tip or main changed"):
        gate.publish(
            candidate,
            receipt.digest,
            remote_active,
            publish_remote=True,
            expected_remote_tip="0" * 40,
            expected_remote_main=remote_main,
        )
    assert git(integration, "rev-parse", gate.ACTIVE_REF) == remote_active
    assert (
        gate.publish(
            candidate,
            receipt.digest,
            remote_active,
            publish_remote=True,
            expected_remote_tip=remote_active,
            expected_remote_main=remote_main,
        )
        == candidate.sha
    )
    assert (
        gate.publish(
            candidate,
            receipt.digest,
            remote_active,
            publish_remote=True,
            expected_remote_tip=remote_active,
            expected_remote_main=remote_main,
        )
        == candidate.sha
    )
    assert (
        git(integration, "ls-remote", "origin", "refs/heads/research/active").split()[0]
        == candidate.sha
    )
    git(integration, "push", "origin", f"{candidate.sha}:refs/heads/main")
    with pytest.raises(GitGateError, match="tip or main changed"):
        gate.publish(
            candidate,
            receipt.digest,
            remote_active,
            publish_remote=True,
            expected_remote_tip=candidate.sha,
            expected_remote_main=remote_main,
        )
