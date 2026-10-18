from __future__ import annotations

import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from tools.brain_host_inert_restore_v1 import HANDOFF_SCHEMA
from tools.brain_host_restore_assessment import (
    REQUIRED_TARGETS,
    _digest,
    _load_restore_handoff,
    _readonly_repository,
    main,
)
from tools.brain_senses_external_writer_v1 import canonical_json


def _handoff(tmp_path: Path) -> dict[str, object]:
    return {
        "schema": HANDOFF_SCHEMA,
        "status": "blocked",
        "set_id": "1" * 32,
        "expected_commit": "2" * 40,
        "expected_volume_uuid": "synthetic-volume",
        "expected_root_id": "3" * 32,
        "expected_index_sha256": "4" * 64,
        "witness_id": "5" * 32,
        "expected_witness_sha256": "6" * 64,
        "witness_targets": {role: str(tmp_path / role) for role in sorted(REQUIRED_TARGETS)},
        "repo": str(tmp_path / "repo"),
        "activation_authorized": False,
        "safe_to_erase": False,
    }


def test_loads_integrity_checked_conductor_result_and_maps_handoff(tmp_path: Path) -> None:
    handoff = _handoff(tmp_path)
    conductor_result = {
        "schema": "brain-host-inert-restore-v1",
        "status": "complete_inert_quarantine",
        "assessor_handoff": handoff,
    }
    conductor_result["evidence_sha256"] = _digest(canonical_json(conductor_result))
    path = tmp_path / "conductor-result.json"
    path.write_text(json.dumps(conductor_result), encoding="utf-8")

    assert _load_restore_handoff(path) == handoff


def test_loads_standalone_handoff_bundle(tmp_path: Path) -> None:
    handoff = _handoff(tmp_path)
    path = tmp_path / "handoff.json"
    path.write_text(json.dumps(handoff), encoding="utf-8")

    assert _load_restore_handoff(path) == handoff


def test_rejects_modified_conductor_result(tmp_path: Path) -> None:
    conductor_result = {
        "schema": "brain-host-inert-restore-v1",
        "status": "complete_inert_quarantine",
        "assessor_handoff": _handoff(tmp_path),
        "evidence_sha256": "0" * 64,
    }
    path = tmp_path / "tampered-result.json"
    path.write_text(json.dumps(conductor_result), encoding="utf-8")

    with pytest.raises(ValueError, match="handoff_parent_integrity_mismatch"):
        _load_restore_handoff(path)


def test_rejects_handoff_with_activation_or_relative_targets(tmp_path: Path) -> None:
    handoff = _handoff(tmp_path)
    handoff["activation_authorized"] = True
    path = tmp_path / "active-handoff.json"
    path.write_text(json.dumps(handoff), encoding="utf-8")
    with pytest.raises(ValueError, match="handoff_authority_fields_invalid"):
        _load_restore_handoff(path)

    handoff = _handoff(tmp_path)
    handoff["witness_targets"]["owner_work"] = "relative/target"
    path.write_text(json.dumps(handoff), encoding="utf-8")
    with pytest.raises(ValueError, match="handoff_targets_invalid"):
        _load_restore_handoff(path)


def test_cli_handoff_maps_pins_and_targets_to_assessor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.brain_host_restore_assessment as assessment

    handoff = _handoff(tmp_path)
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
    observed = {}

    @contextmanager
    def open_reader(_storage_config):
        yield object(), lambda: None

    def assess(_reader, **kwargs):
        observed.update(kwargs)
        return {"status": "blocked"}

    monkeypatch.setattr(assessment, "open_readonly_storage_reader", open_reader)
    monkeypatch.setattr(assessment, "assess_restore_v1", assess)
    monkeypatch.setattr(assessment, "_print", lambda *_args: None)
    result = main(
        [
            "--handoff",
            str(handoff_path),
            "--storage-config",
            str(tmp_path / "binding.json"),
            "--format",
            "json",
        ]
    )

    assert result == 1
    assert observed["repo"] == Path(handoff["repo"])
    assert observed["set_id"] == handoff["set_id"]
    assert observed["expected_commit"] == handoff["expected_commit"]
    assert observed["expected_volume_uuid"] == handoff["expected_volume_uuid"]
    assert observed["expected_root_id"] == handoff["expected_root_id"]
    assert observed["expected_index_sha256"] == handoff["expected_index_sha256"]
    assert observed["expected_witness_sha256"] == handoff["expected_witness_sha256"]
    assert observed["witness_id"] == handoff["witness_id"]
    assert observed["witness_targets"] == {
        role: Path(target) for role, target in handoff["witness_targets"].items()
    }


def test_handoff_input_rejects_symlink_fifo_and_oversized_file(tmp_path: Path) -> None:
    valid_path = tmp_path / "valid.json"
    valid_path.write_text(json.dumps(_handoff(tmp_path)), encoding="utf-8")
    symlink_path = tmp_path / "handoff-link.json"
    symlink_path.symlink_to(valid_path)
    with pytest.raises(ValueError, match="handoff_invalid_json"):
        _load_restore_handoff(symlink_path)

    fifo_path = tmp_path / "handoff.fifo"
    fifo_path.parent.mkdir(exist_ok=True)
    os.mkfifo(fifo_path)
    with pytest.raises(ValueError, match="handoff_not_regular_file"):
        _load_restore_handoff(fifo_path)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="handoff_too_large"):
        _load_restore_handoff(oversized)


def test_handoff_rejects_symlinked_repository_path(tmp_path: Path) -> None:
    real_repo = tmp_path / "real-repo"
    real_repo.mkdir()
    repo_link = tmp_path / "repo-link"
    repo_link.symlink_to(real_repo, target_is_directory=True)
    handoff = _handoff(tmp_path)
    handoff["repo"] = str(repo_link)
    path = tmp_path / "handoff.json"
    path.write_text(json.dumps(handoff), encoding="utf-8")

    with pytest.raises(ValueError, match="handoff_repo_path_symlinked"):
        _load_restore_handoff(path)


def test_readonly_repository_disables_fsmonitor_and_hooks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = []

    def runner(argv, *, timeout, env):
        calls.append((argv, timeout, env))
        stdout = "a" * 40 + "\n" if "rev-parse" in argv else ""
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    assert _readonly_repository(repo, runner)["state"] == "present"
    assert len(calls) == 2
    for argv, _timeout, env in calls:
        assert argv[1:5] == [
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
        ]
        assert env["GIT_OPTIONAL_LOCKS"] == "0"
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull
