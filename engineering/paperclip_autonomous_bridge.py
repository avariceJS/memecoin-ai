"""Owner-configured local Paperclip submit/review/GitGate bridge.

The Paperclip plugin is the identity gateway. It passes only identities obtained from the
host-owned ToolRunContext after checking native issue/run association. This process never
imports candidate code: fixed verifier commands run under SandboxRunner in child processes.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import resource
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

# `python -I -S -B /absolute/path/to/this_file.py` excludes cwd and user site paths.
# Only this owner-pinned source root is added for imports of trusted controller modules.
TRUSTED_SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(TRUSTED_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(TRUSTED_SOURCE_ROOT))

from engineering import paperclip_canonical_publish as canonical_publisher  # noqa: E402
from engineering.confinement import (  # noqa: E402
    VERIFIER_ENV_KEYS,
    SandboxRunner,
    python_runtime_roots,
)
from engineering.git_gate import (  # noqa: E402
    OWNER_CAS_REFS,
    Candidate,
    GitGate,
    GitGateError,
    VerificationPolicy,
)
from engineering.paperclip_git_broker import (  # noqa: E402
    OperationBinding,
    PaperclipGitBroker,
    ReviewerApproval,
    company_operation,
    require_company_open,
)
from engineering.paperclip_source_delivery import (  # noqa: E402
    SOURCE_PATHS,
    SourceDeliveryError,
    discard_projection,
    materialize_owner_projection,
    refresh_owner_workspace,
    restore_owner_workspace_snapshot,
    snapshot_records,
    validate_path_ids,
)
from engineering.paperclip_accepted_source_reader import (  # noqa: E402
    AcceptedSourceReadError,
    build_snapshot as build_accepted_source_snapshot,
    list_files as list_accepted_source_files,
    read_lines as read_accepted_source_lines,
    search_text as search_accepted_source_text,
)

MAX_CONFIG_BYTES = 128_000
MAX_REQUEST_BYTES = 1_700_000
MAX_EVIDENCE_BYTES = 16_000
MAX_REVIEW_DIFF_BYTES = 60_000
MAX_GIT_ERROR_BYTES = 16_000
MAX_PREFLIGHT_ARTIFACT_BYTES = 2_000_000
MAX_PREFLIGHT_OUTPUT_BYTES = 16_000
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
OPERATION_RE = re.compile(r"^[0-9a-f]{64}$")


class BridgeError(RuntimeError):
    """Invalid owner configuration, request binding, or held side effect."""


@dataclass(frozen=True)
class SourceStageSpec:
    stage_key: str
    workspace_id: str
    initial_base_sha: str | None
    predecessor_stage_key: str | None
    path_ids: tuple[str, ...]
    verifier_policy_id: str
    project_id: str | None = None
    goal_id: str | None = None
    lead_agent_id: str | None = None
    lead_issue_id: str | None = None
    source_workspace_id: str | None = None
    source_workspace_root: Path | None = None
    source_repo: Path | None = None
    publisher_repo: Path | None = None
    canonical_repo: Path | None = None
    canonical_ref: str | None = None
    canonical_expected_tip: str | None = None
    expected_dev_ref: str | None = None


@dataclass(frozen=True)
class AcceptedSourceProjectionSpec:
    corpus_id: str
    repository: Path
    ref: str
    allowed_roots: tuple[str, ...]
    bootstrap_projection_sha: str
    bootstrap_source_sha: str
    bootstrap_receipt_digest: str


@dataclass(frozen=True)
class AcceptedSourceProgramSpec:
    project_id: str
    program_issue_id: str
    sol_agent_id: str
    luna_agent_id: str
    qa_agent_ids: tuple[str, ...]
    corpus_ids: tuple[str, ...]


@dataclass(frozen=True)
class AcceptedSourceReaderSpec:
    projections: tuple[AcceptedSourceProjectionSpec, ...]
    programs: tuple[AcceptedSourceProgramSpec, ...]
    max_file_bytes: int = 2_000_000
    max_page_size: int = 50
    max_read_lines: int = 80
    max_result_bytes: int = 48_000
    max_search_scan_bytes: int = 8_000_000


@dataclass(frozen=True)
class ProgramAdmissionProfile:
    profile_id: str
    allowed_path_ids: tuple[str, ...]
    allowed_prefixes: tuple[str, ...]
    required_test_paths: tuple[str, ...]
    verifier_policy_id: str


@dataclass(frozen=True)
class ProgramAdmissionMission:
    project_id: str
    standing_issue_id: str
    sol_agent_id: str
    astra_agent_id: str
    luna_agent_id: str
    qa_agent_ids: tuple[str, ...]
    goal_ids: tuple[str, ...]
    allowed_prefixes: tuple[str, ...]
    excluded_prefixes: tuple[str, ...]
    corpus_ids: tuple[str, ...]
    profiles: tuple[ProgramAdmissionProfile, ...]
    source_repo: Path
    publisher_repo: Path
    canonical_repo: Path
    canonical_ref: str
    expected_dev_ref: str
    source_workspace_id: str
    source_workspace_root: Path


@dataclass(frozen=True)
class ProgramAdmissionSpec:
    missions: tuple[ProgramAdmissionMission, ...]


@dataclass(frozen=True)
class VerifierProfile:
    policy_id: str
    commands: tuple[tuple[str, ...], ...]
    env: tuple[tuple[str, str], ...]
    runtime_read_roots: tuple[Path, ...]


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise BridgeError("invalid_json_value") from None


def _operation_scope(config: BridgeConfig, journal: dict[str, Any]) -> tuple[str, ...]:
    if journal.get("source_stage_binding"):
        return config.source_stage(journal.get("source_stage_key", "")).path_ids
    if journal.get("source_snapshot_sha256"):
        return SOURCE_PATHS
    return config.allowed_scope


def _read_json(path: Path, limit: int) -> dict[str, Any]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    except OSError as exc:
        raise BridgeError("owner_file_unavailable") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise BridgeError("owner_file_permissions_invalid")
        if info.st_uid != os.getuid():
            raise BridgeError("owner_file_owner_invalid")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        if (
            len(raw) > limit
            or len(raw) != info.st_size
            or (
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
        ):
            raise BridgeError("owner_file_changed_or_too_large")
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeError("owner_file_unreadable") from exc
    finally:
        os.close(fd)
    if type(value) is not dict:
        raise BridgeError("owner_file_not_object")
    return value


def _journal_path(config: BridgeConfig, operation_key: str) -> Path:
    if not OPERATION_RE.fullmatch(operation_key):
        raise BridgeError("operation_key_invalid")
    directory = config.bridge_journal_dir
    if directory.is_symlink():
        raise BridgeError("bridge_journal_directory_invalid")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not directory.is_dir():
        raise BridgeError("bridge_journal_directory_invalid")
    info = directory.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise BridgeError("bridge_journal_directory_permissions_invalid")
    return directory / f"operation-{operation_key}.json"


@contextmanager
def _operation_lock(config: BridgeConfig, operation_key: str):
    path = _journal_path(config, operation_key).with_suffix(".lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise BridgeError("bridge_operation_lock_permissions_invalid")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write_journal(
    config: BridgeConfig, operation_key: str, value: dict[str, Any], *, create: bool
) -> None:
    path = _journal_path(config, operation_key)
    encoded = _canonical(value)
    if len(encoded) > MAX_CONFIG_BYTES:
        raise BridgeError("bridge_journal_too_large")
    with company_operation(config.broker_journal_dir, config.company_id):
        require_company_open(config.broker_journal_dir, config.company_id)
        if create and path.exists():
            raise BridgeError("operation_already_recorded")
        if not create and not path.exists():
            raise BridgeError("bridge_journal_missing")
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise BridgeError("bridge_journal_permissions_invalid")
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if not create:
                previous = path.lstat()
                if (
                    not stat.S_ISREG(previous.st_mode)
                    or previous.st_uid != os.getuid()
                    or previous.st_mode & 0o077
                ):
                    raise BridgeError("bridge_journal_permissions_invalid")
            os.replace(temp_path, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            temp_path.unlink(missing_ok=True)
            raise
        finally:
            try:
                os.close(fd)
            except OSError:
                pass


def _load_journal(config: BridgeConfig, operation_key: str) -> dict[str, Any]:
    with company_operation(config.broker_journal_dir, config.company_id):
        require_company_open(config.broker_journal_dir, config.company_id)
        return _read_json(_journal_path(config, operation_key), MAX_CONFIG_BYTES)


def _stage_journal_key(stage_key: str, attempt: int = 1) -> str:
    if type(attempt) is not int or attempt not in (1, 2, 3):
        raise BridgeError("source_stage_attempt_invalid")
    if attempt == 1:
        return hashlib.sha256(_canonical(["paperclip-source-stage-v1", stage_key])).hexdigest()
    return hashlib.sha256(
        _canonical(["paperclip-source-stage-attempt-v1", stage_key, attempt])
    ).hexdigest()


def _record_attempt(record: dict[str, Any]) -> int:
    """Treat only a missing legacy field as attempt 1; reject bool/int aliases."""
    attempt = record["attempt"] if "attempt" in record else 1
    if type(attempt) is not int or attempt not in (1, 2, 3):
        raise BridgeError("source_stage_attempt_invalid")
    return attempt


def _stage_attempt_limit(stage: SourceStageSpec) -> int:
    return 3


def _stage_record_key(record: dict[str, Any]) -> str:
    attempt = _record_attempt(record)
    return _stage_journal_key(record.get("stage_key", ""), attempt)


def _preflight_dir(config: BridgeConfig) -> Path:
    root = config.bridge_journal_dir
    if root.is_symlink():
        raise BridgeError("bridge_journal_directory_invalid")
    directory = root / "source-preflight-v1"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise BridgeError("source_preflight_directory_permissions_invalid")
    return directory


def _preflight_path(config: BridgeConfig, kind: str, content_check_id: str) -> Path:
    if kind not in {"snapshot", "intent", "result", "retry-snapshot"} or not HASH_RE.fullmatch(
        content_check_id
    ):
        raise BridgeError("source_preflight_identity_invalid")
    return _preflight_dir(config) / f"{kind}-{content_check_id}.json"


def _write_immutable_file(path: Path, encoded: bytes, *, limit: int) -> str:
    if len(encoded) > limit:
        raise BridgeError("source_preflight_artifact_too_large")
    parent = path.parent
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=parent)
    temporary = Path(temp_name)
    digest = hashlib.sha256(encoded).hexdigest()
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            existing = _read_json(path, limit)
            if _canonical(existing) != encoded:
                raise BridgeError("source_preflight_artifact_replay_mismatch") from None
        directory_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return digest
    except OSError as exc:
        raise BridgeError("source_preflight_artifact_write_failed") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _persist_preflight_json(
    config: BridgeConfig, kind: str, content_check_id: str, value: dict[str, Any]
) -> str:
    path = _preflight_path(config, kind, content_check_id)
    encoded = _canonical(value)
    with company_operation(config.broker_journal_dir, config.company_id):
        require_company_open(config.broker_journal_dir, config.company_id)
        return _write_immutable_file(path, encoded, limit=MAX_PREFLIGHT_ARTIFACT_BYTES)


def _load_preflight_json(config: BridgeConfig, kind: str, content_check_id: str) -> dict[str, Any]:
    path = _preflight_path(config, kind, content_check_id)
    with company_operation(config.broker_journal_dir, config.company_id):
        require_company_open(config.broker_journal_dir, config.company_id)
        return _read_json(path, MAX_PREFLIGHT_ARTIFACT_BYTES)


def _snapshot_rejected_workspace(
    config: BridgeConfig,
    operation: dict[str, Any],
    binding: dict[str, Any],
    stage: SourceStageSpec,
) -> tuple[str, str]:
    """Archive and compare the exact owner workspace bytes before retry refresh."""
    if config.source_workspace_root is None:
        raise BridgeError("source_stage_retry_workspace_unavailable")
    candidate_sha = operation.get("candidate", {}).get("candidate_sha")
    if type(candidate_sha) is not str or not SHA_RE.fullmatch(candidate_sha):
        raise BridgeError("source_stage_retry_candidate_missing")
    path_ids = stage.path_ids
    metadata = operation.get("source_files")
    if type(metadata) is not list or len(metadata) != len(path_ids):
        raise BridgeError("source_stage_retry_snapshot_metadata_invalid")
    metadata_by_path = {row.get("path_id"): row for row in metadata if type(row) is dict}
    if set(metadata_by_path) != set(path_ids):
        raise BridgeError("source_stage_retry_snapshot_metadata_invalid")
    gate = config.gate()
    expected_records: list[dict[str, Any]] = []
    for path_id in path_ids:
        try:
            data = gate._run(gate.repo, ["show", f"{candidate_sha}:{path_id}"]).stdout
        except Exception as exc:
            raise BridgeError("source_stage_retry_candidate_missing") from exc
        row = metadata_by_path[path_id]
        if (
            len(data) > 1_000_000
            or len(data) != row.get("size")
            or hashlib.sha256(data).hexdigest() != row.get("sha256")
        ):
            raise BridgeError("source_stage_retry_candidate_snapshot_mismatch")
        try:
            content = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise BridgeError("source_stage_retry_candidate_snapshot_invalid") from exc
        expected_records.append(
            {
                "path_id": path_id,
                "content": content,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    try:
        expected_snapshot = snapshot_records(expected_records, path_ids)
        if expected_snapshot.digest != operation.get("source_snapshot_sha256"):
            raise BridgeError("source_stage_retry_candidate_snapshot_mismatch")
        actual_records = _snapshot_owner_workspace(config.source_workspace_root, path_ids)
        actual_snapshot = snapshot_records(actual_records, path_ids)
    except SourceDeliveryError as exc:
        raise BridgeError("source_stage_retry_workspace_changed") from exc
    if actual_snapshot.digest != expected_snapshot.digest:
        raise BridgeError("source_stage_retry_workspace_changed")
    archive_key = hashlib.sha256(
        _canonical(
            [
                "source-stage-retry-workspace-v1",
                binding.get("stage_binding_key"),
                operation.get("operation_key"),
                actual_snapshot.digest,
            ]
        )
    ).hexdigest()
    archive = _preflight_snapshot_document(actual_snapshot)
    archive["schema"] = "paperclip-source-stage-retry-workspace-v1"
    artifact_digest = _persist_preflight_json(config, "retry-snapshot", archive_key, archive)
    return actual_snapshot.digest, artifact_digest


def _restore_rejected_workspace_seed(
    config: BridgeConfig,
    retry_binding: dict[str, Any],
    retry_operation: dict[str, Any],
    accepted_base_workspace_digest: str,
    seed_snapshot_digest: str,
    seed_artifact_digest: str,
) -> None:
    """Restore only the exact archived rejected bytes after the accepted base refresh."""
    if config.source_workspace_root is None:
        raise BridgeError("source_stage_retry_workspace_unavailable")
    candidate = retry_operation.get("candidate")
    if (
        retry_operation.get("state") != "review_rejected"
        or retry_operation.get("source_stage_binding") != retry_binding.get("stage_binding_key")
        or retry_operation.get("operation_key") != retry_binding.get("operation_key")
        or type(candidate) is not dict
        or candidate.get("operation_key") != retry_operation.get("operation_key")
        or candidate.get("candidate_sha") != retry_binding.get("candidate_sha")
        or retry_operation.get("source_snapshot_sha256") != seed_snapshot_digest
        or type(seed_snapshot_digest) is not str
        or not HASH_RE.fullmatch(seed_snapshot_digest)
        or type(seed_artifact_digest) is not str
        or not HASH_RE.fullmatch(seed_artifact_digest)
    ):
        raise BridgeError("source_stage_retry_seed_binding_mismatch")
    archive_key = hashlib.sha256(
        _canonical(
            [
                "source-stage-retry-workspace-v1",
                retry_binding.get("stage_binding_key"),
                retry_operation.get("operation_key"),
                seed_snapshot_digest,
            ]
        )
    ).hexdigest()
    try:
        archive = _load_preflight_json(config, "retry-snapshot", archive_key)
    except BridgeError as exc:
        raise BridgeError("source_stage_retry_seed_unavailable") from exc
    if (
        hashlib.sha256(_canonical(archive)).hexdigest() != seed_artifact_digest
        or set(archive) != {"schema", "snapshot_sha256", "files"}
        or archive.get("schema") != "paperclip-source-stage-retry-workspace-v1"
        or type(archive.get("files")) is not list
    ):
        raise BridgeError("source_stage_retry_seed_artifact_mismatch")
    try:
        stage_key = retry_binding.get("stage_key")
        path_ids = (
            config.source_stage(stage_key).path_ids if type(stage_key) is str else SOURCE_PATHS
        )
        seed = snapshot_records(archive["files"], path_ids)
        accepted_base = snapshot_records(
            _snapshot_owner_workspace(config.source_workspace_root, path_ids), path_ids
        )
    except SourceDeliveryError as exc:
        raise BridgeError("source_stage_retry_workspace_changed") from exc
    source_files = retry_operation.get("source_files")
    expected_manifest = [
        {"path_id": item.path_id, "size": item.size, "sha256": item.sha256} for item in seed.files
    ]
    if (
        archive.get("snapshot_sha256") != seed_snapshot_digest
        or seed.digest != seed_snapshot_digest
        or type(source_files) is not list
        or source_files != expected_manifest
        or accepted_base.digest != accepted_base_workspace_digest
    ):
        raise BridgeError("source_stage_retry_seed_identity_mismatch")
    try:
        restored_digest = restore_owner_workspace_snapshot(
            config.source_workspace_root,
            archive["files"],
            seed_snapshot_digest,
            allowed_paths=path_ids,
        )
        restored = snapshot_records(
            _snapshot_owner_workspace(config.source_workspace_root, path_ids), path_ids
        )
    except SourceDeliveryError as exc:
        raise BridgeError("source_stage_retry_seed_restore_failed") from exc
    if restored_digest != seed_snapshot_digest or restored.digest != seed_snapshot_digest:
        raise BridgeError("source_stage_retry_seed_restore_failed")


def _snapshot_owner_workspace(
    workspace_root: Path, path_ids: tuple[str, ...] = SOURCE_PATHS
) -> list[dict[str, Any]]:
    if workspace_root.is_symlink() or not workspace_root.is_absolute():
        raise SourceDeliveryError("source_workspace_binding_invalid")
    try:
        resolved_root = workspace_root.resolve(strict=True)
    except OSError as exc:
        raise SourceDeliveryError("source_workspace_binding_invalid") from exc
    records = []
    for path_id in validate_path_ids(path_ids):
        target = resolved_root / path_id
        parent = resolved_root
        for component in Path(path_id).parts[:-1]:
            parent = parent / component
            if parent.is_symlink() or not parent.is_dir():
                raise SourceDeliveryError("source_workspace_path_parent_invalid")
        try:
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
        except OSError as exc:
            raise SourceDeliveryError("source_workspace_target_invalid") from exc
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > 1_000_000:
                raise SourceDeliveryError("source_workspace_target_invalid")
            data = bytearray()
            while chunk := os.read(descriptor, 64 * 1024):
                data.extend(chunk)
                if len(data) > 1_000_000:
                    raise SourceDeliveryError("source_workspace_file_exceeds_limit")
            after = os.fstat(descriptor)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) or len(data) != before.st_size:
                raise SourceDeliveryError("source_workspace_file_changed")
        finally:
            os.close(descriptor)
        try:
            content = bytes(data).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise SourceDeliveryError("source_workspace_file_not_utf8") from exc
        records.append(
            {
                "path_id": path_id,
                "content": content,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return records


def _trusted_stage_source_cache_path(
    config: BridgeConfig, stage: SourceStageSpec, base_sha: str
) -> Path:
    if (
        stage.workspace_id != "published"
        or type(base_sha) is not str
        or not SHA_RE.fullmatch(base_sha)
    ):
        raise BridgeError("source_stage_cache_binding_invalid")
    return config.bridge_journal_dir / "trusted-stage-sources" / f"{stage.stage_key}-{base_sha}"


def _trusted_stage_source_cache_identity(
    config: BridgeConfig,
    stage: SourceStageSpec,
    base_sha: str,
    path: Path,
    *,
    require_expected_path: bool = True,
) -> str:
    config = _stage_target_config(config, stage, base_sha)
    expected_path = _trusted_stage_source_cache_path(config, stage, base_sha)
    if (require_expected_path and path != expected_path) or path.is_symlink() or not path.is_dir():
        raise BridgeError("source_stage_cache_missing_or_changed")
    if expected_path.parent.is_symlink() or not expected_path.parent.is_dir():
        raise BridgeError("source_stage_cache_parent_invalid")
    try:
        if (
            expected_path.parent.resolve(strict=True)
            != config.bridge_journal_dir.resolve(strict=True) / "trusted-stage-sources"
        ):
            raise BridgeError("source_stage_cache_parent_invalid")
    except OSError as exc:
        raise BridgeError("source_stage_cache_parent_invalid") from exc
    resolved = path.resolve(strict=True)
    publisher = config.publisher_repo.resolve(strict=True)
    if resolved == publisher or resolved in publisher.parents or publisher in resolved.parents:
        raise BridgeError("source_stage_cache_overlaps_publisher")
    gate = config.gate()
    try:
        head = gate._rev(path, "HEAD")
        stage_ref = gate._ref_sha(path, config.expected_dev_ref, missing_ok=True)
        tree = gate._run(path, ["rev-parse", f"{base_sha}^{{tree}}"])
        tree_sha = tree.stdout.decode("ascii", errors="strict").strip()
        top = gate._run(path, ["rev-parse", "--show-toplevel"])
        cache_root = Path(top.stdout.decode("utf-8", errors="strict").strip()).resolve(strict=True)
        status = gate._run(path, ["status", "--porcelain", "--untracked-files=all"])
        git_dir = gate._run(path, ["rev-parse", "--git-dir"]).stdout.decode("utf-8").strip()
    except Exception as exc:
        raise BridgeError("source_stage_cache_invalid") from exc
    git_dir_path = Path(git_dir)
    if not git_dir_path.is_absolute():
        git_dir_path = path / git_dir_path
    if (
        head != base_sha
        or stage_ref != base_sha
        or cache_root != resolved
        or status.stdout
        or (git_dir_path / "objects" / "info" / "alternates").exists()
        or not SHA_RE.fullmatch(tree_sha)
    ):
        raise BridgeError("source_stage_cache_invalid")
    return tree_sha


def _create_trusted_stage_source_cache(
    config: BridgeConfig, stage: SourceStageSpec, base_sha: str
) -> tuple[Path, str]:
    final_path = _trusted_stage_source_cache_path(config, stage, base_sha)
    final_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if final_path.parent.is_symlink():
        raise BridgeError("source_stage_cache_parent_invalid")
    os.chmod(final_path.parent, 0o700)
    if final_path.is_symlink():
        raise BridgeError("source_stage_cache_changed")
    if final_path.exists():
        return final_path, _trusted_stage_source_cache_identity(config, stage, base_sha, final_path)
    config = _stage_target_config(config, stage, base_sha)
    gate = config.gate()
    ref_before = gate._ref_sha(config.publisher_repo, config.expected_dev_ref, missing_ok=True)
    if ref_before != base_sha:
        raise BridgeError("source_stage_cache_publisher_tip_changed")
    temporary = Path(tempfile.mkdtemp(prefix=f".{stage.stage_key}-", dir=final_path.parent))
    temporary.rmdir()
    branch = config.expected_dev_ref.removeprefix("refs/heads/")
    try:
        gate._run(
            config.publisher_repo,
            [
                "clone",
                "--no-local",
                "--single-branch",
                "--no-tags",
                "--no-checkout",
                "--branch",
                branch,
                "--",
                str(config.publisher_repo),
                str(temporary),
            ],
        )
        gate._run(temporary, ["checkout", "--detach", base_sha])
        ref_after = gate._ref_sha(config.publisher_repo, config.expected_dev_ref, missing_ok=True)
        if ref_after != base_sha:
            raise BridgeError("source_stage_cache_publisher_tip_changed")
        tree = _trusted_stage_source_cache_identity(
            config, stage, base_sha, temporary, require_expected_path=False
        )
        os.replace(temporary, final_path)
        if _trusted_stage_source_cache_identity(config, stage, base_sha, final_path) != tree:
            raise BridgeError("source_stage_cache_changed")
        return final_path, tree
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        raise


def _stage_binding(config: BridgeConfig, stage_binding_key: str) -> dict[str, Any]:
    if type(stage_binding_key) is not str or not HASH_RE.fullmatch(stage_binding_key):
        raise BridgeError("source_stage_binding_key_invalid")
    for stage in config.source_stage_catalog:
        for attempt in range(1, _stage_attempt_limit(stage) + 1):
            try:
                record = _load_journal(config, _stage_journal_key(stage.stage_key, attempt))
            except BridgeError:
                continue
            if record.get("stage_binding_key") != stage_binding_key:
                continue
            expected_schema = (
                "paperclip-source-stage-binding-v1"
                if attempt == 1
                else f"paperclip-source-stage-binding-attempt-v{attempt}"
            )
            if record.get("schema") != expected_schema:
                raise BridgeError("source_stage_binding_invalid")
            if _record_attempt(record) != attempt:
                raise BridgeError("source_stage_attempt_invalid")
            if attempt > 1 and record.get("schema") != expected_schema:
                raise BridgeError("source_stage_binding_invalid")
            if record.get("owner_config_digest") != config.owner_config_digest:
                raise BridgeError("source_stage_owner_config_changed")
            if stage.workspace_id == "published":
                cache = record.get("trusted_source_cache")
                cache_tree = record.get("trusted_source_cache_tree")
                if (
                    type(cache) is not str
                    or type(cache_tree) is not str
                    or not SHA_RE.fullmatch(cache_tree)
                ):
                    raise BridgeError("source_stage_cache_binding_invalid")
                actual_tree = _trusted_stage_source_cache_identity(
                    config, stage, record.get("base_sha", ""), Path(cache)
                )
                if actual_tree != cache_tree:
                    raise BridgeError("source_stage_cache_changed")
            return record
    directory = config.bridge_journal_dir
    if directory.exists() and not directory.is_symlink():
        paths = list(directory.glob("operation-*.json"))
        if len(paths) > 4096:
            raise BridgeError("source_stage_binding_invalid")
        matches: list[dict[str, Any]] = []
        for path in paths:
            if path.is_symlink():
                raise BridgeError("source_stage_binding_invalid")
            operation_key = path.stem.removeprefix("operation-")
            if not OPERATION_RE.fullmatch(operation_key):
                continue
            try:
                record = _load_journal(config, operation_key)
            except BridgeError:
                continue
            if record.get("stage_binding_key") == stage_binding_key and record.get("dynamic_stage_spec") is not None:
                if record.get("owner_config_digest") != config.owner_config_digest:
                    raise BridgeError("source_stage_owner_config_changed")
                _dynamic_stage_from_record(config, record)
                matches.append(record)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise BridgeError("source_stage_binding_ambiguous")
    raise BridgeError("source_stage_binding_invalid")


def _dynamic_stage_from_record(config: BridgeConfig, record: dict[str, Any]) -> SourceStageSpec:
    raw = record.get("dynamic_stage_spec")
    if type(raw) is not dict or set(raw) != {"mission_project_id", "profile_id", "corpus_id", "goal_id", "path_ids"}:
        raise BridgeError("source_stage_binding_invalid")
    if config.program_admission is None:
        raise BridgeError("source_stage_not_owner_admitted")
    missions = [item for item in config.program_admission.missions if item.project_id == raw.get("mission_project_id")]
    if len(missions) != 1:
        raise BridgeError("source_stage_not_owner_admitted")
    mission = missions[0]
    profiles = [item for item in mission.profiles if item.profile_id == raw.get("profile_id")]
    if len(profiles) != 1 or raw.get("corpus_id") not in mission.corpus_ids or raw.get("goal_id") not in mission.goal_ids:
        raise BridgeError("source_stage_not_owner_admitted")
    try:
        path_ids = validate_path_ids(raw.get("path_ids"))
    except SourceDeliveryError as exc:
        raise BridgeError("source_stage_binding_invalid") from exc
    profile = profiles[0]
    allowed = set(profile.allowed_path_ids)
    if profile.allowed_prefixes:
        allowed.update(path for path in path_ids if any(path.startswith(root) for root in profile.allowed_prefixes))
    if any(path not in allowed for path in path_ids) or any(path not in path_ids for path in profile.required_test_paths):
        raise BridgeError("source_stage_binding_invalid")
    if any(not any(path.startswith(root) for root in mission.allowed_prefixes) for path in path_ids) or any(
        any(path.startswith(root) for root in mission.excluded_prefixes) for path in path_ids
    ):
        raise BridgeError("source_stage_binding_invalid")
    stage_key = record.get("stage_key")
    if (type(stage_key) is not str or not ID_RE.fullmatch(stage_key)
        or record.get("path_ids") != list(path_ids)
        or record.get("verifier_policy_id") != profile.verifier_policy_id
        or record.get("project_id") != mission.project_id
        or record.get("lead_agent_id") != mission.sol_agent_id
        or type(record.get("lead_issue_id")) is not str
        or stage_key != _dynamic_stage_key(
            config.company_id, mission.project_id, record["lead_issue_id"], profile.profile_id,
            raw["corpus_id"], path_ids, record.get("source_issue_id"), record.get("review_issue_id"),
        )):
        raise BridgeError("source_stage_binding_invalid")
    return SourceStageSpec(
        stage_key, "source", record.get("base_sha"), None, path_ids, profile.verifier_policy_id,
        mission.project_id, raw["goal_id"], mission.sol_agent_id, record.get("lead_issue_id"),
        mission.source_workspace_id, mission.source_workspace_root, mission.source_repo,
        mission.publisher_repo, mission.canonical_repo, mission.canonical_ref,
        record.get("canonical_base_sha"), mission.expected_dev_ref,
    )


def _stage_source_repo(
    config: BridgeConfig, stage: SourceStageSpec, record: dict[str, Any]
) -> Path:
    if stage.workspace_id == "source":
        return stage.source_repo or config.source_repo
    cache = record.get("trusted_source_cache")
    if type(cache) is not str:
        raise BridgeError("source_stage_cache_binding_invalid")
    return Path(cache)


_ACCEPTED_BASE_FILE_MAX_BYTES = 65_536
_ACCEPTED_BASE_TOTAL_MAX_BYTES = 131_072
_ACCEPTED_BASE_DELIVERY_MAX_BYTES = 170_000
_ACCEPTED_BASE_NOTE_MAX_BYTES = 150_000


def _accepted_base_context(
    config: BridgeConfig, stage: SourceStageSpec, record: dict[str, Any]
) -> dict[str, Any]:
    """Return bounded bytes from the exact owner-pinned accepted base tree."""
    if config.source_stage(stage.stage_key) != stage:
        raise BridgeError("source_stage_accepted_base_binding_invalid")
    base_sha = record.get("base_sha")
    binding_key = record.get("stage_binding_key")
    if (
        record.get("owner_config_digest") != config.owner_config_digest
        or record.get("stage_key") != stage.stage_key
        or record.get("path_ids") != list(stage.path_ids)
        or type(base_sha) is not str
        or not SHA_RE.fullmatch(base_sha)
        or type(binding_key) is not str
        or not HASH_RE.fullmatch(binding_key)
        or record.get("state") not in {"bound", "active"}
    ):
        raise BridgeError("source_stage_accepted_base_binding_invalid")

    repo = _stage_source_repo(config, stage, record)
    if stage.workspace_id == "published":
        cache_tree = record.get("trusted_source_cache_tree")
        if (
            type(cache_tree) is not str
            or not SHA_RE.fullmatch(cache_tree)
            or _trusted_stage_source_cache_identity(config, stage, base_sha, repo) != cache_tree
        ):
            raise BridgeError("source_stage_cache_changed")
    stage_config = config.for_source_stage(
        stage,
        source_issue_id=record["source_issue_id"],
        reviewer_issue_id=record["review_issue_id"],
        base_sha=base_sha,
        canonical_base_sha=record.get("canonical_base_sha"),
        source_repo=repo,
    )
    gate = stage_config.gate()
    try:
        resolved_base = gate._resolve_sha(repo, base_sha)
    except GitGateError as exc:
        raise BridgeError("source_stage_accepted_base_unavailable") from exc
    if resolved_base != base_sha:
        raise BridgeError("source_stage_accepted_base_binding_invalid")

    files: list[dict[str, Any]] = []
    total_bytes = 0
    for path_id in stage.path_ids:
        try:
            listing = gate._run(
                repo,
                ["--literal-pathspecs", "ls-tree", "-z", "--full-tree", base_sha, "--", path_id],
            )
        except GitGateError as exc:
            raise BridgeError("source_stage_accepted_base_unavailable") from exc
        entries = [entry for entry in listing.stdout.split(b"\0") if entry]
        if not entries:
            files.append(
                {
                    "path_id": path_id,
                    "present": False,
                    "mode": None,
                    "blob_sha": None,
                    "size_bytes": 0,
                    "content_sha256": None,
                    "content": None,
                }
            )
            continue
        if len(entries) != 1:
            raise BridgeError("source_stage_accepted_base_path_ambiguous")
        metadata, separator, listed_path = entries[0].partition(b"\t")
        if not separator or listed_path.decode("utf-8", errors="strict") != path_id:
            raise BridgeError("source_stage_accepted_base_path_mismatch")
        parts = metadata.decode("ascii", errors="strict").split()
        if len(parts) != 3 or parts[1] != "blob" or parts[0] not in {"100644", "100755"}:
            raise BridgeError("source_stage_accepted_base_file_type_invalid")
        blob_sha = parts[2]
        if not SHA_RE.fullmatch(blob_sha):
            raise BridgeError("source_stage_accepted_base_blob_invalid")
        size_result = gate._run(repo, ["cat-file", "-s", blob_sha])
        try:
            size_bytes = int(size_result.stdout.strip())
        except ValueError:
            raise BridgeError("source_stage_accepted_base_blob_invalid") from None
        if size_bytes < 0 or size_bytes > _ACCEPTED_BASE_FILE_MAX_BYTES:
            raise BridgeError("source_stage_accepted_base_context_too_large")
        total_bytes += size_bytes
        if total_bytes > _ACCEPTED_BASE_TOTAL_MAX_BYTES:
            raise BridgeError("source_stage_accepted_base_context_too_large")
        content_bytes = gate._run(repo, ["cat-file", "blob", blob_sha]).stdout
        if len(content_bytes) != size_bytes:
            raise BridgeError("source_stage_accepted_base_blob_invalid")
        try:
            content = content_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise BridgeError("source_stage_accepted_base_not_utf8") from None
        files.append(
            {
                "path_id": path_id,
                "present": True,
                "mode": parts[0],
                "blob_sha": blob_sha,
                "size_bytes": size_bytes,
                "content_sha256": hashlib.sha256(content_bytes).hexdigest(),
                "content": content,
            }
        )

    context: dict[str, Any] = {
        "schema": "paperclip-source-stage-accepted-base-context-v1",
        "stage_key": stage.stage_key,
        "stage_binding_key": binding_key,
        "owner_config_digest": config.owner_config_digest,
        "project_id": stage.project_id or config.project_id,
        "lead_issue_id": stage.lead_issue_id or config.finite_lead_issue_id,
        "accepted_base_sha": base_sha,
        "accepted_canonical_base_sha": record.get("canonical_base_sha"),
        "accepted_base_ref": stage.expected_dev_ref or config.expected_dev_ref,
        "repository_role": (
            "owner_pinned_published_base"
            if stage.workspace_id == "published"
            else "owner_pinned_stage_source_base"
        ),
        "path_ids": list(stage.path_ids),
        "files": files,
        "total_size_bytes": total_bytes,
    }
    context["context_sha256"] = hashlib.sha256(_canonical(context)).hexdigest()
    return context


def _accepted_base_context_note(stage: SourceStageSpec, context: dict[str, Any]) -> dict[str, str]:
    binding_key = context["stage_binding_key"]
    fields = [
        f"stage_key:{stage.stage_key}",
        f"stage_binding_key:{binding_key}",
        f"accepted_base_sha:{context['accepted_base_sha']}",
        f"accepted_base_ref:{context['accepted_base_ref']}",
        f"repository_role:{context['repository_role']}",
        "context_scope:exact_owner_admitted_paths_only",
        "comparison_scope:bounded_base_content_not_full_repository_equivalence",
        "rejected_retry_seed:excluded",
        f"accepted_base_context_sha256:{context['context_sha256']}",
        f"accepted_base_context_json:{_canonical(context).decode('utf-8', errors='strict')}",
    ]
    idempotency_key = f"{binding_key}:accepted-base-context"
    body = "\n".join(
        [
            f"brain-autonomous-bridge:{idempotency_key}",
            f"brain-source-stage:{binding_key}:accepted-base-context",
            *fields,
            f"accepted_base_context_note_sha256:{hashlib.sha256(chr(10).join(fields).encode('utf-8')).hexdigest()}",
        ]
    )
    if len(body.encode("utf-8")) > _ACCEPTED_BASE_NOTE_MAX_BYTES:
        raise BridgeError("source_stage_accepted_base_context_note_too_large")
    return {"idempotency_key": idempotency_key, "body": body}


def _accepted_base_delivery(
    config: BridgeConfig, stage: SourceStageSpec, record: dict[str, Any]
) -> dict[str, Any]:
    context = _accepted_base_context(config, stage, record)
    note = _accepted_base_context_note(stage, context)
    delivery = {"accepted_base_context_note": note}
    if len(_canonical(delivery)) > _ACCEPTED_BASE_DELIVERY_MAX_BYTES:
        raise BridgeError("source_stage_accepted_base_context_serialized_too_large")
    return delivery


def _parse_accepted_source_reader(raw: Any) -> AcceptedSourceReaderSpec | None:
    if raw is None:
        return None
    if type(raw) is not dict or set(raw) != {"projections", "programs"}:
        raise BridgeError("owner_config_accepted_source_reader_invalid")
    raw_projections = raw.get("projections")
    raw_programs = raw.get("programs")
    if (
        type(raw_projections) is not list
        or not raw_projections
        or len(raw_projections) > 8
        or type(raw_programs) is not list
        or not raw_programs
        or len(raw_programs) > 16
    ):
        raise BridgeError("owner_config_accepted_source_reader_invalid")
    projections: list[AcceptedSourceProjectionSpec] = []
    for row in raw_projections:
        if type(row) is not dict or set(row) != {
            "corpus_id",
            "repository",
            "ref",
            "allowed_roots",
            "bootstrap_projection_sha",
            "bootstrap_source_sha",
            "bootstrap_receipt_digest",
        }:
            raise BridgeError("owner_config_accepted_source_projection_invalid")
        corpus_id = row.get("corpus_id")
        repository = row.get("repository")
        ref = row.get("ref")
        roots = row.get("allowed_roots")
        projection_sha = row.get("bootstrap_projection_sha")
        source_sha = row.get("bootstrap_source_sha")
        receipt_digest = row.get("bootstrap_receipt_digest")
        if (
            type(corpus_id) is not str
            or not ID_RE.fullmatch(corpus_id)
            or type(repository) is not str
            or not Path(repository).is_absolute()
            or Path(repository) == Path("/")
            or ".." in Path(repository).parts
            or type(ref) is not str
            or ref not in {
                "refs/heads/brain/development",
                "refs/heads/org/development",
            }
            or type(roots) is not list
            or not roots
            or len(roots) > 16
            or any(
                type(root) is not str
                or not root
                or "\0" in root
                or "\\" in root
                or root.startswith("/")
                or any(part in {"", ".", ".."} for part in root.rstrip("/").split("/"))
                or (not root.endswith("/") and "/" in root)
                for root in roots
            )
            or len(set(roots)) != len(roots)
            or type(projection_sha) is not str
            or not SHA_RE.fullmatch(projection_sha)
            or type(source_sha) is not str
            or not SHA_RE.fullmatch(source_sha)
            or type(receipt_digest) is not str
            or not HASH_RE.fullmatch(receipt_digest)
        ):
            raise BridgeError("owner_config_accepted_source_projection_invalid")
        projections.append(
            AcceptedSourceProjectionSpec(
                corpus_id,
                Path(repository),
                ref,
                tuple(roots),
                projection_sha,
                source_sha,
                receipt_digest,
            )
        )
    if len({item.corpus_id for item in projections}) != len(projections):
        raise BridgeError("owner_config_accepted_source_projection_invalid")
    corpus_ids = {item.corpus_id for item in projections}
    programs: list[AcceptedSourceProgramSpec] = []
    for row in raw_programs:
        if type(row) is not dict or set(row) != {
            "project_id",
            "program_issue_id",
            "sol_agent_id",
            "luna_agent_id",
            "qa_agent_ids",
            "corpus_ids",
        }:
            raise BridgeError("owner_config_accepted_source_program_invalid")
        identities = tuple(row.get(key) for key in (
            "project_id", "program_issue_id", "sol_agent_id", "luna_agent_id"
        ))
        qa_ids = row.get("qa_agent_ids")
        allowed_projections = row.get("corpus_ids")
        if (
            any(type(value) is not str or not ID_RE.fullmatch(value) for value in identities)
            or identities[2] == identities[3]
            or type(qa_ids) is not list
            or len(qa_ids) > 8
            or any(type(value) is not str or not ID_RE.fullmatch(value) for value in qa_ids)
            or len(set(qa_ids)) != len(qa_ids)
            or identities[2] in qa_ids
            or identities[3] in qa_ids
            or type(allowed_projections) is not list
            or not allowed_projections
            or len(allowed_projections) > 8
            or any(type(value) is not str or value not in corpus_ids for value in allowed_projections)
            or len(set(allowed_projections)) != len(allowed_projections)
        ):
            raise BridgeError("owner_config_accepted_source_program_invalid")
        programs.append(
            AcceptedSourceProgramSpec(
                identities[0], identities[1], identities[2], identities[3],
                tuple(qa_ids), tuple(allowed_projections),
            )
        )
    if len({(item.project_id, item.program_issue_id) for item in programs}) != len(programs):
        raise BridgeError("owner_config_accepted_source_program_invalid")
    return AcceptedSourceReaderSpec(tuple(projections), tuple(programs))


def _parse_program_admission(raw: Any) -> ProgramAdmissionSpec | None:
    """Parse the immutable, task-independent write envelope for episodic programs."""
    if raw is None:
        return None
    if type(raw) is not dict or set(raw) != {"missions"}:
        raise BridgeError("owner_config_program_admission_invalid")
    rows = raw["missions"]
    if type(rows) is not list or not 1 <= len(rows) <= 8:
        raise BridgeError("owner_config_program_admission_invalid")

    def paths(value: Any, *, prefixes: bool) -> tuple[str, ...]:
        if type(value) is not list or not 1 <= len(value) <= 32:
            raise BridgeError("owner_config_program_admission_invalid")
        result = tuple(value)
        if any(
            type(item) is not str or not item or item.startswith("/")
            or "\\" in item or "\0" in item
            or any(part in {"", ".", ".."} or part.startswith(".") for part in item.rstrip("/").split("/"))
            or (prefixes and not item.endswith("/"))
            or (not prefixes and item.endswith("/"))
            for item in result
        ) or len(set(result)) != len(result):
            raise BridgeError("owner_config_program_admission_invalid")
        return result

    missions: list[ProgramAdmissionMission] = []
    for row in rows:
        required = {"project_id", "standing_issue_id", "sol_agent_id", "astra_agent_id", "luna_agent_id", "qa_agent_ids", "goal_ids", "allowed_prefixes", "excluded_prefixes", "corpus_ids", "profiles", "source_repo", "publisher_repo", "canonical_repo", "canonical_ref", "expected_dev_ref", "source_workspace_id", "source_workspace_root"}
        if type(row) is not dict or set(row) != required:
            raise BridgeError("owner_config_program_admission_invalid")
        project_id, standing_issue_id = row["project_id"], row["standing_issue_id"]
        sol_agent_id, astra_agent_id, luna_agent_id = row["sol_agent_id"], row["astra_agent_id"], row["luna_agent_id"]
        for value in (project_id, standing_issue_id, sol_agent_id, astra_agent_id, luna_agent_id):
            if type(value) is not str or not ID_RE.fullmatch(value):
                raise BridgeError("owner_config_program_admission_invalid")
        target_paths = [row[key] for key in ("source_repo", "publisher_repo", "canonical_repo", "source_workspace_root")]
        if any(type(value) is not str or not Path(value).is_absolute() or ".." in Path(value).parts or Path(value) == Path("/") for value in target_paths):
            raise BridgeError("owner_config_program_admission_invalid")
        if (type(row["source_workspace_id"]) is not str or not ID_RE.fullmatch(row["source_workspace_id"])
            or row["canonical_ref"] not in {"refs/heads/brain/development", "refs/heads/org/development"}
            or row["expected_dev_ref"] not in {"refs/heads/brain/development", "refs/heads/brain/paperclip-integration", "refs/heads/org/development"}):
            raise BridgeError("owner_config_program_admission_invalid")
        def ids(value: Any, *, minimum: int, maximum: int) -> tuple[str, ...]:
            if type(value) is not list or not minimum <= len(value) <= maximum or any(
                type(item) is not str or not ID_RE.fullmatch(item) for item in value
            ) or len(set(value)) != len(value):
                raise BridgeError("owner_config_program_admission_invalid")
            return tuple(value)
        goal_ids = ids(row["goal_ids"], minimum=1, maximum=16)
        qa_agent_ids = ids(row["qa_agent_ids"], minimum=1, maximum=8)
        if len({sol_agent_id, astra_agent_id, luna_agent_id, *qa_agent_ids}) != 3 + len(qa_agent_ids):
            raise BridgeError("owner_config_program_admission_invalid")
        corpus_ids = ids(row["corpus_ids"], minimum=1, maximum=8)
        allowed = paths(row["allowed_prefixes"], prefixes=True)
        excluded = paths(row["excluded_prefixes"], prefixes=True) if row["excluded_prefixes"] else ()
        if any(not any(prefix.startswith(root) or root.startswith(prefix) for root in allowed) for prefix in excluded):
            raise BridgeError("owner_config_program_admission_invalid")
        profile_rows = row["profiles"]
        if type(profile_rows) is not list or not 1 <= len(profile_rows) <= 16:
            raise BridgeError("owner_config_program_admission_invalid")
        profiles: list[ProgramAdmissionProfile] = []
        for profile in profile_rows:
            profile_keys = {"profile_id", "verifier_policy_id", "required_test_paths"}
            if type(profile) is not dict or not profile_keys.issubset(profile) or set(profile) - (profile_keys | {"allowed_path_ids", "allowed_prefixes"}):
                raise BridgeError("owner_config_program_admission_invalid")
            profile_id, policy_id = profile["profile_id"], profile["verifier_policy_id"]
            if any(type(value) is not str or not ID_RE.fullmatch(value) for value in (profile_id, policy_id)):
                raise BridgeError("owner_config_program_admission_invalid")
            exact_paths = ()
            if "allowed_path_ids" in profile:
                try:
                    exact_paths = validate_path_ids(profile["allowed_path_ids"])
                except SourceDeliveryError as exc:
                    raise BridgeError("owner_config_program_admission_invalid") from exc
            profile_prefixes = paths(profile["allowed_prefixes"], prefixes=True) if profile.get("allowed_prefixes") else ()
            if not exact_paths and not profile_prefixes:
                raise BridgeError("owner_config_program_admission_invalid")
            tests = paths(profile["required_test_paths"], prefixes=False)
            if any(not any(path.startswith(root) for root in allowed) for path in (*exact_paths, *tests, *profile_prefixes)) or any(
                any(path.startswith(root) for root in excluded) for path in (*exact_paths, *tests)
            ) or any(not path.startswith(("tests/", "engineering/")) for path in tests):
                raise BridgeError("owner_config_program_admission_invalid")
            profiles.append(ProgramAdmissionProfile(profile_id, exact_paths, profile_prefixes, tests, policy_id))
        if len({item.profile_id for item in profiles}) != len(profiles):
            raise BridgeError("owner_config_program_admission_invalid")
        missions.append(ProgramAdmissionMission(
            project_id, standing_issue_id, sol_agent_id, astra_agent_id, luna_agent_id,
            qa_agent_ids, goal_ids, allowed, excluded, corpus_ids, tuple(profiles),
            Path(row["source_repo"]), Path(row["publisher_repo"]), Path(row["canonical_repo"]),
            row["canonical_ref"], row["expected_dev_ref"], row["source_workspace_id"], Path(row["source_workspace_root"]),
        ))
    if len({mission.project_id for mission in missions}) != len(missions):
        raise BridgeError("owner_config_program_admission_invalid")
    return ProgramAdmissionSpec(tuple(missions))


def _accepted_source_program(
    config: BridgeConfig, payload: dict[str, Any]
) -> tuple[AcceptedSourceProgramSpec, AcceptedSourceProjectionSpec]:
    reader = config.accepted_source_reader
    if reader is None:
        raise BridgeError("accepted_source_reader_not_owner_configured")
    program_id = payload.get("program_issue_id")
    project_id = payload.get("project_id")
    agent_id = payload.get("agent_id")
    corpus_id = payload.get("corpus_id")
    if any(
        type(value) is not str or not ID_RE.fullmatch(value)
        for value in (program_id, project_id, agent_id, corpus_id)
    ):
        raise BridgeError("accepted_source_reader_binding_invalid")
    matches = [
        item
        for item in reader.programs
        if item.program_issue_id == program_id and item.project_id == project_id
    ]
    if len(matches) == 1:
        program = matches[0]
    elif not matches and config.program_admission is not None:
        missions = [item for item in config.program_admission.missions if item.project_id == project_id]
        if len(missions) != 1:
            raise BridgeError("accepted_source_reader_program_not_admitted")
        mission = missions[0]
        program = AcceptedSourceProgramSpec(
            mission.project_id, program_id, mission.sol_agent_id, mission.luna_agent_id,
            mission.qa_agent_ids, mission.corpus_ids,
        )
    else:
        raise BridgeError("accepted_source_reader_program_not_admitted")
    if agent_id not in {program.sol_agent_id, program.luna_agent_id, *program.qa_agent_ids}:
        raise BridgeError("accepted_source_reader_actor_not_admitted")
    if corpus_id not in program.corpus_ids:
        raise BridgeError("accepted_source_reader_projection_not_admitted")
    projection_matches = [item for item in reader.projections if item.corpus_id == corpus_id]
    if len(projection_matches) != 1:
        raise BridgeError("accepted_source_reader_projection_not_admitted")
    return program, projection_matches[0]


def _read_owner_record(path: Path, *, limit: int = 2_000_000) -> dict[str, Any]:
    """Read one private immutable owner receipt without following links."""
    try:
        raw = _read_json(path, limit)
    except BridgeError as exc:
        raise BridgeError("accepted_source_reader_acceptance_unavailable") from exc
    return raw


def _read_only_published_operation_config(
    config: BridgeConfig, operation_key: str, bridge_record: dict[str, Any]
) -> BridgeConfig:
    """Resolve the fixed verifier/roots for a published operation without lifecycle APIs."""
    stage_binding_key = bridge_record.get("source_stage_binding")
    stage_key = bridge_record.get("source_stage_key")
    if stage_binding_key is None:
        if stage_key is not None:
            raise BridgeError("accepted_source_reader_acceptance_unavailable")
        return config
    if type(stage_binding_key) is not str or not HASH_RE.fullmatch(stage_binding_key):
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    if type(stage_key) is not str:
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    try:
        stage = config.source_stage(stage_key)
    except BridgeError as exc:
        raise BridgeError("accepted_source_reader_acceptance_unavailable") from exc
    binding = bridge_record.get("binding")
    base_sha = binding.get("base_sha") if type(binding) is dict else None
    if type(base_sha) is not str or not SHA_RE.fullmatch(base_sha):
        raise BridgeError("accepted_source_reader_acceptance_unavailable")

    records: list[tuple[int, dict[str, Any]]] = []
    for attempt in range(1, _stage_attempt_limit(stage) + 1):
        path = config.bridge_journal_dir / f"operation-{_stage_journal_key(stage.stage_key, attempt)}.json"
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BridgeError("accepted_source_reader_acceptance_unavailable") from exc
        record = _read_owner_record(path)
        if record.get("stage_binding_key") == stage_binding_key:
            records.append((attempt, record))
    if len(records) != 1:
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    attempt, record = records[0]
    expected_schema = (
        "paperclip-source-stage-binding-v1"
        if attempt == 1
        else f"paperclip-source-stage-binding-attempt-v{attempt}"
    )
    if (
        record.get("schema") != expected_schema
        or _record_attempt(record) != attempt
        or record.get("stage_key") != stage.stage_key
        or record.get("owner_config_digest") != config.owner_config_digest
        or record.get("path_ids") != list(stage.path_ids)
        or record.get("verifier_policy_id") != stage.verifier_policy_id
        or record.get("workspace_id") != stage.workspace_id
        or record.get("lead_issue_id") != (stage.lead_issue_id or config.finite_lead_issue_id)
        or record.get("lead_agent_id") != (stage.lead_agent_id or config.finite_lead_agent_id)
        or record.get("operation_key") != operation_key
        or record.get("candidate_sha") != bridge_record.get("candidate", {}).get("candidate_sha")
        or record.get("source_issue_id") != binding.get("issue_id")
        or record.get("state") != "active"
    ):
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    try:
        source_repo = _stage_source_repo(config, stage, record)
        return config.for_source_stage(
            stage,
            source_issue_id=record["source_issue_id"],
            reviewer_issue_id=record["review_issue_id"],
            base_sha=base_sha,
            canonical_base_sha=record.get("canonical_base_sha"),
            source_repo=source_repo,
        )
    except (BridgeError, KeyError, TypeError) as exc:
        raise BridgeError("accepted_source_reader_acceptance_unavailable") from exc


def _validate_canonical_acceptance(
    config: BridgeConfig, operation_key: str, projected_sha: str, bridge_record: dict[str, Any]
) -> tuple[str, str]:
    canonical = bridge_record.get("canonical")
    if type(canonical) is not dict:
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    receipt = canonical.get("receipt")
    acknowledgement = canonical.get("ack")
    candidate_sha = canonical.get("candidate")
    receipt_digest = canonical.get("receipt_digest")
    if (
        canonical.get("status") != "published"
        or type(candidate_sha) is not str
        or not SHA_RE.fullmatch(candidate_sha)
        or type(receipt_digest) is not str
        or not HASH_RE.fullmatch(receipt_digest)
        or canonical.get("approved_candidate") != candidate_sha
        or canonical.get("approved_receipt_digest") != receipt_digest
        or type(receipt) is not dict
        or type(acknowledgement) is not dict
        or acknowledgement.get("status") != "reconciled"
        or acknowledgement.get("candidate") != candidate_sha
        or acknowledgement.get("receipt_digest") != receipt_digest
        or acknowledgement.get("ref") not in {
            "refs/heads/brain/development",
            "refs/heads/org/development",
        }
        or receipt.get("schema") != "paperclip-canonical-content-v1"
        or receipt.get("candidate") != candidate_sha
        or receipt.get("receipt_digest") != receipt_digest
        or receipt.get("ref") != acknowledgement.get("ref")
        or set(receipt) != {
            "schema", "repo", "ref", "expected_tip", "tree", "candidate", "files", "receipt_digest"
        }
        or type(receipt.get("repo")) is not str
        or (config.canonical_repo is not None and receipt["repo"] != str(config.canonical_repo))
        or (config.canonical_ref is not None and receipt["ref"] != config.canonical_ref)
        or type(receipt.get("files")) is not list
        or not receipt["files"]
        or any(
            type(row) is not dict
            or set(row) != {"path", "mode", "old", "new", "sha256"}
            or row.get("mode") != "100644"
            or type(row.get("path")) is not str
            or type(row.get("sha256")) is not str
            or not HASH_RE.fullmatch(row["sha256"])
            or type(row.get("old")) is not str
            or not SHA_RE.fullmatch(row["old"])
            or type(row.get("new")) is not str
            or not SHA_RE.fullmatch(row["new"])
            for row in receipt["files"]
        )
    ):
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    unsigned_receipt = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if hashlib.sha256(_canonical(unsigned_receipt)).hexdigest() != receipt_digest:
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    if bridge_record.get("operation_key") != operation_key:
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    return candidate_sha, receipt_digest


def _published_acceptance_binding(
    config: BridgeConfig, projection: AcceptedSourceProjectionSpec, projected_sha: str
) -> tuple[str, str] | None:
    """Find the unique paired bridge/GitGate ACK for the live sanitized publisher tip."""
    directory = config.bridge_journal_dir
    if directory.is_symlink() or not directory.is_dir():
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    directory_info = directory.stat()
    if directory_info.st_uid != os.getuid() or directory_info.st_mode & 0o077:
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise BridgeError("accepted_source_reader_acceptance_unavailable") from exc
    operation_entries = [
        item for item in entries if item.name.startswith("operation-") and item.name.endswith(".json")
    ]
    if len(operation_entries) > 8192:
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    matches: list[tuple[str, dict[str, Any]]] = []
    for path in operation_entries:
        operation_key = path.name[len("operation-") : -len(".json")]
        if not OPERATION_RE.fullmatch(operation_key) or path.is_symlink():
            raise BridgeError("accepted_source_reader_acceptance_unavailable")
        bridge_record = _read_owner_record(path)
        candidate = bridge_record.get("candidate")
        if type(candidate) is dict and candidate.get("candidate_sha") == projected_sha:
            matches.append((operation_key, bridge_record))
    if not matches:
        return None
    if len(matches) != 1:
        raise BridgeError("accepted_source_reader_acceptance_ambiguous")
    operation_key, bridge_record = matches[0]
    binding = bridge_record.get("binding")
    operation_config = _read_only_published_operation_config(config, operation_key, bridge_record)
    try:
        expected_publisher = operation_config.publisher_repo.resolve(strict=True)
        projection_repository = projection.repository.resolve(strict=True)
    except OSError as exc:
        raise BridgeError("accepted_source_reader_acceptance_unavailable") from exc
    if (
        bridge_record.get("schema") != "paperclip-autonomous-operation-v1"
        or bridge_record.get("state") != "published"
        or type(binding) is not dict
        or hashlib.sha256(
            _canonical([binding.get("company_id"), binding.get("issue_id"), binding.get("run_id")])
        ).hexdigest() != operation_key
        or binding.get("company_id") != config.company_id
        or binding.get("worker_agent_id") != config.worker_agent_id
        or binding.get("reviewer_agent_id") != config.reviewer_agent_id
        or binding.get("owner_config_digest") != config.owner_config_digest
        or binding.get("active_ref") != operation_config.expected_dev_ref
        or projection.ref != operation_config.expected_dev_ref
        or projection_repository != expected_publisher
        or binding.get("owner_roots") != list(operation_config.owner_roots)
        or binding.get("policy_digest") != operation_config.policy().digest
        or binding.get("issue_id") != operation_config.source_issue_id
        or bridge_record.get("source_snapshot_sha256") is None
        or type(bridge_record.get("source_snapshot_sha256")) is not str
        or not HASH_RE.fullmatch(bridge_record["source_snapshot_sha256"])
    ):
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    broker_path = config.broker_journal_dir / f"{operation_key}.json"
    broker_record = _read_owner_record(broker_path)
    broker_binding = broker_record.get("binding")
    broker_candidate = broker_record.get("candidate")
    approval = broker_record.get("approval")
    if (
        broker_record.get("schema") != "paperclip-git-operation-v1"
        or broker_record.get("operation_key") != operation_key
        or broker_record.get("state") != "published"
        or broker_record.get("publish_ack") not in {"confirmed", "reconciled_exact_tip"}
        or broker_binding != binding
        or type(broker_candidate) is not dict
        or broker_candidate.get("sha") != projected_sha
        or type(approval) is not dict
        or approval.get("approved") is not True
        or approval.get("company_id") != config.company_id
        or approval.get("run_id") != binding.get("run_id")
        or approval.get("candidate_sha") != projected_sha
        or approval.get("reviewer_agent_id") != binding.get("reviewer_agent_id")
        or approval.get("policy_digest") != binding.get("policy_digest")
    ):
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    try:
        gate_receipt = operation_config.gate().load_receipt(broker_record.get("receipt_digest"), projected_sha)
    except Exception as exc:
        raise BridgeError("accepted_source_reader_acceptance_unavailable") from exc
    if gate_receipt.get("result", {}).get("status") != "passed":
        raise BridgeError("accepted_source_reader_acceptance_unavailable")
    return _validate_canonical_acceptance(operation_config, operation_key, projected_sha, bridge_record)


def _resolve_accepted_source_snapshot(
    config: BridgeConfig, projection: AcceptedSourceProjectionSpec
):
    if projection.repository.is_symlink():
        raise BridgeError("accepted_source_reader_projection_unavailable")
    try:
        repository = projection.repository.resolve(strict=True)
        if not repository.is_dir():
            raise BridgeError("accepted_source_reader_projection_unavailable")
        if config.canonical_repo is not None:
            canonical = config.canonical_repo
            if repository == canonical or repository in canonical.parents or canonical in repository.parents:
                raise BridgeError("accepted_source_reader_projection_invalid")
        gate = config.gate()
        projected_sha = gate._ref_sha(repository, projection.ref)
    except (OSError, GitGateError) as exc:
        raise BridgeError("accepted_source_reader_projection_unavailable") from exc
    if type(projected_sha) is not str or not SHA_RE.fullmatch(projected_sha):
        raise BridgeError("accepted_source_reader_projection_unavailable")
    provenance = _published_acceptance_binding(config, projection, projected_sha)
    if provenance is None:
        if projected_sha != projection.bootstrap_projection_sha:
            raise BridgeError("accepted_source_reader_acceptance_unavailable")
        provenance = (projection.bootstrap_source_sha, projection.bootstrap_receipt_digest)
    try:
        snapshot = build_accepted_source_snapshot(
            str(repository),
            projected_sha,
            provenance[0],
            provenance[1],
            projection.allowed_roots,
        )
    except AcceptedSourceReadError as exc:
        raise BridgeError(str(exc)) from exc
    return repository, snapshot


def bind_source_stage(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    if not config.source_stage_catalog:
        raise BridgeError("source_stage_lane_not_configured")
    required = {
        "company_id",
        "project_id",
        "lead_agent_id",
        "lead_issue_id",
        "lead_run_id",
        "stage_key",
        "source_issue_id",
        "source_version",
        "review_issue_id",
        "review_version",
        "predecessor_operation_key",
        "predecessor_source_status",
        "predecessor_source_version",
        "predecessor_review_status",
        "predecessor_review_version",
        "predecessor_source_issue_id",
        "predecessor_review_issue_id",
        "predecessor_dependency_ready",
        "source_instruction_sha256",
        "review_instruction_sha256",
    }
    optional_attempt_fields = {
        "attempt",
        "retry_of_operation_key",
        "retry_of_source_status",
        "retry_of_source_version",
        "retry_of_review_status",
        "retry_of_review_version",
        "retry_of_dependency_ready",
    }
    optional_fields = {"predecessor_lead_issue_id"}
    if (
        type(payload) is not dict
        or not required.issubset(payload)
        or set(payload) - required - optional_attempt_fields - optional_fields
    ):
        raise BridgeError("source_stage_bind_payload_shape_invalid")
    attempt = payload["attempt"] if "attempt" in payload else 1
    if type(attempt) is not int or attempt not in (1, 2, 3):
        raise BridgeError("source_stage_attempt_invalid")
    if attempt == 1 and set(payload) & (optional_attempt_fields - {"attempt"}):
        raise BridgeError("source_stage_retry_unexpected")
    if attempt >= 2 and not (optional_attempt_fields - {"attempt"}).issubset(payload):
        raise BridgeError("source_stage_retry_required")
    stage = config.source_stage(payload.get("stage_key", ""))
    if any(
        payload[name] != expected
        for name, expected in {
            "company_id": config.company_id,
            "project_id": stage.project_id or config.project_id,
            "lead_agent_id": stage.lead_agent_id or config.finite_lead_agent_id,
            "lead_issue_id": stage.lead_issue_id or config.finite_lead_issue_id,
        }.items()
    ):
        raise BridgeError("source_stage_lead_binding_mismatch")
    target_config = _stage_target_config(
        config, stage, stage.initial_base_sha or config.expected_base_sha or "0" * 40
    )
    for name in ("lead_run_id", "stage_key", "source_issue_id", "review_issue_id"):
        if type(payload[name]) is not str or not ID_RE.fullmatch(payload[name]):
            raise BridgeError("source_stage_identity_invalid")
    for name in ("source_version", "review_version"):
        if type(payload[name]) is not int or payload[name] < 0:
            raise BridgeError("source_stage_issue_version_invalid")
    for name in ("source_instruction_sha256", "review_instruction_sha256"):
        if type(payload[name]) is not str or not HASH_RE.fullmatch(payload[name]):
            raise BridgeError("source_stage_instruction_binding_invalid")
    if attempt >= 2:
        if (
            type(payload["retry_of_operation_key"]) is not str
            or not OPERATION_RE.fullmatch(payload["retry_of_operation_key"])
            or payload["retry_of_source_status"] != "done"
            or payload["retry_of_review_status"] != "done"
            or type(payload["retry_of_source_version"]) is not int
            or payload["retry_of_source_version"] < 0
            or type(payload["retry_of_review_version"]) is not int
            or payload["retry_of_review_version"] < 0
            or payload["retry_of_dependency_ready"] is not True
        ):
            raise BridgeError("source_stage_retry_validation_invalid")
    if attempt > _stage_attempt_limit(stage):
        raise BridgeError("source_stage_attempt_limit_exceeded")
    if payload["source_issue_id"] == payload["review_issue_id"]:
        raise BridgeError("source_stage_issue_pair_invalid")
    binding_value: dict[str, Any] = {
        "schema": (
            "paperclip-source-stage-binding-v1"
            if attempt == 1
            else f"paperclip-source-stage-binding-attempt-v{attempt}"
        ),
        "stage_key": stage.stage_key,
        "source_issue_id": payload["source_issue_id"],
        "review_issue_id": payload["review_issue_id"],
        "lead_issue_id": stage.lead_issue_id or config.finite_lead_issue_id,
        "lead_agent_id": stage.lead_agent_id or config.finite_lead_agent_id,
        "lead_run_id": payload["lead_run_id"],
        "source_version": payload["source_version"],
        "review_version": payload["review_version"],
        "source_instruction_sha256": payload["source_instruction_sha256"],
        "review_instruction_sha256": payload["review_instruction_sha256"],
        "workspace_id": stage.workspace_id,
        "path_ids": list(stage.path_ids),
        "verifier_policy_id": stage.verifier_policy_id,
        "owner_config_digest": config.owner_config_digest,
        "predecessor_operation_key": payload["predecessor_operation_key"],
        "predecessor_source_issue_id": payload["predecessor_source_issue_id"],
        "predecessor_review_issue_id": payload["predecessor_review_issue_id"],
        "predecessor_lead_issue_id": payload.get("predecessor_lead_issue_id"),
        "base_sha": stage.initial_base_sha,
        "canonical_base_sha": stage.canonical_expected_tip or config.canonical_expected_tip,
        "state": "bound",
        "qa_intent": None,
        "source_intent": None,
        "operation_key": None,
        "candidate_sha": None,
    }
    if attempt >= 2:
        binding_value["attempt"] = attempt
        binding_value["retry_of_operation_key"] = payload["retry_of_operation_key"]
        if attempt == 3:
            binding_value["retry_of_attempt"] = 2
        binding_value["retry_validation"] = {
            "source_status": payload["retry_of_source_status"],
            "source_version": payload["retry_of_source_version"],
            "review_status": payload["retry_of_review_status"],
            "review_version": payload["retry_of_review_version"],
            "dependency_ready": payload["retry_of_dependency_ready"],
        }
    if stage.predecessor_stage_key is not None:
        operation_key = payload["predecessor_operation_key"]
        if type(operation_key) is not str or not OPERATION_RE.fullmatch(operation_key):
            raise BridgeError("source_stage_predecessor_required")
        previous = _load_journal(config, operation_key)
        previous_binding = previous.get("source_stage_binding")
        prior_stage = (
            _stage_binding(config, previous_binding) if type(previous_binding) is str else None
        )
        prior_spec = config.source_stage(stage.predecessor_stage_key)
        cross_project_predecessor = (prior_spec.project_id or config.project_id) != (
            stage.project_id or config.project_id
        ) or (prior_spec.lead_issue_id or config.finite_lead_issue_id) != (
            stage.lead_issue_id or config.finite_lead_issue_id
        )
        predecessor_native_valid = (
            (
                payload.get("predecessor_lead_issue_id")
                == (prior_spec.lead_issue_id or config.finite_lead_issue_id)
                and payload["predecessor_source_status"] is None
                and payload["predecessor_source_version"] is None
                and payload["predecessor_review_status"] is None
                and payload["predecessor_review_version"] is None
                and payload["predecessor_dependency_ready"] is True
            )
            if cross_project_predecessor
            else (
                payload.get("predecessor_lead_issue_id") is None
                and payload["predecessor_source_status"] == "done"
                and payload["predecessor_review_status"] == "done"
                and type(payload["predecessor_source_version"]) is int
                and payload["predecessor_source_version"] >= 0
                and type(payload["predecessor_review_version"]) is int
                and payload["predecessor_review_version"] >= 0
                and payload["predecessor_dependency_ready"] is True
                and payload["predecessor_source_version"]
                != (prior_stage or {}).get("source_version")
                and payload["predecessor_review_version"]
                != (prior_stage or {}).get("review_version")
            )
        )
        if (
            previous.get("state") != "published"
            or (
                target_config.canonical_repo is not None
                and previous.get("canonical", {}).get("status") != "published"
            )
            or prior_stage is None
            or prior_stage.get("stage_key") != stage.predecessor_stage_key
            or prior_stage.get("operation_key") != operation_key
            or previous.get("candidate", {}).get("operation_key") != operation_key
            or not predecessor_native_valid
        ):
            raise BridgeError("source_stage_predecessor_not_finalized")
        if payload.get("predecessor_source_issue_id") != prior_stage.get(
            "source_issue_id"
        ) or payload.get("predecessor_review_issue_id") != prior_stage.get("review_issue_id"):
            raise BridgeError("source_stage_predecessor_identity_mismatch")
        binding_value["predecessor_lead_issue_id"] = (
            prior_spec.lead_issue_id or config.finite_lead_issue_id
        )
        try:
            binding_value["base_sha"] = previous["candidate"]["candidate_sha"]
        except (KeyError, TypeError):
            raise BridgeError("source_stage_predecessor_candidate_invalid") from None
        if not SHA_RE.fullmatch(binding_value["base_sha"]):
            raise BridgeError("source_stage_predecessor_candidate_invalid")
        if prior_stage.get("candidate_sha") != binding_value["base_sha"]:
            raise BridgeError("source_stage_predecessor_candidate_invalid")
        if target_config.canonical_repo is not None:
            canonical_sha = previous.get("canonical", {}).get("candidate")
            if type(canonical_sha) is not str or not SHA_RE.fullmatch(canonical_sha):
                raise BridgeError("source_stage_predecessor_candidate_invalid")
            binding_value["canonical_base_sha"] = canonical_sha
        cache_path = _trusted_stage_source_cache_path(config, stage, binding_value["base_sha"])
        binding_value["trusted_source_cache"] = str(cache_path)
    else:
        if any(
            payload.get(name) is not None
            for name in (
                "predecessor_operation_key",
                "predecessor_source_status",
                "predecessor_source_version",
                "predecessor_review_status",
                "predecessor_review_version",
                "predecessor_source_issue_id",
                "predecessor_review_issue_id",
                "predecessor_dependency_ready",
                "predecessor_lead_issue_id",
            )
        ):
            raise BridgeError("source_stage_unexpected_predecessor")
    if attempt >= 2:
        retry_attempt = attempt - 1
        retry_operation_key = payload["retry_of_operation_key"]
        try:
            retry_operation = _load_journal(config, retry_operation_key)
        except BridgeError as exc:
            if str(exc) == "owner_file_unavailable":
                raise BridgeError("source_stage_retry_not_admitted") from None
            raise
        retry_binding_key = retry_operation.get("source_stage_binding")
        retry_binding = (
            _stage_binding(config, retry_binding_key) if type(retry_binding_key) is str else None
        )
        retry_candidate = retry_operation.get("candidate")
        retry_candidate_sha = (
            retry_candidate.get("candidate_sha") if type(retry_candidate) is dict else None
        )
        retry_review = retry_operation.get("review")
        retry_canonical = retry_operation.get("canonical")
        canonical_retry_invalid = (
            target_config.canonical_repo is not None
            and (
                type(retry_canonical) is not dict
                or retry_canonical.get("status") != "prepared"
                or type(retry_canonical.get("candidate")) is not str
                or not SHA_RE.fullmatch(retry_canonical["candidate"])
                or type(retry_canonical.get("receipt_digest")) is not str
                or not HASH_RE.fullmatch(retry_canonical["receipt_digest"])
            )
        ) or (target_config.canonical_repo is None and retry_canonical is not None)
        expected_predecessor = {
            key: binding_value.get(key)
            for key in (
                "predecessor_operation_key",
                "predecessor_lead_issue_id",
                "predecessor_source_issue_id",
                "predecessor_review_issue_id",
                "base_sha",
                "canonical_base_sha",
            )
        }
        if (
            retry_operation.get("state") != "review_rejected"
            or type(retry_review) is not dict
            or retry_review.get("approved") is not False
            or retry_binding is None
            or retry_binding.get("stage_key") != stage.stage_key
            or retry_binding.get("owner_config_digest") != config.owner_config_digest
            or retry_binding.get("operation_key") != retry_operation_key
            or retry_binding.get("review_issue_id") == payload["review_issue_id"]
            or retry_binding.get("source_issue_id") == payload["source_issue_id"]
            or retry_binding.get("candidate_sha") != retry_candidate_sha
            or type(retry_candidate_sha) is not str
            or not SHA_RE.fullmatch(retry_candidate_sha)
            or retry_candidate.get("operation_key") != retry_operation_key
            or _record_attempt(retry_binding) != retry_attempt
            or retry_binding.get("base_sha") != binding_value.get("base_sha")
            or retry_binding.get("canonical_base_sha") != binding_value.get("canonical_base_sha")
            or any(retry_binding.get(key) != value for key, value in expected_predecessor.items())
            or canonical_retry_invalid
            or payload["retry_of_source_status"] != "done"
            or payload["retry_of_review_status"] != "done"
            or payload["retry_of_source_version"] <= retry_binding.get("source_version", -1)
            or payload["retry_of_review_version"] <= retry_binding.get("review_version", -1)
        ):
            raise BridgeError("source_stage_retry_not_admitted")
        binding_value["retry_of_stage_binding_key"] = retry_binding["stage_binding_key"]
        binding_value["retry_of_candidate_sha"] = retry_candidate_sha
        if retry_attempt != 1:
            binding_value["retry_of_attempt"] = retry_attempt
        retry_stage_path = _journal_path(config, _stage_journal_key(stage.stage_key, attempt))
        if retry_stage_path.exists():
            existing_retry = _load_journal(config, _stage_journal_key(stage.stage_key, attempt))
            workspace_digest = existing_retry.get("retry_workspace_snapshot_sha256")
            workspace_artifact_digest = existing_retry.get("retry_workspace_artifact_sha256")
            if (
                type(workspace_digest) is not str
                or not HASH_RE.fullmatch(workspace_digest)
                or type(workspace_artifact_digest) is not str
                or not HASH_RE.fullmatch(workspace_artifact_digest)
            ):
                raise BridgeError("source_stage_retry_snapshot_missing")
            binding_value["retry_workspace_snapshot_sha256"] = workspace_digest
            binding_value["retry_workspace_artifact_sha256"] = workspace_artifact_digest
        else:
            if attempt == 3:
                target_config = _stage_target_config(config, stage, binding_value["base_sha"])
                gate = target_config.gate()
                if (
                    gate._ref_sha(
                        target_config.publisher_repo,
                        target_config.expected_dev_ref,
                        missing_ok=True,
                    )
                    != binding_value["base_sha"]
                ):
                    raise BridgeError("source_stage_retry_projected_base_changed")
                if (
                    target_config.canonical_repo is not None
                    and gate._ref_sha(
                        target_config.canonical_repo, target_config.canonical_ref, missing_ok=True
                    )
                    != binding_value["canonical_base_sha"]
                ):
                    raise BridgeError("source_stage_retry_canonical_base_changed")
            snapshot_config = config.for_source_stage(
                stage,
                source_issue_id=retry_binding.get("source_issue_id"),
                reviewer_issue_id=retry_binding.get("review_issue_id"),
                base_sha=binding_value["base_sha"],
                canonical_base_sha=binding_value.get("canonical_base_sha"),
            )
            workspace_digest, workspace_artifact_digest = _snapshot_rejected_workspace(
                snapshot_config, retry_operation, retry_binding, stage
            )
            binding_value["retry_workspace_snapshot_sha256"] = workspace_digest
            binding_value["retry_workspace_artifact_sha256"] = workspace_artifact_digest
    stage_journal_key = _stage_journal_key(stage.stage_key, attempt)
    if _journal_path(config, stage_journal_key).exists():
        with _operation_lock(config, stage_journal_key):
            existing = _load_journal(config, stage_journal_key)
            if any(
                existing.get(key) != value
                for key, value in binding_value.items()
                if key
                not in {"state", "qa_intent", "source_intent", "operation_key", "candidate_sha"}
            ):
                raise BridgeError("source_stage_bind_replay_mismatch")
            if stage.workspace_id == "published":
                cache_path = existing.get("trusted_source_cache")
                cache_tree = existing.get("trusted_source_cache_tree")
                if type(cache_path) is not str or type(cache_tree) is not str:
                    raise BridgeError("source_stage_cache_binding_invalid")
                if (
                    _trusted_stage_source_cache_identity(
                        config, stage, existing["base_sha"], Path(cache_path)
                    )
                    != cache_tree
                ):
                    raise BridgeError("source_stage_cache_changed")
            return {
                "stage_binding_key": existing["stage_binding_key"],
                "attempt": attempt,
                "base_sha": existing["base_sha"],
                "canonical_base_sha": existing.get("canonical_base_sha"),
                "workspace_baseline_sha256": existing.get("workspace_baseline_sha256"),
                "retry_of_operation_key": existing.get("retry_of_operation_key"),
                "retry_of_candidate_sha": existing.get("retry_of_candidate_sha"),
                "retry_workspace_snapshot_sha256": existing.get("retry_workspace_snapshot_sha256"),
                "retry_workspace_artifact_sha256": existing.get("retry_workspace_artifact_sha256"),
                "state": existing["state"],
                **_accepted_base_delivery(config, stage, existing),
            }
    if stage.workspace_id == "published":
        cache_path, cache_tree = _create_trusted_stage_source_cache(
            config, stage, binding_value["base_sha"]
        )
        binding_value["trusted_source_cache"] = str(cache_path)
        binding_value["trusted_source_cache_tree"] = cache_tree
    stage_config = config.for_source_stage(
        stage,
        source_issue_id=payload["source_issue_id"],
        reviewer_issue_id=payload["review_issue_id"],
        base_sha=binding_value["base_sha"],
        canonical_base_sha=binding_value.get("canonical_base_sha"),
    )
    if stage_config.source_workspace_root is not None:
        workspace_repo = (
            Path(binding_value["trusted_source_cache"])
            if stage.workspace_id == "published"
            else stage_config.source_repo
        )
        try:
            binding_value["workspace_baseline_sha256"] = refresh_owner_workspace(
                stage_config.gate(),
                workspace_repo,
                binding_value["base_sha"],
                stage_config.source_workspace_root,
                allowed_paths=stage.path_ids,
            )
        except SourceDeliveryError as exc:
            raise BridgeError(str(exc)) from exc
        if attempt >= 2:
            _restore_rejected_workspace_seed(
                stage_config,
                retry_binding,
                retry_operation,
                binding_value["workspace_baseline_sha256"],
                binding_value["retry_workspace_snapshot_sha256"],
                binding_value["retry_workspace_artifact_sha256"],
            )
    else:
        binding_value["workspace_baseline_sha256"] = None
    binding_key = hashlib.sha256(_canonical(binding_value)).hexdigest()
    binding_value["stage_binding_key"] = binding_key
    stage_key = _stage_journal_key(stage.stage_key, attempt)
    with _operation_lock(config, stage_key):
        path = _journal_path(config, stage_key)
        if path.exists():
            existing = _load_journal(config, stage_key)
            if any(
                existing.get(key) != value
                for key, value in binding_value.items()
                if key
                not in {
                    "state",
                    "qa_intent",
                    "source_intent",
                    "operation_key",
                    "candidate_sha",
                    "workspace_baseline_sha256",
                }
            ):
                raise BridgeError("source_stage_bind_replay_mismatch")
            return {
                "stage_binding_key": existing["stage_binding_key"],
                "attempt": attempt,
                "base_sha": existing["base_sha"],
                "canonical_base_sha": existing.get("canonical_base_sha"),
                "workspace_baseline_sha256": existing.get("workspace_baseline_sha256"),
                "retry_of_operation_key": existing.get("retry_of_operation_key"),
                "retry_of_candidate_sha": existing.get("retry_of_candidate_sha"),
                "retry_workspace_snapshot_sha256": existing.get("retry_workspace_snapshot_sha256"),
                "retry_workspace_artifact_sha256": existing.get("retry_workspace_artifact_sha256"),
                "state": existing["state"],
                **_accepted_base_delivery(config, stage, existing),
            }
        _write_journal(config, stage_key, binding_value, create=True)
    return {
        "stage_binding_key": binding_key,
        "attempt": attempt,
        "base_sha": binding_value["base_sha"],
        "canonical_base_sha": binding_value.get("canonical_base_sha"),
        "workspace_baseline_sha256": binding_value.get("workspace_baseline_sha256"),
        "retry_of_operation_key": binding_value.get("retry_of_operation_key"),
        "retry_of_candidate_sha": binding_value.get("retry_of_candidate_sha"),
        "retry_workspace_snapshot_sha256": binding_value.get("retry_workspace_snapshot_sha256"),
        "retry_workspace_artifact_sha256": binding_value.get("retry_workspace_artifact_sha256"),
        "state": "bound",
        **_accepted_base_delivery(config, stage, binding_value),
    }


def _dynamic_stage_key(
    company_id: str, project_id: str, lead_issue_id: str, profile_id: str, corpus_id: str,
    path_ids: tuple[str, ...], source_issue_id: str, review_issue_id: str,
) -> str:
    digest = hashlib.sha256(_canonical([
        "paperclip-program-source-stage-v2", company_id, project_id, lead_issue_id, profile_id, corpus_id,
        path_ids, source_issue_id, review_issue_id,
    ])).hexdigest()
    return f"DYN-{digest[:32]}"


def bind_program_source_stage(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    required = {"company_id", "project_id", "lead_agent_id", "lead_issue_id", "lead_run_id", "goal_id", "profile_id", "corpus_id", "path_ids", "source_issue_id", "source_version", "review_issue_id", "review_version", "source_instruction_sha256", "review_instruction_sha256"}
    if type(payload) is not dict or set(payload) != required:
        raise BridgeError("program_source_stage_bind_payload_invalid")
    mission_rows = [row for row in (config.program_admission.missions if config.program_admission else ()) if row.project_id == payload.get("project_id")]
    if len(mission_rows) != 1:
        raise BridgeError("program_source_stage_not_owner_admitted")
    mission = mission_rows[0]
    if (payload.get("company_id") != config.company_id or payload.get("lead_agent_id") != mission.sol_agent_id
        or payload.get("goal_id") not in mission.goal_ids or payload.get("corpus_id") not in mission.corpus_ids):
        raise BridgeError("program_source_stage_lead_binding_mismatch")
    profile_rows = [row for row in mission.profiles if row.profile_id == payload.get("profile_id")]
    if len(profile_rows) != 1:
        raise BridgeError("program_source_profile_not_admitted")
    profile = profile_rows[0]
    try:
        path_ids = validate_path_ids(payload.get("path_ids"))
    except SourceDeliveryError as exc:
        raise BridgeError("program_source_proposal_path_invalid") from exc
    proposal = validate_program_source_proposal(config, {key: payload[key] for key in ("company_id", "project_id", "goal_id", "profile_id", "path_ids")})
    if proposal["path_ids"] != list(path_ids):
        raise BridgeError("program_source_proposal_path_invalid")
    for name in ("lead_issue_id", "lead_run_id", "source_issue_id", "review_issue_id"):
        if type(payload[name]) is not str or not ID_RE.fullmatch(payload[name]):
            raise BridgeError("program_source_stage_identity_invalid")
    for name in ("source_version", "review_version"):
        if type(payload[name]) is not int or payload[name] < 0:
            raise BridgeError("program_source_stage_identity_invalid")
    for name in ("source_instruction_sha256", "review_instruction_sha256"):
        if type(payload[name]) is not str or not HASH_RE.fullmatch(payload[name]):
            raise BridgeError("program_source_stage_identity_invalid")
    if payload["source_issue_id"] == payload["review_issue_id"]:
        raise BridgeError("program_source_stage_issue_pair_invalid")
    projection_rows = [item for item in (config.accepted_source_reader.projections if config.accepted_source_reader else ()) if item.corpus_id == payload["corpus_id"]]
    if len(projection_rows) != 1:
        raise BridgeError("program_source_stage_accepted_base_unavailable")
    try:
        projection_repo, accepted = _resolve_accepted_source_snapshot(config, projection_rows[0])
    except BridgeError as exc:
        raise BridgeError("program_source_stage_accepted_base_unavailable") from exc
    if projection_repo.resolve(strict=True) != mission.publisher_repo.resolve(strict=True):
        raise BridgeError("program_source_stage_accepted_base_target_mismatch")
    stage_key = _dynamic_stage_key(
        config.company_id, mission.project_id, payload["lead_issue_id"], profile.profile_id, payload["corpus_id"],
        path_ids, payload["source_issue_id"], payload["review_issue_id"],
    )
    stage = SourceStageSpec(stage_key, "source", accepted.projection_sha, None, path_ids, profile.verifier_policy_id,
        mission.project_id, payload["goal_id"], mission.sol_agent_id, payload["lead_issue_id"],
        mission.source_workspace_id, mission.source_workspace_root, mission.source_repo, mission.publisher_repo,
        mission.canonical_repo, mission.canonical_ref, accepted.canonical_sha, mission.expected_dev_ref)
    stage_config = config.for_source_stage(stage, source_issue_id=payload["source_issue_id"], reviewer_issue_id=payload["review_issue_id"],
        base_sha=accepted.projection_sha, canonical_base_sha=accepted.canonical_sha)
    gate = stage_config.gate()
    try:
        if gate._ref_sha(mission.publisher_repo, mission.expected_dev_ref) != accepted.projection_sha:
            raise BridgeError("program_source_stage_accepted_tip_changed")
        if gate._ref_sha(mission.canonical_repo, mission.canonical_ref) != accepted.canonical_sha:
            raise BridgeError("program_source_stage_canonical_tip_changed")
        if gate._resolve_sha(mission.source_repo, accepted.projection_sha) != accepted.projection_sha:
            raise BridgeError("program_source_stage_accepted_base_unavailable")
    except BridgeError:
        raise
    except Exception as exc:
        raise BridgeError("program_source_stage_accepted_base_unavailable") from exc
    dynamic_spec = {"mission_project_id": mission.project_id, "profile_id": profile.profile_id,
        "corpus_id": payload["corpus_id"], "goal_id": payload["goal_id"], "path_ids": list(path_ids)}
    binding_value: dict[str, Any] = {
        "schema": "paperclip-source-stage-binding-v1", "stage_key": stage_key,
        "source_issue_id": payload["source_issue_id"], "review_issue_id": payload["review_issue_id"],
        "lead_issue_id": payload["lead_issue_id"], "lead_agent_id": mission.sol_agent_id,
        "lead_run_id": payload["lead_run_id"], "source_version": payload["source_version"],
        "review_version": payload["review_version"], "source_instruction_sha256": payload["source_instruction_sha256"],
        "review_instruction_sha256": payload["review_instruction_sha256"], "workspace_id": "source",
        "path_ids": list(path_ids), "verifier_policy_id": profile.verifier_policy_id,
        "owner_config_digest": config.owner_config_digest, "predecessor_operation_key": None,
        "predecessor_source_issue_id": None, "predecessor_review_issue_id": None,
        "predecessor_lead_issue_id": None, "base_sha": accepted.projection_sha,
        "canonical_base_sha": accepted.canonical_sha, "dynamic_stage_spec": dynamic_spec,
        "accepted_projection_sha": accepted.projection_sha,
        "accepted_receipt_digest": accepted.acceptance_receipt_digest,
        "accepted_manifest_digest": accepted.manifest_digest,
        "project_id": mission.project_id,
        "state": "bound", "qa_intent": None, "source_intent": None,
        "operation_key": None, "candidate_sha": None,
    }
    binding_key = hashlib.sha256(_canonical(binding_value)).hexdigest()
    binding_value["stage_binding_key"] = binding_key
    journal_key = _stage_journal_key(stage_key)
    with _operation_lock(config, journal_key):
        path = _journal_path(config, journal_key)
        if path.exists():
            existing = _load_journal(config, journal_key)
            if any(existing.get(key) != value for key, value in binding_value.items() if key not in {"state", "source_intent", "qa_intent", "operation_key", "candidate_sha"}):
                raise BridgeError("source_stage_bind_replay_mismatch")
            return {"stage_key": stage_key, "stage_binding_key": existing["stage_binding_key"], "base_sha": existing["base_sha"],
                "canonical_base_sha": existing.get("canonical_base_sha"), "state": existing["state"]}
        workspace_baseline = refresh_owner_workspace(gate, mission.source_repo, accepted.projection_sha,
            mission.source_workspace_root, allowed_paths=path_ids)
        binding_value["workspace_baseline_sha256"] = workspace_baseline
        _write_journal(config, journal_key, binding_value, create=True)
    return {"stage_key": stage_key, "stage_binding_key": binding_key, "base_sha": accepted.projection_sha,
        "canonical_base_sha": accepted.canonical_sha, "state": "bound",
        "accepted_receipt_digest": accepted.acceptance_receipt_digest}


def _lookup_published_stage_predecessor(
    config: BridgeConfig, payload: dict[str, Any], required: set[str]
) -> dict[str, Any]:
    if set(payload) != required | {"published_predecessor_lookup", "successor_stage_key"}:
        raise BridgeError("source_stage_lookup_payload_shape_invalid")
    predecessor_stage = config.source_stage(payload["stage_key"])
    successor_stage = config.source_stage(payload["successor_stage_key"])
    predecessor_project = predecessor_stage.project_id or config.project_id
    predecessor_lead = predecessor_stage.lead_issue_id or config.finite_lead_issue_id
    successor_project = successor_stage.project_id or config.project_id
    successor_lead = successor_stage.lead_issue_id or config.finite_lead_issue_id
    if (
        payload.get("company_id") != config.company_id
        or payload.get("project_id") != predecessor_project
        or payload.get("lead_agent_id")
        != (predecessor_stage.lead_agent_id or config.finite_lead_agent_id)
        or payload.get("lead_issue_id") != predecessor_lead
        or successor_stage.predecessor_stage_key != predecessor_stage.stage_key
        or (predecessor_project == successor_project and predecessor_lead == successor_lead)
        or predecessor_stage.canonical_repo is None
        or predecessor_stage.canonical_ref is None
    ):
        raise BridgeError("source_stage_predecessor_not_owner_admitted")
    candidates = []
    for candidate_attempt in range(1, _stage_attempt_limit(predecessor_stage) + 1):
        candidate = lookup_source_stage_binding(
            config,
            {
                "company_id": payload["company_id"],
                "project_id": predecessor_project,
                "lead_agent_id": predecessor_stage.lead_agent_id or config.finite_lead_agent_id,
                "lead_issue_id": predecessor_lead,
                "stage_key": predecessor_stage.stage_key,
                "attempt": candidate_attempt,
            },
            include_accepted_base_context=False,
        )
        if type(candidate) is not dict:
            raise BridgeError("source_stage_predecessor_state_unknown")
        if candidate.get("found") is False:
            continue
        binding_is_exact = (
            type(candidate.get("attempt")) is int
            and candidate.get("attempt") == candidate_attempt
            and candidate.get("found") is True
            and type(candidate.get("operation_key")) is str
            and OPERATION_RE.fullmatch(candidate["operation_key"])
            and type(candidate.get("stage_binding_key")) is str
            and HASH_RE.fullmatch(candidate["stage_binding_key"])
            and candidate.get("operation_source_stage_key") == predecessor_stage.stage_key
            and candidate.get("operation_source_stage_binding")
            == candidate.get("stage_binding_key")
            and candidate.get("candidate_stage_binding_key") == candidate.get("stage_binding_key")
            and type(candidate.get("source_issue_id")) is str
            and type(candidate.get("review_issue_id")) is str
        )
        if not binding_is_exact or candidate.get("activation_pending") is not False:
            raise BridgeError("source_stage_predecessor_state_unknown")
        if (
            candidate.get("state") == "active"
            and candidate.get("operation_state") == "review_rejected"
            and candidate.get("operation_review_approved") is False
            and candidate.get("canonical_status") == "prepared"
            and candidate.get("canonical_receipt_ref") == predecessor_stage.canonical_ref
            and type(candidate.get("candidate_sha")) is str
            and SHA_RE.fullmatch(candidate["candidate_sha"])
            and type(candidate.get("canonical_candidate_sha")) is str
            and SHA_RE.fullmatch(candidate["canonical_candidate_sha"])
            and type(candidate.get("canonical_receipt_digest")) is str
            and HASH_RE.fullmatch(candidate["canonical_receipt_digest"])
            and candidate.get("canonical_approved_receipt_digest") is None
        ):
            continue
        if (
            candidate.get("state") == "active"
            and candidate.get("operation_state") == "published"
            and candidate.get("operation_review_approved") is True
            and candidate.get("canonical_status") == "published"
            and candidate.get("canonical_receipt_ref") == predecessor_stage.canonical_ref
            and type(candidate.get("candidate_sha")) is str
            and SHA_RE.fullmatch(candidate["candidate_sha"])
            and type(candidate.get("source_receipt_digest")) is str
            and HASH_RE.fullmatch(candidate["source_receipt_digest"])
            and type(candidate.get("canonical_candidate_sha")) is str
            and SHA_RE.fullmatch(candidate["canonical_candidate_sha"])
            and type(candidate.get("canonical_receipt_digest")) is str
            and HASH_RE.fullmatch(candidate["canonical_receipt_digest"])
            and candidate.get("canonical_approved_receipt_digest")
            == candidate.get("canonical_receipt_digest")
        ):
            candidates.append(candidate)
            continue
        raise BridgeError("source_stage_predecessor_state_unknown")
    if len(candidates) > 1:
        raise BridgeError("source_stage_predecessor_ambiguous")
    if not candidates:
        return {"found": False, "stage_key": predecessor_stage.stage_key}
    return candidates[0]


def lookup_source_stage_binding(
    config: BridgeConfig,
    payload: dict[str, Any],
    *,
    include_accepted_base_context: bool = True,
) -> dict[str, Any]:
    required = {
        "company_id",
        "project_id",
        "lead_agent_id",
        "lead_issue_id",
        "stage_key",
    }
    if type(payload) is dict and payload.get("published_predecessor_lookup") is True:
        return _lookup_published_stage_predecessor(config, payload, required)
    identity_fields = {"source_issue_id", "review_issue_id"}
    optional_fields = identity_fields | {"attempt", "stage_binding_key", "operation_key"}
    if (
        type(payload) is not dict
        or not required.issubset(payload)
        or set(payload) - required - optional_fields
        or (
            not (set(payload) & identity_fields)
            and "stage_binding_key" not in payload
            and "attempt" not in payload
            and "operation_key" not in payload
        )
    ):
        raise BridgeError("source_stage_lookup_payload_shape_invalid")
    requested_operation = payload.get("operation_key")
    if requested_operation is not None and (
        type(requested_operation) is not str
        or not OPERATION_RE.fullmatch(requested_operation)
        or "attempt" in payload
        or "stage_binding_key" in payload
        or identity_fields & set(payload)
    ):
        raise BridgeError("source_stage_lookup_payload_shape_invalid")
    requested_attempt = payload["attempt"] if "attempt" in payload else None
    if "attempt" in payload and (
        type(requested_attempt) is not int or requested_attempt not in (1, 2, 3)
    ):
        raise BridgeError("source_stage_attempt_invalid")
    stage = config.source_stage(payload["stage_key"])
    if any(
        payload.get(key) != expected
        for key, expected in {
            "company_id": config.company_id,
            "project_id": stage.project_id or config.project_id,
            "lead_agent_id": stage.lead_agent_id or config.finite_lead_agent_id,
            "lead_issue_id": stage.lead_issue_id or config.finite_lead_issue_id,
        }.items()
    ):
        raise BridgeError("source_stage_lead_binding_mismatch")
    if requested_attempt is not None and requested_attempt > _stage_attempt_limit(stage):
        raise BridgeError("source_stage_attempt_limit_exceeded")
    if requested_operation is not None:
        matches = []
        for candidate_attempt in range(1, _stage_attempt_limit(stage) + 1):
            journal_key = _stage_journal_key(stage.stage_key, candidate_attempt)
            try:
                _journal_path(config, journal_key).lstat()
            except FileNotFoundError:
                continue
            candidate = _load_journal(config, journal_key)
            if candidate.get("operation_key") == requested_operation:
                matches.append((candidate_attempt, candidate))
        if len(matches) > 1:
            raise BridgeError("source_stage_operation_binding_ambiguous")
        if not matches:
            return {"found": False, "operation_key": requested_operation}
        attempt, record = matches[0]
    elif "stage_binding_key" in payload:
        record = _stage_binding(config, payload["stage_binding_key"])
        attempt = _record_attempt(record)
        if (
            type(attempt) is not int
            or attempt not in (1, 2, 3)
            or record.get("stage_key") != stage.stage_key
            or (requested_attempt is not None and attempt != requested_attempt)
        ):
            raise BridgeError("source_stage_bind_replay_mismatch")
    else:
        attempt = requested_attempt if requested_attempt is not None else 1
        journal_key = _stage_journal_key(stage.stage_key, attempt)
        try:
            _journal_path(config, journal_key).lstat()
        except FileNotFoundError:
            return {"found": False, "attempt": attempt}
        record = _load_journal(config, journal_key)
    if (
        record.get("schema")
        != (
            "paperclip-source-stage-binding-v1"
            if attempt == 1
            else f"paperclip-source-stage-binding-attempt-v{attempt}"
        )
        or record.get("owner_config_digest") != config.owner_config_digest
        or _record_attempt(record) != attempt
        or any(record.get(field) != payload[field] for field in identity_fields if field in payload)
    ):
        raise BridgeError("source_stage_bind_replay_mismatch")
    result = {
        "found": True,
        "attempt": attempt,
        "stage_binding_key": record["stage_binding_key"],
        "source_issue_id": record.get("source_issue_id"),
        "review_issue_id": record.get("review_issue_id"),
        "source_version": record.get("source_version"),
        "review_version": record.get("review_version"),
        "base_sha": record["base_sha"],
        "canonical_base_sha": record.get("canonical_base_sha"),
        "path_ids": list(record.get("path_ids", [])),
        "verifier_policy_id": record.get("verifier_policy_id"),
        "goal_id": record.get("dynamic_stage_spec", {}).get("goal_id") if type(record.get("dynamic_stage_spec")) is dict else None,
        "program_issue_id": record.get("lead_issue_id"),
        "state": record["state"],
        "operation_key": record.get("operation_key"),
        "candidate_sha": record.get("candidate_sha"),
        "retry_of_operation_key": record.get("retry_of_operation_key"),
        "retry_of_attempt": record.get("retry_of_attempt", attempt - 1 if attempt > 1 else None),
        "retry_of_candidate_sha": record.get("retry_of_candidate_sha"),
        "retry_workspace_snapshot_sha256": record.get("retry_workspace_snapshot_sha256"),
        "retry_workspace_artifact_sha256": record.get("retry_workspace_artifact_sha256"),
        "operation_state": None,
        "operation_review_approved": None,
        "canonical_status": None,
        "predecessor_operation_key": record.get("predecessor_operation_key"),
        "predecessor_lead_issue_id": record.get("predecessor_lead_issue_id"),
        "predecessor_source_issue_id": record.get("predecessor_source_issue_id"),
        "predecessor_review_issue_id": record.get("predecessor_review_issue_id"),
        "source_instruction_sha256": record.get("source_instruction_sha256"),
        "review_instruction_sha256": record.get("review_instruction_sha256"),
        "activation_pending": record.get("source_intent") is not None
        and record.get("source_intent", {}).get("acknowledged") is not True,
    }
    if type(record.get("operation_key")) is str:
        operation = _load_journal(config, record["operation_key"])
        review = operation.get("review")
        canonical = operation.get("canonical")
        candidate = operation.get("candidate")
        if (
            operation.get("source_stage_binding") != record.get("stage_binding_key")
            or operation.get("source_stage_key") != stage.stage_key
            or type(candidate) is not dict
            or candidate.get("operation_key") != record.get("operation_key")
            or candidate.get("stage_binding_key") != record.get("stage_binding_key")
            or candidate.get("candidate_sha") != record.get("candidate_sha")
        ):
            raise BridgeError("source_stage_bind_replay_mismatch")
        result["operation_state"] = operation.get("state")
        result["operation_source_stage_binding"] = operation.get("source_stage_binding")
        result["operation_source_stage_key"] = operation.get("source_stage_key")
        result["operation_review_approved"] = (
            review.get("approved") if type(review) is dict else None
        )
        result["canonical_status"] = canonical.get("status") if type(canonical) is dict else None
        result["source_receipt_digest"] = operation.get("receipt_digest")
        result["canonical_candidate_sha"] = (
            canonical.get("candidate") if type(canonical) is dict else None
        )
        result["canonical_receipt_digest"] = (
            canonical.get("receipt_digest") if type(canonical) is dict else None
        )
        result["canonical_approved_receipt_digest"] = (
            canonical.get("approved_receipt_digest") if type(canonical) is dict else None
        )
        result["canonical_receipt_ref"] = (
            canonical.get("receipt", {}).get("ref")
            if type(canonical) is dict and type(canonical.get("receipt")) is dict
            else None
        )
        result["candidate_stage_binding_key"] = (
            candidate.get("stage_binding_key") if type(candidate) is dict else None
        )
    if include_accepted_base_context and record.get("state") in {"bound", "active"}:
        result.update(_accepted_base_delivery(config, stage, record))
    return result


def get_program_source_scope(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    """Return only the public task envelope for one owner-mapped department."""
    if type(payload) is not dict or set(payload) != {"company_id", "project_id", "goal_id"}:
        raise BridgeError("program_source_scope_payload_invalid")
    if payload.get("company_id") != config.company_id:
        raise BridgeError("program_source_scope_not_admitted")
    admission = config.program_admission
    if admission is None:
        raise BridgeError("program_source_scope_not_configured")
    matches = [row for row in admission.missions if row.project_id == payload.get("project_id")]
    if len(matches) != 1 or payload.get("goal_id") not in matches[0].goal_ids:
        raise BridgeError("program_source_scope_not_admitted")
    mission = matches[0]
    return {
        "status": "ok",
        "project_id": mission.project_id,
        "standing_issue_id": mission.standing_issue_id,
        "sol_agent_id": mission.sol_agent_id,
        "astra_agent_id": mission.astra_agent_id,
        "luna_agent_id": mission.luna_agent_id,
        "qa_agent_ids": list(mission.qa_agent_ids),
        "goal_id": payload["goal_id"],
        "allowed_prefixes": list(mission.allowed_prefixes),
        "excluded_prefixes": list(mission.excluded_prefixes),
        "corpus_ids": list(mission.corpus_ids),
        "profiles": [
            {"profile_id": item.profile_id, "allowed_path_ids": list(item.allowed_path_ids),
             "allowed_prefixes": list(item.allowed_prefixes), "required_test_paths": list(item.required_test_paths),
             "verifier_policy_id": item.verifier_policy_id}
            for item in mission.profiles
        ],
    }


def validate_program_source_proposal(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    required = {"company_id", "project_id", "goal_id", "profile_id", "path_ids"}
    if type(payload) is not dict or set(payload) != required:
        raise BridgeError("program_source_proposal_payload_invalid")
    scope = get_program_source_scope(config, {key: payload[key] for key in ("company_id", "project_id", "goal_id")})
    mission = next(item for item in config.program_admission.missions if item.project_id == payload["project_id"])
    profiles = [item for item in mission.profiles if item.profile_id == payload["profile_id"]]
    if len(profiles) != 1:
        raise BridgeError("program_source_profile_not_admitted")
    profile = profiles[0]
    try:
        path_ids = validate_path_ids(payload["path_ids"])
    except SourceDeliveryError as exc:
        raise BridgeError("program_source_proposal_path_invalid") from exc
    allowed = set(profile.allowed_path_ids)
    if profile.allowed_prefixes:
        allowed.update(path for path in path_ids if any(path.startswith(root) for root in profile.allowed_prefixes))
    if any(path not in allowed for path in path_ids) or any(path not in path_ids for path in profile.required_test_paths):
        raise BridgeError("program_source_proposal_outside_verifier_compatibility")
    if any(not any(path.startswith(root) for root in mission.allowed_prefixes) for path in path_ids) or any(
        any(path.startswith(root) for root in mission.excluded_prefixes) for path in path_ids
    ):
        raise BridgeError("program_source_proposal_outside_owner_envelope")
    return {"status": "admitted", "project_id": mission.project_id, "goal_id": payload["goal_id"],
        "profile_id": profile.profile_id, "verifier_policy_id": profile.verifier_policy_id,
        "path_ids": list(path_ids), "required_test_paths": list(profile.required_test_paths)}


def source_stage_activation(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("step") if type(payload) is dict else None
    if action not in {"intent", "ack"}:
        raise BridgeError("source_stage_activation_step_invalid")
    expected_fields = (
        {"step", "stage_binding_key", "issue_id", "observed_status", "observed_version"}
        if action == "intent"
        else {"step", "stage_binding_key", "issue_id", "mutation_status", "mutation_status_version"}
    )
    if set(payload) != expected_fields:
        raise BridgeError("source_stage_activation_payload_shape_invalid")
    record = _stage_binding(config, payload.get("stage_binding_key"))
    if payload.get("issue_id") != record["source_issue_id"]:
        raise BridgeError("source_stage_activation_issue_mismatch")
    expected_prior = record["source_version"]
    target_status = "todo"
    field = "source_intent"
    stage_key = _stage_record_key(record)
    with _operation_lock(config, stage_key):
        record = _stage_binding(config, payload["stage_binding_key"])
        if action == "intent":
            if (
                payload.get("observed_status") != "backlog"
                or type(payload.get("observed_version")) is not int
                or payload.get("observed_version") != expected_prior
            ):
                raise BridgeError("source_stage_activation_observation_mismatch")
            desired = {
                "issue_id": payload["issue_id"],
                "status": target_status,
                "prior_version": expected_prior,
                "acknowledged": False,
            }
            current = record.get(field)
            if current is not None and current != desired:
                raise BridgeError("source_stage_activation_intent_mismatch")
            if current is None:
                record[field] = desired
                _write_journal(config, stage_key, record, create=False)
                return {
                    "intent": "created",
                    "issue_id": payload["issue_id"],
                    "status": target_status,
                }
            return {
                "intent": "reconcile_required",
                "issue_id": payload["issue_id"],
                "status": target_status,
            }
        intent = record.get(field)
        if type(intent) is not dict or intent.get("issue_id") != payload.get("issue_id"):
            raise BridgeError("source_stage_activation_intent_missing")
        version = payload.get("mutation_status_version")
        if (
            payload.get("mutation_status") != target_status
            or type(version) is not int
            or version != intent["prior_version"] + 1
        ):
            raise BridgeError("source_stage_activation_ack_mismatch")
        intent["acknowledged"] = True
        intent["ack_version"] = version
        record[field] = intent
        record["state"] = "active"
        _write_journal(config, stage_key, record, create=False)
        return {
            "intent": "acknowledged",
            "issue_id": payload["issue_id"],
            "status": target_status,
            "state": record["state"],
        }


def _bounded_candidate_diff(candidate: Candidate) -> str:
    """Return a complete text-only diff under a hard child file-size limit."""
    command = [
        "/usr/bin/git",
        "--no-pager",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-C",
        str(candidate.checkout),
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-color",
        "--full-index",
        "--no-renames",
        "--unified=80",
        f"{candidate.integration_tip}..{candidate.sha}",
        "--",
    ]
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }

    def limit_child_files() -> None:
        resource.setrlimit(
            resource.RLIMIT_FSIZE, (MAX_REVIEW_DIFF_BYTES + 1, MAX_REVIEW_DIFF_BYTES + 1)
        )

    with tempfile.TemporaryFile(mode="w+b") as output, tempfile.TemporaryFile(mode="w+b") as errors:
        try:
            completed = subprocess.run(
                command,
                cwd=candidate.checkout,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=errors,
                env=env,
                check=False,
                timeout=30,
                preexec_fn=limit_child_files,
            )
        except subprocess.TimeoutExpired:
            raise BridgeError("candidate_diff_timed_out") from None
        output.seek(0)
        raw = output.read(MAX_REVIEW_DIFF_BYTES + 2)
        errors.seek(0)
        error = errors.read(MAX_GIT_ERROR_BYTES + 1)
    if len(raw) > MAX_REVIEW_DIFF_BYTES:
        raise BridgeError("candidate_diff_exceeds_review_limit")
    if completed.returncode != 0:
        raise BridgeError(f"candidate_diff_failed:{hashlib.sha256(error).hexdigest()}")
    try:
        diff = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise BridgeError("candidate_diff_not_utf8") from None
    if "Binary files " in diff or "GIT binary patch" in diff:
        raise BridgeError("binary_candidate_diff_not_reviewable")
    return diff


def _source_preflight_inspection(
    config: BridgeConfig, journal: dict[str, Any]
) -> tuple[dict[str, Any], str] | None:
    stage_binding_key = journal.get("source_stage_binding")
    if stage_binding_key is None:
        return None
    if type(stage_binding_key) is not str or not HASH_RE.fullmatch(stage_binding_key):
        raise BridgeError("source_preflight_stage_binding_invalid")
    record = _stage_binding(config, stage_binding_key)
    stage = config.source_stage(record.get("stage_key", ""))
    stage_config = config.for_source_stage(
        stage,
        source_issue_id=record["source_issue_id"],
        reviewer_issue_id=record["review_issue_id"],
        base_sha=record["base_sha"],
        canonical_base_sha=record.get("canonical_base_sha"),
        source_repo=_stage_source_repo(config, stage, record),
    )
    binding = journal.get("binding")
    candidate = journal.get("candidate")
    content_check_id = journal.get("preflight_check_id")
    if (
        journal.get("schema") != "paperclip-autonomous-operation-v1"
        or journal.get("state")
        not in {
            "prepared",
            "review_custodied",
            "review_rejected",
            "projection_published",
            "published",
            "canonical_publish_intent",
            "canonical_publish_held",
        }
        or type(binding) is not dict
        or type(candidate) is not dict
        or type(content_check_id) is not str
        or not HASH_RE.fullmatch(content_check_id)
        or record.get("owner_config_digest") != config.owner_config_digest
        or record.get("stage_key") != journal.get("source_stage_key")
        or record.get("operation_key") != journal.get("operation_key")
        or record.get("candidate_sha") != candidate.get("candidate_sha")
        or record.get("source_issue_id") != binding.get("issue_id")
        or record.get("base_sha") != binding.get("base_sha")
        or record.get("base_sha") != candidate.get("base_sha")
        or record.get("canonical_base_sha") != (journal.get("canonical") or {}).get("expected_tip")
        or binding.get("policy_digest") != stage_config.policy().digest
        or candidate.get("policy_digest") != stage_config.policy().digest
        or candidate.get("preflight_check_id") != content_check_id
        or type(journal.get("source_snapshot_sha256")) is not str
        or not HASH_RE.fullmatch(journal["source_snapshot_sha256"])
    ):
        raise BridgeError("source_preflight_operation_binding_mismatch")
    try:
        snapshot_document = _load_preflight_json(config, "snapshot", content_check_id)
        intent = _load_preflight_json(config, "intent", content_check_id)
        result = _load_preflight_json(config, "result", content_check_id)
    except BridgeError as exc:
        raise BridgeError("source_preflight_receipt_unavailable") from exc
    if (
        set(snapshot_document) != {"schema", "snapshot_sha256", "files"}
        or snapshot_document.get("schema") != "paperclip-source-preflight-snapshot-v1"
        or type(snapshot_document.get("files")) is not list
    ):
        raise BridgeError("source_preflight_snapshot_invalid")
    try:
        snapshot = snapshot_records(snapshot_document["files"], stage_config.allowed_scope)
    except SourceDeliveryError as exc:
        raise BridgeError("source_preflight_snapshot_invalid") from exc
    snapshot_sha256 = snapshot.digest
    snapshot_artifact_sha256 = hashlib.sha256(_canonical(snapshot_document)).hexdigest()
    policy_digest = stage_config.policy().digest
    operation_key = journal.get("operation_key")
    run_id = binding.get("run_id")
    expected_check_id = hashlib.sha256(
        _canonical(
            [
                "finite-preflight-v1",
                config.owner_config_digest,
                stage_binding_key,
                record["source_issue_id"],
                run_id,
                record["base_sha"],
                record.get("canonical_base_sha"),
                policy_digest,
                snapshot_sha256,
            ]
        )
    ).hexdigest()
    file_manifest = [
        {"path_id": item.path_id, "size": item.size, "sha256": item.sha256}
        for item in snapshot.files
    ]
    if (
        snapshot_document.get("snapshot_sha256") != snapshot_sha256
        or snapshot_sha256 != journal.get("source_snapshot_sha256")
        or journal.get("source_files") != file_manifest
        or expected_check_id != content_check_id
        or set(intent)
        != {
            "schema",
            "content_check_id",
            "owner_config_digest",
            "stage_binding_key",
            "source_issue_id",
            "run_id",
            "base_sha",
            "canonical_base_sha",
            "policy_digest",
            "snapshot_sha256",
            "snapshot_artifact_sha256",
        }
        or intent.get("schema") != "paperclip-source-preflight-intent-v1"
        or intent.get("content_check_id") != content_check_id
        or intent.get("owner_config_digest") != config.owner_config_digest
        or intent.get("stage_binding_key") != stage_binding_key
        or intent.get("source_issue_id") != record.get("source_issue_id")
        or intent.get("run_id") != run_id
        or intent.get("base_sha") != record.get("base_sha")
        or intent.get("canonical_base_sha") != record.get("canonical_base_sha")
        or intent.get("policy_digest") != policy_digest
        or intent.get("snapshot_sha256") != snapshot_sha256
        or intent.get("snapshot_artifact_sha256") != snapshot_artifact_sha256
        or result.get("schema") != "paperclip-source-preflight-result-v1"
        or result.get("status") != "passed"
        or result.get("content_check_id") != content_check_id
        or result.get("snapshot_sha256") != snapshot_sha256
        or result.get("snapshot_artifact_sha256") != snapshot_artifact_sha256
        or result.get("policy_digest") != policy_digest
        or type(result.get("commands_sha256")) is not str
        or not HASH_RE.fullmatch(result["commands_sha256"])
        or candidate.get("operation_key") != operation_key
    ):
        raise BridgeError("source_preflight_receipt_mismatch")
    receipt_sha256 = hashlib.sha256(_canonical(result)).hexdigest()
    phase = (
        "pending_after_independent_QA"
        if journal.get("state") == "prepared"
        else "passed_before_publication"
        if journal.get("state")
        in {
            "projection_published",
            "published",
            "canonical_publish_intent",
            "canonical_publish_held",
        }
        else "not_run_review_rejected"
        if journal.get("state") == "review_rejected"
        else None
    )
    if phase is None:
        raise BridgeError("source_final_verifier_phase_unavailable")
    return {
        "status": "passed",
        "content_check_id": content_check_id,
        "snapshot_sha256": snapshot_sha256,
        "policy_digest": policy_digest,
        "receipt_sha256": receipt_sha256,
        "commands_sha256": result["commands_sha256"],
    }, phase


def inspect_candidate(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    operation_key = payload.get("operation_key")
    if type(operation_key) is not str or not OPERATION_RE.fullmatch(operation_key):
        raise BridgeError("operation_key_invalid")
    with _operation_lock(config, operation_key):
        journal = _load_journal(config, operation_key)
        source_preflight = _source_preflight_inspection(config, journal)
        operation_config = _operation_config(config, operation_key)
        return _inspect_candidate_locked(operation_config, payload, source_preflight)


def _inspect_candidate_locked(
    config: BridgeConfig,
    payload: dict[str, Any],
    source_preflight: tuple[dict[str, Any], str] | None = None,
) -> dict[str, Any]:
    if set(payload) != {
        "company_id",
        "project_id",
        "reviewer_agent_id",
        "reviewer_run_id",
        "reviewer_issue_id",
        "operation_key",
        "candidate_sha",
    }:
        raise BridgeError("inspect_payload_shape_invalid")
    if any(
        payload.get(key) != expected
        for key, expected in {
            "company_id": config.company_id,
            "project_id": config.project_id,
            "reviewer_agent_id": config.reviewer_agent_id,
            "reviewer_issue_id": config.reviewer_issue_id,
        }.items()
    ):
        raise BridgeError("reviewer_actor_or_issue_binding_mismatch")
    reviewer_run_id = payload.get("reviewer_run_id")
    operation_key = payload.get("operation_key")
    candidate_sha = payload.get("candidate_sha")
    if type(reviewer_run_id) is not str or not ID_RE.fullmatch(reviewer_run_id):
        raise BridgeError("reviewer_run_identity_invalid")
    if type(operation_key) is not str or not OPERATION_RE.fullmatch(operation_key):
        raise BridgeError("operation_key_invalid")
    if type(candidate_sha) is not str or not SHA_RE.fullmatch(candidate_sha):
        raise BridgeError("candidate_sha_invalid")
    journal = _load_journal(config, operation_key)
    binding_data = journal.get("binding")
    if type(binding_data) is not dict:
        raise BridgeError("operation_binding_invalid")
    binding = OperationBinding(
        company_id=config.company_id,
        issue_id=config.source_issue_id,
        run_id=binding_data.get("run_id", ""),
        worker_agent_id=config.worker_agent_id,
        reviewer_agent_id=config.reviewer_agent_id,
        base_sha=binding_data.get("base_sha", ""),
        submitted_sha=binding_data.get("submitted_sha", ""),
        scope=_operation_scope(config, journal),
        policy_digest=config.policy().digest,
        active_ref=config.expected_dev_ref,
        owner_config_digest=config.owner_config_digest,
        owner_roots=config.owner_roots,
    )
    if binding.as_dict() != binding_data or binding.operation_key != operation_key:
        raise BridgeError("operation_binding_mismatch")
    if (
        type(journal.get("candidate")) is not dict
        or journal["candidate"].get("candidate_sha") != candidate_sha
    ):
        raise BridgeError("candidate_does_not_match_prepared_journal")
    broker = config.broker()
    broker_record, candidate = broker.recover_candidate(binding)
    if candidate.sha != candidate_sha:
        raise BridgeError("candidate_does_not_match_prepared_journal")
    gate = config.gate()
    receipt_digest = broker_record.get("receipt_digest")
    receipt = gate.load_receipt(receipt_digest, candidate.sha) if receipt_digest else None
    receipt_summary = (
        None
        if receipt is None
        else {
            "policy_hash": receipt["policy_hash"],
            "result": receipt["result"],
        }
    )
    diff = _bounded_candidate_diff(candidate)
    canonical_details = None
    canonical_record = journal.get("canonical")
    if canonical_record is not None:
        if (
            config.canonical_repo is None
            or type(canonical_record) is not dict
            or type(canonical_record.get("receipt")) is not dict
            or canonical_record.get("candidate") != canonical_record["receipt"].get("candidate")
        ):
            raise BridgeError("canonical_receipt_custody_invalid")
        try:
            canonical_state = canonical_publisher.inspect(
                config.canonical_repo,
                config.canonical_ref,
                canonical_record["receipt"],
                allowed_paths=_operation_scope(config, journal),
            )
            if canonical_record.get("status") == "prepared" and (
                canonical_state.get("ref_state") != "prepared"
                or canonical_state.get("files_state") != "old"
            ):
                raise BridgeError("canonical_inspection_state_invalid")
            canonical_diff_raw = gate._run(
                config.canonical_repo,
                [
                    "diff",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-renames",
                    canonical_record["expected_tip"],
                    canonical_record["candidate"],
                    "--",
                    *_operation_scope(config, journal),
                ],
            ).stdout
            if len(canonical_diff_raw) > MAX_REVIEW_DIFF_BYTES:
                raise BridgeError("canonical_review_diff_too_large")
            canonical_diff = canonical_diff_raw.decode("utf-8", errors="strict")
        except (Exception, UnicodeDecodeError) as exc:
            raise BridgeError(f"canonical_inspection_failed:{type(exc).__name__}") from exc
        canonical_details = {
            "canonical_candidate_sha": canonical_record["candidate"],
            "canonical_receipt_digest": canonical_record["receipt_digest"],
            "canonical_base_sha": canonical_record["expected_tip"],
            "canonical_tree": canonical_record["receipt"].get("tree"),
            "canonical_files": canonical_record["receipt"].get("files"),
            "canonical_projected_mapping": {
                "projected_base": canonical_record.get("projected_base"),
                "projected_candidate": canonical_record.get("projected_candidate"),
                "files": canonical_record.get("projected_files"),
            },
            "canonical_diff_sha256": hashlib.sha256(canonical_diff_raw).hexdigest(),
        }
    inspection = {
        "reviewer_agent_id": config.reviewer_agent_id,
        "reviewer_issue_id": config.reviewer_issue_id,
        "reviewer_run_id": reviewer_run_id,
        "target_ref": config.expected_dev_ref,
        "canonical_ref": config.canonical_ref,
        "candidate_sha": candidate.sha,
        "projected_base_sha": candidate.base_sha,
        "projected_integration_tip": candidate.integration_tip,
        "projected_tree": gate._run(candidate.checkout, ["rev-parse", f"{candidate.sha}^{{tree}}"])
        .stdout.decode("ascii")
        .strip(),
        "projected_changed_paths": list(candidate.changed_paths),
        "source_snapshot_sha256": journal.get("source_snapshot_sha256"),
        "source_workspace_baseline_sha256": journal.get("source_workspace_baseline_sha256"),
        "policy_digest": binding.policy_digest,
        "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        **(
            {"source_preflight": source_preflight[0], "final_verifier_phase": source_preflight[1]}
            if source_preflight is not None
            else {}
        ),
        **(canonical_details or {}),
    }
    inspections = journal.get("inspections", [])
    if type(inspections) is not list:
        raise BridgeError("inspection_custody_invalid")
    previous = [
        item
        for item in inspections
        if type(item) is dict and item.get("reviewer_run_id") == reviewer_run_id
    ]
    if previous and previous != [inspection]:
        raise BridgeError("inspection_replay_mismatch")
    if not previous:
        if len(inspections) >= 64:
            raise BridgeError("inspection_custody_full")
        inspections.append(inspection)
        journal["inspections"] = inspections
        _write_journal(config, operation_key, journal, create=False)
    return {
        "operation_key": operation_key,
        "candidate_sha": candidate.sha,
        "submitted_sha": candidate.submitted_sha,
        "base_sha": candidate.base_sha,
        "integration_tip": candidate.integration_tip,
        "changed_paths": list(candidate.changed_paths),
        "policy_digest": binding.policy_digest,
        "target_ref": config.expected_dev_ref,
        "canonical_ref": config.canonical_ref,
        "verifier": {
            "state": broker_record.get("state"),
            "receipt_digest": receipt_digest,
            "receipt": receipt_summary,
        },
        **(
            {"source_preflight": source_preflight[0], "final_verifier_phase": source_preflight[1]}
            if source_preflight is not None
            else {}
        ),
        "diff": diff,
        **({"canonical_diff": canonical_diff, **canonical_details} if canonical_details else {}),
        "inspection_digest": hashlib.sha256(_canonical(inspection)).hexdigest(),
    }


@dataclass(frozen=True)
class BridgeConfig:
    company_id: str
    project_id: str
    worker_agent_id: str
    reviewer_agent_id: str
    source_issue_id: str | None
    reviewer_issue_id: str | None
    source_repo: Path
    worker_checkout: Path | None
    publisher_repo: Path
    broker_journal_dir: Path
    bridge_journal_dir: Path
    receipt_store: Path
    verifier_scratch_root: Path
    python_executable: Path
    allowed_scope: tuple[str, ...]
    verifier_commands: tuple[tuple[str, ...], ...]
    verifier_env: tuple[tuple[str, str], ...]
    verifier_policy_id: str
    fixed_dev_ref: str
    expected_dev_ref: str
    expected_base_sha: str | None = None
    verifier_runtime_read_roots: tuple[Path, ...] = ()
    verifier_policy_catalog: tuple[VerifierProfile, ...] = ()
    reviewer_parent_issue_id: str | None = None
    finite_lead_agent_id: str | None = None
    finite_lead_issue_id: str | None = None
    source_stage_catalog: tuple[SourceStageSpec, ...] = ()
    canonical_repo: Path | None = None
    canonical_ref: str | None = None
    canonical_expected_tip: str | None = None
    canonical_base_sha: str | None = None
    source_workspace_id: str | None = None
    source_workspace_root: Path | None = None
    accepted_source_reader: AcceptedSourceReaderSpec | None = None
    program_admission: ProgramAdmissionSpec | None = None
    _owner_config_digest: str | None = None
    source_stage_key: str | None = None

    @classmethod
    def load(cls, path: Path) -> BridgeConfig:
        raw = _read_json(path, MAX_CONFIG_BYTES)
        if raw.get("schema") != "paperclip-autonomous-bridge-config-v1":
            raise BridgeError("owner_config_schema_invalid")
        allowed_keys = {
            "schema",
            "company_id",
            "project_id",
            "worker_agent_id",
            "reviewer_agent_id",
            "source_issue_id",
            "reviewer_issue_id",
            "reviewer_parent_issue_id",
            "finite_lead_agent_id",
            "finite_lead_issue_id",
            "source_stage_catalog",
            "verifier_policy_catalog",
            "source_repo",
            "worker_checkout",
            "publisher_repo",
            "broker_journal_dir",
            "bridge_journal_dir",
            "receipt_store",
            "verifier_scratch_root",
            "python_executable",
            "allowed_scope",
            "verifier_commands",
            "verifier_env",
            "verifier_policy_id",
            "fixed_dev_ref",
            "expected_dev_ref",
            "expected_base_sha",
            "verifier_runtime_read_roots",
            "canonical_repo",
            "canonical_ref",
            "canonical_expected_tip",
            "source_workspace_id",
            "source_workspace_root",
            "accepted_source_reader",
            "program_admission",
        }
        if set(raw) - allowed_keys:
            raise BridgeError("owner_config_unknown_fields")
        path_values = (
            "source_repo",
            "publisher_repo",
            "broker_journal_dir",
            "bridge_journal_dir",
            "receipt_store",
            "verifier_scratch_root",
            "python_executable",
        )
        try:
            paths = {key: Path(raw[key]) for key in path_values if key in raw}
            canonical_repo = Path(raw["canonical_repo"]) if "canonical_repo" in raw else None
            source_workspace_root = (
                Path(raw["source_workspace_root"]) if "source_workspace_root" in raw else None
            )
            worker_checkout = Path(raw["worker_checkout"]) if "worker_checkout" in raw else None
        except (KeyError, TypeError):
            raise BridgeError("owner_config_paths_invalid") from None
        if (
            any(not value.is_absolute() for value in paths.values())
            or (worker_checkout is not None and not worker_checkout.is_absolute())
            or (canonical_repo is not None and not canonical_repo.is_absolute())
            or (source_workspace_root is not None and not source_workspace_root.is_absolute())
        ):
            raise BridgeError("owner_config_paths_must_be_absolute")
        source_workspace_id = raw.get("source_workspace_id")
        if (source_workspace_id is None) != (source_workspace_root is None) or (
            source_workspace_id is not None
            and (
                type(source_workspace_id) is not str
                or not ID_RE.fullmatch(source_workspace_id)
                or ".." in source_workspace_root.parts
                or source_workspace_root == Path("/")
            )
        ):
            raise BridgeError("owner_config_source_workspace_invalid")
        canonical_values = (
            canonical_repo,
            raw.get("canonical_ref"),
            raw.get("canonical_expected_tip"),
        )
        if any(value is not None for value in canonical_values) and (
            canonical_repo is None
            or raw.get("canonical_ref")
            not in {"refs/heads/brain/development", "refs/heads/org/development"}
            or type(raw.get("canonical_expected_tip")) is not str
            or not SHA_RE.fullmatch(raw["canonical_expected_tip"])
        ):
            raise BridgeError("owner_config_canonical_binding_invalid")
        ids = (
            "company_id",
            "project_id",
            "worker_agent_id",
            "reviewer_agent_id",
        )
        if any(type(raw.get(key)) is not str or not ID_RE.fullmatch(raw[key]) for key in ids):
            raise BridgeError("owner_config_identity_invalid")
        if raw["worker_agent_id"] == raw["reviewer_agent_id"]:
            raise BridgeError("owner_config_agents_must_be_distinct")
        source_issue = raw.get("source_issue_id")
        reviewer_issue = raw.get("reviewer_issue_id")
        if (source_issue is None) != (reviewer_issue is None) or any(
            type(value) is not str or not ID_RE.fullmatch(value)
            for value in (source_issue, reviewer_issue)
            if value is not None
        ):
            raise BridgeError("owner_config_source_pair_invalid")
        finite_lead_agent_id = raw.get("finite_lead_agent_id")
        finite_lead_issue_id = raw.get("finite_lead_issue_id")
        stage_rows = raw.get("source_stage_catalog", [])
        verifier_rows = raw.get("verifier_policy_catalog", [])
        if type(verifier_rows) is not list or len(verifier_rows) > 32:
            raise BridgeError("owner_config_verifier_catalog_invalid")
        verifier_specs: list[VerifierProfile] = []
        for row in verifier_rows:
            if type(row) is not dict or set(row) != {
                "policy_id",
                "commands",
                "env",
                "runtime_read_roots",
            }:
                raise BridgeError("owner_config_verifier_catalog_invalid")
            policy_id, commands, env, roots = (
                row["policy_id"],
                row["commands"],
                row["env"],
                row["runtime_read_roots"],
            )
            if (
                type(policy_id) is not str
                or not ID_RE.fullmatch(policy_id)
                or type(commands) is not list
                or not commands
                or type(env) is not dict
                or type(roots) is not list
                or any(
                    type(command) is not list
                    or not command
                    or any(type(arg) is not str or not arg or "\x00" in arg for arg in command)
                    or not Path(command[0]).is_absolute()
                    or any(
                        arg not in {"{checkout}", "{candidate_sha}"} and "{" in arg
                        for arg in command
                    )
                    for command in commands
                )
                or any(
                    type(k) is not str or type(v) is not str or "\x00" in k + v
                    for k, v in env.items()
                )
                or set(env) - VERIFIER_ENV_KEYS
                or any(
                    type(root) is not str
                    or not Path(root).is_absolute()
                    or ".." in Path(root).parts
                    or Path(root) in {Path("/"), Path("/Users"), Path("/opt"), Path("/tmp")}
                    for root in roots
                )
            ):
                raise BridgeError("owner_config_verifier_catalog_invalid")
            verifier_specs.append(
                VerifierProfile(
                    policy_id,
                    tuple(tuple(command) for command in commands),
                    tuple(sorted(env.items())),
                    tuple(Path(root) for root in roots),
                )
            )
        if len({profile.policy_id for profile in verifier_specs}) != len(verifier_specs):
            raise BridgeError("owner_config_verifier_catalog_invalid")
        if finite_lead_agent_id is None and finite_lead_issue_id is None and not stage_rows:
            stage_catalog: tuple[SourceStageSpec, ...] = ()
        else:
            if (
                type(finite_lead_agent_id) is not str
                or not ID_RE.fullmatch(finite_lead_agent_id)
                or type(finite_lead_issue_id) is not str
                or not ID_RE.fullmatch(finite_lead_issue_id)
                or type(stage_rows) is not list
                or not 1 <= len(stage_rows) <= 32
            ):
                raise BridgeError("owner_config_source_stage_catalog_invalid")
            stage_specs: list[SourceStageSpec] = []
            workspace_by_lane: dict[tuple[str, str, str], tuple[Any, Any]] = {}
            target_by_stage: dict[str, tuple[Any, ...]] = {}
            for row in stage_rows:
                stage_keys = {
                    "stage_key",
                    "workspace_id",
                    "initial_base_sha",
                    "predecessor_stage_key",
                    "path_ids",
                    "verifier_policy_id",
                }
                stage_optional_keys = {
                    "project_id",
                    "goal_id",
                    "lead_agent_id",
                    "lead_issue_id",
                    "source_workspace_id",
                    "source_workspace_root",
                    "source_repo",
                    "publisher_repo",
                    "canonical_repo",
                    "canonical_ref",
                    "canonical_expected_tip",
                    "expected_dev_ref",
                }
                if (
                    type(row) is not dict
                    or not stage_keys.issubset(row)
                    or set(row) - (stage_keys | stage_optional_keys)
                ):
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                stage_key = row["stage_key"]
                workspace_id = row["workspace_id"]
                initial_base = row["initial_base_sha"]
                predecessor = row["predecessor_stage_key"]
                path_ids_raw = row["path_ids"]
                verifier_policy_id = row["verifier_policy_id"]
                if (
                    type(stage_key) is not str
                    or not ID_RE.fullmatch(stage_key)
                    or workspace_id not in {"source", "published"}
                    or type(path_ids_raw) is not list
                    or type(verifier_policy_id) is not str
                    or not ID_RE.fullmatch(verifier_policy_id)
                ):
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                try:
                    path_ids = validate_path_ids(path_ids_raw)
                except SourceDeliveryError as exc:
                    raise BridgeError("owner_config_source_stage_catalog_invalid") from exc
                lane_ids = (
                    row.get("project_id"),
                    row.get("lead_agent_id"),
                    row.get("lead_issue_id"),
                )
                if any(
                    value is not None and (type(value) is not str or not ID_RE.fullmatch(value))
                    for value in lane_ids
                ):
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                if any(value is not None for value in lane_ids) and not all(
                    value is not None for value in lane_ids
                ):
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                if row.get("goal_id") is not None and (
                    type(row["goal_id"]) is not str or not ID_RE.fullmatch(row["goal_id"])
                ):
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                if any(value is not None for value in lane_ids) and "goal_id" not in row:
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                stage_workspace = workspace_id
                task_workspace_id, workspace_root = (
                    row.get("source_workspace_id"),
                    row.get("source_workspace_root"),
                )
                if (task_workspace_id is None) != (workspace_root is None) or (
                    task_workspace_id is not None
                    and (
                        type(task_workspace_id) is not str
                        or not ID_RE.fullmatch(task_workspace_id)
                        or type(workspace_root) is not str
                        or not Path(workspace_root).is_absolute()
                        or ".." in Path(workspace_root).parts
                        or Path(workspace_root) == Path("/")
                    )
                ):
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                repo_values = tuple(
                    row.get(key) for key in ("source_repo", "publisher_repo", "canonical_repo")
                )
                if any(
                    value is not None and (type(value) is not str or not Path(value).is_absolute())
                    for value in repo_values
                ):
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                if (row.get("source_repo") is None) != (row.get("publisher_repo") is None):
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                lane_ref = row.get("expected_dev_ref")
                if lane_ref is not None and lane_ref not in {
                    "refs/heads/brain/development",
                    "refs/heads/brain/paperclip-integration",
                    "refs/heads/org/development",
                }:
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                canonical_ref = row.get("canonical_ref")
                canonical_tip = row.get("canonical_expected_tip")
                if (canonical_ref is None) != (canonical_tip is None) or (
                    canonical_ref is not None
                    and (
                        canonical_ref
                        not in {"refs/heads/brain/development", "refs/heads/org/development"}
                        or type(canonical_tip) is not str
                        or not SHA_RE.fullmatch(canonical_tip)
                        or row.get("canonical_repo") is None
                    )
                ):
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                if lane_ref == "refs/heads/org/development" and row.get("source_repo") is None:
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                if lane_ref == "refs/heads/org/development" and (
                    canonical_ref != "refs/heads/org/development"
                    or row.get("canonical_repo") is None
                ):
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                if row.get("project_id") not in (None, raw["project_id"]):
                    if task_workspace_id is None:
                        raise BridgeError("owner_config_source_stage_catalog_invalid")
                    if verifier_policy_id != raw.get(
                        "verifier_policy_id"
                    ) and verifier_policy_id not in {
                        profile.policy_id for profile in verifier_specs
                    }:
                        raise BridgeError("owner_config_source_stage_catalog_invalid")
                lane = tuple(
                    row.get(field, raw.get(default))
                    for field, default in (
                        ("project_id", "project_id"),
                        ("goal_id", "goal_id"),
                        ("lead_agent_id", "finite_lead_agent_id"),
                        ("lead_issue_id", "finite_lead_issue_id"),
                    )
                )
                workspace_identity = (task_workspace_id, workspace_root)
                prior_workspace = workspace_by_lane.setdefault(lane, workspace_identity)
                target_identity = tuple(
                    row.get(key, raw.get(key))
                    for key in (
                        "source_repo",
                        "publisher_repo",
                        "canonical_repo",
                        "canonical_ref",
                        "canonical_expected_tip",
                        "expected_dev_ref",
                    )
                )
                if prior_workspace != workspace_identity:
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                if predecessor is None:
                    if (
                        stage_workspace != "source"
                        or type(initial_base) is not str
                        or not SHA_RE.fullmatch(initial_base)
                        or predecessor is not None
                        or (
                            initial_base != raw.get("expected_base_sha")
                            and row.get("source_repo") is None
                            and row.get("expected_dev_ref") is None
                        )
                    ):
                        raise BridgeError("owner_config_source_stage_catalog_invalid")
                else:
                    prior_spec = next(
                        (item for item in stage_specs if item.stage_key == predecessor), None
                    )
                    if (
                        stage_workspace != "published"
                        or initial_base is not None
                        or type(predecessor) is not str
                        or not ID_RE.fullmatch(predecessor)
                        or prior_spec is None
                        or target_by_stage.get(predecessor) != target_identity
                    ):
                        raise BridgeError("owner_config_source_stage_catalog_invalid")
                stage_specs.append(
                    SourceStageSpec(
                        stage_key,
                        stage_workspace,
                        initial_base,
                        predecessor,
                        path_ids,
                        verifier_policy_id,
                        row.get("project_id"),
                        row.get("goal_id"),
                        row.get("lead_agent_id"),
                        row.get("lead_issue_id"),
                        task_workspace_id,
                        Path(workspace_root) if workspace_root is not None else None,
                        Path(row["source_repo"]) if row.get("source_repo") is not None else None,
                        Path(row["publisher_repo"])
                        if row.get("publisher_repo") is not None
                        else None,
                        Path(row["canonical_repo"])
                        if row.get("canonical_repo") is not None
                        else None,
                        canonical_ref,
                        canonical_tip,
                        lane_ref,
                    )
                )
                target_by_stage[stage_key] = target_identity
            if len({stage.stage_key for stage in stage_specs}) != len(stage_specs):
                raise BridgeError("owner_config_source_stage_catalog_invalid")
            stage_catalog = tuple(stage_specs)
            if source_workspace_id is None and any(
                stage.source_workspace_id is None for stage in stage_catalog
            ):
                raise BridgeError("owner_config_source_workspace_required")
        reviewer_parent = raw.get(
            "reviewer_parent_issue_id",
            raw.get("source_issue_id", finite_lead_issue_id),
        )
        if reviewer_parent is not None and (
            type(reviewer_parent) is not str or not ID_RE.fullmatch(reviewer_parent)
        ):
            raise BridgeError("owner_config_reviewer_parent_invalid")
        scope = raw.get("allowed_scope")
        if type(scope) is not list or not scope or any(type(item) is not str for item in scope):
            raise BridgeError("owner_config_scope_invalid")
        commands = raw.get("verifier_commands")
        if type(commands) is not list or not commands:
            raise BridgeError("owner_config_verifier_commands_invalid")
        command_rows: list[tuple[str, ...]] = []
        for command in commands:
            if (
                type(command) is not list
                or not command
                or any(type(arg) is not str or not arg or "\x00" in arg for arg in command)
                or not Path(command[0]).is_absolute()
                or any(
                    arg not in {"{checkout}", "{candidate_sha}"} and "{" in arg for arg in command
                )
            ):
                raise BridgeError("owner_config_verifier_command_invalid")
            command_rows.append(tuple(command))
        env = raw.get("verifier_env", {})
        if type(env) is not dict or any(
            type(k) is not str or type(v) is not str or "\x00" in k + v for k, v in env.items()
        ):
            raise BridgeError("owner_config_verifier_environment_invalid")
        if set(env) - VERIFIER_ENV_KEYS:
            raise BridgeError("owner_config_verifier_environment_invalid")
        runtime_roots_raw = raw.get("verifier_runtime_read_roots", [])
        if type(runtime_roots_raw) is not list or any(
            type(item) is not str
            or not Path(item).is_absolute()
            or ".." in Path(item).parts
            or item != str(Path(item))
            or Path(item) in {Path("/"), Path("/Users"), Path("/opt"), Path("/tmp")}
            for item in runtime_roots_raw
        ):
            raise BridgeError("owner_config_verifier_runtime_roots_invalid")
        runtime_roots = tuple(Path(item) for item in runtime_roots_raw)
        allowed_refs = {
            "refs/heads/brain/paperclip-integration",
            "refs/heads/brain/development",
            "refs/heads/org/development",
        }
        if raw.get("fixed_dev_ref") not in allowed_refs:
            raise BridgeError("owner_config_development_ref_invalid")
        if raw.get("expected_dev_ref") != raw["fixed_dev_ref"]:
            raise BridgeError("owner_config_expected_ref_mismatch")
        expected_base = raw.get("expected_base_sha")
        if raw["fixed_dev_ref"] in OWNER_CAS_REFS and (
            type(expected_base) is not str or not SHA_RE.fullmatch(expected_base)
        ):
            raise BridgeError("owner_config_expected_base_invalid")
        if type(raw.get("verifier_policy_id")) is not str or not ID_RE.fullmatch(
            raw["verifier_policy_id"]
        ):
            raise BridgeError("owner_config_policy_id_invalid")
        reader_spec = _parse_accepted_source_reader(raw.get("accepted_source_reader"))
        program_admission = _parse_program_admission(raw.get("program_admission"))
        if program_admission is not None and any(
            profile.verifier_policy_id not in {item.policy_id for item in verifier_specs}
            and profile.verifier_policy_id != raw.get("verifier_policy_id")
            for mission in program_admission.missions for profile in mission.profiles
        ):
            raise BridgeError("owner_config_program_admission_invalid")
        if program_admission is not None and any(
            mission.luna_agent_id != raw["worker_agent_id"]
            or mission.qa_agent_ids != (raw["reviewer_agent_id"],)
            for mission in program_admission.missions
        ):
            raise BridgeError("owner_config_program_admission_invalid")
        config = cls(
            **{key: raw[key] for key in ids},
            source_issue_id=source_issue,
            reviewer_issue_id=reviewer_issue,
            **paths,
            worker_checkout=worker_checkout,
            allowed_scope=tuple(scope),
            verifier_commands=tuple(command_rows),
            verifier_env=tuple(sorted(env.items())),
            verifier_policy_id=raw["verifier_policy_id"],
            fixed_dev_ref=raw["fixed_dev_ref"],
            expected_dev_ref=raw["expected_dev_ref"],
            expected_base_sha=expected_base,
            verifier_runtime_read_roots=runtime_roots,
            verifier_policy_catalog=tuple(verifier_specs),
            reviewer_parent_issue_id=reviewer_parent,
            finite_lead_agent_id=finite_lead_agent_id,
            finite_lead_issue_id=finite_lead_issue_id,
            source_stage_catalog=stage_catalog,
            canonical_repo=canonical_repo,
            canonical_ref=raw.get("canonical_ref"),
            canonical_expected_tip=raw.get("canonical_expected_tip"),
            source_workspace_id=source_workspace_id,
            source_workspace_root=source_workspace_root,
            accepted_source_reader=reader_spec,
            program_admission=program_admission,
        )
        return replace(config, _owner_config_digest=config._compute_owner_config_digest())

    def policy(self) -> VerificationPolicy:
        return VerificationPolicy(
            commands=self.verifier_commands,
            env=self.verifier_env,
            policy_id=self.verifier_policy_id,
        )

    def gate(self) -> GitGate:
        owner_engineering_paths: tuple[str, ...] = ()
        if self.source_stage_key is not None:
            stage = self.source_stage(self.source_stage_key)
            org_ref = "refs/heads/org/development"
            if (
                stage.expected_dev_ref == org_ref
                and stage.canonical_ref == org_ref
                and self.expected_dev_ref == org_ref
                and self.fixed_dev_ref == org_ref
                and self.canonical_ref == org_ref
                and stage.project_id is not None
                and stage.project_id == self.project_id
                and stage.lead_agent_id is not None
                and stage.lead_agent_id == self.finite_lead_agent_id
                and stage.lead_issue_id is not None
                and stage.lead_issue_id == self.finite_lead_issue_id
                and stage.source_repo is not None
                and stage.source_repo == self.source_repo
                and stage.publisher_repo is not None
                and stage.publisher_repo == self.publisher_repo
                and stage.canonical_repo is not None
                and stage.canonical_repo == self.canonical_repo
                and stage.canonical_expected_tip is not None
            ):
                owner_engineering_paths = tuple(
                    path for path in stage.path_ids if path.startswith("engineering/")
                )
        return GitGate(
            self.source_repo,
            self.publisher_repo,
            self.policy(),
            self.receipt_store,
            remote=None,
            active_ref=self.expected_dev_ref,
            _owner_engineering_paths=owner_engineering_paths,
        )

    def broker(self) -> PaperclipGitBroker:
        gate = self.gate()
        broker = PaperclipGitBroker(gate, self.broker_journal_dir, active_ref=self.expected_dev_ref)
        if broker.active_ref != self.expected_dev_ref:
            raise BridgeError("configured_active_ref_mismatch")
        return broker

    @property
    def owner_roots(self) -> tuple[str, ...]:
        return tuple(
            str(path.resolve(strict=False))
            for path in (
                self.source_repo,
                self.publisher_repo,
                self.broker_journal_dir,
                self.bridge_journal_dir,
                self.receipt_store,
                self.verifier_scratch_root,
                self.python_executable,
                *((self.canonical_repo,) if self.canonical_repo is not None else ()),
                *((self.source_workspace_root,) if self.source_workspace_root is not None else ()),
                *(
                    path
                    for mission in (self.program_admission.missions if self.program_admission else ())
                    for path in (mission.source_repo, mission.publisher_repo, mission.canonical_repo, mission.source_workspace_root)
                ),
                *self.verifier_runtime_read_roots,
            )
        )

    @property
    def owner_config_digest(self) -> str:
        return self._owner_config_digest or self._compute_owner_config_digest()

    @property
    def accepted_source_read_policy_digest(self) -> str:
        if self.accepted_source_reader is None:
            raise BridgeError("accepted_source_reader_not_owner_configured")
        value = {
            "owner_config_digest": self.owner_config_digest,
            "projections": [
                {
                    "corpus_id": item.corpus_id,
                    "repository": str(item.repository),
                    "ref": item.ref,
                    "allowed_roots": item.allowed_roots,
                    "bootstrap_projection_sha": item.bootstrap_projection_sha,
                    "bootstrap_source_sha": item.bootstrap_source_sha,
                    "bootstrap_receipt_digest": item.bootstrap_receipt_digest,
                }
                for item in self.accepted_source_reader.projections
            ],
            "programs": [
                {
                    "project_id": item.project_id,
                    "program_issue_id": item.program_issue_id,
                    "sol_agent_id": item.sol_agent_id,
                    "luna_agent_id": item.luna_agent_id,
                    "qa_agent_ids": item.qa_agent_ids,
                    "corpus_ids": item.corpus_ids,
                }
                for item in self.accepted_source_reader.programs
            ],
        }
        return hashlib.sha256(_canonical(value)).hexdigest()

    def _compute_owner_config_digest(self) -> str:
        value = {
            "company_id": self.company_id,
            "project_id": self.project_id,
            "worker_agent_id": self.worker_agent_id,
            "reviewer_agent_id": self.reviewer_agent_id,
            "source_issue_id": self.source_issue_id,
            "reviewer_issue_id": self.reviewer_issue_id,
            "reviewer_parent_issue_id": self.reviewer_parent_issue_id or self.source_issue_id,
            "finite_lead_agent_id": self.finite_lead_agent_id,
            "finite_lead_issue_id": self.finite_lead_issue_id,
            "source_stage_catalog": [
                {
                    "stage_key": stage.stage_key,
                    "workspace_id": stage.workspace_id,
                    "initial_base_sha": stage.initial_base_sha,
                    "predecessor_stage_key": stage.predecessor_stage_key,
                    "path_ids": stage.path_ids,
                    "verifier_policy_id": stage.verifier_policy_id,
                    "project_id": stage.project_id,
                    "goal_id": stage.goal_id,
                    "lead_agent_id": stage.lead_agent_id,
                    "lead_issue_id": stage.lead_issue_id,
                    "source_workspace_id": stage.source_workspace_id,
                    "source_workspace_root": (
                        str(stage.source_workspace_root) if stage.source_workspace_root else None
                    ),
                    "source_repo": str(stage.source_repo) if stage.source_repo else None,
                    "publisher_repo": str(stage.publisher_repo) if stage.publisher_repo else None,
                    "canonical_repo": str(stage.canonical_repo) if stage.canonical_repo else None,
                    "canonical_ref": stage.canonical_ref,
                    "canonical_expected_tip": stage.canonical_expected_tip,
                    "expected_dev_ref": stage.expected_dev_ref,
                }
                for stage in self.source_stage_catalog
            ],
            "program_admission": None if self.program_admission is None else [
                {
                    "project_id": mission.project_id,
                    "standing_issue_id": mission.standing_issue_id,
                    "sol_agent_id": mission.sol_agent_id,
                    "astra_agent_id": mission.astra_agent_id,
                    "luna_agent_id": mission.luna_agent_id,
                    "qa_agent_ids": mission.qa_agent_ids,
                    "goal_ids": mission.goal_ids,
                    "allowed_prefixes": mission.allowed_prefixes,
                    "excluded_prefixes": mission.excluded_prefixes,
                    "corpus_ids": mission.corpus_ids,
                    "profiles": [
                        {"profile_id": item.profile_id, "allowed_path_ids": item.allowed_path_ids,
                         "allowed_prefixes": item.allowed_prefixes, "required_test_paths": item.required_test_paths,
                         "verifier_policy_id": item.verifier_policy_id}
                        for item in mission.profiles
                    ],
                    "source_repo": str(mission.source_repo), "publisher_repo": str(mission.publisher_repo),
                    "canonical_repo": str(mission.canonical_repo), "canonical_ref": mission.canonical_ref,
                    "expected_dev_ref": mission.expected_dev_ref, "source_workspace_id": mission.source_workspace_id,
                    "source_workspace_root": str(mission.source_workspace_root),
                }
                for mission in self.program_admission.missions
            ],
            "verifier_policy_catalog": [
                {
                    "policy_id": profile.policy_id,
                    "commands": profile.commands,
                    "env": profile.env,
                    "runtime_read_roots": tuple(str(path) for path in profile.runtime_read_roots),
                }
                for profile in self.verifier_policy_catalog
            ],
            "python_executable": str(self.python_executable),
            "owner_roots": self.owner_roots,
            "allowed_scope": self.allowed_scope,
            "verifier_commands": self.verifier_commands,
            "verifier_env": self.verifier_env,
            "verifier_policy_id": self.verifier_policy_id,
            "policy_digest": self.policy().digest,
            "fixed_dev_ref": self.fixed_dev_ref,
            "expected_dev_ref": self.expected_dev_ref,
            "expected_base_sha": self.expected_base_sha,
            "canonical_repo": (
                str(self.canonical_repo.resolve(strict=False)) if self.canonical_repo else None
            ),
            "canonical_ref": self.canonical_ref,
            "canonical_expected_tip": self.canonical_expected_tip,
            "source_workspace_id": self.source_workspace_id,
            "source_workspace_root": (
                str(self.source_workspace_root) if self.source_workspace_root else None
            ),
            "verifier_runtime_read_roots": tuple(
                str(path) for path in self.verifier_runtime_read_roots
            ),
        }
        return hashlib.sha256(_canonical(value)).hexdigest()

    def source_stage(self, stage_key: str) -> SourceStageSpec:
        matches = [stage for stage in self.source_stage_catalog if stage.stage_key == stage_key]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise BridgeError("source_stage_not_owner_admitted")
        if type(stage_key) is str and ID_RE.fullmatch(stage_key):
            try:
                record = _load_journal(self, _stage_journal_key(stage_key))
            except BridgeError:
                record = None
            if record is not None and record.get("dynamic_stage_spec") is not None:
                if record.get("owner_config_digest") != self.owner_config_digest:
                    raise BridgeError("source_stage_owner_config_changed")
                return _dynamic_stage_from_record(self, record)
        raise BridgeError("source_stage_not_owner_admitted")

    def for_source_stage(
        self,
        stage: SourceStageSpec,
        *,
        source_issue_id: str,
        reviewer_issue_id: str,
        base_sha: str,
        canonical_base_sha: str | None = None,
        source_repo: Path | None = None,
    ) -> BridgeConfig:
        profile = next(
            (
                item
                for item in self.verifier_policy_catalog
                if item.policy_id == stage.verifier_policy_id
            ),
            None,
        )
        return replace(
            self,
            _owner_config_digest=self.owner_config_digest,
            project_id=stage.project_id or self.project_id,
            source_repo=source_repo or stage.source_repo or self.source_repo,
            publisher_repo=stage.publisher_repo or self.publisher_repo,
            canonical_repo=stage.canonical_repo or self.canonical_repo,
            canonical_ref=stage.canonical_ref or self.canonical_ref,
            canonical_expected_tip=stage.canonical_expected_tip or self.canonical_expected_tip,
            expected_dev_ref=stage.expected_dev_ref or self.expected_dev_ref,
            fixed_dev_ref=stage.expected_dev_ref or self.fixed_dev_ref,
            finite_lead_agent_id=stage.lead_agent_id or self.finite_lead_agent_id,
            finite_lead_issue_id=stage.lead_issue_id or self.finite_lead_issue_id,
            source_issue_id=source_issue_id,
            reviewer_issue_id=reviewer_issue_id,
            reviewer_parent_issue_id=stage.lead_issue_id or self.finite_lead_issue_id,
            source_workspace_id=stage.source_workspace_id or self.source_workspace_id,
            source_workspace_root=stage.source_workspace_root or self.source_workspace_root,
            allowed_scope=stage.path_ids,
            source_stage_key=stage.stage_key,
            verifier_policy_id=stage.verifier_policy_id,
            verifier_commands=profile.commands if profile is not None else self.verifier_commands,
            verifier_env=profile.env if profile is not None else self.verifier_env,
            verifier_runtime_read_roots=(
                profile.runtime_read_roots
                if profile is not None
                else self.verifier_runtime_read_roots
            ),
            expected_base_sha=base_sha,
            canonical_base_sha=(
                canonical_base_sha
                if canonical_base_sha is not None
                else self.canonical_expected_tip
            ),
        )


def _stage_target_config(
    config: BridgeConfig, stage: SourceStageSpec, base_sha: str
) -> BridgeConfig:
    profile = next(
        (
            item
            for item in config.verifier_policy_catalog
            if item.policy_id == stage.verifier_policy_id
        ),
        None,
    )
    target_ref = stage.expected_dev_ref or config.expected_dev_ref
    return replace(
        config,
        _owner_config_digest=config.owner_config_digest,
        source_repo=stage.source_repo or config.source_repo,
        publisher_repo=stage.publisher_repo or config.publisher_repo,
        canonical_repo=stage.canonical_repo or config.canonical_repo,
        canonical_ref=stage.canonical_ref or config.canonical_ref,
        canonical_expected_tip=stage.canonical_expected_tip or config.canonical_expected_tip,
        expected_dev_ref=target_ref,
        fixed_dev_ref=target_ref,
        expected_base_sha=base_sha,
        verifier_policy_id=stage.verifier_policy_id,
        verifier_commands=profile.commands if profile is not None else config.verifier_commands,
        verifier_env=profile.env if profile is not None else config.verifier_env,
        verifier_runtime_read_roots=(
            profile.runtime_read_roots
            if profile is not None
            else config.verifier_runtime_read_roots
        ),
    )


def _verifier(
    config: BridgeConfig,
    candidate_checkout: Path,
    *,
    content_only: bool = False,
    capture_failure: bool = False,
):
    """Build trusted verifier callback; only fixed commands execute in sandboxed children."""
    config.verifier_scratch_root.mkdir(parents=True, exist_ok=True)
    if config.verifier_scratch_root.is_symlink() or not config.verifier_scratch_root.is_dir():
        raise BridgeError("verifier_scratch_root_invalid")
    scratch = Path(tempfile.mkdtemp(prefix="paperclip-verifier-", dir=config.verifier_scratch_root))
    try:
        os.chmod(scratch, 0o700)
        policy = config.policy()
        python = str(config.python_executable)
        publisher_root = config.publisher_repo.resolve(strict=False)
        dependency_roots = [
            *python_runtime_roots(python),
            *config.verifier_runtime_read_roots,
        ]
        for dependency in dependency_roots:
            root = Path(dependency).resolve(strict=False)
            if (
                root == publisher_root
                or root in publisher_root.parents
                or publisher_root in root.parents
            ):
                raise BridgeError("verifier_runtime_root_overlaps_publisher")
        box = SandboxRunner(
            writable_roots=[scratch],
            read_roots=[candidate_checkout, *dependency_roots],
            home=scratch,
            protected_roots=[
                config.source_repo,
                *(
                    (config.worker_checkout,)
                    if not content_only and config.worker_checkout is not None
                    else ()
                ),
                # The candidate worktree is nested under GitGate's publisher .git directory.
                # The publisher is not a granted read/write root; keeping it out of protected
                # roots lets Seatbelt grant only the exact candidate worktree below it.
                config.broker_journal_dir,
                config.bridge_journal_dir,
                config.receipt_store,
            ],
        )
    except BaseException:
        import shutil

        shutil.rmtree(scratch, ignore_errors=True)
        raise

    def run(
        _checkout: Path, candidate_sha: str, fixed_policy: VerificationPolicy
    ) -> dict[str, Any]:
        if fixed_policy.digest != policy.digest or _checkout != candidate_checkout:
            raise BridgeError("verifier_policy_or_checkout_changed")
        results: list[dict[str, Any]] = []
        for command in policy.commands:
            argv = [
                str(candidate_checkout)
                if arg == "{checkout}"
                else candidate_sha
                if arg == "{candidate_sha}"
                else arg
                for arg in command
            ]
            result = box.run(
                argv,
                cwd=candidate_checkout,
                timeout=90,
                max_output_bytes=200_000,
                env_overrides=dict(policy.env),
            )
            failed = result.returncode != 0 or result.timed_out or result.output_truncated
            if failed and capture_failure:

                def bounded_output(value: str) -> str:
                    return value.encode("utf-8")[:MAX_PREFLIGHT_OUTPUT_BYTES].decode(
                        "utf-8", errors="ignore"
                    )

                return {
                    "status": "preflight_failed",
                    "failure_code": "sandboxed_verifier_failed",
                    "command_index": len(results),
                    "returncode": result.returncode,
                    "timed_out": result.timed_out,
                    "output_truncated": result.output_truncated,
                    "stdout": bounded_output(result.stdout),
                    "stderr": bounded_output(result.stderr),
                    "stdout_sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
                    "stderr_sha256": hashlib.sha256(result.stderr.encode("utf-8")).hexdigest(),
                    "sandbox_profile_sha256": result.profile_sha256,
                    "prior_commands_sha256": hashlib.sha256(_canonical(results)).hexdigest(),
                }
            if failed:
                raise BridgeError("sandboxed_verifier_failed")
            results.append(
                {
                    "returncode": str(result.returncode),
                    "stdout_sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
                    "stderr_sha256": hashlib.sha256(result.stderr.encode("utf-8")).hexdigest(),
                    "sandbox_profile_sha256": result.profile_sha256,
                }
            )
        return {
            "status": "passed",
            "commands_sha256": hashlib.sha256(_canonical(results)).hexdigest(),
        }

    return run, scratch


def _binding(config: BridgeConfig, payload: dict[str, Any]) -> OperationBinding:
    if set(payload) != {
        "company_id",
        "project_id",
        "source_issue_id",
        "worker_agent_id",
        "run_id",
        "base_sha",
        "submitted_sha",
    }:
        raise BridgeError("submit_payload_shape_invalid")
    for name in ("company_id", "project_id", "source_issue_id", "worker_agent_id"):
        expected = {
            "company_id": config.company_id,
            "project_id": config.project_id,
            "source_issue_id": config.source_issue_id,
            "worker_agent_id": config.worker_agent_id,
        }[name]
        if payload.get(name) != expected:
            raise BridgeError("actor_or_issue_binding_mismatch")
    for key in ("run_id",):
        if type(payload.get(key)) is not str or not ID_RE.fullmatch(payload[key]):
            raise BridgeError("actor_run_identity_invalid")
    for key in ("base_sha", "submitted_sha"):
        if type(payload.get(key)) is not str or not SHA_RE.fullmatch(payload[key]):
            raise BridgeError("submitted_sha_invalid")
    if (
        config.expected_dev_ref in OWNER_CAS_REFS
        and payload["base_sha"] != config.expected_base_sha
    ):
        raise BridgeError("development_base_not_owner_admitted")
    if config.worker_checkout is None:
        raise BridgeError("legacy_git_submission_disabled")
    policy = config.policy()
    return OperationBinding(
        company_id=config.company_id,
        issue_id=config.source_issue_id,
        run_id=payload["run_id"],
        worker_agent_id=config.worker_agent_id,
        reviewer_agent_id=config.reviewer_agent_id,
        base_sha=payload["base_sha"],
        submitted_sha=payload["submitted_sha"],
        scope=config.allowed_scope,
        policy_digest=policy.digest,
        active_ref=config.expected_dev_ref,
        owner_config_digest=config.owner_config_digest,
        owner_roots=config.owner_roots,
    )


def prepare_candidate(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    binding = _binding(config, payload)
    with _operation_lock(config, binding.operation_key):
        return _prepare_candidate_locked(config, binding)


def _prepare_candidate_locked(config: BridgeConfig, binding: OperationBinding) -> dict[str, Any]:
    broker = config.broker()
    bridge_path = _journal_path(config, binding.operation_key)
    candidate = broker.prepare(binding, config.worker_checkout)
    result = {
        "operation_key": binding.operation_key,
        "candidate_sha": candidate.sha,
        "submitted_sha": candidate.submitted_sha,
        "base_sha": candidate.base_sha,
        "changed_paths": list(candidate.changed_paths),
        "policy_digest": binding.policy_digest,
        "reviewer_issue_id": config.reviewer_issue_id,
    }
    journal = {
        "schema": "paperclip-autonomous-operation-v1",
        "operation_key": binding.operation_key,
        "state": "prepared",
        "binding": binding.as_dict(),
        "candidate": result,
        "review": None,
    }
    path = bridge_path
    if path.exists():
        existing = _load_journal(config, binding.operation_key)
        if existing.get("binding") != binding.as_dict() or existing.get("candidate") != result:
            raise BridgeError("operation_replay_binding_mismatch")
    else:
        _write_journal(config, binding.operation_key, journal, create=True)
    return result


def prepare_source_content(
    config: BridgeConfig,
    payload: dict[str, Any],
    *,
    stage_binding_key: str | None = None,
    source_stage_key: str | None = None,
    source_workspace_baseline_sha256: str | None = None,
    preflight_check_id: str | None = None,
) -> dict[str, Any]:
    """Prepare one candidate from bounded complete file contents, never worker Git."""
    if type(payload) is not dict or set(payload) != {
        "company_id",
        "project_id",
        "source_issue_id",
        "worker_agent_id",
        "run_id",
        "base_sha",
        "files",
    }:
        raise BridgeError("source_delivery_payload_shape_invalid")
    expected = {
        "company_id": config.company_id,
        "project_id": config.project_id,
        "source_issue_id": config.source_issue_id,
        "worker_agent_id": config.worker_agent_id,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise BridgeError("actor_or_issue_binding_mismatch")
    if type(payload.get("run_id")) is not str or not ID_RE.fullmatch(payload["run_id"]):
        raise BridgeError("actor_run_identity_invalid")
    base_sha = payload.get("base_sha")
    if type(base_sha) is not str or not SHA_RE.fullmatch(base_sha):
        raise BridgeError("source_base_sha_invalid")
    if config.expected_dev_ref in OWNER_CAS_REFS and base_sha != config.expected_base_sha:
        raise BridgeError("development_base_not_owner_admitted")
    content_paths = config.allowed_scope if stage_binding_key is not None else SOURCE_PATHS
    try:
        snapshot = snapshot_records(payload["files"], content_paths)
    except SourceDeliveryError as exc:
        raise BridgeError(str(exc)) from exc
    operation_key = hashlib.sha256(
        _canonical([config.company_id, config.source_issue_id, payload["run_id"]])
    ).hexdigest()
    with _operation_lock(config, operation_key):
        journal_path = _journal_path(config, operation_key)
        if journal_path.exists():
            journal = _load_journal(config, operation_key)
            if journal.get("source_snapshot_sha256") != snapshot.digest:
                raise BridgeError("source_delivery_replay_content_mismatch")
            if journal.get("binding", {}).get("base_sha") != base_sha:
                raise BridgeError("source_delivery_replay_binding_mismatch")
            if journal.get("source_stage_binding") != stage_binding_key:
                raise BridgeError("source_delivery_replay_stage_mismatch")
            if journal.get("preflight_check_id") != preflight_check_id:
                raise BridgeError("source_delivery_replay_preflight_mismatch")
            return journal["candidate"]
        return _prepare_source_content_locked(
            config,
            payload,
            snapshot,
            operation_key,
            stage_binding_key=stage_binding_key,
            source_stage_key=source_stage_key,
            source_workspace_baseline_sha256=source_workspace_baseline_sha256,
            preflight_check_id=preflight_check_id,
        )


def _prepare_source_content_locked(
    config,
    payload,
    snapshot,
    operation_key,
    *,
    stage_binding_key=None,
    source_stage_key=None,
    source_workspace_baseline_sha256=None,
    preflight_check_id=None,
):
    content_paths = config.allowed_scope if stage_binding_key is not None else SOURCE_PATHS
    gate = config.gate()
    try:
        projection = materialize_owner_projection(
            gate, payload["base_sha"], snapshot, allowed_paths=content_paths
        )
    except SourceDeliveryError as exc:
        raise BridgeError(str(exc)) from exc
    canonical_record = None
    if config.canonical_repo is not None:
        canonical_base_sha = config.canonical_base_sha or config.canonical_expected_tip
        if (
            config.canonical_ref
            not in {"refs/heads/brain/development", "refs/heads/org/development"}
            or not canonical_base_sha
        ):
            discard_projection(gate, projection)
            raise BridgeError("canonical_owner_binding_invalid")
        try:
            old_blobs = {
                item.path_id: gate._run(
                    config.canonical_repo,
                    ["rev-parse", f"{canonical_base_sha}:{item.path_id}"],
                )
                .stdout.decode("ascii")
                .strip()
                for item in snapshot.files
            }
            new_blobs = {
                item.path_id: gate._run(
                    config.canonical_repo, ["hash-object", "--stdin"], input=item.data
                )
                .stdout.decode("ascii")
                .strip()
                for item in snapshot.files
            }
            projected_files = []
            for item in snapshot.files:
                projected_old = (
                    gate._run(
                        config.source_repo,
                        ["rev-parse", f"{projection.base_sha}:{item.path_id}"],
                    )
                    .stdout.decode("ascii")
                    .strip()
                )
                projected_new = (
                    gate._run(
                        config.source_repo,
                        ["rev-parse", f"{projection.submitted_sha}:{item.path_id}"],
                    )
                    .stdout.decode("ascii")
                    .strip()
                )
                projected_files.append(
                    {
                        "path": item.path_id,
                        "projected_old_blob": projected_old,
                        "projected_new_blob": projected_new,
                        "sha256": item.sha256,
                    }
                )
            receipt = canonical_publisher.prepare(
                config.canonical_repo,
                config.canonical_ref,
                canonical_base_sha,
                old_blobs,
                new_blobs,
                {item.path_id: item.data for item in snapshot.files},
                allowed_paths=content_paths,
            )
        except Exception as exc:
            discard_projection(gate, projection)
            raise BridgeError(f"canonical_prepare_failed:{type(exc).__name__}") from exc
        canonical_record = {
            "status": "prepared",
            "expected_tip": canonical_base_sha,
            "candidate": receipt["candidate"],
            "receipt_digest": receipt["receipt_digest"],
            "receipt": receipt,
            "projected_base": projection.base_sha,
            "projected_candidate": projection.submitted_sha,
            "projected_files": projected_files,
        }
    policy = config.policy()
    binding = OperationBinding(
        company_id=config.company_id,
        issue_id=config.source_issue_id,
        run_id=payload["run_id"],
        worker_agent_id=config.worker_agent_id,
        reviewer_agent_id=config.reviewer_agent_id,
        base_sha=projection.base_sha,
        submitted_sha=projection.submitted_sha,
        scope=content_paths,
        policy_digest=policy.digest,
        active_ref=config.expected_dev_ref,
        owner_config_digest=config.owner_config_digest,
        owner_roots=config.owner_roots,
    )
    try:
        candidate = config.broker().prepare(binding, projection.checkout)
    finally:
        discard_projection(gate, projection)
    result = {
        "operation_key": operation_key,
        "candidate_sha": candidate.sha,
        "submitted_sha": candidate.submitted_sha,
        "base_sha": candidate.base_sha,
        "changed_paths": list(candidate.changed_paths),
        "policy_digest": binding.policy_digest,
        "reviewer_issue_id": config.reviewer_issue_id,
        "source_snapshot_sha256": snapshot.digest,
        **({"preflight_check_id": preflight_check_id} if preflight_check_id is not None else {}),
        **(
            {
                "canonical_candidate_sha": canonical_record["candidate"],
                "canonical_receipt_digest": canonical_record["receipt_digest"],
            }
            if canonical_record is not None
            else {}
        ),
        **({"stage_binding_key": stage_binding_key} if stage_binding_key is not None else {}),
    }
    journal = {
        "schema": "paperclip-autonomous-operation-v1",
        "operation_key": operation_key,
        "state": "prepared",
        "binding": binding.as_dict(),
        "candidate": result,
        "source_snapshot_sha256": snapshot.digest,
        "source_stage_binding": stage_binding_key,
        "source_stage_key": (source_stage_key if source_stage_key is not None else None),
        "source_workspace_baseline_sha256": source_workspace_baseline_sha256,
        "preflight_check_id": preflight_check_id,
        "source_files": [
            {"path_id": item.path_id, "size": item.size, "sha256": item.sha256}
            for item in snapshot.files
        ],
        "review": None,
        "canonical": canonical_record,
    }
    _write_journal(config, operation_key, journal, create=True)
    return result


def _preflight_snapshot_document(snapshot) -> dict[str, Any]:
    return {
        "schema": "paperclip-source-preflight-snapshot-v1",
        "snapshot_sha256": snapshot.digest,
        "files": [
            {
                "path_id": item.path_id,
                "size": item.size,
                "sha256": item.sha256,
                "content": item.data.decode("utf-8"),
            }
            for item in snapshot.files
        ],
    }


def _preflight_source_stage_content(
    config: BridgeConfig,
    stage_config: BridgeConfig,
    binding: dict[str, Any],
    payload: dict[str, Any],
    snapshot,
) -> dict[str, Any]:
    policy = stage_config.policy()
    check_id = hashlib.sha256(
        _canonical(
            [
                "finite-preflight-v1",
                config.owner_config_digest,
                binding["stage_binding_key"],
                payload["source_issue_id"],
                payload["run_id"],
                binding["base_sha"],
                binding.get("canonical_base_sha"),
                policy.digest,
                snapshot.digest,
            ]
        )
    ).hexdigest()
    with _operation_lock(config, check_id):
        try:
            result = _load_preflight_json(config, "result", check_id)
        except BridgeError as exc:
            if str(exc) != "owner_file_unavailable":
                raise
        else:
            if (
                result.get("content_check_id") != check_id
                or result.get("snapshot_sha256") != snapshot.digest
                or result.get("policy_digest") != policy.digest
                or result.get("status") not in {"passed", "preflight_failed"}
            ):
                raise BridgeError("source_preflight_result_identity_mismatch")
            intent = _load_preflight_json(config, "intent", check_id)
            snapshot_document = _load_preflight_json(config, "snapshot", check_id)
            if (
                intent.get("content_check_id") != check_id
                or intent.get("snapshot_sha256") != snapshot.digest
                or intent.get("snapshot_artifact_sha256")
                != hashlib.sha256(_canonical(snapshot_document)).hexdigest()
                or snapshot_document != _preflight_snapshot_document(snapshot)
            ):
                raise BridgeError("source_preflight_artifact_identity_mismatch")
            return result
        try:
            intent = _load_preflight_json(config, "intent", check_id)
        except BridgeError as exc:
            if str(exc) != "owner_file_unavailable":
                raise
            intent = None
        if intent is not None:
            snapshot_document = _load_preflight_json(config, "snapshot", check_id)
            if (
                intent.get("content_check_id") != check_id
                or intent.get("snapshot_sha256") != snapshot.digest
                or intent.get("owner_config_digest") != config.owner_config_digest
                or intent.get("stage_binding_key") != binding["stage_binding_key"]
                or intent.get("source_issue_id") != payload["source_issue_id"]
                or intent.get("run_id") != payload["run_id"]
                or intent.get("base_sha") != binding["base_sha"]
                or intent.get("canonical_base_sha") != binding.get("canonical_base_sha")
                or intent.get("policy_digest") != policy.digest
                or intent.get("snapshot_artifact_sha256")
                != hashlib.sha256(_canonical(snapshot_document)).hexdigest()
                or snapshot_document != _preflight_snapshot_document(snapshot)
            ):
                raise BridgeError("source_preflight_intent_identity_mismatch")
            return {
                "status": "preflight_unknown",
                "content_check_id": check_id,
                "snapshot_sha256": snapshot.digest,
                "policy_digest": policy.digest,
                "reconcile_required": True,
            }
        snapshot_document = _preflight_snapshot_document(snapshot)
        snapshot_artifact_sha256 = _persist_preflight_json(
            config, "snapshot", check_id, snapshot_document
        )
        intent = {
            "schema": "paperclip-source-preflight-intent-v1",
            "content_check_id": check_id,
            "owner_config_digest": config.owner_config_digest,
            "stage_binding_key": binding["stage_binding_key"],
            "source_issue_id": payload["source_issue_id"],
            "run_id": payload["run_id"],
            "base_sha": binding["base_sha"],
            "canonical_base_sha": binding.get("canonical_base_sha"),
            "policy_digest": policy.digest,
            "snapshot_sha256": snapshot.digest,
            "snapshot_artifact_sha256": snapshot_artifact_sha256,
        }
        _persist_preflight_json(config, "intent", check_id, intent)
        candidate_parent = config.verifier_scratch_root / "finite-preflight-worktrees"
        candidate_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if candidate_parent.is_symlink():
            raise BridgeError("source_preflight_checkout_root_invalid")
        candidate_parent = candidate_parent.resolve(strict=True)
        projection = None
        verifier_scratch = None
        try:
            projection = materialize_owner_projection(
                stage_config.gate(),
                binding["base_sha"],
                snapshot,
                checkout_parent=candidate_parent,
                allowed_paths=stage_config.allowed_scope,
            )
            verifier, verifier_scratch = _verifier(
                stage_config, projection.checkout, content_only=True, capture_failure=True
            )
            verification = verifier(projection.checkout, projection.submitted_sha, policy)
            receipt = {
                "schema": "paperclip-source-preflight-result-v1",
                "content_check_id": check_id,
                "snapshot_sha256": snapshot.digest,
                "snapshot_artifact_sha256": snapshot_artifact_sha256,
                "policy_digest": policy.digest,
                **verification,
            }
        except Exception:
            return {
                "status": "preflight_unknown",
                "content_check_id": check_id,
                "snapshot_sha256": snapshot.digest,
                "policy_digest": policy.digest,
                "reconcile_required": True,
            }
        finally:
            if verifier_scratch is not None:
                shutil.rmtree(verifier_scratch, ignore_errors=True)
            if projection is not None:
                discard_projection(stage_config.gate(), projection)
        try:
            _persist_preflight_json(config, "result", check_id, receipt)
        except BridgeError:
            return {
                "status": "preflight_unknown",
                "content_check_id": check_id,
                "snapshot_sha256": snapshot.digest,
                "policy_digest": policy.digest,
                "reconcile_required": True,
            }
        return receipt


def _snapshot_base_hashes(stage_config: BridgeConfig, base_sha: str, snapshot) -> dict[str, str] | None:
    """Return exact accepted-base blob IDs when every delivered byte is unchanged."""
    if type(base_sha) is not str or not SHA_RE.fullmatch(base_sha):
        raise BridgeError("source_stage_base_invalid")
    hashes: dict[str, str] = {}
    try:
        gate = stage_config.gate()
        resolved = gate._resolve_sha(stage_config.source_repo, base_sha)
        if resolved != base_sha:
            raise BridgeError("source_stage_base_invalid")
        for item in snapshot.files:
            blob = gate._run(stage_config.source_repo, ["rev-parse", f"{base_sha}:{item.path_id}"])
            blob_id = blob.stdout.decode("ascii", errors="strict").strip()
            base_bytes = gate._run(stage_config.source_repo, ["show", f"{base_sha}:{item.path_id}"]).stdout
            if hashlib.sha256(base_bytes).hexdigest() != item.sha256 or base_bytes != item.data:
                return None
            if not SHA_RE.fullmatch(blob_id):
                raise BridgeError("source_stage_base_invalid")
            hashes[item.path_id] = blob_id
    except BridgeError:
        raise
    except Exception:
        # A missing/non-text baseline is not classified as no-change; normal fixed
        # source verification remains responsible for its ordinary failure result.
        return None
    return hashes


def prepare_source_stage_content(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "company_id",
        "project_id",
        "source_issue_id",
        "worker_agent_id",
        "run_id",
        "stage_key",
        "stage_binding_key",
        "source_instruction_sha256",
        "files",
    }
    if type(payload) is not dict or set(payload) != required:
        raise BridgeError("source_stage_delivery_payload_shape_invalid")
    record = _stage_binding(config, payload["stage_binding_key"])
    if (
        record["stage_key"] != payload["stage_key"]
        or record["source_issue_id"] != payload["source_issue_id"]
        or record["state"] != "active"
    ):
        raise BridgeError("source_stage_not_active")
    if payload["source_instruction_sha256"] != record.get("source_instruction_sha256"):
        raise BridgeError("source_stage_instruction_binding_mismatch")
    stage = config.source_stage(record["stage_key"])
    stage_config = config.for_source_stage(
        stage,
        source_issue_id=record["source_issue_id"],
        reviewer_issue_id=record["review_issue_id"],
        base_sha=record["base_sha"],
        canonical_base_sha=record.get("canonical_base_sha"),
        source_repo=_stage_source_repo(config, stage, record),
    )
    content_payload = {
        key: payload[key]
        for key in (
            "company_id",
            "project_id",
            "source_issue_id",
            "worker_agent_id",
            "run_id",
            "files",
        )
    }
    content_payload["base_sha"] = record["base_sha"]
    try:
        snapshot = snapshot_records(content_payload["files"], stage_config.allowed_scope)
    except SourceDeliveryError as exc:
        raise BridgeError(str(exc)) from exc
    base_file_hashes = _snapshot_base_hashes(stage_config, record["base_sha"], snapshot)
    if base_file_hashes is not None:
        policy = stage_config.policy()
        check_id = hashlib.sha256(_canonical([
            "finite-preflight-v1", config.owner_config_digest, record["stage_binding_key"],
            payload["source_issue_id"], payload["run_id"], record["base_sha"],
            record.get("canonical_base_sha"), policy.digest, snapshot.digest,
        ])).hexdigest()
        # An intent/result from an earlier version remains unknown until reconciled.
        for kind in ("intent", "result"):
            try:
                _load_preflight_json(config, kind, check_id)
            except BridgeError as exc:
                if str(exc) != "owner_file_unavailable":
                    raise
            else:
                raise BridgeError("source_preflight_reconciliation_required")
        if record.get("operation_key") is not None or (
            record.get("source_intent") is not None
            and record.get("source_intent", {}).get("acknowledged") is not True
        ):
            raise BridgeError("source_stage_reconciliation_required")
        return {
            "status": "unchanged_not_submitted",
            "stage_key": record["stage_key"],
            "stage_binding_key": record["stage_binding_key"],
            "base_sha": record["base_sha"],
            "snapshot_sha256": snapshot.digest,
            "path_hashes": base_file_hashes,
            "baseline_action": "verify_source_stage_unchanged",
        }
    preflight = _preflight_source_stage_content(
        config, stage_config, record, content_payload, snapshot
    )
    if preflight.get("status") != "passed":
        return preflight
    result = prepare_source_content(
        stage_config,
        content_payload,
        stage_binding_key=payload["stage_binding_key"],
        source_stage_key=record["stage_key"],
        source_workspace_baseline_sha256=record.get("workspace_baseline_sha256"),
        preflight_check_id=preflight["content_check_id"],
    )
    stage_journal_key = _stage_record_key(record)
    with _operation_lock(config, stage_journal_key):
        current = _stage_binding(config, payload["stage_binding_key"])
        if current.get("operation_key") not in (None, result["operation_key"]):
            raise BridgeError("source_stage_operation_replay_mismatch")
        if current.get("candidate_sha") not in (None, result["candidate_sha"]):
            raise BridgeError("source_stage_candidate_replay_mismatch")
        current["operation_key"] = result["operation_key"]
        current["candidate_sha"] = result["candidate_sha"]
        _write_journal(config, stage_journal_key, current, create=False)
    return result


def inspect_bound_source_stage(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "company_id",
        "project_id",
        "reviewer_agent_id",
        "reviewer_run_id",
        "reviewer_issue_id",
        "stage_key",
        "stage_binding_key",
        "review_instruction_sha256",
    }
    if type(payload) is not dict or set(payload) != required:
        raise BridgeError("source_stage_review_payload_shape_invalid")
    record = _stage_binding(config, payload["stage_binding_key"])
    if (
        record.get("stage_key") != payload["stage_key"]
        or record.get("review_issue_id") != payload["reviewer_issue_id"]
        or payload.get("company_id") != config.company_id
        or payload.get("project_id")
        != (config.source_stage(record["stage_key"]).project_id or config.project_id)
        or payload.get("reviewer_agent_id") != config.reviewer_agent_id
        or payload.get("review_instruction_sha256") != record.get("review_instruction_sha256")
        or type(payload.get("reviewer_run_id")) is not str
        or not ID_RE.fullmatch(payload["reviewer_run_id"])
    ):
        raise BridgeError("source_stage_reviewer_binding_mismatch")
    operation_key = record.get("operation_key")
    if type(operation_key) is not str or not OPERATION_RE.fullmatch(operation_key):
        raise BridgeError("source_stage_candidate_not_prepared")
    operation_config = _operation_config(config, operation_key)
    candidate_sha = record.get("candidate_sha")
    if type(candidate_sha) is not str or not SHA_RE.fullmatch(candidate_sha):
        raise BridgeError("source_stage_candidate_not_prepared")
    return inspect_candidate(
        operation_config,
        {
            "company_id": operation_config.company_id,
            "project_id": operation_config.project_id,
            "reviewer_agent_id": config.reviewer_agent_id,
            "reviewer_run_id": payload["reviewer_run_id"],
            "reviewer_issue_id": record["review_issue_id"],
            "operation_key": operation_key,
            "candidate_sha": candidate_sha,
        },
    )


def _accepted_stage_path_hashes(stage_config: BridgeConfig, stage: SourceStageSpec, base_sha: str) -> dict[str, str]:
    gate = stage_config.gate()
    hashes: dict[str, str] = {}
    for path in stage.path_ids:
        try:
            output = gate._run(stage_config.source_repo, ["rev-parse", f"{base_sha}:{path}"])
            value = output.stdout.decode("ascii", errors="strict").strip()
        except Exception as exc:
            raise BridgeError("source_stage_accepted_base_unavailable") from exc
        if not SHA_RE.fullmatch(value):
            raise BridgeError("source_stage_accepted_base_unavailable")
        hashes[path] = value
    return hashes


def _stage_has_preflight_activity(config: BridgeConfig, stage_binding_key: str) -> bool:
    directory = _preflight_dir(config)
    paths = list(directory.glob("intent-*.json"))
    if len(paths) > 4096:
        raise BridgeError("source_preflight_reconciliation_required")
    for path in paths:
        if path.is_symlink():
            raise BridgeError("source_preflight_reconciliation_required")
        value = _read_json(path, MAX_PREFLIGHT_ARTIFACT_BYTES)
        if value.get("stage_binding_key") == stage_binding_key:
            return True
    return False


def verify_source_stage_unchanged(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    required = {"company_id", "project_id", "worker_agent_id", "run_id", "source_issue_id", "stage_key", "stage_binding_key", "source_instruction_sha256"}
    if type(payload) is not dict or set(payload) != required:
        raise BridgeError("source_stage_no_change_payload_invalid")
    record = _stage_binding(config, payload["stage_binding_key"])
    stage = config.source_stage(record["stage_key"])
    if (record.get("state") != "active" or record.get("stage_key") != payload["stage_key"]
        or record.get("source_issue_id") != payload["source_issue_id"]
        or record.get("source_instruction_sha256") != payload["source_instruction_sha256"]
        or payload.get("company_id") != config.company_id
        or payload.get("project_id") != (stage.project_id or config.project_id)
        or payload.get("worker_agent_id") != config.worker_agent_id
        or type(payload.get("run_id")) is not str or not ID_RE.fullmatch(payload["run_id"])):
        raise BridgeError("source_stage_no_change_binding_mismatch")
    if record.get("operation_key") is not None or record.get("candidate_sha") is not None or (
        record.get("source_intent") is not None
        and record.get("source_intent", {}).get("acknowledged") is not True
    ):
        raise BridgeError("source_stage_reconciliation_required")
    if _stage_has_preflight_activity(config, record["stage_binding_key"]):
        raise BridgeError("source_stage_reconciliation_required")
    stage_config = config.for_source_stage(stage, source_issue_id=record["source_issue_id"], reviewer_issue_id=record["review_issue_id"],
        base_sha=record["base_sha"], canonical_base_sha=record.get("canonical_base_sha"),
        source_repo=_stage_source_repo(config, stage, record))
    policy = stage_config.policy()
    check_id = hashlib.sha256(_canonical([
        "accepted-base-no-change-v1", config.owner_config_digest, record["stage_binding_key"],
        payload["source_issue_id"], payload["run_id"], record["base_sha"], policy.digest,
        list(stage.path_ids),
    ])).hexdigest()
    journal_key = _stage_record_key(record)
    with _operation_lock(config, journal_key):
        current = _stage_binding(config, record["stage_binding_key"])
        gate = stage_config.gate()
        try:
            current_tip = gate._ref_sha(stage_config.publisher_repo, stage_config.expected_dev_ref)
        except Exception as exc:
            raise BridgeError("source_stage_accepted_tip_unavailable") from exc
        if current_tip != record["base_sha"]:
            raise BridgeError("source_stage_accepted_tip_changed")
        previous = current.get("no_change_result")
        if previous is not None:
            if current.get("no_change_intent", {}).get("check_id") != check_id or previous.get("check_id") != check_id:
                raise BridgeError("source_stage_no_change_replay_mismatch")
            return previous
        if current.get("no_change_intent") is not None:
            raise BridgeError("source_stage_no_change_reconciliation_required")
        if current.get("operation_key") is not None or (
            current.get("source_intent") is not None
            and current.get("source_intent", {}).get("acknowledged") is not True
        ):
            raise BridgeError("source_stage_reconciliation_required")
        path_hashes = _accepted_stage_path_hashes(stage_config, stage, record["base_sha"])
        tree_sha = gate._run(stage_config.source_repo, ["rev-parse", f"{record['base_sha']}^{{tree}}"]).stdout.decode("ascii").strip()
        intent = {"schema": "paperclip-source-stage-no-change-intent-v1", "check_id": check_id,
            "stage_binding_key": record["stage_binding_key"], "source_issue_id": record["source_issue_id"],
            "run_id": payload["run_id"], "base_sha": record["base_sha"], "tree_sha": tree_sha,
            "path_hashes": path_hashes, "policy_digest": policy.digest}
        current["no_change_intent"] = intent
        _write_journal(config, journal_key, current, create=False)
    checkout_parent = stage_config.verifier_scratch_root / "accepted-base-no-change-worktrees"
    checkout_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if checkout_parent.is_symlink():
        raise BridgeError("source_stage_no_change_checkout_invalid")
    checkout = Path(tempfile.mkdtemp(prefix="baseline-", dir=checkout_parent))
    checkout.rmdir()
    verifier_scratch = None
    try:
        gate._run(stage_config.source_repo, ["worktree", "add", "--detach", str(checkout), record["base_sha"]])
        verifier, verifier_scratch = _verifier(stage_config, checkout, content_only=True, capture_failure=True)
        verification = verifier(checkout, record["base_sha"], policy)
        after_tip = gate._ref_sha(stage_config.publisher_repo, stage_config.expected_dev_ref)
        if after_tip != record["base_sha"]:
            raise BridgeError("source_stage_accepted_tip_changed")
        result = {"schema": "paperclip-source-stage-no-change-result-v1", "status": verification.get("status"),
            "check_id": check_id, "stage_key": stage.stage_key, "stage_binding_key": record["stage_binding_key"],
            "source_issue_id": record["source_issue_id"], "run_id": payload["run_id"], "base_sha": record["base_sha"],
            "tree_sha": tree_sha, "path_hashes": path_hashes, "policy_digest": policy.digest,
            "verification": verification}
        result["receipt_digest"] = hashlib.sha256(_canonical(result)).hexdigest()
    except Exception:
        return {"status": "no_change_unknown", "check_id": check_id, "reconcile_required": True}
    finally:
        if verifier_scratch is not None:
            shutil.rmtree(verifier_scratch, ignore_errors=True)
        if checkout.exists():
            try:
                gate._run(stage_config.source_repo, ["worktree", "remove", "--force", str(checkout)])
            except Exception:
                shutil.rmtree(checkout, ignore_errors=True)
    if result.get("status") not in {"passed", "preflight_failed"}:
        return {"status": "no_change_unknown", "check_id": check_id, "reconcile_required": True}
    with _operation_lock(config, journal_key):
        current = _stage_binding(config, record["stage_binding_key"])
        if current.get("no_change_intent") != intent or current.get("no_change_result") is not None:
            raise BridgeError("source_stage_no_change_reconciliation_required")
        current["no_change_result"] = result
        _write_journal(config, journal_key, current, create=False)
    return result


def inspect_source_stage_no_change(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    required = {"company_id", "project_id", "reviewer_agent_id", "reviewer_run_id", "reviewer_issue_id", "stage_key", "stage_binding_key", "review_instruction_sha256"}
    if type(payload) is not dict or set(payload) != required:
        raise BridgeError("source_stage_no_change_review_payload_invalid")
    record = _stage_binding(config, payload["stage_binding_key"])
    stage = config.source_stage(record["stage_key"])
    result = record.get("no_change_result")
    if (record.get("state") != "active" or record.get("stage_key") != payload["stage_key"]
        or record.get("review_issue_id") != payload["reviewer_issue_id"]
        or payload.get("company_id") != config.company_id
        or payload.get("project_id") != (stage.project_id or config.project_id)
        or payload.get("reviewer_agent_id") != config.reviewer_agent_id
        or record.get("review_instruction_sha256") != payload.get("review_instruction_sha256")
        or type(payload.get("reviewer_run_id")) is not str or not ID_RE.fullmatch(payload["reviewer_run_id"])):
        raise BridgeError("source_stage_no_change_reviewer_binding_mismatch")
    if type(result) is not dict or result.get("status") != "passed":
        raise BridgeError("source_stage_no_change_result_unavailable")
    operation_config = config.for_source_stage(stage, source_issue_id=record["source_issue_id"], reviewer_issue_id=record["review_issue_id"],
        base_sha=record["base_sha"], canonical_base_sha=record.get("canonical_base_sha"), source_repo=_stage_source_repo(config, stage, record))
    try:
        tip = operation_config.gate()._ref_sha(operation_config.publisher_repo, operation_config.expected_dev_ref)
    except Exception as exc:
        raise BridgeError("source_stage_accepted_tip_unavailable") from exc
    if tip != result.get("base_sha"):
        raise BridgeError("source_stage_accepted_tip_changed")
    inspection = {"schema": "paperclip-source-stage-no-change-inspection-v1", "stage_binding_key": record["stage_binding_key"],
        "reviewer_issue_id": record["review_issue_id"], "reviewer_run_id": payload["reviewer_run_id"],
        "base_sha": result["base_sha"], "tree_sha": result["tree_sha"], "path_hashes": result["path_hashes"],
        "receipt_digest": result["receipt_digest"], "policy_digest": result["policy_digest"],
        "merit_required": True, "claim": "accepted_base_fixed_verifier_passed; product_merit_not_decided"}
    inspection["inspection_digest"] = hashlib.sha256(_canonical(inspection)).hexdigest()
    return inspection


def record_source_stage_no_change_merits(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    required = {"company_id", "project_id", "reviewer_agent_id", "reviewer_run_id", "reviewer_issue_id", "stage_key", "stage_binding_key", "review_instruction_sha256", "merits", "evidence"}
    if type(payload) is not dict or set(payload) != required or type(payload.get("merits")) is not bool or type(payload.get("evidence")) is not dict:
        raise BridgeError("source_stage_no_change_merits_payload_invalid")
    if set(payload["evidence"]) != {"inspection_digest", "rationale"} or type(payload["evidence"].get("inspection_digest")) is not str or not HASH_RE.fullmatch(payload["evidence"]["inspection_digest"]) or type(payload["evidence"].get("rationale")) is not str or not 1 <= len(payload["evidence"]["rationale"]) <= 4000:
        raise BridgeError("source_stage_no_change_merits_payload_invalid")
    inspection = inspect_source_stage_no_change(config, {key: payload[key] for key in required - {"merits", "evidence"}})
    if inspection["inspection_digest"] != payload["evidence"]["inspection_digest"]:
        raise BridgeError("source_stage_no_change_inspection_mismatch")
    record = _stage_binding(config, payload["stage_binding_key"])
    journal_key = _stage_record_key(record)
    merits = {"schema": "paperclip-source-stage-no-change-merits-v1", "reviewer_issue_id": record["review_issue_id"],
        "reviewer_run_id": payload["reviewer_run_id"], "inspection_digest": inspection["inspection_digest"],
        "receipt_digest": record["no_change_result"]["receipt_digest"], "merits": payload["merits"],
        "rationale": payload["evidence"]["rationale"], "claim": "independent_product_merits_only; no_content_candidate_or_publication"}
    merits["merits_digest"] = hashlib.sha256(_canonical(merits)).hexdigest()
    with _operation_lock(config, journal_key):
        current = _stage_binding(config, record["stage_binding_key"])
        previous = current.get("no_change_merits")
        if previous is not None and previous != merits:
            raise BridgeError("source_stage_no_change_merits_replay_mismatch")
        current["no_change_merits"] = merits
        _write_journal(config, journal_key, current, create=False)
    return merits


def approve_bound_source_stage(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "company_id",
        "project_id",
        "reviewer_agent_id",
        "reviewer_run_id",
        "reviewer_issue_id",
        "stage_key",
        "stage_binding_key",
        "approved",
        "evidence",
        "review_instruction_sha256",
    }
    if type(payload) is not dict or set(payload) != required:
        raise BridgeError("source_stage_review_payload_shape_invalid")
    record = _stage_binding(config, payload["stage_binding_key"])
    if (
        record.get("stage_key") != payload["stage_key"]
        or record.get("review_issue_id") != payload["reviewer_issue_id"]
        or payload.get("company_id") != config.company_id
        or payload.get("project_id")
        != (config.source_stage(record["stage_key"]).project_id or config.project_id)
        or payload.get("reviewer_agent_id") != config.reviewer_agent_id
        or payload.get("review_instruction_sha256") != record.get("review_instruction_sha256")
    ):
        raise BridgeError("source_stage_reviewer_binding_mismatch")
    operation_key = record.get("operation_key")
    if type(operation_key) is not str or not OPERATION_RE.fullmatch(operation_key):
        raise BridgeError("source_stage_candidate_not_prepared")
    operation_config = _operation_config(config, operation_key)
    candidate_sha = record.get("candidate_sha")
    if type(candidate_sha) is not str or not SHA_RE.fullmatch(candidate_sha):
        raise BridgeError("source_stage_candidate_not_prepared")
    return integrate_candidate(
        operation_config,
        {
            "company_id": operation_config.company_id,
            "project_id": operation_config.project_id,
            "reviewer_agent_id": config.reviewer_agent_id,
            "reviewer_run_id": payload["reviewer_run_id"],
            "reviewer_issue_id": record["review_issue_id"],
            "operation_key": operation_key,
            "candidate_sha": candidate_sha,
            "approved": payload["approved"],
            "evidence": payload["evidence"],
        },
    )


def _operation_config(config: BridgeConfig, operation_key: str) -> BridgeConfig:
    journal = _load_journal(config, operation_key)
    stage_binding_key = journal.get("source_stage_binding")
    if stage_binding_key is None:
        return config
    record = _stage_binding(config, stage_binding_key)
    stage = config.source_stage(record["stage_key"])
    base_sha = journal.get("binding", {}).get("base_sha")
    if type(base_sha) is not str or not SHA_RE.fullmatch(base_sha):
        raise BridgeError("source_stage_operation_base_invalid")
    stage_config = config.for_source_stage(
        stage,
        source_issue_id=record["source_issue_id"],
        reviewer_issue_id=record["review_issue_id"],
        base_sha=base_sha,
        canonical_base_sha=record.get("canonical_base_sha"),
        source_repo=_stage_source_repo(config, stage, record),
    )
    if journal.get("source_stage_key") != stage.stage_key:
        raise BridgeError("source_stage_operation_binding_mismatch")
    return stage_config


def integrate_candidate(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    operation_key = payload.get("operation_key")
    if type(operation_key) is not str or not OPERATION_RE.fullmatch(operation_key):
        raise BridgeError("operation_key_invalid")
    with _operation_lock(config, operation_key):
        journal = _load_journal(config, operation_key)
        source_preflight = _source_preflight_inspection(config, journal)
        operation_config = _operation_config(config, operation_key)
        return _integrate_candidate_locked(operation_config, payload, source_preflight)


def _integrate_candidate_locked(
    config: BridgeConfig,
    payload: dict[str, Any],
    source_preflight: tuple[dict[str, Any], str] | None = None,
) -> dict[str, Any]:
    if set(payload) != {
        "company_id",
        "project_id",
        "reviewer_agent_id",
        "reviewer_run_id",
        "reviewer_issue_id",
        "operation_key",
        "candidate_sha",
        "approved",
        "evidence",
    }:
        raise BridgeError("review_payload_shape_invalid")
    if any(
        payload.get(key) != expected
        for key, expected in {
            "company_id": config.company_id,
            "project_id": config.project_id,
            "reviewer_agent_id": config.reviewer_agent_id,
            "reviewer_issue_id": config.reviewer_issue_id,
        }.items()
    ):
        raise BridgeError("reviewer_actor_or_issue_binding_mismatch")
    operation_key = payload.get("operation_key")
    if type(operation_key) is not str or not OPERATION_RE.fullmatch(operation_key):
        raise BridgeError("operation_key_invalid")
    journal = _load_journal(config, operation_key)
    if (
        journal.get("schema") != "paperclip-autonomous-operation-v1"
        or journal.get("operation_key") != operation_key
    ):
        raise BridgeError("operation_journal_invalid")
    binding_data = journal.get("binding")
    if type(binding_data) is not dict:
        raise BridgeError("operation_binding_invalid")
    binding = OperationBinding(
        company_id=config.company_id,
        issue_id=config.source_issue_id,
        run_id=binding_data.get("run_id", ""),
        worker_agent_id=config.worker_agent_id,
        reviewer_agent_id=config.reviewer_agent_id,
        base_sha=binding_data.get("base_sha", ""),
        submitted_sha=binding_data.get("submitted_sha", ""),
        scope=_operation_scope(config, journal),
        policy_digest=config.policy().digest,
        active_ref=config.expected_dev_ref,
        owner_config_digest=config.owner_config_digest,
        owner_roots=config.owner_roots,
    )
    if binding.as_dict() != binding_data or binding.operation_key != operation_key:
        raise BridgeError("operation_binding_mismatch")
    reviewer_run_id = payload.get("reviewer_run_id")
    if type(reviewer_run_id) is not str or not ID_RE.fullmatch(reviewer_run_id):
        raise BridgeError("reviewer_run_identity_invalid")
    if type(payload.get("candidate_sha")) is not str or not SHA_RE.fullmatch(
        payload["candidate_sha"]
    ):
        raise BridgeError("candidate_sha_invalid")
    candidate_data = journal.get("candidate")
    if (
        type(candidate_data) is not dict
        or candidate_data.get("candidate_sha") != payload["candidate_sha"]
    ):
        raise BridgeError("candidate_does_not_match_prepared_journal")
    inspections = journal.get("inspections", [])
    inspection = (
        next(
            (
                item
                for item in inspections
                if type(item) is dict
                and item.get("reviewer_agent_id") == config.reviewer_agent_id
                and item.get("reviewer_issue_id") == config.reviewer_issue_id
                and item.get("reviewer_run_id") == reviewer_run_id
                and item.get("candidate_sha") == payload["candidate_sha"]
                and item.get("policy_digest") == binding.policy_digest
            ),
            None,
        )
        if type(inspections) is list
        else None
    )
    if inspection is None:
        raise BridgeError("reviewer_must_inspect_candidate_first")
    if source_preflight is not None:
        if inspection.get("source_preflight") != source_preflight[0] or inspection.get(
            "final_verifier_phase"
        ) not in {"pending_after_independent_QA", "passed_before_publication"}:
            raise BridgeError("source_preflight_inspection_binding_mismatch")
    inspection_digest = hashlib.sha256(_canonical(inspection)).hexdigest()
    approved = payload.get("approved")
    if type(approved) is not bool:
        raise BridgeError("review_decision_invalid")
    evidence = payload.get("evidence")
    if type(evidence) is not dict or len(_canonical(evidence)) > MAX_EVIDENCE_BYTES:
        raise BridgeError("review_evidence_invalid")
    if journal.get("source_stage_binding") is not None and (
        type(evidence.get("rationale")) is not str
        or len(evidence["rationale"].encode("utf-8")) > 4_000
    ):
        raise BridgeError("review_rationale_invalid")
    if evidence.get("inspection_digest") != inspection_digest:
        error = (
            "review_evidence_replay_mismatch"
            if journal.get("review") is not None
            else "review_evidence_inspection_mismatch"
        )
        raise BridgeError(error)
    canonical_record = journal.get("canonical")
    if canonical_record is not None and (
        evidence.get("canonical_candidate_sha") != canonical_record.get("candidate")
        or evidence.get("canonical_receipt_digest") != canonical_record.get("receipt_digest")
        or inspection.get("canonical_candidate_sha") != canonical_record.get("candidate")
        or inspection.get("canonical_receipt_digest") != canonical_record.get("receipt_digest")
    ):
        raise BridgeError("canonical_review_binding_mismatch")
    evidence_record = {
        "company_id": config.company_id,
        "project_id": config.project_id,
        "source_issue_id": config.source_issue_id,
        "worker_agent_id": config.worker_agent_id,
        "worker_run_id": binding.run_id,
        "reviewer_issue_id": config.reviewer_issue_id,
        "reviewer_agent_id": config.reviewer_agent_id,
        "reviewer_run_id": reviewer_run_id,
        "candidate_sha": payload["candidate_sha"],
        "policy_digest": binding.policy_digest,
        "inspection_digest": inspection_digest,
        "approved": approved,
        "evidence": evidence,
    }
    evidence_digest = hashlib.sha256(_canonical(evidence_record)).hexdigest()
    review_record = {**evidence_record, "evidence_digest": evidence_digest}
    if journal.get("review") is not None:
        if journal["review"] != review_record:
            raise BridgeError("review_evidence_replay_mismatch")
    else:
        journal["review"] = review_record
        journal["state"] = "review_custodied"
        _write_journal(config, operation_key, journal, create=False)
    if journal.get("state") == "review_rejected":
        return {
            "status": "review_rejected",
            "candidate_sha": payload["candidate_sha"],
            "operation_key": operation_key,
            "evidence_digest": evidence_digest,
        }
    approval = ReviewerApproval(
        company_id=config.company_id,
        run_id=binding.run_id,
        candidate_sha=payload["candidate_sha"],
        policy_digest=binding.policy_digest,
        reviewer_agent_id=config.reviewer_agent_id,
        reviewer_run_id=reviewer_run_id,
        evidence_digest=evidence_digest,
        approved=approved,
    )
    broker = config.broker()
    if journal.get("state") not in {
        "prepared",
        "review_custodied",
        "published",
        "projection_published",
        "canonical_publish_intent",
        "canonical_publish_held",
    }:
        raise BridgeError("operation_not_reviewable")
    broker_record, candidate = broker.recover_candidate(binding)
    if candidate.sha != payload["candidate_sha"]:
        raise BridgeError("candidate_does_not_match_prepared_journal")
    if not approved:
        result = broker.reject(binding, candidate, approval)
        journal["state"] = "review_rejected"
        _write_journal(config, operation_key, journal, create=False)
        return {
            "status": "review_rejected",
            "candidate_sha": result.candidate_sha,
            "operation_key": result.operation_key,
            "evidence_digest": result.evidence_digest,
        }
    scratch = None
    if broker_record.get("state") == "prepared":
        verifier, scratch = _verifier(
            config, candidate.checkout, content_only=bool(journal.get("source_snapshot_sha256"))
        )
    else:

        def verifier(*_args):
            raise BridgeError("verifier_must_not_be_retried")

    try:
        result = broker.integrate(binding, candidate, approval, verifier)
    finally:
        if scratch is not None:
            import shutil

            shutil.rmtree(scratch, ignore_errors=True)
    journal["state"] = result.status
    journal["receipt_digest"] = result.receipt_digest
    _write_journal(config, operation_key, journal, create=False)
    if canonical_record is not None:
        if not approved:
            raise BridgeError("canonical_review_rejected")
        canonical_record["status"] = "publish_intent"
        canonical_record["approved_candidate"] = canonical_record["candidate"]
        canonical_record["approved_receipt_digest"] = canonical_record["receipt_digest"]
        journal["state"] = "canonical_publish_intent"
        _write_journal(config, operation_key, journal, create=False)
        try:
            with company_operation(config.broker_journal_dir, config.company_id):
                require_company_open(config.broker_journal_dir, config.company_id)
                if canonical_record.get("status") == "publish_intent":
                    published = canonical_publisher.reconcile(
                        config.canonical_repo,
                        config.canonical_ref,
                        canonical_record["receipt"],
                        canonical_record["approved_candidate"],
                        canonical_record["approved_receipt_digest"],
                        allowed_paths=_operation_scope(config, journal),
                    )
                else:
                    published = {"status": canonical_record.get("status")}
        except Exception as exc:
            canonical_record["last_error"] = type(exc).__name__
            journal["state"] = "canonical_publish_held"
            _write_journal(config, operation_key, journal, create=False)
            raise BridgeError(f"canonical_publish_held:{type(exc).__name__}") from exc
        if (
            published.get("status") != "reconciled"
            or published.get("candidate") != canonical_record["candidate"]
            or published.get("receipt_digest") != canonical_record["receipt_digest"]
        ):
            canonical_record["status"] = "held"
            journal["state"] = "canonical_publish_held"
            _write_journal(config, operation_key, journal, create=False)
            raise BridgeError("canonical_publish_readback_mismatch")
        canonical_record["status"] = "published"
        canonical_record["ack"] = published
        journal["state"] = "published"
        _write_journal(config, operation_key, journal, create=False)
    return {
        "status": result.status,
        "candidate_sha": result.candidate_sha,
        "receipt_digest": result.receipt_digest,
        "operation_key": result.operation_key,
        "evidence_digest": evidence_digest,
        **(
            {
                "projection_status": result.status,
                "canonical_status": canonical_record["status"],
                "canonical_candidate_sha": canonical_record["candidate"],
                "canonical_receipt_digest": canonical_record["receipt_digest"],
            }
            if canonical_record is not None
            else {}
        ),
    }


_READ_ACTOR_FIELDS = {
    "company_id",
    "project_id",
    "program_issue_id",
    "issue_id",
    "agent_id",
    "run_id",
    "corpus_id",
}


def read_accepted_source(
    config: BridgeConfig, action: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Serve a bounded read from the current owner-approved sanitized projection tip."""
    action_fields = {
        "list_accepted_source": {"prefix", "cursor", "limit"},
        "search_accepted_source": {"query", "prefix", "cursor", "limit"},
        "read_accepted_source": {"path", "start_line", "line_count"},
    }
    if action not in action_fields or set(payload) != _READ_ACTOR_FIELDS | action_fields[action]:
        raise BridgeError("accepted_source_reader_request_shape_invalid")
    company_id = payload.get("company_id")
    run_id = payload.get("run_id")
    issue_id = payload.get("issue_id")
    if (
        company_id != config.company_id
        or any(type(value) is not str or not ID_RE.fullmatch(value) for value in (run_id, issue_id))
    ):
        raise BridgeError("accepted_source_reader_binding_invalid")
    program, projection = _accepted_source_program(config, payload)
    repository, snapshot = _resolve_accepted_source_snapshot(config, projection)
    try:
        if action == "list_accepted_source":
            result = list_accepted_source_files(
                str(repository),
                snapshot,
                config.accepted_source_read_policy_digest,
                prefix=payload["prefix"],
                cursor=payload["cursor"],
                limit=payload["limit"],
            )
        elif action == "search_accepted_source":
            result = search_accepted_source_text(
                str(repository),
                snapshot,
                config.accepted_source_read_policy_digest,
                payload["query"],
                prefix=payload["prefix"],
                cursor=payload["cursor"],
                limit=payload["limit"],
            )
        else:
            result = read_accepted_source_lines(
                str(repository),
                snapshot,
                payload["path"],
                start_line=payload["start_line"],
                line_count=payload["line_count"],
            )
    except AcceptedSourceReadError as exc:
        raise BridgeError(str(exc)) from exc
    return {
        "status": "ok",
        "program_issue_id": program.program_issue_id,
        "issue_id": issue_id,
        "corpus_id": projection.corpus_id,
        "projection_sha": snapshot.projection_sha,
        "canonical_sha": snapshot.canonical_sha,
        "acceptance_receipt_digest": snapshot.acceptance_receipt_digest,
        "manifest_digest": snapshot.manifest_digest,
        "result": result,
    }


def dispatch(config: BridgeConfig, request: dict[str, Any]) -> dict[str, Any]:
    action = request.get("action")
    payload = request.get("payload")
    if type(payload) is not dict:
        raise BridgeError("request_payload_invalid")
    if action == "submit_candidate":
        return prepare_candidate(config, payload)
    if action == "deliver_source_content":
        return prepare_source_content(config, payload)
    if action == "bind_source_stage":
        return bind_source_stage(config, payload)
    if action == "bind_program_source_stage":
        return bind_program_source_stage(config, payload)
    if action == "get_program_source_scope":
        return get_program_source_scope(config, payload)
    if action == "validate_program_source_proposal":
        return validate_program_source_proposal(config, payload)
    if action == "lookup_source_stage_binding":
        return lookup_source_stage_binding(config, payload)
    if action == "source_stage_activation":
        return source_stage_activation(config, payload)
    if action == "deliver_source_stage_content":
        return prepare_source_stage_content(config, payload)
    if action == "inspect_bound_source_stage":
        return inspect_bound_source_stage(config, payload)
    if action == "verify_source_stage_unchanged":
        return verify_source_stage_unchanged(config, payload)
    if action == "inspect_source_stage_no_change":
        return inspect_source_stage_no_change(config, payload)
    if action == "record_source_stage_no_change_merits":
        return record_source_stage_no_change_merits(config, payload)
    if action == "approve_bound_source_stage":
        return approve_bound_source_stage(config, payload)
    if action in {"list_accepted_source", "search_accepted_source", "read_accepted_source"}:
        return read_accepted_source(config, action, payload)
    if action in {"approve_candidate", "inspect_candidate"} and "stage_binding_key" in payload:
        operation_key = payload.get("operation_key")
        if type(operation_key) is not str or not OPERATION_RE.fullmatch(operation_key):
            raise BridgeError("operation_key_invalid")
        journal = _load_journal(config, operation_key)
        stage_binding_key = payload.get("stage_binding_key")
        if journal.get("source_stage_binding") != stage_binding_key:
            raise BridgeError("source_stage_operation_binding_mismatch")
        stage_record = _stage_binding(config, stage_binding_key)
        stage = config.source_stage(stage_record["stage_key"])
        if (
            payload.get("stage_key") != stage.stage_key
            or payload.get("source_issue_id") != stage_record["source_issue_id"]
            or payload.get("reviewer_issue_id") != stage_record["review_issue_id"]
        ):
            raise BridgeError("source_stage_reviewer_binding_mismatch")
        payload = {
            key: value
            for key, value in payload.items()
            if key not in {"stage_binding_key", "stage_key", "source_issue_id"}
        }
    if action == "approve_candidate":
        return integrate_candidate(config, payload)
    if action == "inspect_candidate":
        return inspect_candidate(config, payload)
    raise BridgeError("request_action_invalid")


def main() -> int:
    try:
        if len(sys.argv) != 2 or not Path(sys.argv[1]).is_absolute():
            raise BridgeError("absolute_owner_config_path_required")
        config_path = Path(sys.argv[1])
        if not config_path.is_absolute() or config_path.is_symlink():
            raise BridgeError("absolute_owner_config_path_required")
        config = BridgeConfig.load(config_path)
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise BridgeError("request_too_large")
        request = json.loads(raw)
        if type(request) is not dict:
            raise BridgeError("request_not_object")
        result = dispatch(config, request)
        sys.stdout.buffer.write(_canonical({"ok": True, "result": result}))
        sys.stdout.buffer.flush()
        return 0
    except BaseException as exc:
        code = str(exc) if isinstance(exc, BridgeError) else f"bridge_failed:{type(exc).__name__}"
        sys.stdout.buffer.write(_canonical({"ok": False, "error": code}))
        sys.stdout.buffer.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
