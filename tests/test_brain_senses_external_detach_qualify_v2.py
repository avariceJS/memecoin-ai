from __future__ import annotations

import copy

import pytest

from tools.brain_senses_external_detach_qualify_v2 import (
    QualificationFailure,
    _publish_receipt,
    sha,
    validate_receipt,
)


def _valid_receipt() -> dict[str, object]:
    source = {"tools/brain_senses_external_launch_v2.py": "a" * 64}
    return {
        "schema": "external-v2-descriptor-detach-integration-qualification",
        "schema_version": 2,
        "status": "passed",
        "provider_requests": 0,
        "head": "b" * 40,
        "source_sha256": source,
        "writer_source_sha256": source,
        "mount_identity": {"root_id": "lease-root", "volume_uuid": "lease-uuid"},
        "descriptor_path": "services/v2-live",
        "storage": {
            "root_id": "lease-root",
            "volume_uuid": "lease-uuid",
            "relative_root": "services/v2-live",
        },
        "tested_disposable_identity": {
            "volume_uuid": "temporary-uuid",
            "mount_path": "/private/tmp/test/mount",
            "fs_type": "case-sensitive APFS",
            "case_sensitive": True,
            "owners_enabled": False,
            "root_id": "temporary-root",
            "relative_root": "services/v2-detach-fixture",
        },
        "test_only_ownership_normalization": True,
        "tested_disposable_source": "tools/brain_senses_external_detach_qualify_v2.py",
        "tested_disposable_source_sha256": "c" * 64,
        "capacity_override_test_only": True,
        "capacity_qualified": False,
        "actual_disposable_free_bytes": 37_000_000_000,
        "requested_live_min_free_bytes": 57_000_000_000,
        "applied_min_free_bytes": 0,
        "capacity_override_call_count": 16,
        "real_capacity_precheck_rejected": True,
        "configured_kingston_touched": False,
        "forced_detach_points": [
            {
                "underlay_unchanged": True,
                "no_retry_or_replay": True,
                "provider_loader_calls": 0,
                "fallback_artifacts": [],
                "safe_syscall_failure": True,
            }
        ],
        "underlay_manifest_sha256": "d" * 64,
        "fallback_artifacts": [],
        "underlay_manifest_unchanged": True,
        "cleanup_verified": True,
        "temporary_root_removed": True,
    }


def test_receipt_distinguishes_lease_binding_from_temporary_apfs() -> None:
    validate_receipt(_valid_receipt())


def test_real_publication_shape_is_accepted_and_pins_start_bytes(tmp_path) -> None:
    harness = b"qualification harness snapshot"
    receipt = _valid_receipt()
    receipt["tested_disposable_source_sha256"] = sha(harness)
    assert "script_sha256" not in receipt

    destination = tmp_path / "qualification.json"
    _publish_receipt(
        receipt,
        initial_harness=harness,
        current_harness=harness,
        output=destination,
    )
    assert destination.exists()


def test_source_mutation_fails_before_receipt_creation(tmp_path) -> None:
    harness = b"qualification harness snapshot"
    receipt = _valid_receipt()
    receipt["tested_disposable_source_sha256"] = sha(harness)
    destination = tmp_path / "qualification.json"

    with pytest.raises(QualificationFailure, match="harness_source_changed_during_qualification"):
        _publish_receipt(
            receipt,
            initial_harness=harness,
            current_harness=harness + b" changed",
            output=destination,
        )
    assert not destination.exists()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("provider_requests",), 1),
        (("configured_kingston_touched",), True),
        (("tested_disposable_identity", "volume_uuid"), "lease-uuid"),
        (("tested_disposable_identity", "fs_type"), "APFS"),
        (("tested_disposable_identity", "case_sensitive"), False),
        (("forced_detach_points", 0, "provider_loader_calls"), 1),
        (("forced_detach_points", 0, "fallback_artifacts"), ["ResearchData/services/v2-live"]),
        (("forced_detach_points", 0, "safe_syscall_failure"), False),
        (("forced_detach_points", 0, "no_retry_or_replay"), False),
        (("capacity_override_test_only",), False),
        (("capacity_qualified",), True),
        (("real_capacity_precheck_rejected",), False),
        (("fallback_artifacts",), ["internal-fallback"]),
        (("temporary_root_removed",), False),
    ],
)
def test_receipt_rejects_unsafe_or_conflated_evidence(
    path: tuple[object, ...], value: object
) -> None:
    receipt = copy.deepcopy(_valid_receipt())
    target = receipt
    for part in path[:-1]:
        target = target[part]  # type: ignore[index,assignment]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises(QualificationFailure):
        validate_receipt(receipt)


def test_receipt_rejects_missing_evidence() -> None:
    receipt = _valid_receipt()
    del receipt["tested_disposable_identity"]
    with pytest.raises(QualificationFailure, match="receipt_fields"):
        validate_receipt(receipt)
