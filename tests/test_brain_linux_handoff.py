"""Pure identity and logical-state contract checks for handoff-v1."""

import sqlite3
from pathlib import Path

import pytest

from memecoin_operator.linux_handoff import (
    StorageError,
    _handoff_identity,
    _logical_state_payload,
    _verify_recovery_generation_boundary,
)


def _database_bytes(values: tuple[str, ...]) -> bytes:
    db = sqlite3.connect(":memory:")
    try:
        db.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        db.executemany("INSERT INTO sample(value) VALUES (?)", [(value,) for value in values])
        return db.serialize()
    finally:
        db.close()


def test_handoff_identity_binds_installation_generation_release_and_policy():
    identity = _handoff_identity("night-lab", "a" * 64, "b" * 64)
    assert identity == _handoff_identity("night-lab", "a" * 64, "b" * 64)
    assert identity != _handoff_identity("other-lab", "a" * 64, "b" * 64)
    assert identity != _handoff_identity("night-lab", "c" * 64, "b" * 64)
    assert identity != _handoff_identity("night-lab", "a" * 64, "d" * 64)


def test_logical_state_digest_is_stable_for_equal_rows_and_changes_with_state():
    schema_a, state_a = _logical_state_payload(_database_bytes(("one", "two")))
    schema_b, state_b = _logical_state_payload(_database_bytes(("one", "two")))
    schema_c, state_c = _logical_state_payload(_database_bytes(("one", "different")))
    assert schema_a == schema_b == schema_c
    assert state_a == state_b
    assert state_a != state_c


def test_recovery_requires_exact_generation_root_members(tmp_path: Path, monkeypatch):
    generation = tmp_path / "generation"
    (generation / "operator").mkdir(parents=True)
    (generation / "foreign").mkdir()
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, **_kwargs: "1 0 0:1 / / rw - ext4 /dev/root rw",
    )
    with pytest.raises(StorageError, match="generation_contents_unsafe"):
        _verify_recovery_generation_boundary(generation)


@pytest.mark.parametrize("nested", ["", "/operator/runtime"])
def test_recovery_rejects_mounts_at_or_below_generation(tmp_path: Path, monkeypatch, nested: str):
    generation = tmp_path / "generation"
    (generation / "operator").mkdir(parents=True)
    mountpoint = f"{generation}{nested}"
    line = f"1 0 0:1 / {mountpoint} rw - ext4 /dev/root rw"
    real_read_text = Path.read_text

    def read_text(self, **kwargs):
        if self == Path("/proc/self/mountinfo"):
            return line
        return real_read_text(self, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(StorageError, match="nested_mount_unsafe"):
        _verify_recovery_generation_boundary(generation)
