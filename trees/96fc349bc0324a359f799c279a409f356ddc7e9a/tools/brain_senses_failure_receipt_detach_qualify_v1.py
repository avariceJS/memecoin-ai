#!/usr/bin/env python3
"""Force-detach qualify the launch failure-receipt writer on disposable APFS.

The qualification creates only a sparse case-sensitive APFS image under
``/private/tmp``. It never binds or writes to the configured Kingston volume,
reads provider credentials, or contacts a provider.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from memecoin_research_storage import (  # noqa: E402
    StorageError,
    configure,
    create_root,
    writer_guard,
)
from tools import brain_senses_failure_receipt_v1 as receipt_writer  # noqa: E402
from tools.brain_senses_external_verify_v2 import DescriptorReaderV2  # noqa: E402
from tools.brain_senses_external_writer_v1 import DescriptorWriter  # noqa: E402
from tools.research_storage_detach_qualify_v1 import (  # noqa: E402
    ImageVolumeProbe,
    MountedImage,
    _assert_underlay_unchanged,
    _disk_info,
    _run,
    _seed_trap,
    _trap_snapshot,
)
from tools.research_storage_detach_qualify_v1 import (  # noqa: E402
    QualificationError as MountQualificationError,
)

DATA_ROOT = "ResearchData"
SERVICE_ID = "launch-v3-receipt-detach-fixture"
RECEIPT_RELATIVE = f"diagnostics/external-launch-v1/{SERVICE_ID}/failure-20261012T0600Z.json"
PINNED = (
    "tools/brain_senses_failure_receipt_detach_qualify_v1.py",
    "tools/brain_senses_failure_receipt_v1.py",
    "tools/brain_senses_external_launch_v3.py",
    "tools/brain_senses_external_journal_snapshot_v1.py",
    "tools/brain_senses_external_writer_v1.py",
    "tools/brain_senses_external_launch_v2.py",
    "tools/brain_senses_external_orchestrator_v2.py",
    "tools/brain_senses_external_verify_v2.py",
    "tools/brain_senses_external_verify_v1.py",
    "src/memecoin_research_storage/__init__.py",
    "tools/research_storage_detach_qualify_v1.py",
    "tests/test_brain_senses_failure_receipt_v1.py",
    "tests/test_brain_senses_external_journal_snapshot_v1.py",
)


class QualificationFailure(RuntimeError):
    """A bounded, sanitized APFS qualification failure."""


def _canonical(value: Any) -> bytes:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return (encoded + "\n").encode()


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _source_hashes() -> dict[str, str]:
    return {path: _sha((REPO / path).read_bytes()) for path in PINNED}


@contextlib.contextmanager
def _no_host_lock() -> Iterator[None]:
    yield


def _new_image(base: Path) -> tuple[MountedImage, dict[str, tuple[str, int]]]:
    mountpoint = base / "mount"
    config_dir = base / "config"
    mountpoint.mkdir(mode=0o700)
    config_dir.mkdir(mode=0o700)
    image = MountedImage(
        base,
        mountpoint,
        base / "failure-receipt.sparseimage",
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
            "BrainFailureReceiptQualification",
            "-type",
            "SPARSE",
            str(image.image),
        ],
        timeout=90,
    )
    image.attach()
    try:
        if image.volume_uuid is None:
            raise QualificationFailure("disposable_uuid_missing")
        image.probe = ImageVolumeProbe(image.volume_uuid)
        configure(
            image.config,
            image.mountpoint,
            DATA_ROOT,
            min_free_bytes=0,
            volume_uuid=image.volume_uuid,
            probe=image.probe,
        )
        create_root(image.config, probe=image.probe)
        return image, baseline
    except BaseException:
        if os.path.ismount(image.mountpoint):
            image.detach()
        raise


def _synthetic_record() -> dict[str, Any]:
    wall = time.time_ns()
    monotonic = time.monotonic_ns()
    bucket = "20261012T0600Z"
    proof = receipt_writer.JournalProof(
        bucket=bucket,
        reservation_ids_before=(),
        reservation_ids_after=(),
        intent_ids_before=(),
        intent_ids_after=(),
        event_tail_sha256="a" * 64,
        fresh_independent_reads=True,
        required_locks_held=True,
    )
    return receipt_writer.build_record(
        service_id=SERVICE_ID,
        plan_sha256="b" * 64,
        scheduled_bucket=bucket,
        callback_start_utc_ns=wall - 1_000_000,
        callback_start_monotonic_ns=monotonic - 1_000_000,
        phase="pre-dispatch",
        exception=RuntimeError("synthetic private exception text"),
        reason_code="pre_dispatch_failed",
        journal_proof=proof,
        receipt_utc_ns=wall,
        receipt_monotonic_ns=monotonic,
    )


def _run_detach_case(base: Path, operation: str) -> dict[str, Any]:
    image, baseline = _new_image(base)
    detached = False
    calls = 0
    outcome = False
    record = _synthetic_record()

    class HookedDescriptorWriter(DescriptorWriter):
        def __init__(self, root_fd: int, *, before_write: Any = None) -> None:
            def hook(current: str) -> None:
                nonlocal detached
                if current == operation and not detached:
                    if not os.path.ismount(image.mountpoint):
                        raise QualificationFailure("detach_hook_mount_state")
                    image.detach()
                    detached = True
                    _assert_underlay_unchanged(
                        image, baseline, context=f"failure-receipt:{operation}"
                    )

            super().__init__(root_fd, before_write=before_write, fault_hook=hook)

    try:
        with writer_guard(image.config, probe=image.probe) as guard:
            calls += 1
            outcome = receipt_writer.persist_record_with_guard(
                guard,
                record,
                writer_factory=HookedDescriptorWriter,
            )
    except (OSError, StorageError):
        outcome = False
    finally:
        if os.path.ismount(image.mountpoint):
            image.detach()
    if not detached or calls != 1:
        raise QualificationFailure("forced_detach_not_reached_once")
    _assert_underlay_unchanged(image, baseline, context=f"after:{operation}")
    underlay = _trap_snapshot(image.mountpoint)
    if underlay != baseline:
        raise QualificationFailure("internal_fallback_artifact")
    image.attach()
    try:
        if image.volume_uuid is None:
            raise QualificationFailure("remount_uuid_missing")
        remounted_uuid = image.volume_uuid
        root = image.mountpoint / DATA_ROOT
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with DescriptorReaderV2(root_fd) as reader:
                names = set(reader.list_files())
        finally:
            os.close(root_fd)
        return {
            "operation": operation,
            "detach_reached": True,
            "writer_returned_true": outcome,
            "receipt_present_after_remount": RECEIPT_RELATIVE in names,
            "underlay_unchanged": True,
            "fallback_artifacts": [],
            "provider_calls": 0,
            "remounted_uuid": remounted_uuid,
            "fs_type": str(_disk_info(image.mountpoint).get("FilesystemName", "")),
            "owners_enabled": image.owners_enabled,
            "underlay_manifest_sha256": _sha(_canonical(sorted(baseline.items()))),
        }
    finally:
        image.detach()


def run_qualification(output: Path | None = None) -> dict[str, Any]:
    if sys.platform != "darwin" or not Path("/usr/bin/hdiutil").exists():
        raise QualificationFailure("darwin_hdiutil_required")
    temp_parent = Path("/private/tmp")
    if not temp_parent.is_dir():
        raise QualificationFailure("private_tmp_missing")
    initial_sources = _source_hashes()
    points: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="brain-v3-receipt-detach-", dir=temp_parent) as raw:
        root = Path(raw)
        root.chmod(0o700)
        for operation in ("mkdir", "create"):
            case = root / operation
            case.mkdir(mode=0o700)
            point = _run_detach_case(case, operation)
            if point["fs_type"].strip().casefold() != "case-sensitive apfs":
                raise QualificationFailure("disposable_filesystem_mismatch")
            points.append(point)
    cleanup = not root.exists()
    if not cleanup or _source_hashes() != initial_sources:
        raise QualificationFailure("qualification_cleanup_or_source_drift")
    for point in points:
        if (
            point["underlay_unchanged"] is not True
            or point["fallback_artifacts"] != []
            or point["provider_calls"] != 0
            or point["detach_reached"] is not True
        ):
            raise QualificationFailure("detach_evidence_incomplete")
    receipt = {
        "schema": "brain-senses-launch-v3-failure-receipt-detach-qualification-v1",
        "schema_version": 1,
        "status": "passed",
        "observed_at_utc_ns": time.time_ns(),
        "head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.strip(),
        "source_sha256": initial_sources,
        "configured_kingston_touched": False,
        "provider_io": False,
        "transaction_signing_or_submission": False,
        "disposable_images": 2,
        "disposable_fs_type": "case-sensitive APFS",
        "disposable_mount_root": "/private/tmp",
        "forced_detach_points": points,
        "capacity_qualified": False,
        "temporary_root_removed": cleanup,
    }
    if output is not None:
        output.write_bytes(_canonical(receipt))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        receipt = run_qualification(args.output)
    except (OSError, StorageError, MountQualificationError, QualificationFailure) as exc:
        print(json.dumps({"status": "rejected", "reason": type(exc).__name__}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {"status": receipt["status"], "detach_points": len(receipt["forced_detach_points"])}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
