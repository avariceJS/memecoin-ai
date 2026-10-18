"""Pure cutover and accounting kernel for capacity-coordinator v3.

This module models the immutable v2 bridge and v3 ledger. It performs no I/O,
locking, provider work, or storage mutation; a host adapter must hold the v2
global lock and use descriptor-bound create-only writes for the returned bytes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from tools import brain_senses_capacity_coordinator_v2 as v2

VERSION = 3
POLICY_SCHEMA = "brain-senses-capacity-coordinator-policy-v3"
EVENT_SCHEMA = "brain-senses-capacity-coordinator-ledger-event-v3"
BRIDGE_SCHEMA = "brain-senses-capacity-coordinator-v2-bridge-v1"
POINTER_SCHEMA = "brain-senses-capacity-coordinator-v3-cutover-pointer-v1"
COORDINATOR_ROOT = "services/senses-capacity-v3"
POLICY_PATH = f"{COORDINATOR_ROOT}/policy.json"
LEDGER_PATH = f"{COORDINATOR_ROOT}/ledger.jsonl"
POINTER_PATH = f"{COORDINATOR_ROOT}/v2-cutover-pointer.json"
SHARED_LOCK_PATH = v2.LOCK_PATH
MAX_LEDGER_BYTES = 64_000_000
COORDINATOR_APPEND_RESERVE_BYTES = 2 * v2.COORDINATOR_APPEND_BLOCK_BYTES
DISCOVERY_EPOCH_HOLD_BYTES = 6_794_772_480
DISCOVERY_PAGE_RESERVE_BYTES = 2_621_440
DISCOVERY_EPOCH_PAGE_COUNT = 2_592
DISCOVERY_ROOT = "services/gecko-new-pool-discovery-v1"
SERVICE_CAPS = {
    "helius": 150_000_000_000,
    "alchemy": 256_000_000,
    "gecko_legacy": 16_000_000_000,
    "gecko_discovery": 101_921_587_200,
    "coordinator": 64_000_000,
}
FIXED_RESERVES = dict(v2._FIXED_RESERVES)
FIXED_FREE_FLOOR_BYTES = sum(FIXED_RESERVES.values())
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
ID_RE = re.compile(r"[0-9a-f]{32}\Z")
VOLUME_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")


class CoordinatorV3Error(ValueError):
    """Malformed migration, ledger, or capacity state."""


def canonical(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CoordinatorV3Error("canonical_json_invalid") from exc


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise CoordinatorV3Error(reason)


def _hash(value: Any) -> bool:
    return type(value) is str and SHA_RE.fullmatch(value) is not None


def _id(value: Any) -> bool:
    return type(value) is str and ID_RE.fullmatch(value) is not None


def _uint(value: Any) -> bool:
    return type(value) is int and value >= 0


def _decode(raw: bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CoordinatorV3Error("duplicate_json_key")
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=_bad_constant
        )
    except CoordinatorV3Error:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise CoordinatorV3Error("json_invalid") from exc


def _bad_constant(_: str) -> None:
    raise CoordinatorV3Error("nonfinite_json")


def _relative(value: Any) -> bool:
    if type(value) is not str or not value or "\\" in value or "\x00" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and str(path) == value


def _v2_root_paths(policy: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    allocation = policy.get("allocation_roots")
    _need(
        type(allocation) is dict
        and set(allocation) == {"helius", "alchemy", "gecko", "coordinator"},
        "v2_allocation_shape",
    )
    result: dict[str, tuple[str, ...]] = {}
    for name, values in allocation.items():
        _need(
            type(values) is list and values and all(_relative(path) for path in values),
            "v2_allocation_path",
        )
        _need(len(set(values)) == len(values), "v2_allocation_duplicate")
        result[name] = tuple(values)
    roots = [path for values in result.values() for path in values]
    _need(len(set(roots)) == len(roots), "v2_allocation_overlap")
    return result


def _v2_policy(policy_bytes: bytes) -> dict[str, Any]:
    policy = _decode(policy_bytes)
    _need(type(policy) is dict and canonical(policy) == policy_bytes, "v2_policy_not_canonical")
    _need(
        policy.get("schema") == v2.POLICY_SCHEMA
        and policy.get("schema_version") == v2.POLICY_VERSION
        and set(policy)
        == {
            "schema",
            "schema_version",
            "root_binding",
            "service_lock_paths",
            "allocation_roots",
            "service_caps_bytes",
            "fixed_reserves_bytes",
            "coordinator_append_reservation_bytes",
            "ledger",
            "storage",
        },
        "v2_policy_schema",
    )
    _need(policy.get("service_caps_bytes") == v2._SERVICE_CAPS, "v2_policy_caps")
    _need(policy.get("fixed_reserves_bytes") == v2._FIXED_RESERVES, "v2_policy_reserves")
    _need(
        policy.get("coordinator_append_reservation_bytes") == v2.COORDINATOR_APPEND_BLOCK_BYTES,
        "v2_policy_append_reserve",
    )
    _need(policy.get("ledger") == "append_only_canonical_sha256_chain", "v2_policy_ledger")
    _need(
        policy.get("storage") == "descriptor_bound_root_relative_flock_create_only_fsync",
        "v2_policy_storage",
    )
    root_binding = policy.get("root_binding")
    _need(
        type(root_binding) is dict
        and set(root_binding) == {"root_id", "volume_uuid", "device", "inode"}
        and _id(root_binding.get("root_id"))
        and type(root_binding.get("volume_uuid")) is str
        and VOLUME_UUID_RE.fullmatch(root_binding["volume_uuid"].lower())
        and _uint(root_binding.get("device"))
        and type(root_binding.get("inode")) is int
        and root_binding["inode"] > 0,
        "v2_policy_root_binding",
    )
    locks = policy.get("service_lock_paths")
    _need(type(locks) is dict and set(locks) == {"helius", "alchemy", "gecko"}, "v2_policy_locks")
    _need(
        all(_relative(path) for path in locks.values()) and len(set(locks.values())) == 3,
        "v2_policy_lock_paths",
    )
    _v2_root_paths(policy)
    return policy


def _v2_ledger(raw: bytes, policy_bytes: bytes) -> list[dict[str, Any]]:
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_LEDGER_BYTES, "v2_ledger_bytes")
    try:
        events = v2._verify_ledger(raw)
    except Exception as exc:
        raise CoordinatorV3Error("v2_ledger_replay_failed") from exc
    _need(events[0]["fields"]["policy_sha256"] == sha256(policy_bytes), "v2_ledger_policy_mismatch")
    return events


def _inventory_snapshot(
    root_inventory: Mapping[str, Any], expected_roots: Sequence[str]
) -> dict[str, int]:
    _need(
        type(root_inventory) is dict and set(root_inventory) == set(expected_roots),
        "root_inventory_paths",
    )
    _need(all(_uint(value) for value in root_inventory.values()), "root_inventory_sizes")
    return {path: root_inventory[path] for path in sorted(root_inventory)}


def build_v2_bridge(
    *,
    v2_policy_bytes: bytes,
    v2_ledger_bytes: bytes,
    v2_root_inventory: Mapping[str, int],
    artifact_sha256_by_path: Mapping[str, str],
) -> dict[str, Any]:
    """Replay and freeze the exact v2 prefix, roots, IDs, and unresolved debt."""
    policy = _v2_policy(v2_policy_bytes)
    events = _v2_ledger(v2_ledger_bytes, v2_policy_bytes)
    paths = _v2_root_paths(policy)
    all_roots = sorted(path for group in paths.values() for path in group)
    inventory = _inventory_snapshot(v2_root_inventory, all_roots)
    _need(type(artifact_sha256_by_path) is dict, "v2_artifact_index_shape")
    _need(
        all(_relative(path) and _hash(digest) for path, digest in artifact_sha256_by_path.items()),
        "v2_artifact_index_value",
    )
    reservations: dict[str, dict[str, Any]] = {}
    terminal_events: dict[str, Mapping[str, Any]] = {}
    request_ids: list[dict[str, str]] = []
    for event in events[1:]:
        fields = event["fields"]
        if event["kind"] == "reserved":
            rid = fields["reservation_id"]
            service_roots = paths[fields["service"]]
            _need(
                any(fields["intent_path"].startswith(root + "/") for root in service_roots)
                and any(fields["outcome_path"].startswith(root + "/") for root in service_roots),
                "v2_reservation_path_outside_service_root",
            )
            reservations[rid] = dict(fields)
            request_ids.append({"service": fields["service"], "request_id": fields["request_id"]})
            intent_hash = artifact_sha256_by_path.get(fields["intent_path"])
            proven_preintent_release = any(
                terminal["kind"] == "released"
                and terminal["fields"].get("reservation_id") == rid
                and terminal["fields"].get("outcome") == "pre_intent_absent"
                for terminal in events
            )
            if not proven_preintent_release or intent_hash is not None:
                _need(intent_hash == fields["intent_sha256"], "v2_intent_artifact_mismatch")
        elif event["kind"] in {"settled", "ambiguous_terminal", "released"}:
            rid = fields["reservation_id"]
            terminal_events[rid] = event
            outcome_path = fields.get("outcome_path")
            if outcome_path is not None:
                _need(
                    artifact_sha256_by_path.get(outcome_path) == fields["outcome_sha256"],
                    "v2_outcome_artifact_mismatch",
                )
    _need(
        len({row["request_id"] for row in request_ids}) == len(request_ids),
        "v2_cross_lane_request_id_collision",
    )
    liabilities = []
    for rid, fields in reservations.items():
        terminal = terminal_events.get(rid)
        kind = terminal["kind"] if terminal else "pending"
        if kind in {"pending", "ambiguous_terminal"}:
            liabilities.append(
                {
                    "reservation_id": rid,
                    "service": fields["service"],
                    "request_id": fields["request_id"],
                    "reserved_bytes": fields["reserved_bytes"],
                    "liability_bytes": fields["reserved_bytes"],
                    "state": "pending" if kind == "pending" else "ambiguous_full_reserve",
                }
            )
        elif kind == "released":
            _need(
                terminal["fields"].get("outcome") == "pre_intent_absent", "v2_release_not_preintent"
            )
        elif kind == "settled":
            pass
        else:
            raise CoordinatorV3Error("v2_reservation_terminal_invalid")
    core = {
        "schema": BRIDGE_SCHEMA,
        "schema_version": VERSION,
        "v2_policy_sha256": sha256(v2_policy_bytes),
        "v2_ledger_sha256": sha256(v2_ledger_bytes),
        "v2_event_count": len(events),
        "v2_tail_sequence": events[-1]["seq"],
        "v2_tail_sha256": events[-1]["event_sha256"],
        "v2_root_inventory": inventory,
        "v2_root_inventory_sha256": sha256(canonical(inventory)),
        "v2_allocation_roots": {name: list(paths[name]) for name in sorted(paths)},
        "v2_reservation_ids": sorted(reservations),
        "v2_request_ids": sorted(request_ids, key=lambda row: (row["service"], row["request_id"])),
        "pending_liabilities": sorted(liabilities, key=lambda row: row["reservation_id"]),
        "pending_liability_bytes": sum(row["liability_bytes"] for row in liabilities),
        "shared_lock_path": SHARED_LOCK_PATH,
    }
    return {**core, "bridge_sha256": sha256(canonical(core))}


def _verify_bridge(bridge: Any) -> dict[str, Any]:
    keys = {
        "schema",
        "schema_version",
        "v2_policy_sha256",
        "v2_ledger_sha256",
        "v2_event_count",
        "v2_tail_sequence",
        "v2_tail_sha256",
        "v2_root_inventory",
        "v2_root_inventory_sha256",
        "v2_allocation_roots",
        "v2_reservation_ids",
        "v2_request_ids",
        "pending_liabilities",
        "pending_liability_bytes",
        "shared_lock_path",
        "bridge_sha256",
    }
    _need(type(bridge) is dict and set(bridge) == keys, "bridge_shape")
    core = dict(bridge)
    digest = core.pop("bridge_sha256")
    _need(_hash(digest) and sha256(canonical(core)) == digest, "bridge_hash")
    _need(
        bridge["schema"] == BRIDGE_SCHEMA and bridge["schema_version"] == VERSION, "bridge_version"
    )
    _need(
        all(
            _hash(bridge[name])
            for name in (
                "v2_policy_sha256",
                "v2_ledger_sha256",
                "v2_tail_sha256",
                "v2_root_inventory_sha256",
            )
        ),
        "bridge_hash_fields",
    )
    _need(
        type(bridge["v2_event_count"]) is int and bridge["v2_event_count"] >= 1,
        "bridge_event_count",
    )
    _need(bridge["v2_tail_sequence"] == bridge["v2_event_count"] - 1, "bridge_tail_sequence")
    _need(
        type(bridge["v2_root_inventory"]) is dict
        and all(
            _relative(path) and _uint(size) for path, size in bridge["v2_root_inventory"].items()
        ),
        "bridge_inventory",
    )
    _need(
        sha256(canonical(bridge["v2_root_inventory"])) == bridge["v2_root_inventory_sha256"],
        "bridge_inventory_hash",
    )
    roots = bridge["v2_allocation_roots"]
    _need(
        type(roots) is dict
        and set(roots) == {"helius", "alchemy", "gecko", "coordinator"}
        and all(
            type(items) is list and items and all(_relative(path) for path in items)
            for items in roots.values()
        ),
        "bridge_allocation_roots",
    )
    flattened_roots = [path for items in roots.values() for path in items]
    _need(
        len(flattened_roots) == len(set(flattened_roots))
        and set(flattened_roots) == set(bridge["v2_root_inventory"]),
        "bridge_allocation_inventory_mismatch",
    )
    _need(
        type(bridge["v2_reservation_ids"]) is list
        and all(type(item) is str and item for item in bridge["v2_reservation_ids"]),
        "bridge_reservation_ids",
    )
    _need(
        len(set(bridge["v2_reservation_ids"])) == len(bridge["v2_reservation_ids"]),
        "bridge_reservation_duplicate",
    )
    _need(
        type(bridge["v2_request_ids"]) is list
        and all(
            type(row) is dict
            and set(row) == {"service", "request_id"}
            and row["service"] in {"helius", "alchemy", "gecko"}
            and type(row["request_id"]) is str
            and row["request_id"]
            for row in bridge["v2_request_ids"]
        ),
        "bridge_request_ids",
    )
    _need(
        len({row["request_id"] for row in bridge["v2_request_ids"]})
        == len(bridge["v2_request_ids"]),
        "bridge_request_duplicate",
    )
    _need(
        type(bridge["pending_liabilities"]) is list
        and all(
            type(row) is dict
            and set(row)
            == {
                "reservation_id",
                "service",
                "request_id",
                "reserved_bytes",
                "liability_bytes",
                "state",
            }
            for row in bridge["pending_liabilities"]
        ),
        "bridge_liabilities",
    )
    _need(
        all(
            row["service"] in {"helius", "alchemy", "gecko"}
            and row["state"] in {"pending", "ambiguous_full_reserve"}
            and type(row["request_id"]) is str
            and _uint(row["reserved_bytes"])
            and row["reserved_bytes"] > 0
            and row["liability_bytes"] == row["reserved_bytes"]
            for row in bridge["pending_liabilities"]
        ),
        "bridge_liability_row",
    )
    _need(
        bridge["pending_liability_bytes"]
        == sum(row["liability_bytes"] for row in bridge["pending_liabilities"]),
        "bridge_liability_total",
    )
    _need(bridge["shared_lock_path"] == SHARED_LOCK_PATH, "bridge_lock_path")
    return dict(bridge)


def verify_v2_prefix(
    bridge: Mapping[str, Any],
    *,
    v2_policy_bytes: bytes,
    v2_ledger_bytes: bytes,
    v2_root_inventory: Mapping[str, int],
    artifact_sha256_by_path: Mapping[str, str],
) -> dict[str, Any]:
    """Require the original v2 prefix and root inventory to remain frozen."""
    _verify_bridge(bridge)
    expected = build_v2_bridge(
        v2_policy_bytes=v2_policy_bytes,
        v2_ledger_bytes=v2_ledger_bytes,
        v2_root_inventory=v2_root_inventory,
        artifact_sha256_by_path=artifact_sha256_by_path,
    )
    _need(dict(bridge) == expected, "v2_frozen_prefix_drift")
    return expected


def _v3_root_sets(v2_policy: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    old = _v2_root_paths(v2_policy)
    all_old = [path for group in old.values() for path in group]
    _need(DISCOVERY_ROOT not in all_old and COORDINATOR_ROOT not in all_old, "v3_root_overlap")
    _need(
        not any(
            left.startswith(right + "/") or right.startswith(left + "/")
            for index, left in enumerate(all_old + [DISCOVERY_ROOT, COORDINATOR_ROOT])
            for right in (all_old + [DISCOVERY_ROOT, COORDINATOR_ROOT])[index + 1 :]
        ),
        "v3_root_overlap",
    )
    return {
        "helius": old["helius"],
        "alchemy": old["alchemy"],
        "gecko_legacy": old["gecko"],
        "gecko_discovery": (DISCOVERY_ROOT,),
        "coordinator": old["coordinator"] + (COORDINATOR_ROOT,),
    }


def policy_document_v3(
    *, root_binding: Mapping[str, Any], v2_policy_bytes: bytes, bridge: Mapping[str, Any]
) -> dict[str, Any]:
    _verify_bridge(bridge)
    policy2 = _v2_policy(v2_policy_bytes)
    _need(
        set(root_binding) == {"root_id", "volume_uuid", "device", "inode"}, "v3_root_binding_shape"
    )
    _need(dict(root_binding) == policy2["root_binding"], "v3_root_binding_changed")
    _need(bridge.get("v2_policy_sha256") == sha256(v2_policy_bytes), "v3_bridge_policy_binding")
    root_sets = _v3_root_sets(policy2)
    return {
        "schema": POLICY_SCHEMA,
        "schema_version": VERSION,
        "root_binding": dict(root_binding),
        "shared_lock_path": SHARED_LOCK_PATH,
        "v2_policy_sha256": sha256(v2_policy_bytes),
        "v2_bridge_sha256": bridge["bridge_sha256"],
        "allocation_roots": {name: list(paths) for name, paths in root_sets.items()},
        "service_caps_bytes": dict(SERVICE_CAPS),
        "fixed_reserves_bytes": dict(FIXED_RESERVES),
        "fixed_free_floor_bytes": FIXED_FREE_FLOOR_BYTES,
        "coordinator_append_reserve_bytes": COORDINATOR_APPEND_RESERVE_BYTES,
        "capacity_rule": "allocated_plus_pending_plus_new_with_combined_fixed_free_floor",
        "v2_post_cutover_rule": "exact_frozen_policy_ledger_root_inventory_before_each_reservation",
        "ledger_rule": "append_only_canonical_sha256_chain_create_only_pointer_last",
    }


def _event(
    *, seq: int, kind: str, at_ns: int, previous: str | None, fields: Mapping[str, Any]
) -> dict[str, Any]:
    unsigned = {
        "schema": EVENT_SCHEMA,
        "schema_version": VERSION,
        "seq": seq,
        "kind": kind,
        "at_ns": at_ns,
        "previous_sha256": previous,
        "fields": dict(fields),
    }
    return {**unsigned, "event_sha256": sha256(canonical(unsigned))}


def migration_documents(
    *,
    v2_policy_bytes: bytes,
    v2_ledger_bytes: bytes,
    v2_root_inventory: Mapping[str, int],
    artifact_sha256_by_path: Mapping[str, str],
    bridge: Mapping[str, Any],
    root_binding: Mapping[str, Any],
    cutover_ns: int,
    migration_id: str,
) -> dict[str, bytes]:
    _need(_uint(cutover_ns) and cutover_ns > 0 and _id(migration_id), "v3_migration_identity")
    replayed_bridge = build_v2_bridge(
        v2_policy_bytes=v2_policy_bytes,
        v2_ledger_bytes=v2_ledger_bytes,
        v2_root_inventory=v2_root_inventory,
        artifact_sha256_by_path=artifact_sha256_by_path,
    )
    _need(dict(bridge) == replayed_bridge, "v3_migration_bridge_mismatch")
    _verify_bridge(bridge)
    policy = policy_document_v3(
        root_binding=root_binding, v2_policy_bytes=v2_policy_bytes, bridge=bridge
    )
    policy_bytes = canonical(policy)
    carry = bridge["pending_liabilities"]
    genesis_fields = {
        "policy_sha256": sha256(policy_bytes),
        "bridge_sha256": bridge["bridge_sha256"],
        "v2_policy_sha256": bridge["v2_policy_sha256"],
        "v2_ledger_sha256": bridge["v2_ledger_sha256"],
        "v2_tail_sequence": bridge["v2_tail_sequence"],
        "v2_tail_sha256": bridge["v2_tail_sha256"],
        "v2_root_inventory_sha256": bridge["v2_root_inventory_sha256"],
        "v2_reservation_ids": bridge["v2_reservation_ids"],
        "v2_request_ids": bridge["v2_request_ids"],
        "pending_liabilities": carry,
        "pending_liability_bytes": bridge["pending_liability_bytes"],
        "migration_id": migration_id,
        "root_binding": dict(root_binding),
    }
    genesis = _event(seq=0, kind="genesis", at_ns=cutover_ns, previous=None, fields=genesis_fields)
    ledger_bytes = canonical(genesis)
    pointer_core = {
        "schema": POINTER_SCHEMA,
        "schema_version": VERSION,
        "migration_id": migration_id,
        "policy_path": POLICY_PATH,
        "policy_sha256": sha256(policy_bytes),
        "ledger_path": LEDGER_PATH,
        "genesis_event_sha256": genesis["event_sha256"],
        "bridge_sha256": bridge["bridge_sha256"],
        "v2_policy_sha256": bridge["v2_policy_sha256"],
        "v2_ledger_sha256": bridge["v2_ledger_sha256"],
        "v2_tail_sha256": bridge["v2_tail_sha256"],
        "v2_root_inventory_sha256": bridge["v2_root_inventory_sha256"],
        "shared_lock_path": SHARED_LOCK_PATH,
        "root_binding": dict(root_binding),
        "create_only": True,
    }
    pointer = {**pointer_core, "pointer_sha256": sha256(canonical(pointer_core))}
    return {"policy": policy_bytes, "ledger": ledger_bytes, "pointer": canonical(pointer)}


def reconcile_migration_prefix(
    expected: Mapping[str, bytes],
    *,
    existing_policy: bytes | None = None,
    existing_ledger: bytes | None = None,
    existing_pointer: bytes | None = None,
) -> dict[str, Any]:
    """Return create-only writes in durable order; exact partial prefixes resume."""
    _need(set(expected) == {"policy", "ledger", "pointer"}, "v3_expected_documents")
    for name, present, data in (
        ("policy", existing_policy, expected["policy"]),
        ("ledger", existing_ledger, expected["ledger"]),
        ("pointer", existing_pointer, expected["pointer"]),
    ):
        _need(present is None or present == data, f"v3_{name}_create_only_conflict")
    _need(
        existing_pointer is None or (existing_policy is not None and existing_ledger is not None),
        "v3_pointer_before_genesis",
    )
    _need(existing_ledger is None or existing_policy is not None, "v3_ledger_without_policy")
    writes = []
    if existing_policy is None:
        writes.append({"path": POLICY_PATH, "payload": expected["policy"], "create_only": True})
    if existing_ledger is None:
        writes.append({"path": LEDGER_PATH, "payload": expected["ledger"], "create_only": True})
    if existing_pointer is None:
        writes.append({"path": POINTER_PATH, "payload": expected["pointer"], "create_only": True})
    return {
        "writes": writes,
        "activation_ready": existing_pointer == expected["pointer"],
        "pointer_last": True,
    }


def _verify_pointer(pointer_bytes: bytes, expected_docs: Mapping[str, bytes]) -> dict[str, Any]:
    value = _decode(pointer_bytes)
    _need(type(value) is dict and canonical(value) == pointer_bytes, "v3_pointer_canonical")
    expected = _decode(expected_docs["pointer"])
    _need(value == expected, "v3_pointer_mismatch")
    return value


def _verify_policy_bytes(policy_bytes: bytes, bridge: Mapping[str, Any]) -> dict[str, Any]:
    policy = _decode(policy_bytes)
    keys = {
        "schema",
        "schema_version",
        "root_binding",
        "shared_lock_path",
        "v2_policy_sha256",
        "v2_bridge_sha256",
        "allocation_roots",
        "service_caps_bytes",
        "fixed_reserves_bytes",
        "fixed_free_floor_bytes",
        "coordinator_append_reserve_bytes",
        "capacity_rule",
        "v2_post_cutover_rule",
        "ledger_rule",
    }
    _need(
        type(policy) is dict and canonical(policy) == policy_bytes and set(policy) == keys,
        "v3_policy_shape",
    )
    _need(
        policy["schema"] == POLICY_SCHEMA and policy["schema_version"] == VERSION,
        "v3_policy_version",
    )
    _need(policy["shared_lock_path"] == SHARED_LOCK_PATH, "v3_policy_lock")
    _need(policy["v2_policy_sha256"] == bridge["v2_policy_sha256"], "v3_policy_v2_hash")
    _need(policy["v2_bridge_sha256"] == bridge["bridge_sha256"], "v3_policy_bridge_hash")
    _need(policy["service_caps_bytes"] == SERVICE_CAPS, "v3_policy_caps")
    _need(policy["fixed_reserves_bytes"] == FIXED_RESERVES, "v3_policy_reserves")
    _need(policy["fixed_free_floor_bytes"] == FIXED_FREE_FLOOR_BYTES, "v3_policy_floor")
    _need(
        policy["coordinator_append_reserve_bytes"] == COORDINATOR_APPEND_RESERVE_BYTES,
        "v3_policy_append_reserve",
    )
    _need(
        policy["capacity_rule"] == "allocated_plus_pending_plus_new_with_combined_fixed_free_floor",
        "v3_policy_capacity_rule",
    )
    _need(
        policy["v2_post_cutover_rule"]
        == "exact_frozen_policy_ledger_root_inventory_before_each_reservation",
        "v3_policy_prefix_rule",
    )
    _need(
        policy["ledger_rule"] == "append_only_canonical_sha256_chain_create_only_pointer_last",
        "v3_policy_ledger_rule",
    )
    binding = policy["root_binding"]
    _need(
        type(binding) is dict
        and set(binding) == {"root_id", "volume_uuid", "device", "inode"}
        and _id(binding["root_id"])
        and type(binding["volume_uuid"]) is str
        and VOLUME_UUID_RE.fullmatch(binding["volume_uuid"].lower())
        and _uint(binding["device"])
        and type(binding["inode"]) is int
        and binding["inode"] > 0,
        "v3_policy_root_binding",
    )
    old = bridge["v2_allocation_roots"]
    expected_roots = {
        "helius": old["helius"],
        "alchemy": old["alchemy"],
        "gecko_legacy": old["gecko"],
        "gecko_discovery": [DISCOVERY_ROOT],
        "coordinator": old["coordinator"] + [COORDINATOR_ROOT],
    }
    _need(policy["allocation_roots"] == expected_roots, "v3_policy_roots")
    all_roots = [path for paths in expected_roots.values() for path in paths]
    _need(
        len(all_roots) == len(set(all_roots))
        and not any(
            left.startswith(right + "/") or right.startswith(left + "/")
            for index, left in enumerate(all_roots)
            for right in all_roots[index + 1 :]
        ),
        "v3_policy_root_overlap",
    )
    return policy


def verify_v3_ledger(
    ledger_bytes: bytes, *, expected_policy_bytes: bytes, bridge: Mapping[str, Any]
) -> list[dict[str, Any]]:
    bridge = _verify_bridge(bridge)
    policy = _verify_policy_bytes(expected_policy_bytes, bridge)
    _need(type(ledger_bytes) is bytes and ledger_bytes.endswith(b"\n"), "v3_ledger_torn")
    events: list[dict[str, Any]] = []
    previous = None
    imported_reservation_ids = set(bridge["v2_reservation_ids"])
    reservation_ids: set[str] = set()
    epoch_hold_ids: set[str] = set()
    epoch_hold_page_ids: set[str] = set()
    request_ids = {(row["service"], row["request_id"]) for row in bridge["v2_request_ids"]}
    terminal: set[str] = set()
    terminal_kind: dict[str, str] = {}
    epoch_holds: dict[str, dict[str, Any]] = {}
    prior_at_ns = 0
    for seq, line in enumerate(ledger_bytes.splitlines(keepends=True)):
        value = _decode(line)
        _need(type(value) is dict and canonical(value) == line, "v3_ledger_not_canonical")
        expected_keys = {
            "schema",
            "schema_version",
            "seq",
            "kind",
            "at_ns",
            "previous_sha256",
            "fields",
            "event_sha256",
        }
        _need(
            set(value) == expected_keys
            and value["schema"] == EVENT_SCHEMA
            and value["schema_version"] == VERSION
            and type(value["seq"]) is int
            and value["seq"] == seq
            and type(value["at_ns"]) is int
            and value["at_ns"] > 0
            and value["at_ns"] > prior_at_ns
            and value["previous_sha256"] == previous
            and type(value["fields"]) is dict
            and _hash(value["event_sha256"]),
            "v3_ledger_event_shape",
        )
        unsigned = {key: item for key, item in value.items() if key != "event_sha256"}
        _need(value["event_sha256"] == sha256(canonical(unsigned)), "v3_ledger_event_hash")
        fields = value["fields"]
        if seq == 0:
            genesis_keys = {
                "policy_sha256",
                "bridge_sha256",
                "v2_policy_sha256",
                "v2_ledger_sha256",
                "v2_tail_sequence",
                "v2_tail_sha256",
                "v2_root_inventory_sha256",
                "v2_reservation_ids",
                "v2_request_ids",
                "pending_liabilities",
                "pending_liability_bytes",
                "migration_id",
                "root_binding",
            }
            _need(set(fields) == genesis_keys, "v3_genesis_shape")
            _need(value["kind"] == "genesis", "v3_genesis_kind")
            _need(fields.get("policy_sha256") == sha256(expected_policy_bytes), "v3_genesis_policy")
            _need(fields.get("bridge_sha256") == bridge["bridge_sha256"], "v3_genesis_bridge")
            _need(fields.get("v2_tail_sha256") == bridge["v2_tail_sha256"], "v3_genesis_v2_tail")
            _need(
                fields.get("v2_root_inventory_sha256") == bridge["v2_root_inventory_sha256"],
                "v3_genesis_inventory",
            )
            _need(
                fields.get("pending_liabilities") == bridge["pending_liabilities"],
                "v3_genesis_liabilities",
            )
            _need(
                fields.get("v2_policy_sha256") == bridge["v2_policy_sha256"], "v3_genesis_v2_policy"
            )
            _need(
                fields.get("v2_ledger_sha256") == bridge["v2_ledger_sha256"], "v3_genesis_v2_ledger"
            )
            _need(
                fields.get("v2_tail_sequence") == bridge["v2_tail_sequence"],
                "v3_genesis_v2_sequence",
            )
            _need(
                fields.get("v2_reservation_ids") == bridge["v2_reservation_ids"],
                "v3_genesis_v2_reservations",
            )
            _need(
                fields.get("v2_request_ids") == bridge["v2_request_ids"], "v3_genesis_v2_requests"
            )
            _need(
                fields.get("pending_liability_bytes") == bridge["pending_liability_bytes"],
                "v3_genesis_v2_liability_bytes",
            )
            _need(
                fields.get("root_binding") == policy.get("root_binding"), "v3_genesis_root_binding"
            )
            _need(_id(fields.get("migration_id")), "v3_genesis_migration_id")
        elif value["kind"] == "epoch_hold_open":
            _need(
                set(fields)
                == {
                    "hold_id",
                    "service",
                    "successor_epoch_id",
                    "standby_sha256",
                    "reserved_bytes",
                    "intent_path",
                    "outcome_path",
                    "intent_sha256",
                    "page_request_ids",
                },
                "v3_epoch_hold_shape",
            )
            hold_id = fields.get("hold_id")
            service = fields.get("service")
            page_ids = fields.get("page_request_ids")
            roots = policy["allocation_roots"].get(service)
            _need(
                _id(hold_id)
                and hold_id not in epoch_holds
                and hold_id not in reservation_ids
                and hold_id not in epoch_hold_ids
                and service == "gecko_discovery"
                and _id(fields.get("successor_epoch_id"))
                and _hash(fields.get("standby_sha256"))
                and _uint(fields.get("reserved_bytes"))
                and fields["reserved_bytes"] == DISCOVERY_EPOCH_HOLD_BYTES
                and _relative(fields.get("intent_path"))
                and _relative(fields.get("outcome_path"))
                and _hash(fields.get("intent_sha256"))
                and fields["intent_path"].startswith(roots[0] + "/")
                and fields["outcome_path"].startswith(roots[0] + "/")
                and type(page_ids) is list
                and len(page_ids) == DISCOVERY_EPOCH_PAGE_COUNT
                and len(page_ids) == len(set(page_ids))
                and all(_hash(item) for item in page_ids)
                and not any(item in epoch_hold_page_ids for item in page_ids)
                and not any(
                    old_request == item for _, old_request in request_ids for item in page_ids
                ),
                "v3_epoch_hold_binding",
            )
            epoch_holds[hold_id] = {
                "remaining_bytes": fields["reserved_bytes"],
                "standby_sha256": fields["standby_sha256"],
                "successor_epoch_id": fields["successor_epoch_id"],
                "page_request_ids": list(page_ids),
                "consumed_request_ids": [],
                "last_reserved_ordinal": 0,
                "reservation_ids": [],
                "closed": False,
            }
            epoch_hold_ids.add(hold_id)
            epoch_hold_page_ids.update(page_ids)
        elif value["kind"] in {"reserved", "page_reserved_from_hold"}:
            expected_reservation_fields = {
                "reservation_id",
                "service",
                "request_id",
                "reserved_bytes",
                "intent_path",
                "outcome_path",
                "intent_sha256",
                "allocated_before_bytes",
            }
            if value["kind"] == "page_reserved_from_hold":
                expected_reservation_fields |= {"hold_id", "standby_sha256", "page_ordinal"}
            _need(
                set(fields) == expected_reservation_fields,
                "v3_reservation_shape",
            )
            rid = fields.get("reservation_id")
            service = fields.get("service")
            request_id = fields.get("request_id")
            _need(
                _id(rid)
                and rid not in reservation_ids
                and rid not in imported_reservation_ids
                and rid not in epoch_hold_ids,
                "v3_reservation_id_reused",
            )
            _need(service in SERVICE_CAPS and service != "coordinator", "v3_reservation_service")
            _need(type(request_id) is str and request_id, "v3_request_id")
            _need(
                (service, request_id) not in request_ids
                and not any(old_req == request_id for _, old_req in request_ids),
                "v3_request_id_reused",
            )
            if value["kind"] == "reserved":
                _need(request_id not in epoch_hold_page_ids, "v3_request_id_reserved_by_hold")
            _need(
                _uint(fields.get("reserved_bytes")) and fields["reserved_bytes"] > 0,
                "v3_reservation_bytes",
            )
            _need(
                _relative(fields.get("intent_path"))
                and _relative(fields.get("outcome_path"))
                and fields["intent_path"] != fields["outcome_path"],
                "v3_reservation_path",
            )
            _need(
                _hash(fields.get("intent_sha256")) and _uint(fields.get("allocated_before_bytes")),
                "v3_reservation_binding",
            )
            _need(fields["reserved_bytes"] <= SERVICE_CAPS[service], "v3_reservation_cap")
            allowed_roots = policy["allocation_roots"].get(service)
            _need(type(allowed_roots) is list and allowed_roots, "v3_service_roots_missing")
            _need(
                any(fields["intent_path"].startswith(root + "/") for root in allowed_roots),
                "v3_intent_outside_service_root",
            )
            _need(
                any(fields["outcome_path"].startswith(root + "/") for root in allowed_roots),
                "v3_outcome_outside_service_root",
            )
            if value["kind"] == "page_reserved_from_hold":
                hold_id = fields.get("hold_id")
                hold = epoch_holds.get(hold_id)
                request_id = fields["request_id"]
                _need(
                    hold is not None
                    and not hold["closed"]
                    and fields.get("standby_sha256") == hold["standby_sha256"]
                    and request_id in hold["page_request_ids"]
                    and request_id not in hold["consumed_request_ids"]
                    and _uint(fields.get("page_ordinal"))
                    and fields["page_ordinal"] > hold["last_reserved_ordinal"]
                    and fields["page_ordinal"] <= len(hold["page_request_ids"])
                    and hold["page_request_ids"][fields["page_ordinal"] - 1] == request_id
                    and fields["reserved_bytes"] == DISCOVERY_PAGE_RESERVE_BYTES
                    and hold["remaining_bytes"] >= fields["reserved_bytes"],
                    "v3_hold_page_conversion",
                )
                hold["remaining_bytes"] -= fields["reserved_bytes"]
                hold["consumed_request_ids"].append(request_id)
                hold["last_reserved_ordinal"] = fields["page_ordinal"]
                hold["reservation_ids"].append(rid)
            reservation_ids.add(rid)
            request_ids.add((service, request_id))
        elif value["kind"] == "epoch_hold_close":
            _need(
                set(fields) == {"hold_id", "standby_sha256", "remaining_bytes", "outcome_sha256"},
                "v3_epoch_hold_close_shape",
            )
            hold = epoch_holds.get(fields.get("hold_id"))
            _need(
                hold is not None
                and not hold["closed"]
                and fields.get("standby_sha256") == hold["standby_sha256"]
                and fields.get("remaining_bytes") == hold["remaining_bytes"]
                and all(rid in terminal for rid in hold["reservation_ids"])
                and _hash(fields.get("outcome_sha256")),
                "v3_epoch_hold_close_binding",
            )
            hold["closed"] = True
            request_ids.update(
                ("gecko_discovery", request_id) for request_id in hold["page_request_ids"]
            )
        elif value["kind"] in {"settled", "ambiguous_terminal", "released"}:
            _need(
                set(fields)
                == {"reservation_id", "outcome", "outcome_sha256", "actual_allocated_bytes"},
                "v3_terminal_shape",
            )
            rid = fields.get("reservation_id")
            _need(rid in reservation_ids and rid not in terminal, "v3_terminal_reservation")
            _need(type(fields.get("outcome")) is str and fields["outcome"], "v3_terminal_outcome")
            _need(
                _hash(fields.get("outcome_sha256")) and _uint(fields.get("actual_allocated_bytes")),
                "v3_terminal_binding",
            )
            _need(
                value["kind"] != "released" or fields["outcome"] == "pre_intent_absent",
                "v3_release_outcome",
            )
            terminal.add(rid)
            terminal_kind[rid] = value["kind"]
        else:
            raise CoordinatorV3Error("v3_ledger_kind")
        previous = value["event_sha256"]
        prior_at_ns = value["at_ns"]
        events.append(value)
    _need(events, "v3_ledger_empty")
    return events


def verify_pointer_and_ledger(
    *,
    pointer_bytes: bytes,
    ledger_bytes: bytes,
    policy_bytes: bytes,
    bridge: Mapping[str, Any],
) -> list[dict[str, Any]]:
    events = verify_v3_ledger(ledger_bytes, expected_policy_bytes=policy_bytes, bridge=bridge)
    pointer = _decode(pointer_bytes)
    _need(type(pointer) is dict and canonical(pointer) == pointer_bytes, "v3_pointer_canonical")
    expected_fields = {
        "schema": POINTER_SCHEMA,
        "schema_version": VERSION,
        "migration_id": events[0]["fields"]["migration_id"],
        "policy_path": POLICY_PATH,
        "policy_sha256": sha256(policy_bytes),
        "ledger_path": LEDGER_PATH,
        "genesis_event_sha256": events[0]["event_sha256"],
        "bridge_sha256": bridge["bridge_sha256"],
        "v2_policy_sha256": bridge["v2_policy_sha256"],
        "v2_ledger_sha256": bridge["v2_ledger_sha256"],
        "v2_tail_sha256": bridge["v2_tail_sha256"],
        "v2_root_inventory_sha256": bridge["v2_root_inventory_sha256"],
        "shared_lock_path": SHARED_LOCK_PATH,
        "root_binding": events[0]["fields"]["root_binding"],
        "create_only": True,
    }
    _need(set(pointer) == set(expected_fields) | {"pointer_sha256"}, "v3_pointer_shape")
    _need(
        pointer == {**expected_fields, "pointer_sha256": sha256(canonical(expected_fields))},
        "v3_pointer_binding",
    )
    return events


def append_reservation(
    *,
    ledger_bytes: bytes,
    policy_bytes: bytes,
    pointer_bytes: bytes,
    bridge: Mapping[str, Any],
    v2_policy_bytes: bytes,
    current_v2_ledger_bytes: bytes,
    current_v2_root_inventory: Mapping[str, int],
    v2_artifact_sha256_by_path: Mapping[str, str],
    current_root_inventory: Mapping[str, int],
    free_bytes: int,
    at_ns: int,
    service: str,
    reservation_id: str,
    request_id: str,
    intent_path: str,
    outcome_path: str,
    intent_sha256: str,
    reserve_bytes: int,
) -> bytes:
    """Pure admission decision; host must hold shared v2 lock across this call/write."""
    verify_v2_prefix(
        bridge,
        v2_policy_bytes=v2_policy_bytes,
        v2_ledger_bytes=current_v2_ledger_bytes,
        v2_root_inventory=current_v2_root_inventory,
        artifact_sha256_by_path=v2_artifact_sha256_by_path,
    )
    docs_pointer = _verify_policy_bytes(policy_bytes, bridge)
    verify_pointer_and_ledger(
        pointer_bytes=pointer_bytes,
        ledger_bytes=ledger_bytes,
        policy_bytes=policy_bytes,
        bridge=bridge,
    )
    roots = docs_pointer["allocation_roots"]
    expected_paths = sorted(path for group in roots.values() for path in group)
    inventory = _inventory_snapshot(current_root_inventory, expected_paths)
    events = verify_v3_ledger(ledger_bytes, expected_policy_bytes=policy_bytes, bridge=bridge)
    _need(
        type(at_ns) is int and at_ns > events[-1]["at_ns"] and _uint(free_bytes),
        "v3_admission_time_space",
    )
    _need(service in SERVICE_CAPS and service != "coordinator", "v3_admission_service")
    _need(_id(reservation_id) and type(request_id) is str and request_id, "v3_admission_identity")
    _need(
        _relative(intent_path) and _relative(outcome_path) and intent_path != outcome_path,
        "v3_admission_paths",
    )
    _need(
        _hash(intent_sha256) and type(reserve_bytes) is int and reserve_bytes > 0,
        "v3_admission_reserve",
    )
    _need(intent_path.startswith(roots[service][0] + "/"), "v3_intent_outside_service_root")
    _need(outcome_path.startswith(roots[service][0] + "/"), "v3_outcome_outside_service_root")
    _need(reserve_bytes <= SERVICE_CAPS[service], "v3_reservation_exceeds_service_cap")
    reservations: dict[str, Mapping[str, Any]] = {}
    terminal: set[str] = set()
    terminal_kinds: dict[str, str] = {}
    request_ids = {(row["service"], row["request_id"]) for row in bridge["v2_request_ids"]}
    used_reservations = set(bridge["v2_reservation_ids"])
    for event in events[1:]:
        if event["kind"] in {"reserved", "page_reserved_from_hold"}:
            fields = event["fields"]
            reservations[fields["reservation_id"]] = fields
            request_ids.add((fields["service"], fields["request_id"]))
            used_reservations.add(fields["reservation_id"])
        elif event["kind"] in {"settled", "ambiguous_terminal", "released"}:
            terminal.add(event["fields"]["reservation_id"])
            terminal_kinds[event["fields"]["reservation_id"]] = event["kind"]
    _need(reservation_id not in used_reservations, "v3_reservation_id_reused")
    _need(
        (service, request_id) not in request_ids
        and not any(req == request_id for _, req in request_ids),
        "v3_request_id_reused",
    )
    current_allocated = {
        name: sum(inventory[path] for path in roots[name]) for name in SERVICE_CAPS
    }
    pending = {name: 0 for name in SERVICE_CAPS}
    for liability in bridge["pending_liabilities"]:
        name = "gecko_legacy" if liability["service"] == "gecko" else liability["service"]
        pending[name] += liability["liability_bytes"]
    for rid, fields in reservations.items():
        if rid not in terminal or terminal_kinds[rid] == "ambiguous_terminal":
            pending[fields["service"]] += fields["reserved_bytes"]
    holds: dict[str, dict[str, Any]] = {}
    for event in events[1:]:
        fields = event["fields"]
        if event["kind"] == "epoch_hold_open":
            holds[fields["hold_id"]] = {
                "service": fields["service"],
                "remaining": fields["reserved_bytes"],
                "closed": False,
            }
        elif event["kind"] == "page_reserved_from_hold":
            holds[fields["hold_id"]]["remaining"] -= fields["reserved_bytes"]
        elif event["kind"] == "epoch_hold_close":
            holds[fields["hold_id"]]["closed"] = True
    for hold in holds.values():
        if not hold["closed"]:
            pending[hold["service"]] += hold["remaining"]
    for name, cap in SERVICE_CAPS.items():
        new_bytes = reserve_bytes if name == service else 0
        if name == "coordinator":
            new_bytes += COORDINATOR_APPEND_RESERVE_BYTES
        _need(current_allocated[name] + pending[name] + new_bytes <= cap, f"v3_{name}_capacity")
    pending_total = sum(pending.values())
    _need(
        free_bytes
        >= FIXED_FREE_FLOOR_BYTES
        + pending_total
        + reserve_bytes
        + COORDINATOR_APPEND_RESERVE_BYTES,
        "v3_combined_free_floor",
    )
    fields = {
        "reservation_id": reservation_id,
        "service": service,
        "request_id": request_id,
        "reserved_bytes": reserve_bytes,
        "intent_path": intent_path,
        "outcome_path": outcome_path,
        "intent_sha256": intent_sha256,
        "allocated_before_bytes": current_allocated[service],
    }
    event = _event(
        seq=len(events),
        kind="reserved",
        at_ns=at_ns,
        previous=events[-1]["event_sha256"],
        fields=fields,
    )
    next_ledger = ledger_bytes + canonical(event)
    _need(len(next_ledger) <= MAX_LEDGER_BYTES, "v3_ledger_cap")
    verify_v3_ledger(next_ledger, expected_policy_bytes=policy_bytes, bridge=bridge)
    return next_ledger


def append_epoch_hold_open(
    *,
    ledger_bytes: bytes,
    policy_bytes: bytes,
    pointer_bytes: bytes,
    bridge: Mapping[str, Any],
    v2_policy_bytes: bytes,
    current_v2_ledger_bytes: bytes,
    current_v2_root_inventory: Mapping[str, int],
    v2_artifact_sha256_by_path: Mapping[str, str],
    current_root_inventory: Mapping[str, int],
    free_bytes: int,
    at_ns: int,
    hold_id: str,
    successor_epoch_id: str,
    standby_sha256: str,
    intent_path: str,
    outcome_path: str,
    intent_sha256: str,
    reserved_bytes: int,
    page_request_ids: Sequence[str],
) -> bytes:
    """Open an epoch-wide physical runway hold from a pinned standby manifest."""
    verify_v2_prefix(
        bridge,
        v2_policy_bytes=v2_policy_bytes,
        v2_ledger_bytes=current_v2_ledger_bytes,
        v2_root_inventory=current_v2_root_inventory,
        artifact_sha256_by_path=v2_artifact_sha256_by_path,
    )
    policy = _verify_policy_bytes(policy_bytes, bridge)
    events = verify_pointer_and_ledger(
        pointer_bytes=pointer_bytes,
        ledger_bytes=ledger_bytes,
        policy_bytes=policy_bytes,
        bridge=bridge,
    )
    roots = policy["allocation_roots"]
    paths = sorted(path for group in roots.values() for path in group)
    inventory = _inventory_snapshot(current_root_inventory, paths)
    _need(type(at_ns) is int and at_ns > events[-1]["at_ns"], "v3_hold_open_time")
    _need(
        _id(hold_id)
        and _id(successor_epoch_id)
        and _hash(standby_sha256)
        and _hash(intent_sha256)
        and _relative(intent_path)
        and _relative(outcome_path)
        and intent_path != outcome_path
        and intent_path.startswith(roots["gecko_discovery"][0] + "/")
        and outcome_path.startswith(roots["gecko_discovery"][0] + "/")
        and type(reserved_bytes) is int
        and 0 < reserved_bytes <= SERVICE_CAPS["gecko_discovery"]
        and type(page_request_ids) in (list, tuple)
        and len(page_request_ids) > 0
        and len(set(page_request_ids)) == len(page_request_ids)
        and all(type(request_id) is str and request_id for request_id in page_request_ids),
        "v3_hold_open_inputs",
    )
    current_allocated = {
        name: sum(inventory[path] for path in roots[name]) for name in SERVICE_CAPS
    }
    pending = {name: 0 for name in SERVICE_CAPS}
    for liability in bridge["pending_liabilities"]:
        name = "gecko_legacy" if liability["service"] == "gecko" else liability["service"]
        pending[name] += liability["liability_bytes"]
    for event in events[1:]:
        fields = event["fields"]
        if event["kind"] in {"reserved", "page_reserved_from_hold"}:
            reservation_service = fields["service"]
            rid = fields["reservation_id"]
            terminal_kind = next(
                (
                    candidate["kind"]
                    for candidate in events
                    if candidate["kind"] in {"settled", "ambiguous_terminal", "released"}
                    and candidate["fields"].get("reservation_id") == rid
                ),
                None,
            )
            if terminal_kind is None or terminal_kind == "ambiguous_terminal":
                pending[reservation_service] += fields["reserved_bytes"]
        elif event["kind"] == "epoch_hold_open":
            hold_id_current = fields["hold_id"]
            remaining = fields["reserved_bytes"]
            for candidate in events:
                if (
                    candidate["kind"] == "page_reserved_from_hold"
                    and candidate["fields"].get("hold_id") == hold_id_current
                ):
                    remaining -= candidate["fields"]["reserved_bytes"]
                elif (
                    candidate["kind"] == "epoch_hold_close"
                    and candidate["fields"].get("hold_id") == hold_id_current
                ):
                    remaining = 0
            pending[fields["service"]] += max(0, remaining)
    _need(
        current_allocated["gecko_discovery"] + pending["gecko_discovery"] + reserved_bytes
        <= SERVICE_CAPS["gecko_discovery"],
        "v3_gecko_discovery_capacity",
    )
    _need(
        free_bytes
        >= FIXED_FREE_FLOOR_BYTES
        + sum(pending.values())
        + reserved_bytes
        + COORDINATOR_APPEND_RESERVE_BYTES,
        "v3_combined_free_floor",
    )
    fields = {
        "hold_id": hold_id,
        "service": "gecko_discovery",
        "successor_epoch_id": successor_epoch_id,
        "standby_sha256": standby_sha256,
        "reserved_bytes": reserved_bytes,
        "intent_path": intent_path,
        "outcome_path": outcome_path,
        "intent_sha256": intent_sha256,
        "page_request_ids": list(page_request_ids),
    }
    event = _event(
        seq=len(events),
        kind="epoch_hold_open",
        at_ns=at_ns,
        previous=events[-1]["event_sha256"],
        fields=fields,
    )
    next_ledger = ledger_bytes + canonical(event)
    verify_v3_ledger(next_ledger, expected_policy_bytes=policy_bytes, bridge=bridge)
    return next_ledger


def append_page_reserved_from_hold(
    *,
    ledger_bytes: bytes,
    policy_bytes: bytes,
    pointer_bytes: bytes,
    bridge: Mapping[str, Any],
    at_ns: int,
    hold_id: str,
    standby_sha256: str,
    reservation_id: str,
    request_id: str,
    page_ordinal: int,
    intent_path: str,
    outcome_path: str,
    intent_sha256: str,
    reserved_bytes: int,
    allocated_before_bytes: int,
) -> bytes:
    """Atomically convert held runway into one at-most-once page reservation."""
    policy = _verify_policy_bytes(policy_bytes, bridge)
    events = verify_pointer_and_ledger(
        pointer_bytes=pointer_bytes,
        ledger_bytes=ledger_bytes,
        policy_bytes=policy_bytes,
        bridge=bridge,
    )
    _need(type(at_ns) is int and at_ns > events[-1]["at_ns"], "v3_hold_convert_time")
    _need(
        _id(reservation_id)
        and type(request_id) is str
        and request_id
        and _uint(page_ordinal)
        and _relative(intent_path)
        and _relative(outcome_path)
        and intent_path != outcome_path
        and _hash(intent_sha256)
        and reserved_bytes == DISCOVERY_PAGE_RESERVE_BYTES
        and _uint(allocated_before_bytes),
        "v3_hold_convert_inputs",
    )
    hold_open = next(
        (
            event
            for event in events
            if event["kind"] == "epoch_hold_open" and event["fields"].get("hold_id") == hold_id
        ),
        None,
    )
    _need(hold_open is not None, "v3_hold_missing")
    hold_fields = hold_open["fields"]
    _need(hold_fields["standby_sha256"] == standby_sha256, "v3_hold_standby_mismatch")
    converted = 0
    closed = False
    for event in events:
        fields = event["fields"]
        if event["kind"] == "page_reserved_from_hold" and fields.get("hold_id") == hold_id:
            converted += fields["reserved_bytes"]
        elif event["kind"] == "epoch_hold_close" and fields.get("hold_id") == hold_id:
            closed = True
    _need(not closed, "v3_hold_closed")
    remaining = hold_fields["reserved_bytes"] - converted
    consumed_ids = [
        event["fields"]["request_id"]
        for event in events
        if event["kind"] == "page_reserved_from_hold" and event["fields"].get("hold_id") == hold_id
    ]
    consumed_ordinals = [
        event["fields"]["page_ordinal"]
        for event in events
        if event["kind"] == "page_reserved_from_hold" and event["fields"].get("hold_id") == hold_id
    ]
    _need(
        request_id in hold_fields["page_request_ids"]
        and len(consumed_ids) < len(hold_fields["page_request_ids"])
        and not any(
            event["kind"] == "page_reserved_from_hold"
            and event["fields"].get("hold_id") == hold_id
            and event["fields"].get("request_id") == request_id
            for event in events
        )
        and remaining >= reserved_bytes,
        "v3_hold_page_not_available",
    )
    _need(
        page_ordinal <= len(hold_fields["page_request_ids"])
        and hold_fields["page_request_ids"][page_ordinal - 1] == request_id
        and page_ordinal > max(consumed_ordinals, default=0),
        "v3_hold_page_ordinal",
    )
    roots = policy["allocation_roots"]
    _need(
        intent_path.startswith(roots["gecko_discovery"][0] + "/")
        and outcome_path.startswith(roots["gecko_discovery"][0] + "/"),
        "v3_hold_page_path",
    )
    fields = {
        "reservation_id": reservation_id,
        "service": "gecko_discovery",
        "request_id": request_id,
        "reserved_bytes": reserved_bytes,
        "intent_path": intent_path,
        "outcome_path": outcome_path,
        "intent_sha256": intent_sha256,
        "allocated_before_bytes": allocated_before_bytes,
        "hold_id": hold_id,
        "standby_sha256": standby_sha256,
        "page_ordinal": page_ordinal,
    }
    event = _event(
        seq=len(events),
        kind="page_reserved_from_hold",
        at_ns=at_ns,
        previous=events[-1]["event_sha256"],
        fields=fields,
    )
    result = ledger_bytes + canonical(event)
    _need(len(result) <= MAX_LEDGER_BYTES, "v3_ledger_cap")
    verify_v3_ledger(result, expected_policy_bytes=policy_bytes, bridge=bridge)
    return result


def append_epoch_hold_close(
    *,
    ledger_bytes: bytes,
    policy_bytes: bytes,
    pointer_bytes: bytes,
    bridge: Mapping[str, Any],
    at_ns: int,
    hold_id: str,
    standby_sha256: str,
    remaining_bytes: int,
    outcome_sha256: str,
) -> bytes:
    """Release only the unconverted remainder after all epoch slots terminalize."""
    events = verify_pointer_and_ledger(
        pointer_bytes=pointer_bytes,
        ledger_bytes=ledger_bytes,
        policy_bytes=policy_bytes,
        bridge=bridge,
    )
    _need(type(at_ns) is int and at_ns > events[-1]["at_ns"], "v3_hold_close_time")
    opened = next(
        (
            event
            for event in events
            if event["kind"] == "epoch_hold_open" and event["fields"].get("hold_id") == hold_id
        ),
        None,
    )
    _need(opened is not None, "v3_hold_close_missing")
    fields = opened["fields"]
    converted = sum(
        event["fields"]["reserved_bytes"]
        for event in events
        if event["kind"] == "page_reserved_from_hold" and event["fields"].get("hold_id") == hold_id
    )
    _need(
        fields["standby_sha256"] == standby_sha256
        and remaining_bytes == fields["reserved_bytes"] - converted
        and _hash(outcome_sha256)
        and not any(
            event["kind"] == "epoch_hold_close" and event["fields"].get("hold_id") == hold_id
            for event in events
        ),
        "v3_hold_close_binding",
    )
    event_fields = {
        "hold_id": hold_id,
        "standby_sha256": standby_sha256,
        "remaining_bytes": remaining_bytes,
        "outcome_sha256": outcome_sha256,
    }
    event = _event(
        seq=len(events),
        kind="epoch_hold_close",
        at_ns=at_ns,
        previous=events[-1]["event_sha256"],
        fields=event_fields,
    )
    result = ledger_bytes + canonical(event)
    verify_v3_ledger(result, expected_policy_bytes=policy_bytes, bridge=bridge)
    return result


def append_terminal(
    *,
    ledger_bytes: bytes,
    policy_bytes: bytes,
    bridge: Mapping[str, Any],
    at_ns: int,
    reservation_id: str,
    outcome: str,
    outcome_sha256: str,
    actual_allocated_bytes: int,
    kind: str,
) -> bytes:
    _need(kind in {"settled", "ambiguous_terminal", "released"}, "v3_terminal_kind")
    events = verify_v3_ledger(ledger_bytes, expected_policy_bytes=policy_bytes, bridge=bridge)
    _need(type(at_ns) is int and at_ns > events[-1]["at_ns"], "v3_terminal_time")
    _need(
        _id(reservation_id) and type(outcome) is str and outcome and _hash(outcome_sha256),
        "v3_terminal_input",
    )
    _need(_uint(actual_allocated_bytes), "v3_terminal_allocated")
    _need(kind != "released" or outcome == "pre_intent_absent", "v3_terminal_release")
    _need(
        any(
            event["kind"] in {"reserved", "page_reserved_from_hold"}
            and event["fields"]["reservation_id"] == reservation_id
            for event in events
        ),
        "v3_terminal_reservation_missing",
    )
    _need(
        not any(
            event["kind"] in {"settled", "ambiguous_terminal", "released"}
            and event["fields"]["reservation_id"] == reservation_id
            for event in events
        ),
        "v3_terminal_duplicate",
    )
    event = _event(
        seq=len(events),
        kind=kind,
        at_ns=at_ns,
        previous=events[-1]["event_sha256"],
        fields={
            "reservation_id": reservation_id,
            "outcome": outcome,
            "outcome_sha256": outcome_sha256,
            "actual_allocated_bytes": actual_allocated_bytes,
        },
    )
    result = ledger_bytes + canonical(event)
    verify_v3_ledger(result, expected_policy_bytes=policy_bytes, bridge=bridge)
    return result
