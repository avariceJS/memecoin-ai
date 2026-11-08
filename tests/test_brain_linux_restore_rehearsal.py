from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from memecoin_operator import control
from memecoin_research_storage import StorageError
from tools import brain_linux_restore_rehearsal as rehearsal


def _idle_database(path: Path) -> bytes:
    db = sqlite3.connect(path)
    try:
        control._schema(db)
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
        control._push(db, state, "initialized", 1)
        db.commit()
    finally:
        db.close()
    return path.read_bytes()


def _bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    monkeypatch.setattr(rehearsal, "_current_source_commit", lambda: "a" * 40)
    database = _idle_database(tmp_path / "db.sqlite")
    config = rehearsal._json_bytes(rehearsal.EXPECTED_CONFIG)
    manifest = {
        "schema": rehearsal.SCHEMA,
        "source_release_id": "b" * 64,
        "source_commit": "a" * 40,
        "sqlite_schema_sha256": rehearsal._expected_schema_fingerprint(),
        "files": {
            "config.json": {
                "bytes": len(config),
                "mode": "0600",
                "sha256": hashlib.sha256(config).hexdigest(),
            },
            "operator.sqlite": {
                "bytes": len(database),
                "mode": "0600",
                "sha256": hashlib.sha256(database).hexdigest(),
            },
        },
    }
    manifest_bytes = rehearsal._json_bytes(manifest)
    bundle = tmp_path / "bundle"
    rehearsal._create_private_bundle(
        bundle,
        {
            "config.json": config,
            "operator.sqlite": database,
            "manifest.json": manifest_bytes,
        },
    )
    return bundle, hashlib.sha256(manifest_bytes).hexdigest()


def test_read_bundle_requires_external_pin_and_valid_idle_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, pin = _bundle(tmp_path, monkeypatch)
    payloads = rehearsal._read_bundle(bundle, pin, "b" * 64)
    assert set(payloads) == {"config.json", "operator.sqlite", "manifest.json"}
    with pytest.raises(StorageError, match="restore_manifest_pin_mismatch"):
        rehearsal._read_bundle(bundle, "c" * 64, "b" * 64)


def test_read_bundle_rejects_sqlite_byte_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, pin = _bundle(tmp_path, monkeypatch)
    with (bundle / "operator.sqlite").open("ab") as handle:
        handle.write(b"drift")
    with pytest.raises(StorageError, match="restore_manifest_member_mismatch"):
        rehearsal._read_bundle(bundle, pin, "b" * 64)


def test_idle_database_rejects_nonidle_or_resumable_state(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"
    payload = _idle_database(database)
    fingerprint = rehearsal._expected_schema_fingerprint()
    rehearsal._verify_idle_database(payload, expected_schema=fingerprint)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE revisions SET mode='paper', phase='running'")
    with pytest.raises(StorageError, match="restore_state_not_inert_idle"):
        rehearsal._verify_idle_database(database.read_bytes(), expected_schema=fingerprint)


def test_idle_database_rejects_jobs_and_bad_sqlite(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"
    _idle_database(database)
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO jobs(job_id,action,params_json,status,origin,created_ns) "
            "VALUES('synthetic-job','snapshot','{}','queued','manual',2)"
        )
    with pytest.raises(StorageError, match="restore_state_has_activity"):
        rehearsal._verify_idle_database(
            database.read_bytes(), expected_schema=rehearsal._expected_schema_fingerprint()
        )
    with pytest.raises(StorageError, match="restore_sqlite_read_failed"):
        rehearsal._verify_idle_database(
            b"not a sqlite database", expected_schema=rehearsal._expected_schema_fingerprint()
        )


def test_idle_database_rejects_pending_stop_metadata_and_revision_drift(tmp_path: Path) -> None:
    database = tmp_path / "db.sqlite"
    _idle_database(database)
    fingerprint = rehearsal._expected_schema_fingerprint()
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO meta(key, value) VALUES('pending_stop', 'full')")
    with pytest.raises(StorageError, match="restore_state_has_pending_metadata"):
        rehearsal._verify_idle_database(database.read_bytes(), expected_schema=fingerprint)

    database.unlink()
    _idle_database(database)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE revisions SET collect_profile='aggressive'")
    with pytest.raises(StorageError, match="restore_state_not_inert_idle"):
        rehearsal._verify_idle_database(database.read_bytes(), expected_schema=fingerprint)


@pytest.mark.parametrize("run_id", ["", "../escape", "A", "has/slash", "has\\slash"])
def test_target_names_reject_unsafe_run_ids(run_id: str) -> None:
    with pytest.raises(StorageError, match="restore_run_id_invalid"):
        rehearsal._target_names("quarantine", run_id)


def test_bundle_rejects_extra_files_and_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, pin = _bundle(tmp_path, monkeypatch)
    (bundle / "extra").write_text("synthetic", encoding="utf-8")
    with pytest.raises(StorageError, match="restore_bundle_member_set_mismatch"):
        rehearsal._read_bundle(bundle, pin, "b" * 64)
    (bundle / "extra").unlink()
    (bundle / "config.json").unlink()
    (bundle / "config.json").symlink_to("operator.sqlite")
    with pytest.raises(StorageError, match="restore_bundle_member_unsafe"):
        rehearsal._read_bundle(bundle, pin, "b" * 64)


def test_private_bundle_files_are_owner_only(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    rehearsal._create_private_bundle(output, {"manifest.json": b"{}\n"})
    assert os.stat(output).st_mode & 0o777 == 0o700
    assert os.stat(output / "manifest.json").st_mode & 0o777 == 0o600
