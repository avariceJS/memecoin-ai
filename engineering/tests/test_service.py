import errno
import hashlib
import json
import plistlib
import sqlite3
import sys
from pathlib import Path

import pytest

import engineering.service as service
from engineering.service import (
    LABEL,
    backup_snapshot,
    launch_agent_plist,
    restore_snapshot,
    validate_deployment,
)


def _write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _json(path: Path, value):
    _write(path, json.dumps(value, sort_keys=True).encode())


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def deployment(tmp_path):
    state = tmp_path / "state"
    state.mkdir(mode=0o700, parents=True)
    controller = state / "controller"
    code = controller / "engineering/__main__.py"
    trusted = state / "trusted/pilot_acceptance.py"
    _write(code, b"raise SystemExit(0)\n")
    adapter = controller / "engineering/codex_rpc.py"
    _write(adapter, b"def rpc(): return None\n")
    executable = state / "synthetic-codex"
    _write(executable, b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    _write(trusted, b"def accept(): return True\n")
    cfg = {
        "schema": "brain-engineering-deployment-v1",
        "python": sys.executable,
        "controller": str(controller),
        "remote_publication_enabled": False,
        "codex_executable": str(executable),
        "codex_executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
    }
    _json(state / "config.json", cfg)
    _write(
        state / "config.sha256",
        hashlib.sha256((state / "config.json").read_bytes()).hexdigest().encode(),
    )
    manifest = {
        "engineering/__main__.py": hashlib.sha256(code.read_bytes()).hexdigest(),
        "engineering/codex_rpc.py": hashlib.sha256(adapter.read_bytes()).hexdigest(),
        "trusted/pilot_acceptance.py": hashlib.sha256(trusted.read_bytes()).hexdigest(),
    }
    _json(state / "controller-manifest.json", manifest)
    _json(state / "confinement.json", {"qualified": True})
    _json(
        state / "native-profile.json",
        {
            "qualified": True,
            "adapter_sha256": hashlib.sha256(adapter.read_bytes()).hexdigest(),
            "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        },
    )
    with sqlite3.connect(state / "organization.sqlite") as db:
        db.execute("CREATE TABLE tasks (id TEXT, state TEXT)")
        db.execute("INSERT INTO tasks VALUES ('EXP-001', 'submitted')")
        db.execute("CREATE TABLE evidence (value TEXT)")
        db.execute("INSERT INTO evidence VALUES ('synthetic')")
    _write(state / "STOP", b"")
    _write(state / "supervisor.lock", b"")
    return state


def test_plist_is_fixed_bounded_and_does_not_install(tmp_path):
    state = deployment(tmp_path)
    plist = launch_agent_plist(state, max_seconds=14400)
    assert plist["Label"] == LABEL
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    args = plist["ProgramArguments"]
    assert args[-2:] == ["--max-seconds", "14400"]
    assert "market" not in " ".join(args).lower()
    assert not (Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist").exists()
    with pytest.raises(ValueError, match="episode_bound"):
        launch_agent_plist(state, max_seconds=14401)


@pytest.mark.parametrize(
    "mutation,code",
    [
        (
            lambda s: (s / "controller/engineering/__main__.py").write_text("# altered"),
            "manifest_hash",
        ),
        (
            lambda s: _json(s / "native-profile.json", {"qualified": False}),
            "deployment_not_qualified",
        ),
        (
            lambda s: _json_update(s / "config.json", "remote_publication_enabled", True),
            "deployment_policy_hash",
        ),
    ],
)
def test_deployment_evidence_fails_closed(tmp_path, mutation, code):
    state = deployment(tmp_path)
    mutation(state)
    # If policy contents change, refresh its pinned digest so the specific unsafe
    # policy is what the verifier rejects.
    if code == "deployment_policy_hash":
        (state / "config.sha256").write_text(
            hashlib.sha256((state / "config.json").read_bytes()).hexdigest()
        )
        code = "remote_publication_must_remain_disabled"
    with pytest.raises(ValueError, match=code):
        validate_deployment(state)


def _json_update(path, key, value):
    data = json.loads(path.read_text())
    data[key] = value
    _json(path, data)


def test_snapshot_round_trip_uses_sqlite_backup_and_copies_durable_state(tmp_path):
    state = deployment(tmp_path)
    receipt_body = {"digest": "trusted-record"}
    receipt = state / "receipts" / (hashlib.sha256(_canonical(receipt_body)).hexdigest() + ".json")
    _write(receipt, _canonical(receipt_body))
    destination = tmp_path / "backup"
    result = backup_snapshot(state, destination)
    assert result == destination
    assert (destination / "config.json").read_bytes() == (state / "config.json").read_bytes()
    assert (destination / "receipts" / receipt.name).read_bytes() == receipt.read_bytes()
    snapshot = json.loads((destination / "snapshot.json").read_text())
    assert snapshot["schema"] == "brain-organization-snapshot-v2"
    assert "receipts" in snapshot["entries"]
    with sqlite3.connect(destination / "organization.sqlite") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT value FROM evidence").fetchone()[0] == "synthetic"
        assert db.execute("SELECT state FROM tasks").fetchone()[0] == "submitted"
    assert destination.stat().st_mode & 0o077 == 0
    assert (destination / "organization.sqlite").stat().st_mode & 0o077 == 0
    with pytest.raises(ValueError, match="snapshot_destination"):
        backup_snapshot(state, destination)
    digest = hashlib.sha256((destination / "snapshot.json").read_bytes()).hexdigest()
    state.rename(tmp_path / "detached-state")
    restored = restore_snapshot(destination, state, expected_manifest_sha256=digest)
    assert restored == state
    assert (restored / "supervisor.lock").read_bytes() == b""


def test_backup_does_not_replace_raced_in_empty_destination(tmp_path, monkeypatch):
    state = deployment(tmp_path)
    destination = tmp_path / "raced-backup"
    publish = service._publish_directory_exclusive

    def race(source, target):
        target.mkdir()
        publish(source, target)

    monkeypatch.setattr(service, "_publish_directory_exclusive", race)
    with pytest.raises(ValueError, match="snapshot_destination"):
        backup_snapshot(state, destination)
    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert list(tmp_path.glob(".raced-backup.*")) == []


def test_restore_does_not_replace_raced_in_empty_destination(tmp_path, monkeypatch):
    state = deployment(tmp_path)
    snapshot = backup_snapshot(state, tmp_path / "backup")
    digest = hashlib.sha256((snapshot / "snapshot.json").read_bytes()).hexdigest()
    state.rename(tmp_path / "detached-state")
    publish = service._publish_directory_exclusive

    def race(source, target):
        target.mkdir()
        publish(source, target)

    monkeypatch.setattr(service, "_publish_directory_exclusive", race)
    with pytest.raises(ValueError, match="snapshot_destination"):
        restore_snapshot(snapshot, state, expected_manifest_sha256=digest)
    assert state.is_dir()
    assert list(state.iterdir()) == []
    assert list(tmp_path.glob(".state.restore.*")) == []


def test_snapshot_closes_lock_descriptor_when_flock_fails(tmp_path, monkeypatch):
    state = deployment(tmp_path)
    opened = []
    real_open = service.os.open

    def track_open(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if Path(path) == state / "supervisor.lock":
            opened.append(fd)
        return fd

    def contention(*_args):
        raise BlockingIOError(errno.EWOULDBLOCK, "synthetic lock contention")

    monkeypatch.setattr(service.os, "open", track_open)
    monkeypatch.setattr(service.fcntl, "flock", contention)
    with pytest.raises(ValueError, match="snapshot_busy"):
        backup_snapshot(state, tmp_path / "locked-backup")
    assert len(opened) == 1
    with pytest.raises(OSError) as error:
        service.os.fstat(opened[0])
    assert error.value.errno == errno.EBADF


def test_plist_round_trip_has_no_extra_launch_services(tmp_path):
    plist = launch_agent_plist(deployment(tmp_path))
    decoded = plistlib.loads(plistlib.dumps(plist))
    assert decoded == plist
    assert decoded["ProgramArguments"][3:5] == ["supervise", "--state"]


def test_manifest_cannot_follow_a_symlinked_parent(tmp_path):
    state = deployment(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    content = external / "module.py"
    content.write_text("# outside controller")
    (state / "controller/vendor").symlink_to(external, target_is_directory=True)
    manifest = json.loads((state / "controller-manifest.json").read_text())
    manifest["vendor/module.py"] = hashlib.sha256(content.read_bytes()).hexdigest()
    _json(state / "controller-manifest.json", manifest)
    with pytest.raises(ValueError, match="manifest_file_permissions"):
        validate_deployment(state)


def test_native_bindings_reject_missing_and_stale_evidence(tmp_path):
    state = deployment(tmp_path)
    native = state / "native-profile.json"
    report = json.loads(native.read_text())
    report.pop("adapter_sha256")
    _json(native, report)
    with pytest.raises(ValueError, match="native_adapter_binding"):
        validate_deployment(state)

    state = deployment(tmp_path / "adapter")
    adapter = state / "controller/engineering/codex_rpc.py"
    adapter.write_bytes(b"def rpc(): return changed\n")
    manifest_path = state / "controller-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["engineering/codex_rpc.py"] = hashlib.sha256(adapter.read_bytes()).hexdigest()
    _json(manifest_path, manifest)
    with pytest.raises(ValueError, match="native_adapter_binding"):
        validate_deployment(state)

    state = deployment(tmp_path / "missing-executable")
    executable = Path(json.loads((state / "config.json").read_text())["codex_executable"])
    executable.unlink()
    with pytest.raises(ValueError, match="codex_executable_binding"):
        validate_deployment(state)

    state = deployment(tmp_path / "executable")
    executable = Path(json.loads((state / "config.json").read_text())["codex_executable"])
    executable.write_bytes(b"#!/bin/sh\nexit 9\n")
    with pytest.raises(ValueError, match="codex_executable_binding"):
        validate_deployment(state)


def test_native_bindings_reject_missing_config_pins_and_report_mismatch(tmp_path):
    state = deployment(tmp_path)
    cfg = json.loads((state / "config.json").read_text())
    cfg.pop("codex_executable_sha256")
    _json(state / "config.json", cfg)
    (state / "config.sha256").write_text(
        hashlib.sha256((state / "config.json").read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError, match="codex_executable_binding"):
        validate_deployment(state)

    state = deployment(tmp_path / "report")
    report = json.loads((state / "native-profile.json").read_text())
    report["executable_sha256"] = "0" * 64
    _json(state / "native-profile.json", report)
    with pytest.raises(ValueError, match="native_executable_binding"):
        validate_deployment(state)
