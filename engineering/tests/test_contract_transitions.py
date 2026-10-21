"""Synthetic tests for durable, content-bound A10 contract transitions."""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
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


def prepare_review(store, *, consumer_contracts=None):
    store.add_task("EXP-1", "Experience", "produce contract", scope=["schema.py"])
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
    request = prepare_review(store)
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
                other.execute("UPDATE tasks SET title=? WHERE id=?", ("changed", "PLAT-1"))
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
    finished = recovered.finish_contract_review(
        body["request_id"], body["packet_hash"], response["raw_sha256"], result
    )
    assert finished["body"]["status"] == "approved"
    assert recovered.get_decision(recovered._contract_ack_key("sample", 2, "Platform"))
    assert (
        recovered.finish_contract_review(
            body["request_id"], body["packet_hash"], response["raw_sha256"], result
        )
        == finished
    )
    recovered.close()


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
    with pytest.raises(StateError, match="does not match parsed result"):
        store.finish_contract_review(
            body["request_id"], body["packet_hash"], recorded["body"]["raw_sha256"], review_result()
        )
    assert store.get_contract_review_response(body["request_id"])["raw_result"] == raw_result
    assert store.get_decision(request["name"])["body"]["status"] == "response_recorded"
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
