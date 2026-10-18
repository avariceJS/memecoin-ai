#!/usr/bin/env python3
"""Reconstruct a v4 synthetic custody capture into inert disposable quarantine.

This is a byte/topology witness only. It applies no source metadata, creates no
symlinks, and cannot publish real custody, T, COMPLETE, or erase readiness.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from collections import defaultdict
from pathlib import Path
from typing import Any

from tools.brain_host_tree_custody_v4 import PLAN_SCHEMA, verify_plan

RECEIPT_SCHEMA = "brain-host-tree-restore-v4-synthetic-receipt"
MAX_CHUNK = 1024 * 1024
MAX_JSON_BYTES = 128 * 1024 * 1024
MAX_OBJECT_BYTES = 128 * 1024 * 1024
MAX_CAPTURE_BYTES = 512 * 1024 * 1024


class SyntheticRestoreError(ValueError):
    """The synthetic v4 artifact or inert reconstruction is invalid."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _verify_fd(fd: int, *, expected_size: int, expected_sha256: str) -> None:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != expected_size:
        raise SyntheticRestoreError("artifact_identity_mismatch")
    digest = hashlib.sha256()
    total = 0
    while chunk := os.read(fd, MAX_CHUNK):
        digest.update(chunk)
        total += len(chunk)
    after = os.fstat(fd)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_mode,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_mode,
    ):
        raise SyntheticRestoreError("artifact_changed_during_read")
    if total != expected_size or digest.hexdigest() != expected_sha256:
        raise SyntheticRestoreError("artifact_hash_mismatch")


def _stable_json(fd: int, *, label: str) -> tuple[bytes, Any]:
    before = os.fstat(fd)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size < 2
        or before.st_size > MAX_JSON_BYTES
    ):
        raise SyntheticRestoreError(f"{label}_identity_invalid")
    chunks: list[bytes] = []
    total = 0
    while chunk := os.read(fd, MAX_CHUNK):
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_JSON_BYTES:
            raise SyntheticRestoreError(f"{label}_too_large")
    after = os.fstat(fd)
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_mode,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_mode,
    )
    if before_identity != after_identity or total != before.st_size:
        raise SyntheticRestoreError(f"{label}_changed_during_read")
    raw = b"".join(chunks)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyntheticRestoreError(f"{label}_json_invalid") from exc
    if _canonical(value) != raw:
        raise SyntheticRestoreError(f"{label}_not_canonical")
    return raw, value


def _capture_bundle(
    capture_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    bundle_fd = os.open(capture_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        capture_fd = -1
        receipt_fd = -1
        try:
            capture_fd = os.open(
                "capture.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=bundle_fd
            )
            receipt_fd = os.open(
                "receipt.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=bundle_fd
            )
            capture_bytes, manifest = _stable_json(capture_fd, label="capture")
            receipt_bytes, receipt = _stable_json(receipt_fd, label="capture_receipt")
        finally:
            if receipt_fd >= 0:
                os.close(receipt_fd)
            if capture_fd >= 0:
                os.close(capture_fd)
        if (
            not isinstance(manifest, dict)
            or set(manifest)
            != {
                "schema",
                "synthetic",
                "nonce",
                "source_view_authority",
                "plan",
                "metadata_values",
                "objects",
                "complete",
                "kingston_durable",
                "restore_verified",
                "erase_ready",
            }
            or manifest.get("schema") != "brain-host-tree-capture-v4-synthetic"
            or manifest.get("synthetic") is not True
            or manifest.get("source_view_authority") != "synthetic_fixture_only"
            or not isinstance(manifest.get("nonce"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", manifest["nonce"])
            or manifest.get("complete") is not False
            or manifest.get("kingston_durable") is not False
            or manifest.get("restore_verified") is not False
            or manifest.get("erase_ready") is not False
        ):
            raise SyntheticRestoreError("synthetic_capture_required")
        if (
            not isinstance(receipt, dict)
            or set(receipt)
            != {
                "schema",
                "synthetic",
                "captured",
                "complete_marker_published",
                "real_T",
                "real_capture",
                "kingston_custody",
                "restore_verified",
                "erase_ready",
                "manifest_sha256",
            }
            or receipt.get("schema") != "brain-host-tree-capture-v4-synthetic-receipt"
            or receipt.get("synthetic") is not True
            or receipt.get("captured") is not True
            or receipt.get("complete_marker_published") is not False
            or receipt.get("real_T") is not False
            or receipt.get("real_capture") is not False
            or receipt.get("kingston_custody") is not False
            or receipt.get("restore_verified") is not False
            or receipt.get("erase_ready") is not False
            or receipt.get("manifest_sha256") != hashlib.sha256(capture_bytes).hexdigest()
        ):
            raise SyntheticRestoreError("capture_receipt_invalid")
        plan = manifest.get("plan")
        try:
            plan = verify_plan(plan)
        except (TypeError, ValueError) as exc:
            raise SyntheticRestoreError("capture_plan_invalid") from exc
        if plan.get("schema") != PLAN_SCHEMA or plan.get("unsupported_metadata_member_count") != 0:
            raise SyntheticRestoreError("capture_metadata_unresolved")
        metadata_values = manifest.get("metadata_values")
        if not isinstance(metadata_values, dict):
            raise SyntheticRestoreError("capture_metadata_evidence_invalid")
        expected_metadata_keys = {f"root:{row['root_key']}" for row in plan["root_roster"]}
        expected_metadata_keys.update(
            f"entry:{row['root_key']}:{row['path']}"
            for row in plan["entries"]
            if row["disposition"] == "included_opaque"
        )
        if set(metadata_values) != expected_metadata_keys:
            raise SyntheticRestoreError("capture_metadata_evidence_roster_mismatch")
        objects = manifest.get("objects")
        if not isinstance(objects, list):
            raise SyntheticRestoreError("object_roster_invalid")
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in plan["entries"]:
            if row["kind"] == "file" and row["disposition"] == "included_opaque":
                groups[row["hardlink_group_id"]].append(row)
        if len(objects) != len(groups):
            raise SyntheticRestoreError("object_group_roster_mismatch")
        total_object_bytes = 0
        object_map: dict[str, dict[str, Any]] = {}
        for item in objects:
            if not isinstance(item, dict) or set(item) != {
                "hardlink_group_id",
                "path",
                "size_bytes",
                "sha256",
                "alias_count",
            }:
                raise SyntheticRestoreError("object_record_invalid")
            group = item["hardlink_group_id"]
            path = item["path"]
            if (
                not isinstance(group, str)
                or not re.fullmatch(r"[a-f0-9]{64}", group)
                or group not in groups
                or group in object_map
                or path != f"object-{group}.bin"
                or type(item["size_bytes"]) is not int
                or not 0 <= item["size_bytes"] <= MAX_OBJECT_BYTES
                or item["size_bytes"] != groups[group][0]["size"]
                or not isinstance(item["sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
                or type(item["alias_count"]) is not int
                or item["alias_count"] != len(groups[group])
            ):
                raise SyntheticRestoreError("object_group_mapping_invalid")
            total_object_bytes += item["size_bytes"]
            if total_object_bytes > MAX_CAPTURE_BYTES:
                raise SyntheticRestoreError("capture_size_limit_exceeded")
            object_map[group] = item
        expected_names = sorted(["capture.json", "receipt.json", *(row["path"] for row in objects)])
        if sorted(os.listdir(bundle_fd)) != expected_names:
            raise SyntheticRestoreError("capture_member_roster_mismatch")
        for item in objects:
            fd = os.open(
                item["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=bundle_fd
            )
            try:
                _verify_fd(fd, expected_size=item["size_bytes"], expected_sha256=item["sha256"])
            finally:
                os.close(fd)
        return manifest, receipt, object_map
    finally:
        os.close(bundle_fd)


def _assert_capture_unchanged(
    capture_dir: Path,
    expected_manifest: dict[str, Any],
    expected_receipt: dict[str, Any],
    expected_objects: dict[str, dict[str, Any]],
) -> None:
    manifest, receipt, objects = _capture_bundle(capture_dir)
    if manifest != expected_manifest or receipt != expected_receipt or objects != expected_objects:
        raise SyntheticRestoreError("capture_bundle_changed_during_restore")


def _open_dir(root_fd: int, relative: str = "") -> int:
    current = os.dup(root_fd)
    if not relative:
        return current
    try:
        for part in relative.split("/"):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _make_dirs(root_fd: int, relative: str) -> None:
    current = os.dup(root_fd)
    try:
        for part in relative.split("/"):
            try:
                os.mkdir(part, 0o700, dir_fd=current)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            if stat.S_IMODE(os.fstat(child).st_mode) != 0o700:
                os.close(child)
                raise SyntheticRestoreError("quarantine_directory_mode_invalid")
            os.close(current)
            current = child
    finally:
        os.close(current)


def _write_object(
    source_bundle_fd: int,
    object_record: dict[str, Any],
    destination_parent_fd: int,
    destination_name: str,
) -> None:
    source_fd = os.open(
        object_record["path"],
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=source_bundle_fd,
    )
    destination_fd = -1
    try:
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
            raise SyntheticRestoreError("object_identity_mismatch")
        destination_fd = os.open(
            destination_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=destination_parent_fd,
        )
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(source_fd, MAX_CHUNK):
            digest.update(chunk)
            total += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise SyntheticRestoreError("restore_write_short")
                view = view[written:]
        os.fsync(destination_fd)
        if total != object_record["size_bytes"] or digest.hexdigest() != object_record["sha256"]:
            raise SyntheticRestoreError("object_changed_during_restore")
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(source_fd)


def _tree_inventory(
    quarantine_fd: int, root_keys: list[str]
) -> tuple[list[str], dict[str, tuple[int, int, int, str, int]]]:
    names: list[str] = []
    files: dict[str, tuple[int, int, int, str, int]] = {}
    for root_key in root_keys:
        root_fd = os.open(
            root_key, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=quarantine_fd
        )
        names.append(f"{root_key}/")

        def walk(dir_fd: int, prefix: str, root_key: str = root_key) -> None:
            for name in sorted(os.listdir(dir_fd)):
                rel = f"{prefix}/{name}" if prefix else name
                full = f"{root_key}/{rel}"
                st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    child = os.open(
                        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd
                    )
                    try:
                        names.append(full + "/")
                        walk(child, rel)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(st.st_mode):
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
                    try:
                        before = os.fstat(fd)
                        if not stat.S_ISREG(before.st_mode):
                            raise SyntheticRestoreError("restored_regular_file_type_changed")
                        digest = hashlib.sha256()
                        size = 0
                        while chunk := os.read(fd, MAX_CHUNK):
                            digest.update(chunk)
                            size += len(chunk)
                        after = os.fstat(fd)
                        if (before.st_dev, before.st_ino, before.st_size) != (
                            after.st_dev,
                            after.st_ino,
                            after.st_size,
                        ):
                            raise SyntheticRestoreError("restored_file_changed_during_read")
                        names.append(full)
                        files[full] = (
                            after.st_dev,
                            after.st_ino,
                            size,
                            digest.hexdigest(),
                            after.st_nlink,
                        )
                    finally:
                        os.close(fd)
                else:
                    raise SyntheticRestoreError("restored_special_member_rejected")

        try:
            walk(root_fd, "")
        finally:
            os.close(root_fd)
    return sorted(names), files


def _remove_tree_contents(directory_fd: int) -> None:
    for name in os.listdir(directory_fd):
        child_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(child_stat.st_mode):
            child_fd = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd
            )
            try:
                opened = os.fstat(child_fd)
                if (opened.st_dev, opened.st_ino) != (child_stat.st_dev, child_stat.st_ino):
                    raise SyntheticRestoreError("quarantine_child_identity_changed")
                _remove_tree_contents(child_fd)
            finally:
                os.close(child_fd)
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (child_stat.st_dev, child_stat.st_ino):
                raise SyntheticRestoreError("quarantine_child_identity_changed")
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)


def _remove_owned_quarantine(parent_fd: int, name: str, identity: tuple[int, int]) -> None:
    try:
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (st.st_dev, st.st_ino) != identity or not stat.S_ISDIR(st.st_mode):
        raise SyntheticRestoreError("quarantine_identity_changed_cleanup_refused")
    root_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        opened = os.fstat(root_fd)
        if (opened.st_dev, opened.st_ino) != identity:
            raise SyntheticRestoreError("quarantine_identity_changed_cleanup_refused")
        _remove_tree_contents(root_fd)
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity or not stat.S_ISDIR(current.st_mode):
            raise SyntheticRestoreError("quarantine_identity_changed_cleanup_refused")
        os.rmdir(name, dir_fd=parent_fd)
    finally:
        os.close(root_fd)


def reconstruct_synthetic_v4(
    capture_dir: Path,
    output_parent: Path,
    *,
    nonce: str | None = None,
    interruption_hook: Any = None,
) -> dict[str, Any]:
    """Reconstruct included bytes/topology only; symlinks and metadata stay inert."""
    nonce = nonce or secrets.token_hex(16)
    if not re.fullmatch(r"[a-f0-9]{32}", nonce):
        raise SyntheticRestoreError("nonce_invalid")
    manifest, capture_receipt, object_map = _capture_bundle(capture_dir)
    plan = manifest["plan"]
    root_keys = sorted(row["root_key"] for row in plan["root_roster"])
    if len(root_keys) != 3:
        raise SyntheticRestoreError("root_roster_invalid")
    included = [row for row in plan["entries"] if row["disposition"] == "included_opaque"]
    expected_members = [f"{root_key}/" for root_key in root_keys]
    expected_members.extend(
        f"{row['root_key']}/{row['path']}/"
        if row["kind"] == "directory"
        else f"{row['root_key']}/{row['path']}"
        for row in included
        if row["kind"] in {"directory", "file"}
    )
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in included:
        if row["kind"] == "symlink" and row.get("symlink_materialize") is not False:
            raise SyntheticRestoreError("symlink_materialization_not_explicitly_disabled")
    for row in included:
        if row["kind"] == "file":
            groups[row["hardlink_group_id"]].append(row)
    expected_alias_to_group = {
        f"{row['root_key']}/{row['path']}": group
        for group, aliases in groups.items()
        for row in aliases
    }
    output = output_parent / f"brain-restore-{nonce}"
    output_parent_fd = os.open(output_parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    output_name = output.name
    try:
        os.mkdir(output_name, 0o700, dir_fd=output_parent_fd)
    except BaseException:
        os.close(output_parent_fd)
        raise
    quarantine_fd = -1
    output_identity: tuple[int, int] | None = None
    try:
        quarantine_fd = os.open(
            output_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=output_parent_fd
        )
        quarantine_stat = os.fstat(quarantine_fd)
        output_identity = (quarantine_stat.st_dev, quarantine_stat.st_ino)
        current_stat = os.stat(output_name, dir_fd=output_parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(current_stat.st_mode) or output_identity != (
            current_stat.st_dev,
            current_stat.st_ino,
        ):
            raise SyntheticRestoreError("quarantine_identity_changed_after_create")
    except BaseException as exc:
        try:
            if output_identity is None:
                try:
                    os.rmdir(output_name, dir_fd=output_parent_fd)
                except OSError:
                    raise SyntheticRestoreError("quarantine_cleanup_incomplete") from exc
            else:
                _remove_owned_quarantine(output_parent_fd, output_name, output_identity)
        finally:
            if quarantine_fd >= 0:
                os.close(quarantine_fd)
            os.close(output_parent_fd)
        raise
    try:
        bundle_fd = os.open(capture_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if stat.S_IMODE(os.fstat(quarantine_fd).st_mode) != 0o700:
                raise SyntheticRestoreError("quarantine_root_mode_invalid")
            for root_key in root_keys:
                os.mkdir(root_key, 0o700, dir_fd=quarantine_fd)
            root_fds: dict[str, int] = {}
            try:
                for key in root_keys:
                    root_fds[key] = os.open(
                        key, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=quarantine_fd
                    )
            except BaseException:
                for fd in root_fds.values():
                    os.close(fd)
                raise
            try:
                dirs = sorted(
                    (row for row in included if row["kind"] == "directory"),
                    key=lambda row: (row["path"].count("/"), row["root_key"], row["path"]),
                )
                for row in dirs:
                    _make_dirs(root_fds[row["root_key"]], row["path"])
                for index, (group, aliases) in enumerate(sorted(groups.items())):
                    if group not in object_map:
                        raise SyntheticRestoreError("object_group_mapping_invalid")
                    aliases = sorted(aliases, key=lambda row: (row["root_key"], row["path"]))
                    primary = aliases[0]
                    primary_parent = _open_dir(
                        root_fds[primary["root_key"]], "/".join(primary["path"].split("/")[:-1])
                    )
                    try:
                        _write_object(
                            bundle_fd,
                            object_map[group],
                            primary_parent,
                            primary["path"].split("/")[-1],
                        )
                    finally:
                        os.close(primary_parent)
                    for alias in aliases[1:]:
                        source_parent = _open_dir(
                            root_fds[primary["root_key"]], "/".join(primary["path"].split("/")[:-1])
                        )
                        destination_parent = _open_dir(
                            root_fds[alias["root_key"]], "/".join(alias["path"].split("/")[:-1])
                        )
                        try:
                            os.link(
                                primary["path"].split("/")[-1],
                                alias["path"].split("/")[-1],
                                src_dir_fd=source_parent,
                                dst_dir_fd=destination_parent,
                                follow_symlinks=False,
                            )
                        finally:
                            os.close(source_parent)
                            os.close(destination_parent)
                    if interruption_hook is not None:
                        interruption_hook(index, output)
                for fd in root_fds.values():
                    os.fsync(fd)
            finally:
                for fd in root_fds.values():
                    os.close(fd)
            actual_members, actual_files = _tree_inventory(quarantine_fd, root_keys)
            expected_members = sorted(expected_members)
            if actual_members != expected_members:
                raise SyntheticRestoreError("restore_member_roster_mismatch")
            group_inodes: dict[str, tuple[int, int]] = {}
            inode_groups: dict[tuple[int, int], str] = {}
            group_alias_counts = {group: len(aliases) for group, aliases in groups.items()}
            for alias_key, group_id in expected_alias_to_group.items():
                identity = actual_files[alias_key]
                inode = identity[:2]
                if inode in inode_groups and inode_groups[inode] != group_id:
                    raise SyntheticRestoreError("distinct_hardlink_groups_share_inode")
                inode_groups[inode] = group_id
                if group_id in group_inodes and group_inodes[group_id] != identity[:2]:
                    raise SyntheticRestoreError("restored_hardlink_topology_mismatch")
                group_inodes[group_id] = identity[:2]
                object_record = object_map[group_id]
                if identity[2:4] != (object_record["size_bytes"], object_record["sha256"]):
                    raise SyntheticRestoreError("restored_file_bytes_mismatch")
                if identity[4] != group_alias_counts[group_id]:
                    raise SyntheticRestoreError("restored_hardlink_external_alias")
            # Preserve actual source metadata records as inert private evidence; do not apply them.
            metadata_bytes = _canonical(manifest.get("metadata_values"))
            metadata_fd = os.open(
                "metadata-evidence.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=quarantine_fd,
            )
            try:
                view = memoryview(metadata_bytes)
                while view:
                    written = os.write(metadata_fd, view)
                    if written <= 0:
                        raise SyntheticRestoreError("metadata_evidence_write_short")
                    view = view[written:]
                os.fsync(metadata_fd)
            finally:
                os.close(metadata_fd)
            # Recheck every source artifact after reconstruction before witness.
            _assert_capture_unchanged(capture_dir, manifest, capture_receipt, object_map)
            final_members, final_files = _tree_inventory(quarantine_fd, root_keys)
            if final_members != actual_members or final_files != actual_files:
                raise SyntheticRestoreError("restored_tree_changed_before_witness")
            unresolved_groups = []
            for group, aliases in sorted(groups.items()):
                external_count = aliases[0]["unaccounted_alias_count"]
                if external_count:
                    unresolved_groups.append(
                        {
                            "hardlink_group_id": group,
                            "in_group_alias_count": len(aliases),
                            "external_alias_count": external_count,
                            "external_location": "unresolved",
                        }
                    )
            witness = {
                "schema": RECEIPT_SCHEMA,
                "synthetic": True,
                "byte_topology_reconstruction_verified": True,
                "root_count": len(root_keys),
                "restored_file_alias_count": len(expected_alias_to_group),
                "restored_file_group_count": len(groups),
                "excluded_entry_count": plan["reissue_excluded_entry_count"],
                "inert_symlink_count": sum(row["kind"] == "symlink" for row in included),
                "unresolved_external_hardlink_group_count": len(unresolved_groups),
                "unresolved_external_hardlink_groups": unresolved_groups,
                "metadata_evidence_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
                "metadata_evidence_member": "metadata-evidence.json",
                "capture_manifest_sha256": hashlib.sha256(_canonical(manifest)).hexdigest(),
                "capture_receipt_sha256": hashlib.sha256(_canonical(capture_receipt)).hexdigest(),
                "metadata_applied": False,
                "semantic_lineage": "unproven",
                "real_T": False,
                "kingston_custody": False,
                "full_restore_verified": False,
                "erase_ready": False,
            }
            witness_bytes = _canonical(witness)
            pending_witness_name = ".synthetic-witness.pending"
            witness_fd = os.open(
                pending_witness_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=quarantine_fd,
            )
            try:
                view = memoryview(witness_bytes)
                while view:
                    written = os.write(witness_fd, view)
                    if written <= 0:
                        raise SyntheticRestoreError("witness_write_short")
                    view = view[written:]
                os.fsync(witness_fd)
            finally:
                os.close(witness_fd)
            os.fsync(quarantine_fd)
            pending_top = sorted([*root_keys, "metadata-evidence.json", pending_witness_name])
            if sorted(os.listdir(quarantine_fd)) != pending_top:
                raise SyntheticRestoreError("restore_top_level_roster_mismatch")
            metadata_read_fd = -1
            witness_read_fd = -1
            try:
                metadata_read_fd = os.open(
                    "metadata-evidence.json",
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=quarantine_fd,
                )
                witness_read_fd = os.open(
                    pending_witness_name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=quarantine_fd,
                )
                _verify_fd(
                    metadata_read_fd,
                    expected_size=len(metadata_bytes),
                    expected_sha256=hashlib.sha256(metadata_bytes).hexdigest(),
                )
                verified_witness, parsed_witness = _stable_json(
                    witness_read_fd,
                    label="restore_witness_readback",
                )
            finally:
                if metadata_read_fd >= 0:
                    os.close(metadata_read_fd)
                if witness_read_fd >= 0:
                    os.close(witness_read_fd)
            if verified_witness != witness_bytes or parsed_witness != witness:
                raise SyntheticRestoreError("witness_readback_mismatch")
            _assert_capture_unchanged(capture_dir, manifest, capture_receipt, object_map)
            pre_publish_members, pre_publish_files = _tree_inventory(quarantine_fd, root_keys)
            if pre_publish_members != actual_members or pre_publish_files != actual_files:
                raise SyntheticRestoreError("restored_tree_changed_before_witness_publish")
            try:
                os.stat("synthetic-witness.json", dir_fd=quarantine_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise SyntheticRestoreError("restore_witness_target_preexisting")
            _assert_capture_unchanged(capture_dir, manifest, capture_receipt, object_map)
            os.rename(
                pending_witness_name,
                "synthetic-witness.json",
                src_dir_fd=quarantine_fd,
                dst_dir_fd=quarantine_fd,
            )
            os.fsync(quarantine_fd)
            expected_top = sorted([*root_keys, "metadata-evidence.json", "synthetic-witness.json"])
            if sorted(os.listdir(quarantine_fd)) != expected_top:
                raise SyntheticRestoreError("restore_top_level_roster_changed")
            metadata_read_fd = -1
            witness_read_fd = -1
            try:
                metadata_read_fd = os.open(
                    "metadata-evidence.json",
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=quarantine_fd,
                )
                witness_read_fd = os.open(
                    "synthetic-witness.json",
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=quarantine_fd,
                )
                _verify_fd(
                    metadata_read_fd,
                    expected_size=len(metadata_bytes),
                    expected_sha256=hashlib.sha256(metadata_bytes).hexdigest(),
                )
                final_witness_bytes, final_witness = _stable_json(
                    witness_read_fd,
                    label="published_witness_readback",
                )
            finally:
                if metadata_read_fd >= 0:
                    os.close(metadata_read_fd)
                if witness_read_fd >= 0:
                    os.close(witness_read_fd)
            if final_witness_bytes != witness_bytes or final_witness != witness:
                raise SyntheticRestoreError("published_witness_readback_mismatch")
            _assert_capture_unchanged(capture_dir, manifest, capture_receipt, object_map)
            post_publish_members, post_publish_files = _tree_inventory(quarantine_fd, root_keys)
            if post_publish_members != actual_members or post_publish_files != actual_files:
                raise SyntheticRestoreError("restored_tree_changed_after_witness_publish")
            if sorted(os.listdir(quarantine_fd)) != expected_top:
                raise SyntheticRestoreError("restore_top_level_roster_changed_after_publish")
            return {
                **witness,
                "witness_sha256": hashlib.sha256(witness_bytes).hexdigest(),
                "quarantine": output.name,
            }
        finally:
            os.close(bundle_fd)
    except BaseException:
        if output_identity is not None:
            _remove_owned_quarantine(output_parent_fd, output_name, output_identity)
        raise
    finally:
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        os.close(output_parent_fd)
