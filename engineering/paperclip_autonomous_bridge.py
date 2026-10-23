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

from engineering.confinement import (  # noqa: E402
    VERIFIER_ENV_KEYS,
    SandboxRunner,
    python_runtime_roots,
)
from engineering.git_gate import Candidate, GitGate, VerificationPolicy  # noqa: E402
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
    snapshot_records,
)

MAX_CONFIG_BYTES = 128_000
MAX_REQUEST_BYTES = 1_700_000
MAX_EVIDENCE_BYTES = 16_000
MAX_REVIEW_DIFF_BYTES = 60_000
MAX_GIT_ERROR_BYTES = 16_000
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


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, OverflowError):
        raise BridgeError("invalid_json_value") from None


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


def _stage_journal_key(stage_key: str) -> str:
    return hashlib.sha256(_canonical(["paperclip-source-stage-v1", stage_key])).hexdigest()


def _stage_binding(config: BridgeConfig, stage_binding_key: str) -> dict[str, Any]:
    if type(stage_binding_key) is not str or not HASH_RE.fullmatch(stage_binding_key):
        raise BridgeError("source_stage_binding_key_invalid")
    for stage in config.source_stage_catalog:
        try:
            record = _load_journal(config, _stage_journal_key(stage.stage_key))
        except BridgeError:
            continue
        if record.get("stage_binding_key") == stage_binding_key:
            if record.get("schema") != "paperclip-source-stage-binding-v1":
                break
            if record.get("owner_config_digest") != config.owner_config_digest:
                raise BridgeError("source_stage_owner_config_changed")
            return record
    raise BridgeError("source_stage_binding_invalid")


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
    }
    if type(payload) is not dict or set(payload) != required:
        raise BridgeError("source_stage_bind_payload_shape_invalid")
    if any(
        payload[name] != expected
        for name, expected in {
            "company_id": config.company_id,
            "project_id": config.project_id,
            "lead_agent_id": config.finite_lead_agent_id,
            "lead_issue_id": config.finite_lead_issue_id,
        }.items()
    ):
        raise BridgeError("source_stage_lead_binding_mismatch")
    for name in ("lead_run_id", "stage_key", "source_issue_id", "review_issue_id"):
        if type(payload[name]) is not str or not ID_RE.fullmatch(payload[name]):
            raise BridgeError("source_stage_identity_invalid")
    for name in ("source_version", "review_version"):
        if type(payload[name]) is not int or payload[name] < 0:
            raise BridgeError("source_stage_issue_version_invalid")
    stage = config.source_stage(payload["stage_key"])
    if payload["source_issue_id"] == payload["review_issue_id"]:
        raise BridgeError("source_stage_issue_pair_invalid")
    binding_value: dict[str, Any] = {
        "schema": "paperclip-source-stage-binding-v1",
        "stage_key": stage.stage_key,
        "source_issue_id": payload["source_issue_id"],
        "review_issue_id": payload["review_issue_id"],
        "lead_issue_id": config.finite_lead_issue_id,
        "lead_agent_id": config.finite_lead_agent_id,
        "lead_run_id": payload["lead_run_id"],
        "source_version": payload["source_version"],
        "review_version": payload["review_version"],
        "workspace_id": stage.workspace_id,
        "path_ids": list(stage.path_ids),
        "verifier_policy_id": stage.verifier_policy_id,
        "owner_config_digest": config.owner_config_digest,
        "predecessor_operation_key": payload["predecessor_operation_key"],
        "base_sha": stage.initial_base_sha,
        "state": "bound",
        "qa_intent": None,
        "source_intent": None,
        "operation_key": None,
        "candidate_sha": None,
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
        if (
            previous.get("state") != "published"
            or prior_stage is None
            or prior_stage.get("stage_key") != stage.predecessor_stage_key
            or prior_stage.get("operation_key") != operation_key
            or previous.get("candidate", {}).get("operation_key") != operation_key
            or payload["predecessor_source_status"] != "done"
            or payload["predecessor_review_status"] != "done"
            or type(payload["predecessor_source_version"]) is not int
            or payload["predecessor_source_version"] < 0
            or type(payload["predecessor_review_version"]) is not int
            or payload["predecessor_review_version"] < 0
            or payload["predecessor_dependency_ready"] is not True
            or payload["predecessor_source_version"] == prior_stage.get("source_version")
            or payload["predecessor_review_version"] == prior_stage.get("review_version")
        ):
            raise BridgeError("source_stage_predecessor_not_finalized")
        if payload.get("predecessor_source_issue_id") != prior_stage.get(
            "source_issue_id"
        ) or payload.get("predecessor_review_issue_id") != prior_stage.get("review_issue_id"):
            raise BridgeError("source_stage_predecessor_identity_mismatch")
        try:
            binding_value["base_sha"] = previous["candidate"]["candidate_sha"]
        except (KeyError, TypeError):
            raise BridgeError("source_stage_predecessor_candidate_invalid") from None
        if not SHA_RE.fullmatch(binding_value["base_sha"]):
            raise BridgeError("source_stage_predecessor_candidate_invalid")
        if prior_stage.get("candidate_sha") != binding_value["base_sha"]:
            raise BridgeError("source_stage_predecessor_candidate_invalid")
    else:
        if any(
            payload[name] is not None
            for name in (
                "predecessor_operation_key",
                "predecessor_source_status",
                "predecessor_source_version",
                "predecessor_review_status",
                "predecessor_review_version",
                "predecessor_source_issue_id",
                "predecessor_review_issue_id",
                "predecessor_dependency_ready",
            )
        ):
            raise BridgeError("source_stage_unexpected_predecessor")
    binding_key = hashlib.sha256(_canonical(binding_value)).hexdigest()
    binding_value["stage_binding_key"] = binding_key
    stage_key = _stage_journal_key(stage.stage_key)
    with _operation_lock(config, stage_key):
        path = _journal_path(config, stage_key)
        if path.exists():
            existing = _load_journal(config, stage_key)
            if any(
                existing.get(key) != value
                for key, value in binding_value.items()
                if key
                not in {"state", "qa_intent", "source_intent", "operation_key", "candidate_sha"}
            ):
                raise BridgeError("source_stage_bind_replay_mismatch")
            return {
                "stage_binding_key": existing["stage_binding_key"],
                "base_sha": existing["base_sha"],
                "state": existing["state"],
            }
        _write_journal(config, stage_key, binding_value, create=True)
    return {
        "stage_binding_key": binding_key,
        "base_sha": binding_value["base_sha"],
        "state": "bound",
    }


def lookup_source_stage_binding(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "company_id",
        "project_id",
        "lead_agent_id",
        "lead_issue_id",
        "stage_key",
        "source_issue_id",
        "review_issue_id",
    }
    if type(payload) is not dict or set(payload) != required:
        raise BridgeError("source_stage_lookup_payload_shape_invalid")
    if any(
        payload.get(key) != expected
        for key, expected in {
            "company_id": config.company_id,
            "project_id": config.project_id,
            "lead_agent_id": config.finite_lead_agent_id,
            "lead_issue_id": config.finite_lead_issue_id,
        }.items()
    ):
        raise BridgeError("source_stage_lead_binding_mismatch")
    stage = config.source_stage(payload["stage_key"])
    try:
        _journal_path(config, _stage_journal_key(stage.stage_key)).lstat()
    except FileNotFoundError:
        return {"found": False}
    record = _load_journal(config, _stage_journal_key(stage.stage_key))
    if (
        record.get("schema") != "paperclip-source-stage-binding-v1"
        or record.get("owner_config_digest") != config.owner_config_digest
        or record.get("source_issue_id") != payload["source_issue_id"]
        or record.get("review_issue_id") != payload["review_issue_id"]
    ):
        raise BridgeError("source_stage_bind_replay_mismatch")
    return {
        "found": True,
        "stage_binding_key": record["stage_binding_key"],
        "base_sha": record["base_sha"],
        "state": record["state"],
        "activation_pending": record.get("source_intent") is not None
        and record.get("source_intent", {}).get("acknowledged") is not True,
    }


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
    stage_key = _stage_journal_key(record["stage_key"])
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


def inspect_candidate(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    operation_key = payload.get("operation_key")
    if type(operation_key) is not str or not OPERATION_RE.fullmatch(operation_key):
        raise BridgeError("operation_key_invalid")
    config = _operation_config(config, operation_key)
    with _operation_lock(config, operation_key):
        return _inspect_candidate_locked(config, payload)


def _inspect_candidate_locked(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
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
        scope=SOURCE_PATHS if journal.get("source_snapshot_sha256") else config.allowed_scope,
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
    inspection = {
        "reviewer_agent_id": config.reviewer_agent_id,
        "reviewer_issue_id": config.reviewer_issue_id,
        "reviewer_run_id": reviewer_run_id,
        "candidate_sha": candidate.sha,
        "policy_digest": binding.policy_digest,
        "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
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
        "verifier": {
            "state": broker_record.get("state"),
            "receipt_digest": receipt_digest,
            "receipt": receipt_summary,
        },
        "diff": diff,
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
    reviewer_parent_issue_id: str | None = None
    finite_lead_agent_id: str | None = None
    finite_lead_issue_id: str | None = None
    source_stage_catalog: tuple[SourceStageSpec, ...] = ()

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
            worker_checkout = Path(raw["worker_checkout"]) if "worker_checkout" in raw else None
        except (KeyError, TypeError):
            raise BridgeError("owner_config_paths_invalid") from None
        if any(not value.is_absolute() for value in paths.values()) or (
            worker_checkout is not None and not worker_checkout.is_absolute()
        ):
            raise BridgeError("owner_config_paths_must_be_absolute")
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
        if finite_lead_agent_id is None and finite_lead_issue_id is None and not stage_rows:
            stage_catalog: tuple[SourceStageSpec, ...] = ()
        else:
            if (
                type(finite_lead_agent_id) is not str
                or not ID_RE.fullmatch(finite_lead_agent_id)
                or type(finite_lead_issue_id) is not str
                or not ID_RE.fullmatch(finite_lead_issue_id)
                or type(stage_rows) is not list
                or len(stage_rows) != 2
            ):
                raise BridgeError("owner_config_source_stage_catalog_invalid")
            stage_specs: list[SourceStageSpec] = []
            for index, row in enumerate(stage_rows):
                if type(row) is not dict or set(row) != {
                    "stage_key",
                    "workspace_id",
                    "initial_base_sha",
                    "predecessor_stage_key",
                    "path_ids",
                    "verifier_policy_id",
                }:
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
                    or tuple(path_ids_raw) != SOURCE_PATHS
                    or type(verifier_policy_id) is not str
                    or not ID_RE.fullmatch(verifier_policy_id)
                ):
                    raise BridgeError("owner_config_source_stage_catalog_invalid")
                if index == 0:
                    if (
                        workspace_id != "source"
                        or type(initial_base) is not str
                        or not SHA_RE.fullmatch(initial_base)
                        or predecessor is not None
                    ):
                        raise BridgeError("owner_config_source_stage_catalog_invalid")
                else:
                    if (
                        workspace_id != "published"
                        or initial_base is not None
                        or predecessor != stage_specs[0].stage_key
                    ):
                        raise BridgeError("owner_config_source_stage_catalog_invalid")
                stage_specs.append(
                    SourceStageSpec(
                        stage_key,
                        workspace_id,
                        initial_base,
                        predecessor,
                        tuple(path_ids_raw),
                        verifier_policy_id,
                    )
                )
            if len({stage.stage_key for stage in stage_specs}) != 2:
                raise BridgeError("owner_config_source_stage_catalog_invalid")
            if stage_specs[0].initial_base_sha != raw.get("expected_base_sha"):
                raise BridgeError("owner_config_source_stage_base_mismatch")
            stage_catalog = tuple(stage_specs)
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
        }
        if raw.get("fixed_dev_ref") not in allowed_refs:
            raise BridgeError("owner_config_development_ref_invalid")
        if raw.get("expected_dev_ref") != raw["fixed_dev_ref"]:
            raise BridgeError("owner_config_expected_ref_mismatch")
        expected_base = raw.get("expected_base_sha")
        if raw["fixed_dev_ref"] == "refs/heads/brain/development" and (
            type(expected_base) is not str or not SHA_RE.fullmatch(expected_base)
        ):
            raise BridgeError("owner_config_expected_base_invalid")
        if type(raw.get("verifier_policy_id")) is not str or not ID_RE.fullmatch(
            raw["verifier_policy_id"]
        ):
            raise BridgeError("owner_config_policy_id_invalid")
        return cls(
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
            reviewer_parent_issue_id=reviewer_parent,
            finite_lead_agent_id=finite_lead_agent_id,
            finite_lead_issue_id=finite_lead_issue_id,
            source_stage_catalog=stage_catalog,
        )

    def policy(self) -> VerificationPolicy:
        return VerificationPolicy(
            commands=self.verifier_commands,
            env=self.verifier_env,
            policy_id=self.verifier_policy_id,
        )

    def gate(self) -> GitGate:
        return GitGate(
            self.source_repo,
            self.publisher_repo,
            self.policy(),
            self.receipt_store,
            remote=None,
            active_ref=self.expected_dev_ref,
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
                *self.verifier_runtime_read_roots,
            )
        )

    @property
    def owner_config_digest(self) -> str:
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
                }
                for stage in self.source_stage_catalog
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
            "verifier_runtime_read_roots": tuple(
                str(path) for path in self.verifier_runtime_read_roots
            ),
        }
        return hashlib.sha256(_canonical(value)).hexdigest()

    def source_stage(self, stage_key: str) -> SourceStageSpec:
        matches = [stage for stage in self.source_stage_catalog if stage.stage_key == stage_key]
        if len(matches) != 1:
            raise BridgeError("source_stage_not_owner_admitted")
        return matches[0]

    def for_source_stage(
        self,
        stage: SourceStageSpec,
        *,
        source_issue_id: str,
        reviewer_issue_id: str,
        base_sha: str,
    ) -> BridgeConfig:
        return replace(
            self,
            source_issue_id=source_issue_id,
            reviewer_issue_id=reviewer_issue_id,
            reviewer_parent_issue_id=self.finite_lead_issue_id,
            source_repo=(
                self.publisher_repo if stage.workspace_id == "published" else self.source_repo
            ),
            allowed_scope=stage.path_ids,
            verifier_policy_id=stage.verifier_policy_id,
            expected_base_sha=base_sha,
        )


def _verifier(config: BridgeConfig, candidate_checkout: Path, *, content_only: bool = False):
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
    ) -> dict[str, str]:
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
            if result.returncode != 0 or result.timed_out or result.output_truncated:
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
        config.expected_dev_ref == "refs/heads/brain/development"
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
    config: BridgeConfig, payload: dict[str, Any], *, stage_binding_key: str | None = None
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
    if (
        config.expected_dev_ref == "refs/heads/brain/development"
        and base_sha != config.expected_base_sha
    ):
        raise BridgeError("development_base_not_owner_admitted")
    try:
        snapshot = snapshot_records(payload["files"])
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
            return journal["candidate"]
        return _prepare_source_content_locked(
            config, payload, snapshot, operation_key, stage_binding_key=stage_binding_key
        )


def _prepare_source_content_locked(
    config, payload, snapshot, operation_key, *, stage_binding_key=None
):
    gate = config.gate()
    try:
        projection = materialize_owner_projection(gate, payload["base_sha"], snapshot)
    except SourceDeliveryError as exc:
        raise BridgeError(str(exc)) from exc
    policy = config.policy()
    binding = OperationBinding(
        company_id=config.company_id,
        issue_id=config.source_issue_id,
        run_id=payload["run_id"],
        worker_agent_id=config.worker_agent_id,
        reviewer_agent_id=config.reviewer_agent_id,
        base_sha=projection.base_sha,
        submitted_sha=projection.submitted_sha,
        scope=SOURCE_PATHS,
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
        "source_stage_key": (
            _stage_binding(config, stage_binding_key)["stage_key"]
            if stage_binding_key is not None
            else None
        ),
        "source_files": [
            {"path_id": item.path_id, "size": item.size, "sha256": item.sha256}
            for item in snapshot.files
        ],
        "review": None,
    }
    _write_journal(config, operation_key, journal, create=True)
    return result


def prepare_source_stage_content(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    required = {
        "company_id",
        "project_id",
        "source_issue_id",
        "worker_agent_id",
        "run_id",
        "review_issue_id",
        "stage_key",
        "stage_binding_key",
        "files",
    }
    if type(payload) is not dict or set(payload) != required:
        raise BridgeError("source_stage_delivery_payload_shape_invalid")
    record = _stage_binding(config, payload["stage_binding_key"])
    if (
        record["stage_key"] != payload["stage_key"]
        or record["source_issue_id"] != payload["source_issue_id"]
        or record["review_issue_id"] != payload["review_issue_id"]
        or record["state"] != "active"
    ):
        raise BridgeError("source_stage_not_active")
    stage = config.source_stage(record["stage_key"])
    stage_config = config.for_source_stage(
        stage,
        source_issue_id=record["source_issue_id"],
        reviewer_issue_id=record["review_issue_id"],
        base_sha=record["base_sha"],
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
    result = prepare_source_content(
        stage_config, content_payload, stage_binding_key=payload["stage_binding_key"]
    )
    stage_journal_key = _stage_journal_key(record["stage_key"])
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
    }
    if type(payload) is not dict or set(payload) != required:
        raise BridgeError("source_stage_review_payload_shape_invalid")
    record = _stage_binding(config, payload["stage_binding_key"])
    if (
        record.get("stage_key") != payload["stage_key"]
        or record.get("review_issue_id") != payload["reviewer_issue_id"]
        or payload.get("company_id") != config.company_id
        or payload.get("project_id") != config.project_id
        or payload.get("reviewer_agent_id") != config.reviewer_agent_id
        or type(payload.get("reviewer_run_id")) is not str
        or not ID_RE.fullmatch(payload["reviewer_run_id"])
    ):
        raise BridgeError("source_stage_reviewer_binding_mismatch")
    operation_key = record.get("operation_key")
    candidate_sha = record.get("candidate_sha")
    if (
        type(operation_key) is not str
        or not OPERATION_RE.fullmatch(operation_key)
        or type(candidate_sha) is not str
        or not SHA_RE.fullmatch(candidate_sha)
    ):
        raise BridgeError("source_stage_candidate_not_prepared")
    return inspect_candidate(
        config,
        {
            "company_id": config.company_id,
            "project_id": config.project_id,
            "reviewer_agent_id": config.reviewer_agent_id,
            "reviewer_run_id": payload["reviewer_run_id"],
            "reviewer_issue_id": record["review_issue_id"],
            "operation_key": operation_key,
            "candidate_sha": candidate_sha,
        },
    )


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
    }
    if type(payload) is not dict or set(payload) != required:
        raise BridgeError("source_stage_review_payload_shape_invalid")
    record = _stage_binding(config, payload["stage_binding_key"])
    if (
        record.get("stage_key") != payload["stage_key"]
        or record.get("review_issue_id") != payload["reviewer_issue_id"]
        or payload.get("company_id") != config.company_id
        or payload.get("project_id") != config.project_id
        or payload.get("reviewer_agent_id") != config.reviewer_agent_id
    ):
        raise BridgeError("source_stage_reviewer_binding_mismatch")
    operation_key = record.get("operation_key")
    candidate_sha = record.get("candidate_sha")
    if (
        type(operation_key) is not str
        or not OPERATION_RE.fullmatch(operation_key)
        or type(candidate_sha) is not str
        or not SHA_RE.fullmatch(candidate_sha)
    ):
        raise BridgeError("source_stage_candidate_not_prepared")
    return integrate_candidate(
        config,
        {
            "company_id": config.company_id,
            "project_id": config.project_id,
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
    )
    if journal.get("source_stage_key") != stage.stage_key:
        raise BridgeError("source_stage_operation_binding_mismatch")
    return stage_config


def integrate_candidate(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
    operation_key = payload.get("operation_key")
    if type(operation_key) is not str or not OPERATION_RE.fullmatch(operation_key):
        raise BridgeError("operation_key_invalid")
    config = _operation_config(config, operation_key)
    with _operation_lock(config, operation_key):
        return _integrate_candidate_locked(config, payload)


def _integrate_candidate_locked(config: BridgeConfig, payload: dict[str, Any]) -> dict[str, Any]:
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
        scope=SOURCE_PATHS if journal.get("source_snapshot_sha256") else config.allowed_scope,
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
    inspection_digest = hashlib.sha256(_canonical(inspection)).hexdigest()
    approved = payload.get("approved")
    if type(approved) is not bool:
        raise BridgeError("review_decision_invalid")
    evidence = payload.get("evidence")
    if type(evidence) is not dict or len(_canonical(evidence)) > MAX_EVIDENCE_BYTES:
        raise BridgeError("review_evidence_invalid")
    if evidence.get("inspection_digest") != inspection_digest:
        error = (
            "review_evidence_replay_mismatch"
            if journal.get("review") is not None
            else "review_evidence_inspection_mismatch"
        )
        raise BridgeError(error)
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
    if journal.get("state") not in {"prepared", "review_custodied", "published"}:
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
    return {
        "status": result.status,
        "candidate_sha": result.candidate_sha,
        "receipt_digest": result.receipt_digest,
        "operation_key": result.operation_key,
        "evidence_digest": evidence_digest,
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
    if action == "lookup_source_stage_binding":
        return lookup_source_stage_binding(config, payload)
    if action == "source_stage_activation":
        return source_stage_activation(config, payload)
    if action == "deliver_source_stage_content":
        return prepare_source_stage_content(config, payload)
    if action == "inspect_bound_source_stage":
        return inspect_bound_source_stage(config, payload)
    if action == "approve_bound_source_stage":
        return approve_bound_source_stage(config, payload)
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
