#!/usr/bin/env python3
"""Capture exact plan-bound private files as opaque quarantine candidates.

Only the three declared MCP developer-tool file roles are accepted. Captures
preserve bytes and basic source identity for custody; they do not restore file
metadata, authorize configuration use, or qualify root closure, T, K, or erase.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tools.brain_host_held_data_t_v1 import (
    ApprovedDataPlan,
    HeldDataT,
    approved_data_plan_sha256,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

POLICY_SCHEMA = "brain-host-held-data-file-policy-v1"
MANIFEST_SCHEMA = "brain-host-held-data-file-manifest-v1"
FILE_BASE = "host-recovery/held-data-file-units-v1"
ALLOWED_FILE_ROLES = frozenset({"helius_mcp_wrapper", "codex_mcp_config", "cursor_mcp_config"})
MAX_FILE_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
CAPTURE_CONSISTENCY_LITERAL = "unqualified_opaque_bytes"
POLICY_FIELDS = {
    "schema",
    "version",
    "source_plan_sha256",
    "role",
    "relative_locator",
    "scope",
    "restore_disposition",
    "metadata_disposition",
    "max_file_bytes",
}
MANIFEST_FIELDS = {
    "schema",
    "capture_id",
    "source_plan_sha256",
    "policy_sha256",
    "role",
    "relative_locator",
    "source_data_identity",
    "source_mount_device",
    "source_file_signature",
    "storage_directory_identity",
    "payload_relative_path",
    "payload_bytes",
    "payload_sha256",
    "scope",
    "restore_disposition",
    "metadata_disposition",
    "application_consistency",
}
SOURCE_SIGNATURE_FIELDS = {
    "device",
    "inode",
    "mode",
    "nlink",
    "uid",
    "gid",
    "size",
    "mtime_ns",
    "ctime_ns",
}


class HeldDataFileError(ValueError):
    """A file role, source descriptor, policy, or custody artifact is invalid."""


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _signature(st: os.stat_result) -> dict[str, int]:
    return {
        "device": int(st.st_dev),
        "inode": int(st.st_ino),
        "mode": int(st.st_mode),
        "nlink": int(st.st_nlink),
        "uid": int(st.st_uid),
        "gid": int(st.st_gid),
        "size": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "ctime_ns": int(st.st_ctime_ns),
    }


def _validate_policy(
    policy: object,
    plan: ApprovedDataPlan,
    *,
    expected_plan_sha256: str,
    role: str,
) -> dict[str, Any]:
    if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS:
        raise HeldDataFileError("file_policy_fields_invalid")
    declaration = plan.root_roster.get(role)
    if (
        role not in ALLOWED_FILE_ROLES
        or declaration is None
        or declaration.present is not True
        or declaration.kind != "file"
        or not isinstance(declaration.relative_locator, str)
        or policy.get("schema") != POLICY_SCHEMA
        or type(policy.get("version")) is not int
        or policy.get("version") != 1
        or policy.get("source_plan_sha256") != expected_plan_sha256
        or not _is_digest(expected_plan_sha256)
        or approved_data_plan_sha256(plan) != expected_plan_sha256
        or policy.get("role") != role
        or policy.get("relative_locator") != declaration.relative_locator
        or policy.get("scope") != "private_developer_tool_opaque"
        or policy.get("restore_disposition") != "quarantine_only"
        or policy.get("metadata_disposition") != "omitted_unqualified"
        or type(policy.get("max_file_bytes")) is not int
        or not 0 < policy["max_file_bytes"] <= MAX_FILE_BYTES
    ):
        raise HeldDataFileError("file_policy_plan_binding_invalid")
    return policy


def read_file_policy(
    path: Path,
    *,
    expected_sha256: str,
    plan: ApprovedDataPlan,
    expected_plan_sha256: str,
    role: str,
) -> dict[str, Any]:
    if not _is_digest(expected_sha256):
        raise HeldDataFileError("file_policy_sha256_invalid")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise HeldDataFileError("file_policy_open_failed") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > 1024 * 1024:
            raise HeldDataFileError("file_policy_not_bounded_regular_file")
        raw = bytearray()
        while chunk := os.read(fd, min(65536, 1024 * 1024 + 1 - len(raw))):
            raw.extend(chunk)
            if len(raw) > 1024 * 1024:
                raise HeldDataFileError("file_policy_too_large")
    finally:
        os.close(fd)
    try:
        policy = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise HeldDataFileError("file_policy_json_invalid") from exc
    if canonical_json(policy) != bytes(raw) or sha256(bytes(raw)) != expected_sha256:
        raise HeldDataFileError("file_policy_canonical_hash_mismatch")
    return _validate_policy(policy, plan, expected_plan_sha256=expected_plan_sha256, role=role)


def _source_fd(view: HeldDataT, role: str) -> int:
    try:
        borrowed = os.dup(view.root_fds[role])
    except KeyError as exc:
        raise HeldDataFileError("file_role_not_held") from exc
    st = os.fstat(borrowed)
    mount_dev = os.fstat(view.mount_fd).st_dev
    if (
        not stat.S_ISREG(st.st_mode)
        or st.st_nlink != 1
        or st.st_dev != mount_dev
        or st.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
        or st.st_size < 0
    ):
        os.close(borrowed)
        raise HeldDataFileError("file_source_type_link_device_or_mode_invalid")
    return borrowed


def preflight_held_data_file_roles_v1(
    view: HeldDataT,
    *,
    policy_path_by_role: dict[str, Path],
    expected_policy_sha256_by_role: dict[str, str],
    expected_plan_sha256: str,
) -> dict[str, Any]:
    """Preflight every selected exact file role before any Kingston write."""
    if not isinstance(view, HeldDataT) or view.synthetic:
        raise HeldDataFileError("real_held_data_view_required")
    roles = set(policy_path_by_role)
    if not roles or not roles <= ALLOWED_FILE_ROLES or set(expected_policy_sha256_by_role) != roles:
        raise HeldDataFileError("file_preflight_role_roster_invalid")
    if approved_data_plan_sha256(view.plan) != expected_plan_sha256:
        raise HeldDataFileError("plan_pin_does_not_match_supplied_plan")
    policies: dict[str, dict[str, Any]] = {}
    for role in sorted(roles):
        policies[role] = read_file_policy(
            policy_path_by_role[role],
            expected_sha256=expected_policy_sha256_by_role[role],
            plan=view.plan,
            expected_plan_sha256=expected_plan_sha256,
            role=role,
        )
    view.revalidate()
    identities: dict[str, dict[str, int]] = {}
    for role in sorted(roles):
        source_fd = _source_fd(view, role)
        try:
            st = os.fstat(source_fd)
            if st.st_size > policies[role]["max_file_bytes"]:
                raise HeldDataFileError("file_source_exceeds_policy_limit")
            identities[role] = _signature(st)
        finally:
            os.close(source_fd)
    view.revalidate()
    return {
        "source_plan_sha256": expected_plan_sha256,
        "source_data_identity": view.result()["data"],
        "source_mount_device": int(os.fstat(view.mount_fd).st_dev),
        "source_file_signatures": identities,
    }


def capture_held_data_file_unit_v1(
    view: HeldDataT,
    writer: DescriptorWriter,
    *,
    role: str,
    policy_path: Path,
    expected_plan_sha256: str,
    expected_policy_sha256: str,
    storage_guard: Any,
    capture_id: str | None = None,
    expected_source_file_signature: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Stream one exact held file into a private Kingston candidate unit."""
    if not isinstance(view, HeldDataT) or view.synthetic:
        raise HeldDataFileError("real_held_data_view_required")
    if role not in ALLOWED_FILE_ROLES:
        raise HeldDataFileError("file_role_not_supported")
    if approved_data_plan_sha256(view.plan) != expected_plan_sha256:
        raise HeldDataFileError("plan_pin_does_not_match_supplied_plan")
    if writer.storage_guard is not storage_guard or storage_guard is None:
        raise HeldDataFileError("pinned_kingston_guard_required")
    try:
        writer.assert_storage_binding(storage_guard)
    except Exception as exc:
        raise HeldDataFileError("pinned_kingston_binding_invalid") from exc
    policy = read_file_policy(
        policy_path,
        expected_sha256=expected_policy_sha256,
        plan=view.plan,
        expected_plan_sha256=expected_plan_sha256,
        role=role,
    )
    declaration = view.plan.root_roster[role]
    if not declaration.present or declaration.kind != "file":
        raise HeldDataFileError("file_role_plan_declaration_invalid")

    chosen_id = capture_id or secrets.token_hex(16)
    if (
        not isinstance(chosen_id, str)
        or len(chosen_id) != 32
        or any(character not in "0123456789abcdef" for character in chosen_id)
    ):
        raise HeldDataFileError("capture_id_invalid")
    view.revalidate()
    writer.assert_storage_binding(storage_guard)
    source_identity = view.result()["data"]
    source_fd = _source_fd(view, role)
    try:
        before = os.fstat(source_fd)
        signature = _signature(before)
        if (
            expected_source_file_signature is not None
            and signature != expected_source_file_signature
        ):
            raise HeldDataFileError("file_source_changed_after_preflight")
        source_mount_device = int(os.fstat(view.mount_fd).st_dev)
        if before.st_dev != source_mount_device:
            raise HeldDataFileError("file_source_crosses_data_mount_device")
        if before.st_size > policy["max_file_bytes"]:
            raise HeldDataFileError("file_source_exceeds_policy_limit")

        def source_chunks() -> Iterator[bytes]:
            offset = 0
            while offset < before.st_size:
                view.revalidate_mount_fast()
                chunk = os.pread(source_fd, min(CHUNK_BYTES, before.st_size - offset), offset)
                if not chunk:
                    raise HeldDataFileError("file_source_truncated_during_capture")
                offset += len(chunk)
                yield chunk
            if os.pread(source_fd, 1, offset):
                raise HeldDataFileError("file_source_grew_during_capture")

        payload_path = f"{FILE_BASE}/{chosen_id}/payload.bin"
        try:
            payload_bytes, payload_hash = writer.atomic_write_stream(
                payload_path,
                source_chunks(),
                max_bytes=policy["max_file_bytes"],
                create_only=True,
                before_chunk=view.revalidate_mount_fast,
            )
        except Exception as exc:
            raise HeldDataFileError("file_payload_stream_failed_unindexed") from exc
        after = os.fstat(source_fd)
        if _signature(after) != signature or payload_bytes != before.st_size:
            raise HeldDataFileError("file_source_changed_during_capture")
        view.revalidate()
        writer.assert_storage_binding(storage_guard)
        unit_relative = f"{FILE_BASE}/{chosen_id}"
        unit_fd = writer.open_directory(unit_relative)
        try:
            unit_stat = os.fstat(unit_fd)
            storage_directory_identity = {
                "device": int(unit_stat.st_dev),
                "inode": int(unit_stat.st_ino),
            }
        finally:
            os.close(unit_fd)
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "capture_id": chosen_id,
            "source_plan_sha256": expected_plan_sha256,
            "policy_sha256": expected_policy_sha256,
            "role": role,
            "relative_locator": declaration.relative_locator,
            "source_data_identity": source_identity,
            "source_mount_device": source_mount_device,
            "source_file_signature": signature,
            "storage_directory_identity": storage_directory_identity,
            "payload_relative_path": f"{chosen_id}/payload.bin",
            "payload_bytes": payload_bytes,
            "payload_sha256": payload_hash,
            "scope": policy["scope"],
            "restore_disposition": policy["restore_disposition"],
            "metadata_disposition": policy["metadata_disposition"],
            "application_consistency": CAPTURE_CONSISTENCY_LITERAL,
        }
        raw = canonical_json(manifest)
        if set(manifest) != MANIFEST_FIELDS:
            raise HeldDataFileError("file_manifest_internal_fields_invalid")
        writer.atomic_write(f"{FILE_BASE}/{chosen_id}/manifest.json", raw, create_only=True)
        writer.assert_storage_binding(storage_guard)
        if (
            writer.read_bytes(f"{FILE_BASE}/{chosen_id}/manifest.json", max_bytes=1024 * 1024)
            != raw
        ):
            raise HeldDataFileError("file_manifest_readback_mismatch")
        return {
            "schema": MANIFEST_SCHEMA,
            "candidate_published": True,
            "independently_verified": False,
            "capture_id": chosen_id,
            "manifest_sha256": sha256(raw),
            "payload_sha256": payload_hash,
            "payload_bytes": payload_bytes,
            "role": role,
            "restore_disposition": "quarantine_only",
            "T_qualified": False,
            "required_root_closure_complete": False,
            "kingston_K_frontier_qualified": False,
            "brain_restore_verified": False,
            "activation_authorized": False,
            "safe_to_erase": False,
        }
    finally:
        os.close(source_fd)


def verify_held_data_file_unit_v1(
    writer: DescriptorWriter,
    capture_id: str,
    plan: ApprovedDataPlan,
    *,
    role: str,
    policy_path: Path,
    expected_plan_sha256: str,
    expected_policy_sha256: str,
    expected_manifest_sha256: str,
    storage_guard: Any,
) -> dict[str, Any]:
    """Independently verify external pins, manifest membership, and payload hash."""
    if writer.storage_guard is not storage_guard or storage_guard is None:
        raise HeldDataFileError("pinned_kingston_guard_required")
    try:
        writer.assert_storage_binding(storage_guard)
    except Exception as exc:
        raise HeldDataFileError("pinned_kingston_binding_invalid") from exc
    if (
        not isinstance(capture_id, str)
        or len(capture_id) != 32
        or any(character not in "0123456789abcdef" for character in capture_id)
    ):
        raise HeldDataFileError("capture_id_invalid")
    policy = read_file_policy(
        policy_path,
        expected_sha256=expected_policy_sha256,
        plan=plan,
        expected_plan_sha256=expected_plan_sha256,
        role=role,
    )
    unit_relative = f"{FILE_BASE}/{capture_id}"
    try:
        unit_fd = writer.open_directory(unit_relative)
    except Exception as exc:
        raise HeldDataFileError("file_unit_directory_open_failed") from exc
    try:
        unit_stat = os.fstat(unit_fd)
        unit_identity = {"device": int(unit_stat.st_dev), "inode": int(unit_stat.st_ino)}
        expected_names = ("manifest.json", "payload.bin")
        if tuple(sorted(os.listdir(unit_fd))) != expected_names:
            raise HeldDataFileError("file_unit_membership_mismatch")
        raw = _read_unit_member(unit_fd, "manifest.json", max_bytes=1024 * 1024)
        payload = _read_unit_member(unit_fd, "payload.bin", max_bytes=MAX_FILE_BYTES)
        if tuple(sorted(os.listdir(unit_fd))) != expected_names:
            raise HeldDataFileError("file_unit_membership_changed_during_verify")
        current_fd = writer.open_directory(unit_relative)
        try:
            current_stat = os.fstat(current_fd)
            if (current_stat.st_dev, current_stat.st_ino) != (
                unit_stat.st_dev,
                unit_stat.st_ino,
            ):
                raise HeldDataFileError("file_unit_directory_replaced_during_verify")
        finally:
            os.close(current_fd)
    finally:
        os.close(unit_fd)
    if sha256(raw) != expected_manifest_sha256:
        raise HeldDataFileError("file_manifest_external_pin_mismatch")
    try:
        manifest = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise HeldDataFileError("file_manifest_json_invalid") from exc
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS:
        raise HeldDataFileError("file_manifest_binding_invalid")
    declaration = plan.root_roster[role]
    source_identity = manifest.get("source_data_identity")
    source_signature = manifest.get("source_file_signature")
    storage_identity = manifest.get("storage_directory_identity")
    if (
        not isinstance(source_signature, dict)
        or set(source_signature) != SOURCE_SIGNATURE_FIELDS
        or any(type(value) is not int for value in source_signature.values())
        or not isinstance(storage_identity, dict)
        or set(storage_identity) != {"device", "inode"}
        or any(type(value) is not int for value in storage_identity.values())
    ):
        raise HeldDataFileError("file_manifest_binding_invalid")
    if (
        canonical_json(manifest) != raw
        or manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("capture_id") != capture_id
        or manifest.get("source_plan_sha256") != expected_plan_sha256
        or manifest.get("policy_sha256") != expected_policy_sha256
        or manifest.get("role") != role
        or manifest.get("relative_locator") != declaration.relative_locator
        or storage_identity != unit_identity
        or manifest.get("scope") != policy["scope"]
        or manifest.get("restore_disposition") != "quarantine_only"
        or manifest.get("metadata_disposition") != "omitted_unqualified"
        or manifest.get("application_consistency") != CAPTURE_CONSISTENCY_LITERAL
        or manifest.get("payload_relative_path") != f"{capture_id}/payload.bin"
        or type(manifest.get("payload_bytes")) is not int
        or not 0 <= manifest["payload_bytes"] <= policy["max_file_bytes"]
        or not _is_digest(manifest.get("payload_sha256"))
        or not isinstance(source_identity, dict)
        or source_identity.get("readonly") is not True
        or str(source_identity.get("data_volume_uuid", "")).lower() != plan.data_volume_uuid.lower()
        or source_identity.get("data_device_identifier") != plan.data_device_identifier
        or source_identity.get("data_container_reference") != plan.data_container_reference
        or str(source_identity.get("data_volume_group_id", "")).lower()
        != plan.data_volume_group_id.lower()
        or type(manifest.get("source_mount_device")) is not int
        or source_signature.get("size") != manifest["payload_bytes"]
        or source_signature.get("nlink") != 1
        or source_signature.get("device") != manifest.get("source_mount_device")
        or not stat.S_ISREG(source_signature.get("mode", 0))
        or source_signature.get("mode", 0) & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX)
    ):
        raise HeldDataFileError("file_manifest_binding_invalid")
    if len(payload) != manifest["payload_bytes"] or sha256(payload) != manifest["payload_sha256"]:
        raise HeldDataFileError("file_payload_hash_or_size_mismatch")
    final_fd = writer.open_directory(unit_relative)
    try:
        final_stat = os.fstat(final_fd)
        if (final_stat.st_dev, final_stat.st_ino) != (
            unit_stat.st_dev,
            unit_stat.st_ino,
        ) or tuple(sorted(os.listdir(final_fd))) != expected_names:
            raise HeldDataFileError("file_unit_directory_replaced_during_verify")
    finally:
        os.close(final_fd)
    writer.assert_storage_binding(storage_guard)
    return {
        "schema": MANIFEST_SCHEMA,
        "verified": True,
        "capture_id": capture_id,
        "manifest_sha256": expected_manifest_sha256,
        "payload_sha256": manifest["payload_sha256"],
        "payload_bytes": manifest["payload_bytes"],
        "role": role,
        "source_data_identity": source_identity,
        "restore_disposition": "quarantine_only",
        "T_qualified": False,
        "required_root_closure_complete": False,
        "kingston_K_frontier_qualified": False,
        "brain_restore_verified": False,
        "activation_authorized": False,
        "safe_to_erase": False,
    }


def _read_unit_member(directory_fd: int, name: str, *, max_bytes: int) -> bytes:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    except OSError as exc:
        raise HeldDataFileError("file_unit_member_open_failed") from exc
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_dev != os.fstat(directory_fd).st_dev
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size < 0
            or before.st_size > max_bytes
        ):
            raise HeldDataFileError("file_unit_member_metadata_invalid")
        chunks: list[bytes] = []
        total = 0
        while total < before.st_size:
            chunk = os.read(fd, min(CHUNK_BYTES, before.st_size - total))
            if not chunk:
                raise HeldDataFileError("file_unit_member_truncated")
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(fd)
        if _signature(after) != _signature(before):
            raise HeldDataFileError("file_unit_member_changed_during_verify")
        return b"".join(chunks)
    finally:
        os.close(fd)
