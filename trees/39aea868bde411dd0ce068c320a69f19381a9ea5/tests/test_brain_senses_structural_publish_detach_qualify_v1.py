from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from memecoin_research_storage import StorageError
from tools import brain_senses_external_structural_publish_v1 as publication
from tools import brain_senses_structural_publish_detach_qualify_v1 as qualify
from tools.research_storage_detach_qualify_v1 import KINGSTON_UUID


def _valid_receipt() -> dict[str, Any]:
    underlay = "b" * 64
    uuid = "11111111-2222-3333-4444-555555555555"
    sources = {path: "a" * 64 for path in (*qualify.PINNED_SOURCES, *qualify.TEST_SOURCE_PATHS)}
    operations = [
        ("mkdir", 1),
        ("lock_open", 1),
        ("create", 1),
        ("atomic_rename", 1),
        ("create", 2),
    ]
    points = [
        {
            "operation": operation,
            "occurrence": occurrence,
            "detached_after_successful_revalidation": True,
            "underlay_unchanged": True,
            "no_retry_or_replay": True,
            "provider_calls": 0,
            "fallback_artifacts": [],
            "underlay_manifest_sha256": underlay,
            "remounted_uuid": uuid,
        }
        for operation, occurrence in operations
    ]
    return {
        "schema": qualify.RECEIPT_SCHEMA,
        "schema_version": 2,
        "status": "passed",
        "provider_calls": 0,
        "configured_kingston_touched": False,
        "planned_root_id": "planned-kingston-root",
        "planned_volume_uuid": KINGSTON_UUID,
        "tested_disposable_identity": {
            "volume_uuid": uuid,
            "mount_path": "/private/tmp/qualification/mount",
            "fs_type": "case-sensitive APFS",
            "case_sensitive": True,
            "owners_enabled": False,
            "root_id": "disposable-root",
            "relative_root": qualify.DATA_ROOT,
        },
        "capacity_override_test_only": True,
        "capacity_qualified": False,
        "actual_disposable_free_bytes": 234_567_890,
        "requested_live_min_free_bytes": publication.required_free_bytes(),
        "applied_min_free_bytes": 0,
        "capacity_override_call_count": 5,
        "capacity_override_revalidation_count": 14,
        "real_capacity_precheck_rejected": True,
        "test_only_ownership_normalization": True,
        "source_sha256": sources,
        "test_sha256": qualify._sha(
            qualify._canonical({path: sources[path] for path in qualify.TEST_SOURCE_PATHS})
        ),
        "patch_sha256": qualify._sha(qualify._canonical(sources)),
        "forced_detach_points": points,
        "underlay_before_sha256": underlay,
        "underlay_after_sha256": underlay,
        "remount_uuid": uuid,
        "cleanup_verified": True,
        "open_handles_after_unmount": 0,
        "temporary_root_removed": True,
    }


def test_receipt_accepts_five_bounded_detach_points_and_observed_capacity() -> None:
    qualify.validate_receipt(_valid_receipt())
    assert qualify.RECEIPT_SCHEMA == "brain-senses-structural-input-sibling-writer-qualification-v2"
    assert qualify.RECEIPT_PATH == (
        "docs/evidence/brain-senses-structural-input-sibling-writer-qualification-v2.json"
    )


def test_receipt_write_is_create_only_and_canonical(tmp_path: Path) -> None:
    receipt = _valid_receipt()
    path = tmp_path / "qualification.json"
    qualify.write_receipt(path, receipt)
    assert path.read_bytes() == qualify._canonical(receipt)
    with pytest.raises(FileExistsError):
        qualify.write_receipt(path, receipt)


def test_receipt_rejects_capacity_or_detach_claim_mutations() -> None:
    mutations: list[tuple[tuple[str | int, ...], Any]] = [
        (("capacity_override_call_count",), 1),
        (("actual_disposable_free_bytes",), 0),
        (("applied_min_free_bytes",), 256 * 1024 * 1024),
        (("forced_detach_points", 0, "underlay_unchanged"), False),
        (("forced_detach_points", 1, "operation"), "create"),
        (("forced_detach_points", 3, "occurrence"), 2),
        (("tested_disposable_identity", "relative_root"), "services/not-the-test-root"),
    ]
    for path, value in mutations:
        receipt = copy.deepcopy(_valid_receipt())
        target: Any = receipt
        for item in path[:-1]:
            target = target[item]
        target[path[-1]] = value
        with pytest.raises(qualify.QualificationFailure):
            qualify.validate_receipt(receipt)


def test_capacity_override_counts_contexts_separately_from_revalidations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = publication.required_free_bytes()

    class Guard:
        root_id = "disposable-root"
        available_bytes = 234_567_890

        def revalidate(self, *, min_free_bytes: int | None = None) -> None:
            if min_free_bytes == required:
                raise StorageError("insufficient_free_space")

    class Context:
        def __enter__(self) -> Guard:
            return Guard()

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(qualify, "writer_guard", lambda *_a, **_k: Context())
    metrics: dict[str, Any] = {"capacity_override_call_count": 0}
    with qualify._capacity_test_guard(
        tmp_path / "storage.json",
        min_free_bytes=required,
        probe=object(),
        metrics=metrics,
    ) as guard:
        guard.revalidate(min_free_bytes=required)
        guard.revalidate(min_free_bytes=required)

    assert metrics["capacity_override_call_count"] == 1
    assert metrics["capacity_override_revalidation_count"] == 2
    assert metrics["actual_disposable_free_bytes"] == 234_567_890
    assert metrics["real_capacity_precheck_rejected"] is True


def test_fixture_seeding_reuses_existing_predecessor_lock_without_rewriting(
    tmp_path: Path,
) -> None:
    mount = tmp_path / "mount"
    services = mount / qualify.DATA_ROOT / "services"
    predecessor = services / "v1-structural-detach-fixture"
    predecessor.mkdir(mode=0o700, parents=True)

    class FakeGuard:
        root_id = "fixture-root"
        volume_uuid = "fixture-volume"

        def create_directory(self, relative: str) -> None:
            (mount / qualify.DATA_ROOT / relative).mkdir(mode=0o700, parents=True)

        def revalidate(self, *, min_free_bytes: int | None = None) -> None:
            return None

    image = SimpleNamespace(mountpoint=mount)
    guard = FakeGuard()

    qualify._write_service_fixture(guard, "first-service", image)
    lock = predecessor / ".writer.lock"
    before = lock.stat()
    assert lock.read_bytes() == qualify.PREDECESSOR_LOCK_BYTES

    qualify._write_service_fixture(guard, "second-service", image)
    after = lock.stat()
    assert lock.read_bytes() == qualify.PREDECESSOR_LOCK_BYTES
    assert (after.st_dev, after.st_ino, after.st_mtime_ns) == (
        before.st_dev,
        before.st_ino,
        before.st_mtime_ns,
    )
