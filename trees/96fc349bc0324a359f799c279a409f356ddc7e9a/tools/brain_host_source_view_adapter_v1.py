#!/usr/bin/env python3
"""Consume and reconcile a declared source view without creating snapshots.

V1 is synthetic-only. It binds a fixture view and its declared roots through
held no-follow descriptors, then reconciles capture pages using the existing
path-based synthetic source proof. It cannot attest APFS, qualify T, or prove
owner/protected closure. Snapshot creation and mounting are intentionally absent.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools import brain_host_source_view_proof_v1 as source_proof
from tools.brain_senses_external_writer_v1 import canonical_json, sha256

SCHEMA = "brain-host-source-view-adapter-v1"
REQUIRED_ROOT_ROLES = {
    "owner_work",
    "research_work",
    "private_config",
    "launchagent_definitions",
    "protected_d43",
    "sqlite_backup",
}
TREE_ROOT_ROLES = {
    "owner_work",
    "research_work",
    "private_config",
    "launchagent_definitions",
}


class SourceViewAdapterError(ValueError):
    """A declared source-view binding is invalid, unstable, or unsupported."""


@dataclass(frozen=True)
class SyntheticViewPlan:
    """Synthetic-only locator plan; no APFS identity is accepted as authority."""

    fixture_id: str
    view_id: str
    view_root: Path
    root_locators: Mapping[str, str]
    source_volume_uuid: str
    source_root_id: str
    credential_reissue_only: tuple[tuple[str, str], ...] = ()


def _open_absolute_directory_nofollow(path: Path) -> int:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute() or ".." in absolute.parts:
        raise SourceViewAdapterError("view_path_invalid")
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in absolute.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = child
        result = current
        current = -1
        return result
    except OSError as exc:
        raise SourceViewAdapterError("view_path_open_failed") from exc
    finally:
        if current >= 0:
            os.close(current)


def _relative_parts(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise SourceViewAdapterError("root_locator_invalid")
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SourceViewAdapterError("root_locator_invalid")
    if path.as_posix() != value:
        raise SourceViewAdapterError("root_locator_noncanonical")
    return path.parts


def _root_identity(st: os.stat_result) -> dict[str, int]:
    return {"device": st.st_dev, "inode": st.st_ino}


class HeldSyntheticView(AbstractContextManager["HeldSyntheticView"]):
    """Read-only descriptor capability for one synthetic view fixture."""

    def __init__(self, plan: SyntheticViewPlan) -> None:
        if not isinstance(plan.fixture_id, str) or not plan.fixture_id.strip():
            raise SourceViewAdapterError("fixture_id_invalid")
        if not isinstance(plan.view_id, str) or not plan.view_id.strip():
            raise SourceViewAdapterError("view_id_invalid")
        if not isinstance(plan.source_volume_uuid, str) or not plan.source_volume_uuid:
            raise SourceViewAdapterError("source_volume_identity_invalid")
        if not isinstance(plan.source_root_id, str) or not plan.source_root_id:
            raise SourceViewAdapterError("source_root_identity_invalid")
        root_locators = dict(plan.root_locators)
        credential_reissue_only = tuple(plan.credential_reissue_only)
        if set(root_locators) != REQUIRED_ROOT_ROLES:
            raise SourceViewAdapterError("declared_root_roles_incomplete")
        if len(set(credential_reissue_only)) != len(credential_reissue_only):
            raise SourceViewAdapterError("credential_exclusion_duplicate")
        for role, relative in credential_reissue_only:
            if role not in REQUIRED_ROOT_ROLES:
                raise SourceViewAdapterError("credential_exclusion_role_invalid")
            _relative_parts(relative)
        if not plan.view_root.is_absolute():
            raise SourceViewAdapterError("view_path_must_be_absolute")
        self.fixture_id = plan.fixture_id
        self.view_id = plan.view_id
        self.view_root = plan.view_root
        self.source_volume_uuid = plan.source_volume_uuid
        self.source_root_id = plan.source_root_id
        self.root_locators = root_locators
        self.credential_reissue_only = credential_reissue_only
        self.plan_sha256 = sha256(
            canonical_json(
                {
                    "schema": "brain-host-synthetic-source-view-plan-v1",
                    "fixture_id": self.fixture_id,
                    "view_id": self.view_id,
                    "source_volume_uuid": self.source_volume_uuid,
                    "source_root_id": self.source_root_id,
                    "root_locators": self.root_locators,
                    "credential_reissue_only": list(self.credential_reissue_only),
                }
            )
        )
        self.view_fd = _open_absolute_directory_nofollow(self.view_root)
        self._view_identity = _root_identity(os.fstat(self.view_fd))
        self._device = self._view_identity["device"]
        self._root_fds: dict[str, int] = {}
        self._root_identities: dict[str, dict[str, int]] = {}
        self._closed = False
        self.opened_at_utc = datetime.now(UTC).isoformat(timespec="seconds")
        try:
            for role in sorted(REQUIRED_ROOT_ROLES):
                root_fd = self._open_declared_root(self.root_locators[role])
                self._root_fds[role] = root_fd
                self._root_identities[role] = _root_identity(os.fstat(root_fd))
            self.revalidate()
        except BaseException:
            self.close()
            raise

    def _open_declared_root(self, locator: str) -> int:
        parts = _relative_parts(locator)
        current = os.dup(self.view_fd)
        try:
            for component in parts:
                child = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current,
                )
                st = os.fstat(child)
                if st.st_dev != self._device:
                    os.close(child)
                    raise SourceViewAdapterError("declared_root_crosses_device")
                os.close(current)
                current = child
            return current
        except OSError as exc:
            os.close(current)
            raise SourceViewAdapterError("declared_root_open_failed") from exc
        except BaseException:
            os.close(current)
            raise

    def revalidate(self) -> None:
        if self._closed:
            raise SourceViewAdapterError("source_view_released")
        held_view = os.fstat(self.view_fd)
        if _root_identity(held_view) != self._view_identity:
            raise SourceViewAdapterError("held_view_identity_changed")
        current_view_fd = _open_absolute_directory_nofollow(self.view_root)
        try:
            current_view = _root_identity(os.fstat(current_view_fd))
            if current_view != self._view_identity:
                raise SourceViewAdapterError("view_path_replaced")
        finally:
            os.close(current_view_fd)
        for role in sorted(REQUIRED_ROOT_ROLES):
            held_root = _root_identity(os.fstat(self._root_fds[role]))
            current_fd = self._open_declared_root(self.root_locators[role])
            try:
                if (
                    held_root != self._root_identities[role]
                    or _root_identity(os.fstat(current_fd)) != held_root
                ):
                    raise SourceViewAdapterError("declared_root_replaced")
            finally:
                os.close(current_fd)

    def root_path(self, role: str) -> Path:
        if self._closed:
            raise SourceViewAdapterError("source_view_released")
        try:
            locator = self.root_locators[role]
            _relative_parts(locator)
        except KeyError as exc:
            raise SourceViewAdapterError("declared_root_role_unknown") from exc
        self.revalidate()
        return self.view_root.joinpath(*locator.split("/"))

    def root_identity(self, role: str) -> dict[str, int]:
        if self._closed:
            raise SourceViewAdapterError("source_view_released")
        try:
            return dict(self._root_identities[role])
        except KeyError as exc:
            raise SourceViewAdapterError("declared_root_role_unknown") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in self._root_fds.values():
            os.close(fd)
        self._root_fds.clear()
        os.close(self.view_fd)

    def __enter__(self) -> HeldSyntheticView:
        if self._closed:
            raise SourceViewAdapterError("source_view_released")
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def reconcile_synthetic_capture(
    writer: Any,
    plan: SyntheticViewPlan,
    proof: dict[str, Any],
    *,
    expected_volume_uuid: str,
    expected_root_id: str,
    expected_source_volume_uuid: str,
    expected_source_root_id: str,
    revalidate_identity: Any,
) -> dict[str, Any]:
    """Reconcile a prior synthetic capture against held view/root identities.

    Existing v3 proof readers reopen paths, so this reports post-capture
    reconciliation. It does not claim that capture bytes were read from these
    descriptors, nor that the synthetic view was immutable or read-only.
    """
    if not isinstance(proof, dict):
        raise SourceViewAdapterError("source_proof_invalid")
    if proof.get("scenario") != "synthetic":
        raise SourceViewAdapterError("apfs_snapshot_attestation_unavailable")
    source_view = proof.get("source_view")
    if not isinstance(source_view, dict) or (
        source_view.get("kind") != "synthetic_readonly_view_fixture"
        or source_view.get("volume_uuid") != expected_source_volume_uuid
        or source_view.get("root_id") != expected_source_root_id
        or source_view.get("view_id") != plan.view_id
        or plan.source_volume_uuid != expected_source_volume_uuid
        or plan.source_root_id != expected_source_root_id
    ):
        raise SourceViewAdapterError("synthetic_source_identity_mismatch")
    for key in ("apfs_snapshot_attested", "T_qualified", "host_closure"):
        if key in proof:
            raise SourceViewAdapterError("caller_qualification_fields_forbidden")

    opened_at = datetime.now(UTC).isoformat(timespec="seconds")
    with HeldSyntheticView(plan) as view:
        view.revalidate()
        tree_roles = TREE_ROOT_ROLES
        tree_roots = {role: view.root_path(role) for role in tree_roles}
        proof_result = source_proof.verify_synthetic_source_view_proof(
            writer,
            proof,
            synthetic_source_roots=tree_roots,
            expected_volume_uuid=expected_volume_uuid,
            expected_root_id=expected_root_id,
            expected_source_volume_uuid=expected_source_volume_uuid,
            expected_source_root_id=expected_source_root_id,
            revalidate_identity=revalidate_identity,
        )
        view.revalidate()
        if proof_result["synthetic_tree_source_bindings_verified"] != len(tree_roles):
            raise SourceViewAdapterError("tree_root_reconciliation_incomplete")
        member_by_role = {item["role"]: item for item in proof["member_bindings"]}
        ledger_by_role = {item["role"]: item for item in proof["closure_ledger"]}
        credential_dispositions = []
        for role, relative in view.credential_reissue_only:
            member = member_by_role.get(role)
            if not isinstance(member, dict) or member.get("format") != "tree-custody-v3":
                raise SourceViewAdapterError("credential_capture_member_unavailable")
            _member_pin, detail = source_proof.recovery_set._verify_member(writer, member)
            manifest = detail["manifest"]
            included = {
                row["path"]
                for row in source_proof.tree_v3._iter_records(
                    writer, manifest["entry_indexes"], kind="entries"
                )
            }
            excluded = {
                row["path"]: row
                for row in source_proof.tree_v3._iter_records(
                    writer, manifest["exclusion_indexes"], kind="exclusions"
                )
            }
            if relative in included:
                raise SourceViewAdapterError("credential_payload_included")
            exclusion = excluded.get(relative)
            if (
                exclusion is None
                or exclusion.get("exclusion_kind") != "file"
                or exclusion.get("entry_type") != "file"
                or exclusion.get("disposition") != "reauth_reissue"
            ):
                raise SourceViewAdapterError("credential_exact_file_exclusion_missing")
            credential_dispositions.append(
                {
                    "role": role,
                    "locator_sha256": sha256(
                        canonical_json({"role": role, "relative_path": relative})
                    ),
                    "state": "excluded_exact_file_reissue_only",
                }
            )
        root_bindings = {}
        for role in sorted(REQUIRED_ROOT_ROLES):
            member = member_by_role.get(role)
            ledger = ledger_by_role.get(role)
            if not isinstance(member, dict) or not isinstance(ledger, dict):
                raise SourceViewAdapterError("capture_member_binding_missing")
            capture_locator = source_proof.synthetic_root_locator_sha256(
                role,
                expected_source_volume_uuid,
                expected_source_root_id,
                view.root_identity(role),
            )
            plan_locator = sha256(
                canonical_json(
                    {
                        "schema": "brain-host-synthetic-relative-root-locator-v1",
                        "fixture_id": view.fixture_id,
                        "role": role,
                        "relative_locator": view.root_locators[role],
                        "source_volume_uuid": view.source_volume_uuid,
                        "source_root_id": view.source_root_id,
                        **view.root_identity(role),
                    }
                )
            )
            locator_matches_capture = (
                ledger.get("root_locator_sha256") == capture_locator if role in tree_roles else None
            )
            # The existing source proof binds locator hashes only for v3 tree
            # roles. Keep other roles explicitly unreconciled by that contract.
            if role in tree_roles and not locator_matches_capture:
                raise SourceViewAdapterError("capture_root_locator_mismatch")
            root_bindings[role] = {
                "plan_locator_sha256": plan_locator,
                "capture_locator_sha256": ledger.get("root_locator_sha256"),
                "locator_matches_capture": locator_matches_capture,
                "root_identity": view.root_identity(role),
                "capture_complete_sha256": member.get("complete_sha256"),
                "capture_manifest_sha256": member.get("manifest_sha256"),
                "included_count": ledger.get("included_count"),
                "excluded_count": ledger.get("excluded_count"),
                "unknown_count": ledger.get("unknown_count"),
                "capture_relation": (
                    "post_capture_path_reconciliation"
                    if role in tree_roles
                    else "artifact_verified_source_unbound"
                ),
            }
        view.revalidate()
        reconciliation_completed_at = datetime.now(UTC).isoformat(timespec="seconds")
        result = {
            "schema": SCHEMA,
            "scenario": "synthetic_fixture",
            "fixture_id": view.fixture_id,
            "view_id": view.view_id,
            "source_volume_uuid": plan.source_volume_uuid,
            "source_root_id": plan.source_root_id,
            "observed_at_utc": opened_at,
            "view_opened_at_utc": view.opened_at_utc,
            "reconciliation_completed_at_utc": reconciliation_completed_at,
            "plan_sha256": view.plan_sha256,
            "declared_capture_policy_sha256": proof["policy_sha256"],
            "declared_capture_tool_sha256": proof["capture_tool_sha256"],
            "declared_source_proof_adapter_sha256": proof["adapter_sha256"],
            "capture_times_ns_declared": proof["times_ns"],
            "set_id": proof_result["set_id"],
            "set_index_sha256": proof_result["set_index_sha256"],
            "view_identity": {
                "mount_device": view._view_identity["device"],
                "mount_inode": view._view_identity["inode"],
                "root_identity": view._view_identity,
                "mount_readonly_observed": (
                    None
                    if not hasattr(os, "ST_RDONLY")
                    else bool(os.fstatvfs(view.view_fd).f_flag & os.ST_RDONLY)
                ),
            },
            "root_bindings": root_bindings,
            "credential_dispositions": credential_dispositions,
            "credential_closure": "declared_exact_credential_exclusions_verified"
            if credential_dispositions
            else "unknown_no_exact_credential_scope_declared",
            "credential_scope_complete": False,
            "member_count": proof_result["member_count"],
            "declared_unknown_count": proof_result["declared_unknown_count"],
            "synthetic_tree_root_reconciliation_verified": True,
            "reconciled_tree_root_roles": sorted(tree_roles),
            "sqlite_source_family_verified_via_owner_root": proof_result[
                "synthetic_sqlite_source_family_verified"
            ],
            "capture_was_descriptor_sourced": False,
            "apfs_snapshot_attested": False,
            "T_qualified": False,
            "host_closure": False,
            "erase_ready": False,
        }
    result["evidence_sha256"] = hashlib.sha256(canonical_json(result)).hexdigest()
    return result
