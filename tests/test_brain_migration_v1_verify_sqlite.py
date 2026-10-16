from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from tools.brain_migration_v1_verify_sqlite import (
    SQLiteVerificationError,
    verify_sqlite,
)


def _sha(path: Path) -> tuple[int, str]:
    body = path.read_bytes()
    return len(body), hashlib.sha256(body).hexdigest()


def _write_manifest(path: Path, members: list[tuple[str, str, str]]) -> str:
    rows = [
        {
            "record_type": "header",
            "schema": "brain-practical-migration-v1",
            "candidate_id": "a" * 32,
            "candidate_status": "complete",
        }
    ]
    for relative, custody_class, kind in members:
        member_path = path.parent / "payload" / "owner_work" / relative
        size, digest = _sha(member_path) if kind == "file" else (0, None)
        rows.append(
            {
                "record_type": "member",
                "alias": "owner_work",
                "path": relative,
                "type": kind,
                "size_bytes": size,
                "sha256": digest,
                "custody_class": custody_class,
            }
        )
    encoded = b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode() for row in rows
    )
    path.write_bytes(encoded)
    return hashlib.sha256(encoded).hexdigest()


def test_integrity_check_is_readonly_and_validates_sqlite_family(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    root = payload / "owner_work"
    root.mkdir(parents=True)
    database = root / "state.sqlite"
    writer = sqlite3.connect(database)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE state (value TEXT NOT NULL)")
    writer.execute("INSERT INTO state VALUES ('ordinary')")
    writer.commit()
    assert Path(str(database) + "-wal").exists()
    assert Path(str(database) + "-shm").exists()

    manifest = tmp_path / "MANIFEST.jsonl"
    sha = _write_manifest(
        manifest,
        [
            ("state.sqlite", "ordinary_opaque", "file"),
            ("state.sqlite-wal", "ordinary_opaque", "file"),
            ("state.sqlite-shm", "ordinary_opaque", "file"),
        ],
    )
    before = [_sha(Path(str(database) + suffix)) for suffix in ("", "-wal", "-shm")]
    result = verify_sqlite(
        payload_root=payload,
        manifest_path=manifest,
        expected_manifest_sha256=sha,
    )
    after = [_sha(Path(str(database) + suffix)) for suffix in ("", "-wal", "-shm")]
    writer.close()
    assert before == after
    assert result["state"] == "ordinary_sqlite_integrity_verified"
    assert result["ordinary_database_count"] == 1
    assert result["protected_database_semantics_checked"] is False
    assert result["restore_payload_mutated"] is False


def test_protected_database_is_not_opened(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    root = payload / "owner_work"
    root.mkdir(parents=True)
    protected = root / "d43-protected.sqlite"
    protected.write_bytes(b"not a database and must stay opaque")
    ordinary = root / "ordinary.sqlite"
    connection = sqlite3.connect(ordinary)
    connection.execute("CREATE TABLE value (x INTEGER)")
    connection.close()
    manifest = tmp_path / "MANIFEST.jsonl"
    sha = _write_manifest(
        manifest,
        [
            ("ordinary.sqlite", "ordinary_opaque", "file"),
            ("d43-protected.sqlite", "protected_opaque", "file"),
        ],
    )

    result = verify_sqlite(
        payload_root=payload,
        manifest_path=manifest,
        expected_manifest_sha256=sha,
    )

    assert result["ordinary_database_count"] == 1
    assert result["protected_database_semantics_checked"] is False


def test_manifest_mismatch_and_symlink_database_fail_closed(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    root = payload / "owner_work"
    root.mkdir(parents=True)
    database = root / "real.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE value (x INTEGER)")
    connection.close()
    manifest = tmp_path / "MANIFEST.jsonl"
    sha = _write_manifest(manifest, [("real.sqlite", "ordinary_opaque", "file")])

    with pytest.raises(SQLiteVerificationError, match="manifest_sha256_mismatch"):
        verify_sqlite(
            payload_root=payload,
            manifest_path=manifest,
            expected_manifest_sha256="0" * 64,
        )

    link = root / "linked.sqlite"
    link.symlink_to(database.name)
    sha = _write_manifest(
        manifest,
        [
            ("real.sqlite", "ordinary_opaque", "file"),
            ("linked.sqlite", "ordinary_opaque", "file"),
        ],
    )
    with pytest.raises(SQLiteVerificationError, match="database_path_symlink_rejected"):
        verify_sqlite(
            payload_root=payload,
            manifest_path=manifest,
            expected_manifest_sha256=sha,
        )
