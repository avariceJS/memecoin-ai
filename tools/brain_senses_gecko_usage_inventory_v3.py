"""Read-only usage inventory v3 with the explicit successor handoff root shape."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from typing import Any

from tools import brain_senses_gecko_capture_only_v2_handoff_v1 as handoff_v1
from tools import brain_senses_gecko_scout_chain_v2 as chain_v2
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage
from tools import brain_senses_gecko_usage_inventory_v2 as inventory_v2
from tools.brain_senses_external_writer_v1 import DescriptorWriter

SCHEMA = "brain-senses-gecko-shared-usage-inventory-v3"
HANDOFF_ROOT_PREFIX = "services/gecko-market-v1/capture-only-v2/"
SERVICE_ROOT = "services/gecko-market-v1"
CAPTURE_V2_PARENT = f"{SERVICE_ROOT}/capture-only-v2"


class InventoryV3Error(ValueError):
    """The new handoff root or the full v2 inventory could not be verified."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class _HandoffHiddenWriter:
    """Hide independently-versioned roots from the frozen v2 parser."""

    def __init__(self, writer: DescriptorWriter, handoff_roots: set[str]):
        self._writer = writer
        self._handoff_roots = handoff_roots

    def __getattr__(self, name: str) -> Any:
        return getattr(self._writer, name)

    def list_names(self, relative: str) -> tuple[str, ...]:
        if relative == CAPTURE_V2_PARENT:
            return tuple(
                name
                for name in self._writer.list_names(relative)
                if f"{relative}/{name}" not in self._handoff_roots
            )
        names = self._writer.list_names(relative)
        if relative in self._handoff_roots:
            return tuple(name for name in names if name != handoff_v1.JOURNAL_NAME)
        return names


def _read_json(
    writer: DescriptorWriter, path: str, *, maximum: int = 2 * 1024 * 1024
) -> dict[str, Any]:
    raw = writer.read_bytes(path, max_bytes=maximum)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise InventoryV3Error("successor_v2_json_invalid") from exc
    if type(value) is not dict or handoff_v1.canonical_json(value) != raw:
        raise InventoryV3Error("successor_v2_json_not_canonical")
    return value


def _verify_chain_v2_root(
    writer: DescriptorWriter,
    *,
    root: str,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    if set(context) != {"certificate_inputs"}:
        raise InventoryV3Error("successor_v2_chain_context_shape")
    names = set(writer.list_names(root))
    allowed = {
        ".runner.lock",
        "binding.json",
        "policy.json",
        "predecessor-cutover.json",
        "handoff-v1.jsonl",
        "chain",
    }
    if (
        not {"binding.json", "policy.json", "predecessor-cutover.json", "chain"} <= names
        or not names <= allowed
    ):
        raise InventoryV3Error("successor_v2_chain_root_shape")
    binding = _read_json(writer, f"{root}/binding.json", maximum=16 * 1024)
    policy = _read_json(writer, f"{root}/policy.json")
    certificate = _read_json(writer, f"{root}/predecessor-cutover.json")
    if binding.get("schema") != f"{chain_v2.SCHEMA}-binding" or binding.get("schema_version") != 2:
        raise InventoryV3Error("successor_v2_binding_schema")
    if binding.get("root") != root or binding.get("storage") != certificate.get("storage"):
        raise InventoryV3Error("successor_v2_binding_root_storage")
    if binding.get("policy_sha256") != policy.get("policy_sha256"):
        raise InventoryV3Error("successor_v2_binding_policy")
    if binding.get("cutover_certificate_sha256") != certificate.get("certificate_sha256"):
        raise InventoryV3Error("successor_v2_binding_certificate")
    chain_names = tuple(writer.list_names(f"{root}/chain"))
    if chain_names != tuple(f"{index:08d}.json" for index in range(len(chain_names))):
        raise InventoryV3Error("successor_v2_chain_file_closure")
    records = [_read_json(writer, f"{root}/chain/{name}") for name in chain_names]
    try:
        journal = chain_v2.DescriptorScoutJournalV2(
            writer,
            root=root,
            policy=policy,
            certificate=certificate,
            certificate_inputs=context["certificate_inputs"],
        )
        if journal.binding != binding or journal.records() != records:
            raise InventoryV3Error("successor_v2_chain_binding_or_replay")
        replay = chain_v2.verify_chain(
            policy,
            certificate,
            records,
            certificate_inputs=context["certificate_inputs"],
        )
    except (chain_v2.ChainV2Error, KeyError, TypeError, ValueError) as exc:
        raise InventoryV3Error("successor_v2_chain_replay_failed") from exc
    if replay.get("cursor") != 12:
        raise InventoryV3Error("successor_v2_capture_adapter_required")
    if any(
        name in names for name in ("intents", "results", "confirmations", "skips", "dispatch.jsonl")
    ):
        raise InventoryV3Error("successor_v2_capture_adapter_required")
    return {
        "status": "verified_none",
        "chain_status": replay["status"],
        "record_count": replay["record_count"],
        "cursor": replay["cursor"],
        "certificate_sha256": certificate["certificate_sha256"],
        "binding_sha256": binding["binding_sha256"],
        "provider_io": False,
        "dispatch_authorized": False,
    }


def verify_successor_root_shape(
    writer: DescriptorWriter,
    *,
    root: str,
    expected_genesis: Mapping[str, Any],
    expected_storage: Mapping[str, Any],
    predecessor_attempts: list[Mapping[str, Any]],
    predecessor_slots: list[Mapping[str, Any]],
    observed_now_ns: int,
) -> dict[str, Any]:
    """Verify one root’s exact journal placement and source-bound handoff semantics."""
    if not root.startswith(HANDOFF_ROOT_PREFIX) or root.count("/") != 3:
        raise InventoryV3Error("successor_root_path_invalid")
    entries = list(writer.list_names(root))
    if entries.count(handoff_v1.JOURNAL_NAME) != 1:
        raise InventoryV3Error("successor_handoff_file_missing_or_duplicate")
    base_entries = set(entries) - {handoff_v1.JOURNAL_NAME}
    allowed = {
        ".runner.lock",
        "binding.json",
        "policy.json",
        "chain",
        "intents",
        "results",
        "confirmations",
        "skips",
        "dispatch.jsonl",
    }
    if not {"binding.json", "policy.json", "chain"} <= base_entries or not base_entries <= allowed:
        raise InventoryV3Error("successor_v2_root_shape_incomplete")
    info = writer.stat(f"{root}/{handoff_v1.JOURNAL_NAME}")
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_dev != writer.device
    ):
        raise InventoryV3Error("successor_handoff_file_unsafe")
    raw = writer.read_bytes(f"{root}/{handoff_v1.JOURNAL_NAME}", max_bytes=256 * 1024)
    if dict(expected_genesis.get("storage", {})) != dict(expected_storage):
        raise InventoryV3Error("successor_storage_context_mismatch")
    root_stat = os.fstat(writer.root_fd)
    if (
        expected_storage.get("device") != writer.device
        or expected_storage.get("inode") != root_stat.st_ino
    ):
        raise InventoryV3Error("successor_storage_descriptor_mismatch")
    replay = handoff_v1.verify_journal(
        raw,
        successor_root=root,
        expected_genesis=expected_genesis,
        predecessor_attempts=predecessor_attempts,
        predecessor_slots=predecessor_slots,
        observed_now_ns=observed_now_ns,
    )
    if writer.read_bytes(f"{root}/{handoff_v1.JOURNAL_NAME}", max_bytes=256 * 1024) != raw:
        raise InventoryV3Error("successor_handoff_changed_during_read")
    return {
        **replay,
        "journal_sha256": _sha(raw),
        "journal_path": f"{root}/{handoff_v1.JOURNAL_NAME}",
    }


def build_usage_inventory(
    writer: DescriptorWriter,
    *,
    required_successor_root: str,
    expected_handoffs: Mapping[str, Mapping[str, Any]],
    expected_v2_chains: Mapping[str, Mapping[str, Any]] | None = None,
    observed_now_ns: int,
) -> dict[str, Any]:
    """Replay the full established inventory and validate each named v3 handoff root.

    `expected_handoffs` must be compiled from the independently replayed predecessor root,
    coordinator prefix, pre-init census and quiescence evidence. This function compares those
    pinned values to the replayed inventory and never grants provider or dispatch authority.
    """
    try:
        roots, manifest, inventory_hash = inventory_v2._discover_roots(writer)
        if required_successor_root not in roots:
            raise InventoryV3Error("required_successor_root_missing")
        hidden_roots: set[str] = set()
        v2_roots: set[str] = set()
        for root in roots:
            if not root.startswith(HANDOFF_ROOT_PREFIX):
                continue
            names = set(writer.list_names(root))
            has_handoff = handoff_v1.JOURNAL_NAME in names
            is_chain_v2 = False
            if "binding.json" in names:
                binding = _read_json(writer, f"{root}/binding.json", maximum=16 * 1024)
                is_chain_v2 = binding.get("schema") == f"{chain_v2.SCHEMA}-binding"
            if has_handoff or is_chain_v2:
                hidden_roots.add(root)
            if is_chain_v2:
                v2_roots.add(root)
        if required_successor_root not in hidden_roots:
            raise InventoryV3Error("required_successor_root_unrecognized")
        expected_v2_chains = dict(expected_v2_chains or {})
        if expected_v2_chains.keys() != v2_roots:
            raise InventoryV3Error("successor_v2_chain_context_set_mismatch")
        legacy_handoff_roots = {
            root
            for root in hidden_roots
            if handoff_v1.JOURNAL_NAME in set(writer.list_names(root)) and root not in v2_roots
        }
        if set(expected_handoffs) != legacy_handoff_roots:
            raise InventoryV3Error("successor_handoff_context_set_mismatch")
        proxy = _HandoffHiddenWriter(writer, hidden_roots)
        base = inventory_v2.build_usage_inventory(proxy)  # type: ignore[arg-type]
        snapshots = {row["root"]: row for row in base["snapshots"]}
        handoffs: dict[str, Any] = {}
        for root in sorted(hidden_roots):
            if root in legacy_handoff_roots:
                context = expected_handoffs[root]
                if set(context) != {
                    "expected_genesis",
                    "predecessor_attempts",
                    "predecessor_slots",
                    "expected_storage",
                }:
                    raise InventoryV3Error("successor_handoff_context_shape")
            else:
                context = None
            lock_fd = inventory_v2._shared_root_lock(writer, root)
            try:
                prefix_before = inventory_v2._root_files_sha256(writer, root)
                if root in v2_roots:
                    verified = _verify_chain_v2_root(
                        writer, root=root, context=expected_v2_chains[root]
                    )
                else:
                    verified = {"status": "verified_none", "provider_io": False}
                if root in legacy_handoff_roots:
                    assert context is not None
                    predecessor = context["expected_genesis"].get("predecessor_root")
                    predecessor_snapshot = snapshots.get(predecessor)
                    if predecessor_snapshot is None:
                        raise InventoryV3Error("successor_predecessor_snapshot_missing")
                    if predecessor_snapshot.get("source_prefix_sha256") != context[
                        "expected_genesis"
                    ].get("predecessor_file_prefix_sha256"):
                        raise InventoryV3Error("successor_predecessor_snapshot_mismatch")
                    predecessor_attempts = list(context["predecessor_attempts"])
                    snapshot_attempts = {
                        row["request_id"]: row for row in predecessor_snapshot.get("attempts", [])
                    }
                    for row in predecessor_attempts:
                        existing = snapshot_attempts.get(row.get("request_id"))
                        if (
                            type(existing) is not dict
                            or existing.get("response_sha256") != row.get("response_sha256")
                            or existing.get("response_bytes") != row.get("response_bytes")
                        ):
                            raise InventoryV3Error("successor_predecessor_attempt_not_in_inventory")
                    handoff_verified = verify_successor_root_shape(
                        writer,
                        root=root,
                        expected_genesis=context["expected_genesis"],
                        expected_storage=context["expected_storage"],
                        predecessor_attempts=predecessor_attempts,
                        predecessor_slots=list(context["predecessor_slots"]),
                        observed_now_ns=observed_now_ns,
                    )
                    verified["handoff"] = handoff_verified
                prefix_after = inventory_v2._root_files_sha256(writer, root)
                if prefix_before != prefix_after:
                    raise InventoryV3Error("successor_root_changed_during_handoff_replay")
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            if root in snapshots and snapshots[root].get("attempts"):
                raise InventoryV3Error("successor_root_not_zero_attempt_prefix")
            handoffs[root] = verified

        roots_after, manifest_after, hash_after = inventory_v2._discover_roots(writer)
        if roots_after != roots or hash_after != inventory_hash or manifest_after != manifest:
            raise InventoryV3Error("successor_inventory_changed_during_handoff_replay")
        snapshots_by_root: dict[str, dict[str, Any]] = {}
        for root in roots:
            if root in hidden_roots:
                snapshots_by_root[root] = shared_usage.make_verified_root_snapshot(
                    root=root,
                    status="verified_none",
                    root_inventory_sha256=inventory_hash,
                    source_prefix_sha256=inventory_v2._root_files_sha256(writer, root),
                    attempts=[],
                )
                continue
            original = snapshots.get(root)
            if original is None:
                raise InventoryV3Error("successor_snapshot_missing")
            snapshots_by_root[root] = shared_usage.make_verified_root_snapshot(
                root=root,
                status=original["status"],
                root_inventory_sha256=inventory_hash,
                source_prefix_sha256=original["source_prefix_sha256"],
                attempts=original["attempts"],
            )
        return {
            **base,
            "schema": SCHEMA,
            "root_inventory": manifest,
            "root_inventory_sha256": inventory_hash,
            "snapshots": [snapshots_by_root[root] for root in roots],
            "handoffs": handoffs,
            "provider_io": False,
            "dispatch_authorized": False,
        }
    except (handoff_v1.HandoffError, inventory_v2.InventoryError) as exc:
        raise InventoryV3Error(str(exc)) from exc


__all__ = ["InventoryV3Error", "SCHEMA", "build_usage_inventory", "verify_successor_root_shape"]
