"""Bounded content-only delivery for the Paperclip source lane.

The worker supplies complete UTF-8 file contents under owner-defined path IDs. This
module snapshots and validates those bytes, then materializes them in a disposable
controller-owned checkout of the admitted source base. It never opens a worker path.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engineering.git_gate import GitGate, GitGateError

SOURCE_PATHS = (
    "src/memecoin_brain_sources/events.py",
    "tests/brain/test_raw_sources.py",
)
MAX_FILE_BYTES = 1_000_000
MAX_TOTAL_BYTES = 1_500_000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")


class SourceDeliveryError(ValueError):
    """A content-only delivery violated its immutable input contract."""


@dataclass(frozen=True)
class SourceFile:
    path_id: str
    path: str
    data: bytes
    size: int
    sha256: str


@dataclass(frozen=True)
class SourceSnapshot:
    files: tuple[SourceFile, ...]
    digest: str
    total_bytes: int


@dataclass(frozen=True)
class MaterializedProjection:
    checkout: Path
    submitted_sha: str
    base_sha: str
    snapshot_digest: str


def snapshot_records(records: Any) -> SourceSnapshot:
    """Validate a complete exact file set and retain an immutable byte snapshot."""
    if type(records) is not list:
        raise SourceDeliveryError("source_records_invalid")
    if len(records) != len(SOURCE_PATHS):
        raise SourceDeliveryError("source_file_set_incomplete")
    by_id: dict[str, SourceFile] = {}
    total = 0
    for row in records:
        if type(row) is not dict or set(row) != {"path_id", "content", "size", "sha256"}:
            raise SourceDeliveryError("source_record_shape_invalid")
        path_id = row["path_id"]
        if type(path_id) is not str or path_id not in SOURCE_PATHS:
            raise SourceDeliveryError("source_path_id_not_allowed")
        if path_id in by_id:
            raise SourceDeliveryError("source_path_id_duplicate")
        content = row["content"]
        if type(content) is not str:
            raise SourceDeliveryError("source_content_must_be_utf8_text")
        try:
            data = content.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise SourceDeliveryError("source_content_must_be_utf8_text") from exc
        if type(row["size"]) is not int or row["size"] != len(data):
            raise SourceDeliveryError("source_declared_size_mismatch")
        digest = row["sha256"]
        if type(digest) is not str or not SHA256_RE.fullmatch(digest):
            raise SourceDeliveryError("source_sha256_invalid")
        actual = hashlib.sha256(data).hexdigest()
        if actual != digest:
            raise SourceDeliveryError("source_sha256_mismatch")
        if len(data) > MAX_FILE_BYTES:
            raise SourceDeliveryError("source_file_exceeds_limit")
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise SourceDeliveryError("source_delivery_exceeds_limit")
        by_id[path_id] = SourceFile(path_id, path_id, data, len(data), digest)
    if set(by_id) != set(SOURCE_PATHS):
        raise SourceDeliveryError("source_file_set_incomplete")
    files = tuple(by_id[path] for path in SOURCE_PATHS)
    body = b"\n".join(
        path.encode() + b"\0" + str(item.size).encode() + b"\0" + item.sha256.encode()
        for path, item in ((item.path_id, item) for item in files)
    )
    return SourceSnapshot(files, hashlib.sha256(body).hexdigest(), total)


def materialize_owner_projection(
    gate: GitGate,
    base_sha: str,
    snapshot: SourceSnapshot,
    *,
    checkout_parent: Path | None = None,
) -> MaterializedProjection:
    """Create an owner-controlled source commit from the validated snapshot."""
    if type(base_sha) is not str or not GIT_SHA_RE.fullmatch(base_sha):
        raise SourceDeliveryError("source_base_sha_invalid")
    if not isinstance(snapshot, SourceSnapshot):
        raise SourceDeliveryError("source_snapshot_invalid")
    checkout: Path | None = None
    try:
        base = gate._resolve_sha(gate.source_repo, base_sha)
        source_tip = gate._ref_sha(gate.source_repo, gate.active_ref, missing_ok=True)
        source_tip = source_tip or gate._rev(gate.source_repo, "HEAD")
        if base != base_sha or not gate._is_ancestor(gate.source_repo, base, source_tip):
            raise SourceDeliveryError("source_base_not_owner_admitted")
        parent = checkout_parent or gate.repo / ".git" / "git-gate-source-projections"
        if parent.is_symlink():
            raise SourceDeliveryError("owner_source_projection_parent_invalid")
        parent.mkdir(parents=True, exist_ok=True)
        checkout = Path(tempfile.mkdtemp(prefix="content-", dir=parent))
        checkout.rmdir()
        gate._run(gate.source_repo, ["worktree", "add", "--detach", str(checkout), base])
        for item in snapshot.files:
            target = checkout / item.path
            cursor = checkout
            for component in Path(item.path).parts[:-1]:
                cursor = cursor / component
                if cursor.is_symlink() or not cursor.is_dir():
                    raise SourceDeliveryError("owner_source_path_parent_invalid")
            try:
                target_info = target.lstat()
            except OSError as exc:
                raise SourceDeliveryError("owner_source_path_not_regular_file") from exc
            if not stat.S_ISREG(target_info.st_mode):
                raise SourceDeliveryError("owner_source_path_not_regular_file")
            target.write_bytes(item.data)
        gate._run(checkout, ["add", "--", *SOURCE_PATHS])
        gate._run(
            checkout,
            [
                "-c",
                "user.name=PaperclipContentDelivery",
                "-c",
                "user.email=paperclip-content@localhost",
                "commit",
                "--no-gpg-sign",
                "-m",
                f"Paperclip content delivery {snapshot.digest}",
            ],
            env_overrides={"GIT_AUTHOR_DATE": "@0 +0000", "GIT_COMMITTER_DATE": "@0 +0000"},
        )
        submitted_sha = gate._rev(checkout, "HEAD")
        if not GIT_SHA_RE.fullmatch(submitted_sha):
            raise SourceDeliveryError("source_projection_commit_invalid")
        changed = gate._changed_paths(checkout, base, submitted_sha)
        if any(path not in SOURCE_PATHS for path in changed):
            raise SourceDeliveryError("source_projection_changed_unapproved_path")
        for item in snapshot.files:
            projected = gate._run(checkout, ["show", f"{submitted_sha}:{item.path}"]).stdout
            if projected != item.data:
                raise SourceDeliveryError("source_projection_content_mismatch")
        return MaterializedProjection(checkout, submitted_sha, base, snapshot.digest)
    except (GitGateError, OSError) as exc:
        if checkout is not None:
            try:
                gate._run(gate.source_repo, ["worktree", "remove", "--force", str(checkout)])
            except GitGateError:
                shutil.rmtree(checkout, ignore_errors=True)
        raise SourceDeliveryError("owner_source_projection_failed") from exc
    except BaseException:
        if checkout is not None:
            try:
                gate._run(gate.source_repo, ["worktree", "remove", "--force", str(checkout)])
            except GitGateError:
                shutil.rmtree(checkout, ignore_errors=True)
        raise


def discard_projection(gate: GitGate, projection: MaterializedProjection) -> None:
    """Remove only the disposable worktree created for this delivery."""
    try:
        gate._run(gate.source_repo, ["worktree", "remove", "--force", str(projection.checkout)])
    except GitGateError:
        shutil.rmtree(projection.checkout, ignore_errors=True)


def refresh_owner_workspace(
    gate: GitGate, source_repo: Path, base_sha: str, workspace_root: Path
) -> str:
    """Refresh only the admitted source files from an owner-pinned commit into a noGit root."""
    if (
        type(base_sha) is not str
        or not GIT_SHA_RE.fullmatch(base_sha)
        or workspace_root.is_symlink()
        or not workspace_root.is_absolute()
        or not workspace_root.is_dir()
    ):
        raise SourceDeliveryError("source_workspace_binding_invalid")
    try:
        resolved_root = workspace_root.resolve(strict=True)
    except OSError as exc:
        raise SourceDeliveryError("source_workspace_binding_invalid") from exc
    digests = []
    for path in SOURCE_PATHS:
        try:
            data = gate._run(source_repo, ["show", f"{base_sha}:{path}"]).stdout
        except (OSError, GitGateError) as exc:
            raise SourceDeliveryError("source_workspace_base_unavailable") from exc
        if len(data) > MAX_FILE_BYTES:
            raise SourceDeliveryError("source_workspace_file_exceeds_limit")
        try:
            data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise SourceDeliveryError("source_workspace_file_not_utf8") from exc
        target = resolved_root / path
        parent = resolved_root
        for component in Path(path).parts[:-1]:
            parent = parent / component
            if parent.is_symlink():
                raise SourceDeliveryError("source_workspace_path_parent_invalid")
            parent.mkdir(mode=0o755, exist_ok=True)
            if not parent.is_dir():
                raise SourceDeliveryError("source_workspace_path_parent_invalid")
        if target.is_symlink():
            raise SourceDeliveryError("source_workspace_target_invalid")
        try:
            info = target.lstat()
        except FileNotFoundError:
            info = None
        if info is not None and not stat.S_ISREG(info.st_mode):
            raise SourceDeliveryError("source_workspace_target_invalid")
        fd, temp_name = tempfile.mkstemp(prefix=".paperclip-source-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_name, 0o644)
            os.replace(temp_name, target)
        finally:
            Path(temp_name).unlink(missing_ok=True)
        digests.append((path, len(data), hashlib.sha256(data).hexdigest()))
    body = b"\n".join(
        path.encode() + b"\0" + str(size).encode() + b"\0" + digest.encode()
        for path, size, digest in digests
    )
    return hashlib.sha256(body).hexdigest()
