"""Small trusted broker for importing, verifying, and publishing worker Git commits.

Worker code is never imported here. All Git commands use fixed argument vectors and disable
system/global configuration and hooks. A caller supplies the verifier callback, which must run
the exact candidate checkout under its own sandbox policy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path


class GitGateError(RuntimeError):
    """A submitted commit or publication failed a broker invariant."""


@dataclass(frozen=True)
class VerificationPolicy:
    """Broker-owned immutable verifier inputs; never load this from a worker checkout."""

    commands: tuple[tuple[str, ...], ...]
    env: tuple[tuple[str, str], ...] = ()
    policy_id: str = "default"

    @property
    def digest(self) -> str:
        body = {"commands": self.commands, "env": self.env, "policy_id": self.policy_id}
        return _digest(_canonical(body))


@dataclass(frozen=True)
class Candidate:
    submitted_sha: str
    base_sha: str
    integration_tip: str
    sha: str
    checkout: Path
    changed_paths: tuple[str, ...]


@dataclass(frozen=True)
class Receipt:
    digest: str
    candidate_sha: str
    policy_hash: str
    commands: tuple[tuple[str, ...], ...]
    env: tuple[tuple[str, str], ...]
    result: Mapping[str, str]


Verifier = Callable[[Path, str, VerificationPolicy], Mapping[str, str]]

MAX_RECEIPT_BYTES = 256_000
OWNER_ACTIVE_REFS = frozenset(
    {"refs/heads/brain/paperclip-integration", "refs/heads/brain/development"}
)


class GitGate:
    """Broker worker commits into one canonical integration repository."""

    ACTIVE_REF = "refs/heads/research/active"
    SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_active_ref" and hasattr(self, "_active_ref"):
            raise AttributeError("GitGate active ref is immutable")
        super().__setattr__(name, value)

    def __init__(
        self,
        source_repo: Path,
        integration_repo: Path,
        policy: VerificationPolicy,
        receipt_store: Path,
        *,
        remote: str | None = None,
        operation_guard: Callable[[], object] | None = None,
        active_ref: str | None = None,
    ) -> None:
        self.source_repo = Path(source_repo).resolve()
        self.repo = Path(integration_repo).resolve()
        self.policy = policy
        self.receipt_store = Path(receipt_store).resolve()
        self.remote = remote
        self.operation_guard = operation_guard
        selected_ref = self.ACTIVE_REF if active_ref is None else active_ref
        if selected_ref != self.ACTIVE_REF and selected_ref not in OWNER_ACTIVE_REFS:
            raise GitGateError("active ref is not an approved owner target")
        self._active_ref = selected_ref
        if remote is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", remote):
            raise GitGateError("remote must be a broker-configured Git remote name")
        if not (self.repo / ".git").exists():
            raise GitGateError("integration_repo must be an initialized Git checkout")
        self.receipt_store.mkdir(parents=True, exist_ok=True)

    @property
    def active_ref(self) -> str:
        return self._active_ref

    def submit(
        self,
        checkout: Path,
        commit: str,
        expected_base: str,
        allowed_paths: Sequence[str],
        *,
        expected_source_tip: str | None = None,
        expected_integration_tip: str | None = None,
    ) -> Candidate:
        """Import and merge a worker commit; return an isolated detached candidate checkout."""
        submitted = self._resolve_sha(Path(checkout), commit)
        base = self._resolve_sha(Path(checkout), expected_base)
        if not self._is_ancestor(Path(checkout), base, submitted):
            raise GitGateError("expected base is not an ancestor of submitted commit")
        trusted_base = self._resolve_sha(self.source_repo, expected_base)
        # Planning advances on the broker-owned active ref even when the stable checkout HEAD
        # remains on main; a task may use only that active history (or initial HEAD fallback).
        source_ref_tip = self._ref_sha(self.source_repo, self.active_ref, missing_ok=True)
        source_tip = source_ref_tip or self._rev(self.source_repo, "HEAD")
        if expected_source_tip is not None and source_ref_tip != expected_source_tip:
            raise GitGateError("source active tip differs from the owner-admitted tip")
        if trusted_base != base or not self._is_ancestor(self.source_repo, base, source_tip):
            raise GitGateError("expected base is not in trusted source history")
        paths = self._changed_paths(Path(checkout), base, submitted)
        self._check_paths(Path(checkout), base, submitted, paths, allowed_paths)

        # Fetch only the explicit object id from the submitted local checkout. No worker remote,
        # config, hooks, or executable is consulted. A private ref keeps the imported object alive.
        imported_ref = f"refs/git-gate/import/{submitted}"
        self._run(
            self.repo,
            [
                "fetch",
                "--no-tags",
                "--no-write-fetch-head",
                str(Path(checkout).resolve()),
                f"{submitted}:{imported_ref}",
            ],
        )
        if self._rev(self.repo, imported_ref) != submitted:
            raise GitGateError("imported object id differs from submitted commit")

        integration_ref_tip = self._ref_sha(self.repo, self.active_ref, missing_ok=True)
        tip = integration_ref_tip or self._rev(self.repo, "HEAD")
        if expected_integration_tip is not None and integration_ref_tip != expected_integration_tip:
            raise GitGateError("integration active tip differs from the owner-admitted tip")
        if not self._is_ancestor(self.repo, base, tip):
            raise GitGateError("expected base is not present in canonical integration history")
        # `merge-tree --write-tree` computes a tree without checking out or running hooks.
        merge = self._run(self.repo, ["merge-tree", "--write-tree", tip, submitted], check=False)
        if merge.returncode:
            raise GitGateError("worker commit conflicts with current integration tip")
        tree = merge.stdout.decode("ascii", errors="strict").splitlines()[0].strip()
        candidate = (
            self._run(
                self.repo,
                [
                    "-c",
                    "user.name=GitGate",
                    "-c",
                    "user.email=git-gate@localhost",
                    "commit-tree",
                    tree,
                    "-p",
                    tip,
                    "-p",
                    submitted,
                ],
                input=f"GitGate candidate {submitted}\n".encode(),
            )
            .stdout.decode()
            .strip()
        )
        if not self.SHA_RE.fullmatch(candidate):
            raise GitGateError("Git returned an invalid candidate object id")

        tmp_parent = self.repo / ".git" / "git-gate-checkouts"
        tmp_parent.mkdir(parents=True, exist_ok=True)
        checkout_path = Path(tempfile.mkdtemp(prefix="candidate-", dir=tmp_parent))
        checkout_path.rmdir()
        self._run(self.repo, ["worktree", "add", "--detach", str(checkout_path), candidate])
        return Candidate(submitted, base, tip, candidate, checkout_path, paths)

    def is_detached(self) -> bool:
        result = self._run(self.repo, ["symbolic-ref", "--quiet", "HEAD"], check=False)
        if result.returncode not in (0, 1):
            raise GitGateError("publisher HEAD could not be inspected")
        return result.returncode == 1

    def verify(self, candidate: Candidate, runner: Verifier) -> Receipt:
        """Run a trusted verifier callback and persist a content-addressed immutable receipt."""
        if self._rev(candidate.checkout, "HEAD") != candidate.sha:
            raise GitGateError("candidate checkout no longer points at the submitted candidate")
        self._require_clean_checkout(candidate.checkout)
        result = runner(candidate.checkout, candidate.sha, self.policy)
        if self._rev(candidate.checkout, "HEAD") != candidate.sha:
            raise GitGateError("verifier changed candidate checkout HEAD")
        self._require_clean_checkout(candidate.checkout)
        if not isinstance(result, Mapping) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in result.items()
        ):
            raise GitGateError("verifier result must be a string-to-string mapping")
        if result.get("status") != "passed":
            raise GitGateError("verifier must return status='passed' to mint a receipt")
        body = {
            "candidate_sha": candidate.sha,
            "policy_hash": self.policy.digest,
            "commands": self.policy.commands,
            "env": self.policy.env,
            "result": dict(sorted(result.items())),
        }
        canonical = _canonical(body)
        if len(canonical) > MAX_RECEIPT_BYTES:
            raise GitGateError("receipt exceeds byte limit")
        digest = _digest(canonical)
        path = self.receipt_store / f"{digest}.json"
        try:
            _write_exclusive(path, canonical)
        except FileExistsError:
            self.load_receipt(digest, candidate.sha)
        return Receipt(
            digest,
            candidate.sha,
            self.policy.digest,
            self.policy.commands,
            self.policy.env,
            dict(result),
        )

    def publish(
        self,
        candidate: Candidate,
        receipt_hash: str,
        expected_tip: str,
        *,
        publish_remote: bool = False,
        expected_remote_tip: str | None = None,
        expected_remote_main: str | None = None,
    ) -> str:
        """CAS-publish a verified candidate to local active ref and optionally fixed remote."""
        guard = self.operation_guard() if self.operation_guard is not None else nullcontext()
        with guard:
            return self._publish_locked(
                candidate,
                receipt_hash,
                expected_tip,
                publish_remote=publish_remote,
                expected_remote_tip=expected_remote_tip,
                expected_remote_main=expected_remote_main,
            )

    def _publish_locked(
        self,
        candidate: Candidate,
        receipt_hash: str,
        expected_tip: str,
        *,
        publish_remote: bool,
        expected_remote_tip: str | None,
        expected_remote_main: str | None,
    ) -> str:
        self.load_receipt(receipt_hash, candidate.sha)
        self.validate_candidate(candidate)
        if publish_remote:
            self._check_remote(expected_remote_tip, expected_remote_main, candidate.sha)
        if expected_tip == "0" * 40 or expected_tip == "0" * 64:
            expected = expected_tip
        else:
            expected = self._resolve_sha(self.repo, expected_tip)
        current_active = self._ref_sha(self.repo, self.active_ref, missing_ok=True)
        if current_active is None:
            if expected != "0" * len(expected):
                raise GitGateError("active ref differs from caller's expected tip")
            current_tip = self._rev(self.repo, "HEAD")
        else:
            current_tip = current_active
        if current_active != candidate.sha and (current_active or "0" * len(expected)) != expected:
            raise GitGateError("active ref differs from caller's expected tip")
        # Candidate is a two-parent merge of current integration tip and submitted commit.
        if not self._is_ancestor(self.repo, candidate.integration_tip, candidate.sha):
            raise GitGateError("candidate does not descend from the captured integration tip")
        if current_active != candidate.sha and current_tip != candidate.integration_tip:
            raise GitGateError("integration active tip moved since candidate creation")
        if expected not in ("0" * 40, "0" * 64) and not self._is_ancestor(
            self.repo, expected, candidate.sha
        ):
            raise GitGateError("expected ref tip is not an ancestor of candidate")
        if current_active != candidate.sha:
            self._run(self.repo, ["update-ref", self.active_ref, candidate.sha, expected])
        if publish_remote:
            self._publish_remote(candidate.sha, expected_remote_tip, expected_remote_main)
        return candidate.sha

    def validate_candidate(self, candidate: Candidate) -> None:
        """Bind a candidate object to the exact integration tip and submitted commit."""
        sha = self._resolve_sha(self.repo, candidate.sha)
        integration_tip = self._resolve_sha(self.repo, candidate.integration_tip)
        submitted_sha = self._resolve_sha(self.repo, candidate.submitted_sha)
        result = self._run(self.repo, ["rev-list", "--parents", "-n", "1", sha])
        parents = result.stdout.decode("ascii", errors="strict").split()
        if parents != [sha, integration_tip, submitted_sha]:
            raise GitGateError(
                "candidate parents do not match its bound integration and submission"
            )

    def archive_active(self, tag: str) -> str:
        """Retain the current active target under a new immutable archive tag."""
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", tag) or tag.startswith("-"):
            raise GitGateError("invalid archive tag")
        ref = f"refs/tags/{tag}"
        if self._ref_sha(self.repo, ref, missing_ok=True):
            raise GitGateError("archive tag already exists")
        active = self._ref_sha(self.repo, self.active_ref)
        self._run(self.repo, ["update-ref", ref, active, "0" * len(active)])
        return active

    def _publish_remote(
        self, candidate: str, expected_tip: str | None, expected_main: str | None
    ) -> None:
        self._check_remote(expected_tip, expected_main, candidate)
        active, _ = self._remote_refs()
        if active == candidate:
            return
        self._run(
            self.repo,
            ["push", self.remote, f"{candidate}:{self.active_ref.removeprefix('refs/heads/')}"],
        )
        active_after, main_after = self._remote_refs()
        if active_after != candidate or main_after != expected_main:
            raise GitGateError("remote refs changed during publication")

    def _check_remote(
        self, expected_tip: str | None, expected_main: str | None, candidate: str
    ) -> None:
        if not self.remote:
            raise GitGateError("remote publication is disabled")
        if not expected_tip or not expected_main:
            raise GitGateError("remote publication requires expected research/active and main SHAs")
        active, main = self._remote_refs()
        if main != expected_main:
            raise GitGateError("remote tip or main changed before publication")
        if active not in (expected_tip, candidate):
            raise GitGateError("remote tip or main changed before publication")

    def _remote_refs(self) -> tuple[str | None, str | None]:
        output = self._run(
            self.repo, ["ls-remote", self.remote or "", self.active_ref, "refs/heads/main"]
        )
        refs = {}
        for line in output.stdout.decode().splitlines():
            sha, name = line.split("\t", 1)
            refs[name] = sha
        return refs.get(self.active_ref), refs.get("refs/heads/main")

    def load_receipt(self, receipt_hash: str, candidate_sha: str) -> dict:
        """Load and strictly validate the content-addressed verifier receipt."""
        if type(receipt_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", receipt_hash):
            raise GitGateError("invalid receipt hash")
        path = self.receipt_store / f"{receipt_hash}.json"
        no_follow = getattr(os, "O_NOFOLLOW", None)
        non_blocking = getattr(os, "O_NONBLOCK", None)
        if no_follow is None or non_blocking is None:
            raise GitGateError("receipt no-follow non-blocking open is unavailable")
        try:
            descriptor = os.open(path, os.O_RDONLY | non_blocking | no_follow)
            with os.fdopen(descriptor, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise GitGateError("verified receipt is not a regular file")
                raw = stream.read(MAX_RECEIPT_BYTES + 1)
            if len(raw) > MAX_RECEIPT_BYTES:
                raise GitGateError("verified receipt exceeds byte limit")
            body = json.loads(raw)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GitGateError("verified receipt is missing or unreadable") from exc
        try:
            canonical = _canonical(body)
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise GitGateError("verified receipt schema is invalid") from None
        if _digest(canonical) != receipt_hash or raw != canonical:
            raise GitGateError("verified receipt was altered")
        required = {"candidate_sha", "policy_hash", "commands", "env", "result"}
        if type(body) is not dict or set(body) != required:
            raise GitGateError("verified receipt schema is invalid")
        commands = [list(command) for command in self.policy.commands]
        environment = [list(item) for item in self.policy.env]
        result = body["result"]
        if (
            type(body["candidate_sha"]) is not str
            or not self.SHA_RE.fullmatch(body["candidate_sha"])
            or body["candidate_sha"] != candidate_sha
            or type(body["policy_hash"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", body["policy_hash"])
            or body["policy_hash"] != self.policy.digest
            or body["commands"] != commands
            or body["env"] != environment
            or type(result) is not dict
            or any(type(key) is not str or type(value) is not str for key, value in result.items())
            or result.get("status") != "passed"
        ):
            raise GitGateError("receipt does not verify this candidate under the current policy")
        return body

    def _check_paths(
        self, repo: Path, base: str, commit: str, paths: tuple[str, ...], allowed: Sequence[str]
    ) -> None:
        roots = tuple(_safe_path(p) for p in allowed)
        if not roots:
            raise GitGateError("allowed_paths must be non-empty")
        for name in paths:
            if (
                name
                in {"AGENTS.md", ".github", "engineering", "policy", "policies", ".gitattributes"}
                or name.endswith("/AGENTS.md")
                or name.endswith("/.gitattributes")
                or name.startswith((".github/", "engineering/", "policy/", "policies/"))
            ):
                raise GitGateError(f"protected path changed: {name}")
            if name in {"AGENTS.md", ".gitmodules"} or name.startswith(".git/"):
                raise GitGateError(f"protected path changed: {name}")
            if not any(name == root or name.startswith(root.rstrip("/") + "/") for root in roots):
                raise GitGateError(f"path outside allowed scope: {name}")
        for rev in (base, commit):
            modes = self._tree_modes(repo, rev)
            for name in paths:
                mode = modes.get(name)
                if mode in {"120000", "160000"}:
                    raise GitGateError(f"symlink or submodule path rejected: {name}")

    def _changed_paths(self, repo: Path, base: str, commit: str) -> tuple[str, ...]:
        output = self._run(
            repo,
            [
                "--no-pager",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-status",
                "-z",
                "--find-renames",
                base,
                commit,
            ],
        )
        fields = output.stdout.decode("utf-8", errors="surrogateescape").split("\0")
        fields = [item for item in fields if item]
        changed: set[str] = set()
        i = 0
        while i < len(fields):
            status = fields[i]
            i += 1
            count = 2 if status.startswith(("R", "C")) else 1
            if i + count > len(fields):
                raise GitGateError("unable to parse changed path list")
            changed.update(fields[i : i + count])
            i += count
        return tuple(sorted(changed))

    def _tree_modes(self, repo: Path, rev: str) -> dict[str, str]:
        output = self._run(repo, ["ls-tree", "-r", "-z", rev])
        result: dict[str, str] = {}
        for record in output.stdout.split(b"\0"):
            if record:
                meta, name = record.split(b"\t", 1)
                mode = meta.split(b" ", 1)[0].decode("ascii")
                result[name.decode("utf-8", errors="surrogateescape")] = mode
        return result

    def _resolve_sha(self, repo: Path, rev: str) -> str:
        if not isinstance(rev, str) or not self.SHA_RE.fullmatch(rev):
            raise GitGateError("only full object ids are accepted")
        sha = self._rev(repo, rev)
        if sha != rev:
            raise GitGateError("object id did not resolve exactly")
        return sha

    def _rev(self, repo: Path, rev: str) -> str:
        result = self._run(repo, ["rev-parse", "--verify", f"{rev}^{{commit}}"], check=False)
        if result.returncode:
            raise GitGateError(f"unknown commit or ref: {rev}")
        return result.stdout.decode().strip()

    def _ref_sha(self, repo: Path, ref: str, *, missing_ok: bool = False) -> str | None:
        result = self._run(repo, ["rev-parse", "--verify", ref], check=False)
        if result.returncode:
            if missing_ok:
                return None
            raise GitGateError(f"missing ref: {ref}")
        return result.stdout.decode().strip()

    def _is_ancestor(self, repo: Path, older: str, newer: str) -> bool:
        return (
            self._run(repo, ["merge-base", "--is-ancestor", older, newer], check=False).returncode
            == 0
        )

    def _require_clean_checkout(self, checkout: Path) -> None:
        status = self._run(checkout, ["status", "--porcelain", "-z", "--untracked-files=all"])
        if status.stdout:
            raise GitGateError("candidate checkout has modifications or untracked files")

    def _run(
        self,
        repo: Path,
        args: Sequence[str],
        *,
        input: bytes | None = None,
        check: bool = True,
        env_overrides: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
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
        if env_overrides is not None:
            if set(env_overrides) - {"GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"} or any(
                type(value) is not str or "\x00" in value for value in env_overrides.values()
            ):
                raise GitGateError("Git environment override is not permitted")
            env.update(env_overrides)
        command = [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-C",
            str(repo),
            *args,
        ]
        result = subprocess.run(
            command,
            input=input,
            capture_output=True,
            env=env,
            check=False,
        )
        if check and result.returncode:
            raise GitGateError(
                result.stderr.decode("utf-8", errors="replace").strip()
                or f"git command failed: {args[0]}"
            )
        return result


def _safe_path(value: str) -> str:
    if not value or value.startswith("/") or "\\" in value:
        raise GitGateError(f"invalid allowed path: {value!r}")
    parts = value.rstrip("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise GitGateError(f"invalid allowed path: {value!r}")
    return "/".join(parts)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_exclusive(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
