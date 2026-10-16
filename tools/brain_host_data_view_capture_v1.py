#!/usr/bin/env python3
"""Capture a deliberately narrow set of regular bytes from held Data roots.

This bounded DR scaffold accepts only a real, already-attested HeldDataT and a
descriptor-bound Kingston capture writer. It captures regular-file bytes only;
symlinks, hard links, protected-name candidates, credentials, SQLite families,
special files, and inventories above the existing 100,000-entry limit fail
closed. It omits metadata needed for restoration and does not establish a cutoff,
owner-root closure, restore readiness, Kingston K, or erase authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Sequence
from contextlib import ExitStack
from pathlib import Path, PurePosixPath
from typing import Any

from memecoin_research_storage import (
    StorageError,
    _open_relative_directory,
    writer_guard,
)
from tools import brain_host_recovery_capture as static_capture
from tools.brain_host_capture_writer_v1 import HeldKingstonCapture
from tools.brain_host_held_data_t_cli_v1 import CLIInputError, _load_plan
from tools.brain_host_held_data_t_v1 import (
    ApprovedDataPlan,
    HeldDataError,
    HeldDataT,
    RootDeclaration,
    approved_data_plan_sha256,
)

SCHEMA = "brain-host-held-data-byte-capture-v1"
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024


class DataViewCaptureError(ValueError):
    """A held source view or its bounded custody capture is incomplete/unsafe."""


def _json_no_duplicate_keys(payload: bytes) -> dict[str, Any]:
    def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in rows:
            if key in result:
                raise DataViewCaptureError("control_json_duplicate_key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise DataViewCaptureError("control_json_nonstandard_constant")

    try:
        value = json.loads(payload, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise DataViewCaptureError("control_json_invalid") from exc
    if not isinstance(value, dict):
        raise DataViewCaptureError("control_json_not_object")
    return value


def _verify_manifest_pin(manifest_bytes: bytes, expected_sha256: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise DataViewCaptureError("manifest_sha256_invalid")
    observed = hashlib.sha256(manifest_bytes).hexdigest()
    if observed != expected_sha256:
        raise DataViewCaptureError("manifest_pin_mismatch")
    return observed


def _require_exact_member_set(actual_names: set[str], expected_objects: set[str]) -> None:
    if actual_names != expected_objects | {"manifest.json", "COMPLETE"}:
        raise DataViewCaptureError("capture_membership_not_closed")


def _validate_capture_path_binding(
    held_directory: os.stat_result,
    reopened_fd: int,
    expected_objects: set[str],
) -> None:
    reopened = os.fstat(reopened_fd)
    if (
        reopened.st_dev != held_directory.st_dev
        or reopened.st_ino != held_directory.st_ino
        or reopened.st_uid != os.getuid()
        or stat.S_IMODE(reopened.st_mode) != 0o700
    ):
        raise DataViewCaptureError("capture_path_changed_during_verify")
    _require_exact_member_set(set(os.listdir(reopened_fd)), expected_objects)


def _validate_control_pair(
    manifest: dict[str, Any],
    complete: dict[str, Any],
    *,
    capture_id: str,
    expected_plan_sha256: str,
    manifest_sha256: str,
) -> set[str]:
    manifest_fields = {
        "schema",
        "capture_id",
        "source_plan_sha256",
        "source_volume_uuid",
        "source_device",
        "source_device_number",
        "source_view_identity",
        "root_roster_complete",
        "unresolved_required_roles",
        "firmlink_provenance_complete",
        "roots",
        "entry_count",
        "total_source_bytes",
        "xattrs_acl_flags",
        "symlinks_hardlinks_special_files",
        "sqlite_family",
        "source_cutoff_verified",
        "T_qualified",
        "kingston_K_frontier_qualified",
        "brain_restore_verified",
        "activation_authorized",
        "safe_to_erase",
    }
    complete_fields = {
        "schema",
        "capture_id",
        "manifest_sha256",
        "entry_count",
        "selected_present_root_group_complete",
        "required_root_closure_complete",
        "complete_marker_scope",
        "T_qualified",
        "kingston_K_frontier_qualified",
        "brain_restore_verified",
        "activation_authorized",
        "safe_to_erase",
    }
    if set(manifest) != manifest_fields or manifest.get("schema") != SCHEMA:
        raise DataViewCaptureError("manifest_schema_invalid")
    if (
        set(complete) != complete_fields
        or complete.get("schema") != "brain-host-held-data-byte-capture-complete-v1"
    ):
        raise DataViewCaptureError("complete_schema_invalid")
    if (
        manifest.get("capture_id") != capture_id
        or complete.get("capture_id") != capture_id
        or manifest.get("source_plan_sha256") != expected_plan_sha256
        or complete.get("manifest_sha256") != manifest_sha256
    ):
        raise DataViewCaptureError("capture_control_binding_mismatch")
    for row in (manifest, complete):
        for field in (
            "T_qualified",
            "kingston_K_frontier_qualified",
            "brain_restore_verified",
            "activation_authorized",
            "safe_to_erase",
        ):
            if row.get(field) is not False:
                raise DataViewCaptureError("capture_control_authority_flag_invalid")
    if (
        type(manifest.get("entry_count")) is not int
        or manifest["entry_count"] < 0
        or type(manifest.get("total_source_bytes")) is not int
        or manifest["total_source_bytes"] < 0
        or type(manifest.get("root_roster_complete")) is not bool
        or type(complete.get("required_root_closure_complete")) is not bool
        or complete.get("selected_present_root_group_complete") is not True
        or complete.get("entry_count") != manifest["entry_count"]
        or complete.get("required_root_closure_complete") != manifest["root_roster_complete"]
        or complete.get("complete_marker_scope") != "selected_present_root_bytes_only"
        or manifest.get("firmlink_provenance_complete") is not False
        or manifest.get("xattrs_acl_flags") != "not_captured"
        or manifest.get("symlinks_hardlinks_special_files") != "rejected"
        or manifest.get("sqlite_family") != "rejected_requires_online_backup_receipt"
        or manifest.get("source_cutoff_verified") is not False
    ):
        raise DataViewCaptureError("capture_control_claim_invalid")
    roots = manifest.get("roots")
    if not isinstance(roots, list) or not roots:
        raise DataViewCaptureError("manifest_roots_invalid")
    if (
        not isinstance(manifest.get("source_volume_uuid"), str)
        or not re.fullmatch(
            r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}",
            manifest["source_volume_uuid"],
        )
        or not isinstance(manifest.get("source_device"), str)
        or not re.fullmatch(r"disk[0-9]+s[0-9]+(?:s[0-9]+)*", manifest["source_device"])
        or type(manifest.get("source_device_number")) is not int
        or manifest["source_device_number"] < 0
        or not isinstance(manifest.get("source_view_identity"), dict)
        or not isinstance(manifest.get("unresolved_required_roles"), dict)
        or manifest["root_roster_complete"] != (not bool(manifest["unresolved_required_roles"]))
    ):
        raise DataViewCaptureError("manifest_source_identity_invalid")
    expected_objects: set[str] = set()
    entry_count = 0
    total_bytes = 0
    roles: set[str] = set()
    for root in roots:
        if (
            not isinstance(root, dict)
            or not isinstance(root.get("role"), str)
            or root["role"] in roles
            or not isinstance(root.get("source_locator"), str)
            or root.get("source_kind") not in {"directory", "file"}
            or root.get("firmlink_provenance") != "unknown_if_material"
            or root.get("metadata_restore_ready") is not False
            or not isinstance(root.get("entries"), list)
        ):
            raise DataViewCaptureError("manifest_root_invalid")
        roles.add(root["role"])
        entries = root["entries"]
        entry_count += len(entries)
        for entry in entries:
            if not isinstance(entry, dict):
                raise DataViewCaptureError("manifest_entry_invalid")
            if not isinstance(entry.get("path"), str):
                raise DataViewCaptureError("manifest_entry_path_invalid")
            if entry.get("kind") != "file":
                continue
            name = entry.get("object")
            size = entry.get("size_bytes")
            digest = entry.get("sha256")
            if (
                not isinstance(name, str)
                or not re.fullmatch(r"object-[0-9]{8}\.bin", name)
                or name in expected_objects
                or type(size) is not int
                or size < 0
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise DataViewCaptureError("manifest_object_reference_invalid")
            expected_objects.add(name)
            total_bytes += size
    if entry_count != manifest["entry_count"] or total_bytes != manifest["total_source_bytes"]:
        raise DataViewCaptureError("manifest_count_or_size_mismatch")
    return expected_objects


def _hash_capture_member(parent_fd: int, name: str, expected: dict[str, Any]) -> None:
    try:
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise DataViewCaptureError("capture_member_open_failed") from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_dev != expected["device"]
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
        ):
            raise DataViewCaptureError("capture_member_identity_invalid")
        digest = hashlib.sha256()
        total = 0
        while True:
            try:
                chunk = os.read(fd, CHUNK_BYTES)
            except InterruptedError:
                continue
            if not chunk:
                break
            total += len(chunk)
            if total > expected["size_bytes"]:
                raise DataViewCaptureError("capture_member_size_exceeds_manifest")
            digest.update(chunk)
        after = os.fstat(fd)
        if _identity(before) != _identity(after):
            raise DataViewCaptureError("capture_member_changed_during_verify")
        if total != expected["size_bytes"] or digest.hexdigest() != expected["sha256"]:
            raise DataViewCaptureError("capture_member_hash_mismatch")
    finally:
        os.close(fd)


def verify_published_capture(
    writer_guard: Any,
    capture_id: str,
    plan: ApprovedDataPlan,
    *,
    expected_plan_sha256: str,
    expected_manifest_sha256: str,
    min_free_bytes: int = 50 * 1024**3,
) -> dict[str, Any]:
    """Verify a candidate group's integrity against externally pinned control hashes."""
    if not re.fullmatch(r"[0-9a-f]{32}", capture_id):
        raise DataViewCaptureError("capture_id_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_plan_sha256):
        raise DataViewCaptureError("plan_sha256_invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256):
        raise DataViewCaptureError("manifest_sha256_invalid")
    if approved_data_plan_sha256(plan) != expected_plan_sha256:
        raise DataViewCaptureError("plan_pin_does_not_match_supplied_plan")
    writer_guard.revalidate(min_free_bytes=min_free_bytes)
    relative = "/".join(
        (writer_guard.config.data_root, "host-recovery", "data-view-captures", capture_id)
    )
    try:
        capture_fd = _open_relative_directory(writer_guard.mount_fd, relative)
    except (OSError, StorageError) as exc:
        raise DataViewCaptureError("capture_directory_open_failed") from exc
    try:
        capture_st = os.fstat(capture_fd)
        device = capture_st.st_dev
        if (
            capture_st.st_uid != os.getuid()
            or stat.S_IMODE(capture_st.st_mode) != 0o700
            or device != writer_guard.root.mount_device
        ):
            raise DataViewCaptureError("capture_directory_identity_invalid")
        # Read control documents through strict private-file checks before accepting
        # any names or hashes from their contents.
        manifest_bytes = _read_capture_control(
            capture_fd, "manifest.json", MAX_MANIFEST_BYTES, device
        )
        observed_manifest_sha256 = _verify_manifest_pin(manifest_bytes, expected_manifest_sha256)
        complete_bytes = _read_capture_control(capture_fd, "COMPLETE", CHUNK_BYTES, device)
        manifest = _json_no_duplicate_keys(manifest_bytes)
        complete = _json_no_duplicate_keys(complete_bytes)
        expected_objects = _validate_control_pair(
            manifest,
            complete,
            capture_id=capture_id,
            expected_plan_sha256=expected_plan_sha256,
            manifest_sha256=observed_manifest_sha256,
        )
        view_identity = manifest["source_view_identity"]
        if (
            view_identity.get("data_volume_uuid") != plan.data_volume_uuid.lower()
            or view_identity.get("data_device_identifier") != plan.data_device_identifier
            or view_identity.get("data_container_reference") != plan.data_container_reference
            or view_identity.get("data_volume_group_id") != plan.data_volume_group_id.lower()
            or view_identity.get("mounted_fs_type") != "apfs"
            or view_identity.get("source") != f"/dev/{plan.data_device_identifier}"
            or view_identity.get("readonly") is not True
            or not isinstance(view_identity.get("mountpoint"), str)
            or not isinstance(view_identity.get("mounted_fsid"), list)
            or len(view_identity["mounted_fsid"]) != 2
            or any(type(part) is not int for part in view_identity["mounted_fsid"])
            or not isinstance(view_identity.get("readonly_alias_mountpoints"), list)
            or any(
                not isinstance(alias, str) for alias in view_identity["readonly_alias_mountpoints"]
            )
        ):
            raise DataViewCaptureError("manifest_source_view_identity_does_not_match_plan")
        expected_roles = {
            role for role, declaration in plan.root_roster.items() if declaration.present
        }
        roots_by_role = {row["role"]: row for row in manifest["roots"]}
        expected_unresolved = {
            role: {"state": "absent_unresolved", "reason": declaration.unresolved_reason}
            for role, declaration in plan.root_roster.items()
            if not declaration.present
        }
        if (
            set(roots_by_role) != expected_roles
            or manifest["source_volume_uuid"] != plan.data_volume_uuid.lower()
            or manifest["source_device"] != plan.data_device_identifier
            or manifest["unresolved_required_roles"] != expected_unresolved
        ):
            raise DataViewCaptureError("manifest_does_not_match_approved_plan")
        for role, declaration in plan.root_roster.items():
            if not declaration.present:
                continue
            root_row = roots_by_role[role]
            if (
                root_row["source_locator"] != declaration.relative_locator
                or root_row["source_kind"] != declaration.kind
            ):
                raise DataViewCaptureError("manifest_root_does_not_match_approved_plan")
        rows = {
            entry["object"]: entry
            for root in manifest["roots"]
            for entry in root["entries"]
            if entry.get("kind") == "file"
        }
        _require_exact_member_set(set(os.listdir(capture_fd)), expected_objects)
        expected_rows = {
            name: {"device": device, "size_bytes": row["size_bytes"], "sha256": row["sha256"]}
            for name, row in rows.items()
        }
        expected_rows["manifest.json"] = {
            "device": device,
            "size_bytes": len(manifest_bytes),
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        }
        expected_rows["COMPLETE"] = {
            "device": device,
            "size_bytes": len(complete_bytes),
            "sha256": hashlib.sha256(complete_bytes).hexdigest(),
        }
        for name, row in expected_rows.items():
            writer_guard.revalidate_identity(min_free_bytes=min_free_bytes)
            _hash_capture_member(capture_fd, name, row)
        writer_guard.revalidate(min_free_bytes=min_free_bytes)
        try:
            current_path_fd = _open_relative_directory(writer_guard.mount_fd, relative)
        except (OSError, StorageError) as exc:
            raise DataViewCaptureError("capture_path_changed_during_verify") from exc
        try:
            _validate_capture_path_binding(capture_st, current_path_fd, expected_objects)
        finally:
            os.close(current_path_fd)
        writer_guard.revalidate(min_free_bytes=min_free_bytes)
        return {
            "schema": "brain-host-held-data-byte-capture-verification-v1",
            "verified": True,
            "capture_id": capture_id,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "member_count": len(expected_rows),
            "entry_count": manifest["entry_count"],
            "total_source_bytes": manifest["total_source_bytes"],
            "required_root_closure_complete": manifest["root_roster_complete"],
            "T_qualified": False,
            "kingston_K_frontier_qualified": False,
            "brain_restore_verified": False,
            "safe_to_erase": False,
        }
    finally:
        os.close(capture_fd)


def _read_capture_control(parent_fd: int, name: str, limit: int, device: int) -> bytes:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    except OSError as exc:
        raise DataViewCaptureError("capture_control_open_failed") from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_dev != device
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size > limit
        ):
            raise DataViewCaptureError("capture_control_identity_invalid")
        chunks: list[bytes] = []
        size = 0
        while True:
            try:
                chunk = os.read(fd, min(CHUNK_BYTES, limit - size + 1))
            except InterruptedError:
                continue
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise DataViewCaptureError("capture_control_too_large")
            chunks.append(chunk)
        after = os.fstat(fd)
        if _identity(before) != _identity(after) or size != before.st_size:
            raise DataViewCaptureError("capture_control_changed_during_read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _identity(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_uid,
        st.st_gid,
        st.st_nlink,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


def _stream_file(
    view: HeldDataT,
    held: HeldKingstonCapture,
    source_fd: int,
    name: str,
    expected: tuple[int, ...],
) -> dict[str, Any]:
    before = os.fstat(source_fd)
    if _identity(before) != expected or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise DataViewCaptureError("source_file_identity_mismatch")
    sink = held.create_object(name)
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            view.revalidate_mount_fast()
            try:
                chunk = os.read(source_fd, CHUNK_BYTES)
            except InterruptedError:
                continue
            if not chunk:
                break
            total += len(chunk)
            if total > before.st_size or total > static_capture.MAX_TOTAL_BYTES:
                raise DataViewCaptureError("source_file_grew_during_capture")
            sink.write(chunk)
            digest.update(chunk)
        final_hash = digest.hexdigest()
        if total != before.st_size or _identity(os.fstat(source_fd)) != expected:
            raise DataViewCaptureError("source_file_changed_during_capture")
        verified = sink.finalize(expected_size=total, expected_sha256=final_hash)
    except BaseException:
        sink._close_unfinished()
        held._active = None
        raise
    return {
        "object": verified.name,
        "size_bytes": verified.size_bytes,
        "sha256": verified.sha256,
        "source_device": before.st_dev,
        "source_inode": before.st_ino,
        "mode": stat.S_IMODE(before.st_mode),
        "uid": before.st_uid,
        "gid": before.st_gid,
        "mtime_ns": before.st_mtime_ns,
        "ctime_ns": before.st_ctime_ns,
    }


def _capture_root(
    view: HeldDataT,
    writer: HeldKingstonCapture,
    role: str,
    root_fd: int,
    entries: list[static_capture.SourceEntry],
    expected_root_identity: tuple[int, ...],
    object_start: int,
) -> tuple[dict[str, Any], int, int]:
    declaration = view.plan.root_roster.get(role)
    if not isinstance(declaration, RootDeclaration) or declaration.present is not True:
        raise DataViewCaptureError("requested_root_not_declared_present")
    locator = declaration.relative_locator
    if locator is None:
        raise DataViewCaptureError("source_root_locator_missing")
    try:
        for component in PurePosixPath(locator).parts:
            static_capture._valid_component(component)
    except static_capture.RecoveryCaptureError as exc:
        raise DataViewCaptureError("source_root_locator_excluded") from exc
    root_st = os.fstat(root_fd)
    if _identity(root_st) != expected_root_identity:
        raise DataViewCaptureError("source_root_changed_after_preflight")

    rows: list[dict[str, Any]] = []
    total_bytes = 0
    for index, entry in enumerate(entries):
        if entry.kind != "file":
            rows.append(entry.manifest())
            continue
        if entry.path:
            source_fd = static_capture._open_source_file(root_fd, entry.path, entry)
        else:
            source_fd = os.dup(root_fd)
            if static_capture._signature(os.fstat(source_fd)) != entry.signature():
                os.close(source_fd)
                raise DataViewCaptureError("source_root_file_changed")
        try:
            payload = _stream_file(
                view,
                writer,
                source_fd,
                f"object-{object_start + index:08d}.bin",
                _identity(os.fstat(source_fd)),
            )
        finally:
            os.close(source_fd)
        payload.update({"path": entry.path, "kind": "file"})
        rows.append(payload)
        total_bytes += payload["size_bytes"]
        if total_bytes > static_capture.MAX_TOTAL_BYTES:
            raise DataViewCaptureError("root_byte_limit")
    return (
        {
            "role": role,
            "source_locator": declaration.relative_locator,
            "source_kind": declaration.kind,
            "source_device": root_st.st_dev,
            "source_inode": root_st.st_ino,
            "source_mode": stat.S_IMODE(root_st.st_mode),
            "entry_count": len(entries),
            "entries": rows,
            "firmlink_provenance": "unknown_if_material",
            "metadata_restore_ready": False,
        },
        len(entries),
        total_bytes,
    )


def _preflight_root(
    view: HeldDataT, role: str, root_fd: int
) -> tuple[tuple[int, ...], list[static_capture.SourceEntry]]:
    declaration = view.plan.root_roster.get(role)
    if not isinstance(declaration, RootDeclaration) or declaration.present is not True:
        raise DataViewCaptureError("requested_root_not_declared_present")
    locator = declaration.relative_locator
    if locator is None:
        raise DataViewCaptureError("source_root_locator_missing")
    try:
        for component in PurePosixPath(locator).parts:
            static_capture._valid_component(component)
    except static_capture.RecoveryCaptureError as exc:
        raise DataViewCaptureError("source_root_locator_excluded") from exc
    root_st = os.fstat(root_fd)
    if root_st.st_dev != os.fstat(view.mount_fd).st_dev:
        raise DataViewCaptureError("root_not_on_held_data_device")
    root_identity = _identity(root_st)
    if declaration.kind == "directory":
        if not stat.S_ISDIR(root_st.st_mode):
            raise DataViewCaptureError("root_kind_changed")
        entries = static_capture._inventory(root_fd, root_st.st_dev)
    elif declaration.kind == "file":
        if (
            not stat.S_ISREG(root_st.st_mode)
            or root_st.st_nlink != 1
            or stat.S_IMODE(root_st.st_mode) & 0o7000
        ):
            raise DataViewCaptureError("root_kind_or_link_count_invalid")
        entries = [
            static_capture.SourceEntry(
                "",
                "file",
                stat.S_IMODE(root_st.st_mode),
                root_st.st_uid,
                root_st.st_gid,
                root_st.st_size,
                root_st.st_mtime_ns,
                root_st.st_dev,
                root_st.st_ino,
                root_st.st_ctime_ns,
            )
        ]
    else:
        raise DataViewCaptureError("root_kind_unsupported")
    return root_identity, entries


def capture_held_data_roots(
    view: HeldDataT,
    writer_guard: Any,
    roles: tuple[str, ...],
    *,
    capture_id: str | None = None,
    min_free_bytes: int = 50 * 1024**3,
) -> dict[str, Any]:
    """Stream supported file bytes from every selected present root.

    `roles` must be the entire set of roots marked present by the externally
    pinned source plan. Every root must fit the strict static inventory policy.
    The resulting candidate marker covers these bytes only; it is not a restorable
    Brain backup or a T/K/erase witness.
    """
    if not isinstance(view, HeldDataT) or view.synthetic or view._closed:
        raise DataViewCaptureError("real_held_data_view_required")
    if not isinstance(roles, tuple) or not roles or len(set(roles)) != len(roles):
        raise DataViewCaptureError("root_role_selection_invalid")
    expected_roles = tuple(
        sorted(role for role, row in view.plan.root_roster.items() if row.present)
    )
    if tuple(sorted(roles)) != expected_roles:
        raise DataViewCaptureError("all_present_source_roles_required")
    held_result = view.result()
    if held_result.get("mock_backend") is not False:
        raise DataViewCaptureError("real_darwin_held_data_backend_required")
    chosen_id = capture_id or secrets.token_hex(16)
    with ExitStack() as stack:
        root_fds = {role: stack.enter_context(view.borrow_root(role)) for role in expected_roles}
        source_device = os.fstat(view.mount_fd).st_dev
        preflight: dict[str, tuple[tuple[int, ...], list[static_capture.SourceEntry]]] = {}
        total_entries = 0
        total_bytes = 0
        for role in expected_roles:
            view.revalidate()
            root_identity, entries = _preflight_root(view, role, root_fds[role])
            preflight[role] = (root_identity, entries)
            total_entries += len(entries)
            total_bytes += sum(entry.size_bytes for entry in entries if entry.kind == "file")
            if total_entries > static_capture.MAX_ENTRIES:
                raise DataViewCaptureError("source_inventory_limit")
            if total_bytes > static_capture.MAX_TOTAL_BYTES:
                raise DataViewCaptureError("source_inventory_byte_limit")
        view.revalidate()
        try:
            with HeldKingstonCapture(
                writer_guard,
                chosen_id,
                source_volume_uuid=view.plan.data_volume_uuid,
                source_device=source_device,
                min_free_bytes=min_free_bytes,
            ) as writer:
                roots: list[dict[str, Any]] = []
                object_start = 0
                for role in expected_roles:
                    view.revalidate()
                    root_identity, entries = preflight[role]
                    root, count, size = _capture_root(
                        view,
                        writer,
                        role,
                        root_fds[role],
                        entries,
                        root_identity,
                        object_start,
                    )
                    roots.append(root)
                    object_start += count
                    if size != sum(entry.size_bytes for entry in entries if entry.kind == "file"):
                        raise DataViewCaptureError("captured_root_size_invalid")
                view.revalidate()
                # Re-inventory each root while its held descriptor remains live.
                for role in expected_roles:
                    declaration = view.plan.root_roster[role]
                    root_fd = root_fds[role]
                    expected_root_identity, expected_entries = preflight[role]
                    if _identity(os.fstat(root_fd)) != expected_root_identity:
                        raise DataViewCaptureError("source_root_changed_after_capture")
                    if declaration.kind == "directory":
                        current = static_capture._inventory(root_fd, os.fstat(root_fd).st_dev)
                        if [entry.signature() for entry in current] != [
                            entry.signature() for entry in expected_entries
                        ]:
                            raise DataViewCaptureError("source_membership_changed_after_capture")
                    elif declaration.kind != "file":
                        raise DataViewCaptureError("root_kind_changed_after_capture")
                view.revalidate()
                manifest = {
                    "schema": SCHEMA,
                    "capture_id": chosen_id,
                    "source_plan_sha256": view.trusted_plan_sha256,
                    "source_volume_uuid": view.plan.data_volume_uuid.lower(),
                    "source_device": view.plan.data_device_identifier,
                    "source_device_number": source_device,
                    "source_view_identity": held_result["data"],
                    "root_roster_complete": held_result["required_role_closure"],
                    "unresolved_required_roles": held_result["unresolved_required_roles"],
                    "firmlink_provenance_complete": False,
                    "roots": roots,
                    "entry_count": total_entries,
                    "total_source_bytes": total_bytes,
                    "xattrs_acl_flags": "not_captured",
                    "symlinks_hardlinks_special_files": "rejected",
                    "sqlite_family": "rejected_requires_online_backup_receipt",
                    "source_cutoff_verified": False,
                    "T_qualified": False,
                    "kingston_K_frontier_qualified": False,
                    "brain_restore_verified": False,
                    "activation_authorized": False,
                    "safe_to_erase": False,
                }
                manifest_bytes = json.dumps(
                    manifest, sort_keys=True, separators=(",", ":"), allow_nan=False
                ).encode("utf-8")
                if len(manifest_bytes) > MAX_MANIFEST_BYTES:
                    raise DataViewCaptureError("manifest_size_limit")
                with writer.create_object("manifest.json") as sink:
                    for offset in range(0, len(manifest_bytes), CHUNK_BYTES):
                        sink.write(manifest_bytes[offset : offset + CHUNK_BYTES])
                    manifest_object = sink.finalize(
                        expected_size=len(manifest_bytes),
                        expected_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                    )
                complete = {
                    "schema": "brain-host-held-data-byte-capture-complete-v1",
                    "capture_id": chosen_id,
                    "manifest_sha256": manifest_object.sha256,
                    "entry_count": total_entries,
                    "selected_present_root_group_complete": True,
                    "required_root_closure_complete": held_result["required_role_closure"],
                    "complete_marker_scope": "selected_present_root_bytes_only",
                    "T_qualified": False,
                    "kingston_K_frontier_qualified": False,
                    "brain_restore_verified": False,
                    "activation_authorized": False,
                    "safe_to_erase": False,
                }
                complete_bytes = (
                    json.dumps(complete, sort_keys=True, separators=(",", ":")) + "\n"
                ).encode("ascii")
                expected_objects = _validate_control_pair(
                    manifest,
                    complete,
                    capture_id=chosen_id,
                    expected_plan_sha256=view.trusted_plan_sha256,
                    manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                )
                if set(writer._verified) != expected_objects | {"manifest.json"}:
                    raise DataViewCaptureError("manifest_payload_membership_mismatch")
                writer.publish_complete(complete_bytes)
                verification = verify_published_capture(
                    writer_guard,
                    chosen_id,
                    view.plan,
                    expected_plan_sha256=view.trusted_plan_sha256,
                    expected_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                    min_free_bytes=min_free_bytes,
                )
                view.revalidate()
                return {
                    **complete,
                    "total_source_bytes": total_bytes,
                    "manifest_sha256": verification["manifest_sha256"],
                    "readback_verified": verification["verified"],
                }
        except (OSError, ValueError) as exc:
            if isinstance(exc, DataViewCaptureError):
                raise
            raise DataViewCaptureError("held_data_capture_failed") from exc


def main(argv: Sequence[str] | None = None) -> int:
    """Operator entry point for a pre-approved, already-mounted read-only Data view."""
    parser = argparse.ArgumentParser(
        description=(
            "Capture a restricted regular-file byte subset from all present approved Data roots. "
            "Protected-like paths, links, SQLite families, special files, and inventories above "
            "100,000 entries fail closed. Metadata restore, cutoff, T, K, and erase readiness "
            "are not established."
        ),
        epilog=(
            "Prerequisites: a separately qualified source-view/mount-history decision, an "
            "already-mounted read-only APFS Data volume, an externally pinned plan, and a "
            "valid Kingston storage binding. This command never unlocks/mounts source or "
            "starts Brain services. COMPLETE is only a candidate seal; acceptance requires a "
            "fresh independent verifier pass. An error after marker publication can leave an "
            "unaccepted candidate directory on Kingston."
        ),
    )
    parser.add_argument("--plan", help="externally approved Data root plan JSON (capture mode)")
    parser.add_argument("--plan-sha256", required=True, help="independent 64-digit plan pin")
    parser.add_argument(
        "--manifest-sha256", help="independent manifest pin (required in verify mode)"
    )
    parser.add_argument("--mountpoint", help="already-mounted Data path (capture mode)")
    parser.add_argument("--storage-config", required=True, help="private Kingston binding JSON")
    parser.add_argument("--capture-id", help="optional new 32-digit lowercase hex identifier")
    parser.add_argument(
        "--verify-capture-id", help="verify an existing candidate group without source access"
    )
    try:
        args = parser.parse_args(argv)
        if len(args.plan_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in args.plan_sha256
        ):
            raise DataViewCaptureError("plan_sha256_invalid")
        if args.verify_capture_id is not None:
            if (
                args.plan is None
                or args.mountpoint is not None
                or args.capture_id is not None
                or args.manifest_sha256 is None
            ):
                raise DataViewCaptureError("verify_mode_arguments_conflict")
            if len(args.manifest_sha256) != 64 or any(
                char not in "0123456789abcdef" for char in args.manifest_sha256
            ):
                raise DataViewCaptureError("manifest_sha256_invalid")
            plan = _load_plan(Path(args.plan))
            with writer_guard(Path(args.storage_config).expanduser()) as guard:
                result = verify_published_capture(
                    guard,
                    args.verify_capture_id,
                    plan,
                    expected_plan_sha256=args.plan_sha256,
                    expected_manifest_sha256=args.manifest_sha256,
                )
            print(json.dumps({"ok": True, **result}, sort_keys=True))
            return 0
        if args.plan is None or args.mountpoint is None:
            raise DataViewCaptureError("capture_plan_and_mountpoint_required")
        plan = _load_plan(Path(args.plan))
        with HeldDataT(
            plan,
            Path(args.mountpoint),
            trusted_plan_sha256=args.plan_sha256,
        ) as view:
            roles = tuple(sorted(role for role, row in plan.root_roster.items() if row.present))
            with writer_guard(Path(args.storage_config).expanduser()) as guard:
                result = capture_held_data_roots(
                    view,
                    guard,
                    roles,
                    capture_id=args.capture_id,
                )
        output = {
            "schema": "brain-host-held-data-byte-capture-operator-v1",
            "ok": True,
            "capture_id": result["capture_id"],
            "storage_relative_path": f"host-recovery/data-view-captures/{result['capture_id']}",
            "manifest_sha256": result["manifest_sha256"],
            "readback_verified": result["readback_verified"],
            "entry_count": result["entry_count"],
            "total_source_bytes": result["total_source_bytes"],
            "selected_present_root_group_complete": result["selected_present_root_group_complete"],
            "required_root_closure_complete": result["required_root_closure_complete"],
            "T_qualified": False,
            "kingston_K_frontier_qualified": False,
            "brain_restore_verified": False,
            "activation_authorized": False,
            "safe_to_erase": False,
        }
        print(json.dumps(output, sort_keys=True))
        return 0
    except (CLIInputError, HeldDataError, StorageError, DataViewCaptureError, OSError) as exc:
        error = (
            str(exc)
            if isinstance(exc, (CLIInputError, HeldDataError, DataViewCaptureError))
            else "capture_precondition_failed"
        )
        print(
            json.dumps(
                {
                    "schema": "brain-host-held-data-byte-capture-operator-v1",
                    "ok": False,
                    "error": error,
                    "T_qualified": False,
                    "kingston_K_frontier_qualified": False,
                    "brain_restore_verified": False,
                    "activation_authorized": False,
                    "safe_to_erase": False,
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
