#!/usr/bin/env python3
"""Global held-view inventory and candidate capture for tree capture v5.

The engine borrows every present root from a pinned HeldDataT view, inventories
all roots without following links into descriptor-bound SQLite, reconciles
hardlinks globally, then streams one payload per complete included inode group.
It publishes create-only evidence candidates and supports independent readback
and inert data extraction. Existing v1/v3/v4 behavior remains unchanged.
"""

from __future__ import annotations

import base64
import ctypes
import errno
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root))
    sys.path.insert(0, str(_repo_root / "src"))

from memecoin_research_storage import ReadOnlyStorageGuard, StorageGuard
from tools import brain_host_tree_custody as v2
from tools import brain_host_tree_custody_v3 as v3
from tools.brain_host_held_data_t_v1 import (
    REQUIRED_ROOT_ROLES,
    ROOT_KINDS,
    DarwinDataOS,
    HeldDataT,
    approved_data_plan_sha256,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SCHEMA = "brain-host-tree-capture-v5-inventory"
SPOOL_BASE = "host-recovery/tree-capture-v5/spool"
MAX_ENTRIES = 2_000_000
MAX_SOURCE_BYTES = 2 * 1024**4
MAX_SCRATCH_BYTES = 32 * 1024**3
MAX_METADATA_TOTAL_BYTES = 8 * 1024**3
CONSERVATIVE_SCALE_DB_BYTES_PER_ROW = 250
OBSERVED_LONG_PATH_DB_BYTES = 249_417_728
OBSERVED_LONG_PATH_ROW_COUNT = 1_000_000
MODELED_DB_BYTES_PER_ROW = 512
MODELED_MEMBER_JSON_FIXED_BYTES = 768
MODELED_PAYLOAD_PAGE_OVERHEAD_BYTES = 5_120
RESOURCE_MODEL_MARGIN_BPS = 5_000
MAX_RELATIVE_PATH_BYTES = 32 * 1024
MAX_PATH_DEPTH = 128
DB_BASENAME = "inventory.sqlite"
CANDIDATE_BASE = "host-recovery/tree-capture-v5/candidates"
_PRODUCTION_CAPTURE_AUTHORITY = object()
_CAPACITY_PLAN_PRODUCTION_AUTHORITY = object()
_BOUND_V2_CAPTURE_AUTHORITY = object()
STREAM_CHUNK_BYTES = 1024 * 1024
PAGE_RECORDS = 256
MAX_PAGE_BYTES = 16 * 1024 * 1024
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_METADATA_VALUE_BYTES = 8 * 1024 * 1024
MAX_OBJECT_METADATA_BYTES = 32 * 1024 * 1024
MAX_CANDIDATE_JSON_BYTES = 256 * 1024**3
MAX_PLANNED_NEW_ENTRIES = 16_000_000
MAX_APFS_ALLOCATION_RESERVE_BYTES = 1024**4
APFS_ALLOCATION_FORMULA_VERSION = "operational-reserve-v1"
APFS_ALLOCATION_BASE_BYTES = 8 * 1024**3
APFS_ALLOCATION_PER_ENTRY_BYTES = 32 * 1024
APFS_ALLOCATION_RATIO_DENOMINATOR = 10
GIT_OPAQUE_WAIVER = {
    "role": "research_checkout",
    "path": ".git",
    "disposition": "opaque_unresolved",
    "unresolved_hardlink_group_count": 6053,
    "census_locator": "docs/evidence/portability-dr-root-metadata-classes-2026-10-16T0352Z.json",
    "census_sha256": "de9c8cb336658754c0931073a18d5e0bb209fcc5292f313cbf7ce8dd5b1729d9",
    "observed_at": "2026-10-16T03:52Z",
}


class TreeCaptureV5Error(ValueError):
    """Stable fail-closed errors for the global held-view capture engine."""


def apfs_allocation_reserve_floor_v5(
    payload_bytes: int,
    metadata_bytes: int,
    candidate_json_bytes: int,
    spool_bytes: int,
    new_entry_count: int,
) -> int:
    """Conservative operational APFS reserve; not a mathematical allocation bound."""
    values = (payload_bytes, metadata_bytes, candidate_json_bytes, spool_bytes, new_entry_count)
    if any(type(value) is not int or value < 0 for value in values):
        raise TreeCaptureV5Error("apfs_allocation_budget_invalid")
    if (
        payload_bytes > MAX_SOURCE_BYTES
        or metadata_bytes > MAX_METADATA_TOTAL_BYTES
        or candidate_json_bytes > MAX_CANDIDATE_JSON_BYTES
        or spool_bytes > MAX_SCRATCH_BYTES
        or new_entry_count > MAX_PLANNED_NEW_ENTRIES
    ):
        raise TreeCaptureV5Error("apfs_allocation_budget_limit")
    variable_bytes = payload_bytes + metadata_bytes + candidate_json_bytes + spool_bytes
    ratio_reserve = (variable_bytes + APFS_ALLOCATION_RATIO_DENOMINATOR - 1) // (
        APFS_ALLOCATION_RATIO_DENOMINATOR
    )
    reserve = (
        APFS_ALLOCATION_BASE_BYTES
        + APFS_ALLOCATION_PER_ENTRY_BYTES * new_entry_count
        + ratio_reserve
    )
    if reserve > MAX_APFS_ALLOCATION_RESERVE_BYTES:
        raise TreeCaptureV5Error("apfs_allocation_reserve_limit")
    return reserve


def planned_new_entry_count_v5(
    json_sizes: Mapping[str, int], existing_target_directories: Mapping[str, bool]
) -> int:
    """Count planned regular files/directories plus the spool child and DB."""
    directory_keys = {"host-recovery", "tree-capture-v5", "candidates", "spool"}
    if set(existing_target_directories) != directory_keys or any(
        type(value) is not bool for value in existing_target_directories.values()
    ):
        raise TreeCaptureV5Error("capacity_plan_target_directory_state_invalid")
    numeric_fields = (
        "payload_page_file_count",
        "sidecar_file_count",
        "member_page_count",
        "payload_index_page_count",
        "payload_directory_count",
        "metadata_directory_count",
    )
    if any(type(json_sizes.get(key)) is not int or json_sizes[key] < 0 for key in numeric_fields):
        raise TreeCaptureV5Error("capacity_plan_output_count_invalid")
    file_count = (
        json_sizes["payload_page_file_count"]
        + json_sizes["sidecar_file_count"]
        + json_sizes["member_page_count"]
        + json_sizes["payload_index_page_count"]
        + 2  # INDEX.json and READBACK.json
    )
    directory_count = (
        1  # candidate id directory
        + int(json_sizes["payload_page_file_count"] > 0)
        + json_sizes["payload_directory_count"]
        + int(json_sizes["sidecar_file_count"] > 0)
        + json_sizes["metadata_directory_count"]
        + int(json_sizes["member_page_count"] > 0)
        + int(json_sizes["payload_index_page_count"] > 0)
    )
    # The spool child and its SQLite DB are both newly created. Missing shared
    # ancestors/area directories are also charged once for this operation.
    new_count = (
        file_count
        + directory_count
        + 2
        + sum(not exists for exists in existing_target_directories.values())
    )
    if new_count > MAX_PLANNED_NEW_ENTRIES:
        raise TreeCaptureV5Error("capacity_plan_new_entry_count_limit")
    return new_count


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def model_candidate_capacity_v5(
    entry_count: int,
    source_bytes: int,
    *,
    average_path_chars: int = 128,
    metadata_reserve_bytes: int = MAX_METADATA_TOTAL_BYTES,
) -> dict[str, int]:
    """Conservative estimate using the longer-path synthetic 1M-row probe.

    That probe used 1M ``synthetic/{i:07d}.json`` paths and measured
    249,417,728 bytes (249.42 bytes per row), rounded up to a 250-byte baseline.
    This estimates metadata/index overhead only. `source_bytes` is the streamed
    payload reserve. The model includes
    member JSON and a conservative 5 KiB per potential payload page for index
    and file allocation overhead, then adds 50 percent headroom. The bounded
    xattr/ACL sidecar reserve is added separately, up to 8 GiB.
    """
    if type(entry_count) is not int or not 0 <= entry_count <= MAX_ENTRIES:
        raise TreeCaptureV5Error("capacity_model_entry_count_invalid")
    if type(source_bytes) is not int or not 0 <= source_bytes <= MAX_SOURCE_BYTES:
        raise TreeCaptureV5Error("capacity_model_source_bytes_invalid")
    if (
        type(metadata_reserve_bytes) is not int
        or not 0 <= metadata_reserve_bytes <= MAX_METADATA_TOTAL_BYTES
    ):
        raise TreeCaptureV5Error("capacity_model_metadata_reserve_invalid")
    if (
        type(average_path_chars) is not int
        or not 0 <= average_path_chars <= MAX_RELATIVE_PATH_BYTES
    ):
        raise TreeCaptureV5Error("capacity_model_path_length_invalid")
    payload_page_count = entry_count + (source_bytes + STREAM_CHUNK_BYTES - 1) // STREAM_CHUNK_BYTES
    database_bytes = entry_count * MODELED_DB_BYTES_PER_ROW
    member_page_bytes = entry_count * (MODELED_MEMBER_JSON_FIXED_BYTES + 6 * average_path_chars)
    payload_index_bytes = payload_page_count * MODELED_PAYLOAD_PAGE_OVERHEAD_BYTES
    unbuffered = database_bytes + member_page_bytes + payload_index_bytes
    modeled_scratch = max(
        1024 * 1024,
        (unbuffered * (10_000 + RESOURCE_MODEL_MARGIN_BPS) + 9_999) // 10_000,
    )
    return {
        "entry_count": entry_count,
        "source_payload_bytes": source_bytes,
        "metadata_sidecar_reserve_bytes": metadata_reserve_bytes,
        "conservative_database_bytes_per_row": CONSERVATIVE_SCALE_DB_BYTES_PER_ROW,
        "observed_long_path_database_bytes": OBSERVED_LONG_PATH_DB_BYTES,
        "observed_long_path_row_count": OBSERVED_LONG_PATH_ROW_COUNT,
        "modeled_database_bytes": database_bytes,
        "modeled_member_page_bytes": member_page_bytes,
        "modeled_payload_page_overhead_bytes": payload_index_bytes,
        "margin_bps": RESOURCE_MODEL_MARGIN_BPS,
        "minimum_scratch_reserve_bytes": modeled_scratch,
        "minimum_total_reserve_bytes": modeled_scratch + metadata_reserve_bytes + source_bytes,
    }


def policy_roster_sha256_v5(plan_sha256: str, role_rows: list[Mapping[str, Any]]) -> str:
    """Hash the canonical plan-bound policy roster carried by a v5 candidate."""
    if not _is_sha256(plan_sha256) or not isinstance(role_rows, list):
        raise TreeCaptureV5Error("policy_roster_invalid")
    if len(role_rows) != len(REQUIRED_ROOT_ROLES):
        raise TreeCaptureV5Error("policy_roster_invalid")
    roles: dict[str, dict[str, Any]] = {}
    for row in role_rows:
        if not isinstance(row, Mapping):
            raise TreeCaptureV5Error("policy_roster_invalid")
        role = row.get("role")
        present = row.get("present")
        if role not in REQUIRED_ROOT_ROLES or role in roles or type(present) is not bool:
            raise TreeCaptureV5Error("policy_roster_invalid")
        if present:
            scope = row.get("scope")
            policy_digest = row.get("policy_sha256")
            if scope not in {"non_protected", "protected_opaque"} or not _is_sha256(policy_digest):
                raise TreeCaptureV5Error("policy_roster_present_role_invalid")
            roles[role] = {
                "present": True,
                "scope": scope,
                "policy_sha256": policy_digest,
            }
        else:
            reason_digest = row.get("unresolved_reason_sha256")
            if (
                row.get("disposition") != "absent_unresolved"
                or not _is_sha256(reason_digest)
                or row.get("scope") is not None
                or row.get("policy_sha256") is not None
            ):
                raise TreeCaptureV5Error("policy_roster_absent_role_invalid")
            roles[role] = {
                "present": False,
                "disposition": "absent_unresolved",
                "unresolved_reason_sha256": reason_digest,
            }
    if set(roles) != REQUIRED_ROOT_ROLES:
        raise TreeCaptureV5Error("policy_roster_invalid")
    return sha256(
        canonical_json(
            {
                "schema": "brain-host-tree-capture-v5-policy-roster",
                "version": 1,
                "plan_sha256": plan_sha256,
                "roles": {role: roles[role] for role in sorted(roles)},
            }
        )
    )


@dataclass(frozen=True)
class RoleCapturePolicy:
    policy_path: Path
    expected_sha256: str
    scope: str = "non_protected"


def _validate_policy_pin(policy: RoleCapturePolicy) -> tuple[dict[str, Any], bytes, str]:
    if not isinstance(policy, RoleCapturePolicy) or policy.scope not in {
        "non_protected",
        "protected_opaque",
    }:
        raise TreeCaptureV5Error("role_policy_invalid")
    try:
        value, raw, digest = v3._load_policy(Path(policy.policy_path))
    except Exception as exc:
        raise TreeCaptureV5Error("role_policy_read_failed") from exc
    if digest != policy.expected_sha256:
        raise TreeCaptureV5Error("role_policy_pin_mismatch")
    return value, raw, digest


def _db_open_at(directory_fd: int, device: int) -> tuple[sqlite3.Connection, int]:
    try:
        fd = os.open(
            DB_BASENAME,
            os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise TreeCaptureV5Error("spool_database_create_failed") from exc
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_dev != device
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600
            or st.st_nlink != 1
        ):
            raise TreeCaptureV5Error("spool_database_identity_invalid")
        db_path = f"/dev/fd/{fd}"
        db: sqlite3.Connection | None = None
        db = sqlite3.connect(db_path)
        journal_mode = db.execute("PRAGMA journal_mode=OFF").fetchone()
        if journal_mode != ("off",):
            raise TreeCaptureV5Error("spool_journal_mode_unavailable")
        db.execute("PRAGMA synchronous=OFF")
        db.execute("PRAGMA temp_store=MEMORY")
        if db.execute("PRAGMA temp_store").fetchone() != (2,):
            raise TreeCaptureV5Error("spool_temp_store_not_memory")
        db.execute("PRAGMA cache_size=-8192")
        db.execute("PRAGMA mmap_size=0")
        database_list = db.execute("PRAGMA database_list").fetchone()
        if not database_list or database_list[2] != db_path:
            raise TreeCaptureV5Error("spool_database_path_unbound")
        db.execute(
            "CREATE TABLE members(role TEXT NOT NULL,path TEXT NOT NULL,kind TEXT NOT NULL,"
            "state TEXT NOT NULL,rule TEXT,disposition TEXT,device INTEGER NOT NULL,"
            "inode INTEGER NOT NULL,nlink INTEGER NOT NULL,size INTEGER NOT NULL,"
            "mode INTEGER NOT NULL,uid INTEGER NOT NULL,gid INTEGER NOT NULL,"
            "mtime_ns INTEGER NOT NULL,"
            "ctime_ns INTEGER NOT NULL,atime_ns INTEGER NOT NULL,birthtime_ns INTEGER NOT NULL,"
            "flags INTEGER NOT NULL,sig BLOB NOT NULL,link_text BLOB,"
            "group_id TEXT,primary_role TEXT,primary_path TEXT,"
            "payload_id TEXT,"
            "payload_sha256 TEXT,payload_bytes INTEGER,metadata_json BLOB,"
            "metadata_state TEXT NOT NULL DEFAULT 'pending',"
            "xattr_names BLOB,acl_state TEXT,flags_state TEXT,PRIMARY KEY(role,path))"
        )
        db.execute("CREATE INDEX members_inode ON members(device,inode)")
        db.execute("CREATE INDEX members_payload_id ON members(payload_id)")
        db.execute("CREATE INDEX members_role_state ON members(role,state,kind)")
        db.execute(
            "CREATE TABLE pending_dirs("
            "role TEXT NOT NULL,path TEXT NOT NULL,PRIMARY KEY(role,path))"
        )
        db.execute(
            "CREATE TABLE payload_pages(payload_id TEXT NOT NULL,page INTEGER NOT NULL,"
            "path TEXT NOT NULL,sha256 TEXT NOT NULL,size_bytes INTEGER NOT NULL,"
            "PRIMARY KEY(payload_id,page))"
        )
        return db, fd
    except BaseException:
        if db is not None:
            db.close()
        database_identity = os.fstat(fd)
        os.close(fd)
        try:
            current = os.stat(DB_BASENAME, dir_fd=directory_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (
                database_identity.st_dev,
                database_identity.st_ino,
            ):
                os.unlink(DB_BASENAME, dir_fd=directory_fd)
        except OSError:
            pass
        raise


def _safe_name(value: str) -> str:
    try:
        name = v3._path_component(value)
    except ValueError as exc:
        raise TreeCaptureV5Error("source_component_invalid") from exc
    if len(os.fsencode(name)) > MAX_RELATIVE_PATH_BYTES:
        raise TreeCaptureV5Error("source_path_limit")
    return name


def _kind(st: os.stat_result) -> str:
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    if stat.S_ISREG(st.st_mode):
        return "file"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    raise TreeCaptureV5Error("source_special_file_rejected")


def _insert_member(
    db: sqlite3.Connection,
    role: str,
    path: str,
    kind: str,
    st: os.stat_result,
    *,
    state: str,
    rule: str | None,
    disposition: str | None,
    link_text: bytes | None = None,
) -> None:
    flags = v2._validated_source_flags(st)
    sig = canonical_json(list(v2._sig(st)))
    db.execute(
        "INSERT INTO members(role,path,kind,state,rule,disposition,device,inode,nlink,size,"
        "mode,uid,gid,mtime_ns,ctime_ns,atime_ns,birthtime_ns,flags,sig,link_text,payload_id) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            role,
            path,
            kind,
            state,
            rule,
            disposition,
            st.st_dev,
            st.st_ino,
            st.st_nlink,
            st.st_size,
            stat.S_IMODE(st.st_mode),
            st.st_uid,
            st.st_gid,
            st.st_mtime_ns,
            st.st_ctime_ns,
            st.st_atime_ns,
            getattr(st, "st_birthtime_ns", 0),
            flags,
            sig,
            link_text,
            sha256(f"{role}\0{path}".encode()) if kind == "file" and state == "included" else None,
        ),
    )


def _check_spool_size(db: sqlite3.Connection, db_fd: int, spool_fd: int, limit: int) -> None:
    db.commit()
    os.fsync(db_fd)
    db_stat = os.fstat(db_fd)
    if db_stat.st_size > limit:
        raise TreeCaptureV5Error("scratch_resource_limit")
    with os.scandir(spool_fd) as scan:
        entries = [
            (entry.name, os.stat(entry.name, dir_fd=spool_fd, follow_symlinks=False))
            for entry in scan
        ]
    if len(entries) != 1 or entries[0][0] != DB_BASENAME:
        raise TreeCaptureV5Error("spool_sidecar_or_unexpected_entry")
    actual = entries[0][1]
    if (actual.st_dev, actual.st_ino) != (db_stat.st_dev, db_stat.st_ino):
        raise TreeCaptureV5Error("spool_database_replaced")


def _allocated_bytes_from_stat(st: os.stat_result) -> int:
    """Return durable filesystem allocation in Darwin/POSIX 512-byte units."""
    blocks = getattr(st, "st_blocks", None)
    if type(blocks) is not int or blocks < 0:
        raise TreeCaptureV5Error("allocated_storage_measurement_unavailable")
    return blocks * 512


def _remove_spool_files(
    spool_fd: int, device: int, database_identity: tuple[int, int] | None = None
) -> None:
    first_error: BaseException | None = None
    with os.scandir(spool_fd) as scan:
        names = (entry.name for entry in scan)
        for name in names:
            try:
                st = os.stat(name, dir_fd=spool_fd, follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode) or st.st_dev != device:
                    raise TreeCaptureV5Error("spool_unexpected_entry")
                if (
                    name != DB_BASENAME
                    or not stat.S_ISREG(st.st_mode)
                    or st.st_uid != os.getuid()
                    or stat.S_IMODE(st.st_mode) != 0o600
                    or database_identity is None
                    or (st.st_dev, st.st_ino) != database_identity
                ):
                    raise TreeCaptureV5Error("spool_database_replaced")
                os.unlink(name, dir_fd=spool_fd)
            except BaseException as exc:
                first_error = first_error or exc
    if first_error is not None:
        raise TreeCaptureV5Error("spool_cleanup_incomplete") from first_error


def _cleanup_spool_directory(
    parent_fd: int,
    name: str,
    spool_fd: int,
    database_identity: tuple[int, int] | None = None,
) -> None:
    held = os.fstat(spool_fd)
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
        held.st_dev,
        held.st_ino,
    ):
        raise TreeCaptureV5Error("spool_directory_replaced_during_cleanup")
    _remove_spool_files(spool_fd, held.st_dev, database_identity)
    os.rmdir(name, dir_fd=parent_fd)


def _validate_policy_membership(
    db: sqlite3.Connection, role: str, policy: Mapping[str, Any]
) -> None:
    for path in policy["pem_candidate_dispositions"]:
        if role == "research_checkout" and _inside_research_git_waiver(path):
            continue
        found = db.execute(
            "SELECT kind FROM members WHERE role=? AND path=?", (role, path)
        ).fetchone()
        if found is None or found[0] != "file":
            raise TreeCaptureV5Error("policy_pem_candidate_unmatched")
    for path, declaration in policy["exact_path_exclusions"].items():
        if role == "research_checkout" and _inside_research_git_waiver(path):
            continue
        found = db.execute(
            "SELECT kind,state,disposition FROM members WHERE role=? AND path=?", (role, path)
        ).fetchone()
        if found is None or found[0] != declaration["entry_type"] or found[1] == "included":
            raise TreeCaptureV5Error("policy_exact_exclusion_unmatched")
        if found[2] != declaration["disposition"]:
            raise TreeCaptureV5Error("policy_exact_exclusion_disposition_mismatch")


def _inside_research_git_waiver(path: str) -> bool:
    return path == ".git" or path.startswith(".git/")


def _classify_path_v5(
    role: str, path: str, kind: str, policy: Mapping[str, Any], protected_scope: bool
) -> tuple[str, str | None, str | None]:
    if role == "research_checkout" and (path == ".git" or path.startswith(".git/")):
        return (
            "excluded_subtree" if kind == "directory" else "excluded_file",
            "opaque_git_waiver",
            "opaque_unresolved_census_groups",
        )
    return v3._classify_path(path, kind, dict(policy), protected_scope=protected_scope)


def _scan_role(
    db: sqlite3.Connection,
    view: HeldDataT,
    role: str,
    root_fd: int,
    policy: Mapping[str, Any],
    scope: str,
    *,
    max_entries: int,
    db_fd: int,
    spool_fd: int,
    scratch_limit: int,
    fast_boundary_check: Callable[[], None] | None = None,
) -> tuple[tuple[int, ...], int]:
    root_stat = os.fstat(root_fd)
    if root_stat.st_dev != os.fstat(view.mount_fd).st_dev:
        raise TreeCaptureV5Error("source_mount_boundary")
    root_kind = ROOT_KINDS[role]
    if root_kind == "directory" and not stat.S_ISDIR(root_stat.st_mode):
        raise TreeCaptureV5Error("source_root_kind_changed")
    if root_kind == "file" and not stat.S_ISREG(root_stat.st_mode):
        raise TreeCaptureV5Error("source_root_kind_changed")
    state, rule, disposition = _classify_path_v5(
        role, "", root_kind, policy, scope == "protected_opaque"
    )
    _insert_member(
        db, role, "", root_kind, root_stat, state=state, rule=rule, disposition=disposition
    )
    count = 1
    included_bytes = root_stat.st_size if root_kind == "file" and state == "included" else 0
    if root_kind == "file":
        _validate_policy_membership(db, role, policy)
        return tuple(v2._sig(root_stat)), included_bytes

    if state != "included":
        _validate_policy_membership(db, role, policy)
        return tuple(v2._sig(root_stat)), included_bytes
    db.execute("INSERT INTO pending_dirs VALUES(?,?)", (role, ""))
    while row := db.execute(
        "SELECT path FROM pending_dirs WHERE role=? ORDER BY path COLLATE BINARY LIMIT 1",
        (role,),
    ).fetchone():
        prefix = row[0]
        db.execute("DELETE FROM pending_dirs WHERE role=? AND path=?", (role, prefix))
        directory_fd = _open_directory_relative(root_fd, prefix)
        try:
            with os.scandir(directory_fd) as scan:
                for entry in scan:
                    name = _safe_name(entry.name)
                    relative = f"{prefix}/{name}" if prefix else name
                    try:
                        v3._relative(relative)
                    except ValueError as exc:
                        raise TreeCaptureV5Error("source_path_invalid") from exc
                    if (
                        len(os.fsencode(relative)) > MAX_RELATIVE_PATH_BYTES
                        or len(relative.split("/")) > MAX_PATH_DEPTH
                    ):
                        raise TreeCaptureV5Error("source_path_limit")
                    if v2._sig(os.fstat(root_fd)) != v2._sig(root_stat):
                        raise TreeCaptureV5Error("source_root_changed_during_inventory")
                    try:
                        st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    except OSError as exc:
                        raise TreeCaptureV5Error("source_inventory_changed") from exc
                    if st.st_dev != root_stat.st_dev:
                        raise TreeCaptureV5Error("source_mount_boundary")
                    kind = _kind(st)
                    state, rule, disposition = _classify_path_v5(
                        role, relative, kind, policy, scope == "protected_opaque"
                    )
                    link_text = None
                    if kind == "symlink":
                        try:
                            text = os.readlink(name, dir_fd=directory_fd)
                            link_text = text if isinstance(text, bytes) else os.fsencode(text)
                        except OSError as exc:
                            raise TreeCaptureV5Error("source_symlink_read_failed") from exc
                        after_link = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if v2._sig(after_link) != v2._sig(st):
                            raise TreeCaptureV5Error("source_inventory_changed")
                    elif kind == "file":
                        source_fd = os.open(
                            name,
                            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                            dir_fd=directory_fd,
                        )
                        try:
                            if v2._sig(os.fstat(source_fd)) != v2._sig(st):
                                raise TreeCaptureV5Error("source_inventory_changed")
                        finally:
                            os.close(source_fd)
                    _insert_member(
                        db,
                        role,
                        relative,
                        kind,
                        st,
                        state=state,
                        rule=rule,
                        disposition=disposition,
                        link_text=link_text,
                    )
                    count += 1
                    if fast_boundary_check is not None:
                        fast_boundary_check()
                    if count > max_entries:
                        raise TreeCaptureV5Error("global_entry_limit")
                    if count % 128 == 0:
                        _check_spool_size(db, db_fd, spool_fd, scratch_limit)
                    if state == "included" and kind == "file":
                        included_bytes += st.st_size
                    opaque_research_git = role == "research_checkout" and relative == ".git"
                    if kind == "directory" and state == "included" and not opaque_research_git:
                        child_fd = os.open(
                            name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory_fd,
                        )
                        try:
                            if v2._sig(os.fstat(child_fd)) != v2._sig(st):
                                raise TreeCaptureV5Error("source_inventory_changed")
                        finally:
                            os.close(child_fd)
                        db.execute("INSERT INTO pending_dirs VALUES(?,?)", (role, relative))
                    if v2._sig(os.fstat(root_fd)) != v2._sig(root_stat):
                        raise TreeCaptureV5Error("source_root_changed_during_inventory")
        finally:
            os.close(directory_fd)
        _check_spool_size(db, db_fd, spool_fd, scratch_limit)
    _validate_policy_membership(db, role, policy)
    return tuple(v2._sig(root_stat)), included_bytes


def _open_directory_relative(root_fd: int, relative: str) -> int:
    current = os.dup(root_fd)
    try:
        for component in relative.split("/") if relative else []:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _resolve_global_hardlinks(db: sqlite3.Connection) -> dict[str, int]:
    groups = db.execute(
        "SELECT device,inode,max(nlink),count(*),"
        "sum(CASE WHEN state!='included' THEN 1 ELSE 0 END) "
        ",count(DISTINCT nlink),count(DISTINCT size),count(DISTINCT sig) "
        "FROM members WHERE kind='file' GROUP BY device,inode HAVING max(nlink)>1"
    )
    unresolved = excluded = included = 0
    group_count = 0
    for (
        dev,
        ino,
        nlink,
        aliases,
        excluded_count,
        nlink_variants,
        size_variants,
        sig_variants,
    ) in groups:
        group_count += 1
        if excluded_count:
            excluded += 1
            db.execute(
                "UPDATE members SET state='excluded_file',rule='hardlink_alias_to_excluded',"
                "disposition='unresolved_hardlink_group',group_id=?,payload_id=NULL,"
                "payload_sha256=NULL,payload_bytes=NULL "
                "WHERE device=? AND inode=?",
                (sha256(f"{dev}:{ino}".encode()), dev, ino),
            )
            continue
        if aliases != nlink or nlink_variants != 1 or size_variants != 1 or sig_variants != 1:
            unresolved += 1
            db.execute(
                "UPDATE members SET state='excluded_file',rule='hardlink_group_incomplete',"
                "disposition='unresolved_hardlink_group',group_id=?,payload_id=NULL,"
                "payload_sha256=NULL,payload_bytes=NULL "
                "WHERE device=? AND inode=?",
                (sha256(f"{dev}:{ino}".encode()), dev, ino),
            )
            continue
        included += 1
        primary_role, primary_path = db.execute(
            "SELECT role,path FROM members WHERE device=? AND inode=? AND state='included' "
            "ORDER BY role COLLATE BINARY,path COLLATE BINARY LIMIT 1",
            (dev, ino),
        ).fetchone()
        group_id = sha256(f"{dev}:{ino}".encode())
        db.execute(
            "UPDATE members SET group_id=?,primary_role=?,primary_path=?,payload_id=? "
            "WHERE device=? AND inode=?",
            (
                group_id,
                primary_role,
                primary_path,
                sha256(f"{primary_role}\0{primary_path}".encode()),
                dev,
                ino,
            ),
        )
    db.commit()
    return {
        "hardlink_group_count": group_count,
        "included_complete_group_count": included,
        "excluded_group_count": excluded,
        "unresolved_group_count": unresolved,
    }


def _unique_included_bytes(db: sqlite3.Connection) -> int:
    result = db.execute(
        "SELECT coalesce(sum(size),0) FROM members "
        "WHERE kind='file' AND state='included' "
        "AND (group_id IS NULL OR (role=primary_role AND path=primary_path))"
    ).fetchone()
    return int(result[0])


_INVENTORY_DIGEST_FIELDS = (
    "role,path,kind,state,rule,disposition,device,inode,nlink,size,mode,uid,gid,"
    "mtime_ns,ctime_ns,atime_ns,birthtime_ns,flags,sig,link_text,group_id,"
    "primary_role,primary_path,payload_id"
)


def source_inventory_sha256_v5(
    db: sqlite3.Connection,
    *,
    plan_sha256: str,
    policy_roster_sha256: str,
    root_signatures: Mapping[str, tuple[int, ...]],
) -> str:
    """Hash the metadata-only, hardlink-resolved inventory before payload work."""
    if not _is_sha256(plan_sha256) or not _is_sha256(policy_roster_sha256):
        raise TreeCaptureV5Error("source_inventory_digest_pin_invalid")
    digest = hashlib.sha256()
    header = canonical_json(
        {
            "schema": "brain-host-tree-capture-v5-source-inventory-digest-v1",
            "plan_sha256": plan_sha256,
            "policy_roster_sha256": policy_roster_sha256,
            "root_signatures": {
                role: list(root_signatures[role]) for role in sorted(root_signatures)
            },
        }
    )
    digest.update(len(header).to_bytes(8, "big"))
    digest.update(header)
    query = (
        f"SELECT {_INVENTORY_DIGEST_FIELDS} FROM members "
        "ORDER BY role COLLATE BINARY,path COLLATE BINARY"
    )
    for row in db.execute(query):
        values = [
            base64.b64encode(value).decode("ascii") if isinstance(value, bytes) else value
            for value in row
        ]
        encoded = canonical_json(values)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def bind_metadata_measurement_sha256_v5(
    source_inventory_sha256: str, metadata_measurement_sha256: str
) -> str:
    """Bind size-only xattr/ACL/name measurements to the path inventory pin."""
    if not _is_sha256(source_inventory_sha256) or not _is_sha256(metadata_measurement_sha256):
        raise TreeCaptureV5Error("source_inventory_metadata_digest_invalid")
    return sha256(
        canonical_json(
            {
                "schema": "brain-host-tree-capture-v5-source-inventory-with-metadata-v1",
                "source_inventory_sha256": source_inventory_sha256,
                "metadata_measurement_sha256": metadata_measurement_sha256,
            }
        )
    )


class GlobalInventoryV5:
    """Held descriptors and private global inventory, closed by caller/context."""

    def __init__(
        self,
        *,
        view: HeldDataT,
        writer: DescriptorWriter,
        parent_fd: int,
        spool_fd: int,
        spool_name: str,
        db: sqlite3.Connection,
        db_fd: int,
        borrow_stack: ExitStack,
        roots: dict[str, int],
        root_signatures: dict[str, tuple[int, ...]],
        policy_hashes: dict[str, str],
        policy_scopes: dict[str, str],
        plan_sha256: str,
        policy_roster_sha256: str,
        hardlink_summary: dict[str, int],
        included_source_bytes: int,
        source_inventory_sha256: str,
        planned_metadata_bytes: int | None = None,
        planned_json_bytes: int | None = None,
        reserve_capacity: Callable[[int], None],
        scratch_reserve_bytes: int,
        metadata_reserve_bytes: int,
        payload_reserve_bytes: int,
        verifier_scratch_reserve_bytes: int,
        operation_spec_sha256: str | None = None,
        production_authority: object | None = None,
        production_root_check: Callable[[], None] | None = None,
        production_fast_check: Callable[[int], None] | None = None,
        production_boundary_check: Callable[[int], None] | None = None,
        production_cadence_check: Callable[[int, int, int], None] | None = None,
    ) -> None:
        self.view = view
        self.writer = writer
        self.parent_fd = parent_fd
        self.spool_fd = spool_fd
        self.spool_name = spool_name
        self.db = db
        self.db_fd = db_fd
        self.borrow_stack = borrow_stack
        self.roots = roots
        self.root_signatures = root_signatures
        self.policy_hashes = policy_hashes
        self.policy_scopes = policy_scopes
        self.plan_sha256 = plan_sha256
        self.policy_roster_sha256 = policy_roster_sha256
        self.hardlink_summary = hardlink_summary
        self.included_source_bytes = included_source_bytes
        self.source_inventory_sha256 = source_inventory_sha256
        self.metadata_bytes_remaining = planned_metadata_bytes
        self.json_bytes_remaining = planned_json_bytes
        self.reserve_capacity = reserve_capacity
        self.scratch_reserve_bytes = scratch_reserve_bytes
        self.metadata_reserve_bytes = metadata_reserve_bytes
        self.payload_reserve_bytes = payload_reserve_bytes
        self.verifier_scratch_reserve_bytes = verifier_scratch_reserve_bytes
        self.operation_spec_sha256 = operation_spec_sha256
        self.production_authorized = production_authority is _PRODUCTION_CAPTURE_AUTHORITY
        self.production_root_check = production_root_check
        self.production_fast_check = production_fast_check
        self.production_boundary_check = production_boundary_check
        self.production_cadence_check = production_cadence_check
        self.verify_scratch_fd = spool_fd if self.production_authorized else None
        self.payload_bytes_remaining = included_source_bytes
        self.candidate_write_guard: Callable[[], None] | None = None
        self.entry_count = int(db.execute("SELECT count(*) FROM members").fetchone()[0])
        self._closed = False

    def remaining_capacity_reserve(self) -> int:
        # Commit and flush SQLite before crediting any space already occupied by
        # the spool. st_size is logical length and is not an allocation measure.
        self.db.commit()
        os.fsync(self.db_fd)
        db_stat = os.fstat(self.db_fd)
        if not stat.S_ISREG(db_stat.st_mode) or db_stat.st_nlink != 1:
            raise TreeCaptureV5Error("spool_database_identity_invalid")
        used_spool = _allocated_bytes_from_stat(db_stat)
        spool_remaining = max(0, self.scratch_reserve_bytes - used_spool)
        payload_remaining = self.payload_bytes_remaining
        metadata_remaining = self.metadata_bytes_remaining
        json_remaining = self.json_bytes_remaining
        if metadata_remaining is None or json_remaining is None:
            metadata_remaining = self.metadata_reserve_bytes
            json_remaining = 0
            spool_remaining = self.scratch_reserve_bytes
        return (
            spool_remaining
            + payload_remaining
            + metadata_remaining
            + json_remaining
            + self.payload_reserve_bytes
        )

    def check_remaining_capacity(self) -> None:
        try:
            self.reserve_capacity(self.remaining_capacity_reserve())
        except Exception as exc:
            raise TreeCaptureV5Error("capacity_reserve_recheck_failed") from exc

    def consume_metadata_bytes(self, allocated_bytes: int) -> None:
        if self.metadata_bytes_remaining is None:
            return
        if type(allocated_bytes) is not int or allocated_bytes < 0:
            raise TreeCaptureV5Error("metadata_plan_allocation_invalid")
        self.metadata_bytes_remaining -= min(allocated_bytes, self.metadata_bytes_remaining)

    def consume_json_bytes(self, allocated_bytes: int) -> None:
        if self.json_bytes_remaining is None:
            return
        if type(allocated_bytes) is not int or allocated_bytes < 0:
            raise TreeCaptureV5Error("json_plan_allocation_invalid")
        self.json_bytes_remaining -= min(allocated_bytes, self.json_bytes_remaining)

    def assert_source_stable(self) -> None:
        self.view.revalidate()
        for role, fd in self.roots.items():
            if tuple(v2._sig(os.fstat(fd))) != self.root_signatures[role]:
                raise TreeCaptureV5Error("source_root_changed")

    def public_inventory_receipt(self) -> dict[str, Any]:
        average_path_chars = int(
            self.db.execute("SELECT coalesce(avg(length(path)),0) FROM members").fetchone()[0]
        )
        resource_model = model_candidate_capacity_v5(
            self.entry_count,
            self.included_source_bytes,
            average_path_chars=average_path_chars,
            metadata_reserve_bytes=self.metadata_reserve_bytes,
        )
        dispositions = []
        for role in sorted(REQUIRED_ROOT_ROLES):
            declaration = self.view.plan.root_roster[role]
            root_stat = os.fstat(self.roots[role]) if declaration.present else None
            locator_digest = None
            unresolved_digest = None
            if declaration.present:
                locator = declaration.relative_locator
                if not isinstance(locator, str):
                    raise TreeCaptureV5Error("candidate_present_root_locator_invalid")
                locator_digest = sha256(locator.encode())
            else:
                unresolved_reason = declaration.unresolved_reason
                if not isinstance(unresolved_reason, str) or not unresolved_reason:
                    raise TreeCaptureV5Error("candidate_absent_root_reason_invalid")
                unresolved_digest = sha256(unresolved_reason.encode())
            dispositions.append(
                {
                    "role": role,
                    "present": bool(declaration.present),
                    "kind": declaration.kind if declaration.present else None,
                    "scope": self.policy_scopes.get(role),
                    "disposition": (
                        "protected_opaque"
                        if declaration.present
                        and self.policy_scopes.get(role) == "protected_opaque"
                        else "included_root"
                        if declaration.present
                        else "absent_unresolved"
                    ),
                    "locator_sha256": locator_digest,
                    "policy_sha256": self.policy_hashes.get(role),
                    "root_signature": (
                        list(self.root_signatures[role])
                        if declaration.present and role in self.root_signatures
                        else None
                    ),
                    "root_stat": (
                        {
                            "dev": root_stat.st_dev,
                            "ino": root_stat.st_ino,
                            "nlink": root_stat.st_nlink,
                            "mode": root_stat.st_mode,
                            "uid": root_stat.st_uid,
                            "gid": root_stat.st_gid,
                            "size": root_stat.st_size,
                            "mtime_ns": root_stat.st_mtime_ns,
                            "ctime_ns": root_stat.st_ctime_ns,
                            "atime_ns": root_stat.st_atime_ns,
                            "birthtime_ns": getattr(root_stat, "st_birthtime_ns", 0),
                            "flags": getattr(root_stat, "st_flags", 0),
                        }
                        if root_stat is not None
                        else None
                    ),
                    "unresolved_reason_sha256": unresolved_digest,
                }
            )
        return {
            "schema": SCHEMA,
            "synthetic_fixture_only": bool(self.view.synthetic),
            "plan_sha256": self.plan_sha256,
            "policy_roster_sha256": self.policy_roster_sha256,
            "role_dispositions": dispositions,
            "opaque_waivers": [dict(GIT_OPAQUE_WAIVER)],
            "present_root_count": len(self.roots),
            "unresolved_role_count": len(REQUIRED_ROOT_ROLES) - len(self.roots),
            "entry_count": self.entry_count,
            "included_source_bytes": self.included_source_bytes,
            "source_inventory_sha256": self.source_inventory_sha256,
            "resource_model": resource_model,
            **self.hardlink_summary,
            "spool_journal_mode": "off",
            "spool_temp_store": "memory",
            "spool_path": "private_descriptor_bound",
            "payload_read": False,
            "T_qualified": False,
            "capture_complete": False,
            "kingston_durable": False,
            "restore_verified": False,
            "erase_ready": False,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: BaseException | None = None
        try:
            self.borrow_stack.close()
        except BaseException as exc:
            first_error = exc
        self.roots.clear()
        database_stat = os.fstat(self.db_fd)
        try:
            self.db.close()
        except BaseException as exc:
            first_error = first_error or exc
        try:
            os.close(self.db_fd)
        except OSError as exc:
            first_error = first_error or exc
        try:
            _remove_spool_files(
                self.spool_fd,
                os.fstat(self.spool_fd).st_dev,
                (database_stat.st_dev, database_stat.st_ino),
            )
            os.rmdir(self.spool_name, dir_fd=self.parent_fd)
        except BaseException as exc:
            first_error = first_error or exc
        finally:
            os.close(self.spool_fd)
            os.close(self.parent_fd)
        if first_error is not None:
            raise TreeCaptureV5Error("inventory_cleanup_incomplete") from first_error

    def __enter__(self) -> GlobalInventoryV5:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _inventory_held_data_v5_core(
    view: HeldDataT,
    writer: DescriptorWriter,
    *,
    role_policies: Mapping[str, RoleCapturePolicy],
    reserve_capacity: Callable[[int], None],
    scratch_reserve_bytes: int,
    payload_reserve_bytes: int,
    verifier_scratch_reserve_bytes: int = 0,
    metadata_reserve_bytes: int = MAX_METADATA_TOTAL_BYTES,
    max_entries: int = MAX_ENTRIES,
    expected_plan_sha256: str | None = None,
    expected_policy_roster_sha256: str | None = None,
    expected_source_inventory_sha256: str | None = None,
    expected_capacity_budget: Mapping[str, int] | None = None,
    expected_new_entry_count: int | None = None,
    expected_allocation_floor_bytes: int | None = None,
    expected_allocation_reserve_bytes: int | None = None,
    existing_target_directories: Mapping[str, bool] | None = None,
    operation_spec_sha256: str | None = None,
    production_authority: object | None = None,
    production_root_check: Callable[[], None] | None = None,
    production_fast_check: Callable[[int], None] | None = None,
    production_boundary_check: Callable[[int], None] | None = None,
    production_cadence_check: Callable[[int, int, int], None] | None = None,
) -> GlobalInventoryV5:
    """Build one descriptor-bound global inventory before any payload reads."""
    if not isinstance(view, HeldDataT) or not isinstance(writer, DescriptorWriter):
        raise TreeCaptureV5Error("held_view_or_writer_invalid")
    production_authorized = production_authority is _PRODUCTION_CAPTURE_AUTHORITY
    if getattr(view, "synthetic", False) is not True and not production_authorized:
        raise TreeCaptureV5Error("synthetic_fixture_only")
    if production_authorized and (
        view.synthetic is not False or type(view.os_api) is not DarwinDataOS
    ):
        raise TreeCaptureV5Error("production_source_view_not_real")
    if writer.storage_guard is None:
        raise TreeCaptureV5Error("pinned_storage_binding_required")
    if not callable(reserve_capacity):
        raise TreeCaptureV5Error("storage_reserve_callback_required")
    if type(scratch_reserve_bytes) is not int or scratch_reserve_bytes < 1:
        raise TreeCaptureV5Error("scratch_reserve_invalid")
    if scratch_reserve_bytes > MAX_SCRATCH_BYTES:
        raise TreeCaptureV5Error("scratch_reserve_invalid")
    if (
        type(metadata_reserve_bytes) is not int
        or not 0 <= metadata_reserve_bytes <= MAX_METADATA_TOTAL_BYTES
    ):
        raise TreeCaptureV5Error("metadata_reserve_invalid")
    if type(payload_reserve_bytes) is not int or payload_reserve_bytes < 0:
        raise TreeCaptureV5Error("payload_reserve_invalid")
    if payload_reserve_bytes > MAX_SOURCE_BYTES:
        raise TreeCaptureV5Error("payload_reserve_limit")
    if (
        type(verifier_scratch_reserve_bytes) is not int
        or not 0 <= verifier_scratch_reserve_bytes <= MAX_SCRATCH_BYTES
    ):
        raise TreeCaptureV5Error("verifier_scratch_reserve_invalid")
    if type(max_entries) is not int or not 1 <= max_entries <= MAX_ENTRIES:
        raise TreeCaptureV5Error("entry_limit_invalid")
    roster = dict(view.plan.root_roster)
    if set(roster) != REQUIRED_ROOT_ROLES:
        raise TreeCaptureV5Error("root_roster_incomplete")
    present_roles = {role for role, declaration in roster.items() if declaration.present}
    if not present_roles:
        raise TreeCaptureV5Error("source_root_roster_all_absent")
    if set(role_policies) != present_roles:
        raise TreeCaptureV5Error("root_policy_roster_mismatch")
    plan_sha256 = approved_data_plan_sha256(view.plan)
    if production_authorized and (
        not _is_sha256(expected_plan_sha256)
        or expected_plan_sha256 != plan_sha256
        or view.trusted_plan_sha256 != expected_plan_sha256
        or not _is_sha256(expected_policy_roster_sha256)
    ):
        raise TreeCaptureV5Error("production_external_plan_policy_pin_invalid")
    if production_authorized and not _is_sha256(operation_spec_sha256):
        raise TreeCaptureV5Error("production_operation_spec_pin_invalid")
    if expected_source_inventory_sha256 is not None and not isinstance(
        expected_capacity_budget, Mapping
    ):
        raise TreeCaptureV5Error("production_capacity_budget_pin_required")
    if expected_source_inventory_sha256 is not None and (
        type(expected_new_entry_count) is not int
        or not 0 <= expected_new_entry_count <= MAX_PLANNED_NEW_ENTRIES
        or type(expected_allocation_floor_bytes) is not int
        or not 1 <= expected_allocation_floor_bytes <= MAX_APFS_ALLOCATION_RESERVE_BYTES
        or type(expected_allocation_reserve_bytes) is not int
        or not expected_allocation_floor_bytes <= expected_allocation_reserve_bytes
        or existing_target_directories is None
    ):
        raise TreeCaptureV5Error("production_apfs_allocation_pins_required")
    if expected_capacity_budget is not None and (
        set(expected_capacity_budget) != {"B", "M", "J", "S_K"}
        or any(
            type(expected_capacity_budget[key]) is not int or expected_capacity_budget[key] < 0
            for key in ("B", "M", "J", "S_K")
        )
    ):
        raise TreeCaptureV5Error("production_capacity_budget_pin_invalid")
    db_fd: int | None = None

    def remaining_capacity_need() -> int:
        if expected_capacity_budget is None:
            used_spool = 0
            output_reserve = metadata_reserve_bytes
        else:
            if db_fd is not None:
                db.commit()
                os.fsync(db_fd)
                used_spool = _allocated_bytes_from_stat(os.fstat(db_fd))
            else:
                used_spool = 0
            output_reserve = (
                expected_capacity_budget["B"]
                + expected_capacity_budget["M"]
                + expected_capacity_budget["J"]
            )
        return max(0, scratch_reserve_bytes - used_spool) + output_reserve + payload_reserve_bytes

    policies: dict[str, dict[str, Any]] = {}
    policy_hashes: dict[str, str] = {}
    policy_scopes = {role: role_policies[role].scope for role in present_roles}
    for role in sorted(present_roles):
        policy, _raw, digest = _validate_policy_pin(role_policies[role])
        policies[role] = policy
        policy_hashes[role] = digest
    policy_roster_rows: list[Mapping[str, Any]] = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        declaration = roster[role]
        if declaration.present:
            policy_roster_rows.append(
                {
                    "role": role,
                    "present": True,
                    "scope": policy_scopes[role],
                    "policy_sha256": policy_hashes[role],
                }
            )
        else:
            unresolved_reason = declaration.unresolved_reason
            if not isinstance(unresolved_reason, str) or not unresolved_reason:
                raise TreeCaptureV5Error("production_absent_role_reason_invalid")
            policy_roster_rows.append(
                {
                    "role": role,
                    "present": False,
                    "disposition": "absent_unresolved",
                    "unresolved_reason_sha256": sha256(unresolved_reason.encode("utf-8")),
                    "scope": None,
                    "policy_sha256": None,
                }
            )
    policy_roster_digest = policy_roster_sha256_v5(plan_sha256, policy_roster_rows)
    if expected_policy_roster_sha256 is not None and (
        policy_roster_digest != expected_policy_roster_sha256
    ):
        raise TreeCaptureV5Error("production_policy_roster_pin_mismatch")
    try:
        writer.assert_storage_binding(writer.storage_guard)
        reserve_capacity(remaining_capacity_need())
    except Exception as exc:
        raise TreeCaptureV5Error("storage_preflight_failed") from exc
    parent_fd = writer.open_directory(SPOOL_BASE, create=True)
    spool_name = secrets.token_hex(16)
    try:
        os.mkdir(spool_name, mode=0o700, dir_fd=parent_fd)
        spool_fd = os.open(
            spool_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        try:
            created = os.open(
                spool_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            try:
                _cleanup_spool_directory(parent_fd, spool_name, created)
            finally:
                os.close(created)
        except BaseException:
            pass
        os.close(parent_fd)
        raise TreeCaptureV5Error("spool_directory_create_failed") from exc
    spool_stat = os.fstat(spool_fd)
    writer_stat = os.fstat(writer.root_fd)
    if (
        not stat.S_ISDIR(spool_stat.st_mode)
        or spool_stat.st_dev != writer_stat.st_dev
        or spool_stat.st_uid != os.getuid()
        or stat.S_IMODE(spool_stat.st_mode) != 0o700
    ):
        try:
            _cleanup_spool_directory(parent_fd, spool_name, spool_fd)
        finally:
            os.close(spool_fd)
        os.close(parent_fd)
        raise TreeCaptureV5Error("spool_directory_identity_invalid")
    try:
        db, db_fd = _db_open_at(spool_fd, spool_stat.st_dev)
    except BaseException:
        try:
            _cleanup_spool_directory(parent_fd, spool_name, spool_fd)
        finally:
            os.close(spool_fd)
        os.close(parent_fd)
        raise

    roots: dict[str, int] = {}
    stack = ExitStack()
    try:
        view.revalidate()
        for role in sorted(present_roles):
            roots[role] = stack.enter_context(view.borrow_root(role))
        root_signatures: dict[str, tuple[int, ...]] = {}
        entry_count = 0
        data_device = os.fstat(view.mount_fd).st_dev
        for role in sorted(present_roles):
            if production_root_check is not None:
                production_root_check()
            root_sig, _included_bytes = _scan_role(
                db,
                view,
                role,
                roots[role],
                policies[role],
                role_policies[role].scope,
                max_entries=max_entries - entry_count,
                db_fd=db_fd,
                spool_fd=spool_fd,
                scratch_limit=scratch_reserve_bytes,
                fast_boundary_check=(
                    (lambda: production_cadence_check(remaining_capacity_need(), 1, 0))
                    if production_cadence_check is not None
                    else (
                        lambda: (
                            production_fast_check(remaining_capacity_need())
                            if production_fast_check is not None
                            else None
                        )
                    )
                ),
            )
            if os.fstat(roots[role]).st_dev != data_device:
                raise TreeCaptureV5Error("source_mount_boundary")
            root_signatures[role] = root_sig
            entry_count = int(db.execute("SELECT count(*) FROM members").fetchone()[0])
            if entry_count > max_entries:
                raise TreeCaptureV5Error("source_resource_limit")
            if production_root_check is not None:
                production_root_check()
        hardlink_summary = _resolve_global_hardlinks(db)
        _check_spool_size(db, db_fd, spool_fd, scratch_reserve_bytes)
        source_bytes = _unique_included_bytes(db)
        if source_bytes > MAX_SOURCE_BYTES:
            raise TreeCaptureV5Error("source_resource_limit")
        average_path_chars = int(
            db.execute("SELECT coalesce(avg(length(path)),0) FROM members").fetchone()[0]
        )
        resource_model = model_candidate_capacity_v5(
            entry_count,
            source_bytes,
            average_path_chars=average_path_chars,
            metadata_reserve_bytes=metadata_reserve_bytes,
        )
        if scratch_reserve_bytes < resource_model["minimum_scratch_reserve_bytes"]:
            raise TreeCaptureV5Error("scratch_reserve_below_capacity_model")
        db.commit()
        view.revalidate()
        for role, fd in roots.items():
            if tuple(v2._sig(os.fstat(fd))) != root_signatures[role]:
                raise TreeCaptureV5Error("source_root_changed_during_inventory")
        inventory_digest_raw = source_inventory_sha256_v5(
            db,
            plan_sha256=plan_sha256,
            policy_roster_sha256=policy_roster_digest,
            root_signatures=root_signatures,
        )
        if expected_source_inventory_sha256 is not None or production_authorized:
            metadata_bytes, _sidecars, metadata_digest = _plan_metadata_rows_v5(
                db,
                roots,
                root_signatures,
                metadata_reserve_bytes=metadata_reserve_bytes,
                candidate_id="0" * 32,
            )
            inventory_digest = bind_metadata_measurement_sha256_v5(
                inventory_digest_raw, metadata_digest
            )
        else:
            inventory_digest = inventory_digest_raw
        if expected_source_inventory_sha256 is not None and (
            not _is_sha256(expected_source_inventory_sha256)
            or inventory_digest != expected_source_inventory_sha256
        ):
            raise TreeCaptureV5Error("source_inventory_plan_mismatch")
        planned_json_bytes: int | None = None
        planned_metadata_bytes: int | None = None
        if expected_capacity_budget is not None:
            if metadata_bytes != expected_capacity_budget["M"]:
                raise TreeCaptureV5Error("capacity_plan_metadata_budget_mismatch")
            if source_bytes != expected_capacity_budget["B"]:
                raise TreeCaptureV5Error("capacity_plan_payload_budget_mismatch")
            projection_id = "0" * 32
            _simulate_payload_db_v5(db, projection_id)
            projection = _candidate_json_bytes_v5(
                db,
                view=view,
                roots=roots,
                root_signatures=root_signatures,
                policy_hashes=policy_hashes,
                policy_scopes=policy_scopes,
                plan_sha256=plan_sha256,
                policy_roster_sha256=policy_roster_digest,
                included_source_bytes=source_bytes,
                metadata_reserve_bytes=metadata_reserve_bytes,
                candidate_id=projection_id,
                operation_spec_sha256=operation_spec_sha256,
            )
            planned_json_bytes = projection["candidate_json_bytes"]
            if planned_json_bytes != expected_capacity_budget["J"]:
                raise TreeCaptureV5Error("capacity_plan_json_budget_mismatch")
            projected_spool_bytes = os.fstat(db_fd).st_size + 16 * 1024**2
            if projected_spool_bytes != expected_capacity_budget["S_K"]:
                raise TreeCaptureV5Error("capacity_plan_spool_budget_mismatch")
            if expected_source_inventory_sha256 is not None:
                fresh_entry_count = planned_new_entry_count_v5(
                    projection, existing_target_directories
                )
                fresh_allocation_floor = apfs_allocation_reserve_floor_v5(
                    source_bytes,
                    metadata_bytes,
                    projection["candidate_json_bytes"],
                    projected_spool_bytes,
                    fresh_entry_count,
                )
                if (
                    fresh_entry_count != expected_new_entry_count
                    or fresh_allocation_floor != expected_allocation_floor_bytes
                    or expected_allocation_reserve_bytes < fresh_allocation_floor
                ):
                    raise TreeCaptureV5Error("capacity_plan_apfs_allocation_pin_mismatch")
            db.execute("DELETE FROM payload_pages")
            db.execute(
                "UPDATE members SET payload_sha256=NULL,payload_bytes=NULL,"
                "metadata_json=NULL,metadata_state='pending'"
            )
            db.commit()
            planned_metadata_bytes = metadata_bytes
            _check_spool_size(db, db_fd, spool_fd, scratch_reserve_bytes)
        writer.assert_storage_binding(writer.storage_guard)
        try:
            reserve_capacity(remaining_capacity_need())
        except Exception as exc:
            raise TreeCaptureV5Error("storage_reserve_recheck_failed") from exc
        return GlobalInventoryV5(
            view=view,
            writer=writer,
            parent_fd=parent_fd,
            spool_fd=spool_fd,
            spool_name=spool_name,
            db=db,
            db_fd=db_fd,
            borrow_stack=stack.pop_all(),
            roots=roots,
            root_signatures=root_signatures,
            policy_hashes=policy_hashes,
            policy_scopes={role: role_policies[role].scope for role in present_roles},
            plan_sha256=plan_sha256,
            policy_roster_sha256=policy_roster_digest,
            hardlink_summary=hardlink_summary,
            included_source_bytes=source_bytes,
            source_inventory_sha256=inventory_digest,
            planned_metadata_bytes=planned_metadata_bytes,
            planned_json_bytes=planned_json_bytes,
            reserve_capacity=reserve_capacity,
            scratch_reserve_bytes=scratch_reserve_bytes,
            metadata_reserve_bytes=metadata_reserve_bytes,
            payload_reserve_bytes=payload_reserve_bytes,
            verifier_scratch_reserve_bytes=verifier_scratch_reserve_bytes,
            operation_spec_sha256=operation_spec_sha256,
            production_authority=(_PRODUCTION_CAPTURE_AUTHORITY if production_authorized else None),
            production_root_check=production_root_check,
            production_fast_check=production_fast_check,
            production_boundary_check=production_boundary_check,
            production_cadence_check=production_cadence_check,
        )
    except BaseException:
        cleanup_error: BaseException | None = None
        database_stat = os.fstat(db_fd)
        try:
            stack.close()
        except BaseException as exc:
            cleanup_error = exc
        try:
            db.close()
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        try:
            os.close(db_fd)
        except OSError:
            pass
        try:
            _cleanup_spool_directory(
                parent_fd,
                spool_name,
                spool_fd,
                (database_stat.st_dev, database_stat.st_ino),
            )
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        finally:
            os.close(spool_fd)
        os.close(parent_fd)
        if cleanup_error is not None:
            raise TreeCaptureV5Error("inventory_failure_cleanup_incomplete") from cleanup_error
        raise


def inventory_held_data_v5(
    view: HeldDataT,
    writer: DescriptorWriter,
    *,
    role_policies: Mapping[str, RoleCapturePolicy],
    reserve_capacity: Callable[[int], None],
    scratch_reserve_bytes: int,
    payload_reserve_bytes: int,
    metadata_reserve_bytes: int = MAX_METADATA_TOTAL_BYTES,
    max_entries: int = MAX_ENTRIES,
) -> GlobalInventoryV5:
    """Build a global inventory for explicitly synthetic fixtures only."""
    return _inventory_held_data_v5_core(
        view,
        writer,
        role_policies=role_policies,
        reserve_capacity=reserve_capacity,
        scratch_reserve_bytes=scratch_reserve_bytes,
        payload_reserve_bytes=payload_reserve_bytes,
        metadata_reserve_bytes=metadata_reserve_bytes,
        max_entries=max_entries,
    )


def _plan_held_data_capacity_v5_core(
    view: HeldDataT,
    *,
    role_policies: Mapping[str, RoleCapturePolicy],
    scratch_parent_fd: int,
    scratch_device: int,
    target_device: int,
    expected_plan_sha256: str,
    expected_policy_roster_sha256: str,
    scratch_db_limit_bytes: int,
    metadata_reserve_bytes: int = MAX_METADATA_TOTAL_BYTES,
    payload_reserve_bytes: int = 0,
    target_spool_reserve_bytes: int = MAX_SCRATCH_BYTES,
    allocation_reserve_bytes: int = 0,
    prior_target_usage_bytes: int = 0,
    target_available_bytes: int | None = None,
    prior_target_usage_entries: int = 0,
    target_observation_sha256: str | None = None,
    existing_target_directories: Mapping[str, bool] | None = None,
    safety_floor_bytes: int = 50 * 1024**3,
    writer_floor_bytes: int = 8 * 1024**3,
    operation_spec_sha256: str | None = None,
    external_boundary_check: Callable[[int], None] | None = None,
    production_authority: object | None = None,
    max_entries: int = MAX_ENTRIES,
) -> dict[str, Any]:
    """Read-only source sizing into a pinned external SQLite scratch directory.

    This planner shares the production inventory scanner and global hardlink
    reconciliation, but has no DescriptorWriter and cannot create a Kingston
    spool or candidate. It reads filesystem metadata only, never regular-file
    contents, xattr values, or ACL bytes. Its report is a capacity plan, not T,
    a capture authorization, or a durability claim.
    """
    if (
        not isinstance(view, HeldDataT)
        or type(view.synthetic) is not bool
        or type(scratch_parent_fd) is not int
        or type(scratch_device) is not int
        or type(target_device) is not int
        or target_device <= 0
        or type(scratch_db_limit_bytes) is not int
        or not 1 <= scratch_db_limit_bytes <= MAX_SCRATCH_BYTES
        or type(metadata_reserve_bytes) is not int
        or not 0 <= metadata_reserve_bytes <= MAX_METADATA_TOTAL_BYTES
        or type(payload_reserve_bytes) is not int
        or not 0 <= payload_reserve_bytes <= MAX_SOURCE_BYTES
        or type(target_spool_reserve_bytes) is not int
        or not 1 <= target_spool_reserve_bytes <= MAX_SCRATCH_BYTES
        or type(allocation_reserve_bytes) is not int
        or allocation_reserve_bytes < 0
        or type(prior_target_usage_bytes) is not int
        or prior_target_usage_bytes < 0
        or (target_available_bytes is not None and type(target_available_bytes) is not int)
        or (target_available_bytes is not None and target_available_bytes < 0)
        or type(prior_target_usage_entries) is not int
        or prior_target_usage_entries < 0
        or (target_observation_sha256 is not None and not _is_sha256(target_observation_sha256))
        or type(safety_floor_bytes) is not int
        or safety_floor_bytes < 0
        or type(writer_floor_bytes) is not int
        or writer_floor_bytes < 0
        or (operation_spec_sha256 is not None and not _is_sha256(operation_spec_sha256))
        or type(max_entries) is not int
        or not 1 <= max_entries <= MAX_ENTRIES
        or not _is_sha256(expected_plan_sha256)
        or not _is_sha256(expected_policy_roster_sha256)
    ):
        raise TreeCaptureV5Error("capacity_plan_input_invalid")
    if view.synthetic is False and (
        production_authority is not _CAPACITY_PLAN_PRODUCTION_AUTHORITY
        or not callable(external_boundary_check)
    ):
        raise TreeCaptureV5Error("capacity_plan_production_capability_required")
    target_directories = (
        {"host-recovery": True, "tree-capture-v5": True, "candidates": True, "spool": True}
        if existing_target_directories is None
        else dict(existing_target_directories)
    )
    if set(target_directories) != {
        "host-recovery",
        "tree-capture-v5",
        "candidates",
        "spool",
    } or any(type(present) is not bool for present in target_directories.values()):
        raise TreeCaptureV5Error("capacity_plan_target_directory_state_invalid")
    source_device = os.fstat(view.mount_fd).st_dev
    parent_st = os.fstat(scratch_parent_fd)
    parent_vfs = os.fstatvfs(scratch_parent_fd)
    initial_free_bytes = int(parent_vfs.f_bavail * parent_vfs.f_frsize)
    if (
        not stat.S_ISDIR(parent_st.st_mode)
        or parent_st.st_dev != scratch_device
        or parent_st.st_uid != os.getuid()
        or stat.S_IMODE(parent_st.st_mode) != 0o700
        or parent_vfs.f_flag & getattr(os, "ST_RDONLY", 1)
        or _acl_bytes(scratch_parent_fd) is not None
    ):
        raise TreeCaptureV5Error("capacity_plan_scratch_identity_or_capacity_invalid")
    if source_device == parent_st.st_dev or target_device == parent_st.st_dev:
        raise TreeCaptureV5Error("capacity_plan_device_separation_invalid")
    if initial_free_bytes < scratch_db_limit_bytes + 256 * 1024**2:
        raise TreeCaptureV5Error("capacity_plan_scratch_identity_or_capacity_invalid")
    plan_sha256 = approved_data_plan_sha256(view.plan)
    roster = dict(view.plan.root_roster)
    present_roles = {role for role, declaration in roster.items() if declaration.present}
    if (
        plan_sha256 != expected_plan_sha256
        or view.trusted_plan_sha256 != expected_plan_sha256
        or set(roster) != REQUIRED_ROOT_ROLES
        or not present_roles
        or set(role_policies) != present_roles
    ):
        raise TreeCaptureV5Error("capacity_plan_source_roster_invalid")
    policy_hashes: dict[str, str] = {}
    policy_scopes: dict[str, str] = {}
    for role in sorted(present_roles):
        _policy, _raw, digest = _validate_policy_pin(role_policies[role])
        policy_hashes[role] = digest
        policy_scopes[role] = role_policies[role].scope
    rows: list[Mapping[str, Any]] = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        declaration = roster[role]
        if declaration.present:
            rows.append(
                {
                    "role": role,
                    "present": True,
                    "scope": policy_scopes[role],
                    "policy_sha256": policy_hashes[role],
                }
            )
        else:
            reason = declaration.unresolved_reason
            if not isinstance(reason, str) or not reason:
                raise TreeCaptureV5Error("capacity_plan_absent_role_invalid")
            rows.append(
                {
                    "role": role,
                    "present": False,
                    "disposition": "absent_unresolved",
                    "unresolved_reason_sha256": sha256(reason.encode("utf-8")),
                    "scope": None,
                    "policy_sha256": None,
                }
            )
    actual_roster_sha256 = policy_roster_sha256_v5(plan_sha256, rows)
    if actual_roster_sha256 != expected_policy_roster_sha256:
        raise TreeCaptureV5Error("capacity_plan_policy_roster_pin_mismatch")

    name = secrets.token_hex(16)
    created = False
    try:
        os.mkdir(name, mode=0o700, dir_fd=scratch_parent_fd)
        created = True
        plan_fd = os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=scratch_parent_fd
        )
    except OSError as exc:
        if created:
            # Without a held fd there is no race-free proof that this name is
            # still the directory created above. Leave it for operator review.
            raise TreeCaptureV5Error("capacity_plan_scratch_open_failed_unreconciled") from exc
        raise TreeCaptureV5Error("capacity_plan_scratch_create_failed") from exc
    db: sqlite3.Connection | None = None
    db_fd: int | None = None
    roots: dict[str, int] = {}
    stack = ExitStack()
    plan_identity: tuple[int, int] | None = None
    try:
        plan_st = os.fstat(plan_fd)
        plan_identity = (plan_st.st_dev, plan_st.st_ino)
        if (
            not stat.S_ISDIR(plan_st.st_mode)
            or plan_st.st_dev != scratch_device
            or plan_st.st_uid != os.getuid()
            or stat.S_IMODE(plan_st.st_mode) != 0o700
        ):
            raise TreeCaptureV5Error("capacity_plan_scratch_identity_invalid")
        db, db_fd = _db_open_at(plan_fd, scratch_device)
        view.revalidate()
        root_signatures: dict[str, tuple[int, ...]] = {}
        for role in sorted(present_roles):
            roots[role] = stack.enter_context(view.borrow_root(role))
        for role in sorted(present_roles):
            view.revalidate()
            if external_boundary_check is not None:
                external_boundary_check(os.fstat(db_fd).st_size)
            view.revalidate_mount_fast()
            prior_count = int(db.execute("SELECT count(*) FROM members").fetchone()[0])
            checks = 0
            last_capacity_check = time.monotonic()

            def plan_boundary_check() -> None:
                nonlocal checks, last_capacity_check
                view.revalidate_mount_fast()
                checks += 1
                now = time.monotonic()
                if checks >= 10_000 or now - last_capacity_check >= 60:
                    view.revalidate()
                    parent_st = os.fstat(scratch_parent_fd)
                    vfs = os.fstatvfs(scratch_parent_fd)
                    db_size = os.fstat(db_fd).st_size
                    free_bytes = int(vfs.f_bavail * vfs.f_frsize)
                    if (
                        parent_st.st_dev != scratch_device
                        or vfs.f_flag & getattr(os, "ST_RDONLY", 1)
                        or db_size > scratch_db_limit_bytes
                        or free_bytes < scratch_db_limit_bytes - db_size + 256 * 1024**2
                    ):
                        raise TreeCaptureV5Error("capacity_plan_scratch_capacity_invalid")
                    if external_boundary_check is not None:
                        external_boundary_check(db_size)
                    checks = 0
                    last_capacity_check = time.monotonic()

            root_sig, _included_bytes = _scan_role(
                db,
                view,
                role,
                roots[role],
                _validate_policy_pin(role_policies[role])[0],
                policy_scopes[role],
                max_entries=max_entries - prior_count,
                db_fd=db_fd,
                spool_fd=plan_fd,
                scratch_limit=scratch_db_limit_bytes,
                fast_boundary_check=plan_boundary_check,
            )
            if os.fstat(roots[role]).st_dev != source_device:
                raise TreeCaptureV5Error("source_mount_boundary")
            root_signatures[role] = root_sig
            count = int(db.execute("SELECT count(*) FROM members").fetchone()[0])
            if count > max_entries:
                raise TreeCaptureV5Error("source_resource_limit")
        hardlink_summary = _resolve_global_hardlinks(db)
        _check_spool_size(db, db_fd, plan_fd, scratch_db_limit_bytes)
        entry_count = int(db.execute("SELECT count(*) FROM members").fetchone()[0])
        included_bytes = _unique_included_bytes(db)
        if included_bytes > MAX_SOURCE_BYTES:
            raise TreeCaptureV5Error("source_resource_limit")
        path_stats = db.execute(
            "SELECT coalesce(sum(length(path)),0),coalesce(max(length(path)),0),"
            "coalesce(avg(length(path)),0) FROM members"
        ).fetchone()
        path_chars_total, max_path_chars, average_path_chars = (int(value) for value in path_stats)
        estimate = model_candidate_capacity_v5(
            entry_count,
            included_bytes,
            average_path_chars=average_path_chars,
            metadata_reserve_bytes=metadata_reserve_bytes,
        )
        inventory_digest_raw = source_inventory_sha256_v5(
            db,
            plan_sha256=plan_sha256,
            policy_roster_sha256=actual_roster_sha256,
            root_signatures=root_signatures,
        )
        candidate_id = "0" * 32
        metadata_bytes, sidecars, metadata_digest = _plan_metadata_rows_v5(
            db,
            roots,
            root_signatures,
            metadata_reserve_bytes=metadata_reserve_bytes,
            candidate_id=candidate_id,
        )
        inventory_digest = bind_metadata_measurement_sha256_v5(
            inventory_digest_raw, metadata_digest
        )
        _simulate_payload_db_v5(db, candidate_id)
        _check_spool_size(db, db_fd, plan_fd, scratch_db_limit_bytes)
        json_sizes = _candidate_json_bytes_v5(
            db,
            view=view,
            roots=roots,
            root_signatures=root_signatures,
            policy_hashes=policy_hashes,
            policy_scopes=policy_scopes,
            plan_sha256=plan_sha256,
            policy_roster_sha256=actual_roster_sha256,
            included_source_bytes=included_bytes,
            metadata_reserve_bytes=metadata_reserve_bytes,
            candidate_id=candidate_id,
            operation_spec_sha256=operation_spec_sha256,
        )
        spool_bytes = os.fstat(db_fd).st_size
        spool_overshoot = 16 * 1024**2
        if spool_bytes + spool_overshoot > target_spool_reserve_bytes:
            raise TreeCaptureV5Error("capacity_plan_spool_reserve_below_replayed_size")
        if spool_bytes + spool_overshoot > MAX_SCRATCH_BYTES:
            raise TreeCaptureV5Error("capacity_plan_spool_reserve_exceeded")
        spool_cap_bytes = spool_bytes + spool_overshoot
        candidate_file_count = (
            json_sizes["payload_page_file_count"]
            + json_sizes["sidecar_file_count"]
            + json_sizes["member_page_count"]
            + json_sizes["payload_index_page_count"]
            + 2
        )
        candidate_directory_count = (
            1
            + int(json_sizes["payload_page_file_count"] > 0)
            + json_sizes["payload_directory_count"]
            + int(json_sizes["sidecar_file_count"] > 0)
            + json_sizes["metadata_directory_count"]
            + int(json_sizes["member_page_count"] > 0)
            + int(json_sizes["payload_index_page_count"] > 0)
        )
        new_entry_count = planned_new_entry_count_v5(json_sizes, target_directories)
        allocation_floor = apfs_allocation_reserve_floor_v5(
            included_bytes,
            metadata_bytes,
            json_sizes["candidate_json_bytes"],
            spool_bytes=spool_cap_bytes,
            new_entry_count=new_entry_count,
        )
        if view.synthetic is False and allocation_reserve_bytes < allocation_floor:
            raise TreeCaptureV5Error("capacity_plan_apfs_reserve_below_operational_floor")
        required_new_free = (
            included_bytes
            + metadata_bytes
            + json_sizes["candidate_json_bytes"]
            + spool_cap_bytes
            + allocation_reserve_bytes
            + payload_reserve_bytes
            + safety_floor_bytes
            + writer_floor_bytes
        )
        view.revalidate()
        if external_boundary_check is not None:
            external_boundary_check(os.fstat(db_fd).st_size)
        for role, fd in roots.items():
            if tuple(v2._sig(os.fstat(fd))) != root_signatures[role]:
                raise TreeCaptureV5Error("source_root_changed_during_inventory")
        return {
            "schema": "brain-host-tree-capture-v5-capacity-plan-v1",
            "synthetic_fixture_only": bool(view.synthetic),
            "plan_sha256": plan_sha256,
            "policy_roster_sha256": actual_roster_sha256,
            "source_inventory_sha256": inventory_digest,
            "present_role_count": len(present_roles),
            "unresolved_role_count": len(REQUIRED_ROOT_ROLES) - len(present_roles),
            "entry_count": entry_count,
            "included_unique_logical_bytes_upper_bound": included_bytes,
            "path_chars_total": path_chars_total,
            "max_path_chars": max_path_chars,
            "estimated_average_path_chars": average_path_chars,
            "hardlink_summary": hardlink_summary,
            "candidate_resource_estimate": estimate,
            "logical_budget": {
                "B_payload_logical_bytes": included_bytes,
                "M_metadata_value_bytes": metadata_bytes,
                "J_candidate_json_bytes": json_sizes["candidate_json_bytes"],
                "J_member_page_bytes": json_sizes["member_page_bytes"],
                "J_payload_index_bytes": json_sizes["payload_index_bytes"],
                "J_index_bytes": json_sizes["index_bytes"],
                "J_readback_bytes": json_sizes["readback_bytes"],
                "sidecar_file_count": json_sizes["sidecar_file_count"],
                "S_K_replayed_spool_bytes": spool_bytes,
                "S_K_overshoot_margin_bytes": spool_overshoot,
                "S_K_reserved_bytes": spool_bytes + spool_overshoot,
                "A_apfs_allocation_reserve_bytes": allocation_reserve_bytes,
                "A_formula_version": APFS_ALLOCATION_FORMULA_VERSION,
                "A_new_entry_count_N_new": new_entry_count,
                "A_min_operational_reserve_bytes": allocation_floor,
                "A_chosen_operational_reserve_bytes": allocation_reserve_bytes,
                "N_new_candidate_regular_file_count": candidate_file_count,
                "N_new_candidate_directory_count": candidate_directory_count,
                "N_new_capture_parent_directory_count": sum(
                    not exists for exists in target_directories.values()
                ),
                "N_new_spool_child_and_db_count": 2,
                "U_prior_target_usage_bytes": prior_target_usage_bytes,
                "U_prior_target_usage_entries": prior_target_usage_entries,
                "U_observation_sha256": target_observation_sha256,
                "U_disposition": "informational_already_reflected_in_F",
                "P_R_payload_reserve_bytes": payload_reserve_bytes,
                "R_safety_floor_bytes": safety_floor_bytes,
                "G_production_free_floor_bytes": writer_floor_bytes,
                "F_live_available_bytes": target_available_bytes,
                "required_new_free_bytes": required_new_free,
                "capacity_gate_satisfied": (
                    target_available_bytes is not None
                    and target_available_bytes >= required_new_free
                ),
                "existing_usage_already_excluded_from_available_bytes": True,
            },
            "capacity_result_status": "sizing_only_unqualified",
            "candidate_allocation_hard_bound_established": False,
            "enforced_tool_caps": {
                "kingston_spool_db_bytes": MAX_SCRATCH_BYTES,
                "metadata_sidecar_value_bytes": MAX_METADATA_TOTAL_BYTES,
                "source_payload_bytes": MAX_SOURCE_BYTES,
                "entries": MAX_ENTRIES,
            },
            "planner_scratch_db_limit_bytes": scratch_db_limit_bytes,
            "kingston_spool_reserve_bytes": target_spool_reserve_bytes,
            "payload_read": False,
            "xattr_values_read": False,
            "acl_bytes_read": False,
            "T_qualified": False,
            "capture_authorized": False,
            "kingston_written": False,
            "erase_ready": False,
        }
    finally:
        cleanup_error: BaseException | None = None
        try:
            stack.close()
        except BaseException as exc:
            cleanup_error = exc
        if db is not None and db_fd is not None:
            database_stat = os.fstat(db_fd)
            try:
                db.close()
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
            try:
                os.close(db_fd)
            except OSError as exc:
                cleanup_error = cleanup_error or exc
            try:
                _cleanup_spool_directory(
                    scratch_parent_fd,
                    name,
                    plan_fd,
                    (database_stat.st_dev, database_stat.st_ino),
                )
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        else:
            try:
                current = os.stat(name, dir_fd=scratch_parent_fd, follow_symlinks=False)
                if plan_identity is None or (current.st_dev, current.st_ino) != plan_identity:
                    raise TreeCaptureV5Error("capacity_plan_scratch_cleanup_identity_invalid")
                if os.listdir(plan_fd):
                    raise TreeCaptureV5Error("capacity_plan_scratch_cleanup_nonempty")
                os.rmdir(name, dir_fd=scratch_parent_fd)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        os.close(plan_fd)
        if cleanup_error is None and external_boundary_check is not None:
            try:
                external_boundary_check(0)
            except BaseException as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            raise TreeCaptureV5Error("capacity_plan_cleanup_incomplete") from cleanup_error


def _open_source_entry(
    root_fd: int, relative: str, kind: str
) -> tuple[int | None, int, str | None]:
    if not relative:
        return os.dup(root_fd), -1, None
    parent, name = relative.rsplit("/", 1) if "/" in relative else ("", relative)
    parent_fd = _open_directory_relative(root_fd, parent)
    if kind == "symlink":
        return None, parent_fd, name
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if kind == "directory":
        flags |= os.O_DIRECTORY
    try:
        return os.open(name, flags, dir_fd=parent_fd), parent_fd, name
    except BaseException:
        os.close(parent_fd)
        raise


def _xattr_names_fd(fd: int) -> list[bytes]:
    try:
        return v2._list_xattrs_fd(fd)
    except Exception as exc:
        raise TreeCaptureV5Error("metadata_xattr_inventory_failed") from exc


def _getxattr_fd(fd: int, name: bytes) -> bytes:
    libc = ctypes.CDLL(
        "/usr/lib/libSystem.B.dylib" if sys.platform == "darwin" else None, use_errno=True
    )
    function = getattr(libc, "fgetxattr", None)
    if function is None:
        raise TreeCaptureV5Error("metadata_xattr_read_unavailable")
    function.restype = ctypes.c_ssize_t
    if sys.platform == "darwin":
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.c_int,
        ]

        def call(buffer, size):
            return function(fd, name, buffer, size, 0, 0)
    else:
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]

        def call(buffer, size):
            return function(fd, name, buffer, size)

    size = call(None, 0)
    if size < 0:
        error = ctypes.get_errno()
        raise TreeCaptureV5Error(f"metadata_xattr_read_failed_{error}")
    if size > MAX_METADATA_VALUE_BYTES:
        raise TreeCaptureV5Error("metadata_value_limit")
    buffer = ctypes.create_string_buffer(size)
    actual = call(buffer, size)
    if actual < 0 or actual != size:
        raise TreeCaptureV5Error("metadata_xattr_read_failed")
    return buffer.raw[:actual]


def _getxattr_size_fd(fd: int, name: bytes) -> int:
    """Return an xattr's Darwin-reported length without reading its value bytes."""
    if sys.platform != "darwin":
        raise TreeCaptureV5Error("metadata_xattr_size_probe_unavailable")
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = getattr(libc, "fgetxattr", None)
    if function is None:
        raise TreeCaptureV5Error("metadata_xattr_size_probe_unavailable")
    function.restype = ctypes.c_ssize_t
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    size = function(fd, name, None, 0, 0, 0)
    if size < 0:
        error = ctypes.get_errno()
        raise TreeCaptureV5Error(f"metadata_xattr_size_probe_failed_{error}")
    if size > MAX_METADATA_VALUE_BYTES:
        raise TreeCaptureV5Error("metadata_value_limit")
    return int(size)


def _acl_bytes(fd: int) -> bytes | None:
    if sys.platform != "darwin":
        raise TreeCaptureV5Error("metadata_acl_read_unavailable")
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    getter = libc.acl_get_fd_np
    getter.argtypes = [ctypes.c_int, ctypes.c_int]
    getter.restype = ctypes.c_void_p
    acl = getter(fd, 0x00000100)
    if not acl:
        error = ctypes.get_errno()
        if error in {errno.ENOENT, getattr(errno, "ENOATTR", errno.ENODATA)}:
            return None
        raise TreeCaptureV5Error("metadata_acl_read_failed")
    acl_value = ctypes.c_void_p(acl)
    try:
        size_fn = libc.acl_size
        size_fn.argtypes = [ctypes.c_void_p]
        size_fn.restype = ctypes.c_ssize_t
        size = size_fn(acl_value)
        if size < 0 or size > MAX_METADATA_VALUE_BYTES:
            raise TreeCaptureV5Error("metadata_acl_size_invalid")
        buffer = ctypes.create_string_buffer(size)
        copy_fn = libc.acl_copy_ext
        copy_fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t]
        copy_fn.restype = ctypes.c_ssize_t
        actual = copy_fn(buffer, acl_value, size)
        if actual != size:
            raise TreeCaptureV5Error("metadata_acl_copy_failed")
        return buffer.raw[:actual]
    finally:
        libc.acl_free.argtypes = [ctypes.c_void_p]
        libc.acl_free(acl_value)


def _acl_size_fd(fd: int) -> int | None:
    """Return serialized Darwin ACL length without copying ACL value bytes."""
    if sys.platform != "darwin":
        raise TreeCaptureV5Error("metadata_acl_size_probe_unavailable")
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    getter = libc.acl_get_fd_np
    getter.argtypes = [ctypes.c_int, ctypes.c_int]
    getter.restype = ctypes.c_void_p
    acl = getter(fd, 0x00000100)
    if not acl:
        error = ctypes.get_errno()
        if error in {errno.ENOENT, getattr(errno, "ENOATTR", errno.ENODATA)}:
            return None
        raise TreeCaptureV5Error(f"metadata_acl_size_probe_failed_{error}")
    acl_value = ctypes.c_void_p(acl)
    try:
        size_fn = libc.acl_size
        size_fn.argtypes = [ctypes.c_void_p]
        size_fn.restype = ctypes.c_ssize_t
        size = size_fn(acl_value)
        if size < 0 or size > MAX_METADATA_VALUE_BYTES:
            raise TreeCaptureV5Error("metadata_acl_size_invalid")
        return int(size)
    finally:
        libc.acl_free.argtypes = [ctypes.c_void_p]
        libc.acl_free(acl_value)


def _plan_metadata_rows_v5(
    db: sqlite3.Connection,
    roots: Mapping[str, int],
    root_signatures: Mapping[str, tuple[int, ...]],
    *,
    metadata_reserve_bytes: int,
    candidate_id: str,
) -> tuple[int, list[dict[str, Any]], str]:
    """Replay capture metadata references using sizes only; never read values."""
    total_bytes = 0
    sidecars: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    query = (
        "SELECT role,path,kind,sig,flags,payload_id,group_id,primary_role,primary_path "
        "FROM members WHERE state='included' AND kind IN ('directory','symlink') "
        "OR state='included' AND kind='file' AND payload_id IS NOT NULL "
        "AND (group_id IS NULL OR (role=primary_role AND path=primary_path)) "
        "ORDER BY role COLLATE BINARY,path COLLATE BINARY"
    )
    for ordinal, (role, path, kind, sig_raw, source_flags, payload_id, *_rest) in enumerate(
        db.execute(query)
    ):
        if tuple(v2._sig(os.fstat(roots[role]))) != root_signatures[role]:
            raise TreeCaptureV5Error("source_root_changed_before_metadata")
        metadata: dict[str, Any]
        if kind == "symlink":
            metadata = {
                "state": "unobserved_symlink_nofollow",
                "xattrs": [],
                "acl": None,
                "acl_state": "unobserved_symlink_nofollow",
                "flags_state": "value_required" if source_flags else "absent",
                "flags_value": source_flags or None,
            }
        else:
            source_fd, parent_fd, name = _open_source_entry(roots[role], path, kind)
            assert source_fd is not None
            try:
                before = os.fstat(source_fd)
                if canonical_json(list(v2._sig(before))) != bytes(sig_raw):
                    raise TreeCaptureV5Error("source_object_changed_before_metadata")
                names = _xattr_names_fd(source_fd)
                if len(names) > 4096:
                    raise TreeCaptureV5Error("metadata_xattr_count_limit")
                refs: list[dict[str, Any]] = []
                object_bytes = 0
                token = f"{ordinal:016x}"
                for attr_name in sorted(names):
                    try:
                        decoded_name = attr_name.decode("utf-8", errors="strict")
                    except UnicodeDecodeError as exc:
                        raise TreeCaptureV5Error("metadata_xattr_name_invalid") from exc
                    value_size = _getxattr_size_fd(source_fd, attr_name)
                    object_bytes += value_size
                    if object_bytes > MAX_OBJECT_METADATA_BYTES:
                        raise TreeCaptureV5Error("metadata_object_limit")
                    rel = (
                        f"host-recovery/tree-capture-v5/candidates/{candidate_id}/metadata/"
                        f"{token}/{len(refs):06d}.bin"
                    )
                    ref = {
                        "name": decoded_name,
                        "path": rel,
                        "sha256": "0" * 64,
                        "size_bytes": value_size,
                    }
                    refs.append(ref)
                    sidecars.append({"path": rel, "sha256": "0" * 64, "size_bytes": value_size})
                    if len(sidecars) > MAX_ENTRIES:
                        raise TreeCaptureV5Error("metadata_sidecar_file_count_limit")
                    if len(sidecars) > MAX_ENTRIES:
                        raise TreeCaptureV5Error("metadata_sidecar_file_count_limit")
                acl_size = _acl_size_fd(source_fd)
                acl_ref = None
                if acl_size is not None:
                    object_bytes += acl_size
                    if object_bytes > MAX_OBJECT_METADATA_BYTES:
                        raise TreeCaptureV5Error("metadata_object_limit")
                    rel = (
                        f"host-recovery/tree-capture-v5/candidates/{candidate_id}/metadata/"
                        f"{token}/acl.bin"
                    )
                    acl_ref = {
                        "path": rel,
                        "sha256": "0" * 64,
                        "size_bytes": acl_size,
                    }
                    sidecars.append({"path": rel, "sha256": "0" * 64, "size_bytes": acl_size})
                    if len(sidecars) > MAX_ENTRIES:
                        raise TreeCaptureV5Error("metadata_sidecar_file_count_limit")
                    if len(sidecars) > MAX_ENTRIES:
                        raise TreeCaptureV5Error("metadata_sidecar_file_count_limit")
                if canonical_json(list(v2._sig(os.fstat(source_fd)))) != bytes(sig_raw):
                    raise TreeCaptureV5Error("source_object_changed_during_metadata")
                total_bytes += object_bytes
                if total_bytes > metadata_reserve_bytes:
                    raise TreeCaptureV5Error("metadata_total_limit")
                flags = getattr(before, "st_flags", 0)
                metadata = {
                    "state": "observed",
                    "xattrs": refs,
                    "acl": acl_ref,
                    "acl_state": "value_required" if acl_ref is not None else "absent",
                    "flags_state": "value_required" if flags else "absent",
                    "flags_value": flags or None,
                }
            finally:
                os.close(source_fd)
                if parent_fd >= 0:
                    os.close(parent_fd)
        metadata_raw = canonical_json(metadata)
        digest_record = canonical_json([role, path, metadata])
        digest.update(len(digest_record).to_bytes(8, "big"))
        digest.update(digest_record)
        db.execute(
            "UPDATE members SET metadata_json=?,metadata_state='observed' WHERE role=? AND path=?",
            (metadata_raw, role, path),
        )
        if payload_id is not None:
            db.execute(
                "UPDATE members SET metadata_json=?,metadata_state='observed' WHERE payload_id=?",
                (metadata_raw, payload_id),
            )
    db.commit()
    sidecars.sort(key=lambda value: value["path"])
    return total_bytes, sidecars, digest.hexdigest()


def _capture_metadata(
    inventory: GlobalInventoryV5,
    base: str,
    role: str,
    path: str,
    kind: str,
    ordinal: int,
    expected_sig: bytes,
    expected_metadata_raw: bytes | None,
    sidecar_pages: list[dict[str, Any]],
    metadata_reserve_before: int,
) -> dict[str, Any]:
    if v2._sig(os.fstat(inventory.roots[role])) != inventory.root_signatures[role]:
        raise TreeCaptureV5Error("source_root_changed_before_metadata")
    expected_metadata = (
        json.loads(expected_metadata_raw) if expected_metadata_raw is not None else None
    )
    source_fd, parent_fd, name = _open_source_entry(inventory.roots[role], path, kind)
    try:
        if source_fd is None:
            st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if canonical_json(list(v2._sig(st))) != expected_sig:
                raise TreeCaptureV5Error("source_object_changed_before_metadata")
            after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if v2._sig(after) != v2._sig(st):
                raise TreeCaptureV5Error("source_object_changed_during_metadata")
            result = {
                "state": "unobserved_symlink_nofollow",
                "xattrs": [],
                "acl": None,
                "acl_state": "unobserved_symlink_nofollow",
                "flags_state": "value_required" if getattr(st, "st_flags", 0) else "absent",
                "flags_value": getattr(st, "st_flags", 0) or None,
            }
            if expected_metadata is not None and result != expected_metadata:
                raise TreeCaptureV5Error("metadata_plan_changed_before_write")
            return result
        st = os.fstat(source_fd)
        if canonical_json(list(v2._sig(st))) != expected_sig:
            raise TreeCaptureV5Error("source_object_changed_before_metadata")
        names = _xattr_names_fd(source_fd)
        if len(names) > 4096:
            raise TreeCaptureV5Error("metadata_xattr_count_limit")
        planned_xattrs = (
            {item["name"]: item["size_bytes"] for item in expected_metadata["xattrs"]}
            if expected_metadata is not None
            else None
        )
        decoded_names = []
        for attr_name in sorted(names):
            try:
                decoded_names.append(attr_name.decode("utf-8", errors="strict"))
            except UnicodeDecodeError as exc:
                raise TreeCaptureV5Error("metadata_xattr_name_invalid") from exc
        if planned_xattrs is not None and set(decoded_names) != set(planned_xattrs):
            raise TreeCaptureV5Error("metadata_plan_changed_before_write")
        total = 0
        refs = []
        values = []
        token = f"{ordinal:016x}"
        for attr_name, decoded_name in zip(sorted(names), decoded_names, strict=True):
            value = _getxattr_fd(source_fd, attr_name)
            if planned_xattrs is not None and len(value) != planned_xattrs[decoded_name]:
                raise TreeCaptureV5Error("metadata_plan_changed_before_write")
            total += len(value)
            if total > MAX_OBJECT_METADATA_BYTES:
                raise TreeCaptureV5Error("metadata_object_limit")
            values.append((decoded_name, value))
        acl = _acl_bytes(source_fd)
        planned_acl = expected_metadata.get("acl") if expected_metadata is not None else None
        if expected_metadata is not None and (
            (acl is None) != (planned_acl is None)
            or (acl is not None and len(acl) != planned_acl["size_bytes"])
            or expected_metadata.get("flags_value") != (getattr(st, "st_flags", 0) or None)
        ):
            raise TreeCaptureV5Error("metadata_plan_changed_before_write")
        if acl is not None:
            total += len(acl)
            if total > MAX_OBJECT_METADATA_BYTES:
                raise TreeCaptureV5Error("metadata_object_limit")
        if metadata_reserve_before + total > inventory.metadata_reserve_bytes:
            raise TreeCaptureV5Error("metadata_total_limit")
        inventory.check_remaining_capacity()
        for decoded_name, value in values:
            rel = f"{base}/metadata/{token}/{len(refs):06d}.bin"
            _create_candidate_member(inventory, rel, value)
            digest = sha256(value)
            ref = {"name": decoded_name, "path": rel, "sha256": digest, "size_bytes": len(value)}
            refs.append(ref)
            sidecar_pages.append({"path": rel, "sha256": digest, "size_bytes": len(value)})
        acl_ref = None
        if acl is not None:
            rel = f"{base}/metadata/{token}/acl.bin"
            _create_candidate_member(inventory, rel, acl)
            acl_ref = {"path": rel, "sha256": sha256(acl), "size_bytes": len(acl)}
            sidecar_pages.append(acl_ref)
        if v2._sig(os.fstat(source_fd)) != v2._sig(st):
            raise TreeCaptureV5Error("source_object_changed_during_metadata")
        return {
            "state": "observed",
            "xattrs": refs,
            "acl": acl_ref,
            "acl_state": "value_required" if acl is not None else "absent",
            "flags_state": "value_required" if getattr(st, "st_flags", 0) else "absent",
            "flags_value": getattr(st, "st_flags", 0) or None,
        }
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if parent_fd >= 0:
            os.close(parent_fd)
        if v2._sig(os.fstat(inventory.roots[role])) != inventory.root_signatures[role]:
            raise TreeCaptureV5Error("source_root_changed_during_metadata")


def _flush_json_page(
    writer: DescriptorWriter,
    base: str,
    category: str,
    page_no: int,
    records: list[dict[str, Any]],
    *,
    before_write: Callable[[], None] | None = None,
) -> dict[str, Any]:
    raw = b"\n".join(canonical_json(item).rstrip(b"\n") for item in records) + b"\n"
    path = f"{base}/{category}/{page_no:08d}.jsonl"
    if before_write is not None:
        before_write()
    writer.create(path, raw)
    return {"path": path, "sha256": sha256(raw), "count": len(records), "size_bytes": len(raw)}


def _candidate_member_record_v5(row: tuple[Any, ...]) -> dict[str, Any]:
    (
        role,
        path,
        kind,
        state,
        rule,
        disposition,
        source_device,
        source_inode,
        source_nlink,
        mode,
        uid,
        gid,
        size,
        mtime_ns,
        ctime_ns,
        atime_ns,
        birthtime_ns,
        flags,
        link_text,
        group_id,
        primary_role,
        primary_path,
        payload_id,
        payload_sha,
        payload_bytes,
        metadata_raw,
    ) = row
    return {
        "role": role,
        "path": path,
        "kind": kind,
        "state": state,
        "rule": rule,
        "disposition": disposition,
        "mode": mode,
        "source_device": source_device,
        "source_inode": source_inode,
        "source_nlink": source_nlink,
        "uid": uid,
        "gid": gid,
        "size_bytes": size,
        "mtime_ns": mtime_ns,
        "ctime_ns": ctime_ns,
        "atime_ns": atime_ns,
        "birthtime_ns": birthtime_ns,
        "flags": flags,
        "link_target_b64": base64.b64encode(link_text).decode("ascii")
        if link_text is not None
        else None,
        "group_id": group_id,
        "primary_role": primary_role,
        "primary_path": primary_path,
        "payload_id": payload_id,
        "payload_sha256": payload_sha,
        "payload_bytes": payload_bytes,
        "metadata": json.loads(metadata_raw) if metadata_raw is not None else None,
    }


def _canonical_page_bytes(records: list[dict[str, Any]]) -> bytes:
    return b"\n".join(canonical_json(item).rstrip(b"\n") for item in records) + b"\n"


def _simulate_payload_db_v5(db: sqlite3.Connection, candidate_id: str) -> None:
    """Replay capture's payload-page row writes with fixed-width hash placeholders."""
    query = (
        "SELECT payload_id,size FROM members WHERE state='included' "
        "AND kind='file' AND payload_id IS NOT NULL "
        "AND (group_id IS NULL OR (role=primary_role AND path=primary_path)) "
        "ORDER BY payload_id COLLATE BINARY"
    )
    for payload_id, size in db.execute(query):
        digest = hashlib.sha256(b"").hexdigest() if size == 0 else "0" * 64
        page_count = (size + STREAM_CHUNK_BYTES - 1) // STREAM_CHUNK_BYTES
        for page_no in range(page_count):
            page_size = min(STREAM_CHUNK_BYTES, size - page_no * STREAM_CHUNK_BYTES)
            relative = f"{CANDIDATE_BASE}/{candidate_id}/payload/{payload_id}/{page_no:08d}.bin"
            db.execute(
                "INSERT INTO payload_pages VALUES(?,?,?,?,?)",
                (payload_id, page_no, relative, "0" * 64, page_size),
            )
        db.execute(
            "UPDATE members SET payload_sha256=?,payload_bytes=? WHERE payload_id=?",
            (digest, size, payload_id),
        )
    db.commit()


def _root_dispositions_v5(
    view: HeldDataT,
    roots: Mapping[str, int],
    root_signatures: Mapping[str, tuple[int, ...]],
    policy_hashes: Mapping[str, str],
    policy_scopes: Mapping[str, str],
) -> list[dict[str, Any]]:
    dispositions = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        declaration = view.plan.root_roster[role]
        root_stat = os.fstat(roots[role]) if declaration.present else None
        if declaration.present:
            locator = declaration.relative_locator
            if not isinstance(locator, str):
                raise TreeCaptureV5Error("candidate_present_root_locator_invalid")
            locator_digest = sha256(locator.encode())
            unresolved_digest = None
        else:
            reason = declaration.unresolved_reason
            if not isinstance(reason, str) or not reason:
                raise TreeCaptureV5Error("candidate_absent_root_reason_invalid")
            locator_digest = None
            unresolved_digest = sha256(reason.encode())
        dispositions.append(
            {
                "role": role,
                "present": bool(declaration.present),
                "kind": declaration.kind if declaration.present else None,
                "scope": policy_scopes.get(role),
                "disposition": (
                    "protected_opaque"
                    if declaration.present and policy_scopes.get(role) == "protected_opaque"
                    else "included_root"
                    if declaration.present
                    else "absent_unresolved"
                ),
                "locator_sha256": locator_digest,
                "policy_sha256": policy_hashes.get(role),
                "root_signature": (
                    list(root_signatures[role])
                    if declaration.present and role in root_signatures
                    else None
                ),
                "root_stat": (
                    {
                        "dev": root_stat.st_dev,
                        "ino": root_stat.st_ino,
                        "nlink": root_stat.st_nlink,
                        "mode": root_stat.st_mode,
                        "uid": root_stat.st_uid,
                        "gid": root_stat.st_gid,
                        "size": root_stat.st_size,
                        "mtime_ns": root_stat.st_mtime_ns,
                        "ctime_ns": root_stat.st_ctime_ns,
                        "atime_ns": root_stat.st_atime_ns,
                        "birthtime_ns": getattr(root_stat, "st_birthtime_ns", 0),
                        "flags": getattr(root_stat, "st_flags", 0),
                    }
                    if root_stat is not None
                    else None
                ),
                "unresolved_reason_sha256": unresolved_digest,
            }
        )
    return dispositions


def _page_ref_v5(
    base: str, category: str, page_no: int, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    raw = _canonical_page_bytes(rows)
    if len(raw) > MAX_PAGE_BYTES:
        raise TreeCaptureV5Error("candidate_member_page_limit")
    return {
        "path": f"{base}/{category}/{page_no:08d}.jsonl",
        "sha256": "0" * 64,
        "count": len(rows),
        "size_bytes": len(raw),
    }


def _candidate_json_bytes_v5(
    db: sqlite3.Connection,
    *,
    view: HeldDataT,
    roots: Mapping[str, int],
    root_signatures: Mapping[str, tuple[int, ...]],
    policy_hashes: Mapping[str, str],
    policy_scopes: Mapping[str, str],
    plan_sha256: str,
    policy_roster_sha256: str,
    included_source_bytes: int,
    metadata_reserve_bytes: int,
    candidate_id: str,
    operation_spec_sha256: str | None,
) -> dict[str, Any]:
    """Count exact canonical candidate JSON lengths with fixed-width digests."""
    base = f"{CANDIDATE_BASE}/{candidate_id}"
    member_refs: list[dict[str, Any]] = []
    member_batch: list[dict[str, Any]] = []
    member_bytes = 0
    member_page_no = 0
    query = (
        "SELECT role,path,kind,state,rule,disposition,device,inode,nlink,mode,uid,gid,"
        "size,mtime_ns,ctime_ns,atime_ns,birthtime_ns,flags,link_text,group_id,"
        "primary_role,primary_path,payload_id,payload_sha256,payload_bytes,metadata_json "
        "FROM members ORDER BY role COLLATE BINARY,path COLLATE BINARY"
    )
    for row in db.execute(query):
        record = _candidate_member_record_v5(row)
        encoded_size = len(canonical_json(record)) + 1
        if encoded_size > MAX_PAGE_BYTES:
            raise TreeCaptureV5Error("candidate_member_record_limit")
        if member_batch and (
            len(member_batch) >= PAGE_RECORDS or member_bytes + encoded_size > MAX_PAGE_BYTES
        ):
            member_refs.append(_page_ref_v5(base, "members", member_page_no, member_batch))
            member_page_no += 1
            member_batch = []
            member_bytes = 0
        member_batch.append(record)
        member_bytes += encoded_size
    if member_batch:
        member_refs.append(_page_ref_v5(base, "members", member_page_no, member_batch))

    payload_refs: list[dict[str, Any]] = []
    payload_batch: list[dict[str, Any]] = []
    payload_batch_bytes = 0
    payload_page_no = 0
    for payload_id, page_no, path, _page_sha, size_bytes in db.execute(
        "SELECT payload_id,page,path,sha256,size_bytes FROM payload_pages "
        "ORDER BY payload_id COLLATE BINARY,page"
    ):
        full = db.execute(
            "SELECT payload_sha256,payload_bytes FROM members WHERE payload_id=? LIMIT 1",
            (payload_id,),
        ).fetchone()
        if full is None:
            raise TreeCaptureV5Error("candidate_payload_index_inconsistent")
        item = {
            "payload_id": payload_id,
            "page": page_no,
            "path": path,
            "sha256": "0" * 64,
            "size_bytes": size_bytes,
            "payload_sha256": full[0],
            "payload_bytes": full[1],
        }
        encoded_size = len(canonical_json(item)) + 1
        if payload_batch and (
            len(payload_batch) >= PAGE_RECORDS
            or payload_batch_bytes + encoded_size > MAX_PAGE_BYTES
        ):
            payload_refs.append(_page_ref_v5(base, "payload-index", payload_page_no, payload_batch))
            payload_page_no += 1
            payload_batch = []
            payload_batch_bytes = 0
        payload_batch.append(item)
        payload_batch_bytes += encoded_size
    if payload_batch:
        payload_refs.append(_page_ref_v5(base, "payload-index", payload_page_no, payload_batch))

    sidecar_manifest: dict[str, dict[str, Any]] = {}
    for row in db.execute("SELECT metadata_json FROM members WHERE metadata_json IS NOT NULL"):
        metadata = json.loads(row[0])
        refs = list(metadata.get("xattrs", []))
        if metadata.get("acl") is not None:
            refs.append(metadata["acl"])
        for sidecar in refs:
            ref = {
                "path": sidecar["path"],
                "sha256": "0" * 64,
                "size_bytes": sidecar["size_bytes"],
            }
            prior = sidecar_manifest.setdefault(ref["path"], ref)
            if prior != ref:
                raise TreeCaptureV5Error("candidate_sidecar_reference_conflict")
    sidecar_pages = [sidecar_manifest[path] for path in sorted(sidecar_manifest)]
    payload_page_file_count = int(db.execute("SELECT count(*) FROM payload_pages").fetchone()[0])
    payload_directory_count = int(
        db.execute("SELECT count(DISTINCT payload_id) FROM payload_pages").fetchone()[0]
    )
    metadata_directory_count = len(
        {ref["path"].rsplit("/", 1)[0].rsplit("/", 1)[-1] for ref in sidecar_pages}
    )
    entry_count = int(db.execute("SELECT count(*) FROM members").fetchone()[0])
    average_path_chars = int(
        db.execute("SELECT coalesce(avg(length(path)),0) FROM members").fetchone()[0]
    )
    resource_model = model_candidate_capacity_v5(
        entry_count,
        included_source_bytes,
        average_path_chars=average_path_chars,
        metadata_reserve_bytes=metadata_reserve_bytes,
    )
    index: dict[str, Any] = {
        "schema": "brain-host-tree-capture-v5-candidate-bound-v1"
        if operation_spec_sha256 is not None
        else "brain-host-tree-capture-v5-candidate",
        "candidate_id": candidate_id,
        "synthetic_fixture_only": bool(view.synthetic),
        "plan_sha256": plan_sha256,
        "policy_roster_sha256": policy_roster_sha256,
        "role_dispositions": _root_dispositions_v5(
            view, roots, root_signatures, policy_hashes, policy_scopes
        ),
        "opaque_waivers": [dict(GIT_OPAQUE_WAIVER)],
        "member_pages": member_refs,
        "payload_pages": payload_refs,
        "sidecar_pages": sidecar_pages,
        "entry_count": entry_count,
        "payload_bytes": included_source_bytes,
        "resource_model": resource_model,
        "xattr_acl_flags_restore_policy": "evidence_only_never_applied",
    }
    if operation_spec_sha256 is not None:
        index["operation_spec_sha256"] = operation_spec_sha256
    index_bytes = len(canonical_json(index))
    if index_bytes > MAX_INDEX_BYTES:
        raise TreeCaptureV5Error("candidate_index_limit")
    readback = {
        "schema": "brain-host-tree-capture-v5-final-readback-witness",
        "candidate_id": candidate_id,
        "index_sha256": "0" * 64,
        "member_count": entry_count,
        "payload_bytes": included_source_bytes,
    }
    readback_bytes = len(canonical_json(readback))
    if readback_bytes > MAX_PAGE_BYTES:
        raise TreeCaptureV5Error("candidate_readback_limit")
    member_page_bytes = sum(ref["size_bytes"] for ref in member_refs)
    payload_index_bytes = sum(ref["size_bytes"] for ref in payload_refs)
    return {
        "member_page_bytes": member_page_bytes,
        "payload_index_bytes": payload_index_bytes,
        "index_bytes": index_bytes,
        "readback_bytes": readback_bytes,
        "sidecar_value_bytes": sum(ref["size_bytes"] for ref in sidecar_pages),
        "sidecar_file_count": len(sidecar_pages),
        "metadata_directory_count": metadata_directory_count,
        "payload_page_file_count": payload_page_file_count,
        "payload_directory_count": payload_directory_count,
        "member_page_count": len(member_refs),
        "payload_index_page_count": len(payload_refs),
        "candidate_json_bytes": (
            member_page_bytes + payload_index_bytes + index_bytes + readback_bytes
        ),
    }


def _durable_output_allocation_bytes(
    writer: DescriptorWriter, relative: str, expected_size: int
) -> int:
    """Measure only a completed, writer-fsynced file through its held descriptor."""
    fd = _open_output_file(writer.root_fd, relative)
    try:
        held = os.fstat(fd)
        if (
            not stat.S_ISREG(held.st_mode)
            or held.st_dev != writer.device
            or held.st_uid != os.getuid()
            or stat.S_IMODE(held.st_mode) != 0o600
            or held.st_nlink != 1
            or held.st_size != expected_size
        ):
            raise TreeCaptureV5Error("candidate_output_allocation_identity_invalid")
        blocks = getattr(held, "st_blocks", None)
        if type(blocks) is not int or blocks <= 0:
            # Do not release capacity on filesystems that cannot report
            # attributable allocation. The live free-space guard remains active.
            return 0
        return blocks * 512
    finally:
        os.close(fd)


def _create_candidate_member(inventory: GlobalInventoryV5, relative: str, payload: bytes) -> int:
    planned = inventory.metadata_bytes_remaining is not None
    if planned:
        inventory.check_remaining_capacity()
    if inventory.candidate_write_guard is not None:
        inventory.candidate_write_guard()
    inventory.writer.create(relative, payload)
    allocated = _durable_output_allocation_bytes(inventory.writer, relative, len(payload))
    if planned and "/metadata/" in relative:
        inventory.consume_metadata_bytes(allocated)
    elif planned and (
        "/members/" in relative
        or "/payload-index/" in relative
        or relative.endswith("/INDEX.json")
        or relative.endswith("/READBACK.json")
    ):
        inventory.consume_json_bytes(allocated)
    return allocated if planned else len(payload)


def _open_output_file(root_fd: int, relative: str) -> int:
    parts = relative.split("/")
    current = os.dup(root_fd)
    try:
        _validate_output_directory_fd(root_fd, current)
        for component in parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            _validate_output_directory_fd(root_fd, child, parent_fd=current, name=component)
            os.close(current)
            current = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        os.close(current)
        return fd
    except BaseException:
        os.close(current)
        raise


def _open_output_directory(root_fd: int, relative: str) -> int:
    current = os.dup(root_fd)
    try:
        _validate_output_directory_fd(root_fd, current)
        for component in relative.split("/"):
            if component in {"", ".", ".."}:
                raise TreeCaptureV5Error("candidate_path_invalid")
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            _validate_output_directory_fd(root_fd, child, parent_fd=current, name=component)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _validate_output_directory_fd(
    root_fd: int,
    directory_fd: int,
    *,
    parent_fd: int | None = None,
    name: str | None = None,
) -> None:
    root_stat = os.fstat(root_fd)
    directory_stat = os.fstat(directory_fd)
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_dev != root_stat.st_dev
        or directory_stat.st_uid != os.getuid()
        or stat.S_IMODE(directory_stat.st_mode) != 0o700
    ):
        raise TreeCaptureV5Error("candidate_directory_component_invalid")
    if parent_fd is not None and name is not None:
        try:
            path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise TreeCaptureV5Error("candidate_directory_component_replaced") from exc
        if not stat.S_ISDIR(path_stat.st_mode) or (path_stat.st_dev, path_stat.st_ino) != (
            directory_stat.st_dev,
            directory_stat.st_ino,
        ):
            raise TreeCaptureV5Error("candidate_directory_component_replaced")


def _read_output(root_fd: int, relative: str, limit: int) -> bytes:
    fd = _open_output_file(root_fd, relative)
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_size > limit
            or st.st_dev != os.fstat(root_fd).st_dev
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o600
            or st.st_nlink != 1
        ):
            raise TreeCaptureV5Error("candidate_member_invalid")
        chunks = []
        total = 0
        while chunk := os.read(fd, min(1024 * 1024, limit + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise TreeCaptureV5Error("candidate_member_limit")
        if total != st.st_size:
            raise TreeCaptureV5Error("candidate_member_changed")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _candidate_read(root_fd: int, base: str, relative: str, limit: int) -> bytes:
    prefix = f"{base}/"
    if not isinstance(relative, str) or not relative.startswith(prefix):
        raise TreeCaptureV5Error("candidate_path_outside_candidate")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} or "\\" in part for part in parts):
        raise TreeCaptureV5Error("candidate_path_invalid")
    return _read_output(root_fd, "/".join(parts[len(base.split("/")) :]), limit)


def _validate_candidate_directory(writer: DescriptorWriter, candidate_fd: int) -> tuple[int, int]:
    st = os.fstat(candidate_fd)
    if (
        not stat.S_ISDIR(st.st_mode)
        or st.st_dev != writer.device
        or st.st_uid != os.getuid()
        or stat.S_IMODE(st.st_mode) != 0o700
    ):
        raise TreeCaptureV5Error("candidate_directory_identity_invalid")
    return st.st_dev, st.st_ino


def _recheck_candidate_binding(
    writer: DescriptorWriter,
    candidate_id: str,
    candidate_fd: int,
    candidate_identity: tuple[int, int],
    *,
    allow_unbound_synthetic: bool = False,
) -> None:
    try:
        if writer.storage_guard is None:
            if not allow_unbound_synthetic:
                raise TreeCaptureV5Error("candidate_storage_binding_invalid")
        else:
            writer.assert_storage_binding(writer.storage_guard)
        parent_fd = _open_output_directory(writer.root_fd, CANDIDATE_BASE)
        try:
            current = os.stat(candidate_id, dir_fd=parent_fd, follow_symlinks=False)
        finally:
            os.close(parent_fd)
    except Exception as exc:
        raise TreeCaptureV5Error("candidate_storage_binding_changed") from exc
    held = os.fstat(candidate_fd)
    if (
        (held.st_dev, held.st_ino) != candidate_identity
        or not stat.S_ISDIR(current.st_mode)
        or (current.st_dev, current.st_ino) != candidate_identity
    ):
        raise TreeCaptureV5Error("candidate_directory_replaced")


def _cleanup_empty_created_directory(
    parent_fd: int,
    name: str,
    directory_fd: int | None,
    identity: tuple[int, int] | None,
) -> None:
    """Remove only this run's still-bound, empty nonce directory."""
    if identity is None:
        raise TreeCaptureV5Error("created_directory_identity_unavailable")
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != identity:
        raise TreeCaptureV5Error("created_directory_replaced_during_cleanup")
    opened_here = directory_fd is None
    if directory_fd is None:
        directory_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        held = os.fstat(directory_fd)
        if (held.st_dev, held.st_ino) != identity:
            raise TreeCaptureV5Error("created_directory_replaced_during_cleanup")
        if os.listdir(directory_fd):
            raise TreeCaptureV5Error("created_directory_unexpected_contents")
        # Recheck immediately before the descriptor-relative rmdir. A changed
        # nonce binding is left for review.
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        held = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(current.st_mode)
            or (held.st_dev, held.st_ino) != identity
            or (current.st_dev, current.st_ino) != identity
        ):
            raise TreeCaptureV5Error("created_directory_replaced_during_cleanup")
        os.rmdir(name, dir_fd=parent_fd)
    finally:
        if opened_here:
            os.close(directory_fd)


def _candidate_file_count(directory_fd: int) -> int:
    count = 0
    with os.scandir(directory_fd) as scan:
        for entry in scan:
            name = entry.name
            st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(st.st_mode):
                child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd
                )
                try:
                    child_stat = os.fstat(child)
                    if (
                        child_stat.st_dev != os.fstat(directory_fd).st_dev
                        or child_stat.st_uid != os.getuid()
                        or stat.S_IMODE(child_stat.st_mode) != 0o700
                    ):
                        raise TreeCaptureV5Error("candidate_unexpected_directory_identity")
                    child_files = _candidate_file_count(child)
                    if child_files == 0:
                        raise TreeCaptureV5Error("candidate_unexpected_empty_directory")
                    count += child_files
                finally:
                    os.close(child)
            elif stat.S_ISREG(st.st_mode):
                if (
                    st.st_dev != os.fstat(directory_fd).st_dev
                    or st.st_uid != os.getuid()
                    or stat.S_IMODE(st.st_mode) != 0o600
                    or st.st_nlink != 1
                ):
                    raise TreeCaptureV5Error("candidate_unexpected_file_identity")
                count += 1
            else:
                raise TreeCaptureV5Error("candidate_unexpected_entry_type")
    return count


def _open_candidate_verify_db(
    scratch_parent_fd: int | None = None,
) -> tuple[int, str, int, int, sqlite3.Connection]:
    """Open bounded verifier scratch under a caller-pinned directory when supplied."""
    parent_fd = (
        os.dup(scratch_parent_fd)
        if scratch_parent_fd is not None
        else os.open(tempfile.gettempdir(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    )
    parent_stat = os.fstat(parent_fd)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.getuid()
        or stat.S_IMODE(parent_stat.st_mode) != 0o700
    ):
        os.close(parent_fd)
        raise TreeCaptureV5Error("candidate_verify_scratch_parent_invalid")
    name = f"brain-capture-v5-verify-{secrets.token_hex(16)}"
    created_directory = False
    created_identity: tuple[int, int] | None = None
    directory_fd = -1
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        created_directory = True
        created_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(created_stat.st_mode):
            raise TreeCaptureV5Error("candidate_verify_scratch_directory_invalid")
        created_identity = (created_stat.st_dev, created_stat.st_ino)
        directory_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        opened_stat = os.fstat(directory_fd)
        if (opened_stat.st_dev, opened_stat.st_ino) != created_identity:
            raise TreeCaptureV5Error("candidate_verify_scratch_directory_replaced")
    except BaseException:
        if created_directory and created_identity is not None:
            cleanup_fd = -1
            try:
                current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    stat.S_ISDIR(current.st_mode)
                    and (current.st_dev, current.st_ino) == created_identity
                ):
                    cleanup_fd = (
                        os.dup(directory_fd)
                        if directory_fd >= 0
                        else os.open(
                            name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent_fd,
                        )
                    )
                    opened = os.fstat(cleanup_fd)
                    if (opened.st_dev, opened.st_ino) == created_identity and not os.listdir(
                        cleanup_fd
                    ):
                        os.rmdir(name, dir_fd=parent_fd)
            except BaseException:
                pass
            finally:
                if cleanup_fd >= 0:
                    os.close(cleanup_fd)
        if directory_fd >= 0:
            os.close(directory_fd)
        os.close(parent_fd)
        raise TreeCaptureV5Error("candidate_verify_scratch_create_failed") from None
    database_fd = -1
    db: sqlite3.Connection | None = None
    try:
        directory_stat = os.fstat(directory_fd)
        if directory_stat.st_uid != os.getuid() or stat.S_IMODE(directory_stat.st_mode) != 0o700:
            raise TreeCaptureV5Error("candidate_verify_scratch_identity_invalid")
        database_fd = os.open(
            "verify.sqlite",
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        database_stat = os.fstat(database_fd)
        if (
            not stat.S_ISREG(database_stat.st_mode)
            or database_stat.st_uid != os.getuid()
            or stat.S_IMODE(database_stat.st_mode) != 0o600
            or database_stat.st_nlink != 1
            or database_stat.st_dev != directory_stat.st_dev
        ):
            raise TreeCaptureV5Error("candidate_verify_database_identity_invalid")
        db_path = f"/dev/fd/{database_fd}"
        db = sqlite3.connect(db_path)
        if db.execute("PRAGMA journal_mode=OFF").fetchone() != ("off",):
            raise TreeCaptureV5Error("candidate_verify_journal_mode_invalid")
        db.execute("PRAGMA synchronous=OFF")
        db.execute("PRAGMA temp_store=MEMORY")
        if db.execute("PRAGMA temp_store").fetchone() != (2,):
            raise TreeCaptureV5Error("candidate_verify_temp_store_invalid")
        db.execute("PRAGMA cache_size=-8192")
        db.execute("PRAGMA mmap_size=0")
        if db.execute("PRAGMA database_list").fetchone()[2] != db_path:
            raise TreeCaptureV5Error("candidate_verify_database_path_invalid")
        db.execute(
            "CREATE TABLE members(role TEXT,path TEXT,kind TEXT,state TEXT,group_id TEXT,"
            "primary_role TEXT,primary_path TEXT,payload_id TEXT,payload_sha256 TEXT,"
            "payload_bytes INTEGER,source_device INTEGER,source_inode INTEGER,source_nlink INTEGER,"
            "metadata_json BLOB,PRIMARY KEY(role,path))"
        )
        db.execute("CREATE INDEX verify_member_payload ON members(payload_id)")
        db.execute("CREATE INDEX verify_member_group ON members(group_id)")
        db.execute("CREATE TABLE sidecars(path TEXT PRIMARY KEY,sha256 TEXT,size_bytes INTEGER)")
        db.execute(
            "CREATE TABLE payload_pages(payload_id TEXT,page INTEGER,path TEXT UNIQUE,"
            "sha256 TEXT,size_bytes INTEGER,payload_sha256 TEXT,payload_bytes INTEGER,"
            "PRIMARY KEY(payload_id,page))"
        )
        return parent_fd, name, directory_fd, database_fd, db
    except BaseException as exc:
        if db is not None:
            db.close()
        database_identity = None
        if database_fd >= 0:
            try:
                database_stat = os.fstat(database_fd)
                database_identity = (database_stat.st_dev, database_stat.st_ino)
            except OSError:
                pass
        try:
            if database_identity is not None:
                current = os.stat("verify.sqlite", dir_fd=directory_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (
                    database_identity[0],
                    database_identity[1],
                ):
                    os.unlink("verify.sqlite", dir_fd=directory_fd)
        except BaseException:
            pass
        if database_fd >= 0:
            try:
                os.close(database_fd)
            except OSError:
                pass
        try:
            os.close(directory_fd)
            current_directory = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (current_directory.st_dev, current_directory.st_ino) == (
                directory_stat.st_dev,
                directory_stat.st_ino,
            ):
                os.rmdir(name, dir_fd=parent_fd)
        except BaseException:
            pass
        os.close(parent_fd)
        if isinstance(exc, TreeCaptureV5Error):
            raise
        raise TreeCaptureV5Error("candidate_verify_scratch_unavailable") from exc


def _close_candidate_verify_db(
    parent_fd: int,
    name: str,
    directory_fd: int,
    database_fd: int,
    db: sqlite3.Connection,
) -> None:
    directory_identity = os.fstat(directory_fd)
    database_identity = os.fstat(database_fd)
    db.close()
    os.close(database_fd)
    cleanup_error: BaseException | None = None
    try:
        current = os.stat("verify.sqlite", dir_fd=directory_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (
            database_identity.st_dev,
            database_identity.st_ino,
        ):
            raise TreeCaptureV5Error("candidate_verify_database_replaced")
        os.unlink("verify.sqlite", dir_fd=directory_fd)
    except BaseException as exc:
        cleanup_error = exc
    try:
        current = os.fstat(directory_fd)
        if (current.st_dev, current.st_ino) != (
            directory_identity.st_dev,
            directory_identity.st_ino,
        ):
            raise TreeCaptureV5Error("candidate_verify_scratch_replaced")
        current_directory = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (current_directory.st_dev, current_directory.st_ino) != (
            directory_identity.st_dev,
            directory_identity.st_ino,
        ):
            raise TreeCaptureV5Error("candidate_verify_scratch_replaced")
        os.rmdir(name, dir_fd=parent_fd)
    except BaseException as exc:
        cleanup_error = cleanup_error or exc
    finally:
        os.close(directory_fd)
        os.close(parent_fd)
    if cleanup_error is not None:
        raise TreeCaptureV5Error("candidate_verify_scratch_cleanup_incomplete") from cleanup_error


def _prove_candidate_verify_scratch_writable(scratch_parent_fd: int | None) -> None:
    """Prove pinned scratch can create/write private files before reading a candidate."""
    if scratch_parent_fd is None:
        raise TreeCaptureV5Error("candidate_verify_scratch_capability_required")
    parent_fd = os.dup(scratch_parent_fd)
    parent_stat = os.fstat(parent_fd)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.getuid()
        or stat.S_IMODE(parent_stat.st_mode) != 0o700
    ):
        os.close(parent_fd)
        raise TreeCaptureV5Error("candidate_verify_scratch_parent_invalid")
    directory_name = f"brain-capture-v5-probe-{secrets.token_hex(16)}"
    directory_fd = file_fd = -1
    dir_identity: tuple[int, int] | None = None
    file_identity: tuple[int, int] | None = None
    cleanup_ok = False
    failure: BaseException | None = None
    try:
        os.mkdir(directory_name, mode=0o700, dir_fd=parent_fd)
        st = os.stat(directory_name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(st.st_mode)
            or st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o700
        ):
            raise TreeCaptureV5Error("candidate_verify_scratch_probe_directory_invalid")
        dir_identity = st.st_dev, st.st_ino
        directory_fd = os.open(
            directory_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
        )
        held_dir = os.fstat(directory_fd)
        if (held_dir.st_dev, held_dir.st_ino) != dir_identity:
            raise TreeCaptureV5Error("candidate_verify_scratch_probe_directory_replaced")
        file_fd = os.open(
            "write-probe",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        file_stat = os.fstat(file_fd)
        if (
            not stat.S_ISREG(file_stat.st_mode)
            or file_stat.st_uid != os.getuid()
            or stat.S_IMODE(file_stat.st_mode) != 0o600
            or file_stat.st_nlink != 1
            or file_stat.st_dev != held_dir.st_dev
        ):
            raise TreeCaptureV5Error("candidate_verify_scratch_probe_file_invalid")
        file_identity = file_stat.st_dev, file_stat.st_ino
        probe = b"brain-capture-v5-scratch-write-proof"
        if os.write(file_fd, probe) != len(probe):
            raise TreeCaptureV5Error("candidate_verify_scratch_write_proof_failed")
        os.fsync(file_fd)
        current_file = os.stat("write-probe", dir_fd=directory_fd, follow_symlinks=False)
        if (current_file.st_dev, current_file.st_ino) != file_identity:
            raise TreeCaptureV5Error("candidate_verify_scratch_probe_file_replaced")
    except BaseException as exc:
        failure = exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if directory_fd >= 0:
            try:
                if file_identity is not None:
                    current = os.stat("write-probe", dir_fd=directory_fd, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == file_identity:
                        os.unlink("write-probe", dir_fd=directory_fd)
                if dir_identity is not None and not os.listdir(directory_fd):
                    current = os.stat(directory_name, dir_fd=parent_fd, follow_symlinks=False)
                    held = os.fstat(directory_fd)
                    if (
                        (current.st_dev, current.st_ino)
                        == dir_identity
                        == (held.st_dev, held.st_ino)
                    ):
                        os.rmdir(directory_name, dir_fd=parent_fd)
                        cleanup_ok = True
            except OSError:
                cleanup_ok = False
            os.close(directory_fd)
        os.close(parent_fd)
    if failure is not None:
        if not cleanup_ok:
            raise TreeCaptureV5Error(
                "candidate_verify_scratch_probe_cleanup_incomplete"
            ) from failure
        if isinstance(failure, TreeCaptureV5Error):
            raise failure
        raise TreeCaptureV5Error("candidate_verify_scratch_write_proof_failed") from failure
    if not cleanup_ok:
        raise TreeCaptureV5Error("candidate_verify_scratch_probe_cleanup_incomplete")


def _validate_candidate_relationships(db: sqlite3.Connection) -> None:
    for row in db.execute(
        "SELECT payload_id,count(*),min(payload_sha256),max(payload_sha256),"
        "min(payload_bytes),max(payload_bytes),count(DISTINCT group_id),"
        "min(group_id),max(group_id),count(DISTINCT primary_role),min(primary_role),"
        "max(primary_role),count(DISTINCT primary_path),min(primary_path),max(primary_path),"
        "count(DISTINCT source_device),min(source_device),max(source_device),"
        "count(DISTINCT source_inode),min(source_inode),max(source_inode),"
        "count(DISTINCT source_nlink),min(source_nlink),max(source_nlink),"
        "count(DISTINCT metadata_json) "
        "FROM members WHERE state='included' AND kind='file' GROUP BY payload_id "
        "ORDER BY payload_id COLLATE BINARY"
    ):
        (
            payload_id,
            aliases,
            digest_min,
            digest_max,
            bytes_min,
            bytes_max,
            group_variants,
            group_min,
            group_max,
            primary_role_variants,
            primary_role_min,
            primary_role_max,
            primary_path_variants,
            primary_path_min,
            primary_path_max,
            device_variants,
            source_device_min,
            source_device_max,
            inode_variants,
            source_inode_min,
            source_inode_max,
            nlink_variants,
            source_nlink_min,
            source_nlink_max,
            metadata_variants,
        ) = row
        if (
            not _is_sha256(payload_id)
            or digest_min != digest_max
            or bytes_min != bytes_max
            or metadata_variants != 1
        ):
            raise TreeCaptureV5Error("candidate_payload_member_join_invalid")
        if group_min is None:
            primary = db.execute(
                "SELECT role,path,source_nlink FROM members "
                "WHERE payload_id=? AND state='included' "
                "AND kind='file'",
                (payload_id,),
            ).fetchall()
            if (
                aliases != 1
                or group_variants != 0
                or primary_role_variants != 0
                or primary_path_variants != 0
                or primary_role_min is not None
                or primary_path_min is not None
                or device_variants != 1
                or inode_variants != 1
                or nlink_variants != 1
                or source_nlink_min != 1
                or len(primary) != 1
                or payload_id != sha256(f"{primary[0][0]}\0{primary[0][1]}".encode())
            ):
                raise TreeCaptureV5Error("candidate_ungrouped_file_topology_invalid")
        else:
            if (
                group_min != group_max
                or group_variants != 1
                or primary_role_variants != 1
                or primary_path_variants != 1
                or not _is_sha256(group_min)
                or device_variants != 1
                or inode_variants != 1
                or nlink_variants != 1
                or aliases != source_nlink_min
                or primary_role_min is None
                or primary_path_min is None
                or payload_id != sha256(f"{primary_role_min}\0{primary_path_min}".encode())
                or group_min != sha256(f"{source_device_min}:{source_inode_min}".encode())
            ):
                raise TreeCaptureV5Error("candidate_hardlink_topology_invalid")
            primary_count = db.execute(
                "SELECT count(*) FROM members WHERE payload_id=? AND role=? AND path=? "
                "AND group_id=? AND state='included' AND kind='file'",
                (payload_id, primary_role_min, primary_path_min, group_min),
            ).fetchone()[0]
            if primary_count != 1:
                raise TreeCaptureV5Error("candidate_hardlink_primary_invalid")
        payload_bytes = bytes_min
        pages = db.execute(
            "SELECT count(*),coalesce(sum(size_bytes),0),min(payload_sha256),"
            "max(payload_sha256),min(payload_bytes),max(payload_bytes),min(page),max(page) "
            "FROM payload_pages WHERE payload_id=?",
            (payload_id,),
        ).fetchone()
        (
            page_count,
            page_bytes,
            page_digest_min,
            page_digest_max,
            page_size_min,
            page_size_max,
            page_min,
            page_max,
        ) = pages
        expected_pages = (payload_bytes + STREAM_CHUNK_BYTES - 1) // STREAM_CHUNK_BYTES
        if payload_bytes == 0:
            if digest_min != hashlib.sha256(b"").hexdigest() or page_count != 0:
                raise TreeCaptureV5Error("candidate_zero_byte_payload_invalid")
        elif (
            page_count != expected_pages
            or page_bytes != payload_bytes
            or page_digest_min != digest_min
            or page_digest_max != digest_max
            or page_size_min != payload_bytes
            or page_size_max != payload_bytes
            or page_min != 0
            or page_max != page_count - 1
        ):
            raise TreeCaptureV5Error("candidate_payload_page_closure_invalid")
    invalid_payload_page = db.execute(
        "SELECT 1 FROM payload_pages p LEFT JOIN members m ON "
        "m.payload_id=p.payload_id AND m.state='included' AND m.kind='file' "
        "WHERE m.role IS NULL OR m.payload_bytes=0 LIMIT 1"
    ).fetchone()
    if invalid_payload_page is not None:
        raise TreeCaptureV5Error("candidate_unreferenced_payload_pages")


def _verify_index_preflight_v5(
    candidate_fd: int,
    base: str,
    candidate_id: str,
    *,
    expected_index_sha256: str | None,
    expected_plan_sha256: str | None,
    expected_operation_spec_sha256: str | None,
) -> tuple[bytes, dict[str, Any], int]:
    """Read bounded index/member pages and estimate SQLite needs before opening its DB."""
    raw = _candidate_read(candidate_fd, base, f"{base}/INDEX.json", MAX_INDEX_BYTES)
    digest = sha256(raw)
    if expected_index_sha256 is not None and (
        not _is_sha256(expected_index_sha256) or digest != expected_index_sha256
    ):
        raise TreeCaptureV5Error("candidate_index_pin_mismatch")
    try:
        index = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TreeCaptureV5Error("candidate_index_json_invalid") from exc
    legacy_fields = {
        "schema",
        "candidate_id",
        "synthetic_fixture_only",
        "plan_sha256",
        "policy_roster_sha256",
        "role_dispositions",
        "opaque_waivers",
        "member_pages",
        "payload_pages",
        "sidecar_pages",
        "entry_count",
        "payload_bytes",
        "resource_model",
        "xattr_acl_flags_restore_policy",
    }
    bound_fields = legacy_fields | {"operation_spec_sha256"}
    if not isinstance(index, dict) or set(index) not in (legacy_fields, bound_fields):
        raise TreeCaptureV5Error("candidate_index_fields_invalid")
    is_spec_bound = set(index) == bound_fields
    if (
        index.get("schema")
        != (
            "brain-host-tree-capture-v5-candidate-bound-v1"
            if is_spec_bound
            else "brain-host-tree-capture-v5-candidate"
        )
        or index.get("candidate_id") != candidate_id
        or type(index.get("synthetic_fixture_only")) is not bool
        or type(index.get("entry_count")) is not int
        or not 1 <= index["entry_count"] <= MAX_ENTRIES
        or type(index.get("payload_bytes")) is not int
        or not 0 <= index["payload_bytes"] <= MAX_SOURCE_BYTES
        or index.get("xattr_acl_flags_restore_policy") != "evidence_only_never_applied"
        or not isinstance(index.get("resource_model"), dict)
        or not _is_sha256(index.get("plan_sha256"))
        or not _is_sha256(index.get("policy_roster_sha256"))
        or (is_spec_bound and not _is_sha256(index.get("operation_spec_sha256")))
        or (expected_plan_sha256 is not None and index["plan_sha256"] != expected_plan_sha256)
    ):
        raise TreeCaptureV5Error("candidate_index_value_invalid")
    if expected_operation_spec_sha256 is not None and (
        not is_spec_bound or index.get("operation_spec_sha256") != expected_operation_spec_sha256
    ):
        raise TreeCaptureV5Error("candidate_operation_spec_pin_mismatch")
    if not isinstance(index["role_dispositions"], list) or len(index["role_dispositions"]) != len(
        REQUIRED_ROOT_ROLES
    ):
        raise TreeCaptureV5Error("candidate_root_plan_invalid")
    if (
        policy_roster_sha256_v5(index["plan_sha256"], index["role_dispositions"])
        != index["policy_roster_sha256"]
    ):
        raise TreeCaptureV5Error("candidate_policy_roster_digest_mismatch")

    member_pages = index["member_pages"]
    payload_pages = index["payload_pages"]
    sidecar_pages = index["sidecar_pages"]
    if not all(isinstance(value, list) for value in (member_pages, payload_pages, sidecar_pages)):
        raise TreeCaptureV5Error("candidate_page_index_invalid")
    if (
        len(member_pages) > index["entry_count"]
        or len(payload_pages) > MAX_ENTRIES
        or len(sidecar_pages) > MAX_ENTRIES
    ):
        raise TreeCaptureV5Error("candidate_page_index_limit_invalid")
    member_page_bytes = payload_index_bytes = sidecar_index_bytes = 0
    declared_member_count = 0
    sidecar_count = sidecar_path_bytes = 0
    for page_no, ref in enumerate(member_pages):
        if (
            not isinstance(ref, dict)
            or set(ref) != {"path", "sha256", "count", "size_bytes"}
            or ref.get("path") != f"{base}/members/{page_no:08d}.jsonl"
            or not _is_sha256(ref.get("sha256"))
            or type(ref.get("count")) is not int
            or type(ref.get("size_bytes")) is not int
            or not 1 <= ref["count"] <= index["entry_count"]
            or not 1 <= ref["size_bytes"] <= MAX_PAGE_BYTES
        ):
            raise TreeCaptureV5Error("candidate_member_page_index_invalid")
        page = _candidate_read(candidate_fd, base, ref["path"], MAX_PAGE_BYTES)
        if len(page) != ref["size_bytes"] or sha256(page) != ref["sha256"]:
            raise TreeCaptureV5Error("candidate_page_hash_mismatch")
        lines = page.splitlines()
        if len(lines) != ref["count"]:
            raise TreeCaptureV5Error("candidate_member_page_count_invalid")
        declared_member_count += len(lines)
        if declared_member_count > index["entry_count"]:
            raise TreeCaptureV5Error("candidate_entry_count_mismatch")
        for line in lines:
            try:
                member = json.loads(line)
                metadata = member.get("metadata") if isinstance(member, dict) else None
                if metadata is None:
                    continue
                refs = list(metadata.get("xattrs", []))
                if metadata.get("acl") is not None:
                    refs.append(metadata["acl"])
                for sidecar in refs:
                    if not isinstance(sidecar, dict) or not isinstance(sidecar.get("path"), str):
                        raise ValueError
                    path_len = len(sidecar["path"].encode("utf-8"))
                    if path_len > MAX_RELATIVE_PATH_BYTES or not _is_sha256(sidecar.get("sha256")):
                        raise ValueError
                    if (
                        type(sidecar.get("size_bytes")) is not int
                        or not 0 <= sidecar["size_bytes"] <= MAX_METADATA_VALUE_BYTES
                    ):
                        raise ValueError
                    sidecar_count += 1
                    sidecar_path_bytes += path_len
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                TypeError,
                ValueError,
                KeyError,
            ) as exc:
                raise TreeCaptureV5Error("candidate_member_preflight_invalid") from exc
        member_page_bytes += len(page)
    if declared_member_count != index["entry_count"]:
        raise TreeCaptureV5Error("candidate_entry_count_mismatch")
    for pages, expected_prefix in (
        (payload_pages, f"{base}/payload-index/"),
        (sidecar_pages, f"{base}/metadata/"),
    ):
        for _page_no, ref in enumerate(pages):
            if (
                not isinstance(ref, dict)
                or not isinstance(ref.get("path"), str)
                or not ref["path"].startswith(expected_prefix)
                or len(ref["path"].encode("utf-8")) > MAX_RELATIVE_PATH_BYTES
                or not _is_sha256(ref.get("sha256"))
                or type(ref.get("size_bytes")) is not int
                or not 0 <= ref["size_bytes"] <= MAX_PAGE_BYTES
            ):
                raise TreeCaptureV5Error("candidate_page_index_invalid")
            if pages is payload_pages:
                if set(ref) != {"path", "sha256", "count", "size_bytes"}:
                    raise TreeCaptureV5Error("candidate_payload_page_index_invalid")
                if type(ref.get("count")) is not int or ref["count"] < 1:
                    raise TreeCaptureV5Error("candidate_payload_page_index_invalid")
                payload_index_bytes += ref["size_bytes"]
            else:
                sidecar_index_bytes += len(ref["path"].encode("utf-8")) + 128
    # SQLite stores member JSON plus indexes and several rows per sidecar/page.
    estimate = (
        index["entry_count"] * 512
        + member_page_bytes * 2
        + payload_index_bytes * 4
        + sidecar_index_bytes * 2
        + sidecar_count * 512
        + sidecar_path_bytes * 8
        + len(payload_pages) * 512
        + 8 * 1024**2
    )
    if not 1 <= estimate <= MAX_SCRATCH_BYTES:
        raise TreeCaptureV5Error("candidate_verify_scratch_reserve_exceeded")
    return raw, index, estimate


def verify_candidate_v5(
    writer: DescriptorWriter,
    candidate_id: str,
    *,
    expected_index_sha256: str | None = None,
    require_readback: bool = True,
    allow_unbound_synthetic: bool = False,
    scratch_parent_fd: int | None = None,
    scratch_db_limit_bytes: int = MAX_SCRATCH_BYTES,
    scratch_capacity_check: Callable[[int, int], None] | None = None,
    expected_plan_sha256: str | None = None,
    expected_operation_spec_sha256: str | None = None,
) -> dict[str, Any]:
    """Read a published v5 candidate through a fresh root descriptor."""
    if len(candidate_id) != 32 or any(ch not in "0123456789abcdef" for ch in candidate_id):
        raise TreeCaptureV5Error("candidate_id_invalid")
    base = f"{CANDIDATE_BASE}/{candidate_id}"
    unbound_synthetic = writer.storage_guard is None and allow_unbound_synthetic
    if writer.storage_guard is None and not unbound_synthetic:
        raise TreeCaptureV5Error("candidate_storage_binding_invalid")
    if (
        isinstance(writer.storage_guard, (StorageGuard, ReadOnlyStorageGuard))
        and scratch_parent_fd is None
    ):
        raise TreeCaptureV5Error("candidate_verify_scratch_capability_required")
    if (
        type(scratch_db_limit_bytes) is not int
        or not 1 <= scratch_db_limit_bytes <= MAX_SCRATCH_BYTES
    ):
        raise TreeCaptureV5Error("candidate_verify_scratch_limit_invalid")
    if writer.storage_guard is not None:
        try:
            writer.assert_storage_binding(writer.storage_guard)
        except Exception as exc:
            raise TreeCaptureV5Error("candidate_storage_binding_invalid") from exc
    fresh_root = os.dup(writer.root_fd)
    candidate_fd = -1
    verify_scratch = None
    verify_db: sqlite3.Connection | None = None
    try:
        if scratch_parent_fd is not None:
            if scratch_capacity_check is not None:
                scratch_capacity_check(0, scratch_db_limit_bytes)
            _prove_candidate_verify_scratch_writable(scratch_parent_fd)
        candidate_fd = _open_output_directory(fresh_root, base)
        candidate_identity = _validate_candidate_directory(writer, candidate_fd)
        index_raw, preflight_index, calculated_reserve = _verify_index_preflight_v5(
            candidate_fd,
            base,
            candidate_id,
            expected_index_sha256=expected_index_sha256,
            expected_plan_sha256=expected_plan_sha256,
            expected_operation_spec_sha256=expected_operation_spec_sha256,
        )
        if unbound_synthetic and preflight_index.get("synthetic_fixture_only") is not True:
            raise TreeCaptureV5Error("candidate_unbound_requires_synthetic_fixture")
        if calculated_reserve > scratch_db_limit_bytes:
            raise TreeCaptureV5Error("candidate_verify_scratch_limit")
        if scratch_capacity_check is not None:
            scratch_capacity_check(0, calculated_reserve)
        verify_scratch = _open_candidate_verify_db(scratch_parent_fd)
        verify_db = verify_scratch[4]
        scratch_db_limit_bytes = calculated_reserve
        index_digest = sha256(index_raw)
        index = preflight_index
        if unbound_synthetic and index.get("synthetic_fixture_only") is not True:
            raise TreeCaptureV5Error("candidate_unbound_requires_synthetic_fixture")
        legacy_fields = {
            "schema",
            "candidate_id",
            "synthetic_fixture_only",
            "plan_sha256",
            "policy_roster_sha256",
            "role_dispositions",
            "opaque_waivers",
            "member_pages",
            "payload_pages",
            "sidecar_pages",
            "entry_count",
            "payload_bytes",
            "resource_model",
            "xattr_acl_flags_restore_policy",
        }
        bound_fields = legacy_fields | {"operation_spec_sha256"}
        if set(index) not in (legacy_fields, bound_fields):
            raise TreeCaptureV5Error("candidate_index_fields_invalid")
        if type(index.get("synthetic_fixture_only")) is not bool:
            raise TreeCaptureV5Error("candidate_source_mode_invalid")
        if (
            type(index.get("entry_count")) is not int
            or not 1 <= index["entry_count"] <= MAX_ENTRIES
            or type(index.get("payload_bytes")) is not int
            or not 0 <= index["payload_bytes"] <= MAX_SOURCE_BYTES
            or index.get("xattr_acl_flags_restore_policy") != "evidence_only_never_applied"
            or not isinstance(index.get("resource_model"), dict)
        ):
            raise TreeCaptureV5Error("candidate_index_value_invalid")
        is_spec_bound = set(index) == bound_fields
        if index.get("schema") != (
            "brain-host-tree-capture-v5-candidate-bound-v1"
            if is_spec_bound
            else "brain-host-tree-capture-v5-candidate"
        ):
            raise TreeCaptureV5Error("candidate_index_schema_invalid")
        if is_spec_bound and not _is_sha256(index.get("operation_spec_sha256")):
            raise TreeCaptureV5Error("candidate_index_value_invalid")
        if (
            expected_operation_spec_sha256 is not None
            and index.get("operation_spec_sha256") != expected_operation_spec_sha256
        ):
            raise TreeCaptureV5Error("candidate_operation_spec_pin_mismatch")
        if index.get("candidate_id") != candidate_id:
            raise TreeCaptureV5Error("candidate_index_id_mismatch")
        plan_digest = index.get("plan_sha256")
        if (
            not isinstance(plan_digest, str)
            or len(plan_digest) != 64
            or any(ch not in "0123456789abcdef" for ch in plan_digest)
        ):
            raise TreeCaptureV5Error("candidate_plan_digest_invalid")
        policy_roster_digest = index.get("policy_roster_sha256")
        if not _is_sha256(policy_roster_digest):
            raise TreeCaptureV5Error("candidate_policy_roster_digest_invalid")
        role_dispositions = index.get("role_dispositions")
        if (
            not isinstance(role_dispositions, list)
            or len(role_dispositions) != len(REQUIRED_ROOT_ROLES)
            or {row.get("role") for row in role_dispositions} != REQUIRED_ROOT_ROLES
        ):
            raise TreeCaptureV5Error("candidate_root_plan_invalid")
        if not any(row.get("present") is True for row in role_dispositions):
            raise TreeCaptureV5Error("candidate_root_plan_all_absent")
        for row in role_dispositions:
            if set(row) != {
                "role",
                "present",
                "kind",
                "scope",
                "disposition",
                "locator_sha256",
                "policy_sha256",
                "root_signature",
                "root_stat",
                "unresolved_reason_sha256",
            }:
                raise TreeCaptureV5Error("candidate_root_plan_fields_invalid")
            role = row["role"]
            present = row.get("present")
            if type(present) is not bool:
                raise TreeCaptureV5Error("candidate_root_presence_invalid")
            if present:
                expected_root_stat_keys = {
                    "dev",
                    "ino",
                    "nlink",
                    "mode",
                    "uid",
                    "gid",
                    "size",
                    "mtime_ns",
                    "ctime_ns",
                    "atime_ns",
                    "birthtime_ns",
                    "flags",
                }
                if (
                    row.get("kind") != ROOT_KINDS[role]
                    or row.get("disposition") not in {"included_root", "protected_opaque"}
                    or row.get("scope") not in {"non_protected", "protected_opaque"}
                    or not _is_sha256(row.get("locator_sha256"))
                    or not _is_sha256(row.get("policy_sha256"))
                    or set(row.get("root_stat", {})) != expected_root_stat_keys
                    or any(type(value) is not int for value in row["root_stat"].values())
                    or not isinstance(row.get("root_signature"), list)
                    or len(row["root_signature"]) != 11
                    or any(type(value) is not int for value in row["root_signature"])
                    or row.get("unresolved_reason_sha256") is not None
                ):
                    raise TreeCaptureV5Error("candidate_present_root_plan_invalid")
            elif (
                row.get("kind") is not None
                or row.get("disposition") != "absent_unresolved"
                or row.get("locator_sha256") is not None
                or row.get("scope") is not None
                or row.get("policy_sha256") is not None
                or row.get("root_signature") is not None
                or row.get("root_stat") is not None
                or not _is_sha256(row.get("unresolved_reason_sha256"))
            ):
                raise TreeCaptureV5Error("candidate_absent_root_plan_invalid")
        if policy_roster_sha256_v5(plan_digest, role_dispositions) != policy_roster_digest:
            raise TreeCaptureV5Error("candidate_policy_roster_digest_mismatch")
        waivers = index.get("opaque_waivers")
        if not isinstance(waivers, list) or len(waivers) != 1 or waivers[0] != GIT_OPAQUE_WAIVER:
            raise TreeCaptureV5Error("candidate_opaque_waiver_invalid")
        checked = 0
        expected_files = 1 + int(require_readback)
        last_member_key = None
        member_pages = index.get("member_pages", [])
        payload_pages = index.get("payload_pages", [])
        sidecar_pages = index.get("sidecar_pages", [])
        if (
            not isinstance(member_pages, list)
            or not isinstance(payload_pages, list)
            or not isinstance(sidecar_pages, list)
        ):
            raise TreeCaptureV5Error("candidate_page_index_invalid")
        for page_no, ref in enumerate(member_pages):
            if set(ref) != {"path", "sha256", "count", "size_bytes"} or not _is_sha256(
                ref.get("sha256")
            ):
                raise TreeCaptureV5Error("candidate_member_page_index_invalid")
            if ref.get("path") != f"{base}/members/{page_no:08d}.jsonl":
                raise TreeCaptureV5Error("candidate_member_page_index_invalid")
            expected_files += 1
            raw = _candidate_read(candidate_fd, base, ref["path"], MAX_PAGE_BYTES)
            if len(raw) != ref["size_bytes"] or sha256(raw) != ref["sha256"]:
                raise TreeCaptureV5Error("candidate_page_hash_mismatch")
            lines = raw.splitlines()
            if len(lines) != ref["count"]:
                raise TreeCaptureV5Error("candidate_member_page_count_invalid")
            for line in lines:
                member = json.loads(line)
                checked += 1
                if set(member) != {
                    "role",
                    "path",
                    "kind",
                    "state",
                    "rule",
                    "disposition",
                    "mode",
                    "source_device",
                    "source_inode",
                    "source_nlink",
                    "uid",
                    "gid",
                    "size_bytes",
                    "mtime_ns",
                    "ctime_ns",
                    "atime_ns",
                    "birthtime_ns",
                    "flags",
                    "link_target_b64",
                    "group_id",
                    "primary_role",
                    "primary_path",
                    "payload_id",
                    "payload_sha256",
                    "payload_bytes",
                    "metadata",
                }:
                    raise TreeCaptureV5Error("candidate_member_fields_invalid")
                key = (member.get("role"), member.get("path"))
                if (
                    key[0] not in REQUIRED_ROOT_ROLES
                    or not isinstance(key[1], str)
                    or (last_member_key is not None and key <= last_member_key)
                    or member.get("kind") not in {"file", "directory", "symlink"}
                    or member.get("state") not in {"included", "excluded_file", "excluded_subtree"}
                    or any(
                        type(member.get(field)) is not int or member[field] < 0
                        for field in (
                            "mode",
                            "source_device",
                            "source_inode",
                            "source_nlink",
                            "uid",
                            "gid",
                            "size_bytes",
                            "mtime_ns",
                            "ctime_ns",
                            "atime_ns",
                            "birthtime_ns",
                            "flags",
                        )
                    )
                    or member["source_nlink"] < 1
                ):
                    raise TreeCaptureV5Error("candidate_member_key_order_invalid")
                last_member_key = key
                if member["kind"] == "symlink":
                    if not isinstance(member.get("link_target_b64"), str):
                        raise TreeCaptureV5Error("candidate_symlink_evidence_invalid")
                    try:
                        base64.b64decode(member["link_target_b64"], validate=True)
                    except (ValueError, base64.binascii.Error) as exc:
                        raise TreeCaptureV5Error("candidate_symlink_evidence_invalid") from exc
                elif member.get("link_target_b64") is not None:
                    raise TreeCaptureV5Error("candidate_nonlink_target_invalid")
                metadata = member.get("metadata")
                if metadata is not None:
                    if set(metadata) != {
                        "state",
                        "xattrs",
                        "acl",
                        "acl_state",
                        "flags_state",
                        "flags_value",
                    } or not isinstance(metadata.get("xattrs"), list):
                        raise TreeCaptureV5Error("candidate_metadata_fields_invalid")
                    if (
                        metadata.get("acl_state")
                        not in {"absent", "value_required", "unobserved_symlink_nofollow"}
                        or metadata.get("flags_state") not in {"absent", "value_required"}
                        or (
                            metadata.get("flags_value") is not None
                            and type(metadata.get("flags_value")) is not int
                        )
                    ):
                        raise TreeCaptureV5Error("candidate_metadata_value_invalid")
                    if member["kind"] == "symlink":
                        if (
                            metadata["state"] != "unobserved_symlink_nofollow"
                            or metadata["xattrs"]
                            or metadata["acl"] is not None
                            or metadata["acl_state"] != "unobserved_symlink_nofollow"
                        ):
                            raise TreeCaptureV5Error("candidate_symlink_metadata_invalid")
                    elif metadata["state"] != "observed":
                        raise TreeCaptureV5Error("candidate_metadata_state_invalid")
                    refs = list(metadata.get("xattrs", []))
                    if metadata.get("acl") is not None:
                        refs.append(metadata["acl"])
                    for sidecar in refs:
                        if set(sidecar) != {"path", "sha256", "size_bytes"} and set(sidecar) != {
                            "name",
                            "path",
                            "sha256",
                            "size_bytes",
                        }:
                            raise TreeCaptureV5Error("candidate_sidecar_reference_invalid")
                        if not _is_sha256(sidecar.get("sha256")):
                            raise TreeCaptureV5Error("candidate_sidecar_reference_invalid")
                        if not member.get("group_id") or (
                            member.get("role") == member.get("primary_role")
                            and member.get("path") == member.get("primary_path")
                        ):
                            expected_files += 1
                        value = _candidate_read(
                            candidate_fd, base, sidecar["path"], MAX_METADATA_VALUE_BYTES
                        )
                        if (
                            len(value) != sidecar["size_bytes"]
                            or sha256(value) != sidecar["sha256"]
                        ):
                            raise TreeCaptureV5Error("candidate_metadata_hash_mismatch")
                        prior_sidecar = verify_db.execute(
                            "SELECT sha256,size_bytes FROM sidecars WHERE path=?",
                            (sidecar["path"],),
                        ).fetchone()
                        if prior_sidecar is None:
                            verify_db.execute(
                                "INSERT INTO sidecars VALUES(?,?,?)",
                                (sidecar["path"], sidecar["sha256"], sidecar["size_bytes"]),
                            )
                        elif prior_sidecar != (sidecar["sha256"], sidecar["size_bytes"]):
                            raise TreeCaptureV5Error("candidate_sidecar_reference_conflict")
                elif member.get("state") == "included":
                    raise TreeCaptureV5Error("candidate_metadata_missing")
                elif metadata is not None:
                    raise TreeCaptureV5Error("candidate_excluded_metadata_invalid")
                if member.get("kind") == "file" and member.get("state") == "included":
                    if (
                        member.get("payload_bytes") == 0
                        and member.get("payload_sha256") != hashlib.sha256(b"").hexdigest()
                    ):
                        raise TreeCaptureV5Error("candidate_empty_payload_digest_mismatch")
                    if (
                        not _is_sha256(member.get("payload_id"))
                        or not _is_sha256(member.get("payload_sha256"))
                        or type(member.get("payload_bytes")) is not int
                        or member["payload_bytes"] != member.get("size_bytes")
                    ):
                        raise TreeCaptureV5Error("candidate_payload_reference_invalid")
                elif any(
                    member.get(field) is not None
                    for field in ("payload_id", "payload_sha256", "payload_bytes")
                ):
                    raise TreeCaptureV5Error("candidate_nonfile_payload_invalid")
                try:
                    verify_db.execute(
                        "INSERT INTO members VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            member["role"],
                            member["path"],
                            member["kind"],
                            member["state"],
                            member["group_id"],
                            member["primary_role"],
                            member["primary_path"],
                            member["payload_id"],
                            member["payload_sha256"],
                            member["payload_bytes"],
                            member["source_device"],
                            member["source_inode"],
                            member["source_nlink"],
                            canonical_json(metadata) if metadata is not None else None,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise TreeCaptureV5Error("candidate_member_duplicate") from exc
                if checked % 128 == 0:
                    verify_db.commit()
                    logical_bytes = os.fstat(verify_scratch[3]).st_size
                    if logical_bytes > scratch_db_limit_bytes:
                        raise TreeCaptureV5Error("candidate_verify_scratch_limit")
                    if scratch_capacity_check is not None:
                        os.fsync(verify_scratch[3])
                        used_bytes = _allocated_bytes_from_stat(os.fstat(verify_scratch[3]))
                        scratch_capacity_check(used_bytes, scratch_db_limit_bytes)
        payload_id = None
        payload_digest = hashlib.sha256()
        payload_total = 0
        payload_sum = 0
        expected_page = 0
        expected_payload_hash = None
        expected_payload_bytes = None
        for page_no, ref in enumerate(payload_pages):
            if set(ref) != {"path", "sha256", "count", "size_bytes"} or not _is_sha256(
                ref.get("sha256")
            ):
                raise TreeCaptureV5Error("candidate_payload_page_index_invalid")
            if ref.get("path") != f"{base}/payload-index/{page_no:08d}.jsonl":
                raise TreeCaptureV5Error("candidate_payload_page_index_invalid")
            expected_files += 1
            raw = _candidate_read(candidate_fd, base, ref["path"], MAX_PAGE_BYTES)
            if len(raw) != ref["size_bytes"] or sha256(raw) != ref["sha256"]:
                raise TreeCaptureV5Error("candidate_page_hash_mismatch")
            lines = raw.splitlines()
            if len(lines) != ref["count"]:
                raise TreeCaptureV5Error("candidate_payload_page_count_invalid")
            for line in lines:
                item = json.loads(line)
                if set(item) != {
                    "payload_id",
                    "page",
                    "path",
                    "sha256",
                    "size_bytes",
                    "payload_sha256",
                    "payload_bytes",
                }:
                    raise TreeCaptureV5Error("candidate_payload_item_fields_invalid")
                if item["payload_id"] != payload_id:
                    if payload_id is not None and (
                        payload_digest.hexdigest() != expected_payload_hash
                        or payload_total != expected_payload_bytes
                    ):
                        raise TreeCaptureV5Error("candidate_payload_digest_mismatch")
                    if payload_id is not None:
                        payload_sum += expected_payload_bytes
                    payload_id = item["payload_id"]
                    payload_digest = hashlib.sha256()
                    payload_total = 0
                    expected_page = 0
                    expected_payload_hash = item["payload_sha256"]
                    expected_payload_bytes = item["payload_bytes"]
                if (
                    item["payload_sha256"] != expected_payload_hash
                    or item["payload_bytes"] != expected_payload_bytes
                ):
                    raise TreeCaptureV5Error("candidate_payload_index_inconsistent")
                if item["page"] != expected_page:
                    raise TreeCaptureV5Error("candidate_payload_page_order_invalid")
                if item["path"] != f"{base}/payload/{payload_id}/{expected_page:08d}.bin":
                    raise TreeCaptureV5Error("candidate_payload_path_invalid")
                expected_files += 1
                value = _candidate_read(candidate_fd, base, item["path"], STREAM_CHUNK_BYTES)
                if len(value) != item["size_bytes"] or sha256(value) != item["sha256"]:
                    raise TreeCaptureV5Error("candidate_data_hash_mismatch")
                payload_digest.update(value)
                payload_total += len(value)
                try:
                    verify_db.execute(
                        "INSERT INTO payload_pages VALUES(?,?,?,?,?,?,?)",
                        (
                            item["payload_id"],
                            item["page"],
                            item["path"],
                            item["sha256"],
                            item["size_bytes"],
                            item["payload_sha256"],
                            item["payload_bytes"],
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise TreeCaptureV5Error("candidate_payload_page_duplicate") from exc
                expected_page += 1
                if expected_page % 128 == 0:
                    verify_db.commit()
                    logical_bytes = os.fstat(verify_scratch[3]).st_size
                    if logical_bytes > scratch_db_limit_bytes:
                        raise TreeCaptureV5Error("candidate_verify_scratch_limit")
                    if scratch_capacity_check is not None:
                        os.fsync(verify_scratch[3])
                        used_bytes = _allocated_bytes_from_stat(os.fstat(verify_scratch[3]))
                        scratch_capacity_check(used_bytes, scratch_db_limit_bytes)
        if payload_id is not None and (
            payload_digest.hexdigest() != expected_payload_hash
            or payload_total != expected_payload_bytes
        ):
            raise TreeCaptureV5Error("candidate_payload_digest_mismatch")
        if payload_id is not None:
            payload_sum += expected_payload_bytes
        sidecar_cursor = iter(
            verify_db.execute(
                "SELECT path,sha256,size_bytes FROM sidecars ORDER BY path COLLATE BINARY"
            )
        )
        last_sidecar_path = None
        for sidecar in sidecar_pages:
            if (
                not isinstance(sidecar, dict)
                or set(sidecar) != {"path", "sha256", "size_bytes"}
                or not _is_sha256(sidecar.get("sha256"))
                or not isinstance(sidecar.get("path"), str)
                or not sidecar["path"].startswith(f"{base}/metadata/")
                or type(sidecar.get("size_bytes")) is not int
                or sidecar["size_bytes"] < 0
            ):
                raise TreeCaptureV5Error("candidate_sidecar_index_invalid")
            if last_sidecar_path is not None and sidecar["path"] <= last_sidecar_path:
                raise TreeCaptureV5Error("candidate_sidecar_index_order_invalid")
            last_sidecar_path = sidecar["path"]
            expected_sidecar = next(sidecar_cursor, None)
            if expected_sidecar != (
                sidecar["path"],
                sidecar["sha256"],
                sidecar["size_bytes"],
            ):
                raise TreeCaptureV5Error("candidate_sidecar_index_closure_invalid")
        if next(sidecar_cursor, None) is not None:
            raise TreeCaptureV5Error("candidate_sidecar_index_closure_invalid")
        verify_db.commit()
        if os.fstat(verify_scratch[3]).st_size > MAX_SCRATCH_BYTES:
            raise TreeCaptureV5Error("candidate_verify_scratch_limit")
        if scratch_capacity_check is not None:
            os.fsync(verify_scratch[3])
            used_bytes = _allocated_bytes_from_stat(os.fstat(verify_scratch[3]))
            scratch_capacity_check(used_bytes, scratch_db_limit_bytes)
        _validate_candidate_relationships(verify_db)
        if payload_sum != index.get("payload_bytes"):
            raise TreeCaptureV5Error("candidate_payload_total_mismatch")
        if checked != index.get("entry_count"):
            raise TreeCaptureV5Error("candidate_member_total_mismatch")
        for disposition in role_dispositions:
            role = disposition["role"]
            root_member = verify_db.execute(
                "SELECT kind,state FROM members WHERE role=? AND path=''",
                (role,),
            ).fetchone()
            if disposition["present"]:
                if root_member != (ROOT_KINDS[role], "included"):
                    raise TreeCaptureV5Error("candidate_present_root_member_invalid")
            elif (
                root_member is not None
                or verify_db.execute(
                    "SELECT 1 FROM members WHERE role=? LIMIT 1", (role,)
                ).fetchone()
            ):
                raise TreeCaptureV5Error("candidate_absent_root_has_members")
        actual_files = _candidate_file_count(candidate_fd)
        if actual_files != expected_files:
            raise TreeCaptureV5Error(
                f"candidate_unexpected_member_set:{actual_files}:{expected_files}"
            )
        if require_readback:
            witness = json.loads(
                _candidate_read(candidate_fd, base, f"{base}/READBACK.json", MAX_PAGE_BYTES)
            )
            if (
                witness.get("schema") != "brain-host-tree-capture-v5-final-readback-witness"
                or witness.get("candidate_id") != candidate_id
                or witness.get("index_sha256") != index_digest
                or witness.get("member_count") != checked
            ):
                raise TreeCaptureV5Error("candidate_final_witness_invalid")
        _recheck_candidate_binding(
            writer,
            candidate_id,
            candidate_fd,
            candidate_identity,
            allow_unbound_synthetic=unbound_synthetic,
        )
        return {
            "schema": "brain-host-tree-capture-v5-independent-readback",
            "candidate_id": candidate_id,
            "synthetic_fixture_only": index["synthetic_fixture_only"],
            "index_sha256": index_digest,
            "member_count": checked,
            "payload_bytes": int(index["payload_bytes"]),
            "metadata_bytes": sum(int(ref["size_bytes"]) for ref in index["sidecar_pages"]),
            "member_page_bytes": sum(int(ref["size_bytes"]) for ref in index["member_pages"]),
            "entry_overhead_bytes": int(index["entry_count"]) * 4096,
            "scratch_reserve_bytes": calculated_reserve,
            "xattr_acl_flags_restore_policy": "evidence_only_never_applied",
        }
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise TreeCaptureV5Error("candidate_readback_invalid") from exc
    finally:
        if candidate_fd >= 0:
            os.close(candidate_fd)
        os.close(fresh_root)
        if verify_scratch is not None:
            _close_candidate_verify_db(*verify_scratch)


def capture_candidate_v5(inventory: GlobalInventoryV5) -> dict[str, Any]:
    """Stream globally reconciled file groups and metadata into a create-only candidate."""
    if not isinstance(inventory, GlobalInventoryV5) or inventory._closed:
        raise TreeCaptureV5Error("inventory_not_live")
    if (
        getattr(inventory.view, "synthetic", False) is not True
        and not inventory.production_authorized
    ):
        raise TreeCaptureV5Error("synthetic_fixture_only")
    inventory.assert_source_stable()
    if inventory.production_boundary_check is not None:
        inventory.production_boundary_check(inventory.remaining_capacity_reserve())
    candidate_id = secrets.token_hex(16)
    inventory.writer.assert_storage_binding(inventory.writer.storage_guard)
    parent_fd = inventory.writer.open_directory(CANDIDATE_BASE, create=True)
    candidate_fd = -1
    candidate_created = False
    candidate_identity: tuple[int, int] | None = None
    try:
        os.mkdir(candidate_id, mode=0o700, dir_fd=parent_fd)
        candidate_created = True
        created_stat = os.stat(candidate_id, dir_fd=parent_fd, follow_symlinks=False)
        candidate_identity = (created_stat.st_dev, created_stat.st_ino)
        candidate_fd = os.open(
            candidate_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
        )
        st = os.fstat(candidate_fd)
        if (st.st_dev, st.st_ino) != candidate_identity:
            raise TreeCaptureV5Error("candidate_directory_replaced_during_create")
        _validate_candidate_directory(inventory.writer, candidate_fd)
    except BaseException as exc:
        cleanup_error: BaseException | None = None
        if candidate_created:
            try:
                _cleanup_empty_created_directory(
                    parent_fd,
                    candidate_id,
                    candidate_fd if candidate_fd >= 0 else None,
                    candidate_identity,
                )
            except BaseException as cleanup_exc:
                cleanup_error = cleanup_exc
        if candidate_fd >= 0:
            os.close(candidate_fd)
        os.close(parent_fd)
        if cleanup_error is not None:
            raise TreeCaptureV5Error("candidate_create_cleanup_incomplete") from cleanup_error
        raise TreeCaptureV5Error("candidate_create_failed") from exc
    base = f"{CANDIDATE_BASE}/{candidate_id}"

    def candidate_guard() -> None:
        _recheck_candidate_binding(inventory.writer, candidate_id, candidate_fd, candidate_identity)

    inventory.candidate_write_guard = candidate_guard

    def verifier_scratch_capacity_check(used_allocated_bytes: int, reserve_bytes: int) -> None:
        verifier_cap = inventory.verifier_scratch_reserve_bytes
        if (
            verifier_cap < 1
            or type(used_allocated_bytes) is not int
            or type(reserve_bytes) is not int
            or not 0 <= used_allocated_bytes <= verifier_cap
            or not 1 <= reserve_bytes <= verifier_cap
        ):
            raise TreeCaptureV5Error("capture_verifier_scratch_capacity_invalid")
        current = inventory.remaining_capacity_reserve()
        if current < verifier_cap:
            raise TreeCaptureV5Error("capture_verifier_scratch_reserve_missing")
        required = current - verifier_cap + max(0, verifier_cap - used_allocated_bytes)
        if inventory.production_boundary_check is not None:
            inventory.production_boundary_check(required)
        else:
            inventory.reserve_capacity(required)

    capture_verifier_capacity_check = (
        verifier_scratch_capacity_check if inventory.verifier_scratch_reserve_bytes > 0 else None
    )

    def flush_planned_page(
        category: str, page_no: int, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        inventory.check_remaining_capacity()
        ref = _flush_json_page(
            inventory.writer,
            base,
            category,
            page_no,
            records,
            before_write=inventory.candidate_write_guard,
        )
        allocated = _durable_output_allocation_bytes(
            inventory.writer, ref["path"], ref["size_bytes"]
        )
        inventory.consume_json_bytes(allocated)
        return ref

    try:
        primary_query = (
            "SELECT role,path,payload_id,sig,size FROM members WHERE state='included' "
            "AND kind='file' AND payload_id IS NOT NULL "
            "AND (group_id IS NULL OR (role=primary_role AND path=primary_path)) "
            "ORDER BY payload_id COLLATE BINARY"
        )
        for role, path, payload_id, sig_raw, expected_size in inventory.db.execute(primary_query):
            required = inventory.remaining_capacity_reserve()
            if inventory.production_cadence_check is not None:
                inventory.production_cadence_check(required, 1, 0)
            elif inventory.production_boundary_check is not None:
                inventory.production_boundary_check(required)
            if v2._sig(os.fstat(inventory.roots[role])) != inventory.root_signatures[role]:
                raise TreeCaptureV5Error("source_root_changed_before_stream")
            source_fd, parent_dir_fd, name = _open_source_entry(inventory.roots[role], path, "file")
            digest = hashlib.sha256()
            total = 0
            page_no = 0
            expected_sig = bytes(sig_raw)
            try:
                if canonical_json(list(v2._sig(os.fstat(source_fd)))) != expected_sig:
                    raise TreeCaptureV5Error("source_object_changed_before_stream")
                while chunk := os.pread(source_fd, STREAM_CHUNK_BYTES, total):
                    digest.update(chunk)
                    total += len(chunk)
                    if inventory.production_cadence_check is not None:
                        inventory.production_cadence_check(
                            inventory.remaining_capacity_reserve(),
                            0,
                            len(chunk),
                        )
                    elif inventory.production_fast_check is not None:
                        inventory.production_fast_check(inventory.remaining_capacity_reserve())
                    rel = f"{base}/payload/{payload_id}/{page_no:08d}.bin"
                    allocated = _create_candidate_member(inventory, rel, chunk)
                    inventory.payload_bytes_remaining -= min(
                        allocated, inventory.payload_bytes_remaining
                    )
                    if inventory.payload_bytes_remaining < 0:
                        raise TreeCaptureV5Error("production_payload_reserve_underflow")
                    inventory.db.execute(
                        "INSERT INTO payload_pages VALUES(?,?,?,?,?)",
                        (payload_id, page_no, rel, sha256(chunk), len(chunk)),
                    )
                    if page_no % 128 == 0:
                        _check_spool_size(
                            inventory.db,
                            inventory.db_fd,
                            inventory.spool_fd,
                            inventory.scratch_reserve_bytes,
                        )
                    page_no += 1
                after = os.fstat(source_fd)
                entry_after = (
                    os.fstat(source_fd)
                    if parent_dir_fd < 0 or name is None
                    else os.stat(name, dir_fd=parent_dir_fd, follow_symlinks=False)
                )
                if (entry_after.st_dev, entry_after.st_ino) != (after.st_dev, after.st_ino):
                    raise TreeCaptureV5Error("source_entry_replaced_during_stream")
                if canonical_json(list(v2._sig(after))) != expected_sig:
                    raise TreeCaptureV5Error("source_object_changed_during_stream")
                if canonical_json(list(v2._sig(entry_after))) != expected_sig:
                    raise TreeCaptureV5Error("source_entry_replaced_during_stream")
                if total != expected_size:
                    raise TreeCaptureV5Error("source_object_size_changed")
                if inventory.production_cadence_check is not None:
                    inventory.production_cadence_check(
                        inventory.remaining_capacity_reserve(),
                        0,
                        0,
                    )
                elif inventory.production_boundary_check is not None:
                    inventory.production_boundary_check(inventory.remaining_capacity_reserve())
                payload_sha = digest.hexdigest()
                inventory.db.execute(
                    "UPDATE members SET payload_sha256=?,payload_bytes=? WHERE payload_id=?",
                    (payload_sha, total, payload_id),
                )
            finally:
                os.close(source_fd)
                if parent_dir_fd >= 0:
                    os.close(parent_dir_fd)
                if v2._sig(os.fstat(inventory.roots[role])) != inventory.root_signatures[role]:
                    raise TreeCaptureV5Error("source_root_changed_during_stream")
        inventory.db.commit()
        _check_spool_size(
            inventory.db,
            inventory.db_fd,
            inventory.spool_fd,
            inventory.scratch_reserve_bytes,
        )

        payload_refs: list[dict[str, Any]] = []
        payload_batch: list[dict[str, Any]] = []
        payload_batch_bytes = 0
        payload_page_no = 0
        for row in inventory.db.execute(
            "SELECT payload_id,page,path,sha256,size_bytes FROM payload_pages "
            "ORDER BY payload_id COLLATE BINARY,page"
        ):
            payload_id, page_no, path, page_sha, size_bytes = row
            full = inventory.db.execute(
                "SELECT payload_sha256,payload_bytes FROM members WHERE payload_id=? LIMIT 1",
                (payload_id,),
            ).fetchone()
            item = {
                "payload_id": payload_id,
                "page": page_no,
                "path": path,
                "sha256": page_sha,
                "size_bytes": size_bytes,
                "payload_sha256": full[0],
                "payload_bytes": full[1],
            }
            encoded = canonical_json(item)
            if payload_batch and (
                len(payload_batch) >= PAGE_RECORDS
                or payload_batch_bytes + len(encoded) + 1 > MAX_PAGE_BYTES
            ):
                payload_refs.append(
                    flush_planned_page("payload-index", payload_page_no, payload_batch)
                )
                payload_page_no += 1
                payload_batch = []
                payload_batch_bytes = 0
            payload_batch.append(item)
            payload_batch_bytes += len(encoded) + 1
        if payload_batch:
            payload_refs.append(flush_planned_page("payload-index", payload_page_no, payload_batch))

        metadata_query = (
            "SELECT role,path,kind,sig,payload_id,group_id,primary_role,primary_path,metadata_json "
            "FROM members WHERE state='included' AND kind IN ('directory','symlink') "
            "OR state='included' AND kind='file' AND payload_id IS NOT NULL "
            "AND (group_id IS NULL OR (role=primary_role AND path=primary_path)) "
            "ORDER BY role COLLATE BINARY,path COLLATE BINARY"
        )
        metadata_total_bytes = 0
        for ordinal, (
            role,
            path,
            kind,
            sig_raw,
            payload_id,
            _group_id,
            _primary_role,
            _primary_path,
            expected_metadata_raw,
        ) in enumerate(inventory.db.execute(metadata_query)):
            required = inventory.remaining_capacity_reserve()
            if inventory.production_cadence_check is not None:
                inventory.production_cadence_check(required, 1, 0)
            elif inventory.production_boundary_check is not None:
                inventory.production_boundary_check(required)
            sidecar_records: list[dict[str, Any]] = []
            metadata = _capture_metadata(
                inventory,
                base,
                role,
                path,
                kind,
                ordinal,
                bytes(sig_raw),
                bytes(expected_metadata_raw) if expected_metadata_raw is not None else None,
                sidecar_records,
                metadata_total_bytes,
            )
            if inventory.production_cadence_check is not None:
                inventory.production_cadence_check(
                    inventory.remaining_capacity_reserve(),
                    0,
                    0,
                )
            elif inventory.production_boundary_check is not None:
                inventory.production_boundary_check(inventory.remaining_capacity_reserve())
            metadata_total_bytes += sum(ref["size_bytes"] for ref in sidecar_records)
            metadata_raw = canonical_json(metadata)
            inventory.db.execute(
                "UPDATE members SET metadata_json=?,metadata_state='observed' "
                "WHERE role=? AND path=?",
                (metadata_raw, role, path),
            )
            if payload_id is not None:
                inventory.db.execute(
                    "UPDATE members SET metadata_json=?,metadata_state='observed' "
                    "WHERE payload_id=?",
                    (metadata_raw, payload_id),
                )
        inventory.db.commit()
        _check_spool_size(
            inventory.db,
            inventory.db_fd,
            inventory.spool_fd,
            inventory.scratch_reserve_bytes,
        )

        member_batch: list[dict[str, Any]] = []
        sidecar_manifest: dict[str, dict[str, Any]] = {}
        member_page_no = 0
        member_bytes = 0
        member_refs: list[dict[str, Any]] = []
        query = (
            "SELECT role,path,kind,state,rule,disposition,device,inode,nlink,mode,uid,gid,"
            "size,mtime_ns,ctime_ns,atime_ns,birthtime_ns,"
            "flags,link_text,group_id,primary_role,primary_path,payload_id,payload_sha256,"
            "payload_bytes,metadata_json FROM members "
            "ORDER BY role COLLATE BINARY,path COLLATE BINARY"
        )
        for row in inventory.db.execute(query):
            (
                role,
                path,
                kind,
                state,
                rule,
                disposition,
                source_device,
                source_inode,
                source_nlink,
                mode,
                uid,
                gid,
                size,
                mtime_ns,
                ctime_ns,
                atime_ns,
                birthtime_ns,
                flags,
                link_text,
                group_id,
                primary_role,
                primary_path,
                payload_id,
                payload_sha,
                payload_bytes,
                metadata_raw,
            ) = row
            record = _candidate_member_record_v5(row)
            if record["metadata"] is not None:
                metadata_values = list(record["metadata"].get("xattrs", []))
                if record["metadata"].get("acl") is not None:
                    metadata_values.append(record["metadata"]["acl"])
                for sidecar in metadata_values:
                    ref = {
                        "path": sidecar["path"],
                        "sha256": sidecar["sha256"],
                        "size_bytes": sidecar["size_bytes"],
                    }
                    prior = sidecar_manifest.setdefault(ref["path"], ref)
                    if prior != ref:
                        raise TreeCaptureV5Error("candidate_sidecar_reference_conflict")
            encoded = canonical_json(record)
            if len(encoded) + 1 > MAX_PAGE_BYTES:
                raise TreeCaptureV5Error("candidate_member_record_limit")
            if member_batch and (
                len(member_batch) >= PAGE_RECORDS
                or member_bytes + len(encoded) + 1 > MAX_PAGE_BYTES
            ):
                ref = flush_planned_page("members", member_page_no, member_batch)
                member_refs.append(ref)
                member_page_no += 1
                member_batch = []
                member_bytes = 0
            member_batch.append(record)
            member_bytes += len(encoded) + 1
        if member_batch:
            member_refs.append(flush_planned_page("members", member_page_no, member_batch))

        plan_receipt = inventory.public_inventory_receipt()
        index = {
            "schema": (
                "brain-host-tree-capture-v5-candidate-bound-v1"
                if inventory.operation_spec_sha256 is not None
                else "brain-host-tree-capture-v5-candidate"
            ),
            "candidate_id": candidate_id,
            "synthetic_fixture_only": bool(inventory.view.synthetic),
            "plan_sha256": inventory.plan_sha256,
            "policy_roster_sha256": inventory.policy_roster_sha256,
            "role_dispositions": plan_receipt["role_dispositions"],
            "opaque_waivers": plan_receipt["opaque_waivers"],
            "member_pages": member_refs,
            "payload_pages": payload_refs,
            "sidecar_pages": [sidecar_manifest[path] for path in sorted(sidecar_manifest)],
            "entry_count": inventory.entry_count,
            "payload_bytes": inventory.included_source_bytes,
            "resource_model": plan_receipt["resource_model"],
            "xattr_acl_flags_restore_policy": "evidence_only_never_applied",
        }
        if inventory.operation_spec_sha256 is not None:
            index["operation_spec_sha256"] = inventory.operation_spec_sha256
        index_raw = canonical_json(index)
        _create_candidate_member(inventory, f"{base}/INDEX.json", index_raw)
        verification = verify_candidate_v5(
            inventory.writer,
            candidate_id,
            require_readback=False,
            scratch_parent_fd=inventory.verify_scratch_fd,
            scratch_capacity_check=capture_verifier_capacity_check,
            expected_plan_sha256=inventory.plan_sha256,
        )
        witness = {
            "schema": "brain-host-tree-capture-v5-final-readback-witness",
            "candidate_id": candidate_id,
            "index_sha256": verification["index_sha256"],
            "member_count": verification["member_count"],
            "payload_bytes": inventory.included_source_bytes,
        }
        witness_raw = canonical_json(witness)
        _create_candidate_member(inventory, f"{base}/READBACK.json", witness_raw)
        witness_fd = _open_output_directory(inventory.writer.root_fd, base)
        try:
            fresh_witness = _candidate_read(
                witness_fd, base, f"{base}/READBACK.json", MAX_PAGE_BYTES
            )
        finally:
            os.close(witness_fd)
        if fresh_witness != witness_raw:
            raise TreeCaptureV5Error("candidate_final_witness_changed")
        verification = verify_candidate_v5(
            inventory.writer,
            candidate_id,
            scratch_parent_fd=inventory.verify_scratch_fd,
            scratch_capacity_check=capture_verifier_capacity_check,
            expected_plan_sha256=inventory.plan_sha256,
        )
        if inventory.production_root_check is not None:
            inventory.production_root_check()
        inventory.assert_source_stable()
        return {
            "schema": "brain-host-tree-capture-v5-candidate-receipt",
            "candidate_id": candidate_id,
            "synthetic_fixture_only": bool(inventory.view.synthetic),
            "index_sha256": sha256(index_raw),
            "operation_spec_sha256": inventory.operation_spec_sha256,
            "entry_count": inventory.entry_count,
            "payload_bytes": inventory.included_source_bytes,
            "source_inventory_sha256": inventory.source_inventory_sha256,
            "readback_verified": True,
            "xattr_acl_flags_restore_policy": "evidence_only_never_applied",
            "T_qualified": False,
            "capture_complete": False,
            "kingston_durable": False,
            "restore_verified": False,
            "erase_ready": False,
        }
    except BaseException:
        cleanup_error: BaseException | None = None
        try:
            _cleanup_empty_created_directory(
                parent_fd, candidate_id, candidate_fd, candidate_identity
            )
        except BaseException as exc:
            cleanup_error = exc
        if cleanup_error is not None:
            raise TreeCaptureV5Error("candidate_cleanup_incomplete") from cleanup_error
        raise
    finally:
        inventory.candidate_write_guard = None
        if candidate_fd >= 0:
            os.close(candidate_fd)
        os.close(parent_fd)


def _ensure_destination_directory(
    parent_fd: int,
    name: str,
    relative: str,
    created_directories: dict[str, tuple[int, int]],
) -> int:
    parent_stat = os.fstat(parent_fd)
    created_here = False
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError as exc:
        expected_identity = created_directories.get(relative)
        if expected_identity is None:
            raise TreeCaptureV5Error("inert_destination_directory_collision") from exc
    else:
        created_here = True
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        st = os.fstat(fd)
        path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            st.st_uid != os.getuid()
            or stat.S_IMODE(st.st_mode) != 0o700
            or st.st_dev != parent_stat.st_dev
            or (path_stat.st_dev, path_stat.st_ino) != (st.st_dev, st.st_ino)
        ):
            raise TreeCaptureV5Error("inert_destination_directory_invalid")
        identity = (st.st_dev, st.st_ino)
        if created_here:
            created_directories[relative] = identity
        elif identity != expected_identity:
            raise TreeCaptureV5Error("inert_destination_directory_replaced")
        if sys.platform == "darwin" and _acl_bytes(fd) is not None:
            raise TreeCaptureV5Error("inert_destination_directory_acl_present")
    except OSError as exc:
        os.close(fd)
        raise TreeCaptureV5Error("inert_destination_directory_replaced") from exc
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_destination_parent(
    root_fd: int,
    relative: str,
    created_directories: dict[str, tuple[int, int]],
) -> tuple[int, str]:
    parts = relative.split("/")
    if not parts or any(part in {"", ".", ".."} or "/" in part for part in parts):
        raise TreeCaptureV5Error("inert_destination_path_invalid")
    current = os.dup(root_fd)
    prefix = ""
    try:
        for component in parts[:-1]:
            prefix = f"{prefix}/{component}" if prefix else component
            child = _ensure_destination_directory(current, component, prefix, created_directories)
            os.close(current)
            current = child
        return current, parts[-1]
    except BaseException:
        os.close(current)
        raise


def _check_extraction_scratch(
    db: sqlite3.Connection,
    database_fd: int,
    scratch_directory_fd: int,
    reserve_bytes: int,
    capacity_check: Callable[[int, int], None] | None,
) -> None:
    db.commit()
    used = os.fstat(database_fd).st_size
    database_stat = os.fstat(database_fd)
    directory_stat = os.fstat(scratch_directory_fd)
    try:
        path_stat = os.stat("verify.sqlite", dir_fd=scratch_directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise TreeCaptureV5Error("inert_extraction_scratch_database_replaced") from exc
    if used > reserve_bytes or reserve_bytes > MAX_SCRATCH_BYTES:
        raise TreeCaptureV5Error("inert_extraction_scratch_limit")
    if (
        not stat.S_ISDIR(directory_stat.st_mode)
        or directory_stat.st_uid != os.getuid()
        or stat.S_IMODE(directory_stat.st_mode) != 0o700
        or database_stat.st_dev != directory_stat.st_dev
        or database_stat.st_uid != os.getuid()
        or stat.S_IMODE(database_stat.st_mode) != 0o600
        or database_stat.st_nlink != 1
        or (path_stat.st_dev, path_stat.st_ino) != (database_stat.st_dev, database_stat.st_ino)
        or os.listdir(scratch_directory_fd) != ["verify.sqlite"]
        or (sys.platform == "darwin" and _acl_bytes(scratch_directory_fd) is not None)
    ):
        raise TreeCaptureV5Error("inert_extraction_scratch_identity_invalid")
    if capacity_check is not None:
        capacity_check(used, reserve_bytes)


def _insert_quarantine_readback(
    extraction_fd: int,
    db: sqlite3.Connection,
    *,
    destination_device: int,
    candidate_fd: int,
    base: str,
    member_pages: list[dict[str, Any]],
    database_fd: int,
    scratch_directory_fd: int,
    scratch_reserve_bytes: int,
    scratch_capacity_check: Callable[[int, int], None] | None,
) -> dict[str, int]:
    """Reopen and compare exact inert member set, bytes, and hardlink topology."""
    db.execute(
        "CREATE TABLE quarantine_expected(path TEXT PRIMARY KEY,kind TEXT,group_id TEXT,"
        "payload_sha256 TEXT,payload_bytes INTEGER)"
    )
    db.execute(
        "CREATE TABLE quarantine_actual(path TEXT PRIMARY KEY,kind TEXT,dev INTEGER,ino INTEGER,"
        "nlink INTEGER,payload_sha256 TEXT,payload_bytes INTEGER)"
    )
    db.execute("CREATE INDEX quarantine_expected_group ON quarantine_expected(group_id)")
    members_seen = files_seen = directories_seen = omitted_links = 0
    actual_seen = 0
    for page_ref in member_pages:
        raw = _candidate_read(candidate_fd, base, page_ref["path"], MAX_PAGE_BYTES)
        if sha256(raw) != page_ref["sha256"]:
            raise TreeCaptureV5Error("inert_member_page_hash_mismatch")
        for line in raw.splitlines():
            member = json.loads(line)
            if member["state"] != "included":
                continue
            members_seen += 1
            if member["kind"] == "symlink":
                omitted_links += 1
                continue
            rel = f"{member['role']}/{member['path']}" if member["path"] else member["role"]
            if member["kind"] not in {"directory", "file"}:
                raise TreeCaptureV5Error("inert_member_kind_invalid")
            db.execute(
                "INSERT INTO quarantine_expected VALUES(?,?,?,?,?)",
                (
                    rel,
                    member["kind"],
                    member.get("group_id"),
                    member.get("payload_sha256"),
                    member.get("payload_bytes"),
                ),
            )
            if member["kind"] == "file":
                files_seen += 1
            else:
                directories_seen += 1
            if members_seen % 128 == 0:
                _check_extraction_scratch(
                    db,
                    database_fd,
                    scratch_directory_fd,
                    scratch_reserve_bytes,
                    scratch_capacity_check,
                )

    def visit(directory_fd: int, prefix: str) -> None:
        nonlocal actual_seen
        # scandir streams directory entries; sorting a large sibling set would
        # make readback memory proportional to an untrusted directory fanout.
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                name = entry.name
                st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                relative = f"{prefix}/{name}" if prefix else name
                if st.st_dev != destination_device or st.st_uid != os.getuid():
                    raise TreeCaptureV5Error("inert_readback_member_identity_invalid")
                if stat.S_ISDIR(st.st_mode):
                    if stat.S_IMODE(st.st_mode) != 0o700:
                        raise TreeCaptureV5Error("inert_readback_directory_mode_invalid")
                    child_fd = os.open(
                        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd
                    )
                    try:
                        child_st = os.fstat(child_fd)
                        path_st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if (child_st.st_dev, child_st.st_ino) != (path_st.st_dev, path_st.st_ino):
                            raise TreeCaptureV5Error("inert_readback_directory_replaced")
                        if sys.platform == "darwin" and _acl_bytes(child_fd) is not None:
                            raise TreeCaptureV5Error("inert_readback_directory_acl_present")
                        db.execute(
                            "INSERT INTO quarantine_actual VALUES(?,?,?,?,?,?,?)",
                            (relative, "directory", st.st_dev, st.st_ino, st.st_nlink, None, None),
                        )
                        actual_seen += 1
                        if actual_seen % 128 == 0:
                            _check_extraction_scratch(
                                db,
                                database_fd,
                                scratch_directory_fd,
                                scratch_reserve_bytes,
                                scratch_capacity_check,
                            )
                        visit(child_fd, relative)
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(st.st_mode):
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
                    try:
                        opened = os.fstat(fd)
                        path_st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if (
                            (opened.st_dev, opened.st_ino) != (path_st.st_dev, path_st.st_ino)
                            or opened.st_size != st.st_size
                            or stat.S_IMODE(opened.st_mode) != 0o600
                            or opened.st_nlink < 1
                        ):
                            raise TreeCaptureV5Error("inert_readback_file_identity_invalid")
                        if sys.platform == "darwin" and _acl_bytes(fd) is not None:
                            raise TreeCaptureV5Error("inert_readback_file_acl_present")
                        digest = hashlib.sha256()
                        total = 0
                        while chunk := os.read(fd, STREAM_CHUNK_BYTES):
                            digest.update(chunk)
                            total += len(chunk)
                        if total != opened.st_size:
                            raise TreeCaptureV5Error("inert_readback_file_size_changed")
                        db.execute(
                            "INSERT INTO quarantine_actual VALUES(?,?,?,?,?,?,?)",
                            (
                                relative,
                                "file",
                                opened.st_dev,
                                opened.st_ino,
                                opened.st_nlink,
                                digest.hexdigest(),
                                total,
                            ),
                        )
                        actual_seen += 1
                        if actual_seen % 128 == 0:
                            _check_extraction_scratch(
                                db,
                                database_fd,
                                scratch_directory_fd,
                                scratch_reserve_bytes,
                                scratch_capacity_check,
                            )
                    finally:
                        os.close(fd)
                else:
                    raise TreeCaptureV5Error("inert_readback_unexpected_link_or_type")

    visit(extraction_fd, "")
    missing = db.execute(
        "SELECT e.path FROM quarantine_expected e LEFT JOIN quarantine_actual a "
        "ON a.path=e.path WHERE a.path IS NULL LIMIT 1"
    ).fetchone()
    unexpected = db.execute(
        "SELECT a.path FROM quarantine_actual a LEFT JOIN quarantine_expected e "
        "ON e.path=a.path WHERE e.path IS NULL LIMIT 1"
    ).fetchone()
    mismatch = db.execute(
        "SELECT 1 FROM quarantine_expected e JOIN quarantine_actual a ON a.path=e.path "
        "WHERE e.kind!=a.kind OR (e.kind='file' AND "
        "(e.payload_sha256!=a.payload_sha256 OR e.payload_bytes!=a.payload_bytes)) LIMIT 1"
    ).fetchone()
    if missing is not None or unexpected is not None or mismatch is not None:
        if missing is not None:
            raise TreeCaptureV5Error("inert_readback_member_missing")
        if unexpected is not None:
            raise TreeCaptureV5Error("inert_readback_member_unexpected")
        raise TreeCaptureV5Error("inert_readback_content_mismatch")
    bad_hardlink = db.execute(
        "SELECT 1 FROM quarantine_expected e "
        "JOIN quarantine_actual a ON a.path=e.path "
        "WHERE e.kind='file' AND e.group_id IS NOT NULL GROUP BY e.group_id "
        "HAVING count(DISTINCT a.dev || ':' || a.ino)!=1 OR min(a.nlink)!=count(*) "
        "OR max(a.nlink)!=count(*) LIMIT 1"
    ).fetchone()
    bad_singleton = db.execute(
        "SELECT 1 FROM quarantine_expected e JOIN quarantine_actual a ON a.path=e.path "
        "WHERE e.kind='file' AND e.group_id IS NULL AND a.nlink!=1 LIMIT 1"
    ).fetchone()
    if bad_hardlink is not None or bad_singleton is not None:
        raise TreeCaptureV5Error("inert_readback_hardlink_topology_mismatch")
    return {
        "member_count": members_seen,
        "file_count": files_seen,
        "directory_count": directories_seen,
        "symlink_omitted_count": omitted_links,
    }


def extract_candidate_inert_v5(
    writer: DescriptorWriter,
    candidate_id: str,
    *,
    quarantine_parent_fd: int,
    allow_unbound_synthetic: bool = False,
    scratch_parent_fd: int | None = None,
    expected_index_sha256: str | None = None,
    expected_plan_sha256: str | None = None,
    capacity_check: Callable[[int], None] | None = None,
    scratch_capacity_check: Callable[[int, int], None] | None = None,
    leave_partial_on_error: bool = False,
) -> dict[str, Any]:
    """Extract verified bytes into an inert quarantine rehearsal only.

    This is not a host restore. The caller supplies pinned private scratch and
    quarantine directory FDs on devices distinct from candidate storage. The
    scratch SQLite database is only a bounded readback index; it never opens
    any extracted application data as a database.
    """
    if scratch_parent_fd is None or expected_index_sha256 is None or expected_plan_sha256 is None:
        raise TreeCaptureV5Error("inert_extraction_external_pins_required")
    verification = verify_candidate_v5(
        writer,
        candidate_id,
        allow_unbound_synthetic=allow_unbound_synthetic,
        scratch_parent_fd=scratch_parent_fd,
        expected_index_sha256=expected_index_sha256,
        expected_plan_sha256=expected_plan_sha256,
        scratch_capacity_check=scratch_capacity_check,
    )
    candidate_device = os.fstat(writer.root_fd).st_dev
    parent_stat = os.fstat(quarantine_parent_fd)
    quarantine_acl_error = None
    if sys.platform == "darwin":
        try:
            if _acl_bytes(quarantine_parent_fd) is not None:
                quarantine_acl_error = True
        except Exception:
            quarantine_acl_error = True
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.getuid()
        or stat.S_IMODE(parent_stat.st_mode) != 0o700
        or (parent_stat.st_dev == candidate_device and not allow_unbound_synthetic)
        or quarantine_acl_error
    ):
        raise TreeCaptureV5Error("inert_quarantine_parent_invalid")
    scratch_stat = os.fstat(scratch_parent_fd)
    if (
        not stat.S_ISDIR(scratch_stat.st_mode)
        or scratch_stat.st_uid != os.getuid()
        or stat.S_IMODE(scratch_stat.st_mode) != 0o700
        or (scratch_stat.st_dev == parent_stat.st_dev and not allow_unbound_synthetic)
        or (not allow_unbound_synthetic and scratch_stat.st_dev == candidate_device)
    ):
        raise TreeCaptureV5Error("inert_extraction_scratch_invalid")
    payload_remaining = int(verification.get("payload_bytes", 0))
    metadata_bytes = int(verification.get("metadata_bytes", 0))
    member_count = int(verification["member_count"])
    entry_overhead = member_count * 4096
    capacity_floor = 256 * 1024**2
    readback_scratch_reserve = max(
        16 * 1024**2,
        member_count * 8192 + int(verification.get("member_page_bytes", 0)),
    )
    if readback_scratch_reserve > MAX_SCRATCH_BYTES:
        raise TreeCaptureV5Error("inert_extraction_scratch_limit")
    if capacity_check is not None:
        capacity_check(capacity_floor + payload_remaining + metadata_bytes + entry_overhead)
    if scratch_capacity_check is not None:
        scratch_capacity_check(0, readback_scratch_reserve)
    base = f"{CANDIDATE_BASE}/{candidate_id}"
    source_root = os.dup(writer.root_fd)
    candidate_fd = -1
    destination_root = os.dup(quarantine_parent_fd)
    if (os.fstat(source_root).st_dev, os.fstat(source_root).st_ino) != (
        os.fstat(writer.root_fd).st_dev,
        os.fstat(writer.root_fd).st_ino,
    ):
        os.close(source_root)
        os.close(destination_root)
        raise TreeCaptureV5Error("candidate_root_replaced")
    if (os.fstat(destination_root).st_dev, os.fstat(destination_root).st_ino) != (
        parent_stat.st_dev,
        parent_stat.st_ino,
    ):
        os.close(source_root)
        os.close(destination_root)
        raise TreeCaptureV5Error("inert_quarantine_root_replaced")
    extraction_id = secrets.token_hex(16)
    extraction_fd = -1
    extraction_created = False
    extraction_identity: tuple[int, int] | None = None
    readback_scratch = None
    readback_result: dict[str, int] | None = None
    try:
        candidate_fd = _open_output_directory(source_root, base)
        candidate_identity = _validate_candidate_directory(writer, candidate_fd)
        os.mkdir(extraction_id, mode=0o700, dir_fd=destination_root)
        extraction_created = True
        created_stat = os.stat(extraction_id, dir_fd=destination_root, follow_symlinks=False)
        extraction_identity = (created_stat.st_dev, created_stat.st_ino)
        extraction_fd = os.open(
            extraction_id,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=destination_root,
        )
        extraction_stat = os.fstat(extraction_fd)
        if extraction_stat.st_dev != parent_stat.st_dev or extraction_stat.st_uid != os.getuid():
            raise TreeCaptureV5Error("inert_quarantine_device_changed")
        if (extraction_stat.st_dev, extraction_stat.st_ino) != extraction_identity:
            raise TreeCaptureV5Error("inert_extraction_replaced_during_create")
        index_raw = _candidate_read(candidate_fd, base, f"{base}/INDEX.json", MAX_PAGE_BYTES)
        if sha256(index_raw) != verification["index_sha256"]:
            raise TreeCaptureV5Error("candidate_index_changed_after_verify")
        index = json.loads(index_raw)
        member_pages = index["member_pages"]
        readback_scratch = _open_candidate_verify_db(scratch_parent_fd)
        _check_extraction_scratch(
            readback_scratch[4],
            readback_scratch[3],
            readback_scratch[2],
            readback_scratch_reserve,
            scratch_capacity_check,
        )
        extracted_entries = 0
        links_materialized = 0
        links_evidence_only = 0
        entries_considered = 0
        created_directories: dict[str, tuple[int, int]] = {}
        for page_ref in member_pages:
            raw = _candidate_read(candidate_fd, base, page_ref["path"], MAX_PAGE_BYTES)
            if sha256(raw) != page_ref["sha256"]:
                raise TreeCaptureV5Error("inert_member_page_hash_mismatch")
            for line in raw.splitlines():
                member = json.loads(line)
                entries_considered += 1
                if member["state"] != "included":
                    continue
                role = member["role"]
                path = member["path"]
                kind = member["kind"]
                rel = f"{role}/{path}" if path else role
                if kind == "directory":
                    directory_fd, name = _open_destination_parent(
                        extraction_fd, rel, created_directories
                    )
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=directory_fd)
                        created = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        created_directories[rel] = (created.st_dev, created.st_ino)
                    except FileExistsError as exc:
                        raise TreeCaptureV5Error("inert_destination_directory_collision") from exc
                    finally:
                        os.close(directory_fd)
                elif kind == "symlink":
                    # Link text is retained in the candidate as evidence. The
                    # inert quarantine does not recreate links because lexical
                    # containment cannot prove safety through symlink chains.
                    base64.b64decode(member["link_target_b64"], validate=True)
                    links_evidence_only += 1
                elif kind == "file":
                    if member.get("group_id") and not (
                        member.get("role") == member.get("primary_role")
                        and member.get("path") == member.get("primary_path")
                    ):
                        continue
                    directory_fd, name = _open_destination_parent(
                        extraction_fd, rel, created_directories
                    )
                    fd = -1
                    digest = hashlib.sha256()
                    total = 0
                    try:
                        fd = os.open(
                            name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=directory_fd,
                        )
                        remaining = member["payload_bytes"]
                        page_no = 0
                        while remaining:
                            if capacity_check is not None:
                                capacity_check(
                                    capacity_floor
                                    + payload_remaining
                                    + metadata_bytes
                                    + max(0, member_count - entries_considered) * 4096
                                )
                            rel_data = f"{base}/payload/{member['payload_id']}/{page_no:08d}.bin"
                            chunk = _candidate_read(
                                candidate_fd, base, rel_data, STREAM_CHUNK_BYTES
                            )
                            if not chunk or len(chunk) > remaining:
                                raise TreeCaptureV5Error("inert_payload_page_invalid")
                            view = memoryview(chunk)
                            while view:
                                written = os.write(fd, view)
                                if written <= 0:
                                    raise TreeCaptureV5Error("inert_destination_write_failed")
                                view = view[written:]
                            digest.update(chunk)
                            total += len(chunk)
                            remaining -= len(chunk)
                            payload_remaining -= len(chunk)
                            if payload_remaining < 0:
                                raise TreeCaptureV5Error("inert_payload_reserve_underflow")
                            page_no += 1
                        if (
                            total != member["payload_bytes"]
                            or digest.hexdigest() != member["payload_sha256"]
                        ):
                            raise TreeCaptureV5Error("inert_payload_digest_mismatch")
                        os.fsync(fd)
                    finally:
                        if fd >= 0:
                            os.close(fd)
                        os.close(directory_fd)
                else:
                    raise TreeCaptureV5Error("inert_member_kind_invalid")
                extracted_entries += 1
                if capacity_check is not None:
                    capacity_check(
                        capacity_floor
                        + payload_remaining
                        + metadata_bytes
                        + max(0, member_count - entries_considered) * 4096
                    )
        # Recreate cross-root hardlink aliases inside this single quarantine tree.
        for page_ref in member_pages:
            raw = _candidate_read(candidate_fd, base, page_ref["path"], MAX_PAGE_BYTES)
            for line in raw.splitlines():
                member = json.loads(line)
                if (
                    member.get("state") != "included"
                    or member.get("kind") != "file"
                    or not member.get("group_id")
                    or (
                        member.get("role") == member.get("primary_role")
                        and member.get("path") == member.get("primary_path")
                    )
                ):
                    continue
                source_rel = f"{member['primary_role']}/{member['primary_path']}"
                target_rel = f"{member['role']}/{member['path']}"
                source_parent, source_name = _open_destination_parent(
                    extraction_fd, source_rel, created_directories
                )
                target_parent, target_name = _open_destination_parent(
                    extraction_fd, target_rel, created_directories
                )
                try:
                    os.link(
                        source_name,
                        target_name,
                        src_dir_fd=source_parent,
                        dst_dir_fd=target_parent,
                        follow_symlinks=False,
                    )
                finally:
                    os.close(source_parent)
                    os.close(target_parent)
                extracted_entries += 1
                links_materialized += 1
        os.fsync(extraction_fd)
        # Reopen by the nonce name and require it to bind to the held child
        # before publishing a successful rehearsal receipt.
        reopened = _open_output_directory(destination_root, extraction_id)
        try:
            held = os.fstat(extraction_fd)
            reopened_st = os.fstat(reopened)
            path_st = os.stat(extraction_id, dir_fd=destination_root, follow_symlinks=False)
            if (
                (held.st_dev, held.st_ino) != extraction_identity
                or (reopened_st.st_dev, reopened_st.st_ino) != extraction_identity
                or (path_st.st_dev, path_st.st_ino) != extraction_identity
            ):
                raise TreeCaptureV5Error("inert_extraction_replaced_during_readback")
            readback_result = _insert_quarantine_readback(
                reopened,
                readback_scratch[4],
                destination_device=parent_stat.st_dev,
                candidate_fd=candidate_fd,
                base=base,
                member_pages=member_pages,
                database_fd=readback_scratch[3],
                scratch_directory_fd=readback_scratch[2],
                scratch_reserve_bytes=readback_scratch_reserve,
                scratch_capacity_check=scratch_capacity_check,
            )
            if scratch_capacity_check is not None:
                scratch_capacity_check(
                    os.fstat(readback_scratch[3]).st_size,
                    readback_scratch_reserve,
                )
        finally:
            os.close(reopened)
        _recheck_candidate_binding(
            writer,
            candidate_id,
            candidate_fd,
            candidate_identity,
            allow_unbound_synthetic=allow_unbound_synthetic,
        )
        assert readback_scratch is not None
        scratch_to_close = readback_scratch
        readback_scratch = None
        _close_candidate_verify_db(*scratch_to_close)
        held_after_readback = os.fstat(extraction_fd)
        path_after_readback = os.stat(extraction_id, dir_fd=destination_root, follow_symlinks=False)
        if (held_after_readback.st_dev, held_after_readback.st_ino) != extraction_identity or (
            path_after_readback.st_dev,
            path_after_readback.st_ino,
        ) != extraction_identity:
            raise TreeCaptureV5Error("inert_extraction_replaced_after_readback")
        os.fsync(destination_root)
        os.close(extraction_fd)
        extraction_fd = -1
        return {
            "schema": "brain-host-tree-capture-v5-inert-extraction",
            "candidate_id": candidate_id,
            "extraction_id": extraction_id,
            "entry_count": extracted_entries,
            "members_read": entries_considered,
            "links_materialized": links_materialized,
            "hardlinks_materialized": links_materialized,
            "links_evidence_only": links_evidence_only,
            "links_omitted": links_evidence_only,
            "unconfined_links_omitted": links_evidence_only,
            "symlink_policy": "all_links_omitted_chain_confinement_unverified",
            "hardlink_topology_preserved_in_quarantine": True,
            "inert_data_extraction": True,
            "quarantine_only": True,
            "candidate_source_device": candidate_device,
            "quarantine_device": parent_stat.st_dev,
            "xattr_acl_flags_applied": False,
            "restore_verified": False,
            "T_qualified": False,
            "erase_ready": False,
            "candidate_readback_sha256": verification["index_sha256"],
            "quarantine_rehearsal_only": True,
            "start_or_restore_performed": False,
            "readback_verified": True,
            "readback_member_count": readback_result["member_count"] if readback_result else 0,
            "readback_file_count": readback_result["file_count"] if readback_result else 0,
            "readback_directory_count": (
                readback_result["directory_count"] if readback_result else 0
            ),
            "readback_omitted_symlink_count": (
                readback_result["symlink_omitted_count"] if readback_result else 0
            ),
            "scratch_sqlite_role": "private_readback_index_only",
        }
    except BaseException:
        cleanup_error: BaseException | None = None
        if extraction_created and not leave_partial_on_error:
            try:
                _cleanup_empty_created_directory(
                    destination_root,
                    extraction_id,
                    extraction_fd if extraction_fd >= 0 else None,
                    extraction_identity,
                )
            except BaseException as exc:
                cleanup_error = exc
        if extraction_fd >= 0:
            os.close(extraction_fd)
            extraction_fd = -1
        if readback_scratch is not None:
            scratch_to_close = readback_scratch
            readback_scratch = None
            try:
                _close_candidate_verify_db(*scratch_to_close)
            except BaseException as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise TreeCaptureV5Error("inert_extraction_cleanup_incomplete") from cleanup_error
        raise
    finally:
        if candidate_fd >= 0:
            os.close(candidate_fd)
        os.close(source_root)
        os.close(destination_root)


def main(argv: list[str] | None = None) -> int:
    """Delegate to the separately pinned, read-only production verifier CLI."""
    from tools.brain_host_tree_capture_v5_production import main as production_main

    return production_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
