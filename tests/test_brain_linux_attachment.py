"""Synthetic attachment runtime receipts accept only pinned safe local state."""

import json
import os
import sqlite3
import sys

import pytest

from memecoin_operator import control
from memecoin_operator.linux_attachment import (
    EXPECTED_CONFIG,
    RUNTIME_DIRECTORIES,
    _prepare_runtime_directories,
    _publish_attachment_directory,
    _rename_noreplace,
    _runtime_inventory,
    _schema_fingerprint,
    _verify_idle_database,
)
from memecoin_research_storage import StorageError


def _runtime_tree(root):
    (root / "config.json").write_bytes(b"{}\n")
    (root / "config.json").chmod(0o600)
    (root / "operator.sqlite").write_bytes(b"sqlite-fixture")
    (root / "operator.sqlite").chmod(0o600)
    for directory in (root / "background", root / "operator"):
        directory.mkdir(mode=0o700)


def test_runtime_inventory_hashes_a_stopped_attachment_tree(tmp_path):
    _runtime_tree(tmp_path)
    inventory = _runtime_inventory(tmp_path, os.getuid(), os.getgid())
    assert [entry["path"] for entry in inventory] == [
        "background",
        "config.json",
        "operator",
        "operator.sqlite",
    ]
    assert next(entry for entry in inventory if entry["path"] == "config.json")["mode"] == "0600"


def test_runtime_inventory_rejects_sqlite_sidecars(tmp_path):
    _runtime_tree(tmp_path)
    (tmp_path / "operator.sqlite-wal").write_bytes(b"unqualified")
    (tmp_path / "operator.sqlite-wal").chmod(0o600)
    with pytest.raises(StorageError, match="attachment_runtime_root_members_mismatch"):
        _runtime_inventory(tmp_path, os.getuid(), os.getgid())


def test_initial_runtime_directory_layout_is_complete_and_private(tmp_path):
    root = tmp_path / "operator"
    root.mkdir(mode=0o700)
    _prepare_runtime_directories(root, os.getuid(), os.getgid())
    for relative in (
        "config.json",
        "operator.sqlite",
        "operator/owner.lock",
        "operator/logs/operator.log",
    ):
        path = root / relative
        path.write_bytes(b"synthetic")
        path.chmod(0o600)
    inventory = _runtime_inventory(root, os.getuid(), os.getgid())
    assert {item["path"] for item in inventory if item["kind"] == "directory"} == (
        RUNTIME_DIRECTORIES
    )


@pytest.mark.skipif(sys.platform != "linux", reason="renameat2 is Linux-specific")
def test_attachment_publication_never_replaces_existing_directory(tmp_path):
    source = tmp_path / ".pending"
    destination = tmp_path / "attachment"
    source.mkdir()
    destination.mkdir()
    (destination / "preserve").write_text("existing")
    with pytest.raises(StorageError, match="attachment_id_already_exists"):
        _rename_noreplace(tmp_path, source.name, destination.name)
    assert source.is_dir()
    assert (destination / "preserve").read_text() == "existing"


@pytest.mark.skipif(sys.platform != "linux", reason="renameat2 is Linux-specific")
def test_attachment_collision_removes_only_its_staging_directory(tmp_path):
    source = tmp_path / ".pending"
    destination = tmp_path / "attachment"
    source.mkdir()
    (source / "payload").write_text("temporary")
    destination.mkdir()
    (destination / "preserve").write_text("existing")
    with pytest.raises(StorageError, match="attachment_id_already_exists"):
        _publish_attachment_directory(source, destination)
    assert not source.exists()
    assert (destination / "preserve").read_text() == "existing"


def test_post_start_verifier_accepts_only_the_expected_idle_maintenance_event(tmp_path):
    path = tmp_path / "operator.sqlite"
    database = sqlite3.connect(path)
    try:
        control._schema(database)
        state = {
            "mode": "idle",
            "phase": "idle",
            "collect_profile": "balanced",
            "selected_candidate_id": None,
            "active_exam_id": None,
            "resume_mode": None,
            "auto_reason": None,
            "cycle": control._cycle_default(),
        }
        control._push(database, state, "initialized", 1)
        database.execute("INSERT INTO meta(key,value) VALUES('last_maintain_ns','2')")
        database.execute(
            "INSERT INTO events(kind,reason,payload_json,created_ns) "
            "VALUES('incident','adapter_error','{}',3)"
        )
        database.commit()
    finally:
        database.close()
    payload = path.read_bytes()
    expected_schema = _schema_fingerprint()
    with pytest.raises(StorageError, match="attachment_state_has_pending_metadata"):
        _verify_idle_database(payload, expected_schema)
    _verify_idle_database(payload, expected_schema, allow_startup_maintenance=True)


def test_linux_synthetic_idle_maintenance_is_a_bounded_noop(tmp_path, monkeypatch):
    root = tmp_path / "state"
    root.mkdir()
    (root / "config.json").write_text(json.dumps(EXPECTED_CONFIG))
    monkeypatch.setattr(control.sys, "platform", "linux")

    def unexpected_adapter():
        raise AssertionError("Linux synthetic idle must not load the Mac-oriented adapter")

    monkeypatch.setattr(control, "load_adapter", unexpected_adapter)
    assert control._maintain(root, {"mode": "idle", "phase": "idle"}) == {
        "ok": True,
        "reason": "synthetic_idle_noop",
    }
    assert control._maintain(root, {"mode": "collect", "phase": "collect"}) == {
        "ok": False,
        "reason": "linux_synthetic_idle_only",
    }


def test_linux_synthetic_idle_rejects_mutating_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(control.sys, "platform", "linux")
    root = tmp_path / "state"
    root.mkdir()
    (root / "config.json").write_text(json.dumps(EXPECTED_CONFIG))
    control.initialize(root)
    result = control.command(root, "synthetic-collect", "collect", {"profile": "balanced"})
    assert result["ok"] is False
    assert result["reason"] == "linux_synthetic_idle_only"


def test_linux_tick_and_status_never_load_adapter_after_config_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(control.sys, "platform", "linux")
    root = tmp_path / "state"
    root.mkdir()
    config_path = root / "config.json"
    config_path.write_text(json.dumps(EXPECTED_CONFIG))
    control.initialize(root)

    def unexpected_adapter():
        raise AssertionError("Linux operator path must not load the generic adapter")

    monkeypatch.setattr(control, "load_adapter", unexpected_adapter)
    assert control._load_view(root)["warning"] == "linux_synthetic_idle_only"
    assert control.tick(root)["mode"] == "idle"
    assert control.status(root)["mode"] == "idle"

    config_path.write_text(
        json.dumps(
            {key: value for key, value in EXPECTED_CONFIG.items() if key != "maintenance_policy"}
        )
    )
    assert control._load_view(root)["warning"] == "linux_unsupported_mode"
    assert control.tick(root)["mode"] == "idle"
    rejected = control.command(root, "drifted-collect", "collect", {"profile": "balanced"})
    assert rejected["reason"] == "linux_unsupported_mode"
    assert (
        control.command(root, "synthetic-collect", "collect", {"profile": "balanced"})["reason"]
        == "linux_unsupported_mode"
    )


def test_linux_policy_string_does_not_change_macos_adapter_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(control.sys, "platform", "darwin")
    root = tmp_path / "state"
    root.mkdir()
    (root / "config.json").write_text(json.dumps(EXPECTED_CONFIG))
    control.initialize(root)
    assert control._load_view(root)["warning"] == "linux_policy_platform_mismatch"
    assert control._maintain(root, {"mode": "idle", "phase": "idle"}) == {
        "ok": False,
        "reason": "linux_policy_platform_mismatch",
    }


def test_linux_tick_does_not_advance_a_nonidle_database(tmp_path, monkeypatch):
    monkeypatch.setattr(control.sys, "platform", "linux")
    root = tmp_path / "state"
    root.mkdir()
    (root / "config.json").write_text(json.dumps(EXPECTED_CONFIG))
    control.initialize(root)
    db = control._connect(root)
    try:
        db.execute("UPDATE revisions SET mode='collect',phase='collect' WHERE revision=1")
        db.commit()
    finally:
        db.close()

    def unexpected_adapter():
        raise AssertionError("unsupported Linux state must not load the generic adapter")

    monkeypatch.setattr(control, "load_adapter", unexpected_adapter)
    assert control.tick(root)["mode"] == "collect"


def test_linux_synthetic_stop_is_local_and_does_not_load_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(control.sys, "platform", "linux")
    root = tmp_path / "state"
    root.mkdir()
    (root / "config.json").write_text(json.dumps(EXPECTED_CONFIG))
    control.initialize(root)

    def unexpected_adapter():
        raise AssertionError("Linux synthetic stop must not load the generic adapter")

    monkeypatch.setattr(control, "load_adapter", unexpected_adapter)
    result = control.command(root, "idle-stop", "stop", {"confirm": True})
    assert result["ok"] is True
    assert result["mode"] == "stopped"
