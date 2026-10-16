#!/usr/bin/env python3
"""Capture and verify a mixed directory/file held-Data candidate set.

This v2 protocol preserves the strict v1 directory-only format and adds exact
opaque quarantine units for the three declared private MCP file roles. It never
qualifies source closure, application consistency, T, Kingston K, restore,
activation, or erase readiness.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from tools.brain_host_held_data_file_capture_v1 import (
    ALLOWED_FILE_ROLES,
    HeldDataFileError,
    capture_held_data_file_unit_v1,
    preflight_held_data_file_roles_v1,
    read_file_policy,
    verify_held_data_file_unit_v1,
)
from tools.brain_host_held_data_file_capture_v1 import (
    CAPTURE_CONSISTENCY_LITERAL as FILE_CONSISTENCY_LITERAL,
)
from tools.brain_host_held_data_root_set_capture_v1 import (
    CAPTURE_CONSISTENCY_LITERAL,
    MAX_POLICY_BYTES,
    HeldDataRootSetError,
    _capture_id,
    _hex_digest,
    _open_index_relation,
    _scan_hardlinks,
)
from tools.brain_host_held_data_t_v1 import (
    REQUIRED_ROOT_ROLES,
    ApprovedDataPlan,
    HeldDataT,
    RootDeclaration,
    approved_data_plan_sha256,
)
from tools.brain_host_held_data_tree_capture_v3 import (
    _read_policy_pin,
    capture_held_data_root_v3,
    verify_held_data_root_relation_v3,
)
from tools.brain_host_tree_custody_v3 import verify_tree
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

POLICY_SCHEMA = "brain-host-data-root-set-policy-v2"
INDEX_SCHEMA = "brain-host-held-data-root-set-index-v2"
SET_BASE = "host-recovery/held-data-root-sets-v2"
MAX_POLICY_BYTES_V2 = MAX_POLICY_BYTES
POLICY_FIELDS = {"schema", "version", "source_plan_sha256", "roles"}
ROLE_FIELDS = {
    "state",
    "relative_locator",
    "kind",
    "unresolved_reason",
    "scope",
    "policy_sha256",
    "application_consistency",
}
DIRECTORY_ROLE_FIELDS = {
    "state",
    "kind",
    "relative_locator",
    "scope",
    "application_consistency",
    "policy_sha256",
    "capture_id",
    "relation_sha256",
    "manifest_sha256",
    "included_entry_count",
    "unresolved_exclusion_count",
    "external_custody_required_count",
}
FILE_ROLE_FIELDS = {
    "state",
    "kind",
    "relative_locator",
    "scope",
    "application_consistency",
    "policy_sha256",
    "capture_id",
    "manifest_sha256",
    "payload_sha256",
    "payload_bytes",
    "restore_disposition",
    "metadata_disposition",
}
INDEX_FIELDS = {
    "schema",
    "set_id",
    "source_plan_sha256",
    "role_policy_sha256",
    "role_policy",
    "source_data_identity",
    "roles",
    "declared_role_count",
    "captured_directory_count",
    "captured_file_count",
    "unresolved_role_count",
    "index_scope",
    "application_consistency",
    "required_root_closure_complete",
    "T_qualified",
    "kingston_K_frontier_qualified",
    "brain_restore_verified",
    "activation_authorized",
    "safe_to_erase",
}


class HeldDataRootSetV2Error(ValueError):
    """A mixed candidate-set plan, source, policy, or artifact failed closed."""


def _policy_read(
    path: Path,
    *,
    expected_sha256: str,
    plan: ApprovedDataPlan,
    expected_plan_sha256: str,
) -> dict[str, Any]:
    if not _hex_digest(expected_sha256):
        raise HeldDataRootSetV2Error("role_policy_sha256_invalid")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise HeldDataRootSetV2Error("role_policy_open_failed") from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size > MAX_POLICY_BYTES_V2
        ):
            raise HeldDataRootSetV2Error("role_policy_too_large")
        chunks: list[bytes] = []
        size = 0
        while chunk := os.read(fd, min(65536, MAX_POLICY_BYTES_V2 + 1 - size)):
            size += len(chunk)
            if size > MAX_POLICY_BYTES_V2:
                raise HeldDataRootSetV2Error("role_policy_too_large")
            chunks.append(chunk)
        raw = b"".join(chunks)
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
        ) or size != before.st_size:
            raise HeldDataRootSetV2Error("role_policy_changed_during_read")
    finally:
        os.close(fd)
    try:
        policy = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HeldDataRootSetV2Error("role_policy_json_invalid") from exc
    if (
        not isinstance(policy, dict)
        or canonical_json(policy) != raw
        or sha256(raw) != expected_sha256
    ):
        raise HeldDataRootSetV2Error("role_policy_canonical_hash_mismatch")
    validated = _validate_policy(policy, plan, expected_plan_sha256=expected_plan_sha256)
    return validated


def _validate_policy(
    policy: object,
    plan: ApprovedDataPlan,
    *,
    expected_plan_sha256: str,
) -> dict[str, Any]:
    if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS:
        raise HeldDataRootSetV2Error("role_policy_fields_invalid")
    if (
        policy.get("schema") != POLICY_SCHEMA
        or type(policy.get("version")) is not int
        or policy.get("version") != 2
        or policy.get("source_plan_sha256") != expected_plan_sha256
        or not _hex_digest(expected_plan_sha256)
        or approved_data_plan_sha256(plan) != expected_plan_sha256
    ):
        raise HeldDataRootSetV2Error("role_policy_plan_binding_invalid")
    roles = policy.get("roles")
    if not isinstance(roles, dict) or set(roles) != REQUIRED_ROOT_ROLES:
        raise HeldDataRootSetV2Error("role_policy_roster_incomplete_or_unexpected")
    for role in sorted(REQUIRED_ROOT_ROLES):
        record = roles[role]
        declaration = plan.root_roster[role]
        if not isinstance(record, dict) or set(record) != ROLE_FIELDS:
            raise HeldDataRootSetV2Error("role_policy_record_invalid")
        if declaration.present:
            if (
                record.get("relative_locator") != declaration.relative_locator
                or record.get("kind") != declaration.kind
                or record.get("unresolved_reason") is not None
                or not _hex_digest(record.get("policy_sha256"))
                or record.get("application_consistency") != CAPTURE_CONSISTENCY_LITERAL
            ):
                raise HeldDataRootSetV2Error("present_role_policy_plan_binding_invalid")
            if declaration.kind == "directory":
                if (
                    record.get("state") != "capture_directory"
                    or record.get("scope") != "non_protected"
                ):
                    raise HeldDataRootSetV2Error("present_directory_policy_invalid")
            elif declaration.kind == "file":
                if (
                    role not in ALLOWED_FILE_ROLES
                    or record.get("state") != "capture_file_quarantine"
                    or record.get("scope") != "private_developer_tool_opaque"
                ):
                    raise HeldDataRootSetV2Error("present_file_policy_invalid")
            else:
                raise HeldDataRootSetV2Error("present_role_kind_unsupported")
        elif (
            record.get("state") != "unresolved_absence"
            or record.get("relative_locator") is not None
            or record.get("kind") is not None
            or record.get("unresolved_reason") != declaration.unresolved_reason
            or record.get("scope") is not None
            or record.get("policy_sha256") is not None
            or record.get("application_consistency") is not None
        ):
            raise HeldDataRootSetV2Error("absent_role_policy_invalid")
    if plan.root_roster["verifier_worktree"].present:
        raise HeldDataRootSetV2Error("verifier_worktree_must_remain_unresolved")
    return policy


def capture_held_data_root_set_v2(
    view: HeldDataT,
    writer: DescriptorWriter,
    *,
    role_policy_path: Path,
    expected_plan_sha256: str,
    expected_role_policy_sha256: str,
    directory_policy_paths: dict[str, Path],
    expected_directory_policy_sha256_by_role: dict[str, str],
    file_policy_paths: dict[str, Path],
    expected_file_policy_sha256_by_role: dict[str, str],
    storage_guard: Any,
    scratch_root_relative: str,
    scratch_budget_bytes_per_role: int,
    set_id: str | None = None,
    reserve_capacity: Any = None,
) -> dict[str, Any]:
    """Preflight all declared files, capture all supported units, then index."""
    if not isinstance(view, HeldDataT) or view.synthetic:
        raise HeldDataRootSetV2Error("real_held_data_view_required")
    if approved_data_plan_sha256(view.plan) != expected_plan_sha256:
        raise HeldDataRootSetV2Error("plan_pin_does_not_match_supplied_plan")
    if writer.storage_guard is not storage_guard or storage_guard is None:
        raise HeldDataRootSetV2Error("pinned_kingston_guard_required")
    try:
        writer.assert_storage_binding(storage_guard)
    except Exception as exc:
        raise HeldDataRootSetV2Error("pinned_kingston_binding_invalid") from exc
    policy = _policy_read(
        role_policy_path,
        expected_sha256=expected_role_policy_sha256,
        plan=view.plan,
        expected_plan_sha256=expected_plan_sha256,
    )
    roles = policy["roles"]
    directory_roles = sorted(
        role for role, row in roles.items() if row["state"] == "capture_directory"
    )
    file_roles = sorted(
        role for role, row in roles.items() if row["state"] == "capture_file_quarantine"
    )
    if (
        set(directory_policy_paths) != set(directory_roles)
        or set(expected_directory_policy_sha256_by_role) != set(directory_roles)
        or set(file_policy_paths) != set(file_roles)
        or set(expected_file_policy_sha256_by_role) != set(file_roles)
    ):
        raise HeldDataRootSetV2Error("role_policy_path_roster_mismatch")

    directory_policies: dict[str, dict[str, Any]] = {}
    for role in directory_roles:
        if roles[role]["policy_sha256"] != expected_directory_policy_sha256_by_role[role]:
            raise HeldDataRootSetV2Error("directory_policy_hash_roster_mismatch")
        directory_policies[role] = _read_policy_pin(
            directory_policy_paths[role], expected_directory_policy_sha256_by_role[role]
        )[0]
    for role in file_roles:
        if roles[role]["policy_sha256"] != expected_file_policy_sha256_by_role[role]:
            raise HeldDataRootSetV2Error("file_policy_hash_roster_mismatch")
        read_file_policy(
            file_policy_paths[role],
            expected_sha256=expected_file_policy_sha256_by_role[role],
            plan=view.plan,
            expected_plan_sha256=expected_plan_sha256,
            role=role,
        )
    if not directory_roles and not file_roles:
        raise HeldDataRootSetV2Error("no_supported_present_roles")

    # Every file role is checked before the first Kingston write.
    try:
        preflight = (
            preflight_held_data_file_roles_v1(
                view,
                policy_path_by_role=file_policy_paths,
                expected_policy_sha256_by_role=expected_file_policy_sha256_by_role,
                expected_plan_sha256=expected_plan_sha256,
            )
            if file_roles
            else {
                "source_data_identity": view.result()["data"],
                "source_file_signatures": {},
            }
        )
        view.revalidate()
        writer.assert_storage_binding(storage_guard)
        source_identity = view.result()["data"]
        if preflight["source_data_identity"] != source_identity:
            raise HeldDataRootSetV2Error("file_preflight_source_identity_drift")
        _scan_hardlinks(view, directory_roles, directory_policies)
        view.revalidate()
        writer.assert_storage_binding(storage_guard)
    except (HeldDataRootSetV2Error, HeldDataRootSetError):
        raise
    except Exception as exc:
        raise HeldDataRootSetV2Error("mixed_source_preflight_failed") from exc

    chosen_set_id = set_id or secrets.token_hex(16)
    if not _capture_id(chosen_set_id):
        raise HeldDataRootSetV2Error("set_id_invalid")
    captured: dict[str, dict[str, Any]] = {}
    for role in directory_roles:
        try:
            view.revalidate()
            writer.assert_storage_binding(storage_guard)
            record = capture_held_data_root_v3(
                view,
                writer,
                role=role,
                policy_path=directory_policy_paths[role],
                expected_plan_sha256=expected_plan_sha256,
                expected_policy_sha256=expected_directory_policy_sha256_by_role[role],
                storage_guard=storage_guard,
                scratch_root_relative=scratch_root_relative,
                scratch_budget_bytes=scratch_budget_bytes_per_role,
                reserve_capacity=reserve_capacity,
            )
            relation_result = verify_held_data_root_relation_v3(
                writer,
                record["capture_id"],
                view.plan,
                storage_guard=storage_guard,
                expected_plan_sha256=expected_plan_sha256,
                expected_policy_sha256=expected_directory_policy_sha256_by_role[role],
                expected_relation_sha256=record["relation_sha256"],
            )
            relation = _open_index_relation(writer, record["capture_id"])
            archive = verify_tree(
                writer,
                record["capture_id"],
                expected_policy_sha256=expected_directory_policy_sha256_by_role[role],
            )
            if (
                relation_result.get("verified") is not True
                or relation.get("source_data_identity") != source_identity
                or relation.get("source_role") != role
                or relation.get("source_relative_locator")
                != view.plan.root_roster[role].relative_locator
                or relation.get("policy_sha256") != expected_directory_policy_sha256_by_role[role]
                or archive.get("complete") is not True
                or archive.get("manifest_sha256") != record.get("manifest_sha256")
                or archive.get("included_entry_count") != record.get("included_entry_count")
                or record.get("unresolved_exclusion_count") != 0
                or record.get("external_custody_required_count") != 0
                or archive.get("unresolved_exclusion_count") != 0
                or archive.get("external_custody_required_count") != 0
            ):
                raise HeldDataRootSetV2Error("directory_candidate_verification_failed")
        except Exception as exc:
            raise HeldDataRootSetV2Error(
                f"capture_set_directory_failed:{role}; prior_candidates={len(captured)}; "
                "no_set_index_published"
            ) from exc
        captured[role] = {
            "state": "captured_candidate",
            "kind": "directory",
            "relative_locator": view.plan.root_roster[role].relative_locator,
            "scope": "non_protected",
            "application_consistency": CAPTURE_CONSISTENCY_LITERAL,
            "policy_sha256": expected_directory_policy_sha256_by_role[role],
            "capture_id": record["capture_id"],
            "relation_sha256": record["relation_sha256"],
            "manifest_sha256": record["manifest_sha256"],
            "included_entry_count": record["included_entry_count"],
            "unresolved_exclusion_count": record["unresolved_exclusion_count"],
            "external_custody_required_count": record["external_custody_required_count"],
        }

    for role in file_roles:
        try:
            view.revalidate()
            writer.assert_storage_binding(storage_guard)
            record = capture_held_data_file_unit_v1(
                view,
                writer,
                role=role,
                policy_path=file_policy_paths[role],
                expected_plan_sha256=expected_plan_sha256,
                expected_policy_sha256=expected_file_policy_sha256_by_role[role],
                storage_guard=storage_guard,
                expected_source_file_signature=preflight["source_file_signatures"][role],
            )
            verified = verify_held_data_file_unit_v1(
                writer,
                record["capture_id"],
                view.plan,
                role=role,
                policy_path=file_policy_paths[role],
                expected_plan_sha256=expected_plan_sha256,
                expected_policy_sha256=expected_file_policy_sha256_by_role[role],
                expected_manifest_sha256=record["manifest_sha256"],
                storage_guard=storage_guard,
            )
            if (
                record.get("candidate_published") is not True
                or record.get("independently_verified") is not False
                or verified.get("verified") is not True
                or verified.get("source_data_identity") != source_identity
                or verified.get("capture_id") != record.get("capture_id")
                or verified.get("manifest_sha256") != record.get("manifest_sha256")
                or verified.get("role") != role
                or verified.get("payload_sha256") != record.get("payload_sha256")
                or verified.get("payload_bytes") != record.get("payload_bytes")
            ):
                raise HeldDataRootSetV2Error("file_candidate_verification_failed")
        except Exception as exc:
            raise HeldDataRootSetV2Error(
                f"capture_set_file_failed:{role}; prior_candidates={len(captured)}; "
                "no_set_index_published"
            ) from exc
        captured[role] = {
            "state": "file_quarantine_candidate",
            "kind": "file",
            "relative_locator": view.plan.root_roster[role].relative_locator,
            "scope": "private_developer_tool_opaque",
            "application_consistency": FILE_CONSISTENCY_LITERAL,
            "policy_sha256": expected_file_policy_sha256_by_role[role],
            "capture_id": record["capture_id"],
            "manifest_sha256": record["manifest_sha256"],
            "payload_sha256": record["payload_sha256"],
            "payload_bytes": record["payload_bytes"],
            "restore_disposition": "quarantine_only",
            "metadata_disposition": "omitted_unqualified",
        }

    view.revalidate()
    writer.assert_storage_binding(storage_guard)
    index_roles: dict[str, dict[str, Any]] = {}
    for role in sorted(REQUIRED_ROOT_ROLES):
        if role in captured:
            index_roles[role] = captured[role]
        else:
            declaration: RootDeclaration = view.plan.root_roster[role]
            index_roles[role] = {
                "state": "absent_unresolved",
                "unresolved_reason": declaration.unresolved_reason,
            }
    unresolved_count = len(REQUIRED_ROOT_ROLES) - len(captured)
    index = {
        "schema": INDEX_SCHEMA,
        "set_id": chosen_set_id,
        "source_plan_sha256": expected_plan_sha256,
        "role_policy_sha256": expected_role_policy_sha256,
        "role_policy": policy,
        "source_data_identity": source_identity,
        "roles": index_roles,
        "declared_role_count": len(REQUIRED_ROOT_ROLES),
        "captured_directory_count": len(directory_roles),
        "captured_file_count": len(file_roles),
        "unresolved_role_count": unresolved_count,
        "index_scope": "plan_declared_supported_directory_and_private_file_candidates_only",
        "application_consistency": CAPTURE_CONSISTENCY_LITERAL,
        "required_root_closure_complete": False,
        "T_qualified": False,
        "kingston_K_frontier_qualified": False,
        "brain_restore_verified": False,
        "activation_authorized": False,
        "safe_to_erase": False,
    }
    if set(index) != INDEX_FIELDS:
        raise HeldDataRootSetV2Error("set_index_internal_fields_invalid")
    raw = canonical_json(index)
    path = f"{SET_BASE}/{chosen_set_id}.json"
    writer.create(path, raw)
    writer.assert_storage_binding(storage_guard)
    if writer.read_bytes(path, max_bytes=MAX_POLICY_BYTES_V2 * 8) != raw:
        raise HeldDataRootSetV2Error("set_index_readback_mismatch")
    return {
        "schema": INDEX_SCHEMA,
        "candidate_published": True,
        "independently_verified": False,
        "set_id": chosen_set_id,
        "set_index_sha256": sha256(raw),
        "captured_directory_count": len(directory_roles),
        "captured_file_count": len(file_roles),
        "unresolved_role_count": unresolved_count,
        "required_root_closure_complete": False,
        "T_qualified": False,
        "kingston_K_frontier_qualified": False,
        "brain_restore_verified": False,
        "activation_authorized": False,
        "safe_to_erase": False,
    }


def verify_held_data_root_set_v2(
    writer: DescriptorWriter,
    set_id: str,
    plan: ApprovedDataPlan,
    *,
    storage_guard: Any,
    expected_plan_sha256: str,
    role_policy_path: Path,
    expected_role_policy_sha256: str,
    directory_policy_paths: dict[str, Path],
    expected_directory_policy_sha256_by_role: dict[str, str],
    file_policy_paths: dict[str, Path],
    expected_file_policy_sha256_by_role: dict[str, str],
    expected_set_index_sha256: str,
) -> dict[str, Any]:
    """Freshly verify a mixed set index and every exact directory/file unit."""
    if writer.storage_guard is not storage_guard or storage_guard is None:
        raise HeldDataRootSetV2Error("pinned_kingston_guard_required")
    writer.assert_storage_binding(storage_guard)
    if not _hex_digest(expected_set_index_sha256):
        raise HeldDataRootSetV2Error("set_index_sha256_invalid")
    if approved_data_plan_sha256(plan) != expected_plan_sha256:
        raise HeldDataRootSetV2Error("plan_pin_does_not_match_supplied_plan")
    policy = _policy_read(
        role_policy_path,
        expected_sha256=expected_role_policy_sha256,
        plan=plan,
        expected_plan_sha256=expected_plan_sha256,
    )
    directory_roles = {
        role
        for role, declaration in plan.root_roster.items()
        if declaration.present and declaration.kind == "directory"
    }
    file_roles = {
        role
        for role, declaration in plan.root_roster.items()
        if declaration.present and declaration.kind == "file"
    }
    if not directory_roles and not file_roles:
        raise HeldDataRootSetV2Error("no_supported_present_roles")
    if (
        set(directory_policy_paths) != directory_roles
        or set(expected_directory_policy_sha256_by_role) != directory_roles
        or set(file_policy_paths) != file_roles
        or set(expected_file_policy_sha256_by_role) != file_roles
    ):
        raise HeldDataRootSetV2Error("role_policy_path_roster_mismatch")
    for role in directory_roles:
        if policy["roles"][role]["policy_sha256"] != expected_directory_policy_sha256_by_role[role]:
            raise HeldDataRootSetV2Error("directory_policy_hash_roster_mismatch")
        _read_policy_pin(
            directory_policy_paths[role],
            expected_directory_policy_sha256_by_role[role],
        )
    for role in file_roles:
        if policy["roles"][role]["policy_sha256"] != expected_file_policy_sha256_by_role[role]:
            raise HeldDataRootSetV2Error("file_policy_hash_roster_mismatch")
        read_file_policy(
            file_policy_paths[role],
            expected_sha256=expected_file_policy_sha256_by_role[role],
            plan=plan,
            expected_plan_sha256=expected_plan_sha256,
            role=role,
        )
    if not _capture_id(set_id):
        raise HeldDataRootSetV2Error("set_id_invalid")
    raw = writer.read_bytes(f"{SET_BASE}/{set_id}.json", max_bytes=MAX_POLICY_BYTES_V2 * 8)
    try:
        index = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HeldDataRootSetV2Error("set_index_json_invalid") from exc
    if not isinstance(index, dict) or canonical_json(index) != raw:
        raise HeldDataRootSetV2Error("set_index_canonical_encoding_invalid")
    source_identity = index.get("source_data_identity")
    if (
        set(index) != INDEX_FIELDS
        or sha256(raw) != expected_set_index_sha256
        or index.get("schema") != INDEX_SCHEMA
        or index.get("set_id") != set_id
        or index.get("source_plan_sha256") != expected_plan_sha256
        or index.get("role_policy_sha256") != expected_role_policy_sha256
        or index.get("role_policy") != policy
        or not isinstance(source_identity, dict)
        or source_identity.get("data_volume_uuid") != plan.data_volume_uuid.lower()
        or source_identity.get("data_device_identifier") != plan.data_device_identifier
        or source_identity.get("data_container_reference") != plan.data_container_reference
        or source_identity.get("data_volume_group_id") != plan.data_volume_group_id.lower()
        or source_identity.get("readonly") is not True
        or type(index.get("declared_role_count")) is not int
        or index.get("declared_role_count") != len(REQUIRED_ROOT_ROLES)
        or index.get("index_scope")
        != "plan_declared_supported_directory_and_private_file_candidates_only"
        or index.get("application_consistency") != CAPTURE_CONSISTENCY_LITERAL
        or index.get("required_root_closure_complete") is not False
        or index.get("T_qualified") is not False
        or index.get("kingston_K_frontier_qualified") is not False
        or index.get("brain_restore_verified") is not False
        or index.get("activation_authorized") is not False
        or index.get("safe_to_erase") is not False
    ):
        raise HeldDataRootSetV2Error("set_index_binding_invalid")
    role_rows = index.get("roles")
    if not isinstance(role_rows, dict) or set(role_rows) != REQUIRED_ROOT_ROLES:
        raise HeldDataRootSetV2Error("set_index_role_roster_invalid")
    verified_directories = 0
    verified_files = 0
    unresolved_roles = 0
    for role in sorted(REQUIRED_ROOT_ROLES):
        declaration = plan.root_roster[role]
        row = role_rows[role]
        if not isinstance(row, dict):
            raise HeldDataRootSetV2Error("set_index_role_record_invalid")
        if not declaration.present:
            if (
                set(row) != {"state", "unresolved_reason"}
                or row.get("state") != "absent_unresolved"
                or row.get("unresolved_reason") != declaration.unresolved_reason
            ):
                raise HeldDataRootSetV2Error("set_index_absence_disposition_mismatch")
            unresolved_roles += 1
            continue
        if (
            row.get("kind") != declaration.kind
            or row.get("relative_locator") != declaration.relative_locator
            or row.get("application_consistency") != CAPTURE_CONSISTENCY_LITERAL
        ):
            raise HeldDataRootSetV2Error("set_index_role_plan_binding_invalid")
        if declaration.kind == "directory":
            if (
                set(row) != DIRECTORY_ROLE_FIELDS
                or row.get("state") != "captured_candidate"
                or row.get("scope") != "non_protected"
                or row.get("policy_sha256") != expected_directory_policy_sha256_by_role[role]
                or type(row.get("included_entry_count")) is not int
                or row["included_entry_count"] < 1
                or type(row.get("unresolved_exclusion_count")) is not int
                or row["unresolved_exclusion_count"] != 0
                or type(row.get("external_custody_required_count")) is not int
                or row["external_custody_required_count"] != 0
                or not _capture_id(row.get("capture_id"))
                or not _hex_digest(row.get("relation_sha256"))
            ):
                raise HeldDataRootSetV2Error("directory_role_record_invalid")
            relation_result = verify_held_data_root_relation_v3(
                writer,
                row["capture_id"],
                plan,
                storage_guard=storage_guard,
                expected_plan_sha256=expected_plan_sha256,
                expected_policy_sha256=expected_directory_policy_sha256_by_role[role],
                expected_relation_sha256=row["relation_sha256"],
            )
            relation = _open_index_relation(writer, row["capture_id"])
            archive = verify_tree(
                writer,
                row["capture_id"],
                expected_policy_sha256=expected_directory_policy_sha256_by_role[role],
            )
            if (
                relation_result.get("verified") is not True
                or relation.get("source_data_identity") != source_identity
                or relation.get("source_role") != role
                or relation.get("source_relative_locator") != declaration.relative_locator
                or relation.get("unresolved_exclusion_count") != 0
                or relation.get("external_custody_required_count") != 0
                or archive.get("complete") is not True
                or archive.get("manifest_sha256") != row.get("manifest_sha256")
                or archive.get("included_entry_count") != row.get("included_entry_count")
            ):
                raise HeldDataRootSetV2Error("directory_archive_relation_mismatch")
            verified_directories += 1
        elif declaration.kind == "file":
            if (
                role not in ALLOWED_FILE_ROLES
                or set(row) != FILE_ROLE_FIELDS
                or row.get("state") != "file_quarantine_candidate"
                or row.get("scope") != "private_developer_tool_opaque"
                or row.get("policy_sha256") != expected_file_policy_sha256_by_role[role]
                or row.get("restore_disposition") != "quarantine_only"
                or row.get("metadata_disposition") != "omitted_unqualified"
                or type(row.get("payload_bytes")) is not int
                or row["payload_bytes"] < 0
                or not _capture_id(row.get("capture_id"))
                or not _hex_digest(row.get("manifest_sha256"))
                or not _hex_digest(row.get("payload_sha256"))
            ):
                raise HeldDataRootSetV2Error("file_role_record_invalid")
            try:
                verified = verify_held_data_file_unit_v1(
                    writer,
                    row["capture_id"],
                    plan,
                    role=role,
                    policy_path=file_policy_paths[role],
                    expected_plan_sha256=expected_plan_sha256,
                    expected_policy_sha256=expected_file_policy_sha256_by_role[role],
                    expected_manifest_sha256=row["manifest_sha256"],
                    storage_guard=storage_guard,
                )
            except HeldDataFileError as exc:
                raise HeldDataRootSetV2Error("file_unit_fresh_verification_failed") from exc
            if (
                verified.get("verified") is not True
                or verified.get("source_data_identity") != source_identity
                or verified.get("payload_sha256") != row.get("payload_sha256")
                or verified.get("payload_bytes") != row.get("payload_bytes")
            ):
                raise HeldDataRootSetV2Error("file_unit_set_binding_mismatch")
            verified_files += 1
        else:
            raise HeldDataRootSetV2Error("set_index_role_kind_unsupported")

    if (
        type(index.get("captured_directory_count")) is not int
        or index.get("captured_directory_count") != verified_directories
        or type(index.get("captured_file_count")) is not int
        or index.get("captured_file_count") != verified_files
        or type(index.get("unresolved_role_count")) is not int
        or index.get("unresolved_role_count") != unresolved_roles
    ):
        raise HeldDataRootSetV2Error("set_index_count_mismatch")
    writer.assert_storage_binding(storage_guard)
    return {
        "schema": INDEX_SCHEMA,
        "verified": True,
        "set_id": set_id,
        "set_index_sha256": expected_set_index_sha256,
        "captured_directory_count": verified_directories,
        "captured_file_count": verified_files,
        "unresolved_role_count": unresolved_roles,
        "required_root_closure_complete": False,
        "T_qualified": False,
        "kingston_K_frontier_qualified": False,
        "brain_restore_verified": False,
        "activation_authorized": False,
        "safe_to_erase": False,
    }
