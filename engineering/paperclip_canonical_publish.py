"""Bounded publisher for the two canonical Brain source files.

The caller supplies owner-pinned canonical repository/ref identities, exact selected
file bytes, and trusted projected blob identities. Review and durable operation
journaling remain the caller's responsibility.
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
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

SCHEMA = "paperclip-canonical-content-v1"
ALLOWED_PATHS = (
    "src/memecoin_brain_sources/events.py",
    "tests/brain/test_raw_sources.py",
)
_OID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_REF_RE = re.compile(r"^refs/heads/[A-Za-z0-9._/-]+$")
_GIT = "/usr/bin/git"
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class CanonicalPublishError(RuntimeError):
    """A canonical target, receipt, review binding, or reconciliation is unsafe."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _oid(value: object) -> bool:
    return type(value) is str and bool(_OID_RE.fullmatch(value))


def _git(
    repo: Path,
    *args: str,
    index: Path | None = None,
    data: bytes | None = None,
    env_extra: dict[str, str] | None = None,
) -> bytes:
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": "/var/empty",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_PAGER": "cat",
    }
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [
            _GIT,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.attributesFile=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "protocol.allow=never",
            "-C",
            str(repo),
            *args,
        ],
        input=data,
        capture_output=True,
        env=env,
        check=False,
        timeout=20,
    )
    if result.returncode:
        raise CanonicalPublishError(f"git operation failed: {args[0]}")
    return result.stdout


def _repo_ref(repo: Path, ref: str) -> tuple[Path, str]:
    if type(ref) is not str or not _REF_RE.fullmatch(ref) or ".." in ref or "//" in ref:
        raise CanonicalPublishError("owner-pinned canonical ref is invalid")
    try:
        resolved = Path(repo).resolve(strict=True)
    except OSError as exc:
        raise CanonicalPublishError("canonical repository is unavailable") from exc
    if not resolved.is_dir():
        raise CanonicalPublishError("canonical repository is not a directory")
    if _git(resolved, "check-ref-format", ref) != b"":
        raise CanonicalPublishError("owner-pinned canonical ref is invalid")
    return resolved, ref


def _paths(allowed_paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if type(allowed_paths) not in (tuple, list) or not allowed_paths or len(allowed_paths) > 32:
        raise CanonicalPublishError("owner-pinned canonical path set is invalid")
    paths = tuple(allowed_paths)
    if (
        len(set(paths)) != len(paths)
        or any(
            type(path) is not str
            or not path
            or "\\" in path
            or path.startswith("/")
            or str(PurePosixPath(path)) != path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or not path.startswith(("src/", "tests/", "engineering/"))
            for path in paths
        )
        or not any(
            path.startswith("src/") or (path.startswith("engineering/") and "/tests/" not in path)
            for path in paths
        )
        or not any(path.startswith("tests/") or "/tests/" in path for path in paths)
    ):
        raise CanonicalPublishError("owner-pinned canonical path set is invalid")
    return paths


def _validate_maps(
    old_blobs: dict[str, str],
    new_blobs: dict[str, str],
    file_bytes: dict[str, bytes],
    allowed_paths: tuple[str, ...],
) -> None:
    for mapping in (old_blobs, new_blobs, file_bytes):
        if type(mapping) is not dict or set(mapping) != set(allowed_paths):
            raise CanonicalPublishError("input must contain the exact owner-pinned paths")
    if any(not _oid(value) for value in (*old_blobs.values(), *new_blobs.values())):
        raise CanonicalPublishError("trusted projected blob identity is invalid")
    if any(type(value) is not bytes for value in file_bytes.values()):
        raise CanonicalPublishError("selected file contents must be bytes")


def _tree_blobs(
    repo: Path, commit: str, allowed_paths: tuple[str, ...]
) -> dict[str, tuple[str, str]]:
    rows = _git(repo, "ls-tree", "-rz", "--full-tree", commit, "--", *allowed_paths)
    found: dict[str, tuple[str, str]] = {}
    for row in rows.split(b"\0"):
        if row:
            meta, path = row.split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split()
            if kind != "blob" or mode != "100644":
                raise CanonicalPublishError("selected canonical path is not a regular 100644 blob")
            found[path.decode("utf-8")] = (mode, oid)
    if set(found) != set(allowed_paths):
        raise CanonicalPublishError("canonical base lacks the exact selected files")
    return found


def _raw_paths(repo: Path, base: str, commit: str) -> tuple[str, ...]:
    raw = _git(
        repo, "diff-tree", "--no-commit-id", "--raw", "--no-renames", "-r", "-z", base, commit
    )
    fields = raw.split(b"\0")
    paths: list[str] = []
    for index in range(0, len(fields) - 1, 2):
        header = fields[index].split()
        if len(header) != 5:
            raise CanonicalPublishError("candidate raw diff is malformed")
        if header[0] != b":100644" or header[1] != b"100644" or header[4] != b"M":
            raise CanonicalPublishError("candidate contains an unexpected mode or change type")
        paths.append(fields[index + 1].decode("utf-8"))
    return tuple(sorted(paths))


def _receipt_digest(receipt: dict) -> str:
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    return hashlib.sha256(_canonical(unsigned)).hexdigest()


def _validate_receipt(receipt: dict, allowed_paths: tuple[str, ...]) -> None:
    keys = {"schema", "repo", "ref", "expected_tip", "tree", "candidate", "files", "receipt_digest"}
    if type(receipt) is not dict or set(receipt) != keys or receipt.get("schema") != SCHEMA:
        raise CanonicalPublishError("prepared receipt schema is invalid")
    if (
        type(receipt["repo"]) is not str
        or type(receipt["ref"]) is not str
        or not _REF_RE.fullmatch(receipt["ref"])
        or any(not _oid(receipt.get(key)) for key in ("expected_tip", "tree", "candidate"))
        or type(receipt["files"]) is not list
        or len(receipt["files"]) != len(allowed_paths)
        or [item.get("path") for item in receipt["files"] if type(item) is dict]
        != list(allowed_paths)
        or any(
            set(item) != {"path", "mode", "old", "new", "sha256"}
            or item.get("mode") != "100644"
            or not _oid(item.get("old"))
            or not _oid(item.get("new"))
            or type(item.get("sha256")) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
            for item in receipt["files"]
            if type(item) is dict
        )
        or receipt.get("receipt_digest") != _receipt_digest(receipt)
    ):
        raise CanonicalPublishError("prepared receipt is malformed or digest-mismatched")


def prepare(
    canonical_repo: Path,
    canonical_ref: str,
    expected_tip: str,
    trusted_old_blobs: dict[str, str],
    trusted_new_blobs: dict[str, str],
    selected_bytes: dict[str, bytes],
    *,
    allowed_paths: tuple[str, ...] | list[str] = ALLOWED_PATHS,
) -> dict:
    """Create a deterministic, unreferenced commit by changing only owner-pinned blobs."""
    repo, ref = _repo_ref(canonical_repo, canonical_ref)
    allowed = _paths(allowed_paths)
    _validate_maps(trusted_old_blobs, trusted_new_blobs, selected_bytes, allowed)
    if not _oid(expected_tip):
        raise CanonicalPublishError("expected canonical tip is invalid")
    if _git(repo, "rev-parse", ref).decode().strip() != expected_tip:
        raise CanonicalPublishError("canonical ref is stale")
    old_tree = _tree_blobs(repo, expected_tip, allowed)
    for path in allowed:
        if old_tree[path] != ("100644", trusted_old_blobs[path]):
            raise CanonicalPublishError(f"trusted projected base differs for {path}")

    fd, index_name = tempfile.mkstemp(prefix="canonical-publish-index-")
    os.close(fd)
    index = Path(index_name)
    try:
        _git(repo, "read-tree", expected_tip, index=index)
        rows = []
        for path in allowed:
            data = selected_bytes[path]
            imported = _git(repo, "hash-object", "-w", "--stdin", data=data).decode().strip()
            if imported != trusted_new_blobs[path]:
                raise CanonicalPublishError(
                    f"selected bytes differ from trusted new blob for {path}"
                )
            rows.append(f"100644 {imported}\t{path}\n")
        _git(repo, "update-index", "--index-info", index=index, data="".join(rows).encode())
        tree = _git(repo, "write-tree", index=index).decode().strip()
        candidate = (
            _git(
                repo,
                "commit-tree",
                tree,
                "-p",
                expected_tip,
                data=b"Paperclip canonical content candidate\n",
                env_extra={
                    "GIT_AUTHOR_NAME": "Paperclip Canonical Publisher",
                    "GIT_AUTHOR_EMAIL": "publisher@localhost",
                    "GIT_COMMITTER_NAME": "Paperclip Canonical Publisher",
                    "GIT_COMMITTER_EMAIL": "publisher@localhost",
                    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+0000",
                    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+0000",
                },
            )
            .decode()
            .strip()
        )
        changed = _raw_paths(repo, expected_tip, candidate)
        if not changed or any(path not in allowed for path in changed):
            raise CanonicalPublishError(
                "prepared candidate changed paths outside the owner-pinned scope"
            )
        files = [
            {
                "path": path,
                "mode": "100644",
                "old": trusted_old_blobs[path],
                "new": trusted_new_blobs[path],
                "sha256": hashlib.sha256(selected_bytes[path]).hexdigest(),
            }
            for path in allowed
        ]
        receipt = {
            "schema": SCHEMA,
            "repo": str(repo),
            "ref": ref,
            "expected_tip": expected_tip,
            "tree": tree,
            "candidate": candidate,
            "files": files,
        }
        receipt["receipt_digest"] = _receipt_digest(receipt)
        return receipt
    finally:
        index.unlink(missing_ok=True)


def _lock_path(repo: Path, ref: str) -> Path:
    common = Path(_git(repo, "rev-parse", "--git-common-dir").decode().strip())
    if not common.is_absolute():
        common = (repo / common).resolve()
    digest = hashlib.sha256(f"{repo}\0{ref}".encode()).hexdigest()
    return common / f"paperclip-canonical-{digest}.lock"


@contextmanager
def _serialized(repo: Path, ref: str) -> Iterator[None]:
    lock_path = _lock_path(repo, ref)
    key = str(lock_path)
    with _LOCKS_GUARD:
        local = _LOCKS.setdefault(key, threading.RLock())
    with local:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise CanonicalPublishError("canonical lock no-follow open is unavailable")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | no_follow, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise CanonicalPublishError("canonical publication lock is not a regular file")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _check_candidate(repo: Path, ref: str, receipt: dict, allowed_paths: tuple[str, ...]) -> None:
    _validate_receipt(receipt, allowed_paths)
    if receipt["repo"] != str(repo) or receipt["ref"] != ref:
        raise CanonicalPublishError("receipt canonical target differs from caller-pinned target")
    row = _git(repo, "rev-list", "--parents", "-n", "1", receipt["candidate"]).decode().split()
    if row != [receipt["candidate"], receipt["expected_tip"]]:
        raise CanonicalPublishError("candidate does not have the exact expected parent")
    if (
        _git(repo, "rev-parse", f"{receipt['candidate']}^{{tree}}").decode().strip()
        != receipt["tree"]
    ):
        raise CanonicalPublishError("candidate tree differs from receipt")
    base = _tree_blobs(repo, receipt["expected_tip"], allowed_paths)
    candidate = _tree_blobs(repo, receipt["candidate"], allowed_paths)
    for item in receipt["files"]:
        path = item["path"]
        if base[path] != ("100644", item["old"]) or candidate[path] != ("100644", item["new"]):
            raise CanonicalPublishError("receipt blob provenance differs from canonical objects")
    changed = _raw_paths(repo, receipt["expected_tip"], receipt["candidate"])
    if not changed or any(path not in allowed_paths for path in changed):
        raise CanonicalPublishError("candidate contains changes outside the exact allowed paths")


def _file_state(repo: Path, path: str, old: str, new: str) -> str:
    target = repo / path
    for parent in target.parents:
        if parent == repo:
            break
        try:
            info = parent.lstat()
        except OSError as exc:
            raise CanonicalPublishError("selected worktree parent is unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise CanonicalPublishError(
                "selected worktree path has a symlink or non-directory parent"
            )
    try:
        info = target.lstat()
    except OSError as exc:
        raise CanonicalPublishError("selected worktree file is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o111:
        raise CanonicalPublishError("selected worktree path is not a regular non-executable file")
    worktree_oid = _git(repo, "hash-object", "--no-filters", "--", str(target)).decode().strip()
    if worktree_oid not in (old, new):
        raise CanonicalPublishError("selected worktree file has unknown user edits")
    row = _git(repo, "ls-files", "--stage", "-z", "--", path).rstrip(b"\0")
    entries = row.split(b"\0") if row else []
    if len(entries) != 1:
        raise CanonicalPublishError("selected index entry is missing or unmerged")
    meta, indexed_path = entries[0].split(b"\t", 1)
    mode, index_oid, stage = meta.decode("ascii").split()
    if (
        indexed_path.decode() != path
        or mode != "100644"
        or stage != "0"
        or index_oid not in (old, new)
    ):
        raise CanonicalPublishError("selected index entry has unknown state")
    return worktree_oid


def inspect(
    canonical_repo: Path,
    canonical_ref: str,
    receipt: dict,
    *,
    allowed_paths: tuple[str, ...] | list[str] = ALLOWED_PATHS,
) -> dict:
    """Read publication state without changing the ref, worktree, or index."""
    repo, ref = _repo_ref(canonical_repo, canonical_ref)
    _check_candidate(repo, ref, receipt, _paths(allowed_paths))
    with _serialized(repo, ref):
        tip = _git(repo, "rev-parse", ref).decode().strip()
        if tip not in (receipt["expected_tip"], receipt["candidate"]):
            raise CanonicalPublishError("canonical ref moved to an unknown third tip")
        states = [
            _file_state(repo, item["path"], item["old"], item["new"]) for item in receipt["files"]
        ]
        return {
            "candidate": receipt["candidate"],
            "receipt_digest": receipt["receipt_digest"],
            "ref_state": "prepared" if tip == receipt["expected_tip"] else "cas_applied",
            "files_state": "old"
            if all(value == receipt["files"][i]["old"] for i, value in enumerate(states))
            else (
                "new"
                if all(value == receipt["files"][i]["new"] for i, value in enumerate(states))
                else "partial"
            ),
        }


def _reconcile_locked(repo: Path, ref: str, receipt: dict) -> dict:
    tip = _git(repo, "rev-parse", ref).decode().strip()
    candidate = receipt["candidate"]
    expected = receipt["expected_tip"]
    if tip not in (expected, candidate):
        raise CanonicalPublishError("canonical ref moved to an unknown third tip")
    for item in receipt["files"]:
        _file_state(repo, item["path"], item["old"], item["new"])
    if tip == expected:
        try:
            _git(repo, "update-ref", ref, candidate, expected)
        except CanonicalPublishError:
            if _git(repo, "rev-parse", ref).decode().strip() != candidate:
                raise
    if _git(repo, "rev-parse", ref).decode().strip() != candidate:
        raise CanonicalPublishError("canonical ref readback differs after compare-and-swap")
    for item in receipt["files"]:
        path, old, new = item["path"], item["old"], item["new"]
        state = _file_state(repo, path, old, new)
        _git(repo, "update-index", "--cacheinfo", f"100644,{new},{path}")
        if state == old:
            if _file_state(repo, path, old, new) != old:
                raise CanonicalPublishError("selected file changed before materialization")
            data = _git(repo, "cat-file", "blob", new)
            target = repo / path
            fd, temp_name = tempfile.mkstemp(prefix=".canonical-publish-", dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temp_name, 0o644)
                os.replace(temp_name, target)
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                Path(temp_name).unlink(missing_ok=True)
    for item in receipt["files"]:
        if _file_state(repo, item["path"], item["new"], item["new"]) != item["new"]:
            raise CanonicalPublishError("selected file reconciliation is incomplete")
    return {
        "candidate": candidate,
        "receipt_digest": receipt["receipt_digest"],
        "status": "reconciled",
        "ref": ref,
    }


def publish(
    canonical_repo: Path,
    canonical_ref: str,
    receipt: dict,
    approved_candidate: str,
    approved_receipt_digest: str,
    *,
    allowed_paths: tuple[str, ...] | list[str] = ALLOWED_PATHS,
) -> dict:
    """Publish only the exact externally reviewed candidate and reconcile its two paths."""
    repo, ref = _repo_ref(canonical_repo, canonical_ref)
    _check_candidate(repo, ref, receipt, _paths(allowed_paths))
    if (
        approved_candidate != receipt["candidate"]
        or approved_receipt_digest != receipt["receipt_digest"]
    ):
        raise CanonicalPublishError("external approval does not bind this exact candidate receipt")
    with _serialized(repo, ref):
        head = _git(repo, "symbolic-ref", "--quiet", "HEAD").decode().strip()
        if head != ref:
            raise CanonicalPublishError("canonical worktree HEAD is not on the pinned ref")
        result = _reconcile_locked(repo, ref, receipt)
    return result


def reconcile(
    canonical_repo: Path,
    canonical_ref: str,
    receipt: dict,
    approved_candidate: str,
    approved_receipt_digest: str,
    *,
    allowed_paths: tuple[str, ...] | list[str] = ALLOWED_PATHS,
) -> dict:
    """Resume an approved operation after interruption, without replacing unknown edits."""
    return publish(
        canonical_repo,
        canonical_ref,
        receipt,
        approved_candidate,
        approved_receipt_digest,
        allowed_paths=allowed_paths,
    )
