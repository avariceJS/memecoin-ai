from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from memecoin_research_storage import StorageGuard
from tools.brain_host_held_data_t_v1 import DarwinDataOS, HeldDataT
from tools.brain_host_tree_capture_v5 import (
    REQUIRED_ROOT_ROLES,
    RoleCapturePolicy,
    TreeCaptureV5Error,
    verify_candidate_v5,
)
from tools.brain_host_tree_capture_v5_production import (
    PinnedScratchV5,
    ProductionCaptureV5Error,
    _assert_writer_source_separation,
    _preflight_policy_roster,
    _require_real_held_view,
    _required_boundary_callback,
    _validate_pins,
    _validate_scratch_devices,
    production_capture_v5,
    verify_production_candidate_v5,
)
from tools.brain_host_tree_custody_v3 import POLICY_SCHEMA
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json


def test_required_boundary_callback_forwards_reserve_to_full_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[object, ...]] = []

    def record_boundary(*args: object) -> None:
        observed.append(args)

    monkeypatch.setattr(
        "tools.brain_host_tree_capture_v5_production._full_boundary", record_boundary
    )
    view, guard, writer = object(), object(), object()
    callback = _required_boundary_callback(
        view,
        guard,
        writer,
        "11111111-2222-3333-4444-555555555555",
        "a" * 32,
        17,
    )
    callback(1234)
    assert observed == [
        (view, guard, writer, "11111111-2222-3333-4444-555555555555", "a" * 32, 17, 1234)
    ]
    with pytest.raises(ProductionCaptureV5Error, match="production_reserve_value_invalid"):
        callback(-1)


def test_production_factory_rejects_synthetic_view_before_storage_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    view = object.__new__(HeldDataT)
    view.synthetic = True
    view.os_api = object()

    def forbidden_storage_open(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("storage must not be opened for synthetic source")

    monkeypatch.setattr(
        "tools.brain_host_tree_capture_v5_production.storage.writer_guard", forbidden_storage_open
    )
    with pytest.raises(
        ProductionCaptureV5Error, match="production_source_view_not_real_or_unpinned"
    ):
        with production_capture_v5(
            view,
            tmp_path / "does-not-exist.json",
            expected_plan_sha256="0" * 64,
            expected_policy_roster_sha256="0" * 64,
            expected_target_volume_uuid="11111111-2222-3333-4444-555555555555",
            expected_target_root_id="a" * 32,
            role_policies={},
            scratch_reserve_bytes=1,
        ):
            pytest.fail("synthetic source cannot enter the production context")


@pytest.mark.parametrize(
    ("volume_uuid", "root_id"),
    [
        ("not-a-uuid", "a" * 32),
        ("11111111-2222-3333-4444-555555555555", "short"),
        ("11111111-2222-3333-4444-555555555555", "A" * 32),
    ],
)
def test_production_storage_pins_require_canonical_values(volume_uuid: str, root_id: str) -> None:
    with pytest.raises(ProductionCaptureV5Error, match="target_storage_pin_invalid"):
        _validate_pins(volume_uuid, root_id)


def test_real_view_requires_independent_plan_pin_before_revalidation() -> None:
    view = object.__new__(HeldDataT)
    view.synthetic = False
    view.os_api = object.__new__(DarwinDataOS)
    view.trusted_plan_sha256 = "a" * 64
    with pytest.raises(
        ProductionCaptureV5Error, match="production_source_view_not_real_or_unpinned"
    ):
        _require_real_held_view(view, "b" * 64)


def test_policy_roster_digest_binds_all_role_scopes_hashes_and_absences(
    tmp_path: Path,
) -> None:
    plan_sha = "d" * 64
    roster = {}
    for role in REQUIRED_ROOT_ROLES:
        roster[role] = SimpleNamespace(
            present=role == "owner_checkout",
            unresolved_reason=(None if role == "owner_checkout" else f"absent:{role}"),
        )
    plan = SimpleNamespace(plan=SimpleNamespace(root_roster=roster))
    policy_value = {
        "schema": POLICY_SCHEMA,
        "policy_id": "synthetic-roster-pin",
        "version": 1,
        "rule_set": "owner-work-sensitive-v1",
        "pem_candidate_dispositions": {},
        "exact_path_exclusions": {},
        "symlink_target_dispositions": {},
        "rebuild_inputs": [],
    }
    raw = canonical_json(policy_value)
    policy_path = tmp_path / "owner.policy.json"
    policy_path.write_bytes(raw)
    policies = {"owner_checkout": RoleCapturePolicy(policy_path, hashlib.sha256(raw).hexdigest())}
    expected_rows = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        if role == "owner_checkout":
            expected_rows.append(
                {
                    "role": role,
                    "present": True,
                    "scope": "non_protected",
                    "policy_sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        else:
            expected_rows.append(
                {
                    "role": role,
                    "present": False,
                    "disposition": "absent_unresolved",
                    "unresolved_reason_sha256": hashlib.sha256(
                        f"absent:{role}".encode()
                    ).hexdigest(),
                    "scope": None,
                    "policy_sha256": None,
                }
            )
    from tools.brain_host_tree_capture_v5 import policy_roster_sha256_v5

    digest = policy_roster_sha256_v5(plan_sha, expected_rows)
    assert (
        _preflight_policy_roster(
            plan,
            policies,
            plan_sha256=plan_sha,
            expected_policy_roster_sha256=digest,
        )
        == digest
    )
    with pytest.raises(ProductionCaptureV5Error, match="production_policy_roster_pin_mismatch"):
        _preflight_policy_roster(
            plan,
            policies,
            plan_sha256=plan_sha,
            expected_policy_roster_sha256="0" * 64,
        )
    policy_path.write_bytes(raw + b"\n")
    with pytest.raises(ProductionCaptureV5Error, match="production_policy_file_pin_invalid"):
        _preflight_policy_roster(
            plan,
            policies,
            plan_sha256=plan_sha,
            expected_policy_roster_sha256=digest,
        )


def test_scratch_capability_pins_no_follow_path_and_rejects_source_alias(
    tmp_path: Path,
) -> None:
    scratch_path = tmp_path / "scratch"
    scratch_path.mkdir(mode=0o700)
    scratch_path.chmod(0o700)
    capability = PinnedScratchV5.open(scratch_path)
    device = os.fstat(capability.fd).st_dev
    try:
        _validate_scratch_devices(device + 1, device + 2, device + 3)
        with pytest.raises(ProductionCaptureV5Error, match="scratch_device_separation_invalid"):
            _validate_scratch_devices(device, device, device + 1)
        capability.validate(
            source_device=device + 1,
            target_device=device + 2,
            required_free_bytes=0,
        )
        with pytest.raises(ProductionCaptureV5Error, match="scratch_device_separation_invalid"):
            capability.validate(
                source_device=device,
                target_device=device + 1,
                required_free_bytes=0,
            )
        moved = tmp_path / "moved-scratch"
        scratch_path.rename(moved)
        scratch_path.mkdir(mode=0o700)
        scratch_path.chmod(0o700)
        with pytest.raises(ProductionCaptureV5Error, match="scratch_identity_or_capacity_invalid"):
            capability.validate(
                source_device=device + 1,
                target_device=device + 2,
                required_free_bytes=0,
            )
    finally:
        capability.close()


def test_scratch_path_rejects_symlink_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    real.chmod(0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ProductionCaptureV5Error, match="scratch_path_open_failed"):
        PinnedScratchV5.open(alias)


def test_source_storage_separation_rejects_equal_volume_uuid_and_device(tmp_path: Path) -> None:
    directory = tmp_path / "root"
    directory.mkdir()
    root_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        source_device = os.fstat(root_fd).st_dev
        view = SimpleNamespace(
            plan=SimpleNamespace(data_volume_uuid="11111111-2222-3333-4444-555555555555"),
            root_fds={"owner_checkout": root_fd},
        )
        writer = SimpleNamespace(
            storage_volume_uuid="11111111-2222-3333-4444-555555555555",
            device=source_device + 1,
        )
        with pytest.raises(ProductionCaptureV5Error, match="source_storage_not_distinct"):
            _assert_writer_source_separation(view, writer, source_device)

        writer.storage_volume_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        writer.device = source_device
        with pytest.raises(ProductionCaptureV5Error, match="source_storage_not_distinct"):
            _assert_writer_source_separation(view, writer, source_device)
    finally:
        os.close(root_fd)


def test_production_verifier_rejects_unbound_writer_before_scratch_use(tmp_path: Path) -> None:
    root = tmp_path / "candidate-root"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    writer = DescriptorWriter(root_fd)
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    scratch_fd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(
            ProductionCaptureV5Error,
            match="production_verify_read_only_candidate_guard_required",
        ):
            verify_production_candidate_v5(
                writer,
                "c" * 32,
                expected_target_volume_uuid="11111111-2222-3333-4444-555555555555",
                expected_target_root_id="a" * 32,
                scratch=None,
                expected_index_sha256="0" * 64,
                expected_plan_sha256="d" * 64,
            )
        assert list(scratch.iterdir()) == []
    finally:
        os.close(scratch_fd)
        writer.close()


def test_bound_candidate_verifier_requires_explicit_scratch_fd(tmp_path: Path) -> None:
    root = tmp_path / "candidate-root"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    guard = object.__new__(StorageGuard)
    guard.root = SimpleNamespace(
        volume_uuid="11111111-2222-3333-4444-555555555555",
        root_id="a" * 32,
    )
    writer = DescriptorWriter(root_fd, storage_guard=guard)
    try:
        with pytest.raises(
            TreeCaptureV5Error, match="candidate_verify_scratch_capability_required"
        ):
            verify_candidate_v5(writer, "c" * 32)
    finally:
        writer.close()
        os.close(root_fd)
