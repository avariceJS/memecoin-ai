import fcntl
import hashlib
import json
import plistlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from engineering.service import (
    LABEL,
    backup_snapshot,
    launch_agent_plist,
    restore_snapshot,
    validate_deployment,
)
from engineering.usage_ledger import UsageLedger


def _write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _json(path: Path, value):
    _write(path, json.dumps(value, sort_keys=True).encode())


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _prepare_complete_checkpoint(state: Path):
    (state / "STOP").touch()
    for name in ("intents", "attempts", "verification", "integration"):
        (state / name).mkdir(exist_ok=True)
    attempt = state / "attempts/7/worker.json"
    _write(attempt, b'{"synthetic":true}')
    _write(state / "intents/EXP-001.json", b'{"status":"pending"}')
    _write(state / "operations.jsonl", b'{"kind":"synthetic"}\n')
    ledger = UsageLedger(state / "usage-ledger.sqlite")
    ledger.register("synthetic", "epoch-1", "gpt-6-luna", None, evidence="synthetic")
    ledger.close()
    integration = state / "integration"
    subprocess.run(["git", "init", "-b", "research/active", str(integration)], check=True)
    subprocess.run(["git", "-C", str(integration), "config", "user.name", "Synthetic"], check=True)
    subprocess.run(
        ["git", "-C", str(integration), "config", "user.email", "synthetic@example.invalid"],
        check=True,
    )
    _write(integration / "tracked.txt", b"checkpointed ref\n")
    subprocess.run(["git", "-C", str(integration), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(integration), "commit", "-m", "checkpoint"], check=True)
    return subprocess.run(
        ["git", "-C", str(integration), "rev-parse", "refs/heads/research/active"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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
        "controller": str(controller.resolve()),
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
        db.execute("CREATE TABLE evidence (value TEXT)")
        db.execute("INSERT INTO evidence VALUES ('synthetic')")
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


def test_snapshot_uses_sqlite_backup_and_copies_policy_and_receipts(tmp_path):
    state = deployment(tmp_path)
    integration_tip = _prepare_complete_checkpoint(state)
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
    assert snapshot["stop_preserved"] is True
    assert "usage-ledger.sqlite" in snapshot["files"]
    assert "integration/.git/HEAD" in snapshot["files"]
    assert "attempts/7/worker.json" in snapshot["files"]
    assert "intents/EXP-001.json" in snapshot["files"]
    with sqlite3.connect(destination / "organization.sqlite") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT value FROM evidence").fetchone()[0] == "synthetic"
    assert destination.stat().st_mode & 0o077 == 0
    assert (destination / "organization.sqlite").stat().st_mode & 0o222 == 0
    with pytest.raises(ValueError, match="snapshot_destination"):
        backup_snapshot(state, destination)

    restored_path = tmp_path / "restored"
    restored = restore_snapshot(destination, restored_path)
    assert (restored / "STOP").is_file()
    assert (restored / "attempts/7/worker.json").read_bytes() == b'{"synthetic":true}'
    assert (restored / "intents/EXP-001.json").read_bytes() == b'{"status":"pending"}'
    assert (restored / "operations.jsonl").read_bytes() == b'{"kind":"synthetic"}\n'
    receipt = json.loads((restored / "restore-receipt.json").read_text())
    assert receipt["stop_preserved"] is True
    assert receipt["runtime_ready"] is False
    assert receipt["host_requalification_required"] is True
    restored_cfg = json.loads((restored / "config.json").read_text())
    assert restored_cfg["controller"] == str((restored / "controller").resolve())
    with pytest.raises(ValueError, match="deployment_requalification_required"):
        validate_deployment(restored)
    with sqlite3.connect(restored / "usage-ledger.sqlite") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT identity FROM registrations").fetchone()[0] == "synthetic"
    restored_tip = subprocess.run(
        ["git", "-C", str(restored / "integration"), "rev-parse", "refs/heads/research/active"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert restored_tip == integration_tip


def test_restore_rejects_modified_checkpoint_file(tmp_path):
    state = deployment(tmp_path)
    _prepare_complete_checkpoint(state)
    snapshot = backup_snapshot(state, tmp_path / "backup")
    (snapshot / "attempts/7/worker.json").write_text("changed")
    with pytest.raises(ValueError, match="snapshot_inventory_mismatch"):
        restore_snapshot(snapshot, tmp_path / "restored")


def test_snapshot_requires_stop_and_an_inactive_supervisor(tmp_path):
    state = deployment(tmp_path)
    _prepare_complete_checkpoint(state)
    (state / "STOP").unlink()
    with pytest.raises(ValueError, match="snapshot_requires_STOP"):
        backup_snapshot(state, tmp_path / "no-stop")

    (state / "STOP").touch()
    lock_path = state / "supervisor.lock"
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(ValueError, match="snapshot_supervisor_active"):
                backup_snapshot(state, tmp_path / "active")
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def test_snapshot_rejects_running_attempts(tmp_path):
    state = deployment(tmp_path)
    _prepare_complete_checkpoint(state)
    with sqlite3.connect(state / "organization.sqlite") as db:
        db.execute("CREATE TABLE attempts(state TEXT)")
        db.execute("INSERT INTO attempts VALUES ('running')")
    with pytest.raises(ValueError, match="snapshot_live_attempts"):
        backup_snapshot(state, tmp_path / "active-attempt")


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
