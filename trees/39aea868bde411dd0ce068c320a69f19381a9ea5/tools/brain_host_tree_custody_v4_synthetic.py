"""Exercise the v4 capture shape using disposable synthetic bytes only.

This module has no source-tree reader and no persistent-store API. It accepts
already supplied fixture metadata and bytes, writes to a private temporary
directory, verifies through a fresh descriptor, writes a synthetic-only
completion receipt, and removes the whole fixture when it returns.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from tools.brain_host_tree_custody_v4 import PLAN_SCHEMA, verify_plan
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SYNTHETIC_CAPTURE_SCHEMA = "brain-host-tree-custody-v4-synthetic-capture"
SYNTHETIC_COMPLETE_SCHEMA = "brain-host-tree-custody-v4-synthetic-complete"
SYNTHETIC_RECEIPT_SCHEMA = "brain-host-tree-custody-v4-synthetic-rehearsal"
MAX_FIXTURE_FILE_BYTES = 16 * 1024 * 1024


class SyntheticCaptureError(ValueError):
    """A synthetic v4 capture fixture is malformed or fails readback."""


def _member_key(row: dict[str, Any], *, root: bool = False) -> str:
    if root:
        return f"root:{row['root_key']}"
    return f"entry:{row['root_key']}:{row['path']}"


def _metadata_value_row(
    row: dict[str, Any], member_key: str, values: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(values, dict) or set(values) != {"xattrs", "acl_bytes", "flags_value"}:
        raise SyntheticCaptureError("metadata_value_record_invalid")
    if not isinstance(values["xattrs"], dict):
        raise SyntheticCaptureError("xattr_values_invalid")
    names = row["xattr_names_b64"]
    if row["xattr_inventory_state"] != "observed" or set(values["xattrs"]) != set(names):
        raise SyntheticCaptureError("xattr_inventory_not_captured_exactly")
    xattrs = []
    for name_b64 in names:
        value = values["xattrs"][name_b64]
        if not isinstance(value, bytes):
            raise SyntheticCaptureError("xattr_value_not_bytes")
        xattrs.append(
            {
                "name_b64": name_b64,
                "value_b64": base64.b64encode(value).decode("ascii"),
                "value_sha256": sha256(value),
            }
        )
    acl_state = row["acl_state"]
    acl_bytes = values["acl_bytes"]
    if acl_state == "value_required":
        if not isinstance(acl_bytes, bytes):
            raise SyntheticCaptureError("acl_value_not_bytes")
        acl_value: dict[str, Any] = {
            "state": "value_required",
            "value_b64": base64.b64encode(acl_bytes).decode("ascii"),
            "value_sha256": sha256(acl_bytes),
        }
    elif acl_state == "absent" and acl_bytes is None:
        acl_value = {"state": "absent", "value_b64": None, "value_sha256": None}
    else:
        raise SyntheticCaptureError("acl_state_unresolved")
    flags_state = row["flags_state"]
    flags_value = values["flags_value"]
    if flags_state == "value_required":
        if type(flags_value) is not int or flags_value < 0:
            raise SyntheticCaptureError("flags_value_invalid")
        flags_record: dict[str, Any] = {"state": "value_required", "value": flags_value}
    elif flags_state == "absent" and flags_value is None:
        flags_record = {"state": "absent", "value": None}
    else:
        raise SyntheticCaptureError("flags_state_unresolved")
    return {
        "member_key": member_key,
        "xattrs": xattrs,
        "acl": acl_value,
        "flags": flags_record,
    }


def _new_writer(path: Path) -> DescriptorWriter:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return DescriptorWriter(fd)
    finally:
        os.close(fd)


def rehearse_synthetic_capture(
    plan: dict[str, Any],
    *,
    file_payloads: dict[str, bytes],
    metadata_values: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Write and reread a synthetic-only group; never returns a usable archive.

    `file_payloads` is keyed by the planner's hardlink group ID. Metadata
    members are keyed by `root:<key>` or `entry:<root_key>:<path>`. Every input
    is fixture data supplied by the caller; this function performs no source
    filesystem reads and proves no source view, T, real custody, or restoration.
    """
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise SyntheticCaptureError("metadata_plan_contract_invalid")
    try:
        plan = verify_plan(plan)
    except ValueError as exc:
        raise SyntheticCaptureError("metadata_plan_internal_consistency_failed") from exc
    if (
        plan.get("format_schema") != "brain-host-tree-custody-v4"
        or plan["source_view"]["authority"] != "unqualified_caller_metadata"
        or plan.get("complete_marker_publishable") is not False
    ):
        raise SyntheticCaptureError("metadata_plan_contract_invalid")
    if plan.get("unsupported_metadata_member_count") != 0:
        raise SyntheticCaptureError("unsupported_metadata_present")

    roots = plan.get("root_roster")
    entries = plan.get("entries")
    if not isinstance(roots, list) or len(roots) != 3 or not isinstance(entries, list):
        raise SyntheticCaptureError("metadata_plan_roster_invalid")
    included_entries = [row for row in entries if row.get("disposition") == "included_opaque"]
    metadata_members = [(_member_key(row, root=True), row) for row in roots]
    metadata_members.extend((_member_key(row), row) for row in included_entries)
    expected_metadata_keys = {key for key, _row in metadata_members}
    if not isinstance(metadata_values, dict):
        raise SyntheticCaptureError("metadata_member_roster_mismatch")
    try:
        metadata_values = copy.deepcopy(metadata_values)
    except (TypeError, ValueError) as exc:
        raise SyntheticCaptureError("metadata_value_snapshot_failed") from exc
    if set(metadata_values) != expected_metadata_keys:
        raise SyntheticCaptureError("metadata_member_roster_mismatch")
    metadata_records = [
        _metadata_value_row(row, key, metadata_values[key]) for key, row in metadata_members
    ]

    included_files = [row for row in included_entries if row.get("kind") == "file"]
    file_groups: dict[str, list[dict[str, Any]]] = {}
    for row in included_files:
        group_id = row.get("hardlink_group_id")
        if not isinstance(group_id, str) or len(group_id) != 64:
            raise SyntheticCaptureError("hardlink_group_identity_invalid")
        file_groups.setdefault(group_id, []).append(row)
    if not isinstance(file_payloads, dict):
        raise SyntheticCaptureError("file_payload_roster_mismatch")
    file_payloads = dict(file_payloads)
    if set(file_payloads) != set(file_groups):
        raise SyntheticCaptureError("file_payload_roster_mismatch")

    objects: dict[str, bytes] = {}
    object_rows = []
    for group_id, aliases in sorted(file_groups.items()):
        payload = file_payloads[group_id]
        if not isinstance(payload, bytes) or len(payload) > MAX_FIXTURE_FILE_BYTES:
            raise SyntheticCaptureError("fixture_file_payload_invalid")
        if any(row["size"] != len(payload) for row in aliases):
            raise SyntheticCaptureError("fixture_file_size_mismatch")
        object_path = f"objects/{group_id}.bin"
        objects[object_path] = payload
        object_rows.append(
            {
                "hardlink_group_id": group_id,
                "path": object_path,
                "size_bytes": len(payload),
                "sha256": sha256(payload),
                "alias_count": len(aliases),
            }
        )

    manifest = {
        "schema": SYNTHETIC_CAPTURE_SCHEMA,
        "fixture_only": True,
        "capture_scope": "synthetic_fixture_input_only",
        "membership_scope": "supplied_fixture_roster_only_unqualified",
        "artifact_membership_scope": "exact_regular_file_artifacts_only",
        "source_view_qualified": False,
        "t_cutoff_qualified": False,
        "classification": plan["classification"],
        "semantic_lineage": "unproven",
        "source_metadata_observation_restore_equality": False,
        "plan_sha256": sha256(canonical_json(plan)),
        "root_roster": roots,
        "entries": entries,
        "metadata_values": metadata_records,
        "objects": object_rows,
        "excluded_entry_count": plan["reissue_excluded_entry_count"],
        "unaccounted_hardlink_group_count": plan["unaccounted_hardlink_group_count"],
    }
    manifest_bytes = canonical_json(manifest)
    manifest_digest = sha256(manifest_bytes)
    canonical_manifest = json.loads(manifest_bytes)
    complete = {
        "schema": SYNTHETIC_COMPLETE_SCHEMA,
        "fixture_only": True,
        "semantics": "supplied_roster_fixture_artifacts_verified_with_synthetic_readback",
        "manifest_sha256": manifest_digest,
        "object_count": len(object_rows),
        "metadata_member_count": len(metadata_records),
        "activation_authorized": False,
        "source_view_qualified": False,
        "real_complete_authority": False,
    }
    complete_bytes = canonical_json(complete)

    with tempfile.TemporaryDirectory(prefix="brain-host-custody-v4-") as temporary:
        root = Path(temporary)
        os.chmod(root, 0o700)
        with _new_writer(root) as writer:
            writer.mkdir("objects")
            for path, payload in sorted(objects.items()):
                writer.create(path, payload)
            writer.create("MANIFEST.json", manifest_bytes)

        expected_before_complete = sorted([*objects, "MANIFEST.json"])
        with _new_writer(root) as reader:
            if reader.list_files() != expected_before_complete:
                raise SyntheticCaptureError("precomplete_membership_mismatch")
            reread_manifest = reader.read_bytes("MANIFEST.json", max_bytes=64 * 1024 * 1024)
            if reread_manifest != manifest_bytes or sha256(reread_manifest) != manifest_digest:
                raise SyntheticCaptureError("manifest_readback_mismatch")
            decoded_manifest = json.loads(reread_manifest)
            if decoded_manifest != canonical_manifest:
                raise SyntheticCaptureError("manifest_semantic_readback_mismatch")
            for path, expected in objects.items():
                actual = reader.read_bytes(path, max_bytes=MAX_FIXTURE_FILE_BYTES)
                if actual != expected or sha256(actual) != sha256(expected):
                    raise SyntheticCaptureError("object_readback_mismatch")

        with _new_writer(root) as writer:
            writer.create("SYNTHETIC-COMPLETE.json", complete_bytes)

        expected_final = sorted([*objects, "MANIFEST.json", "SYNTHETIC-COMPLETE.json"])
        with _new_writer(root) as reader:
            if reader.list_files() != expected_final:
                raise SyntheticCaptureError("final_membership_mismatch")
            actual_complete = reader.read_bytes("SYNTHETIC-COMPLETE.json")
            if actual_complete != complete_bytes or sha256(actual_complete) != sha256(
                complete_bytes
            ):
                raise SyntheticCaptureError("synthetic_complete_readback_mismatch")
            actual_manifest = reader.read_bytes("MANIFEST.json", max_bytes=64 * 1024 * 1024)
            if sha256(actual_manifest) != manifest_digest:
                raise SyntheticCaptureError("final_manifest_hash_mismatch")
            for path, expected in objects.items():
                actual = reader.read_bytes(path, max_bytes=MAX_FIXTURE_FILE_BYTES)
                if actual != expected or sha256(actual) != sha256(expected):
                    raise SyntheticCaptureError("final_object_readback_mismatch")

    return {
        "schema": SYNTHETIC_RECEIPT_SCHEMA,
        "status": "synthetic_artifact_regular_files_verified",
        "manifest_sha256": manifest_digest,
        "synthetic_complete_sha256": sha256(complete_bytes),
        "object_count": len(object_rows),
        "metadata_member_count": len(metadata_records),
        "source_view_qualified": False,
        "t_cutoff_qualified": False,
        "real_complete_authority": False,
        "semantic_lineage": "unproven",
        "activation_authorized": False,
        "restore_qualified": False,
        "factory_reset_ready": False,
    }
