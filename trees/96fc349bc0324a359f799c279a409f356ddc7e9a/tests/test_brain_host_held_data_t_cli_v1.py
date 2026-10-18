from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tools import brain_host_held_data_t_cli_v1 as cli
from tools.brain_host_held_data_t_v1 import (
    REQUIRED_ROOT_ROLES,
    ROOT_KINDS,
    ApprovedDataPlan,
    RootDeclaration,
    approved_data_plan_sha256,
)

DATA_UUID = "430740ca-42e7-4e6c-a07a-e4e7370152cc"
DATA_GROUP = "b72c52b0-591e-4c21-b4d2-87e6d7679c3a"


def _plan() -> ApprovedDataPlan:
    roster = {
        role: RootDeclaration(
            present=True,
            relative_locator=f"roots/{role}",
            kind=ROOT_KINDS[role],
        )
        for role in REQUIRED_ROOT_ROLES
        if role != "verifier_worktree"
    }
    roster["verifier_worktree"] = RootDeclaration(
        present=False,
        unresolved_reason="verifier_worktree_absent_not_recreated",
    )
    return ApprovedDataPlan(
        approval_id="cli-test-plan",
        data_volume_uuid=DATA_UUID,
        data_device_identifier="disk3s5",
        data_container_reference="disk3",
        data_volume_group_id=DATA_GROUP,
        root_roster=roster,
    )


def _document(plan: ApprovedDataPlan) -> dict[str, Any]:
    return {
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
            for role, declaration in plan.root_roster.items()
        },
    }


def _write_plan(path: Path, plan: ApprovedDataPlan) -> None:
    path.write_text(json.dumps(_document(plan)), encoding="utf-8")


class _HeldResult:
    def __init__(self, plan: ApprovedDataPlan, mountpoint: Path, **kwargs: Any) -> None:
        self.plan = plan
        self.mountpoint = mountpoint
        self.kwargs = kwargs

    def __enter__(self) -> _HeldResult:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def result(self) -> dict[str, Any]:
        return {
            "schema": "brain-host-held-data-t-v1",
            "classification": "held_readonly_Data_identity_verified",
            "approval_id": self.plan.approval_id,
            "trusted_plan_pin_matches": True,
            "data": {"volume_uuid": DATA_UUID},
            "root_locators": {"private": {"locator": "private/config"}},
            "held_root_count": 8,
            "root_role_roster_schema_validated": True,
            "required_role_closure": False,
            "unresolved_required_roles": {"verifier_worktree": {}},
            "clean_shutdown_verified": False,
            "prior_writable_mount_history_verified": False,
            "source_cutoff_verified": False,
            "T_qualified": False,
            "capture_complete": False,
            "kingston_durable": False,
            "restore_verified": False,
            "erase_ready": False,
            "mock_backend": False,
        }


def _invoke(plan_path: Path, plan: ApprovedDataPlan, *extra: str) -> int:
    return cli.main(
        [
            "--plan",
            str(plan_path),
            "--plan-sha256",
            approved_data_plan_sha256(plan),
            "--mountpoint",
            "/Volumes/SyntheticData",
            *extra,
        ]
    )


def test_cli_success_stdout_is_redacted_or_explicitly_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _plan()
    plan_path = tmp_path / "approved-plan.json"
    _write_plan(plan_path, plan)
    monkeypatch.setattr(cli, "HeldDataT", _HeldResult)

    assert _invoke(plan_path, plan) == 0
    summary = json.loads(capsys.readouterr().out)
    digest = approved_data_plan_sha256(plan)
    assert summary["ok"] is True
    assert summary["plan_sha256"] == digest
    assert summary["approval_authority"] == "external_plan_sha256_pin_not_approval_authentication"
    assert summary["mock_backend"] is False
    assert summary["required_role_closure"] is False
    assert summary["T_qualified"] is False
    assert summary["erase_ready"] is False
    assert "data" not in summary
    assert "root_locators" not in summary
    assert DATA_UUID not in json.dumps(summary)
    assert "private/config" not in json.dumps(summary)

    assert _invoke(plan_path, plan, "--private-json-stdout") == 0
    private_report = json.loads(capsys.readouterr().out)
    assert private_report["plan_sha256"] == digest
    assert private_report["approval_authority"] == (
        "external_plan_sha256_pin_not_approval_authentication"
    )
    assert private_report["root_locators"]["private"]["locator"] == "private/config"


@pytest.mark.parametrize(
    "document,error",
    [
        pytest.param(
            b'{"schema":"brain-host-approved-data-plan-v1","schema":"x"}',
            "plan_duplicate_json_field",
            id="duplicate-top-level-field",
        ),
        pytest.param(
            b'{"schema":"brain-host-approved-data-plan-v1","unexpected":true}',
            "plan_schema_fields_invalid",
            id="unknown-top-level-field",
        ),
        pytest.param(b"{}", "plan_schema_fields_invalid", id="missing-plan-fields"),
        pytest.param(
            b" " * (cli.MAX_PLAN_BYTES + 1), "plan_size_invalid", id="oversized-plan"
        ),
        pytest.param(
            b"[" * 12000 + b"]" * 12000,
            "plan_json_invalid",
            id="deeply-nested-plan",
        ),
        pytest.param(b"9" * 5000, "plan_json_invalid", id="oversized-integer"),
        pytest.param(b"{bad", "plan_json_invalid", id="invalid-json"),
    ],
)
def test_cli_rejects_malformed_plan_documents(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    document: bytes,
    error: str,
) -> None:
    path = tmp_path / "plan.json"
    path.write_bytes(document)
    assert cli.main(
        [
            "--plan",
            str(path),
            "--plan-sha256",
            "0" * 64,
            "--mountpoint",
            "/Volumes/Data",
        ]
    ) == 2
    assert json.loads(capsys.readouterr().out)["error"] == error


def test_cli_rejects_unknown_nested_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = _plan()
    document = _document(plan)
    document["root_roster"]["owner_checkout"]["unexpected"] = "value"
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert cli.main(
        [
            "--plan",
            str(path),
            "--plan-sha256",
            approved_data_plan_sha256(plan),
            "--mountpoint",
            "/Volumes/Data",
        ]
    ) == 2
    failure = json.loads(capsys.readouterr().out)
    assert failure["error"] == "plan_root_declaration_fields_invalid"
    assert failure["T_qualified"] is False
    assert failure["erase_ready"] is False


def test_cli_requires_explicit_inputs_and_has_no_mock_or_synthetic_switch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([]) == 2
    failure = json.loads(capsys.readouterr().out)
    assert failure["error"] == "arguments_invalid"
    assert failure["T_qualified"] is False
    assert failure["erase_ready"] is False
    assert cli.main(["--synthetic"]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "arguments_invalid"
    assert cli.main(["--output", "/tmp/report.json"]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "arguments_invalid"


def test_fifo_plan_is_rejected_without_blocking(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fifo = tmp_path / "plan.fifo"
    os.mkfifo(fifo)
    assert cli.main(
        [
            "--plan",
            str(fifo),
            "--plan-sha256",
            "0" * 64,
            "--mountpoint",
            "/Volumes/Data",
        ]
    ) == 2
    failure = json.loads(capsys.readouterr().out)
    assert failure["error"] == "plan_must_be_regular_file"
    assert failure["T_qualified"] is False
