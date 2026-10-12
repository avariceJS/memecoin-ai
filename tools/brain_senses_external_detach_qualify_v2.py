#!/usr/bin/env python3
"""Qualify the v2 launch-to-descriptor-writer detach boundary on disposable APFS.

This is an offline fault-injection qualification. It uses a sparse APFS image
under /private/tmp and never contacts a provider. The receipt distinguishes the
disposable volume used by the test from any future lease storage binding.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memecoin_research_storage import StorageError, configure, create_root, writer_guard
from tools import brain_senses_external_clock_v1 as clock
from tools import brain_senses_external_launch_v2 as launch
from tools import brain_senses_external_orchestrator_v2 as engine
from tools import brain_senses_external_schema_v2 as schema
from tools import brain_senses_external_verify_v2 as verify
from tools.brain_senses_external_writer_v1 import DescriptorWriter
from tools.research_storage_detach_qualify_v1 import (
    ImageVolumeProbe,
    MountedImage,
    _assert_underlay_unchanged,
    _disk_info,
    _run,
    _seed_trap,
)

REPO = Path(__file__).resolve().parents[1]
RECEIPT_PATH = (
    "docs/evidence/external-v2-descriptor-detach-integration-qualification-2026-10-12-v2.json"
)
KINGSTON_UUID = "8f8f1081-9be4-4914-9111-75f1b90255bf"
DATA_ROOT = "ResearchData"
V1_SERVICE = "services/v1-detach-fixture"
V2_SERVICE = "services/v2-detach-fixture"
_MINUTE_NS = 60_000_000_000


class QualificationFailure(RuntimeError):
    """A bounded, sanitized qualification failure."""


def canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_disposable_fs_type(actual: str) -> str:
    if actual.strip().casefold() != "case-sensitive apfs":
        raise QualificationFailure("disposable_filesystem_not_case_sensitive_apfs")
    return "case-sensitive APFS"


def _publish_receipt(
    receipt: dict[str, Any],
    *,
    initial_harness: bytes,
    current_harness: bytes,
    output: Path | None,
) -> None:
    """Publish only while the exact harness bytes used at start remain in place."""
    initial_digest = sha(initial_harness)
    if current_harness != initial_harness or sha(current_harness) != initial_digest:
        raise QualificationFailure("harness_source_changed_during_qualification")
    if receipt.get("tested_disposable_source_sha256") != initial_digest:
        raise QualificationFailure("harness_source_pin_mismatch")
    validate_receipt(receipt)
    if output is not None:
        _write_receipt(output, receipt)


def _read_private_plan(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        entry = os.fstat(fd)
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_uid != os.getuid()
            or stat.S_IMODE(entry.st_mode) != 0o600
            or entry.st_nlink != 1
            or entry.st_size > 4_000_000
        ):
            raise QualificationFailure("lease_plan_file_identity")
        chunks: list[bytes] = []
        remaining = entry.st_size
        while remaining:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                raise QualificationFailure("lease_plan_short_read")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(fd)
        if (entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise QualificationFailure("lease_plan_changed")
        return b"".join(chunks)
    finally:
        os.close(fd)


def validate_receipt(receipt: Any) -> None:
    """Validate the fixed gate contract without accepting partial evidence."""
    required = {
        "schema",
        "schema_version",
        "status",
        "provider_requests",
        "head",
        "source_sha256",
        "writer_source_sha256",
        "mount_identity",
        "descriptor_path",
        "storage",
        "tested_disposable_identity",
        "test_only_ownership_normalization",
        "tested_disposable_source",
        "tested_disposable_source_sha256",
        "capacity_override_test_only",
        "capacity_qualified",
        "actual_disposable_free_bytes",
        "requested_live_min_free_bytes",
        "applied_min_free_bytes",
        "capacity_override_call_count",
        "real_capacity_precheck_rejected",
        "configured_kingston_touched",
        "forced_detach_points",
        "underlay_manifest_sha256",
        "fallback_artifacts",
        "underlay_manifest_unchanged",
        "cleanup_verified",
        "temporary_root_removed",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise QualificationFailure("receipt_fields")
    if (
        receipt["schema"] != "external-v2-descriptor-detach-integration-qualification"
        or receipt["schema_version"] != 2
        or receipt["status"] != "passed"
        or receipt["provider_requests"] != 0
        or not isinstance(receipt["head"], str)
        or len(receipt["head"]) != 40
        or not isinstance(receipt["source_sha256"], dict)
        or not receipt["source_sha256"]
        or receipt["writer_source_sha256"] != receipt["source_sha256"]
        or not isinstance(receipt["mount_identity"], dict)
        or set(receipt["mount_identity"]) != {"root_id", "volume_uuid"}
        or not isinstance(receipt["descriptor_path"], str)
        or not receipt["descriptor_path"].startswith("services/")
        or not isinstance(receipt["storage"], dict)
        or receipt["mount_identity"].get("root_id") != receipt["storage"].get("root_id")
        or receipt["mount_identity"].get("volume_uuid") != receipt["storage"].get("volume_uuid")
        or receipt["descriptor_path"] != receipt["storage"].get("relative_root")
        or not isinstance(receipt["tested_disposable_identity"], dict)
        or set(receipt["tested_disposable_identity"])
        != {
            "volume_uuid",
            "mount_path",
            "fs_type",
            "case_sensitive",
            "owners_enabled",
            "root_id",
            "relative_root",
        }
        or receipt["tested_disposable_identity"]["fs_type"] != "case-sensitive APFS"
        or receipt["tested_disposable_identity"]["case_sensitive"] is not True
        or receipt["tested_disposable_identity"]["volume_uuid"]
        == receipt["mount_identity"].get("volume_uuid")
        or receipt["tested_disposable_identity"]["volume_uuid"] == KINGSTON_UUID
        or not receipt["tested_disposable_identity"]["mount_path"].startswith("/private/tmp/")
        or type(receipt["tested_disposable_identity"]["owners_enabled"]) is not bool
        or not receipt["tested_disposable_identity"]["root_id"]
        or receipt["tested_disposable_identity"]["relative_root"] != V2_SERVICE
        or receipt["capacity_override_test_only"] is not True
        or receipt["capacity_qualified"] is not False
        or type(receipt["actual_disposable_free_bytes"]) is not int
        or type(receipt["requested_live_min_free_bytes"]) is not int
        or receipt["applied_min_free_bytes"] != 0
        or type(receipt["capacity_override_call_count"]) is not int
        or receipt["capacity_override_call_count"] <= 0
        or receipt["real_capacity_precheck_rejected"] is not True
        or receipt["test_only_ownership_normalization"] is not True
        or receipt["tested_disposable_source"] != "tools/brain_senses_external_detach_qualify_v2.py"
        or not isinstance(receipt["tested_disposable_source_sha256"], str)
        or len(receipt["tested_disposable_source_sha256"]) != 64
        or receipt["configured_kingston_touched"] is not False
        or not isinstance(receipt["forced_detach_points"], list)
        or not receipt["forced_detach_points"]
        or receipt["fallback_artifacts"] != []
        or not isinstance(receipt["underlay_manifest_sha256"], str)
        or len(receipt["underlay_manifest_sha256"]) != 64
        or receipt["underlay_manifest_unchanged"] is not True
        or receipt["cleanup_verified"] is not True
        or receipt["temporary_root_removed"] is not True
    ):
        raise QualificationFailure("receipt_gate_contract")
    for point in receipt["forced_detach_points"]:
        if (
            not isinstance(point, dict)
            or point.get("underlay_unchanged") is not True
            or point.get("no_retry_or_replay") is not True
            or point.get("provider_loader_calls") != 0
            or point.get("fallback_artifacts") != []
            or point.get("safe_syscall_failure") is not True
        ):
            raise QualificationFailure("detach_point_evidence")


def _new_image(base: Path) -> tuple[MountedImage, dict[str, tuple[str, int]]]:
    mountpoint = base / "mount"
    mountpoint.mkdir(mode=0o700)
    (base / "config").mkdir(mode=0o700)
    image = MountedImage(
        base,
        mountpoint,
        base / "external-v2-detach.sparseimage",
        base / "config" / "storage.json",
    )
    baseline = _seed_trap(image)
    _run(
        [
            "/usr/bin/hdiutil",
            "create",
            "-size",
            "100g",
            "-fs",
            "Case-sensitive APFS",
            "-volname",
            "BrainV2DetachQualification",
            "-type",
            "SPARSE",
            str(image.image),
        ],
        timeout=90,
    )
    image.attach()
    try:
        if image.volume_uuid == KINGSTON_UUID:
            raise QualificationFailure("configured_kingston_uuid_collision")
        image.probe = ImageVolumeProbe(str(image.volume_uuid))
        cfg = configure(
            image.config,
            image.mountpoint,
            DATA_ROOT,
            min_free_bytes=0,
            volume_uuid=image.volume_uuid,
            probe=image.probe,
        )
        if cfg.volume_uuid != image.volume_uuid:
            raise QualificationFailure("temporary_config_identity")
        create_root(image.config, probe=image.probe)
        with writer_guard(image.config, probe=image.probe) as guard:
            guard.create_directory(V1_SERVICE)
            guard.create_directory(V2_SERVICE)
            v1_fd = launch.open_service(guard, V1_SERVICE)
            try:

                def before_write() -> None:
                    guard.revalidate()

                with DescriptorWriter(v1_fd, before_write=before_write) as writer:
                    writer.create(".writer.lock", b"read-only predecessor lock\n")
            finally:
                os.close(v1_fd)
    except BaseException:
        if os.path.ismount(image.mountpoint):
            image.detach()
        raise
    return image, baseline


def _synthetic_plan(root_id: str, volume_uuid: str) -> dict[str, Any]:
    candidate_path = REPO / "docs/evidence/external-v1-20261011T2133Z-successor-v2-candidate.json"
    candidate = json.loads(candidate_path.read_bytes())
    cert = candidate["predecessor"]
    cert["service_id"] = "v1-detach-fixture"
    cert["storage"] = {
        "root_id": root_id,
        "volume_uuid": volume_uuid,
        "relative_root": V1_SERVICE,
    }
    candidate["predecessor_sha256"] = sha(canonical(cert))
    candidate["predecessor"] = cert
    now = time.time_ns()
    start = ((now + 3 * _MINUTE_NS + _MINUTE_NS - 1) // _MINUTE_NS) * _MINUTE_NS
    if start < cert["expires_ns"] + _MINUTE_NS:
        start = ((cert["expires_ns"] + 2 * _MINUTE_NS) // _MINUTE_NS) * _MINUTE_NS
    return engine.build_plan(
        candidate=candidate,
        validation_sha256="1" * 64,
        completion_sha256="2" * 64,
        service_id="v2-detach-fixture",
        root_id=root_id,
        volume_uuid=volume_uuid,
        bootstrap_start_ns=start,
        clock_preflight_ns=now,
        endpoint_origin="https://mainnet.helius-rpc.com",
    )


def _initial_state(plan: dict[str, Any], writer: DescriptorWriter) -> dict[str, Any]:
    events = verify.verify_events(writer.read_bytes(schema.EVENTS_FILE))
    reservation: dict[str, Any] = {}
    bucket_rows: dict[str, Any] = {}
    for event in events:
        if event["kind"] == "reservation" and event["bucket"]:
            reservation[event["bucket"]] = {"phase": "bootstrap"}
            bucket_rows[event["bucket"]] = {"status": "interrupted_no_replay"}
    return {
        "stop": None,
        "predecessor_lineage_verified": True,
        "reservations": reservation,
        "sealed": {},
        "requests": 0,
        "predecessor_day_usage": [],
        "predecessor_day_usage_sha256": sha(canonical([])),
        "predecessor_day_usage_verified": True,
        "v2_day_usage": {},
        "v2_stored_bytes": 0,
        "v2_raw_bytes": 0,
        "event_tail_sha256": events[-1]["event_sha256"] if events else "0" * 64,
        "buckets": bucket_rows,
        "missed": set(),
        "plan": plan,
        "status": "verified_prefix",
    }


def _make_clock_record() -> dict[str, Any]:
    from datetime import UTC, datetime

    wall = time.time_ns()
    mono = time.monotonic_ns()
    date = datetime.fromtimestamp(wall // 1_000_000_000, UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    samples = []
    for host in clock.HOSTS:
        samples.append(
            {
                "host": host,
                "status": 200,
                "date": date,
                "age": None,
                "sent_ns": wall,
                "received_ns": wall,
                "monotonic_sent_ns": mono,
                "monotonic_received_ns": mono,
            }
        )
    return {
        "schema": clock.CLOCK_SCHEMA,
        "schema_version": 1,
        "samples": samples,
        "checked_ns": wall,
        "monotonic_ns": mono,
    }


def _run_scenario(base: Path, trigger: str, occurrence: int, *, replay: bool) -> dict[str, Any]:
    image, baseline = _new_image(base)
    provider_loader_calls = 0
    detached = False
    operation_count = 0
    forced_points: list[dict[str, Any]] = []
    plan: dict[str, Any] | None = None
    activation: dict[str, Any] | None = None
    capacity: dict[str, Any] = {
        "actual_disposable_free_bytes": 0,
        "requested_live_min_free_bytes": 0,
        "applied_min_free_bytes": 0,
        "capacity_override_call_count": 0,
        "real_capacity_precheck_rejected": False,
    }
    try:
        # Exercise StorageGuard.create_directory's post-revalidation, pre-mkdir
        # gap on this same disposable mount before invoking the v2 controller.
        with writer_guard(image.config, probe=image.probe) as mkdir_guard:
            dynamic_guard = cast(Any, mkdir_guard)
            original_revalidate = mkdir_guard.revalidate
            revalidated_at: list[int] = []

            def counted_revalidate(*args: Any, **kwargs: Any) -> Any:
                result = original_revalidate(*args, **kwargs)
                revalidated_at.append(time.monotonic_ns())
                return result

            dynamic_guard.revalidate = counted_revalidate
            original_mkdir = os.mkdir
            mkdir_detached = False
            mkdir_failure: str | None = None

            def detach_before_mkdir(
                path: str | bytes,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> None:
                nonlocal mkdir_detached, mkdir_failure
                if path == "detach-gap" and not mkdir_detached:
                    if (
                        not revalidated_at
                        or time.monotonic_ns() - revalidated_at[-1] > 1_000_000_000
                    ):
                        raise QualificationFailure("mkdir_detach_not_after_revalidation")
                    image.detach()
                    mkdir_detached = True
                    _assert_underlay_unchanged(image, baseline, context="guard_mkdir_hook")
                try:
                    original_mkdir(path, mode, dir_fd=dir_fd)
                except OSError as exc:
                    if path == "detach-gap":
                        mkdir_failure = type(exc).__name__
                    raise

            os.mkdir = detach_before_mkdir  # type: ignore[assignment]
            try:
                try:
                    mkdir_guard.create_directory("services/detach-gap")
                except Exception as exc:
                    mkdir_failure = type(exc).__name__
            finally:
                os.mkdir = original_mkdir
                dynamic_guard.revalidate = original_revalidate
            if not mkdir_detached or mkdir_failure not in {"OSError", "StorageError"}:
                raise QualificationFailure("guard_mkdir_detach_not_safe")
        _assert_underlay_unchanged(image, baseline, context="guard_mkdir_after")
        image.attach()
        forced_points.append(
            {
                "name": "storage_guard_mkdir_after_revalidation",
                "operation": "mkdir",
                "outcome_class": mkdir_failure,
                "safe_syscall_failure": True,
                "detached_after_successful_revalidation": mkdir_detached,
                "underlay_unchanged": True,
                "no_retry_or_replay": True,
                "replay_classification": "not_attempted",
                "provider_loader_calls": 0,
                "fallback_artifacts": [],
                "temporary_uuid": image.volume_uuid,
                "temporary_mount_path": str(image.mountpoint),
                "tested_disposable_volume_uuid": image.volume_uuid,
                "tested_disposable_mount_path": str(image.mountpoint),
                "temporary_root_id": mkdir_guard.root_id,
                "temporary_relative_root": V2_SERVICE,
                "temporary_fs_type": str(_disk_info(image.mountpoint).get("FilesystemName", "")),
                "temporary_owners_enabled": bool(image.owners_enabled),
                "underlay_manifest_sha256": sha(canonical(sorted(baseline.items()))),
            }
        )
        with writer_guard(image.config, probe=image.probe) as guard:
            v2_fd = launch.open_service(guard, V2_SERVICE)
            try:
                plan = _synthetic_plan(guard.root_id, guard.volume_uuid)
                plan_raw = canonical(plan)
                activation = {
                    "plan_sha256": sha(plan_raw),
                    "storage_config_sha256": "0" * 64,
                }

                def before_write() -> None:
                    guard.revalidate()

                with DescriptorWriter(v2_fd, before_write=before_write) as seed:
                    seed.create(schema.PLAN_FILE, plan_raw)
                    seed.create(schema.EVENTS_FILE, b"")
                    engine._append_event(
                        seed,
                        kind="initialized",
                        bucket=None,
                        at_ns=plan["schedule"]["clock_preflight_ns"],
                        payload={
                            "plan_sha256": sha(plan_raw),
                            "predecessor_day_usage": [],
                            "predecessor_day_usage_sha256": sha(canonical([])),
                        },
                    )
            finally:
                os.close(v2_fd)

        if plan is None or activation is None:
            raise QualificationFailure("synthetic_plan_setup")
        fixed_now = plan["schedule"]["bootstrap"]["start_ns"] + 10_000_000_000

        class HookedDescriptorWriter(DescriptorWriter):
            def __init__(self, root_fd: int, *, before_write: Any = None) -> None:
                def hook(operation: str) -> None:
                    nonlocal operation_count, detached
                    if operation != trigger:
                        return
                    operation_count += 1
                    if operation_count == occurrence:
                        if not os.path.ismount(image.mountpoint):
                            raise QualificationFailure("unexpected_unmounted_before_hook")
                        image.detach()
                        detached = True
                        _assert_underlay_unchanged(
                            image, baseline, context=f"{trigger}:{occurrence}"
                        )

                super().__init__(root_fd, before_write=before_write, fault_hook=hook)

        launch_module = cast(Any, launch)
        old_writer = launch_module.DescriptorWriter
        old_gate = launch.activation_gate
        old_verify = verify.verify
        clock_mod = cast(
            Any,
            __import__("tools.brain_senses_external_clock_v1", fromlist=["probe_pair"]),
        )
        old_probe = clock_mod.probe_pair
        original_loader = launch._client
        original_wall = time.time_ns
        original_mono = time.monotonic_ns
        wall_offset = fixed_now - original_wall()
        time.time_ns = lambda: original_wall() + wall_offset
        time.monotonic_ns = lambda: original_mono() + wall_offset

        def fake_verify(reader: Any, **_: Any) -> dict[str, Any]:
            with DescriptorWriter(reader.fd) as read_writer:
                return _initial_state(plan, read_writer)

        def fake_gate(*_: Any, **__: Any) -> tuple[dict[str, Any], dict[str, Any]]:
            return activation, plan

        def forbidden_client(*_: Any, **__: Any) -> Any:
            nonlocal provider_loader_calls
            provider_loader_calls += 1
            raise QualificationFailure("provider_loader_forbidden")

        @contextlib.contextmanager
        def disposable_guard(config_path: Path, *, min_free_bytes: int) -> Any:
            capacity["requested_live_min_free_bytes"] = min_free_bytes
            with writer_guard(config_path, min_free_bytes=0, probe=image.probe) as real_guard:
                capacity["actual_disposable_free_bytes"] = real_guard.available_bytes
                try:
                    real_guard.revalidate(min_free_bytes=min_free_bytes)
                except StorageError as exc:
                    if str(exc) != "insufficient_free_space":
                        raise
                    capacity["real_capacity_precheck_rejected"] = True
                else:
                    raise QualificationFailure("disposable_live_capacity_unexpectedly_passed")

                class CapacityOverrideGuard:
                    def __getattr__(self, name: str) -> Any:
                        return getattr(real_guard, name)

                    def revalidate(self, *, min_free_bytes: int | None = None) -> Any:
                        capacity["capacity_override_call_count"] += 1
                        capacity["applied_min_free_bytes"] = 0
                        return real_guard.revalidate(min_free_bytes=0)

                yield CapacityOverrideGuard()

        launch_module.DescriptorWriter = HookedDescriptorWriter
        launch.activation_gate = fake_gate
        verify.verify = fake_verify
        clock_mod.probe_pair = _make_clock_record
        launch._client = forbidden_client
        try:
            failure_class: str | None
            run_result: dict[str, Any] | None = None
            safe_syscall_failure = False
            try:
                run_result = launch.execute(
                    "run",
                    activation_path=base / "activation.json",
                    config_path=image.config,
                    now_ns=fixed_now,
                    guard_factory=disposable_guard,
                )
            except Exception as exc:
                # Do not serialize exception text; mount errors can contain paths.
                failure_class = type(exc).__name__
                safe_syscall_failure = isinstance(exc, (OSError, StorageError))
                failure_reason = str(exc)
            else:
                failure_class = None
                failure_reason = ""
            if not detached:
                status = failure_class if run_result is None else str(run_result.get("status"))
                raise QualificationFailure(
                    f"detach_hook_not_reached:{trigger}:{occurrence}:{status}:{failure_reason}"
                )
            if os.path.ismount(image.mountpoint):
                image.detach()
            _assert_underlay_unchanged(image, baseline, context="post-operation")

            replay_status = "not_attempted"
            if replay:
                image.attach()
                retry_state = launch.execute(
                    "run",
                    activation_path=base / "activation.json",
                    config_path=image.config,
                    now_ns=fixed_now,
                    guard_factory=disposable_guard,
                )
                replay_status = (
                    "reservation_spent"
                    if plan["schedule"]["bootstrap"]["bucket"]
                    in retry_state.get("reservations", {})
                    or retry_state.get("status") == "interrupted_no_replay"
                    else "no_reservation"
                )
                if replay_status != "reservation_spent":
                    raise QualificationFailure("replay_scenario_missing_spent_reservation")
            if provider_loader_calls != 0:
                raise QualificationFailure("provider_loader_called")
            forced_points.append(
                {
                    "name": f"{trigger}_{occurrence}",
                    "operation": trigger,
                    "occurrence": occurrence,
                    "outcome_class": failure_class,
                    "safe_syscall_failure": safe_syscall_failure,
                    "detached_after_successful_revalidation": detached,
                    "underlay_unchanged": True,
                    "no_retry_or_replay": replay_status in {"not_attempted", "reservation_spent"},
                    "replay_classification": replay_status,
                    "provider_loader_calls": provider_loader_calls,
                    "fallback_artifacts": [],
                    "temporary_uuid": image.volume_uuid,
                    "temporary_mount_path": str(image.mountpoint),
                    "tested_disposable_volume_uuid": image.volume_uuid,
                    "tested_disposable_mount_path": str(image.mountpoint),
                    "temporary_root_id": plan["storage"]["root_id"],
                    "temporary_relative_root": plan["storage"]["relative_root"],
                    "temporary_fs_type": str(
                        _disk_info(image.mountpoint).get("FilesystemName", "")
                    ),
                    "temporary_owners_enabled": bool(image.owners_enabled),
                    "underlay_manifest_sha256": sha(canonical(sorted(baseline.items()))),
                }
            )
            return {
                "forced_points": forced_points,
                "temporary_uuid": image.volume_uuid,
                "temporary_mount_path": str(image.mountpoint),
                "temporary_root_id": plan["storage"]["root_id"],
                "temporary_relative_root": plan["storage"]["relative_root"],
                "temporary_fs_type": str(_disk_info(image.mountpoint).get("FilesystemName", "")),
                "temporary_owners_enabled": bool(image.owners_enabled),
                "underlay_manifest_sha256": sha(canonical(sorted(baseline.items()))),
                **capacity,
            }
        finally:
            launch_module.DescriptorWriter = old_writer
            launch.activation_gate = old_gate
            verify.verify = old_verify
            clock_mod.probe_pair = old_probe
            launch._client = original_loader
            time.time_ns = original_wall
            time.monotonic_ns = original_mono
    finally:
        if os.path.ismount(image.mountpoint):
            image.detach()


def run_qualification(*, lease_plan_path: Path, output: Path | None = None) -> dict[str, Any]:
    harness_path = Path(__file__)
    initial_harness = harness_path.read_bytes()
    harness_sha256 = sha(initial_harness)
    if sys.platform != "darwin" or not Path("/usr/bin/hdiutil").exists():
        raise QualificationFailure("darwin_hdiutil_required")
    temp_parent = Path("/private/tmp")
    if not temp_parent.is_dir():
        raise QualificationFailure("private_tmp_missing")
    initial_sources = engine.source_bindings()
    lease_plan_raw = _read_private_plan(lease_plan_path)
    lease_plan = verify.decode(lease_plan_raw)
    verify.verify_plan(lease_plan, expected_sources=initial_sources)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()
    if len(head) != 40:
        raise QualificationFailure("head_identity_invalid")
    points = []
    with tempfile.TemporaryDirectory(prefix="brain-v2-detach-qualify-", dir=temp_parent) as raw:
        root = Path(raw)
        root.chmod(0o700)
        point = _run_scenario(root, "atomic_rename", 2, replay=True)
        points.extend(point["forced_points"])
    if engine.source_bindings() != initial_sources:
        raise QualificationFailure("source_map_changed_during_qualification")
    receipt = {
        "schema": "external-v2-descriptor-detach-integration-qualification",
        "schema_version": 2,
        "status": "passed",
        "provider_requests": 0,
        "head": head,
        "source_sha256": initial_sources,
        "writer_source_sha256": initial_sources,
        "mount_identity": {
            "root_id": lease_plan["storage"]["root_id"],
            "volume_uuid": lease_plan["storage"]["volume_uuid"],
        },
        "descriptor_path": lease_plan["storage"]["relative_root"],
        "storage": lease_plan["storage"],
        "tested_disposable_identity": {
            "volume_uuid": point["temporary_uuid"],
            "mount_path": point["temporary_mount_path"],
            "fs_type": _canonical_disposable_fs_type(point["temporary_fs_type"]),
            "case_sensitive": (
                point["temporary_fs_type"].strip().casefold() == "case-sensitive apfs"
            ),
            "owners_enabled": point["temporary_owners_enabled"],
            "root_id": point["temporary_root_id"],
            "relative_root": point["temporary_relative_root"],
        },
        "test_only_ownership_normalization": True,
        "tested_disposable_source": "tools/brain_senses_external_detach_qualify_v2.py",
        "tested_disposable_source_sha256": harness_sha256,
        "configured_kingston_touched": False,
        "forced_detach_points": points,
        "underlay_manifest_sha256": point["underlay_manifest_sha256"],
        "fallback_artifacts": [],
        "underlay_manifest_unchanged": True,
        "cleanup_verified": True,
        "temporary_root_removed": not root.exists(),
        "capacity_override_test_only": True,
        "capacity_qualified": False,
        "actual_disposable_free_bytes": point["actual_disposable_free_bytes"],
        "requested_live_min_free_bytes": point["requested_live_min_free_bytes"],
        "applied_min_free_bytes": point["applied_min_free_bytes"],
        "capacity_override_call_count": point["capacity_override_call_count"],
        "real_capacity_precheck_rejected": point["real_capacity_precheck_rejected"],
    }
    _publish_receipt(
        receipt,
        initial_harness=initial_harness,
        current_harness=harness_path.read_bytes(),
        output=output,
    )
    return receipt


def _write_receipt(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        raise QualificationFailure("receipt_already_exists")
    raw = canonical(value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("short receipt write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO / RECEIPT_PATH)
    parser.add_argument("--lease-plan", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="validate host prerequisites only")
    args = parser.parse_args(argv)
    if args.dry_run:
        if sys.platform != "darwin" or not Path("/usr/bin/hdiutil").exists():
            raise QualificationFailure("darwin_hdiutil_required")
        print("ready: disposable APFS image qualification; no image created")
        return 0
    result = run_qualification(lease_plan_path=args.lease_plan, output=args.output)
    print(
        json.dumps(
            {
                "status": result["status"],
                "forced_detach_points": len(result["forced_detach_points"]),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
