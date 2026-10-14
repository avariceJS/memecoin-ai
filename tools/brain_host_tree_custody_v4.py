#!/usr/bin/env python3
"""Plan a private three-root opaque custody group without reading payloads.

This phase-one contract reconciles metadata and aliases only. It never writes
capture artifacts or a COMPLETE marker and never qualifies source-view T.
The returned raw paths and symlink text are private; public reports must use a
separate aggregate-only projection.
"""

from __future__ import annotations

import base64
import hashlib
import json
import posixpath
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any

SCHEMA = "brain-host-tree-custody-v4"
PLAN_SCHEMA = "brain-host-tree-custody-v4-plan"
CLASSIFICATION = "private_opaque_unresolved_backup_required"
SEMANTIC_LINEAGE = "unproven"
COMPLETE_SEMANTICS = "declared_policy_capture_verified"
ROOT_COUNT = 3
HEX256 = re.compile(r"[0-9a-f]{64}\Z")


class TreeCustodyV4Error(ValueError):
    """The v4 source roster or metadata plan is malformed or incomplete."""


def _relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise TreeCustodyV4Error("path_invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(p in {"", ".", ".."} for p in path.parts)
    ):
        raise TreeCustodyV4Error("path_invalid")
    return value


def _identity_int(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise TreeCustodyV4Error(f"{label}_invalid")
    return value


def _xattr_inventory(names: object, state: object, label: str) -> list[str]:
    if not isinstance(state, str) or state not in {"observed", "unsupported", "unknown"}:
        raise TreeCustodyV4Error(f"{label}_xattr_inventory_state_invalid")
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise TreeCustodyV4Error(f"{label}_xattr_inventory_invalid")
    try:
        decoded = [base64.b64decode(name, validate=True) for name in names]
    except (ValueError, TypeError) as exc:
        raise TreeCustodyV4Error(f"{label}_xattr_name_invalid") from exc
    if (
        any(not name or b"\x00" in name for name in decoded)
        or decoded != sorted(set(decoded))
        or any(
            base64.b64encode(raw).decode("ascii") != encoded
            for raw, encoded in zip(decoded, names, strict=True)
        )
        or (state != "observed" and names)
    ):
        raise TreeCustodyV4Error(f"{label}_xattr_name_invalid")
    return list(names)


def _normalize_roots(roots: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if (
        not isinstance(roots, Sequence)
        or isinstance(roots, (str, bytes))
        or len(roots) != ROOT_COUNT
    ):
        raise TreeCustodyV4Error("root_roster_incomplete")
    normalized = []
    seen: set[str] = set()
    devices: set[int] = set()
    inodes: set[tuple[int, int]] = set()
    for item in roots:
        if not isinstance(item, dict) or set(item) != {
            "root_key",
            "source_locator",
            "device",
            "inode",
            "nlink",
            "mode",
            "uid",
            "gid",
            "size",
            "mtime_ns",
            "ctime_ns",
            "xattr_names_b64",
            "xattr_inventory_state",
            "acl_state",
            "flags_state",
        }:
            raise TreeCustodyV4Error("root_record_invalid")
        key = item["root_key"]
        locator = _relative_path(item["source_locator"])
        if not isinstance(key, str) or not re.fullmatch(r"root-[a-z0-9]{1,12}", key) or key in seen:
            raise TreeCustodyV4Error("root_identity_invalid")
        locator_hash = hashlib.sha256(locator.encode("utf-8")).hexdigest()
        device = _identity_int(item["device"], "root_device")
        inode = _identity_int(item["inode"], "root_inode", minimum=1)
        nlink = _identity_int(item["nlink"], "root_nlink", minimum=1)
        mode = _identity_int(item["mode"], "root_mode")
        uid = _identity_int(item["uid"], "root_uid")
        gid = _identity_int(item["gid"], "root_gid")
        size = _identity_int(item["size"], "root_size")
        mtime_ns = _identity_int(item["mtime_ns"], "root_mtime")
        ctime_ns = _identity_int(item["ctime_ns"], "root_ctime")
        xattrs = _xattr_inventory(item["xattr_names_b64"], item["xattr_inventory_state"], "root")
        acl_state = item["acl_state"]
        flags_state = item["flags_state"]
        if not isinstance(acl_state, str) or acl_state not in {
            "absent",
            "value_required",
            "unsupported",
            "unknown",
        }:
            raise TreeCustodyV4Error("root_acl_state_invalid")
        if not isinstance(flags_state, str) or flags_state not in {
            "absent",
            "value_required",
            "unsupported",
            "unknown",
        }:
            raise TreeCustodyV4Error("root_flags_state_invalid")
        seen.add(key)
        devices.add(device)
        inodes.add((device, inode))
        normalized.append(
            {
                "root_key": key,
                "source_locator": locator,
                "source_locator_sha256": locator_hash,
                "device": device,
                "inode": inode,
                "nlink": nlink,
                "mode": mode,
                "uid": uid,
                "gid": gid,
                "size": size,
                "mtime_ns": mtime_ns,
                "ctime_ns": ctime_ns,
                "xattr_names_b64": list(xattrs),
                "xattr_inventory_state": item["xattr_inventory_state"],
                "acl_state": acl_state,
                "flags_state": flags_state,
                "disposition": "included_opaque",
                "metadata_provenance": "caller_metadata_unqualified",
            }
        )
    if len(devices) != 1:
        raise TreeCustodyV4Error("root_cross_device")
    if len(inodes) != ROOT_COUNT:
        raise TreeCustodyV4Error("root_alias_overlap")
    locators = [PurePosixPath(str(item["source_locator"])).parts for item in normalized]
    for index, left in enumerate(locators):
        for right in locators[index + 1 :]:
            if left[: len(right)] == right or right[: len(left)] == left:
                raise TreeCustodyV4Error("root_locator_overlap")
    return sorted(normalized, key=lambda item: item["root_key"])


def _normalize_entry(item: dict[str, Any], root_keys: set[str], device: int) -> dict[str, Any]:
    expected = {
        "root_key",
        "path",
        "kind",
        "device",
        "inode",
        "nlink",
        "mode",
        "uid",
        "gid",
        "size",
        "mtime_ns",
        "ctime_ns",
        "xattr_names_b64",
        "xattr_inventory_state",
        "acl_state",
        "flags_state",
        "link_text_b64",
    }
    if not isinstance(item, dict) or set(item) != expected:
        raise TreeCustodyV4Error("entry_record_invalid")
    root_key = item["root_key"]
    kind = item["kind"]
    if not isinstance(root_key, str) or root_key not in root_keys:
        raise TreeCustodyV4Error("entry_root_unknown")
    path = _relative_path(item["path"])
    if not isinstance(kind, str) or kind not in {"file", "directory", "symlink"}:
        raise TreeCustodyV4Error("special_file_rejected")
    entry_device = _identity_int(item["device"], "entry_device")
    if entry_device != device:
        raise TreeCustodyV4Error("entry_cross_device")
    inode = _identity_int(item["inode"], "entry_inode", minimum=1)
    nlink = _identity_int(item["nlink"], "entry_nlink", minimum=1)
    mode = _identity_int(item["mode"], "entry_mode")
    uid = _identity_int(item["uid"], "entry_uid")
    gid = _identity_int(item["gid"], "entry_gid")
    size = _identity_int(item["size"], "entry_size")
    mtime_ns = _identity_int(item["mtime_ns"], "entry_mtime")
    ctime_ns = _identity_int(item["ctime_ns"], "entry_ctime")
    xattrs = _xattr_inventory(item["xattr_names_b64"], item["xattr_inventory_state"], "entry")
    acl_state = item["acl_state"]
    flags_state = item["flags_state"]
    if not isinstance(acl_state, str) or acl_state not in {
        "absent",
        "value_required",
        "unsupported",
        "unknown",
    }:
        raise TreeCustodyV4Error("acl_state_invalid")
    if not isinstance(flags_state, str) or flags_state not in {
        "absent",
        "value_required",
        "unsupported",
        "unknown",
    }:
        raise TreeCustodyV4Error("flags_state_invalid")
    link_text_b64 = item["link_text_b64"]
    env_local_path = PurePosixPath(path).name == ".env.local"
    if kind == "symlink":
        if env_local_path:
            if link_text_b64 is not None:
                raise TreeCustodyV4Error("env_local_symlink_text_forbidden")
            link_text = None
        else:
            if not isinstance(link_text_b64, str):
                raise TreeCustodyV4Error("symlink_text_missing")
            try:
                link_text = base64.b64decode(link_text_b64, validate=True)
            except (ValueError, TypeError) as exc:
                raise TreeCustodyV4Error("symlink_text_invalid") from exc
            if not link_text or b"\x00" in link_text:
                raise TreeCustodyV4Error("symlink_text_invalid")
    elif link_text_b64 is not None:
        raise TreeCustodyV4Error("unexpected_symlink_text")
    return {
        "root_key": root_key,
        "path": path,
        "kind": kind,
        "device": entry_device,
        "inode": inode,
        "nlink": nlink,
        "mode": mode,
        "uid": uid,
        "gid": gid,
        "size": size,
        "mtime_ns": mtime_ns,
        "ctime_ns": ctime_ns,
        "xattr_names_b64": list(xattrs),
        "xattr_inventory_state": item["xattr_inventory_state"],
        "acl_state": acl_state,
        "flags_state": flags_state,
        "link_text_b64": link_text_b64,
    }


def _symlink_lexical_hint(
    entry: dict[str, Any],
    entry_kinds: dict[tuple[str, str], str],
    excluded_paths: set[tuple[str, str]],
) -> str:
    target = base64.b64decode(entry["link_text_b64"])
    if target.startswith(b"/") or re.match(rb"^[A-Za-z]:[/\\]", target):
        return "absolute_target_unresolved"
    if b"\\" in target:
        return "non_posix_target_unresolved"
    try:
        target_text = target.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "non_utf8_target_unresolved"
    parent = posixpath.dirname(entry["path"])
    components = list(PurePosixPath(parent).parts) if parent else []
    target_components = [
        component for component in target_text.split("/") if component not in {"", "."}
    ]
    for index, component in enumerate(target_components):
        if component == "..":
            if not components:
                return "outside_root_target_unresolved"
            components.pop()
            continue
        candidate = "/".join((*components, component))
        candidate_key = (entry["root_key"], candidate)
        if entry_kinds.get(candidate_key) == "symlink":
            if index == len(target_components) - 1:
                return "in_root_symlink_chain_unresolved"
            return "in_root_symlink_component_unresolved"
        if index < len(target_components) - 1:
            candidate_kind = entry_kinds.get(candidate_key)
            if candidate_kind is None:
                return "in_root_missing_intermediate_unresolved"
            if candidate_kind != "directory":
                return "in_root_non_directory_intermediate_unresolved"
        components.append(component)
    normalized = "/".join(components)
    target_key = (entry["root_key"], normalized)
    if target_key in excluded_paths:
        return "in_root_target_excluded_unresolved"
    if target_key in entry_kinds:
        if entry_kinds[(entry["root_key"], normalized)] == "symlink":
            return "in_root_symlink_chain_unresolved"
        return "in_root_target_present"
    if normalized in {"", "."}:
        return "in_root_root_target"
    return "in_root_target_unresolved"


def plan_group_inventory(
    *,
    source_view_ref: str,
    volume_uuid: str,
    roots: Sequence[dict[str, Any]],
    entries: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Return a metadata-only plan; this function does not qualify T or publish artifacts."""
    if not isinstance(source_view_ref, str) or not source_view_ref or len(source_view_ref) > 128:
        raise TreeCustodyV4Error("source_view_ref_invalid")
    if not isinstance(volume_uuid, str) or not volume_uuid or len(volume_uuid) > 64:
        raise TreeCustodyV4Error("volume_uuid_invalid")
    root_rows = _normalize_roots(roots)
    root_keys = {item["root_key"] for item in root_rows}
    root_device = root_rows[0]["device"]
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise TreeCustodyV4Error("entry_roster_invalid")
    rows = [_normalize_entry(item, root_keys, root_device) for item in entries]
    root_identities = {(item["device"], item["inode"]) for item in root_rows}
    if any((row["device"], row["inode"]) in root_identities for row in rows):
        raise TreeCustodyV4Error("entry_aliases_root")
    paths = [(item["root_key"], item["path"]) for item in rows]
    if len(paths) != len(set(paths)):
        raise TreeCustodyV4Error("entry_path_duplicate")
    inode_kinds: dict[tuple[int, int], str] = {}
    inode_paths: dict[tuple[int, int], set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        identity = (row["device"], row["inode"])
        prior_kind = inode_kinds.setdefault(identity, row["kind"])
        if prior_kind != row["kind"]:
            raise TreeCustodyV4Error("inode_kind_conflict")
        inode_paths[identity].add((row["root_key"], row["path"]))
        if row["kind"] != "file" and len(inode_paths[identity]) > 1:
            raise TreeCustodyV4Error("nonregular_inode_alias_unsupported")
    rows.sort(key=lambda item: (item["root_key"], item["path"].encode("utf-8")))
    entry_kinds = {(item["root_key"], item["path"]): item["kind"] for item in rows}
    for row in rows:
        parent = posixpath.dirname(row["path"])
        while parent:
            if entry_kinds.get((row["root_key"], parent)) != "directory":
                raise TreeCustodyV4Error("entry_parent_directory_unproven")
            parent = posixpath.dirname(parent)

    groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["kind"] == "file":
            groups[(row["device"], row["inode"])].append(row)
    group_ids: dict[tuple[int, int], str] = {}
    excluded_groups: set[tuple[int, int]] = set()
    for identity, aliases in groups.items():
        source_nlinks = {row["nlink"] for row in aliases}
        signatures = {
            (
                row["mode"],
                row["uid"],
                row["gid"],
                row["size"],
                row["mtime_ns"],
                row["ctime_ns"],
                tuple(row["xattr_names_b64"]),
                row["xattr_inventory_state"],
                row["acl_state"],
                row["flags_state"],
            )
            for row in aliases
        }
        if len(source_nlinks) != 1 or len(signatures) != 1:
            raise TreeCustodyV4Error("hardlink_metadata_conflict")
        nlink = next(iter(source_nlinks))
        if nlink < len(aliases):
            raise TreeCustodyV4Error("hardlink_alias_count_exceeds_source")
        group_id = hashlib.sha256(f"{identity[0]}:{identity[1]}".encode()).hexdigest()
        group_ids[identity] = group_id
        if any(PurePosixPath(row["path"]).name == ".env.local" for row in aliases):
            excluded_groups.add(identity)

    for row in rows:
        if row["kind"] in {"file", "symlink"} and PurePosixPath(row["path"]).name == ".env.local":
            row["disposition"] = "reissue_excluded"
            row["exclusion_reason"] = "exact_env_local_credential_policy"
        elif row["kind"] == "directory" and PurePosixPath(row["path"]).name == ".env.local":
            raise TreeCustodyV4Error("env_local_directory_unclassified")
        elif row["kind"] == "file" and (row["device"], row["inode"]) in excluded_groups:
            row["disposition"] = "reissue_excluded"
            row["exclusion_reason"] = "hardlink_alias_to_exact_env_local"
        else:
            row["disposition"] = "included_opaque"

    excluded_paths = {
        (row["root_key"], row["path"]) for row in rows if row["disposition"] == "reissue_excluded"
    }
    for row in rows:
        if row["kind"] == "file":
            identity = (row["device"], row["inode"])
            row["hardlink_group_id"] = group_ids[identity]
            row["in_group_alias_count"] = len(groups[identity])
            row["source_nlink"] = row["nlink"]
            row["unaccounted_alias_count"] = row["nlink"] - len(groups[identity])
            row["unaccounted_alias_location"] = (
                "unresolved" if row["unaccounted_alias_count"] > 0 else "none_reported"
            )
        elif row["kind"] == "symlink":
            row["symlink_lexical_target_hint"] = (
                "excluded_target_not_supplied"
                if row["disposition"] == "reissue_excluded"
                else _symlink_lexical_hint(row, entry_kinds, excluded_paths)
            )
            row["symlink_materialize"] = False

    alias_refs: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for identity, aliases in groups.items():
        if identity in excluded_groups:
            continue
        group_id = group_ids[identity]
        alias_refs[group_id] = sorted((row["root_key"], row["path"]) for row in aliases)
    for row in rows:
        if row["kind"] == "file" and row["disposition"] == "included_opaque":
            hardlink_aliases = alias_refs[row["hardlink_group_id"]]
            row["hardlink_primary"] = hardlink_aliases[0]

    included_metadata_members = root_rows + [
        row for row in rows if row["disposition"] == "included_opaque"
    ]
    unsupported_metadata = sum(
        row["xattr_inventory_state"] in {"unsupported", "unknown"}
        or row["acl_state"] in {"unsupported", "unknown"}
        or row["flags_state"] in {"unsupported", "unknown"}
        for row in included_metadata_members
    )
    metadata_value_obligations = sum(
        bool(row["xattr_names_b64"])
        or row["acl_state"] == "value_required"
        or row["flags_state"] == "value_required"
        for row in included_metadata_members
    )
    unaccounted_hardlink_groups = sum(
        max(aliases[0]["nlink"] - len(aliases), 0) > 0 for aliases in groups.values()
    )
    symlink_counts = Counter(
        row["symlink_lexical_target_hint"] for row in rows if row["kind"] == "symlink"
    )
    excluded_alias_count = sum(row["disposition"] == "reissue_excluded" for row in rows)
    plan = {
        "schema": PLAN_SCHEMA,
        "format_schema": SCHEMA,
        "classification": CLASSIFICATION,
        "output_sensitivity": "private_raw_paths_and_symlink_text",
        "semantic_lineage": SEMANTIC_LINEAGE,
        "activation_authorized": False,
        "complete_marker_publishable": False,
        "complete_semantics": COMPLETE_SEMANTICS,
        "source_view": {
            "reference": source_view_ref,
            "authority": "unqualified_caller_metadata",
            "volume_uuid": volume_uuid,
            "device": root_device,
        },
        "root_roster": root_rows,
        "entry_count": len(rows),
        "root_count": len(root_rows),
        "included_entry_count": sum(row["disposition"] == "included_opaque" for row in rows),
        "reissue_excluded_entry_count": excluded_alias_count,
        "unsupported_metadata_member_count": unsupported_metadata,
        "metadata_value_obligation_member_count": metadata_value_obligations,
        "unaccounted_hardlink_group_count": unaccounted_hardlink_groups,
        "symlink_lexical_hint_counts": dict(sorted(symlink_counts.items())),
        "entries": rows,
        "status": "metadata_plan_only_not_captured",
    }
    return plan


def verify_plan(plan: object) -> dict[str, Any]:
    """Re-derive every disposition and group field from the plan's supplied roster.

    This proves internal consistency only. It cannot prove that the supplied
    roster is complete or that its metadata came from a qualified source view.
    """
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise TreeCustodyV4Error("plan_schema_invalid")
    try:
        frozen_bytes = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        frozen = json.loads(frozen_bytes)
    except (TypeError, ValueError) as exc:
        raise TreeCustodyV4Error("plan_not_json_serializable") from exc
    source_view = frozen.get("source_view")
    roots = frozen.get("root_roster")
    entries = frozen.get("entries")
    if (
        not isinstance(source_view, dict)
        or set(source_view) != {"reference", "authority", "volume_uuid", "device"}
        or source_view.get("authority") != "unqualified_caller_metadata"
        or not isinstance(roots, list)
        or not isinstance(entries, list)
    ):
        raise TreeCustodyV4Error("plan_source_roster_invalid")
    source_view_ref = source_view.get("reference")
    volume_uuid = source_view.get("volume_uuid")
    if not isinstance(source_view_ref, str) or not isinstance(volume_uuid, str):
        raise TreeCustodyV4Error("plan_source_identity_invalid")
    root_fields = {
        "root_key",
        "source_locator",
        "device",
        "inode",
        "nlink",
        "mode",
        "uid",
        "gid",
        "size",
        "mtime_ns",
        "ctime_ns",
        "xattr_names_b64",
        "xattr_inventory_state",
        "acl_state",
        "flags_state",
    }
    entry_fields = {
        "root_key",
        "path",
        "kind",
        "device",
        "inode",
        "nlink",
        "mode",
        "uid",
        "gid",
        "size",
        "mtime_ns",
        "ctime_ns",
        "xattr_names_b64",
        "xattr_inventory_state",
        "acl_state",
        "flags_state",
        "link_text_b64",
    }
    if any(not isinstance(row, dict) or not root_fields <= set(row) for row in roots):
        raise TreeCustodyV4Error("plan_root_record_invalid")
    if any(not isinstance(row, dict) or not entry_fields <= set(row) for row in entries):
        raise TreeCustodyV4Error("plan_entry_record_invalid")
    rebuilt = plan_group_inventory(
        source_view_ref=source_view_ref,
        volume_uuid=volume_uuid,
        roots=[{key: row[key] for key in root_fields} for row in roots],
        entries=[{key: row[key] for key in entry_fields} for row in entries],
    )
    rebuilt_bytes = json.dumps(rebuilt, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if frozen_bytes != rebuilt_bytes:
        raise TreeCustodyV4Error("plan_derived_fields_mismatch")
    return rebuilt
