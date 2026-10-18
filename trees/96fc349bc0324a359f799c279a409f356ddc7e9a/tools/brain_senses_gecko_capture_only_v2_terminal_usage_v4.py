"""Bridge the immutable a078 terminal receipt across recognized sibling roots.

This adapter preserves the historical 19-root census inside the frozen receipt while the
current usage inventory continues to include every recognized root for live admission.
It performs no writes and grants no dispatch or cursor authority.
"""

from __future__ import annotations

import copy
import hashlib
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any, cast

from tools import brain_senses_gecko_capture_only_v2_terminal_usage_v1 as terminal_v1
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json

SCHEMA = "brain-senses-gecko-capture-only-v2-terminal-usage-census-bridge-v4"
HISTORICAL_CENSUS_SHA256 = "3dd9e18d8f700c2ba7ba9d003b9c5372aa5de02623a5c5cc1d40d2fb0afaf417"
ADDED_ROOT_RE = re.compile(r"services/gecko-market-v1/capture-only-v2/([0-9a-f]{32})\Z")
ADDED_HEALTH_RE = re.compile(r"services/gecko-market-v1/host-health-v1/([0-9a-f]{32})\Z")
ADDED_WATCHDOG_RE = re.compile(r"services/gecko-market-v1/canary-watchdog-v1/([0-9a-f]{32})\Z")
HASH_RE = re.compile(r"[0-9a-f]{64}\Z")


class CensusBridgeError(ValueError):
    """The live inventory cannot be safely projected to the frozen a078 census."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise CensusBridgeError(reason)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _build_a078_receipt_with_v2_census(
    writer: DescriptorWriter,
    *,
    storage: Mapping[str, Any],
    root_inventory_sha256: str,
) -> dict[str, Any]:
    """Run the frozen receipt verifier against v2 discovery for this call only."""
    from tools import brain_senses_gecko_usage_inventory_v2 as inventory_v2

    tools_package = sys.modules["tools"]
    had_attribute = hasattr(tools_package, "brain_senses_gecko_usage_inventory_v1")
    previous = getattr(tools_package, "brain_senses_gecko_usage_inventory_v1", None)
    vars(tools_package)["brain_senses_gecko_usage_inventory_v1"] = inventory_v2
    try:
        return terminal_v1.build_receipt(
            writer,
            storage=storage,
            root_inventory_sha256=root_inventory_sha256,
        )
    finally:
        if had_attribute:
            vars(tools_package)["brain_senses_gecko_usage_inventory_v1"] = previous
        else:
            vars(tools_package).pop("brain_senses_gecko_usage_inventory_v1", None)


def _project_historical_manifest(
    current_manifest: Mapping[str, Any], pinned_paths: Sequence[str]
) -> dict[str, Any]:
    """Remove only recognized post-receipt v2 sibling IDs from the collection projection."""
    _require(type(current_manifest) is dict, "current_manifest_shape")
    projected = copy.deepcopy(dict(current_manifest))
    service_entries = projected.get("service_entries")
    _require(type(service_entries) is list, "current_manifest_service_entries")
    projected["service_entries"] = [
        entry for entry in cast(list[Any], service_entries) if entry != "canary-watchdog-v1"
    ]
    collections = projected.get("collections")
    _require(type(collections) is dict, "current_manifest_collections")
    collection_map = cast(dict[str, Any], collections)
    pinned = set(pinned_paths)
    _require(len(pinned) == len(pinned_paths), "historical_paths_duplicate")
    for name, entry in collection_map.items():
        _require(type(name) is str and type(entry) is dict, "current_collection_shape")
        entry_map = cast(dict[str, Any], entry)
        children = entry_map.get("children")
        _require(type(children) is list, "current_collection_children")
        prefix = f"{projected.get('service_root')}/{name}/"
        entry_map["children"] = [
            child for child in cast(list[Any], children) if f"{prefix}{child}" in pinned
        ]
        if name == "canary-watchdog-v1":
            # This collection key did not exist in the frozen a078 manifest.
            collection_map.pop(name)
            break
    projected["root_paths"] = list(pinned_paths)
    return projected


def _validate_live_census(
    *,
    receipt: Mapping[str, Any],
    current_roots: Sequence[str],
    current_manifest: Mapping[str, Any],
    current_inventory_sha256: str,
    after_roots: Sequence[str],
    after_manifest: Mapping[str, Any],
    after_inventory_sha256: str,
    snapshots: Sequence[Mapping[str, Any]],
    root_metadata: Mapping[str, Mapping[str, Any]],
    storage: Mapping[str, Any],
) -> tuple[list[str], str]:
    historical = receipt.get("global_root_census")
    _require(type(historical) is dict, "receipt_historical_census_missing")
    historical_map = cast(dict[str, Any], historical)
    pinned_paths = historical_map.get("paths")
    _require(
        type(pinned_paths) is list
        and len(pinned_paths) == 19
        and pinned_paths == sorted(set(pinned_paths))
        and terminal_v1.ROOT in pinned_paths
        and terminal_v1.HEALTH_ROOT in pinned_paths,
        "receipt_historical_paths_invalid",
    )
    pinned_path_list = cast(list[str], pinned_paths)
    _require(
        historical_map.get("sha256") == HISTORICAL_CENSUS_SHA256,
        "receipt_historical_census_hash_invalid",
    )
    _require(
        list(current_roots) == sorted(set(current_roots))
        and list(current_roots) == current_manifest.get("root_paths"),
        "current_roots_manifest_mismatch",
    )
    _require(
        list(after_roots) == list(current_roots)
        and after_inventory_sha256 == current_inventory_sha256
        and dict(after_manifest) == dict(current_manifest),
        "current_census_changed_during_replay",
    )
    current_hash = _sha256(canonical_json(current_manifest))
    _require(current_hash == current_inventory_sha256, "current_census_hash_invalid")
    added = set(current_roots) - set(pinned_path_list)
    _require(set(pinned_path_list) <= set(current_roots), "historical_root_missing")
    added_capture_ids: dict[str, str] = {}
    added_health_ids: dict[str, str] = {}
    added_watchdog_ids: dict[str, str] = {}
    for root in added:
        capture_match = ADDED_ROOT_RE.fullmatch(root)
        health_match = ADDED_HEALTH_RE.fullmatch(root)
        watchdog_match = ADDED_WATCHDOG_RE.fullmatch(root)
        if capture_match is not None:
            release_id = capture_match.group(1)
            _require(release_id != terminal_v1.RELEASE_ID, "a078_descendant_or_duplicate_root")
            added_capture_ids[release_id] = root
            continue
        if watchdog_match is not None:
            release_id = watchdog_match.group(1)
            _require(release_id != terminal_v1.RELEASE_ID, "a078_descendant_or_duplicate_root")
            added_watchdog_ids[release_id] = root
            continue
        _require(health_match is not None, "added_root_not_allowlisted_sibling")
        release_id = cast(re.Match[str], health_match).group(1)
        _require(release_id != terminal_v1.RELEASE_ID, "a078_descendant_or_duplicate_root")
        added_health_ids[release_id] = root
    _require(
        set(added_health_ids) <= set(added_capture_ids),
        "added_health_root_missing_capture_sibling",
    )
    _require(
        set(added_watchdog_ids) <= set(added_capture_ids),
        "added_watchdog_root_missing_capture_sibling",
    )

    projected = _project_historical_manifest(current_manifest, pinned_path_list)
    projected_hash = _sha256(canonical_json(projected))
    _require(projected_hash == HISTORICAL_CENSUS_SHA256, "historical_projection_mismatch")

    snapshot_roots = [snapshot.get("root") for snapshot in snapshots]
    _require(snapshot_roots == list(current_roots), "snapshot_root_coverage_mismatch")
    snapshots_by_root = {str(snapshot.get("root")): snapshot for snapshot in snapshots}
    for snapshot in snapshots:
        _require(
            snapshot.get("root_inventory_sha256") == current_inventory_sha256,
            "snapshot_current_census_mismatch",
        )
        if snapshot.get("root") in added:
            _require(
                snapshot.get("status") in {"verified_prefix", "verified_none"},
                "added_root_not_independently_replayed",
            )
        if snapshot.get("root") in added_watchdog_ids.values():
            _require(
                snapshot.get("status") == "verified_none" and snapshot.get("attempts") == [],
                "added_watchdog_must_be_zero_attempt_diagnostic",
            )
            metadata = root_metadata.get(str(snapshot.get("root")))
            _require(
                type(metadata) is dict
                and type(metadata.get("receipt")) is dict
                and metadata.get("receipt_sha256") == _sha256(canonical_json(metadata["receipt"])),
                "added_watchdog_receipt_metadata_invalid",
            )
        try:
            shared_usage._validate_snapshot(snapshot)
        except shared_usage.UsageError as exc:
            raise CensusBridgeError("snapshot_independent_validation_failed") from exc
    for release_id, capture_root in added_capture_ids.items():
        capture_snapshot = snapshots_by_root[capture_root]
        _require(
            capture_snapshot.get("status") in {"verified_prefix", "verified_none"},
            "added_capture_not_independently_replayed",
        )
        capture_record = root_metadata.get(capture_root)
        _require(type(capture_record) is dict, "added_capture_metadata_missing")
        capture_record_map = cast(dict[str, Any], capture_record)
        binding = capture_record_map.get("binding")
        _require(type(binding) is dict, "added_capture_binding_missing")
        capture_binding = cast(dict[str, Any], binding)
        capture_binding_core = dict(capture_binding)
        capture_binding_sha256 = capture_binding_core.pop("binding_sha256", None)
        capture_storage = capture_binding.get("storage")
        _require(
            capture_binding.get("root") == capture_root
            and type(capture_binding.get("release_sha256")) is str
            and HASH_RE.fullmatch(capture_binding["release_sha256"]) is not None
            and type(capture_binding_sha256) is str
            and capture_binding_sha256 == _sha256(canonical_json(capture_binding_core))
            and type(capture_storage) is dict
            and set(capture_storage) == {"root_id", "volume_uuid", "device", "inode"}
            and dict(capture_storage) == dict(storage),
            "added_capture_binding_invalid",
        )
        capture_storage_map = cast(dict[str, Any], capture_storage)
        health_root = f"services/gecko-market-v1/host-health-v1/{release_id}"
        if release_id not in added_health_ids:
            continue
        health_record = root_metadata.get(health_root)
        _require(type(health_record) is dict, "added_health_metadata_missing")
        health_record_map = cast(dict[str, Any], health_record)
        health_value = health_record_map.get("health")
        _require(type(health_value) is dict, "added_health_snapshot_missing")
        health_snapshot = cast(dict[str, Any], health_value)
        health_storage = health_record_map.get("storage")
        health_usage_snapshot = snapshots_by_root[health_root]
        _require(
            health_usage_snapshot.get("status") == "verified_none"
            and health_usage_snapshot.get("attempts") == [],
            "added_health_usage_must_be_empty",
        )
        _require(
            health_snapshot.get("release_id") == release_id
            and health_snapshot.get("release_sha256") == capture_binding.get("release_sha256")
            and health_snapshot.get("binding_sha256") == capture_binding.get("binding_sha256")
            and health_snapshot.get("root_id") == capture_storage_map.get("root_id")
            and health_snapshot.get("volume_uuid") == capture_storage_map.get("volume_uuid")
            and health_snapshot.get("device") == capture_storage_map.get("device")
            and health_snapshot.get("inode") == capture_storage_map.get("inode")
            and type(health_storage) is dict
            and dict(cast(dict[str, Any], health_storage)) == dict(capture_storage_map)
            and capture_storage_map.get("root_id") == storage.get("root_id")
            and capture_storage_map.get("volume_uuid") == storage.get("volume_uuid")
            and capture_storage_map.get("device") == storage.get("device")
            and capture_storage_map.get("inode") == storage.get("inode"),
            "added_health_capture_binding_mismatch",
        )
        _require(
            type(health_snapshot.get("intent_count")) is int
            and health_snapshot["intent_count"] <= len(capture_snapshot.get("attempts", [])),
            "added_health_intent_count_exceeds_capture_attempts",
        )
    return list(pinned_path_list), projected_hash


def _make_terminal_sidecar(
    receipt: Mapping[str, Any], current_inventory_sha256: str
) -> dict[str, Any]:
    health_stop = receipt.get("health_stop")
    _require(type(health_stop) is dict, "receipt_health_stop_missing")
    health_stop_map = cast(dict[str, Any], health_stop)
    return cast(
        dict[str, Any],
        shared_usage.make_verified_root_snapshot(
            root=terminal_v1.HEALTH_ROOT,
            status="verified_none",
            root_inventory_sha256=current_inventory_sha256,
            source_prefix_sha256=str(health_stop_map["health_prefix_sha256"]),
            attempts=[],
        ),
    )


def reconcile_a078_health_snapshot(
    writer: DescriptorWriter,
    *,
    receipt: Mapping[str, Any],
    receipt_sha256: str,
    storage: Mapping[str, Any],
    current_roots: Sequence[str],
    current_manifest: Mapping[str, Any],
    current_inventory_sha256: str,
    after_roots: Sequence[str],
    after_manifest: Mapping[str, Any],
    after_inventory_sha256: str,
    snapshots: Sequence[Mapping[str, Any]],
    root_metadata: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Revalidate the frozen 19-root receipt while returning a live-census sidecar."""
    _require(receipt_sha256 == terminal_v1.RECEIPT_SHA256, "receipt_hash_not_pinned")
    _require(
        _sha256(canonical_json(receipt)) == receipt_sha256,
        "receipt_bytes_hash_mismatch",
    )
    _require(receipt.get("schema") == terminal_v1.SCHEMA, "receipt_schema_mismatch")
    pinned_paths, projected_hash = _validate_live_census(
        receipt=receipt,
        current_roots=current_roots,
        current_manifest=current_manifest,
        current_inventory_sha256=current_inventory_sha256,
        after_roots=after_roots,
        after_manifest=after_manifest,
        after_inventory_sha256=after_inventory_sha256,
        snapshots=snapshots,
        root_metadata=root_metadata,
        storage=storage,
    )
    _require(projected_hash == HISTORICAL_CENSUS_SHA256, "historical_projection_hash_mismatch")

    try:
        expected = _build_a078_receipt_with_v2_census(
            writer,
            storage=storage,
            root_inventory_sha256=current_inventory_sha256,
        )
        # This is the only adjustment: compare the v1 receipt against its reconstructed
        # historical census, not the live census that admission must continue to use.
        expected["global_root_census"] = {
            "paths": pinned_paths,
            "sha256": projected_hash,
        }
        terminal_v1.validate_receipt(receipt, expected=expected)
    except terminal_v1.TerminalUsageError as exc:
        raise CensusBridgeError(str(exc)) from exc

    return _make_terminal_sidecar(receipt, current_inventory_sha256)


__all__ = [
    "CensusBridgeError",
    "HISTORICAL_CENSUS_SHA256",
    "SCHEMA",
    "reconcile_a078_health_snapshot",
]
