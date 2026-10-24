from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
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


def valid_receipt_bytes(gate: GitGate, candidate) -> bytes:
    body = {
        "candidate_sha": candidate.sha,
        "policy_hash": gate.policy.digest,
        "commands": gate.policy.commands,
        "env": gate.policy.env,
        "result": {"status": "passed"},
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def valid_receipt_digest(gate: GitGate, candidate) -> str:
    return hashlib.sha256(valid_receipt_bytes(gate, candidate)).hexdigest()


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


def test_receipt_fifo_is_rejected_without_blocking(setup_gate):
    _, integration, _, _, gate = setup_gate
    digest = "a" * 64
    os.mkfifo(gate.receipt_store / f"{digest}.json")
    script = """
import sys
from pathlib import Path
from engineering.git_gate import GitGate, GitGateError, VerificationPolicy
repo, receipts, digest = sys.argv[1:]
gate = GitGate(Path(repo), Path(repo), VerificationPolicy(((\"python\",),)), Path(receipts))
try:
    gate.load_receipt(digest, \"b\" * 40)
except GitGateError:
    raise SystemExit(0)
raise SystemExit(3)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(integration), str(gate.receipt_store), digest],
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )
    assert result.returncode == 0, result.stderr


def test_receipt_loader_fails_closed_without_no_follow_flag(setup_gate, monkeypatch):
    *_, gate = setup_gate
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    with pytest.raises(GitGateError, match="no-follow non-blocking open is unavailable"):
        gate.load_receipt("a" * 64, "b" * 40)


@pytest.mark.parametrize("kind", ["fifo", "symlink"])
def test_verify_rejects_existing_non_regular_receipt_without_blocking(setup_gate, kind):
    _, integration, _, _, gate = setup_gate
    candidate = submit(setup_gate)
    digest = valid_receipt_digest(gate, candidate)
    path = gate.receipt_store / f"{digest}.json"
    if kind == "fifo":
        os.mkfifo(path)
    else:
        target = gate.receipt_store / "outside-receipt.json"
        target.write_bytes(valid_receipt_bytes(gate, candidate))
        path.symlink_to(target)

    script = """
import sys
from pathlib import Path
from engineering.git_gate import Candidate, GitGate, GitGateError, VerificationPolicy
integration, receipts, submitted, base, tip, sha, checkout = sys.argv[1:]
policy = VerificationPolicy(
    ((\"python\", \"-m\", \"pytest\"),),
    ((\"PYTHONDONTWRITEBYTECODE\", \"1\"),),
    \"pilot\",
)
gate = GitGate(Path(integration), Path(integration), policy, Path(receipts))
candidate = Candidate(submitted, base, tip, sha, Path(checkout), (\"src/module.py\",))
try:
    gate.verify(candidate, lambda *_args: {\"status\": \"passed\"})
except GitGateError:
    raise SystemExit(0)
raise SystemExit(3)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(integration),
            str(gate.receipt_store),
            candidate.submitted_sha,
            candidate.base_sha,
            candidate.integration_tip,
            candidate.sha,
            str(candidate.checkout),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=2,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "tamper",
    [
        lambda body: body.update(commands=[]),
        lambda body: body.update(env=[["OTHER", "1"]]),
        lambda body: body["result"].update(status="failed"),
        lambda body: body["result"].update(extra=1),
        lambda body: body.update(extra="unexpected"),
    ],
    ids=["commands", "environment", "failed-result", "result-type", "extra-field"],
)
def test_self_consistent_but_invalid_receipt_cannot_publish(setup_gate, tamper):
    _, integration, _, _, gate = setup_gate
    candidate = submit(setup_gate)
    gate.verify(candidate, verifier)
    valid_path = next(gate.receipt_store.glob("*.json"))
    body = json.loads(valid_path.read_bytes())
    tamper(body)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    forged_digest = hashlib.sha256(canonical).hexdigest()
    (gate.receipt_store / f"{forged_digest}.json").write_bytes(canonical)

    with pytest.raises(GitGateError, match="receipt"):
        gate.publish(candidate, forged_digest, "0" * 40)
    result = subprocess.run(
        ["git", "-C", str(integration), "show-ref", "--verify", "--quiet", gate.ACTIVE_REF],
        check=False,
    )
    assert result.returncode == 1


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


def test_owner_lane_refs_are_instance_scoped_and_main_is_rejected(setup_gate):
    _, integration, worker, base, _ = setup_gate
    lanes = (
        "refs/heads/brain/paperclip-integration",
        "refs/heads/brain/development",
    )
    for ref in lanes:
        git(integration, "update-ref", ref, base)
    gates = [
        GitGate(
            integration,
            integration,
            VerificationPolicy((("python", "-c", "pass"),)),
            integration.parent / f"receipts-{index}",
            active_ref=ref,
        )
        for index, ref in enumerate(lanes)
    ]
    (worker / "src").mkdir()
    (worker / "src" / "module.py").write_text("value = 1\n")
    submitted = commit(worker, "shared lane submission")
    with pytest.raises(GitGateError, match="source active tip differs"):
        gates[0].submit(
            worker,
            submitted,
            base,
            ["src"],
            expected_source_tip="c" * 40,
            expected_integration_tip=base,
        )
    with pytest.raises(GitGateError, match="integration active tip differs"):
        gates[0].submit(
            worker,
            submitted,
            base,
            ["src"],
            expected_source_tip=base,
            expected_integration_tip="c" * 40,
        )
    candidates = [gate.submit(worker, submitted, base, ["src"]) for gate in gates]
    for gate, candidate, ref in zip(gates, candidates, lanes, strict=True):
        receipt = gate.verify(candidate, verifier)
        gate.publish(candidate, receipt.digest, base)
        assert git(integration, "rev-parse", ref) == candidate.sha
    assert git(integration, "rev-parse", "main") == base
    assert GitGate.ACTIVE_REF == "refs/heads/research/active"
    with pytest.raises(AttributeError):
        gates[0].active_ref = "refs/heads/main"
    with pytest.raises(AttributeError, match="immutable"):
        gates[0]._active_ref = "refs/heads/main"
    with pytest.raises(GitGateError, match="approved owner target"):
        GitGate(
            integration,
            integration,
            VerificationPolicy((("python", "-c", "pass"),)),
            integration.parent / "receipts-main",
            active_ref="refs/heads/main",
        )


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
