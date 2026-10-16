#!/usr/bin/env python3
"""Global held-view inventory and candidate capture for tree capture v5.

The engine borrows every present root from a pinned HeldDataT view, inventories
all roots without following links into descriptor-bound SQLite, reconciles
hardlinks globally, then streams one payload per complete included inode group.
It publishes create-only evidence candidates and supports independent readback
and inert data extraction. Existing v1/v3/v4 behavior remains unchanged.
"""

from __future__ import annotations

import argparse
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
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_repo_root))
    sys.path.insert(0, str(_repo_root / "src"))

from tools import brain_host_tree_custody as v2
from tools import brain_host_tree_custody_v3 as v3
from tools.brain_host_held_data_t_v1 import (
    REQUIRED_ROOT_ROLES,
    ROOT_KINDS,
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
STREAM_CHUNK_BYTES = 1024 * 1024
PAGE_RECORDS = 256
MAX_PAGE_BYTES = 16 * 1024 * 1024
MAX_METADATA_VALUE_BYTES = 8 * 1024 * 1024
MAX_OBJECT_METADATA_BYTES = 32 * 1024 * 1024
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
        hardlink_summary: dict[str, int],
        included_source_bytes: int,
        reserve_capacity: Callable[[int], None],
        scratch_reserve_bytes: int,
        metadata_reserve_bytes: int,
        payload_reserve_bytes: int,
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
        self.hardlink_summary = hardlink_summary
        self.included_source_bytes = included_source_bytes
        self.reserve_capacity = reserve_capacity
        self.scratch_reserve_bytes = scratch_reserve_bytes
        self.metadata_reserve_bytes = metadata_reserve_bytes
        self.payload_reserve_bytes = payload_reserve_bytes
        self.candidate_write_guard: Callable[[], None] | None = None
        self.entry_count = int(db.execute("SELECT count(*) FROM members").fetchone()[0])
        self._closed = False

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
                    "locator_sha256": (
                        sha256(declaration.relative_locator.encode())
                        if declaration.present
                        else None
                    ),
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
                    "unresolved_reason_sha256": (
                        sha256(declaration.unresolved_reason.encode())
                        if not declaration.present and declaration.unresolved_reason
                        else None
                    ),
                }
            )
        return {
            "schema": SCHEMA,
            "synthetic_fixture_only": True,
            "plan_sha256": self.plan_sha256,
            "role_dispositions": dispositions,
            "opaque_waivers": [dict(GIT_OPAQUE_WAIVER)],
            "present_root_count": len(self.roots),
            "unresolved_role_count": len(REQUIRED_ROOT_ROLES) - len(self.roots),
            "entry_count": self.entry_count,
            "included_source_bytes": self.included_source_bytes,
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
    """Build one descriptor-bound global inventory before any payload reads."""
    if not isinstance(view, HeldDataT) or not isinstance(writer, DescriptorWriter):
        raise TreeCaptureV5Error("held_view_or_writer_invalid")
    if getattr(view, "synthetic", False) is not True:
        raise TreeCaptureV5Error("synthetic_fixture_only")
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
    try:
        writer.assert_storage_binding(writer.storage_guard)
        reserve_capacity(scratch_reserve_bytes + metadata_reserve_bytes + payload_reserve_bytes)
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
        policies: dict[str, dict[str, Any]] = {}
        policy_hashes: dict[str, str] = {}
        for role in sorted(present_roles):
            policy, _raw, digest = _validate_policy_pin(role_policies[role])
            policies[role] = policy
            policy_hashes[role] = digest
        root_signatures: dict[str, tuple[int, ...]] = {}
        entry_count = 0
        data_device = os.fstat(view.mount_fd).st_dev
        for role in sorted(present_roles):
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
            )
            if os.fstat(roots[role]).st_dev != data_device:
                raise TreeCaptureV5Error("source_mount_boundary")
            root_signatures[role] = root_sig
            entry_count = int(db.execute("SELECT count(*) FROM members").fetchone()[0])
            if entry_count > max_entries:
                raise TreeCaptureV5Error("source_resource_limit")
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
        writer.assert_storage_binding(writer.storage_guard)
        try:
            reserve_capacity(
                scratch_reserve_bytes
                + metadata_reserve_bytes
                + source_bytes
                + payload_reserve_bytes
            )
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
            hardlink_summary=hardlink_summary,
            included_source_bytes=source_bytes,
            reserve_capacity=reserve_capacity,
            scratch_reserve_bytes=scratch_reserve_bytes,
            metadata_reserve_bytes=metadata_reserve_bytes,
            payload_reserve_bytes=payload_reserve_bytes,
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


def _capture_metadata(
    inventory: GlobalInventoryV5,
    base: str,
    role: str,
    path: str,
    kind: str,
    ordinal: int,
    expected_sig: bytes,
    sidecar_pages: list[dict[str, Any]],
    metadata_reserve_before: int,
) -> dict[str, Any]:
    source_fd, parent_fd, name = _open_source_entry(inventory.roots[role], path, kind)
    try:
        if source_fd is None:
            st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if canonical_json(list(v2._sig(st))) != expected_sig:
                raise TreeCaptureV5Error("source_object_changed_before_metadata")
            after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if v2._sig(after) != v2._sig(st):
                raise TreeCaptureV5Error("source_object_changed_during_metadata")
            return {
                "state": "unobserved_symlink_nofollow",
                "xattrs": [],
                "acl": None,
                "acl_state": "unobserved_symlink_nofollow",
                "flags_state": "value_required" if getattr(st, "st_flags", 0) else "absent",
                "flags_value": getattr(st, "st_flags", 0) or None,
            }
        st = os.fstat(source_fd)
        if canonical_json(list(v2._sig(st))) != expected_sig:
            raise TreeCaptureV5Error("source_object_changed_before_metadata")
        names = _xattr_names_fd(source_fd)
        if len(names) > 4096:
            raise TreeCaptureV5Error("metadata_xattr_count_limit")
        total = 0
        refs = []
        values = []
        token = f"{ordinal:016x}"
        for attr_name in sorted(names):
            try:
                decoded_name = attr_name.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise TreeCaptureV5Error("metadata_xattr_name_invalid") from exc
            value = _getxattr_fd(source_fd, attr_name)
            total += len(value)
            if total > MAX_OBJECT_METADATA_BYTES:
                raise TreeCaptureV5Error("metadata_object_limit")
            values.append((decoded_name, value))
        acl = _acl_bytes(source_fd)
        if acl is not None:
            total += len(acl)
            if total > MAX_OBJECT_METADATA_BYTES:
                raise TreeCaptureV5Error("metadata_object_limit")
        if metadata_reserve_before + total > inventory.metadata_reserve_bytes:
            raise TreeCaptureV5Error("metadata_total_limit")
        try:
            inventory.reserve_capacity(
                inventory.scratch_reserve_bytes
                + inventory.metadata_reserve_bytes
                + inventory.included_source_bytes
                + inventory.payload_reserve_bytes
            )
        except Exception as exc:
            raise TreeCaptureV5Error("metadata_storage_reserve_failed") from exc
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


def _create_candidate_member(inventory: GlobalInventoryV5, relative: str, payload: bytes) -> None:
    if inventory.candidate_write_guard is not None:
        inventory.candidate_write_guard()
    inventory.writer.create(relative, payload)


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


def _open_candidate_verify_db() -> tuple[int, str, int, int, sqlite3.Connection]:
    """Open a bounded, private local SQLite merge spool for candidate checks."""
    parent_fd = os.open(tempfile.gettempdir(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    name = f"brain-capture-v5-verify-{secrets.token_hex(16)}"
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        directory_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except BaseException:
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


def verify_candidate_v5(
    writer: DescriptorWriter,
    candidate_id: str,
    *,
    expected_index_sha256: str | None = None,
    require_readback: bool = True,
    allow_unbound_synthetic: bool = False,
) -> dict[str, Any]:
    """Read a published v5 candidate through a fresh root descriptor."""
    if len(candidate_id) != 32 or any(ch not in "0123456789abcdef" for ch in candidate_id):
        raise TreeCaptureV5Error("candidate_id_invalid")
    base = f"{CANDIDATE_BASE}/{candidate_id}"
    unbound_synthetic = writer.storage_guard is None and allow_unbound_synthetic
    if writer.storage_guard is None and not unbound_synthetic:
        raise TreeCaptureV5Error("candidate_storage_binding_invalid")
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
        candidate_fd = _open_output_directory(fresh_root, base)
        candidate_identity = _validate_candidate_directory(writer, candidate_fd)
        verify_scratch = _open_candidate_verify_db()
        verify_db = verify_scratch[4]
        index_raw = _candidate_read(candidate_fd, base, f"{base}/INDEX.json", MAX_PAGE_BYTES)
        index_digest = sha256(index_raw)
        if expected_index_sha256 is not None:
            if (
                len(expected_index_sha256) != 64
                or any(ch not in "0123456789abcdef" for ch in expected_index_sha256)
                or index_digest != expected_index_sha256
            ):
                raise TreeCaptureV5Error("candidate_index_pin_mismatch")
        index = json.loads(index_raw)
        if unbound_synthetic and index.get("synthetic_fixture_only") is not True:
            raise TreeCaptureV5Error("candidate_unbound_requires_synthetic_fixture")
        if set(index) != {
            "schema",
            "candidate_id",
            "synthetic_fixture_only",
            "plan_sha256",
            "role_dispositions",
            "opaque_waivers",
            "member_pages",
            "payload_pages",
            "sidecar_pages",
            "entry_count",
            "payload_bytes",
            "resource_model",
            "xattr_acl_flags_restore_policy",
        }:
            raise TreeCaptureV5Error("candidate_index_fields_invalid")
        if index.get("synthetic_fixture_only") is not True:
            raise TreeCaptureV5Error("candidate_not_synthetic_fixture")
        if (
            type(index.get("entry_count")) is not int
            or not 1 <= index["entry_count"] <= MAX_ENTRIES
            or type(index.get("payload_bytes")) is not int
            or not 0 <= index["payload_bytes"] <= MAX_SOURCE_BYTES
            or index.get("xattr_acl_flags_restore_policy") != "evidence_only_never_applied"
            or not isinstance(index.get("resource_model"), dict)
        ):
            raise TreeCaptureV5Error("candidate_index_value_invalid")
        if index.get("schema") != "brain-host-tree-capture-v5-candidate":
            raise TreeCaptureV5Error("candidate_index_schema_invalid")
        if index.get("candidate_id") != candidate_id:
            raise TreeCaptureV5Error("candidate_index_id_mismatch")
        plan_digest = index.get("plan_sha256")
        if (
            not isinstance(plan_digest, str)
            or len(plan_digest) != 64
            or any(ch not in "0123456789abcdef" for ch in plan_digest)
        ):
            raise TreeCaptureV5Error("candidate_plan_digest_invalid")
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
                    if os.fstat(verify_scratch[3]).st_size > MAX_SCRATCH_BYTES:
                        raise TreeCaptureV5Error("candidate_verify_scratch_limit")
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
            "index_sha256": index_digest,
            "member_count": checked,
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
    if getattr(inventory.view, "synthetic", False) is not True:
        raise TreeCaptureV5Error("synthetic_fixture_only")
    inventory.assert_source_stable()
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
    inventory.candidate_write_guard = lambda: _recheck_candidate_binding(
        inventory.writer, candidate_id, candidate_fd, candidate_identity
    )
    try:
        primary_query = (
            "SELECT role,path,payload_id,sig,size FROM members WHERE state='included' "
            "AND kind='file' AND payload_id IS NOT NULL "
            "AND (group_id IS NULL OR (role=primary_role AND path=primary_path)) "
            "ORDER BY payload_id COLLATE BINARY"
        )
        for role, path, payload_id, sig_raw, expected_size in inventory.db.execute(primary_query):
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
                    rel = f"{base}/payload/{payload_id}/{page_no:08d}.bin"
                    _create_candidate_member(inventory, rel, chunk)
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
                payload_sha = digest.hexdigest()
                inventory.db.execute(
                    "UPDATE members SET payload_sha256=?,payload_bytes=? WHERE payload_id=?",
                    (payload_sha, total, payload_id),
                )
            finally:
                os.close(source_fd)
                if parent_dir_fd >= 0:
                    os.close(parent_dir_fd)
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
                    _flush_json_page(
                        inventory.writer,
                        base,
                        "payload-index",
                        payload_page_no,
                        payload_batch,
                        before_write=inventory.candidate_write_guard,
                    )
                )
                payload_page_no += 1
                payload_batch = []
                payload_batch_bytes = 0
            payload_batch.append(item)
            payload_batch_bytes += len(encoded) + 1
        if payload_batch:
            payload_refs.append(
                _flush_json_page(
                    inventory.writer,
                    base,
                    "payload-index",
                    payload_page_no,
                    payload_batch,
                    before_write=inventory.candidate_write_guard,
                )
            )

        metadata_query = (
            "SELECT role,path,kind,sig,payload_id,group_id,primary_role,primary_path "
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
        ) in enumerate(inventory.db.execute(metadata_query)):
            sidecar_records: list[dict[str, Any]] = []
            metadata = _capture_metadata(
                inventory,
                base,
                role,
                path,
                kind,
                ordinal,
                bytes(sig_raw),
                sidecar_records,
                metadata_total_bytes,
            )
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
            record = {
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
                "link_target_b64": __import__("base64").b64encode(link_text).decode("ascii")
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
                ref = _flush_json_page(
                    inventory.writer,
                    base,
                    "members",
                    member_page_no,
                    member_batch,
                    before_write=inventory.candidate_write_guard,
                )
                member_refs.append(ref)
                member_page_no += 1
                member_batch = []
                member_bytes = 0
            member_batch.append(record)
            member_bytes += len(encoded) + 1
        if member_batch:
            member_refs.append(
                _flush_json_page(
                    inventory.writer,
                    base,
                    "members",
                    member_page_no,
                    member_batch,
                    before_write=inventory.candidate_write_guard,
                )
            )

        plan_receipt = inventory.public_inventory_receipt()
        index = {
            "schema": "brain-host-tree-capture-v5-candidate",
            "candidate_id": candidate_id,
            "synthetic_fixture_only": True,
            "plan_sha256": inventory.plan_sha256,
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
        index_raw = canonical_json(index)
        _create_candidate_member(inventory, f"{base}/INDEX.json", index_raw)
        verification = verify_candidate_v5(inventory.writer, candidate_id, require_readback=False)
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
        verification = verify_candidate_v5(inventory.writer, candidate_id)
        inventory.assert_source_stable()
        return {
            "schema": "brain-host-tree-capture-v5-candidate-receipt",
            "candidate_id": candidate_id,
            "synthetic_fixture_only": True,
            "index_sha256": sha256(index_raw),
            "entry_count": inventory.entry_count,
            "payload_bytes": inventory.included_source_bytes,
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


def _ensure_destination_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    st = os.fstat(fd)
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        os.close(fd)
        raise TreeCaptureV5Error("inert_destination_directory_invalid")
    return fd


def _open_destination_parent(root_fd: int, relative: str) -> tuple[int, str]:
    parts = relative.split("/")
    if not parts or any(part in {"", ".", ".."} or "/" in part for part in parts):
        raise TreeCaptureV5Error("inert_destination_path_invalid")
    current = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            child = _ensure_destination_directory(current, component)
            os.close(current)
            current = child
        return current, parts[-1]
    except BaseException:
        os.close(current)
        raise


def extract_candidate_inert_v5(
    writer: DescriptorWriter,
    candidate_id: str,
    *,
    quarantine_parent_fd: int,
    allow_unbound_synthetic: bool = False,
) -> dict[str, Any]:
    """Extract candidate data into a separate-device inert quarantine only.

    This is test/review extraction, not a host restore. The caller supplies a
    pinned, private directory FD on a device distinct from candidate storage.
    """
    verification = verify_candidate_v5(
        writer, candidate_id, allow_unbound_synthetic=allow_unbound_synthetic
    )
    candidate_device = os.fstat(writer.root_fd).st_dev
    parent_stat = os.fstat(quarantine_parent_fd)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.getuid()
        or stat.S_IMODE(parent_stat.st_mode) != 0o700
        or parent_stat.st_dev == candidate_device
    ):
        raise TreeCaptureV5Error("inert_quarantine_parent_invalid")
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
        extracted_entries = 0
        links_materialized = 0
        links_evidence_only = 0
        for page_ref in member_pages:
            raw = _candidate_read(candidate_fd, base, page_ref["path"], MAX_PAGE_BYTES)
            if sha256(raw) != page_ref["sha256"]:
                raise TreeCaptureV5Error("inert_member_page_hash_mismatch")
            for line in raw.splitlines():
                member = json.loads(line)
                if member["state"] != "included":
                    continue
                role = member["role"]
                path = member["path"]
                kind = member["kind"]
                rel = f"{role}/{path}" if path else role
                if kind == "directory":
                    directory_fd, name = _open_destination_parent(extraction_fd, rel)
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=directory_fd)
                    except FileExistsError:
                        st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if not stat.S_ISDIR(st.st_mode):
                            raise TreeCaptureV5Error("inert_destination_type_conflict") from None
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
                    directory_fd, name = _open_destination_parent(extraction_fd, rel)
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
                source_parent, source_name = _open_destination_parent(extraction_fd, source_rel)
                target_parent, target_name = _open_destination_parent(extraction_fd, target_rel)
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
        os.fsync(extraction_fd)
        _recheck_candidate_binding(
            writer,
            candidate_id,
            candidate_fd,
            candidate_identity,
            allow_unbound_synthetic=allow_unbound_synthetic,
        )
        os.close(extraction_fd)
        extraction_fd = -1
        return {
            "schema": "brain-host-tree-capture-v5-inert-extraction",
            "candidate_id": candidate_id,
            "extraction_id": extraction_id,
            "entry_count": extracted_entries,
            "links_materialized": links_materialized,
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
        }
    except BaseException:
        cleanup_error: BaseException | None = None
        if extraction_created:
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
        if cleanup_error is not None:
            raise TreeCaptureV5Error("inert_extraction_cleanup_incomplete") from cleanup_error
        raise
    finally:
        if candidate_fd >= 0:
            os.close(candidate_fd)
        os.close(source_root)
        os.close(destination_root)


def main(argv: list[str] | None = None) -> int:
    """Read-only fresh-process candidate verifier; no capture/publish options."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify", help="verify one existing candidate read-only")
    verify.add_argument("candidate_id")
    verify.add_argument("--index-sha256", required=True)
    verify.add_argument("--storage-config", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command != "verify":
        parser.error("unsupported command")
    try:
        if len(args.candidate_id) != 32 or any(
            ch not in "0123456789abcdef" for ch in args.candidate_id
        ):
            raise TreeCaptureV5Error("candidate_id_invalid")
        if len(args.index_sha256) != 64 or any(
            ch not in "0123456789abcdef" for ch in args.index_sha256
        ):
            raise TreeCaptureV5Error("candidate_index_pin_invalid")
        if __package__ in {None, ""}:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        import memecoin_research_storage as storage

        with storage.read_only_guard(args.storage_config.expanduser().absolute()) as guard:
            reader = DescriptorWriter.read_only_from_storage_guard(guard)
            try:
                result = verify_candidate_v5(
                    reader,
                    args.candidate_id,
                    expected_index_sha256=args.index_sha256,
                )
                guard.revalidate()
            finally:
                reader.close()
        sys.stdout.buffer.write(canonical_json(result))
        return 0
    except Exception as exc:
        message = str(exc)
        code = (
            message if message.isascii() and message.replace("_", "").isalnum() else "verify_failed"
        )
        sys.stderr.write(f"brain-host-tree-capture-v5 verify failed: {code}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
