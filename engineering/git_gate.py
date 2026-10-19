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
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
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


class GitGate:
    """Broker worker commits into one canonical integration repository."""

    ACTIVE_REF = "refs/heads/research/active"
    SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")

    def __init__(
        self,
        source_repo: Path,
        integration_repo: Path,
        policy: VerificationPolicy,
        receipt_store: Path,
        *,
        remote: str | None = None,
    ) -> None:
        self.source_repo = Path(source_repo).resolve()
        self.repo = Path(integration_repo).resolve()
        self.policy = policy
        self.receipt_store = Path(receipt_store).resolve()
        self.remote = remote
        if remote is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", remote):
            raise GitGateError("remote must be a broker-configured Git remote name")
        if not (self.repo / ".git").exists():
            raise GitGateError("integration_repo must be an initialized Git checkout")
        self.receipt_store.mkdir(parents=True, exist_ok=True)

    def submit(
        self,
        checkout: Path,
        commit: str,
        expected_base: str,
        allowed_paths: Sequence[str],
    ) -> Candidate:
        """Import and merge a worker commit; return an isolated detached candidate checkout."""
        submitted = self._resolve_sha(Path(checkout), commit)
        base = self._resolve_sha(Path(checkout), expected_base)
        if not self._is_ancestor(Path(checkout), base, submitted):
            raise GitGateError("expected base is not an ancestor of submitted commit")
        trusted_base = self._resolve_sha(self.source_repo, expected_base)
        source_tip = self._rev(self.source_repo, "HEAD")
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

        tip = self._ref_sha(self.repo, self.ACTIVE_REF, missing_ok=True) or self._rev(
            self.repo, "HEAD"
        )
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
        digest = _digest(_canonical(body))
        path = self.receipt_store / f"{digest}.json"
        if path.exists() and path.read_bytes() != _canonical(body):
            raise GitGateError("receipt digest collision or tampered receipt store")
        if not path.exists():
            _write_exclusive(path, _canonical(body))
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
        self._require_receipt(receipt_hash, candidate.sha)
        if publish_remote:
            self._check_remote(expected_remote_tip, expected_remote_main, candidate.sha)
        if expected_tip == "0" * 40 or expected_tip == "0" * 64:
            expected = expected_tip
        else:
            expected = self._resolve_sha(self.repo, expected_tip)
        current_active = self._ref_sha(self.repo, self.ACTIVE_REF, missing_ok=True)
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
            self._run(self.repo, ["update-ref", self.ACTIVE_REF, candidate.sha, expected])
        if publish_remote:
            self._publish_remote(candidate.sha, expected_remote_tip, expected_remote_main)
        return candidate.sha

    def archive_active(self, tag: str) -> str:
        """Retain the current active target under a new immutable archive tag."""
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", tag) or tag.startswith("-"):
            raise GitGateError("invalid archive tag")
        ref = f"refs/tags/{tag}"
        if self._ref_sha(self.repo, ref, missing_ok=True):
            raise GitGateError("archive tag already exists")
        active = self._ref_sha(self.repo, self.ACTIVE_REF)
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
            ["push", self.remote, f"{candidate}:{self.ACTIVE_REF.removeprefix('refs/heads/')}"],
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
            self.repo, ["ls-remote", self.remote or "", self.ACTIVE_REF, "refs/heads/main"]
        )
        refs = {}
        for line in output.stdout.decode().splitlines():
            sha, name = line.split("\t", 1)
            refs[name] = sha
        return refs.get(self.ACTIVE_REF), refs.get("refs/heads/main")

    def _require_receipt(self, receipt_hash: str, candidate_sha: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", receipt_hash):
            raise GitGateError("invalid receipt hash")
        path = self.receipt_store / f"{receipt_hash}.json"
        try:
            raw = path.read_bytes()
            body = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise GitGateError("verified receipt is missing or unreadable") from exc
        if _digest(_canonical(body)) != receipt_hash or raw != _canonical(body):
            raise GitGateError("verified receipt was altered")
        if (
            body.get("candidate_sha") != candidate_sha
            or body.get("policy_hash") != self.policy.digest
        ):
            raise GitGateError("receipt does not verify this candidate under the current policy")

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
        self, repo: Path, args: Sequence[str], *, input: bytes | None = None, check: bool = True
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
