import hashlib
import json
import plistlib
import sqlite3
import sys
from pathlib import Path

import pytest

from engineering.service import (
    LABEL,
    backup_snapshot,
    launch_agent_plist,
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
    state.mkdir(mode=0o700)
    controller = state / "controller"
    code = controller / "engineering/__main__.py"
    trusted = state / "trusted/pilot_acceptance.py"
    _write(code, b"raise SystemExit(0)\n")
    _write(trusted, b"def accept(): return True\n")
    cfg = {
        "schema": "brain-engineering-deployment-v1",
        "python": sys.executable,
        "controller": str(controller),
        "remote_publication_enabled": False,
    }
    _json(state / "config.json", cfg)
    _write(
        state / "config.sha256",
        hashlib.sha256((state / "config.json").read_bytes()).hexdigest().encode(),
    )
    manifest = {
        "engineering/__main__.py": hashlib.sha256(code.read_bytes()).hexdigest(),
        "trusted/pilot_acceptance.py": hashlib.sha256(trusted.read_bytes()).hexdigest(),
    }
    _json(state / "controller-manifest.json", manifest)
    for name in ("confinement.json", "native-profile.json"):
        _json(state / name, {"qualified": True})
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
    receipt_body = {"digest": "trusted-record"}
    receipt = state / "receipts" / (hashlib.sha256(_canonical(receipt_body)).hexdigest() + ".json")
    _write(receipt, _canonical(receipt_body))
    destination = tmp_path / "backup"
    result = backup_snapshot(state, destination)
    assert result == destination
    assert (destination / "config.json").read_bytes() == (state / "config.json").read_bytes()
    assert (destination / "receipts" / receipt.name).read_bytes() == receipt.read_bytes()
    snapshot = json.loads((destination / "snapshot.json").read_text())
    assert snapshot["schema"] == "brain-organization-snapshot-v1"
    with sqlite3.connect(destination / "organization.sqlite") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT value FROM evidence").fetchone()[0] == "synthetic"
    assert destination.stat().st_mode & 0o077 == 0
    assert (destination / "organization.sqlite").stat().st_mode & 0o222 == 0
    with pytest.raises(ValueError, match="snapshot_destination"):
        backup_snapshot(state, destination)


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
