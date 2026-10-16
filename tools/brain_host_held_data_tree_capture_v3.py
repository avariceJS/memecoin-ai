#!/usr/bin/env python3
"""Bind one v3 tree-custody unit to a held read-only Data root descriptor.

This adapter observes one declared directory root at a time. Its relation record
binds the v3 archive to the approved Data plan and held root identity, while
leaving source cutoff T, root-set closure, Kingston K, and erase readiness false.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

from tools import brain_host_tree_custody as v2
from tools import brain_host_tree_custody_v3 as v3
from tools.brain_host_held_data_t_v1 import (
    ApprovedDataPlan,
    HeldDataT,
    approved_data_plan_sha256,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

RELATION_BASE = "host-recovery/held-data-tree-custody-v3"
RELATION_SCHEMA = "brain-host-held-data-tree-custody-relation-v1"
MAX_RELATION_BYTES = 1024 * 1024
RELATION_FIELDS = {
    "schema",
    "capture_id",
    "source_plan_sha256",
    "source_data_identity",
    "source_role",
    "source_relative_locator",
    "source_root_identity",
    "policy_sha256",
    "v3_manifest_sha256",
    "v3_recovery_point_source",
    "source_view_observed",
    "acceptance_status",
    "root_unit_policy_complete",
    "root_unit_policy_scope",
    "metadata_restore_qualified",
    "unresolved_exclusion_count",
    "external_custody_required_count",
    "T_qualified",
    "kingston_K_frontier_qualified",
    "required_root_closure_complete",
    "brain_restore_verified",
    "activation_authorized",
    "safe_to_erase",
}


class HeldDataTreeCaptureError(ValueError):
    """Held Data identity, policy, or relation validation failed closed."""


def _fd_identity(fd: int) -> dict[str, int]:
    st = os.fstat(fd)
    return {
        "device": st.st_dev,
        "inode": st.st_ino,
        "mode": st.st_mode,
        "owner": st.st_uid,
        "link_count": st.st_nlink,
    }


def _read_policy_pin(policy_path: Path, expected_sha256: str) -> tuple[dict[str, Any], str]:
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise HeldDataTreeCaptureError("policy_sha256_invalid")
    try:
        fd = os.open(policy_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise HeldDataTreeCaptureError("policy_open_failed") from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
        ):
            raise HeldDataTreeCaptureError("policy_file_identity_invalid")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(fd, min(64 * 1024, v3.MAX_PAGE_BYTES + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > v3.MAX_PAGE_BYTES:
                raise HeldDataTreeCaptureError("policy_size_limit")
        raw = b"".join(chunks)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise HeldDataTreeCaptureError("policy_invalid")
        policy = v3._validate_policy(value)
        if canonical_json(policy) != raw:
            raise HeldDataTreeCaptureError("policy_noncanonical")
        digest = sha256(raw)
        after = os.fstat(fd)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or digest != expected_sha256
            or total != before.st_size
        ):
            raise HeldDataTreeCaptureError("policy_pin_or_identity_mismatch")
        return policy, digest
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, v3.TreeCustodyV3Error) as exc:
        raise HeldDataTreeCaptureError("policy_pin_or_content_invalid") from exc
    finally:
        os.close(fd)


def capture_held_data_root_v3(
    view: HeldDataT,
    writer: DescriptorWriter,
    *,
    role: str,
    policy_path: Path,
    expected_plan_sha256: str,
    expected_policy_sha256: str,
    storage_guard: Any,
    scratch_root_relative: str,
    scratch_budget_bytes: int,
    capture_id: str | None = None,
    reserve_capacity: Any = None,
) -> dict[str, Any]:
    """Capture one declared directory from a held Data FD and bind its relation."""
    if not isinstance(view, HeldDataT) or view.synthetic:
        raise HeldDataTreeCaptureError("real_held_data_view_required")
    if approved_data_plan_sha256(view.plan) != expected_plan_sha256:
        raise HeldDataTreeCaptureError("plan_pin_does_not_match_supplied_plan")
    if writer.storage_guard is not storage_guard:
        raise HeldDataTreeCaptureError("pinned_kingston_guard_required")
    if storage_guard is None:
        raise HeldDataTreeCaptureError("pinned_kingston_guard_required")
    try:
        writer.assert_storage_binding(storage_guard)
    except Exception as exc:
        raise HeldDataTreeCaptureError("pinned_kingston_binding_invalid") from exc
    try:
        declaration = view.plan.root_roster[role]
    except KeyError as exc:
        raise HeldDataTreeCaptureError("unknown_root_role") from exc
    if (
        not declaration.present
        or declaration.kind != "directory"
        or not declaration.relative_locator
    ):
        raise HeldDataTreeCaptureError("present_directory_role_required")
    _policy, observed_policy_sha256 = _read_policy_pin(policy_path, expected_policy_sha256)
    if (
        not isinstance(scratch_root_relative, str)
        or not scratch_root_relative
        or scratch_root_relative.startswith("/")
        or ".." in PurePosixPath(scratch_root_relative).parts
    ):
        raise HeldDataTreeCaptureError("scratch_relative_locator_invalid")
    if type(scratch_budget_bytes) is not int or scratch_budget_bytes <= 0:
        raise HeldDataTreeCaptureError("scratch_budget_invalid")

    def reserve_storage(required_bytes: int) -> None:
        storage_guard.revalidate(min_free_bytes=required_bytes + scratch_budget_bytes)
        if reserve_capacity is not None:
            reserve_capacity(required_bytes)

    view.revalidate()
    source_display = view.mountpoint.joinpath(*PurePosixPath(declaration.relative_locator).parts)
    root_result = view.result()["root_locators"][role]
    if root_result["locator"] != declaration.relative_locator:
        raise HeldDataTreeCaptureError("held_root_locator_mismatch")
    with view.borrow_root(role) as source_fd:
        source_identity = _fd_identity(source_fd)
        if source_identity["device"] != os.fstat(view.mount_fd).st_dev:
            raise HeldDataTreeCaptureError("held_root_device_mismatch")
        tree_root = v2.TreeRoot(
            name=role,
            authority_id=view.plan.approval_id,
            recovery_point_id="caller_asserted",
            path=source_display,
            scope="non_protected",
        )
        result = v3.capture_tree(
            writer,
            tree_root,
            policy_path,
            capture_id=capture_id,
            reserve_capacity=reserve_storage,
            revalidate_before_publish=lambda: _revalidate_bound_state(view, writer, storage_guard),
            source_root_fd=source_fd,
            source_display_path=source_display,
            scratch_root_relative=scratch_root_relative,
            scratch_budget_bytes=scratch_budget_bytes,
            revalidate_source_chunk=view.revalidate_mount_fast,
            expected_policy_sha256=observed_policy_sha256,
        )
    view.revalidate()
    writer.assert_storage_binding(storage_guard)
    relation = {
        "schema": RELATION_SCHEMA,
        "capture_id": result["capture_id"],
        "source_plan_sha256": expected_plan_sha256,
        "source_data_identity": view.result()["data"],
        "source_role": role,
        "source_relative_locator": declaration.relative_locator,
        "source_root_identity": source_identity,
        "policy_sha256": observed_policy_sha256,
        "v3_manifest_sha256": result["manifest_sha256"],
        "v3_recovery_point_source": "caller_asserted",
        "source_view_observed": True,
        "acceptance_status": "candidate_until_external_relation_pin",
        "root_unit_policy_complete": result["full_recovery_ready"],
        "root_unit_policy_scope": "v3_path_exclusion_policy_only",
        "metadata_restore_qualified": False,
        "unresolved_exclusion_count": result["unresolved_exclusion_count"],
        "external_custody_required_count": result["external_custody_required_count"],
        "T_qualified": False,
        "kingston_K_frontier_qualified": False,
        "required_root_closure_complete": False,
        "brain_restore_verified": False,
        "activation_authorized": False,
        "safe_to_erase": False,
    }
    relation_bytes = canonical_json(relation)
    writer.mkdir(RELATION_BASE)
    relation_path = f"{RELATION_BASE}/{result['capture_id']}.json"
    writer.create(relation_path, relation_bytes)
    if writer.read_bytes(relation_path, max_bytes=MAX_RELATION_BYTES) != relation_bytes:
        raise HeldDataTreeCaptureError("relation_readback_mismatch")
    verified = verify_held_data_root_relation_v3(
        writer,
        result["capture_id"],
        view.plan,
        storage_guard=storage_guard,
        expected_plan_sha256=expected_plan_sha256,
        expected_policy_sha256=expected_policy_sha256,
        expected_relation_sha256=sha256(relation_bytes),
    )
    return {**verified, **result, "relation_sha256": sha256(relation_bytes)}


def _revalidate_bound_state(view: HeldDataT, writer: DescriptorWriter, storage_guard: Any) -> None:
    view.revalidate()
    writer.assert_storage_binding(storage_guard)


def verify_held_data_root_relation_v3(
    writer: DescriptorWriter,
    capture_id: str,
    plan: ApprovedDataPlan,
    *,
    storage_guard: Any,
    expected_plan_sha256: str,
    expected_policy_sha256: str,
    expected_relation_sha256: str,
) -> dict[str, Any]:
    if approved_data_plan_sha256(plan) != expected_plan_sha256:
        raise HeldDataTreeCaptureError("plan_pin_does_not_match_supplied_plan")
    if writer.storage_guard is not storage_guard:
        raise HeldDataTreeCaptureError("pinned_kingston_guard_required")
    try:
        writer.assert_storage_binding(storage_guard)
    except Exception as exc:
        raise HeldDataTreeCaptureError("pinned_kingston_binding_invalid") from exc
    for value, error in (
        (expected_policy_sha256, "policy_sha256_invalid"),
        (expected_relation_sha256, "relation_sha256_invalid"),
    ):
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise HeldDataTreeCaptureError(error)
    path = f"{RELATION_BASE}/{capture_id}.json"
    try:
        relation_bytes = writer.read_bytes(path, max_bytes=MAX_RELATION_BYTES)
        relation = json.loads(relation_bytes)
    except (OSError, ValueError) as exc:
        raise HeldDataTreeCaptureError("relation_read_failed") from exc
    if (
        not isinstance(relation, dict)
        or set(relation) != RELATION_FIELDS
        or canonical_json(relation) != relation_bytes
        or sha256(relation_bytes) != expected_relation_sha256
        or relation.get("schema") != RELATION_SCHEMA
        or relation.get("capture_id") != capture_id
        or relation.get("source_plan_sha256") != expected_plan_sha256
        or relation.get("policy_sha256") != expected_policy_sha256
        or relation.get("T_qualified") is not False
        or relation.get("kingston_K_frontier_qualified") is not False
        or relation.get("required_root_closure_complete") is not False
        or relation.get("brain_restore_verified") is not False
        or relation.get("activation_authorized") is not False
        or relation.get("safe_to_erase") is not False
        or relation.get("source_view_observed") is not True
        or relation.get("acceptance_status") != "candidate_until_external_relation_pin"
        or relation.get("root_unit_policy_scope") != "v3_path_exclusion_policy_only"
        or relation.get("metadata_restore_qualified") is not False
        or type(relation.get("root_unit_policy_complete")) is not bool
        or type(relation.get("unresolved_exclusion_count")) is not int
        or relation.get("unresolved_exclusion_count", -1) < 0
        or type(relation.get("external_custody_required_count")) is not int
        or relation.get("external_custody_required_count", -1) < 0
    ):
        raise HeldDataTreeCaptureError("relation_binding_invalid")
    source_data = relation.get("source_data_identity")
    role = relation.get("source_role")
    declaration = plan.root_roster.get(role) if isinstance(role, str) else None
    if (
        not isinstance(source_data, dict)
        or source_data.get("data_volume_uuid") != plan.data_volume_uuid.lower()
        or source_data.get("data_device_identifier") != plan.data_device_identifier
        or source_data.get("data_container_reference") != plan.data_container_reference
        or source_data.get("data_volume_group_id") != plan.data_volume_group_id.lower()
        or source_data.get("mounted_fs_type") != "apfs"
        or source_data.get("readonly") is not True
        or source_data.get("source") != f"/dev/{plan.data_device_identifier}"
        or not isinstance(source_data.get("mounted_fsid"), list)
        or not source_data.get("mounted_fsid")
        or not isinstance(source_data.get("mountpoint"), str)
        or not source_data.get("mountpoint", "").startswith("/")
        or declaration is None
        or not declaration.present
        or declaration.kind != "directory"
        or relation.get("source_relative_locator") != declaration.relative_locator
        or relation.get("v3_recovery_point_source") != "caller_asserted"
    ):
        raise HeldDataTreeCaptureError("relation_source_identity_mismatch")
    root_identity = relation.get("source_root_identity")
    if (
        not isinstance(root_identity, dict)
        or set(root_identity) != {"device", "inode", "mode", "owner", "link_count"}
        or any(
            type(root_identity[field]) is not int or root_identity[field] < 0
            for field in root_identity
        )
        or not stat.S_ISDIR(root_identity["mode"])
        or root_identity["link_count"] < 1
    ):
        raise HeldDataTreeCaptureError("relation_root_identity_invalid")
    v3_result = v3.verify_tree(writer, capture_id, expected_policy_sha256=expected_policy_sha256)
    if (
        v3_result.get("manifest_sha256") != relation.get("v3_manifest_sha256")
        or v3_result.get("full_recovery_ready") != relation.get("root_unit_policy_complete")
        or v3_result.get("unresolved_exclusion_count") != relation.get("unresolved_exclusion_count")
        or v3_result.get("external_custody_required_count")
        != relation.get("external_custody_required_count")
    ):
        raise HeldDataTreeCaptureError("relation_manifest_binding_mismatch")
    return {
        "schema": RELATION_SCHEMA,
        "verified": True,
        "capture_id": capture_id,
        "relation_sha256": expected_relation_sha256,
        "manifest_sha256": v3_result["manifest_sha256"],
        "source_view_observed": True,
        "relation_external_pin_matches": True,
        "root_unit_policy_complete": relation["root_unit_policy_complete"],
        "root_unit_policy_scope": relation["root_unit_policy_scope"],
        "metadata_restore_qualified": False,
        "T_qualified": False,
        "kingston_K_frontier_qualified": False,
        "required_root_closure_complete": False,
        "brain_restore_verified": False,
        "activation_authorized": False,
        "safe_to_erase": False,
    }
