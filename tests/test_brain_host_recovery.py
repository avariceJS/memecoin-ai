from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tools import brain_host_recovery as recovery

COMMIT = "a" * 40


def completed(args, *, code=0, stdout=""):
    return subprocess.CompletedProcess(args, code, stdout, "")


class StatusRunner:
    def __init__(self, *, dirty: str = "", launchd: str = "") -> None:
        self.dirty = dirty
        self.launchd = launchd
        self.calls: list[list[str]] = []

    def __call__(self, args, *, cwd=None, timeout=30, env=None):
        command = list(args)
        self.calls.append(command)
        if command == ["git", "--version"]:
            return completed(command, stdout="git version 2.50.0")
        if command[:4] == [sys.executable, "-m", "pip", "--version"]:
            return completed(command, stdout="pip 25.0")
        if "rev-parse" in command and "HEAD" in command:
            return completed(command, stdout=COMMIT)
        if "status" in command and "--porcelain" in command:
            return completed(command, stdout=self.dirty)
        if command == ["launchctl", "list"]:
            return completed(command, stdout=self.launchd)
        return completed(command, code=1)


def _fake_storage(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    calls: list[Path] = []

    class Result:
        volume_uuid = "8f8f1081-9be4-4914-9111-75f1b90255bf"
        root_id = "9428695af6d4f46c08e1a444cdc66de8"
        mount_path = Path("/Volumes/Kingston")
        path = Path("/Volumes/Kingston/BrainData")
        available_bytes = 1234

    def verify_root(path, *, probe=None):
        calls.append(path)
        assert probe == "fake-probe"
        return Result()

    monkeypatch.setitem(
        sys.modules,
        "memecoin_research_storage",
        types.SimpleNamespace(verify_root=verify_root),
    )
    return calls


def test_status_reports_metadata_only_dynamic_launchd_and_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "research"
    repo.mkdir()
    owner = tmp_path / "owner"
    owner.mkdir()
    data = tmp_path / "BrainData"
    data.mkdir()
    protected_marker = data / "do-not-open"
    protected_marker.write_text("synthetic sentinel", encoding="utf-8")
    config_path = tmp_path / "host.json"
    config_path.write_text(
        json.dumps(
            {
                "research_root": str(repo),
                "owner_root": str(owner),
                "data_root": str(data),
                "storage_config": str(tmp_path / "storage.json"),
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "storage.json").write_text("{}", encoding="utf-8")
    storage_calls = _fake_storage(monkeypatch)
    runner = StatusRunner(
        dirty=" M docs/now.md\n",
        launchd="PID\tStatus\tLabel\n42\t0\tai.memecoin.brain.operator\n-\t0\tai.memecoin.brain.custom\n",
    )

    result = recovery.status(
        repo=None,
        config=config_path,
        runner=runner,
        system="Darwin",
        python_version="3.13.0",
        storage_probe="fake-probe",
    )

    assert result["repositories"]["research"]["head"] == COMMIT
    assert result["repositories"]["research"]["dirty"] is True
    assert result["roots"]["owner"]["state"] == "present"
    assert result["roots"]["data"]["state"] == "present"
    assert result["storage"]["state"] == "present"
    assert result["storage"]["root_id"] == "9428695af6d4f46c08e1a444cdc66de8"
    assert result["services"]["launchd"]["loaded_count"] == 2
    assert result["services"]["launchd"]["running_count"] == 1
    assert storage_calls == [tmp_path / "storage.json"]
    assert result["scope"] == {
        "protected_payloads_read": False,
        "secret_values_read": False,
        "service_changes": False,
        "provider_requests": 0,
    }
    assert "do-not-open" not in json.dumps(result)


def test_status_uses_explicit_not_inspected_and_unknown_states(tmp_path: Path) -> None:
    runner = StatusRunner()
    result = recovery.status(repo=None, runner=runner, system="Linux")
    assert result["repositories"]["research"]["state"] == "not_inspected"
    assert result["roots"]["research"]["state"] == "not_inspected"
    assert result["roots"]["owner"]["state"] == "not_inspected"
    assert result["storage"]["state"] == "not_inspected"
    assert result["services"]["launchd"]["state"] == "unknown"
    assert all("/Users/" not in str(value) for value in result["roots"].values())


def test_status_does_not_follow_or_read_symlink_root(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "secret-name"
    sentinel.write_text("sentinel", encoding="utf-8")
    link = tmp_path / "owner-link"
    link.symlink_to(target, target_is_directory=True)
    assert recovery._path_status(link)["kind"] == "symlink"
    assert "secret-name" not in json.dumps(recovery._path_status(link))


class BootstrapRunner:
    def __init__(self, root: Path, *, fail_stage: str | None = None) -> None:
        self.root = root
        self.fail_stage = fail_stage
        self.calls: list[list[str]] = []
        self.environments: list[dict[str, str] | None] = []

    def __call__(self, args, *, cwd=None, timeout=30, env=None):
        command = list(args)
        self.calls.append(command)
        self.environments.append(env)
        if command[-2:] == ["rev-parse", "--show-toplevel"]:
            return completed(command, code=1)
        if command[:2] == ["git", "clone"]:
            Path(command[-1]).mkdir()
            stage = "clone"
        elif "fetch" in command:
            stage = "fetch_exact_commit"
        elif "checkout" in command:
            stage = "checkout_exact_commit"
        elif "rev-parse" in command and "HEAD" in command:
            return completed(command, stdout=COMMIT)
        elif command[-3:-1] == ["-m", "venv"]:
            venv_python = Path(command[-1]) / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.touch()
            stage = "create_environment"
        elif "pip" in command:
            stage = "install_locked_dependencies"
        elif command[-1].endswith("smoke.py"):
            stage = "smoke"
        else:
            return completed(command, code=1)
        return completed(command, code=1 if self.fail_stage == stage else 0)


def test_bootstrap_clones_exact_commit_and_runs_locked_setup_only_in_new_target(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "restores"
    parent.mkdir()
    destination = parent / "clean-research"
    runner = BootstrapRunner(tmp_path)

    result = recovery.bootstrap(
        recovery.BootstrapOptions(
            "https://example.invalid/repo.git", COMMIT, destination, Path("/usr/bin/python3")
        ),
        runner=runner,
    )

    assert result["state"] == "complete"
    assert result["head"] == COMMIT
    assert result["services_changed"] is False
    assert result["storage_changed"] is False
    clone_call = next(command for command in runner.calls if command[:2] == ["git", "clone"])
    assert clone_call == [
        "git",
        "clone",
        "--no-checkout",
        "--",
        "https://example.invalid/repo.git",
        str(destination),
    ]
    assert any("fetch" in command and COMMIT in command for command in runner.calls)
    assert any(
        any(part.endswith("requirements-dev.lock") for part in command) for command in runner.calls
    )
    smoke_index = next(
        index
        for index, command in enumerate(runner.calls)
        if command[-1].endswith("tools/smoke.py")
    )
    assert runner.environments[smoke_index]["PYTHONPATH"] == str(destination / "src")
    assert not any("launchctl" in command[0] for command in runner.calls)


def test_bootstrap_rejects_existing_target_and_non_exact_commit(tmp_path: Path) -> None:
    parent = tmp_path / "restores"
    parent.mkdir()
    existing = parent / "existing"
    existing.mkdir()
    runner = BootstrapRunner(tmp_path)
    options = recovery.BootstrapOptions("remote", COMMIT, existing, Path("python3"))
    with pytest.raises(ValueError, match="destination_must_be_new"):
        recovery.bootstrap(options, runner=runner)
    with pytest.raises(ValueError, match="exact_40_character_commit_required"):
        recovery.bootstrap(
            recovery.BootstrapOptions("remote", "main", parent / "new", Path("python3")),
            runner=runner,
        )


def test_bootstrap_failure_stays_inert_and_preserves_partial_target(tmp_path: Path) -> None:
    parent = tmp_path / "restores"
    parent.mkdir()
    destination = parent / "partial"
    runner = BootstrapRunner(tmp_path, fail_stage="smoke")
    result = recovery.bootstrap(
        recovery.BootstrapOptions("remote", COMMIT, destination, Path("python3")),
        runner=runner,
    )
    assert result["state"] == "failed"
    assert result["failed_stage"] == "smoke"
    assert result["services_changed"] is False
    assert destination.is_dir()
