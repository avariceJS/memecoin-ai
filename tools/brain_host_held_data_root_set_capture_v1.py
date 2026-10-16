#!/usr/bin/env python3
"""Capture a plan-bound set of supported held Data directory roots.

This coordinator publishes only an integrity index over candidate byte-custody
units. It never attests source-roster completeness, SQLite consistency, restore,
T, Kingston K, activation, or erase readiness.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from tools.brain_host_held_data_t_v1 import (
    REQUIRED_ROOT_ROLES,
    ApprovedDataPlan,
    HeldDataT,
    RootDeclaration,
    approved_data_plan_sha256,
)
from tools.brain_host_held_data_tree_capture_v3 import (
    RELATION_BASE,
    _read_policy_pin,
    capture_held_data_root_v3,
    verify_held_data_root_relation_v3,
)
from tools.brain_host_tree_custody_v3 import (
    TreeCustodyV3Error,
    _classify_path,
    _path_component,
    verify_tree,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

POLICY_SCHEMA = "brain-host-data-root-set-policy-v1"
INDEX_SCHEMA = "brain-host-held-data-root-set-index-v1"
SET_BASE = "host-recovery/held-data-root-sets-v1"
MAX_POLICY_BYTES = 1024 * 1024
MAX_ENTRIES_PER_PREFLIGHT = 5_000_000
CAPTURE_CONSISTENCY_LITERAL = "unqualified_opaque_bytes"
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
CAPTURED_ROLE_FIELDS = {
    "state",
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


class HeldDataRootSetError(ValueError):
    """A plan, role policy, source, or set-index boundary failed closed."""


def _hex_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _capture_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_role_policy(
    policy: object,
    plan: ApprovedDataPlan,
    *,
    expected_plan_sha256: str,
) -> dict[str, Any]:
    if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS:
        raise HeldDataRootSetError("role_policy_fields_invalid")
    if (
        policy.get("schema") != POLICY_SCHEMA
        or type(policy.get("version")) is not int
        or policy.get("version") != 1
        or policy.get("source_plan_sha256") != expected_plan_sha256
        or not _hex_digest(expected_plan_sha256)
        or approved_data_plan_sha256(plan) != expected_plan_sha256
    ):
        raise HeldDataRootSetError("role_policy_plan_binding_invalid")
    roles = policy.get("roles")
    if not isinstance(roles, dict) or set(roles) != REQUIRED_ROOT_ROLES:
        raise HeldDataRootSetError("role_policy_roster_incomplete_or_unexpected")

    for role in sorted(REQUIRED_ROOT_ROLES):
        record = roles[role]
        declaration = plan.root_roster[role]
        if not isinstance(record, dict) or set(record) != ROLE_FIELDS:
            raise HeldDataRootSetError("role_policy_record_invalid")
        expected_locator = declaration.relative_locator
        if declaration.present:
            if (
                record.get("relative_locator") != expected_locator
                or record.get("kind") != declaration.kind
                or record.get("unresolved_reason") is not None
            ):
                raise HeldDataRootSetError("role_policy_plan_locator_mismatch")
            if declaration.kind != "directory":
                if record.get("state") != "unsupported_present_file":
                    raise HeldDataRootSetError("present_file_role_policy_invalid")
                raise HeldDataRootSetError("unsupported_present_file_role")
            if record.get("state") != "capture_directory":
                raise HeldDataRootSetError("present_directory_role_policy_invalid")
            if record.get("scope") == "protected_opaque":
                raise HeldDataRootSetError("protected_role_not_supported")
            if record.get("scope") != "non_protected":
                raise HeldDataRootSetError("present_directory_scope_unqualified")
            if not _hex_digest(record.get("policy_sha256")):
                raise HeldDataRootSetError("role_policy_sha256_invalid")
            if record.get("application_consistency") != CAPTURE_CONSISTENCY_LITERAL:
                raise HeldDataRootSetError("application_consistency_must_remain_unqualified")
        else:
            if (
                record.get("state") != "unresolved_absence"
                or record.get("relative_locator") is not None
                or record.get("kind") is not None
                or record.get("unresolved_reason") != declaration.unresolved_reason
                or record.get("scope") is not None
                or record.get("policy_sha256") is not None
                or record.get("application_consistency") is not None
            ):
                raise HeldDataRootSetError("absent_role_disposition_mismatch")
    return policy


def _read_role_policy(
    path: Path,
    *,
    expected_sha256: str,
    plan: ApprovedDataPlan,
    expected_plan_sha256: str,
) -> dict[str, Any]:
    if not _hex_digest(expected_sha256):
        raise HeldDataRootSetError("role_policy_sha256_invalid")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise HeldDataRootSetError("role_policy_open_failed") from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size > MAX_POLICY_BYTES
        ):
            raise HeldDataRootSetError("role_policy_file_identity_invalid")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(fd, min(64 * 1024, MAX_POLICY_BYTES + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_POLICY_BYTES:
                raise HeldDataRootSetError("role_policy_size_limit")
        raw = b"".join(chunks)
        try:
            policy = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HeldDataRootSetError("role_policy_json_invalid") from exc
        if canonical_json(policy) != raw or sha256(raw) != expected_sha256:
            raise HeldDataRootSetError("role_policy_pin_mismatch")
        validated = _validate_role_policy(
            policy,
            plan,
            expected_plan_sha256=expected_plan_sha256,
        )
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
        ) or total != before.st_size:
            raise HeldDataRootSetError("role_policy_changed_during_read")
        return validated
    finally:
        os.close(fd)


def _scan_hardlinks(
    view: HeldDataT,
    role_names: list[str],
    policy_by_role: dict[str, dict[str, Any]],
) -> None:
    observed: dict[tuple[int, int], dict[str, Any]] = {}
    directory_roles: dict[tuple[int, int], set[str]] = {}
    entry_count = 0
    for role in role_names:
        with view.borrow_root(role) as root_fd:
            root_stat = os.fstat(root_fd)
            root_key = (int(root_stat.st_dev), int(root_stat.st_ino))
            prior_roles = directory_roles.setdefault(root_key, set())
            if prior_roles and role not in prior_roles:
                raise HeldDataRootSetError("cross_root_directory_alias_unsupported")
            prior_roles.add(role)
            role_directories = {root_key}
            # Reopen each queued directory from the held root; open descriptor count is
            # independent of directory fanout and bounded by path depth.
            stack: list[tuple[str, tuple[int, int]]] = [("", root_key)]
            while stack:
                prefix, expected_key = stack.pop()
                directory_fd = root_fd
                owns_directory_fd = False
                if prefix:
                    current_fd = os.dup(root_fd)
                    try:
                        for component in prefix.split("/"):
                            try:
                                child_fd = os.open(
                                    component,
                                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=current_fd,
                                )
                            except OSError as exc:
                                raise HeldDataRootSetError(
                                    "hardlink_preflight_directory_reopen_failed"
                                ) from exc
                            child_stat = os.fstat(child_fd)
                            if (
                                not stat.S_ISDIR(child_stat.st_mode)
                                or child_stat.st_dev != root_stat.st_dev
                            ):
                                os.close(child_fd)
                                raise HeldDataRootSetError(
                                    "hardlink_preflight_directory_reopen_identity_invalid"
                                )
                            os.close(current_fd)
                            current_fd = child_fd
                        opened_stat = os.fstat(current_fd)
                        if (int(opened_stat.st_dev), int(opened_stat.st_ino)) != expected_key:
                            raise HeldDataRootSetError(
                                "hardlink_preflight_directory_reopen_identity_changed"
                            )
                        directory_fd = current_fd
                        owns_directory_fd = True
                        current_fd = -1
                    finally:
                        if current_fd >= 0:
                            os.close(current_fd)
                try:
                    try:
                        scan = os.scandir(directory_fd)
                    except OSError as exc:
                        raise HeldDataRootSetError("hardlink_preflight_scan_failed") from exc
                    with scan:
                        for entry in scan:
                            try:
                                name = _path_component(entry.name)
                            except (UnicodeError, TreeCustodyV3Error) as exc:
                                raise HeldDataRootSetError(
                                    "hardlink_preflight_path_component_invalid"
                                ) from exc
                            entry_count += 1
                            if entry_count > MAX_ENTRIES_PER_PREFLIGHT:
                                raise HeldDataRootSetError("hardlink_preflight_entry_limit")
                            if entry_count % 4096 == 0:
                                view.revalidate_mount_fast()
                            try:
                                before = os.stat(
                                    name,
                                    dir_fd=directory_fd,
                                    follow_symlinks=False,
                                )
                            except OSError as exc:
                                raise HeldDataRootSetError(
                                    "hardlink_preflight_stat_failed"
                                ) from exc
                            if before.st_dev != root_stat.st_dev:
                                raise HeldDataRootSetError("hardlink_preflight_mount_boundary")
                            relative = f"{prefix}/{name}" if prefix else name
                            if stat.S_ISDIR(before.st_mode):
                                state, _, _ = _classify_path(
                                    relative,
                                    "directory",
                                    policy_by_role[role],
                                    protected_scope=False,
                                )
                                if state != "included":
                                    continue
                                try:
                                    child_fd = os.open(
                                        name,
                                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=directory_fd,
                                    )
                                except OSError as exc:
                                    raise HeldDataRootSetError(
                                        "hardlink_preflight_directory_open_failed"
                                    ) from exc
                                try:
                                    after = os.fstat(child_fd)
                                    child_key = (int(after.st_dev), int(after.st_ino))
                                    if (
                                        not stat.S_ISDIR(after.st_mode)
                                        or (before.st_dev, before.st_ino)
                                        != (after.st_dev, after.st_ino)
                                        or after.st_dev != root_stat.st_dev
                                    ):
                                        raise HeldDataRootSetError(
                                            "hardlink_preflight_directory_changed"
                                        )
                                finally:
                                    os.close(child_fd)
                                if child_key in role_directories:
                                    raise HeldDataRootSetError(
                                        "directory_alias_within_root_unsupported"
                                    )
                                child_roles = directory_roles.setdefault(child_key, set())
                                if child_roles and role not in child_roles:
                                    raise HeldDataRootSetError(
                                        "cross_root_directory_alias_unsupported"
                                    )
                                child_roles.add(role)
                                role_directories.add(child_key)
                                stack.append((relative, child_key))
                            elif stat.S_ISREG(before.st_mode):
                                if before.st_nlink <= 1:
                                    continue
                                state, _, _ = _classify_path(
                                    relative,
                                    "file",
                                    policy_by_role[role],
                                    protected_scope=False,
                                )
                                if state != "included":
                                    continue
                                key = (int(before.st_dev), int(before.st_ino))
                                row = observed.setdefault(
                                    key,
                                    {"roles": set(), "paths": 0, "link_count": before.st_nlink},
                                )
                                if row["link_count"] != before.st_nlink:
                                    raise HeldDataRootSetError("hardlink_preflight_identity_drift")
                                row["roles"].add(role)
                                row["paths"] += 1
                            elif not stat.S_ISLNK(before.st_mode):
                                raise HeldDataRootSetError(
                                    "hardlink_preflight_unsupported_entry_type"
                                )
                finally:
                    if owns_directory_fd:
                        os.close(directory_fd)
    for row in observed.values():
        if len(row["roles"]) > 1:
            raise HeldDataRootSetError("cross_root_hardlink_unsupported")
        if row["paths"] != row["link_count"]:
            raise HeldDataRootSetError("hardlink_alias_outside_selected_roots")


def _open_index_relation(writer: DescriptorWriter, capture_id: str) -> dict[str, Any]:
    raw = writer.read_bytes(f"{RELATION_BASE}/{capture_id}.json", max_bytes=MAX_POLICY_BYTES)
    try:
        relation = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HeldDataRootSetError("relation_json_invalid") from exc
    if not isinstance(relation, dict) or canonical_json(relation) != raw:
        raise HeldDataRootSetError("relation_canonical_encoding_invalid")
    return relation


def _read_set_index(
    writer: DescriptorWriter,
    set_id: str,
    max_bytes: int,
) -> tuple[dict[str, Any], bytes]:
    if not _capture_id(set_id):
        raise HeldDataRootSetError("set_id_invalid")
    raw = writer.read_bytes(f"{SET_BASE}/{set_id}.json", max_bytes=max_bytes)
    try:
        index = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HeldDataRootSetError("set_index_json_invalid") from exc
    if not isinstance(index, dict) or canonical_json(index) != raw:
        raise HeldDataRootSetError("set_index_canonical_encoding_invalid")
    return index, raw


def capture_held_data_root_set_v1(
    view: HeldDataT,
    writer: DescriptorWriter,
    *,
    role_policy_path: Path,
    expected_plan_sha256: str,
    expected_role_policy_sha256: str,
    role_policy_paths: dict[str, Path],
    expected_policy_sha256_by_role: dict[str, str],
    storage_guard: Any,
    scratch_root_relative: str,
    scratch_budget_bytes_per_role: int,
    set_id: str | None = None,
    reserve_capacity: Any = None,
) -> dict[str, Any]:
    """Preflight all nine roles, capture supported directories, then index them."""
    if not isinstance(view, HeldDataT) or view.synthetic:
        raise HeldDataRootSetError("real_held_data_view_required")
    if approved_data_plan_sha256(view.plan) != expected_plan_sha256:
        raise HeldDataRootSetError("plan_pin_does_not_match_supplied_plan")
    if writer.storage_guard is not storage_guard or storage_guard is None:
        raise HeldDataRootSetError("pinned_kingston_guard_required")
    try:
        writer.assert_storage_binding(storage_guard)
    except Exception as exc:
        raise HeldDataRootSetError("pinned_kingston_binding_invalid") from exc

    policy = _read_role_policy(
        role_policy_path,
        expected_sha256=expected_role_policy_sha256,
        plan=view.plan,
        expected_plan_sha256=expected_plan_sha256,
    )
    roles = policy["roles"]
    present_roles = sorted(
        role for role, row in roles.items() if row["state"] == "capture_directory"
    )
    if set(role_policy_paths) != set(present_roles) or set(expected_policy_sha256_by_role) != set(
        present_roles
    ):
        raise HeldDataRootSetError("role_policy_path_roster_mismatch")
    policy_by_role: dict[str, dict[str, Any]] = {}
    for role in present_roles:
        if roles[role]["policy_sha256"] != expected_policy_sha256_by_role[role]:
            raise HeldDataRootSetError("role_policy_hash_roster_mismatch")
        policy_by_role[role] = _read_policy_pin(
            role_policy_paths[role], expected_policy_sha256_by_role[role]
        )[0]
    if not present_roles:
        raise HeldDataRootSetError("no_supported_present_directory_roles")

    view.revalidate()
    writer.assert_storage_binding(storage_guard)
    root_result = view.result()
    source_identity = root_result.get("data")
    if not isinstance(source_identity, dict) or source_identity.get("readonly") is not True:
        raise HeldDataRootSetError("held_source_identity_not_readonly")
    _scan_hardlinks(view, present_roles, policy_by_role)
    view.revalidate()
    writer.assert_storage_binding(storage_guard)

    chosen_set_id = set_id or secrets.token_hex(16)
    if not _capture_id(chosen_set_id):
        raise HeldDataRootSetError("set_id_invalid")
    captured: dict[str, dict[str, Any]] = {}
    for role in present_roles:
        try:
            view.revalidate()
            writer.assert_storage_binding(storage_guard)
            capture = capture_held_data_root_v3(
                view,
                writer,
                role=role,
                policy_path=role_policy_paths[role],
                expected_plan_sha256=expected_plan_sha256,
                expected_policy_sha256=expected_policy_sha256_by_role[role],
                storage_guard=storage_guard,
                scratch_root_relative=scratch_root_relative,
                scratch_budget_bytes=scratch_budget_bytes_per_role,
                reserve_capacity=reserve_capacity,
            )
            relation = verify_held_data_root_relation_v3(
                writer,
                capture["capture_id"],
                view.plan,
                storage_guard=storage_guard,
                expected_plan_sha256=expected_plan_sha256,
                expected_policy_sha256=expected_policy_sha256_by_role[role],
                expected_relation_sha256=capture["relation_sha256"],
            )
            relation_document = _open_index_relation(writer, capture["capture_id"])
            archive = verify_tree(
                writer,
                capture["capture_id"],
                expected_policy_sha256=expected_policy_sha256_by_role[role],
            )
            if (
                relation.get("verified") is not True
                or relation_document.get("source_data_identity") != source_identity
                or relation_document.get("source_role") != role
                or relation_document.get("source_relative_locator")
                != view.plan.root_roster[role].relative_locator
                or relation_document.get("policy_sha256") != expected_policy_sha256_by_role[role]
                or relation_document.get("unresolved_exclusion_count")
                != capture.get("unresolved_exclusion_count")
                or relation_document.get("external_custody_required_count")
                != capture.get("external_custody_required_count")
                or archive.get("complete") is not True
                or archive.get("manifest_sha256") != capture.get("manifest_sha256")
                or archive.get("included_entry_count") != capture.get("included_entry_count")
                or archive.get("unresolved_exclusion_count")
                != capture.get("unresolved_exclusion_count")
                or archive.get("external_custody_required_count")
                != capture.get("external_custody_required_count")
                or capture.get("unresolved_exclusion_count") != 0
                or capture.get("external_custody_required_count") != 0
            ):
                raise HeldDataRootSetError("role_capture_not_independently_verified")
        except Exception as exc:
            raise HeldDataRootSetError(
                f"capture_set_role_failed:{role}; prior_candidates={len(captured)}; "
                "no_set_index_published"
            ) from exc
        captured[role] = {
            "state": "captured_candidate",
            "relative_locator": view.plan.root_roster[role].relative_locator,
            "scope": "non_protected",
            "application_consistency": CAPTURE_CONSISTENCY_LITERAL,
            "policy_sha256": expected_policy_sha256_by_role[role],
            "capture_id": capture["capture_id"],
            "relation_sha256": capture["relation_sha256"],
            "manifest_sha256": capture["manifest_sha256"],
            "included_entry_count": capture["included_entry_count"],
            "unresolved_exclusion_count": capture["unresolved_exclusion_count"],
            "external_custody_required_count": capture["external_custody_required_count"],
        }

    try:
        view.revalidate()
        writer.assert_storage_binding(storage_guard)
    except Exception as exc:
        raise HeldDataRootSetError(
            f"capture_set_pre_publish_binding_failed; prior_candidates={len(captured)}; "
            "no_set_index_published"
        ) from exc
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
        "captured_directory_count": len(captured),
        "unresolved_role_count": unresolved_count,
        "index_scope": "plan_declared_supported_directory_candidates_only",
        "application_consistency": CAPTURE_CONSISTENCY_LITERAL,
        "required_root_closure_complete": False,
        "T_qualified": False,
        "kingston_K_frontier_qualified": False,
        "brain_restore_verified": False,
        "activation_authorized": False,
        "safe_to_erase": False,
    }
    if set(index) != INDEX_FIELDS:
        raise HeldDataRootSetError("set_index_internal_fields_invalid")
    raw = canonical_json(index)
    path = f"{SET_BASE}/{chosen_set_id}.json"
    writer.create(path, raw)
    writer.assert_storage_binding(storage_guard)
    if writer.read_bytes(path, max_bytes=MAX_POLICY_BYTES * 8) != raw:
        raise HeldDataRootSetError("set_index_readback_mismatch")
    return {
        "schema": INDEX_SCHEMA,
        "verified": True,
        "set_id": chosen_set_id,
        "set_index_sha256": sha256(raw),
        "role_policy_sha256": expected_role_policy_sha256,
        "captured_directory_count": len(captured),
        "unresolved_role_count": unresolved_count,
        "required_root_closure_complete": False,
        "T_qualified": False,
        "kingston_K_frontier_qualified": False,
        "brain_restore_verified": False,
        "activation_authorized": False,
        "safe_to_erase": False,
    }


def verify_held_data_root_set_v1(
    writer: DescriptorWriter,
    set_id: str,
    plan: ApprovedDataPlan,
    *,
    storage_guard: Any,
    expected_plan_sha256: str,
    role_policy_path: Path,
    expected_role_policy_sha256: str,
    expected_policy_sha256_by_role: dict[str, str],
    expected_set_index_sha256: str,
) -> dict[str, Any]:
    """Freshly verify a set index and every captured directory relation/archive."""
    if writer.storage_guard is not storage_guard or storage_guard is None:
        raise HeldDataRootSetError("pinned_kingston_guard_required")
    try:
        writer.assert_storage_binding(storage_guard)
    except Exception as exc:
        raise HeldDataRootSetError("pinned_kingston_binding_invalid") from exc
    if not _hex_digest(expected_set_index_sha256):
        raise HeldDataRootSetError("set_index_sha256_invalid")
    if approved_data_plan_sha256(plan) != expected_plan_sha256:
        raise HeldDataRootSetError("plan_pin_does_not_match_supplied_plan")
    policy = _read_role_policy(
        role_policy_path,
        expected_sha256=expected_role_policy_sha256,
        plan=plan,
        expected_plan_sha256=expected_plan_sha256,
    )
    expected_present = {
        role
        for role, declaration in plan.root_roster.items()
        if declaration.present and declaration.kind == "directory"
    }
    if set(expected_policy_sha256_by_role) != expected_present:
        raise HeldDataRootSetError("role_policy_hash_roster_mismatch")
    for role in expected_present:
        if policy["roles"][role]["policy_sha256"] != expected_policy_sha256_by_role[role]:
            raise HeldDataRootSetError("role_policy_hash_roster_mismatch")
    index, raw = _read_set_index(writer, set_id, MAX_POLICY_BYTES * 8)
    if (
        set(index) != INDEX_FIELDS
        or sha256(raw) != expected_set_index_sha256
        or index.get("schema") != INDEX_SCHEMA
        or index.get("set_id") != set_id
        or index.get("source_plan_sha256") != expected_plan_sha256
        or index.get("role_policy_sha256") != expected_role_policy_sha256
        or index.get("role_policy") != policy
        or not isinstance(index.get("source_data_identity"), dict)
        or index["source_data_identity"].get("data_volume_uuid") != plan.data_volume_uuid.lower()
        or index["source_data_identity"].get("data_device_identifier")
        != plan.data_device_identifier
        or index["source_data_identity"].get("data_container_reference")
        != plan.data_container_reference
        or index["source_data_identity"].get("data_volume_group_id")
        != plan.data_volume_group_id.lower()
        or index["source_data_identity"].get("readonly") is not True
        or type(index.get("declared_role_count")) is not int
        or index.get("declared_role_count") != len(REQUIRED_ROOT_ROLES)
        or index.get("index_scope") != "plan_declared_supported_directory_candidates_only"
        or index.get("application_consistency") != CAPTURE_CONSISTENCY_LITERAL
        or index.get("required_root_closure_complete") is not False
        or index.get("T_qualified") is not False
        or index.get("kingston_K_frontier_qualified") is not False
        or index.get("brain_restore_verified") is not False
        or index.get("activation_authorized") is not False
        or index.get("safe_to_erase") is not False
    ):
        raise HeldDataRootSetError("set_index_binding_invalid")
    roles = index.get("roles")
    if not isinstance(roles, dict) or set(roles) != REQUIRED_ROOT_ROLES:
        raise HeldDataRootSetError("set_index_role_roster_invalid")
    expected_captured: set[str] = set()
    for role in sorted(REQUIRED_ROOT_ROLES):
        declaration = plan.root_roster[role]
        role_row = roles[role]
        if not isinstance(role_row, dict):
            raise HeldDataRootSetError("set_index_role_record_invalid")
        if declaration.present:
            if declaration.kind != "directory":
                raise HeldDataRootSetError("unsupported_present_file_role")
            if role_row.get("state") != "captured_candidate":
                raise HeldDataRootSetError("present_directory_not_captured")
            included_entry_count = role_row.get("included_entry_count")
            if (
                set(role_row) != CAPTURED_ROLE_FIELDS
                or role_row.get("relative_locator") != declaration.relative_locator
                or role_row.get("scope") != "non_protected"
                or role_row.get("application_consistency") != CAPTURE_CONSISTENCY_LITERAL
                or role_row.get("policy_sha256") != expected_policy_sha256_by_role.get(role)
                or type(included_entry_count) is not int
                or not isinstance(included_entry_count, int)
                or included_entry_count < 1
                or type(role_row.get("unresolved_exclusion_count")) is not int
                or role_row.get("unresolved_exclusion_count") != 0
                or type(role_row.get("external_custody_required_count")) is not int
                or role_row.get("external_custody_required_count") != 0
                or not _capture_id(role_row.get("capture_id"))
                or not _hex_digest(role_row.get("relation_sha256"))
            ):
                raise HeldDataRootSetError("set_index_capture_binding_invalid")
            verified = verify_held_data_root_relation_v3(
                writer,
                role_row["capture_id"],
                plan,
                storage_guard=storage_guard,
                expected_plan_sha256=expected_plan_sha256,
                expected_policy_sha256=expected_policy_sha256_by_role[role],
                expected_relation_sha256=role_row["relation_sha256"],
            )
            relation = _open_index_relation(writer, role_row["capture_id"])
            archive = verify_tree(
                writer,
                role_row["capture_id"],
                expected_policy_sha256=expected_policy_sha256_by_role[role],
            )
            if (
                verified.get("verified") is not True
                or relation.get("source_data_identity") != index.get("source_data_identity")
                or relation.get("source_role") != role
                or relation.get("source_relative_locator") != declaration.relative_locator
                or relation.get("policy_sha256") != expected_policy_sha256_by_role[role]
                or relation.get("unresolved_exclusion_count")
                != role_row.get("unresolved_exclusion_count")
                or relation.get("external_custody_required_count")
                != role_row.get("external_custody_required_count")
                or archive.get("complete") is not True
                or archive.get("manifest_sha256") != role_row.get("manifest_sha256")
                or archive.get("included_entry_count") != role_row.get("included_entry_count")
                or archive.get("unresolved_exclusion_count")
                != role_row.get("unresolved_exclusion_count")
                or archive.get("external_custody_required_count")
                != role_row.get("external_custody_required_count")
            ):
                raise HeldDataRootSetError("set_index_archive_relation_mismatch")
            expected_captured.add(role)
        elif (
            role_row.get("state") != "absent_unresolved"
            or role_row.get("unresolved_reason") != declaration.unresolved_reason
            or set(role_row) != {"state", "unresolved_reason"}
        ):
            raise HeldDataRootSetError("set_index_absence_disposition_mismatch")
    if (
        type(index.get("captured_directory_count")) is not int
        or index.get("captured_directory_count") != len(expected_captured)
        or type(index.get("unresolved_role_count")) is not int
        or index.get("unresolved_role_count") != len(REQUIRED_ROOT_ROLES) - len(expected_captured)
    ):
        raise HeldDataRootSetError("set_index_count_mismatch")
    writer.assert_storage_binding(storage_guard)
    return {
        "schema": INDEX_SCHEMA,
        "verified": True,
        "set_id": set_id,
        "set_index_sha256": expected_set_index_sha256,
        "captured_directory_count": len(expected_captured),
        "unresolved_role_count": len(REQUIRED_ROOT_ROLES) - len(expected_captured),
        "required_root_closure_complete": False,
        "T_qualified": False,
        "kingston_K_frontier_qualified": False,
        "brain_restore_verified": False,
        "activation_authorized": False,
        "safe_to_erase": False,
    }
