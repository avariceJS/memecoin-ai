from __future__ import annotations

import hashlib
import json
import os

import pytest

from tools.brain_host_data_view_capture_v1 import (
    SCHEMA,
    DataViewCaptureError,
    _hash_capture_member,
    _json_no_duplicate_keys,
    _require_exact_member_set,
    _validate_capture_path_binding,
    _validate_control_pair,
    _verify_manifest_pin,
    capture_held_data_roots,
    main,
)
from tools.brain_host_held_data_t_v1 import HeldDataT


def test_capture_refuses_synthetic_held_view_before_destination_access() -> None:
    view = object.__new__(HeldDataT)
    view.synthetic = True
    view._closed = False
    with pytest.raises(DataViewCaptureError, match="real_held_data_view_required"):
        capture_held_data_roots(view, object(), ("owner_checkout",))


def test_control_json_rejects_duplicate_keys() -> None:
    with pytest.raises(DataViewCaptureError, match="control_json_duplicate_key"):
        _json_no_duplicate_keys(b'{"safe":true,"safe":false}')


def test_control_json_requires_object() -> None:
    with pytest.raises(DataViewCaptureError, match="control_json_not_object"):
        _json_no_duplicate_keys(b"[]")


def test_external_manifest_pin_rejects_drift() -> None:
    original = b'{"schema":"fixture"}\n'
    assert _verify_manifest_pin(original, hashlib.sha256(original).hexdigest())
    with pytest.raises(DataViewCaptureError, match="manifest_pin_mismatch"):
        _verify_manifest_pin(original + b" ", hashlib.sha256(original).hexdigest())


def test_exact_member_set_rejects_unmanifested_member() -> None:
    with pytest.raises(DataViewCaptureError, match="capture_membership_not_closed"):
        _require_exact_member_set(
            {"object-00000000.bin", "manifest.json", "COMPLETE", "extra"},
            {"object-00000000.bin"},
        )


def test_reopened_group_path_must_resolve_to_same_directory(tmp_path) -> None:
    held_path = tmp_path / "held"
    replacement_path = tmp_path / "replacement"
    held_path.mkdir(mode=0o700)
    replacement_path.mkdir(mode=0o700)
    held_fd = os.open(held_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    replacement_fd = os.open(replacement_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        held_stat = os.fstat(held_fd)
        with pytest.raises(DataViewCaptureError, match="capture_path_changed_during_verify"):
            _validate_capture_path_binding(held_stat, replacement_fd, set())
    finally:
        os.close(held_fd)
        os.close(replacement_fd)


def test_payload_verifier_rejects_hash_and_growth_drift(tmp_path) -> None:
    payload = tmp_path / "object.bin"
    payload.write_bytes(b"trusted")
    payload.chmod(0o600)
    fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        expected = {
            "device": os.fstat(fd).st_dev,
            "size_bytes": 7,
            "sha256": hashlib.sha256(b"trusted").hexdigest(),
        }
        _hash_capture_member(fd, "object.bin", expected)
        payload.write_bytes(b"tampered")
        with pytest.raises(DataViewCaptureError, match="capture_member_size_exceeds_manifest"):
            _hash_capture_member(fd, "object.bin", expected)
        payload.write_bytes(b"changed")
        expected["size_bytes"] = 7
        with pytest.raises(DataViewCaptureError, match="capture_member_hash_mismatch"):
            _hash_capture_member(fd, "object.bin", expected)
    finally:
        os.close(fd)


def test_control_pair_binds_exact_manifest_and_never_grants_authority() -> None:
    capture_id = "a" * 32
    plan_sha = "b" * 64
    manifest = {
        "schema": SCHEMA,
        "capture_id": capture_id,
        "source_plan_sha256": plan_sha,
        "source_volume_uuid": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "source_device": "disk9s1",
        "source_device_number": 9,
        "source_view_identity": {},
        "root_roster_complete": False,
        "unresolved_required_roles": {"verifier_worktree": {}},
        "firmlink_provenance_complete": False,
        "roots": [
            {
                "role": "owner_checkout",
                "source_locator": "roots/owner_checkout",
                "source_kind": "directory",
                "firmlink_provenance": "unknown_if_material",
                "metadata_restore_ready": False,
                "entries": [],
            }
        ],
        "entry_count": 0,
        "total_source_bytes": 0,
        "xattrs_acl_flags": "not_captured",
        "symlinks_hardlinks_special_files": "rejected",
        "sqlite_family": "rejected_requires_online_backup_receipt",
        "source_cutoff_verified": False,
        "T_qualified": False,
        "kingston_K_frontier_qualified": False,
        "brain_restore_verified": False,
        "activation_authorized": False,
        "safe_to_erase": False,
    }
    complete = {
        "schema": "brain-host-held-data-byte-capture-complete-v1",
        "capture_id": capture_id,
        "manifest_sha256": "d" * 64,
        "entry_count": 0,
        "selected_present_root_group_complete": True,
        "required_root_closure_complete": False,
        "complete_marker_scope": "selected_present_root_bytes_only",
        "T_qualified": False,
        "kingston_K_frontier_qualified": False,
        "brain_restore_verified": False,
        "activation_authorized": False,
        "safe_to_erase": False,
    }
    assert (
        _validate_control_pair(
            manifest,
            complete,
            capture_id=capture_id,
            expected_plan_sha256=plan_sha,
            manifest_sha256="d" * 64,
        )
        == set()
    )
    complete["manifest_sha256"] = "e" * 64
    with pytest.raises(DataViewCaptureError, match="capture_control_binding_mismatch"):
        _validate_control_pair(
            manifest,
            complete,
            capture_id=capture_id,
            expected_plan_sha256=plan_sha,
            manifest_sha256="d" * 64,
        )
    complete["manifest_sha256"] = "d" * 64
    complete["safe_to_erase"] = True
    with pytest.raises(DataViewCaptureError, match="capture_control_authority_flag_invalid"):
        _validate_control_pair(
            manifest,
            complete,
            capture_id=capture_id,
            expected_plan_sha256=plan_sha,
            manifest_sha256="d" * 64,
        )


def test_operator_cli_rejects_invalid_plan_pin_before_source_access(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = main(
        [
            "--plan",
            "/missing/plan.json",
            "--plan-sha256",
            "invalid",
            "--mountpoint",
            "/missing/data",
            "--storage-config",
            "/missing/storage.json",
        ]
    )
    assert status == 2
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "plan_sha256_invalid"
    assert output["safe_to_erase"] is False


def test_operator_verify_cli_rejects_invalid_pin_before_storage_access(
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = main(
        [
            "--plan-sha256",
            "invalid",
            "--storage-config",
            "/missing/storage.json",
            "--verify-capture-id",
            "a" * 32,
        ]
    )
    assert status == 2
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "plan_sha256_invalid"
    assert output["safe_to_erase"] is False
