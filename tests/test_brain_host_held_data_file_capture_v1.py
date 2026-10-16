from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tools.brain_host_held_data_file_capture_v1 import (
    ALLOWED_FILE_ROLES,
    POLICY_SCHEMA,
    HeldDataFileError,
    _source_fd,
    capture_held_data_file_unit_v1,
    preflight_held_data_file_roles_v1,
    read_file_policy,
    verify_held_data_file_unit_v1,
)
from tools.brain_host_held_data_t_v1 import (
    REQUIRED_ROOT_ROLES,
    ROOT_KINDS,
    ApprovedDataPlan,
    HeldDataT,
    RootDeclaration,
    approved_data_plan_sha256,
)
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json, sha256


class _Guard:
    volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"
    root_id = "a" * 32


def _plan(role: str = "helius_mcp_wrapper") -> ApprovedDataPlan:
    roles = {
        item: RootDeclaration(
            False,
            unresolved_reason=f"fixture_{item}_not_selected",
        )
        for item in REQUIRED_ROOT_ROLES
    }
    roles["verifier_worktree"] = RootDeclaration(
        False, unresolved_reason="fixture_verifier_not_selected"
    )
    roles[role] = RootDeclaration(True, f"roots/{role}", ROOT_KINDS[role])
    return ApprovedDataPlan(
        "synthetic-file-unit-plan",
        "6e652e4f-9c38-4a48-887a-530158ba2767",
        "disk5s1",
        "disk5",
        "6e652e4f-9c38-4a48-887a-530158ba2767",
        roles,
    )


def _policy(tmp_path: Path, plan: ApprovedDataPlan, role: str) -> tuple[Path, str]:
    raw = canonical_json(
        {
            "schema": POLICY_SCHEMA,
            "version": 1,
            "source_plan_sha256": approved_data_plan_sha256(plan),
            "role": role,
            "relative_locator": plan.root_roster[role].relative_locator,
            "scope": "private_developer_tool_opaque",
            "restore_disposition": "quarantine_only",
            "metadata_disposition": "omitted_unqualified",
            "max_file_bytes": 1024 * 1024,
        }
    )
    path = tmp_path / f"{role}-policy.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    return path, sha256(raw)


def _view(plan: ApprovedDataPlan, source: Path) -> HeldDataT:
    view = object.__new__(HeldDataT)
    view.plan = plan
    view.synthetic = False
    view._closed = False
    view.mountpoint = source.parent
    view.mount_fd = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY)
    view.root_fds = {"helius_mcp_wrapper": os.open(source, os.O_RDONLY | os.O_NOFOLLOW)}
    view.revalidate = lambda: None
    view.revalidate_mount_fast = lambda: None
    data = {
        "data_volume_uuid": plan.data_volume_uuid,
        "data_device_identifier": plan.data_device_identifier,
        "data_container_reference": plan.data_container_reference,
        "data_volume_group_id": plan.data_volume_group_id,
        "mounted_fs_type": "apfs",
        "mounted_fsid": [41, 97],
        "mountpoint": str(source.parent),
        "readonly": True,
        "source": f"/dev/{plan.data_device_identifier}",
    }
    view.result = lambda: {"data": data}
    return view


def _close(view: HeldDataT) -> None:
    for fd in view.root_fds.values():
        os.close(fd)
    os.close(view.mount_fd)


def _writer(path: Path, guard: _Guard) -> DescriptorWriter:
    path.mkdir(mode=0o700)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        writer = DescriptorWriter(fd, storage_guard=guard)
    finally:
        os.close(fd)
    writer.assert_storage_binding = lambda supplied: None  # type: ignore[method-assign]
    return writer


def test_exact_file_role_policy_and_external_pins(tmp_path: Path) -> None:
    plan = _plan()
    path, policy_hash = _policy(tmp_path, plan, "helius_mcp_wrapper")
    policy = read_file_policy(
        path,
        expected_sha256=policy_hash,
        plan=plan,
        expected_plan_sha256=approved_data_plan_sha256(plan),
        role="helius_mcp_wrapper",
    )
    assert policy["restore_disposition"] == "quarantine_only"
    with pytest.raises(HeldDataFileError, match="file_policy_plan_binding_invalid"):
        read_file_policy(
            path,
            expected_sha256=policy_hash,
            plan=plan,
            expected_plan_sha256=approved_data_plan_sha256(plan),
            role="codex_mcp_config",
        )
    assert ALLOWED_FILE_ROLES == {
        "helius_mcp_wrapper",
        "codex_mcp_config",
        "cursor_mcp_config",
    }


def test_file_unit_streams_bytes_and_freshly_verifies_quarantine_candidate(
    tmp_path: Path,
) -> None:
    source = tmp_path / "opaque-wrapper"
    payload = b"opaque private configuration bytes\x00\xff"
    source.write_bytes(payload)
    plan = _plan()
    policy_path, policy_hash = _policy(tmp_path, plan, "helius_mcp_wrapper")
    guard = _Guard()
    writer = _writer(tmp_path / "kingston-root", guard)
    view = _view(plan, source)
    try:
        preflight = preflight_held_data_file_roles_v1(
            view,
            policy_path_by_role={"helius_mcp_wrapper": policy_path},
            expected_policy_sha256_by_role={"helius_mcp_wrapper": policy_hash},
            expected_plan_sha256=approved_data_plan_sha256(plan),
        )
        assert os.read(view.root_fds["helius_mcp_wrapper"], len(payload)) == payload
        captured = capture_held_data_file_unit_v1(
            view,
            writer,
            role="helius_mcp_wrapper",
            policy_path=policy_path,
            expected_plan_sha256=approved_data_plan_sha256(plan),
            expected_policy_sha256=policy_hash,
            storage_guard=guard,
            capture_id="a" * 32,
            expected_source_file_signature=preflight["source_file_signatures"][
                "helius_mcp_wrapper"
            ],
        )
        assert captured["candidate_published"] is True
        assert captured["independently_verified"] is False
        assert captured["payload_sha256"] == sha256(payload)
        assert captured["payload_bytes"] == len(payload)
        assert captured["restore_disposition"] == "quarantine_only"
        assert captured["safe_to_erase"] is False
        verified = verify_held_data_file_unit_v1(
            writer,
            captured["capture_id"],
            plan,
            role="helius_mcp_wrapper",
            policy_path=policy_path,
            expected_plan_sha256=approved_data_plan_sha256(plan),
            expected_policy_sha256=policy_hash,
            expected_manifest_sha256=captured["manifest_sha256"],
            storage_guard=guard,
        )
        assert verified["verified"] is True
        assert verified["brain_restore_verified"] is False

        retried = capture_held_data_file_unit_v1(
            view,
            writer,
            role="helius_mcp_wrapper",
            policy_path=policy_path,
            expected_plan_sha256=approved_data_plan_sha256(plan),
            expected_policy_sha256=policy_hash,
            storage_guard=guard,
            capture_id="b" * 32,
            expected_source_file_signature=preflight["source_file_signatures"][
                "helius_mcp_wrapper"
            ],
        )
        assert retried["payload_sha256"] == sha256(payload)
        extra_path = f"host-recovery/held-data-file-units-v1/{retried['capture_id']}/extra.bin"
        writer.create(extra_path, b"unexpected member")
        with pytest.raises(HeldDataFileError, match="file_unit_membership_mismatch"):
            verify_held_data_file_unit_v1(
                writer,
                retried["capture_id"],
                plan,
                role="helius_mcp_wrapper",
                policy_path=policy_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_policy_sha256=policy_hash,
                expected_manifest_sha256=retried["manifest_sha256"],
                storage_guard=guard,
            )

        replaced = capture_held_data_file_unit_v1(
            view,
            writer,
            role="helius_mcp_wrapper",
            policy_path=policy_path,
            expected_plan_sha256=approved_data_plan_sha256(plan),
            expected_policy_sha256=policy_hash,
            storage_guard=guard,
            capture_id="c" * 32,
            expected_source_file_signature=preflight["source_file_signatures"][
                "helius_mcp_wrapper"
            ],
        )
        unit = tmp_path / "kingston-root" / "host-recovery" / "held-data-file-units-v1" / ("c" * 32)
        held = unit.with_name("c" * 32 + ".held")
        os.rename(unit, held)
        unit.mkdir(mode=0o700)
        for name in ("manifest.json", "payload.bin"):
            copied = unit / name
            copied.write_bytes((held / name).read_bytes())
            copied.chmod(0o600)
        with pytest.raises(HeldDataFileError, match="file_manifest_binding_invalid"):
            verify_held_data_file_unit_v1(
                writer,
                replaced["capture_id"],
                plan,
                role="helius_mcp_wrapper",
                policy_path=policy_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_policy_sha256=policy_hash,
                expected_manifest_sha256=replaced["manifest_sha256"],
                storage_guard=guard,
            )

        with pytest.raises(HeldDataFileError, match="file_manifest_external_pin_mismatch"):
            verify_held_data_file_unit_v1(
                writer,
                captured["capture_id"],
                plan,
                role="helius_mcp_wrapper",
                policy_path=policy_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_policy_sha256=policy_hash,
                expected_manifest_sha256="0" * 64,
                storage_guard=guard,
            )
        manifest_path = (
            f"host-recovery/held-data-file-units-v1/{captured['capture_id']}/manifest.json"
        )
        original_manifest = writer.read_bytes(manifest_path)
        malformed_manifest = json.loads(original_manifest)
        del malformed_manifest["source_file_signature"]["inode"]
        malformed_raw = canonical_json(malformed_manifest)
        writer.atomic_write(manifest_path, malformed_raw)
        with pytest.raises(HeldDataFileError, match="file_manifest_binding_invalid"):
            verify_held_data_file_unit_v1(
                writer,
                captured["capture_id"],
                plan,
                role="helius_mcp_wrapper",
                policy_path=policy_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_policy_sha256=policy_hash,
                expected_manifest_sha256=sha256(malformed_raw),
                storage_guard=guard,
            )
        malformed_manifest["source_file_signature"] = json.loads(original_manifest)[
            "source_file_signature"
        ]
        malformed_manifest["source_file_signature"]["inode"] = True
        malformed_raw = canonical_json(malformed_manifest)
        writer.atomic_write(manifest_path, malformed_raw)
        with pytest.raises(HeldDataFileError, match="file_manifest_binding_invalid"):
            verify_held_data_file_unit_v1(
                writer,
                captured["capture_id"],
                plan,
                role="helius_mcp_wrapper",
                policy_path=policy_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_policy_sha256=policy_hash,
                expected_manifest_sha256=sha256(malformed_raw),
                storage_guard=guard,
            )
        list_raw = canonical_json([])
        writer.atomic_write(manifest_path, list_raw)
        with pytest.raises(HeldDataFileError, match="file_manifest_binding_invalid"):
            verify_held_data_file_unit_v1(
                writer,
                captured["capture_id"],
                plan,
                role="helius_mcp_wrapper",
                policy_path=policy_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_policy_sha256=policy_hash,
                expected_manifest_sha256=sha256(list_raw),
                storage_guard=guard,
            )
        writer.atomic_write(manifest_path, original_manifest)
        writer.atomic_write(
            f"host-recovery/held-data-file-units-v1/{captured['capture_id']}/payload.bin",
            b"tampered",
        )
        with pytest.raises(HeldDataFileError, match="file_payload_hash_or_size_mismatch"):
            verify_held_data_file_unit_v1(
                writer,
                captured["capture_id"],
                plan,
                role="helius_mcp_wrapper",
                policy_path=policy_path,
                expected_plan_sha256=approved_data_plan_sha256(plan),
                expected_policy_sha256=policy_hash,
                expected_manifest_sha256=captured["manifest_sha256"],
                storage_guard=guard,
            )
    finally:
        _close(view)
        writer.close()


def test_held_file_source_rejects_hardlink_and_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_bytes(b"private")
    alias = tmp_path / "alias"
    os.link(source, alias)
    plan = _plan()
    view = _view(plan, source)
    try:
        with pytest.raises(HeldDataFileError, match="file_source_type_link_device_or_mode_invalid"):
            _source_fd(view, "helius_mcp_wrapper")
        link = tmp_path / "source-link"
        link.symlink_to(source)
        with pytest.raises(OSError):
            os.open(link, os.O_RDONLY | os.O_NOFOLLOW)
    finally:
        _close(view)


def test_present_non_mcp_file_role_is_rejected(tmp_path: Path) -> None:
    plan = _plan("codex_mcp_config")
    path, policy_hash = _policy(tmp_path, plan, "codex_mcp_config")
    with pytest.raises(HeldDataFileError, match="file_policy_plan_binding_invalid"):
        read_file_policy(
            path,
            expected_sha256=policy_hash,
            plan=plan,
            expected_plan_sha256=approved_data_plan_sha256(plan),
            role="private_brain_config",
        )
