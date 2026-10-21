"""Cortex preregistration is metadata-only and reuses reviewed A10 transitions."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from engineering.org_state import StateError, Store
from engineering.research_protocol import (
    CONTRACT_NAME,
    ResearchProtocolError,
    canonical_protocol,
    protocol_sha256,
)
from engineering.tests.contract_review_helpers import approve_participant
from engineering.tests.test_task_spec_store import (
    ADMISSION,
    BASE,
    POLICY,
    SCOPE,
    SPEC,
)


def protocol():
    return {
        "schema": "brain-cortex-research-protocol-v1",
        "version": 2,
        "protocol_id": "CORTEX-101",
        "objective_id": "offline-brain-engineering",
        "hypothesis": "Changing the architecture improves the pinned evaluation cohort.",
        "input_pins": [
            {
                "role": "train",
                "artifact_sha256": "1" * 64,
                "available_at_ns": 10,
            },
            {
                "role": "research_evaluation",
                "artifact_sha256": "2" * 64,
                "available_at_ns": 10,
            },
        ],
        "recipe_sha256": "3" * 64,
        "code_sha256": "4" * 64,
        "baseline": {"name": "current_model", "artifact_sha256": "5" * 64},
        "metrics": [{"id": "balanced_accuracy", "direction": "higher_is_better"}],
        "population": {
            "definition": "The preregistered synthetic population.",
            "definition_sha256": hashlib.sha256(
                b"The preregistered synthetic population."
            ).hexdigest(),
        },
        "evaluation_window": {"start_ns": 20, "end_ns": 30},
        "resource_budget": {"max_compute_seconds": 600, "max_output_bytes": 1_000_000},
        "stop_criteria": ["Stop when the registered budget is reached."],
        "output_owner": "Cortex",
    }


def integrate_cortex_producer(store):
    store.add_task("CORTEX-001", "Cortex", "Publish protocol contract", scope=["protocol.py"])
    attempt = store.claim("CORTEX-001", "synthetic-cortex-producer", 30)
    store.submit(attempt["id"], attempt["epoch"], "candidate-sha", {"receipt": "synthetic"})
    store.verify("CORTEX-001", "candidate-sha", True, {"passed": True})
    store.accept("CORTEX-001", "candidate-sha")
    store.integrate("CORTEX-001", "candidate-sha", "integration-sha", {"merged": True})


def test_protocol_rejects_ambiguous_timing_and_duplicate_roles():
    value = protocol()
    value["input_pins"][1]["available_at_ns"] = value["evaluation_window"]["start_ns"] + 1
    with pytest.raises(ResearchProtocolError, match="window_availability"):
        canonical_protocol(value)

    value = protocol()
    value["input_pins"][1]["role"] = "train"
    with pytest.raises(ResearchProtocolError, match="input_roles"):
        canonical_protocol(value)

    value = protocol()
    value["population"]["definition_sha256"] = "6" * 64
    with pytest.raises(ResearchProtocolError, match="population_hash_mismatch"):
        canonical_protocol(value)


def test_protocol_transition_requires_cortex_producer_and_evaluation_consumer(tmp_path):
    store = Store(tmp_path / "org.sqlite")
    try:
        store.put_contract(
            CONTRACT_NAME,
            1,
            {
                "schema": "brain-cortex-research-protocol-unreviewed-v1",
                "version": 1,
                "status": "unreviewed_baseline",
            },
        )
        with pytest.raises(StateError, match="Cortex and Evaluation"):
            store.propose_contract_transition(
                CONTRACT_NAME,
                1,
                2,
                protocol(),
                producer_task_id="CORTEX-001",
                producer_team="Cortex",
                consumer_teams=["Platform"],
            )
        assert store.get_contract_transition(CONTRACT_NAME, 2) is None
    finally:
        store.close()


def test_reviewed_protocol_is_admitted_by_exact_hash_after_reopen(tmp_path):
    path = tmp_path / "org.sqlite"
    store = Store(path)
    store.add_objective("offline-brain-engineering", "Synthetic Cortex organization test")
    store.put_contract(
        CONTRACT_NAME,
        1,
        {
            "schema": "brain-cortex-research-protocol-unreviewed-v1",
            "version": 1,
            "status": "unreviewed_baseline",
        },
    )
    value = protocol()
    expected_hash = protocol_sha256(value, version=2)
    store.propose_contract_transition(
        CONTRACT_NAME,
        1,
        2,
        value,
        producer_task_id="CORTEX-001",
        producer_team="Cortex",
        consumer_teams=["Evaluation"],
    )
    integrate_cortex_producer(store)
    with pytest.raises(StateError, match="acknowledgements incomplete"):
        store.activate_contract_transition(CONTRACT_NAME, 2)
    approve_participant(store, CONTRACT_NAME, 2, "Cortex")
    with pytest.raises(StateError, match="acknowledgements incomplete"):
        store.activate_contract_transition(CONTRACT_NAME, 2)
    approve_participant(store, CONTRACT_NAME, 2, "Evaluation")
    activated = store.activate_contract_transition(CONTRACT_NAME, 2)
    assert activated["body"]["acknowledged_teams"] == ["Cortex", "Evaluation"]
    assert store.get_contract(CONTRACT_NAME, 2)["body"] == value

    wrong_digest = "0" * 64
    spec = copy.deepcopy(SPEC)
    spec.update(team="Cortex", id="CORTEX-102")
    spec["contracts"] = {CONTRACT_NAME: {"version": 2, "proposal_sha256": wrong_digest}}
    envelope = {
        "schema": "brain-task-envelope-v1",
        "objective_id": spec["objective_id"],
        "teams": ["Cortex"],
        "allowed_paths": SCOPE,
        "capabilities": spec["capabilities"],
        "max_attempts": 3,
        "max_turn_seconds": 600,
        "allowed_contracts": {CONTRACT_NAME: {"2": wrong_digest}},
        "base_sha": BASE,
        "risk_levels": ["low"],
    }
    acceptance_id = spec["acceptance"][0]["id"]
    acceptance_hash = spec["acceptance"][0]["sha256"]
    registry = {
        acceptance_id: {
            "sha256": acceptance_hash,
            "objective_id": spec["objective_id"],
            "scope": SCOPE,
            "contracts": spec["contracts"],
            "independent": True,
            "frozen": True,
            "risk": "low",
        }
    }
    with pytest.raises(StateError, match="contract_hash_mismatch"):
        store.admit_task_spec(
            spec,
            envelope=envelope,
            acceptance_registry=registry,
            policy_sha256=POLICY,
            admission=ADMISSION,
        )
    assert "CORTEX-102" not in {task["id"] for task in store.list_tasks()}

    store.add_objective("other-objective", "Negative objective binding test")
    spec["objective_id"] = "other-objective"
    envelope["objective_id"] = "other-objective"
    registry[acceptance_id]["objective_id"] = "other-objective"
    spec["contracts"][CONTRACT_NAME]["proposal_sha256"] = expected_hash
    envelope["allowed_contracts"][CONTRACT_NAME]["2"] = expected_hash
    registry[acceptance_id]["contracts"] = copy.deepcopy(spec["contracts"])
    with pytest.raises(StateError, match="protocol_objective_mismatch"):
        store.admit_task_spec(
            spec,
            envelope=envelope,
            acceptance_registry=registry,
            policy_sha256=POLICY,
            admission=ADMISSION,
        )
    spec["objective_id"] = "offline-brain-engineering"
    envelope["objective_id"] = spec["objective_id"]
    registry[acceptance_id]["objective_id"] = spec["objective_id"]

    spec["capabilities"].append("training")
    with pytest.raises(StateError, match="capability_outside_source_only_scope"):
        store.admit_task_spec(
            spec,
            envelope=envelope,
            acceptance_registry=registry,
            policy_sha256=POLICY,
            admission=ADMISSION,
        )
    spec["capabilities"].remove("training")
    store.admit_task_spec(
        spec,
        envelope=envelope,
        acceptance_registry=registry,
        policy_sha256=POLICY,
        admission=ADMISSION,
    )
    store.close()

    reopened = Store(path)
    try:
        persisted = reopened.get_task_spec("CORTEX-102")
        assert persisted["spec"]["contracts"][CONTRACT_NAME] == {
            "version": 2,
            "proposal_sha256": expected_hash,
        }
        assert reopened.get_contract_transition(CONTRACT_NAME, 2)["body"]["contract_hash"] == (
            expected_hash
        )
        assert reopened.ready_tasks(team="Cortex") == []
        assert reopened.claim("CORTEX-102", "synthetic-cortex-worker") is None
        readiness = next(
            row for row in reopened.readiness_report() if row["task_id"] == "CORTEX-102"
        )
        assert {
            "kind": "execution_qualification",
            "reason": "cortex_execution_not_qualified",
        } in readiness["blockers"]

        changed_protocol = copy.deepcopy(value)
        changed_protocol["hypothesis"] = "Tampered hypothesis after task admission."
        reopened.db.execute(
            "UPDATE contracts SET body=? WHERE name=? AND version=2",
            (
                json.dumps(changed_protocol, sort_keys=True, separators=(",", ":")),
                CONTRACT_NAME,
            ),
        )
        readiness = next(
            row for row in reopened.readiness_report() if row["task_id"] == "CORTEX-102"
        )
        assert any(
            blocker.get("reason") == "task_spec_contract_hash_mismatch"
            for blocker in readiness["blockers"]
        )
    finally:
        reopened.close()
