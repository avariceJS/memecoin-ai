"""Descriptor-bound enumeration and independent replay of all Gecko usage roots."""

from __future__ import annotations

import base64
import binascii
import fcntl
import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast

from memecoin_research_storage import _open_relative_directory
from tools import (
    brain_senses_capacity_coordinator_v2 as capacity,
)
from tools import (
    brain_senses_gecko_capture_only_runtime_v1 as capture_runtime,
)
from tools import (
    brain_senses_gecko_capture_only_v2_health_v1 as health_v2,
)
from tools import (
    brain_senses_gecko_capture_only_v2_terminal_usage_v1 as terminal_v2_usage,
)
from tools import (
    brain_senses_gecko_capture_only_v2_terminal_usage_v4 as terminal_v3_bridge,
)
from tools import (
    brain_senses_gecko_capture_only_v2_verify as capture_v2_verify,
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
from tools import (
    brain_senses_gecko_v4_usage_reconciliation_v1 as reconcile_v4,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter
from tools.brain_senses_gecko_rotating_scout_v1 import MAX_RESPONSE_BYTES

SERVICE_ROOT = "services/gecko-market-v1"
DIRECT_ROOTS = ("v3", "v4", "v5")
COLLECTION_ROOTS = (
    "v3-pilots",
    "v4-pilots",
    "capture-only-v1",
    "capture-only-v2",
    "host-health-v1",
    "canary-watchdog-v1",
)
KNOWN_SERVICE_ENTRIES = set(DIRECT_ROOTS) | set(COLLECTION_ROOTS)
ROOT_ID_RE = re.compile(r"^[a-f0-9]{32}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_ATOMIC_TEMP_RE = re.compile(r"^\..+\.[0-9]+\.[a-f0-9]{16}\.tmp$")
MAX_JOURNAL_FILE_BYTES = 32 * 1024 * 1024
WATCHDOG_ROOT = f"{SERVICE_ROOT}/canary-watchdog-v1"
OBSERVED_WATCHDOG_RELEASE = "c22205dad614fe5d00b6bbe7869a0c3b"
OBSERVED_WATCHDOG_BUCKET = 1_791_863_100_000_000_000
OBSERVED_WATCHDOG_RECEIPT_SHA256 = (
    "379ae2a4adc8c30e3df2adc0655704c385e695634ad0ea7e900c597fe082f51c"
)


class InventoryError(ValueError):
    """Root census, storage, plan-version, or independent replay failed."""


def canonical_json(value: Any) -> bytes:
    return shared_usage.canonical_json(value)


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


def _watchdog_diagnostic_snapshot(
    writer: DescriptorWriter, *, root: str, root_inventory_sha256: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify the known no-lock diagnostic receipt by stable double-read."""
    release_id = root.rsplit("/", 1)[-1]
    _require(ROOT_ID_RE.fullmatch(release_id) is not None, "watchdog_release_id_invalid")
    sibling_parent = f"{SERVICE_ROOT}/capture-only-v2"
    try:
        sibling_names = writer.list_names(sibling_parent)
    except FileNotFoundError:
        sibling_names = ()
    _require(
        release_id in sibling_names,
        "watchdog_capture_sibling_missing",
    )

    def read_once() -> tuple[tuple[str, ...], bytes, str]:
        names = tuple(writer.list_names(root))
        _require(
            len(names) == 1 and re.fullmatch(r"[0-9]{19}\.json", names[0]) is not None,
            "watchdog_receipt_file_closure",
        )
        relative = f"{root}/{names[0]}"
        info = writer.stat(relative)
        _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "watchdog_receipt_file_unsafe")
        raw = writer.read_bytes(relative, max_bytes=128 * 1024)
        return names, raw, sha256(raw)

    names_before, raw_before, digest_before = read_once()
    try:
        receipt = json.loads(raw_before)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryError("watchdog_receipt_json_invalid") from exc
    _require(
        type(receipt) is dict and canonical_json(receipt) == raw_before,
        "watchdog_receipt_not_canonical",
    )
    receipt_core = dict(receipt)
    record_sha256 = receipt_core.pop("record_sha256", None)
    self_hash_valid = type(record_sha256) is str and record_sha256 == sha256(
        canonical_json(receipt_core)
    )
    filename_close = int(names_before[0][:-5])
    common_valid = (
        self_hash_valid
        and receipt.get("release_id") == release_id
        and receipt.get("bucket_close_ns") == filename_close
        and receipt.get("deadline_ns") == filename_close + 300_000_000_000
        and type(receipt.get("observed_ns")) is int
        and receipt["observed_ns"] >= receipt["deadline_ns"]
        and receipt.get("provider_io") is False
        and receipt.get("activation_authorized") is False
        and receipt.get("status") in {"passed", "failed_closed"}
    )
    if receipt.get("schema") == "brain-senses-gecko-canary-watchdog-v1":
        common_valid = common_valid and (
            set(receipt)
            == {
                "activation_authorized",
                "bucket_close_ns",
                "deadline_ns",
                "observed_ns",
                "provider_io",
                "reason",
                "record_sha256",
                "release_id",
                "schema",
                "schema_version",
                "status",
            }
            and receipt.get("schema_version") == 1
            and receipt.get("status") == "failed_closed"
            and receipt.get("reason") == "StopIteration"
            and release_id == OBSERVED_WATCHDOG_RELEASE
            and filename_close == OBSERVED_WATCHDOG_BUCKET
            and digest_before == OBSERVED_WATCHDOG_RECEIPT_SHA256
        )
    elif receipt.get("schema") == "brain-senses-gecko-canary-watchdog-v2":
        proof = receipt.get("proof")
        if receipt.get("status") == "passed":
            pre = proof.get("pre_publication_census") if type(proof) is dict else None
            common_valid = common_valid and (
                type(proof) is dict
                and set(proof)
                == {
                    "bucket_close_ns",
                    "confirmation_count",
                    "dispatch_count",
                    "dispatch_ns",
                    "economic_admission",
                    "eligible_new_open_count",
                    "intent_count",
                    "market_source_qualified",
                    "missed_dispatch_count",
                    "pre_publication_census",
                    "provider_io",
                    "result_count",
                    "shared_usage_admitted",
                    "timing_gate",
                    "training_admission",
                }
                and receipt.get("schema_version") == 2
                and set(receipt)
                == {
                    "activation_authorized",
                    "bucket_close_ns",
                    "deadline_ns",
                    "observed_ns",
                    "proof",
                    "provider_io",
                    "record_sha256",
                    "release_id",
                    "schema",
                    "schema_version",
                    "status",
                }
                and type(pre) is dict
                and type(pre.get("observed_ns")) is int
                and pre["observed_ns"] <= receipt["observed_ns"]
                and pre.get("includes_this_receipt") is False
                and HASH_RE.fullmatch(str(pre.get("root_inventory_sha256"))) is not None
                and proof.get("timing_gate") == "passed"
                and proof.get("dispatch_count") == 6
                and proof.get("intent_count") == 6
                and proof.get("result_count") == 6
                and proof.get("confirmation_count") == 6
                and proof.get("missed_dispatch_count") == 0
                and proof.get("provider_io") is False
                and proof.get("shared_usage_admitted") is True
                and proof.get("economic_admission") is False
                and proof.get("market_source_qualified") is False
                and proof.get("training_admission") is False
                and type(proof.get("eligible_new_open_count")) is int
                and proof["eligible_new_open_count"] >= 0
                and proof.get("bucket_close_ns") == filename_close
                and type(proof.get("dispatch_ns")) is list
                and len(proof["dispatch_ns"]) == 6
                and all(type(value) is int for value in proof["dispatch_ns"])
            )
        else:
            reason = receipt.get("reason")
            common_valid = common_valid and (
                receipt.get("schema_version") == 2
                and set(receipt)
                == {
                    "activation_authorized",
                    "bucket_close_ns",
                    "deadline_ns",
                    "observed_ns",
                    "provider_io",
                    "reason",
                    "record_sha256",
                    "release_id",
                    "schema",
                    "schema_version",
                    "status",
                }
                and type(reason) is str
                and 1 <= len(reason) <= 128
                and "proof" not in receipt
            )
    else:
        common_valid = False
    _require(common_valid, "watchdog_receipt_semantics_invalid")
    names_after, raw_after, digest_after = read_once()
    _require(
        names_after == names_before and raw_after == raw_before and digest_after == digest_before,
        "watchdog_root_changed_during_replay",
    )
    snapshot = shared_usage.make_verified_root_snapshot(
        root=root,
        status="verified_none",
        root_inventory_sha256=root_inventory_sha256,
        source_prefix_sha256=digest_before,
        attempts=[],
    )
    return snapshot, {"receipt": receipt, "receipt_sha256": digest_before}


def _json_rows(store: Any, directory: str) -> list[dict[str, Any]]:
    names = store.files(directory)
    _require(all(name.endswith(".json") for name in names), "gecko_journal_file_unexpected")
    return [store.read_json(f"{directory}/{name}") for name in names]


def _v2_json_rows(store: Any, directory: str, *, maximum_bytes: int) -> list[dict[str, Any]]:
    names = _v2_final_names(store.files(directory))
    rows = []
    for name in names:
        raw = store.writer.read_bytes(store._path(f"{directory}/{name}"), max_bytes=maximum_bytes)
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InventoryError("gecko_v2_journal_json_invalid") from exc
        _require(
            type(value) is dict and canonical_json(value) == raw, "gecko_v2_journal_noncanonical"
        )
        rows.append(value)
    return rows


def _v2_final_names(names: Sequence[str]) -> tuple[str, ...]:
    """Ignore only exact atomic-writer temp names; never treat them as committed artifacts."""
    final = tuple(name for name in names if name.endswith(".json"))
    debris = tuple(name for name in names if not name.endswith(".json"))
    _require(
        all(_ATOMIC_TEMP_RE.fullmatch(name) is not None for name in debris),
        "gecko_v2_journal_file_unexpected",
    )
    return final


def _v2_directory_names(store: Any, directory: str) -> tuple[str, ...]:
    try:
        return store.files(directory)
    except FileNotFoundError:
        return ()


def _canonical_jsonl_records(raw: bytes, label: str) -> list[dict[str, Any]]:
    _require(not raw or raw.endswith(b"\n"), f"{label}_torn")
    records: list[dict[str, Any]] = []
    for line in raw.splitlines(keepends=True):
        try:
            row = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InventoryError(f"{label}_json_invalid") from exc
        _require(
            type(row) is dict and canonical_json(row) == line,
            f"{label}_noncanonical",
        )
        records.append(row)
    return records


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


def _v2_raw_responses(terminals: Sequence[Mapping[str, Any]]) -> dict[str, bytes]:
    """Project bounded v2 bodies embedded in the atomic result envelope."""
    raw: dict[str, bytes] = {}
    for row in terminals:
        request_id = row.get("request_id")
        body = row.get("raw_body_b64")
        _require(type(request_id) is str, "gecko_result_request_id_invalid")
        if body is None:
            continue
        _require(type(body) is str, "gecko_v2_raw_body_encoding")
        try:
            decoded = base64.b64decode(body, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InventoryError("gecko_v2_raw_body_encoding") from exc
        _require(base64.b64encode(decoded).decode("ascii") == body, "gecko_v2_raw_body_encoding")
        raw[cast(str, request_id)] = decoded
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
    root_metadata: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if root == f"{SERVICE_ROOT}/v5":
        raise InventoryError("gecko_v5_usage_verifier_unavailable")
    if root.startswith(f"{WATCHDOG_ROOT}/"):
        snapshot, diagnostic = _watchdog_diagnostic_snapshot(
            writer, root=root, root_inventory_sha256=root_inventory_sha256
        )
        if root_metadata is not None:
            root_metadata[root] = diagnostic
        return snapshot
    lock_fd = _shared_root_lock(writer, root)
    try:
        if root.startswith(f"{SERVICE_ROOT}/host-health-v1/"):
            health_root_fd = _open_relative_directory(writer.root_fd, root)
            try:
                health_stat = os.fstat(health_root_fd)
            finally:
                os.close(health_root_fd)
            with writer.lock(capacity.LOCK_PATH, blocking=False):
                policy_raw = writer.read_bytes(capacity.POLICY_PATH, max_bytes=1_000_000)
                try:
                    policy = json.loads(policy_raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise InventoryError("gecko_health_coordinator_policy_invalid") from exc
                _require(
                    type(policy) is dict
                    and capacity.canonical(policy) == policy_raw
                    and type(policy.get("root_binding")) is dict,
                    "gecko_health_coordinator_policy_invalid",
                )
                storage = policy["root_binding"]
            _require(
                storage.get("device") == writer.device
                and storage.get("inode") == os.fstat(writer.root_fd).st_ino
                and health_stat.st_dev == writer.device,
                "gecko_health_storage_binding",
            )
            release_id = root.rsplit("/", 1)[-1]
            snapshot = health_v2.read_snapshot(
                writer,
                release_id=release_id,
                storage=storage,
            )
            if root_metadata is not None:
                root_metadata[root] = {"health": snapshot, "storage": storage}
            now_ns = time.time_ns()
            _require(
                snapshot["observed_ns"] <= now_ns,
                "gecko_health_snapshot_from_future",
            )
            if root == terminal_v2_usage.HEALTH_ROOT:
                # Temporary live-census snapshot. The versioned bridge at the end of
                # build_usage_inventory validates the frozen receipt against its exact
                # historical projection, then replaces only this health sidecar.
                return shared_usage.make_verified_root_snapshot(
                    root=root,
                    status="verified_none",
                    root_inventory_sha256=root_inventory_sha256,
                    source_prefix_sha256=_root_files_sha256(writer, root),
                    attempts=[],
                )
            # Health is diagnostic, not usage authority. The matching request root is
            # replayed under its own lock below, so an old clean sidecar cannot block
            # admission forever after an otherwise safe stop or source-pin refresh.
            _require(
                snapshot["status"] != "stopped" and snapshot["stop_latched"] is not True,
                "gecko_health_stop_latched",
            )
            return shared_usage.make_verified_root_snapshot(
                root=root,
                status="verified_none",
                root_inventory_sha256=root_inventory_sha256,
                source_prefix_sha256=_root_files_sha256(writer, root),
                attempts=[],
            )
        if root.startswith(f"{SERVICE_ROOT}/capture-only-v2/"):
            store = runtime_v4.DescriptorLaneStore(writer, guard=None, root=root)
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
            root_entries = set(writer.list_names(root))
            _require(root_entries <= allowed, "gecko_v2_root_entry_unknown")
            _require(
                {"binding.json", "policy.json", "chain"} <= root_entries,
                "gecko_v2_root_incomplete",
            )
            binding = store.read_json("binding.json")
            if root_metadata is not None:
                root_metadata[root] = {"binding": binding}
            policy = store.read_json("policy.json")
            chain_names = _v2_final_names(store.files("chain"))
            _require(
                all(re.fullmatch(r"[0-9]{8}\.json", name) for name in chain_names)
                and list(chain_names) == [f"{index:08d}.json" for index in range(len(chain_names))],
                "gecko_v2_chain_file_closure",
            )
            chain_records = [store.read_json(f"chain/{name}") for name in chain_names]
            intent_names = _v2_final_names(_v2_directory_names(store, "intents"))
            result_names = _v2_final_names(_v2_directory_names(store, "results"))
            skip_names = _v2_final_names(_v2_directory_names(store, "skips"))
            intents = (
                _v2_json_rows(store, "intents", maximum_bytes=8 * 1024 * 1024)
                if intent_names
                else []
            )
            terminals = (
                _v2_json_rows(store, "results", maximum_bytes=64 * 1024) if result_names else []
            )
            skips = _v2_json_rows(store, "skips", maximum_bytes=64 * 1024) if skip_names else []
            _require(
                intent_names == tuple(sorted(f"{row.get('request_id')}.json" for row in intents))
                and result_names
                == tuple(sorted(f"{row.get('request_id')}.json" for row in terminals)),
                "gecko_v2_result_file_closure",
            )
            _require(
                skip_names
                == tuple(
                    sorted(
                        f"{row.get('plan_sequence')}-{row.get('slot_index'):02d}.json"
                        for row in skips
                        if type(row.get("slot_index")) is int
                    )
                ),
                "gecko_v2_skip_file_closure",
            )
            raw = _v2_raw_responses(terminals)
            _require(
                set(raw)
                == {row["request_id"] for row in terminals if row.get("raw_body_b64") is not None},
                "gecko_v2_raw_body_closure",
            )
            confirmation_names = _v2_final_names(_v2_directory_names(store, "confirmations"))
            v2_confirmations = (
                _v2_json_rows(store, "confirmations", maximum_bytes=64 * 1024)
                if confirmation_names
                else []
            )
            _require(
                confirmation_names
                == tuple(sorted(f"{row.get('request_id')}.json" for row in v2_confirmations)),
                "gecko_v2_confirmation_file_closure",
            )
            dispatch_raw = (
                writer.read_bytes(store._path("dispatch.jsonl"), max_bytes=16 * 1024 * 1024)
                if writer.exists(store._path("dispatch.jsonl"))
                else b""
            )
            _require(
                not dispatch_raw or dispatch_raw.endswith(b"\n"),
                "gecko_v2_dispatch_torn",
            )
            try:
                dispatch_records = _canonical_jsonl_records(dispatch_raw, "gecko_v2_dispatch")
                with writer.lock(capacity.LOCK_PATH, blocking=False):
                    coordinator_policy_raw = writer.read_bytes(
                        capacity.POLICY_PATH, max_bytes=1_000_000
                    )
                    coordinator_policy = json.loads(coordinator_policy_raw)
                    _require(
                        capacity.canonical(coordinator_policy) == coordinator_policy_raw
                        and coordinator_policy.get("root_binding") == binding.get("storage")
                        and binding.get("storage", {}).get("device") == writer.device
                        and binding.get("storage", {}).get("inode")
                        == os.fstat(writer.root_fd).st_ino,
                        "gecko_v2_coordinator_policy_binding",
                    )
                    coordinator_raw = writer.read_bytes(capacity.LEDGER_PATH, max_bytes=64_000_000)
                    coordinator_events = capacity._verify_ledger(coordinator_raw)
                    _require(
                        coordinator_events[0]["fields"].get("policy_sha256")
                        == capacity.sha256(coordinator_policy_raw),
                        "gecko_v2_coordinator_policy_hash",
                    )
            except (ValueError, KeyError, TypeError, capacity.CoordinatorError) as exc:
                raise InventoryError("gecko_v2_coordinator_replay_failed") from exc
            summary = capture_v2_verify.verify_capture_prefix_v2(
                root=root,
                binding=binding,
                policy=policy,
                chain_records=chain_records,
                intents=intents,
                dispatch_records=dispatch_records,
                terminals=terminals,
                raw_responses=raw,
                confirmations=v2_confirmations,
                coordinator_events=coordinator_events,
                skips=skips,
            )
        elif root.startswith(f"{SERVICE_ROOT}/capture-only-"):
            if root == reconcile_v4.ROOT:
                receipt = reconcile_v4.load_reconciliation_receipt()
                snapshot, _replay = reconcile_v4.replay_usage_root(
                    writer,
                    root_inventory_sha256=root_inventory_sha256,
                    receipt=receipt,
                    storage=receipt["storage"],
                )
                return snapshot
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
        if root.startswith(f"{SERVICE_ROOT}/capture-only-v2/"):
            attempts = summary["usage_attempts"]
            prefix_hash = _root_files_sha256(writer, root)
            status = "verified_prefix" if attempts else "verified_none"
            return shared_usage.make_verified_root_snapshot(
                root=root,
                status=status,
                root_inventory_sha256=root_inventory_sha256,
                source_prefix_sha256=prefix_hash,
                attempts=attempts,
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
        return shared_usage.make_verified_root_snapshot(
            root=root,
            status=status,
            root_inventory_sha256=root_inventory_sha256,
            source_prefix_sha256=prefix_hash,
            attempts=attempts,
        )
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def build_usage_inventory(writer: DescriptorWriter) -> dict[str, Any]:
    """Discover and replay every known Gecko root without writes or provider calls."""
    roots, manifest, inventory_hash = _discover_roots(writer)
    snapshots = []
    root_metadata: dict[str, dict[str, Any]] = {}
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
                    root_metadata=root_metadata,
                )
            )
    roots_after, manifest_after, inventory_hash_after = _discover_roots(writer)
    _require(
        roots_after == roots and inventory_hash_after == inventory_hash,
        "gecko_root_inventory_changed_during_replay",
    )
    root_stat = os.fstat(writer.root_fd)
    bound_a078_storage = (
        root_stat.st_dev == terminal_v2_usage.EXPECTED_STORAGE["device"]
        and root_stat.st_ino == terminal_v2_usage.EXPECTED_STORAGE["inode"]
    )
    if (
        bound_a078_storage
        or terminal_v2_usage.ROOT in roots
        or terminal_v2_usage.HEALTH_ROOT in roots
    ):
        receipt = terminal_v2_usage.load_pinned_receipt()
        try:
            health_snapshot = terminal_v3_bridge.reconcile_a078_health_snapshot(
                writer,
                receipt=receipt,
                receipt_sha256=terminal_v2_usage.RECEIPT_SHA256,
                storage=receipt["storage"],
                current_roots=roots,
                current_manifest=manifest,
                current_inventory_sha256=inventory_hash,
                after_roots=roots_after,
                after_manifest=manifest_after,
                after_inventory_sha256=inventory_hash_after,
                snapshots=snapshots,
                root_metadata=root_metadata,
            )
        except terminal_v3_bridge.CensusBridgeError as exc:
            raise InventoryError(str(exc)) from exc
        health_indexes = [
            index
            for index, snapshot in enumerate(snapshots)
            if snapshot.get("root") == terminal_v2_usage.HEALTH_ROOT
        ]
        _require(len(health_indexes) == 1, "a078_health_snapshot_missing")
        snapshots[health_indexes[0]] = health_snapshot
    snapshots_by_root = {row["root"]: row for row in snapshots}
    for root in roots:
        if not root.startswith(f"{WATCHDOG_ROOT}/"):
            continue
        repeated, repeated_metadata = _watchdog_diagnostic_snapshot(
            writer, root=root, root_inventory_sha256=inventory_hash
        )
        _require(
            repeated == snapshots_by_root[root] and repeated_metadata == root_metadata.get(root),
            "watchdog_root_changed_during_full_replay",
        )
    roots_final, manifest_final, inventory_hash_final = _discover_roots(writer)
    _require(
        roots_final == roots
        and manifest_final == manifest
        and inventory_hash_final == inventory_hash,
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
