"""Single-operation, local Paperclip to Brain GitGate bridge.

The caller supplies already admitted task bindings, a separately obtained reviewer
approval, and the trusted verifier callback. This module owns no scheduler, worker,
reviewer transport, or lifecycle state.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from engineering.git_gate import OWNER_ACTIVE_REFS, OWNER_CAS_REFS, Candidate, GitGate, GitGateError, Verifier

ACTIVE_REF = "refs/heads/brain/paperclip-integration"
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_JOURNAL_BYTES = 256_000
COMPANY_FENCE_SCHEMA = "paperclip-company-fence-v1"
_COMPANY_LOCKS: dict[str, threading.RLock] = {}
_COMPANY_LOCKS_GUARD = threading.Lock()


class BrokerError(RuntimeError):
    """A binding, approval, journal, or operation-state invariant failed."""


@dataclass(frozen=True)
class OperationBinding:
    company_id: str
    issue_id: str
    run_id: str
    worker_agent_id: str
    reviewer_agent_id: str
    base_sha: str
    submitted_sha: str
    scope: tuple[str, ...]
    policy_digest: str
    active_ref: str = ACTIVE_REF
    owner_config_digest: str = "0" * 64
    owner_roots: tuple[str, ...] = ()

    def validate(self, gate: GitGate) -> None:
        for value in (
            self.company_id,
            self.issue_id,
            self.run_id,
            self.worker_agent_id,
            self.reviewer_agent_id,
        ):
            if type(value) is not str or not IDENTIFIER_RE.fullmatch(value):
                raise BrokerError("binding contains an invalid identity")
        if self.worker_agent_id == self.reviewer_agent_id:
            raise BrokerError("worker and reviewer identities must be distinct")
        if not isinstance(self.base_sha, str) or not SHA_RE.fullmatch(self.base_sha):
            raise BrokerError("binding base SHA is invalid")
        if not isinstance(self.submitted_sha, str) or not SHA_RE.fullmatch(self.submitted_sha):
            raise BrokerError("binding submitted SHA is invalid")
        if not isinstance(self.policy_digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.policy_digest
        ):
            raise BrokerError("binding policy digest is invalid")
        if self.policy_digest != gate.policy.digest:
            raise BrokerError("binding policy digest differs from the configured verifier")
        if self.active_ref not in OWNER_ACTIVE_REFS or self.active_ref != gate.active_ref:
            raise BrokerError("binding active ref differs from the configured owner target")
        if type(self.owner_config_digest) is not str or not re.fullmatch(
            r"[0-9a-f]{64}", self.owner_config_digest
        ):
            raise BrokerError("binding owner config digest is invalid")
        if type(self.owner_roots) is not tuple or any(
            type(root) is not str or not Path(root).is_absolute() for root in self.owner_roots
        ):
            raise BrokerError("binding owner roots are invalid")
        if self.active_ref in OWNER_CAS_REFS and (
            not self.owner_roots or self.owner_config_digest == "0" * 64
        ):
            raise BrokerError("development lane requires owner config and roots")
        required_roots = {
            str(gate.source_repo),
            str(gate.repo),
            str(gate.receipt_store),
        }
        if self.owner_roots and not required_roots.issubset(self.owner_roots):
            raise BrokerError("binding owner roots differ from configured GitGate roots")
        if type(self.scope) is not tuple or not self.scope:
            raise BrokerError("binding scope must be a non-empty tuple")
        for path in self.scope:
            if type(path) is not str:
                raise BrokerError("binding scope contains an invalid path")

    def as_dict(self) -> dict:
        return {
            "company_id": self.company_id,
            "issue_id": self.issue_id,
            "run_id": self.run_id,
            "worker_agent_id": self.worker_agent_id,
            "reviewer_agent_id": self.reviewer_agent_id,
            "base_sha": self.base_sha,
            "submitted_sha": self.submitted_sha,
            "scope": list(self.scope),
            "policy_digest": self.policy_digest,
            "active_ref": self.active_ref,
            "owner_config_digest": self.owner_config_digest,
            "owner_roots": list(self.owner_roots),
        }

    @property
    def operation_key(self) -> str:
        # This stable logical key lets the journal detect substituted bindings.
        value = _canonical([self.company_id, self.issue_id, self.run_id])
        return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class ReviewerApproval:
    company_id: str
    run_id: str
    candidate_sha: str
    policy_digest: str
    reviewer_agent_id: str
    reviewer_run_id: str
    evidence_digest: str
    approved: bool


@dataclass(frozen=True)
class IntegrationResult:
    operation_key: str
    candidate_sha: str
    receipt_digest: str
    status: str


@dataclass(frozen=True)
class RejectionResult:
    operation_key: str
    candidate_sha: str
    evidence_digest: str
    status: str


@contextmanager
def company_operation(journal_dir: Path, company_id: str) -> Iterator[None]:
    """Serialize handoff and broker effects for one company across threads and processes."""
    if type(company_id) is not str or not IDENTIFIER_RE.fullmatch(company_id):
        raise BrokerError("company identity is invalid")
    supplied_directory = Path(journal_dir)
    if supplied_directory.is_symlink() or not supplied_directory.is_dir():
        raise BrokerError("company journal directory is missing or invalid")
    directory = supplied_directory.resolve()
    digest = hashlib.sha256(f"{directory}\0{company_id}".encode()).hexdigest()
    with _COMPANY_LOCKS_GUARD:
        local_lock = _COMPANY_LOCKS.setdefault(digest, threading.RLock())
    with local_lock:
        path = directory / f"company-{digest}.lock"
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise BrokerError("company lock no-follow open is unavailable")
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | no_follow, 0o600)
        except OSError as exc:
            raise BrokerError("company lock could not be opened") from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise BrokerError("company lock is not a regular file")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def _company_fence_path(journal_dir: Path, company_id: str) -> Path:
    digest = hashlib.sha256(company_id.encode()).hexdigest()
    return Path(journal_dir) / f"company-{digest}.json"


def read_company_fence(journal_dir: Path, company_id: str) -> dict | None:
    path = _company_fence_path(journal_dir, company_id)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BrokerError("company fence cannot be inspected") from exc
    if not stat.S_ISREG(info.st_mode):
        raise BrokerError("company fence is not a regular file")
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise BrokerError("company fence no-follow open is unavailable")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | no_follow)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise BrokerError("company fence is not a regular file")
            raw = stream.read(MAX_JOURNAL_BYTES + 1)
        if len(raw) > MAX_JOURNAL_BYTES:
            raise BrokerError("company fence exceeds byte limit")
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BrokerError("company fence is missing or unreadable") from exc
    if (
        type(value) is not dict
        or value.get("schema") != COMPANY_FENCE_SCHEMA
        or value.get("company_id") != company_id
        or value.get("state") not in {"draining", "resume_intent", "open"}
    ):
        raise BrokerError("company fence schema or identity is invalid")
    return value


def _write_company_fence(journal_dir: Path, value: dict) -> None:
    directory = Path(journal_dir).resolve()
    data = _canonical(value)
    if len(data) > MAX_JOURNAL_BYTES:
        raise BrokerError("company fence exceeds byte limit")
    fd, temp_name = tempfile.mkstemp(prefix=".paperclip-company-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, _company_fence_path(directory, value["company_id"]))
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def begin_company_drain(journal_dir: Path, company_id: str, snapshot_digest: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot_digest):
        raise BrokerError("drain reconciliation digest is invalid")
    current = read_company_fence(journal_dir, company_id)
    if current is not None and current["state"] == "draining":
        return current
    if current is not None and current["state"] == "resume_intent":
        raise BrokerError("prior founder resume has an unresolved API outcome")
    value = {
        "schema": COMPANY_FENCE_SCHEMA,
        "company_id": company_id,
        "state": "draining",
        "started_at_ns": time.time_ns(),
        "reconciled_snapshot_sha256": snapshot_digest,
    }
    _write_company_fence(journal_dir, value)
    return value


def require_company_open(journal_dir: Path, company_id: str) -> None:
    current = read_company_fence(journal_dir, company_id)
    if current is not None and current["state"] != "open":
        raise BrokerError("company is durably draining; registration/publication is fenced")


def record_company_resume_intent(
    journal_dir: Path, company_id: str, founder_command_id: str, snapshot_digest: str
) -> dict:
    if not IDENTIFIER_RE.fullmatch(founder_command_id) or not re.fullmatch(
        r"[0-9a-f]{64}", snapshot_digest
    ):
        raise BrokerError("founder resume identity or reconciliation digest is invalid")
    current = read_company_fence(journal_dir, company_id)
    if current is None or current["state"] != "draining":
        raise BrokerError("company is not durably draining")
    value = {
        "schema": COMPANY_FENCE_SCHEMA,
        "company_id": company_id,
        "state": "resume_intent",
        "founder_command_id": founder_command_id,
        "reconciled_snapshot_sha256": snapshot_digest,
        "intent_at_ns": time.time_ns(),
    }
    _write_company_fence(journal_dir, value)
    return value


def clear_company_drain(
    journal_dir: Path,
    company_id: str,
    founder_command_id: str,
    snapshot_digest: str,
    *,
    resume_confirmation_digest: str | None = None,
) -> None:
    if not IDENTIFIER_RE.fullmatch(founder_command_id) or not re.fullmatch(
        r"[0-9a-f]{64}", snapshot_digest
    ):
        raise BrokerError("founder resume identity or reconciliation digest is invalid")
    current = read_company_fence(journal_dir, company_id)
    if (
        current is None
        or current["state"] != "resume_intent"
        or current.get("founder_command_id") != founder_command_id
        or current.get("reconciled_snapshot_sha256") != snapshot_digest
        or (
            resume_confirmation_digest is not None
            and not re.fullmatch(r"[0-9a-f]{64}", resume_confirmation_digest)
        )
    ):
        raise BrokerError("company has no matching durable resume intent")
    _write_company_fence(
        journal_dir,
        {
            "schema": COMPANY_FENCE_SCHEMA,
            "company_id": company_id,
            "state": "open",
            "resumed_at_ns": time.time_ns(),
            "founder_command_id": founder_command_id,
            "reconciled_snapshot_sha256": snapshot_digest,
            "resume_confirmation_sha256": resume_confirmation_digest or snapshot_digest,
        },
    )


def safe_git(repo: Path, args: tuple[str, ...]) -> subprocess.CompletedProcess[bytes]:
    """Read-only fixed-argument Git helper with config, hooks, and inherited Git env disabled."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
        }
    )
    return subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        env=env,
        check=True,
        timeout=3,
    )


class PaperclipGitBroker:
    """One-shot task candidate broker with durable intent and unknown-outcome holds."""

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_active_ref" and hasattr(self, "_active_ref"):
            raise AttributeError("PaperclipGitBroker active ref is immutable")
        super().__setattr__(name, value)

    def __init__(
        self, gate: GitGate, journal_dir: Path, *, active_ref: str | None = None
    ) -> None:
        if gate.remote is not None:
            raise BrokerError("Paperclip broker requires local-only GitGate publication")
        target_ref = active_ref or (
            gate.active_ref if gate.active_ref in OWNER_ACTIVE_REFS else ACTIVE_REF
        )
        if target_ref not in OWNER_ACTIVE_REFS:
            raise BrokerError("broker active ref is not an approved owner target")
        self.gate = gate if gate.active_ref == target_ref else GitGate(
            gate.source_repo,
            gate.repo,
            gate.policy,
            gate.receipt_store,
            remote=None,
            operation_guard=gate.operation_guard,
            active_ref=target_ref,
        )
        self._active_ref = target_ref
        self.journal_dir = Path(journal_dir).resolve()
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @property
    def active_ref(self) -> str:
        return self._active_ref

    def _validate_binding(self, binding: OperationBinding) -> None:
        binding.validate(self.gate)
        if binding.owner_roots and str(self.journal_dir) not in binding.owner_roots:
            raise BrokerError("binding owner roots differ from configured journal root")
        if self.active_ref in OWNER_CAS_REFS and (
            not binding.owner_roots or binding.owner_config_digest == "0" * 64
        ):
            raise BrokerError("development lane requires owner config and roots")

    def prepare(self, binding: OperationBinding, worker_checkout: Path) -> Candidate:
        """Record intent, then import and merge one submitted commit into a detached candidate."""
        self._validate_binding(binding)
        with self._lock, company_operation(self.journal_dir, binding.company_id):
            require_company_open(self.journal_dir, binding.company_id)
            return self._prepare_locked(binding, worker_checkout)

    def recover_candidate(self, binding: OperationBinding) -> tuple[dict, Candidate]:
        """Load a custodied candidate and reconcile a published ref without rerunning work."""
        self._validate_binding(binding)
        with self._lock, company_operation(self.journal_dir, binding.company_id):
            require_company_open(self.journal_dir, binding.company_id)
            with self._operation_lock(binding):
                path = self._journal_path(binding)
                record = self._read(path)
                if record is None:
                    raise BrokerError("operation has no prepared candidate")
                self._check_binding(record, binding)
                state = record.get("state")
                if state not in {"prepared", "publish_intent", "published"}:
                    raise BrokerError(f"candidate recovery is held in state {state}")
                candidate = self._candidate(record)
                self._check_candidate(record, candidate)
                if state in {"publish_intent", "published"}:
                    self._validate_receipt(record, candidate)
                    if self._active_tip() != candidate.sha:
                        raise BrokerError("published candidate ref differs; recovery is held")
                    if state == "publish_intent":
                        record["state"] = "published"
                        record["publish_ack"] = "reconciled_exact_tip"
                        self._write(path, record)
                return record, candidate

    def reject(
        self,
        binding: OperationBinding,
        candidate: Candidate,
        approval: ReviewerApproval,
    ) -> RejectionResult:
        """Durably terminalize an exact reviewer rejection without verification or publication."""
        self._validate_binding(binding)
        if not isinstance(approval, ReviewerApproval) or approval.approved is not False:
            raise BrokerError("review rejection must contain an explicit declined approval")
        with self._lock, company_operation(self.journal_dir, binding.company_id):
            require_company_open(self.journal_dir, binding.company_id)
            with self._operation_lock(binding):
                path = self._journal_path(binding)
                record = self._read(path)
                if record is None:
                    raise BrokerError("operation has no prepared candidate")
                self._check_binding(record, binding)
                self._check_candidate(record, candidate)
                self._check_approval(binding, candidate, approval, approved=False)
                approval_data = self._approval_dict(approval)
                state = record.get("state")
                if state == "rejected":
                    if record.get("approval") != approval_data:
                        raise BrokerError("review rejection differs from the recorded evidence")
                    return self._rejection_result(record)
                if state != "prepared":
                    raise BrokerError(f"review rejection is held in state {state}")
                record["approval"] = approval_data
                record["state"] = "rejected"
                record["rejected_at_ns"] = time.time_ns()
                self._write(path, record)
                return self._rejection_result(record)

    def _prepare_locked(self, binding: OperationBinding, worker_checkout: Path) -> Candidate:
        with self._operation_lock(binding):
            path = self._journal_path(binding)
            record = self._read(path)
            if record is not None:
                self._check_binding(record, binding)
                if record["state"] == "prepared":
                    candidate = self._candidate(record)
                    self._check_candidate(record, candidate)
                    return candidate
                raise BrokerError(f"operation is held in state {record['state']}")

            expected_tip = self.gate._ref_sha(
                self.gate.repo, self.active_ref, missing_ok=True
            )
            record = {
                "schema": "paperclip-git-operation-v1",
                "operation_key": binding.operation_key,
                "binding": binding.as_dict(),
                "state": "preparing",
                "expected_tip": expected_tip or "0" * len(binding.base_sha),
            }
            self._create(path, record)
            try:
                if self.active_ref in OWNER_CAS_REFS and not (
                    self.gate.is_detached()
                ):
                    raise GitGateError("development publisher HEAD must be detached")
                candidate = self.gate.submit(
                    Path(worker_checkout),
                    binding.submitted_sha,
                    binding.base_sha,
                    binding.scope,
                    expected_source_tip=(
                        binding.base_sha
                        if self.active_ref in OWNER_CAS_REFS
                        else None
                    ),
                    expected_integration_tip=(
                        binding.base_sha
                        if self.active_ref in OWNER_CAS_REFS
                        else None
                    ),
                )
            except BaseException as exc:
                self._mark_held(path, "preparing", exc)
                raise BrokerError(
                    "candidate preparation outcome is held for reconciliation"
                ) from exc

            record.update(
                state="prepared",
                candidate={
                    "submitted_sha": candidate.submitted_sha,
                    "base_sha": candidate.base_sha,
                    "integration_tip": candidate.integration_tip,
                    "sha": candidate.sha,
                    "checkout": str(candidate.checkout),
                    "changed_paths": list(candidate.changed_paths),
                },
            )
            self._write(path, record)
            return candidate

    def integrate(
        self,
        binding: OperationBinding,
        candidate: Candidate,
        approval: ReviewerApproval,
        verifier: Verifier,
    ) -> IntegrationResult:
        """Check exact independent approval, verify once, then locally CAS-publish once."""
        self._validate_binding(binding)
        with self._lock, company_operation(self.journal_dir, binding.company_id):
            require_company_open(self.journal_dir, binding.company_id)
            return self._integrate_locked(binding, candidate, approval, verifier)

    def _integrate_locked(
        self,
        binding: OperationBinding,
        candidate: Candidate,
        approval: ReviewerApproval,
        verifier: Verifier,
    ) -> IntegrationResult:
        with self._operation_lock(binding):
            path = self._journal_path(binding)
            record = self._read(path)
            if record is None:
                raise BrokerError("operation has no prepared candidate")
            self._check_binding(record, binding)
            self._check_candidate(record, candidate)
            self._check_approval(binding, candidate, approval)
            state = record["state"]
            if state != "prepared":
                if record.get("approval") != self._approval_dict(approval):
                    raise BrokerError("reviewer approval differs from the recorded operation")
            if state == "published":
                self._validate_receipt(record, candidate)
                self._reconcile_published(candidate.sha)
                return self._result(record)
            if state != "prepared":
                if state == "publish_intent":
                    self._validate_receipt(record, candidate)
                if state == "publish_intent" and self._active_tip() == candidate.sha:
                    record["state"] = "published"
                    self._write(path, record)
                    return self._result(record)
                raise BrokerError(f"operation is held in state {state}")

            record["approval"] = self._approval_dict(approval)
            record["state"] = "verification_intent"
            self._write(path, record)
            try:
                receipt = self.gate.verify(candidate, verifier)
            except BaseException as exc:
                self._mark_held(path, "verification_intent", exc)
                raise BrokerError(
                    "verification outcome is held; verifier will not be retried"
                ) from exc

            record["receipt_digest"] = receipt.digest
            record["state"] = "publish_intent"
            self._write(path, record)
            try:
                self.gate.publish(
                    candidate,
                    receipt.digest,
                    record["expected_tip"],
                    publish_remote=False,
                )
            except BaseException as exc:
                # A lost acknowledgement is settled only by exact local ref readback.
                if self._active_tip() == candidate.sha:
                    try:
                        self._check_candidate(record, candidate)
                        self._validate_receipt(record, candidate)
                    except BrokerError as custody_error:
                        self._mark_held(path, "publish_intent", exc)
                        raise BrokerError(
                            "publication tip lacks valid candidate or receipt custody"
                        ) from custody_error
                    record["state"] = "published"
                    record["publish_ack"] = "reconciled_exact_tip"
                    self._write(path, record)
                    return self._result(record)
                self._mark_held(path, "publish_intent", exc)
                raise BrokerError("publication outcome is held for reconciliation") from exc

            record["state"] = "published"
            record["publish_ack"] = "confirmed"
            self._write(path, record)
            return self._result(record)

    def _check_approval(
        self,
        binding: OperationBinding,
        candidate: Candidate,
        approval: ReviewerApproval,
        *,
        approved: bool = True,
    ) -> None:
        if not isinstance(approval, ReviewerApproval):
            raise BrokerError("reviewer approval has an invalid type")
        if approval.reviewer_agent_id == binding.worker_agent_id:
            raise BrokerError("reviewer identity must differ from the worker")
        if (
            approval.company_id != binding.company_id
            or approval.run_id != binding.run_id
            or approval.candidate_sha != candidate.sha
            or approval.policy_digest != binding.policy_digest
            or approval.reviewer_agent_id != binding.reviewer_agent_id
            or type(approval.reviewer_run_id) is not str
            or not IDENTIFIER_RE.fullmatch(approval.reviewer_run_id)
            or type(approval.evidence_digest) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", approval.evidence_digest)
            or approval.approved is not approved
        ):
            raise BrokerError("reviewer approval does not bind this exact operation")

    @staticmethod
    def _approval_dict(approval: ReviewerApproval) -> dict:
        return {
            "company_id": approval.company_id,
            "run_id": approval.run_id,
            "candidate_sha": approval.candidate_sha,
            "policy_digest": approval.policy_digest,
            "reviewer_agent_id": approval.reviewer_agent_id,
            "reviewer_run_id": approval.reviewer_run_id,
            "evidence_digest": approval.evidence_digest,
            "approved": approval.approved,
        }

    def _journal_path(self, binding: OperationBinding) -> Path:
        return self.journal_dir / f"{binding.operation_key}.json"

    @contextmanager
    def _operation_lock(self, binding: OperationBinding) -> Iterator[None]:
        """Serialize journal reads, Git effects, and updates across broker processes."""
        path = self.journal_dir / f"{binding.operation_key}.lock"
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise BrokerError("operation lock no-follow open is unavailable")
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | no_follow, 0o600)
        except OSError as exc:
            raise BrokerError("operation lock could not be opened") from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise BrokerError("operation lock is not a regular file")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _read(self, path: Path) -> dict | None:
        if not path.exists():
            return None
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise BrokerError("journal no-follow open is unavailable")
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | no_follow)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise BrokerError("operation journal is not a regular file")
                raw = stream.read(MAX_JOURNAL_BYTES + 1)
            if len(raw) > MAX_JOURNAL_BYTES:
                raise BrokerError("operation journal exceeds byte limit")
            value = json.loads(raw)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BrokerError("operation journal is missing or unreadable") from exc
        if type(value) is not dict or value.get("schema") != "paperclip-git-operation-v1":
            raise BrokerError("operation journal schema is invalid")
        return value

    def _write(self, path: Path, value: dict) -> None:
        data = _canonical(value)
        if len(data) > MAX_JOURNAL_BYTES:
            raise BrokerError("operation journal exceeds byte limit")
        fd, temp_name = tempfile.mkstemp(prefix=".paperclip-git-", dir=self.journal_dir)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
            directory_fd = os.open(self.journal_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def _create(self, path: Path, value: dict) -> None:
        """Claim a logical operation key atomically before its first Git effect."""
        data = _canonical(value)
        if len(data) > MAX_JOURNAL_BYTES:
            raise BrokerError("operation journal exceeds byte limit")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise BrokerError("operation key was concurrently claimed") from exc
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            directory_fd = os.open(self.journal_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            # Preserve a partial intent as a fail-closed custody record.
            raise

    def _check_binding(self, record: dict, binding: OperationBinding) -> None:
        if (
            record.get("operation_key") != binding.operation_key
            or record.get("binding") != binding.as_dict()
        ):
            raise BrokerError("operation key was reused with substituted binding identity")

    def _candidate(self, record: dict) -> Candidate:
        value = record["candidate"]
        return Candidate(
            value["submitted_sha"],
            value["base_sha"],
            value["integration_tip"],
            value["sha"],
            Path(value["checkout"]),
            tuple(value["changed_paths"]),
        )

    def _check_candidate(self, record: dict, candidate: Candidate) -> None:
        if record.get("candidate") != {
            "submitted_sha": candidate.submitted_sha,
            "base_sha": candidate.base_sha,
            "integration_tip": candidate.integration_tip,
            "sha": candidate.sha,
            "checkout": str(candidate.checkout),
            "changed_paths": list(candidate.changed_paths),
        }:
            raise BrokerError("candidate differs from the prepared operation")
        try:
            self.gate.validate_candidate(candidate)
        except GitGateError as exc:
            raise BrokerError("prepared candidate identity is invalid") from exc

    def _validate_receipt(self, record: dict, candidate: Candidate) -> None:
        try:
            self.gate.load_receipt(record.get("receipt_digest"), candidate.sha)
        except (GitGateError, TypeError) as exc:
            raise BrokerError(
                "journal receipt no longer verifies this candidate and policy"
            ) from exc

    def _active_tip(self) -> str | None:
        return self.gate._ref_sha(self.gate.repo, self.active_ref, missing_ok=True)

    def _reconcile_published(self, candidate_sha: str) -> None:
        if self._active_tip() != candidate_sha:
            raise BrokerError("journal says published but development ref differs")

    def _result(self, record: dict) -> IntegrationResult:
        return IntegrationResult(
            record["operation_key"],
            record["candidate"]["sha"],
            record["receipt_digest"],
            record["state"],
        )

    def _rejection_result(self, record: dict) -> RejectionResult:
        return RejectionResult(
            record["operation_key"],
            record["candidate"]["sha"],
            record["approval"]["evidence_digest"],
            record["state"],
        )

    def _mark_held(self, path: Path, prior_state: str, error: BaseException) -> None:
        record = self._read(path)
        if record is not None and record.get("state") == prior_state:
            record["state"] = "held_unknown"
            record["last_error_type"] = type(error).__name__[:80]
            self._write(path, record)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
