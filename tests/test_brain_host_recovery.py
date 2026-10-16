from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tools import brain_host_recovery as recovery

COMMIT = "a" * 40


def completed(args, *, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, code, stdout, stderr)


class StatusRunner:
    def __init__(
        self,
        *,
        dirty: str = "",
        launchd: str = "",
        cwd_output: str = "",
        cwd_code: int = 0,
    ) -> None:
        self.dirty = dirty
        self.launchd = launchd
        self.cwd_output = cwd_output
        self.cwd_code = cwd_code
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
        if command == ["lsof", "-nP", "-Fpcn", "-d", "cwd"]:
            return completed(command, code=self.cwd_code, stdout=self.cwd_output)
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
        launchd=(
            "PID\tStatus\tLabel\n"
            "42\t0\tai.memecoin.brain.operator\n"
            "-\t0\tai.memecoin.brain.custom\n"
            "-\t0\tai.memecoin.d43.calendar01\n"
        ),
        cwd_output=f"p77\nccalendar_schedule.py\nn{owner}\n",
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
    assert result["services"]["launchd"]["loaded_count"] == 3
    assert result["services"]["launchd"]["running_count"] == 1
    assert "ai.memecoin.d43.calendar01" in {
        row["label"] for row in result["services"]["launchd"]["labels"]
    }
    assert storage_calls == [tmp_path / "storage.json"]
    assert result["processes"]["workspace"] == {
        "state": "present",
        "processes": [
            {
                "pid": 77,
                "name": "calendar_schedule.py",
                "cwd": str(owner),
                "roots": ["owner"],
            }
        ],
        "count": 1,
        "arguments_read": False,
        "environment_read": False,
    }
    process_call = next(call for call in runner.calls if call[0] == "lsof")
    assert "-d" in process_call and "cwd" in process_call
    assert all("args" not in argument and "env" not in argument for argument in process_call)
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
    assert result["processes"]["workspace"]["state"] == "not_inspected"
    assert all("/Users/" not in str(value) for value in result["roots"].values())


def test_workspace_process_status_excludes_unassociated_cwds(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    runner = StatusRunner(cwd_output=f"p88\ncworker\nn{unrelated}\n")
    result = recovery.status(repo=None, owner_root=owner, runner=runner, system="Darwin")
    assert result["processes"]["workspace"]["state"] == "absent"
    assert result["processes"]["workspace"]["processes"] == []


def test_workspace_process_parse_failure_is_unknown(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    runner = StatusRunner(cwd_output="cmissing-pid\n")
    result = recovery.status(repo=None, owner_root=owner, runner=runner, system="Darwin")
    assert result["processes"]["workspace"]["state"] == "unknown"
    assert result["processes"]["workspace"]["reason"] == "cwd_process_probe_incomplete"


def test_workspace_process_probe_failure_is_unknown(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    runner = StatusRunner(cwd_code=1)
    result = recovery.status(repo=None, owner_root=owner, runner=runner, system="Darwin")
    assert result["processes"]["workspace"]["state"] == "unknown"


def test_workspace_process_inventory_is_unknown_off_macos(tmp_path: Path) -> None:
    owner = tmp_path / "owner"
    owner.mkdir()
    runner = StatusRunner()
    result = recovery.status(repo=None, owner_root=owner, runner=runner, system="Linux")
    assert result["processes"]["workspace"] == {
        "state": "unknown",
        "reason": "cwd_process_probe_not_supported",
        "processes": [],
    }
    assert not any(call and call[0] == "lsof" for call in runner.calls)


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
    def __init__(
        self,
        root: Path,
        *,
        fail_stage: str | None = None,
        python_version: str = "3.13.0",
        failure_stdout: str = "",
        failure_stderr: str = "",
    ) -> None:
        self.root = root
        self.fail_stage = fail_stage
        self.python_version = python_version
        self.failure_stdout = failure_stdout
        self.failure_stderr = failure_stderr
        self.calls: list[list[str]] = []
        self.environments: list[dict[str, str] | None] = []
        self.working_directories: list[Path | None] = []

    def __call__(self, args, *, cwd=None, timeout=30, env=None):
        command = list(args)
        self.calls.append(command)
        self.environments.append(env)
        self.working_directories.append(cwd)
        if len(command) >= 3 and command[1] == "-c":
            if "site.ENABLE_USER_SITE" in command[-1]:
                return completed(
                    command, code=1 if self.fail_stage == "verify_environment_isolation" else 0
                )
            return completed(command, stdout=self.python_version)
        if command[-2:] == ["rev-parse", "--show-toplevel"]:
            return completed(command, code=1)
        if command[:2] == ["git", "clone"]:
            Path(command[-1]).mkdir()
            (Path(command[-1]) / "requirements-dev.lock").write_text("fixture==1\n")
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
        elif "pip" in command and "check" in command:
            stage = "verify_dependencies"
        elif "pip" in command:
            stage = "install_locked_dependencies"
        elif "site.ENABLE_USER_SITE" in command[-1]:
            stage = "verify_environment_isolation"
        elif command[-1].endswith("smoke.py"):
            stage = "smoke"
        else:
            return completed(command, code=1)
        failed = self.fail_stage == stage
        return completed(
            command,
            code=1 if failed else 0,
            stdout=self.failure_stdout if failed else "",
            stderr=self.failure_stderr if failed else "",
        )


def test_bootstrap_clones_exact_commit_and_runs_locked_setup_only_in_new_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "restores"
    parent.mkdir()
    destination = parent / "clean-research"
    runner = BootstrapRunner(tmp_path)
    monkeypatch.setenv("PIP_INDEX_URL", "https://host-cache.invalid/simple")
    monkeypatch.setenv("PIP_CONFIG_FILE", str(tmp_path / "host-pip.conf"))
    monkeypatch.setenv("PIP_CACHE_DIR", str(tmp_path / "host-pip-cache"))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "host-pythonpath"))
    monkeypatch.setenv("PYTHONUSERBASE", str(tmp_path / "host-user-site"))

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
    assert result["bootstrap_isolation"] == {
        "state": "applied",
        "scope": "python_venv_install_checks_and_smoke",
        "temporary_home": True,
        "pip_configuration": "disabled",
        "pip_cache": "disabled",
        "python_user_site": "disabled",
        "package_index": "https://pypi.org/simple",
    }
    assert len(result["dependency_lock_sha256"]) == 64
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
    install_index = next(
        index
        for index, command in enumerate(runner.calls)
        if "pip" in command and "install" in command
    )
    assert runner.calls[install_index] == [
        str(destination / ".venv" / "bin" / "python"),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--index-url",
        "https://pypi.org/simple",
        "-r",
        "requirements-dev.lock",
        "-e",
        ".",
    ]
    assert runner.working_directories[install_index] == destination
    dependency_check_index = next(
        index
        for index, command in enumerate(runner.calls)
        if "pip" in command and "check" in command
    )
    isolation_probe_index = next(
        index
        for index, command in enumerate(runner.calls)
        if "site.ENABLE_USER_SITE" in command[-1]
    )
    smoke_index = next(
        index
        for index, command in enumerate(runner.calls)
        if command[-1].endswith("tools/smoke.py")
    )
    assert dependency_check_index < isolation_probe_index < smoke_index
    assert runner.working_directories[smoke_index] == destination
    environment = runner.environments[smoke_index]
    assert environment is not None
    assert set(environment) == {
        "PATH",
        "HOME",
        "TMPDIR",
        "PIP_CONFIG_FILE",
        "PIP_NO_CACHE_DIR",
        "PYTHONNOUSERSITE",
    }
    assert environment["PATH"] == os.defpath
    assert environment["HOME"] != os.environ["HOME"]
    assert environment["TMPDIR"].startswith(environment["HOME"])
    assert environment["PIP_CONFIG_FILE"] == os.devnull
    assert environment["PIP_NO_CACHE_DIR"] == "1"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert not any("host-cache.invalid" in str(value) for value in environment.values())
    assert not any("host-pip-cache" in str(value) for value in environment.values())
    assert not any("host-pythonpath" in str(value) for value in environment.values())
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


def test_bootstrap_rejects_unsupported_python_before_clone_or_target_creation(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "restores"
    parent.mkdir()
    destination = parent / "unsupported"
    runner = BootstrapRunner(tmp_path, python_version="3.9.6")
    with pytest.raises(ValueError, match="python_3_11_or_newer_required"):
        recovery.bootstrap(
            recovery.BootstrapOptions("remote", COMMIT, destination, Path("/usr/bin/python3")),
            runner=runner,
        )
    assert not destination.exists()
    assert not any(command[:2] == ["git", "clone"] for command in runner.calls)


def test_bootstrap_accepts_python_311(tmp_path: Path) -> None:
    parent = tmp_path / "restores"
    parent.mkdir()
    runner = BootstrapRunner(tmp_path, python_version="3.11.0")
    result = recovery.bootstrap(
        recovery.BootstrapOptions("remote", COMMIT, parent / "supported", Path("/python311")),
        runner=runner,
    )
    assert result["state"] == "complete"


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
    assert result["bootstrap_isolation"]["state"] == "applied"
    assert result["services_changed"] is False
    assert destination.is_dir()


def test_bootstrap_clone_failure_does_not_claim_isolation_started(tmp_path: Path) -> None:
    parent = tmp_path / "restores"
    parent.mkdir()
    runner = BootstrapRunner(tmp_path, fail_stage="clone")
    result = recovery.bootstrap(
        recovery.BootstrapOptions(
            "https://example.invalid/repo.git", COMMIT, parent / "failed-clone", Path("python3")
        ),
        runner=runner,
    )
    assert result["state"] == "failed"
    assert result["failed_stage"] == "clone"
    assert result["bootstrap_isolation"] == {
        "state": "not_started",
        "scope": "python_venv_install_checks_and_smoke",
    }


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    [
        ("fatal: couldn't find remote ref", "commit_unavailable_or_wrong_pin"),
        ("fatal: Authentication failed", "authentication_or_access"),
        ("fatal: Could not resolve host: git.example.invalid", "network_unavailable"),
        ("unexpected private response", "fetch_failed"),
    ],
)
def test_bootstrap_fetch_failure_class_is_safe_and_preserves_partial_target(
    tmp_path: Path, diagnostic: str, expected: str
) -> None:
    parent = tmp_path / "restores"
    parent.mkdir()
    destination = parent / "partial-fetch"
    sensitive = "https://user:secret-token@git.example.invalid/private.git"
    runner = BootstrapRunner(
        tmp_path,
        fail_stage="fetch_exact_commit",
        failure_stdout=f"diagnostic={diagnostic}; remote={sensitive}",
        failure_stderr=f"credential=ghp_sensitive-token {diagnostic}",
    )

    result = recovery.bootstrap(
        recovery.BootstrapOptions(sensitive, COMMIT, destination, Path("python3")),
        runner=runner,
    )

    assert result["state"] == "failed"
    assert result["failed_stage"] == "fetch_exact_commit"
    assert result["exit_code"] == 1
    assert result["failure_class"] == expected
    assert destination.is_dir()
    serialized = json.dumps(result)
    assert sensitive not in serialized
    assert "secret-token" not in serialized
    assert "ghp_sensitive-token" not in serialized
    assert "diagnostic=" not in serialized
