import fcntl
import hashlib
import json
import os
import plistlib
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from engineering.service import (
    LABEL,
    backup_snapshot,
    launch_agent_plist,
    restore_snapshot,
    validate_deployment,
)
import engineering.service as service_module
from engineering.org_state import Store
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
    db = Store(state / "organization.sqlite")
    db.add_task("synthetic", "team", "Synthetic checkpoint task", scope=["src/example.py"])
    db.close()
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
    assert result == destination.resolve()
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
        assert db.execute("SELECT id FROM tasks").fetchone()[0] == "synthetic"
    assert destination.stat().st_mode & 0o077 == 0
    assert (destination / "organization.sqlite").stat().st_mode & 0o222 == 0
    with pytest.raises(ValueError, match="snapshot_destination"):
        backup_snapshot(state, destination)

    moved_source = tmp_path / "state-original-moved"
    state.rename(moved_source)
    restored_path = tmp_path / "restored"
    rename = os.rename

    def verify_barrier_before_publication(source, target):
        cfg = json.loads((Path(source) / "config.json").read_text())
        assert cfg["restore_requires_host_requalification"] is True
        rename(source, target)

    with patch.object(service_module.os, "rename", verify_barrier_before_publication):
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
    assert restored_cfg["codex_executable"] == str((restored / "synthetic-codex").resolve())
    assert Path(restored_cfg["codex_executable"]).is_file()
    with pytest.raises(ValueError, match="deployment_requalification_required"):
        validate_deployment(restored)
    with sqlite3.connect(restored / "usage-ledger.sqlite") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT identity FROM registrations").fetchone()[0] == "synthetic"
    with sqlite3.connect(restored / "organization.sqlite") as db:
        assert db.execute("SELECT id FROM tasks").fetchone()[0] == "synthetic"
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
    db = Store(state / "organization.sqlite")
    db.add_task("running", "team", "Running synthetic task", scope=["src/example.py"])
    db.claim("running", "worker", 60)
    db.close()
    with pytest.raises(ValueError, match="snapshot_live_attempts"):
        backup_snapshot(state, tmp_path / "active-attempt")


def test_snapshot_rejects_incomplete_organization_schema(tmp_path):
    state = deployment(tmp_path)
    _prepare_complete_checkpoint(state)
    (state / "organization.sqlite").unlink()
    with sqlite3.connect(state / "organization.sqlite") as db:
        db.execute("CREATE TABLE evidence (value TEXT)")
    with pytest.raises(ValueError, match="snapshot_organization_schema"):
        backup_snapshot(state, tmp_path / "incomplete")


def test_snapshot_rejects_organization_tables_without_constraints(tmp_path):
    state = deployment(tmp_path)
    _prepare_complete_checkpoint(state)
    with sqlite3.connect(state / "organization.sqlite") as db:
        db.execute("PRAGMA foreign_keys=OFF")
        tables = [
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for table in tables:
            copy = "copy_" + table
            db.execute(f"CREATE TABLE {copy} AS SELECT * FROM {table}")
        for table in tables:
            db.execute(f"DROP TABLE {table}")
        for table in tables:
            db.execute(f"ALTER TABLE copy_{table} RENAME TO {table}")
    with pytest.raises(ValueError, match="snapshot_organization_schema"):
        backup_snapshot(state, tmp_path / "missing-constraints")


def test_snapshot_rejects_incomplete_integration_git_objects(tmp_path):
    state = deployment(tmp_path)
    tip = _prepare_complete_checkpoint(state)
    object_path = state / "integration/.git/objects" / tip[:2] / tip[2:]
    object_path.unlink()
    with pytest.raises(ValueError, match="snapshot_integration_repository_invalid"):
        backup_snapshot(state, tmp_path / "incomplete-git")


def test_snapshot_rejects_promisor_remote_without_running_helper(tmp_path):
    state = deployment(tmp_path)
    tip = _prepare_complete_checkpoint(state)
    git = state / "integration/.git"
    object_path = git / "objects" / tip[:2] / tip[2:]
    object_path.unlink()
    subprocess.run(
        ["git", "-C", str(state / "integration"), "config", "extensions.partialClone", "origin"],
        check=True,
    )
    subprocess.run(
        [
            "git", "-C", str(state / "integration"), "config",
            "remote.origin.url", "auditprobe::repo",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(state / "integration"), "config", "remote.origin.promisor", "yes"],
        check=True,
    )
    helper_dir = tmp_path / "helpers"
    helper_dir.mkdir()
    marker = tmp_path / "helper-ran"
    helper = helper_dir / "git-remote-auditprobe"
    helper.write_text(f"#!/bin/sh\nprintf called > '{marker}'\nexit 1\n")
    helper.chmod(0o700)
    path = str(helper_dir) + ":" + os.environ.get("PATH", "/usr/bin:/bin")
    with patch.dict(os.environ, {"PATH": path}):
        with pytest.raises(ValueError, match="snapshot_integration_repository_external_objects"):
            backup_snapshot(state, tmp_path / "promisor")
    assert not marker.exists()


def test_snapshot_resolves_destination_parent_before_containment_check(tmp_path):
    state = deployment(tmp_path)
    _prepare_complete_checkpoint(state)
    alias = tmp_path / "state-alias"
    alias.symlink_to(state, target_is_directory=True)
    with pytest.raises(ValueError, match="snapshot_destination"):
        backup_snapshot(state, alias / "inside-snapshot")
    assert not (state / "inside-snapshot").exists()


def test_restore_does_not_delete_unowned_destination_on_failure(tmp_path):
    state = deployment(tmp_path)
    _prepare_complete_checkpoint(state)
    snapshot = backup_snapshot(state, tmp_path / "backup")
    destination = tmp_path / "restored"

    def create_unowned_destination_then_fail(path, **kwargs):
        destination.mkdir()
        (destination / "owner.txt").write_text("created by another operation")
        raise ValueError("injected_restore_validation_failure")

    with patch.object(service_module, "validate_deployment", create_unowned_destination_then_fail):
        with pytest.raises(ValueError, match="injected_restore_validation_failure"):
            restore_snapshot(snapshot, destination)
    assert (destination / "owner.txt").read_text() == "created by another operation"


def test_restore_without_original_host_executable_stays_unqualified(tmp_path):
    state = deployment(tmp_path)
    _prepare_complete_checkpoint(state)
    external_executable = tmp_path / "host-codex"
    external_executable.write_bytes((state / "synthetic-codex").read_bytes())
    external_executable.chmod(0o700)
    external_python = tmp_path / "host-python"
    external_python.write_text("#!/bin/sh\nexit 0\n")
    external_python.chmod(0o700)
    cfg_path = state / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["codex_executable"] = str(external_executable.resolve())
    cfg["python"] = str(external_python.resolve())
    cfg_bytes = json.dumps(cfg, sort_keys=True, indent=2).encode()
    cfg_path.write_bytes(cfg_bytes)
    (state / "config.sha256").write_text(hashlib.sha256(cfg_bytes).hexdigest())
    snapshot = backup_snapshot(state, tmp_path / "backup")
    external_executable.unlink()
    external_python.unlink()

    restored = restore_snapshot(snapshot, tmp_path / "restored")
    receipt = json.loads((restored / "restore-receipt.json").read_text())
    restored_cfg = json.loads((restored / "config.json").read_text())
    assert receipt["runtime_ready"] is False
    assert restored_cfg["restore_requires_host_requalification"] is True
    assert restored_cfg["codex_executable"] == str(external_executable.resolve())
    assert restored_cfg["python"] == str(external_python.resolve())


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
