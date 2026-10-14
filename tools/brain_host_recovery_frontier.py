#!/usr/bin/env python3
"""Compose a synthetic post-capture fingerprint and storage fixture around a recovery set.

The v1 recovery-set and member artifacts remain unchanged. This envelope binds their
exact hashes, a caller-supplied post-capture fingerprint assertion, and a separately
verified synthetic storage corpus fixture. It does not prove capture from an immutable
source cutoff or qualify a real owner recovery or factory reset.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

from tools import brain_host_recovery_set as recovery_set
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256

SCHEMA = "brain-host-recovery-frontier-v1"
SPEC_SCHEMA = "brain-host-recovery-frontier-spec-v1"
RESERVATION_SCHEMA = "brain-host-recovery-frontier-reservation-v1"
COMPLETE_SCHEMA = "brain-host-recovery-frontier-complete-v1"
BASE = "host-recovery/recovery-frontiers-v1"
K_MANIFEST_SCHEMA = "brain-host-synthetic-durable-frontier-v1"
K_COMPLETE_SCHEMA = "brain-host-synthetic-durable-frontier-complete-v1"
K_END_SCHEMA = "brain-host-synthetic-stream-end-v1"
K_STOP_SCHEMA = "brain-host-synthetic-stop-state-v1"
MAX_SPEC_BYTES = 4 * 1024 * 1024
ID = re.compile(r"[0-9a-f]{32}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
ROLES = set(recovery_set.REQUIRED_ROLES)


class RecoveryFrontierError(ValueError):
    """A frontier proof or envelope is missing, ambiguous, or inconsistent."""


def _json(raw: bytes, label: str) -> dict[str, Any]:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise RecoveryFrontierError(f"{label}_duplicate_key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryFrontierError(f"{label}_invalid") from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise RecoveryFrontierError(f"{label}_noncanonical")
    return value


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise RecoveryFrontierError(f"{label}_invalid")
    return value


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise RecoveryFrontierError(f"{label}_invalid")
    return value


def _verify_k_frontier(
    writer: DescriptorWriter,
    value: dict[str, Any],
    *,
    expected_volume_uuid: str,
    expected_root_id: str,
) -> dict[str, Any]:
    fields = {
        "schema",
        "scenario",
        "frontier_id",
        "volume_uuid",
        "root_id",
        "manifest_path",
        "complete_path",
        "manifest_sha256",
        "complete_sha256",
        "vector",
    }
    if set(value) != fields or value.get("schema") != "brain-host-durable-frontier-proof-v1":
        raise RecoveryFrontierError("durable_frontier_contract_invalid")
    if value.get("scenario") != "synthetic":
        raise RecoveryFrontierError("real_durable_frontier_verifier_unavailable")
    frontier_id = _id(value["frontier_id"], "durable_frontier_id")
    if value.get("volume_uuid") != expected_volume_uuid or value.get("root_id") != expected_root_id:
        raise RecoveryFrontierError("durable_frontier_identity_or_method_invalid")
    base = f"host-recovery/synthetic-durable-frontiers/{frontier_id}"
    manifest_path, complete_path = value["manifest_path"], value["complete_path"]
    if manifest_path != f"{base}/MANIFEST.json" or complete_path != f"{base}/COMPLETE.json":
        raise RecoveryFrontierError("durable_frontier_path_invalid")
    manifest_raw = writer.read_bytes(manifest_path, max_bytes=MAX_SPEC_BYTES)
    complete_raw = writer.read_bytes(complete_path, max_bytes=65536)
    manifest = _json(manifest_raw, "durable_frontier_manifest")
    complete = _json(complete_raw, "durable_frontier_complete")
    if (
        manifest.get("schema") != K_MANIFEST_SCHEMA
        or manifest.get("scenario") != "synthetic"
        or manifest.get("frontier_id") != frontier_id
        or manifest.get("volume_uuid") != expected_volume_uuid
        or manifest.get("root_id") != expected_root_id
        or sha256(manifest_raw) != value["manifest_sha256"]
        or sha256(complete_raw) != value["complete_sha256"]
        or complete
        != {
            "schema": K_COMPLETE_SCHEMA,
            "frontier_id": frontier_id,
            "manifest_sha256": sha256(manifest_raw),
        }
    ):
        raise RecoveryFrontierError("durable_frontier_hash_or_identity_mismatch")
    vector = manifest.get("writer_ends")
    if not isinstance(vector, list) or not vector:
        raise RecoveryFrontierError("durable_frontier_vector_missing")
    seen: set[str] = set()
    normalized = []
    expected_names = {"MANIFEST.json", "COMPLETE.json"}
    observed: dict[str, bytes] = {
        manifest_path: manifest_raw,
        complete_path: complete_raw,
    }
    for item in vector:
        if not isinstance(item, dict) or set(item) != {
            "stream_id",
            "durable_end",
            "marker_path",
            "marker_sha256",
        }:
            raise RecoveryFrontierError("durable_frontier_vector_invalid")
        stream_id, durable_end, marker_path = (
            item["stream_id"],
            item["durable_end"],
            item["marker_path"],
        )
        marker_sha = _hash(item["marker_sha256"], "durable_frontier_marker_hash")
        if (
            not isinstance(stream_id, str)
            or not stream_id
            or len(stream_id) > 128
            or type(durable_end) is not int
            or durable_end <= 0
            or marker_path != f"{base}/END-{sha256(stream_id.encode())}.json"
            or stream_id in seen
        ):
            raise RecoveryFrontierError("durable_frontier_vector_invalid")
        marker_raw = writer.read_bytes(marker_path, max_bytes=65536)
        marker = _json(marker_raw, "durable_frontier_end_marker")
        payload_path = f"{base}/OBJECT-{sha256(stream_id.encode())}.bin"
        stop_path = f"{base}/STOP-{sha256(stream_id.encode())}.json"
        try:
            payload_bytes = writer.read_bytes(payload_path, max_bytes=MAX_SPEC_BYTES)
            stop_raw = writer.read_bytes(stop_path, max_bytes=65536)
        except FileNotFoundError as exc:
            raise RecoveryFrontierError("durable_frontier_object_or_stop_missing") from exc
        stop = _json(stop_raw, "durable_frontier_stop")
        if (
            not isinstance(durable_end, int)
            or type(durable_end) is not int
            or durable_end <= 0
            or marker
            != {
                "schema": K_END_SCHEMA,
                "stream_id": stream_id,
                "durable_end": durable_end,
                "object_path": payload_path,
                "object_sha256": sha256(payload_bytes),
                "object_size": len(payload_bytes),
                "stop_path": stop_path,
                "stop_sha256": sha256(stop_raw),
                "state": "committed",
            }
            or sha256(marker_raw) != marker_sha
            or stop
            != {
                "schema": K_STOP_SCHEMA,
                "stream_id": stream_id,
                "state": "stopped",
                "resumable": False,
                "pending_count": 1,
            }
        ):
            raise RecoveryFrontierError("durable_frontier_end_marker_mismatch")
        expected_names.update(
            {
                marker_path.rsplit("/", 1)[1],
                payload_path.rsplit("/", 1)[1],
                stop_path.rsplit("/", 1)[1],
            }
        )
        observed.update(
            {
                marker_path: marker_raw,
                payload_path: payload_bytes,
                stop_path: stop_raw,
            }
        )
        seen.add(stream_id)
        normalized.append(
            {
                "stream_id": stream_id,
                "durable_end": durable_end,
                "marker_path": marker_path,
                "marker_sha256": marker_sha,
            }
        )
    if set(writer.list_names(base)) != expected_names:
        raise RecoveryFrontierError("durable_frontier_membership_invalid")
    if any(
        writer.read_bytes(path, max_bytes=max(len(raw), 1)) != raw for path, raw in observed.items()
    ):
        raise RecoveryFrontierError("durable_frontier_changed_during_verification")
    if normalized != value["vector"]:
        raise RecoveryFrontierError("durable_frontier_vector_mismatch")
    return {
        "frontier_id": frontier_id,
        "manifest_sha256": sha256(manifest_raw),
        "complete_sha256": sha256(complete_raw),
        "proof_sha256": sha256(canonical_json(value)),
        "verification_method": "fresh_descriptor_bound_readback",
        "vector": normalized,
    }


def _validate_source(
    value: dict[str, Any],
    members: list[dict[str, Any]],
    *,
    expected_source_volume_uuid: str,
    expected_source_root_id: str,
) -> dict[str, Any]:
    fields = {
        "schema",
        "scenario",
        "source_view_kind",
        "volume_uuid",
        "root_id",
        "synthetic_view_id",
        "fingerprint_recorded_ns",
        "verification_method",
        "assertion_sha256",
        "root_inventory",
        "root_inventory_sha256",
        "member_bindings",
        "selection_policy_sha256",
        "exclusion_policy_sha256",
        "unresolved_candidate_count",
        "wallets_disposition",
        "metadata_disposition",
    }
    if set(value) != fields or value.get("schema") != "brain-host-post-capture-fingerprint-v1":
        raise RecoveryFrontierError("source_frontier_contract_invalid")
    if (
        value.get("scenario") != "synthetic"
        or value.get("source_view_kind") != "synthetic_post_capture_fingerprint"
    ):
        raise RecoveryFrontierError("real_source_frontier_verifier_unavailable")
    view_id = value.get("synthetic_view_id")
    if (
        not isinstance(view_id, str)
        or not view_id
        or len(view_id) > 128
        or value.get("volume_uuid") != expected_source_volume_uuid
        or value.get("root_id") != expected_source_root_id
        or type(value.get("fingerprint_recorded_ns")) is not int
        or value["fingerprint_recorded_ns"] <= 0
        or value.get("verification_method") != "synthetic_post_capture_fingerprint_assertion"
    ):
        raise RecoveryFrontierError("source_frontier_identity_or_method_invalid")
    _hash(value.get("assertion_sha256"), "source_fingerprint_assertion_hash")
    _hash(value.get("selection_policy_sha256"), "selection_policy_hash")
    _hash(value.get("exclusion_policy_sha256"), "exclusion_policy_hash")
    if (
        type(value.get("unresolved_candidate_count")) is not int
        or value["unresolved_candidate_count"] != 0
    ):
        raise RecoveryFrontierError("source_frontier_unresolved_candidates")
    if value.get("wallets_disposition") != "synthetic_fixture_absent":
        raise RecoveryFrontierError("source_frontier_wallets_unresolved")
    metadata = value.get("metadata_disposition")
    if metadata != {
        "scope": "synthetic_bytes_and_topology_only",
        "xattrs": "not_preserved",
        "acls": "not_preserved",
        "flags": "not_preserved",
        "uid_gid": "restored_by_mapping",
    }:
        raise RecoveryFrontierError("source_frontier_metadata_claim_unsupported")
    inventory = value.get("root_inventory")
    if not isinstance(inventory, list) or not inventory:
        raise RecoveryFrontierError("source_root_inventory_missing")
    roles: set[str] = set()
    for row in inventory:
        if not isinstance(row, dict) or set(row) != {
            "role",
            "root_locator_sha256",
            "membership_sha256",
            "included_count",
            "excluded_count",
            "unknown_count",
        }:
            raise RecoveryFrontierError("source_root_inventory_row_invalid")
        role = row["role"]
        if role not in ROLES or role in roles:
            raise RecoveryFrontierError("source_root_inventory_role_invalid")
        _hash(row["root_locator_sha256"], "source_root_locator_hash")
        _hash(row["membership_sha256"], "source_root_membership_hash")
        if any(
            type(row[key]) is not int or row[key] < 0
            for key in ("included_count", "excluded_count", "unknown_count")
        ):
            raise RecoveryFrontierError("source_root_inventory_count_invalid")
        if row["unknown_count"] != 0:
            raise RecoveryFrontierError("source_root_inventory_unknown_member")
        roles.add(role)
    if roles != ROLES or inventory != sorted(inventory, key=lambda row: row["role"]):
        raise RecoveryFrontierError("source_root_inventory_incomplete")
    if sha256(canonical_json(inventory)) != value["root_inventory_sha256"]:
        raise RecoveryFrontierError("source_root_inventory_hash_mismatch")
    assertion = canonical_json(
        {
            "schema": "brain-host-synthetic-post-capture-fingerprint-v1",
            "synthetic_view_id": view_id,
            "fingerprint_recorded_ns": value["fingerprint_recorded_ns"],
            "root_inventory_sha256": value["root_inventory_sha256"],
        }
    )
    if sha256(assertion) != value["assertion_sha256"]:
        raise RecoveryFrontierError("source_fingerprint_assertion_mismatch")
    bindings = value.get("member_bindings")
    if not isinstance(bindings, list) or len(bindings) != len(members):
        raise RecoveryFrontierError("source_member_bindings_invalid")
    normalized_bindings = []
    by_role = {item["role"]: item for item in members}
    seen: set[str] = set()
    for row in bindings:
        if not isinstance(row, dict) or set(row) != {
            "role",
            "format",
            "artifact_id",
            "manifest_sha256",
            "complete_sha256",
            "scope",
            "source_view_id",
        }:
            raise RecoveryFrontierError("source_member_binding_invalid")
        role = row["role"]
        if role not in by_role or role in seen or row["source_view_id"] != view_id:
            raise RecoveryFrontierError("source_member_binding_role_or_view_mismatch")
        expected = by_role[role]
        normalized = {
            **{
                key: row[key]
                for key in (
                    "role",
                    "format",
                    "artifact_id",
                    "manifest_sha256",
                    "complete_sha256",
                    "scope",
                )
            },
            "source_view_id": view_id,
        }
        if any(normalized[key] != expected[key] for key in expected):
            raise RecoveryFrontierError("source_member_binding_artifact_mismatch")
        normalized_bindings.append(normalized)
        seen.add(role)
    if seen != ROLES or normalized_bindings != sorted(
        normalized_bindings, key=lambda row: row["role"]
    ):
        raise RecoveryFrontierError("source_member_bindings_incomplete")
    return {
        "synthetic_view_id": view_id,
        "fingerprint_recorded_ns": value["fingerprint_recorded_ns"],
        "fingerprint_spec_sha256": sha256(canonical_json(value)),
        "root_inventory_sha256": value["root_inventory_sha256"],
        "assertion_sha256": value["assertion_sha256"],
        "member_bindings": normalized_bindings,
        "selection_policy_sha256": value["selection_policy_sha256"],
        "exclusion_policy_sha256": value["exclusion_policy_sha256"],
        "metadata_disposition": metadata,
        "wallets_disposition": value["wallets_disposition"],
    }


def _set_pins(writer: DescriptorWriter, set_id: str) -> tuple[dict[str, Any], bytes, bytes]:
    _id(set_id, "set_id")
    base = f"{recovery_set.BASE}/{set_id}"
    index_raw = writer.read_bytes(f"{base}/INDEX.json", max_bytes=MAX_SPEC_BYTES)
    complete_raw = writer.read_bytes(f"{base}/COMPLETE.json", max_bytes=65536)
    index = _json(index_raw, "recovery_set_index")
    if (
        index.get("application_recovery_qualified") is not False
        or index.get("factory_reset_ready") is not False
    ):
        raise RecoveryFrontierError("recovery_set_qualification_invalid")
    return index, index_raw, complete_raw


def _envelope_payload(
    writer: DescriptorWriter,
    spec: dict[str, Any],
    *,
    expected_volume_uuid: str,
    expected_root_id: str,
    expected_source_volume_uuid: str,
    expected_source_root_id: str,
    witness_targets: dict[str, Any],
    revalidate_identity: Callable[[], tuple[str, str]],
    now_ns: int,
) -> dict[str, Any]:
    set_id = _id(spec["set_id"], "set_id")
    witness_id = _id(spec["witness_id"], "witness_id")
    source = spec["source_frontier"]
    durable = spec["durable_frontier"]
    index, index_raw, set_complete_raw = _set_pins(writer, set_id)
    witness_base = f"{recovery_set.WITNESS_BASE}/{set_id}/{witness_id}"
    witness_raw_before = writer.read_bytes(f"{witness_base}/WITNESS.json", max_bytes=MAX_SPEC_BYTES)
    witness_complete_before = writer.read_bytes(f"{witness_base}/COMPLETE.json", max_bytes=65536)
    verified = recovery_set.verify_set(
        writer,
        set_id,
        expected_volume_uuid=expected_volume_uuid,
        expected_root_id=expected_root_id,
        revalidate_identity=revalidate_identity,
        witness_id=witness_id,
        witness_targets=witness_targets,
    )
    if not verified["restore_witness_bound"] or verified["application_recovery_qualified"]:
        raise RecoveryFrontierError("recovery_set_witness_not_inert")
    _same_index, index_after, complete_after = _set_pins(writer, set_id)
    witness_after = writer.read_bytes(f"{witness_base}/WITNESS.json", max_bytes=MAX_SPEC_BYTES)
    witness_complete_after = writer.read_bytes(f"{witness_base}/COMPLETE.json", max_bytes=65536)
    if (index_raw, set_complete_raw) != (index_after, complete_after) or (
        witness_raw_before,
        witness_complete_before,
    ) != (witness_after, witness_complete_after):
        raise RecoveryFrontierError("recovery_set_or_witness_generation_changed")
    set_members = index["spec"]["members"]
    source_summary = _validate_source(
        source,
        set_members,
        expected_source_volume_uuid=expected_source_volume_uuid,
        expected_source_root_id=expected_source_root_id,
    )
    durable_summary = _verify_k_frontier(
        writer,
        durable,
        expected_volume_uuid=expected_volume_uuid,
        expected_root_id=expected_root_id,
    )
    return {
        "schema": SCHEMA,
        "envelope_id": spec["envelope_id"],
        "scenario": "synthetic",
        "application_recovery_qualified": False,
        "factory_reset_ready": False,
        "source_fingerprint_after_capture": {
            "kind": "synthetic_post_capture_fingerprint",
            "volume_uuid": expected_source_volume_uuid,
            "root_id": expected_source_root_id,
            **source_summary,
        },
        "durable_frontier_K": {
            "kind": "synthetic_storage_corpus_vector",
            "volume_uuid": expected_volume_uuid,
            "root_id": expected_root_id,
            **durable_summary,
        },
        "archive_destination": {
            "volume_uuid": expected_volume_uuid,
            "root_id": expected_root_id,
        },
        "recovery_set": {
            "set_id": set_id,
            "index_sha256": sha256(index_raw),
            "complete_sha256": sha256(set_complete_raw),
            "member_count": len(set_members),
            "coverage_sha256": sha256(canonical_json(index["coverage"])),
        },
        "restore_witness": {
            "witness_id": witness_id,
            "witness_sha256": sha256(witness_after),
            "complete_sha256": sha256(witness_complete_after),
        },
        "cross_volume_atomic": False,
        "post_fingerprint_state": "unknown",
        "post_K_state": "unknown",
        "in_flight_state": "unknown",
        "metadata_scope": "synthetic_bytes_and_topology_only",
        "created_ns": now_ns,
    }


def _validate_spec(value: dict[str, Any]) -> dict[str, Any]:
    if (
        set(value)
        != {
            "schema",
            "envelope_id",
            "scenario",
            "set_id",
            "witness_id",
            "expected_source_volume_uuid",
            "expected_source_root_id",
            "expected_volume_uuid",
            "expected_root_id",
            "source_frontier",
            "durable_frontier",
        }
        or value.get("schema") != SPEC_SCHEMA
    ):
        raise RecoveryFrontierError("spec_contract_invalid")
    if value.get("scenario") != "synthetic":
        raise RecoveryFrontierError("real_frontier_composer_unavailable")
    _id(value["envelope_id"], "envelope_id")
    _id(value["set_id"], "set_id")
    _id(value["witness_id"], "witness_id")
    _id(value["expected_source_root_id"], "source_root_id")
    _id(value["expected_root_id"], "root_id")
    for key in ("expected_source_volume_uuid", "expected_volume_uuid"):
        if not isinstance(value[key], str) or not value[key] or len(value[key]) > 64:
            raise RecoveryFrontierError("spec_volume_identity_invalid")
    if not isinstance(value["source_frontier"], dict) or not isinstance(
        value["durable_frontier"], dict
    ):
        raise RecoveryFrontierError("spec_frontier_proof_invalid")
    return value


def _read_envelope(
    writer: DescriptorWriter, envelope_id: str
) -> tuple[dict[str, Any], bytes, bytes]:
    envelope_id = _id(envelope_id, "envelope_id")
    base = f"{BASE}/{envelope_id}"
    if set(writer.list_names(base)) != {"RESERVATION.json", "INDEX.json", "COMPLETE.json"}:
        raise RecoveryFrontierError("envelope_membership_invalid")
    reservation_raw = writer.read_bytes(f"{base}/RESERVATION.json", max_bytes=65536)
    reservation = _json(reservation_raw, "reservation")
    if reservation != {
        "schema": RESERVATION_SCHEMA,
        "envelope_id": envelope_id,
        "state": "incomplete_until_complete_marker",
    }:
        raise RecoveryFrontierError("envelope_reservation_invalid")
    index_raw = writer.read_bytes(f"{base}/INDEX.json", max_bytes=MAX_SPEC_BYTES)
    complete_raw = writer.read_bytes(f"{base}/COMPLETE.json", max_bytes=65536)
    index = _json(index_raw, "index")
    complete = _json(complete_raw, "complete")
    if (
        complete
        != {
            "schema": COMPLETE_SCHEMA,
            "envelope_id": envelope_id,
            "index_sha256": sha256(index_raw),
            "reservation_sha256": sha256(reservation_raw),
        }
        or index.get("schema") != SCHEMA
        or index.get("envelope_id") != envelope_id
        or index.get("application_recovery_qualified") is not False
        or index.get("factory_reset_ready") is not False
        or index.get("cross_volume_atomic") is not False
    ):
        raise RecoveryFrontierError("envelope_complete_or_identity_mismatch")
    return index, index_raw, complete_raw


def create_frontier_envelope(
    writer: DescriptorWriter,
    fresh_reader: Callable[[], DescriptorWriter],
    spec_value: dict[str, Any],
    *,
    witness_targets: dict[str, Any],
    expected_storage_identity: tuple[str, str],
    expected_source_volume_uuid: str,
    expected_source_root_id: str,
    revalidate_identity: Callable[[], tuple[str, str]],
    now_ns: int | None = None,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Publish an inert synthetic T/K envelope around a verified set and witness."""
    spec = _validate_spec(spec_value)
    expected_volume_uuid, expected_root_id = expected_storage_identity
    if (spec["expected_volume_uuid"], spec["expected_root_id"]) != expected_storage_identity:
        raise RecoveryFrontierError("storage_identity_pin_mismatch")
    if (spec["expected_source_volume_uuid"], spec["expected_source_root_id"]) != (
        expected_source_volume_uuid,
        expected_source_root_id,
    ):
        raise RecoveryFrontierError("source_identity_pin_mismatch")
    if revalidate_identity() != expected_storage_identity:
        raise RecoveryFrontierError("storage_identity_changed")
    created_ns = now_ns if now_ns is not None else time.time_ns()
    with fresh_reader() as reader:
        payload = _envelope_payload(
            reader,
            spec,
            expected_volume_uuid=expected_volume_uuid,
            expected_root_id=expected_root_id,
            expected_source_volume_uuid=expected_source_volume_uuid,
            expected_source_root_id=expected_source_root_id,
            witness_targets=witness_targets,
            revalidate_identity=revalidate_identity,
            now_ns=created_ns,
        )
    raw = canonical_json(payload)
    base = f"{BASE}/{spec['envelope_id']}"
    writer.mkdir(BASE)
    with writer.lock(f"{BASE}/.publish.lock"):
        try:
            writer.list_names(base)
        except FileNotFoundError:
            pass
        else:
            raise RecoveryFrontierError("envelope_collision")
        writer.mkdir(base)
        reservation = canonical_json(
            {
                "schema": RESERVATION_SCHEMA,
                "envelope_id": spec["envelope_id"],
                "state": "incomplete_until_complete_marker",
            }
        )
        writer.create(f"{base}/RESERVATION.json", reservation)
        if fault_hook:
            fault_hook("after_reservation")
        if revalidate_identity() != expected_storage_identity:
            raise RecoveryFrontierError("storage_identity_changed")
        writer.create(f"{base}/INDEX.json", raw)
        if writer.read_bytes(f"{base}/INDEX.json", max_bytes=MAX_SPEC_BYTES) != raw:
            raise RecoveryFrontierError("envelope_index_readback_mismatch")
        if fault_hook:
            fault_hook("after_index")
        # Freshly reopen the bound storage and re-verify T, K, set, witness, and policy inputs.
        with fresh_reader() as reader:
            current = _envelope_payload(
                reader,
                spec,
                expected_volume_uuid=expected_volume_uuid,
                expected_root_id=expected_root_id,
                expected_source_volume_uuid=expected_source_volume_uuid,
                expected_source_root_id=expected_source_root_id,
                witness_targets=witness_targets,
                revalidate_identity=revalidate_identity,
                now_ns=created_ns,
            )
            if canonical_json(current) != raw:
                raise RecoveryFrontierError("frontier_inputs_changed_before_complete")
        if revalidate_identity() != expected_storage_identity:
            raise RecoveryFrontierError("storage_identity_changed")
        complete = canonical_json(
            {
                "schema": COMPLETE_SCHEMA,
                "envelope_id": spec["envelope_id"],
                "index_sha256": sha256(raw),
                "reservation_sha256": sha256(reservation),
            }
        )
        writer.create(f"{base}/COMPLETE.json", complete)
        if writer.read_bytes(f"{base}/COMPLETE.json", max_bytes=65536) != complete:
            raise RecoveryFrontierError("envelope_complete_readback_mismatch")
    with fresh_reader() as reader:
        verified_index, verified_raw, _ = _read_envelope(reader, spec["envelope_id"])
        if verified_raw != raw or verified_index != payload:
            raise RecoveryFrontierError("envelope_fresh_readback_mismatch")
        current = _envelope_payload(
            reader,
            spec,
            expected_volume_uuid=expected_volume_uuid,
            expected_root_id=expected_root_id,
            expected_source_volume_uuid=expected_source_volume_uuid,
            expected_source_root_id=expected_source_root_id,
            witness_targets=witness_targets,
            revalidate_identity=revalidate_identity,
            now_ns=created_ns,
        )
        if canonical_json(current) != raw:
            raise RecoveryFrontierError("frontier_postpublication_verification_mismatch")
    return {
        "schema": SCHEMA,
        "envelope_id": spec["envelope_id"],
        "index_sha256": sha256(raw),
        "complete": True,
        "application_recovery_qualified": False,
        "factory_reset_ready": False,
        "cross_volume_atomic": False,
    }


def verify_frontier_envelope(
    writer: DescriptorWriter,
    envelope_id: str,
    spec_value: dict[str, Any],
    *,
    witness_targets: dict[str, Any],
    expected_storage_identity: tuple[str, str],
    expected_source_volume_uuid: str,
    expected_source_root_id: str,
    revalidate_identity: Callable[[], tuple[str, str]],
) -> dict[str, Any]:
    spec = _validate_spec(spec_value)
    if spec["envelope_id"] != envelope_id:
        raise RecoveryFrontierError("envelope_spec_identity_mismatch")
    volume_uuid, root_id = expected_storage_identity
    if (spec["expected_volume_uuid"], spec["expected_root_id"]) != expected_storage_identity:
        raise RecoveryFrontierError("storage_identity_pin_mismatch")
    if (spec["expected_source_volume_uuid"], spec["expected_source_root_id"]) != (
        expected_source_volume_uuid,
        expected_source_root_id,
    ):
        raise RecoveryFrontierError("source_identity_pin_mismatch")
    if revalidate_identity() != expected_storage_identity:
        raise RecoveryFrontierError("storage_identity_changed")
    index, raw, complete = _read_envelope(writer, envelope_id)
    expected = _envelope_payload(
        writer,
        spec,
        expected_volume_uuid=volume_uuid,
        expected_root_id=root_id,
        expected_source_volume_uuid=expected_source_volume_uuid,
        expected_source_root_id=expected_source_root_id,
        witness_targets=witness_targets,
        revalidate_identity=revalidate_identity,
        now_ns=index.get("created_ns"),
    )
    if canonical_json(expected) != raw:
        raise RecoveryFrontierError("envelope_reverification_mismatch")
    if revalidate_identity() != expected_storage_identity:
        raise RecoveryFrontierError("storage_identity_changed")
    return {
        "schema": SCHEMA,
        "envelope_id": envelope_id,
        "index_sha256": sha256(raw),
        "complete_sha256": sha256(complete),
        "post_capture_fingerprint_recorded_ns": index["source_fingerprint_after_capture"][
            "fingerprint_recorded_ns"
        ],
        "durable_frontier_K_vector_count": len(index["durable_frontier_K"]["vector"]),
        "member_count": index["recovery_set"]["member_count"],
        "restore_witness_bound": True,
        "application_recovery_qualified": False,
        "factory_reset_ready": False,
        "cross_volume_atomic": False,
        "metadata_scope": index["metadata_scope"],
    }
