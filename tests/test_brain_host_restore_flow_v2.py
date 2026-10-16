from __future__ import annotations

import contextlib
import json
import os
import subprocess
from copy import deepcopy
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from memecoin_research_storage import ReadOnlyStorageGuard
from tools import brain_host_restore_flow_v2 as flow
from tools.brain_host_held_data_t_v1 import ApprovedDataPlan, RootDeclaration
from tools.brain_senses_external_writer_v1 import DescriptorWriter, canonical_json


def _documents() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], ApprovedDataPlan]:
    present = {"owner_checkout", "research_checkout", *flow.FILE_ROLES}
    roster = {
        role: RootDeclaration(True, f"roots/{role}", kind)
        if role in present
        else RootDeclaration(False, unresolved_reason="synthetic_source_not_observed")
        for role, kind in flow.ROOT_KINDS.items()
    }
    volume = "11111111-2222-3333-4444-555555555555"
    plan = ApprovedDataPlan("synthetic-plan", volume, "disk9s1", "disk9", volume, roster)
    plan_hash = flow.approved_data_plan_sha256(plan)
    policy_rows, handoff_rows, index_rows = {}, {}, {}
    for role, declaration in roster.items():
        kind = flow.ROOT_KINDS[role]
        file = role in flow.FILE_ROLES
        policy_hash = flow._digest(role.encode())
        unit_id = flow._digest(f"unit-{role}".encode())[:32]
        manifest_hash = flow._digest(f"manifest-{role}".encode())
        relation_hash = flow._digest(f"relation-{role}".encode())
        policy_rows[role] = {
            "state": ("capture_file_quarantine" if file else "capture_directory")
            if declaration.present
            else "unresolved_absence",
            "relative_locator": declaration.relative_locator,
            "kind": declaration.kind,
            "unresolved_reason": declaration.unresolved_reason,
            "scope": ("private_developer_tool_opaque" if file else "non_protected")
            if declaration.present
            else None,
            "policy_sha256": policy_hash if declaration.present else None,
            "application_consistency": flow.mixed.CAPTURE_CONSISTENCY_LITERAL
            if declaration.present
            else None,
        }
        handoff_rows[role] = {
            "kind": kind,
            "source_state": "present" if declaration.present else "absent",
            "absence_reason": declaration.unresolved_reason,
            "relative_locator": declaration.relative_locator,
            "custody_state": "verified_unit" if declaration.present else "unresolved",
            "custody_reason": None if declaration.present else "custody_not_established",
            "capture_unit": {
                "id": unit_id,
                "sha256": manifest_hash if file else relation_hash,
                "type": "private_file_manifest_v1" if file else "directory_relation_v3",
            }
            if declaration.present
            else None,
            "protected_disposition": "non_protected",
            "opaque_disposition": "private_opaque" if file else "backup_required",
            "restore_disposition": "quarantine_reissue_only" if file else "inert_quarantine_only",
        }
        if not declaration.present:
            index_rows[role] = {
                "state": "absent_unresolved",
                "unresolved_reason": declaration.unresolved_reason,
            }
            continue
        index_rows[role] = {
            "state": "file_quarantine_candidate" if file else "captured_candidate",
            "kind": kind,
            "relative_locator": declaration.relative_locator,
            "scope": "private_developer_tool_opaque" if file else "non_protected",
            "application_consistency": flow.mixed.CAPTURE_CONSISTENCY_LITERAL,
            "policy_sha256": policy_hash,
            "capture_id": unit_id,
            "manifest_sha256": manifest_hash,
        }
        if file:
            index_rows[role].update(
                payload_sha256="f" * 64,
                payload_bytes=7,
                restore_disposition="quarantine_only",
                metadata_disposition="omitted_unqualified",
            )
        else:
            index_rows[role].update(
                relation_sha256=relation_hash,
                included_entry_count=1,
                unresolved_exclusion_count=0,
                external_custody_required_count=0,
            )
    policy = {
        "schema": flow.mixed.POLICY_SCHEMA,
        "version": 2,
        "source_plan_sha256": plan_hash,
        "roles": policy_rows,
    }
    policy_hash = flow._digest(canonical_json(policy))
    index = {
        "schema": flow.mixed.INDEX_SCHEMA,
        "set_id": "a" * 32,
        "source_plan_sha256": plan_hash,
        "role_policy_sha256": policy_hash,
        "role_policy": policy,
        "source_data_identity": {
            "data_volume_uuid": volume,
            "data_device_identifier": "disk9s1",
            "data_container_reference": "disk9",
            "data_volume_group_id": volume,
            "readonly": True,
        },
        "roles": index_rows,
        "declared_role_count": 9,
        "captured_directory_count": 2,
        "captured_file_count": 3,
        "unresolved_role_count": 4,
        "index_scope": "plan_declared_supported_directory_and_private_file_candidates_only",
        "application_consistency": flow.mixed.CAPTURE_CONSISTENCY_LITERAL,
        **{key: False for key in flow.AUTHORITY_FLAGS},
    }
    handoff = {
        "schema": flow.HANDOFF_SCHEMA,
        "version": 2,
        "handoff_id": "b" * 32,
        "execution_commit": "c" * 40,
        "entry_card_sha256": flow._digest(b"synthetic entry card\n"),
        "kingston": {"volume_uuid": volume, "root_id": "d" * 32},
        "data_plan_sha256": plan_hash,
        "role_policy_roster_sha256": policy_hash,
        "mixed_candidate": {
            "set_id": index["set_id"],
            "index_sha256": flow._digest(canonical_json(index)),
        },
        "authorization_scope": "inspect_only",
        "roles": handoff_rows,
    }
    return handoff, index, policy, plan


def test_exact_nine_mixed_roles_are_always_blocked_without_payload_reverification() -> None:
    handoff, index, policy, _ = _documents()
    result = flow.assess_index_metadata(handoff, index, policy)
    assert result["status"] == "blocked"
    assert result["metadata_pins_match"] is True
    assert result["directory_unit_count"] == 2 and result["file_unit_count"] == 3
    assert result["unresolved_role_count"] == 4
    assert result["unit_payloads_reverified"] is False
    assert all(result[key] is False for key in flow.AUTHORITY_FLAGS)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_role",
        "legacy_alias",
        "extra_bridge",
        "bool_version",
        "file_restore",
        "file_kind",
        "unit_type",
        "duplicate_unit",
        "unit_on_unresolved",
        "extra_authority",
    ],
)
def test_handoff_strictly_rejects_alias_type_custody_and_authority_drift(mutation: str) -> None:
    handoff, _, _, _ = _documents()
    if mutation == "missing_role":
        handoff["roles"].pop("verifier_worktree")
    elif mutation == "legacy_alias":
        handoff["roles"]["owner_work"] = handoff["roles"].pop("owner_checkout")
    elif mutation == "extra_bridge":
        handoff["lineage_bridge"] = {"owner_work": "owner_checkout"}
    elif mutation == "bool_version":
        handoff["version"] = True
    elif mutation == "file_restore":
        handoff["roles"]["codex_mcp_config"]["restore_disposition"] = "restore_original_path"
    elif mutation == "file_kind":
        handoff["roles"]["cursor_mcp_config"]["kind"] = "directory"
    elif mutation == "unit_type":
        handoff["roles"]["owner_checkout"]["capture_unit"]["type"] = "private_file_manifest_v1"
    elif mutation == "duplicate_unit":
        handoff["roles"]["research_checkout"]["capture_unit"]["id"] = handoff["roles"][
            "owner_checkout"
        ]["capture_unit"]["id"]
    elif mutation == "unit_on_unresolved":
        handoff["roles"]["verifier_worktree"]["capture_unit"] = deepcopy(
            handoff["roles"]["owner_checkout"]["capture_unit"]
        )
    else:
        handoff["activation_authorized"] = True
    with pytest.raises(flow.InspectionError):
        flow.validate_handoff(handoff)


def test_present_source_can_remain_uncaptured_without_inferred_absence() -> None:
    handoff, index, policy, _ = _documents()
    row = handoff["roles"]["private_brain_config"]
    row.update(
        source_state="present",
        absence_reason=None,
        relative_locator="roots/private_brain_config",
        custody_state="unresolved",
        custody_reason="present_source_custody_not_established",
        capture_unit=None,
    )
    index["roles"]["private_brain_config"]["unresolved_reason"] = row["custody_reason"]
    handoff["mixed_candidate"]["index_sha256"] = flow._digest(canonical_json(index))
    result = flow.assess_index_metadata(handoff, index, policy)
    assert result["present_uncaptured_count"] == 1
    assert "present_source_roles_uncaptured" in result["blockers"]
    assert result["source_presence_inferred_from_index"] is False


@pytest.mark.parametrize(
    "mutation", ["index_flag", "unit_hash", "index_kind", "count_bool", "absent_reason"]
)
def test_index_metadata_rejects_binding_drift(mutation: str) -> None:
    handoff, index, policy, _ = _documents()
    if mutation == "index_flag":
        index["safe_to_erase"] = True
    elif mutation == "unit_hash":
        index["roles"]["owner_checkout"]["relation_sha256"] = "e" * 64
    elif mutation == "index_kind":
        index["roles"]["codex_mcp_config"]["kind"] = "directory"
    elif mutation == "count_bool":
        index["unresolved_role_count"] = True
    else:
        index["roles"]["verifier_worktree"]["unresolved_reason"] = "different"
    handoff["mixed_candidate"]["index_sha256"] = flow._digest(canonical_json(index))
    with pytest.raises(flow.InspectionError):
        flow.assess_index_metadata(handoff, index, policy)


@pytest.fixture
def disposable_inspection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    handoff, index, policy, plan = _documents()
    repo, mount = tmp_path / "repo", tmp_path / "mount"
    data = mount / "BrainData"

    def write(path: Path, raw: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(0o600)
        return path

    card = b"synthetic entry card\n"
    write(repo / "recovery/ENTRY-v2.txt", card)
    write(data / "host-recovery/ENTRY-v2.txt", card)
    index_path = write(
        data / flow.mixed.SET_BASE / f"{index['set_id']}.json", canonical_json(index)
    )
    handoff_path = write(tmp_path / "handoff.json", canonical_json(handoff))
    policy_path = write(tmp_path / "policy.json", canonical_json(policy))
    plan_path = write(
        tmp_path / "plan.json",
        canonical_json({"schema": "brain-host-approved-data-plan-v1", **asdict(plan)}),
    )
    for directory, _dirs, _files in os.walk(mount):
        Path(directory).chmod(0o700)

    class Guard(ReadOnlyStorageGuard):
        def revalidate(self):
            return self.root

    @contextlib.contextmanager
    def open_guard(_path):
        guard = object.__new__(Guard)
        guard.mount_fd = os.open(mount, os.O_RDONLY | os.O_DIRECTORY)
        guard.root_fd = os.open(data, os.O_RDONLY | os.O_DIRECTORY)
        guard.config = SimpleNamespace(data_root="BrainData")
        guard.root = SimpleNamespace(**handoff["kingston"])
        try:
            yield guard
        finally:
            guard.close()

    monkeypatch.setattr(flow, "read_only_guard", open_guard)
    monkeypatch.setattr(flow, "__file__", str(repo / "tools/brain_host_restore_flow_v2.py"))
    reads = []
    original_read = DescriptorWriter.read_bytes

    def read_only_allowlist(self, relative, *, max_bytes):
        assert relative in {
            "host-recovery/ENTRY-v2.txt",
            f"{flow.mixed.SET_BASE}/{index['set_id']}.json",
        }
        reads.append(relative)
        return original_read(self, relative, max_bytes=max_bytes)

    monkeypatch.setattr(DescriptorWriter, "read_bytes", read_only_allowlist)

    def runner(args, **_kwargs):
        return subprocess.CompletedProcess(
            args, 0, handoff["execution_commit"] + "\n" if args[-1] == "HEAD" else "", ""
        )

    kwargs = {
        "handoff_path": handoff_path,
        "expected_handoff_sha256": flow._digest(canonical_json(handoff)),
        "repository_root": repo,
        "storage_config": tmp_path / "unused-storage.json",
        "plan_path": plan_path,
        "expected_plan_sha256": handoff["data_plan_sha256"],
        "role_policy_path": policy_path,
        "expected_role_policy_sha256": handoff["role_policy_roster_sha256"],
        "runner": runner,
    }
    return SimpleNamespace(
        kwargs=kwargs,
        reads=reads,
        handoff=handoff,
        index=index,
        data=data,
        index_path=index_path,
        write=write,
    )


def test_public_inspector_reads_only_disposable_card_and_index(disposable_inspection) -> None:
    fixture = disposable_inspection
    before = {str(p): flow._digest(p.read_bytes()) for p in fixture.data.rglob("*") if p.is_file()}
    result = flow.inspect_handoff(**fixture.kwargs)
    assert result["status"] == "blocked" and result["metadata_pins_match"] is True
    assert result["unresolved_role_count"] == 4
    assert len(fixture.reads) == 3
    assert result["protected_payloads_read"] is False
    assert before == {
        str(p): flow._digest(p.read_bytes()) for p in fixture.data.rglob("*") if p.is_file()
    }


def _cli_args(args: dict[str, Any]) -> list[str]:
    return [
        "inspect",
        "--handoff",
        str(args["handoff_path"]),
        "--handoff-sha256",
        args["expected_handoff_sha256"],
        "--repository-root",
        str(args["repository_root"]),
        "--storage-config",
        str(args["storage_config"]),
        "--plan",
        str(args["plan_path"]),
        "--plan-sha256",
        args["expected_plan_sha256"],
        "--role-policy",
        str(args["role_policy_path"]),
        "--role-policy-sha256",
        args["expected_role_policy_sha256"],
    ]


def test_inspect_cli_runs_disposable_mixed_index_and_stays_blocked(
    disposable_inspection, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = disposable_inspection
    output = BytesIO()
    monkeypatch.setattr(flow.sys, "stdout", SimpleNamespace(buffer=output))
    monkeypatch.setattr(
        flow,
        "_git_state",
        lambda *_args: {"head": fixture.handoff["execution_commit"], "clean": True},
    )
    assert flow.main(_cli_args(fixture.kwargs)) == 2
    result = json.loads(output.getvalue())
    assert result["status"] == "blocked"
    assert result["metadata_pins_match"] is True
    assert result["unresolved_role_count"] == 4
    assert result["restore_performed"] is False
    assert result["start_performed"] is False
    assert all(result[key] is False for key in flow.AUTHORITY_FLAGS)


@pytest.mark.parametrize("scenario", ["missing", "malformed"])
def test_inspect_cli_names_bad_handoff_without_exposing_input(
    disposable_inspection, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    fixture = disposable_inspection
    original_path = fixture.kwargs["handoff_path"]
    if scenario == "missing":
        fixture.kwargs["handoff_path"] = original_path.with_name("missing-handoff.json")
    else:
        raw = b'{"schema":"bad","schema":"duplicate"}\n'
        fixture.write(original_path, raw)
        fixture.kwargs["expected_handoff_sha256"] = flow._digest(raw)
    output = BytesIO()
    monkeypatch.setattr(flow.sys, "stdout", SimpleNamespace(buffer=output))

    assert flow.main(_cli_args(fixture.kwargs)) == 2
    result = json.loads(output.getvalue())
    assert result["status"] == "blocked"
    assert result["metadata_pins_match"] is False
    assert result["blockers"] == ["handoff_plan_policy_inspection_failed"]
    assert str(fixture.kwargs["handoff_path"]) not in output.getvalue().decode()
    assert all(result[key] is False for key in flow.AUTHORITY_FLAGS)


@pytest.mark.parametrize(
    "mutation",
    [
        "handoff_hash",
        "handoff_mode",
        "plan_mode",
        "plan_pin",
        "policy_pin",
        "index_hash",
        "card_hash",
        "source_absence",
    ],
)
def test_public_inspector_fails_closed_on_independent_pin_and_file_drift(
    disposable_inspection, mutation: str
) -> None:
    fixture = disposable_inspection
    if mutation == "handoff_hash":
        fixture.kwargs["expected_handoff_sha256"] = "f" * 64
    elif mutation == "handoff_mode":
        fixture.kwargs["handoff_path"].chmod(0o644)
    elif mutation == "plan_mode":
        fixture.kwargs["plan_path"].chmod(0o644)
    elif mutation == "plan_pin":
        fixture.kwargs["expected_plan_sha256"] = "f" * 64
    elif mutation == "policy_pin":
        fixture.kwargs["expected_role_policy_sha256"] = "f" * 64
    elif mutation == "index_hash":
        fixture.index_path.write_bytes(b"{}\n")
    elif mutation == "card_hash":
        (fixture.data / "host-recovery/ENTRY-v2.txt").write_bytes(b"changed")
    else:
        fixture.handoff["roles"]["verifier_worktree"]["absence_reason"] = "wrong-source-reason"
        raw = canonical_json(fixture.handoff)
        fixture.write(fixture.kwargs["handoff_path"], raw)
        fixture.kwargs["expected_handoff_sha256"] = flow._digest(raw)
    result = flow.inspect_handoff(**fixture.kwargs)
    assert result["metadata_pins_match"] is False
    assert result["status"] == "blocked"
    expected_stage = {
        "handoff_hash": "handoff_plan_policy_inspection_failed",
        "handoff_mode": "handoff_plan_policy_inspection_failed",
        "plan_mode": "handoff_plan_policy_inspection_failed",
        "plan_pin": "handoff_plan_policy_inspection_failed",
        "policy_pin": "handoff_plan_policy_inspection_failed",
        "index_hash": "candidate_index_inspection_failed",
        "card_hash": "storage_or_card_inspection_failed",
        "source_absence": "handoff_plan_policy_inspection_failed",
    }[mutation]
    assert result["blockers"] == [expected_stage]
    assert result["operator_action"] == flow.FAILURE_ACTIONS[expected_stage]
    assert all(result[key] is False for key in flow.AUTHORITY_FLAGS)


def test_public_inspector_reports_pinned_checkout_recovery_action(disposable_inspection) -> None:
    fixture = disposable_inspection

    def runner(args, **_kwargs):
        stdout = "f" * 40 + "\n" if args[-1] == "HEAD" else ""
        return subprocess.CompletedProcess(args, 0, stdout, "")

    fixture.kwargs["runner"] = runner
    result = flow.inspect_handoff(**fixture.kwargs)
    assert result["blockers"] == ["checkout_or_source_card_inspection_failed"]
    assert (
        result["operator_action"]
        == flow.FAILURE_ACTIONS["checkout_or_source_card_inspection_failed"]
    )


def test_private_json_rejects_duplicate_fields_and_symlink(tmp_path: Path) -> None:
    path = tmp_path / "handoff.json"
    raw = b'{"version":2,"version":2}\n'
    path.write_bytes(raw)
    path.chmod(0o600)
    with pytest.raises(flow.InspectionError):
        flow._private_json(path, flow._digest(raw))
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        flow._private_json(link, flow._digest(raw))


def test_private_json_rejects_symlinked_parent(tmp_path: Path) -> None:
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = target_dir / "input.json"
    target.write_bytes(canonical_json({"safe": True}))
    target.chmod(0o600)
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(target_dir, target_is_directory=True)
    with pytest.raises(OSError):
        flow._private_json(parent_link / target.name, flow._digest(target.read_bytes()))


@pytest.mark.parametrize("mutation", ["mode", "link_count"])
def test_private_file_rejects_privacy_change_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    path = tmp_path / "private.json"
    path.write_bytes(canonical_json({"safe": True}))
    path.chmod(0o600)
    original_read = flow.os.read
    changed = False

    def chmod_during_read(fd: int, count: int) -> bytes:
        nonlocal changed
        payload = original_read(fd, count)
        if payload and not changed:
            if mutation == "mode":
                os.fchmod(fd, 0o644)
            else:
                os.link(path, tmp_path / "hardlink.json")
            changed = True
        return payload

    monkeypatch.setattr(flow.os, "read", chmod_during_read)
    with pytest.raises(flow.InspectionError):
        flow._read_private_file(path)
    path.chmod(0o600)
    (tmp_path / "hardlink.json").unlink(missing_ok=True)
