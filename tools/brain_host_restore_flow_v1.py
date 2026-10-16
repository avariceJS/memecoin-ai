#!/usr/bin/env python3
"""Inspect or perform a pinned, inert Brain restore into a new quarantine.

This post-bootstrap flow composes the existing recovery-set verifier, inert
restore conductor, and negative-only restore assessor. It has no start or
activation operation. `restore-inert` writes a local run record and a Kingston
restore witness; it is not a read-only command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import brain_host_recovery as host_recovery

SCHEMA = "brain-host-post-bootstrap-restore-flow-v1"
HANDOFF_SCHEMA = "brain-host-restore-handoff-v1"
MAX_HANDOFF_BYTES = 256 * 1024
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
ID_RE = re.compile(r"[0-9a-f]{32}\Z")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\Z")
ROOT_ROLES = {
    "owner_checkout",
    "owner_work",
    "research_checkout",
    "research_work",
    "private_brain_config",
    "launchagents",
    "owner_codex_worktree",
    "verifier_worktree",
}
ROOT_ABSENCE_DISPOSITIONS = {"confirmed_absent", "rebuild_from_git", "reissue", "not_applicable"}
HANDOFF_FIELDS = {
    "schema",
    "version",
    "handoff_id",
    "execution_commit",
    "entry_card_sha256",
    "kingston",
    "recovery_set",
    "target_policy",
    "authorization_scope",
    "source_roots",
}


class RestoreFlowError(ValueError):
    """A restore handoff or local restore path failed closed."""


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RestoreFlowError("handoff_duplicate_json_field")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise RestoreFlowError("handoff_nonstandard_json_constant")


def _read_handoff(path: Path, expected_sha256: str) -> tuple[dict[str, Any], bytes]:
    if not SHA256_RE.fullmatch(expected_sha256):
        raise RestoreFlowError("external_handoff_sha256_invalid")
    if not path.is_absolute() or ".." in path.parts:
        raise RestoreFlowError("handoff_path_invalid")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise RestoreFlowError("handoff_read_failed") from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > MAX_HANDOFF_BYTES
        ):
            raise RestoreFlowError("handoff_file_unsafe")
        raw = bytearray()
        while chunk := os.read(fd, min(65536, MAX_HANDOFF_BYTES + 1 - len(raw))):
            raw.extend(chunk)
            if len(raw) > MAX_HANDOFF_BYTES:
                raise RestoreFlowError("handoff_size_limit")
        after = os.fstat(fd)
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
        ) or len(raw) != before.st_size:
            raise RestoreFlowError("handoff_changed_during_read")
    finally:
        os.close(fd)
    payload = bytes(raw)
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise RestoreFlowError("external_handoff_pin_mismatch")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=_reject_json_constant,
        )
    except RestoreFlowError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise RestoreFlowError("handoff_json_invalid") from exc
    if not isinstance(value, dict) or _canonical(value) != payload:
        raise RestoreFlowError("handoff_not_canonical")
    _validate_handoff(value)
    return value, payload


def _validate_handoff(value: dict[str, Any]) -> None:
    if (
        set(value) != HANDOFF_FIELDS
        or value.get("schema") != HANDOFF_SCHEMA
        or type(value.get("version")) is not int
        or value.get("version") != 1
    ):
        raise RestoreFlowError("handoff_schema_invalid")
    handoff_id = value.get("handoff_id")
    execution_commit = value.get("execution_commit")
    entry_card_sha256 = value.get("entry_card_sha256")
    if not isinstance(handoff_id, str) or not ID_RE.fullmatch(handoff_id):
        raise RestoreFlowError("handoff_id_invalid")
    if not isinstance(execution_commit, str) or not COMMIT_RE.fullmatch(execution_commit):
        raise RestoreFlowError("handoff_execution_commit_invalid")
    if not isinstance(entry_card_sha256, str) or not SHA256_RE.fullmatch(entry_card_sha256):
        raise RestoreFlowError("handoff_entry_card_pin_invalid")
    if value.get("target_policy") != "new_quarantine_only":
        raise RestoreFlowError("handoff_target_policy_unsupported")
    if value.get("authorization_scope") != "inert_restore_only":
        raise RestoreFlowError("handoff_authority_invalid")
    kingston = value.get("kingston")
    if (
        not isinstance(kingston, dict)
        or set(kingston) != {"volume_uuid", "root_id"}
        or not isinstance(kingston.get("volume_uuid"), str)
        or not UUID_RE.fullmatch(kingston["volume_uuid"])
        or not isinstance(kingston.get("root_id"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", kingston["root_id"])
    ):
        raise RestoreFlowError("handoff_kingston_pin_invalid")
    recovery_set = value.get("recovery_set")
    if (
        not isinstance(recovery_set, dict)
        or set(recovery_set) != {"set_id", "index_sha256", "research_commit", "owner_commit"}
        or not isinstance(recovery_set.get("set_id"), str)
        or not ID_RE.fullmatch(recovery_set["set_id"])
        or not isinstance(recovery_set.get("index_sha256"), str)
        or not SHA256_RE.fullmatch(recovery_set["index_sha256"])
        or not isinstance(recovery_set.get("research_commit"), str)
        or not COMMIT_RE.fullmatch(recovery_set["research_commit"])
        or not isinstance(recovery_set.get("owner_commit"), str)
        or not COMMIT_RE.fullmatch(recovery_set["owner_commit"])
    ):
        raise RestoreFlowError("handoff_recovery_set_pin_invalid")
    roots = value.get("source_roots")
    if not isinstance(roots, dict) or set(roots) != ROOT_ROLES:
        raise RestoreFlowError("handoff_source_root_roster_invalid")
    for role, declaration in roots.items():
        if not isinstance(declaration, dict):
            raise RestoreFlowError("handoff_source_root_declaration_invalid")
        state = declaration.get("state")
        if state == "present":
            if set(declaration) != {"state", "path"}:
                raise RestoreFlowError(f"handoff_{role}_declaration_invalid")
            path = declaration.get("path")
            if (
                not isinstance(path, str)
                or not path.startswith("/")
                or "\x00" in path
                or ".." in Path(path).parts
            ):
                raise RestoreFlowError(f"handoff_{role}_path_invalid")
        elif state == "absent":
            if set(declaration) != {"state", "path", "disposition", "reason"}:
                raise RestoreFlowError(f"handoff_{role}_declaration_invalid")
            path = declaration.get("path")
            if (
                not isinstance(path, str)
                or not path.startswith("/")
                or "\x00" in path
                or ".." in Path(path).parts
            ):
                raise RestoreFlowError(f"handoff_{role}_path_invalid")
            if declaration.get("disposition") not in ROOT_ABSENCE_DISPOSITIONS:
                raise RestoreFlowError(f"handoff_{role}_absence_unresolved")
            reason = declaration.get("reason")
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 512:
                raise RestoreFlowError(f"handoff_{role}_absence_reason_invalid")
        else:
            raise RestoreFlowError(f"handoff_{role}_state_invalid")


def _open_directory(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise RestoreFlowError("directory_path_invalid")
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise RestoreFlowError("directory_path_invalid")
            nxt = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = nxt
        return current
    except OSError as exc:
        os.close(current)
        raise RestoreFlowError("directory_open_failed") from exc


def _write_create_only(directory_fd: int, name: str, payload: bytes) -> None:
    if not re.fullmatch(r"[A-Z][A-Z0-9_.-]{0,63}", name):
        raise RestoreFlowError("run_record_name_invalid")
    fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        view = memoryview(payload)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise RestoreFlowError("run_record_short_write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(directory_fd)


def _path_is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _path_overlaps(first: Path, second: Path) -> bool:
    return _path_is_within(first, second) or _path_is_within(second, first)


def _validated_directory(
    path: Path,
    label: str,
    *,
    must_be_empty: bool = False,
    private: bool = True,
) -> tuple[int, os.stat_result]:
    try:
        fd = _open_directory(path)
    except RestoreFlowError as exc:
        raise RestoreFlowError(f"{label}_{exc}") from exc
    metadata = os.fstat(fd)
    unsafe_mode = (
        stat.S_IMODE(metadata.st_mode) & 0o077
        if private
        else stat.S_IMODE(metadata.st_mode) & 0o022
    )
    if metadata.st_uid != os.geteuid() or unsafe_mode:
        os.close(fd)
        raise RestoreFlowError(f"{label}_permissions_unsafe")
    if must_be_empty and os.listdir(fd):
        os.close(fd)
        raise RestoreFlowError(f"{label}_must_be_empty")
    return fd, metadata


def _read_card_hash(path: Path) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise RestoreFlowError("entry_card_read_failed") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 64 * 1024:
            raise RestoreFlowError("entry_card_file_invalid")
        digest = hashlib.sha256()
        total = 0
        while block := os.read(fd, 8192):
            total += len(block)
            if total > 64 * 1024:
                raise RestoreFlowError("entry_card_size_limit")
            digest.update(block)
        after = os.fstat(fd)
        if (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or total != metadata.st_size:
            raise RestoreFlowError("entry_card_changed_during_read")
        return digest.hexdigest()
    finally:
        os.close(fd)


def _git_state(
    repo: Path, runner: Callable[..., subprocess.CompletedProcess[str]]
) -> dict[str, Any]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        head = runner(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "--no-optional-locks",
                "-C",
                str(repo),
                "rev-parse",
                "HEAD",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=env,
        )
        status = runner(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "--no-optional-locks",
                "-C",
                str(repo),
                "status",
                "--porcelain",
                "--untracked-files=all",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RestoreFlowError("repository_probe_failed") from exc
    if head.returncode != 0 or status.returncode != 0:
        raise RestoreFlowError("repository_probe_failed")
    value = head.stdout.strip()
    if not COMMIT_RE.fullmatch(value):
        raise RestoreFlowError("repository_head_invalid")
    return {"head": value, "clean": not bool(status.stdout.strip())}


def _resolve_source_roots(value: dict[str, Any], repo: Path) -> tuple[dict[str, str], list[Path]]:
    states: dict[str, str] = {}
    paths: list[Path] = []
    for role in sorted(ROOT_ROLES):
        declaration = value["source_roots"][role]
        if role == "research_checkout" and declaration["state"] != "present":
            raise RestoreFlowError("research_checkout_must_be_present")
        if declaration["state"] == "absent":
            absent_path = Path(declaration["path"])
            if absent_path.resolve(strict=False) != absent_path or os.path.lexists(absent_path):
                raise RestoreFlowError(f"source_root_{role}_declared_absent_but_present_or_aliased")
            states[role] = "absent:" + declaration["disposition"]
            paths.append(absent_path)
            continue
        path = Path(declaration["path"])
        if path.resolve(strict=True) != path:
            raise RestoreFlowError(f"source_root_{role}_path_not_canonical")
        fd = _open_directory(path)
        try:
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise RestoreFlowError(f"source_root_{role}_not_directory")
        finally:
            os.close(fd)
        if role == "research_checkout" and path != repo:
            raise RestoreFlowError("research_checkout_not_current_repository")
        states[role] = "present"
        paths.append(path)
    return states, paths


def _storage_and_set(
    handoff: dict[str, Any], storage_config: Path
) -> tuple[dict[str, Any], list[str]]:
    from tools.brain_host_recovery_set import verify_set
    from tools.brain_host_restore_assessment import open_readonly_storage_reader

    blockers: list[str] = []
    pins = handoff["kingston"]
    set_pin = handoff["recovery_set"]
    storage_result: dict[str, Any] = {"state": "unknown"}
    set_result: dict[str, Any] = {"state": "unknown"}
    try:
        with open_readonly_storage_reader(storage_config) as (reader, revalidate):
            actual = revalidate()
            volume_uuid, root_id, available_bytes, required_free_bytes = actual
            storage_result = {
                "state": "present",
                "volume_uuid": volume_uuid,
                "root_id": root_id,
                "device": reader.device,
                "available_bytes": available_bytes,
                "required_free_bytes": required_free_bytes,
            }
            if (volume_uuid, root_id) != (pins["volume_uuid"], pins["root_id"]):
                blockers.append("kingston_identity_pin_mismatch")
            if available_bytes is None or available_bytes < required_free_bytes:
                blockers.append("kingston_capacity_below_configured_floor")
            base = f"host-recovery/recovery-sets-v1/{set_pin['set_id']}/INDEX.json"
            try:
                index_raw = reader.read_bytes(base, max_bytes=4 * 1024 * 1024)
            except FileNotFoundError:
                blockers.append("set_missing")
                set_result = {"state": "absent", "set_id": set_pin["set_id"]}
            else:
                index_sha256 = hashlib.sha256(index_raw).hexdigest()
                set_result = {
                    "state": "present",
                    "set_id": set_pin["set_id"],
                    "index_sha256": index_sha256,
                }
                if index_sha256 != set_pin["index_sha256"]:
                    blockers.append("set_index_pin_mismatch")
                if index_sha256 == set_pin["index_sha256"]:
                    try:
                        index = json.loads(index_raw.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        blockers.append("set_index_invalid")
                    else:
                        if (
                            index.get("research_commit") != set_pin["research_commit"]
                            or index.get("owner_commit") != set_pin["owner_commit"]
                        ):
                            blockers.append("set_commit_pin_mismatch")
                        try:
                            verified = verify_set(
                                reader,
                                set_pin["set_id"],
                                expected_volume_uuid=pins["volume_uuid"],
                                expected_root_id=pins["root_id"],
                                revalidate_identity=lambda: revalidate()[:2],
                            )
                        except Exception as exc:
                            blockers.append(f"set_verification_failed:{type(exc).__name__}")
                        else:
                            set_result.update(verified)
                            if verified.get("member_count") != 6:
                                blockers.append("set_required_role_count_invalid")
                            if verified.get("coverage_resolved") is not True:
                                blockers.append("set_coverage_unresolved")
            if revalidate()[:2] != (volume_uuid, root_id):
                blockers.append("kingston_identity_changed_during_inspection")
    except Exception as exc:
        blockers.append(f"storage_inspection_failed:{type(exc).__name__}")
    return {"storage": storage_result, "recovery_set": set_result}, sorted(set(blockers))


def _revalidate_storage_binding(
    storage_config: Path,
    expected_volume_uuid: str,
    expected_root_id: str,
    expected_device: int | None = None,
) -> tuple[Path, int]:
    """Bind the configured data-root path to a currently held read-only root."""
    from memecoin_research_storage import StorageConfig
    from tools.brain_host_restore_assessment import open_readonly_storage_reader

    with open_readonly_storage_reader(storage_config) as (reader, revalidate):
        actual_uuid, actual_root_id, _available, _required = revalidate()
        root_stat = os.fstat(reader.root_fd)
        if (actual_uuid, actual_root_id) != (expected_volume_uuid, expected_root_id):
            raise RestoreFlowError("kingston_identity_changed_after_inspection")
        if expected_device is not None and root_stat.st_dev != expected_device:
            raise RestoreFlowError("kingston_device_changed_after_inspection")
        loaded = StorageConfig.load(storage_config)
        configured_root = loaded.mount_path / loaded.data_root
        if configured_root.resolve(strict=True) != configured_root:
            raise RestoreFlowError("storage_root_path_not_canonical")
        path_stat = configured_root.stat(follow_symlinks=False)
        if (path_stat.st_dev, path_stat.st_ino) != (root_stat.st_dev, root_stat.st_ino):
            raise RestoreFlowError("storage_config_root_changed_after_inspection")
        if (loaded.volume_uuid, loaded.root_id) != (expected_volume_uuid, expected_root_id):
            raise RestoreFlowError("storage_config_identity_changed_after_inspection")
        return configured_root, root_stat.st_dev


def inspect_restore(
    *,
    handoff_path: Path,
    expected_handoff_sha256: str,
    repository_root: Path,
    storage_config: Path,
    target_parent: Path,
    run_parent: Path | None = None,
    same_host_isolated: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Read-only preflight. Does not create a target, run record, or Kingston witness."""
    blockers: list[str] = []
    host_observation: dict[str, Any] = {"state": "unknown"}
    basic: dict[str, Any] = {}
    try:
        handoff, raw_handoff = _read_handoff(handoff_path, expected_handoff_sha256)
    except RestoreFlowError as exc:
        return _blocked([str(exc)], handoff_sha256=expected_handoff_sha256)
    repo = repository_root
    if not repo.is_absolute() or ".." in repo.parts or repo.resolve(strict=False) != repo:
        blockers.append("repository_path_not_canonical")
    if (
        not storage_config.is_absolute()
        or ".." in storage_config.parts
        or storage_config.resolve(strict=False) != storage_config
    ):
        blockers.append("storage_config_path_not_absolute_canonical")
    if not target_parent.is_absolute() or ".." in target_parent.parts:
        blockers.append("target_parent_path_not_absolute_canonical")
    if run_parent is not None and (not run_parent.is_absolute() or ".." in run_parent.parts):
        blockers.append("run_parent_path_not_absolute_canonical")
    if not handoff_path.is_absolute() or ".." in handoff_path.parts:
        blockers.append("handoff_path_not_absolute_canonical")
    if handoff["recovery_set"]["research_commit"] != handoff["execution_commit"]:
        blockers.append("v1_set_research_commit_must_match_execution_commit")
    target_identity: dict[str, int] | None = None
    try:
        git = _git_state(repo, runner)
    except RestoreFlowError as exc:
        git = {"state": "unknown"}
        blockers.append(str(exc))
    if git.get("head") != handoff["execution_commit"]:
        blockers.append("execution_commit_pin_mismatch")
    if git.get("clean") is not True:
        blockers.append("execution_checkout_not_clean")
    try:
        source_card_path = repo / "recovery" / "ENTRY.txt"
        if source_card_path.resolve(strict=True) != source_card_path:
            raise RestoreFlowError("entry_card_path_not_canonical")
        source_card = _read_card_hash(source_card_path)
    except RestoreFlowError as exc:
        source_card = kingston_card = None
        blockers.append(str(exc))
    if source_card != handoff["entry_card_sha256"]:
        blockers.append("entry_card_pin_mismatch")
    source_roots_resolved = False
    try:
        root_states, source_roots = _resolve_source_roots(handoff, repo)
        source_roots_resolved = True
    except (OSError, RuntimeError, RestoreFlowError) as exc:
        root_states, source_roots = {}, []
        blockers.append(
            str(exc) if isinstance(exc, RestoreFlowError) else "source_root_probe_failed"
        )
    storage = _storage_and_set(handoff, storage_config.expanduser().absolute())
    blockers.extend(storage[1])
    storage_info = storage[0]["storage"]
    derived_storage_root: Path | None = None
    storage_root_verified = False
    if storage_info.get("state") == "present":
        try:
            derived_storage_root, verified_device = _revalidate_storage_binding(
                storage_config,
                handoff["kingston"]["volume_uuid"],
                handoff["kingston"]["root_id"],
                storage_info.get("device"),
            )
            if verified_device != storage_info.get("device"):
                blockers.append("storage_device_changed_after_inspection")
            else:
                storage_root_verified = True
        except Exception as exc:
            blockers.append(f"storage_root_derivation_failed:{type(exc).__name__}")
    if derived_storage_root is not None:
        try:
            kingston_card_path = derived_storage_root / "host-recovery" / "ENTRY.txt"
            if kingston_card_path.resolve(strict=True) != kingston_card_path:
                raise RestoreFlowError("kingston_entry_card_path_not_canonical")
            kingston_card = _read_card_hash(kingston_card_path)
        except RestoreFlowError as exc:
            kingston_card = None
            blockers.append(str(exc))
    else:
        kingston_card = None
        blockers.append("kingston_entry_card_storage_identity_unknown")
    if kingston_card != handoff["entry_card_sha256"]:
        blockers.append("entry_card_kingston_pin_mismatch")
    forbidden = [repo, *([derived_storage_root] if derived_storage_root else []), *source_roots]
    canonical_forbidden = list(dict.fromkeys(path.resolve(strict=False) for path in forbidden))
    try:
        target_fd, target_stat = _validated_directory(
            target_parent, "target_parent", must_be_empty=True
        )
        try:
            target_path = Path(os.path.realpath(target_parent))
            if derived_storage_root and target_stat.st_dev == os.stat(derived_storage_root).st_dev:
                blockers.append("target_must_be_on_separate_device")
            if any(_path_overlaps(target_path, root) for root in canonical_forbidden):
                blockers.append("target_overlaps_forbidden_root")
            target_identity = {"device": target_stat.st_dev, "inode": target_stat.st_ino}
        finally:
            os.close(target_fd)
    except (OSError, RestoreFlowError) as exc:
        target_stat = None
        target_identity = None
        blockers.append(
            str(exc) if isinstance(exc, RestoreFlowError) else "target_parent_probe_failed"
        )
    run_identity: dict[str, int] | None = None
    if run_parent is not None:
        try:
            run_fd, run_stat = _validated_directory(run_parent, "run_parent")
            try:
                run_path = Path(os.path.realpath(run_parent))
                if derived_storage_root and run_stat.st_dev == os.stat(derived_storage_root).st_dev:
                    blockers.append("run_records_must_be_outside_kingston")
                if any(_path_overlaps(run_path, root) for root in canonical_forbidden):
                    blockers.append("run_parent_overlaps_forbidden_root")
                if _path_overlaps(run_path, Path(os.path.realpath(target_parent))):
                    blockers.append("run_parent_overlaps_target_parent")
                run_identity = {"device": run_stat.st_dev, "inode": run_stat.st_ino}
            finally:
                os.close(run_fd)
        except (OSError, RestoreFlowError) as exc:
            run_identity = None
            blockers.append(
                str(exc) if isinstance(exc, RestoreFlowError) else "run_parent_probe_failed"
            )
    try:
        owner_decl = handoff["source_roots"]["owner_checkout"]
        if owner_decl["state"] == "absent":
            owner_decl = handoff["source_roots"]["owner_work"]
        owner_root = Path(owner_decl["path"]) if owner_decl["state"] == "present" else None
        host_observation = host_recovery.status(
            repo=repo,
            owner_root=owner_root,
            storage_config=storage_config.expanduser().absolute(),
            runner=host_recovery.run_command,
        )
    except Exception as exc:
        host_observation = {"state": "unknown", "error_class": type(exc).__name__}
    launchd = host_observation.get("services", {}).get("launchd", {})
    processes = host_observation.get("processes", {}).get("workspace", {})
    services_observed_inert = launchd.get("state") == "absent" and launchd.get("loaded_count") == 0
    processes_observed_inert = processes.get("state") == "absent" and processes.get("count") == 0
    if not same_host_isolated and not (services_observed_inert and processes_observed_inert):
        blockers.append("brain_runtime_not_observably_inert")
    basic.update(
        {
            "schema": SCHEMA,
            "operation": "inspect",
            "status": "ready_for_inert_restore" if not blockers else "blocked",
            "execution_commit": git.get("head"),
            "execution_checkout_clean": git.get("clean"),
            "entry_card": {
                "checkout_sha256": source_card,
                "kingston_sha256": kingston_card,
                "pinned": handoff["entry_card_sha256"],
            },
            "source_root_states": root_states,
            **storage[0],
            "host_observation": {
                "launchd_state": launchd.get("state", "unknown"),
                "loaded_brain_d43_labels": launchd.get("loaded_count"),
                "workspace_process_state": processes.get("state", "unknown"),
                "workspace_process_count": processes.get("count"),
                "arguments_read": False,
                "environment_read": False,
                "scope": "same_host_isolated_only"
                if same_host_isolated
                else "host_identity_unverified",
                "clean_host_claim": False,
            },
            "target_device": None if target_stat is None else target_stat.st_dev,
            "target_parent_identity": target_identity,
            "run_parent_identity": run_identity if run_parent is not None else None,
            "storage_device": storage_info.get("device"),
            "run_parent_safe_for_records": run_parent is not None
            and run_identity is not None
            and source_roots_resolved
            and storage_root_verified
            and storage_info.get("state") == "present"
            and storage_info.get("device") is not None
            and not any(
                code in blockers
                for code in (
                    "run_parent_path_not_absolute_canonical",
                    "run_parent_permissions_unsafe",
                    "run_parent_directory_path_invalid",
                    "run_parent_directory_open_failed",
                    "run_records_must_be_outside_kingston",
                    "run_parent_overlaps_forbidden_root",
                    "run_parent_overlaps_target_parent",
                    "run_parent_probe_failed",
                )
            ),
            "target_policy": handoff["target_policy"],
            "activation_authorized": False,
            "final_readiness": False,
            "safe_to_erase": False,
            "blockers": sorted(set(blockers)),
            "handoff_sha256": hashlib.sha256(raw_handoff).hexdigest(),
        }
    )
    return basic


def _blocked(blockers: list[str], *, handoff_sha256: str | None = None) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "operation": "inspect",
        "status": "blocked",
        "activation_authorized": False,
        "final_readiness": False,
        "safe_to_erase": False,
        "blockers": blockers,
        "handoff_sha256": handoff_sha256 if SHA256_RE.fullmatch(handoff_sha256 or "") else None,
    }


def _reserve_run_directory(
    parent: Path,
    handoff_id: str,
    *,
    expected_identity: dict[str, int],
    storage_device: int | None,
    storage_config: Path,
    expected_volume_uuid: str,
    expected_root_id: str,
    forbidden_roots: list[Path],
    target_parent: Path,
) -> tuple[int, Path]:
    if not parent.is_absolute() or ".." in parent.parts:
        raise RestoreFlowError("run_parent_path_not_absolute_canonical")
    if parent.resolve(strict=True) != parent:
        raise RestoreFlowError("run_parent_path_not_canonical")
    parent_fd, metadata = _validated_directory(parent, "run_parent")
    parent_path = Path(os.path.realpath(parent))
    if (metadata.st_dev, metadata.st_ino) != (
        expected_identity.get("device"),
        expected_identity.get("inode"),
    ):
        os.close(parent_fd)
        raise RestoreFlowError("run_parent_identity_changed_since_inspection")
    if storage_device is not None and metadata.st_dev == storage_device:
        os.close(parent_fd)
        raise RestoreFlowError("run_records_must_be_outside_kingston")
    if any(_path_overlaps(parent_path, root.resolve(strict=False)) for root in forbidden_roots):
        os.close(parent_fd)
        raise RestoreFlowError("run_parent_overlaps_forbidden_root")
    if _path_overlaps(parent_path, Path(os.path.realpath(target_parent))):
        os.close(parent_fd)
        raise RestoreFlowError("run_parent_overlaps_target_parent")
    try:
        _revalidate_storage_binding(
            storage_config, expected_volume_uuid, expected_root_id, storage_device
        )
    except Exception:
        os.close(parent_fd)
        raise
    for _ in range(10):
        name = f"brain-restore-run-{handoff_id}-{secrets.token_hex(4)}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        os.fchmod(fd, 0o700)
        os.fsync(parent_fd)
        path = parent / name
        os.close(parent_fd)
        return fd, path
    os.close(parent_fd)
    raise RestoreFlowError("run_directory_collision")


def _revalidate_target_parent(
    target_parent: Path,
    expected_identity: dict[str, int] | None,
    *,
    storage_device: int | None,
    forbidden_roots: list[Path],
) -> None:
    if not target_parent.is_absolute() or ".." in target_parent.parts:
        raise RestoreFlowError("target_parent_path_not_absolute_canonical")
    if target_parent.resolve(strict=True) != target_parent:
        raise RestoreFlowError("target_parent_path_not_canonical")
    target_fd, metadata = _validated_directory(target_parent, "target_parent", must_be_empty=True)
    try:
        if expected_identity is None or (metadata.st_dev, metadata.st_ino) != (
            expected_identity.get("device"),
            expected_identity.get("inode"),
        ):
            raise RestoreFlowError("target_parent_identity_changed_since_inspection")
        if storage_device is not None and metadata.st_dev == storage_device:
            raise RestoreFlowError("target_must_be_on_separate_device")
        target_path = Path(os.path.realpath(target_parent))
        if any(_path_overlaps(target_path, root.resolve(strict=False)) for root in forbidden_roots):
            raise RestoreFlowError("target_overlaps_forbidden_root")
    finally:
        os.close(target_fd)


def restore_inert(
    *,
    handoff_path: Path,
    expected_handoff_sha256: str,
    repository_root: Path,
    storage_config: Path,
    target_parent: Path,
    run_parent: Path,
    same_host_isolated: bool = False,
) -> dict[str, Any]:
    """Create a private run record, then extract only if every inspect gate passes."""
    handoff, raw_handoff = _read_handoff(handoff_path, expected_handoff_sha256)
    preflight = inspect_restore(
        handoff_path=handoff_path,
        expected_handoff_sha256=expected_handoff_sha256,
        repository_root=repository_root,
        storage_config=storage_config,
        target_parent=target_parent,
        run_parent=run_parent,
        same_host_isolated=same_host_isolated,
    )
    if not preflight.get("run_parent_safe_for_records"):
        return {
            **preflight,
            "operation": "restore-inert",
            "status": "blocked_without_run_record_parent_unsafe",
            "run_record_created": False,
            "quarantine_created": False,
            "kingston_restore_witness_written": False,
        }
    forbidden_roots = [
        repository_root,
        *(Path(item["path"]) for item in handoff["source_roots"].values()),
    ]
    run_fd, run_path = _reserve_run_directory(
        run_parent,
        handoff["handoff_id"],
        expected_identity=preflight["run_parent_identity"],
        storage_device=preflight.get("storage_device"),
        storage_config=storage_config,
        expected_volume_uuid=handoff["kingston"]["volume_uuid"],
        expected_root_id=handoff["kingston"]["root_id"],
        forbidden_roots=forbidden_roots,
        target_parent=target_parent,
    )
    conductor_payload: dict[str, Any] | None = None
    try:
        _write_create_only(run_fd, "REQUEST.JSON", raw_handoff)
        _write_create_only(run_fd, "INSPECT.JSON", _canonical(preflight))
        if preflight["blockers"]:
            result = {
                **preflight,
                "operation": "restore-inert",
                "status": "blocked_before_extraction",
                "run_record_path": str(run_path),
                "quarantine_created": False,
                "kingston_restore_witness_written": False,
            }
            _write_create_only(run_fd, "BLOCKED.JSON", _canonical(result))
            return result

        from tools import brain_host_inert_restore_v1 as conductor
        from tools import brain_host_restore_assessment as assessor
        from tools import brain_host_tree_custody_v3 as tree_v3

        _revalidate_target_parent(
            target_parent,
            preflight.get("target_parent_identity"),
            storage_device=preflight.get("storage_device"),
            forbidden_roots=forbidden_roots,
        )

        pins = handoff["kingston"]
        set_pin = handoff["recovery_set"]
        source_roots = [Path(item["path"]) for item in handoff["source_roots"].values()]
        forbidden = list(dict.fromkeys(source_roots))
        with tree_v3._storage_writer(storage_config) as writer:
            guard = writer._custody_mutation_guard.storage_guard

            def revalidate_identity() -> tuple[str, str]:
                resolved = guard.revalidate()
                return resolved.volume_uuid, resolved.root_id

            result = conductor.restore_inert_v1(
                writer,
                set_id=set_pin["set_id"],
                expected_index_sha256=set_pin["index_sha256"],
                expected_volume_uuid=pins["volume_uuid"],
                expected_root_id=pins["root_id"],
                revalidate_identity=revalidate_identity,
                target_parent=target_parent,
                repository_root=repository_root,
                storage_root=guard.mount_path / guard.config.data_root,
                forbidden_roots=forbidden,
            )
            conductor_payload = result.payload
        _write_create_only(run_fd, "CONDUCTOR.JSON", _canonical(conductor_payload))
        saved_conductor = assessor._load_restore_handoff(run_path / "CONDUCTOR.JSON")
        handoff_result = saved_conductor
        expected_handoff = {
            "set_id": set_pin["set_id"],
            "expected_commit": handoff["execution_commit"],
            "expected_volume_uuid": pins["volume_uuid"],
            "expected_root_id": pins["root_id"],
            "expected_index_sha256": set_pin["index_sha256"],
            "repo": str(repository_root),
        }
        if not isinstance(handoff_result, dict) or any(
            handoff_result.get(key) != value for key, value in expected_handoff.items()
        ):
            raise RestoreFlowError("conductor_handoff_does_not_match_independent_pins")
        targets = {role: Path(path) for role, path in handoff_result["witness_targets"].items()}
        with assessor.open_readonly_storage_reader(storage_config) as (reader, revalidate):
            assessment = assessor.assess_restore_v1(
                reader,
                repo=repository_root,
                expected_commit=handoff["execution_commit"],
                expected_volume_uuid=pins["volume_uuid"],
                expected_root_id=pins["root_id"],
                expected_index_sha256=set_pin["index_sha256"],
                expected_witness_sha256=handoff_result["expected_witness_sha256"],
                set_id=set_pin["set_id"],
                witness_id=handoff_result["witness_id"],
                witness_targets=targets,
                revalidate_identity=revalidate,
            )
        _write_create_only(run_fd, "ASSESSMENT.JSON", _canonical(assessment))
        final = {
            "schema": SCHEMA,
            "status": "inert_extraction_complete_readiness_blocked",
            "run_record_path": str(run_path),
            "conductor_sha256": hashlib.sha256(_canonical(conductor_payload)).hexdigest(),
            "assessment_sha256": hashlib.sha256(_canonical(assessment)).hexdigest(),
            "inert_extraction_verified": conductor_payload.get("status")
            == "complete_inert_quarantine",
            "activation_authorized": False,
            "final_readiness": False,
            "safe_to_erase": False,
            "assessment_final_readiness": assessment.get("final_readiness", False),
        }
        critical_assessor_gates = {
            "kingston_identity",
            "storage_capacity",
            "accepted_set_pin",
            "research_commit",
            "restore_witness",
            "accepted_witness_pin",
            "v1_set_integrity",
            "kingston_bound_root_stable",
        }
        gates = assessment.get("gates")
        passed_gates = (
            {
                gate.get("name")
                for gate in gates
                if isinstance(gate, dict) and gate.get("state") == "pass"
            }
            if isinstance(gates, list)
            else set()
        )
        if (
            final["inert_extraction_verified"] is not True
            or assessment.get("status") != "blocked"
            or assessment.get("final_readiness") is not False
            or not critical_assessor_gates.issubset(passed_gates)
        ):
            raise RestoreFlowError("conductor_result_not_complete_inert_extraction")
        _write_create_only(run_fd, "RUN_COMPLETE.JSON", _canonical(final))
        return {**final, "assessment_status": assessment.get("status")}
    except Exception as exc:
        failure = {
            "schema": SCHEMA,
            "status": "partial_or_failed_run_retained",
            "run_record_path": str(run_path),
            "error_class": type(exc).__name__,
            "error_code": str(exc).split(";", 1)[0] if isinstance(exc, RestoreFlowError) else None,
            "conductor_status": None
            if conductor_payload is None
            else conductor_payload.get("status"),
            "activation_authorized": False,
            "final_readiness": False,
            "safe_to_erase": False,
            "partial_output_policy": "retain_for_review_do_not_resume_or_overwrite",
        }
        try:
            _write_create_only(run_fd, "FAILURE.JSON", _canonical(failure))
        except FileExistsError:
            pass
        return failure
    finally:
        os.close(run_fd)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "restore-inert"):
        command = sub.add_parser(name)
        command.add_argument("--handoff", type=Path, required=True)
        command.add_argument(
            "--handoff-sha256",
            required=True,
            help="pin delivered independently of Kingston and the handoff file",
        )
        command.add_argument("--repo", type=Path, required=True)
        command.add_argument("--storage-config", type=Path, required=True)
        command.add_argument("--target-parent", type=Path, required=True)
        command.add_argument(
            "--same-host-isolated",
            action="store_true",
            help="permit a same-host quarantine rehearsal; never claims a clean replacement host",
        )
        if name == "restore-inert":
            command.add_argument("--run-parent", type=Path, required=True)
        else:
            command.add_argument("--run-parent", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_restore(
                handoff_path=args.handoff,
                expected_handoff_sha256=args.handoff_sha256,
                repository_root=args.repo,
                storage_config=args.storage_config,
                target_parent=args.target_parent,
                run_parent=args.run_parent,
                same_host_isolated=args.same_host_isolated,
            )
        else:
            result = restore_inert(
                handoff_path=args.handoff,
                expected_handoff_sha256=args.handoff_sha256,
                repository_root=args.repo,
                storage_config=args.storage_config,
                target_parent=args.target_parent,
                run_parent=args.run_parent,
                same_host_isolated=args.same_host_isolated,
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return (
            0
            if result.get("status")
            in {"ready_for_inert_restore", "inert_extraction_complete_readiness_blocked"}
            else 2
        )
    except RestoreFlowError as exc:
        print(json.dumps(_blocked([str(exc)]), indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
