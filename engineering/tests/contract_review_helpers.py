"""Reusable synthetic review construction for state-machine tests only."""

from __future__ import annotations

import hashlib
import json


def approve_participant(store, name: str, version: int, team: str):
    ack_key = store._contract_ack_key(name, version, team)
    existing = store.get_decision(ack_key)
    if existing is not None:
        return existing
    revision = hashlib.sha256(f"test-review:{name}:{version}:{team}".encode()).hexdigest()
    packet = {
        "source_commit": "a" * 40,
        "evidence_refs": [{"ref": "compatibility:synthetic", "sha256": "b" * 64}],
        "compatibility_analysis": "Reviewed the synthetic contract and participant snapshot.",
        "migration_plan": None,
    }
    request = store.prepare_contract_review(name, version, team, revision, packet)
    body = request["body"]
    store.begin_contract_review_dispatch(body["request_id"], body["packet_hash"])
    store.record_contract_review_session(
        body["request_id"],
        body["packet_hash"],
        f"synthetic-review-{name}-{version}-{team}",
        "synthetic:independent-reviewer",
        body["dispatch_id"],
        {"pid": 123, "birth": "synthetic-test-process"},
    )
    result = {
        "decision": "approve",
        "compatibility": "compatible",
        "rationale": "Synthetic review evidence is complete.",
        "findings": [],
        "reviewed_evidence_refs": ["compatibility:synthetic"],
        "affected_consumers_reviewed": body["packet"]["affected_consumer_task_ids"],
        "migration_plan": None,
    }
    raw = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
    response = store.record_contract_review_response(
        body["request_id"],
        body["packet_hash"],
        body["dispatch_id"],
        f"synthetic-turn-{name}-{version}-{team}",
        raw,
    )
    store.finish_contract_review(
        body["request_id"],
        body["packet_hash"],
        response["body"]["raw_sha256"],
        result,
    )
    return store.get_decision(ack_key)
