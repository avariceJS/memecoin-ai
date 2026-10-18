"""Read-only Linux capability report safety and aggregation contracts."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from memecoin_operator import linux_capabilities as capabilities


def test_role_gate_requires_all_required_capabilities():
    available = {"one": {"status": "available"}, "two": {"status": "available"}}
    assert capabilities._aggregate(available, ("one", "two"))["status"] == "available"
    available["two"]["status"] = "absent"
    assert capabilities._aggregate(available, ("one", "two"))["status"] == "absent"
    available["two"]["status"] = "indeterminate"
    assert capabilities._aggregate(available, ("one", "two"))["status"] == "indeterminate"


def test_report_keeps_optional_ml_and_gpu_out_of_required_roles(tmp_path: Path, monkeypatch):
    ok = {"status": "available", "reason": "fixture"}
    monkeypatch.setattr(
        capabilities,
        "_python_build_checks",
        lambda _workspace: {
            key: dict(ok)
            for key in (
                "python_version",
                "venv",
                "ensurepip",
                "git",
                "make",
                "free_space",
                "os",
                "architecture",
            )
        },
    )
    monkeypatch.setattr(
        capabilities,
        "_service_checks",
        lambda _workspace: {
            key: dict(ok)
            for key in (
                "systemd",
                "ext4",
                "nsenter",
                "mountinfo",
                "fdinfo",
                "blkid",
                "udevadm",
                "root_action",
            )
        },
    )
    monkeypatch.setattr(
        capabilities,
        "_cpu_ml_check",
        lambda: capabilities._check("absent", "cpu_ml_dependencies_missing"),
    )
    monkeypatch.setattr(
        capabilities,
        "_gpu_observation",
        lambda: capabilities._check("available", "driver_and_device_observed"),
    )
    monkeypatch.setattr(capabilities, "_unit_observation", lambda _unit: None)
    monkeypatch.setattr(capabilities, "_source_os", lambda: dict(ok))
    monkeypatch.setattr(capabilities.platform, "machine", lambda: "x86_64")

    result = capabilities.report(source_commit="a" * 40, workspace=tmp_path)

    assert result["roles"]["source_build"]["status"] == "available"
    assert result["roles"]["inert_service_ext4"]["status"] == "available"
    assert result["roles"]["cpu_ml"]["status"] == "absent"
    assert result["roles"]["gpu"]["status"] == "indeterminate"
    assert result["roles"]["gpu"]["observation"]["status"] == "available"


def test_report_rejects_invalid_commit_and_missing_workspace(tmp_path: Path):
    with pytest.raises(ValueError, match="source_commit_invalid"):
        capabilities.report(source_commit="not-a-commit", workspace=tmp_path)
    with pytest.raises(OSError):
        capabilities.report(source_commit="a" * 40, workspace=tmp_path / "missing")


def test_command_uses_fixed_minimal_environment_and_bounded_output(monkeypatch):
    observed = {}

    def run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="safe version\n", stderr="")

    monkeypatch.setattr(capabilities.subprocess, "run", run)
    assert capabilities._command(["/usr/bin/git", "--version"]) == ("safe version", "ok")
    assert observed["env"] == capabilities.SAFE_ENV
    assert observed["timeout"] == 2.0


def test_unit_name_is_validated_before_systemctl_probe(monkeypatch):
    monkeypatch.setattr(capabilities, "_tool", lambda _name: "/usr/bin/systemctl")
    with pytest.raises(ValueError, match="unit_name_invalid"):
        capabilities._unit_observation("x.service;touch /tmp/unwanted")
