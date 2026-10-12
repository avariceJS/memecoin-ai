"""Descriptor-bound enumeration and independent replay of all Gecko usage roots."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import stat
from collections.abc import Mapping, Sequence
from typing import Any, cast

from memecoin_research_storage import _open_relative_directory
from tools import (
    brain_senses_gecko_capture_only_runtime_v1 as capture_runtime,
)
from tools import (
    brain_senses_gecko_capture_only_verify_v1 as capture_verify,
)
from tools import (
    brain_senses_gecko_lane_runtime_v3 as runtime_v3,
)
from tools import (
    brain_senses_gecko_lane_runtime_v4 as runtime_v4,
)
from tools import (
    brain_senses_gecko_lane_verify_v3 as verify_v3,
)
from tools import (
    brain_senses_gecko_lane_verify_v4 as verify_clocked,
)
from tools import (
    brain_senses_gecko_lane_verify_v5 as verify_v5,
)
from tools import (
    brain_senses_gecko_shared_usage_v1 as shared_usage,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter
from tools.brain_senses_gecko_rotating_scout_v1 import MAX_RESPONSE_BYTES

SERVICE_ROOT = "services/gecko-market-v1"
DIRECT_ROOTS = ("v3", "v4", "v5")
COLLECTION_ROOTS = ("v3-pilots", "v4-pilots", "capture-only-v1", "capture-only-v2")
KNOWN_SERVICE_ENTRIES = set(DIRECT_ROOTS) | set(COLLECTION_ROOTS)
ROOT_ID_RE = re.compile(r"^[a-f0-9]{32}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
MAX_JOURNAL_FILE_BYTES = 32 * 1024 * 1024


class InventoryError(ValueError):
    """Root census, storage, plan-version, or independent replay failed."""


def canonical_json(value: Any) -> bytes:
    return cast(bytes, shared_usage.canonical_json(value))


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise InventoryError(reason)


def _discover_roots(writer: DescriptorWriter) -> tuple[list[str], dict[str, Any], str]:
    service_entries = writer.list_names(SERVICE_ROOT)
    _require(set(service_entries) <= KNOWN_SERVICE_ENTRIES, "gecko_service_entry_unknown")
    manifest: dict[str, Any] = {
        "service_root": SERVICE_ROOT,
        "service_entries": list(service_entries),
    }
    roots: set[str] = set()
    for name in DIRECT_ROOTS:
        roots.add(f"{SERVICE_ROOT}/{name}")
    collections: dict[str, Any] = {}
    for parent_name in COLLECTION_ROOTS:
        parent = f"{SERVICE_ROOT}/{parent_name}"
        if parent_name not in service_entries:
            collections[parent_name] = {"present": False, "children": []}
            continue
        children = writer.list_names(parent)
        _require(
            all(ROOT_ID_RE.fullmatch(child) for child in children), "gecko_pilot_identity_invalid"
        )
        collections[parent_name] = {"present": True, "children": list(children)}
        roots.update(f"{parent}/{child}" for child in children)
    manifest["collections"] = collections
    manifest["direct_root_presence"] = {name: name in service_entries for name in DIRECT_ROOTS}
    root_list = sorted(roots)
    manifest["root_paths"] = root_list
    return root_list, manifest, sha256(canonical_json(manifest))


def _shared_root_lock(writer: DescriptorWriter, root: str) -> int:
    root_fd = _open_relative_directory(writer.root_fd, root)
    try:
        root_stat = os.fstat(root_fd)
        fd = os.open(
            ".runner.lock",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=root_fd,
        )
    except BaseException:
        os.close(root_fd)
        raise
    os.close(root_fd)
    lock_stat = os.fstat(fd)
    if (
        not stat.S_ISREG(lock_stat.st_mode)
        or lock_stat.st_dev != writer.device
        or lock_stat.st_dev != root_stat.st_dev
        or lock_stat.st_uid != os.getuid()
        or lock_stat.st_nlink != 1
        or stat.S_IMODE(lock_stat.st_mode) != 0o600
    ):
        os.close(fd)
        raise InventoryError("gecko_runner_lock_unsafe")
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise InventoryError("gecko_root_writer_active") from exc
    return fd


def _root_files_sha256(writer: DescriptorWriter, root: str) -> str:
    files: list[dict[str, Any]] = []

    def visit(relative: str) -> None:
        for name in writer.list_names(relative):
            path = f"{relative}/{name}"
            entry = writer.stat(path)
            if stat.S_ISDIR(entry.st_mode):
                visit(path)
            else:
                raw = writer.read_bytes(path, max_bytes=MAX_JOURNAL_FILE_BYTES)
                files.append(
                    {"path": path[len(root) + 1 :], "size": len(raw), "sha256": sha256(raw)}
                )

    visit(root)
    return sha256(canonical_json(files))


def _json_rows(store: Any, directory: str) -> list[dict[str, Any]]:
    names = store.files(directory)
    _require(all(name.endswith(".json") for name in names), "gecko_journal_file_unexpected")
    return [store.read_json(f"{directory}/{name}") for name in names]


def _raw_responses(store: Any, terminals: Sequence[Mapping[str, Any]]) -> dict[str, bytes]:
    raw: dict[str, bytes] = {}
    for row in terminals:
        request_id = row.get("request_id")
        _require(type(request_id) is str, "gecko_result_request_id_invalid")
        request_id = cast(str, request_id)
        path = f"captures/{request_id}.bin"
        if store.writer.exists(store._path(path)):
            raw[request_id] = store.writer.read_bytes(
                store._path(path), max_bytes=MAX_JOURNAL_FILE_BYTES
            )
    return raw


def _attempt_summaries(
    intents: Sequence[Mapping[str, Any]],
    terminals: Sequence[Mapping[str, Any]],
    maximum_response_bytes: int,
) -> list[dict[str, Any]]:
    terminal_by_id = {row["request_id"]: row for row in terminals}
    attempts = []
    for intent in intents:
        request_id = intent.get("request_id")
        decision_ns = intent.get("decision_ns")
        _require(type(request_id) is str and type(decision_ns) is int, "gecko_intent_usage_shape")
        terminal = terminal_by_id.get(request_id)
        digest = terminal.get("response_sha256") if terminal is not None else None
        size = terminal.get("response_bytes") if terminal is not None else None
        if digest is None:
            digest = None
            size = None
        attempts.append(
            {
                "request_id": request_id,
                "decision_ns": decision_ns,
                "maximum_response_bytes": maximum_response_bytes,
                "response_bytes": size,
                "response_sha256": digest,
            }
        )
    return sorted(attempts, key=lambda row: (row["decision_ns"], row["request_id"]))


def _verify_inert_legacy_capture_root(store: Any, plan: Mapping[str, Any]) -> None:
    """Accept only old source-pinned roots that never had provider authority or attempts."""
    required = {
        "schema",
        "schema_version",
        "capture_id",
        "service_root",
        "storage",
        "predecessor",
        "policy_sha256",
        "policy",
        "source_sha256",
        "schedule",
        "authority",
        "plan_sha256",
    }
    _require(set(plan) == required, "legacy_capture_plan_shape")
    core = dict(plan)
    claimed = core.pop("plan_sha256")
    _require(claimed == sha256(canonical_json(core)), "legacy_capture_plan_hash")
    _require(plan.get("service_root") == store.root, "legacy_capture_root_binding")
    _require(
        plan.get("authority")
        == {
            "capture_authorized": False,
            "market_source_qualified": False,
            "training_admission": False,
            "economic_admission": False,
            "provider_io": False,
        },
        "legacy_capture_authority_invalid",
    )
    policy = plan.get("policy")
    _require(
        type(policy) is dict and plan.get("policy_sha256") == policy.get("policy_sha256"),
        "legacy_capture_policy_hash",
    )
    runtime_v4.offline.verify_policy_v4(policy)
    _require(
        not store.writer.exists(store._path("activation.json")),
        "legacy_capture_activation_present",
    )
    for directory in ("intents", "results", "captures", "skips", "clocks"):
        _require(not store.files(directory), "legacy_capture_attempt_markers_present")
    for journal in ("dispatch.jsonl", "result-confirmations.jsonl"):
        _require(
            not store.writer.exists(store._path(journal)),
            "legacy_capture_attempt_journal_present",
        )


def _verify_plan_envelope(plan: Mapping[str, Any], *, root: str, version: int) -> None:
    """Bind the immutable plan shell; version-specific independent verifiers replay journals."""
    _require(type(plan) is dict, "gecko_plan_shape_invalid")
    core = dict(plan)
    claimed = core.pop("plan_sha256", None)
    _require(
        type(claimed) is str and claimed == sha256(canonical_json(core)), "gecko_plan_hash_invalid"
    )
    _require(
        plan.get("schema") == f"brain-senses-gecko-live-v{version}-plan",
        "gecko_plan_schema_invalid",
    )
    _require(plan.get("schema_version") == version, "gecko_plan_version_invalid")
    _require(plan.get("activation_authorized") is False, "gecko_plan_activation_invalid")
    _require(plan.get("provider_io") is False, "gecko_plan_provider_authority_invalid")
    _require(plan.get("release_required") is True, "gecko_plan_release_gate_missing")
    pilot_id = plan.get("pilot_id")
    if pilot_id is None:
        _require(version == 3 and root == f"{SERVICE_ROOT}/v3", "gecko_plan_root_invalid")
        _require(plan.get("service_root") == SERVICE_ROOT, "gecko_plan_root_invalid")
    else:
        _require(
            type(pilot_id) is str
            and ROOT_ID_RE.fullmatch(pilot_id) is not None
            and root.endswith(f"/{pilot_id}")
            and plan.get("service_root") == root,
            "gecko_plan_pilot_identity_invalid",
        )
    policy = plan.get("policy")
    _require(
        type(policy) is dict and plan.get("policy_sha256") == policy.get("policy_sha256"),
        "gecko_plan_policy_hash_invalid",
    )
    sources = plan.get("source_sha256")
    _require(
        type(sources) is dict
        and bool(sources)
        and all(
            type(key) is str and type(value) is str and HASH_RE.fullmatch(value)
            for key, value in sources.items()
        ),
        "gecko_plan_sources_invalid",
    )


def _scheduled_closes(
    intents: Sequence[Mapping[str, Any]], skips: Sequence[Mapping[str, Any]]
) -> list[int]:
    closes = {
        intent["request"]["bucket_close_ns"]
        for intent in intents
        if isinstance(intent.get("request"), Mapping)
        and type(intent["request"].get("bucket_close_ns")) is int
    }
    closes.update(
        skip["bucket_close_ns"] for skip in skips if type(skip.get("bucket_close_ns")) is int
    )
    return sorted(closes)


def _load_present_root(
    writer: DescriptorWriter,
    *,
    root: str,
    root_inventory_sha256: str,
) -> dict[str, Any]:
    if root == f"{SERVICE_ROOT}/v5":
        # V5 uses a distinct scout-chain journal contract. Never parse it as v4 usage.
        raise InventoryError("gecko_v5_usage_verifier_unavailable")
    lock_fd = _shared_root_lock(writer, root)
    try:
        if root.startswith(f"{SERVICE_ROOT}/capture-only-"):
            store = runtime_v4.DescriptorLaneStore(writer, guard=None, root=root)
            plan = store.read_json("plan.json")
            try:
                capture_runtime.verify_capture_plan(plan)
                summary = capture_verify.verify_capture_prefix(store, plan)
                _require(summary.get("status") == "verified_prefix", "capture_prefix_unverified")
            except capture_runtime.CaptureRuntimeError as exc:
                if exc.args != ("capture_source_map_shape",):
                    raise
                _verify_inert_legacy_capture_root(store, plan)
                summary = {"status": "verified_prefix", "provider_io": False}
        else:
            store_class = (
                runtime_v3.DescriptorLaneStore if "/v3" in root else runtime_v4.DescriptorLaneStore
            )
            store = store_class(writer, guard=None, root=root)
            plan = store.read_json("plan.json")
            intents = _json_rows(store, "intents")
            terminals = _json_rows(store, "results")
            skips = _json_rows(store, "skips")
            raw = _raw_responses(store, terminals)
            if "/v3" in root:
                _verify_plan_envelope(plan, root=root, version=3)
                dispatch = store.read_dispatch_records()
                confirmations: list[dict[str, Any]] = []
                has_clocked_intent = any("clock_path" in intent for intent in intents)
                if has_clocked_intent:
                    verify_module = verify_clocked
                    clock_files = {
                        f"{root}/clocks/{name}": writer.read_bytes(
                            store._path(f"clocks/{name}"), max_bytes=64 * 1024
                        )
                        for name in store.files("clocks")
                        if name.endswith(".json")
                    }
                else:
                    verify_module = verify_v3
                    clock_files = {}
            else:
                _verify_plan_envelope(plan, root=root, version=4)
                verify_module = verify_v5
                dispatch = store.read_dispatch_records()
                confirmations = store.read_confirmation_records()
                clock_files = {
                    f"{root}/clocks/{name}": writer.read_bytes(
                        store._path(f"clocks/{name}"), max_bytes=64 * 1024
                    )
                    for name in store.files("clocks")
                    if name.endswith(".json")
                }
            if "/v3" in root:
                if has_clocked_intent:
                    summary = verify_module.verify_journal(
                        plan["policy"],
                        intents,
                        terminals,
                        raw,
                        root=root,
                        clock_files=clock_files,
                        skips=skips,
                        scheduled_closes_ns=_scheduled_closes(intents, skips),
                        dispatch_records=dispatch,
                        allow_clocked_v3_forensics=True,
                    )
                else:
                    summary = verify_module.verify_journal(
                        plan["policy"],
                        intents,
                        terminals,
                        raw,
                        skips=skips,
                        scheduled_closes_ns=_scheduled_closes(intents, skips),
                        dispatch_records=dispatch,
                    )
            else:
                summary = verify_module.verify_journal_v5(
                    plan["policy"],
                    intents,
                    terminals,
                    raw,
                    root=root,
                    clock_files=clock_files,
                    skips=skips,
                    scheduled_closes_ns=_scheduled_closes(intents, skips),
                    dispatch_records=dispatch,
                    confirmations=confirmations,
                )
            _require(
                summary.get("status") in {"verified_prefix", "diagnostic_only"},
                f"gecko_journal_unverified:{root}",
            )
        plan_policy = plan["policy"]
        protocol = plan_policy.get("protocol")
        maximum_response_bytes = (
            protocol.get("maximum_raw_response_bytes")
            if isinstance(protocol, Mapping)
            else MAX_RESPONSE_BYTES
        )
        _require(
            type(maximum_response_bytes) is int and maximum_response_bytes > 0,
            "gecko_response_bound_missing",
        )
        # Re-read the exact journal vectors for use accounting after the independent replay.
        intents = _json_rows(store, "intents")
        terminals = _json_rows(store, "results")
        attempts = _attempt_summaries(intents, terminals, cast(int, maximum_response_bytes))
        prefix_hash = _root_files_sha256(writer, root)
        status = "verified_prefix" if attempts else "verified_none"
        return cast(
            dict[str, Any],
            shared_usage.make_verified_root_snapshot(
                root=root,
                status=status,
                root_inventory_sha256=root_inventory_sha256,
                source_prefix_sha256=prefix_hash,
                attempts=attempts,
            ),
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def build_usage_inventory(writer: DescriptorWriter) -> dict[str, Any]:
    """Discover and replay every known Gecko root without writes or provider calls."""
    roots, manifest, inventory_hash = _discover_roots(writer)
    snapshots = []
    for root in roots:
        direct_name = root.rsplit("/", 1)[-1]
        is_direct = root.count("/") == SERVICE_ROOT.count("/") + 1
        present = direct_name in manifest["service_entries"] if is_direct else True
        if not present:
            source_hash = sha256(
                canonical_json(
                    {
                        "root": root,
                        "root_inventory_sha256": inventory_hash,
                        "status": "verified_absent",
                    }
                )
            )
            snapshots.append(
                shared_usage.make_verified_root_snapshot(
                    root=root,
                    status="verified_absent",
                    root_inventory_sha256=inventory_hash,
                    source_prefix_sha256=source_hash,
                    attempts=[],
                )
            )
        else:
            snapshots.append(
                _load_present_root(
                    writer,
                    root=root,
                    root_inventory_sha256=inventory_hash,
                )
            )
    roots_after, _manifest_after, inventory_hash_after = _discover_roots(writer)
    _require(
        roots_after == roots and inventory_hash_after == inventory_hash,
        "gecko_root_inventory_changed_during_replay",
    )
    return {
        "schema": "brain-senses-gecko-shared-usage-inventory-v1",
        "root_inventory": manifest,
        "root_inventory_sha256": inventory_hash,
        "snapshots": snapshots,
        "provider_io": False,
    }


__all__ = ["InventoryError", "build_usage_inventory"]
