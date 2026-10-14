#!/usr/bin/env python3
"""Bind separately verified host recovery points into one private recovery set.

This index verifies declared member coverage and hashes. It does not qualify
application readiness, establish cross-root atomicity, or grant activation authority.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from memecoin_research_storage import StorageError
from tools import brain_host_recovery_capture as static_v1
from tools import brain_host_tree_custody as tree_v2
from tools import brain_host_tree_custody_v3 as tree_v3
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SCHEMA = "brain-host-recovery-set-v1"
SPEC_SCHEMA = "brain-host-recovery-set-spec-v1"
RESERVATION_SCHEMA = "brain-host-recovery-set-reservation-v1"
COMPLETE_SCHEMA = "brain-host-recovery-set-complete-v1"
WITNESS_SCHEMA = "brain-host-recovery-restore-witness-v1"
WITNESS_COMPLETE_SCHEMA = "brain-host-recovery-restore-witness-complete-v1"
BASE = "host-recovery/recovery-sets-v1"
WITNESS_BASE = "host-recovery/restore-witnesses-v1"
SQLITE_BASE = "host-recovery/sqlite-online-backups"
MAX_SPEC_BYTES = 4 * 1024 * 1024
MAX_RECEIPT_BYTES = 256 * 1024
MAX_DB_BYTES = 8 * 1024**3
MAX_DB_SECONDS = 600
SET_ID = re.compile(r"[0-9a-f]{32}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
ROLES = {
    "owner_work",
    "research_work",
    "private_config",
    "launchagent_definitions",
    "sqlite_backup",
    "protected_d43",
}
TREE_FORMATS = {"tree-custody-v3", "static-recovery-v1"}
ROLE_FORMATS = {
    "owner_work": {"tree-custody-v3"},
    "research_work": {"tree-custody-v3"},
    "private_config": TREE_FORMATS,
    "launchagent_definitions": TREE_FORMATS,
    "sqlite_backup": {"sqlite-online-backup-v1"},
    # Reuse only the already verified opaque V2 D43 point; do not route protected
    # content through the broad-root V3 capture path in this horizon.
    "protected_d43": {"tree-custody-v2"},
}
REQUIRED_ROLES = set(ROLES)
SQLITE_SOURCE_PATH = "brain-b13/operator04/operator.sqlite"
SQLITE_SUFFIXES = ("", "-wal", "-shm", "-journal")
D43_RELATIVE_PATH = "calendar-execution-01"


class RecoverySetError(ValueError):
    """A recovery set is malformed, incomplete, or does not match its pins."""


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not SET_ID.fullmatch(value):
        raise RecoverySetError(f"{label}_invalid")
    return value


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise RecoverySetError(f"{label}_invalid")
    return value


def _load_json(raw: bytes, label: str) -> dict[str, Any]:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise RecoverySetError(f"{label}_duplicate_key")
            value[key] = item
        return value

    try:
        value = json.loads(raw, object_pairs_hook=unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoverySetError(f"{label}_invalid") from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise RecoverySetError(f"{label}_noncanonical")
    return value


def _load_spec(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SPEC_BYTES:
            raise RecoverySetError("spec_file_invalid")
        chunks = []
        size = 0
        while data := os.read(fd, min(65536, MAX_SPEC_BYTES + 1 - size)):
            chunks.append(data)
            size += len(data)
            if size > MAX_SPEC_BYTES:
                raise RecoverySetError("spec_size_limit")
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or size != before.st_size:
            raise RecoverySetError("spec_changed_during_read")
        return _load_json(b"".join(chunks), "spec")
    finally:
        os.close(fd)


def _load_external_bindings(path: Path | None) -> dict[str, dict[str, dict[str, str]]] | None:
    if path is None:
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_SPEC_BYTES:
            raise RecoverySetError("external_root_bindings_invalid")
        chunks = []
        size = 0
        while chunk := os.read(fd, min(65536, MAX_SPEC_BYTES + 1 - size)):
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_SPEC_BYTES:
                raise RecoverySetError("external_root_bindings_invalid")
        after = os.fstat(fd)
        if _file_identity(before) != _file_identity(after) or size != before.st_size:
            raise RecoverySetError("external_root_bindings_changed")
        value = _load_json(b"".join(chunks), "external_root_bindings")
    finally:
        os.close(fd)
    if not value or any(
        role not in ROLES or not isinstance(items, dict) for role, items in value.items()
    ):
        raise RecoverySetError("external_root_bindings_invalid")
    for role_items in value.values():
        for member_path, binding in role_items.items():
            if (
                not isinstance(member_path, str)
                or not isinstance(binding, dict)
                or set(binding) != {"volume_uuid", "root_id", "relative_locator", "authority_ref"}
                or any(not isinstance(item, str) or not item for item in binding.values())
            ):
                raise RecoverySetError("external_root_bindings_invalid")
    return value


def _validate_spec(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema",
        "set_id",
        "volume_uuid",
        "root_id",
        "research_commit",
        "owner_commit",
        "members",
    }
    if set(value) != required or value.get("schema") != SPEC_SCHEMA:
        raise RecoverySetError("spec_contract_invalid")
    set_id = _id(value["set_id"], "set_id")
    if (
        not isinstance(value["volume_uuid"], str)
        or not value["volume_uuid"]
        or len(value["volume_uuid"]) > 64
        or not isinstance(value["root_id"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", value["root_id"])
        or not isinstance(value["research_commit"], str)
        or not COMMIT.fullmatch(value["research_commit"])
        or not isinstance(value["owner_commit"], str)
        or not COMMIT.fullmatch(value["owner_commit"])
    ):
        raise RecoverySetError("spec_identity_invalid")
    members_value = value["members"]
    if not isinstance(members_value, list) or len(members_value) != len(REQUIRED_ROLES):
        raise RecoverySetError("spec_member_set_invalid")
    members = []
    seen_roles = set()
    seen_members = set()
    member_fields = {
        "role",
        "format",
        "artifact_id",
        "manifest_sha256",
        "complete_sha256",
        "scope",
    }
    for item in members_value:
        if not isinstance(item, dict) or set(item) != member_fields:
            raise RecoverySetError("spec_member_invalid")
        role, fmt = item["role"], item["format"]
        if role not in ROLES or fmt not in ROLE_FORMATS[role] or role in seen_roles:
            raise RecoverySetError("spec_member_role_format_invalid")
        artifact_id = _id(item["artifact_id"], "artifact_id")
        identity = (fmt, artifact_id)
        if identity in seen_members:
            raise RecoverySetError("spec_member_duplicate")
        scope = item["scope"]
        expected_scope = "protected_opaque" if role == "protected_d43" else "non_protected"
        if fmt == "static-recovery-v1":
            expected_scope = "non_protected_static"
        elif fmt == "sqlite-online-backup-v1":
            expected_scope = "non_protected_sqlite_backup"
        if scope != expected_scope:
            raise RecoverySetError("spec_member_scope_invalid")
        members.append(
            {
                "role": role,
                "format": fmt,
                "artifact_id": artifact_id,
                "manifest_sha256": _hash(item["manifest_sha256"], "manifest_sha256"),
                "complete_sha256": _hash(item["complete_sha256"], "complete_sha256"),
                "scope": scope,
            }
        )
        seen_roles.add(role)
        seen_members.add(identity)
    if seen_roles != REQUIRED_ROLES:
        raise RecoverySetError("spec_member_set_invalid")
    members.sort(key=lambda item: (item["role"], item["format"], item["artifact_id"]))
    return {
        "schema": SPEC_SCHEMA,
        "set_id": set_id,
        "volume_uuid": value["volume_uuid"],
        "root_id": value["root_id"],
        "research_commit": value["research_commit"],
        "owner_commit": value["owner_commit"],
        "members": members,
    }


def _stream_hash(
    writer: DescriptorWriter, relative: str, *, max_bytes: int
) -> tuple[str, dict[str, int]]:
    parent, name = writer._parent_and_name(relative)
    parent_fd = writer._directory(parent)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    except OSError:
        os.close(parent_fd)
        raise RecoverySetError("artifact_unavailable") from None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise RecoverySetError("artifact_size_or_type_invalid")
        digest = hashlib.sha256()
        size = 0
        while data := os.read(fd, 1024 * 1024):
            digest.update(data)
            size += len(data)
            if size > max_bytes:
                raise RecoverySetError("artifact_size_limit")
        after = os.fstat(fd)
        if _file_identity(before) != _file_identity(after) or size != before.st_size:
            raise RecoverySetError("artifact_changed_during_read")
        return digest.hexdigest(), _file_identity(after)
    finally:
        os.close(fd)
        os.close(parent_fd)


def _file_identity(st: os.stat_result) -> dict[str, int]:
    return {
        "device": st.st_dev,
        "inode": st.st_ino,
        "mode": stat.S_IMODE(st.st_mode),
        "uid": st.st_uid,
        "gid": st.st_gid,
        "size_bytes": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "ctime_ns": st.st_ctime_ns,
    }


def _tree_member(writer: DescriptorWriter, member: dict[str, Any]) -> dict[str, Any]:
    aid = member["artifact_id"]
    if member["format"] == "tree-custody-v3":
        result = tree_v3.verify_tree(writer, aid)
        base = f"{tree_v3.BASE}/{aid}"
        manifest_raw = writer.read_bytes(
            f"{base}/MANIFEST.json", max_bytes=tree_v3.MAX_MANIFEST_BYTES
        )
        complete_raw = writer.read_bytes(f"{base}/COMPLETE.json", max_bytes=65536)
        manifest = _load_json(manifest_raw, "member_manifest")
        complete = _load_json(complete_raw, "member_complete")
        scope = manifest["scope"]
        interval = [manifest["capture_start_ns"], manifest["capture_end_ns"]]
        if scope != member["scope"]:
            raise RecoverySetError("member_scope_mismatch")
        return {
            "manifest_sha256": sha256(manifest_raw),
            "complete_sha256": sha256(complete_raw),
            "scope": scope,
            "interval_ns": interval,
            "verify_result": result,
            "manifest": manifest,
            "complete": complete,
        }
    if member["format"] == "tree-custody-v2":
        result = tree_v2.verify_tree(writer, aid)
        base = f"{tree_v2.BASE}/{aid}"
        manifest_raw = writer.read_bytes(
            f"{base}/MANIFEST.json", max_bytes=tree_v2.MAX_MANIFEST_BYTES
        )
        complete_raw = writer.read_bytes(f"{base}/COMPLETE.json", max_bytes=65536)
        manifest = _load_json(manifest_raw, "member_manifest")
        complete = _load_json(complete_raw, "member_complete")
        if manifest.get("scope") != "protected_opaque":
            raise RecoverySetError("member_scope_mismatch")
        if (
            manifest.get("schema") != tree_v2.SCHEMA
            or manifest.get("capture_id") != aid
            or complete.get("capture_id") != aid
            or complete.get("manifest_sha256") != sha256(manifest_raw)
            or result.get("manifest_sha256") != sha256(manifest_raw)
        ):
            raise RecoverySetError("member_pin_mismatch")
        interval = [manifest.get("capture_start_ns"), manifest.get("capture_end_ns")]
        if any(type(value) is not int for value in interval) or interval[1] < interval[0]:
            raise RecoverySetError("member_interval_invalid")
        return {
            "manifest_sha256": sha256(manifest_raw),
            "complete_sha256": sha256(complete_raw),
            "scope": manifest["scope"],
            "interval_ns": interval,
            "verify_result": result,
            "manifest": manifest,
            "complete": complete,
        }
    result = static_v1.verify_capture(writer, aid)
    base = f"{static_v1.CAPTURES_DIR}/{aid}"
    manifest_raw = writer.read_bytes(
        f"{base}/manifest.json", max_bytes=static_v1.MAX_METADATA_BYTES
    )
    complete_raw = writer.read_bytes(f"{base}/COMPLETE", max_bytes=16384)
    manifest = _load_json(manifest_raw, "member_manifest")
    complete = _load_json(complete_raw, "member_complete")
    created = manifest.get("created_ns")
    completed = complete.get("completed_ns")
    if type(created) is not int or type(completed) is not int or completed < created:
        raise RecoverySetError("member_interval_invalid")
    return {
        "manifest_sha256": sha256(manifest_raw),
        "complete_sha256": sha256(complete_raw),
        "scope": "non_protected_static",
        "interval_ns": [created, completed],
        "verify_result": result,
        "manifest": manifest,
        "complete": complete,
    }


def _v2_root_entry(writer: DescriptorWriter, manifest: dict[str, Any]) -> dict[str, Any]:
    """Read the hash-pinned root metadata row from a verified V2 point."""
    for index_ref in manifest["index_pages"]:
        index_raw = writer.read_bytes(index_ref["path"], max_bytes=tree_v2.MAX_PAGE_BYTES)
        if sha256(index_raw) != index_ref["sha256"]:
            raise RecoverySetError("member_index_hash_mismatch")
        index = _load_json(index_raw, "member_index")
        if index.get("schema") != "brain-host-tree-index-v1":
            raise RecoverySetError("member_index_invalid")
        for page_ref in index["rows"]:
            page_raw = writer.read_bytes(page_ref["path"], max_bytes=tree_v2.MAX_PAGE_BYTES)
            if sha256(page_raw) != page_ref["sha256"]:
                raise RecoverySetError("member_entry_page_hash_mismatch")
            rows = page_raw.splitlines()
            if len(rows) != page_ref["count"]:
                raise RecoverySetError("member_entry_page_invalid")
            for row in rows:
                try:
                    entry = json.loads(row)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RecoverySetError("member_entry_invalid") from exc
                if not isinstance(entry, dict) or canonical_json(entry).rstrip(b"\n") != row:
                    raise RecoverySetError("member_entry_noncanonical")
                if entry.get("path") == "":
                    return entry
    raise RecoverySetError("member_root_entry_missing")


def _sqlite_member(writer: DescriptorWriter, member: dict[str, Any]) -> dict[str, Any]:
    aid = member["artifact_id"]
    base = f"{SQLITE_BASE}/{aid}"
    receipt_raw = writer.read_bytes(f"{base}/RECEIPT.json", max_bytes=MAX_RECEIPT_BYTES)
    complete_raw = writer.read_bytes(f"{base}/COMPLETE.json", max_bytes=65536)
    receipt = _load_json(receipt_raw, "sqlite_receipt")
    complete = _load_json(complete_raw, "sqlite_complete")
    if (
        receipt.get("schema") != "brain-host-sqlite-backup-v1"
        or receipt.get("status") != "complete"
        or receipt.get("backup_integrity") != "ok"
        or receipt.get("source_path_recorded") is not False
        or receipt.get("source_logical_writes_initiated") is not False
        or receipt.get("source_durable_writes_initiated") is not False
        or receipt.get("transient_shm_coordination_may_change") is not True
        or receipt.get("source_changes_attributed") is not False
        or set(complete)
        != {"schema", "artifact_id", "backup_sha256", "backup_size_bytes", "receipt_sha256"}
        or complete.get("schema") != "brain-host-sqlite-online-artifact-complete-v1"
        or complete.get("artifact_id") != aid
        or complete.get("receipt_sha256") != sha256(receipt_raw)
    ):
        raise RecoverySetError("sqlite_artifact_contract_invalid")
    _validate_sqlite_source_family(receipt)
    if set(writer.list_names(base)) != {"backup.sqlite", "RECEIPT.json", "COMPLETE.json"}:
        raise RecoverySetError("sqlite_artifact_membership_invalid")
    backup_path = f"{base}/backup.sqlite"
    backup_hash, before_identity = _stream_hash(writer, backup_path, max_bytes=MAX_DB_BYTES)
    if (
        backup_hash != complete["backup_sha256"]
        or before_identity["size_bytes"] != receipt.get("backup_size_bytes")
        or before_identity["size_bytes"] != complete["backup_size_bytes"]
    ):
        raise RecoverySetError("sqlite_artifact_hash_mismatch")
    # Open only the already-published, hash-pinned standalone artifact. immutable=1
    # suppresses journal/WAL creation; bounded page progress prevents unbounded work.
    parent, name = writer._parent_and_name(backup_path)
    parent_fd = writer._directory(parent)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    os.close(parent_fd)
    started = time.monotonic()
    try:
        uri = f"file:/dev/fd/{fd}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.set_progress_handler(
                lambda: int(time.monotonic() - started > MAX_DB_SECONDS), 1000
            )
            if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RecoverySetError("sqlite_artifact_integrity_failed")
        finally:
            conn.close()
        after_hash, after_identity = _stream_hash(writer, backup_path, max_bytes=MAX_DB_BYTES)
        if (
            backup_hash != after_hash
            or before_identity != after_identity
            or set(writer.list_names(base)) != {"backup.sqlite", "RECEIPT.json", "COMPLETE.json"}
        ):
            raise RecoverySetError("sqlite_artifact_changed_during_verify")
        return {
            "manifest_sha256": sha256(receipt_raw),
            "complete_sha256": sha256(complete_raw),
            "scope": "non_protected_sqlite_backup",
            "interval_ns": _sqlite_interval(receipt),
            "backup_sha256": backup_hash,
            "backup_size_bytes": before_identity["size_bytes"],
            "backup_identity": before_identity,
            "source_family": receipt["source_family_before"],
            "receipt": receipt,
            "complete": complete,
        }
    finally:
        os.close(fd)


def _validate_sqlite_source_family(receipt: dict[str, Any]) -> None:
    family_before = receipt.get("source_family_before")
    family_after = receipt.get("source_family_after")
    if not isinstance(family_before, dict) or not isinstance(family_after, dict):
        raise RecoverySetError("sqlite_artifact_contract_invalid")
    expected_suffixes = {"", "-wal", "-shm", "-journal"}
    signature_fields = {
        "device",
        "inode",
        "mode",
        "uid",
        "gid",
        "size_bytes",
        "mtime_ns",
        "ctime_ns",
    }
    if set(family_before) != expected_suffixes or set(family_after) != expected_suffixes:
        raise RecoverySetError("sqlite_artifact_contract_invalid")
    if any(
        signature is not None
        and (
            not isinstance(signature, dict)
            or set(signature) != signature_fields
            or any(type(value) is not int or value < 0 for value in signature.values())
        )
        for family in (family_before, family_after)
        for signature in family.values()
    ):
        raise RecoverySetError("sqlite_artifact_contract_invalid")
    # The online reader may create/update transient SHM coordination state.
    # Main DB, WAL, and rollback journal remain durable source evidence and must
    # retain their exact signatures across the backup interval.
    if any(
        family_before.get(suffix) != family_after.get(suffix) for suffix in ("", "-wal", "-journal")
    ):
        raise RecoverySetError("sqlite_source_durable_family_drift")
    if receipt.get("source_shm_signature_changed") is not (
        family_before.get("-shm") != family_after.get("-shm")
    ):
        raise RecoverySetError("sqlite_source_shm_status_invalid")


def _sqlite_interval(receipt: dict[str, Any]) -> list[int]:
    try:
        start = datetime.fromisoformat(receipt["started_at_utc"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(receipt["completed_at_utc"].replace("Z", "+00:00"))
        start_ns = int(start.timestamp() * 1_000_000_000)
        end_ns = int(end.timestamp() * 1_000_000_000)
    except (KeyError, TypeError, ValueError) as exc:
        raise RecoverySetError("sqlite_interval_invalid") from exc
    if end_ns < start_ns:
        raise RecoverySetError("sqlite_interval_invalid")
    return [start_ns, end_ns]


def _verify_member(writer: DescriptorWriter, member: dict[str, Any]) -> dict[str, Any]:
    if member["format"] == "sqlite-online-backup-v1":
        result = _sqlite_member(writer, member)
    else:
        result = _tree_member(writer, member)
    if (
        result["manifest_sha256"] != member["manifest_sha256"]
        or result["complete_sha256"] != member["complete_sha256"]
        or result["scope"] != member["scope"]
    ):
        raise RecoverySetError("member_pin_mismatch")
    return {
        "role": member["role"],
        "format": member["format"],
        "artifact_id": member["artifact_id"],
        "manifest_sha256": result["manifest_sha256"],
        "complete_sha256": result["complete_sha256"],
        "scope": result["scope"],
        "interval_ns": result["interval_ns"],
        "artifact_sha256": result.get("backup_sha256", result["manifest_sha256"]),
        "artifact_size_bytes": result.get("backup_size_bytes", 0),
        "authority_id": result.get("manifest", {}).get("authority_id"),
        "recovery_point_id": result.get("manifest", {}).get("recovery_point_id"),
    }, result


def _validate_coverage(
    members: list[dict[str, Any]], verified: list[tuple[dict[str, Any], dict[str, Any]]]
) -> dict[str, Any]:
    by_role = {item["role"]: raw for item, raw in verified}
    owner = by_role["owner_work"]
    policy = owner["policy"]
    exact = policy.get("exact_path_exclusions", {})
    d43_decl = exact.get(D43_RELATIVE_PATH)
    d43 = by_role["protected_d43"]
    d43_manifest = d43["manifest"]
    d43_root_entry = d43["root_entry"]
    d43_exclusion = owner["d43_exclusion"]
    d43_identity_matches = (
        d43_decl == {"entry_type": "directory", "disposition": "separate_protected_custody"}
        and d43_manifest["scope"] == "protected_opaque"
        and bool(d43_exclusion)
        and d43_root_entry["source_device"] == d43_exclusion["source_device"]
        and d43_root_entry["source_inode"] == d43_exclusion["source_inode"]
        and d43_root_entry["mode"] == d43_exclusion["mode"]
        and d43_root_entry["uid"] == d43_exclusion["uid"]
        and d43_root_entry["gid"] == d43_exclusion["gid"]
        and d43_root_entry["size_bytes"] == d43_exclusion["size_bytes"]
        and d43_root_entry["source_nlink"] == d43_exclusion["source_nlink"]
        and d43_root_entry["source_flags"] == d43_exclusion["source_flags"]
        and d43_root_entry["source_ctime_ns"] == d43_exclusion["source_ctime_ns"]
        and d43_root_entry["mtime_ns"] == d43_exclusion["mtime_ns"]
    )
    sqlite = by_role["sqlite_backup"]
    sqlite_receipt = sqlite["receipt"]
    sqlite_source_family = sqlite_receipt["source_family_before"]
    sqlite_exclusions = {
        path: declaration
        for path, declaration in exact.items()
        if path == SQLITE_SOURCE_PATH or path.startswith(SQLITE_SOURCE_PATH + "-")
    }
    sqlite_entries = owner["exclusions_by_path"]
    expected_sqlite_paths = {
        SQLITE_SOURCE_PATH + suffix
        for suffix in SQLITE_SUFFIXES
        if suffix == ""
        or sqlite_source_family.get(suffix) is not None
        or sqlite_receipt["source_family_after"].get(suffix) is not None
    }
    sqlite_family_match = set(sqlite_exclusions) == expected_sqlite_paths and all(
        declaration.get("disposition") == "substitute_online_backup"
        for declaration in sqlite_exclusions.values()
    )
    for suffix, signature in sqlite_source_family.items():
        after_signature = sqlite_receipt["source_family_after"].get(suffix)
        if suffix != "-shm" and signature != after_signature:
            sqlite_family_match = False
        if signature is None and after_signature is None:
            continue
        path = SQLITE_SOURCE_PATH + suffix
        entry = sqlite_entries.get(path)
        if entry is None:
            sqlite_family_match = False
            continue
        mapping = {
            "device": "source_device",
            "inode": "source_inode",
            "mode": "mode",
            "uid": "uid",
            "gid": "gid",
            "size_bytes": "size_bytes",
            "mtime_ns": "mtime_ns",
            "ctime_ns": "source_ctime_ns",
        }
        observed_signatures = [
            item for item in (signature, after_signature) if isinstance(item, dict)
        ]
        if not observed_signatures or any(
            any(item.get(key) != entry.get(field) for key, field in mapping.items())
            for item in observed_signatures
        ):
            sqlite_family_match = False
    unresolved_policy = 0
    obligation_counts: dict[str, int] = {}
    rebuild_input_pins_valid = True
    for role in ("owner_work", "research_work", "private_config", "launchagent_definitions"):
        item = by_role[role]
        policy_value = item["policy"]
        for pin in policy_value.get("rebuild_inputs", []):
            entry = item.get("included_by_path", {}).get(pin["path"])
            if entry is None or entry.get("sha256") != pin["sha256"]:
                rebuild_input_pins_valid = False
        for declaration in policy_value.get("exact_path_exclusions", {}).values():
            disposition = declaration["disposition"]
            obligation_counts[disposition] = obligation_counts.get(disposition, 0) + 1
            if disposition.startswith("unresolved"):
                unresolved_policy += 1
        for disposition in policy_value.get("pem_candidate_dispositions", {}).values():
            obligation_counts[disposition] = obligation_counts.get(disposition, 0) + 1
            if disposition.startswith("unresolved"):
                unresolved_policy += 1
        for declaration in policy_value.get("symlink_target_dispositions", {}).values():
            disposition = declaration["disposition"]
            obligation_counts[disposition] = obligation_counts.get(disposition, 0) + 1
            if disposition == "external_root_binding":
                unresolved_policy += 1
            elif declaration["required"] and disposition == "preserve_dangling_internal":
                unresolved_policy += 1
        if item.get("verify_result", {}).get("unresolved_exclusion_count", 0):
            unresolved_policy += item["verify_result"]["unresolved_exclusion_count"]
    return {
        "d43_exclusion_bound_to_opaque_capture": d43_identity_matches,
        "sqlite_substitution_bound_to_backup": sqlite_family_match,
        "rebuild_inputs_hash_bound": rebuild_input_pins_valid,
        "declared_obligation_count": sum(obligation_counts.values()),
        "external_root_binding_obligation_count": obligation_counts.get("external_root_binding", 0),
        "unresolved_required_obligation_count": unresolved_policy,
        "obligation_disposition_counts": dict(sorted(obligation_counts.items())),
        "coverage_resolved": (
            d43_identity_matches
            and sqlite_family_match
            and rebuild_input_pins_valid
            and unresolved_policy == 0
        ),
    }


def _prepare_details(
    writer: DescriptorWriter, members: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    verified = []
    for member in members:
        item, detail = _verify_member(writer, member)
        if member["role"] in {
            "owner_work",
            "research_work",
            "private_config",
            "launchagent_definitions",
            "protected_d43",
        }:
            if member["format"] == "tree-custody-v3":
                aid = member["artifact_id"]
                base = f"{tree_v3.BASE}/{aid}"
                manifest = _load_json(
                    writer.read_bytes(
                        f"{base}/MANIFEST.json", max_bytes=tree_v3.MAX_MANIFEST_BYTES
                    ),
                    "manifest",
                )
                policy_raw = writer.read_bytes(
                    f"{base}/{tree_v3.POLICY_NAME}", max_bytes=tree_v3.MAX_PAGE_BYTES
                )
                policy = _load_json(policy_raw, "policy")
                detail.update({"manifest": manifest, "policy": policy, "policy_raw": policy_raw})
                rebuild_paths = {pin["path"] for pin in policy.get("rebuild_inputs", [])}
                detail["included_by_path"] = {
                    row["path"]: row
                    for row in tree_v3._iter_records(
                        writer, manifest["entry_indexes"], kind="entries"
                    )
                    if row["kind"] == "file" and row["path"] in rebuild_paths
                }
                if member["role"] == "owner_work":
                    relevant_exclusions = {
                        D43_RELATIVE_PATH,
                        *(SQLITE_SOURCE_PATH + suffix for suffix in SQLITE_SUFFIXES),
                    }
                    detail["exclusions_by_path"] = {
                        row["path"]: row
                        for row in tree_v3._iter_records(
                            writer, manifest["exclusion_indexes"], kind="exclusions"
                        )
                        if row["path"] in relevant_exclusions
                    }
                    detail["d43_exclusion"] = detail["exclusions_by_path"].get(
                        D43_RELATIVE_PATH, {}
                    )
                if member["role"] == "protected_d43":
                    records = tree_v3._iter_records(
                        writer, manifest["entry_indexes"], kind="entries"
                    )
                    detail["root_entry"] = next(
                        record for record in records if record["path"] == ""
                    )
            elif member["format"] == "tree-custody-v2":
                if member["role"] != "protected_d43":
                    raise RecoverySetError("member_format_role_invalid")
                detail["root_entry"] = _v2_root_entry(writer, detail["manifest"])
            else:
                detail.setdefault("policy", {})
        if member["role"] == "sqlite_backup":
            detail.setdefault("manifest", detail["receipt"])
        if (
            member["role"] in {"private_config", "launchagent_definitions"}
            and member["format"] == "static-recovery-v1"
        ):
            detail.setdefault("policy", {})
        verified.append((item, detail))
    return verified


def _iter_tree_entries(writer: DescriptorWriter, member: dict[str, Any], manifest: dict[str, Any]):
    if member["format"] == "tree-custody-v3":
        yield from tree_v3._iter_records(writer, manifest["entry_indexes"], kind="entries")
        return
    for index_ref in manifest["index_pages"]:
        index_raw = writer.read_bytes(index_ref["path"], max_bytes=tree_v2.MAX_PAGE_BYTES)
        if sha256(index_raw) != index_ref["sha256"]:
            raise RecoverySetError("member_index_hash_mismatch")
        index = _load_json(index_raw, "member_index")
        for page_ref in index["rows"]:
            raw = writer.read_bytes(page_ref["path"], max_bytes=tree_v2.MAX_PAGE_BYTES)
            if sha256(raw) != page_ref["sha256"]:
                raise RecoverySetError("member_entry_page_hash_mismatch")
            lines = raw.splitlines()
            if len(lines) != page_ref["count"]:
                raise RecoverySetError("member_entry_page_invalid")
            for line in lines:
                try:
                    entry = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise RecoverySetError("member_entry_invalid") from exc
                if not isinstance(entry, dict) or canonical_json(entry).rstrip(b"\n") != line:
                    raise RecoverySetError("member_entry_noncanonical")
                yield entry


def _open_quarantine_entry(root_fd: int, relative: str) -> tuple[int, str]:
    if not relative:
        return os.dup(root_fd), ""
    parent, _, leaf = relative.rpartition("/")
    parent_fd = tree_v2._open_rel_dir(root_fd, parent)
    return parent_fd, leaf


def _verify_sqlite_quarantine_file(
    writer: DescriptorWriter, member: dict[str, Any], destination: Path
) -> dict[str, Any]:
    _member, detail = _verify_member(writer, member)
    destination = Path(os.path.abspath(destination))
    parent_fd = tree_v2._open_absolute_directory(destination.parent)
    try:
        leaf = destination.name
        if tree_v3._path_component(leaf) != leaf:
            raise RecoverySetError("restore_witness_path_invalid")
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size != detail["backup_size_bytes"]:
                raise RecoverySetError("restore_witness_sqlite_size_mismatch")
            digest = hashlib.sha256()
            total = 0
            while block := os.read(fd, 1024 * 1024):
                digest.update(block)
                total += len(block)
            if total != before.st_size or digest.hexdigest() != detail["backup_sha256"]:
                raise RecoverySetError("restore_witness_sqlite_hash_mismatch")
            for suffix in ("-wal", "-shm", "-journal"):
                try:
                    os.stat(f"{leaf}{suffix}", dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise RecoverySetError("restore_witness_sqlite_sidecar_present")
            started = time.monotonic()
            os.lseek(fd, 0, os.SEEK_SET)
            conn = sqlite3.connect(f"file:/dev/fd/{fd}?mode=ro&immutable=1", uri=True, timeout=1.0)
            try:
                conn.execute("PRAGMA query_only=ON")
                conn.set_progress_handler(
                    lambda: int(time.monotonic() - started > MAX_DB_SECONDS), 1000
                )
                if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RecoverySetError("restore_witness_sqlite_integrity_failed")
            finally:
                conn.close()
            after = os.fstat(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            final_hash = hashlib.sha256()
            final_size = 0
            while block := os.read(fd, 1024 * 1024):
                final_hash.update(block)
                final_size += len(block)
            if (
                _file_identity(before) != _file_identity(after)
                or final_size != total
                or final_hash.hexdigest() != detail["backup_sha256"]
            ):
                raise RecoverySetError("restore_witness_sqlite_changed")
            return {
                "manifest_sha256": detail["manifest_sha256"],
                "restore_marker_sha256": None,
                "entry_count": 1,
                "destination_sha256": hashlib.sha256(os.fsencode(destination)).hexdigest(),
                "artifact_sha256": digest.hexdigest(),
                "artifact_size_bytes": total,
                "integrity": "ok",
            }
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def _verify_static_quarantine(
    writer: DescriptorWriter, member: dict[str, Any], destination: Path
) -> dict[str, Any]:
    _member, detail = _verify_member(writer, member)
    manifest = detail["manifest"]
    manifest_hash = detail["manifest_sha256"]
    destination = Path(os.path.abspath(destination))
    dest_fd = tree_v2._open_absolute_directory(destination)
    temp_dir = tempfile.TemporaryDirectory(prefix="brain-static-witness-")
    db = sqlite3.connect(Path(temp_dir.name) / "membership.sqlite")
    db.execute("CREATE TABLE expected(path TEXT PRIMARY KEY, kind TEXT NOT NULL)")
    db.execute("CREATE TABLE actual(path TEXT PRIMARY KEY, kind TEXT NOT NULL)")
    entry_count = 0
    try:
        marker_fd = os.open(
            "RESTORE_COMPLETE.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dest_fd
        )
        try:
            marker_stat = os.fstat(marker_fd)
            if not stat.S_ISREG(marker_stat.st_mode) or marker_stat.st_size > 65536:
                raise RecoverySetError("restore_witness_marker_invalid")
            marker_chunks = []
            marker_size = 0
            while chunk := os.read(marker_fd, min(65536, 65537 - marker_size)):
                marker_chunks.append(chunk)
                marker_size += len(chunk)
                if marker_size > 65536:
                    raise RecoverySetError("restore_witness_marker_invalid")
            if marker_size != marker_stat.st_size:
                raise RecoverySetError("restore_witness_marker_changed")
            marker_raw = b"".join(marker_chunks)
        finally:
            os.close(marker_fd)
        marker = _load_json(marker_raw, "restore_witness_marker")
        if (
            marker.get("schema") != static_v1.RESTORE_SCHEMA
            or marker.get("capture_id") != member["artifact_id"]
            or marker.get("manifest_sha256") != manifest_hash
            or marker.get("membership_sha256") != detail["verify_result"].get("membership_sha256")
        ):
            raise RecoverySetError("restore_witness_marker_mismatch")
        for root in manifest["roots"]:
            root_name = root["name"]
            root_fd = os.open(
                root_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dest_fd
            )
            try:
                for entry in root["entries"]:
                    rel = entry["path"]
                    kind = entry["kind"]
                    if kind not in {"directory", "file"}:
                        raise RecoverySetError("restore_witness_static_kind_unsupported")
                    combined = f"{root_name}/{rel}" if rel else root_name
                    db.execute("INSERT INTO expected(path,kind) VALUES(?,?)", (combined, kind))
                    pfd, leaf = _open_quarantine_entry(root_fd, rel)
                    try:
                        st = (
                            os.fstat(pfd)
                            if not leaf
                            else os.stat(leaf, dir_fd=pfd, follow_symlinks=False)
                        )
                        if (
                            (kind == "directory" and not stat.S_ISDIR(st.st_mode))
                            or (kind == "file" and not stat.S_ISREG(st.st_mode))
                            or st.st_uid != os.geteuid()
                            or st.st_gid != os.getegid()
                            or stat.S_IMODE(st.st_mode) != (entry["mode"] & 0o777)
                            or st.st_mtime_ns != entry["mtime_ns"]
                        ):
                            raise RecoverySetError("restore_witness_metadata_mismatch")
                        if kind == "file":
                            fd = os.open(
                                leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd
                            )
                            try:
                                digest = hashlib.sha256()
                                size = 0
                                while block := os.read(fd, 1024 * 1024):
                                    digest.update(block)
                                    size += len(block)
                                if (
                                    size != entry["size_bytes"]
                                    or digest.hexdigest() != entry["sha256"]
                                ):
                                    raise RecoverySetError("restore_witness_file_hash_mismatch")
                            finally:
                                os.close(fd)
                    finally:
                        os.close(pfd)
                    entry_count += 1
            finally:
                os.close(root_fd)
        stack = [(os.dup(dest_fd), "")]
        while stack:
            current_fd, prefix = stack.pop()
            try:
                with os.scandir(current_fd) as iterator:
                    for item in iterator:
                        if not prefix and item.name == "RESTORE_COMPLETE.json":
                            continue
                        rel = f"{prefix}/{item.name}" if prefix else item.name
                        st = os.stat(item.name, dir_fd=current_fd, follow_symlinks=False)
                        kind = (
                            "directory"
                            if stat.S_ISDIR(st.st_mode)
                            else "file"
                            if stat.S_ISREG(st.st_mode)
                            else "special"
                        )
                        db.execute("INSERT INTO actual(path,kind) VALUES(?,?)", (rel, kind))
                        if kind == "directory":
                            stack.append(
                                (
                                    os.open(
                                        item.name,
                                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=current_fd,
                                    ),
                                    rel,
                                )
                            )
            finally:
                os.close(current_fd)
        mismatch = db.execute(
            "SELECT 1 FROM expected e LEFT JOIN actual a ON e.path=a.path AND e.kind=a.kind "
            "WHERE a.path IS NULL UNION ALL SELECT 1 FROM actual a LEFT JOIN expected e "
            "ON a.path=e.path AND a.kind=e.kind WHERE e.path IS NULL LIMIT 1"
        ).fetchone()
        if mismatch:
            raise RecoverySetError("restore_witness_membership_mismatch")
        return {
            "entry_count": entry_count,
            "destination_sha256": hashlib.sha256(os.fsencode(destination)).hexdigest(),
            "restore_marker_sha256": sha256(marker_raw),
            "manifest_sha256": manifest_hash,
        }
    finally:
        db.close()
        temp_dir.cleanup()
        os.close(dest_fd)


def verify_quarantine_restore(
    writer: DescriptorWriter,
    member: dict[str, Any],
    destination: Path,
    *,
    external_root_bindings: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Recheck a completed extraction's marker, membership, topology, and file bytes."""
    if member["format"] == "sqlite-online-backup-v1":
        return _verify_sqlite_quarantine_file(writer, member, destination)
    if member["format"] == "static-recovery-v1":
        return _verify_static_quarantine(writer, member, destination)
    _verified, detail = _verify_member(writer, member)
    manifest = detail["manifest"]
    manifest_hash = detail["manifest_sha256"]
    destination = Path(os.path.abspath(destination))
    dest_fd = tree_v2._open_absolute_directory(destination)
    root_fd = -1
    db = None
    try:
        marker_fd = os.open(
            "RESTORE_COMPLETE.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dest_fd
        )
        try:
            marker_stat = os.fstat(marker_fd)
            if not stat.S_ISREG(marker_stat.st_mode) or marker_stat.st_size > 65536:
                raise RecoverySetError("restore_witness_marker_invalid")
            marker_raw = b""
            while chunk := os.read(marker_fd, 65536 - len(marker_raw) + 1):
                marker_raw += chunk
                if len(marker_raw) > 65536:
                    raise RecoverySetError("restore_witness_marker_invalid")
        finally:
            os.close(marker_fd)
        marker = _load_json(marker_raw, "restore_witness_marker")
        expected_marker_schema = {
            "tree-custody-v2": tree_v2.RESTORE_SCHEMA,
            "tree-custody-v3": tree_v3.RESTORE_SCHEMA,
        }.get(member["format"])
        if (
            marker.get("capture_id") != member["artifact_id"]
            or marker.get("manifest_sha256") != manifest_hash
            or expected_marker_schema is None
            or marker.get("schema") != expected_marker_schema
        ):
            raise RecoverySetError("restore_witness_marker_mismatch")
        mapped_targets: dict[str, str] = {}
        if member["format"] == "tree-custody-v3":
            base = f"{tree_v3.BASE}/{member['artifact_id']}"
            policy_raw = writer.read_bytes(
                f"{base}/{tree_v3.POLICY_NAME}", max_bytes=tree_v3.MAX_PAGE_BYTES
            )
            if sha256(policy_raw) != manifest["policy_sha256"]:
                raise RecoverySetError("restore_witness_policy_hash_mismatch")
            policy = tree_v3._validate_policy(_load_json(policy_raw, "restore_witness_policy"))
            external = {
                path: declaration
                for path, declaration in policy["symlink_target_dispositions"].items()
                if declaration["disposition"] == "external_root_binding"
            }
            bindings = external_root_bindings or {}
            originals = {
                item["path"]: item["target_b64"]
                for item in _iter_tree_entries(writer, member, manifest)
                if item["path"] in external
            }
            derived = (
                tree_v3._derive_external_root_targets(writer, external, bindings, originals)
                if external
                else {}
            )
            if not external and bindings:
                raise RecoverySetError("restore_witness_external_bindings_invalid")
            expected_mappings = [
                {"path": path, **mapping} for path, mapping in sorted(derived.items())
            ]
            if marker.get("external_root_mappings") != expected_mappings:
                raise RecoverySetError("restore_witness_external_mapping_mismatch")
            mapped_targets = {path: value["derived_target_b64"] for path, value in derived.items()}
        elif external_root_bindings:
            raise RecoverySetError("restore_witness_external_bindings_invalid")
        root_name = manifest["root_name"]
        root_fd = os.open(root_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dest_fd)
        uid, gid = marker.get("target_uid"), marker.get("target_gid")
        if type(uid) is not int or type(gid) is not int:
            raise RecoverySetError("restore_witness_owner_mapping_invalid")
        temp_dir = tempfile.TemporaryDirectory(prefix="brain-set-witness-")
        db = sqlite3.connect(Path(temp_dir.name) / "membership.sqlite")
        db.execute("CREATE TABLE expected(path TEXT PRIMARY KEY, kind TEXT NOT NULL)")
        db.execute("CREATE TABLE actual(path TEXT PRIMARY KEY, kind TEXT NOT NULL)")
        entry_count = 0
        for entry in _iter_tree_entries(writer, member, manifest):
            rel = entry["path"]
            kind = entry["kind"]
            db.execute(
                "INSERT INTO expected(path,kind) VALUES(?,?)",
                (rel, "file" if kind == "hardlink" else kind),
            )
            pfd, leaf = _open_quarantine_entry(root_fd, rel)
            try:
                st = os.fstat(pfd) if not leaf else os.stat(leaf, dir_fd=pfd, follow_symlinks=False)
                expected_mode = entry["mode"]
                if (
                    (kind == "directory" and not stat.S_ISDIR(st.st_mode))
                    or (kind in {"file", "hardlink"} and not stat.S_ISREG(st.st_mode))
                    or (kind == "symlink" and not stat.S_ISLNK(st.st_mode))
                    or st.st_uid != uid
                    or st.st_gid != gid
                    or (kind in {"file", "hardlink"} and st.st_nlink != entry["source_nlink"])
                    or (kind != "symlink" and stat.S_IMODE(st.st_mode) != expected_mode)
                    or st.st_mtime_ns != entry["mtime_ns"]
                    or getattr(st, "st_flags", 0) != entry.get("source_flags", 0)
                ):
                    raise RecoverySetError("restore_witness_metadata_mismatch")
                if kind == "symlink":
                    link = os.readlink(os.fsencode(leaf), dir_fd=pfd)
                    link_bytes = link if isinstance(link, bytes) else os.fsencode(link)
                    if base64.b64encode(link_bytes).decode("ascii") != entry["target_b64"]:
                        if base64.b64encode(link_bytes).decode("ascii") != mapped_targets.get(
                            rel, entry["target_b64"]
                        ):
                            raise RecoverySetError("restore_witness_symlink_mismatch")
                elif kind == "file":
                    fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=pfd)
                    try:
                        digest = hashlib.sha256()
                        total = 0
                        while block := os.read(fd, 1024 * 1024):
                            digest.update(block)
                            total += len(block)
                        if total != entry["size_bytes"] or digest.hexdigest() != entry["sha256"]:
                            raise RecoverySetError("restore_witness_file_hash_mismatch")
                    finally:
                        os.close(fd)
                elif kind == "hardlink":
                    primary_pfd, primary_leaf = _open_quarantine_entry(root_fd, entry["link_to"])
                    try:
                        primary_st = os.stat(
                            primary_leaf, dir_fd=primary_pfd, follow_symlinks=False
                        )
                    finally:
                        os.close(primary_pfd)
                    if (st.st_dev, st.st_ino) != (primary_st.st_dev, primary_st.st_ino):
                        raise RecoverySetError("restore_witness_hardlink_mismatch")
            finally:
                os.close(pfd)
            entry_count += 1
        db.execute("INSERT INTO actual(path,kind) VALUES(?,?)", ("", "directory"))
        stack = [(os.dup(root_fd), "")]
        while stack:
            current_fd, prefix = stack.pop()
            try:
                with os.scandir(current_fd) as iterator:
                    for item in iterator:
                        rel = f"{prefix}/{item.name}" if prefix else item.name
                        st = os.stat(item.name, dir_fd=current_fd, follow_symlinks=False)
                        kind = (
                            "directory"
                            if stat.S_ISDIR(st.st_mode)
                            else "symlink"
                            if stat.S_ISLNK(st.st_mode)
                            else "file"
                            if stat.S_ISREG(st.st_mode)
                            else "special"
                        )
                        db.execute("INSERT INTO actual(path,kind) VALUES(?,?)", (rel, kind))
                        if kind == "directory":
                            child = os.open(
                                item.name,
                                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=current_fd,
                            )
                            stack.append((child, rel))
            finally:
                os.close(current_fd)
        expected_n = db.execute("SELECT count(*) FROM expected").fetchone()[0]
        actual_n = db.execute("SELECT count(*) FROM actual").fetchone()[0]
        mismatch = db.execute(
            "SELECT 1 FROM expected e LEFT JOIN actual a ON e.path=a.path AND e.kind=a.kind "
            "WHERE a.path IS NULL UNION ALL SELECT 1 FROM actual a LEFT JOIN expected e "
            "ON a.path=e.path AND a.kind=e.kind WHERE e.path IS NULL LIMIT 1"
        ).fetchone()
        if expected_n != actual_n or mismatch:
            raise RecoverySetError("restore_witness_membership_mismatch")
        return {
            "entry_count": entry_count,
            "external_root_mapping_count": len(mapped_targets),
            "destination_sha256": hashlib.sha256(os.fsencode(destination)).hexdigest(),
            "restore_marker_sha256": sha256(marker_raw),
            "manifest_sha256": manifest_hash,
        }
    finally:
        if db is not None:
            db.close()
        if "temp_dir" in locals():
            temp_dir.cleanup()
        if root_fd >= 0:
            os.close(root_fd)
        os.close(dest_fd)


def _verify_restore_witness(
    writer: DescriptorWriter,
    set_id: str,
    witness_id: str,
    *,
    index_sha256: str,
    expected_volume_uuid: str,
    expected_root_id: str,
    members_by_role: dict[str, dict[str, Any]],
    targets: dict[str, Path],
    external_root_bindings: dict[str, dict[str, dict[str, str]]] | None = None,
    require_complete: bool = True,
) -> tuple[str, int]:
    witness_id = _id(witness_id, "witness_id")
    expected_roles = REQUIRED_ROLES
    if set(targets) != expected_roles:
        raise RecoverySetError("restore_witness_target_roles_invalid")
    bindings = external_root_bindings or {}
    if not isinstance(bindings, dict) or set(bindings) - {
        "owner_work",
        "research_work",
        "private_config",
        "launchagent_definitions",
    }:
        raise RecoverySetError("restore_witness_external_bindings_invalid")
    if members_by_role["protected_d43"]["format"] != "tree-custody-v2":
        raise RecoverySetError("restore_witness_protected_format_invalid")
    base = f"{WITNESS_BASE}/{set_id}/{witness_id}"
    expected_names = {"WITNESS.json", "COMPLETE.json"} if require_complete else {"WITNESS.json"}
    if set(writer.list_names(base)) != expected_names:
        raise RecoverySetError("restore_witness_membership_invalid")
    witness_raw = writer.read_bytes(f"{base}/WITNESS.json", max_bytes=MAX_SPEC_BYTES)
    witness = _load_json(witness_raw, "restore_witness")
    complete = (
        _load_json(
            writer.read_bytes(f"{base}/COMPLETE.json", max_bytes=65536), "restore_witness_complete"
        )
        if require_complete
        else None
    )
    if (
        witness.get("schema") != WITNESS_SCHEMA
        or witness.get("set_id") != set_id
        or witness.get("index_sha256") != index_sha256
        or witness.get("tool_sha256") != _tool_hashes()
        or witness.get("application_recovery_qualified") is not False
        or witness.get("volume_uuid") != expected_volume_uuid
        or witness.get("root_id") != expected_root_id
        or (
            require_complete
            and complete
            != {
                "schema": WITNESS_COMPLETE_SCHEMA,
                "set_id": set_id,
                "witness_id": witness_id,
                "index_sha256": index_sha256,
                "witness_sha256": sha256(witness_raw),
            }
        )
    ):
        raise RecoverySetError("restore_witness_pin_mismatch")
    evidence = witness.get("targets")
    if not isinstance(evidence, dict) or set(evidence) != expected_roles:
        raise RecoverySetError("restore_witness_target_roles_invalid")
    external_mapping_count = 0
    for role in sorted(expected_roles):
        member = members_by_role[role]
        observed = verify_quarantine_restore(
            writer,
            member,
            targets[role],
            external_root_bindings=bindings.get(role),
        )
        external_mapping_count += observed.get("external_root_mapping_count", 0)
        expected_evidence = {
            "destination_sha256": hashlib.sha256(
                os.fsencode(Path(os.path.abspath(targets[role])))
            ).hexdigest(),
            "manifest_sha256": observed["manifest_sha256"],
            "restore_marker_sha256": observed["restore_marker_sha256"],
            "entry_count": observed["entry_count"],
            "external_root_mapping_count": observed.get("external_root_mapping_count", 0),
        }
        if role == "sqlite_backup":
            expected_evidence.update(
                {
                    "artifact_sha256": observed["artifact_sha256"],
                    "artifact_size_bytes": observed["artifact_size_bytes"],
                    "integrity": observed["integrity"],
                }
            )
        if evidence[role] != expected_evidence:
            raise RecoverySetError("restore_witness_target_changed")
    return sha256(witness_raw), external_mapping_count


def bind_restore_witness(
    writer: DescriptorWriter,
    set_id: str,
    witness_id: str,
    targets: dict[str, Path],
    *,
    expected_volume_uuid: str,
    expected_root_id: str,
    revalidate_identity: Callable[[], tuple[str, str]],
    external_root_bindings: dict[str, dict[str, dict[str, str]]] | None = None,
) -> dict[str, Any]:
    """Bind already extracted quarantine roots; this does not activate services."""
    verified = verify_set(
        writer,
        set_id,
        expected_volume_uuid=expected_volume_uuid,
        expected_root_id=expected_root_id,
        revalidate_identity=revalidate_identity,
    )
    base = f"{BASE}/{set_id}"
    index_raw = writer.read_bytes(f"{base}/INDEX.json", max_bytes=MAX_SPEC_BYTES)
    index = _load_json(index_raw, "index")
    members = {item["role"]: item for item in _validate_spec(index["spec"])["members"]}
    expected_roles = REQUIRED_ROLES
    if set(targets) != expected_roles:
        raise RecoverySetError("restore_witness_target_roles_invalid")
    if members["protected_d43"]["format"] != "tree-custody-v2":
        raise RecoverySetError("restore_witness_protected_format_invalid")
    target_evidence = {}
    for role in sorted(expected_roles):
        observed = verify_quarantine_restore(
            writer,
            members[role],
            targets[role],
            external_root_bindings=(external_root_bindings or {}).get(role),
        )
        target_evidence[role] = {
            "destination_sha256": observed["destination_sha256"],
            "manifest_sha256": observed["manifest_sha256"],
            "restore_marker_sha256": observed["restore_marker_sha256"],
            "entry_count": observed["entry_count"],
            "external_root_mapping_count": observed.get("external_root_mapping_count", 0),
        }
        if role == "sqlite_backup":
            target_evidence[role].update(
                {
                    "artifact_sha256": observed["artifact_sha256"],
                    "artifact_size_bytes": observed["artifact_size_bytes"],
                    "integrity": observed["integrity"],
                }
            )
    witness = canonical_json(
        {
            "schema": WITNESS_SCHEMA,
            "set_id": set_id,
            "witness_id": _id(witness_id, "witness_id"),
            "volume_uuid": expected_volume_uuid,
            "root_id": expected_root_id,
            "index_sha256": sha256(index_raw),
            "targets": target_evidence,
            "tool_sha256": _tool_hashes(),
            "application_recovery_qualified": False,
        }
    )
    witness_base = f"{WITNESS_BASE}/{set_id}/{witness_id}"
    writer.mkdir(WITNESS_BASE)
    writer.mkdir(f"{WITNESS_BASE}/{set_id}")
    try:
        writer.list_names(witness_base)
    except FileNotFoundError:
        pass
    else:
        raise RecoverySetError("restore_witness_collision")
    writer.mkdir(witness_base)
    writer.create(f"{witness_base}/WITNESS.json", witness)
    if writer.read_bytes(f"{witness_base}/WITNESS.json", max_bytes=MAX_SPEC_BYTES) != witness:
        raise RecoverySetError("restore_witness_readback_mismatch")
    if revalidate_identity() != (expected_volume_uuid, expected_root_id):
        raise RecoverySetError("set_identity_changed")
    # Re-read the quarantine roots immediately before publishing the marker.
    witness_hash, external_mapping_count = _verify_restore_witness(
        writer,
        set_id,
        witness_id,
        index_sha256=sha256(index_raw),
        expected_volume_uuid=expected_volume_uuid,
        expected_root_id=expected_root_id,
        members_by_role=members,
        targets=targets,
        external_root_bindings=external_root_bindings,
        require_complete=False,
    )
    if revalidate_identity() != (expected_volume_uuid, expected_root_id):
        raise RecoverySetError("set_identity_changed")
    complete = canonical_json(
        {
            "schema": WITNESS_COMPLETE_SCHEMA,
            "set_id": set_id,
            "witness_id": witness_id,
            "index_sha256": sha256(index_raw),
            "witness_sha256": witness_hash,
        }
    )
    writer.create(f"{witness_base}/COMPLETE.json", complete)
    if writer.read_bytes(f"{witness_base}/COMPLETE.json", max_bytes=65536) != complete:
        raise RecoverySetError("restore_witness_complete_readback_mismatch")
    return {
        "schema": WITNESS_SCHEMA,
        "set_id": set_id,
        "restore_witness_bound": True,
        "restore_mapping_obligations_resolved": True,
        "restore_external_root_binding_count": external_mapping_count,
        "application_recovery_qualified": False,
        "source_root_closure_verified": False,
        "factory_reset_ready": False,
        "target_count": len(target_evidence),
        "witness_sha256": witness_hash,
        "members_verified": verified["members_verified"],
    }


def _tool_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parent
    names = {
        "recovery_set": "brain_host_recovery_set.py",
        "tree_custody_v3": "brain_host_tree_custody_v3.py",
        "static_recovery_v1": "brain_host_recovery_capture.py",
        "sqlite_backup": "brain_host_sqlite_backup.py",
        "tree_custody_v2": "brain_host_tree_custody.py",
        "storage_backend": "../src/memecoin_research_storage/__init__.py",
        "descriptor_writer": "brain_senses_external_writer_v1.py",
    }
    return {
        key: hashlib.sha256((root / name).resolve().read_bytes()).hexdigest()
        for key, name in names.items()
    }


def verify_set(
    writer: DescriptorWriter,
    set_id: str,
    *,
    expected_volume_uuid: str,
    expected_root_id: str,
    revalidate_identity: Callable[[], tuple[str, str]] | None = None,
    witness_id: str | None = None,
    witness_targets: dict[str, Path] | None = None,
    external_root_bindings: dict[str, dict[str, dict[str, str]]] | None = None,
) -> dict[str, Any]:
    set_id = _id(set_id, "set_id")
    base = f"{BASE}/{set_id}"
    if revalidate_identity is not None and revalidate_identity() != (
        expected_volume_uuid,
        expected_root_id,
    ):
        raise RecoverySetError("set_identity_changed")
    names = set(writer.list_names(base))
    if names != {"RESERVATION.json", "INDEX.json", "COMPLETE.json"}:
        raise RecoverySetError("set_membership_invalid")
    reservation_raw = writer.read_bytes(f"{base}/RESERVATION.json", max_bytes=65536)
    reservation = _load_json(reservation_raw, "reservation")
    if reservation != {
        "schema": RESERVATION_SCHEMA,
        "set_id": set_id,
        "state": "incomplete",
    }:
        raise RecoverySetError("set_reservation_invalid")
    index_raw = writer.read_bytes(f"{base}/INDEX.json", max_bytes=MAX_SPEC_BYTES)
    index = _load_json(index_raw, "index")
    complete_raw = writer.read_bytes(f"{base}/COMPLETE.json", max_bytes=65536)
    complete = _load_json(complete_raw, "complete")
    if (
        set(index)
        != {
            "schema",
            "set_id",
            "volume_uuid",
            "root_id",
            "research_commit",
            "owner_commit",
            "spec",
            "created_ns",
            "members",
            "coverage",
            "tool_sha256",
            "restore_witness_sha256",
            "application_recovery_qualified",
            "source_root_closure_verified",
            "factory_reset_ready",
            "consistency_scope",
        }
        or type(index.get("created_ns")) is not int
        or index.get("application_recovery_qualified") is not False
        or index.get("source_root_closure_verified") is not False
        or index.get("factory_reset_ready") is not False
        or index.get("consistency_scope")
        != "independent_recovery_points_no_cross_root_or_application_atomicity"
        or set(complete)
        != {
            "schema",
            "set_id",
            "volume_uuid",
            "root_id",
            "index_sha256",
            "reservation_sha256",
            "completed_ns",
        }
        or type(complete.get("completed_ns")) is not int
        or index.get("schema") != SCHEMA
        or index.get("set_id") != set_id
        or index.get("volume_uuid") != expected_volume_uuid
        or index.get("root_id") != expected_root_id
        or sha256(index_raw) != complete.get("index_sha256")
        or complete.get("schema") != COMPLETE_SCHEMA
        or complete.get("set_id") != set_id
        or complete.get("volume_uuid") != expected_volume_uuid
        or complete.get("root_id") != expected_root_id
        or complete.get("reservation_sha256") != sha256(reservation_raw)
    ):
        raise RecoverySetError("set_identity_or_hash_mismatch")
    spec = _validate_spec(index["spec"])
    if (
        index["research_commit"] != spec["research_commit"]
        or index["owner_commit"] != spec["owner_commit"]
        or index["restore_witness_sha256"] is not None
    ):
        raise RecoverySetError("set_index_contract_invalid")
    prepared = _prepare_details(writer, spec["members"])
    members = [item for item, _ in prepared]
    if members != index["members"]:
        raise RecoverySetError("set_member_pin_mismatch")
    if index.get("tool_sha256") != _tool_hashes():
        raise RecoverySetError("set_verifier_identity_mismatch")
    coverage = _validate_coverage(spec["members"], prepared)
    if coverage != index["coverage"]:
        raise RecoverySetError("set_coverage_changed")
    prepared_after = _prepare_details(writer, spec["members"])
    if [item for item, _ in prepared_after] != members:
        raise RecoverySetError("set_member_changed_during_verify")
    if revalidate_identity is not None and revalidate_identity() != (
        expected_volume_uuid,
        expected_root_id,
    ):
        raise RecoverySetError("set_identity_changed")
    restore_witness_bound = False
    restore_external_root_binding_count = 0
    if witness_id is not None:
        if witness_targets is None:
            raise RecoverySetError("restore_witness_targets_required")
        members_by_role = {item["role"]: item for item in spec["members"]}
        _witness_hash, restore_external_root_binding_count = _verify_restore_witness(
            writer,
            set_id,
            witness_id,
            index_sha256=sha256(index_raw),
            expected_volume_uuid=expected_volume_uuid,
            expected_root_id=expected_root_id,
            members_by_role=members_by_role,
            targets=witness_targets,
            external_root_bindings=external_root_bindings,
        )
        restore_witness_bound = True
    elif witness_targets is not None:
        raise RecoverySetError("restore_witness_id_required")
    elif external_root_bindings is not None:
        raise RecoverySetError("restore_witness_id_required")
    return {
        "schema": SCHEMA,
        "set_id": set_id,
        "members_verified": True,
        **coverage,
        "restore_witness_bound": restore_witness_bound,
        "restore_mapping_obligations_resolved": restore_witness_bound,
        "restore_external_root_binding_count": restore_external_root_binding_count,
        "application_recovery_qualified": False,
        "source_root_closure_verified": False,
        "factory_reset_ready": False,
        "member_count": len(members),
        "complete": True,
        "index_sha256": sha256(index_raw),
    }


def create_set(
    writer: DescriptorWriter,
    spec_value: dict[str, Any],
    *,
    expected_volume_uuid: str,
    expected_root_id: str,
    revalidate_identity: Callable[[], tuple[str, str]],
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    spec = _validate_spec(spec_value)
    if (spec["volume_uuid"], spec["root_id"]) != (expected_volume_uuid, expected_root_id):
        raise RecoverySetError("set_identity_pin_mismatch")
    if revalidate_identity() != (expected_volume_uuid, expected_root_id):
        raise RecoverySetError("set_identity_changed")
    prepared = _prepare_details(writer, spec["members"])
    members = [item for item, _ in prepared]
    coverage = _validate_coverage(spec["members"], prepared)
    # Members can verify correctly while required source obligations remain open;
    # preserve the evidence but do not call the set fully resolved.
    tools = _tool_hashes()
    base = f"{BASE}/{spec['set_id']}"
    writer.mkdir(BASE)
    with writer.lock(f"{BASE}/.index.lock"):
        try:
            writer.list_names(base)
        except FileNotFoundError:
            pass
        else:
            raise RecoverySetError("set_id_collision")
        writer.mkdir(base)
        reservation = canonical_json(
            {"schema": RESERVATION_SCHEMA, "set_id": spec["set_id"], "state": "incomplete"}
        )
        writer.create(f"{base}/RESERVATION.json", reservation)
        if fault_hook:
            fault_hook("after_reservation")
        created_ns = time.time_ns()
        index = {
            "schema": SCHEMA,
            "set_id": spec["set_id"],
            "volume_uuid": expected_volume_uuid,
            "root_id": expected_root_id,
            "research_commit": spec["research_commit"],
            "owner_commit": spec["owner_commit"],
            "spec": spec,
            "created_ns": created_ns,
            "members": members,
            "coverage": coverage,
            "tool_sha256": tools,
            "restore_witness_sha256": None,
            "application_recovery_qualified": False,
            "source_root_closure_verified": False,
            "factory_reset_ready": False,
            "consistency_scope": (
                "independent_recovery_points_no_cross_root_or_application_atomicity"
            ),
        }
        raw = canonical_json(index)
        if len(raw) > MAX_SPEC_BYTES:
            raise RecoverySetError("set_index_size_limit")
        if revalidate_identity() != (expected_volume_uuid, expected_root_id):
            raise RecoverySetError("set_identity_changed")
        writer.create(f"{base}/INDEX.json", raw)
        if writer.read_bytes(f"{base}/INDEX.json", max_bytes=MAX_SPEC_BYTES) != raw:
            raise RecoverySetError("set_index_readback_mismatch")
        if fault_hook:
            fault_hook("after_index")
        prepared_after = _prepare_details(writer, spec["members"])
        if [item for item, _ in prepared_after] != members:
            raise RecoverySetError("set_member_changed_before_complete")
        coverage_after = _validate_coverage(spec["members"], prepared_after)
        if coverage_after != coverage:
            raise RecoverySetError("set_coverage_changed_before_complete")
        if revalidate_identity() != (expected_volume_uuid, expected_root_id):
            raise RecoverySetError("set_identity_changed")
        complete = canonical_json(
            {
                "schema": COMPLETE_SCHEMA,
                "set_id": spec["set_id"],
                "volume_uuid": expected_volume_uuid,
                "root_id": expected_root_id,
                "index_sha256": sha256(raw),
                "reservation_sha256": sha256(reservation),
                "completed_ns": time.time_ns(),
            }
        )
        writer.create(f"{base}/COMPLETE.json", complete)
        if writer.read_bytes(f"{base}/COMPLETE.json", max_bytes=65536) != complete:
            raise RecoverySetError("set_complete_readback_mismatch")
    return {
        "schema": SCHEMA,
        "set_id": spec["set_id"],
        "members_verified": True,
        **coverage,
        "restore_witness_bound": False,
        "restore_mapping_obligations_resolved": False,
        "application_recovery_qualified": False,
        "source_root_closure_verified": False,
        "factory_reset_ready": False,
        "member_count": len(members),
        "complete": True,
        "index_sha256": sha256(raw),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--storage-config", type=Path, required=True)
    create.add_argument("--spec", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--storage-config", type=Path, required=True)
    verify.add_argument("--set-id", required=True)
    verify.add_argument("--witness-id")
    verify.add_argument("--owner-target", type=Path)
    verify.add_argument("--research-target", type=Path)
    verify.add_argument("--private-config-target", type=Path)
    verify.add_argument("--launchagent-target", type=Path)
    verify.add_argument("--protected-target", type=Path)
    verify.add_argument("--sqlite-target", type=Path)
    verify.add_argument("--external-bindings-json", type=Path)
    bind_witness = sub.add_parser("bind-witness")
    bind_witness.add_argument("--storage-config", type=Path, required=True)
    bind_witness.add_argument("--set-id", required=True)
    bind_witness.add_argument("--witness-id", required=True)
    bind_witness.add_argument("--owner-target", type=Path, required=True)
    bind_witness.add_argument("--research-target", type=Path, required=True)
    bind_witness.add_argument("--private-config-target", type=Path, required=True)
    bind_witness.add_argument("--launchagent-target", type=Path, required=True)
    bind_witness.add_argument("--protected-target", type=Path, required=True)
    bind_witness.add_argument("--sqlite-target", type=Path, required=True)
    bind_witness.add_argument("--external-bindings-json", type=Path)
    args = parser.parse_args(argv)
    try:
        from tools.brain_host_tree_custody import _storage_writer

        with _storage_writer(args.storage_config) as writer:
            guard = writer._custody_mutation_guard.guard

            def revalidate_identity() -> tuple[str, str]:
                resolved = guard.revalidate()
                return resolved.volume_uuid, resolved.root_id

            if args.command == "create":
                result = create_set(
                    writer,
                    _load_spec(args.spec),
                    expected_volume_uuid=guard.volume_uuid,
                    expected_root_id=guard.root_id,
                    revalidate_identity=revalidate_identity,
                )
            elif args.command == "bind-witness":
                result = bind_restore_witness(
                    writer,
                    args.set_id,
                    args.witness_id,
                    {
                        "owner_work": args.owner_target,
                        "research_work": args.research_target,
                        "private_config": args.private_config_target,
                        "launchagent_definitions": args.launchagent_target,
                        "protected_d43": args.protected_target,
                        "sqlite_backup": args.sqlite_target,
                    },
                    expected_volume_uuid=guard.volume_uuid,
                    expected_root_id=guard.root_id,
                    revalidate_identity=revalidate_identity,
                    external_root_bindings=_load_external_bindings(args.external_bindings_json),
                )
            else:
                witness_targets = None
                external_root_bindings = None
                if args.witness_id:
                    if not all(
                        (
                            args.owner_target,
                            args.research_target,
                            args.private_config_target,
                            args.launchagent_target,
                            args.protected_target,
                            args.sqlite_target,
                        )
                    ):
                        raise RecoverySetError("restore_witness_targets_required")
                    witness_targets = {
                        "owner_work": args.owner_target,
                        "research_work": args.research_target,
                        "private_config": args.private_config_target,
                        "launchagent_definitions": args.launchagent_target,
                        "protected_d43": args.protected_target,
                        "sqlite_backup": args.sqlite_target,
                    }
                    external_root_bindings = _load_external_bindings(args.external_bindings_json)
                elif args.external_bindings_json:
                    raise RecoverySetError("restore_witness_id_required")
                result = verify_set(
                    writer,
                    args.set_id,
                    expected_volume_uuid=guard.volume_uuid,
                    expected_root_id=guard.root_id,
                    revalidate_identity=revalidate_identity,
                    witness_id=args.witness_id,
                    witness_targets=witness_targets,
                    external_root_bindings=external_root_bindings,
                )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        code = str(exc) if isinstance(exc, (RecoverySetError, StorageError)) else "operation_failed"
        print(json.dumps({"error": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
