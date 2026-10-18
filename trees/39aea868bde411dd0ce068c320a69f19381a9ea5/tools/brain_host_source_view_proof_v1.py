#!/usr/bin/env python3
"""Validate a synthetic source-view proof against independently verified artifacts.

This is a contract probe, not an APFS adapter. Synthetic proofs can never qualify a
real recovery point or factory reset. A production verifier must establish the
source-view facts from held descriptors and the OS snapshot API.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from tools import brain_host_recovery_set as recovery_set
from tools import brain_host_tree_custody as tree_v2
from tools import brain_host_tree_custody_v3 as tree_v3
from tools.brain_senses_external_writer_v1 import canonical_json, sha256

SCHEMA = "brain-host-source-view-proof-v1"
REQUIRED_ROLES = {
    "owner_work",
    "research_work",
    "private_config",
    "launchagent_definitions",
    "protected_d43",
    "sqlite_backup",
}
HASH_LENGTH = 64


class SourceViewProofError(ValueError):
    """A source-view proof is incomplete or inconsistent with verified captures."""


def _open_absolute_directory_nofollow(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = next_fd
        result = current
        current = -1
        return result
    finally:
        if current >= 0:
            os.close(current)


def synthetic_root_locator_sha256(
    role: str, volume_uuid: str, root_id: str, root_identity: dict[str, int]
) -> str:
    return sha256(
        canonical_json(
            {
                "schema": "brain-host-synthetic-source-root-binding-v1",
                "role": role,
                "volume_uuid": volume_uuid,
                "root_id": root_id,
                **root_identity,
            }
        )
    )


def verify_synthetic_tree_source_binding(
    writer: Any, member: dict[str, Any], source_root: Path
) -> dict[str, Any]:
    """Compare a synthetic local root through held descriptors with verified v3 pages.

    This fixture reader is a portability contract exercise. Its caller must not
    pass owner/protected data, and it is not an APFS snapshot adapter.
    """
    if member.get("format") != "tree-custody-v3":
        raise SourceViewProofError("synthetic_tree_binding_requires_v3")
    _pin, detail = recovery_set._verify_member(writer, member)
    manifest = detail["manifest"]
    expected_entries = {
        row["path"]: row
        for row in tree_v3._iter_records(writer, manifest["entry_indexes"], kind="entries")
    }
    expected_exclusions = {
        row["path"]: row
        for row in tree_v3._iter_records(writer, manifest["exclusion_indexes"], kind="exclusions")
    }
    expected_paths = set(expected_entries) | set(expected_exclusions)
    observed: dict[str, dict[str, Any]] = {}
    root_fd = _open_absolute_directory_nofollow(source_root)
    root_before = os.fstat(root_fd)
    root_dev = root_before.st_dev

    def metadata(st: os.stat_result, path: str, kind: str, omitted: list[str] | None = None):
        result = {
            "path": path,
            "kind": kind,
            "mode": stat.S_IMODE(st.st_mode),
            "uid": st.st_uid,
            "gid": st.st_gid,
            "size_bytes": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "source_ctime_ns": st.st_ctime_ns,
            "source_device": st.st_dev,
            "source_inode": st.st_ino,
            "source_nlink": st.st_nlink,
            "source_flags": tree_v2._validated_source_flags(st),
        }
        if omitted:
            result["omitted_xattrs"] = omitted
        return result

    def walk(directory_fd: int, prefix: str) -> None:
        with os.scandir(directory_fd) as iterator:
            for entry in sorted(iterator, key=lambda item: item.name):
                name = tree_v3._path_component(entry.name)
                relative = f"{prefix}/{name}" if prefix else name
                st = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if st.st_dev != root_dev:
                    raise SourceViewProofError("synthetic_source_mount_boundary")
                excluded = expected_exclusions.get(relative)
                if excluded is not None:
                    kind = (
                        "directory"
                        if stat.S_ISDIR(st.st_mode)
                        else (
                            "file"
                            if stat.S_ISREG(st.st_mode)
                            else "symlink"
                            if stat.S_ISLNK(st.st_mode)
                            else "special"
                        )
                    )
                    if kind != excluded["entry_type"]:
                        raise SourceViewProofError("synthetic_source_exclusion_type_mismatch")
                    if tree_v2._sig(st) != tree_v2._sig(
                        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    ):
                        raise SourceViewProofError("synthetic_source_exclusion_changed")
                    observed[relative] = {
                        "metadata": metadata(st, relative, kind),
                        "excluded": True,
                    }
                    if excluded["exclusion_kind"] == "subtree":
                        continue
                    if kind == "directory":
                        raise SourceViewProofError("synthetic_source_file_exclusion_is_directory")
                    continue
                if relative not in expected_entries:
                    raise SourceViewProofError("synthetic_source_unexpected_member")
                if stat.S_ISDIR(st.st_mode):
                    kind = "directory"
                    child_fd = os.open(
                        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd
                    )
                    try:
                        child_before = os.fstat(child_fd)
                        if tree_v2._sig(st) != tree_v2._sig(child_before):
                            raise SourceViewProofError("synthetic_source_changed_during_open")
                        omitted = sorted(tree_v2._check_extended_metadata_fd(child_fd))
                        observed[relative] = {"metadata": metadata(st, relative, kind, omitted)}
                        walk(child_fd, relative)
                        if tree_v2._sig(child_before) != tree_v2._sig(os.fstat(child_fd)):
                            raise SourceViewProofError("synthetic_source_directory_changed")
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(st.st_mode):
                    fd = os.open(
                        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
                    )
                    try:
                        before = os.fstat(fd)
                        if tree_v2._sig(st) != tree_v2._sig(before):
                            raise SourceViewProofError("synthetic_source_changed_during_open")
                        omitted = sorted(tree_v2._check_extended_metadata_fd(fd))
                        digest = hashlib.sha256()
                        while block := os.read(fd, 1024 * 1024):
                            digest.update(block)
                        after = os.fstat(fd)
                        if tree_v2._sig(before) != tree_v2._sig(after):
                            raise SourceViewProofError("synthetic_source_file_changed")
                        observed[relative] = {
                            "metadata": metadata(st, relative, "file", omitted),
                            "sha256": digest.hexdigest(),
                        }
                    finally:
                        os.close(fd)
                elif stat.S_ISLNK(st.st_mode):
                    omitted = sorted(tree_v3._xattrs_for_symlink(directory_fd, name))
                    target = os.readlink(os.fsencode(name), dir_fd=directory_fd)
                    target_bytes = target if isinstance(target, bytes) else os.fsencode(target)
                    if tree_v2._sig(st) != tree_v2._sig(
                        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    ):
                        raise SourceViewProofError("synthetic_source_symlink_changed")
                    observed[relative] = {
                        "metadata": metadata(st, relative, "symlink", omitted),
                        "target_b64": base64.b64encode(target_bytes).decode("ascii"),
                    }
                else:
                    raise SourceViewProofError("synthetic_source_special_file_rejected")

    try:
        root_xattrs = sorted(tree_v2._check_extended_metadata_fd(root_fd))
        observed[""] = {"metadata": metadata(root_before, "", "directory", root_xattrs)}
        walk(root_fd, "")
        root_after = os.fstat(root_fd)
        if tree_v2._sig(root_before) != tree_v2._sig(root_after):
            raise SourceViewProofError("synthetic_source_root_changed")
    finally:
        os.close(root_fd)
    if set(observed) != expected_paths:
        raise SourceViewProofError("synthetic_source_membership_mismatch")

    for path, actual in observed.items():
        if actual.get("excluded"):
            expected = expected_exclusions[path]
            if not _metadata_matches(actual["metadata"], expected, excluded=True):
                raise SourceViewProofError("synthetic_source_exclusion_metadata_mismatch")
            continue
        expected = expected_entries[path]
        if not _metadata_matches(actual["metadata"], expected, excluded=False):
            raise SourceViewProofError("synthetic_source_entry_metadata_mismatch")
        if expected["kind"] in {"file", "hardlink"}:
            primary = expected.get("link_to", path)
            digest = expected_entries[primary].get("sha256")
            if actual.get("sha256") != digest:
                raise SourceViewProofError("synthetic_source_file_hash_mismatch")
        if expected["kind"] == "symlink" and actual.get("target_b64") != expected.get("target_b64"):
            raise SourceViewProofError("synthetic_source_symlink_target_mismatch")
    return {
        "role": member["role"],
        "root_identity": {"device": root_before.st_dev, "inode": root_before.st_ino},
        "entry_count": len(expected_entries),
        "exclusion_count": len(expected_exclusions),
        "membership_and_bytes_match": True,
    }


def verify_synthetic_sqlite_source_family_binding(
    writer: Any, member: dict[str, Any], owner_root: Path
) -> dict[str, Any]:
    """Compare a synthetic SQLite DB/WAL family via held directory descriptors.

    This reads only lstat/fstat metadata. It does not open or parse the database,
    and does not establish that the family was captured from an APFS snapshot.
    """
    if member.get("role") != "sqlite_backup" or member.get("format") != "sqlite-online-backup-v1":
        raise SourceViewProofError("synthetic_sqlite_binding_requires_backup_member")
    _pin, detail = recovery_set._verify_member(writer, member)
    receipt = detail["receipt"]
    expected_family = receipt["source_family_after"]
    root_fd = _open_absolute_directory_nofollow(owner_root)
    root_before = os.fstat(root_fd)
    relative_parent, _, leaf = recovery_set.SQLITE_SOURCE_PATH.rpartition("/")
    held_chain: list[tuple[str, int, int, tuple[int, ...], tuple[int, ...]]] = []
    parent_fd = os.dup(root_fd)
    signature_fields = (
        "device",
        "inode",
        "mode",
        "uid",
        "gid",
        "size_bytes",
        "mtime_ns",
        "ctime_ns",
    )
    try:
        for component in relative_parent.split("/"):
            parent_before = os.fstat(parent_fd)
            child_stat = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(child_stat.st_mode):
                raise SourceViewProofError("synthetic_sqlite_parent_type_invalid")
            child_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent_fd,
            )
            child_opened = os.fstat(child_fd)
            child_signature = _source_directory_signature(child_stat)
            parent_signature = _source_directory_signature(parent_before)
            if child_signature != _source_directory_signature(child_opened):
                os.close(child_fd)
                raise SourceViewProofError("synthetic_sqlite_parent_changed_during_open")
            held_chain.append((component, parent_fd, child_fd, child_signature, parent_signature))
            parent_fd = child_fd
        for suffix, expected in expected_family.items():
            name = f"{leaf}{suffix}"
            try:
                before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                observed = None
            else:
                if not stat.S_ISREG(before.st_mode):
                    raise SourceViewProofError("synthetic_sqlite_family_member_type_invalid")
                observed = {
                    "device": before.st_dev,
                    "inode": before.st_ino,
                    "mode": stat.S_IMODE(before.st_mode),
                    "uid": before.st_uid,
                    "gid": before.st_gid,
                    "size_bytes": before.st_size,
                    "mtime_ns": before.st_mtime_ns,
                    "ctime_ns": before.st_ctime_ns,
                }
                after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if any(
                    getattr(before, attr) != getattr(after, attr)
                    for attr in (
                        "st_dev",
                        "st_ino",
                        "st_mode",
                        "st_uid",
                        "st_gid",
                        "st_size",
                        "st_mtime_ns",
                        "st_ctime_ns",
                    )
                ):
                    raise SourceViewProofError("synthetic_sqlite_family_changed")
            if expected is not None and set(expected) != set(signature_fields):
                raise SourceViewProofError("synthetic_sqlite_receipt_family_invalid")
            if observed != expected:
                raise SourceViewProofError("synthetic_sqlite_family_mismatch")
        for component, chain_parent_fd, child_fd, child_signature, parent_signature in held_chain:
            current_name = os.stat(component, dir_fd=chain_parent_fd, follow_symlinks=False)
            if (
                _source_directory_signature(current_name) != child_signature
                or _source_directory_signature(os.fstat(child_fd)) != child_signature
                or _source_directory_signature(os.fstat(chain_parent_fd)) != parent_signature
            ):
                raise SourceViewProofError("synthetic_sqlite_parent_chain_changed")
        if _source_directory_signature(root_before) != _source_directory_signature(
            os.fstat(root_fd)
        ):
            raise SourceViewProofError("synthetic_sqlite_owner_root_changed")
    finally:
        closed_fds: set[int] = set()
        for _component, chain_parent_fd, child_fd, _child_sig, _parent_sig in reversed(held_chain):
            for fd in (child_fd, chain_parent_fd):
                if fd not in closed_fds:
                    os.close(fd)
                    closed_fds.add(fd)
        if not held_chain:
            os.close(parent_fd)
        os.close(root_fd)
    family_sha256 = sha256(
        canonical_json(
            {
                "source_family_before": receipt["source_family_before"],
                "source_family_after": receipt["source_family_after"],
            }
        )
    )
    return {
        "family_sha256": family_sha256,
        "backup_sha256": detail["backup_sha256"],
        "members_verified": len(expected_family),
        "root_identity": {"device": root_before.st_dev, "inode": root_before.st_ino},
        "source_bytes_read": False,
    }


def _source_directory_signature(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        stat.S_IFMT(st.st_mode),
        stat.S_IMODE(st.st_mode),
        st.st_uid,
        st.st_gid,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )


def _metadata_matches(actual: dict[str, Any], expected: dict[str, Any], *, excluded: bool) -> bool:
    expected_kind = expected.get("entry_type" if excluded else "kind")
    if expected_kind == "hardlink":
        expected_kind = "file"
    fields = (
        ("path", "path"),
        ("mode", "mode"),
        ("uid", "uid"),
        ("gid", "gid"),
        ("size_bytes", "size_bytes"),
        ("mtime_ns", "mtime_ns"),
        ("source_ctime_ns", "source_ctime_ns"),
        ("source_device", "source_device"),
        ("source_inode", "source_inode"),
        ("source_nlink", "source_nlink"),
        ("source_flags", "source_flags"),
    )
    return (
        actual.get("kind") == expected_kind
        and all(
            actual.get(actual_key) == expected.get(expected_key)
            for actual_key, expected_key in fields
        )
        and actual.get("omitted_xattrs", []) == expected.get("omitted_xattrs", [])
    )


def capture_inventory_sha256(writer: Any, member: dict[str, Any]) -> str:
    """Hash the page references that define one independently verified capture."""
    _pin, detail = recovery_set._verify_member(writer, member)
    manifest = detail.get("manifest", detail.get("receipt"))
    if member["format"] == "tree-custody-v3":
        inventory = {
            "format": member["format"],
            "entries": manifest["entry_indexes"],
            "exclusions": manifest["exclusion_indexes"],
        }
    elif member["format"] == "tree-custody-v2":
        inventory = {"format": member["format"], "index_pages": manifest["index_pages"]}
    elif member["format"] == "sqlite-online-backup-v1":
        inventory = {
            "format": member["format"],
            "backup_sha256": detail["backup_sha256"],
            "source_family_before": detail["receipt"]["source_family_before"],
            "source_family_after": detail["receipt"]["source_family_after"],
        }
    else:
        inventory = {
            "format": member["format"],
            "manifest_sha256": detail["manifest_sha256"],
        }
    return sha256(canonical_json(inventory))


def capture_closure_counts(writer: Any, member: dict[str, Any]) -> dict[str, int]:
    """Derive inclusion and unresolved counts from a verified member artifact."""
    _pin, detail = recovery_set._verify_member(writer, member)
    if member["format"] == "tree-custody-v3":
        result = detail["verify_result"]
        return {
            "included_count": result["included_entry_count"],
            "excluded_count": result["excluded_entry_count"],
            "unknown_count": (
                result["unresolved_exclusion_count"] + result["unknown_descendant_subtree_count"]
            ),
        }
    if member["format"] == "tree-custody-v2":
        return {
            "included_count": detail["verify_result"]["entry_count"],
            "excluded_count": 0,
            "unknown_count": 0,
        }
    if member["format"] == "sqlite-online-backup-v1":
        return {"included_count": 1, "excluded_count": 0, "unknown_count": 0}
    manifest = detail["manifest"]
    return {"included_count": manifest["entry_count"], "excluded_count": 0, "unknown_count": 0}


def _verify_time_order(times: dict[str, Any]) -> list[int]:
    time_fields = (
        "snapshot_event",
        "view_opened",
        "capture_started",
        "capture_completed",
        "artifact_verification_completed",
        "proof_sealed",
        "view_released",
    )
    ordered = [times[key] for key in time_fields]
    if ordered != sorted(ordered) or len(set(ordered)) != len(ordered):
        raise SourceViewProofError("source_view_time_order_invalid")
    return ordered


def _hash(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != HASH_LENGTH
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise SourceViewProofError(f"{label}_invalid")
    return value


def verify_synthetic_source_view_proof(
    writer: Any,
    proof: dict[str, Any],
    *,
    synthetic_source_roots: dict[str, Path],
    expected_volume_uuid: str,
    expected_root_id: str,
    expected_source_volume_uuid: str,
    expected_source_root_id: str,
    revalidate_identity: Any,
) -> dict[str, Any]:
    """Check a synthetic view and its exact relation to recovery-set member pins.

    The set is verified inside this call, so caller-provided summaries cannot
    stand in for artifact verification. This validator deliberately rejects
    scenario="real" until a held-descriptor APFS verifier exists. It never makes
    an application-recovery or erase-readiness claim.
    """
    fields = {
        "schema",
        "scenario",
        "set_id",
        "set_index_sha256",
        "source_view",
        "times_ns",
        "adapter_sha256",
        "capture_tool_sha256",
        "policy_sha256",
        "closure_ledger",
        "member_bindings",
        "wallets_disposition",
        "metadata_disposition",
    }
    if not isinstance(proof, dict) or set(proof) != fields or proof.get("schema") != SCHEMA:
        raise SourceViewProofError("proof_contract_invalid")
    if proof.get("scenario") != "synthetic":
        raise SourceViewProofError("real_source_view_verifier_unavailable")

    try:
        verified_set = recovery_set.verify_set(
            writer,
            proof.get("set_id"),
            expected_volume_uuid=expected_volume_uuid,
            expected_root_id=expected_root_id,
            revalidate_identity=revalidate_identity,
        )
    except (recovery_set.RecoverySetError, TypeError) as exc:
        raise SourceViewProofError("verified_set_result_required") from exc
    index_raw = writer.read_bytes(
        f"{recovery_set.BASE}/{verified_set['set_id']}/INDEX.json",
        max_bytes=recovery_set.MAX_SPEC_BYTES,
    )
    if sha256(index_raw) != verified_set.get("index_sha256"):
        raise SourceViewProofError("verified_set_index_changed")
    try:
        index = recovery_set._load_json(index_raw, "source_view_set_index")
    except recovery_set.RecoverySetError as exc:
        raise SourceViewProofError("verified_set_index_invalid") from exc
    set_id = verified_set.get("set_id")
    if not isinstance(set_id, str) or proof.get("set_id") != set_id:
        raise SourceViewProofError("set_id_mismatch")
    index_hash = _hash(verified_set.get("index_sha256"), "verified_set_index_hash")
    if proof.get("set_index_sha256") != index_hash:
        raise SourceViewProofError("set_index_hash_mismatch")

    view = proof.get("source_view")
    view_fields = {"kind", "volume_uuid", "root_id", "view_id", "binding_method"}
    if (
        not isinstance(view, dict)
        or set(view) != view_fields
        or view.get("kind") != "synthetic_readonly_view_fixture"
        or view.get("binding_method") != "synthetic_held_descriptor_fixture"
        or view.get("volume_uuid") != expected_source_volume_uuid
        or view.get("root_id") != expected_source_root_id
        or any(not isinstance(view.get(key), str) or not view[key] for key in view_fields)
    ):
        raise SourceViewProofError("source_view_identity_invalid")

    times = proof.get("times_ns")
    time_fields = {
        "snapshot_event",
        "view_opened",
        "capture_started",
        "capture_completed",
        "artifact_verification_completed",
        "proof_sealed",
        "view_released",
    }
    if (
        not isinstance(times, dict)
        or set(times) != time_fields
        or any(type(times[key]) is not int or times[key] <= 0 for key in time_fields)
    ):
        raise SourceViewProofError("source_view_times_invalid")
    _verify_time_order(times)

    for key in ("adapter_sha256", "capture_tool_sha256"):
        _hash(proof.get(key), key)
    policy = proof.get("policy_sha256")
    policy_fields = {"selection", "exclusion", "metadata"}
    if not isinstance(policy, dict) or set(policy) != policy_fields:
        raise SourceViewProofError("source_view_policy_invalid")
    for key in policy_fields:
        _hash(policy[key], f"{key}_policy_hash")

    ledger = proof.get("closure_ledger")
    if not isinstance(ledger, list) or len(ledger) != len(REQUIRED_ROLES):
        raise SourceViewProofError("closure_ledger_incomplete")
    expected_member_by_role = {item.get("role"): item for item in index.get("members", [])}
    if set(expected_member_by_role) != REQUIRED_ROLES:
        raise SourceViewProofError("verified_set_member_roles_invalid")
    tree_roles = {
        role
        for role, member in expected_member_by_role.items()
        if member["format"] == "tree-custody-v3"
    }
    if not isinstance(synthetic_source_roots, dict) or set(synthetic_source_roots) != tree_roles:
        raise SourceViewProofError("synthetic_source_roots_incomplete")
    source_bindings = {
        role: verify_synthetic_tree_source_binding(
            writer, expected_member_by_role[role], synthetic_source_roots[role]
        )
        for role in sorted(tree_roles)
    }
    sqlite_source_family = verify_synthetic_sqlite_source_family_binding(
        writer,
        expected_member_by_role["sqlite_backup"],
        synthetic_source_roots["owner_work"],
    )
    if sqlite_source_family["root_identity"] != source_bindings["owner_work"]["root_identity"]:
        raise SourceViewProofError("synthetic_sqlite_owner_root_identity_mismatch")
    source_locator_hashes = {
        role: synthetic_root_locator_sha256(
            role,
            expected_source_volume_uuid,
            expected_source_root_id,
            source_bindings[role]["root_identity"],
        )
        for role in tree_roles
    }
    expected_inventory_by_role = {
        role: capture_inventory_sha256(writer, member)
        for role, member in expected_member_by_role.items()
    }
    expected_counts_by_role = {
        role: capture_closure_counts(writer, member)
        for role, member in expected_member_by_role.items()
    }
    seen: set[str] = set()
    normalized_ledger = []
    for row in ledger:
        if not isinstance(row, dict) or set(row) != {
            "role",
            "root_locator_sha256",
            "inventory_sha256",
            "included_count",
            "excluded_count",
            "unknown_count",
            "disposition",
        }:
            raise SourceViewProofError("closure_ledger_row_invalid")
        role = row["role"]
        if role not in REQUIRED_ROLES or role in seen:
            raise SourceViewProofError("closure_ledger_role_invalid")
        _hash(row["root_locator_sha256"], "root_locator_hash")
        _hash(row["inventory_sha256"], "capture_inventory_hash")
        if (
            role in source_locator_hashes
            and row["root_locator_sha256"] != source_locator_hashes[role]
        ):
            raise SourceViewProofError("synthetic_source_root_locator_mismatch")
        if row["inventory_sha256"] != expected_inventory_by_role[role]:
            raise SourceViewProofError("closure_ledger_capture_inventory_mismatch")
        if any(
            type(row[k]) is not int or row[k] < 0
            for k in ("included_count", "excluded_count", "unknown_count")
        ):
            raise SourceViewProofError("closure_ledger_count_invalid")
        if any(
            row[key] != expected_counts_by_role[role][key] for key in expected_counts_by_role[role]
        ):
            raise SourceViewProofError("closure_ledger_capture_counts_mismatch")
        expected_disposition = "opaque_protected" if role == "protected_d43" else "included"
        if row["disposition"] != expected_disposition:
            raise SourceViewProofError("closure_ledger_disposition_invalid")
        seen.add(role)
        normalized_ledger.append(row)
    if seen != REQUIRED_ROLES or normalized_ledger != sorted(
        normalized_ledger, key=lambda row: row["role"]
    ):
        raise SourceViewProofError("closure_ledger_incomplete")

    intervals = [member.get("interval_ns") for member in index.get("members", [])]
    if any(
        not isinstance(interval, list)
        or len(interval) != 2
        or any(type(value) is not int or value <= 0 for value in interval)
        or interval[1] < interval[0]
        for interval in intervals
    ):
        raise SourceViewProofError("verified_set_member_interval_invalid")
    times = proof["times_ns"]
    if any(
        times["capture_started"] > interval[0] or times["capture_completed"] < interval[1]
        for interval in intervals
    ):
        raise SourceViewProofError("capture_time_does_not_cover_member_intervals")

    bindings = proof.get("member_bindings")
    if not isinstance(bindings, list) or len(bindings) != len(REQUIRED_ROLES):
        raise SourceViewProofError("source_member_bindings_incomplete")
    seen.clear()
    normalized_bindings = []
    for row in bindings:
        if not isinstance(row, dict) or set(row) != {
            "role",
            "format",
            "artifact_id",
            "manifest_sha256",
            "complete_sha256",
            "scope",
            "source_view_id",
            "root_locator_sha256",
            "inventory_sha256",
        }:
            raise SourceViewProofError("source_member_binding_invalid")
        role = row["role"]
        if role not in REQUIRED_ROLES or role in seen or row["source_view_id"] != view["view_id"]:
            raise SourceViewProofError("source_member_binding_role_or_view_mismatch")
        expected = expected_member_by_role[role]
        for field in ("format", "artifact_id", "manifest_sha256", "complete_sha256", "scope"):
            if row[field] != expected[field]:
                raise SourceViewProofError("source_member_binding_artifact_mismatch")
        ledger_row = normalized_ledger[sorted(REQUIRED_ROLES).index(role)]
        if (
            row["root_locator_sha256"] != ledger_row["root_locator_sha256"]
            or row["inventory_sha256"] != ledger_row["inventory_sha256"]
        ):
            raise SourceViewProofError("source_member_binding_inventory_mismatch")
        _hash(row["manifest_sha256"], "member_manifest_hash")
        _hash(row["complete_sha256"], "member_complete_hash")
        _hash(row["root_locator_sha256"], "root_locator_hash")
        _hash(row["inventory_sha256"], "capture_inventory_hash")
        seen.add(role)
        normalized_bindings.append(row)
    if seen != REQUIRED_ROLES or normalized_bindings != sorted(
        normalized_bindings, key=lambda row: row["role"]
    ):
        raise SourceViewProofError("source_member_bindings_incomplete")

    if proof.get("wallets_disposition") != "synthetic_fixture_absent":
        raise SourceViewProofError("source_view_wallets_unresolved")
    if proof.get("metadata_disposition") != {
        "scope": "synthetic_bytes_and_topology_only",
        "xattrs": "not_preserved",
        "acls": "not_preserved",
        "flags": "not_preserved",
        "uid_gid": "restored_by_mapping",
    }:
        raise SourceViewProofError("source_view_metadata_claim_unsupported")

    return {
        "schema": SCHEMA,
        "scenario": "synthetic",
        "proof_sha256": sha256(canonical_json(proof)),
        "set_id": set_id,
        "set_index_sha256": index_hash,
        "source_view_id": view["view_id"],
        "member_count": len(normalized_bindings),
        "declared_unknown_count": sum(row["unknown_count"] for row in normalized_ledger),
        "synthetic_tree_source_bindings_verified": len(source_bindings),
        "synthetic_sqlite_source_family_verified": True,
        "synthetic_sqlite_source_family_sha256": sqlite_source_family["family_sha256"],
        "source_root_closure_verified": False,
        "application_recovery_qualified": False,
        "factory_reset_ready": False,
    }
