from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import tools.brain_host_sqlite_backup as sqlite_backup
from tools.brain_host_sqlite_backup import SQLiteBackupError, backup_read_only


def family_state(source: Path) -> dict[str, tuple[int, int, int, str] | None]:
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(f"{source}{suffix}")
        if not path.exists():
            result[suffix] = None
            continue
        metadata = path.stat()
        result[suffix] = (
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return result


def backup(source: Path, destination: Path, root: Path, **kwargs):
    return backup_read_only(
        source,
        destination,
        allowed_root=root,
        logical_id="fixture-db",
        timeout_seconds=kwargs.pop("timeout_seconds", 3),
        busy_timeout_ms=kwargs.pop("busy_timeout_ms", 100),
        **kwargs,
    )


def test_online_backup_preserves_writer_held_wal_database_bytes(tmp_path: Path) -> None:
    source = tmp_path / "owner-work"
    source.mkdir()
    database = source / "paper.sqlite"
    writer = sqlite3.connect(database)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE observations(value TEXT)")
    writer.commit()
    writer.execute("INSERT INTO observations VALUES ('durable-row')")
    writer.commit()
    before = family_state(database)

    destination = tmp_path / "snapshot" / "snapshot.bin"
    destination.parent.mkdir(mode=0o700)
    receipt = backup(database, destination, source)

    after = family_state(database)
    assert receipt["source_logical_writes_initiated"] is False
    assert receipt["source_durable_writes_initiated"] is False
    assert receipt["transient_shm_coordination_may_change"] is True
    assert before[""] == after[""]
    assert before["-wal"] == after["-wal"]
    assert receipt["source_shm_signature_changed"] == (
        receipt["source_family_before"]["-shm"] != receipt["source_family_after"]["-shm"]
    )
    restored = sqlite3.connect(f"file:{destination}?mode=ro", uri=True)
    try:
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert restored.execute("SELECT value FROM observations").fetchall() == [("durable-row",)]
    finally:
        restored.close()
        writer.close()


def test_closed_database_backup_has_valid_integrity(tmp_path: Path) -> None:
    source = tmp_path / "owner-work"
    source.mkdir()
    database = source / "index.db"
    with sqlite3.connect(database) as writer:
        writer.execute("CREATE TABLE values_table(value INTEGER)")
        writer.execute("INSERT INTO values_table VALUES (7)")
    destination = tmp_path / "snapshot.bin"
    receipt = backup(database, destination, source)
    assert receipt["backup_integrity"] == "ok"
    assert receipt["backup_page_count"] > 0


def test_sole_wal_reader_backup_keeps_durable_family(tmp_path: Path) -> None:
    source = tmp_path / "owner-work"
    source.mkdir()
    database = source / "reader.sqlite"
    writer = sqlite3.connect(database)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE observations(value INTEGER)")
    writer.execute("INSERT INTO observations VALUES (11)")
    writer.commit()
    reader = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    assert reader.execute("SELECT value FROM observations").fetchall() == [(11,)]
    before = family_state(database)
    destination = tmp_path / "snapshot.bin"
    receipt = backup(database, destination, source)
    after = family_state(database)
    assert before[""] == after[""]
    assert before["-wal"] == after["-wal"]
    assert receipt["backup_integrity"] == "ok"
    reader.close()
    writer.close()


def test_declared_protected_root_is_never_opened(tmp_path: Path) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()
    database = protected / "holdout.sqlite"
    with sqlite3.connect(database) as writer:
        writer.execute("CREATE TABLE t(x)")
    destination = tmp_path / "snapshot.bin"
    with pytest.raises(SQLiteBackupError, match="protected_source_forbidden"):
        backup_read_only(
            database,
            destination,
            allowed_root=tmp_path,
            protected_roots=[protected],
            logical_id="protected-fixture",
        )
    assert not destination.exists()


def test_symlink_source_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "owner-work"
    root.mkdir()
    real = root / "real.sqlite"
    with sqlite3.connect(real) as writer:
        writer.execute("CREATE TABLE t(x)")
    alias = root / "alias.sqlite"
    alias.symlink_to(real)
    with pytest.raises(SQLiteBackupError, match="source_not_regular_file"):
        backup(alias, tmp_path / "snapshot.bin", root)


def test_destination_collision_is_create_only(tmp_path: Path) -> None:
    root = tmp_path / "owner-work"
    root.mkdir()
    database = root / "state.sqlite"
    with sqlite3.connect(database) as writer:
        writer.execute("CREATE TABLE t(x)")
    destination = tmp_path / "snapshot.bin"
    destination.write_bytes(b"keep")
    with pytest.raises(SQLiteBackupError, match="destination_must_be_absent"):
        backup(database, destination, root)
    assert destination.read_bytes() == b"keep"


def test_recovery_needed_rollback_journal_preserves_uncommitted_state_as_unknown(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owner-work"
    root.mkdir()
    database = root / "crash.sqlite"
    init = sqlite3.connect(database)
    init.execute("PRAGMA journal_mode=DELETE")
    init.execute("CREATE TABLE t(x)")
    init.commit()
    init.close()
    program = (
        "import sqlite3, os, sys; "
        "c=sqlite3.connect(sys.argv[1]); c.execute('BEGIN EXCLUSIVE'); "
        "c.execute('INSERT INTO t VALUES (1)'); os._exit(0)"
    )
    subprocess.run([sys.executable, "-c", program, str(database)], check=True)
    journal = Path(f"{database}-journal")
    assert journal.exists()
    before = family_state(database)
    destination = tmp_path / "snapshot.bin"
    receipt = backup(database, destination, root)
    assert family_state(database) == before
    assert receipt["backup_integrity"] == "ok"
    assert receipt["rollback_journal_present_at_start"] is True
    restored = sqlite3.connect(f"file:{destination}?mode=ro", uri=True)
    try:
        # The durable state is recovered; the interrupted transaction is not promoted.
        assert restored.execute("SELECT * FROM t").fetchall() == []
    finally:
        restored.close()


def test_busy_database_fails_with_bounded_timeout_and_cleanup(tmp_path: Path) -> None:
    root = tmp_path / "owner-work"
    root.mkdir()
    database = root / "locked.sqlite"
    writer = sqlite3.connect(database, timeout=0.1)
    writer.execute("PRAGMA journal_mode=DELETE")
    writer.execute("CREATE TABLE t(x)")
    writer.commit()
    writer.execute("BEGIN EXCLUSIVE")
    writer.execute("INSERT INTO t VALUES (1)")
    before = family_state(database)
    destination = tmp_path / "snapshot.bin"
    try:
        with pytest.raises(sqlite3.Error):
            backup(
                database,
                destination,
                root,
                busy_timeout_ms=100,
                timeout_seconds=0.2,
            )
        assert family_state(database) == before
        assert not destination.exists()
    finally:
        writer.rollback()
        writer.close()


def test_deadline_cancels_backup_and_removes_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "owner-work"
    root.mkdir()
    database = root / "large.sqlite"
    with sqlite3.connect(database) as writer:
        writer.execute("CREATE TABLE t(value TEXT)")
        writer.executemany("INSERT INTO t VALUES (?)", [("x" * 2048,) for _ in range(4000)])

    now = 0.0

    def advancing_clock() -> float:
        nonlocal now
        now += 0.2
        return now

    monkeypatch.setattr(sqlite_backup.time, "monotonic", advancing_clock)
    destination = tmp_path / "snapshot.bin"
    with pytest.raises(TimeoutError, match="sqlite_backup_deadline"):
        backup(
            database,
            destination,
            root,
            timeout_seconds=0.1,
            pages_per_step=1,
        )
    assert not destination.exists()
