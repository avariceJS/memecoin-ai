from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from tools.brain_host_held_data_file_capture_v1 import CAPTURE_CONSISTENCY_LITERAL
from tools.brain_host_held_data_root_set_capture_v2 import (
    POLICY_SCHEMA,
    HeldDataRootSetV2Error,
    _validate_policy,
    capture_held_data_root_set_v2,
    verify_held_data_root_set_v2,
)
from tools.brain_host_held_data_t_v1 import (
    REQUIRED_ROOT_ROLES,
    ApprovedDataPlan,
    HeldDataT,
    RootDeclaration,
    approved_data_plan_sha256,
)
from tools.brain_senses_external_writer_v1 import sha256


def _plan() -> ApprovedDataPlan:
    directories = {"owner_checkout", "research_checkout"}
    files = {"helius_mcp_wrapper", "codex_mcp_config", "cursor_mcp_config"}
    roster: dict[str, RootDeclaration] = {}
    for role in sorted(REQUIRED_ROOT_ROLES):
        if role in directories:
            roster[role] = RootDeclaration(True, f"roots/{role}", "directory")
        elif role in files:
            roster[role] = RootDeclaration(True, f"roots/{role}", "file")
        else:
            roster[role] = RootDeclaration(False, unresolved_reason="fixture_outside_scope")
    return ApprovedDataPlan(
        "mixed-v2-policy-test",
        "6e652e4f-9c38-4a48-887a-530158ba2767",
        "disk5s1",
        "disk5",
        "6e652e4f-9c38-4a48-887a-530158ba2767",
        roster,
    )


def _policy(plan: ApprovedDataPlan) -> dict[str, Any]:
    plan_hash = approved_data_plan_sha256(plan)
    roles: dict[str, dict[str, Any]] = {}
    for role, declaration in plan.root_roster.items():
        if not declaration.present:
            roles[role] = {
                "state": "unresolved_absence",
                "relative_locator": None,
                "kind": None,
                "unresolved_reason": declaration.unresolved_reason,
                "scope": None,
                "policy_sha256": None,
                "application_consistency": None,
            }
            continue
        is_file = declaration.kind == "file"
        roles[role] = {
            "state": "capture_file_quarantine" if is_file else "capture_directory",
            "relative_locator": declaration.relative_locator,
            "kind": declaration.kind,
            "unresolved_reason": None,
            "scope": "private_developer_tool_opaque" if is_file else "non_protected",
            "policy_sha256": sha256(role.encode("utf-8")),
            "application_consistency": CAPTURE_CONSISTENCY_LITERAL,
        }
    return {
        "schema": POLICY_SCHEMA,
        "version": 2,
        "source_plan_sha256": plan_hash,
        "roles": roles,
    }


def test_mixed_policy_accepts_exact_directory_file_and_unresolved_roles() -> None:
    plan = _plan()
    policy = _policy(plan)
    assert (
        _validate_policy(
            policy,
            plan,
            expected_plan_sha256=approved_data_plan_sha256(plan),
        )
        == policy
    )


@pytest.mark.parametrize(
    ("role", "key", "value"),
    [
        ("helius_mcp_wrapper", "state", "capture_directory"),
        ("helius_mcp_wrapper", "scope", "non_protected"),
        ("owner_checkout", "scope", "protected_opaque"),
        ("owner_checkout", "kind", "file"),
        ("private_brain_config", "unresolved_reason", "wrong_absence_reason"),
    ],
)
def test_mixed_policy_rejects_role_kind_scope_or_absence_drift(
    role: str,
    key: str,
    value: str,
) -> None:
    plan = _plan()
    policy = deepcopy(_policy(plan))
    policy["roles"][role][key] = value
    with pytest.raises(HeldDataRootSetV2Error):
        _validate_policy(
            policy,
            plan,
            expected_plan_sha256=approved_data_plan_sha256(plan),
        )


def test_mixed_policy_rejects_file_kind_for_non_file_role() -> None:
    plan = _plan()
    roster = dict(plan.root_roster)
    roster["private_brain_config"] = RootDeclaration(True, "roots/private-config", "file")
    unsupported = ApprovedDataPlan(
        plan.approval_id,
        plan.data_volume_uuid,
        plan.data_device_identifier,
        plan.data_container_reference,
        plan.data_volume_group_id,
        roster,
    )
    policy = _policy(unsupported)
    policy["roles"]["private_brain_config"].update(
        {
            "state": "capture_file_quarantine",
            "scope": "private_developer_tool_opaque",
            "policy_sha256": sha256(b"private_brain_config"),
        }
    )
    with pytest.raises(HeldDataRootSetV2Error, match="present_file_policy_invalid"):
        _validate_policy(
            policy,
            unsupported,
            expected_plan_sha256=approved_data_plan_sha256(unsupported),
        )


class _FakeWriter:
    def __init__(self, guard: object) -> None:
        self.storage_guard = guard
        self.created: list[str] = []

    def assert_storage_binding(self, supplied: object) -> None:
        assert supplied is self.storage_guard

    def create(self, relative: str, _data: bytes) -> None:
        self.created.append(relative)


class _FakeView(HeldDataT):
    synthetic = False

    def __init__(self, plan: ApprovedDataPlan, source_identity: dict[str, Any]) -> None:
        self.plan = plan
        self.source_identity = source_identity

    def revalidate(self) -> None:
        return None

    def result(self) -> dict[str, Any]:
        return {"data": self.source_identity}


def _one_file_plan() -> ApprovedDataPlan:
    plan = _plan()
    roster = dict(plan.root_roster)
    for role in roster:
        if role == "helius_mcp_wrapper":
            continue
        declaration = roster[role]
        roster[role] = RootDeclaration(
            False,
            unresolved_reason=declaration.unresolved_reason or "outside_test_scope",
        )
    return ApprovedDataPlan(
        plan.approval_id,
        plan.data_volume_uuid,
        plan.data_device_identifier,
        plan.data_container_reference,
        plan.data_volume_group_id,
        roster,
    )


@pytest.mark.parametrize("failure", ["preflight", "capture", "verifier_payload_drift"])
def test_capture_failures_leave_set_index_unpublished(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    import tools.brain_host_held_data_root_set_capture_v2 as mixed

    plan = _one_file_plan()
    plan_hash = approved_data_plan_sha256(plan)
    policy = _policy(plan)
    role = "helius_mcp_wrapper"
    file_policy_hash = policy["roles"][role]["policy_sha256"]
    source_identity = {
        "data_volume_uuid": plan.data_volume_uuid,
        "data_device_identifier": plan.data_device_identifier,
        "data_container_reference": plan.data_container_reference,
        "data_volume_group_id": plan.data_volume_group_id,
        "readonly": True,
    }
    view = _FakeView(plan, source_identity)
    guard = object()
    writer = _FakeWriter(guard)
    calls = {"preflight": 0, "capture": 0, "verify": 0}
    monkeypatch.setattr(mixed, "_policy_read", lambda *_args, **_kwargs: policy)
    monkeypatch.setattr(mixed, "read_file_policy", lambda *_args, **_kwargs: {})

    def preflight(*_args: object, **_kwargs: object) -> dict[str, Any]:
        calls["preflight"] += 1
        if failure == "preflight":
            raise ValueError("fixture preflight rejection")
        return {
            "source_data_identity": source_identity,
            "source_file_signatures": {role: {"device": 1}},
        }

    monkeypatch.setattr(mixed, "preflight_held_data_file_roles_v1", preflight)
    monkeypatch.setattr(mixed, "_scan_hardlinks", lambda *_args, **_kwargs: None)

    def capture(*_args: object, **_kwargs: object) -> dict[str, Any]:
        calls["capture"] += 1
        if failure == "capture":
            raise ValueError("fixture partial capture failure")
        return {
            "candidate_published": True,
            "independently_verified": False,
            "capture_id": "a" * 32,
            "manifest_sha256": "b" * 64,
            "payload_sha256": "c" * 64,
            "payload_bytes": 10,
        }

    def verify(*_args: object, **_kwargs: object) -> dict[str, Any]:
        calls["verify"] += 1
        payload_hash = "d" * 64 if failure == "verifier_payload_drift" else "c" * 64
        return {
            "verified": True,
            "capture_id": "a" * 32,
            "manifest_sha256": "b" * 64,
            "payload_sha256": payload_hash,
            "payload_bytes": 10,
            "role": role,
            "source_data_identity": source_identity,
        }

    monkeypatch.setattr(mixed, "capture_held_data_file_unit_v1", capture)
    monkeypatch.setattr(mixed, "verify_held_data_file_unit_v1", verify)
    expected_error = (
        "mixed_source_preflight_failed" if failure == "preflight" else "capture_set_file_failed"
    )
    with pytest.raises(HeldDataRootSetV2Error, match=expected_error):
        capture_held_data_root_set_v2(
            view,  # type: ignore[arg-type]
            writer,  # type: ignore[arg-type]
            role_policy_path=tmp_path / "role-policy.json",
            expected_plan_sha256=plan_hash,
            expected_role_policy_sha256="e" * 64,
            directory_policy_paths={},
            expected_directory_policy_sha256_by_role={},
            file_policy_paths={role: tmp_path / "file-policy.json"},
            expected_file_policy_sha256_by_role={role: file_policy_hash},
            storage_guard=guard,
            scratch_root_relative="scratch",
            scratch_budget_bytes_per_role=1,
        )
    assert writer.created == []
    assert calls["preflight"] == 1
    assert calls["capture"] == int(failure != "preflight")
    assert calls["verify"] == int(failure == "verifier_payload_drift")


def test_verifier_rejects_zero_unit_index_before_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.brain_host_held_data_root_set_capture_v2 as mixed

    plan = _plan()
    roster = {
        role: RootDeclaration(False, unresolved_reason="outside_test_scope")
        for role in REQUIRED_ROOT_ROLES
    }
    absent_plan = ApprovedDataPlan(
        plan.approval_id,
        plan.data_volume_uuid,
        plan.data_device_identifier,
        plan.data_container_reference,
        plan.data_volume_group_id,
        roster,
    )
    policy = _policy(absent_plan)
    guard = object()
    writer = _FakeWriter(guard)
    monkeypatch.setattr(mixed, "_policy_read", lambda *_args, **_kwargs: policy)
    with pytest.raises(HeldDataRootSetV2Error, match="no_supported_present_roles"):
        verify_held_data_root_set_v2(
            writer,  # type: ignore[arg-type]
            "a" * 32,
            absent_plan,
            storage_guard=guard,
            expected_plan_sha256=approved_data_plan_sha256(absent_plan),
            role_policy_path=__import__("pathlib").Path("unused"),
            expected_role_policy_sha256="b" * 64,
            directory_policy_paths={},
            expected_directory_policy_sha256_by_role={},
            file_policy_paths={},
            expected_file_policy_sha256_by_role={},
            expected_set_index_sha256="c" * 64,
        )
