"""Synthetic tests for durable, content-bound A10 contract transitions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from unittest.mock import patch

import pytest

from engineering import org_state, supervisor
from engineering.org_state import (
    _CONTRACT_REVIEW_BOUNDED_RECORD_QUERY,
    _CONTRACT_REVIEW_GLOBAL_REPLAY_FIRST_QUERY,
    _CONTRACT_REVIEW_GLOBAL_REPLAY_NEXT_QUERY,
    _CONTRACT_REVIEW_TRANSITION_REPLAY_FIRST_QUERY,
    _CONTRACT_REVIEW_TRANSITION_REPLAY_NEXT_QUERY,
    StateError,
    Store,
)


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


def integrate_producer(db, task_id="EXP-1"):
    db.add_task(task_id, "Experience", "produce contract", scope=["schema.py"])
    attempt = db.claim(task_id, "synthetic", 30)
    db.submit(attempt["id"], attempt["epoch"], "candidate-sha", {"receipt": "synthetic"})
    db.verify(task_id, "candidate-sha", True, {"passed": True})
    db.accept(task_id, "candidate-sha")
    db.integrate(task_id, "candidate-sha", "integration-sha", {"merged": True})


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
    assert store.contract_transition_review_projection() == {
        "items": [],
        "pending_count": 0,
        "omitted_count": 0,
    }
    assert len([e for e in store.events() if e["kind"] == "contract_published"]) == 2


def test_contract_transition_batch_rolls_back_when_later_proposal_conflicts(store):
    store.put_contract("alpha", 1, {"schema": "alpha-v1"})
    existing = propose(store)
    events_before = store.events()
    alpha = {
        "name": "alpha",
        "from_version": 1,
        "to_version": 2,
        "body": {"schema": "alpha-v2"},
        "producer_task_id": "EXP-1",
        "producer_team": "Experience",
        "consumer_teams": ["Platform"],
    }
    conflicting = {
        "name": "sample",
        "from_version": 1,
        "to_version": 2,
        "body": {"schema": "different-v2"},
        "producer_task_id": "EXP-1",
        "producer_team": "Experience",
        "consumer_teams": ["Platform"],
    }

    with pytest.raises(StateError, match="proposal is immutable"):
        store.propose_contract_transitions([alpha, conflicting])

    assert store.get_contract_transition("alpha", 2) is None
    retained = store.get_contract_transition("sample", 2)
    assert retained["body"] == existing["body"]
    assert retained["version"] == existing["version"]
    assert store.events() == events_before


def test_contract_transition_batch_detaches_input_before_write_lock_wait(store, tmp_path):
    body = {"schema": "v2"}
    consumers = ["Platform"]
    proposal = {
        "name": "sample",
        "from_version": 1,
        "to_version": 2,
        "body": body,
        "producer_task_id": "EXP-1",
        "producer_team": "Experience",
        "consumer_teams": consumers,
    }
    proposals = [proposal]
    other = sqlite3.connect(str(Path(tmp_path) / "org.sqlite"), timeout=30, isolation_level=None)
    waiting_for_write_lock = threading.Event()
    store.db.set_trace_callback(
        lambda statement: (
            waiting_for_write_lock.set() if statement.strip().upper() == "BEGIN IMMEDIATE" else None
        )
    )
    other.execute("BEGIN IMMEDIATE")
    pool = ThreadPoolExecutor(1)
    future = None
    try:
        future = pool.submit(store.propose_contract_transitions, proposals)
        assert waiting_for_write_lock.wait(2)
        with pytest.raises(FutureTimeout):
            future.result(timeout=0.05)
        body["schema"] = "mutated"
        consumers[:] = ["Research"]
        proposals.append(
            {
                **proposal,
                "name": "missing-contract",
                "from_version": 1,
                "to_version": 2,
            }
        )
        other.execute("COMMIT")
        result = future.result(timeout=2)
    finally:
        if other.in_transaction:
            other.execute("ROLLBACK")
        pool.shutdown(wait=True)
        store.db.set_trace_callback(None)
        other.close()

    assert len(result) == 1
    persisted = store.get_contract_transition("sample", 2)["body"]
    assert persisted["body"] == {"schema": "v2"}
    assert persisted["consumer_teams"] == ["Platform"]
    assert persisted["contract_hash"] == hashlib.sha256(b'{"schema":"v2"}').hexdigest()


def test_contract_transition_batch_count_limit_uses_detached_list_snapshot(store, monkeypatch):
    import engineering.org_state as org_state

    monkeypatch.setattr(org_state, "CONTRACT_TRANSITION_BATCH_MAX_PROPOSALS", 1)
    first = {
        "name": "sample",
        "from_version": 1,
        "to_version": 2,
        "body": {"schema": "v2"},
        "producer_task_id": "EXP-1",
        "producer_team": "Experience",
        "consumer_teams": ["Platform"],
    }
    appended = {
        **first,
        "name": "missing-contract",
    }
    proposals = [first]
    validate = store._validated_contract_transition
    appended_once = False

    def append_during_preparation(body, name, version):
        nonlocal appended_once
        result = validate(body, name, version)
        if not appended_once:
            appended_once = True
            proposals.append(appended)
        return result

    monkeypatch.setattr(store, "_validated_contract_transition", append_during_preparation)
    result = store.propose_contract_transitions(proposals)

    assert len(result) == 1
    assert result[0]["body"]["name"] == "sample"
    assert store.get_contract_transition("sample", 2) is not None
    assert store.get_contract_transition("missing-contract", 2) is None


def test_contract_transition_batch_enforces_count_and_record_byte_limits(store, monkeypatch):
    first = {
        "name": "first",
        "from_version": 1,
        "to_version": 2,
        "body": {"schema": "v2"},
        "producer_task_id": "EXP-1",
        "producer_team": "Experience",
        "consumer_teams": ["Platform"],
    }
    second = {**first, "name": "second"}
    events_before = store.events()
    monkeypatch.setattr(org_state, "CONTRACT_TRANSITION_BATCH_MAX_PROPOSALS", 1)
    with pytest.raises(StateError, match="batch exceeds count limit"):
        store.propose_contract_transitions([first, second])
    assert store.events() == events_before
    assert store.get_contract_transition("first", 2) is None

    record = {
        "kind": "contract_transition",
        "status": "proposed",
        "name": first["name"],
        "from_version": first["from_version"],
        "to_version": first["to_version"],
        "body": first["body"],
        "contract_hash": hashlib.sha256(org_state._json(first["body"]).encode()).hexdigest(),
        "producer_task_id": first["producer_task_id"],
        "producer_team": first["producer_team"],
        "consumer_teams": first["consumer_teams"],
    }
    exact_bytes = len(org_state._json(record).encode("utf-8"))
    monkeypatch.setattr(org_state, "CONTRACT_TRANSITION_BATCH_MAX_PROPOSALS", 1000)
    monkeypatch.setattr(org_state, "_CONTRACT_TRANSITION_BATCH_MAX_RECORD_BYTES", exact_bytes)
    store.put_contract("first", 1, {"schema": "v1"})
    accepted = store.propose_contract_transition(
        "first",
        1,
        2,
        first["body"],
        producer_task_id=first["producer_task_id"],
        producer_team=first["producer_team"],
        consumer_teams=first["consumer_teams"],
    )
    assert accepted["body"] == record

    oversized = {**first, "name": "oversized", "body": {"schema": "v" * 100}}
    store.put_contract("oversized", 1, {"schema": "v1"})
    events_before = store.events()
    with pytest.raises(StateError, match="proposal exceeds byte limit"):
        store.propose_contract_transition(
            "oversized",
            1,
            2,
            oversized["body"],
            producer_task_id=oversized["producer_task_id"],
            producer_team=oversized["producer_team"],
            consumer_teams=oversized["consumer_teams"],
        )
    assert store.events() == events_before
    assert store.get_contract_transition("oversized", 2) is None


def test_contract_transition_batch_enforces_aggregate_byte_limit(store, monkeypatch):
    proposals = [
        {
            "name": name,
            "from_version": 1,
            "to_version": 2,
            "body": {"schema": "v2"},
            "producer_task_id": "EXP-1",
            "producer_team": "Experience",
            "consumer_teams": ["Platform"],
        }
        for name in ("alpha", "bravo")
    ]
    records = []
    for proposal in proposals:
        records.append(
            {
                "kind": "contract_transition",
                "status": "proposed",
                "name": proposal["name"],
                "from_version": proposal["from_version"],
                "to_version": proposal["to_version"],
                "body": proposal["body"],
                "contract_hash": hashlib.sha256(
                    org_state._json(proposal["body"]).encode()
                ).hexdigest(),
                "producer_task_id": proposal["producer_task_id"],
                "producer_team": proposal["producer_team"],
                "consumer_teams": proposal["consumer_teams"],
            }
        )
    sizes = [len(org_state._json(record).encode("utf-8")) for record in records]
    assert sizes[0] == sizes[1]
    monkeypatch.setattr(org_state, "_CONTRACT_TRANSITION_BATCH_MAX_RECORD_BYTES", sizes[0])
    monkeypatch.setattr(org_state, "_CONTRACT_TRANSITION_BATCH_MAX_BYTES", sum(sizes) - 1)
    events_before = store.events()

    with pytest.raises(StateError, match="batch exceeds byte limit"):
        store.propose_contract_transitions(proposals)

    assert store.events() == events_before
    assert store.get_contract_transition("alpha", 2) is None
    assert store.get_contract_transition("bravo", 2) is None


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


def test_quiescence_snapshot_contains_pending_reviews_from_the_same_store_projection(store):
    propose(store)

    snapshot = store.quiescence_snapshot()

    assert snapshot["contract_reviews"] == store.contract_transition_review_projection()
    assert snapshot["contract_reviews"]["pending_count"] == 2
    assert [item["participant_team"] for item in snapshot["contract_reviews"]["items"]] == [
        "Experience",
        "Platform",
    ]


def test_quiescence_handoff_carries_exact_prepared_review_ids_without_packets(store):
    request = prepare_review(store)
    snapshot = store.quiescence_snapshot()
    worklist = snapshot["review_requests"]
    assert worklist["items"][0]["request_id"] == request["body"]["request_id"]
    assert worklist["items"][0]["packet_hash"] == request["body"]["packet_hash"]
    assert "packet" not in worklist["items"][0]

    identity = {
        "snapshot": snapshot,
        "resource_reason": "synthetic",
        "reason": "contract-reviews-pending",
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    body = {
        "owner": "Sol",
        "status": "requested",
        "reason": "contract-reviews-pending",
        "resource_reason": "synthetic",
        "fingerprint": fingerprint,
        "review_requests": worklist,
    }

    assert store.record_quiescence_handoff_if_current(fingerprint, snapshot, body)
    assert store.get_decision("sol-handoff/" + fingerprint)["body"]["review_requests"] == worklist


def test_quiescence_ignores_case_variant_non_reserved_transition_names(store):
    before = store.quiescence_snapshot()

    store.put_decision("CONTRACT-TRANSITION/unrelated", {"status": "ordinary"})

    after = store.quiescence_snapshot()
    assert after["revision"] == before["revision"]
    assert after["contract_reviews"] == before["contract_reviews"]


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


def review_packet():
    return {
        "source_commit": "a" * 40,
        "evidence_refs": [{"ref": "compatibility:receipt-1", "sha256": "b" * 64}],
        "compatibility_analysis": "Preserved v1 behavior and reviewed every Platform task.",
        "migration_plan": None,
    }


def review_result(**overrides):
    result = {
        "decision": "approve",
        "compatibility": "compatible",
        "rationale": "The reviewed interface preserves the recorded behavior.",
        "findings": [],
        "reviewed_evidence_refs": ["compatibility:receipt-1"],
        "affected_consumers_reviewed": ["PLAT-1"],
        "migration_plan": None,
    }
    result.update(overrides)
    return result


def record_review_result(store, body, result, raw_result=None):
    if raw_result is None:
        raw_result = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return store.record_contract_review_response(
        body["request_id"],
        body["packet_hash"],
        body["dispatch_id"],
        "provider-turn-" + body["request_id"],
        raw_result,
    )


def finish_recorded_review(store, body, result):
    response = record_review_result(store, body, result)
    return store.finish_contract_review(
        body["request_id"], body["packet_hash"], response["body"]["raw_sha256"], result
    )


def review_session_thread_id_at_size(store, request, target_bytes):
    body = request["body"]
    dispatching = store.get_decision(request["name"])["body"]
    session = {
        "thread_id": "",
        "model": "model",
        "turn_dispatch_id": body["dispatch_id"],
        "process_identity": {"pid": 123, "birth": "birth"},
    }
    candidate = {**dispatching, "status": "thread_known", "session": session}
    thread_length = target_bytes - len(org_state._json(candidate).encode("utf-8"))
    assert thread_length > 0
    session["thread_id"] = "x" * thread_length
    candidate["session"] = session
    assert len(org_state._json(candidate).encode("utf-8")) == target_bytes
    return session["thread_id"]


def review_result_at_serialized_size(target_bytes):
    findings = ["x" * 1000 for _ in range(32)] + [""]
    result = review_result(findings=findings)
    base_bytes = len(org_state._json(result).encode("utf-8"))
    findings[-1] = "x" * (target_bytes - base_bytes)
    assert len(findings[-1]) <= 1000
    assert len(org_state._json(result).encode("utf-8")) == target_bytes
    return result


def prepare_review(store, *, consumer_contracts=None, producer_contracts=None):
    store.add_task(
        "EXP-1", "Experience", "produce contract", scope=["schema.py"], contracts=producer_contracts
    )
    store.add_task(
        "PLAT-1",
        "Platform",
        "consume contract",
        scope=["cli.py"],
        contracts=consumer_contracts,
    )
    propose(store)
    revision = "c" * 64
    return store.prepare_contract_review("sample", 2, "Platform", revision, review_packet())


def test_task_independent_contract_review_is_durable_and_acknowledges_atomically(store):
    request = prepare_review(store)
    body = request["body"]
    assert body["status"] == "prepared"
    assert body["role"] == "consumer"
    assert body["packet"]["affected_consumer_task_ids"] == ["PLAT-1"]
    assert body["packet"]["participant_tasks"][0]["id"] == "PLAT-1"
    assert (
        store.prepare_contract_review("sample", 2, "Platform", "c" * 64, review_packet()) == request
    )
    with pytest.raises(StateError, match="namespace_reserved"):
        store.put_decision(request["name"], {"status": "approved"})

    dispatching = store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    assert dispatching["body"]["status"] == "dispatching"
    with pytest.raises(StateError, match="process identity invalid"):
        store.record_contract_review_session(
            body["request_id"],
            body["packet_hash"],
            "thread-1",
            "gpt-6.1-sol",
            body["dispatch_id"],
            {"pid": True, "birth": None},
        )
    assert store.get_decision(request["name"])["body"]["status"] == "dispatching"
    with pytest.raises(StateError, match="not sendable"):
        store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    session = store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    assert session["body"]["status"] == "thread_known"
    assert (
        store.record_contract_review_session(
            body["request_id"],
            body["packet_hash"],
            "thread-1",
            "gpt-6.1-sol",
            body["dispatch_id"],
            {"pid": 123, "birth": "synthetic-birth"},
        )
        == session
    )
    result = review_result()
    with pytest.raises(StateError, match="not durably recorded"):
        store.finish_contract_review(body["request_id"], body["packet_hash"], "0" * 64, result)
    with pytest.raises(StateError, match="dispatch identity mismatch"):
        store.record_contract_review_response(
            body["request_id"], body["packet_hash"], "wrong-dispatch", "provider-turn", b"{}"
        )
    response = record_review_result(store, body, result)
    receipt_hash = response["body"]["raw_sha256"]
    altered = review_result(decision="blocked", compatibility="insufficient")
    with pytest.raises(StateError, match="does not match parsed result"):
        store.finish_contract_review(body["request_id"], body["packet_hash"], receipt_hash, altered)
    finished = store.finish_contract_review(
        body["request_id"], body["packet_hash"], receipt_hash, result
    )
    assert finished["body"]["status"] == "approved"
    ack = store.get_decision(store._contract_ack_key("sample", 2, "Platform"))
    assert ack["body"] == {
        "kind": "contract_acknowledgement",
        "name": "sample",
        "version": 2,
        "team": "Platform",
        "contract_hash": body["contract_hash"],
    }
    assert (
        store.finish_contract_review(body["request_id"], body["packet_hash"], receipt_hash, result)
        == finished
    )


def test_migration_required_review_is_custodied_but_cannot_acknowledge(store):
    integrate_producer(store)
    store.add_task("PLAT-1", "Platform", "consume contract", scope=["cli.py"])
    propose(store)
    request = store.prepare_contract_review("sample", 2, "Platform", "c" * 64, review_packet())
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-migration-review",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    result = review_result(
        compatibility="migration_required",
        migration_plan="Update all consumers before publishing this transition.",
    )

    finished = finish_recorded_review(store, body, result)

    assert finished["body"]["status"] == "blocked"
    assert finished["body"]["model_result"] == result
    assert finished["body"]["result"] == result
    assert "migration_completion_unqualified" in finished["body"]["blocking_reasons"]
    assert (
        store.get_contract_review_response(body["request_id"])["raw_sha256"]
        == finished["body"]["raw_sha256"]
    )
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None
    assert finish_recorded_review(store, body, result) == finished
    events_before_replay = store.events()
    replayed = store.replay_contract_review_response(body["request_id"], body["packet_hash"])
    assert replayed == finished
    current = store.get_decision(request["name"])
    assert current["version"] == finished["version"]
    assert current["body"] == finished["body"]
    assert store.events() == events_before_replay

    store.acknowledge_contract_transition("sample", 2, "Experience", body["contract_hash"])
    with pytest.raises(StateError, match="acknowledgements incomplete"):
        store.activate_contract_transition("sample", 2)
    assert store.get_contract("sample")["version"] == 1
    assert store.get_decision(store._contract_transition_key("sample", 2) + "/activated") is None

    successor = store.prepare_contract_review("sample", 2, "Platform", "d" * 64, review_packet())
    successor_body = successor["body"]
    store.begin_contract_review_dispatch(
        successor_body["request_id"], successor_body["packet_hash"]
    )
    store.record_contract_review_session(
        successor_body["request_id"],
        successor_body["packet_hash"],
        "thread-compatible-review",
        "gpt-6.1-sol",
        successor_body["dispatch_id"],
        {"pid": 124, "birth": "synthetic-birth-2"},
    )
    replacement = finish_recorded_review(store, successor_body, review_result())
    assert replacement["body"]["status"] == "approved"
    historical = store.get_decision(request["name"])
    assert historical["version"] == finished["version"]
    assert historical["body"] == finished["body"]


def test_contract_review_currentness_allows_blocked_consumer_without_send_authority(store):
    request = prepare_review(store, consumer_contracts={"sample": 2})
    body = request["body"]

    assert store.claim("PLAT-1", "synthetic") is None
    assert store.contract_review_currentness(body["request_id"], body["packet_hash"]) == {
        "request_id": body["request_id"],
        "packet_hash": body["packet_hash"],
        "journal_version": 1,
        "status": "prepared",
        "current": True,
        "blockers": [],
    }
    dispatching = store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    assert dispatching["body"]["status"] == "dispatching"
    assert dispatching["version"] == 2
    with pytest.raises(StateError, match="not sendable"):
        store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])


def test_contract_review_worklist_is_bounded_read_only_and_not_dispatch_authority(store):
    request = prepare_review(store)
    body = request["body"]
    events_before = len(store.events())

    worklist = store.contract_review_worklist()

    assert worklist == {
        "items": [
            {
                "request_id": body["request_id"],
                "journal_version": 1,
                "status": "prepared",
                "team": "Platform",
                "role": "consumer",
                "packet_hash": body["packet_hash"],
                "current": True,
                "blockers": [],
                "dispatch_eligible": True,
            }
        ],
        "count": 1,
        "limit": 100,
        "dispatch_authority": False,
    }
    assert "packet" not in worklist["items"][0]
    assert store.get_decision(request["name"])["version"] == 1
    assert len(store.events()) == events_before

    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    dispatching = store.contract_review_worklist()["items"][0]
    assert dispatching["status"] == "dispatching"
    assert dispatching["dispatch_eligible"] is False

    with pytest.raises(StateError, match="limit invalid"):
        store.contract_review_worklist(limit=True)


def test_contract_review_worklist_fails_closed_above_bound(store):
    prepare_review(store)
    store.prepare_contract_review("sample", 2, "Platform", "d" * 64, review_packet())

    with pytest.raises(StateError, match="worklist exceeds bound"):
        store.contract_review_worklist(limit=1)


def test_contract_review_worklist_name_seek_uses_bounded_indexed_discovery(store):
    first_name = "contract-review/" + "a" * 64
    next_name = "contract-review/" + "b" * 64
    store.db.executemany(
        "INSERT INTO decisions(name,version,body) VALUES(?,?,?)",
        [(first_name, version, "{}") for version in range(1, 1001)] + [(next_name, 1, "{}")],
    )
    statements = []
    store.db.set_trace_callback(statements.append)
    try:
        with pytest.raises(StateError, match="worklist exceeds bound"):
            store.contract_review_worklist(limit=1)
    finally:
        store.db.set_trace_callback(None)

    discovery = [sql for sql in statements if "FROM decisions" in sql and "contract-review0" in sql]
    assert len(discovery) == 2
    assert "name>='contract-review/'" in discovery[0]
    assert "name>'contract-review/" in discovery[1]


def test_contract_review_worklist_keeps_bare_namespace_prefix_fail_closed(store):
    store.db.execute(
        "INSERT INTO decisions(name,version,body) VALUES(?,?,?)",
        ("contract-review/", 1, "{}"),
    )

    with pytest.raises(StateError, match="worklist identity invalid"):
        store.contract_review_worklist()


def test_contract_review_worklist_does_not_hydrate_oversized_journal_names(store):
    name = "contract-review/" + "a" * 64 + "x" * (256 * 1024)
    store.db.execute("INSERT INTO decisions(name,version,body) VALUES(?,?,?)", (name, 1, "{}"))

    row = store.db.execute(
        _CONTRACT_REVIEW_GLOBAL_REPLAY_FIRST_QUERY, ("contract-review/",)
    ).fetchone()
    assert row["journal_name"] is None
    assert row["journal_name_length"] == len(name)
    with pytest.raises(StateError, match="worklist identity invalid"):
        store.contract_review_worklist(limit=1)


def test_contract_review_worklist_does_not_hydrate_names_with_embedded_nul(store):
    name = "contract-review/" + "a" * 64 + "\0" + "x" * (256 * 1024)
    store.db.execute("INSERT INTO decisions(name,version,body) VALUES(?,?,?)", (name, 1, "{}"))

    row = store.db.execute(
        _CONTRACT_REVIEW_GLOBAL_REPLAY_FIRST_QUERY, ("contract-review/",)
    ).fetchone()
    assert row["journal_name"] is None
    with pytest.raises(StateError, match="worklist identity invalid"):
        store.contract_review_worklist(limit=1)


def test_default_contract_review_record_rejects_oversized_history_before_body_hydration(store):
    request_id = "a" * 64
    name = "contract-review/" + request_id
    store.db.executemany(
        "INSERT INTO decisions(name,version,body) VALUES(?,?,?)",
        [(name, version, "{}") for version in range(1, 1001)],
    )
    traced = []
    store.db.set_trace_callback(traced.append)

    try:
        with patch("engineering.org_state.json.loads", side_effect=AssertionError("body hydrated")):
            with pytest.raises(StateError, match="contract review history invalid"):
                store._contract_review_record(request_id)
    finally:
        store.db.set_trace_callback(None)

    assert any("ORDER BY version LIMIT 6" in statement for statement in traced)


def test_contract_review_dispatch_does_not_hydrate_oversized_body(store):
    request_id = "b" * 64
    name = "contract-review/" + request_id
    store.db.execute(
        "INSERT INTO decisions(name,version,body) VALUES(?,?,?)",
        (name, 1, "x" * (512 * 1024 + 1)),
    )

    with patch("engineering.org_state.json.loads", side_effect=AssertionError("body hydrated")):
        with pytest.raises(StateError, match="replay history byte limit exceeded"):
            store.begin_contract_review_dispatch(request_id, "0" * 64)


def test_bounded_contract_review_rejects_oversized_replaces_before_hydration(store):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.db.execute(
        "UPDATE decisions SET replaces=? WHERE name=? AND version=2",
        ("r" * (256 * 1024), request["name"]),
    )

    with patch("engineering.org_state.json.loads", side_effect=AssertionError("body hydrated")):
        with pytest.raises(StateError, match="contract review history invalid"):
            store._contract_review_record(body["request_id"], bounded_replay=True)


def test_bounded_contract_review_suppresses_bodies_on_aggregate_overflow(store):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-aggregate-overflow",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-aggregate-overflow"},
    )
    response = record_review_result(store, body, review_result())
    store.finish_contract_review(
        body["request_id"], body["packet_hash"], response["body"]["raw_sha256"], review_result()
    )
    with store._tx():
        rows = store.db.execute(
            "SELECT version,body FROM decisions WHERE name=? ORDER BY version", (request["name"],)
        ).fetchall()
        for row in rows:
            oversized = json.loads(row["body"])
            oversized["legacy_extra"] = "x" * 430000
            store.db.execute(
                "UPDATE decisions SET body=? WHERE name=? AND version=?",
                (json.dumps(oversized), request["name"], row["version"]),
            )

    rows = store.db.execute(
        _CONTRACT_REVIEW_BOUNDED_RECORD_QUERY,
        (request["name"], request["name"], request["name"]),
    ).fetchall()
    assert rows[0]["bounded_history_bytes"] > org_state._CONTRACT_REVIEW_REPLAY_REQUEST_MAX_BYTES
    assert all(row["bounded_body"] is None for row in rows)
    assert all(
        row["body_bytes"] <= org_state._CONTRACT_REVIEW_REPLAY_VERSION_MAX_BYTES for row in rows
    )

    with patch("engineering.org_state.json.loads", side_effect=AssertionError("body hydrated")):
        with pytest.raises(StateError, match="replay request byte limit exceeded"):
            store._contract_review_record(body["request_id"], bounded_replay=True)


def test_contract_review_session_reserves_response_and_finalization_space(store):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])

    session = store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        review_session_thread_id_at_size(
            store, request, org_state._CONTRACT_REVIEW_THREAD_KNOWN_VERSION_MAX_BYTES
        ),
        "model",
        body["dispatch_id"],
        {"pid": 123, "birth": "birth"},
    )
    assert session["body"]["status"] == "thread_known"
    assert (
        len(org_state._json(session["body"]).encode("utf-8"))
        == org_state._CONTRACT_REVIEW_THREAD_KNOWN_VERSION_MAX_BYTES
    )

    result = review_result_at_serialized_size(32768)
    raw_result = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    raw_result += b" " * (65536 - len(raw_result))
    assert len(raw_result) == 65536
    response = store.record_contract_review_response(
        body["request_id"],
        body["packet_hash"],
        body["dispatch_id"],
        "😀" * 256,
        raw_result,
    )
    assert len(response["body"]["provider_turn_id"]) == 256
    assert (
        len(org_state._json(response["body"]).encode("utf-8"))
        <= org_state._CONTRACT_REVIEW_REPLAY_VERSION_MAX_BYTES
    )
    finished = store.finish_contract_review(
        body["request_id"],
        body["packet_hash"],
        response["body"]["raw_sha256"],
        result,
    )

    rows = store.db.execute(
        "SELECT version,length(CAST(body AS BLOB)) AS body_bytes "
        "FROM decisions WHERE name=? ORDER BY version",
        (request["name"],),
    ).fetchall()
    assert len(rows) == 5
    assert all(
        row["body_bytes"] <= org_state._CONTRACT_REVIEW_REPLAY_VERSION_MAX_BYTES for row in rows
    )
    assert (
        sum(row["body_bytes"] for row in rows)
        <= org_state._CONTRACT_REVIEW_REPLAY_REQUEST_MAX_BYTES
    )
    assert finished["body"]["status"] == "approved"


def test_contract_review_session_write_cannot_consume_response_headroom(store):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])

    events_before = store.events()
    with pytest.raises(StateError, match="session leaves insufficient response space"):
        store.record_contract_review_session(
            body["request_id"],
            body["packet_hash"],
            review_session_thread_id_at_size(
                store, request, org_state._CONTRACT_REVIEW_THREAD_KNOWN_VERSION_MAX_BYTES + 1
            ),
            "model",
            body["dispatch_id"],
            {"pid": 123, "birth": "birth"},
        )

    current = store._contract_review_record(body["request_id"])
    assert current["body"]["status"] == "dispatching"
    assert current["version"] == 2
    assert store.events() == events_before


def test_oversized_legacy_thread_known_retry_fails_closed_without_mutation(store):
    request = prepare_review(store)
    body = request["body"]
    dispatching = store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    session = {
        "thread_id": "x" * 400_000,
        "model": "model",
        "turn_dispatch_id": body["dispatch_id"],
        "process_identity": {"pid": 123, "birth": "birth"},
    }
    legacy = {**dispatching["body"], "status": "thread_known", "session": session}
    legacy_bytes = len(org_state._json(legacy).encode("utf-8"))
    assert (
        org_state._CONTRACT_REVIEW_THREAD_KNOWN_VERSION_MAX_BYTES
        < legacy_bytes
        < org_state._CONTRACT_REVIEW_REPLAY_VERSION_MAX_BYTES
    )
    with store._tx():
        store._put_decision_in_transaction(request["name"], legacy)
    events_before = store.events()

    with pytest.raises(StateError, match="session leaves insufficient response space"):
        store.record_contract_review_session(
            body["request_id"],
            body["packet_hash"],
            session["thread_id"],
            session["model"],
            session["turn_dispatch_id"],
            session["process_identity"],
        )

    current = store._contract_review_record(body["request_id"])
    assert current["version"] == 3
    assert current["body"] == legacy
    assert store.events() == events_before

    result = review_result()
    finished = finish_recorded_review(store, body, result)
    retained = store.get_contract_review_response(body["request_id"], _bounded_replay=True)
    assert finished["body"]["status"] == "approved"
    assert retained["raw_result"] == json.dumps(
        result, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def test_contract_review_worklist_does_not_hydrate_malformed_utf8_names(store):
    raw_name = b"contract-review/" + b"a" * 62 + b"\xc2" + b"\x80" * (256 * 1024)
    store.db.execute(
        "INSERT INTO decisions(name,version,body) VALUES(CAST(? AS TEXT),?,?)",
        (sqlite3.Binary(raw_name), 1, "{}"),
    )

    row = store.db.execute(
        _CONTRACT_REVIEW_GLOBAL_REPLAY_FIRST_QUERY, ("contract-review/",)
    ).fetchone()
    assert row["journal_name"] is None
    assert row["journal_name_storage_bytes"] == len(raw_name)
    with pytest.raises(StateError, match="worklist identity invalid"):
        store.contract_review_worklist(limit=1)


def test_contract_review_worklist_accepts_utf16_database_encoding(tmp_path):
    path = Path(tmp_path) / "utf16.sqlite"
    db = sqlite3.connect(path)
    db.execute("PRAGMA encoding='UTF-16le'")
    db.execute("CREATE TABLE seed(value)")
    db.commit()
    db.close()
    utf16_store = Store(path, clock=lambda: 1000.0)
    try:
        utf16_store.put_contract("sample", 1, {"schema": "v1"})
        request = prepare_review(utf16_store)
        record_pending_review_response(utf16_store, request["body"], suffix="utf16-database")
        assert utf16_store.db.execute("PRAGMA encoding").fetchone()[0] == "UTF-16le"
        default_record = utf16_store._contract_review_record(request["body"]["request_id"])
        bounded_record = utf16_store._contract_review_record(
            request["body"]["request_id"], bounded_replay=True
        )
        assert default_record["body"]["status"] == "response_recorded"
        assert bounded_record["body"]["status"] == "response_recorded"
        projection = utf16_store.contract_review_worklist()
        assert projection["count"] == 1
        assert projection["items"][0]["request_id"] == request["body"]["request_id"]
        assert projection["items"][0]["status"] == "response_recorded"
    finally:
        utf16_store.close()


def test_contract_review_readers_reject_blob_typed_replaces_link(store):
    request = prepare_review(store)
    store.begin_contract_review_dispatch(
        request["body"]["request_id"], request["body"]["packet_hash"]
    )
    store.db.execute(
        "UPDATE decisions SET replaces=? WHERE name=? AND version=2",
        (sqlite3.Binary(request["name"].encode("utf-8")), request["name"]),
    )

    for bounded_replay in (False, True):
        with pytest.raises(StateError, match="contract review history invalid"):
            store._contract_review_record(
                request["body"]["request_id"], bounded_replay=bounded_replay
            )


def test_transition_replay_does_not_hydrate_oversized_journal_names(store):
    request = prepare_review(store)
    body = request["body"]
    record_pending_review_response(store, body, suffix="oversized-journal-name")
    oversized_name = request["name"] + "x" * (256 * 1024)
    store.db.execute("UPDATE decisions SET name=? WHERE name=?", (oversized_name, request["name"]))

    with pytest.raises(StateError, match="worklist identity invalid"):
        store.replay_contract_review_responses_for_transition(
            body["name"], body["version"], body["contract_hash"]
        )
    assert (
        store.get_decision(store._contract_ack_key(body["name"], body["version"], body["team"]))
        is None
    )


def test_contract_review_worklist_preflights_oversized_legacy_before_hydration(store, monkeypatch):
    request = prepare_review(store)
    body = {**request["body"], "legacy_extra": "x" * 1000}
    store.db.execute(
        "UPDATE decisions SET body=? WHERE name=?",
        (json.dumps(body), request["name"]),
    )
    events_before = len(store.events())
    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_REPLAY_VERSION_MAX_BYTES", 100)

    with monkeypatch.context() as scoped:
        scoped.setattr(
            store,
            "_contract_review_record",
            lambda *_args, **_kwargs: pytest.fail("worklist hydrated before byte preflight"),
        )
        with pytest.raises(StateError, match="worklist byte limit exceeded"):
            store.contract_review_worklist()
        overflow = store.quiescence_snapshot()["review_requests"]

    assert overflow == {
        "items": [],
        "count": None,
        "limit": 100,
        "dispatch_authority": False,
        "overflow": True,
    }
    assert store._contract_review_record(body["request_id"])["body"]["legacy_extra"] == "x" * 1000
    assert len(store.events()) == events_before
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


def test_contract_review_worklist_preflight_and_hydration_share_snapshot(
    store, tmp_path, monkeypatch
):
    request = prepare_review(store)
    body = request["body"]
    current_size = store.db.execute(
        "SELECT length(CAST(body AS BLOB)) FROM decisions WHERE name=? AND version=1",
        (request["name"],),
    ).fetchone()[0]
    byte_limit = current_size + 100
    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_REPLAY_VERSION_MAX_BYTES", byte_limit)
    other = sqlite3.connect(str(Path(tmp_path) / "org.sqlite"), timeout=30, isolation_level=None)
    preflight_started = threading.Event()
    continue_preflight = threading.Event()
    paused = False
    hydrated = []
    read_review = store._contract_review_record

    def capture_hydrated_review(request_id, *args, **kwargs):
        result = read_review(request_id, *args, **kwargs)
        hydrated.append(result)
        return result

    monkeypatch.setattr(store, "_contract_review_record", capture_hydrated_review)

    def pause_before_preflight(statement):
        nonlocal paused
        if not paused and statement.strip().upper().startswith(
            "SELECT VERSION,LENGTH(CAST(BODY AS BLOB)) AS BODY_BYTES FROM DECISIONS"
        ):
            paused = True
            preflight_started.set()
            continue_preflight.wait()

    store.db.set_trace_callback(pause_before_preflight)
    pool = ThreadPoolExecutor(1)
    try:
        future = pool.submit(store.contract_review_worklist)
        try:
            assert preflight_started.wait(2)
            with pytest.raises(FutureTimeout):
                future.result(timeout=0.05)
            other.execute("BEGIN IMMEDIATE")
            changed = json.loads(
                other.execute(
                    "SELECT body FROM decisions WHERE name=? AND version=1", (request["name"],)
                ).fetchone()[0]
            )
            changed["legacy_extra"] = "x" * (600 * 1024)
            other.execute(
                "UPDATE decisions SET body=? WHERE name=? AND version=1",
                (json.dumps(changed), request["name"]),
            )
            other.execute("COMMIT")
        finally:
            continue_preflight.set()
        projection = future.result(timeout=2)
        assert len(hydrated) == 1
        assert "legacy_extra" not in hydrated[0]["body"]
        fresh = store._contract_review_record(body["request_id"])
        assert projection["count"] == 1
        assert projection["items"][0]["status"] == "prepared"
        assert fresh["body"]["legacy_extra"] == "x" * (600 * 1024)
        assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None
    finally:
        continue_preflight.set()
        pool.shutdown(wait=True)
        store.db.set_trace_callback(None)
        if other.in_transaction:
            other.execute("ROLLBACK")
        other.close()


def test_contract_review_worklist_aggregate_byte_budget_exact_and_plus_one(store, monkeypatch):
    first = prepare_review(store)
    second = store.prepare_contract_review("sample", 2, "Platform", "d" * 64, review_packet())
    names = (first["name"], second["name"])
    total = sum(
        store.db.execute(
            "SELECT SUM(length(CAST(body AS BLOB))) FROM decisions WHERE name=?", (name,)
        ).fetchone()[0]
        for name in names
    )
    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_WORKLIST_MAX_BYTES", total)

    exact = store.contract_review_worklist()
    assert exact["count"] == 2

    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_WORKLIST_MAX_BYTES", total - 1)
    with pytest.raises(StateError, match="worklist byte limit exceeded"):
        store.contract_review_worklist()
    with monkeypatch.context() as scoped:
        scoped.setattr(
            store,
            "_contract_review_record",
            lambda *_args, **_kwargs: pytest.fail("worklist hydrated before aggregate preflight"),
        )
        overflow = store.quiescence_snapshot()["review_requests"]
    assert overflow["overflow"] is True
    assert overflow["items"] == []
    assert overflow["count"] is None


def test_contract_review_stale_inventory_blocks_dispatch_without_mutation(store):
    request = prepare_review(store)
    body = request["body"]
    store.add_task("PLAT-2", "Platform", "new consumer", scope=["other.py"])
    currentness = store.contract_review_currentness(body["request_id"], body["packet_hash"])
    events_before = len(store.events())

    assert currentness["current"] is False
    assert currentness["blockers"] == ["participant_inventory_changed"]
    with pytest.raises(StateError, match="participant_inventory_changed"):
        store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    assert store.get_decision(request["name"])["version"] == 1
    assert store.get_decision(request["name"])["body"]["status"] == "prepared"
    assert len(store.events()) == events_before
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


def test_contract_review_stale_producer_snapshot_blocks_dispatch(store):
    request = prepare_review(store)
    body = request["body"]
    store.claim("EXP-1", "synthetic")
    currentness = store.contract_review_currentness(body["request_id"], body["packet_hash"])
    events_before = len(store.events())

    assert currentness["current"] is False
    assert currentness["blockers"] == ["producer_snapshot_changed"]
    with pytest.raises(StateError, match="producer_snapshot_changed"):
        store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    assert store.get_decision(request["name"])["version"] == 1
    assert len(store.events()) == events_before


@pytest.mark.parametrize(
    ("snapshot", "expected_blocker"),
    [
        ("source", "source_contract_changed"),
        ("producer", "producer_snapshot_changed"),
        ("participant", "participant_inventory_changed"),
        ("participant_nonfinite", "participant_inventory_changed"),
    ],
)
def test_contract_review_type_only_json_drift_blocks_dispatch(store, snapshot, expected_blocker):
    if snapshot == "source":
        old_body = {"schema": "v1", "flag": 1}
        store.db.execute(
            "UPDATE contracts SET body=? WHERE name='sample' AND version=1",
            (org_state._json(old_body),),
        )
        request = prepare_review(store)
        new_body = {"schema": "v1", "flag": True}
        store.db.execute(
            "UPDATE contracts SET body=? WHERE name='sample' AND version=1",
            (org_state._json(new_body),),
        )
    elif snapshot == "producer":
        request = prepare_review(store, producer_contracts={"marker": 1})
        store.db.execute(
            "UPDATE tasks SET contracts=? WHERE id='EXP-1'",
            (org_state._json({"marker": True}),),
        )
    else:
        request = prepare_review(store, consumer_contracts={"sample": 1})
        replacement = (
            '{"sample":NaN}'
            if snapshot == "participant_nonfinite"
            else org_state._json({"sample": True})
        )
        store.db.execute(
            "UPDATE tasks SET contracts=? WHERE id='PLAT-1'",
            (replacement,),
        )
    body = request["body"]
    events_before = len(store.events())

    freshness = store.contract_review_currentness(body["request_id"], body["packet_hash"])
    assert freshness["current"] is False
    assert freshness["blockers"] == [expected_blocker]
    with pytest.raises(StateError, match=expected_blocker):
        store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    assert store.get_decision(request["name"])["version"] == 1
    assert len(store.events()) == events_before
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


def test_contract_review_canonical_comparison_preserves_key_order_equivalence(store):
    old_body = {"schema": "v1", "flag": 1}
    store.db.execute(
        "UPDATE contracts SET body=? WHERE name='sample' AND version=1",
        (org_state._json(old_body),),
    )
    request = prepare_review(store)
    reordered_body = '{ "flag" : 1, "schema" : "v1" }'
    store.db.execute(
        "UPDATE contracts SET body=? WHERE name='sample' AND version=1", (reordered_body,)
    )
    body = request["body"]
    assert store.contract_review_currentness(body["request_id"], body["packet_hash"])["current"]


def test_contract_review_existing_ack_blocks_dispatch(store):
    request = prepare_review(store)
    body = request["body"]
    store.acknowledge_contract_transition("sample", 2, "Platform", body["contract_hash"])
    currentness = store.contract_review_currentness(body["request_id"], body["packet_hash"])
    events_before = len(store.events())

    assert currentness["current"] is False
    assert currentness["blockers"] == ["participant_already_acknowledged"]
    with pytest.raises(StateError, match="participant_already_acknowledged"):
        store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    assert store.get_decision(request["name"])["version"] == 1
    assert len(store.events()) == events_before


def test_contract_review_begin_rechecks_inventory_after_lock_wait(store, tmp_path):
    request = prepare_review(store, consumer_contracts={"sample": 1})
    body = request["body"]
    assert store.contract_review_currentness(body["request_id"], body["packet_hash"])["current"]
    other = sqlite3.connect(str(Path(tmp_path) / "org.sqlite"), timeout=30, isolation_level=None)
    waiting_for_write_lock = threading.Event()
    store.db.set_trace_callback(
        lambda statement: (
            waiting_for_write_lock.set() if statement.strip().upper() == "BEGIN IMMEDIATE" else None
        )
    )
    other.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(
                store.begin_contract_review_dispatch, body["request_id"], body["packet_hash"]
            )
            try:
                assert waiting_for_write_lock.wait(2)
                with pytest.raises(FutureTimeout):
                    future.result(timeout=0.05)
                other.execute(
                    "UPDATE tasks SET contracts=? WHERE id=?",
                    (org_state._json({"sample": True}), "PLAT-1"),
                )
                other.execute("COMMIT")
                with pytest.raises(StateError, match="participant_inventory_changed"):
                    future.result(timeout=2)
            finally:
                if other.in_transaction:
                    other.execute("ROLLBACK")
                if not future.done():
                    future.result(timeout=2)
        assert store.get_decision(request["name"])["version"] == 1
        assert store.get_decision(request["name"])["body"]["status"] == "prepared"
        assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None
    finally:
        store.db.set_trace_callback(None)
        if other.in_transaction:
            other.execute("ROLLBACK")
        other.close()


def test_contract_review_raw_result_is_replayed_after_store_reopen(store, tmp_path):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    result = review_result()
    stored = record_review_result(store, body, result)
    store.close()

    recovered = Store(tmp_path / "org.sqlite", clock=lambda: 1000.0)
    response = recovered.get_contract_review_response(body["request_id"])
    assert response["raw_sha256"] == stored["body"]["raw_sha256"]
    assert json.loads(response["raw_result"]) == result
    with pytest.raises(StateError, match="response is stale"):
        recovered.replay_contract_review_response(body["request_id"], "0" * 64)
    assert recovered.get_decision(request["name"])["body"]["status"] == "response_recorded"
    assert recovered.get_decision(recovered._contract_ack_key("sample", 2, "Platform")) is None
    finished = recovered.replay_contract_review_response(body["request_id"], body["packet_hash"])
    assert finished["body"]["status"] == "approved"
    assert recovered.get_decision(recovered._contract_ack_key("sample", 2, "Platform"))
    assert (
        recovered.replay_contract_review_response(body["request_id"], body["packet_hash"])
        == finished
    )
    recovered.close()


def test_contract_review_replay_rechecks_freshness_after_lock_wait(store, tmp_path):
    request = prepare_review(store, consumer_contracts={"sample": 1})
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    stored = record_review_result(store, body, review_result())

    other = sqlite3.connect(str(Path(tmp_path) / "org.sqlite"), timeout=30, isolation_level=None)
    waiting_for_write_lock = threading.Event()
    store.db.set_trace_callback(
        lambda statement: (
            waiting_for_write_lock.set() if statement.strip().upper() == "BEGIN IMMEDIATE" else None
        )
    )
    other.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(
                store.replay_contract_review_response,
                body["request_id"],
                body["packet_hash"],
            )
            try:
                assert waiting_for_write_lock.wait(2)
                with pytest.raises(FutureTimeout):
                    future.result(timeout=0.05)
                other.execute(
                    "UPDATE tasks SET contracts=? WHERE id=?",
                    (org_state._json({"sample": True}), "PLAT-1"),
                )
                other.execute("COMMIT")
                finished = future.result(timeout=2)
            finally:
                if other.in_transaction:
                    other.execute("ROLLBACK")
                if not future.done():
                    future.result(timeout=2)
        assert finished["body"]["status"] == "blocked"
        assert any("inventory changed" in item for item in finished["body"]["blocking_reasons"])
        assert finished["body"]["raw_sha256"] == stored["body"]["raw_sha256"]
        assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None
    finally:
        store.db.set_trace_callback(None)
        if other.in_transaction:
            other.execute("ROLLBACK")
        other.close()


def test_contract_review_replay_sweep_preserves_per_request_failures(store):
    valid = prepare_review(store)
    malformed = store.prepare_contract_review("sample", 2, "Platform", "d" * 64, review_packet())
    prepared = store.prepare_contract_review("sample", 2, "Platform", "e" * 64, review_packet())
    valid_body = valid["body"]
    malformed_body = malformed["body"]

    valid_result = review_result()
    for body, raw_result in (
        (
            valid_body,
            json.dumps(valid_result, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        ),
        (malformed_body, b'{"decision":"approve","decision":"blocked"}'),
    ):
        store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
        store.record_contract_review_session(
            body["request_id"],
            body["packet_hash"],
            "thread-1",
            "gpt-6.1-sol",
            body["dispatch_id"],
            {"pid": 123, "birth": "synthetic-birth"},
        )
        store.record_contract_review_response(
            body["request_id"],
            body["packet_hash"],
            body["dispatch_id"],
            "provider-turn-" + body["request_id"],
            raw_result,
        )

    malformed_response = store.get_contract_review_response(malformed_body["request_id"])
    swept = store.replay_pending_contract_review_responses()
    assert swept["overflow"] is False
    assert swept["count"] == 2
    assert swept["response_recorded_count"] == 2
    assert swept["skipped_count"] == 1
    assert swept["dispatch_authority"] is False
    assert swept["provider_dispatch"] is False
    assert [item["request_id"] for item in swept["replayed"]] == [valid_body["request_id"]]
    assert swept["replayed"][0]["status"] == "approved"
    assert swept["failures"] == [
        {
            "request_id": malformed_body["request_id"],
            "packet_hash": malformed_body["packet_hash"],
            "error": "contract review response is not replayable",
            "error_truncated": False,
        }
    ]
    assert store.get_decision(valid["name"])["body"]["status"] == "approved"
    assert store.get_decision(malformed["name"])["body"]["status"] == "response_recorded"
    assert store.get_contract_review_response(malformed_body["request_id"]) == malformed_response
    assert store.get_decision(prepared["name"])["body"]["status"] == "prepared"
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform"))

    repeated = store.replay_pending_contract_review_responses()
    assert repeated["response_recorded_count"] == 1
    assert repeated["replayed"] == []
    assert repeated["failures"] == swept["failures"]


def record_pending_review_response(store, body, *, suffix, result=None, raw_result=None):
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-" + suffix,
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth-" + suffix},
    )
    record_review_result(
        store,
        body,
        review_result() if result is None else result,
        raw_result=raw_result,
    )


def test_configured_activation_replays_saved_approval_without_provider_or_resend(store, tmp_path):
    integrate_producer(store)
    store.add_task(
        "PLAT-1",
        "Platform",
        "consume v2 contract",
        scope=["cli.py"],
        contracts={"sample": 2},
    )
    proposal = {
        "schema": "v2",
        "version": 2,
        "producer": "Experience",
        "producer_task_id": "EXP-1",
        "consumers": ["Platform"],
    }
    transition = store.propose_contract_transition(
        "sample",
        1,
        2,
        proposal,
        producer_task_id="EXP-1",
        producer_team="Experience",
        consumer_teams=["Platform"],
    )
    request = store.prepare_contract_review("sample", 2, "Platform", "c" * 64, review_packet())
    body = request["body"]
    record_pending_review_response(store, body, suffix="activate")
    store.acknowledge_contract_transition(
        "sample", 2, "Experience", transition["body"]["contract_hash"]
    )
    assert [item["task_id"] for item in store.readiness_report() if item["eligible"]] == []

    cfg = {"contracts": {"sample": proposal}}
    with (
        patch.object(supervisor, "config", return_value=cfg),
        patch.object(supervisor, "log_event"),
        patch.object(supervisor, "Codex", side_effect=AssertionError("provider must not start")),
        patch.object(
            supervisor,
            "metered_client",
            side_effect=AssertionError("provider must not start"),
        ),
    ):
        supervisor.activate_contract(tmp_path, store)

    assert store.get_decision(request["name"])["body"]["status"] == "approved"
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is not None
    assert store.get_contract("sample")["version"] == 2
    assert [item["task_id"] for item in store.readiness_report() if item["eligible"]] == ["PLAT-1"]


def test_transition_scoped_replay_overflow_does_not_starve_another_transition(store):
    first = prepare_review(store)
    first_body = first["body"]
    record_pending_review_response(store, first_body, suffix="sample-1")
    second = store.prepare_contract_review("sample", 2, "Platform", "d" * 64, review_packet())
    second_body = second["body"]
    record_pending_review_response(store, second_body, suffix="sample-2")

    store.add_task("EXP-2", "Experience", "produce other contract", scope=["other.py"])
    store.put_contract("other", 1, {"schema": "other-v1"})
    other = store.propose_contract_transition(
        "other",
        1,
        2,
        {"schema": "other-v2"},
        producer_task_id="EXP-2",
        producer_team="Experience",
        consumer_teams=["Platform"],
    )
    other_request = store.prepare_contract_review("other", 2, "Platform", "e" * 64, review_packet())
    record_pending_review_response(store, other_request["body"], suffix="other")

    overflow = store.replay_contract_review_responses_for_transition(
        "sample", 2, first_body["contract_hash"], limit=1
    )
    assert overflow["overflow"] is True
    assert overflow["replayed"] == []
    assert store.get_decision(first["name"])["body"]["status"] == "response_recorded"
    assert store.get_decision(second["name"])["body"]["status"] == "response_recorded"
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None

    replayed_other = store.replay_contract_review_responses_for_transition(
        "other", 2, other["body"]["contract_hash"], limit=1
    )
    assert replayed_other["overflow"] is False
    assert [item["request_id"] for item in replayed_other["replayed"]] == [
        other_request["body"]["request_id"]
    ]
    assert store.get_decision(store._contract_ack_key("other", 2, "Platform")) is not None


def test_transition_replay_scan_limit_counts_non_candidate_journals(store, monkeypatch):
    first = prepare_review(store)
    second = store.prepare_contract_review("sample", 2, "Platform", "d" * 64, review_packet())
    record_pending_review_response(store, first["body"], suffix="scan-limit")
    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_REPLAY_SCAN_LIMIT", 1)

    result = store.replay_contract_review_responses_for_transition(
        "sample", 2, first["body"]["contract_hash"], limit=100
    )

    assert result["overflow"] is True
    assert result["replayed"] == []
    assert store.get_decision(first["name"])["body"]["status"] == "response_recorded"
    assert store.get_decision(second["name"])["body"]["status"] == "prepared"
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


def test_global_replay_scan_limit_counts_terminal_only_journals(store, monkeypatch):
    first = prepare_review(store)
    store.prepare_contract_review("sample", 2, "Platform", "d" * 64, review_packet())
    record_pending_review_response(store, first["body"], suffix="global-scan-limit")
    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_REPLAY_SCAN_LIMIT", 1)

    result = store.replay_pending_contract_review_responses(limit=100)

    assert result["overflow"] is True
    assert result["replayed"] == []
    assert store.get_decision(first["name"])["body"]["status"] == "response_recorded"
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


def test_review_replay_history_byte_limit_exact_and_plus_one(store, monkeypatch):
    request = prepare_review(store)
    size = store.db.execute(
        "SELECT length(CAST(body AS BLOB)) FROM decisions WHERE name=?",
        (request["name"],),
    ).fetchone()[0]
    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_REPLAY_VERSION_MAX_BYTES", size)

    assert store._contract_review_replay_history_size(request["body"]["request_id"]) == size
    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_REPLAY_VERSION_MAX_BYTES", size - 1)
    with pytest.raises(StateError, match="byte limit exceeded"):
        store._contract_review_replay_history_size(request["body"]["request_id"])


def test_replay_inventory_byte_budget_exact_and_plus_one_prevents_partial_ack(store, monkeypatch):
    first = prepare_review(store)
    second = store.prepare_contract_review("sample", 2, "Platform", "d" * 64, review_packet())
    record_pending_review_response(store, first["body"], suffix="aggregate-byte-budget")
    names = (first["name"], second["name"])
    byte_total = sum(
        store.db.execute(
            "SELECT SUM(length(CAST(body AS BLOB))) FROM decisions WHERE name=?", (name,)
        ).fetchone()[0]
        for name in names
    )
    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_REPLAY_INVENTORY_MAX_BYTES", byte_total)

    with store._read_snapshot():
        exact = store._contract_review_transition_replay_inventory_in_snapshot(
            name="sample",
            version=2,
            contract_hash=first["body"]["contract_hash"],
            limit=100,
        )
    assert exact["overflow"] is False
    assert exact["items"] == [
        {"request_id": first["body"]["request_id"], "packet_hash": first["body"]["packet_hash"]}
    ]

    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_REPLAY_INVENTORY_MAX_BYTES", byte_total - 1)
    overflow = store.replay_contract_review_responses_for_transition(
        "sample", 2, first["body"]["contract_hash"], limit=100
    )
    assert overflow["overflow"] is True
    assert overflow["replayed"] == []
    assert store.get_decision(first["name"])["body"]["status"] == "response_recorded"
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


def test_replay_request_byte_budget_exact_and_plus_one(store, monkeypatch):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-request-budget",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-request-budget"},
    )
    response = record_review_result(store, body, review_result())
    store.finish_contract_review(
        body["request_id"],
        body["packet_hash"],
        response["body"]["raw_sha256"],
        review_result(),
    )
    rows = store.db.execute(
        "SELECT version,body FROM decisions WHERE name=? ORDER BY version", (request["name"],)
    ).fetchall()
    with store._tx():
        for row in rows:
            mutated = json.loads(row["body"])
            mutated["legacy_extra"] = "x" * 420000
            store.db.execute(
                "UPDATE decisions SET body=? WHERE name=? AND version=?",
                (json.dumps(mutated), request["name"], row["version"]),
            )
    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_REPLAY_VERSION_MAX_BYTES", 450 * 1024)
    sizes = store.db.execute(
        "SELECT length(CAST(body AS BLOB)) FROM decisions WHERE name=? ORDER BY version",
        (request["name"],),
    ).fetchall()
    total = sum(row[0] for row in sizes)
    assert max(row[0] for row in sizes) <= 450 * 1024
    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_REPLAY_REQUEST_MAX_BYTES", total)

    assert store._contract_review_replay_history_size(body["request_id"]) == total
    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_REPLAY_REQUEST_MAX_BYTES", total - 1)
    with pytest.raises(StateError, match="request byte limit exceeded"):
        store._contract_review_replay_history_size(body["request_id"])


def test_replay_history_rejects_sixth_version_before_body_hydration(store):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-six-version",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-six-version"},
    )
    response = record_review_result(store, body, review_result())
    store.finish_contract_review(
        body["request_id"],
        body["packet_hash"],
        response["body"]["raw_sha256"],
        review_result(),
    )
    key = request["name"]
    store.db.execute(
        "INSERT INTO decisions(name,version,body,replaces,replaces_version) "
        "SELECT name,6,body,name,5 FROM decisions WHERE name=? AND version=5",
        (key,),
    )

    with pytest.raises(StateError, match="history invalid"):
        store._contract_review_replay_history_size(body["request_id"])


def test_oversized_legacy_review_remains_readable_but_replay_fails_closed(store, monkeypatch):
    request = prepare_review(store)
    body = request["body"]
    record_pending_review_response(store, body, suffix="oversized-legacy")
    current = store.get_decision(request["name"])
    oversized = {**current["body"], "legacy_extra": "x" * 1000}
    store.db.execute(
        "UPDATE decisions SET body=? WHERE name=? AND version=?",
        (json.dumps(oversized), request["name"], current["version"]),
    )
    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_REPLAY_VERSION_MAX_BYTES", 100)

    assert store._contract_review_record(body["request_id"])["body"]["legacy_extra"] == "x" * 1000
    assert (
        store.get_contract_review_response(body["request_id"])["raw_sha256"]
        == current["body"]["raw_sha256"]
    )
    with pytest.raises(StateError, match="replay history byte limit exceeded"):
        store.get_contract_review_response(body["request_id"], _bounded_replay=True)
    result = store.replay_contract_review_responses_for_transition(
        "sample", 2, body["contract_hash"], limit=100
    )

    assert result["overflow"] is True
    assert result["replayed"] == []
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


def test_replay_byte_budget_is_rechecked_under_finalization_lock(store, tmp_path, monkeypatch):
    request = prepare_review(store)
    body = request["body"]
    record_pending_review_response(store, body, suffix="bounded-lock")
    current_bytes = store.db.execute(
        "SELECT MAX(length(CAST(body AS BLOB))) FROM decisions WHERE name=?",
        (request["name"],),
    ).fetchone()[0]
    byte_limit = current_bytes + 100
    monkeypatch.setattr(org_state, "_CONTRACT_REVIEW_REPLAY_VERSION_MAX_BYTES", byte_limit)
    finish = store.finish_contract_review
    mutation_executed = False

    def append_oversized_legacy_field(
        request_id, packet_hash, raw_sha256, result, *, _bounded_replay=False
    ):
        nonlocal mutation_executed
        mutation_executed = True
        with sqlite3.connect(tmp_path / "org.sqlite") as other:
            raw = other.execute(
                "SELECT body FROM decisions WHERE name=? ORDER BY version DESC LIMIT 1",
                (request["name"],),
            ).fetchone()[0]
            mutated = json.loads(raw)
            mutated["legacy_extra"] = "x" * (byte_limit + 1)
            other.execute(
                "UPDATE decisions SET body=? WHERE name=? AND version=(SELECT MAX(version) "
                "FROM decisions WHERE name=?)",
                (json.dumps(mutated), request["name"], request["name"]),
            )
        return finish(
            request_id,
            packet_hash,
            raw_sha256,
            result,
            _bounded_replay=_bounded_replay,
        )

    with store._read_snapshot():
        assert store._contract_review_record(body["request_id"], bounded_replay=True) is not None
    monkeypatch.setattr(store, "finish_contract_review", append_oversized_legacy_field)
    with pytest.raises(StateError, match="byte limit exceeded"):
        store.replay_contract_review_response(body["request_id"], body["packet_hash"])

    assert mutation_executed is True
    assert store.get_decision(request["name"])["body"]["status"] == "response_recorded"
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


def test_supervisor_transition_overflow_keeps_processing_other_configured_contracts(
    store, tmp_path
):
    integrate_producer(store, "EXP-1")
    integrate_producer(store, "EXP-2")
    store.add_task("PLAT-1", "Platform", "review both contract transitions", scope=["cli.py"])
    sample_proposal = {
        "schema": "v2",
        "version": 2,
        "producer": "Experience",
        "producer_task_id": "EXP-1",
        "consumers": ["Platform"],
    }
    other_proposal = {
        "schema": "other-v2",
        "version": 2,
        "producer": "Experience",
        "producer_task_id": "EXP-2",
        "consumers": ["Platform"],
    }
    store.put_contract("z-other", 1, {"schema": "other-v1"})
    transitions = {
        "sample": store.propose_contract_transition(
            "sample",
            1,
            2,
            sample_proposal,
            producer_task_id="EXP-1",
            producer_team="Experience",
            consumer_teams=["Platform"],
        ),
        "z-other": store.propose_contract_transition(
            "z-other",
            1,
            2,
            other_proposal,
            producer_task_id="EXP-2",
            producer_team="Experience",
            consumer_teams=["Platform"],
        ),
    }
    for name in ("sample", "z-other"):
        store.acknowledge_contract_transition(
            name, 2, "Experience", transitions[name]["body"]["contract_hash"]
        )
    sample_requests = []
    for index in range(101):
        request = store.prepare_contract_review(
            "sample", 2, "Platform", f"{index:064x}", review_packet()
        )
        sample_requests.append(request)
        record_pending_review_response(store, request["body"], suffix=f"sample-{index}")
    other_request = store.prepare_contract_review(
        "z-other", 2, "Platform", "f" * 64, review_packet()
    )
    record_pending_review_response(store, other_request["body"], suffix="other-transition")

    cfg = {"contracts": {"sample": sample_proposal, "z-other": other_proposal}}
    with (
        patch.object(supervisor, "config", return_value=cfg),
        patch.object(supervisor, "log_event") as log_event,
        patch.object(supervisor, "Codex", side_effect=AssertionError("provider must not start")),
        patch.object(
            supervisor,
            "metered_client",
            side_effect=AssertionError("provider must not start"),
        ),
    ):
        supervisor.activate_contract(tmp_path, store)

    assert store.get_contract("sample")["version"] == 1
    assert store.get_contract("z-other")["version"] == 2
    assert all(
        store.get_decision(request["name"])["body"]["status"] == "response_recorded"
        for request in sample_requests
    )
    assert store.get_decision(other_request["name"])["body"]["status"] == "approved"
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None
    assert store.get_decision(store._contract_ack_key("z-other", 2, "Platform")) is not None
    assert any(
        call.args[1] == "contract_review_replay_reconciled"
        and call.kwargs.get("name") == "sample"
        and call.kwargs.get("overflow") is True
        for call in log_event.call_args_list
    )


def test_supervisor_replay_integrity_error_does_not_skip_later_activation(store, tmp_path):
    integrate_producer(store, "EXP-1")
    integrate_producer(store, "EXP-2")
    sample_proposal = {
        "schema": "v2",
        "version": 2,
        "producer": "Experience",
        "producer_task_id": "EXP-1",
        "consumers": ["Platform"],
    }
    other_proposal = {
        "schema": "other-v2",
        "version": 2,
        "producer": "Experience",
        "producer_task_id": "EXP-2",
        "consumers": ["Platform"],
    }
    store.put_contract("z-other", 1, {"schema": "other-v1"})
    transitions = {
        "sample": store.propose_contract_transition(
            "sample",
            1,
            2,
            sample_proposal,
            producer_task_id="EXP-1",
            producer_team="Experience",
            consumer_teams=["Platform"],
        ),
        "z-other": store.propose_contract_transition(
            "z-other",
            1,
            2,
            other_proposal,
            producer_task_id="EXP-2",
            producer_team="Experience",
            consumer_teams=["Platform"],
        ),
    }
    for name in ("sample", "z-other"):
        store.acknowledge_contract_transition(
            name, 2, "Experience", transitions[name]["body"]["contract_hash"]
        )
    store.acknowledge_contract_transition(
        "z-other", 2, "Platform", transitions["z-other"]["body"]["contract_hash"]
    )
    store.db.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?)",
        ("contract-review/malformed-id", 1, "{}", None, None),
    )

    cfg = {"contracts": {"sample": sample_proposal, "z-other": other_proposal}}
    with (
        patch.object(supervisor, "config", return_value=cfg),
        patch.object(supervisor, "log_event") as log_event,
        patch.object(supervisor, "Codex", side_effect=AssertionError("provider must not start")),
        patch.object(
            supervisor,
            "metered_client",
            side_effect=AssertionError("provider must not start"),
        ),
    ):
        supervisor.activate_contract(tmp_path, store)

    assert store.get_contract("sample")["version"] == 1
    assert store.get_contract("z-other")["version"] == 2
    assert not any(
        call.args[1] == "contract_review_replay_failed" for call in log_event.call_args_list
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "malformed_json",
        "list_status",
        "object_status",
        "missing_packet_hash",
        "invalid_packet_hash",
        "terminal_result",
        "terminal_model_result",
        "terminal_both_results",
        "malformed_utf8_body",
    ],
)
def test_supervisor_malformed_review_history_does_not_skip_later_activation(
    store, tmp_path, corruption
):
    integrate_producer(store, "EXP-1")
    integrate_producer(store, "EXP-2")
    store.add_task("PLAT-1", "Platform", "review contract", scope=["cli.py"])
    sample_proposal = {
        "schema": "v2",
        "version": 2,
        "producer": "Experience",
        "producer_task_id": "EXP-1",
        "consumers": ["Platform"],
    }
    other_proposal = {
        "schema": "other-v2",
        "version": 2,
        "producer": "Experience",
        "producer_task_id": "EXP-2",
        "consumers": ["Platform"],
    }
    store.put_contract("z-other", 1, {"schema": "other-v1"})
    transitions = {
        "sample": store.propose_contract_transition(
            "sample",
            1,
            2,
            sample_proposal,
            producer_task_id="EXP-1",
            producer_team="Experience",
            consumer_teams=["Platform"],
        ),
        "z-other": store.propose_contract_transition(
            "z-other",
            1,
            2,
            other_proposal,
            producer_task_id="EXP-2",
            producer_team="Experience",
            consumer_teams=["Platform"],
        ),
    }
    for name in ("sample", "z-other"):
        store.acknowledge_contract_transition(
            name, 2, "Experience", transitions[name]["body"]["contract_hash"]
        )
    store.acknowledge_contract_transition(
        "z-other", 2, "Platform", transitions["z-other"]["body"]["contract_hash"]
    )
    request = store.prepare_contract_review("sample", 2, "Platform", "c" * 64, review_packet())
    if corruption.startswith("terminal_"):
        terminal_result = review_result(decision="blocked", compatibility="insufficient")
        record_pending_review_response(
            store,
            request["body"],
            suffix="corrupt-history",
            result=terminal_result,
            raw_result=(
                b" \n"
                + json.dumps(terminal_result, sort_keys=True, separators=(",", ":")).encode("utf-8")
                + b"\t"
            ),
        )
        response = store.get_contract_review_response(request["body"]["request_id"])
        store.finish_contract_review(
            request["body"]["request_id"],
            request["body"]["packet_hash"],
            response["raw_sha256"],
            terminal_result,
        )
    else:
        record_pending_review_response(store, request["body"], suffix="corrupt-history")
    latest = store.db.execute(
        "SELECT version,body FROM decisions WHERE name=? ORDER BY version DESC LIMIT 1",
        (request["name"],),
    ).fetchone()
    latest_raw_sha256 = json.loads(latest["body"]).get("raw_sha256")
    if corruption == "malformed_utf8_body":
        store.db.execute(
            "UPDATE decisions SET body=CAST(x'80' AS TEXT) WHERE name=? AND version=1",
            (request["name"],),
        )
        persisted_body = latest["body"]
    elif corruption in {"missing_packet_hash", "invalid_packet_hash"}:
        journal_rows = store.db.execute(
            "SELECT version,body FROM decisions WHERE name=? ORDER BY version",
            (request["name"],),
        ).fetchall()
        persisted_body = None
        for row in journal_rows:
            invalid_body = json.loads(row["body"])
            if corruption == "missing_packet_hash":
                invalid_body.pop("packet_hash")
            else:
                invalid_body["packet_hash"] = []
            version_body = json.dumps(invalid_body, sort_keys=True, separators=(",", ":"))
            store.db.execute(
                "UPDATE decisions SET body=? WHERE name=? AND version=?",
                (version_body, request["name"], row["version"]),
            )
            if row["version"] == latest["version"]:
                persisted_body = version_body
    else:
        if corruption == "malformed_json":
            persisted_body = "{malformed"
        elif corruption.startswith("terminal_"):
            invalid_body = json.loads(latest["body"])
            altered_result = review_result()
            if corruption in {"terminal_result", "terminal_both_results"}:
                invalid_body["result"] = altered_result
            if corruption in {"terminal_model_result", "terminal_both_results"}:
                invalid_body["model_result"] = altered_result
            persisted_body = json.dumps(invalid_body, sort_keys=True, separators=(",", ":"))
        else:
            invalid_body = json.loads(latest["body"])
            invalid_body["status"] = [] if corruption == "list_status" else {}
            persisted_body = json.dumps(invalid_body, sort_keys=True, separators=(",", ":"))
        store.db.execute(
            "UPDATE decisions SET body=? WHERE name=? "
            "AND version=(SELECT MAX(version) FROM decisions WHERE name=?)",
            (persisted_body, request["name"], request["name"]),
        )

    expected_error = (
        "contract review result custody invalid"
        if corruption.startswith("terminal_")
        else "contract review history invalid"
    )
    events_before_reads = store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    with pytest.raises(StateError, match=expected_error):
        store._contract_review_record(request["body"]["request_id"], bounded_replay=True)
    with pytest.raises(StateError, match=expected_error):
        store._contract_review_record(request["body"]["request_id"])

    if corruption.startswith("terminal_"):
        with pytest.raises(StateError, match=expected_error):
            store.finish_contract_review(
                request["body"]["request_id"],
                request["body"]["packet_hash"],
                latest_raw_sha256,
                terminal_result,
            )
        with pytest.raises(StateError, match=expected_error):
            store.replay_contract_review_response(
                request["body"]["request_id"], request["body"]["packet_hash"]
            )
        assert store.get_decision(request["name"])["version"] == latest["version"]
        assert store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before_reads

    cfg = {"contracts": {"sample": sample_proposal, "z-other": other_proposal}}
    with (
        patch.object(supervisor, "config", return_value=cfg),
        patch.object(supervisor, "log_event") as log_event,
        patch.object(supervisor, "Codex", side_effect=AssertionError("provider must not start")),
        patch.object(
            supervisor,
            "metered_client",
            side_effect=AssertionError("provider must not start"),
        ),
    ):
        supervisor.activate_contract(tmp_path, store)

    assert store.get_contract("sample")["version"] == 1
    assert store.get_contract("z-other")["version"] == 2
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None
    assert store.get_decision(store._contract_ack_key("z-other", 2, "Platform")) is not None
    assert (
        store.db.execute(
            "SELECT body FROM decisions WHERE name=? ORDER BY version DESC LIMIT 1",
            (request["name"],),
        ).fetchone()["body"]
        == persisted_body
    )
    expected_error = (
        "contract review result custody invalid"
        if corruption.startswith("terminal_")
        else "contract review history invalid"
    )
    assert any(
        call.args[1] == "contract_review_replay_failed"
        and call.kwargs.get("name") == "sample"
        and call.kwargs.get("version") == 2
        and call.kwargs.get("dispatch_authority") is False
        and call.kwargs.get("provider_dispatch") is False
        and call.kwargs.get("error") == expected_error
        for call in log_event.call_args_list
    )


def test_contract_review_worklist_rejects_missing_packet_hash(store):
    request = prepare_review(store)
    body = store.get_decision(request["name"])["body"]
    del body["packet_hash"]
    store.db.execute(
        "UPDATE decisions SET body=? WHERE name=? AND version=1",
        (json.dumps(body, sort_keys=True, separators=(",", ":")), request["name"]),
    )

    with pytest.raises(StateError, match="contract review history invalid"):
        store.contract_review_worklist()
    with pytest.raises(StateError, match="contract review history invalid"):
        store.quiescence_snapshot()


def test_transition_replay_uses_scoped_index_and_ignores_unrelated_bad_identity(store):
    request = prepare_review(store)
    body = request["body"]
    record_pending_review_response(store, body, suffix="scoped-index")
    store.db.execute(
        "INSERT INTO decisions VALUES(?,?,?,?,?)",
        ("contract-review/unrelated-malformed-id", 1, "{malformed", None, None),
    )

    params = (body["name"], body["version"], body["contract_hash"], body["proposal_digest"])
    for query, query_params, requires_cursor_seek in (
        (_CONTRACT_REVIEW_TRANSITION_REPLAY_FIRST_QUERY, params, False),
        (
            _CONTRACT_REVIEW_TRANSITION_REPLAY_NEXT_QUERY,
            (*params, "contract-review/" + "a" * 64),
            True,
        ),
    ):
        plan = store.db.execute("EXPLAIN QUERY PLAN " + query, query_params).fetchall()
        assert any(
            "idx_contract_review_transition_replay" in row["detail"]
            and (not requires_cursor_seek or "name>?" in row["detail"])
            for row in plan
        )

    result = store.replay_contract_review_responses_for_transition(
        body["name"], body["version"], body["contract_hash"]
    )

    assert result["overflow"] is False
    assert [item["request_id"] for item in result["replayed"]] == [body["request_id"]]
    assert store.get_decision(store._contract_ack_key(body["name"], body["version"], body["team"]))


def test_global_replay_discovery_uses_decision_primary_key_range(store):
    for query, cursor, seek_opcode in (
        (_CONTRACT_REVIEW_GLOBAL_REPLAY_FIRST_QUERY, "contract-review/", "SeekGE"),
        (
            _CONTRACT_REVIEW_GLOBAL_REPLAY_NEXT_QUERY,
            "contract-review/" + "a" * 64,
            "SeekGT",
        ),
    ):
        plan = store.db.execute("EXPLAIN QUERY PLAN " + query, (cursor,)).fetchall()
        assert any(
            "SEARCH decisions USING COVERING INDEX sqlite_autoindex_decisions_1" in row["detail"]
            and "name>? AND name<?" in row["detail"]
            for row in plan
        )
        program = store.db.execute("EXPLAIN " + query, (cursor,)).fetchall()
        cursor_register = next(row[3] for row in program if row[1] == "Variable")
        lower_seek = next(row for row in program if row[1] == seek_opcode)
        assert lower_seek[4] == cursor_register


def test_global_replay_name_seek_skips_versions_of_prior_journal(store):
    first_name = "contract-review/" + "a" * 64
    next_name = "contract-review/" + "b" * 64
    store.db.executemany(
        "INSERT INTO decisions(name,version,body) VALUES(?,?,?)",
        [(first_name, version, "{}") for version in range(1, 1001)] + [(next_name, 1, "{}")],
    )

    with store._read_snapshot():
        names = store._contract_review_global_replay_names_in_snapshot()

    assert names == [first_name, next_name]


def test_transition_replay_name_seek_skips_versions_of_prior_journal(store):
    name = "sample"
    version = 2
    contract_hash = "a" * 64
    proposal_digest = "b" * 64
    first_name = "contract-review/" + "a" * 64
    next_name = "contract-review/" + "b" * 64
    body = json.dumps(
        {
            "name": name,
            "version": version,
            "contract_hash": contract_hash,
            "proposal_digest": proposal_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    store.db.executemany(
        "INSERT INTO decisions(name,version,body) VALUES(?,?,?)",
        [(first_name, journal_version, body) for journal_version in range(1, 1001)]
        + [(next_name, 1, body)],
    )

    with store._read_snapshot():
        names = store._contract_review_transition_replay_names_in_snapshot(
            name=name,
            version=version,
            contract_hash=contract_hash,
            proposal_digest=proposal_digest,
        )

    assert names == [first_name, next_name]


def test_contract_review_replay_sweep_overflow_performs_no_partial_replay(store):
    request = prepare_review(store)
    body = request["body"]
    record_pending_review_response(store, body, suffix="1")
    second = store.prepare_contract_review("sample", 2, "Platform", "d" * 64, review_packet())
    second_body = second["body"]
    record_pending_review_response(store, second_body, suffix="2")

    with pytest.raises(StateError, match="replay limit invalid"):
        store.replay_pending_contract_review_responses(limit=True)
    overflow = store.replay_pending_contract_review_responses(limit=1)
    assert overflow == {
        "limit": 1,
        "count": None,
        "overflow": True,
        "response_recorded_count": None,
        "replayed": [],
        "failures": [],
        "skipped_count": None,
        "dispatch_authority": False,
        "provider_dispatch": False,
    }
    assert store.get_decision(request["name"])["body"]["status"] == "response_recorded"
    assert store.get_decision(second["name"])["body"]["status"] == "response_recorded"
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


def test_contract_review_replay_ignores_retained_terminal_history_for_candidate_limit(store):
    first = prepare_review(store)
    first_body = first["body"]
    blocked_result = review_result(decision="blocked", compatibility="insufficient")
    for index in range(101):
        request = (
            first
            if index == 0
            else store.prepare_contract_review(
                "sample", 2, "Platform", f"{index:064x}", review_packet()
            )
        )
        body = request["body"]
        if index == 0:
            body = first_body
        record_pending_review_response(store, body, suffix=str(index), result=blocked_result)
        response = store.get_contract_review_response(body["request_id"])
        store.finish_contract_review(
            body["request_id"], body["packet_hash"], response["raw_sha256"], blocked_result
        )

    pending = store.prepare_contract_review("sample", 2, "Platform", "f" * 64, review_packet())
    pending_body = pending["body"]
    record_pending_review_response(store, pending_body, suffix="pending", result=blocked_result)
    sweep = store.replay_pending_contract_review_responses(limit=1)

    assert sweep["overflow"] is False
    assert sweep["count"] == 1
    assert sweep["response_recorded_count"] == 1
    assert sweep["skipped_count"] == 101
    assert sweep["replayed"] == [
        {
            "request_id": pending_body["request_id"],
            "packet_hash": pending_body["packet_hash"],
            "status": "blocked",
            "raw_sha256": store.get_contract_review_response(pending_body["request_id"])[
                "raw_sha256"
            ],
        }
    ]
    assert store.get_decision(first["name"])["body"]["status"] == "blocked"
    assert store.get_decision(pending["name"])["body"]["status"] == "blocked"
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


@pytest.mark.parametrize(
    "raw_result",
    [b"{malformed", b'{"decision":"blocked","decision":"approve"}'],
)
def test_contract_review_malformed_raw_is_durable_and_non_sendable(store, raw_result):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    recorded = store.record_contract_review_response(
        body["request_id"],
        body["packet_hash"],
        body["dispatch_id"],
        "provider-turn-malformed",
        raw_result,
    )
    with pytest.raises(StateError, match="not replayable"):
        store.replay_contract_review_response(body["request_id"], body["packet_hash"])
    with pytest.raises(StateError, match="does not match parsed result"):
        store.finish_contract_review(
            body["request_id"], body["packet_hash"], recorded["body"]["raw_sha256"], review_result()
        )
    assert store.get_contract_review_response(body["request_id"])["raw_result"] == raw_result
    assert store.get_decision(request["name"])["body"]["status"] == "response_recorded"
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None
    with pytest.raises(StateError, match="not sendable"):
        store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])


def test_legacy_terminal_contract_review_history_remains_readable(store):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    legacy_result = review_result(decision="blocked", compatibility="insufficient")
    legacy = {
        **store.get_decision(request["name"])["body"],
        "status": "blocked",
        "raw_sha256": "a" * 64,
        "model_result": legacy_result,
        "result": legacy_result,
        "blocking_reasons": [],
    }
    with store._tx():
        store._put_decision_in_transaction(request["name"], legacy)

    recovered = store._contract_review_record(body["request_id"])
    assert recovered["body"]["status"] == "blocked"
    assert recovered["body"]["model_result"] == legacy_result
    with pytest.raises(StateError, match="legacy contract review"):
        store.get_contract_review_response(body["request_id"])
    assert (
        store.finish_contract_review(
            body["request_id"], body["packet_hash"], "a" * 64, legacy_result
        )
        == recovered
    )


def test_contract_review_preserves_forged_raw_result_without_ack(store):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    forged = review_result(reviewed_evidence_refs=["invented:receipt"])
    response = record_review_result(store, body, forged)
    with pytest.raises(StateError, match="evidence coverage"):
        store.finish_contract_review(
            body["request_id"], body["packet_hash"], response["body"]["raw_sha256"], forged
        )
    assert store.get_decision(request["name"])["body"]["status"] == "response_recorded"
    blocked = review_result(decision="blocked", compatibility="insufficient")
    with pytest.raises(StateError, match="custody changed"):
        record_review_result(store, body, blocked)
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


def test_contract_review_packet_and_proposal_are_fenced(store):
    request = prepare_review(store)
    body = request["body"]
    with pytest.raises(StateError, match="not a participant"):
        store.prepare_contract_review("sample", 2, "Research", "f" * 64, review_packet())
    with pytest.raises(StateError, match="packet changed"):
        store.begin_contract_review_dispatch(body["request_id"], "0" * 64)
    with pytest.raises(StateError, match="byte bound"):
        store.prepare_contract_review(
            "sample",
            2,
            "Platform",
            "f" * 64,
            {
                **review_packet(),
                "compatibility_analysis": "x" * 8000,
                "evidence_refs": [
                    {
                        "ref": f"ref{index}-" + "x" * (506 - len(str(index))),
                        "sha256": f"{index:064x}",
                    }
                    for index in range(100)
                ],
            },
        )


def test_contract_review_stale_inventory_is_terminal_blocked_and_not_retried(store):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    store.add_task("PLAT-2", "Platform", "new consumer", scope=["other.py"])
    result = review_result()
    finished = finish_recorded_review(store, body, result)
    assert finished["body"]["status"] == "blocked"
    assert finished["body"]["model_result"] == result
    assert any("inventory changed" in item for item in finished["body"]["blocking_reasons"])
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None
    with pytest.raises(StateError, match="identity conflict"):
        store.prepare_contract_review("sample", 2, "Platform", "c" * 64, review_packet())


def test_contract_review_type_only_drift_blocks_saved_response_without_ack(store):
    request = prepare_review(store, consumer_contracts={"sample": 1})
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    result = review_result()
    response = record_review_result(store, body, result)
    store.db.execute(
        "UPDATE tasks SET contracts=? WHERE id='PLAT-1'",
        (org_state._json({"sample": True}),),
    )

    finished = store.finish_contract_review(
        body["request_id"],
        body["packet_hash"],
        response["body"]["raw_sha256"],
        result,
    )
    assert finished["body"]["status"] == "blocked"
    assert finished["body"]["model_result"] == result
    assert any("inventory changed" in item for item in finished["body"]["blocking_reasons"])
    version = finished["version"]
    repeated = store.finish_contract_review(
        body["request_id"],
        body["packet_hash"],
        response["body"]["raw_sha256"],
        result,
    )
    assert repeated == finished
    assert store.get_decision(request["name"])["version"] == version
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


def test_blocked_review_can_record_partial_consumer_coverage(store):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    blocked = review_result(
        decision="blocked",
        compatibility="insufficient",
        rationale="Review remains incomplete.",
        reviewed_evidence_refs=[],
        affected_consumers_reviewed=[],
        findings=["not reviewed"],
    )
    finished = finish_recorded_review(store, body, blocked)
    assert finished["body"]["status"] == "blocked"
    assert finished["body"]["result"]["affected_consumers_reviewed"] == []


def test_contract_review_ack_race_preserves_response_without_second_ack(store):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    store.acknowledge_contract_transition("sample", 2, "Platform", body["contract_hash"])
    result = review_result()
    finished = finish_recorded_review(store, body, result)
    assert finished["body"]["status"] == "blocked"
    assert finished["body"]["model_result"] == result
    assert any("was acknowledged" in item for item in finished["body"]["blocking_reasons"])


def test_contract_review_after_activation_cannot_start_dispatch(store):
    request = prepare_review(store)
    body = request["body"]
    attempt = store.claim("EXP-1", "synthetic", 30)
    store.submit(attempt["id"], attempt["epoch"], "candidate-sha", {"receipt": "synthetic"})
    store.verify("EXP-1", "candidate-sha", True, {"passed": True})
    store.accept("EXP-1", "candidate-sha")
    store.integrate("EXP-1", "candidate-sha", "integration-sha", {"merged": True})
    store.acknowledge_contract_transition("sample", 2, "Experience", body["contract_hash"])
    store.acknowledge_contract_transition("sample", 2, "Platform", body["contract_hash"])
    store.activate_contract_transition("sample", 2)
    with pytest.raises(StateError, match="source_version_changed"):
        store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    assert store.get_decision(request["name"])["version"] == 1
    assert store.get_decision(request["name"])["body"]["status"] == "prepared"
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is not None


def test_contract_review_result_limits_reject_unbounded_output(store):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    with pytest.raises(StateError, match="result exceeds bound"):
        finish_recorded_review(store, body, review_result(rationale="x" * 4001))


def test_contract_review_rechecks_producer_snapshot_before_ack(store):
    request = prepare_review(store)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    store.claim("EXP-1", "synthetic", 30)
    result = review_result()
    finished = finish_recorded_review(store, body, result)
    assert finished["body"]["status"] == "blocked"
    assert any(
        "producer task evidence changed" in item for item in finished["body"]["blocking_reasons"]
    )
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


def test_contract_review_task_growth_past_bound_is_terminal_blocked(store):
    store.add_task("EXP-1", "Experience", "produce contract", scope=["schema.py"])
    store.add_task("PLAT-000", "Platform", "consume contract", scope=["cli.py"])
    for index in range(1, 100):
        store.add_task(f"PLAT-{index:03d}", "Platform", "consume contract", scope=["cli.py"])
    propose(store)
    request = store.prepare_contract_review("sample", 2, "Platform", "c" * 64, review_packet())
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    store.add_task("PLAT-100", "Platform", "new consumer", scope=["other.py"])
    result = review_result(affected_consumers_reviewed=body["packet"]["affected_consumer_task_ids"])
    finished = finish_recorded_review(store, body, result)
    assert finished["body"]["status"] == "blocked"
    assert any("exceeds bound" in item for item in finished["body"]["blocking_reasons"])
    assert store.get_decision(store._contract_ack_key("sample", 2, "Platform")) is None


def test_contract_review_accepts_legacy_task_id_within_result_byte_bound(store):
    store.add_task("EXP-1", "Experience", "produce contract", scope=["schema.py"])
    task_id = "P" * 300
    store.add_task(task_id, "Platform", "consume contract", scope=["cli.py"])
    propose(store)
    request = store.prepare_contract_review("sample", 2, "Platform", "c" * 64, review_packet())
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        "thread-1",
        "gpt-6.1-sol",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-birth"},
    )
    result = review_result(affected_consumers_reviewed=[task_id])
    finished = finish_recorded_review(store, body, result)
    assert finished["body"]["status"] == "approved"


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
        "INSERT INTO decisions(name,version,body,replaces,replaces_version) "
        "VALUES(?,?,?,CAST(X'80' AS TEXT),NULL)",
        (
            transition["name"],
            2,
            store.db.execute(
                "SELECT body FROM decisions WHERE name=? AND version=1", (transition["name"],)
            ).fetchone()[0],
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
    with pytest.raises(StateError, match="contract decision history invalid"):
        store.contract_transition_review_projection()


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


def test_ack_root_link_metadata_corruption_blocks_activation(store):
    transition = propose(store)
    integrate_producer(store)
    digest = transition["body"]["contract_hash"]
    for team in ("Experience", "Platform"):
        store.acknowledge_contract_transition("sample", 2, team, digest)
    ack_name = store._contract_ack_key("sample", 2, "Platform")
    store.db.execute(
        "UPDATE decisions SET replaces=NULL,replaces_version=1 WHERE name=? AND version=1",
        (ack_name,),
    )
    events_before = store.events()

    with pytest.raises(StateError, match="contract decision history invalid"):
        store.activate_contract_transition("sample", 2)

    assert store.get_contract("sample")["version"] == 1
    assert store.get_decision(store._contract_transition_key("sample", 2) + "/activated") is None
    assert store.events() == events_before


def test_projection_fails_closed_for_missing_ack_after_activation(store):
    transition = propose(store)
    integrate_producer(store)
    digest = transition["body"]["contract_hash"]
    for team in ("Experience", "Platform"):
        store.acknowledge_contract_transition("sample", 2, team, digest)
    store.activate_contract_transition("sample", 2)
    second = store.propose_contract_transition(
        "sample",
        2,
        3,
        {"schema": "v3"},
        producer_task_id="EXP-2",
        producer_team="Experience",
        consumer_teams=["Platform"],
    )
    integrate_producer(store, "EXP-2")
    second_digest = second["body"]["contract_hash"]
    for team in ("Experience", "Platform"):
        store.acknowledge_contract_transition("sample", 3, team, second_digest)
    store.activate_contract_transition("sample", 3)
    ack_name = store._contract_ack_key("sample", 2, "Platform")
    store.db.execute("DELETE FROM decisions WHERE name=?", (ack_name,))
    store.db.execute("DELETE FROM events WHERE kind='decision_recorded' AND entity=?", (ack_name,))
    with pytest.raises(StateError, match="activated contract review evidence is incomplete"):
        store.contract_transition_review_projection()
