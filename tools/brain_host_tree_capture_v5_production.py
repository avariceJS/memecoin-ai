#!/usr/bin/env python3
"""Production-gated v5 capture factory for externally pinned held Data and storage."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

if __package__ in {None, ""}:
    _REPO_ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_REPO_ROOT))
    sys.path.insert(0, str(_REPO_ROOT / "src"))

import memecoin_research_storage as storage
from memecoin_research_storage import ReadOnlyStorageGuard, StorageConfig, StorageGuard
from tools import brain_host_tree_capture_v5 as capture
from tools.brain_host_held_data_t_cli_v1 import _load_plan
from tools.brain_host_held_data_t_v1 import (
    REQUIRED_ROOT_ROLES,
    ApprovedDataPlan,
    DarwinDataOS,
    HeldDataT,
    approved_data_plan_sha256,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SCHEMA = "brain-host-tree-capture-v5-production-session"
CAPTURE_SPEC_SCHEMA = "brain-host-tree-capture-v5-operation-spec-v1"
MAX_CAPTURE_SPEC_BYTES = 128 * 1024
_CAPTURE_PUBLIC_ERRORS = frozenset(
    {
        "arguments_invalid",
        "capture_absent_role_fields_invalid",
        "capture_absent_role_reason_mismatch",
        "capture_failed",
        "capture_json_duplicate_key",
        "capture_json_nonstandard_constant",
        "capture_metadata_reserve_invalid",
        "capture_payload_reserve_invalid",
        "capture_plan_invalid",
        "capture_plan_pin_mismatch",
        "capture_plan_root_roster_invalid",
        "capture_plan_sha256_invalid",
        "capture_policy_path_invalid",
        "capture_policy_pin_mismatch",
        "capture_policy_roster_pin_mismatch",
        "capture_policy_roster_sha256_invalid",
        "capture_policy_sha256_invalid",
        "capture_present_role_disposition_invalid",
        "capture_present_role_fields_invalid",
        "capture_role_declaration_invalid",
        "capture_role_roster_invalid",
        "capture_role_scope_invalid",
        "capture_scratch_reserve_invalid",
        "capture_source_mountpoint_invalid",
        "capture_source_target_volume_alias",
        "capture_spec_json_invalid",
        "capture_spec_path_invalid",
        "capture_spec_private_file_invalid",
        "capture_spec_read_failed",
        "capture_spec_schema_fields_invalid",
        "capture_spec_schema_invalid",
        "capture_spec_sha256_invalid",
        "capture_spec_sha256_mismatch",
        "capture_spec_size_changed",
        "capture_storage_config_path_invalid",
        "capture_target_storage_pin_invalid",
    }
)
_VERIFY_PUBLIC_ERRORS = frozenset(
    {
        "independent_verify_scratch_volume_uuid_pin_required",
        "production_verify_candidate_target_not_read_only",
        "production_verify_cli_pin_invalid",
        "production_verify_plan_pin_invalid",
        "production_verify_read_only_candidate_guard_required",
        "production_verify_scratch_capability_invalid",
        "production_verify_scratch_usage_invalid",
        "production_verify_shared_pool_identity_invalid",
        "production_verify_shared_pool_pin_invalid",
        "production_verify_shared_pool_reserve_invalid",
        "production_verify_shared_pool_source_alias",
        "production_verify_operation_spec_mismatch",
        "scratch_device_separation_invalid",
        "scratch_directory_acl_present",
        "scratch_directory_private_identity_invalid",
        "scratch_filesystem_identity_invalid",
        "scratch_identity_or_capacity_invalid",
        "scratch_mountpoint_identity_invalid",
        "scratch_path_component_not_directory",
        "scratch_path_not_canonical",
        "scratch_path_open_failed",
        "scratch_path_revalidation_failed",
        "scratch_revalidation_failed",
        "scratch_volume_uuid_unresolved",
        "storage_config_path_not_canonical",
        "target_storage_binding_pin_mismatch",
        "target_storage_pin_invalid",
        "verify_scratch_source_volume_alias",
        "verify_scratch_target_volume_alias",
    }
)
PRODUCTION_FREE_SPACE_FLOOR_BYTES = 8 * 1024**3
VERIFY_SCRATCH_DB_RESERVE_BYTES = capture.MAX_SCRATCH_BYTES
VERIFY_SCRATCH_FREE_FLOOR_BYTES = 256 * 1024**2
VERIFY_SHARED_POOL_FLOOR_BYTES = 50 * 1024**3
CAPACITY_PLANNER_DB_LIMIT_BYTES = 8 * 1024**3
CAPACITY_PLANNER_FREE_FLOOR_BYTES = 256 * 1024**2
INERT_QUARANTINE_FREE_FLOOR_BYTES = 256 * 1024**2
INERT_QUARANTINE_SCRATCH_FLOOR_BYTES = 256 * 1024**2
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_ROOT_ID = re.compile(r"[0-9a-f]{32}\Z")


class ProductionCaptureV5Error(ValueError):
    """Fail-closed source/storage binding errors for the v5 production gate."""


class ProductionCaptureV5CLIError(ValueError):
    """Path-free stable operator input or execution failure."""


def _apfs_container_uuid(container_reference: str) -> str:
    if re.fullmatch(r"disk[0-9]+", container_reference) is None:
        raise ProductionCaptureV5Error("scratch_volume_uuid_unresolved")
    try:
        payload = DarwinDataOS._run_plist(["apfs", "list", "-plist", container_reference])
        rows = payload.get("Containers")
        matches = [
            row
            for row in rows or []
            if isinstance(row, dict) and row.get("ContainerReference") == container_reference
        ]
        value = matches[0].get("APFSContainerUUID") if len(matches) == 1 else None
        if not isinstance(value, str) or _UUID.fullmatch(value.lower()) is None:
            raise ValueError("invalid container uuid")
        return value.lower()
    except Exception as exc:
        raise ProductionCaptureV5Error("scratch_volume_uuid_unresolved") from exc


def _verify_scratch_required_free_bytes(
    used_bytes: int,
    reserve_bytes: int,
    *,
    shared_pool_mode: bool = False,
    shared_pool_headroom_bytes: int = 0,
) -> int:
    if (
        type(used_bytes) is not int
        or type(reserve_bytes) is not int
        or type(shared_pool_mode) is not bool
        or type(shared_pool_headroom_bytes) is not int
        or not 0 <= used_bytes <= reserve_bytes <= VERIFY_SCRATCH_DB_RESERVE_BYTES
        or shared_pool_headroom_bytes < 0
    ):
        raise ProductionCaptureV5Error("production_verify_scratch_usage_invalid")
    return (
        VERIFY_SCRATCH_FREE_FLOOR_BYTES
        + (VERIFY_SHARED_POOL_FLOOR_BYTES if shared_pool_mode else 0)
        + shared_pool_headroom_bytes
        + max(0, reserve_bytes - used_bytes)
    )


def _assert_candidate_target_read_only(guard: ReadOnlyStorageGuard) -> None:
    try:
        vfs = os.fstatvfs(guard.mount_fd)
        filesystem = DarwinDataOS().filesystem(guard.mount_fd)
        readonly_flag = getattr(os, "ST_RDONLY", 1)
        if (
            not (vfs.f_flag & readonly_flag)
            or not isinstance(filesystem.get("flags"), int)
            or not (filesystem["flags"] & readonly_flag)
        ):
            raise ProductionCaptureV5Error("production_verify_candidate_target_not_read_only")
    except ProductionCaptureV5Error:
        raise
    except Exception as exc:
        raise ProductionCaptureV5Error("production_verify_candidate_target_not_read_only") from exc


# Exact exception types and literal messages only. Do not classify by prefix,
# substring, inheritance, or arbitrary exception text: messages may contain paths
# or candidate-controlled/private bytes. Unknown failures remain deliberately generic.
_EXTRACT_ERROR_LITERALS = {
    ProductionCaptureV5CLIError: {
        "input_pin_invalid": ("arguments_invalid",),
    },
    ProductionCaptureV5Error: {
        "input_pin_invalid": (
            "target_storage_pin_invalid",
            "production_extract_external_pin_invalid",
            "production_verify_cli_pin_invalid",
        ),
        "plan_or_storage_pin_mismatch": (
            "production_extract_source_plan_invalid",
            "production_extract_source_plan_pin_mismatch",
            "production_extract_storage_config_invalid",
            "production_extract_storage_config_pin_mismatch",
            "target_storage_binding_pin_mismatch",
            "storage_config_path_not_canonical",
        ),
        "target_not_read_only": ("production_extract_target_not_read_only",),
        "volume_separation_failed": (
            "production_extract_volume_uuid_alias",
            "production_extract_scratch_volume_alias",
            "production_extract_scratch_device_alias",
            "production_extract_source_storage_device_alias",
            "scratch_device_separation_invalid",
        ),
        "scratch_unavailable": (
            "production_verify_scratch_capability_invalid",
            "production_extract_scratch_filesystem_invalid",
            "production_extract_scratch_volume_unpinned",
            "production_extract_scratch_usage_invalid",
            "scratch_path_not_canonical",
            "scratch_path_component_not_directory",
            "scratch_path_open_failed",
            "scratch_directory_private_identity_invalid",
            "scratch_directory_acl_present",
            "scratch_volume_uuid_unresolved",
            "scratch_path_revalidation_failed",
            "scratch_identity_or_capacity_invalid",
            "scratch_mountpoint_identity_invalid",
            "scratch_revalidation_failed",
            "scratch_filesystem_identity_invalid",
        ),
        "destination_unavailable": (
            "production_extract_destination_identity_invalid",
            "production_extract_destination_acl_present",
        ),
        "capacity_below_reserve": ("production_extract_capacity_below_reserve",),
        # These known failures do not identify any narrower public category.
        "extract_inert_failed": (
            "production_extract_target_acl_present",
            "production_extract_capacity_request_invalid",
            "production_extract_mount_identity_invalid",
            "production_extract_source_mount_unresolved",
            "production_verify_read_only_candidate_guard_required",
        ),
    },
    capture.TreeCaptureV5Error: {
        "input_pin_invalid": ("inert_extraction_external_pins_required",),
        "scratch_unavailable": (
            "candidate_verify_scratch_unavailable",
            "candidate_verify_scratch_create_failed",
            "inert_extraction_scratch_invalid",
            "inert_extraction_scratch_identity_invalid",
            "inert_extraction_scratch_database_replaced",
            "inert_extraction_scratch_limit",
        ),
        "destination_unavailable": (
            "inert_quarantine_parent_invalid",
            "inert_quarantine_root_replaced",
            "inert_destination_directory_invalid",
            "inert_destination_directory_replaced",
            "inert_destination_directory_acl_present",
            "inert_destination_write_failed",
        ),
        "extract_inert_failed": ("inert_destination_directory_collision",),
        "candidate_integrity_failed": (
            "candidate_index_pin_mismatch",
            "candidate_page_hash_mismatch",
            "candidate_data_hash_mismatch",
            "candidate_readback_invalid",
            "candidate_root_replaced",
            "candidate_index_changed_after_verify",
            "inert_member_page_hash_mismatch",
            "inert_payload_page_invalid",
            "inert_payload_digest_mismatch",
            "inert_member_kind_invalid",
        ),
        "quarantine_readback_failed": (
            "inert_readback_member_identity_invalid",
            "inert_readback_directory_mode_invalid",
            "inert_readback_directory_replaced",
            "inert_readback_directory_acl_present",
            "inert_readback_file_identity_invalid",
            "inert_readback_file_acl_present",
            "inert_readback_file_size_changed",
            "inert_readback_unexpected_link_or_type",
            "inert_readback_member_missing",
            "inert_readback_member_unexpected",
            "inert_readback_content_mismatch",
            "inert_readback_hardlink_topology_mismatch",
            "inert_extraction_replaced_during_readback",
            "inert_extraction_replaced_after_readback",
        ),
    },
    storage.StorageError: {
        "plan_or_storage_pin_mismatch": (
            "volume_uuid_mismatch",
            "root_marker_binding_mismatch",
            "binding_changed_during_read",
            "binding_changed_under_lock",
            "mounted_directory_replaced",
            "research_root_replaced",
            "research_root_device_mismatch",
            "unsafe_root_marker",
            "root_marker_too_large",
            "root_marker_changed_during_read",
        ),
        "capacity_below_reserve": ("insufficient_free_space",),
        "destination_unavailable": (
            "volume_not_writable",
            "unsafe_writer_lock",
            "writer_lock_replaced",
        ),
        # These guards are shared by candidate and destination. Without a phase
        # binding their literals cannot safely identify which volume needs repair.
        "extract_inert_failed": (
            "volume_not_mounted",
            "mount_missing",
            "mountpoint_directory_fallback",
            "volume_uuid_unavailable",
            "filesystem_not_case_sensitive_apfs",
            "filesystem_type_not_apfs",
            "unsafe_mount_options",
            "volume_owners_disabled",
            "research_root_missing",
            "symlink_root_component",
            "unsafe_root_ancestor",
            "unsafe_root_owner_or_mode",
            "symlink_path_rejected",
        ),
    },
}
_EXTRACT_NEXT_STEPS = {
    "input_pin_invalid": "check_external_pins_and_arguments",
    "plan_or_storage_pin_mismatch": "reconcile_pinned_plan_and_storage",
    "target_not_read_only": "remount_candidate_volume_read_only",
    "volume_separation_failed": "choose_distinct_pinned_volumes",
    "scratch_unavailable": "prepare_private_pinned_scratch",
    "destination_unavailable": "prepare_private_pinned_destination",
    "capacity_below_reserve": "provide_capacity_above_reserve",
    "candidate_integrity_failed": "reverify_candidate_from_trusted_pins",
    "quarantine_readback_failed": "inspect_unaccepted_quarantine_without_starting",
    "extract_inert_failed": "inspect_local_failure_without_starting",
}


def _extract_failure(exc: Exception) -> dict[str, Any]:
    """Return only static public diagnostics, never exception-supplied content."""
    category = "extract_inert_failed"
    known_groups = _EXTRACT_ERROR_LITERALS.get(type(exc))
    if known_groups is not None and len(exc.args) == 1 and type(exc.args[0]) is str:
        for known_category, literals in known_groups.items():
            if exc.args[0] in literals:
                category = known_category
                break
    return {
        "schema": "brain-host-tree-capture-v5-inert-quarantine-result",
        "ok": False,
        "error": category,
        "next_step": _EXTRACT_NEXT_STEPS[category],
        "quarantine_rehearsal_only": True,
        "unaccepted_partial_quarantine_may_remain": True,
        "sqlite_scratch_role": "private_readback_index_only",
        "start_or_restore_performed": False,
        "restore_verified": False,
        "T_qualified": False,
        "erase_ready": False,
    }


class _OperatorArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        raise ProductionCaptureV5CLIError("arguments_invalid") from None


def _json_no_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductionCaptureV5CLIError("capture_json_duplicate_key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ProductionCaptureV5CLIError("capture_json_nonstandard_constant")


def _read_private_capture_spec(path: Path, expected_sha256: str) -> dict[str, Any]:
    if not _valid_sha256(expected_sha256):
        raise ProductionCaptureV5CLIError("capture_spec_sha256_invalid")
    try:
        canonical = _canonical_config_path(path)
        fd = os.open(canonical, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except Exception as exc:
        raise ProductionCaptureV5CLIError("capture_spec_path_invalid") from exc
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= MAX_CAPTURE_SPEC_BYTES
        ):
            raise ProductionCaptureV5CLIError("capture_spec_private_file_invalid")
        raw = bytearray()
        while len(raw) <= MAX_CAPTURE_SPEC_BYTES:
            chunk = os.read(fd, min(8192, MAX_CAPTURE_SPEC_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        if len(raw) != metadata.st_size or len(raw) > MAX_CAPTURE_SPEC_BYTES:
            raise ProductionCaptureV5CLIError("capture_spec_size_changed")
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ProductionCaptureV5CLIError("capture_spec_sha256_mismatch")
    except ProductionCaptureV5CLIError:
        raise
    except OSError as exc:
        raise ProductionCaptureV5CLIError("capture_spec_read_failed") from exc
    finally:
        os.close(fd)
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_json_no_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except ProductionCaptureV5CLIError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProductionCaptureV5CLIError("capture_spec_json_invalid") from exc
    if not isinstance(document, dict):
        raise ProductionCaptureV5CLIError("capture_spec_schema_invalid")
    return document


def _require_canonical_directory(value: object, error: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ProductionCaptureV5CLIError(error)
    path = Path(value)
    try:
        if (
            not path.is_absolute()
            or ".." in path.parts
            or Path(os.path.normpath(path)) != path
            or path.resolve(strict=True) != path
            or not stat.S_ISDIR(path.lstat().st_mode)
        ):
            raise ProductionCaptureV5CLIError(error)
    except ProductionCaptureV5CLIError:
        raise
    except OSError as exc:
        raise ProductionCaptureV5CLIError(error) from exc
    return path


def _require_sha256(value: object, error: str) -> str:
    if not _valid_sha256(value):
        raise ProductionCaptureV5CLIError(error)
    return str(value)


def _capture_spec_fields(spec: Mapping[str, Any]) -> None:
    expected = {
        "schema",
        "source_mountpoint",
        "storage_config_path",
        "target_volume_uuid",
        "target_root_id",
        "scratch_reserve_bytes",
        "metadata_reserve_bytes",
        "payload_reserve_bytes",
        "role_roster",
    }
    if set(spec) != expected or spec.get("schema") != CAPTURE_SPEC_SCHEMA:
        raise ProductionCaptureV5CLIError("capture_spec_schema_fields_invalid")


def _load_capture_policies(
    spec: Mapping[str, Any], plan: ApprovedDataPlan, plan_sha256: str
) -> tuple[dict[str, capture.RoleCapturePolicy], str]:
    roster = spec.get("role_roster")
    if not isinstance(roster, dict) or set(roster) != REQUIRED_ROOT_ROLES:
        raise ProductionCaptureV5CLIError("capture_role_roster_invalid")
    policies: dict[str, capture.RoleCapturePolicy] = {}
    digest_rows: list[Mapping[str, Any]] = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        row = roster[role]
        declaration = plan.root_roster[role]
        if not isinstance(row, dict):
            raise ProductionCaptureV5CLIError("capture_role_declaration_invalid")
        if declaration.present:
            if set(row) != {"disposition", "policy_path", "policy_sha256", "scope"}:
                raise ProductionCaptureV5CLIError("capture_present_role_fields_invalid")
            if row.get("disposition") != "present":
                raise ProductionCaptureV5CLIError("capture_present_role_disposition_invalid")
            scope = row.get("scope")
            if scope not in {"non_protected", "protected_opaque"}:
                raise ProductionCaptureV5CLIError("capture_role_scope_invalid")
            policy_path_value = row.get("policy_path")
            if not isinstance(policy_path_value, str) or not policy_path_value:
                raise ProductionCaptureV5CLIError("capture_policy_path_invalid")
            policy_path = Path(policy_path_value)
            try:
                if (
                    not policy_path.is_absolute()
                    or ".." in policy_path.parts
                    or Path(os.path.normpath(policy_path)) != policy_path
                    or policy_path.resolve(strict=True) != policy_path
                    or not stat.S_ISREG(policy_path.lstat().st_mode)
                ):
                    raise ProductionCaptureV5CLIError("capture_policy_path_invalid")
            except ProductionCaptureV5CLIError:
                raise
            except OSError as exc:
                raise ProductionCaptureV5CLIError("capture_policy_path_invalid") from exc
            policy_sha = _require_sha256(row.get("policy_sha256"), "capture_policy_sha256_invalid")
            policy = capture.RoleCapturePolicy(policy_path, policy_sha, scope)
            try:
                _value, _raw, actual = capture._validate_policy_pin(policy)
            except Exception as exc:
                raise ProductionCaptureV5CLIError("capture_policy_pin_mismatch") from exc
            if actual != policy_sha:
                raise ProductionCaptureV5CLIError("capture_policy_pin_mismatch")
            policies[role] = policy
            digest_rows.append(
                {
                    "role": role,
                    "present": True,
                    "scope": scope,
                    "policy_sha256": policy_sha,
                }
            )
        else:
            if set(row) != {"disposition", "unresolved_reason"}:
                raise ProductionCaptureV5CLIError("capture_absent_role_fields_invalid")
            reason = row.get("unresolved_reason")
            if (
                row.get("disposition") != "absent_unresolved"
                or not isinstance(reason, str)
                or reason != declaration.unresolved_reason
            ):
                raise ProductionCaptureV5CLIError("capture_absent_role_reason_mismatch")
            digest_rows.append(
                {
                    "role": role,
                    "present": False,
                    "disposition": "absent_unresolved",
                    "unresolved_reason_sha256": sha256(reason.encode("utf-8")),
                    "scope": None,
                    "policy_sha256": None,
                }
            )
    try:
        digest = capture.policy_roster_sha256_v5(plan_sha256, digest_rows)
    except Exception as exc:
        raise ProductionCaptureV5CLIError("capture_role_roster_invalid") from exc
    return policies, digest


def _positive_bounded_int(value: object, *, minimum: int, maximum: int, error: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProductionCaptureV5CLIError(error)
    return value


def _capture_summary(
    receipt: Mapping[str, Any],
    *,
    operation_spec_sha256: str,
    plan_sha256: str,
    policy_roster_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": "brain-host-tree-capture-v5-operator-result",
        "ok": True,
        "operation_spec_sha256": operation_spec_sha256,
        "plan_sha256": plan_sha256,
        "policy_roster_sha256": policy_roster_sha256,
        "candidate_id": receipt["candidate_id"],
        "index_sha256": receipt["index_sha256"],
        "entry_count": receipt["entry_count"],
        "payload_bytes": receipt["payload_bytes"],
        "readback_verified": receipt["readback_verified"],
        "synthetic_fixture_only": receipt["synthetic_fixture_only"],
        "clean_shutdown_verified": False,
        "prior_writable_mount_history_verified": False,
        "source_cutoff_verified": False,
        "root_closure_verified": False,
        "protected_closure_verified": False,
        "T_qualified": False,
        "capture_complete": False,
        "kingston_durable": False,
        "restore_verified": False,
        "erase_ready": False,
        "activation_permitted": False,
    }


def _capture_failure(error: str, *, partial_candidate_possible: bool) -> dict[str, Any]:
    stable = error if error in _CAPTURE_PUBLIC_ERRORS else "capture_failed"
    return {
        "schema": "brain-host-tree-capture-v5-operator-result",
        "ok": False,
        "error": stable,
        "unaccepted_partial_candidate_may_remain": partial_candidate_possible,
        "clean_shutdown_verified": False,
        "prior_writable_mount_history_verified": False,
        "source_cutoff_verified": False,
        "root_closure_verified": False,
        "protected_closure_verified": False,
        "T_qualified": False,
        "capture_complete": False,
        "kingston_durable": False,
        "restore_verified": False,
        "erase_ready": False,
        "activation_permitted": False,
    }


def _open_absolute_directory_nofollow(path: Path) -> tuple[int, tuple[tuple[int, ...], ...]]:
    if (
        not path.is_absolute()
        or ".." in path.parts
        or Path(os.path.normpath(path)) != path
        or path == Path("/")
    ):
        raise ProductionCaptureV5Error("scratch_path_not_canonical")
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    identities: list[tuple[int, ...]] = [_scratch_path_sig(os.fstat(current))]
    try:
        for component in path.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            child_stat = os.fstat(child)
            if not stat.S_ISDIR(child_stat.st_mode):
                os.close(child)
                raise ProductionCaptureV5Error("scratch_path_component_not_directory")
            identities.append(_scratch_path_sig(child_stat))
            os.close(current)
            current = child
        return current, tuple(identities)
    except BaseException:
        os.close(current)
        raise


def _scratch_path_sig(st: os.stat_result) -> tuple[int, ...]:
    return st.st_dev, st.st_ino, st.st_mode, st.st_uid


@dataclass
class PinnedScratchV5:
    """Held writable RecoveryOS scratch directory with a pinned no-follow path chain."""

    path: Path
    fd: int
    identity: tuple[int, int]
    chain_identities: tuple[tuple[int, ...], ...]
    filesystem_identity: tuple[str, tuple[int, ...], str, str]
    volume_uuid: str | None
    apfs_container_reference: str | None
    apfs_container_uuid: str | None

    @classmethod
    def open(cls, path: Path) -> PinnedScratchV5:
        try:
            held_fd, chain = _open_absolute_directory_nofollow(Path(path))
        except ProductionCaptureV5Error:
            raise
        except Exception as exc:
            raise ProductionCaptureV5Error("scratch_path_open_failed") from exc
        try:
            st = os.fstat(held_fd)
            if (
                not stat.S_ISDIR(st.st_mode)
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o700
            ):
                raise ProductionCaptureV5Error("scratch_directory_private_identity_invalid")
            if capture._acl_bytes(held_fd) is not None:
                raise ProductionCaptureV5Error("scratch_directory_acl_present")
            fs = DarwinDataOS().filesystem(held_fd)
            fs_identity = _scratch_filesystem_identity(fs)
            volume_uuid = None
            apfs_container_reference = None
            apfs_container_uuid = None
            if fs_identity[0] in {"apfs", "hfs"}:
                try:
                    info = DarwinDataOS().disk_info(fs_identity[2])
                    candidate_uuid = info.get("VolumeUUID", info.get("APFSVolumeUUID"))
                    if isinstance(candidate_uuid, str) and _UUID.fullmatch(candidate_uuid.lower()):
                        volume_uuid = candidate_uuid.lower()
                    candidate_container = info.get("APFSContainerReference")
                    if isinstance(candidate_container, str) and re.fullmatch(
                        r"disk[0-9]+", candidate_container
                    ):
                        apfs_container_reference = candidate_container
                        apfs_container_uuid = _apfs_container_uuid(candidate_container)
                except Exception as exc:
                    raise ProductionCaptureV5Error("scratch_volume_uuid_unresolved") from exc
            if fs_identity[0] == "apfs" and (
                apfs_container_reference is None or apfs_container_uuid is None
            ):
                raise ProductionCaptureV5Error("scratch_volume_uuid_unresolved")
            return cls(
                Path(path),
                held_fd,
                (st.st_dev, st.st_ino),
                chain,
                fs_identity,
                volume_uuid,
                apfs_container_reference,
                apfs_container_uuid,
            )
        except BaseException:
            os.close(held_fd)
            raise

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def validate(
        self,
        *,
        source_device: int | None,
        target_device: int,
        required_free_bytes: int,
    ) -> int:
        if type(required_free_bytes) is not int or required_free_bytes < 0:
            raise ProductionCaptureV5Error("scratch_capacity_request_invalid")
        try:
            current_fd, current_chain = _open_absolute_directory_nofollow(self.path)
        except Exception as exc:
            raise ProductionCaptureV5Error("scratch_path_revalidation_failed") from exc
        try:
            st = os.fstat(self.fd)
            current = os.fstat(current_fd)
            fs = DarwinDataOS().filesystem(self.fd)
            vfs = os.fstatvfs(self.fd)
            free_bytes = int(vfs.f_bavail * vfs.f_frsize)
            current_disk_info = DarwinDataOS().disk_info(self.filesystem_identity[2])
            current_uuid = current_disk_info.get(
                "VolumeUUID", current_disk_info.get("APFSVolumeUUID")
            )
            current_container = current_disk_info.get("APFSContainerReference")
            current_container_uuid = (
                _apfs_container_uuid(current_container)
                if isinstance(current_container, str)
                else None
            )
            if (
                (st.st_dev, st.st_ino) != self.identity
                or (current.st_dev, current.st_ino) != self.identity
                or current_chain != self.chain_identities
                or st.st_uid != os.getuid()
                or stat.S_IMODE(st.st_mode) != 0o700
                or capture._acl_bytes(self.fd) is not None
                or _scratch_filesystem_identity(fs) != self.filesystem_identity
                or (self.volume_uuid is not None and str(current_uuid).lower() != self.volume_uuid)
                or (
                    self.apfs_container_reference is not None
                    and current_container != self.apfs_container_reference
                )
                or (
                    self.apfs_container_uuid is not None
                    and current_container_uuid != self.apfs_container_uuid
                )
                or fs.get("flags", 0) & getattr(os, "ST_RDONLY", 1)
                or vfs.f_flag & getattr(os, "ST_RDONLY", 1)
                or free_bytes < required_free_bytes
            ):
                raise ProductionCaptureV5Error("scratch_identity_or_capacity_invalid")
            if source_device is None:
                _validate_scratch_target_separation(st.st_dev, target_device)
            else:
                _validate_scratch_devices(st.st_dev, source_device, target_device)
            mountpoint = Path(self.filesystem_identity[2])
            if not mountpoint.is_absolute() or mountpoint.stat().st_dev != st.st_dev:
                raise ProductionCaptureV5Error("scratch_mountpoint_identity_invalid")
            return free_bytes
        except ProductionCaptureV5Error:
            raise
        except Exception as exc:
            raise ProductionCaptureV5Error("scratch_revalidation_failed") from exc
        finally:
            os.close(current_fd)


def _scratch_filesystem_identity(fs: Mapping[str, Any]) -> tuple[str, tuple[int, ...], str, str]:
    fs_type = fs.get("fs_type")
    fsid = fs.get("fsid")
    mountpoint = fs.get("mountpoint")
    source = fs.get("source")
    if (
        fs_type not in {"apfs", "hfs"}
        or not isinstance(fsid, tuple)
        or len(fsid) != 2
        or any(type(value) is not int for value in fsid)
        or fsid == (0, 0)
        or not isinstance(mountpoint, str)
        or not mountpoint.startswith("/")
        or not isinstance(source, str)
        or not source
    ):
        raise ProductionCaptureV5Error("scratch_filesystem_identity_invalid")
    return fs_type, fsid, mountpoint, source


def _validate_scratch_devices(scratch_device: int, source_device: int, target_device: int) -> None:
    if (
        any(
            type(device) is not int or device <= 0
            for device in (scratch_device, source_device, target_device)
        )
        or len({scratch_device, source_device, target_device}) != 3
    ):
        raise ProductionCaptureV5Error("scratch_device_separation_invalid")


def _validate_scratch_target_separation(scratch_device: int, target_device: int) -> None:
    if (
        type(scratch_device) is not int
        or scratch_device <= 0
        or type(target_device) is not int
        or target_device <= 0
        or scratch_device == target_device
    ):
        raise ProductionCaptureV5Error("scratch_device_separation_invalid")


def _canonical_config_path(value: Path) -> Path:
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or Path(os.path.normpath(path)) != path
        or path.resolve(strict=True) != path
    ):
        raise ProductionCaptureV5Error("storage_config_path_not_canonical")
    return path


def _validate_pins(volume_uuid: str, root_id: str) -> tuple[str, str]:
    normalized_uuid = volume_uuid.lower() if isinstance(volume_uuid, str) else ""
    if (
        not _UUID.fullmatch(normalized_uuid)
        or not isinstance(root_id, str)
        or not _ROOT_ID.fullmatch(root_id)
    ):
        raise ProductionCaptureV5Error("target_storage_pin_invalid")
    return normalized_uuid, root_id


def _require_real_held_view(view: HeldDataT, expected_plan_sha256: str) -> int:
    if (
        type(view) is not HeldDataT
        or view.synthetic is not False
        or type(view.os_api) is not DarwinDataOS
        or not _valid_sha256(expected_plan_sha256)
        or view.trusted_plan_sha256 != expected_plan_sha256
        or approved_data_plan_sha256(view.plan) != view.trusted_plan_sha256
    ):
        raise ProductionCaptureV5Error("production_source_view_not_real_or_unpinned")
    try:
        view.revalidate()
    except Exception as exc:
        raise ProductionCaptureV5Error("production_source_revalidation_failed") from exc
    return os.fstat(view.mount_fd).st_dev


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _preflight_policy_roster(
    view: HeldDataT,
    role_policies: Mapping[str, capture.RoleCapturePolicy],
    *,
    plan_sha256: str,
    expected_policy_roster_sha256: str,
) -> str:
    present_roles = {
        role for role, declaration in view.plan.root_roster.items() if declaration.present
    }
    if set(role_policies) != present_roles:
        raise ProductionCaptureV5Error("production_policy_roster_membership_invalid")
    rows: list[Mapping[str, Any]] = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        declaration = view.plan.root_roster[role]
        if declaration.present:
            policy_spec = role_policies[role]
            try:
                _policy, _raw, actual_digest = capture._validate_policy_pin(policy_spec)
            except Exception as exc:
                raise ProductionCaptureV5Error("production_policy_file_pin_invalid") from exc
            rows.append(
                {
                    "role": role,
                    "present": True,
                    "scope": policy_spec.scope,
                    "policy_sha256": actual_digest,
                }
            )
        else:
            unresolved_reason = declaration.unresolved_reason
            if not isinstance(unresolved_reason, str) or not unresolved_reason:
                raise ProductionCaptureV5Error("production_absent_role_reason_invalid")
            rows.append(
                {
                    "role": role,
                    "present": False,
                    "disposition": "absent_unresolved",
                    "unresolved_reason_sha256": sha256(unresolved_reason.encode("utf-8")),
                    "scope": None,
                    "policy_sha256": None,
                }
            )
    try:
        digest = capture.policy_roster_sha256_v5(plan_sha256, rows)
    except Exception as exc:
        raise ProductionCaptureV5Error("production_policy_roster_invalid") from exc
    if not _valid_sha256(expected_policy_roster_sha256) or digest != expected_policy_roster_sha256:
        raise ProductionCaptureV5Error("production_policy_roster_pin_mismatch")
    return digest


def _assert_guard_pins(
    guard: StorageGuard | ReadOnlyStorageGuard,
    config: StorageConfig,
    expected_volume_uuid: str,
    expected_root_id: str,
) -> None:
    if (
        not isinstance(guard, (StorageGuard, ReadOnlyStorageGuard))
        or getattr(guard, "bootstrap", True)
        or guard.config != config
        or str(guard.volume_uuid).lower() != expected_volume_uuid
        or guard.root_id != expected_root_id
        or str(config.volume_uuid).lower() != expected_volume_uuid
        or config.root_id != expected_root_id
    ):
        raise ProductionCaptureV5Error("target_storage_binding_pin_mismatch")


def _assert_writer_source_separation(
    view: HeldDataT, writer: DescriptorWriter, source_device: int
) -> None:
    if (
        view.plan.data_volume_uuid.lower() == writer.storage_volume_uuid
        or source_device == writer.device
    ):
        raise ProductionCaptureV5Error("source_storage_not_distinct")
    for fd in view.root_fds.values():
        if os.fstat(fd).st_dev != source_device:
            raise ProductionCaptureV5Error("source_root_device_mismatch")


def _assert_writer_identity(
    guard: StorageGuard,
    writer: DescriptorWriter,
    required_free_bytes: int,
    source_device: int,
) -> None:
    try:
        resolved = guard.revalidate_identity(min_free_bytes=required_free_bytes)
        held = os.fstat(writer.root_fd)
        if (
            resolved.available_bytes < required_free_bytes
            or held.st_dev != writer.device
            or writer.device == source_device
        ):
            raise ProductionCaptureV5Error("target_capacity_or_source_binding_invalid")
    except ProductionCaptureV5Error:
        raise
    except Exception as exc:
        raise ProductionCaptureV5Error("target_storage_fast_revalidation_failed") from exc


def _full_boundary(
    view: HeldDataT,
    guard: StorageGuard,
    writer: DescriptorWriter,
    expected_volume_uuid: str,
    expected_root_id: str,
    source_device: int,
    required_reserve_bytes: int,
) -> None:
    required = PRODUCTION_FREE_SPACE_FLOOR_BYTES + required_reserve_bytes
    try:
        view.revalidate()
        _assert_guard_pins(guard, guard.config, expected_volume_uuid, expected_root_id)
        result = guard.revalidate(min_free_bytes=required)
        writer.assert_storage_binding(guard)
        _assert_writer_source_separation(view, writer, source_device)
        if result.available_bytes < required:
            raise ProductionCaptureV5Error("production_capacity_below_required_reserve")
    except ProductionCaptureV5Error:
        raise
    except Exception as exc:
        raise ProductionCaptureV5Error("production_boundary_revalidation_failed") from exc


def _required_boundary_callback(
    view: HeldDataT,
    guard: StorageGuard,
    writer: DescriptorWriter,
    expected_volume_uuid: str,
    expected_root_id: str,
    source_device: int,
) -> Callable[[int], None]:
    """Build the argument-bearing callback used around capture payload work."""

    def check(required_reserve_bytes: int) -> None:
        if type(required_reserve_bytes) is not int or required_reserve_bytes < 0:
            raise ProductionCaptureV5Error("production_reserve_value_invalid")
        _full_boundary(
            view,
            guard,
            writer,
            expected_volume_uuid,
            expected_root_id,
            source_device,
            required_reserve_bytes,
        )

    return check


class ProductionCaptureV5:
    """Bound production candidate session; its context owns the writer and spool."""

    def __init__(
        self,
        view: HeldDataT,
        guard: StorageGuard,
        writer: DescriptorWriter,
        inventory: capture.GlobalInventoryV5,
        *,
        expected_volume_uuid: str,
        expected_root_id: str,
        source_device: int,
    ) -> None:
        self.view = view
        self.guard = guard
        self.writer = writer
        self.inventory = inventory
        self.expected_volume_uuid = expected_volume_uuid
        self.expected_root_id = expected_root_id
        self.source_device = source_device
        self._published = False

    def capture_candidate(self) -> dict[str, Any]:
        if self._published:
            raise ProductionCaptureV5Error("production_candidate_already_published")
        required = (
            self.inventory.scratch_reserve_bytes
            + self.inventory.metadata_reserve_bytes
            + self.inventory.payload_bytes_remaining
            + self.inventory.payload_reserve_bytes
        )
        _full_boundary(
            self.view,
            self.guard,
            self.writer,
            self.expected_volume_uuid,
            self.expected_root_id,
            self.source_device,
            required,
        )
        receipt = capture.capture_candidate_v5(self.inventory)
        _full_boundary(
            self.view,
            self.guard,
            self.writer,
            self.expected_volume_uuid,
            self.expected_root_id,
            self.source_device,
            0,
        )
        self._published = True
        if receipt.get("synthetic_fixture_only") is not False:
            raise ProductionCaptureV5Error("production_candidate_source_mode_invalid")
        return receipt


@contextlib.contextmanager
def production_capture_v5(
    view: HeldDataT,
    storage_config_path: Path,
    *,
    expected_plan_sha256: str,
    expected_policy_roster_sha256: str,
    operation_spec_sha256: str | None = None,
    expected_target_volume_uuid: str,
    expected_target_root_id: str,
    role_policies: Mapping[str, capture.RoleCapturePolicy],
    scratch_reserve_bytes: int,
    payload_reserve_bytes: int = 0,
    metadata_reserve_bytes: int = capture.MAX_METADATA_TOTAL_BYTES,
    max_entries: int = capture.MAX_ENTRIES,
) -> Iterator[ProductionCaptureV5]:
    """Open a fully pinned real-source/target session, then yield candidate tools.

    This publishes only a candidate. It cannot mint T, COMPLETE, restore,
    durability, or erase claims.
    """
    target_uuid, target_root_id = _validate_pins(
        expected_target_volume_uuid, expected_target_root_id
    )
    source_device = _require_real_held_view(view, expected_plan_sha256)
    if not _valid_sha256(operation_spec_sha256):
        raise ProductionCaptureV5Error("production_operation_spec_pin_invalid")
    plan_sha256 = approved_data_plan_sha256(view.plan)
    if plan_sha256 != expected_plan_sha256:
        raise ProductionCaptureV5Error("production_external_plan_pin_mismatch")
    policy_roster_sha256 = _preflight_policy_roster(
        view,
        role_policies,
        plan_sha256=plan_sha256,
        expected_policy_roster_sha256=expected_policy_roster_sha256,
    )
    if view.plan.data_volume_uuid.lower() == target_uuid:
        raise ProductionCaptureV5Error("source_storage_uuid_not_distinct")
    config_path = _canonical_config_path(storage_config_path)
    try:
        config = StorageConfig.load(config_path)
    except Exception as exc:
        raise ProductionCaptureV5Error("canonical_storage_config_invalid") from exc
    if config.root_id != target_root_id or str(config.volume_uuid).lower() != target_uuid:
        raise ProductionCaptureV5Error("target_storage_config_pin_mismatch")

    writer: DescriptorWriter | None = None
    inventory: capture.GlobalInventoryV5 | None = None
    with storage.writer_guard(
        config_path,
        min_free_bytes=PRODUCTION_FREE_SPACE_FLOOR_BYTES,
    ) as guard:
        _assert_guard_pins(guard, config, target_uuid, target_root_id)
        try:
            guard.revalidate(min_free_bytes=PRODUCTION_FREE_SPACE_FLOOR_BYTES)
            writer = DescriptorWriter.from_storage_guard(guard)
            _assert_writer_source_separation(view, writer, source_device)

            def reserve_capacity(required_reserve_bytes: int) -> None:
                if type(required_reserve_bytes) is not int or required_reserve_bytes < 0:
                    raise ProductionCaptureV5Error("production_reserve_value_invalid")
                _assert_writer_identity(
                    guard,
                    writer,
                    PRODUCTION_FREE_SPACE_FLOOR_BYTES + required_reserve_bytes,
                    source_device,
                )

            def full_root_check() -> None:
                reserve = scratch_reserve_bytes + metadata_reserve_bytes + payload_reserve_bytes
                _full_boundary(
                    view,
                    guard,
                    writer,
                    target_uuid,
                    target_root_id,
                    source_device,
                    reserve,
                )

            boundary_check = _required_boundary_callback(
                view,
                guard,
                writer,
                target_uuid,
                target_root_id,
                source_device,
            )

            def fast_check(required_reserve_bytes: int) -> None:
                view.revalidate_mount_fast()
                _assert_writer_identity(
                    guard,
                    writer,
                    PRODUCTION_FREE_SPACE_FLOOR_BYTES + required_reserve_bytes,
                    source_device,
                )

            cadence_entries = 0
            cadence_bytes = 0
            last_full_check = time.monotonic()

            def cadence_check(
                required_reserve_bytes: int,
                entries_delta: int,
                bytes_delta: int,
            ) -> None:
                nonlocal cadence_entries, cadence_bytes, last_full_check
                if (
                    type(required_reserve_bytes) is not int
                    or required_reserve_bytes < 0
                    or type(entries_delta) is not int
                    or entries_delta < 0
                    or type(bytes_delta) is not int
                    or bytes_delta < 0
                ):
                    raise ProductionCaptureV5Error("production_cadence_value_invalid")
                fast_check(required_reserve_bytes)
                cadence_entries += entries_delta
                cadence_bytes += bytes_delta
                now = time.monotonic()
                if (
                    cadence_entries >= 10_000
                    or cadence_bytes >= 256 * 1024**2
                    or now - last_full_check >= 60
                ):
                    _full_boundary(
                        view,
                        guard,
                        writer,
                        target_uuid,
                        target_root_id,
                        source_device,
                        required_reserve_bytes,
                    )
                    cadence_entries = 0
                    cadence_bytes = 0
                    last_full_check = time.monotonic()

            _full_boundary(
                view,
                guard,
                writer,
                target_uuid,
                target_root_id,
                source_device,
                scratch_reserve_bytes + metadata_reserve_bytes + payload_reserve_bytes,
            )
            inventory = capture._inventory_held_data_v5_core(
                view,
                writer,
                role_policies=role_policies,
                reserve_capacity=reserve_capacity,
                scratch_reserve_bytes=scratch_reserve_bytes,
                payload_reserve_bytes=payload_reserve_bytes,
                metadata_reserve_bytes=metadata_reserve_bytes,
                max_entries=max_entries,
                expected_plan_sha256=expected_plan_sha256,
                expected_policy_roster_sha256=policy_roster_sha256,
                operation_spec_sha256=operation_spec_sha256,
                production_authority=capture._PRODUCTION_CAPTURE_AUTHORITY,
                production_root_check=full_root_check,
                production_fast_check=fast_check,
                production_boundary_check=boundary_check,
                production_cadence_check=cadence_check,
            )
            _assert_writer_source_separation(view, writer, source_device)
            yield ProductionCaptureV5(
                view,
                guard,
                writer,
                inventory,
                expected_volume_uuid=target_uuid,
                expected_root_id=target_root_id,
                source_device=source_device,
            )
        finally:
            if inventory is not None:
                inventory.close()
            if writer is not None:
                writer.close()


def verify_production_candidate_v5(
    writer: DescriptorWriter,
    candidate_id: str,
    *,
    expected_target_volume_uuid: str,
    expected_target_root_id: str,
    scratch: PinnedScratchV5,
    expected_index_sha256: str,
    expected_plan_sha256: str,
    expected_operation_spec_sha256: str | None = None,
    source_view: HeldDataT | None = None,
    expected_source_data_volume_uuid: str | None = None,
    expected_shared_apfs_container_uuid: str | None = None,
    shared_pool_headroom_bytes: int = 0,
) -> dict[str, Any]:
    """Verify with an optional held source view; absent source is explicitly un-attested."""
    target_uuid, target_root_id = _validate_pins(
        expected_target_volume_uuid, expected_target_root_id
    )
    guard = writer.storage_guard
    if not isinstance(guard, ReadOnlyStorageGuard) or not writer._read_only:
        raise ProductionCaptureV5Error("production_verify_read_only_candidate_guard_required")
    _assert_guard_pins(guard, guard.config, target_uuid, target_root_id)
    if not _valid_sha256(expected_plan_sha256):
        raise ProductionCaptureV5Error("production_verify_plan_pin_invalid")
    source_device: int | None = None
    if source_view is not None:
        source_device = _require_real_held_view(source_view, expected_plan_sha256)
        if source_view.plan.data_volume_uuid.lower() == target_uuid:
            raise ProductionCaptureV5Error("source_storage_uuid_not_distinct")
    if not isinstance(scratch, PinnedScratchV5) or scratch.fd < 0:
        raise ProductionCaptureV5Error("production_verify_scratch_capability_invalid")
    scratch_is_apfs = scratch.filesystem_identity[0] == "apfs"
    if scratch_is_apfs and (
        scratch.apfs_container_reference is None or scratch.apfs_container_uuid is None
    ):
        raise ProductionCaptureV5Error("scratch_volume_uuid_unresolved")
    if type(shared_pool_headroom_bytes) is not int or shared_pool_headroom_bytes < 0:
        raise ProductionCaptureV5Error("production_verify_shared_pool_pin_invalid")
    shared_pool_mode = expected_shared_apfs_container_uuid is not None
    scratch_container_uuid = scratch.apfs_container_uuid
    target_container_uuid = None
    if scratch_is_apfs or shared_pool_mode:
        try:
            target_fs = DarwinDataOS().filesystem(guard.mount_fd)
            target_fs_type = target_fs.get("fs_type")
            if target_fs_type == "apfs":
                target_info = DarwinDataOS().disk_info(str(guard.config.mount_path))
                target_container_reference = target_info.get("APFSContainerReference")
                if not isinstance(target_container_reference, str):
                    raise ProductionCaptureV5Error("production_verify_shared_pool_identity_invalid")
                target_container_uuid = _apfs_container_uuid(target_container_reference)
            elif shared_pool_mode:
                raise ProductionCaptureV5Error("production_verify_shared_pool_identity_invalid")
        except Exception as exc:
            if isinstance(exc, ProductionCaptureV5Error):
                raise
            raise ProductionCaptureV5Error(
                "production_verify_shared_pool_identity_invalid"
            ) from exc
    if (
        not shared_pool_mode
        and target_container_uuid is not None
        and scratch_container_uuid == target_container_uuid
    ):
        raise ProductionCaptureV5Error("production_verify_shared_pool_pin_invalid")
    if shared_pool_mode:
        if (
            not isinstance(expected_shared_apfs_container_uuid, str)
            or _UUID.fullmatch(expected_shared_apfs_container_uuid.lower()) is None
            or not _valid_sha256(expected_operation_spec_sha256)
            or scratch.apfs_container_uuid != expected_shared_apfs_container_uuid.lower()
        ):
            raise ProductionCaptureV5Error("production_verify_shared_pool_pin_invalid")
        if target_container_uuid != expected_shared_apfs_container_uuid.lower():
            raise ProductionCaptureV5Error("production_verify_shared_pool_identity_invalid")
        if source_view is not None:
            source_container_uuid = _apfs_container_uuid(source_view.plan.data_container_reference)
            if source_container_uuid == expected_shared_apfs_container_uuid.lower():
                raise ProductionCaptureV5Error("production_verify_shared_pool_source_alias")
        shared_pool_floor = VERIFY_SHARED_POOL_FLOOR_BYTES + shared_pool_headroom_bytes
    else:
        if shared_pool_headroom_bytes != 0:
            raise ProductionCaptureV5Error("production_verify_shared_pool_pin_invalid")
        shared_pool_floor = 0
    _assert_candidate_target_read_only(guard)
    writer.assert_storage_binding(guard)
    if scratch.filesystem_identity[0] in {"apfs", "hfs"}:
        if scratch.volume_uuid is None or scratch.volume_uuid == target_uuid:
            raise ProductionCaptureV5Error("verify_scratch_target_volume_alias")
        if (
            source_view is not None
            and scratch.volume_uuid == source_view.plan.data_volume_uuid.lower()
        ):
            raise ProductionCaptureV5Error("verify_scratch_source_volume_alias")
    if source_device is None:
        # Independent verification currently accepts separately pinned APFS/HFS
        # scratch only. A RecoveryOS tmpfs capability needs platform qualification.
        if (
            not isinstance(expected_source_data_volume_uuid, str)
            or not _UUID.fullmatch(expected_source_data_volume_uuid.lower())
            or scratch.volume_uuid is None
            or scratch.volume_uuid == expected_source_data_volume_uuid.lower()
        ):
            raise ProductionCaptureV5Error("independent_verify_scratch_volume_uuid_pin_required")
        scratch.validate(
            source_device=None,
            target_device=writer.device,
            required_free_bytes=VERIFY_SCRATCH_FREE_FLOOR_BYTES,
        )
        source_status = "source_not_present_or_not_attested"
    else:
        scratch.validate(
            source_device=source_device,
            target_device=writer.device,
            required_free_bytes=VERIFY_SCRATCH_FREE_FLOOR_BYTES,
        )
        source_status = "held_source_view"

    def check_scratch_capacity(used_bytes: int, reserve_bytes: int) -> None:
        if (
            type(used_bytes) is not int
            or type(reserve_bytes) is not int
            or not 0 <= used_bytes <= reserve_bytes <= VERIFY_SCRATCH_DB_RESERVE_BYTES
        ):
            raise ProductionCaptureV5Error("production_verify_scratch_usage_invalid")
        required_free = _verify_scratch_required_free_bytes(
            used_bytes,
            reserve_bytes,
            shared_pool_mode=shared_pool_mode,
            shared_pool_headroom_bytes=shared_pool_headroom_bytes,
        )
        if source_device is None:
            scratch.validate(
                source_device=None,
                target_device=writer.device,
                required_free_bytes=required_free,
            )
        else:
            scratch.validate(
                source_device=source_device,
                target_device=writer.device,
                required_free_bytes=required_free,
            )
        if shared_pool_mode:
            try:
                guard.revalidate()
                _assert_candidate_target_read_only(guard)
                current_target_info = DarwinDataOS().disk_info(str(guard.config.mount_path))
                current_target_reference = current_target_info.get("APFSContainerReference")
                current_target_container_uuid = (
                    _apfs_container_uuid(current_target_reference)
                    if isinstance(current_target_reference, str)
                    else None
                )
            except ProductionCaptureV5Error:
                raise
            except Exception as exc:
                raise ProductionCaptureV5Error(
                    "production_verify_shared_pool_identity_invalid"
                ) from exc
            if current_target_container_uuid != expected_shared_apfs_container_uuid.lower():
                raise ProductionCaptureV5Error("production_verify_shared_pool_identity_invalid")
            pool_vfs = os.fstatvfs(guard.mount_fd)
            pool_free = int(pool_vfs.f_bavail * pool_vfs.f_frsize)
            if pool_free < required_free:
                raise ProductionCaptureV5Error("production_verify_shared_pool_reserve_invalid")

    if shared_pool_mode:
        check_scratch_capacity(0, VERIFY_SCRATCH_DB_RESERVE_BYTES)
    try:
        result = capture.verify_candidate_v5(
            writer,
            candidate_id,
            expected_index_sha256=expected_index_sha256,
            scratch_parent_fd=scratch.fd,
            scratch_db_limit_bytes=VERIFY_SCRATCH_DB_RESERVE_BYTES,
            scratch_capacity_check=check_scratch_capacity,
            expected_plan_sha256=expected_plan_sha256,
            expected_operation_spec_sha256=expected_operation_spec_sha256,
        )
    except capture.TreeCaptureV5Error as exc:
        if str(exc) == "candidate_operation_spec_pin_mismatch":
            raise ProductionCaptureV5Error("production_verify_operation_spec_mismatch") from None
        raise
    if result.get("synthetic_fixture_only") is not False:
        raise ProductionCaptureV5Error("production_candidate_source_mode_invalid")
    check_scratch_capacity(0, 0)
    _assert_candidate_target_read_only(guard)
    guard.revalidate()
    writer.assert_storage_binding(guard)
    if source_device is not None:
        scratch.validate(
            source_device=source_device,
            target_device=writer.device,
            required_free_bytes=VERIFY_SCRATCH_FREE_FLOOR_BYTES,
        )
    else:
        _validate_scratch_target_separation(os.fstat(scratch.fd).st_dev, writer.device)
        scratch.validate(
            source_device=None,
            target_device=writer.device,
            required_free_bytes=VERIFY_SCRATCH_FREE_FLOOR_BYTES,
        )
    result["shared_pool_container_uuid"] = (
        expected_shared_apfs_container_uuid if shared_pool_mode else None
    )
    result["shared_pool_policy_floor_bytes"] = shared_pool_floor if shared_pool_mode else 0
    result["shared_pool_headroom_bytes"] = shared_pool_headroom_bytes
    result["source_status"] = source_status
    if source_view is not None:
        source_view.revalidate()
    return result


def capture_session_verify(
    view: HeldDataT,
    writer: DescriptorWriter,
    candidate_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Verify during capture while deriving source device from the held view."""
    return verify_production_candidate_v5(writer, candidate_id, source_view=view, **kwargs)


def independent_target_verify(
    writer: DescriptorWriter,
    candidate_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Verify an existing target with no source handle and no caller device hint."""
    return verify_production_candidate_v5(writer, candidate_id, source_view=None, **kwargs)


def extract_production_candidate_inert_v5(
    candidate_id: str,
    *,
    target_storage_config: Path,
    expected_target_volume_uuid: str,
    expected_target_root_id: str,
    destination_storage_config: Path,
    expected_destination_volume_uuid: str,
    expected_destination_root_id: str,
    scratch: PinnedScratchV5,
    expected_index_sha256: str,
    expected_plan_sha256: str,
    expected_source_data_volume_uuid: str,
    source_plan_path: Path,
) -> dict[str, Any]:
    """Run a pinned inert quarantine rehearsal; this never restores or starts data."""
    target_uuid, target_root_id = _validate_pins(
        expected_target_volume_uuid, expected_target_root_id
    )
    destination_uuid, destination_root_id = _validate_pins(
        expected_destination_volume_uuid, expected_destination_root_id
    )
    if (
        not _valid_sha256(expected_index_sha256)
        or not _valid_sha256(expected_plan_sha256)
        or not isinstance(expected_source_data_volume_uuid, str)
        or not _UUID.fullmatch(expected_source_data_volume_uuid.lower())
    ):
        raise ProductionCaptureV5Error("production_extract_external_pin_invalid")
    source_uuid = expected_source_data_volume_uuid.lower()
    if len(candidate_id) != 32 or any(ch not in "0123456789abcdef" for ch in candidate_id):
        raise ProductionCaptureV5Error("production_verify_cli_pin_invalid")
    if len({target_uuid, destination_uuid, source_uuid}) != 3:
        raise ProductionCaptureV5Error("production_extract_volume_uuid_alias")
    try:
        source_plan = _load_plan(_canonical_config_path(source_plan_path))
    except Exception as exc:
        raise ProductionCaptureV5Error("production_extract_source_plan_invalid") from exc
    if (
        approved_data_plan_sha256(source_plan) != expected_plan_sha256
        or source_plan.data_volume_uuid.lower() != source_uuid
    ):
        raise ProductionCaptureV5Error("production_extract_source_plan_pin_mismatch")
    if not isinstance(scratch, PinnedScratchV5) or scratch.fd < 0:
        raise ProductionCaptureV5Error("production_verify_scratch_capability_invalid")

    target_path = _canonical_config_path(target_storage_config)
    destination_path = _canonical_config_path(destination_storage_config)
    try:
        target_config = StorageConfig.load(target_path)
        destination_config = StorageConfig.load(destination_path)
    except Exception as exc:
        raise ProductionCaptureV5Error("production_extract_storage_config_invalid") from exc
    if (
        target_config.root_id != target_root_id
        or str(target_config.volume_uuid).lower() != target_uuid
        or destination_config.root_id != destination_root_id
        or str(destination_config.volume_uuid).lower() != destination_uuid
    ):
        raise ProductionCaptureV5Error("production_extract_storage_config_pin_mismatch")

    with storage.read_only_guard(target_path) as target_guard:
        _assert_guard_pins(target_guard, target_config, target_uuid, target_root_id)
        reader = DescriptorWriter.read_only_from_storage_guard(target_guard)
        try:
            if not isinstance(reader.storage_guard, ReadOnlyStorageGuard) or not reader._read_only:
                raise ProductionCaptureV5Error(
                    "production_verify_read_only_candidate_guard_required"
                )
            target_guard.revalidate()
            reader.assert_storage_binding(target_guard)
            target_device = reader.device
            os.fstat(reader.root_fd)
            if sys.platform == "darwin" and capture._acl_bytes(reader.root_fd) is not None:
                raise ProductionCaptureV5Error("production_extract_target_acl_present")
            if os.fstatvfs(target_guard.mount_fd).f_flag & getattr(os, "ST_RDONLY", 1) == 0:
                raise ProductionCaptureV5Error("production_extract_target_not_read_only")
            if scratch.filesystem_identity[0] not in {"apfs", "hfs"}:
                raise ProductionCaptureV5Error("production_extract_scratch_filesystem_invalid")
            if scratch.volume_uuid in {None, target_uuid, destination_uuid, source_uuid}:
                raise ProductionCaptureV5Error("production_extract_scratch_volume_alias")
            if scratch.filesystem_identity[0] in {"apfs", "hfs"} and scratch.volume_uuid is None:
                raise ProductionCaptureV5Error("production_extract_scratch_volume_unpinned")

            with storage.writer_guard(
                destination_path,
                min_free_bytes=INERT_QUARANTINE_FREE_FLOOR_BYTES,
            ) as destination_guard:
                _assert_guard_pins(
                    destination_guard,
                    destination_config,
                    destination_uuid,
                    destination_root_id,
                )
                destination_writer = DescriptorWriter.from_storage_guard(destination_guard)
                try:
                    destination_writer.assert_storage_binding(destination_guard)
                    destination_root = os.fstat(destination_writer.root_fd)
                    if (
                        destination_writer.device == target_device
                        or destination_writer.device == os.fstat(scratch.fd).st_dev
                        or destination_uuid in {target_uuid, source_uuid}
                        or destination_root.st_uid != os.getuid()
                        or stat.S_IMODE(destination_root.st_mode) != 0o700
                        or not stat.S_ISDIR(destination_root.st_mode)
                    ):
                        raise ProductionCaptureV5Error(
                            "production_extract_destination_identity_invalid"
                        )
                    if (
                        sys.platform == "darwin"
                        and capture._acl_bytes(destination_writer.root_fd) is not None
                    ):
                        raise ProductionCaptureV5Error("production_extract_destination_acl_present")
                    # If the externally pinned Data volume is mounted in this
                    # session, resolve its current device from kernel mount
                    # entries and diskutil, then reject any destination alias.
                    for mount in DarwinDataOS().mount_entries():
                        if mount.get("fs_type") not in {"apfs", "hfs"}:
                            continue
                        mountpoint = mount.get("mountpoint")
                        if not isinstance(mountpoint, str) or not Path(mountpoint).is_absolute():
                            raise ProductionCaptureV5Error(
                                "production_extract_mount_identity_invalid"
                            )
                        info = DarwinDataOS().disk_info(mountpoint)
                        mounted_uuid = str(
                            info.get("VolumeUUID", info.get("APFSVolumeUUID", ""))
                        ).lower()
                        if mounted_uuid == source_uuid:
                            source_mount_fd = -1
                            try:
                                source_mount_fd, _ = _open_absolute_directory_nofollow(
                                    Path(mountpoint)
                                )
                                source_mount_stat = os.fstat(source_mount_fd)
                                current_mount_stat = os.stat(mountpoint, follow_symlinks=False)
                            except Exception as exc:
                                raise ProductionCaptureV5Error(
                                    "production_extract_source_mount_unresolved"
                                ) from exc
                            finally:
                                if source_mount_fd >= 0:
                                    os.close(source_mount_fd)
                            if (source_mount_stat.st_dev, source_mount_stat.st_ino) != (
                                current_mount_stat.st_dev,
                                current_mount_stat.st_ino,
                            ) or source_mount_stat.st_dev in {
                                target_device,
                                destination_writer.device,
                            }:
                                raise ProductionCaptureV5Error(
                                    "production_extract_source_storage_device_alias"
                                )

                    scratch.validate(
                        source_device=None,
                        target_device=target_device,
                        required_free_bytes=INERT_QUARANTINE_SCRATCH_FLOOR_BYTES,
                    )
                    if os.fstat(scratch.fd).st_dev == destination_writer.device:
                        raise ProductionCaptureV5Error("production_extract_scratch_device_alias")

                    # Prove the destination reserve floor before the first
                    # candidate INDEX read; the verified size model is checked
                    # again by the core before creating the quarantine child.
                    def capacity_check(required_bytes: int) -> None:
                        if type(required_bytes) is not int or required_bytes < 0:
                            raise ProductionCaptureV5Error(
                                "production_extract_capacity_request_invalid"
                            )
                        result = destination_guard.revalidate_identity(
                            min_free_bytes=required_bytes
                        )
                        destination_writer.assert_storage_binding(destination_guard)
                        if result.available_bytes < required_bytes:
                            raise ProductionCaptureV5Error(
                                "production_extract_capacity_below_reserve"
                            )

                    capacity_check(INERT_QUARANTINE_FREE_FLOOR_BYTES)

                    def scratch_check(used: int, reserve: int) -> None:
                        if (
                            type(used) is not int
                            or type(reserve) is not int
                            or not 0 <= used <= reserve <= capture.MAX_SCRATCH_BYTES
                        ):
                            raise ProductionCaptureV5Error(
                                "production_extract_scratch_usage_invalid"
                            )
                        scratch.validate(
                            source_device=None,
                            target_device=target_device,
                            required_free_bytes=INERT_QUARANTINE_SCRATCH_FLOOR_BYTES
                            + max(0, reserve - used),
                        )

                    result = capture.extract_candidate_inert_v5(
                        reader,
                        candidate_id,
                        quarantine_parent_fd=destination_writer.root_fd,
                        scratch_parent_fd=scratch.fd,
                        expected_index_sha256=expected_index_sha256,
                        expected_plan_sha256=expected_plan_sha256,
                        capacity_check=capacity_check,
                        scratch_capacity_check=scratch_check,
                        leave_partial_on_error=True,
                    )
                    destination_guard.revalidate()
                    destination_writer.assert_storage_binding(destination_guard)
                    target_guard.revalidate()
                    reader.assert_storage_binding(target_guard)
                    scratch.validate(
                        source_device=None,
                        target_device=target_device,
                        required_free_bytes=INERT_QUARANTINE_SCRATCH_FLOOR_BYTES,
                    )
                    result.update(
                        {
                            "production_storage_pins_checked": True,
                            "candidate_volume_uuid": target_uuid,
                            "quarantine_volume_uuid": destination_uuid,
                            "source_data_volume_uuid": source_uuid,
                            "source_status": "source_not_present_or_not_attested",
                            "quarantine_rehearsal_only": True,
                            "restore_verified": False,
                            "T_qualified": False,
                            "erase_ready": False,
                            "sqlite_scratch_role": "private_readback_index_only",
                        }
                    )
                    return result
                finally:
                    destination_writer.close()
        finally:
            reader.close()


def _capture_from_pinned_spec(
    *,
    spec_path: Path,
    expected_spec_sha256: str,
    plan_path: Path,
    expected_plan_sha256: str,
    expected_policy_roster_sha256: str,
) -> dict[str, Any]:
    spec = _read_private_capture_spec(spec_path, expected_spec_sha256)
    _capture_spec_fields(spec)
    plan_digest = _require_sha256(expected_plan_sha256, "capture_plan_sha256_invalid")
    policy_digest = _require_sha256(
        expected_policy_roster_sha256, "capture_policy_roster_sha256_invalid"
    )
    try:
        canonical_plan_path = _canonical_config_path(plan_path)
        plan = _load_plan(canonical_plan_path)
    except ProductionCaptureV5CLIError:
        raise
    except Exception as exc:
        raise ProductionCaptureV5CLIError("capture_plan_invalid") from exc
    actual_plan_digest = approved_data_plan_sha256(plan)
    if actual_plan_digest != plan_digest:
        raise ProductionCaptureV5CLIError("capture_plan_pin_mismatch")
    if set(plan.root_roster) != REQUIRED_ROOT_ROLES:
        raise ProductionCaptureV5CLIError("capture_plan_root_roster_invalid")

    source_mountpoint = _require_canonical_directory(
        spec.get("source_mountpoint"), "capture_source_mountpoint_invalid"
    )
    try:
        storage_config_path = _canonical_config_path(Path(spec["storage_config_path"]))
    except Exception as exc:
        raise ProductionCaptureV5CLIError("capture_storage_config_path_invalid") from exc
    target_uuid_value = spec.get("target_volume_uuid")
    target_root_id_value = spec.get("target_root_id")
    if not isinstance(target_uuid_value, str) or not isinstance(target_root_id_value, str):
        raise ProductionCaptureV5CLIError("capture_target_storage_pin_invalid")
    try:
        target_uuid, target_root_id = _validate_pins(target_uuid_value, target_root_id_value)
    except ProductionCaptureV5Error as exc:
        raise ProductionCaptureV5CLIError("capture_target_storage_pin_invalid") from exc
    if plan.data_volume_uuid.lower() == target_uuid:
        raise ProductionCaptureV5CLIError("capture_source_target_volume_alias")
    scratch_reserve = _positive_bounded_int(
        spec.get("scratch_reserve_bytes"),
        minimum=1,
        maximum=capture.MAX_SCRATCH_BYTES,
        error="capture_scratch_reserve_invalid",
    )
    metadata_reserve = _positive_bounded_int(
        spec.get("metadata_reserve_bytes"),
        minimum=0,
        maximum=capture.MAX_METADATA_TOTAL_BYTES,
        error="capture_metadata_reserve_invalid",
    )
    payload_reserve = _positive_bounded_int(
        spec.get("payload_reserve_bytes"),
        minimum=0,
        maximum=capture.MAX_SOURCE_BYTES,
        error="capture_payload_reserve_invalid",
    )
    role_policies, actual_policy_digest = _load_capture_policies(spec, plan, plan_digest)
    if actual_policy_digest != policy_digest:
        raise ProductionCaptureV5CLIError("capture_policy_roster_pin_mismatch")

    partial_candidate_possible = False
    try:
        with HeldDataT(
            plan,
            source_mountpoint,
            trusted_plan_sha256=plan_digest,
        ) as view:
            with production_capture_v5(
                view,
                storage_config_path,
                expected_plan_sha256=plan_digest,
                expected_policy_roster_sha256=policy_digest,
                operation_spec_sha256=expected_spec_sha256,
                expected_target_volume_uuid=target_uuid,
                expected_target_root_id=target_root_id,
                role_policies=role_policies,
                scratch_reserve_bytes=scratch_reserve,
                metadata_reserve_bytes=metadata_reserve,
                payload_reserve_bytes=payload_reserve,
            ) as session:
                partial_candidate_possible = True
                receipt = session.capture_candidate()
                result = _capture_summary(
                    receipt,
                    operation_spec_sha256=expected_spec_sha256,
                    plan_sha256=plan_digest,
                    policy_roster_sha256=policy_digest,
                )
        partial_candidate_possible = False
        return result
    except Exception:
        code = "capture_failed"
        raise ProductionCaptureV5CLIError(
            f"{code}|partial={int(partial_candidate_possible)}"
        ) from None


def _capacity_plan_from_pinned_spec(
    *,
    spec_path: Path,
    expected_spec_sha256: str,
    plan_path: Path,
    expected_plan_sha256: str,
    expected_policy_roster_sha256: str,
    scratch_parent: Path,
) -> dict[str, Any]:
    """Run metadata-only sizing with Kingston held read-only and never written."""
    spec = _read_private_capture_spec(spec_path, expected_spec_sha256)
    _capture_spec_fields(spec)
    plan_digest = _require_sha256(expected_plan_sha256, "capture_plan_sha256_invalid")
    policy_digest = _require_sha256(
        expected_policy_roster_sha256, "capture_policy_roster_sha256_invalid"
    )
    try:
        plan = _load_plan(_canonical_config_path(plan_path))
    except Exception as exc:
        raise ProductionCaptureV5CLIError("capture_plan_invalid") from exc
    if (
        approved_data_plan_sha256(plan) != plan_digest
        or set(plan.root_roster) != REQUIRED_ROOT_ROLES
    ):
        raise ProductionCaptureV5CLIError("capture_plan_pin_mismatch")
    source_mountpoint = _require_canonical_directory(
        spec.get("source_mountpoint"), "capture_source_mountpoint_invalid"
    )
    config_path = _canonical_config_path(Path(spec["storage_config_path"]))
    try:
        target_uuid, target_root_id = _validate_pins(
            spec["target_volume_uuid"], spec["target_root_id"]
        )
    except Exception as exc:
        raise ProductionCaptureV5CLIError("capture_target_storage_pin_invalid") from exc
    if plan.data_volume_uuid.lower() == target_uuid:
        raise ProductionCaptureV5CLIError("capture_source_target_volume_alias")
    try:
        policies, actual_policy_digest = _load_capture_policies(spec, plan, plan_digest)
    except Exception as exc:
        raise ProductionCaptureV5CLIError("capture_policy_roster_pin_mismatch") from exc
    if actual_policy_digest != policy_digest:
        raise ProductionCaptureV5CLIError("capture_policy_roster_pin_mismatch")
    scratch_path = _require_canonical_directory(scratch_parent, "scratch_path_not_canonical")
    result: dict[str, Any]
    with storage.read_only_guard(config_path) as target_guard:
        _assert_guard_pins(target_guard, target_guard.config, target_uuid, target_root_id)
        _assert_candidate_target_read_only(target_guard)
        target_device = os.fstat(target_guard.root_fd).st_dev
        target_disk = DarwinDataOS().disk_info(str(target_guard.mountpoint))
        target_container_ref = target_disk.get("APFSContainerReference")
        if not isinstance(target_container_ref, str) or not re.fullmatch(
            r"disk[0-9]+", target_container_ref
        ):
            raise ProductionCaptureV5Error("capacity_plan_target_container_unresolved")
        target_container_uuid = _apfs_container_uuid(target_container_ref)
        scratch = PinnedScratchV5.open(scratch_path)
        try:
            source_container_uuid = _apfs_container_uuid(plan.data_container_reference)
            if (
                scratch.volume_uuid is None
                or scratch.volume_uuid == target_uuid
                or (
                    scratch.apfs_container_uuid is not None
                    and scratch.apfs_container_uuid
                    in {source_container_uuid, target_container_uuid}
                )
            ):
                raise ProductionCaptureV5Error("capacity_plan_scratch_not_external")
            with HeldDataT(
                plan,
                source_mountpoint,
                trusted_plan_sha256=plan_digest,
            ) as view:
                scratch.validate(
                    source_device=os.fstat(view.mount_fd).st_dev,
                    target_device=target_device,
                    required_free_bytes=(
                        CAPACITY_PLANNER_DB_LIMIT_BYTES + CAPACITY_PLANNER_FREE_FLOOR_BYTES
                    ),
                )

                def external_boundary_check(used_db_bytes: int) -> None:
                    if (
                        type(used_db_bytes) is not int
                        or not 0 <= used_db_bytes <= CAPACITY_PLANNER_DB_LIMIT_BYTES
                    ):
                        raise ProductionCaptureV5Error("capacity_plan_scratch_usage_invalid")
                    view.revalidate()
                    target_guard.revalidate_identity()
                    _assert_candidate_target_read_only(target_guard)
                    scratch.validate(
                        source_device=os.fstat(view.mount_fd).st_dev,
                        target_device=target_device,
                        required_free_bytes=(
                            CAPACITY_PLANNER_DB_LIMIT_BYTES
                            - used_db_bytes
                            + CAPACITY_PLANNER_FREE_FLOOR_BYTES
                        ),
                    )

                result = capture._plan_held_data_capacity_v5_core(
                    view,
                    role_policies=policies,
                    scratch_parent_fd=scratch.fd,
                    scratch_device=os.fstat(scratch.fd).st_dev,
                    target_device=target_device,
                    expected_plan_sha256=plan_digest,
                    expected_policy_roster_sha256=policy_digest,
                    scratch_db_limit_bytes=CAPACITY_PLANNER_DB_LIMIT_BYTES,
                    external_boundary_check=external_boundary_check,
                    production_authority=capture._CAPACITY_PLAN_PRODUCTION_AUTHORITY,
                )
                target_guard.revalidate_identity()
                _assert_candidate_target_read_only(target_guard)
        finally:
            scratch.close()
    result.update(
        {
            "operation_spec_sha256": expected_spec_sha256,
            "scratch_volume_uuid": scratch.volume_uuid,
            "scratch_db_limit_bytes": CAPACITY_PLANNER_DB_LIMIT_BYTES,
            "kingston_read_only": True,
            "kingston_write_performed": False,
        }
    )
    return result


def main(argv: list[str] | None = None) -> int:
    """Run capture, candidate verification, or inert quarantine rehearsal."""
    parser = _OperatorArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    operator_capture = subparsers.add_parser(
        "capture",
        help="capture an already-mounted read-only Data view into a pinned target",
    )
    operator_capture.add_argument("--spec", required=True, type=Path)
    operator_capture.add_argument("--spec-sha256", required=True)
    operator_capture.add_argument("--plan", required=True, type=Path)
    operator_capture.add_argument("--plan-sha256", required=True)
    operator_capture.add_argument("--policy-roster-sha256", required=True)
    capacity_plan = subparsers.add_parser(
        "plan-capacity",
        help="size a pinned read-only Data view using separate external scratch",
    )
    capacity_plan.add_argument("--spec", required=True, type=Path)
    capacity_plan.add_argument("--spec-sha256", required=True)
    capacity_plan.add_argument("--plan", required=True, type=Path)
    capacity_plan.add_argument("--plan-sha256", required=True)
    capacity_plan.add_argument("--policy-roster-sha256", required=True)
    capacity_plan.add_argument("--scratch-parent", required=True, type=Path)
    verify = subparsers.add_parser("verify", help="verify one existing candidate read-only")
    verify.add_argument("candidate_id")
    verify.add_argument("--index-sha256", required=True)
    verify.add_argument("--storage-config", required=True, type=Path)
    verify.add_argument("--target-volume-uuid", required=True)
    verify.add_argument("--target-root-id", required=True)
    verify.add_argument("--plan-sha256", required=True)
    verify.add_argument("--source-data-volume-uuid")
    verify.add_argument("--scratch-parent", required=True, type=Path)
    verify.add_argument("--shared-apfs-container-uuid")
    verify.add_argument("--operation-spec", type=Path)
    verify.add_argument("--operation-spec-sha256")
    extract = subparsers.add_parser(
        "extract-inert",
        help="copy candidate bytes into a pinned inert quarantine rehearsal",
    )
    extract.add_argument("candidate_id")
    extract.add_argument("--index-sha256", required=True)
    extract.add_argument("--plan-sha256", required=True)
    extract.add_argument("--source-data-volume-uuid", required=True)
    extract.add_argument("--source-plan", required=True, type=Path)
    extract.add_argument("--target-storage-config", required=True, type=Path)
    extract.add_argument("--target-volume-uuid", required=True)
    extract.add_argument("--target-root-id", required=True)
    extract.add_argument("--destination-storage-config", required=True, type=Path)
    extract.add_argument("--destination-volume-uuid", required=True)
    extract.add_argument("--destination-root-id", required=True)
    extract.add_argument("--scratch-parent", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
    except ProductionCaptureV5CLIError as exc:
        command_args = sys.argv[1:] if argv is None else argv
        if command_args and command_args[0] == "extract-inert":
            sys.stdout.buffer.write(canonical_json(_extract_failure(exc)))
            return 2
        sys.stdout.buffer.write(
            canonical_json(_capture_failure(str(exc), partial_candidate_possible=False))
        )
        return 2
    if args.command == "extract-inert":
        try:
            scratch = PinnedScratchV5.open(args.scratch_parent)
            try:
                result = extract_production_candidate_inert_v5(
                    args.candidate_id,
                    target_storage_config=args.target_storage_config,
                    expected_target_volume_uuid=args.target_volume_uuid,
                    expected_target_root_id=args.target_root_id,
                    destination_storage_config=args.destination_storage_config,
                    expected_destination_volume_uuid=args.destination_volume_uuid,
                    expected_destination_root_id=args.destination_root_id,
                    scratch=scratch,
                    expected_index_sha256=args.index_sha256,
                    expected_plan_sha256=args.plan_sha256,
                    expected_source_data_volume_uuid=args.source_data_volume_uuid,
                    source_plan_path=args.source_plan,
                )
            finally:
                scratch.close()
            sys.stdout.buffer.write(canonical_json(result))
            return 0
        except Exception as exc:
            sys.stdout.buffer.write(canonical_json(_extract_failure(exc)))
            return 2
    if args.command == "capture":
        try:
            result = _capture_from_pinned_spec(
                spec_path=args.spec,
                expected_spec_sha256=args.spec_sha256,
                plan_path=args.plan,
                expected_plan_sha256=args.plan_sha256,
                expected_policy_roster_sha256=args.policy_roster_sha256,
            )
            sys.stdout.buffer.write(canonical_json(result))
            return 0
        except ProductionCaptureV5CLIError as exc:
            message = str(exc)
            code, _, partial = message.partition("|partial=")
            sys.stdout.buffer.write(
                canonical_json(_capture_failure(code, partial_candidate_possible=partial == "1"))
            )
            return 2
        except Exception:
            sys.stdout.buffer.write(
                canonical_json(_capture_failure("capture_failed", partial_candidate_possible=False))
            )
            return 2
    if args.command == "plan-capacity":
        try:
            result = _capacity_plan_from_pinned_spec(
                spec_path=args.spec,
                expected_spec_sha256=args.spec_sha256,
                plan_path=args.plan,
                expected_plan_sha256=args.plan_sha256,
                expected_policy_roster_sha256=args.policy_roster_sha256,
                scratch_parent=args.scratch_parent,
            )
            sys.stdout.buffer.write(canonical_json(result))
            return 0
        except Exception as exc:
            sys.stdout.buffer.write(
                canonical_json(
                    {
                        "schema": "brain-host-tree-capture-v5-capacity-plan-failure",
                        "error": str(exc)
                        if isinstance(
                            exc,
                            (
                                ProductionCaptureV5CLIError,
                                ProductionCaptureV5Error,
                                capture.TreeCaptureV5Error,
                            ),
                        )
                        else "capacity_plan_failed",
                        "payload_read": False,
                        "kingston_written": False,
                        "capture_authorized": False,
                        "erase_ready": False,
                    }
                )
            )
            return 2
    try:
        _validate_pins(args.target_volume_uuid, args.target_root_id)
        if (
            len(args.candidate_id) != 32
            or any(ch not in "0123456789abcdef" for ch in args.candidate_id)
            or len(args.index_sha256) != 64
            or any(ch not in "0123456789abcdef" for ch in args.index_sha256)
            or not _valid_sha256(args.plan_sha256)
        ):
            raise ProductionCaptureV5Error("production_verify_cli_pin_invalid")
        if (args.operation_spec is None) != (args.operation_spec_sha256 is None):
            raise ProductionCaptureV5Error("production_verify_shared_pool_pin_invalid")
        shared_pool_headroom_bytes = 0
        if args.shared_apfs_container_uuid is not None:
            if args.operation_spec is None or args.operation_spec_sha256 is None:
                raise ProductionCaptureV5Error("production_verify_shared_pool_pin_invalid")
            spec = _read_private_capture_spec(args.operation_spec, args.operation_spec_sha256)
            _capture_spec_fields(spec)
            try:
                spec_target_uuid, spec_target_root = _validate_pins(
                    spec["target_volume_uuid"], spec["target_root_id"]
                )
            except Exception as exc:
                raise ProductionCaptureV5Error("production_verify_shared_pool_pin_invalid") from exc
            payload_reserve = spec.get("payload_reserve_bytes")
            minimum_payload_reserve = (
                VERIFY_SHARED_POOL_FLOOR_BYTES
                + VERIFY_SCRATCH_DB_RESERVE_BYTES
                + VERIFY_SCRATCH_FREE_FLOOR_BYTES
            )
            if (
                spec_target_uuid != args.target_volume_uuid.lower()
                or spec_target_root != args.target_root_id
                or type(payload_reserve) is not int
                or payload_reserve < minimum_payload_reserve
            ):
                raise ProductionCaptureV5Error("production_verify_shared_pool_pin_invalid")
            shared_pool_headroom_bytes = payload_reserve - minimum_payload_reserve
        elif args.operation_spec is not None:
            raise ProductionCaptureV5Error("production_verify_shared_pool_pin_invalid")
        config_path = _canonical_config_path(args.storage_config)
        if __package__ in {None, ""}:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        with storage.read_only_guard(config_path) as guard:
            _assert_guard_pins(
                guard,
                guard.config,
                args.target_volume_uuid.lower(),
                args.target_root_id,
            )
            reader = DescriptorWriter.read_only_from_storage_guard(guard)
            scratch = PinnedScratchV5.open(args.scratch_parent)
            try:
                result = verify_production_candidate_v5(
                    reader,
                    args.candidate_id,
                    expected_target_volume_uuid=args.target_volume_uuid,
                    expected_target_root_id=args.target_root_id,
                    scratch=scratch,
                    expected_index_sha256=args.index_sha256,
                    expected_plan_sha256=args.plan_sha256,
                    expected_source_data_volume_uuid=args.source_data_volume_uuid,
                    expected_shared_apfs_container_uuid=args.shared_apfs_container_uuid,
                    shared_pool_headroom_bytes=shared_pool_headroom_bytes,
                    expected_operation_spec_sha256=args.operation_spec_sha256,
                )
                result["operation_spec_sha256"] = args.operation_spec_sha256
                guard.revalidate()
            finally:
                scratch.close()
                reader.close()
        sys.stdout.buffer.write(canonical_json(result))
        return 0
    except Exception as exc:
        message = str(exc)
        code = (
            message
            if isinstance(exc, ProductionCaptureV5Error) and message in _VERIFY_PUBLIC_ERRORS
            else "verify_failed"
        )
        sys.stderr.write(f"brain-host-tree-capture-v5 verify failed: {code}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
