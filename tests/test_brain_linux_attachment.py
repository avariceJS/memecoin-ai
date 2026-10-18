"""Synthetic attachment runtime receipts accept only pinned safe local state."""

import os
import sqlite3

import pytest

from memecoin_operator import control
from memecoin_operator.linux_attachment import (
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
