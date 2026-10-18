"""Pure identity and logical-state contract checks for handoff-v1."""

import sqlite3

from memecoin_operator.linux_handoff import _handoff_identity, _logical_state_payload


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
