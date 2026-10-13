"""Full-current Gecko census with an exact historical-v2 projection and v6 replay."""

from __future__ import annotations

import json
import os
import re
import stat
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tools import brain_senses_gecko_capture_only_v6 as capture_v6
from tools import brain_senses_gecko_capture_only_v6_host as host_v6
from tools import brain_senses_gecko_lane_policy_v6 as policy_v6
from tools import brain_senses_gecko_rotating_scout_v1 as scout
from tools import brain_senses_gecko_shared_usage_v1 as shared_usage
from tools import brain_senses_gecko_usage_inventory_v2 as inventory_v2
from tools.brain_senses_external_writer_v1 import DescriptorWriter

SERVICE_ROOT = inventory_v2.SERVICE_ROOT
CAPTURE_V6_ENTRY = "capture-only-v6"
CAPTURE_V6_ROOT = f"{SERVICE_ROOT}/{CAPTURE_V6_ENTRY}"
LEGACY_V6_RELEASE_ID = "259c20d752fa383c3bb42637d10fb5da"
HISTORICAL_RECEIPT_SHA256 = "099eb830ba9aea845799bfaa8e005dbff5dde9b3ef15d32d3baa777d284dd835"
LEGACY_ACTIVATION_PATH = "control/gecko-capture-v6/activation.json"
LEGACY_SOURCE_MANIFEST_PATH = "control/gecko-capture-v6/source-manifest.json"
HISTORICAL_SOURCE_PATHS = tuple(
    path
    for path in host_v6.SOURCE_PATHS
    if path != "tools/brain_senses_gecko_usage_inventory_v3.py"
)
ROOT_ID_RE = re.compile(r"[a-f0-9]{32}\Z")
HASH_RE = re.compile(r"[a-f0-9]{64}\Z")
MAX_EVENT_BYTES = 64 * 1024 * 1024


class InventoryV3Error(ValueError):
    """The historical projection or full current census could not be proved."""


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise InventoryV3Error(reason)


def _parse_canonical(raw: bytes, reason: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryV3Error(reason) from exc
    _require(type(value) is dict and host_v6.canonical(value) == raw, reason)
    return value


def _historical_projection_matches(
    inventory: Mapping[str, Any], receipt: Mapping[str, Any]
) -> None:
    roots = receipt.get("root_inventory")
    snapshots = receipt.get("snapshot_sha256_by_root")
    attempts = receipt.get("attempts")
    _require(
        host_v6.sha256(host_v6.canonical(dict(receipt))) == HISTORICAL_RECEIPT_SHA256
        and type(inventory) is dict
        and set(inventory)
        == {"schema", "root_inventory", "root_inventory_sha256", "snapshots", "provider_io"}
        and inventory.get("schema") == "brain-senses-gecko-shared-usage-inventory-v1"
        and inventory.get("provider_io") is False
        and inventory.get("root_inventory") == roots
        and inventory.get("root_inventory_sha256") == receipt.get("root_inventory_sha256")
        and type(snapshots) is dict
        and type(roots) is dict
        and type(attempts) is list
        and type(inventory.get("snapshots")) is list,
        "gecko_v3_historical_projection_mismatch",
    )
    replay_rows: list[dict[str, Any]] = []
    replay_hashes: dict[str, str] = {}
    for snapshot in inventory["snapshots"]:
        _require(
            type(snapshot) is dict
            and type(snapshot.get("root")) is str
            and type(snapshot.get("attempts")) is list
            and type(snapshot.get("snapshot_sha256")) is str,
            "gecko_v3_historical_snapshot_shape",
        )
        root = snapshot["root"]
        _require(root not in replay_hashes, "gecko_v3_historical_duplicate_root")
        replay_hashes[root] = snapshot["snapshot_sha256"]
        replay_rows.extend({**row, "root": root} for row in snapshot["attempts"])
    replay_rows.sort(key=lambda row: (row.get("decision_ns", -1), row.get("request_id", "")))
    _require(replay_hashes == snapshots, "gecko_v3_historical_snapshot_hash_mismatch")
    _require(replay_rows == attempts, "gecko_v3_historical_attempt_mismatch")
    _require(
        len(replay_rows) == receipt.get("attempt_count") == 62
        and type(roots.get("root_paths")) is list
        and len(roots["root_paths"]) == receipt.get("root_count") == 41,
        "gecko_v3_historical_denominator_mismatch",
    )


class _HistoricalProjectionWriter:
    """Hide only the additive v6 service family from the unchanged v2 verifier."""

    def __init__(self, writer: DescriptorWriter) -> None:
        self._writer = writer

    def list_names(self, relative: str = "") -> tuple[str, ...]:
        names = self._writer.list_names(relative)
        if relative == SERVICE_ROOT:
            return tuple(name for name in names if name != CAPTURE_V6_ENTRY)
        return names

    def __getattr__(self, name: str) -> Any:
        return getattr(self._writer, name)


def _source_manifest_valid(
    manifest: Mapping[str, Any], *, repo_root: Path, release_id: str
) -> dict[str, Any]:
    expected_paths = (
        list(HISTORICAL_SOURCE_PATHS)
        if release_id == LEGACY_V6_RELEASE_ID
        else list(host_v6.SOURCE_PATHS)
    )
    _require(
        type(manifest) is dict
        and manifest.get("schema") == host_v6.SOURCE_MANIFEST_SCHEMA
        and manifest.get("schema_version") == 6
        and manifest.get("source_paths") == expected_paths
        and type(manifest.get("source_map")) is dict
        and set(manifest["source_map"]) == set(expected_paths),
        "gecko_v3_source_manifest_shape",
    )
    source_paths = tuple(expected_paths)
    core = dict(manifest)
    declared = core.pop("source_manifest_sha256", None)
    source_commit = manifest.get("source_commit")
    _require(
        type(source_commit) is str
        and re.fullmatch(r"[0-9a-f]{40}", source_commit) is not None
        and type(declared) is str
        and HASH_RE.fullmatch(declared) is not None
        and declared == host_v6.sha256(host_v6.canonical(core)),
        "gecko_v3_source_manifest_hash",
    )
    try:
        host_v6._git(repo_root, "merge-base", "--is-ancestor", source_commit, "HEAD")
        for relative in source_paths:
            expected = manifest["source_map"].get(relative)
            _require(
                type(expected) is str
                and HASH_RE.fullmatch(expected) is not None
                and host_v6.sha256(host_v6._git_show_blob(repo_root, source_commit, relative))
                == expected,
                "gecko_v3_source_blob_mismatch",
            )
    except host_v6.HostV6Error as exc:
        raise InventoryV3Error("gecko_v3_source_commit_unavailable") from exc
    return dict(manifest)


def _control_paths(release_id: str) -> tuple[str, str]:
    if release_id == LEGACY_V6_RELEASE_ID:
        return LEGACY_SOURCE_MANIFEST_PATH, LEGACY_ACTIVATION_PATH
    directory = f"control/gecko-capture-v6/releases/{release_id}"
    return f"{directory}/source-manifest.json", f"{directory}/activation.json"


def _root_file_digest(writer: DescriptorWriter) -> str:
    files: list[dict[str, Any]] = []
    for relative in writer.list_files():
        raw = writer.read_bytes(relative, max_bytes=MAX_EVENT_BYTES)
        files.append({"path": relative, "size": len(raw), "sha256": host_v6.sha256(raw)})
    return host_v6.sha256(host_v6.canonical(files))


def _validate_v6_release_id(
    release_id: str,
    *,
    writer: DescriptorWriter,
    guard: Any,
    repo_root: Path,
    now_ns: int,
) -> None:
    """Independently replay one source-bound v6 root and enforce exact file closure."""
    root = f"{CAPTURE_V6_ROOT}/{release_id}"
    service_fd = host_v6._open_private_directory(writer.root_fd, root)
    service_writer = DescriptorWriter(service_fd)
    try:
        lock_stat = service_writer.stat(".lane.lock")
        _require(
            stat.S_ISREG(lock_stat.st_mode) and lock_stat.st_size == 0,
            "gecko_v3_service_lock_invalid",
        )
        expected_top_level = {
            ".lane.lock",
            "confirmations",
            "control",
            "epoch.json",
            "events.jsonl",
            "genesis.json",
            "intents",
            "plans",
            "policy.json",
            "requests",
            "responses",
        }
        _require(
            set(service_writer.list_names()) == expected_top_level
            and service_writer.list_names("control") == ("gecko-v6-prior-usage",),
            "gecko_v3_root_entry_closure",
        )
        epoch_raw, epoch = host_v6._load_canonical_object(
            service_writer, capture_v6.EPOCH_PATH, 256 * 1024
        )
        _require(
            epoch.get("release_id") == release_id and epoch.get("root") == root,
            "gecko_v3_epoch_release_binding",
        )
        capture_v6.verify_epoch(epoch)
        policy_raw, policy = host_v6._load_canonical_object(
            service_writer, capture_v6.POLICY_PATH, 512 * 1024
        )
        del epoch_raw, policy_raw
        policy_v6.verify_policy_hash(policy)

        manifest_path, activation_path = _control_paths(release_id)
        manifest_raw, manifest = host_v6._load_canonical_object(
            writer, manifest_path, host_v6.MAX_SOURCE_MAP_BYTES
        )
        activation_raw, activation = host_v6._load_canonical_object(
            writer, activation_path, 128 * 1024
        )
        del manifest_raw, activation_raw
        manifest = _source_manifest_valid(manifest, repo_root=repo_root, release_id=release_id)
        manifest_core = dict(manifest)
        manifest_hash = manifest_core.pop("source_manifest_sha256")
        _require(
            activation.get("release_id") == release_id
            and activation.get("service_root") == root
            and activation.get("source_manifest_sha256") == manifest_hash
            and activation.get("epoch_sha256") == epoch.get("epoch_sha256"),
            "gecko_v3_activation_release_binding",
        )
        # The current owner binding proves the present volume/root identity; v6 epoch
        # identities separately preserve each service inode.
        observed_storage = host_v6.storage_identity(guard, writer.root_fd, service_fd, root)
        _require(
            observed_storage == epoch.get("storage") == activation.get("storage"),
            "gecko_v3_storage_binding",
        )
        plist_raw = host_v6._read_repo_file(repo_root, host_v6.PLIST_PATH, maximum=64 * 1024)
        host_v6.verify_activation_record(
            activation,
            source_manifest=manifest,
            policy=policy,
            storage=observed_storage,
            plist_sha256=host_v6.sha256(plist_raw),
            epoch=epoch,
            repo_root=repo_root,
        )
        receipt_raw = host_v6._read_repo_file(
            repo_root, host_v6.PRIOR_USAGE_CENSUS_PATH, maximum=4 * 1024 * 1024
        )
        inherited = host_v6.verify_prior_usage_census(
            receipt_raw,
            source_manifest=manifest,
            epoch=epoch,
            storage=observed_storage,
            repo_root=repo_root,
        )
        pinned_raw, pinned_rows = capture_v6._load_pinned_prior_usage(service_writer, epoch)
        _require(
            pinned_raw == host_v6.canonical(inherited) and pinned_rows == inherited,
            "gecko_v3_prior_usage_binding",
        )
        events = capture_v6.replay_events(
            service_writer.read_bytes(capture_v6.EVENTS_PATH, max_bytes=MAX_EVENT_BYTES)
        )
        source_hash = _root_file_digest(service_writer)
        expected_storage = observed_storage

        def observed_service_storage() -> Mapping[str, Any]:
            current_fd = host_v6._open_private_directory(writer.root_fd, root)
            try:
                return host_v6.storage_identity(guard, writer.root_fd, current_fd, root)
            finally:
                os.close(current_fd)

        report = capture_v6.verify_local_journal(
            service_writer,
            epoch=epoch,
            policy=policy,
            selector=scout.plan_bucket,
            selector_sha256=activation["selector_sha256"],
            expected_storage=expected_storage,
            observed_storage=observed_service_storage,
            now_ns=now_ns,
            inherited_usage_rows=inherited,
            resource_snapshot={
                "free_bytes": guard.revalidate(
                    min_free_bytes=policy_v6.MIN_FREE_BYTES
                ).available_bytes,
                "resident_bytes": sum(
                    service_writer.stat(path).st_size for path in service_writer.list_files()
                ),
            },
        )
        usage = capture_v6.usage(events, now_ns=now_ns)
        outcomes = {
            event["fields"]["request_id"]: event["fields"]
            for event in events
            if event["kind"] == "outcome"
        }
        attempts = []
        intent_times = {
            event["fields"]["request_id"]: event["fields"]["intent_ns"]
            for event in events
            if event["kind"] == "intent"
        }
        for row in usage["rows"]:
            outcome = outcomes.get(row["request_id"])
            complete = outcome is not None and outcome.get("status") != "possibly_spent_unresolved"
            response_sha = outcome.get("raw_sha256") if complete else None
            response_bytes = outcome.get("raw_bytes") if response_sha is not None else None
            attempts.append(
                {
                    "request_id": row["request_id"],
                    "decision_ns": intent_times[row["request_id"]],
                    "maximum_response_bytes": policy_v6.MAX_RESPONSE_BYTES,
                    "response_bytes": response_bytes,
                    "response_sha256": response_sha,
                }
            )
        attempts.sort(key=lambda row: (row["decision_ns"], row["request_id"]))
        _require(
            report.get("provider_io") is False
            and report.get("economic_admission") is False
            and report.get("market_source_qualified") is False
            and report.get("training_admission") is False,
            "gecko_v3_v6_replay_claim",
        )
        if release_id == LEGACY_V6_RELEASE_ID:
            _require(
                report.get("plan_count") == 0
                and not events
                and not attempts
                and service_writer.list_files()
                == sorted(
                    [
                        ".lane.lock",
                        epoch["prior_usage"]["path"],
                        "epoch.json",
                        "events.jsonl",
                        "genesis.json",
                        "policy.json",
                    ]
                ),
                "gecko_v3_legacy_v6_not_verified_none",
            )
        return {
            "root": root,
            "status": "verified_prefix" if attempts else "verified_none",
            "source_prefix_sha256": source_hash,
            "attempts": attempts,
        }
    finally:
        service_writer.close()
        os.close(service_fd)


def _full_shape(writer: DescriptorWriter) -> tuple[list[str], list[str]]:
    entries = list(writer.list_names(SERVICE_ROOT))
    _require(
        entries == sorted(set(entries))
        and set(entries) <= inventory_v2.KNOWN_SERVICE_ENTRIES | {CAPTURE_V6_ENTRY},
        "gecko_v3_service_entry_unknown",
    )
    ids: list[str] = []
    if CAPTURE_V6_ENTRY in entries:
        ids = list(writer.list_names(CAPTURE_V6_ROOT))
        _require(
            ids == sorted(set(ids)) and all(ROOT_ID_RE.fullmatch(item) for item in ids),
            "gecko_v3_release_id_invalid",
        )
    return entries, ids


def build_usage_inventory_v3(
    writer: DescriptorWriter,
    *,
    guard: Any,
    historical_receipt: Mapping[str, Any],
    candidate_release_id: str | None = None,
    require_candidate_absent: bool = False,
    repo_root: Path = host_v6.REPO_ROOT,
    now_ns: int | None = None,
) -> dict[str, Any]:
    """Verify historical v2 projection plus every present v6 root, without provider I/O."""
    observed_ns = time.time_ns() if now_ns is None else now_ns
    _require(type(observed_ns) is int and observed_ns > 0, "gecko_v3_clock_invalid")
    before_entries, before_ids = _full_shape(writer)
    if candidate_release_id is not None:
        _require(ROOT_ID_RE.fullmatch(candidate_release_id) is not None, "gecko_v3_candidate_id")
    if require_candidate_absent and candidate_release_id is not None:
        _require(candidate_release_id not in before_ids, "gecko_v3_candidate_already_present")

    historical = inventory_v2.build_usage_inventory(_HistoricalProjectionWriter(writer))
    _historical_projection_matches(historical, historical_receipt)
    v6_roots = [
        _validate_v6_release_id(
            release_id,
            writer=writer,
            guard=guard,
            repo_root=repo_root,
            now_ns=observed_ns,
        )
        for release_id in before_ids
    ]
    after_entries, after_ids = _full_shape(writer)
    _require(
        before_entries == after_entries and before_ids == after_ids,
        "gecko_v3_inventory_changed_during_replay",
    )

    historical_snapshots = {row["root"]: row for row in historical["snapshots"]}
    roots = sorted([*historical_snapshots, *(row["root"] for row in v6_roots)])
    full_manifest = {
        "schema": "brain-senses-gecko-full-current-inventory-v3",
        "schema_version": 3,
        "service_root": SERVICE_ROOT,
        "service_entries": before_entries,
        "historical_v2_root_inventory_sha256": historical["root_inventory_sha256"],
        "capture_only_v6_release_ids": before_ids,
        "root_paths": roots,
    }
    full_hash = host_v6.sha256(host_v6.canonical(full_manifest))
    snapshots = []
    for root in roots:
        old = historical_snapshots.get(root)
        if old is None:
            current = next(row for row in v6_roots if row["root"] == root)
            status = current["status"]
            prefix_hash = current["source_prefix_sha256"]
            attempts = current["attempts"]
        else:
            status = old["status"]
            prefix_hash = old["source_prefix_sha256"]
            attempts = old["attempts"]
        snapshots.append(
            shared_usage.make_verified_root_snapshot(
                root=root,
                status=status,
                root_inventory_sha256=full_hash,
                source_prefix_sha256=prefix_hash,
                attempts=attempts,
            )
        )

    # Recheck names and byte-prefix hashes after all per-root semantic replays.
    final_entries, final_ids = _full_shape(writer)
    _require(
        final_entries == before_entries and final_ids == before_ids,
        "gecko_v3_inventory_changed_after_replay",
    )
    for root, snapshot in historical_snapshots.items():
        if snapshot["status"] == "verified_absent" or root.startswith(
            f"{inventory_v2.WATCHDOG_ROOT}/"
        ):
            # Watchdog snapshots intentionally bind immutable signed receipts rather
            # than raw-root bytes; v2 independently replays and rechecks those receipts.
            continue
        _require(
            inventory_v2._root_files_sha256(writer, root) == snapshot["source_prefix_sha256"],
            "gecko_v3_historical_root_changed_after_replay",
        )
    for row in v6_roots:
        root = row["root"]
        service_fd = host_v6._open_private_directory(writer.root_fd, root)
        service_writer = DescriptorWriter(service_fd)
        try:
            _require(
                _root_file_digest(service_writer) == row["source_prefix_sha256"],
                "gecko_v3_v6_root_changed_after_replay",
            )
        finally:
            service_writer.close()
            os.close(service_fd)
    return {
        "schema": "brain-senses-gecko-shared-usage-inventory-v3",
        "root_inventory": full_manifest,
        "root_inventory_sha256": full_hash,
        "snapshots": snapshots,
        "provider_io": False,
        "candidate_release_present": (
            candidate_release_id in before_ids if candidate_release_id is not None else None
        ),
        "historical_attempt_count": historical_receipt.get("attempt_count"),
    }


def assess_shared_usage(
    inventory: Mapping[str, Any], *, checked_ns: int, prospective_request_id: str
) -> dict[str, Any]:
    """Apply request/raw/physical caps to historical carry-in and every current v6 root."""
    snapshots = inventory.get("snapshots")
    root_paths = inventory.get("root_inventory", {}).get("root_paths")
    _require(
        type(snapshots) is list and type(root_paths) is list,
        "gecko_v3_assessment_inventory_shape",
    )
    assessment = shared_usage.assess_usage(
        snapshots,
        expected_roots=root_paths,
        checked_ns=checked_ns,
        prospective_request_id=prospective_request_id,
        prospective_maximum_response_bytes=policy_v6.MAX_RESPONSE_BYTES,
    )
    attempts = [row for snapshot in snapshots for row in snapshot["attempts"]]
    rolling_start = checked_ns - shared_usage.ROLLING_NS
    in_window = [row for row in attempts if rolling_start <= row["decision_ns"] <= checked_ns]
    physical_rolling = (len(in_window) + 1) * policy_v6.PHYSICAL_RESERVE_PER_INTENT
    physical_cap = policy_v6.MAX_PHYSICAL_RESERVE_PER_ROLLING_30D
    reasons = list(assessment["reasons"])
    if physical_rolling > physical_cap:
        reasons.append("rolling_physical_reserve_cap")
    assessment["rolling_30d"]["physical_reserve_including_prospective"] = physical_rolling
    assessment["rolling_30d"]["maximum_physical_reserve"] = physical_cap
    assessment["admitted"] = not reasons
    assessment["reasons"] = reasons
    return assessment


__all__ = [
    "InventoryV3Error",
    "assess_shared_usage",
    "build_usage_inventory_v3",
]
