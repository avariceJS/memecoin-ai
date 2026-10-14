#!/usr/bin/env python3
"""Descriptor-bound tree custody with explicit sensitive-path exclusions.

V3 is a separate archive namespace. It records included opaque files and a
private metadata-only inventory for excluded files/subtrees. It never reads or
hashes the contents of excluded regular files and never descends into excluded
subtree roots.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import posixpath
import secrets
import sqlite3
import stat
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

# Support both `python -m tools.brain_host_tree_custody_v3` and direct CLI use.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memecoin_research_storage import StorageError
from tools import brain_host_tree_custody as v2
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SCHEMA = "brain-host-tree-custody-v3"
RESERVATION_SCHEMA = "brain-host-tree-custody-v3-reservation"
COMPLETE_SCHEMA = "brain-host-tree-custody-v3-complete"
RESTORE_SCHEMA = "brain-host-tree-custody-v3-restore"
POLICY_SCHEMA = "brain-host-tree-exclusion-policy-v3"
BASE = "host-recovery/tree-custody-v3"
POLICY_NAME = "POLICY.json"
CHUNK_BYTES = v2.CHUNK_BYTES
PAGE_ENTRIES = 512
INDEX_ENTRIES = 1024
MAX_ENTRIES = v2.MAX_ENTRIES
MAX_SOURCE_BYTES = v2.MAX_SOURCE_BYTES
MAX_PAGE_BYTES = v2.MAX_PAGE_BYTES
MAX_MANIFEST_BYTES = v2.MAX_MANIFEST_BYTES
CAPTURE_ID_CHARS = v2.CAPTURE_ID_CHARS
PRIVATE_SUBTREE_COMPONENTS = {".ssh", "keychains", "credentials", "secrets", "wallets"}
PROTECTED_COMPONENTS = v2.PROTECTED_COMPONENTS
PEM_DISPOSITIONS = {"reauth_reissue", "rebuild_disposable", "include_nonsecret"}
EXCLUSION_RULES = {
    "private_subtree_components": sorted(PRIVATE_SUBTREE_COMPONENTS),
    "env_filename_prefix": ".env",
    "private_key_suffixes": [".key", ".p12", ".pfx"],
    "private_key_basenames": ["id_dsa", "id_ecdsa", "id_ed25519", "id_rsa"],
    "pem_candidate_suffix": ".pem",
    "protected_components_in_nonprotected_scope": sorted(PROTECTED_COMPONENTS),
}
RULES_SHA256 = sha256(canonical_json(EXCLUSION_RULES))
STABILITY_CLAIM = "no_metadata_drift_pre_post_and_each_included_file_stable_during_hashing"
ATOMICITY_SCOPE = "no_cross_file_or_application_atomicity"
ROOT_CLOSURE_SCOPE = (
    "declared_root_only_excluded_subtree_descendants_unknown_external_links_not_traversed"
)
SYMLINK_MODE_POLICY = "source_mode_recorded_restore_uses_host_default"
EXCLUDED_METADATA_POLICY = "lstat_only_xattrs_acl_not_inspected_no_content_read"


class TreeCustodyV3Error(ValueError):
    """Stable, path-free error codes for v3 capture and restore."""


def _path_component(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or len(os.fsencode(value)) > v2.MAX_COMPONENT_BYTES
        or any(ord(char) < 32 for char in value)
    ):
        raise TreeCustodyV3Error("unsafe_path_component")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise TreeCustodyV3Error("path_component_not_utf8") from exc
    return value


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise TreeCustodyV3Error("unsafe_relative_path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or not path.parts:
        raise TreeCustodyV3Error("unsafe_relative_path")
    if len(path.parts) > v2.MAX_PATH_DEPTH:
        raise TreeCustodyV3Error("path_depth_limit")
    for part in path.parts:
        _path_component(part)
    return value


def _validate_policy(policy: object) -> dict[str, Any]:
    if not isinstance(policy, dict) or set(policy) != {
        "schema",
        "policy_id",
        "version",
        "rule_set",
        "pem_candidate_dispositions",
        "exact_path_exclusions",
        "symlink_target_dispositions",
        "rebuild_inputs",
    }:
        raise TreeCustodyV3Error("policy_contract_invalid")
    if policy["schema"] != POLICY_SCHEMA or policy["rule_set"] != "owner-work-sensitive-v1":
        raise TreeCustodyV3Error("policy_version_unsupported")
    if (
        not isinstance(policy["policy_id"], str)
        or not policy["policy_id"]
        or len(policy["policy_id"]) > 128
        or type(policy["version"]) is not int
        or policy["version"] != 1
    ):
        raise TreeCustodyV3Error("policy_identity_invalid")
    candidates = policy["pem_candidate_dispositions"]
    if not isinstance(candidates, dict):
        raise TreeCustodyV3Error("policy_pem_candidates_invalid")
    validated: dict[str, str] = {}
    for raw_path, disposition in candidates.items():
        path = _relative(raw_path)
        if (
            not path.casefold().endswith(".pem")
            or not isinstance(disposition, str)
            or disposition not in PEM_DISPOSITIONS
        ):
            raise TreeCustodyV3Error("policy_pem_candidate_invalid")
        validated[path] = disposition
    raw_exact = policy["exact_path_exclusions"]
    if not isinstance(raw_exact, dict):
        raise TreeCustodyV3Error("policy_exact_exclusions_invalid")
    exact: dict[str, dict[str, str]] = {}
    allowed_exact_dispositions = {
        "separate_protected_custody",
        "substitute_online_backup",
        "rebuild_disposable",
        "disposable_not_required",
    }
    for raw_path, declaration in raw_exact.items():
        path = _relative(raw_path)
        if (
            not isinstance(declaration, dict)
            or set(declaration) != {"entry_type", "disposition"}
            or not isinstance(declaration.get("entry_type"), str)
            or not isinstance(declaration.get("disposition"), str)
            or declaration["entry_type"] not in {"directory", "file", "symlink"}
            or declaration["disposition"] not in allowed_exact_dispositions
        ):
            raise TreeCustodyV3Error("policy_exact_exclusion_invalid")
        exact[path] = {
            "entry_type": declaration["entry_type"],
            "disposition": declaration["disposition"],
        }
    exact_paths = set(exact)
    for path in exact_paths:
        parent = path
        while "/" in parent:
            parent = parent.rpartition("/")[0]
            if parent in exact_paths:
                raise TreeCustodyV3Error("policy_exact_exclusion_nested")
    raw_symlinks = policy["symlink_target_dispositions"]
    if not isinstance(raw_symlinks, dict):
        raise TreeCustodyV3Error("policy_symlink_targets_invalid")
    symlink_targets: dict[str, dict[str, Any]] = {}
    allowed_symlink_dispositions = {
        "preserve_dangling_internal",
        "preserve_intra_root_absolute",
        "external_root_binding",
    }
    for raw_path, declaration in raw_symlinks.items():
        path = _relative(raw_path)
        if not isinstance(declaration, dict):
            raise TreeCustodyV3Error("policy_symlink_target_invalid")
        disposition = declaration.get("disposition")
        digest = v2._strict_hash(declaration.get("target_sha256"), "symlink_target")
        if (
            type(declaration.get("required")) is not bool
            or disposition not in allowed_symlink_dispositions
        ):
            raise TreeCustodyV3Error("policy_symlink_target_invalid")
        normalized: dict[str, Any] = {
            "disposition": disposition,
            "required": declaration["required"],
            "target_sha256": digest,
        }
        if disposition in {"preserve_dangling_internal", "preserve_intra_root_absolute"}:
            if set(declaration) != {"disposition", "required", "target_sha256", "relative_locator"}:
                raise TreeCustodyV3Error("policy_symlink_target_invalid")
            normalized["relative_locator"] = _relative(declaration["relative_locator"])
        else:
            required_keys = {
                "disposition",
                "required",
                "target_sha256",
                "volume_uuid",
                "root_id",
                "relative_locator",
                "authority_ref",
            }
            if set(declaration) != required_keys:
                raise TreeCustodyV3Error("policy_symlink_target_invalid")
            for key in ("volume_uuid", "root_id", "authority_ref"):
                if (
                    not isinstance(declaration[key], str)
                    or not declaration[key]
                    or len(declaration[key]) > 256
                ):
                    raise TreeCustodyV3Error("policy_symlink_target_invalid")
            normalized.update(
                {
                    "volume_uuid": declaration["volume_uuid"],
                    "root_id": declaration["root_id"],
                    "relative_locator": _relative(declaration["relative_locator"]),
                    "authority_ref": declaration["authority_ref"],
                }
            )
        if path in exact:
            raise TreeCustodyV3Error("policy_symlink_target_excluded")
        symlink_targets[path] = normalized
    raw_rebuild_inputs = policy["rebuild_inputs"]
    if not isinstance(raw_rebuild_inputs, list):
        raise TreeCustodyV3Error("policy_rebuild_inputs_invalid")
    rebuild_inputs: list[dict[str, str]] = []
    role_paths: set[tuple[str, str]] = set()
    for item in raw_rebuild_inputs:
        if (
            not isinstance(item, dict)
            or set(item) != {"role", "path", "sha256"}
            or not isinstance(item["role"], str)
            or item["role"] not in {"dependency_lock", "bootstrap"}
            or not isinstance(item["path"], str)
        ):
            raise TreeCustodyV3Error("policy_rebuild_input_invalid")
        path = _relative(item["path"])
        digest = v2._strict_hash(item["sha256"], "rebuild_input")
        if (item["role"], path) in role_paths:
            raise TreeCustodyV3Error("policy_rebuild_input_duplicate")
        rebuild_inputs.append({"role": item["role"], "path": path, "sha256": digest})
        role_paths.add((item["role"], path))
    requires_rebuild_inputs = (
        any(item["disposition"] == "rebuild_disposable" for item in exact.values())
        or "rebuild_disposable" in validated.values()
    )
    required_inputs = {
        ("bootstrap", "Makefile"),
        ("dependency_lock", "requirements-dev.lock"),
        ("dependency_lock", "requirements-ml.lock"),
    }
    if requires_rebuild_inputs and role_paths != required_inputs:
        raise TreeCustodyV3Error("policy_rebuild_inputs_incomplete")
    if not requires_rebuild_inputs and rebuild_inputs:
        raise TreeCustodyV3Error("policy_rebuild_inputs_unexpected")
    rebuild_inputs.sort(key=lambda item: (item["role"], item["path"]))
    return {
        "schema": POLICY_SCHEMA,
        "policy_id": policy["policy_id"],
        "version": 1,
        "rule_set": "owner-work-sensitive-v1",
        "pem_candidate_dispositions": dict(sorted(validated.items())),
        "exact_path_exclusions": dict(sorted(exact.items())),
        "symlink_target_dispositions": dict(sorted(symlink_targets.items())),
        "rebuild_inputs": rebuild_inputs,
    }


def _load_policy(policy_path: Path) -> tuple[dict[str, Any], bytes, str]:
    fd = -1
    try:
        fd = os.open(policy_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_PAGE_BYTES:
            raise TreeCustodyV3Error("policy_file_invalid")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(fd, min(64 * 1024, MAX_PAGE_BYTES + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_PAGE_BYTES:
                raise TreeCustodyV3Error("policy_size_limit")
        after = os.fstat(fd)
        if v2._sig(before) != v2._sig(after) or total != before.st_size:
            raise TreeCustodyV3Error("policy_changed_during_read")
        raw = b"".join(chunks)
    except OSError as exc:
        raise TreeCustodyV3Error("policy_read_failed") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TreeCustodyV3Error("policy_invalid") from exc
    policy = _validate_policy(value)
    canonical = canonical_json(policy)
    if canonical != raw:
        raise TreeCustodyV3Error("policy_noncanonical")
    return policy, raw, sha256(raw)


def _classify_path(
    path: str,
    kind: str,
    policy: dict[str, Any],
    *,
    protected_scope: bool,
) -> tuple[str, str | None, str | None]:
    parts = path.split("/") if path else []
    for component in parts:
        lowered = component.casefold()
        if not protected_scope and lowered in PROTECTED_COMPONENTS:
            return (
                "excluded_subtree" if kind == "directory" else "excluded_file",
                "protected_reserved_component",
                "protected_scope",
            )
    if not parts:
        return "included", None, None
    exact = policy["exact_path_exclusions"].get(path)
    if exact is not None:
        if exact["entry_type"] != kind:
            raise TreeCustodyV3Error("policy_exact_exclusion_type_mismatch")
        state = "excluded_subtree" if kind == "directory" else "excluded_file"
        return state, "policy_exact_path", exact["disposition"]
    basename = parts[-1]
    lowered = basename.casefold()
    if any(part.casefold() in PRIVATE_SUBTREE_COMPONENTS for part in parts):
        return (
            "excluded_subtree" if kind == "directory" else "excluded_file",
            "private_subtree_component",
            "reauth_reissue",
        )
    if lowered.startswith(".env"):
        return (
            "excluded_subtree" if kind == "directory" else "excluded_file",
            "env_filename_prefix",
            "reauth_reissue",
        )
    if (
        lowered.endswith((".key", ".p12", ".pfx"))
        or lowered in EXCLUSION_RULES["private_key_basenames"]
    ):
        return (
            "excluded_subtree" if kind == "directory" else "excluded_file",
            "private_key_material",
            "reauth_reissue",
        )
    if kind == "file" and lowered.endswith(".pem"):
        disposition = policy["pem_candidate_dispositions"].get(path)
        if disposition == "include_nonsecret":
            return "included", "pem_candidate", disposition
        if disposition is None:
            return "excluded_file", "pem_candidate", "unresolved_candidate"
        return "excluded_file", "pem_candidate", disposition
    return "included", None, None


def _db_open(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    db.execute(
        "CREATE TABLE entries(path TEXT PRIMARY KEY,kind TEXT NOT NULL,mode INTEGER NOT NULL,"
        "uid INTEGER NOT NULL,gid INTEGER NOT NULL,size INTEGER NOT NULL,mtime INTEGER NOT NULL,"
        "ctime INTEGER NOT NULL,dev INTEGER NOT NULL,ino INTEGER NOT NULL,nlink INTEGER NOT NULL,"
        "sig BLOB NOT NULL,link_b64 TEXT,group_id TEXT,primary_path TEXT,digest TEXT,"
        "chunk_count INTEGER NOT NULL DEFAULT 0,chunk_pages TEXT NOT NULL DEFAULT '[]',"
        "omitted_xattrs TEXT NOT NULL DEFAULT '[]',state TEXT NOT NULL,rule TEXT,"
        "disposition TEXT,descendants_unknown INTEGER NOT NULL DEFAULT 0,"
        "symlink_target_unresolved INTEGER NOT NULL DEFAULT 0,symlink_policy TEXT)"
    )
    return db


def _insert_entry(
    db: sqlite3.Connection,
    path: str,
    kind: str,
    st: os.stat_result,
    *,
    state: str,
    rule: str | None,
    disposition: str | None,
    link_b64: str | None = None,
    omitted_xattrs: list[str] | None = None,
) -> None:
    db.execute(
        "INSERT INTO entries(path,kind,mode,uid,gid,size,mtime,ctime,dev,ino,nlink,sig,"
        "link_b64,omitted_xattrs,state,rule,disposition,descendants_unknown) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            path,
            kind,
            stat.S_IMODE(st.st_mode),
            st.st_uid,
            st.st_gid,
            st.st_size,
            st.st_mtime_ns,
            st.st_ctime_ns,
            st.st_dev,
            st.st_ino,
            st.st_nlink,
            json.dumps(v2._sig(st), separators=(",", ":")).encode(),
            link_b64,
            json.dumps(omitted_xattrs or [], separators=(",", ":")),
            state,
            rule,
            disposition,
            int(state == "excluded_subtree"),
        ),
    )


def _xattrs_for_symlink(dir_fd: int, name: str) -> list[str]:
    if sys.platform == "darwin" and hasattr(os, "O_SYMLINK"):
        fd = os.open(name, os.O_RDONLY | os.O_SYMLINK, dir_fd=dir_fd)
        try:
            return list(v2._check_extended_metadata_fd(fd))
        finally:
            os.close(fd)
    return list(v2._check_extended_metadata(f"/dev/fd/{dir_fd}/{name}", is_link=True))


def _inventory(
    root_fd: int,
    root_device: int,
    db: sqlite3.Connection,
    policy: dict[str, Any],
    *,
    protected_scope: bool,
) -> tuple[int, int]:
    entries = 0
    included_bytes = 0

    def add(
        path: str,
        kind: str,
        st: os.stat_result,
        state: str,
        rule: str | None,
        disposition: str | None,
        *,
        link_b64: str | None = None,
        omitted_xattrs: list[str] | None = None,
    ) -> None:
        nonlocal entries, included_bytes
        entries += 1
        if entries > MAX_ENTRIES:
            raise TreeCustodyV3Error("source_entry_limit")
        if st.st_dev != root_device:
            raise TreeCustodyV3Error("source_mount_boundary")
        v2._validated_source_flags(st)
        if state == "included" and kind == "file":
            included_bytes += st.st_size
            if included_bytes > MAX_SOURCE_BYTES:
                raise TreeCustodyV3Error("source_size_limit")
        _insert_entry(
            db,
            path,
            kind,
            st,
            state=state,
            rule=rule,
            disposition=disposition,
            link_b64=link_b64,
            omitted_xattrs=omitted_xattrs,
        )

    root_stat = os.fstat(root_fd)
    if not stat.S_ISDIR(root_stat.st_mode):
        raise TreeCustodyV3Error("source_root_changed")
    root_xattrs = list(v2._check_extended_metadata_fd(root_fd))
    add("", "directory", root_stat, "included", None, None, omitted_xattrs=root_xattrs)

    def walk(directory_fd: int, prefix: str) -> None:
        try:
            scan = os.scandir(directory_fd)
        except OSError as exc:
            raise TreeCustodyV3Error("source_inventory_failed") from exc
        with scan:
            for entry in scan:
                name = _path_component(entry.name)
                relative = f"{prefix}/{name}" if prefix else name
                _relative(relative)
                try:
                    st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError as exc:
                    raise TreeCustodyV3Error("source_inventory_changed") from exc
                if st.st_dev != root_device:
                    raise TreeCustodyV3Error("source_mount_boundary")
                if stat.S_ISDIR(st.st_mode):
                    kind = "directory"
                elif stat.S_ISREG(st.st_mode):
                    kind = "file"
                elif stat.S_ISLNK(st.st_mode):
                    kind = "symlink"
                else:
                    raise TreeCustodyV3Error("source_special_file_rejected")
                state, rule, disposition = _classify_path(
                    relative,
                    kind,
                    policy,
                    protected_scope=protected_scope,
                )
                if state != "included":
                    add(relative, kind, st, state, rule, disposition)
                    continue
                if kind == "directory":
                    child_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                    try:
                        child_stat = os.fstat(child_fd)
                        if v2._sig(child_stat) != v2._sig(st):
                            raise TreeCustodyV3Error("source_inventory_changed")
                        omitted = list(v2._check_extended_metadata_fd(child_fd))
                        add(relative, kind, st, state, rule, disposition, omitted_xattrs=omitted)
                        walk(child_fd, relative)
                    finally:
                        os.close(child_fd)
                elif kind == "file":
                    fd = os.open(
                        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
                    )
                    try:
                        if v2._sig(os.fstat(fd)) != v2._sig(st):
                            raise TreeCustodyV3Error("source_inventory_changed")
                        omitted = list(v2._check_extended_metadata_fd(fd))
                    finally:
                        os.close(fd)
                    add(relative, kind, st, state, rule, disposition, omitted_xattrs=omitted)
                else:
                    omitted = _xattrs_for_symlink(directory_fd, name)
                    try:
                        target = os.readlink(os.fsencode(name), dir_fd=directory_fd)
                        target_bytes = target if isinstance(target, bytes) else os.fsencode(target)
                    except OSError as exc:
                        raise TreeCustodyV3Error("source_symlink_read_failed") from exc
                    add(
                        relative,
                        kind,
                        st,
                        state,
                        rule,
                        disposition,
                        link_b64=base64.b64encode(target_bytes).decode("ascii"),
                        omitted_xattrs=omitted,
                    )

    walk(root_fd, "")
    for candidate in policy["pem_candidate_dispositions"]:
        row = db.execute("SELECT kind FROM entries WHERE path=?", (candidate,)).fetchone()
        if row is None or row[0] != "file" or not candidate.casefold().endswith(".pem"):
            raise TreeCustodyV3Error("policy_candidate_path_unmatched")
    for exact_path, declaration in policy["exact_path_exclusions"].items():
        row = db.execute(
            "SELECT kind,state,disposition FROM entries WHERE path=?", (exact_path,)
        ).fetchone()
        if row is None or row[0] != declaration["entry_type"] or row[1] == "included":
            raise TreeCustodyV3Error("policy_exact_exclusion_unmatched")
        if row[2] != declaration["disposition"]:
            raise TreeCustodyV3Error("policy_exact_exclusion_disposition_mismatch")
    db.commit()
    return entries, included_bytes


def _resolve_hardlinks(db: sqlite3.Connection) -> tuple[int, int]:
    """Propagate exclusions across aliases and reject incomplete inode groups."""
    unresolved_groups = 0
    db.execute("CREATE TEMP TABLE link_groups(dev,ino,count,nlink,first_path,excluded_count)")
    db.execute(
        "INSERT INTO link_groups "
        "SELECT dev,ino,count(*),max(nlink),min(path),"
        "sum(CASE WHEN state!='included' THEN 1 ELSE 0 END) "
        "FROM entries WHERE kind='file' GROUP BY dev,ino HAVING max(nlink)>1"
    )
    cursor = db.execute("SELECT dev,ino,count,nlink,first_path,excluded_count FROM link_groups")
    while rows := cursor.fetchmany(128):
        for dev, ino, count, nlink, first_path, excluded_count in rows:
            group_id = hashlib.sha256(f"{dev}:{ino}:{first_path}".encode()).hexdigest()
            if excluded_count:
                direct = db.execute(
                    "SELECT disposition FROM entries WHERE dev=? AND ino=? AND state!='included' "
                    "ORDER BY path LIMIT 1",
                    (dev, ino),
                ).fetchone()
                disposition = direct[0] if direct else "reauth_reissue"
                db.execute(
                    "UPDATE entries SET state='excluded_file',rule='hardlink_alias_to_excluded',"
                    "disposition=?,group_id=?,primary_path=NULL "
                    "WHERE dev=? AND ino=? AND state='included'",
                    (disposition, group_id, dev, ino),
                )
                db.execute(
                    "UPDATE entries SET group_id=? WHERE dev=? AND ino=? AND state!='included'",
                    (group_id, dev, ino),
                )
            elif count != nlink:
                unresolved_groups += 1
                db.execute(
                    "UPDATE entries SET state='excluded_file',rule='hardlink_group_incomplete',"
                    "disposition='unresolved_hardlink_group',group_id=?,primary_path=NULL "
                    "WHERE dev=? AND ino=? AND state='included'",
                    (group_id, dev, ino),
                )
            else:
                primary = db.execute(
                    "SELECT min(path) FROM entries WHERE dev=? AND ino=?", (dev, ino)
                ).fetchone()[0]
                db.execute(
                    "UPDATE entries SET group_id=?,primary_path=? WHERE dev=? AND ino=?",
                    (group_id, primary, dev, ino),
                )
    db.execute("DROP TABLE temp.link_groups")
    db.commit()
    return db.execute(
        "SELECT count(*) FROM entries WHERE state='excluded_file' "
        "AND disposition='unresolved_hardlink_group'"
    ).fetchone()[0], unresolved_groups


def _mark_excluded_symlink_targets(
    db: sqlite3.Connection, source_root: Path, policy: dict[str, Any]
) -> int:
    count = 0
    seen_policy: set[str] = set()
    root_text = os.fspath(source_root).rstrip(os.sep)
    for path, encoded in db.execute(
        "SELECT path,link_b64 FROM entries WHERE state='included' AND kind='symlink'"
    ):
        try:
            target = base64.b64decode(encoded, validate=True).decode("utf-8", errors="strict")
        except (ValueError, UnicodeDecodeError):
            db.execute("UPDATE entries SET symlink_target_unresolved=1 WHERE path=?", (path,))
            count += 1
            continue
        if target.startswith(os.sep):
            if target == root_text:
                resolved = ""
            elif target.startswith(root_text + os.sep):
                resolved = target[len(root_text) + 1 :]
            else:
                resolved = None
        else:
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
        unresolved = resolved is None or resolved == ".." or resolved.startswith("../")
        if not unresolved:
            if resolved:
                try:
                    _relative(resolved)
                except TreeCustodyV3Error:
                    unresolved = True
        if not unresolved:
            matched = db.execute(
                "SELECT state FROM entries WHERE path=? LIMIT 1", (resolved,)
            ).fetchone()
            unresolved = matched is None or matched[0] != "included"
            parent = resolved
            while not unresolved and "/" in parent:
                parent = parent.rpartition("/")[0]
                state = db.execute(
                    "SELECT state FROM entries WHERE path=? LIMIT 1", (parent,)
                ).fetchone()
                if state is None or state[0] != "included":
                    unresolved = True
        declaration = policy["symlink_target_dispositions"].get(path)
        if not unresolved and declaration is None:
            continue
        if declaration is None:
            db.execute("UPDATE entries SET symlink_target_unresolved=1 WHERE path=?", (path,))
            count += 1
            continue
        target_bytes = base64.b64decode(encoded, validate=True)
        if sha256(target_bytes) != declaration["target_sha256"]:
            raise TreeCustodyV3Error("policy_symlink_target_mismatch")
        disposition = declaration["disposition"]
        locator = declaration["relative_locator"]
        if disposition == "preserve_dangling_internal":
            if target.startswith(os.sep) or resolved is None or resolved in {"", "."}:
                raise TreeCustodyV3Error("policy_symlink_target_mismatch")
            if _relative(resolved) != locator:
                raise TreeCustodyV3Error("policy_symlink_target_mismatch")
            matched = db.execute(
                "SELECT state FROM entries WHERE path=? LIMIT 1", (resolved,)
            ).fetchone()
            if matched is not None:
                raise TreeCustodyV3Error("policy_symlink_target_mismatch")
        elif disposition == "preserve_intra_root_absolute":
            if not target.startswith(root_text + os.sep) or resolved is None:
                raise TreeCustodyV3Error("policy_symlink_target_mismatch")
            if _relative(resolved) != locator:
                raise TreeCustodyV3Error("policy_symlink_target_mismatch")
            if unresolved:
                raise TreeCustodyV3Error("policy_symlink_target_mismatch")
        elif disposition == "external_root_binding":
            if resolved is not None:
                raise TreeCustodyV3Error("policy_symlink_target_mismatch")
        else:
            raise TreeCustodyV3Error("policy_symlink_target_invalid")
        seen_policy.add(path)
        pending = disposition == "external_root_binding" or declaration["required"]
        db.execute(
            "UPDATE entries SET symlink_target_unresolved=?,symlink_policy=? WHERE path=?",
            (int(pending), canonical_json(declaration).decode("utf-8"), path),
        )
        count += int(pending)
    if seen_policy != set(policy["symlink_target_dispositions"]):
        raise TreeCustodyV3Error("policy_symlink_membership_mismatch")
    db.commit()
    return count


def _excluded_row(row: tuple[Any, ...]) -> dict[str, Any]:
    (
        path,
        kind,
        mode,
        uid,
        gid,
        size,
        mtime,
        ctime,
        dev,
        ino,
        nlink,
        sig_bytes,
        _link_b64,
        group_id,
        _primary,
        _digest,
        _chunks,
        _chunk_pages,
        _omitted,
        state,
        rule,
        disposition,
        descendants_unknown,
    ) = row
    record = {
        "path": path,
        "entry_type": kind,
        "exclusion_kind": "subtree" if state == "excluded_subtree" else "file",
        "mode": mode,
        "uid": uid,
        "gid": gid,
        "size_bytes": size,
        "mtime_ns": mtime,
        "source_ctime_ns": ctime,
        "source_device": dev,
        "source_inode": ino,
        "source_nlink": nlink,
        "source_flags": json.loads(sig_bytes)[-1],
        "excluded_xattrs_status": "not_inspected",
        "rule": rule,
        "disposition": disposition,
    }
    if group_id:
        record["hardlink_group"] = group_id
    if descendants_unknown:
        record["descendants_status"] = "unknown_not_traversed"
    return record


def _included_row_record(row: tuple[Any, ...], *, protected_scope: bool) -> dict[str, Any]:
    path = row[0]
    _relative(path) if path else None
    if path.casefold().endswith(".pem"):
        placeholder = "__v3_explicit_nonsecret_pem__"
        mutable = list(row[:19])
        mutable[0] = placeholder
        if mutable[14] == path:
            mutable[14] = placeholder
        item = v2._entry_record(tuple(mutable), protected=protected_scope)
        item["path"] = path
        if item.get("primary_path") == placeholder:
            item["primary_path"] = path
        return item
    return v2._entry_record(row[:19], protected=protected_scope)


def _emit_pages(
    writer: DescriptorWriter,
    base: str,
    db: sqlite3.Connection,
    *,
    included: bool,
    protected_scope: bool,
) -> list[dict[str, Any]]:
    folder = "entry-pages" if included else "exclusion-pages"
    refs: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    page_bytes = 0

    def flush() -> None:
        nonlocal page_bytes
        if not records:
            return
        index = len(refs)
        rel = f"{base}/{folder}/{index:08d}.jsonl"
        raw = b"\n".join(canonical_json(item).rstrip(b"\n") for item in records) + b"\n"
        writer.create(rel, raw)
        refs.append({"path": rel, "sha256": sha256(raw), "count": len(records)})
        records.clear()
        page_bytes = 0

    query = (
        "SELECT path,kind,mode,uid,gid,size,mtime,ctime,dev,ino,nlink,sig,link_b64,"
        "group_id,primary_path,digest,chunk_count,chunk_pages,omitted_xattrs,state,rule,"
        "disposition,descendants_unknown,symlink_target_unresolved,symlink_policy "
        "FROM entries WHERE state='included' "
        "ORDER BY path COLLATE BINARY"
        if included
        else "SELECT path,kind,mode,uid,gid,size,mtime,ctime,dev,ino,nlink,sig,link_b64,"
        "group_id,primary_path,digest,chunk_count,chunk_pages,omitted_xattrs,state,rule,"
        "disposition,descendants_unknown FROM entries WHERE state!='included' "
        "ORDER BY path COLLATE BINARY"
    )
    for row in db.execute(query):
        item = (
            _included_row_record(row, protected_scope=protected_scope)
            if included
            else _excluded_row(row)
        )
        if included and row[23]:
            item["unresolved_target_exclusion"] = True
        if included and row[24] is not None:
            item["symlink_target_policy"] = json.loads(row[24])
        encoded = canonical_json(item)
        if records and (
            len(records) >= PAGE_ENTRIES or page_bytes + len(encoded) + 1 > MAX_PAGE_BYTES
        ):
            flush()
        records.append(item)
        page_bytes += len(encoded) + 1
    flush()
    return refs


def _emit_indexes(
    writer: DescriptorWriter,
    base: str,
    refs: list[dict[str, Any]],
    *,
    kind: str,
) -> list[dict[str, Any]]:
    result = []
    folder = "entry-index" if kind == "entries" else "exclusion-index"
    for start in range(0, len(refs), INDEX_ENTRIES):
        rows = refs[start : start + INDEX_ENTRIES]
        rel = f"{base}/{folder}/{start // INDEX_ENTRIES:08d}.json"
        raw = canonical_json({"schema": f"brain-host-tree-v3-{kind}-index", "rows": rows})
        writer.create(rel, raw)
        result.append({"path": rel, "sha256": sha256(raw), "count": len(rows)})
    return result


def _iter_index_refs(
    writer: DescriptorWriter, indexes: list[dict[str, Any]], *, kind: str
) -> Iterator[dict[str, Any]]:
    schema = f"brain-host-tree-v3-{kind}-index"
    for iref in indexes:
        raw = v2._restore_read_pinned(
            writer,
            _relative(iref["path"]),
            iref["sha256"],
            max_bytes=MAX_PAGE_BYTES,
            label=f"{kind}_index",
        )
        try:
            obj = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TreeCustodyV3Error("index_page_invalid") from exc
        if (
            not isinstance(obj, dict)
            or canonical_json(obj) != raw
            or set(obj) != {"schema", "rows"}
            or obj["schema"] != schema
            or not isinstance(obj["rows"], list)
            or len(obj["rows"]) != iref["count"]
        ):
            raise TreeCustodyV3Error("index_page_invalid")
        yield from obj["rows"]


def _read_page(
    writer: DescriptorWriter, ref: dict[str, Any], *, included: bool
) -> list[dict[str, Any]]:
    label = "entry_page" if included else "exclusion_page"
    raw = v2._restore_read_pinned(
        writer,
        _relative(ref["path"]),
        ref["sha256"],
        max_bytes=MAX_PAGE_BYTES,
        label=label,
    )
    lines = raw.splitlines()
    if len(lines) != ref["count"]:
        raise TreeCustodyV3Error(f"{label}_count_mismatch")
    try:
        records = [json.loads(line) for line in lines]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TreeCustodyV3Error(f"{label}_invalid") from exc
    if any(
        not isinstance(item, dict) or canonical_json(item).rstrip(b"\n") != line
        for item, line in zip(records, lines, strict=True)
    ):
        raise TreeCustodyV3Error(f"{label}_invalid")
    return records


def _iter_records(
    writer: DescriptorWriter,
    indexes: list[dict[str, Any]],
    *,
    kind: str,
    reverse: bool = False,
) -> Iterator[dict[str, Any]]:
    refs = list(_iter_index_refs(writer, indexes, kind=kind))
    if reverse:
        refs.reverse()
    for ref in refs:
        records = _read_page(writer, ref, included=kind == "entries")
        yield from reversed(records) if reverse else records


def _read_chunk_rows(writer: DescriptorWriter, entry: dict[str, Any]) -> Iterator[dict[str, Any]]:
    file_id = hashlib.sha256(entry["path"].encode("utf-8")).hexdigest()
    for ref in entry["chunk_index_pages"]:
        raw = v2._restore_read_pinned(
            writer,
            _relative(ref["path"]),
            ref["sha256"],
            max_bytes=MAX_PAGE_BYTES,
            label="chunk_index_page",
        )
        try:
            index = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TreeCustodyV3Error("chunk_index_page_invalid") from exc
        if (
            not isinstance(index, dict)
            or canonical_json(index) != raw
            or set(index) != {"schema", "file_id", "rows"}
            or index["schema"] != "brain-host-tree-chunk-index-v1"
            or index["file_id"] != file_id
            or not isinstance(index["rows"], list)
            or len(index["rows"]) != ref["count"]
        ):
            raise TreeCustodyV3Error("chunk_index_page_invalid")
        for chunk in index["rows"]:
            if (
                not isinstance(chunk, dict)
                or set(chunk) != {"path", "size_bytes", "sha256"}
                or type(chunk.get("size_bytes")) is not int
                or not 0 <= chunk["size_bytes"] <= CHUNK_BYTES
            ):
                raise TreeCustodyV3Error("chunk_ref_invalid")
            _relative(chunk["path"])
            v2._strict_hash(chunk["sha256"], "chunk")
            yield chunk


def _capture_base(capture_id: str) -> str:
    if len(capture_id) != 32 or any(ch not in CAPTURE_ID_CHARS for ch in capture_id):
        raise TreeCustodyV3Error("capture_id_invalid")
    return f"{BASE}/{capture_id}"


def _archive_path(base: str, value: object) -> str:
    path = _relative(value)
    if not path.startswith(f"{base}/"):
        raise TreeCustodyV3Error("archive_reference_outside_capture")
    return path


def _read_json(
    writer: DescriptorWriter, path: str, limit: int, label: str
) -> tuple[dict[str, Any], bytes]:
    try:
        raw = writer.read_bytes(path, max_bytes=limit)
        value = json.loads(raw)
    except (OSError, StorageError) as exc:
        raise TreeCustodyV3Error(f"{label}_missing") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TreeCustodyV3Error(f"{label}_invalid") from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise TreeCustodyV3Error(f"{label}_noncanonical")
    return value, raw


def _manifest(
    writer: DescriptorWriter, capture_id: str, expected_hash: str | None = None
) -> tuple[dict[str, Any], bytes]:
    raw = writer.read_bytes(
        f"{_capture_base(capture_id)}/MANIFEST.json", max_bytes=MAX_MANIFEST_BYTES
    )
    if expected_hash is not None and sha256(raw) != expected_hash:
        raise TreeCustodyV3Error("manifest_hash_mismatch")
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TreeCustodyV3Error("manifest_invalid") from exc
    if not isinstance(manifest, dict) or canonical_json(manifest) != raw:
        raise TreeCustodyV3Error("manifest_noncanonical")
    return manifest, raw


def _verify_capture_objects(
    writer: DescriptorWriter,
    capture_id: str,
    manifest: dict[str, Any],
    *,
    expected_policy_sha256: str | None,
    require_complete: bool = True,
) -> dict[str, Any]:
    base = _capture_base(capture_id)
    policy_raw = writer.read_bytes(f"{base}/{POLICY_NAME}", max_bytes=MAX_PAGE_BYTES)
    if sha256(policy_raw) != manifest.get("policy_sha256"):
        raise TreeCustodyV3Error("policy_hash_mismatch")
    if expected_policy_sha256 is not None and sha256(policy_raw) != expected_policy_sha256:
        raise TreeCustodyV3Error("policy_pin_mismatch")
    try:
        policy_value = json.loads(policy_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TreeCustodyV3Error("policy_invalid") from exc
    policy = _validate_policy(policy_value)
    if canonical_json(policy) != policy_raw:
        raise TreeCustodyV3Error("policy_invalid")
    if (
        policy["policy_id"] != manifest.get("policy_id")
        or policy["version"] != manifest.get("policy_version")
        or manifest.get("rule_set_sha256") != RULES_SHA256
    ):
        raise TreeCustodyV3Error("policy_identity_mismatch")

    scope = manifest.get("scope")
    seen_policy_paths: set[str] = set()
    seen_symlink_policy_paths: set[str] = set()
    symlink_disposition_counts: Counter[str] = Counter()
    included = excluded = source_bytes = unresolved = source_flags_or = external_required = 0
    rebuild_disposable = rebuild_subtrees = 0
    disposable_not_required = disposable_not_required_subtrees = 0
    unknown_subtrees = 0
    counts: Counter[str] = Counter()
    omitted_counts: Counter[str] = Counter()
    last_path: dict[str, str | None] = {"entries": None, "exclusions": None}
    for kind, indexes in (
        ("entries", manifest["entry_indexes"]),
        ("exclusions", manifest["exclusion_indexes"]),
    ):
        if not isinstance(indexes, list):
            raise TreeCustodyV3Error("index_pages_invalid")
        for iref in indexes:
            if (
                not isinstance(iref, dict)
                or set(iref) != {"path", "sha256", "count"}
                or type(iref["count"]) is not int
                or iref["count"] < 0
            ):
                raise TreeCustodyV3Error("index_ref_invalid")
            _archive_path(base, iref["path"])
            v2._strict_hash(iref["sha256"], "index")
        for page_ref in _iter_index_refs(writer, indexes, kind=kind):
            if (
                not isinstance(page_ref, dict)
                or set(page_ref) != {"path", "sha256", "count"}
                or type(page_ref["count"]) is not int
            ):
                raise TreeCustodyV3Error("page_ref_invalid")
            _archive_path(base, page_ref["path"])
            v2._strict_hash(page_ref["sha256"], "page")
        for item in _iter_records(writer, indexes, kind=kind):
            path = item.get("path")
            if (
                not isinstance(path, str)
                or (path and _relative(path) != path)
                or (last_path[kind] is not None and path <= last_path[kind])
            ):
                raise TreeCustodyV3Error("record_order_invalid")
            last_path[kind] = path
            if kind == "entries":
                included += 1
                entry_kind = item.get("kind")
                source_kind = "file" if entry_kind in {"file", "hardlink"} else entry_kind
                state, rule, disposition = _classify_path(
                    path, source_kind, policy, protected_scope=scope == "protected_opaque"
                )
                if state != "included":
                    raise TreeCustodyV3Error("included_entry_violates_policy")
                if path in policy["pem_candidate_dispositions"]:
                    if rule != "pem_candidate" or disposition != "include_nonsecret":
                        raise TreeCustodyV3Error("policy_pem_candidate_mismatch")
                    seen_policy_paths.add(path)
                flags = item.get("source_flags")
                if type(flags) is not int or flags < 0 or flags & ~v2.SUPPORTED_SOURCE_FLAGS:
                    raise TreeCustodyV3Error("source_flags_invalid")
                source_flags_or |= flags
                omitted_names = item.get("omitted_xattrs", [])
                if not isinstance(omitted_names, list) or any(
                    name not in v2.OMITTED_DARWIN_XATTRS for name in omitted_names
                ):
                    raise TreeCustodyV3Error("omitted_metadata_record_invalid")
                omitted_counts.update(omitted_names)
                if "unresolved_target_exclusion" in item:
                    if item["unresolved_target_exclusion"] is not True:
                        raise TreeCustodyV3Error("symlink_dependency_marker_invalid")
                    unresolved += 1
                symlink_policy = item.get("symlink_target_policy")
                expected_symlink_policy = policy["symlink_target_dispositions"].get(path)
                if symlink_policy is not None or expected_symlink_policy is not None:
                    if (
                        item.get("kind") != "symlink"
                        or symlink_policy != expected_symlink_policy
                        or not isinstance(symlink_policy, dict)
                        or sha256(base64.b64decode(item.get("target_b64", ""), validate=True))
                        != symlink_policy["target_sha256"]
                    ):
                        raise TreeCustodyV3Error("symlink_policy_mismatch")
                    seen_symlink_policy_paths.add(path)
                    symlink_disposition_counts[symlink_policy["disposition"]] += 1
                if item.get("kind") == "file" and item.get("primary_path", path) == path:
                    source_bytes += item["size_bytes"]
                    whole = hashlib.sha256()
                    total = chunks = 0
                    chunk_index_pages = item.get("chunk_index_pages")
                    if not isinstance(chunk_index_pages, list):
                        raise TreeCustodyV3Error("chunk_index_refs_invalid")
                    for cref in chunk_index_pages:
                        if (
                            not isinstance(cref, dict)
                            or set(cref) != {"path", "sha256", "count"}
                            or type(cref["count"]) is not int
                            or cref["count"] < 0
                        ):
                            raise TreeCustodyV3Error("chunk_index_ref_invalid")
                        _archive_path(base, cref["path"])
                        v2._strict_hash(cref["sha256"], "chunk_index")
                    for chunk in _read_chunk_rows(writer, item):
                        _archive_path(base, chunk["path"])
                        try:
                            data = writer.read_bytes(chunk["path"], max_bytes=CHUNK_BYTES)
                        except (OSError, StorageError) as exc:
                            raise TreeCustodyV3Error("chunk_missing") from exc
                        if len(data) != chunk["size_bytes"] or sha256(data) != chunk["sha256"]:
                            raise TreeCustodyV3Error("chunk_hash_mismatch")
                        total += len(data)
                        chunks += 1
                        whole.update(data)
                    if (
                        total != item["size_bytes"]
                        or chunks != item["chunk_count"]
                        or whole.hexdigest() != item["sha256"]
                    ):
                        raise TreeCustodyV3Error("file_content_hash_mismatch")
            else:
                excluded += 1
                entry_kind = item.get("entry_type")
                state, rule, disposition = _classify_path(
                    path, entry_kind, policy, protected_scope=scope == "protected_opaque"
                )
                if item.get("rule") in {
                    "hardlink_alias_to_excluded",
                    "hardlink_group_incomplete",
                }:
                    if not item.get("hardlink_group"):
                        raise TreeCustodyV3Error("hardlink_exclusion_group_missing")
                elif state == "included" or (item.get("rule"), item.get("disposition")) != (
                    rule,
                    disposition,
                ):
                    raise TreeCustodyV3Error("excluded_entry_violates_policy")
                if path in policy["exact_path_exclusions"]:
                    if item.get("rule") != "policy_exact_path":
                        raise TreeCustodyV3Error("policy_exact_exclusion_mismatch")
                    seen_policy_paths.add(path)
                if path in policy["pem_candidate_dispositions"]:
                    expected_disposition = policy["pem_candidate_dispositions"][path]
                    if (
                        item.get("rule") != "pem_candidate"
                        or item.get("disposition") != expected_disposition
                    ):
                        raise TreeCustodyV3Error("policy_pem_candidate_mismatch")
                    seen_policy_paths.add(path)
                if (
                    item.get("exclusion_kind") not in {"file", "subtree"}
                    or not item.get("rule")
                    or not item.get("disposition")
                ):
                    raise TreeCustodyV3Error("exclusion_record_invalid")
                flags = item.get("source_flags")
                if (
                    type(flags) is not int
                    or flags < 0
                    or flags & ~v2.SUPPORTED_SOURCE_FLAGS
                    or item.get("excluded_xattrs_status") != "not_inspected"
                ):
                    raise TreeCustodyV3Error("excluded_metadata_record_invalid")
                counts[item["exclusion_kind"]] += 1
                if item["disposition"] in {
                    "separate_protected_custody",
                    "substitute_online_backup",
                }:
                    external_required += 1
                if item["disposition"] == "rebuild_disposable":
                    rebuild_disposable += 1
                elif item["disposition"] == "disposable_not_required":
                    disposable_not_required += 1
                if str(item["disposition"]).startswith("unresolved"):
                    unresolved += 1
                if (
                    item["exclusion_kind"] == "subtree"
                    and item.get("descendants_status") != "unknown_not_traversed"
                ):
                    raise TreeCustodyV3Error("excluded_descendant_scope_invalid")
                if item["exclusion_kind"] == "subtree":
                    unknown_subtrees += 1
                    if item["disposition"] == "rebuild_disposable":
                        rebuild_subtrees += 1
                    elif item["disposition"] == "disposable_not_required":
                        disposable_not_required_subtrees += 1
    declared = (
        manifest["included_entry_count"],
        manifest["excluded_entry_count"],
        manifest["included_source_bytes"],
        manifest["unresolved_exclusion_count"],
        manifest["external_custody_required_count"],
        manifest["unknown_descendant_subtree_count"],
        manifest["rebuild_disposable_count"],
        manifest["rebuild_disposable_subtree_count"],
        manifest["disposable_not_required_count"],
        manifest["disposable_not_required_subtree_count"],
        manifest["rebuild_input_count"],
        dict(sorted(omitted_counts.items())),
        manifest["source_flags_or"],
        manifest["symlink_disposition_counts"],
    )
    observed = (
        included,
        excluded,
        source_bytes,
        unresolved,
        external_required,
        unknown_subtrees,
        rebuild_disposable,
        rebuild_subtrees,
        disposable_not_required,
        disposable_not_required_subtrees,
        len(policy["rebuild_inputs"]),
        dict(sorted(omitted_counts.items())),
        source_flags_or,
        dict(sorted(symlink_disposition_counts.items())),
    )
    expected_policy_paths = set(policy["exact_path_exclusions"]) | set(
        policy["pem_candidate_dispositions"]
    )
    if seen_policy_paths != expected_policy_paths:
        raise TreeCustodyV3Error("policy_exclusion_membership_mismatch")
    if seen_symlink_policy_paths != set(policy["symlink_target_dispositions"]):
        raise TreeCustodyV3Error("policy_symlink_membership_mismatch")
    if observed != declared:
        raise TreeCustodyV3Error("manifest_membership_count_mismatch")
    return {
        "included_entry_count": included,
        "excluded_entry_count": excluded,
        "included_source_bytes": source_bytes,
        "unresolved_exclusion_count": unresolved,
        "external_custody_required_count": external_required,
        "unknown_descendant_subtree_count": unknown_subtrees,
        "rebuild_disposable_count": rebuild_disposable,
        "rebuild_disposable_subtree_count": rebuild_subtrees,
        "disposable_not_required_count": disposable_not_required,
        "disposable_not_required_subtree_count": disposable_not_required_subtrees,
        "rebuild_input_count": len(policy["rebuild_inputs"]),
        "exclusion_kind_counts": dict(sorted(counts.items())),
        "omitted_metadata_counts": dict(sorted(omitted_counts.items())),
        "symlink_disposition_counts": dict(sorted(symlink_disposition_counts.items())),
    }


def _derive_external_root_targets(
    writer: DescriptorWriter,
    expected: dict[str, dict[str, Any]],
    supplied: dict[str, dict[str, str]],
    original_targets: dict[str, str],
) -> dict[str, dict[str, str]]:
    """Resolve declared Kingston links under the held, verified storage root."""
    if set(supplied) != set(expected):
        raise TreeCustodyV3Error("external_root_binding_set_invalid")
    custody_guard = getattr(writer, "_custody_mutation_guard", None)
    guard = getattr(custody_guard, "guard", None)
    if guard is None:
        raise TreeCustodyV3Error("external_root_binding_storage_guard_required")
    try:
        resolved = guard.revalidate()
        held = os.fstat(writer.root_fd)
        current_fd = v2._open_absolute_directory(resolved.path)
    except (OSError, StorageError) as exc:
        raise TreeCustodyV3Error("external_root_binding_storage_unavailable") from exc
    try:
        current = os.fstat(current_fd)
        if (
            resolved.volume_uuid != guard.volume_uuid
            or resolved.root_id != guard.root_id
            or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise TreeCustodyV3Error("external_root_binding_storage_mismatch")
        root_path = Path(resolved.path)
        targets: dict[str, dict[str, str]] = {}
        for path, declaration in expected.items():
            binding = supplied.get(path)
            pins = {
                key: declaration[key]
                for key in ("volume_uuid", "root_id", "relative_locator", "authority_ref")
            }
            if (
                binding != pins
                or pins["volume_uuid"] != resolved.volume_uuid
                or pins["root_id"] != resolved.root_id
            ):
                raise TreeCustodyV3Error("external_root_binding_mismatch")
            locator = _relative(pins["relative_locator"])
            parts = locator.split("/")
            current_child = os.dup(current_fd)
            try:
                for component in parts[:-1]:
                    _path_component(component)
                    next_fd = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=current_child,
                    )
                    os.close(current_child)
                    current_child = next_fd
                leaf = _path_component(parts[-1])
                stat_value = os.stat(leaf, dir_fd=current_child, follow_symlinks=False)
                if stat.S_ISLNK(stat_value.st_mode) or not (
                    stat.S_ISREG(stat_value.st_mode) or stat.S_ISDIR(stat_value.st_mode)
                ):
                    raise TreeCustodyV3Error("external_root_target_type_invalid")
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if stat.S_ISDIR(stat_value.st_mode):
                    flags |= os.O_DIRECTORY
                target_fd = os.open(leaf, flags, dir_fd=current_child)
                try:
                    opened = os.fstat(target_fd)
                    if (opened.st_dev, opened.st_ino) != (
                        stat_value.st_dev,
                        stat_value.st_ino,
                    ) or opened.st_dev != current.st_dev:
                        raise TreeCustodyV3Error("external_root_target_changed")
                finally:
                    os.close(target_fd)
            except OSError as exc:
                raise TreeCustodyV3Error("external_root_target_unavailable") from exc
            finally:
                os.close(current_child)
            derived = os.fsencode(root_path.joinpath(*parts))
            targets[path] = {
                **pins,
                "derived_target_b64": base64.b64encode(derived).decode("ascii"),
            }
        final = guard.revalidate()
        if (
            final.volume_uuid != resolved.volume_uuid
            or final.root_id != resolved.root_id
            or final.path != resolved.path
            or final.mount_device != resolved.mount_device
            or final.mount_inode != resolved.mount_inode
        ):
            raise TreeCustodyV3Error("external_root_binding_storage_changed")
        if set(original_targets) != set(expected):
            raise TreeCustodyV3Error("external_root_binding_source_link_missing")
        for path, declaration in expected.items():
            if (
                sha256(base64.b64decode(original_targets[path], validate=True))
                != declaration["target_sha256"]
            ):
                raise TreeCustodyV3Error("external_root_binding_source_link_mismatch")
            targets[path]["original_target_b64"] = original_targets[path]
        return targets
    finally:
        os.close(current_fd)


def verify_tree(
    writer: DescriptorWriter, capture_id: str, *, expected_policy_sha256: str | None = None
) -> dict[str, Any]:
    base = _capture_base(capture_id)
    reservation, _ = _read_json(writer, f"{base}/RESERVATION.json", 64 * 1024, "reservation")
    if reservation != {
        "schema": RESERVATION_SCHEMA,
        "capture_id": capture_id,
        "state": "incomplete_until_complete_marker",
    }:
        raise TreeCustodyV3Error("reservation_invalid")
    manifest, manifest_raw = _manifest(writer, capture_id)
    required = {
        "schema",
        "capture_id",
        "scope",
        "root_name",
        "authority_id",
        "recovery_point_id",
        "recovery_point_source",
        "source_root_sha256",
        "source_flags_or",
        "symlink_mode_policy",
        "extended_metadata_policy",
        "excluded_metadata_policy",
        "omitted_metadata_counts",
        "policy_id",
        "policy_version",
        "policy_sha256",
        "rule_set_sha256",
        "capture_start_ns",
        "capture_end_ns",
        "stability_claim",
        "atomicity_scope",
        "root_closure_scope",
        "included_entry_count",
        "excluded_entry_count",
        "included_source_bytes",
        "unresolved_exclusion_count",
        "external_custody_required_count",
        "unknown_descendant_subtree_count",
        "rebuild_disposable_count",
        "rebuild_disposable_subtree_count",
        "disposable_not_required_count",
        "disposable_not_required_subtree_count",
        "rebuild_input_count",
        "symlink_disposition_counts",
        "entry_indexes",
        "exclusion_indexes",
    }
    if (
        set(manifest) != required
        or manifest.get("schema") != SCHEMA
        or manifest.get("capture_id") != capture_id
    ):
        raise TreeCustodyV3Error("manifest_contract_invalid")
    if (
        manifest["scope"] not in {"non_protected", "protected_opaque"}
        or manifest["recovery_point_source"] != "caller_asserted"
    ):
        raise TreeCustodyV3Error("manifest_scope_invalid")
    if (
        manifest["stability_claim"],
        manifest["atomicity_scope"],
        manifest["root_closure_scope"],
    ) != (STABILITY_CLAIM, ATOMICITY_SCOPE, ROOT_CLOSURE_SCOPE):
        raise TreeCustodyV3Error("consistency_scope_invalid")
    if manifest["extended_metadata_policy"] != v2.EXTENDED_METADATA_POLICY:
        raise TreeCustodyV3Error("extended_metadata_policy_invalid")
    if manifest["excluded_metadata_policy"] != EXCLUDED_METADATA_POLICY:
        raise TreeCustodyV3Error("excluded_metadata_policy_invalid")
    if (
        type(manifest["source_flags_or"]) is not int
        or manifest["source_flags_or"] < 0
        or manifest["source_flags_or"] & ~v2.SUPPORTED_SOURCE_FLAGS
    ):
        raise TreeCustodyV3Error("source_flags_invalid")
    if manifest["symlink_mode_policy"] != SYMLINK_MODE_POLICY:
        raise TreeCustodyV3Error("symlink_mode_policy_invalid")
    if (
        type(manifest["capture_start_ns"]) is not int
        or type(manifest["capture_end_ns"]) is not int
        or manifest["capture_end_ns"] < manifest["capture_start_ns"]
    ):
        raise TreeCustodyV3Error("capture_interval_invalid")
    for key in (
        "included_entry_count",
        "excluded_entry_count",
        "included_source_bytes",
        "unresolved_exclusion_count",
        "external_custody_required_count",
        "unknown_descendant_subtree_count",
        "rebuild_disposable_count",
        "rebuild_disposable_subtree_count",
        "disposable_not_required_count",
        "disposable_not_required_subtree_count",
        "rebuild_input_count",
    ):
        if type(manifest[key]) is not int or manifest[key] < 0:
            raise TreeCustodyV3Error("manifest_count_invalid")
    if not isinstance(manifest["symlink_disposition_counts"], dict) or any(
        key
        not in {
            "preserve_dangling_internal",
            "preserve_intra_root_absolute",
            "external_root_binding",
        }
        or type(value) is not int
        or value < 0
        for key, value in manifest["symlink_disposition_counts"].items()
    ):
        raise TreeCustodyV3Error("manifest_symlink_counts_invalid")
    complete, _ = _read_json(writer, f"{base}/COMPLETE.json", 64 * 1024, "complete")
    if (
        set(complete)
        != {
            "schema",
            "capture_id",
            "manifest_sha256",
            "capture_start_ns",
            "capture_end_ns",
            "complete_ns",
        }
        or complete.get("schema") != COMPLETE_SCHEMA
        or complete.get("capture_id") != capture_id
        or complete.get("manifest_sha256") != sha256(manifest_raw)
        or complete.get("capture_start_ns") != manifest["capture_start_ns"]
        or complete.get("capture_end_ns") != manifest["capture_end_ns"]
        or type(complete.get("complete_ns")) is not int
        or complete["complete_ns"] < manifest["capture_end_ns"]
    ):
        raise TreeCustodyV3Error("complete_marker_invalid")
    aggregates = _verify_capture_objects(
        writer, capture_id, manifest, expected_policy_sha256=expected_policy_sha256
    )
    return {
        "schema": SCHEMA,
        "capture_id": capture_id,
        "scope": manifest["scope"],
        **aggregates,
        "policy_sha256": manifest["policy_sha256"],
        "manifest_sha256": sha256(manifest_raw),
        "capture_start_ns": manifest["capture_start_ns"],
        "capture_end_ns": manifest["capture_end_ns"],
        "complete_ns": complete["complete_ns"],
        "complete": True,
        "full_recovery_ready": aggregates["unresolved_exclusion_count"] == 0
        and aggregates["external_custody_required_count"] == 0
        and aggregates["unknown_descendant_subtree_count"]
        == (
            aggregates["rebuild_disposable_subtree_count"]
            + aggregates["disposable_not_required_subtree_count"]
        ),
        "stability_claim": STABILITY_CLAIM,
        "atomicity_scope": ATOMICITY_SCOPE,
        "omitted_metadata_reason": "darwin_host_metadata_policy",
    }


def capture_tree(
    writer: DescriptorWriter,
    root: v2.TreeRoot,
    policy_path: Path,
    *,
    capture_id: str | None = None,
    reserve_capacity: Callable[[int], None] | None = None,
    revalidate_before_publish: Callable[[], None] | None = None,
) -> dict[str, Any]:
    v2._validate_root(root)
    policy, policy_raw, policy_hash = _load_policy(policy_path)
    chosen = capture_id or secrets.token_hex(16)
    base = _capture_base(chosen)
    start_ns = time.time_ns()
    source_fd = v2._open_absolute_directory(root.path)
    root_stat = os.fstat(source_fd)
    root_sig = v2._sig(root_stat)
    with tempfile.TemporaryDirectory(prefix="brain-tree-v3-") as tmp:
        db = _db_open(Path(tmp) / "before.sqlite")
        try:
            _inventory(
                source_fd,
                root_stat.st_dev,
                db,
                policy,
                protected_scope=root.scope == "protected_opaque",
            )
            _resolve_hardlinks(db)
            _mark_excluded_symlink_targets(db, root.path, policy)
            included_count = db.execute(
                "SELECT count(*) FROM entries WHERE state='included'"
            ).fetchone()[0]
            excluded_count = db.execute(
                "SELECT count(*) FROM entries WHERE state!='included'"
            ).fetchone()[0]
            source_bytes = db.execute(
                "SELECT coalesce(sum(size),0) FROM entries WHERE state='included' "
                "AND kind='file' AND (primary_path IS NULL OR primary_path=path)"
            ).fetchone()[0]
            if reserve_capacity is not None:
                reserve_capacity(
                    source_bytes + (included_count + excluded_count) * 2048 + 1_048_576
                )
            writer.mkdir(BASE)
            with writer.lock("host-recovery/.tree-custody-v3.lock"):
                try:
                    writer.list_names(base)
                except FileNotFoundError:
                    pass
                else:
                    raise TreeCustodyV3Error("capture_id_collision")
                writer.mkdir(base)
                writer.create(
                    f"{base}/RESERVATION.json",
                    canonical_json(
                        {
                            "schema": RESERVATION_SCHEMA,
                            "capture_id": chosen,
                            "state": "incomplete_until_complete_marker",
                        }
                    ),
                )
                writer.create(f"{base}/{POLICY_NAME}", policy_raw)
                query = (
                    "SELECT path,kind,mode,uid,gid,size,mtime,ctime,dev,ino,nlink,sig,link_b64,"
                    "group_id,primary_path,digest,chunk_count,chunk_pages FROM entries "
                    "WHERE state='included' AND kind='file' "
                    "AND (primary_path IS NULL OR primary_path=path) ORDER BY path COLLATE BINARY"
                )
                for row in db.execute(query):
                    v2._chunk_file(
                        writer,
                        base,
                        hashlib.sha256(row[0].encode()).hexdigest(),
                        source_fd,
                        db,
                        row,
                    )
                entry_refs = _emit_pages(
                    writer,
                    base,
                    db,
                    included=True,
                    protected_scope=root.scope == "protected_opaque",
                )
                exclusion_refs = _emit_pages(
                    writer,
                    base,
                    db,
                    included=False,
                    protected_scope=root.scope == "protected_opaque",
                )
                entry_indexes = _emit_indexes(writer, base, entry_refs, kind="entries")
                exclusion_indexes = _emit_indexes(writer, base, exclusion_refs, kind="exclusions")
                after = _db_open(Path(tmp) / "after.sqlite")
                try:
                    _inventory(
                        source_fd,
                        root_stat.st_dev,
                        after,
                        policy,
                        protected_scope=root.scope == "protected_opaque",
                    )
                    _resolve_hardlinks(after)
                    _mark_excluded_symlink_targets(after, root.path, policy)
                    before_count = db.execute("SELECT count(*) FROM entries").fetchone()[0]
                    after_count = after.execute("SELECT count(*) FROM entries").fetchone()[0]
                    if before_count != after_count:
                        raise TreeCustodyV3Error("source_membership_drift")
                    before_rows = db.execute(
                        "SELECT path,kind,sig,link_b64,state,rule,disposition,group_id,"
                        "primary_path,omitted_xattrs,symlink_target_unresolved,symlink_policy "
                        "FROM entries "
                        "ORDER BY path COLLATE BINARY"
                    )
                    after_rows = after.execute(
                        "SELECT path,kind,sig,link_b64,state,rule,disposition,group_id,"
                        "primary_path,omitted_xattrs,symlink_target_unresolved,symlink_policy "
                        "FROM entries "
                        "ORDER BY path COLLATE BINARY"
                    )
                    for before, current in zip(before_rows, after_rows, strict=True):
                        if before != current:
                            raise TreeCustodyV3Error("source_member_signature_drift")
                finally:
                    after.close()
                policy_now, raw_now, hash_now = _load_policy(policy_path)
                if raw_now != policy_raw or hash_now != policy_hash or policy_now != policy:
                    raise TreeCustodyV3Error("policy_changed_during_capture")
                if v2._sig(os.fstat(source_fd)) != root_sig:
                    raise TreeCustodyV3Error("source_root_changed")
                path_fd = v2._open_absolute_directory(root.path)
                try:
                    if v2._sig(os.fstat(path_fd)) != root_sig:
                        raise TreeCustodyV3Error("source_root_replaced")
                finally:
                    os.close(path_fd)
                end_ns = time.time_ns()
                unresolved = db.execute(
                    "SELECT count(*) FROM entries WHERE state!='included' "
                    "AND disposition LIKE 'unresolved%'"
                ).fetchone()[0]
                unresolved += db.execute(
                    "SELECT count(*) FROM entries WHERE symlink_target_unresolved=1"
                ).fetchone()[0]
                symlink_disposition_counts = dict(
                    sorted(
                        Counter(
                            json.loads(raw)["disposition"]
                            for (raw,) in db.execute(
                                "SELECT symlink_policy FROM entries "
                                "WHERE symlink_policy IS NOT NULL"
                            )
                        ).items()
                    )
                )
                external_required = db.execute(
                    "SELECT count(*) FROM entries WHERE state!='included' "
                    "AND disposition IN ('separate_protected_custody','substitute_online_backup')"
                ).fetchone()[0]
                unknown_subtrees = db.execute(
                    "SELECT count(*) FROM entries WHERE state='excluded_subtree'"
                ).fetchone()[0]
                rebuild_disposable = db.execute(
                    "SELECT count(*) FROM entries WHERE state!='included' "
                    "AND disposition='rebuild_disposable'"
                ).fetchone()[0]
                rebuild_subtrees = db.execute(
                    "SELECT count(*) FROM entries WHERE state='excluded_subtree' "
                    "AND disposition='rebuild_disposable'"
                ).fetchone()[0]
                disposable_not_required = db.execute(
                    "SELECT count(*) FROM entries WHERE state!='included' "
                    "AND disposition='disposable_not_required'"
                ).fetchone()[0]
                disposable_not_required_subtrees = db.execute(
                    "SELECT count(*) FROM entries WHERE state='excluded_subtree' "
                    "AND disposition='disposable_not_required'"
                ).fetchone()[0]
                source_flags_or = 0
                for (sig_blob,) in db.execute("SELECT sig FROM entries WHERE state='included'"):
                    source_flags_or |= json.loads(sig_blob)[-1]
                manifest = {
                    "schema": SCHEMA,
                    "capture_id": chosen,
                    "scope": root.scope,
                    "root_name": root.name,
                    "authority_id": root.authority_id,
                    "recovery_point_id": root.recovery_point_id,
                    "recovery_point_source": "caller_asserted",
                    "source_root_sha256": hashlib.sha256(os.fsencode(root.path)).hexdigest(),
                    "source_flags_or": source_flags_or,
                    "symlink_mode_policy": SYMLINK_MODE_POLICY,
                    "extended_metadata_policy": v2.EXTENDED_METADATA_POLICY,
                    "excluded_metadata_policy": EXCLUDED_METADATA_POLICY,
                    "omitted_metadata_counts": dict(
                        sorted(
                            Counter(
                                name
                                for (raw_names,) in db.execute(
                                    "SELECT omitted_xattrs FROM entries WHERE state='included'"
                                )
                                for name in json.loads(raw_names)
                            ).items()
                        )
                    ),
                    "policy_id": policy["policy_id"],
                    "policy_version": policy["version"],
                    "policy_sha256": policy_hash,
                    "rule_set_sha256": RULES_SHA256,
                    "capture_start_ns": start_ns,
                    "capture_end_ns": end_ns,
                    "stability_claim": STABILITY_CLAIM,
                    "atomicity_scope": ATOMICITY_SCOPE,
                    "root_closure_scope": ROOT_CLOSURE_SCOPE,
                    "included_entry_count": included_count,
                    "excluded_entry_count": excluded_count,
                    "included_source_bytes": source_bytes,
                    "unresolved_exclusion_count": unresolved,
                    "external_custody_required_count": external_required,
                    "unknown_descendant_subtree_count": unknown_subtrees,
                    "rebuild_disposable_count": rebuild_disposable,
                    "rebuild_disposable_subtree_count": rebuild_subtrees,
                    "disposable_not_required_count": disposable_not_required,
                    "disposable_not_required_subtree_count": disposable_not_required_subtrees,
                    "rebuild_input_count": len(policy["rebuild_inputs"]),
                    "symlink_disposition_counts": symlink_disposition_counts,
                    "entry_indexes": entry_indexes,
                    "exclusion_indexes": exclusion_indexes,
                }
                manifest_raw = canonical_json(manifest)
                if len(manifest_raw) > MAX_MANIFEST_BYTES:
                    raise TreeCustodyV3Error("manifest_size_limit")
                if revalidate_before_publish is not None:
                    revalidate_before_publish()
                writer.create(f"{base}/MANIFEST.json", manifest_raw)
                _verify_capture_objects(
                    writer,
                    chosen,
                    manifest,
                    expected_policy_sha256=policy_hash,
                    require_complete=False,
                )
                if revalidate_before_publish is not None:
                    revalidate_before_publish()
                complete_ns = time.time_ns()
                writer.create(
                    f"{base}/COMPLETE.json",
                    complete_raw := canonical_json(
                        {
                            "schema": COMPLETE_SCHEMA,
                            "capture_id": chosen,
                            "manifest_sha256": sha256(manifest_raw),
                            "capture_start_ns": start_ns,
                            "capture_end_ns": end_ns,
                            "complete_ns": complete_ns,
                        }
                    ),
                )
                if writer.read_bytes(f"{base}/COMPLETE.json", max_bytes=64 * 1024) != complete_raw:
                    raise TreeCustodyV3Error("complete_marker_readback_mismatch")
                return {
                    "schema": SCHEMA,
                    "capture_id": chosen,
                    "scope": root.scope,
                    "included_entry_count": included_count,
                    "excluded_entry_count": excluded_count,
                    "included_source_bytes": source_bytes,
                    "unresolved_exclusion_count": unresolved,
                    "external_custody_required_count": external_required,
                    "unknown_descendant_subtree_count": unknown_subtrees,
                    "rebuild_disposable_count": rebuild_disposable,
                    "rebuild_disposable_subtree_count": rebuild_subtrees,
                    "disposable_not_required_count": disposable_not_required,
                    "disposable_not_required_subtree_count": disposable_not_required_subtrees,
                    "rebuild_input_count": len(policy["rebuild_inputs"]),
                    "symlink_disposition_counts": symlink_disposition_counts,
                    "omitted_metadata_counts": manifest["omitted_metadata_counts"],
                    "omitted_metadata_reason": "darwin_host_metadata_policy",
                    "policy_sha256": policy_hash,
                    "manifest_sha256": sha256(manifest_raw),
                    "capture_start_ns": start_ns,
                    "capture_end_ns": end_ns,
                    "complete_ns": complete_ns,
                    "complete": True,
                    "full_recovery_ready": unresolved == 0
                    and external_required == 0
                    and unknown_subtrees == rebuild_subtrees + disposable_not_required_subtrees,
                }
        finally:
            db.close()
            os.close(source_fd)


def extract_tree(
    writer: DescriptorWriter,
    capture_id: str,
    destination: Path,
    *,
    target_uid: int | None = None,
    target_gid: int | None = None,
    expected_policy_sha256: str | None = None,
    verified_external_root_bindings: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    verified = verify_tree(writer, capture_id, expected_policy_sha256=expected_policy_sha256)
    manifest, manifest_raw = _manifest(writer, capture_id, verified["manifest_sha256"])
    policy_raw = writer.read_bytes(
        f"{_capture_base(capture_id)}/{POLICY_NAME}", max_bytes=MAX_PAGE_BYTES
    )
    if sha256(policy_raw) != manifest["policy_sha256"]:
        raise TreeCustodyV3Error("policy_hash_mismatch")
    policy = _validate_policy(json.loads(policy_raw))
    provided_bindings = verified_external_root_bindings or {}
    if not isinstance(provided_bindings, dict):
        raise TreeCustodyV3Error("external_root_binding_invalid")
    expected_external = {
        path: declaration
        for path, declaration in policy["symlink_target_dispositions"].items()
        if declaration["disposition"] == "external_root_binding"
    }
    original_targets = {
        item["path"]: item["target_b64"]
        for item in _iter_records(writer, manifest["entry_indexes"], kind="entries")
        if item["path"] in expected_external
    }
    external_targets = (
        _derive_external_root_targets(
            writer, expected_external, provided_bindings, original_targets
        )
        if expected_external
        else {}
    )
    if not expected_external and provided_bindings:
        raise TreeCustodyV3Error("external_root_binding_set_invalid")
    external_bindings_verified = len(external_targets)
    uid = os.geteuid() if target_uid is None else target_uid
    gid = os.getegid() if target_gid is None else target_gid
    if type(uid) is not int or uid < 0 or type(gid) is not int or gid < 0:
        raise TreeCustodyV3Error("restore_owner_mapping_invalid")
    if manifest["scope"] == "protected_opaque":
        raise TreeCustodyV3Error("protected_restore_requires_separate_authority")
    if manifest.get("source_flags_or", 0):
        raise TreeCustodyV3Error("restore_source_flags_unmapped")

    def entries():
        yield from _iter_records(writer, manifest["entry_indexes"], kind="entries")

    with v2._destination_parent(destination) as (parent_fd, target_name):
        os.mkdir(target_name, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        target_fd = os.open(
            target_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
        )
        try:
            root_fd = v2._ensure_dirs(target_fd, manifest["root_name"])
            os.close(root_fd)
            root_fd = os.open(
                manifest["root_name"],
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=target_fd,
            )
            for item in entries():
                path = item["path"]
                kind = item["kind"]
                if path == "":
                    continue
                parent_path, _, leaf = path.rpartition("/")
                pfd = v2._open_rel_dir(root_fd, parent_path) if parent_path else os.dup(root_fd)
                try:
                    if kind == "directory":
                        os.mkdir(leaf, mode=0o700, dir_fd=pfd)
                        cfd = os.open(
                            leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd
                        )
                        try:
                            os.fsync(cfd)
                        finally:
                            os.close(cfd)
                        os.fsync(pfd)
                    elif kind == "symlink":
                        target_b64 = external_targets.get(path, {}).get(
                            "derived_target_b64", item["target_b64"]
                        )
                        target = base64.b64decode(target_b64, validate=True)
                        os.symlink(target, leaf, dir_fd=pfd)
                        if (
                            os.chown not in os.supports_dir_fd
                            or os.chown not in os.supports_follow_symlinks
                        ):
                            raise TreeCustodyV3Error("restore_symlink_owner_unsupported")
                        if (
                            os.utime not in os.supports_dir_fd
                            or os.utime not in os.supports_follow_symlinks
                        ):
                            raise TreeCustodyV3Error("restore_symlink_time_unsupported")
                        os.chown(leaf, uid, gid, dir_fd=pfd, follow_symlinks=False)
                        os.utime(
                            leaf,
                            ns=(item["mtime_ns"], item["mtime_ns"]),
                            dir_fd=pfd,
                            follow_symlinks=False,
                        )
                        if item["source_flags"]:
                            chflags = getattr(os, "chflags", None)
                            if (
                                chflags is None
                                or chflags not in os.supports_dir_fd
                                or chflags not in os.supports_follow_symlinks
                            ):
                                raise TreeCustodyV3Error("restore_symlink_flags_unsupported")
                            chflags(
                                leaf,
                                item["source_flags"],
                                dir_fd=pfd,
                                follow_symlinks=False,
                            )
                    elif kind == "hardlink":
                        source = item.get("link_to")
                        if not isinstance(source, str) or source >= path:
                            raise TreeCustodyV3Error("restore_hardlink_target_invalid")
                        src_parent, _, src_leaf = source.rpartition("/")
                        sfd = (
                            v2._open_rel_dir(root_fd, src_parent) if src_parent else os.dup(root_fd)
                        )
                        try:
                            os.link(
                                src_leaf,
                                leaf,
                                src_dir_fd=sfd,
                                dst_dir_fd=pfd,
                                follow_symlinks=False,
                            )
                        finally:
                            os.close(sfd)
                    elif kind == "file":
                        fd = os.open(
                            leaf,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=pfd,
                        )
                        full = hashlib.sha256()
                        total = 0
                        try:
                            for chunk in _read_chunk_rows(writer, item):
                                data = writer.read_bytes(chunk["path"], max_bytes=CHUNK_BYTES)
                                if (
                                    len(data) != chunk["size_bytes"]
                                    or sha256(data) != chunk["sha256"]
                                ):
                                    raise TreeCustodyV3Error("chunk_hash_mismatch")
                                v2._write_all_fd(fd, data)
                                total += len(data)
                                full.update(data)
                            if total != item["size_bytes"] or full.hexdigest() != item["sha256"]:
                                raise TreeCustodyV3Error("file_content_hash_mismatch")
                            v2._apply_fd_metadata(fd, item, uid, gid)
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                        os.fsync(pfd)
                    else:
                        raise TreeCustodyV3Error("restore_entry_kind_invalid")
                finally:
                    os.close(pfd)
            # Apply directory metadata post-order, then reread all restored
            # bytes, links, topology, and mapped ownership before the marker.
            for item in _iter_records(
                writer, manifest["entry_indexes"], kind="entries", reverse=True
            ):
                if item["kind"] != "directory":
                    continue
                dfd = v2._open_rel_dir(root_fd, item["path"]) if item["path"] else os.dup(root_fd)
                try:
                    v2._apply_fd_metadata(dfd, item, uid, gid)
                    os.fsync(dfd)
                finally:
                    os.close(dfd)
            for item in entries():
                path = item["path"]
                kind = item["kind"]
                if kind == "directory":
                    dfd = v2._open_rel_dir(root_fd, path) if path else os.dup(root_fd)
                    try:
                        st = os.fstat(dfd)
                    finally:
                        os.close(dfd)
                else:
                    parent_path, _, leaf = path.rpartition("/")
                    pfd = v2._open_rel_dir(root_fd, parent_path) if parent_path else os.dup(root_fd)
                    try:
                        st = os.stat(leaf, dir_fd=pfd, follow_symlinks=False)
                        if kind == "symlink":
                            actual_target = os.readlink(os.fsencode(leaf), dir_fd=pfd)
                            actual_bytes = (
                                actual_target
                                if isinstance(actual_target, bytes)
                                else os.fsencode(actual_target)
                            )
                            target_b64 = external_targets.get(path, {}).get(
                                "derived_target_b64", item["target_b64"]
                            )
                            if actual_bytes != base64.b64decode(target_b64, validate=True):
                                raise TreeCustodyV3Error("restore_symlink_target_mismatch")
                        elif kind == "hardlink":
                            target_path = item.get("link_to")
                            if not isinstance(target_path, str):
                                raise TreeCustodyV3Error("restore_hardlink_target_invalid")
                            target_parent, _, target_leaf = target_path.rpartition("/")
                            tfd = (
                                v2._open_rel_dir(root_fd, target_parent)
                                if target_parent
                                else os.dup(root_fd)
                            )
                            try:
                                target_stat = os.stat(
                                    target_leaf, dir_fd=tfd, follow_symlinks=False
                                )
                            finally:
                                os.close(tfd)
                            if (st.st_dev, st.st_ino) != (target_stat.st_dev, target_stat.st_ino):
                                raise TreeCustodyV3Error("restore_hardlink_topology_mismatch")
                        elif kind == "file" and item.get("primary_path", path) == path:
                            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pfd)
                            try:
                                before = os.fstat(fd)
                                if (before.st_dev, before.st_ino) != (st.st_dev, st.st_ino):
                                    raise TreeCustodyV3Error("restore_destination_changed")
                                digest = hashlib.sha256()
                                size = 0
                                while data := os.read(fd, CHUNK_BYTES):
                                    digest.update(data)
                                    size += len(data)
                                after = os.fstat(fd)
                                if v2._sig(before) != v2._sig(after):
                                    raise TreeCustodyV3Error("restore_destination_changed")
                                st = after
                                if (
                                    size != item["size_bytes"]
                                    or digest.hexdigest() != item["sha256"]
                                ):
                                    raise TreeCustodyV3Error("restore_destination_hash_mismatch")
                            finally:
                                os.close(fd)
                    finally:
                        if kind != "directory":
                            os.close(pfd)
                if (
                    st.st_uid != uid
                    or st.st_gid != gid
                    or (kind != "symlink" and stat.S_IMODE(st.st_mode) != item["mode"])
                    or st.st_mtime_ns != item["mtime_ns"]
                    or getattr(st, "st_flags", 0) != item["source_flags"]
                ):
                    raise TreeCustodyV3Error("restore_metadata_mismatch")
            os.fsync(root_fd)
            receipt = canonical_json(
                {
                    "schema": RESTORE_SCHEMA,
                    "capture_id": capture_id,
                    "manifest_sha256": sha256(manifest_raw),
                    "policy_sha256": manifest["policy_sha256"],
                    "excluded_paths_materialized": False,
                    "target_uid": uid,
                    "target_gid": gid,
                    "entry_count": verified["included_entry_count"],
                    "excluded_entry_count": verified["excluded_entry_count"],
                    "symlink_topology_preserved": True,
                    "external_root_bindings_verified_count": external_bindings_verified,
                    "external_root_mappings": [
                        {"path": path, **mapping}
                        for path, mapping in sorted(external_targets.items())
                    ],
                }
            )
            rfd = os.open(
                "RESTORE_COMPLETE.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=target_fd,
            )
            try:
                v2._write_all_fd(rfd, receipt)
                os.fsync(rfd)
            finally:
                os.close(rfd)
            os.fsync(target_fd)
            os.fsync(parent_fd)
        finally:
            os.close(root_fd)
            os.close(target_fd)
    return {
        "schema": RESTORE_SCHEMA,
        "capture_id": capture_id,
        "complete": True,
        "included_entry_count": verified["included_entry_count"],
        "excluded_entry_count": verified["excluded_entry_count"],
        "excluded_paths_materialized": False,
        "symlink_topology_preserved": True,
        "external_root_bindings_verified_count": external_bindings_verified,
        "external_root_remappings_count": external_bindings_verified,
    }


def _load_root(path: Path, scope: str) -> v2.TreeRoot:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TreeCustodyV3Error("root_declaration_invalid") from exc
    if not isinstance(value, dict) or set(value) != {
        "name",
        "authority_id",
        "recovery_point_id",
        "path",
    }:
        raise TreeCustodyV3Error("root_declaration_invalid")
    try:
        return v2.TreeRoot(
            value["name"],
            value["authority_id"],
            value["recovery_point_id"],
            Path(value["path"]),
            scope,
        )
    except (TypeError, ValueError) as exc:
        raise TreeCustodyV3Error("root_declaration_invalid") from exc


def _load_external_bindings(path: Path | None) -> dict[str, dict[str, str]] | None:
    if path is None:
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
            raise TreeCustodyV3Error("external_root_bindings_file_invalid")
        chunks = []
        total = 0
        while block := os.read(fd, min(65536, 1024 * 1024 + 1 - total)):
            chunks.append(block)
            total += len(block)
            if total > 1024 * 1024:
                raise TreeCustodyV3Error("external_root_bindings_file_invalid")
        after = os.fstat(fd)
        if v2._sig(before) != v2._sig(after) or total != before.st_size:
            raise TreeCustodyV3Error("external_root_bindings_file_changed")
        raw = b"".join(chunks)
        value = json.loads(raw)
        if not isinstance(value, dict) or canonical_json(value) != raw:
            raise TreeCustodyV3Error("external_root_bindings_file_invalid")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TreeCustodyV3Error("external_root_bindings_file_invalid") from exc
    finally:
        os.close(fd)
    for relative, binding in value.items():
        _relative(relative)
        if (
            not isinstance(binding, dict)
            or set(binding)
            != {
                "volume_uuid",
                "root_id",
                "relative_locator",
                "authority_ref",
            }
            or any(not isinstance(item, str) or not item for item in binding.values())
        ):
            raise TreeCustodyV3Error("external_root_bindings_file_invalid")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("--storage-config", type=Path, required=True)
    capture.add_argument("--root", type=Path, required=True)
    capture.add_argument("--scope", choices=("non_protected", "protected_opaque"), required=True)
    capture.add_argument("--policy", type=Path, required=True)
    capture.add_argument("--capture-id")
    verify = sub.add_parser("verify")
    verify.add_argument("--storage-config", type=Path, required=True)
    verify.add_argument("--capture-id", required=True)
    verify.add_argument("--expected-policy-sha256")
    extract = sub.add_parser("extract")
    extract.add_argument("--storage-config", type=Path, required=True)
    extract.add_argument("--capture-id", required=True)
    extract.add_argument("--destination", type=Path, required=True)
    extract.add_argument("--target-uid", type=int)
    extract.add_argument("--target-gid", type=int)
    extract.add_argument("--expected-policy-sha256")
    extract.add_argument("--external-bindings-json", type=Path)
    args = parser.parse_args(argv)
    try:
        with v2._storage_writer(args.storage_config) as writer:
            if args.command == "capture":
                guard = getattr(writer, "_custody_mutation_guard", None)
                result = capture_tree(
                    writer,
                    _load_root(args.root, args.scope),
                    args.policy,
                    capture_id=args.capture_id,
                    reserve_capacity=guard.reserve_capacity if guard is not None else None,
                    revalidate_before_publish=guard.full_revalidate if guard is not None else None,
                )
            elif args.command == "verify":
                result = verify_tree(
                    writer, args.capture_id, expected_policy_sha256=args.expected_policy_sha256
                )
            else:
                result = extract_tree(
                    writer,
                    args.capture_id,
                    args.destination,
                    target_uid=args.target_uid,
                    target_gid=args.target_gid,
                    expected_policy_sha256=args.expected_policy_sha256,
                    verified_external_root_bindings=_load_external_bindings(
                        args.external_bindings_json
                    ),
                )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        code = (
            str(exc)
            if isinstance(exc, (TreeCustodyV3Error, v2.TreeCustodyError, StorageError))
            else f"io_error_{exc.errno or 0}"
            if isinstance(exc, OSError)
            else "operation_failed"
        )
        print(json.dumps({"error": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
