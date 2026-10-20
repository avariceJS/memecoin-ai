"""Synthetic tests for durable, content-bound A10 contract transitions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engineering.org_state import StateError, Store


@pytest.fixture
def store(tmp_path):
    db = Store(Path(tmp_path) / "org.sqlite", clock=lambda: 1000.0)
    try:
        db.put_contract("sample", 1, {"schema": "v1"})
        yield db
    finally:
        db.close()


def propose(db, body=None):
    return db.propose_contract_transition(
        "sample",
        1,
        2,
        {"schema": "v2"} if body is None else body,
        producer_task_id="EXP-1",
        producer_team="Experience",
        consumer_teams=["Platform"],
    )


def integrate_producer(db):
    db.add_task("EXP-1", "Experience", "produce contract", scope=["schema.py"])
    attempt = db.claim("EXP-1", "synthetic", 30)
    db.submit(attempt["id"], attempt["epoch"], "candidate-sha", {"receipt": "synthetic"})
    db.verify("EXP-1", "candidate-sha", True, {"passed": True})
    db.accept("EXP-1", "candidate-sha")
    db.integrate("EXP-1", "candidate-sha", "integration-sha", {"merged": True})


def test_transition_requires_integrated_producer_and_exact_participant_acks(store):
    transition = propose(store)
    digest = transition["body"]["contract_hash"]
    with pytest.raises(StateError, match="producer is not integrated"):
        store.activate_contract_transition("sample", 2)
    assert store.get_contract("sample")["version"] == 1

    integrate_producer(store)
    store.acknowledge_contract_transition("sample", 2, "Experience", digest)
    with pytest.raises(StateError, match="acknowledgements incomplete"):
        store.activate_contract_transition("sample", 2)
    with pytest.raises(StateError, match="does not match proposal"):
        store.acknowledge_contract_transition("sample", 2, "Platform", "0" * 64)
    with pytest.raises(StateError, match="does not match proposal"):
        store.acknowledge_contract_transition("sample", 2, "Research", digest)
    assert store.get_contract("sample")["version"] == 1

    store.acknowledge_contract_transition("sample", 2, "Platform", digest)
    result = store.activate_contract_transition("sample", 2)
    assert result["body"]["acknowledged_teams"] == ["Experience", "Platform"]
    assert store.get_contract("sample") == {
        "name": "sample",
        "version": 2,
        "body": {"schema": "v2"},
    }
    repeated = store.activate_contract_transition("sample", 2)
    assert repeated["body"] == result["body"]
    assert repeated["created"] is False
    assert result["created"] is True
    assert len([e for e in store.events() if e["kind"] == "contract_published"]) == 2


def test_proposal_and_ack_retries_are_exact_and_survive_restart(tmp_path):
    path = Path(tmp_path) / "org.sqlite"
    db = Store(path, clock=lambda: 1000.0)
    db.put_contract("sample", 1, {"schema": "v1"})
    transition = propose(db)
    digest = transition["body"]["contract_hash"]
    assert propose(db) == transition
    db.acknowledge_contract_transition("sample", 2, "Experience", digest)
    assert db.acknowledge_contract_transition("sample", 2, "Experience", digest)["version"] == 1
    with pytest.raises(StateError, match="immutable"):
        propose(db, {"schema": "changed"})
    db.close()

    reopened = Store(path, clock=lambda: 1000.0)
    try:
        integrate_producer(reopened)
        reopened.acknowledge_contract_transition("sample", 2, "Platform", digest)
        reopened.activate_contract_transition("sample", 2)
        assert reopened.get_contract("sample")["version"] == 2
    finally:
        reopened.close()


def test_generic_decisions_cannot_bypass_transition_or_ack_apis(store):
    for name in (
        "contract-transition/anything",
        "contract-ack/anything",
    ):
        with pytest.raises(StateError, match="namespace_reserved"):
            store.put_decision(name, {"status": "activated"})


def test_public_contract_publication_cannot_bypass_transition_acks(store):
    transition = propose(store)
    with pytest.raises(StateError, match="require an acknowledged transition"):
        store.put_contract("sample", 2, transition["body"]["body"])
    assert store.get_contract("sample")["version"] == 1


def test_transition_rejects_skipped_versions_duplicate_participants_and_bad_hash(store):
    with pytest.raises(StateError, match="advance exactly one"):
        store.propose_contract_transition(
            "sample",
            1,
            3,
            {},
            producer_task_id="EXP-1",
            producer_team="Experience",
            consumer_teams=["Platform"],
        )
    with pytest.raises(StateError, match="participants must be unique"):
        store.propose_contract_transition(
            "sample",
            1,
            2,
            {},
            producer_task_id="EXP-1",
            producer_team="Experience",
            consumer_teams=["Platform", "Platform"],
        )
    transition = propose(store)
    with pytest.raises(StateError, match="lowercase SHA-256"):
        store.acknowledge_contract_transition("sample", 2, "Experience", "BAD")
    assert transition["body"]["status"] == "proposed"


def test_invalid_body_version_does_not_poison_transition_identity(store):
    with pytest.raises(StateError, match="body version mismatch"):
        propose(store, {"version": 3})
    assert (
        store.db.execute(
            "SELECT COUNT(*) FROM decisions WHERE name LIKE 'contract-transition/%'"
        ).fetchone()[0]
        == 0
    )
    assert (
        store.db.execute(
            "SELECT COUNT(*) FROM events WHERE kind='decision_recorded' "
            "AND entity LIKE 'contract-transition/%'"
        ).fetchone()[0]
        == 0
    )
    corrected = propose(store, {"version": 2})
    assert corrected["body"]["to_version"] == 2


def test_transition_and_ack_histories_must_each_be_exactly_one_record(store):
    transition = propose(store)
    store.db.execute(
        "INSERT INTO decisions(name,version,body,replaces,replaces_version) VALUES(?,?,?,?,?)",
        (
            transition["name"],
            2,
            store.db.execute(
                "SELECT body FROM decisions WHERE name=? AND version=1", (transition["name"],)
            ).fetchone()[0],
            transition["name"],
            1,
        ),
    )
    with pytest.raises(StateError, match="decision history invalid"):
        store.acknowledge_contract_transition(
            "sample", 2, "Experience", transition["body"]["contract_hash"]
        )


def test_transition_rejects_corrupt_skipped_version_and_missing_participant(store):
    transition = propose(store)
    corrupt = transition["body"]
    corrupt["to_version"] = 3
    corrupt["consumer_teams"] = []
    store.db.execute(
        "UPDATE decisions SET body=? WHERE name=? AND version=1",
        (json.dumps(corrupt), transition["name"]),
    )
    with pytest.raises(StateError, match="decision history invalid"):
        store.activate_contract_transition("sample", 2)


def test_canonical_snapshot_preserves_participants_and_mixed_numeric_keys(store):
    consumers = ["Platform"]

    class MutatingDict(dict):
        def items(self):
            consumers.clear()
            return super().items()

    proposal = store.propose_contract_transition(
        "sample",
        1,
        2,
        MutatingDict({2: "numeric", 10: "key"}),
        producer_task_id="EXP-1",
        producer_team="Experience",
        consumer_teams=consumers,
    )
    assert consumers == []
    assert proposal["body"]["consumer_teams"] == ["Platform"]
    digest = proposal["body"]["contract_hash"]
    integrate_producer(store)
    store.acknowledge_contract_transition("sample", 2, "Experience", digest)
    store.acknowledge_contract_transition("sample", 2, "Platform", digest)
    store.activate_contract_transition("sample", 2)
    assert store.get_contract("sample")["body"] == {"10": "key", "2": "numeric"}


def test_activation_retry_revalidates_complete_activation_record(store):
    transition = propose(store)
    integrate_producer(store)
    digest = transition["body"]["contract_hash"]
    for team in ("Experience", "Platform"):
        store.acknowledge_contract_transition("sample", 2, team, digest)
    result = store.activate_contract_transition("sample", 2)
    store.db.execute(
        "UPDATE decisions SET body=? WHERE name=? AND version=1",
        ('{"contract_hash":"' + digest + '"}', result["name"]),
    )
    with pytest.raises(StateError, match="contract decision history invalid"):
        store.activate_contract_transition("sample", 2)


def test_ack_history_corruption_blocks_activation(store):
    transition = propose(store)
    integrate_producer(store)
    digest = transition["body"]["contract_hash"]
    store.acknowledge_contract_transition("sample", 2, "Experience", digest)
    store.acknowledge_contract_transition("sample", 2, "Platform", digest)
    ack = store.db.execute(
        "SELECT name,body FROM decisions WHERE name LIKE 'contract-ack/%' ORDER BY name LIMIT 1"
    ).fetchone()
    store.db.execute(
        "INSERT INTO decisions(name,version,body,replaces,replaces_version) VALUES(?,?,?,?,?)",
        (ack["name"], 2, ack["body"], ack["name"], 1),
    )
    with pytest.raises(StateError, match="decision history invalid"):
        store.activate_contract_transition("sample", 2)
