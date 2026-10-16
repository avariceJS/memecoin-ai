from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import memecoin_research_storage as storage
from memecoin_research_storage import StorageGuard
from tools import brain_host_tree_capture_v5_production as production_module
from tools.brain_host_held_data_t_v1 import DarwinDataOS, HeldDataT
from tools.brain_host_tree_capture_v5 import (
    REQUIRED_ROOT_ROLES,
    RoleCapturePolicy,
    TreeCaptureV5Error,
    verify_candidate_v5,
)
from tools.brain_host_tree_capture_v5_production import (
    CAPTURE_SPEC_SCHEMA,
    PinnedScratchV5,
    ProductionCaptureV5CLIError,
    ProductionCaptureV5Error,
    _assert_writer_source_separation,
    _capture_failure,
    _capture_summary,
    _load_capture_policies,
    _preflight_policy_roster,
    _read_private_capture_spec,
    _require_real_held_view,
    _required_boundary_callback,
    _validate_pins,
    _validate_scratch_devices,
    _verify_scratch_required_free_bytes,
    extract_production_candidate_inert_v5,
    production_capture_v5,
    verify_production_candidate_v5,
)
from tools.brain_host_tree_capture_v5_production import (
    main as production_cli_main,
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


def _inert_cli_synthetic_args() -> list[str]:
    args = ["extract-inert", "a" * 32]
    for name in (
        "index-sha256",
        "plan-sha256",
        "source-data-volume-uuid",
        "source-plan",
        "target-storage-config",
        "target-volume-uuid",
        "target-root-id",
        "destination-storage-config",
        "destination-volume-uuid",
        "destination-root-id",
        "scratch-parent",
    ):
        args.extend([f"--{name}", "secret-token"])
    return args


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


def test_inert_production_rejects_source_target_destination_uuid_alias_before_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("storage or candidate must not be opened before pin validation")

    monkeypatch.setattr(storage, "read_only_guard", forbidden)
    with pytest.raises(ProductionCaptureV5Error, match="production_extract_volume_uuid_alias"):
        extract_production_candidate_inert_v5(
            "a" * 32,
            target_storage_config=Path("/does/not/exist-target.json"),
            expected_target_volume_uuid="11111111-2222-3333-4444-555555555555",
            expected_target_root_id="a" * 32,
            destination_storage_config=Path("/does/not/exist-destination.json"),
            expected_destination_volume_uuid="11111111-2222-3333-4444-555555555555",
            expected_destination_root_id="b" * 32,
            scratch=object(),  # type: ignore[arg-type]
            expected_index_sha256="0" * 64,
            expected_plan_sha256="1" * 64,
            expected_source_data_volume_uuid="22222222-3333-4444-5555-666666666666",
            source_plan_path=Path("/does/not/exist-plan.json"),
        )


def test_inert_production_binds_source_uuid_to_externally_pinned_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = SimpleNamespace(data_volume_uuid="22222222-3333-4444-5555-666666666666")
    monkeypatch.setattr(
        production_module,
        "_load_plan",
        lambda _path: plan,
    )
    monkeypatch.setattr(
        production_module,
        "approved_data_plan_sha256",
        lambda _plan: "2" * 64,
    )
    source_plan_path = tmp_path / "pinned-plan.json"
    source_plan_path.write_text("{}", encoding="utf-8")
    with pytest.raises(
        ProductionCaptureV5Error, match="production_extract_source_plan_pin_mismatch"
    ):
        extract_production_candidate_inert_v5(
            "a" * 32,
            target_storage_config=Path("/does/not/exist-target.json"),
            expected_target_volume_uuid="11111111-2222-3333-4444-555555555555",
            expected_target_root_id="a" * 32,
            destination_storage_config=Path("/does/not/exist-destination.json"),
            expected_destination_volume_uuid="33333333-4444-5555-6666-777777777777",
            expected_destination_root_id="b" * 32,
            scratch=object(),  # type: ignore[arg-type]
            expected_index_sha256="0" * 64,
            expected_plan_sha256="1" * 64,
            expected_source_data_volume_uuid=plan.data_volume_uuid,
            source_plan_path=source_plan_path,
        )


def test_extract_inert_cli_reports_unaccepted_partial_quarantine_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_scratch_open(cls: type[PinnedScratchV5], path: Path) -> PinnedScratchV5:
        del cls, path
        raise ProductionCaptureV5Error("secrettoken123")

    monkeypatch.setattr(PinnedScratchV5, "open", classmethod(fail_scratch_open))
    result = production_cli_main(
        [
            "extract-inert",
            "a" * 32,
            "--index-sha256",
            "0" * 64,
            "--plan-sha256",
            "1" * 64,
            "--source-data-volume-uuid",
            "22222222-3333-4444-5555-666666666666",
            "--source-plan",
            str(tmp_path / "missing-plan.json"),
            "--target-storage-config",
            str(tmp_path / "missing-target.json"),
            "--target-volume-uuid",
            "11111111-2222-3333-4444-555555555555",
            "--target-root-id",
            "a" * 32,
            "--destination-storage-config",
            str(tmp_path / "missing-destination.json"),
            "--destination-volume-uuid",
            "33333333-4444-5555-6666-777777777777",
            "--destination-root-id",
            "b" * 32,
            "--scratch-parent",
            str(tmp_path / "missing-scratch"),
        ]
    )
    assert result == 2
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["error"] == "extract_inert_failed"
    assert payload["next_step"] == "inspect_local_failure_without_starting"
    assert payload["ok"] is False
    assert payload["quarantine_rehearsal_only"] is True
    assert payload["unaccepted_partial_quarantine_may_remain"] is True
    assert payload["start_or_restore_performed"] is False
    assert payload["restore_verified"] is False
    assert payload["T_qualified"] is False
    assert payload["erase_ready"] is False
    assert "secrettoken123" not in output


@pytest.mark.parametrize(
    ("failure", "category", "next_step"),
    [
        (
            ProductionCaptureV5Error("target_storage_pin_invalid"),
            "input_pin_invalid",
            "check_external_pins_and_arguments",
        ),
        (
            ProductionCaptureV5Error("production_extract_source_plan_pin_mismatch"),
            "plan_or_storage_pin_mismatch",
            "reconcile_pinned_plan_and_storage",
        ),
        (
            ProductionCaptureV5Error("production_extract_target_not_read_only"),
            "target_not_read_only",
            "remount_candidate_volume_read_only",
        ),
        (
            ProductionCaptureV5Error("production_extract_volume_uuid_alias"),
            "volume_separation_failed",
            "choose_distinct_pinned_volumes",
        ),
        (
            ProductionCaptureV5Error("scratch_path_open_failed"),
            "scratch_unavailable",
            "prepare_private_pinned_scratch",
        ),
        (
            TreeCaptureV5Error("inert_destination_directory_invalid"),
            "destination_unavailable",
            "prepare_private_pinned_destination",
        ),
        (
            storage.StorageError("insufficient_free_space"),
            "capacity_below_reserve",
            "provide_capacity_above_reserve",
        ),
        (
            TreeCaptureV5Error("candidate_data_hash_mismatch"),
            "candidate_integrity_failed",
            "reverify_candidate_from_trusted_pins",
        ),
        (
            TreeCaptureV5Error("inert_readback_content_mismatch"),
            "quarantine_readback_failed",
            "inspect_unaccepted_quarantine_without_starting",
        ),
        (
            ProductionCaptureV5Error("production_extract_target_not_read_only secret-token"),
            "extract_inert_failed",
            "inspect_local_failure_without_starting",
        ),
        (
            TreeCaptureV5Error("candidate_data_hash_mismatch /private/secret-token"),
            "extract_inert_failed",
            "inspect_local_failure_without_starting",
        ),
        (
            ValueError("production_extract_target_not_read_only"),
            "extract_inert_failed",
            "inspect_local_failure_without_starting",
        ),
        (
            OSError("/private/secret-token"),
            "extract_inert_failed",
            "inspect_local_failure_without_starting",
        ),
        (
            storage.StorageError("volume_not_writable /private/secret-token"),
            "extract_inert_failed",
            "inspect_local_failure_without_starting",
        ),
    ],
)
def test_extract_inert_cli_uses_only_static_safe_diagnostics(
    failure: Exception,
    category: str,
    next_step: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = []
    monkeypatch.setattr(
        PinnedScratchV5,
        "open",
        classmethod(lambda _cls, _path: SimpleNamespace(close=lambda: closed.append(True))),
    )
    partial = tmp_path / "unaccepted-partial-quarantine"

    def fail_extraction(*_args: object, **_kwargs: object) -> None:
        partial.mkdir(mode=0o700)
        (partial / "unaccepted-bytes").write_bytes(b"synthetic unaccepted payload")
        raise failure

    monkeypatch.setattr(production_module, "extract_production_candidate_inert_v5", fail_extraction)
    assert production_cli_main(_inert_cli_synthetic_args()) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "secret-token" not in captured.out
    assert "/private/" not in captured.out
    payload = json.loads(captured.out)
    assert payload["error"] == category
    assert payload["next_step"] == next_step
    assert payload["unaccepted_partial_quarantine_may_remain"] is True
    assert payload["start_or_restore_performed"] is False
    assert payload["restore_verified"] is False
    assert payload["T_qualified"] is False
    assert payload["erase_ready"] is False
    assert closed == [True]
    assert (partial / "unaccepted-bytes").read_bytes() == b"synthetic unaccepted payload"


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (
            ProductionCaptureV5Error("production_extract_target_acl_present"),
            "extract_inert_failed",
        ),
        (
            ProductionCaptureV5Error("production_extract_mount_identity_invalid"),
            "extract_inert_failed",
        ),
        (
            ProductionCaptureV5Error("production_extract_source_mount_unresolved"),
            "extract_inert_failed",
        ),
        (
            ProductionCaptureV5Error("production_verify_read_only_candidate_guard_required"),
            "extract_inert_failed",
        ),
        (
            ProductionCaptureV5Error("production_extract_capacity_request_invalid"),
            "extract_inert_failed",
        ),
        (
            ProductionCaptureV5Error("production_extract_capacity_below_reserve"),
            "capacity_below_reserve",
        ),
        (
            ProductionCaptureV5Error("production_extract_scratch_usage_invalid"),
            "scratch_unavailable",
        ),
        (TreeCaptureV5Error("inert_member_kind_invalid"), "candidate_integrity_failed"),
        (TreeCaptureV5Error("inert_destination_directory_collision"), "extract_inert_failed"),
        (TreeCaptureV5Error("inert_extraction_scratch_limit"), "scratch_unavailable"),
        (TreeCaptureV5Error("inert_extraction_scratch_identity_invalid"), "scratch_unavailable"),
        (storage.StorageError("binding_changed_during_read"), "plan_or_storage_pin_mismatch"),
        (storage.StorageError("binding_changed_under_lock"), "plan_or_storage_pin_mismatch"),
        (storage.StorageError("root_marker_binding_mismatch"), "plan_or_storage_pin_mismatch"),
        (storage.StorageError("mounted_directory_replaced"), "plan_or_storage_pin_mismatch"),
        (storage.StorageError("volume_not_writable"), "destination_unavailable"),
        (storage.StorageError("writer_lock_replaced"), "destination_unavailable"),
        (storage.StorageError("volume_owners_disabled"), "extract_inert_failed"),
        (storage.StorageError("mount_missing"), "extract_inert_failed"),
    ],
)
def test_extract_inert_guard_diagnostics_preserve_accurate_failure_categories(
    failure: Exception, category: str
) -> None:
    payload = production_module._extract_failure(failure)
    assert payload["error"] == category
    assert payload["next_step"] == production_module._EXTRACT_NEXT_STEPS[category]
    assert payload["unaccepted_partial_quarantine_may_remain"] is True
    assert payload["start_or_restore_performed"] is False


def test_extract_inert_cli_redacts_scratch_close_failure_before_success_output(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_close() -> None:
        raise OSError("secret-token /private/member-name")

    monkeypatch.setattr(
        PinnedScratchV5,
        "open",
        classmethod(lambda _cls, _path: SimpleNamespace(close=fail_close)),
    )
    monkeypatch.setattr(
        production_module,
        "extract_production_candidate_inert_v5",
        lambda *_args, **_kwargs: {"ok": True},
    )
    assert production_cli_main(_inert_cli_synthetic_args()) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "secret-token" not in captured.out
    assert "/private/member-name" not in captured.out
    payload = json.loads(captured.out)
    assert payload["error"] == "extract_inert_failed"
    assert payload["ok"] is False
    assert payload["unaccepted_partial_quarantine_may_remain"] is True


def test_extract_inert_cli_argument_failure_is_static_and_inert(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert production_cli_main(["extract-inert", "/private/secret-token"]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "secret-token" not in captured.out
    payload = json.loads(captured.out)
    assert payload["error"] == "input_pin_invalid"
    assert payload["unaccepted_partial_quarantine_may_remain"] is True
    assert payload["start_or_restore_performed"] is False


def test_extract_inert_diagnostics_reject_subclasses_and_nonliteral_arguments() -> None:
    class UntrustedError(ProductionCaptureV5Error):
        def __str__(self) -> str:
            raise AssertionError("must not format exception")

    class UntrustedString(str):
        def __eq__(self, other: object) -> bool:
            raise AssertionError("must not compare untrusted message objects")

    for failure in (
        UntrustedError("production_extract_target_not_read_only"),
        ProductionCaptureV5Error(UntrustedString("production_extract_target_not_read_only")),
        ProductionCaptureV5Error("production_extract_target_not_read_only", "secret-token"),
    ):
        payload = production_module._extract_failure(failure)
        assert payload["error"] == "extract_inert_failed"
        assert "secret-token" not in json.dumps(payload)


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


@pytest.mark.parametrize("container_reference", [None, "not-a-disk-reference"])
def test_apfs_scratch_requires_resolvable_container_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    container_reference: str | None,
) -> None:
    scratch_path = tmp_path / "scratch"
    scratch_path.mkdir(mode=0o700)
    monkeypatch.setattr(
        production_module.DarwinDataOS,
        "disk_info",
        lambda _self, _target: {
            "VolumeUUID": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
            "APFSContainerReference": container_reference,
        },
    )
    with pytest.raises(
        ProductionCaptureV5Error,
        match="scratch_volume_uuid_unresolved",
    ):
        PinnedScratchV5.open(scratch_path)


def test_shared_pool_scratch_floor_tracks_remaining_db_and_policy_reserve() -> None:
    from tools.brain_host_tree_capture_v5_production import (
        VERIFY_SCRATCH_DB_RESERVE_BYTES,
        VERIFY_SCRATCH_FREE_FLOOR_BYTES,
        VERIFY_SHARED_POOL_FLOOR_BYTES,
    )

    headroom = 3 * 1024**2
    assert (
        _verify_scratch_required_free_bytes(0, VERIFY_SCRATCH_DB_RESERVE_BYTES)
        == VERIFY_SCRATCH_FREE_FLOOR_BYTES + VERIFY_SCRATCH_DB_RESERVE_BYTES
    )
    assert _verify_scratch_required_free_bytes(
        0,
        VERIFY_SCRATCH_DB_RESERVE_BYTES,
        shared_pool_mode=True,
        shared_pool_headroom_bytes=headroom,
    ) == (
        VERIFY_SCRATCH_FREE_FLOOR_BYTES
        + VERIFY_SHARED_POOL_FLOOR_BYTES
        + VERIFY_SCRATCH_DB_RESERVE_BYTES
        + headroom
    )
    used = 7 * 1024**3
    assert _verify_scratch_required_free_bytes(
        used,
        VERIFY_SCRATCH_DB_RESERVE_BYTES,
        shared_pool_mode=True,
        shared_pool_headroom_bytes=headroom,
    ) == (
        VERIFY_SCRATCH_FREE_FLOOR_BYTES
        + VERIFY_SHARED_POOL_FLOOR_BYTES
        + VERIFY_SCRATCH_DB_RESERVE_BYTES
        - used
        + headroom
    )
    with pytest.raises(ProductionCaptureV5Error, match="production_verify_scratch_usage_invalid"):
        _verify_scratch_required_free_bytes(
            VERIFY_SCRATCH_DB_RESERVE_BYTES + 1,
            VERIFY_SCRATCH_DB_RESERVE_BYTES,
            shared_pool_mode=True,
        )


@pytest.mark.parametrize(
    (
        "concurrent_pool_free",
        "target_becomes_writable",
        "shared_pool_mode",
        "scratch_container_pin_valid",
        "target_container_pin_valid",
        "scratch_filesystem",
    ),
    [
        (100 * 1024**3, False, True, True, True, "apfs"),
        (50 * 1024**3 + 32 * 1024**3 + 256 * 1024**2 - 1, False, True, True, True, "apfs"),
        (100 * 1024**3, True, True, True, True, "apfs"),
        (100 * 1024**3, False, False, True, True, "apfs"),
        (100 * 1024**3, False, True, False, True, "apfs"),
        (100 * 1024**3, False, True, True, False, "apfs"),
        (100 * 1024**3, False, False, True, False, "apfs"),
        (100 * 1024**3, False, False, True, True, "hfs"),
    ],
)
def test_shared_pool_verifier_checks_dynamic_floor_before_candidate_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    concurrent_pool_free: int,
    target_becomes_writable: bool,
    shared_pool_mode: bool,
    scratch_container_pin_valid: bool,
    target_container_pin_valid: bool,
    scratch_filesystem: str,
) -> None:
    from types import SimpleNamespace

    from memecoin_research_storage import ReadOnlyStorageGuard
    from tools.brain_host_tree_capture_v5_production import (
        VERIFY_SCRATCH_DB_RESERVE_BYTES,
        VERIFY_SCRATCH_FREE_FLOOR_BYTES,
        VERIFY_SHARED_POOL_FLOOR_BYTES,
    )

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    scratch_path = tmp_path / "scratch"
    scratch_path.mkdir(mode=0o700)
    (target / "sentinel").write_bytes(b"read-only target evidence")
    sentinel_before = hashlib.sha256((target / "sentinel").read_bytes()).hexdigest()
    mount_fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    scratch_fd = os.open(scratch_path, os.O_RDONLY | os.O_DIRECTORY)
    container_uuid = "11111111-2222-3333-4444-555555555555"
    target_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    root_id = "a" * 32
    container_reference = "disk55"
    guard = object.__new__(ReadOnlyStorageGuard)
    guard.config = SimpleNamespace(
        root_id=root_id,
        volume_uuid=target_uuid,
        mount_path=target,
        data_root=".",
    )
    guard.root = SimpleNamespace(root_id=root_id, volume_uuid=target_uuid)
    guard.mount_fd = mount_fd
    guard.revalidate = lambda: None
    writer = SimpleNamespace(
        storage_guard=guard,
        _read_only=True,
        device=os.fstat(mount_fd).st_dev,
        assert_storage_binding=lambda _guard: None,
    )
    scratch = PinnedScratchV5(
        path=scratch_path,
        fd=scratch_fd,
        identity=(os.fstat(scratch_fd).st_dev, os.fstat(scratch_fd).st_ino),
        chain_identities=(),
        filesystem_identity=(scratch_filesystem, (1, 2), str(scratch_path), "/dev/disk55s2"),
        volume_uuid="bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
        apfs_container_reference=container_reference,
        apfs_container_uuid=container_uuid,
    )
    if not scratch_container_pin_valid:
        scratch.apfs_container_reference = None
        scratch.apfs_container_uuid = None
    if scratch_filesystem == "hfs":
        scratch.apfs_container_reference = None
        scratch.apfs_container_uuid = None
    validations: list[int] = []
    scratch.validate = lambda **kwargs: validations.append(kwargs["required_free_bytes"]) or 0
    monkeypatch.setattr(
        production_module.DarwinDataOS,
        "disk_info",
        lambda _self, _target: (
            {"APFSContainerReference": container_reference} if target_container_pin_valid else {}
        ),
    )
    target_fs_checks = 0

    def fake_target_filesystem(_self, _fd):
        nonlocal target_fs_checks
        target_fs_checks += 1
        writable = target_becomes_writable and target_fs_checks > 1
        return {
            "flags": 0 if writable else getattr(os, "ST_RDONLY", 1),
            "fs_type": "apfs",
        }

    monkeypatch.setattr(production_module.DarwinDataOS, "filesystem", fake_target_filesystem)
    monkeypatch.setattr(
        production_module,
        "_apfs_container_uuid",
        lambda _reference: container_uuid,
    )
    monkeypatch.setattr(
        production_module, "_validate_scratch_target_separation", lambda *_args: None
    )
    monkeypatch.setattr(
        production_module.os,
        "fstatvfs",
        lambda _fd: SimpleNamespace(
            f_flag=getattr(os, "ST_RDONLY", 1),
            f_bavail=concurrent_pool_free,
            f_frsize=1,
        ),
    )
    candidate_reads: list[bool] = []
    observed_operation_spec_hashes: list[object] = []

    def fake_candidate_verify(*_args: object, scratch_capacity_check, **_kwargs: object):
        scratch_capacity_check(0, VERIFY_SCRATCH_DB_RESERVE_BYTES)
        candidate_reads.append(True)
        observed_operation_spec_hashes.append(_kwargs.get("expected_operation_spec_sha256"))
        return {"synthetic_fixture_only": False, "index_sha256": "0" * 64}

    monkeypatch.setattr(production_module.capture, "verify_candidate_v5", fake_candidate_verify)
    try:
        if scratch_filesystem == "hfs":
            result = verify_production_candidate_v5(
                writer,
                "c" * 32,
                expected_target_volume_uuid=target_uuid,
                expected_target_root_id=root_id,
                scratch=scratch,
                expected_index_sha256="0" * 64,
                expected_plan_sha256="d" * 64,
                expected_operation_spec_sha256="c" * 64,
                expected_source_data_volume_uuid="99999999-8888-7777-6666-555555555555",
            )
            assert result["shared_pool_container_uuid"] is None
            assert candidate_reads == [True]
            assert observed_operation_spec_hashes == ["c" * 64]
        elif not scratch_container_pin_valid:
            with pytest.raises(
                ProductionCaptureV5Error,
                match="scratch_volume_uuid_unresolved",
            ):
                verify_production_candidate_v5(
                    writer,
                    "c" * 32,
                    expected_target_volume_uuid=target_uuid,
                    expected_target_root_id=root_id,
                    scratch=scratch,
                    expected_index_sha256="0" * 64,
                    expected_plan_sha256="d" * 64,
                    expected_shared_apfs_container_uuid=container_uuid,
                    expected_operation_spec_sha256="c" * 64,
                )
            assert candidate_reads == []
        elif not target_container_pin_valid:
            with pytest.raises(
                ProductionCaptureV5Error,
                match="production_verify_shared_pool_identity_invalid",
            ):
                verify_production_candidate_v5(
                    writer,
                    "c" * 32,
                    expected_target_volume_uuid=target_uuid,
                    expected_target_root_id=root_id,
                    scratch=scratch,
                    expected_index_sha256="0" * 64,
                    expected_plan_sha256="d" * 64,
                    expected_shared_apfs_container_uuid=container_uuid,
                    expected_operation_spec_sha256="c" * 64,
                )
            assert candidate_reads == []
        elif not shared_pool_mode:
            with pytest.raises(
                ProductionCaptureV5Error,
                match="production_verify_shared_pool_pin_invalid",
            ):
                verify_production_candidate_v5(
                    writer,
                    "c" * 32,
                    expected_target_volume_uuid=target_uuid,
                    expected_target_root_id=root_id,
                    scratch=scratch,
                    expected_index_sha256="0" * 64,
                    expected_plan_sha256="d" * 64,
                )
            assert candidate_reads == []
        elif target_becomes_writable:
            with pytest.raises(
                ProductionCaptureV5Error,
                match="production_verify_candidate_target_not_read_only",
            ):
                verify_production_candidate_v5(
                    writer,
                    "c" * 32,
                    expected_target_volume_uuid=target_uuid,
                    expected_target_root_id=root_id,
                    scratch=scratch,
                    expected_index_sha256="0" * 64,
                    expected_plan_sha256="d" * 64,
                    expected_source_data_volume_uuid="99999999-8888-7777-6666-555555555555",
                    expected_shared_apfs_container_uuid=container_uuid,
                    expected_operation_spec_sha256="c" * 64,
                )
            assert candidate_reads == []
        elif concurrent_pool_free < (
            VERIFY_SHARED_POOL_FLOOR_BYTES
            + VERIFY_SCRATCH_FREE_FLOOR_BYTES
            + VERIFY_SCRATCH_DB_RESERVE_BYTES
        ):
            with pytest.raises(
                ProductionCaptureV5Error,
                match="production_verify_shared_pool_reserve_invalid",
            ):
                verify_production_candidate_v5(
                    writer,
                    "c" * 32,
                    expected_target_volume_uuid=target_uuid,
                    expected_target_root_id=root_id,
                    scratch=scratch,
                    expected_index_sha256="0" * 64,
                    expected_plan_sha256="d" * 64,
                    expected_source_data_volume_uuid="99999999-8888-7777-6666-555555555555",
                    expected_shared_apfs_container_uuid=container_uuid,
                    expected_operation_spec_sha256="c" * 64,
                )
            assert candidate_reads == []
        else:
            result = verify_production_candidate_v5(
                writer,
                "c" * 32,
                expected_target_volume_uuid=target_uuid,
                expected_target_root_id=root_id,
                scratch=scratch,
                expected_index_sha256="0" * 64,
                expected_plan_sha256="d" * 64,
                expected_source_data_volume_uuid="99999999-8888-7777-6666-555555555555",
                expected_shared_apfs_container_uuid=container_uuid,
                expected_operation_spec_sha256="c" * 64,
                shared_pool_headroom_bytes=4 * 1024**2,
            )
            assert (
                result["shared_pool_policy_floor_bytes"]
                == VERIFY_SHARED_POOL_FLOOR_BYTES + 4 * 1024**2
            )
            assert candidate_reads == [True]
            assert (
                VERIFY_SHARED_POOL_FLOOR_BYTES + 4 * 1024**2 + VERIFY_SCRATCH_FREE_FLOOR_BYTES
            ) in validations
    finally:
        os.close(scratch_fd)
        os.close(mount_fd)
    assert hashlib.sha256((target / "sentinel").read_bytes()).hexdigest() == sentinel_before


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


def _write_private_spec(path: Path, payload: bytes) -> str:
    path.write_bytes(payload)
    path.chmod(0o600)
    return hashlib.sha256(payload).hexdigest()


def test_capture_spec_requires_external_hash_private_mode_and_unique_json_keys(
    tmp_path: Path,
) -> None:
    spec = tmp_path / "operation.json"
    raw = json.dumps({"schema": CAPTURE_SPEC_SCHEMA}, separators=(",", ":")).encode()
    digest = _write_private_spec(spec, raw)
    assert _read_private_capture_spec(spec, digest) == {"schema": CAPTURE_SPEC_SCHEMA}
    with pytest.raises(ProductionCaptureV5CLIError, match="capture_spec_sha256_mismatch"):
        _read_private_capture_spec(spec, "0" * 64)
    spec.chmod(0o644)
    with pytest.raises(ProductionCaptureV5CLIError, match="capture_spec_private_file_invalid"):
        _read_private_capture_spec(spec, digest)
    duplicate = b'{"schema":"one","schema":"two"}'
    duplicate_digest = _write_private_spec(spec, duplicate)
    with pytest.raises(ProductionCaptureV5CLIError, match="capture_json_duplicate_key"):
        _read_private_capture_spec(spec, duplicate_digest)
    alias = tmp_path / "operation-alias.json"
    alias.symlink_to(spec)
    with pytest.raises(ProductionCaptureV5CLIError, match="capture_spec_path_invalid"):
        _read_private_capture_spec(alias, duplicate_digest)


def test_capture_spec_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "operation.fifo"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ProductionCaptureV5CLIError, match="capture_spec_private_file_invalid"):
        _read_private_capture_spec(fifo, "a" * 64)


def test_capture_policy_roster_rejects_absence_downgrade_and_scope_mutation(
    tmp_path: Path,
) -> None:
    from tools.brain_host_held_data_t_v1 import RootDeclaration
    from tools.brain_host_tree_capture_v5 import policy_roster_sha256_v5

    plan_sha = "d" * 64
    declarations = {
        role: RootDeclaration(
            present=role == "owner_checkout",
            relative_locator="root" if role == "owner_checkout" else None,
            kind="directory" if role == "owner_checkout" else None,
            unresolved_reason=None if role == "owner_checkout" else f"absent:{role}",
        )
        for role in REQUIRED_ROOT_ROLES
    }
    policy_value = {
        "schema": POLICY_SCHEMA,
        "policy_id": "cli-policy",
        "version": 1,
        "rule_set": "owner-work-sensitive-v1",
        "pem_candidate_dispositions": {},
        "exact_path_exclusions": {},
        "symlink_target_dispositions": {},
        "rebuild_inputs": [],
    }
    policy_path = tmp_path / "owner.policy.json"
    raw = canonical_json(policy_value)
    policy_path.write_bytes(raw)
    policy_sha = hashlib.sha256(raw).hexdigest()
    roster: dict[str, object] = {}
    digest_rows = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        if role == "owner_checkout":
            roster[role] = {
                "disposition": "present",
                "policy_path": str(policy_path),
                "policy_sha256": policy_sha,
                "scope": "non_protected",
            }
            digest_rows.append(
                {
                    "role": role,
                    "present": True,
                    "scope": "non_protected",
                    "policy_sha256": policy_sha,
                }
            )
        else:
            roster[role] = {
                "disposition": "absent_unresolved",
                "unresolved_reason": f"absent:{role}",
            }
            digest_rows.append(
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
    spec = {"role_roster": roster}
    policies, digest = _load_capture_policies(
        spec, SimpleNamespace(root_roster=declarations), plan_sha
    )
    assert set(policies) == {"owner_checkout"}
    assert digest == policy_roster_sha256_v5(plan_sha, digest_rows)

    roster["owner_codex_worktree"] = {
        "disposition": "absent_unresolved",
        "unresolved_reason": "silently-downgraded",
    }
    with pytest.raises(ProductionCaptureV5CLIError, match="capture_absent_role_reason_mismatch"):
        _load_capture_policies(spec, SimpleNamespace(root_roster=declarations), plan_sha)

    roster["owner_codex_worktree"] = {
        "disposition": "absent_unresolved",
        "unresolved_reason": "absent:owner_codex_worktree",
    }
    roster["owner_checkout"]["scope"] = "protected_opaque"
    _policies, changed_digest = _load_capture_policies(
        spec, SimpleNamespace(root_roster=declarations), plan_sha
    )
    assert changed_digest != digest


def test_capture_cli_pin_failure_is_path_free_and_has_false_authority(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = tmp_path / "private-operation.json"
    digest = _write_private_spec(spec, b'{"schema":"wrong"}')
    code = production_cli_main(
        [
            "capture",
            "--spec",
            str(spec),
            "--spec-sha256",
            digest,
            "--plan",
            str(tmp_path / "plan.json"),
            "--plan-sha256",
            "0" * 64,
            "--policy-roster-sha256",
            "1" * 64,
        ]
    )
    output = capsys.readouterr().out
    assert code == 2
    assert str(tmp_path) not in output
    assert '"capture_complete":false' in output
    assert '"erase_ready":false' in output
    assert '"activation_permitted":false' in output
    assert '"unaccepted_partial_candidate_may_remain":false' in output
    assert '"root_closure_verified":false' in output


def test_capacity_plan_cli_success_is_sizing_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def positive_plan(**kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {
            "schema": "brain-host-tree-capture-v5-capacity-plan-v1",
            "synthetic_fixture_only": True,
            "capacity_result_status": "sizing_only_unqualified",
            "payload_read": False,
            "kingston_written": False,
            "capture_authorized": False,
            "erase_ready": False,
        }

    monkeypatch.setattr(production_module, "_capacity_plan_from_pinned_spec", positive_plan)
    code = production_cli_main(
        [
            "plan-capacity",
            "--spec",
            str(tmp_path / "private-operation.json"),
            "--spec-sha256",
            "a" * 64,
            "--plan",
            str(tmp_path / "plan.json"),
            "--plan-sha256",
            "b" * 64,
            "--policy-roster-sha256",
            "c" * 64,
            "--scratch-parent",
            str(tmp_path / "scratch"),
        ]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert seen["expected_spec_sha256"] == "a" * 64
    assert seen["expected_plan_sha256"] == "b" * 64
    assert '"capacity_result_status":"sizing_only_unqualified"' in output
    assert '"payload_read":false' in output
    assert '"kingston_written":false' in output
    assert '"capture_authorized":false' in output
    assert '"erase_ready":false' in output


def test_capture_result_is_provenance_bound_and_never_authorizes_activation() -> None:
    result = _capture_summary(
        {
            "candidate_id": "a" * 32,
            "index_sha256": "b" * 64,
            "entry_count": 7,
            "payload_bytes": 12,
            "readback_verified": True,
            "synthetic_fixture_only": False,
        },
        operation_spec_sha256="c" * 64,
        plan_sha256="d" * 64,
        policy_roster_sha256="e" * 64,
    )
    assert result["operation_spec_sha256"] == "c" * 64
    assert result["plan_sha256"] == "d" * 64
    assert result["policy_roster_sha256"] == "e" * 64
    assert result["capture_complete"] is False
    assert result["erase_ready"] is False
    assert result["activation_permitted"] is False
    failure = _capture_failure("candidate_write_failed", partial_candidate_possible=True)
    assert failure["unaccepted_partial_candidate_may_remain"] is True
    assert failure["activation_permitted"] is False
    secret_failure = _capture_failure("secrettoken123", partial_candidate_possible=True)
    assert secret_failure["error"] == "capture_failed"
    assert secret_failure["unaccepted_partial_candidate_may_remain"] is True


def test_capture_cli_argument_errors_are_path_free(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = production_cli_main(["capture", "--spec", "/private/secret/path.json"])
    output = capsys.readouterr().out
    assert code == 2
    assert "/private/secret" not in output
    assert '"error":"arguments_invalid"' in output


def test_verify_cli_does_not_echo_arbitrary_exception_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "storage.json"
    config.write_text("{}", encoding="utf-8")

    def fail_guard(*_args: object, **_kwargs: object) -> None:
        raise ValueError("secrettoken123")

    monkeypatch.setattr(storage, "read_only_guard", fail_guard)
    code = production_cli_main(
        [
            "verify",
            "a" * 32,
            "--index-sha256",
            "b" * 64,
            "--storage-config",
            str(config),
            "--target-volume-uuid",
            "11111111-2222-3333-4444-555555555555",
            "--target-root-id",
            "c" * 32,
            "--plan-sha256",
            "d" * 64,
            "--scratch-parent",
            str(tmp_path),
        ]
    )
    error = capsys.readouterr().err
    assert code == 2
    assert "verify_failed" in error
    assert "secrettoken123" not in error
    assert str(tmp_path) not in error


def test_capture_cli_reports_partial_candidate_as_unaccepted_and_path_free(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.brain_host_held_data_t_v1 import (
        ApprovedDataPlan,
        RootDeclaration,
        approved_data_plan_sha256,
    )
    from tools.brain_host_tree_capture_v5 import policy_roster_sha256_v5

    source_uuid = "11111111-2222-3333-4444-555555555555"
    target_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    declarations = {
        role: (
            RootDeclaration(True, "root", "directory", None)
            if role == "owner_checkout"
            else RootDeclaration(False, None, None, f"absent_{role}")
        )
        for role in REQUIRED_ROOT_ROLES
    }
    plan = ApprovedDataPlan(
        "synthetic-cli-partial-failure",
        source_uuid,
        "disk99s1",
        "disk99",
        source_uuid,
        declarations,
    )
    plan_document = {
        "schema": "brain-host-approved-data-plan-v1",
        "approval_id": plan.approval_id,
        "data_volume_uuid": plan.data_volume_uuid,
        "data_device_identifier": plan.data_device_identifier,
        "data_container_reference": plan.data_container_reference,
        "data_volume_group_id": plan.data_volume_group_id,
        "root_roster": {
            role: {
                "present": declaration.present,
                "relative_locator": declaration.relative_locator,
                "kind": declaration.kind,
                "unresolved_reason": declaration.unresolved_reason,
            }
            for role, declaration in declarations.items()
        },
    }
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan_document), encoding="utf-8")
    plan_sha = approved_data_plan_sha256(plan)

    policy_path = tmp_path / "policy.json"
    policy_raw = canonical_json(
        {
            "schema": POLICY_SCHEMA,
            "policy_id": "injected-partial-failure",
            "version": 1,
            "rule_set": "owner-work-sensitive-v1",
            "pem_candidate_dispositions": {},
            "exact_path_exclusions": {},
            "symlink_target_dispositions": {},
            "rebuild_inputs": [],
        }
    )
    policy_path.write_bytes(policy_raw)
    policy_sha = hashlib.sha256(policy_raw).hexdigest()
    role_roster = {}
    digest_rows = []
    for role in sorted(REQUIRED_ROOT_ROLES):
        declaration = declarations[role]
        if declaration.present:
            role_roster[role] = {
                "disposition": "present",
                "policy_path": str(policy_path),
                "policy_sha256": policy_sha,
                "scope": "non_protected",
            }
            digest_rows.append(
                {
                    "role": role,
                    "present": True,
                    "scope": "non_protected",
                    "policy_sha256": policy_sha,
                }
            )
        else:
            role_roster[role] = {
                "disposition": "absent_unresolved",
                "unresolved_reason": declaration.unresolved_reason,
            }
            digest_rows.append(
                {
                    "role": role,
                    "present": False,
                    "disposition": "absent_unresolved",
                    "unresolved_reason_sha256": hashlib.sha256(
                        declaration.unresolved_reason.encode()
                    ).hexdigest(),
                    "scope": None,
                    "policy_sha256": None,
                }
            )
    policy_roster_sha = policy_roster_sha256_v5(plan_sha, digest_rows)
    config_path = tmp_path / "storage.json"
    config_path.write_text("{}", encoding="utf-8")
    spec_document = {
        "schema": CAPTURE_SPEC_SCHEMA,
        "source_mountpoint": str(tmp_path),
        "storage_config_path": str(config_path),
        "target_volume_uuid": target_uuid,
        "target_root_id": "b" * 32,
        "scratch_reserve_bytes": 1,
        "metadata_reserve_bytes": 0,
        "payload_reserve_bytes": 0,
        "role_roster": role_roster,
    }
    spec_path = tmp_path / "operation.json"
    spec_raw = json.dumps(spec_document, sort_keys=True, separators=(",", ":")).encode()
    spec_sha = _write_private_spec(spec_path, spec_raw)

    class FakeHeldView:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> FakeHeldView:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FailingSession:
        def capture_candidate(self) -> dict[str, object]:
            raise ValueError("secrettoken123")

        def __enter__(self) -> FailingSession:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    @contextlib.contextmanager
    def fake_factory(*_args: object, **_kwargs: object):
        assert _kwargs["operation_spec_sha256"] == spec_sha
        yield FailingSession()

    monkeypatch.setattr(production_module, "HeldDataT", FakeHeldView)
    monkeypatch.setattr(production_module, "production_capture_v5", fake_factory)
    code = production_cli_main(
        [
            "capture",
            "--spec",
            str(spec_path),
            "--spec-sha256",
            spec_sha,
            "--plan",
            str(plan_path),
            "--plan-sha256",
            plan_sha,
            "--policy-roster-sha256",
            policy_roster_sha,
        ]
    )
    output = capsys.readouterr().out
    assert code == 2
    assert str(tmp_path) not in output
    assert '"error":"capture_failed"' in output
    assert "secrettoken123" not in output
    assert '"unaccepted_partial_candidate_may_remain":true' in output
    assert '"capture_complete":false' in output
    assert '"activation_permitted":false' in output

    class SuccessfulSession:
        def capture_candidate(self) -> dict[str, object]:
            return {
                "candidate_id": "a" * 32,
                "index_sha256": "b" * 64,
                "entry_count": 1,
                "payload_bytes": 1,
                "readback_verified": True,
                "synthetic_fixture_only": False,
            }

        def __enter__(self) -> SuccessfulSession:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    @contextlib.contextmanager
    def exit_failing_factory(*_args: object, **_kwargs: object):
        assert _kwargs["operation_spec_sha256"] == spec_sha
        yield SuccessfulSession()
        raise ValueError("private teardown detail")

    monkeypatch.setattr(production_module, "production_capture_v5", exit_failing_factory)
    code = production_cli_main(
        [
            "capture",
            "--spec",
            str(spec_path),
            "--spec-sha256",
            spec_sha,
            "--plan",
            str(plan_path),
            "--plan-sha256",
            plan_sha,
            "--policy-roster-sha256",
            policy_roster_sha,
        ]
    )
    output = capsys.readouterr().out
    assert code == 2
    assert str(tmp_path) not in output
    assert '"error":"capture_failed"' in output
    assert "private teardown detail" not in output
    assert '"unaccepted_partial_candidate_may_remain":true' in output


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
