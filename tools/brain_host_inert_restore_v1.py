#!/usr/bin/env python3
"""Restore a pinned recovery set into a fresh inert quarantine.

This conductor verifies and extracts the six declared recovery roles, then binds
one restore witness. It does not install services, contact providers, or grant
application, host-closure, T/K, or factory-reset readiness.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Support both `python -m tools.brain_host_inert_restore_v1` and direct CLI use.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import brain_host_recovery_capture as static_v1
from tools import brain_host_recovery_set as recovery_set
from tools import brain_host_tree_custody as tree_v2
from tools import brain_host_tree_custody_v3 as tree_v3
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SCHEMA = "brain-host-inert-restore-v1"
HANDOFF_SCHEMA = "brain-host-restore-assessor-handoff-v1"
ROLE_TARGET_NAMES = {
    "owner_work": "owner_work",
    "research_work": "research_work",
    "private_config": "private_config",
    "launchagent_definitions": "launchagent_definitions",
    "protected_d43": "protected_d43_opaque",
    "sqlite_backup": "sqlite_backup/backup.sqlite",
}


class InertRestoreError(ValueError):
    """The pinned recovery set cannot be safely restored as an inert quarantine."""


@dataclass(frozen=True)
class RestoreResult:
    payload: dict[str, Any]
    quarantine_root: Path


def _read_json(raw: bytes, label: str) -> dict[str, Any]:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise InertRestoreError(f"{label}_duplicate_key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InertRestoreError(f"{label}_invalid_json") from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise InertRestoreError(f"{label}_invalid_contract")
    return value


def _contained(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


def _audit_tree_capture_scope(
    writer: DescriptorWriter, role: str, member: dict[str, Any], detail: dict[str, Any]
) -> dict[str, Any]:
    manifest = detail["manifest"]
    policy = detail["policy"]
    capture_verified = detail["verify_result"]
    symlink_dispositions = {
        declaration["disposition"] for declaration in policy["symlink_target_dispositions"].values()
    }
    if "external_root_binding" in symlink_dispositions:
        raise InertRestoreError(f"{role}_external_symlink_binding_unbound")
    if symlink_dispositions - {"preserve_dangling_internal"}:
        raise InertRestoreError(f"{role}_symlink_disposition_unsupported")
    for entry in tree_v3._iter_records(writer, manifest["entry_indexes"], kind="entries"):
        if entry.get("kind") != "symlink":
            continue
        try:
            target = base64.b64decode(entry["target_b64"], validate=True)
        except (KeyError, ValueError) as exc:
            raise InertRestoreError(f"{role}_symlink_target_invalid") from exc
        if os.path.isabs(target):
            raise InertRestoreError(f"{role}_absolute_symlink_unrebased")
    exclusion_counts: dict[str, int] = {}
    external_custody = []
    protected_scope_count = 0
    for row in tree_v3._iter_records(writer, manifest["exclusion_indexes"], kind="exclusions"):
        disposition = row["disposition"]
        exclusion_counts[disposition] = exclusion_counts.get(disposition, 0) + 1
        if disposition == "protected_scope":
            protected_scope_count += 1
            raise InertRestoreError(f"{role}_protected_scope_exclusion_unbound")
        if disposition.startswith("unresolved"):
            raise InertRestoreError(f"{role}_capture_scope_unresolved")
        if disposition in {"separate_protected_custody", "substitute_online_backup"}:
            path = row["path"]
            allowed = role == "owner_work" and (
                (
                    path == "calendar-execution-01"
                    and row["exclusion_kind"] == "subtree"
                    and disposition == "separate_protected_custody"
                )
                or (
                    (
                        path == recovery_set.SQLITE_SOURCE_PATH
                        or path.startswith(recovery_set.SQLITE_SOURCE_PATH + "-")
                    )
                    and row["exclusion_kind"] == "file"
                    and disposition == "substitute_online_backup"
                )
            )
            if not allowed:
                raise InertRestoreError(f"{role}_external_custody_exclusion_unbound")
            external_custody.append((path, disposition))
        elif disposition not in {"reauth_reissue", "rebuild_disposable", "disposable_not_required"}:
            raise InertRestoreError(f"{role}_exclusion_disposition_unsupported")

    if role == "owner_work":
        exact = policy["exact_path_exclusions"]
        pem = policy["pem_candidate_dispositions"]
        sqlite_root = recovery_set.SQLITE_SOURCE_PATH
        for path, declaration in exact.items():
            disposition = declaration["disposition"]
            if disposition.startswith("unresolved"):
                raise InertRestoreError("owner_work_capture_scope_unresolved")
            if disposition == "separate_protected_custody" and path != "calendar-execution-01":
                raise InertRestoreError("owner_work_external_custody_unbound")
            if disposition == "substitute_online_backup" and not (
                path == sqlite_root or path.startswith(sqlite_root + "-")
            ):
                raise InertRestoreError("owner_work_external_custody_unbound")
        if any(value.startswith("unresolved") for value in pem.values()):
            raise InertRestoreError("owner_work_capture_scope_unresolved")
        bound_paths = {path for path, _ in external_custody}
        expected_paths = {
            path
            for path, declaration in exact.items()
            if declaration["disposition"]
            in {"separate_protected_custody", "substitute_online_backup"}
        }
        if bound_paths != expected_paths:
            raise InertRestoreError("owner_work_external_custody_membership_mismatch")
        expected_unknown_subtrees = int(
            "calendar-execution-01" in bound_paths
            and exact["calendar-execution-01"]["entry_type"] == "directory"
        )
        if (
            capture_verified["unresolved_exclusion_count"] != 0
            or capture_verified["unknown_descendant_subtree_count"] != expected_unknown_subtrees
        ):
            raise InertRestoreError("owner_work_unknown_descendant_scope_unresolved")

    if role != "owner_work" and (
        not capture_verified["full_recovery_ready"]
        or capture_verified["external_custody_required_count"] != 0
        or capture_verified["unknown_descendant_subtree_count"] != 0
    ):
        raise InertRestoreError(f"{role}_capture_scope_unresolved")
    return {
        "capture_interval_ns": [manifest["capture_start_ns"], manifest["capture_end_ns"]],
        "included_entry_count": capture_verified["included_entry_count"],
        "excluded_entry_count": capture_verified["excluded_entry_count"],
        "unknown_descendant_subtree_count": capture_verified["unknown_descendant_subtree_count"],
        "external_custody_required_count": capture_verified["external_custody_required_count"],
        "exclusion_disposition_counts": dict(sorted(exclusion_counts.items())),
        "protected_scope_exclusion_count": protected_scope_count,
        "member_full_recovery_ready": capture_verified["full_recovery_ready"],
        "recovery_set_disposition": (
            "cross_member_external_custody_bound"
            if role == "owner_work"
            else "member_scope_resolved"
        ),
    }


def _open_abs_dir(path: Path) -> int:
    return tree_v2._open_absolute_directory(path)


def _copy_sqlite_artifact(
    writer: DescriptorWriter,
    member: dict[str, Any],
    destination: Path,
    revalidate_identity: Callable[[], tuple[str, str]],
    expected_identity: tuple[str, str],
) -> None:
    base = f"{recovery_set.SQLITE_BASE}/{member['artifact_id']}"
    source_path = f"{base}/backup.sqlite"
    source_parent, source_name = writer._parent_and_name(source_path)
    source_parent_fd = writer._directory(source_parent)
    source_fd = -1
    destination_parent_fd = -1
    destination_fd = -1
    try:
        source_fd = os.open(
            source_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_parent_fd
        )
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise InertRestoreError("sqlite_backup_source_type_invalid")
        destination_parent_fd = _open_abs_dir(destination.parent)
        destination_fd = os.open(
            destination.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=destination_parent_fd,
        )
        if revalidate_identity() != expected_identity:
            raise InertRestoreError("storage_identity_changed_during_restore")
        digest = hashlib.sha256()
        total = 0
        while block := os.read(source_fd, 1024 * 1024):
            view = memoryview(block)
            while view:
                count = os.write(destination_fd, view)
                if count <= 0:
                    raise InertRestoreError("sqlite_restore_short_write")
                view = view[count:]
            digest.update(block)
            total += len(block)
        source_after = os.fstat(source_fd)
        if (
            source_stat.st_dev,
            source_stat.st_ino,
            source_stat.st_size,
            source_stat.st_mtime_ns,
        ) != (
            source_after.st_dev,
            source_after.st_ino,
            source_after.st_size,
            source_after.st_mtime_ns,
        ) or total != source_stat.st_size:
            raise InertRestoreError("sqlite_backup_changed_during_copy")
        os.fsync(destination_fd)
        os.fsync(destination_parent_fd)
        if revalidate_identity() != expected_identity:
            raise InertRestoreError("storage_identity_changed_during_restore")
        # The recovery-set verifier rechecks the committed artifact hash and size.
        verified, detail = recovery_set._verify_member(writer, member)
        if (
            total != detail["backup_size_bytes"]
            or digest.hexdigest() != detail["backup_sha256"]
            or verified["artifact_sha256"] != digest.hexdigest()
        ):
            raise InertRestoreError("sqlite_backup_copy_hash_mismatch")
    except OSError as exc:
        raise InertRestoreError("sqlite_backup_copy_failed") from exc
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        if destination_parent_fd >= 0:
            os.close(destination_parent_fd)
        if source_fd >= 0:
            os.close(source_fd)
        os.close(source_parent_fd)


def _extract_member(
    writer: DescriptorWriter,
    member: dict[str, Any],
    destination: Path,
    *,
    target_uid: int,
    target_gid: int,
    revalidate_identity: Callable[[], tuple[str, str]],
    expected_identity: tuple[str, str],
) -> dict[str, Any]:
    if revalidate_identity() != expected_identity:
        raise InertRestoreError("storage_identity_changed_during_restore")
    fmt = member["format"]
    artifact_id = member["artifact_id"]
    if fmt == "tree-custody-v3":
        _pin, detail = recovery_set._verify_member(writer, member)
        tree_v3.extract_tree(
            writer,
            artifact_id,
            destination,
            target_uid=target_uid,
            target_gid=target_gid,
            expected_policy_sha256=detail["manifest"]["policy_sha256"],
        )
    elif fmt == "tree-custody-v2":
        tree_v2.extract_tree(
            writer, artifact_id, destination, target_uid=target_uid, target_gid=target_gid
        )
    elif fmt == "static-recovery-v1":
        static_v1.extract_capture(writer, artifact_id, destination)
    elif fmt == "sqlite-online-backup-v1":
        _copy_sqlite_artifact(writer, member, destination, revalidate_identity, expected_identity)
    else:
        raise InertRestoreError("member_format_unsupported")
    if revalidate_identity() != expected_identity:
        raise InertRestoreError("storage_identity_changed_during_restore")
    return recovery_set.verify_quarantine_restore(writer, member, destination)


def restore_inert_v1(
    writer: DescriptorWriter,
    *,
    set_id: str,
    expected_index_sha256: str,
    expected_volume_uuid: str,
    expected_root_id: str,
    revalidate_identity: Callable[[], tuple[str, str]],
    target_parent: Path,
    repository_root: Path,
    storage_root: Path,
    forbidden_roots: Sequence[Path] = (),
    witness_id: str | None = None,
    target_uid: int | None = None,
    target_gid: int | None = None,
    require_separate_device: bool = True,
    fault_hook: Callable[[str], None] | None = None,
) -> RestoreResult:
    """Restore a complete pinned set and leave it inert in a new quarantine.

    `require_separate_device=False` is intended only for synthetic fixtures whose
    source and target share the test filesystem. The command-line entry point
    always requires a target device separate from Kingston.
    """
    if revalidate_identity() != (expected_volume_uuid, expected_root_id):
        raise InertRestoreError("storage_identity_pin_mismatch")
    if not re.fullmatch(r"[0-9a-f]{32}", set_id):
        raise InertRestoreError("set_id_invalid")
    if not isinstance(expected_index_sha256, str) or len(expected_index_sha256) != 64:
        raise InertRestoreError("expected_index_pin_invalid")
    witness_id = witness_id or secrets.token_hex(16)
    if not re.fullmatch(r"[0-9a-f]{32}", witness_id):
        raise InertRestoreError("witness_id_invalid")
    base = f"{recovery_set.BASE}/{set_id}"
    index_raw = writer.read_bytes(f"{base}/INDEX.json", max_bytes=recovery_set.MAX_SPEC_BYTES)
    if sha256(index_raw) != expected_index_sha256:
        raise InertRestoreError("independent_index_pin_mismatch")
    index = _read_json(index_raw, "recovery_set_index")
    verified = recovery_set.verify_set(
        writer,
        set_id,
        expected_volume_uuid=expected_volume_uuid,
        expected_root_id=expected_root_id,
        revalidate_identity=revalidate_identity,
    )
    if verified["index_sha256"] != expected_index_sha256:
        raise InertRestoreError("independent_index_pin_mismatch")
    if verified["member_count"] != len(ROLE_TARGET_NAMES) or not verified["coverage_resolved"]:
        raise InertRestoreError("recovery_set_obligations_unresolved")
    spec = recovery_set._validate_spec(index["spec"])
    by_role = {member["role"]: member for member in spec["members"]}
    if set(by_role) != set(ROLE_TARGET_NAMES):
        raise InertRestoreError("recovery_set_role_mapping_incomplete")
    if by_role["protected_d43"]["format"] != "tree-custody-v2":
        raise InertRestoreError("protected_member_format_unsupported")
    if index.get("volume_uuid") != expected_volume_uuid or index.get("root_id") != expected_root_id:
        raise InertRestoreError("recovery_set_storage_pin_mismatch")
    witness_path = f"{recovery_set.WITNESS_BASE}/{set_id}/{witness_id}"
    try:
        writer.list_names(witness_path)
    except FileNotFoundError:
        pass
    else:
        raise InertRestoreError("restore_witness_collision")
    capture_scopes = {}
    prepared_details = {
        member["role"]: detail
        for member, detail in recovery_set._prepare_details(writer, spec["members"])
    }
    if prepared_details["protected_d43"]["manifest"]["symlink_count"] != 0:
        raise InertRestoreError("protected_d43_symlinks_unrebased")
    for role, member in sorted(by_role.items()):
        if member["format"] != "tree-custody-v3":
            continue
        capture_scopes[role] = _audit_tree_capture_scope(
            writer, role, member, prepared_details[role]
        )

    exclusion_roots = [repository_root, storage_root, *forbidden_roots]
    for label, path in (
        ("target_parent", target_parent),
        *((f"forbidden_root_{index}", path) for index, path in enumerate(exclusion_roots)),
    ):
        if not path.is_absolute() or ".." in path.parts:
            raise InertRestoreError(f"{label}_path_invalid")
    target_parent = Path(os.path.abspath(target_parent))
    repository_root = Path(os.path.abspath(repository_root))
    storage_root = Path(os.path.abspath(storage_root))
    exclusion_roots = [Path(os.path.abspath(path)) for path in exclusion_roots]
    repo_fd = _open_abs_dir(repository_root)
    storage_fd = -1
    parent_fd = -1
    quarantine_fd = -1
    try:
        storage_fd = _open_abs_dir(storage_root)
        parent_fd = _open_abs_dir(target_parent)
        parent_stat = os.fstat(parent_fd)
        if parent_stat.st_uid != os.geteuid() or stat.S_IMODE(parent_stat.st_mode) & 0o022:
            raise InertRestoreError("target_parent_permissions_unsafe")
        if any(_contained(target_parent, root) for root in exclusion_roots):
            raise InertRestoreError("target_parent_inside_forbidden_root")
        storage_stat = os.fstat(storage_fd)
        if require_separate_device and parent_stat.st_dev == storage_stat.st_dev:
            raise InertRestoreError("target_must_be_on_separate_device")
        if revalidate_identity() != (expected_volume_uuid, expected_root_id):
            raise InertRestoreError("storage_identity_changed_before_restore")
        nonce = secrets.token_hex(16)
        quarantine_name = f"brain-restore-{nonce}"
        os.mkdir(quarantine_name, mode=0o700, dir_fd=parent_fd)
        quarantine_fd = os.open(
            quarantine_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd
        )
        os.fchmod(quarantine_fd, 0o700)
        os.fsync(parent_fd)
        quarantine_stat = os.fstat(quarantine_fd)
        parent_identity = (parent_stat.st_dev, parent_stat.st_ino)
        quarantine_root = target_parent / quarantine_name
        created_identity = (quarantine_stat.st_dev, quarantine_stat.st_ino)
    except OSError as exc:
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        if parent_fd >= 0:
            os.close(parent_fd)
        os.close(repo_fd)
        if storage_fd >= 0:
            os.close(storage_fd)
        raise InertRestoreError("quarantine_root_creation_failed") from exc
    except BaseException:
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        if parent_fd >= 0:
            os.close(parent_fd)
        os.close(repo_fd)
        if storage_fd >= 0:
            os.close(storage_fd)
        raise

    def revalidate_target() -> None:
        try:
            current_parent = _open_abs_dir(target_parent)
            try:
                current_parent_stat = os.fstat(current_parent)
                if (current_parent_stat.st_dev, current_parent_stat.st_ino) != parent_identity:
                    raise InertRestoreError("target_parent_replaced")
                current_root = os.open(
                    quarantine_name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current_parent,
                )
                try:
                    st = os.fstat(current_root)
                    if (st.st_dev, st.st_ino) != created_identity:
                        raise InertRestoreError("quarantine_root_replaced")
                finally:
                    os.close(current_root)
            finally:
                os.close(current_parent)
        except OSError as exc:
            raise InertRestoreError("quarantine_root_replaced") from exc

    try:
        targets: dict[str, Path] = {}
        for role, relative in ROLE_TARGET_NAMES.items():
            path = quarantine_root / relative
            if role == "sqlite_backup":
                os.mkdir("sqlite_backup", mode=0o700, dir_fd=quarantine_fd)
                os.fsync(quarantine_fd)
                sqlite_dir_fd = os.open(
                    "sqlite_backup",
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=quarantine_fd,
                )
                try:
                    os.fsync(sqlite_dir_fd)
                finally:
                    os.close(sqlite_dir_fd)
            targets[role] = path
        uid = os.geteuid() if target_uid is None else target_uid
        gid = os.getegid() if target_gid is None else target_gid
        if type(uid) is not int or uid < 0 or type(gid) is not int or gid < 0:
            raise InertRestoreError("target_owner_mapping_invalid")
        if any(member["format"] == "static-recovery-v1" for member in by_role.values()) and (
            uid != os.geteuid() or gid != os.getegid()
        ):
            raise InertRestoreError("static_target_owner_mapping_unsupported")
        member_results = {}
        for role in sorted(ROLE_TARGET_NAMES):
            revalidate_target()
            if revalidate_identity() != (expected_volume_uuid, expected_root_id):
                raise InertRestoreError("storage_identity_changed_during_restore")
            member_results[role] = _extract_member(
                writer,
                by_role[role],
                targets[role],
                target_uid=uid,
                target_gid=gid,
                revalidate_identity=revalidate_identity,
                expected_identity=(expected_volume_uuid, expected_root_id),
            )
            revalidate_target()
            if fault_hook:
                fault_hook(f"after_{role}")
        os.fsync(quarantine_fd)
        if revalidate_identity() != (expected_volume_uuid, expected_root_id):
            raise InertRestoreError("storage_identity_changed_before_witness")
        revalidate_target()
        witness = recovery_set.bind_restore_witness(
            writer,
            set_id,
            witness_id,
            targets,
            expected_volume_uuid=expected_volume_uuid,
            expected_root_id=expected_root_id,
            revalidate_identity=revalidate_identity,
        )
        if fault_hook:
            fault_hook("after_witness")
        revalidate_target()
        verified_after = recovery_set.verify_set(
            writer,
            set_id,
            expected_volume_uuid=expected_volume_uuid,
            expected_root_id=expected_root_id,
            revalidate_identity=revalidate_identity,
            witness_id=witness_id,
            witness_targets=targets,
        )
        if not verified_after["restore_witness_bound"] or not verified_after["coverage_resolved"]:
            raise InertRestoreError("final_restore_verification_incomplete")
        result = {
            "schema": SCHEMA,
            "status": "complete_inert_quarantine",
            "positive_claim": "inert_artifacts_restored_and_byte_verified",
            "set_id": set_id,
            "index_sha256": expected_index_sha256,
            "witness_id": witness_id,
            "witness_sha256": witness["witness_sha256"],
            "quarantine_root": str(quarantine_root),
            "target_identity": {
                "device": quarantine_stat.st_dev,
                "inode": quarantine_stat.st_ino,
                "mode": stat.S_IMODE(quarantine_stat.st_mode),
                "uid": quarantine_stat.st_uid,
                "gid": quarantine_stat.st_gid,
            },
            "ownership_mapping": {
                "target_uid": uid,
                "target_gid": gid,
                "static_capture_owner_mapping": "current_user_only",
                "sqlite_backup_mode": "0600_current_user",
            },
            "role_targets": {role: str(path) for role, path in sorted(targets.items())},
            "member_results": member_results,
            "capture_scopes": capture_scopes,
            "member_count": verified_after["member_count"],
            "restore_witness_bound": verified_after["restore_witness_bound"],
            "coverage_resolved": verified_after["coverage_resolved"],
            "application_recovery_qualified": False,
            "source_root_closure_verified": False,
            "T_qualified": False,
            "K_frontier_qualified": False,
            "host_closure": False,
            "activation_authorized": False,
            "services_changed": False,
            "provider_requests_sent": 0,
            "safe_to_erase": False,
            "partial_failure_policy": "leave_in_quarantine_for_review_no_resume",
        }
        handoff = {
            "schema": HANDOFF_SCHEMA,
            "status": "blocked",
            "set_id": set_id,
            "expected_commit": index["research_commit"],
            "expected_volume_uuid": expected_volume_uuid,
            "expected_root_id": expected_root_id,
            "expected_index_sha256": expected_index_sha256,
            "witness_id": witness_id,
            "expected_witness_sha256": witness["witness_sha256"],
            "witness_targets": {role: str(path) for role, path in sorted(targets.items())},
            "repo": str(repository_root),
            "activation_authorized": False,
            "safe_to_erase": False,
        }
        result["assessor_handoff"] = handoff
        result["evidence_sha256"] = sha256(canonical_json(result))
        return RestoreResult(result, quarantine_root)
    except Exception as exc:
        # Deliberately retain the nonce directory and any partial restore.
        if isinstance(exc, InertRestoreError):
            raise InertRestoreError(f"{exc}; quarantine_retained={quarantine_root}") from exc
        raise InertRestoreError(
            f"restore_failed:{type(exc).__name__}; quarantine_retained={quarantine_root}"
        ) from exc
    finally:
        if quarantine_fd >= 0:
            os.close(quarantine_fd)
        os.close(parent_fd)
        os.close(repo_fd)
        os.close(storage_fd)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage-config", type=Path, required=True)
    parser.add_argument("--set-id", required=True)
    parser.add_argument("--expected-index-sha256", required=True)
    parser.add_argument("--target-parent", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument(
        "--forbidden-root",
        type=Path,
        action="append",
        required=True,
        help="declared source root that must not contain the restore target (repeatable)",
    )
    parser.add_argument("--witness-id")
    parser.add_argument("--target-uid", type=int)
    parser.add_argument("--target-gid", type=int)
    args = parser.parse_args(argv)
    try:
        with tree_v2._storage_writer(args.storage_config) as writer:
            guard = writer._custody_mutation_guard.storage_guard

            def revalidate_identity() -> tuple[str, str]:
                resolved = guard.revalidate()
                return resolved.volume_uuid, resolved.root_id

            result = restore_inert_v1(
                writer,
                set_id=args.set_id,
                expected_index_sha256=args.expected_index_sha256,
                expected_volume_uuid=guard.volume_uuid,
                expected_root_id=guard.root_id,
                revalidate_identity=revalidate_identity,
                target_parent=args.target_parent,
                repository_root=args.repository_root,
                storage_root=guard.mount_path / guard.config.data_root,
                forbidden_roots=args.forbidden_root,
                witness_id=args.witness_id,
                target_uid=args.target_uid,
                target_gid=args.target_gid,
            )
        print(json.dumps(result.payload, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "status": "blocked",
                    "activation_authorized": False,
                    "safe_to_erase": False,
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
