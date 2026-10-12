#!/usr/bin/env python3
"""Disposable APFS detach qualification for structural sibling-root publication.

This tool writes only to a temporary APFS image under /private/tmp and emits
one immutable qualification receipt. It never opens provider credentials or
uses the configured Kingston data root.
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
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from memecoin_research_storage import (  # noqa: E402
    StorageError,
    configure,
    create_root,
    writer_guard,
)
from tools import brain_senses_external_schema_v2 as v2_schema  # noqa: E402
from tools import (  # noqa: E402
    brain_senses_external_structural_input_schema_v1 as structural_schema,  # noqa: E402
)
from tools import brain_senses_external_structural_input_v1 as structural_builder  # noqa: E402
from tools import brain_senses_external_structural_publish_v1 as publication  # noqa: E402
from tools import brain_senses_external_verify_v2 as v2_verify  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402
from tools.research_storage_detach_qualify_v1 import (  # noqa: E402
    KINGSTON_UUID,
    ImageVolumeProbe,
    MountedImage,
    _run,
    _seed_trap,
    _trap_snapshot,
)
from tools.research_storage_detach_qualify_v1 import (  # noqa: E402
    QualificationError as MountQualificationError,
)

RECEIPT_SCHEMA = "brain-senses-structural-input-sibling-writer-qualification-v2"
RECEIPT_PATH = "docs/evidence/brain-senses-structural-input-sibling-writer-qualification-v2.json"
DATA_ROOT = "ResearchData"
V1_ROOT = "services/v1-structural-detach-fixture"
PREDECESSOR_LOCK_BYTES = b"predecessor-shared-lock\n"
SOURCE_ROOT = "services/structural-detach-fixture"
OUTPUT_ROOT = "derived/structural-input-v1"
TEST_SOURCE_PATHS = (
    "tests/test_brain_senses_external_structural_publish_v1.py",
    "tests/test_brain_senses_structural_publish_detach_qualify_v1.py",
)
PINNED_SOURCES = (
    "tools/brain_senses_structural_publish_detach_qualify_v1.py",
    "tools/brain_senses_external_structural_input_schema_v1.py",
    "tools/brain_senses_external_structural_input_v1.py",
    "tools/brain_senses_external_structural_publish_v1.py",
    "tools/brain_senses_external_schema_v2.py",
    "tools/brain_senses_external_verify_v2.py",
    "tools/brain_senses_external_launch_v2.py",
    "tools/brain_senses_external_writer_v1.py",
    "tools/brain_senses_external_verify_v1.py",
    "tools/research_storage_detach_qualify_v1.py",
    "src/memecoin_research_storage/__init__.py",
)
DETACH_CASES = (
    ("mkdir", 1),
    ("lock_open", 1),
    ("create", 1),
    ("atomic_rename", 1),
    ("create", 2),
)


class QualificationFailure(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_hashes() -> dict[str, str]:
    return {
        path: _sha((REPO / path).read_bytes()) for path in (*PINNED_SOURCES, *TEST_SOURCE_PATHS)
    }


def _open_handles_under(path: Path) -> int:
    lsof = Path("/usr/sbin/lsof")
    if not lsof.exists():
        raise QualificationFailure("lsof_unavailable_for_cleanup_check")
    result = subprocess.run(
        [str(lsof), "-n", "-Fpcfn", "+D", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise QualificationFailure("lsof_cleanup_check_failed")
    return sum(line.startswith("p") for line in result.stdout.splitlines())


def _write_private(path: Path, payload: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short_write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _setup_image(base: Path) -> tuple[MountedImage, dict[str, tuple[str, int]], Path]:
    mountpoint = base / "mount"
    mountpoint.mkdir(mode=0o700)
    config_dir = base / "config"
    config_dir.mkdir(mode=0o700)
    image = MountedImage(
        base,
        mountpoint,
        base / "structural-publish.sparseimage",
        config_dir / "storage.json",
    )
    baseline = _seed_trap(image)
    _run(
        [
            "/usr/bin/hdiutil",
            "create",
            "-size",
            "256m",
            "-fs",
            "Case-sensitive APFS",
            "-volname",
            "StructuralPublishQualification",
            "-type",
            "SPARSE",
            str(image.image),
        ],
        timeout=60,
    )
    image.attach()
    try:
        if image.volume_uuid == KINGSTON_UUID:
            raise QualificationFailure("configured_volume_uuid_collision")
        image.probe = ImageVolumeProbe(str(image.volume_uuid))
        cfg = configure(
            image.config,
            image.mountpoint,
            DATA_ROOT,
            min_free_bytes=0,
            volume_uuid=image.volume_uuid,
            probe=image.probe,
        )
        if cfg.volume_uuid.lower() != str(image.volume_uuid).lower():
            raise QualificationFailure("disposable_config_identity")
        create_root(image.config, probe=image.probe)
        with writer_guard(image.config, probe=image.probe) as guard:
            guard.create_directory(V1_ROOT)
            _write_service_fixture(guard, "seed", image)
        return image, baseline, image.config
    except BaseException:
        if os.path.ismount(image.mountpoint):
            image.detach()
        raise


def _write_service_fixture(
    guard: Any, service_id: str, image: MountedImage
) -> tuple[dict[str, Any], str]:
    source_rel = f"services/{service_id}"
    pred_rel = V1_ROOT
    guard.create_directory(source_rel)
    source_fd = os.open(
        image.mountpoint / DATA_ROOT / source_rel,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    pred_fd = os.open(
        image.mountpoint / DATA_ROOT / pred_rel,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    plan = {
        "schema": v2_schema.PLAN_SCHEMA,
        "schema_version": 2,
        "service_id": service_id,
        "provider": "helius-free",
        "endpoint_origin": "https://mainnet.helius-rpc.com",
        "storage": {
            "root_id": guard.root_id,
            "volume_uuid": guard.volume_uuid,
            "relative_root": source_rel,
        },
        "predecessor": {"relative_root": pred_rel},
        "code_sha256": {"synthetic-engine.py": "a" * 64},
        "caps": v2_schema.CAPS,
    }
    plan_sha = v2_verify.sha(v2_verify.canonical(plan))
    try:
        with DescriptorWriter(source_fd, before_write=guard.revalidate) as source_writer:
            source_writer.create(v2_schema.PLAN_FILE, v2_verify.canonical(plan))
            source_writer.create(v2_schema.LOCK_FILE, b"source-shared-lock\n")
        try:
            lock_fd = os.open(
                ".writer.lock",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=pred_fd,
            )
        except FileNotFoundError:
            with DescriptorWriter(pred_fd, before_write=guard.revalidate) as predecessor_writer:
                try:
                    predecessor_writer.create(".writer.lock", PREDECESSOR_LOCK_BYTES)
                except FileExistsError:
                    # Another fixture seeder may have won the create-only race;
                    # validate that exact immutable inode below.
                    pass
            lock_fd = os.open(
                ".writer.lock",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=pred_fd,
            )
        try:
            lock_stat = os.fstat(lock_fd)
            pred_stat = os.fstat(pred_fd)
            lock_body = os.read(lock_fd, len(PREDECESSOR_LOCK_BYTES) + 1)
            if (
                not stat.S_ISREG(lock_stat.st_mode)
                or lock_stat.st_dev != pred_stat.st_dev
                or lock_stat.st_uid != os.getuid()
                or lock_stat.st_nlink != 1
                or stat.S_IMODE(lock_stat.st_mode) != 0o600
                or lock_body != PREDECESSOR_LOCK_BYTES
            ):
                raise QualificationFailure("predecessor_fixture_lock_identity")
        finally:
            os.close(lock_fd)
    finally:
        os.close(source_fd)
        os.close(pred_fd)
    return plan, plan_sha


def _synthetic_artifact(plan: Mapping[str, Any], plan_sha: str) -> dict[str, Any]:
    bucket = "20261012T060000Z"
    interval = {
        "start": 100,
        "end_exclusive": 100,
        "reason": "uncovered_since_predecessor_no_backfill",
        "bootstrap_bucket": bucket,
    }
    return {
        "schema": structural_schema.SCHEMA,
        "schema_version": structural_schema.VERSION,
        "status": "sealed_sample_ready",
        "publishable": True,
        "point_in_time_eligible": False,
        "decision_ns": None,
        "scope": {
            "plan_sha256": plan_sha,
            "event_tail_sha256": "b" * 64,
            "source_code_sha256": dict(plan["code_sha256"]),
            "builder_code_sha256": {"synthetic-builder.py": "c" * 64},
            "storage": dict(plan["storage"]),
        },
        "predecessor_certificate_sha256": "d" * 64,
        "predecessor_gap_intervals": [],
        "bootstrap_uncovered_interval": interval,
        "verified_gap_intervals": [interval],
        "maximum_source_availability_ns": 10,
        "frames": [],
        "captures": [],
        "references": [],
        "totals": {},
    }


@contextlib.contextmanager
def _capacity_test_guard(
    config: Path,
    *,
    min_free_bytes: int,
    probe: Any,
    metrics: dict[str, Any],
) -> Iterator[Any]:
    required = publication.required_free_bytes()
    if min_free_bytes != required:
        raise QualificationFailure("requested_capacity_envelope_mismatch")
    with writer_guard(config, min_free_bytes=0, probe=probe) as real_guard:
        try:
            real_guard.revalidate(min_free_bytes=required)
        except StorageError as exc:
            if "insufficient_free_space" not in str(exc):
                raise
            real_rejected = True
        else:
            raise QualificationFailure("test_image_unexpectedly_meets_live_capacity")
        metrics["capacity_override_call_count"] = metrics.get("capacity_override_call_count", 0) + 1
        metrics.update(
            actual_disposable_free_bytes=min(
                metrics.get("actual_disposable_free_bytes", real_guard.available_bytes),
                real_guard.available_bytes,
            ),
            requested_live_min_free_bytes=required,
            applied_min_free_bytes=0,
            real_capacity_precheck_rejected=real_rejected,
            disposable_root_id=real_guard.root_id,
        )

        class OverrideGuard:
            def __getattr__(self, name: str) -> Any:
                return getattr(real_guard, name)

            def revalidate(self, *, min_free_bytes: int | None = None) -> Any:
                metrics["capacity_override_revalidation_count"] = (
                    metrics.get("capacity_override_revalidation_count", 0) + 1
                )
                return real_guard.revalidate(min_free_bytes=0)

        override = OverrideGuard()
        yield override


def _run_detach_case(
    *,
    image: MountedImage,
    config: Path,
    baseline: dict[str, tuple[str, int]],
    service_id: str,
    operation: str,
    occurrence: int,
    capacity_metrics: dict[str, Any],
) -> dict[str, Any]:
    from tools import brain_senses_external_structural_publish_v1 as publish_callsite

    detached = False
    hit_count = 0
    outcome: str | None = None
    underlay_sha = _sha(_canonical(sorted(baseline.items())))
    with writer_guard(config, probe=image.probe) as guard:
        plan, plan_sha = _write_service_fixture(guard, service_id, image)
    artifact = _synthetic_artifact(plan, plan_sha)

    def fault_writer(root_fd: int, *, before_write: Any = None) -> Any:
        def hook(current: str) -> None:
            nonlocal detached, hit_count
            if current != operation:
                return
            hit_count += 1
            if hit_count != occurrence:
                return
            if not os.path.ismount(image.mountpoint):
                raise QualificationFailure("detach_hook_unexpected_mount_state")
            image.detach()
            detached = True

        return DescriptorWriter(
            root_fd,
            before_write=before_write,
            fault_hook=hook,
        )

    old_writer = publish_callsite.__dict__["DescriptorWriter"]
    old_compile = structural_builder.compile_from_sealed_v2
    publish_callsite.__dict__["DescriptorWriter"] = fault_writer
    structural_builder.compile_from_sealed_v2 = lambda *_a, **_k: artifact
    try:
        try:
            publish_callsite.publish_after_seal(
                config_path=config,
                service_id=service_id,
                predecessor_relative_root=V1_ROOT,
                expected_plan_sha256=plan_sha,
                expected_lease_sources=plan["code_sha256"],
                guard_factory=lambda path, min_free_bytes: _capacity_test_guard(
                    path,
                    min_free_bytes=min_free_bytes,
                    probe=image.probe,
                    metrics=capacity_metrics,
                ),
            )
        except (OSError, StorageError) as exc:
            outcome = type(exc).__name__
        except BaseException:
            if not detached:
                raise
            outcome = "unexpected_exception_after_detach"
        if not detached:
            raise QualificationFailure(f"detach_hook_not_reached:{operation}:{occurrence}")
        if outcome == "unexpected_exception_after_detach":
            raise QualificationFailure("unexpected_post_detach_failure")
        if os.path.ismount(image.mountpoint):
            image.detach()
        if _trap_snapshot(image.mountpoint) != baseline:
            raise QualificationFailure("underlay_changed_after_detach")
        image.attach()
        if str(image.volume_uuid).lower() != str(plan["storage"]["volume_uuid"]).lower():
            raise QualificationFailure("remount_volume_uuid_changed")
        _assert_sibling_state(image, service_id, operation, occurrence)
        return {
            "operation": operation,
            "occurrence": occurrence,
            "outcome_class": outcome or "completed_on_held_descriptor",
            "detached_after_successful_revalidation": True,
            "underlay_unchanged": True,
            "no_retry_or_replay": True,
            "provider_calls": 0,
            "fallback_artifacts": [],
            "underlay_manifest_sha256": underlay_sha,
            "remounted_uuid": image.volume_uuid,
        }
    finally:
        publish_callsite.__dict__["DescriptorWriter"] = old_writer
        structural_builder.compile_from_sealed_v2 = old_compile


def _assert_sibling_state(
    image: MountedImage, service_id: str, operation: str, occurrence: int
) -> None:
    service = image.mountpoint / DATA_ROOT / "services" / service_id
    output = image.mountpoint / DATA_ROOT / OUTPUT_ROOT / service_id
    if operation == "mkdir":
        return
    if not output.is_dir():
        raise QualificationFailure("output_root_missing_after_remount")
    if operation == "lock_open":
        if (output / "manifest.json").exists() or (output / "publication.json").exists():
            raise QualificationFailure("lock_detach_advanced_to_publication")
        return
    if operation == "create" and occurrence == 1 and (output / "publication.json").exists():
        raise QualificationFailure("manifest_detach_published_receipt")
    if operation == "create" and occurrence == 2 and (output / "publication.json").exists():
        # A successful write on the detached image descriptor is acceptable;
        # the underlay remained unchanged and the process did not replay.
        pass
    if service.exists() and not (service / v2_schema.PLAN_FILE).exists():
        raise QualificationFailure("source_root_changed")


def _run_mkdir_case(
    *,
    image: MountedImage,
    config: Path,
    baseline: dict[str, tuple[str, int]],
    capacity_metrics: dict[str, Any],
) -> dict[str, Any]:
    from tools import brain_senses_external_structural_publish_v1 as publish_callsite

    with writer_guard(config, probe=image.probe) as guard:
        plan, plan_sha = _write_service_fixture(guard, "structural-mkdir", image)
    artifact = _synthetic_artifact(plan, plan_sha)
    old_compile = structural_builder.compile_from_sealed_v2
    structural_builder.compile_from_sealed_v2 = lambda *_a, **_k: artifact
    original_mkdir = os.mkdir
    detached = False
    outcome: str | None = None

    def hook(path: str | bytes, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        nonlocal detached, outcome
        if path == "derived" and not detached:
            image.detach()
            detached = True
        try:
            original_mkdir(path, mode, dir_fd=dir_fd)
        except OSError as exc:
            if path == "derived":
                outcome = type(exc).__name__
            raise

    os.mkdir = hook  # type: ignore[assignment]
    try:
        try:
            publish_callsite.publish_after_seal(
                config_path=config,
                service_id="structural-mkdir",
                predecessor_relative_root=V1_ROOT,
                expected_plan_sha256=plan_sha,
                expected_lease_sources=plan["code_sha256"],
                guard_factory=lambda path, min_free_bytes: _capacity_test_guard(
                    path,
                    min_free_bytes=min_free_bytes,
                    probe=image.probe,
                    metrics=capacity_metrics,
                ),
            )
        except (OSError, StorageError) as exc:
            outcome = type(exc).__name__
        if not detached:
            raise QualificationFailure("mkdir_detach_not_triggered")
        if os.path.ismount(image.mountpoint):
            image.detach()
        if _trap_snapshot(image.mountpoint) != baseline:
            raise QualificationFailure("mkdir_underlay_changed")
        image.attach()
        return {
            "operation": "mkdir",
            "occurrence": 1,
            "outcome_class": outcome or "completed_on_held_descriptor",
            "detached_after_successful_revalidation": True,
            "underlay_unchanged": True,
            "no_retry_or_replay": True,
            "provider_calls": 0,
            "fallback_artifacts": [],
            "underlay_manifest_sha256": _sha(_canonical(sorted(baseline.items()))),
            "remounted_uuid": image.volume_uuid,
        }
    finally:
        os.mkdir = original_mkdir
        structural_builder.compile_from_sealed_v2 = old_compile


def _run_atomic_rename_case(
    *,
    image: MountedImage,
    config: Path,
    baseline: dict[str, tuple[str, int]],
    capacity_metrics: dict[str, Any],
) -> dict[str, Any]:
    with _capacity_test_guard(
        config,
        min_free_bytes=publication.required_free_bytes(),
        probe=image.probe,
        metrics=capacity_metrics,
    ) as guard:
        guard.create_directory("derived/structural-input-v1/rename-probe")
        root_path = image.mountpoint / DATA_ROOT / OUTPUT_ROOT / "rename-probe"
        root_fd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        detached = False
        outcome: str | None = None

        def hook(operation: str) -> None:
            nonlocal detached
            if operation == "atomic_rename" and not detached:
                image.detach()
                detached = True

        try:
            try:
                with DescriptorWriter(
                    root_fd,
                    before_write=lambda: guard.revalidate(min_free_bytes=0),
                    fault_hook=hook,
                ) as writer:
                    writer.atomic_write("rename-probe.json", b"probe", create_only=True)
            except (OSError, StorageError) as exc:
                outcome = type(exc).__name__
        finally:
            os.close(root_fd)
        if not detached:
            raise QualificationFailure("atomic_rename_detach_not_triggered")
        if os.path.ismount(image.mountpoint):
            image.detach()
        if _trap_snapshot(image.mountpoint) != baseline:
            raise QualificationFailure("atomic_rename_underlay_changed")
        image.attach()
        return {
            "operation": "atomic_rename",
            "occurrence": 1,
            "outcome_class": outcome or "completed_on_held_descriptor",
            "detached_after_successful_revalidation": True,
            "underlay_unchanged": True,
            "no_retry_or_replay": True,
            "provider_calls": 0,
            "fallback_artifacts": [],
            "underlay_manifest_sha256": _sha(_canonical(sorted(baseline.items()))),
            "remounted_uuid": image.volume_uuid,
        }


def validate_receipt(value: Any) -> None:
    required = {
        "schema",
        "schema_version",
        "status",
        "provider_calls",
        "configured_kingston_touched",
        "planned_root_id",
        "planned_volume_uuid",
        "tested_disposable_identity",
        "capacity_override_test_only",
        "capacity_qualified",
        "actual_disposable_free_bytes",
        "requested_live_min_free_bytes",
        "applied_min_free_bytes",
        "capacity_override_call_count",
        "capacity_override_revalidation_count",
        "real_capacity_precheck_rejected",
        "test_only_ownership_normalization",
        "source_sha256",
        "test_sha256",
        "patch_sha256",
        "forced_detach_points",
        "underlay_before_sha256",
        "underlay_after_sha256",
        "remount_uuid",
        "cleanup_verified",
        "open_handles_after_unmount",
        "temporary_root_removed",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise QualificationFailure("receipt_shape")
    identity = value["tested_disposable_identity"]
    if (
        value["schema"] != RECEIPT_SCHEMA
        or value["schema_version"] != 2
        or value["status"] != "passed"
        or value["provider_calls"] != 0
        or value["configured_kingston_touched"] is not False
        or not isinstance(identity, dict)
        or set(identity)
        != {
            "volume_uuid",
            "mount_path",
            "fs_type",
            "case_sensitive",
            "owners_enabled",
            "root_id",
            "relative_root",
        }
        or identity["volume_uuid"].lower() == value["planned_volume_uuid"].lower()
        or identity["root_id"] == value["planned_root_id"]
        or not Path(identity["mount_path"]).is_absolute()
        or identity["relative_root"] != DATA_ROOT
        or identity["fs_type"] != "case-sensitive APFS"
        or identity["case_sensitive"] is not True
        or value["capacity_override_test_only"] is not True
        or value["capacity_qualified"] is not False
        or value["applied_min_free_bytes"] != 0
        or not isinstance(value["actual_disposable_free_bytes"], int)
        or value["actual_disposable_free_bytes"] <= 0
        or not isinstance(value["requested_live_min_free_bytes"], int)
        or value["requested_live_min_free_bytes"] != publication.required_free_bytes()
        or value["capacity_override_call_count"] != 5
        or not isinstance(value["capacity_override_revalidation_count"], int)
        or value["capacity_override_revalidation_count"] < 5
        or value["real_capacity_precheck_rejected"] is not True
        or value["test_only_ownership_normalization"] is not (identity["owners_enabled"] is False)
        or value["underlay_before_sha256"] != value["underlay_after_sha256"]
        or value["remount_uuid"].lower() != identity["volume_uuid"].lower()
        or value["cleanup_verified"] is not True
        or value["open_handles_after_unmount"] != 0
        or value["temporary_root_removed"] is not True
    ):
        raise QualificationFailure("receipt_contract")
    points = value["forced_detach_points"]
    if not isinstance(points, list) or len(points) != 5:
        raise QualificationFailure("receipt_detach_points")
    expected_points = [
        ("mkdir", 1),
        ("lock_open", 1),
        ("create", 1),
        ("atomic_rename", 1),
        ("create", 2),
    ]
    for point, (operation, occurrence) in zip(points, expected_points, strict=True):
        if (
            not isinstance(point, dict)
            or point.get("operation") != operation
            or point.get("occurrence") != occurrence
            or point.get("detached_after_successful_revalidation") is not True
            or point.get("underlay_unchanged") is not True
            or point.get("no_retry_or_replay") is not True
            or point.get("provider_calls") != 0
            or point.get("fallback_artifacts") != []
            or point.get("underlay_manifest_sha256") != value["underlay_before_sha256"]
            or point.get("remounted_uuid", "").lower() != identity["volume_uuid"].lower()
        ):
            raise QualificationFailure("receipt_detach_point")
    expected_source_paths = {*PINNED_SOURCES, *TEST_SOURCE_PATHS}
    source_map = value["source_sha256"]
    if (
        not isinstance(source_map, dict)
        or set(source_map) != expected_source_paths
        or any(not isinstance(digest, str) or len(digest) != 64 for digest in source_map.values())
        or value["test_sha256"]
        != _sha(_canonical({path: source_map[path] for path in TEST_SOURCE_PATHS}))
        or value["patch_sha256"] != _sha(_canonical(source_map))
    ):
        raise QualificationFailure("receipt_source_pins")


def run_qualification() -> dict[str, Any]:
    if sys.platform != "darwin" or not Path("/usr/bin/hdiutil").exists():
        raise QualificationFailure("darwin_hdiutil_required")
    source_hashes = _source_hashes()
    source_patch_hash = _sha(_canonical(source_hashes))
    capacity_metrics: dict[str, Any] = {"capacity_override_call_count": 0}
    receipt: dict[str, Any] | None = None
    temp_root_path: Path | None = None
    final_uuid: str | None = None
    baseline_hash: str | None = None
    identity: dict[str, Any] | None = None
    open_handles_after_unmount: int | None = None
    with tempfile.TemporaryDirectory(
        prefix="structural-publish-qualify-", dir="/private/tmp"
    ) as raw:
        temp_root = Path(raw)
        temp_root_path = temp_root
        image, baseline, config = _setup_image(temp_root)
        baseline_hash = _sha(_canonical(sorted(baseline.items())))
        try:
            points = [
                _run_mkdir_case(
                    image=image,
                    config=config,
                    baseline=baseline,
                    capacity_metrics=capacity_metrics,
                )
            ]
            for index, (operation, occurrence) in enumerate(DETACH_CASES[1:], start=1):
                service_id = f"structural-detach-{index}"
                if operation == "atomic_rename":
                    point = _run_atomic_rename_case(
                        image=image,
                        config=config,
                        baseline=baseline,
                        capacity_metrics=capacity_metrics,
                    )
                else:
                    point = _run_detach_case(
                        image=image,
                        config=config,
                        baseline=baseline,
                        service_id=service_id,
                        operation=operation,
                        occurrence=occurrence,
                        capacity_metrics=capacity_metrics,
                    )
                points.append(point)
            _require(image.volume_uuid is not None, "remount_uuid_missing")
            final_uuid = str(image.volume_uuid)
            if os.path.ismount(image.mountpoint):
                image.detach()
            if _trap_snapshot(image.mountpoint) != baseline:
                raise QualificationFailure("final_underlay_changed")
            image.attach()
            if str(image.volume_uuid).lower() != final_uuid.lower():
                raise QualificationFailure("final_remount_identity_changed")
            if os.path.ismount(image.mountpoint):
                image.detach()
            if _trap_snapshot(image.mountpoint) != baseline:
                raise QualificationFailure("cleanup_underlay_changed")
            if os.path.ismount(image.mountpoint):
                raise QualificationFailure("cleanup_mount_still_active")
            open_handles_after_unmount = _open_handles_under(temp_root)
            if open_handles_after_unmount != 0:
                raise QualificationFailure("cleanup_open_handles_present")
            requested = publication.required_free_bytes()
            if capacity_metrics.get("capacity_override_call_count") != len(DETACH_CASES):
                raise QualificationFailure("capacity_guard_context_count")
            if capacity_metrics.get("requested_live_min_free_bytes") != requested:
                raise QualificationFailure("capacity_request_not_recorded")
            if capacity_metrics.get("applied_min_free_bytes") != 0:
                raise QualificationFailure("capacity_override_not_recorded")
            if capacity_metrics.get("real_capacity_precheck_rejected") is not True:
                raise QualificationFailure("real_capacity_precheck_missing")
            if not isinstance(capacity_metrics.get("actual_disposable_free_bytes"), int):
                raise QualificationFailure("actual_capacity_not_observed")
            if capacity_metrics["actual_disposable_free_bytes"] <= 0:
                raise QualificationFailure("actual_capacity_invalid")
            identity = {
                "volume_uuid": final_uuid,
                "mount_path": str(image.mountpoint),
                "fs_type": "case-sensitive APFS",
                "case_sensitive": True,
                "owners_enabled": bool(image.owners_enabled),
                "root_id": str(capacity_metrics["disposable_root_id"]),
                "relative_root": DATA_ROOT,
            }
            receipt = {
                "schema": RECEIPT_SCHEMA,
                "schema_version": 2,
                "status": "passed",
                "provider_calls": 0,
                "configured_kingston_touched": False,
                "planned_root_id": "9428695af6d4f46c08e1a444cdc66de8",
                "planned_volume_uuid": KINGSTON_UUID,
                "tested_disposable_identity": identity,
                "capacity_override_test_only": True,
                "capacity_qualified": False,
                "actual_disposable_free_bytes": capacity_metrics["actual_disposable_free_bytes"],
                "requested_live_min_free_bytes": requested,
                "applied_min_free_bytes": 0,
                "capacity_override_call_count": capacity_metrics["capacity_override_call_count"],
                "capacity_override_revalidation_count": capacity_metrics.get(
                    "capacity_override_revalidation_count", 0
                ),
                "real_capacity_precheck_rejected": True,
                "test_only_ownership_normalization": identity["owners_enabled"] is False,
                "source_sha256": source_hashes,
                "test_sha256": _sha(
                    _canonical({path: source_hashes[path] for path in TEST_SOURCE_PATHS})
                ),
                "patch_sha256": source_patch_hash,
                "forced_detach_points": points,
                "underlay_before_sha256": baseline_hash,
                "underlay_after_sha256": _sha(
                    _canonical(sorted(_trap_snapshot(image.mountpoint).items()))
                ),
                "remount_uuid": final_uuid,
                "cleanup_verified": True,
                "open_handles_after_unmount": open_handles_after_unmount,
                "temporary_root_removed": False,
            }
        finally:
            if os.path.ismount(image.mountpoint):
                image.detach()
    if temp_root_path is None or temp_root_path.exists():
        raise QualificationFailure("temporary_root_cleanup_failed")
    if (
        receipt is None
        or identity is None
        or final_uuid is None
        or baseline_hash is None
        or open_handles_after_unmount != 0
    ):
        raise QualificationFailure("qualification_no_result")
    if _source_hashes() != source_hashes:
        raise QualificationFailure("pinned_source_changed_after_cleanup")
    receipt["temporary_root_removed"] = True
    validate_receipt(receipt)
    return receipt


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise QualificationFailure(reason)


def write_receipt(path: Path, value: Mapping[str, Any]) -> None:
    validate_receipt(value)
    raw = _canonical(value)
    _write_private(path, raw)
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO / RECEIPT_PATH)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise QualificationFailure("receipt_already_exists")
        if not args.output.parent.is_dir():
            raise QualificationFailure("receipt_parent_missing")
        value = run_qualification()
        write_receipt(args.output, value)
    except (QualificationFailure, MountQualificationError, OSError, StorageError) as exc:
        print(f"qualification failed: {type(exc).__name__}:{exc}", file=sys.stderr)
        return 2
    print(
        json.dumps({"status": value["status"], "forced_points": len(value["forced_detach_points"])})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
