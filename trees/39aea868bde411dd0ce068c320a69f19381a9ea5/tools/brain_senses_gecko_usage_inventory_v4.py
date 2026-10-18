"""Read-only usage inventory v4 with verified chain-v2 capture attempts."""

from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from typing import Any

from tools import brain_senses_capacity_coordinator_v2 as capacity
from tools import brain_senses_gecko_capture_only_v2_handoff_v1 as handoff_v1
from tools import brain_senses_gecko_capture_only_v3_verify as capture_verify_v3
from tools import brain_senses_gecko_scout_chain_v2 as chain_v2
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage
from tools import brain_senses_gecko_usage_inventory_v2 as inventory_v2
from tools.brain_senses_external_writer_v1 import DescriptorWriter

SCHEMA = "brain-senses-gecko-shared-usage-inventory-v4"
HANDOFF_ROOT_PREFIX = "services/gecko-market-v1/capture-only-v2/"
SERVICE_ROOT = "services/gecko-market-v1"
CAPTURE_V2_PARENT = f"{SERVICE_ROOT}/capture-only-v2"


class InventoryV4Error(ValueError):
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
        raise InventoryV4Error("successor_v2_json_invalid") from exc
    if type(value) is not dict or handoff_v1.canonical_json(value) != raw:
        raise InventoryV4Error("successor_v2_json_not_canonical")
    return value


def _read_optional_rows(
    writer: DescriptorWriter, *, root: str, directory: str, maximum: int
) -> list[dict[str, Any]]:
    path = f"{root}/{directory}"
    try:
        names = tuple(writer.list_names(path))
    except FileNotFoundError:
        return []
    if not all(name.endswith(".json") for name in names):
        raise InventoryV4Error("capture_v2_unexpected_entry")
    rows: list[dict[str, Any]] = []
    for name in names:
        rows.append(_read_json(writer, f"{path}/{name}", maximum=maximum))
    return rows


def _read_dispatch_rows(writer: DescriptorWriter, root: str) -> list[dict[str, Any]]:
    path = f"{root}/dispatch.jsonl"
    try:
        raw = writer.read_bytes(path, max_bytes=16 * 1024 * 1024)
    except FileNotFoundError:
        return []
    if raw and not raw.endswith(b"\n"):
        raise InventoryV4Error("capture_v2_dispatch_torn")
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InventoryV4Error("capture_v2_dispatch_invalid") from exc
        if type(value) is not dict or handoff_v1.canonical_json(value) != line + b"\n":
            raise InventoryV4Error("capture_v2_dispatch_noncanonical")
        rows.append(value)
    return rows


def _read_coordinator(
    writer: DescriptorWriter, storage: Mapping[str, Any]
) -> tuple[bytes, list[dict[str, Any]]]:
    try:
        with writer.lock(capacity.LOCK_PATH, blocking=False):
            policy_raw = writer.read_bytes(capacity.POLICY_PATH, max_bytes=1_000_000)
            policy = json.loads(policy_raw)
            if (
                type(policy) is not dict
                or capacity.canonical(policy) != policy_raw
                or policy.get("root_binding") != dict(storage)
                or storage.get("device") != writer.device
                or storage.get("inode") != os.fstat(writer.root_fd).st_ino
            ):
                raise InventoryV4Error("capture_v2_coordinator_policy_binding")
            raw = writer.read_bytes(capacity.LEDGER_PATH, max_bytes=64_000_000)
            events = capacity._verify_ledger(raw)
            if events[0]["fields"].get("policy_sha256") != capacity.sha256(policy_raw):
                raise InventoryV4Error("capture_v2_coordinator_policy_hash")
            return raw, events
    except (capacity.CoordinatorError, OSError, TypeError, ValueError) as exc:
        if isinstance(exc, InventoryV4Error):
            raise
        raise InventoryV4Error("capture_v2_coordinator_replay_failed") from exc


def _verify_chain_v2_root(
    writer: DescriptorWriter,
    *,
    root: str,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    if set(context) != {"certificate_inputs", "observed_now_ns"}:
        raise InventoryV4Error("successor_v2_chain_context_shape")
    if type(context["observed_now_ns"]) is not int or context["observed_now_ns"] < 0:
        raise InventoryV4Error("successor_v2_observed_time_invalid")
    names = set(writer.list_names(root))
    allowed = {
        ".runner.lock",
        "binding.json",
        "policy.json",
        "predecessor-cutover.json",
        "chain",
        "intents",
        "results",
        "confirmations",
        "skips",
        "dispatch.jsonl",
    }
    if (
        not {"binding.json", "policy.json", "predecessor-cutover.json", "chain"} <= names
        or not names <= allowed
    ):
        raise InventoryV4Error("successor_v2_chain_root_shape")
    binding = _read_json(writer, f"{root}/binding.json", maximum=16 * 1024)
    policy = _read_json(writer, f"{root}/policy.json")
    certificate = _read_json(writer, f"{root}/predecessor-cutover.json")
    if binding.get("schema") != f"{chain_v2.SCHEMA}-binding" or binding.get("schema_version") != 2:
        raise InventoryV4Error("successor_v2_binding_schema")
    if binding.get("root") != root or binding.get("storage") != certificate.get("storage"):
        raise InventoryV4Error("successor_v2_binding_root_storage")
    if binding.get("policy_sha256") != policy.get("policy_sha256"):
        raise InventoryV4Error("successor_v2_binding_policy")
    if binding.get("cutover_certificate_sha256") != certificate.get("certificate_sha256"):
        raise InventoryV4Error("successor_v2_binding_certificate")
    chain_names = tuple(writer.list_names(f"{root}/chain"))
    if chain_names != tuple(f"{index:08d}.json" for index in range(len(chain_names))):
        raise InventoryV4Error("successor_v2_chain_file_closure")
    records = [_read_json(writer, f"{root}/chain/{name}") for name in chain_names]
    intents = _read_optional_rows(writer, root=root, directory="intents", maximum=8 * 1024 * 1024)
    terminals = _read_optional_rows(writer, root=root, directory="results", maximum=64 * 1024)
    confirmations = _read_optional_rows(
        writer, root=root, directory="confirmations", maximum=64 * 1024
    )
    skips = _read_optional_rows(writer, root=root, directory="skips", maximum=64 * 1024)
    dispatch = _read_dispatch_rows(writer, root)
    expected_intent_names = tuple(sorted(f"{row.get('request_id')}.json" for row in intents))
    expected_result_names = tuple(sorted(f"{row.get('request_id')}.json" for row in terminals))
    expected_confirmation_names = tuple(
        sorted(f"{row.get('request_id')}.json" for row in confirmations)
    )
    expected_skip_names = tuple(
        sorted(
            f"{row.get('plan_sequence')}-{row.get('slot_index'):02d}.json"
            for row in skips
            if type(row.get("slot_index")) is int
        )
    )
    for directory, expected_names in (
        ("intents", expected_intent_names),
        ("results", expected_result_names),
        ("confirmations", expected_confirmation_names),
        ("skips", expected_skip_names),
    ):
        try:
            found_names = tuple(writer.list_names(f"{root}/{directory}"))
        except FileNotFoundError:
            found_names = ()
        if found_names != expected_names:
            raise InventoryV4Error("successor_v2_capture_file_closure")
    raw_responses: dict[str, bytes] = {}
    for terminal in terminals:
        encoded = terminal.get("raw_body_b64")
        if encoded is not None:
            if type(encoded) is not str:
                raise InventoryV4Error("successor_v2_raw_body_shape")
            try:
                body = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise InventoryV4Error("successor_v2_raw_body_encoding") from exc
            if base64.b64encode(body).decode("ascii") != encoded:
                raise InventoryV4Error("successor_v2_raw_body_encoding")
            raw_responses[str(terminal.get("request_id"))] = body
    coordinator_raw, coordinator_events = _read_coordinator(writer, binding["storage"])
    try:
        summary = capture_verify_v3.verify_capture_prefix_v3(
            root=root,
            binding=binding,
            policy=policy,
            chain_records=records,
            intents=intents,
            dispatch_records=dispatch,
            terminals=terminals,
            raw_responses=raw_responses,
            confirmations=confirmations,
            coordinator_events=coordinator_events,
            skips=skips,
            certificate=certificate,
            certificate_inputs=context["certificate_inputs"],
            observed_now_ns=context["observed_now_ns"],
        )
    except (capture_verify_v3.VerifyError, KeyError, TypeError, ValueError) as exc:
        raise InventoryV4Error("successor_v2_capture_replay_failed") from exc
    coordinator_after, _ = _read_coordinator(writer, binding["storage"])
    if coordinator_after != coordinator_raw:
        raise InventoryV4Error("successor_v2_coordinator_changed_during_replay")
    return {
        **summary,
        "binding_sha256": binding["binding_sha256"],
        "certificate_sha256": certificate["certificate_sha256"],
        "coordinator_sha256": _sha(coordinator_raw),
        "dispatch_blocked": summary["dispatch_blocked"],
        "dispatch_block_reason": summary["dispatch_block_reason"],
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
        raise InventoryV4Error("successor_root_path_invalid")
    entries = list(writer.list_names(root))
    if entries.count(handoff_v1.JOURNAL_NAME) != 1:
        raise InventoryV4Error("successor_handoff_file_missing_or_duplicate")
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
        raise InventoryV4Error("successor_v2_root_shape_incomplete")
    info = writer.stat(f"{root}/{handoff_v1.JOURNAL_NAME}")
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_dev != writer.device
    ):
        raise InventoryV4Error("successor_handoff_file_unsafe")
    raw = writer.read_bytes(f"{root}/{handoff_v1.JOURNAL_NAME}", max_bytes=256 * 1024)
    if dict(expected_genesis.get("storage", {})) != dict(expected_storage):
        raise InventoryV4Error("successor_storage_context_mismatch")
    root_stat = os.fstat(writer.root_fd)
    if (
        expected_storage.get("device") != writer.device
        or expected_storage.get("inode") != root_stat.st_ino
    ):
        raise InventoryV4Error("successor_storage_descriptor_mismatch")
    replay = handoff_v1.verify_journal(
        raw,
        successor_root=root,
        expected_genesis=expected_genesis,
        predecessor_attempts=predecessor_attempts,
        predecessor_slots=predecessor_slots,
        observed_now_ns=observed_now_ns,
    )
    if writer.read_bytes(f"{root}/{handoff_v1.JOURNAL_NAME}", max_bytes=256 * 1024) != raw:
        raise InventoryV4Error("successor_handoff_changed_during_read")
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
            raise InventoryV4Error("required_successor_root_missing")
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
            raise InventoryV4Error("required_successor_root_unrecognized")
        expected_v2_chains = dict(expected_v2_chains or {})
        if expected_v2_chains.keys() != v2_roots:
            raise InventoryV4Error("successor_v2_chain_context_set_mismatch")
        legacy_handoff_roots = {
            root
            for root in hidden_roots
            if handoff_v1.JOURNAL_NAME in set(writer.list_names(root)) and root not in v2_roots
        }
        if set(expected_handoffs) != legacy_handoff_roots:
            raise InventoryV4Error("successor_handoff_context_set_mismatch")
        proxy = _HandoffHiddenWriter(writer, hidden_roots)
        base = inventory_v2.build_usage_inventory(proxy)  # type: ignore[arg-type]
        snapshots = {row["root"]: row for row in base["snapshots"]}
        handoffs: dict[str, Any] = {}
        hidden_prefixes: dict[str, str] = {}
        for root in sorted(hidden_roots):
            if root in legacy_handoff_roots:
                context = expected_handoffs[root]
                if set(context) != {
                    "expected_genesis",
                    "predecessor_attempts",
                    "predecessor_slots",
                    "expected_storage",
                }:
                    raise InventoryV4Error("successor_handoff_context_shape")
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
                        raise InventoryV4Error("successor_predecessor_snapshot_missing")
                    if predecessor_snapshot.get("source_prefix_sha256") != context[
                        "expected_genesis"
                    ].get("predecessor_file_prefix_sha256"):
                        raise InventoryV4Error("successor_predecessor_snapshot_mismatch")
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
                            raise InventoryV4Error("successor_predecessor_attempt_not_in_inventory")
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
                    raise InventoryV4Error("successor_root_changed_during_handoff_replay")
                hidden_prefixes[root] = prefix_after
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            handoffs[root] = verified

        roots_after, manifest_after, hash_after = inventory_v2._discover_roots(writer)
        if roots_after != roots or hash_after != inventory_hash or manifest_after != manifest:
            raise InventoryV4Error("successor_inventory_changed_during_handoff_replay")
        snapshots_by_root: dict[str, dict[str, Any]] = {}
        for root in roots:
            if root in hidden_roots:
                verified = handoffs[root]
                attempts = list(verified.get("usage_attempts", []))
                snapshots_by_root[root] = shared_usage.make_verified_root_snapshot(
                    root=root,
                    status="verified_prefix" if attempts else "verified_none",
                    root_inventory_sha256=inventory_hash,
                    source_prefix_sha256=hidden_prefixes[root],
                    attempts=attempts,
                )
                continue
            original = snapshots.get(root)
            if original is None:
                raise InventoryV4Error("successor_snapshot_missing")
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
        raise InventoryV4Error(str(exc)) from exc


__all__ = ["InventoryV4Error", "SCHEMA", "build_usage_inventory", "verify_successor_root_shape"]
