#!/usr/bin/env python3
"""Same-view proof that the pinned verifier worktree leaf is absent.

This is deliberately separate from HeldDataT v1: v1 continues to report an
unresolved absent role.  The witness only proves leaf absence in one already
held read-only Data view; it does not establish cutoff T, source closure, or
recoverability of historical non-Git worktree state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.brain_host_held_data_t_v1 import (
    REQUIRED_ROOT_ROLES,
    ROOT_KINDS,
    ApprovedDataPlan,
    HeldDataT,
    RootDeclaration,
    approved_data_plan_sha256,
)
from tools.brain_senses_external_writer_v1 import canonical_json

SCHEMA = "brain-host-held-data-absence-witness-v2"
EXPECTED_ROLE = "verifier_worktree"
EXPECTED_COMMIT = "f330999930afdb29485b89908fb275736587a815"
HISTORICAL_STATE = "unknown_unrecoverable"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_DEVICE = re.compile(r"disk[0-9]+s[0-9]+\Z")
PLAN_SCHEMA = "brain-host-held-data-absence-plan-v2"
MAX_PLAN_BYTES = 128 * 1024
_PLAN_FIELDS = {
    "schema",
    "approval_id",
    "data_volume_uuid",
    "data_device_identifier",
    "data_container_reference",
    "data_volume_group_id",
    "root_roster",
    "verifier_worktree_locator",
    "expected_fetchable_commit",
    "remote_fetchability_evidence_sha256",
    "historical_non_git_state",
}
_ROOT_FIELDS = {"present", "relative_locator", "kind", "unresolved_reason"}


class AbsenceWitnessError(ValueError):
    """The pinned same-view absence condition cannot be proved."""


@dataclass(frozen=True)
class AbsenceGapPlanV2:
    """Parsed v2 plan whose canonical digest is pinned by the caller."""

    document: Mapping[str, Any]
    plan_sha256: str
    held_data_plan: ApprovedDataPlan
    held_data_plan_sha256: str
    verifier_worktree_locator: str
    _validation_token: object = field(repr=False, compare=False)


_PLAN_VALIDATION_TOKEN = object()


def _json_no_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AbsenceWitnessError("absence_plan_duplicate_key")
        result[key] = value
    return result


def _read_plan_bytes(path: Path) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise AbsenceWitnessError("absence_plan_open_failed") from exc
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or not 0 < metadata.st_size <= MAX_PLAN_BYTES
        ):
            raise AbsenceWitnessError("absence_plan_file_invalid")
        chunks: list[bytes] = []
        remaining = MAX_PLAN_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(8192, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != metadata.st_size or len(payload) > MAX_PLAN_BYTES:
            raise AbsenceWitnessError("absence_plan_size_changed")
        return payload
    except OSError as exc:
        raise AbsenceWitnessError("absence_plan_read_failed") from exc
    finally:
        os.close(fd)


def load_absence_gap_plan_v2(path: Path, *, expected_plan_sha256: str) -> AbsenceGapPlanV2:
    """Load a strict v2 plan and require the independently supplied SHA-256 pin."""
    if not isinstance(expected_plan_sha256, str) or not _SHA256.fullmatch(expected_plan_sha256):
        raise AbsenceWitnessError("absence_plan_pin_invalid")
    raw = _read_plan_bytes(path)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_json_no_duplicate_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                AbsenceWitnessError("absence_plan_nonstandard_constant")
            ),
        )
    except AbsenceWitnessError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise AbsenceWitnessError("absence_plan_json_invalid") from exc
    if not isinstance(value, dict) or set(value) != _PLAN_FIELDS:
        raise AbsenceWitnessError("absence_plan_schema_fields_invalid")
    if value.get("schema") != PLAN_SCHEMA:
        raise AbsenceWitnessError("absence_plan_schema_unsupported")
    plan_sha256 = hashlib.sha256(canonical_json(value)).hexdigest()
    if plan_sha256 != expected_plan_sha256:
        raise AbsenceWitnessError("absence_plan_sha256_mismatch")

    roster_doc = value["root_roster"]
    if not isinstance(roster_doc, Mapping) or set(roster_doc) != REQUIRED_ROOT_ROLES:
        raise AbsenceWitnessError("absence_plan_roster_invalid")
    declarations: dict[str, RootDeclaration] = {}
    for role, row in roster_doc.items():
        if not isinstance(row, Mapping) or set(row) != _ROOT_FIELDS:
            raise AbsenceWitnessError("absence_plan_role_fields_invalid")
        present = row["present"]
        if type(present) is not bool:
            raise AbsenceWitnessError("absence_plan_role_presence_invalid")
        locator, kind, unresolved = (
            row["relative_locator"],
            row["kind"],
            row["unresolved_reason"],
        )
        if present:
            if not isinstance(locator, str) or kind != ROOT_KINDS[role] or unresolved is not None:
                raise AbsenceWitnessError("absence_plan_present_role_invalid")
            _parts(locator)
        elif locator is not None or kind is not None or not isinstance(unresolved, str):
            raise AbsenceWitnessError("absence_plan_absent_role_invalid")
        declarations[role] = RootDeclaration(present, locator, kind, unresolved)

    verifier = declarations[EXPECTED_ROLE]
    if (
        verifier.present
        or verifier.relative_locator is not None
        or verifier.kind is not None
        or verifier.unresolved_reason != "verifier_worktree_absent_unresolved"
    ):
        raise AbsenceWitnessError("absence_plan_verifier_role_invalid")
    if any(not declarations[role].present for role in REQUIRED_ROOT_ROLES if role != EXPECTED_ROLE):
        raise AbsenceWitnessError("absence_plan_nonverifier_role_absent")
    locator = value["verifier_worktree_locator"]
    _parts(locator)
    if value["expected_fetchable_commit"] != EXPECTED_COMMIT:
        raise AbsenceWitnessError("absence_plan_commit_invalid")
    if value["historical_non_git_state"] != HISTORICAL_STATE:
        raise AbsenceWitnessError("absence_plan_history_disposition_invalid")
    if not isinstance(value["remote_fetchability_evidence_sha256"], str) or not _SHA256.fullmatch(
        value["remote_fetchability_evidence_sha256"]
    ):
        raise AbsenceWitnessError("absence_plan_fetchability_evidence_invalid")
    for name in ("data_volume_uuid", "data_volume_group_id"):
        if not isinstance(value[name], str) or not _UUID.fullmatch(value[name].lower()):
            raise AbsenceWitnessError("absence_plan_data_identity_invalid")
    if not isinstance(value["data_device_identifier"], str) or not _DEVICE.fullmatch(
        value["data_device_identifier"]
    ):
        raise AbsenceWitnessError("absence_plan_data_identity_invalid")
    if not isinstance(value["data_container_reference"], str) or not re.fullmatch(
        r"disk[0-9]+", value["data_container_reference"]
    ):
        raise AbsenceWitnessError("absence_plan_data_identity_invalid")
    if not isinstance(value["approval_id"], str) or not value["approval_id"].strip():
        raise AbsenceWitnessError("absence_plan_approval_missing")
    held_plan = ApprovedDataPlan(
        approval_id=value["approval_id"],
        data_volume_uuid=value["data_volume_uuid"],
        data_device_identifier=value["data_device_identifier"],
        data_container_reference=value["data_container_reference"],
        data_volume_group_id=value["data_volume_group_id"],
        root_roster=declarations,
    )
    return AbsenceGapPlanV2(
        document=value,
        plan_sha256=plan_sha256,
        held_data_plan=held_plan,
        held_data_plan_sha256=approved_data_plan_sha256(held_plan),
        verifier_worktree_locator=locator,
        _validation_token=_PLAN_VALIDATION_TOKEN,
    )


def is_validated_absence_gap_plan_v2(value: object) -> bool:
    return (
        isinstance(value, AbsenceGapPlanV2)
        and value._validation_token is _PLAN_VALIDATION_TOKEN
        and hashlib.sha256(canonical_json(value.document)).hexdigest() == value.plan_sha256
        and value.document.get("verifier_worktree_locator") == value.verifier_worktree_locator
        and approved_data_plan_sha256(value.held_data_plan) == value.held_data_plan_sha256
    )


def _parts(locator: str) -> tuple[str, ...]:
    if (
        not isinstance(locator, str)
        or not locator
        or locator.startswith("/")
        or "\\" in locator
        or "\x00" in locator
    ):
        raise AbsenceWitnessError("absence_locator_invalid")
    parts = tuple(locator.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise AbsenceWitnessError("absence_locator_invalid")
    return parts


def _identity(fd: int) -> dict[str, int]:
    st = os.fstat(fd)
    return {"device": int(st.st_dev), "inode": int(st.st_ino)}


def _walk_parent(view: HeldDataT, parts: tuple[str, ...]) -> tuple[int, list[dict[str, int]]]:
    """Open each ancestor from the held Data fd without following symlinks."""
    current = os.dup(view.mount_fd)
    chain = [_identity(current)]
    try:
        data_device = chain[0]["device"]
        for component in parts[:-1]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            identity = _identity(child)
            if identity["device"] != data_device:
                os.close(child)
                raise AbsenceWitnessError("absence_parent_device_changed")
            chain.append(identity)
            os.close(current)
            current = child
        return current, chain
    except AbsenceWitnessError:
        os.close(current)
        raise
    except OSError as exc:
        os.close(current)
        # Missing ancestors, symlinks/ELOOP, and permission failures are all
        # distinct from a leaf ENOENT and must never become an absence witness.
        raise AbsenceWitnessError("absence_parent_unavailable_or_untrusted") from exc


def _leaf_absent(parent_fd: int, leaf: str) -> None:
    try:
        os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise AbsenceWitnessError("absence_leaf_probe_failed") from exc
    raise AbsenceWitnessError("absence_leaf_present_or_symlink")


def build_absence_witness_v2(
    view: HeldDataT,
    *,
    plan: AbsenceGapPlanV2,
) -> dict[str, Any]:
    """Fresh-check the externally hash-pinned plan's absent leaf on one view.

    Ancestors are opened and identity-pinned on the first traversal, then the
    same exact path is traversed again after full HeldDataT revalidation.  The
    leaf must return ENOENT both times; no parent is synthesized or followed
    through a symlink.  The returned record is evidence, not a T qualification.
    """
    if (
        not isinstance(view, HeldDataT)
        or view._closed
        or not isinstance(plan, AbsenceGapPlanV2)
        or plan._validation_token is not _PLAN_VALIDATION_TOKEN
    ):
        raise AbsenceWitnessError("absence_requires_open_real_held_view")
    if (
        view.plan != plan.held_data_plan
        or view.trusted_plan_sha256 != plan.held_data_plan_sha256
        or approved_data_plan_sha256(plan.held_data_plan) != plan.held_data_plan_sha256
        or hashlib.sha256(canonical_json(plan.document)).hexdigest() != plan.plan_sha256
        or plan.document.get("verifier_worktree_locator") != plan.verifier_worktree_locator
    ):
        raise AbsenceWitnessError("absence_held_plan_pin_mismatch")
    if set(view.plan.root_roster) != REQUIRED_ROOT_ROLES:
        raise AbsenceWitnessError("absence_required_roster_invalid")
    declaration = view.plan.root_roster.get(EXPECTED_ROLE)
    if (
        declaration is None
        or declaration.present is not False
        or declaration.relative_locator is not None
        or declaration.kind is not None
    ):
        raise AbsenceWitnessError("absence_role_not_declared_absent_unresolved")
    relative_locator = plan.verifier_worktree_locator
    parts = _parts(relative_locator)
    expected_commit = plan.document["expected_fetchable_commit"]
    if expected_commit != EXPECTED_COMMIT or not _COMMIT.fullmatch(expected_commit):
        raise AbsenceWitnessError("absence_commit_pin_invalid")
    remote_fetchability_evidence_sha256 = plan.document["remote_fetchability_evidence_sha256"]
    if not _SHA256.fullmatch(remote_fetchability_evidence_sha256):
        raise AbsenceWitnessError("absence_fetchability_evidence_pin_invalid")
    if not isinstance(declaration.unresolved_reason, str) or not re.fullmatch(
        r"[a-z][a-z0-9_]{0,63}", declaration.unresolved_reason
    ):
        raise AbsenceWitnessError("absence_role_reason_mismatch")

    try:
        view.revalidate()
        first_parent, first_chain = _walk_parent(view, parts)
        try:
            _leaf_absent(first_parent, parts[-1])
            first_parent_identity = _identity(first_parent)
        finally:
            os.close(first_parent)

        view.revalidate()
        second_parent, second_chain = _walk_parent(view, parts)
        try:
            _leaf_absent(second_parent, parts[-1])
            second_parent_identity = _identity(second_parent)
        finally:
            os.close(second_parent)
        view.revalidate()
    except AbsenceWitnessError:
        raise
    except Exception as exc:
        raise AbsenceWitnessError("absence_view_revalidation_failed") from exc

    if (
        first_chain != second_chain
        or first_parent_identity != second_parent_identity
        or first_chain[-1] != first_parent_identity
        or second_chain[-1] != second_parent_identity
    ):
        raise AbsenceWitnessError("absence_parent_identity_drift")

    body: dict[str, Any] = {
        "schema": SCHEMA,
        "role": EXPECTED_ROLE,
        "relative_locator": relative_locator,
        "absence_plan_v2_sha256": plan.plan_sha256,
        "held_data_plan_sha256": view.trusted_plan_sha256,
        "data_volume_uuid": view.plan.data_volume_uuid.lower(),
        "data_device_identifier": view.plan.data_device_identifier,
        "data_volume_group_id": view.plan.data_volume_group_id.lower(),
        "parent_identity_chain": first_chain,
        "leaf_observation": "source_absent_on_held_readonly_view",
        "expected_fetchable_commit": expected_commit,
        "remote_fetchability_evidence_sha256": remote_fetchability_evidence_sha256,
        "historical_non_git_state": HISTORICAL_STATE,
        "synthetic_backend": view.synthetic,
        "root_closure": False,
        "full_restore_qualified": False,
        "activation_authorized": False,
        "erase_ready": False,
        "T_qualified": False,
        "K_qualified": False,
    }
    body["witness_sha256"] = hashlib.sha256(canonical_json(body)).hexdigest()
    return body


def validate_absence_witness_v2(value: Mapping[str, Any]) -> None:
    """Strictly validate the exact v2 witness envelope; reject legacy extras."""
    fields = {
        "schema",
        "role",
        "relative_locator",
        "absence_plan_v2_sha256",
        "held_data_plan_sha256",
        "data_volume_uuid",
        "data_device_identifier",
        "data_volume_group_id",
        "parent_identity_chain",
        "leaf_observation",
        "expected_fetchable_commit",
        "remote_fetchability_evidence_sha256",
        "historical_non_git_state",
        "synthetic_backend",
        "root_closure",
        "full_restore_qualified",
        "activation_authorized",
        "erase_ready",
        "T_qualified",
        "K_qualified",
        "witness_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise AbsenceWitnessError("absence_witness_schema_fields_invalid")
    if (
        value["schema"] != SCHEMA
        or value["role"] != EXPECTED_ROLE
        or value["leaf_observation"] != "source_absent_on_held_readonly_view"
        or value["expected_fetchable_commit"] != EXPECTED_COMMIT
        or value["historical_non_git_state"] != HISTORICAL_STATE
        or type(value["synthetic_backend"]) is not bool
        or any(
            value[name] is not False
            for name in (
                "root_closure",
                "full_restore_qualified",
                "activation_authorized",
                "erase_ready",
                "T_qualified",
                "K_qualified",
            )
        )
    ):
        raise AbsenceWitnessError("absence_witness_policy_invalid")
    for name in (
        "absence_plan_v2_sha256",
        "held_data_plan_sha256",
        "remote_fetchability_evidence_sha256",
    ):
        if not isinstance(value[name], str) or not _SHA256.fullmatch(value[name]):
            raise AbsenceWitnessError("absence_witness_digest_invalid")
    if not isinstance(value["witness_sha256"], str) or not _SHA256.fullmatch(
        value["witness_sha256"]
    ):
        raise AbsenceWitnessError("absence_witness_digest_invalid")
    if (
        not isinstance(value["data_volume_uuid"], str)
        or not _UUID.fullmatch(value["data_volume_uuid"])
        or not isinstance(value["data_volume_group_id"], str)
        or not _UUID.fullmatch(value["data_volume_group_id"])
        or not isinstance(value["data_device_identifier"], str)
        or not _DEVICE.fullmatch(value["data_device_identifier"])
    ):
        raise AbsenceWitnessError("absence_witness_data_identity_invalid")
    _parts(value["relative_locator"])
    if not isinstance(value["parent_identity_chain"], list) or not value["parent_identity_chain"]:
        raise AbsenceWitnessError("absence_witness_parent_chain_invalid")
    for row in value["parent_identity_chain"]:
        if (
            not isinstance(row, Mapping)
            or set(row) != {"device", "inode"}
            or type(row["device"]) is not int
            or type(row["inode"]) is not int
        ):
            raise AbsenceWitnessError("absence_witness_parent_chain_invalid")
    if len({row["device"] for row in value["parent_identity_chain"]}) != 1:
        raise AbsenceWitnessError("absence_witness_parent_device_invalid")
    canonical_body = {key: item for key, item in value.items() if key != "witness_sha256"}
    if hashlib.sha256(canonical_json(canonical_body)).hexdigest() != value["witness_sha256"]:
        raise AbsenceWitnessError("absence_witness_digest_mismatch")
