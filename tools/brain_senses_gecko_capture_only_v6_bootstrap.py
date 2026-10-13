"""Create-only owner preparation for one source-bound Gecko capture-only v6 epoch.

This module performs no provider or launchd operations. It requires an already-open,
verified BrainData writer and the shared v6 cross-release lease. Production callers
must supply the live owner facts; tests use disposable descriptor roots only.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from tools import brain_senses_gecko_capture_only_v6 as capture_v6
from tools import brain_senses_gecko_capture_only_v6_host as host_v6
from tools import brain_senses_gecko_lane_policy_v6 as policy_v6
from tools import brain_senses_gecko_rotating_scout_v1 as scout
from tools import brain_senses_gecko_usage_inventory_v2 as usage_inventory_v2
from tools.brain_senses_external_writer_v1 import DescriptorWriter

SCHEMA = "brain-senses-gecko-capture-v6-bootstrap"
SCHEMA_VERSION = 1
PRIOR_USAGE_DIR = "control/gecko-v6-prior-usage"
ANCHOR_MARGIN_NS = 2 * 60 * 1_000_000_000
RECEIPT_EXPECTED_ATTEMPTS = 62
RECEIPT_EXPECTED_RAW_BYTES = 1_015_808
RECEIPT_EXPECTED_PHYSICAL_BYTES = 5_079_040


class BootstrapError(ValueError):
    """Release preparation failed closed."""


def canonical(value: Any) -> bytes:
    return host_v6.canonical(value)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise BootstrapError(reason)


def _anchor_after(now_ns: int, observed_ns: int, *, margin_ns: int) -> int:
    if type(now_ns) is not int or now_ns <= 0 or type(margin_ns) is not int or margin_ns < 0:
        raise BootstrapError("v6_bootstrap_clock_invalid")
    bucket = policy_v6.BUCKET_NS
    lower = max(now_ns + margin_ns, observed_ns + 1)
    return ((lower + bucket - 1) // bucket) * bucket


def _assert_inventory_matches_receipt(
    inventory: Mapping[str, Any], receipt: Mapping[str, Any]
) -> None:
    """Join a fresh v2 full replay to the exact committed metadata census."""
    roots = receipt.get("root_inventory")
    snapshots = receipt.get("snapshot_sha256_by_root")
    attempts = receipt.get("attempts")
    _require(
        type(inventory) is dict
        and set(inventory)
        == {"schema", "root_inventory", "root_inventory_sha256", "snapshots", "provider_io"}
        and inventory.get("schema") == "brain-senses-gecko-shared-usage-inventory-v1"
        and inventory.get("provider_io") is False
        and inventory.get("root_inventory") == roots
        and inventory.get("root_inventory_sha256") == receipt.get("root_inventory_sha256")
        and type(snapshots) is dict
        and type(attempts) is list
        and type(inventory.get("snapshots")) is list,
        "v6_bootstrap_inventory_shape_or_root_mismatch",
    )
    replay_rows: list[dict[str, Any]] = []
    replay_hashes: dict[str, str] = {}
    for snapshot in inventory["snapshots"]:
        _require(
            type(snapshot) is dict
            and type(snapshot.get("root")) is str
            and type(snapshot.get("attempts")) is list
            and type(snapshot.get("snapshot_sha256")) is str,
            "v6_bootstrap_inventory_snapshot_shape",
        )
        root = snapshot["root"]
        _require(root not in replay_hashes, "v6_bootstrap_inventory_duplicate_root")
        replay_hashes[root] = snapshot["snapshot_sha256"]
        for attempt in snapshot["attempts"]:
            _require(type(attempt) is dict, "v6_bootstrap_inventory_attempt_shape")
            replay_rows.append({**attempt, "root": root})
    replay_rows.sort(key=lambda row: (row.get("decision_ns", -1), row.get("request_id", "")))
    _require(replay_hashes == snapshots, "v6_bootstrap_inventory_snapshot_hash_mismatch")
    _require(replay_rows == attempts, "v6_bootstrap_inventory_attempt_mismatch")
    _require(
        len(replay_rows) == RECEIPT_EXPECTED_ATTEMPTS
        and sum(row["maximum_response_bytes"] for row in replay_rows) == RECEIPT_EXPECTED_RAW_BYTES
        and sum(policy_v6.PHYSICAL_RESERVE_PER_INTENT for _ in replay_rows)
        == RECEIPT_EXPECTED_PHYSICAL_BYTES
        and receipt.get("attempt_count") == RECEIPT_EXPECTED_ATTEMPTS
        and receipt.get("raw_bytes_upper_bound") == RECEIPT_EXPECTED_RAW_BYTES
        and receipt.get("physical_bytes_upper_bound") == RECEIPT_EXPECTED_PHYSICAL_BYTES,
        "v6_bootstrap_prior_usage_denominator_mismatch",
    )


def _check_prior_caps(rows: Sequence[Mapping[str, int]], *, now_ns: int) -> None:
    day_start = (now_ns // capture_v6.DAY_NS) * capture_v6.DAY_NS
    rolling_start = now_ns - capture_v6.ROLLING_WINDOW_NS
    recent = [row for row in rows if rolling_start <= row["intent_ns"] <= now_ns]
    daily = [row for row in rows if day_start <= row["intent_ns"] <= now_ns]
    _require(
        len(recent) <= policy_v6.MAX_INTENTS_PER_ROLLING_30D
        and sum(row["raw_bytes_upper_bound"] for row in recent)
        <= policy_v6.MAX_RAW_BYTES_PER_ROLLING_30D
        and sum(row["physical_bytes_upper_bound"] for row in recent)
        <= policy_v6.MAX_PHYSICAL_RESERVE_PER_ROLLING_30D
        and len(daily) <= policy_v6.MAX_INTENTS_PER_UTC_DAY
        and sum(row["raw_bytes_upper_bound"] for row in daily)
        <= policy_v6.MAX_RAW_BYTES_PER_UTC_DAY,
        "v6_bootstrap_prior_usage_cap",
    )


def _root_storage_identity(guard: Any, data_writer: DescriptorWriter) -> dict[str, Any]:
    data_stat = os.fstat(data_writer.root_fd)
    mount_stat = os.fstat(guard.mount_fd)
    _require(
        stat.S_ISDIR(data_stat.st_mode)
        and data_stat.st_uid == os.getuid()
        and stat.S_IMODE(data_stat.st_mode) == 0o700
        and data_stat.st_dev == mount_stat.st_dev
        and guard.config.data_root == "BrainData"
        and type(guard.root_id) is str
        and type(guard.volume_uuid) is str,
        "v6_bootstrap_data_root_identity",
    )
    try:
        guard.revalidate(min_free_bytes=policy_v6.MIN_FREE_BYTES)
    except Exception as exc:
        raise BootstrapError("v6_bootstrap_storage_preflight") from exc
    return {
        "root_id": guard.root_id,
        "volume_uuid": guard.volume_uuid.lower(),
        "data_root": {"device": data_stat.st_dev, "inode": data_stat.st_ino},
    }


def _write_identical_or_create(
    writer: DescriptorWriter,
    path: str,
    payload: bytes,
    *,
    revalidate: Callable[[], Any],
) -> None:
    revalidate()
    try:
        writer.create(path, payload)
    except FileExistsError:
        if writer.read_bytes(path, max_bytes=max(len(payload), 1)) != payload:
            raise BootstrapError("v6_bootstrap_create_only_conflict") from None
    revalidate()
    if writer.read_bytes(path, max_bytes=max(len(payload), 1)) != payload:
        raise BootstrapError("v6_bootstrap_readback_mismatch")


def prepare_v6_release(
    data_root_writer: DescriptorWriter,
    *,
    guard: Any,
    lane_lease: capture_v6.CrossReleaseLaneLeaseV6,
    active_gecko_labels: Sequence[str],
    wall_ns: Callable[[], int] = time.time_ns,
    repo_root: Path = host_v6.REPO_ROOT,
    fault_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Create one deterministic v6 epoch; activation is the final durable write.

    `data_root_writer` must already be rooted at the verified BrainData descriptor. The
    function does not open a mount, config file, provider, or launchd service.
    """
    from tools import brain_senses_gecko_usage_inventory_v2 as inventory_v2

    _require(
        (os.fstat(data_root_writer.root_fd).st_dev, os.fstat(data_root_writer.root_fd).st_ino)
        == (os.fstat(lane_lease.writer.root_fd).st_dev, os.fstat(lane_lease.writer.root_fd).st_ino),
        "v6_bootstrap_lane_writer_mismatch",
    )
    labels = list(active_gecko_labels)
    _require(
        type(active_gecko_labels) in {list, tuple}
        and labels == []
        and all(type(label) is str for label in labels),
        "v6_bootstrap_competing_gecko_label",
    )

    def revalidate() -> None:
        try:
            guard.revalidate(min_free_bytes=policy_v6.MIN_FREE_BYTES)
        except Exception as exc:
            raise BootstrapError("v6_bootstrap_storage_preflight") from exc
        root_stat = os.fstat(data_root_writer.root_fd)
        _require(
            root_stat.st_dev == os.fstat(guard.mount_fd).st_dev
            and root_stat.st_uid == os.getuid()
            and stat.S_IMODE(root_stat.st_mode) == 0o700,
            "v6_bootstrap_data_root_changed",
        )

    # Give every write in this bootstrap an independent pre-write guard call.
    guarded_writer = DescriptorWriter(data_root_writer.root_fd, before_write=revalidate)
    service_writer: DescriptorWriter | None = None
    service_fd = -1
    service_storage: dict[str, Any] | None = None
    try:
        with lane_lease.hold():
            revalidate()
            _root_storage_identity(guard, guarded_writer)
            try:
                resource = guard.revalidate(min_free_bytes=policy_v6.MIN_FREE_BYTES)
            except Exception as exc:
                raise BootstrapError("v6_bootstrap_storage_preflight") from exc
            _require(
                type(resource.available_bytes) is int
                and resource.available_bytes >= policy_v6.MIN_FREE_BYTES,
                "v6_bootstrap_free_space_floor",
            )

            source_manifest = host_v6.compile_source_manifest(repo_root)
            host_v6.verify_source_manifest(repo_root, source_manifest)
            receipt_raw = host_v6._read_repo_file(
                repo_root, host_v6.PRIOR_USAGE_CENSUS_PATH, maximum=4 * 1024 * 1024
            )
            receipt = json.loads(receipt_raw.decode("utf-8"))
            _require(
                type(receipt) is dict
                and source_manifest["source_map"].get(host_v6.PRIOR_USAGE_CENSUS_PATH)
                == sha256(receipt_raw),
                "v6_bootstrap_census_source_pin",
            )
            policy = host_v6.load_policy_from_writer(guarded_writer)
            policy_v6.verify_policy_hash(policy)

            # This is the current descriptor-bound v2 full census, not a historical
            # receipt-only estimate. It must exactly reproduce all receipt roots/hashes.
            current_inventory = inventory_v2.build_usage_inventory(guarded_writer)
            _assert_inventory_matches_receipt(current_inventory, receipt)
            observed_ns = receipt["observed_ns"]
            now = wall_ns()
            _require(type(now) is int and now > observed_ns, "v6_bootstrap_clock_before_census")
            _check_prior_caps(
                [
                    {
                        "intent_ns": row["decision_ns"],
                        "raw_bytes_upper_bound": row["maximum_response_bytes"],
                        "physical_bytes_upper_bound": policy_v6.PHYSICAL_RESERVE_PER_INTENT,
                    }
                    for row in receipt["attempts"]
                ],
                now_ns=now,
            )
            census_sha = sha256(receipt_raw)
            # Each source-pinned successor selects exactly one immutable control
            # namespace. The prior v6 control pair remains untouched and unread.
            release_id = host_v6.CONTROL_RELEASE_ID
            service_root = f"{capture_v6.ROOT_PREFIX}/{release_id}"
            raw_anchor = _anchor_after(now, observed_ns, margin_ns=ANCHOR_MARGIN_NS)

            # Exact existing activation is only an idempotent replay; a different
            # activation permanently blocks this fixed v6 control namespace.
            activation_existing: bytes | None = None
            try:
                activation_existing = guarded_writer.read_bytes(
                    host_v6.ACTIVATION_PATH, max_bytes=128 * 1024
                )
            except FileNotFoundError:
                pass
            if activation_existing is not None:
                parsed_existing = json.loads(activation_existing.decode("utf-8"))
                _require(
                    type(parsed_existing) is dict
                    and parsed_existing.get("release_id") == release_id,
                    "v6_bootstrap_activation_conflict",
                )

            # A deterministic release root permits only the same immutable partial
            # prefix to resume. The first durable epoch fixes its anchor.
            try:
                entry = guarded_writer.stat(service_root)
                _require(stat.S_ISDIR(entry.st_mode), "v6_bootstrap_service_root_type")
                service_exists = True
            except FileNotFoundError:
                service_exists = False
            if not service_exists:
                revalidate()
                guarded_writer.mkdir(service_root)
                revalidate()
            service_fd = host_v6._open_private_directory(guarded_writer.root_fd, service_root)
            storage = host_v6.storage_identity(
                guard, guarded_writer.root_fd, service_fd, service_root
            )
            service_storage = storage

            def revalidate_service() -> None:
                revalidate()
                current = host_v6.storage_identity(
                    guard, guarded_writer.root_fd, service_fd, service_root
                )
                _require(current == service_storage, "v6_bootstrap_service_identity_changed")

            service_writer = DescriptorWriter(
                service_fd, before_write=revalidate_service, fault_hook=fault_hook
            )
            prior_epoch: dict[str, Any] | None = None
            try:
                prior_epoch_raw = service_writer.read_bytes(
                    capture_v6.EPOCH_PATH, max_bytes=256 * 1024
                )
            except FileNotFoundError:
                anchor_close_ns = raw_anchor
            else:
                prior_epoch = json.loads(prior_epoch_raw.decode("utf-8"))
                capture_v6.verify_epoch(prior_epoch)
                _require(
                    prior_epoch.get("release_id") == release_id
                    and prior_epoch.get("storage") == storage
                    and prior_epoch.get("prior_usage_census", {}).get("receipt_sha256")
                    == census_sha
                    and prior_epoch.get("first_bucket_close_ns") > now + ANCHOR_MARGIN_NS,
                    "v6_bootstrap_partial_epoch_conflict_or_expired",
                )
                anchor_close_ns = prior_epoch["first_bucket_close_ns"]

            prior_binding = {
                "receipt_path": host_v6.PRIOR_USAGE_CENSUS_PATH,
                "receipt_sha256": census_sha,
                "root_inventory_sha256": receipt["root_inventory_sha256"],
                "attempt_count": receipt["attempt_count"],
                "observed_ns": observed_ns,
            }
            prior_path = f"{PRIOR_USAGE_DIR}/{census_sha}.json"
            prior_rows = host_v6.verify_prior_usage_census(
                receipt_raw,
                source_manifest=source_manifest,
                epoch={
                    "first_bucket_close_ns": anchor_close_ns,
                    "prior_usage_census": prior_binding,
                },
                storage=storage,
                repo_root=repo_root,
            )
            prior_usage_raw = canonical(prior_rows)
            selector_sha = source_manifest["source_map"][
                "tools/brain_senses_gecko_rotating_scout_v1.py"
            ]
            epoch = capture_v6.epoch_document(
                release_id=release_id,
                anchor_close_ns=anchor_close_ns,
                policy_sha256=policy["policy_sha256"],
                selector_sha256=selector_sha,
                population_sha256=policy_v6.POPULATION_SHA256,
                storage=storage,
                prior_usage_path=prior_path,
                prior_usage_sha256=sha256(prior_usage_raw),
                prior_usage_census=prior_binding,
            )
            if prior_epoch is not None:
                _require(epoch == prior_epoch, "v6_bootstrap_epoch_rebuild_mismatch")

            # Store the source binding before the service journal; activation remains
            # the last create-only artifact in the whole transaction.
            source_manifest_raw = canonical(source_manifest)
            guarded_writer.mkdir(host_v6.CONTROL_RELEASE_DIRECTORY)
            _write_identical_or_create(
                guarded_writer,
                host_v6.SOURCE_MANIFEST_PATH,
                source_manifest_raw,
                revalidate=revalidate,
            )

            if fault_hook is not None:
                fault_hook("before_service_initialize")
            try:
                capture_v6.initialize_existing_root_v6(
                    service_writer,
                    epoch=epoch,
                    policy=policy,
                    selector_sha256=selector_sha,
                    prior_usage_bytes=prior_usage_raw,
                    expected_storage=storage,
                    observed_storage=lambda: host_v6.storage_identity(
                        guard, guarded_writer.root_fd, service_fd, service_root
                    ),
                    revalidate_storage=revalidate,
                )
            except capture_v6.CaptureV6Error as exc:
                raise BootstrapError("v6_bootstrap_service_initialize") from exc
            # The local verifier requires the exact persistent lock file. Taking
            # this lock create-only establishes it without dispatch authority.
            with service_writer.lock(".lane.lock", blocking=False):
                revalidate()
            source_inputs = {
                name: guarded_writer.read_bytes(path, max_bytes=4 * 1024 * 1024)
                for name, (path, _digest) in host_v6.SOURCE_INPUTS.items()
            }
            _require(
                all(
                    sha256(source_inputs[name]) == expected
                    for name, (_path, expected) in host_v6.SOURCE_INPUTS.items()
                ),
                "v6_bootstrap_source_input_changed",
            )
            # Verify the exact v2 receipt once more against the now-final epoch.
            rederived_rows = host_v6.verify_prior_usage_census(
                receipt_raw,
                source_manifest=source_manifest,
                epoch=epoch,
                storage=storage,
                repo_root=repo_root,
            )
            _require(rederived_rows == prior_rows, "v6_bootstrap_prior_rows_changed")
            try:
                free_after = guard.revalidate(
                    min_free_bytes=policy_v6.MIN_FREE_BYTES
                ).available_bytes
            except Exception as exc:
                raise BootstrapError("v6_bootstrap_storage_preflight") from exc
            all_bytes = sum(
                service_writer.stat(path).st_size for path in service_writer.list_files()
            )
            report = capture_v6.verify_local_journal(
                service_writer,
                epoch=epoch,
                policy=policy,
                selector=scout.plan_bucket,
                selector_sha256=selector_sha,
                expected_storage=storage,
                observed_storage=lambda: host_v6.storage_identity(
                    guard, guarded_writer.root_fd, service_fd, service_root
                ),
                now_ns=now,
                inherited_usage_rows=prior_rows,
                resource_snapshot={"free_bytes": free_after, "resident_bytes": all_bytes},
            )
            _require(
                report.get("plan_count") == 0
                and report.get("provider_io") is False
                and report.get("market_source_qualified") is False
                and report.get("training_admission") is False
                and report.get("economic_admission") is False,
                "v6_bootstrap_independent_replay",
            )

            activation_id = sha256(
                canonical(
                    {
                        "schema": host_v6.ACTIVATION_SCHEMA,
                        "release_id": release_id,
                        "epoch_sha256": epoch["epoch_sha256"],
                        "source_manifest_sha256": source_manifest["source_manifest_sha256"],
                    }
                )
            )[:32]
            activation_core = {
                "schema": host_v6.ACTIVATION_SCHEMA,
                "schema_version": 6,
                "activation_id": activation_id,
                "source_commit": source_manifest["source_commit"],
                "source_manifest_sha256": source_manifest["source_manifest_sha256"],
                "policy_sha256": policy["policy_sha256"],
                "selector_sha256": selector_sha,
                "population_sha256": policy_v6.POPULATION_SHA256,
                "release_id": release_id,
                "service_root": service_root,
                "epoch_sha256": epoch["epoch_sha256"],
                "storage": storage,
                "plist_sha256": source_manifest["source_map"][host_v6.PLIST_PATH],
                "label": host_v6.LABEL,
                "program_arguments": [
                    str(repo_root / ".venv/bin/python"),
                    "-B",
                    str(repo_root / "tools/brain_senses_gecko_capture_only_v6_host.py"),
                    "--scheduled-tick",
                ],
                "capture_authorized": True,
                "market_source_qualified": False,
                "training_admission": False,
                "economic_admission": False,
            }
            activation = {
                **activation_core,
                "activation_sha256": sha256(canonical(activation_core)),
            }
            host_v6.verify_activation_record(
                activation,
                source_manifest=source_manifest,
                policy=policy,
                storage=storage,
                plist_sha256=source_manifest["source_map"][host_v6.PLIST_PATH],
                epoch=epoch,
                repo_root=repo_root,
            )
            if fault_hook is not None:
                fault_hook("before_activation")
            _write_identical_or_create(
                guarded_writer,
                host_v6.ACTIVATION_PATH,
                canonical(activation),
                revalidate=revalidate,
            )
            if fault_hook is not None:
                fault_hook("after_activation")
            return {
                "schema": SCHEMA,
                "schema_version": SCHEMA_VERSION,
                "release_id": release_id,
                "activation_id": activation_id,
                "service_root": service_root,
                "anchor_close_ns": anchor_close_ns,
                "attempt_count": receipt["attempt_count"],
                "raw_bytes_upper_bound": receipt["raw_bytes_upper_bound"],
                "physical_bytes_upper_bound": receipt["physical_bytes_upper_bound"],
                "source_manifest_sha256": source_manifest["source_manifest_sha256"],
                "activation_sha256": activation["activation_sha256"],
                "provider_io": False,
                "launchd_action": False,
            }
    except (host_v6.HostV6Error, usage_inventory_v2.InventoryError, OSError) as exc:
        raise BootstrapError("v6_bootstrap_prerequisite_failed") from exc
    finally:
        if service_writer is not None:
            service_writer.close()
        if service_fd >= 0:
            os.close(service_fd)
        guarded_writer.close()


__all__ = ["BootstrapError", "prepare_v6_release"]
