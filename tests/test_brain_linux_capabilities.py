"""Read-only Linux capability report safety and aggregation contracts."""

import json
import subprocess
import sys
import time
from pathlib import Path

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
        lambda _path, _unit: {
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
                "unit_inert",
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
    monkeypatch.setattr(
        capabilities,
        "_source_checkout",
        lambda _workspace, _commit: dict(ok),
    )
    monkeypatch.setattr(capabilities, "_source_os", lambda: dict(ok))
    monkeypatch.setattr(capabilities.platform, "machine", lambda: "x86_64")

    result = capabilities.report(source_commit="a" * 40, workspace=tmp_path)

    assert result["roles"]["source_build"]["status"] == "available"
    assert result["roles"]["inert_service_host_ext4"]["status"] == "available"
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
    popen = capabilities.subprocess.Popen

    def capture(argv, **kwargs):
        observed.update(kwargs)
        return popen(argv, **kwargs)

    monkeypatch.setattr(capabilities.subprocess, "Popen", capture)
    result = capabilities._command(
        [sys.executable, "-c", "import json,os; print(json.dumps(dict(os.environ)))"]
    )
    assert result is not None and result[1] == "ok"
    child_env = json.loads(result[0])
    assert {key: child_env[key] for key in capabilities.SAFE_ENV} == capabilities.SAFE_ENV
    assert set(child_env) <= set(capabilities.SAFE_ENV) | {"__CF_USER_TEXT_ENCODING"}
    assert observed["env"] == capabilities.SAFE_ENV
    assert observed["stdin"] is capabilities.subprocess.DEVNULL


def test_source_checkout_rejects_forged_hash_and_dirty_tree(monkeypatch):
    monkeypatch.setattr(capabilities, "_tool", lambda _name: "/usr/bin/git")
    responses = iter([("b" * 40, "ok"), ("", "ok")])
    monkeypatch.setattr(capabilities, "_command", lambda _argv: next(responses))
    mismatch = capabilities._source_checkout(Path("/workspace"), "a" * 40)
    assert mismatch["status"] == "absent"
    assert mismatch["reason"] == "source_commit_mismatch"

    responses = iter([("a" * 40, "ok"), ("?? untracked\n", "ok")])
    dirty = capabilities._source_checkout(Path("/workspace"), "a" * 40)
    assert dirty["reason"] == "source_checkout_dirty"
    assert dirty["dirty_entry_count"] == 1


def test_source_checkout_disables_fsmonitor_and_optional_index_writes(tmp_path, monkeypatch):
    git = capabilities.shutil.which("git")
    if git is None:
        pytest.skip("git unavailable")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    marker = tmp_path / "fsmonitor-ran"
    script = tmp_path / "fsmonitor"
    script.write_text(f"#!/bin/sh\ntouch {marker}\nprintf 'token'\n", encoding="utf-8")
    script.chmod(0o755)
    for argv in (
        [git, "init", "-q", str(workspace)],
        [git, "-C", str(workspace), "config", "user.email", "test@example.invalid"],
        [git, "-C", str(workspace), "config", "user.name", "Test"],
    ):
        subprocess.run(argv, check=True, env=capabilities.SAFE_ENV, capture_output=True)
    (workspace / "tracked").write_text("data\n", encoding="utf-8")
    subprocess.run([git, "-C", str(workspace), "add", "tracked"], check=True)
    subprocess.run([git, "-C", str(workspace), "commit", "-qm", "fixture"], check=True)
    subprocess.run([git, "-C", str(workspace), "config", "core.fsmonitor", str(script)], check=True)
    index = workspace / ".git" / "index"
    before = index.stat().st_mtime_ns
    head = subprocess.check_output(
        [git, "-C", str(workspace), "rev-parse", "HEAD"], text=True
    ).strip()
    monkeypatch.setattr(capabilities, "_tool", lambda _name: git)

    result = capabilities._source_checkout(workspace, head)

    assert result["status"] == "available"
    assert not marker.exists()
    assert index.stat().st_mtime_ns == before


def test_state_path_and_unit_must_support_inert_host_claim(monkeypatch, tmp_path):
    assert capabilities._ext4_check(None)["reason"] == "state_path_not_supplied"
    monkeypatch.setattr(
        capabilities, "_ext4_check", lambda _path: capabilities._check("available", "ext4")
    )
    monkeypatch.setattr(
        capabilities, "_systemd_check", lambda: capabilities._check("available", "systemd")
    )
    monkeypatch.setattr(
        capabilities, "_version_check", lambda _name: capabilities._check("available", "present")
    )
    monkeypatch.setattr(
        capabilities, "_root_action_check", lambda: capabilities._check("available", "sudo")
    )
    active = capabilities._check(
        "available",
        "unit_status_observed",
        LoadState="loaded",
        ActiveState="active",
        UnitFileState="enabled",
        MainPID="12",
    )
    checks = capabilities._service_checks(tmp_path, active)
    assert checks["unit_inert"]["status"] == "absent"
    assert capabilities._service_checks(tmp_path, None)["unit_inert"]["status"] == "indeterminate"


def test_filesystem_check_measures_the_requested_state_path(monkeypatch, tmp_path):
    observed = {}
    monkeypatch.setattr(capabilities, "_tool", lambda _name: "/usr/bin/findmnt")

    def command(argv):
        observed["path"] = argv[-1]
        return ("ext4", "ok")

    monkeypatch.setattr(capabilities, "_command", command)
    state_path = tmp_path / "durable-state"
    state_path.mkdir()
    check = capabilities._ext4_check(state_path)
    assert check["status"] == "available"
    assert observed["path"] == str(state_path)


def test_cpu_ml_checks_lock_versions_and_disables_bytecode(monkeypatch):
    versions = {"numpy": "2.5.3", "torch": "2.14.1"}
    monkeypatch.setattr(capabilities.importlib.metadata, "version", lambda name: versions[name])
    monkeypatch.setattr(capabilities, "_ml_pins", lambda: {"numpy": "2.5.3", "torch": "2.14.1"})
    observed = {}

    def command(argv, **_kwargs):
        observed["argv"] = argv
        return ('{"cpu_sum":6.0,"numpy":"2.5.3","torch":"2.14.1"}', "ok")

    monkeypatch.setattr(capabilities, "_command", command)
    assert capabilities._cpu_ml_check()["status"] == "available"
    assert "-B" in observed["argv"]
    versions["torch"] = "wrong"
    assert capabilities._cpu_ml_check()["reason"] == "cpu_ml_versions_mismatch"


def test_cpu_ml_checks_imported_versions_against_pins(monkeypatch):
    versions = {"numpy": "2.5.3", "torch": "2.14.1"}
    monkeypatch.setattr(capabilities.importlib.metadata, "version", lambda name: versions[name])
    monkeypatch.setattr(capabilities, "_ml_pins", lambda: dict(versions))
    monkeypatch.setattr(
        capabilities,
        "_command",
        lambda *_args, **_kwargs: ('{"cpu_sum":6.0,"numpy":"wrong","torch":"2.14.1"}', "ok"),
    )
    assert capabilities._cpu_ml_check()["reason"] == "cpu_ml_imported_versions_mismatch"


def test_ml_pins_are_read_from_installed_distribution_metadata(monkeypatch):
    monkeypatch.setattr(
        capabilities.importlib.metadata,
        "requires",
        lambda _name: [
            'torch==2.14.1; extra == "ml"',
            'numpy==2.5.3; extra == "ml"',
            'ruff>=0.11; extra == "dev"',
        ],
    )
    assert capabilities._ml_pins() == {"torch": "2.14.1", "numpy": "2.5.3"}


def test_command_stops_on_oversized_output(monkeypatch):
    del monkeypatch
    result = capabilities._command([sys.executable, "-c", "print('x' * 10000)"])
    assert result == ("", "output_oversize")


def test_command_timeout_includes_wait_after_pipe_eof():
    script = "import os,time; os.close(1); os.close(2); time.sleep(5)"
    started = time.monotonic()
    assert capabilities._command([sys.executable, "-c", script], timeout=0.2) is None
    assert time.monotonic() - started < 2


def test_unit_name_is_validated_before_systemctl_probe(monkeypatch):
    monkeypatch.setattr(capabilities, "_tool", lambda _name: "/usr/bin/systemctl")
    with pytest.raises(ValueError, match="unit_name_invalid"):
        capabilities._unit_observation("x.service;touch /tmp/unwanted")
