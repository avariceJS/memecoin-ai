from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools.brain_host_restore_flow_v1 import (
    HANDOFF_SCHEMA,
    ROOT_ROLES,
    RestoreFlowError,
    _read_handoff,
    _resolve_source_roots,
    _write_create_only,
    restore_inert,
)


def _handoff(root: Path) -> dict[str, object]:
    roots: dict[str, dict[str, str]] = {}
    for role in ROOT_ROLES:
        path = root / role
        roots[role] = {
            "state": "absent",
            "path": str(path),
            "disposition": "confirmed_absent",
            "reason": "not present in this isolated fixture",
        }
    return {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "handoff_id": "a" * 32,
        "execution_commit": "b" * 40,
        "entry_card_sha256": "c" * 64,
        "kingston": {
            "volume_uuid": "11111111-1111-4111-8111-111111111111",
            "root_id": "d" * 32,
        },
        "recovery_set": {
            "set_id": "e" * 32,
            "index_sha256": "f" * 64,
            "research_commit": "1" * 40,
            "owner_commit": "2" * 40,
        },
        "target_policy": "new_quarantine_only",
        "authorization_scope": "inert_restore_only",
        "source_roots": roots,
    }


def _write_handoff(path: Path, value: dict[str, object]) -> str:
    payload = (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()
    path.write_bytes(payload)
    path.chmod(0o600)
    return hashlib.sha256(payload).hexdigest()


def test_external_pin_and_canonical_handoff_are_required(tmp_path: Path) -> None:
    path = tmp_path / "handoff.json"
    digest = _write_handoff(path, _handoff(tmp_path))

    parsed, raw = _read_handoff(path, digest)

    assert parsed["schema"] == HANDOFF_SCHEMA
    assert hashlib.sha256(raw).hexdigest() == digest
    with pytest.raises(RestoreFlowError, match="external_handoff_pin_mismatch"):
        _read_handoff(path, "0" * 64)


def test_absent_root_requires_a_locator_that_is_really_absent(tmp_path: Path) -> None:
    value = _handoff(tmp_path)
    role = "owner_work"
    Path(value["source_roots"][role]["path"]).mkdir()

    with pytest.raises(RestoreFlowError, match="declared_absent_but_present"):
        _resolve_source_roots(value, tmp_path)


def test_present_root_must_be_canonical_and_research_root_must_match(tmp_path: Path) -> None:
    value = _handoff(tmp_path)
    research = tmp_path / "research"
    research.mkdir()
    value["source_roots"]["research_checkout"] = {"state": "present", "path": str(research)}

    states, paths = _resolve_source_roots(value, research)

    assert states["research_checkout"] == "present"
    assert research in paths
    with pytest.raises(RestoreFlowError, match="research_checkout_not_current_repository"):
        _resolve_source_roots(value, tmp_path)


def test_run_records_are_create_only(tmp_path: Path) -> None:
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        _write_create_only(directory_fd, "INSPECT.JSON", b"{}\n")
        with pytest.raises(FileExistsError):
            _write_create_only(directory_fd, "INSPECT.JSON", b"{}\n")
    finally:
        os.close(directory_fd)
    assert (tmp_path / "INSPECT.JSON").read_bytes() == b"{}\n"


def test_research_checkout_cannot_be_declared_absent(tmp_path: Path) -> None:
    value = _handoff(tmp_path)

    with pytest.raises(RestoreFlowError, match="research_checkout_must_be_present"):
        _resolve_source_roots(value, tmp_path)


def test_reservation_rejects_parent_identity_change_and_overlap(tmp_path: Path) -> None:
    from tools.brain_host_restore_flow_v1 import _reserve_run_directory

    parent = tmp_path / "runs"
    parent.mkdir(mode=0o700)
    metadata = parent.stat()
    with pytest.raises(RestoreFlowError, match="identity_changed"):
        _reserve_run_directory(
            parent,
            "a" * 32,
            expected_identity={"device": metadata.st_dev, "inode": metadata.st_ino + 1},
            storage_device=None,
            storage_config=tmp_path / "storage.json",
            expected_volume_uuid="11111111-1111-4111-8111-111111111111",
            expected_root_id="d" * 32,
            forbidden_roots=[],
            target_parent=tmp_path / "target",
        )
    assert list(parent.iterdir()) == []
    with pytest.raises(RestoreFlowError, match="overlaps_forbidden_root"):
        _reserve_run_directory(
            parent,
            "a" * 32,
            expected_identity={"device": metadata.st_dev, "inode": metadata.st_ino},
            storage_device=None,
            storage_config=tmp_path / "storage.json",
            expected_volume_uuid="11111111-1111-4111-8111-111111111111",
            expected_root_id="d" * 32,
            forbidden_roots=[tmp_path],
            target_parent=tmp_path / "target",
        )
    assert list(parent.iterdir()) == []


def test_reservation_maps_binding_failure_without_writing(tmp_path: Path, monkeypatch: Any) -> None:
    from tools import brain_host_restore_flow_v1 as flow

    parent = tmp_path / "runs"
    target = tmp_path / "target"
    parent.mkdir(mode=0o700)
    target.mkdir(mode=0o700)
    metadata = parent.stat()
    monkeypatch.setattr(
        flow,
        "_revalidate_storage_binding",
        lambda *_args: (_ for _ in ()).throw(OSError("probe failed")),
    )

    with pytest.raises(RestoreFlowError, match="kingston_binding_revalidation_failed:OSError"):
        flow._reserve_run_directory(
            parent,
            "a" * 32,
            expected_identity={"device": metadata.st_dev, "inode": metadata.st_ino},
            storage_device=metadata.st_dev + 1,
            storage_config=tmp_path / "storage.json",
            expected_volume_uuid="11111111-1111-4111-8111-111111111111",
            expected_root_id="d" * 32,
            forbidden_roots=[],
            target_parent=target,
        )
    assert list(parent.iterdir()) == []


def test_target_revalidation_requires_same_empty_directory(tmp_path: Path) -> None:
    from tools.brain_host_restore_flow_v1 import _revalidate_target_parent

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    metadata = target.stat()
    expected = {"device": metadata.st_dev, "inode": metadata.st_ino}
    _revalidate_target_parent(
        target, expected, storage_device=metadata.st_dev + 1, forbidden_roots=[]
    )
    (target / "appeared-after-inspection").touch()
    with pytest.raises(RestoreFlowError, match="must_be_empty"):
        _revalidate_target_parent(
            target, expected, storage_device=metadata.st_dev + 1, forbidden_roots=[]
        )


def test_unsafe_run_parent_gets_no_record_directory(tmp_path: Path, monkeypatch: Any) -> None:
    from tools import brain_host_restore_flow_v1 as flow

    handoff_path = tmp_path / "handoff.json"
    value = _handoff(tmp_path)
    digest = _write_handoff(handoff_path, value)
    run_parent = tmp_path / "runs"
    run_parent.mkdir(mode=0o700)
    before = list(run_parent.iterdir())
    monkeypatch.setattr(
        flow,
        "inspect_restore",
        lambda **_kwargs: {
            "schema": flow.SCHEMA,
            "operation": "inspect",
            "status": "blocked",
            "blockers": ["run_parent_overlaps_forbidden_root"],
            "run_parent_safe_for_records": False,
            "activation_authorized": False,
            "final_readiness": False,
            "safe_to_erase": False,
        },
    )

    result = restore_inert(
        handoff_path=handoff_path,
        expected_handoff_sha256=digest,
        repository_root=tmp_path,
        storage_config=tmp_path / "storage.json",
        target_parent=tmp_path / "target",
        run_parent=run_parent,
        same_host_isolated=True,
    )

    assert result["status"] == "blocked_without_run_record_parent_unsafe"
    assert result["run_record_created"] is False
    assert list(run_parent.iterdir()) == before


def test_direct_cli_help_imports_from_neutral_working_directory(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "tools" / "brain_host_restore_flow_v1.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "restore-inert" in result.stdout


def test_missing_entry_cards_return_named_blockers_not_tracebacks(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from tools import brain_host_restore_flow_v1 as flow

    repo = tmp_path / "repo"
    repo.mkdir(mode=0o700)
    storage_root = tmp_path / "kingston-data"
    storage_root.mkdir(mode=0o700)
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    run_parent = tmp_path / "runs"
    run_parent.mkdir(mode=0o700)
    storage_device = run_parent.stat().st_dev + 1
    value = _handoff(tmp_path)
    value["execution_commit"] = "b" * 40
    value["recovery_set"]["research_commit"] = "b" * 40
    value["source_roots"]["research_checkout"] = {"state": "present", "path": str(repo)}
    handoff_path = tmp_path / "handoff.json"
    digest = _write_handoff(handoff_path, value)

    monkeypatch.setattr(
        flow,
        "_git_state",
        lambda *_args: {"head": "b" * 40, "clean": True},
    )
    monkeypatch.setattr(
        flow,
        "_storage_and_set",
        lambda *_args: (
            {
                "storage": {
                    "state": "present",
                    "volume_uuid": value["kingston"]["volume_uuid"],
                    "root_id": value["kingston"]["root_id"],
                    "device": storage_device,
                    "available_bytes": 100,
                    "required_free_bytes": 1,
                },
                "recovery_set": {"state": "absent", "set_id": value["recovery_set"]["set_id"]},
            },
            ["set_missing"],
        ),
    )
    monkeypatch.setattr(
        flow,
        "_revalidate_storage_binding",
        lambda *_args: (storage_root, storage_device),
    )
    monkeypatch.setattr(
        flow.host_recovery,
        "status",
        lambda **_kwargs: {
            "services": {"launchd": {"state": "absent", "loaded_count": 0}},
            "processes": {"workspace": {"state": "absent", "count": 0}},
        },
    )

    result = flow.inspect_restore(
        handoff_path=handoff_path,
        expected_handoff_sha256=digest,
        repository_root=repo,
        storage_config=tmp_path / "storage.json",
        target_parent=target,
        run_parent=run_parent,
    )

    assert result["status"] == "blocked"
    assert "entry_card_probe_failed:FileNotFoundError" in result["blockers"]
    assert "kingston_entry_card_probe_failed:FileNotFoundError" in result["blockers"]
